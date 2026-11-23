"""Shared doubles for the Decision loop tests: a model, a clock, a session host and an effective
settings map. Tests import them as `from tests.decision_loop.fakes import ...`.

None of them reaches a network, a process or the real clock.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from viki_agent.decision_loop.contracts import (
    MODEL_IDS,
    Answer,
    CheckFolder,
    CheckLook,
    ChoiceAnswer,
    CodeLook,
    DecisionLoopError,
    InboxLook,
    ModelError,
    ModelReply,
    ModelRequest,
    NoulAnswer,
    ScoreAnswer,
    SeatFacts,
    SeatTeam,
    SeatWaiting,
    canonical_json,
)
from viki_agent.events import Event, SessionClosed, TurnDone

Envelope = dict[str, Any]
Step = ModelError | Mapping[str, Answer]

WALL = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)

# What a question nobody scripted answers: nothing worth bringing up. Every other name is a
# yes/no question answered "no".
QUIET_ANSWERS: Mapping[str, Answer] = {
    "kind": ChoiceAnswer("inform", 1.0, {"inform": 1.0}),
    "work": ChoiceAnswer("none", 1.0, {"none": 1.0}),
    "importance": ScoreAnswer(0.0, 1.0, {0: 1.0}),
}
QUIET_NOUL = NoulAnswer(0.0)

# The effective values of every key `parse_settings` reads, at their registered defaults.
_DEFAULT_VALUES: Mapping[str, object] = {
    "decision.enabled": False,
    "decision.paused_until": "",
    "decision.focus_until": "",
    "decision.send_level": "excerpts",
    "decision.never_send": "[]",
    "decision.watch.tasks": True,
    "decision.watch.inbox": True,
    "decision.watch.vault_changes": True,
    "decision.watch.approvals": True,
    "decision.watch.chats": True,
    "decision.watch.routines": True,
    "decision.watch.improve": True,
    "decision.watch.kanban": True,
    "decision.watch.vault_health": True,
    "decision.watch.messaging": True,
    "decision.cadence": "change",
    "decision.daily_cap_usd": 0.5,
    "decision.nudges_per_hour": 2,
    "decision.alerts": False,
    "decision.in_context": True,
    "decision.model_version": "pinned",
    "decision.grant.prepare": "allowed",
    "decision.grant.run": "ask",
    "decision.grant.team": "ask",
    "decision.grant.look": "allowed",
    "decision.grant.second_look": "allowed",
    "decision.grant.project_plan": "allowed",
    "decision.grant.check": "ask",
    "decision.grant.inbox_triage": "allowed",
    "decision.limit.drafts": 5,
    "decision.limit.chats": 3,
    "decision.limit.changes": 3,
    "decision.plan_brake": "80",
    "decision.limit.team": 4,
    "decision.team.handoffs": False,
    "decision.limit.look": 3,
    "decision.limit.second_look": 3,
    "decision.limit.project_plan": 3,
    "decision.limit.message_draft": 3,
    "decision.limit.check": 3,
    "decision.limit.inbox_triage": 3,
    "decision.learning": True,
    "decision.shadow": "off",
    "decision.launcher_session": "",
    "notifications.quiet_start": None,
    "notifications.quiet_end": None,
}


def settings_values(**overrides: object) -> dict[str, object]:
    """An effective settings map: every key `parse_settings` reads at its default (quiet hours
    unset), with `overrides` applied. Keys are full setting names, so pass them as
    `**{"decision.enabled": True}`; a name outside the map raises KeyError."""
    values = dict(_DEFAULT_VALUES)
    for key, value in overrides.items():
        if key not in values:
            raise KeyError(key)
        values[key] = value
    return values


class FakeModel:
    """`ModelPort` without a network.

    Each `ask` records its request and key, takes the next step queued by `script`, waits for
    `gate` when one is set, then raises the step's `ModelError` or answers every asked name: from
    the step, else from `answers`, else from `QUIET_ANSWERS` (any other name: `QUIET_NOUL`). The
    reply names `reply_model`; its `input_tokens` is the UTF-8 byte count of the request's state
    and questions as canonical JSON, divided by 3 and rounded up.
    """

    def __init__(self, answers: Mapping[str, Answer] | None = None) -> None:
        self.answers: dict[str, Answer] = dict(answers or {})
        self.steps: list[Step] = []
        self.requests: list[ModelRequest] = []
        self.keys: list[str] = []
        self.listed: list[str] = []  # always empty: no caller fills it any more
        self.reply_model = MODEL_IDS["pinned"]
        self.latency_ms = 0
        self.gate: asyncio.Event | None = None
        self.in_flight = 0
        self.most_in_flight = 0
        self.closed = False

    def script(self, *steps: Step) -> None:
        """Queue one step per coming ask, in order: an error is raised, a mapping answers those
        names for that ask only."""
        self.steps.extend(steps)

    async def ask(self, request: ModelRequest, *, key: str) -> ModelReply:
        self.requests.append(request)
        self.keys.append(key)
        step: Step = self.steps.pop(0) if self.steps else {}
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            if self.gate is not None:
                await self.gate.wait()
        finally:
            self.in_flight -= 1
        if isinstance(step, ModelError):
            raise step
        answers = {name: self._answer(name, step) for name in request.questions}
        sent = canonical_json(request.state) + canonical_json(request.questions)
        return ModelReply(
            model=self.reply_model,
            answers=answers,
            input_tokens=math.ceil(len(sent.encode("utf-8")) / 3),
            latency_ms=self.latency_ms,
        )

    def _answer(self, name: str, step: Mapping[str, Answer]) -> Answer:
        if name in step:
            return step[name]
        if name in self.answers:
            return self.answers[name]
        return QUIET_ANSWERS.get(name, QUIET_NOUL)

    async def close(self) -> None:
        self.closed = True


class FakeClock:
    """The scheduler's `Clock` on settable time; it never sleeps and never moves on its own.

    `wait` records each requested duration and returns when its event is set, when `advance`
    brings the monotonic time to its deadline, or when `tick` wakes every pending wait. A
    duration of zero or less returns at once, as `SystemClock.wait` does.
    """

    def __init__(self, wall: datetime = WALL, monotonic: float = 1000.0) -> None:
        self._wall = wall
        self._monotonic = monotonic
        self.waits: list[float] = []
        self._sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def wall(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._monotonic

    @property
    def pending(self) -> int:
        """How many waits are still waiting."""
        return len(self._sleepers)

    def advance(self, seconds: float, *, wall: bool = True, monotonic: bool = True) -> None:
        """Move the clocks forward; every wait whose deadline is reached returns."""
        if wall:
            self._wall += timedelta(seconds=seconds)
        if monotonic:
            self._monotonic += seconds
        for deadline, timer in self._sleepers:
            if deadline <= self._monotonic and not timer.done():
                timer.set_result(None)

    def tick(self) -> None:
        """Wake every pending wait now, without moving time."""
        for _deadline, timer in self._sleepers:
            if not timer.done():
                timer.set_result(None)

    async def wait(self, event: asyncio.Event, seconds: float) -> None:
        self.waits.append(seconds)
        if event.is_set() or seconds <= 0:
            return
        timer: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        sleeper = (self._monotonic + seconds, timer)
        self._sleepers.append(sleeper)
        woken = asyncio.ensure_future(event.wait())
        woken.add_done_callback(lambda _: timer.done() or timer.set_result(None))
        try:
            await timer
        finally:
            woken.cancel()
            self._sleepers.remove(sleeper)


class FakeActor:
    """`SessionActor` as a mission reaches it.

    `send` records the text and answers `send_result`; a truthy answer starts a turn (`busy`).
    `emit` hands an event's envelope to every subscriber, after a turn end or a close has
    cleared `busy`. `interrupt` only counts.
    """

    def __init__(self, session_id: str, *, send_result: bool | None = True) -> None:
        self.session_id = session_id
        self.send_result = send_result
        self.busy = False
        self.sent: list[str] = []
        self.interrupts = 0
        self.subscribers: list[Callable[[Envelope], None]] = []
        self._seq = 0

    def subscribe(self, subscriber: Callable[[Envelope], None]) -> Callable[[], None]:
        self.subscribers.append(subscriber)

        def unsubscribe() -> None:
            if subscriber in self.subscribers:
                self.subscribers.remove(subscriber)

        return unsubscribe

    async def send(self, text: str) -> bool | None:
        self.sent.append(text)
        if self.send_result:
            self.busy = True
        return self.send_result

    async def interrupt(self) -> None:
        self.interrupts += 1

    def emit(self, event: Event) -> Envelope:
        self._seq += 1
        envelope = event.envelope(self.session_id, self._seq)
        if isinstance(event, TurnDone | SessionClosed):
            self.busy = False
        for subscriber in list(self.subscribers):
            subscriber(envelope)
        return envelope


class FakeHost:
    """`SessionManager` as a mission reaches it. It is its own `app_state`, so `calls` holds
    every remember, create and close in the order they were made. A new actor answers sends
    with `send_result`; a set `create_error` is raised by `create` after it is recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.actors: dict[str, FakeActor] = {}
        self.send_result: bool | None = True
        self.create_error: Exception | None = None

    @property
    def app_state(self) -> FakeHost:
        return self

    def remember(self, session_id: str, **changes: Any) -> None:
        self.calls.append(("remember", session_id, changes))

    def create(self, session_id: str) -> FakeActor:
        self.calls.append(("create", session_id))
        if self.create_error is not None:
            raise self.create_error
        actor = self.actors[session_id] = FakeActor(session_id, send_result=self.send_result)
        return actor

    def get(self, session_id: str) -> FakeActor | None:
        return self.actors.get(session_id)

    async def close(self, session_id: str) -> None:
        self.calls.append(("close", session_id))
        self.actors.pop(session_id, None)


