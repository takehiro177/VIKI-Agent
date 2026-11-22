"""The Decision loop's engine: the clock that runs cycles, the limits every Decision model
request passes, and what the engine keeps between cycles.

The clock. `start` runs a cycle at once ("app_open"), then one per cadence (`OBSERVE_S` for
"change", 300 or 900 seconds for "5m" or "15m"), and one `COALESCE_S` after the first `wake` no
cycle has covered yet, so a burst of wakes (a presence change, the launcher, an API write, an
ended mission) makes one cycle. Every `HEARTBEAT_S` the beat closes the λ window: while
Decision is on, each scope that ran a cycle in it stores `arbiter.pace(λ, attention,
nudges_per_hour)` as its `lambda`, where attention sums `contracts.ALPHA` of each level its
cycles newly showed (`CycleResult.attention`: an alert 3, a nudge 1, a badge 0.1); then, while
learning (`decision.learning`) or Self-improvement (`decision.watch.improve`) is on, it reads
each such scope's decisions since `learn_since` once (`DecisionLoopStats.learning_rows`): while
learning is on, the scope stores `learning.relearn` over them as its `learned` snapshot; while
Self-improvement is on, the engine keeps `improve.from_rows` over them, under the scope's
wording in use and with no question to tune while a proposal is on trial, as the scope's
self-improvement candidates until the next beat, or until Delete all or Reset drops them; with
Self-improvement off, it drops every scope's. The beat writes in a worker thread under the cycle
lock, halting no run, so it overlaps no cycle and no `between_cycles` work; cancelled, it holds
the lock until that thread ends. A
store failure there is logged by its code and leaves that scope's λ, snapshot or candidates as
they were. A beat that fails otherwise logs one fixed line and is not made up; the clock goes
on. A pass of the clock over two beats long (the computer slept) runs one cycle and starts the
beats again from then; the checks and beats it slept through are not made up. One cycle runs at
a time.

A cycle reads the settings. While Decision is on, not paused and `attended()`, it gathers the
active workspace in a worker thread, Kanban too while `decision.watch.kanban` is on
(`DecisionSettings.watch_kanban`) or, with a seat host (`seats`), while Start Bots' work
(`decision.grant.team`) is not Off. While vault changes (`contracts.CHANGES`) are detected
(`detectors.detects_changes`, which holds only in an ordinary vault, `Observation.policy`), it
then reads what the scope's change rows hold back on the day of the cycle's one clock read
(`ChangeProposals.held`, with whether the scope has room for another row, and `today`; rows that
cannot be read count as the limit reached, logged by the code), before the vault-change read the
gather left (`Observation.read_changes`). That read then skips, before
spending a read, what the rows hold (`vault_watch.read_changes`); once the day's change rows in
every workspace reach `decision.limit.changes`, when the scope has no room, or when the rows
cannot be read, it reads only the notes changed lately, for the user part, and nothing a change
could be built from. While vault changes are not detected, no row is read and the read stops
after the notes changed lately, as at the cap. While Vault health (`decision.watch.vault_health`)
is on, it then runs the vault Look read the gather left (`Observation.read_look`) in a worker
thread and adds that read's failure words to the observation's; with Vault health off the read
does not run. With a code host (`code`), in a vault cycle whose policy allows prepare
(`detectors.POLICY_WORK`) while Look on my behalf (`decision.grant.look`) is not Off, it then
reads the trusted code folders (`CodeHost.read` at the cycle's clock read) in a worker thread, as
`Observation.code`, and adds that read's failure words to the observation's; otherwise no folder
is read. When the gather read Kanban
(`Observation.kanban`) while Kanban is on, the scope's open card items (`contracts.CARDS`)
are read next, in a worker thread (`DecisionAgenda.open_items`; a failed read fails the cycle),
as `Observation.open_cards`, so the card selection keeps their places and `cycle.run_cycle`
keeps the items `detectors.kanban_keep` names open as they are; with Kanban off it keeps
none. The cycle then detects its candidates (card items only while Kanban is on), adds its
second looks and project-plan findings (below) and, when any is a Look mission
(`contracts.LOOK_MISSIONS`), a second look (`contracts.SECOND_LOOKS`) or a project plan
(`contracts.PROJECT_PLANS`), drops each such one whose key a stored mission of any scope holds
(`MissionService.taken`, in a worker thread; a failed read fails the cycle), so an offer closes
once a mission holds its key, then the project-plan offers learning pauses and the message-draft
offers it does not admit (both below). In a
vault, with a seat host and while Start Bots' work is not Off or Routines
(`decision.watch.routines`) is on, it reads the vault's seat facts next, in a worker thread
(`SeatHost.facts`, the runs since the oldest change of a seat's `todo` card,
`seats.cards_since`; a failed read fails the cycle), plans the seats over them (`seats.plan`)
with the runs waiting on a card (`SeatHost.waiting`), the vault's policy, the plan brake's hold
at the cycle's clock read and whether quiet hours hold, and adds the plan's seat items
(`detectors.seat_candidates`). With a Look host (`looks`), it then reads the live app's Look on
the event loop (`LookHost.read`), each part only while its switch is on: the waiting chats while
Approvals (`decision.watch.approvals`) is on, the heavy MCP servers and the uncertain sends while
Chats and runs (`decision.watch.chats`) is on, and the stopped bridges while Messaging
(`decision.watch.messaging`) is on; and adds its items (`looks.app_candidates`), leaving out the
chats of the running missions and of the seat runs waiting on a card (`SeatHost.waiting`). It
then adds, while Self-improvement is on and the workspace is
a vault, the self-improvement candidates the
last beat kept, but no question to tune while the scope's wording (below) has a proposal on
trial or whose cached wording no longer matches the question as it is worded now, drops each
vault change no proposal could be filed for, by that same read of the rows, so that no request
is spent on it: all of them at the limit, with no room or with rows not read, and any whose key
a change row of the scope holds, or whose target an open row holds, keeps the first
`MAX_OPEN_ITEMS` by deadline (soonest first, none last), topic and title, runs `cycle.run_cycle`
with the failure memory the last cycle of that scope handed back, each item's showings in the
last day (`DecisionLoopStore.exposures_since`), λ, the arbiter's weights, T and C
(`learning.inputs`): the scope's stored snapshot while learning is on, else, or when
the snapshot does not read back, the start values; and the scope's wording
(`QuestionProposals.wording`, `proposals`): the wording in use, its version and the open
proposal as the trial, or, when the proposals cannot be read, the playbook's own wording and
version and no trial, logged by the code; it passes the cycle what holds VIKI's own starts too
(Held starts, below). It then prunes the agenda and files each vault change
it committed open, neither silent nor held to a later time (timed "at"), in candidate order, as
an open change proposal (`ChangeProposals.file`, which counts the day's rows against the limit
in its own write transaction; one a cycle for each note marked replaced and each file of ticked
tasks), and sets its item done; a refusal leaves the item as it is. The filing runs under the
cycle lock, so no `between_cycles` work runs beside it. `run_cycle` records each
call and adds each reply's measured spend. The engine then starts a mission for each committed
item that is open, shown now and has no mission, whose work's grant (`cycle.work_grants`) is
"allowed": once per item, state hash and work, so neither a refusal nor an ended mission starts
another until the item's state or work changes, and none once the user stopped a mission of
that item, whatever its state or work. Just before each start it makes on its own (a
mission, a seat run, a saved check or an inbox run) it reads the settings again (`_gate`), so a
change the user saves while the cycle waits on reads, the model or an earlier start holds it,
and reads the clock again for that start's holds; none starts unless Decision is still on, not
paused and an app window open (`attended`). No mission starts while the plan brake holds
(`plan_brake.plan_hold` over a fresh `plan_reading()` and that read's `decision.plan_brake`; no
reading yet this launch holds every one), while Never send differs from the cycle's settings or
cannot be read, while a Stop all holds them (below), or once its work's grant is no longer
"allowed"; the user's own start does not ask. A self-improvement item (`contracts.IMPROVE`)
starts none, whatever the grants: it only prepares a draft; nor does a saved check
(`contracts.SAVED_CHECKS`) or an inbox suggestion (`contracts.INBOX_TRIAGE`), both below. A Look
mission starts only for work prepare under Look on my behalf, a second look only for work prepare
under Take a second look at my work (`decision.grant.second_look`), its brief leaving out the
finding kinds learning pauses, a project plan only for work prepare under Plan next steps for my
projects (`decision.grant.project_plan`), and none of them while quiet hours hold at that clock
read in the user's zone. A held start is not remembered, so it starts at the first cycle after
the hold ends. A Stop all (`note_stop_all`, which the service calls before it stops anything)
holds the missions and seat runs VIKI starts on its own in the cycle in flight, by the count that
cycle read before its first await, and in every cycle that reaches its starts within a cadence
(`decision.cadence`) of it, so the cycle its own wake brings does not refill the slots it freed
with the offers still waiting; the clock's next cycle on its cadence starts them as usual. A
start past the refusals stores its mission, which keeps that rule across
app launches: `MissionService.start` refuses VIKI work already started at the item's state, or
for an item whose mission the user stopped (`tried`), and a look, a second look or a project
plan whose key any mission holds. The engine also remembers its last `MAX_ATTEMPTS` attempts,
refused ones too, so it does not ask again for those each cycle; a look, a second look and a
project plan skip that memory, so one refused at the limit or while busy is asked again at a
later cycle, and a start of any type refused while busy (VIKI's own start waits while any
mission is open) is forgotten, so it is asked again at the next cycle.
While Start Bots' work is Allowed, the engine then
starts each seat start the plan made, in order, through the seat host alone (`SeatHost.start`,
with the `decision.limit.team` of the settings read just before it and the cycle's day), never
as a mission, so it stores no mission row. None starts unless, at that read, Decision is still on,
not paused and an app window open, Start Bots' work is still Allowed, the plan brake does not
hold (as above) and no Stop all holds it; in quiet hours at that clock read only a routine whose
revision stops at a draft starts. A refused start is skipped, a halted cycle starts no more, and
any start wakes the clock once.
The engine keeps no memory of seat starts: the next cycle's facts show the started run as open,
so its seat starts nothing more. On or off, the cycle then
polls the missions: each one that ended, or whose plan waits, becomes a `mission_report`
candidate of its own scope until the user closes its item, and wakes the clock once.

Held starts. Each cycle hands `cycle.run_cycle` what holds VIKI's own starts as it begins
(`cycle.Holds`, `CycleContext.holds`), so an item whose own start VIKI holds offers that start to
the user and its why names the hold (`cycle.held_start`): the plan brake over a fresh
`plan_reading()` at the cycle's clock read (no reading yet holds), quiet hours at that read in
the user's zone, any open mission (`MissionService.running`, as VIKI's own start then waits),
and the `cycle.START_FAMILIES` whose missions started on the cycle's day, the user's own
counted, reach their limit (`_limited`: `MissionService.count_looks`, `count_second_looks`,
`count_project_plans` and `count_today` for prepare and run, against `decision.limit.look`,
`second_look`, `project_plan`, `drafts` and `chats`), read with the running missions in the
inputs' worker thread; a failed read fails the cycle. The holds shape only the items: each start
still reads its own gates just before it, as above, so an item may name a hold one cycle after
it began or ended.

Second looks. Each cycle lists, on the event loop, one finding item (`second_looks.finding`) for
each second look of the scope whose plan card waits (`MissionService.findings`), whatever the grants
and the page, so a finding closes once its plan is answered or its chat ends. The engine keeps, for
each scope and chat, the last reply the page reported worth a second look (`PageFacts.second_look`),
at most `MAX_KEPT_OFFERS`, the oldest forgotten first, in memory only, so a restart forgets them:
in a vault cycle whose policy allows prepare (`detectors.POLICY_WORK`) while Take a second look at
my work is not Off, a reply in a chat other than VIKI's own (`decision.launcher_session`) with an
admitted note ref is kept once `MissionService.owner_chat` (in a worker thread) says the chat is
the user's own, and a reply of a chat already kept replaces its own without that read. A kept
reply is forgotten once the page shows its chat (`Presence.chat_id`) with no reply worth a second
look or with another one, and once a mission holds its offer's key (after the `taken` read);
while the page shows another chat, or none, or reports a reply of another chat, it stays.
Presence merges every open window into one page signal, so with two windows open, the other
window's page with no reply worth a second look can forget the reply early; that is accepted
(ADR-076). Each cycle adds one offer
(`second_looks.offer`) for each reply kept in its scope, under the same vault, policy and grant
rule. With an offer it reads, in a worker thread, what the scope's second looks yielded
(`second_looks.yields` over `MissionService.second_look_rows` since the later of
`SECOND_LOOK_WINDOW` before the cycle's clock read and `learn_since`, under the restarts the
"second_looks" preference holds; no Not useful reasons), and drops every offer while learning
pauses every finding kind, else each offer whose type learning pauses. A failed read fails the
cycle.

Project plans. `detectors.detect` lists the project-plan offers (`project_plans.stalled_projects`
and `project_plans.overdue_tasks`). Each cycle also lists, on the event loop, one finding item
(`project_plans.finding`) for each project plan of the scope whose plan card waits
(`MissionService.findings`), whatever the grants, so a finding closes once its plan is answered
or its chat ends. While an offer is left after the `taken` drop, it reads, in a worker thread,
what the scope's project plans yielded (`second_looks.yields` with `looks=PROJECT_PLANS` and no
kinds, over `MissionService.second_look_rows` of `PROJECT_PLANS` since the later of
`SECOND_LOOK_WINDOW` before the cycle's clock read and `learn_since`, under the restarts the
"project_plans" preference holds; no Not useful reasons), and drops the offers of each type
learning pauses. A failed read fails the cycle. VIKI's own start then needs Plan next steps for
my projects Allowed, work prepare, no quiet hours, no plan brake and Never send unchanged since
the cycle read it, each read again just before the start (a rule saved after detection holds the
start, and the next cycle detects with the rule); it leaves nothing out of the brief and keeps no
attempt memory.

Message drafts. `detectors.detect` lists the message-draft offers (`message_drafts.offers`).
While one is left after the project plans' pause, the cycle admits them in one worker-thread
call, and a failed read fails the cycle. It drops each offer whose chat `MissionService.owner_chat`
does not say is the user's own, one read a chat. With an offer left, it reads what the scope's
message drafts yielded (`second_looks.yields` with `looks=MESSAGE_DRAFTS` and no kinds, over
`DecisionLoopStats.draft_rows` since the later of `SECOND_LOOK_WINDOW` before the cycle's clock
read and `learn_since`, under the restarts the "message_drafts" preference holds; no Not useful
reasons), and drops the offers of each type the user stopped with Stop these
(`DecisionLoopStore.preferences`, read first) or learning pauses. With an offer still left, it
reads how many message drafts every scope raised on the cycle's day, at any level, and which
offers' keys the scope already stores (`DecisionLoopStats.drafts_today`): an
offer whose key is stored stays, and the others are admitted in order while that count plus
those admitted stays under `decision.limit.message_draft`. A message draft starts nothing,
whatever the grants: the cycle gives it no work.

Saved checks. With a check host (`checks`), in a vault cycle whose policy allows run
(`detectors.POLICY_WORK`: an ordinary vault only) while Run my saved checks
(`decision.grant.check`) is not Off and the host says this computer runs checks
(`CheckHost.supported`), the cycle reads the folders whose commit its saved checks have not all run
on (`CheckHost.read` at the cycle's clock read) in a worker thread, after the code folders, as
`Observation.checks`, and adds that read's failure words to the observation's; otherwise nothing is
read. `detectors.detect` lists their items (`saved_checks.candidates`). After the message drafts,
while such an item is left, the cycle admits them in one worker-thread call, and a failed read
fails the cycle: it drops each type the user stopped with Stop these
(`DecisionLoopStore.preferences`, read first) or learning pauses (`second_looks.yields` with
`looks=SAVED_CHECKS` over `DecisionLoopStats.draft_rows` with `closed=""`, under the restarts the
"saved_checks" preference holds, the window as for message drafts), then reads how many saved-check
items every scope raised on the cycle's day, at any level, and which keys the scope already stores
(`DecisionLoopStats.drafts_today`): a stored key stays in any phase, and a waiting folder's new key
is admitted while that count plus those admitted stays under `decision.limit.check`; a running or
ended folder raises no new item. A saved check is never a mission. Last, while Run my saved checks
is Allowed in the cycle's settings, for the committed items whose work is "run", open and shown
now, the engine reads the day's saved-check items (`drafts_today`, in a worker thread), then, just
before each folder's start, the settings again, and starts nothing more unless Decision is still
on, not paused and an app window open, Run my saved checks still Allowed, that read's quiet hours
do not hold at its clock read in the user's zone, and the day's count is not above that read's
`decision.limit.check`. A held start is not remembered, so it starts at the first cycle after the
hold ends; the plan brake does not hold it, nor does a plan reading not yet come, as a check
spends no plan. It
then starts each item's pending checks, in order, through the check host alone (`CheckHost.start`
with the folder, its commit and the pending ids); a refused start is skipped, a halt stops the
start in progress (a run it already spawned stays supervised by the host) and starts no more, and
any check started wakes the clock once. Each start carries the Stop all count the cycle read as it
began, before its first await (`CheckHost.stops`), so a Stop all pressed once a cycle began starts
no check in it.

Inbox suggestions. With an inbox host (`inbox`), in a vault cycle whose policy allows prepare
(`detectors.POLICY_WORK`: an ordinary or protected vault) while Suggest destinations for my inbox
(`decision.grant.inbox_triage`) is not Off and Inbox (`decision.watch.inbox`) is watched, the cycle
reads what the host remembers of the vault's suggestion run (`InboxHost.read`) on the event loop,
after the saved checks, as `Observation.inbox`; otherwise the host is not read.
`detectors.detect` lists the inbox's item (`inbox_triage.candidates`). After the saved checks,
while such an item is left, the cycle admits it in one worker-thread call, and a failed read fails
the cycle: an item whose run found nothing (phase "nothing") is dropped and its stored item, if
open, set done; then the admission drops each type the user stopped with Stop these or learning
pauses (`second_looks.yields` with `looks=INBOX_TRIAGE` over `DecisionLoopStats.draft_rows` with
`closed=""` and `done="nothing"`, under the restarts the "inbox_triage" preference holds) and
admits a stored key in any phase, or a waiting inbox's new key while the day's inbox-suggestion
items in every scope, at any level (`drafts_today`), plus those admitted stay under
`decision.limit.inbox_triage`, as for saved checks. An inbox suggestion is never a mission. Last,
while Suggest destinations for my inbox is Allowed in the cycle's settings, for the committed
items whose work is "prepare", open and shown now, the engine reads the day's inbox-suggestion
items (`drafts_today`, in a worker thread), then, just before the start, the settings again, and
starts nothing unless Decision is still on, not paused and an app window open, the grant still
Allowed, Inbox is still watched, Never send reads as usable and unchanged from the cycle's
settings, that read's quiet hours do not hold at its clock read in the user's zone, the plan
brake does not hold under that read's `decision.plan_brake` at that clock read (no reading yet
holds it), as the run spends plan, and the day's count is not above that read's
`decision.limit.inbox_triage`. A held start is not remembered, so it starts
at the first cycle after the hold ends, which checks Never send against the listing again. Just
before each start, with no await between, it reads the host again and skips the start when the
latest run is not the one the cycle read (the user's own Ask VIKI to suggest has started since,
and saw every capture). It then starts the run through the inbox host alone (`InboxHost.start` with
the scope and the item's key); a refused start is skipped, a halt stops the start in progress (a
start the host already began runs on, shielded) and starts no more, and a start wakes the clock
once. The host keeps each started key, so a state is not started again. When the host's keys
outlive a restart (`InboxHost.persistent`) a state still waiting from an earlier launch starts as
any other; otherwise the engine starts only an item created since it was built (its clock's wall
time then), so a state from an earlier launch is not started again on a relaunch, and under
Allowed such an item offers Suggest destinations, as under Ask first (`CycleContext.inbox_since`),
so the user's own start reaches it.

The model. The cycle's `ask` answers None, leaving that item to the rules, while: no key is
saved; a request was refused with 401 and the key has not been saved since; the day's measured
spend over every scope plus this request's estimated cost reaches the daily cap; a 429 or 529
cools every item for its `retry_after_s` (`COOL_S` when it named none); the last `WINDOW_S`
seconds hold `MAX_PER_MINUTE` requests; or `MAX_IN_FLIGHT` requests stay in flight for
`SLOT_WAIT_S`. Otherwise it makes one request and answers the reply or the model's error; a
request is not retried. A refused, too large or invalid answer stays with its item until the
item's state hash changes (the cycle's failure memory); an unreachable model leaves that item to
the rules for this cycle. While shadowing is on (`decision.shadow` is not "off"), the cycle's
`shadow_ask` sends the shadow version's requests through the same limits and cap; a shadow
request leaves no call record and, failed, no failure memory: its item keeps its live answer.
While a proposal is on trial, the cycle's `trial_ask` sends the trial's requests the same way.
`test_key` asks one yes/no question about a fixed state that holds no user data, through the
same slots, window and cooling but not the cap; its measured spend counts toward the day under
`TEST_SCOPE`, and it records no call, since a call row belongs to an item. `status` answers the
model's state as the launcher and the page show it.

Stopping. `stop`, `between_cycles` and a `recheck` past `RECHECK_S` halt a cycle at its next
safe point: a request in flight is cancelled, and the cycle ends before its next request or its
next item, so each item is committed whole or not at all. A halted cycle keeps the failures it
met but hands back no counts. A cycle still waiting for the lock is dropped. `stop` and
`between_cycles` halt every key test the same way. `between_cycles(work)` leaves the clock
running and runs `work` in a worker thread under the cycle lock, so no cycle writes while it
runs: the service's Delete all, Reset and proposal acts go through it, and Delete all and Reset
drop the scope's self-improvement candidates there (`forget_improve`).

Logs hold fixed text and codes only: never the key, a request or an answer. A failed cycle's
line names the step it was at (`_Run.step`, a fixed name) and the error's code, with the store's
or sealing's own code in brackets when the loop's error replaced one (`_code`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from pathlib import PurePosixPath

from viki_agent import protection
from viki_agent.automation.kanban import KanbanRead
from viki_agent.automation.routines import parse_stamp
from viki_agent.automation.scheduler import Clock
from viki_agent.decision_loop import (
    arbiter,
    improve,
    learning,
    looks,
    owner,
    playbook,
    project_plans,
    seats,
    second_looks,
)
from viki_agent.decision_loop.agenda import DecisionAgenda
from viki_agent.decision_loop.changes import _TARGETED, ChangeProposals, Held
from viki_agent.decision_loop.contracts import (
    CANDIDATES,
    CARDS,
    CHANGES,
    COALESCE_S,
    FINDING_KINDS,
    HEARTBEAT_S,
    IMPROVE,
    INBOX_TRIAGE,
    LOOK_MISSIONS,
    MAX_IN_FLIGHT,
    MAX_OPEN_ITEMS,
    MAX_PER_MINUTE,
    MESSAGE_DRAFTS,
    MODEL_ERROR_CODES,
    MODEL_IDS,
    OBSERVE_S,
    PAIRED,
    PROJECT_PLANS,
    RECHECK_S,
    SAVED_CHECKS,
    SECOND_LOOK_WINDOW,
    SECOND_LOOKS,
    TRIGGERS,
    Candidate,
    CheckHost,
    CodeHost,
    DecisionLoopError,
    DecisionSettings,
    InboxHost,
    InboxRun,
    LookHost,
    MissionRecord,
    ModelError,
    ModelPort,
    ModelReply,
    ModelRequest,
    PageFacts,
    SeatHost,
    SecondLookHint,
    SecondLookYields,
    canonical_json,
    quiet_until,
)
from viki_agent.decision_loop.cycle import (
    START_FAMILIES,
    STICKY_FAILURES,
    CycleContext,
    CycleResult,
    Holds,
    Trial,
    cost_micro_usd,
    run_cycle,
    work_grants,
)
from viki_agent.decision_loop.detectors import (
    POLICY_WORK,
    Observation,
    detects_changes,
    kanban_keep,
    seat_candidates,
)
from viki_agent.decision_loop.key_store import DecisionKeyStore, KeyStatus
from viki_agent.decision_loop.missions import MissionError, MissionService
from viki_agent.decision_loop.plan_brake import PlanReading, no_reading, plan_hold
from viki_agent.decision_loop.presence import PresenceTracker
from viki_agent.decision_loop.proposals import QuestionProposals
from viki_agent.decision_loop.state import History, Mention, Owner
from viki_agent.decision_loop.stats import DecisionLoopStats, LearningRow
from viki_agent.decision_loop.store import DecisionLoopStore
from viki_agent.storage import StateError

log = logging.getLogger(__name__)

CADENCE_S: Mapping[str, float] = {"change": OBSERVE_S, "5m": 300.0, "15m": 900.0}
WINDOW_S = 60.0  # the sliding window `MAX_PER_MINUTE` counts requests in
SLOT_WAIT_S = 5.0  # how long a request waits for an in-flight slot: one request's deadline
COOL_S = 60.0  # how long a 429 or 529 that names no wait cools every item
MAX_COOL_S = 600.0
MAX_ATTEMPTS = 4 * MAX_OPEN_ITEMS  # the mission starts remembered, the oldest forgotten first
MAX_KEPT_OFFERS = 10  # the chats' replies kept for a second-look offer, the oldest forgotten first
# What `test_key` sends: one of the playbook's yes/no questions about a fixed, made-up item,
# after the guide every request's state opens with, so a key test sends a decision's shape.
TEST_QUESTION = "actionable"
TEST_STATE: Mapping[str, object] = {
    "guide": list(playbook.GUIDE),
    "candidate": {"type": "task due", "title": "Reply to a short message"},
}
# The scope a key test's spend is kept under: the nil id, which is no workspace's. The daily
# cap and the day's total (`DecisionLoopStore.spent_on`) sum every scope.
TEST_SCOPE = str(uuid.UUID(int=0))

_TOPIC_RANK = {name: rank for rank, name in enumerate(CANDIDATES)}
# The types VIKI takes once per key: a key any mission holds closes the item.
_ONCE_A_KEY = LOOK_MISSIONS + SECOND_LOOKS + PROJECT_PLANS
# What change rows that cannot be read hold back from a vault-change read: every change, as at
# the cap.
_UNREAD = Held(frozenset(), frozenset(), room=False)
_DAY = timedelta(hours=24)  # the window of an item's showings the arbiter counts
_MICRO = 1_000_000
_MAX_TOKENS = 1_000_000  # the most input tokens a stored cost counts, as the cycle bounds them


@dataclass(frozen=True)
class EngineStatus:
    """The Decision model as the launcher and the page show it. `state` is one of
    `wire.MODEL_STATES`; `checked_at` is when the last cycle decided; `last_error` the code of
    the last model error; `last_test` (when, latency_ms) of the last key test answered."""

    state: str
    checked_at: datetime | None
    last_error: str | None
    last_test: tuple[datetime, int] | None


class _Halted(Exception):
    """A halted cycle or key test stopping at a safe point."""


@dataclass(eq=False)  # hashable by identity: the engine keeps its runs in a set
class _Run:
    """One cycle or key test: its trigger, the event that halts it, whether it holds the cycle
    lock yet, and the step a cycle is at: a fixed name, which a failed cycle's log line names."""

    trigger: str
    halt: asyncio.Event = field(default_factory=asyncio.Event)
    entered: bool = False
    step: str = "settings"


