"""Schema step 25: the table of proposed wordings for Decision's questions."""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import (
    _SCOPE,
    _STAMP,
    OTHER,
    SID,
    _downgrade_decision_proposals,
)
from tests.persistence.test_storage_schema_decision import (
    _EPISODE,
    _KEY,
    _LATER,
    _episode,
    _indexes,
    _insert,
    _mission,
    _tables,
)
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

_PROPOSAL = "00000000-0000-4000-8000-000000000061"
# The versions a v24 build recognises: its header check refuses any other before SQLite opens.
_V24_BUILD = tuple(range(1, 25))
_PREFERENCE = {
    "scope": SID,
    "name": "mute:inbox",
    "value": "1",
    "revision": 2,
    "updated_at": _STAMP,
}
_ITEM = {
    "scope": SID,
    "item_key": "c" * 64,
    "candidate": "inbox",
    "state": "open",
    "level": "list",
    "timing": "now",
    "episode_id": _EPISODE,
    "body": b"\x00sealed-item\xff",
    "revision": 3,
    "created_at": _STAMP,
    "updated_at": _STAMP,
}
_CALL = {
    "call_id": OTHER,
    "scope": SID,
    "created_at": _STAMP,
    "item_key": "c" * 64,
    "outcome": "answered",
    "body": b"\x00sealed-call\xff",
}


def _every_row(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    """Every legacy column and row; schema40 provenance has its own migration assertions."""
    names = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT GLOB 'sqlite_*'"
    ).fetchall()
    columns = {
        name: ",".join(
            row[1]
            for row in connection.execute(f"PRAGMA table_info({name})")
            if row[1] != "launch_origin"
        )
        for (name,) in names
    }
    rows = {
        name: connection.execute(f"SELECT {columns[name]} FROM {name} ORDER BY 1").fetchall()
        for (name,) in names
    }
    # These additive tables have no rows in historical fixtures.
    rows.setdefault("session_bot_memory", [])
    rows.setdefault("bot_threads", [])
    rows.setdefault("bot_thread_sessions", [])
    for name in storage.BOT_CODE_SCHEMAS:
        rows.setdefault(name, [])
    for name in storage.INBOX_SUGGESTION_SCHEMAS:
        rows.setdefault(name, [])
    return rows


def _v24_store(tmp_path: Path) -> Path:
    """A v24 store as the last such build left it: a session, a draft and a row in each of the
    six decision tables."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v24-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        _downgrade_decision_proposals(connection)
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        connection.execute("PRAGMA user_version=24")
        connection.commit()
    return state.database_path


def test_fresh_store_has_the_proposal_table(tmp_path: Path) -> None:
    assert storage.SCHEMA_VERSION == 41
    assert storage.DECISION_PROPOSALS_SCHEMA_STEP == 25
    assert storage.DECISION_LOOP_SCHEMA_STEP == 24
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (41,)
        assert _tables(connection, "decision_proposals") == storage.DECISION_PROPOSAL_SCHEMAS
        assert _indexes(connection, "decision_proposals") == [
            ("pk", ["proposal_id"]),
            ("u", ["scope", "created_at", "proposal_id"]),  # a scope read in order, unsorted
        ]
        assert connection.execute("SELECT count(*) FROM decision_proposals").fetchone() == (0,)


def test_v24_store_upgrades_with_backup(tmp_path: Path) -> None:
    """Step 25 adds the proposal table, empty, beside a v24 backup that validates; every row of
    every other table, the six decision tables included, stays as it was."""
    database = _v24_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        assert _tables(source, "decision_proposals") == {}
        before = _every_row(source)
    assert all(before[table] for table in ("drafts", *storage.DECISION_LOOP_SCHEMAS))

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v24-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (41,)
        assert _tables(connection, "decision_proposals") == storage.DECISION_PROPOSAL_SCHEMAS
        assert _every_row(connection) == {**before, "decision_proposals": []}
    (backup,) = tmp_path.glob("app.sqlite3.v24-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (24,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (24,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_a_v24_build_refuses_v25_and_opens_the_v24_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v24 build and the v24 backup: that build refuses the v25 store by its
    header, before SQLite opens it, and leaves it unchanged; the backup put back in the store's
    place opens under it with every row."""
    database = _v24_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v24-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v24 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V24_BUILD):
            pytest.fail("The v25 store was admitted under v24")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V24_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (24,)
        assert _every_row(connection) == before


