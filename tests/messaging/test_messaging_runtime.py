"""Synthetic generation races drive the real remote conversation engine."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import pytest

from tests.messaging.test_mattermost_bot import HANG_GUARD_S, Rig, take
from tests.messaging.test_remote_conversation import Channel
from viki_agent.messaging.mattermost_bot import RemoteBot
from viki_agent.messaging.messaging_channel import Provider, RemoteBinding

PROVIDERS: tuple[Provider, ...] = ("mattermost", "slack", "discord")


async def checkpoint() -> None:
    resumed = asyncio.Event()
    asyncio.get_running_loop().call_soon(resumed.set)
    await resumed.wait()


class HeldChannel(Channel):
    def __init__(self, provider: Provider) -> None:
        super().__init__(provider)
        self.bind_entered, self.close_entered = asyncio.Event(), asyncio.Event()
        self.bind_release, self.close_release = asyncio.Event(), asyncio.Event()
        self.bind_release.set()
        self.close_release.set()
        self.bind_error: Exception | None = None

    async def bind(self) -> RemoteBinding:
        self.bind_entered.set()
        if self.bind_error is not None:
            raise self.bind_error
        while not self.bind_release.is_set():
            # A late external completion cannot regain local authority.
            with suppress(asyncio.CancelledError):
                await self.bind_release.wait()
        return self.binding

    async def close(self) -> None:
        self.close_entered.set()
        await self.close_release.wait()
        self.closed = True


class Factory:
    def __init__(self, rig: Rig, provider: Provider) -> None:
        self.rig = rig
        self.provider: Provider = provider
        self.channels: list[HeldChannel] = []
        self.callbacks: list[Callable[[], None]] = []
        self.hold_bind = self.hold_close = self.broken = False
        self.bind_error: Exception | None = None

    def __call__(self, on_disarm: Callable[[], None]) -> RemoteBot:
        if self.broken:
            raise OSError("synthetic factory failure")
        channel = HeldChannel(self.provider)
        channel.bind_error = self.bind_error
        if self.hold_bind:
            channel.bind_release.clear()
        if self.hold_close:
            channel.close_release.clear()
        self.channels.append(channel)
        self.callbacks.append(on_disarm)
        return RemoteBot(
            channel,
            self.rig.manager,
            self.rig.approvals,
            create_actor=self.rig.create,
            clock=lambda: self.rig.now,
            on_disarm=on_disarm,
        )


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        from viki_agent.messaging.messaging_runtime import MessagingRuntime

        self.rig = Rig(tmp_path)
        self.factories = {provider: Factory(self.rig, provider) for provider in PROVIDERS}
        self.revoked: list[None] = []
        self.cooldown = 0.0
        self.remembered: list[tuple[Provider, bool]] = []
        self.remember_gates = {True: asyncio.Event(), False: asyncio.Event()}
        for gate in self.remember_gates.values():
            gate.set()
        self.remember_failure: Exception | None = None
        self.runtime = MessagingRuntime(
            mattermost=self.factories["mattermost"],
            slack=self.factories["slack"],
            discord=self.factories["discord"],
            revoke_mattermost=lambda: self.revoked.append(None),
            cooldown_until=lambda _provider, _bot: self.cooldown,
            clock=lambda: self.rig.now,
            remember=self.remember,
        )

    async def remember(self, provider: Provider, on: bool) -> None:
        await self.remember_gates[on].wait()
        if self.remember_failure is not None:
            raise self.remember_failure
        self.remembered.append((provider, on))

    async def enable(self, provider: Provider) -> HeldChannel:
        result = await self.runtime.set_enabled(provider, True)
        assert result["state"] == "connecting"
        channel = self.factories[provider].channels[-1]
        assert "ready" in (await take(channel.sent))[0]
        assert self.runtime.status()[provider]["state"] == "connected"
        return channel

    async def close(self) -> None:
        for factory in self.factories.values():
            for channel in factory.channels:
                channel.bind_release.set()
                channel.close_release.set()
        for gate in self.remember_gates.values():
            gate.set()
        await self.runtime.close()
        await self.rig.approvals.close()
        await self.rig.manager.close_all()


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    value = Harness(tmp_path)
    try:
        yield value
    finally:
        await value.close()


async def test_default_off_and_idempotent_enable(harness: Harness) -> None:
    assert set(harness.runtime.status()) == {"mattermost", "slack", "discord"}
    assert all(
        row == {"configured": True, "state": "disabled", "restart_required": False, "reason": None}
        for row in harness.runtime.status().values()
    )
    assert not any(factory.channels for factory in harness.factories.values())
    await harness.enable("slack")
    assert (await harness.runtime.set_enabled("slack", True))["state"] == "connected"
    assert len(harness.factories["slack"].channels) == 1
    assert harness.runtime.bridge("discord") is None


async def test_disable_enable_during_bind_cannot_adopt_old_actor(harness: Harness) -> None:
    factory = harness.factories["slack"]
    factory.hold_bind = factory.hold_close = True
    await harness.runtime.set_enabled("slack", True)
    old = factory.channels[0]
    async with asyncio.timeout(0.05):
        await old.bind_entered.wait()
    old.post("Offline old-generation prompt")
    disable = asyncio.create_task(harness.runtime.set_enabled("slack", False))
    await checkpoint()
    assert harness.runtime.bridge("slack") is None
    factory.hold_bind = factory.hold_close = False
    enable = asyncio.create_task(harness.runtime.set_enabled("slack", True))
    await checkpoint()
    assert len(factory.channels) == 1 and harness.rig.created.empty()
    old.bind_release.set()
    old.close_release.set()
    await disable
    assert (await enable)["state"] == "connecting"
    current = factory.channels[1]
    await take(current.sent)
    factory.callbacks[0]()  # A retired failure callback arrives after replacement.
    assert harness.runtime.status()["slack"]["state"] == "connected"
    assert old.closed and old.sent.empty() and harness.rig.created.empty()
    current.post("Fresh prompt")
    await take(harness.rig.created)
    assert await take(harness.rig.drivers[-1].sent) == "Fresh prompt"


async def test_disable_revokes_before_action_lock(harness: Harness) -> None:
    await harness.enable("mattermost")
    await harness.runtime._entries["mattermost"].lock.acquire()
    disable = asyncio.create_task(harness.runtime.set_enabled("mattermost", False))
    try:
        await checkpoint()
        assert harness.revoked == [None] and harness.runtime.bridge("mattermost") is None
        assert harness.runtime.status()["mattermost"]["state"] == "disabled"
        assert not disable.done()
    finally:
        harness.runtime._entries["mattermost"].lock.release()
    await disable


async def test_cancelled_disable_cleanup_finishes_before_new_generation(harness: Harness) -> None:
    factory = harness.factories["discord"]
    factory.hold_close = True
    old = await harness.enable("discord")
    disable = asyncio.create_task(harness.runtime.set_enabled("discord", False))
    async with asyncio.timeout(0.05):
        await old.close_entered.wait()
    disable.cancel()
    with pytest.raises(asyncio.CancelledError):
        await disable
    factory.hold_close = False
    enable = asyncio.create_task(harness.runtime.set_enabled("discord", True))
    await checkpoint()
    assert len(factory.channels) == 1
    old.close_release.set()
    await enable
    assert old.closed and len(factory.channels) == 2


async def test_failure_revokes_consent_before_blocked_cleanup(harness: Harness) -> None:
    factory = harness.factories["mattermost"]
    factory.hold_close = True
    channel = await harness.enable("mattermost")
    channel.incoming.put_nowait(ValueError("synthetic private body"))
    async with asyncio.timeout(0.05):
        await channel.close_entered.wait()
    assert harness.revoked == [None] and harness.runtime.bridge("mattermost") is None
    assert harness.runtime.status()["mattermost"] == {
        "configured": True,
        "state": "stopped",
        "restart_required": False,
        "reason": "connection_lost",
    }
    await checkpoint()
    assert len(factory.channels) == 1


async def test_cooldown_survives_disable_and_fresh_objects(harness: Harness) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    old = await harness.enable("slack")
    harness.rig.now, harness.cooldown = 10.0, 15.0
    await harness.runtime.set_enabled("slack", False)
    harness.cooldown = 0.0
    with pytest.raises(MessagingError) as error:
        await harness.runtime.set_enabled("slack", True)
    assert error.value.code == "cooldown" and error.value.retry_after_seconds == 5
    assert len(harness.factories["slack"].channels) == 1 and old.closed
    harness.rig.now = 15.0
    new = await harness.enable("slack")
    assert new is not old


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0, 86401.0, True])
async def test_unsafe_cooldown_refuses_fresh_connection(harness: Harness, value: float) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    await harness.enable("slack")
    harness.cooldown = value
    await harness.runtime.set_enabled("slack", False)
    assert harness.runtime.status()["slack"]["restart_required"]
    with pytest.raises(MessagingError, match="^restart_required$"):
        await harness.runtime.set_enabled("slack", True)


async def test_root_invalidation_and_dynamic_shutdown_disarm_current_generations(
    harness: Harness,
) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    await harness.enable("mattermost")
    await harness.runtime.set_enabled("mattermost", False)
    await harness.enable("mattermost")
    await harness.enable("discord")
    harness.runtime.invalidate("mattermost")
    assert harness.runtime.status()["mattermost"]["restart_required"]
    assert harness.runtime.status()["discord"]["state"] == "connected"
    with pytest.raises(MessagingError, match="^restart_required$"):
        await harness.runtime.set_enabled("mattermost", True)
    harness.runtime.disarm_all()
    assert all(
        harness.runtime.bridge(provider) is None for provider in ("mattermost", "slack", "discord")
    )
    await harness.runtime.close()
    assert all(
        channel.closed for factory in harness.factories.values() for channel in factory.channels
    )


async def test_runtime_names_owner_and_app_stops(harness: Harness) -> None:
    def bot_of(provider: Provider) -> RemoteBot:
        bot = harness.runtime._entries[provider].bot
        assert bot is not None
        return bot

    async def commit() -> None:
        return None

    await harness.enable("mattermost")
    first = bot_of("mattermost")
    await harness.runtime.set_enabled("mattermost", False)
    assert first._stop == "turned_off"
    assert harness.runtime.status()["mattermost"]["state"] == "disabled"
    assert harness.runtime.status()["mattermost"]["reason"] is None

    await harness.enable("mattermost")
    second = bot_of("mattermost")
    await harness.runtime.configure("mattermost", harness.factories["mattermost"], commit)
    assert second._stop == "turned_off"

    await harness.enable("slack")
    slack = bot_of("slack")
    harness.runtime.invalidate("slack")
    assert slack._stop == "restart_required"

    await harness.enable("discord")
    discord = bot_of("discord")
    harness.runtime.disarm_all()
    assert discord._stop == "app_closing"

    await harness.enable("mattermost")
    broken = bot_of("mattermost")
    broken.disarm(reason="connection_lost")
    harness.runtime.disarm_all()
    assert broken._stop == "connection_lost"


async def test_absent_and_invalid_input_never_create_bridge(tmp_path: Path) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError, MessagingRuntime

    runtime = MessagingRuntime()
    assert all(
        row
        == {
            "configured": False,
            "state": "not_configured",
            "restart_required": False,
            "reason": None,
        }
        for row in runtime.status().values()
    )
    with pytest.raises(MessagingError, match="^not_configured$"):
        await runtime.set_enabled("slack", True)
    with pytest.raises(MessagingError, match="^invalid_input$"):
        await runtime.set_enabled("slack", cast(bool, 1))
    await runtime.close()


async def test_failed_native_cleanup_requires_restart_and_prevents_reenable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from viki_agent.drivers.base import PermissionAnswer
    from viki_agent.messaging.messaging_runtime import MessagingError

    value = Harness(tmp_path)
    channel = await value.enable("slack")
    sibling_channel = await value.enable("discord")
    channel.post("Owned synthetic prompt")
    actor = await take(value.rig.created)
    driver = value.rig.drivers[-1]
    await take(driver.sent)
    sibling_channel.post("Sibling synthetic prompt")
    sibling = await take(value.rig.created)
    other_driver = value.rig.drivers[-1]
    await take(other_driver.sent)
    local = await value.rig.create()
    await take(value.rig.created)
    assert await local.start()
    bot = value.runtime.bridge("slack")
    assert bot is not None
    original = driver.interrupt

    async def failed_interrupt() -> None:
        assert not bot._armed and not driver.pending()
        raise OSError("synthetic private driver failure")

    monkeypatch.setattr(driver, "interrupt", failed_interrupt)
    driver.ask()
    try:
        with pytest.raises(MessagingError, match="^unavailable$"):
            await value.runtime.set_enabled("slack", False)
        assert await take(driver.answers) == ("card", PermissionAnswer(False))
        assert channel.closed and actor in bot._owned
        assert value.rig.manager.get(actor.session_id) is actor and driver.alive
        assert value.runtime.status()["slack"] == {
            "configured": True,
            "state": "stopped",
            "restart_required": True,
            "reason": None,
        }
        with pytest.raises(MessagingError, match="^restart_required$"):
            await value.runtime.set_enabled("slack", True)
        assert len(value.factories["slack"].channels) == 1 and value.rig.created.empty()
        assert value.rig.manager.get(sibling.session_id) is sibling and other_driver.alive
        assert value.rig.manager.get(local.session_id) is local and local.live
        driver.ask("late")
        assert await take(driver.answers) == ("late", PermissionAnswer(False))
    finally:
        monkeypatch.setattr(driver, "interrupt", original)
        with suppress(MessagingError):
            await value.runtime.close()
        await value.rig.approvals.close()
        await value.rig.manager.close_all()


DISABLED = {"configured": True, "state": "disabled", "restart_required": False, "reason": None}
STOPPED = {"configured": True, "state": "stopped", "restart_required": False, "reason": None}
RESTART = {"configured": True, "state": "stopped", "restart_required": True, "reason": None}
ABSENT = {"configured": False, "state": "not_configured", "restart_required": False, "reason": None}


async def test_enable_remembers_on_before_bridge_and_failures_keep_it(harness: Harness) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    factory = harness.factories["slack"]
    harness.remember_gates[True].clear()
    enable = asyncio.create_task(harness.runtime.set_enabled("slack", True))
    await checkpoint()
    assert not factory.channels and not enable.done()
    harness.remember_gates[True].set()
    assert (await enable)["state"] == "connecting"
    assert harness.remembered == [("slack", True)] and len(factory.channels) == 1
    await take(factory.channels[0].sent)
    harness.rig.now, harness.cooldown = 10.0, 15.0
    await harness.runtime.set_enabled("slack", False)
    with pytest.raises(MessagingError, match="^cooldown$"):
        await harness.runtime.set_enabled("slack", True)
    harness.factories["discord"].broken = True
    with pytest.raises(MessagingError, match="^unavailable$"):
        await harness.runtime.set_enabled("discord", True)
    assert harness.runtime.status()["discord"]["state"] == "stopped"
    assert harness.remembered == [
        ("slack", True),
        ("slack", False),
        ("slack", True),
        ("discord", True),
    ]


async def test_disable_remembers_off_even_when_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    value = Harness(tmp_path)
    try:
        channel = await value.enable("slack")

        async def failed_close() -> None:
            raise OSError("synthetic close failure")

        monkeypatch.setattr(channel, "close", failed_close)
        with pytest.raises(MessagingError, match="^unavailable$"):
            await value.runtime.set_enabled("slack", False)
        assert value.remembered == [("slack", True), ("slack", False)]
        assert value.runtime.status()["slack"] == {
            "configured": True,
            "state": "stopped",
            "restart_required": True,
            "reason": None,
        }
    finally:
        with suppress(MessagingError):
            await value.runtime.close()
        await value.rig.approvals.close()
        await value.rig.manager.close_all()


async def test_remember_failure_reports_unavailable(harness: Harness) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    harness.remember_failure = OSError("synthetic store failure")
    with pytest.raises(MessagingError, match="^unavailable$"):
        await harness.runtime.set_enabled("mattermost", True)
    assert not harness.factories["mattermost"].channels and harness.revoked == [None]
    assert harness.runtime.bridge("mattermost") is None
    assert harness.runtime.status()["mattermost"] == STOPPED
    harness.remember_failure = None
    channel = await harness.enable("mattermost")
    harness.remember_failure = OSError("synthetic store failure")
    with pytest.raises(MessagingError, match="^unavailable$"):
        await harness.runtime.set_enabled("mattermost", False)
    assert channel.closed and harness.runtime.bridge("mattermost") is None
    # The stored flag may still read on, so the state is not shown as a clean Off.
    assert harness.runtime.status()["mattermost"] == STOPPED
    assert harness.remembered == [("mattermost", True)]


async def test_later_disable_owns_state_after_superseded_write_failure(harness: Harness) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    await harness.enable("discord")
    harness.remember_gates[False].clear()
    first = asyncio.create_task(harness.runtime.set_enabled("discord", False))
    await checkpoint()
    second = asyncio.create_task(harness.runtime.set_enabled("discord", False))
    await checkpoint()
    harness.remember_failure = OSError("synthetic store failure")
    harness.remember_gates[False].set()
    await checkpoint()  # The first write has failed; the second Disable has not written yet.
    harness.remember_failure = None
    with pytest.raises(MessagingError, match="^unavailable$"):
        await first
    assert await second == DISABLED
    assert harness.remembered == [("discord", True), ("discord", False)]


async def test_failure_invalidate_disarm_and_close_never_remember(harness: Harness) -> None:
    channels = {provider: await harness.enable(provider) for provider in PROVIDERS}
    channels["slack"].incoming.put_nowait(ValueError("synthetic private body"))
    async with asyncio.timeout(1):
        await channels["slack"].close_entered.wait()
    assert harness.runtime.status()["slack"]["state"] == "stopped"
    harness.runtime.invalidate("mattermost")
    harness.runtime.disarm_all()
    await harness.runtime.close()
    assert harness.remembered == [(provider, True) for provider in PROVIDERS]


async def test_disable_then_enable_race_remembers_last_action(harness: Harness) -> None:
    await harness.enable("slack")
    harness.remembered.clear()
    harness.remember_gates[False].clear()
    disable = asyncio.create_task(harness.runtime.set_enabled("slack", False))
    await checkpoint()
    enable = asyncio.create_task(harness.runtime.set_enabled("slack", True))
    for _ in range(50):  # Old cleanup is not held: a queued Enable gets every chance to run.
        await checkpoint()
    assert not harness.remembered and len(harness.factories["slack"].channels) == 1
    assert not enable.done() and harness.runtime.bridge("slack") is None
    harness.remember_gates[False].set()
    await disable
    assert (await enable)["state"] == "connecting"
    assert harness.remembered == [("slack", False), ("slack", True)]
    await take(harness.factories["slack"].channels[1].sent)
    assert harness.runtime.status()["slack"]["state"] == "connected"
    assert harness.runtime.bridge("slack") is not None


async def test_configure_revokes_first_and_swaps_factory_after_cleanup(harness: Harness) -> None:
    old = await harness.enable("mattermost")
    replacement = Factory(harness.rig, "mattermost")
    commits: list[bool] = []

    async def commit() -> None:
        commits.append(old.closed)

    lock = harness.runtime._entries["mattermost"].lock
    await lock.acquire()
    configure = asyncio.create_task(harness.runtime.configure("mattermost", replacement, commit))
    try:
        await checkpoint()
        assert harness.revoked == [None] and harness.runtime.bridge("mattermost") is None
        assert harness.runtime.status()["mattermost"] == DISABLED
        assert not configure.done() and not commits
    finally:
        lock.release()
    assert await configure == DISABLED
    assert commits == [True]
    assert (await harness.runtime.set_enabled("mattermost", True))["state"] == "connecting"
    assert len(replacement.channels) == 1 and len(harness.factories["mattermost"].channels) == 1
    await take(replacement.channels[0].sent)


async def test_failed_commit_keeps_old_factory(harness: Harness) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    old = await harness.enable("slack")
    replacement = Factory(harness.rig, "slack")

    async def commit() -> None:
        raise OSError("synthetic store failure")

    with pytest.raises(MessagingError, match="^unavailable$"):
        await harness.runtime.configure("slack", replacement, commit)
    # The stored flag may still read on, so the state is not shown as a clean Off.
    assert old.closed and harness.runtime.status()["slack"] == STOPPED
    await harness.enable("slack")
    assert len(harness.factories["slack"].channels) == 2 and not replacement.channels


async def test_remove_after_failed_cleanup_still_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    value = Harness(tmp_path)
    commits: list[None] = []

    async def commit() -> None:
        commits.append(None)

    async def failed_commit() -> None:
        raise OSError("synthetic store failure")

    try:
        channel = await value.enable("slack")

        async def failed_close() -> None:
            raise OSError("synthetic close failure")

        monkeypatch.setattr(channel, "close", failed_close)
        replacement = Factory(value.rig, "slack")
        with pytest.raises(MessagingError, match="^unavailable$"):
            await value.runtime.configure("slack", replacement, failed_commit)
        assert value.runtime._entries["slack"].factory is value.factories["slack"]
        assert value.runtime.status()["slack"] == RESTART
        with pytest.raises(MessagingError, match="^unavailable$"):
            await value.runtime.configure("slack", None, commit)
        # The Remove reached its commit although cleanup failed; the bridge stays disarmed.
        assert commits == [None] and value.runtime.status()["slack"] == ABSENT
        assert value.runtime.bridge("slack") is None and not replacement.channels
    finally:
        with suppress(MessagingError):
            await value.runtime.close()
        await value.rig.approvals.close()
        await value.rig.manager.close_all()


async def test_enable_queued_during_configure_never_uses_old_factory(harness: Harness) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError, ProviderStatus

    await harness.enable("slack")
    replacement = Factory(harness.rig, "slack")
    entered, release = asyncio.Event(), asyncio.Event()

    async def commit() -> None:
        entered.set()
        await release.wait()

    async def queue_enable(factory: Factory | None) -> asyncio.Task[ProviderStatus]:
        entered.clear()
        release.clear()
        configure = asyncio.create_task(harness.runtime.configure("slack", factory, commit))
        async with asyncio.timeout(1):
            await entered.wait()
        enable = asyncio.create_task(harness.runtime.set_enabled("slack", True))
        await checkpoint()
        release.set()
        await configure
        return enable

    assert (await (await queue_enable(replacement)))["state"] == "connecting"
    assert len(harness.factories["slack"].channels) == 1 and len(replacement.channels) == 1
    await take(replacement.channels[0].sent)
    enable = await queue_enable(None)
    with pytest.raises(MessagingError, match="^not_configured$"):
        await enable
    assert len(replacement.channels) == 1 and harness.runtime.status()["slack"] == ABSENT
    assert harness.remembered == [("slack", True), ("slack", True)]


async def test_configure_none_then_invalidate_keeps_restart_required(harness: Harness) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    commits: list[None] = []

    async def commit() -> None:
        commits.append(None)

    assert await harness.runtime.configure("discord", None, commit) == ABSENT
    with pytest.raises(MessagingError, match="^not_configured$"):
        await harness.runtime.set_enabled("discord", True)
    harness.runtime.invalidate("discord")
    assert harness.runtime.status()["discord"] == ABSENT
    replacement = Factory(harness.rig, "discord")
    assert await harness.runtime.configure("discord", replacement, commit) == {
        "configured": True,
        "state": "stopped",
        "restart_required": True,
        "reason": None,
    }
    with pytest.raises(MessagingError, match="^restart_required$"):
        await harness.runtime.set_enabled("discord", True)
    assert commits == [None, None] and not replacement.channels and not harness.remembered


async def test_closed_runtime_refuses_configure(harness: Harness) -> None:
    from viki_agent.messaging.messaging_runtime import MessagingError

    commits: list[None] = []

    async def commit() -> None:
        commits.append(None)

    await harness.runtime.close()
    with pytest.raises(MessagingError, match="^unavailable$"):
        await harness.runtime.configure("slack", None, commit)
    assert not commits and harness.runtime.status()["slack"]["configured"]


@pytest.mark.parametrize(
    ("provider", "code", "reason"),
    [
        ("mattermost", "mattermost_token_rejected", "token_rejected"),
        ("mattermost", "mattermost_unreachable", "server_unreachable"),
        ("mattermost", "mattermost_binding_failed", "account_check_failed"),
        ("mattermost", "mattermost_http_failed", "start_failed"),
        ("slack", "", "start_failed"),
    ],
)
async def test_failed_start_reads_stopped_with_its_reason(
    harness: Harness, provider: Provider, code: str, reason: str
) -> None:
    if provider == "mattermost":
        from viki_agent.messaging.mattermost_http import ErrorCode, MattermostHttpError

        harness.factories[provider].bind_error = MattermostHttpError(cast(ErrorCode, code))
    else:
        harness.factories[provider].bind_error = OSError("synthetic bind failure")
    enabled = await harness.runtime.set_enabled(provider, True)
    assert enabled == {
        "configured": True,
        "state": "connecting",
        "restart_required": False,
        "reason": None,
    }
    run_task = harness.runtime._entries[provider].run
    assert run_task is not None
    async with asyncio.timeout(HANG_GUARD_S):
        await run_task
    assert harness.runtime.status()[provider] == {
        "configured": True,
        "state": "stopped",
        "restart_required": False,
        "reason": reason,
    }


async def test_mattermost_401_reads_token_rejected(tmp_path: Path) -> None:
    import httpx2

    from viki_agent.messaging.mattermost_bot import MattermostBot
    from viki_agent.messaging.mattermost_config import MattermostConfig
    from viki_agent.messaging.mattermost_http import MattermostHttp
    from viki_agent.messaging.messaging_runtime import MessagingRuntime

    requests: list[httpx2.Request] = []

    def unauthorized(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(401, json={})

    rig = Rig(tmp_path)
    config = MattermostConfig("https://chat.example.test", "o" * 26, "x" * 16)
    http = MattermostHttp(config, transport=httpx2.MockTransport(unauthorized))

    def factory(on_disarm: Callable[[], None]) -> RemoteBot:
        return MattermostBot(
            config,
            http,
            rig.manager,
            rig.approvals,
            create_actor=rig.create,
            clock=lambda: rig.now,
            on_disarm=on_disarm,
        )

    runtime = MessagingRuntime(mattermost=factory)
    try:
        enabled = await runtime.set_enabled("mattermost", True)
        assert enabled == {
            "configured": True,
            "state": "connecting",
            "restart_required": False,
            "reason": None,
        }
        run_task = runtime._entries["mattermost"].run
        assert run_task is not None
        async with asyncio.timeout(HANG_GUARD_S):
            await run_task
        assert runtime.status()["mattermost"] == {
            "configured": True,
            "state": "stopped",
            "restart_required": False,
            "reason": "token_rejected",
        }
        assert len(requests) == 1
    finally:
        await runtime.close()  # Retires the bot, which owns and closes `http`.
        await rig.approvals.close()
        await rig.manager.close_all()


async def test_only_owner_actions_clear_a_stop_reason(harness: Harness) -> None:
    from viki_agent.messaging.mattermost_http import ErrorCode, MattermostHttpError

    async def run_to_failure(factory: Factory, code: ErrorCode) -> None:
        factory.bind_error = MattermostHttpError(code)
        await harness.runtime.set_enabled("mattermost", True)
        run_task = harness.runtime._entries["mattermost"].run
        assert run_task is not None
        async with asyncio.timeout(HANG_GUARD_S):
            await run_task

    factory = harness.factories["mattermost"]
    await run_to_failure(factory, "mattermost_token_rejected")
    stopped_token_rejected = {
        "configured": True,
        "state": "stopped",
        "restart_required": False,
        "reason": "token_rejected",
    }
    assert harness.runtime.status()["mattermost"] == stopped_token_rejected

    harness.runtime.disarm_all()  # A shutdown sweep keeps the stop reason.
    assert harness.runtime.status()["mattermost"] == stopped_token_rejected

    await harness.runtime.set_enabled("mattermost", False)  # Disable clears it.
    assert harness.runtime.status()["mattermost"] == {
        "configured": True,
        "state": "disabled",
        "restart_required": False,
        "reason": None,
    }

    await run_to_failure(factory, "mattermost_unreachable")
    assert harness.runtime.status()["mattermost"]["reason"] == "server_unreachable"

    replacement = Factory(harness.rig, "mattermost")

    async def commit() -> None:
        return None

    await harness.runtime.configure("mattermost", replacement, commit)  # Save clears it.
    assert harness.runtime.status()["mattermost"] == {
        "configured": True,
        "state": "disabled",
        "restart_required": False,
        "reason": None,
    }

    await run_to_failure(replacement, "mattermost_binding_failed")
    assert harness.runtime.status()["mattermost"]["reason"] == "account_check_failed"

    replacement.bind_error = None
    enabled = await harness.runtime.set_enabled("mattermost", True)  # Connect clears it.
    assert enabled == {
        "configured": True,
        "state": "connecting",
        "restart_required": False,
        "reason": None,
    }


async def test_restart_required_hides_the_stop_reason(tmp_path: Path) -> None:
    from viki_agent.messaging.mattermost_http import MattermostHttpError
    from viki_agent.messaging.messaging_runtime import MessagingRuntime

    def failing_cooldown(_provider: Provider, _bot: RemoteBot) -> float:
        raise OSError("synthetic cooldown failure")

    rig = Rig(tmp_path)
    factory = Factory(rig, "mattermost")
    factory.bind_error = MattermostHttpError("mattermost_token_rejected")
    runtime = MessagingRuntime(
        mattermost=factory, cooldown_until=failing_cooldown, clock=lambda: rig.now
    )
    try:
        enabled = await runtime.set_enabled("mattermost", True)
        assert enabled["state"] == "connecting"
        run_task = runtime._entries["mattermost"].run
        assert run_task is not None
        async with asyncio.timeout(HANG_GUARD_S):
            await run_task
        # A failure that also forces a restart must hide its reason: the user can only clear
        # restart_required by reconfiguring, so a stale reason would mislead.
        assert runtime.status()["mattermost"] == {
            "configured": True,
            "state": "stopped",
            "restart_required": True,
            "reason": None,
        }
    finally:
        await runtime.close()
        await rig.approvals.close()
        await rig.manager.close_all()


async def test_observe_filters_event_type_and_isolates_a_raising_bridge(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MessagingRuntime.observe is the manager's single observer (SessionManager.bind_observer),
    fanned out to every connected bridge. A text.delta envelope never reaches a bridge's own
    observe(); a question.asked one does, and a bridge whose observe() raises is disarmed alone
    while the other bridge still sees the envelope."""
    from viki_agent.session import SessionActor

    await harness.enable("slack")
    await harness.enable("discord")
    slack_bot, discord_bot = harness.runtime.bridge("slack"), harness.runtime.bridge("discord")
    assert slack_bot is not None and discord_bot is not None
    calls: list[str] = []

    def failing(_actor: SessionActor, _envelope: dict[str, Any]) -> None:
        raise RuntimeError("synthetic observer failure")

    def recording(_actor: SessionActor, envelope: dict[str, Any]) -> None:
        calls.append(envelope["type"])

    monkeypatch.setattr(slack_bot, "observe", failing)
    monkeypatch.setattr(discord_bot, "observe", recording)
    actor = await harness.rig.create()

    harness.runtime.observe(actor, {"type": "text.delta", "data": {}})
    assert calls == []
    assert harness.runtime.status()["slack"]["state"] == "connected"  # never reached, not disarmed

    harness.runtime.observe(actor, {"type": "question.asked", "data": {}})
    assert calls == ["question.asked"]  # the other bridge still saw it
    assert harness.runtime.status()["slack"]["state"] == "stopped"  # disarmed alone
    assert harness.runtime.status()["discord"]["state"] == "connected"
