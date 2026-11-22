"""Schema step 16: the code_checks and check_runs tables."""

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
    _downgrade_schema_sixteen,
    _workspace,
)
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

_WORKTREE = "00000000-0000-4000-8000-000000000021"
_CHECK = "00000000-0000-4000-8000-000000000031"
_RUN = "00000000-0000-4000-8000-000000000041"
_OID = "a" * 40


def _ddl(connection: sqlite3.Connection) -> dict[str, str]:
    names = ",".join(f"'{name}'" for name in storage.CHECK_SCHEMAS)
    return dict(
        connection.execute(
            f"SELECT name, sql FROM sqlite_schema WHERE type='table' AND name IN ({names})"
        ).fetchall()
    )


_KEPT = ("sessions", "workspaces", "drafts", *storage.ROOT_SCHEMAS)


def _rows(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, list[Any]]:
    root_columns = "session_id,workspace_id,worktree_id,display_root,created_at"
    return {
        table: connection.execute(
            f"SELECT {root_columns if table == 'session_roots' else '*'} FROM {table} ORDER BY 1"
        ).fetchall()
        for table in tables
    }


def _check(**overrides: Any) -> tuple[Any, ...]:
    row = {
        "check_id": _CHECK,
        "workspace_id": SID,
        "body": b"sealed",
        "timeout_s": 600,
        "created_at": _STAMP,
    }
    return tuple({**row, **overrides}.values())


def _run(**overrides: Any) -> tuple[Any, ...]:
    row = {
        "run_id": _RUN,
        "check_id": _CHECK,
        "workspace_id": SID,
        "worktree_id": None,
        "head_oid": _OID,
        "dirty": 0,
        "outcome": "passed",
        "exit_code": 0,
        "duration_ms": 1,
        "output_bytes": 1,
        "tail": b"x",
        "started_at": _STAMP,
        "ended_at": _STAMP,
    }
    return tuple({**row, **overrides}.values())


def _version_fifteen(tmp_path: Path) -> Path:
    """A schema-15 store, as the last schema-15 build left it: a session, a workspace, a draft,
    a session root and a worktree."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v15-model")
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
            "INSERT INTO session_roots "
            "(session_id,workspace_id,worktree_id,display_root,created_at) VALUES (?,?,?,?,?)",
            (SID, OTHER, _WORKTREE, "<repo>/.worktrees/one", _STAMP),
        )
        connection.execute(
            "INSERT INTO worktrees VALUES (?,?,?,?,?,?,?,?)",
            (_WORKTREE, OTHER, "<repo>/.worktrees/one", "viki/one", _OID, "ready", _STAMP, _STAMP),
        )
        _downgrade_schema_sixteen(connection)
        connection.execute("PRAGMA user_version=15")
        connection.commit()
    return state.database_path


def test_fresh_store_is_v16_with_both_tables(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 41
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (41,)
        assert _ddl(connection) == storage.CHECK_SCHEMAS


def test_v15_store_upgrades_to_v16_with_rows_intact(tmp_path: Path) -> None:
    database = _version_fifteen(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        assert _ddl(source) == {}
        rows = _rows(source, _KEPT)
    assert all(len(value) == 1 for value in rows.values())

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v15-model")
    (backup,) = tmp_path.glob("app.sqlite3.v15-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (15,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _ddl(saved) == {}
        state._store._validate(saved, (15,))  # the store's own check, at the version it names
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (41,)
        assert _ddl(connection) == storage.CHECK_SCHEMAS
        assert _rows(connection, (*_KEPT, *storage.CHECK_SCHEMAS)) == {
            **rows,
            "check_runs": [],
            "code_checks": [],
        }


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


@pytest.mark.parametrize(
    ("table", "row", "valid"),
    [
        ("code_checks", _check(check_id=_CHECK[:-1]), _check()),
        ("code_checks", _check(timeout_s=59), _check()),
        ("code_checks", _check(timeout_s=3601), _check()),
        ("code_checks", _check(body=b""), _check()),
        ("code_checks", _check(body=b"x" * 65537), _check()),
        ("check_runs", _run(run_id=_RUN[:-1]), _run()),
        ("check_runs", _run(outcome="done"), _run()),
        ("check_runs", _run(head_oid="1" * 39), _run()),
        ("check_runs", _run(head_oid="A" * 40), _run()),
        ("check_runs", _run(dirty=2), _run()),
        ("check_runs", _run(dirty=None), _run()),
        ("check_runs", _run(head_oid=None), _run()),
        ("check_runs", _run(outcome="running"), _run(outcome="running", ended_at=None)),
        ("check_runs", _run(ended_at=None), _run()),
        ("check_runs", _run(tail=b"x" * 81921), _run()),
        ("check_runs", _run(exit_code=4294967296), _run()),
        ("check_runs", _run(duration_ms=-1), _run()),
    ],
)
def test_inserts_outside_the_checks_are_refused(
    tmp_path: Path, table: str, row: tuple[Any, ...], valid: tuple[Any, ...]
) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    insert = f"INSERT INTO {table} VALUES ({','.join('?' * len(row))})"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, row)
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        connection.execute(insert, valid)  # the same row within bounds is admitted
