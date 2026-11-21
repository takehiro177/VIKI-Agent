"""summary() and trace() over directly-seeded bot_usage rows; P6 (messaging/bot_usage.py) does
not exist yet and is never imported here -- the caller (P14) owns BotUsageError and retention."""

from __future__ import annotations

import contextlib
import json
import random
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from viki_agent import api_json
from viki_agent.automation.routines import stamp
from viki_agent.messaging import bot_usage_report
from viki_agent.messaging.bot_usage_report import MAX_TRACE_CALLS, Names, summary, trace
from viki_agent.state import AppState, Prefs
from viki_agent.storage import SessionStore, StateError

_WORKSPACE = "00000000-0000-4000-8000-000000000900"
_OTHER_WORKSPACE = "00000000-0000-4000-8000-000000000901"
_NAMES = Names(profiles={}, bots=frozenset(), connections={})


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012x}"


def _local(local_date: Any, local_time: time = time(12)) -> str:
    """A UTC stamp for *local_time* on *local_date*, in this machine's own local zone -- the
    same conversion `summary`'s window uses, so these tests pass under any system timezone."""
    return stamp(datetime.combine(local_date, local_time).astimezone(UTC))


def _session_row(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": _id(1),
        "workspace_id": _WORKSPACE,
        "bot_id": _id(101),
        "profile_id": _id(201),
        "kind": "main",
        "connection_id": None,
        "read_ns": None,
        **overrides,
    }


def _call_row(**overrides: Any) -> dict[str, Any]:
    return {
        "session_id": _id(1),
        "message_id": "m0",
        "prompt_id": None,
        "at": _local(datetime(2026, 11, 20, tzinfo=UTC).date()),
        "model": "claude-sonnet-5",
        "agent": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        **overrides,
    }


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    connection.execute(
        f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
        tuple(row.values()),
    )


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return AppState(tmp_path / "state.json", Prefs()).store


def _seed(store: SessionStore, sessions: list[dict[str, Any]], calls: list[dict[str, Any]]) -> None:
    with store._connection(write=True) as connection:
        for row in sessions:
            _insert(connection, "bot_usage_sessions", row)
        for row in calls:
            _insert(connection, "bot_usage_calls", row)


def test_totals_match_directly_inserted_rows_and_coverage_rows_are_all_time(
    store: SessionStore,
) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    old = today - timedelta(days=100)
    _seed(
        store,
        [_session_row()],
        [
            _call_row(message_id="m1", at=_local(today, time(10)), input_tokens=5, output_tokens=7),
            _call_row(
                message_id="m2",
                at=_local(today, time(14)),
                cache_read_tokens=3,
                cache_write_tokens=11,
            ),
            # Outside the 1-day window: must not affect totals/daily, but does count in
            # coverage.rows and sets coverage.oldest_at, since those are all-time for the
            # workspace, not period-scoped.
            _call_row(message_id="m3", at=_local(old, time(0)), input_tokens=99),
        ],
    )
    result = summary(
        store,
        _WORKSPACE,
        1,
        now=now,
        present=frozenset({_id(1)}),
        pending=7,
        unlogged=2,
        names=_NAMES,
        max_rows=100_000,
        max_age_days=90,
    )
    assert result["dates"] == [today.isoformat()]
    assert result["totals"] == {
        "input": 5,
        "output": 7,
        "cache_read": 3,
        "cache_write": 11,
        "calls": 2,
    }
    coverage = result["coverage"]
    assert coverage["sessions"] == 1
    assert coverage["log_only_sessions"] == 0
    assert coverage["log_only_calls"] == 0
    assert coverage["pending"] == 7
    assert coverage["unlogged"] == 2
    assert coverage["max_rows"] == 100_000
    assert coverage["max_age_days"] == 90
    assert coverage["rows"] == 3
    assert coverage["oldest_at"] == _local(old, time(0))


def test_midnight_boundary_rows_land_on_adjacent_local_dates(store: SessionStore) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    yesterday = today - timedelta(days=1)
    _seed(
        store,
        [_session_row()],
        [
            _call_row(message_id="late", at=_local(yesterday, time(23, 59, 59, 999000))),
            _call_row(message_id="early", at=_local(today, time(0, 0, 0, 0))),
        ],
    )
    result = summary(
        store,
        _WORKSPACE,
        2,
        now=now,
        present=frozenset({_id(1)}),
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=1,
        max_age_days=1,
    )
    by_date = {row["date"]: row for row in result["daily"]}
    assert by_date[yesterday.isoformat()]["calls"] == 1
    assert by_date[today.isoformat()]["calls"] == 1
    assert result["totals"]["calls"] == 2


