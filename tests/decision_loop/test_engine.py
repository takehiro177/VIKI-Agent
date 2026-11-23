"""The Decision loop's engine over a real store and the real cycle, on a fake clock, a fake model
and a fake session host: the clock's wakes, beat (a failed or cancelled one too) and sleep gap,
the request limits, the daily cap, the failures, the rejected key's pause, cooling, running off
and without a key, missions and their reports, each item's showings of the last day, the hourly
nudge cap across workspaces, the bounded recheck, a clean stop, the key test and work run between
cycles; learned numbers in cycles, pacing by attention, the beat's relearn under the cycle lock
(a failed one, and a Reset during the beat, too), and the shadow's requests under the same
limits; the scope's question wording and an open proposal's trial (its requests under the same
limits too, and a failed read of the proposals), the beat's self-improvement candidates (a new
retro after Reset learning, with learning off too, quoting the wording in use, none to tune while
a proposal is tried, and kept through a failed read of the proposals), and no mission for them;
vault changes: none asked about at the day's limit, under a held key or on an open target, the
shown ones filed once and set done under the cycle lock, never-send pairs, a failed read of the
change rows, the notes changed lately in the `owner` part, and the user's bounds left alone; a
skill suggestion: nothing about the skill or the chat sent in any state, and one item a chat;
Kanban cards: the Kanban read only while Kanban is on, an unread card's item kept and a gone
card's closed, and listed cards keeping their places; seats: a card of the user's starting its
seat in one cycle through the seat host alone, never as a mission, a waiting run's item raised at
the wake's cycle, the plan brake holding and reporting seat starts, and the Kanban read for seats
while Kanban is off; the Look: the vault Look read only while Vault health is on, each app
Look part read only while its switch is on, a cleared Look closing its item, the chats of
missions and waiting runs left out, and a halt during the app Look read; code looks: the code
folders read only under the Look grant in a vault whose policy allows prepare, Allowed starting
a look at that check, Ask first offering Take a look, quiet hours, the brake and the limit
holding a look until they end, and a taken, cleared or changed look closing; second looks: an
offer only in a usable vault under a grant that is not Off for the user's own chat, Allowed
starting one look for the chat turn in view, Ask first offering Take a second look, quiet hours,
the brake and the limit holding a second look until they end, a busy hold offering Take a second
look while the user's own start runs, today's limit showing no start under either grant, a
finding listed while its plan waits, an offer kept until its chat gets a new reply and closed
once a mission holds its key, a look that found nothing listing one quiet report, and learning's
pauses dropping the offer or reaching the brief; project plans: Allowed
starting a plan for a stalled project and a check of overdue tasks, one plan per key in any
workspace, Ask first offering Plan next steps or Check these tasks whatever the other grants,
quiet hours, the brake and the limit holding a plan until they end, a finding listed in its own
workspace while its plan waits (grant Off too), an offer closing once its fact clears or a
mission holds its key, and learning's pause dropping a paused type's offer; message drafts: listed
only in a usable vault while Prepare drafts is not Off for the user's own chat, starting nothing
whatever the grants, stopping at the daily limit across workspaces while one raised stays, closing
once their fact clears, learning's pause dropping a paused type, no admission read without an
offer, and a failed admission read failing the cycle; saved checks: read only under the check
grant in an Ordinary vault on a host that runs them, stopping at the daily limit across
workspaces with a held-back item taking its slot, only a waiting folder raising a new item, a
stopped or paused type taking no slot, Allowed starting a waiting folder's checks through the
check host alone at that check after reading the settings again, Ask first offering Run checks,
quiet hours but not the brake holding the start, a refused start skipped, never a mission, a
moved folder closing its item, and a halt during the read, the admission or a start; inbox
suggestions: the inbox host read only under the inbox grant while Inbox is watched in an Ordinary
or Protected vault, stopping at the daily limit across workspaces with a held-back item taking its
slot, a stopped or paused type taking no slot, a run that found nothing closing its item done,
Allowed starting a waiting inbox's run once through the inbox host after reading the settings
again, Ask first offering Suggest destinations, quiet hours and the brake holding the start, a
failed or refused run not started again, a refused start skipped, never a mission, and a halt
during the admission or a start."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import random
import sqlite3
import threading
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.decision_loop.fakes import (
    WALL,
    FakeCheckHost,
    FakeClock,
    FakeCodeHost,
    FakeHost,
    FakeInboxHost,
    FakeModel,
    FakeSeatHost,
    settings_values,
)
from tests.decision_loop.test_vault_watch import old, service, spy_reads, touch, write
from viki_agent.automation.brief import (
    Goal,
    InboxFacts,
    InboxItem,
    TaskFacts,
    TaskItem,
    TickedFile,
    VaultFacts,
)
from viki_agent.automation.kanban import Card, KanbanRead
from viki_agent.automation.routines import stamp
from viki_agent.decision_loop import (
    arbiter,
    detectors,
    improve,
    learning,
    playbook,
    second_looks,
    templates,
)
from viki_agent.decision_loop.agenda import DecisionAgenda
from viki_agent.decision_loop.changes import MAX_CHANGES, ChangeRecord, Held
from viki_agent.decision_loop.contracts import (
    ALPHA,
    APP_LOOKS,
    COALESCE_S,
    FEEDBACK_SHOWN,
    GRANTS,
    HEARTBEAT_S,
    IMPROVE,
    INBOX_TRIAGE,
    LATER,
    LOOKS,
    MAX_IN_FLIGHT,
    MAX_LEARN_ROWS,
    MAX_OPEN_ITEMS,
    MAX_PER_MINUTE,
    MESSAGE_DRAFTS,
    MODEL_IDS,
    OBSERVE_S,
    PROJECT_PLANS,
    RECHECK_S,
    SAVED_CHECKS,
    SEATS,
    SECOND_LOOK_WINDOW,
    SECOND_LOOKS,
    Answer,
    AppLook,
    Candidate,
    CheckFolder,
    CheckLook,
    ChoiceAnswer,
    CodeFolder,
    CodeLook,
    ContextHint,
    DecisionLoopError,
    DecisionSettings,
    Finding,
    HeavyServer,
    InboxLook,
    InboxRun,
    ItemRecord,
    Learned,
    LookRow,
    MissionRecord,
    ModelError,
    ModelReply,
    ModelRequest,
    NoulAnswer,
    Outcome,
    PageFacts,
    Presence,
    SaveRow,
    ScoreAnswer,
    SeatFacts,
    SeatRoutine,
    SeatRun,
    SeatWaiting,
    SecondLookHint,
    SourceHint,
    StoppedBridge,
    SuggestHint,
    UncertainSend,
    VaultLook,
    WaitingChat,
    canonical_json,
    item_key,
    parse_settings,
    sha256_hex,
)
from viki_agent.decision_loop.cycle import cost_micro_usd, decode_shadow, decode_trial
from viki_agent.decision_loop.detectors import Observation
from viki_agent.decision_loop.engine import (
    _UNREAD,
    COOL_S,
    SLOT_WAIT_S,
    TEST_QUESTION,
    TEST_SCOPE,
    TEST_STATE,
    WINDOW_S,
    DecisionLoopEngine,
)
from viki_agent.decision_loop.key_store import DecisionKeyStore
from viki_agent.decision_loop.missions import MissionService
from viki_agent.decision_loop.presence import PresenceTracker
from viki_agent.decision_loop.service import _CLOSES
from viki_agent.decision_loop.stats import DecisionLoopStats, LearningRow
from viki_agent.decision_loop.store import DecisionLoopStore
from viki_agent.decision_loop.store import Usage as DayUsage
from viki_agent.decision_loop.vault_watch import (
    LinkPair,
    NoteFacts,
    SupersededPair,
    VaultChanges,
    read_changes,
)
from viki_agent.events import PermissionRequested, RequestAnswered, SessionClosed, TurnDone, Usage
from viki_agent.state import AppState, Prefs

JST = timezone(timedelta(hours=9))
TODAY = date(2026, 10, 21)  # WALL is 18:00 on the 21st in JST
DAY = TODAY.isoformat()
SCOPE = "00000000-0000-4000-8000-0000000000a1"
PLAYBOOK = playbook.PLAYBOOK_VERSION  # the version the playbook's own questions are asked under
OTHER_SCOPE = "00000000-0000-4000-8000-0000000000b2"
KEY = "ts-test-key-5d1e8a2f"
TASKS = "10-projects/plan/tasks.md"
LOGGER = "viki_agent.decision_loop.engine"
HANG_GUARD_S = 5.0  # bounds a wait that settles in milliseconds; it never fires in a healthy run
SETTLE_S = 0.05  # long enough for a cycle that need not wait to read its workspace
# The plan reading a rig's engine sees unless a test sets another: one far below every brake, as
# VIKI holds its own starts until a launch's first reading comes.
LOW_USE: Mapping[str, object] = {"status": "allowed", "utilization": 0.1}

# Every answer high: a new item scores its way to a nudge once the reversed question agrees.
STRONG: Mapping[str, Answer] = {
    "goal_link": NoulAnswer(0.95),
    "owner_only": NoulAnswer(0.9),
    "news": NoulAnswer(0.9),
    "actionable": NoulAnswer(0.9),
    "same_as_muted": NoulAnswer(0.0),
    "fits_now": NoulAnswer(0.9),
    "sensitive": NoulAnswer(0.0),
    "importance": ScoreAnswer(3.0, 0.9, {3: 0.9, 2: 0.1}),
    "kind": ChoiceAnswer("ask", 0.9, {"ask": 0.9, "inform": 0.1}),
    "work": ChoiceAnswer("none", 0.9, {"none": 0.9, "prepare": 0.1}),
}
PREPARE: Mapping[str, Answer] = {
    **STRONG,
    "work": ChoiceAnswer("prepare", 0.8, {"prepare": 0.8, "none": 0.2}),
}
RUN: Mapping[str, Answer] = {
    **STRONG,
    "work": ChoiceAnswer("run", 0.8, {"run": 0.8, "none": 0.2}),
}


Detect = Callable[
    [Observation, DecisionSettings, Sequence[MissionRecord], datetime], Sequence[Candidate]
]


class Clock(FakeClock):
    """`FakeClock` that marks each wait it starts, so a test can wait for the engine."""

    def __init__(self, changed: asyncio.Event) -> None:
        super().__init__()
        self.changed = changed

    async def wait(self, event: asyncio.Event, seconds: float) -> None:
        self.changed.set()
        await super().wait(event, seconds)


class Model(FakeModel):
    """`FakeModel` that marks each ask it starts and counts the asks cancelled in flight. From
    the `hold_at`-th ask on, asks wait on `held`; a set `input_tokens` replaces the reply's
    measured count."""

    def __init__(self, changed: asyncio.Event) -> None:
        super().__init__()
        self.changed = changed
        self.cancelled = 0
        self.input_tokens: int | None = None
        self.hold_at: int | None = None
        self.held = asyncio.Event()

    async def ask(self, request: ModelRequest, *, key: str) -> ModelReply:
        self.changed.set()
        if self.hold_at is not None:
            self.gate = self.held if len(self.requests) + 1 >= self.hold_at else None
        try:
            reply = await super().ask(request, key=key)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.input_tokens is None:
            return reply
        return replace(reply, input_tokens=self.input_tokens)


class FakeLookHost:
    """`LookHost` that answers `look` with each part not asked for left None, and records each
    read's flags as (waiting, heavy, sends, bridges), marking `changed`. While `gate` is set a
    read waits on it; `cancelled` counts the reads cancelled there."""

    def __init__(self, look: AppLook, changed: asyncio.Event) -> None:
        self.look = look
        self.changed = changed
        self.reads: list[tuple[bool, bool, bool, bool]] = []
        self.gate: asyncio.Event | None = None
        self.cancelled = 0

    async def read(self, *, waiting: bool, heavy: bool, sends: bool, bridges: bool) -> AppLook:
        self.reads.append((waiting, heavy, sends, bridges))
        self.changed.set()
        if self.gate is not None:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        look = self.look
        return AppLook(
            look.waiting if waiting else None,
            look.heavy if heavy else None,
            look.sends if sends else None,
            look.bridges if bridges else None,
            look.failures,
        )


class Rig:
    """An engine over a real store, the real cycle and detectors, and fakes for the rest."""

    def __init__(self, tmp_path: Path) -> None:
        self.changed = asyncio.Event()
        self.clock = Clock(self.changed)
        self.state = AppState(tmp_path / "state.json", Prefs()).store
        self.store = DecisionLoopStore(self.state)
        self.agenda = DecisionAgenda(self.state)
        self.keys = DecisionKeyStore(self.state, now=self.clock.wall)
        self.model = Model(self.changed)
        self.host = FakeHost()
        self.seats = FakeSeatHost()
        self.values = settings_values(**{"decision.enabled": True})
        self.missions = MissionService(
            self.state, self.host, self.settings, wall=self.clock.wall, roots=self.root
        )
        self.presence = PresenceTracker(monotonic=self.clock.monotonic)
        self.observation = observation()
        self.gathered = 0
        self.kanbans: list[bool] = []  # each gather's `kanban`
        self.attended = True
        self.reading: Mapping[str, object] | None = LOW_USE  # the plan reading the engine sees
        self.looks: FakeLookHost | None = None  # the app's Look host, none unless a test sets one
        self.code: FakeCodeHost | None = None  # the code folders' host, none unless a test sets one
        self.checks: FakeCheckHost | None = None  # the saved checks' host, none unless set
        self.inbox: FakeInboxHost | None = None  # the inbox suggestion host, none unless set
        self.engine = self.build(self.keys)

    def build(
        self, keys: DecisionKeyStore, detect: Detect = detectors.detect
    ) -> DecisionLoopEngine:
        return DecisionLoopEngine(
            self.store,
            self.agenda,
            keys,
            self.model,
            self.missions,
            self.presence,
            self.settings,
            self.gather,
            detect,
            clock=self.clock,
            attended=lambda: self.attended,
            zone=JST,
            plan_reading=lambda: self.reading,
            seats=self.seats,
            looks=self.looks,
            code=self.code,
            checks=self.checks,
            inbox=self.inbox,
        )

    async def root(self, session_id: str, workspace_id: str) -> bool:
        """The missions' `roots` port: a look's root is recorded in the host's calls."""
        self.host.calls.append(("root", session_id, workspace_id))
        return True

    def settings(self) -> DecisionSettings:
        return parse_settings(self.values)

    def gather(self, page: PageFacts | None, today: date, kanban: bool = False) -> Observation:
        """`observation`, its Kanban left unread unless `kanban`, as `detectors.gather` does."""
        self.gathered += 1
        self.kanbans.append(kanban)
        return self.observation if kanban else replace(self.observation, kanban=None)

    async def until(self, ready: Callable[[], object]) -> None:
        async with asyncio.timeout(HANG_GUARD_S):
            while not ready():
                self.changed.clear()
                await self.changed.wait()

    async def parked(self, since: int) -> None:
        """Until the engine's clock waits again after its `since`-th wait."""
        await self.until(lambda: len(self.clock.waits) > since and self.clock.pending == 1)

    async def status(self) -> str:
        return (await self.engine.status()).state

    def rows(self, table: str) -> list[dict[str, Any]]:
        with self.state._connection() as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    rig = Rig(tmp_path)
    yield rig
    await asyncio.wait_for(rig.engine.stop(), HANG_GUARD_S)


def task(text: str, due: date = TODAY) -> TaskItem:
    return TaskItem(ref=TASKS, line=1, text=text, due=due, tags=())


def tasks(*names: str) -> tuple[TaskItem, ...]:
    return tuple(task(f"Task {name}") for name in names)


def observation(
    *items: TaskItem,
    captures: tuple[InboxItem, ...] = (),
    ticked: tuple[TickedFile, ...] = (),
) -> Observation:
    """The active vault workspace with these tasks due, these inbox captures and these files of
    ticked tasks."""
    task_facts = TaskFacts(
        overdue=sum(1 for item in items if item.due < TODAY),
        today=sum(1 for item in items if item.due == TODAY),
        soon=sum(1 for item in items if item.due > TODAY),
        items=items,
        files=1,
        files_key="tasks",
        partial=False,
        ticked=ticked,
    )
    inbox = InboxFacts(
        count=len(captures),
        oldest=None,
        checked=len(captures),
        undated=0,
        partial=False,
        items=captures,
    )
    return Observation(SCOPE, "vault", VaultFacts(inbox, task_facts), (), (), None, ())


def key_of(text: str) -> str:
    return item_key("tasks_due", TASKS, text)


def titles(model: FakeModel) -> list[str]:
    """The title of each asked candidate, in the order asked."""
    found: list[str] = []
    for request in model.requests:
        candidate = request.state.get("candidate")
        assert isinstance(candidate, dict)
        found.append(candidate["title"])
    return found


# ---- the limits ------------------------------------------------------------------------


async def test_in_flight_and_minute_limits(rig: Rig) -> None:
    rig.keys.save(KEY)
    rig.observation = observation(*(task(f"Task {n:02d}") for n in range(12)))
    await rig.engine.recheck()
    # Ten requests in a minute; the rest wait on the rules, unasked.
    assert titles(rig.model) == [f"Task {n:02d}" for n in range(MAX_PER_MINUTE)]
    for n in range(12):
        found = rig.agenda.item(SCOPE, key_of(f"Task {n:02d}"))
        assert found is not None
        assert (found.answers_json is None) == (n >= MAX_PER_MINUTE)
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert titles(rig.model)[MAX_PER_MINUTE:] == ["Task 10", "Task 11"]

    # Two requests in flight at most: a third waits for a slot, then gives up.
    rig.model.gate = asyncio.Event()
    tests = [asyncio.ensure_future(rig.engine.test_key()) for _ in range(3)]
    await rig.until(lambda: rig.model.in_flight == MAX_IN_FLIGHT and rig.clock.pending == 1)
    assert len(rig.model.requests) == 14
    rig.clock.advance(SLOT_WAIT_S)
    done, waiting = await asyncio.wait(
        tests, timeout=HANG_GUARD_S, return_when=asyncio.FIRST_COMPLETED
    )
    (refused,) = done
    result = refused.result()
    assert isinstance(result, ModelError) and result.code == "rate_limited"
    rig.model.gate.set()
    for test in waiting:
        assert isinstance(await asyncio.wait_for(test, HANG_GUARD_S), ModelReply)
    assert (rig.model.most_in_flight, len(rig.model.requests)) == (MAX_IN_FLIGHT, 14)


async def test_daily_cap_counts_measured_spend(rig: Rig) -> None:
    rig.keys.save(KEY)
    rig.values["decision.daily_cap_usd"] = 0.1  # 100,000 micro-USD
    rig.model.input_tokens = 1_000_000  # each answer measures 42,000 micro-USD
    rig.observation = observation(*tasks("a", "b", "c", "d"))
    await rig.engine.recheck()
    # 0, 42,000 and 84,000 plus an estimate stay under the cap; 126,000 reaches it.
    assert titles(rig.model) == ["Task a", "Task b", "Task c"]
    assert rig.store.spent_on(DAY) == 3 * cost_micro_usd(1_000_000) == 126_000
    held = rig.agenda.item(SCOPE, key_of("Task d"))
    assert held is not None and (held.level, held.answers_json) == ("list", None)
    assert await rig.status() == "capped"
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert len(rig.model.requests) == 3

    # The next request's estimate counts too: one micro-USD short of the cap asks nothing.
    rig.clock.advance(timedelta(days=1).total_seconds())
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    assert rig.store.add_spend(SCOPE, tomorrow, 99_999, rig.clock.wall()) == 99_999
    await rig.engine.recheck()
    assert len(rig.model.requests) == 3
    assert await rig.status() == "capped"
    rig.values["decision.daily_cap_usd"] = 0.25
    assert await rig.status() == "ready"
    await rig.engine.recheck()
    assert len(rig.model.requests) == 7
    assert rig.store.spent_on(tomorrow) == 99_999 + 4 * 42_000


async def test_failures_fall_back_without_retry(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    rig.keys.save(KEY)
    rig.observation = observation(*tasks("a", "b", "c", "d"))
    sticky = ("refused", "too_large", "invalid_reply")
    rig.model.script(*(ModelError(code) for code in sticky), ModelError("unreachable"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await rig.engine.recheck()
    # One request each, none repeated; every item falls back to the rules.
    assert titles(rig.model) == ["Task a", "Task b", "Task c", "Task d"]
    for name in "abcd":
        found = rig.agenda.item(SCOPE, key_of(f"Task {name}"))
        assert found is not None and (found.level, found.answers_json) == ("list", None)
    failures = sorted(call.failure or "" for call in rig.agenda.calls(SCOPE))
    assert failures == sorted([*sticky, "unreachable"])
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert messages == [
        "decision model ask failed: refused",
        "decision model ask failed: too_large",
        "decision model ask failed: invalid_reply",
        "decision model unreachable",
    ]
    status = await rig.engine.status()
    assert (status.state, status.last_error) == ("unreachable", "unreachable")

    # The next cycle asks only the item no answer reached; the others stay on the rules.
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert titles(rig.model)[4:] == ["Task d"]
    assert await rig.status() == "ready"

    # A new state (the task moved to tomorrow) is asked again.
    rig.observation = observation(task("Task a", TODAY + timedelta(days=1)), *tasks("b", "c", "d"))
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert titles(rig.model)[5:] == ["Task a"]


async def test_key_rejected_pauses_until_key_changes(rig: Rig) -> None:
    rig.keys.save(KEY)
    rig.observation = observation(*tasks("a", "b", "c"))
    rig.model.script(ModelError("key_rejected"))
    await rig.engine.recheck()
    assert titles(rig.model) == ["Task a"]
    status = await rig.engine.status()
    assert (status.state, status.last_error) == ("key_rejected", "key_rejected")
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert len(rig.model.requests) == 1

    rig.keys.save(KEY)  # the same key, saved again
    assert await rig.status() == "ready"
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert titles(rig.model)[1:] == ["Task a", "Task b", "Task c"]
    assert rig.model.keys == [KEY] * 4

    # A key saved after the engine read the stamp but before it read the key is still tried:
    # the pause names the stamp read first.
    class SavedWhileLoading(DecisionKeyStore):
        armed = False

        def load(self) -> str | None:
            if self.armed:
                self.armed = False
                self.save(KEY)
            return super().load()

    keys = SavedWhileLoading(rig.state, now=rig.clock.wall)
    await rig.engine.stop()
    rig.engine = rig.build(keys)
    rig.observation = observation(*tasks("e", "f"))
    keys.armed = True
    rig.model.script(ModelError("key_rejected"))
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert titles(rig.model)[4:] == ["Task e"]
    assert await rig.status() == "ready"
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert titles(rig.model)[5:] == ["Task e", "Task f"]


@pytest.mark.parametrize(
    ("error", "cool_s"),
    [(ModelError("rate_limited", 30.0), 30.0), (ModelError("overloaded"), COOL_S)],
)
async def test_rate_limit_cools_every_item(rig: Rig, error: ModelError, cool_s: float) -> None:
    rig.keys.save(KEY)
    rig.observation = observation(*tasks("a", "b", "c"))
    rig.model.script(error)
    await rig.engine.recheck()
    assert titles(rig.model) == ["Task a"]
    status = await rig.engine.status()
    assert (status.state, status.last_error) == ("rules", error.code)
    rig.clock.advance(cool_s - 1)
    await rig.engine.recheck()
    assert len(rig.model.requests) == 1
    rig.clock.advance(1)
    await rig.engine.recheck()
    assert titles(rig.model)[1:] == ["Task a", "Task b", "Task c"]
    assert await rig.status() == "ready"


# ---- the clock -------------------------------------------------------------------------


async def test_wakes_coalesce_and_the_beat_paces_lambda(rig: Rig) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.observation = observation(task("Task a"))
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)
    assert rig.gathered == 1
    assert (rig.engine.nudge, rig.engine.glow) == (1, 0)  # one new nudge

    # Wakes within COALESCE_S of the first make one cycle, COALESCE_S after the first.
    seen = len(rig.clock.waits)
    rig.engine.wake("change")
    await rig.parked(seen)
    rig.clock.advance(COALESCE_S / 2)
    seen = len(rig.clock.waits)
    rig.engine.wake("launcher")
    await rig.parked(seen)
    assert rig.gathered == 1
    seen = len(rig.clock.waits)
    rig.clock.advance(COALESCE_S / 2)
    await rig.parked(seen)
    assert rig.gathered == 2
    assert rig.store.get_json(SCOPE, "lambda") is None

    # The beat closes the λ window: the window's one nudge against two an hour raises it.
    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S)
    await rig.parked(seen)
    assert rig.store.get_json(SCOPE, "lambda") == pytest.approx(
        arbiter.pace(arbiter.START_LAMBDA, 1, 2)
    )
    assert rig.gathered == 3  # one check for the five minutes, not one per missed minute
    assert (rig.engine.nudge, rig.engine.glow) == (1, 0)


@pytest.mark.parametrize("monotonic", [True, False])
async def test_sleep_gap_runs_one_cycle(rig: Rig, monotonic: bool) -> None:
    rig.observation = observation(task("Task a"))
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)
    assert rig.gathered == 1

    # Asleep for three beats. Where the monotonic clock stops in sleep, the wall clock tells.
    seen = len(rig.clock.waits)
    if monotonic:
        rig.clock.advance(3 * HEARTBEAT_S)
    else:
        rig.clock.advance(3 * HEARTBEAT_S, monotonic=False)
        rig.clock.advance(OBSERVE_S, wall=False)
    await rig.parked(seen)
    assert rig.gathered == 2  # one cycle; the missed checks and beats are not replayed
    assert rig.store.get_json(SCOPE, "lambda") is None

    # The beats start again from the wake.
    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S - 1)
    await rig.parked(seen)
    assert rig.store.get_json(SCOPE, "lambda") is None
    seen = len(rig.clock.waits)
    rig.clock.advance(1)
    await rig.parked(seen)
    assert rig.store.get_json(SCOPE, "lambda") == pytest.approx(
        arbiter.pace(arbiter.START_LAMBDA, 0, 2)
    )


async def test_a_beat_that_fails_unexpectedly_leaves_the_clock_running(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.observation = observation(task("Task a"))
    secret = "Marker 4e9b: words of the store's own error"
    save = rig.store.set_json
    failures = [RuntimeError(secret)]

    def flaky(scope: str, name: str, value: object, now: datetime) -> None:
        if failures:
            raise failures.pop()
        save(scope, name, value, now)

    monkeypatch.setattr(rig.store, "set_json", flaky)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)
    assert (rig.gathered, rig.engine.nudge) == (1, 1)  # one new nudge for the beat to pace

    # The beat's store write fails with a raw error, once: the clock goes on, and its cycles too.
    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S)
    await rig.parked(seen)
    assert failures == [] and rig.gathered == 2
    assert rig.store.get_json(SCOPE, "lambda") is None
    seen = len(rig.clock.waits)
    rig.engine.wake("change")
    rig.clock.advance(COALESCE_S)
    await rig.parked(seen)
    assert rig.gathered == 3

    # The next beat paces only its own window: the failed one is not made up.
    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S - COALESCE_S)
    await rig.parked(seen)
    assert rig.store.get_json(SCOPE, "lambda") == pytest.approx(
        arbiter.pace(arbiter.START_LAMBDA, 0, 2)
    )
    ours = [record for record in caplog.records if record.name == LOGGER]
    assert [record.getMessage() for record in ours] == ["decision beat failed: RuntimeError"]
    assert secret not in caplog.text and all(record.exc_info is None for record in ours)


async def test_cancelling_the_clock_during_a_beat_ends_it(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.observation = observation(task("Task a"))
    entered, release, left = threading.Event(), threading.Event(), threading.Event()
    pace = rig.engine._pace

    def held(scope: str, shown: float, per_hour: int, now: datetime) -> None:
        entered.set()
        release.wait(HANG_GUARD_S)
        try:
            pace(scope, shown, per_hour, now)
        finally:
            left.set()

    monkeypatch.setattr(rig.engine, "_pace", held)
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)
    ticking = rig.engine._task
    assert ticking is not None
    rig.clock.advance(HEARTBEAT_S)
    try:
        assert await asyncio.to_thread(entered.wait, HANG_GUARD_S)
        ticking.cancel()  # as the event loop's shutdown cancels it
        # The beat's thread writes on: the clock holds the cycle lock until it ends, so work
        # between cycles (a Reset) waits for it.
        work = asyncio.ensure_future(rig.engine.between_cycles(left.is_set))
        await asyncio.wait((ticking, work), timeout=SETTLE_S)
        assert not ticking.done() and not work.done()
    finally:
        release.set()
        await asyncio.to_thread(left.wait, HANG_GUARD_S)
    await asyncio.wait((ticking,), timeout=HANG_GUARD_S)
    assert ticking.cancelled()
    assert await asyncio.wait_for(work, HANG_GUARD_S) is True


# ---- off and keyless -------------------------------------------------------------------


@pytest.mark.parametrize("why", ["off", "paused", "unattended"])
async def test_off_makes_no_request(rig: Rig, why: str, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.keys.save(KEY)
    rig.observation = observation(task("Task a"))
    if why == "off":
        rig.values["decision.enabled"] = False
    elif why == "paused":
        rig.values["decision.paused_until"] = stamp(WALL + timedelta(hours=1))
    else:
        rig.attended = False
    polls: list[datetime] = []
    poll = rig.missions.poll

    async def counted(now: datetime) -> list[MissionRecord]:
        polls.append(now)
        return await poll(now)

    monkeypatch.setattr(rig.missions, "poll", counted)
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)
    seen = len(rig.clock.waits)
    rig.engine.wake("change")
    rig.clock.advance(COALESCE_S)
    await rig.parked(seen)
    await rig.engine.recheck()
    await rig.engine.stop()
    assert rig.model.requests == [] and rig.gathered == 0
    for table in ("decision_episodes", "decision_items", "decision_calls"):
        assert rig.rows(table) == []
    assert rig.store.spent_on(DAY) == 0 and rig.store.get_json(SCOPE, "lambda") is None
    assert len(polls) >= 3  # the missions are still watched
    assert (await rig.engine.status()).checked_at is None


async def test_no_key_runs_rules(rig: Rig) -> None:
    rig.observation = observation(*tasks("a", "b"))
    await rig.engine.recheck()
    assert rig.model.requests == []
    for name in "ab":
        found = rig.agenda.item(SCOPE, key_of(f"Task {name}"))
        assert found is not None and (found.level, found.answers_json) == ("list", None)
    episodes = rig.rows("decision_episodes")
    assert [(row["policy"], row["model"]) for row in episodes] == [("rules", None)] * 2
    status = await rig.engine.status()
    assert (status.state, status.checked_at) == ("no_key", WALL)


# ---- missions --------------------------------------------------------------------------


