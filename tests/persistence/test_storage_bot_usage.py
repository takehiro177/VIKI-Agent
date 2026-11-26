"""The Bot usage log arrives in step 41; a v40 build refuses the store and opens its backup."""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import _STAMP, _workspace
from viki_agent import storage
from viki_agent.state import AppState, Prefs

# Inbox suggestions (step 42) are newer than v40 too.
_TABLES = (
    "bot_usage_calls",
    "bot_usage_sessions",
    *storage.INBOX_SUGGESTION_SCHEMAS,
    *storage.LEARNING_TABLES,
)
_INDEX = "bot_usage_calls_at"
_MAX_TOKENS = 9007199254740991


def _id(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012x}"


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    connection.execute(
        f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
        tuple(row.values()),
    )


def _schema(connection: sqlite3.Connection) -> list[Any]:
    return connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
    ).fetchall()


def _rows(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    """Every row of every table that predates step 41."""
    return {
        name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'"
        ).fetchall()
        if name not in _TABLES
    }


def version_40(tmp_path: Path) -> Path:
    """A v41 store without the three step-41 objects, stamped 40, is a populated v40 store."""
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(f"DROP INDEX IF EXISTS {_INDEX}")
        for name in _TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {name}")
        connection.execute("PRAGMA user_version=40")
        _insert(connection, "workspaces", _workspace(_id(900)))
        connection.executemany(
            "INSERT INTO sessions VALUES (?,'plan','high',?,'app')",
            [(_id(1), "synthetic-model"), (_id(2), None)],
        )
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,3,?,2,7,?)",
            ("a" * 64, _id(1), b"\x00sealed-draft\xff", _STAMP),
        )
        connection.execute(
            "INSERT INTO bot_code_baselines VALUES (?,?,?)", (_id(900), "a" * 64, _STAMP)
        )
        connection.commit()
        state.store._validate(connection, (40,))
    return state.database_path


def test_fresh_and_upgraded_schemas_match(tmp_path: Path) -> None:
    path = version_40(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        before = _rows(connection)
    upgraded = AppState(tmp_path / "state.json", Prefs()).store
    fresh = AppState(tmp_path / "fresh" / "state.json", Prefs()).store
    with upgraded._connection() as connection, fresh._connection() as created:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _schema(connection) == _schema(created)
        assert _rows(connection) == before
        usage = storage.BOT_USAGE_SCHEMAS
        assert [row for row in _schema(created) if row[1].startswith("bot_usage_")] == [
            ("table", "bot_usage_calls", "bot_usage_calls", usage["bot_usage_calls"]),
            ("index", _INDEX, "bot_usage_calls", storage.BOT_USAGE_INDEXES[_INDEX]),
            ("table", "bot_usage_sessions", "bot_usage_sessions", usage["bot_usage_sessions"]),
        ]
        for name in _TABLES:
            assert connection.execute(f"SELECT count(*) FROM {name}").fetchone() == (0,)


def test_upgrade_writes_a_v40_backup_that_opens_with_identical_rows(tmp_path: Path) -> None:
    path = version_40(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        before, old_schema = _rows(connection), _schema(connection)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v40-backup-*")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        store._validate(saved, (40,))
        assert _schema(saved) == old_schema
        assert _rows(saved) == before
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]


def test_v40_build_refuses_v41_like_any_newer_version_and_opens_the_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = version_40(tmp_path)
    with contextlib.closing(sqlite3.connect(path)) as connection:
        before = _rows(connection)
    AppState(tmp_path / "state.json", Prefs())
    (backup,) = tmp_path.glob("app.sqlite3.v40-backup-*.sqlite3")
    files = {item.name: item.read_bytes() for item in tmp_path.iterdir()}
    connect = sqlite3.connect

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v40 build reached SQLite for the v41 store")

    with monkeypatch.context() as patch:
        # The base build's view: every version up to 40 and nothing newer.
        patch.setattr(storage, "SCHEMA_VERSION", 40)
        patch.setattr(storage, "_RECOGNISED_VERSIONS", tuple(range(1, 41)))
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(storage.StateError) as failure:
            AppState(tmp_path / "state.json", Prefs())
        assert failure.value.code == "incompatible"
        assert {item.name: item.read_bytes() for item in tmp_path.iterdir()} == files
        patch.setattr(sqlite3, "connect", connect)
        shutil.copyfile(backup, path)
        restored = AppState(tmp_path / "state.json", Prefs()).store
        with restored._connection(versions=(40,)) as connection:
            assert _rows(connection) == before
            assert not connection.execute(
                "SELECT name FROM sqlite_schema WHERE name GLOB 'bot_usage_*'"
            ).fetchall()
    # Outside the base build's view again: this build opens the restored v40 store once more,
    # upgrades it to 41 with its rows kept, and writes a second v40 backup alongside the first.
    second_build = AppState(tmp_path / "state.json", Prefs()).store
    with second_build._connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _rows(connection) == before
    all_backups = set(tmp_path.glob("app.sqlite3.v40-backup-*.sqlite3"))
    assert len(all_backups) == 2
    assert backup in all_backups
    (second_backup,) = all_backups - {backup}
    with contextlib.closing(
        sqlite3.connect(f"{second_backup.as_uri()}?mode=ro", uri=True)
    ) as saved:
        second_build._validate(saved, (40,))
        assert _rows(saved) == before
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]


