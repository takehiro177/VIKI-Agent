"""Store accessors for session roots and worktrees (schema step 15)."""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from viki_agent import storage
from viki_agent.state import AppState, Prefs
from viki_agent.storage import SessionStore, StateError

_WORKSPACE = "00000000-0000-4000-8000-000000000101"
_OID = "a" * 40
_JOIN_GUARD_S = 10  # a bound on a race that settles in milliseconds, never on real work


def _id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def _stamp(day: int) -> str:
    return f"2026-10-{day:02d}T00:00:00.000Z"


def _store(tmp_path: Path) -> SessionStore:
    return AppState(tmp_path / "state.json", Prefs())._store


def _root(session: int, **overrides: Any) -> dict[str, Any]:
    row = {
        "session_id": _id(session),
        "workspace_id": _WORKSPACE,
        "worktree_id": None,
        "display_root": "<repo>",
        "created_at": _stamp(1),
    }
    return {**row, **overrides}


def _worktree(n: int, **overrides: Any) -> dict[str, Any]:
    row = {
        "worktree_id": _id(n),
        "workspace_id": _WORKSPACE,
        "path": f"<repo>/.worktrees/{n}",
        "branch": f"viki/{n}",
        "base_oid": _OID,
        "state": "creating",
        "created_at": _stamp(1),
        "updated_at": _stamp(1),
    }
    return {**row, **overrides}


def _code(call: Callable[[], object]) -> str:
    with pytest.raises(StateError) as failure:
        call()
    return failure.value.code


def _race(*calls: Callable[[], object]) -> list[str]:
    """Run each call on its own thread, released together; answer "ok" or each error code."""
    barrier = threading.Barrier(len(calls))
    outcomes: list[str] = []
    lock = threading.Lock()

    def run(call: Callable[[], object]) -> None:
        barrier.wait()
        try:
            call()
            outcome = "ok"
        except StateError as exc:
            outcome = exc.code
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_JOIN_GUARD_S)
        assert not thread.is_alive()
    return sorted(outcomes)


# Session roots.


def test_put_session_root_inserts_once_then_answers_false_for_an_equal_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_session_root(_id(1)) is None
    assert store.put_session_root(_root(1)) is True
    assert store.get_session_root(_id(1)) == _root(1)
    # Equal in workspace, worktree and root; a later stamp does not replace the first.
    assert store.put_session_root(_root(1, created_at=_stamp(2))) is False
    assert store.get_session_root(_id(1)) == _root(1)


@pytest.mark.parametrize(
    "change",
    [
        {"workspace_id": _id(102)},
        {"worktree_id": _id(7)},
        {"display_root": "<repo>/other"},
    ],
)
def test_put_session_root_refuses_a_different_root(tmp_path: Path, change: dict[str, Any]) -> None:
    store = _store(tmp_path)
    assert store.put_session_root(_root(1)) is True
    assert _code(lambda: store.put_session_root(_root(1, **change))) == "conflict"
    assert store.get_session_root(_id(1)) == _root(1)


@pytest.mark.parametrize(
    "row",
    [
        {k: v for k, v in _root(1).items() if k != "created_at"},
        {**_root(1), "extra": 1},
        _root(1, session_id=_id(1)[:-1]),
        _root(1, display_root="ab"),
        _root(1, created_at="2026-10-01"),
        _root(1, worktree_id="short"),
    ],
)
def test_put_session_root_refuses_wrong_keys_and_values(
    tmp_path: Path, row: dict[str, Any]
) -> None:
    store = _store(tmp_path)
    assert _code(lambda: store.put_session_root(row)) == "invalid_input"
    assert store.list_session_roots(10) == ([], False)


