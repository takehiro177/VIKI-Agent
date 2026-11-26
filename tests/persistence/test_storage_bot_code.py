"""Bot code metadata and launch provenance migrate atomically from the shipped v39 format."""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import _STAMP, _downgrade_bot_code, _workspace
from viki_agent import storage
from viki_agent.profiles.profile_launch import MAX_BINDINGS
from viki_agent.state import AppState, Prefs

_TABLES = (
    "bot_code_folders",
    "bot_code_copies",
    "bot_code_baselines",
    *storage.BOT_USAGE_SCHEMAS,
    *storage.INBOX_SUGGESTION_SCHEMAS,
    *storage.LEARNING_TABLES,
)
_BINDING_COLUMNS = (
    "session_id,command_id,request_hash,profile_id,profile_revision,workspace_id,"
    "workspace_revision,policy,policy_revision,settings_revision,runtime,capability_key,"
    "launch_spec_hash,native_started,created_at"
)


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012x}"


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    connection.execute(
        f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
        tuple(row.values()),
    )


def _binding(connection: sqlite3.Connection, number: int, **changes: Any) -> None:
    _insert(
        connection,
        "session_profile_bindings",
        {
            "session_id": _id(number),
            "command_id": _id(number + 10000),
            "request_hash": "a" * 64,
            "profile_id": _id(number + 20000),
            "profile_revision": 1,
            "workspace_id": _id(900),
            "workspace_revision": 1,
            "policy": "ordinary",
            "policy_revision": 1,
            "settings_revision": 0,
            "runtime": "claude",
            "capability_key": "b" * 64,
            "launch_spec_hash": "c" * 64,
            "native_started": number % 2,
            "created_at": _STAMP,
            **changes,
        },
    )


def _legacy_rows(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    return {
        name: connection.execute(
            f"SELECT {_BINDING_COLUMNS if name == 'session_profile_bindings' else '*'} "
            f"FROM {name} ORDER BY 1"
        ).fetchall()
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'"
        ).fetchall()
        if name not in _TABLES
    }


def version_39(tmp_path: Path, *, populated: bool = True) -> Path:
    """Reproduce actual v39 DDL, including its unchanged memory and binding columns."""
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        _downgrade_bot_code(connection)
        connection.execute("PRAGMA user_version=39")
        if populated:
            _populate_legacy(connection)
        connection.commit()
        state.store._validate(connection, (39,))
    return state.database_path


def _populate_legacy(connection: sqlite3.Connection) -> None:
    _insert(connection, "workspaces", _workspace(_id(900)))
    for number in range(1, 13):
        connection.execute(
            "INSERT INTO sessions VALUES (?,'plan','high',?,'profile')",
            (_id(number), "viki-llm/" + _id(999) + "/synthetic-model" if number == 1 else None),
        )
        _binding(connection, number)
        if number != 3:
            connection.execute(
                "INSERT INTO session_bot_memory VALUES (?,?)", (_id(number), _id(number + 100))
            )
        if number in (1, 2, 3, 4, 5, 6, 10, 11, 12):
            _insert(
                connection,
                "bots",
                {
                    "bot_id": _id(number + 100),
                    "profile_id": _id(number + 20000 if number != 4 else 29999),
                    "workspace_id": _id(900 if number != 5 else 901),
                    "chat_session_id": _id(number),
                    "request_hash": "d" * 64,
                    "state_revision": 1,
                    "created_at": _STAMP,
                    "updated_at": _STAMP,
                },
            )
    # A retained routine wins even over an otherwise exact Bot-main owner match.
    for number in (2, 8):
        connection.execute(
            "INSERT INTO task_attempts VALUES "
            "(?,'routine',?,?,?,?,'done','expired','',NULL,NULL,NULL,?,NULL,?)",
            (
                _id(number + 30000),
                _id(number + 40000),
                "e" * 64,
                _id(number),
                _id(950),
                _STAMP,
                _STAMP,
            ),
        )
    connection.execute(
        "UPDATE session_bot_memory SET bot_id=? WHERE session_id=?", (_id(999), _id(6))
    )
    connection.execute("UPDATE sessions SET origin='app' WHERE session_id=?", (_id(10),))
    connection.execute("UPDATE workspaces SET kind='code' WHERE workspace_id=?", (_id(900),))
    # The main positive match uses a vault; mismatched current workspace kinds stay unknown.
    _insert(connection, "workspaces", _workspace(_id(901)))
    connection.execute(
        "UPDATE session_profile_bindings SET workspace_id=? WHERE session_id IN (?,?,?)",
        (_id(901), _id(1), _id(2), _id(12)),
    )
    connection.execute(
        "UPDATE bots SET workspace_id=? WHERE chat_session_id IN (?,?,?)",
        (_id(901), _id(1), _id(2), _id(12)),
    )
    connection.execute(
        "INSERT INTO bot_threads VALUES (?, ?,1,?,2,?,?,?)",
        (_id(960), _id(901), b"sealed", "f" * 64, _STAMP, _STAMP),
    )
    connection.executemany(
        "INSERT INTO bot_thread_sessions VALUES (?,?)", [(_id(960), _id(n)) for n in (7, 8, 9)]
    )
    connection.execute(
        "INSERT INTO drafts VALUES (?,?,3,?,2,7,?)",
        ("a" * 64, _id(1), b"\x00sealed-draft\xff", _STAMP),
    )


