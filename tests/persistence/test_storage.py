"""Transactional persistence and migration, entirely inside synthetic temporary directories."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast, get_args

import pytest

from viki_agent import domain, protection, storage
from viki_agent.conversations import approvals, commands
from viki_agent.state import MAX_MODEL_BYTES, MAX_SESSION_ID_BYTES, AppState, Prefs
from viki_agent.storage import APPLICATION_ID, SCHEMA_VERSION, StateError

SID = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"


def legacy(path: Path) -> bytes:
    payload = {"version": 1, "sessions": {SID: {"mode": "plan", "effort": "max", "model": "opus"}}}
    contents = b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
    path.write_bytes(contents)
    return contents


def test_legacy_import_is_atomic_preserves_bytes_and_is_not_repeated(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = legacy(path)
    state = AppState(path, Prefs())
    assert state.database_path == tmp_path / "app.sqlite3"
    assert state.ours(SID)
    assert state.prefs(SID) == Prefs(mode="plan", effort="max", model="opus")
    assert path.read_bytes() == original
    state.remember(SID, model="sonnet")
    assert path.read_bytes() == original
    path.write_text("corrupt leftover from an older app", encoding="utf-8")
    again = AppState(path, Prefs())
    assert again.prefs(SID).model == "sonnet"
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone() == (1,)
        assert connection.execute("PRAGMA application_id").fetchone() == (APPLICATION_ID,)
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)


def test_two_instances_merge_current_preferences_and_observe_commits(tmp_path: Path) -> None:
    first = AppState(tmp_path / "state.json", Prefs())
    second = AppState(tmp_path / "state.json", Prefs())
    first.remember(SID, mode="plan")
    second.remember(SID, effort="max")
    first.remember(OTHER, model="opus")
    assert first.prefs(SID) == second.prefs(SID) == Prefs(mode="plan", effort="max")
    assert second.ours(OTHER)


def _insert_profile_binding(connection: sqlite3.Connection, session_id: str = SID) -> None:
    connection.execute(
        "INSERT INTO session_profile_bindings "
        "(session_id,command_id,request_hash,profile_id,profile_revision,workspace_id,"
        "workspace_revision,policy,policy_revision,settings_revision,runtime,capability_key,"
        "launch_spec_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            session_id,
            "00000000-0000-4000-8000-000000000010",
            "a" * 64,
            "00000000-0000-4000-8000-000000000011",
            1,
            "00000000-0000-4000-8000-000000000012",
            1,
            "ordinary",
            1,
            0,
            "claude",
            "b" * 64,
            "c" * 64,
            "2026-09-21T00:00:00.000Z",
        ),
    )


def test_session_origin_is_durable_and_binding_mismatches_fail_closed(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    store = state.store
    assert store.session_origin(SID) is None
    state.remember(SID, mode="plan")
    assert store.session_origin(SID) == "app"

    with store._connection(write=True) as connection:
        connection.execute("UPDATE sessions SET origin='profile' WHERE session_id=?", (SID,))
        _insert_profile_binding(connection)
    assert store.session_origin(SID) == "profile"
    state.remember(SID, effort="max")
    assert store.session_origin(SID) == "profile"

    with store._connection(write=True) as connection:
        connection.execute("DELETE FROM session_profile_bindings WHERE session_id=?", (SID,))
    with pytest.raises(StateError) as missing_binding:
        store.session_origin(SID)
    assert missing_binding.value.code == "corrupt"

    with store._connection(write=True) as connection:
        _insert_profile_binding(connection)
        connection.execute("UPDATE sessions SET origin='app' WHERE session_id=?", (SID,))
    with pytest.raises(StateError) as stray_binding:
        store.session_origin(SID)
    assert stray_binding.value.code == "corrupt"

    with store._connection(write=True) as connection:
        connection.execute("DELETE FROM sessions WHERE session_id=?", (SID,))
    with pytest.raises(StateError) as missing_session:
        store.session_origin(SID)
    assert missing_session.value.code == "corrupt"


@pytest.mark.parametrize("statement", ["INSERT INTO sessions", "COMMIT"])
def test_failed_update_or_commit_does_not_publish_or_reappear_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan")
    original_connect = sqlite3.connect

    class Failing(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.startswith(statement):
                raise sqlite3.OperationalError("synthetic disk full: private SQL must not escape")
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(
            storage.sqlite3, "connect", lambda *a, **kw: original_connect(*a, factory=Failing, **kw)
        )
        with pytest.raises(StateError) as failure:
            state.remember(SID, effort="max")
        assert failure.value.code == "unavailable"
        assert "private SQL" not in str(failure.value)
    assert state.prefs(SID) == Prefs(mode="plan")
    state.remember(OTHER)
    assert AppState(tmp_path / "state.json", Prefs()).prefs(SID) == Prefs(mode="plan")


def test_busy_writer_keeps_last_good_data_and_releases_connection(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan")
    with contextlib.closing(sqlite3.connect(state.database_path, isolation_level=None)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(StateError) as failure:
            state.remember(SID, effort="max")
        assert failure.value.code == "busy"
        assert state.prefs(SID) == Prefs(mode="plan")
        writer.execute("ROLLBACK")
    state.remember(SID, effort="max")
    assert state.prefs(SID).effort == "max"


def test_migration_publish_failure_keeps_original_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    original = legacy(path)
    with monkeypatch.context() as patch:
        patch.setattr(
            storage.osfs, "rename_no_replace", lambda *_: (_ for _ in ()).throw(PermissionError())
        )
        with pytest.raises(StateError):
            AppState(path, Prefs())
    assert path.read_bytes() == original
    assert not (tmp_path / "app.sqlite3").exists()
    assert AppState(path, Prefs()).prefs(SID).mode == "plan"


def test_interrupted_stage_does_not_replace_or_obstruct_the_original(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = legacy(path)
    abandoned = tmp_path / ".viki-state-interrupted.sqlite3"
    abandoned.write_bytes(b"unfinished stage")
    assert AppState(path, Prefs()).ours(SID)
    assert path.read_bytes() == original
    assert abandoned.read_bytes() == b"unfinished stage"


@pytest.mark.parametrize("content", [b"", b"SQLite format 3\x00", b"not a database"])
def test_empty_truncated_and_corrupt_existing_database_are_never_reinitialized(
    tmp_path: Path, content: bytes
) -> None:
    legacy(tmp_path / "state.json")
    database = tmp_path / "app.sqlite3"
    database.write_bytes(content)
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "corrupt"
    assert database.read_bytes() == content


@pytest.mark.parametrize("alter", ["foreign", "newer", "schema", "wal", "trigger"])
def test_incompatible_database_is_unchanged_without_new_sidecars(
    tmp_path: Path, alter: str
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        if alter == "foreign":
            connection.execute("PRAGMA application_id=7")
        elif alter == "newer":
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        elif alter == "schema":
            connection.execute("CREATE TABLE unrelated (secret TEXT)")
        elif alter == "trigger":
            connection.execute(
                "CREATE TRIGGER sqliteXrewrite AFTER INSERT ON sessions BEGIN "
                "UPDATE sessions SET model='changed-by-trigger' "
                "WHERE session_id=new.session_id; END"
            )
        else:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.commit()
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_database_and_sidecar_hardlinks_are_refused(tmp_path: Path, suffix: str) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    original = tmp_path / "unrelated-original"
    if not suffix:
        state.database_path.rename(original)
    else:
        original.write_bytes(b"do not change")
    target = Path(f"{state.database_path}{suffix}")
    os.link(original, target)
    before = original.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "unsafe_path"
    assert original.read_bytes() == before


def test_orphan_journal_is_preserved_instead_of_initializing_a_database(tmp_path: Path) -> None:
    journal = tmp_path / "app.sqlite3-journal"
    journal.write_bytes(b"recovery material")
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "corrupt"
    assert journal.read_bytes() == b"recovery material"
    assert not (tmp_path / "app.sqlite3").exists()


def test_connections_close_after_reads_writes_and_failures(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID)
    assert state.ours(SID)
    assert state.prefs(SID) == Prefs()
    moved = tmp_path / "renamed.sqlite3"
    state.database_path.rename(moved)
    with pytest.raises(StateError):
        state.remember(SID, mode="plan")
    assert not state.database_path.exists()
    moved.rename(state.database_path)
    state.remember(SID, mode="plan")


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "bypassPermissions"},
        {"mode": []},
        {"effort": "extreme"},
        {"effort": {}},
        {"model": "\ud800"},
        {"model": []},
        {"unexpected": "x"},
    ],
)
def test_invalid_runtime_preferences_never_enter_the_database(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with pytest.raises(StateError) as failure:
        state.remember(SID, **changes)
    assert failure.value.code == "invalid_preferences"
    assert not state.ours(SID)


def test_failed_import_transaction_leaves_original_and_no_partial_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    original = legacy(path)
    original_connect = sqlite3.connect

    class Failing(sqlite3.Connection):
        def executemany(self, sql: str, seq_of_parameters: Any, /) -> sqlite3.Cursor:
            # First row really reaches the transaction before the simulated storage failure.
            super().executemany(sql, seq_of_parameters)
            raise sqlite3.OperationalError("synthetic disk full")

    with monkeypatch.context() as patch:
        patch.setattr(
            storage.sqlite3, "connect", lambda *a, **kw: original_connect(*a, factory=Failing, **kw)
        )
        with pytest.raises(StateError):
            AppState(path, Prefs())
    assert path.read_bytes() == original
    assert not (tmp_path / "app.sqlite3").exists()
    assert AppState(path, Prefs()).ours(SID)


def test_a_competing_publish_is_never_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = AppState(tmp_path / "first" / "state.json", Prefs())
    first.remember(OTHER, model="other-instance")
    winner = first.database_path.read_bytes()
    path = tmp_path / "state.json"
    original = legacy(path)
    original_rename = storage.osfs.rename_no_replace

    def publish_winner(source: Path, destination: Path) -> None:
        destination.write_bytes(winner)
        original_rename(source, destination)  # The publish must refuse the existing destination.

    monkeypatch.setattr(storage.osfs, "rename_no_replace", publish_winner)
    selected = AppState(path, Prefs())
    assert selected.ours(OTHER) and not selected.ours(SID)
    assert selected.database_path.read_bytes() == winner
    assert path.read_bytes() == original


def test_extended_readonly_rollback_is_an_actionable_recovery_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    before = state.database_path.read_bytes()
    error = sqlite3.OperationalError("attempt to write a readonly database")
    error.sqlite_errorcode = sqlite3.SQLITE_READONLY_ROLLBACK

    def rollback_required(*_args: Any, **_kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(storage.sqlite3, "connect", rollback_required)
    with pytest.raises(StateError) as failure:
        state.prefs(SID)
    assert failure.value.code == "recovery_required"
    assert state.database_path.read_bytes() == before


def test_redirected_parent_is_refused_before_any_database_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_lstat = Path.lstat

    def redirected(path: Path) -> Any:
        info = original_lstat(path)
        if path == tmp_path:
            from types import SimpleNamespace

            return SimpleNamespace(
                st_mode=info.st_mode,
                st_nlink=info.st_nlink,
                st_file_attributes=storage.stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return info

    monkeypatch.setattr(Path, "lstat", redirected)
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "unsafe_path"
    assert not (tmp_path / "app.sqlite3").exists()


def test_database_override_uses_only_the_explicit_destination(tmp_path: Path) -> None:
    database = tmp_path / "other" / "metadata.sqlite3"
    state = AppState(tmp_path / "state.json", Prefs(), database_path=database)
    state.remember(SID)
    assert state.database_path == database
    assert not (tmp_path / "app.sqlite3").exists()
    assert AppState(tmp_path / "state.json", Prefs(), database_path=database).ours(SID)


@pytest.mark.parametrize("model", ["\ud800", "a" * (MAX_MODEL_BYTES + 1)])
def test_unusable_native_default_is_reported_before_any_database_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    def unexpected_sql(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("invalid model must be refused before opening SQL")

    monkeypatch.setattr(storage.sqlite3, "connect", unexpected_sql)
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs(model=model))
    assert failure.value.code == "invalid_preferences"
    assert not (tmp_path / "app.sqlite3").exists()


@pytest.mark.parametrize(
    ("session_id", "model"),
    [("a" * (MAX_SESSION_ID_BYTES + 1), "opus"), (SID, "あ" * (MAX_MODEL_BYTES // 3 + 1))],
)
def test_metadata_bounds_are_checked_before_sql_and_preserve_existing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str, model: str
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, model="sonnet")
    before = state.database_path.read_bytes()

    def unexpected_sql(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("oversized metadata must be refused before opening SQL")

    monkeypatch.setattr(storage.sqlite3, "connect", unexpected_sql)
    with pytest.raises(StateError) as failure:
        state.remember(session_id, model=model)
    assert failure.value.code == "invalid_preferences"
    assert state.database_path.read_bytes() == before


def _version_one(path: Path) -> None:
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.execute(storage._SESSION_SCHEMA_V6)
        connection.execute(
            "INSERT INTO sessions VALUES (?, 'plan', 'max', 'preserved-v1-model')", (SID,)
        )
        connection.commit()


def test_version_one_migrates_once_and_preserves_a_valid_metadata_backup(tmp_path: Path) -> None:
    database = tmp_path / "app.sqlite3"
    _version_one(database)
    legacy_path = tmp_path / "state.json"
    legacy_path.write_text("unreadable leftover must not be consulted", encoding="utf-8")
    state = AppState(legacy_path, Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="max", model="preserved-v1-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM approvals").fetchone() == (0,)
    backups = list(tmp_path.glob("app.sqlite3.v1-backup-*.sqlite3"))
    assert len(backups) == 1
    backup_bytes = backups[0].read_bytes()
    with contextlib.closing(
        sqlite3.connect(f"{backups[0].as_uri()}?mode=ro", uri=True)
    ) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert connection.execute(
            "SELECT model FROM sessions WHERE session_id=?", (SID,)
        ).fetchone() == ("preserved-v1-model",)
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='drafts'").fetchall()
            == []
        )
    state.remember(SID, model="updated-v2")
    assert AppState(legacy_path, Prefs()).prefs(SID).model == "updated-v2"
    assert list(tmp_path.glob("app.sqlite3.v1-backup-*.sqlite3")) == backups
    assert backups[0].read_bytes() == backup_bytes
    # One open crossed every step: v1 jumps straight to 4, then one backup per step up to
    # SCHEMA_VERSION.
    assert sorted(path.name.split(".")[2].split("-")[0] for path in tmp_path.glob("*backup*")) == [
        "v1",
        "v10",
        "v11",
        "v12",
        "v13",
        "v14",
        "v15",
        "v16",
        "v17",
        "v18",
        "v19",
        "v20",
        "v21",
        "v22",
        "v23",
        "v24",
        "v25",
        "v26",
        "v27",
        "v28",
        "v29",
        "v30",
        "v31",
        "v32",
        "v33",
        "v34",
        "v35",
        "v36",
        "v37",
        "v38",
        "v39",
        "v4",
        "v40",
        "v41",
        "v42",
        "v5",
        "v6",
        "v7",
        "v8",
        "v9",
    ]


@pytest.mark.parametrize("statement", [storage._DRAFT_SCHEMA, "COMMIT"])
def test_interrupted_schema_migration_rolls_back_and_keeps_its_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
) -> None:
    database = tmp_path / "app.sqlite3"
    _version_one(database)
    original = database.read_bytes()
    connect = sqlite3.connect

    class Failing(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == statement:
                if statement == storage._DRAFT_SCHEMA:
                    super().execute(sql, parameters)  # The DDL really happened before failure.
                raise sqlite3.OperationalError("synthetic migration failure")
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(
            storage.sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Failing, **kw)
        )
        with pytest.raises(StateError) as failure:
            AppState(tmp_path / "state.json", Prefs())
        assert failure.value.code == "unavailable"
    assert database.read_bytes() == original
    backups = list(tmp_path.glob("app.sqlite3.v1-backup-*.sqlite3"))
    assert len(backups) == 1
    preserved = backups[0].read_bytes()
    assert AppState(tmp_path / "state.json", Prefs()).prefs(SID).model == "preserved-v1-model"
    assert backups[0].read_bytes() == preserved


def test_backup_publish_failure_never_starts_schema_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "app.sqlite3"
    _version_one(database)
    original = database.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            storage.osfs, "rename_no_replace", lambda *a: (_ for _ in ()).throw(PermissionError())
        )
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == original
    assert list(tmp_path.iterdir()) == [database]


def test_unknown_version_one_schema_is_not_migrated_or_backed_up(tmp_path: Path) -> None:
    database = tmp_path / "app.sqlite3"
    _version_one(database)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE unrelated (body TEXT)")
        connection.commit()
    original = database.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == original and list(tmp_path.iterdir()) == [database]


def test_concurrent_constructors_publish_one_schema_migration(tmp_path: Path) -> None:
    _version_one(tmp_path / "app.sqlite3")
    barrier = threading.Barrier(2)

    def start() -> AppState | StateError:
        barrier.wait()
        try:
            return AppState(tmp_path / "state.json", Prefs())
        except StateError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(start) for _ in range(2)]
        results = [future.result() for future in futures]
    assert any(isinstance(result, AppState) for result in results)
    assert all(not isinstance(result, StateError) or result.code == "busy" for result in results)
    assert AppState(tmp_path / "state.json", Prefs()).prefs(SID).model == "preserved-v1-model"
    assert len(list(tmp_path.glob("app.sqlite3.v1-backup-*.sqlite3"))) == 1


def test_new_app_data_leaf_is_created_private_before_database_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = tmp_path / "ordinary-ancestor" / "private-data"
    original = protection.create_private_directory
    calls: list[Path] = []

    def create(path: Path) -> bool:
        calls.append(path)
        assert path == final and path.parent.is_dir() and not path.exists()
        return original(path)

    monkeypatch.setattr(protection, "create_private_directory", create)
    state = AppState(final / "state.json", Prefs())
    assert calls == [final]
    protection.require_private_directory(final)
    protection.require_private_file(state.database_path)


def test_existing_app_data_directory_permissions_are_never_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        protection, "create_private_directory", lambda path: pytest.fail("changed an existing leaf")
    )
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID)
    assert state.ours(SID)


def test_failed_private_leaf_creation_never_publishes_a_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = tmp_path / "private-data"

    def refuse(path: Path) -> bool:
        raise protection.ProtectionError()

    monkeypatch.setattr(protection, "create_private_directory", refuse)
    with pytest.raises(StateError) as failure:
        AppState(final / "state.json", Prefs())
    assert failure.value.code == "unavailable"
    assert not final.exists()


def _drop_schema_five(connection: sqlite3.Connection) -> None:
    """Undo every post-v3 object so a current store can pose as a released older one."""
    for table in (
        *storage.LEARNING_TABLES,
        *reversed(storage.INBOX_SUGGESTION_SCHEMAS),
        *reversed(storage.BOT_USAGE_SCHEMAS),
        *reversed(storage.BOT_CODE_SCHEMAS),
        *storage.DECISION_PROPOSAL_SCHEMAS,
        *storage.MESSAGING_SCHEMAS,
        *storage.BOT_MEMORY_SCHEMAS,
        *storage.BOT_THREAD_SCHEMAS,
        *storage.BOT_SCHEMAS,
        *storage.BOOKMARK_SCHEMAS,
        *storage.DECISION_SCHEMAS,
        *storage.OPERATION_SCHEMAS,
        *storage.MEMORY_SCHEMAS,
        *storage.ROUTINE_SCHEMAS,
        *storage.PROFILE_SCHEMAS,
        *storage.ROOT_SCHEMAS,
        *storage.CHECK_SCHEMAS,
        *storage.SKILL_SCHEMAS,
        *storage.TASK_SCHEMAS,
        *storage.DECISION_LOOP_SCHEMAS,
    ):
        connection.execute(f"DROP TABLE {table}")
    for table in (
        "asset_refs",
        "assets",
        "conversation_archives",
        "queued_turns",
        "session_metadata",
        "turn_queues",
        "workspaces",
        "settings",
        "setting_commands",
        "legacy_binding",
    ):
        connection.execute(f"DROP TABLE {table}")  # dropping workspaces drops its partial index
    # Strip current discriminator columns so the lowered user_version describes a real release.
    connection.execute("ALTER TABLE sessions RENAME TO sessions_v7")
    connection.execute(storage._SESSION_SCHEMA_V6)
    connection.execute(
        "INSERT INTO sessions (session_id,mode,effort,model) "
        "SELECT session_id,mode,effort,model FROM sessions_v7"
    )
    connection.execute("DROP TABLE sessions_v7")
    connection.execute("ALTER TABLE drafts RENAME TO drafts_v6")
    connection.execute(storage._DRAFT_SCHEMA)
    connection.execute(
        "INSERT INTO drafts (scope,key,revision,body,text_bytes,saved_at) "
        "SELECT scope,key,revision,body,text_bytes,saved_at FROM drafts_v6"
    )
    connection.execute("DROP TABLE drafts_v6")
    connection.execute("ALTER TABLE commands RENAME TO commands_v6")
    connection.execute(storage._COMMAND_SCHEMA)
    connection.execute(
        "INSERT INTO commands "
        "(scope,command_id,session_id,generation,draft_revision,request_hash,owner,state,reason,"
        "body,text_bytes,created_at,updated_at) "
        "SELECT scope,command_id,session_id,generation,draft_revision,request_hash,owner,state,"
        "reason,body,text_bytes,created_at,updated_at FROM commands_v6"
    )
    connection.execute("DROP TABLE commands_v6")


def _version_two(tmp_path: Path) -> tuple[Path, bytes]:
    from viki_agent.conversations.drafts import DraftService

    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v2-model")
    DraftService(state, tmp_path / "vault").save(SID, 0, "private-v2-submission-naïve")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        cipher = connection.execute("SELECT body FROM drafts").fetchone()[0]
        _drop_schema_five(connection)
        connection.execute("DROP TABLE approvals")
        connection.execute("DROP TABLE commands")
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    return state.database_path, cipher


def test_schema_two_migration_preserves_ciphertext_and_valid_backup(tmp_path: Path) -> None:
    from viki_agent.conversations.drafts import DraftService

    database, cipher = _version_two(tmp_path)
    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v2-model"
    assert DraftService(state, tmp_path / "vault").get(SID).text == "private-v2-submission-naïve"
    (backup,) = tmp_path.glob("app.sqlite3.v2-backup-*.sqlite3")
    for path, version in ((backup, 2), (database, SCHEMA_VERSION)):
        with contextlib.closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (version,)
            assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert connection.execute("SELECT body FROM drafts").fetchone() == (cipher,)
    AppState(tmp_path / "state.json", Prefs())
    assert len(list(tmp_path.glob("app.sqlite3.v2-backup-*.sqlite3"))) == 1


def test_schema_two_with_the_current_draft_table_is_refused_without_a_backup(
    tmp_path: Path,
) -> None:
    # No released v2 store has the current drafts table, so this shape is foreign, not legacy.
    database = tmp_path / "app.sqlite3"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=2")
        connection.execute(storage._SESSION_SCHEMA_V6)
        connection.execute(storage._DRAFT_SCHEMA_V6)
        connection.commit()
    before = database.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == before
    assert not list(tmp_path.glob("app.sqlite3.v2-backup-*"))


@pytest.mark.parametrize("boundary", ["CREATE TABLE commands", "COMMIT"])
def test_schema_two_migration_failure_rolls_back_without_reencrypting_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database, cipher = _version_two(tmp_path)
    before = database.read_bytes()
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if sql.startswith(boundary):
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    assert len(list(tmp_path.glob("app.sqlite3.v2-backup-*.sqlite3"))) == 1
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("SELECT body FROM drafts").fetchone() == (cipher,)


def test_unknown_schema_two_is_refused_without_backup(tmp_path: Path) -> None:
    database, _ = _version_two(tmp_path)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE foreign_data (value TEXT)")
        connection.commit()
    before = database.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == before
    assert not list(tmp_path.glob("*.v2-backup-*"))


def _version_three(tmp_path: Path) -> tuple[Path, bytes, bytes]:
    from viki_agent.conversations.commands import CommandInput, CommandService
    from viki_agent.conversations.drafts import DraftService

    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v3-model")
    DraftService(state, tmp_path / "vault").save(SID, 0, "private-v3-submission-naïve")
    CommandService(state, tmp_path / "vault", owner=OTHER).accept(
        CommandInput("00000000-0000-4000-8000-000000000003", SID, OTHER, 1)
    )
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        draft = connection.execute("SELECT body FROM drafts").fetchone()[0]
        submitted = connection.execute("SELECT body FROM commands").fetchone()[0]
        _drop_schema_five(connection)
        connection.execute("DROP TABLE approvals")
        connection.execute("PRAGMA user_version=3")
        connection.commit()
    return state.database_path, draft, submitted


def test_schema_three_migration_preserves_commands_and_valid_backup(tmp_path: Path) -> None:
    database, draft, submitted = _version_three(tmp_path)
    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v3-model"
    (backup,) = tmp_path.glob("app.sqlite3.v3-backup-*.sqlite3")
    for path, version in ((backup, 3), (database, SCHEMA_VERSION)):
        with contextlib.closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (version,)
            assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert connection.execute("SELECT body FROM drafts").fetchone() == (draft,)
            assert connection.execute("SELECT body FROM commands").fetchone() == (submitted,)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM approvals").fetchone() == (0,)
    AppState(tmp_path / "state.json", Prefs())
    assert len(list(tmp_path.glob("app.sqlite3.v3-backup-*.sqlite3"))) == 1


@pytest.mark.parametrize("boundary", ["CREATE TABLE approvals", "COMMIT"])
def test_schema_three_migration_failure_rolls_back_and_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database, draft, submitted = _version_three(tmp_path)
    before = database.read_bytes()
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if sql.startswith(boundary):
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    assert len(list(tmp_path.glob("app.sqlite3.v3-backup-*.sqlite3"))) == 1
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("SELECT body FROM drafts").fetchone() == (draft,)
        assert connection.execute("SELECT body FROM commands").fetchone() == (submitted,)


def test_unknown_schema_three_is_refused_without_backup(tmp_path: Path) -> None:
    database, _, _ = _version_three(tmp_path)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE foreign_data (value TEXT)")
        connection.commit()
    before = database.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == before
    assert not list(tmp_path.glob("*.v3-backup-*"))


@pytest.mark.parametrize("origin", ["fresh", 1, 2, 3, 4])
def test_current_schema_validates_with_the_unique_autoindex_invisible(
    tmp_path: Path, origin: object
) -> None:
    """The approvals UNIQUE is enforced, and _validate compares the named objects only."""
    home = tmp_path / str(origin)
    home.mkdir()
    if origin == "fresh":
        AppState(home / "state.json", Prefs())
    elif origin == 3:
        _version_three(home)
    elif origin == 4:
        _version_four(home / "app.sqlite3")
    else:
        with contextlib.closing(sqlite3.connect(home / "app.sqlite3")) as connection:
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={origin}")
            connection.execute(storage._SESSION_SCHEMA_V6)
            if origin == 2:
                connection.execute(storage._DRAFT_SCHEMA)
            connection.commit()
    # A schema _validate rejects raises "incompatible" from this constructor, so reaching the
    # assertions at all is the measurement: the two sqlite_autoindex_* entries stay invisible.
    state = AppState(home / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        assert sorted(row[1] for row in connection.execute("PRAGMA index_list(approvals)")) == [
            "sqlite_autoindex_approvals_1",
            "sqlite_autoindex_approvals_2",
        ]
        stamp = "2026-09-16T00:00:00.000Z"
        row = ("0" * 64, SID, OTHER, SID, SID, SID, SID, "toolu_1", "b" * 64, SID, "recorded", "")
        insert = f"INSERT INTO approvals VALUES ({','.join('?' * 15)})"
        connection.execute(insert, (*row, b"x", stamp, stamp))
        with pytest.raises(sqlite3.IntegrityError):
            # A second choice_id for the same (scope, session, runtime, request) is refused by
            # the UNIQUE the schema comparison never sees.
            connection.execute(
                insert,
                (row[0], "00000000-0000-4000-8000-000000000009", *row[2:], b"x", stamp, stamp),
            )


_SCHEMA_SIX_NAMES = [
    "approvals",
    "asset_refs",
    "assets",
    "commands",
    "conversation_archives",
    "drafts",
    "legacy_binding",
    "queued_turns",
    "session_metadata",
    "sessions",
    "setting_commands",
    "settings",
    "turn_queues",
    "workspaces",
    "workspaces_active",
]
_CURRENT_SCHEMA_NAMES = sorted(
    [
        *_SCHEMA_SIX_NAMES,
        *storage.PROFILE_SCHEMAS,
        *storage.ROUTINE_SCHEMAS,
        *storage.BOOKMARK_SCHEMAS,
        *storage.DECISION_SCHEMAS,
        *storage.OPERATION_SCHEMAS,
        *storage.MEMORY_SCHEMAS,
        *storage.ROOT_SCHEMAS,
        *storage.CHECK_SCHEMAS,
        *storage.SKILL_SCHEMAS,
        *storage.TASK_SCHEMAS,
        *storage.DECISION_LOOP_SCHEMAS,
        "task_attempts_active",
        *storage.BOT_MEMORY_SCHEMAS,
        *storage.BOT_THREAD_SCHEMAS,
        *storage.BOT_SCHEMAS,
        *storage.MESSAGING_SCHEMAS,
        *storage.DECISION_PROPOSAL_SCHEMAS,
        *storage.BOT_CODE_SCHEMAS,
        *storage.BOT_CODE_INDEXES,
        *storage.INBOX_SUGGESTION_SCHEMAS,
        *storage.BOT_USAGE_SCHEMAS,
        *storage.BOT_USAGE_INDEXES,
        *storage.BOT_LEARNING_SCHEMAS,
        *storage.BOT_LEARNING_INDEXES,
    ]
)
_ROUTINE_TABLES = ",".join(f"'{name}'" for name in storage.ROUTINE_SCHEMAS)
_BOOKMARK_TABLES = ",".join(f"'{name}'" for name in storage.BOOKMARK_SCHEMAS)
_DECISION_TABLES = ",".join(f"'{name}'" for name in storage.DECISION_SCHEMAS)
_OPERATION_TABLES = ",".join(f"'{name}'" for name in storage.OPERATION_SCHEMAS)
_MEMORY_TABLES = ",".join(f"'{name}'" for name in storage.MEMORY_SCHEMAS)
_ROOT_TABLES = ",".join(f"'{name}'" for name in storage.ROOT_SCHEMAS)
_CHECK_TABLES = ",".join(f"'{name}'" for name in storage.CHECK_SCHEMAS)
_SKILL_TABLES = ",".join(f"'{name}'" for name in storage.SKILL_SCHEMAS)
_TASK_TABLES = ",".join(f"'{name}'" for name in storage.TASK_SCHEMAS)
_DECISION_LOOP_TABLES = ",".join(f"'{name}'" for name in storage.DECISION_LOOP_SCHEMAS)
_BOT_TABLES = ",".join(
    f"'{name}'"
    for name in (
        *storage.BOT_SCHEMAS,
        *storage.BOT_MEMORY_SCHEMAS,
        *storage.BOT_THREAD_SCHEMAS,
        *storage.BOT_CODE_SCHEMAS,
        *storage.BOT_CODE_INDEXES,
        *storage.INBOX_SUGGESTION_SCHEMAS,
        *storage.BOT_USAGE_SCHEMAS,
        *storage.BOT_USAGE_INDEXES,
        *storage.BOT_LEARNING_SCHEMAS,
        *storage.BOT_LEARNING_INDEXES,
    )
)
_MESSAGING_TABLES = ",".join(f"'{name}'" for name in storage.MESSAGING_SCHEMAS)
_DECISION_PROPOSAL_TABLES = ",".join(f"'{name}'" for name in storage.DECISION_PROPOSAL_SCHEMAS)
# The two tables step 13 rebuilt and the one it added; their DDL is pinned by that step.
_SCHEMA_THIRTEEN_TABLES = f"{_MEMORY_TABLES},'vault_proposals','vault_publications'"
# The three tables step 10 rebuilt to carry format 3; their DDL is pinned by that step.
_FORMAT_TABLES = "'commands','drafts','queued_turns'"
_STAMP = "2026-09-17T00:00:00.000Z"
_SCOPE = "a" * 64
_OTHER_SCOPE = "b" * 64
# The launch running the reset, and a launch that has exited. Only one process can hold this
# store, so `owner <> OURS` means that launch is over.
OURS = "00000000-0000-4000-8000-000000000101"
DEAD = "00000000-0000-4000-8000-000000000102"
# Only the state/reason pairs approvals.py admits; the store itself checks neither.
_APPROVAL_REASON = {"not_delivered": "interrupted", "unknown": "interrupted"}


def _choice(index: int) -> str:
    return f"00000000-0000-4000-9000-{index:012d}"


def _stamp(second: int) -> str:
    return f"2026-09-17T00:00:{second:02d}.000Z"


def _seed_approvals(database: Path, rows: list[tuple[str, int, str, str, str, bytes]]) -> None:
    """Insert (scope, index, owner, state, created_at, body) rows straight through SQLite."""
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            f"INSERT INTO approvals VALUES ({','.join('?' * 15)})",
            [
                (
                    scope,
                    _choice(index),
                    SID,
                    OTHER,
                    SID,
                    _choice(index),  # request_id: one row per native request, as the UNIQUE says
                    SID,
                    "toolu_1",
                    "b" * 64,
                    owner,
                    row_state,
                    _APPROVAL_REASON.get(row_state, ""),
                    body,
                    created_at,
                    created_at,
                )
                for scope, index, owner, row_state, created_at, body in rows
            ],
        )
        connection.commit()


def _seed_commands(database: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """Insert (scope, state, reason, owner) rows straight through SQLite."""
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            f"INSERT INTO commands VALUES ({','.join('?' * 15)})",
            [
                (
                    scope,
                    f"00000000-0000-4000-8000-{index:012d}",
                    SID,
                    OTHER,
                    SID,
                    index,
                    "c" * 64,
                    owner,
                    row_state,
                    reason,
                    b"x",
                    1,
                    1,
                    _STAMP,
                    _STAMP,
                )
                for index, (scope, row_state, reason, owner) in enumerate(rows, start=1)
            ],
        )
        connection.commit()


def _count(database: Path, table: str, scope: str) -> int:
    with contextlib.closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            f"SELECT count(*) FROM {table} WHERE scope=?", (scope,)
        ).fetchone()[0]


def _choice_ids(database: Path, scope: str) -> list[str]:
    with contextlib.closing(sqlite3.connect(database)) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT choice_id FROM approvals WHERE scope=? ORDER BY choice_id", (scope,)
            )
        ]


def _version_four(path: Path) -> None:
    """A schema-4 store exactly as the released code left it: no schema-5 objects at all."""
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=4")
        for ddl in (
            storage._SESSION_SCHEMA_V6,
            storage._DRAFT_SCHEMA,
            storage._COMMAND_SCHEMA,
            storage._APPROVAL_SCHEMA,
        ):
            connection.execute(ddl)
        connection.execute(
            "INSERT INTO sessions VALUES (?, 'plan', 'max', 'preserved-v4-model')", (SID,)
        )
        connection.commit()


def _workspace(workspace_id: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "workspace_id": workspace_id,
        "kind": "vault",
        "canonical_key": f"c:\\synthetic\\{workspace_id}",
        "display_root": f"C:\\Synthetic\\{workspace_id}",
        "physical_id": f"1:{workspace_id}",
        "name": "synthetic",
        "policy": "ordinary",
        "policy_revision": 1,
        "revision": 1,
        "source": "created",
        "layout": "unknown",
        "portable_id": None,
        "identity": "{}",
        "face_dir": None,
        "bootstrap": "none",
        "command_id": None,
        "active": 0,
        "created_at": _STAMP,
        "updated_at": _STAMP,
    }
    return {**row, **overrides}


def _settings(revision: int, updated_at: str = _STAMP) -> dict[str, Any]:
    return {
        "version": 1,
        "revision": revision,
        "document": '{"theme":"dark"}',
        "updated_at": updated_at,
    }


def test_current_schema_creates_every_table_and_the_workspace_index(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='workspaces_active'"
        ).fetchone() == (storage._WORKSPACE_ACTIVE_INDEX,)
    store = state.store
    store.update_workspace(SID, lambda current, rows: _workspace(SID, active=1))
    store.update_workspace(OTHER, lambda current, rows: _workspace(OTHER, active=0))
    with (
        contextlib.closing(sqlite3.connect(state.database_path)) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        # The partial index admits many inactive rows and only one selected workspace.
        connection.execute("UPDATE workspaces SET active=1 WHERE workspace_id=?", (OTHER,))


def test_fresh_schema_7_ddl_is_frozen(tmp_path: Path) -> None:
    """The schema is frozen, and the other tests take their expected DDL from storage itself,
    so this is the one mechanical pin. Changing any schema-6/7 object needs a maintainer's decision
    and a new schema step. The routine, bookmark, decision, operation, memory, root, check,
    skill, task-flow, decision-loop, decision-proposal, Bot and format tables are left out, each
    pinned by the step that owns it, so this digest is still the schema-7 one: no schema-7
    object this step still owns moved. Step 13 widened one CHECK of vault_publications, so its
    row is hashed in the pre-13 text a schema-7 to 12 store validates against, which pins that
    text to the bytes step 7 shipped. Step 14 widened the kind CHECK of workspaces, so its row is
    likewise hashed in the pre-14 text. Step 21 added label columns, so the metadata row is
    hashed in its retained schema-20 text."""
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        rows = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema WHERE tbl_name NOT IN "
            f"({_ROUTINE_TABLES},{_BOOKMARK_TABLES},{_DECISION_TABLES},{_OPERATION_TABLES},"
            f"{_MEMORY_TABLES},{_ROOT_TABLES},{_CHECK_TABLES},{_SKILL_TABLES},{_TASK_TABLES},"
            f"{_DECISION_LOOP_TABLES},{_DECISION_PROPOSAL_TABLES},{_BOT_TABLES},"
            f"{_MESSAGING_TABLES},{_FORMAT_TABLES}) "
            "ORDER BY type,name"
        ).fetchall()
    before = {
        **storage.SCHEMAS_BEFORE_13,
        "workspaces": storage._WORKSPACE_SCHEMA,
        "session_metadata": storage._SESSION_METADATA_SCHEMA_V20,
        "session_profile_bindings": storage.PROFILE_SCHEMAS["session_profile_bindings"],
    }
    rows = [(kind, name, before.get(name, sql)) for kind, name, sql in rows]
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == "b92e36210a874d5eee60eac1cd63fb7c9c5d3e78d11f851d067eb3b0cfdf58c0"


def test_fresh_schema_8_ddl_is_frozen(tmp_path: Path) -> None:
    """Step 8's routine tables and index never change once shipped: a schema-8 to 28 store in
    the wild means exactly these objects, so a later change takes its own schema step. Step 29
    rebuilt routine_occurrences, so a fresh store is put back on its old DDL first."""
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        _downgrade_routine_occurrences(connection)
        rows = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            f"WHERE tbl_name IN ({_ROUTINE_TABLES}) ORDER BY type,name"
        ).fetchall()
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == "eb4b42c483dd75d6e9f76269c449df9ee8faa4bf9a163bd8c845fc7730212bbb"


def test_fresh_schema_9_ddl_is_frozen(tmp_path: Path) -> None:
    """Step 9's bookmark table never changes once shipped: a schema-9 store in the wild means
    exactly this object and its primary-key autoindex, so a later change takes its own schema
    step."""
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        rows = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            f"WHERE tbl_name IN ({_BOOKMARK_TABLES}) ORDER BY type,name"
        ).fetchall()
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == "cbaee4b8ff9fe6c5f0240bbbbae87af5d489d799151efb3c7fa7d04e3e130450"


def test_schema_ten_widens_only_the_format_check() -> None:
    """Step 10 rebuilds three tables that hold encrypted user text, so what it changes is read
    off the shipped v6 DDL rather than retyped: one CHECK per table, nothing else."""
    assert (
        storage._DRAFT_SCHEMA_V6.replace("CHECK(format IN (1,2))", "CHECK(format IN (1,2,3))")
        == storage._DRAFT_SCHEMA_V10
    )
    assert (
        storage._COMMAND_SCHEMA_V6.replace("CHECK(format IN (1,2))", "CHECK(format IN (1,2,3))")
        == storage._COMMAND_SCHEMA_V10
    )
    assert (
        storage._QUEUED_TURN_SCHEMA.replace("CHECK(format = 2)", "CHECK(format IN (2,3))")
        == storage._QUEUED_TURN_SCHEMA_V10
    )
    # The replacements above are vacuously true if the old text never held the old CHECK.
    assert storage._DRAFT_SCHEMA_V10 != storage._DRAFT_SCHEMA_V6
    assert storage._COMMAND_SCHEMA_V10 != storage._COMMAND_SCHEMA_V6
    assert storage._QUEUED_TURN_SCHEMA_V10 != storage._QUEUED_TURN_SCHEMA


def test_fresh_schema_10_format_ddl_is_frozen(tmp_path: Path) -> None:
    """Step 10's three widened tables never change once shipped: a schema-10 store in the wild
    means exactly these objects. The name list is the one _validate compares, still in name
    order; the version literal moved to the schema-11 pin below."""
    assert sorted(_CURRENT_SCHEMA_NAMES) == _CURRENT_SCHEMA_NAMES
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        rows = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            f"WHERE tbl_name IN ({_FORMAT_TABLES}) ORDER BY type,name"
        ).fetchall()
    assert [row[2] for row in rows if row[0] == "table"] == [
        storage._COMMAND_SCHEMA_V10,
        storage._DRAFT_SCHEMA_V10,
        storage._QUEUED_TURN_SCHEMA_V10,
    ]
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == "dda5509f2b8684ba0b46af0409c03e398aa47ef94e1b0bf73543344a5e2655db"


def test_schema_ten_takes_format_three_and_refuses_format_four(tmp_path: Path) -> None:
    """The widened CHECK is what lets a turn carrying excerpt parts persist. Format 4 has no
    meaning, so each table refuses it, and queued_turns still refuses the legacy format 1."""
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        for table, row, at, refused in (
            ("drafts", (_SCOPE, SID, 1, b"x", 3, 1, _STAMP), 4, (0, 4)),
            (
                "commands",
                (_SCOPE, SID, SID, OTHER, SID, 1, "c" * 64, OTHER, "accepted", "", b"x", 3, 1)
                + (_STAMP, _STAMP),
                11,
                (0, 4),
            ),
            ("queued_turns", (_SCOPE, SID, SID, 0, SID, 1, b"x", 3, 1, _STAMP, _STAMP), 7, (1, 4)),
        ):
            insert = f"INSERT INTO {table} VALUES ({','.join('?' * len(row))})"
            assert row[at] == 3
            for value in refused:
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(insert, (*row[:at], value, *row[at + 1 :]))
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
            connection.execute(insert, row)
            assert connection.execute(f"SELECT format FROM {table}").fetchall() == [(3,)]


def test_validate_expects_the_bookmark_table_where_its_name_sorts(tmp_path: Path) -> None:
    """_validate compares a name-ordered list against a name-ordered sqlite_schema read, so a
    step-9 row appended after the index names would make every open answer `incompatible`."""
    assert sorted(_CURRENT_SCHEMA_NAMES) == _CURRENT_SCHEMA_NAMES
    at = _CURRENT_SCHEMA_NAMES.index("vault_bookmarks")
    assert _CURRENT_SCHEMA_NAMES[at - 1 : at + 2] == [
        "turn_queues",
        "vault_bookmarks",
        "vault_operations",
    ]
    # Both _create and the constructor validate, so reaching the assertion is the measurement.
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES


def test_bookmark_rows_are_bounded_by_their_checks(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        row = (SID, "30-knowledge/note.md", _STAMP)
        connection.execute("INSERT INTO vault_bookmarks VALUES (?,?,?)", row)
        for refused in (
            row,  # One row per (workspace, ref): the primary key refuses the second star.
            ("short", "note.md", _STAMP),
            (SID, "", _STAMP),
            (SID, "x" * 1025, _STAMP),
            (SID, "other.md", "2026-09-17"),
            (SID, None, _STAMP),
            (SID, b"note.md", _STAMP),  # STRICT refuses a blob ref.
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO vault_bookmarks VALUES (?,?,?)", refused)
        # STRICT converts an integer ref to text instead of refusing it, so the service that
        # inserts these rows is the one that keeps a ref a ref.
        connection.execute("INSERT INTO vault_bookmarks VALUES (?,?,?)", (SID, 1, _STAMP))
        assert connection.execute(
            "SELECT typeof(ref) FROM vault_bookmarks WHERE ref='1'"
        ).fetchone() == ("text",)


def test_fresh_schema_11_ddl_is_frozen(tmp_path: Path) -> None:
    """Step 11's two decision tables never change once shipped: a schema-11 store in the wild
    means exactly these objects and the UNIQUE's autoindex, so a later change takes its own
    schema step."""
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        rows = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            f"WHERE tbl_name IN ({_DECISION_TABLES}) ORDER BY type,name"
        ).fetchall()
        # The primary key and the UNIQUE (workspace_id, ref) are enforced by two indexes
        # _validate never sees, the second of which refuses a second proposal for one ref.
        assert sorted(
            row[1] for row in connection.execute("PRAGMA index_list(vault_proposals)")
        ) == ["sqlite_autoindex_vault_proposals_1", "sqlite_autoindex_vault_proposals_2"]
    assert [row[2] for row in rows if row[0] == "table"] == [
        storage.DECISION_SCHEMAS["retro_decisions"],
        storage.DECISION_SCHEMAS["vault_proposals"],
    ]
    # Step 13 widened the proposal origin, so that row is hashed in the pre-13 text, which pins
    # it to the bytes step 11 shipped.
    rows = [(kind, name, storage.SCHEMAS_BEFORE_13.get(name, sql)) for kind, name, sql in rows]
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == "223cb9f92ce6f679c5679c6760e4b8bbf8e1af682cc0987858dd85a5d43362e3"


def test_validate_expects_the_decision_tables_where_their_names_sort(tmp_path: Path) -> None:
    """_validate compares a name-ordered list against a name-ordered sqlite_schema read, so a
    step-11 row appended after the later names would make every open answer `incompatible`."""
    assert sorted(_CURRENT_SCHEMA_NAMES) == _CURRENT_SCHEMA_NAMES
    retro = _CURRENT_SCHEMA_NAMES.index("retro_decisions")
    assert _CURRENT_SCHEMA_NAMES[retro - 1 : retro + 2] == [
        "queued_turns",
        "retro_decisions",
        "routine_commands",
    ]
    proposals = _CURRENT_SCHEMA_NAMES.index("vault_proposals")
    assert _CURRENT_SCHEMA_NAMES[proposals - 1 : proposals + 2] == [
        "vault_operations",
        "vault_proposals",
        "vault_publications",
    ]
    # Both _create and the constructor validate, so reaching the assertion is the measurement.
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES


_RETRO_SOURCE = "system/retro/2026-09.md"
_HASH = "a" * 64
_OTHER_HASH = "b" * 64


def test_retro_decision_rows_are_bounded_by_their_checks(tmp_path: Path) -> None:
    """The app may record a choice, a snooze and a hand-off time against a hash it saw, and
    nothing else: no `applied` choice, no decision without one of the two hashes it is bound to,
    and no hand-off of an item that was never decided."""
    state = AppState(tmp_path / "state.json", Prefs())
    insert = f"INSERT INTO retro_decisions VALUES ({','.join('?' * 8)})"
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        row = (SID, _RETRO_SOURCE, "item-1", "approve", _HASH, _STAMP, None, _STAMP)
        connection.execute(insert, row)
        for refused in (
            row,  # One row per (workspace, source, item): the primary key refuses the second.
            ("short", _RETRO_SOURCE, "i", "approve", _HASH, None, None, _STAMP),
            (SID, "", "i", "approve", _HASH, None, None, _STAMP),
            (SID, "s" * 1025, "i", "approve", _HASH, None, None, _STAMP),
            (SID, _RETRO_SOURCE, "", "approve", _HASH, None, None, _STAMP),
            (SID, _RETRO_SOURCE, "i" * 129, "approve", _HASH, None, None, _STAMP),
            # The outcome lives in the retro file: no route and no row can claim an application.
            (SID, _RETRO_SOURCE, "i", "applied", _HASH, None, None, _STAMP),
            (SID, _RETRO_SOURCE, "i", "approve", "A" * 64, None, None, _STAMP),
            (SID, _RETRO_SOURCE, "i", "approve", "a" * 63, None, None, _STAMP),
            # A choice and the hash it was made against stand or fall together.
            (SID, _RETRO_SOURCE, "i", "approve", None, None, _OTHER_HASH, _STAMP),
            (SID, _RETRO_SOURCE, "i", None, _HASH, None, None, _STAMP),
            # A hand-off needs a decision, and a row needs a choice or a snooze to exist at all.
            (SID, _RETRO_SOURCE, "i", None, None, _STAMP, _OTHER_HASH, _STAMP),
            (SID, _RETRO_SOURCE, "i", None, None, None, None, _STAMP),
            (SID, _RETRO_SOURCE, "i", "approve", _HASH, "2026-09-17", None, _STAMP),
            (SID, _RETRO_SOURCE, "i", "approve", _HASH, None, "b" * 63, _STAMP),
            (SID, _RETRO_SOURCE, "i", "approve", _HASH, None, None, "2026-09-17"),
            (SID, _RETRO_SOURCE, "i", b"approve", _HASH, None, None, _STAMP),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, refused)
        assert connection.execute("SELECT count(*) FROM retro_decisions").fetchone() == (1,)
        # A snooze alone is a row; so is a decided item handed off to an editor.
        connection.execute(
            insert, (SID, _RETRO_SOURCE, "item-2", None, None, None, _OTHER_HASH, _STAMP)
        )
        connection.execute(
            insert, (SID, _RETRO_SOURCE, "item-3", "edit", _HASH, _STAMP, _OTHER_HASH, _STAMP)
        )
        assert connection.execute("SELECT count(*) FROM retro_decisions").fetchone() == (3,)


def test_vault_proposal_rows_are_bounded_by_their_checks(tmp_path: Path) -> None:
    """One open proposal per note, its text sealed in `body`, bounded at a megabyte."""
    state = AppState(tmp_path / "state.json", Prefs())
    other_id = "00000000-0000-4000-8000-0000000000bb"
    insert = f"INSERT INTO vault_proposals VALUES ({','.join('?' * 10)})"
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        row = (SID, OTHER, "note.md", "note_edit", _HASH, _OTHER_HASH, b"sealed", 6, None, _STAMP)
        connection.execute(insert, row)
        for refused in (
            row,  # The primary key refuses the second proposal under one id.
            # The UNIQUE refuses a second open proposal for one ref, under any id.
            (other_id, *row[1:]),
            ("short", *row[1:]),
            (other_id, "short", *row[2:]),
            (other_id, OTHER, "", *row[3:]),
            (other_id, OTHER, "x" * 1025, *row[3:]),
            (other_id, OTHER, "other.md", "note_create", *row[4:]),
            (other_id, OTHER, "other.md", "note_edit", "a" * 63, *row[5:]),
            (other_id, OTHER, "other.md", "note_edit", _HASH, "B" * 64, *row[6:]),
            (other_id, OTHER, "other.md", "note_edit", _HASH, None, *row[6:]),
            (other_id, OTHER, "other.md", "note_edit", _HASH, _OTHER_HASH, None, 6, None, _STAMP),
            (other_id, OTHER, "other.md", "note_edit", _HASH, _OTHER_HASH, "x", 6, None, _STAMP),
            (other_id, OTHER, "other.md", "note_edit", _HASH, _OTHER_HASH, b"x", -1, None, _STAMP),
            (other_id, OTHER, "other.md", "note_edit", _HASH, _OTHER_HASH, b"x", (1 << 20) + 1)
            + (None, _STAMP),
            (other_id, OTHER, "other.md", "note_edit", _HASH, _OTHER_HASH, b"x", 6, "2026", _STAMP),
            (other_id, OTHER, "other.md", "note_edit", _HASH, _OTHER_HASH, b"x", 6, None, "2026"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, refused)
        assert connection.execute("SELECT count(*) FROM vault_proposals").fetchone() == (1,)
        # A new note has no base revision, and another ref takes its own proposal.
        connection.execute(
            insert,
            (other_id, OTHER, "new.md", "note_edit", None, _OTHER_HASH, b"x", 1, _STAMP, _STAMP),
        )
        assert connection.execute("SELECT count(*) FROM vault_proposals").fetchone() == (2,)


_OPERATION_ID = "00000000-0000-4000-8000-0000000000c1"
_RECOVERY_ID = "00000000-0000-4000-8000-0000000000d1"
_STEPS = (
    '[{"n":0,"action":"move","ref":"30-knowledge/note.md","to":"90-archive/note.md",'
    '"pre":"' + _HASH + '","post":null,"preimage":null}]'
)
_OPERATION_STATES = (
    "planned",
    "preserved",
    "applying",
    "done",
    "failed",
    "conflict",
    "recovery_required",
)


def test_fresh_schema_12_ddl_is_frozen(tmp_path: Path) -> None:
    """Step 12's operation journal and recovery index never change once shipped: a schema-12
    store in the wild means exactly these objects and their primary-key autoindexes, so a later
    change takes its own schema step."""
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        rows = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            f"WHERE tbl_name IN ({_OPERATION_TABLES}) ORDER BY type,name"
        ).fetchall()
        # One index per table, the primary key's: neither table carries a UNIQUE, and an
        # operation's refs are in its `steps` rather than in a column of their own.
        for table in storage.OPERATION_SCHEMAS:
            assert [row[1] for row in connection.execute(f"PRAGMA index_list({table})")] == [
                f"sqlite_autoindex_{table}_1"
            ]
    assert [row[2] for row in rows if row[0] == "table"] == [
        storage.OPERATION_SCHEMAS["vault_operations"],
        storage.OPERATION_SCHEMAS["vault_recovery"],
    ]
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == "b8369d0b4817c5b0df649a08d75adaeae796433308e6cb7132b0fb076c1bafe7"


def test_operation_rows_are_bounded_by_their_checks(tmp_path: Path) -> None:
    """One row per operation, keyed by the request's `command_id`, carrying the root it was
    admitted against, the request hash a replay is compared with, and a step list under 4 KiB."""
    state = AppState(tmp_path / "state.json", Prefs())
    insert = f"INSERT INTO vault_operations VALUES ({','.join('?' * 10)})"
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        row = (_OPERATION_ID, SID, f"1:{SID}", 1, "move", _HASH, _STEPS, "planned", _STAMP, _STAMP)
        connection.execute(insert, row)
        for refused in (
            row,  # One row per operation id: the primary key refuses the second.
            ("short", *row[1:]),
            (OTHER, "short", *row[2:]),
            (OTHER, SID, "ab", *row[3:]),
            (OTHER, SID, f"1:{SID}", 0, *row[4:]),
            (OTHER, SID, f"1:{SID}", 1, "publish", *row[5:]),
            (OTHER, SID, f"1:{SID}", 1, "move", "A" * 64, *row[6:]),
            (OTHER, SID, f"1:{SID}", 1, "move", "a" * 63, *row[6:]),
            (OTHER, SID, f"1:{SID}", 1, "move", _HASH, "", "planned", _STAMP, _STAMP),
            (OTHER, SID, f"1:{SID}", 1, "move", _HASH, "x" * 4097, "planned", _STAMP, _STAMP),
            (OTHER, SID, f"1:{SID}", 1, "move", _HASH, b"[]", "planned", _STAMP, _STAMP),
            (OTHER, SID, f"1:{SID}", 1, "move", _HASH, _STEPS, "pending", _STAMP, _STAMP),
            (OTHER, SID, f"1:{SID}", 1, "move", _HASH, _STEPS, "planned", "2026-09-17", _STAMP),
            (OTHER, SID, f"1:{SID}", 1, "move", _HASH, _STEPS, "planned", _STAMP, "2026-09-17"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, refused)
        assert connection.execute("SELECT count(*) FROM vault_operations").fetchone() == (1,)
        # Every admitted kind takes a row, and the row walks every state the journal records.
        for index, kind in enumerate(("rename", "move", "archive", "delete", "restore")):
            connection.execute(
                insert, (f"00000000-0000-4000-8000-{index:012d}", *row[1:4], kind, *row[5:])
            )
        for state in _OPERATION_STATES:
            connection.execute(
                "UPDATE vault_operations SET state=? WHERE operation_id=?", (state, _OPERATION_ID)
            )
        assert connection.execute("SELECT count(*) FROM vault_operations").fetchone() == (6,)


def test_recovery_rows_are_bounded_by_their_checks(tmp_path: Path) -> None:
    """The index names a private file and the note it holds, bounded by the note cap; the bytes
    themselves are never in this store."""
    state = AppState(tmp_path / "state.json", Prefs())
    insert = f"INSERT INTO vault_recovery VALUES ({','.join('?' * 8)})"
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        row = (_RECOVERY_ID, SID, "deleted", "30-knowledge/note.md", _HASH, 12, _STAMP, _STAMP)
        connection.execute(insert, row)
        for refused in (
            row,  # One row per entry: the primary key refuses the second.
            ("short", *row[1:]),
            (OTHER, "short", *row[2:]),
            (OTHER, SID, "removed", *row[3:]),
            (OTHER, SID, "deleted", "", *row[4:]),
            (OTHER, SID, "deleted", "x" * 513, *row[4:]),
            (OTHER, SID, "deleted", b"note.md", *row[4:]),
            (OTHER, SID, "deleted", "other.md", "A" * 64, *row[5:]),
            (OTHER, SID, "deleted", "other.md", "a" * 63, *row[5:]),
            (OTHER, SID, "deleted", "other.md", _HASH, -1, _STAMP, _STAMP),
            (OTHER, SID, "deleted", "other.md", _HASH, (2 << 20) + 1, _STAMP, _STAMP),
            (OTHER, SID, "deleted", "other.md", _HASH, 12, "2026-09-17", _STAMP),
            (OTHER, SID, "deleted", "other.md", _HASH, 12, _STAMP, "2026-09-17"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(insert, refused)
        assert connection.execute("SELECT count(*) FROM vault_recovery").fetchone() == (1,)
        # An undo's preimage is the other kind, an empty note is a real entry, and one ref can
        # have both: the file, not the ref, is what an entry names.
        connection.execute(insert, (OTHER, SID, "preimage", *row[3:5], 0, _STAMP, _STAMP))
        assert connection.execute("SELECT count(*) FROM vault_recovery").fetchone() == (2,)


_MEMORY_ID = "00000000-0000-4000-8000-0000000000e1"
_PROMOTION_ID = "00000000-0000-4000-8000-0000000000e2"


def _publication(operation_id: str, stage_bytes: int) -> tuple[object, ...]:
    """A reserved create: the one journal shape whose only size column is the stage's."""
    return (
        (operation_id, SID, "1:2", 1, f"{operation_id}.md", "create")
        + (f".viki-stage-{operation_id}.tmp", f".viki-backup-{operation_id}.tmp", None, "0" * 64)
        + (stage_bytes, None, None, None, "reserved", _STAMP, _STAMP)
    )


