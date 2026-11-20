"""SessionActor + ClaudeDriver over the in-process fake: turns, cards, stops, crashes, respawns,
guards. Real frames come from the recorded fixtures; edge cases use synthetic scripts."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import os
import threading
import traceback
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from claude_agent_sdk import CLIConnectionError

from viki_agent import winproc
from viki_agent.app.diagnostics import Readiness
from viki_agent.conversations.approval_runtime import ApprovalRunner
from viki_agent.conversations.approvals import (
    ApprovalService,
    Choice,
    ChoiceInput,
    NativeRequest,
    PermissionChoice,
    QuestionChoice,
)
from viki_agent.domain import StorageReadiness
from viki_agent.drivers.base import Answer, PermissionAnswer, QuestionAnswer, StartError, StartFacts
from viki_agent.drivers.claude.control import Launch
from viki_agent.drivers.claude.driver import (
    MSG_CRASHED,
    MSG_STOP_AT_DRAFT,
    MSG_STOP_KILLED,
    ClaudeDriver,
    DriverConfig,
    check_billing,
    classify_exit,
    menu_commands,
)
from viki_agent.drivers.claude.mapper import Mapper
from viki_agent.events import (
    Error,
    Event,
    OtherSession,
    PermissionRequested,
    SessionStarted,
    TurnDone,
    Usage,
)
from viki_agent.session import (
    MSG_CLI_CONFIG_DIR,
    MSG_CLI_MISSING,
    MSG_CLI_SHIM,
    MSG_LOST,
    MSG_MODEL,
    MSG_START_FAILED,
    CommandDispatchRejected,
    CommandDispatchResult,
    SessionActor,
    SessionManager,
    find_holder,
    vault_sessions,
)
from viki_agent.state import AppState, Prefs, user_defaults
from viki_agent.storage import StateError
from viki_agent.turn_input import Attachment, AttachmentUnavailable, TurnInput

from replay import (
    DEFAULT_INIT,
    SID,
    Entry,
    FakeSpawner,
    expect,
    init_frame,
    load_script,
    out,
    permission_request,
    permission_response_in,
    result_frame,
    text_delta,
    user_in,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "claude"
# Login is the one provider fact Readiness still reports only through a capabilities document;
# storage state does not affect it.
_READY = StorageReadiness(state="ready", reason=None, private=True)


def _login(readiness: Readiness) -> str:
    return readiness.capabilities(None, _READY).provider["claude"].login


FORK = "44444444-4444-4444-8444-444444444444"

# A guard on a wait that normally settles in milliseconds; it never fires in a healthy run.
HANG_GUARD_S = 5


class Seen:
    """A subscriber that records envelopes and lets a test wait without polling."""

    def __init__(self) -> None:
        self.envelopes: list[dict[str, Any]] = []
        self._changed = asyncio.Event()

    def __call__(self, envelope: dict[str, Any]) -> None:
        self.envelopes.append(envelope)
        self._changed.set()

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.envelopes[index]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.envelopes)

    def __len__(self) -> int:
        return len(self.envelopes)

    async def until(self, event_type: str, count: int = 1, timeout: float = 3.0) -> dict[str, Any]:
        async with asyncio.timeout(timeout):
            while len(found := [e for e in self if e["type"] == event_type]) < count:
                self._changed.clear()
                await self._changed.wait()
        return found[count - 1]

    @property
    def types(self) -> list[str]:
        return [e["type"] for e in self]


def setup(
    tmp_path: Path,
    script: list[Entry],
    *,
    home: Path | None = None,
    stop_pickup_s: float = 30.0,
    readiness: Readiness | None = None,
    **spawner_options: Any,
) -> tuple[SessionManager, FakeSpawner]:
    spawner = FakeSpawner(script, **spawner_options)
    config = DriverConfig(
        cli=("claude.exe",),
        vault=Path("C:/Vault"),
        env={},
        cli_version="2.1.259",
        skills=frozenset({"capture"}),
        spawner=spawner,
        grace_s=0.05,
        poll_s=0.05,
        control_timeout_s=0.05,
        # Left at the production value unless a test is about the deadline itself: every other
        # test reaches system/init in milliseconds, so a short one would only add flakiness.
        stop_pickup_s=stop_pickup_s,
    )
    state = AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low"))
    claude_home = home or tmp_path / "home"
    (claude_home / "sessions").mkdir(parents=True, exist_ok=True)
    manager = SessionManager(
        config=config, state=state, claude_home=claude_home, readiness=readiness
    )
    return manager, spawner


async def eventually(flag: Callable[[], bool], turns: int = 1000) -> None:
    """Yield until a flag the driver sets right after an event the test has already seen."""
    for _ in range(turns):
        if flag():
            return
        await asyncio.sleep(0)
    raise AssertionError("the flag never settled")


def watch(actor: SessionActor) -> Seen:
    seen = Seen()
    actor.subscribe(seen)
    return seen


class GatedDriver:
    """An AgentDriver whose open() finishes only when the test sets `release`; it records
    every call in order."""

    pid: int | None = None
    busy = False

    def __init__(self) -> None:
        self.alive = False
        self.opening = asyncio.Event()
        self.release = asyncio.Event()
        self.closing = asyncio.Event()
        self.close_gate = asyncio.Event()  # close() finishes only once this is set
        self.close_gate.set()
        self.send_error: Exception | None = None
        self.calls: list[str] = []

    async def open(self) -> SessionStarted:
        self.calls.append("open")
        self.opening.set()
        await self.release.wait()
        self.alive = True
        self.calls.append("opened")
        return SessionStarted(
            model="default", mode="auto", effort="low", commands=[], agents=[], models=[],
            cli_version="",
        )  # fmt: skip

    async def send(self, text: str) -> None:
        self.calls.append(f"send {text}")
        if self.send_error:
            raise self.send_error

    async def interrupt(self) -> None:
        self.calls.append("interrupt")

    def respond(self, tool_use_id: str, answer: Answer) -> bool:
        return False

    def pending(self) -> list[str]:
        return []

    async def set_model(self, model: str) -> None:
        self.calls.append(f"model {model}")

    async def close(self) -> None:
        self.closing.set()
        await self.close_gate.wait()
        self.alive = False
        self.calls.append("close")


class Drivers:
    """A make_driver that builds a fresh GatedDriver per process and keeps what each one got.
    With `gate_first`, the first open() waits for the test; the rest open at once."""

    def __init__(self, *, gate_first: bool = False) -> None:
        self.made: list[tuple[GatedDriver, Launch, Prefs, Callable[[Event], None]]] = []
        self.created = asyncio.Event()
        self._gate_first = gate_first

    def __call__(self, launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> GatedDriver:
        driver = GatedDriver()
        if self.made or not self._gate_first:
            driver.release.set()
        self.made.append((driver, launch, prefs, emit))
        self.created.set()
        return driver


def gated(tmp_path: Path, driver: GatedDriver | Drivers) -> SessionManager:
    config = DriverConfig(cli=("claude.exe",), vault=Path("C:/Vault"), env={})
    state = AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low"))
    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    make = driver if isinstance(driver, Drivers) else lambda *_: driver
    return SessionManager(config=config, state=state, claude_home=home, make_driver=make)


def fixture(name: str) -> list[Entry]:
    return load_script(FIXTURES / f"{name}.jsonl")


def aborted() -> dict[str, Any]:
    frame = result_frame(subtype="error_during_execution")
    return {**frame, "is_error": True, "terminal_reason": "aborted_streaming"}


def interrupt_in(recorded_id: str = "rec-int") -> Entry:
    return expect(
        {"type": "control_request", "request_id": recorded_id, "request": {"subtype": "interrupt"}}
    )


def interrupt_ack(recorded_id: str = "rec-int") -> Entry:
    response = {"subtype": "success", "request_id": recorded_id, "response": {"still_queued": []}}
    return out({"type": "control_response", "response": response})


def control_in(subtype: str, recorded_id: str) -> Entry:
    request = {"subtype": subtype}
    return expect({"type": "control_request", "request_id": recorded_id, "request": request})


def control_error(recorded_id: str, error: str = "refused") -> Entry:
    response = {"subtype": "error", "request_id": recorded_id, "error": error}
    return out({"type": "control_response", "response": response})


def sent_responses(spawner: FakeSpawner, child: int = 0) -> list[dict[str, Any]]:
    seen = spawner.children[child].process.replayer.seen
    return [f["response"] for f in seen if f["type"] == "control_response"]


# --- turns and the event log --------------------------------------------------------------------


async def test_terminal_provider_start_error_preserves_non_retryable_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = GatedDriver()
    actor = gated(tmp_path, driver).create(SID)
    seen = watch(actor)

    async def refuse() -> SessionStarted:
        raise StartError("frame_error", "synthetic intake overflow")

    monkeypatch.setattr(driver, "open", refuse)
    await actor.send("must not dispatch")
    assert seen.types == ["error"]
    assert seen[0]["data"] == {
        "code": "frame_error",
        "message": "synthetic intake overflow",
        "recoverable": False,
    }
    assert driver.calls == [] and not actor.live and not actor.busy
    await actor.close()


async def test_send_after_overflow_latch_preserves_terminal_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("viki_agent.drivers.claude.driver.FRAME_QUEUE_ITEMS", 4)
    manager, spawner = setup(tmp_path, [])
    actor = manager.create(SID)
    seen = watch(actor)
    assert await actor.start()
    driver = actor._driver
    assert isinstance(driver, ClaudeDriver)
    try:
        # Same event-loop step: the actor still holds this driver, but its intake is closed.
        for _ in range(5):
            driver._enqueue_frame(text_delta("synthetic backlog"), 100)
        await actor.send("must not reach the provider")
        errors = [event["data"] for event in seen if event["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == "frame_error" and not errors[0]["recoverable"]
        assert [e["data"]["status"] for e in seen if e["type"] == "turn.done"] == ["error"]
        assert not actor.live and len(spawner.children) == 1
        assert not any(
            frame.get("type") == "user" for frame in spawner.children[0].process.replayer.seen
        )
    finally:
        await manager.close_all()
    assert spawner.children[0].released


async def test_overflow_while_send_waits_for_stdin_has_one_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("viki_agent.drivers.claude.driver.FRAME_QUEUE_ITEMS", 4)
    manager, spawner = setup(tmp_path, [])
    actor = manager.create(SID)
    seen = watch(actor)
    assert await actor.start()
    driver = actor._driver
    assert isinstance(driver, ClaudeDriver)
    await driver._transport._stdin_lock.acquire()
    sending = asyncio.create_task(actor.send("must not reach the provider"))
    held = True
    try:
        await eventually(lambda: driver.busy)
        for _ in range(5):
            driver._enqueue_frame(text_delta("synthetic backlog"), 100)
        await seen.until("error", timeout=0.05)
        driver._transport._stdin_lock.release()
        held = False
        await asyncio.wait_for(sending, 0.05)
        errors = [event["data"] for event in seen if event["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["code"] == "frame_error" and not errors[0]["recoverable"]
        assert [e["data"]["status"] for e in seen if e["type"] == "turn.done"] == ["error"]
        assert not any(
            frame.get("type") == "user" for frame in spawner.children[0].process.replayer.seen
        )
    finally:
        if held:
            driver._transport._stdin_lock.release()
        await sending
        await driver.close()
        await manager.close_all()


async def test_a_new_session_streams_turns_with_contiguous_seq(tmp_path: Path) -> None:
    manager, _ = setup(tmp_path, fixture("text-two-turns"))
    actor = manager.create(SID)
    seen = watch(actor)
    assert await actor.start()
    await actor.send("What is prime directive 1?")
    await seen.until("turn.done")
    await actor.send("Reply with B")
    done = await seen.until("turn.done", count=2)
    assert done["data"]["status"] == "success"
    assert [e["seq"] for e in seen] == list(range(1, len(seen) + 1))
    assert seen.types[:3] == ["session.started", "user.message", "turn.started"]
    started = seen[0]["data"]
    assert (
        started["mode"] == "auto"
        and started["effort"] == "low"
        and started["cli_version"] == "2.1.259"
    )
    names = [c["name"] for c in started["commands"]]
    assert "clear" not in names and "doctor" not in names
    assert started["commands"][0] == {**started["commands"][0], "name": "capture", "skill": True}
    await actor.close()
    assert seen.types[-1] == "session.closed"


async def test_reconnecting_clients_get_finished_blocks_without_their_deltas(
    tmp_path: Path,
) -> None:
    manager, _ = setup(tmp_path, fixture("text-two-turns"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.start()
    await actor.send("What is prime directive 1?")
    await seen.until("turn.done")
    replay = actor.events_after(1)
    assert "text.delta" not in [e["type"] for e in replay]
    assert [e["type"] for e in replay][-2:] == ["text.done", "turn.done"]
    assert actor.events_after(actor.seq) == []
    await actor.close()


async def test_write_capable_tools_mark_the_session(tmp_path: Path) -> None:
    manager, _ = setup(tmp_path, fixture("bash-hook-block"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("run the canary")
    await seen.until("turn.done")
    assert actor.wrote
    assert "hook" in seen.types
    await actor.close()


# --- cards --------------------------------------------------------------------------------------


async def test_a_permission_card_round_trips_to_the_cli(tmp_path: Path) -> None:
    manager, spawner = setup(tmp_path, fixture("webfetch-denied"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Use WebFetch on https://example.com")
    card = (await seen.until("permission.requested"))["data"]
    assert card["kind"] == "tool" and card["tool"] == "WebFetch"
    # The rest only: the card prints the tool's name itself, in its own colour.
    assert card["summary"] == "example.com"
    assert actor.respond(card["tool_use_id"], PermissionAnswer(allow=False))
    assert not actor.respond(card["tool_use_id"], PermissionAnswer(allow=True))  # already answered
    await seen.until("turn.done")
    sent = [f for f in spawner.children[0].process.replayer.seen if f["type"] == "control_response"]
    assert sent[0]["response"]["response"] == {
        "behavior": "deny",
        "message": "The owner denied this request.",
    }
    await actor.close()


async def test_a_question_card_answers_through_updated_input(tmp_path: Path) -> None:
    manager, spawner = setup(tmp_path, fixture("ask-user-question"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Ask me tea or coffee")
    card = (await seen.until("question.asked"))["data"]
    question = card["questions"][0]
    assert [o["label"] for o in question["options"]] == ["Tea", "Coffee"]
    actor.respond(card["tool_use_id"], QuestionAnswer(answers={question["question"]: "Tea"}))
    await seen.until("turn.done")
    sent = [f for f in spawner.children[0].process.replayer.seen if f["type"] == "control_response"]
    reply = sent[0]["response"]["response"]
    assert reply["behavior"] == "allow"
    assert reply["updatedInput"]["answers"] == {question["question"]: "Tea"}
    await actor.close()


async def test_an_approved_plan_restores_the_mode_before_it(tmp_path: Path) -> None:
    manager, spawner = setup(tmp_path, fixture("plan-mode-exit"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Make a plan")
    card = (await seen.until("permission.requested"))["data"]
    assert card["kind"] == "plan" and card["plan"]
    actor.respond(card["tool_use_id"], PermissionAnswer(allow=True))
    await seen.until("turn.done")
    sent = [f for f in spawner.children[0].process.replayer.seen if f["type"] == "control_response"]
    reply = sent[0]["response"]["response"]
    assert reply["updatedPermissions"] == [
        {"type": "setMode", "mode": "auto", "destination": "session"}
    ]
    await actor.close()


@pytest.mark.parametrize(
    ("name", "kind"), [("webfetch-denied", "tool"), ("plan-mode-exit", "plan")]
)
async def test_a_stop_at_draft_denial_sends_the_fixed_reason(
    tmp_path: Path, name: str, kind: str
) -> None:
    manager, spawner = setup(tmp_path, fixture(name))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Go")
    card = (await seen.until("permission.requested"))["data"]
    assert card["kind"] == kind
    assert actor.respond(card["tool_use_id"], PermissionAnswer(allow=False, stop_at_draft=True))
    await seen.until("turn.done")
    sent = [f for f in spawner.children[0].process.replayer.seen if f["type"] == "control_response"]
    assert sent[0]["response"]["response"] == {"behavior": "deny", "message": MSG_STOP_AT_DRAFT}
    await actor.close()


# --- stopping -------------------------------------------------------------------------------------


async def test_interrupt_mid_stream_then_the_next_turn(tmp_path: Path) -> None:
    manager, _ = setup(tmp_path, fixture("interrupt-mid-stream"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Count to 300")
    await seen.until("text.delta")
    await actor.interrupt()
    assert (await seen.until("turn.done"))["data"]["status"] == "cancelled"
    await actor.send("Reply with done")
    assert (await seen.until("turn.done", count=2))["data"]["status"] == "success"
    await actor.close()


async def test_a_stop_before_the_turn_starts_is_sent_once_it_does(tmp_path: Path) -> None:
    script = [
        user_in(),
        out({"type": "fake.sleep", "s": 0.05}),  # claude is still in its SessionStart hook
        out(init_frame()),
        out(text_delta("x")),
        interrupt_in(),
        interrupt_ack(),
        out(aborted()),
    ]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await actor.interrupt()
    replayer = spawner.children[0].process.replayer
    assert [f["type"] for f in replayer.seen] == ["control_request", "user"]  # held back
    assert (await seen.until("turn.done"))["data"]["status"] == "cancelled"
    assert replayer.seen[2]["request"]["subtype"] == "interrupt"
    await actor.close()


async def test_a_stop_the_cli_never_picks_up_kills_the_tree(tmp_path: Path) -> None:
    """A child wedged before system/init — a SessionStart hook that never returns, an MCP server
    that never answers — used to swallow Stop entirely: the flag flipped, no layer ever ran, and
    the composer stayed blocked with no way out but closing the session."""
    manager, spawner = setup(tmp_path, [user_in()], stop_pickup_s=0.05)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await actor.interrupt()  # nothing can be sent yet: the CLI has not entered the turn
    assert (await seen.until("turn.done"))["data"]["status"] == "cancelled"
    error = (await seen.until("error"))["data"]
    assert error == {"code": "forced_stop", "message": MSG_STOP_KILLED, "recoverable": True}
    assert seen.types[-2:] == ["turn.done", "error"]
    assert spawner.children[0].killed and not actor.live


async def test_a_turn_that_ends_before_the_cli_picks_it_up_disarms_the_stop(
    tmp_path: Path,
) -> None:
    """The waiting stop belongs to the turn that armed it. Left running it would fire into the
    next one — an interrupt the user never asked for, or a kill of a process that had moved on."""
    script = [
        *(user_in(), out({"type": "fake.sleep", "s": 0.05}), out(result_frame())),
        *(user_in(), out(init_frame()), out(result_frame())),
    ]
    manager, spawner = setup(tmp_path, script, stop_pickup_s=0.1)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await actor.interrupt()  # armed: no system/init has arrived for this turn
    await seen.until("turn.done")  # ... and the turn ends without one ever arriving
    await actor.send("again")
    assert (await seen.until("turn.done", count=2))["data"]["status"] == "success"
    replayer = spawner.children[0].process.replayer
    asked = [f["request"]["subtype"] for f in replayer.seen if f["type"] == "control_request"]
    assert "interrupt" not in asked and not spawner.children[0].killed
    await actor.close()


async def test_an_unacknowledged_interrupt_kills_the_process_tree(tmp_path: Path) -> None:
    script = [user_in(), out(init_frame()), out(text_delta("x")), interrupt_in()]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("text.delta")
    await actor.interrupt()
    # The user asked for the stop, so the turn reads as cancelled, not as a crash.
    assert (await seen.until("turn.done"))["data"]["status"] == "cancelled"
    error = (await seen.until("error"))["data"]
    assert error == {"code": "forced_stop", "message": MSG_STOP_KILLED, "recoverable": True}
    assert seen.types[-2:] == ["turn.done", "error"]
    assert spawner.children[0].killed
    assert not actor.live
    await eventually(lambda: spawner.children[0].released)  # the Job goes with it


async def test_a_crash_is_reported_and_the_next_send_resumes(tmp_path: Path) -> None:
    script = [
        user_in(),
        out(init_frame()),
        out({"type": "fake.stderr", "text": "segfault\n"}),
        out({"type": "fake.exit", "code": 3}),
    ]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    error = (await seen.until("error"))["data"]
    assert error == {"code": "crashed", "message": MSG_CRASHED, "recoverable": True}
    assert seen.types[-2] == "turn.done" and not actor.live
    await actor.send("again")
    await seen.until("error", count=2)
    assert f"--resume={SID}" in spawner.children[1].argv
    assert spawner.children[0].released  # the crashed driver released its Job


async def test_a_crash_while_idle_is_reported_without_a_turn_done(tmp_path: Path) -> None:
    """The process can end on its own between turns, not just mid-turn. With no turn running,
    _on_exit must not manufacture a TurnDone that never happened."""
    script = [
        user_in(),
        out(init_frame()),
        out(result_frame()),
        out({"type": "fake.stderr", "text": "segfault\n"}),
        out({"type": "fake.exit", "code": 3}),
    ]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("turn.done")
    error = (await seen.until("error"))["data"]
    assert error == {"code": "crashed", "message": MSG_CRASHED, "recoverable": True}
    assert seen.types[-2:] == ["turn.done", "error"]  # no second turn.done manufactured
    assert not actor.live


# --- respawns, forks, guards ----------------------------------------------------------------------


async def test_an_effort_change_before_any_send_reuses_the_session_id(tmp_path: Path) -> None:
    script = [user_in(), out(init_frame()), out(result_frame())]
    manager, spawner = setup(tmp_path, script, state_dir=tmp_path / "fake-sessions")
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.start()
    actor.set_effort("max")
    await actor.send("hello")
    await seen.until("turn.done")
    second = spawner.children[1].argv
    assert f"--session-id={SID}" in second and "--effort=max" in second
    assert not any(a.startswith("--resume") for a in second)  # fake_claude would refuse it
    await actor.close()


async def test_an_effort_change_after_a_turn_resumes(tmp_path: Path) -> None:
    script = [user_in(), out(init_frame()), out(result_frame())]
    manager, spawner = setup(tmp_path, script, state_dir=tmp_path / "fake-sessions")
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("turn.done")
    actor.set_effort("medium")
    await actor.send("again")
    await seen.until("turn.done", count=2)
    assert f"--resume={SID}" in spawner.children[1].argv
    assert "--effort=medium" in spawner.children[1].argv
    await actor.close()


async def test_a_fork_writes_under_the_fork_id(tmp_path: Path) -> None:
    manager, spawner = setup(tmp_path, fixture("text-two-turns"))
    actor = manager.fork(SID, FORK)
    seen = watch(actor)
    await actor.send("What is prime directive 1?")
    await seen.until("turn.done")
    argv = spawner.children[0].argv
    assert f"--resume={SID}" in argv and "--fork-session" in argv
    assert f"--session-id={FORK}" in argv
    assert seen[0]["data"]["resumed"] is True
    assert all(e["session_id"] == FORK for e in seen)
    await actor.close()


async def test_transcript_expected_follows_the_launch(tmp_path: Path) -> None:
    """A missing transcript is reported as such only when the CLI should already have
    written it: a resumed session, a fork, or a dropped process re-armed to resume."""
    script = [
        user_in(),
        out(init_frame()),
        out({"type": "fake.stderr", "text": "segfault\n"}),
        out({"type": "fake.exit", "code": 3}),
    ]
    manager, _spawner = setup(tmp_path, script)
    assert manager.open(SID).transcript_expected  # resumed: the file exists by definition
    assert manager.fork(SID, FORK).transcript_expected  # its history is the source's file
    fresh = manager.create("33333333-3333-4333-8333-333333333333")
    assert not fresh.transcript_expected  # the CLI writes the file on the first turn
    seen = watch(fresh)
    await fresh.send("hello")
    await seen.until("error")
    assert fresh.transcript_expected  # the crash re-armed a resume: the file is expected now


async def test_a_session_live_elsewhere_is_blocked_before_any_spawn(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True)
    me = os.getpid()
    entry = {
        "pid": me,
        "sessionId": SID,
        "procStart": str(winproc.process_start_time(me)),
        "entrypoint": "claude-vscode",
    }
    (home / "sessions" / f"{me}.json").write_text(json.dumps(entry), encoding="utf-8")
    manager, spawner = setup(tmp_path, [], home=home)
    actor = manager.open(SID)
    seen = watch(actor)
    await actor.send("hello")
    blocked = (await seen.until("session.blocked"))["data"]
    assert blocked["holder"] == {
        "session_id": SID,
        "entrypoint": "claude-vscode",
        "pid": me,
        "title": "",
    }
    assert spawner.children == [] and not actor.live


async def test_the_live_elsewhere_check_reads_the_registry_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On macOS find_holder runs ps: it must not block the loop. own_pids() iterates the actor
    # tables, which change only on the loop, so it must still run there.
    loop_thread = threading.get_ident()
    calls: list[tuple[str, int, Path, str, set[int]]] = []
    holder = OtherSession(session_id=SID, entrypoint="cli", pid=4242)

    def fake_find_holder(claude_home: Path, session_id: str, own: set[int]) -> OtherSession:
        calls.append(("find_holder", threading.get_ident(), claude_home, session_id, own))
        return holder

    manager, spawner = setup(tmp_path, [])
    real_own_pids = manager.own_pids

    def own_pids() -> set[int]:
        calls.append(("own_pids", threading.get_ident(), tmp_path, "", set()))
        return real_own_pids() | {777}

    monkeypatch.setattr("viki_agent.session.find_holder", fake_find_holder)
    monkeypatch.setattr(manager, "own_pids", own_pids)
    actor = manager.open(SID)
    seen = watch(actor)
    await actor.send("hello")
    blocked = (await seen.until("session.blocked"))["data"]
    assert blocked["holder"]["pid"] == 4242
    assert [(name, thread == loop_thread) for name, thread, *_ in calls] == [
        ("own_pids", True),
        ("find_holder", False),
    ]
    assert calls[1][2:] == (tmp_path / "home", SID, {777})
    assert spawner.children == [] and not actor.live


@pytest.mark.parametrize("path", ["send", "send_command"])
async def test_an_effort_picked_during_the_live_elsewhere_check_is_the_one_spawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    # set_effort takes no lock, so it can land while a resumed start waits on the registry read:
    # the process must run what the store holds after that wait, not the prefs read before it.
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), threading.Event()

    def parked_find_holder(claude_home: Path, session_id: str, own: set[int]) -> None:
        loop.call_soon_threadsafe(entered.set)
        release.wait(HANG_GUARD_S)

    async def accept() -> str:
        return "hello"

    async def intent() -> None:
        pass

    monkeypatch.setattr("viki_agent.session.find_holder", parked_find_holder)
    drivers = Drivers()
    actor = gated(tmp_path, drivers).open(SID)
    attempt: asyncio.Future[Any]
    if path == "send":
        attempt = asyncio.ensure_future(actor.send("hello"))
    else:
        attempt = asyncio.ensure_future(
            actor.send_command(
                expected_generation=actor.generation, accept=accept, dispatch_intent=intent
            )
        )
    try:
        await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        assert actor.set_effort("high")
    finally:
        release.set()
    result = await attempt
    [(_, _, prefs, _)] = drivers.made
    assert prefs.effort == "high" and not actor._effort_pending and actor.live
    # A command still sees the configuration move under it; the user's resend then reaches a
    # process that already runs the new effort.
    changed = CommandDispatchResult("not_dispatched", "runtime_changed")
    assert result == (True if path == "send" else changed)
    await actor.close()


async def test_a_state_error_on_the_prefs_re_read_refuses_the_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = asyncio.get_running_loop()
    entered, release = asyncio.Event(), threading.Event()

    def parked_find_holder(claude_home: Path, session_id: str, own: set[int]) -> None:
        loop.call_soon_threadsafe(entered.set)
        release.wait(HANG_GUARD_S)

    monkeypatch.setattr("viki_agent.session.find_holder", parked_find_holder)
    drivers = Drivers()
    actor = gated(tmp_path, drivers).open(SID)
    seen = watch(actor)
    attempt = asyncio.ensure_future(actor.send("hello"))
    # set_effort lands during the live-elsewhere wait, so the start re-reads the prefs; it fails.
    with monkeypatch.context() as patch:
        try:
            await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
            assert actor.set_effort("high")
            patch.setattr(AppState, "launch_prefs", unavailable_state)
        finally:
            release.set()
        await attempt
    assert seen.types == ["error"]
    assert not drivers.made and not actor.live
    await actor.close()


async def test_a_send_waits_for_a_start_that_is_still_opening(tmp_path: Path) -> None:
    driver = GatedDriver()
    actor = gated(tmp_path, driver).create(SID)
    start = asyncio.create_task(actor.start())  # the eager start of a new session
    await driver.opening.wait()
    send = asyncio.create_task(actor.send("hello"))
    done, _ = await asyncio.wait({send}, timeout=0.05)
    assert not done  # waiting for the start, not writing to a process still initializing
    driver.release.set()
    assert await start
    await send
    assert driver.calls == ["open", "opened", "send hello"]


async def test_since_marks_the_first_start_and_forks_read_their_source(tmp_path: Path) -> None:
    driver = GatedDriver()
    driver.release.set()
    manager = gated(tmp_path, driver)
    actor = manager.open(SID)
    assert actor.since is None and actor.history_source == SID
    await actor.start()
    since = actor.since
    assert since and since.endswith("Z") and since[:4].isdigit()
    await actor.close()
    await actor.start()
    assert actor.since == since  # the log still covers everything from the first start
    assert manager.fork(SID, FORK).history_source == SID


async def test_closing_through_the_manager_forgets_the_session(tmp_path: Path) -> None:
    driver = GatedDriver()
    driver.release.set()
    manager = gated(tmp_path, driver)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.start()
    await manager.close(SID)
    assert manager.get(SID) is None and not actor.live
    assert seen.types[-1] == "session.closed" and driver.calls[-1] == "close"
    await manager.close(SID)  # closing an unknown session is a no-op


async def test_at_most_eight_sessions_run_at_once(tmp_path: Path) -> None:
    manager, _ = setup(tmp_path, [])
    manager.bind_max_live(lambda: 8)
    actors = [manager.create(f"0000000{i}-0000-4000-8000-000000000000") for i in range(9)]
    seen = watch(actors[8])
    for actor in actors[:8]:
        assert await actor.start()
    assert not await actors[8].start()
    assert (await seen.until("error"))["data"]["code"] == "too_many_sessions"
    await actors[0].close()
    assert await actors[8].start()
    await manager.close_all()


async def test_the_billing_gate_refuses_non_subscription_auth(tmp_path: Path) -> None:
    reply = {"account": {"apiProvider": "bedrock", "subscriptionType": ""}, "commands": []}
    script = [
        expect(
            {
                "type": "control_request",
                "request_id": "rec-init",
                "request": {"subtype": "initialize"},
            }
        ),
        out(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": "rec-init", "response": reply},
            }
        ),
    ]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    assert not await actor.start()
    error = (await seen.until("error"))["data"]
    assert error["code"] == "billing" and error["recoverable"] is False
    assert spawner.children[0].released and not actor.live


# --- turns the user did not send, and frames nobody expected ------------------------------------


async def test_a_command_is_labelled_only_when_the_session_offers_it(tmp_path: Path) -> None:
    two_turns = [user_in(), out(init_frame()), out(result_frame())] * 2
    manager, spawner = setup(tmp_path, two_turns)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("/capture buy milk")
    await seen.until("turn.done")
    await actor.send("/Users/me/notes.md summarize this")
    await seen.until("turn.done", count=2)
    messages = [e["data"] for e in seen if e["type"] == "user.message"]
    assert [(m["command"], m["text"]) for m in messages] == [
        ("/capture", "buy milk"),  # the history form: a /cmd chip plus its arguments
        ("", "/Users/me/notes.md summarize this"),
    ]
    seen_by_cli = spawner.children[0].process.replayer.seen
    written = [f["message"]["content"] for f in seen_by_cli if f["type"] == "user"]
    assert written == ["/capture buy milk", "/Users/me/notes.md summarize this"]  # byte-for-byte
    await actor.close()


async def test_a_turn_the_cli_starts_itself_runs_like_any_other(tmp_path: Path) -> None:
    # A background agent's completion wakes claude for a continuation turn.
    manager, _ = setup(tmp_path, fixture("background-agent"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Launch an Explore agent in the background")
    await seen.until("turn.done", count=2)
    lifecycle = [t for t in seen.types if t in ("user.message", "turn.started", "turn.done")]
    assert lifecycle == ["user.message", "turn.started", "turn.done", "turn.started", "turn.done"]
    assert not actor.busy
    await actor.close()


async def test_a_frame_that_cannot_be_mapped_is_reported_and_the_stream_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    original = Mapper.frame

    def fragile(self: Mapper, frame: dict[str, Any]) -> list[Event]:
        if frame.get("boom"):
            raise ValueError("a shape the mapper does not know")
        return original(self, frame)

    monkeypatch.setattr(Mapper, "frame", fragile)
    script = [
        *(user_in(), out(init_frame()), out({**text_delta("a"), "boom": True})),
        *(out(text_delta("b")), out(result_frame())),
        *(user_in(), out(init_frame()), out({**result_frame(), "boom": True})),
        *(user_in(), out(init_frame()), out(result_frame())),
    ]
    manager, _ = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("one")
    assert (await seen.until("turn.done"))["data"]["status"] == "success"
    assert (await seen.until("error"))["data"]["code"] == "internal"
    assert "text.delta" in seen.types
    await actor.send("two")  # its result frame fails: the turn still ends, as an error
    assert (await seen.until("turn.done", count=2))["data"]["status"] == "error"
    await actor.send("three")
    assert (await seen.until("turn.done", count=3))["data"]["status"] == "success"
    await actor.close()
    # The CLI writing a shape the mapper was not written against is this app's likeliest break,
    # and a bare exception class against a 680-line mapper names nothing. The log must say which
    # rule broke, which only the traceback can.
    traces = [
        "".join(traceback.format_tb(r.exc_info[2])) if r.exc_info else ""
        for r in caplog.records
        if r.getMessage().startswith("frame ")
    ]
    assert traces and all("fragile" in trace for trace in traces)


async def test_a_control_error_after_a_turn_still_ends_it(tmp_path: Path) -> None:
    script = [
        *(user_in(), out(init_frame()), out(result_frame())),
        *(control_in("get_context_usage", "rec-usage"), control_error("rec-usage")),
        *(user_in(), out(init_frame()), out(result_frame())),
    ]
    manager, _ = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    done = (await seen.until("turn.done"))["data"]
    assert (done["status"], done["context_percent"]) == ("success", None)
    await actor.send("again")
    assert (await seen.until("turn.done", count=2))["data"]["status"] == "success"
    await actor.close()


# --- cards: withdrawn, orphaned, allowed ----------------------------------------------------------


async def test_an_allowed_tool_carries_no_permission_updates(tmp_path: Path) -> None:
    manager, spawner = setup(tmp_path, fixture("webfetch-denied"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Use WebFetch on https://example.com")
    card = (await seen.until("permission.requested"))["data"]
    assert actor.respond(card["tool_use_id"], PermissionAnswer(allow=True))
    await seen.until("turn.done")
    reply = sent_responses(spawner)[0]["response"]
    assert reply["behavior"] == "allow" and "updatedPermissions" not in reply  # never suggestions
    await actor.close()


async def test_an_answered_card_closes_in_the_stream_before_its_tool_finishes(
    tmp_path: Path,
) -> None:
    """Every view of a session learns a card is closed from the stream, not from the tool ending.

    `tool.done` carries the card's tool_use_id and removes it too, but it arrives when the TOOL
    finishes. That is the wrong clock: the user answers, the tool runs for thirty seconds, and
    until it ends a reload, a replay of the actor log or a second window all still show the card
    — with live buttons, which the server ignores, because respond() refuses a future already
    resolved. They press Allow once and nothing happens.

    So the assertion that matters is the ORDERING: request.answered must be in the stream while
    the turn is still running. The fake holds the tool open (fake.sleep) after it receives the
    permission response, which is exactly the window the bug lived in.
    """
    script = [
        *(user_in(), out(init_frame()), out(permission_request("rec-perm"))),
        permission_response_in("rec-perm"),
        out({"type": "fake.sleep", "s": 0.2}),  # the tool is running
        out(result_frame()),
    ]
    manager, _ = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Use WebFetch on https://example.com")
    card = (await seen.until("permission.requested"))["data"]
    assert actor.respond(card["tool_use_id"], PermissionAnswer(allow=True))
    assert (await seen.until("request.answered"))["data"] == {"tool_use_id": "toolu_1"}
    assert "turn.done" not in seen.types  # the point: it did not wait for the tool
    await seen.until("turn.done")
    await actor.close()


async def test_a_denied_card_closes_the_same_way(tmp_path: Path) -> None:
    """Deny resolves the card exactly as Allow once does. The event says the card is closed and
    nothing else — a second window is not told which button they pressed."""
    manager, _ = setup(tmp_path, fixture("webfetch-denied"))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("Use WebFetch on https://example.com")
    card = (await seen.until("permission.requested"))["data"]
    assert actor.respond(card["tool_use_id"], PermissionAnswer(allow=False))
    assert (await seen.until("request.answered"))["data"] == {"tool_use_id": card["tool_use_id"]}
    await seen.until("turn.done")
    await actor.close()


async def test_a_withdrawn_card_is_cancelled_and_a_late_answer_refused(tmp_path: Path) -> None:
    script = [
        *(user_in(), out(init_frame()), out(permission_request("rec-perm"))),
        out({"type": "fake.sleep", "s": 0.05}),
        out({"type": "control_cancel_request", "request_id": "rec-perm"}),
        out(result_frame()),
    ]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("permission.requested")
    assert (await seen.until("request.cancelled"))["data"] == {"tool_use_id": "toolu_1"}
    await seen.until("turn.done")
    assert not actor.respond("toolu_1", PermissionAnswer(allow=True))
    assert sent_responses(spawner) == []  # nothing answered a request claude withdrew
    assert "request.answered" not in seen.types  # withdrawn is not answered; only one may fire
    await actor.close()


async def test_a_crash_while_a_card_waits_cancels_it(tmp_path: Path) -> None:
    script = [
        *(user_in(), out(init_frame()), out(permission_request("rec-perm"))),
        out({"type": "fake.sleep", "s": 0.05}),
        out({"type": "fake.exit", "code": 1}),
    ]
    manager, _ = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("error")
    tail = [t for t in seen.types if t in ("request.cancelled", "turn.done", "error")]
    assert tail == ["request.cancelled", "turn.done", "error"]
    assert not actor.live and not actor.respond("toolu_1", PermissionAnswer(allow=True))


# --- failures, races and guards -------------------------------------------------------------------


async def test_a_second_stop_does_not_kill_the_process(tmp_path: Path) -> None:
    script = [
        *(user_in(), out(init_frame()), out(text_delta("x"))),
        *(interrupt_in(), interrupt_ack(), out(aborted())),
    ]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("text.delta")
    await asyncio.gather(actor.interrupt(), actor.interrupt())  # a double click on Stop
    assert (await seen.until("turn.done"))["data"]["status"] == "cancelled"
    assert not spawner.children[0].killed and actor.live
    await actor.close()


async def test_a_failed_initialize_is_reported_and_leaves_nothing_live(tmp_path: Path) -> None:
    script = [control_in("initialize", "rec-init"), control_error("rec-init")]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    assert not await actor.start()
    assert (await seen.until("error"))["data"]["code"] == "crashed"
    assert not actor.live and spawner.children[0].released
    await actor.send("hello")  # a fresh start: no transcript was ever written to resume
    assert f"--session-id={SID}" in spawner.children[1].argv


async def test_a_driver_that_cannot_even_be_built_is_a_card_not_silence(tmp_path: Path) -> None:
    """Construction used to sit one line above the start's try. Anything it raised — build_argv
    refusing a mode or an effort, the transport, the control glue — left send() with a bare
    exception that reached the server's fire-and-forget dispatch task: one WARNING, no
    user.message, no turn.started, no card, and a composer that had already cleared the text."""

    def explode(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> GatedDriver:
        raise ValueError(f"permission mode not allowed: {prefs.mode!r}")

    state = AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low"))
    (tmp_path / "home" / "sessions").mkdir(parents=True, exist_ok=True)
    manager = SessionManager(
        config=DriverConfig(cli=("claude.exe",), vault=Path("C:/Vault"), env={}),
        state=state,
        claude_home=tmp_path / "home",
        make_driver=explode,
    )
    actor = manager.create(SID)
    seen = watch(actor)
    assert not await actor.start()
    error = (await seen.until("error"))["data"]
    assert error == {"code": "internal", "message": MSG_START_FAILED, "recoverable": True}
    assert not actor.live  # no driver was built, so nothing may count against MAX_LIVE
    await actor.send("hello")
    assert await seen.until("error", count=2)  # the next Enter says so too, instead of nothing
    assert "user.message" not in seen.types  # the turn never began; the log must not claim it did


async def test_a_permission_mode_viki_cannot_spawn_in_is_never_stored(tmp_path: Path) -> None:
    """PERMISSION_MODES is deliberately a subset of what the CLI knows, and a CLI bump can rename
    a mode (2.1.259 already turned "default" into "manual"). Storing one from outside it poisoned
    state.json: build_argv raised on the next start, so every later send did nothing at all until
    the app was restarted and the load-side scrub cleaned it."""

    def mode_frame(mode: str) -> dict[str, Any]:
        return {"type": "system", "subtype": "status", "session_id": SID, "permissionMode": mode}

    # The known mode first, the one VIKI will not spawn with last: it is the value left in state
    # that decides whether the next process can start at all.
    script = [
        *(user_in(), out(init_frame()), out(mode_frame("plan"))),
        *(out(mode_frame("dontAsk")), out(result_frame())),
    ]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    # The chip still shows whatever the CLI reports: that comes from the event, not from state.
    assert (await seen.until("status", count=2))["data"]["mode"] == "dontAsk"
    await seen.until("turn.done")
    stored = AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low")).prefs(SID)
    assert stored.mode == "plan"  # the mode VIKI can spawn in, not the one it cannot
    actor.set_effort("max")  # the next send respawns: this is where the poisoned mode raised
    await actor.send("again")
    assert (await seen.until("turn.done", count=2))["data"]["status"] == "success"
    argv = spawner.children[1].argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    await actor.close()


async def test_a_failed_first_write_retires_the_process_and_starts_fresh(tmp_path: Path) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.start()
    first = drivers.made[0][0]
    first.send_error = CLIConnectionError("the pipe closed")
    await actor.send("hello")
    error = (await seen.until("error"))["data"]
    assert error == {"code": "provider_disconnected", "message": MSG_LOST, "recoverable": False}
    assert seen.types[-2:] == ["turn.done", "error"] and first.calls[-1] == "close"
    await actor.send("again")
    assert not drivers.made[1][1].resume  # never --resume of a transcript that was never made


@pytest.mark.parametrize("text_first", [False, True])
async def test_a_crash_during_a_failed_image_read_restores_the_launch_it_had(
    tmp_path: Path, text_first: bool
) -> None:
    """The process dies while the image is read, so the crash re-arms a resume; the read then
    fails before any provider write. A new session has no transcript to resume and starts
    fresh; after a sent text turn the transcript exists, so the next start must resume it."""

    class CrashDuringRead(GatedDriver):
        def __init__(self, emit: Callable[[Event], None]) -> None:
            super().__init__()
            self.release.set()
            self.emit = emit

        async def send_turn(self, turn: TurnInput) -> None:
            self.alive = False
            self.emit(Error(code="crashed", message=MSG_CRASHED, recoverable=True))
            raise AttachmentUnavailable

    class CrashingDrivers(Drivers):
        def __call__(
            self, launch: Launch, prefs: Prefs, emit: Callable[[Event], None]
        ) -> GatedDriver:
            self.made.append((CrashDuringRead(emit), launch, prefs, emit))
            return self.made[-1][0]

    async def image_turn() -> TurnInput:
        image = Attachment(str(uuid.uuid4()), "image/png", 4, 1, 1, "synthetic.png")
        return TurnInput("describe this", (image,))

    async def intent() -> None:
        return None

    drivers = CrashingDrivers()
    actor = gated(tmp_path, drivers).create(SID)
    if text_first:
        await actor.send("hello")
        drivers.made[0][3](TurnDone(status="success", usage=Usage()))
    result = await actor.send_command(
        expected_generation=actor.generation, accept=image_turn, dispatch_intent=intent
    )
    assert result == CommandDispatchResult("not_dispatched", "attachment_unavailable")
    assert not actor.live
    assert await actor.start()
    assert drivers.made[-1][1] == Launch(SID, resume=text_first)
    await actor.close()


async def test_an_effort_change_during_the_start_is_not_lost(tmp_path: Path) -> None:
    drivers = Drivers(gate_first=True)
    actor = gated(tmp_path, drivers).create(SID)
    start = asyncio.create_task(actor.start())  # the eager start of a new session
    await drivers.created.wait()
    first = drivers.made[0][0]
    await first.opening.wait()
    actor.set_effort("max")
    first.release.set()
    assert await start
    await actor.send("hello")
    assert [prefs.effort for _, _, prefs, _ in drivers.made] == ["low", "max"]
    assert first.calls[-1] == "close"


async def test_a_replaced_process_can_no_longer_touch_the_session(tmp_path: Path) -> None:
    drivers = Drivers()
    actor = gated(tmp_path, drivers).create(SID)
    seen = watch(actor)
    await actor.start()
    old_emit = drivers.made[0][3]
    actor.set_effort("max")
    await actor.send("hello")  # respawns: the first process is retired
    old_emit(Error(code="crashed", message="late news", recoverable=True))
    assert actor.live and "late news" not in [e["data"].get("message") for e in seen]


async def test_a_closing_process_stays_ours_until_it_exits(tmp_path: Path) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    await manager.create(SID).start()
    driver = drivers.made[0][0]
    driver.pid = 4321
    driver.close_gate.clear()
    closing = asyncio.create_task(manager.close(SID))
    await driver.closing.wait()
    assert manager.own_pids() == {4321}
    # The id stays reserved for the whole close, and the actor holding it refuses to start
    # anything: together that is what stops a second command from putting a fresh
    # `claude --resume` on the transcript the dying process is still flushing.
    held = manager.get(SID)
    assert held is not None
    sending = asyncio.create_task(held.send("while it closes"))
    driver.close_gate.set()
    await closing
    await sending
    # One process, start to finish: the send woke up behind close()'s lock and found a session
    # that had already gone, instead of putting a second `claude --resume` on the transcript the
    # first one was still flushing.
    assert len(drivers.made) == 1
    assert manager.own_pids() == set() and manager.get(SID) is None


async def test_closing_a_busy_session_stops_the_turn_first(tmp_path: Path) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    await manager.create(SID).start()
    driver = drivers.made[0][0]
    driver.busy = True
    await manager.close(SID)
    assert driver.calls[-2:] == ["interrupt", "close"]


async def test_closing_before_the_cli_picks_the_message_up_still_asks_it_to_stop(
    tmp_path: Path,
) -> None:
    """Close inside the send → system/init window used to send no interrupt at all: interrupt()
    only armed one, close() shut stdin under a turn about to run at full speed, and the grace ran
    out on the whole tree — claude, its hooks, and whatever tool it had started."""
    script = [
        user_in(),
        out({"type": "fake.sleep", "s": 0.05}),  # claude is still in its SessionStart hook
        *(out(init_frame()), interrupt_in(), interrupt_ack(), out(aborted())),
    ]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    await actor.send("commit the vault and push")
    replayer = spawner.children[0].process.replayer
    assert [f["type"] for f in replayer.seen] == ["control_request", "user"]  # not picked up yet
    await manager.close(SID)
    sent = [f["request"]["subtype"] for f in replayer.seen if f["type"] == "control_request"]
    assert "interrupt" in sent  # over a stdin still open, not into one already closed
    assert not spawner.children[0].killed  # claude ended the turn and exited by itself


async def test_a_model_is_remembered_only_once_it_applies(tmp_path: Path) -> None:
    script = [control_in("set_model", "rec-model"), control_error("rec-model")]
    manager, spawner = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)

    def remembered() -> str | None:
        return AppState(tmp_path / "state.json", Prefs()).prefs(SID).model

    await actor.set_model("sonnet")  # not live yet: the next spawn takes it
    assert remembered() == "sonnet"
    await actor.start()
    assert "--model=sonnet" in spawner.children[0].argv
    await actor.set_model("bogus")  # the CLI refuses it
    assert (await seen.until("error"))["data"]["message"] == MSG_MODEL
    assert remembered() == "sonnet"
    assert seen.types.count("session.started") == 1  # refused: no confirmation follows
    await actor.set_model("opus")  # the CLI accepts it
    assert remembered() == "opus"
    confirmed = (await seen.until("session.started", count=2))["data"]
    assert confirmed["model"] == "opus" and confirmed["mode"] == "auto"
    await actor.close()


async def test_a_switch_after_a_mode_change_resends_the_current_mode(tmp_path: Path) -> None:
    status = {"type": "system", "subtype": "status", "session_id": SID, "permissionMode": "plan"}
    script = [user_in(), out(init_frame()), out(status), out(result_frame())]
    manager, _ = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("turn.done")
    assert (await seen.until("status"))["data"]["mode"] == "plan"
    await actor.set_model("opus")
    confirmed = (await seen.until("session.started", count=2))["data"]
    assert confirmed["model"] == "opus" and confirmed["mode"] == "plan"
    await actor.close()


def test_registry_entries_need_a_matching_start_time_and_skip_our_own(tmp_path: Path) -> None:
    home, vault, me = tmp_path / "home", Path("C:/Vault"), os.getpid()
    (home / "sessions").mkdir(parents=True)
    entry = {"pid": me, "sessionId": SID, "cwd": str(vault), "entrypoint": "claude-vscode"}
    registry = home / "sessions" / f"{me}.json"
    registry.write_text(json.dumps(entry), encoding="utf-8")  # no procStart: not provably alive
    assert find_holder(home, SID, set()) is None and vault_sessions(home, vault, set()) == []
    entry["procStart"] = str(winproc.process_start_time(me))
    registry.write_text(json.dumps(entry), encoding="utf-8")
    assert find_holder(home, SID, set()) is not None
    assert find_holder(home, SID, {me}) is None and vault_sessions(home, vault, {me}) == []


def test_a_registry_entry_written_with_a_bom_is_still_read(tmp_path: Path) -> None:
    """PowerShell writes a UTF-8 BOM by default, and plain utf-8 makes json.loads raise on one.
    A dropped entry reads as "nobody else has this session open" — the one answer this registry
    must never invent."""
    home, vault, me = tmp_path / "home", Path("C:/Vault"), os.getpid()
    (home / "sessions").mkdir(parents=True)
    entry = {
        "pid": me,
        "sessionId": SID,
        "cwd": str(vault),
        "entrypoint": "claude-vscode",
        "procStart": str(winproc.process_start_time(me)),
    }
    path = home / "sessions" / f"{me}.json"
    path.write_text(json.dumps(entry), encoding="utf-8-sig")
    assert path.read_bytes()[:3] == codecs.BOM_UTF8
    assert find_holder(home, SID, set()) is not None
    assert [o.pid for o in vault_sessions(home, vault, set())] == [me]


def test_a_registry_entry_for_a_dead_pid_is_not_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing start time on either side (a dead pid, an entry without procStart) must never
    compare equal as "None" == "None"."""
    home, vault = tmp_path / "home", Path("C:/Vault")
    (home / "sessions").mkdir(parents=True)
    entry = {"pid": 4242, "sessionId": SID, "cwd": str(vault), "entrypoint": "claude-vscode"}
    (home / "sessions" / "4242.json").write_text(json.dumps(entry), encoding="utf-8")
    monkeypatch.setattr(winproc, "process_start_times", lambda pids: {})
    assert find_holder(home, SID, set()) is None and vault_sessions(home, vault, set()) == []


