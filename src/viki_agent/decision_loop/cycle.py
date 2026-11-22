"""One Decision cycle: decide each candidate of one workspace scope and commit it.

`run_cycle` takes the candidates in the order given (a repeated item key counts once). For each:
1. the rules verdict, from the scope's mute and later preferences; its level is kept as the
   episode's `rules_level`;
2. the state (`state.build_state`) from the user, the moment and the item's history, its hash
   naming the context's `playbook_version`, so a new wording asks each item anew;
3. the answers. There are none, and the rules decide, while the model is off (`model_ok` false
   or Decision disabled), the state is blocked, the topic is muted, or the last ask for this
   state hash failed with a `STICKY_FAILURES` code. Otherwise the item's stored answers are
   reused when they were given for this state hash, else the questions of its type are asked
   once through `ask`: `playbook.questions_for` the type and the context's `wording`, the ten
   with each one the wording rewords in its words, then the type's own questions. A request
   `ask` skips (None) or one that fails leaves the rules to decide;
4. `arbiter.decide`, with the context's λ, weights, T and C. Its hourly
   nudge cap counts every scope's showings of the last hour: an item still showing a nudge or an
   alert leaves its own out, so it keeps its level, while an item that would show anew (new,
   released from a hold, back after a drop) counts them all. When only the reversed kind
   question holds an item back from a nudge or an alert (`arbiter.needs_reversal`),
   `playbook.kind_reversed()` is asked once: a choice equal to the kind answer lets the item
   through, another holds it at badge. The agreement is stored with the answers, so later
   cycles on the same state hash pass it on without asking. A reversed ask failing with a
   sticky code counts as disagreeing; one skipped, or failing with another code, leaves the
   item held this cycle and is asked again on the next. With answers fresh from this cycle,
   the shadow version (`settings.shadow`) is then asked the same state and questions once
   through `shadow_ask`, when the context has one, shadowing is not "off" and that version's
   model id is not the live one. A reply answering exactly the asked names is decided on the
   inputs of the live outcome, reversal agreement included, and a `ShadowSummary` compares the
   two. Then, with a proposed wording on trial (`trial`) and a `trial_ask`, when the fresh
   answer to the question it rewords is a yes/no answer and `playbook.check_question` accepts
   the wording, that one question is asked once more in the proposed words, of the live
   version and on the same state, through `trial_ask`; a reply answering exactly that name yes
   or no gives a `TrialSummary` of both wordings' yes probabilities. A shadow or trial ask
   leaves no call record and no failure memory, and one skipped, failed or misfitting gives no
   summary. A `mission_report` then takes its own kind, whichever policy decided: "ready" when
   its plan waits on the user (reason "ready"), else "report", at most "list" when a second
   look's own report found nothing (reason "nothing"); a self-improvement item
   (`IMPROVE`) takes "suggest"; a message draft (`MESSAGE_DRAFTS`) takes "suggest", the
   arbiter's level and no work, as it only fills a chat's message box for the user to send; a
   Kanban card that needs the user (`card_needs_you`) takes "ready" when its status is review,
   else "ask", and a stale one (`card_stale`) "report"; a
   seat item (`SEATS`) takes its type's kind (`_SEAT_KINDS`: a waiting run "ask", a working one
   "inform", a Start offer "suggest", a run report or hold "report") and, all but a waiting run
   (`seat_waiting`), at most "list"; a Look item (`LOOKS`) takes its type's kind (`_LOOK_KINDS`:
   a waiting chat and a memory drift "ask", the rest "report") and at most "list"; a Look
   mission (`LOOK_MISSIONS`) reaches the arbiter with no `work_allowed`, so the arbiter gives it
   no work, and then takes "suggest", at most "list", and work "prepare" only while it is not
   silent, its `work_allowed` names prepare and the Look grant (`grant_look`) is not Off, else
   none (`_look_outcome`); a second look (`SECOND_LOOKS`) reaches the arbiter with no
   `work_allowed` too: its offer then takes `_look_outcome` under its own grant
   (`grant_second_look`), and its finding (`facts["found"]`) "suggest", the arbiter's level and
   no work; a project plan (`PROJECT_PLANS`) reaches it with no `work_allowed` as well: its
   offer then takes `_look_outcome` under its own grant (`grant_project_plan`), and its finding
   "suggest", the arbiter's level and no work; a saved check (`SAVED_CHECKS`) reaches it with no
   `work_allowed` too, then takes its phase's kind (`_check_outcome`: a waiting folder "suggest",
   a running one "inform", an ended one "report"), at most "list", and work "run" only while it
   waits, is not silent, its `work_allowed` names run and Run my saved checks (`grant_check`) is
   not Off, else none; an inbox suggestion (`INBOX_TRIAGE`) reaches it with no `work_allowed`
   too, then takes its phase's kind (`_triage_outcome`: a waiting inbox "suggest", a running run
   "inform", suggestions waiting "ready", any other phase "report"), at most "list", and work
   "prepare" only while it waits, is not silent, its `work_allowed` names prepare and Suggest
   destinations for my inbox (`grant_inbox_triage`) is not Off, else none; every other item
   keeps the arbiter's. A vault
   change (`CHANGES`) the arbiter puts at a badge or louder takes "list" instead, keeping its
   timing, so it logs no showing for the hourly nudge cap and adds no attention: the change
   proposal the engine files for it is how it shows. A `card_needs_you` item the arbiter puts at
   a badge or louder takes "list" the same way while its card changed less than `CARD_QUIET` ago
   (`facts["changed_at"]`; a change time that is not an aware datetime counts as recent), as
   the card may still be moving, unless the stored item already shows a badge or louder. A
   silent or listed one keeps its level;
5. the commit: an episode when the answers are fresh from this cycle or the state hash or the
   level differs from the stored item's (its answers carry the trial and the shadow summaries
   while the whole text fits `ANSWERS_CHARS`, the shadow summary left out first, then the
   trial; its policy version is "playbook-" and the context's `playbook_version`), a call record
   naming that version for every live request made, the item upserted with its template body
   under its type's grants (`work_grants`: a Look mission's under the Look grant, a second
   look's and a project plan's under their own, a saved check's with `grant_check` as its run
   grant, an inbox suggestion's with `grant_inbox_triage` as its prepare grant, read as "ask"
   when "allowed" for an item stored before the context's `inbox_since`, as VIKI starts no run
   for it), and a showing logged
   (`DecisionLoopStore.add_showing`) when the item now shows a nudge or an alert, timed "now",
   louder than it showed before.
Then each open item of the scope whose candidate is not among them and whose key is not in the
context's `keep` goes stale.

Each request made, live, shadow or trial, answered or failed, is counted in the scope's day as
it is made (`DecisionLoopStore.add_usage`): one call, and one shadow call for a shadow or a
trial ask; the reply's measured input tokens and their spend (`cost_micro_usd`; none for an
error); and one held-back request when never-send kept any of the item's evidence out of the
state it sent (`BuiltState.held_back` above 0) -- the questions of its type, the reversed one,
the shadow ask and the trial ask each count, since they all send that state.

The engine owns the clock, the limits and the memory between cycles: it hands in `failed`, the
running missions and the exposures, and takes `CycleResult.failed` back for the next cycle. A
cycle reads and writes the store and the agenda through `asyncio.to_thread` and makes its
requests one at a time; it does not sleep, retry or log. Items are decided in order, so a
showing one item logs counts against the next.

Answers at rest (`encode_answers`, `decode_answers`) are numbers only: a yes/no answer
{"noul": p}; a choice {"confidence": c, "probabilities": {option: p}}, its choice being the
likeliest option (on a tie the one the playbook lists first); a score {"score": s,
"confidence": c, "probabilities": {"0": p, ...}}; "reversal" 1 or 0 once the reversed kind
question has been answered; "shadow" {"agree", "kind", "level", "p", "setting"} once a shadow
ask has answered, read back only by `decode_shadow`; and "trial" {"live", "trial"} once a trial
ask has answered, read back only by `decode_trial`; both are otherwise dropped unread. A fresh
reply passes through that encoding before it is used, so a cycle that reuses stored answers
decides as the cycle that asked did.

`held_start` predicts, ahead of the engine's own gate (`engine._start_missions` and its kin),
whether VIKI's own start of one candidate is held back this cycle: `ctx.holds` carries what the
engine already knows this cycle (the plan brake, quiet hours, a mission already open, the
day's family limits from `START_FAMILIES`), and `_commit` reads it for an item with no active
mission of its own, plus its own "since" code for an inbox suggestion stored before
`ctx.inbox_since`. A hold other than "limit" renders the item's grant as "ask" so its start
action still shows, the hold named in `why` (`templates.HOLD_WHY`, asserted equal to
`HOLD_CODES` below); "limit" leaves the grant as the engine set it but still drops the start
action (`templates._work_actions`), since there is nothing to raise it to today. The
prediction may lag the engine's own decision by one cycle (a start wakes the next cycle, inside
`COALESCE_S`), and says nothing while a mission is already open for the item, which always
wins over any hold.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Protocol

from viki_agent.automation.routines import parse_stamp
from viki_agent.decision_loop import arbiter, playbook, rules, templates
from viki_agent.decision_loop.agenda import CallWrite, DecisionAgenda, ItemWrite
from viki_agent.decision_loop.contracts import (
    ALPHA,
    CANDIDATES,
    CARD_QUIET,
    CARDS,
    CHANGES,
    IMPROVE,
    INBOX_TRIAGE,
    LEVELS,
    LOOK_MISSIONS,
    LOOKS,
    MESSAGE_DRAFTS,
    MISSION_TYPES,
    MODEL_ERROR_CODES,
    MODEL_IDS,
    PRICE_USD_PER_MTOK,
    PROJECT_PLANS,
    SAVED_CHECKS,
    SEATS,
    SECOND_LOOKS,
    TRIGGERS,
    Answer,
    Answers,
    CallBody,
    Candidate,
    ChoiceAnswer,
    DecisionLoopError,
    DecisionSettings,
    ItemRecord,
    MissionRecord,
    ModelError,
    ModelReply,
    ModelRequest,
    NoulAnswer,
    Outcome,
    Presence,
    ScoreAnswer,
    canonical_json,
    quiet_until,
)
from viki_agent.decision_loop.state import (
    BuiltState,
    History,
    Mention,
    Now,
    Owner,
    build_state,
    state_text,
)
from viki_agent.decision_loop.store import DecisionLoopStore, EpisodeInput

# A failed ask with one of these codes keeps its item on the rules until its state hash
# changes. The other codes say nothing about the request itself (limits, the key, the network),
# so the item is asked again on a later cycle.
STICKY_FAILURES = frozenset({"refused", "too_large", "invalid_reply", "failed"})
REVERSAL = "reversal"  # the stored answers' member for the reversed kind question's agreement
SHADOW = "shadow"  # the stored answers' member for the shadow ask's summary
TRIAL = "trial"  # the stored answers' member for the trial ask's summary
ANSWERS_CHARS = 2048  # the bound of stored answers, in the store and the agenda alike
LOUD = ("nudge", "alert")

_LISTED = ("list", "badge")
_RUNNING = ("starting", "running")  # a mission still going
_HOUR = timedelta(hours=1)
_RANK = {level: rank for rank, level in enumerate(LEVELS)}
# What each seat item asks of the user: only a waiting run needs an answer.
_SEAT_KINDS = {
    "seat_waiting": "ask",
    "seat_working": "inform",
    "seat_start": "suggest",
    "seat_report": "report",
}
# What each Look item asks of the user: only a waiting chat and a memory drift need an answer.
_LOOK_KINDS = dict.fromkeys(LOOKS, "report") | {"chat_waiting": "ask", "memory_drift": "ask"}
_HEX_KEY = re.compile(r"[0-9a-f]{64}")
_MICRO_USD_PER_MTOK = round(PRICE_USD_PER_MTOK * 1_000_000)
_LATENCY_MS = 60_000  # the bounds of the stored columns
_INPUT_TOKENS = 1_000_000
_MODEL_CHARS = 32


def _choice_options() -> dict[str, tuple[str, ...]]:
    """Each choice question's options in the playbook's order."""
    options: dict[str, tuple[str, ...]] = {}
    for name, question in playbook.questions().items():
        criteria = question["criteria"]
        if question["type"] == "choice" and isinstance(criteria, dict):
            options[name] = tuple(str(option) for option in criteria)
    return options


_CHOICE_OPTIONS = _choice_options()


class Ask(Protocol):
    """The engine's way to the model: it adds the key and enforces the limits. It answers the
    reply, the model's error, or None when a limit skipped the request."""

    async def __call__(
        self, item_key: str, request: ModelRequest, *, reversal: bool
    ) -> ModelReply | ModelError | None: ...


