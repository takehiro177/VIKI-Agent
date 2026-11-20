"""A bridge records why it stopped; the first reason wins and only internal ones are kept.
A bridge that had announced itself says why it stopped, once."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.messaging.test_mattermost_bot import HANG_GUARD_S, Rig, take
from tests.messaging.test_remote_conversation import Channel, Conversation
from viki_agent.messaging import mattermost_bot as module
from viki_agent.messaging.mattermost_format import chat_text
from viki_agent.messaging.messaging_channel import StopCause, StopReason

Trigger = Callable[[Conversation], Awaitable[None]]


@pytest.fixture
async def conversation(tmp_path: Path) -> AsyncIterator[Conversation]:
    rig = Rig(tmp_path)
    value = Conversation(rig, "slack")
    try:
        yield value
    finally:
        await value.close()
        await rig.approvals.close()
        await rig.manager.close_all()


def fake_actor(**changes: Any) -> Any:
    values: dict[str, Any] = {
        "session_id": str(uuid.uuid4()),
        "generation": "g",
        "live": False,
        "busy": False,
        "request_pending": lambda *_: True,
        "displayed_request": lambda *_: None,
        "remote_permission": lambda *_: None,
        "deny_pending": lambda: None,
    }
    return SimpleNamespace(**{**values, **changes})


def event(kind: str, actor: Any, **data: Any) -> dict[str, Any]:
    return {
        "type": kind,
        "data": {"tool_use_id": "t", **data},
        "native_request": {"tool": "x"},
        "session_id": actor.session_id,
        "generation": actor.generation,
    }


def adopt(bot: module.RemoteBot, actor: Any) -> None:
    bot._actor = actor
    bot._cards_enabled = True


async def queue_over_budget(c: Conversation) -> None:
    c.bot._output_count = module.MAX_OUTPUT_MESSAGES
    c.bot._enqueue("synthetic", "")


async def listen_over_cap(c: Conversation) -> None:
    c.bot._binding = c.channel.binding
    c.bot._input_count = module.MAX_INPUT_MESSAGES
    c.channel.post("synthetic")
    await c.bot._listen()


async def admit_over_post_ids(c: Conversation) -> None:
    post = c.channel.post("synthetic")
    c.bot._seen = {str(n) for n in range(module.MAX_POST_IDS)}
    c.bot._input.put_nowait(module._Input(post, 1, False, c.bot._epoch, None))
    await c.bot._admit()


async def send_over_actors(c: Conversation) -> None:
    c.bot._actor_count = module.MAX_ACTORS
    await c.bot._send("synthetic", c.bot._epoch)


async def event_over_cards(c: Conversation) -> None:
    actor = fake_actor()
    adopt(c.bot, actor)
    c.bot._cards = {str(n): cast(Any, SimpleNamespace(tool_id="t", native={})) for n in range(8)}
    c.bot._event(actor, event("permission.requested", actor))


async def render_fails(c: Conversation) -> None:
    c.channel.render = lambda _: ()  # type: ignore[method-assign]
    c.bot._enqueue("synthetic", "")


async def undisplayable_question(c: Conversation) -> None:
    actor = fake_actor()
    adopt(c.bot, actor)
    c.bot._event(actor, event("question.asked", actor))


async def revalidation_fails(c: Conversation) -> None:
    await c.start()
    _, driver = await c.chat()
    nonce = await c.nonce(driver)
    c.bot._request = lambda *_: "changed"  # type: ignore[method-assign]
    c.channel.post(f"!viki allow {nonce}")
    assert c.task is not None
    await c.task


async def conversation_event_fails(c: Conversation) -> None:
    actor = fake_actor()
    adopt(c.bot, actor)
    c.bot._event(actor, event("session.blocked", actor))


async def refused_send(c: Conversation) -> None:
    actor = fake_actor()

    async def send(*_: object, **__: object) -> bool:
        return False

    actor.send = send
    c.bot._actor = actor
    await c.bot._send("synthetic", c.bot._epoch)


async def actor_check_fails(c: Conversation) -> None:
    async def create() -> Any:
        return fake_actor(live=True)

    c.bot._create_actor = create
    await c.bot._send("synthetic", c.bot._epoch)


async def create_raises(c: Conversation) -> None:
    async def create() -> Any:
        raise RuntimeError("synthetic")

    c.bot._create_actor = create
    await c.bot._send("synthetic", c.bot._epoch)


async def event_raises(c: Conversation) -> None:
    c.bot._event(fake_actor(), {})


async def admit_raises(c: Conversation) -> None:
    post = c.channel.post("synthetic")

    async def confirm(_: object) -> None:
        raise RuntimeError("synthetic")

    c.channel.confirm = confirm  # type: ignore[method-assign]
    c.bot._input.put_nowait(module._Input(post, 1, False, c.bot._epoch, None))
    await c.bot._admit()


async def observe_raises(c: Conversation) -> None:
    c.bot.observe(fake_actor(), cast(Any, None))


async def choose_raises(c: Conversation) -> None:
    await c.start()
    _, driver = await c.chat()
    nonce = await c.nonce(driver)

    async def decide(_: object) -> None:
        raise RuntimeError("synthetic")

    c.rig.approvals.decide = decide  # type: ignore[method-assign]
    c.channel.post(f"!viki allow {nonce}")
    assert c.task is not None
    await c.task


STOPS: list[tuple[str, Trigger]] = [
    ("limit_reached", queue_over_budget),
    ("limit_reached", listen_over_cap),
    ("limit_reached", admit_over_post_ids),
    ("limit_reached", send_over_actors),
    ("limit_reached", event_over_cards),
    ("safety_check", render_fails),
    ("safety_check", undisplayable_question),
    ("safety_check", revalidation_fails),
    ("conversation_failed", conversation_event_fails),
    ("conversation_failed", refused_send),
    ("internal_error", actor_check_fails),
    ("internal_error", create_raises),
    ("internal_error", event_raises),
    ("internal_error", admit_raises),
    ("internal_error", observe_raises),
    ("internal_error", choose_raises),
]


@pytest.mark.parametrize(("reason", "trigger"), STOPS, ids=[f"{r}-{t.__name__}" for r, t in STOPS])
async def test_each_internal_stop_records_its_reason(
    conversation: Conversation, reason: StopReason, trigger: Trigger
) -> None:
    await trigger(conversation)
    assert conversation.bot.state == "stopped"
    assert conversation.bot.stop_reason == reason


async def test_first_reason_wins(conversation: Conversation) -> None:
    conversation.bot.disarm("limit_reached")
    conversation.bot.disarm("internal_error")
    conversation.bot.disarm()
    assert conversation.bot.stop_reason == "limit_reached"


async def test_transport_loss_after_ready_is_connection_lost(conversation: Conversation) -> None:
    await conversation.start()
    conversation.channel.incoming.put_nowait(RuntimeError("synthetic"))
    assert conversation.task is not None
    await conversation.task
    assert conversation.bot.stop_reason == "connection_lost"


async def test_owner_causes_do_not_change_the_reported_reason(conversation: Conversation) -> None:
    await conversation.start()
    conversation.bot.disarm("turned_off")
    assert conversation.bot.stop_reason == "connection_lost"


STOP_LINES: dict[StopCause, str] = {
    "limit_reached": (
        "VIKI Agent stopped this bot because it reached a safety limit, such as too many waiting "
        "requests or messages at once. Turn it on again in Messaging in VIKI Agent to continue."
    ),
    "conversation_failed": (
        "VIKI Agent stopped this bot because its Claude Code conversation reported an error or "
        "was closed. Check that conversation in VIKI Agent, then turn the bot on again in "
        "Messaging."
    ),
    "safety_check": (
        "VIKI Agent stopped this bot because a request changed after it was shown here, or a "
        "request or reply could not be shown completely. Check VIKI Agent, then turn the bot on "
        "again in Messaging."
    ),
    "internal_error": (
        "VIKI Agent stopped this bot after an unexpected error. Turn it on again in Messaging in "
        "VIKI Agent."
    ),
    "connection_lost": (
        "VIKI Agent stopped this bot because its connection to this chat service was lost. Turn "
        "it on again in Messaging in VIKI Agent."
    ),
    "turned_off": (
        "VIKI Agent stopped this bot because it was turned off or its settings changed in "
        "Messaging in VIKI Agent. Messages sent here are not answered until it is turned on "
        "again."
    ),
    "app_closing": (
        "VIKI Agent stopped this bot because the VIKI Agent window was closed or VIKI Agent "
        "shut down on the computer. Messages sent here are not answered until the bot runs "
        "again; open VIKI Agent and check Messaging."
    ),
    "restart_required": (
        "VIKI Agent stopped this bot because VIKI Agent needs a restart before this bot can run "
        "again."
    ),
}


def drain(c: Conversation) -> list[tuple[str, str]]:
    posts: list[tuple[str, str]] = []
    while not c.channel.sent.empty():
        posts.append(c.channel.sent.get_nowait())
    return posts


@pytest.mark.parametrize("cause", list(STOP_LINES))
async def test_each_cause_posts_its_line_top_level(
    conversation: Conversation, cause: StopCause
) -> None:
    await conversation.start()
    conversation.bot.disarm(cause)
    assert conversation.task is not None
    await conversation.task
    assert drain(conversation) == [(STOP_LINES[cause], "")]


async def test_ready_bridge_posts_one_stop_line(conversation: Conversation) -> None:
    await conversation.start()
    conversation.bot.disarm()
    assert conversation.task is not None
    await conversation.task
    await conversation.bot.close()
    await conversation.bot.close()
    assert drain(conversation) == [(STOP_LINES["connection_lost"], "")]


async def test_startup_failure_before_ready_posts_no_line(conversation: Conversation) -> None:
    async def bind() -> Any:
        raise RuntimeError("synthetic")

    conversation.channel.bind = bind  # type: ignore[method-assign]
    await conversation.bot.run()
    assert drain(conversation) == [] and conversation.channel.closed


async def test_turn_is_stopped_while_the_stop_line_is_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "STOP_NOTICE_DEADLINE_S", 1e6)  # Only the gate ends the post.
    rig = Rig(tmp_path)
    await rig.start(monkeypatch)
    try:
        _, driver = await rig.chat()
        assert driver.busy  # Claude's turn is running.
        rig.http.gate.clear()  # The chat service is slow: the stop line's post hangs.
        rig.bot.disarm("turned_off")
        assert await take(rig.http.sent) == (chat_text(STOP_LINES["turned_off"]), "")
        async with asyncio.timeout(HANG_GUARD_S):
            await driver.interrupting.wait()
        assert not rig.http.closed  # The line is still in flight.
        rig.http.gate.set()
        assert rig.run is not None
        await rig.run
        assert rig.http.closed and rig.http.sent.empty()
    finally:
        rig.http.gate.set()
        await rig.bot.close()
        if rig.run is not None:
            await rig.run
        await rig.approvals.close()
        await rig.manager.close_all()


class ClosingChannel(Channel):
    """Like Slack's and Discord's: a cancelled reader closes the channel, and a closed channel
    refuses a send, including one already under way."""

    def __init__(self) -> None:
        super().__init__("slack")
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def receive(
        self,
        *,
        fresh_cutoff: Callable[[], Awaitable[int]],
        on_failure: Callable[[], None] | None = None,
    ) -> AsyncIterator[object]:
        try:
            async for item in super().receive(fresh_cutoff=fresh_cutoff, on_failure=on_failure):
                yield item
        finally:
            if not self.closed:
                await self.close()

    async def send(self, part: str, reply_to: str) -> None:
        if self.closed:
            raise RuntimeError("synthetic closed")
        self.entered.set()
        await self.release.wait()
        if self.closed:
            raise RuntimeError("synthetic closed meanwhile")
        await super().send(part, reply_to)


async def test_stop_line_is_posted_before_the_reader_closes_the_channel(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    channel = ClosingChannel()
    bot = module.RemoteBot(channel, rig.manager, rig.approvals, create_actor=rig.create)
    run = asyncio.create_task(bot.run())
    try:
        assert "ready" in (await take(channel.sent))[0]
        channel.entered.clear()
        channel.release.clear()
        bot.disarm("turned_off")
        async with asyncio.timeout(HANG_GUARD_S):
            await channel.entered.wait()  # The stop line is under way.
        assert bot._listen_task is not None
        # A reader cancelled now would close the channel under the line.
        await asyncio.wait({bot._listen_task}, timeout=0.05)
        channel.release.set()
        async with asyncio.timeout(HANG_GUARD_S):
            await run
        posted = [channel.sent.get_nowait() for _ in range(channel.sent.qsize())]
        assert posted == [(STOP_LINES["turned_off"], "")] and channel.closed
    finally:
        channel.release.set()
        await bot.close()
        await rig.approvals.close()
        await rig.manager.close_all()


@pytest.mark.parametrize("failure", ["blocks", "raises"])
async def test_failing_stop_line_does_not_keep_the_bridge_open(
    conversation: Conversation, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(module, "STOP_NOTICE_DEADLINE_S", 0.01)
    await conversation.start()
    actor, _ = await conversation.chat()

    async def send(*_: object) -> None:
        if failure == "raises":
            raise RuntimeError("synthetic")
        await asyncio.Event().wait()

    conversation.channel.send = send  # type: ignore[method-assign]
    async with asyncio.timeout(HANG_GUARD_S):
        await conversation.bot.close()
    assert conversation.channel.closed and not actor.live
    assert not conversation.bot._owned