def test_fresh_schema_13_ddl_is_frozen(tmp_path: Path) -> None:
    """Step 13's two rebuilt tables and its memory table never change once shipped: a schema-13
    store in the wild means exactly these objects and their autoindexes, so a later change takes
    its own schema step. The version literal is pinned here, so the refusals elsewhere really
    describe a store one version newer than this build's."""
    assert SCHEMA_VERSION == 43
    # What the rebuild changes is read off the shipped text rather than retyped: one CHECK each.
    for current, before, widened, narrow in (
        (
            storage.PROFILE_SCHEMAS["vault_publications"],
            storage.SCHEMAS_BEFORE_13["vault_publications"],
            "CHECK(stage_bytes BETWEEN 0 AND 20971520)",
            "CHECK(stage_bytes BETWEEN 0 AND 2097152)",
        ),
        (
            storage.DECISION_SCHEMAS["vault_proposals"],
            storage.SCHEMAS_BEFORE_13["vault_proposals"],
            "CHECK(origin IN ('note_edit','memory_promotion'))",
            "CHECK(origin IN ('note_edit'))",
        ),
    ):
        assert current.count(widened) == 1
        assert current.replace(widened, narrow) == before
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        rows = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema "
            f"WHERE tbl_name IN ({_SCHEMA_THIRTEEN_TABLES}) ORDER BY type,name"
        ).fetchall()
        # One index on the memory table, its primary key's.
        assert [row[1] for row in connection.execute("PRAGMA index_list(memory_reviews)")] == [
            "sqlite_autoindex_memory_reviews_1"
        ]
    assert [row[2] for row in rows if row[0] == "table"] == [
        storage.MEMORY_SCHEMAS["memory_reviews"],
        storage.DECISION_SCHEMAS["vault_proposals"],
        storage.PROFILE_SCHEMAS["vault_publications"],
    ]
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == "15fad2f7ee41fb435126457975be531205e767edfd80b47aff830acd88c44921"