def _schema(connection: sqlite3.Connection) -> list[Any]:
    return connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
    ).fetchall()


def test_fresh_and_migrated_v40_have_identical_sql_and_keep_old_rows(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 43
    assert MAX_BINDINGS == 4096
    path = version_39(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        before = _legacy_rows(connection)
        old_schema = _schema(connection)
    state = AppState(tmp_path / "state.json", Prefs())
    fresh = AppState(tmp_path / "fresh" / "state.json", Prefs())
    with state.store._connection() as connection, fresh.store._connection() as created:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _schema(connection) == _schema(created)
        assert _legacy_rows(connection) == before
        assert (
            dict(
                connection.execute(
                    "SELECT name,sql FROM sqlite_schema "
                    "WHERE name LIKE 'bot_code_%' AND type='table'"
                )
            )
            == storage.BOT_CODE_SCHEMAS
        )
        assert len(storage.BOT_CODE_SCHEMAS) == 3
        assert len(storage.BOT_CODE_INDEXES) == 2
        assert connection.execute(
            "SELECT session_id,launch_origin FROM session_profile_bindings ORDER BY session_id"
        ).fetchall() == [
            (_id(n), "interactive" if n in (1, 12) else "routine" if n in (2, 8) else "unknown")
            for n in range(1, 13)
        ]
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name='session_bot_memory'"
        ).fetchone() == (storage.BOT_MEMORY_SCHEMAS["session_bot_memory"],)
    (backup,) = tmp_path.glob("app.sqlite3.v39-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(backup)) as connection:
        state.store._validate(connection, (39,))
        assert _schema(connection) == old_schema
        assert _legacy_rows(connection) == before
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]


@pytest.mark.parametrize("origin", [None, "", "Interactive", "shared", "unknown ", "x" * 4096])
def test_launch_origin_enum_and_default(tmp_path: Path, origin: Any) -> None:
    store = AppState(tmp_path / "state.json", Prefs()).store
    with store._connection(write=True) as connection:
        _binding(connection, 1)
        assert connection.execute(
            "SELECT launch_origin FROM session_profile_bindings"
        ).fetchone() == ("unknown",)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE session_profile_bindings SET launch_origin=?", (origin,))
        for valid in ("unknown", "interactive", "routine"):
            connection.execute("UPDATE session_profile_bindings SET launch_origin=?", (valid,))


def test_cap_sized_legacy_binding_set_stays_bounded_and_unknown(tmp_path: Path) -> None:
    path = version_39(tmp_path, populated=False)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        for number in range(1, MAX_BINDINGS + 1):
            _binding(connection, number)
        connection.commit()
        before = _legacy_rows(connection)
    store = AppState(tmp_path / "state.json", Prefs()).store
    with store._connection() as connection:
        assert _legacy_rows(connection) == before
        assert connection.execute(
            "SELECT count(*),count(DISTINCT session_id) FROM session_profile_bindings "
            "WHERE launch_origin='unknown'"
        ).fetchone() == (MAX_BINDINGS, MAX_BINDINGS)


def test_reopen_does_not_promote_unknown_after_bot_membership_changes(tmp_path: Path) -> None:
    version_39(tmp_path)
    store = AppState(tmp_path / "state.json", Prefs()).store
    with store._connection(write=True) as connection:
        # A formerly ambiguous side chat later becomes an exact current Bot-main match.
        connection.execute(
            "UPDATE bots SET chat_session_id=?,profile_id=? WHERE bot_id=?",
            (_id(9), _id(20009), _id(101)),
        )
        connection.execute(
            "UPDATE session_bot_memory SET bot_id=? WHERE session_id=?", (_id(101), _id(9))
        )
        connection.execute(
            "UPDATE session_profile_bindings SET workspace_id=? WHERE session_id=?",
            (_id(901), _id(9)),
        )
    reopened = AppState(tmp_path / "state.json", Prefs()).store
    with reopened._connection() as connection:
        assert connection.execute(
            "SELECT launch_origin FROM session_profile_bindings WHERE session_id=?", (_id(9),)
        ).fetchone() == ("unknown",)


def _copy(**changes: Any) -> dict[str, Any]:
    return {
        "worktree_id": _id(1),
        "workspace_id": _id(900),
        "bot_id": _id(101),
        "session_id": _id(2),
        "thread_id": None,
        "state": "bound",
        "created_at": _STAMP,
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"worktree_id": "short"},
        {"workspace_id": "short"},
        {"bot_id": "x" * 36},
        {"bot_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"bot_id": "0" * 36},
        {"bot_id": _id(101).replace("-", "_", 1)},
        pytest.param({"bot_id": _id(101) + "\x00evil"}, id="uuid_nul_suffix"),
        pytest.param({"bot_id": _id(101) + "\x00"}, id="uuid_nul_terminator"),
        pytest.param({"bot_id": _id(101)[:-1] + "\x00f"}, id="uuid_nul_embedded"),
        {"session_id": "short"},
        {"thread_id": "short", "session_id": None},
        {"session_id": None},
        {"thread_id": _id(3)},
        {"state": "archived"},
        {"created_at": "short"},
        {"created_at": None},
    ],
)
def test_copy_checks(tmp_path: Path, changes: dict[str, Any]) -> None:
    with AppState(tmp_path / "state.json", Prefs()).store._connection(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "bot_code_copies", _copy(**changes))
        _insert(connection, "bot_code_copies", _copy())