def count_starts(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The item key of every mission start the engine asks for, made or refused."""
    starts: list[str] = []
    start = rig.missions.start

    async def counted(scope: str, item: Any, work: str, **options: Any) -> MissionRecord:
        starts.append(item.item_key)
        return await start(scope, item, work, **options)

    monkeypatch.setattr(rig.missions, "start", counted)
    return starts


async def test_allowed_prepare_starts_a_mission(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    rig.values["decision.grant.prepare"] = "ask"
    rig.observation = observation(task("Task a"))
    starts = count_starts(rig, monkeypatch)
    # On "ask" the user starts the work: the item offers it, and nothing starts.
    await rig.engine.recheck()
    first = rig.agenda.item(SCOPE, key_of("Task a"))
    assert first is not None and (first.level, first.timing) == ("nudge", "now")
    assert "start_prepare" in [action.type for action in first.body.actions]
    assert starts == [] and rig.host.calls == []

    # On "allowed" VIKI starts it: a plan-mode chat that gets the brief.
    rig.values["decision.grant.prepare"] = "allowed"
    await rig.engine.recheck()
    (mission,) = rig.missions.running()
    assert (mission.scope, mission.item_key, mission.work) == (SCOPE, key_of("Task a"), "prepare")
    session = mission.session_id
    assert rig.host.calls == [
        ("remember", session, {"new_session": True, "mode": "plan"}),
        ("create", session),
    ]
    actor = rig.host.actors[session]
    assert actor.sent[0].startswith("VIKI started this chat (Decision).\nGoal: ")

    # The item carries its running mission.
    await rig.engine.recheck()
    carried = rig.agenda.item(SCOPE, key_of("Task a"))
    assert carried is not None and carried.mission_id == mission.mission_id

    # The mission ends: the next poll reports it, and the cycle after shows the report.
    actor.emit(TurnDone(status="success", usage=Usage()))
    await rig.engine.recheck()
    await rig.engine.recheck()
    report = rig.agenda.item(SCOPE, item_key("mission_report", mission.mission_id))
    assert report is not None and (report.candidate, report.state) == ("mission_report", "open")
    chats = [action.session_id for action in report.body.actions if action.type == "open_chat"]
    assert chats == [session] and rig.missions.running() == []

    # Later cycles on the same state start nothing again.
    await rig.engine.recheck()
    assert starts == [key_of("Task a")]
    assert [call for call in rig.host.calls if call[0] == "create"] == [("create", session)]

    # A new state (the task moved to tomorrow) is new work: a new chat.
    rig.observation = observation(task("Task a", TODAY + timedelta(days=1)))
    await rig.engine.recheck()
    assert starts == [key_of("Task a")] * 2
    (again,) = rig.missions.running()
    assert again.session_id != session and again.item_key == key_of("Task a")


async def test_a_refused_start_is_not_retried(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    rig.values["decision.limit.drafts"] = 0  # every prepare start is refused: limit
    rig.observation = observation(task("Task a"))
    starts = count_starts(rig, monkeypatch)
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert starts == [key_of("Task a")]
    assert rig.host.calls == [] and rig.missions.running() == []


def plan_reading(
    *, status: str = "allowed", use: float | None = None, reset: int | None = None, **top: object
) -> dict[str, object]:
    """A plan reading whose five-hour window has `use` and `reset` (unix seconds); `top` replaces
    the reading's own top-level keys."""
    return {
        "status": status,
        "windows": {"five_hour": {"utilization": use, "resets_at": reset}, "seven_day": None},
        **top,
    }


def at_wall(seconds: float) -> int:
    """Unix seconds `seconds` after the rig's clock now."""
    return int(WALL.timestamp() + seconds)


@pytest.mark.parametrize("release", ["brake", "reset"])
async def test_the_plan_brake_holds_vikis_own_starts_until_it_releases(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, release: str
) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    rig.values["decision.grant.prepare"] = "allowed"
    rig.observation = observation(task("Task a"))
    rig.reading = plan_reading(use=0.8, reset=at_wall(3600))
    starts = count_starts(rig, monkeypatch)

    # The brake holds the start: nothing is asked of the missions, stored or remembered.
    await rig.engine.recheck()
    await rig.engine.recheck()
    held = rig.agenda.item(SCOPE, key_of("Task a"))
    assert held is not None and (held.state, held.mission_id) == ("open", None)
    assert starts == [] and rig.host.calls == [] and rig.rows("decision_missions") == []

    # Once it lets go, the same state starts at the next cycle, as if never tried.
    if release == "brake":
        rig.values["decision.plan_brake"] = "95"
    else:
        rig.reading = plan_reading(use=0.79, reset=at_wall(5 * 3600))
    await rig.engine.recheck()
    assert starts == [key_of("Task a")]
    (mission,) = rig.missions.running()
    assert (mission.scope, mission.item_key) == (SCOPE, key_of("Task a"))


async def test_a_rejected_reading_holds_even_with_the_brake_off(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    rig.values["decision.grant.prepare"] = "allowed"
    rig.values["decision.plan_brake"] = "off"
    starts = count_starts(rig, monkeypatch)

    # Off ignores the use: 0.99 starts.
    rig.observation = observation(task("Task a"))
    rig.reading = plan_reading(use=0.99, reset=at_wall(3600))
    await rig.engine.recheck()
    assert starts == [key_of("Task a")]

    # A rejected reading with its reset ahead holds, Off or not.
    rig.observation = observation(task("Task b"))
    rig.reading = plan_reading(status="rejected", resets_at=at_wall(3600))
    await rig.engine.recheck()
    assert starts == [key_of("Task a")]

    # Past its own reset it holds no more, whatever a later weekly reset says.
    rig.reading = plan_reading(
        status="rejected",
        resets_at=at_wall(-60),
        windows={"five_hour": None, "seven_day": {"utilization": 0.6, "resets_at": at_wall(86400)}},
    )
    await rig.engine.recheck()
    assert starts == [key_of("Task a"), key_of("Task b")]


# ---- each item's showings of the last day ------------------------------------------------------


async def test_an_items_showings_of_the_last_day_reach_the_arbiter(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.observation = observation(*tasks("a", "b"))
    seen: list[tuple[str, int]] = []
    decide = arbiter.decide

    def spy(inp: arbiter.ArbiterInput) -> Outcome:
        seen.append((inp.candidate.item_key, inp.exposures_24h))
        return decide(inp)

    monkeypatch.setattr(arbiter, "decide", spy)
    await rig.engine.recheck()
    assert set(seen) == {(key_of("Task a"), 0), (key_of("Task b"), 0)}

    # The page shows Task a three times, an hour apart.
    shown = rig.agenda.item(SCOPE, key_of("Task a"))
    assert shown is not None
    for _ in range(3):
        rig.clock.advance(3600)
        rig.store.observe(
            SCOPE, event="shown", episode_ids=[shown.episode_id], now=rig.clock.wall()
        )
    seen.clear()
    await rig.engine.recheck()
    assert set(seen) == {(key_of("Task a"), 3), (key_of("Task b"), 0)}

    # A day after the second showing, the day holds half the time from the first to the last:
    # 1.5 showings, rounded up. A day after the last, none.
    rig.clock.advance(timedelta(hours=23).total_seconds())
    seen.clear()
    await rig.engine.recheck()
    assert set(seen) == {(key_of("Task a"), 2), (key_of("Task b"), 0)}
    rig.clock.advance(timedelta(hours=1, milliseconds=1).total_seconds())
    seen.clear()
    await rig.engine.recheck()
    assert set(seen) == {(key_of("Task a"), 0), (key_of("Task b"), 0)}


async def test_a_failed_read_of_the_showings_fails_that_cycle_unretried(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    rig.observation = observation(task("Task a"))
    asked: list[datetime] = []
    read = rig.store.exposures_since
    broken = [True]

    def flaky(scope: str, since: datetime) -> dict[str, int]:
        asked.append(since)
        if broken[0]:
            raise DecisionLoopError("unavailable")
        return read(scope, since)

    monkeypatch.setattr(rig.store, "exposures_since", flaky)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await rig.engine.recheck()
    assert asked == [WALL - timedelta(hours=24)]
    assert rig.rows("decision_episodes") == [] and rig.rows("decision_items") == []
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert messages == ["decision cycle failed at inputs: unavailable"]

    broken[0] = False
    await rig.engine.recheck()
    assert len(asked) == 2
    found = rig.agenda.item(SCOPE, key_of("Task a"))
    assert found is not None and found.level == "list"


async def test_a_nudge_in_one_workspace_counts_toward_the_hourly_cap_in_another(rig: Rig) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.values["decision.nudges_per_hour"] = 1
    rig.observation = observation(task("Task a"))
    await rig.engine.recheck()
    shown = rig.agenda.item(SCOPE, key_of("Task a"))
    assert shown is not None and shown.level == "nudge"

    # Five minutes on, in another workspace, the hour's one nudge is already spent.
    rig.clock.advance(5 * 60)
    rig.observation = replace(observation(task("Task b")), scope=OTHER_SCOPE)
    await rig.engine.recheck()
    held = rig.agenda.item(OTHER_SCOPE, key_of("Task b"))
    assert held is not None and held.level == "badge"
    assert rig.engine.nudge == 1


# ---- recheck, stop and the key test ------------------------------------------------------------


async def test_recheck_is_bounded_and_halts_cleanly(rig: Rig) -> None:
    rig.keys.save(KEY)
    rig.observation = observation(*tasks("a", "b", "c"))
    rig.model.script(ModelError("refused"))  # Task a's request is refused
    rig.model.hold_at = 2  # Task b's request never answers
    recheck = asyncio.ensure_future(rig.engine.recheck())
    await rig.until(lambda: rig.model.in_flight == 1 and rig.clock.pending == 1)
    assert titles(rig.model) == ["Task a", "Task b"]
    rig.clock.advance(RECHECK_S)
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    # The request in flight is cancelled; the item before it stays committed, the item it was
    # for and the one after it stay as they were.
    assert (rig.model.cancelled, rig.model.in_flight) == (1, 0)
    refused = rig.agenda.item(SCOPE, key_of("Task a"))
    assert refused is not None and (refused.level, refused.answers_json) == ("list", None)
    assert rig.agenda.item(SCOPE, key_of("Task b")) is None
    assert rig.agenda.item(SCOPE, key_of("Task c")) is None
    assert [call.failure for call in rig.agenda.calls(SCOPE)] == ["refused"]
    assert len(rig.rows("decision_episodes")) == 1

    # The refusal is kept through the halt: the next cycle asks only the other two.
    rig.model.hold_at, rig.model.gate = None, None
    await rig.engine.recheck()
    assert titles(rig.model)[2:] == ["Task b", "Task c"]


async def test_stop_cancels_the_request_in_flight(rig: Rig) -> None:
    rig.keys.save(KEY)
    rig.observation = observation(task("Task a"))
    rig.model.gate = asyncio.Event()
    rig.engine.start()
    await rig.until(lambda: rig.model.in_flight == 1)
    await asyncio.wait_for(rig.engine.stop(), HANG_GUARD_S)
    assert (rig.model.cancelled, rig.model.in_flight) == (1, 0)
    for table in ("decision_episodes", "decision_items", "decision_calls"):
        assert rig.rows(table) == []
    assert rig.store.spent_on(DAY) == 0

    # A stopped engine stays stopped.
    rig.engine.start()
    rig.engine.wake("change")
    await rig.engine.recheck()
    assert rig.gathered == 1 and len(rig.model.requests) == 1


async def test_test_key_asks_one_question_on_a_fixed_state(rig: Rig) -> None:
    missing = await rig.engine.test_key()
    assert isinstance(missing, ModelError) and missing.code == "no_key"
    assert rig.model.requests == []

    rig.keys.save(KEY)
    rig.model.latency_ms = 112
    reply = await rig.engine.test_key()
    assert isinstance(reply, ModelReply)
    (request,) = rig.model.requests
    assert (request.model, request.state) == (MODEL_IDS["pinned"], TEST_STATE)
    # A decision's shape: the guide first, then the made-up item.
    assert list(request.state) == ["guide", "candidate"]
    assert request.state["guide"] == list(playbook.GUIDE)
    assert request.questions == {TEST_QUESTION: playbook.questions()[TEST_QUESTION]}
    assert request.questions[TEST_QUESTION]["type"] == "noul"
    assert (await rig.engine.status()).last_test == (WALL, 112)
    # Its spend is real: it counts toward the day, under no workspace. It records no call.
    assert rig.store.spent_on(DAY) == cost_micro_usd(reply.input_tokens) > 0
    assert rig.store.get_json(SCOPE, "lambda") is None and rig.rows("decision_calls") == []
    assert {row["scope"] for row in rig.rows("decision_preferences")} == {TEST_SCOPE}

    # A rejected key pauses the loop; a test the same key passes lifts the pause.
    rig.model.script(ModelError("key_rejected"))
    rejected = await rig.engine.test_key()
    assert isinstance(rejected, ModelError) and rejected.code == "key_rejected"
    assert await rig.status() == "key_rejected"
    assert isinstance(await rig.engine.test_key(), ModelReply)
    assert await rig.status() == "ready"


# ---- work between cycles (Delete all) ----------------------------------------------------------


async def test_work_between_cycles_halts_the_clocks_cycle_and_the_clock_runs_on(rig: Rig) -> None:
    rig.keys.save(KEY)
    rig.observation = observation(*tasks("a", "b"))
    rig.model.gate = asyncio.Event()  # Task a's request is never answered
    rig.engine.start()
    await rig.until(lambda: rig.model.in_flight == 1)
    # The work waits for no answer: the request in flight is cancelled, and the cycle ends before
    # Task a's first write.
    in_flight = await asyncio.wait_for(
        rig.engine.between_cycles(lambda: rig.model.in_flight), HANG_GUARD_S
    )
    assert (in_flight, rig.model.cancelled) == (0, 1)
    assert rig.rows("decision_items") == [] and rig.rows("decision_episodes") == []

    # The clock runs on: the next wake runs a whole cycle.
    rig.model.gate = None
    await rig.parked(0)
    seen = len(rig.clock.waits)
    rig.engine.wake("change")
    await rig.parked(seen)
    seen = len(rig.clock.waits)
    rig.clock.advance(COALESCE_S)
    await rig.parked(seen)
    assert rig.gathered == 2
    assert {row["item_key"] for row in rig.rows("decision_items")} == {
        key_of("Task a"),
        key_of("Task b"),
    }


@pytest.mark.parametrize("cancelled", [False, True], ids=["awaited", "caller-cancelled"])
async def test_work_between_cycles_keeps_the_lock_until_it_ends(rig: Rig, cancelled: bool) -> None:
    rig.observation = observation(task("Task a"))
    started, release = threading.Event(), threading.Event()
    ended: list[tuple[int, int]] = []  # the work's thread, and the cycles run by its end

    def work() -> str:
        started.set()
        release.wait(HANG_GUARD_S)
        ended.append((threading.get_ident(), rig.gathered))
        return "done"

    job = asyncio.ensure_future(rig.engine.between_cycles(work))
    assert await asyncio.to_thread(started.wait, HANG_GUARD_S)
    if cancelled:
        job.cancel()
    recheck = asyncio.ensure_future(rig.engine.recheck())
    await asyncio.wait((recheck,), timeout=SETTLE_S)
    release.set()
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    # The work ran in a worker thread with no cycle beside it; the cycle then ran in full.
    [(thread, gathered)] = ended
    assert thread != threading.get_ident() and gathered == 0
    found = rig.agenda.item(SCOPE, key_of("Task a"))
    assert rig.gathered == 1 and found is not None and found.level == "list"
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await job
    else:
        assert await job == "done"


async def test_candidates_are_capped_by_deadline_first(rig: Rig) -> None:
    items = [task(f"Task {n:02d}", TODAY + timedelta(days=n % 7)) for n in range(55)]
    rig.observation = observation(*items)
    await rig.engine.recheck()
    kept = sorted(items, key=lambda item: (item.due, item.text))[:MAX_OPEN_ITEMS]
    assert {row["item_key"] for row in rig.rows("decision_items")} == {
        key_of(item.text) for item in kept
    }


async def test_a_reorder_of_the_same_goals_keeps_every_items_state(rig: Rig) -> None:
    """The active projects arrive newest-modified first, so editing one overview reorders them;
    the same goals in another order must not give an item a new state, or VIKI would ask again
    and start the same unchanged task's work again."""
    alpha = Goal("10-projects/alpha/alpha.md", "Alpha launch", ())
    beta = Goal("10-projects/beta/beta.md", "Beta research", ())
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    base = observation(task("Task a"))
    rig.observation = replace(base, goals=(alpha, beta))
    await rig.engine.recheck()
    first = rig.agenda.item(base.scope, key_of("Task a"))
    (mission,) = rig.missions.running()
    rig.host.actors[mission.session_id].emit(TurnDone(status="success", usage=Usage()))
    await rig.engine.recheck()
    await rig.engine.recheck()  # the finished chat's report settles before the count
    asked = len(rig.model.requests)

    rig.observation = replace(base, goals=(beta, alpha))
    await rig.engine.recheck()

    second = rig.agenda.item(base.scope, key_of("Task a"))
    assert first is not None and second is not None
    assert second.state_hash == first.state_hash
    assert len(rig.model.requests) == asked
    assert [call for call in rig.host.calls if call[0] == "create"] == [
        ("create", mission.session_id)
    ]


# ---- learning: its numbers in cycles, pacing by attention, the beat's relearn -------------------


def snapshot() -> Learned:
    """A snapshot whose every number differs from its start value, each inside its bounds: a
    lower bias, a stronger goal link, an offset for due tasks, and T and C of its own."""
    start = learning.relearn([], now=WALL)
    return replace(
        start,
        labels=40,
        weights={**start.weights, "bias": -0.2, "goal_link": 1.1, "topic.tasks_due": 0.8},
        reach={"list": 0.4, "badge": 0.7, "nudge": 0.8, "alert": 0.9},
        costs={"badge": 0.08, "nudge": 0.6, "alert": 1.5},
    )


def terms_of(rig: Rig, text: str) -> dict[str, float]:
    """The terms stored with the task's decision episode."""
    (row,) = [row for row in rig.rows("decision_episodes") if row["item_key"] == key_of(text)]
    return json.loads(row["terms"])


async def test_cycles_use_learned_numbers_only_while_learning_is_on(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    learned = snapshot()
    stored = learning.encode(learned)
    rig.store.set_json(SCOPE, "learned", stored, WALL)
    assert learning.parse(rig.store.get_json(SCOPE, "learned")) == learned
    seen: list[tuple[dict[str, float], dict[str, float], dict[str, float]]] = []
    decide = arbiter.decide

    def spy(inp: arbiter.ArbiterInput) -> Outcome:
        seen.append((dict(inp.weights), dict(inp.reach), dict(inp.costs)))
        return decide(inp)

    monkeypatch.setattr(arbiter, "decide", spy)

    # Learning on, the default: every decision reads the snapshot's numbers, and its terms show
    # them: T, and p with the due tasks' offset.
    rig.observation = observation(task("Task a"))
    await rig.engine.recheck()
    assert seen and all(numbers == learning.inputs(learned) for numbers in seen)
    terms = terms_of(rig, "Task a")
    assert terms["T.nudge"] == learned.reach["nudge"]
    assert terms["p"] == round(arbiter.p_of(STRONG, learned.weights, "tasks_due"), 4)

    # Learning off: the start values; the snapshot stays stored.
    rig.values["decision.learning"] = False
    seen.clear()
    rig.observation = observation(task("Task b"))
    await rig.engine.recheck()
    assert seen and all(numbers == learning.inputs(None) for numbers in seen)
    terms = terms_of(rig, "Task b")
    assert terms["T.nudge"] == arbiter.START_REACH["nudge"]
    assert terms["p"] == round(arbiter.p_of(STRONG, arbiter.START_WEIGHTS, "tasks_due"), 4)
    assert rig.store.get_json(SCOPE, "learned") == stored

    # On again, with a snapshot that does not read back: the start values.
    rig.values["decision.learning"] = True
    rig.store.set_json(SCOPE, "learned", {**stored, "v": 2}, WALL)
    seen.clear()
    rig.observation = observation(task("Task c"))
    await rig.engine.recheck()
    assert seen and all(numbers == learning.inputs(None) for numbers in seen)


async def test_beat_paces_by_attention(rig: Rig) -> None:
    """λ moves by the attention the window's cycles newly asked of the user, `ALPHA` of each
    level newly shown: the overdue task's alert counts 3 and the badge the hourly cap held the
    other task at counts 0.1, where counting the nudges and alerts shown gave 1."""
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.values["decision.alerts"] = True
    rig.values["decision.nudges_per_hour"] = 1
    rig.observation = observation(task("Task a", TODAY - timedelta(days=1)), task("Task b"))
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)
    found = [rig.agenda.item(SCOPE, key_of(text)) for text in ("Task a", "Task b")]
    assert [None if item is None else item.level for item in found] == ["alert", "badge"]
    assert rig.engine.nudge == 1

    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S)
    await rig.parked(seen)
    assert rig.store.get_json(SCOPE, "lambda") == pytest.approx(
        arbiter.pace(arbiter.START_LAMBDA, ALPHA["alert"] + ALPHA["badge"], 1)
    )


async def test_beat_relearns_under_the_lock(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each beat re-fits the snapshot of every scope that ran a cycle in its window, from the
    scope's decisions since `learn_since`, under the cycle lock and halting no run, so work
    between cycles waits for it. With learning off the beat paces λ and fits nothing."""
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.values["decision.cadence"] = "15m"  # between the beats, only the wakes below run cycles
    rig.observation = observation(task("Task a"))
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)

    def opened(text: str) -> None:  # the user opens the item: its decision is labelled 1
        found = rig.agenda.item(SCOPE, key_of(text))
        assert found is not None
        rig.store.observe(
            SCOPE, event="opened", episode_ids=[found.episode_id], now=rig.clock.wall()
        )

    async def cycle() -> None:
        seen = len(rig.clock.waits)
        rig.engine.wake("change")
        rig.clock.advance(COALESCE_S)
        await rig.parked(seen)

    async def beat() -> None:  # the next beat: every beat so far came COALESCE_S before a wake
        seen = len(rig.clock.waits)
        rig.clock.advance(HEARTBEAT_S - COALESCE_S)
        await rig.parked(seen)

    # Task a is labelled before the scope restarts learning, Task b after: the fit reads Task b.
    opened("Task a")
    rig.clock.advance(60 - COALESCE_S)
    since = rig.clock.wall()
    rig.store.forget_learning(SCOPE, restart_at=since)
    rig.observation = observation(*tasks("a", "b"))
    await cycle()
    opened("Task b")
    rig.model.gate = asyncio.Event()  # a key test stays in flight through the beat
    test = asyncio.ensure_future(rig.engine.test_key())
    await rig.until(lambda: rig.model.in_flight == 1)
    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S - 60)
    await rig.parked(seen)
    rows = DecisionLoopStats(rig.state).learning_rows(SCOPE, since=since)
    stored = rig.store.get_json(SCOPE, "learned")
    assert stored == learning.encode(learning.relearn(rows, now=rig.clock.wall()))
    learned = learning.parse(stored)
    assert learned is not None and (learned.fitted_at, learned.labels) == (rig.clock.wall(), 1)
    rig.model.gate.set()
    assert isinstance(await asyncio.wait_for(test, HANG_GUARD_S), ModelReply)
    assert rig.model.cancelled == 0  # not halted

    # Learning off: the beat paces λ by its window and stores no snapshot.
    rig.values["decision.learning"] = False
    rig.store.forget_learning(SCOPE, restart_at=None)
    rig.observation = observation()  # a cycle with nothing to show: no attention
    await cycle()
    lam = arbiter.lambda_of(rig.store.get_json(SCOPE, "lambda"))
    await beat()
    assert rig.store.get_json(SCOPE, "lambda") == pytest.approx(arbiter.pace(lam, 0.0, 2))
    assert rig.store.get_json(SCOPE, "learned") is None

    # Learning on: work between cycles that comes while the beat re-fits waits for its write.
    rig.values["decision.learning"] = True
    await cycle()
    entered, release = threading.Event(), threading.Event()
    relearn = learning.relearn

    def held(rows: Sequence[LearningRow], *, now: datetime) -> Learned:
        entered.set()
        release.wait(HANG_GUARD_S)
        return relearn(rows, now=now)

    monkeypatch.setattr(learning, "relearn", held)
    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S - COALESCE_S)
    try:
        assert await asyncio.to_thread(entered.wait, HANG_GUARD_S)
        work = asyncio.ensure_future(
            rig.engine.between_cycles(lambda: rig.store.get_json(SCOPE, "learned"))
        )
        await asyncio.wait((work,), timeout=SETTLE_S)
        assert not work.done()
    finally:
        release.set()
    learned = learning.parse(await asyncio.wait_for(work, HANG_GUARD_S))
    assert learned is not None and learned.fitted_at == rig.clock.wall()
    await rig.parked(seen)


async def test_a_failed_relearn_keeps_the_old_snapshot(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A scope whose rows the beat cannot read keeps its old snapshot, logged by the code
    alone; the beat still paces its λ and re-fits the window's other scopes."""
    rig.values["decision.cadence"] = "15m"
    rig.observation = observation(task("Task a"))
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)
    rig.observation = replace(observation(task("Task b")), scope=OTHER_SCOPE)
    seen = len(rig.clock.waits)
    rig.engine.wake("change")
    rig.clock.advance(COALESCE_S)
    await rig.parked(seen)  # both scopes ran a cycle in the window, newly showing no level
    old = learning.encode(snapshot())
    rig.store.set_json(SCOPE, "learned", old, WALL)
    learn_since = rig.store.learn_since

    def flaky(scope: str) -> datetime | None:
        if scope == SCOPE:
            raise DecisionLoopError("unavailable")
        return learn_since(scope)

    monkeypatch.setattr(rig.store, "learn_since", flaky)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S - COALESCE_S)
    await rig.parked(seen)
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert messages == ["decision rows not read: unavailable"]
    assert rig.store.get_json(SCOPE, "learned") == old
    learned = learning.parse(rig.store.get_json(OTHER_SCOPE, "learned"))
    assert learned is not None and learned.fitted_at == rig.clock.wall()
    for scope in (SCOPE, OTHER_SCOPE):
        assert rig.store.get_json(scope, "lambda") == pytest.approx(
            arbiter.pace(arbiter.START_LAMBDA, 0.0, 2)
        )


async def test_a_reset_while_the_beat_paces_is_not_undone(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Reset (`forget_learning` through `between_cycles`) that comes after the beat read λ and
    before it wrote the paced value waits for the beat, so that write cannot bring back the λ the
    Reset forgets, nor the snapshot."""
    rig.values["decision.cadence"] = "15m"
    rig.observation = observation(task("Task a"))
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)  # one cycle: the scope is in the beat's window
    rig.store.set_json(SCOPE, "lambda", 2.0, WALL)
    entered, release = threading.Event(), threading.Event()
    pace = arbiter.pace

    def held(lam: float, spent: float, per_hour: float) -> float:
        entered.set()  # λ is read; its write waits
        release.wait(HANG_GUARD_S)
        return pace(lam, spent, per_hour)

    monkeypatch.setattr(arbiter, "pace", held)
    seen = len(rig.clock.waits)
    rig.clock.advance(HEARTBEAT_S)
    restart = rig.clock.wall()
    try:
        assert await asyncio.to_thread(entered.wait, HANG_GUARD_S)
        reset = asyncio.ensure_future(
            rig.engine.between_cycles(lambda: rig.store.forget_learning(SCOPE, restart_at=restart))
        )
        await asyncio.wait((reset,), timeout=SETTLE_S)
        assert not reset.done()  # the Reset waits for the beat
    finally:
        release.set()
    await asyncio.wait_for(reset, HANG_GUARD_S)
    await rig.parked(seen)
    assert rig.store.get_json(SCOPE, "lambda") is None
    assert rig.store.get_json(SCOPE, "learned") is None
    assert rig.store.learn_since(SCOPE) == restart


# ---- the shadow ----------------------------------------------------------------------------------


def asked(model: FakeModel) -> list[tuple[str, str]]:
    """(model id, candidate title) of each request, in the order asked."""
    return [
        (request.model, title) for request, title in zip(model.requests, titles(model), strict=True)
    ]


async def test_shadow_requests_share_the_limits_and_cap(rig: Rig) -> None:
    """With the shadow on another version, each fresh answer's shadow request passes the live
    requests' daily cap, minute window and in-flight slots; one that fails is not retried and
    leaves no failure memory behind, even through a halt."""
    rig.keys.save(KEY)
    rig.values["decision.shadow"] = "latest"
    pinned, latest = MODEL_IDS["pinned"], MODEL_IDS["latest"]

    # The cap: the shadow request's measured spend counts toward the day as a live one's does.
    rig.values["decision.daily_cap_usd"] = 0.1  # 100,000 micro-USD
    rig.model.input_tokens = 1_000_000  # each answer measures 42,000 micro-USD
    rig.observation = observation(*tasks("a", "b", "c"))
    await rig.engine.recheck()
    assert asked(rig.model) == [(pinned, "Task a"), (latest, "Task a"), (pinned, "Task b")]
    assert rig.store.spent_on(DAY) == 126_000 and await rig.status() == "capped"
    episodes = {row["item_key"]: row["answers"] for row in rig.rows("decision_episodes")}
    assert decode_shadow(episodes[key_of("Task a")]) is not None
    assert decode_shadow(episodes[key_of("Task b")]) is None

    # The minute window: live and shadow requests alternate, ten a minute in all.
    rig.values["decision.daily_cap_usd"] = 2
    rig.model.input_tokens = None
    rig.observation = observation(*tasks("d", "e", "f", "g", "h", "i"))
    rig.clock.advance(WINDOW_S)
    start = len(rig.model.requests)
    await rig.engine.recheck()
    assert asked(rig.model)[start:] == [
        (model, f"Task {name}") for name in "defgh" for model in (pinned, latest)
    ]
    held = rig.agenda.item(SCOPE, key_of("Task i"))
    assert held is not None and held.answers_json is None

    # A refused shadow request is not asked again and marks nothing, though a halt follows it.
    rig.observation = observation(*tasks("j", "k"))
    rig.clock.advance(WINDOW_S)
    start = len(rig.model.requests)
    rig.model.script({}, ModelError("refused"))  # Task j's live answer, then its shadow's refusal
    rig.model.hold_at = start + 3  # Task k's live request never answers
    recheck = asyncio.ensure_future(rig.engine.recheck())
    await rig.until(lambda: rig.model.in_flight == 1 and rig.clock.pending == 1)
    rig.clock.advance(RECHECK_S)
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    assert asked(rig.model)[start:] == [(pinned, "Task j"), (latest, "Task j"), (pinned, "Task k")]
    assert rig.engine._failed[SCOPE] == {}
    rig.model.hold_at, rig.model.gate = None, None
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert asked(rig.model)[start + 3 :] == [(pinned, "Task k"), (latest, "Task k")]

    # The in-flight slots: a shadow request in flight holds one, so a third request waits.
    rig.observation = observation(*tasks("j", "k", "l"))
    rig.clock.advance(WINDOW_S)
    start = len(rig.model.requests)
    rig.model.hold_at = start + 2  # Task l's shadow request, and every one after it, waits
    recheck = asyncio.ensure_future(rig.engine.recheck())
    await rig.until(lambda: rig.model.in_flight == 1 and len(rig.model.requests) == start + 2)
    keys = [asyncio.ensure_future(rig.engine.test_key()) for _ in range(2)]
    await rig.until(lambda: rig.model.in_flight == MAX_IN_FLIGHT and rig.clock.pending == 2)
    assert asked(rig.model)[start:] == [
        (pinned, "Task l"),
        (latest, "Task l"),
        (pinned, "Reply to a short message"),  # the first key test's
    ]
    rig.model.held.set()
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    for test in keys:
        assert isinstance(await asyncio.wait_for(test, HANG_GUARD_S), ModelReply)
    assert rig.model.most_in_flight == MAX_IN_FLIGHT


# ---- the wording in use and an open proposal's trial -----------------------------


def worded(question: str) -> dict[str, object]:
    """A wording of goal_link that `playbook.check_question` accepts."""
    return {
        "type": "noul",
        "instructions": {"question": question, "focus": playbook.OUTSIDE_SENTENCE},
        "criteria": {"true": "Moves a listed goal", "false": "Touches no listed goal"},
    }


IN_USE = worded("Does `candidate` serve one of `owner.goals`?")
PROPOSED = worded("Does `candidate` move one of `owner.goals` forward or hold it back?")


def tried(model: FakeModel) -> list[tuple[str, str]]:
    """("trial" or "live", candidate title) of each request, in the order asked: a trial request
    asks goal_link alone, in the proposed words."""
    return [
        ("trial" if request.questions == {"goal_link": PROPOSED} else "live", title)
        for request, title in zip(model.requests, titles(model), strict=True)
    ]


async def test_cycles_ask_the_wording_in_use_and_try_only_an_open_proposal(rig: Rig) -> None:
    """Each cycle reads the scope's proposals: with none, the playbook's own questions and no
    trial; an open one is tried once per fresh answer; rejected, nothing is tried; approved, it
    is the wording the requests ask, under its own version, and nothing is tried."""
    rig.keys.save(KEY)
    rig.observation = observation(task("Task a"))
    await rig.engine.recheck()
    (first,) = rig.model.requests
    assert first.questions == playbook.questions_for("tasks_due", {})

    proposals = rig.engine.proposals
    opened = proposals.create(SCOPE, "goal_link", PROPOSED, WALL)
    rig.observation = observation(task("Task b"))
    await rig.engine.recheck()
    assert tried(rig.model)[1:] == [("live", "Task b"), ("trial", "Task b")]

    proposals.act(SCOPE, opened.proposal_id, "reject", opened.revision, None, WALL)
    rig.observation = observation(task("Task c"))
    await rig.engine.recheck()
    assert tried(rig.model)[3:] == [("live", "Task c")]

    later = WALL + timedelta(seconds=1)
    again = proposals.create(SCOPE, "goal_link", PROPOSED, later)
    proposals.act(SCOPE, again.proposal_id, "approve", again.revision, None, later)
    await rig.engine.recheck()  # Task c anew: the version names its state
    (asked,) = rig.model.requests[4:]
    assert asked.questions == playbook.questions_for("tasks_due", {"goal_link": PROPOSED})
    version = playbook.version({"goal_link": PROPOSED})
    assert version != playbook.PLAYBOOK_VERSION
    episodes = [row for row in rig.rows("decision_episodes") if row["item_key"] == key_of("Task c")]
    assert {row["policy_version"] for row in episodes} == {
        f"playbook-{PLAYBOOK}",
        f"playbook-{version}",
    }
    calls = [rig.agenda.call(SCOPE, entry.call_id) for entry in rig.agenda.calls(SCOPE)]
    assert sorted(call.body.playbook_version for call in calls) == [PLAYBOOK] * 3 + [version]


async def test_an_unreadable_wording_falls_back_to_the_playbooks_own(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When the scope's proposals cannot be read, a cycle asks the playbook's own questions
    under its own version and tries nothing; a beat cannot read them either. Each logs the code
    alone."""
    rig.keys.save(KEY)
    proposals = rig.engine.proposals
    approved = proposals.create(SCOPE, "goal_link", IN_USE, WALL)
    proposals.act(SCOPE, approved.proposal_id, "approve", approved.revision, None, WALL)
    proposals.create(SCOPE, "goal_link", PROPOSED, WALL)  # open: on trial

    def unreadable(scope: str) -> object:
        raise DecisionLoopError("unavailable")

    monkeypatch.setattr(proposals, "wording", unreadable)
    rig.observation = observation(task("Task a"))
    caplog.set_level(logging.WARNING, logger=LOGGER)
    await rig.engine.recheck()
    (request,) = rig.model.requests
    assert request.questions == playbook.questions_for("tasks_due", {})
    found = rig.agenda.item(SCOPE, key_of("Task a"))
    assert found is not None and found.answers_json is not None
    (episode,) = rig.rows("decision_episodes")
    assert episode["policy_version"] == f"playbook-{PLAYBOOK}"
    (entry,) = rig.agenda.calls(SCOPE)
    assert rig.agenda.call(SCOPE, entry.call_id).body.playbook_version == PLAYBOOK

    await rig.engine._beat()
    ours = [record for record in caplog.records if record.name == LOGGER]
    messages = [record.getMessage() for record in ours]
    assert messages == ["decision wording not read: unavailable"] * 2
    assert all(record.exc_info is None for record in ours)


async def test_trial_shares_the_live_limits(rig: Rig) -> None:
    """With a proposal open, each fresh answer's trial request passes the live requests' daily
    cap, minute window, cooling and in-flight slots; it counts as a shadow call and leaves no
    call record; one that fails is not retried and leaves no failure memory behind, even
    through a halt."""
    rig.keys.save(KEY)
    rig.engine.proposals.create(SCOPE, "goal_link", PROPOSED, WALL)

    # The cap: the trial request's measured spend counts toward the day as a live one's does.
    rig.values["decision.daily_cap_usd"] = 0.1  # 100,000 micro-USD
    rig.model.input_tokens = 1_000_000  # each answer measures 42,000 micro-USD
    rig.observation = observation(*tasks("a", "b", "c"))
    await rig.engine.recheck()
    assert tried(rig.model) == [("live", "Task a"), ("trial", "Task a"), ("live", "Task b")]
    assert rig.store.spent_on(DAY) == 126_000 and await rig.status() == "capped"
    assert rig.store.usage_on(DAY) == DayUsage(3, 1, 3_000_000, 0)  # one shadow call
    assert len(rig.agenda.calls(SCOPE)) == 2  # the live requests only
    episodes = {row["item_key"]: row["answers"] for row in rig.rows("decision_episodes")}
    assert decode_trial(episodes[key_of("Task a")]) is not None
    assert decode_trial(episodes[key_of("Task b")]) is None

    # The minute window: live and trial requests alternate, ten a minute in all.
    rig.values["decision.daily_cap_usd"] = 2
    rig.model.input_tokens = None
    rig.observation = observation(*tasks("d", "e", "f", "g", "h", "i"))
    rig.clock.advance(WINDOW_S)
    start = len(rig.model.requests)
    await rig.engine.recheck()
    assert tried(rig.model)[start:] == [
        (what, f"Task {name}") for name in "defgh" for what in ("live", "trial")
    ]
    held = rig.agenda.item(SCOPE, key_of("Task i"))
    assert held is not None and held.answers_json is None

    # A refused trial request is not asked again and marks nothing, though a halt follows it.
    rig.observation = observation(*tasks("j", "k"))
    rig.clock.advance(WINDOW_S)
    start = len(rig.model.requests)
    rig.model.script({}, ModelError("refused"))  # Task j's live answer, then its trial's refusal
    rig.model.hold_at = start + 3  # Task k's live request never answers
    recheck = asyncio.ensure_future(rig.engine.recheck())
    await rig.until(lambda: rig.model.in_flight == 1 and rig.clock.pending == 1)
    rig.clock.advance(RECHECK_S)
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    assert tried(rig.model)[start:] == [("live", "Task j"), ("trial", "Task j"), ("live", "Task k")]
    assert rig.engine._failed[SCOPE] == {}
    rig.model.hold_at, rig.model.gate = None, None
    rig.clock.advance(WINDOW_S)
    await rig.engine.recheck()
    assert tried(rig.model)[start + 3 :] == [("live", "Task k"), ("trial", "Task k")]

    # Cooling: a rate limit on a trial request holds back every item's next request.
    rig.observation = observation(*tasks("m", "n"))
    rig.clock.advance(WINDOW_S)
    start = len(rig.model.requests)
    rig.model.script({}, ModelError("rate_limited", 30.0))  # Task m's live answer, then its trial's
    await rig.engine.recheck()
    assert tried(rig.model)[start:] == [("live", "Task m"), ("trial", "Task m")]
    assert await rig.status() == "rules"
    rig.clock.advance(30.0)
    await rig.engine.recheck()
    assert tried(rig.model)[start + 2 :] == [("live", "Task n"), ("trial", "Task n")]

    # The in-flight slots: a trial request in flight holds one, so a third request waits.
    rig.observation = observation(*tasks("m", "n", "o"))
    rig.clock.advance(WINDOW_S)
    start = len(rig.model.requests)
    rig.model.hold_at = start + 2  # Task o's trial request, and every one after it, waits
    recheck = asyncio.ensure_future(rig.engine.recheck())
    await rig.until(lambda: rig.model.in_flight == 1 and len(rig.model.requests) == start + 2)
    keys = [asyncio.ensure_future(rig.engine.test_key()) for _ in range(2)]
    await rig.until(lambda: rig.model.in_flight == MAX_IN_FLIGHT and rig.clock.pending == 2)
    assert tried(rig.model)[start:] == [
        ("live", "Task o"),
        ("trial", "Task o"),
        ("live", "Reply to a short message"),  # the first key test's
    ]
    rig.model.held.set()
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    for test in keys:
        assert isinstance(await asyncio.wait_for(test, HANG_GUARD_S), ModelReply)
    assert rig.model.most_in_flight == MAX_IN_FLIGHT


# ---- the `owner` part ----------------------------------------------------------------------


async def test_owner_filled(rig: Rig) -> None:
    """A shown item's own title comes back as the `owner` part's `recent` on a later cycle, read
    through the agenda's current item -- never an excerpt; with nothing shown yet, `recent`
    starts empty."""
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.observation = observation(task("Task a"))
    await rig.engine.recheck()
    before = rig.model.requests[-1].state.get("owner")
    assert isinstance(before, dict) and before["recent"] == []
    found = rig.agenda.item(SCOPE, key_of("Task a"))
    assert found is not None and found.level != "silent"

    rig.observation = observation(task("Task b"))
    await rig.engine.recheck()
    after = rig.model.requests[-1].state.get("owner")
    assert isinstance(after, dict) and found.body.title in after["recent"]


async def test_unreadable_owner_rows_fall_back_to_goals_only(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When the scope's rows cannot be read, a cycle's `owner` part keeps only the goals
    `owner_for` gave it -- empty here -- rather than raise; the code alone is logged."""
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.observation = observation(task("Task a"))
    await rig.engine.recheck()

    def unreadable(scope: str, since: datetime) -> object:
        raise DecisionLoopError("unavailable")

    monkeypatch.setattr(rig.engine._stats, "owner_rows", unreadable)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    rig.observation = observation(task("Task b"))
    await rig.engine.recheck()
    after = rig.model.requests[-1].state.get("owner")
    assert isinstance(after, dict) and after["recent"] == []
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert messages == ["decision owner not filled: unavailable"]


# ---- self-improvement items ---------------------------------------------------------

FIVE = tuple(f"Task {name}" for name in "abcde")  # five Not useful labels call for a retro


def items_of(rig: Rig, candidate: str) -> list[dict[str, Any]]:
    """The stored items of one candidate type."""
    return [row for row in rig.rows("decision_items") if row["candidate"] == candidate]


def mark_wrong(rig: Rig, *texts: str) -> None:
    """The user marks each task's decision Not useful."""
    for text in texts:
        found = rig.agenda.item(SCOPE, key_of(text))
        assert found is not None
        rig.store.feedback(
            SCOPE,
            episode_id=found.episode_id,
            value="wrong",
            reason=None,
            source="launcher",
            now=rig.clock.wall(),
        )


def decided_types(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The candidate type of each decision the arbiter makes from now on."""
    decided: list[str] = []
    decide = arbiter.decide

    def spy(inp: arbiter.ArbiterInput) -> Outcome:
        decided.append(inp.candidate.type)
        return decide(inp)

    monkeypatch.setattr(arbiter, "decide", spy)
    return decided


def decided(monkeypatch: pytest.MonkeyPatch) -> list[Candidate]:
    """Each candidate the arbiter decides from now on."""
    found: list[Candidate] = []
    decide = arbiter.decide

    def spy(inp: arbiter.ArbiterInput) -> Outcome:
        found.append(inp.candidate)
        return decide(inp)

    monkeypatch.setattr(arbiter, "decide", spy)
    return found


def quoted(seen: Sequence[Candidate]) -> list[str]:
    """The wording each question_tune among `seen` quotes (`facts["wording_json"]`), once each."""
    return sorted(
        {str(found.facts["wording_json"]) for found in seen if found.type == "question_tune"}
    )


async def labelled_five(rig: Rig) -> None:
    """The model answers five tasks' decisions and the user marks each Not useful: a retro is
    due, and so is tuning goal_link, the question the model was surest of on those calls."""
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.observation = observation(*(task(text) for text in FIVE))
    await rig.engine.recheck()
    mark_wrong(rig, *FIVE)


async def test_improve_candidates_come_from_the_beat(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The beat reads each scope's decisions once, for its learned snapshot and its
    self-improvement candidates alike, and keeps those candidates: a vault scope's cycles decide
    them with the rest until the next beat, a folder's do not. Acting on a retro clears it at
    the next beat."""
    rig.observation = observation(*(task(text) for text in FIVE))
    await rig.engine.recheck()
    mark_wrong(rig, *FIVE)
    decided = decided_types(monkeypatch)
    reads: list[str] = []
    read = rig.engine._stats.learning_rows

    def counted(
        scope: str, *, since: datetime | None, limit: int = MAX_LEARN_ROWS
    ) -> list[LearningRow]:
        reads.append(scope)
        return read(scope, since=since, limit=limit)

    monkeypatch.setattr(rig.engine._stats, "learning_rows", counted)
    await rig.engine.recheck()
    assert "retro_due" not in decided and items_of(rig, "retro_due") == []  # no beat yet

    await rig.engine._beat()
    assert reads == [SCOPE]  # one read, for the snapshot and the candidates
    rows = DecisionLoopStats(rig.state).learning_rows(SCOPE, since=None)
    stored = rig.store.get_json(SCOPE, "learned")
    assert stored == learning.encode(learning.relearn(rows, now=rig.clock.wall()))

    decided.clear()
    rig.observation = replace(rig.observation, kind="folder")
    await rig.engine.recheck()
    assert "tasks_due" in decided and "retro_due" not in decided
    rig.observation = replace(rig.observation, kind="vault")
    await rig.engine.recheck()
    (retro,) = items_of(rig, "retro_due")
    assert retro["state"] == "open"

    # The user acts on it. Until the next beat its candidate is still decided, and its item
    # stays closed; after that beat, the labels from before the act call for no other.
    rig.clock.advance(60)
    rig.store.observe(SCOPE, event="acted", episode_ids=[retro["episode_id"]], now=rig.clock.wall())
    rig.agenda.set_state(SCOPE, retro["item_key"], "acted", rig.clock.wall())
    decided.clear()
    await rig.engine.recheck()
    assert "retro_due" in decided
    await rig.engine._beat()
    decided.clear()
    await rig.engine.recheck()
    assert "tasks_due" in decided and "retro_due" not in decided
    assert [row["state"] for row in items_of(rig, "retro_due")] == ["acted"]


async def test_a_new_retro_shows_after_a_reset_of_learning(rig: Rig) -> None:
    """After the user acted on a retro and then reset learning, five new Not useful labels
    bring a new open retro at the next beat: its rows start at the new `learn_since`, which the
    acted one's row predates, so its key anchors on that `learn_since` rather than take the
    acted item's back, which stays closed."""
    rig.observation = observation(*(task(text) for text in FIVE))
    await rig.engine.recheck()
    mark_wrong(rig, *FIVE)
    await rig.engine._beat()
    await rig.engine.recheck()
    (retro,) = items_of(rig, "retro_due")
    assert retro["state"] == "open"
    rig.clock.advance(60)
    rig.store.observe(SCOPE, event="acted", episode_ids=[retro["episode_id"]], now=rig.clock.wall())
    rig.agenda.set_state(SCOPE, retro["item_key"], "acted", rig.clock.wall())

    rig.clock.advance(60)
    restart = rig.clock.wall()

    def reset() -> None:  # Reset learning, as the service runs it
        rig.engine.forget_improve(SCOPE)
        rig.store.forget_learning(SCOPE, restart_at=restart)

    await rig.engine.between_cycles(reset)
    rig.clock.advance(60)
    more = tuple(f"Task {name}" for name in "fghij")
    rig.observation = observation(*(task(text) for text in more))
    await rig.engine.recheck()
    mark_wrong(rig, *more)
    await rig.engine._beat()
    await rig.engine.recheck()
    states = {row["item_key"]: row["state"] for row in items_of(rig, "retro_due")}
    assert states == {
        retro["item_key"]: "acted",
        item_key("retro_due", SCOPE, stamp(restart)): "open",
    }


async def test_the_beat_keeps_no_improve_candidates_while_the_switch_is_off(rig: Rig) -> None:
    """While Self-improvement is off the beat keeps no candidates and drops those it kept, so
    turned on again it shows none before the next beat; turned off, the next cycle drops
    them."""
    rig.values["decision.watch.improve"] = False
    rig.observation = observation(*(task(text) for text in FIVE))
    await rig.engine.recheck()
    mark_wrong(rig, *FIVE)
    await rig.engine._beat()
    rig.values["decision.watch.improve"] = True
    await rig.engine.recheck()
    assert items_of(rig, "retro_due") == []

    await rig.engine._beat()
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "retro_due")] == ["open"]

    rig.values["decision.watch.improve"] = False
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "retro_due")] == ["stale"]
    await rig.engine._beat()
    rig.values["decision.watch.improve"] = True
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "retro_due")] == ["stale"]