@dataclass(frozen=True)
class _Gate:
    """What holds a start VIKI makes on its own, read just before it (`_gate`): the settings read
    again; whether, at that read's clock, quiet hours hold (`quiet`) and the plan brake holds
    over a fresh plan reading (`brake`); and whether Never send differs from the cycle's own
    settings or cannot be read (`never_send`). Each start path applies its own share of them."""

    settings: DecisionSettings
    quiet: bool
    brake: bool
    never_send: bool


def owner_of(obs: Observation) -> Owner:
    """The user's side as the observation alone tells it: the active projects as goals, each
    titled by its note's title, else its file name. They are read newest-modified first; sorting
    them by ref keeps an item's state hash, and so its once-per-state work, unchanged when only
    their order moves."""
    goals = sorted(obs.goals, key=lambda goal: goal.ref)
    return Owner(
        goals=tuple(Mention(goal.title or _stem(goal.ref), goal.ref, goal.tags) for goal in goals)
    )


def no_history(scope: str, item_key: str) -> History:
    """No earlier items: the history every item has until something keeps one."""
    return History()


class DecisionLoopEngine:
    """The Decision loop's clock, limits and memory; see the module docstring.

    `gather(page, today, kanban)` reads the active workspace in a worker thread, Kanban
    too when `kanban` (`settings.watch_kanban`); `detect(obs, settings, missions_ended, now)` is
    `detectors.detect`. `zone` is the user's time zone (None: this computer's), which places
    the day, quiet hours and the moment. `owner_for(obs)` and `history_for(scope, item_key)`
    feed each item's state; both run in a worker thread. `plan_reading()` is the newest plan
    reading (None: none yet this launch, which holds them all), which the plan brake holds VIKI's
    own mission, seat and inbox starts against.
    `seats` reads and starts the vault routines' seats (None: no seat is read or started).
    `looks` reads the live app's Look (None: no app Look is read). `code` reads the trusted code
    folders for Look missions (None: no folder is read). `checks` reads and starts the saved
    checks (None: no saved check is read or started). `inbox` reads and starts the inbox
    suggestion run (None: no inbox suggestion is read or started).
    """

    def __init__(
        self,
        store: DecisionLoopStore,
        agenda: DecisionAgenda,
        keys: DecisionKeyStore,
        model: ModelPort,
        missions: MissionService,
        presence: PresenceTracker,
        read_settings: Callable[[], DecisionSettings],
        gather: Callable[[PageFacts | None, date, bool], Observation],
        detect: Callable[
            [Observation, DecisionSettings, Sequence[MissionRecord], datetime],
            Sequence[Candidate],
        ],
        *,
        clock: Clock,
        attended: Callable[[], bool],
        zone: tzinfo | None = None,
        owner_for: Callable[[Observation], Owner] = owner_of,
        history_for: Callable[[str, str], History] = no_history,
        plan_reading: PlanReading = no_reading,
        seats: SeatHost | None = None,
        looks: LookHost | None = None,
        code: CodeHost | None = None,
        checks: CheckHost | None = None,
        inbox: InboxHost | None = None,
    ) -> None:
        self._store = store
        self._stats = DecisionLoopStats(store.store)
        self._proposals = QuestionProposals(store.store)
        self._changes = ChangeProposals(store.store)
        self._agenda = agenda
        self._keys = keys
        self._model = model
        self._missions = missions
        self._presence = presence
        self._read_settings = read_settings
        self._gather = gather
        self._detect = detect
        self._clock = clock
        self._attended = attended
        self._zone = zone
        self._owner_for = owner_for
        self._history_for = history_for
        self._plan_reading = plan_reading
        self._seats = seats
        self._looks = looks
        self._code = code
        self._checks = checks
        self._inbox = inbox
        # an inbox host that forgets its started keys on a relaunch pre-runs only a state raised
        # since this instant, so a stored one is not sent again on every launch
        self._since = None if inbox is not None and inbox.persistent else clock.wall()
        self._lock = asyncio.Lock()
        self._event = asyncio.Event()  # wakes the clock
        self._freed = asyncio.Event()  # set, and replaced, when an in-flight slot frees
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._runs: set[_Run] = set()
        self._wake_due: float | None = None
        self._wake_reason = "change"
        self._cadence_s = float(OBSERVE_S)
        # the model's limits and state
        self._in_flight = 0
        self._window: deque[float] = deque()
        self._cool_until = -math.inf
        self._cool_code = "rate_limited"
        self._rejected: tuple[str | None] | None = None  # the key stamp a 401 paused
        self._unreachable = False
        self._capped: tuple[str, int] | None = None  # (day, estimate) the cap last held back
        self._last_error: str | None = None
        self._last_test: tuple[datetime, int] | None = None
        self._checked_at: datetime | None = None
        # the memory between cycles
        self._failed: dict[str, dict[str, str]] = {}
        self._nudges: dict[str, float] = {}  # this λ window's attention newly asked, per scope
        self._improve: dict[str, list[Candidate]] = {}  # the beat's candidates, per scope
        self._reports: dict[str, MissionRecord] = {}
        # (scope, item key) -> (state hash, work) of the last mission start made or refused; a
        # cache, as the stored missions keep the rule (`tried`)
        self._attempts: dict[tuple[str, str], tuple[str | None, str]] = {}
        # (scope, chat) -> the chat's last reply worth a second look, kept for its offer until
        # the page shows that chat with another or no such reply (`_second_looks`)
        self._offers: dict[tuple[str, str], SecondLookHint] = {}
        # Stop all (`note_stop_all`): how many this launch, and the monotonic time of the last
        self._stop_alls = 0
        self._stopped_at = -math.inf
        self._nudge = 0
        self._glow = 0

    @property
    def nudge(self) -> int:
        """How many nudges and alerts cycles have newly shown since the engine was made."""
        return self._nudge

    @property
    def glow(self) -> int:
        """How many items cycles have newly listed (list or badge) since the engine was made."""
        return self._glow

    @property
    def proposals(self) -> QuestionProposals:
        """The question wording proposals each cycle reads its scope's wording and trial from."""
        return self._proposals

    @property
    def changes(self) -> ChangeProposals:
        """The vault-change proposals each cycle files the vault changes it shows into."""
        return self._changes

    # ---- the clock -----------------------------------------------------------------------------

    def start(self) -> None:
        """Start the clock; its first cycle runs at once. A stopped engine stays stopped."""
        if self._task is None and not self._closing:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Halt every cycle and key test at its next safe point, cancelling a request in
        flight, and wait for the clock to end."""
        self._closing = True
        for run in list(self._runs):
            self._halt(run)
        self._event.set()
        if self._task is not None:
            await asyncio.wait((self._task,))

    def wake(self, reason: str) -> None:
        """Ask for a cycle `COALESCE_S` after the first wake no cycle has covered yet; that
        wake's `reason` (one of `TRIGGERS`, else "change") is the cycle's trigger."""
        if self._closing:
            return
        if self._wake_due is None:
            self._wake_due = self._clock.monotonic() + COALESCE_S
            self._wake_reason = reason if reason in TRIGGERS else "change"
        self._event.set()

    async def recheck(self) -> None:
        """Run one cycle now and wait for it at most `RECHECK_S`; past that it is halted at its
        next safe point, or dropped while it still waits for another cycle to end."""
        if self._closing:
            return
        run = _Run("launcher")
        task = asyncio.ensure_future(self._locked(run))
        finished = asyncio.Event()
        task.add_done_callback(lambda _task: finished.set())
        try:
            await self._clock.wait(finished, RECHECK_S)
        finally:
            if not task.done():
                self._halt(run)
                if not run.entered:
                    task.cancel()
            await asyncio.wait((task,))

    async def between_cycles[T](self, work: Callable[[], T]) -> T:
        """Run `work` in a worker thread while no cycle runs, and answer what it answers.

        Every cycle and key test is halted at its next safe point, as `stop` halts them, but
        the clock keeps running. `work` then runs under the cycle lock, held until `work` ends
        even when the caller is cancelled first. A stopped engine runs it too."""
        for run in list(self._runs):
            self._halt(run)
        return await self._exclusive(work)

    def note_stop_all(self) -> None:
        """The user pressed Stop all: VIKI starts no mission or seat run on its own in a cycle
        already going, nor in one that reaches its starts within a cadence of it (`_gate`).
        Synchronous and never raises, so Stop all calls it before it stops anything."""
        self._stop_alls += 1
        self._stopped_at = self._clock.monotonic()

    def forget_improve(self, scope: str) -> None:
        """Drop the self-improvement candidates the last beat kept for `scope`; cycles decide
        none of them until the next beat reads the scope's rows anew. Call it from
        `between_cycles` work, as Delete all and Reset do: under the cycle lock no beat is
        between reading rows and keeping what it counted from them, so none read before can
        come back."""
        self._improve.pop(scope, None)

    async def _exclusive[T](self, work: Callable[[], T]) -> T:
        """Run `work` in a worker thread under the cycle lock, and answer what it answers. The
        lock is held until `work` ends, even when the caller is cancelled first."""
        async with self._lock:
            done = asyncio.ensure_future(asyncio.to_thread(work))
            try:
                return await asyncio.shield(done)
            finally:
                if not done.done():  # the caller was cancelled: hold the lock until `work` ends
                    await asyncio.wait((done,))

    async def _loop(self) -> None:
        clock = self._clock
        trigger: str | None = "app_open"
        last_wall, last_mono = clock.wall(), clock.monotonic()
        next_beat = last_mono + HEARTBEAT_S
        next_check = last_mono
        while not self._closing:
            wall, mono = clock.wall(), clock.monotonic()
            gap = max((wall - last_wall).total_seconds(), mono - last_mono)
            last_wall, last_mono = wall, mono
            if gap > 2 * HEARTBEAT_S:
                next_beat = mono + HEARTBEAT_S  # asleep: one cycle, and no beat is made up
                trigger = "heartbeat"
            elif mono >= next_beat:
                try:
                    await self._beat()
                except Exception as exc:  # one failed beat leaves the clock running, unretried
                    log.warning("decision beat failed: %s", _code(exc))
                next_beat = mono + HEARTBEAT_S
            if trigger is None and self._wake_due is not None and mono >= self._wake_due:
                trigger = self._wake_reason
            if trigger is None and mono >= next_check:
                trigger = "change"
            if trigger is not None:
                await self._locked(_Run(trigger))
                trigger = None
                next_check = clock.monotonic() + self._cadence_s
            if self._closing:
                break
            until = min(
                next_beat, next_check, math.inf if self._wake_due is None else self._wake_due
            )
            await clock.wait(self._event, until - clock.monotonic())
            self._event.clear()

    async def _beat(self) -> None:
        """Close the λ window under the cycle lock, halting no run: pace and store each scope's
        λ by the attention its cycles newly asked in the window, then, while learning is on,
        re-fit and store its snapshot, and while Self-improvement is on, keep its
        self-improvement candidates."""
        window, self._nudges = self._nudges, {}
        if not window:
            return
        settings = await self._settings()
        if settings is None or not settings.enabled:
            return
        now = self._clock.wall()
        await self._exclusive(lambda: self._close_window(window, settings, now))

    def _close_window(
        self, window: Mapping[str, float], settings: DecisionSettings, now: datetime
    ) -> None:
        """The beat's writes, in its worker thread: each scope's λ, then, while learning or
        Self-improvement is on, one read of each scope's decisions since `learn_since` for its
        snapshot (learning) and its self-improvement candidates (Self-improvement). Off, the
        beat keeps no candidates. A store failure is logged by its code and leaves that λ, that
        snapshot or those candidates as they were."""
        for scope, attention in window.items():
            try:
                self._pace(scope, attention, settings.nudges_per_hour, now)
            except DecisionLoopError as exc:
                log.warning("decision pace not saved: %s", exc.code)
        if not settings.watch_improve:
            self._improve.clear()
        if not (settings.learning or settings.watch_improve):
            return
        for scope in window:
            try:
                since = self._store.learn_since(scope)
                rows = self._stats.learning_rows(scope, since=since)
            except DecisionLoopError as exc:
                log.warning("decision rows not read: %s", exc.code)
                continue
            if settings.learning:
                try:
                    self._relearn(scope, rows, now)
                except DecisionLoopError as exc:
                    log.warning("decision relearn not saved: %s", exc.code)
            if settings.watch_improve:
                try:
                    wording, _version, trial = self._proposals.wording(scope)
                except DecisionLoopError as exc:
                    log.warning("decision wording not read: %s", exc.code)
                    continue
                self._improve[scope] = improve.from_rows(
                    rows,
                    scope=scope,
                    wording=wording,
                    trial_open=trial is not None,
                    now=now,
                    learn_since=since,
                )

    def _pace(self, scope: str, attention: float, per_hour: int, now: datetime) -> None:
        lam = arbiter.lambda_of(self._store.get_json(scope, "lambda"))
        self._store.set_json(scope, "lambda", arbiter.pace(lam, attention, per_hour), now)

    def _relearn(self, scope: str, rows: Sequence[LearningRow], now: datetime) -> None:
        """Re-fit the scope's snapshot from `rows`, its decisions since `learn_since`, and store
        it."""
        learned = learning.relearn(rows, now=now)
        self._store.set_json(scope, "learned", learning.encode(learned), now)

    # ---- one cycle -----------------------------------------------------------------------------

    async def _locked(self, run: _Run) -> None:
        self._runs.add(run)
        try:
            async with self._lock:
                run.entered = True
                self._wake_due = None  # this cycle covers every wake asked for so far
                await self._cycle(run)
        finally:
            self._runs.discard(run)

    async def _cycle(self, run: _Run) -> None:
        if run.halt.is_set():
            return
        wall = self._clock.wall()
        now = wall.astimezone(self._zone)
        # Before the first await: a Stop all pressed from here on holds this cycle's check starts,
        # and its mission and seat starts.
        stops = self._checks.stops() if self._checks is not None else 0
        stop_alls = self._stop_alls
        try:
            settings = await self._settings()
            if settings is not None and self._on(settings, wall):
                await self._decide(run, settings, wall, now, stops, stop_alls)
        except _Halted:
            return
        except Exception as exc:  # one failed cycle leaves the clock running
            # The step and the codes only: an exception's text can hold SQL, a path or vault text.
            log.warning("decision cycle failed at %s: %s", run.step, _code(exc))
        if not run.halt.is_set():
            await self._poll(wall)

    async def _settings(self) -> DecisionSettings | None:
        """The Decision settings, or None when they cannot be read."""
        try:
            settings = await asyncio.to_thread(self._read_settings)
        except Exception:
            return None
        self._cadence_s = CADENCE_S.get(settings.cadence, float(OBSERVE_S))
        return settings

    def _on(self, settings: DecisionSettings, wall: datetime) -> bool:
        paused = settings.paused_until is not None and settings.paused_until > wall
        return settings.enabled and not paused and self._attended()

    async def _decide(
        self,
        run: _Run,
        settings: DecisionSettings,
        wall: datetime,
        now: datetime,
        stops: int,
        stop_alls: int,
    ) -> None:
        # Each step is named before it runs, so a failed cycle's log line says which one failed.
        run.step = "gather"
        presence = self._presence.current()
        today = now.date()
        kanban = settings.watch_kanban or (self._seats is not None and settings.grant_team != "off")
        obs = await _until_halted(
            run, asyncio.to_thread(self._gather, presence.page, today, kanban)
        )
        scope = obs.scope
        if not _is_id(scope):
            return
        day = today.isoformat()
        run.step = "vault_changes"
        obs, held = await _until_halted(run, asyncio.to_thread(self._watch, obs, settings, day))
        if settings.watch_vault_health and obs.read_look is not None:
            run.step = "vault_look"
            look = await _until_halted(run, asyncio.to_thread(obs.read_look))
            obs = replace(obs, look=look, read_look=None, failures=obs.failures + look.failures)
        if self._code is not None and _reads_code(obs, settings):
            run.step = "code_read"
            code = await _until_halted(run, asyncio.to_thread(self._code.read, wall))
            obs = replace(obs, code=code, failures=obs.failures + code.failures)
        run.step = "check_read"
        if self._checks is not None and _reads_checks(obs, settings) and self._checks.supported():
            checks = await _until_halted(run, asyncio.to_thread(self._checks.read, wall))
            obs = replace(obs, checks=checks, failures=obs.failures + checks.failures)
        if self._inbox is not None and _reads_inbox(obs, settings):  # memory only: no thread
            run.step = "inbox_read"
            obs = replace(obs, inbox=self._inbox.read(scope))
        if settings.watch_kanban and obs.kanban is not None:
            run.step = "open_cards"
            obs = replace(obs, open_cards=await asyncio.to_thread(self._open_cards, scope))
        run.step = "detect"
        reports = [mission for mission in self._reports.values() if mission.scope == scope]
        detected = list(self._detect(obs, settings, reports, now))
        run.step = "second_looks"
        second, leave_out = await self._second_looks(
            run, obs, settings, wall, chat=presence.chat_id
        )
        detected.extend(second)
        looked = [found.item_key for found in detected if found.type in _ONCE_A_KEY]
        if looked:  # VIKI looks once per key: a key a mission holds closes its item
            run.step = "taken"
            taken = await asyncio.to_thread(self._missions.taken, looked)
            detected = [
                found
                for found in detected
                if found.type not in _ONCE_A_KEY or found.item_key not in taken
            ]
            for kept, reply in list(self._offers.items()):  # a taken offer's reply is forgotten
                offer = second_looks.offer(reply, scope) if kept[0] == scope else None
                if offer is not None and offer.item_key in taken:
                    del self._offers[kept]
        run.step = "plan_yields"
        detected = await self._unpaused_plans(run, detected, scope, wall, settings)
        run.step = "draft_admission"
        detected = await self._admitted_drafts(run, detected, scope, wall, day, settings)
        run.step = "check_admission"
        detected = await self._admitted_checks(run, detected, scope, wall, day, settings)
        run.step = "triage_admission"
        detected = await self._admitted_triage(run, detected, scope, wall, day, settings)
        run.step = "seats"
        planned = await self._plan_seats(run, obs, settings, wall, now, day)
        if planned is not None:
            detected.extend(seat_candidates(planned[0], scope, day))
        run.step = "inputs"
        (
            owner,
            lam,
            (stamp, key),
            running,
            limited,
            exposures,
            (weights, reach, costs),
            (wording, version, trial),
        ) = await asyncio.to_thread(self._inputs, obs, scope, wall - _DAY, settings, day)
        # What holds VIKI's own starts this cycle, so a held item offers its start to the user
        # (`cycle.held_start`); the starts still read their own gates just before them.
        holds = Holds(
            brake=plan_hold(self._plan_reading(), settings.plan_brake, wall) is not None,
            quiet=quiet_until(settings.quiet, now) is not None,
            busy=bool(running),
            limited=limited,
        )
        if self._looks is not None:
            run.step = "app_look"
            app = await _until_halted(
                run,
                self._looks.read(
                    waiting=settings.watch_approvals,
                    heavy=settings.watch_chats,
                    sends=settings.watch_chats,
                    bridges=settings.watch_messaging,
                ),
            )
            # A mission's chat and a seat run waiting on a card have their own items already.
            answered = {mission.session_id for mission in running}
            if self._seats is not None:
                answered.update(waiting.session_id for waiting in self._seats.waiting())
            detected.extend(looks.app_candidates(app, scope, now, exclude=answered))
        run.step = "improve"
        if obs.kind == "vault" and settings.watch_improve:
            detected.extend(
                found
                for found in self._improve.get(scope, ())
                if found.type != "question_tune"
                or (trial is None and _tune_current(found, wording))
            )
        candidates = _first(
            [found for found in detected if _fileable(found, held, settings.limit_changes)]
        )
        cap = round(settings.daily_cap_usd * _MICRO)
        sticky: set[str] = set()  # the items whose request failed for good in this cycle

        async def ask(
            item_key: str, request: ModelRequest, *, reversal: bool
        ) -> ModelReply | ModelError | None:
            answer = await self._ask(run, request, key, stamp, day, cap)
            if isinstance(answer, ModelError) and answer.code in STICKY_FAILURES and not reversal:
                sticky.add(item_key)
            return answer

        async def shadow_ask(
            item_key: str, request: ModelRequest, *, reversal: bool
        ) -> ModelReply | ModelError | None:
            # The live requests' limits, and no failure memory: a shadow's or a trial's failure
            # says nothing of the live answer the item keeps.
            return await self._ask(run, request, key, stamp, day, cap)

        def history(item_key: str) -> History:
            if run.halt.is_set():  # a halted cycle stops here, before the item's first write
                raise _Halted
            return self._history_for(scope, item_key)

        ctx = CycleContext(
            scope=scope,
            store=self._store,
            agenda=self._agenda,
            settings=settings,
            presence=presence,
            owner=owner,
            history_for=history,
            now=now,
            lam=lam,
            weights=weights,
            ask=ask,
            model_ok=key is not None and not self._paused(stamp),
            failed=self._failed.get(scope, {}),
            missions=running,
            exposures=exposures,
            reach=reach,
            costs=costs,
            shadow_ask=None if settings.shadow == "off" else shadow_ask,
            wording=wording,
            playbook_version=version,
            trial=trial,
            trial_ask=None if trial is None else shadow_ask,
            keep=kanban_keep(obs) if settings.watch_kanban else frozenset(),
            inbox_since=self._since,
            holds=holds,
        )
        run.step = "cycle"
        try:
            result = await run_cycle(ctx, candidates, trigger=run.trigger)
        except _Halted:
            await asyncio.to_thread(self._keep_failures, scope, sticky)
            raise
        self._checked_at = wall
        self._failed[scope] = dict(result.failed)
        self._nudge += result.loud
        self._glow += result.new_list
        self._nudges[scope] = self._nudges.get(scope, 0.0) + result.attention
        self._settle_reports(result)
        if run.halt.is_set():
            return
        run.step = "prune"
        await asyncio.to_thread(self._agenda.prune, scope, now)
        run.step = "file_changes"
        await asyncio.to_thread(
            self._file_changes, scope, obs, result, settings.limit_changes, now, day
        )
        run.step = "start_missions"
        await self._start_missions(run, scope, settings, result, leave_out, stop_alls)
        if planned is not None:
            run.step = "start_seats"
            await self._start_seats(run, *planned, settings, day, stop_alls)
        run.step = "start_checks"
        await self._start_checks(run, scope, settings, result, day, stops)
        run.step = "start_triage"
        seen = None if obs.inbox is None else obs.inbox.run
        await self._start_triage(run, scope, settings, result, day, seen)

    async def _plan_seats(
        self,
        run: _Run,
        obs: Observation,
        settings: DecisionSettings,
        wall: datetime,
        now: datetime,
        day: str,
    ) -> tuple[seats.SeatPlan, frozenset[str]] | None:
        """The vault's seat plan, with the ids of the routines whose revision stops at a draft
        (the ones quiet hours let start); None with no seat host, outside a vault, or while Start
        Bots' work is Off and Routines is not watched. See the module docstring."""
        host = self._seats
        if host is None or obs.kind != "vault":
            return None
        if settings.grant_team == "off" and not settings.watch_routines:
            return None
        kanban = obs.kanban if obs.kanban is not None else KanbanRead((), (), False)
        facts = await _until_halted(
            run, asyncio.to_thread(host.facts, obs.scope, seats.cards_since(kanban), day)
        )
        plan = seats.plan(
            kanban,
            facts,
            host.waiting(),
            settings,
            policy=obs.policy,
            hold=plan_hold(self._plan_reading(), settings.plan_brake, wall),
            quiet=quiet_until(settings.quiet, now) is not None,
        )
        drafts = frozenset(found.routine_id for found in facts.routines if found.stop_at_draft)
        return plan, drafts

    async def _second_looks(
        self,
        run: _Run,
        obs: Observation,
        settings: DecisionSettings,
        wall: datetime,
        *,
        chat: str | None,
    ) -> tuple[list[Candidate], tuple[str, ...]]:
        """The scope's second-look items, its findings and one offer for each reply it keeps,
        with the finding kinds learning pauses (for a start's Leave out). It first keeps the
        reply the page reports, then forgets the one kept for `chat`, the chat open now, when
        the page shows that chat with another of its replies or none; see the module docstring."""
        scope = obs.scope
        found = [
            project_plans.finding(waiting)
            if waiting.look in PROJECT_PLANS
            else second_looks.finding(waiting)
            for waiting in self._missions.findings()
            if waiting.scope == scope
        ]
        hint = None if obs.page is None else obs.page.second_look
        if (
            hint is not None
            and hint.session != settings.launcher_session
            and _offers_second_looks(obs, settings)
            and second_looks.offer(hint, scope) is not None
        ):
            # A kept chat's reply replaces its own; a new chat's is kept once it is the user's.
            kept = (scope, hint.session)
            if kept in self._offers or await _until_halted(
                run, asyncio.to_thread(self._missions.owner_chat, hint.session)
            ):
                self._offers.pop(kept, None)
                self._offers[kept] = hint
                while len(self._offers) > MAX_KEPT_OFFERS:
                    del self._offers[next(iter(self._offers))]
        # The page shows the open chat: its kept reply goes once another reply, or none, shows;
        # a reply of another chat is another window's, so it stays.
        if obs.page is not None and chat is not None:
            shown = self._offers.get((scope, chat))
            if shown is not None and (
                hint is None or (hint.session == chat and hint.turn != shown.turn)
            ):
                del self._offers[(scope, chat)]
        offers = [
            offer
            for (where, _chat), reply in self._offers.items()
            if where == scope and (offer := second_looks.offer(reply, scope)) is not None
        ]
        if not offers or not _offers_second_looks(obs, settings):
            return found, ()
        yields = await _until_halted(
            run,
            asyncio.to_thread(self._yields, scope, wall, settings, SECOND_LOOKS, "second_looks"),
        )
        paused = second_looks.paused_kinds(yields)
        if len(paused) == len(FINDING_KINDS):
            return found, paused
        looks = second_looks.paused_looks(yields)
        return [*found, *(offer for offer in offers if offer.type not in looks)], paused

    async def _unpaused_plans(
        self,
        run: _Run,
        detected: list[Candidate],
        scope: str,
        wall: datetime,
        settings: DecisionSettings,
    ) -> list[Candidate]:
        """`detected` less the project-plan offers of a type learning pauses; the yields are read
        only while such an offer remains. See the module docstring."""
        if not any(_plan_offer(found) for found in detected):
            return detected
        yields = await _until_halted(
            run,
            asyncio.to_thread(self._yields, scope, wall, settings, PROJECT_PLANS, "project_plans"),
        )
        paused = second_looks.paused_looks(yields)
        return [found for found in detected if not (_plan_offer(found) and found.type in paused)]

    async def _admitted_drafts(
        self,
        run: _Run,
        detected: list[Candidate],
        scope: str,
        wall: datetime,
        day: str,
        settings: DecisionSettings,
    ) -> list[Candidate]:
        """`detected` less the message-draft offers the admission drops; its reads run only while
        such an offer remains. See the module docstring."""
        offers = [found for found in detected if found.type in MESSAGE_DRAFTS]
        if not offers:
            return detected
        admitted = await _until_halted(
            run, asyncio.to_thread(self._draft_admission, offers, scope, wall, day, settings)
        )
        return [
            found
            for found in detected
            if found.type not in MESSAGE_DRAFTS or found.item_key in admitted
        ]

    def _draft_admission(
        self,
        offers: list[Candidate],
        scope: str,
        wall: datetime,
        day: str,
        settings: DecisionSettings,
    ) -> set[str]:
        """The keys of the `offers` admitted: the user's own chats, the types the user did not
        stop and learning does not pause, and the keys the scope stores or today's limit has room
        for. Each read runs only while an offer is left."""
        chats = dict.fromkeys(found.related_chat for found in offers)
        mine = {chat for chat in chats if chat is not None and self._missions.owner_chat(chat)}
        offers = [found for found in offers if found.related_chat in mine]
        if not offers:
            return set()
        muted = self._store.preferences(scope, wall).muted
        offers = [found for found in offers if found.type not in muted]
        if not offers:
            return set()
        yields = self._yields(scope, wall, settings, MESSAGE_DRAFTS, "message_drafts")
        paused = second_looks.paused_looks(yields)
        offers = [found for found in offers if found.type not in paused]
        if not offers:
            return set()
        today, stored = self._stats.drafts_today(scope, day, [found.item_key for found in offers])
        room = settings.limit_message_draft - today
        admitted: set[str] = set()
        for found in offers:  # a draft already raised stays; new ones stop at the day's limit
            if found.item_key in stored:
                admitted.add(found.item_key)
            elif room > 0:
                admitted.add(found.item_key)
                room -= 1
        return admitted

    async def _admitted_checks(
        self,
        run: _Run,
        detected: list[Candidate],
        scope: str,
        wall: datetime,
        day: str,
        settings: DecisionSettings,
    ) -> list[Candidate]:
        """`detected` less the saved-check items the admission drops; its reads run only while
        such an item remains. See the module docstring."""
        found = [candidate for candidate in detected if candidate.type in SAVED_CHECKS]
        if not found:
            return detected
        admitted = await _until_halted(
            run,
            asyncio.to_thread(
                self._admission,
                found,
                scope,
                wall,
                day,
                settings,
                SAVED_CHECKS,
                "saved_checks",
                settings.limit_check,
            ),
        )
        return [
            candidate
            for candidate in detected
            if candidate.type not in SAVED_CHECKS or candidate.item_key in admitted
        ]

    async def _admitted_triage(
        self,
        run: _Run,
        detected: list[Candidate],
        scope: str,
        wall: datetime,
        day: str,
        settings: DecisionSettings,
    ) -> list[Candidate]:
        """`detected` less the inbox-suggestion items the admission drops; its reads run only
        while such an item remains. See the module docstring."""
        found = [candidate for candidate in detected if candidate.type in INBOX_TRIAGE]
        if not found:
            return detected
        admitted = await _until_halted(
            run, asyncio.to_thread(self._triage_admission, found, scope, wall, day, settings)
        )
        return [
            candidate
            for candidate in detected
            if candidate.type not in INBOX_TRIAGE or candidate.item_key in admitted
        ]

    def _triage_admission(
        self,
        found: list[Candidate],
        scope: str,
        wall: datetime,
        day: str,
        settings: DecisionSettings,
    ) -> set[str]:
        """The keys of the inbox-suggestion items `found` admitted. A run that found nothing ends
        its item: it is dropped, and its stored item, if open, set done (so it does not go
        stale); the rest pass `_admission`."""
        for candidate in found:
            if candidate.facts.get("phase") != "nothing":
                continue
            item = self._agenda.item(scope, candidate.item_key)
            if item is not None and item.state == "open":
                self._agenda.set_state(scope, candidate.item_key, "done", wall)
        found = [candidate for candidate in found if candidate.facts.get("phase") != "nothing"]
        if not found:
            return set()
        return self._admission(
            found,
            scope,
            wall,
            day,
            settings,
            INBOX_TRIAGE,
            "inbox_triage",
            settings.limit_inbox_triage,
        )

    def _admission(
        self,
        found: list[Candidate],
        scope: str,
        wall: datetime,
        day: str,
        settings: DecisionSettings,
        family: tuple[str, ...],
        name: str,
        limit: int,
    ) -> set[str]:
        """The keys of the items `found` of `family` (the saved checks or the inbox suggestions)
        admitted: a type the user did not stop and learning does not pause (under the restarts
        the `name` preference holds), then a key the scope stores, in any phase, or a waiting
        item's new key while today's items of `family` plus those admitted stay under `limit`.
        Each read runs only while an item is left."""
        muted = self._store.preferences(scope, wall).muted
        found = [candidate for candidate in found if candidate.type not in muted]
        if not found:
            return set()
        yields = self._yields(scope, wall, settings, family, name)
        paused = second_looks.paused_looks(yields)
        found = [candidate for candidate in found if candidate.type not in paused]
        if not found:
            return set()
        today, stored = self._stats.drafts_today(
            scope, day, [candidate.item_key for candidate in found], types=family
        )
        room = limit - today
        admitted: set[str] = set()
        for candidate in found:  # a stored key stays; only a waiting item raises a new one
            if candidate.item_key in stored:
                admitted.add(candidate.item_key)
            elif room > 0 and candidate.facts.get("phase") == "waiting":
                admitted.add(candidate.item_key)
                room -= 1
        return admitted

    def _yields(
        self,
        scope: str,
        wall: datetime,
        settings: DecisionSettings,
        family: tuple[str, ...],
        name: str,
    ) -> SecondLookYields:
        """What the scope's second looks (`family` `SECOND_LOOKS`), project plans
        (`PROJECT_PLANS`), message drafts (`MESSAGE_DRAFTS`), saved checks (`SAVED_CHECKS`) or
        inbox suggestions (`INBOX_TRIAGE`; none but the second looks has kinds) yielded since the
        later of `SECOND_LOOK_WINDOW` before `wall` and the scope's `learn_since`, under the
        restarts the `name` preference holds. A message draft, a saved check and an inbox
        suggestion have no mission, so their rows come from `DecisionLoopStats.draft_rows`, where
        a saved check or an inbox suggestion that closed unanswered counts as neither used nor
        turned down, and an inbox suggestion whose run found nothing (its item done) reads
        "nothing". No Not useful reasons are read: they pause nothing."""
        since = wall - SECOND_LOOK_WINDOW
        reset = self._store.learn_since(scope)
        if reset is not None and reset > since:
            since = reset
        kinds = FINDING_KINDS if family == SECOND_LOOKS else ()
        if family == MESSAGE_DRAFTS:
            rows = self._stats.draft_rows(scope, since)
        elif family == SAVED_CHECKS:
            rows = self._stats.draft_rows(scope, since, types=SAVED_CHECKS, closed="")
        elif family == INBOX_TRIAGE:
            rows = self._stats.draft_rows(
                scope, since, types=INBOX_TRIAGE, closed="", done="nothing"
            )
        else:
            rows = self._missions.second_look_rows(scope, since, types=family)
        return second_looks.yields(
            rows,
            second_looks.restarts(self._store.get_json(scope, name), looks=family, kinds=kinds),
            {},
            learning=settings.learning,
            looks=family,
            kinds=kinds,
        )

    def _watch(
        self, obs: Observation, settings: DecisionSettings, day: str
    ) -> tuple[Observation, tuple[Held, int] | None]:
        """The cycle's vault changes, read once the change rows tell what no proposal could be
        filed for. While vault changes are detected (`detectors.detects_changes`, which holds
        only in an ordinary vault, `Observation.policy`), what the scope's change rows hold back
        on the local day `day` is read first (`_held_changes`), and the vault-change read the
        gather left (`Observation.read_changes`) then runs with it (`_holding`): skipping what
        they hold while a change can be filed, and reading only the
        notes changed lately, for the user part, once none can -- the day's change rows in
        every workspace at the limit, no room in the scope for another row, or the rows not
        read. While vault changes are not detected, no row is read and the read stops after the
        notes changed lately, as at the cap. An observation with no vault changes and none to
        read reads nothing. Answers the observation, its read run, and what the rows hold back
        with the day's count (None when not read)."""
        read = obs.read_changes
        if read is None and obs.changes is None:
            return obs, None
        held, holding = None, _UNREAD
        if detects_changes(settings, obs.policy):
            held = self._held_changes(obs.scope, day)
            holding = _holding(held, settings.limit_changes)
        if read is None:
            return obs, held
        return replace(obs, changes=read(holding), read_changes=None), held

    def _open_cards(self, scope: str) -> dict[str, str]:
        """The scope's open Kanban card items (`contracts.CARDS`): each one's key to its type."""
        items, _ = self._agenda.open_items(scope)
        return {item.item_key: item.candidate for item in items if item.candidate in CARDS}

    def _inputs(
        self,
        obs: Observation,
        scope: str,
        since: datetime,
        settings: DecisionSettings,
        day: str,
    ) -> tuple[
        Owner,
        float,
        tuple[str | None, str | None],
        list[MissionRecord],
        frozenset[str],
        dict[str, int],
        tuple[dict[str, float], dict[str, float], dict[str, float]],
        tuple[Mapping[str, object], str, Trial | None],
    ]:
        """What a cycle reads besides its candidates and the change rows: the user (`_owner`),
        λ, the key, the running missions, the start families at the day's limit (`_limited`),
        each item's showings since `since`, the arbiter's weights, T and C (`learning.inputs`):
        the stored snapshot's while learning is on, else, or when it does not read back, the
        start values; and the scope's wording (`_wording`)."""
        lam = arbiter.lambda_of(self._store.get_json(scope, "lambda"))
        exposures = self._store.exposures_since(scope, since)
        learned = None
        if settings.learning:
            learned = learning.parse(self._store.get_json(scope, "learned"))
        return (
            self._owner(obs, scope, since),
            lam,
            self._key(),
            self._missions.running(),
            self._limited(settings, day),
            exposures,
            learning.inputs(learned),
            self._wording(scope),
        )

    def _limited(self, settings: DecisionSettings, day: str) -> frozenset[str]:
        """The `START_FAMILIES` whose missions started on the local day `day`, the user's own
        counted, in any state and any scope, reach that family's limit in `settings`, as
        `MissionService.start` counts them for its `limit` refusal."""
        missions = self._missions
        counts = (
            (missions.count_looks(day), settings.limit_look),
            (missions.count_second_looks(day), settings.limit_second_look),
            (missions.count_project_plans(day), settings.limit_project_plan),
            (missions.count_today("prepare", day), settings.limit_drafts),
            (missions.count_today("run", day), settings.limit_chats),
        )
        return frozenset(
            family
            for family, (today, limit) in zip(START_FAMILIES, counts, strict=True)
            if today >= limit
        )

    def _owner(self, obs: Observation, scope: str, since: datetime) -> Owner:
        """The user part a cycle's state carries: `owner_for(obs)` as the base, goals kept,
        with the rest rebuilt (`owner.fill`) from the notes changed lately
        (`obs.changes.changed`, each titled by its title, else its file name, with its tags)
        and the scope's `entry='decision'` rows since `since`
        (`DecisionLoopStats.owner_rows`) -- each row's item key resolved to a title through the
        agenda's current item: its body title and its first evidence label's ref, tags None
        (the agenda keeps no tags, so `state.py`'s own rule holds these back wherever a
        never-send tag rule exists). A DecisionLoopError, from the rows or from resolving one
        of their keys, leaves `owner_for(obs)` as it is."""
        base = self._owner_for(obs)
        notes = () if obs.changes is None else obs.changes.changed
        changed = [
            Mention(note.title or PurePosixPath(note.ref).stem, note.ref, note.tags)
            for note in notes
        ]

        def title_of(item_key: str) -> Mention | None:
            found = self._agenda.item(scope, item_key)
            if found is None:
                return None
            ref = found.body.evidence[0].ref if found.body.evidence else None
            return Mention(found.body.title, ref, None)

        try:
            rows = self._stats.owner_rows(scope, since)
            return owner.fill(base, rows, title_of, changed)
        except DecisionLoopError as exc:
            log.warning("decision owner not filled: %s", exc.code)
            return base

    def _held_changes(self, scope: str, day: str) -> tuple[Held, int] | None:
        """What the scope's change rows hold back from filing on the local day `day`
        (`ChangeProposals.held`, room included) and how many change rows that day holds in every
        workspace (`today`); None, which a cycle reads as at the cap, when they cannot be read,
        logged by the code."""
        try:
            return self._changes.held(scope, day), self._changes.today(day)
        except DecisionLoopError as exc:
            log.warning("decision changes not read: %s", exc.code)
            return None

    def _file_changes(
        self,
        scope: str,
        obs: Observation,
        result: CycleResult,
        limit: int,
        now: datetime,
        day: str,
    ) -> None:
        """File each vault change the cycle committed open and shown, neither silent nor held to
        a later time (timed "at"), in candidate order: an open change proposal of the scope on
        `day`, at most `limit` a day in every workspace (`ChangeProposals.file`), then its item
        set done. The refs are the candidate's: `related_ref`, then the ref of its second
        evidence for a paired kind, or the archive `obs.changes` names for its file of ticked
        tasks. A kind whose open row holds its target (a note marked replaced, a file of ticked
        tasks) files that target once a cycle. A refusal leaves the item as it is; an item that
        cannot be set done stays open with its key held, so the next cycle drops it."""
        archive = {} if obs.changes is None else obs.changes.archive
        targets: set[tuple[str, str]] = set()
        for done in result.committed:
            item, kind, ref = done.item, done.candidate.type, done.candidate.related_ref
            if kind not in CHANGES or ref is None or (kind, ref) in targets:
                continue
            if item.state != "open" or item.level == "silent" or item.timing == "at":
                continue
            evidence, paired = done.candidate.evidence, kind in PAIRED
            try:
                self._changes.file(
                    scope,
                    kind=kind,
                    ref=ref,
                    other=evidence[1].ref if paired and len(evidence) > 1 else None,
                    archive=None if paired else archive.get(ref),
                    item_key=item.item_key,
                    episode_id=item.episode_id,
                    day=day,
                    limit=limit,
                    now=now,
                )
            except DecisionLoopError:
                continue
            if kind in _TARGETED:
                targets.add((kind, ref))
            with contextlib.suppress(DecisionLoopError):
                self._agenda.set_state(scope, item.item_key, "done", now)

    def _wording(self, scope: str) -> tuple[Mapping[str, object], str, Trial | None]:
        """A cycle's wording: the scope's wording in use, its version and the proposal on trial
        (`QuestionProposals.wording`); when the proposals cannot be read, the playbook's own
        wording and version and no trial, logged by the code. The beat does not fall back: it
        keeps the scope's candidates as they were."""
        try:
            wording, version, trial = self._proposals.wording(scope)
        except DecisionLoopError as exc:
            log.warning("decision wording not read: %s", exc.code)
            return {}, playbook.PLAYBOOK_VERSION, None
        return wording, version, None if trial is None else Trial(*trial)

    def _key(self) -> tuple[str | None, str | None]:
        """(the key's save stamp, the key): the stamp is read first, so a key saved in between
        is tried rather than paused. (None, None) when the store does not work."""
        try:
            stamp = self._keys.changed_at()
            return stamp, self._keys.load()
        except DecisionLoopError:
            return None, None

    def _keep_failures(self, scope: str, keys: set[str]) -> None:
        """After a halt: remember each item whose request failed for good before it, at the
        state hash it was committed with, as a finished cycle would have handed it back."""
        memory = self._failed.setdefault(scope, {})
        for key in keys:
            try:
                found = self._agenda.item(scope, key)
            except DecisionLoopError:
                continue
            if found is not None and found.state_hash is not None:
                memory[key] = found.state_hash

    def _settle_reports(self, result: CycleResult) -> None:
        """Forget each mission report whose item the user has closed."""
        closed = {
            done.candidate.origin_session
            for done in result.committed
            if done.candidate.type == "mission_report" and done.item.state != "open"
        }
        for mission_id, mission in list(self._reports.items()):
            if mission.session_id in closed:
                del self._reports[mission_id]

    async def _gate(self, settings: DecisionSettings) -> _Gate | None:
        """What holds a start VIKI makes on its own, read just before it, so that a change the
        user saves while the cycle waits on reads, the model or an earlier start holds it
        (`_Gate`); None, which holds every start, when the settings cannot be read or Decision is
        off or paused or no window is open (`attended`). `settings` are the cycle's own."""
        fresh = await self._settings()
        wall = self._clock.wall()
        if fresh is None or not self._on(fresh, wall):
            return None
        return _Gate(
            settings=fresh,
            quiet=quiet_until(fresh.quiet, wall.astimezone(self._zone)) is not None,
            brake=plan_hold(self._plan_reading(), fresh.plan_brake, wall) is not None,
            never_send=fresh.never_send != settings.never_send or fresh.never_send_invalid,
        )

    def _stopped(self, stop_alls: int) -> bool:
        """Whether a Stop all holds the missions and seat runs VIKI starts on its own: one pressed
        since the cycle read `stop_alls`, before its first await, or within a cadence of now."""
        # The cycle that Stop all's own wake brings `COALESCE_S` later, or a stopped mission's,
        # would otherwise refill the slots it freed with the offers still waiting. The clock's
        # next cycle on its cadence starts them as usual.
        return (
            self._stop_alls != stop_alls
            or self._clock.monotonic() - self._stopped_at < self._cadence_s
        )

    async def _start_missions(
        self,
        run: _Run,
        scope: str,
        settings: DecisionSettings,
        result: CycleResult,
        leave_out: tuple[str, ...],
        stop_alls: int,
    ) -> None:
        """Start a mission for each item that wants work its grant allows, but none for a
        self-improvement item; a second look's brief leaves out the kinds in `leave_out`. The
        settings are read again just before each start (`_gate`): none starts once they are off
        or paused, no window is open, the plan brake holds, Never send differs from the cycle's
        `settings`, a Stop all holds (`_stopped`) or the work's grant is no longer Allowed, and
        no look, second look or project plan starts while quiet hours hold. See the module
        docstring. A refused start is remembered like a started one, but a look's, a second
        look's or a project plan's is not, nor is one refused while busy or a held one."""
        started = False
        for done in result.committed:
            item, work, kind = done.item, done.outcome.work, done.candidate.type
            if work_grants(kind, settings).get(work) != "allowed":
                continue
            if kind in IMPROVE:  # it only prepares a draft, whatever the grants
                continue
            if kind in SAVED_CHECKS:  # its checks start through the check host
                continue
            if kind in INBOX_TRIAGE:  # its run starts through the inbox host
                continue
            if item.state != "open" or item.timing != "now" or item.mission_id is not None:
                continue
            look = kind in _ONCE_A_KEY  # a look's key is held by `taken`: a refused one asks again
            attempt, tried = (scope, item.item_key), (item.state_hash, work)
            if not look and self._attempts.get(attempt) == tried:
                continue
            # A held start stores nothing and is not remembered, so it starts at the first cycle
            # after the hold ends.
            gate = await self._gate(settings)
            if run.halt.is_set():
                break
            if gate is None or gate.brake or gate.never_send or self._stopped(stop_alls):
                break
            if (look and gate.quiet) or work_grants(kind, gate.settings).get(work) != "allowed":
                continue
            if not look:
                self._attempts.pop(attempt, None)
                self._attempts[attempt] = tried
                while len(self._attempts) > MAX_ATTEMPTS:
                    del self._attempts[next(iter(self._attempts))]
            try:
                await self._missions.start(
                    scope,
                    item,
                    work,
                    by_owner=False,
                    deadline=done.candidate.deadline,
                    leave_out=leave_out if kind in SECOND_LOOKS else (),
                )
            except MissionError as refusal:
                # Busy holds rather than answers: VIKI's own start waits while any mission is
                # open, so one place stays the user's. Forgotten, it is asked again next cycle.
                if refusal.code == "busy":
                    self._attempts.pop(attempt, None)
                continue
            except DecisionLoopError:
                continue
            started = True
        if started:
            self.wake("mission")

    async def _start_seats(
        self,
        run: _Run,
        plan: seats.SeatPlan,
        drafts: frozenset[str],
        settings: DecisionSettings,
        day: str,
        stop_alls: int,
    ) -> None:
        """Start each of `plan`'s seat starts through the seat host while Start Bots' work is
        Allowed. The settings are read again just before each start (`_gate`): none starts once
        they are off or paused, no window is open, the plan brake holds, a Stop all holds
        (`_stopped`) or the grant is no longer Allowed, and a routine that does not stop at a
        draft (not in `drafts`) does not start while quiet hours hold. See the module
        docstring."""
        if self._seats is None or settings.grant_team != "allowed":
            return
        started = False
        for start in plan.starts:
            gate = await self._gate(settings)
            if run.halt.is_set():
                break
            if (
                gate is None
                or gate.brake
                or self._stopped(stop_alls)
                or gate.settings.grant_team != "allowed"
            ):
                break
            if gate.quiet and start.routine_id not in drafts:
                continue
            try:
                await self._seats.start(
                    start.routine_id,
                    start.revision,
                    newest=start.newest,
                    cards=start.cards,
                    limit=gate.settings.limit_team,
                    day=day,
                )
            except DecisionLoopError:  # it stores nothing: the next cycle plans on fresh facts
                continue
            started = True
        if started:
            self.wake("mission")

    async def _start_checks(
        self,
        run: _Run,
        scope: str,
        settings: DecisionSettings,
        result: CycleResult,
        day: str,
        stops: int,
    ) -> None:
        """Start the pending checks of each committed waiting folder through the check host
        while Run my saved checks is Allowed; see the module docstring."""
        host = self._checks
        if host is None or settings.grant_check != "allowed":
            return
        waiting = [
            done
            for done in result.committed
            if done.candidate.type in SAVED_CHECKS
            and done.outcome.work == "run"
            and done.item.state == "open"
            and done.item.timing == "now"
        ]
        if not waiting:
            return
        # The count first, so no await lies between a folder's settings read and its start. A
        # held start is not remembered: the item still waits, so its checks start at the first
        # cycle after the hold.
        today, _ = await _until_halted(
            run,
            asyncio.to_thread(self._stats.drafts_today, scope, day, (), types=SAVED_CHECKS),
        )
        started = False
        for done in waiting:
            facts = done.candidate.facts
            folder, head, pending = facts.get("folder_id"), facts.get("head"), facts.get("pending")
            if not isinstance(folder, str) or not isinstance(head, str):
                continue
            if not isinstance(pending, tuple):
                continue
            # The plan brake holds no check, as a check spends no plan; a Stop all holds it
            # through `stops`, which the host compares.
            gate = await self._gate(settings)
            if run.halt.is_set():
                break
            if (
                gate is None
                or gate.quiet
                or gate.settings.grant_check != "allowed"
                or today > gate.settings.limit_check  # a lowered limit holds more that day
            ):
                break
            try:  # a halt leaves a run already spawning supervised: the host shields it
                count = await _until_halted(
                    run, host.start(folder, head, [str(check) for check in pending], stops=stops)
                )
            except DecisionLoopError:  # the commit moved or checks do not run here: skip it
                continue
            started = started or count > 0
        if started:
            self.wake("mission")

    async def _start_triage(
        self,
        run: _Run,
        scope: str,
        settings: DecisionSettings,
        result: CycleResult,
        day: str,
        seen: InboxRun | None,
    ) -> None:
        """Start the suggestion run of each committed waiting inbox (one at most) through the
        inbox host while Suggest destinations for my inbox is Allowed, unless the host's run is no
        longer `seen`, the one the cycle read; see the module docstring."""
        host = self._inbox
        if host is None or settings.grant_inbox_triage != "allowed":
            return
        waiting = [
            done
            for done in result.committed
            if done.candidate.type in INBOX_TRIAGE
            and done.outcome.work == "prepare"
            and done.item.state == "open"
            and done.item.timing == "now"
            and (self._since is None or parse_stamp(done.item.created_at) >= self._since)
        ]
        if not waiting:
            return
        # The count first, so no await lies between the settings read and the start. A held start
        # is not remembered: the item still waits, so its run starts at the first cycle after the
        # hold, which checks Never send against the listing again.
        today, _ = await _until_halted(
            run,
            asyncio.to_thread(self._stats.drafts_today, scope, day, (), types=INBOX_TRIAGE),
        )
        started = False
        for done in waiting:
            gate = await self._gate(settings)
            if run.halt.is_set():
                break
            if (
                gate is None
                or gate.quiet
                or gate.brake  # the run spends plan
                or gate.never_send
                or gate.settings.grant_inbox_triage != "allowed"
                or not gate.settings.watch_inbox
                or today > gate.settings.limit_inbox_triage  # a lowered limit holds more that day
            ):
                break
            # The user's own run may have started (and ended) since the cycle read the host: that
            # run saw every capture, so a second would send their text again.
            if host.read(scope).run != seen:  # memory only: no await before `start` reserves
                continue
            try:  # a halt leaves a start already begun running: the host shields it
                await _until_halted(run, host.start(scope, done.item.item_key))
            except DecisionLoopError:  # busy, or refused and remembered by the host: skip it
                continue
            started = True
        if started:
            self.wake("mission")

    async def _poll(self, wall: datetime) -> None:
        """Poll the missions; keep each one that ended, or whose plan waits, as a report."""
        try:
            ended = await self._missions.poll(wall)
        except Exception as exc:
            log.warning("decision missions not polled: %s", _code(exc))
            return
        for mission in ended:
            self._reports.pop(mission.mission_id, None)
            self._reports[mission.mission_id] = mission
        while len(self._reports) > MAX_OPEN_ITEMS:
            del self._reports[next(iter(self._reports))]
        if ended:
            self.wake("mission")

    # ---- the model -----------------------------------------------------------------------------

    async def _ask(
        self,
        run: _Run,
        request: ModelRequest,
        key: str | None,
        stamp: str | None,
        day: str,
        cap: int,
    ) -> ModelReply | ModelError | None:
        """The cycle's `ask`: the reply, the model's error, or None when a limit holds the
        request back."""
        if run.halt.is_set():
            raise _Halted
        if key is None or self._paused(stamp) or self._cooling():
            return None
        estimate = cost_micro_usd(_estimate(request))
        try:
            spent = await asyncio.to_thread(self._store.spent_on, day)
        except DecisionLoopError:
            return None
        if spent + estimate >= cap:
            self._capped = (day, estimate)
            return None
        self._capped = None
        return await self._send(run, request, key, stamp)

    async def test_key(self) -> ModelReply | ModelError:
        """Ask one yes/no question about `TEST_STATE` with the saved key. A reply also lifts a
        401 pause on this key; a refused request pauses or cools as a cycle's would."""
        if self._closing:
            return ModelError("unreachable")
        run = _Run("context")
        self._runs.add(run)
        try:
            settings = await self._settings()
            version = "pinned" if settings is None else settings.model_version
            stamp, key = await asyncio.to_thread(self._key)
            if key is None:
                return ModelError("no_key")
            if self._cooling():
                return ModelError(self._cool_code, self._cool_until - self._clock.monotonic())
            request = ModelRequest(
                model=MODEL_IDS[version],
                state=TEST_STATE,
                questions={TEST_QUESTION: playbook.questions()[TEST_QUESTION]},
            )
            result = await self._send(run, request, key, stamp, test=True)
        except _Halted:
            return ModelError("unreachable")
        finally:
            self._runs.discard(run)
        if result is None:
            return ModelError("rate_limited")
        if isinstance(result, ModelReply):
            wall = self._clock.wall()
            self._last_test = (wall, result.latency_ms)
            if self._paused(stamp):
                self._rejected = None
            await self._spend(result, wall)
        return result

    async def _spend(self, reply: ModelReply, wall: datetime) -> None:
        """Add a key test's measured cost to the user's day, under `TEST_SCOPE`."""
        cost = cost_micro_usd(min(max(reply.input_tokens, 0), _MAX_TOKENS))
        if not cost:
            return
        day = wall.astimezone(self._zone).date().isoformat()
        try:
            await asyncio.to_thread(self._store.add_spend, TEST_SCOPE, day, cost, wall)
        except DecisionLoopError as exc:
            log.warning("decision test spend not saved: %s", exc.code)

    async def _send(
        self,
        run: _Run,
        request: ModelRequest,
        key: str,
        stamp: str | None,
        *,
        test: bool = False,
    ) -> ModelReply | ModelError | None:
        """One request through an in-flight slot and the minute window; None when a limit
        holds it back. A key test passes a 401 pause."""
        if not await self._slot(run):
            return None
        try:
            if self._cooling() or (not test and self._paused(stamp)) or self._window_full():
                return None
            self._window.append(self._clock.monotonic())
            try:
                reply = await self._call(run, request, key)
            except ModelError as error:
                self._failed_with(error, stamp)
                return error
            self._unreachable = False
            return reply
        finally:
            self._in_flight -= 1
            self._free_slots()

    async def _slot(self, run: _Run) -> bool:
        """Take an in-flight slot, waiting at most `SLOT_WAIT_S` for one to free."""
        deadline = self._clock.monotonic() + SLOT_WAIT_S
        while self._in_flight >= MAX_IN_FLIGHT:
            if run.halt.is_set():
                raise _Halted
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                return False
            await self._clock.wait(self._freed, remaining)
        if run.halt.is_set():
            raise _Halted
        self._in_flight += 1
        return True

    def _free_slots(self) -> None:
        """Wake every slot waiter to look again."""
        self._freed.set()
        self._freed = asyncio.Event()

    def _halt(self, run: _Run) -> None:
        run.halt.set()
        self._free_slots()

    async def _call(self, run: _Run, request: ModelRequest, key: str) -> ModelReply:
        """The model's reply to `request`; a halt cancels the request and raises `_Halted`."""
        call = asyncio.ensure_future(self._model.ask(request, key=key))
        halted = asyncio.ensure_future(run.halt.wait())
        try:
            await asyncio.wait((call, halted), return_when=asyncio.FIRST_COMPLETED)
        finally:
            halted.cancel()
            if not call.done():
                call.cancel()
                await asyncio.wait((call,))
        if call.cancelled():
            raise _Halted
        error = call.exception()
        if error is None:
            return call.result()
        if isinstance(error, ModelError):
            raise error
        log.warning("decision model ask failed: %s", type(error).__name__)
        raise ModelError("failed")

    def _failed_with(self, error: ModelError, stamp: str | None) -> None:
        """Note a refused request: a 401 pauses the model until the key is saved again, a 429
        or 529 cools every item, an unreachable model is shown as such."""
        code = error.code if error.code in MODEL_ERROR_CODES else "failed"
        self._last_error = code
        if code == "key_rejected":
            if not self._paused(stamp):
                log.warning("decision model key rejected")
            self._rejected = (stamp,)
        elif code in ("rate_limited", "overloaded"):
            wait = error.retry_after_s
            if wait is None or not math.isfinite(wait) or wait <= 0:
                wait = COOL_S
            if not self._cooling():
                log.warning("decision model cooling: %s", code)
            self._cool_until = max(
                self._cool_until, self._clock.monotonic() + min(wait, MAX_COOL_S)
            )
            self._cool_code = code
        elif code == "unreachable":
            if not self._unreachable:
                log.warning("decision model unreachable")
            self._unreachable = True
        else:
            log.warning("decision model ask failed: %s", code)

    def _paused(self, stamp: str | None) -> bool:
        """Whether a 401 paused the key saved at `stamp`."""
        return self._rejected is not None and self._rejected[0] == stamp

    def _cooling(self) -> bool:
        return self._clock.monotonic() < self._cool_until

    def _window_full(self) -> bool:
        now = self._clock.monotonic()
        while self._window and now - self._window[0] >= WINDOW_S:
            self._window.popleft()
        return len(self._window) >= MAX_PER_MINUTE

    # ---- status --------------------------------------------------------------------------------

    async def status(self) -> EngineStatus:
        """The model's state now (see `EngineStatus`): no_key without a readable key,
        key_rejected while a 401 pauses it, capped while the day's spend, with the estimate the
        cap last held back, reaches the cap, unreachable until a request is answered again,
        rules while cooling, while the never-send list is damaged, or when the settings, the
        store or sealing do not work, else ready."""
        day = self._clock.wall().astimezone(self._zone).date().isoformat()
        settings, key, stamp, spent = await asyncio.to_thread(self._status_reads, day)
        if key in ("missing", "unreadable"):
            state = "no_key"
        elif settings is None or key != "saved" or spent is None:
            state = "rules"
        elif self._paused(stamp):
            state = "key_rejected"
        elif self._over_cap(spent, settings, day):
            state = "capped"
        elif self._unreachable:
            state = "unreachable"
        elif self._cooling() or settings.never_send_invalid:
            state = "rules"
        else:
            state = "ready"
        return EngineStatus(state, self._checked_at, self._last_error, self._last_test)

    def _status_reads(
        self, day: str
    ) -> tuple[DecisionSettings | None, KeyStatus, str | None, int | None]:
        try:
            settings = self._read_settings()
        except Exception:
            settings = None
        key = self._keys.status()
        try:
            return settings, key, self._keys.changed_at(), self._store.spent_on(day)
        except DecisionLoopError:
            return settings, key, None, None

    def _over_cap(self, spent: int, settings: DecisionSettings, day: str) -> bool:
        cap = round(settings.daily_cap_usd * _MICRO)
        if spent >= cap:
            return True
        held = self._capped
        return held is not None and held[0] == day and spent + held[1] >= cap


