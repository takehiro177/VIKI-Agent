"""The Decision loop's shared contract, so one name means one thing in every module and test.

What lives here:
- vocabularies: `CANDIDATES`, `CARDS`, `SEATS`, `LOOKS` (`VAULT_LOOKS` and `APP_LOOKS`),
  `LOOK_MISSIONS`, `MISSION_TYPES`, `IMPROVE` (with `CHANGES` and
  `PAIRED`), `TOPICS`, `KINDS`, `WORK`,
  `LEVELS` (with `ALPHA`), `TIMINGS`, the feedback values and reasons, `SOURCES`, `TRIGGERS`,
  `GRANTS`, `ACTIONS`, `SKILL_TRIGGERS`, the item, mission and call states, and the error codes;
- the model ids (`MODEL_IDS`), `SHADOWS`, the price, the loop's limits and the learning limits;
- frozen records: `Evidence`, `Candidate`, the answers, `ModelRequest`/`ModelReply`,
  `SkillHint`, `SuggestHint`, `PageFacts`, `Presence`, `DecisionSettings`, `Outcome`, and the
  stored `ItemRecord`, `CallRecord` and `MissionRecord` with their bodies; the learning snapshot
  `Learned` and its `Calibration` (with `CalibrationBin`), `QuestionRate`, `ShadowStats` and
  `TrialStats`;
- the team's seat records `SeatRoutine`, `SeatRun`, `SeatWaiting`, `SeatFacts` and `SeatTeam`;
- the Look records `SaveRow`, `DeletedNote`, `VaultLook`, `WaitingChat`, `HeavyServer`,
  `UncertainSend`, `StoppedBridge` and `AppLook`;
- the code look's records `CodeFolder` and `CodeLook`;
- the second looks' `SECOND_LOOKS`, `SECOND_LOOK_TYPES`, `FINDING_KINDS`, `UNTITLED_CHAT` and
  limits, with their records `SecondLookHint`, `Finding`, `LookRow`, `TriggerYield`, `KindYield`
  and `SecondLookYields`;
- the project plans' `PROJECT_PLANS`, `PROJECT_STALL`, `OVERDUE_CHECK_AFTER` and
  `MAX_PROJECT_PLANS`;
- the message drafts' `MESSAGE_DRAFTS` and `CONTEXT_NEARLY_FULL`, with their page records
  `ContextHint` and `SourceHint`;
- the saved checks' `SAVED_CHECKS`, `CHECK_PHASES`, `CHECK_LOOK_EVERY`, `CHECK_HEAD_SETTLE` and
  `MAX_CHECK_FOLDERS`, with their records `CheckFolder` and `CheckLook`;
- the inbox triage's `INBOX_TRIAGE`, `TRIAGE_PHASES` and `INBOX_SETTLE`, with their records
  `InboxRun` and `InboxLook`;
- protocols `ModelPort`, `MissionHost`, `MissionSession`, `SeatHost`, `LookHost`, `CodeHost`,
  `CheckHost` and `InboxHost`; errors
  `ModelError` and `DecisionLoopError`;
- pure helpers `canonical_json`, `sha256_hex`, `item_key`, `parse_settings` and `quiet_until`.

Pure: no I/O and no clock reads. It imports nothing else from the app, so any module, the
settings registry included, can import it without a cycle.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

# ---- vocabularies ------------------------------------------------------------------------------

# Vault upkeep Decision may propose: link two notes, mark an older note superseded by a newer
# one, archive a project's ticked tasks. `PAIRED` names the two that rest on an (older, newer) or
# (A, B) note pair, as opposed to tasks_ticked's single ref.
CHANGES = ("note_link", "note_superseded", "tasks_ticked")
PAIRED = ("note_link", "note_superseded")
# The self-improvement candidate types: offer a new skill, suggest one of the user's own skills
# for the chat in view, learn a source, call for a retro, tune Decision's own questions, and
# propose vault upkeep (`CHANGES`).
IMPROVE = ("skill_offer", "skill_suggest", "learn_source", "retro_due", "question_tune") + CHANGES
# The Kanban card types: a card that needs the user, and a card that has gone stale.
CARDS = ("card_needs_you", "card_stale")
# The routine-seat types: a run waiting on the user, a seat working, a seat ready to start, and
# a report on a start VIKI held back.
SEATS = ("seat_waiting", "seat_working", "seat_start", "seat_report")
# The Look types, things VIKI sees without a chat in view. The vault ones: a save or recovery that
# has stopped or runs short, and a memory whose cited note changed after its review. The app ones:
# a stopped bridge, a chat waiting on the user, a send that may not have gone, and an MCP server
# that fills the context.
VAULT_LOOKS = ("vault_recovery", "memory_drift")
APP_LOOKS = ("bridge_stopped", "chat_waiting", "send_uncertain", "mcp_context")
LOOKS = VAULT_LOOKS + APP_LOOKS
# The Look missions: a Look that may also start a chat. `code_look` offers a second look at a
# trusted code folder whose uncommitted changes have sat untouched.
LOOK_MISSIONS = ("code_look",)
# The second looks: VIKI offers to look again, in plan mode, at a chat's finished work. One per
# trigger: `long_task_look` after a long task, `correction_look` after work the user corrected.
SECOND_LOOKS = ("long_task_look", "correction_look")
# The chat skill triggers that offer a second look, and the type each one offers.
SECOND_LOOK_TYPES: Mapping[str, str] = {
    "correction": "correction_look",
    "long_session": "long_task_look",
}
# What a second look may find, in the order the learning view lists them.
FINDING_KINDS = ("flaw", "approach", "missing")
# The title a second look uses for a chat without one.
UNTITLED_CHAT = "This chat"
# The project plans: VIKI offers to plan in plan mode, for an active project with no recent change
# (`stalled_project`) or for a project's overdue tasks (`overdue_tasks`).
PROJECT_PLANS = ("stalled_project", "overdue_tasks")
# The message drafts: VIKI offers to put a message in a vault chat's box, never to send it. One for
# a chat whose context is nearly full (`context_handoff`) and one for a reply that fetched web
# pages (`source_note`).
MESSAGE_DRAFTS = ("context_handoff", "source_note")
# The saved checks: VIKI offers to run, or runs, the checks the user has run in a code folder
# before, once the folder has stayed at a new commit they have not run on (`commit_checks`).
SAVED_CHECKS = ("commit_checks",)
# Where one folder commit's saved checks stand: not run yet, running, all passed, one failed, or
# one did not finish (cancelled or interrupted).
CHECK_PHASES = ("waiting", "running", "passed", "failed", "stopped")
# The inbox triage: VIKI offers to start, or starts, the inbox's read-only suggestion run so
# suggestions wait in Triage inbox (`inbox_triage`).
INBOX_TRIAGE = ("inbox_triage",)
# Where one inbox state's suggestion run stands: not started, running, suggestions wait, it
# failed, or it found no destination to suggest.
TRIAGE_PHASES = ("waiting", "running", "ready", "failed", "nothing")
CANDIDATES = (
    (
        "approvals",
        "tasks_due",
        "agent_runs",
        "inbox",
        "uncommitted",
        "routine",
        "mission_report",
    )
    + CARDS
    + SEATS
    + LOOKS
    + LOOK_MISSIONS
    + SECOND_LOOKS
    + PROJECT_PLANS
    + MESSAGE_DRAFTS
    + SAVED_CHECKS
    + INBOX_TRIAGE
    + IMPROVE
)
# The only types whose items may start a mission.
MISSION_TYPES = ("tasks_due",)
TOPICS = (*CANDIDATES, "unwatched")
KINDS = ("inform", "ask", "suggest", "ready", "report")
WORK = ("none", "prepare", "run", "coordinate")
LEVELS = ("silent", "list", "badge", "nudge", "alert")
ALPHA: Mapping[str, float] = {"silent": 0.0, "list": 0.0, "badge": 0.1, "nudge": 1.0, "alert": 3.0}
TIMINGS = ("now", "at", "after_focus", "in_context")
FEEDBACK_SHOWN = ("useful", "wrong", "later", "mute")
FEEDBACK_HELD = ("missed", "right_to_hold")
FEEDBACK_REASONS = ("out_of_date", "already_knew", "wrong_topic", "bad_timing")
SOURCES = ("brief", "rail", "review", "launcher", "page")
TRIGGERS = (
    "app_open",
    "new_chat",
    "review",
    "change",
    "launcher",
    "context",
    "heartbeat",
    "mission",
)
GRANTS = ("off", "ask", "allowed")
# Percent of a plan window's use at which VIKI holds its own starts; "off" leaves a reached
# limit holding (plan_brake.py).
PLAN_BRAKES = ("off", "50", "80", "95")
ACTIONS = (
    "open_chat",
    "open_note",
    "open_approvals",
    "open_inbox",
    "open_routine",
    "open_runs",
    "open_context",
    "open_vault",
    "open_messaging",
    "open_code",
    "open_triage",
    "start_prepare",
    "start_run",
    "stop_mission",
    "start_seat",
    "stop_seat",
    "run_checks",
    "stop_checks",
    "suggest_inbox",
    "draft_chat",
    "draft_new",
)
# What fired a chat's skill hint, in the order the client's `skillHint` tries them.
SKILL_TRIGGERS = ("correction", "repeated_request", "long_session")
ITEM_STATES = ("open", "acted", "dismissed", "stale", "done")
MISSION_STATES = ("starting", "running", "done", "failed", "stopped", "unknown")
CALL_OUTCOMES = ("answered", "failed")
MODEL_ERROR_CODES = (
    "no_key",
    "key_rejected",
    "refused",
    "too_large",
    "rate_limited",
    "overloaded",
    "unreachable",
    "invalid_reply",
    "failed",
)
LOOP_ERROR_CODES = (
    "invalid_request",
    "unknown_item",
    "not_applicable",
    "limit",
    "busy",
    "unavailable",
)

# ---- model and limits --------------------------------------------------------------------------

MODEL_IDS: Mapping[str, str] = {
    "pinned": "jev-1.14.0",
    "latest": "jev-latest",
    "preview": "jev-preview",
}
SHADOWS = ("off", *MODEL_IDS)
PRICE_USD_PER_MTOK = 0.042  # TypeSafe's Models page, 2026-10-21
REQUEST_LIMIT = 64_000
QUESTION_LIMIT = 32_000
MAX_IN_FLIGHT = 2
MAX_PER_MINUTE = 10
OBSERVE_S = 60
HEARTBEAT_S = 300
COALESCE_S = 2
RECHECK_S = 3.0
NUDGE_CAP = 2  # nudges and alerts in a rolling hour
TYPING_HOLD_S = 3
AWAY_S = 300
ESCALATE_S = 1800
MAX_RUNNING_MISSIONS = 2
MISSION_MINUTES = 30
MAX_OPEN_ITEMS = 50
MAX_CALLS = 20
MAX_EPISODES = 10_000
RETENTION_DAYS = 365
LATER = timedelta(hours=4)
CARD_QUIET = timedelta(minutes=30)
CARD_STALE_WORKING = timedelta(days=1)
CARD_STALE_TODO = timedelta(days=3)
MAX_CARDS_NEED_YOU = 6
MAX_CARDS_STALE = 3
# The Look thresholds: a save row idle this long is stopped; a deleted note expiring within the
# warning is listed; the recovery store is full at this share of either bound; a card waits this
# long before its chat is listed (at most `MAX_CHATS_WAITING`); a send is uncertain for a day
# (at most `MAX_UNCERTAIN_SENDS`); an MCP server is heavy at this share of a chat's context window
# (at most `MAX_MCP_CONTEXT`).
SAVE_STOPPED_AFTER = timedelta(minutes=2)
RECOVERY_EXPIRY_WARNING = timedelta(days=3)
RECOVERY_FULL_SHARE = 0.9
CHAT_WAIT = timedelta(minutes=5)
MAX_CHATS_WAITING = 6
UNCERTAIN_SEND_WINDOW = timedelta(days=1)
MAX_UNCERTAIN_SENDS = 5
MCP_CONTEXT_SHARE = 0.1
MAX_MCP_CONTEXT = 3
# The code look: a folder's changes idle this long are worth a look, the reader looks at one
# folder this often, and at most `MAX_CODE_LOOKS` folders are listed.
CODE_LOOK_IDLE = timedelta(days=1)
CODE_LOOK_EVERY = timedelta(minutes=30)
MAX_CODE_LOOKS = 3
# The saved checks: the host reads a folder's commit this often, a commit counts as new once the
# folder has stayed at it this long, and at most `MAX_CHECK_FOLDERS` folders are listed.
CHECK_LOOK_EVERY = timedelta(minutes=5)
CHECK_HEAD_SETTLE = timedelta(minutes=10)
MAX_CHECK_FOLDERS = 3
# The inbox triage: the newest listed capture must have stayed unchanged this long before VIKI
# offers or starts a suggestion run.
INBOX_SETTLE = timedelta(minutes=10)
# The second looks: a hint names at most `MAX_SECOND_LOOK_REFS` notes of at most
# `MAX_SECOND_LOOK_REF_BYTES` UTF-8 bytes in all; learning reads the last `SECOND_LOOK_WINDOW`; a
# type is paused once `SECOND_LOOK_ANSWERED` looks were answered and fewer than one in
# `SECOND_LOOK_ACCEPT_ONE_IN` was accepted; a kind is paused after `SECOND_LOOK_KIND_REJECTS`
# rejections in a row.
MAX_SECOND_LOOK_REFS = 10
MAX_SECOND_LOOK_REF_BYTES = 1024
SECOND_LOOK_WINDOW = timedelta(days=30)
SECOND_LOOK_ANSWERED = 10
SECOND_LOOK_ACCEPT_ONE_IN = 4
SECOND_LOOK_KIND_REJECTS = 5
# The project plans: a project is stalled once nothing in it changed for `PROJECT_STALL`, a task
# is worth checking once its due date passed `OVERDUE_CHECK_AFTER` ago, and at most
# `MAX_PROJECT_PLANS` stalled projects are listed.
PROJECT_STALL = timedelta(days=14)
OVERDUE_CHECK_AFTER = timedelta(days=3)
MAX_PROJECT_PLANS = 3
# A chat's context is nearly full from this share, in percent: the share the status bar shows.
CONTEXT_NEARLY_FULL = 70
STATE_BYTES = 12_000
EXCERPT: Mapping[str, int] = {"titles": 0, "excerpts": 200, "sections": 2000}

# Learning: bounds for the relearn fit, the rows it reads and the weekly audit sample.
MIN_LABELS = 20
MIN_EACH = 3
MAX_FIT_ROWS = 1_000
MAX_LEARN_ROWS = 5_000
AUDIT_SIZE = 10

# ---- records -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """One thing a candidate rests on. `outside` marks text from other people or the web;
    `tags` is None when the note's tags are unknown."""

    ref: str | None
    excerpt: str
    outside: bool
    tags: tuple[str, ...] | None