def test_partial_indexes_allow_released_history_and_separate_thread_folders(tmp_path: Path) -> None:
    with AppState(tmp_path / "state.json", Prefs()).store._connection(write=True) as connection:
        _insert(connection, "bot_code_copies", _copy())
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "bot_code_copies", _copy(worktree_id=_id(3)))
        _insert(connection, "bot_code_copies", _copy(worktree_id=_id(3), state="released"))
        _insert(connection, "bot_code_copies", _copy(worktree_id=_id(7), state="former"))
        _insert(
            connection,
            "bot_code_copies",
            _copy(worktree_id=_id(4), session_id=None, thread_id=_id(30)),
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert(
                connection,
                "bot_code_copies",
                _copy(worktree_id=_id(5), session_id=None, thread_id=_id(30)),
            )
        _insert(
            connection,
            "bot_code_copies",
            _copy(worktree_id=_id(5), workspace_id=_id(901), session_id=None, thread_id=_id(30)),
        )
        _insert(
            connection,
            "bot_code_copies",
            _copy(worktree_id=_id(6), session_id=None, thread_id=_id(30), state="released"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "bot_code_copies", _copy(state="released"))
        assert {
            row[2] for row in connection.execute("PRAGMA foreign_key_list(bot_code_copies)")
        } == {"worktrees", "workspaces"}


@pytest.mark.parametrize(
    "fingerprint",
    [
        None,
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        pytest.param("a" * 64 + "\x00evil", id="fingerprint_nul_suffix"),
        pytest.param("a" * 64 + "\x00", id="fingerprint_nul_terminator"),
        pytest.param("a" * 63 + "\x00a", id="fingerprint_nul_embedded"),
    ],
)
def test_baseline_checks(tmp_path: Path, fingerprint: Any) -> None:
    with AppState(tmp_path / "state.json", Prefs()).store._connection(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bot_code_baselines VALUES (?,?,?)", (_id(900), fingerprint, _STAMP)
            )
        connection.execute(
            "INSERT INTO bot_code_baselines VALUES (?,?,?)", (_id(900), "a" * 64, _STAMP)
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bot_code_baselines VALUES (?,?,?)", (_id(900), "b" * 64, _STAMP)
            )


@pytest.mark.parametrize(
    "table,row",
    [
        ("bot_code_folders", {"bot_id": "short", "workspace_id": _id(900), "created_at": _STAMP}),
        ("bot_code_folders", {"bot_id": _id(101), "workspace_id": "short", "created_at": _STAMP}),
        ("bot_code_folders", {"bot_id": _id(101), "workspace_id": _id(900), "created_at": "short"}),
        (
            "bot_code_baselines",
            {"workspace_id": "short", "git_fingerprint": "a" * 64, "created_at": _STAMP},
        ),
        (
            "bot_code_baselines",
            {"workspace_id": _id(900), "git_fingerprint": "a" * 64, "created_at": "short"},
        ),
    ],
)
def test_folder_and_baseline_checks(tmp_path: Path, table: str, row: dict[str, Any]) -> None:
    with (
        AppState(tmp_path / "state.json", Prefs()).store._connection(write=True) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        _insert(connection, table, row)


def test_foreign_keys_and_released_copy_survive_bot_deletion(tmp_path: Path) -> None:
    path = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        _insert(connection, "workspaces", _workspace(_id(900), kind="code"))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO bot_code_folders VALUES (?,?,?)", (_id(101), _id(900), _STAMP)
            )
        connection.execute(
            "INSERT INTO profiles VALUES (?,1,1,0,?,?)", (_id(20001), _STAMP, _STAMP)
        )
        connection.execute(
            "INSERT INTO bots VALUES (?,?,?,?,?,1,?,?)",
            (_id(101), _id(20001), _id(900), _id(2), "a" * 64, _STAMP, _STAMP),
        )
        connection.execute(
            "INSERT INTO bot_code_folders VALUES (?,?,?)", (_id(101), _id(900), _STAMP)
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bot_code_folders VALUES (?,?,?)", (_id(101), _id(900), _STAMP)
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            _insert(connection, "bot_code_copies", _copy(state="released"))
        connection.execute(
            "INSERT INTO worktrees VALUES (?,?,?,?,?,?,?,?)",
            (
                _id(1),
                _id(900),
                "<repo>/.worktrees/copy",
                "viki/copy",
                "a" * 40,
                "ready",
                _STAMP,
                _STAMP,
            ),
        )
        _insert(connection, "bot_code_copies", _copy(state="released"))
        connection.execute(
            "INSERT INTO bot_code_baselines VALUES (?,?,?)", (_id(900), "a" * 64, _STAMP)
        )
        connection.execute("DELETE FROM bot_code_folders")
        connection.execute("DELETE FROM bots")
        assert connection.execute("SELECT bot_id,state FROM bot_code_copies").fetchall() == [
            (_id(101), "released")
        ]
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("DELETE FROM worktrees")
        connection.execute("DELETE FROM bot_code_copies")
        assert connection.execute("SELECT git_fingerprint FROM bot_code_baselines").fetchall() == [
            ("a" * 64,)
        ]
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("DELETE FROM workspaces")


@pytest.mark.parametrize(
    "phase",
    ["alter", "after_alter", "routine", "interactive", "ddl", "index", "commit", "row_loss"],
)
def test_interrupted_v40_transaction_rolls_back_all_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    path = version_39(tmp_path)
    before, connect = path.read_bytes(), sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            adding = sql.startswith("ALTER TABLE session_profile_bindings ADD COLUMN")
            failing = (
                (phase == "alter" and adding)
                or (
                    phase in ("routine", "interactive")
                    and sql.startswith(
                        "UPDATE session_profile_bindings SET launch_origin='" + phase + "'"
                    )
                )
                or (phase == "ddl" and sql.startswith("CREATE TABLE bot_code_copies"))
                or (phase == "index" and sql.startswith("CREATE UNIQUE INDEX bot_code_"))
                or (phase == "commit" and sql == "COMMIT")
            )
            if failing:
                raise sqlite3.OperationalError("synthetic interrupted migration")
            result = super().execute(sql, parameters)
            if phase == "after_alter" and adding:
                raise sqlite3.OperationalError("synthetic interrupted migration")
            if phase == "row_loss" and adding:
                super().execute("DELETE FROM session_profile_bindings")
            return result

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
        with pytest.raises(storage.StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert path.read_bytes() == before
    (backup,) = tmp_path.glob("app.sqlite3.v39-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (39,)
    assert not list(tmp_path.glob(".viki-migration-*"))
    AppState(tmp_path / "state.json", Prefs())


@pytest.mark.parametrize("failure", ["missing", "corrupt", "unwritable"])
def test_unverified_backup_refuses_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = version_39(tmp_path)
    before, rename = path.read_bytes(), storage.osfs.rename_no_replace

    def publish(source: Path, target: Path) -> None:
        if ".v39-backup-" in target.name:
            if failure == "unwritable":
                raise PermissionError("synthetic backup directory refusal")
            if failure == "missing":
                source.unlink()
            else:
                source.write_bytes(b"synthetic corrupt backup")
        rename(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(storage.osfs, "rename_no_replace", publish)
        with pytest.raises(storage.StateError):
            AppState(tmp_path / "state.json", Prefs())
    assert path.read_bytes() == before


def test_v39_reader_refuses40_then_restores_matching_backup_and_reupgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = version_39(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        before = _legacy_rows(connection)
    store = AppState(tmp_path / "state.json", Prefs()).store
    with store._connection(write=True) as connection:
        _binding(connection, 100, launch_origin="interactive")
        _insert(connection, "bot_code_copies", _copy())
        connection.execute(
            "INSERT INTO bot_code_baselines VALUES (?,?,?)", (_id(900), "a" * 64, _STAMP)
        )
    upgraded = path.read_bytes()
    (backup,) = tmp_path.glob("app.sqlite3.v39-backup-*.sqlite3")

    def refuse(*args: Any, **kwargs: Any) -> None:
        pytest.fail("The v39 reader reached SQLite for v40")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with (
            pytest.raises(storage.StateError, match="compatible"),
            store._connection(versions=tuple(range(1, 40))),
        ):
            pytest.fail("The v39 reader admitted v40")
    assert path.read_bytes() == upgraded
    shutil.copyfile(backup, path)
    with store._connection(versions=(39,)) as connection:
        assert _legacy_rows(connection) == before
        assert not connection.execute(
            "SELECT name FROM sqlite_schema WHERE name LIKE 'bot_code_%'"
        ).fetchall()
        assert "launch_origin" not in [
            row[1] for row in connection.execute("PRAGMA table_info(session_profile_bindings)")
        ]
    reopened = AppState(tmp_path / "state.json", Prefs())
    with reopened.store._connection() as connection:
        assert _legacy_rows(connection) == before
        assert connection.execute("SELECT * FROM bot_code_copies").fetchall() == []
        assert connection.execute("SELECT * FROM bot_code_baselines").fetchall() == []
        assert connection.execute(
            "SELECT launch_origin FROM session_profile_bindings WHERE session_id=?", (_id(1),)
        ).fetchone() == ("interactive",)
