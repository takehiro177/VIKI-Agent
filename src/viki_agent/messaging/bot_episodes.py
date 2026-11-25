"""Bot episodes: one row per finished turn of a Bot-bound session, and each session's taint.

An episode names its Bot, vault, session and kind (`main`, `task`, `bot_thread` or `routine`, as
Bot usage names them), when the turn started and ended, how it ended, how many tools it started,
the lessons recalled into its session (`bot_recall`), whether it was tainted, and whether a reflect
run has read it. It holds no text: the turn itself stays in Claude Code's transcript, which a
learning run reads, bounded, only while it runs (`bot_excerpts`).

`EpisodeRecorder` folds the session envelopes of the sessions it watches (each Bot session is
watched from its spawn, `BotLearning.fence`) and writes a row when a turn ends, in a worker thread.
`EpisodeStore` is the SQL; its methods block.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from anyio.to_thread import run_sync

from viki_agent.automation.routines import parse_stamp, stamp
from viki_agent.messaging.bot_taint import SOURCES, join_sources, taint_source
from viki_agent.storage import SessionStore

log = logging.getLogger(__name__)

MAX_EPISODES_PER_BOT = 2000
MAX_LESSON_IDS = 5
Kind = Literal["main", "task", "bot_thread", "routine"]

# A session's owner and kind, by the rule Bot usage applies (`bot_usage._SESSIONS`): a routine
# run, then a Bot-thread seat, then the Bot's current chat, else a task thread.
_SESSION = (
    "SELECT m.bot_id,b.workspace_id,CASE WHEN b.launch_origin='routine' "
    "OR EXISTS (SELECT 1 FROM task_attempts t WHERE t.session_id=m.session_id) THEN 'routine' "
    "WHEN EXISTS (SELECT 1 FROM bot_thread_sessions r WHERE r.session_id=m.session_id) "
    "THEN 'bot_thread' WHEN EXISTS (SELECT 1 FROM bots x WHERE x.chat_session_id=m.session_id "
    "AND x.bot_id=m.bot_id) THEN 'main' ELSE 'task' END "
    "FROM session_bot_memory m JOIN session_profile_bindings b ON b.session_id=m.session_id "
    "WHERE m.session_id=?"
)
# Tainted: the session itself, or any session of a Bot thread it takes part in.
_TAINTED = (
    "SELECT EXISTS (SELECT 1 FROM bot_session_taint WHERE session_id=?1) OR EXISTS ("
    "SELECT 1 FROM bot_thread_sessions mine JOIN bot_thread_sessions peer "
    "ON peer.thread_id=mine.thread_id JOIN bot_session_taint t ON t.session_id=peer.session_id "
    "WHERE mine.session_id=?1)"
)


@dataclass(frozen=True)
class SessionInfo:
    bot_id: str
    workspace_id: str
    kind: Kind


@dataclass(frozen=True)
class Episode:
    episode_id: str
    workspace_id: str
    bot_id: str
    session_id: str
    kind: Kind
    started_at: str
    finished_at: str
    status: Literal["success", "cancelled", "error"]
    tainted: bool
    tools: int
    lessons: tuple[str, ...]
    reflected: bool


def _episode(row: sqlite3.Row | tuple[Any, ...]) -> Episode:
    values = tuple(row)
    lessons = tuple(part for part in str(values[10]).split(",") if part)
    return Episode(
        values[0],
        values[1],
        values[2],
        values[3],
        values[4],
        values[5],
        values[6],
        values[7],
        bool(values[8]),
        int(values[9]),
        lessons,
        bool(values[11]),
    )


_COLUMNS = (
    "episode_id,workspace_id,bot_id,session_id,kind,started_at,finished_at,status,tainted,tools,"
    "lessons,reflected"
)


class EpisodeStore:
    """Episodes and taint in the app's store. Every method blocks."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def session(self, session_id: str) -> SessionInfo | None:
        with self.store._connection() as connection:
            row = connection.execute(_SESSION, (session_id,)).fetchone()
        return None if row is None else SessionInfo(row[0], row[1], row[2])

    def tainted(self, session_id: str) -> bool:
        with self.store._connection() as connection:
            return bool(connection.execute(_TAINTED, (session_id,)).fetchone()[0])

    def mark_tainted(self, session_id: str, info: SessionInfo, source: str, now: str) -> None:
        if source not in SOURCES:
            raise ValueError("invalid taint source")
        with self.store._connection(write=True) as connection:
            row = connection.execute(
                "SELECT sources FROM bot_session_taint WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO bot_session_taint VALUES (?,?,?,?,?)",
                    (session_id, info.workspace_id, info.bot_id, source, now),
                )
            else:
                merged = join_sources((*str(row[0]).split(","), source))
                connection.execute(
                    "UPDATE bot_session_taint SET sources=? WHERE session_id=?",
                    (merged, session_id),
                )

    def untaint(self, session_id: str) -> bool:
        """Clear the session's own taint, as only its owner may. Its episodes read untainted
        again unless a Bot-thread peer is still tainted. Answers whether it was tainted."""
        with self.store._connection(write=True) as connection:
            removed = connection.execute(
                "DELETE FROM bot_session_taint WHERE session_id=?", (session_id,)
            ).rowcount
            if not connection.execute(_TAINTED, (session_id,)).fetchone()[0]:
                connection.execute(
                    "UPDATE bot_episodes SET tainted=0 WHERE session_id=?", (session_id,)
                )
        return removed == 1

    def tainted_sessions(self, bot_id: str, limit: int = 50) -> list[dict[str, str]]:
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT session_id,sources,tainted_at FROM bot_session_taint WHERE bot_id=? "
                "ORDER BY tainted_at DESC,session_id LIMIT ?",
                (bot_id, limit),
            ).fetchall()
        return [{"session_id": r[0], "sources": r[1], "tainted_at": r[2]} for r in rows]

    def record(self, episode: Episode) -> None:
        """Insert one episode, count each recalled lesson as used, and keep the Bot's newest
        MAX_EPISODES_PER_BOT episodes."""
        tainted = self.tainted(episode.session_id) or episode.tainted
        with self.store._connection(write=True) as connection:
            connection.execute(
                f"INSERT INTO bot_episodes ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    episode.episode_id,
                    episode.workspace_id,
                    episode.bot_id,
                    episode.session_id,
                    episode.kind,
                    episode.started_at,
                    episode.finished_at,
                    episode.status,
                    int(tainted),
                    episode.tools,
                    ",".join(episode.lessons[:MAX_LESSON_IDS]),
                    0,
                ),
            )
            for memory_id in episode.lessons[:MAX_LESSON_IDS]:
                connection.execute(
                    "INSERT INTO bot_lesson_stats VALUES (?,?,?,1,0,0,?) "
                    "ON CONFLICT(workspace_id,memory_id) DO UPDATE SET uses=uses+1,"
                    "last_used_at=excluded.last_used_at",
                    (episode.workspace_id, memory_id, episode.bot_id, episode.finished_at),
                )
            connection.execute(
                "DELETE FROM bot_episodes WHERE bot_id=? AND episode_id NOT IN (SELECT episode_id "
                "FROM bot_episodes WHERE bot_id=? ORDER BY finished_at DESC,episode_id DESC "
                "LIMIT ?)",
                (episode.bot_id, episode.bot_id, MAX_EPISODES_PER_BOT),
            )

    def episodes(
        self,
        bot_id: str,
        *,
        limit: int,
        reflected: bool | None = None,
        after: str | None = None,
        include_tainted: bool = False,
    ) -> list[Episode]:
        """The Bot's newest episodes, newest first: untainted ones unless asked, only those
        (not) reflected when `reflected` says so, only those finished after `after`."""
        clauses: list[str] = ["bot_id=?"]
        values: list[object] = [bot_id]
        if not include_tainted:
            clauses.append("tainted=0")
        if reflected is not None:
            clauses.append("reflected=?")
            values.append(int(reflected))
        if after is not None:
            clauses.append("finished_at>?")
            values.append(after)
        with self.store._connection() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM bot_episodes WHERE {' AND '.join(clauses)} "
                "ORDER BY finished_at DESC,episode_id DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [_episode(row) for row in rows]

    def count(self, bot_id: str, *, after: str | None, reflected: bool | None = None) -> int:
        clauses: list[str] = ["bot_id=?", "tainted=0"]
        values: list[object] = [bot_id]
        if after is not None:
            clauses.append("finished_at>?")
            values.append(after)
        if reflected is not None:
            clauses.append("reflected=?")
            values.append(int(reflected))
        with self.store._connection() as connection:
            return int(
                connection.execute(
                    f"SELECT count(*) FROM bot_episodes WHERE {' AND '.join(clauses)}", values
                ).fetchone()[0]
            )

    def last_finished(self, bot_id: str) -> str | None:
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT max(finished_at) FROM bot_episodes WHERE bot_id=?", (bot_id,)
            ).fetchone()
        return None if row is None else row[0]

    def mark_reflected(self, episode_ids: Iterable[str]) -> None:
        ids = list(episode_ids)
        if not ids:
            return
        with self.store._connection(write=True) as connection:
            connection.executemany(
                "UPDATE bot_episodes SET reflected=1 WHERE episode_id=?", [(i,) for i in ids]
            )


