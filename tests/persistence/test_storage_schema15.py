"""Schema step 15: the session_roots and worktrees tables."""

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
    _downgrade_schema_fifteen,
    _workspace,
)
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

_WORKTREE = "00000000-0000-4000-8000-000000000021"
_OID = "a" * 40


def _ddl(connection: sqlite3.Connection) -> dict[str, str]:
    names = ",".join(f"'{name}'" for name in storage.ROOT_SCHEMAS)
    return dict(
        connection.execute(
            f"SELECT name, sql FROM sqlite_schema WHERE type='table' AND name IN ({names})"
        ).fetchall()
    )


def _rows(connection: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("sessions", "workspaces", "drafts", *storage.ROOT_SCHEMAS)
    }


def _worktree(worktree_id: str, **overrides: Any) -> tuple[Any, ...]:
    row = {
        "worktree_id": worktree_id,
        "workspace_id": SID,
        "path": "<repo>/.worktrees/one",
        "branch": "viki/one",
        "base_oid": _OID,
        "state": "ready",
        "created_at": _STAMP,
        "updated_at": _STAMP,
    }
    return tuple({**row, **overrides}.values())


def _version_fourteen(tmp_path: Path) -> Path:
    """A schema-14 store, as the last schema-14 build left it: a session, a workspace, a draft."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v14-model")
    columns = storage._WORKSPACE_COLUMNS
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            f"INSERT INTO workspaces ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
            tuple(_workspace(OTHER, kind="code").values()),
        )
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _downgrade_schema_fifteen(connection)
        connection.execute("PRAGMA user_version=14")
        connection.commit()
    return state.database_path


def test_fresh_store_is_v15_with_both_tables(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 43
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _ddl(connection) == storage.ROOT_SCHEMAS_V20


def test_v14_store_upgrades_to_v15_with_rows_intact(tmp_path: Path) -> None:
    database = _version_fourteen(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        assert _ddl(source) == {}
        rows = {
            table: source.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("sessions", "workspaces", "drafts")
        }
    assert all(len(value) == 1 for value in rows.values())

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v14-model")
    (backup,) = tmp_path.glob("app.sqlite3.v14-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (14,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _ddl(saved) == {}
        state._store._validate(saved, (14,))  # the store's own check, at the version it names
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _ddl(connection) == storage.ROOT_SCHEMAS_V20
        assert _rows(connection) == {**rows, "session_roots": [], "worktrees": []}


@pytest.mark.parametrize(
    ("table", "row"),
    [
        ("session_roots", (SID[:-1], SID, None, "<repo>", _STAMP)),
        ("worktrees", _worktree(_WORKTREE, state="gone")),
        ("worktrees", _worktree(_WORKTREE, base_oid="a" * 39)),
        ("worktrees", _worktree(_WORKTREE, base_oid="G" + "a" * 39)),
    ],
)
def test_inserts_outside_the_checks_are_refused(
    tmp_path: Path, table: str, row: tuple[Any, ...]
) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    columns = (
        " (session_id,workspace_id,worktree_id,display_root,created_at)"
        if table == "session_roots"
        else ""
    )
    with contextlib.closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO {table}{columns} VALUES ({','.join('?' * len(row))})", row
            )
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_a_second_worktree_with_the_same_path_is_refused(tmp_path: Path) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    insert = f"INSERT INTO worktrees VALUES ({','.join('?' * 8)})"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(insert, _worktree(_WORKTREE))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, _worktree(OTHER))


def test_a_newer_store_is_refused_unchanged_for_the_recovery_shell(tmp_path: Path) -> None:
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