# ---- helpers -----------------------------------------------------------------------------------


async def _until_halted[T](run: _Run, work: Awaitable[T]) -> T:
    """`work`'s result, or `_Halted` (leaving `work` cancelled) when the run is halted first."""
    task = asyncio.ensure_future(work)
    halted = asyncio.ensure_future(run.halt.wait())
    try:
        await asyncio.wait((task, halted), return_when=asyncio.FIRST_COMPLETED)
    finally:
        halted.cancel()
        if not task.done():
            task.cancel()
    if not task.done() or task.cancelled():
        raise _Halted
    return task.result()


def _offers_second_looks(obs: Observation, settings: DecisionSettings) -> bool:
    """Whether a cycle may offer a second look: a vault cycle whose policy allows prepare
    (`POLICY_WORK`) while Take a second look at my work is not Off."""
    if obs.kind != "vault" or obs.policy is None or settings.grant_second_look == "off":
        return False
    return "prepare" in POLICY_WORK.get(obs.policy, ())


def _plan_offer(candidate: Candidate) -> bool:
    """Whether `candidate` is a project-plan offer, not a project plan's finding."""
    return candidate.type in PROJECT_PLANS and "found" not in candidate.facts


def _reads_code(obs: Observation, settings: DecisionSettings) -> bool:
    """Whether a cycle reads the code folders: a vault cycle whose policy allows prepare
    (`POLICY_WORK`; a read-only vault or an unread policy does not) while Look on my behalf is
    not Off."""
    if obs.kind != "vault" or obs.policy is None or settings.grant_look == "off":
        return False
    return "prepare" in POLICY_WORK.get(obs.policy, ())


