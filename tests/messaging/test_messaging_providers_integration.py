"""Synthetic providers exercise the server's real session and approval composition."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from tests.messaging.test_bot_integration import Integration as BotIntegration
from tests.messaging.test_mattermost_bot import CONFIG, Driver, Http, take
from tests.messaging.test_remote_conversation import Channel
from tests.profiles.test_profile_launch_api import AUTH, BASE, HANG_GUARD_S, TOKEN, WRITE, Rig
from viki_agent import protection, server
from viki_agent.conversations import approval_runtime
from viki_agent.drivers.base import PermissionAnswer, StartError
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.messaging.discord_channel import DiscordChannel
from viki_agent.messaging.mattermost_bot import _STOP_LINES
from viki_agent.messaging.messaging_channel import Provider, RemoteBinding, RemotePost
from viki_agent.messaging.messaging_config import DiscordConfig, SlackConfig
from viki_agent.messaging.messaging_store import MessagingConfigStore
from viki_agent.messaging.slack_channel import SlackChannel
from viki_agent.session import SessionActor, SessionManager

SLACK = SlackConfig("xoxb-synthetic", "xapp-synthetic", "U12345678")
DISCORD = DiscordConfig("synthetic", "123", "https://example.test/client")
DISABLED = {"configured": True, "state": "disabled", "restart_required": False, "reason": None}
RESTART = {"configured": True, "state": "stopped", "restart_required": True, "reason": None}


class SyntheticChannel(Channel):
    async def send(self, part: str, reply_to: str = "") -> None:
        await super().send(part, reply_to)

    async def receive(
        self,
        *,
        fresh_cutoff: Callable[[], Awaitable[int]],
        on_failure: Callable[[], None] | None = None,
    ) -> AsyncGenerator[RemotePost]:
        async for post in super().receive(fresh_cutoff=fresh_cutoff):
            assert isinstance(post, RemotePost)
            yield post


class Integration:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        with_workspaces: bool = True,
        prepare: Callable[[Integration], object] = lambda _: None,
    ) -> None:
        self.rig = Rig(tmp_path)
        self.settings = MessagingConfigStore(self.rig.state.store)
        self.settings.save("mattermost", CONFIG)
        self.settings.save("slack", SLACK)
        self.settings.save("discord", DISCORD)
        prepare(self)
        self.channels: dict[Provider, list[Channel]] = {"slack": [], "discord": []}
        self.http: list[Http] = []
        self.started: asyncio.Queue[tuple[Any, Any, Driver]] = asyncio.Queue()

        stack = self

        class Slack(SyntheticChannel, SlackChannel):
            def __init__(self, _config: SlackConfig) -> None:
                Channel.__init__(self, "slack")
                self.http = SimpleNamespace(cooldown_until=0.0)
                stack.channels["slack"].append(self)

        class Discord(SyntheticChannel, DiscordChannel):
            def __init__(self, _config: DiscordConfig) -> None:
                Channel.__init__(self, "discord")
                self.http = SimpleNamespace(cooldown_until=0.0)
                stack.channels["discord"].append(self)

        def http(_config: Any) -> Http:
            made = Http()
            self.http.append(made)
            return made

        def driver(launch: Any, prefs: Any, emit: Any) -> Driver:
            made = Driver(emit)
            self.started.put_nowait((launch, prefs, made))
            return made

        monkeypatch.setattr(server, "SlackChannel", Slack, raising=False)
        monkeypatch.setattr(server, "DiscordChannel", Discord, raising=False)
        monkeypatch.setattr(server, "MattermostHttp", http)
        self.manager = SessionManager(
            config=DriverConfig(cli=("must-not-run",), vault=self.rig.vault, env={}),
            state=self.rig.state,
            claude_home=self.rig.home,
            make_driver=driver,
        )
        self.app = server.create_app(
            config=self.rig.config,
            token=TOKEN,
            manager=self.manager,
            catalog=self.rig.catalog,
            static_dir=None,
            workspaces=self.rig.workspaces if with_workspaces else None,
        )
        self.runtime = self.app.state.messaging_runtime
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE)

    async def enable(self, provider: Provider) -> Channel:
        response = await self.client.post(f"/api/v2/messaging/{provider}/enable", headers=WRITE)
        assert response.status_code == 200, response.text
        channel = self.channels[provider][-1]
        assert "ready" in (await take(channel.sent))[0]
        return channel

    async def chat(self, channel: Channel) -> tuple[SessionActor, Driver]:
        channel.post("Synthetic remote prompt")
        launch, prefs, driver = await take(self.started)
        assert await take(driver.sent) == "Synthetic remote prompt"
        actor = self.manager.get(launch.session_id)
        assert actor is not None and prefs.mode == "auto" and launch.cwd == self.rig.vault
        return actor, driver

    async def close(self) -> None:
        await self.runtime.close()
        await self.app.state.approvals.close()
        await self.manager.close_all()
        await self.client.aclose()

    def flag(self, provider: Provider) -> str | None:
        saved = self.settings.load(provider)
        assert saved is not None
        return saved.enabled_workspace


async def until(check: Callable[[], object]) -> None:
    async with asyncio.timeout(HANG_GUARD_S):
        while not check():
            await asyncio.sleep(0.01)


async def test_disabled_launch_has_no_network_and_siblings_stay_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    try:
        async with stack.app.router.lifespan_context(stack.app):
            response = await stack.client.get("/api/v2/messaging", headers=AUTH)
            assert all(
                entry
                == {
                    "configured": True,
                    "state": "disabled",
                    "restart_required": False,
                    "reason": None,
                }
                for entry in response.json().values()
            )
            assert not stack.http and all(not value for value in stack.channels.values())
            assert stack.started.empty()
            slack = await stack.enable("slack")
            own, driver = await stack.chat(slack)
            discord = await stack.enable("discord")
            sibling, other = await stack.chat(discord)
            driver.ask()
            other.ask()
            assert own is not sibling and driver.pending() == other.pending() == ["card"]
            response = await stack.client.post("/api/v2/messaging/slack/disable", headers=WRITE)
            assert response.status_code == 200
            assert (await take(driver.answers))[1] == PermissionAnswer(False)
            assert other.pending() == ["card"] and other.answers.empty() and sibling.live
            assert not stack.http
            fresh = await stack.enable("slack")
            assert fresh is not slack and slack.closed
            new, _ = await stack.chat(fresh)
            assert new is not own and new is not sibling
    finally:
        await stack.close()


async def test_root_failure_invalidates_every_configured_provider_on_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    try:
        await stack.enable("slack")
        await stack.enable("discord")
        original = stack.rig.workspaces.get
        monkeypatch.setattr(
            stack.rig.workspaces,
            "get",
            lambda *args, **kwargs: replace(original(*args, **kwargs), revision=99),
        )
        threads: list[int] = []
        invalidate = stack.runtime.invalidate

        def observed(provider: Provider) -> None:
            threads.append(threading.get_ident())
            invalidate(provider)

        monkeypatch.setattr(stack.runtime, "invalidate", observed)
        bot = stack.runtime.bridge("slack")
        assert bot is not None
        with pytest.raises(StartError):
            await bot._create_actor()
        assert threads == [threading.get_ident()] * 3
        assert all(
            value
            == {
                "configured": True,
                "state": "stopped",
                "restart_required": True,
                "reason": None,
            }
            for value in stack.runtime.status().values()
        )
        assert stack.started.empty()
    finally:
        await stack.close()


async def test_configured_without_startup_workspace_requires_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = Integration(tmp_path, monkeypatch, with_workspaces=False)
    try:
        assert all(
            value
            == {
                "configured": True,
                "state": "stopped",
                "restart_required": True,
                "reason": None,
            }
            for value in stack.runtime.status().values()
        )
        response = await stack.client.post("/api/v2/messaging/slack/enable", headers=WRITE)
        assert response.status_code == 409
        assert response.json() == {
            "error": {
                "code": "restart_required",
                "message": "Restart VIKI Agent before connecting.",
            }
        }
        assert not stack.http and all(not value for value in stack.channels.values())
    finally:
        await stack.close()


async def test_disable_discards_actor_factory_completion_before_fresh_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    release = threading.Event()
    entered, finished = asyncio.Event(), asyncio.Event()
    loop = asyncio.get_running_loop()
    remember = stack.rig.state.remember

    def held(session_id: str, **kwargs: Any) -> Any:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(HANG_GUARD_S)
        result = remember(session_id, **kwargs)
        loop.call_soon_threadsafe(finished.set)
        return result

    try:
        old = await stack.enable("slack")
        monkeypatch.setattr(stack.rig.state, "remember", held)
        old.post("Synthetic cancelled prompt")
        await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        response = await stack.client.post("/api/v2/messaging/slack/disable", headers=WRITE)
        assert response.status_code == 200 and stack.runtime.bridge("slack") is None
        release.set()
        await asyncio.wait_for(finished.wait(), HANG_GUARD_S)
        assert stack.started.empty() and old.closed
        monkeypatch.setattr(stack.rig.state, "remember", remember)
        fresh = await stack.enable("slack")
        _, driver = await stack.chat(fresh)
        assert fresh is not old and driver.answers.empty()
    finally:
        release.set()
        await stack.close()


async def test_failed_transport_cleanup_still_finishes_desktop_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    closed = asyncio.Event()
    manager_close = stack.manager.close_all

    async def close_manager() -> None:
        await manager_close()
        closed.set()

    async def fail() -> None:
        raise RuntimeError("synthetic private provider response")

    monkeypatch.setattr(stack.manager, "close_all", close_manager)
    try:
        async with stack.app.router.lifespan_context(stack.app):
            channel = await stack.enable("slack")
            monkeypatch.setattr(channel, "close", fail)
        assert closed.is_set() and stack.app.state.approvals._closing
        assert "messaging cleanup unavailable (unavailable)" in caplog.text
        assert "synthetic private provider response" not in caplog.text
    finally:
        await stack.app.state.approvals.close()
        await stack.manager.close_all()
        await stack.client.aclose()


@pytest.mark.parametrize("reason", ["disable", "failure", "settle"])
async def test_mattermost_reconnect_requires_new_bot_delivery_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    stack = BotIntegration(tmp_path, monkeypatch)
    async with stack.app.router.lifespan_context(stack.app), stack.client:
        await stack.connect()
        await take(stack.http.sent)
        bot = await stack.enable(await stack.create())
        old = stack.bridge
        destination = old.destination_key()
        assert destination is not None
        if reason == "disable":
            response = await stack.client.post(
                "/api/v2/messaging/mattermost/disable", headers=WRITE
            )
            assert response.status_code == 200
        elif reason == "failure":
            stack.http.failure = True
            assert old._enqueue("Synthetic failed notice", "")
            await take(stack.http.sent)
        else:
            await stack.manager.settle(0)
        await old.close()
        while not stack.http.sent.empty():
            assert stack.http.sent.get_nowait()[0] in _STOP_LINES.values()
        assert not old._armed and not stack.app.state.bot_runtime._consents
        assert not stack.app.state.bot_runtime.delivery_status(bot)["enabled"]
        stack.http.failure = False
        await stack.connect()
        await take(stack.http.sent)
        assert stack.bridge is not old and stack.bridge.destination_key() == destination
        assert not stack.app.state.bot_runtime.delivery_status(bot)["enabled"]
        fresh = await stack.enable(bot)
        assert fresh["delivery"]["enabled"]


@pytest.mark.parametrize("pause_at", [1, 2, 3])
async def test_disable_revokes_allow_during_storage_and_preserves_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pause_at: int
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    release = asyncio.Event()
    try:
        channel = await stack.enable("slack")
        own, driver = await stack.chat(channel)
        other_channel = await stack.enable("discord")
        _, other = await stack.chat(other_channel)
        driver.ask()
        await take(channel.sent)
        instructions = (await take(channel.sent))[0]
        nonce = instructions.split("!viki allow ")[1].split()[0]
        entered, closing = asyncio.Event(), asyncio.Event()
        original, calls = approval_runtime.storage_call, 0

        async def storage(operation: Any, observer: Any = None) -> Any:
            nonlocal calls
            result = await original(operation, observer)
            calls += 1
            if calls == pause_at:
                entered.set()
                await release.wait()
            return result

        close = stack.manager.close

        async def held_close(session_id: str, **kwargs: Any) -> None:
            if session_id == own.session_id:
                closing.set()
                await release.wait()
            await close(session_id, **kwargs)

        monkeypatch.setattr(approval_runtime, "storage_call", storage)
        monkeypatch.setattr(stack.manager, "close", held_close)
        channel.post(f"!viki allow {nonce}")
        await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        disabling = asyncio.create_task(
            stack.client.post("/api/v2/messaging/slack/disable", headers=WRITE)
        )
        await asyncio.wait_for(closing.wait(), HANG_GUARD_S)
        assert (await take(driver.answers))[1] == PermissionAnswer(False)
        driver.ask("late")
        assert (await take(driver.answers))[1] == PermissionAnswer(False)
        other.ask("sibling")
        assert other.pending() == ["sibling"] and other.answers.empty()
        release.set()
        assert (await asyncio.wait_for(disabling, HANG_GUARD_S)).status_code == 200
        assert driver.answers.empty()
    finally:
        release.set()
        await stack.close()


async def test_saved_settings_launch_off_and_enable_records_startup_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    try:
        async with stack.app.router.lifespan_context(stack.app):
            assert stack.runtime.status()["slack"] == DISABLED
            response = await stack.client.get("/api/v2/messaging/config", headers=AUTH)
            assert response.json()["slack"] == {
                "source": "saved",
                "values": {"owner_id": SLACK.owner_id},
            }
            assert SLACK.bot_token not in response.text and SLACK.app_token not in response.text
            await stack.enable("slack")
            assert stack.flag("slack") == stack.rig.workspace_id and stack.flag("discord") is None
            response = await stack.client.post("/api/v2/messaging/slack/disable", headers=WRITE)
            assert response.status_code == 200 and stack.flag("slack") is None
    finally:
        await stack.close()


async def test_saved_on_in_startup_workspace_reconnects_without_holding_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = Integration(
        tmp_path,
        monkeypatch,
        prepare=lambda stack: stack.settings.remember("slack", stack.rig.workspace_id),
    )
    written, bound = threading.Event(), asyncio.Event()
    remember = MessagingConfigStore.remember

    def held_write(store: MessagingConfigStore, provider: Provider, workspace: str | None) -> None:
        assert written.wait(HANG_GUARD_S)
        remember(store, provider, workspace)

    async def held_bind(channel: Channel) -> RemoteBinding:
        await bound.wait()
        return channel.binding

    monkeypatch.setattr(MessagingConfigStore, "remember", held_write)
    monkeypatch.setattr(SyntheticChannel, "bind", held_bind)
    try:
        async with stack.app.router.lifespan_context(stack.app):
            # Startup finished while the reconnect's own flag write is still held.
            await until(lambda: stack.runtime.status()["slack"]["state"] == "connecting")
            written.set()
            await until(lambda: stack.channels["slack"])
            assert stack.runtime.status()["slack"]["state"] == "connecting"
            bound.set()
            assert "ready" in (await take(stack.channels["slack"][-1].sent))[0]
            await until(lambda: stack.runtime.status()["slack"]["state"] == "connected")
            assert stack.runtime.status()["discord"] == DISABLED and not stack.channels["discord"]
            assert stack.flag("slack") == stack.rig.workspace_id and stack.flag("discord") is None
    finally:
        written.set()
        bound.set()
        await stack.close()


@pytest.mark.parametrize("case", ["other_workspace", "no_root", "root_drift"])
async def test_saved_on_reconnects_only_in_its_available_startup_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
) -> None:
    stack = Integration(
        tmp_path,
        monkeypatch,
        with_workspaces=case != "no_root",
        prepare=lambda stack: stack.settings.remember(
            "slack", str(uuid.uuid4()) if case == "other_workspace" else stack.rig.workspace_id
        ),
    )
    kept = stack.flag("slack")
    if case == "root_drift":
        original = stack.rig.workspaces.get
        monkeypatch.setattr(
            stack.rig.workspaces,
            "get",
            lambda *args, **kwargs: replace(original(*args, **kwargs), revision=99),
        )
    try:
        async with stack.app.router.lifespan_context(stack.app):
            if case == "root_drift":
                await until(lambda: "slack reconnect unavailable (unavailable)" in caplog.text)
            await asyncio.sleep(0)
            assert stack.runtime.status()["slack"] == (
                DISABLED if case == "other_workspace" else RESTART
            )
        assert not stack.channels["slack"] and not stack.http
        assert stack.flag("slack") == kept and kept is not None
        assert (kept == stack.rig.workspace_id) == (case != "other_workspace")
        assert ("reconnect unavailable" in caplog.text) == (case == "root_drift")
    finally:
        await stack.close()


async def test_corrupt_saved_row_is_not_configured_and_never_reconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "xoxb-synthetic-corrupt-secret"

    def corrupt(stack: Integration) -> None:
        plain = {"bot_token": secret, "app_token": "xapp-synthetic", "owner_id": "invalid"}
        body = protection.protect(
            json.dumps(plain).encode("ascii"), context=b"VIKI messaging settings v1\x00slack"
        )
        with stack.rig.state.store._connection(write=True) as connection:
            connection.execute(
                "UPDATE messaging_config SET body=?,enabled_workspace=? WHERE provider='slack'",
                (body, stack.rig.workspace_id),
            )

    stack = Integration(tmp_path, monkeypatch, prepare=corrupt)
    try:
        async with stack.app.router.lifespan_context(stack.app):
            await asyncio.sleep(0)
            response = await stack.client.get("/api/v2/messaging", headers=AUTH)
            assert response.json()["slack"] == {
                "configured": False,
                "state": "not_configured",
                "restart_required": False,
                "reason": None,
            }
            assert response.json()["discord"] == DISABLED
        assert not stack.channels["slack"]
        assert "slack saved settings unavailable (corrupt)" in caplog.text
        assert "reconnect" not in caplog.text and secret not in caplog.text
    finally:
        await stack.close()
