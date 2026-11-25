"""Bot memory ownership upgrades without adding content to the app store."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import _downgrade_bot_code
from tests.persistence.test_storage_schema_proposals import _every_row
from viki_agent import storage
from viki_agent.state import AppState, Prefs


def version_36(tmp_path: Path) -> Path:
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember("00000000-0000-4000-8000-000000000001", mode="plan", effort="high", model=None)
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        _downgrade_bot_code(connection)
        connection.execute("DROP TABLE bot_thread_sessions")
        connection.execute("DROP TABLE bot_threads")
        connection.execute("DROP TABLE session_bot_memory")
        connection.execute("PRAGMA user_version=36")
        connection.commit()
    return state.database_path


def test_v36_upgrade_keeps_existing_rows_and_verifies_its_backup(tmp_path: Path) -> None:
    path = version_36(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        before = _every_row(connection)
    state = AppState(tmp_path / "state.json", Prefs())
    with state.store._connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _every_row(connection) == before
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='session_bot_memory'"
        ).fetchone() == (storage.BOT_MEMORY_SCHEMAS["session_bot_memory"],)
    (backup,) = tmp_path.glob("app.sqlite3.v36-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as connection:
        state.store._validate(connection, (36,))
        assert _every_row(connection) == before


@pytest.mark.parametrize("phase", ["ddl", "row_loss", "commit"])
def test_interrupted_memory_migration_keeps_the_v36_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    path = version_36(tmp_path)
    before, connect = path.read_bytes(), sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            adding = sql.startswith("CREATE TABLE session_bot_memory (")
            if (phase == "ddl" and adding) or (phase == "commit" and sql == "COMMIT"):
                raise sqlite3.OperationalError("synthetic interrupted migration")
            result = super().execute(sql, parameters)
            if phase == "row_loss" and adding:
                super().execute("DELETE FROM sessions")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
        with pytest.raises(storage.StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert path.read_bytes() == before
    with contextlib.closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (36,)


def test_v36_reader_refuses_new_memory_bindings_before_sqlite_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    before = state.database_path.read_bytes()

    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("The older reader opened the upgraded database")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    with (
        pytest.raises(storage.StateError, match="compatible"),
        state.store._connection(versions=tuple(range(1, 37))),
    ):
        pytest.fail("The older reader accepted Bot memory bindings")
    assert state.database_path.read_bytes() == before