def _reads_checks(obs: Observation, settings: DecisionSettings) -> bool:
    """Whether a cycle reads the saved checks: a vault cycle whose policy allows run
    (`POLICY_WORK`: an ordinary vault only) while Run my saved checks is not Off."""
    if obs.kind != "vault" or obs.policy is None or settings.grant_check == "off":
        return False
    return "run" in POLICY_WORK.get(obs.policy, ())


def _reads_inbox(obs: Observation, settings: DecisionSettings) -> bool:
    """Whether a cycle reads the inbox host: a vault cycle whose policy allows prepare
    (`POLICY_WORK`: an ordinary or protected vault) while Inbox is watched and Suggest
    destinations for my inbox is not Off."""
    if obs.kind != "vault" or obs.policy is None or not settings.watch_inbox:
        return False
    if settings.grant_inbox_triage == "off":
        return False
    return "prepare" in POLICY_WORK.get(obs.policy, ())


def _first(candidates: Sequence[Candidate]) -> list[Candidate]:
    """The first `MAX_OPEN_ITEMS` distinct candidates: soonest deadline first (none last), then
    in `CANDIDATES` order, then by title and key."""
    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        unique.setdefault(candidate.item_key, candidate)

    def priority(candidate: Candidate) -> tuple[bool, float, int, str, str]:
        deadline = candidate.deadline
        return (
            deadline is None,
            0.0 if deadline is None else deadline.timestamp(),
            _TOPIC_RANK.get(candidate.type, len(_TOPIC_RANK)),
            candidate.title,
            candidate.item_key,
        )

    return sorted(unique.values(), key=priority)[:MAX_OPEN_ITEMS]