@dataclass
class _Live:
    info: SessionInfo
    lessons: tuple[str, ...] = ()
    started_at: str | None = None
    tools: int = 0
    sources: set[str] = field(default_factory=set)
    tainted: bool = False  # once a turn of this process was, every later one is too


class EpisodeRecorder:
    """Folds the envelopes of watched Bot sessions into episodes. `observe` runs on the event
    loop and never raises; the store writes run in worker threads, one task each."""

    def __init__(
        self,
        store: EpisodeStore,
        *,
        tasks: set[asyncio.Task[Any]],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._tasks = tasks
        self._now = now
        self._live: dict[str, _Live] = {}
        self._lock = threading.Lock()

    def watch(self, session_id: str, info: SessionInfo, lessons: tuple[str, ...]) -> None:
        """Watch `session_id` from its next turn; at a respawn keep a turn already running."""
        with self._lock:
            live = self._live.get(session_id)
            if live is None:
                self._live[session_id] = _Live(info, lessons)
            else:
                live.info, live.lessons = info, lessons

    def watching(self, session_id: str) -> SessionInfo | None:
        with self._lock:
            live = self._live.get(session_id)
            return None if live is None else live.info

    def observe(self, session_id: str, envelope: dict[str, Any]) -> None:
        try:
            self._observe(session_id, envelope)
        except Exception as error:
            log.warning("Bot episode not recorded: %s", type(error).__name__)

    def _observe(self, session_id: str, envelope: dict[str, Any]) -> None:
        kind = envelope.get("type")
        with self._lock:
            live = self._live.get(session_id)
            if live is None:
                return
            if kind == "session.closed":
                del self._live[session_id]
                return
            data = envelope.get("data") or {}
            if kind == "turn.started":
                live.started_at, live.tools, live.sources = stamp(self._now()), 0, set()
                return
            if kind == "tool.started" and live.started_at is not None:
                live.tools += 1
                source = taint_source(data.get("tool"))
                if source is not None:
                    live.sources.add(source)
                    live.tainted = True
                return
            if kind != "turn.done" or live.started_at is None:
                return
            status = data.get("status")
            if status not in ("success", "cancelled", "error"):
                status = "error"
            finished = self._now()
            started = parse_stamp(live.started_at)
            episode = Episode(
                str(uuid.uuid4()),
                live.info.workspace_id,
                live.info.bot_id,
                session_id,
                live.info.kind,
                live.started_at,
                stamp(max(finished, started + timedelta(milliseconds=1))),
                status,
                live.tainted,
                live.tools,
                live.lessons,
                False,
            )
            sources, info = set(live.sources), live.info
            live.started_at = None
        self._write(session_id, info, sources, episode)

    def _write(
        self, session_id: str, info: SessionInfo, sources: set[str], episode: Episode
    ) -> None:
        def work() -> None:
            for source in sorted(sources):
                self._store.mark_tainted(session_id, info, source, episode.finished_at)
            self._store.record(episode)

        async def run() -> None:
            try:
                await run_sync(work)
            except Exception as error:
                log.warning("Bot episode write failed: %s", type(error).__name__)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