@dataclass(frozen=True)
class Trial:
    """A proposed wording being tried: `name` is the yes/no question it rewords and `question`
    the proposed question, which `playbook.check_question` must accept for it to be asked."""

    name: str
    question: Mapping[str, object]


# The five families a start counts toward for the day's limits (`Holds.limited`, read by
# `held_start` through `start_family`): a Look mission's, a second look's and a project plan's
# own family, else the work itself ("prepare" for a draft, "run" for a chat) — each counted
# apart from the other four.
START_FAMILIES = ("look", "second_look", "project_plan", "prepare", "run")


@dataclass(frozen=True)
class Holds:
    """What holds VIKI's own starts this cycle, read by `held_start` for every candidate:
    `brake` (the plan brake), `quiet` (quiet hours) and `busy` (a mission already open) are
    read under an "allowed" grant only, since an "ask" item already shows its own start
    button; `limited` names the `START_FAMILIES` already at today's limit, read under "ask" or
    "allowed" alike. The default, no hold at all, is what a cycle whose engine has not built
    the real `Holds` yet (or a test) reads."""

    brake: bool = False
    quiet: bool = False
    busy: bool = False
    limited: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CycleContext:
    """What one cycle reads.

    `now` is an aware datetime on the user's wall clock: quiet hours, the moment and the
    episode day (`now.date()`) read it as it is, and the store keeps it as a UTC stamp. `owner`,
    `history_for` (an item key's History; it runs in a worker thread) and `presence` feed the
    state; `lam`, `weights`, `reach` and `costs` the arbiter (`arbiter.ArbiterInput`; the last
    two default to the arbiter's start values). `model_ok` is the engine's word that
    the model may be asked. `failed` maps an item key to the state hash whose ask failed for
    good: the last cycle's `CycleResult.failed`. `missions` are the running missions, of any
    scope; `exposures` how often each item was shown in the last day (0 when absent).
    `shadow_ask` takes the shadow version's requests as `ask` takes the live ones; None asks no
    shadow. `wording` is the wording in use, a proposed question by the name it rewords ({} for
    the playbook's own), and `playbook_version` its `playbook.version`, which names the state
    and each call. `trial` is the proposed wording being tried, if any, and `trial_ask` takes
    its requests as `ask` takes the live ones; None asks no trial. `keep` names the open items
    the cycle leaves as they are though no candidate names them (`detectors.kanban_keep`).
    `inbox_since` is when the engine began (None: no bound, as when the inbox host keeps its started
    keys across restarts): VIKI starts no inbox suggestion run for an item stored before it, so
    under Allowed such an item offers Suggest destinations, as under Ask first. `holds` is what
    holds VIKI's own starts this cycle (`held_start`); the default `Holds()` predicts no hold at
    all.
    """

    scope: str
    store: DecisionLoopStore
    agenda: DecisionAgenda
    settings: DecisionSettings
    presence: Presence
    owner: Owner
    history_for: Callable[[str], History]
    now: datetime
    lam: float
    weights: Mapping[str, float]
    ask: Ask
    model_ok: bool
    failed: Mapping[str, str] = field(default_factory=dict)
    missions: Sequence[MissionRecord] = ()
    exposures: Mapping[str, int] = field(default_factory=dict)
    reach: Mapping[str, float] = arbiter.START_REACH
    costs: Mapping[str, float] = arbiter.START_COSTS
    shadow_ask: Ask | None = None
    wording: Mapping[str, object] = field(default_factory=dict)
    playbook_version: str = playbook.PLAYBOOK_VERSION
    trial: Trial | None = None
    trial_ask: Ask | None = None
    keep: frozenset[str] = frozenset()
    inbox_since: datetime | None = None
    holds: Holds = Holds()


