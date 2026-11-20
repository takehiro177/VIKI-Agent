"""Synthetic Bot ownership, the rules policy, and bounded result delivery."""

from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from tests.automation.test_routines import _document_raw
from tests.messaging.test_bots import _attempt, _bot, _id, _routine
from tests.messaging.test_bots import service as service_fixture
from tests.messaging.test_mattermost_bot import Rig, take
from viki_agent.automation.routines import stamp
from viki_agent.automation.tasks import Attempt, Outcome, TaskStore
from viki_agent.decision_loop.store import DecisionLoopStore
from viki_agent.events import TextDone, ThinkingDone, TurnDone, TurnStarted, Usage
from viki_agent.messaging import bot_runtime
from viki_agent.messaging.bots import BotError, BotService
from viki_agent.messaging.mattermost_bot import MattermostBot, NoticeResult
from viki_agent.messaging.mattermost_format import chat_text
from viki_agent.session import SessionActor

service = service_fixture
NOW = datetime(2026, 10, 14, tzinfo=UTC)


class Bridge:
    def __init__(self) -> None:
        self.key: str | None = "a" * 64
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.sent: list[str] = []
        self.result: NoticeResult = "sent"

    def destination_key(self) -> str | None:
        return self.key

    async def send_notice(
        self,
        text: str,
        *,
        destination_key: str,
        current: Callable[[], Awaitable[bool]],
    ) -> NoticeResult:
        self.entered.set()
        await self.release.wait()
        if destination_key != self.key or not await current():
            return "unavailable"
        self.sent.append(text)
        return self.result


@dataclass
class Actor:
    session_id: str
    generation: str
    seq: int = 0


class Preview(dict[str, Any]):
    pass


class Harness:
    def __init__(self, service: BotService) -> None:
        self.service = service
        self.bot = _bot(service)
        self.routine = _routine(service, self.bot)
        self.bridge = Bridge()
        self.workspace = self.bot["workspace_id"]
        self.attended = True
        self.now = NOW
        self.runtime = bot_runtime.BotRuntime(
            service,
            DecisionLoopStore(service.store),
            bridge=lambda: cast(MattermostBot, self.bridge),
            bridge_workspace=lambda: self.workspace,
            attended=lambda: self.attended,
            now=lambda: self.now,
        )

    async def enable(self) -> None:
        row = self.service.record(self.bot["bot_id"])
        self.bot = await self.runtime.set_delivery(self.bot["bot_id"], row["state_revision"], True)

    async def begin(self, *, done: bool = False) -> Attempt:
        attempt = _attempt(self.service, self.routine, done=done)
        document = self.service.routines.frozen(self.routine, attempt.revision)
        await self.runtime.begin_attempt(attempt, document)
        return attempt

    def observe(
        self, attempt: Attempt, **changes: Any
    ) -> tuple[Actor, Preview, Callable[[dict[str, Any]], None] | None]:
        actor = Actor(attempt.session_id, _id())
        preview = Preview(
            profile_id=self.bot["profile_id"],
            workspace_id=self.bot["workspace_id"],
            session_id=attempt.session_id,
            generation=actor.generation,
            profile_revision=1,
            identity={"name": "Pinned helper", "profile_revision": 1},
            draft={"text": "Sensitive instructions must not be retained."},
        )
        preview.update(changes)
        return (
            actor,
            preview,
            self.runtime.observe_attempt(attempt, cast(SessionActor, actor), preview),
        )

    @staticmethod
    def event(actor: Actor, observer: Callable[[dict[str, Any]], None], event: Any) -> None:
        actor.seq += 1
        observer(event.envelope(actor.session_id, actor.seq, actor.generation))

    def text(
        self,
        actor: Actor,
        observer: Callable[[dict[str, Any]], None],
        text: str = "Useful final result",
    ) -> None:
        self.event(actor, observer, TurnStarted(turn_id=_id()))
        self.event(actor, observer, TextDone(block_id="final", text=text))
        self.event(actor, observer, TurnDone(status="success", usage=Usage()))

    def commit(self, attempt: Attempt, outcome: Outcome = "succeeded") -> None:
        assert TaskStore(self.service.store, "a" * 64, _id()).finish(
            attempt.attempt_id, outcome, "", stamp(self.now)
        )

    def preference(self, name: str, value: str, *, scope: str | None = None) -> None:
        with self.service.store._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO decision_preferences VALUES (?,?,?,1,?) ON CONFLICT(scope,name) "
                "DO UPDATE SET value=excluded.value,revision=revision+1,"
                "updated_at=excluded.updated_at",
                (scope or self.bot["workspace_id"], name, value, stamp(self.now)),
            )