def _fileable(candidate: Candidate, held: tuple[Held, int] | None, limit: int) -> bool:
    """Whether a cycle may decide `candidate`: any candidate but a vault change, and a change
    only while one can be filed on the day at all (`_open_day`), no change row of the scope
    holds its key and no open row holds its (type, `related_ref`) target. `held` None (the rows
    were not read) reads as at the cap."""
    if candidate.type not in CHANGES:
        return True
    if held is None or not _open_day(held, limit):
        return False
    rows = held[0]
    return (
        candidate.item_key not in rows.keys
        and (candidate.type, candidate.related_ref) not in rows.open_targets
    )


def _holding(held: tuple[Held, int] | None, limit: int) -> Held:
    """What the change rows hold back from a cycle's vault-change read: the scope's `Held`, with
    no room once no change could be filed on the day (`_open_day` false, or the rows not read),
    so that the read stops after the notes changed lately (`vault_watch.read_changes`)."""
    if held is None:
        return _UNREAD
    rows = held[0]
    return rows if _open_day(held, limit) else replace(rows, room=False)


def _open_day(held: tuple[Held, int], limit: int) -> bool:
    """Whether the change rows let a change be filed on the day: the day's change rows in every
    workspace (`held`'s count) are under `limit`, and the scope has room for one more
    (`Held.room`)."""
    rows, today = held
    return today < limit and rows.room