def test_schema_thirteen_bounds_hold(tmp_path: Path) -> None:
    """Step 13 widens exactly two bounds and adds one table: a stage holds a 20 MiB attachment
    and not a byte more, a proposal may promote a memory record and take no other new origin,
    and a memory review is one hash of one record per vault."""
    state = AppState(tmp_path / "state.json", Prefs())
    publish = f"INSERT INTO vault_publications VALUES ({','.join('?' * 17)})"
    propose = f"INSERT INTO vault_proposals VALUES ({','.join('?' * 10)})"
    review = f"INSERT INTO memory_reviews VALUES ({','.join('?' * 4)})"
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(publish, _publication(SID, 20 << 20))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(publish, _publication(OTHER, (20 << 20) + 1))
        assert connection.execute("SELECT count(*) FROM vault_publications").fetchone() == (1,)
        edit = (SID, OTHER, "note.md", "note_edit", _HASH, _OTHER_HASH, b"sealed", 6, None, _STAMP)
        connection.execute(propose, edit)
        connection.execute(
            propose, (_PROMOTION_ID, OTHER, "memory/m.md", "memory_promotion", None, *edit[5:])
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(propose, (_MEMORY_ID, OTHER, "other.md", "memory_edit", *edit[4:]))
        assert connection.execute("SELECT count(*) FROM vault_proposals").fetchone() == (2,)
        row = (SID, _MEMORY_ID, _HASH, _STAMP)
        connection.execute(review, row)
        for refused in (
            row,  # One review per (workspace, record): the primary key refuses the second.
            ("short", _MEMORY_ID, _HASH, _STAMP),
            (OTHER, "short", _HASH, _STAMP),
            (OTHER, _MEMORY_ID, "A" * 64, _STAMP),
            (OTHER, _MEMORY_ID, "a" * 63, _STAMP),
            (OTHER, _MEMORY_ID, _HASH, "2026-09-17"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(review, refused)
        # The same record reviewed in another vault, and another record in this one.
        connection.execute(review, (OTHER, _MEMORY_ID, _OTHER_HASH, _STAMP))
        connection.execute(review, (SID, OTHER, _HASH, _STAMP))
        assert connection.execute("SELECT count(*) FROM memory_reviews").fetchone() == (3,)


_EARLIER_SCHEMA_EIGHT_RECEIPT = """CREATE TABLE routine_commands (
    command_id TEXT PRIMARY KEY NOT NULL CHECK(length(command_id) = 36),
    operation TEXT NOT NULL CHECK(operation IN
        ('create','revise','enable','pause','archive','restore')),
    routine_id TEXT NOT NULL CHECK(length(routine_id) = 36),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*'),
    result_revision INTEGER CHECK(result_revision IS NULL OR result_revision >= 1),
    result_state_revision INTEGER CHECK(result_state_revision IS NULL
        OR result_state_revision >= 1),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24)
) STRICT"""


def test_earlier_unpublished_schema_eight_is_incompatible_and_unchanged(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute("DROP TABLE routine_commands")
        connection.execute(_EARLIER_SCHEMA_EIGHT_RECEIPT)
        connection.execute(
            "INSERT INTO routine_commands VALUES (?,?,?,?,?,?,?)",
            (SID, "create", OTHER, "a" * 64, 1, 1, _STAMP),
        )
        connection.commit()
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("alter", ["missing", "predicate"])
def test_schema_eight_refuses_missing_or_changed_active_index(tmp_path: Path, alter: str) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute("DROP INDEX task_attempts_active")
        if alter == "predicate":
            connection.execute(
                "CREATE INDEX task_attempts_active ON task_attempts(origin_id) "
                "WHERE state='running'"
            )
        connection.commit()
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("migrate", [False, True])
def test_schema_eight_receipts_keep_mutation_results_and_independent_manual_ids(
    tmp_path: Path, migrate: bool
) -> None:
    if migrate:
        _version_seven(tmp_path)
    state = AppState(tmp_path / "state.json", Prefs())
    with state.store._connection(write=True) as connection:
        for operation in ("create", "revise", "enable", "pause", "archive", "restore", "run"):
            manual = operation == "run"
            receipt = (
                str(uuid.uuid4()),
                operation,
                OTHER,
                "a" * 64,
                9007199254740991,
                None if manual else 9007199254740991,
                None if manual else "draft",
                SID if manual else None,
                SID if manual else None,
                SID if manual else None,
                _STAMP,
            )
            connection.execute(
                "INSERT INTO routine_commands VALUES (?,?,?,?,?,?,?,?,?,?,?)", receipt
            )
            assert (
                connection.execute(
                    "SELECT * FROM routine_commands WHERE command_id=?", (receipt[0],)
                ).fetchone()
                == receipt
            )
        assert connection.execute("PRAGMA foreign_key_list(routine_commands)").fetchall() == []
    again = AppState(tmp_path / "state.json", Prefs())
    with again.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM routine_commands").fetchone() == (7,)


@pytest.mark.parametrize(
    ("operation", "changes"),
    [
        ("create", {"operation": "unsupported"}),
        ("create", {"result_revision": None}),
        ("create", {"result_revision": 0}),
        ("create", {"result_revision": 9007199254740992}),
        ("create", {"result_state_revision": None}),
        ("create", {"result_state_revision": 0}),
        ("create", {"result_state_revision": 9007199254740992}),
        ("create", {"result_state": None}),
        ("create", {"result_state": "claimed"}),
        ("create", {"result_occurrence_id": SID}),
        ("create", {"result_attempt_id": SID}),
        ("create", {"result_session_id": SID}),
        ("run", {"result_state_revision": 1}),
        ("run", {"result_state": "draft"}),
        ("run", {"result_occurrence_id": None}),
        ("run", {"result_attempt_id": None}),
        ("run", {"result_session_id": None}),
        ("run", {"result_occurrence_id": "short"}),
        ("run", {"result_attempt_id": "short"}),
        ("run", {"result_session_id": "short"}),
    ],
)
def test_schema_eight_receipts_refuse_incomplete_or_mixed_results(
    tmp_path: Path, operation: str, changes: dict[str, Any]
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    manual = operation == "run"
    receipt = {
        "command_id": SID,
        "operation": operation,
        "routine_id": OTHER,
        "request_hash": "a" * 64,
        "result_revision": 1,
        "result_state_revision": None if manual else 1,
        "result_state": None if manual else "draft",
        "result_occurrence_id": SID if manual else None,
        "result_attempt_id": SID if manual else None,
        "result_session_id": SID if manual else None,
        "created_at": _STAMP,
    }
    receipt.update(changes)
    with state.store._connection(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO routine_commands ({','.join(receipt)}) "
                f"VALUES ({','.join('?' for _ in receipt)})",
                tuple(receipt.values()),
            )
        assert connection.execute("SELECT count(*) FROM routine_commands").fetchone() == (0,)


def test_released_pre_6_ddl_is_frozen() -> None:
    """Released stores must keep opening: a real v5 store, such as
    test_commands_legacy._v5_store builds, is made from these constants. A one-space edit to
    any of them would refuse that store `incompatible` at its first launch, which the
    schema-7 pin above does not catch."""
    with contextlib.closing(sqlite3.connect(":memory:")) as connection:
        for schema in (
            storage._SESSION_SCHEMA_V6,
            storage._DRAFT_SCHEMA,
            storage._COMMAND_SCHEMA,
            storage._APPROVAL_SCHEMA,
            storage._LEGACY_BINDING_SCHEMA,
            storage._WORKSPACE_SCHEMA,
            storage._WORKSPACE_ACTIVE_INDEX,
            storage._SETTINGS_SCHEMA,
            storage._SETTING_COMMAND_SCHEMA,
        ):
            connection.execute(schema)
        rows = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema ORDER BY type,name"
        ).fetchall()
    digest = hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
    assert digest == "1e0f189a0285dcde18061a1fb94a4b6b20e0949e97954019250586383bbcdcaa"


def test_schema_migration_backs_up_and_refuses_newer(tmp_path: Path) -> None:
    home = tmp_path / "upgrade"
    home.mkdir()
    database = home / "app.sqlite3"
    _version_four(database)
    state = AppState(home / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="max", model="preserved-v4-model")
    (backup,) = home.glob("app.sqlite3.v4-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (4,)
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='workspaces'").fetchall()
            == []
        )
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("SELECT count(*) FROM workspaces").fetchone() == (0,)

    newer = tmp_path / "newer"
    newer.mkdir()
    path = AppState(newer / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(newer / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert path.read_bytes() == before
    assert not list(newer.glob("*backup*"))


def test_migration_chain_from_3_to_current_keeps_every_backup(tmp_path: Path) -> None:
    database, draft, submitted = _version_three(tmp_path)
    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v3-model"
    (third,) = tmp_path.glob("app.sqlite3.v3-backup-*.sqlite3")
    (fourth,) = tmp_path.glob("app.sqlite3.v4-backup-*.sqlite3")
    (fifth,) = tmp_path.glob("app.sqlite3.v5-backup-*.sqlite3")
    (sixth,) = tmp_path.glob("app.sqlite3.v6-backup-*.sqlite3")
    (seventh,) = tmp_path.glob("app.sqlite3.v7-backup-*.sqlite3")
    (eighth,) = tmp_path.glob("app.sqlite3.v8-backup-*.sqlite3")
    (ninth,) = tmp_path.glob("app.sqlite3.v9-backup-*.sqlite3")
    (tenth,) = tmp_path.glob("app.sqlite3.v10-backup-*.sqlite3")
    for path, version in (
        (third, 3),
        (fourth, 4),
        (fifth, 5),
        (sixth, 6),
        (seventh, 7),
        (eighth, 8),
        (ninth, 9),
        (tenth, 10),
        (database, SCHEMA_VERSION),
    ):
        with contextlib.closing(
            sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        ) as connection:
            assert connection.execute("PRAGMA user_version").fetchone() == (version,)
            assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert connection.execute("SELECT count(*) FROM sessions").fetchone() == (1,)
            assert connection.execute("SELECT body FROM drafts").fetchone() == (draft,)
            assert connection.execute("SELECT body FROM commands").fetchone() == (submitted,)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        for table in (
            "approvals",
            "legacy_binding",
            "workspaces",
            "settings",
            "setting_commands",
            # The six tables the 5->6 step creates.
            "assets",
            "asset_refs",
            "conversation_archives",
            "queued_turns",
            "turn_queues",
            "session_metadata",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def _build_schema_five(path: Path, objects: tuple[str, ...]) -> None:
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=5")
        for ddl in objects:
            connection.execute(ddl)
        connection.commit()


_SCHEMA_FIVE_REVERSED = (
    storage._SETTING_COMMAND_SCHEMA,
    storage._SETTINGS_SCHEMA,
    storage._WORKSPACE_SCHEMA,
    storage._WORKSPACE_ACTIVE_INDEX,
    storage._LEGACY_BINDING_SCHEMA,
    storage._APPROVAL_SCHEMA,
    storage._COMMAND_SCHEMA,
    storage._DRAFT_SCHEMA,
    storage._SESSION_SCHEMA_V6,
)

_SCHEMA_SIX_OBJECTS = (
    storage._SESSION_SCHEMA_V6,
    storage._DRAFT_SCHEMA_V6,
    storage._COMMAND_SCHEMA_V6,
    storage._APPROVAL_SCHEMA,
    storage._ASSET_SCHEMA,
    storage._ASSET_REF_SCHEMA,
    storage._CONVERSATION_ARCHIVE_SCHEMA,
    storage._QUEUED_TURN_SCHEMA,
    storage._TURN_QUEUE_SCHEMA,
    storage._SESSION_METADATA_SCHEMA_V20,
    storage._LEGACY_BINDING_SCHEMA,
    storage._WORKSPACE_SCHEMA,
    storage._WORKSPACE_ACTIVE_INDEX,
    storage._SETTINGS_SCHEMA,
    storage._SETTING_COMMAND_SCHEMA,
)


def _build_schema_six(path: Path) -> tuple[bytes, bytes]:
    draft = b"preserved schema-six draft"
    archive = b"preserved schema-six archive"
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=6")
        for ddl in _SCHEMA_SIX_OBJECTS:
            connection.execute(ddl)
        connection.execute(
            "INSERT INTO sessions VALUES (?, 'plan', 'high', 'preserved-v6-model')", (SID,)
        )
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,1,?,2,?,?)",
            (_SCOPE, SID, draft, len(draft), _STAMP),
        )
        connection.execute(
            "INSERT INTO conversation_archives VALUES (?,?,?,?,?)",
            (_SCOPE, SID, _STAMP, archive, len(archive)),
        )
        connection.commit()
    return draft, archive


def test_schema_six_migration_preserves_p2_rows_and_creates_exact_current_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "app.sqlite3"
    draft, archive = _build_schema_six(database)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _SCHEMA_SIX_NAMES

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v6-model"
    assert state.store.session_origin(SID) == "app"
    (backup,) = tmp_path.glob("app.sqlite3.v6-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)
        assert connection.execute("SELECT body FROM drafts").fetchone() == (draft,)
        assert connection.execute("SELECT body FROM conversation_archives").fetchone() == (archive,)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='sessions'"
        ).fetchone() == (storage._SCHEMA,)
        assert connection.execute(
            "SELECT origin FROM sessions WHERE session_id=?", (SID,)
        ).fetchone() == ("app",)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        assert connection.execute("SELECT body FROM drafts").fetchone() == (draft,)
        assert connection.execute("SELECT body FROM conversation_archives").fetchone() == (archive,)
        assert all(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
            for table in storage.PROFILE_SCHEMAS
        )


@pytest.mark.parametrize("boundary", ["first_schema_7_ddl", "COMMIT"])
def test_schema_six_migration_failure_rolls_back_and_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database = tmp_path / "app.sqlite3"
    draft, archive = _build_schema_six(database)
    before = database.read_bytes()
    first_schema_7_ddl = next(iter(storage.PROFILE_SCHEMAS.values()))
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if sql == first_schema_7_ddl and boundary == "first_schema_7_ddl":
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    (backup,) = tmp_path.glob("app.sqlite3.v6-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (6,)
        assert connection.execute("SELECT body FROM drafts").fetchone() == (draft,)
        assert connection.execute("SELECT body FROM conversation_archives").fetchone() == (archive,)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v6-model"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("SELECT body FROM drafts").fetchone() == (draft,)
        assert connection.execute("SELECT body FROM conversation_archives").fetchone() == (archive,)


_PROFILE = "00000000-0000-4000-8000-000000000011"
_SCHEMA_SEVEN_TABLES = [
    name
    for name in _CURRENT_SCHEMA_NAMES
    if name not in {"workspaces_active", "task_attempts_active"}
    and name not in storage.ROUTINE_SCHEMAS
    and name not in storage.BOOKMARK_SCHEMAS
    and name not in storage.DECISION_SCHEMAS
    and name not in storage.OPERATION_SCHEMAS
    and name not in storage.MEMORY_SCHEMAS
    and name not in storage.ROOT_SCHEMAS
    and name not in storage.CHECK_SCHEMAS
    and name not in storage.SKILL_SCHEMAS
    and name not in storage.TASK_SCHEMAS
    and name not in storage.DECISION_LOOP_SCHEMAS
    and name not in storage.BOT_MEMORY_SCHEMAS
    and name not in storage.BOT_THREAD_SCHEMAS
    and name not in storage.BOT_CODE_SCHEMAS
    and name not in storage.BOT_CODE_INDEXES
    and name not in storage.INBOX_SUGGESTION_SCHEMAS
    and name not in storage.BOT_LEARNING_SCHEMAS
    and name not in storage.BOT_LEARNING_INDEXES
    and name not in storage.BOT_USAGE_SCHEMAS
    and name not in storage.BOT_USAGE_INDEXES
    and name not in storage.BOT_SCHEMAS
    and name not in storage.MESSAGING_SCHEMAS
    and name not in storage.DECISION_PROPOSAL_SCHEMAS
]


_PRE_TEN_FORMAT_TABLES = (
    ("drafts", storage._DRAFT_SCHEMA_V6, "scope,key,revision,body,format,text_bytes,saved_at"),
    (
        "commands",
        storage._COMMAND_SCHEMA_V6,
        "scope,command_id,session_id,generation,source_draft_id,draft_revision,request_hash,"
        "owner,state,reason,body,format,text_bytes,created_at,updated_at",
    ),
    (
        "queued_turns",
        storage._QUEUED_TURN_SCHEMA,
        "scope,item_id,session_id,ordinal,source_draft_id,source_draft_revision,body,format,"
        "text_bytes,created_at,updated_at",
    ),
)


def test_schema_twentytwo_migrates_to_current_with_a_valid_backup(tmp_path: Path) -> None:
    """Step 23 adds the messaging_config table the same way step 22 added the bot tables: a
    store lowered to 22 comes back current with an empty table of the exact shipped DDL, every
    other table's rows kept, and one validated v22 backup."""
    state = AppState(tmp_path / "state.json", Prefs())
    database = state.database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        before = {
            name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'"
            ).fetchall()
            if name != "messaging_config"
        }
        _downgrade_messaging(connection)
        connection.execute("PRAGMA user_version=22")
        connection.commit()
    AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='messaging_config'"
        ).fetchone() == (storage.MESSAGING_SCHEMAS["messaging_config"],)
        assert connection.execute("SELECT count(*) FROM messaging_config").fetchone() == (0,)
        after = {
            name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in before
        }
        assert after == before
    (backup,) = tmp_path.glob("app.sqlite3.v22-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (22,))
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert saved.execute("PRAGMA user_version").fetchone() == (22,)


def _downgrade_format_checks(connection: sqlite3.Connection) -> None:
    """Put drafts, commands and queued_turns back to the DDL a released v6 to v9 store has, so a
    fixture lowered from the current store validates at the version it claims. Step 10 widened
    their format CHECK; every column and row comes across unchanged."""
    for table, ddl, columns in _PRE_TEN_FORMAT_TABLES:
        connection.execute(f"ALTER TABLE {table} RENAME TO {table}_wide")
        connection.execute(ddl)
        connection.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table}_wide")
        connection.execute(f"DROP TABLE {table}_wide")


def _downgrade_bot_code(connection: sqlite3.Connection) -> None:
    """Restore the shipped v39 binding DDL before lowering a current fixture's version."""
    for table in storage.LEARNING_TABLES:
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    for table in reversed(storage.INBOX_SUGGESTION_SCHEMAS):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    for table in reversed(storage.BOT_USAGE_SCHEMAS):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    for table in reversed(storage.BOT_CODE_SCHEMAS):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    columns = [row[1] for row in connection.execute("PRAGMA table_info(session_profile_bindings)")]
    if "launch_origin" not in columns:
        return
    columns.remove("launch_origin")
    names = ",".join(columns)
    rows = connection.execute(f"SELECT {names} FROM session_profile_bindings").fetchall()
    # Do not rename the old table: that would retarget session_bot_memory's foreign key.
    connection.execute("DROP TABLE session_profile_bindings")
    connection.execute(storage.PROFILE_SCHEMAS["session_profile_bindings"])
    connection.executemany(
        f"INSERT INTO session_profile_bindings ({names}) VALUES ({','.join('?' * len(columns))})",
        rows,
    )


def _downgrade_routine_occurrences(connection: sqlite3.Connection) -> None:
    """Put routine_occurrences back to the DDL a released v8 to v28 store has, when the table
    exists. Step 29 widened its kind CHECK; every column and row comes across unchanged."""
    _downgrade_bot_code(connection)
    connection.execute("DROP TABLE IF EXISTS session_bot_memory")
    connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
    connection.execute("DROP TABLE IF EXISTS bot_threads")
    if not connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE name='routine_occurrences'"
    ).fetchone():
        return
    connection.execute("ALTER TABLE routine_occurrences RENAME TO routine_occurrences_wide")
    connection.execute(storage.ROUTINE_SCHEMAS_BEFORE_29["routine_occurrences"])
    connection.execute("INSERT INTO routine_occurrences SELECT * FROM routine_occurrences_wide")
    connection.execute("DROP TABLE routine_occurrences_wide")


def _downgrade_decision_proposals(connection: sqlite3.Connection) -> None:
    """Undo schemas 29 and 25 for a historical fixture: put routine_occurrences back to its old
    DDL and drop the question-proposal table."""
    _downgrade_routine_occurrences(connection)
    for table in storage.DECISION_PROPOSAL_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")


def _downgrade_decision_loop(connection: sqlite3.Connection) -> None:
    """Undo schemas 25 and 24 for a historical fixture: drop the proposal and decision-loop tables
    and put the step-19 episode and preference tables back, empty."""
    _downgrade_decision_proposals(connection)
    for table in storage.DECISION_LOOP_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")
    for sql in storage.JEV_SCHEMAS.values():
        connection.execute(sql)


def _downgrade_messaging(connection: sqlite3.Connection) -> None:
    """Undo schemas 25 and 24, then drop schema 23's messaging_config table, for a historical
    fixture."""
    _downgrade_decision_loop(connection)
    for table in storage.MESSAGING_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")


def _downgrade_session_labels(connection: sqlite3.Connection) -> None:
    """Undo schemas 25 and 24, drop schema 23's messaging table and schema 22's Bot tables, and
    undo schema 21's labels for a historical fixture."""
    _downgrade_messaging(connection)
    for table in storage.BOT_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")
    columns = "scope,session_id,revision,title,pinned,archived"
    connection.execute("ALTER TABLE session_metadata RENAME TO session_metadata_v21")
    connection.execute(storage._SESSION_METADATA_SCHEMA_V20)
    connection.execute(
        f"INSERT INTO session_metadata ({columns}) SELECT {columns} FROM session_metadata_v21"
    )
    connection.execute("DROP TABLE session_metadata_v21")


def _downgrade_jev_tables(connection: sqlite3.Connection) -> None:
    """Undo schemas 25 down to 20 and JEV so an older fixture validates: restore the six-column
    label and five-column root tables, then drop the episode and preference tables."""
    _downgrade_session_labels(connection)
    columns = "session_id,workspace_id,worktree_id,display_root,created_at"
    connection.execute("ALTER TABLE session_roots RENAME TO session_roots_v20")
    connection.execute(storage.ROOT_SCHEMAS["session_roots"])
    connection.execute(
        f"INSERT INTO session_roots ({columns}) SELECT {columns} FROM session_roots_v20"
    )
    connection.execute("DROP TABLE session_roots_v20")
    for table in storage.JEV_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")


def _downgrade_task_flows(connection: sqlite3.Connection) -> None:
    """Undo the task_flows step and every later step so a fixture lowered from the current store
    validates at the version it claims: undo the later steps, then drop the task_flows table."""
    _downgrade_jev_tables(connection)
    for table in storage.TASK_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")


def _downgrade_schema_seventeen(connection: sqlite3.Connection) -> None:
    """Undo step 17 and every later step so a fixture lowered from the current store validates
    at the version it claims: undo the later steps, then drop the skill-proposal table."""
    _downgrade_task_flows(connection)
    for table in storage.SKILL_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")


def _downgrade_schema_sixteen(connection: sqlite3.Connection) -> None:
    """Undo step 16 and every later step so a fixture lowered from the current store validates
    at the version it claims: undo the later steps, then drop the check and check-run tables."""
    _downgrade_schema_seventeen(connection)
    for table in storage.CHECK_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")


def _downgrade_schema_fifteen(connection: sqlite3.Connection) -> None:
    """Undo step 15 and every later step so a fixture lowered from the current store validates
    at the version it claims: undo the later steps, then drop the session-root and worktree
    tables."""
    _downgrade_schema_sixteen(connection)
    for table in storage.ROOT_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")


def _downgrade_schema_fourteen(connection: sqlite3.Connection) -> None:
    """Undo step 14 and every later step so a fixture lowered from the current store validates
    at the version it claims: put workspaces back to its pre-14 DDL and index. Every column and
    row comes across unchanged."""
    _downgrade_schema_fifteen(connection)
    connection.execute("ALTER TABLE workspaces RENAME TO workspaces_wide")
    connection.execute(storage._WORKSPACE_SCHEMA)
    connection.execute("INSERT INTO workspaces SELECT * FROM workspaces_wide")
    connection.execute("DROP TABLE workspaces_wide")
    connection.execute(storage._WORKSPACE_ACTIVE_INDEX)


def _downgrade_schema_thirteen(connection: sqlite3.Connection) -> None:
    """Undo step 13 and every later step so a fixture lowered from the current store validates
    at the version it claims: drop the memory table and put each rebuilt table this fixture still
    holds back to its pre-13 DDL. Every column and row comes across unchanged."""
    _downgrade_schema_fourteen(connection)
    for table in storage.MEMORY_SCHEMAS:
        connection.execute(f"DROP TABLE {table}")
    for table, ddl in storage.SCHEMAS_BEFORE_13.items():
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name=?", (table,)).fetchone():
            connection.execute(f"ALTER TABLE {table} RENAME TO {table}_wide")
            connection.execute(ddl)
            connection.execute(f"INSERT INTO {table} SELECT * FROM {table}_wide")
            connection.execute(f"DROP TABLE {table}_wide")


def _version_seven(tmp_path: Path) -> Path:
    """A schema-7 store, as the last schema-7 build left it, with one valid row per table."""
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    rows: dict[str, tuple[object, ...]] = {
        "sessions": (SID, "plan", "high", "preserved-v7-model", "profile"),
        "drafts": (_SCOPE, SID, 1, b"x", 2, 1, _STAMP),
        "commands": (_SCOPE, SID, SID, OTHER, SID, 1, "c" * 64, OTHER, "accepted", "")
        + (b"x", 2, 1, _STAMP, _STAMP),
        "approvals": (_SCOPE, SID, SID, OTHER, SID, SID, SID, "toolu_1", "b" * 64, OTHER)
        + ("recorded", "", b"x", _STAMP, _STAMP),
        "assets": (_SCOPE, SID, "image/png", 1, "d" * 64, 1, 1, _STAMP, "stored", None),
        "asset_refs": (_SCOPE, SID, "draft", SID, "attachment.0", _STAMP),
        "conversation_archives": (_SCOPE, SID, _STAMP, b"x", 1),
        "queued_turns": (_SCOPE, SID, SID, 0, SID, 1, b"x", 2, 0, _STAMP, _STAMP),
        "turn_queues": (_SCOPE, SID, 1, "", _STAMP),
        "session_metadata": (_SCOPE, SID, 1, "title", 0, 0),
        "legacy_binding": (1, "absent", None, None, _STAMP),
        "workspaces": (SID, "vault", "key", "<vault>", "1:2", "Vault", "ordinary", 1, 1)
        + ("opened", "unknown", None, "{}", None, "none", None, 1, _STAMP, _STAMP),
        "settings": ("app", 1, 1, "{}", _STAMP),
        "setting_commands": (SID, 1, "0" * 64, _STAMP),
        "profiles": (_PROFILE, 1, 1, 0, _STAMP, _STAMP),
        "profile_revisions": (_PROFILE, 1, 1, b"{}", 2, "e" * 64, _STAMP),
        "profile_commands": (SID, "create", _PROFILE, "a" * 64, 1, 1, _STAMP),
        "provider_catalogs": ("f" * 64, "claude", "1", "2.0.0", 1, b"{}", 2, "a" * 64, _STAMP),
        "vault_publications": (SID, SID, "1:2", 1, "note.md", "create", f".viki-stage-{SID}.tmp")
        + (f".viki-backup-{SID}.tmp", None, "0" * 64, 3, None, None, None, "reserved")
        + (_STAMP, _STAMP),
        "avatars": (SID, "a" * 64, 1, 1, "b" * 64, 6, _STAMP),
        "avatar_variants": (SID, 24, 1, "c" * 64),
        "profile_avatar_refs": (_PROFILE, 1, SID, _STAMP),
    }
    assert sorted([*rows, "session_profile_bindings"]) == _SCHEMA_SEVEN_TABLES
    with contextlib.closing(sqlite3.connect(database)) as connection:
        for table, row in rows.items():
            columns = (
                "(scope,session_id,revision,title,pinned,archived)"
                if table == "session_metadata"
                else ""
            )
            connection.execute(
                f"INSERT INTO {table} {columns} VALUES ({','.join('?' * len(row))})", row
            )
        _insert_profile_binding(connection)
        for table in (
            *storage.ROUTINE_SCHEMAS,
            *storage.BOOKMARK_SCHEMAS,
            *storage.DECISION_SCHEMAS,
            *storage.OPERATION_SCHEMAS,
        ):
            connection.execute(f"DROP TABLE {table}")
        _downgrade_format_checks(connection)
        _downgrade_schema_thirteen(connection)
        connection.execute("PRAGMA user_version=7")
        connection.commit()
    return database


def _schema_seven_counts(database: Path) -> dict[str, int]:
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        return {
            table: source.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in _SCHEMA_SEVEN_TABLES
        }


def test_schema_seven_migrates_to_eight_with_a_valid_backup(tmp_path: Path) -> None:
    database = _version_seven(tmp_path)
    before = _schema_seven_counts(database)
    assert set(before.values()) == {1}

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v7-model"
    (backup,) = tmp_path.glob("app.sqlite3.v7-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (7,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert (
            saved.execute(
                f"SELECT name FROM sqlite_schema WHERE tbl_name IN ({_ROUTINE_TABLES})"
            ).fetchall()
            == []
        )
    assert _schema_seven_counts(backup) == before
    # One open crosses step 8 and then step 9, each with its own backup.
    assert len(list(tmp_path.glob("app.sqlite3.v8-backup-*.sqlite3"))) == 1
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        assert all(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
            for table in storage.ROUTINE_SCHEMAS
        )
    assert _schema_seven_counts(database) == before


@pytest.mark.parametrize(
    "boundary", ["first_schema_8_ddl", "receipt_ddl", "active_index_ddl", "COMMIT"]
)
def test_schema_seven_migration_failure_rolls_back_and_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database = _version_seven(tmp_path)
    counts = _schema_seven_counts(database)
    before = database.read_bytes()
    first_schema_8_ddl = next(iter(storage.ROUTINE_SCHEMAS.values()))
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if (
                (sql == first_schema_8_ddl and boundary == "first_schema_8_ddl")
                or (
                    sql == storage.ROUTINE_SCHEMAS["routine_commands"] and boundary == "receipt_ddl"
                )
                or (sql == storage.TASK_ATTEMPTS_ACTIVE_INDEX and boundary == "active_index_ddl")
            ):
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    (backup,) = tmp_path.glob("app.sqlite3.v7-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (7,)
    assert _schema_seven_counts(backup) == counts

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v7-model"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    assert _schema_seven_counts(database) == counts


def _version_eight(tmp_path: Path) -> Path:
    """A schema-8 store, as the last schema-8 build left it, with no bookmark table."""
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?,'plan','high','preserved-v8-model','app')", (SID,)
        )
        connection.execute("INSERT INTO drafts VALUES (?,?,1,?,2,1,?)", (_SCOPE, SID, b"x", _STAMP))
        for table in (
            *storage.BOOKMARK_SCHEMAS,
            *storage.DECISION_SCHEMAS,
            *storage.OPERATION_SCHEMAS,
        ):
            connection.execute(f"DROP TABLE {table}")
        _downgrade_format_checks(connection)
        _downgrade_schema_thirteen(connection)
        connection.execute("PRAGMA user_version=8")
        connection.commit()
    return database


def test_schema_eight_migrates_to_nine_with_a_valid_backup(tmp_path: Path) -> None:
    database = _version_eight(tmp_path)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v8-model"
    (backup,) = tmp_path.glob("app.sqlite3.v8-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (8,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert (
            saved.execute(
                f"SELECT name FROM sqlite_schema WHERE tbl_name IN ({_BOOKMARK_TABLES})"
            ).fetchall()
            == []
        )
        assert saved.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='vault_bookmarks'"
        ).fetchone() == (storage.BOOKMARK_SCHEMAS["vault_bookmarks"],)
        assert connection.execute("SELECT count(*) FROM vault_bookmarks").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)


