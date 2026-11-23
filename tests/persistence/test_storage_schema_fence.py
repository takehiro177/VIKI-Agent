"""Schema steps 26, 27, 28, 30, 31, 32, 33, 34, 35, 36 and 38: fences only, with no table change
(ADR-059, ADR-061, ADR-064, ADR-066, ADR-067, ADR-068, ADR-069, ADR-070, ADR-071, ADR-072).
Upgrades from before 29 also cross step 29, which rebuilds routine_occurrences (ADR-065).
Upgrades from before 37 also cross step 37, which adds session_bot_memory."""

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
    SID,
    _downgrade_bot_code,
    _downgrade_routine_occurrences,
)
from tests.persistence.test_storage_schema_decision import _KEY, _episode, _insert, _mission
from tests.persistence.test_storage_schema_proposals import (
    _CALL,
    _ITEM,
    _PREFERENCE,
    _every_row,
    _proposal,
)
from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

# The versions a v25 build recognises: its header check refuses any other before SQLite opens.
_V25_BUILD = tuple(range(1, 26))

# The versions a v26 build recognises: its header check refuses any other before SQLite opens.
_V26_BUILD = tuple(range(1, 27))

# The versions a v27 build recognises: its header check refuses any other before SQLite opens.
_V27_BUILD = tuple(range(1, 28))

# The versions a v29 build recognises: its header check refuses any other before SQLite opens.
_V29_BUILD = tuple(range(1, 30))

# The versions a v30 build recognises: its header check refuses any other before SQLite opens.
_V30_BUILD = tuple(range(1, 31))

# The versions a v31 build recognises: its header check refuses any other before SQLite opens.
_V31_BUILD = tuple(range(1, 32))

# The versions a v32 build recognises: its header check refuses any other before SQLite opens.
_V32_BUILD = tuple(range(1, 33))

# The versions a v33 build recognises: its header check refuses any other before SQLite opens.
_V33_BUILD = tuple(range(1, 34))

# The versions a v34 build recognises: its header check refuses any other before SQLite opens.
_V34_BUILD = tuple(range(1, 35))

# The versions a v35 build recognises: its header check refuses any other before SQLite opens.
_V35_BUILD = tuple(range(1, 36))

# Every table a v25 store already holds, the change proposal family included.
_NONEMPTY = ("drafts", "decision_proposals", *storage.DECISION_LOOP_SCHEMAS)


def _v25_store(tmp_path: Path) -> Path:
    """A v25 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Steps 26, 27 and 28 touch no table, so this is a fresh store
    with routine_occurrences on its pre-29 DDL and PRAGMA user_version set back to 25."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v25-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_routine_occurrences(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=25")
        connection.commit()
    return state.database_path


def _v26_store(tmp_path: Path) -> Path:
    """A v26 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Steps 27 and 28 touch no table, so this is a fresh store
    with routine_occurrences on its pre-29 DDL and PRAGMA user_version set back to 26."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v26-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_routine_occurrences(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=26")
        connection.commit()
    return state.database_path


def _v27_store(tmp_path: Path) -> Path:
    """A v27 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Step 28 touches no table, so this is a fresh store with
    routine_occurrences on its pre-29 DDL and PRAGMA user_version set back to 27."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v27-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_routine_occurrences(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=27")
        connection.commit()
    return state.database_path


def _v29_store(tmp_path: Path) -> Path:
    """A v29 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Step 30 touches no table, so this is a fresh store with
    PRAGMA user_version set back to 29; routine_occurrences already has its step-29 DDL."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v29-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_bot_code(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=29")
        connection.commit()
    return state.database_path


def _v30_store(tmp_path: Path) -> Path:
    """A v30 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Step 31 touches no table, so this is a fresh store with
    PRAGMA user_version set back to 30."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v30-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_bot_code(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=30")
        connection.commit()
    return state.database_path


def _v31_store(tmp_path: Path) -> Path:
    """A v31 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Step 32 touches no table, so this is a fresh store with
    PRAGMA user_version set back to 31."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v31-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_bot_code(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=31")
        connection.commit()
    return state.database_path


def _v32_store(tmp_path: Path) -> Path:
    """A v32 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Step 33 touches no table, so this is a fresh store with
    PRAGMA user_version set back to 32."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v32-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_bot_code(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=32")
        connection.commit()
    return state.database_path


def _v33_store(tmp_path: Path) -> Path:
    """A v33 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Step 34 touches no table, so this is a fresh store with
    PRAGMA user_version set back to 33."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v33-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_bot_code(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=33")
        connection.commit()
    return state.database_path