def _tune_current(found: Candidate, wording: Mapping[str, object]) -> bool:
    """Whether a cached `question_tune` candidate still names a question worded as `wording`
    has it now (#127 follow-up 1): the beat that cached it may have run under a wording an
    approved proposal, a trial or a reset has since moved past, or under a name the playbook
    no longer tunes."""
    name = found.facts.get("name")
    if not isinstance(name, str):
        return False
    current = playbook.questions_for("question_tune", wording).get(name)
    return current is not None and found.facts.get("wording_json") == canonical_json(current)


def _estimate(request: ModelRequest) -> int:
    """The request's estimated input tokens."""
    return playbook.estimate_tokens(
        canonical_json(request.state) + canonical_json(request.questions)
    )


def _is_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _stem(ref: str) -> str:
    return ref.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".md")


def _code(exc: BaseException) -> str:
    """A failure as a log names it: its code for the loop's own errors, else its type; then, in
    brackets, the store's or sealing's own failure under it (`_cause`). Never its text, which
    can hold SQL, a path or vault text."""
    if isinstance(exc, DecisionLoopError | ModelError | MissionError):
        code = exc.code
    else:
        code = type(exc).__name__
    cause = _cause(exc)
    return code if cause is None else f"{code} ({cause})"


def _cause(exc: BaseException) -> str | None:
    """The code of the first `StateError` in `exc` and the errors it replaced (`__context__`, a
    few deep), or "ProtectionError" for sealing's: the loop's readers raise their own code with
    `from None`, which keeps the store's failure there and drops only its display."""
    found: BaseException | None = exc
    for _depth in range(4):
        if found is None:
            break
        if isinstance(found, StateError):
            return found.code
        if isinstance(found, protection.ProtectionError):
            return "ProtectionError"
        found = found.__context__
    return None
