"""Sessions as the browser sees them.

`SessionActor`: one chat session — a seq-numbered event log that reconnecting clients replay,
the turn lifecycle, and the claude process behind it. A new session's process starts eagerly;
a reopened one starts on the first send (after the live-elsewhere guard); an effort change
respawns at the next send; a crash is reported and the next send resumes. Nothing respawns
on its own. Commands for one session run one at a time, so a send never overtakes a start
that is still opening; interrupts and card answers never wait.

Replay and recent snapshot content are bounded by stream_state.py; current requests and state
are projected separately. Older turns come from transcript pages cut off at the fixed `since`.
Evicted live content is marked omitted rather than silently refilled across that boundary.

`SessionManager`: all actors, at most `max_live()` with a live process. Closing a session forgets
its actor, so reopening it reads the transcript afresh. The browser going away is
`settle(grace_s)` — every open card is denied (nobody is there to choose) and running turns get
up to the grace to end on their own — and then, unless a window came back meanwhile,
`close_all()`: every live session closes with reason browser_gone. A chat the `bind_retain`
predicate retains is left out of settle: its cards stay open and its turn is not waited for.
While one is live, `close_unretained()` first closes the others and `hold()` keeps the app,
denying nothing, until a window is back, no live chat is retained or its limit passes.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import sys
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import CLIConnectionError, ProcessError

from viki_agent import winproc
from viki_agent.app.diagnostics import Readiness
from viki_agent.conversations.stream_state import MAX_STEERS, STEER_TEXT_CHARS, StreamState
from viki_agent.drivers.base import (
    AgentDriver,
    Answer,
    ContextDriver,
    InventoryDriver,
    McpControlDriver,
    McpControlError,
    ModeDriver,
    PermissionAnswer,
    PermissionDetailsDriver,
    RunsDriver,
    RunStopError,
    SendError,
    StartError,
    StartFacts,
    StartFactsDriver,
    SteerDriver,
    TurnInputDriver,
)
from viki_agent.drivers.claude.control import PERMISSION_MODES, Launch
from viki_agent.drivers.claude.driver import (
    MSG_BILLING,
    MSG_CLI_MISSING,
    ClaudeDriver,
    DriverConfig,
)
from viki_agent.events import (
    CodeCopy,
    Error,
    Event,
    OtherSession,
    PermissionRequested,
    QuestionAsked,
    RequestAnswered,
    RequestCancelled,
    SessionBlocked,
    SessionClosed,
    SessionStarted,
    Status,
    SteerEnded,
    SteerPending,
    ToolDone,
    ToolStarted,
    TurnDone,
    TurnStarted,
    Usage,
    UserMessage,
)
from viki_agent.llm.connections import PREFIX, ConnectionError, Connections, split_model
from viki_agent.llm.gateway import Gateway
from viki_agent.profiles.profile_capabilities import steer_supported
from viki_agent.protection import ProtectionError
from viki_agent.state import MODEL_UNSPECIFIED, AppState, Prefs
from viki_agent.storage import StateError
from viki_agent.turn_input import (
    MAX_TEXT_BYTES,
    Attachment,
    AttachmentUnavailable,
    TurnInput,
    TurnInputError,
    claude_accepts,
    coerce,
)
from viki_agent.vaults.vault import VaultError, VaultService
from viki_agent.vaults.vault_excerpts import check_block_budget, joined_text, resolve_excerpts

log = logging.getLogger(__name__)

MAX_LIVE = 30  # the built-in default only; SessionManager.max_live() is the real admission limit
DENY_POLL_S = 1.0  # while the browser is gone, new cards are denied within this
CLOSE_TIMEOUT_S = 15.0  # a session stuck in its start handshake must not keep the app alive
# How long a close lets waiting steers settle: the driver config's default for an early stop.
STOP_FLUSH_S = DriverConfig.stop_flush_s
# Tools that can change the vault; a session that used one may have uncommitted work.
WRITE_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "Bash", "PowerShell"})
_COMMAND = re.compile(r"/([\w:.-]+)(?=\s|$)")


def msg_too_many(limit: int) -> str:
    return (
        f"You can run at most {limit} sessions at the same time. Close one, or raise the "
        "limit in Settings › Chat, then send again."
    )


MSG_TURN_RUNNING = "VIKI is responding. Stop it or wait for it to finish, then send again."
MSG_LOST = (
    "Couldn't send to Claude Code. There's no way to know whether it arrived. "
    "Check the send history."
)
MSG_NOT_SENT = (
    "This contains characters that can't be sent, so nothing was sent. "
    "Fix the text, then send again."
)
MSG_TURN_TOO_LARGE = "This message is too long, so nothing was sent. Shorten it, then send again."
MSG_ATTACHMENT_UNAVAILABLE = (
    "Couldn't load the attached image, so nothing was sent. "
    "You can review the text in Drafts and send history → Send history → Restore text. "
    "Remove that image, then send again."
)
MSG_ATTACHMENT_BUSY = (
    "Couldn't load the attached image right now, so nothing was sent. "
    "You can review the text in Drafts and send history → Send history → Restore text. "
    "Wait a moment, then send again."
)
MSG_MODEL = "Couldn't switch to this model."
MSG_MODE = "Couldn't switch the mode. The chat keeps its current mode."
MSG_REQUEST_LIMIT = (
    "This request is too long to show. It can't be allowed because it can't be reviewed."
)
MSG_START_FAILED = "Couldn't start the session. Try sending again."
MSG_COMMAND_UNCONFIRMED = "Can't confirm whether this was sent. Check the command record."
MSG_MODEL_NOT_SAVED = (
    "The model changed, but the setting couldn't be saved. "
    "The next time VIKI starts, it will use the last saved setting."
)
MSG_CLI_CONFIG_DIR = (
    "Claude Code can't start because CLAUDE_CONFIG_DIR is set. "
    "Remove this variable, then open VIKI Agent again."
)
MSG_CLI_SHIM = (
    "Claude Code can't start because VIKI_CLAUDE_CLI points to a .cmd/.bat wrapper. "
    "Point it at claude.exe itself, then open VIKI Agent again."
)


Envelope = dict[str, Any]
Subscriber = Callable[[Envelope], None]
CloseReason = Literal["requested", "browser_gone"]
DriverFactory = Callable[[Launch, Prefs, Callable[[Event], None]], AgentDriver]
LaunchGuard = Callable[[str, Prefs], Awaitable[None]]
StartedObserver = Callable[[SessionStarted], Awaitable[None]]
# A profile session's app MCP config: the absolute path of its file for this spawn, or None.
McpResolver = Callable[[str], Awaitable[str | None]]
# The folder a session's next spawn runs in, or None for the vault.
RootResolver = Callable[[str], Awaitable[Path | None]]
# Visible path context, revalidated for each send; None preserves single-folder turns.
RootContextResolver = Callable[[str], Awaitable[str | None]]
# The user's ticked folders a spawn without a session root adds (`--add-dir`), asked per spawn.
FolderResolver = Callable[[str], Awaitable[tuple[Path, ...]]]
# A Bot's private code copy, admitted after its generated memory settings for this spawn.
BotCodeResolver = Callable[[str, str], Awaitable[CodeCopy | None]]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- the ~/.claude/sessions registry (read-only) --------------------------------------------------


def _registry(claude_home: Path) -> list[dict[str, Any]]:
    entries = []
    for path in (claude_home / "sessions").glob("*.json"):
        try:
            # utf-8-sig, not utf-8: a BOM makes json.loads raise, and a dropped entry here reads
            # as "nobody else has this session open" — the one answer this registry must not
            # invent. rails.py strips the same BOM for the same reason.
            entry = json.loads(path.read_text(encoding="utf-8-sig"))
        except OSError, ValueError, RecursionError:
            continue
        pid = entry.get("pid") if isinstance(entry, dict) else None
        # type(pid) is int, not isinstance: isinstance(True, int) is True, and a bad pid (0,
        # negative, 2**31 or more, or a JSON true) makes the one ps call on macOS fail for
        # every pid, so a live holder would read as dead.
        if type(pid) is int and 0 < pid <= 0x7FFFFFFF:
            entries.append(entry)
    return entries


def _cwd_key(path: str, platform: str) -> str:
    """The comparison key of a registered `cwd`, per OS: Windows compares case-insensitively;
    macOS casefolds and NFC-normalizes (its filesystem is case-insensitive but keeps the
    original spelling, and can hand back NFD); Linux compares exactly."""
    if platform == "win32":
        return path.rstrip("\\/").lower()
    if platform == "darwin":
        return unicodedata.normalize("NFC", path.rstrip("/") or "/").casefold()
    return path.rstrip("/") or "/"


@functools.cache
def _pid_domain() -> str:
    """This process's pid domain, built as the Linux CLI builds the `pidDomain` it registers:
    the machine id, then the pid namespace, each empty when unreadable."""
    try:
        with open("/etc/machine-id", encoding="utf-8", errors="replace") as f:
            machine = f.read().strip()
    except OSError:
        machine = ""
    try:
        namespace = os.readlink("/proc/self/ns/pid")
    except OSError:
        namespace = ""
    return f"linux:{machine}:{namespace}"


def _alive(entry: dict[str, Any], starts: dict[int, int | str]) -> bool:
    """The registered pid is still that process: its creation time equals procStart."""
    started = starts.get(entry["pid"])
    recorded = entry.get("procStart")
    return started is not None and recorded is not None and str(started) == str(recorded)


def _live(entries: list[dict[str, Any]], own_pids: set[int], platform: str) -> list[dict[str, Any]]:
    """The entries that are another live process, in order. On Linux, an entry whose string
    `pidDomain` differs from ours was registered from another pid namespace (a Flatpak sandbox),
    so its pid means nothing here and it counts as live, as the CLI counts it. The cost, which
    the CLI pays too: until its file is deleted, a stale entry of that kind blocks resuming its
    one session and, when its cwd is the vault, stays in the vault's other-sessions list, so the
    vault banner names it, its row reads as open elsewhere and Wrap up stays disabled."""
    here = _pid_domain() if platform == "linux" else None
    foreign = [
        here is not None and isinstance(e.get("pidDomain"), str) and e["pidDomain"] != here
        for e in entries
    ]
    local = {e["pid"] for e, far in zip(entries, foreign, strict=True) if not far} - own_pids
    starts = winproc.process_start_times(local)
    return [
        e
        for e, far in zip(entries, foreign, strict=True)
        if far or (e["pid"] in local and _alive(e, starts))
    ]


def _other(entry: dict[str, Any]) -> OtherSession:
    return OtherSession(
        session_id=str(entry.get("sessionId", "")),
        entrypoint=str(entry.get("entrypoint", "")),
        pid=entry["pid"],
        title=str(entry.get("name") or ""),
    )


def find_holder(claude_home: Path, session_id: str, own_pids: set[int]) -> OtherSession | None:
    """Another live process that has `session_id` open, if any."""
    matching = [entry for entry in _registry(claude_home) if entry.get("sessionId") == session_id]
    live = _live(matching, own_pids, sys.platform)
    return _other(live[0]) if live else None


def vault_sessions(claude_home: Path, vault: Path, own_pids: set[int]) -> list[OtherSession]:
    """Live claude sessions, not ours, whose working directory is the vault."""
    here = _cwd_key(str(vault), sys.platform)
    matching = [
        entry
        for entry in _registry(claude_home)
        if _cwd_key(str(entry.get("cwd", "")), sys.platform) == here
    ]
    return [_other(entry) for entry in _live(matching, own_pids, sys.platform)]


# --- one session ----------------------------------------------------------------------------------


async def _finish_cleanup(operation: Awaitable[None]) -> None:
    """Keep a native close owned through caller cancellation, then propagate cancellation."""
    cleanup = asyncio.ensure_future(operation)
    cancelled: asyncio.CancelledError | None = None
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError as exc:
            cancelled = exc
    cleanup.result()
    if cancelled is not None:
        raise cancelled


class CommandDispatchRejected(RuntimeError):
    """A safe actor/coordinator admission error; the caller owns durable-phase recovery."""

    def __init__(
        self,
        code: Literal[
            "stale_generation",
            "closed",
            "turn_running",
            "no_target",
            "closing",
            "overloaded",
            "profile_locked",
            "runtime_changed",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


DispatchReason = Literal[
    "native_unconfirmed",
    "start_failed",
    "runtime_changed",
    "send_failed",
    "cancelled",
    "internal",
    "unsupported_attachment",
    "attachment_unavailable",
    "excerpt_with_command",
    "excerpt_wrong_vault",
    "excerpt_stale",
    "excerpt_unavailable",
    "excerpts_too_large",
]


@dataclass(frozen=True)
class CommandDispatchResult:
    status: Literal["duplicate", "not_dispatched", "unknown"]
    reason: DispatchReason | None = None


# What resolving a turn's excerpts can refuse, as a dispatch reason. Anything else from the vault
# is a defect here, not a refusal the client has copy for, so it ends the attempt as internal.
_EXCERPT_REASONS: Mapping[str, DispatchReason] = {
    "excerpt_wrong_vault": "excerpt_wrong_vault",
    "excerpt_stale": "excerpt_stale",
    "excerpt_unavailable": "excerpt_unavailable",
    "excerpts_too_large": "excerpts_too_large",
}


class SessionActor:
    def __init__(
        self,
        session_id: str,
        launch: Launch,
        *,
        state: AppState,
        make_driver: DriverFactory,
        admit: Callable[[SessionActor], bool],
        holder: Callable[[str], Awaitable[OtherSession | None]],
        max_live: Callable[[], int] = lambda: MAX_LIVE,
        registration_pending: bool = False,
        readiness: Readiness | None = None,
        launch_guard: Callable[[str, bool, Prefs], Awaitable[None]] | None = None,
        started_observer: StartedObserver | None = None,
        initialized: Callable[[str, StartFacts | None], Awaitable[None]] | None = None,
        profile: bool = False,
        mcp_resolver: McpResolver | None = None,
        bot_memory_resolver: McpResolver | None = None,
        bot_prompt_resolver: McpResolver | None = None,
        bot_code_resolver: BotCodeResolver | None = None,
        excerpt_vault: VaultService | None = None,
        active_vault_id: str = "",
        root_resolver: RootResolver | None = None,
        root_context: RootContextResolver | None = None,
        folder_resolver: FolderResolver | None = None,
        copy_uses: dict[AgentDriver, str] | None = None,
    ) -> None:
        self.session_id = session_id
        # Each driver made for a Bot code copy, with that copy's worktree id, from just before it
        # goes live until its own close completes. Shared with the manager, so the record
        # outlives this actor's removal from it (SessionManager.code_copy_in_use).
        self._copy_uses: dict[AgentDriver, str] = {} if copy_uses is None else copy_uses
        self._copy_closes: set[asyncio.Task[None]] = set()  # closes of exited copy drivers
        # The durable origin, read once when the manager made this actor: it never changes
        # while the actor exists, and a send or spawn must not pay a store read for it.
        self._profile = profile
        self._readiness = readiness
        self._launch_guard = launch_guard
        self._started_observer = started_observer
        self._initialized = initialized
        self._mcp_resolver = mcp_resolver
        self._bot_memory_resolver = bot_memory_resolver
        self._bot_prompt_resolver = bot_prompt_resolver
        self._bot_code_resolver = bot_code_resolver
        self._root_resolver = root_resolver
        self._root_context = root_context
        self._folder_resolver = folder_resolver
        # The bounded vault reader a dispatch resolves its excerpt parts through, and the window's
        # active vault, bound together by SessionManager.bind_excerpts. Without them a turn
        # carrying parts is refused rather than sent with its citations missing.
        self._excerpt_vault = excerpt_vault
        self._active_vault_id = active_vault_id
        self._generation = str(uuid.uuid4())
        self._launch = launch
        self._state = state
        self._make_driver = make_driver
        self._admit = admit
        self._max_live = max_live
        self._holder = holder
        self._registration_pending = registration_pending
        self._driver: AgentDriver | None = None
        # Identifies the current driver's events; a replaced driver's late events are dropped.
        self._token: object | None = None
        self._runtime_id = str(uuid.uuid4())
        self._turn_id = str(uuid.uuid4())
        self._native_requests: dict[str, dict[str, str]] = {}
        self._answered_requests: set[str] = set()
        self._closing_pids: set[int] = set()  # replaced processes still exiting: still ours
        self._lock = asyncio.Lock()
        self._idle_waiters: list[asyncio.Future[None]] = []  # resolved when a turn ends
        # Steers for the running turn, held from just before their write until the driver ends
        # them, in write order, each with the echo _emit_from shows once Claude Code reads it.
        self._steers: dict[str, UserMessage] = {}
        # A message was sent under the current launch, so its transcript exists and the next
        # process must --resume it; until then the same launch (new id or fork) can be reused.
        self._sent = False
        self._effort_pending = False
        # set_effort is intentionally synchronous in v1. Detect its successful writes across
        # durable-command callback awaits without changing that existing wire contract.
        self._configuration_revision = 0
        # Stop also cancels work awaiting acceptance/preflight before a driver exists.
        # Later explicit invocations capture the new revision and remain usable.
        self._interrupt_revision = 0
        # Set the moment close() takes the lock, so a command that was already waiting on that
        # lock does not wake up and start a fresh process under a session the manager is in the
        # middle of forgetting.
        self._closed = False
        self._seq = 0
        self._stream = StreamState()
        self._subscribers: set[Subscriber] = set()
        self._observer: Callable[[SessionActor, Envelope], None] | None = None
        self.wrote = False  # used a write-capable tool (with a dirty vault: uncommitted work)
        self.started: SessionStarted | None = None
        # When the first process started (ISO 8601, UTC). Transcript entries from then on are
        # in the log; history pages stop before it.
        self.since: str | None = None
        # The transcript that holds the history: a fork's source (its entries are copied into
        # the fork with the same uuids and timestamps, and the fork's own file appears late).
        self.history_source = launch.fork_of or session_id

    # --- queries

    @property
    def generation(self) -> str:
        """Identity of this actor; individual native runtimes and requests have separate IDs."""
        return self._generation

    def command_context(self) -> dict[str, str | bool]:
        """Read-only descriptor for the guarded REST consumer; never creates a process."""
        return {
            "session_id": self.session_id,
            "generation": self.generation,
            "closed": self._closed,
            "busy": self.busy,
            "live": self.live,
        }

    @property
    def live(self) -> bool:
        """A process is starting or running for this session."""
        return self._driver is not None

    async def inventory(self) -> dict[str, object] | None:
        """The live driver's inventory document, or None with no live driver that reports one.
        Takes no lock: a running turn never blocks it."""
        driver = self._driver
        if driver is None or not isinstance(driver, InventoryDriver):
            return None
        return await driver.inventory()

    async def context(self) -> dict[str, object] | None:
        """The live driver's context report, or None with no live driver that reports one.
        Takes no lock: a running turn never blocks it."""
        driver = self._driver
        if driver is None or not isinstance(driver, ContextDriver):
            return None
        return await driver.context()

    def requests(self) -> list[dict[str, object]]:
        """The live driver's pending requests, or [] with no live driver that reports them.
        Takes no lock: a running turn never blocks it."""
        driver = self._driver
        if driver is None or not isinstance(driver, RunsDriver):
            return []
        return driver.requests()

    async def stop_run(self, block_id: str) -> None:
        """Stop the run `block_id` names on the live driver.

        Raises RunStopError("not_running") with no live driver that reports runs; otherwise
        whatever the driver's own stop_run raises. Takes no lock: a running turn never blocks
        it, same as inventory() and requests()."""
        driver = self._driver
        if driver is None or not isinstance(driver, RunsDriver):
            raise RunStopError("not_running")
        await driver.stop_run(block_id)

    async def set_mcp_server(self, name: str, enabled: bool) -> None:
        """Switch the MCP server `name` on or off on the live driver.

        Raises McpControlError("not_running") with no live driver that reports the capability;
        otherwise whatever the driver's own set_mcp_server raises. Takes no lock, same as
        stop_run(): a start holding the actor's lock does not block it."""
        driver = self._driver
        if driver is None or not isinstance(driver, McpControlDriver):
            raise McpControlError("not_running")
        await driver.set_mcp_server(name, enabled)

    async def reconnect_mcp_server(self, name: str) -> None:
        """Reconnect the MCP server `name` on the live driver; raises as set_mcp_server() does."""
        driver = self._driver
        if driver is None or not isinstance(driver, McpControlDriver):
            raise McpControlError("not_running")
        await driver.reconnect_mcp_server(name)

    @property
    def busy(self) -> bool:
        """A turn runs, or a steer waits for the driver to end it."""
        return self._driver is not None and (self._driver.busy or bool(self._steers))

    @property
    def waiting(self) -> bool:
        """A card waits for an answer: the driver exists and lists at least one as pending."""
        return self._driver is not None and bool(self._driver.pending())

    @property
    def transcript_expected(self) -> bool:
        """The CLI should already have written this session's transcript: a resumed session, a
        fork (its history is the source's file) or a session whose process was dropped after a
        send, which re-arms a resume. False for a brand-new session during its first process:
        the file appears on the first turn, and a reload racing that first write must not read
        as a missing transcript, so `_sent` is deliberately not consulted."""
        return bool(self._launch.resume or self._launch.fork_of is not None)

    @property
    def pids(self) -> set[int]:
        """This session's claude processes: the current one and any still exiting."""
        pid = self._driver.pid if self._driver else None
        return self._closing_pids | ({pid} if pid is not None else set())

    @property
    def seq(self) -> int:
        return self._seq

    def events_after(self, seq: int) -> list[Envelope]:
        """What a (re)connecting client missed. Deltas of finished blocks are compacted away,
        so a gap inside one yields the block's `*.done` instead."""
        return self._stream.events_after(seq)

    @property
    def replay_floor(self) -> int:
        return self._stream.replay_floor

    def recovery(self, generation: str | None, after_seq: int) -> list[Envelope]:
        """Capture recovery without awaiting; subscribe and enqueue its barrier in this step."""
        if generation == self.generation and self.replay_floor <= after_seq <= self.seq:
            return self.events_after(after_seq)
        snapshot = self._stream.snapshot()
        snapshot["state"]["live"] = self.live
        snapshot["state"]["max_live_sessions"] = self._max_live()
        return [
            {
                "v": 2,
                "type": "session.snapshot",
                "session_id": self.session_id,
                "generation": self.generation,
                "seq": self.seq,
                "data": snapshot,
            }
        ]

    def _guard(self, expected_generation: str | None, current: Callable[[], bool] | None) -> None:
        if expected_generation is not None and expected_generation != self.generation:
            raise CommandDispatchRejected("stale_generation")
        if current is not None and not current():
            raise CommandDispatchRejected("no_target")
        if expected_generation is not None and self._closed:
            raise CommandDispatchRejected("closed")

    def _guard_profile_controls(self) -> None:
        if self._profile:
            raise CommandDispatchRejected("profile_locked")

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        self._subscribers.add(subscriber)
        return lambda: self._subscribers.discard(subscriber)

    # --- commands

    def register(
        self, *, model: str | None = None, expected_model: object = MODEL_UNSPECIFIED
    ) -> None:
        """Persist a new/forked session before it may start a native process.

        A failed registration keeps its actor and original launch for an explicit retry;
        no transcript exists yet, so treating that retry as a resume would be wrong.
        """
        if not self._registration_pending:
            if (
                expected_model is not MODEL_UNSPECIFIED
                and self._state.launch_prefs(self.session_id).model != expected_model
            ):
                raise StateError("model_preview_changed")
            return
        changes = {}
        if self._launch.fork_of:
            source = (
                self._state.launch_prefs(self.history_source)
                if model is None
                else self._state.prefs(self.history_source)
            )
            changes = asdict(source)
        if model is not None:
            changes["model"] = model
        if expected_model is MODEL_UNSPECIFIED:
            self._state.remember(self.session_id, **changes)
        else:
            self._state.remember(self.session_id, expected_model=expected_model, **changes)
        self._registration_pending = False

    def report_state_error(self, error: StateError, *, message: str | None = None) -> Envelope:
        """Keep storage failures on this actor's replay stream, including before startup."""
        return self._emit(
            Error(code="internal", message=message or error.message, recoverable=True)
        )

    async def start(
        self,
        *,
        expected_generation: str | None = None,
        current: Callable[[], bool] | None = None,
    ) -> bool:
        """Make sure a claude process runs for this session. Emits session.started,
        session.blocked or error; returns whether a process is live."""
        interrupt_revision = self._interrupt_revision
        async with self._lock:
            self._guard(expected_generation, current)
            result = await self._start(interrupt_revision=interrupt_revision)
            self._guard(expected_generation, current)
            return result

    async def _start(self, *, interrupt_revision: int, explicit_send: bool = False) -> bool:
        if self._closed or self._interrupt_revision != interrupt_revision:
            # A closed session or an invocation captured before Stop cannot start a process.
            return False
        if self.live:
            # A running process keeps the launch it was checked with: drift is
            # checked before a spawn, and the live session's own menu stays authoritative.
            return True
        mcp: str | None = None
        bot_memory: str | None = None
        bot_prompt: str | None = None
        code_copy: CodeCopy | None = None
        configuration = self._configuration_revision
        try:
            self.register()
            prefs = self._state.launch_prefs(self.session_id)
        except StateError as exc:
            self.report_state_error(exc)
            return False
        if self._launch.resume and (holder := await self._holder(self.session_id)):
            self._emit(SessionBlocked(reason="live_elsewhere", holder=holder))
            return False
        cwd: Path | None = None
        if self._root_resolver is not None:
            # Asked again for every spawn and kept only in this launch, like the MCP config.
            try:
                cwd = await self._root_resolver(self.session_id)
            except StateError as exc:
                self.report_state_error(exc)
                return False
            except StartError as exc:
                self._start_refused(exc)
                return False
        add_dirs: tuple[str, ...] = ()
        if (
            cwd is None
            and self._folder_resolver is not None
            and not self._profile
            and not self._launch.read_only_tools
        ):
            # Asked again for every spawn and kept only in this launch, like the cwd; only without
            # one, so a worktree spawn keeps no await between its root check and going live.
            try:
                add_dirs = tuple(str(p) for p in await self._folder_resolver(self.session_id))
            except StateError as exc:
                self.report_state_error(exc)
                return False
        if self._interrupt_revision != interrupt_revision:
            return False
        if self._profile:
            # Holder and root preparation can await a revocation. Check the pinned launch and
            # resolve its generated configs after that preparation, for this spawn only.
            try:
                if self._launch_guard is None:
                    raise StartError("bad_request", "This profile session cannot be launched yet.")
                await self._launch_guard(self.session_id, explicit_send, prefs)
                if self._interrupt_revision != interrupt_revision:
                    return False
                if self._mcp_resolver is not None:
                    mcp = await self._mcp_resolver(self.session_id)
                    if self._interrupt_revision != interrupt_revision:
                        return False
                if self._bot_memory_resolver is not None:
                    bot_memory = await self._bot_memory_resolver(self.session_id)
                    if self._interrupt_revision != interrupt_revision:
                        return False
                if bot_memory is not None and self._bot_prompt_resolver is not None:
                    bot_prompt = await self._bot_prompt_resolver(self.session_id)
                    if self._interrupt_revision != interrupt_revision:
                        return False
            except StateError as exc:
                self.report_state_error(exc)
                return False
            except StartError as exc:
                self._start_refused(exc)
                return False
        if self._configuration_revision != configuration:
            # set_effort takes no lock, so it can land in the awaits above: spawn with what the
            # store holds now, not the prefs read before them, before any Bot-copy preparation.
            try:
                prefs = self._state.launch_prefs(self.session_id)
            except StateError as exc:
                self.report_state_error(exc)
                return False
        if not self._admit(self):
            self._emit(
                Error(
                    code="too_many_sessions",
                    message=msg_too_many(self._max_live()),
                    recoverable=True,
                )
            )
            return False
        if (
            self._bot_code_resolver is not None
            and self._profile
            and bot_memory is not None
            and cwd is None
            and self._launch.fork_of is None
            and not self._launch.read_only_tools
        ):
            try:
                # The resolver's final blocked/live-copy check runs on this loop. Nothing
                # awaits between its return and making the driver live below.
                code_copy = await self._bot_code_resolver(self.session_id, bot_memory)
                if self._closed or self._interrupt_revision != interrupt_revision:
                    return False
                current_prefs = self._state.launch_prefs(self.session_id)
                if current_prefs != prefs:
                    raise StartError(
                        "bad_request", "This profile launch has changed. Open a new chat."
                    )
            except StateError as exc:
                self.report_state_error(exc)
                return False
            except StartError as exc:
                self._start_refused(exc)
                return False
            if not self._admit(self):
                self._emit(
                    Error(
                        code="too_many_sessions",
                        message=msg_too_many(self._max_live()),
                        recoverable=True,
                    )
                )
                return False
        if self.since is None:
            self.since = now_iso()
        self._effort_pending = False  # the prefs as they stand now are what this process runs with
        token = self._token = object()
        self._runtime_id = str(uuid.uuid4())
        self._turn_id = str(uuid.uuid4())
        try:
            # Building the driver belongs inside the start, not one line above it: build_argv
            # refuses a mode or effort it will not spawn with, and the transport and control glue
            # are built here too. Outside this seam any of that raised past send() into the
            # server's fire-and-forget dispatch task, where it became one WARNING — no
            # user.message, no turn.started, no card — while the composer had already cleared
            # the user's message.
            launch = replace(
                self._launch,
                mcp_config=mcp or None,
                cwd=cwd,
                add_dirs=add_dirs,
                bot_memory_settings=bot_memory,
                code_worktree=code_copy.path if code_copy is not None else None,
                append_system_prompt=bot_prompt,
            )
            driver = self._make_driver(launch, prefs, lambda e: self._emit_from(token, e))
            if code_copy is not None:
                # WorktreeService.create names each copy's folder by its worktree id.
                self._copy_uses[driver] = Path(code_copy.path).name
            self._driver = driver  # counts as live while it starts, so the limit holds
        except StartError as exc:
            # The launch refuses this spawn (no usable CLI, or a billing problem the preflight
            # already found): no process exists, so this is a plain start refusal, not the
            # "could not build a driver" fault below.
            self._token = None
            self._start_refused(exc)
            return False
        except Exception:
            self._token = None  # assigned above; without a driver to own it, live must stay False
            log.warning("could not build a driver for %s", self.session_id, exc_info=True)
            self._emit(Error(code="internal", message=MSG_START_FAILED, recoverable=True))
            return False
        try:
            started = await driver.open()
        except asyncio.CancelledError:
            await self._retire_captured(driver, token)
            raise
        except StartError as exc:
            # ClaudeDriver.open closes its own process before it raises StartError.
            self._copy_uses.pop(driver, None)
            if self._interrupt_revision != interrupt_revision:
                await self._retire_captured(driver, token)
                return False
            if self._driver is driver and self._token is token:
                self._drop_driver()
            self._start_refused(exc)
            return False
        except Exception:
            await self._retire_captured(driver, token)
            raise
        if (
            self._driver is not driver
            or self._token is not token
            or self._interrupt_revision != interrupt_revision
        ):
            # A terminal event can invalidate this runtime while open awaits SDK setup. A
            # late successful return must neither announce readiness nor orphan its process.
            await self._retire_captured(driver, token)
            return False
        if not driver.alive:
            await self._retire(finish_cleanup=True)
            return False
        if self._profile and self._initialized is not None:
            try:
                # What Claude Code itself reported when it started, read before session.started
                # or any send and handed to the hook; None from a driver that reports none.
                facts: StartFacts | None = None
                if isinstance(driver, StartFactsDriver):
                    facts = await driver.start_facts()
                    # The read awaits the CLI too, so the two checks after open() run again.
                    if (
                        self._driver is not driver
                        or self._token is not token
                        or self._interrupt_revision != interrupt_revision
                    ):
                        await self._retire_captured(driver, token)
                        return False
                    if not driver.alive:
                        await self._retire(finish_cleanup=True)
                        return False
                await self._initialized(self.session_id, facts)
            except asyncio.CancelledError:
                await self._retire_captured(driver, token)
                raise
            except StateError as exc:
                await self._retire_captured(driver, token)
                self.report_state_error(exc)
                return False
            except StartError as exc:
                await self._retire_captured(driver, token)
                self._start_refused(exc)
                return False
            except Exception:
                await self._retire_captured(driver, token)
                raise
        if (
            self._driver is not driver
            or self._token is not token
            or self._interrupt_revision != interrupt_revision
        ):
            await self._retire_captured(driver, token)
            return False
        started = replace(started, additional_folders=list(add_dirs), code_copy=code_copy)
        self.started = started
        if self._readiness is not None and not started.model.startswith(PREFIX):
            # open() returned: the driver's billing gate accepted the initialize reply's account
            # block as a first-party subscription login, or the user allowed other sign-in
            # methods and the gate was skipped (a first turn's authentication failure then
            # reports signed_out, in _track). The block itself never leaves the driver.
            self._readiness.observe_account({"apiProvider": "firstParty"})
        self._emit(started)
        if (
            self._started_observer is not None
            and not self._profile
            and not started.model.startswith(PREFIX)
        ):
            # Only an ordinary start records the menu: a profile session's own start would
            # re-key the catalog it is bound to and refuse itself at the next spawn.
            try:
                await self._started_observer(started)
            except asyncio.CancelledError:
                await self._retire_captured(driver, token)
                raise
            except Exception:
                # Evidence caching is best effort; never log provider labels or fail a turn.
                log.warning("provider catalog observation unavailable")
        if self._interrupt_revision != interrupt_revision:
            await self._retire_captured(driver, token)
            return False
        return True

    def _start_refused(self, error: StartError) -> None:
        """Report a start that never produced a running process, and let readiness learn from
        it: `not_logged_in` and `billing` are launch facts the shell shows, not just this
        session's error."""
        if self._readiness is not None:
            self._readiness.observe_start_error(error.code)
        self._emit(
            Error(
                code=error.code,
                message=error.message,
                recoverable=error.code not in {"billing", "frame_error"},
            )
        )

    async def send(
        self,
        text: str,
        *,
        expected_generation: str | None = None,
        current: Callable[[], bool] | None = None,
    ) -> bool | None:
        """The WebSocket's text-only turn; typed turns with attachments go through
        send_command.

        Returns True when the text was handed to the driver (not proof of native pickup), False
        when certainly nothing reached Claude Code, and None when that cannot be known because
        the write failed partway. Both False and None are falsy for callers that only test
        whether the turn was handed off."""
        interrupt_revision = self._interrupt_revision
        try:
            turn = TurnInput(text)
        except TurnInputError:
            # Refused before anything reached Claude Code: say so, not "cannot tell".
            self._emit(Error(code="bad_request", message=MSG_NOT_SENT, recoverable=True))
            return False
        async with self._lock:
            self._guard(expected_generation, current)
            if self._interrupt_revision != interrupt_revision:
                return False
            if self.busy:
                if expected_generation is not None:
                    raise CommandDispatchRejected("turn_running")
                self._emit(Error(code="bad_request", message=MSG_TURN_RUNNING, recoverable=True))
                return False
            if self._effort_pending and self.live:
                await self._retire(finish_cleanup=expected_generation is not None)
                self._guard(expected_generation, current)
            opened_here = not self.live
            if not await self._start(interrupt_revision=interrupt_revision, explicit_send=True):
                if expected_generation is not None:
                    raise CommandDispatchRejected("no_target")
                return False
            self._guard(expected_generation, current)
            assert self._driver is not None
            driver, token = self._driver, self._token
            configuration = self._configuration_revision
            try:
                sent = await self._with_root_context(turn.text)
            except StateError as exc:
                self.report_state_error(exc)
                return False
            except StartError as exc:
                self._start_refused(exc)
                return False
            finally:
                if self._interrupt_revision != interrupt_revision and opened_here:
                    await self._retire_captured(driver, token)
            self._guard(expected_generation, current)
            if self._interrupt_revision != interrupt_revision:
                return False
            if (
                self._driver is not driver
                or self._token is not token
                or self._configuration_revision != configuration
                or not driver.alive
            ):
                raise CommandDispatchRejected("stale_generation")
            if self.busy:
                raise CommandDispatchRejected("turn_running")
            command, rest = self._command(sent)
            self._emit(UserMessage(text=rest, command=command, at=now_iso()))
            self._emit(TurnStarted(turn_id=str(uuid.uuid4())))
            self._guard(expected_generation, current)
            if (
                self._driver is not driver
                or self._token is not token
                or self._interrupt_revision != interrupt_revision
            ):
                raise CommandDispatchRejected("stale_generation")
            try:
                await driver.send(sent)
            except SendError as exc:
                # The provider may already have emitted its terminal error while send awaited
                # stdin. Preserve that receipt; otherwise retire before emitting exactly one.
                if self._driver is driver:
                    await self._retire()
                    self._emit(TurnDone(status="error", usage=Usage()))
                    self._emit(Error(code=exc.code, message=exc.message, recoverable=False))
                return False if exc.unsent else None
            except CLIConnectionError, ProcessError:
                await self._retire()
                self._emit(TurnDone(status="error", usage=Usage()))
                # The write failed on the pipe, so nothing proves whether the text
                # reached Claude Code; the code says so and no retry is offered.
                self._emit(Error(code="provider_disconnected", message=MSG_LOST, recoverable=False))
                return None
            self._sent = True  # the transcript exists now: a later process must --resume it
            return True  # invocation returned; this is not evidence of native pickup

    async def send_command(
        self,
        *,
        expected_generation: str,
        accept: Callable[[], Awaitable[str | TurnInput | None]],
        dispatch_intent: Callable[[], Awaitable[None]],
    ) -> CommandDispatchResult:
        """Commit acceptance and dispatch intent before one guarded native send attempt.

        accept returns the immutable committed turn (or a legacy text string), or None for an
        already-recorded command.
        Callbacks own persistence and may raise. Cancellation propagates to the coordinator,
        which reconciles its durable phase; a query return never establishes native receipt.
        While a chat is busy on a `SteerDriver`, the attempt is written into the running turn as
        a steer (`SteerPending`), held until the driver's `SteerEnded` for it. A profile chat
        steers only when the Claude Code version it started with (`self.started.cli_version`:
        the version its initialize reply names, else the launch check's) has steer evidence
        (`steer_supported`); a busy one without it is refused as on a driver that cannot steer:
        `turn_running` at entry, `not_dispatched`/`runtime_changed` once accepted.
        """
        interrupt_revision = self._interrupt_revision

        def may_steer() -> bool:
            return not self._profile or (
                self.started is not None and steer_supported(self.started.cli_version)
            )

        async with self._lock:
            if expected_generation != self.generation:
                raise CommandDispatchRejected("stale_generation")
            if self._closed:
                raise CommandDispatchRejected("closed")
            if self._interrupt_revision != interrupt_revision:
                return CommandDispatchResult("not_dispatched", "runtime_changed")
            # A profile chat steers only on a Claude Code version with recorded steer evidence:
            # the one it started with (`self.started`).
            if self.busy and (not may_steer() or not isinstance(self._driver, SteerDriver)):
                raise CommandDispatchRejected("turn_running")
            if len(self._steers) >= MAX_STEERS:
                raise CommandDispatchRejected("turn_running")
            configuration = self._configuration_revision

            def unchanged(driver: AgentDriver | None, token: object | None) -> bool:
                return (
                    not self._closed
                    and self.generation == expected_generation
                    and self._configuration_revision == configuration
                    and self._interrupt_revision == interrupt_revision
                    and self._driver is driver
                    and self._token is token
                    # A steer driver takes the turn busy or not: the write step decides which. A
                    # profile chat without steer evidence must be idle, so no steer decision below
                    # (each one directly follows this check) picks it.
                    and ((isinstance(driver, SteerDriver) and may_steer()) or not self.busy)
                )

            driver, token = self._driver, self._token
            accepted = await accept()
            if accepted is None:
                return CommandDispatchResult("duplicate")
            try:
                turn = coerce(accepted)
            except TurnInputError:
                return CommandDispatchResult("not_dispatched", "internal")
            if not turn.sendable:
                return CommandDispatchResult("not_dispatched", "internal")
            if not claude_accepts(turn):
                return CommandDispatchResult("not_dispatched", "unsupported_attachment")
            if not unchanged(driver, token):
                return CommandDispatchResult("not_dispatched", "runtime_changed")
            # A steer joins the turn its process runs: a pending effort waits for the next turn.
            steering = self.busy and isinstance(driver, SteerDriver)
            try:
                if self._effort_pending and self.live and not steering:
                    await self._retire(finish_cleanup=True)
                    if not unchanged(None, None):
                        return CommandDispatchResult("not_dispatched", "runtime_changed")
                opened_here = not self.live
                if not steering and not await self._start(
                    interrupt_revision=interrupt_revision, explicit_send=True
                ):
                    return CommandDispatchResult("not_dispatched", "start_failed")
            except Exception:
                return CommandDispatchResult("not_dispatched", "internal")

            driver, token = self._driver, self._token
            if driver is None or not driver.alive or not unchanged(driver, token):
                return CommandDispatchResult("not_dispatched", "runtime_changed")
            if turn.attachments and not isinstance(driver, TurnInputDriver):
                return CommandDispatchResult("not_dispatched", "unsupported_attachment")
            sent = turn.text
            if turn.parts:
                if self._excerpt_vault is None:
                    return CommandDispatchResult("not_dispatched", "excerpt_unavailable")
                if self._command(turn.text)[0]:
                    # A slash command's text is the command's own argument, so a quoted note
                    # would reach the command, not the model.
                    return CommandDispatchResult("not_dispatched", "excerpt_with_command")
                try:
                    # One bounded note read per part, off the loop: the actor lock is held.
                    blocks = await asyncio.to_thread(
                        resolve_excerpts, self._excerpt_vault, self._active_vault_id, turn.parts
                    )
                    check_block_budget(turn.text, blocks)
                except VaultError as exc:
                    # Refused before the echo and before any provider write: nothing of this turn
                    # was emitted and nothing was sent, so the attempt is certainly not_dispatched.
                    return CommandDispatchResult(
                        "not_dispatched", _EXCERPT_REASONS.get(exc.code, "internal")
                    )
                finally:
                    if self._interrupt_revision != interrupt_revision and opened_here:
                        await self._retire_captured(driver, token)
                if not driver.alive or not unchanged(driver, token):
                    return CommandDispatchResult("not_dispatched", "runtime_changed")
                # The user's words first: the stream's clip can only eat the citations' end.
                sent = joined_text(turn.text, blocks)
            try:
                sent = await self._with_root_context(sent)
            except StateError as exc:
                self.report_state_error(exc)
                return CommandDispatchResult("not_dispatched", "start_failed")
            except StartError as exc:
                self._start_refused(exc)
                return CommandDispatchResult("not_dispatched", "start_failed")
            finally:
                if self._interrupt_revision != interrupt_revision and opened_here:
                    await self._retire_captured(driver, token)
            if not driver.alive or not unchanged(driver, token):
                return CommandDispatchResult("not_dispatched", "runtime_changed")
            try:
                await dispatch_intent()
            finally:
                if self._interrupt_revision != interrupt_revision and opened_here:
                    await self._retire_captured(driver, token)
            if not driver.alive or not unchanged(driver, token):
                # Intent can be committed without entering native send. This observation is
                # stronger than a coordinator's conservative unknown after interruption.
                return CommandDispatchResult("not_dispatched", "runtime_changed")

            entered = False
            sent_before, launch_before = self._sent, self._launch
            # A failed write ends the turn it opened. A failed steer opened none: it ends a turn
            # only when its driver runs one as the failure is handled (driver.busy, read before
            # the retire). With only steers waiting, the last turn has already ended.
            steer_id: str | None = None
            try:
                command, rest = self._command(sent)
                echo = UserMessage(
                    text=rest, command=command, at=now_iso(), attachments=turn.attachments
                )
                if isinstance(driver, SteerDriver) and self.busy:
                    if command:
                        # How Claude Code handles a slash command folded into a running turn is
                        # unverified, so none is steered: nothing is emitted or written.
                        return CommandDispatchResult("not_dispatched", "runtime_changed")
                    # Held with its echo until the driver ends it (_emit_from). Only the pending
                    # copy is bounded: the driver gets the whole text and its images.
                    steer_id = str(uuid.uuid4())
                    self._steers[steer_id] = echo
                    self._emit(
                        SteerPending(
                            steer_id=steer_id,
                            text=rest[:STEER_TEXT_CHARS],
                            command=command,
                            images=len(turn.attachments),
                            at=echo.at,
                        )
                    )
                    entered = True
                    self._sent = True  # as for a turn: the transcript may exist from here on
                    await driver.steer(replace(turn, text=sent, parts=()), steer_id)
                    return CommandDispatchResult("unknown", "native_unconfirmed")
                self._emit(echo)
                self._emit(TurnStarted(turn_id=str(uuid.uuid4())))
                if not driver.alive or not unchanged(driver, token):
                    return CommandDispatchResult("not_dispatched", "runtime_changed")
                entered = True
                # From this point the transcript may exist. Never reuse the fresh/fork launch
                # after a drop merely because query() did not return; this is not an ack.
                self._sent = True
                # Keep text-only drivers and existing fake adapters source-compatible. The typed
                # value reaches the provider exactly when it carries attachment blocks.
                if turn.attachments and isinstance(driver, TurnInputDriver):
                    # Resolved excerpts and folder context travel in the same text block beside
                    # the images. A turn without augmentation keeps its original typed value.
                    outgoing = (
                        replace(turn, text=sent, parts=())
                        if turn.parts or sent != turn.text
                        else turn
                    )
                    await driver.send_turn(outgoing)
                else:
                    await driver.send(sent)
            except AttachmentUnavailable as exc:
                # Refused before any provider write: the process is fine and keeps running, and
                # no transcript came of this attempt, so a later relaunch must not --resume it.
                # A crash during the read already re-armed the resume: undo that too.
                self._sent = sent_before
                if not sent_before:
                    self._launch = launch_before
                if steer_id is not None and self._steers.pop(steer_id, None) is not None:
                    # Not when a driver drop already ended it; the running turn itself goes on.
                    self._emit(SteerEnded(steer_id=steer_id, outcome="dropped"))
                if self._driver is driver:
                    message = MSG_ATTACHMENT_BUSY if exc.transient else MSG_ATTACHMENT_UNAVAILABLE
                    if steer_id is None:
                        self._emit(TurnDone(status="error", usage=Usage()))
                    self._emit(Error(code="bad_request", message=message, recoverable=True))
                return CommandDispatchResult("not_dispatched", "attachment_unavailable")
            except asyncio.CancelledError:
                if self._driver is driver:
                    running = steer_id is None or driver.busy
                    await self._retire(finish_cleanup=True)
                    if running:
                        self._emit(TurnDone(status="error", usage=Usage()))
                    self._emit(
                        Error(code="internal", message=MSG_COMMAND_UNCONFIRMED, recoverable=False)
                    )
                raise
            except SendError as exc:
                if self._driver is driver:
                    running = steer_id is None or driver.busy
                    await self._retire(finish_cleanup=True)
                    if running:
                        self._emit(TurnDone(status="error", usage=Usage()))
                    self._emit(Error(code=exc.code, message=exc.message, recoverable=False))
                return CommandDispatchResult("unknown", "send_failed")
            except CLIConnectionError, ProcessError:
                if self._driver is driver:
                    running = steer_id is None or driver.busy
                    await self._retire(finish_cleanup=True)
                    if running:
                        self._emit(TurnDone(status="error", usage=Usage()))
                    self._emit(
                        Error(
                            code="provider_disconnected",
                            message=MSG_COMMAND_UNCONFIRMED,
                            recoverable=False,
                        )
                    )
                return CommandDispatchResult("unknown", "send_failed")
            except Exception:
                if self._driver is driver:
                    running = steer_id is None or driver.busy
                    await self._retire(finish_cleanup=True)
                    if running:
                        self._emit(TurnDone(status="error", usage=Usage()))
                    self._emit(
                        Error(code="internal", message=MSG_COMMAND_UNCONFIRMED, recoverable=False)
                    )
                return CommandDispatchResult("unknown" if entered else "not_dispatched", "internal")
            return CommandDispatchResult("unknown", "native_unconfirmed")

    async def interrupt(
        self,
        *,
        expected_generation: str | None = None,
        current: Callable[[], bool] | None = None,
    ) -> None:
        self._guard(expected_generation, current)
        self._interrupt_revision += 1
        if self._driver:
            await self._driver.interrupt()

    def respond(
        self,
        tool_use_id: str,
        answer: Answer,
        *,
        expected_generation: str | None = None,
        current: Callable[[], bool] | None = None,
        native_request: dict[str, str] | None = None,
    ) -> bool:
        self._guard(expected_generation, current)
        if expected_generation is not None and (
            native_request is None
            or self._native_requests.get(tool_use_id) != native_request
            or tool_use_id in self._answered_requests
            or native_request.get("runtime_id") != self._runtime_id
        ):
            return False
        driver, token = self._driver, self._token
        binding = self._native_requests.get(tool_use_id)
        if driver is None or token is None:
            return False
        result = driver.respond(tool_use_id, answer)
        # Remove its authority immediately, even when the driver's ordered answered event
        # is still waiting behind output. The displayed card closes on that event.
        if result and binding is not None and self._native_requests.get(tool_use_id) == binding:
            self._answered_requests.add(tool_use_id)
        return result

    def request_pending(self, tool_use_id: str, native_request: Mapping[str, str]) -> bool:
        """The card with exactly this binding is still waiting for an answer.

        The same four checks respond() makes before it touches the driver, in the same order and
        against the same state, so that a True here followed immediately by respond() can only
        become False through an event in between — never through a difference of opinion about
        what "pending" means. Read-only, and it takes no lock.
        """
        return (
            self._native_requests.get(tool_use_id) == native_request
            and tool_use_id not in self._answered_requests
            and native_request.get("runtime_id") == self._runtime_id
            and self._driver is not None
            and self._token is not None
        )

    def displayed_request(
        self, tool_use_id: str, native_request: Mapping[str, str]
    ) -> dict[str, Any] | None:
        """The projected {type, data, native_request} item of a still-pending card, deep-copied.

        This is what a recorded choice is an answer to, captured here rather than supplied by
        the browser: the card the app displayed is the only card it can honestly record.
        """
        if not self.request_pending(tool_use_id, native_request):
            return None
        return self._stream.pending_request(tool_use_id)

    def pending_cards(self) -> list[dict[str, Any]]:
        """Copies of this actor's still-pending projected cards, in arrival order.

        Read-only and takes no lock: a card may be answered or cancelled the moment after.
        """
        return [
            item
            for tool_use_id, binding in list(self._native_requests.items())
            if (item := self.displayed_request(tool_use_id, binding)) is not None
        ]

    def remote_permission(
        self, tool_use_id: str, native_request: Mapping[str, str]
    ) -> dict[str, Any] | None:
        """Complete details only while the same native runtime and request stay pending."""
        driver, token = self._driver, self._token
        if (
            self._closed
            or not self.request_pending(tool_use_id, native_request)
            or not isinstance(driver, PermissionDetailsDriver)
        ):
            return None
        binding = dict(self._native_requests[tool_use_id])
        details = driver.permission_details(tool_use_id)
        if (
            self._closed
            or self._driver is not driver
            or self._token is not token
            or native_request != binding
            or not self.request_pending(tool_use_id, binding)
        ):
            return None
        return details

    def deny_pending(self) -> None:
        """Answer every open card with Deny: the browser is gone, nobody will choose."""
        if self._driver is not None:
            for tool_use_id in self._driver.pending():
                self._driver.respond(tool_use_id, PermissionAnswer(allow=False))

    async def wait_idle(self, timeout_s: float) -> bool:
        """Wait up to `timeout_s` for the running turn, if any, to end. True when idle."""
        if not self.busy:
            return True
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._idle_waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout_s)
        except TimeoutError:
            return False
        finally:
            if waiter in self._idle_waiters:
                self._idle_waiters.remove(waiter)
        return not self.busy

    async def set_model(
        self,
        model: str,
        *,
        expected_generation: str | None = None,
        current: Callable[[], bool] | None = None,
    ) -> None:
        async with self._lock:
            self._guard(expected_generation, current)
            self._guard_profile_controls()
            try:
                self.register(model=model)
            except StateError as exc:
                self.report_state_error(exc)
                return
            if self._driver is not None:
                driver, token = self._driver, self._token
                try:
                    await driver.set_model(model)
                except Exception:  # an error reply, a timeout, or a gone process
                    self._emit(Error(code="bad_request", message=MSG_MODEL, recoverable=True))
                    return
                self._guard(expected_generation, current)
                if self._driver is not driver or self._token is not token:
                    raise CommandDispatchRejected("stale_generation")
            try:
                self._state.remember(self.session_id, model=model)
            except StateError as exc:
                self.report_state_error(
                    exc, message=MSG_MODEL_NOT_SAVED if self._driver is not None else None
                )

    def set_effort(
        self,
        effort: str,
        *,
        expected_generation: str | None = None,
        current: Callable[[], bool] | None = None,
    ) -> bool:
        """Effort is an argv flag: it takes effect by respawning at the next send."""
        self._guard(expected_generation, current)
        self._guard_profile_controls()
        try:
            self.register()
            self._state.remember(self.session_id, effort=effort)
        except StateError as exc:
            self.report_state_error(exc)
            return False
        self._effort_pending = self.live
        self._configuration_revision += 1
        return True

    async def set_mode(
        self,
        mode: str,
        *,
        expected_generation: str | None = None,
        current: Callable[[], bool] | None = None,
    ) -> None:
        """Without a process, store `mode` for the next spawn and emit it as a status. A live
        switch is asked of the driver and stores nothing here: a status the driver reports
        afterwards does (_track)."""
        async with self._lock:
            self._guard(expected_generation, current)
            self._guard_profile_controls()
            try:
                self.register()
            except StateError as exc:
                self.report_state_error(exc)
                return
            driver, token = self._driver, self._token
            if driver is None:
                try:
                    self._state.remember(self.session_id, mode=mode)
                except StateError as exc:
                    self.report_state_error(exc)
                    return
                self._emit(Status(mode=mode))
                return
            if not isinstance(driver, ModeDriver):
                self._emit(Error(code="bad_request", message=MSG_MODE, recoverable=True))
                return
            try:
                await driver.set_permission_mode(mode)
            except Exception:  # an error reply, a timeout, or a gone process
                self._emit(Error(code="bad_request", message=MSG_MODE, recoverable=True))
                return
            self._guard(expected_generation, current)
            if self._driver is not driver or self._token is not token:
                raise CommandDispatchRejected("stale_generation")

    async def close(
        self,
        reason: CloseReason = "requested",
        *,
        expected_generation: str | None = None,
        expected_turn_id: str | None = None,
        current: Callable[[], bool] | None = None,
    ) -> None:
        async with self._lock:
            self._guard(expected_generation, current)
            if expected_turn_id is not None and (self._turn_id != expected_turn_id or self._steers):
                raise CommandDispatchRejected("runtime_changed")
            self._closed = True
            driver, token = self._driver, self._token
            if self._driver is not None and self._driver.busy:
                # Ask first. The driver's close() is what waits for that ask to reach claude,
                # including when the CLI has not picked the message up yet and the interrupt is
                # still armed rather than sent.
                await self._driver.interrupt()
            if isinstance(self._driver, SteerDriver):
                # Claude Code runs a waiting steer as its next turn, even after a stop, and one may
                # start while the stop above is awaited. While the driver is still bound, let each
                # one end and its turn stop, so the stream says what became of it; it returns at
                # once when none is pending or running. What still waits is dropped below.
                await self._driver.settle_steers(STOP_FLUSH_S)
            changed = current is not None and not current()
            if driver is not None and (self._driver is not driver or self._token is not token):
                await self._close_driver(driver, finish_cleanup=True)
                if expected_generation is not None:
                    raise CommandDispatchRejected("stale_generation")
                return
            await self._retire(finish_cleanup=expected_generation is not None)
            if changed:
                raise CommandDispatchRejected("no_target")
            self._emit(SessionClosed(reason=reason))

    # --- internals

    async def _with_root_context(self, text: str) -> str:
        context = await self._root_context(self.session_id) if self._root_context else None
        # Native slash commands and skills own their argument bytes, including names the
        # initialize menu did not report. They still pass the per-turn folder check above.
        if context and not text.startswith("/"):
            text = joined_text(text, (context,))
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            raise StartError("bad_request", MSG_NOT_SENT) from None
        if size > MAX_TEXT_BYTES:
            raise StartError("bad_request", MSG_TURN_TOO_LARGE)
        return text

    def _command(self, text: str) -> tuple[str, str]:
        """("/name", the rest) when `text` starts with a command this session offers (the
        history form), else ("", text): a leading "/Users/..." is not a command."""
        match = _COMMAND.match(text)
        known = {c.name for c in self.started.commands} if self.started else set()
        if match and match[1] in known:
            return f"/{match[1]}", text[match.end() :].strip()
        return "", text

    async def _retire(self, *, finish_cleanup: bool = False) -> None:
        """Close the current process (if any) and forget it; its pid stays ours until it has
        exited, and nothing it emits from here on reaches the log."""
        driver = self._driver
        self._drop_driver()
        if driver is None:
            return
        await self._close_driver(driver, finish_cleanup=finish_cleanup)

    async def _close_driver(self, driver: AgentDriver, *, finish_cleanup: bool) -> None:
        """Retain ownership of a captured runtime even after its token was invalidated."""
        pid = driver.pid
        held: set[int] = {pid} if pid is not None else set()
        self._closing_pids |= held

        async def closed() -> None:
            await driver.close()
            # Only a completed close ends the driver's use of its Bot code copy; a close that
            # raises or is cancelled keeps it, even when a caller is cancelled after it.
            self._copy_uses.pop(driver, None)

        try:
            if finish_cleanup:
                await _finish_cleanup(closed())
            else:
                await closed()
        finally:
            self._closing_pids -= held

    async def _retire_captured(self, driver: AgentDriver, token: object) -> None:
        """Cancel only the runtime this operation opened, including after a late return."""
        if self._driver is driver:
            if self._token is not token:
                return
            self._drop_driver()
        await self._close_driver(driver, finish_cleanup=True)

    async def _close_exited(self, driver: AgentDriver) -> None:
        """Await the close of a Bot-copy driver whose process ended on its own, so its copy is
        released only once that close completes; a failed close keeps the copy in use."""
        try:
            await self._close_driver(driver, finish_cleanup=True)
        except Exception:
            log.warning("a Bot code copy's driver did not close")

    def _drop_driver(self) -> None:
        """Forget the process; the next one resumes the transcript once anything was sent. Each
        steer still waiting ends dropped here, since the gone driver's events no longer count."""
        self._driver = self._token = None
        pending = list(self._native_requests.items())
        self._native_requests.clear()
        self._answered_requests.clear()
        steers = list(self._steers)
        self._steers.clear()
        for tool_use_id, binding in pending:
            self._emit(RequestCancelled(tool_use_id=tool_use_id), native_request=binding)
        for steer_id in steers:  # in write order
            self._emit(SteerEnded(steer_id=steer_id, outcome="dropped"))
        if self._sent:
            read_only_tools = self._launch.read_only_tools  # a read-only session stays read-only
            self._launch = Launch(self.session_id, resume=True, read_only_tools=read_only_tools)

    def _emit_from(self, token: object, event: Event) -> None:
        if token is not self._token:
            return
        if isinstance(event, SteerEnded):
            # Each steer ends once: an end for one not held (never, or already ended) is dropped.
            echo = self._steers.pop(event.steer_id, None)
            if echo is None:
                return
            self._emit(event)
            if event.outcome == "started":  # the message joins the conversation as it is read
                self._emit(replace(echo, at=now_iso()))
            return
        self._emit(event)

    def _emit(self, event: Event, *, native_request: dict[str, str] | None = None) -> Envelope:
        if isinstance(event, TurnStarted):
            try:
                canonical = str(uuid.UUID(event.turn_id))
            except ValueError:
                canonical = ""
            self._turn_id = event.turn_id if canonical == event.turn_id else str(uuid.uuid4())
        if isinstance(event, QuestionAsked | PermissionRequested):
            native_request = {
                "runtime_id": self._runtime_id,
                "request_id": str(uuid.uuid4()),
                "turn_id": self._turn_id,
            }
            candidate = event.envelope(self.session_id, self._seq + 1, self.generation)
            candidate["native_request"] = native_request
            if not self._stream.can_keep_card(candidate):
                # Never offer Allow for a shortened operation. Resolve the captured native
                # request before reporting this bounded refusal; respond may emit reentrantly.
                driver = self._driver
                if driver is not None:
                    driver.respond(event.tool_use_id, PermissionAnswer(allow=False))
                self._emit(
                    RequestCancelled(tool_use_id=event.tool_use_id), native_request=native_request
                )
                return self._emit(
                    Error(code="bad_request", message=MSG_REQUEST_LIMIT, recoverable=False)
                )
            self._native_requests[event.tool_use_id] = native_request
            self._answered_requests.discard(event.tool_use_id)
        elif isinstance(event, RequestAnswered | RequestCancelled):
            native_request = native_request or self._native_requests.get(event.tool_use_id)
            if self._native_requests.get(event.tool_use_id) == native_request:
                self._native_requests.pop(event.tool_use_id, None)
                self._answered_requests.discard(event.tool_use_id)
        elif isinstance(event, ToolDone):
            self._native_requests.pop(event.block_id, None)
            self._answered_requests.discard(event.block_id)
        elif isinstance(event, TurnDone | SessionClosed):
            # A run's card outlives the turn that ends here, as it does in sessionReducer's
            # turn.done (the CLI ends turns of its own while a run goes on, scenario 28): its
            # binding and its projected card stay while the driver still waits on it, or until its
            # answered event, which can arrive after this turn end and closes the browser's card
            # only when it carries the binding. The chat's own cards end with the turn.
            waiting = (
                set(self._driver.pending()) | self._answered_requests
                if isinstance(event, TurnDone) and self._driver is not None
                else set()
            )
            for tool_use_id in list(self._native_requests):
                card = self._stream.pending_request(tool_use_id)
                if tool_use_id in waiting and card is not None and card["data"].get("agent"):
                    continue
                binding = self._native_requests.pop(tool_use_id)
                self._stream.drop_card(tool_use_id)
                if card is not None and card["data"].get("agent"):
                    # The browser keeps a run's card past a turn end; close it with its binding,
                    # since the driver's own cancel for it may still be queued and arrive unbound.
                    self._emit(RequestCancelled(tool_use_id=tool_use_id), native_request=binding)
            self._answered_requests.intersection_update(self._native_requests)
        state_error = None
        try:
            self._track(event)
        except StateError as exc:
            # A native mode observation remains true even when saving it fails. Deliver it
            # before the storage error, and never unwind the provider's frame pump.
            state_error = exc
        self._seq += 1
        envelope = event.envelope(self.session_id, self._seq, self.generation)
        if native_request is not None:
            envelope["native_request"] = native_request
        self._stream.append(envelope)
        for subscriber in list(self._subscribers):
            subscriber(envelope)
        if self._observer is not None:
            try:
                self._observer(self, envelope)
            except Exception:
                log.warning("a session observer failed")
        if state_error is not None:
            self.report_state_error(state_error)
        return envelope

    def _remember_mode(self, mode: str) -> None:
        """Persist the mode claude reports, but only one a process can be spawned in.

        PERMISSION_MODES is deliberately a subset of what the CLI knows (never bypassPermissions
        or dontAsk), and a CLI bump can rename one — 2.1.259 already turned "default" into
        "manual". Storing a name from outside it poisoned state.json: build_argv raised on the
        next start, so every later send did nothing at all until the app was restarted and the
        load-side scrub cleaned it. The session keeps the mode it was spawned with, which is the
        one the next process can actually use; the chip still shows what the CLI reports, since
        that comes from the event, not from here.

        A profile session's Prefs are its bound launch, written only by prepare: storing a
        reported mode (a plan approval's setMode, or "default" for an argv "manual") would make
        every later send and resume refuse as launch_changed."""
        if self._profile:
            return
        if mode not in PERMISSION_MODES:
            log.warning("claude reports a permission mode VIKI cannot spawn in: %r", mode)
            return
        self._state.remember(self.session_id, mode=mode)

    def _track(self, event: Event) -> None:
        if isinstance(event, ToolStarted) and event.tool in WRITE_TOOLS:
            self.wrote = True
        elif isinstance(event, Status) and event.mode:
            self._remember_mode(event.mode)
        elif isinstance(event, Error) and self._driver is not None and not self._driver.alive:
            driver = self._driver
            self._drop_driver()  # the process is gone; the next send starts a new one
            if driver in self._copy_uses:
                # Its tree may still run in the copy until the driver's own close ends it.
                close = asyncio.ensure_future(self._close_exited(driver))
                self._copy_closes.add(close)
                close.add_done_callback(self._copy_closes.discard)
        if self._readiness is not None and not (
            self.started is not None and self.started.model.startswith(PREFIX)
        ):
            if isinstance(event, Error) and event.code == "not_logged_in":
                # A refused start already calls this once (_start_refused); a mid-session auth
                # failure is the same fact from a different place, so observing it twice is
                # harmless.
                self._readiness.observe_start_error("not_logged_in")
            elif isinstance(event, TurnDone) and event.status == "success":
                # Proof the user signed back in: per Anthropic's docs, one sign-in
                # re-authenticates every session on the machine, this one included.
                self._readiness.observe_account({"apiProvider": "firstParty"})
        if self._idle_waiters and not self.busy:
            for waiter in self._idle_waiters:
                if not waiter.done():
                    waiter.set_result(None)
            self._idle_waiters.clear()


