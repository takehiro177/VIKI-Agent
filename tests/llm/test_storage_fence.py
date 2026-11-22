from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from tests.persistence.test_storage import SID, _downgrade_bot_code
from tests.persistence.test_storage_bot_memory import version_36
from tests.persistence.test_storage_schema_proposals import _every_row
from tests.profiles.test_bot_memory import bot
from tests.profiles.test_bot_memory import rig as rig
from tests.profiles.test_bot_memory import setup as setup
from viki_agent import storage
from viki_agent.state import AppState, Prefs


@pytest.mark.parametrize("reader_version", (36, 37))
def test_pre_connection_reader_refuses_new_route_semantics_before_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader_version: int
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, model="viki-llm/11111111-1111-4111-8111-111111111111/model")
    before = state.database_path.read_bytes()

    def refuse(*args, **kwargs):
        pytest.fail("A subscription-only reader reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with (
            pytest.raises(storage.StateError) as failed,
            state.store._connection(versions=tuple(range(1, reader_version + 1))),
        ):
            pytest.fail("A subscription-only reader admitted endpoint routes")
    assert failed.value.code == "incompatible"
    assert state.database_path.read_bytes() == before


def test_upgrade_from_36_keeps_all_rows_and_a_verified_backup(tmp_path: Path) -> None:
    path = version_36(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as sql:
        before = _every_row(sql)
    migrated = AppState(tmp_path / "state.json", Prefs())
    assert storage.SCHEMA_VERSION == 42
    with migrated.store._connection() as sql:
        assert sql.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(sql) == before
    (memory_backup,) = tmp_path.glob("app.sqlite3.v37-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(memory_backup)) as sql:
        migrated.store._validate(sql, (37,))
        assert _every_row(sql) == before
    (backup,) = tmp_path.glob("app.sqlite3.v36-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(backup)) as sql:
        migrated.store._validate(sql, (36,))
        assert sql.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(sql) == before


def test_upgrade_from_37_keeps_bot_memory_binding_and_verified_backup(rig) -> None:
    service, state, _, profile_id, workspace_id = rig
    owner = bot(service, profile_id, workspace_id)
    session_id = "33333333-3333-4333-8333-333333333333"
    service.prepare(
        profile_id,
        "44444444-4444-4444-8444-444444444444",
        session_id,
        1,
        workspace_id,
        owner,
    )
    with contextlib.closing(sqlite3.connect(state.database_path)) as sql:
        _downgrade_bot_code(sql)
        sql.execute("DROP TABLE bot_thread_sessions")
        sql.execute("DROP TABLE bot_threads")
        before = _every_row(sql)
        sql.execute("PRAGMA user_version=37")
        sql.commit()
    migrated = AppState(state.database_path.parent / "state.json", Prefs())
    with migrated.store._connection() as sql:
        assert sql.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(sql) == before
        assert sql.execute("SELECT * FROM session_bot_memory").fetchall() == [(session_id, owner)]
    (backup,) = state.database_path.parent.glob("app.sqlite3.v37-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(backup)) as sql:
        migrated.store._validate(sql, (37,))
        assert _every_row(sql) == before


def test_unrecognised_future_version_is_refused_before_sqlite_without_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as sql:
        sql.execute(f"PRAGMA user_version={storage.SCHEMA_VERSION + 1}")
        sql.commit()
    before = state.database_path.read_bytes()

    def refuse(*args, **kwargs):
        pytest.fail("An unrecognised format reached SQLite")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    with pytest.raises(storage.StateError) as failed:
        storage.SessionStore(state.database_path, lambda: {})
    assert failed.value.code == "incompatible"
    assert state.database_path.read_bytes() == before
    assert not list(tmp_path.glob("*backup*"))
