"""Schema step 24: the decision-loop tables, and the move of every step-19 episode into them."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import (
    _SCOPE,
    _STAMP,
    OTHER,
    SID,
    _downgrade_decision_loop,
    _downgrade_jev_tables,
    _workspace,
)
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

_CHECK = "00000000-0000-4000-8000-000000000031"
_RUN = "00000000-0000-4000-8000-000000000041"
_EPISODE = "00000000-0000-4000-8000-000000000051"
_LATER = "2026-10-02T12:00:00.000Z"
# The tables step 24 adds beside the two it fills; each starts empty.
_ADDED = ("decision_items", "decision_calls", "decision_missions", "decision_key")
# Every decision_* table of a current store: step 24's and step 25's proposal table.
_CURRENT_DECISION_TABLES = {**storage.DECISION_LOOP_SCHEMAS, **storage.DECISION_PROPOSAL_SCHEMAS}


def _tables(connection: sqlite3.Connection, pattern: str) -> dict[str, str]:
    """Each table whose name matches the GLOB `pattern`, with its DDL."""
    return dict(
        connection.execute(
            "SELECT name, sql FROM sqlite_schema WHERE type='table' AND name GLOB ?", (pattern,)
        ).fetchall()
    )


def _indexes(connection: sqlite3.Connection, table: str) -> list[tuple[str, list[str]]]:
    """Each index of `table` in name order: its origin ('pk' or 'u') and its columns."""
    found = sorted(connection.execute(f"PRAGMA index_list({table})"), key=lambda row: row[1])
    return [
        (row[3], [info[2] for info in connection.execute(f"PRAGMA index_info({row[1]})")])
        for row in found
    ]


_INDEXES = {
    "decision_episodes": [
        ("pk", ["episode_id"]),
        ("u", ["scope", "dedupe_key"]),  # one episode per scope and dedupe key
        ("u", ["scope", "created_at", "episode_id"]),  # a scope read newest first, unsorted
    ],
    "decision_preferences": [("pk", ["scope", "name"])],
    "decision_items": [("pk", ["scope", "item_key"])],
    "decision_calls": [("pk", ["call_id"]), ("u", ["scope", "created_at", "call_id"])],
    "decision_missions": [("pk", ["mission_id"]), ("u", ["session_id"])],
    "decision_key": [("pk", ["slot"])],
}


def _rows(connection: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _other_tables(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    """Every row of every table step 24 leaves alone."""
    names = [
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*' "
            "AND name NOT GLOB 'jev_*' AND name NOT GLOB 'decision_*'"
        )
    ]
    rows = {
        name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall() for name in names
    }
    rows.setdefault("session_bot_memory", [])
    rows.setdefault("bot_threads", [])
    rows.setdefault("bot_thread_sessions", [])
    for name in storage.BOT_CODE_SCHEMAS:
        rows.setdefault(name, [])
    for name in storage.INBOX_SUGGESTION_SCHEMAS:
        rows.setdefault(name, [])
    for name in storage.BOT_LEARNING_SCHEMAS:
        rows.setdefault(name, [])
    for name in storage.BOT_USAGE_SCHEMAS:
        rows.setdefault(name, [])
    return rows


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    connection.execute(
        f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
        tuple(row.values()),
    )


def _at(day: int, minute: int) -> str:
    """A 24-character stamp: 08:mm on 2026-10-dd."""
    return f"2026-10-{day:02d}T08:{minute:02d}:00.000Z"


def _old(n: int, **overrides: Any) -> dict[str, Any]:
    """Step-19 episode `n`: a notified decision with no feedback unless `overrides` say
    otherwise. Each value step 24 copies differs from row to row and from column to column."""
    row = {
        "episode_id": f"00000000-0000-4000-8000-{n:012d}",
        "scope": SID if n % 2 else OTHER,
        "kind": "decision",
        "created_at": _at(n, 0),
        "day": _at(n, 0)[:10],
        "trigger": "app_open",
        "candidate": "approvals",
        "evidence": f'{{"count":{n}}}',
        "evidence_key": f"{n}" * 64,
        "eligible": '["silent","notify"]',
        "work": "none",
        "attention": "notify",
        "defer_until": None,
        "reason": f"rule_{n}",
        "policy_version": f"b1.{n}",
        "mode": "B1",
        "block": "",
        "dedupe_key": "d" * 63 + f"{n}",
        "brief_exposures": 0,
        "rail_exposures": 0,
        "first_exposed_at": None,
        "last_exposed_at": None,
        "opened_at": None,
        "reviewed_at": None,
        "observed_until": _at(n, 5),
        "channel_status": "available",
        "feedback": None,
        "feedback_source": None,
        "feedback_revision": 0,
        "feedback_at": None,
    }
    return {**row, **overrides}


def _shown(n: int, brief: int, rail: int) -> dict[str, Any]:
    return {
        "brief_exposures": brief,
        "rail_exposures": rail,
        "first_exposed_at": _at(n, 1),
        "last_exposed_at": _at(n, 2),
    }


def _feedback(n: int, value: str, source: str, revision: int) -> dict[str, Any]:
    return {
        "feedback": value,
        "feedback_source": source,
        "feedback_revision": revision,
        "feedback_at": _at(n, 6),
    }


_REPORT = {"kind": "report", "eligible": None, "work": None, "attention": None}
# Each step-19 episode beside what step 24 must make of it: the columns it derives, and the
# defer time, exposures and feedback the episode recorded. `_moved` adds the columns it copies
# unchanged or leaves NULL.
_MOVES: list[tuple[dict[str, Any], dict[str, Any]]] = [
    (
        _old(1, attention="silent", reviewed_at=_at(1, 4), **_feedback(1, "missed", "review", 1)),
        {
            "entry": "decision",
            "level": "silent",
            "timing": "now",
            "defer_until": None,
            "work": "none",
            "exposures": 0,
            "feedback": "missed",
            "feedback_source": "review",
        },
    ),
    (
        _old(
            2,
            trigger="new_chat",
            candidate="tasks_due",
            work="draft",
            attention="defer",
            defer_until=_LATER,
            reviewed_at=_at(2, 4),
            **_shown(2, 2, 3),
            **_feedback(2, "right_to_hold", "review", 2),
        ),
        {
            "entry": "decision",
            "level": "silent",
            "timing": "at",
            "defer_until": _LATER,
            "work": "prepare",
            "exposures": 5,
            "feedback": "right_to_hold",
            "feedback_source": "review",
        },
    ),
    (
        _old(
            3,
            trigger="review",
            candidate="agent_runs",
            work="dispatch",
            opened_at=_at(3, 3),
            **_shown(3, 4, 3),
            **_feedback(3, "useful", "brief", 3),
        ),
        {
            "entry": "decision",
            "level": "list",
            "timing": "now",
            "defer_until": None,
            "work": "run",
            "exposures": 7,
            "feedback": "useful",
            "feedback_source": "brief",
        },
    ),
    (
        _old(
            4,
            candidate="inbox",
            attention="ask",
            **_shown(4, 0, 1),
            **_feedback(4, "wrong", "rail", 1),
        ),
        {
            "entry": "decision",
            "level": "list",
            "timing": "now",
            "defer_until": None,
            "work": "none",
            "exposures": 1,
            "feedback": "wrong",
            "feedback_source": "rail",
        },
    ),
    (
        _old(
            5,
            candidate="routine",
            **_REPORT,
            **_shown(5, 2, 0),
            **_feedback(5, "later", "brief", 2),
        ),
        {
            "entry": "report",
            "level": None,
            "timing": None,
            "defer_until": None,
            "work": None,
            "exposures": 2,
            "feedback": "later",
            "feedback_source": "brief",
        },
    ),
    (
        _old(
            6,
            trigger="review",
            candidate="unwatched",
            **_REPORT,
            **_shown(6, 0, 6),
            **_feedback(6, "mute", "rail", 4),
        ),
        {
            "entry": "report",
            "level": None,
            "timing": None,
            "defer_until": None,
            "work": None,
            "exposures": 6,
            "feedback": "mute",
            "feedback_source": "rail",
        },
    ),
    (
        # The two counts sum to the new column's upper bound.
        _old(
            7,
            candidate="uncommitted",
            mode="B0",
            block="quiet",
            channel_status="unavailable",
            **_shown(7, 600_000, 400_000),
        ),
        {
            "entry": "decision",
            "level": "list",
            "timing": "now",
            "defer_until": None,
            "work": "none",
            "exposures": 1_000_000,
            "feedback": None,
            "feedback_source": None,
        },
    ),
]
# Copied unchanged, beside the columns `_MOVES` spells out.
_COPIED = (
    "episode_id",
    "scope",
    "created_at",
    "day",
    "trigger",
    "candidate",
    "evidence",
    "evidence_key",
    "policy_version",
    "reason",
    "first_exposed_at",
    "last_exposed_at",
    "opened_at",
    "reviewed_at",
    "observed_until",
    "feedback_revision",
    "feedback_at",
    "dedupe_key",
)
# Nothing in a step-19 episode says what these hold.
_UNSET = (
    "item_key",
    "state_hash",
    "model",
    "answers",
    "terms",
    "kind",
    "language",
    "latency_ms",
    "input_tokens",
    "cost_micro_usd",
    "mission_id",
    "acted_at",
    "feedback_reason",
)


def _moved(old: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    """The decision_episodes row step 24 makes of one step-19 episode: the rules decided it."""
    return {
        **{column: old[column] for column in _COPIED},
        **dict.fromkeys(_UNSET),
        "policy": "rules",
        "rules_level": derived["level"],
        **derived,
    }


_OLD_PREFERENCES = [
    (SID, "mute:inbox", "1", 2, _at(1, 10)),
    (SID, "review_seen", _at(1, 11), 3, _at(1, 12)),
    (SID, "study", "b1", 1, _at(1, 13)),
    (OTHER, "mute:routine", "1", 1, _at(2, 10)),
    (OTHER, "study", "b0", 4, _at(2, 11)),
]


def _v23_store(tmp_path: Path) -> Path:
    """A v23 store as the last such build left it, its step-19 tables holding every episode in
    `_MOVES` and every preference in `_OLD_PREFERENCES`."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v23-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        _downgrade_decision_loop(connection)
        for old, _ in _MOVES:
            _insert(connection, "jev_episodes", old)
        connection.executemany("INSERT INTO jev_preferences VALUES (?,?,?,?,?)", _OLD_PREFERENCES)
        connection.execute("PRAGMA user_version=23")
        connection.commit()
    return state.database_path


