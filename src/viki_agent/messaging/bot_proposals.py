"""Learning runs, their sealed proposals and lesson counters, in the app's own store (ADR-088).

A run row says which Bot, which mode (`reflect`, `dream`, or a code-only `import` of the Bot's
earlier memory files), what started it and how it ended. A proposal row keeps its op, its target's
id and hash, and its body sealed (`protection`) under a context bound to its vault and id, so the
text the owner approves is the text that was checked. A proposal is `open` until the owner
decides: `applied`, `rejected`, or `stale` when its target changed first; an applied add can be
`undone`. Lesson counters (`bot_lesson_stats`) count uses (`bot_episodes`) and helpful or harmful
verdicts; they live here, never in the vault. Every method blocks.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from viki_agent import protection
from viki_agent.api_json import unique_object
from viki_agent.messaging.bot_recall import LessonStat
from viki_agent.messaging.dream_ops import Proposal, Score
from viki_agent.storage import SessionStore

MAX_RUNS_PER_BOT = 200
MAX_DECIDED_PER_BOT = 300
MAX_OPEN_PER_BOT = 100
MAX_BODY_BYTES = 65536
RUN_REASONS = frozenset(
    {
        "",
        "no_episodes",
        "not_sent",
        "deadline",
        "cancelled",
        "error",
        "no_reply",
        "over_limit",
        "app_closed",
        "internal",
        "too_many_sessions",
        "cli_missing",
        "not_logged_in",
        "billing",
        "crashed",
        "nothing_new",
    }
)
_CONTEXT = b"VIKI bot proposal v1\0"


class ProposalError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Run:
    run_id: str
    workspace_id: str
    bot_id: str
    mode: Literal["reflect", "dream", "import"]
    trigger: Literal["user", "auto"]
    session_id: str | None
    state: Literal["running", "done", "failed"]
    reason: str
    episodes: int
    proposed: int
    rejected: int
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class Stored:
    proposal_id: str
    run_id: str
    workspace_id: str
    bot_id: str
    op: str
    target_id: str | None
    target_sha256: str | None
    state: str
    result_id: str | None
    created_at: str
    decided_at: str | None
    body: dict[str, Any]


_RUN_COLUMNS = (
    "run_id,workspace_id,bot_id,mode,trigger,session_id,state,reason,episodes,proposed,rejected,"
    "started_at,finished_at"
)
_PROPOSAL_COLUMNS = (
    "proposal_id,run_id,workspace_id,bot_id,op,target_id,target_sha256,body,body_sha256,state,"
    "result_id,created_at,decided_at"
)


def _context(workspace_id: str, proposal_id: str) -> bytes:
    return _CONTEXT + workspace_id.encode("ascii") + b"\0" + proposal_id.encode("ascii")


class ProposalStore:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    # --- runs

    def start_run(
        self,
        workspace_id: str,
        bot_id: str,
        mode: Literal["reflect", "dream", "import"],
        trigger: Literal["user", "auto"],
        session_id: str | None,
        now: str,
    ) -> Run:
        run = Run(
            str(uuid.uuid4()),
            workspace_id,
            bot_id,
            mode,
            trigger,
            session_id,
            "running",
            "",
            0,
            0,
            0,
            now,
            None,
        )
        with self.store._connection(write=True) as connection:
            connection.execute(
                f"INSERT INTO bot_runs ({_RUN_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run.run_id,
                    workspace_id,
                    bot_id,
                    mode,
                    trigger,
                    session_id,
                    "running",
                    "",
                    0,
                    0,
                    0,
                    now,
                    None,
                ),
            )
            self._prune_runs(connection, bot_id)
        return run

    def finish_run(
        self,
        run_id: str,
        *,
        reason: str,
        episodes: int,
        proposed: int,
        rejected: int,
        now: str,
    ) -> None:
        if reason not in RUN_REASONS:
            reason = "internal"
        with self.store._connection(write=True) as connection:
            connection.execute(
                "UPDATE bot_runs SET state=?,reason=?,episodes=?,proposed=?,rejected=?,"
                "finished_at=? WHERE run_id=? AND state='running'",
                (
                    "done" if reason in ("", "nothing_new") else "failed",
                    reason,
                    min(episodes, 1000),
                    min(proposed, 1000),
                    min(rejected, 1000),
                    now,
                    run_id,
                ),
            )

    def end_running(self, now: str) -> int:
        """At startup: a run left running by a closed app ended then."""
        with self.store._connection(write=True) as connection:
            return connection.execute(
                "UPDATE bot_runs SET state='failed',reason='app_closed',finished_at=? "
                "WHERE state='running'",
                (now,),
            ).rowcount

    def runs(self, bot_id: str, limit: int = 20) -> list[Run]:
        with self.store._connection() as connection:
            rows = connection.execute(
                f"SELECT {_RUN_COLUMNS} FROM bot_runs WHERE bot_id=? "
                "ORDER BY started_at DESC,rowid DESC LIMIT ?",
                (bot_id, limit),
            ).fetchall()
        return [Run(*row) for row in rows]

    def last_run(
        self, bot_id: str, mode: Literal["reflect", "dream", "import"], *, done: bool = True
    ) -> Run | None:
        clause = " AND state='done'" if done else ""
        with self.store._connection() as connection:
            row = connection.execute(
                f"SELECT {_RUN_COLUMNS} FROM bot_runs WHERE bot_id=? AND mode=?{clause} "
                "ORDER BY started_at DESC,rowid DESC LIMIT 1",
                (bot_id, mode),
            ).fetchone()
        return None if row is None else Run(*row)

    def runs_since(self, bot_id: str, mode: str, since: str) -> int:
        with self.store._connection() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM bot_runs WHERE bot_id=? AND mode=? AND started_at>=?",
                    (bot_id, mode, since),
                ).fetchone()[0]
            )

    def _prune_runs(self, connection: sqlite3.Connection, bot_id: str) -> None:
        old = connection.execute(
            "SELECT run_id FROM bot_runs WHERE bot_id=? AND state!='running' AND run_id NOT IN "
            "(SELECT run_id FROM bot_proposals WHERE state='open') ORDER BY started_at DESC,"
            "run_id DESC LIMIT -1 OFFSET ?",
            (bot_id, MAX_RUNS_PER_BOT),
        ).fetchall()
        for (run_id,) in old:
            connection.execute("DELETE FROM bot_proposals WHERE run_id=?", (run_id,))
            connection.execute("DELETE FROM bot_runs WHERE run_id=?", (run_id,))

    # --- proposals

    def add(self, run: Run, proposals: Iterable[Proposal], now: str) -> int:
        """Seal and store each proposal as open; answers how many were stored. Past the Bot's
        MAX_OPEN_PER_BOT open proposals the rest are dropped."""
        rows = []
        for proposal in proposals:
            proposal_id = str(uuid.uuid4())
            plain = json.dumps(proposal.body(), ensure_ascii=False, sort_keys=True).encode()
            if len(plain) > MAX_BODY_BYTES:
                continue
            try:
                sealed = protection.protect(plain, context=_context(run.workspace_id, proposal_id))
            except protection.ProtectionError:
                raise ProposalError("unavailable") from None
            rows.append(
                (
                    proposal_id,
                    run.run_id,
                    run.workspace_id,
                    run.bot_id,
                    proposal.op,
                    proposal.target_id,
                    proposal.target_sha256,
                    sealed,
                    hashlib.sha256(plain).hexdigest(),
                    "open",
                    None,
                    now,
                    None,
                )
            )
        if not rows:
            return 0
        with self.store._connection(write=True) as connection:
            open_now = connection.execute(
                "SELECT count(*) FROM bot_proposals WHERE bot_id=? AND state='open'",
                (run.bot_id,),
            ).fetchone()[0]
            rows = rows[: max(0, MAX_OPEN_PER_BOT - open_now)]
            connection.executemany(
                f"INSERT INTO bot_proposals ({_PROPOSAL_COLUMNS}) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def _decode(self, row: tuple[Any, ...]) -> Stored:
        (
            proposal_id,
            run_id,
            workspace_id,
            bot_id,
            op,
            target_id,
            target_sha256,
            body,
            body_sha256,
            state,
            result_id,
            created_at,
            decided_at,
        ) = row
        try:
            plain = protection.unprotect(body, context=_context(workspace_id, proposal_id))
        except protection.ProtectionError:
            raise ProposalError("unavailable") from None
        if hashlib.sha256(plain).hexdigest() != body_sha256:
            raise ProposalError("corrupt")
        try:
            value = json.loads(plain.decode("utf-8"), object_pairs_hook=unique_object)
        except ValueError, UnicodeDecodeError:
            raise ProposalError("corrupt") from None
        if not isinstance(value, dict) or value.get("op") != op:
            raise ProposalError("corrupt")
        return Stored(
            proposal_id,
            run_id,
            workspace_id,
            bot_id,
            op,
            target_id,
            target_sha256,
            state,
            result_id,
            created_at,
            decided_at,
            value,
        )

    def get(self, proposal_id: str) -> Stored:
        with self.store._connection() as connection:
            row = connection.execute(
                f"SELECT {_PROPOSAL_COLUMNS} FROM bot_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise ProposalError("not_found")
        return self._decode(tuple(row))

    def listing(self, bot_id: str, *, limit: int = 60) -> list[Stored]:
        """The Bot's open proposals, oldest first, then its newest decided ones."""
        with self.store._connection() as connection:
            rows = connection.execute(
                f"SELECT {_PROPOSAL_COLUMNS} FROM bot_proposals WHERE bot_id=? AND state='open' "
                "ORDER BY created_at,rowid LIMIT ?",
                (bot_id, limit),
            ).fetchall()
            rows += connection.execute(
                f"SELECT {_PROPOSAL_COLUMNS} FROM bot_proposals WHERE bot_id=? AND state!='open' "
                "ORDER BY decided_at DESC,proposal_id LIMIT 20",
                (bot_id,),
            ).fetchall()
        return [self._decode(tuple(row)) for row in rows]

    def open_targets(self, bot_id: str) -> set[str]:
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT target_id FROM bot_proposals WHERE bot_id=? AND state='open' "
                "AND target_id IS NOT NULL",
                (bot_id,),
            ).fetchall()
        return {row[0] for row in rows}

    def decide(
        self,
        proposal_id: str,
        *,
        expected: str,
        state: Literal["applied", "rejected", "stale", "undone"],
        now: str,
        result_id: str | None = None,
    ) -> None:
        """Move one proposal from `expected` to `state`; another decision first is a conflict."""
        with self.store._connection(write=True) as connection:
            changed = connection.execute(
                "UPDATE bot_proposals SET state=?,decided_at=?,result_id=coalesce(?,result_id) "
                "WHERE proposal_id=? AND state=?",
                (state, now, result_id, proposal_id, expected),
            ).rowcount
            if changed != 1:
                raise ProposalError("conflict")
            bot = connection.execute(
                "SELECT bot_id FROM bot_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()[0]
            connection.execute(
                "DELETE FROM bot_proposals WHERE bot_id=? AND state!='open' AND proposal_id IN ("
                "SELECT proposal_id FROM bot_proposals WHERE bot_id=? AND state!='open' "
                "ORDER BY decided_at DESC,proposal_id LIMIT -1 OFFSET ?)",
                (bot, bot, MAX_DECIDED_PER_BOT),
            )

    # --- lesson counters

    def score(self, workspace_id: str, bot_id: str, scores: Iterable[Score]) -> None:
        with self.store._connection(write=True) as connection:
            for item in scores:
                helpful, harmful = (1, 0) if item.verdict == "helpful" else (0, 1)
                connection.execute(
                    "INSERT INTO bot_lesson_stats VALUES (?,?,?,0,?,?,NULL) "
                    "ON CONFLICT(workspace_id,memory_id) DO UPDATE SET "
                    "helpful=helpful+excluded.helpful,harmful=harmful+excluded.harmful",
                    (workspace_id, item.memory_id, bot_id, helpful, harmful),
                )

    def stats(self, workspace_id: str, bot_id: str) -> dict[str, LessonStat]:
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT memory_id,uses,helpful,harmful,last_used_at FROM bot_lesson_stats "
                "WHERE workspace_id=? AND bot_id=?",
                (workspace_id, bot_id),
            ).fetchall()
        return {row[0]: LessonStat(row[1], row[2], row[3], row[4]) for row in rows}


def summary(stored: Stored) -> Mapping[str, Any]:
    """A proposal as the Bot page shows it."""
    body = stored.body
    return {
        "proposal_id": stored.proposal_id,
        "run_id": stored.run_id,
        "op": stored.op,
        "state": stored.state,
        "claim": body.get("claim", ""),
        "target_id": stored.target_id,
        "target_claim": body.get("target_claim", ""),
        "why": body.get("why", ""),
        "evidence": [
            {"session_id": item.get("session_id"), "quote": item.get("quote")}
            for item in body.get("evidence", [])
        ],
        "result_id": stored.result_id,
        "created_at": stored.created_at,
        "decided_at": stored.decided_at,
    }