def _v34_store(tmp_path: Path) -> Path:
    """A v34 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Step 35 touches no table, so this is a fresh store with
    PRAGMA user_version set back to 34."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v34-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_bot_code(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=34")
        connection.commit()
    return state.database_path


def _v35_store(tmp_path: Path) -> Path:
    """A v35 store as the last such build left it: a session, a draft, a proposal and a row in
    each of the six decision tables. Step 36 touches no table, so this is a fresh store with
    PRAGMA user_version set back to 35."""
    state = AppState(tmp_path / "state.json", Prefs())
    state.remember(SID, mode="plan", effort="high", model="preserved-v35-model")
    with contextlib.closing(sqlite3.connect(state.database_path)) as connection:
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?,?,?)", (_SCOPE, SID, 1, b"x", 2, 1, _STAMP)
        )
        _insert(connection, "decision_episodes", _episode())
        _insert(connection, "decision_preferences", _PREFERENCE)
        _insert(connection, "decision_items", _ITEM)
        _insert(connection, "decision_calls", _CALL)
        _insert(connection, "decision_missions", _mission())
        _insert(connection, "decision_key", _KEY)
        _insert(connection, "decision_proposals", _proposal())
        _downgrade_bot_code(connection)
        connection.execute("DROP TABLE IF EXISTS session_bot_memory")
        connection.execute("DROP TABLE IF EXISTS bot_thread_sessions")
        connection.execute("DROP TABLE IF EXISTS bot_threads")
        connection.execute("PRAGMA user_version=35")
        connection.commit()
    return state.database_path


def test_upgrade_from_25_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 26 changes no table: the store is stamped straight from 25, beside a v25 backup that
    validates; every row of every table, the question and change proposal families included,
    stays as it was."""
    database = _v25_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v25-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v25-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (25,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (25,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_25_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v25 build and the v25 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v25_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v25-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v25 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V25_BUILD):
            pytest.fail("The upgraded store was admitted under v25")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V25_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (25,)
        assert _every_row(connection) == before


def test_upgrade_from_26_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 27 changes no table: the store is stamped straight from 26, beside a v26 backup that
    validates; every row of every table, the question and change proposal families included,
    stays as it was."""
    database = _v26_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v26-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v26-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (26,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (26,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_26_refuses_27(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rollback is the v26 build and the v26 backup: that build refuses the upgraded store by its
    header, before SQLite opens it, and leaves it unchanged; the backup put back in the store's
    place opens under it with every row."""
    database = _v26_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v26-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v26 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V26_BUILD):
            pytest.fail("The upgraded store was admitted under v26")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V26_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (26,)
        assert _every_row(connection) == before