@dataclass(frozen=True)
class Candidate:
    """Something VIKI might bring up. `type` is one of `CANDIDATES`; `facts` are code values and
    `words` their word forms; `rules_evidence` is what the rules policy reads; `work_allowed`
    names the `WORK` this item may lead to."""

    item_key: str
    type: str
    title: str
    facts: Mapping[str, object]
    words: Mapping[str, str]
    evidence: tuple[Evidence, ...]
    related_chat: str | None
    related_ref: str | None
    deadline: datetime | None
    shown_elsewhere: tuple[str, ...]
    rules_evidence: Mapping[str, object]
    origin_session: str | None
    work_allowed: tuple[str, ...]


@dataclass(frozen=True)
class NoulAnswer:
    """A yes/no question's answer: `p` is the probability of yes."""

    p: float


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    """A score question's answer; `probabilities` is keyed by level index."""

    score: float
    confidence: float
    probabilities: Mapping[int, float]


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer
Answers = Mapping[str, Answer]


@dataclass(frozen=True)
class ModelRequest:
    """One ask: a model id from `MODEL_IDS`, the state object and the questions by name."""

    model: str
    state: Mapping[str, object]
    questions: Mapping[str, Mapping[str, object]]


@dataclass(frozen=True)
class ModelReply:
    model: str
    answers: Answers
    input_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class SkillHint:
    """A chat's skill hint as the page reported it: `trigger` is one of `SKILL_TRIGGERS`,
    `sessions` the chats it is about (the chat in view first) and `titles` their titles in the
    same order, "" for an untitled one."""

    trigger: str
    sessions: tuple[str, ...]
    titles: tuple[str, ...]