async def test_self_improvement_alone_reads_the_rows_at_the_beat(rig: Rig) -> None:
    """With learning off and Self-improvement on, the beat still reads each scope's decisions
    for its self-improvement candidates, though it re-fits no snapshot."""
    rig.values["decision.learning"] = False
    rig.observation = observation(*(task(text) for text in FIVE))
    await rig.engine.recheck()
    mark_wrong(rig, *FIVE)
    await rig.engine._beat()
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "retro_due")] == ["open"]
    assert rig.store.get_json(SCOPE, "learned") is None


async def test_the_beat_tunes_the_wording_in_use_and_nothing_while_a_proposal_is_tried(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A beat while a proposal is tried keeps a retro but no question to tune, so none shows
    even once the trial ends, until the next beat; that one's quotes the question's wording in
    use: the playbook's own after a rejection, the approved one after an approval."""
    await labelled_five(rig)
    proposals = rig.engine.proposals
    opened = proposals.create(SCOPE, "goal_link", PROPOSED, WALL)
    seen = decided(monkeypatch)
    await rig.engine._beat()
    await rig.engine.recheck()
    assert "retro_due" in {found.type for found in seen} and quoted(seen) == []

    proposals.act(SCOPE, opened.proposal_id, "reject", opened.revision, None, WALL)
    seen.clear()
    await rig.engine.recheck()  # the candidates are still those the beat kept during the trial
    assert "retro_due" in {found.type for found in seen} and quoted(seen) == []
    await rig.engine._beat()
    seen.clear()
    await rig.engine.recheck()
    assert quoted(seen) == [canonical_json(playbook.questions()["goal_link"])]

    later = WALL + timedelta(seconds=1)
    approved = proposals.create(SCOPE, "goal_link", IN_USE, later)
    proposals.act(SCOPE, approved.proposal_id, "approve", approved.revision, None, later)
    await rig.engine._beat()
    seen.clear()
    await rig.engine.recheck()
    assert quoted(seen) == [canonical_json(IN_USE)]


async def test_a_proposal_opened_between_beats_hides_question_tune_at_once(rig: Rig) -> None:
    """A proposal opened after a beat kept a question to tune hides it from the next cycle on:
    the cycle leaves it out while its own wording read finds a proposal on trial, and keeps the
    retro the same beat kept. Rejected before the next beat, the question shows again."""
    await labelled_five(rig)
    await rig.engine._beat()
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "question_tune")] == ["open"]

    proposals = rig.engine.proposals
    opened = proposals.create(SCOPE, "goal_link", PROPOSED, WALL)
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "question_tune")] == ["stale"]
    assert [row["state"] for row in items_of(rig, "retro_due")] == ["open"]

    proposals.act(SCOPE, opened.proposal_id, "reject", opened.revision, None, WALL)
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "question_tune")] == ["open"]


async def test_question_tune_stale_wording_dropped(rig: Rig) -> None:
    """A beat's cached question_tune quotes the wording in use at that beat. Approving a
    different wording for the same question, with no trial and no new beat in between, moves
    the wording a cycle's own read now sees past what is cached, so the item is dropped rather
    than shown with stale words; the next beat catches up and shows it again, quoting the newly
    approved wording."""
    await labelled_five(rig)
    await rig.engine._beat()
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "question_tune")] == ["open"]

    proposals = rig.engine.proposals
    approved = proposals.create(SCOPE, "goal_link", IN_USE, WALL)
    proposals.act(SCOPE, approved.proposal_id, "approve", approved.revision, None, WALL)
    await rig.engine.recheck()  # the beat's cached candidate still quotes the old wording
    assert [row["state"] for row in items_of(rig, "question_tune")] == ["stale"]
    assert [row["state"] for row in items_of(rig, "retro_due")] == ["open"]

    await rig.engine._beat()
    await rig.engine.recheck()
    assert [row["state"] for row in items_of(rig, "question_tune")] == ["open"]


