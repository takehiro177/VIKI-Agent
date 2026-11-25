"""Step 39 keeps model routes and Bot memory bindings while adding group references."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from tests.persistence.test_storage import _downgrade_bot_code, _insert_profile_binding
from tests.persistence.test_storage_schema_proposals import _every_row
from viki_agent import storage
from viki_agent.state import AppState, Prefs


def test_v38_upgrade_has_a_valid_backup_and_keeps_every_existing_row(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    sid = "00000000-0000-4000-8000-000000000001"
    state.remember(sid, mode="plan", effort="high", model="synthetic-model")
    ordinary = "00000000-0000-4000-8000-000000000002"
    qualified = "viki-llm/11111111-1111-4111-8111-111111111111/synthetic-model"
    state.remember(ordinary, model=qualified)
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        _downgrade_bot_code(connection)
        _insert_profile_binding(connection, sid)
        connection.execute(
            "INSERT INTO session_bot_memory VALUES (?,?)",
            (sid, "00000000-0000-4000-8000-000000000020"),
        )
        connection.execute("DROP TABLE bot_thread_sessions")
        connection.execute("DROP TABLE bot_threads")
        connection.execute("PRAGMA user_version=38")
        connection.commit()
        before = _every_row(connection)
    reopened = AppState(tmp_path / "state.json", Prefs())
    assert reopened.prefs(ordinary).model == qualified
    with reopened.store._connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _every_row(connection) == before
        for name, ddl in storage.BOT_THREAD_SCHEMAS.items():
            assert connection.execute(
                "SELECT sql FROM sqlite_schema WHERE name=?", (name,)
            ).fetchone() == (ddl,)
    (backup,) = tmp_path.glob("app.sqlite3.v38-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as connection:
        reopened.store._validate(connection, (38,))
        assert _every_row(connection) == before
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    (step39,) = tmp_path.glob("app.sqlite3.v39-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(step39)) as connection:
        reopened.store._validate(connection, (39,))
        assert connection.execute("PRAGMA user_version").fetchone() == (39,)
        assert _every_row(connection) == before
        for name, ddl in storage.BOT_THREAD_SCHEMAS.items():
            assert connection.execute(
                "SELECT sql FROM sqlite_schema WHERE name=?", (name,)
            ).fetchone() == (ddl,)


def test_a_v38_reader_refuses_v39_before_opening_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        _downgrade_bot_code(connection)
        connection.execute("PRAGMA user_version=39")
        connection.commit()
        state.store._validate(connection, (39,))
    before = state.database_path.read_bytes()

    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("An older reader must refuse the header before opening SQLite")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    with (
        pytest.raises(storage.StateError, match="compatible"),
        state.store._connection(versions=tuple(range(1, 39))),
    ):
        pytest.fail("An older reader opened a v39 store")
    assert state.database_path.read_bytes() == before