@dataclass(frozen=True)
class Committed:
    """One item the cycle decided: the item as stored, its candidate, the outcome, who decided
    ("model" or "rules") and whether the cycle recorded a new episode for it."""

    item: ItemRecord
    candidate: Candidate
    outcome: Outcome
    policy: str
    new_episode: bool


@dataclass(frozen=True)
class CycleResult:
    """What one cycle did.

    `committed` holds every item it decided, in candidate order. `loud` counts the open items
    now showing a nudge or an alert (timing "now") that did not show so loud a level before,
    each logged as a showing for the hourly nudge cap; `new_list` the open items now showing at
    list or badge that did not show before. `attention` sums `contracts.ALPHA` of the level each
    open item now shows (timing "now") when it did not show before or shows louder: the
    attention the cycle newly asks of the user. `asked` counts the requests made, answered or
    failed, shadow and trial ones included (a request a limit skipped is not made). `failed` is
    the memory for the next cycle: item key to the state hash whose ask failed for good.
    `spent_micro_usd` is the spend the replies measured, shadow and trial ones included.
    `new_list` leaves out vault changes (`CHANGES`).
    """

    committed: tuple[Committed, ...]
    loud: int
    new_list: int
    attention: float
    asked: int
    failed: Mapping[str, str]
    spent_micro_usd: int


@dataclass(frozen=True)
class _Asked:
    """One request made for an item and what came back; `failure` is None when answered."""

    questions: Mapping[str, Mapping[str, object]]
    model: str | None
    failure: str | None
    answers_json: str | None
    latency_ms: int | None
    input_tokens: int | None
    cost_micro_usd: int | None