@dataclass(frozen=True)
class SuggestHint:
    """A vault chat's own facts for skill_suggest: `session` is the chat, `title` its own title
    and `used` the skill names and slash commands already seen there. `title` is matched against
    the workspace's own skills on this computer; it, and the names in `used`, are never sent to
    the Decision model."""

    session: str
    title: str
    used: tuple[str, ...]


@dataclass(frozen=True)
class SecondLookHint:
    """A vault chat's finished turn worth a second look: `session` is the chat, `turn` the turn,
    `trigger` one of `SECOND_LOOK_TYPES`, `title` the chat's title ("" when untitled) and `refs`
    the notes the turn changed."""

    session: str
    turn: str
    trigger: str
    title: str
    refs: tuple[str, ...]


@dataclass(frozen=True)
class ContextHint:
    """A vault chat's context share: `session` is the chat and `percent` the whole percent of its
    context window in use, 0 to 100."""

    session: str
    percent: int


@dataclass(frozen=True)
class SourceHint:
    """A vault chat's last reply that fetched web pages: `session` is the chat, `turn` the reply
    and `notes` whether the reply also read a note in the vault."""

    session: str
    turn: str
    notes: bool


@dataclass(frozen=True)
class PageFacts:
    """What the page itself counted: cards waiting, runs, the git state and the next routine
    (`routine_id`, `next_run` and `routines_paused` are set together, or all None), and the
    chat in view's skill hint, skill-suggestion facts, second-look hint, context share and fetched
    sources, each its own type or None."""

    native_waiting: int
    runs_working: int
    runs_review: int
    git_state: str
    git_dirty: int | None
    routine_id: str | None
    next_run: datetime | None
    routines_paused: bool | None
    skill: SkillHint | None = None
    suggest: SuggestHint | None = None
    second_look: SecondLookHint | None = None
    context: ContextHint | None = None
    sources: SourceHint | None = None


