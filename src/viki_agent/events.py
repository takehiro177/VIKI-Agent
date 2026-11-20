"""The browser contract (VIKI envelope v2): every event the server sends, and every message the
client may send. `frontend/src/protocol.ts` mirrors this file; tests/test_contract.py keeps the
two in step.

Session envelopes carry v, type, session_id, generation, seq and data. The server adds
subscription request_id at delivery. Global events have a separate launch generation.
`seq` starts at 1 when a session actor starts and orders everything the actor emits; history
pages travel outside it (`HistoryPage`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Literal

from viki_agent.conversations.artifacts import ArtifactResult, ArtifactStart
from viki_agent.turn_input import Attachment

VERSION = 2

TurnStatus = Literal["success", "cancelled", "error"]
ErrorCode = Literal[
    "cli_missing",
    "not_logged_in",
    "billing",
    # The process ended on its own: a nonzero exit, or an exit the app did not ask for, whose
    # stderr matched nothing more specific. Never a stop-kill and never a failed send (below).
    "crashed",
    # The app killed the process tree because a Stop could not be delivered (no system/init
    # before the pickup deadline) or was not acknowledged (interrupt timeout or an error reply).
    # The user asked for this: the turn ends `cancelled` first, and the next send resumes.
    "forced_stop",
    # A send never reached the process (a connection or process error while writing). Whether
    # the message arrived is unknown, so no card offers a resend.
    "provider_disconnected",
    # The transcript this session was resuming is gone (deleted or moved between the catalog's
    # check and the spawn). Separate from "crashed" because the card for a crash offers to send
    # the message again, and there is nothing left to resume: the retry can only fail the same
    # way. ErrorCard has no copy of its own for it, which is the contract for a code it does not
    # know — it shows this message, and no button.
    "transcript_missing",
    "too_many_sessions",
    "frame_error",
    "bad_request",
    "internal",
]


@dataclass(frozen=True, kw_only=True)
class Event:
    """Base class. Subclasses set TYPE and declare their data fields."""

    TYPE: ClassVar[str] = ""

    def data(self) -> dict[str, Any]:
        return asdict(self)

    def envelope(self, session_id: str, seq: int, generation: str = "") -> dict[str, Any]:
        return {
            "v": VERSION,
            "type": self.TYPE,
            "session_id": session_id,
            "seq": seq,
            "generation": generation,
            "data": self.data(),
        }


# --- nested shapes --------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Command:
    name: str  # without the leading slash
    description: str
    argument_hint: str = ""
    skill: bool = False  # a vault skill (.claude/skills/*); listed first in the / menu


@dataclass(frozen=True, kw_only=True)
class ModelOption:
    value: str  # what set_model / --model takes
    label: str
    description: str = ""


@dataclass(frozen=True, kw_only=True)
class QuestionOption:
    label: str
    description: str = ""


@dataclass(frozen=True, kw_only=True)
class Question:
    header: str
    question: str
    options: list[QuestionOption]
    multi_select: bool = False


@dataclass(frozen=True, kw_only=True)
class ToolRow:
    """One ToolBlock row: `head` renders orange, `rest` plain, `tail` dim (e.g. "→ 7 files").
    Built by the mapper from the tool's input."""

    head: str
    rest: str = ""
    tail: str = ""


@dataclass(frozen=True, kw_only=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True, kw_only=True)
class OtherSession:
    session_id: str
    entrypoint: str  # claude-vscode, cli, viki-agent, ...
    pid: int
    title: str = ""


# --- REST shapes (the sessions list and search; catalog.py builds them) ---------------------------


@dataclass(frozen=True, kw_only=True)
class SessionRow:
    """One transcript in the sessions list. Live state (streaming, needs you, VS Code, uncommitted)
    is not here: it comes from the running app, not the file."""

    session_id: str
    title: str  # custom title, else AI title, else the first prompt; one line
    first_prompt: str  # one line
    entrypoint: str  # of the first entry: claude-vscode, cli, viki-agent, sdk-py, ...
    modified: str  # ISO 8601 UTC of the newest entry (the client groups Today/Yesterday/Earlier)
    cli_version: str  # of the newest entry
    other: bool  # not VS Code, CLI or this app: listed under "Other (N)", never searched
    bot: bool = False  # Bot ownership recorded by the app; the sessions route adds it