def test_list_session_roots_is_newest_first_with_ties_by_id_and_says_more(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    for session, day in ((3, 1), (2, 2), (1, 2), (4, 3)):
        store.put_session_root(_root(session, created_at=_stamp(day)))
    rows, more = store.list_session_roots(3)
    assert [row["session_id"] for row in rows] == [_id(4), _id(1), _id(2)]
    assert more is True
    assert rows[0] == _root(4, created_at=_stamp(3))
    rows, more = store.list_session_roots(4)
    assert [row["session_id"] for row in rows] == [_id(4), _id(1), _id(2), _id(3)]
    assert more is False


def test_session_roots_in_worktree_lists_sorted_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for session, worktree in ((3, _id(7)), (1, _id(7)), (2, _id(8)), (4, None)):
        store.put_session_root(_root(session, worktree_id=worktree))
    assert store.session_roots_in_worktree(_id(7)) == [_id(1), _id(3)]
    assert store.session_roots_in_worktree(_id(9)) == []


def test_session_roots_in_folder_skips_worktree_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put_session_root(_root(3))
    store.put_session_root(_root(1))
    store.put_session_root(_root(2, worktree_id=_id(7)))
    store.put_session_root(_root(4, workspace_id=_id(102)))
    assert store.session_roots_in_folder(_WORKSPACE) == [_id(1), _id(3)]


# Worktrees.


def test_insert_worktree_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_worktree(_id(1)) is None
    store.insert_worktree(_worktree(1), limit=4)
    assert store.get_worktree(_id(1)) == _worktree(1)


@pytest.mark.parametrize(
    "row",
    [
        {k: v for k, v in _worktree(1).items() if k != "branch"},
        {**_worktree(1), "extra": 1},
        _worktree(1, state="ready"),
        _worktree(1, base_oid="a" * 39),
        _worktree(1, base_oid="G" + "a" * 39),
        _worktree(1, branch=""),
    ],
)
def test_insert_worktree_refuses_wrong_keys_state_and_values(
    tmp_path: Path, row: dict[str, Any]
) -> None:
    store = _store(tmp_path)
    assert _code(lambda: store.insert_worktree(row, limit=4)) == "invalid_input"
    assert store.list_worktrees(10) == ([], False)


def test_insert_worktree_refuses_a_duplicate_path_or_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=4)
    duplicate_path = _worktree(2, path=_worktree(1)["path"])
    assert _code(lambda: store.insert_worktree(duplicate_path, limit=4)) == "invalid_input"
    duplicate_id = _worktree(1, path="<repo>/.worktrees/other")
    assert _code(lambda: store.insert_worktree(duplicate_id, limit=4)) == "invalid_input"
    assert [row["worktree_id"] for row in store.list_worktrees(10)[0]] == [_id(1)]


def test_the_worktree_cap_counts_only_rows_not_removed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=2)
    store.insert_worktree(_worktree(2), limit=2)
    assert _code(lambda: store.insert_worktree(_worktree(3), limit=2)) == "capacity"
    store.update_worktree_state(_id(1), "creating", "removed", _stamp(2))
    store.insert_worktree(_worktree(3), limit=2)
    # Every other state still counts.
    for state in ("ready", "removing", "kept"):
        store.update_worktree_state(_id(3), "creating", state, _stamp(2))
        assert _code(lambda: store.insert_worktree(_worktree(4), limit=2)) == "capacity"
        store.delete_worktree(_id(3), expected_state=state)
        store.insert_worktree(_worktree(3), limit=2)


def test_list_worktrees_is_newest_first_with_ties_by_id_and_says_more(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for n, day in ((3, 1), (2, 2), (1, 2), (4, 3)):
        store.insert_worktree(_worktree(n, created_at=_stamp(day)), limit=8)
    rows, more = store.list_worktrees(3)
    assert [row["worktree_id"] for row in rows] == [_id(4), _id(1), _id(2)]
    assert more is True
    assert rows[0] == _worktree(4, created_at=_stamp(3))
    rows, more = store.list_worktrees(4)
    assert [row["worktree_id"] for row in rows] == [_id(4), _id(1), _id(2), _id(3)]
    assert more is False


def test_update_worktree_state_changes_state_and_stamp(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=4)
    store.update_worktree_state(_id(1), "creating", "ready", _stamp(5))
    assert store.get_worktree(_id(1)) == _worktree(1, state="ready", updated_at=_stamp(5))


def test_update_worktree_state_tells_not_found_from_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=4)
    assert (
        _code(lambda: store.update_worktree_state(_id(2), "creating", "ready", _stamp(2)))
        == "not_found"
    )
    assert (
        _code(lambda: store.update_worktree_state(_id(1), "ready", "removing", _stamp(2)))
        == "conflict"
    )
    assert store.get_worktree(_id(1)) == _worktree(1)


def test_update_worktree_state_refuses_a_state_outside_the_check(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=4)
    assert (
        _code(lambda: store.update_worktree_state(_id(1), "creating", "gone", _stamp(2)))
        == "invalid_input"
    )
    assert store.get_worktree(_id(1)) == _worktree(1)


def test_delete_worktree_tells_not_found_from_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=4)
    assert _code(lambda: store.delete_worktree(_id(2), expected_state="creating")) == "not_found"
    assert _code(lambda: store.delete_worktree(_id(1), expected_state="ready")) == "conflict"
    assert store.get_worktree(_id(1)) == _worktree(1)
    store.delete_worktree(_id(1), expected_state="creating")
    assert store.get_worktree(_id(1)) is None


def _removed(store: SessionStore, n: int) -> None:
    store.insert_worktree(_worktree(n), limit=64)
    store.update_worktree_state(_id(n), "creating", "removed", _stamp(2))