@dataclass(frozen=True)
class Presence:
    """Where the user is. `note_ref` is the open note as (workspace_id, ref)."""

    focused: bool
    typing: bool
    away: bool
    chat_id: str | None
    chat_title: str | None
    note_ref: tuple[str, str] | None
    launcher_open: bool
    page: PageFacts | None


@dataclass(frozen=True)
class DecisionSettings:
    """The Decision settings `parse_settings` reads; the defaults are the registered ones.

    One field per `decision.*` key (`decision.grant.run` is `grant_run`); `paused_until` and
    `focus_until` are aware UTC instants or None; `never_send` keeps the stored entries, each a
    folder ending "/" or a tag starting "#". `quiet` is the ("HH:MM", "HH:MM") quiet hours from
    `notifications.quiet_start`/`quiet_end`, or None; `never_send_invalid` says the stored
    never-send value was unusable, so `never_send` is empty.
    """

    enabled: bool = False
    paused_until: datetime | None = None
    focus_until: datetime | None = None
    send_level: str = "excerpts"
    never_send: tuple[str, ...] = ()
    watch_tasks: bool = True
    watch_inbox: bool = True
    watch_vault_changes: bool = True
    watch_approvals: bool = True
    watch_chats: bool = True
    watch_routines: bool = True
    watch_improve: bool = True
    watch_kanban: bool = True
    watch_vault_health: bool = True
    watch_messaging: bool = True
    cadence: str = "change"
    daily_cap_usd: float = 0.5
    nudges_per_hour: int = 2
    alerts: bool = False
    in_context: bool = True
    model_version: str = "pinned"
    grant_prepare: str = "allowed"
    grant_run: str = "ask"
    grant_team: str = "ask"
    grant_look: str = "allowed"
    grant_second_look: str = "allowed"
    grant_project_plan: str = "allowed"
    grant_check: str = "ask"
    grant_inbox_triage: str = "allowed"
    limit_drafts: int = 5
    limit_chats: int = 3
    limit_changes: int = 3
    plan_brake: str = "80"
    limit_team: int = 4
    team_handoffs: bool = False
    limit_look: int = 3
    limit_second_look: int = 3
    limit_project_plan: int = 3
    limit_message_draft: int = 3
    limit_check: int = 3
    limit_inbox_triage: int = 3
    learning: bool = True
    shadow: str = "off"
    launcher_session: str = ""
    quiet: tuple[str, str] | None = None
    never_send_invalid: bool = False


@dataclass(frozen=True)
class Outcome:
    level: str
    timing: str
    defer_until: datetime | None
    work: str
    kind: str | None
    reason: str
    terms: Mapping[str, float]
    blocked_by: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceLabel:
    label: str
    workspace_id: str | None = None
    ref: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class ActionSpec:
    """One button on an item; `type` is one of `ACTIONS`, the ids are what it opens or starts,
    and `text` is the message a draft action puts in a message box (`draft_chat`: the chat
    `session_id`'s; `draft_new`: a new chat's)."""

    type: str
    label: str
    primary: bool
    session_id: str | None = None
    workspace_id: str | None = None
    ref: str | None = None
    routine_id: str | None = None
    call_id: str | None = None
    mission_id: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class ItemBody:
    title: str
    what: str
    why: str
    evidence: tuple[EvidenceLabel, ...]
    actions: tuple[ActionSpec, ...]


@dataclass(frozen=True)
class ItemRecord:
    """A stored item: its columns as stored (stamps as text) and its unsealed body."""

    item_key: str
    episode_id: str
    candidate: str
    kind: str | None
    level: str
    state: str
    timing: str
    defer_until: str | None
    created_at: str
    shown_at: str | None
    mission_id: str | None
    answers_json: str | None
    state_hash: str | None
    body: ItemBody


@dataclass(frozen=True)
class CallBody:
    """What one model call sent and got. `split` is (part, tokens) in display order and
    `held_back` the number of evidence entries never-send kept back."""

    title: str
    candidate: str
    state_text: str
    questions_text: str
    split: tuple[tuple[str, int], ...]
    answers_json: str | None
    held_back: int
    version_setting: str
    playbook_version: str


@dataclass(frozen=True)
class CallRecord:
    """A stored model call: its columns as stored (stamps as text) and its unsealed body."""

    call_id: str
    created_at: str
    item_key: str
    episode_id: str | None
    session_id: str | None
    model: str | None
    outcome: str
    failure: str | None
    latency_ms: int | None
    input_tokens: int | None
    cost_micro_usd: int | None
    body: CallBody


@dataclass(frozen=True)
class MissionRecord:
    """A stored mission: its columns as stored (stamps as text); `title` and `brief` come from
    its sealed body."""

    mission_id: str
    scope: str
    session_id: str
    item_key: str
    work: str
    state: str
    reason: str
    title: str
    brief: str
    created_at: str
    deadline_at: str
    finished_at: str | None


