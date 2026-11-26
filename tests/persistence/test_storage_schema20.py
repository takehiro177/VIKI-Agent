"""Schema 20 rebuilds session roots with a bounded additional-roots snapshot."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import _downgrade_session_labels
from tests.persistence.test_storage_roots import _code, _id, _race, _root, _store
from viki_agent import storage
from viki_agent.state import AppState, Prefs

_OLD_COLUMNS = "session_id,workspace_id,worktree_id,display_root,created_at"


def _version_nineteen(tmp_path: Path) -> Path:
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(_id(1), mode="plan", effort="high", model="preserved-v19-model")
    state._store.put_session_root(_root(1))
    state._store.put_session_root(_root(2, worktree_id=_id(7)))
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute("ALTER TABLE session_roots RENAME TO session_roots_latest")
        connection.execute(storage.ROOT_SCHEMAS["session_roots"])
        connection.execute(
            f"INSERT INTO session_roots ({_OLD_COLUMNS}) "
            f"SELECT {_OLD_COLUMNS} FROM session_roots_latest"
        )
        connection.execute("DROP TABLE session_roots_latest")
        _downgrade_session_labels(connection)
        connection.execute("PRAGMA user_version=19")
        connection.commit()
    return state.database_path


def _rows(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'"
        )
    ]
    return {
        table: connection.execute(
            f"SELECT {_OLD_COLUMNS if table == 'session_roots' else '*'} FROM {table} ORDER BY 1"
        ).fetchall()
        for table in tables
    }


def test_fresh_store_has_schema20_roots_and_byte_bound(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert storage.SCHEMA_VERSION == 43 and storage.JEV_SCHEMA_STEP == 19
    store.put_session_root(_root(1))
    with contextlib.closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='session_roots'"
        ).fetchone() == (storage.ROOT_SCHEMAS_V20["session_roots"],)
        assert connection.execute("SELECT additional_roots FROM session_roots").fetchone() == (
            "[]",
        )
        for raw in ("x" * 32769, "\u00e9" * 16385, "", "x"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE session_roots SET additional_roots=?", (raw,))


def test_v19_migration_preserves_all_rows_and_a_validated_backup(tmp_path: Path) -> None:
    database = _version_nineteen(tmp_path)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        before = _rows(connection)
    store = _store(tmp_path)
    session = store.get(_id(1))
    assert session is not None and session["model"] == "preserved-v19-model"
    assert store.get_session_root(_id(1)) == _root(1)
    assert store.get_session_root(_id(2)) == _root(2, worktree_id=_id(7))
    (backup,) = tmp_path.glob("app.sqlite3.v19-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        store._validate(saved, (19,))
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _rows(saved) == before
        assert saved.execute(
            "SELECT sql FROM sqlite_schema WHERE name='session_roots'"
        ).fetchone() == (storage.ROOT_SCHEMAS["session_roots"],)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        # Step 24 also moves v19's jev_* rows (none here) into its six decision_* tables, and
        # step 25 adds the proposal table.
        assert _rows(connection) == {
            **{name: rows for name, rows in before.items() if name not in storage.JEV_SCHEMAS},
            "bots": [],
            "session_bot_memory": [],
            "bot_threads": [],
            "bot_thread_sessions": [],
            **dict.fromkeys(storage.BOT_CODE_SCHEMAS, []),
            **dict.fromkeys(storage.INBOX_SUGGESTION_SCHEMAS, []),
            **dict.fromkeys(storage.BOT_LEARNING_SCHEMAS, []),
            **dict.fromkeys(storage.BOT_USAGE_SCHEMAS, []),
            "bot_deliveries": [],
            "messaging_config": [],
            **dict.fromkeys(storage.DECISION_LOOP_SCHEMAS, []),
            **dict.fromkeys(storage.DECISION_PROPOSAL_SCHEMAS, []),
        }
        assert connection.execute("SELECT additional_roots FROM session_roots").fetchall() == [
            ("[]",),
            ("[]",),
        ]
    _store(tmp_path)
    (label_backup,) = tmp_path.glob("app.sqlite3.v20-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{label_backup.as_uri()}?mode=ro", uri=True)) as saved:
        store._validate(saved, (20,))
        assert saved.execute("PRAGMA user_version").fetchone() == (20,)
        assert _rows(saved) == before
    (bot_backup,) = tmp_path.glob("app.sqlite3.v21-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{bot_backup.as_uri()}?mode=ro", uri=True)) as saved:
        store._validate(saved, (21,))
        assert saved.execute("PRAGMA user_version").fetchone() == (21,)
        assert _rows(saved) == before
    # Each step from 19 through the current schema backs up its source version.
    assert len(list(tmp_path.glob("*backup*"))) == 24
    assert _code(lambda: _old_version_connection(store)) == "incompatible"


def _old_version_connection(store: storage.SessionStore) -> None:
    # The header/version contract an older binary uses rejects the current store before writing.
    with store._connection(versions=(19,)):
        pytest.fail("The current store was opened under the v19 contract")


@pytest.mark.parametrize("phase", ["backup", "copy", "lost_row"])
def test_failed_v20_migration_keeps_v19_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    database = _version_nineteen(tmp_path)
    before = database.read_bytes()
    connect = sqlite3.connect
    rename = storage.osfs.rename_no_replace

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.startswith("INSERT INTO session_roots ("):
                if phase == "copy":
                    raise sqlite3.OperationalError("synthetic interrupted copy")
                if phase == "lost_row":
                    return super().execute("SELECT 1")
            return super().execute(sql, parameters)

    def publish(source: Path, destination: Path) -> None:
        if phase == "backup" and ".v19-backup-" in destination.name:
            raise OSError("synthetic backup publication failure")
        rename(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
        patch.setattr(storage.osfs, "rename_no_replace", publish)
        assert _code(lambda: _store(tmp_path)) == (
            "corrupt" if phase == "lost_row" else "unavailable"
        )
    assert database.read_bytes() == before
    backups = list(tmp_path.glob("app.sqlite3.v19-backup-*.sqlite3"))
    assert len(backups) == (0 if phase == "backup" else 1)
    assert not list(tmp_path.glob(".viki-migration-*"))
    assert _store(tmp_path).get_session_root(_id(1)) == _root(1)


def test_unreviewed_v19_schema_is_refused_without_a_backup(tmp_path: Path) -> None:
    database = _version_nineteen(tmp_path)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("ALTER TABLE session_roots ADD COLUMN unreviewed TEXT")
        connection.commit()
    before = database.read_bytes()
    assert _code(lambda: _store(tmp_path)) == "incompatible"
    assert database.read_bytes() == before
    assert not list(tmp_path.glob("*backup*"))


def test_concurrent_constructors_publish_one_v19_backup(tmp_path: Path) -> None:
    _version_nineteen(tmp_path)
    outcomes = _race(lambda: _store(tmp_path), lambda: _store(tmp_path))
    assert "ok" in outcomes and set(outcomes) <= {"ok", "busy"}
    assert _store(tmp_path).get_session_root(_id(1)) == _root(1)
    assert len(list(tmp_path.glob("app.sqlite3.v19-backup-*.sqlite3"))) == 1