def test_a_registry_entry_nested_too_deep_is_skipped_the_rest_kept(tmp_path: Path) -> None:
    """json.loads raises RecursionError, not ValueError, on JSON nested this deep. `_registry`
    must read that the same way it reads any other unparseable entry: dropped, the other entries
    kept, never a crash."""
    home, vault, me = tmp_path / "home", Path("C:/Vault"), os.getpid()
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / "nested.json").write_text("[" * 200_000, encoding="utf-8")
    entry = {
        "pid": me,
        "sessionId": SID,
        "cwd": str(vault),
        "entrypoint": "claude-vscode",
        "procStart": str(winproc.process_start_time(me)),
    }
    (home / "sessions" / f"{me}.json").write_text(json.dumps(entry), encoding="utf-8")
    assert find_holder(home, SID, set()) is not None
    assert [o.pid for o in vault_sessions(home, vault, set())] == [me]


# --- pure pieces ----------------------------------------------------------------------------------


def test_check_billing() -> None:
    check_billing({"account": {"apiProvider": "firstParty", "subscriptionType": "Claude Max"}})
    for info, code in [
        ({}, "not_logged_in"),
        ({"account": {"apiProvider": "firstParty", "subscriptionType": ""}}, "billing"),
        ({"account": {"apiProvider": "vertex", "subscriptionType": "max"}}, "billing"),
    ]:
        try:
            check_billing(info)
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "code", None) == code
        else:
            raise AssertionError(f"{info} passed the gate")