@dataclass(frozen=True)
class Learned:
    """A fitted snapshot of what Decision learned, as stored in the "learned" preference.

    `weights` keys are "bias", five feature names and "topic.<candidate>" for each
    `CANDIDATES`. `reach` and `costs` key by level name (reach: list, badge, nudge, alert;
    costs: badge, nudge, alert). `support` counts the rows behind the fit: "topic.<candidate>"
    for each `CANDIDATES`, and "reach.<level>" and "costs.<level>" for the levels above.
    """

    fitted_at: datetime
    labels: int
    weights: Mapping[str, float]
    reach: Mapping[str, float]
    costs: Mapping[str, float]
    support: Mapping[str, int]


@dataclass(frozen=True)
class CalibrationBin:
    """One probability band `[low, high)` of the calibration table; `mean_p` and `rate` are
    None when `n` is 0."""

    low: float
    high: float
    n: int
    mean_p: float | None
    rate: float | None


@dataclass(frozen=True)
class Calibration:
    """How well recent `p` matched the user's labels: the Brier score, plus five probability
    bins. `brier` is None with no scored rows."""

    n: int
    brier: float | None
    bins: tuple[CalibrationBin, ...]


@dataclass(frozen=True)
class QuestionRate:
    """One yes/no question's usefulness when Decision answered yes and when it answered no;
    `name` is the question's name in `playbook.questions`."""

    name: str
    yes_n: int
    yes_rate: float | None
    no_n: int
    no_rate: float | None


@dataclass(frozen=True)
class ShadowStats:
    """How the shadowed model version (`setting`, a `SHADOWS` member) compared to the live one
    over recent decisions; `brier`/`live_brier` are None without labelled shadow decisions."""

    setting: str
    decisions: int
    agree: int
    kind_agree: int
    labels: int
    brier: float | None
    live_brier: float | None


@dataclass(frozen=True)
class TrialStats:
    """How a proposed wording of the yes/no question `name` compared with the live wording over
    the decisions it was tried on: `agree` counts those whose two answers fell on the same side
    of 0.5, `labels` those the user labelled, and `live` and `trial` are each wording's
    `QuestionRate`."""

    name: str
    decisions: int
    agree: int
    labels: int
    live: QuestionRate
    trial: QuestionRate


# ---- protocols ---------------------------------------------------------------------------------


class ModelPort(Protocol):
    """The Decision model's client."""

    async def ask(self, request: ModelRequest, *, key: str) -> ModelReply: ...

    async def close(self) -> None: ...


class _State(Protocol):
    def remember(self, session_id: str, **changes: Any) -> object: ...


class MissionSession(Protocol):
    """What a mission reaches of `SessionActor`. It has no way to answer a card, so a mission
    cannot answer its chat's permission requests."""

    @property
    def busy(self) -> bool: ...

    def subscribe(self, subscriber: Callable[[dict[str, Any]], None]) -> Callable[[], None]: ...

    async def send(self, text: str) -> bool | None: ...

    async def interrupt(self) -> None: ...


class MissionHost(Protocol):
    """What a mission reaches of `SessionManager`. `create` takes the id alone: no read-only
    tools and no session root."""

    @property
    def app_state(self) -> _State: ...

    def create(self, session_id: str) -> MissionSession: ...

    def get(self, session_id: str) -> MissionSession | None: ...

    async def close(self, session_id: str) -> None: ...


# ---- the team's routine seats ------------------------------------------------------------------


@dataclass(frozen=True)
class SeatRoutine:
    """A vault routine as the seat planner sees it: `state` its stored state, `revision` its
    current revision, `tested_revision` the revision last tested (None when none was), `seat` the
    seat name its revision claims (None when it claims none) and `stop_at_draft` whether its
    revision stops at a draft."""

    routine_id: str
    state: str
    revision: int
    tested_revision: int | None
    seat: str | None
    stop_at_draft: bool


@dataclass(frozen=True)
class SeatRun:
    """One attempt of a routine. `kind` says how it began, `dispatched` whether its session was
    handed the prompt, `finished` and `outcome` are None while it is open, `reason` the stored
    reason text."""

    attempt_id: str
    routine_id: str
    kind: str
    session_id: str
    started: datetime
    dispatched: bool
    finished: datetime | None
    outcome: str | None
    reason: str


@dataclass(frozen=True)
class SeatWaiting:
    """An open run that waits on a card: `question` is true for a question card, false for a
    permission or plan card."""

    routine_id: str
    attempt_id: str
    session_id: str
    question: bool


@dataclass(frozen=True)
class SeatFacts:
    """What the seat planner reads of one vault: its `routines`, the `runs` in the asked window
    (open ones first, then by finish, latest first; past the host's window cap the last, of kind
    "unread", runs from the window's start to the finish of the first run left out), the
    `newest` run per routine id, `free` the slots VIKI's own start may take (1 while no routine
    run is going, else 0), `today` the starts made that day, `paused` whether Pause all holds,
    and `complete` false when a bad stamp or an unreadable routine left something out."""

    routines: tuple[SeatRoutine, ...]
    runs: tuple[SeatRun, ...]
    newest: Mapping[str, SeatRun]
    free: int
    today: int
    paused: bool
    complete: bool


@dataclass(frozen=True)
class SeatTeam:
    """The team's counts for the page: `today` starts made that day, `running` decision runs
    open now, and `sessions` the decision sessions started in the window, newest first."""

    today: int
    running: int
    sessions: tuple[str, ...]


class SeatHost(Protocol):
    """What Decision reaches of the routine runner. `facts` and `team` read the store and run in
    a worker thread; `waiting` and the async methods run on the event loop. `since` is the
    window's start (None reads no runs) and `day` the local day the counts cover. `start` answers
    the new session's id and raises `DecisionLoopError` (limit, busy, not_applicable or
    unavailable) when it cannot start: `cards` is None for the user's Start, which may take
    either routine-run slot, and VIKI's own start, with its cards' hash, is busy while any
    routine run is going. `stop` answers whether a run was interrupted and `stop_all` how
    many."""

    def facts(self, workspace_id: str, since: datetime | None, day: str) -> SeatFacts: ...

    def team(self, day: str, since: datetime | None) -> SeatTeam: ...

    def waiting(self) -> tuple[SeatWaiting, ...]: ...

    async def start(
        self,
        routine_id: str,
        revision: int,
        *,
        newest: str | None,
        cards: str | None,
        limit: int,
        day: str,
    ) -> str: ...

    async def stop(self, routine_id: str) -> bool: ...

    async def stop_all(self) -> int: ...


