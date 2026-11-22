"""Schema step 17: the skill_proposals table."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import (
    _SCOPE,
    _STAMP,
    OTHER,
    SID,
    _downgrade_schema_seventeen,
    _workspace,
)
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

_CHECK = "00000000-0000-4000-8000-000000000031"
_RUN = "00000000-0000-4000-8000-000000000041"
_PROPOSAL = "00000000-0000-4000-8000-000000000051"
# The store bounds only the length: a compact JSON list of one UUID is 40 characters, of ten 391.
_ONE = json.dumps([SID], separators=(",", ":"))
_TEN = json.dumps([SID] * 10, separators=(",", ":"))


def _ddl(connection: sqlite3.Connection) -> dict[str, str]:
    names = ",".join(f"'{name}'" for name in storage.SKILL_SCHEMAS)
    return dict(
        connection.execute(
            f"SELECT name, sql FROM sqlite_schema WHERE type='table' AND name IN ({names})"
        ).fetchall()
    )


_KEPT = ("sessions", "workspaces", "drafts", *storage.CHECK_SCHEMAS)


def _rows(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, list[Any]]:
    return {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in tables
    }


def _proposal(**overrides: Any) -> tuple[Any, ...]:
    row = {
        "proposal_id": _PROPOSAL,
        "workspace_id": SID,
        "name": "weekly-review",
        "trigger": "button",
        "sessions": _ONE,
        "base_sha256": None,
        "proposed_sha256": "b" * 64,
        "body": b"sealed",
        "text_bytes": 100,
        "approved_at": None,
        "rejected_at": None,
        "reason": None,
        "created_at": _STAMP,
        "updated_at": _STAMP,
    }
    return tuple({**row, **overrides}.values())


def _version_sixteen(tmp_path: Path) -> Path:
    """A schema-16 store, as the last schema-16 build left it: a session, a workspace, a draft,
    a check and a run of it."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v16-model")
    columns = storage._WORKSPACE_COLUMNS
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            f"INSERT INTO workspaces ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
            tuple(_workspace(OTHER, kind="code").values()),
        )
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        connection.execute(
            "INSERT INTO code_checks VALUES (?,?,?,?,?)", (_CHECK, OTHER, b"sealed", 600, _STAMP)
        )
        connection.execute(
            f"INSERT INTO check_runs VALUES ({','.join('?' * 13)})",
            (_RUN, _CHECK, OTHER, None, "a" * 40, 0, "passed", 0, 1, 1, b"x", _STAMP, _STAMP),
        )
        _downgrade_schema_seventeen(connection)
        connection.execute("PRAGMA user_version=16")
        connection.commit()
    return state.database_path


def test_fresh_store_is_v17_with_the_table(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 41
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (41,)
        assert _ddl(connection) == storage.SKILL_SCHEMAS


def test_v16_store_upgrades_to_v17_with_rows_intact(tmp_path: Path) -> None:
    database = _version_sixteen(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        assert _ddl(source) == {}
        rows = _rows(source, _KEPT)
    assert all(len(value) == 1 for value in rows.values())

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v16-model")
    (backup,) = tmp_path.glob("app.sqlite3.v16-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        assert saved.execute("PRAGMA user_version").fetchone() == (16,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _ddl(saved) == {}
        assert _rows(saved, _KEPT) == rows
        state._store._validate(saved, (16,))  # the store's own check, at the version it names
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (41,)
        assert _ddl(connection) == storage.SKILL_SCHEMAS
        assert _rows(connection, (*_KEPT, *storage.SKILL_SCHEMAS)) == {
            **rows,
            "skill_proposals": [],
        }


def test_a_newer_store_is_refused_unchanged(tmp_path: Path) -> None:
    path = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA user_version={storage.SCHEMA_VERSION + 1}")
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(StateError) as failure:
        AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == "incompatible"
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*backup*"))


@pytest.mark.parametrize(
    ("row", "valid"),
    [
        (_proposal(name="weekly_review"), _proposal()),
        (_proposal(name="Weekly-review"), _proposal()),
        (_proposal(name="a" * 65), _proposal(name="a" * 64)),
        (_proposal(trigger="schedule"), _proposal(trigger="correction")),
        (_proposal(sessions=_ONE[:-1]), _proposal(sessions=_ONE)),
        (_proposal(sessions=f"{_TEN} "), _proposal(sessions=_TEN)),
        (_proposal(base_sha256="a" * 63), _proposal(base_sha256="a" * 64)),
        (_proposal(text_bytes=0), _proposal(text_bytes=1)),
        (_proposal(text_bytes=131073), _proposal(text_bytes=131072)),
        (_proposal(reason="Too broad."), _proposal(reason="Too broad.", rejected_at=_STAMP)),
        (_proposal(approved_at=_STAMP, rejected_at=_STAMP), _proposal(rejected_at=_STAMP)),
        (
            _proposal(reason="r" * 201, rejected_at=_STAMP),
            _proposal(reason="r" * 200, rejected_at=_STAMP),
        ),
    ],
)
def test_inserts_outside_the_checks_are_refused(
    tmp_path: Path, row: tuple[Any, ...], valid: tuple[Any, ...]
) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    insert = f"INSERT INTO skill_proposals VALUES ({','.join('?' * len(row))})"
    with contextlib.closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, row)
        assert connection.execute("SELECT count(*) FROM skill_proposals").fetchone() == (0,)
        connection.execute(insert, valid)  # the same row within bounds is admitted