def test_classify_exit() -> None:
    # Its own code, not "crashed": the crash card offers to send the message again, and a
    # transcript that is gone cannot be resumed by trying once more.
    assert classify_exit("No conversation found with session ID: x")[0] == "transcript_missing"
    assert classify_exit("Invalid API key · Please run /login")[0] == "not_logged_in"
    assert classify_exit("")[0] == "crashed"


def test_menu_commands_put_vault_skills_first_and_drop_terminal_ones() -> None:
    raw = [
        {"name": n, "description": n}
        for n in ("zeta", "clear", "retro", "alpha", "doctor", "capture")
    ]
    names = [(c.name, c.skill) for c in menu_commands(raw, frozenset({"retro", "capture"}))]
    assert names == [("capture", True), ("retro", True), ("alpha", False), ("zeta", False)]


def test_app_state_persists_preferences(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = AppState(path, Prefs(mode="auto", effort="high"))
    assert not state.ours(SID)
    state.remember(SID, effort="max")
    state.remember(SID, mode="plan")
    again = AppState(path, Prefs(mode="auto", effort="high"))
    assert again.ours(SID)
    assert again.prefs(SID) == Prefs(mode="plan", effort="max", model=None)
    assert again.prefs(FORK) == again.defaults


def test_user_defaults_follow_the_owners_settings(tmp_path: Path) -> None:
    home, vault = tmp_path / "home", tmp_path / "vault"
    (vault / ".claude").mkdir(parents=True)
    home.mkdir()

    def settings(path: Path, value: dict[str, Any]) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    settings(
        home / "settings.json",
        {"permissions": {"defaultMode": "auto"}, "effortLevel": "xhigh", "model": "opus"},
    )
    assert user_defaults(home, vault, env={}) == Prefs(mode="auto", effort="xhigh", model="opus")
    # The CLI's merge: the vault's project settings over the user's, its local ones over both.
    settings(vault / ".claude" / "settings.json", {"permissions": {"defaultMode": "default"}})
    settings(vault / ".claude" / "settings.local.json", {"effortLevel": "low"})
    assert user_defaults(home, vault, env={}) == Prefs(mode="default", effort="low", model="opus")
    settings(home / "settings.json", {"permissions": {"defaultMode": "bypassPermissions"}})
    settings(vault / ".claude" / "settings.json", {})
    settings(vault / ".claude" / "settings.local.json", {"effortLevel": ["odd"]})
    assert user_defaults(home, vault, env={}) == Prefs()  # an unsafe mode never becomes a default
    assert user_defaults(tmp_path / "missing", tmp_path / "missing", env={}) == Prefs()
    # A chat-only launch has no vault workspace: the provider home is the only source left, and
    # the process working directory is never one.
    settings(home / "settings.json", {"effortLevel": "low"})
    assert user_defaults(home, None) == Prefs(effort="low")


# --- the launch gate before any spawn ----------------------------------------------------


class _Spawnless:
    """A DriverConfig spawner that fails the test if anything ever reaches it."""

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a refused start must never spawn a process")


def _gate_manager(tmp_path: Path, *, cli: tuple[str, ...], readiness: Readiness) -> SessionManager:
    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True, exist_ok=True)
    return SessionManager(
        config=DriverConfig(cli=cli, vault=tmp_path / "vault", env={}, spawner=_Spawnless()),
        state=AppState(tmp_path / "state.json", Prefs()),
        claude_home=home,
        readiness=readiness,
    )