@dataclass(frozen=True, kw_only=True)
class SearchHit:
    session_id: str
    title: str
    role: Literal["title", "user", "assistant"]  # where the query matched
    snippet: str  # one line around the match
    at: str  # the matching entry's timestamp; "" for a title


@dataclass(frozen=True, kw_only=True)
class SessionsResult:
    """The sessions list as an observation: `partial` when files went unread, `error`
    when the project folder itself could not be listed, or the recorded session roots could not
    be read (then only the vault's own rows travel). An unknown count is never zero."""

    sessions: list[SessionRow]
    state: Literal["ready", "partial", "error"]
    reason: Literal["", "time_budget", "read_failed", "project_unreadable"] = ""
    omitted: int = 0  # transcripts without a row because of `reason`; by-design skips never count


@dataclass(frozen=True, kw_only=True)
class SearchResult:
    """A search as an observation. `superseded` means a newer query replaced this one
    before it scanned; `partial` names the budget that stopped it."""

    hits: list[SearchHit]
    state: Literal["ready", "partial", "superseded", "error"]
    reason: Literal[
        "",
        "time_budget",
        "scan_budget",
        "file_budget",
        "response_budget",
        "listing_partial",
        "cancelled",
        "read_failed",
        "project_unreadable",
    ] = ""
    scanned: int = 0  # sessions whose transcript was scanned (whole or tail window)
    total: int = 0  # sessions eligible: listed and not `other`
    request_id: str = ""  # echoed by the server; "" when the client sent none


# --- REST shapes (the rails; rails.py builds them) ------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class AgentInfo:
    """One `.claude/agents/*.md` of the vault, with its real last run."""

    name: str
    description: str  # one line
    tools: list[str]
    model: str  # "" when the agent inherits the session's model
    last_run: str  # ISO 8601 UTC (…Z) of the newest subagent run with this agentType; "" if none


@dataclass(frozen=True, kw_only=True)
class GateRow:
    """Kept for the wire shape; nothing produces one."""

    file: str  # the retro file name, e.g. "2026-09-10.md"
    id: str  # the `#` cell of the retro row
    item: str  # the Item cell, markdown stripped, one line
    label: str  # "owner applies", else the Conf. cell, else ""
    owner_applies: bool  # the user edits the target by hand; the app opens the file instead


@dataclass(frozen=True, kw_only=True)
class Routine:
    when: str  # "07:30", "22:00", "Sun 18:00"
    name: str  # "/daily · morning brief"
    planned: bool  # True until a schedule exists


@dataclass(frozen=True, kw_only=True)
class VaultCounts:
    knowledge: int
    sources: int
    inbox: int


@dataclass(frozen=True, kw_only=True)
class GitChip:
    branch: str  # independently observed branch name, otherwise ""
    head: str  # independently observed short hash, otherwise ""
    committed_at: str  # HEAD's committer date as git prints %cI, otherwise ""
    dirty: int | None  # None when working-tree status could not be established
    state: Literal["known", "unknown", "not_repository"] = "unknown"
    ref_state: Literal["branch", "detached", "unknown"] = "unknown"


@dataclass(frozen=True, kw_only=True)
class Rails:
    agents: list[AgentInfo]
    gates: list[GateRow]  # always []
    routines: list[Routine]
    counts: VaultCounts
    git: GitChip
    counts_state: Literal["ready", "error", "unknown"] = "unknown"


# --- session lifecycle ----------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class CodeCopy:
    path: str
    folder: str
    branch: str


@dataclass(frozen=True, kw_only=True)
class SessionStarted(Event):
    TYPE: ClassVar[str] = "session.started"
    model: str
    mode: str
    effort: str
    commands: list[Command]
    agents: list[str]
    models: list[ModelOption]
    cli_version: str
    resumed: bool = False
    additional_folders: list[str] = field(default_factory=list)  # this spawn's --add-dir folders
    code_copy: CodeCopy | None = None

    def data(self) -> dict[str, Any]:
        data = super().data()
        if self.code_copy is None:
            del data["code_copy"]
        return data