@pytest.mark.parametrize(
    "table,old,new",
    [
        pytest.param("", "", "", id="missing_index"),
        pytest.param("bot_usage_calls", "BETWEEN 1 AND 128", "BETWEEN 1 AND 129", id="calls_check"),
        pytest.param("bot_usage_sessions", ",'routine'", "", id="sessions_check"),
    ],
)
def test_validate_refuses_missing_index_or_altered_check(
    tmp_path: Path, table: str, old: str, new: str
) -> None:
    state = AppState(tmp_path / "state.json", Prefs())
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(f"DROP INDEX {_INDEX}")
        if table:
            sql = storage.BOT_USAGE_SCHEMAS[table]
            assert sql.replace(old, new) != sql
            connection.execute(f"DROP TABLE {table}")
            connection.execute(sql.replace(old, new))
            connection.execute(storage.BOT_USAGE_INDEXES[_INDEX])
        connection.commit()
        with pytest.raises(storage.StateError) as failure:
            state.store._validate(connection)
        assert failure.value.code == "incompatible"
    with pytest.raises(storage.StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"


def _session(**changes: Any) -> dict[str, Any]:
    return {
        "session_id": _id(1),
        "workspace_id": _id(900),
        "bot_id": _id(101),
        "profile_id": _id(201),
        "kind": "routine",
        "connection_id": _id(301),
        "read_ns": 9223372036854775807,
        **changes,
    }


def _call(**changes: Any) -> dict[str, Any]:
    return {
        "session_id": _id(1),
        "message_id": "m" * 128,
        "prompt_id": "p" * 64,
        "at": _STAMP,
        "model": "x" * 256,
        "agent": 1,
        "input_tokens": _MAX_TOKENS,
        "output_tokens": 0,
        "cache_read_tokens": _MAX_TOKENS,
        "cache_write_tokens": 0,
        **changes,
    }


@pytest.mark.parametrize(
    "table,changes",
    [
        ("bot_usage_sessions", {"session_id": "short"}),
        ("bot_usage_sessions", {"workspace_id": None}),
        ("bot_usage_sessions", {"bot_id": "x" * 37}),
        ("bot_usage_sessions", {"profile_id": "x" * 35}),
        ("bot_usage_sessions", {"kind": "chat"}),
        ("bot_usage_sessions", {"connection_id": "short"}),
        ("bot_usage_sessions", {"read_ns": -1}),
        ("bot_usage_sessions", {"read_ns": 0.5}),
        ("bot_usage_calls", {"session_id": "short"}),
        ("bot_usage_calls", {"message_id": ""}),
        ("bot_usage_calls", {"message_id": "m" * 129}),
        ("bot_usage_calls", {"prompt_id": ""}),
        ("bot_usage_calls", {"prompt_id": "p" * 65}),
        ("bot_usage_calls", {"at": "short"}),
        ("bot_usage_calls", {"model": "x" * 257}),
        ("bot_usage_calls", {"model": None}),
        ("bot_usage_calls", {"agent": 2}),
        ("bot_usage_calls", {"input_tokens": -1}),
        ("bot_usage_calls", {"output_tokens": _MAX_TOKENS + 1}),
        ("bot_usage_calls", {"cache_read_tokens": None}),
        ("bot_usage_calls", {"cache_write_tokens": -1}),
    ],
)
def test_usage_checks(tmp_path: Path, table: str, changes: dict[str, Any]) -> None:
    row = _session if table == "bot_usage_sessions" else _call
    with AppState(tmp_path / "state.json", Prefs()).store._connection(write=True) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, table, row(**changes))
        _insert(connection, "bot_usage_sessions", _session())
        for number, kind in enumerate(("main", "task", "bot_thread"), start=2):
            _insert(
                connection,
                "bot_usage_sessions",
                _session(session_id=_id(number), kind=kind, connection_id=None, read_ns=None),
            )
        _insert(connection, "bot_usage_calls", _call())
        _insert(connection, "bot_usage_calls", _call(message_id="m", prompt_id=None, agent=0))
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "bot_usage_calls", _call(model=""))