def cost_micro_usd(input_tokens: int) -> int:
    """What `input_tokens` cost at `PRICE_USD_PER_MTOK`, in micro-USD rounded up."""
    return -(-max(input_tokens, 0) * _MICRO_USD_PER_MTOK // 1_000_000)


async def run_cycle(
    ctx: CycleContext, candidates: Sequence[Candidate], *, trigger: str
) -> CycleResult:
    """Decide and commit `candidates`, the scope's complete set now; see the module docstring.

    `trigger` is one of `TRIGGERS`. A candidate whose type is not one of `CANDIDATES` or whose
    item key is not 64 lower-case hex digits is `invalid_request`, refused before any request.
    """
    if trigger not in TRIGGERS:
        raise DecisionLoopError("invalid_request")
    usable: dict[str, Candidate] = {}
    for candidate in candidates:
        if candidate.type not in CANDIDATES or not _is_key(candidate.item_key):
            raise DecisionLoopError("invalid_request")
        usable.setdefault(candidate.item_key, candidate)
    run = _Run(ctx, trigger)
    run.prefs = await asyncio.to_thread(ctx.store.preferences, ctx.scope, ctx.now)
    for candidate in usable.values():
        await run.item(candidate)
    await asyncio.to_thread(ctx.agenda.close_missing, ctx.scope, [*usable, *ctx.keep], ctx.now)
    return CycleResult(
        committed=tuple(run.committed),
        loud=run.loud,
        new_list=run.new_list,
        attention=run.attention,
        asked=run.requests,
        failed=dict(run.failed),
        spent_micro_usd=run.spent,
    )


class _Run:
    """One cycle's running state."""

    def __init__(self, ctx: CycleContext, trigger: str) -> None:
        settings = ctx.settings
        self.ctx = ctx
        self.trigger = trigger
        self.day = ctx.now.date().isoformat()
        self.model_id = MODEL_IDS[settings.model_version]
        self.model_on = ctx.model_ok and settings.enabled
        self.quiet = quiet_until(settings.quiet, ctx.now)
        self.focus = settings.focus_until is not None and settings.focus_until > ctx.now
        self.missions: dict[str, MissionRecord] = {}
        for mission in ctx.missions:
            if mission.scope == ctx.scope and mission.state in _RUNNING:
                self.missions.setdefault(mission.item_key, mission)
        self.prefs = rules.Prefs()
        self.committed: list[Committed] = []
        self.failed: dict[str, str] = {}
        self.loud = self.new_list = self.requests = self.spent = 0
        self.attention = 0.0

    async def item(self, candidate: Candidate) -> None:
        """Decide one candidate and commit it."""
        ctx, key = self.ctx, candidate.item_key
        prior = await asyncio.to_thread(ctx.agenda.item, ctx.scope, key)
        since = ctx.now - _HOUR
        shown = await asyncio.to_thread(ctx.store.showings_since, ctx.scope, since)
        history = await asyncio.to_thread(ctx.history_for, key)
        verdict = rules.decide(candidate.type, candidate.rules_evidence, ctx.now, self.prefs)
        built = build_state(
            candidate,
            ctx.owner,
            self._moment(shown),
            history,
            ctx.settings,
            playbook_version=ctx.playbook_version,
        )
        state_hash = built.state_hash
        failed_before = state_hash is not None and ctx.failed.get(key) == state_hash
        if failed_before and state_hash is not None:
            self.failed[key] = state_hash
        known = _stored(prior, state_hash)  # the answers given for this state hash
        asked: list[_Asked] = []
        answers: Answers = {}
        reversal: bool | None = None
        fresh = False
        if self.model_on and state_hash is not None and candidate.type not in self.prefs.muted:
            if known is not None:
                answers, reversal = known
            elif not failed_before:
                questions = playbook.questions_for(candidate.type, ctx.wording)
                got, failure = await self._ask(key, built, questions, asked)
                if got is not None:
                    answers, fresh, known = got, True, (got, None)
                elif failure in STICKY_FAILURES:
                    self.failed[key] = state_hash
        # A nudge or an alert the item still shows is no new showing, so its own showings leave
        # the cap; an item that would show anew counts every showing of the hour, its own too.
        loud_last_hour = shown
        if _showing(prior) in LOUD:
            loud_last_hour = await asyncio.to_thread(
                ctx.store.showings_since, ctx.scope, since, exclude_item=key
            )
        # The cycle, not the arbiter, gives a Look mission, a second look, a project plan, a
        # saved check or an inbox suggestion its work.
        look = candidate.type in LOOK_MISSIONS
        second = candidate.type in SECOND_LOOKS
        plan = candidate.type in PROJECT_PLANS
        checks = candidate.type in SAVED_CHECKS
        triage = candidate.type in INBOX_TRIAGE
        own_work = look or second or plan or checks or triage
        inp = arbiter.ArbiterInput(
            candidate=replace(candidate, work_allowed=()) if own_work else candidate,
            answers=answers,
            reversal_agrees=reversal,
            rules_verdict=verdict,
            settings=ctx.settings,
            presence=ctx.presence,
            quiet_until=self.quiet,
            current_level=prior.level if prior is not None and prior.state == "open" else "silent",
            exposures_24h=ctx.exposures.get(key, 0),
            loud_last_hour=loud_last_hour,
            lam=ctx.lam,
            weights=ctx.weights,
            now=ctx.now,
            reach=ctx.reach,
            costs=ctx.costs,
        )
        outcome = arbiter.decide(inp)
        if answers and arbiter.needs_reversal(outcome, inp):
            reversed_kind = {"kind": playbook.kind_reversed()}
            got, failure = await self._ask(key, built, reversed_kind, asked, reversal=True)
            if got is not None:
                answer = got.get("kind")
                reversal = isinstance(answer, ChoiceAnswer) and answer.choice == outcome.kind
            elif failure in STICKY_FAILURES:
                reversal = False
            if reversal is not None:
                known = (answers, reversal)
                outcome = arbiter.decide(replace(inp, reversal_agrees=reversal))
        shadow = trial = None
        if fresh:  # on the inputs the live outcome was decided with
            shadow = await self._shadow(key, built, replace(inp, reversal_agrees=reversal), outcome)
            trial = await self._trial(key, built, answers)
        own = work_grants(candidate.type, ctx.settings)
        if candidate.type == "mission_report":  # after the reversal, which compares the model's
            ready = candidate.facts.get("reason") == "ready"
            outcome = replace(outcome, kind="ready" if ready else "report")
            # A second look's quiet "found nothing" report asks no more attention than a list.
            if candidate.facts.get("reason") == "nothing" and _RANK[outcome.level] > _RANK["list"]:
                outcome = replace(outcome, level="list")
        elif candidate.type in IMPROVE:  # a self-improvement item only offers what it prepares
            outcome = replace(outcome, kind="suggest")
            if candidate.type in CHANGES and _RANK[outcome.level] > _RANK["list"]:
                outcome = replace(outcome, level="list")  # its filed proposal is how it shows
        elif candidate.type in MESSAGE_DRAFTS:  # it only fills a chat's box: the user sends it
            outcome = replace(outcome, kind="suggest", work="none")
        elif candidate.type in CARDS:  # a Kanban card's status says what it asks of the user
            if candidate.type == "card_stale":
                outcome = replace(outcome, kind="report")
            else:
                review = candidate.facts.get("status") == "review"
                outcome = replace(outcome, kind="ready" if review else "ask")
                shown = _showing(prior)  # an item already louder than a list keeps its level
                if (
                    _card_quiet(candidate, ctx.now)
                    and _RANK[outcome.level] > _RANK["list"]
                    and (shown is None or _RANK[shown] <= _RANK["list"])
                ):
                    outcome = replace(outcome, level="list")  # a new escalation: card may be moving
        elif candidate.type in SEATS:  # a seat item's type says what it asks of the user
            outcome = replace(outcome, kind=_SEAT_KINDS[candidate.type])
            # Only a waiting run holds its work until the user answers; the rest wait in the list.
            if candidate.type != "seat_waiting" and _RANK[outcome.level] > _RANK["list"]:
                outcome = replace(outcome, level="list")
        elif candidate.type in LOOKS:  # a Look item waits in the list, whatever it reports
            outcome = replace(outcome, kind=_LOOK_KINDS[candidate.type])
            if _RANK[outcome.level] > _RANK["list"]:
                outcome = replace(outcome, level="list")
        elif (second or plan) and "found" in candidate.facts:  # a finding: its plan waits
            outcome = replace(outcome, kind="suggest", work="none")
        elif look or second or plan:  # each under its own grant, as it renders and VIKI starts
            outcome = _look_outcome(candidate, outcome, own["prepare"])
        elif checks:
            outcome = _check_outcome(candidate, outcome, own["run"])
        elif triage:
            outcome = _triage_outcome(candidate, outcome, own["prepare"])
        rules_level = arbiter.decide(replace(inp, answers={})).level
        await self._commit(
            candidate,
            prior,
            built,
            outcome,
            answers=answers,
            reversal=reversal,
            known=known,
            asked=asked,
            fresh=fresh,
            rules_level=rules_level,
            shadow=shadow,
            trial=trial,
        )

    def _moment(self, shown: int) -> Now:
        ctx = self.ctx
        return Now(
            active=_active(ctx.presence),
            focus=self.focus,
            typing=ctx.presence.typing,
            time=ctx.now,
            nudges_this_hour=shown,
        )

    async def _ask(
        self,
        key: str,
        built: BuiltState,
        questions: Mapping[str, Mapping[str, object]],
        asked: list[_Asked],
        *,
        reversal: bool = False,
    ) -> tuple[dict[str, Answer] | None, str | None]:
        """Make one request, count it (`_use`) and note it in `asked`. Answer its answers as
        storage reads them back, or None and the failure's code; (None, None) when a limit
        skipped it. A reply that does not answer exactly the asked names fails as
        invalid_reply."""
        ctx = self.ctx
        request = ModelRequest(model=self.model_id, state=built.state, questions=questions)
        result = await ctx.ask(key, request, reversal=reversal)
        if result is None:
            return None, None
        tokens, cost = await self._use(result, held=built.held_back > 0)
        if isinstance(result, ModelError):
            code = result.code if result.code in MODEL_ERROR_CODES else "failed"
            asked.append(_Asked(questions, self.model_id, code, None, None, None, None))
            return None, code
        model = result.model if len(result.model) <= _MODEL_CHARS else None
        latency = min(max(result.latency_ms, 0), _LATENCY_MS)
        read = _read_back(result.answers, questions)
        if read is None:
            asked.append(_Asked(questions, model, "invalid_reply", None, latency, tokens, cost))
            return None, "invalid_reply"
        text, answers = read
        asked.append(_Asked(questions, model, None, text, latency, tokens, cost))
        return answers, None

    async def _shadow(
        self, key: str, built: BuiltState, inp: arbiter.ArbiterInput, live: Outcome
    ) -> ShadowSummary | None:
        """Ask the shadow version (`settings.shadow`) the live request's questions on the item's
        state once, count the request (`_use`), and compare the decision on its answers with
        `inp` to `live`, the outcome `inp` gave. None when no shadow is asked (no `shadow_ask`,
        shadowing off, or the live model id) or the request is skipped, fails or does not answer
        exactly the asked names."""
        ctx, setting = self.ctx, self.ctx.settings.shadow
        model = MODEL_IDS.get(setting)
        if ctx.shadow_ask is None or model is None or model == self.model_id:
            return None
        questions = playbook.questions_for(inp.candidate.type, ctx.wording)
        request = ModelRequest(model=model, state=built.state, questions=questions)
        result = await ctx.shadow_ask(key, request, reversal=False)
        if result is None:
            return None
        await self._use(result, held=built.held_back > 0, shadow=True)
        read = None if isinstance(result, ModelError) else _read_back(result.answers, questions)
        if read is None:
            return None
        _, answers = read
        outcome = arbiter.decide(replace(inp, answers=answers))
        return ShadowSummary(
            agree=outcome.level == live.level,
            kind_agree=outcome.kind == live.kind,
            level=outcome.level,
            p=arbiter.p_of(answers, inp.weights, inp.candidate.type),
            setting=setting,
        )

    async def _trial(
        self, key: str, built: BuiltState, answers: Mapping[str, Answer]
    ) -> TrialSummary | None:
        """Ask the proposed wording on trial (`ctx.trial`) alone, of the live version, on the
        item's state once through `trial_ask`, and count the request (`_use`) as a shadow call.
        `answers` are the live ones. None when no trial is asked (no trial or `trial_ask`, no
        yes/no live answer to the question it rewords, or a wording `playbook.check_question`
        refuses) or the request is skipped, fails or does not answer exactly that question yes
        or no."""
        ctx, trial = self.ctx, self.ctx.trial
        if trial is None or ctx.trial_ask is None:
            return None
        live = answers.get(trial.name)
        if not isinstance(live, NoulAnswer):
            return None
        try:
            questions = {trial.name: playbook.check_question(trial.name, trial.question)}
        except ValueError:
            return None
        request = ModelRequest(model=self.model_id, state=built.state, questions=questions)
        result = await ctx.trial_ask(key, request, reversal=False)
        if result is None:
            return None
        await self._use(result, held=built.held_back > 0, shadow=True)
        read = None if isinstance(result, ModelError) else _read_back(result.answers, questions)
        tried = None if read is None else read[1][trial.name]
        if not isinstance(tried, NoulAnswer):
            return None
        return TrialSummary(live=live.p, trial=tried.p)

    async def _use(
        self, result: ModelReply | ModelError, *, held: bool, shadow: bool = False
    ) -> tuple[int, int]:
        """Count one request made, in the cycle and in the scope's day (`add_usage`): one call,
        one shadow call too when `shadow`, the reply's measured input tokens and their spend
        (none for an error), and one held-back request when `held`: never-send kept evidence
        out of the state it sent, one or more entries alike. Answer (tokens, spend)."""
        ctx = self.ctx
        tokens = 0
        if not isinstance(result, ModelError):
            tokens = min(max(result.input_tokens, 0), _INPUT_TOKENS)
        cost = cost_micro_usd(tokens)
        self.requests += 1
        self.spent += cost
        await asyncio.to_thread(
            ctx.store.add_usage,
            ctx.scope,
            self.day,
            calls=1,
            shadow_calls=1 if shadow else 0,
            tokens=tokens,
            held_back=1 if held else 0,
            micro_usd=cost,
            now=ctx.now,
        )
        return tokens, cost

    async def _commit(
        self,
        candidate: Candidate,
        prior: ItemRecord | None,
        built: BuiltState,
        outcome: Outcome,
        *,
        answers: Answers,
        reversal: bool | None,
        known: tuple[dict[str, Answer], bool | None] | None,
        asked: list[_Asked],
        fresh: bool,
        rules_level: str,
        shadow: ShadowSummary | None,
        trial: TrialSummary | None,
    ) -> None:
        """Record the episode when the answers are fresh or the state hash or the level changed,
        then the calls, then the item — its grant read as "ask" under any hold `held_start`
        predicts this cycle (or "since" for an inbox suggestion stored before the engine began),
        with no active mission of its own; count what now shows."""
        ctx, key, settings = self.ctx, candidate.item_key, self.ctx.settings
        mission = self.missions.get(key)
        mission_id = None if mission is None else mission.mission_id
        state_hash = built.state_hash
        # A fresh answer records its episode even on an unchanged state and level: its shadow
        # summary is kept, and labels on the item reach a model episode, the rows learning reads.
        same = (
            prior is not None
            and not fresh
            and prior.state_hash == state_hash
            and prior.level == outcome.level
        )
        if prior is not None and same:
            episode_id = prior.episode_id
        else:
            measured = [entry for entry in asked if entry.input_tokens is not None]
            answers_json = None
            if answers:
                answers_json = _episode_answers(answers, reversal, shadow, trial)
            episode = EpisodeInput(
                candidate=candidate.type,
                evidence=candidate.rules_evidence,
                policy="model" if answers else "rules",
                policy_version=(
                    f"playbook-{ctx.playbook_version}" if answers else rules.POLICY_VERSION
                ),
                level=outcome.level,
                timing=outcome.timing,
                work=outcome.work,
                reason=outcome.reason,
                item_key=key,
                state_hash=state_hash,
                model=(asked[0].model if fresh else self.model_id) if answers else None,
                answers_json=answers_json,
                terms=dict(outcome.terms) or None,
                kind=outcome.kind,
                defer_until=outcome.defer_until,
                rules_level=rules_level,
                latency_ms=_total((entry.latency_ms for entry in measured), _LATENCY_MS),
                input_tokens=_total((entry.input_tokens for entry in measured), _INPUT_TOKENS),
                cost_micro_usd=_total((entry.cost_micro_usd for entry in measured), None),
                mission_id=mission_id,
            )
            (recorded,) = await asyncio.to_thread(
                ctx.store.record,
                ctx.scope,
                [episode],
                trigger=self.trigger,
                now=ctx.now,
                day=self.day,
            )
            episode_id = recorded.episode_id
        sent = state_text(built.state)
        call_ids: list[str] = []
        for entry in asked:
            call = CallWrite(
                item_key=key,
                episode_id=episode_id,
                session_id=_session(candidate.related_chat),
                model=entry.model,
                outcome="answered" if entry.failure is None else "failed",
                failure=entry.failure,
                latency_ms=entry.latency_ms,
                input_tokens=entry.input_tokens,
                cost_micro_usd=entry.cost_micro_usd,
                body=CallBody(
                    title=candidate.title,
                    candidate=candidate.type,
                    state_text=sent,
                    questions_text=playbook.questions_text(entry.questions),
                    split=_split(built, entry.questions),
                    answers_json=entry.answers_json,
                    held_back=built.held_back,
                    version_setting=settings.model_version,
                    playbook_version=ctx.playbook_version,
                ),
            )
            call_ids.append(
                await asyncio.to_thread(ctx.agenda.record_call, ctx.scope, call, ctx.now)
            )
        # "See why" opens the call the answers came from: this cycle's, or the stored one's.
        call_id = None
        if answers:
            call_id = call_ids[0] if fresh else _context_call(prior)
        grants = work_grants(candidate.type, settings)
        hold: str | None = None
        if (
            candidate.type in INBOX_TRIAGE
            and grants["prepare"] == "allowed"
            and _stored_before(prior, ctx.inbox_since)
        ):
            # VIKI starts no run for it (the engine's `_since`), so the start is the user's own.
            hold = "since"
        elif mission is None:  # an active mission always wins over any predicted hold
            hold = held_start(
                candidate.type,
                outcome.work,
                grants.get(outcome.work, "off"),
                outcome.timing,
                ctx.holds,
            )
        if hold is not None and hold != "limit":
            grants = {**grants, outcome.work: "ask"}
        body = templates.render(
            candidate, outcome, answers, mission=mission, grants=grants, call_id=call_id, hold=hold
        )
        write = ItemWrite(
            item_key=key,
            episode_id=episode_id,
            candidate=candidate.type,
            kind=outcome.kind,
            level=outcome.level,
            timing=outcome.timing,
            defer_until=outcome.defer_until,
            state_hash=state_hash,
            answers_json=None if known is None else encode_answers(known[0], reversal=known[1]),
            mission_id=mission_id,
            body=body,
        )
        record = await asyncio.to_thread(ctx.agenda.upsert, ctx.scope, write, ctx.now)
        before, now_showing = _showing(prior), _showing(record)
        if now_showing is not None and (before is None or _RANK[before] < _RANK[now_showing]):
            self.attention += ALPHA[now_showing]  # shown anew, or louder than before
        if now_showing in LOUD and (before is None or _RANK[before] < _RANK[now_showing]):
            self.loud += 1
            await asyncio.to_thread(ctx.store.add_showing, ctx.scope, key, ctx.now)
        elif now_showing in _LISTED and before is None and candidate.type not in CHANGES:
            self.new_list += 1
        self.committed.append(
            Committed(
                item=record,
                candidate=candidate,
                outcome=outcome,
                policy="model" if answers else "rules",
                new_episode=not same,
            )
        )


def _look_outcome(candidate: Candidate, outcome: Outcome, grant: str) -> Outcome:
    """A Look mission's, a second-look offer's or a project-plan offer's outcome: kind "suggest",
    at most "list", and work "prepare" only while it is not silent, `candidate.work_allowed` (the
    policy's cut) names prepare and its grant `grant` is not Off; else no work. The arbiter
    decided it without `work_allowed`, so its own work is never kept."""
    level = "list" if _RANK[outcome.level] > _RANK["list"] else outcome.level
    prepare = level != "silent" and "prepare" in candidate.work_allowed and grant != "off"
    return replace(outcome, kind="suggest", level=level, work="prepare" if prepare else "none")


def _check_outcome(candidate: Candidate, outcome: Outcome, grant: str) -> Outcome:
    """A saved-check item's outcome by its phase (`facts["phase"]`): a waiting folder "suggest",
    a running one "inform" and an ended one "report", at most "list", and work "run" only while it
    waits, is not silent, `candidate.work_allowed` (the policy's cut) names run and the check grant
    `grant` is not Off; else no work. The arbiter decided it without `work_allowed`."""
    phase = candidate.facts.get("phase")
    kind = {"waiting": "suggest", "running": "inform"}.get(str(phase), "report")
    level = "list" if _RANK[outcome.level] > _RANK["list"] else outcome.level
    run = (
        phase == "waiting"
        and level != "silent"
        and "run" in candidate.work_allowed
        and grant != "off"
    )
    return replace(outcome, kind=kind, level=level, work="run" if run else "none")


def work_grants(candidate_type: str, settings: DecisionSettings) -> dict[str, str]:
    """The grant each work ("prepare", "run") of an item of `candidate_type` is under: a Look
    mission's, a second look's, a project plan's and an inbox suggestion's own grant for prepare,
    a saved check's for run, and Prepare drafts and Start a chat for every other type. An item
    renders under it, and VIKI's own start asks it."""
    if candidate_type in LOOK_MISSIONS:
        return {"prepare": settings.grant_look}
    if candidate_type in SECOND_LOOKS:
        return {"prepare": settings.grant_second_look}
    if candidate_type in PROJECT_PLANS:
        return {"prepare": settings.grant_project_plan}
    if candidate_type in SAVED_CHECKS:
        return {"run": settings.grant_check}
    if candidate_type in INBOX_TRIAGE:
        return {"prepare": settings.grant_inbox_triage}
    return {"prepare": settings.grant_prepare, "run": settings.grant_run}


def start_family(candidate_type: str, work: str) -> str:
    """Which of `START_FAMILIES` a start of `candidate_type` counts toward: a Look mission's
    ("look"), a second look's ("second_look") or a project plan's ("project_plan") own family,
    else `work` itself ("prepare" or "run"), as `work_grants` also singles out those three."""
    if candidate_type in LOOK_MISSIONS:
        return "look"
    if candidate_type in SECOND_LOOKS:
        return "second_look"
    if candidate_type in PROJECT_PLANS:
        return "project_plan"
    return work


# The types that start through the shared mission system (`_Run.missions`, `MissionService`):
# the regular types (`MISSION_TYPES`) and the three `work_grants` and `start_family` also treat
# apart. An active mission for one of them always wins over any hold `held_start` predicts.
_HELD_MISSION_TYPES = MISSION_TYPES + LOOK_MISSIONS + SECOND_LOOKS + PROJECT_PLANS
_HELD_BRAKE_TYPES = _HELD_MISSION_TYPES + INBOX_TRIAGE
_HELD_QUIET_TYPES = LOOK_MISSIONS + SECOND_LOOKS + PROJECT_PLANS + SAVED_CHECKS + INBOX_TRIAGE
# Every code `held_start` or `_commit`'s own "since" case may set, matched to
# `templates.HOLD_WHY`'s keys by the assertion below so neither drifts from the other.
HOLD_CODES = ("limit", "brake", "quiet", "busy", "later", "since")
assert set(HOLD_CODES) == set(templates.HOLD_WHY)


def held_start(candidate_type: str, work: str, grant: str, timing: str, holds: Holds) -> str | None:
    """Whether VIKI's own start of one item is predicted held this cycle, ahead of the engine's
    real gate: None unless `work` is "prepare" or "run" and `grant` is "ask" or "allowed" (an
    "off" grant, or an item with no work, needs no reason, since neither ever starts). "limit"
    when `candidate_type` is one of `_HELD_MISSION_TYPES` and `start_family(candidate_type,
    work)` is one of `holds.limited`, under either grant. Otherwise, only under "allowed", the
    first that holds: "brake" for `_HELD_BRAKE_TYPES` while `holds.brake`; "quiet" for
    `_HELD_QUIET_TYPES` while `holds.quiet`; "busy" for `_HELD_MISSION_TYPES` while
    `holds.busy`; "later" for `_HELD_MISSION_TYPES` while `timing` is not "now". Any other
    candidate, or one none of these match, reads None."""
    if work not in ("prepare", "run") or grant not in ("ask", "allowed"):
        return None
    if (
        candidate_type in _HELD_MISSION_TYPES
        and start_family(candidate_type, work) in holds.limited
    ):
        return "limit"
    if grant != "allowed":
        return None
    if holds.brake and candidate_type in _HELD_BRAKE_TYPES:
        return "brake"
    if holds.quiet and candidate_type in _HELD_QUIET_TYPES:
        return "quiet"
    if holds.busy and candidate_type in _HELD_MISSION_TYPES:
        return "busy"
    if timing != "now" and candidate_type in _HELD_MISSION_TYPES:
        return "later"
    return None


def _stored_before(prior: ItemRecord | None, since: datetime | None) -> bool:
    """Whether the stored item `prior` was first stored before `since` (the store keeps an item's
    `created_at` through every upsert); a stamp that does not read says no, so it fails no
    commit."""
    if prior is None or since is None:
        return False
    try:
        return parse_stamp(prior.created_at) < since
    except ValueError:
        return False


def _triage_outcome(candidate: Candidate, outcome: Outcome, grant: str) -> Outcome:
    """An inbox suggestion's outcome by its phase (`facts["phase"]`): a waiting inbox "suggest",
    a running run "inform", suggestions waiting "ready" and any other phase "report", at most
    "list", and work "prepare" only while it waits, is not silent, `candidate.work_allowed` (the
    policy's cut) names prepare and the inbox grant `grant` is not Off; else no work. The arbiter
    decided it without `work_allowed`."""
    phase = candidate.facts.get("phase")
    kind = {"waiting": "suggest", "running": "inform", "ready": "ready"}.get(str(phase), "report")
    level = "list" if _RANK[outcome.level] > _RANK["list"] else outcome.level
    prepare = (
        phase == "waiting"
        and level != "silent"
        and "prepare" in candidate.work_allowed
        and grant != "off"
    )
    return replace(outcome, kind=kind, level=level, work="prepare" if prepare else "none")


# ---- answers at rest -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ShadowSummary:
    """One shadow ask's outcome against the cycle's live decision: `level` is a `LEVELS` value
    and `setting` a `MODEL_IDS` key, both of the shadow ask; `p` is the shadow answers'
    probability term."""

    agree: bool
    kind_agree: bool
    level: str
    p: float
    setting: str


@dataclass(frozen=True)
class TrialSummary:
    """One trial ask's answer beside the live one on the same state: `live` is the yes
    probability the wording in use got, `trial` the proposed wording's."""

    live: float
    trial: float


def encode_answers(
    answers: Answers,
    *,
    reversal: bool | None = None,
    shadow: ShadowSummary | None = None,
    trial: TrialSummary | None = None,
) -> str:
    """The answers as stored: canonical JSON whose every value is a number (see the module
    docstring), with "reversal" 1 or 0 when `reversal` is known, a "shadow" summary when
    `shadow` is given and a "trial" one, each probability to 4 places, when `trial` is. A value
    that is not a finite number, a choice without probabilities, a score level that is not a
    whole number from 0, a question named "reversal", "shadow" or "trial", or the text (the
    whole encoding, shadow and trial included) past `ANSWERS_CHARS` raises ValueError."""
    members: dict[str, object] = {}
    for name, answer in answers.items():
        if not isinstance(name, str) or name in (REVERSAL, SHADOW, TRIAL):
            raise ValueError("answer name")
        if isinstance(answer, NoulAnswer):
            members[name] = {"noul": _finite(answer.p)}
        elif isinstance(answer, ChoiceAnswer):
            if not answer.probabilities:
                raise ValueError("choice without probabilities")
            members[name] = {
                "confidence": _finite(answer.confidence),
                "probabilities": {
                    _option(option): _finite(p) for option, p in answer.probabilities.items()
                },
            }
        elif isinstance(answer, ScoreAnswer):
            members[name] = {
                "score": _finite(answer.score),
                "confidence": _finite(answer.confidence),
                "probabilities": {
                    _level_name(level): _finite(p) for level, p in answer.probabilities.items()
                },
            }
        else:
            raise ValueError("not an answer")
    if reversal is not None:
        members[REVERSAL] = 1 if reversal else 0
    if shadow is not None:
        members[SHADOW] = {
            "agree": 1 if shadow.agree else 0,
            "kind": 1 if shadow.kind_agree else 0,
            "level": LEVELS.index(shadow.level),
            "p": round(_finite(shadow.p), 4),
            "setting": tuple(MODEL_IDS).index(shadow.setting),
        }
    if trial is not None:
        members[TRIAL] = {
            "live": round(_finite(trial.live), 4),
            "trial": round(_finite(trial.trial), 4),
        }
    text = canonical_json(members)
    if len(text) > ANSWERS_CHARS:
        raise ValueError("answers too long")
    return text


def decode_answers(text: str) -> tuple[dict[str, Answer], bool | None]:
    """The answers and the reversal agreement (None when absent) that `encode_answers` wrote;
    its "shadow" and "trial" members, if any, are dropped unread (`decode_shadow` and
    `decode_trial` read them). A choice reads back as its likeliest option, on a tie the one the
    playbook lists first. Text that is not such an encoding raises ValueError."""
    try:
        value = json.loads(text, parse_constant=_no_constant)
    except RecursionError:
        raise ValueError("answers too deep") from None
    if not isinstance(value, dict):
        raise ValueError("answers not an object")
    reversal = value.pop(REVERSAL, None)
    if reversal is not None and (type(reversal) is not int or reversal not in (0, 1)):
        raise ValueError("reversal")
    value.pop(SHADOW, None)
    value.pop(TRIAL, None)
    answers = {name: _decoded(name, member) for name, member in value.items()}
    return answers, None if reversal is None else reversal == 1


def decode_shadow(text: str) -> ShadowSummary | None:
    """The shadow summary `encode_answers` wrote, or None when `text` is not an answers
    encoding, carries no "shadow" member, or that member is not exactly {"agree", "kind",
    "level", "p", "setting"} with 0/1 flags, in-range indexes and a finite probability in
    0..1. Never raises."""
    try:
        value = json.loads(text, parse_constant=_no_constant)
    except ValueError, RecursionError:
        return None
    if not isinstance(value, dict):
        return None
    member = value.get(SHADOW)
    if not isinstance(member, dict) or member.keys() != {"agree", "kind", "level", "p", "setting"}:
        return None
    agree, kind, level, p, setting = (
        member[name] for name in ("agree", "kind", "level", "p", "setting")
    )
    if any(type(flag) is not int or flag not in (0, 1) for flag in (agree, kind)):
        return None
    if type(level) is not int or not 0 <= level < len(LEVELS):
        return None
    if type(setting) is not int or not 0 <= setting < len(MODEL_IDS):
        return None
    try:
        _finite(p)  # the same root check decode_answers' members use, never OverflowError
    except ValueError:
        return None
    if not 0 <= p <= 1:
        return None
    return ShadowSummary(
        agree=agree == 1,
        kind_agree=kind == 1,
        level=LEVELS[level],
        p=float(p),
        setting=tuple(MODEL_IDS)[setting],
    )


def decode_trial(text: str) -> TrialSummary | None:
    """The trial summary `encode_answers` wrote, or None when `text` is not an answers
    encoding, carries no "trial" member, or that member is not exactly {"live", "trial"}, each
    a finite number in 0..1. Never raises."""
    try:
        value = json.loads(text, parse_constant=_no_constant)
    except ValueError, RecursionError:
        return None
    if not isinstance(value, dict):
        return None
    member = value.get(TRIAL)
    if not isinstance(member, dict) or member.keys() != {"live", "trial"}:
        return None
    try:  # the same root check decode_answers' members use, never OverflowError
        live, tried = _finite(member["live"]), _finite(member["trial"])
    except ValueError:
        return None
    if not (0 <= live <= 1 and 0 <= tried <= 1):
        return None
    return TrialSummary(live=float(live), trial=float(tried))


def _episode_answers(
    answers: Answers,
    reversal: bool | None,
    shadow: ShadowSummary | None,
    trial: TrialSummary | None,
) -> str:
    """An episode's answers as stored: with the shadow and the trial summaries while the whole
    text fits `ANSWERS_CHARS`, else with the trial alone, else with neither."""
    for summary, tried in ((shadow, trial), (None, trial)):
        try:
            return encode_answers(answers, reversal=reversal, shadow=summary, trial=tried)
        except ValueError:
            continue
    return encode_answers(answers, reversal=reversal)


def _decoded(name: str, member: object) -> Answer:
    if not isinstance(member, dict):
        raise ValueError("answer not an object")
    keys = member.keys()
    if keys == {"noul"}:
        return NoulAnswer(_finite(member["noul"]))
    if keys == {"score", "confidence", "probabilities"}:
        return ScoreAnswer(
            score=_finite(member["score"]),
            confidence=_finite(member["confidence"]),
            probabilities={
                _level(level): _finite(p) for level, p in _object(member["probabilities"]).items()
            },
        )
    if keys == {"confidence", "probabilities"}:
        probabilities = {
            option: _finite(p) for option, p in _object(member["probabilities"]).items()
        }
        if not probabilities:
            raise ValueError("choice without probabilities")
        return ChoiceAnswer(
            choice=_likeliest(name, probabilities),
            confidence=_finite(member["confidence"]),
            probabilities=probabilities,
        )
    raise ValueError("unknown answer")


def _likeliest(name: str, probabilities: Mapping[str, float]) -> str:
    """The option with the highest probability; on a tie, the one the playbook lists first."""
    order = _CHOICE_OPTIONS.get(name, ())

    def place(option: str) -> int:
        return order.index(option) if option in order else len(order)

    return max(probabilities, key=lambda option: (probabilities[option], -place(option)))


def _finite(value: object) -> float:
    """A finite number that is not a flag, as it is. An int too large to become a float (which
    `math.isfinite` would refuse with `OverflowError`, not an answer) is just as not-finite."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("not a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError("not a finite number")
    return value


def _option(option: object) -> str:
    if not isinstance(option, str):
        raise ValueError("option")
    return option


def _level_name(level: object) -> str:
    if type(level) is not int or level < 0:
        raise ValueError("score level")
    return str(level)


def _level(name: str) -> int:
    if not (name.isascii() and name.isdecimal()) or str(int(name)) != name:
        raise ValueError("score level")
    return int(name)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("not an object")
    return value


def _no_constant(name: str) -> object:
    raise ValueError(name)  # NaN and the infinities are no JSON numbers


# ---- small helpers -------------------------------------------------------------------------------


def _is_key(value: object) -> bool:
    return isinstance(value, str) and _HEX_KEY.fullmatch(value) is not None


def _stored(
    prior: ItemRecord | None, state_hash: str | None
) -> tuple[dict[str, Answer], bool | None] | None:
    """The answers and agreement the stored item holds for `state_hash`; None when it holds
    none, holds them for another hash, or they do not read back."""
    if (
        prior is None
        or state_hash is None
        or prior.state_hash != state_hash
        or prior.answers_json is None
    ):
        return None
    try:
        answers, reversal = decode_answers(prior.answers_json)
    except ValueError:
        return None
    return (answers, reversal) if answers else None


def _read_back(
    answers: Answers, questions: Mapping[str, Mapping[str, object]]
) -> tuple[str, dict[str, Answer]] | None:
    """A reply's answers as stored and as storage reads them back; None when they cannot be
    stored or do not answer exactly the asked names."""
    try:
        text = encode_answers(answers)
        read, _ = decode_answers(text)
    except ValueError:
        return None
    return (text, read) if read.keys() == questions.keys() else None


def _active(presence: Presence) -> Mention | None:
    """What the user has open: the note (its file name as the title, its tags unknown), else
    the chat's title, else nothing."""
    if presence.note_ref is not None:
        ref = presence.note_ref[1]
        title = ref.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".md")
        return Mention(title=title, ref=ref, tags=None)
    if presence.chat_title:
        return Mention(title=presence.chat_title, ref=None, tags=())
    return None