def test_kinds_sum_to_totals(store: SessionStore) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    at = _local(today)
    sessions = [
        _session_row(session_id=_id(n), bot_id=_id(100 + n), kind=kind)
        for n, kind in enumerate(("main", "task", "bot_thread", "routine"), start=1)
    ]
    calls = [
        _call_row(session_id=_id(n), message_id=f"agent0-{n}", at=at, agent=0, input_tokens=n)
        for n in range(1, 5)
    ] + [
        # A subagent call on the "main" session buckets under "subagent", not "main".
        _call_row(session_id=_id(1), message_id="agent1", at=at, agent=1, output_tokens=9)
    ]
    _seed(store, sessions, calls)
    result = summary(
        store,
        _WORKSPACE,
        1,
        now=now,
        present=frozenset(_id(n) for n in range(1, 5)),
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=1,
        max_age_days=1,
    )
    by_kind = {row["kind"]: row for row in result["kinds"]}
    assert set(by_kind) == {"main", "task", "bot_thread", "routine", "subagent"}
    summed = {
        field: sum(row[field] for row in by_kind.values())
        for field in ("input", "output", "cache_read", "cache_write", "calls")
    }
    assert summed == result["totals"]
    assert by_kind["subagent"] == {
        "kind": "subagent",
        "input": 0,
        "output": 9,
        "cache_read": 0,
        "cache_write": 0,
        "calls": 1,
    }
    assert by_kind["main"]["calls"] == 1  # only the agent=0 row; the agent=1 one moved out


def test_top_32_bots_fold_into_null_daily_row_and_bots_omitted(store: SessionStore) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    at = _local(today)
    sessions = [_session_row(session_id=_id(n), bot_id=_id(1000 + n)) for n in range(1, 36)]
    # Bot n gets n input tokens, so bots 4..35 (32 bots) are the top 32 and bots 1..3 are
    # omitted, folded into one bot_id=None daily row.
    calls = [
        _call_row(session_id=_id(n), message_id=f"c{n}", at=at, input_tokens=n)
        for n in range(1, 36)
    ]
    _seed(store, sessions, calls)
    result = summary(
        store,
        _WORKSPACE,
        1,
        now=now,
        present=frozenset(_id(n) for n in range(1, 36)),
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=1,
        max_age_days=1,
    )
    assert len(result["bots"]) == 32
    assert result["bots_omitted"] == 3
    assert {bot["bot_id"] for bot in result["bots"]} == {_id(1000 + n) for n in range(4, 36)}
    omitted_row = next(row for row in result["daily"] if row["bot_id"] is None)
    assert omitted_row == {
        "date": today.isoformat(),
        "bot_id": None,
        "tokens": 1 + 2 + 3,
        "calls": 3,
    }
    # A top bot keeps its own bot_id in daily, it is not folded away.
    assert any(row["bot_id"] == _id(1035) for row in result["daily"])


def test_top_20_routes_fold_and_routes_omitted(store: SessionStore) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    at = _local(today)
    calls = [
        _call_row(message_id=f"r{n}", at=at, model=f"model-{n}", input_tokens=n)
        for n in range(1, 24)
    ]
    _seed(store, [_session_row()], calls)
    result = summary(
        store,
        _WORKSPACE,
        1,
        now=now,
        present=frozenset({_id(1)}),
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=1,
        max_age_days=1,
    )
    assert len(result["routes"]) == 20
    assert result["routes_omitted"] == 3
    assert {route["model"] for route in result["routes"]} == {f"model-{n}" for n in range(4, 24)}
    assert result["routes"][0]["model"] == "model-23"  # tokens desc


def test_summary_booleans_are_bool_not_int(store: SessionStore) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    at = _local(today)
    _seed(
        store,
        [_session_row()],
        [_call_row(at=at, input_tokens=5)],
    )
    names = Names(profiles={}, bots=frozenset({_id(101)}), connections={})
    result = summary(
        store,
        _WORKSPACE,
        1,
        now=now,
        present=frozenset({_id(1)}),
        pending=0,
        unlogged=0,
        names=names,
        max_rows=1,
        max_age_days=1,
    )
    assert result["bots"][0]["current"] is True
    assert result["largest"][0]["transcript"] is True
    assert type(result["bots_omitted"]) is int
    assert type(result["routes_omitted"]) is int


