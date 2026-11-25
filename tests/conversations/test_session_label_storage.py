"""Session labels migrate only app-owned synthetic rows, without changing their revisions."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import _downgrade_decision_loop
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

SID = "00000000-0000-4000-8000-000000000001"
SCOPE = "a" * 64
OTHER_SCOPE = "b" * 64
COLORS = ("blue", "green", "orange", "purple", "red", "slate")
LEGACY_FIELDS = "scope,session_id,revision,title,pinned,archived"
LEGACY_SCHEMA = """CREATE TABLE session_metadata (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    title TEXT NOT NULL CHECK(length(title) <= 256),
    pinned INTEGER NOT NULL CHECK(pinned IN (0,1)),
    archived INTEGER NOT NULL CHECK(archived IN (0,1)),
    PRIMARY KEY (scope, session_id)
) STRICT"""


def _version_twenty(tmp_path: Path, count: int = 1) -> Path:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        _downgrade_decision_loop(connection)
        for table in (*storage.MESSAGING_SCHEMAS, *storage.BOT_SCHEMAS):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("ALTER TABLE session_metadata RENAME TO session_metadata_new")
        connection.execute(LEGACY_SCHEMA)
        connection.execute("DROP TABLE session_metadata_new")
        rows = [
            (SCOPE, f"00000000-0000-4000-8000-{index:012x}", 9, "Synthetic label", 1, 1)
            for index in range(count)
        ]
        rows.append((OTHER_SCOPE, SID, 7, "Other scope", 0, 0))
        connection.executemany("INSERT INTO session_metadata VALUES (?,?,?,?,?,?)", rows)
        connection.execute("PRAGMA user_version=20")
        connection.commit()
    return database


def _row(session_id: str = SID, **changes: Any) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "revision": 1,
        "title": "Synthetic label",
        "pinned": 0,
        "archived": 0,
        "unread": 0,
        "color": None,
        **changes,
    }


def test_fresh_store_has_exact_version_twenty_one_session_labels(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 43
    assert storage._SESSION_METADATA_SCHEMA_V20 == LEGACY_SCHEMA
    state = AppState(tmp_path / "state.json", Prefs())
    with state.store._connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='session_metadata'"
        ).fetchone() == (storage._SESSION_METADATA_SCHEMA,)
    with state.store._connection(write=True) as connection:
        connection.execute(
            f"INSERT INTO session_metadata ({LEGACY_FIELDS}) VALUES (?,?,1,'',0,0)",
            (SCOPE, SID),
        )
    assert state.store.get_session_metadata(SCOPE, SID) == _row(title="")


def test_schema_twenty_labels_migrate_at_cap_with_valid_source_backup(tmp_path: Path) -> None:
    database = _version_twenty(tmp_path, storage.MAX_SESSION_METADATA)
    # Steps 22-25 reshape these tables too (bots, messaging, jev_* into decision_*, then the
    # proposal table); they are out of this test's scope, like session_metadata, which has its
    # own comparison above.
    reshaped = ",".join(
        f"'{name}'"
        for name in (
            *storage.JEV_SCHEMAS,
            *storage.DECISION_LOOP_SCHEMAS,
            *storage.DECISION_PROPOSAL_SCHEMAS,
        )
    )
    with contextlib.closing(sqlite3.connect(database)) as connection:
        before = connection.execute(
            f"SELECT {LEGACY_FIELDS} FROM session_metadata ORDER BY scope,session_id"
        ).fetchall()
        other_schema = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "WHERE name NOT GLOB 'sqlite_*' AND name NOT IN "
            "('session_metadata','session_profile_bindings','routine_occurrences',"
            f"{reshaped}) ORDER BY name"
        ).fetchall()
    state = AppState(tmp_path / "state.json", Prefs())
    with state.store._connection() as connection:
        assert connection.execute(
            f"SELECT {LEGACY_FIELDS},unread,color FROM session_metadata ORDER BY scope,session_id"
        ).fetchall() == [(*row, 0, None) for row in before]
        assert (
            connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema "
                "WHERE name NOT GLOB 'sqlite_*' "
                "AND name NOT IN "
                "('session_metadata','session_profile_bindings','routine_occurrences',"
                "'bots','bot_deliveries','bot_code_folders','bot_code_copies',"
                "'bot_code_baselines','bot_code_bound_session','bot_code_bound_thread_folder',"
                "'bot_usage_sessions','bot_usage_calls','bot_usage_calls_at',"
                "'inbox_suggestions',"
                "'bot_episodes','bot_episodes_bot','bot_session_taint','bot_runs',"
                "'bot_proposals','bot_lesson_stats',"
                "'session_bot_memory',"
                "'bot_threads','bot_thread_sessions',"
                f"'messaging_config',{reshaped}) "
                "ORDER BY name"
            ).fetchall()
            == other_schema
        )
        # Step 29 rebuilds routine_occurrences on purpose, to admit the decision kind.
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='routine_occurrences'"
        ).fetchone() == (storage.ROUTINE_SCHEMAS["routine_occurrences"],)
        assert dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_schema "
                "WHERE name IN ('bots','bot_deliveries','messaging_config')"
            ).fetchall()
        ) == {**storage.BOT_SCHEMAS, **storage.MESSAGING_SCHEMAS}
    assert len(state.store.list_session_metadata(SCOPE)) == storage.MAX_SESSION_METADATA
    missing_id = "00000000-0000-4000-8000-ffffffffffff"
    with pytest.raises(StateError, match="full") as failure:
        state.store.update_session_metadata(SCOPE, missing_id, lambda _: _row(missing_id))
    assert failure.value.code == "capacity"
    (backup,) = tmp_path.glob("app.sqlite3.v20-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as connection:
        state.store._validate(connection, (20,))
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert (
            connection.execute(
                f"SELECT {LEGACY_FIELDS} FROM session_metadata ORDER BY scope,session_id"
            ).fetchall()
            == before
        )
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='session_metadata'"
        ).fetchone() == (LEGACY_SCHEMA,)
    AppState(tmp_path / "state.json", Prefs())
    assert list(tmp_path.glob("app.sqlite3.v20-backup-*.sqlite3")) == [backup]


@pytest.mark.parametrize(
    "boundary",
    ["INSERT INTO session_metadata", "DROP TABLE session_metadata_v20", "skip_copy"],
)
def test_label_migration_failure_rolls_back_and_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database = _version_twenty(tmp_path)
    before = database.read_bytes()
    real_connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if boundary == "skip_copy" and sql.startswith("INSERT INTO session_metadata"):
                return super().execute("SELECT 1")
            if sql.startswith(boundary):
                raise sqlite3.OperationalError("synthetic migration failure")
            return super().execute(sql, parameters)

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return real_connect(*args, **kwargs, factory=FailingConnection)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", connect)
        with pytest.raises(StateError) as failure:
            AppState(tmp_path / "state.json", Prefs())
        if boundary == "skip_copy":
            assert failure.value.code == "corrupt"
    assert database.read_bytes() == before
    assert not list(tmp_path.glob(".viki-migration-*"))
    state = AppState(tmp_path / "state.json", Prefs())
    assert state.store.get_session_metadata(OTHER_SCOPE, SID) == _row(
        revision=7, title="Other scope"
    )


@pytest.mark.parametrize("claimed_version", [20, 21])
def test_inexact_or_partial_label_schema_is_refused_without_writes(
    tmp_path: Path, claimed_version: int
) -> None:
    database = _version_twenty(tmp_path)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        if claimed_version == 20:
            connection.execute("ALTER TABLE session_metadata RENAME TO old_labels")
            connection.execute(
                LEGACY_SCHEMA.replace("length(title) <= 256", "length(title) <= 257")
            )
            connection.execute("DROP TABLE old_labels")
        connection.execute(f"PRAGMA user_version={claimed_version}")
        connection.commit()
    before = database.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == before
    assert not list(tmp_path.glob("*backup*"))


def test_schema_twenty_version_policy_refuses_new_labels_before_sqlite_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        _downgrade_decision_loop(connection)
        for table in (*storage.MESSAGING_SCHEMAS, *storage.BOT_SCHEMAS):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version=21")
        connection.commit()
        state.store._validate(connection, (21,))
    before = state.database_path.read_bytes()

    def refuse_open(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("The old version policy must reject the header first.")

    monkeypatch.setattr(sqlite3, "connect", refuse_open)
    with pytest.raises(StateError) as failure, state.store._connection(versions=(20,)):
        pytest.fail("A schema-20 binary admitted schema 21.")
    assert failure.value.code == "incompatible"
    assert state.database_path.read_bytes() == before


@pytest.mark.parametrize("color", [None, *COLORS])
def test_session_label_upserts_persist_both_fields_without_crossing_scopes(
    tmp_path: Path, color: str | None
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    state.store.update_session_metadata(SCOPE, SID, lambda _: _row())
    expected = _row(revision=2, unread=1, color=color)
    assert state.store.update_session_metadata(SCOPE, SID, lambda _: expected) == expected
    assert (
        AppState(tmp_path / "state.json", Prefs()).store.get_session_metadata(SCOPE, SID)
        == expected
    )
    assert state.store.list_session_metadata(SCOPE) == [expected]
    assert state.store.get_session_metadata(OTHER_SCOPE, SID) is None
    assert state.store.delete_session_metadata(SCOPE, SID, lambda _: None)
    assert state.store.list_session_metadata(SCOPE) == []


@pytest.mark.parametrize("changes", [{"unread": 2}, {"color": "BLUE"}, {"color": ""}, {"color": 2}])
def test_session_label_constraints_reject_invalid_values(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with pytest.raises(StateError):
        state.store.update_session_metadata(SCOPE, SID, lambda _: _row(**changes))
    assert state.store.list_session_metadata(SCOPE) == []