def _card_quiet(candidate: Candidate, now: datetime) -> bool:
    """Whether a card's item is within `CARD_QUIET` of its card's change (`facts["changed_at"]`);
    a change time that is not an aware datetime reads as within it."""
    changed = candidate.facts.get("changed_at")
    if not isinstance(changed, datetime) or changed.utcoffset() is None:
        return True
    return now - changed < CARD_QUIET


def _showing(item: ItemRecord | None) -> str | None:
    """The level an item shows the user now: open, timed "now" and not silent; else None."""
    if item is None or item.state != "open" or item.timing != "now" or item.level == "silent":
        return None
    return item.level


def _session(value: str | None) -> str | None:
    """A chat id as a call record keeps it: a canonical UUID, else None."""
    if value is None:
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def _split(
    built: BuiltState, questions: Mapping[str, Mapping[str, object]]
) -> tuple[tuple[str, int], ...]:
    """The state's parts with the estimated tokens of the questions this request asked."""
    parts = tuple(part for part in built.split if part[0] != "questions")
    tokens = playbook.estimate_tokens(playbook.questions_text(questions))
    return (*parts, ("questions", tokens))


def _context_call(item: ItemRecord | None) -> str | None:
    """The call the stored item's "See why" opens, if it has one."""
    if item is None:
        return None
    for action in item.body.actions:
        if action.type == "open_context" and action.call_id:
            return action.call_id
    return None


def _total(values: Iterable[int | None], high: int | None) -> int | None:
    """The sum of the values present, at most `high`; None when none is present."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    total = sum(present)
    return total if high is None else min(total, high)