@dataclass(frozen=True, kw_only=True)
class SessionBlocked(Event):
    """The transcript is live in another process; the client may fork instead."""

    TYPE: ClassVar[str] = "session.blocked"
    reason: Literal["live_elsewhere"]
    holder: OtherSession


@dataclass(frozen=True, kw_only=True)
class SessionClosed(Event):
    TYPE: ClassVar[str] = "session.closed"
    reason: Literal["requested", "browser_gone"]


@dataclass(frozen=True, kw_only=True)
class SessionOpenRefused(Event):
    """An open request could not safely attach to a session."""

    TYPE: ClassVar[str] = "session.open.refused"
    reason: Literal["unavailable", "storage_unavailable", "model_review_required"]


# --- turn and blocks ------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class UnavailableAttachment:
    """Native-history media that is deliberately not an app-owned asset reference."""

    name: str
    media_type: str
    byte_size: int
    reason: Literal["not_in_local_assets", "invalid_native_media", "too_large", "limit"]


@dataclass(frozen=True, kw_only=True)
class UserMessage(Event):
    TYPE: ClassVar[str] = "user.message"
    text: str
    command: str = ""  # "/name" when the text invoked a slash command, else ""
    at: str = ""  # ISO 8601 timestamp
    attachments: tuple[Attachment, ...] = ()
    unavailable_attachments: tuple[UnavailableAttachment, ...] = ()

    def data(self) -> dict[str, Any]:
        result: dict[str, Any] = {"text": self.text, "command": self.command, "at": self.at}
        if self.attachments:
            result["attachments"] = [attachment.data() for attachment in self.attachments]
        if self.unavailable_attachments:
            result["unavailable_attachments"] = [
                asdict(value) for value in self.unavailable_attachments
            ]
        return result


@dataclass(frozen=True, kw_only=True)
class TurnStarted(Event):
    TYPE: ClassVar[str] = "turn.started"
    turn_id: str


@dataclass(frozen=True, kw_only=True)
class TextDelta(Event):
    TYPE: ClassVar[str] = "text.delta"
    block_id: str
    text: str


@dataclass(frozen=True, kw_only=True)
class TextDone(Event):
    """Replaces every delta of `block_id` with the final text."""

    TYPE: ClassVar[str] = "text.done"
    block_id: str
    text: str


@dataclass(frozen=True, kw_only=True)
class ThinkingDelta(Event):
    TYPE: ClassVar[str] = "thinking.delta"
    block_id: str
    text: str


@dataclass(frozen=True, kw_only=True)
class ThinkingDone(Event):
    """Final summarized thinking; `text` may be empty ("Thought · 6s")."""

    TYPE: ClassVar[str] = "thinking.done"
    block_id: str
    text: str
    duration_ms: int


@dataclass(frozen=True, kw_only=True)
class ToolStarted(Event):
    TYPE: ClassVar[str] = "tool.started"
    block_id: str  # the tool_use id
    tool: str
    row: ToolRow
    parent_id: str = ""  # the Agent tool_use id when a subagent runs this tool
    artifact: ArtifactStart | None = None

    def data(self) -> dict[str, Any]:
        data = asdict(self)
        if self.artifact is None:
            data.pop("artifact")
        return data


@dataclass(frozen=True, kw_only=True)
class ToolDone(Event):
    TYPE: ClassVar[str] = "tool.done"
    block_id: str
    is_error: bool
    summary: str  # ≤120 chars
    preview: str  # ≤2 KB and ≤40 lines of the result, as text
    tail: str = ""  # replaces the row's tail once the result is known, e.g. "→ exit 2"
    artifact: ArtifactResult | None = None

    def data(self) -> dict[str, Any]:
        data = asdict(self)
        if self.artifact is None:
            data.pop("artifact")
        return data