class FakeSeatHost:
    """`SeatHost` without a runner. `calls` holds every method call in order as a tuple of its
    name and arguments; `start` raises `DecisionLoopError(refuse)` when `refuse` is set, else
    answers a new session id. `stop` answers True and `stop_all` the count of `start`s so far."""

    def __init__(
        self,
        facts: SeatFacts | None = None,
        team: SeatTeam | None = None,
        waiting: tuple[SeatWaiting, ...] = (),
        refuse: str | None = None,
    ) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.seat_facts = facts if facts is not None else SeatFacts((), (), {}, 2, 0, False, True)
        self.seat_team = team if team is not None else SeatTeam(0, 0, ())
        self.seat_waiting = waiting
        self.refuse = refuse
        self.open = 0

    def facts(self, workspace_id: str, since: datetime | None, day: str) -> SeatFacts:
        self.calls.append(("facts", workspace_id, since, day))
        return self.seat_facts

    def team(self, day: str, since: datetime | None) -> SeatTeam:
        self.calls.append(("team", day, since))
        return self.seat_team

    def waiting(self) -> tuple[SeatWaiting, ...]:
        self.calls.append(("waiting",))
        return self.seat_waiting

    async def start(
        self,
        routine_id: str,
        revision: int,
        *,
        newest: str | None,
        cards: str | None,
        limit: int,
        day: str,
    ) -> str:
        self.calls.append(("start", routine_id, revision, newest, cards, limit, day))
        if self.refuse is not None:
            raise DecisionLoopError(self.refuse)
        self.open += 1
        return str(uuid.uuid4())

    async def stop(self, routine_id: str) -> bool:
        self.calls.append(("stop", routine_id))
        return True

    async def stop_all(self) -> int:
        self.calls.append(("stop_all",))
        return self.open


