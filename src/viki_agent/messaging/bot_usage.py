"""Bot token usage: the chats each Bot owns in a vault, and their transcripts' calls in the store.

`BotUsage.sessions` lists a vault's Bot chats by `BotService.chat_session_ids`'s owner rule, each
with its Bot, kind and model connection. `BotUsage.capture` reads the new whole lines of each
chat's transcript and of its `subagents/agent-*.jsonl` transcripts (`usage_reader.read_calls`)
and records each assistant call's token counts in `bot_usage_calls`, one row per message: a line
read again merges into its row (the max of each count) and adds no row. The sessions it names
present also include the vault's logged sessions that are no Bot's chat now, such as a removed
Bot's, while their transcript exists.

A capture remembers each transcript path it finds a file at, until a capture finds that file
gone; it looks up the paths of all other sessions, those with no file found yet included, in one
`Catalog.paths_for` call. Reads resume from in-memory cursors and stop at the capture's byte and
file budget; a session with lines left counts as pending. A session is not opened while its
transcript's mtime is not past its `read_ns`, the time stored once every file of the session was
read to its end. Every read happens before the capture's first write transaction; a transaction
records at most MAX_WRITE_ROWS calls, and WRITE_GAP_S passes between two of them, so an
interactive writer waiting on the store's lock (`storage.BUSY_TIMEOUT_S`) gets it. A busy store
ends the capture and keeps what committed; `stop()` does too, checked before each session's read
and before each write transaction (pruning's included), and every later capture on that instance
then returns at once, reading and writing nothing. After a capture adds rows, pruning keeps the
newest MAX_CALLS calls; after any capture, at most every AGE_PRUNE_EVERY_S, it drops calls older
than MAX_AGE_DAYS + 1 days; then it drops the sessions left without calls. The module writes no
log lines.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from stat import S_ISREG
from typing import Protocol

from viki_agent import protection
from viki_agent.automation.routines import stamp
from viki_agent.conversations.drafts import require_private_store
from viki_agent.conversations.subagents_api import MAX_WALK_ENTRIES, _inside
from viki_agent.llm.connections import ConnectionError, split_model
from viki_agent.messaging.usage_reader import Call, read_calls
from viki_agent.profiles.service import ProfileError
from viki_agent.profiles.service import _id as canonical_id
from viki_agent.storage import SessionStore, StateError

MAX_CAPTURE_BYTES = 64 * 1024 * 1024  # transcript bytes one capture reads
MAX_CAPTURE_FILES = 4096  # transcript files one capture opens
MAX_WRITE_ROWS = 100  # calls one write transaction records
MAX_CALLS = 100_000
MAX_AGE_DAYS = 90
PRUNE_CHUNK = 50  # calls one pruning transaction deletes
AGE_PRUNE_EVERY_S = 600
SETTLE_NS = 2_000_000_000  # a file modified this recently is read again by the next capture
WRITE_GAP_S = 0.03
LOCK_TIMEOUT_S = 10
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# The chats of BotService.chat_session_ids's owner rule, each with its owner and its kind, the
# first that holds of: a routine run (ProfileLaunchService._binding's test), a Bot-thread seat,
# the Bot's current chat, else a task thread.
_SESSIONS = (
    "WITH owned(session_id,bot_id) AS (SELECT chat_session_id,bot_id FROM bots "
    "UNION SELECT m.session_id,m.bot_id FROM session_bot_memory m "
    "JOIN bots bot ON bot.bot_id=m.bot_id "
    "UNION SELECT b.session_id,bot.bot_id FROM session_profile_bindings b "
    "JOIN bots bot ON bot.profile_id=b.profile_id AND bot.workspace_id=b.workspace_id "
    "WHERE NOT EXISTS (SELECT 1 FROM session_bot_memory owner "
    "WHERE owner.session_id=b.session_id AND owner.bot_id!=bot.bot_id)) "
    "SELECT o.session_id,bot.bot_id,bot.profile_id,CASE WHEN b.launch_origin='routine' "
    "OR EXISTS (SELECT 1 FROM task_attempts t WHERE t.session_id=o.session_id) THEN 'routine' "
    "WHEN EXISTS (SELECT 1 FROM bot_thread_sessions r WHERE r.session_id=o.session_id) "
    "THEN 'bot_thread' WHEN o.session_id=bot.chat_session_id THEN 'main' ELSE 'task' END,"
    "s.model,coalesce(b.native_started,0) FROM owned o JOIN bots bot ON bot.bot_id=o.bot_id "
    "LEFT JOIN session_profile_bindings b ON b.session_id=o.session_id "
    "LEFT JOIN sessions s ON s.session_id=o.session_id WHERE bot.workspace_id=? "
    "ORDER BY b.created_at DESC,o.session_id DESC"
)
_UPSERT = (
    "INSERT INTO bot_usage_calls VALUES (?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(session_id,message_id) DO UPDATE SET "
    "prompt_id=coalesce(prompt_id,excluded.prompt_id),at=min(at,excluded.at),"
    "model=CASE WHEN model<>'' THEN model ELSE excluded.model END,"
    "agent=max(agent,excluded.agent),input_tokens=max(input_tokens,excluded.input_tokens),"
    "output_tokens=max(output_tokens,excluded.output_tokens),"
    "cache_read_tokens=max(cache_read_tokens,excluded.cache_read_tokens),"
    "cache_write_tokens=max(cache_write_tokens,excluded.cache_write_tokens)"
)
# Calls over the cap, whether a call is older than the cutoff, whether a session has no calls.
_PRUNE_STATE = (
    "SELECT (SELECT count(*) FROM bot_usage_calls)-?,"
    "EXISTS (SELECT 1 FROM bot_usage_calls WHERE at<?),"
    "EXISTS (SELECT 1 FROM bot_usage_sessions s WHERE NOT EXISTS "
    "(SELECT 1 FROM bot_usage_calls c WHERE c.session_id=s.session_id))"
)
_DELETE_OLDEST = (
    "DELETE FROM bot_usage_calls WHERE rowid IN "
    "(SELECT rowid FROM bot_usage_calls ORDER BY at LIMIT ?)"
)
_DELETE_OLD = (
    "DELETE FROM bot_usage_calls WHERE rowid IN "
    "(SELECT rowid FROM bot_usage_calls WHERE at<? ORDER BY at LIMIT ?)"
)
_DELETE_EMPTY = (
    "DELETE FROM bot_usage_sessions WHERE NOT EXISTS "
    "(SELECT 1 FROM bot_usage_calls c WHERE c.session_id=bot_usage_sessions.session_id)"
)

_Cursor = tuple[int, str | None, int]  # a file's read offset, prompt id there, and size


class _Transcripts(Protocol):
    def paths_for(self, session_ids: list[str]) -> dict[str, Path]: ...


@dataclass(frozen=True)
class Attribution:
    """One Bot chat: its Bot, its kind (`routine`, `bot_thread`, `main` or `task`), the model
    connection it runs on (None for none), and whether its native session started."""

    session_id: str
    workspace_id: str
    bot_id: str
    profile_id: str
    kind: str
    connection_id: str | None
    started: bool


@dataclass(frozen=True)
class Capture:
    """One `capture`: the sessions with a transcript (the Bot chats', and the logged sessions'
    that are no Bot's chat now), how many of the Bot chats still have lines to read, and the call
    rows it added."""

    present: frozenset[str]
    pending: int
    added: int


class BotUsageError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Read:
    """A session's calls read by one capture, its cursors after them, and the `read_ns` to store,
    None until every file of the session was read to its end."""

    target: Attribution
    calls: list[Call]
    cursors: dict[Path, _Cursor]
    read_ns: int | None


def _failure(error: StateError) -> BotUsageError:
    return BotUsageError("corrupt" if error.code == "corrupt" else "unavailable")


def _agents(main: Path, session_id: str) -> list[Path]:
    """The session's `agent-*.jsonl` subagent transcripts among the first MAX_WALK_ENTRIES
    entries of its `subagents` folder, each a regular file resolving inside that folder."""
    folder = main.parent.resolve() / session_id / "subagents"
    try:
        with os.scandir(folder) as entries:
            names = sorted(entry.name for entry in islice(entries, MAX_WALK_ENTRIES))
    except FileNotFoundError, NotADirectoryError:
        return []
    return [
        folder / name
        for name in names
        if name.startswith("agent-") and name.endswith(".jsonl") and _inside(folder / name, folder)
    ]


def _logged(
    targets: list[Attribution], stored: dict[str, int | None], wanted: set[str] | None
) -> list[str]:
    """The logged sessions among `wanted` that are not among `targets`, such as a removed Bot's
    chats: a capture names them present while their transcript exists, and reads none of them."""
    chats = {target.session_id for target in targets}
    return [sid for sid in stored if sid not in chats and (wanted is None or sid in wanted)]


def _chunks(reads: list[_Read]) -> Iterator[list[tuple[_Read, Call | None]]]:
    """`reads` as write transactions of at most MAX_WRITE_ROWS calls: each session's calls, then
    None, which marks where its cursors advance."""
    chunk: list[tuple[_Read, Call | None]] = []
    rows = 0
    for item in reads:
        for call in (*item.calls, None):
            if call is not None and rows == MAX_WRITE_ROWS:
                yield chunk
                chunk, rows = [], 0
            chunk.append((item, call))
            rows += call is not None
    if chunk:
        yield chunk


class _Stopped(Exception):
    """Raised by `BotUsage._transaction` once `stop()` has been called; caught in `_capture`."""


class BotUsage:
    def __init__(
        self,
        store: SessionStore,
        catalog: _Transcripts,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store, self.catalog, self.now, self.sleep = store, catalog, now, sleep
        self._lock = threading.Lock()  # one capture at a time; guards the fields below
        # ponytail: one cursor per transcript file read since start, never dropped; bound it
        # if Bot chats reach tens of thousands of files. A restart re-reads, merging into rows.
        self._cursors: dict[Path, _Cursor] = {}
        # The transcript path of each session whose file a capture found, until one finds it
        # gone. Every other session is looked up at each capture, so a root recorded after a
        # capture found no file is picked up by the next. ponytail: an entry stays while no
        # capture asks about its session, like the cursors; bound it with them.
        self._paths: dict[str, Path] = {}
        self._aged: datetime | None = None  # when the age pruning last finished
        self._wrote = False  # whether the running capture committed a write transaction
        # Set by stop(); checked before each session's read and every write transaction.
        self._stop = threading.Event()

    def sessions(self, workspace_id: str) -> list[Attribution]:
        """The vault's Bot chats, newest binding first, in one read.

        Raises BotUsageError: `corrupt` or `unavailable` for a store fault.
        """
        return self._load(workspace_id, None)[0]

    def stop(self) -> None:
        """Stop this capture's current and future work before its next session read or write
        transaction. Never blocks; calling it again has no further effect."""
        self._stop.set()

    def capture(
        self,
        workspace_id: str,
        session_ids: Iterable[str] | None = None,
        *,
        budget: int = MAX_CAPTURE_BYTES,
    ) -> Capture:
        """Record the new calls of the vault's Bot chats, or of those among `session_ids`, newest
        binding first, within `budget` transcript bytes and MAX_CAPTURE_FILES files. Present
        also names the vault's logged sessions, of those among `session_ids`, that are no Bot's
        chat now, while their transcript exists. A capture still running gets LOCK_TIMEOUT_S to
        finish, else every Bot chat counts as pending and the sessions with a transcript as
        present, with no transcript read and no store write. Once `stop()` has been called,
        returns at once with nothing present, pending or added, reading no transcript and
        touching no store; a stop during the reads ends the capture before the next session's
        read, with nothing present, pending or added and nothing written.

        Raises BotUsageError: `corrupt` or `unavailable` for a store fault other than busy.
        """
        if self._stop.is_set():
            return Capture(frozenset(), 0, 0)
        wanted = None if session_ids is None else set(session_ids)
        if not self._lock.acquire(timeout=LOCK_TIMEOUT_S):
            targets, stored = self._load(workspace_id, wanted)
            ids = [target.session_id for target in targets] + _logged(targets, stored, wanted)
            paths = self.catalog.paths_for(ids)
            present = frozenset(sid for sid, path in paths.items() if path.is_file())
            return Capture(present, len(targets), 0)
        try:
            if self._stop.is_set():
                return Capture(frozenset(), 0, 0)
            targets, stored = self._load(workspace_id, wanted)
            return self._capture(targets, stored, _logged(targets, stored, wanted), budget)
        finally:
            self._lock.release()

    def _load(
        self, workspace_id: str, wanted: set[str] | None
    ) -> tuple[list[Attribution], dict[str, int | None]]:
        """The vault's Bot chats, or those in `wanted`, and each logged session's `read_ns`."""
        try:
            require_private_store(self.store)
            with self.store._connection() as connection:
                rows = connection.execute(_SESSIONS, (workspace_id,)).fetchall()
                stored = dict(
                    connection.execute(
                        "SELECT session_id,read_ns FROM bot_usage_sessions WHERE workspace_id=?",
                        (workspace_id,),
                    ).fetchall()
                )
        except protection.ProtectionError:
            raise BotUsageError("unavailable") from None
        except StateError as error:
            raise _failure(error) from None
        targets = []
        for session_id, bot_id, profile_id, kind, model, started in rows:
            if wanted is not None and session_id not in wanted:
                continue
            try:
                for value in (session_id, bot_id, profile_id):
                    canonical_id(value)
                route = split_model(model)
            except ProfileError:
                raise BotUsageError("corrupt") from None
            except ConnectionError:
                continue  # a malformed model ref skips the session
            connection_id = None if route is None else route[0]
            targets.append(
                Attribution(
                    session_id, workspace_id, bot_id, profile_id, kind, connection_id, bool(started)
                )
            )
        return targets, stored

    def _capture(
        self,
        targets: list[Attribution],
        stored: dict[str, int | None],
        logged: list[str],
        budget: int,
    ) -> Capture:
        present: set[str] = set()
        settled: set[str] = set()  # present sessions with no line left to read
        reads: list[_Read] = []
        files = MAX_CAPTURE_FILES
        looked_up = self._resolve([*(target.session_id for target in targets), *logged])
        for target in targets:
            if self._stop.is_set():
                return Capture(frozenset(), 0, 0)
            found = self._stat(target.session_id, looked_up)
            if found is None:
                continue
            main, info = found
            present.add(target.session_id)
            read_ns = stored.get(target.session_id)
            if read_ns is not None and info.st_mtime_ns <= read_ns:
                settled.add(target.session_id)
                continue
            if budget <= 0 or files <= 0:
                continue
            try:
                item, used, opened = self._read(target, main, budget, files)
            except OSError:
                continue  # an unreadable transcript leaves its session pending
            budget, files = budget - used, files - opened
            if item.calls or item.read_ns is not None:
                reads.append(item)
            else:
                self._cursors.update(item.cursors)  # nothing to record from what was read
        kept = frozenset(sid for sid in logged if self._stat(sid, looked_up) is not None)
        self._wrote = False
        added = 0
        try:
            for chunk in _chunks(reads):
                added += self._write(chunk)
                for item, call in chunk:
                    if call is None:
                        self._cursors.update(item.cursors)
                        if item.read_ns is not None:
                            settled.add(item.target.session_id)
            self._prune(added > 0)
        except _Stopped:
            pass
        except StateError as error:
            if error.code != "busy":
                raise _failure(error) from None
        return Capture(frozenset(present) | kept, len(present - settled), added)

    def _resolve(self, session_ids: list[str]) -> dict[str, Path]:
        """The transcript paths of those of `session_ids` with none remembered, from one
        `Catalog.paths_for` call."""
        unknown = [session_id for session_id in session_ids if session_id not in self._paths]
        return self.catalog.paths_for(unknown) if unknown else {}

    def _stat(
        self, session_id: str, looked_up: dict[str, Path]
    ) -> tuple[Path, os.stat_result] | None:
        """The session's transcript path, remembered or `looked_up`, and its stat while that is
        a regular file, which remembers the path; else None, and the path is forgotten."""
        path = self._paths.get(session_id, looked_up.get(session_id))
        if path is None:
            return None  # the catalog named no path: not a canonical UUID
        try:
            info = path.stat()
        except OSError:
            info = None
        if info is None or not S_ISREG(info.st_mode):
            self._paths.pop(session_id, None)
            return None
        self._paths[session_id] = path
        return path, info

    def _read(
        self, target: Attribution, main: Path, budget: int, files: int
    ) -> tuple[_Read, int, int]:
        """The session's calls after its cursors within `budget` bytes and `files` files, and the
        bytes and files that took. Each file is stat'ed before it is read, so a write after the
        read leaves its mtime past the stored `read_ns`."""
        calls: list[Call] = []
        cursors: dict[Path, _Cursor] = {}
        newest = used = opened = 0
        complete = True
        agents = [(True, path) for path in _agents(main, target.session_id)]
        for agent, path in [(False, main), *agents]:
            info = path.stat()
            newest = max(newest, info.st_mtime_ns)
            offset, prompt_id, size = self._cursors.get(path, (0, None, 0))
            if info.st_size < size:
                offset, prompt_id = 0, None  # the file shrank: read it again from its start
            if offset < info.st_size:
                if used >= budget or opened >= files:
                    complete = False
                    break
                read = read_calls(path, offset, budget - used, agent=agent, prompt_id=prompt_id)
                used, opened = used + read.offset - offset, opened + 1
                offset, prompt_id = read.offset, read.prompt_id
                calls.extend(read.calls)
                complete = complete and read.done
            cursors[path] = (offset, prompt_id, max(info.st_size, offset))
        now_ns = (self.now() - _EPOCH) // timedelta(microseconds=1) * 1000
        read_ns = max(0, min(newest, now_ns - SETTLE_NS)) if complete else None
        return _Read(target, calls, cursors, read_ns), used, opened

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction, WRITE_GAP_S after the capture's previous one; raises _Stopped
        instead of opening one once `stop()` has been called."""
        if self._wrote:
            self.sleep(WRITE_GAP_S)
        if self._stop.is_set():
            raise _Stopped
        with self.store._connection(write=True) as connection:
            yield connection
        self._wrote = True

    def _write(self, chunk: list[tuple[_Read, Call | None]]) -> int:
        """Commit one transaction of `chunk`'s rows; the number of call rows it added."""
        targets = {item.target.session_id: item.target for item, _call in chunk}
        rows = [
            (
                item.target.session_id,
                call.message_id,
                call.prompt_id,
                call.at,
                call.model,
                int(call.agent),
                call.input,
                call.output,
                call.cache_read,
                call.cache_write,
            )
            for item, call in chunk
            if call is not None
        ]
        keys = list(dict.fromkeys((row[0], row[1]) for row in rows))
        with self._transaction() as connection:
            existing = 0
            if keys:
                (existing,) = connection.execute(
                    "SELECT count(*) FROM bot_usage_calls WHERE (session_id,message_id) IN "
                    f"(VALUES {','.join(['(?,?)'] * len(keys))})",
                    [value for key in keys for value in key],
                ).fetchone()
            connection.executemany(
                "INSERT OR IGNORE INTO bot_usage_sessions VALUES (?,?,?,?,?,?,NULL)",
                [
                    (t.session_id, t.workspace_id, t.bot_id, t.profile_id, t.kind, t.connection_id)
                    for t in targets.values()
                ],
            )
            connection.executemany(_UPSERT, rows)
            connection.executemany(
                "UPDATE bot_usage_sessions SET read_ns=? WHERE session_id=?",
                [
                    (item.read_ns, item.target.session_id)
                    for item, call in chunk
                    if call is None and item.read_ns is not None
                ],
            )
        return len(keys) - existing

    def _prune(self, added: bool) -> None:
        """Delete, PRUNE_CHUNK calls a transaction, the oldest calls over MAX_CALLS when the
        capture `added` calls, then, at most every AGE_PRUNE_EVERY_S whether it added any or
        not, the calls older than MAX_AGE_DAYS + 1 days, then the sessions left without calls."""
        moment = self.now()
        due = self._aged is None or not (
            timedelta(0) <= moment - self._aged < timedelta(seconds=AGE_PRUNE_EVERY_S)
        )
        if not (added or due):
            return
        cutoff = stamp(moment - timedelta(days=MAX_AGE_DAYS + 1)) if due else ""
        while True:
            with self.store._connection() as connection:
                over, old, empty = connection.execute(_PRUNE_STATE, (MAX_CALLS, cutoff)).fetchone()
            if added and over > 0:
                sql, values = _DELETE_OLDEST, (min(over, PRUNE_CHUNK),)
            elif old:
                sql, values = _DELETE_OLD, (cutoff, PRUNE_CHUNK)
            elif empty:
                sql, values = _DELETE_EMPTY, ()
            else:
                break
            with self._transaction() as connection:
                connection.execute(sql, values)
        if due:
            self._aged = moment