@dataclass(frozen=True, kw_only=True)
class SubagentStarted(Event):
    """A RoutingRow: only from a main-thread Agent (or Workflow) tool_use. It stays open until
    subagent.done, which for a background agent comes with its task notification."""

    TYPE: ClassVar[str] = "subagent.started"
    block_id: str  # the Agent tool_use id
    agent: str  # subagent_type, or "workflow"
    model: str  # input.model, or "inherit"
    description: str  # input.description; for a Workflow, the name in its script's meta
    reason: str = ""  # the bracketed reason on VIKI's "▸ routing:" line, when present


@dataclass(frozen=True, kw_only=True)
class SubagentDone(Event):
    """The usage counts and summary come only from a task notification that carried valid ones;
    data() leaves out the unset ones, so any other close reads {block_id, status}."""

    TYPE: ClassVar[str] = "subagent.done"
    block_id: str
    status: Literal["completed", "failed", "stopped"]
    tool_uses: int | None = None
    total_tokens: int | None = None
    duration_ms: int | None = None
    summary: str = ""  # the subagent's own closing summary, ≤2000 chars

    def data(self) -> dict[str, Any]:
        data = {key: value for key, value in asdict(self).items() if value is not None}
        if not self.summary:
            data.pop("summary")
        return data


# --- steer (a message sent while a turn is already running) ---------------------------------------


@dataclass(frozen=True, kw_only=True)
class SteerPending(Event):
    """A message Claude Code has not read yet: the actor accepted it to inject into the running
    turn before the CLI confirms it started. `text` is the actor's bounded display copy."""

    TYPE: ClassVar[str] = "steer.pending"
    steer_id: str
    text: str
    command: str = ""
    images: int = 0
    at: str = ""


@dataclass(frozen=True, kw_only=True)
class SteerEnded(Event):
    TYPE: ClassVar[str] = "steer.ended"
    steer_id: str
    outcome: Literal["started", "dropped"]


# --- cards (keyed by tool_use_id; emitted only by the can_use_tool callback) ----------------------


@dataclass(frozen=True, kw_only=True)
class QuestionAsked(Event):
    TYPE: ClassVar[str] = "question.asked"
    tool_use_id: str
    questions: list[Question]


@dataclass(frozen=True, kw_only=True)
class PermissionRequested(Event):
    TYPE: ClassVar[str] = "permission.requested"
    tool_use_id: str
    kind: Literal["tool", "plan"]
    tool: str
    summary: str  # what the tool will do, as text
    # The card's headline, worded from the tool (mapper.tool_question). It is on the wire rather
    # than in the browser because it is the same per-tool knowledge the row grammar already
    # encodes, and the wording is a sentence per tool family ("May I read this web page?" for a
    # fetch) rather than one generic line. The card falls back to a generic sentence when this
    # is "" — an older frame, or a plan card.
    question: str = ""
    reason: str = ""
    agent: str = ""  # set when a subagent asks
    plan: str = ""  # markdown, for kind == "plan"


@dataclass(frozen=True, kw_only=True)
class RequestCancelled(Event):
    TYPE: ClassVar[str] = "request.cancelled"
    tool_use_id: str


@dataclass(frozen=True, kw_only=True)
class RequestAnswered(Event):
    """The user chose, and the card is resolved. The pair to RequestCancelled.

    The answer itself never travels: what they picked is between them and the CLI, and a second
    window has no business being told which button they pressed. Only the fact that the card is
    closed is public, because that is what every other viewer of this session needs.

    Without this the removal was client-local. The window that answered dropped the card
    immediately; every other view of the same session -- a reload, a replay from the actor log, a
    second window -- kept it until tool.done arrived with the same block_id. That is correct
    eventually and wrong meanwhile: a tool that runs for thirty seconds leaves an answered card on
    screen for thirty seconds, offering buttons that now do nothing, because driver.respond()
    returns False for a future already resolved. The user presses the button and the app does not
    react. Emitting this the moment the future resolves makes the invariant true at once instead
    of eventually.
    """

    TYPE: ClassVar[str] = "request.answered"
    tool_use_id: str


