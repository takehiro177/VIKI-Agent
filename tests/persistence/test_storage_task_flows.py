"""The task_flows schema step: one row per task."""

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
    _downgrade_task_flows,
    _workspace,
)
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

_WORKTREE = "00000000-0000-4000-8000-000000000021"
_CHECK = "00000000-0000-4000-8000-000000000031"
_RUN = "00000000-0000-4000-8000-000000000041"
_TASK = "00000000-0000-4000-8000-000000000051"
_COMMAND = "00000000-0000-4000-8000-000000000061"
_OID = "a" * 40
_OLDER = 17  # the version before the task_flows table


def _ddl(connection: sqlite3.Connection) -> dict[str, str]:
    names = ",".join(f"'{name}'" for name in storage.TASK_SCHEMAS)
    return dict(
        connection.execute(
            f"SELECT name, sql FROM sqlite_schema WHERE type='table' AND name IN ({names})"
        ).fetchall()
    )


_KEPT = ("sessions", "workspaces", "drafts", *storage.ROOT_SCHEMAS, *storage.CHECK_SCHEMAS)


def _rows(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, list[Any]]:
    root_columns = "session_id,workspace_id,worktree_id,display_root,created_at"
    return {
        table: connection.execute(
            f"SELECT {root_columns if table == 'session_roots' else '*'} FROM {table} ORDER BY 1"
        ).fetchall()
        for table in tables
    }


def _flow(**overrides: Any) -> tuple[Any, ...]:
    row = {
        "task_id": _TASK,
        "command_id": _COMMAND,
        "session_id": SID,
        "workspace_id": OTHER,
        "worktree_id": _WORKTREE,
        "template": "feature",
        "slug": "add-tasks",
        "body": b"sealed",
        "budget_s": 14400,
        "used_s": 0,
        "state": "working",
        "reason": "",
        "owner": OTHER,
        "created_at": _STAMP,
        "updated_at": _STAMP,
        "finished_at": None,
    }
    return tuple({**row, **overrides}.values())


def _version_older(tmp_path: Path) -> Path:
    """A store one version older, as the last build before the task_flows table left it: a
    session, a workspace, a draft, a session root, a worktree, a check and a check run."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-model")
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
        connection.execute(
            "INSERT INTO code_checks VALUES (?,?,?,?,?)", (_CHECK, OTHER, b"sealed", 600, _STAMP)
        )
        connection.execute(
            f"INSERT INTO check_runs VALUES ({','.join('?' * 13)})",
            (_RUN, _CHECK, OTHER, None, _OID, 0, "passed", 0, 1, 1, b"x", _STAMP, _STAMP),
        )
        _downgrade_task_flows(connection)
        connection.execute(f"PRAGMA user_version={_OLDER}")
        connection.commit()
    return state.database_path


def test_fresh_store_has_the_task_flows_table(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 43
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (storage.SCHEMA_VERSION,)
        assert _ddl(connection) == storage.TASK_SCHEMAS


def test_a_store_one_version_older_upgrades_with_rows_intact(tmp_path: Path) -> None:
    database = _version_older(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        assert _ddl(source) == {}
        rows = _rows(source, _KEPT)
    assert all(len(value) == 1 for value in rows.values())

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-model")
    (backup,) = tmp_path.glob(f"app.sqlite3.v{_OLDER}-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (_OLDER,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _ddl(saved) == {}
        state._store._validate(saved, (_OLDER,))  # the store's own check, at the version it names
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (storage.SCHEMA_VERSION,)
        assert _ddl(connection) == storage.TASK_SCHEMAS
        assert _rows(connection, (*_KEPT, *storage.TASK_SCHEMAS)) == {**rows, "task_flows": []}


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
    ("row", "valid"),
    [
        (_flow(state="paused"), _flow()),
        (_flow(state="stopped"), _flow(state="stopped", finished_at=_STAMP)),
        (_flow(task_id=_TASK + "0"), _flow()),
    ],
)
def test_inserts_outside_the_checks_are_refused(
    tmp_path: Path, row: tuple[Any, ...], valid: tuple[Any, ...]
) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    insert = f"INSERT INTO task_flows VALUES ({','.join('?' * len(row))})"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, row)
        assert connection.execute("SELECT count(*) FROM task_flows").fetchone() == (0,)
        connection.execute(insert, valid)  # the same row within bounds is admitted
