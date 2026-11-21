"""Read-only Bot usage reports over P1's log (`bot_usage_sessions`, `bot_usage_calls`).

``summary`` folds one workspace's calls over a local day window into totals, a daily series,
bot and route breakdowns and the largest sessions; ``trace`` returns one session's own call
log. Each reads the log in chunks of at most CHUNK_ROWS calls, every statement autocommitted
on its own, and writes nothing. Retention counts
(``max_rows``, ``max_age_days``), ``pending`` and ``unlogged`` come from the caller: the
capture service (``messaging/bot_usage.py``) lands separately and is never imported here.
"""

from __future__ import annotations

import heapq
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from time import sleep
from typing import Any

from viki_agent.automation.routines import stamp
from viki_agent.messaging.usage_reader import _MAX_COUNT
from viki_agent.storage import BUSY_TIMEOUT_S, SessionStore

MAX_BOT_ROWS = 32
MAX_ROUTE_ROWS = 20
MAX_LARGEST_ROWS = 10
MAX_TRACE_CALLS = 2000
# Calls one read statement visits. The store keeps a rollback journal: a reader's SHARED lock
# blocks every writer's commit until its statement completes, and a writer waits only
# storage.BUSY_TIMEOUT_S, so each statement stays a few milliseconds long at the 100,000-call
# cap and a commit gets in between two of them. Chunks are separate snapshots: a capture that
# commits between two of them can leave one answer off by the rows it touched (an upsert can
# also move a call's `at` earlier); that is accepted rather than hidden behind a lock.
CHUNK_ROWS = 500
BUSY_RETRIES = 3  # reruns of a chunk that meets SQLITE_BUSY, a writer committing
_KINDS = ("main", "task", "bot_thread", "routine", "subagent")
# The next CHUNK_ROWS calls after a rowid, every vault's: the last rowid among them, how many
# there are and the oldest `at`.
_RANGE = (
    "SELECT max(id), count(*), min(at) FROM (SELECT rowid AS id, at FROM bot_usage_calls "
    "WHERE rowid>? ORDER BY rowid LIMIT ?)"
)
# Those calls inside the period, grouped as `summary` folds them. CROSS JOIN keeps the calls as
# the outer loop, so the rowid range bounds the statement.
_GROUPS = (
    "SELECT c.session_id, date(c.at,'localtime'), c.agent, c.model, "
    "s.bot_id, s.profile_id, s.kind, s.connection_id, "
    "sum(c.input_tokens), sum(c.output_tokens), "
    "sum(c.cache_read_tokens), sum(c.cache_write_tokens), count(*), "
    "min(c.at), max(c.at) "
    "FROM bot_usage_calls c CROSS JOIN bot_usage_sessions s ON s.session_id=c.session_id "
    "WHERE s.workspace_id=? AND c.at>=? AND c.at<? AND c.rowid>? AND c.rowid<=? "
    "GROUP BY c.session_id, date(c.at,'localtime'), c.agent, c.model"
)
# One session's next CHUNK_ROWS calls after a message id, along its UNIQUE index; each row
# leads with the (at, rowid) key `trace` keeps the newest calls by.
_TRACE = (
    "SELECT at, rowid, message_id, prompt_id, model, agent, input_tokens, output_tokens, "
    "cache_read_tokens, cache_write_tokens FROM bot_usage_calls "
    "WHERE session_id=? AND message_id>? ORDER BY message_id LIMIT ?"
)


@dataclass(frozen=True)
class Names:
    """Current display names the caller resolved: ``profile_id`` -> name, the ``bot_id``s that
    still exist, and ``connection_id`` -> name. A key missing from ``profiles`` or
    ``connections`` means the profile or connection it named no longer exists."""

    profiles: Mapping[str, str]
    bots: frozenset[str]
    connections: Mapping[str, str]


def _safe(count: int) -> int:
    """*count* saturated at 2^53-1, so every count the report sums or counts from the log stays
    a safe integer, as the wire requires: one call's count may be as large as that
    (``usage_reader`` keeps any up to it), so a sum of them can pass it."""
    return min(count, _MAX_COUNT)


