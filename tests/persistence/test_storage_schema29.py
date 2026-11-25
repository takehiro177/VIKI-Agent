"""Schema step 29: routine_occurrences rebuilt to admit the decision kind (ADR-065). An upgrade
from 28 also crosses steps 30, 31, 32, 33, 34, 35 and 36, fences that leave their own v29, v30,
v31, v32, v33, v34 and v35 backups (ADR-066, ADR-067, ADR-068, ADR-069, ADR-070, ADR-071,
ADR-072)."""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import _STAMP, SID, _downgrade_routine_occurrences
from tests.persistence.test_storage_schema_proposals import _every_row
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

# The versions a v28 build recognises: its header check refuses any other before SQLite opens.
_V28_BUILD = tuple(range(1, 29))

_ROUTINE = "00000000-0000-4000-8000-000000000101"
_SCHEDULED = "00000000-0000-4000-8000-000000000102"
_SKIPPED = "00000000-0000-4000-8000-000000000103"
_MANUAL = "00000000-0000-4000-8000-000000000104"
_DECISION = "00000000-0000-4000-8000-000000000105"
_OWNER = "00000000-0000-4000-8000-000000000106"
_SLOT = "2026-10-31T09:00:00.000Z"
_SKIP_SLOT = "2026-10-31T10:00:00.000Z"


def _attempt(connection: sqlite3.Connection, occurrence_id: str, n: int) -> None:
    connection.execute(
        "INSERT INTO task_attempts VALUES "
        "(?,'routine',?,?,?,?,'done','succeeded','',NULL,NULL,NULL,?,?,?)",
        (
            f"00000000-0000-4000-8000-00000000020{n}",
            occurrence_id,
            "a" * 64,
            f"00000000-0000-4000-8000-00000000030{n}",
            _OWNER,
            *[_STAMP] * 3,
        ),
    )


def _decision(connection: sqlite3.Connection, request_hash: str | None) -> None:
    connection.execute(
        "INSERT INTO routine_occurrences VALUES (?,?,'decision',NULL,NULL,?,1,'claimed','',?)",
        (_DECISION, _ROUTINE, request_hash, _STAMP),
    )


def _v28_store(tmp_path: Path) -> Path:
    """A v28 store as the last such build left it: a routine and its revision, a claimed and a
    skipped schedule occurrence and a manual one, the claims with their attempts, and
    routine_occurrences on its pre-29 DDL."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v28-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO routines VALUES (?,1,'enabled',2,1,?,?,?,?)",
            (_ROUTINE, _STAMP, _SLOT, _STAMP, _STAMP),
        )
        connection.execute(
            "INSERT INTO routine_revisions VALUES (?,1,1,?,?,?,?,?,?)",
            (_ROUTINE, '{"kind":"daily"}', b"\x00sealed-routine\xff", 9, "c" * 64, _STAMP, _STAMP),
        )
        connection.executemany(
            "INSERT INTO routine_occurrences VALUES (?,?,?,?,?,?,1,?,?,?)",
            (
                (_SCHEDULED, _ROUTINE, "schedule", _SLOT, None, None, "claimed", "", _STAMP),
                (_SKIPPED, _ROUTINE, "schedule", _SKIP_SLOT, None, None, "skipped", "busy", _STAMP),
                (_MANUAL, _ROUTINE, "manual", None, _OWNER, "d" * 64, "claimed", "", _STAMP),
            ),
        )
        _attempt(connection, _SCHEDULED, 1)
        _attempt(connection, _MANUAL, 2)
        _downgrade_routine_occurrences(connection)
        connection.execute("PRAGMA user_version=28")
        connection.commit()
    return state.database_path


def _occurrence_ddl(connection: sqlite3.Connection) -> str:
    return connection.execute(
        "SELECT sql FROM sqlite_schema WHERE name='routine_occurrences'"
    ).fetchone()[0]


def test_upgrade_from_28_rebuilds_routine_occurrences_behind_one_backup(tmp_path: Path) -> None:
    """Step 29 rebuilds routine_occurrences on the widened DDL beside one v28 backup that
    validates at 28; every row of every table, each occurrence and attempt included, is kept.
    The widened DDL differs from the old one in the kind CHECK only."""
    widened, narrow = (
        "CHECK(kind IN ('schedule','manual','decision'))",
        "CHECK(kind IN ('schedule','manual'))",
    )
    current = storage.ROUTINE_SCHEMAS["routine_occurrences"]
    assert current.count(widened) == 1
    assert (
        current.replace(widened, narrow) == storage.ROUTINE_SCHEMAS_BEFORE_29["routine_occurrences"]
    )
    database = _v28_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert len(before["routine_occurrences"]) == 3 and len(before["task_attempts"]) == 2

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v28-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (43,)
        assert _occurrence_ddl(connection) == storage.ROUTINE_SCHEMAS["routine_occurrences"]
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v28-backup-*.sqlite3")
    assert len(list(tmp_path.glob("*backup*"))) == 15  # Backups at v28 through v42.
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (28,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _occurrence_ddl(saved) == storage.ROUTINE_SCHEMAS_BEFORE_29["routine_occurrences"]
        assert _every_row(saved) == before


def test_a_decision_occurrence_is_refused_at_28_and_kept_at_29(tmp_path: Path) -> None:
    """The v28 CHECK refuses a decision occurrence; once upgraded, the store takes one, with or
    without a request hash, and reopens with it."""
    database = _v28_store(tmp_path)
    with contextlib.closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            _decision(connection, "e" * 64)
        connection.rollback()

    AppState(tmp_path / "state.json", Prefs())
    for request_hash in ("e" * 64, None):
        with contextlib.closing(sqlite3.connect(database)) as connection:
            connection.execute("DELETE FROM routine_occurrences WHERE kind='decision'")
            _decision(connection, request_hash)
            connection.commit()
        AppState(tmp_path / "state.json", Prefs())  # the upgraded store still validates
        with contextlib.closing(sqlite3.connect(database)) as connection:
            assert connection.execute(
                "SELECT request_hash FROM routine_occurrences WHERE kind='decision'"
            ).fetchall() == [(request_hash,)]


def test_build_at_28_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v28 build and the v28 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v28_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v28-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v28 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V28_BUILD):
            pytest.fail("The upgraded store was admitted under v28")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V28_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (28,)
        assert _every_row(connection) == before