# ---- the Look: what VIKI sees without a chat ---------------------------------------------------


@dataclass(frozen=True)
class SaveRow:
    """One save or vault-operation row of a vault: `operation` is true for an operation row and
    false for a save, `blocked` whether it needs recovery, `updated` when it last moved (None when
    its stamp does not parse)."""

    row_id: str
    operation: bool
    blocked: bool
    updated: datetime | None


@dataclass(frozen=True)
class DeletedNote:
    """A note in the recovery store: `expires` is when its copy goes (None when unknown)."""

    recovery_id: str
    expires: datetime | None


@dataclass(frozen=True)
class VaultLook:
    """What the vault reader saw of one vault. Each part is a tuple, or None when it could not be
    read: `saves` the rows, `deleted` the recovery store's deleted notes, `usage` its (entries,
    bytes), `drift` the ids of active memories whose cited note changed after their review.
    `failures` names the parts that failed, by their words."""

    saves: tuple[SaveRow, ...] | None
    deleted: tuple[DeletedNote, ...] | None
    usage: tuple[int, int] | None
    drift: tuple[str, ...] | None
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class WaitingChat:
    """A live chat whose oldest open card of `kind` was raised at `since`."""

    session_id: str
    tool_use_id: str
    kind: str
    since: datetime


@dataclass(frozen=True)
class HeavyServer:
    """An MCP `server` that fills a large share of one chat's context."""

    session_id: str
    server: str


@dataclass(frozen=True)
class UncertainSend:
    """A send an earlier launch left unconfirmed, last updated at `updated`."""

    command_id: str
    session_id: str
    updated: datetime


@dataclass(frozen=True)
class StoppedBridge:
    """A messaging `provider` whose bridge is stopped; `restart` whether it asks for a restart
    and `reason` its stored reason (None when it has none)."""

    provider: str
    restart: bool
    reason: str | None


@dataclass(frozen=True)
class AppLook:
    """What the app reader saw: `waiting` chats, `heavy` servers, `sends` and `bridges`. Each part
    is a tuple, or None when it was not asked for or could not be read; `failures` names the parts
    that failed, by their words."""

    waiting: tuple[WaitingChat, ...] | None
    heavy: tuple[HeavyServer, ...] | None
    sends: tuple[UncertainSend, ...] | None
    bridges: tuple[StoppedBridge, ...] | None
    failures: tuple[str, ...] = ()


class LookHost(Protocol):
    """What Decision reaches of the live app. `read` runs on the event loop and never raises; a
    part whose flag is false is not read and answers None."""

    async def read(self, *, waiting: bool, heavy: bool, sends: bool, bridges: bool) -> AppLook: ...


@dataclass(frozen=True)
class CodeFolder:
    """A trusted code folder (`workspace_id`) with its `name` and `newest`, when its newest
    uncommitted change was last touched."""

    workspace_id: str
    name: str
    newest: datetime


@dataclass(frozen=True)
class CodeLook:
    """What the code reader saw: the `folders` it answers, or None when it could not list them;
    `failures` names the parts that failed, by their words."""

    folders: tuple[CodeFolder, ...] | None
    failures: tuple[str, ...] = ()


class CodeHost(Protocol):
    """What Decision reaches of the trusted code folders. `read` blocks, so it runs in a worker
    thread, and never raises."""

    def read(self, now: datetime) -> CodeLook: ...


@dataclass(frozen=True)
class CheckFolder:
    """A trusted code folder (`workspace_id`) with its `name` whose HEAD `head` has saved checks
    in `phase`, one of `CHECK_PHASES`; `pending` holds the ids of the checks with no run at that
    commit."""

    workspace_id: str
    name: str
    head: str
    phase: str
    pending: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckLook:
    """What the check reader saw: the `folders` it answers, or None when it could not list them;
    `failures` names the parts that failed, by their words."""

    folders: tuple[CheckFolder, ...] | None
    failures: tuple[str, ...] = ()


class CheckHost(Protocol):
    """What Decision reaches of the saved checks. `supported` reflects the platform. `read` and
    `folder` block, so they run in a worker thread, and never raise; `read` answers the folders
    whose commit has settled and `folder` one fresh look at a folder (None when it has no commit
    to report). `running` also runs in a worker thread. `start` answers how many checks it started
    and raises `DecisionLoopError` (not_applicable when the commit moved, unavailable) when it
    cannot; `stop` and `stop_all` answer how many started runs were live. `stops` answers how
    many times Stop all ran since launch; a start given the count its caller read starts nothing
    once a Stop all has run since."""

    def supported(self) -> bool: ...

    def read(self, now: datetime) -> CheckLook: ...

    def folder(self, workspace_id: str, now: datetime) -> CheckFolder | None: ...

    def running(self) -> int: ...

    def stops(self) -> int: ...

    async def start(
        self, workspace_id: str, head: str, check_ids: Sequence[str], *, stops: int | None = None
    ) -> int: ...

    async def stop(self, workspace_id: str) -> int: ...

    async def stop_all(self) -> int: ...


@dataclass(frozen=True)
class InboxRun:
    """The inbox suggestion run as the suggester reports it: its `run_id`, `state` (running, done
    or failed), the `reason` it failed (None otherwise) and when it `started_at`, a stamp."""

    run_id: str
    state: str
    reason: str | None
    started_at: str