def test_revive_worktree_moves_a_removed_row_back_to_creating(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _removed(store, 1)
    store.revive_worktree(_id(1), _stamp(5), limit=4)
    assert store.get_worktree(_id(1)) == _worktree(1, updated_at=_stamp(5))


def test_revive_worktree_refuses_at_the_cap_and_leaves_the_row_removed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _removed(store, 1)
    store.insert_worktree(_worktree(2), limit=2)
    store.insert_worktree(_worktree(3), limit=2)
    assert _code(lambda: store.revive_worktree(_id(1), _stamp(5), limit=2)) == "capacity"
    assert store.get_worktree(_id(1)) == _worktree(1, state="removed", updated_at=_stamp(2))
    # Every state but `removed` counts; the revived row's own slot is the one it takes.
    for state in ("ready", "removing", "kept"):
        store.update_worktree_state(_id(3), "creating", state, _stamp(3))
        assert _code(lambda: store.revive_worktree(_id(1), _stamp(5), limit=2)) == "capacity"
        store.update_worktree_state(_id(3), state, "creating", _stamp(3))
    store.update_worktree_state(_id(3), "creating", "removed", _stamp(3))
    store.revive_worktree(_id(1), _stamp(5), limit=2)
    assert store.get_worktree(_id(1)) == _worktree(1, updated_at=_stamp(5))


@pytest.mark.parametrize("state", ["creating", "ready", "removing", "kept"])
def test_revive_worktree_refuses_a_row_not_removed_as_a_conflict(
    tmp_path: Path, state: str
) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=4)
    if state != "creating":
        store.update_worktree_state(_id(1), "creating", state, _stamp(2))
    before = store.get_worktree(_id(1))
    assert _code(lambda: store.revive_worktree(_id(1), _stamp(5), limit=4)) == "conflict"
    assert store.get_worktree(_id(1)) == before


def test_revive_worktree_refuses_an_absent_id_as_not_found(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _removed(store, 1)
    assert _code(lambda: store.revive_worktree(_id(2), _stamp(5), limit=4)) == "not_found"
    assert store.get_worktree(_id(1)) == _worktree(1, state="removed", updated_at=_stamp(2))


# Races on a real store: each decision is one write transaction.


def test_two_inserts_racing_for_the_last_slot_admit_exactly_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=2)
    outcomes = _race(
        lambda: store.insert_worktree(_worktree(2), limit=2),
        lambda: store.insert_worktree(_worktree(3), limit=2),
    )
    assert outcomes == ["capacity", "ok"]
    rows, more = store.list_worktrees(10)
    assert len(rows) == 2 and more is False


def test_two_state_changes_racing_from_ready_admit_exactly_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.insert_worktree(_worktree(1), limit=4)
    store.update_worktree_state(_id(1), "creating", "ready", _stamp(2))
    outcomes = _race(
        lambda: store.update_worktree_state(_id(1), "ready", "removing", _stamp(3)),
        lambda: store.update_worktree_state(_id(1), "ready", "kept", _stamp(4)),
    )
    assert outcomes == ["conflict", "ok"]
    row = store.get_worktree(_id(1))
    assert row is not None
    assert (row["state"], row["updated_at"]) in {("removing", _stamp(3)), ("kept", _stamp(4))}


def test_a_revive_and_an_insert_racing_for_the_last_slot_admit_exactly_one(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _removed(store, 1)
    store.insert_worktree(_worktree(2), limit=2)
    outcomes = _race(
        lambda: store.revive_worktree(_id(1), _stamp(5), limit=2),
        lambda: store.insert_worktree(_worktree(3), limit=2),
    )
    assert outcomes == ["capacity", "ok"]
    rows, _ = store.list_worktrees(10)
    assert sum(row["state"] != "removed" for row in rows) == 2


def test_two_revives_of_one_row_admit_exactly_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _removed(store, 1)
    outcomes = _race(
        lambda: store.revive_worktree(_id(1), _stamp(5), limit=4),
        lambda: store.revive_worktree(_id(1), _stamp(6), limit=4),
    )
    assert outcomes == ["conflict", "ok"]
    row = store.get_worktree(_id(1))
    assert row is not None
    assert (row["state"], row["updated_at"]) in {("creating", _stamp(5)), ("creating", _stamp(6))}


def test_session_root_paths_reads_recorded_ids_in_chunks_of_root_lookup_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bulk lookup answers only the ids with a row, ignores repeats and unknown ids, and runs
    one statement per ROOT_LOOKUP_CHUNK distinct ids."""
    monkeypatch.setattr(storage, "ROOT_LOOKUP_CHUNK", 2)
    store = _store(tmp_path)
    for session in (1, 2, 3, 4, 5):
        store.put_session_root(_root(session, display_root=f"<repo>/{session}"))
    statements: list[int] = []
    opened = store._connection

    @contextlib.contextmanager
    def counted(**kwargs: Any) -> Iterator[sqlite3.Connection]:
        statements.append(1)
        with opened(**kwargs) as connection:
            yield connection

    monkeypatch.setattr(store, "_connection", counted)
    found = store.session_root_paths([_id(5), _id(1), _id(9), _id(1), _id(3), _id(8)])
    assert found == {_id(1): "<repo>/1", _id(3): "<repo>/3", _id(5): "<repo>/5"}
    assert len(statements) == 3  # five distinct ids, two a statement
    statements.clear()
    assert store.session_root_paths([]) == {}
    assert statements == []
