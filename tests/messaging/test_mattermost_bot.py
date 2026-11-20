"""Synthetic bot, actor and approval races; no account or provider connection."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import ssl
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from tests.conversations.test_session import GatedDriver
from viki_agent.conversations import approval_runtime
from viki_agent.conversations.approval_runtime import ApprovalRunner
from viki_agent.conversations.approvals import ApprovalService, PermissionChoice, QuestionChoice
from viki_agent.drivers.base import Answer, PermissionAnswer, QuestionAnswer
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.events import (
    Error,
    Event,
    PermissionRequested,
    Question,
    QuestionAsked,
    QuestionOption,
    RequestAnswered,
    RequestCancelled,
    TextDone,
    ThinkingDone,
    ToolDone,
    TurnDone,
    Usage,
)
from viki_agent.messaging import mattermost_bot as module
from viki_agent.messaging.mattermost_config import MattermostConfig
from viki_agent.messaging.mattermost_http import Binding, MattermostHttp
from viki_agent.session import SessionActor, SessionManager
from viki_agent.state import AppState, Prefs

BINDING = Binding("b" * 26, "o" * 26, "c" * 26)
CONFIG = MattermostConfig("https://example.test", BINDING.owner_user_id, "synthetic-token")
# A deadlock guard for pipeline events, including SQLite worker commits; not a work deadline.
HANG_GUARD_S = 30.0


async def take(queue: asyncio.Queue[Any]) -> Any:
    async with asyncio.timeout(HANG_GUARD_S):
        return await queue.get()


class Http:
    def __init__(self) -> None:
        self.ssl_context = ssl.create_default_context()
        self.posts: dict[str, dict[str, Any]] = {}
        self.reads: list[str] = []
        self.sent: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.gate = asyncio.Event()
        self.gate.set()
        self.failure = False
        self.closed = False

    async def bind(self) -> Binding:
        return BINDING

    async def fresh_cutoff(self) -> int:
        return 1000

    async def post(self, post_id: str) -> dict[str, Any]:
        self.reads.append(post_id)
        return self.posts[post_id]

    async def send(self, binding: Binding, message: str, root_id: str = "") -> str:
        assert binding == BINDING
        self.sent.put_nowait((message, root_id))
        await self.gate.wait()
        if self.failure:
            raise RuntimeError("synthetic body must not escape")
        return "s" * 26

    async def close(self) -> None:
        self.closed = True


class Driver(GatedDriver):
    def __init__(self, emit: Callable[[Event], None]) -> None:
        super().__init__()
        self.release.set()
        self.emit = emit
        self.inputs: dict[str, dict[str, Any] | None] = {}
        self.answers: asyncio.Queue[tuple[str, Answer]] = asyncio.Queue()
        self.sent: asyncio.Queue[str] = asyncio.Queue()
        self.send_gate = asyncio.Event()
        self.send_gate.set()
        self.interrupting = asyncio.Event()
        self.interrupt_gate = asyncio.Event()
        self.interrupt_gate.set()

    async def send(self, text: str) -> None:
        self.busy = True
        self.sent.put_nowait(text)
        await self.send_gate.wait()

    async def interrupt(self) -> None:
        self.calls.append("interrupt")
        self.interrupting.set()
        await self.interrupt_gate.wait()

    def ask(self, tool_id: str = "card", *, question: bool = False) -> None:
        self.inputs[tool_id] = {
            "tool": "AskUserQuestion" if question else "Edit",
            "input": {"complete": "synthetic hidden argument"},
        }
        self.emit(
            QuestionAsked(
                tool_use_id=tool_id,
                questions=[Question(header="Choice", question="Which?", options=[])],
            )
            if question
            else PermissionRequested(tool_use_id=tool_id, kind="tool", tool="Edit", summary="Edit")
        )

    def permission_details(self, tool_use_id: str) -> dict[str, Any] | None:
        return self.inputs.get(tool_use_id)

    def pending(self) -> list[str]:
        return list(self.inputs)

    def respond(self, tool_use_id: str, answer: Answer) -> bool:
        if tool_use_id not in self.inputs:
            return False
        self.inputs.pop(tool_use_id)
        self.answers.put_nowait((tool_use_id, answer))
        self.emit(RequestAnswered(tool_use_id=tool_use_id))
        return True

    def finish(self, text: str = "Synthetic reply") -> None:
        self.emit(TextDone(block_id="final", text=text))
        self.busy = False
        self.emit(TurnDone(status="success", usage=Usage()))


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.http = Http()
        self.incoming: asyncio.Queue[dict[str, Any] | Exception] = asyncio.Queue()
        self.drivers: list[Driver] = []
        self.created: asyncio.Queue[SessionActor] = asyncio.Queue()
        self.state = AppState(tmp_path / "state.json", Prefs(mode="auto"))

        def driver(_launch: Any, prefs: Prefs, emit: Callable[[Event], None]) -> Driver:
            assert prefs.mode == "default"
            made = Driver(emit)
            self.drivers.append(made)
            return made

        self.manager = SessionManager(
            config=DriverConfig(cli=("must-not-run",), vault=tmp_path / "synthetic", env={}),
            state=self.state,
            claude_home=tmp_path / "synthetic-home",
            make_driver=driver,
        )
        self.approvals = ApprovalRunner(
            ApprovalService(self.state, tmp_path / "synthetic", owner=str(uuid.uuid4())),
            self.manager,
        )
        self.now = 0.0
        self.bot = module.MattermostBot(
            CONFIG,
            cast(MattermostHttp, self.http),
            self.manager,
            self.approvals,
            create_actor=self.create,
            clock=lambda: self.now,
        )
        self.run: asyncio.Task[None] | None = None
        self.index = 0

    async def create(self) -> SessionActor:
        session_id = str(uuid.uuid4())
        self.state.remember(session_id, mode="default")
        actor = self.manager.create(session_id)
        self.created.put_nowait(actor)
        return actor

    async def posted(self, _config: Any, binding: Binding, **kwargs: Any) -> AsyncIterator[Any]:
        assert binding == BINDING and kwargs["ssl_context"] is self.http.ssl_context
        assert await kwargs["fresh_cutoff"]() == 1000
        while True:
            post = await self.incoming.get()
            if isinstance(post, Exception):
                raise post
            yield post

    def post(self, text: str, **changes: Any) -> dict[str, Any]:
        self.index += 1
        post = {
            "id": f"{self.index:026d}",
            "user_id": BINDING.owner_user_id,
            "channel_id": BINDING.channel_id,
            "message": text,
            "create_at": 1000,
            "edit_at": 0,
            "delete_at": 0,
            "type": "",
            "original_id": "",
            "root_id": "",
            "props": {},
            "file_ids": [],
        }
        post.update(changes)
        self.http.posts[post["id"]] = dict(post)
        self.incoming.put_nowait({**post, "_fresh_cutoff_ms": 1000})
        return post

    async def start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(module, "posted", self.posted)
        self.run = asyncio.create_task(self.bot.run())
        assert "ready" in (await take(self.http.sent))[0].lower()

    async def chat(self) -> tuple[SessionActor, Driver]:
        self.post("Synthetic prompt")
        actor = await take(self.created)
        driver = self.drivers[-1]
        assert await take(driver.sent) == "Synthetic prompt"
        return actor, driver

    async def nonce(self, driver: Driver, *, question: bool = False) -> str:
        driver.ask(question=question)
        shown = "Which?" if question else "synthetic hidden argument"
        assert shown in (await take(self.http.sent))[0]
        message = (await take(self.http.sent))[0]
        marker = "!viki answer " if question else "!viki allow "
        return message.split(marker)[1].split()[0]


@pytest.fixture
async def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Rig]:
    value = Rig(tmp_path)
    await value.start(monkeypatch)
    yield value
    await value.bot.close()
    if value.run is not None:
        await value.run
    await value.approvals.close()
    await value.manager.close_all()


async def test_nonce_identity_expiry_and_once(rig: Rig) -> None:
    actor, driver = await rig.chat()
    nonce = await rig.nonce(driver)
    assert len(bytes.fromhex(nonce)) >= 16
    rig.post(f"!viki allow {nonce}", user_id="x" * 26)
    rig.post(f"!viki allow {nonce}", channel_id="x" * 26)
    rig.post(f"!viki answer {nonce} wrong type")
    assert "refused" in (await take(rig.http.sent))[0].lower()
    rig.post(f"!viki allow {nonce}")
    assert (await take(driver.answers))[1] == PermissionAnswer(True)
    assert "delivered_locally" in (await take(rig.http.sent))[0]
    rig.post(f"!viki deny {nonce}")
    assert "refused" in (await take(rig.http.sent))[0].lower()
    assert len(rig.approvals.service.list().approvals) == 1
    nonce = await rig.nonce(driver)
    rig.now = 10**6
    rig.post(f"!viki allow {nonce}")
    assert (await take(driver.answers))[1] == PermissionAnswer(True)
    assert "delivered_locally" in (await take(rig.http.sent))[0]
    assert rig.bot._armed


async def test_permission_reply_works_while_send_waits(rig: Rig) -> None:
    actor, driver = await rig.chat()
    driver.send_gate.clear()
    driver.finish()
    await take(rig.http.sent)
    rig.post("Second synthetic prompt")
    assert await take(driver.sent) == "Second synthetic prompt"
    nonce = await rig.nonce(driver)
    rig.post(f"!viki deny {nonce}")
    assert (await take(driver.answers))[1] == PermissionAnswer(False)
    assert "delivered_locally" in (await take(rig.http.sent))[0]
    assert rig.bot._native_task is not None
    assert rig.bot._actor is actor and not rig.bot._native_task.done()


@pytest.mark.parametrize("failure", ["disconnect", "output", "disarm"])
async def test_bridge_failures_deny_and_cleanup(rig: Rig, failure: str) -> None:
    actor, driver = await rig.chat()
    await rig.nonce(driver)
    if failure == "disconnect":
        rig.incoming.put_nowait(RuntimeError("synthetic body"))
    elif failure == "output":
        rig.http.failure = True
        rig.post("!viki status")
    else:
        rig.bot.disarm()
        assert not rig.bot._cards and not driver.pending()
    assert (await take(driver.answers))[1] == PermissionAnswer(False)
    assert rig.run is not None
    async with asyncio.timeout(0.05):
        await rig.run
    assert rig.http.closed and rig.manager.get(actor.session_id) is None


async def test_question_other_uses_normal_runner(rig: Rig) -> None:
    _, driver = await rig.chat()
    nonce = await rig.nonce(driver, question=True)
    rig.post(f"!viki allow {nonce}")
    assert "refused" in (await take(rig.http.sent))[0].lower()
    rig.post(f"!viki answer {nonce} synthetic free text")
    await take(driver.answers)
    await take(rig.http.sent)
    row = rig.approvals.service.get(rig.approvals.service.list().approvals[0].choice_id)
    assert row.choice == QuestionChoice({}, "synthetic free text")
    assert not isinstance(row.choice, PermissionChoice)


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "x" * 26},
        {"channel_id": "x" * 26},
        {"id": "bad"},
        {"create_at": 999},
        {"edit_at": 1},
        {"delete_at": 1},
        {"type": "system"},
        {"original_id": "x" * 26},
        {"file_ids": ["x" * 26]},
        {"props": {"from_webhook": True}},
        {"props": {"override_username": "Owner"}},
        {"metadata": {"files": [{}]}},
        {"message": "x" * 8193},
    ],
)
async def test_invalid_event_does_not_read_or_create(rig: Rig, changes: dict[str, Any]) -> None:
    invalid = rig.post("Must not dispatch", **changes)
    rig.post("!viki status")
    assert "ready" in (await take(rig.http.sent))[0]
    assert invalid["id"] not in rig.http.reads and rig.created.empty()


@pytest.mark.parametrize("change", ["message", "owner", "edit", "props", "attachment"])
async def test_rest_confirmation_rejects_changed_posts(rig: Rig, change: str) -> None:
    candidate = rig.post("Must not dispatch")
    authoritative = rig.http.posts[candidate["id"]]
    authoritative.update(
        {
            "message": "Different",
            "owner": {"user_id": "x" * 26},
            "edit": {"edit_at": 1},
            "props": {"props": {"from_webhook": True}},
            "attachment": {"file_ids": ["x" * 26]},
        }[change]
        if change != "message"
        else {"message": "Different"}
    )
    rig.post("!viki status")
    await take(rig.http.sent)
    assert candidate["id"] in rig.http.reads and rig.created.empty()


async def test_input_boundary_busy_commands_dedup_and_threads(rig: Rig) -> None:
    root = "t" * 26
    post = rig.post("x" * 8192, root_id=root)
    actor = await take(rig.created)
    driver = rig.drivers[-1]
    assert len(await take(driver.sent)) == 8192
    rig.incoming.put_nowait({**post, "_fresh_cutoff_ms": 1000})
    busy = rig.post("Busy must not queue")
    message, thread = await take(rig.http.sent)
    assert "busy" in message and thread == busy["id"]
    assert rig.http.reads.count(post["id"]) == 1
    driver.finish("Reply with ` @all <html> and \u202e")
    message, thread = await take(rig.http.sent)
    assert thread == root and "\\u0060" in message and "@all" not in message
    for refused in ("/native", "  /native", "!viki model", "!vikifake", "!viki allow"):
        rig.post(refused)
        assert "refused" in (await take(rig.http.sent))[0].lower()
    assert driver.sent.empty() and rig.bot._actor is actor


async def test_sibling_actor_is_not_subscribed_or_controlled(rig: Rig) -> None:
    own, driver = await rig.chat()
    sibling_id = str(uuid.uuid4())
    rig.state.remember(sibling_id, mode="default")
    sibling = rig.manager.create(sibling_id)
    await sibling.send("Desktop synthetic prompt")
    other = rig.drivers[-1]
    await take(other.sent)
    other.ask("desktop-card")
    other.finish("Desktop transcript must stay local")
    rig.post("!viki status")
    assert "Desktop transcript" not in (await take(rig.http.sent))[0]
    assert other.pending() == ["desktop-card"]
    rig.post("!viki stop")
    assert "Stop requested" in (await take(rig.http.sent))[0]
    assert "interrupt" not in other.calls and sibling.live
    assert rig.bot._actor is own and "interrupt" in driver.calls
    rig.post("!viki new")
    assert "new conversation" in (await take(rig.http.sent))[0]
    assert rig.manager.get(own.session_id) is None and rig.manager.get(sibling_id) is sibling
    rig.bot.disarm()
    assert other.pending() == ["desktop-card"] and sibling.live


async def test_paused_approval_cannot_allow_after_synchronous_disarm(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, driver = await rig.chat()
    nonce = await rig.nonce(driver)
    original = approval_runtime.storage_call
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def paused(operation: Any, observer: Any = None) -> Any:
        nonlocal calls
        result = await original(operation, observer)
        calls += 1
        if calls == 3:  # The choice is committed; its local future has not been resolved.
            entered.set()
            await release.wait()
        if getattr(result, "state", "") == "not_delivered":
            finished.set()
        return result

    monkeypatch.setattr(approval_runtime, "storage_call", paused)
    try:
        rig.post(f"!viki allow {nonce}")
        async with asyncio.timeout(HANG_GUARD_S):
            await entered.wait()
        assert nonce not in rig.bot._cards
        rig.bot.disarm()
        assert not driver.pending()
        assert (await take(driver.answers))[1] == PermissionAnswer(False)
        release.set()
        async with asyncio.timeout(HANG_GUARD_S):
            await finished.wait()
        assert rig.approvals.service.list().approvals[0].state == "not_delivered"
        assert driver.answers.empty()
    finally:
        release.set()


@pytest.mark.parametrize("terminal", ["disarm", "new"])
async def test_late_factory_result_is_closed_without_dispatch(
    rig: Rig, terminal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    cancelled = asyncio.Event()

    async def late() -> SessionActor:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
        return await rig.create()

    monkeypatch.setattr(rig.bot, "_create_actor", late)
    rig.post("Delayed creation")
    async with asyncio.timeout(0.05):
        await entered.wait()
    if terminal == "disarm":
        rig.bot.disarm()
    else:
        rig.post("!viki new")
    async with asyncio.timeout(0.05):
        await cancelled.wait()
    release.set()
    actor = await take(rig.created)
    if terminal == "new":
        await take(rig.http.sent)
    else:
        assert rig.run is not None
        async with asyncio.timeout(0.05):
            await rig.run
    assert rig.manager.get(actor.session_id) is None and not rig.drivers


async def test_late_card_during_close_is_denied(rig: Rig) -> None:
    _, driver = await rig.chat()
    driver.interrupt_gate.clear()
    rig.bot.disarm()
    try:
        async with asyncio.timeout(0.05):
            await driver.interrupting.wait()
        driver.ask("late-card")
        assert (await take(driver.answers)) == ("late-card", PermissionAnswer(False))
        assert not rig.bot._cards
    finally:
        driver.interrupt_gate.set()


@pytest.mark.parametrize("failure", ["missing", "oversize", "escaping"])
async def test_incomplete_permission_has_deny_only(rig: Rig, failure: str) -> None:
    _, driver = await rig.chat()
    details = {"tool": "Edit", "input": {"hidden": "x" * 8192}}
    if failure == "escaping":
        details["input"]["hidden"] = "@" * 2200
    driver.inputs["card"] = None if failure == "missing" else details
    driver.emit(PermissionRequested(tool_use_id="card", kind="tool", tool="Edit", summary="Tiny"))
    message = (await take(rig.http.sent))[0]
    assert "desktop" in message and "!viki allow" not in message
    nonce = message.split("!viki deny ")[1].split()[0]
    rig.post(f"!viki allow {nonce}")
    assert "refused" in (await take(rig.http.sent))[0].lower()
    rig.post(f"!viki deny {nonce}")
    assert (await take(driver.answers))[1] == PermissionAnswer(False)
    assert "delivered_locally" in (await take(rig.http.sent))[0]


async def test_full_input_mutation_disarms_before_runner(rig: Rig) -> None:
    _, driver = await rig.chat()
    nonce = await rig.nonce(driver)
    assert driver.inputs["card"] is not None
    driver.inputs["card"]["input"]["complete"] = "Changed after display"
    rig.post(f"!viki allow {nonce}")
    assert (await take(driver.answers))[1] == PermissionAnswer(False)
    assert not rig.approvals.service.list().approvals and not rig.bot._armed


@pytest.mark.parametrize("cap", ["cards", "dedup", "actors"])
async def test_counters_fail_closed_at_exact_limits(rig: Rig, cap: str) -> None:
    _, driver = await rig.chat()
    if cap == "cards":
        for index in range(8):
            driver.ask(f"card-{index}")
        assert len(rig.bot._cards) == 8 and rig.bot._armed
        driver.ask("ninth")
    elif cap == "dedup":
        rig.bot._seen = {f"{index:026d}" for index in range(4096)}
        rig.index = 4096
        rig.post("!viki status")
        async with asyncio.timeout(0.05):
            await rig.bot._stopped.wait()
    else:
        rig.bot._actor_count = 16
        rig.post("!viki new")
        await take(rig.http.sent)
        rig.post("Seventeenth allocation")
        async with asyncio.timeout(0.05):
            await rig.bot._stopped.wait()
    assert not rig.bot._armed


async def test_encoded_output_count_includes_inflight(rig: Rig) -> None:
    _, driver = await rig.chat()
    rig.http.gate.clear()
    rig.bot._enqueue("x" * (12 * 1024), "")
    await take(rig.http.sent)
    for _ in range(31):
        rig.bot._enqueue("x" * (12 * 1024), "")
    assert rig.bot._output_count == 32 and rig.bot._output_bytes == 384 * 1024
    assert rig.bot._armed
    driver.inputs["unshown"] = {"tool": "Edit", "input": {}}
    rig.bot._enqueue("x", "")
    assert not rig.bot._armed and not driver.pending()
    rig.http.gate.set()


async def test_only_final_text_is_projected_and_bounded(rig: Rig) -> None:
    actor, driver = await rig.chat()
    driver.emit(ThinkingDone(block_id="thought", text="Private thinking", duration_ms=1))
    driver.emit(
        ToolDone(block_id="tool", is_error=False, summary="Private tool", preview="Private")
    )
    driver.finish("@" * (64 * 1024 + 1))
    messages = [(await take(rig.http.sent))[0] for _ in range(4)]
    assert all(len(message.encode("utf-8")) <= 12 * 1024 for message in messages)
    assert "shortened" in messages[-1]
    assert all("Private" not in message and "@" not in message for message in messages)
    assert rig.bot._final_bytes == 64 * 1024 - 4
    actor._emit(Error(code="internal", message="Native private error", recoverable=False))
    assert not rig.bot._armed


async def test_chat_reply_and_commands_are_readable_while_request_details_stay_json(
    rig: Rig,
) -> None:
    assert rig.bot._ready_sent
    rig.post("!viki help")
    help_message = (await take(rig.http.sent))[0]
    assert help_message.startswith("Send a message") and not help_message.startswith("```")
    _, driver = await rig.chat()
    driver.ask()
    request = (await take(rig.http.sent))[0]
    assert request.startswith("```json\n")
    instructions = (await take(rig.http.sent))[0]
    assert instructions.startswith("Allow once:") and not instructions.startswith("```")
    driver.finish("A **bold** reply with @all and example.test")
    reply = (await take(rig.http.sent))[0]
    assert "**bold**" in reply and "`example.test`" in reply
    assert "@all" not in reply and not reply.startswith("```")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (" \t\n", "Turn ended. Open VIKI Agent desktop for details."),
        ("x" * (12 * 1024) + "\n", "x" * (12 * 1024)),
        ("  **bold**  ", "  **bold**  "),
    ],
)
async def test_strict_channel_skips_blank_final_posts_and_keeps_conversation(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, reply: str, expected: str
) -> None:
    render = rig.bot.channel.render

    def strict(formatted: str) -> tuple[str, ...]:
        if not formatted.strip():
            raise ValueError("synthetic provider refuses blank post")
        return render(formatted)

    monkeypatch.setattr(rig.bot.channel, "render", strict)
    _, driver = await rig.chat()
    driver.finish(reply)
    assert rig.bot._armed
    message, root = await take(rig.http.sent)
    assert root == rig.bot._thread
    assert message == expected
    await checkpoint()
    assert rig.http.sent.empty() and rig.bot._armed
    rig.post("Next prompt")
    assert await take(driver.sent) == "Next prompt"


async def test_nonce_is_disabled_until_complete_display_and_expires_from_display(rig: Rig) -> None:
    _, driver = await rig.chat()
    rig.http.gate.clear()
    driver.ask()
    await take(rig.http.sent)  # Request content is still in flight.
    nonce = next(iter(rig.bot._cards))
    assert rig.bot._cards[nonce].deadline is None
    rig.now = 100.0
    rig.post(f"!viki allow {nonce}")
    rig.post("!viki status")
    rig.http.gate.set()
    assert "!viki allow" in (await take(rig.http.sent))[0]
    assert "refused" in (await take(rig.http.sent))[0].lower()
    await take(rig.http.sent)
    card = rig.bot._cards[nonce]
    assert card.deadline == math.inf
    assert not rig.approvals.service.list().approvals and driver.pending() == ["card"]
    assert rig.bot._armed


@pytest.mark.parametrize("binding", ["generation", "runtime_id", "request_id", "turn_id", "cancel"])
async def test_stale_bindings_cannot_decide(rig: Rig, binding: str) -> None:
    actor, driver = await rig.chat()
    nonce = await rig.nonce(driver)
    if binding == "generation":
        actor._generation = str(uuid.uuid4())
    elif binding == "cancel":
        actor._emit(RequestCancelled(tool_use_id="card"))
    else:
        actor._native_requests["card"] = {
            **actor._native_requests["card"],
            binding: str(uuid.uuid4()),
        }
    rig.post(f"!viki allow {nonce}")
    assert "refused" in (await take(rig.http.sent))[0].lower()
    assert not rig.approvals.service.list().approvals and driver.answers.empty()


@pytest.mark.parametrize("state", ["not_delivered", "unknown"])
async def test_receipt_reports_existing_state_without_retry(
    rig: Rig, state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, driver = await rig.chat()
    nonce = await rig.nonce(driver)
    original = rig.approvals.decide
    calls = 0

    async def receipt(value: Any) -> Any:
        nonlocal calls
        calls += 1
        summary, created = await original(value)
        return dataclasses.replace(summary, state=state), created

    monkeypatch.setattr(rig.approvals, "decide", receipt)
    rig.post(f"!viki deny {nonce}")
    await take(driver.answers)
    assert state in (await take(rig.http.sent))[0]
    rig.post(f"!viki deny {nonce}")
    await take(rig.http.sent)
    assert calls == 1


async def test_incomplete_question_disarms(rig: Rig) -> None:
    _, driver = await rig.chat()
    driver.inputs["question"] = None
    driver.emit(QuestionAsked(tool_use_id="question", questions=[]))
    assert not rig.bot._armed
    assert (await take(driver.answers)) == ("question", PermissionAnswer(False))


@pytest.mark.parametrize("tool", ["ExitPlanMode", "AskUserQuestion"])
async def test_complete_plan_and_question_are_literal_not_shortened(rig: Rig, tool: str) -> None:
    _, driver = await rig.chat()
    hostile = "` @all <img> \u202e"
    payload = {"plan" if tool == "ExitPlanMode" else "questions": hostile}
    driver.inputs["complete"] = {"tool": tool, "input": payload}
    driver.emit(
        PermissionRequested(
            tool_use_id="complete", kind="plan", tool=tool, summary="Tiny", plan="Summary"
        )
        if tool == "ExitPlanMode"
        else QuestionAsked(
            tool_use_id="complete",
            questions=[
                Question(
                    header=hostile,
                    question=hostile,
                    options=[QuestionOption(label=hostile, description=hostile)],
                )
            ],
        )
    )
    message = (await take(rig.http.sent))[0]
    if tool == "ExitPlanMode":
        assert json.loads(message.removeprefix("```json\n").removesuffix("\n```")) == {
            "tool": tool,
            "input": payload,
        }
    else:
        # The readable post keeps every field whole and inert, and shows no native JSON.
        assert message.startswith("```text\n") and message.endswith("\n```")
        body = message.removeprefix("```text\n").removesuffix("\n```")
        assert not set("`@<>\u202e") & set(body)
        assert body.count("\\u0060 \\u0040all \\u003cimg\\u003e \\u202e") == 4
        assert tool not in message
    assert "@all" not in message and "Summary" not in message
    instructions = (await take(rig.http.sent))[0]
    assert ("!viki allow" in instructions) == (tool == "ExitPlanMode")


async def test_native_failure_disarms_without_replay(rig: Rig) -> None:
    actor, driver = await rig.chat()
    driver.busy = False
    driver.finish()
    await take(rig.http.sent)
    driver.send_gate.clear()
    rig.post("Pending prompt")
    await take(driver.sent)
    rig.bot.disarm()
    driver.send_gate.set()
    assert rig.run is not None
    async with asyncio.timeout(0.05):
        await rig.run
    assert driver.sent.empty() and rig.manager.get(actor.session_id) is None


async def test_full_output_counters_return_to_zero(rig: Rig) -> None:
    _, driver = await rig.chat()
    driver.finish()
    await take(rig.http.sent)
    rig.post("!viki help")
    message, _ = await take(rig.http.sent)
    assert "restart" in message and "not backfilled" in message
    assert rig.bot._output_count == 0 and rig.bot._output_bytes == 0
    rig.bot.disarm()
    rig.bot._reply("Late reply", "")
    assert rig.http.sent.empty()


@pytest.mark.parametrize("failure", ["bind", "cutoff"])
async def test_startup_failure_never_claims_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    value = Rig(tmp_path)

    async def fail() -> Any:
        raise RuntimeError("Synthetic private response")

    monkeypatch.setattr(value.http, "bind" if failure == "bind" else "fresh_cutoff", fail)
    monkeypatch.setattr(module, "posted", value.posted)
    await value.bot.run()
    assert value.http.sent.empty() and value.http.closed and not value.bot._armed
    assert value.created.empty()
    await value.approvals.close()


@pytest.mark.parametrize("text", ["!other", "!VIKI help", "  !unrecognized", "!viki model"])
async def test_unknown_bang_commands_do_not_allocate(rig: Rig, text: str) -> None:
    rig.post(text)
    rig.post("!viki status")
    message = (await take(rig.http.sent))[0]
    assert "refused" in message.lower() and rig.created.empty()


async def checkpoint() -> None:
    event = asyncio.Event()
    asyncio.get_running_loop().call_soon(event.set)
    await event.wait()


async def test_disconnect_observed_during_recorded_allow(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, driver = await rig.chat()
    nonce = await rig.nonce(driver)
    original = approval_runtime.storage_call
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def paused(operation: Any, observer: Any = None) -> Any:
        nonlocal calls
        result = await original(operation, observer)
        calls += 1
        if calls == 3:
            entered.set()
            await release.wait()
        if getattr(result, "state", "") == "not_delivered":
            finished.set()
        return result

    monkeypatch.setattr(approval_runtime, "storage_call", paused)
    rig.post(f"!viki allow {nonce}")
    try:
        async with asyncio.timeout(HANG_GUARD_S):
            await entered.wait()
        assert nonce not in rig.bot._cards
        rig.post("!viki status")  # Later REST work must not hide transport failure either.
        rig.incoming.put_nowait(RuntimeError("Synthetic WebSocket disconnect"))
        await checkpoint()
        assert not rig.bot._armed and not driver.pending()
        assert (await take(driver.answers))[1] == PermissionAnswer(False)
    finally:
        release.set()
    async with asyncio.timeout(HANG_GUARD_S):
        await finished.wait()
    assert rig.approvals.service.list().approvals[0].state == "not_delivered"
    assert driver.answers.empty()


async def test_disconnect_observed_during_rest_confirmation(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, driver = await rig.chat()
    await rig.nonce(driver)
    entered, release = asyncio.Event(), asyncio.Event()
    original = rig.http.post

    async def paused(post_id: str) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return await original(post_id)

    monkeypatch.setattr(rig.http, "post", paused)
    rig.post("!viki status")
    try:
        async with asyncio.timeout(0.05):
            await entered.wait()
        rig.incoming.put_nowait(RuntimeError("Synthetic WebSocket disconnect"))
        await checkpoint()
        assert not rig.bot._armed and not driver.pending()
        assert (await take(driver.answers))[1] == PermissionAnswer(False)
    finally:
        release.set()


@pytest.mark.parametrize("command", ["stop", "new"])
async def test_disconnect_observed_during_owned_control(rig: Rig, command: str) -> None:
    _, driver = await rig.chat()
    driver.interrupt_gate.clear()
    rig.post(f"!viki {command}")
    try:
        async with asyncio.timeout(0.05):
            await driver.interrupting.wait()
        rig.incoming.put_nowait(RuntimeError("Synthetic WebSocket disconnect"))
        await checkpoint()
        assert not rig.bot._armed
        driver.ask("late-during-control")
        assert (await take(driver.answers)) == ("late-during-control", PermissionAnswer(False))
    finally:
        driver.interrupt_gate.set()


async def test_prompt_busy_at_arrival_is_not_dispatched_after_rest_wait(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, driver = await rig.chat()
    entered, release = asyncio.Event(), asyncio.Event()
    original = rig.http.post

    async def paused(post_id: str) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return await original(post_id)

    monkeypatch.setattr(rig.http, "post", paused)
    rig.post("Arrived while busy")
    try:
        async with asyncio.timeout(0.05):
            await entered.wait()
        driver.finish()
        await take(rig.http.sent)
    finally:
        release.set()
    rig.post("!viki status")
    assert "Send your message again" in (await take(rig.http.sent))[0]
    assert driver.sent.empty()


async def test_prompt_during_new_close_is_refused_without_future_allocation(rig: Rig) -> None:
    _, driver = await rig.chat()
    driver.finish()
    await take(rig.http.sent)
    driver.close_gate.clear()
    rig.post("!viki new")
    try:
        async with asyncio.timeout(0.05):
            await driver.closing.wait()
        rig.post("Arrived during close")
        await checkpoint()
    finally:
        driver.close_gate.set()
    assert "new conversation" in (await take(rig.http.sent))[0]
    rig.post("!viki status")
    assert "Send your message again" in (await take(rig.http.sent))[0]
    assert rig.created.empty()


@pytest.mark.parametrize("choice", ["allow", "deny", "answer"])
async def test_current_owned_background_card_after_parent_turn_is_answerable(
    rig: Rig, choice: str
) -> None:
    actor, driver = await rig.chat()
    parent_root = rig.bot._thread
    driver.finish()
    await take(rig.http.sent)
    driver.inputs["background"] = {
        "tool": "AskUserQuestion" if choice == "answer" else "Edit",
        "input": {"complete": "Synthetic subagent operation"},
    }
    driver.emit(
        QuestionAsked(
            tool_use_id="background",
            questions=[Question(header="", question="Synthetic subagent question", options=[])],
        )
        if choice == "answer"
        else PermissionRequested(
            tool_use_id="background",
            kind="tool",
            tool="Edit",
            summary="Edit",
            agent="synthetic-subagent",
        )
    )
    assert driver.answers.empty(), "Normal turn completion must not deny current owned cards"
    assert actor.request_pending("background", actor._native_requests["background"])
    message, thread = await take(rig.http.sent)
    shown = (
        "```text\nQuestion\nQuestion 1 of 1: Synthetic subagent question\n```"
        if choice == "answer"
        else "Synthetic subagent operation"
    )
    assert shown in message and thread == parent_root
    message, thread = await take(rig.http.sent)
    marker = "!viki answer " if choice == "answer" else "!viki allow "
    nonce = message.split(marker)[1].split()[0]
    assert thread == parent_root
    rig.post(f"!viki {choice} {nonce}" + (" Synthetic Other" if choice == "answer" else ""))
    answer = (await take(driver.answers))[1]
    assert answer == (
        QuestionAnswer({}, "Synthetic Other")
        if choice == "answer"
        else PermissionAnswer(choice == "allow")
    )
    assert "delivered_locally" in (await take(rig.http.sent))[0]
    row = rig.approvals.service.get(rig.approvals.service.list().approvals[0].choice_id)
    assert row.choice == (
        QuestionChoice({}, "Synthetic Other")
        if choice == "answer"
        else PermissionChoice(choice == "allow")
    )


async def test_stop_still_denies_current_background_cards(rig: Rig) -> None:
    _, driver = await rig.chat()
    driver.finish()
    await take(rig.http.sent)
    rig.post("!viki stop")
    await take(rig.http.sent)
    driver.inputs["background"] = {"tool": "Edit", "input": {"complete": "Late run"}}
    driver.emit(
        PermissionRequested(
            tool_use_id="background",
            kind="tool",
            tool="Edit",
            summary="Edit",
            agent="synthetic-subagent",
        )
    )
    assert (await take(driver.answers)) == ("background", PermissionAnswer(False))
    assert not rig.bot._cards


@pytest.mark.parametrize("cap", ["count", "bytes"])
async def test_bounded_input_includes_rest_inflight_and_cleans_up(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, cap: str
) -> None:
    actor, driver = await rig.chat()
    await rig.nonce(driver)
    entered, release = asyncio.Event(), asyncio.Event()
    original = rig.http.post

    async def paused(post_id: str) -> dict[str, Any]:
        entered.set()
        await release.wait()
        return await original(post_id)

    monkeypatch.setattr(rig.http, "post", paused)
    padding = {"props": {"padding": "x" * 100_000}} if cap == "bytes" else {}
    first = rig.post("!viki status", **padding)
    try:
        async with asyncio.timeout(0.05):
            await entered.wait()
        assert rig.bot._input_count == 1
        before = rig.bot._input_bytes
        assert before > 100_000 if cap == "bytes" else before > 0
        # An in-flight duplicate retains no second job, prompt slot or REST request.
        rig.incoming.put_nowait({**first, "_fresh_cutoff_ms": 1000})
        await checkpoint()
        assert rig.bot._input_count == 1 and rig.bot._input_bytes == before
        count = 3 if cap == "bytes" else 32
        for _ in range(count - 1):
            rig.post("!viki status", **padding)
        await checkpoint()
        assert rig.bot._input_count == count and rig.bot._armed
        assert rig.bot._input_bytes <= module.MAX_INPUT_BYTES
        rig.post("!viki status", **padding)
        await checkpoint()
        assert not rig.bot._armed and not driver.pending()
    finally:
        release.set()
    assert rig.run is not None
    async with asyncio.timeout(HANG_GUARD_S):
        await rig.run
    assert rig.bot._input_count == 0 and rig.bot._input_bytes == 0
    assert not rig.bot._pending_post_ids and rig.bot._input.empty()
    assert rig.manager.get(actor.session_id) is None and rig.http.closed


async def test_only_one_prompt_waits_for_rest_and_second_arrival_is_refused(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered: asyncio.Queue[str] = asyncio.Queue()
    releases = [asyncio.Event(), asyncio.Event()]
    original = rig.http.post
    count = 0

    async def paused(post_id: str) -> dict[str, Any]:
        nonlocal count
        index = count
        count += 1
        if index < 2:
            entered.put_nowait(post_id)
            await releases[index].wait()
        return await original(post_id)

    monkeypatch.setattr(rig.http, "post", paused)
    first = rig.post("First pending prompt")
    second = rig.post("Second must not become a future turn")
    try:
        assert await take(entered) == first["id"]
        assert rig.bot._prompt_post_id == first["id"] and rig.bot._input_count == 2
        releases[0].set()
        await take(rig.created)
        driver = rig.drivers[-1]
        assert await take(driver.sent) == first["message"]
        assert await take(entered) == second["id"]
        driver.finish()
        await take(rig.http.sent)
        releases[1].set()
        assert "Send your message again" in (await take(rig.http.sent))[0]
        assert driver.sent.empty()
        assert rig.bot._prompt_post_id is None
    finally:
        for release in releases:
            release.set()


async def test_consumed_choice_still_counts_toward_eight_live_bindings(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, driver = await rig.chat()
    nonce = await rig.nonce(driver)
    original = approval_runtime.storage_call
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def paused(operation: Any, observer: Any = None) -> Any:
        nonlocal calls
        result = await original(operation, observer)
        calls += 1
        if calls == 3:
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(approval_runtime, "storage_call", paused)
    rig.post(f"!viki allow {nonce}")
    try:
        async with asyncio.timeout(HANG_GUARD_S):
            await entered.wait()
        assert nonce not in rig.bot._cards and rig.bot._deciding_card is not None
        for index in range(7):
            driver.ask(f"parallel-{index}")
        assert rig.bot._armed and len(rig.bot._cards) == 7
        driver.ask("ninth-live-binding")
        assert not rig.bot._armed and not driver.pending()
    finally:
        release.set()


async def test_displayed_run_card_retains_nonce_across_parent_completion(rig: Rig) -> None:
    actor, driver = await rig.chat()
    driver.inputs["background"] = {"tool": "Edit", "input": {"complete": "Run operation"}}
    driver.emit(
        PermissionRequested(
            tool_use_id="background",
            kind="tool",
            tool="Edit",
            summary="Edit",
            agent="synthetic-subagent",
        )
    )
    await take(rig.http.sent)
    nonce = (await take(rig.http.sent))[0].split("!viki allow ")[1].split()[0]
    native = dict(actor._native_requests["background"])
    driver.finish()
    await take(rig.http.sent)
    assert actor.request_pending("background", native) and nonce in rig.bot._cards
    rig.post(f"!viki allow {nonce}")
    assert (await take(driver.answers)) == ("background", PermissionAnswer(True))
    assert "delivered_locally" in (await take(rig.http.sent))[0]