@dataclass(frozen=True)
class InboxLook:
    """What the inbox host remembers of one vault: its latest `run` (None when it has none),
    the suggestions `answered` (capture ref to the modified stamp it was suggested at) and the item
    keys `started` (key to run id, "" for a start that was refused)."""

    run: InboxRun | None
    answered: Mapping[str, str]
    started: Mapping[str, str]


class InboxHost(Protocol):
    """What Decision reaches of the inbox suggestion run. `read` answers from memory on the event
    loop and never raises. `start` starts the run for `item_key` and answers its run id; it raises
    `DecisionLoopError` (busy, not_applicable, unavailable) when it cannot. `persistent` is True
    when the item keys it started outlive a restart."""

    persistent: bool

    def read(self, workspace_id: str) -> InboxLook: ...

    async def start(self, workspace_id: str, item_key: str) -> str: ...


@dataclass(frozen=True)
class Finding:
    """A second look (mission `mission_id`, type `look`) in `scope` whose plan waits in chat
    `session_id`; `kind` is one of `FINDING_KINDS` or "other", `title` the chat's title for a
    second look and the project's name for a project plan."""

    mission_id: str
    scope: str
    session_id: str
    look: str
    kind: str
    title: str


@dataclass(frozen=True)
class LookRow:
    """One second look as learning reads it: its type `look`, mission `state`, stored `reason`
    (its outcome) and when it `started`. A message draft has no mission: its row carries the
    item's state and creation time in the same fields."""

    look: str
    state: str
    reason: str
    started: datetime


@dataclass(frozen=True)
class TriggerYield:
    """What one second-look type yielded: looks `made`, how many were `accepted`, `rejected` or
    found `nothing`, the user's Not useful `reasons` by count, and whether the type is
    `paused`."""

    look: str
    made: int
    accepted: int
    rejected: int
    nothing: int
    reasons: Mapping[str, int]
    paused: bool


@dataclass(frozen=True)
class KindYield:
    """How many looks of finding `kind` the user turned down in a row, and whether it is
    `paused`."""

    kind: str
    rejected_in_a_row: int
    paused: bool


@dataclass(frozen=True)
class SecondLookYields:
    """What the second looks yielded: one `TriggerYield` per type and one `KindYield` per kind."""

    triggers: tuple[TriggerYield, ...]
    kinds: tuple[KindYield, ...]


# ---- errors ------------------------------------------------------------------------------------


class ModelError(RuntimeError):
    """A model call that did not answer; `code` is one of `MODEL_ERROR_CODES` and
    `retry_after_s`, when set, how long to wait. Its text is the code alone."""

    def __init__(self, code: str, retry_after_s: float | None = None) -> None:
        self.code = code
        self.retry_after_s = retry_after_s
        super().__init__(code)


class DecisionLoopError(RuntimeError):
    """A safe refusal; `code` is one of `LOOP_ERROR_CODES` and `extra` keeps the keyword details
    the raiser adds. Its text is the code alone."""

    def __init__(self, code: str, **extra: object) -> None:
        self.code = code
        self.extra = dict(extra)
        super().__init__(code)


# ---- helpers -----------------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """`obj` as JSON with sorted keys, no spaces and ASCII only; datetimes as ISO text. NaN and
    infinities raise ValueError, other values JSON cannot hold TypeError."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_json_default,
    )


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON-serializable: {type(value).__name__}")


def sha256_hex(text: str) -> str:
    """The lower-case hex SHA-256 of `text`'s UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def item_key(*parts: str) -> str:
    """An item's stable key: `sha256_hex` of `parts` joined by the unit separator."""
    return sha256_hex("\x1f".join(parts))


_CADENCES = ("change", "5m", "15m")
_DAILY_CAPS_USD = (0.1, 0.25, 0.5, 1, 2)
_NUDGES_PER_HOUR = (0, 1, 2)
_LIMITS = tuple(range(11))
_QUIET_TIMES = frozenset(f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30))
_NEVER_SEND_ENTRIES = 32
_NEVER_SEND_CHARS = 128