@dataclass
class _Totals:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    calls: int = 0

    def add(self, input_: int, output_: int, cache_read: int, cache_write: int, calls: int) -> None:
        self.input += input_
        self.output += output_
        self.cache_read += cache_read
        self.cache_write += cache_write
        self.calls += calls

    def tokens(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    def as_dict(self) -> dict[str, int]:
        return {
            "input": _safe(self.input),
            "output": _safe(self.output),
            "cache_read": _safe(self.cache_read),
            "cache_write": _safe(self.cache_write),
            "calls": _safe(self.calls),
        }


@dataclass
class _Bot:
    profile_id: str
    totals: _Totals = field(default_factory=_Totals)
    sessions: set[str] = field(default_factory=set)


@dataclass
class _Session:
    bot_id: str
    kind: str
    totals: _Totals = field(default_factory=_Totals)
    first_at: str = ""
    last_at: str = ""


def _period(now: datetime, days: int) -> tuple[date, str, str]:
    """The window's first local date, and its ``[start, end)`` UTC stamps: local midnight of
    *first* through local midnight of the day after *now*'s local today."""
    today = now.astimezone().date()
    first = today - timedelta(days=days - 1)
    start = stamp(datetime.combine(first, time()).astimezone(UTC))
    end = stamp(datetime.combine(today + timedelta(days=1), time()).astimezone(UTC))
    return first, start, end


def _fetch(connection: sqlite3.Connection, sql: str, values: tuple[Any, ...]) -> list[Any]:
    """*sql*'s rows from one autocommit statement. A statement that meets SQLITE_BUSY, a writer
    committing, runs again up to BUSY_RETRIES times, BUSY_TIMEOUT_S apart, then raises."""
    retries = BUSY_RETRIES
    while True:
        try:
            return connection.execute(sql, values).fetchall()
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", 0) & 0xFF
            if retries == 0 or code != sqlite3.SQLITE_BUSY:
                raise
        retries -= 1
        sleep(BUSY_TIMEOUT_S)


def _scan(
    connection: sqlite3.Connection, workspace_id: str, start: str, end: str
) -> tuple[list[Any], int, str | None]:
    """The workspace's calls in ``[start, end)`` grouped by session, local date, agent and
    model, then the count and oldest `at` of every call in the log, every vault's, from one
    pass over it in rowid order, CHUNK_ROWS calls a chunk."""
    groups: list[Any] = []
    rows, oldest, after = 0, None, 0
    while True:
        [(last, count, first)] = _fetch(connection, _RANGE, (after, CHUNK_ROWS))
        if last is None:
            return groups, rows, oldest
        rows += count
        if oldest is None or first < oldest:
            oldest = first
        groups += _fetch(connection, _GROUPS, (workspace_id, start, end, after, last))
        after = last


def summary(
    store: SessionStore,
    workspace_id: str,
    days: int,
    *,
    now: datetime,
    present: frozenset[str],
    pending: int,
    unlogged: int,
    names: Names,
    max_rows: int,
    max_age_days: int,
) -> dict[str, Any]:
    """The workspace's usage dashboard for the *days* local dates ending today (``now``'s local
    date). ``present`` names the session ids a transcript still exists for; a session logged
    here but absent from it is "log only" in ``coverage``. ``pending``, ``unlogged``,
    ``max_rows`` and ``max_age_days`` are passed straight into ``coverage``. Its ``rows`` and
    ``oldest_at`` describe the whole log, every vault's calls, as the log-wide retention limits
    ``max_rows`` and ``max_age_days`` do; the rest of the answer is the workspace's."""
    first, start, end = _period(now, days)
    with store._connection() as connection:
        rows, total_rows, oldest_at = _scan(connection, workspace_id, start, end)

    totals = _Totals()
    kinds = {name: _Totals() for name in _KINDS}
    bots: dict[str, _Bot] = {}
    routes: dict[tuple[str | None, str], _Totals] = {}
    sessions: dict[str, _Session] = {}
    daily_raw: dict[tuple[str, str], _Totals] = {}

    for (
        session_id,
        day,
        agent,
        model,
        bot_id,
        profile_id,
        kind,
        connection_id,
        input_sum,
        output_sum,
        cache_read_sum,
        cache_write_sum,
        calls,
        first_at,
        last_at,
    ) in rows:
        input_sum, output_sum = int(input_sum), int(output_sum)
        cache_read_sum, cache_write_sum, calls = (
            int(cache_read_sum),
            int(cache_write_sum),
            int(calls),
        )
        totals.add(input_sum, output_sum, cache_read_sum, cache_write_sum, calls)

        kinds["subagent" if agent else kind].add(
            input_sum, output_sum, cache_read_sum, cache_write_sum, calls
        )

        bot = bots.setdefault(bot_id, _Bot(profile_id=profile_id))
        bot.profile_id = profile_id
        bot.totals.add(input_sum, output_sum, cache_read_sum, cache_write_sum, calls)
        bot.sessions.add(session_id)

        routes.setdefault((connection_id, model), _Totals()).add(
            input_sum, output_sum, cache_read_sum, cache_write_sum, calls
        )

        session = sessions.setdefault(session_id, _Session(bot_id=bot_id, kind=kind))
        session.totals.add(input_sum, output_sum, cache_read_sum, cache_write_sum, calls)
        # "" sorts below every stamp, so a bare max() would pin first_at at "" forever.
        session.first_at = first_at if not session.first_at else min(session.first_at, first_at)
        session.last_at = max(session.last_at, last_at)

        day_bot = daily_raw.setdefault((day, bot_id), _Totals())
        day_bot.add(input_sum, output_sum, cache_read_sum, cache_write_sum, calls)

    bot_order = sorted(bots.items(), key=lambda kv: (-kv[1].totals.tokens(), kv[0]))
    top_bots = bot_order[:MAX_BOT_ROWS]
    top_bot_ids = {bot_id for bot_id, _ in top_bots}
    bots_omitted = _safe(max(0, len(bot_order) - MAX_BOT_ROWS))

    daily_folded: dict[tuple[str, str | None], _Totals] = {}
    for (day, bot_id), vals in daily_raw.items():
        key = (day, bot_id if bot_id in top_bot_ids else None)
        daily_folded.setdefault(key, _Totals()).add(
            vals.input, vals.output, vals.cache_read, vals.cache_write, vals.calls
        )
    daily_out = [
        {"date": day, "bot_id": bot_id, "tokens": _safe(vals.tokens()), "calls": _safe(vals.calls)}
        for (day, bot_id), vals in sorted(
            daily_folded.items(),
            key=lambda item: (item[0][0], item[0][1] is None, item[0][1] or ""),
        )
    ]

    bots_out = [
        {
            "bot_id": bot_id,
            "profile_id": bot.profile_id,
            "name": names.profiles.get(bot.profile_id),
            "current": bot_id in names.bots,
            **bot.totals.as_dict(),
            "sessions": _safe(len(bot.sessions)),
        }
        for bot_id, bot in top_bots
    ]

    kinds_out = [{"kind": name, **kinds[name].as_dict()} for name in _KINDS]

    route_order = sorted(
        routes.items(),
        key=lambda kv: (-kv[1].tokens(), kv[0][0] is not None, kv[0][0] or "", kv[0][1]),
    )
    routes_omitted = _safe(max(0, len(route_order) - MAX_ROUTE_ROWS))
    routes_out = [
        {
            "connection_id": connection_id,
            "connection_name": names.connections.get(connection_id)
            if connection_id is not None
            else None,
            "model": model,
            **vals.as_dict(),
        }
        for (connection_id, model), vals in route_order[:MAX_ROUTE_ROWS]
    ]

    session_order = sorted(sessions.items(), key=lambda kv: (-kv[1].totals.tokens(), kv[0]))
    largest_out = [
        {
            "session_id": session_id,
            "bot_id": session.bot_id,
            "kind": session.kind,
            **session.totals.as_dict(),
            "first_at": session.first_at,
            "last_at": session.last_at,
            "transcript": session_id in present,
        }
        for session_id, session in session_order[:MAX_LARGEST_ROWS]
    ]

    log_only = [session_id for session_id in sessions if session_id not in present]
    coverage = {
        "sessions": _safe(len(sessions)),
        "log_only_sessions": _safe(len(log_only)),
        "log_only_calls": _safe(sum(sessions[session_id].totals.calls for session_id in log_only)),
        "unlogged": unlogged,
        "pending": pending,
        "rows": _safe(total_rows),
        "oldest_at": oldest_at,
        "max_rows": max_rows,
        "max_age_days": max_age_days,
    }

    return {
        "workspace_id": workspace_id,
        "days": days,
        "dates": [(first + timedelta(days=n)).isoformat() for n in range(days)],
        "totals": totals.as_dict(),
        "daily": daily_out,
        "bots": bots_out,
        "bots_omitted": bots_omitted,
        "kinds": kinds_out,
        "routes": routes_out,
        "routes_omitted": routes_omitted,
        "largest": largest_out,
        "coverage": coverage,
    }


def trace(
    store: SessionStore,
    workspace_id: str,
    session_id: str,
    *,
    present: frozenset[str],
    names: Names,
) -> dict[str, Any] | None:
    """One session's own call log, newest `MAX_TRACE_CALLS` returned oldest first, or None when
    the workspace never logged that session."""
    with store._connection() as connection:
        session = _fetch(
            connection,
            "SELECT bot_id, kind, connection_id FROM bot_usage_sessions "
            "WHERE session_id=? AND workspace_id=?",
            (session_id, workspace_id),
        )
        if not session:
            return None
        total = 0
        newest: list[Any] = []  # a min-heap of the MAX_TRACE_CALLS newest calls by (at, rowid)
        after = ""
        while chunk := _fetch(connection, _TRACE, (session_id, after, CHUNK_ROWS)):
            total += len(chunk)
            for row in chunk:
                if len(newest) < MAX_TRACE_CALLS:
                    heapq.heappush(newest, row)
                else:
                    heapq.heappushpop(newest, row)
            after = chunk[-1][2]

    [(bot_id, kind, connection_id)] = session
    return {
        "session_id": session_id,
        "bot_id": bot_id,
        "kind": kind,
        "connection_id": connection_id,
        "connection_name": names.connections.get(connection_id)
        if connection_id is not None
        else None,
        "transcript": session_id in present,
        "omitted": max(0, total - MAX_TRACE_CALLS),
        # One call's own counts, not sums: `usage_reader` skips a line with a count over 2^53-1
        # and the table's CHECK refuses one, so none is clamped here.
        "calls": [
            {
                "at": at,
                "prompt_id": prompt_id,
                "model": model,
                "agent": bool(agent),
                "input": input_tokens,
                "output": output_tokens,
                "cache_read": cache_read_tokens,
                "cache_write": cache_write_tokens,
            }
            for (
                at,
                _rowid,
                _message_id,
                prompt_id,
                model,
                agent,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
            ) in sorted(newest)
        ],
    }