def test_trace_order_oldest_first_and_omitted(store: SessionStore) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    stamps = [stamp(base + timedelta(seconds=n)) for n in range(2005)]
    calls = [
        _call_row(message_id=f"c{n}", at=stamps[n], input_tokens=n) for n in range(len(stamps))
    ]
    _seed(store, [_session_row()], calls)
    result = trace(store, _WORKSPACE, _id(1), present=frozenset(), names=_NAMES)
    assert result is not None
    assert result["omitted"] == 5
    assert len(result["calls"]) == 2000
    assert [call["at"] for call in result["calls"]] == stamps[5:]
    assert result["calls"][0]["input"] == 5
    assert result["calls"][-1]["input"] == 2004


def test_trace_booleans_and_session_kind_never_subagent(store: SessionStore) -> None:
    at = _local(datetime(2026, 11, 20, tzinfo=UTC).date())
    _seed(
        store,
        [_session_row(kind="task", connection_id=_id(301))],
        [
            _call_row(message_id="m0", at=at, agent=0),
            _call_row(message_id="m1", at=at, agent=1),
        ],
    )
    names = Names(profiles={}, bots=frozenset(), connections={_id(301): "My connection"})
    result = trace(store, _WORKSPACE, _id(1), present=frozenset(), names=names)
    assert result is not None
    assert result["kind"] == "task"
    assert result["connection_name"] == "My connection"
    assert result["transcript"] is False
    agents = {call["agent"] for call in result["calls"]}
    assert agents == {True, False}
    for call in result["calls"]:
        assert type(call["agent"]) is bool

    present_result = trace(store, _WORKSPACE, _id(1), present=frozenset({_id(1)}), names=names)
    assert present_result is not None
    assert present_result["transcript"] is True


def test_trace_returns_none_when_not_logged_for_the_workspace(store: SessionStore) -> None:
    assert trace(store, _WORKSPACE, _id(1), present=frozenset(), names=_NAMES) is None
    _seed(store, [_session_row(workspace_id=_OTHER_WORKSPACE)], [_call_row()])
    assert trace(store, _WORKSPACE, _id(1), present=frozenset(), names=_NAMES) is None
    assert trace(store, _OTHER_WORKSPACE, _id(1), present=frozenset(), names=_NAMES) is not None


def test_coverage_log_only_sessions_and_calls(store: SessionStore) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    at = _local(today)
    _seed(
        store,
        [
            _session_row(session_id=_id(1), bot_id=_id(101)),
            _session_row(session_id=_id(2), bot_id=_id(102)),
        ],
        [
            _call_row(session_id=_id(1), message_id="a", at=at),
            _call_row(session_id=_id(2), message_id="b", at=at),
        ],
    )
    result = summary(
        store,
        _WORKSPACE,
        1,
        now=now,
        present=frozenset({_id(1)}),  # session 2 is log-only
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=1,
        max_age_days=1,
    )
    coverage = result["coverage"]
    assert coverage["sessions"] == 2
    assert coverage["log_only_sessions"] == 1
    assert coverage["log_only_calls"] == 1


def test_worst_case_summary_encodes_under_1_mib(store: SessionStore) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    sessions = [_session_row(session_id=_id(n), bot_id=_id(1000 + n)) for n in range(1, 41)]
    calls = [
        _call_row(
            session_id=_id(n),
            message_id=f"w{n}",
            at=_local(today - timedelta(days=n % 90)),
            model=f"model-{n % 25}",
            input_tokens=n,
        )
        for n in range(1, 41)
    ]
    _seed(store, sessions, calls)
    result = summary(
        store,
        _WORKSPACE,
        90,
        now=now,
        present=frozenset(_id(n) for n in range(1, 41)),
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=100_000,
        max_age_days=90,
    )
    assert len(result["bots"]) <= 32
    assert len(result["routes"]) <= 20
    assert len(result["largest"]) <= 10
    assert len(result["dates"]) == 90
    body = api_json.json_response(result).body
    assert len(body) < 1_048_576