class FakeCodeHost:
    """`CodeHost` without a git repository. `calls` holds the `now` of every `read`; it answers
    `look`, or an empty look with no folders and no failures."""

    def __init__(self, look: CodeLook | None = None) -> None:
        self.calls: list[datetime] = []
        self.look = look if look is not None else CodeLook((), ())

    def read(self, now: datetime) -> CodeLook:
        self.calls.append(now)
        return self.look


class FakeCheckHost:
    """`CheckHost` without a runner. `calls` holds every method call in order as a tuple of its
    name and arguments; `read` answers `look`, or an empty look; `folder` answers `folder`; `start`
    raises `DecisionLoopError(refuse)` when `refuse` is set, else answers `started`, and notes the
    Stop all count it was given in `given_stops`; `stop`, `stop_all` and `running` answer `live`;
    `stops` answers how many times `stop_all` ran; `supported` answers `supported`."""

    def __init__(
        self,
        look: CheckLook | None = None,
        *,
        folder: CheckFolder | None = None,
        supported: bool = True,
        started: int = 1,
        refuse: str | None = None,
        live: int = 0,
    ) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.look = look if look is not None else CheckLook((), ())
        self.one_folder = folder
        self.is_supported = supported
        self.started = started
        self.refuse = refuse
        self.live = live
        self.stop_alls = 0
        self.given_stops: list[int | None] = []

    def supported(self) -> bool:
        self.calls.append(("supported",))
        return self.is_supported

    def read(self, now: datetime) -> CheckLook:
        self.calls.append(("read", now))
        return self.look

    def folder(self, workspace_id: str, now: datetime) -> CheckFolder | None:
        self.calls.append(("folder", workspace_id, now))
        return self.one_folder

    def running(self) -> int:
        self.calls.append(("running",))
        return self.live

    def stops(self) -> int:
        return self.stop_alls

    async def start(
        self, workspace_id: str, head: str, check_ids: Sequence[str], *, stops: int | None = None
    ) -> int:
        self.calls.append(("start", workspace_id, head, tuple(check_ids)))
        self.given_stops.append(stops)
        if self.refuse is not None:
            raise DecisionLoopError(self.refuse)
        return self.started

    async def stop(self, workspace_id: str) -> int:
        self.calls.append(("stop", workspace_id))
        return self.live

    async def stop_all(self) -> int:
        self.calls.append(("stop_all",))
        self.stop_alls += 1
        return self.live


class FakeInboxHost:
    """`InboxHost` without a suggester. `calls` holds every method call in order as a tuple of its
    name and arguments; `read` answers `look`, or an empty look; `start` raises
    `DecisionLoopError(refuse)` when `refuse` is set, else answers `run_id`. `persistent` is what
    `InboxHost.persistent` answers."""

    def __init__(
        self,
        look: InboxLook | None = None,
        *,
        run_id: str = "r1",
        refuse: str | None = None,
        persistent: bool = False,
    ) -> None:
        self.persistent = persistent
        self.calls: list[tuple[Any, ...]] = []
        self.look = look if look is not None else InboxLook(None, {}, {})
        self.run_id = run_id
        self.refuse = refuse

    def read(self, workspace_id: str) -> InboxLook:
        self.calls.append(("read", workspace_id))
        return self.look

    async def start(self, workspace_id: str, item_key: str) -> str:
        self.calls.append(("start", workspace_id, item_key))
        if self.refuse is not None:
            raise DecisionLoopError(self.refuse)
        return self.run_id