@pytest.fixture
def harness(service: BotService) -> Harness:
    return Harness(service)


async def test_launch_only_consent_requires_destination_and_cas(harness: Harness) -> None:
    runtime, bot = harness.runtime, harness.bot
    assert runtime.delivery_status(bot) == {"enabled": False, "available": True, "reason": "off"}
    await harness.enable()
    assert runtime.delivery_status(harness.bot) == {
        "enabled": True,
        "available": True,
        "reason": "ready",
    }
    with pytest.raises(BotError, match="conflict"):
        await runtime.set_delivery(bot["bot_id"], 1, True)
    assert not runtime.delivery_status(harness.service.get(bot["bot_id"]))["enabled"]
    restarted = bot_runtime.BotRuntime(
        harness.service,
        DecisionLoopStore(harness.service.store),
        bridge=lambda: cast(MattermostBot, harness.bridge),
        bridge_workspace=lambda: harness.workspace,
        attended=lambda: True,
        now=lambda: NOW,
    )
    assert not restarted.delivery_status(harness.service.get(bot["bot_id"]))["enabled"]
    with harness.service.store._connection() as connection:
        assert len(connection.execute("PRAGMA table_info(bots)").fetchall()) == 8


async def test_absent_bridge_reports_not_connected_instead_of_workspace_mismatch(
    harness: Harness,
) -> None:
    harness.runtime.bridge = lambda: None
    harness.runtime.bridge_workspace = lambda: None
    assert harness.runtime.delivery_status(harness.bot) == {
        "enabled": False,
        "available": False,
        "reason": "not_connected",
    }
    with pytest.raises(BotError, match="delivery_unavailable"):
        await harness.enable()


@pytest.mark.parametrize("failure", ["disconnected", "workspace", "closing", "window", "archived"])
async def test_enabling_checks_current_admission(harness: Harness, failure: str) -> None:
    if failure == "disconnected":
        harness.bridge.key = None
    elif failure == "workspace":
        harness.workspace = _id()
    elif failure == "closing":
        harness.runtime.close()
    elif failure == "window":
        harness.attended = False
    else:
        harness.service.profiles.archive(harness.bot["profile_id"], _id(), 1, True)
    with pytest.raises(BotError):
        await harness.enable()
    assert not harness.runtime.delivery_status(harness.service.get(harness.bot["bot_id"]))[
        "enabled"
    ]


async def test_success_sends_final_text_with_pinned_identity_and_metadata_only(
    harness: Harness,
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.event(
        actor, observer, ThinkingDone(block_id="thinking", text="Private thought", duration_ms=1)
    )
    harness.text(actor, observer, "A useful final result @all")
    harness.commit(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    [message] = harness.bridge.sent
    assert "Pinned helper" in message and "A useful final result @all" in message
    assert "Private thought" not in message and "Sensitive instructions" not in message
    receipt = harness.service.delivery(attempt.attempt_id)
    assert receipt is not None and receipt["state"] == "sent"
    assert receipt["attention"] == "notify" and receipt["reason"] == "runs_to_review"
    assert not harness.runtime._contexts
    assert b"A useful final result" not in harness.service.store.path.read_bytes()
    with harness.service.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM decision_episodes").fetchone() == (0,)


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "blocked", "expired"])
async def test_relay_message_puts_the_run_line_after_the_body(
    harness: Harness, outcome: Outcome
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer, "A useful final result")
    harness.commit(attempt, outcome)
    await harness.runtime.attempt_finished(attempt, outcome, "")
    [message] = harness.bridge.sent
    assert message.endswith(f"\n\nRun: {attempt.session_id}")
    assert "Run:" not in message.split("\n\n", 1)[0]


async def test_success_notice_uses_same_chat_wire_style_as_conversation(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path / "bridge")
    await rig.start(monkeypatch)
    harness.runtime.bridge = lambda: rig.bot
    try:
        await harness.enable()
        attempt = await harness.begin()
        actor, _, observer = harness.observe(attempt)
        assert observer is not None
        harness.text(actor, observer, "A **bold** result with @all and example.test")
        harness.commit(attempt)
        await harness.runtime.attempt_finished(attempt, "succeeded", "")
        message, root = await take(rig.http.sent)
        assert not root and not message.startswith("```")
        assert "**bold**" in message and "`example.test`" in message
        assert "@all" not in message
        assert len(message.encode("utf-8")) <= 8192
    finally:
        await rig.bot.close()
        if rig.run is not None:
            await rig.run
        await rig.approvals.close()
        await rig.manager.close_all()