def test_upgrade_from_27_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 28 changes no table: the store is stamped straight from 27, beside a v27 backup that
    validates; every row of every table, the question and change proposal families included,
    stays as it was."""
    database = _v27_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v27-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v27-backup-*.sqlite3")
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (27,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (27,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_27_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v27 build and the v27 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v27_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v27-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v27 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V27_BUILD):
            pytest.fail("The upgraded store was admitted under v27")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V27_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (27,)
        assert _every_row(connection) == before


def test_upgrade_from_29_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 30 changes no table: the store is stamped straight from 29, beside one v29 backup
    that validates; every row of every table, the question and change proposal families
    included, stays as it was."""
    database = _v29_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v29-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v29-backup-*.sqlite3")
    assert len(list(tmp_path.glob("*backup*"))) == 13  # Backups at v29 through v41.
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (29,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (29,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_29_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v29 build and the v29 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v29_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v29-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v29 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V29_BUILD):
            pytest.fail("The upgraded store was admitted under v29")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V29_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (29,)
        assert _every_row(connection) == before


def test_upgrade_from_30_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 31 changes no table: the store is stamped straight from 30, beside one v30 backup
    that validates; every row of every table, the question and change proposal families
    included, stays as it was."""
    database = _v30_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v30-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v30-backup-*.sqlite3")
    assert len(list(tmp_path.glob("*backup*"))) == 12  # Backups at v30 through v41.
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (30,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (30,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_30_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v30 build and the v30 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v30_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v30-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v30 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V30_BUILD):
            pytest.fail("The upgraded store was admitted under v30")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V30_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (30,)
        assert _every_row(connection) == before


def test_upgrade_from_31_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 32 changes no table: the store is stamped straight from 31, beside one v31 backup
    that validates; every row of every table, the question and change proposal families
    included, stays as it was."""
    database = _v31_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v31-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v31-backup-*.sqlite3")
    assert len(list(tmp_path.glob("*backup*"))) == 11  # Backups at v31 through v41.
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (31,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (31,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_31_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v31 build and the v31 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v31_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v31-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v31 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V31_BUILD):
            pytest.fail("The upgraded store was admitted under v31")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V31_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (31,)
        assert _every_row(connection) == before


def test_upgrade_from_32_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 33 changes no table: the store is stamped straight from 32, beside one v32 backup
    that validates; every row of every table, the question and change proposal families
    included, stays as it was."""
    database = _v32_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v32-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v32-backup-*.sqlite3")
    assert len(list(tmp_path.glob("*backup*"))) == 10  # Backups at v32 through v41.
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (32,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (32,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_32_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v32 build and the v32 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v32_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v32-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v32 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V32_BUILD):
            pytest.fail("The upgraded store was admitted under v32")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V32_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (32,)
        assert _every_row(connection) == before


def test_upgrade_from_33_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 34 changes no table: the store is stamped straight from 33, beside one v33 backup
    that validates; every row of every table, the question and change proposal families
    included, stays as it was."""
    database = _v33_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v33-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v33-backup-*.sqlite3")
    assert len(list(tmp_path.glob("*backup*"))) == 9  # Backups at v33 through v41.
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (33,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (33,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_33_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v33 build and the v33 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v33_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v33-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v33 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V33_BUILD):
            pytest.fail("The upgraded store was admitted under v33")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V33_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (33,)
        assert _every_row(connection) == before


def test_upgrade_from_34_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 35 changes no table: the store is stamped straight from 34, beside one v34 backup
    that validates; every row of every table, the question and change proposal families
    included, stays as it was."""
    database = _v34_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v34-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v34-backup-*.sqlite3")
    assert len(list(tmp_path.glob("*backup*"))) == 8  # Backups at v34 through v41.
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (34,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (34,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_34_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v34 build and the v34 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v34_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v34-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v34 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V34_BUILD):
            pytest.fail("The upgraded store was admitted under v34")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V34_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (34,)
        assert _every_row(connection) == before


def test_upgrade_from_35_keeps_rows_behind_backup(tmp_path: Path) -> None:
    """Step 36 changes no table: the store is stamped straight from 35, beside one v35 backup
    that validates; every row of every table, the question and change proposal families
    included, stays as it was."""
    database = _v35_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    assert all(before[table] for table in _NONEMPTY)

    state = AppState(tmp_path / "state.json", Prefs())
    assert state.prefs(SID) == Prefs(mode="plan", effort="high", model="preserved-v35-model")
    with contextlib.closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (42,)
        assert _every_row(connection) == before
    (backup,) = tmp_path.glob("app.sqlite3.v35-backup-*.sqlite3")
    assert backup.name.startswith("app.sqlite3.v35-backup-") and backup.suffix == ".sqlite3"
    with contextlib.closing(sqlite3.connect(f"{backup.as_uri()}?mode=ro", uri=True)) as saved:
        state.store._validate(saved, (35,))  # the store's own check, at the version it names
        assert saved.execute("PRAGMA user_version").fetchone() == (35,)
        assert saved.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert _every_row(saved) == before


def test_build_at_35_refuses_the_upgraded_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback is the v35 build and the v35 backup: that build refuses the upgraded store by
    its header, before SQLite opens it, and leaves it unchanged; the backup put back in the
    store's place opens under it with every row."""
    database = _v35_store(tmp_path)
    with contextlib.closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source:
        before = _every_row(source)
    store = AppState(tmp_path / "state.json", Prefs()).store
    (backup,) = tmp_path.glob("app.sqlite3.v35-backup-*.sqlite3")
    upgraded = database.read_bytes()

    def refuse(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        pytest.fail("The v35 contract reached SQLite")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", refuse)
        with pytest.raises(StateError) as failure, store._connection(versions=_V35_BUILD):
            pytest.fail("The upgraded store was admitted under v35")
    assert failure.value.code == "incompatible"
    assert database.read_bytes() == upgraded

    shutil.copyfile(backup, database)
    with store._connection(versions=_V35_BUILD) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (35,)
        assert _every_row(connection) == before