def _reference_scan(
    connection: sqlite3.Connection, workspace_id: str, start: str, end: str
) -> tuple[list[Any], int, str | None]:
    """The single statement `summary` grouped the period's calls with before it read in chunks,
    its SQL verbatim, then the whole log's call count and oldest `at`, as coverage reports them."""
    rows = connection.execute(
        "SELECT c.session_id, date(c.at,'localtime'), c.agent, c.model, "
        "s.bot_id, s.profile_id, s.kind, s.connection_id, "
        "sum(c.input_tokens), sum(c.output_tokens), "
        "sum(c.cache_read_tokens), sum(c.cache_write_tokens), count(*), "
        "min(c.at), max(c.at) "
        "FROM bot_usage_calls c JOIN bot_usage_sessions s ON s.session_id=c.session_id "
        "WHERE s.workspace_id=? AND c.at>=? AND c.at<? "
        "GROUP BY c.session_id, date(c.at,'localtime'), c.agent, c.model",
        (workspace_id, start, end),
    ).fetchall()
    total_rows, oldest_at = connection.execute(
        "SELECT count(*), min(at) FROM bot_usage_calls"
    ).fetchone()
    return rows, total_rows, oldest_at


def _random_log(store: SessionStore, seed: int, today: Any) -> None:
    """Sessions of three workspaces and calls over 100 local dates through tomorrow, inserted
    in random order and then thinned, so rowid order follows neither time nor session. Calls
    land on local midnight and a millisecond before it too; two sessions were never logged."""
    rng = random.Random(seed)
    workspaces = (_WORKSPACE, _OTHER_WORKSPACE, _id(902))
    sessions = []
    for n in range(1, 25):
        bot = rng.randrange(6)
        sessions.append(
            _session_row(
                session_id=_id(n),
                workspace_id=rng.choice(workspaces),
                bot_id=_id(100 + bot),
                profile_id=_id(200 + bot),  # a Bot's profile never changes
                kind=rng.choice(("main", "task", "bot_thread", "routine")),
                connection_id=rng.choice((None, _id(301), _id(302))),
            )
        )
    moments = (time(0), time(23, 59, 59, 999000), time(9, 30), time(17, 5))
    calls = [
        _call_row(
            session_id=_id(rng.randrange(1, 27)),
            message_id=f"m{n}",
            at=_local(today - timedelta(days=rng.randrange(-1, 99)), rng.choice(moments)),
            model=rng.choice(("claude-sonnet-5", "claude-haiku-4.5", "")),
            agent=rng.randrange(2),
            input_tokens=rng.randrange(1000),
            output_tokens=rng.randrange(1000),
            cache_read_tokens=rng.randrange(1000),
            cache_write_tokens=rng.randrange(1000),
        )
        for n in range(400)
    ]
    rng.shuffle(calls)
    _seed(store, sessions, calls)
    with store._connection(write=True) as connection:
        connection.execute("DELETE FROM bot_usage_calls WHERE rowid % 7 = 3")


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_chunked_summary_equals_the_single_statement_scan(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch, seed: int
) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    _random_log(store, seed, now.astimezone().date())
    names = Names(
        profiles={_id(200): "First"}, bots=frozenset({_id(100)}), connections={_id(301): "Route"}
    )

    def answers() -> dict[int, dict[str, Any]]:
        return {
            days: summary(
                store,
                _WORKSPACE,
                days,
                now=now,
                present=frozenset({_id(1), _id(2)}),
                pending=1,
                unlogged=2,
                names=names,
                max_rows=100_000,
                max_age_days=90,
            )
            for days in (1, 7, 90)
        }

    with monkeypatch.context() as reference:
        reference.setattr(bot_usage_report, "_scan", _reference_scan)
        expected = answers()
    assert 0 < expected[7]["totals"]["calls"] < expected[7]["coverage"]["rows"]
    for chunk in (1, 2, 7, bot_usage_report.CHUNK_ROWS):
        monkeypatch.setattr(bot_usage_report, "CHUNK_ROWS", chunk)
        assert answers() == expected, chunk


class _Watched:
    """A read connection that records the statements it runs, and runs those numbered *first*
    to *last* while another connection holds the store's EXCLUSIVE lock, as a writer's commit
    does."""

    def __init__(self, connection: sqlite3.Connection, path: Path, first: int, last: int) -> None:
        self.connection = connection
        self.blocker = sqlite3.connect(path, isolation_level=None)
        self.first, self.last = first, last
        self.statements: list[tuple[str, Any]] = []

    def execute(self, sql: str, values: Any = ()) -> sqlite3.Cursor:
        self.statements.append((sql, values))
        if not self.first <= len(self.statements) <= self.last:
            return self.connection.execute(sql, values)
        self.blocker.execute("BEGIN EXCLUSIVE")
        try:
            return self.connection.execute(sql, values)
        finally:
            self.blocker.execute("ROLLBACK")