# --- all sessions ---------------------------------------------------------------------------------


class SessionManager:
    def __init__(
        self,
        *,
        config: DriverConfig,
        state: AppState,
        claude_home: Path,
        make_driver: DriverFactory | None = None,
        readiness: Readiness | None = None,
    ) -> None:
        self._config = config
        self._state = state
        self._claude_home = claude_home
        self._readiness = readiness
        self._make_driver = make_driver or self._claude_driver
        self._max_live: Callable[[], int] = lambda: MAX_LIVE
        self._llm_connections: Connections | None = None
        self._llm_gateway: Gateway | None = None
        self._actors: dict[str, SessionActor] = {}
        self._closing: set[SessionActor] = set()  # forgotten, but their processes still exiting
        self._copy_uses: dict[AgentDriver, str] = {}  # every actor's (SessionActor._copy_uses)
        self._profile_guard: LaunchGuard | None = None
        self._catalog_observer: StartedObserver | None = None
        self._profile_initialized: Callable[[str, StartFacts | None], Awaitable[None]] | None = None
        self._mcp_resolver: McpResolver | None = None
        self._bot_memory_resolver: McpResolver | None = None
        self._bot_prompt_resolver: McpResolver | None = None
        self._bot_code_resolver: BotCodeResolver | None = None
        self._root_resolver: RootResolver | None = None
        self._root_context: RootContextResolver | None = None
        self._folders: FolderResolver | None = None
        self._excerpt_vault: VaultService | None = None
        self._active_vault_id = ""
        self._settling: Callable[[], None] = lambda: None
        self._retained: Callable[[str], bool] = lambda _session_id: False
        self._observer: Callable[[SessionActor, Envelope], None] | None = None

    @property
    def app_state(self) -> AppState:
        """Shared app-owned storage for services that must not create a native session."""
        return self._state

    def bind_assets(self, resolver: Callable[[Attachment], Awaitable[bytes]]) -> None:
        """Install the app-owned image resolver before any image turn can start a driver."""
        self._config = replace(self._config, resolve_attachment=resolver)

    def bind_limits(self, observer: Callable[[dict[str, object]], None]) -> None:
        """Install the plan-limit observer every later spawn's driver reports rate limits to."""
        self._config = replace(self._config, observe_limits=observer)

    def bind_max_live(self, reader: Callable[[], int]) -> None:
        """Install the live-session cap `reader`, read at every admission and snapshot.

        Applies to the next start: a session already live keeps running. `reader` must be
        sync, so no await enters between an actor's admission check and going live; existing
        actors reach a later bind too, since each holds a closure back to this manager rather
        than a snapshotted int."""
        self._max_live = reader

    def max_live(self) -> int:
        return self._max_live()

    def bind_profiles(
        self,
        guard: LaunchGuard,
        observer: StartedObserver,
        *,
        initialized: Callable[[str, StartFacts | None], Awaitable[None]],
    ) -> None:
        self._profile_guard, self._catalog_observer = guard, observer
        self._profile_initialized = initialized

    def bind_excerpts(self, vault: VaultService, active_workspace_id: str) -> None:
        """Install the bounded vault reader every dispatch resolves its excerpt parts through,
        with the window's active vault. Until it is installed, a turn carrying parts is refused."""
        self._excerpt_vault, self._active_vault_id = vault, active_workspace_id

    def bind_bot_memory(self, resolver: McpResolver) -> None:
        self._bot_memory_resolver = resolver

    def bind_bot_code(self, resolver: BotCodeResolver) -> None:
        """Install the final Bot-copy resolver for existing and future actors."""
        self._bot_code_resolver = resolver
        for actor in self._actors.values():
            actor._bot_code_resolver = resolver

    async def _resolve_bot_memory(self, session_id: str) -> str | None:
        return await self._bot_memory_resolver(session_id) if self._bot_memory_resolver else None

    def bind_bot_prompt(self, resolver: McpResolver) -> None:
        """Install the resolver a Bot session calls after its memory settings, before each
        spawn: its instructions, memory rule and Bot rules for initialize's
        `appendSystemPrompt`."""
        self._bot_prompt_resolver = resolver

    async def _resolve_bot_prompt(self, session_id: str) -> str | None:
        return await self._bot_prompt_resolver(session_id) if self._bot_prompt_resolver else None

    def bind_mcp(self, resolver: McpResolver) -> None:
        """Install the resolver a profile session calls after its guard, before each spawn."""
        self._mcp_resolver = resolver

    def bind_roots(
        self,
        resolver: RootResolver,
        *,
        context: RootContextResolver | None = None,
        folders: FolderResolver | None = None,
    ) -> None:
        """Bind the per-spawn cwd, per-turn visible folder context and per-spawn ticked folders
        for existing/future actors."""
        self._root_resolver, self._root_context, self._folders = resolver, context, folders

    def bind_settle(self, hook: Callable[[], None]) -> None:
        """Run `hook` when the browser is gone, before settle's grace starts."""
        previous = self._settling

        def settling() -> None:
            try:
                previous()
            finally:
                hook()

        self._settling = settling

    def bind_retain(self, pred: Callable[[str], bool]) -> None:
        """Install the predicate, called with a session id at every check, that names the chats
        the last window leaves running: settle and close_unretained spare them, and hold waits
        while one is live. Until it is installed, no chat is retained."""
        self._retained = pred

    def bind_observer(self, observer: Callable[[SessionActor, Envelope], None]) -> None:
        """Install the observer every envelope of an actor made after this call is reported to,
        once projected. Until it is installed, no actor reports to one."""
        self._observer = observer

    # The actor calls these four only for its own kind: the first three for a profile session,
    # the observer for an ordinary one.
    async def _initialized_profile(self, session_id: str, facts: StartFacts | None) -> None:
        if self._profile_initialized is None:
            raise StartError("bad_request", "The profile launch could not be recorded.")
        await self._profile_initialized(session_id, facts)

    async def _guard_profile(self, session_id: str, explicit_send: bool, prefs: Prefs) -> None:
        if not explicit_send or self._profile_guard is None:
            raise StartError("bad_request", "Review the profile draft and use Send to start it.")
        await self._profile_guard(session_id, prefs)

    async def _resolve_mcp(self, session_id: str) -> str | None:
        if self._mcp_resolver is None:
            return None
        return await self._mcp_resolver(session_id)

    async def _resolve_root(self, session_id: str) -> Path | None:
        if self._root_resolver is None:
            return None
        return await self._root_resolver(session_id)

    async def _resolve_root_context(self, session_id: str) -> str | None:
        if self._root_context is None:
            return None
        return await self._root_context(session_id)

    async def _resolve_folders(self, session_id: str) -> tuple[Path, ...]:
        if self._folders is None:
            return ()
        return await self._folders(session_id)

    async def _observe_catalog(self, started: SessionStarted) -> None:
        if self._catalog_observer is not None:
            await self._catalog_observer(started)

    def bind_model_connections(self, connections: Connections, gateway: Gateway) -> None:
        self._llm_connections, self._llm_gateway = connections, gateway
        self._state.bind_default_model_guard(connections.settings.chat_defaults_available)

    def native_model(self) -> str | None:
        """The native model default for a session restricted to subscription inference."""
        model = self._state.defaults.model
        if self._llm_connections is not None:
            return self._llm_connections.settings.native_chat_model(model)
        return None if model and model.startswith(PREFIX) else model

    def _claude_driver(
        self, launch: Launch, prefs: Prefs, emit: Callable[[Event], None]
    ) -> AgentDriver:
        readiness = self._readiness
        if not self._config.cli:
            # An empty argv is what the launcher builds when nothing may be spawned (the one spawn
            # predicate). Refuse here, before a driver exists, rather than letting the
            # SDK try to run "". The message names the cause the user can undo themselves: the
            # override variable, or the batch shim that stands where claude.exe should be.
            state = None if readiness is None else readiness.cli_state
            raise StartError(
                "cli_missing",
                MSG_CLI_CONFIG_DIR
                if state == "config_dir_override"
                else MSG_CLI_SHIM
                if state == "unusable"
                else MSG_CLI_MISSING,
            )
        custom = bool(prefs.model and prefs.model.startswith(PREFIX))
        if readiness is not None and (
            readiness.billing_preflight_blocked if custom else readiness.billing == "blocked"
        ):
            # Conflicting settings can override the host route too, whatever sign-in the user
            # allows. A runtime subscription refusal, in contrast, says nothing about an
            # explicitly selected connection. A native start reads `billing`, which a preflight
            # conflict blocks too unless the user allowed Claude Code's other sign-in methods.
            raise StartError("billing", MSG_BILLING)
        config = self._config
        if readiness is not None:
            # Each spawn starts from the version readiness holds now (a chat's start-up report
            # may have replaced the launch probe's) and hands a differing report back to it.
            config = replace(
                config,
                cli_version=(
                    readiness.version if custom else readiness.version or config.cli_version
                ),
                observe_version=readiness.observe_version,
                subscription_only=not readiness.other_sign_in,
            )
        if custom:
            if re.fullmatch(
                r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", config.cli_version
            ) is None or tuple(int(part) for part in config.cli_version.split(".")) < (2, 1, 281):
                raise StartError(
                    "bad_request",
                    "Model connections require a checked Claude Code version of 2.1.281 or later. "
                    "Update Claude Code and check its version in Providers in Settings.",
                )
            try:
                assert prefs.model is not None
                split_model(prefs.model)
                if self._llm_connections is None or self._llm_gateway is None:
                    raise ConnectionError("connections_unavailable")
                connection = self._llm_connections.resolve(prefs.model)
                route = self._llm_gateway.issue(connection)
                config = replace(config, llm_route=route, observe_limits=None)
            except ConnectionError:
                raise StartError(
                    "bad_request",
                    "This model connection is unavailable. Check Providers in Settings.",
                ) from None
        try:
            return ClaudeDriver(
                launch,
                mode=prefs.mode,
                effort=prefs.effort,
                model=prefs.model,
                config=config,
                emit=emit,
            )
        except Exception:
            if config.llm_route is not None:
                config.llm_route.revoke()
            raise

    def get(self, session_id: str) -> SessionActor | None:
        return self._actors.get(session_id)

    def open(self, session_id: str, *, profile: bool | None = None) -> SessionActor:
        """The actor for an existing transcript; its process starts on the first send.

        `profile` is the durable origin when the caller already read it off the event loop;
        None reads it here, on the caller's thread."""
        if session_id not in self._actors:
            if profile is None:
                profile = self._state.store.session_origin(session_id) == "profile"
            self._actors[session_id] = self._actor(
                session_id, Launch(session_id, resume=True), profile=profile
            )
        return self._actors[session_id]

    def create(
        self,
        session_id: str,
        *,
        read_only_tools: bool = False,
        root_resolver: RootResolver | None = None,
        expected_model: object = MODEL_UNSPECIFIED,
    ) -> SessionActor:
        """A brand-new session with an id the client generated. Only the inbox suggestion run
        passes `read_only_tools` (`Launch.read_only_tools`)."""
        actor = self._actors[session_id] = self._actor(
            session_id,
            Launch(session_id, read_only_tools=read_only_tools),
            registration_pending=True,
            root_resolver=root_resolver,
        )
        if expected_model is MODEL_UNSPECIFIED:
            self._register_new(actor)
        else:
            self._register_new(actor, expected_model=expected_model)
        return actor

    def _register_new(
        self, actor: SessionActor, *, expected_model: object = MODEL_UNSPECIFIED
    ) -> None:
        try:
            if expected_model is MODEL_UNSPECIFIED:
                actor.register()
            else:
                actor.register(expected_model=expected_model)
        except (StateError, ProtectionError) as exc:
            if expected_model is not MODEL_UNSPECIFIED or (
                isinstance(exc, StateError)
                and exc.code
                in (
                    "model_defaults_unavailable",
                    "model_choice_required",
                    "model_preview_changed",
                )
            ):
                self._actors.pop(actor.session_id, None)
            raise

    def attach_prepared(self, session_id: str, *, profile: bool | None = None) -> SessionActor:
        """Attach committed preferences without re-registering or starting a provider.

        `profile` is the durable origin when the caller already read it off the event loop;
        None reads it here, on the caller's thread."""
        if profile is None:
            profile = self._state.store.session_origin(session_id) == "profile"
        if not profile:
            raise StateError("corrupt")
        if session_id not in self._actors:
            self._actors[session_id] = self._actor(session_id, Launch(session_id), profile=True)
        return self._actors[session_id]

    def fork(
        self, source_id: str, fork_id: str, *, source_profile: bool | None = None
    ) -> SessionActor:
        """A copy of `source_id` under `fork_id`, keeping the source's preferences.

        `source_profile` is the source's durable origin, as `profile` is for `open`."""
        if source_profile is None:
            source_profile = self._state.store.session_origin(source_id) == "profile"
        if source_profile:
            raise CommandDispatchRejected("profile_locked")
        actor = self._actors[fork_id] = self._actor(
            fork_id, Launch(fork_id, fork_of=source_id), registration_pending=True
        )
        self._register_new(actor)
        return actor

    async def close(
        self,
        session_id: str,
        *,
        expected_generation: str | None = None,
        expected_turn_id: str | None = None,
        current: Callable[[], bool] | None = None,
    ) -> None:
        """Close a session and forget it: reopening it later reads its transcript afresh (the
        user may have continued it elsewhere in between).

        The id stays reserved until the close finishes. A close is not instant — it interrupts a
        running turn, closes stdin, and waits out two grace periods before reaping — and the
        client learns nothing until SessionClosed arrives at the very end. Dropping the id first
        left a window in which a second Enter (this app supports more than one window) found no
        actor, found a transcript on disk, and started a fresh `claude --resume` on the same
        file the dying process was still flushing. The live-elsewhere guard could not catch it
        either: own_pids() counts the closing process as ours, exactly so that it is not
        mistaken for VS Code."""
        actor = self._actors.get(session_id)
        if actor is None:
            return
        self._closing.add(actor)
        complete = False
        try:
            await actor.close(
                expected_generation=expected_generation,
                expected_turn_id=expected_turn_id,
                current=current,
            )
            complete = True
        finally:
            if complete and self._actors.get(session_id) is actor:
                self._actors.pop(session_id, None)
            self._closing.discard(actor)

    def live(self) -> list[SessionActor]:
        return [a for a in self._actors.values() if a.live]

    def code_copy_in_use(self, worktree_id: str) -> bool:
        """On the loop: a driver made for this Bot code copy has not finished closing, whether
        it is starting, running or closing and whether or not its actor is still held here."""
        return worktree_id in self._copy_uses.values()

    def own_pids(self) -> set[int]:
        """Every claude process this app runs, including ones still exiting after a close."""
        return {pid for a in (*self._actors.values(), *self._closing) for pid in a.pids}

    def others_in_vault(self) -> list[OtherSession]:
        return vault_sessions(self._claude_home, self._config.vault, self.own_pids())

    def retaining(self) -> bool:
        """A live chat is retained (bind_retain): the last window's hold keeps the app for it."""
        return any(self._retained(actor.session_id) for actor in self.live())

    def _unretained(self) -> list[SessionActor]:
        return [actor for actor in self.live() if not self._retained(actor.session_id)]

    async def settle(self, grace_s: float, until: Callable[[], bool] | None = None) -> None:
        """The browser is gone: deny every open card — and every card raised after, within
        DENY_POLL_S, since nobody is there to choose — while running turns get up to
        `grace_s` to end on their own. A retained chat (bind_retain), as the predicate answers
        at each poll, is left alone: its cards stay open and its turn is not waited for.

        `until` is what makes "nobody is there" true rather than merely assumed. A second launch
        opens a window into this same process, and the reconnected page replays the running
        turn; without this check the user would sit in front of a live conversation whose every
        card was refused within a second of appearing, silently, for the rest of the grace.
        Caller passes a predicate that is False once a window is back."""
        clock = asyncio.get_running_loop().time
        deadline = clock() + grace_s
        self._settling()  # nothing unattended may start while cards are refused
        while True:
            if until is not None and not until():
                return
            live = self._unretained()
            for actor in live:
                actor.deny_pending()
            left = deadline - clock()
            if left <= 0 or not any(actor.busy for actor in live):
                return
            await asyncio.gather(*(actor.wait_idle(min(left, DENY_POLL_S)) for actor in live))

    async def close_all(self) -> None:
        """Close every live session as browser_gone, each within CLOSE_TIMEOUT_S. Open cards
        are denied first: a caller that did not go through settle() (the lifespan's own
        shutdown, say) would otherwise leave a turn waiting on an answer nobody can give."""
        for actor in self.live():
            actor.deny_pending()
        for actor in self.live():
            try:
                await asyncio.wait_for(actor.close("browser_gone"), CLOSE_TIMEOUT_S)
            except TimeoutError:
                log.warning("a session did not close within %ss", CLOSE_TIMEOUT_S)

    async def close_unretained(self) -> None:
        """close_all() for the live chats that are not retained when it is called: their cards
        are denied, then each closes as browser_gone within CLOSE_TIMEOUT_S. A retained chat
        keeps its process and its open cards."""
        gone = self._unretained()
        for actor in gone:
            actor.deny_pending()
        for actor in gone:
            try:
                await asyncio.wait_for(actor.close("browser_gone"), CLOSE_TIMEOUT_S)
            except TimeoutError:
                log.warning("a session did not close within %ss", CLOSE_TIMEOUT_S)

    async def hold(self, limit_s: float, until: Callable[[], bool], *, poll_s: float = 1.0) -> None:
        """Wait while a retained chat is live, checking every `poll_s`. Returns once `until()`
        is False (a window is back), no live chat is retained, or `limit_s` has passed. It
        denies nothing: a retained chat's cards wait for the user."""
        clock = asyncio.get_running_loop().time
        deadline = clock() + limit_s
        while until() and self.retaining():
            left = deadline - clock()
            if left <= 0:
                return
            await asyncio.sleep(min(left, poll_s))

    def _actor(
        self,
        session_id: str,
        launch: Launch,
        *,
        registration_pending: bool = False,
        profile: bool = False,
        root_resolver: RootResolver | None = None,
    ) -> SessionActor:
        actor = SessionActor(
            session_id,
            launch,
            state=self._state,
            make_driver=self._make_driver,
            admit=lambda actor: actor.live or len(self.live()) < self._max_live(),
            max_live=lambda: self._max_live(),
            holder=self._find_holder,
            registration_pending=registration_pending,
            readiness=self._readiness,
            launch_guard=self._guard_profile,
            started_observer=self._observe_catalog,
            initialized=self._initialized_profile,
            profile=profile,
            mcp_resolver=self._resolve_mcp,
            bot_memory_resolver=self._resolve_bot_memory,
            bot_prompt_resolver=self._resolve_bot_prompt,
            bot_code_resolver=self._bot_code_resolver,
            excerpt_vault=self._excerpt_vault,
            active_vault_id=self._active_vault_id,
            root_resolver=self._resolve_root if root_resolver is None else root_resolver,
            root_context=self._resolve_root_context,
            # Ticked folders are for an ordinary chat only, not a profile session, a custom-root
            # (bot) actor or the read-only inbox suggestion run.
            folder_resolver=(
                self._resolve_folders
                if root_resolver is None and not profile and not launch.read_only_tools
                else None
            ),
            copy_uses=self._copy_uses,
        )
        actor._observer = self._observer
        return actor

    async def _find_holder(self, session_id: str) -> OtherSession | None:
        """The registry read runs in a thread (on macOS it runs ps); own_pids() stays on the
        loop, the only place the actor tables change."""
        own = self.own_pids()
        return await asyncio.to_thread(find_holder, self._claude_home, session_id, own)