async def test_a_beat_that_cannot_read_the_wording_keeps_the_candidates(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A beat that cannot read the scope's proposals keeps the self-improvement candidates the
    last beat kept and still re-fits the snapshot, logging the code alone: counted anew as if no
    proposal were tried, they could offer to tune a question while one is, quoting the
    playbook's wording instead of the one in use."""
    await labelled_five(rig)
    proposals = rig.engine.proposals
    proposals.create(SCOPE, "goal_link", PROPOSED, WALL)  # open: on trial
    await rig.engine._beat()  # a retro, and no question to tune while one is tried

    def unreadable(scope: str) -> object:
        raise DecisionLoopError("unavailable")

    monkeypatch.setattr(proposals, "wording", unreadable)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    await rig.engine.recheck()  # the scope joins the next beat's window
    rig.clock.advance(60)
    await rig.engine._beat()
    learned = learning.parse(rig.store.get_json(SCOPE, "learned"))
    assert learned is not None and learned.fitted_at == rig.clock.wall()
    seen = decided(monkeypatch)
    await rig.engine.recheck()  # this cycle reads no trial either, so it would show one
    assert "retro_due" in {found.type for found in seen} and quoted(seen) == []
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert messages == ["decision wording not read: unavailable"] * 3


async def test_no_mission_for_improve_items(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Under grants that allow every work, a self-improvement item starts no mission, even when
    its candidate allowed work and its decision chose it, while a task beside it starts its own."""
    rig.keys.save(KEY)
    rig.values["decision.grant.run"] = "allowed"  # prepare is allowed by default
    rig.values["decision.watch.inbox"] = False  # the capture makes the learn_source item alone
    rig.model.answers = {**PREPARE, "worth_note": NoulAnswer(0.9)}

    def widened(
        obs: Observation,
        settings: DecisionSettings,
        missions: Sequence[MissionRecord],
        now: datetime,
    ) -> list[Candidate]:
        return [
            replace(found, work_allowed=("prepare", "run")) if found.type in IMPROVE else found
            for found in detectors.detect(obs, settings, missions, now)
        ]

    await rig.engine.stop()
    rig.engine = rig.build(rig.keys, widened)
    capture = InboxItem(
        ref="00-inbox/paper.md",
        title=None,
        captured="2026-10-20T09:00:00+09:00",
        excerpt="Read https://example.com/paper",
        tags=(),
    )
    rig.observation = observation(task("Task a"), captures=(capture,))
    starts = count_starts(rig, monkeypatch)
    await rig.engine.recheck()
    (row,) = items_of(rig, "learn_source")
    learn = rig.agenda.item(SCOPE, row["item_key"])
    assert learn is not None
    assert (learn.state, learn.timing, learn.mission_id) == ("open", "now", None)
    episodes = rig.rows("decision_episodes")
    (episode,) = [row for row in episodes if row["candidate"] == "learn_source"]
    assert (episode["kind"], episode["work"]) == ("suggest", "prepare")  # a grant allows it
    assert starts == [key_of("Task a")]
    assert [mission.item_key for mission in rig.missions.running()] == [key_of("Task a")]


# ---- vault changes: the pre-filter, filing and the cap ---------

ARCHIVE = "90-archive/plan tasks done 2026.md"
NOTE_A = "30-knowledge/alpha planning.md"
NOTE_B = "30-knowledge/beta planning.md"
OLDER = "30-knowledge/gamma plan one.md"
NEWER = "30-knowledge/gamma plan two.md"
PRIVATE = "private/payroll planning.md"
CHANGE_TITLES = frozenset({"Notes to link", "Superseded note", "Ticked tasks"})
# Every answer high, the change types' own questions too: each change item shows.
CHANGE_YES: Mapping[str, Answer] = {
    **STRONG,
    "should_link": NoulAnswer(0.9),
    "superseded": NoulAnswer(0.9),
}
OWN_QUESTION = {"note_link": "should_link", "note_superseded": "superseded"}
# What a gate, a grant, a cap or never-send reads from the store, besides the items.
AUTHORITY_TABLES = (
    "decision_preferences",
    "decision_episodes",
    "decision_missions",
    "decision_calls",
    "decision_key",
)


def note(ref: str, title: str | None = None, tags: tuple[str, ...] | None = ()) -> NoteFacts:
    """A note as `vault_watch` read it: its opening names it, and it links nothing."""
    return NoteFacts(ref, title, tags, f"The opening of {ref}.", ())


def link(source: NoteFacts, target: NoteFacts) -> LinkPair:
    """`source` names `target` on a line and does not link it."""
    return LinkPair(source, target, f"This line names {target.ref}.")


def ticked_tasks(digest: str = "d" * 64) -> TickedFile:
    """`TASKS` holding five ticked lines whose digest is `digest`."""
    return TickedFile(TASKS, 5, digest, ())


def with_changes(
    *items: TaskItem,
    links: Sequence[LinkPair] = (),
    superseded: Sequence[SupersededPair] = (),
    ticked: Sequence[TickedFile] = (),
    notes: Sequence[NoteFacts] = (),
    goals: Sequence[Goal] = (),
) -> Observation:
    """`observation(*items)` with these vault changes read: link pairs, superseded pairs, files
    of ticked tasks (each archived to `ARCHIVE`) and the notes changed lately; and these
    goals."""
    base = observation(*items, ticked=tuple(ticked))
    archive = {file.ref: ARCHIVE for file in ticked}
    changes = VaultChanges(tuple(notes), tuple(links), tuple(superseded), archive, False)
    return replace(base, goals=tuple(goals), changes=changes)


def rows_of(rig: Rig, scope: str = SCOPE) -> list[tuple[str, str, str]]:
    """(kind, state, title) of each change row of the scope, sorted."""
    return sorted(
        (str(item["change"]), str(item["state"]), str(item["title"]))
        for item in rig.engine.changes.inbox_items(scope)
    )


def state_of(rig: Rig, key: str) -> str | None:
    """The state of the scope's item under `key`; None when it has none."""
    found = rig.agenda.item(SCOPE, key)
    return None if found is None else found.state


def file_row(
    rig: Rig, scope: str, kind: str, ref: str, second: str, when: datetime = WALL
) -> ChangeRecord:
    """File a change row of `scope` outside any cycle, on `when`'s day: `second` is the other
    note of a pair, or the digest of ticked tasks archived to `ARCHIVE`."""
    ticked = kind == "tasks_ticked"
    return rig.engine.changes.file(
        scope,
        kind=kind,
        ref=ref,
        other=None if ticked else second,
        archive=ARCHIVE if ticked else None,
        item_key=item_key(kind, ref, second),
        episode_id=str(uuid.uuid4()),
        day=when.astimezone(JST).date().isoformat(),
        limit=10,
        now=when,
    )


def act_on(rig: Rig, kind: str, action: str) -> tuple[ChangeRecord, str | None]:
    """The user's `action` on the scope's one open change row of `kind`."""
    (row,) = [
        item
        for item in rig.engine.changes.inbox_items(SCOPE)
        if item["change"] == kind and item["state"] == "open"
    ]
    return rig.engine.changes.act(SCOPE, str(row["id"]), action, str(row["revision"]), now=WALL)


def authority(rig: Rig) -> tuple[object, ...]:
    """What a gate, a grant, a cap or never-send reads: the settings; every row of the
    preferences (mutes, laters, λ, the learned numbers, spend and showings), the episodes with
    their labels, the missions, the calls and the key; and each item as decided, its state
    aside."""
    items = [
        {
            name: value
            for name, value in row.items()
            if name not in ("state", "revision", "updated_at")
        }
        for row in rig.rows("decision_items")
    ]
    return (dict(rig.values), *(rig.rows(table) for table in AUTHORITY_TABLES), items)


def spy_filing(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, object]]:
    """Each run of the engine's filing from now on, as (`authority` before it, after it)."""
    runs: list[tuple[object, object]] = []
    real = rig.engine._file_changes

    def spied(*args: Any) -> None:
        before = authority(rig)
        real(*args)
        runs.append((before, authority(rig)))

    monkeypatch.setattr(rig.engine, "_file_changes", spied)
    return runs


async def test_cap_skips_asks(rig: Rig) -> None:
    """At the day's limit of change rows, counted in every workspace, a cycle drops each change
    candidate before asking about it and files nothing, while it still asks about the rest.
    Under the limit it asks and files in candidate order until the limit refuses the
    rest, which stay open as they were; the next cycle, at the limit, asks nothing about them,
    and they go stale."""
    rig.keys.save(KEY)
    rig.model.answers = dict(CHANGE_YES)
    rig.values["decision.limit.changes"] = 2
    file_row(rig, OTHER_SCOPE, "note_link", "30-knowledge/delta notes.md", NOTE_B)
    file_row(rig, OTHER_SCOPE, "tasks_ticked", "10-projects/other/tasks.md", "e" * 64)
    files = (ticked_tasks(),)
    rig.observation = with_changes(
        task("Task a"),
        links=(link(note(NOTE_A), note(NOTE_B)),),
        superseded=(SupersededPair(note(OLDER), note(NEWER)),),
        ticked=files,
    )
    keys = (
        item_key("note_link", NOTE_A, NOTE_B),
        item_key("note_superseded", OLDER, NEWER),
        item_key("tasks_ticked", TASKS, files[0].digest),
    )
    await rig.engine.recheck()
    assert set(titles(rig.model)) == {"Task a"}
    assert [state_of(rig, key) for key in keys] == [None, None, None]
    assert rig.engine.changes.today(DAY) == 2 and rows_of(rig) == []

    rig.values["decision.limit.changes"] = 3
    asked = len(rig.model.requests)
    await rig.engine.recheck()
    assert set(titles(rig.model)[asked:]) == CHANGE_TITLES
    assert [state_of(rig, key) for key in keys] == ["done", "open", "open"]
    assert rows_of(rig) == [("note_link", "open", "Link alpha planning to beta planning")]
    assert rig.engine.changes.today(DAY) == 3

    asked = len(rig.model.requests)
    await rig.engine.recheck()
    assert titles(rig.model)[asked:] == []
    assert [state_of(rig, key) for key in keys] == ["done", "stale", "stale"]
    assert rig.engine.changes.today(DAY) == 3


async def test_changes_filed_once(rig: Rig) -> None:
    """A shown change item files one open row and turns done. The row's refs come from
    the candidate -- its `related_ref`, then its second note's ref or the archive the changes
    name -- with the item's episode. A key a row holds, in any state, is not asked about or
    filed again, even once a new goal moves the item's state past its stored answers; an open
    row holds its superseded or ticked target; and a cycle files a target once."""
    rig.keys.save(KEY)
    rig.model.answers = dict(CHANGE_YES)
    rig.values["decision.limit.changes"] = 10
    links = (link(note(NOTE_A), note(NOTE_B)),)
    superseded = (SupersededPair(note(OLDER), note(NEWER)),)
    first, second = ticked_tasks("1" * 64), ticked_tasks("2" * 64)
    link_key = item_key("note_link", NOTE_A, NOTE_B)
    older_key = item_key("note_superseded", OLDER, NEWER)
    first_key = item_key("tasks_ticked", TASKS, first.digest)
    second_key = item_key("tasks_ticked", TASKS, second.digest)
    rig.observation = with_changes(links=links, superseded=superseded, ticked=(first,))
    await rig.engine.recheck()
    assert rows_of(rig) == [
        ("note_link", "open", "Link alpha planning to beta planning"),
        ("note_superseded", "open", "Mark gamma plan one as replaced by gamma plan two"),
        ("tasks_ticked", "open", "Archive ticked tasks in plan"),
    ]
    assert [state_of(rig, key) for key in (link_key, older_key, first_key)] == ["done"] * 3
    held = rig.engine.changes.held(SCOPE, DAY)
    assert held.keys == {link_key, older_key, first_key}
    assert held.open_targets == {("note_superseded", OLDER), ("tasks_ticked", TASKS)}

    # The same changes under a new goal: each item's state moves, so no stored answer spares it
    # an ask, yet each key is held, so nothing is asked about or filed.
    goals = (Goal("10-projects/plan/plan.md", "Plan", ()),)
    rig.observation = with_changes(links=links, superseded=superseded, ticked=(first,), goals=goals)
    asked = len(rig.model.requests)
    await rig.engine.recheck()
    assert titles(rig.model)[asked:] == [] and len(rows_of(rig)) == 3

    # New ticked lines in the same file: a new key, but the file's open row holds the target.
    rig.observation = with_changes(
        links=links, superseded=superseded, ticked=(second,), goals=goals
    )
    await rig.engine.recheck()
    assert titles(rig.model)[asked:] == [] and state_of(rig, second_key) is None

    # The user approves that row: its draft names the archive the changes named, its episode
    # is the item's. The target is free, so the new lines are asked about and filed.
    record, draft = act_on(rig, "tasks_ticked", "approve")
    filed = rig.agenda.item(SCOPE, first_key)
    assert filed is not None and record.episode_id == filed.episode_id
    assert (record.ref, record.other, record.archive) == (TASKS, None, ARCHIVE)
    assert draft is not None and f"`{ARCHIVE}`" in draft
    await rig.engine.recheck()
    assert set(titles(rig.model)[asked:]) == {"Ticked tasks"}
    assert state_of(rig, second_key) == "done"
    ticked_rows = [state for kind, state, _title in rows_of(rig) if kind == "tasks_ticked"]
    assert ticked_rows == ["approved", "open"]

    # Rejected or approved, a row still holds its key: neither change is asked about again.
    act_on(rig, "note_link", "reject")
    act_on(rig, "note_superseded", "approve")
    asked = len(rig.model.requests)
    await rig.engine.recheck()
    assert titles(rig.model)[asked:] == [] and len(rows_of(rig)) == 4

    # Two pairs on one older note in one cycle, both answered (a fresh minute clears the per-minute
    # request window, so neither falls back to rules for lack of an ask): the first in candidate
    # order is filed and the other stays open, answered and unfiled; the next cycle leaves it
    # unasked, to go stale.
    rig.clock.advance(WINDOW_S + 1.0, wall=False)
    zero = note("30-knowledge/gamma plan zero.md")
    pairs = (SupersededPair(zero, note(NEWER)), SupersededPair(zero, note(OLDER)))
    keys = sorted(item_key("note_superseded", zero.ref, pair.newer.ref) for pair in pairs)
    rig.observation = with_changes(superseded=pairs, goals=goals)
    asked = len(rig.model.requests)
    await rig.engine.recheck()
    # Each item is asked twice (the base questions, then a reversal confirm for its first loud
    # showing): four requests, none falling back to the rules-only verdict for lack of an ask.
    assert titles(rig.model)[asked:] == ["Superseded note"] * 4
    assert [state_of(rig, key) for key in keys] == ["done", "open"]
    second = rig.agenda.item(SCOPE, keys[1])
    assert second is not None and second.level != "silent"
    asked = len(rig.model.requests)
    await rig.engine.recheck()
    assert titles(rig.model)[asked:] == []
    assert [state_of(rig, key) for key in keys] == ["done", "stale"]
    assert len(rows_of(rig)) == 5


async def test_never_send_pair_files_nothing(rig: Rig) -> None:
    """A pair with either note under a never-send folder or tag is not asked about, filed or
    sent, and never-send holds that note's title out of the `owner` part too; a pair beside them
    is asked about and filed."""
    rig.keys.save(KEY)
    rig.model.answers = dict(CHANGE_YES)
    rig.values["decision.limit.changes"] = 10
    rig.values["decision.never_send"] = json.dumps(["private/", "#secret"])
    alpha = note(NOTE_A, "Alpha planning")
    tagged = note("30-knowledge/payroll plan one.md", "Payroll plan one", ("secret",))
    rig.observation = with_changes(
        links=(link(alpha, note(PRIVATE)), link(alpha, note(NOTE_B))),
        superseded=(SupersededPair(tagged, note(NEWER)),),
        notes=(note(PRIVATE, "Payroll planning"), alpha),
    )
    await rig.engine.recheck()
    sent = [canonical_json(request.state).lower() for request in rig.model.requests]
    assert sent and not any("payroll" in state for state in sent)
    for request in rig.model.requests:
        owner = request.state.get("owner")
        assert isinstance(owner, dict) and owner["recent"] == ["Alpha planning"]
    assert rows_of(rig) == [("note_link", "open", "Link alpha planning to beta planning")]
    for key in (
        item_key("note_link", NOTE_A, PRIVATE),
        item_key("note_superseded", tagged.ref, NEWER),
    ):
        held = rig.agenda.item(SCOPE, key)
        assert held is not None and (held.state, held.level) == ("open", "silent")


async def test_only_shown_change_items_are_filed(rig: Rig) -> None:
    """A change item the cycle keeps silent (its own answer under its floor) or holds for later
    (quiet hours hold the nudge the arbiter decided, which commits at list, timed for their end)
    files nothing and stays open; once quiet hours end, the next cycle files the held one, still
    not the silent one."""
    rig.keys.save(KEY)
    rig.model.answers = {**CHANGE_YES, "should_link": NoulAnswer(0.5)}
    rig.values["decision.limit.changes"] = 10
    rig.values["notifications.quiet_start"] = "17:00"  # WALL is 18:00 in JST
    rig.values["notifications.quiet_end"] = "19:00"
    rig.observation = with_changes(
        links=(link(note(NOTE_A), note(NOTE_B)),),
        superseded=(SupersededPair(note(OLDER), note(NEWER)),),
    )
    link_key = item_key("note_link", NOTE_A, NOTE_B)
    older_key = item_key("note_superseded", OLDER, NEWER)
    await rig.engine.recheck()
    silent, held = rig.agenda.item(SCOPE, link_key), rig.agenda.item(SCOPE, older_key)
    assert silent is not None and (silent.state, silent.level) == ("open", "silent")
    assert held is not None and (held.state, held.level, held.timing) == ("open", "list", "at")
    assert rows_of(rig) == []

    rig.clock.advance(timedelta(hours=1).total_seconds())  # 19:00 in JST: quiet hours end
    await rig.engine.recheck()
    assert [state_of(rig, key) for key in (link_key, older_key)] == ["open", "done"]
    assert rows_of(rig) == [
        ("note_superseded", "open", "Mark gamma plan one as replaced by gamma plan two")
    ]


async def test_unreadable_change_rows_read_as_at_the_cap(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When the change rows cannot be read, a cycle drops each change candidate as at the cap
    and logs the code alone; once they read again, the next cycle files."""
    files = (ticked_tasks(),)
    rig.observation = with_changes(ticked=files)  # no key: the rules show ticked tasks
    key = item_key("tasks_ticked", TASKS, files[0].digest)

    def unreadable(scope: str, day: str) -> object:
        raise DecisionLoopError("unavailable")

    monkeypatch.setattr(rig.engine.changes, "held", unreadable)
    caplog.set_level(logging.WARNING, logger=LOGGER)
    await rig.engine.recheck()
    assert state_of(rig, key) is None and rig.engine.changes.today(DAY) == 0
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert messages == ["decision changes not read: unavailable"]

    monkeypatch.undo()
    await rig.engine.recheck()
    assert state_of(rig, key) == "done"
    assert rows_of(rig) == [("tasks_ticked", "open", "Archive ticked tasks in plan")]


def fill_scope(rig: Rig) -> None:
    """`MAX_CHANGES` open change rows in the scope, one a day before today's, none closed: no
    filing can make room in it today."""
    for n in range(MAX_CHANGES):
        older = f"30-knowledge/older note {n:02d}.md"
        file_row(rig, SCOPE, "note_link", older, NOTE_B, WALL - timedelta(days=1 + n))


async def test_a_full_scope_asks_about_no_change(rig: Rig) -> None:
    """Once the scope holds `MAX_CHANGES` change rows and none was closed before the day,
    `file` cannot make room, so a cycle drops each change candidate before asking about it,
    though the day's limit is far off and the state moves every cycle; nothing is filed."""
    rig.keys.save(KEY)
    rig.model.answers = dict(CHANGE_YES)
    rig.values["decision.limit.changes"] = 3
    fill_scope(rig)
    assert rig.engine.changes.today(DAY) == 0
    alpha, gamma = note(NOTE_A), note("30-knowledge/gamma planning.md")
    for n in range(3):
        goals = (Goal(f"10-projects/plan{n}/plan.md", f"Plan {n}", ()),)
        rig.observation = with_changes(task("Task a"), links=(link(alpha, gamma),), goals=goals)
        await rig.engine.recheck()
    assert titles(rig.model) and "Notes to link" not in titles(rig.model)
    assert state_of(rig, item_key("note_link", NOTE_A, gamma.ref)) is None
    assert len(rows_of(rig)) == MAX_CHANGES and rig.engine.changes.today(DAY) == 0


def pending(found: VaultChanges, runs: list[Held | None]) -> Callable[[Held | None], VaultChanges]:
    """A vault-change read left to run, as `gather` leaves one: it notes what each run holds
    back and answers `found`."""

    def read(held: Held | None) -> VaultChanges:
        runs.append(held)
        return found

    return read


async def test_the_change_rows_are_read_once_before_the_vault_changes(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While vault changes are detected, a cycle reads what the change rows hold back on its
    own day once, then runs the vault-change read the gather left with it, so that read skips
    what they hold; the pre-filter reuses that one read. While vault changes are not detected, no
    change row is read and the read holds back the no-room `_UNREAD`, so it stops after the notes
    changed lately."""
    rig.keys.save(KEY)
    rig.model.answers = dict(CHANGE_YES)
    rig.values["decision.limit.changes"] = 10
    held_key = file_row(rig, SCOPE, "note_link", NOTE_A, NOTE_B).item_key
    reads: list[tuple[str, str]] = []
    real = rig.engine.changes.held

    def held(scope: str, day: str) -> Held:
        reads.append((scope, day))
        return real(scope, day)

    monkeypatch.setattr(rig.engine.changes, "held", held)
    runs: list[Held | None] = []
    found = with_changes(links=(link(note(NOTE_A), note("30-knowledge/gamma planning.md")),))
    assert found.changes is not None
    rig.observation = replace(observation(), read_changes=pending(found.changes, runs))
    await rig.engine.recheck()
    assert reads == [(SCOPE, DAY)]
    assert [run.keys if run is not None else None for run in runs] == [frozenset({held_key})]
    assert rows_of(rig) == [
        ("note_link", "open", "Link alpha planning to beta planning"),
        ("note_link", "open", "Link alpha planning to gamma planning"),
    ]

    rig.values["decision.watch.improve"] = False
    await rig.engine.recheck()
    assert reads == [(SCOPE, DAY)] and runs[1:] == [_UNREAD]


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision.watch.improve": False},
        {"decision.limit.changes": 0},
        {"decision.watch.vault_changes": False, "decision.watch.tasks": False},
    ],
    ids=["improve_off", "limit_zero", "both_watches_off"],
)
async def test_the_change_read_stops_after_changed_notes_when_not_detected(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, overrides: Mapping[str, object]
) -> None:
    """While `detectors.detects_changes` reads false -- Self-improvement off, the day's
    limit at 0, or both vault-change watches off -- a cycle never reads what the change rows hold
    back (`changes.held` is not called) and hands the vault-change read the no-room `_UNREAD`
    Held in place of None, so `vault_watch.read_changes` stops after the notes changed lately and
    spends no archive, link or superseded read."""
    rig.keys.save(KEY)
    rig.model.answers = dict(CHANGE_YES)
    rig.values["decision.limit.changes"] = 10
    rig.values.update(overrides)
    reads: list[tuple[str, str]] = []
    real = rig.engine.changes.held

    def held(scope: str, day: str) -> Held:
        reads.append((scope, day))
        return real(scope, day)

    monkeypatch.setattr(rig.engine.changes, "held", held)
    runs: list[Held | None] = []
    found = with_changes(links=(link(note(NOTE_A), note(NOTE_B)),))
    assert found.changes is not None
    rig.observation = replace(observation(), read_changes=pending(found.changes, runs))
    await rig.engine.recheck()
    assert reads == []
    assert runs == [_UNREAD]


@pytest.mark.parametrize(
    "policy,missions",
    [
        ("ordinary", ["run"]),
        ("protected", ["prepare"]),
        ("existing_readonly", []),
        (None, []),
    ],
)
async def test_outside_an_ordinary_vault_no_change_is_read_and_no_run_starts(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, policy: str | None, missions: list[str]
) -> None:
    """With both grants Allowed and a run answered, an ordinary vault reads its
    change rows and starts a run. Outside one no change row is read, the vault-change read
    holds back the no-room `_UNREAD` and nothing is filed; the protected vault gets a plan
    (prepare) in place of the run, and a read-only vault or an unread policy gets no chat."""
    rig.keys.save(KEY)
    rig.model.answers = dict(RUN)
    rig.values["decision.grant.prepare"] = "allowed"
    rig.values["decision.grant.run"] = "allowed"
    rig.values["decision.limit.changes"] = 10
    reads: list[tuple[str, str]] = []
    real = rig.engine.changes.held

    def held(scope: str, day: str) -> Held:
        reads.append((scope, day))
        return real(scope, day)

    monkeypatch.setattr(rig.engine.changes, "held", held)
    runs: list[Held | None] = []
    found = with_changes(links=(link(note(NOTE_A), note(NOTE_B)),))
    assert found.changes is not None
    rig.observation = replace(
        observation(task("Task a")), policy=policy, read_changes=pending(found.changes, runs)
    )
    await rig.engine.recheck()
    assert [mission.work for mission in rig.missions.running()] == missions
    if policy == "ordinary":
        assert reads == [(SCOPE, DAY)] and runs != [_UNREAD]
        return
    assert reads == [] and runs == [_UNREAD]
    assert rig.rows("decision_proposals") == []


@pytest.mark.parametrize("why", ["limit", "room", "unreadable"])
async def test_a_closed_day_reads_the_changed_notes_alone(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    """At the day's limit in every workspace, with no room in the scope, or when the
    change rows cannot be read, a cycle's vault-change read reads the notes changed lately and no
    more (at most `MAX_CHANGED` reads; none for a link, an older note or an archive), over a real
    vault: no vault change is asked about or filed, while the `owner` part keeps the notes changed
    lately and the rest is decided as ever."""
    rig.keys.save(KEY)
    rig.model.answers = dict(CHANGE_YES)
    rig.values["decision.limit.changes"] = 1
    if why == "limit":
        file_row(rig, OTHER_SCOPE, "note_link", "30-knowledge/delta notes.md", NOTE_B)
    elif why == "room":
        fill_scope(rig)
    else:

        def unreadable(scope: str, day: str) -> Held:
            raise DecisionLoopError("unavailable")

        monkeypatch.setattr(rig.engine.changes, "held", unreadable)
    vault = tmp_path / "vault"
    # Changed lately, A names B, has an alike older note beside it, and a project has ticked
    # tasks with an archive folder: an open day would read B, the older note and the archive.
    write(vault / NOTE_A, "Alpha planning names beta planning here.\n")
    for ref in (NOTE_B, "30-knowledge/alpha planning v1.md", "90-archive/placeholder.md"):
        write(vault / ref, "x\n")
        touch(vault / ref, old())
    reader = service(vault)
    reads = spy_reads(reader, monkeypatch)
    ticked = (ticked_tasks(),)
    read = functools.partial(read_changes, reader, SCOPE, reader.scan(SCOPE), TODAY, ticked)
    rig.observation = replace(observation(task("Task a"), ticked=ticked), read_changes=read)
    before = rows_of(rig)
    await rig.engine.recheck()
    assert reads == [NOTE_A]
    assert titles(rig.model) and set(titles(rig.model)) == {"Task a"}
    for request in rig.model.requests:
        owner = request.state.get("owner")
        assert isinstance(owner, dict) and owner["recent"] == ["alpha planning"]
    assert rows_of(rig) == before


async def test_a_cycle_files_its_changes_under_the_cycle_lock(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cycle files its shown changes, and sets their items done, inside its own hold of the
    cycle lock: work between cycles asked for meanwhile, such as an act on a change, waits for
    the whole filing and then finds the row and the done item together."""
    files = (ticked_tasks(),)
    rig.observation = with_changes(ticked=files)  # no key: the rules show ticked tasks
    key = item_key("tasks_ticked", TASKS, files[0].digest)
    started, release = threading.Event(), threading.Event()
    file = rig.engine.changes.file

    def held_open(scope: str, **change: Any) -> ChangeRecord:
        started.set()
        release.wait(HANG_GUARD_S)
        return file(scope, **change)

    monkeypatch.setattr(rig.engine.changes, "file", held_open)
    seen: list[tuple[list[tuple[str, str, str]], str | None]] = []

    def work() -> None:  # what an act on a change reads first
        seen.append((rows_of(rig), state_of(rig, key)))

    recheck = asyncio.ensure_future(rig.engine.recheck())
    assert await asyncio.to_thread(started.wait, HANG_GUARD_S)
    job = asyncio.ensure_future(rig.engine.between_cycles(work))
    await asyncio.wait((job,), timeout=SETTLE_S)
    assert seen == [] and not job.done()
    release.set()
    await asyncio.wait_for(job, HANG_GUARD_S)
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    assert seen == [([("tasks_ticked", "open", "Archive ticked tasks in plan")], "done")]


async def test_owner_recent_starts_with_the_notes_changed_lately(rig: Rig) -> None:
    """The `owner` part's recent titles start with the notes changed lately, in their order, each
    by its title, else its file name, never its text; then come the items shown in the last
    day."""
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    notes = (note(NOTE_A, "Alpha planning"), note(NOTE_B))
    rig.observation = with_changes(task("Task a"), notes=notes)
    await rig.engine.recheck()
    first = rig.model.requests[0].state.get("owner")
    assert isinstance(first, dict) and first["recent"] == ["Alpha planning", "beta planning"]
    shown = rig.agenda.item(SCOPE, key_of("Task a"))
    assert shown is not None and shown.level != "silent"

    rig.observation = with_changes(task("Task b"), notes=notes)
    await rig.engine.recheck()
    after = rig.model.requests[-1].state.get("owner")
    assert isinstance(after, dict)
    assert after["recent"] == ["Alpha planning", "beta planning", shown.body.title]
    assert "opening" not in canonical_json(after)


async def test_changes_change_no_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the engine, over seeded draws of change rows already filed (any kind, state,
    workspace and day), the day's limit, never-send, the grants, quiet hours, the notes changed
    lately and the own answers, a cycle files only inside the user's bounds: the day's rows in
    every workspace never pass the limit; a change at it, under a held key or on an open target
    is never decided, so nothing is asked about it; no target is filed twice; nothing about a
    note never-send holds is sent or filed; no item is filed silent, held to a later time or
    with its own answer under its floor; no change starts a mission, though the model asks for
    work; and none spends a showing of the hourly nudge cap, a launcher cue or attention for λ.
    The filing itself writes nothing a gate, a grant, a cap or never-send reads. The draws
    file, meet the limit, hold a key, hold a target alone, gate a pair, defer an item and
    refuse a shown change at least once each."""
    secret = note(PRIVATE, "Payroll planning", ("secret",))
    alpha, beta = note(NOTE_A, "Alpha planning"), note(NOTE_B, "Beta planning")
    older, newer = note(OLDER), note(NEWER)
    pool = (  # (kind, ref, second) of a change row a draw may already hold
        ("note_link", NOTE_A, NOTE_B),
        ("note_link", NOTE_A, PRIVATE),
        ("note_superseded", OLDER, NEWER),
        ("note_superseded", OLDER, "30-knowledge/gamma plan three.md"),
        ("tasks_ticked", TASKS, "1" * 64),
        ("tasks_ticked", TASKS, "2" * 64),
        ("note_link", "30-knowledge/delta notes.md", NOTE_B),
    )
    draws: Counter[str] = Counter()
    for seed in range(10):
        rng = random.Random(seed)
        folder = tmp_path / f"draw-{seed}"
        folder.mkdir()
        rig = Rig(folder)
        try:
            limit = rng.randint(1, 3)
            own = {
                name: NoulAnswer(rng.choice((0.3, 0.69, 0.7, 0.95)))
                for name in OWN_QUESTION.values()
            }
            rig.keys.save(KEY)
            rig.model.answers = {**PREPARE, **own}
            rig.values["decision.limit.changes"] = limit
            rig.values["decision.never_send"] = json.dumps([rng.choice(("private/", "#secret"))])
            rig.values["decision.grant.prepare"] = rng.choice(GRANTS)
            rig.values["decision.grant.run"] = rng.choice(GRANTS)
            quiet = rng.choice(((None, None), ("17:00", "19:00")))  # WALL is 18:00 in JST
            rig.values["notifications.quiet_start"], rig.values["notifications.quiet_end"] = quiet
            for kind, ref, second in rng.sample(pool, rng.randint(1, 4)):
                when = WALL - timedelta(days=rng.choice((0, 1, 1)))
                scope = rng.choice((SCOPE, SCOPE, OTHER_SCOPE))
                row = file_row(rig, scope, kind, ref, second, when)
                action = rng.choice(("open", "open", "approve", "reject"))
                if action != "open":
                    rig.engine.changes.act(scope, row.proposal_id, action, row.revision, now=when)
            files = (ticked_tasks(rng.choice("12") * 64),)
            rig.observation = with_changes(
                links=(link(alpha, beta), link(alpha, secret)),
                superseded=(SupersededPair(older, newer), SupersededPair(secret, newer)),
                ticked=files,
                notes=rng.sample((alpha, secret, newer), rng.randint(0, 3)),
            )
            changes = {  # each change candidate's key: (kind, ref, the other note or the archive)
                item_key("note_link", NOTE_A, NOTE_B): ("note_link", NOTE_A, NOTE_B),
                item_key("note_link", NOTE_A, PRIVATE): ("note_link", NOTE_A, PRIVATE),
                item_key("note_superseded", OLDER, NEWER): ("note_superseded", OLDER, NEWER),
                item_key("note_superseded", PRIVATE, NEWER): ("note_superseded", PRIVATE, NEWER),
                item_key("tasks_ticked", TASKS, files[0].digest): ("tasks_ticked", TASKS, ARCHIVE),
            }
            held, today = rig.engine.changes.held(SCOPE, DAY), rig.engine.changes.today(DAY)
            filings = spy_filing(rig, monkeypatch)
            await rig.engine.recheck()

            new = rig.engine.changes.held(SCOPE, DAY).keys - held.keys
            assert new <= set(changes), seed
            # The cap: never past the day's limit in every workspace, and nothing asked at it.
            assert rig.engine.changes.today(DAY) == today + len(new) <= max(today, limit), seed
            if today >= limit:
                assert CHANGE_TITLES.isdisjoint(titles(rig.model)), seed
            # A change at the limit, under a held key or on an open target is never decided.
            for key, (kind, ref, _second) in changes.items():
                if today >= limit or key in held.keys or (kind, ref) in held.open_targets:
                    assert rig.agenda.item(SCOPE, key) is None, seed
            # Held keys and open targets: none filed again, and a target once.
            targets = [changes[key][:2] for key in new if changes[key][0] != "note_link"]
            assert len(set(targets)) == len(targets), seed
            assert held.open_targets.isdisjoint(targets), seed
            # Never-send: nothing about the note it holds is sent, and no row names it.
            for request in rig.model.requests:
                assert "payroll" not in canonical_json(request.state).lower(), seed
            assert all(PRIVATE not in changes[key] for key in new), seed
            # The gates: a filed item showed, and its own answer reached its floor.
            for key in new:
                found = rig.agenda.item(SCOPE, key)
                assert found is not None and found.state == "done", seed
                assert found.level != "silent" and found.timing != "at", seed
                question = OWN_QUESTION.get(changes[key][0])
                assert question is None or own[question].p >= 0.7, seed
            # The grants: no change starts a mission.
            assert all(mission.item_key not in changes for mission in rig.missions.running()), seed
            # A change logs no showing for the hourly nudge cap, and gives the launcher no cue
            # and λ no attention.
            assert rig.store.showings_since(SCOPE, WALL - timedelta(hours=1)) == 0, seed
            attention = rig.engine._nudges.get(SCOPE, 0.0)
            assert (rig.engine.nudge, rig.engine.glow, attention) == (0, 0, 0.0), seed
            # The filing itself left all the user's bounds read as it was.
            [(before, after)] = filings
            assert before == after, seed
            under = today < limit
            decided = {
                key: found for key in changes if (found := rig.agenda.item(SCOPE, key)) is not None
            }
            draws.update(
                filed=bool(new),
                at_cap=not under,
                key=under and not held.keys.isdisjoint(changes),
                target=under
                and any(
                    key not in held.keys and changes[key][:2] in held.open_targets
                    for key in changes
                ),
                gated=any(
                    found.level == "silent" and PRIVATE not in changes[key]
                    for key, found in decided.items()
                ),
                deferred=any(found.timing == "at" for found in decided.values()),
                refused=any(
                    found.state == "open" and found.level != "silent" and found.timing != "at"
                    for found in decided.values()
                ),
            )
        finally:
            await asyncio.wait_for(rig.engine.stop(), HANG_GUARD_S)
    branches = ("filed", "at_cap", "key", "target", "gated", "deferred", "refused")
    assert all(draws[name] for name in branches), draws


# ---- a skill suggestion: nothing about it sent in any state, one item a chat -------

CHAT = "00000000-0000-4000-8000-0000000000c3"
OTHER_CHAT = "00000000-0000-4000-8000-0000000000c4"
SKILL, SKILL_ABOUT = "sprint-retro", "Facilitates a blameless sprint retrospective with the squad."
USED, USED_ABOUT = "standup-digest", "Condenses the standup thread into a digest."
SKILLS = (improve.skill_words(SKILL, SKILL_ABOUT), improve.skill_words(USED, USED_ABOUT))
CHAT_TITLE = "Payments squad sprint retro"  # picks sprint-retro: its two name words, then "squad"
OTHER_TITLE = "Squad standup digest"  # picks standup-digest while the chat has not used it
SUGGESTION = "A skill for this chat"  # skill_suggest's fixed title
# What no request may carry in any state -- the skills' names and descriptions, the
# chat's title and the name it used, whole and by the words of their own.
UNSENT = (SKILL, SKILL_ABOUT, USED, USED_ABOUT, CHAT_TITLE)
OWN_WORDS = ("sprint", "retro", "squad", "payments", "standup", "digest", "blameless")


def suggesting(
    *items: TaskItem, chat: str = CHAT, title: str = CHAT_TITLE, used: tuple[str, ...] = (USED,)
) -> Observation:
    """`observation(*items)` with the vault's own skills read (`SKILLS`) and the page reporting
    `chat` in view: a vault chat whose last turn finished, titled `title`, which used `used`."""
    page = PageFacts(
        native_waiting=0,
        runs_working=0,
        runs_review=0,
        git_state="unknown",
        git_dirty=None,
        routine_id=None,
        next_run=None,
        routines_paused=None,
        suggest=SuggestHint(chat, title, used),
    )
    return replace(observation(*items), page=page, skills=SKILLS)


def label(rig: Rig, key: str, value: str) -> None:
    """The user's `value` on the scope's item under `key`, as the service records it: "acted"
    is Use this skill, the page's observe event alone, which leaves the item open; a launcher
    label (one of `FEEDBACK_SHOWN`) labels the item's episode, then acts on the item ("useful"),
    dismisses it ("wrong", "mute") or keeps it open until `LATER` from now ("later")."""
    found = rig.agenda.item(SCOPE, key)
    assert found is not None
    now = rig.clock.wall()
    if value == "acted":
        rig.store.observe(SCOPE, event="acted", episode_ids=[found.episode_id], now=now)
        return
    rig.store.feedback(
        SCOPE, episode_id=found.episode_id, value=value, reason=None, source="launcher", now=now
    )
    if value == "later":
        rig.agenda.set_state(SCOPE, key, "open", now, defer_until=now + LATER)
    else:
        rig.agenda.set_state(SCOPE, key, _CLOSES[value], now)


@pytest.mark.parametrize("state", ["shown", "acted", *FEEDBACK_SHOWN])
async def test_a_skill_suggestion_sends_nothing_about_the_skill_or_the_chat(
    rig: Rig, state: str
) -> None:
    """A chat's skill suggestion is asked about and shown, then left shown, acted on (Use this
    skill) or labelled useful, wrong, later or mute, and a cycle with a task follows, the chat
    still in view. No request of either cycle carries a skill's name or description, the chat's
    title or the name it used, whole or by a word of their own, while the later requests' `owner`
    part carries the suggestion's fixed title alone, with its label where it has one."""
    rig.keys.save(KEY)
    rig.model.answers = dict(STRONG)
    rig.observation = suggesting()
    await rig.engine.recheck()
    key = item_key("skill_suggest", CHAT)
    shown = rig.agenda.item(SCOPE, key)
    assert shown is not None and shown.level != "silent" and shown.body.title == SUGGESTION
    asked = len(rig.model.requests)
    assert asked and set(titles(rig.model)) == {SUGGESTION}
    if state != "shown":
        label(rig, key, state)

    rig.observation = suggesting(task("Task a"))
    await rig.engine.recheck()
    assert "Task a" in titles(rig.model)[asked:]
    for request in rig.model.requests:
        sent = (canonical_json(request.state) + canonical_json(request.questions)).casefold()
        assert [text for text in (*UNSENT, *OWN_WORDS) if text.casefold() in sent] == []
    marked = [SUGGESTION] if state in ("wrong", "mute") else []
    labelled = [{"item": SUGGESTION, "shown_as": shown.level, "outcome": state}]
    for request in rig.model.requests[asked:]:
        owner = request.state.get("owner")
        assert isinstance(owner, dict) and owner["recent"] == [SUGGESTION]
        assert owner["muted_or_wrong"] == marked
        assert owner["recent_feedback"] == (labelled if state in FEEDBACK_SHOWN else [])


def suggestions(rig: Rig) -> dict[str, tuple[str, str, str | None, str | None]]:
    """Each skill suggestion item of the scope by its key: its state, its chip's label, and its
    draft's text and chat."""
    found: dict[str, tuple[str, str, str | None, str | None]] = {}
    for row in items_of(rig, "skill_suggest"):
        item = rig.agenda.item(SCOPE, row["item_key"])
        assert item is not None
        (chip,) = item.body.evidence
        (draft,) = [action for action in item.body.actions if action.type == "draft_chat"]
        found[item.item_key] = (item.state, chip.label, draft.text, draft.session_id)
    return found


async def test_one_skill_suggestion_a_chat(rig: Rig) -> None:
    """One item a chat, on the rules alone: another pick in the same chat updates that
    chat's item, while another chat gets its own; a dismissed or acted item stays closed through
    later picks."""
    first, second = item_key("skill_suggest", CHAT), item_key("skill_suggest", OTHER_CHAT)
    rig.observation = suggesting(used=())
    await rig.engine.recheck()
    assert suggestions(rig) == {first: ("open", SKILL, f"/{SKILL}", CHAT)}
    rig.observation = suggesting(title=OTHER_TITLE, used=())
    await rig.engine.recheck()
    assert suggestions(rig) == {first: ("open", USED, f"/{USED}", CHAT)}

    label(rig, first, "wrong")
    rig.observation = suggesting(used=())
    await rig.engine.recheck()
    assert suggestions(rig) == {first: ("dismissed", SKILL, f"/{SKILL}", CHAT)}

    rig.observation = suggesting(chat=OTHER_CHAT, used=())
    await rig.engine.recheck()
    label(rig, second, "useful")
    rig.observation = suggesting(chat=OTHER_CHAT, title=OTHER_TITLE, used=())
    await rig.engine.recheck()
    assert suggestions(rig) == {
        first: ("dismissed", SKILL, f"/{SKILL}", CHAT),
        second: ("acted", USED, f"/{USED}", OTHER_CHAT),
    }


async def test_a_chat_that_has_used_a_fitting_skill_gets_no_other_suggestion(rig: Rig) -> None:
    """Use this skill only observes "acted" (service.py's `_CLOSES` leaves the item
    open), so once the turn running goes stale, the same key would, without the used-skill rule
    in `pick_skill`, reopen offering the next fitting skill (`SKILL` and `USED` both score 4
    against this title, `SKILL` sorting first). The rule leaves the chat with no open item and
    names neither skill again."""
    title = "Sprint retro and standup digest"
    key = item_key("skill_suggest", CHAT)
    rig.observation = suggesting(title=title, used=())
    await rig.engine.recheck()
    assert suggestions(rig) == {key: ("open", SKILL, f"/{SKILL}", CHAT)}

    label(rig, key, "acted")  # Use this skill: observe "acted" alone
    assert suggestions(rig) == {key: ("open", SKILL, f"/{SKILL}", CHAT)}  # closes nothing

    running = suggesting(title=title, used=())
    assert running.page is not None
    rig.observation = replace(running, page=replace(running.page, suggest=None))
    await rig.engine.recheck()
    assert suggestions(rig) == {key: ("stale", SKILL, f"/{SKILL}", CHAT)}  # the turn running

    rig.observation = suggesting(title=title, used=(SKILL,))
    await rig.engine.recheck()
    assert suggestions(rig) == {key: ("stale", SKILL, f"/{SKILL}", CHAT)}  # neither reopens


# ---- Kanban cards -----------------------------------------------------------------

CARD_NAMES = tuple(f"{n:02}" for n in range(8))


def card(name: str, status: str = "blocked") -> Card:
    """A Kanban card at `kanban/<name>.md`, changed an hour before the clock's wall."""
    return Card(f"kanban/{name}.md", f"Card {name}", status, (), WALL - timedelta(hours=1))


def kanban_observation(
    *cards: Card, unread: tuple[str, ...] = (), complete: bool = True
) -> Observation:
    return replace(observation(), kanban=KanbanRead(cards, unread, complete))


def card_key(name: str) -> str:
    return item_key("card_needs_you", f"kanban/{name}.md")


def open_cards(rig: Rig) -> list[str]:
    """The names of the cards whose card_needs_you item is open."""
    return [name for name in CARD_NAMES if state_of(rig, card_key(name)) == "open"]


async def test_kanban_is_read_only_while_kanban_is_on(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With Start Bots' work Off, the gather is asked for Kanban while `decision.watch.kanban`
    is on, and the open card items are read only for a cycle that read Kanban."""
    rig.values["decision.grant.team"] = "off"
    reads: list[str] = []
    open_items = rig.agenda.open_items

    def spy(scope: str) -> tuple[list[Any], int]:
        reads.append(scope)
        return open_items(scope)

    monkeypatch.setattr(rig.agenda, "open_items", spy)
    await rig.engine.recheck()  # an observation with no Kanban read
    assert (rig.kanbans, reads) == ([True], [])

    rig.observation = kanban_observation(card("01"))
    await rig.engine.recheck()
    assert (rig.kanbans, reads, open_cards(rig)) == ([True, True], [SCOPE], ["01"])

    rig.values["decision.watch.kanban"] = False
    await rig.engine.recheck()
    assert (rig.kanbans, reads) == ([True, True, False], [SCOPE])
    assert state_of(rig, card_key("01")) == "stale"


async def test_an_unread_card_keeps_its_item_and_a_gone_card_closes_it(rig: Rig) -> None:
    blocked = card("01")
    rig.observation = kanban_observation(blocked)
    await rig.engine.recheck()  # no key: the rules list it
    listed = rig.agenda.item(SCOPE, card_key("01"))
    assert listed is not None and (listed.state, listed.level, rig.engine.glow) == (
        "open",
        "list",
        1,
    )

    rig.observation = kanban_observation(unread=(blocked.ref,))
    await rig.engine.recheck()
    assert (rig.agenda.item(SCOPE, card_key("01")), rig.engine.glow) == (listed, 1)

    rig.observation = kanban_observation(complete=False)  # absent from an incomplete listing
    await rig.engine.recheck()
    assert state_of(rig, card_key("01")) == "open"

    rig.observation = kanban_observation()  # absent from a complete one
    await rig.engine.recheck()
    assert state_of(rig, card_key("01")) == "stale"


async def test_listed_cards_keep_their_places(rig: Rig) -> None:
    blocked = [card(name) for name in CARD_NAMES[1:]]
    rig.observation = kanban_observation(*blocked)
    await rig.engine.recheck()
    assert open_cards(rig) == ["01", "02", "03", "04", "05", "06"]

    # A new card that sorts first, and 01 unread: the six listed cards keep their places.
    rig.observation = kanban_observation(card("00"), *blocked[1:], unread=(blocked[0].ref,))
    await rig.engine.recheck()
    assert open_cards(rig) == ["01", "02", "03", "04", "05", "06"]

    # 02 is done: the new card takes its place.
    rig.observation = kanban_observation(card("00"), blocked[0], card("02", "done"), *blocked[2:])
    await rig.engine.recheck()
    assert open_cards(rig) == ["00", "01", "03", "04", "05", "06"]


async def test_kanban_is_read_for_seats_while_kanban_is_off(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With Kanban off and Start Bots' work not Off, the gather still reads Kanban for the
    seats, but no card item is made and no open card item is read or kept."""
    reads: list[str] = []
    open_items = rig.agenda.open_items

    def spy(scope: str) -> tuple[list[Any], int]:
        reads.append(scope)
        return open_items(scope)

    monkeypatch.setattr(rig.agenda, "open_items", spy)
    rig.observation = kanban_observation(card("01"))
    await rig.engine.recheck()
    assert open_cards(rig) == ["01"]

    rig.values["decision.watch.kanban"] = False
    reads.clear()
    await rig.engine.recheck()
    assert (rig.kanbans, reads, open_cards(rig)) == ([True, True], [], [])
    assert state_of(rig, card_key("01")) == "stale"


# ---- seats -----------------------------------------------------------

ROUTINE = "00000000-0000-4000-8000-0000000000d1"
RUN_ID = "00000000-0000-4000-8000-0000000000e1"
SEAT_SESSION = "00000000-0000-4000-8000-0000000000f1"
OWNER_CARD = Card(
    "kanban/sources.md", "Collect sources", "todo", ("owner/research",), WALL - timedelta(hours=1)
)
CARDS_HASH = sha256_hex(
    canonical_json([[OWNER_CARD.ref, OWNER_CARD.changed.isoformat()]])
)  # what `seats.plan` hashes the one counted card to


def seat_facts(*runs: SeatRun) -> SeatFacts:
    """One enabled, tested routine for the research seat at revision 3, these runs (newest
    first), both slots free and no start today."""
    research = SeatRoutine(ROUTINE, "enabled", 3, 3, "research", False)
    newest = {runs[0].routine_id: runs[0]} if runs else {}
    return SeatFacts((research,), runs, newest, 2, 0, False, True)


def open_run() -> SeatRun:
    """The research routine's decision run started this cycle, still open."""
    return SeatRun(RUN_ID, ROUTINE, "decision", SEAT_SESSION, WALL, True, None, None, "")


def seat_starts(rig: Rig) -> list[tuple[Any, ...]]:
    return [call for call in rig.seats.calls if call[0] == "start"]


def seat_items(rig: Rig) -> dict[str, str]:
    """Each open seat item's type and kind, by type."""
    items, _ = rig.agenda.open_items(SCOPE)
    return {item.candidate: item.kind or "" for item in items if item.candidate in SEATS}


async def test_an_owners_todo_card_starts_its_seat_within_one_cycle(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.observation = kanban_observation(OWNER_CARD)
    rig.seats.seat_facts = seat_facts()
    starts = count_starts(rig, monkeypatch)

    # Off: nothing is started or offered, and nothing is working or reported.
    rig.values["decision.grant.team"] = "off"
    await rig.engine.recheck()
    assert (seat_starts(rig), seat_items(rig)) == ([], {})

    # Ask first: a Start offer, and nothing starts.
    rig.values["decision.grant.team"] = "ask"
    await rig.engine.recheck()
    assert (seat_starts(rig), seat_items(rig)) == ([], {"seat_start": "suggest"})
    offer = rig.agenda.item(SCOPE, item_key("seat_start", ROUTINE, CARDS_HASH))
    assert offer is not None
    assert [(a.type, a.routine_id) for a in offer.body.actions] == [("start_seat", ROUTINE)]

    # Allowed: one start at the planned revision, the newest the plan saw, the counted cards'
    # hash, the day's limit and the cycle's day; the facts were read from the card's change on.
    rig.values["decision.grant.team"] = "allowed"
    rig.seats.calls.clear()
    await rig.engine.recheck()
    assert seat_starts(rig) == [("start", ROUTINE, 3, None, CARDS_HASH, 4, DAY)]
    assert ("facts", SCOPE, OWNER_CARD.changed, DAY) in rig.seats.calls
    assert rig.engine._wake_due is not None  # the start woke the clock

    # The next cycle reads the run as open: no second start, and the seat is working.
    rig.seats.seat_facts = seat_facts(open_run())
    await rig.engine.recheck()
    assert len(seat_starts(rig)) == 1
    assert seat_items(rig) == {"seat_working": "inform"}
    assert starts == []


async def test_a_new_owners_card_is_offered_after_the_last_offer_was_not_useful(
    rig: Rig,
) -> None:
    """Under Ask first, the offer is keyed by its cards: after the user says Not useful on one,
    a new card of the user's for the same seat brings a new offer."""
    rig.observation = kanban_observation(OWNER_CARD)
    rig.seats.seat_facts = seat_facts()
    rig.values["decision.grant.team"] = "ask"
    await rig.engine.recheck()
    assert seat_items(rig) == {"seat_start": "suggest"}
    first = item_key("seat_start", ROUTINE, CARDS_HASH)
    rig.agenda.set_state(SCOPE, first, "dismissed", rig.clock.wall())

    newer = replace(
        OWNER_CARD, ref="kanban/other.md", changed=OWNER_CARD.changed + timedelta(minutes=30)
    )
    rig.observation = kanban_observation(OWNER_CARD, newer)
    await rig.engine.recheck()
    assert seat_items(rig) == {"seat_start": "suggest"}
    assert state_of(rig, first) == "dismissed"


@pytest.mark.parametrize(
    ("change", "started"),
    [
        ({"decision.grant.team": "off"}, []),
        ({"decision.grant.team": "ask"}, []),
        ({"decision.enabled": False}, []),
        ({"decision.paused_until": stamp(WALL + timedelta(hours=1))}, []),
        # The seat host refuses a start at the limit: the start carries the new one.
        ({"decision.limit.team": 0}, [("start", ROUTINE, 3, None, CARDS_HASH, 0, DAY)]),
    ],
    ids=["grant_off", "grant_ask", "decision_off", "paused", "limit_0"],
)
async def test_seat_starts_follow_settings_changed_during_the_cycle(
    rig: Rig, change: dict[str, object], started: list[tuple[Any, ...]]
) -> None:
    """The user changes a setting while the cycle reads the seat facts: the starts that cycle
    planned follow the change."""
    rig.observation = kanban_observation(OWNER_CARD)
    rig.seats.seat_facts = seat_facts()
    rig.values["decision.grant.team"] = "allowed"
    facts = rig.seats.facts

    def changing(workspace_id: str, since: datetime | None, day: str) -> SeatFacts:
        rig.values.update(change)
        return facts(workspace_id, since, day)

    rig.seats.facts = changing  # type: ignore[method-assign]
    await rig.engine.recheck()
    assert ("facts", SCOPE, OWNER_CARD.changed, DAY) in rig.seats.calls
    assert seat_starts(rig) == started


async def test_a_refused_seat_start_is_asked_again_on_fresh_facts(rig: Rig) -> None:
    """A refusal stores nothing and the engine remembers none: the next cycle plans again."""
    rig.observation = kanban_observation(OWNER_CARD)
    rig.seats.seat_facts = seat_facts()
    rig.seats.refuse = "busy"
    rig.values["decision.grant.team"] = "allowed"
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert len(seat_starts(rig)) == 2
    assert rig.engine._wake_due is None  # no start, no wake


async def test_a_waiting_run_raises_seat_waiting_within_seconds_of_the_wake(rig: Rig) -> None:
    """While Routines is on, even with Start Bots' work Off, a run's open card asks at the cycle
    its wake brings, `COALESCE_S` later; answering it closes the item at the next."""
    rig.values["decision.grant.team"] = "off"
    rig.seats.seat_facts = seat_facts(open_run())
    seen = len(rig.clock.waits)
    rig.engine.start()
    await rig.parked(seen)
    assert seat_items(rig) == {}

    rig.seats.seat_waiting = (SeatWaiting(ROUTINE, RUN_ID, SEAT_SESSION, False),)
    seen = len(rig.clock.waits)
    rig.engine.wake("change")
    await rig.parked(seen)
    seen = len(rig.clock.waits)
    rig.clock.advance(COALESCE_S)
    await rig.parked(seen)
    assert seat_items(rig) == {"seat_waiting": "ask"}
    waiting = rig.agenda.item(SCOPE, item_key("seat_waiting", RUN_ID))
    assert waiting is not None and waiting.body.what == "Research needs your OK to continue."
    assert [(a.type, a.session_id) for a in waiting.body.actions] == [("open_chat", SEAT_SESSION)]

    rig.seats.seat_waiting = ()
    seen = len(rig.clock.waits)
    rig.engine.wake("change")
    await rig.parked(seen)
    seen = len(rig.clock.waits)
    rig.clock.advance(COALESCE_S)
    await rig.parked(seen)
    assert (seat_items(rig), state_of(rig, waiting.item_key)) == ({}, "stale")


async def test_the_plan_brake_holds_seat_starts_and_reports_them(rig: Rig) -> None:
    rig.observation = kanban_observation(OWNER_CARD)
    rig.seats.seat_facts = seat_facts()
    rig.values["decision.grant.team"] = "allowed"
    rig.reading = plan_reading(use=0.8, reset=at_wall(3600))
    await rig.engine.recheck()
    assert seat_starts(rig) == []
    held = rig.agenda.item(SCOPE, item_key("seat_report", ROUTINE, "plan_brake", DAY))
    assert held is not None and (held.state, held.kind) == ("open", "report")
    assert held.body.what == "Research is held by your plan brake."

    rig.reading = plan_reading(use=0.79, reset=at_wall(3600))
    await rig.engine.recheck()
    assert seat_starts(rig) == [("start", ROUTINE, 3, None, CARDS_HASH, 4, DAY)]


async def test_seat_starts_are_neither_arbiter_work_nor_missions(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With every grant Allowed and the model answering prepare, a seat starts only through the
    seat host: no mission is asked for or stored, and every seat item's work is none."""
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    for grant in ("prepare", "run", "team"):
        rig.values[f"decision.grant.{grant}"] = "allowed"
    rig.observation = kanban_observation(OWNER_CARD)
    rig.seats.seat_facts = seat_facts()
    rig.seats.seat_waiting = (SeatWaiting(ROUTINE, RUN_ID, SEAT_SESSION, True),)
    starts = count_starts(rig, monkeypatch)
    await rig.engine.recheck()
    rig.seats.seat_facts = seat_facts(open_run())
    rig.seats.seat_waiting = ()
    await rig.engine.recheck()
    assert len(seat_starts(rig)) == 1
    assert (starts, rig.rows("decision_missions")) == ([], [])
    kinds = {
        row["candidate"]: row["work"]
        for row in rig.rows("decision_episodes")
        if row["candidate"] in SEATS
    }
    assert kinds == {"seat_waiting": "none", "seat_working": "none"}


# ---- the Look --------------------------------------------------------------

CHAT_SINCE = WALL - timedelta(minutes=10)  # a card raised long enough ago to list its chat
APP = AppLook(
    waiting=(WaitingChat(CHAT, "tool-1", "question", CHAT_SINCE),),
    heavy=(HeavyServer(CHAT, "search"),),
    sends=(UncertainSend("command-1", CHAT, WALL),),
    bridges=(StoppedBridge("mattermost", False, None),),
)
NO_APP = AppLook((), (), (), ())


def looking(rig: Rig, look: AppLook) -> FakeLookHost:
    """Rebuild the rig's engine with a Look host answering `look`."""
    rig.looks = FakeLookHost(look, rig.changed)
    rig.engine = rig.build(rig.keys)
    return rig.looks


def look_items(rig: Rig) -> dict[str, ItemRecord]:
    """Each open Look item, by type."""
    items, _ = rig.agenda.open_items(SCOPE)
    return {item.candidate: item for item in items if item.candidate in LOOKS}


async def test_the_vault_look_is_read_only_while_vault_health_is_on(rig: Rig) -> None:
    """While Vault health is on, the cycle runs the vault Look read the gather left, once and off
    the event loop, adds its failure words to the observation's and detects over what it saw;
    with Vault health off the read does not run and its item closes."""
    threads: list[int] = []
    seen: list[Observation] = []

    def read_look() -> VaultLook:
        threads.append(threading.get_ident())
        return VaultLook((), None, (0, 0), ("m-1",), failures=("recovery_unknown",))

    def detect(obs: Observation, *rest: Any) -> Sequence[Candidate]:
        seen.append(obs)
        return detectors.detect(obs, *rest)

    rig.engine = rig.build(rig.keys, detect)
    rig.observation = replace(observation(), failures=("goals_unknown",), read_look=read_look)
    await rig.engine.recheck()
    assert len(threads) == 1 and threads[0] != threading.get_ident()
    look = seen[0].look
    assert look is not None and look.drift == ("m-1",)
    assert (seen[0].read_look, seen[0].failures) == (None, ("goals_unknown", "recovery_unknown"))
    drift = item_key("memory_drift", SCOPE, "m-1")
    assert state_of(rig, drift) == "open"

    rig.values["decision.watch.vault_health"] = False
    await rig.engine.recheck()
    assert (len(threads), seen[1].look, seen[1].failures) == (1, None, ("goals_unknown",))
    assert state_of(rig, drift) == "stale"


@pytest.mark.parametrize(
    ("switch", "flags", "listed"),
    [
        (None, (True, True, True, True), APP_LOOKS),
        ("decision.watch.approvals", (False, True, True, True), APP_LOOKS[:1] + APP_LOOKS[2:]),
        ("decision.watch.chats", (True, False, False, True), APP_LOOKS[:2]),
        ("decision.watch.messaging", (True, True, True, False), APP_LOOKS[1:]),
    ],
    ids=["all_on", "approvals_off", "chats_off", "messaging_off"],
)
async def test_app_look_reads_follow_their_switches(
    rig: Rig, switch: str | None, flags: tuple[bool, ...], listed: tuple[str, ...]
) -> None:
    """The app Look reads waiting chats while Approvals is on, heavy MCP servers and uncertain
    sends while Chats and runs is on, and stopped bridges while Messaging is on; only what was
    read lists an item."""
    if switch is not None:
        rig.values[switch] = False
    host = looking(rig, APP)
    await rig.engine.recheck()
    assert host.reads == [flags]
    assert sorted(look_items(rig)) == sorted(listed)


async def test_a_cleared_look_closes_its_item(rig: Rig) -> None:
    """A stopped bridge and a save stopped for recovery list their items as reports; once the
    bridge runs and the row goes, the next cycle closes both."""
    host = looking(rig, replace(NO_APP, bridges=APP.bridges))
    looks = [VaultLook((SaveRow("save-1", False, True, WALL),), (), (0, 0), ())]
    rig.observation = replace(observation(), read_look=lambda: looks[-1])
    await rig.engine.recheck()
    opened = look_items(rig)
    shown = {kind: (item.kind, item.level) for kind, item in opened.items()}
    assert shown == {"vault_recovery": ("report", "list"), "bridge_stopped": ("report", "list")}

    host.look = NO_APP
    looks.append(VaultLook((), (), (0, 0), ()))
    await rig.engine.recheck()
    assert look_items(rig) == {}
    assert [state_of(rig, item.item_key) for item in opened.values()] == ["stale", "stale"]


async def test_waiting_chats_of_missions_and_waiting_runs_are_left_out(rig: Rig) -> None:
    """A mission's chat and a seat run waiting on a card already have their place: of three
    chats whose card waits, only the third lists `chat_waiting`."""
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    rig.values["decision.grant.prepare"] = "allowed"
    rig.observation = observation(task("Task a"))
    host = looking(rig, NO_APP)
    await rig.engine.recheck()
    (mission,) = rig.missions.running()

    rig.seats.seat_waiting = (SeatWaiting(ROUTINE, RUN_ID, SEAT_SESSION, True),)
    chats = (mission.session_id, SEAT_SESSION, CHAT)
    host.look = replace(
        NO_APP,
        waiting=tuple(
            WaitingChat(chat, f"tool-{n}", "question", CHAT_SINCE) for n, chat in enumerate(chats)
        ),
    )
    await rig.engine.recheck()
    items, _ = rig.agenda.open_items(SCOPE)
    waiting = [item.item_key for item in items if item.candidate == "chat_waiting"]
    assert waiting == [item_key("chat_waiting", CHAT, "tool-2")]


async def test_a_halt_during_the_app_look_read_stops_the_cycle(rig: Rig) -> None:
    """A recheck past `RECHECK_S` cancels the app Look read and commits nothing."""
    host = looking(rig, APP)
    host.gate = asyncio.Event()
    rig.observation = observation(task("Task a"))
    recheck = asyncio.ensure_future(rig.engine.recheck())
    await rig.until(lambda: host.reads and rig.clock.pending == 1)
    rig.clock.advance(RECHECK_S)
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    assert host.cancelled == 1
    assert rig.rows("decision_episodes") == []


async def test_a_halt_during_the_vault_look_read_stops_the_cycle(rig: Rig) -> None:
    """A recheck past `RECHECK_S` abandons the vault Look read, which a worker thread still
    holds, and commits nothing."""
    started, release = threading.Event(), threading.Event()

    def read_look() -> VaultLook:
        started.set()
        release.wait(HANG_GUARD_S)
        return VaultLook((), None, (0, 0), ())

    rig.observation = replace(observation(task("Task a")), read_look=read_look)
    recheck = asyncio.ensure_future(rig.engine.recheck())
    try:
        async with asyncio.timeout(HANG_GUARD_S):  # a worker thread sets no `rig.changed`
            while not (started.is_set() and rig.clock.pending == 1):
                await asyncio.sleep(0)
        rig.clock.advance(RECHECK_S)
        await asyncio.wait_for(recheck, HANG_GUARD_S)
    finally:
        release.set()
    assert rig.rows("decision_episodes") == []


# ---- code looks -----------------------------------------------------

FOLDER = "00000000-0000-4000-8000-0000000000c9"
IDLE = CodeFolder(FOLDER, "garden-app", WALL - timedelta(days=2))  # its changes untouched a day
LOOK = item_key("code_look", FOLDER, IDLE.newest.isoformat())


class ThreadedCodeHost(FakeCodeHost):
    """`FakeCodeHost` that also notes the thread of each read."""

    def __init__(self, look: CodeLook) -> None:
        super().__init__(look)
        self.threads: list[int] = []

    def read(self, now: datetime) -> CodeLook:
        self.threads.append(threading.get_ident())
        return super().read(now)


def coding(rig: Rig, *folders: CodeFolder, detect: Detect = detectors.detect) -> ThreadedCodeHost:
    """Rebuild the rig's engine with a code host answering `folders`."""
    rig.code = ThreadedCodeHost(CodeLook(folders))
    rig.engine = rig.build(rig.keys, detect)
    return rig.code


async def test_a_halt_during_the_code_look_read_stops_the_cycle(rig: Rig) -> None:
    """A recheck past `RECHECK_S` abandons the code Look read, which a worker thread still
    holds, and commits nothing."""
    started, release = threading.Event(), threading.Event()

    class BlockedCodeHost(FakeCodeHost):
        def read(self, now: datetime) -> CodeLook:
            started.set()
            release.wait(HANG_GUARD_S)
            return super().read(now)

    rig.code = BlockedCodeHost(CodeLook((IDLE,)))
    rig.engine = rig.build(rig.keys)
    rig.observation = observation(task("Task a"))
    recheck = asyncio.ensure_future(rig.engine.recheck())
    try:
        async with asyncio.timeout(HANG_GUARD_S):  # a worker thread sets no `rig.changed`
            while not (started.is_set() and rig.clock.pending == 1):
                await asyncio.sleep(0)
        rig.clock.advance(RECHECK_S)
        await asyncio.wait_for(recheck, HANG_GUARD_S)
    finally:
        release.set()
    assert rig.rows("decision_episodes") == []


def count_taken(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The keys of every `MissionService.taken` read."""
    asked: list[list[str]] = []
    taken = rig.missions.taken

    def counted(item_keys: Sequence[str]) -> frozenset[str]:
        asked.append(list(item_keys))
        return taken(item_keys)

    monkeypatch.setattr(rig.missions, "taken", counted)
    return asked


@pytest.mark.parametrize(
    ("case", "reads"),
    [
        ("ordinary", True),
        ("protected", True),
        ("grant_off", False),
        ("read_only", False),
        ("unknown_policy", False),
        ("folder", False),
        ("chat_only", False),
    ],
)
async def test_no_code_read_while_the_grant_is_off_or_outside_a_usable_vault(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str, reads: bool
) -> None:
    """The code folders are read once a check, off the event loop, at the cycle's clock read, in
    an Ordinary or Protected vault while the Look grant is not Off, and the read's failure words
    join the observation's. Grant Off, a read-only vault, a policy not read, and a folder or
    chat-only launch (here even with an ordinary policy) read no folder, list no look and ask
    the missions nothing."""
    seen: list[Observation] = []

    def detect(obs: Observation, *rest: Any) -> Sequence[Candidate]:
        seen.append(obs)
        return detectors.detect(obs, *rest)

    rig.values["decision.grant.look"] = "off" if case == "grant_off" else "ask"
    if case == "protected":
        rig.observation = replace(observation(), policy="protected")
    elif case == "read_only":
        rig.observation = replace(observation(), policy="existing_readonly")
    elif case == "unknown_policy":
        rig.observation = replace(observation(), policy=None)
    elif case in ("folder", "chat_only"):
        rig.observation = replace(observation(), kind=case)
    host = coding(rig, IDLE, detect=detect)
    host.look = CodeLook((IDLE,), ("code_partial",))
    taken = count_taken(rig, monkeypatch)
    await rig.engine.recheck()
    (obs,) = seen
    if reads:
        assert host.calls == [WALL] and host.threads[0] != threading.get_ident()
        assert (obs.code, obs.failures) == (host.look, ("code_partial",))
        assert state_of(rig, LOOK) == "open" and taken == [[LOOK]]
    else:
        assert (host.calls, obs.code, obs.failures) == ([], None, ())
        assert state_of(rig, LOOK) is None and taken == []


async def test_allowed_starts_a_code_look_at_the_next_check(rig: Rig) -> None:
    """With no model key, at the check that first reads the folder: the cycle gives the look
    its work and VIKI starts it, its root recorded in the folder before plan mode is stored and
    before the chat is made, with the Look brief."""
    coding(rig, IDLE)
    await rig.engine.recheck()
    (mission,) = rig.missions.running()
    assert (mission.scope, mission.item_key, mission.work) == (SCOPE, LOOK, "prepare")
    session = mission.session_id
    assert rig.host.calls == [
        ("root", session, FOLDER),
        ("remember", session, {"new_session": True, "mode": "plan"}),
        ("create", session),
    ]
    sent = rig.host.actors[session].sent[0]
    assert sent.startswith("VIKI started this chat (Decision).\nGoal: Take a second look")
    assert "garden-app" not in sent and rig.model.requests == []


async def test_ask_first_offers_take_a_look_and_starts_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under Ask first the look lists as a suggestion offering its folder in Code, then Take a
    look, and VIKI starts nothing, whatever the prepare grant."""
    rig.values["decision.grant.look"] = "ask"
    rig.values["decision.grant.prepare"] = "allowed"
    starts = count_starts(rig, monkeypatch)
    coding(rig, IDLE)
    await rig.engine.recheck()
    await rig.engine.recheck()
    found = rig.agenda.item(SCOPE, LOOK)
    assert found is not None
    assert (found.state, found.kind, found.level) == ("open", "suggest", "list")
    actions = [(action.type, action.label) for action in found.body.actions]
    assert actions == [("open_code", "Open in Code"), ("start_prepare", "Take a look")]
    assert found.body.actions[0].workspace_id == FOLDER
    assert starts == [] and rig.host.calls == [] and rig.rows("decision_missions") == []


@pytest.mark.parametrize("hold", ["quiet", "brake"])
async def test_quiet_hours_and_the_brake_hold_a_look_until_they_end(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, hold: str
) -> None:
    """Quiet hours and the plan brake hold VIKI's own look: nothing is asked of the missions,
    stored or remembered, and the look starts at the first check after the hold ends."""
    starts = count_starts(rig, monkeypatch)
    coding(rig, IDLE)
    if hold == "quiet":  # WALL is 18:00 in JST
        rig.values["notifications.quiet_start"] = "17:00"
        rig.values["notifications.quiet_end"] = "19:00"
    else:
        rig.reading = plan_reading(use=0.8, reset=at_wall(3600))
    await rig.engine.recheck()
    await rig.engine.recheck()
    held = rig.agenda.item(SCOPE, LOOK)
    assert held is not None and (held.state, held.mission_id) == ("open", None)
    assert starts == [] and rig.host.calls == [] and rig.rows("decision_missions") == []

    if hold == "quiet":
        rig.values["notifications.quiet_start"] = rig.values["notifications.quiet_end"] = None
    else:
        rig.reading = LOW_USE
    await rig.engine.recheck()
    assert starts == [LOOK]
    (mission,) = rig.missions.running()
    assert (mission.item_key, mission.work) == (LOOK, "prepare")


async def test_a_look_refused_at_the_limit_starts_at_a_later_check(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A look refused at the day's limit is not remembered as tried: each check asks again, and
    once the limit has room the same look starts."""
    rig.values["decision.limit.look"] = 0
    starts = count_starts(rig, monkeypatch)
    coding(rig, IDLE)
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert starts == [LOOK, LOOK]
    assert rig.host.calls == [] and rig.rows("decision_missions") == []

    rig.values["decision.limit.look"] = 1
    await rig.engine.recheck()
    assert starts == [LOOK] * 3
    (mission,) = rig.missions.running()
    assert mission.item_key == LOOK


@pytest.mark.parametrize("end", ["taken", "cleared", "changed"])
async def test_a_taken_or_cleared_look_closes(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, end: str
) -> None:
    """Once a mission holds its key the look's item closes and VIKI starts no second look; a
    folder whose changes clear closes it too, and one whose changes change again lists anew
    under its new key."""
    if end != "taken":
        rig.values["decision.grant.look"] = "ask"
    starts = count_starts(rig, monkeypatch)
    host = coding(rig, IDLE)
    await rig.engine.recheck()
    assert state_of(rig, LOOK) == "open"

    again = replace(IDLE, newest=WALL - timedelta(days=1, hours=1))
    if end == "cleared":
        host.look = CodeLook(())
    elif end == "changed":
        host.look = CodeLook((again,))
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, LOOK) == "stale"
    if end == "taken":
        assert starts == [LOOK] and len(rig.missions.running()) == 1
    else:
        assert starts == [] and rig.rows("decision_missions") == []
    newer = item_key("code_look", FOLDER, again.newest.isoformat())
    assert state_of(rig, newer) == ("open" if end == "changed" else None)


# ---- second looks --------------------------------------------

LOOKED_AT = "00000000-0000-4000-8000-0000000000d1"  # the user's chat in view
TURN = "00000000-0000-4000-8000-0000000000d2"  # its last reply
GARDEN = "notes/garden-plan.md"
HINT = SecondLookHint(LOOKED_AT, TURN, "long_session", "Garden plan", (GARDEN,))
OFFER = item_key("long_task_look", LOOKED_AT, TURN)
NEXT_TURN = "00000000-0000-4000-8000-0000000000d3"  # a later reply in the user's chat
WINDOW = "window"  # the one app window a test reports presence for


def in_view(hint: SecondLookHint | None = HINT) -> Observation:
    """`observation()` with the page reporting `hint` for the chat in view (None: none)."""
    page = PageFacts(
        native_waiting=0,
        runs_working=0,
        runs_review=0,
        git_state="unknown",
        git_dirty=None,
        routine_id=None,
        next_run=None,
        routines_paused=None,
        second_look=hint,
    )
    return replace(observation(), page=page)


def viewing(rig: Rig, chat: str | None, hint: SecondLookHint | None = None) -> None:
    """The user has `chat` open (None: no chat), and the page reports `hint` as its last reply
    worth a second look (None: none is)."""
    presence = Presence(
        focused=False,
        typing=False,
        away=False,
        chat_id=chat,
        chat_title=None,
        note_ref=None,
        launcher_open=False,
        page=None,
    )
    rig.presence.report(WINDOW, presence)
    rig.observation = in_view(hint)


def owner_reads(rig: Rig, monkeypatch: pytest.MonkeyPatch, *, mine: bool = True) -> list[str]:
    """Each chat `MissionService.owner_chat` is asked about, answering `mine` off the event
    loop."""
    asked: list[str] = []
    loop = threading.get_ident()

    def owner_chat(session_id: str) -> bool:
        assert threading.get_ident() != loop
        asked.append(session_id)
        return mine

    monkeypatch.setattr(rig.missions, "owner_chat", owner_chat)
    return asked


@pytest.mark.parametrize(
    ("case", "lists"),
    [
        ("ordinary", True),
        ("protected", True),
        ("grant_off", False),
        ("read_only", False),
        ("unknown_policy", False),
        ("folder", False),
        ("chat_only", False),
        ("launcher", False),
        ("not_the_owners", False),
        ("no_note", False),
    ],
)
async def test_no_second_look_while_off_outside_a_usable_vault_or_for_a_chat_that_is_not_the_owners(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str, lists: bool
) -> None:
    """Under Ask first, an Ordinary or Protected vault lists one offer for the user's own chat
    turn in view, asking whose chat it is off the event loop. Grant Off, a read-only vault, a
    policy not read, a folder or chat-only launch, VIKI's own chat, a chat that is not the
    user's (a profile, rooted, routine, Bot or mission chat) and a turn that edited no note list
    none; only the last two ask whose chat it is."""
    rig.values["decision.grant.second_look"] = "off" if case == "grant_off" else "ask"
    asked = owner_reads(rig, monkeypatch, mine=case != "not_the_owners")
    obs = in_view(replace(HINT, refs=("notes/garden.txt",)) if case == "no_note" else HINT)
    if case == "protected":
        obs = replace(obs, policy="protected")
    elif case == "read_only":
        obs = replace(obs, policy="existing_readonly")
    elif case == "unknown_policy":
        obs = replace(obs, policy=None)
    elif case in ("folder", "chat_only"):
        obs = replace(obs, kind=case)
    elif case == "launcher":
        rig.values["decision.launcher_session"] = LOOKED_AT
    rig.observation = obs
    await rig.engine.recheck()
    assert state_of(rig, OFFER) == ("open" if lists else None)
    assert asked == ([LOOKED_AT] if lists or case == "not_the_owners" else [])
    assert rig.rows("decision_missions") == []


async def test_allowed_starts_one_second_look_for_the_chat_turn_in_view(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no model key, at the check that first sees the turn: VIKI starts one plan-mode
    second look in the vault, with no root, whose brief names the chat's title and the edited
    note. Once its mission holds the key the offer closes and nothing starts again."""
    starts = count_starts(rig, monkeypatch)
    rig.observation = in_view()
    await rig.engine.recheck()
    (mission,) = rig.missions.running()
    assert (mission.scope, mission.item_key, mission.work) == (SCOPE, OFFER, "prepare")
    assert mission.title == "Second look: Garden plan"
    session = mission.session_id
    assert rig.host.calls == [
        ("remember", session, {"new_session": True, "mode": "plan"}),
        ("create", session),
    ]
    (sent,) = rig.host.actors[session].sent
    assert 'finished in the chat "Garden plan"' in sent and f"\n- {GARDEN}\n" in sent
    assert "Leave out" not in sent and rig.model.requests == []

    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, OFFER) == "stale" and starts == [OFFER]
    assert [call for call in rig.host.calls if call[0] == "create"] == [("create", session)]


async def test_ask_first_offers_take_a_second_look_and_starts_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under Ask first the offer lists as a suggestion opening the chat in view, then Take a
    second look, with a chip for the edited note, and VIKI starts nothing, whatever the prepare
    grant."""
    rig.values["decision.grant.second_look"] = "ask"
    rig.values["decision.grant.prepare"] = "allowed"
    starts = count_starts(rig, monkeypatch)
    rig.observation = in_view()
    await rig.engine.recheck()
    await rig.engine.recheck()
    found = rig.agenda.item(SCOPE, OFFER)
    assert found is not None
    assert (found.state, found.kind, found.level) == ("open", "suggest", "list")
    actions = [(action.type, action.label) for action in found.body.actions]
    assert actions == [("open_chat", "Open chat"), ("start_prepare", "Take a second look")]
    assert found.body.actions[0].session_id == LOOKED_AT
    assert [label.ref for label in found.body.evidence] == [GARDEN]
    assert starts == [] and rig.host.calls == [] and rig.rows("decision_missions") == []


@pytest.mark.parametrize("hold", ["quiet", "brake"])
async def test_quiet_hours_and_the_brake_hold_a_second_look_until_they_end(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, hold: str
) -> None:
    """Quiet hours and the plan brake hold VIKI's own second look: nothing is asked of the
    missions, stored or remembered, and it starts at the first check after the hold ends while
    the reply is still in view."""
    starts = count_starts(rig, monkeypatch)
    rig.observation = in_view()
    if hold == "quiet":  # WALL is 18:00 in JST
        rig.values["notifications.quiet_start"] = "17:00"
        rig.values["notifications.quiet_end"] = "19:00"
    else:
        rig.reading = plan_reading(use=0.8, reset=at_wall(3600))
    await rig.engine.recheck()
    await rig.engine.recheck()
    held = rig.agenda.item(SCOPE, OFFER)
    assert held is not None and (held.state, held.mission_id) == ("open", None)
    assert starts == [] and rig.host.calls == [] and rig.rows("decision_missions") == []

    if hold == "quiet":
        rig.values["notifications.quiet_start"] = rig.values["notifications.quiet_end"] = None
    else:
        rig.reading = LOW_USE
    await rig.engine.recheck()
    assert starts == [OFFER]
    (mission,) = rig.missions.running()
    assert (mission.item_key, mission.work) == (OFFER, "prepare")


async def test_a_second_look_refused_at_the_limit_starts_at_a_later_check(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second look refused at the day's limit is not remembered as tried: each check asks
    again, and once the limit has room the same look starts."""
    rig.values["decision.limit.second_look"] = 0
    starts = count_starts(rig, monkeypatch)
    rig.observation = in_view()
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert starts == [OFFER, OFFER]
    assert rig.host.calls == [] and rig.rows("decision_missions") == []

    rig.values["decision.limit.second_look"] = 1
    await rig.engine.recheck()
    assert starts == [OFFER] * 3
    (mission,) = rig.missions.running()
    assert mission.item_key == OFFER


async def test_a_busy_hold_offers_take_a_second_look_and_the_owner_starts_it(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While a chat VIKI started is open VIKI starts no other, so an Allowed offer shows Take a
    second look and its why says what holds it; VIKI's own start is asked and refused as before,
    and the user's own start runs beside the open chat."""
    rig.observation = in_view()
    await rig.engine.recheck()
    (first,) = rig.missions.running()
    starts = count_starts(rig, monkeypatch)
    other = item_key("long_task_look", OTHER_CHAT, TURN)
    rig.observation = in_view(replace(HINT, session=OTHER_CHAT))
    await rig.engine.recheck()
    found = rig.agenda.item(SCOPE, other)
    assert found is not None and (found.state, found.mission_id) == ("open", None)
    actions = [(action.type, action.label) for action in found.body.actions]
    assert actions == [("open_chat", "Open chat"), ("start_prepare", "Take a second look")]
    assert found.body.why.endswith(" " + templates.HOLD_WHY["busy"])
    assert starts == [other]
    assert [mission.mission_id for mission in rig.missions.running()] == [first.mission_id]

    mission = await rig.missions.start(SCOPE, found, "prepare", by_owner=True)
    assert mission.state == "running" and len(rig.missions.running()) == 2


@pytest.mark.parametrize("grant", ["ask", "allowed"])
async def test_at_todays_limit_an_offer_shows_no_start_and_says_so(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, grant: str
) -> None:
    """At today's limit an offer shows no start under Ask first or Allowed, and its why says the
    limit is reached; VIKI's own start under Allowed is asked and refused as before."""
    rig.values["decision.grant.second_look"] = grant
    rig.values["decision.limit.second_look"] = 0
    starts = count_starts(rig, monkeypatch)
    rig.observation = in_view()
    await rig.engine.recheck()
    found = rig.agenda.item(SCOPE, OFFER)
    assert found is not None and found.state == "open"
    actions = [(action.type, action.label) for action in found.body.actions]
    assert actions == [("open_chat", "Open chat")]
    assert found.body.why.endswith(" " + templates.HOLD_WHY["limit"])
    assert starts == ([OFFER] if grant == "allowed" else [])
    assert rig.host.calls == [] and rig.rows("decision_missions") == []


@pytest.mark.parametrize("end", ["answered", "closed"])
async def test_a_second_looks_finding_lists_while_its_plan_waits_and_closes_after(
    rig: Rig, end: str
) -> None:
    """While a second look's plan waits, one Suggestion names its kind and opens the look's chat
    (Open plan); once the plan is answered or the chat ends it closes, and no mission report is
    ever listed for the look."""
    rig.observation = in_view()
    await rig.engine.recheck()
    (mission,) = rig.missions.running()
    actor = rig.host.actors[mission.session_id]
    card = PermissionRequested(
        tool_use_id="plan-1",
        kind="plan",
        tool="ExitPlanMode",
        summary="A plan",
        plan="Kind: flaw\nThe beds overlap.",
    )
    actor.emit(card)
    await rig.engine.recheck()
    finding = item_key("long_task_look", mission.mission_id, "found")
    found = rig.agenda.item(SCOPE, finding)
    assert found is not None and (found.state, found.kind) == ("open", "suggest")
    assert found.body.what == 'VIKI found a possible flaw in "Garden plan".'
    actions = [(action.type, action.label, action.session_id) for action in found.body.actions]
    assert actions == [("open_chat", "Open plan", mission.session_id)]

    if end == "answered":
        actor.emit(RequestAnswered(tool_use_id="plan-1"))
        await rig.engine.recheck()
        assert state_of(rig, finding) == "stale"
        actor.emit(TurnDone(status="success", usage=Usage()))
    else:
        actor.emit(SessionClosed(reason="requested"))
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, finding) == "stale"
    assert rig.missions.running() == [] and items_of(rig, "mission_report") == []


async def test_a_finding_lists_only_in_its_own_scope(rig: Rig) -> None:
    """A second look waiting in one workspace lists its finding in that workspace's cycle only."""
    plan = PermissionRequested(
        tool_use_id="plan-1",
        kind="plan",
        tool="ExitPlanMode",
        summary="A plan",
        plan="Kind: flaw\nThe beds overlap.",
    )
    keys: dict[str, str] = {}
    for scope, chat, by_owner in ((OTHER_SCOPE, OTHER_CHAT, False), (SCOPE, LOOKED_AT, True)):
        rig.observation = replace(in_view(replace(HINT, session=chat)), scope=scope)
        await rig.engine.recheck()
        if by_owner:  # VIKI's own chats take one place at most: the user takes this look
            offer = rig.agenda.item(scope, item_key("long_task_look", chat, TURN))
            assert offer is not None
            await rig.missions.start(scope, offer, "prepare", by_owner=True)
        (mission,) = [m for m in rig.missions.running() if m.scope == scope]
        rig.host.actors[mission.session_id].emit(plan)
        await rig.engine.recheck()
        keys[scope] = item_key("long_task_look", mission.mission_id, "found")

    assert keys[SCOPE] != keys[OTHER_SCOPE]
    assert rig.agenda.item(SCOPE, keys[SCOPE]) is not None
    assert rig.agenda.item(SCOPE, keys[OTHER_SCOPE]) is None
    assert rig.agenda.item(OTHER_SCOPE, keys[SCOPE]) is None


async def test_a_second_look_offer_stays_until_its_chat_gets_a_new_reply(rig: Rig) -> None:
    """The offer for a chat's last reply stays open while the page shows another chat or none.
    Once the page shows that chat with another last reply it closes: a newer reply worth a
    second look gets an offer of its own, and a chat with no such reply (the user wrote again)
    keeps none."""
    rig.values["decision.grant.second_look"] = "ask"
    newer = item_key("long_task_look", LOOKED_AT, NEXT_TURN)
    viewing(rig, LOOKED_AT, HINT)
    await rig.engine.recheck()
    assert state_of(rig, OFFER) == "open"

    for chat in (OTHER_CHAT, None):  # another chat with no reply worth a look, then no chat
        viewing(rig, chat)
        await rig.engine.recheck()
        assert state_of(rig, OFFER) == "open"

    viewing(rig, LOOKED_AT, replace(HINT, turn=NEXT_TURN))
    await rig.engine.recheck()
    assert (state_of(rig, OFFER), state_of(rig, newer)) == ("stale", "open")

    viewing(rig, LOOKED_AT)
    await rig.engine.recheck()
    assert state_of(rig, newer) == "stale"


async def test_an_offer_stays_while_the_page_reports_another_windows_chat(rig: Rig) -> None:
    """With the offer's chat open in one window, a reply the page reports for another chat is
    the other window's (presence merges every window), so the offer stays open."""
    rig.values["decision.grant.second_look"] = "ask"
    viewing(rig, LOOKED_AT, HINT)
    await rig.engine.recheck()
    assert state_of(rig, OFFER) == "open"

    viewing(rig, LOOKED_AT, replace(HINT, session=OTHER_CHAT))
    await rig.engine.recheck()
    assert state_of(rig, OFFER) == "open"


async def test_a_kept_offer_closes_once_a_mission_holds_its_key(rig: Rig) -> None:
    """With its chat out of view, an offer the user starts from the launcher closes at the next
    check and stays closed."""
    rig.values["decision.grant.second_look"] = "ask"
    viewing(rig, LOOKED_AT, HINT)
    await rig.engine.recheck()
    viewing(rig, OTHER_CHAT)
    await rig.engine.recheck()
    offer = rig.agenda.item(SCOPE, OFFER)
    assert offer is not None and offer.state == "open"

    await rig.missions.start(SCOPE, offer, "prepare", by_owner=True)
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, OFFER) == "stale"


async def test_a_second_look_that_finds_nothing_lists_one_quiet_report(rig: Rig) -> None:
    """A second look whose turn ends with no plan lists one Report, at list level, that names
    the chat and opens VIKI's chat; no finding is listed."""
    rig.observation = in_view()
    await rig.engine.recheck()
    (mission,) = rig.missions.running()
    rig.host.actors[mission.session_id].emit(TurnDone(status="success", usage=Usage()))
    await rig.engine.recheck()
    await rig.engine.recheck()
    (report,) = items_of(rig, "mission_report")
    found = rig.agenda.item(SCOPE, report["item_key"])
    assert found is not None and (found.state, found.level) == ("open", "list")
    assert found.body.what == 'Second look at "Garden plan": nothing worth your time.'
    chats = [action.session_id for action in found.body.actions if action.type == "open_chat"]
    assert chats == [mission.session_id]
    finding = item_key("long_task_look", mission.mission_id, "found")
    assert state_of(rig, finding) is None


def look_rows(rig: Rig, monkeypatch: pytest.MonkeyPatch, rows: Sequence[LookRow]) -> list[Any]:
    """Each (scope, since, types) `MissionService.second_look_rows` is asked for, off the event
    loop, answering the `rows` of `types` started at or after `since`."""
    reads: list[Any] = []
    loop = threading.get_ident()

    def read(
        scope: str, since: datetime, *, types: tuple[str, ...] = SECOND_LOOKS
    ) -> list[LookRow]:
        assert threading.get_ident() != loop
        reads.append((scope, since, types))
        return [row for row in rows if row.started >= since and row.look in types]

    monkeypatch.setattr(rig.missions, "second_look_rows", read)
    return reads


def answered_rows(
    look: str, *reasons: str, at: datetime = WALL - timedelta(hours=1)
) -> list[LookRow]:
    return [
        LookRow(look, "done", reason, at - timedelta(minutes=n)) for n, reason in enumerate(reasons)
    ]


@pytest.mark.parametrize(
    ("case", "left_out"),
    [
        ("type_paused", None),
        ("every_kind_paused", None),
        ("kinds_paused", " Leave out flaws and missing pieces."),
        ("learning_off", ""),
        ("tried_again", ""),
        ("before_the_window", ""),
        ("before_a_reset", ""),
    ],
)
async def test_a_paused_trigger_lists_no_offer_and_paused_kinds_reach_the_brief(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str, left_out: str | None
) -> None:
    """With an offer, the cycle reads the scope's second looks since the later of 30 days ago
    and the last Reset, off the event loop, under the stored Try again stamps. Ten turned-down
    looks of the type pause it, and five turned-down findings of each kind pause every
    kind: either lists no offer and starts nothing. Kinds paused alone reach the brief's Leave
    out. Learning off, a Try again since, or rows before the window or a Reset pause nothing."""
    rows = answered_rows("long_task_look", *["rejected:other"] * 10)
    since = WALL - SECOND_LOOK_WINDOW
    if case == "every_kind_paused":
        rows = answered_rows(
            "correction_look", *[f"rejected:{kind}" for kind in ("flaw", "approach", "missing")] * 5
        )
    elif case == "kinds_paused":
        rows = answered_rows("correction_look", *["rejected:flaw", "rejected:missing"] * 5)
    elif case == "learning_off":
        rig.values["decision.learning"] = False
    elif case == "tried_again":
        value = second_looks.restarted(
            None, look="long_task_look", now=WALL - timedelta(minutes=30)
        )
        rig.store.set_json(SCOPE, "second_looks", value, WALL)
    elif case == "before_the_window":
        rows = answered_rows("long_task_look", *["nothing"] * 10, at=since - timedelta(seconds=1))
    elif case == "before_a_reset":
        since = WALL - timedelta(minutes=30)
        rig.store.forget_learning(SCOPE, restart_at=since)
    reads = look_rows(rig, monkeypatch, rows)
    starts = count_starts(rig, monkeypatch)
    rig.observation = in_view()
    await rig.engine.recheck()
    assert reads == [(SCOPE, since, SECOND_LOOKS)]
    if left_out is None:
        assert state_of(rig, OFFER) is None and starts == [] and rig.host.calls == []
        return
    (mission,) = rig.missions.running()
    (sent,) = rig.host.actors[mission.session_id].sent
    how = next(line for line in sent.splitlines() if line.startswith("How: "))
    assert how.endswith("stop without another plan." + left_out)


# ---- project plans ----------------------------------------------

GARDEN_NOTE = "10-projects/garden/garden.md"
MISSION_ID = "00000000-0000-4000-8000-0000000000e1"  # a project plan's mission, waiting on its plan
STALLED = Goal(GARDEN_NOTE, "Allotment", (), touched=WALL - timedelta(days=15))
STALL = item_key("stalled_project", GARDEN_NOTE, stamp(WALL - timedelta(days=15)))
LATE = task("Prune the roses", TODAY - timedelta(days=4))
OVERDUE = item_key("overdue_tasks", TASKS, f"{LATE.due.isoformat()} {LATE.text}")
PLAN_CARD = PermissionRequested(
    tool_use_id="plan-1", kind="plan", tool="ExitPlanMode", summary="A plan", plan="1. Sow."
)


def projects(*goals: Goal, late: tuple[TaskItem, ...] = ()) -> Observation:
    """`observation()` with these active projects and these tasks due."""
    return replace(observation(*late), goals=goals)


def plan_of(rig: Rig, key: str) -> MissionRecord:
    """The one running mission that holds `key`."""
    (mission,) = [mission for mission in rig.missions.running() if mission.item_key == key]
    return mission


async def test_allowed_starts_a_plan_for_a_stalled_project(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no model key, at the check that first sees the stall: VIKI starts one plan-mode
    chat in the vault, with no root, titled for the project, whose brief names the project's
    note and not its name. Once its mission holds the key the offer closes and nothing starts
    again."""
    starts = count_starts(rig, monkeypatch)
    rig.observation = projects(STALLED)
    await rig.engine.recheck()
    mission = plan_of(rig, STALL)
    assert (mission.scope, mission.work, mission.title) == (
        SCOPE,
        "prepare",
        "Next steps: Allotment",
    )
    session = mission.session_id
    assert rig.host.calls == [
        ("remember", session, {"new_session": True, "mode": "plan"}),
        ("create", session),
    ]
    (sent,) = rig.host.actors[session].sent
    assert "Goal: Propose three next actions for the project" in sent
    assert f"\nEvidence:\n- {GARDEN_NOTE}\n" in sent
    assert "Allotment" not in sent and rig.model.requests == []

    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, STALL) == "stale" and starts == [STALL]
    assert [call for call in rig.host.calls if call[0] == "create"] == [("create", session)]


async def test_allowed_starts_a_check_of_overdue_tasks(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tasks file with a task four days overdue starts one plan-mode check, titled for the
    project's folder, whose brief names the tasks file and none of the tasks' text; a task two
    days overdue starts none."""
    starts = count_starts(rig, monkeypatch)
    rig.observation = projects(late=(task("Water the beds", TODAY - timedelta(days=2)),))
    await rig.engine.recheck()
    assert starts == [] and rig.rows("decision_missions") == []

    rig.observation = projects(late=(LATE,))
    await rig.engine.recheck()
    mission = plan_of(rig, OVERDUE)
    assert (mission.scope, mission.work, mission.title) == (SCOPE, "prepare", "Overdue tasks: plan")
    session = mission.session_id
    assert rig.host.calls == [
        ("remember", session, {"new_session": True, "mode": "plan"}),
        ("create", session),
    ]
    (sent,) = rig.host.actors[session].sent
    assert "Goal: Check the open tasks in the note under Evidence" in sent
    assert f"\nEvidence:\n- {TASKS}\n" in sent
    assert "Prune" not in sent and starts == [OVERDUE]


async def test_one_project_plan_per_key_in_any_scope(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project plan VIKI started in one workspace holds its key everywhere: the same stall
    seen in another workspace lists no offer there and asks the missions for no start."""
    starts = count_starts(rig, monkeypatch)
    rig.observation = replace(projects(STALLED), scope=OTHER_SCOPE)
    await rig.engine.recheck()
    assert plan_of(rig, STALL).scope == OTHER_SCOPE

    rig.observation = projects(STALLED)
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, STALL) is None and starts == [STALL]
    assert len(rig.rows("decision_missions")) == 1


@pytest.mark.parametrize(
    ("case", "key", "start"),
    [("stalled", STALL, "Plan next steps"), ("overdue", OVERDUE, "Check these tasks")],
    ids=["stalled", "overdue"],
)
async def test_ask_first_offers_a_project_plan_and_starts_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str, key: str, start: str
) -> None:
    """Under Ask first the offer lists as a suggestion opening its note, then its own start,
    with a chip for the note named by the project, and VIKI starts nothing, though the
    second-look grant and Prepare drafts are Allowed."""
    rig.values["decision.grant.project_plan"] = "ask"
    rig.values["decision.grant.second_look"] = "allowed"
    rig.values["decision.grant.prepare"] = "allowed"
    starts = count_starts(rig, monkeypatch)
    rig.observation = projects(STALLED) if case == "stalled" else projects(late=(LATE,))
    await rig.engine.recheck()
    await rig.engine.recheck()
    found = rig.agenda.item(SCOPE, key)
    assert found is not None
    assert (found.state, found.kind, found.level) == ("open", "suggest", "list")
    actions = [(action.type, action.label) for action in found.body.actions]
    assert actions == [("open_note", "Open note"), ("start_prepare", start)]
    note = GARDEN_NOTE if case == "stalled" else TASKS
    assert (found.body.actions[0].ref, found.body.actions[0].workspace_id) == (note, SCOPE)
    assert [(label.label, label.ref) for label in found.body.evidence] == [
        ("Allotment" if case == "stalled" else "plan", note)
    ]
    assert starts == [] and rig.host.calls == [] and rig.rows("decision_missions") == []


@pytest.mark.parametrize("hold", ["quiet", "brake"])
async def test_quiet_hours_and_the_brake_hold_a_project_plan_until_they_end(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, hold: str
) -> None:
    """Quiet hours and the plan brake hold VIKI's own project plan: nothing is asked of the
    missions, stored or remembered, and it starts at the first check after the hold ends."""
    starts = count_starts(rig, monkeypatch)
    rig.observation = projects(STALLED)
    if hold == "quiet":  # WALL is 18:00 in JST
        rig.values["notifications.quiet_start"] = "17:00"
        rig.values["notifications.quiet_end"] = "19:00"
    else:
        rig.reading = plan_reading(use=0.8, reset=at_wall(3600))
    await rig.engine.recheck()
    await rig.engine.recheck()
    held = rig.agenda.item(SCOPE, STALL)
    assert held is not None and (held.state, held.mission_id) == ("open", None)
    assert starts == [] and rig.host.calls == [] and rig.rows("decision_missions") == []

    if hold == "quiet":
        rig.values["notifications.quiet_start"] = rig.values["notifications.quiet_end"] = None
    else:
        rig.reading = LOW_USE
    await rig.engine.recheck()
    assert starts == [STALL] and plan_of(rig, STALL).work == "prepare"


async def test_a_project_plan_refused_at_the_limit_starts_at_a_later_check(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project plan refused at the day's limit is not remembered as tried: each check asks
    again, and once the limit has room the same plan starts."""
    rig.values["decision.limit.project_plan"] = 0
    starts = count_starts(rig, monkeypatch)
    rig.observation = projects(STALLED)
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert starts == [STALL, STALL]
    assert rig.host.calls == [] and rig.rows("decision_missions") == []

    rig.values["decision.limit.project_plan"] = 1
    await rig.engine.recheck()
    assert starts == [STALL] * 3 and plan_of(rig, STALL).scope == SCOPE


async def test_a_project_plans_finding_lists_in_its_own_scope_while_its_plan_waits(
    rig: Rig,
) -> None:
    """While a project plan's plan waits, its own workspace lists one Suggestion naming the
    project with Open plan to the plan's chat, with the grant Off too; another workspace's
    cycle lists none of it. Once the plan is answered the finding closes, and no mission report
    is listed for the plan."""
    orchard = Goal("10-projects/orchard/orchard.md", "Orchard", (), touched=STALLED.touched)
    keys: dict[str, str] = {}
    sessions: dict[str, str] = {}
    for scope, goal, by_owner in ((OTHER_SCOPE, orchard, False), (SCOPE, STALLED, True)):
        rig.values["decision.grant.project_plan"] = "allowed"
        rig.observation = replace(projects(goal), scope=scope)
        await rig.engine.recheck()
        if by_owner:  # VIKI's own chats take one place at most: the user starts this plan
            offer = rig.agenda.item(scope, STALL)
            assert offer is not None
            await rig.missions.start(scope, offer, "prepare", by_owner=True)
        (mission,) = [m for m in rig.missions.running() if m.scope == scope]
        rig.host.actors[mission.session_id].emit(PLAN_CARD)
        rig.values["decision.grant.project_plan"] = "off"
        await rig.engine.recheck()
        keys[scope] = item_key("stalled_project", mission.mission_id, "found")
        sessions[scope] = mission.session_id

    found = rig.agenda.item(SCOPE, keys[SCOPE])
    assert found is not None and (found.state, found.kind) == ("open", "suggest")
    assert found.body.what == 'VIKI\'s plan for "Allotment" waits for your approval.'
    actions = [(action.type, action.label, action.session_id) for action in found.body.actions]
    assert actions == [("open_chat", "Open plan", sessions[SCOPE])]
    assert rig.agenda.item(SCOPE, keys[OTHER_SCOPE]) is None
    assert rig.agenda.item(OTHER_SCOPE, keys[SCOPE]) is None
    other = rig.agenda.item(OTHER_SCOPE, keys[OTHER_SCOPE])
    assert other is not None and other.state == "open"

    actor = rig.host.actors[sessions[SCOPE]]
    actor.emit(RequestAnswered(tool_use_id="plan-1"))
    await rig.engine.recheck()
    assert state_of(rig, keys[SCOPE]) == "stale"
    actor.emit(TurnDone(status="success", usage=Usage()))
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert items_of(rig, "mission_report") == []


@pytest.mark.parametrize("end", ["changed", "ticked", "redated", "taken"])
async def test_a_project_plan_offer_closes_when_its_fact_clears(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, end: str
) -> None:
    """An offer goes stale once the project changes, its overdue task is ticked or given a new
    date, or a mission holds its key; a cleared one lists nothing in its place."""
    if end != "taken":
        rig.values["decision.grant.project_plan"] = "ask"
    stalled = end in ("changed", "taken")
    key = STALL if stalled else OVERDUE
    rig.observation = projects(STALLED) if stalled else projects(late=(LATE,))
    await rig.engine.recheck()
    assert state_of(rig, key) == "open"

    if end == "changed":
        rig.observation = projects(replace(STALLED, touched=WALL - timedelta(days=1)))
    elif end == "ticked":
        rig.observation = projects()
    elif end == "redated":
        rig.observation = projects(late=(replace(LATE, due=TODAY + timedelta(days=2)),))
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, key) == "stale"
    plans = [row for row in rig.rows("decision_items") if row["candidate"] in PROJECT_PLANS]
    assert [row["state"] for row in plans] == ["stale"]
    assert len(rig.rows("decision_missions")) == (1 if end == "taken" else 0)


@pytest.mark.parametrize(
    ("case", "listed"),
    [
        ("paused", False),
        ("learning_off", True),
        ("tried_again", True),
        ("before_the_window", True),
        ("before_a_reset", True),
    ],
)
async def test_a_paused_project_plan_type_lists_no_offer(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str, listed: bool
) -> None:
    """With a project-plan offer, the cycle reads the scope's project plans since the later of
    30 days ago and the last Reset, off the event loop, under the stored "project_plans" Try
    again stamps. Ten stalled-project plans that found nothing pause that type: its offer is not
    listed and nothing starts, while the overdue-tasks offer and a waiting plan's finding still
    list. Learning off, a Try again since, or rows before the window or a Reset pause nothing."""
    rows = answered_rows("stalled_project", *["nothing"] * 10)
    since = WALL - SECOND_LOOK_WINDOW
    if case == "learning_off":
        rig.values["decision.learning"] = False
    elif case == "tried_again":
        value = second_looks.restarted(
            None,
            look="stalled_project",
            now=WALL - timedelta(minutes=30),
            looks=PROJECT_PLANS,
            kinds=(),
        )
        rig.store.set_json(SCOPE, "project_plans", value, WALL)
    elif case == "before_the_window":
        rows = answered_rows("stalled_project", *["nothing"] * 10, at=since - timedelta(seconds=1))
    elif case == "before_a_reset":
        since = WALL - timedelta(minutes=30)
        rig.store.forget_learning(SCOPE, restart_at=since)
    rig.values["decision.grant.project_plan"] = "ask"
    waiting = Finding(MISSION_ID, SCOPE, LOOKED_AT, "stalled_project", "other", "Orchard")
    monkeypatch.setattr(rig.missions, "findings", lambda: [waiting])
    reads = look_rows(rig, monkeypatch, rows)
    rig.observation = projects(STALLED, late=(LATE,))
    await rig.engine.recheck()
    assert reads == [(SCOPE, since, PROJECT_PLANS)]
    assert state_of(rig, STALL) == ("open" if listed else None)
    assert state_of(rig, OVERDUE) == "open"
    assert state_of(rig, item_key("stalled_project", MISSION_ID, "found")) == "open"
    assert rig.rows("decision_missions") == []


async def test_no_project_plan_yields_are_read_without_an_offer(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cycle with only a waiting plan's finding, and no project-plan offer, reads no yields."""
    waiting = Finding(MISSION_ID, SCOPE, LOOKED_AT, "overdue_tasks", "other", "plan")
    monkeypatch.setattr(rig.missions, "findings", lambda: [waiting])
    reads = look_rows(rig, monkeypatch, answered_rows("overdue_tasks", *["nothing"] * 10))
    rig.observation = projects(STALLED)
    rig.values["decision.grant.project_plan"] = "off"
    await rig.engine.recheck()
    assert reads == []
    assert state_of(rig, item_key("overdue_tasks", MISSION_ID, "found")) == "open"


# ---- message drafts ------------------------------------------------------------

HANDOFF = item_key("context_handoff", LOOKED_AT)
SOURCE_NOTE = item_key("source_note", LOOKED_AT, TURN)
DRAFT_KEYS = (HANDOFF, SOURCE_NOTE)
NEXT_TURN = "00000000-0000-4000-8000-0000000000d3"  # the chat's next reply


def drafting(
    percent: int | None = 75, turn: str | None = TURN, *, chat: str = LOOKED_AT
) -> Observation:
    """`observation()` with the page reporting the chat in view `chat` at `percent` of its
    context and its last reply `turn` as one that fetched web pages and read no note; None
    reports no such part."""
    obs = in_view()
    assert obs.page is not None
    page = replace(
        obs.page,
        second_look=None,
        context=None if percent is None else ContextHint(chat, percent),
        sources=None if turn is None else SourceHint(chat, turn, False),
    )
    return replace(obs, page=page)


def draft_reads(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, rows: Sequence[LookRow] = ()
) -> list[tuple[str, str, object]]:
    """The admission's reads besides `owner_chat`, each asserted off the event loop: ("rows",
    scope, since) for `DecisionLoopStats.draft_rows`, answering the `rows` started at or after
    since, and ("today", scope, day) for `drafts_today`, answering the store's own count."""
    reads: list[tuple[str, str, object]] = []
    loop = threading.get_ident()
    stats = rig.engine._stats
    count = stats.drafts_today

    def draft_rows(scope: str, since: datetime) -> list[LookRow]:
        assert threading.get_ident() != loop
        reads.append(("rows", scope, since))
        return [row for row in rows if row.started >= since]

    def drafts_today(scope: str, day: str, keys: Sequence[str]) -> tuple[int, frozenset[str]]:
        assert threading.get_ident() != loop
        reads.append(("today", scope, day))
        return count(scope, day, keys)

    monkeypatch.setattr(stats, "draft_rows", draft_rows)
    monkeypatch.setattr(stats, "drafts_today", drafts_today)
    return reads


@pytest.mark.parametrize(
    ("case", "lists"),
    [
        ("ordinary", True),
        ("protected", True),
        ("ask_first", True),
        ("grant_off", False),
        ("read_only", False),
        ("unknown_policy", False),
        ("folder", False),
        ("chat_only", False),
        ("launcher", False),
        ("not_the_owners", False),
    ],
)
async def test_message_drafts_list_only_for_the_owners_chat_in_a_usable_vault(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str, lists: bool
) -> None:
    """While Prepare drafts is Allowed or Ask first, an Ordinary or Protected vault lists both
    drafts for the user's own chat in view, asking once whose chat it is, off the event loop.
    Prepare drafts Off, a read-only vault, a policy not read, a folder or chat-only launch (which
    read no policy), VIKI's own chat and a chat that is not the user's (a profile, rooted,
    routine, Bot or mission chat) list none; only the last asks whose chat it is."""
    grant = {"grant_off": "off", "ask_first": "ask"}.get(case, "allowed")
    rig.values["decision.grant.prepare"] = grant
    asked = owner_reads(rig, monkeypatch, mine=case != "not_the_owners")
    obs = drafting()
    if case == "protected":
        obs = replace(obs, policy="protected")
    elif case == "read_only":
        obs = replace(obs, policy="existing_readonly")
    elif case == "unknown_policy":
        obs = replace(obs, policy=None)
    elif case in ("folder", "chat_only"):
        obs = replace(obs, kind=case, policy=None)
    elif case == "launcher":
        rig.values["decision.launcher_session"] = LOOKED_AT
    rig.observation = obs
    await rig.engine.recheck()
    assert [state_of(rig, key) for key in DRAFT_KEYS] == ["open" if lists else None] * 2
    assert asked == ([LOOKED_AT] if lists or case == "not_the_owners" else [])
    assert rig.rows("decision_missions") == []


@pytest.mark.parametrize("grant", ["ask", "allowed"])
async def test_a_message_draft_starts_nothing_whatever_the_grants(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, grant: str
) -> None:
    """With the model answering work to prepare, Prepare drafts at Ask first or Allowed and every
    other grant Allowed, each draft lists as a suggestion whose one action puts its text in the
    chat in view's message box (beside See why), decided with no work; VIKI asks for no start
    and stores no mission."""
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    for name in ("run", "team", "look", "second_look", "project_plan"):
        rig.values[f"decision.grant.{name}"] = "allowed"
    rig.values["decision.grant.prepare"] = grant
    owner_reads(rig, monkeypatch)
    starts = count_starts(rig, monkeypatch)
    rig.observation = drafting()
    await rig.engine.recheck()
    await rig.engine.recheck()
    labels = ("Draft a handoff", "Draft literature notes")
    for key, label in zip(DRAFT_KEYS, labels, strict=True):
        found = rig.agenda.item(SCOPE, key)
        assert found is not None
        assert (found.state, found.kind, found.mission_id) == ("open", "suggest", None)
        (draft,) = [action for action in found.body.actions if action.type != "open_context"]
        assert (draft.type, draft.label, draft.session_id) == ("draft_chat", label, LOOKED_AT)
    assert rig.model.requests and {row["work"] for row in rig.rows("decision_episodes")} == {"none"}
    assert starts == [] and rig.host.calls == [] and rig.rows("decision_missions") == []


async def test_message_drafts_stop_at_their_daily_limit_across_workspaces(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a limit of one, the day's first new draft lists; then the chat's second new draft
    lists not, while the one raised stays open, and neither does another workspace's, as the
    day's count spans workspaces; the next day the waiting draft lists."""
    rig.values["decision.limit.message_draft"] = 1
    owner_reads(rig, monkeypatch)
    reads = draft_reads(rig, monkeypatch)
    rig.observation = drafting(turn=None)
    await rig.engine.recheck()
    assert state_of(rig, HANDOFF) == "open"

    rig.observation = drafting()
    await rig.engine.recheck()
    assert [state_of(rig, key) for key in DRAFT_KEYS] == ["open", None]

    rig.observation = replace(drafting(turn=None, chat=OTHER_CHAT), scope=OTHER_SCOPE)
    await rig.engine.recheck()
    assert rig.agenda.item(OTHER_SCOPE, item_key("context_handoff", OTHER_CHAT)) is None

    rig.clock.advance(timedelta(days=1).total_seconds(), monotonic=False)
    rig.observation = drafting()
    await rig.engine.recheck()
    assert [state_of(rig, key) for key in DRAFT_KEYS] == ["open", "open"]
    next_day = (TODAY + timedelta(days=1)).isoformat()
    assert [day for name, _, day in reads if name == "today"] == [DAY] * 3 + [next_day]


@pytest.mark.parametrize("end", ["cleared", "lower", "next_reply"])
async def test_a_message_draft_closes_when_its_fact_clears(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, end: str
) -> None:
    """A draft goes stale once the page stops reporting its fact: no part reported, the chat's
    context share below 70%, or a later reply in place of the one that fetched pages, whose own
    draft lists in its place."""
    owner_reads(rig, monkeypatch)
    rig.observation = drafting()
    await rig.engine.recheck()
    assert [state_of(rig, key) for key in DRAFT_KEYS] == ["open", "open"]

    if end == "cleared":
        rig.observation = drafting(None, None)
    elif end == "lower":
        rig.observation = drafting(69, None)
    else:
        rig.observation = drafting(turn=NEXT_TURN)
    await rig.engine.recheck()
    handoff = "open" if end == "next_reply" else "stale"
    assert [state_of(rig, key) for key in DRAFT_KEYS] == [handoff, "stale"]
    later = state_of(rig, item_key("source_note", LOOKED_AT, NEXT_TURN))
    assert later == ("open" if end == "next_reply" else None)


@pytest.mark.parametrize(
    ("case", "listed"),
    [
        ("paused", False),
        ("learning_off", True),
        ("tried_again", True),
        ("before_the_window", True),
        ("before_a_reset", True),
    ],
)
async def test_a_paused_message_draft_type_lists_no_offer(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str, listed: bool
) -> None:
    """With a draft offer, the cycle reads the scope's message drafts since the later of 30 days
    ago and the last Reset, off the event loop, under the stored "message_drafts" Try again
    stamps. Ten handoffs closed unused pause that type: its offer is not listed, while the
    literature-note draft still lists. Learning off, a Try again since, or rows before the window
    or a Reset pause nothing."""
    rows = answered_rows("context_handoff", *["nothing"] * 10)
    since = WALL - SECOND_LOOK_WINDOW
    if case == "learning_off":
        rig.values["decision.learning"] = False
    elif case == "tried_again":
        value = second_looks.restarted(
            None,
            look="context_handoff",
            now=WALL - timedelta(minutes=30),
            looks=MESSAGE_DRAFTS,
            kinds=(),
        )
        rig.store.set_json(SCOPE, "message_drafts", value, WALL)
    elif case == "before_the_window":
        rows = answered_rows("context_handoff", *["nothing"] * 10, at=since - timedelta(seconds=1))
    elif case == "before_a_reset":
        since = WALL - timedelta(minutes=30)
        rig.store.forget_learning(SCOPE, restart_at=since)
    owner_reads(rig, monkeypatch)
    reads = draft_reads(rig, monkeypatch, rows)
    rig.observation = drafting()
    await rig.engine.recheck()
    assert [read for read in reads if read[0] == "rows"] == [("rows", SCOPE, since)]
    assert state_of(rig, HANDOFF) == ("open" if listed else None)
    assert state_of(rig, SOURCE_NOTE) == "open"
    assert rig.rows("decision_missions") == []


async def test_a_stopped_message_draft_type_takes_no_room_from_the_other(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a limit of three, a literature-note draft lists and the user picks Stop these on
    it; the next two replies that fetch pages store no literature-note draft, so the day's count
    still reads one, and the handoff lists once the chat reports 80%."""
    rig.values["decision.limit.message_draft"] = 3
    owner_reads(rig, monkeypatch)
    turns = [f"00000000-0000-4000-8000-0000000000e{n}" for n in range(3)]
    rig.observation = drafting(None, turns[0])
    await rig.engine.recheck()
    first = rig.agenda.item(SCOPE, item_key("source_note", LOOKED_AT, turns[0]))
    assert first is not None and first.state == "open"
    rig.store.feedback(
        SCOPE,
        episode_id=first.episode_id,
        value="mute",
        reason=None,
        source="launcher",
        now=rig.clock.wall(),
    )
    for turn in turns[1:]:
        rig.observation = drafting(None, turn)
        await rig.engine.recheck()
        assert rig.agenda.item(SCOPE, item_key("source_note", LOOKED_AT, turn)) is None
    assert rig.engine._stats.drafts_today(SCOPE, DAY, ())[0] == 1

    rig.observation = drafting(80, None)
    await rig.engine.recheck()
    assert state_of(rig, HANDOFF) == "open"


async def test_a_draft_held_silent_keeps_the_day_within_its_limit(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a limit of one, the arbiter first holds the handoff silent: it still takes the day's
    slot, so a literature-note draft is admitted on neither that cycle nor the next, and the
    handoff, listed once the arbiter lets it, is the only message draft the day lists."""
    owner_reads(rig, monkeypatch)
    rig.values["decision.limit.message_draft"] = 1
    decide = arbiter.decide
    hold = {"silent": True}

    def decide_held(inp: Any) -> Any:
        out = decide(inp)
        if hold["silent"] and inp.candidate.type == "context_handoff":
            return replace(out, level="silent")
        return out

    monkeypatch.setattr(arbiter, "decide", decide_held)
    rig.observation = drafting(80, None)
    await rig.engine.recheck()
    held = rig.agenda.item(SCOPE, HANDOFF)
    assert held is not None and held.level == "silent"

    rig.observation = drafting(80, TURN)
    await rig.engine.recheck()
    assert state_of(rig, SOURCE_NOTE) is None
    hold["silent"] = False
    await rig.engine.recheck()
    listed = rig.agenda.item(SCOPE, HANDOFF)
    assert listed is not None and listed.level != "silent"
    assert state_of(rig, SOURCE_NOTE) is None
    assert rig.engine._stats.drafts_today(SCOPE, DAY, ())[0] == 1


@pytest.mark.parametrize("case", ["no_offer", "not_the_owners"])
async def test_no_message_draft_reads_without_an_offer(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """A chat in view at 69% whose last reply fetched no page offers no draft, so nothing is
    read for drafts; drafts for a chat that is not the user's read neither the yields nor the
    day's count once the `owner_chat` read drops them."""
    asked = owner_reads(rig, monkeypatch, mine=False)
    reads = draft_reads(rig, monkeypatch)
    rig.observation = drafting(69, None) if case == "no_offer" else drafting()
    await rig.engine.recheck()
    assert asked == ([] if case == "no_offer" else [LOOKED_AT])
    assert reads == [] and rig.rows("decision_items") == []


@pytest.mark.parametrize("read", ["owner", "rows", "today"])
async def test_a_failed_message_draft_read_fails_the_cycle(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, read: str
) -> None:
    """A failed read of whose chat it is, of the drafts' yields or of the day's count fails the
    cycle: nothing is listed, not even the task due beside the drafts."""

    def broken(*args: object) -> Any:
        raise DecisionLoopError("unavailable")

    if read == "owner":
        monkeypatch.setattr(rig.missions, "owner_chat", broken)
    else:
        owner_reads(rig, monkeypatch)
        name = "draft_rows" if read == "rows" else "drafts_today"
        monkeypatch.setattr(rig.engine._stats, name, broken)
    rig.observation = replace(drafting(), vault=observation(task("Task a")).vault)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await rig.engine.recheck()
    assert rig.rows("decision_items") == []
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert messages == ["decision cycle failed at draft_admission: unavailable"]


async def test_a_halt_during_the_message_draft_admission_stops_the_cycle(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recheck past `RECHECK_S` abandons the message-draft admission, whose day's-count read a
    worker thread still holds, and lists nothing."""
    started, release = threading.Event(), threading.Event()
    owner_reads(rig, monkeypatch)
    stats = rig.engine._stats
    real = stats.drafts_today

    def blocked(*args: Any) -> Any:
        started.set()
        release.wait(HANG_GUARD_S)
        return real(*args)

    monkeypatch.setattr(stats, "drafts_today", blocked)
    rig.observation = drafting()
    recheck = asyncio.ensure_future(rig.engine.recheck())
    try:
        async with asyncio.timeout(HANG_GUARD_S):  # a worker thread sets no `rig.changed`
            while not (started.is_set() and rig.clock.pending == 1):
                await asyncio.sleep(0)
        rig.clock.advance(RECHECK_S)
        await asyncio.wait_for(recheck, HANG_GUARD_S)
    finally:
        release.set()
    assert rig.rows("decision_items") == []


# ---- saved checks --------------------------------------------------------

GARDEN_APP = "00000000-0000-4000-8000-0000000000f1"
SHED_APP = "00000000-0000-4000-8000-0000000000f2"
HEAD_A, HEAD_B, HEAD_C = "a" * 40, "b" * 40, "c" * 40
GARDEN_CHECKS = ("00000000-0000-4000-8000-0000000000f3", "00000000-0000-4000-8000-0000000000f4")
SHED_CHECKS = ("00000000-0000-4000-8000-0000000000f5",)
WAITING = CheckFolder(GARDEN_APP, "garden-app", HEAD_A, "waiting", GARDEN_CHECKS)
SHED = CheckFolder(SHED_APP, "shed-app", HEAD_B, "waiting", SHED_CHECKS)
CHECKS = item_key("commit_checks", GARDEN_APP, HEAD_A)
SHED_KEY = item_key("commit_checks", SHED_APP, HEAD_B)


class ThreadedCheckHost(FakeCheckHost):
    """`FakeCheckHost` that also notes the thread of each read."""

    def __init__(self, look: CheckLook) -> None:
        super().__init__(look)
        self.threads: list[int] = []

    def read(self, now: datetime) -> CheckLook:
        self.threads.append(threading.get_ident())
        return super().read(now)


def checking(
    rig: Rig,
    *folders: CheckFolder,
    failures: tuple[str, ...] = (),
    detect: Detect = detectors.detect,
) -> ThreadedCheckHost:
    """Rebuild the rig's engine with a check host answering `folders`."""
    host = ThreadedCheckHost(CheckLook(folders, failures))
    rig.checks = host
    rig.engine = rig.build(rig.keys, detect)
    return host


def check_starts(host: FakeCheckHost) -> list[tuple[Any, ...]]:
    return [call for call in host.calls if call[0] == "start"]


def checks_today(rig: Rig, day: str = DAY) -> int:
    return rig.engine._stats.drafts_today(SCOPE, day, (), types=SAVED_CHECKS)[0]


@pytest.mark.parametrize(
    ("case", "reads"),
    [
        ("ordinary", True),
        ("grant_off", False),
        ("unsupported", False),
        ("protected", False),
        ("read_only", False),
        ("unknown_policy", False),
        ("folder", False),
        ("chat_only", False),
    ],
)
async def test_no_check_read_while_off_unsupported_or_outside_an_ordinary_vault(
    rig: Rig, case: str, reads: bool
) -> None:
    """The saved checks are read once a check, off the event loop, at the cycle's clock read, in
    an Ordinary vault on a host that runs checks while Run my saved checks is not Off, and the
    read's failure words join the observation's. Grant Off asks the host nothing; a host that
    runs no checks is asked only that; a Protected or read-only vault, a policy not read, and a
    folder or chat-only launch (here even with an ordinary policy) read nothing and list no
    item."""
    seen: list[Observation] = []

    def detect(obs: Observation, *rest: Any) -> Sequence[Candidate]:
        seen.append(obs)
        return detectors.detect(obs, *rest)

    rig.values["decision.grant.check"] = "off" if case == "grant_off" else "ask"
    policies = {"protected": "protected", "read_only": "existing_readonly", "unknown_policy": None}
    if case in policies:
        rig.observation = replace(observation(), policy=policies[case])
    elif case in ("folder", "chat_only"):
        rig.observation = replace(observation(), kind=case)
    host = checking(rig, WAITING, failures=("checks_partial",), detect=detect)
    host.is_supported = case != "unsupported"
    await rig.engine.recheck()
    (obs,) = seen
    if reads:
        assert host.calls == [("supported",), ("read", WALL)]
        assert host.threads[0] != threading.get_ident()
        assert (obs.checks, obs.failures) == (host.look, ("checks_partial",))
        assert state_of(rig, CHECKS) == "open"
    else:
        asked = [("supported",)] if case == "unsupported" else []
        assert (host.calls, obs.checks, obs.failures) == (asked, None, ())
        assert state_of(rig, CHECKS) is None


async def test_saved_checks_stop_at_their_daily_limit_across_workspaces(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a limit of one, the arbiter first holds garden-app's new commit silent: it still
    takes the day's slot, so shed-app's waiting commit lists on neither that cycle nor the next,
    while garden-app's item stays once its checks run; another workspace lists none either, as
    the day's count spans workspaces; the next day shed-app's commit lists."""
    rig.values["decision.limit.check"] = 1
    decide = arbiter.decide
    hold = {"silent": True}

    def decide_held(inp: Any) -> Any:
        out = decide(inp)
        if hold["silent"] and inp.candidate.type in SAVED_CHECKS:
            return replace(out, level="silent")
        return out

    monkeypatch.setattr(arbiter, "decide", decide_held)
    host = checking(rig, WAITING)
    await rig.engine.recheck()
    held = rig.agenda.item(SCOPE, CHECKS)
    assert held is not None and (held.state, held.level) == ("open", "silent")

    hold["silent"] = False
    host.look = CheckLook((WAITING, SHED))
    await rig.engine.recheck()
    assert [state_of(rig, key) for key in (CHECKS, SHED_KEY)] == ["open", None]

    host.look = CheckLook((replace(WAITING, phase="running", pending=()), SHED))
    await rig.engine.recheck()
    running = rig.agenda.item(SCOPE, CHECKS)
    assert running is not None and (running.state, running.kind) == ("open", "inform")
    assert state_of(rig, SHED_KEY) is None

    rig.observation = replace(observation(), scope=OTHER_SCOPE)
    host.look = CheckLook((SHED,))
    await rig.engine.recheck()
    assert rig.agenda.item(OTHER_SCOPE, SHED_KEY) is None
    assert checks_today(rig) == 1

    rig.clock.advance(timedelta(days=1).total_seconds(), monotonic=False)
    rig.observation = observation()
    host.look = CheckLook((replace(WAITING, phase="passed", pending=()), SHED))
    await rig.engine.recheck()
    assert [state_of(rig, key) for key in (CHECKS, SHED_KEY)] == ["open", "open"]


@pytest.mark.parametrize("phase", ["running", "passed", "failed", "stopped"])
async def test_a_result_or_running_folder_raises_no_new_item(rig: Rig, phase: str) -> None:
    """With the day's limit free, a folder first read with its checks running or ended raises no
    item: only a waiting folder raises a new one."""
    checking(rig, replace(WAITING, phase=phase, pending=()))
    await rig.engine.recheck()
    assert rig.rows("decision_items") == [] and checks_today(rig) == 0


@pytest.mark.parametrize("case", ["stopped", "paused"])
async def test_a_stopped_or_paused_check_type_takes_no_slot(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """With a limit of two, garden-app's new commit lists; then the user picks Stop these on it,
    or learning pauses the type (ten turned down, read as saved-check rows that leave an item
    closed unanswered out): shed-app's waiting commit stores no item, held back or not, so the
    day's count still reads one."""
    rig.values["decision.limit.check"] = 2
    host = checking(rig, WAITING)
    await rig.engine.recheck()
    first = rig.agenda.item(SCOPE, CHECKS)
    assert first is not None and first.state == "open"
    reads: list[tuple[Any, ...]] = []
    if case == "stopped":
        rig.store.feedback(
            SCOPE,
            episode_id=first.episode_id,
            value="mute",
            reason=None,
            source="launcher",
            now=rig.clock.wall(),
        )
    else:
        rows = answered_rows("commit_checks", *["rejected:other"] * 10)

        def draft_rows(scope: str, since: datetime, **family: Any) -> list[LookRow]:
            reads.append((scope, since, family))
            return rows

        monkeypatch.setattr(rig.engine._stats, "draft_rows", draft_rows)
    host.look = CheckLook((WAITING, SHED))
    await rig.engine.recheck()
    assert rig.agenda.item(SCOPE, SHED_KEY) is None and checks_today(rig) == 1
    if case == "paused":
        family = {"types": SAVED_CHECKS, "closed": ""}
        assert reads == [(SCOPE, WALL - SECOND_LOOK_WINDOW, family)]


async def test_a_halt_during_the_check_read_stops_the_cycle(rig: Rig) -> None:
    """A recheck past `RECHECK_S` abandons the saved-check read, which a worker thread still
    holds, and commits nothing."""
    started, release = threading.Event(), threading.Event()

    class BlockedCheckHost(FakeCheckHost):
        def read(self, now: datetime) -> CheckLook:
            started.set()
            release.wait(HANG_GUARD_S)
            return super().read(now)

    rig.checks = BlockedCheckHost(CheckLook((WAITING,)))
    rig.engine = rig.build(rig.keys)
    rig.observation = observation(task("Task a"))
    recheck = asyncio.ensure_future(rig.engine.recheck())
    try:
        async with asyncio.timeout(HANG_GUARD_S):  # a worker thread sets no `rig.changed`
            while not (started.is_set() and rig.clock.pending == 1):
                await asyncio.sleep(0)
        rig.clock.advance(RECHECK_S)
        await asyncio.wait_for(recheck, HANG_GUARD_S)
    finally:
        release.set()
    assert rig.rows("decision_episodes") == []


async def test_a_halt_during_the_check_admission_stops_the_cycle(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recheck past `RECHECK_S` abandons the saved-check admission, whose day's-count read a
    worker thread still holds, and lists nothing."""
    started, release = threading.Event(), threading.Event()
    checking(rig, WAITING)
    stats = rig.engine._stats
    real = stats.drafts_today

    def blocked(*args: Any, **family: Any) -> Any:
        started.set()
        release.wait(HANG_GUARD_S)
        return real(*args, **family)

    monkeypatch.setattr(stats, "drafts_today", blocked)
    rig.observation = observation(task("Task a"))
    recheck = asyncio.ensure_future(rig.engine.recheck())
    try:
        async with asyncio.timeout(HANG_GUARD_S):  # a worker thread sets no `rig.changed`
            while not (started.is_set() and rig.clock.pending == 1):
                await asyncio.sleep(0)
        rig.clock.advance(RECHECK_S)
        await asyncio.wait_for(recheck, HANG_GUARD_S)
    finally:
        release.set()
    assert rig.rows("decision_items") == []


async def test_a_failed_check_admission_read_fails_the_cycle(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed read of the day's saved-check count fails the cycle: nothing is listed, not even
    the task due beside the folder."""

    def broken(*args: object, **family: object) -> Any:
        raise DecisionLoopError("unavailable")

    checking(rig, WAITING)
    monkeypatch.setattr(rig.engine._stats, "drafts_today", broken)
    rig.observation = observation(task("Task a"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await rig.engine.recheck()
    assert rig.rows("decision_items") == []
    messages = [record.getMessage() for record in caplog.records if record.name == LOGGER]
    assert messages == ["decision cycle failed at check_admission: unavailable"]


async def test_allowed_runs_a_waiting_folders_checks_at_that_check(rig: Rig) -> None:
    """With no model key, under Allowed, the check that first reads garden-app's waiting commit
    lists it as a suggestion offering only its folder in Code, decided with work run, and starts
    its pending checks through the check host at that commit, waking the clock; no mission is
    asked for or stored and the model is asked nothing. The next check reads the checks running:
    the item informs, offers Stop, and starts nothing more."""
    rig.values["decision.grant.check"] = "allowed"
    host = checking(rig, WAITING)
    await rig.engine.recheck()
    assert check_starts(host) == [("start", GARDEN_APP, HEAD_A, GARDEN_CHECKS)]
    found = rig.agenda.item(SCOPE, CHECKS)
    assert found is not None
    assert (found.state, found.kind, found.level) == ("open", "suggest", "list")
    assert [(a.type, a.workspace_id) for a in found.body.actions] == [("open_code", GARDEN_APP)]
    assert [row["work"] for row in rig.rows("decision_episodes")] == ["run"]
    assert rig.engine._wake_due is not None  # the start woke the clock
    assert rig.rows("decision_missions") == [] and rig.model.requests == []

    host.look = CheckLook((replace(WAITING, phase="running", pending=()),))
    await rig.engine.recheck()
    assert len(check_starts(host)) == 1
    running = rig.agenda.item(SCOPE, CHECKS)
    assert running is not None and running.kind == "inform"
    actions = [(a.type, a.label, a.workspace_id) for a in running.body.actions]
    assert actions == [
        ("open_code", "Open in Code", GARDEN_APP),
        ("stop_checks", "Stop", GARDEN_APP),
    ]


async def test_ask_first_offers_run_checks_and_starts_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under Ask first the waiting commit lists as a suggestion offering its folder in Code,
    then Run checks, and VIKI starts nothing, whatever the run grant."""
    rig.values["decision.grant.check"] = "ask"
    rig.values["decision.grant.run"] = "allowed"
    starts = count_starts(rig, monkeypatch)
    host = checking(rig, WAITING)
    await rig.engine.recheck()
    await rig.engine.recheck()
    found = rig.agenda.item(SCOPE, CHECKS)
    assert found is not None
    assert (found.state, found.kind, found.level) == ("open", "suggest", "list")
    actions = [(a.type, a.label, a.workspace_id) for a in found.body.actions]
    assert actions == [
        ("open_code", "Open in Code", GARDEN_APP),
        ("run_checks", "Run checks", GARDEN_APP),
    ]
    assert check_starts(host) == [] and starts == [] and rig.rows("decision_missions") == []


@pytest.mark.parametrize(
    "change",
    [
        {"decision.grant.check": "off"},
        {"decision.grant.check": "ask"},
        {"decision.enabled": False},
        {"decision.paused_until": stamp(WALL + timedelta(hours=1))},
        {"decision.limit.check": 0},
        {"notifications.quiet_start": "17:00", "notifications.quiet_end": "19:00"},
    ],
    ids=["grant_off", "grant_ask", "decision_off", "paused", "limit_0", "quiet"],
)
async def test_check_starts_read_the_settings_again(rig: Rig, change: dict[str, object]) -> None:
    """The user changes a setting while the cycle reads the saved checks: the item still lists
    from the cycle's own settings, but no check starts, as the start reads them again. A limit
    lowered below the day's count holds the start too."""
    rig.values["decision.grant.check"] = "allowed"
    host = checking(rig, WAITING)
    read = host.read

    def changing(now: datetime) -> CheckLook:
        rig.values.update(change)
        return read(now)

    host.read = changing  # type: ignore[method-assign]
    await rig.engine.recheck()
    assert state_of(rig, CHECKS) == "open"
    assert check_starts(host) == []


async def test_a_halt_during_a_check_start_stops_the_cycle(rig: Rig) -> None:
    """A recheck past `RECHECK_S` stops waiting on a check start still in progress, which the
    host is left to settle, and asks for no other start."""
    gate = asyncio.Event()
    cancelled: list[str] = []

    class SlowCheckHost(FakeCheckHost):
        async def start(
            self,
            workspace_id: str,
            head: str,
            check_ids: Sequence[str],
            *,
            stops: int | None = None,
        ) -> int:
            self.calls.append(("start", workspace_id, head, tuple(check_ids)))
            try:
                await gate.wait()
            except asyncio.CancelledError:
                cancelled.append(workspace_id)
                raise
            return 1

    rig.values["decision.grant.check"] = "allowed"
    host = SlowCheckHost(CheckLook((WAITING, SHED)))
    rig.checks = host
    rig.engine = rig.build(rig.keys)
    recheck = asyncio.ensure_future(rig.engine.recheck())
    async with asyncio.timeout(HANG_GUARD_S):  # a start sets no `rig.changed`
        while not (check_starts(host) and rig.clock.pending == 1):
            await asyncio.sleep(0)
    rig.clock.advance(RECHECK_S)
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    (first,) = check_starts(host)
    assert cancelled == [first[1]]
    assert [state_of(rig, key) for key in (CHECKS, SHED_KEY)] == ["open", "open"]


async def test_stop_all_between_two_folders_starts_no_further_folder(rig: Rig) -> None:
    """Under Allowed with two waiting folders, the user's Stop all lands while the first
    folder's checks start: the second folder's start carries the count the cycle read before the
    first, so the host starts nothing there."""

    class StoppingCheckHost(FakeCheckHost):
        async def start(
            self,
            workspace_id: str,
            head: str,
            check_ids: Sequence[str],
            *,
            stops: int | None = None,
        ) -> int:
            answer = await super().start(workspace_id, head, check_ids, stops=stops)
            if len(self.given_stops) == 1:
                await self.stop_all()  # the user's Stop all, between the two folders
            return answer if stops == self.stops() else 0

    rig.values["decision.grant.check"] = "allowed"
    host = StoppingCheckHost(CheckLook((WAITING, SHED)))
    rig.checks = host
    rig.engine = rig.build(rig.keys)
    await rig.engine.recheck()
    assert sorted(check_starts(host)) == [
        ("start", GARDEN_APP, HEAD_A, GARDEN_CHECKS),
        ("start", SHED_APP, HEAD_B, SHED_CHECKS),
    ]
    assert host.given_stops == [0, 0] and host.stops() == 1
    assert [state_of(rig, key) for key in (CHECKS, SHED_KEY)] == ["open", "open"]


async def test_a_stop_all_during_a_cycle_starts_no_check_in_it(rig: Rig) -> None:
    """Under Allowed, the user's Stop all lands after the cycle began, while it reads the saved
    checks: the start carries the count the cycle read as it began, so the host starts nothing
    and nothing wakes the clock."""

    class StoppedCheckHost(FakeCheckHost):
        def read(self, now: datetime) -> CheckLook:
            self.stop_alls += 1  # the user's Stop all, once the cycle began
            return super().read(now)

        async def start(
            self,
            workspace_id: str,
            head: str,
            check_ids: Sequence[str],
            *,
            stops: int | None = None,
        ) -> int:
            answer = await super().start(workspace_id, head, check_ids, stops=stops)
            return answer if stops == self.stops() else 0

    rig.values["decision.grant.check"] = "allowed"
    host = StoppedCheckHost(CheckLook((WAITING,)))
    rig.checks = host
    rig.engine = rig.build(rig.keys)
    await rig.engine.recheck()
    assert check_starts(host) == [("start", GARDEN_APP, HEAD_A, GARDEN_CHECKS)]
    assert host.given_stops == [0] and host.stops() == 1
    assert rig.engine._wake_due is None  # nothing started, so nothing woke the clock
    assert state_of(rig, CHECKS) == "open"


async def test_quiet_hours_hold_vikis_check_runs_but_not_the_brake(rig: Rig) -> None:
    """Quiet hours hold VIKI's check runs, which are not remembered and start at the first check
    after the hold ends; the plan brake holds none, as a check spends no plan."""
    rig.values["decision.grant.check"] = "allowed"
    rig.values["notifications.quiet_start"] = "17:00"  # WALL is 18:00 in JST
    rig.values["notifications.quiet_end"] = "19:00"
    host = checking(rig, WAITING)
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, CHECKS) == "open" and check_starts(host) == []

    rig.values["notifications.quiet_start"] = rig.values["notifications.quiet_end"] = None
    rig.reading = plan_reading(use=0.8, reset=at_wall(3600))
    await rig.engine.recheck()
    assert check_starts(host) == [("start", GARDEN_APP, HEAD_A, GARDEN_CHECKS)]


async def test_saved_checks_are_never_missions(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    """With every grant Allowed and the model answering run, a waiting folder's checks start
    only through the check host: no mission is asked for or stored."""
    rig.keys.save(KEY)
    rig.model.answers = dict(RUN)
    for grant in ("prepare", "run", "check"):
        rig.values[f"decision.grant.{grant}"] = "allowed"
    starts = count_starts(rig, monkeypatch)
    host = checking(rig, WAITING)
    await rig.engine.recheck()
    assert rig.model.requests and [row["work"] for row in rig.rows("decision_episodes")] == ["run"]
    assert check_starts(host) == [("start", GARDEN_APP, HEAD_A, GARDEN_CHECKS)]
    assert (starts, rig.rows("decision_missions")) == ([], [])


async def test_a_refused_check_start_is_skipped(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    """Under Allowed, the host refuses each waiting folder's start: each is asked once, both
    items stay listed, the cycle does not fail, and nothing wakes the clock."""
    rig.values["decision.grant.check"] = "allowed"
    host = checking(rig, WAITING, SHED)
    host.refuse = "not_applicable"
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await rig.engine.recheck()
    assert sorted(check_starts(host)) == [
        ("start", GARDEN_APP, HEAD_A, GARDEN_CHECKS),
        ("start", SHED_APP, HEAD_B, SHED_CHECKS),
    ]
    assert [state_of(rig, key) for key in (CHECKS, SHED_KEY)] == ["open", "open"]
    assert [record for record in caplog.records if record.name == LOGGER] == []
    assert rig.engine._wake_due is None  # no start, no wake


async def test_a_moved_folder_closes_its_item(rig: Rig) -> None:
    """Once garden-app moves to a new commit, its old commit's item goes stale and the new one
    lists under its own key; a folder the read no longer answers closes its item too."""
    host = checking(rig, WAITING)
    await rig.engine.recheck()
    assert state_of(rig, CHECKS) == "open"

    host.look = CheckLook((replace(WAITING, head=HEAD_C),))
    await rig.engine.recheck()
    moved = item_key("commit_checks", GARDEN_APP, HEAD_C)
    assert [state_of(rig, key) for key in (CHECKS, moved)] == ["stale", "open"]

    host.look = CheckLook(())
    await rig.engine.recheck()
    assert state_of(rig, moved) == "stale"


# ---- inbox suggestions ---------------------------------------------------

LISTED = tuple(
    (f"00-inbox/capture-{n}.md", stamp(WALL - timedelta(hours=1, minutes=n))) for n in range(4)
)
# A fifth capture, saved 20 minutes ago: a new state of the inbox, settled too.
MORE = (*LISTED, ("00-inbox/capture-9.md", stamp(WALL - timedelta(minutes=20))))


def triage_key(listed: Sequence[tuple[str, str]], scope: str = SCOPE) -> str:
    return item_key("inbox_triage", scope, canonical_json([[ref, at] for ref, at in listed]))


TRIAGE = triage_key(LISTED)
TRIAGE_MORE = triage_key(MORE)


def inbox_observation(listed: tuple[tuple[str, str], ...] = LISTED, **changes: Any) -> Observation:
    """The active vault with these captures listed in `00-inbox/`, more than three: pressing."""
    obs = observation()
    assert obs.vault is not None
    inbox = replace(obs.vault.inbox, count=len(listed), checked=len(listed))
    return replace(obs, vault=replace(obs.vault, inbox=inbox), inbox_listed=listed, **changes)


class RememberingInboxHost(FakeInboxHost):
    """`FakeInboxHost` that keeps each key it is asked to start in its look, as `SuggestionHost`
    does: a started run reads running under its run id, a refusal other than busy reads "". It
    notes the thread of each read."""

    def __init__(self, *, refuse: str | None = None, persistent: bool = False) -> None:
        super().__init__(refuse=refuse, persistent=persistent)
        self.threads: list[int] = []

    def read(self, workspace_id: str) -> InboxLook:
        self.threads.append(threading.get_ident())
        return super().read(workspace_id)

    async def start(self, workspace_id: str, item_key: str) -> str:
        look = self.look
        try:
            run_id = await super().start(workspace_id, item_key)
        except DecisionLoopError as refusal:
            if refusal.code != "busy":
                self.look = replace(look, started={**look.started, item_key: ""})
            raise
        run = InboxRun(run_id, "running", None, stamp(WALL))
        self.look = replace(look, run=run, started={**look.started, item_key: run_id})
        return run_id


def inboxing(
    rig: Rig, *, refuse: str | None = None, detect: Detect = detectors.detect
) -> RememberingInboxHost:
    """Rebuild the rig's engine with an inbox host, over a pressing inbox of four captures."""
    host = RememberingInboxHost(refuse=refuse)
    rig.inbox = host
    rig.observation = inbox_observation()
    rig.engine = rig.build(rig.keys, detect)
    return host


def inbox_starts(host: FakeInboxHost) -> list[tuple[Any, ...]]:
    return [call for call in host.calls if call[0] == "start"]


def triage_today(rig: Rig, day: str = DAY) -> int:
    return rig.engine._stats.drafts_today(SCOPE, day, (), types=INBOX_TRIAGE)[0]


def ended(run_id: str, state: str, reason: str | None = None) -> InboxLook:
    """The host's look once the run started for `TRIAGE` reached `state`."""
    return InboxLook(InboxRun(run_id, state, reason, stamp(WALL)), {}, {TRIAGE: run_id})


@pytest.mark.parametrize(
    ("case", "reads"),
    [
        ("ordinary", True),
        ("protected", True),
        ("grant_off", False),
        ("unwatched", False),
        ("read_only", False),
        ("unknown_policy", False),
        ("folder", False),
        ("chat_only", False),
    ],
)
async def test_no_inbox_read_while_off_unwatched_or_outside_a_prepare_vault(
    rig: Rig, case: str, reads: bool
) -> None:
    """The inbox host is read once a check, on the event loop, in an Ordinary or Protected vault
    while Inbox is watched and Suggest destinations for my inbox is not Off, and its item lists.
    Grant Off, Inbox unwatched, a read-only vault, a policy not read, and a folder or chat-only
    launch (here even with an ordinary policy) read nothing and list no item."""
    rig.values["decision.grant.inbox_triage"] = "off" if case == "grant_off" else "ask"
    rig.values["decision.watch.inbox"] = case != "unwatched"
    host = inboxing(rig)
    policies = {"protected": "protected", "read_only": "existing_readonly", "unknown_policy": None}
    if case in policies:
        rig.observation = replace(rig.observation, policy=policies[case])
    elif case in ("folder", "chat_only"):
        rig.observation = replace(rig.observation, kind=case)
    await rig.engine.recheck()
    if reads:
        assert host.calls == [("read", SCOPE)]
        assert host.threads == [threading.get_ident()]
        assert state_of(rig, TRIAGE) == "open"
    else:
        assert host.calls == [] and state_of(rig, TRIAGE) is None


async def test_inbox_suggestions_stop_at_their_daily_limit_across_workspaces(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a limit of one, the arbiter first holds the inbox's item silent: it still takes the
    day's slot, so a new state of the inbox lists on neither that check nor the next, while the
    first state's stored item stays in a later phase; another workspace lists none either, as the
    day's count spans workspaces; the next day the new state lists."""
    rig.values["decision.limit.inbox_triage"] = 1
    rig.values["decision.grant.inbox_triage"] = "ask"
    decide = arbiter.decide
    hold = {"silent": True}

    def decide_held(inp: Any) -> Any:
        out = decide(inp)
        if hold["silent"] and inp.candidate.type in INBOX_TRIAGE:
            return replace(out, level="silent")
        return out

    monkeypatch.setattr(arbiter, "decide", decide_held)
    host = inboxing(rig)
    await rig.engine.recheck()
    held = rig.agenda.item(SCOPE, TRIAGE)
    assert held is not None and (held.state, held.level) == ("open", "silent")

    hold["silent"] = False
    rig.observation = inbox_observation(MORE)
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE_MORE) is None

    rig.observation = inbox_observation()
    host.look = ended("r1", "running")
    await rig.engine.recheck()
    running = rig.agenda.item(SCOPE, TRIAGE)
    assert running is not None and (running.state, running.kind) == ("open", "inform")

    rig.observation = inbox_observation(MORE, scope=OTHER_SCOPE)
    host.look = InboxLook(None, {}, {})
    await rig.engine.recheck()
    assert rig.agenda.item(OTHER_SCOPE, triage_key(MORE, OTHER_SCOPE)) is None
    assert triage_today(rig) == 1

    rig.clock.advance(timedelta(days=1).total_seconds(), monotonic=False)
    rig.observation = inbox_observation(MORE)
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE_MORE) == "open"


@pytest.mark.parametrize("case", ["stopped", "paused"])
async def test_a_stopped_or_paused_triage_type_takes_no_slot(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """With a limit of two, the inbox's item lists; then the user picks Stop these on it, or
    learning pauses the type (ten turned down, read as inbox-suggestion rows that leave an item
    closed unanswered out and read a run that found nothing as such): a new state of the inbox
    stores no item, held back or not, so the day's count still reads one."""
    rig.values["decision.limit.inbox_triage"] = 2
    rig.values["decision.grant.inbox_triage"] = "ask"
    inboxing(rig)
    await rig.engine.recheck()
    first = rig.agenda.item(SCOPE, TRIAGE)
    assert first is not None and first.state == "open"
    reads: list[tuple[Any, ...]] = []
    if case == "stopped":
        rig.store.feedback(
            SCOPE,
            episode_id=first.episode_id,
            value="mute",
            reason=None,
            source="launcher",
            now=rig.clock.wall(),
        )
    else:
        rows = answered_rows("inbox_triage", *["rejected:other"] * 10)

        def draft_rows(scope: str, since: datetime, **family: Any) -> list[LookRow]:
            reads.append((scope, since, family))
            return rows

        monkeypatch.setattr(rig.engine._stats, "draft_rows", draft_rows)
    rig.observation = inbox_observation(MORE)
    await rig.engine.recheck()
    assert rig.agenda.item(SCOPE, TRIAGE_MORE) is None and triage_today(rig) == 1
    if case == "paused":
        family = {"types": INBOX_TRIAGE, "closed": "", "done": "nothing"}
        assert reads == [(SCOPE, WALL - SECOND_LOOK_WINDOW, family)]


async def test_a_run_that_found_nothing_closes_its_item_done(rig: Rig) -> None:
    """Once the run started for the inbox found no destination it could suggest, its item is set
    done rather than going stale, and stays done at the next check; nothing new lists."""
    rig.values["decision.grant.inbox_triage"] = "ask"
    host = inboxing(rig)
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "open"

    host.look = ended("r1", "failed", "no_suggestions")
    await rig.engine.recheck()
    closed = items_of(rig, "inbox_triage")
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "done"
    assert items_of(rig, "inbox_triage") == closed  # one write: a done item is not set again
    assert [row["item_key"] for row in closed] == [TRIAGE]


async def test_a_run_that_found_nothing_leaves_a_stale_item_stale(rig: Rig) -> None:
    """An item gone stale (Suggest destinations for my inbox Off for one check) stays stale once
    its run reads as having found nothing: only an open item is set done."""
    rig.values["decision.grant.inbox_triage"] = "ask"
    host = inboxing(rig)
    await rig.engine.recheck()
    rig.values["decision.grant.inbox_triage"] = "off"
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "stale"

    rig.values["decision.grant.inbox_triage"] = "ask"
    host.look = ended("r1", "failed", "no_suggestions")
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "stale"


async def test_a_halt_during_the_triage_admission_stops_the_cycle(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recheck past `RECHECK_S` abandons the inbox-suggestion admission, whose day's-count read
    a worker thread still holds, and lists nothing."""
    started, release = threading.Event(), threading.Event()
    inboxing(rig)
    stats = rig.engine._stats
    real = stats.drafts_today

    def blocked(*args: Any, **family: Any) -> Any:
        started.set()
        release.wait(HANG_GUARD_S)
        return real(*args, **family)

    monkeypatch.setattr(stats, "drafts_today", blocked)
    recheck = asyncio.ensure_future(rig.engine.recheck())
    try:
        async with asyncio.timeout(HANG_GUARD_S):  # a worker thread sets no `rig.changed`
            while not (started.is_set() and rig.clock.pending == 1):
                await asyncio.sleep(0)
        rig.clock.advance(RECHECK_S)
        await asyncio.wait_for(recheck, HANG_GUARD_S)
    finally:
        release.set()
    assert rig.rows("decision_items") == []


async def test_allowed_starts_a_waiting_inbox_once_through_the_host(rig: Rig) -> None:
    """With no model key, under Allowed (the default), the check that first reads the waiting
    inbox lists it as a suggestion offering only Triage inbox, decided with work prepare, and
    starts its run through the inbox host, waking the clock; no mission is asked for or stored
    and the model is asked nothing. The next check reads the run going: the item informs and
    nothing more starts; once suggestions wait it is ready."""
    host = inboxing(rig)
    await rig.engine.recheck()
    assert inbox_starts(host) == [("start", SCOPE, TRIAGE)]
    found = rig.agenda.item(SCOPE, TRIAGE)
    assert found is not None
    assert (found.state, found.kind, found.level) == ("open", "suggest", "list")
    assert [(a.type, a.label) for a in found.body.actions] == [("open_triage", "Triage inbox")]
    works = [row["work"] for row in rig.rows("decision_episodes") if row["item_key"] == TRIAGE]
    assert works == ["prepare"]
    assert rig.engine._wake_due is not None  # the start woke the clock
    assert rig.rows("decision_missions") == [] and rig.model.requests == []

    await rig.engine.recheck()
    assert len(inbox_starts(host)) == 1
    running = rig.agenda.item(SCOPE, TRIAGE)
    assert running is not None and (running.state, running.kind) == ("open", "inform")

    host.look = ended("r1", "done")
    await rig.engine.recheck()
    ready = rig.agenda.item(SCOPE, TRIAGE)
    assert ready is not None and (ready.state, ready.kind) == ("open", "ready")
    assert len(inbox_starts(host)) == 1


async def test_ask_first_offers_suggest_destinations_and_starts_nothing(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under Ask first the waiting inbox lists as a suggestion offering Triage inbox, then Suggest
    destinations, and VIKI starts nothing, whatever the prepare grant."""
    rig.values["decision.grant.inbox_triage"] = "ask"
    rig.values["decision.grant.prepare"] = "allowed"
    starts = count_starts(rig, monkeypatch)
    host = inboxing(rig)
    await rig.engine.recheck()
    await rig.engine.recheck()
    found = rig.agenda.item(SCOPE, TRIAGE)
    assert found is not None
    assert (found.state, found.kind, found.level) == ("open", "suggest", "list")
    actions = [(a.type, a.label) for a in found.body.actions]
    assert actions == [("open_triage", "Triage inbox"), ("suggest_inbox", "Suggest destinations")]
    assert inbox_starts(host) == [] and starts == [] and rig.rows("decision_missions") == []


@pytest.mark.parametrize(
    "change",
    [
        {"decision.grant.inbox_triage": "off"},
        {"decision.grant.inbox_triage": "ask"},
        {"decision.enabled": False},
        {"decision.paused_until": stamp(WALL + timedelta(hours=1))},
        {"decision.limit.inbox_triage": 0},
        {"notifications.quiet_start": "17:00", "notifications.quiet_end": "19:00"},
        {"decision.plan_brake": "50"},
        {"decision.never_send": json.dumps(["#private"])},
        {"decision.never_send": json.dumps(["00-inbox/"])},
        {"decision.never_send": "not a list"},
        {"decision.watch.inbox": False},
    ],
    ids=[
        "grant_off",
        "grant_ask",
        "decision_off",
        "paused",
        "limit_0",
        "quiet",
        "brake",
        "never_send_tag",
        "never_send_inbox_folder",
        "never_send_invalid",
        "inbox_unwatched",
    ],
)
async def test_inbox_starts_read_the_settings_again(rig: Rig, change: dict[str, object]) -> None:
    """The user changes a setting while the cycle reads the inbox host: the item still lists
    from the cycle's own settings, but no run starts, as the start reads them again. A limit
    lowered below the day's count, or a brake lowered under the plan's use, holds it too, and so
    does a Never send rule added (a tag, or a folder over a capture), a Never send value that
    cannot be read, or Inbox no longer watched."""
    rig.reading = plan_reading(use=0.6, reset=at_wall(3600))  # under the default brake of 80
    host = inboxing(rig)
    read = host.read

    def changing(workspace_id: str) -> InboxLook:
        rig.values.update(change)
        return read(workspace_id)

    host.read = changing  # type: ignore[method-assign]
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "open"
    assert inbox_starts(host) == []


async def test_decision_turned_off_during_the_starts_count_holds_it(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user turns Decision off while the start reads the day's count: the start reads the
    settings after that count, so no run starts."""
    inboxing(rig)
    host = rig.inbox
    assert isinstance(host, RememberingInboxHost)
    stats = rig.engine._stats
    real = stats.drafts_today
    counts: list[str] = []

    def counting(*args: Any, **family: Any) -> Any:
        counts.append(args[0])
        if len(counts) == 2:  # the first is the admission's, the second the start's
            rig.values["decision.enabled"] = False
        return real(*args, **family)

    monkeypatch.setattr(stats, "drafts_today", counting)
    await rig.engine.recheck()
    assert counts == [SCOPE, SCOPE]
    assert state_of(rig, TRIAGE) == "open" and inbox_starts(host) == []


@pytest.mark.parametrize("state", ["done", "running"])
async def test_an_owner_run_since_the_cycles_read_holds_vikis_start(rig: Rig, state: str) -> None:
    """The user's own Ask VIKI to suggest starts (and here ends, or still goes) while the cycle
    waits on the model: the start reads the host again, finds a run other than the one the cycle
    read, and starts nothing, so capture text is not sent a second time."""
    host = inboxing(rig)
    read = host.read
    owner = InboxLook(InboxRun("own", state, None, stamp(WALL)), dict(LISTED), {})

    def overtaken(workspace_id: str) -> InboxLook:
        look = read(workspace_id)
        host.look = owner
        return look

    host.read = overtaken  # type: ignore[method-assign]
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "open"
    assert inbox_starts(host) == []


async def test_a_new_inbox_state_after_a_done_run_starts(rig: Rig) -> None:
    """A vault whose earlier run is done still gets a run for a new state: the cycle's read of
    the host holds that done run, so the start finds it unchanged and goes ahead."""
    host = inboxing(rig)
    await rig.engine.recheck()
    assert inbox_starts(host) == [("start", SCOPE, TRIAGE)]
    host.look = InboxLook(
        InboxRun("r1", "done", None, stamp(WALL - timedelta(minutes=50))), {}, {TRIAGE: "r1"}
    )
    rig.observation = inbox_observation(MORE)
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE_MORE) == "open"
    assert inbox_starts(host)[-1] == ("start", SCOPE, TRIAGE_MORE)


async def test_an_inbox_state_raised_after_the_engine_began_starts(rig: Rig) -> None:
    """An inbox state first raised after the engine was built starts its run, as the check
    after a relaunch does not for a state raised before."""
    host = inboxing(rig)
    rig.clock.advance(60)
    await rig.engine.recheck()
    assert inbox_starts(host) == [("start", SCOPE, TRIAGE)]


async def test_a_relaunch_does_not_start_the_stored_waiting_inbox_again(rig: Rig) -> None:
    """VIKI Agent restarts over an unchanged pressing inbox whose run was done: the new engine
    reads the stored open item, with the new host's memory empty, and starts no run, so the same
    capture text is not sent again on every launch."""
    first = inboxing(rig)
    await rig.engine.recheck()
    assert inbox_starts(first) == [("start", SCOPE, TRIAGE)]

    rig.clock.advance(3600)
    second = RememberingInboxHost()  # nothing started, no run: the host's memory is gone
    rig.inbox = second
    rig.engine = rig.build(rig.keys)
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "open"
    assert inbox_starts(second) == []
    assert triage_today(rig) == 1


async def test_a_relaunch_starts_a_stored_waiting_inbox_a_persistent_host_never_started(
    rig: Rig,
) -> None:
    """Quiet hours hold the run of a pressing inbox until VIKI Agent closes. The next launch's
    host keeps its started keys across restarts and never started this state, so the new engine
    starts it though its item was stored before the engine began."""
    rig.values["notifications.quiet_start"] = "17:00"  # WALL is 18:00 in JST
    rig.values["notifications.quiet_end"] = "19:00"
    first = inboxing(rig)
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "open" and inbox_starts(first) == []

    rig.values["notifications.quiet_start"] = rig.values["notifications.quiet_end"] = None
    rig.clock.advance(60)
    second = RememberingInboxHost(persistent=True)
    rig.inbox = second
    rig.engine = rig.build(rig.keys)
    await rig.engine.recheck()
    assert inbox_starts(second) == [("start", SCOPE, TRIAGE)]


async def test_a_relaunch_does_not_start_a_state_a_persistent_host_started_before(
    rig: Rig,
) -> None:
    """The next launch's host loaded the key it started and that run's end, so the stored open
    item is not started again."""
    first = inboxing(rig)
    await rig.engine.recheck()
    assert inbox_starts(first) == [("start", SCOPE, TRIAGE)]

    rig.clock.advance(3600)
    second = RememberingInboxHost(persistent=True)
    second.look = InboxLook(InboxRun("r1", "done", None, stamp(WALL)), {}, {TRIAGE: "r1"})
    rig.inbox = second
    rig.engine = rig.build(rig.keys)
    await rig.engine.recheck()
    assert inbox_starts(second) == []


async def test_a_limit_of_one_starts_the_first_inbox_run(rig: Rig) -> None:
    """Under Allowed with a limit of one, the day's only inbox suggestion starts its run: the
    day's count already holds the item just listed, which the limit admits."""
    rig.values["decision.limit.inbox_triage"] = 1
    host = inboxing(rig)
    await rig.engine.recheck()
    assert inbox_starts(host) == [("start", SCOPE, TRIAGE)]
    assert triage_today(rig) == 1


async def test_the_plan_brake_and_quiet_hours_hold_vikis_inbox_start(rig: Rig) -> None:
    """Quiet hours, then the plan brake, hold VIKI's inbox run, which is not remembered and starts
    at the first check after both holds end."""
    rig.values["notifications.quiet_start"] = "17:00"  # WALL is 18:00 in JST
    rig.values["notifications.quiet_end"] = "19:00"
    host = inboxing(rig)
    await rig.engine.recheck()
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "open" and inbox_starts(host) == []

    rig.values["notifications.quiet_start"] = rig.values["notifications.quiet_end"] = None
    rig.reading = plan_reading(use=0.8, reset=at_wall(3600))
    await rig.engine.recheck()
    assert state_of(rig, TRIAGE) == "open" and inbox_starts(host) == []

    rig.reading = LOW_USE
    await rig.engine.recheck()
    assert inbox_starts(host) == [("start", SCOPE, TRIAGE)]


@pytest.mark.parametrize("case", ["failed", "refused"])
async def test_a_failed_inbox_run_is_not_started_again(rig: Rig, case: str) -> None:
    """Under Allowed, a run that failed, or a start the host refused and remembered, leaves the
    inbox's item a report that does no work: no later check starts it again for that state."""
    host = inboxing(rig, refuse="not_applicable" if case == "refused" else None)
    await rig.engine.recheck()
    assert inbox_starts(host) == [("start", SCOPE, TRIAGE)]
    if case == "failed":
        host.look = ended("r1", "failed", "deadline")
    host.refuse = None
    await rig.engine.recheck()
    await rig.engine.recheck()
    found = rig.agenda.item(SCOPE, TRIAGE)
    assert found is not None and (found.state, found.kind) == ("open", "report")
    assert len(inbox_starts(host)) == 1


async def test_a_refused_inbox_start_is_skipped(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    """Under Allowed, the host refuses the waiting inbox's start: it is asked once, the item stays
    listed, the cycle does not fail, and nothing wakes the clock."""
    host = inboxing(rig, refuse="busy")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await rig.engine.recheck()
    assert inbox_starts(host) == [("start", SCOPE, TRIAGE)]
    assert state_of(rig, TRIAGE) == "open"
    assert [record for record in caplog.records if record.name == LOGGER] == []
    assert rig.engine._wake_due is None  # no start, no wake


async def test_inbox_suggestions_are_never_missions(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With every grant Allowed and the model answering prepare, a waiting inbox's run starts
    only through the inbox host: no mission is asked for or stored."""
    rig.keys.save(KEY)
    rig.model.answers = dict(PREPARE)
    for grant in ("prepare", "run", "inbox_triage"):
        rig.values[f"decision.grant.{grant}"] = "allowed"
    starts = count_starts(rig, monkeypatch)
    host = inboxing(rig)
    await rig.engine.recheck()
    assert rig.model.requests
    works = [row["work"] for row in rig.rows("decision_episodes") if row["item_key"] == TRIAGE]
    assert works == ["prepare"]
    assert inbox_starts(host) == [("start", SCOPE, TRIAGE)]
    assert (starts, rig.rows("decision_missions")) == ([], [])


async def test_a_halt_during_an_inbox_start_stops_the_cycle(rig: Rig) -> None:
    """A recheck past `RECHECK_S` stops waiting on an inbox start still in progress, which the
    host is left to settle; the item stays listed."""
    gate = asyncio.Event()
    cancelled: list[str] = []

    class SlowInboxHost(FakeInboxHost):
        async def start(self, workspace_id: str, item_key: str) -> str:
            self.calls.append(("start", workspace_id, item_key))
            try:
                await gate.wait()
            except asyncio.CancelledError:
                cancelled.append(item_key)
                raise
            return self.run_id

    host = SlowInboxHost()
    rig.inbox = host
    rig.observation = inbox_observation()
    rig.engine = rig.build(rig.keys)
    recheck = asyncio.ensure_future(rig.engine.recheck())
    async with asyncio.timeout(HANG_GUARD_S):  # a start sets no `rig.changed`
        while not (inbox_starts(host) and rig.clock.pending == 1):
            await asyncio.sleep(0)
    rig.clock.advance(RECHECK_S)
    await asyncio.wait_for(recheck, HANG_GUARD_S)
    assert cancelled == [TRIAGE]
    assert state_of(rig, TRIAGE) == "open"
    assert rig.engine._wake_due is None
