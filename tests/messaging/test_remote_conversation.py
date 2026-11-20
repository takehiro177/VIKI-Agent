"""Synthetic channels drive the shared real actor and approval engine."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from tests.messaging.test_mattermost_bot import Driver, Rig, take
from viki_agent.drivers.base import PermissionAnswer
from viki_agent.messaging import mattermost_bot as module
from viki_agent.messaging.mattermost_http import Binding
from viki_agent.messaging.messaging_channel import Provider, RemoteBinding, RemotePost, StopReason
from viki_agent.session import SessionActor


class Channel:
    def __init__(self, provider: Provider) -> None:
        self.binding = RemoteBinding(provider, ("synthetic-scope",), "bot", "owner", "dm")
        self.incoming: asyncio.Queue[RemotePost | Exception] = asyncio.Queue()
        self.sent: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.posts: dict[str, RemotePost] = {}
        self.confirmed: list[str] = []
        self.closed = False
        self.index = 0
        self.reason: StopReason | None = None

    async def bind(self) -> RemoteBinding:
        return self.binding

    async def fresh_cutoff(self) -> int:
        return 1000

    async def confirm(self, candidate: RemotePost) -> RemotePost | None:
        self.confirmed.append(candidate.id)
        return self.posts.get(candidate.id)

    async def send(self, part: str, reply_to: str) -> None:
        self.sent.put_nowait((part, reply_to))

    async def close(self) -> None:
        self.closed = True

    async def receive(
        self,
        *,
        fresh_cutoff: Callable[[], Awaitable[int]],
        on_failure: Callable[[], None] | None = None,
    ) -> AsyncIterator[object]:
        assert await fresh_cutoff() == 1000
        while True:
            value = await self.incoming.get()
            if isinstance(value, Exception):
                raise value
            yield value

    def validate(self, candidate: object, cutoff: int) -> RemotePost | None:
        return (
            candidate
            if isinstance(candidate, RemotePost) and candidate.created_ms >= cutoff
            else None
        )

    def render(self, formatted: str) -> tuple[str, ...]:
        return (formatted,)

    def destination_key(self, binding: RemoteBinding) -> str:
        return hashlib.sha256(json.dumps(asdict(binding)).encode("utf-8")).hexdigest()

    def failure(self) -> StopReason | None:
        return self.reason

    def post(self, text: str) -> RemotePost:
        self.index += 1
        raw = json.dumps([asdict(self.binding), str(self.index), text, 1000]).encode("utf-8")
        post = RemotePost(
            str(self.index), text, "", 1000, hashlib.sha256(raw).hexdigest(), len(raw)
        )
        self.posts[post.id] = post
        self.incoming.put_nowait(post)
        return post


class Conversation:
    def __init__(self, rig: Rig, provider: Provider) -> None:
        self.rig, self.channel = rig, Channel(provider)
        self.bot = module.RemoteBot(
            self.channel, rig.manager, rig.approvals, create_actor=rig.create, clock=lambda: rig.now
        )
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self.bot.run())
        assert "ready" in (await take(self.channel.sent))[0]

    async def chat(self) -> tuple[SessionActor, Driver]:
        self.channel.post("Synthetic prompt")
        actor = await take(self.rig.created)
        driver = self.rig.drivers[-1]
        assert await take(driver.sent) == "Synthetic prompt"
        return actor, driver

    async def nonce(self, driver: Driver) -> str:
        driver.ask()
        assert "synthetic hidden argument" in (await take(self.channel.sent))[0]
        return (await take(self.channel.sent))[0].split("!viki allow ")[1].split()[0]

    async def close(self) -> None:
        await self.bot.close()
        if self.task is not None:
            await self.task


@pytest.fixture
async def conversations(tmp_path: Path) -> AsyncIterator[tuple[Conversation, Conversation]]:
    rig = Rig(tmp_path)
    first, second = Conversation(rig, "slack"), Conversation(rig, "discord")
    try:
        await first.start()
        await second.start()
        yield first, second
    finally:
        await first.close()
        await second.close()
        await rig.approvals.close()
        await rig.manager.close_all()


async def test_other_provider_nonce_cannot_answer(
    conversations: tuple[Conversation, Conversation], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = conversations
    # Namespace separation survives a repeated random nonce sample across providers.
    monkeypatch.setattr(module.secrets, "token_hex", lambda _: "a" * 32)
    own, driver = await first.chat()
    nonce = await first.nonce(driver)
    sibling, other = await second.chat()
    other_nonce = await second.nonce(other)
    assert nonce != other_nonce and own is not sibling
    second.channel.post(f"!viki allow {nonce}")
    assert "refused" in (await take(second.channel.sent))[0].lower()
    assert driver.answers.empty() and other.answers.empty()
    first.channel.post(f"!viki allow {nonce}")
    assert await take(driver.answers) == ("card", PermissionAnswer(True))
    assert "delivered_locally" in (await take(first.channel.sent))[0]
    assert other.pending() == ["card"]


@pytest.mark.parametrize("field", ["message", "fingerprint", "size_bytes", "created_ms", "root_id"])
async def test_confirmation_compares_complete_immutable_post(
    conversations: tuple[Conversation, Conversation], field: str
) -> None:
    first, _ = conversations
    post = first.channel.post("Must not dispatch")
    value = getattr(post, field)
    first.channel.posts[post.id] = replace(
        post, **{field: value + 1 if type(value) is int else "other"}
    )
    first.channel.post("!viki status")
    assert "ready" in (await take(first.channel.sent))[0]
    assert post.id in first.channel.confirmed and first.rig.created.empty()


async def test_foreign_provider_fingerprint_cannot_dispatch(
    conversations: tuple[Conversation, Conversation],
) -> None:
    first, second = conversations
    foreign = first.channel.post("!viki status")
    second.channel.post("!viki status")
    await take(first.channel.sent)
    await take(second.channel.sent)
    foreign = replace(foreign, id="3", message="Must not dispatch")
    second.channel.posts[foreign.id] = replace(foreign, fingerprint="different-provider")
    second.channel.incoming.put_nowait(foreign)
    second.channel.post("!viki status")
    await take(second.channel.sent)
    assert foreign.id in second.channel.confirmed and first.rig.created.empty()


async def test_actor_cannot_be_adopted_by_two_provider_generations(
    conversations: tuple[Conversation, Conversation], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = conversations
    actor = await first.rig.create()
    await take(first.rig.created)
    await actor._lock.acquire()  # Keep the first actor idle during the competing adoption.

    async def shared() -> SessionActor:
        return actor

    monkeypatch.setattr(first.bot, "_create_actor", shared)
    monkeypatch.setattr(second.bot, "_create_actor", shared)
    first.channel.post("First prompt")
    resumed = asyncio.Event()
    asyncio.get_running_loop().call_soon(resumed.set)
    await resumed.wait()
    second.channel.post("Second prompt")
    try:
        async with asyncio.timeout(0.05):
            await second.bot._stopped.wait()
        assert first.bot._armed and first.bot._actor is actor
        assert second.bot._actor is None and not first.rig.drivers
    finally:
        actor._lock.release()
    driver = first.rig.drivers[-1] if first.rig.drivers else None
    if driver is None:
        async with asyncio.timeout(0.05):
            while not first.rig.drivers:
                resumed = asyncio.Event()
                asyncio.get_running_loop().call_soon(resumed.set)
                await resumed.wait()
        driver = first.rig.drivers[-1]
    assert await take(driver.sent) == "First prompt"


async def test_retired_provider_cannot_answer_or_stop_sibling(
    conversations: tuple[Conversation, Conversation],
) -> None:
    first, second = conversations
    own, driver = await first.chat()
    nonce = await first.nonce(driver)
    sibling, other = await second.chat()
    other_nonce = await second.nonce(other)
    first.bot.disarm()
    assert await take(driver.answers) == ("card", PermissionAnswer(False))
    first.channel.post(f"!viki allow {nonce}")
    assert second.bot._armed and other.pending() == ["card"]
    second.channel.post(f"!viki deny {other_nonce}")
    assert await take(other.answers) == ("card", PermissionAnswer(False))
    await take(second.channel.sent)
    await first.close()
    assert first.rig.manager.get(own.session_id) is None
    assert first.rig.manager.get(sibling.session_id) is sibling and other.alive


async def test_legacy_assignment_gets_only_mattermost_scope(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    try:
        legacy = Binding("b" * 26, "o" * 26, "c" * 26)
        rig.bot._binding = legacy
        binding = rig.bot._binding
        assert isinstance(binding, RemoteBinding)
        assert binding.provider == "mattermost" and binding.scope == (rig.bot.config.url,)
        assert binding.bot_user_id == legacy.bot_user_id
    finally:
        await rig.bot.close()
        await rig.approvals.close()
        await rig.manager.close_all()


async def test_generic_channel_refuses_unscoped_legacy_assignment(
    conversations: tuple[Conversation, Conversation], monkeypatch: pytest.MonkeyPatch
) -> None:
    first, _ = conversations
    with pytest.raises(ValueError, match="^remote_binding_invalid$"):
        monkeypatch.setattr(first.bot, "_binding", Binding("bot", "owner", "dm"))


@pytest.mark.parametrize("removed", [False, True])
async def test_actor_cleanup_failure_attempts_all_owned_actors_and_closes_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, removed: bool
) -> None:
    rig = Rig(tmp_path)
    conversation = Conversation(rig, "slack")
    await conversation.start()
    actor, driver = await conversation.chat()
    healthy = await rig.create()
    await take(rig.created)
    assert await healthy.start()
    unsubscribed: list[bool] = []
    unsubscribe = healthy.subscribe(lambda _: None)

    def retired() -> None:
        unsubscribe()
        unsubscribed.append(True)

    conversation.bot._owned[healthy] = retired
    original = driver.interrupt

    async def fail() -> None:
        if removed:
            rig.manager._actors.pop(actor.session_id)
        raise OSError("synthetic private interrupt failure")

    monkeypatch.setattr(driver, "interrupt", fail)
    try:
        with pytest.raises(RuntimeError, match="^remote_cleanup_failed$") as error:
            await conversation.bot.close()
        assert error.value.__context__ is None
        assert conversation.channel.closed and not conversation.bot._armed
        assert actor in conversation.bot._owned and actor.live
        assert healthy not in conversation.bot._owned and not healthy.live
        assert rig.manager.get(healthy.session_id) is None and unsubscribed == [True]
    finally:
        monkeypatch.setattr(driver, "interrupt", original)
        if conversation.task is not None:
            await asyncio.gather(conversation.task, return_exceptions=True)
        await actor.close()
        await rig.approvals.close()
        await rig.manager.close_all()


async def test_still_owned_actor_is_cleanup_failure_even_without_close_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    conversation = Conversation(rig, "slack")
    await conversation.start()
    actor, _ = await conversation.chat()

    async def unchanged(*_: object, **__: object) -> None:
        pass

    monkeypatch.setattr(rig.manager, "close", unchanged)
    try:
        with pytest.raises(RuntimeError, match="^remote_cleanup_failed$"):
            await conversation.bot.close()
        assert actor in conversation.bot._owned and actor.live and conversation.channel.closed
    finally:
        if conversation.task is not None:
            await asyncio.gather(conversation.task, return_exceptions=True)
        await actor.close()
        await rig.approvals.close()
        await rig.manager.close_all()


async def test_channel_cleanup_failure_uses_fixed_error_without_private_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    conversation = Conversation(rig, "slack")
    await conversation.start()
    actor, _ = await conversation.chat()

    async def fail() -> None:
        conversation.channel.closed = True
        raise OSError("synthetic private channel body")

    monkeypatch.setattr(conversation.channel, "close", fail)
    try:
        with pytest.raises(RuntimeError, match="^remote_cleanup_failed$") as error:
            await conversation.bot.close()
        assert error.value.__context__ is None
        assert not actor.live and actor not in conversation.bot._owned
        assert conversation.channel.closed
    finally:
        if conversation.task is not None:
            await asyncio.gather(conversation.task, return_exceptions=True)
        await rig.approvals.close()
        await rig.manager.close_all()


@pytest.mark.parametrize(
    ("bind_fails", "reason", "expected"),
    [
        (True, "token_rejected", "token_rejected"),
        (True, None, "start_failed"),
        (False, "token_rejected", "connection_lost"),
    ],
)
async def test_stop_reason_prefers_the_channel_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bind_fails: bool,
    reason: StopReason | None,
    expected: StopReason,
) -> None:
    rig = Rig(tmp_path)
    channel = Channel("slack")
    seen: list[StopReason] = []
    bot = module.RemoteBot(
        channel,
        rig.manager,
        rig.approvals,
        create_actor=rig.create,
        on_disarm=lambda: seen.append(bot.stop_reason),
    )
    try:
        if bind_fails:

            async def bind() -> RemoteBinding:
                channel.reason = reason
                raise OSError("synthetic bind failure")

            monkeypatch.setattr(channel, "bind", bind)
            await bot.run()
        else:
            # Reached ready before the connection drops: state wins over the channel's reason.
            task = asyncio.create_task(bot.run())
            assert "ready" in (await take(channel.sent))[0]
            channel.reason = reason
            channel.incoming.put_nowait(OSError("synthetic connection loss"))
            await task
        assert seen == [expected]
    finally:
        await rig.bot.close()
        await rig.approvals.close()
        await rig.manager.close_all()