@pytest.mark.parametrize("boundary", ["bookmark_ddl", "COMMIT"])
def test_schema_eight_migration_failure_rolls_back_and_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database = _version_eight(tmp_path)
    before = database.read_bytes()
    bookmark_ddl = storage.BOOKMARK_SCHEMAS["vault_bookmarks"]
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if sql == bookmark_ddl and boundary == "bookmark_ddl":
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    (backup,) = tmp_path.glob("app.sqlite3.v8-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (8,)
        assert saved.execute("SELECT count(*) FROM drafts").fetchone() == (1,)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v8-model"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM vault_bookmarks").fetchone() == (0,)


_NINE_BODIES = {
    "drafts": b"preserved schema-nine draft ciphertext",
    "commands": b"preserved schema-nine command ciphertext",
    "queued_turns": b"preserved schema-nine queued turn ciphertext",
}


def _version_nine(tmp_path: Path) -> Path:
    """A schema-9 store, as the last schema-9 build left it: the three format tables still carry
    the narrow CHECK, one encrypted row each."""
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    draft, command, queued = (
        _NINE_BODIES[table] for table in ("drafts", "commands", "queued_turns")
    )
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?,'plan','high','preserved-v9-model','app')", (SID,)
        )
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,1,?,2,?,?)",
            (_SCOPE, SID, draft, len(draft), _STAMP),
        )
        connection.execute(
            f"INSERT INTO commands VALUES ({','.join('?' * 15)})",
            (_SCOPE, SID, SID, OTHER, SID, 1, "c" * 64, OTHER, "accepted", "", command)
            + (2, len(command), _STAMP, _STAMP),
        )
        connection.execute(
            f"INSERT INTO queued_turns VALUES ({','.join('?' * 11)})",
            (_SCOPE, SID, SID, 0, SID, 1, queued, 2, len(queued), _STAMP, _STAMP),
        )
        for table in (*storage.DECISION_SCHEMAS, *storage.OPERATION_SCHEMAS):
            connection.execute(f"DROP TABLE {table}")
        _downgrade_format_checks(connection)
        _downgrade_schema_thirteen(connection)
        connection.execute("PRAGMA user_version=9")
        connection.commit()
    return database


