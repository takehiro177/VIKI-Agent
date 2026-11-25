"""Schema step 14: the workspaces table admits kind 'code' beside 'vault'."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import OTHER, SID, _downgrade_schema_fourteen, _workspace
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

_INSERT = (
    f"INSERT INTO workspaces ({','.join(storage._WORKSPACE_COLUMNS)}) "
    f"VALUES ({','.join('?' * len(storage._WORKSPACE_COLUMNS))})"
)


def _row(workspace_id: str, **overrides: Any) -> tuple[Any, ...]:
    return tuple(_workspace(workspace_id, **overrides).values())


def _version_thirteen(tmp_path: Path) -> Path:
    """A schema-13 store, as the last schema-13 build left it, with two vault rows, one active."""
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(_INSERT, _row(SID, active=1))
        connection.execute(_INSERT, _row(OTHER, layout="legacy_para", face_dir="faces"))
        _downgrade_schema_fourteen(connection)
        connection.execute("PRAGMA user_version=13")
        connection.commit()
    return database


def _rows(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return connection.execute("SELECT * FROM workspaces ORDER BY workspace_id").fetchall()


def _workspace_objects(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    """Every object on workspaces, the UNIQUE constraints' autoindexes included."""
    return connection.execute(
        "SELECT type,name,sql FROM sqlite_schema WHERE tbl_name='workspaces' ORDER BY type,name"
    ).fetchall()


def _workspace_ddl(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [row for row in _workspace_objects(connection) if row[2] is not None]


def test_fresh_store_keeps_the_widened_kind_check(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 43
    # Only the kind CHECK widens: every other column, CHECK and the index keep their text.
    narrow, wide = "CHECK(kind = 'vault')", "CHECK(kind IN ('vault','code'))"
    assert storage._WORKSPACE_SCHEMA.count(narrow) == 1
    assert storage._WORKSPACE_SCHEMA.replace(narrow, wide) == storage._WORKSPACE_SCHEMA_V14
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _workspace_ddl(connection) == [
            ("index", "workspaces_active", storage._WORKSPACE_ACTIVE_INDEX),
            ("table", "workspaces", storage._WORKSPACE_SCHEMA_V14),
        ]


def test_code_kind_inserts_and_any_other_kind_is_refused(tmp_path: Path) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(_INSERT, _row(SID, kind="code"))
        connection.execute(_INSERT, _row(OTHER, kind="vault"))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(_INSERT, _row("00000000-0000-4000-8000-000000000003", kind="other"))
        assert connection.execute(
            "SELECT kind FROM workspaces ORDER BY workspace_id"
        ).fetchall() == [
            ("code",),
            ("vault",),
        ]


def test_v13_store_upgrades_to_v14_with_rows_intact(tmp_path: Path) -> None:
    database = _version_thirteen(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        rows = _rows(source)
    assert len(rows) == 2
    fresh = AppState(tmp_path / "fresh" / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(fresh)) as connection:
        objects = _workspace_objects(connection)

    AppState(tmp_path / "state.json", Prefs())
    (backup,) = tmp_path.glob("app.sqlite3.v13-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (13,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _workspace_ddl(saved) == [
            ("index", "workspaces_active", storage._WORKSPACE_ACTIVE_INDEX),
            ("table", "workspaces", storage._WORKSPACE_SCHEMA),
        ]
        assert _rows(saved) == rows
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _workspace_ddl(connection) == [
            ("index", "workspaces_active", storage._WORKSPACE_ACTIVE_INDEX),
            ("table", "workspaces", storage._WORKSPACE_SCHEMA_V14),
        ]
        # The same objects a fresh store has, autoindexes included, under the same names.
        assert _workspace_objects(connection) == objects
        # Every column of every row comes across unchanged, the active flag included.
        assert _rows(connection) == rows
        # The re-created index still admits one selected workspace only.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE workspaces SET active=1 WHERE workspace_id=?", (OTHER,))
        connection.execute("UPDATE workspaces SET kind='code' WHERE workspace_id=?", (OTHER,))


def test_schema_fourteen_compares_the_workspace_count_it_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the copy dropped, only the step's row count stands between the registered folders
    and a store that reads `ok` with none of them."""
    database = _version_thirteen(tmp_path)
    before = database.read_bytes()
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.startswith("INSERT INTO workspaces ("):
                return super().execute("SELECT 1")  # the copy the step believed it made
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError) as failure:
            AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "corrupt"
    assert database.read_bytes() == before