# --- status ---------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class PermissionDenied(Event):
    """Auto mode's classifier refused a tool (not a user's Deny)."""

    TYPE: ClassVar[str] = "permission.denied"
    tool: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class Hook(Event):
    """Only hooks that blocked or failed; successful hooks stay silent."""

    TYPE: ClassVar[str] = "hook"
    name: str  # e.g. "PreToolUse:Bash"
    outcome: Literal["blocked", "error"]
    message: str


@dataclass(frozen=True, kw_only=True)
class Status(Event):
    TYPE: ClassVar[str] = "status"
    state: str = ""  # "requesting", "compacting", ... ("" when only the mode changed)
    mode: str = ""  # the permission mode, when the CLI reports one


@dataclass(frozen=True, kw_only=True)
class Compacted(Event):
    TYPE: ClassVar[str] = "compacted"
    trigger: str = ""  # "manual" | "auto"
    pre_tokens: int = 0


@dataclass(frozen=True, kw_only=True)
class RateLimit(Event):
    TYPE: ClassVar[str] = "rate_limit"
    status: str  # "allowed" | "allowed_warning" | "rejected"
    kind: str = ""  # e.g. "five_hour"
    resets_at: int = 0  # epoch seconds


@dataclass(frozen=True, kw_only=True)
class VaultSessions(Event):
    """Who is live in the vault: other claude sessions whose cwd is the vault (the banner and
    Wrap up), and this app's own live sessions (a reloaded page opens any it does not track)."""

    TYPE: ClassVar[str] = "vault.sessions"
    others: list[OtherSession] = field(default_factory=list)
    live: list[str] = field(default_factory=list)  # session ids with a process in this app


# --- end of turn ----------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class TurnDone(Event):
    TYPE: ClassVar[str] = "turn.done"
    status: TurnStatus
    usage: Usage
    context_percent: int | None = None
    duration_ms: int = 0


@dataclass(frozen=True, kw_only=True)
class Error(Event):
    TYPE: ClassVar[str] = "error"
    code: ErrorCode
    message: str  # shown to the user
    recoverable: bool


def _all_events() -> dict[str, type[Event]]:
    found: dict[str, type[Event]] = {}
    pending = list(Event.__subclasses__())
    while pending:
        cls = pending.pop()
        found[cls.TYPE] = cls
        pending += cls.__subclasses__()
    return found


EVENTS: dict[str, type[Event]] = _all_events()


def history_item(event: Event) -> dict[str, Any]:
    """An event inside a history page: no session id, no seq."""
    return {"type": event.TYPE, "data": event.data()}


# --- client → server ------------------------------------------------------------------------------

# Every client message carries `type` and (except auth and open.*) `session_id`. Values are the
# required data keys; optional keys are listed after "?".
CLIENT_MESSAGES: dict[str, tuple[str, ...]] = {
    "auth": ("token", "v"),
    "session.open": (
        "session_id",
        "request_id",
        "?generation",
        "?after_seq",
        "?existing_only",
        "?expected_model",
    ),
    "history.more": ("session_id", "generation", "request_id", "?before_uuid"),
    "session.detach": ("session_id", "request_id"),
    "session.fork": ("session_id", "generation", "command_id", "fork_id"),
    "turn.send": ("session_id", "generation", "command_id", "text"),
    "turn.interrupt": ("session_id", "generation", "command_id"),
    "question.answer": (
        "session_id",
        "generation",
        "command_id",
        "tool_use_id",
        "native_request",
        "answers",
        "?response",
    ),
    "permission.respond": (
        "session_id",
        "generation",
        "command_id",
        "tool_use_id",
        "native_request",
        "allow",
    ),
    "session.set_model": ("session_id", "generation", "command_id", "model"),
    "session.set_effort": ("session_id", "generation", "command_id", "effort"),
    "session.set_mode": ("session_id", "generation", "command_id", "mode"),
    "session.close": ("session_id", "generation", "command_id", "?turn_id"),
    "open.url": ("url",),
    "open.vault": ("path",),
    "open.code": ("workspace_id", "path", "?line"),
    "open.bot_memory": ("workspace_id", "path"),
    "open.transcript": ("session_id",),
}