def _watch(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch, first: int = 0, last: int = 0
) -> list[_Watched]:
    """*store*'s read connections from now on, each a `_Watched` one, listed as they open."""
    original = store._connection
    made: list[_Watched] = []

    @contextlib.contextmanager
    def connection(**kwargs: Any) -> Iterator[Any]:
        with original(**kwargs) as inner:
            made.append(_Watched(inner, store.path, first, last))
            try:
                yield made[-1]
            finally:
                made[-1].blocker.close()

    monkeypatch.setattr(store, "_connection", connection)
    return made


def test_a_summary_reads_at_most_chunk_rows_calls_a_statement(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound that lets a writer's commit in between two reads, pinned without a clock: each
    statement visits only the calls of its rowid window, CHUNK_ROWS at most, so a log of several
    thousand calls takes one window and one grouping statement a chunk, and one more window."""
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    _seed(
        store,
        [_session_row(session_id=_id(n), bot_id=_id(100 + n % 5)) for n in range(1, 41)],
        [
            _call_row(
                session_id=_id(1 + n % 40),
                message_id=f"m{n}",
                at=_local(today - timedelta(days=n % 9)),
                input_tokens=n,
            )
            for n in range(3600)
        ],
    )
    with store._connection(write=True) as connection:
        connection.execute("DELETE FROM bot_usage_calls WHERE rowid % 9 = 4")  # gaps in rowid
        (calls,) = connection.execute("SELECT count(*) FROM bot_usage_calls").fetchone()
    monkeypatch.setattr(bot_usage_report, "CHUNK_ROWS", 500)
    made = _watch(store, monkeypatch)
    summary(
        store,
        _WORKSPACE,
        7,
        now=now,
        present=frozenset(),
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=100_000,
        max_age_days=90,
    )
    [watched] = made
    assert len(watched.statements) >= 2 * -(-calls // 500) + 1
    with store._connection() as connection:
        for sql, values in watched.statements:
            if sql == bot_usage_report._RANGE:  # the next LIMIT calls after a rowid
                window = "SELECT min(count(*), ?2) FROM bot_usage_calls WHERE rowid>?1"
                (visited,) = connection.execute(window, values).fetchone()
            elif sql == bot_usage_report._GROUPS:  # the calls in a rowid range
                window = "SELECT count(*) FROM bot_usage_calls WHERE rowid>? AND rowid<=?"
                (visited,) = connection.execute(window, values[-2:]).fetchone()
            else:
                pytest.fail(f"a statement with no rowid window: {sql}")
            assert visited <= 500, sql


@pytest.mark.parametrize("statement", [1, 4])  # the first chunk's range, a later chunk's groups
def test_a_chunk_that_meets_a_commit_runs_again(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch, statement: int
) -> None:
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    at = _local(now.astimezone().date())
    _seed(
        store,
        [_session_row()],
        [_call_row(message_id=f"m{n}", at=at, input_tokens=n) for n in range(5)],
    )

    def answer() -> dict[str, Any]:
        return summary(
            store,
            _WORKSPACE,
            1,
            now=now,
            present=frozenset(),
            pending=0,
            unlogged=0,
            names=_NAMES,
            max_rows=1,
            max_age_days=1,
        )

    monkeypatch.setattr(bot_usage_report, "CHUNK_ROWS", 2)
    expected = answer()
    made = _watch(store, monkeypatch, statement, statement)
    assert answer() == expected
    [watched] = made
    assert len(watched.statements) == 8  # 4 range and 3 groups statements, and the rerun


def test_a_store_that_stays_busy_fails_as_a_busy_read_did(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store's busy StateError, which the usage routes answer as 503 unavailable."""
    _seed(store, [_session_row()], [_call_row()])
    made = _watch(store, monkeypatch, 1, 1_000)
    with pytest.raises(StateError) as failure:
        summary(
            store,
            _WORKSPACE,
            1,
            now=datetime(2026, 11, 21, 9, 0, tzinfo=UTC),
            present=frozenset(),
            pending=0,
            unlogged=0,
            names=_NAMES,
            max_rows=1,
            max_age_days=1,
        )
    assert failure.value.code == "busy"
    [watched] = made
    assert len(watched.statements) == bot_usage_report.BUSY_RETRIES + 1


@pytest.mark.parametrize("chunk", [7, bot_usage_report.CHUNK_ROWS])
def test_trace_of_a_long_session_keeps_its_newest_calls_oldest_first(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch, chunk: int
) -> None:
    """2,345 calls in random order, many sharing an `at`, against the single statement's
    ORDER BY at DESC, rowid DESC; another session's call stays out."""
    rng = random.Random(7)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    calls = [
        _call_row(
            message_id=f"c{rng.randrange(10**9)}-{n}",
            at=stamp(base + timedelta(seconds=rng.randrange(600))),
            input_tokens=n,
        )
        for n in range(2345)
    ]
    other = _call_row(session_id=_id(2), message_id="m0", at=stamp(base + timedelta(hours=1)))
    _seed(store, [_session_row(), _session_row(session_id=_id(2))], [*calls, other])
    with store._connection() as connection:
        newest = connection.execute(
            "SELECT at, input_tokens FROM bot_usage_calls "
            "WHERE session_id=? ORDER BY at DESC, rowid DESC LIMIT ?",
            (_id(1), MAX_TRACE_CALLS),
        ).fetchall()
    monkeypatch.setattr(bot_usage_report, "CHUNK_ROWS", chunk)
    result = trace(store, _WORKSPACE, _id(1), present=frozenset(), names=_NAMES)
    assert result is not None
    assert result["omitted"] == 2345 - MAX_TRACE_CALLS
    assert [(call["at"], call["input"]) for call in result["calls"]] == newest[::-1]


_MAX_SAFE = (1 << 53) - 1  # the browser's Number.MAX_SAFE_INTEGER


def _numbers(value: Any) -> Iterator[Any]:
    """Every number in a decoded answer, at any depth; booleans are not numbers there."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _numbers(item)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield value


def test_sums_past_the_largest_safe_integer_saturate_there(store: SessionStore) -> None:
    """One call at the reader's per-count cap and two ordinary ones: every number on the wire
    passes the client's count rule (Number.isSafeInteger and not negative), and the sums that
    would pass 2^53-1 stop at it."""
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    at = _local(today)
    _seed(
        store,
        [_session_row()],
        [
            _call_row(message_id="m0", at=at, input_tokens=_MAX_SAFE),
            _call_row(message_id="m1", at=at, input_tokens=5, output_tokens=7),
            _call_row(message_id="m2", at=at, input_tokens=11, cache_read_tokens=13),
        ],
    )
    result = summary(
        store,
        _WORKSPACE,
        7,
        now=now,
        present=frozenset(),
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=100_000,
        max_age_days=90,
    )
    wire = json.loads(bytes(api_json.json_response(result).body))
    assert [n for n in _numbers(wire) if type(n) is not int or not 0 <= n <= _MAX_SAFE] == []
    assert wire["totals"] == {
        "input": _MAX_SAFE,
        "output": 7,
        "cache_read": 13,
        "cache_write": 0,
        "calls": 3,
    }
    assert wire["daily"] == [
        {"date": today.isoformat(), "bot_id": _id(101), "tokens": _MAX_SAFE, "calls": 3}
    ]
    for row in (wire["bots"][0], wire["kinds"][0], wire["routes"][0], wire["largest"][0]):
        assert (row["input"], row["output"], row["calls"]) == (_MAX_SAFE, 7, 3)


def test_coverage_rows_and_oldest_at_count_every_vault(store: SessionStore) -> None:
    """The log's retention limits are log-wide, so the rows and oldest call beside them count
    every vault's calls; the rest of the answer stays this workspace's."""
    now = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    today = now.astimezone().date()
    old = _local(today - timedelta(days=30))
    _seed(
        store,
        [
            _session_row(),
            _session_row(session_id=_id(2), workspace_id=_OTHER_WORKSPACE, bot_id=_id(102)),
        ],
        [
            _call_row(message_id="a", at=_local(today), input_tokens=5),
            _call_row(session_id=_id(2), message_id="b", at=old, input_tokens=7),
            _call_row(session_id=_id(2), message_id="c", at=_local(today), input_tokens=11),
        ],
    )
    result = summary(
        store,
        _WORKSPACE,
        7,
        now=now,
        present=frozenset(),
        pending=0,
        unlogged=0,
        names=_NAMES,
        max_rows=100_000,
        max_age_days=90,
    )
    assert (result["coverage"]["rows"], result["coverage"]["oldest_at"]) == (3, old)
    assert result["coverage"]["sessions"] == 1
    assert (result["totals"]["calls"], result["totals"]["input"]) == (1, 5)
    assert [bot["bot_id"] for bot in result["bots"]] == [_id(101)]