def _readiness(**fields: Any) -> Readiness:
    config = SimpleNamespace(
        cli=Path("claude.exe"), cli_problem=None, claude_config_dir_set=False, cli_source=""
    )
    for name, value in fields.items():
        setattr(config, name, value)
    return Readiness(config=cast(Any, config), launch_id="launch-1", git_available=lambda: False)


@pytest.mark.parametrize(
    ("cli", "fields", "message"),
    [
        ((), {"cli": None, "cli_problem": "not_found"}, MSG_CLI_MISSING),
        ((), {"cli": Path("claude.cmd"), "cli_problem": "batch_shim"}, MSG_CLI_SHIM),
        # config.load() records a shim as cli=None; the problem code decides the message.
        ((), {"cli": None, "cli_problem": "batch_shim"}, MSG_CLI_SHIM),
        ((), {"claude_config_dir_set": True}, MSG_CLI_CONFIG_DIR),
    ],
)
async def test_a_start_with_nothing_to_spawn_is_refused_before_any_process(
    tmp_path: Path, cli: tuple[str, ...], fields: dict[str, Any], message: str
) -> None:
    """The launcher builds an empty argv when nothing may be spawned, and a session start must
    say so rather than handing the SDK an empty command line. The message names
    CLAUDE_CONFIG_DIR when that is the reason, and the .cmd/.bat shim when that is, because the
    user can undo those two themselves; "not found" would send them looking for a file that is
    right there."""
    assert ".cmd" in MSG_CLI_SHIM and ".bat" in MSG_CLI_SHIM
    manager = _gate_manager(tmp_path, cli=cli, readiness=_readiness(**fields))
    actor = manager.create(SID)
    events: list[dict[str, Any]] = []
    actor.subscribe(events.append)

    assert await actor.start() is False

    assert [e["type"] for e in events] == ["error"]
    assert events[0]["data"]["code"] == "cli_missing"
    assert events[0]["data"]["message"] == message
    assert actor.live is False


