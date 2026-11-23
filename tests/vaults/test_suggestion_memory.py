"""Inbox suggestions outlive a restart: the suggester's run and map, and the host's started keys.

Each test builds the suggester or the host over a real store, lets it save, then builds a second
one over the same store, as the next launch does, and reads what it loaded.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.persistence.test_storage import _workspace
from tests.vaults.test_capture_suggest import (
    HANG_GUARD_S,
    REF_A,
    REF_B,
    STAMP,
    WALL,
    WS,
    FakeCaptures,
    FakeHost,
    answer,
    ended,
    expected,
    listing,
    settle,
    silent,
)
from viki_agent import protection
from viki_agent.decision_loop.inbox_host import SuggestionHost
from viki_agent.state import AppState, Prefs
from viki_agent.storage import SessionStore
from viki_agent.vaults import suggestion_memory
from viki_agent.vaults.capture_reply import Suggestion
from viki_agent.vaults.capture_suggest import CaptureSuggester, SuggestionRun
from viki_agent.vaults.suggestion_memory import SuggestionMemory

OTHER = "22222222-2222-4222-8222-222222222222"
KEY = "a" * 64


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return AppState(tmp_path / "state.json", Prefs()).store


def suggester(store: SessionStore, host: Any) -> tuple[CaptureSuggester, set[asyncio.Task[None]]]:
    tasks: set[asyncio.Task[None]] = set()
    built = CaptureSuggester(
        host,
        FakeCaptures(listing()),
        tasks=tasks,
        memory=SuggestionMemory(store, wall=lambda: WALL),
        wall=lambda: WALL,
    )
    return built, tasks


def rows(store: SessionStore) -> list[tuple[str, str]]:
    with contextlib.closing(sqlite3.connect(store.path)) as connection:
        return connection.execute(
            "SELECT workspace_id,part FROM inbox_suggestions ORDER BY workspace_id,part"
        ).fetchall()


class FakeSuggester:
    """The suggester as the host reaches it; every start answers the same done run."""

    def __init__(self) -> None:
        self.run = SuggestionRun("r1", WS, "s1", "done", STAMP, STAMP)

    def run_for(self, workspace_id: str) -> SuggestionRun | None:
        return self.run

    def suggestions(self, workspace_id: str) -> Mapping[str, Suggestion]:
        return {}

    async def start(self, workspace_id: str) -> SuggestionRun:
        return self.run


async def test_a_done_run_and_its_suggestions_load_after_a_restart(store: SessionStore) -> None:
    first, tasks = suggester(store, FakeHost(answer(REF_A, REF_B)))
    run = await first.start(WS)
    await settle(tasks)
    assert rows(store) == [(WS, "suggestions")]

    second, _ = suggester(store, FakeHost())
    assert second.run_for(WS) == ended(run, suggested=2)
    assert second.suggestions(WS) == expected(REF_A, REF_B)
    assert second.run_for(OTHER) is None


async def test_a_run_still_going_at_close_loads_as_failed_and_keeps_the_earlier_map(
    store: SessionStore,
) -> None:
    host = FakeHost(answer(REF_A), silent)
    first, tasks = suggester(store, host)
    await first.start(WS)
    await settle(tasks)
    running = await first.start(WS)
    async with asyncio.timeout(HANG_GUARD_S):
        await host.sent.wait()

    second, _ = suggester(store, FakeHost())  # built while the first run still goes
    assert second.run_for(WS) == ended(running, "app_closed")
    assert second.suggestions(WS) == expected(REF_A)
    for task in list(tasks):
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def test_a_run_in_another_vault_does_not_hide_a_vaults_own_run(store: SessionStore) -> None:
    first, tasks = suggester(store, FakeHost(answer(REF_A), answer(REF_B)))
    own = await first.start(WS)
    await settle(tasks)
    await first.start(OTHER)
    await settle(tasks)
    assert first.run_for(WS) == ended(own, suggested=1)

    second, _ = suggester(store, FakeHost())
    assert second.run_for(WS) == ended(own, suggested=1)
    assert second.run_for(OTHER) is not None


async def test_a_saved_value_out_of_shape_loads_nothing_for_its_vault(store: SessionStore) -> None:
    memory = SuggestionMemory(store, wall=lambda: WALL)
    await memory.save(WS, "suggestions", lambda: {"run": None, "suggestions": [{"ref": REF_A}]})
    await memory.save(OTHER, "suggestions", lambda: {"run": {"run_id": "x"}, "suggestions": []})

    second, _ = suggester(store, FakeHost())
    assert second.run_for(WS) is None and second.suggestions(WS) == {}
    assert second.run_for(OTHER) is None


async def test_a_body_that_does_not_open_is_left_out(store: SessionStore) -> None:
    memory = SuggestionMemory(store, wall=lambda: WALL)
    await memory.save(WS, "started", lambda: [[KEY, "r1"]])
    with contextlib.closing(sqlite3.connect(store.path)) as connection, connection:
        # A body sealed for the other part does not open under this row's context.
        connection.execute(
            "UPDATE inbox_suggestions SET body=? WHERE workspace_id=?",
            (protection.protect(b"[]", context=b"elsewhere"), WS),
        )
    assert memory.load("started") == {}


async def test_the_hosts_started_keys_load_after_a_restart(store: SessionStore) -> None:
    first = SuggestionHost(FakeSuggester(), memory=SuggestionMemory(store))
    assert first.persistent
    assert await first.start(WS, KEY) == "r1"
    await first.close()  # awaits the save still writing

    second = SuggestionHost(FakeSuggester(), memory=SuggestionMemory(store))
    assert second.persistent
    assert second.read(WS).started == {KEY: "r1"}
    assert rows(store) == [(WS, "started")]


def test_a_host_without_a_readable_store_is_not_persistent(
    store: SessionStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not SuggestionHost(FakeSuggester()).persistent

    def refuse(_: SessionStore) -> None:
        raise protection.ProtectionError

    monkeypatch.setattr(suggestion_memory, "require_private_store", refuse)
    host = SuggestionHost(FakeSuggester(), memory=SuggestionMemory(store))
    assert not host.persistent
    assert host.read(WS).started == {}


async def test_unregistering_a_vault_forgets_its_suggestions(store: SessionStore) -> None:
    store.update_workspace(WS, lambda current, rows: _workspace(WS))
    memory = SuggestionMemory(store, wall=lambda: WALL)
    await memory.save(WS, "started", lambda: [[KEY, "r1"]])
    await memory.save(WS, "suggestions", lambda: {"run": None, "suggestions": []})
    assert len(rows(store)) == 2

    store.update_workspace(WS, lambda current, rows: None)
    assert rows(store) == []