def _format_rows(connection: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: connection.execute(f"SELECT body,format,text_bytes FROM {table}").fetchall()
        for table in ("drafts", "commands", "queued_turns")
    }


_NINE_ROWS = {table: [(body, 2, len(body))] for table, body in _NINE_BODIES.items()}


def test_schema_nine_migrates_to_ten_with_a_valid_backup(tmp_path: Path) -> None:
    database = _version_nine(tmp_path)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v9-model"
    (backup,) = tmp_path.glob("app.sqlite3.v9-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (9,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _format_rows(saved) == _NINE_ROWS
        assert [
            row[0]
            for row in saved.execute(
                f"SELECT sql FROM sqlite_schema WHERE name IN ({_FORMAT_TABLES}) ORDER BY name"
            )
        ] == [storage._COMMAND_SCHEMA_V6, storage._DRAFT_SCHEMA_V6, storage._QUEUED_TURN_SCHEMA]
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        # Every column, the encrypted body included, comes across unchanged under the new CHECK.
        assert _format_rows(connection) == _NINE_ROWS
        assert [
            row[0]
            for row in connection.execute(
                f"SELECT sql FROM sqlite_schema WHERE name IN ({_FORMAT_TABLES}) ORDER BY name"
            )
        ] == [
            storage._COMMAND_SCHEMA_V10,
            storage._DRAFT_SCHEMA_V10,
            storage._QUEUED_TURN_SCHEMA_V10,
        ]
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        # The step adds no table, so a format-3 row is the only new thing it admits.
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,1,?,1,?,?)", (_OTHER_SCOPE, SID, b"x", 1, _STAMP)
        )
        connection.execute("UPDATE drafts SET format=3 WHERE scope=?", (_OTHER_SCOPE,))