async def test_a_blocked_billing_preflight_refuses_the_start_without_spawning(
    tmp_path: Path,
) -> None:
    """The preflight stops the spawn, not the launch, with the same message and code the
    driver's own billing gate uses once a process is running."""
    readiness = _readiness()
    readiness.observe_billing(["settings.json: apiKeyHelper is set"])
    manager = _gate_manager(tmp_path, cli=("claude.exe",), readiness=readiness)
    actor = manager.create(SID)
    events: list[dict[str, Any]] = []
    actor.subscribe(events.append)

    assert await actor.start() is False

    assert [e["type"] for e in events] == ["error"]
    assert events[0]["data"]["code"] == "billing"
    assert events[0]["data"]["recoverable"] is False
    assert actor.live is False


async def test_a_refused_start_and_a_successful_one_both_reach_readiness(
    tmp_path: Path,
) -> None:
    """Login state is `unknown` until a real session start observes the CLI's initialize reply,
    and there is no standalone login probe. A start that got that far is the only evidence the
    app has, and a start error is how a signed-out CLI becomes visible."""
    readiness = _readiness()
    manager = _gate_manager(tmp_path, cli=(), readiness=readiness)
    await manager.create(SID).start()
    assert _login(readiness) == "unknown"

    readiness.observe_start_error("not_logged_in")
    assert _login(readiness) == "signed_out"

    driver = GatedDriver()
    driver.release.set()
    home = tmp_path / "second" / "home"
    (home / "sessions").mkdir(parents=True)
    started = SessionManager(
        config=DriverConfig(cli=("claude.exe",), vault=tmp_path / "vault", env={}),
        state=AppState(tmp_path / "second" / "state.json", Prefs()),
        claude_home=home,
        make_driver=lambda *_: driver,
        readiness=readiness,
    )
    assert await started.create(SID).start() is True
    assert _login(readiness) == "signed_in"