def parse_settings(effective: Mapping[str, object]) -> DecisionSettings:
    """The Decision settings in an effective settings map.

    A missing, wrong-typed or unlisted value reads as its default. A `decision.never_send` value
    that is present but unusable (not a JSON list of at most 32 entries, each 1 to 128
    characters without control characters, ending "/" or starting "#") reads as no entries with
    `never_send_invalid` set. Quiet hours need both bounds set, listed and different.
    """
    default = DecisionSettings()
    never_send, never_send_invalid = _never_send(effective)
    return DecisionSettings(
        enabled=_flag(effective, "decision.enabled", default.enabled),
        paused_until=_instant(effective.get("decision.paused_until")),
        focus_until=_instant(effective.get("decision.focus_until")),
        send_level=_option(effective, "decision.send_level", tuple(EXCERPT), default.send_level),
        never_send=never_send,
        watch_tasks=_flag(effective, "decision.watch.tasks", default.watch_tasks),
        watch_inbox=_flag(effective, "decision.watch.inbox", default.watch_inbox),
        watch_vault_changes=_flag(
            effective, "decision.watch.vault_changes", default.watch_vault_changes
        ),
        watch_approvals=_flag(effective, "decision.watch.approvals", default.watch_approvals),
        watch_chats=_flag(effective, "decision.watch.chats", default.watch_chats),
        watch_routines=_flag(effective, "decision.watch.routines", default.watch_routines),
        watch_improve=_flag(effective, "decision.watch.improve", default.watch_improve),
        watch_kanban=_flag(effective, "decision.watch.kanban", default.watch_kanban),
        watch_vault_health=_flag(
            effective, "decision.watch.vault_health", default.watch_vault_health
        ),
        watch_messaging=_flag(effective, "decision.watch.messaging", default.watch_messaging),
        cadence=_option(effective, "decision.cadence", _CADENCES, default.cadence),
        daily_cap_usd=float(
            _number(effective, "decision.daily_cap_usd", _DAILY_CAPS_USD, default.daily_cap_usd)
        ),
        nudges_per_hour=int(
            _number(
                effective, "decision.nudges_per_hour", _NUDGES_PER_HOUR, default.nudges_per_hour
            )
        ),
        alerts=_flag(effective, "decision.alerts", default.alerts),
        in_context=_flag(effective, "decision.in_context", default.in_context),
        model_version=_option(
            effective, "decision.model_version", tuple(MODEL_IDS), default.model_version
        ),
        grant_prepare=_option(effective, "decision.grant.prepare", GRANTS, default.grant_prepare),
        grant_run=_option(effective, "decision.grant.run", GRANTS, default.grant_run),
        grant_team=_option(effective, "decision.grant.team", GRANTS, default.grant_team),
        grant_look=_option(effective, "decision.grant.look", GRANTS, default.grant_look),
        grant_second_look=_option(
            effective, "decision.grant.second_look", GRANTS, default.grant_second_look
        ),
        grant_project_plan=_option(
            effective, "decision.grant.project_plan", GRANTS, default.grant_project_plan
        ),
        grant_check=_option(effective, "decision.grant.check", GRANTS, default.grant_check),
        grant_inbox_triage=_option(
            effective, "decision.grant.inbox_triage", GRANTS, default.grant_inbox_triage
        ),
        limit_drafts=int(
            _number(effective, "decision.limit.drafts", _LIMITS, default.limit_drafts)
        ),
        limit_chats=int(_number(effective, "decision.limit.chats", _LIMITS, default.limit_chats)),
        limit_changes=int(
            _number(effective, "decision.limit.changes", _LIMITS, default.limit_changes)
        ),
        plan_brake=_option(effective, "decision.plan_brake", PLAN_BRAKES, default.plan_brake),
        limit_team=int(_number(effective, "decision.limit.team", _LIMITS, default.limit_team)),
        team_handoffs=_flag(effective, "decision.team.handoffs", default.team_handoffs),
        limit_look=int(_number(effective, "decision.limit.look", _LIMITS, default.limit_look)),
        limit_second_look=int(
            _number(effective, "decision.limit.second_look", _LIMITS, default.limit_second_look)
        ),
        limit_project_plan=int(
            _number(effective, "decision.limit.project_plan", _LIMITS, default.limit_project_plan)
        ),
        limit_message_draft=int(
            _number(effective, "decision.limit.message_draft", _LIMITS, default.limit_message_draft)
        ),
        limit_check=int(_number(effective, "decision.limit.check", _LIMITS, default.limit_check)),
        limit_inbox_triage=int(
            _number(effective, "decision.limit.inbox_triage", _LIMITS, default.limit_inbox_triage)
        ),
        learning=_flag(effective, "decision.learning", default.learning),
        shadow=_option(effective, "decision.shadow", SHADOWS, default.shadow),
        launcher_session=_canonical_uuid(effective.get("decision.launcher_session")),
        quiet=_quiet(
            effective.get("notifications.quiet_start"), effective.get("notifications.quiet_end")
        ),
        never_send_invalid=never_send_invalid,
    )


def _flag(values: Mapping[str, object], key: str, default: bool) -> bool:
    value = values.get(key)
    return value if isinstance(value, bool) else default


def _option(values: Mapping[str, object], key: str, allowed: tuple[str, ...], default: str) -> str:
    value = values.get(key)
    return value if isinstance(value, str) and value in allowed else default


def _number(
    values: Mapping[str, object], key: str, allowed: tuple[float, ...], default: float
) -> float:
    """The listed number equal to the value (a bool is no number), else `default`."""
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return next((option for option in allowed if option == value), default)


def _instant(value: object) -> datetime | None:
    """An exact 24-character `YYYY-MM-DDTHH:MM:SS.mmmZ` stamp as an aware UTC datetime."""
    if not isinstance(value, str) or len(value) != 24 or not value.endswith("Z"):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    exact = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") == value
    return parsed if exact else None


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        return ""
    try:
        return value if str(uuid.UUID(value)) == value else ""
    except ValueError:
        return ""


def _never_send(values: Mapping[str, object]) -> tuple[tuple[str, ...], bool]:
    """The stored never-send entries and whether the value was unusable; missing means none."""
    if "decision.never_send" not in values:
        return (), False
    stored = values["decision.never_send"]
    try:
        entries = json.loads(stored) if isinstance(stored, str) else None
    except ValueError, RecursionError:
        entries = None
    if (
        not isinstance(entries, list)
        or len(entries) > _NEVER_SEND_ENTRIES
        or not all(_never_send_entry(entry) for entry in entries)
    ):
        return (), True
    return tuple(entries), False


def _never_send_entry(entry: object) -> bool:
    return (
        isinstance(entry, str)
        and 1 <= len(entry) <= _NEVER_SEND_CHARS
        and (entry.endswith("/") or entry.startswith("#"))
        and not any(unicodedata.category(ch) == "Cc" for ch in entry)
    )


def _quiet(start: object, end: object) -> tuple[str, str] | None:
    if (
        isinstance(start, str)
        and isinstance(end, str)
        and start in _QUIET_TIMES
        and end in _QUIET_TIMES
        and start != end
    ):
        return start, end
    return None


def quiet_until(quiet: tuple[str, str] | None, now: datetime) -> datetime | None:
    """When the quiet hours that hold `now` end, or None outside them or without them.

    `quiet` is a ("HH:MM", "HH:MM") pair read on `now`'s own wall clock, so pass a local time:
    the start minute is inside, the end minute is not, and an end before the start crosses
    midnight. Equal bounds mean no quiet hours.
    """
    if quiet is None or quiet[0] == quiet[1]:
        return None
    start, end = (int(bound[:2]) * 60 + int(bound[3:]) for bound in quiet)
    at = now.hour * 60 + now.minute
    inside = (start <= at < end) if start < end else (at >= start or at < end)
    if not inside:
        return None
    ends = now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    return ends if ends > now else ends + timedelta(days=1)
