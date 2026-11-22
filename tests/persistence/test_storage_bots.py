"""Bot metadata migration and constraints, using synthetic app state only."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import _downgrade_decision_loop
from viki_agent import storage

_STAMP = "2026-10-14T00:00:00.000Z"
_SID = "00000000-0000-4000-8000-000000000001"
_PROFILE = "00000000-0000-4000-8000-000000000002"
_WORKSPACE = "00000000-0000-4000-8000-000000000003"
_BOT = "00000000-0000-4000-8000-000000000004"
_ATTEMPT = "00000000-0000-4000-8000-000000000005"
_ROUTINE = "00000000-0000-4000-8000-000000000006"
_OTHER = "00000000-0000-4000-8000-000000000007"
_TABLES = ("bots", "bot_deliveries", "messaging_config")


def _store(tmp_path: Path) -> storage.SessionStore:
    return storage.SessionStore(
        tmp_path / "app.sqlite3",
        lambda: {_SID: {"mode": "plan", "effort": "max", "model": "preserved-v21-model"}},
    )


def _rows(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    tables = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'"
    ).fetchall()
    return {
        name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
        for (name,) in tables
    }


def _version_twenty_one(tmp_path: Path) -> Path:
    path = _store(tmp_path).path
    with contextlib.closing(sqlite3.connect(path)) as connection:
        _downgrade_decision_loop(connection)
        for name in _TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {name}")
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)",
            ("a" * 64, _SID, 3, b"\x00sealed-draft\xff", 2, 7, _STAMP),
        )
        connection.execute(
            "INSERT INTO session_metadata VALUES (?,?,?,?,?,?,?,?)",
            ("a" * 64, _SID, 7, "Synthetic chat", 1, 0, 1, "blue"),
        )
        connection.execute("INSERT INTO profiles VALUES (?,1,2,0,?,?)", (_PROFILE, _STAMP, _STAMP))
        connection.execute(
            "INSERT INTO profile_revisions VALUES (?,1,1,?,?,?,?)",
            (_PROFILE, b"\x00sealed-profile\xff", 9, "b" * 64, _STAMP),
        )
        connection.execute(
            "INSERT INTO routines VALUES (?,1,'draft',2,NULL,NULL,NULL,?,?)",
            (_ROUTINE, _STAMP, _STAMP),
        )
        connection.execute(
            "INSERT INTO routine_revisions VALUES (?,1,1,?,?,?,?,?,?)",
            (_ROUTINE, '{"kind":"manual"}', b"\x00sealed-routine\xff", 9, "c" * 64, _STAMP, _STAMP),
        )
        connection.execute("PRAGMA user_version=21")
        connection.commit()
    return path


def _bot(**changes: Any) -> dict[str, Any]:
    return {
        "bot_id": _BOT,
        "profile_id": _PROFILE,
        "workspace_id": _WORKSPACE,
        "chat_session_id": _SID,
        "request_hash": "a" * 64,
        "state_revision": 1,
        "created_at": _STAMP,
        "updated_at": _STAMP,
        **changes,
    }


def _delivery(**changes: Any) -> dict[str, Any]:
    return {
        "attempt_id": _ATTEMPT,
        "bot_id": _BOT,
        "attention": "notify",
        "reason": "rule",
        "state": "sent",
        "delivery_reason": "none",
        "created_at": _STAMP,
        "updated_at": _STAMP,
        **changes,
    }


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    connection.execute(
        f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
        tuple(row.values()),
    )


def test_fresh_store_has_exact_metadata_only_bot_schema(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert storage.SCHEMA_VERSION == 42 and storage.BOT_SCHEMA_STEP == 22
    with store._connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        for name, ddl in storage.BOT_SCHEMAS.items():
            assert connection.execute(
                "SELECT sql FROM sqlite_schema WHERE name=?", (name,)
            ).fetchone() == (ddl,)
            assert connection.execute(f"SELECT count(*) FROM {name}").fetchone() == (0,)
        assert [row[1] for row in connection.execute("PRAGMA table_info(bots)")] == list(_bot())
        assert [row[1] for row in connection.execute("PRAGMA table_info(bot_deliveries)")] == list(
            _delivery()
        )
        assert {row[2] for row in connection.execute("PRAGMA foreign_key_list(bots)")} == {
            "profiles",
            "workspaces",
        }
        assert {
            row[2] for row in connection.execute("PRAGMA foreign_key_list(bot_deliveries)")
        } == {"bots", "task_attempts"}
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (0,)


def test_upgrade_v21_keeps_state_and_backup(tmp_path: Path) -> None:
    path = _version_twenty_one(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        before = _rows(connection)
    store = _store(tmp_path)
    assert store.get(_SID) == {"mode": "plan", "effort": "max", "model": "preserved-v21-model"}
    with store._connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        # Step 24 also moves v21's jev_* rows (none here) into its six decision_* tables, and
        # step 25 adds the proposal table.
        assert _rows(connection) == {
            **{name: rows for name, rows in before.items() if name not in storage.JEV_SCHEMAS},
            "session_bot_memory": [],
            "bot_threads": [],
            "bot_thread_sessions": [],
            **dict.fromkeys(storage.BOT_CODE_SCHEMAS, []),
            **dict.fromkeys(storage.INBOX_SUGGESTION_SCHEMAS, []),
            **dict.fromkeys(storage.BOT_USAGE_SCHEMAS, []),
            "bots": [],
            "bot_deliveries": [],
            "messaging_config": [],
            **dict.fromkeys(storage.DECISION_LOOP_SCHEMAS, []),
            **dict.fromkeys(storage.DECISION_PROPOSAL_SCHEMAS, []),
        }
    (backup,) = tmp_path.glob("app.sqlite3.v21-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        store._validate(saved, (21,))
        assert saved.execute("PRAGMA user_version").fetchone() == (21,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _rows(saved) == before
    _store(tmp_path)
    # Each step from 21 through the current schema backs up its source version.
    assert len(list(tmp_path.glob("*backup*"))) == 21


@pytest.mark.parametrize("phase", ["backup", "ddl", "row_loss", "commit"])
def test_failed_v22_migration_rolls_back_and_keeps_v21(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    path = _version_twenty_one(tmp_path)
    before = path.read_bytes()
    connect = sqlite3.connect
    rename = storage.osfs.rename_no_replace

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if (phase == "ddl" and sql.startswith("CREATE TABLE bots (")) or (
                phase == "commit" and sql == "COMMIT"
            ):
                raise sqlite3.OperationalError("synthetic interrupted migration")
            result = super().execute(sql, parameters)
            if phase == "row_loss" and sql.startswith("CREATE TABLE bot_deliveries ("):
                super().execute("DELETE FROM drafts")
            return result

    def publish(source: Path, destination: Path) -> None:
        if phase == "backup" and ".v21-backup-" in destination.name:
            raise OSError("synthetic backup failure")
        rename(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
        patch.setattr(storage.osfs, "rename_no_replace", publish)
        with pytest.raises(storage.StateError) as failure:
            _store(tmp_path)
        assert failure.value.code == ("corrupt" if phase == "row_loss" else "unavailable")
    assert path.read_bytes() == before
    assert len(list(tmp_path.glob("*backup*"))) == (0 if phase == "backup" else 1)
    assert not list(tmp_path.glob(".viki-migration-*"))
    assert _store(tmp_path).get(_SID) is not None


@pytest.mark.parametrize("mutation", ["future", "altered_v21", "altered_v22", "partial_v22"])
def test_unreviewed_schema_refuses_unchanged_without_backup(tmp_path: Path, mutation: str) -> None:
    path = _version_twenty_one(tmp_path) if mutation == "altered_v21" else _store(tmp_path).path
    with contextlib.closing(sqlite3.connect(path)) as connection:
        if mutation == "future":
            connection.execute(f"PRAGMA user_version={storage.SCHEMA_VERSION + 1}")
        elif mutation == "altered_v21":
            connection.execute("ALTER TABLE profiles ADD COLUMN unreviewed TEXT")
        elif mutation == "altered_v22":
            connection.execute("ALTER TABLE bots ADD COLUMN armed INTEGER")
        else:
            connection.execute("DROP TABLE bot_deliveries")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(storage.StateError) as failure:
        _store(tmp_path)
    assert failure.value.code == "incompatible"
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*backup*"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bot_id", None),
        ("profile_id", "short"),
        ("workspace_id", ""),
        ("chat_session_id", "x" * 37),
        ("request_hash", "A" * 64),
        ("request_hash", "g" * 64),
        ("state_revision", 0),
        ("state_revision", 9007199254740992),
        ("state_revision", 1.5),
        ("created_at", "short"),
        ("updated_at", None),
    ],
)
def test_bot_constraints_refuse_invalid_metadata(tmp_path: Path, field: str, value: Any) -> None:
    with _store(tmp_path)._connection(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "bots", _bot(**{field: value}))
        _insert(connection, "bots", _bot(state_revision=9007199254740991))


@pytest.mark.parametrize("duplicate", ["identity", "pair", "chat"])
def test_bot_unique_identity_pair_and_chat(tmp_path: Path, duplicate: str) -> None:
    with _store(tmp_path)._connection(write=True) as connection:
        _insert(connection, "bots", _bot())
        row = _bot(bot_id=_OTHER, profile_id=_OTHER, chat_session_id=_OTHER)
        if duplicate == "identity":
            row["bot_id"] = _BOT
        elif duplicate == "pair":
            row["profile_id"] = _PROFILE
        else:
            row["chat_session_id"] = _SID
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "bots", row)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_id", None),
        ("bot_id", "short"),
        ("attention", "ask"),
        ("reason", "x" * 33),
        ("state", "queued"),
        ("delivery_reason", "x" * 33),
        ("created_at", "short"),
        ("updated_at", None),
    ],
)
def test_delivery_constraints_refuse_invalid_metadata(
    tmp_path: Path, field: str, value: Any
) -> None:
    with _store(tmp_path)._connection(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "bot_deliveries", _delivery(**{field: value}))
        _insert(connection, "bot_deliveries", _delivery(reason="x" * 32, delivery_reason="x" * 32))
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "bot_deliveries", _delivery())


def test_current_bot_metadata_is_refused_by_the_v21_header_contract_before_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    before = store.path.read_bytes()

    def connect(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The old-version contract reached SQLite")

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(storage.StateError) as failure, store._connection(versions=(21,)):
        pytest.fail("The v22 store was admitted under v21")
    assert failure.value.code == "incompatible"
    assert store.path.read_bytes() == before