@pytest.mark.parametrize("boundary", ["rename_drafts", "COMMIT"])
def test_schema_nine_migration_failure_rolls_back_and_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database = _version_nine(tmp_path)
    before = database.read_bytes()
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if sql == "ALTER TABLE drafts RENAME TO drafts_v9" and boundary == "rename_drafts":
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    (backup,) = tmp_path.glob("app.sqlite3.v9-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (9,)
        assert _format_rows(saved) == _NINE_ROWS

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v9-model"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert _format_rows(connection) == _NINE_ROWS


def test_schema_ten_compares_the_queued_turn_count_it_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step compares the row count of all three tables it rebuilds. With the queued_turns
    INSERT dropped, the only thing standing between a silently emptied queue and a store that
    reads `ok` is that comparison: without ("queued_turns", 6) in _migrate_step's counts this
    migration commits and the row is gone."""
    database = _version_nine(tmp_path)
    before = database.read_bytes()
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.startswith("INSERT INTO queued_turns "):
                return super().execute("SELECT 1")  # the copy the step believed it made
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError) as failure:
            AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "corrupt"
    assert database.read_bytes() == before


def _version_ten(tmp_path: Path) -> Path:
    """A schema-10 store, as the last schema-10 build left it: the three format tables already
    carry the widened CHECK and a format-3 row, and no decision table exists."""
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    body = b"preserved schema-ten draft ciphertext"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?,'plan','high','preserved-v10-model','app')", (SID,)
        )
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,1,?,3,?,?)", (_SCOPE, SID, body, len(body), _STAMP)
        )
        connection.execute(
            "INSERT INTO vault_bookmarks VALUES (?,?,?)", (SID, "30-knowledge/note.md", _STAMP)
        )
        for table in (*storage.DECISION_SCHEMAS, *storage.OPERATION_SCHEMAS):
            connection.execute(f"DROP TABLE {table}")
        _downgrade_schema_thirteen(connection)
        connection.execute("PRAGMA user_version=10")
        connection.commit()
    return database


def test_schema_ten_migrates_to_eleven_with_a_valid_backup(tmp_path: Path) -> None:
    database = _version_ten(tmp_path)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v10-model"
    (backup,) = tmp_path.glob("app.sqlite3.v10-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (10,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert (
            saved.execute(
                f"SELECT name FROM sqlite_schema WHERE tbl_name IN ({_DECISION_TABLES})"
            ).fetchall()
            == []
        )
        assert saved.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
        assert saved.execute("SELECT count(*) FROM vault_bookmarks").fetchone() == (1,)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        assert [
            row[0]
            for row in connection.execute(
                f"SELECT sql FROM sqlite_schema WHERE name IN ({_DECISION_TABLES}) ORDER BY name"
            )
        ] == [
            storage.DECISION_SCHEMAS["retro_decisions"],
            storage.DECISION_SCHEMAS["vault_proposals"],
        ]
        # The step adds two empty tables and touches nothing a v10 store already held.
        for table in storage.DECISION_SCHEMAS:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
        assert connection.execute("SELECT format FROM drafts").fetchone() == (3,)
        assert connection.execute("SELECT count(*) FROM vault_bookmarks").fetchone() == (1,)


@pytest.mark.parametrize("boundary", ["retro_ddl", "proposal_ddl", "COMMIT"])
def test_schema_ten_migration_failure_rolls_back_and_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database = _version_ten(tmp_path)
    before = database.read_bytes()
    stops = {
        "retro_ddl": storage.DECISION_SCHEMAS["retro_decisions"],
        "proposal_ddl": storage.SCHEMAS_BEFORE_13["vault_proposals"],
    }
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if sql == stops.get(boundary):
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    (backup,) = tmp_path.glob("app.sqlite3.v10-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (10,)
        assert saved.execute("SELECT count(*) FROM drafts").fetchone() == (1,)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v10-model"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM retro_decisions").fetchone() == (0,)


def _version_eleven(tmp_path: Path) -> Path:
    """A schema-11 store, as the last schema-11 build left it: the decision tables are there and
    carry a row, and no operation table exists."""
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    body = b"preserved schema-eleven draft ciphertext"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?,'plan','high','preserved-v11-model','app')", (SID,)
        )
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,1,?,3,?,?)", (_SCOPE, SID, body, len(body), _STAMP)
        )
        connection.execute(
            "INSERT INTO retro_decisions VALUES (?,?,?,?,?,?,?,?)",
            (SID, _RETRO_SOURCE, "item-1", "approve", _HASH, None, None, _STAMP),
        )
        for table in storage.OPERATION_SCHEMAS:
            connection.execute(f"DROP TABLE {table}")
        _downgrade_schema_thirteen(connection)
        connection.execute("PRAGMA user_version=11")
        connection.commit()
    return database


def test_schema_eleven_migrates_to_twelve_with_a_valid_backup(tmp_path: Path) -> None:
    database = _version_eleven(tmp_path)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v11-model"
    (backup,) = tmp_path.glob("app.sqlite3.v11-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (11,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert (
            saved.execute(
                f"SELECT name FROM sqlite_schema WHERE tbl_name IN ({_OPERATION_TABLES})"
            ).fetchall()
            == []
        )
        assert saved.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
        assert saved.execute("SELECT count(*) FROM retro_decisions").fetchone() == (1,)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        assert [
            row[0]
            for row in connection.execute(
                f"SELECT sql FROM sqlite_schema WHERE name IN ({_OPERATION_TABLES}) ORDER BY name"
            )
        ] == [
            storage.OPERATION_SCHEMAS["vault_operations"],
            storage.OPERATION_SCHEMAS["vault_recovery"],
        ]
        # The step adds two empty tables and touches nothing a v11 store already held.
        for table in storage.OPERATION_SCHEMAS:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
        assert connection.execute("SELECT format FROM drafts").fetchone() == (3,)
        assert connection.execute("SELECT choice FROM retro_decisions").fetchone() == ("approve",)


@pytest.mark.parametrize("boundary", ["operation_ddl", "recovery_ddl", "COMMIT"])
def test_schema_eleven_migration_failure_rolls_back_and_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database = _version_eleven(tmp_path)
    before = database.read_bytes()
    stops = {
        "operation_ddl": storage.OPERATION_SCHEMAS["vault_operations"],
        "recovery_ddl": storage.OPERATION_SCHEMAS["vault_recovery"],
    }
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if sql == stops.get(boundary):
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    (backup,) = tmp_path.glob("app.sqlite3.v11-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (11,)
        assert saved.execute("SELECT count(*) FROM drafts").fetchone() == (1,)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v11-model"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM vault_operations").fetchone() == (0,)


def _version_twelve(tmp_path: Path) -> Path:
    """A schema-12 store, as the last schema-12 build left it: both tables step 13 rebuilds hold
    two rows under their narrow CHECKs, the stage at the old 2 MiB ceiling, and no memory table
    exists."""
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    publish = f"INSERT INTO vault_publications VALUES ({','.join('?' * 17)})"
    propose = f"INSERT INTO vault_proposals VALUES ({','.join('?' * 10)})"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO sessions VALUES (?,'plan','high','preserved-v12-model','app')", (SID,)
        )
        connection.execute(publish, _publication(SID, 2 << 20))
        connection.execute(
            publish,
            (OTHER, SID, "1:2", 1, "replaced.md", "replace", f".viki-stage-{OTHER}.tmp")
            + (f".viki-backup-{OTHER}.tmp", "3:4", _HASH, 12, "5:6", _OTHER_HASH, 0, "prepared")
            + (_STAMP, _STAMP),
        )
        connection.execute(
            propose,
            (SID, OTHER, "note.md", "note_edit", _HASH, _OTHER_HASH, b"sealed\x00\xff", 8)
            + (_STAMP, _STAMP),
        )
        connection.execute(
            propose, (OTHER, OTHER, "new.md", "note_edit", None, _HASH, b"", 0, None, _STAMP)
        )
        _downgrade_schema_thirteen(connection)
        connection.execute("PRAGMA user_version=12")
        connection.commit()
    return database


def _rebuilt_rows(connection: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY {key}").fetchall()
        for table, key in (
            ("vault_publications", "operation_id"),
            ("vault_proposals", "proposal_id"),
        )
    }


def _schema_thirteen_objects(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        f"WHERE tbl_name IN ({_SCHEMA_THIRTEEN_TABLES}) ORDER BY type,name"
    ).fetchall()


def test_schema_twelve_migrates_to_thirteen_with_a_valid_backup(tmp_path: Path) -> None:
    database = _version_twelve(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        rows = _rebuilt_rows(source)
    assert [len(table_rows) for table_rows in rows.values()] == [2, 2]
    fresh = AppState(tmp_path / "fresh" / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(fresh)) as connection:
        objects = _schema_thirteen_objects(connection)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v12-model"
    (backup,) = tmp_path.glob("app.sqlite3.v12-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (12,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert [
            row[0]
            for row in saved.execute(
                f"SELECT sql FROM sqlite_schema WHERE tbl_name IN ({_SCHEMA_THIRTEEN_TABLES}) "
                "AND type='table' ORDER BY name"
            )
        ] == [
            storage.SCHEMAS_BEFORE_13["vault_proposals"],
            storage.SCHEMAS_BEFORE_13["vault_publications"],
        ]
        assert _rebuilt_rows(saved) == rows
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
            )
        ] == _CURRENT_SCHEMA_NAMES
        # The same objects a fresh store has, autoindexes included, under the same names.
        assert _schema_thirteen_objects(connection) == objects
        # Every column of every row, the sealed body included, comes across unchanged.
        assert _rebuilt_rows(connection) == rows
        assert connection.execute("SELECT count(*) FROM memory_reviews").fetchone() == (0,)
        # The copied rows now take the two widened bounds.
        connection.execute(
            "UPDATE vault_publications SET stage_bytes=? WHERE operation_id=?", (20 << 20, SID)
        )
        connection.execute(
            "UPDATE vault_proposals SET origin='memory_promotion' WHERE proposal_id=?", (OTHER,)
        )


@pytest.mark.parametrize("boundary", ["rename_proposals", "proposal_ddl", "memory_ddl", "COMMIT"])
def test_schema_twelve_migration_failure_rolls_back_and_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    database = _version_twelve(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        rows = _rebuilt_rows(source)
    before = database.read_bytes()
    stops = {
        # vault_publications is already rebuilt when this one is renamed.
        "rename_proposals": "ALTER TABLE vault_proposals RENAME TO vault_proposals_v12",
        "proposal_ddl": storage.DECISION_SCHEMAS["vault_proposals"],
        "memory_ddl": storage.MEMORY_SCHEMAS["memory_reviews"],
    }
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql == "COMMIT" and boundary == "COMMIT":
                raise sqlite3.OperationalError("synthetic failure")
            result = super().execute(sql, parameters)
            if sql == stops.get(boundary):
                raise sqlite3.OperationalError("synthetic failure")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert database.read_bytes() == before
    (backup,) = tmp_path.glob("app.sqlite3.v12-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (12,)
        assert _rebuilt_rows(saved) == rows

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID).model == "preserved-v12-model"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert _rebuilt_rows(connection) == rows


@pytest.mark.parametrize("table", ["vault_publications", "vault_proposals"])
def test_schema_thirteen_compares_the_row_counts_it_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str
) -> None:
    """The step compares the row count of both tables it rebuilds. With one copy dropped, the
    only thing standing between a silently emptied journal or proposal list and a store that
    reads `ok` is that comparison: without the table in _migrate_step's counts this migration
    commits and the rows are gone."""
    database = _version_twelve(tmp_path)
    before = database.read_bytes()
    original = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.startswith(f"INSERT INTO {table} ("):
                return super().execute("SELECT 1")  # the copy the step believed it made
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: original(*a, factory=Connection, **kw))
        with pytest.raises(StateError) as failure:
            AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "corrupt"
    assert database.read_bytes() == before


def test_validate_expects_legacy_binding_after_drafts_in_name_order(tmp_path: Path) -> None:
    accepted = tmp_path / "accepted"
    accepted.mkdir()
    _build_schema_five(accepted / "app.sqlite3", _SCHEMA_FIVE_REVERSED)
    # Creation order is not comparison order: sqlite_schema is read sorted by name.
    assert AppState(accepted / "state.json", Prefs()).store.get_settings() is None

    missing = tmp_path / "missing-index"
    missing.mkdir()
    _build_schema_five(
        missing / "app.sqlite3",
        tuple(ddl for ddl in _SCHEMA_FIVE_REVERSED if ddl != storage._WORKSPACE_ACTIVE_INDEX),
    )
    with pytest.raises(StateError) as failure:
        AppState(missing / "state.json", Prefs())
    assert failure.value.code == "incompatible"

    extra = tmp_path / "extra-column"
    extra.mkdir()
    _build_schema_five(extra / "app.sqlite3", _SCHEMA_FIVE_REVERSED)
    with contextlib.closing(sqlite3.connect(extra / "app.sqlite3")) as connection:
        connection.execute("ALTER TABLE settings ADD COLUMN surprise TEXT")
        connection.commit()
    with pytest.raises(StateError) as failure:
        AppState(extra / "state.json", Prefs())
    assert failure.value.code == "incompatible"


def test_update_settings_runs_the_callback_in_the_writer_transaction_and_prunes_receipts(
    tmp_path: Path,
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    store = state.store
    command = "00000000-0000-4000-8000-00000000aaaa"

    def reserved(current: Any, existing: Any) -> tuple[dict[str, Any], str]:
        assert current is None and existing is None
        with (
            contextlib.closing(
                sqlite3.connect(state.database_path, isolation_level=None, timeout=0.025)
            ) as other,
            pytest.raises(sqlite3.OperationalError),
        ):
            other.execute("BEGIN IMMEDIATE")  # The writer reservation is already held.
        raise RuntimeError("synthetic policy refusal")

    with pytest.raises(RuntimeError):
        store.update_settings(command, reserved)
    assert store.get_settings() is None
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("SELECT count(*) FROM setting_commands").fetchone() == (0,)
        # A full receipt window, written directly so the test never runs 1,024 transactions.
        connection.executemany(
            "INSERT INTO setting_commands VALUES (?,?,?,?)",
            [
                (
                    f"00000000-0000-4000-8000-{index:012d}",
                    1,
                    "0" * 64,
                    f"2026-09-17T00:{index // 60:02d}:{index % 60:02d}.000Z",
                )
                for index in range(storage._SETTING_COMMAND_LIMIT)
            ],
        )
        connection.commit()

    row, receipt = store.update_settings(
        command, lambda current, existing: (_settings(1, "2026-09-18T00:00:00.000Z"), "b" * 64)
    )
    assert row == _settings(1, "2026-09-18T00:00:00.000Z")
    assert receipt == {
        "command_id": command,
        "revision": 1,
        "patch_hash": "b" * 64,
        "applied_at": "2026-09-18T00:00:00.000Z",
    }
    assert store.get_settings() == row
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("SELECT count(*) FROM setting_commands").fetchone() == (
            storage._SETTING_COMMAND_LIMIT,
        )
        assert connection.execute(
            "SELECT count(*) FROM setting_commands WHERE command_id=?",
            ("00000000-0000-4000-8000-000000000000",),
        ).fetchone() == (0,)  # the oldest receipt is the one pruned
        assert connection.execute(
            "SELECT count(*) FROM setting_commands WHERE command_id=?", (command,)
        ).fetchone() == (1,)

    # A replayed command_id keeps the committed receipt instead of writing a second one.
    replayed = store.update_settings(command, lambda current, existing: (_settings(2), "c" * 64))
    assert replayed[1]["patch_hash"] == "b" * 64
    assert replayed[0] == row  # the document is never rewritten by a stale replay
    assert store.get_settings() == row


def test_update_settings_replay_runs_the_callback_but_never_rewrites_a_later_document(
    tmp_path: Path,
) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    command = "00000000-0000-4000-8000-00000000dddd"
    first, receipt = store.update_settings(
        command, lambda current, existing: (_settings(1), "e" * 64)
    )
    assert first == _settings(1) and receipt["revision"] == 1

    # A later, unrelated command moves the document on; the store now holds revision 7.
    store.update_settings(
        "00000000-0000-4000-8000-00000000dfff", lambda current, existing: (_settings(7), "f" * 64)
    )

    seen: list[tuple[Any, Any]] = []

    def replay(current: Any, existing: Any) -> tuple[dict[str, Any], str]:
        seen.append((current, existing))
        return _settings(99), "e" * 64  # what the caller recomputes must be discarded

    row, replayed_receipt = store.update_settings(command, replay)
    assert seen == [(_settings(7), receipt)]  # the callback still ran, and saw the real document
    assert row == _settings(7)  # row@7 stays; a stale replay never rewinds or rewrites it
    assert replayed_receipt == receipt  # receipt@1 comes back alongside it
    assert store.get_settings() == _settings(7)


def test_update_settings_prune_never_deletes_the_receipt_just_inserted(tmp_path: Path) -> None:
    """1,024 identical stamps: a naive tie-break can rank the fresh receipt last and prune it."""
    state = AppState(tmp_path / "state.json", Prefs())
    store = state.store
    stamp = "2026-09-17T00:00:00.000Z"
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.executemany(
            "INSERT INTO setting_commands VALUES (?,?,?,?)",
            [
                (f"00000000-0000-4000-8000-{index:012d}", 1, "0" * 64, stamp)
                for index in range(1, storage._SETTING_COMMAND_LIMIT + 1)
            ],
        )
        connection.commit()
    # Sorts before every id already in the table, so a command_id DESC tie-break would rank
    # this receipt last once the table holds one more row than the retained window.
    command = "00000000-0000-4000-8000-000000000000"
    _, receipt = store.update_settings(
        command, lambda current, existing: (_settings(1, stamp), "e" * 64)
    )
    assert receipt["command_id"] == command
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute("SELECT count(*) FROM setting_commands").fetchone() == (
            storage._SETTING_COMMAND_LIMIT,
        )
        assert connection.execute(
            "SELECT count(*) FROM setting_commands WHERE command_id=?", (command,)
        ).fetchone() == (1,)  # the fresh receipt survives an all-tied prune window
        assert connection.execute(
            "SELECT count(*) FROM setting_commands WHERE command_id=?",
            ("00000000-0000-4000-8000-000000000001",),
        ).fetchone() == (0,)  # the oldest of the tied "others" is the one actually pruned


def test_delete_settings_guard_refuses_before_any_write(tmp_path: Path) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    store.update_settings(
        "00000000-0000-4000-8000-00000000bbbb", lambda current, existing: (_settings(1), "d" * 64)
    )

    def refuse(current: Any) -> None:
        assert current == _settings(1)
        raise RuntimeError("read-only document")

    with pytest.raises(RuntimeError):
        store.delete_settings(refuse)
    assert store.get_settings() == _settings(1)
    store.delete_settings(lambda current: None)
    assert store.get_settings() is None


def test_delete_settings_clears_setting_commands_so_a_stale_command_id_applies_fresh(
    tmp_path: Path,
) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    command = "00000000-0000-4000-8000-00000000eeee"
    store.update_settings(command, lambda current, existing: (_settings(1), "f" * 64))
    store.delete_settings(lambda current: None)

    seen: list[Any] = []

    def fresh(current: Any, existing: Any) -> tuple[dict[str, Any], str]:
        seen.append(existing)
        return _settings(1), "f" * 64

    row, receipt = store.update_settings(command, fresh)
    assert seen == [None]  # not answered from the receipt the deleted document left behind
    assert row == _settings(1) and receipt["revision"] == 1


def test_record_legacy_binding_once_keeps_the_first_row(tmp_path: Path) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    assert store.get_legacy_binding() is None
    first = store.record_legacy_binding_once("bound", _STAMP, workspace_id=SID)
    assert first["result"] == "bound" and first["workspace_id"] == SID and first["code"] is None
    # The caller's stamp is used verbatim; the store keeps no timestamp source of its own.
    assert first["recorded_at"] == _STAMP
    again = store.record_legacy_binding_once(
        "unusable", "2099-01-01T00:00:00.000Z", code="unsafe_path"
    )
    assert again == first == store.get_legacy_binding()


def test_update_workspace_callback_sees_every_row_and_none_deletes(tmp_path: Path) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    store.update_workspace(SID, lambda current, rows: _workspace(SID))
    seen: list[list[str]] = []

    def register(current: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
        assert current is None
        seen.append([row["workspace_id"] for row in rows])
        return _workspace(OTHER, name="second")

    store.update_workspace(OTHER, register)
    assert seen == [[SID]]
    assert [row["workspace_id"] for row in store.list_workspaces()] == [SID, OTHER]
    second = store.get_workspace(OTHER)
    assert second is not None and second["name"] == "second"

    def refuse(current: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
        raise RuntimeError("identity mismatch")

    with pytest.raises(RuntimeError):
        store.update_workspace(SID, refuse)
    assert len(store.list_workspaces()) == 2
    assert store.update_workspace(SID, lambda current, rows: None) is None
    assert [row["workspace_id"] for row in store.list_workspaces()] == [OTHER]


def test_update_workspace_refuses_a_row_whose_id_does_not_match_the_argument(
    tmp_path: Path,
) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    with pytest.raises(StateError) as failure:
        store.update_workspace(SID, lambda current, rows: _workspace(OTHER))
    assert failure.value.code == "unavailable"
    assert store.list_workspaces() == []  # nothing written under either id


def test_update_workspace_freezes_identity_while_publication_recovery_is_live(
    tmp_path: Path,
) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    original = store.update_workspace(SID, lambda current, rows: _workspace(SID))
    other = store.update_workspace(OTHER, lambda current, rows: _workspace(OTHER))
    assert original is not None
    assert other is not None
    operation_id = "00000000-0000-4000-8000-000000000201"
    with store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO vault_publications VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                operation_id,
                SID,
                original["physical_id"],
                original["policy_revision"],
                "note.md",
                "create",
                f".viki-stage-{operation_id}.tmp",
                f".viki-backup-{operation_id}.tmp",
                None,
                "0" * 64,
                3,
                None,
                None,
                None,
                "reserved",
                _STAMP,
                _STAMP,
            ),
        )

    # A no-op callback remains usable for callers re-reading the current registration.
    assert store.update_workspace(SID, lambda current, rows: current) == original

    def changed(current: storage.WorkspaceRow | None, **values: object) -> storage.WorkspaceRow:
        assert current is not None
        return cast(storage.WorkspaceRow, {**current, **values})

    changes = (
        lambda current: None,
        lambda current: changed(current, policy="protected", policy_revision=2),
        lambda current: changed(
            current,
            canonical_key="c:\\synthetic\\moved",
            display_root="C:\\Synthetic\\moved",
            physical_id=f"2:{SID}",
            revision=2,
        ),
    )
    for change in changes:
        with pytest.raises(StateError) as failure:
            store.update_workspace(SID, lambda current, rows, apply=change: apply(current))
        assert failure.value.code == "recovery_required"
        assert store.get_workspace(SID) == original

    # A different row can become part of the protected overlap/identity boundary. Freeze it too
    # until the post-reservation admission proof has either published or recovered.
    with pytest.raises(StateError) as failure:
        store.update_workspace(
            OTHER,
            lambda current, rows: changed(
                current, policy="protected", policy_revision=2, revision=2
            ),
        )
    assert failure.value.code == "recovery_required"
    assert store.get_workspace(OTHER) == other

    # Recovery has verified and removed its durable row, so ordinary metadata changes resume.
    with store._connection(write=True) as connection:
        assert (
            connection.execute(
                "DELETE FROM vault_publications WHERE operation_id=?", (operation_id,)
            ).rowcount
            == 1
        )
    updated = store.update_workspace(
        SID,
        lambda current, rows: changed(current, name="renamed", revision=2),
    )
    assert updated is not None and updated["name"] == "renamed" and updated["revision"] == 2
    promoted = store.update_workspace(
        OTHER,
        lambda current, rows: changed(current, policy="protected", policy_revision=2, revision=2),
    )
    assert promoted is not None and promoted["policy"] == "protected"


def test_update_workspace_freezes_identity_while_a_vault_operation_is_live(
    tmp_path: Path,
) -> None:
    """A lifecycle operation holds the same safety boundary a publication does: it was admitted
    against this root and policy revision, and its steps are mid-flight on those bytes."""
    store = AppState(tmp_path / "state.json", Prefs()).store
    original = store.update_workspace(SID, lambda current, rows: _workspace(SID))
    assert original is not None
    with store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO vault_operations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                _OPERATION_ID,
                SID,
                original["physical_id"],
                original["policy_revision"],
                "move",
                _HASH,
                _STEPS,
                "applying",
                _STAMP,
                _STAMP,
            ),
        )

    def changed(current: storage.WorkspaceRow | None, **values: object) -> storage.WorkspaceRow:
        assert current is not None
        return cast(storage.WorkspaceRow, {**current, **values})

    # A no-op callback remains usable for callers re-reading the current registration.
    assert store.update_workspace(SID, lambda current, rows: current) == original
    for change in (
        lambda current: None,
        lambda current: changed(current, policy="protected", policy_revision=2),
        lambda current: changed(current, name="renamed", revision=2),
    ):
        with pytest.raises(StateError) as failure:
            store.update_workspace(SID, lambda current, rows, apply=change: apply(current))
        assert failure.value.code == "recovery_required"
        assert store.get_workspace(SID) == original
    # A registration this operation never named is frozen too: it can be part of the overlap.
    with pytest.raises(StateError) as failure:
        store.update_workspace(OTHER, lambda current, rows: _workspace(OTHER))
    assert failure.value.code == "recovery_required"
    assert store.get_workspace(OTHER) is None

    # Reconciliation has finished the operation and removed its row, so changes resume.
    with store._connection(write=True) as connection:
        connection.execute("DELETE FROM vault_operations WHERE operation_id=?", (_OPERATION_ID,))
    renamed = store.update_workspace(SID, lambda current, rows: changed(current, name="renamed"))
    assert renamed is not None and renamed["name"] == "renamed"


def test_select_workspace_clears_then_sets_in_one_transaction(tmp_path: Path) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    store.update_workspace(SID, lambda current, rows: _workspace(SID, active=1))
    store.update_workspace(OTHER, lambda current, rows: _workspace(OTHER))
    store.select_workspace(OTHER)
    assert [row["workspace_id"] for row in store.list_workspaces() if row["active"]] == [OTHER]
    store.select_workspace(SID)
    assert [row["workspace_id"] for row in store.list_workspaces() if row["active"]] == [SID]
    store.select_workspace(None)
    assert [row["workspace_id"] for row in store.list_workspaces() if row["active"]] == []


def test_select_workspace_survives_an_unknown_id_instead_of_clearing_selection(
    tmp_path: Path,
) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    store.update_workspace(SID, lambda current, rows: _workspace(SID, active=1))
    ghost = "00000000-0000-4000-8000-00000000ffff"  # well-formed, but never registered
    store.select_workspace(ghost)
    assert [row["workspace_id"] for row in store.list_workspaces() if row["active"]] == [SID]


def test_constraint_violation_maps_to_invalid_input(tmp_path: Path) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    with pytest.raises(StateError) as failure:
        store.update_workspace(SID, lambda current, rows: _workspace(SID, policy="banana"))
    assert failure.value.code == "invalid_input"

    store.update_workspace(SID, lambda current, rows: _workspace(SID, active=1))
    with pytest.raises(StateError) as failure:
        store.update_workspace(OTHER, lambda current, rows: _workspace(OTHER, active=1))
    assert failure.value.code == "invalid_input"


def test_migrate_loop_is_bounded_instead_of_spinning_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "app.sqlite3"
    _version_one(database)

    def stall(self: Any, connection: Any, version: int) -> None:
        pass  # never advances user_version, as a migration step that keeps failing would

    monkeypatch.setattr(storage.SessionStore, "_migrate_step", stall)
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "corrupt"


def test_delete_scope_rows_returns_the_count_and_touches_one_scope(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    asset_id = "00000000-0000-4000-8000-000000000099"
    state.store.put_asset(
        {
            "scope": _SCOPE,
            "asset_id": asset_id,
            "media_type": "image/png",
            "byte_size": 1,
            "sha256": "0" * 64,
            "width": 1,
            "height": 1,
            "created_at": _STAMP,
        }
    )
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.executemany(
            "INSERT INTO drafts VALUES (?,?,1,?,1,3,?)",
            [
                (scope, key, b"xyz", _STAMP)
                for scope in (_SCOPE, _OTHER_SCOPE)
                for key in ("a", "b")
            ],
        )
        connection.commit()
    state.store.replace_asset_refs(_SCOPE, "draft", "a", ((asset_id, "attachment.0"),), _STAMP)
    assert state.store.delete_scope_rows("drafts", _SCOPE, OURS) == (2, 0)
    assert state.store.delete_scope_rows("drafts", _SCOPE, OURS) == (0, 0)
    assert len(state.store.list_drafts(_OTHER_SCOPE, "", 10)) == 2
    assert state.store.delete_asset_if_unreferenced(_SCOPE, asset_id)


def test_delete_scope_rows_refuses_a_table_it_does_not_own(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    # "settings" is the witness because it *has* a scope column: a missing guard would run a
    # successful DELETE and answer (0, 0) rather than failing on "no such column", which is
    # exactly the silent failure the guard exists to prevent.
    with pytest.raises(StateError) as failure:
        state.store.delete_scope_rows("settings", _SCOPE, OURS)  # type: ignore[arg-type]
    assert failure.value.code == "unavailable"


def test_delete_scope_rows_refuses_a_malformed_owner_without_deleting(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    _seed_commands(state.database_path, [(_SCOPE, "accepted", "", OURS)])
    _seed_approvals(state.database_path, [(_SCOPE, 1, OURS, "recorded", _STAMP, b"x")])
    for table in ("commands", "approvals"):
        with pytest.raises(StateError) as failure:
            state.store.delete_scope_rows(table, _SCOPE, "")  # type: ignore[arg-type]
        assert failure.value.code == "invalid_input"
    # An owner that matches no row would widen both DELETEs back over the live rows the
    # narrowing exists to keep, so the raise alone would not prove anything.
    assert _count(state.database_path, "commands", _SCOPE) == 1
    assert _count(state.database_path, "approvals", _SCOPE) == 1


def test_delete_scope_rows_keeps_every_approval_this_launch_recorded(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    _seed_approvals(
        state.database_path,
        [
            (_SCOPE, 1, OURS, "recorded", _STAMP, b"x"),
            (_SCOPE, 2, OURS, "delivered_locally", _STAMP, b"x"),
            # A row finalized `not_delivered` deliberately leaves its request
            # unanswerable until the turn is stopped. Deleting it would let a second choice be
            # recorded for that request, and delivered if the card is still pending.
            (_SCOPE, 3, OURS, "not_delivered", _STAMP, b"x"),
            (_SCOPE, 4, OURS, "unknown", _STAMP, b"x"),
            (_SCOPE, 5, DEAD, "recorded", _STAMP, b"x"),
            (_SCOPE, 6, DEAD, "delivered_locally", _STAMP, b"x"),
            (_OTHER_SCOPE, 7, DEAD, "delivered_locally", _STAMP, b"x"),
        ],
    )
    assert state.store.delete_scope_rows("approvals", _SCOPE, OURS) == (2, 4)
    assert state.store.delete_scope_rows("approvals", _SCOPE, OURS) == (0, 4)
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert (
            connection.execute(
                "SELECT owner FROM approvals WHERE scope=? ORDER BY choice_id", (_SCOPE,)
            ).fetchall()
            == [(OURS,)] * 4
        )
    assert _count(state.database_path, "approvals", _OTHER_SCOPE) == 1


def test_delete_scope_rows_keeps_commands_whose_turn_is_unfinished(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    _seed_commands(
        state.database_path,
        [
            (_SCOPE, "accepted", "", OURS),
            (_SCOPE, "dispatching", "", OURS),
            (_SCOPE, "unknown", "internal", OURS),
            (_SCOPE, "not_dispatched", "cancelled", OURS),
            # A crashed launch's in-flight rows: unfinished, but nothing can ever finish them.
            (_SCOPE, "accepted", "", DEAD),
            (_SCOPE, "dispatching", "", DEAD),
            (_OTHER_SCOPE, "accepted", "", OURS),
        ],
    )
    asset_ids = (
        "00000000-0000-4000-8000-000000000071",
        "00000000-0000-4000-8000-000000000072",
        "00000000-0000-4000-8000-000000000073",
    )
    for asset_id in asset_ids:
        state.store.put_asset(
            {
                "scope": _SCOPE,
                "asset_id": asset_id,
                "media_type": "image/png",
                "byte_size": 1,
                "sha256": asset_id[-1] * 64,
                "width": 1,
                "height": 1,
                "created_at": _STAMP,
            }
        )
    # The first row is live for this launch, while the third and fifth rows are reset-deleted.
    for index, asset_id in ((1, asset_ids[0]), (3, asset_ids[1]), (5, asset_ids[2])):
        state.store.replace_asset_refs(
            _SCOPE,
            "command",
            f"00000000-0000-4000-8000-{index:012d}",
            ((asset_id, "attachment.0"),),
            _STAMP,
        )
    # An admitted turn is the one row a reset must not take: the browser holds its receipt and
    # has cleared the draft, so the `dispatching` write still to come is all that sends it.
    assert state.store.delete_scope_rows("commands", _SCOPE, OURS) == (4, 2)
    assert state.store.delete_scope_rows("commands", _SCOPE, OURS) == (0, 2)
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        assert connection.execute(
            "SELECT state, owner FROM commands WHERE scope=? ORDER BY command_id", (_SCOPE,)
        ).fetchall() == [("accepted", OURS), ("dispatching", OURS)]
    assert _count(state.database_path, "commands", _OTHER_SCOPE) == 1
    assert not state.store.delete_asset_if_unreferenced(_SCOPE, asset_ids[0])
    assert state.store.delete_asset_if_unreferenced(_SCOPE, asset_ids[1])
    assert state.store.delete_asset_if_unreferenced(_SCOPE, asset_ids[2])


def test_asset_command_retention_tombstones_only_old_command_owned_blobs(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    asset_id = "00000000-0000-4000-8000-000000000080"
    state.store.put_asset(
        {
            "scope": _SCOPE,
            "asset_id": asset_id,
            "media_type": "image/png",
            "byte_size": 7,
            "sha256": "0" * 64,
            "width": 1,
            "height": 1,
            "created_at": _stamp(1),
        }
    )
    first = "00000000-0000-4000-8000-000000000081"
    second = "00000000-0000-4000-8000-000000000082"
    state.store.replace_asset_refs(
        _SCOPE, "command", first, ((asset_id, "attachment.0"),), _stamp(2)
    )
    assert [
        row["asset_id"] for row in state.store.list_stored_command_assets(_SCOPE, _stamp(3), 10)
    ] == [asset_id]
    # A draft still owns the blob, so completed-command retention may not expire it.
    state.store.replace_asset_refs(_SCOPE, "draft", SID, ((asset_id, "attachment.0"),), _stamp(2))
    assert not state.store.expire_asset_if_due(_SCOPE, asset_id, _stamp(3), _stamp(4))
    state.store.replace_asset_refs(_SCOPE, "draft", SID, (), _stamp(3))
    # A newer admitted command resets the eligible age for the complete asset set.
    state.store.replace_asset_refs(
        _SCOPE, "command", second, ((asset_id, "attachment.0"),), _stamp(3)
    )
    assert not state.store.expire_asset_if_due(_SCOPE, asset_id, _stamp(3), _stamp(4))
    assert state.store.expire_asset_if_due(_SCOPE, asset_id, _stamp(4), _stamp(5))
    row = state.store.asset_metadata(_SCOPE, (asset_id,))[0]
    assert row["local_state"] == "expired" and row["local_removed_at"] == _stamp(5)
    assert state.store.asset_usage(_SCOPE) == (0, 0)


_FIELDS = ",".join(f"a.{field}" for field in storage._ASSET_FIELDS)
# The queries before the one-grouped-read rewrite, verbatim: correlated subqueries that
# rescan the scope's refs per asset.
_OLD_UNREFERENCED = (
    f"SELECT {_FIELDS} "
    "FROM assets a WHERE a.scope=? AND a.created_at<? AND NOT EXISTS "
    "(SELECT 1 FROM asset_refs r WHERE r.scope=a.scope AND r.asset_id=a.asset_id) "
    "ORDER BY a.created_at,a.asset_id LIMIT ?"
)
_OLD_STORED_COMMAND = (
    f"SELECT {_FIELDS} FROM assets a "
    "WHERE a.scope=? AND a.local_state='stored' AND a.created_at<? "
    "AND EXISTS (SELECT 1 FROM asset_refs r WHERE r.scope=a.scope "
    "AND r.asset_id=a.asset_id AND r.owner_kind='command') "
    "AND NOT EXISTS (SELECT 1 FROM asset_refs r WHERE r.scope=a.scope "
    "AND r.asset_id=a.asset_id AND r.owner_kind<>'command') "
    "AND NOT EXISTS (SELECT 1 FROM asset_refs r WHERE r.scope=a.scope "
    "AND r.asset_id=a.asset_id AND r.owner_kind='command' AND r.created_at>=?) "
    "ORDER BY a.created_at,a.asset_id LIMIT ?"
)


def test_asset_retention_queries_match_the_per_asset_oracle(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    # Per asset: no refs; command only; draft and command; queue only; queue and command;
    # two commands. Ref ages, asset ages and stored/expired vary independently of the kind.
    kinds = (
        (),
        ("command",),
        ("draft", "command"),
        ("queue",),
        ("queue", "command"),
        ("command", "command"),
    )
    assets: list[tuple[Any, ...]] = []
    refs: list[tuple[str, str, str, str, str, str]] = []
    for scope in (_SCOPE, _OTHER_SCOPE):
        for index in range(72):
            asset_id = f"00000000-0000-4000-8000-{index:012d}"
            expired = index % 7 == 0
            assets.append(
                (scope, asset_id, "image/png", 1, "0" * 64, 1, 1, _stamp(index * 11 % 60))
                + (("expired", _stamp(59)) if expired else ("stored", None))
            )
            # The other scope shares asset ids but shifts the kinds, so a scope leak shows.
            for role, kind in enumerate(kinds[(index + (scope == _OTHER_SCOPE)) % len(kinds)]):
                refs.append(
                    (scope, asset_id, kind, f"{kind}-{index}-{role}", f"attachment.{role}")
                    + (_stamp((index * 7 + role * 29) % 60),)
                )
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.executemany(f"INSERT INTO assets VALUES ({','.join('?' * 10)})", assets)
        connection.executemany("INSERT INTO asset_refs VALUES (?,?,?,?,?,?)", refs)
        connection.commit()
    stored = unreferenced = 0
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:

        def oracle(sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
            rows = connection.execute(sql, args).fetchall()
            return [dict(zip(storage._ASSET_FIELDS, row, strict=True)) for row in rows]

        for second in range(60):
            for limit in (1, 4, 100):
                before = _stamp(second)
                old = oracle(_OLD_STORED_COMMAND, (_SCOPE, before, before, limit))
                assert state.store.list_stored_command_assets(_SCOPE, before, limit) == old
                stored += len(old)
                old = oracle(_OLD_UNREFERENCED, (_SCOPE, before, limit))
                assert state.store.list_unreferenced_assets(_SCOPE, before, limit) == old
                unreferenced += len(old)
    # The oracle selected real rows, so the equality above is not vacuous.
    assert stored > 20 and unreferenced > 20


def test_conversation_archives_are_scoped_bounded_and_return_no_ciphertext_in_list(
    tmp_path: Path,
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    archive_id = "00000000-0000-4000-8000-000000000083"
    row = {
        "archive_id": archive_id,
        "created_at": _stamp(6),
        "body": b"private synthetic archive",
        "byte_size": len(b"private synthetic archive"),
    }
    state.store.insert_conversation_archive(_SCOPE, row)
    assert state.store.list_conversation_archives(_SCOPE) == [
        {"archive_id": archive_id, "created_at": _stamp(6), "byte_size": row["byte_size"]}
    ]
    assert state.store.get_conversation_archive(_SCOPE, archive_id) == row
    assert state.store.get_conversation_archive(_OTHER_SCOPE, archive_id) is None
    assert state.store.remove_conversation_archive(_SCOPE, archive_id)
    assert not state.store.remove_conversation_archive(_SCOPE, archive_id)


def test_the_65th_conversation_archive_is_a_typed_capacity_refusal(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    body = b"synthetic archive"
    for index in range(64):
        state.store.insert_conversation_archive(
            _SCOPE,
            {"archive_id": _choice(index), "created_at": _STAMP, "body": body, "byte_size": 1},
        )
    with pytest.raises(StateError) as failure:
        state.store.insert_conversation_archive(
            _SCOPE, {"archive_id": _choice(64), "created_at": _STAMP, "body": body, "byte_size": 1}
        )
    assert failure.value.code == "capacity" and failure.value.message
    assert _count(state.database_path, "conversation_archives", _SCOPE) == 64


def _label(session_id: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "revision": 1,
        "title": "t",
        "pinned": 1,
        "archived": 0,
        "unread": 0,
        "color": None,
    }


def test_delete_session_metadata_runs_the_guard_before_deleting(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    state.store.update_session_metadata(_SCOPE, SID, lambda _: _label(SID))
    seen: list[dict[str, Any] | None] = []

    def refuse(row: dict[str, Any] | None) -> None:
        seen.append(row)
        raise StateError("conflict")

    with pytest.raises(StateError) as failure:
        state.store.delete_session_metadata(_SCOPE, SID, refuse)
    assert failure.value.code == "conflict"
    assert seen == [_label(SID)]
    assert state.store.get_session_metadata(_SCOPE, SID) == _label(SID)
    assert not state.store.delete_session_metadata(_OTHER_SCOPE, SID, seen.append)
    assert seen[-1] is None
    assert state.store.delete_session_metadata(_SCOPE, SID, seen.append)
    assert state.store.get_session_metadata(_SCOPE, SID) is None
    assert not state.store.delete_session_metadata(_SCOPE, SID, seen.append)


def test_delete_session_metadata_keeps_the_guarded_read_inside_its_own_write_transaction(
    tmp_path: Path,
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    state.store.update_session_metadata(_SCOPE, SID, lambda _: _label(SID))

    def guard(_: dict[str, Any] | None) -> None:
        # The delete's own BEGIN IMMEDIATE is still open here, so a second writer on the
        # same row cannot acquire its lock before the store's busy timeout.
        with pytest.raises(StateError) as failure:
            state.store.update_session_metadata(_SCOPE, SID, lambda _: _label(SID))
        assert failure.value.code == "busy"

    assert state.store.delete_session_metadata(_SCOPE, SID, guard)
    assert state.store.get_session_metadata(_SCOPE, SID) is None


def test_session_metadata_holds_exactly_its_cap_per_scope(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    cap = storage.MAX_SESSION_METADATA
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.executemany(
            "INSERT INTO session_metadata "
            "(scope,session_id,revision,title,pinned,archived) VALUES (?,?,1,'',0,0)",
            [(_SCOPE, _choice(index)) for index in range(cap)],
        )
        connection.commit()
    assert len(state.store.list_session_metadata(_SCOPE)) == cap
    with pytest.raises(StateError) as failure:
        state.store.update_session_metadata(_SCOPE, SID, lambda _: _label(SID))
    assert failure.value.code == "capacity"
    # An existing label still updates at the cap, and another scope has its own.
    state.store.update_session_metadata(_SCOPE, _choice(0), lambda _: _label(_choice(0)))
    state.store.update_session_metadata(_OTHER_SCOPE, SID, lambda _: _label(SID))
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO session_metadata "
            "(scope,session_id,revision,title,pinned,archived) VALUES (?,?,1,'',0,0)",
            (_SCOPE, SID),
        )
        connection.commit()
    with pytest.raises(StateError) as failure:
        state.store.list_session_metadata(_SCOPE)
    assert failure.value.code == "corrupt"


def _asset(scope: str, index: int, **overrides: Any) -> dict[str, Any]:
    return {
        "scope": scope,
        "asset_id": _choice(index),
        "media_type": "image/png",
        "byte_size": 7,
        "sha256": "0" * 64,
        "width": 2,
        "height": 3,
        "created_at": _stamp(1),
        **overrides,
    }


def test_asset_scopes_pages_distinct_scopes_after_a_cursor(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    assert state.store.asset_scopes("", 100) == []
    third = "c" * 64
    for index, scope in enumerate((third, _SCOPE, _OTHER_SCOPE, _SCOPE)):
        state.store.put_asset(_asset(scope, index))
    assert state.store.asset_scopes("", 100) == [_SCOPE, _OTHER_SCOPE, third]
    assert state.store.asset_scopes("", 2) == [_SCOPE, _OTHER_SCOPE]
    assert state.store.asset_scopes(_OTHER_SCOPE, 2) == [third]
    assert state.store.asset_scopes(third, 1) == []
    for limit in (0, 101):
        with pytest.raises(StateError) as failure:
            state.store.asset_scopes("", limit)
        assert failure.value.code == "invalid_input"


def test_restore_expired_asset_flips_only_a_matching_expired_row(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    row = _asset(_SCOPE, 1)
    state.store.put_asset(row)
    # A stored row is not restored.
    assert not state.store.restore_expired_asset(row, _stamp(9))
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "UPDATE assets SET local_state='expired',local_removed_at=? WHERE scope=?",
            (_stamp(5), _SCOPE),
        )
        connection.commit()
    for field, value in (
        ("media_type", "image/jpeg"),
        ("byte_size", 8),
        ("sha256", "1" * 64),
        ("width", 3),
        ("height", 2),
        ("scope", _OTHER_SCOPE),
    ):
        assert not state.store.restore_expired_asset({**row, field: value}, _stamp(9))
    for stamp in (_stamp(9)[:-1], ""):
        with pytest.raises(StateError) as failure:
            state.store.restore_expired_asset(row, stamp)
        assert failure.value.code == "invalid_input"
    assert state.store.asset_metadata(_SCOPE, (row["asset_id"],))[0]["local_state"] == "expired"
    assert state.store.restore_expired_asset(row, _stamp(9))
    assert state.store.asset_metadata(_SCOPE, (row["asset_id"],)) == [
        {**row, "created_at": _stamp(9), "local_state": "stored", "local_removed_at": None}
    ]
    assert not state.store.restore_expired_asset(row, _stamp(9))


def test_restore_expired_asset_leaves_a_second_matching_row_expired(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    row = _asset(_SCOPE, 1)
    twin = _asset(_SCOPE, 2)
    state.store.put_asset(row)
    state.store.put_asset(twin)
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "UPDATE assets SET local_state='expired',local_removed_at=? WHERE scope=?",
            (_stamp(5), _SCOPE),
        )
        connection.commit()
    assert state.store.restore_expired_asset(row, _stamp(9))
    rows = {
        item["asset_id"]: item
        for item in state.store.asset_metadata(_SCOPE, (row["asset_id"], twin["asset_id"]))
    }
    assert rows[row["asset_id"]]["local_state"] == "stored"
    assert rows[twin["asset_id"]]["local_state"] == "expired"


def test_delete_asset_if_unreferenced_created_before_keeps_a_row_a_restore_made_fresh(
    tmp_path: Path,
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    row = _asset(_SCOPE, 1)
    state.store.put_asset(row)
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "UPDATE assets SET local_state='expired',local_removed_at=? WHERE scope=?",
            (_stamp(2), _SCOPE),
        )
        connection.commit()
    cutoff = _stamp(5)
    listed = state.store.list_unreferenced_assets(_SCOPE, cutoff, 10)
    assert [item["asset_id"] for item in listed] == [row["asset_id"]]
    # A relink restores the row between the listing and the pass's delete.
    assert state.store.restore_expired_asset(row, _stamp(9))
    assert not state.store.delete_asset_if_unreferenced(
        _SCOPE, row["asset_id"], created_before=cutoff
    )
    assert state.store.asset_metadata(_SCOPE, (row["asset_id"],))[0]["local_state"] == "stored"


def test_reclaim_approvals_takes_the_oldest_terminal_rows_of_another_launch(
    tmp_path: Path,
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    # choice_id ascending, created_at descending: ordering by id alone takes the newest row.
    _seed_approvals(
        state.database_path,
        [
            (_SCOPE, index, DEAD, "delivered_locally", _stamp(10 - index), b"x")
            for index in range(1, 6)
        ],
    )
    assert (
        state.store.reclaim_approvals(_SCOPE, OURS, max_rows=5, max_bytes=1 << 20, incoming=1) == 1
    )
    assert _choice_ids(state.database_path, _SCOPE) == [_choice(index) for index in (1, 2, 3, 4)]


def test_reclaim_approvals_frees_bytes_as_well_as_rows(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    _seed_approvals(
        state.database_path,
        [
            (_SCOPE, index, DEAD, "delivered_locally", _stamp(index), b"x" * 100)
            for index in range(1, 5)
        ],
    )
    # 400 stored bytes, 100 incoming, 300 allowed: the two oldest rows have to go, and the row
    # quota alone would free nothing.
    assert (
        state.store.reclaim_approvals(_SCOPE, OURS, max_rows=1000, max_bytes=300, incoming=100) == 2
    )
    assert _choice_ids(state.database_path, _SCOPE) == [_choice(3), _choice(4)]


def test_reclaim_approvals_never_takes_this_launch_s_rows_or_a_live_one(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    _seed_approvals(
        state.database_path,
        [
            (_SCOPE, 1, OURS, "delivered_locally", _stamp(1), b"x"),
            (_SCOPE, 2, OURS, "recorded", _stamp(2), b"x"),
            # A foreign nonterminal row is the only durable evidence of a choice whose delivery
            # was never observed; only the user's reset may take it.
            (_SCOPE, 3, DEAD, "recorded", _stamp(3), b"x"),
            (_SCOPE, 4, DEAD, "delivering", _stamp(4), b"x"),
        ],
    )
    assert state.store.reclaim_approvals(_SCOPE, OURS, max_rows=4, max_bytes=1, incoming=1) == 0
    assert _choice_ids(state.database_path, _SCOPE) == [_choice(index) for index in (1, 2, 3, 4)]


def test_reclaim_approvals_touches_one_scope(tmp_path: Path) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    _seed_approvals(
        state.database_path,
        [
            (_SCOPE, 1, DEAD, "delivered_locally", _stamp(1), b"x"),
            (_SCOPE, 2, DEAD, "delivered_locally", _stamp(2), b"x"),
            # choice_id _choice(1) again: legal, since the primary key is (scope, choice_id).
            # A reclaim scoped to _SCOPE must not reach this row of ours just because it shares
            # an id with the one _SCOPE row the reclaim is about to take.
            (_OTHER_SCOPE, 1, OURS, "recorded", _stamp(3), b"x"),
            (_OTHER_SCOPE, 4, DEAD, "delivered_locally", _stamp(4), b"x"),
        ],
    )
    assert (
        state.store.reclaim_approvals(_SCOPE, OURS, max_rows=2, max_bytes=1 << 20, incoming=1) == 1
    )
    assert _choice_ids(state.database_path, _SCOPE) == [_choice(2)]
    assert _choice_ids(state.database_path, _OTHER_SCOPE) == [_choice(1), _choice(4)]


def test_the_live_state_lists_match_the_services_that_own_them() -> None:
    """storage cannot import either service (both import storage), so the lists it narrows its
    DELETEs with are pinned here against the definitions their owners publish."""
    assert set(storage._LIVE_APPROVALS) == set(approvals._NONTERMINAL)
    assert set(storage._LIVE_APPROVALS) == set(get_args(approvals.ApprovalState)) - set(
        get_args(approvals.TerminalState)
    )
    assert set(storage._LIVE_COMMANDS) == set(get_args(commands.CommandState)) - set(
        get_args(commands.TerminalState)
    )


def test_scratch_workspace_id_is_its_persisted_literal() -> None:
    assert domain.SCRATCH_WORKSPACE_ID == "165b132f-7fcb-5c95-8c6c-86eff07bfe13"


def test_busy_timeout_matches_the_platform_budget() -> None:
    """Windows keeps its 25 ms busy budget byte for byte; POSIX gets 75 ms so a contended
    writer there waits about as long once timer rounding and fsync are accounted for."""
    assert (0.025 if sys.platform == "win32" else 0.075) == storage.BUSY_TIMEOUT_S


def test_every_store_connection_uses_the_platform_busy_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin above checks the constant; this checks every store connect actually passes it,
    over one create, one read and one write. The migration backup stage keeps SQLite's default
    and is not a store connection, so it is never exercised here."""
    real_connect = sqlite3.connect
    timeouts: list[object] = []

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        timeouts.append(kwargs.get("timeout"))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)

    state = AppState(tmp_path / "state.json", Prefs())  # one create, one read (validation)
    state.remember(SID, mode="plan")  # one write (its own validating read, then the rw connect)

    assert len(timeouts) == 4
    assert all(timeout == storage.BUSY_TIMEOUT_S for timeout in timeouts)