async def test_uncommitted_outcome_cannot_send(harness: Harness) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer)
    with pytest.raises(BotError, match="conflict"):
        await harness.runtime.attempt_finished(attempt, "succeeded", "")
    assert not harness.bridge.sent and not harness.runtime._contexts


async def test_frozen_owner_survives_a_current_routine_move_and_cannot_replay(
    harness: Harness,
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer)
    other = _bot(harness.service)
    harness.service.routines.revise(
        harness.routine,
        _id(),
        1,
        _document_raw(profile_id=other["profile_id"], workspace_id=other["workspace_id"]),
    )
    harness.commit(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    receipt = harness.service.delivery(attempt.attempt_id)
    assert receipt is not None and receipt["bot_id"] == harness.bot["bot_id"]
    assert harness.service.get(other["bot_id"])["latest_delivery"] is None
    await harness.runtime.begin_attempt(
        attempt, harness.service.routines.frozen(harness.routine, 1)
    )
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer, "Replay must not send")
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    assert len(harness.bridge.sent) == 1


async def test_off_run_never_collects_even_if_enabled_later(harness: Harness) -> None:
    attempt = await harness.begin()
    _, _, observer = harness.observe(attempt)
    assert observer is None
    await harness.enable()
    harness.commit(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    receipt = harness.service.delivery(attempt.attempt_id)
    assert receipt is not None and receipt["state"] == "off"
    assert not harness.bridge.sent


@pytest.mark.parametrize("preference,expected", [("mute", "silent"), ("later", "defer")])
@pytest.mark.parametrize("queued", [False, True])
async def test_finish_and_actual_send_use_latest_exact_workspace_policy(
    harness: Harness, preference: str, expected: str, queued: bool
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer)
    harness.commit(attempt)
    task: asyncio.Task[None] | None = None
    if queued:
        harness.bridge.release.clear()
        task = asyncio.create_task(harness.runtime.attempt_finished(attempt, "succeeded", ""))
        async with asyncio.timeout(5):
            await harness.bridge.entered.wait()
    harness.preference(
        "mute:agent_runs" if preference == "mute" else "later:agent_runs",
        "1" if preference == "mute" else stamp(NOW + timedelta(hours=1)),
    )
    if queued:
        assert task is not None
        harness.bridge.release.set()
        await task
    else:
        await harness.runtime.attempt_finished(attempt, "succeeded", "")
    receipt = harness.service.delivery(attempt.attempt_id)
    assert receipt is not None and receipt["state"] == "held"
    assert receipt["attention"] == expected
    assert receipt["reason"] == ("muted_by_owner" if preference == "mute" else "later_by_owner")
    assert not harness.bridge.sent
    harness.now = NOW + timedelta(hours=5)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    assert not harness.bridge.sent


@pytest.mark.parametrize("card_type", ["card_needs_you", "card_stale"])
@pytest.mark.parametrize("preference", ["mute", "later"])
async def test_a_card_preference_does_not_hold_the_bot_notice(
    harness: Harness, card_type: str, preference: str
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer)
    harness.commit(attempt)
    harness.preference(
        f"{preference}:{card_type}",
        "1" if preference == "mute" else stamp(NOW + timedelta(hours=1)),
    )
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    receipt = harness.service.delivery(attempt.attempt_id)
    assert receipt is not None and receipt["state"] != "held"
    assert len(harness.bridge.sent) == 1


@pytest.mark.parametrize(
    "changed", ["off_on", "revision", "remove", "destination", "window", "close"]
)
async def test_queued_output_rechecks_frozen_owner_and_consent(
    harness: Harness, changed: str
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer)
    harness.commit(attempt)
    harness.bridge.release.clear()
    task = asyncio.create_task(harness.runtime.attempt_finished(attempt, "succeeded", ""))
    async with asyncio.timeout(5):
        await harness.bridge.entered.wait()
    if changed == "off_on":
        harness.runtime.revoke(harness.bot["bot_id"])
        await harness.enable()
    elif changed == "revision":
        harness.service.new_chat(harness.bot["bot_id"], harness.bot["state_revision"])
    elif changed == "remove":
        harness.runtime.revoke(harness.bot["bot_id"])
        harness.service.remove(harness.bot["bot_id"], harness.bot["state_revision"])
    elif changed == "destination":
        harness.bridge.key = "b" * 64
    elif changed == "window":
        harness.attended = False
    else:
        harness.runtime.close()
    harness.bridge.release.set()
    await task
    assert not harness.bridge.sent and not harness.runtime._contexts
    receipt = harness.service.delivery(attempt.attempt_id)
    if changed == "remove":
        assert receipt is None
    else:
        assert receipt is not None and receipt["state"] in {"off", "unavailable"}
        assert receipt["delivery_reason"] != "not_connected" or changed == "destination"


@pytest.mark.parametrize("outcome", ["failed", "blocked", "expired", "cancelled", "unknown"])
async def test_unsuccessful_attempt_uses_fixed_status_without_partial_text(
    harness: Harness, outcome: Outcome
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer, "Partial sensitive response")
    harness.commit(attempt, outcome)
    await harness.runtime.attempt_finished(attempt, outcome, "internal")
    assert all("Partial sensitive response" not in text for text in harness.bridge.sent)
    assert bool(harness.bridge.sent) == (outcome in {"failed", "blocked", "expired"})


@pytest.mark.parametrize("result", ["failed", "unknown", "unavailable"])
async def test_delivery_result_does_not_change_success_and_is_not_retried(
    harness: Harness, result: NoticeResult
) -> None:
    await harness.enable()
    harness.bridge.result = result
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer)
    harness.commit(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    receipt = harness.service.delivery(attempt.attempt_id)
    assert receipt is not None and receipt["state"] == result
    assert len(harness.bridge.sent) == 1
    with harness.service.store._connection() as connection:
        assert connection.execute("SELECT outcome FROM task_attempts").fetchone() == ("succeeded",)


@pytest.mark.parametrize("field", ["session_id", "generation", "profile_id", "workspace_id"])
async def test_prepared_preview_must_match_actual_bound_actor(harness: Harness, field: str) -> None:
    await harness.enable()
    attempt = await harness.begin()
    _, _, observer = harness.observe(attempt, **{field: _id()})
    assert observer is None
    harness.commit(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    assert not harness.bridge.sent


async def test_observer_retains_no_actor_preview_or_history(harness: Harness) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, preview, observer = harness.observe(attempt)
    assert observer is not None
    actor_ref, preview_ref = weakref.ref(actor), weakref.ref(preview)
    del actor, preview
    gc.collect()
    assert actor_ref() is None and preview_ref() is None
    harness.runtime.discard_attempt(attempt)
    harness.runtime.discard_attempt(attempt)
    assert not harness.runtime._contexts and not harness.bridge.sent


@pytest.mark.parametrize("cap", ["distinct", "events", "block_id", "unicode", "url", "fence"])
async def test_collector_caps_actual_escaped_output_and_empty_duplicate_events(
    harness: Harness, cap: str
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.event(actor, observer, TurnStarted(turn_id=_id()))
    if cap == "distinct":
        for index in range(65):
            harness.event(actor, observer, TextDone(block_id=str(index), text=""))
    elif cap == "events":
        for _ in range(257):
            harness.event(actor, observer, TextDone(block_id="same", text=""))
    elif cap == "block_id":
        harness.event(actor, observer, TextDone(block_id="\U0001f642" * 33, text=""))
    elif cap == "url":
        harness.event(
            actor,
            observer,
            TextDone(block_id="large", text="https://example.test/" + "a" * 100_000),
        )
    elif cap == "fence":
        harness.event(
            actor,
            observer,
            TextDone(block_id="large", text="```python\n" + "a" * 100_000 + "\n```"),
        )
    else:
        harness.event(actor, observer, TextDone(block_id="large", text="@\U0001f642" * 100_000))
    context = harness.runtime._contexts[attempt.attempt_id]
    assert not context.collecting and len(context.block_ids) <= 64
    harness.event(actor, observer, TextDone(block_id="late", text="Must not be collected"))
    harness.event(actor, observer, TurnDone(status="success", usage=Usage()))
    harness.commit(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    [message] = harness.bridge.sent
    assert "shortened" in message and "Must not be collected" not in message
    assert len(chat_text(message).encode("utf-8")) <= 8192


async def test_full_buffer_plus_next_block_separator_stays_within_actual_cap(
    harness: Harness,
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(
        attempt, identity={"name": "@" * 128, "profile_revision": 1}
    )
    assert observer is not None
    context = harness.runtime._contexts[attempt.attempt_id]
    harness.event(actor, observer, TurnStarted(turn_id=_id()))
    harness.event(actor, observer, TextDone(block_id="full", text="x" * context.budget))
    harness.event(actor, observer, TextDone(block_id="overflow", text="Private overflow"))
    harness.commit(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    [message] = harness.bridge.sent
    assert "Private overflow" not in message and "shortened" in message
    assert len(chat_text(message).encode("utf-8")) == 8192


async def test_cancellation_during_queued_delivery_discards_and_never_replays(
    harness: Harness,
) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    harness.text(actor, observer)
    harness.commit(attempt)
    harness.bridge.release.clear()
    task = asyncio.create_task(harness.runtime.attempt_finished(attempt, "succeeded", ""))
    async with asyncio.timeout(5):
        await harness.bridge.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    harness.bridge.release.set()
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    assert not harness.runtime._contexts and not harness.bridge.sent
    receipt = harness.service.delivery(attempt.attempt_id)
    assert receipt is not None and receipt["state"] == "unknown"


async def test_history_foreign_generation_and_second_turn_are_ignored(harness: Harness) -> None:
    await harness.enable()
    attempt = await harness.begin()
    actor, _, observer = harness.observe(attempt)
    assert observer is not None
    observer(
        TextDone(block_id="history", text="Private history").envelope(
            actor.session_id, 0, actor.generation
        )
    )
    observer(TurnStarted(turn_id=_id()).envelope(_id(), 1, actor.generation))
    observer(TurnStarted(turn_id=_id()).envelope(actor.session_id, 1, _id()))
    harness.event(actor, observer, TextDone(block_id="before", text="Before this attempt"))
    harness.event(actor, observer, TurnStarted(turn_id=_id()))
    harness.event(actor, observer, TextDone(block_id="final", text="Correct result"))
    harness.event(actor, observer, TextDone(block_id="final", text="Duplicate result"))
    harness.event(actor, observer, TurnStarted(turn_id=_id()))
    harness.event(actor, observer, TextDone(block_id="another", text="Another turn"))
    harness.commit(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    [message] = harness.bridge.sent
    assert "Correct result" in message
    assert all(
        text not in message
        for text in ("Private history", "Before this attempt", "Duplicate result", "Another turn")
    )


async def test_actual_shared_writer_context_peak_includes_receipt_waits(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path / "bridge")
    await rig.start(monkeypatch)
    harness.runtime.bridge = lambda: rig.bot
    await harness.enable()
    rig.http.gate.clear()
    tasks: list[asyncio.Task[None]] = []
    attempts: list[Attempt] = []
    admitted: asyncio.Queue[None] = asyncio.Queue()
    enqueue = rig.bot._enqueue

    def observe_enqueue(*args: Any, **kwargs: Any) -> bool:
        result = enqueue(*args, **kwargs)
        if kwargs.get("notice") is not None:
            admitted.put_nowait(None)
        return result

    monkeypatch.setattr(rig.bot, "_enqueue", observe_enqueue)
    try:
        for index in range(bot_runtime.MAX_CONTEXTS):
            attempt = await harness.begin(done=True)
            attempts.append(attempt)
            actor, _, observer = harness.observe(attempt)
            assert observer is not None
            harness.text(actor, observer)
            if index < 31:
                tasks.append(
                    asyncio.create_task(harness.runtime.attempt_finished(attempt, "succeeded", ""))
                )
                await take(admitted)
                if index == 0:
                    await take(rig.http.sent)
            assert len(harness.runtime._contexts) <= bot_runtime.MAX_CONTEXTS
        assert len(harness.runtime._contexts) == 42
        overflow = await harness.begin(done=True)
        assert overflow.attempt_id not in harness.runtime._contexts
        assert (
            harness.runtime.observe_attempt(
                overflow, cast(SessionActor, Actor(overflow.session_id, _id())), {}
            )
            is None
        )
        assert len(harness.runtime._contexts) == 42
        for attempt in attempts[31:]:
            harness.runtime.discard_attempt(attempt)
        assert len(harness.runtime._contexts) == 31
        rig.bot.disarm()
        await asyncio.gather(*tasks)
        assert not harness.runtime._contexts
    finally:
        harness.runtime.close()
        rig.http.gate.set()
        await rig.bot.close()
        if rig.run is not None:
            await rig.run
        await asyncio.gather(*tasks, return_exceptions=True)
        await rig.approvals.close()
        await rig.manager.close_all()


async def test_missing_begin_and_discard_cannot_send(harness: Harness) -> None:
    await harness.enable()
    attempt = _attempt(harness.service, harness.routine)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    assert harness.service.delivery(attempt.attempt_id) is None
    await harness.runtime.begin_attempt(
        attempt, harness.service.routines.frozen(harness.routine, 1)
    )
    harness.runtime.discard_attempt(attempt)
    await harness.runtime.attempt_finished(attempt, "succeeded", "")
    assert not harness.bridge.sent and not harness.runtime._contexts
