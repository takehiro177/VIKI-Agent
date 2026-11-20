"""The live-driver bound includes pending starts and is advertised in full recovery."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from tests.conversations.test_session import HANG_GUARD_S, Drivers, GatedDriver, gated, watch
from tests.test_server import EmptyCatalog
from viki_agent import server
from viki_agent.app.settings import KEYS, SettingsService
from viki_agent.config import Config
from viki_agent.drivers.base import StartError
from viki_agent.drivers.claude.control import Launch
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.events import SessionStarted, Status
from viki_agent.session import MAX_LIVE, SessionManager
from viki_agent.state import AppState, Prefs

IDS = [str(uuid.UUID(int=index)) for index in range(1, 40)]


class Holder:
    """A mutable `bind_max_live` reader: changing `.value` changes what every later admission
    and recovery check reads, without rebinding."""

    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _settings_app(tmp_path: Path, drivers: Drivers) -> tuple[SessionManager, SettingsService]:
    """`server.create_app` wired with only `settings`, the way the launcher binds
    `bind_max_live` beside `bind_limits`."""
    home, vault, data = tmp_path / "home", tmp_path / "vault", tmp_path / "data"
    (home / "sessions").mkdir(parents=True)
    vault.mkdir(parents=True)
    state = AppState(data / "state.json", Prefs(mode="auto", effort="low"))
    settings = SettingsService(
        state.store, provider_defaults=Prefs, on_chat_defaults=lambda _prefs: None
    )
    manager = SessionManager(
        config=DriverConfig(cli=("claude.exe",), vault=vault, env={}),
        state=state,
        claude_home=home,
        make_driver=drivers,
    )
    server.create_app(
        config=Config(vault=vault, claude_home=home, cli=Path("fake"), edge=None, data_dir=data),
        token="test-token",
        manager=manager,
        catalog=EmptyCatalog(home, vault),
        static_dir=None,
        settings=settings,
    )
    return manager, settings


def test_max_live_matches_the_settings_default() -> None:
    assert KEYS["chat.max_live_sessions"].default == MAX_LIVE


async def test_thirty_pending_starts_refuse_the_thirty_first_before_driver_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = GatedDriver.open

    async def pending_open(driver: GatedDriver) -> SessionStarted:
        driver.release.clear()
        return await original(driver)

    monkeypatch.setattr(GatedDriver, "open", pending_open)
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    actors = [manager.create(sid) for sid in IDS[:31]]
    seen = watch(actors[-1])
    starts = [asyncio.create_task(actor.start()) for actor in actors]
    try:
        await asyncio.wait_for(drivers.created.wait(), HANG_GUARD_S)
        assert len(drivers.made) == len(manager.live()) == 30
        assert all(not driver.alive for driver, *_ in drivers.made)
        assert [task.done() for task in starts] == [False] * 30 + [True]
        assert seen[-1]["data"] == {
            "code": "too_many_sessions",
            "message": (
                "You can run at most 30 sessions at the same time. Close one, or raise the "
                "limit in Settings › Chat, then send again."
            ),
            "recoverable": True,
        }
    finally:
        for driver, *_ in drivers.made:
            driver.release.set()
        results = await asyncio.gather(*starts)
        await manager.close_all()
    assert results == [True] * 30 + [False]


@pytest.mark.parametrize("kind", ["new", "resume", "fork"])
async def test_history_does_not_take_slots_and_explicit_starts_share_released_slot(
    tmp_path: Path, kind: str
) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    manager.bind_max_live(lambda: 8)
    actors = [manager.create(sid) for sid in IDS[:8]]
    for actor in actors:
        assert await actor.start()
    history = [manager.open(sid) for sid in IDS[8:20]]
    assert all(not actor.live for actor in history)
    assert len(drivers.made) == 8
    waiting = (
        manager.create(IDS[20])
        if kind == "new"
        else history[0]
        if kind == "resume"
        else manager.fork(actors[0].session_id, IDS[20])
    )
    seen = watch(waiting)
    assert not await waiting.send("synthetic")
    assert seen[-1]["data"]["code"] == "too_many_sessions"
    assert len(drivers.made) == 8
    assert await actors[0].send("already live")
    assert drivers.made[0][0].calls[-1] == "send already live"
    assert len(drivers.made) == 8
    await manager.close(actors[0].session_id)
    assert await waiting.send("explicit retry")
    assert len(drivers.made) == 9 and len(manager.live()) == 8
    assert drivers.made[-1][1] == Launch(
        waiting.session_id,
        resume=kind == "resume",
        fork_of=actors[0].session_id if kind == "fork" else None,
    )
    await manager.close_all()


async def test_failed_eighth_initialization_frees_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    manager.bind_max_live(lambda: 8)
    for sid in IDS[:7]:
        assert await manager.create(sid).start()

    async def failed_open(driver: GatedDriver) -> SessionStarted:
        raise StartError("internal", "synthetic initialization failure")

    with monkeypatch.context() as patch:
        patch.setattr(GatedDriver, "open", failed_open)
        failed = manager.create(IDS[7])
        assert not await failed.start()
    assert not failed.live and len(manager.live()) == 7
    assert len(drivers.made) == 8
    assert await manager.create(IDS[8]).start()
    assert len(drivers.made) == 9 and len(manager.live()) == 8
    await manager.close_all()


@pytest.mark.parametrize("kind", ["new", "history", "live"])
async def test_full_recovery_advertises_thirty_and_preserves_replay(
    tmp_path: Path, kind: str
) -> None:
    manager = gated(tmp_path, Drivers())
    actor = manager.open(IDS[0]) if kind == "history" else manager.create(IDS[0])
    if kind == "live":
        assert await actor.start()
    initial = actor.recovery(None, 0)[0]
    assert initial["data"]["state"]["max_live_sessions"] == 30
    if kind != "live":
        assert initial["seq"] == 0 and initial["data"]["events"] == []
    cursor = actor.seq
    event = actor._emit(Status(mode="auto"))
    assert actor.recovery(actor.generation, cursor) == [event]
    assert actor.recovery(actor.generation, actor.seq) == []
    for generation, after_seq in [(None, 0), (IDS[1], 0), (actor.generation, actor.seq + 1)]:
        snapshot = actor.recovery(generation, after_seq)[0]
        assert snapshot["v"] == 2 and snapshot["type"] == "session.snapshot"
        assert snapshot["data"]["state"]["live"] is (kind == "live")
        assert snapshot["data"]["state"]["max_live_sessions"] == 30
    await manager.close_all()


async def test_a_holder_at_two_refuses_the_third_start(tmp_path: Path) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    manager.bind_max_live(Holder(2))
    actors = [manager.create(sid) for sid in IDS[:2]]
    for actor in actors:
        assert await actor.start()
    third = manager.create(IDS[2])
    seen = watch(third)
    assert not await third.start()
    assert seen[-1]["data"] == {
        "code": "too_many_sessions",
        "message": (
            "You can run at most 2 sessions at the same time. Close one, or raise the "
            "limit in Settings › Chat, then send again."
        ),
        "recoverable": True,
    }
    await manager.close_all()


async def test_a_raised_holder_starts_the_next_send_on_the_same_manager(tmp_path: Path) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    holder = Holder(2)
    manager.bind_max_live(holder)
    actors = [manager.create(sid) for sid in IDS[:2]]
    for actor in actors:
        assert await actor.start()
    third = manager.create(IDS[2])
    assert not await third.start()
    holder.value = 3
    assert await third.send("now admitted")
    assert len(drivers.made) == 3 and len(manager.live()) == 3
    await manager.close_all()


async def test_a_lowered_holder_keeps_three_live_and_refuses_a_fourth(tmp_path: Path) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    holder = Holder(3)
    manager.bind_max_live(holder)
    actors = [manager.create(sid) for sid in IDS[:3]]
    for actor in actors:
        assert await actor.start()
    holder.value = 1
    assert all(actor.live for actor in actors)
    fourth = manager.create(IDS[3])
    seen = watch(fourth)
    assert not await fourth.start()
    assert seen[-1]["data"]["code"] == "too_many_sessions"
    assert len(manager.live()) == 3
    await manager.close_all()


async def test_recovery_follows_the_holder(tmp_path: Path) -> None:
    manager = gated(tmp_path, Drivers())
    holder = Holder(2)
    manager.bind_max_live(holder)
    actor = manager.create(IDS[0])
    assert actor.recovery(None, 0)[0]["data"]["state"]["max_live_sessions"] == 2
    holder.value = 5
    assert actor.recovery(None, 0)[0]["data"]["state"]["max_live_sessions"] == 5
    await manager.close_all()


async def test_create_app_binds_the_settings_reader_beside_bind_limits(tmp_path: Path) -> None:
    manager, settings = _settings_app(tmp_path, Drivers())
    settings.patch(str(uuid.uuid4()), 0, {"chat.max_live_sessions": 1})
    first = manager.create(IDS[0])
    assert await first.start()
    second = manager.create(IDS[1])
    seen = watch(second)
    assert not await second.start()
    assert seen[-1]["data"]["code"] == "too_many_sessions"
    assert "1" in seen[-1]["data"]["message"]
    await manager.close_all()