def _step_19_rows(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    return {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
        for table in storage.JEV_SCHEMAS
    }


def test_fresh_store_has_the_decision_tables(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 43
    assert storage.DECISION_LOOP_SCHEMA_STEP == 24
    assert storage.JEV_SCHEMA_STEP == 19  # kept to validate and migrate stores 19 to 23
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _tables(connection, "decision_*") == _CURRENT_DECISION_TABLES
        assert _tables(connection, "jev_*") == {}
        indexes = {table: _indexes(connection, table) for table in storage.DECISION_LOOP_SCHEMAS}
        assert indexes == _INDEXES
        for table in storage.DECISION_LOOP_SCHEMAS:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_a_v23_store_moves_every_episode(tmp_path: Path) -> None:
    olds = [old for old, _ in _MOVES]
    # Both kinds, and every attention, work, trigger, feedback value and feedback source.
    assert {old["kind"] for old in olds} == {"decision", "report"}
    assert {old["attention"] for old in olds} == {None, "silent", "defer", "notify", "ask"}
    assert {old["work"] for old in olds} == {None, "none", "draft", "dispatch"}
    assert {old["trigger"] for old in olds} == {"app_open", "new_chat", "review"}
    assert {old["feedback"] for old in olds} == {
        None,
        "useful",
        "wrong",
        "later",
        "mute",
        "missed",
        "right_to_hold",
    }
    assert {old["feedback_source"] for old in olds} == {None, "brief", "rail", "review"}
    database = _v23_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        kept = _other_tables(source)
        step_19 = _step_19_rows(source)
    assert [len(rows) for rows in step_19.values()] == [len(_MOVES), len(_OLD_PREFERENCES)]

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v23-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _tables(connection, "jev_*") == {}
        assert _tables(connection, "decision_*") == _CURRENT_DECISION_TABLES
        assert _rows(connection, "SELECT * FROM decision_episodes ORDER BY episode_id") == [
            _moved(old, derived) for old, derived in _MOVES
        ]
        # Every preference but the step-19 `study` one, unchanged.
        assert connection.execute(
            "SELECT * FROM decision_preferences ORDER BY scope, name"
        ).fetchall() == [_OLD_PREFERENCES[0], _OLD_PREFERENCES[1], _OLD_PREFERENCES[3]]
        for table in _ADDED:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert _other_tables(connection) == kept
    (backup,) = tmp_path.glob("app.sqlite3.v23-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (23,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _step_19_rows(saved) == step_19
        assert _other_tables(saved) == kept


@pytest.mark.parametrize("copy", ["_EPISODES_FROM_STEP_19", "_PREFERENCES_FROM_STEP_19"])
def test_a_copy_mismatch_leaves_v23(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, copy: str
) -> None:
    """A copy that lands one row short of its source fails step 24, which nothing else would
    notice: the step's closing count covers neither table. The store stays the v23 store byte for
    byte beside its v23 backup, and the next open moves every row."""
    database = _v23_store(tmp_path)
    before = database.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(storage, copy, f"{getattr(storage, copy)} LIMIT 1")
        with pytest.raises(StateError) as failure:
            AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "corrupt"
    assert database.read_bytes() == before
    assert len(list(tmp_path.glob("app.sqlite3.v23-backup-*.sqlite3"))) == 1
    assert not list(tmp_path.glob(".viki-migration-*"))

    state = AppState(tmp_path / "state.json", Prefs())
    backups = list(tmp_path.glob("app.sqlite3.v23-backup-*.sqlite3"))
    assert len(backups) == 2  # the failed open's and this one's
    for backup in backups:
        with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
            state.store._validate(saved, (23,))
            assert [len(rows) for rows in _step_19_rows(saved).values()] == [
                len(_MOVES),
                len(_OLD_PREFERENCES),
            ]
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert connection.execute("SELECT count(*) FROM decision_episodes").fetchone() == (
            len(_MOVES),
        )
        assert connection.execute("SELECT count(*) FROM decision_preferences").fetchone() == (3,)


def _v18_store(tmp_path: Path) -> Path:
    """A store at the step before the step-19 tables, as the last such build left it: a
    session, a workspace, a draft, a check and one of its runs."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v18-model")
    columns = storage._WORKSPACE_COLUMNS
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            f"INSERT INTO workspaces ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
            tuple(_workspace(OTHER, kind="code").values()),
        )
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        connection.execute(
            "INSERT INTO code_checks VALUES (?,?,?,?,?)", (_CHECK, OTHER, b"sealed", 600, _STAMP)
        )
        connection.execute(
            f"INSERT INTO check_runs VALUES ({','.join('?' * 13)})",
            (_RUN, _CHECK, OTHER, None, "a" * 40, 0, "passed", 0, 1, 1, b"x", _STAMP, _STAMP),
        )
        _downgrade_jev_tables(connection)
        connection.execute("PRAGMA user_version=18")
        connection.commit()
    return state.database_path


_KEPT = ("sessions", "workspaces", "drafts", *storage.CHECK_SCHEMAS)


def _kept_rows(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    return {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in _KEPT
    }


def test_a_v18_store_upgrades_through_24(tmp_path: Path) -> None:
    """One open crosses step 19, which adds the episode tables, up to step 24, which moves them,
    each step beside its own validated backup; every other row comes across."""
    database = _v18_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        assert _tables(source, "jev_*") == {}
        assert _tables(source, "decision_*") == {}
        rows = _kept_rows(source)
    assert all(len(value) == 1 for value in rows.values())

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v18-model")
    assert sorted(
        int(path.name.split(".")[2].split("-")[0][1:]) for path in tmp_path.glob("*backup*")
    ) == list(range(18, 43))
    for version, step_19 in ((18, {}), (23, storage.JEV_SCHEMAS)):
        (backup,) = tmp_path.glob(f"app.sqlite3.v{version}-backup-*.sqlite3")
        with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
            assert saved.execute("PRAGMA user_version").fetchone() == (version,)
            assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert _tables(saved, "jev_*") == step_19
            state.store._validate(saved, (version,))
            assert _kept_rows(saved) == rows
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _tables(connection, "jev_*") == {}
        assert _tables(connection, "decision_*") == _CURRENT_DECISION_TABLES
        indexes = {table: _indexes(connection, table) for table in storage.DECISION_LOOP_SCHEMAS}
        assert indexes == _INDEXES
        for table in storage.DECISION_LOOP_SCHEMAS:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert _kept_rows(connection) == rows


def test_a_newer_store_is_refused_unchanged(tmp_path: Path) -> None:
    path = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA user_version={storage.SCHEMA_VERSION + 1}")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*backup*"))


def _episode(**overrides: Any) -> dict[str, Any]:
    """A decision_episodes row inside every check: a listed decision the rules made, with no
    feedback."""
    row = {
        "episode_id": _EPISODE,
        "scope": SID,
        "entry": "decision",
        "created_at": _STAMP,
        "day": _STAMP[:10],
        "trigger": "heartbeat",
        "candidate": "mission_report",
        "item_key": None,
        "state_hash": None,
        "evidence": "{}",
        "evidence_key": "a" * 64,
        "policy": "rules",
        "policy_version": "r1",
        "model": None,
        "answers": None,
        "terms": None,
        "kind": None,
        "work": "none",
        "level": "list",
        "timing": "now",
        "defer_until": None,
        "reason": "rule",
        "rules_level": "list",
        "language": None,
        "latency_ms": None,
        "input_tokens": None,
        "cost_micro_usd": None,
        "mission_id": None,
        "exposures": 0,
        "first_exposed_at": None,
        "last_exposed_at": None,
        "opened_at": None,
        "acted_at": None,
        "reviewed_at": None,
        "observed_until": _STAMP,
        "feedback": None,
        "feedback_reason": None,
        "feedback_source": None,
        "feedback_revision": 0,
        "feedback_at": None,
        "dedupe_key": "b" * 64,
    }
    return {**row, **overrides}


_FEEDBACK = {"feedback": "wrong", "feedback_source": "launcher", "feedback_at": _STAMP}
_REPORTED = {"entry": "report", "level": None, "work": None, "timing": None}


@pytest.mark.parametrize(
    ("row", "valid"),
    [
        # A decision has a level; a report has none.
        pytest.param(
            _episode(level=None, work=None, timing=None), _episode(**_REPORTED), id="no_level"
        ),
        pytest.param(_episode(entry="report"), _episode(), id="report_level"),
        # Level, work and timing are all set or all empty.
        pytest.param(_episode(work=None), _episode(), id="no_work"),
        pytest.param(_episode(timing=None), _episode(), id="no_timing"),
        pytest.param(
            _episode(entry="report", level=None, timing=None), _episode(**_REPORTED), id="work"
        ),
        # A defer time comes with timing 'at', and only with it.
        pytest.param(_episode(timing="at"), _episode(timing="at", defer_until=_LATER), id="at"),
        pytest.param(
            _episode(defer_until=_LATER), _episode(timing="at", defer_until=_LATER), id="until"
        ),
        # Feedback, its source and its time are all set or all empty.
        pytest.param(
            _episode(feedback="wrong", feedback_source="launcher"),
            _episode(**_FEEDBACK),
            id="feedback_time",
        ),
        pytest.param(
            _episode(feedback="wrong", feedback_at=_STAMP),
            _episode(**_FEEDBACK),
            id="feedback_source",
        ),
        pytest.param(
            _episode(feedback_source="launcher", feedback_at=_STAMP),
            _episode(**_FEEDBACK),
            id="feedback_value",
        ),
        # A feedback reason only with feedback.
        pytest.param(
            _episode(feedback_reason="bad_timing"),
            _episode(**_FEEDBACK, feedback_reason="bad_timing"),
            id="feedback_reason",
        ),
        # Vocabularies and bounds.
        pytest.param(_episode(level="loud"), _episode(level="alert"), id="level"),
        pytest.param(_episode(trigger="timer"), _episode(trigger="mission"), id="trigger"),
        pytest.param(_episode(candidate="Inbox"), _episode(candidate="inbox"), id="candidate"),
        pytest.param(_episode(candidate="x" * 33), _episode(candidate="x" * 32), id="long"),
        pytest.param(_episode(exposures=1_000_001), _episode(exposures=1_000_000), id="seen"),
        pytest.param(_episode(item_key="A" * 64), _episode(item_key="a" * 64), id="item_key"),
    ],
)
def test_episodes_outside_the_checks_are_refused(
    tmp_path: Path, row: dict[str, Any], valid: dict[str, Any]
) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "decision_episodes", row)
        assert connection.execute("SELECT count(*) FROM decision_episodes").fetchone() == (0,)
        _insert(connection, "decision_episodes", valid)  # the same row within bounds is admitted


def test_a_second_episode_with_the_same_dedupe_key_is_refused(tmp_path: Path) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        _insert(connection, "decision_episodes", _episode())
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "decision_episodes", _episode(episode_id=OTHER))
        # The same key in another scope is admitted: the UNIQUE spans (scope, dedupe_key).
        _insert(connection, "decision_episodes", _episode(episode_id=OTHER, scope=OTHER))


def _mission(**overrides: Any) -> dict[str, Any]:
    row = {
        "mission_id": _EPISODE,
        "scope": SID,
        "session_id": OTHER,
        "item_key": "c" * 64,
        "work": "prepare",
        "state": "running",
        "reason": "",
        "body": b"{}",
        "created_at": _STAMP,
        "updated_at": _STAMP,
        "deadline_at": _LATER,
        "finished_at": None,
    }
    return {**row, **overrides}


_KEY = {"slot": "typesafe", "format": 1, "body": b"{}", "updated_at": _STAMP}


@pytest.mark.parametrize(
    ("table", "row", "valid"),
    [
        # A mission has a finish time exactly when it has ended.
        pytest.param(
            "decision_missions",
            _mission(state="stopped"),
            _mission(state="stopped", finished_at=_LATER),
            id="ended",
        ),
        pytest.param("decision_missions", _mission(finished_at=_LATER), _mission(), id="running"),
        pytest.param(
            "decision_missions", _mission(work="coordinate"), _mission(work="run"), id="work"
        ),
        # The item's state hash at the start, when it had one: 64 lower-case hex.
        pytest.param(
            "decision_missions",
            _mission(state_hash="A" * 64),
            _mission(state_hash="a" * 64),
            id="state_hash",
        ),
        # One key, in one slot and format.
        pytest.param("decision_key", {**_KEY, "slot": "other"}, _KEY, id="slot"),
        pytest.param("decision_key", {**_KEY, "format": 2}, _KEY, id="format"),
    ],
)
def test_missions_and_the_key_outside_their_checks_are_refused(
    tmp_path: Path, table: str, row: dict[str, Any], valid: dict[str, Any]
) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, table, row)
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        _insert(connection, table, valid)