@pytest.mark.parametrize("phase", ["backup", "ddl", "row_loss", "commit"])
def test_a_failed_step_25_keeps_v24(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """A step 25 that fails anywhere leaves the v24 store byte for byte, beside its v24 backup
    once that is published, and the next open upgrades it."""
    database = _v24_store(tmp_path)
    before = database.read_bytes()
    connect = sqlite3.connect
    rename = storage.osfs.rename_no_replace

    class Connection(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if (phase == "ddl" and sql.startswith("CREATE TABLE decision_proposals (")) or (
                phase == "commit" and sql == "COMMIT"
            ):
                raise sqlite3.OperationalError("synthetic interrupted migration")
            result = super().execute(sql, parameters)
            if phase == "row_loss" and sql.startswith("CREATE TABLE decision_proposals ("):
                super().execute("DELETE FROM drafts")
            return result

    def publish(source: Path, destination: Path) -> None:
        if phase == "backup" and ".v24-backup-" in destination.name:
            raise OSError("synthetic backup failure")
        rename(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Connection, **kw))
        patch.setattr(storage.osfs, "rename_no_replace", publish)
        with pytest.raises(StateError) as failure:
            AppState(tmp_path / "state.json", Prefs())
    assert failure.value.code == ("corrupt" if phase == "row_loss" else "unavailable")
    assert database.read_bytes() == before
    backups = list(tmp_path.glob("app.sqlite3.v24-backup-*.sqlite3"))
    assert len(backups) == (0 if phase == "backup" else 1)
    assert not list(tmp_path.glob(".viki-migration-*"))

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v24-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (41,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)


def _proposal(**overrides: Any) -> dict[str, Any]:
    """A decision_proposals row inside every check: an open proposal."""
    row = {
        "proposal_id": _PROPOSAL,
        "scope": SID,
        "name": "worth_note",
        "proposed_sha256": "e" * 64,
        "body": b"\x00sealed\xff",
        "created_at": _STAMP,
        "updated_at": _STAMP,
        "approved_at": None,
        "rejected_at": None,
        "reason": None,
    }
    return {**row, **overrides}


# An approval undone by a rejection keeps both stamps.
_UNDONE = {"approved_at": _STAMP, "rejected_at": _LATER}


@pytest.mark.parametrize(
    ("row", "valid"),
    [
        # A name is 1 to 32 lower-case letters and underscores.
        pytest.param(_proposal(name=""), _proposal(name="x"), id="name_empty"),
        pytest.param(_proposal(name="x" * 33), _proposal(name="x" * 32), id="name_long"),
        pytest.param(_proposal(name="Worth_note"), _proposal(), id="name_case"),
        pytest.param(_proposal(name="worth-note"), _proposal(), id="name_dash"),
        pytest.param(_proposal(name="note2"), _proposal(name="note_"), id="name_digit"),
        # The proposed wording's hash: 64 lower-case hex.
        pytest.param(_proposal(proposed_sha256="E" * 64), _proposal(), id="sha_case"),
        pytest.param(_proposal(proposed_sha256="e" * 63), _proposal(), id="sha_short"),
        # The body: 2 to 8,192 bytes.
        pytest.param(_proposal(body=b"x"), _proposal(body=b"xy"), id="body_short"),
        pytest.param(_proposal(body=b"x" * 8193), _proposal(body=b"x" * 8192), id="body_long"),
        # Ids and stamps by their length.
        pytest.param(_proposal(proposal_id=_PROPOSAL[:-1]), _proposal(), id="proposal_id"),
        pytest.param(_proposal(scope=SID[:-1]), _proposal(), id="scope"),
        pytest.param(_proposal(created_at=_STAMP[:-1]), _proposal(), id="created_at"),
        pytest.param(
            _proposal(approved_at=_STAMP[:-1]), _proposal(approved_at=_STAMP), id="approved_at"
        ),
        pytest.param(
            _proposal(rejected_at=_LATER[:-1]), _proposal(rejected_at=_LATER), id="rejected_at"
        ),
        # A reason of 1 to 200 characters, and only with a rejection.
        pytest.param(
            _proposal(reason="Too wordy"),
            _proposal(reason="Too wordy", **_UNDONE),
            id="reason_alone",
        ),
        pytest.param(
            _proposal(reason="Too wordy", approved_at=_STAMP),
            _proposal(reason="Too wordy", approved_at=_STAMP, rejected_at=_LATER),
            id="reason_with_an_approval_alone",
        ),
        pytest.param(
            _proposal(reason="", **_UNDONE), _proposal(reason="x", **_UNDONE), id="reason_empty"
        ),
        pytest.param(
            _proposal(reason="x" * 201, rejected_at=_LATER),
            _proposal(reason="x" * 200, rejected_at=_LATER),
            id="reason_long",
        ),
        pytest.param(_proposal(updated_at=_STAMP[:-1]), _proposal(), id="updated_at"),
    ],
)
def test_proposals_outside_the_checks_are_refused(
    tmp_path: Path, row: dict[str, Any], valid: dict[str, Any]
) -> None:
    database = AppState(tmp_path / "state.json", Prefs()).database_path
    with contextlib.closing(sqlite3.connect(database)) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert(connection, "decision_proposals", row)
        assert connection.execute("SELECT count(*) FROM decision_proposals").fetchone() == (0,)
        _insert(connection, "decision_proposals", valid)  # the same row within bounds is admitted