async def test_each_spawn_starts_from_readiness_s_version_and_reports_a_newer_one(
    tmp_path: Path,
) -> None:
    """The version a chat's start-up report names goes back to readiness, and every later spawn
    starts from readiness's version; before anything is observed, from the launch probe's."""
    readiness = _readiness()
    manager, spawner = setup(tmp_path, [], readiness=readiness)
    reply = {
        "subtype": "success",
        "request_id": "rec-init",
        "response": {**DEFAULT_INIT, "claude_code_version": "2.1.304"},
    }
    reported = [
        control_in("initialize", "rec-init"),
        out({"type": "control_response", "response": reply}),
    ]

    async def started_version(script: list[Entry]) -> str:
        spawner.script = script
        actor = manager.create(str(uuid.uuid4()))
        try:
            assert await actor.start() and actor.started is not None
            return actor.started.cli_version
        finally:
            await actor.close()

    assert await started_version([]) == "2.1.259"  # the launch probe's
    assert await started_version(reported) == "2.1.304" and readiness.version == "2.1.304"
    assert await started_version([]) == "2.1.304"  # readiness's, though this CLI reports none


def test_a_state_file_of_the_wrong_shape_is_refused_and_preserved(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    for text in ("[]", "null", '{"sessions": []}', "{"):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(StateError):
            AppState(path, Prefs())
        assert path.read_text(encoding="utf-8") == text
        assert not (tmp_path / "app.sqlite3").exists()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": {
                    SID: {
                        "mode": "bypassPermissions",
                        "effort": "extreme",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assert AppState(path, Prefs(mode="auto")).prefs(SID) == Prefs(mode="auto")


def unavailable_state(*_args: Any, **_kwargs: Any) -> Any:
    raise StateError("unavailable")


async def test_unreadable_preferences_refuse_start_without_losing_the_next_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    actor = manager.create(SID)
    seen = watch(actor)
    with monkeypatch.context() as patch:
        patch.setattr(AppState, "launch_prefs", unavailable_state)
        await actor.send("first")
    assert seen.types == ["error"]
    assert not drivers.made and not actor.live and actor.since is None
    await actor.send("second")
    assert drivers.made[0][0].calls == ["open", "opened", "send second"]
    assert not drivers.made[0][1].resume
    assert [e["seq"] for e in seen] == [1, 2, 3, 4]
    await actor.close()


@pytest.mark.parametrize("fork", [False, True])
async def test_failed_registration_retains_the_original_launch_for_explicit_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fork: bool,
) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    state = AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low"))
    state.remember(SID, model="sonnet")
    target = FORK if fork else "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with monkeypatch.context() as patch:
        patch.setattr(AppState, "remember", unavailable_state)
        with pytest.raises(StateError):
            manager.fork(SID, target) if fork else manager.create(target)
        actor = manager.get(target)
        assert actor is not None and not actor.live and not drivers.made
        assert not state.ours(target)
        seen = watch(actor)
        await actor.send("still unavailable")
        assert seen.types == ["error"] and not drivers.made
    await actor.send("explicit retry")
    driver, launch, prefs, _ = drivers.made[0]
    assert state.ours(target)
    assert launch.session_id == target and not launch.resume
    assert launch.fork_of == (SID if fork else None)
    assert prefs.model == ("sonnet" if fork else None)
    assert driver.calls == ["open", "opened", "send explicit retry"]
    await actor.close()


async def test_failed_mode_save_does_not_end_the_provider_frame_pump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [
        user_in(),
        out(init_frame()),
        out({"type": "system", "subtype": "status", "session_id": SID, "permissionMode": "plan"}),
        out(result_frame()),
    ]
    manager, _ = setup(tmp_path, script)
    actor = manager.create(SID)
    seen = watch(actor)
    with monkeypatch.context() as patch:
        patch.setattr(AppState, "remember", unavailable_state)
        await actor.send("hello")
        await seen.until("turn.done", timeout=0.05)
    assert any(e["type"] == "status" and e["data"]["mode"] == "plan" for e in seen)
    assert "error" in seen.types and not actor.busy
    assert [e["seq"] for e in seen] == list(range(1, len(seen) + 1))
    assert AppState(tmp_path / "state.json", Prefs()).prefs(SID).mode == "auto"
    await actor.close()


async def test_applied_model_save_failure_is_visible_without_claiming_a_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    actor = manager.create(SID)
    await actor.start()
    seen = watch(actor)
    with monkeypatch.context() as patch:
        patch.setattr(AppState, "remember", unavailable_state)
        await actor.set_model("opus")
    assert drivers.made[0][0].calls[-1] == "model opus"
    assert seen.types == ["error"]
    assert "The model changed, but the setting couldn't be saved." in seen[0]["data"]["message"]
    assert AppState(tmp_path / "state.json", Prefs()).prefs(SID).model is None
    await actor.close()


async def test_failed_effort_save_does_not_schedule_a_respawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drivers = Drivers()
    manager = gated(tmp_path, drivers)
    actor = manager.create(SID)
    await actor.start()
    seen = watch(actor)
    with monkeypatch.context() as patch:
        patch.setattr(AppState, "remember", unavailable_state)
        actor.set_effort("max")
    assert seen.types == ["error"]
    await actor.send("continue")
    assert len(drivers.made) == 1
    assert AppState(tmp_path / "state.json", Prefs()).prefs(SID).effort == "low"
    await actor.close()


# --- the browser is gone --------------------------------------------------------------------------


class CardDriver(GatedDriver):
    """A GatedDriver that opens at once, whose turn the test ends by hand (`busy`), and whose
    open cards the test lists in `cards`: respond() answers one and removes it."""

    def __init__(self) -> None:
        super().__init__()
        self.release.set()
        self.busy = False
        self.cards: list[str] = []
        self.answers: list[tuple[str, Answer]] = []

    def pending(self) -> list[str]:
        return list(self.cards)

    def respond(self, tool_use_id: str, answer: Answer) -> bool:
        if tool_use_id not in self.cards:
            return False
        self.cards.remove(tool_use_id)
        self.answers.append((tool_use_id, answer))
        return True


def with_cards(
    tmp_path: Path,
) -> tuple[SessionManager, CardDriver, list[Callable[[Event], None]]]:
    """A manager over one CardDriver; the emit callbacks it was built with let a test end a
    turn the way the real driver does (busy off, then turn.done)."""
    driver = CardDriver()
    emits: list[Callable[[Event], None]] = []

    def make(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> CardDriver:
        emits.append(emit)
        return driver

    config = DriverConfig(cli=("claude.exe",), vault=Path("C:/Vault"), env={})
    state = AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low"))
    (tmp_path / "home" / "sessions").mkdir(parents=True, exist_ok=True)
    manager = SessionManager(
        config=config, state=state, claude_home=tmp_path / "home", make_driver=make
    )
    return manager, driver, emits


async def test_closing_for_a_gone_browser_denies_cards_and_lets_the_turn_end(
    tmp_path: Path,
) -> None:
    manager, driver, emits = with_cards(tmp_path)
    actor = manager.create(SID)
    seen = watch(actor)
    assert await actor.start()
    driver.busy = True
    driver.cards = ["card-1", "card-2"]

    async def settle_then_close() -> None:
        await manager.settle(1.0)
        await manager.close_all()

    closing = asyncio.create_task(settle_then_close())
    await eventually(lambda: len(driver.answers) == 2)
    assert [answer for _, answer in driver.answers] == [PermissionAnswer(allow=False)] * 2
    await asyncio.sleep(0.02)
    assert not closing.done() and "close" not in driver.calls  # the turn is still running
    driver.busy = False
    emits[0](TurnDone(status="success", usage=Usage()))
    await closing
    assert driver.calls[-1] == "close" and "interrupt" not in driver.calls
    assert (await seen.until("session.closed"))["data"]["reason"] == "browser_gone"
    assert manager.live() == []


async def test_cards_raised_during_the_grace_are_denied_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("viki_agent.session.DENY_POLL_S", 0.02)
    manager, driver, emits = with_cards(tmp_path)
    actor = manager.create(SID)
    assert await actor.start()
    driver.busy = True
    settling = asyncio.create_task(manager.settle(1.0))
    await asyncio.sleep(0.01)
    driver.cards.append("late-card")  # claude asks again after the first denial
    async with asyncio.timeout(1.0):  # denied on the next DENY_POLL_S tick (real time)
        while [i for i, _ in driver.answers] != ["late-card"]:
            await asyncio.sleep(0.005)
    driver.busy = False
    emits[0](TurnDone(status="success", usage=Usage()))
    await settling
    assert "close" not in driver.calls  # settle only settles; close_all closes


async def test_settle_stops_denying_the_moment_a_window_is_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second launch opens a window into this same process and the reconnected page replays
    the running turn. From then on there is somebody to answer, so a card raised after that
    must reach them instead of being refused a second later."""
    monkeypatch.setattr("viki_agent.session.DENY_POLL_S", 0.02)
    manager, driver, emits = with_cards(tmp_path)
    actor = manager.create(SID)
    assert await actor.start()
    driver.busy = True
    driver.cards.append("card")  # open when the browser went away: nobody is there to choose
    windows = 0
    settling = asyncio.create_task(manager.settle(1.0, until=lambda: windows == 0))
    async with asyncio.timeout(1.0):  # the card open when the browser went away is refused
        while not driver.answers:
            await asyncio.sleep(0.005)
    windows = 1  # the user relaunched; a window is back
    await settling  # returns without waiting out the rest of the grace
    driver.cards.append("after-the-window-returned")
    await asyncio.sleep(0.05)  # several DENY_POLL_S ticks, had the loop still been running
    assert [i for i, _ in driver.answers] == ["card"]
    assert driver.busy and "close" not in driver.calls  # the turn is still the user's to watch


async def test_close_all_gives_up_on_a_session_that_will_not_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("viki_agent.session.CLOSE_TIMEOUT_S", 0.05)
    manager, driver, _ = with_cards(tmp_path)
    actor = manager.create(SID)
    assert await actor.start()
    driver.close_gate.clear()  # close() never returns
    await asyncio.wait_for(manager.close_all(), 1.0)
    assert driver.closing.is_set() and manager.live() == []


async def test_a_turn_that_outlives_the_grace_is_interrupted_then_closed(tmp_path: Path) -> None:
    manager, driver, _ = with_cards(tmp_path)
    actor = manager.create(SID)
    assert await actor.start()
    driver.busy = True
    assert await actor.wait_idle(0.01) is False
    await manager.settle(0.05)
    await manager.close_all()
    assert driver.calls[-2:] == ["interrupt", "close"]
    assert manager.live() == []


async def test_close_all_without_a_grace_closes_idle_sessions_at_once(tmp_path: Path) -> None:
    manager, driver, _ = with_cards(tmp_path)
    actor = manager.create(SID)
    assert await actor.start()
    assert await actor.wait_idle(0) is True
    await manager.close_all()
    assert driver.calls[-1] == "close" and driver.answers == []


async def test_close_all_denies_open_cards_even_when_nobody_settled_first(
    tmp_path: Path,
) -> None:
    """The lifespan's own shutdown calls close_all() directly. A card still open then would be
    left for a browser that has gone, so the deny cannot live only in settle()."""
    manager, driver, _ = with_cards(tmp_path)
    actor = manager.create(SID)
    assert await actor.start()
    driver.cards = ["card-1"]
    await manager.close_all()
    assert [answer for _, answer in driver.answers] == [PermissionAnswer(allow=False)]
    assert driver.calls[-1] == "close"


# --- recorded choices ---------------------------------------------------------------------


def approval_runner(manager: SessionManager) -> ApprovalRunner:
    """The delivery seam over the same app-owned store the manager already uses."""
    service = ApprovalService(manager.app_state, Path("C:/Vault"), owner=str(uuid.uuid4()))
    return ApprovalRunner(service, manager)


def choice_for(actor: SessionActor, card: dict[str, Any], choice: Choice) -> ChoiceInput:
    return ChoiceInput(
        choice_id=str(uuid.uuid4()),
        session_id=actor.session_id,
        generation=actor.generation,
        tool_use_id=card["data"]["tool_use_id"],
        native=NativeRequest(**card["native_request"]),
        choice=choice,
    )


def question_answer(card: dict[str, Any]) -> QuestionChoice:
    """The chosen option, keyed by the question the card really asked."""
    return QuestionChoice({card["data"]["questions"][0]["question"]: "Tea"}, "")


@pytest.mark.parametrize(
    "name,event,make_choice,expect",
    [
        (
            "plan-mode-exit",
            "permission.requested",
            lambda _card: PermissionChoice(True),
            lambda reply: (
                reply["updatedPermissions"]
                == [{"type": "setMode", "mode": "auto", "destination": "session"}]
            ),
        ),
        (
            "ask-user-question",
            "question.asked",
            question_answer,
            lambda reply: (
                reply["behavior"] == "allow"
                and reply["updatedInput"]["answers"] == {"Do you prefer Tea or Coffee?": "Tea"}
            ),
        ),
        (
            "ask-user-question",
            "question.asked",
            lambda _card: QuestionChoice({}, "neither, thank you"),
            lambda reply: reply == {"behavior": "deny", "message": "neither, thank you"},
        ),
        (
            "webfetch-denied",
            "permission.requested",
            lambda _card: PermissionChoice(False),
            lambda reply: (
                reply == {"behavior": "deny", "message": "The owner denied this request."}
            ),
        ),
        (
            "webfetch-denied",
            "permission.requested",
            lambda _card: PermissionChoice(True),
            # A tool allow is bare: no updatedPermissions, and never a forwarded suggestion.
            lambda reply: reply["behavior"] == "allow" and "updatedPermissions" not in reply,
        ),
    ],
    ids=["plan-approved", "question-option", "question-free-text", "tool-denied", "tool-allowed"],
)
async def test_recorded_choices_reach_the_cli_as_the_same_three_replies(
    tmp_path: Path,
    name: str,
    event: str,
    make_choice: Callable[[dict[str, Any]], Choice],
    expect: Callable[[dict[str, Any]], bool],
) -> None:
    """A recorded choice produces the reply the socket path produced.

    The fixtures are real recorded frames, so what the CLI receives is what it received before the
    ledger existed: setMode for an approved plan, updatedInput.answers for a chosen option, a
    deny carrying the user's free text, and a bare allow with no permission update at all.
    """
    manager, spawner = setup(tmp_path, fixture(name))
    runner = approval_runner(manager)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("do the thing")
    card = await seen.until(event)
    summary, created = await runner.decide(choice_for(actor, card, make_choice(card)))
    assert created and (summary.state, summary.reason) == ("delivered_locally", "")
    await seen.until("turn.done")
    assert expect(sent_responses(spawner)[0]["response"])
    await actor.close()


async def test_process_exit_between_record_and_callback_is_not_delivered_stale_request(
    tmp_path: Path,
) -> None:
    """claude exits while the choice is being recorded: the future is already cancelled."""
    script = [
        *(user_in(), out(init_frame()), out(permission_request("rec-perm"))),
    ]
    manager, spawner = setup(tmp_path, script)
    runner = approval_runner(manager)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    card = await seen.until("permission.requested")
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ApprovalService.mark_delivering

    def blocked(self: ApprovalService, choice_id: str) -> Any:
        result = original(self, choice_id)
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return result

    ApprovalService.mark_delivering = blocked  # type: ignore[method-assign]
    try:
        attempt = asyncio.create_task(
            runner.decide(choice_for(actor, card, PermissionChoice(True)))
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        assert entered.is_set()  # mark_delivering ran; a skip here would hang the wait above
        spawner.children[-1].process.exit(1)  # claude exits while mark_delivering is running
        await seen.until("request.cancelled")  # the exited process withdrew the card
    finally:
        release.set()
        ApprovalService.mark_delivering = original  # type: ignore[method-assign]
    summary, created = await attempt
    assert created and (summary.state, summary.reason) == ("not_delivered", "stale_request")
    assert sent_responses(spawner) == []  # nothing answered a future that no longer exists


async def test_request_pending_agrees_with_respond_and_forgets_an_answered_card(
    tmp_path: Path,
) -> None:
    manager, driver, _ = with_cards(tmp_path)
    actor = manager.create(SID)
    seen = watch(actor)
    assert await actor.start()
    driver.cards = ["toolu_1"]
    actor._emit(PermissionRequested(tool_use_id="toolu_1", kind="tool", tool="Read", summary="x"))
    binding = seen[-1]["native_request"]
    assert actor.request_pending("toolu_1", binding)
    for forged in ("runtime_id", "request_id", "turn_id"):
        assert not actor.request_pending("toolu_1", {**binding, forged: str(uuid.uuid4())})
    assert not actor.request_pending("toolu_2", binding)
    assert actor.respond(
        "toolu_1", PermissionAnswer(allow=True), expected_generation=actor.generation,
        native_request=binding,
    )  # fmt: skip
    # respond() removes the card's authority immediately, and this must agree with it: a True
    # here after the answer would let a second window record a choice for a resolved future.
    assert not actor.request_pending("toolu_1", binding)
    assert actor.displayed_request("toolu_1", binding) is None


async def test_displayed_request_is_the_projection_and_a_deep_copy(tmp_path: Path) -> None:
    manager, driver, _ = with_cards(tmp_path)
    actor = manager.create(SID)
    seen = watch(actor)
    assert await actor.start()
    driver.cards = ["toolu_1"]
    actor._emit(
        PermissionRequested(tool_use_id="toolu_1", kind="tool", tool="Read", summary="read a.md")
    )
    binding = seen[-1]["native_request"]
    item = actor.displayed_request("toolu_1", binding)
    assert item is not None
    assert item["type"] == "permission.requested" and item["native_request"] == binding
    assert item["data"]["summary"] == "read a.md"
    item["data"]["summary"] = "mutated by the recorder"
    again = actor.displayed_request("toolu_1", binding)
    assert again is not None and again["data"]["summary"] == "read a.md"


async def test_a_retired_runtime_has_no_pending_request_and_no_displayed_card(
    tmp_path: Path,
) -> None:
    manager, driver, _ = with_cards(tmp_path)
    actor = manager.create(SID)
    seen = watch(actor)
    assert await actor.start()
    driver.cards = ["toolu_1"]
    actor._emit(PermissionRequested(tool_use_id="toolu_1", kind="tool", tool="Read", summary="x"))
    binding = seen[-1]["native_request"]
    await actor.close()
    assert not actor.request_pending("toolu_1", binding)
    assert actor.displayed_request("toolu_1", binding) is None


def test_manager_forms_with_a_known_origin_read_no_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that already read the origin off the event loop passes it in; None reads it."""
    manager, _spawner = setup(tmp_path, [])
    store = manager.app_state.store
    other = "55555555-5555-4555-8555-555555555555"
    reads: list[str] = []
    real = store.session_origin

    def spy(session_id: str) -> str | None:
        reads.append(session_id)
        return real(session_id)

    monkeypatch.setattr(store, "session_origin", spy)
    assert manager.attach_prepared(SID, profile=True) is manager.get(SID)
    with pytest.raises(StateError) as not_prepared:
        manager.attach_prepared(other, profile=False)
    assert not_prepared.value.code == "corrupt"
    assert manager.fork(SID, FORK, source_profile=False) is manager.get(FORK)
    with pytest.raises(CommandDispatchRejected) as locked:
        manager.fork(SID, other, source_profile=True)
    assert locked.value.code == "profile_locked" and manager.get(other) is None
    assert reads == []
    with pytest.raises(StateError):
        manager.attach_prepared(other)
    manager.fork(FORK, other)
    assert reads == [other, FORK]


# --- the app MCP config resolver ------------------------------------------------------------------


def mcp_manager(
    tmp_path: Path, script: list[Entry], resolver: Callable[[str], Any]
) -> tuple[SessionManager, FakeSpawner]:
    """A manager whose profile guard passes, bound to `resolver` for app MCP configs."""
    manager, spawner = setup(tmp_path, script)

    async def guard(session_id: str, prefs: Prefs) -> None:
        return None

    async def observed(started: SessionStarted) -> None:
        return None

    async def initialized(session_id: str, facts: StartFacts | None) -> None:
        return None

    manager.bind_profiles(guard, observed, initialized=initialized)
    manager.bind_mcp(resolver)
    return manager, spawner


async def test_a_profile_session_resolves_its_mcp_config_for_every_spawn(tmp_path: Path) -> None:
    path = str((tmp_path / "mcp" / f"{'a' * 64}.json").absolute())
    calls: list[str] = []

    async def resolver(session_id: str) -> str | None:
        calls.append(session_id)
        return path

    script = [
        user_in(),
        out(init_frame()),
        out({"type": "fake.stderr", "text": "segfault\n"}),
        out({"type": "fake.exit", "code": 3}),
    ]
    manager, spawner = mcp_manager(tmp_path, script, resolver)
    actor = manager.attach_prepared(SID, profile=True)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("error")
    assert calls == [SID] and spawner.children[0].argv[-1] == f"--mcp-config={path}"
    # The process is gone; the next send resolves again rather than reusing the path.
    await actor.send("again")
    await seen.until("error", count=2)
    assert calls == [SID, SID] and len(spawner.children) == 2
    second = spawner.children[1].argv
    assert f"--resume={SID}" in second and second[-1] == f"--mcp-config={path}"
    assert [a for a in second if a.startswith("--mcp-config")] == [f"--mcp-config={path}"]


async def test_a_resolved_mcp_config_is_never_kept_on_the_session_launch(tmp_path: Path) -> None:
    # A spawn refused before any send keeps the session's launch for the retry; the path it was
    # given must not ride along into a spawn whose resolver answered no config.
    path = str((tmp_path / "mcp" / f"{'b' * 64}.json").absolute())
    answers: list[str | None] = [path, None]
    launches: list[Launch] = []

    async def resolver(session_id: str) -> str | None:
        return answers.pop(0)

    def factory(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> Any:
        launches.append(launch)
        raise StartError("internal", "synthetic refusal before any process")

    manager, spawner = mcp_manager(tmp_path, [], resolver)
    manager._make_driver = factory
    actor = manager.attach_prepared(SID, profile=True)
    assert not await actor.send("hello")
    assert not await actor.send("again")
    assert [launch.mcp_config for launch in launches] == [path, None]
    assert spawner.children == []


async def test_a_refused_mcp_config_spawns_nothing(tmp_path: Path) -> None:
    message = "Profile launch refused (mcp_revoked). Open the profile in Agents and continue."

    async def resolver(session_id: str) -> str | None:
        raise StartError("bad_request", message)

    manager, spawner = mcp_manager(tmp_path, [], resolver)
    actor = manager.attach_prepared(SID, profile=True)
    seen = watch(actor)
    assert not await actor.send("hello")
    error = (await seen.until("error"))["data"]
    assert error["code"] == "bad_request" and error["message"] == message
    assert spawner.children == [] and not actor.live


async def test_an_ordinary_session_never_resolves_an_mcp_config(tmp_path: Path) -> None:
    calls: list[str] = []

    async def resolver(session_id: str) -> str | None:
        calls.append(session_id)
        return str(tmp_path.absolute() / "unused.json")

    script = [user_in(), out(init_frame()), out(result_frame())]
    manager, spawner = mcp_manager(tmp_path, script, resolver)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("turn.done")
    assert calls == []
    assert not any(a.startswith("--mcp-config") for a in spawner.children[0].argv)
    await actor.close()


# --- a profile start's own facts ------------------------------------------------------------------

FACTS = StartFacts(version="2.1.304", mode="plan", models=("default",), mcp_servers=("viki_echo",))


class FactsDriver(GatedDriver):
    """A GatedDriver that reports start facts; `during` runs inside the read, as the CLI's own
    output can."""

    def __init__(self, during: Callable[[], None] = lambda: None) -> None:
        super().__init__()
        self.during = during

    async def start_facts(self) -> StartFacts:
        self.calls.append("facts")
        self.during()
        return FACTS


def hooked_profile(
    manager: SessionManager, hook: Callable[[str, StartFacts | None], None]
) -> SessionActor:
    """A prepared profile session whose guard passes and whose initialized hook is `hook`."""

    async def allow(*_args: object) -> None:
        return None

    async def initialized(session_id: str, facts: StartFacts | None) -> None:
        hook(session_id, facts)

    manager.bind_profiles(allow, allow, initialized=initialized)
    return manager.attach_prepared(SID, profile=True)


@pytest.mark.parametrize("reports", [True, False])
async def test_a_profile_start_hands_its_start_facts_to_the_hook_before_any_send(
    tmp_path: Path, reports: bool
) -> None:
    driver = FactsDriver() if reports else GatedDriver()
    driver.release.set()
    seen = Seen()
    hooked: list[tuple[str, StartFacts | None, list[str], list[str]]] = []
    actor = hooked_profile(
        gated(tmp_path, driver),
        lambda session_id, facts: hooked.append((session_id, facts, [*driver.calls], seen.types)),
    )
    actor.subscribe(seen)
    assert await actor.send("hello")
    opened = ["open", "opened", "facts"] if reports else ["open", "opened"]
    # When the hook ran, nothing had been announced or sent.
    assert hooked == [(SID, FACTS if reports else None, opened, [])]
    assert driver.calls == [*opened, "send hello"]
    assert seen.types[0] == "session.started"
    await actor.close()


async def test_a_hook_that_refuses_the_facts_closes_the_driver_and_sends_nothing(
    tmp_path: Path,
) -> None:
    driver = FactsDriver()
    driver.release.set()

    def refuse(_session_id: str, _facts: StartFacts | None) -> None:
        raise StartError("bad_request", "Profile launch refused (plan_mode_unconfirmed).")

    actor = hooked_profile(gated(tmp_path, driver), refuse)
    seen = watch(actor)
    assert not await actor.send("hello")
    assert driver.calls == ["open", "opened", "facts", "close"]
    assert seen.types == ["error"] and not actor.live


@pytest.mark.parametrize("terminal_event", [True, False])
async def test_a_runtime_lost_during_the_facts_read_is_closed_and_never_hooked(
    tmp_path: Path, terminal_event: bool
) -> None:
    """The process ends while its status is read: with its terminal error, which invalidates the
    runtime, or before reporting any, so only `alive` says so. Either way the start closes that
    driver, calls no hook, never announces session.started and sends nothing."""
    emits: list[Callable[[Event], None]] = []

    def lose() -> None:
        driver.alive = False
        if terminal_event:
            emits[0](Error(code="crashed", message=MSG_CRASHED, recoverable=True))

    driver = FactsDriver(lose)
    driver.release.set()

    def make(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> GatedDriver:
        emits.append(emit)
        return driver

    manager = gated(tmp_path, driver)
    manager._make_driver = make
    hooked: list[StartFacts | None] = []
    actor = hooked_profile(manager, lambda _session_id, facts: hooked.append(facts))
    seen = watch(actor)
    assert not await actor.send("hello")
    assert hooked == [] and driver.calls == ["open", "opened", "facts", "close"]
    assert not actor.live and actor.started is None
    assert seen.types == (["error"] if terminal_event else [])
