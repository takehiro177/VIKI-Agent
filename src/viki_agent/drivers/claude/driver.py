"""ClaudeDriver: one claude.exe process for one chat session.

It wires the transport (frames), the SDK control glue (initialize, permissions, control calls)
and the mapper (frames → events), and adds what the protocol needs on top:

- permission, plan and question cards, keyed by tool_use_id and answered through `respond()`;
- a layered stop: `interrupt` once the CLI is inside the turn (an earlier interrupt may be
  acknowledged without cancelling, so it waits and close keeps stdin open for it),
  then kill the tree — if the interrupt is not acknowledged, or if the CLI never enters the turn;
- turns the CLI starts by itself: a background agent's completion wakes the parent for a
  continuation turn with no user frame; it runs and stops like any other turn;
- steers (`steer`): each one is ended by its own command_lifecycle frames, as started or
  dropped, or dropped by an exit; one the CLI starts after its turn has ended opens a turn,
  which `settle_steers` interrupts before a close;
- the billing gate on the initialize reply's `account` block;
- a mid-session sign-in failure (an assistant frame's `error: "authentication_failed"`), reported
  once as `not_logged_in` after the turn ends, with the process kept running — per Anthropic's
  docs, one sign-in re-authenticates every session on the machine;
- the context percentage on every `turn.done`, and a whitelisted snapshot of the same reply;
- on request, that snapshot plus a fresh, whitelisted MCP status (`inventory`);
- the CLI's own start report (`start_facts`): the version and permission mode its initialize
  reply names, its model menu and, for a launch with an app `--mcp-config`, the dynamic MCP
  servers one fresh status lists. A reported version names the session and, when it differs
  from `DriverConfig.cli_version`, goes to `DriverConfig.observe_version` once.

Events go to `emit` in order; the SessionActor numbers them and fans them out. One bad frame
never ends the pump: it becomes an `internal` error and the stream goes on.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import ipaddress
import json
import logging
import math
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES,
    CLIConnectionError,
    PermissionMode,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ProcessError,
    ToolPermissionContext,
)

from viki_agent.app.agent_grants import AgentGrant
from viki_agent.drivers.base import (
    Answer,
    McpControlError,
    PermissionAnswer,
    QuestionAnswer,
    RunStopError,
    SendError,
    StartError,
    StartFacts,
)
from viki_agent.drivers.claude.context_report import context_report, failed_report
from viki_agent.drivers.claude.control import ClaudeControl, Launch, build_argv
from viki_agent.drivers.claude.mapper import (
    CARD_REST_LIMIT,
    Mapper,
    MapperOverflow,
    tool_question,
    tool_row,
)
from viki_agent.drivers.claude.plan_limits import plan_limit_reading
from viki_agent.drivers.claude.transport import (
    EXIT,
    FRAME_ERROR,
    GRACE_S,
    POLL_S,
    ClaudeTransport,
    Spawner,
)
from viki_agent.events import (
    Command,
    Error,
    ErrorCode,
    Event,
    ModelOption,
    PermissionRequested,
    Question,
    QuestionAsked,
    QuestionOption,
    RequestAnswered,
    RequestCancelled,
    SessionStarted,
    Status,
    SteerEnded,
    TurnDone,
    TurnStarted,
    Usage,
)
from viki_agent.llm.connections import PREFIX
from viki_agent.llm.gateway import RuntimeRoute
from viki_agent.profiles.mcp_config import SERVER_PREFIX
from viki_agent.turn_input import Attachment, AttachmentUnavailable, TurnInput

log = logging.getLogger(__name__)
Emit = Callable[[Event], None]
PermissionResult = PermissionResultAllow | PermissionResultDeny
# Cards travel through the frame queue as this synthetic frame, so the pump stays the only
# emitter and a card never overtakes the tool_use frame it belongs to.
CARD = "viki.card"
# The bytes are retained NDJSON input (plus canonical JSON for local cards), not Python heap.
# A single valid frame above the queue budget also ends the attempt. EXIT and the private
# overflow sentinel have one reserved slot each and never compete with ordinary output.
FRAME_QUEUE_ITEMS = 1024
FRAME_QUEUE_BYTES = 8 * 1024 * 1024
MAX_PENDING_CARDS = 32
MAX_PERMISSION_BYTES = 8 << 10
MAX_PERMISSION_DEPTH = 16
MAX_PERMISSION_NODES = 2048

# Never offered in the / menu: they only make sense in a terminal (the CLI lists the rest of
# them in system/init's terminal_slash_commands, which arrives too late for the menu).
TERMINAL_ONLY = frozenset({"clear", "doctor", "color"})
# Modes an approved plan may hand back to; anything else falls back to "default".
RESTORE_MODES = frozenset({"default", "acceptEdits", "auto"})

# The Agent SDK's SDKAssistantMessageError value for an expired or invalid login (its reference
# also lists billing_error, rate_limit and others beside it): the one value _watch treats as a
# sign-in failure. Any other value, or an assistant frame with no `error` at all, is not treated
# as one — an exact compare fails closed by construction.
AUTH_FAILED = "authentication_failed"

MSG_CLI_MISSING = "Can't find the Claude Code executable."
MSG_LOGIN = "Not logged in to Claude Code. Open claude in a terminal and run /login."
MSG_BILLING = (
    "Startup was stopped because a setting was found that leads to billing outside the "
    "subscription."
)
MSG_NO_TRANSCRIPT = "This conversation's record could not be found."
# A first-turn crash may leave no transcript to resume, so the message promises resumption only
# when a conversation record remains.
MSG_CRASHED = (
    "Claude Code stopped partway through. If a record of the conversation remains, sending "
    "again will resume from where it left off."
)
# A stop can kill Claude before the first transcript exists, so this message asks the user to
# check what ran before sending again.
MSG_STOP_KILLED = (
    "Couldn't stop it, so Claude Code was ended. Any operation that was partway through is "
    "left as it is. Check the conversation record and what changed, then send your next message."
)
MSG_INTERNAL = "Part of the received data couldn't be processed."
MSG_COMMAND_LIMIT = "This command is too long to review. Ask for a shorter command."
MSG_STOP_AT_DRAFT = (
    "Stopped: this routine runs unattended and stops before any step that needs the owner's "
    "confirmation. Do not take this step or work around it. Finish now with a draft of the work "
    "so far, then report what needs the owner's decision."
)
MSG_OVERFLOW = (
    "This run was ended because the data received from Claude Code went over the processing "
    "limit. There's no way to confirm whether the operation finished. Check the conversation "
    "record and what changed, then send the next one."
)


@dataclass(frozen=True)
class DriverConfig:
    """Everything a driver needs besides the session's own launch and preferences."""

    cli: tuple[str, ...]  # the executable; tests prefix a script: (python, fake_claude.py)
    vault: Path
    env: Mapping[str, str]
    cli_version: str = ""
    skills: frozenset[str] = frozenset()  # vault skill names, listed first in the / menu
    spawner: Spawner | None = None  # tests only
    grace_s: float = GRACE_S
    poll_s: float = POLL_S
    control_timeout_s: float = 5.0
    # A stop asked for before the CLI picks the message up cannot be sent yet, so it
    # waits for system/init. These bound that wait: how long before the tree is killed instead,
    # and how long close() holds stdin open for an armed stop to go out.
    stop_pickup_s: float = 30.0
    stop_flush_s: float = 3.0
    resolve_attachment: Callable[[Attachment], Awaitable[bytes]] | None = None
    # Every recognized rate_limit_event frame's plan-limit reading, for the process lifetime.
    observe_limits: Callable[[dict[str, object]], None] | None = None
    # The version the initialize reply names, when it differs from cli_version: once per process.
    observe_version: Callable[[str], None] | None = None
    llm_route: RuntimeRoute | None = None
    # False when the user allows Claude Code's other sign-in methods: a native session then
    # starts on whatever account the initialize reply names.
    subscription_only: bool = True
    # This process's VIKI Agent CLI key: its variables join the spawn environment after the
    # route's, and _close_control or the process's exit revokes it.
    agent_grant: AgentGrant | None = None


def check_billing(info: Mapping[str, Any]) -> None:
    """The initialize reply must show first-party subscription authentication."""
    account = info.get("account") or {}
    if not account:
        raise StartError("not_logged_in", MSG_LOGIN)
    if account.get("apiProvider") != "firstParty" or not account.get("subscriptionType"):
        raise StartError("billing", MSG_BILLING)


# ASCII only: a str pattern's \d also matches other scripts' digits.
_VERSION = re.compile(r"\d{1,6}(?:\.\d{1,6}){1,3}", re.ASCII)


def _version_report(value: object) -> str | None:
    """The initialize reply's `claude_code_version` when it is two to four dotted integers."""
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else None


def _app_servers(status: Mapping[str, Any]) -> tuple[str, ...] | None:
    """The names an `mcp_status` reply lists with `source` "dynamic" (a plugin server shares
    the scope, not the source), or None when `mcpServers` is not a list."""
    servers = status.get("mcpServers")
    if not isinstance(servers, list):
        return None
    names: list[str] = []
    for item in cast(list[object], servers):
        if not isinstance(item, Mapping):
            continue
        row = cast(Mapping[str, object], item)
        name = row.get("name")
        if isinstance(name, str) and row.get("source") == "dynamic":
            names.append(name)
    return tuple(names)


def classify_exit(stderr: str) -> tuple[ErrorCode, str]:
    """An error code and user-facing message for a claude process that ended on its own."""
    low = stderr.lower()
    if "no conversation found" in low:
        # Not "crashed": that card offers sending again, and there is no transcript left to resume,
        # so the retry could only fail the same way.
        return "transcript_missing", MSG_NO_TRANSCRIPT
    if "login" in low or "not logged in" in low or "authenticat" in low:
        return "not_logged_in", MSG_LOGIN
    return "crashed", MSG_CRASHED


def menu_commands(raw: Sequence[Mapping[str, Any]], skills: frozenset[str]) -> list[Command]:
    """The / menu: vault skills first, then everything else, each alphabetical."""
    commands = [
        Command(
            name=str(c["name"]),
            description=str(c.get("description", "")),
            argument_hint=str(c.get("argumentHint", "")),
            skill=c["name"] in skills,
        )
        for c in raw
        if c.get("name") and c["name"] not in TERMINAL_ONLY
    ]
    return sorted(commands, key=lambda c: (not c.skill, c.name))


def _fields(item: object, spec: Mapping[str, tuple[str, type]]) -> dict[str, object] | None:
    """Only the named fields, each of its exact type; None when one is missing or off type."""
    if not isinstance(item, Mapping):
        return None
    source = cast(Mapping[str, object], item)
    row = {name: source.get(key) for name, (key, _) in spec.items()}
    return row if all(isinstance(row[name], kind) for name, (_, kind) in spec.items()) else None


def _rows(value: object, spec: Mapping[str, tuple[str, type]]) -> list[dict[str, object]] | None:
    """A list whose every element has the spec's fields, or None: one bad element spoils it."""
    if not isinstance(value, list):
        return None
    rows: list[dict[str, object]] = []
    for item in cast(list[object], value):
        row = _fields(item, spec)
        if row is None:
            return None
        rows.append(row)
    return rows


def _names(value: object) -> list[object] | None:
    rows = _rows(value, {"name": ("name", str)})
    return None if rows is None else [row["name"] for row in rows]


def _skill_names(value: object) -> list[object] | None:
    # 2.1.281 sends an object of counts whose skillFrontmatter lists the skills.
    if not isinstance(value, Mapping):
        return None
    return _names(cast(Mapping[str, object], value).get("skillFrontmatter"))


# part → (the context-usage key, its reader). Nothing else in the reply is ever copied: no token
# counts, descriptions or content, and no path but a memory file's.
_SNAPSHOT_PARTS: dict[str, tuple[str, Callable[[object], list[Any] | None]]] = {
    "mcp_tools": (
        "mcpTools",
        lambda v: _rows(
            v, {"name": ("name", str), "server": ("serverName", str), "loaded": ("isLoaded", bool)}
        ),
    ),
    "skills": ("skills", _skill_names),
    "memory_files": (
        "memoryFiles",
        lambda v: _rows(v, {"path": ("path", str), "type": ("type", str)}),
    ),
    "system_tools": ("systemTools", _names),
    "agents": (
        "agents",
        lambda v: _rows(v, {"type": ("agentType", str), "source": ("source", str)}),
    ),
}


def context_snapshot(usage: Mapping[str, Any], observed_at: str) -> dict[str, object]:
    """The inventory parts of a `get_context_usage` reply. A part is a list, or None with its
    reason: `absent` when the reply lacks the key, `unrecognized` when the value is off shape."""
    snapshot: dict[str, object] = {"observed_at": observed_at}
    reasons: dict[str, str] = {}
    for part, (key, read) in _SNAPSHOT_PARTS.items():
        snapshot[part] = read(usage[key]) if key in usage else None
        if snapshot[part] is None:
            reasons[part] = "unrecognized" if key in usage else "absent"
    snapshot["reasons"] = reasons
    return snapshot


MCP_STATUSES = frozenset({"connected", "failed", "needs-auth", "pending", "disabled"})


_NUMERIC_LABEL = re.compile(r"[0-9]+|0[xX][0-9a-fA-F]*")


def _egress(config: object) -> str | None:
    """An http or sse server's `scheme://host[:port]`: never its user, path, query or fragment."""
    if not isinstance(config, Mapping):
        return None
    config = cast(Mapping[str, object], config)
    url = config.get("url")
    # The CLI parses the URL as WHATWG does, which reads `\` as `/` and decodes a percent-encoded
    # or non-ASCII host, where urlsplit does neither; so such a URL shows no egress rather than
    # a host the CLI never contacts.
    if config.get("type") not in ("http", "sse") or not isinstance(url, str) or "\\" in url:
        return None
    # WHATWG strips a C0 control or space from both ends of a URL; urlsplit strips only the
    # leading ones (and drops tab, CR and LF anywhere). A URL with either end shows no egress.
    if url[:1] <= " " or url[-1:] <= " ":
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # an unclosed IPv6 bracket or a port out of range
        return None
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host or "%" in host or not host.isascii():
        return None
    label = host.removesuffix(".").rsplit(".", 1)[-1]  # WHATWG drops one trailing dot first
    if ":" not in host and _NUMERIC_LABEL.fullmatch(label):
        # The CLI reads such a host as an IPv4 number, as WHATWG's URL parser does. A canonical
        # dotted quad is at most 15 characters, so a longer one is never split here.
        if len(host) > 15:
            return None
        try:
            if str(ipaddress.IPv4Address(host)) != host:
                return None
        except ValueError:
            return None
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{host}" + ("" if port is None else f":{port}")


def _server_row(item: object) -> dict[str, object] | None:
    """One server's whitelisted row: a missing optional field or non-string `source` reads None."""
    row = _fields(item, {"name": ("name", str), "status": ("status", str)})
    if row is None or row["status"] not in MCP_STATUSES:
        return None
    source = cast(Mapping[str, object], item)
    scope, info, tools = source.get("scope"), source.get("serverInfo"), source.get("tools")
    # The CLI gives plugin servers the scope `dynamic` too; its `source` tells them apart. It is
    # absent on CLIs that predate the field, so anything but a string reads as absent.
    origin = source.get("source")
    server = (
        {"server_name": None, "server_version": None}
        if info is None
        else _fields(info, {"server_name": ("name", str), "server_version": ("version", str)})
    )
    names = None if tools is None else _names(tools)
    if server is None or not isinstance(scope, str | None) or (tools is not None and names is None):
        return None
    return {
        **row,
        "scope": scope,
        "source": origin if isinstance(origin, str) else None,
        **server,
        "egress": _egress(source.get("config")),
        "tools": names,
    }


def mcp_server_rows(status: Mapping[str, Any]) -> list[dict[str, object]] | None:
    """The rows of an `mcp_status` reply, or None when any row is off shape (a non-string `source`
    only reads None). No `config`, `env`, `headers`, `error`, description or annotation is kept."""
    servers = status.get("mcpServers")
    if not isinstance(servers, list):
        return None
    rows: list[dict[str, object]] = []
    for item in cast(list[object], servers):
        row = _server_row(item)
        if row is None:
            return None
        rows.append(row)
    return rows


def _utc_stamp() -> str:
    """Now, to millisecond precision, Z-suffixed: the 24-character stamp `_context_percent`
    writes, reused for a card's `raised_at`."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _permission_json(tool: str, tool_input: dict[str, Any]) -> str | None:
    """Preflight raw JSON without copying or encoding unbounded strings/containers."""
    size = nodes = 0

    def inspect(value: Any, depth: int) -> bool:
        nonlocal size, nodes
        nodes += 1
        if depth > MAX_PERMISSION_DEPTH or nodes > MAX_PERMISSION_NODES:
            return False
        kind = type(value)
        if kind is str:
            if len(value) > MAX_PERMISSION_BYTES:
                return False
            size += 2
            for char in value:
                code = ord(char)
                size += (
                    2
                    if char in '\\"\b\f\n\r\t'
                    else 6
                    if code < 32 or 127 <= code <= 0xFFFF
                    else 12
                    if code > 0xFFFF
                    else 1
                )
                if size > MAX_PERMISSION_BYTES:
                    return False
        elif kind is dict or kind is list:
            if len(value) > MAX_PERMISSION_NODES:
                return False
            size += 2 + max(0, len(value) - 1) + (len(value) if kind is dict else 0)
            if size > MAX_PERMISSION_BYTES:
                return False
            if kind is dict:
                for key, item in value.items():
                    if type(key) is not str or not inspect(key, depth + 1):
                        return False
                    if not inspect(item, depth + 1):
                        return False
            elif not all(inspect(item, depth + 1) for item in value):
                return False
        elif value is None:
            size += 4
        elif kind is bool:
            size += 4 if value else 5
        elif kind is int:
            if value.bit_length() > MAX_PERMISSION_BYTES * 3:
                return False
            size += len(str(value))
        elif kind is float and math.isfinite(value):
            size += len(repr(value))
        else:
            return False
        return size <= MAX_PERMISSION_BYTES

    value = {"tool": tool, "input": tool_input}
    try:
        if type(tool) is not str or type(tool_input) is not dict or not inspect(value, 0):
            return None
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        return encoded if len(encoded) <= MAX_PERMISSION_BYTES else None
    except ValueError, TypeError, RecursionError:
        return None


class ClaudeDriver:
    def __init__(
        self,
        launch: Launch,
        *,
        mode: str,
        effort: str,
        model: str | None,
        config: DriverConfig,
        emit: Emit,
    ) -> None:
        self._launch = launch
        self._mode = mode
        self._effort = effort
        self._model = model
        self._config = config
        self._emit = emit
        self._cwd = launch.cwd or config.vault  # the session's root, else the vault
        self._frames: asyncio.Queue[tuple[dict[str, Any] | None, int]] = asyncio.Queue(
            maxsize=FRAME_QUEUE_ITEMS + 2
        )
        self._queued_count = 0
        self._queued_bytes = 0
        self._overflowed = False
        self._start_task: asyncio.Task[dict[str, Any]] | None = None
        self._context_task: asyncio.Task[dict[str, Any]] | None = None
        # The last context read's snapshot: the reply's whitelisted parts, or a failed first read's.
        self._context_snapshot: dict[str, object] | None = None
        # The same read's context report (context_report.py); None until the first turn ends.
        self._context_report: dict[str, object] | None = None
        self._control_close_task: asyncio.Task[None] | None = None
        route = config.llm_route
        if model and model.startswith(PREFIX) and (route is None or route.connection.ref != model):
            raise StartError(
                "bad_request", "This model connection is unavailable. Check Providers in Settings."
            )
        argv = build_argv(
            config.cli,
            launch,
            mode=mode,
            effort=effort,
            model=route.connection.model if route else model,
        )
        self._transport = ClaudeTransport(
            argv,
            cwd=self._cwd,
            env={
                **config.env,
                **(route.environment() if route else {}),
                **(config.agent_grant.environment() if config.agent_grant else {}),
            },
            sink=self._enqueue_frame,
            spawner=config.spawner,
            grace_s=config.grace_s,
            poll_s=config.poll_s,
            append_system_prompt=launch.append_system_prompt,
        )
        self._control = ClaudeControl(
            self._transport,
            self._can_use_tool,
            control_timeout_s=config.control_timeout_s,
            hooks=launch.hooks,
        )
        self._mcp_lock = asyncio.Lock()  # one MCP switch or reconnect at a time
        self._pump_task: asyncio.Task[None] | None = None
        self._cards: dict[str, asyncio.Future[Answer]] = {}
        self._visible_cards: set[str] = set()
        # tool_use_id -> (ctx.agent_id or None, raised_at); set before _enqueue_card, popped in
        # _can_use_tool's finally, so it always matches self._cards' own lifetime.
        self._card_meta: dict[str, tuple[str | None, str]] = {}
        # References to inputs already held by pending card coroutines; only a digest is added.
        self._permission_inputs: dict[str, tuple[str, dict[str, Any], bytes]] = {}
        self._agents: dict[str, str] = {}  # task_id (= a subagent's agent_id) → subagent_type
        self._task_tools: dict[str, str] = {}  # task_id -> the launching Task tool's tool_use_id
        self._tool_tasks: dict[str, str] = {}  # tool_use_id -> task_id, kept while the task runs
        self._mode_before_plan = mode if mode in RESTORE_MODES else "default"
        self._busy = False  # a turn is running (sent by us or started by the CLI)
        # Pending steers in write order: added before the write, ended by a lifecycle frame or
        # an exit.
        self._steers: dict[str, None] = {}
        self._steer_turn = False  # the running turn is one a steer's `started` opened
        self._steer_change = asyncio.Event()  # wakes settle_steers on a change it waits for
        # Set once the CLI has started that turn (interrupts work from here on). An event, not a
        # flag, because a stop asked for earlier has to wait for it and then send itself.
        self._in_turn = asyncio.Event()
        self._stop_wanted = False
        self._stop_task: asyncio.Task[None] | None = None  # an armed stop, kept off the GC
        self._killed = False  # the stop had to kill the tree
        # Set by _watch on an assistant frame whose top-level `error` is AUTH_FAILED; _finish_turn
        # reads it once per turn and always clears it, so a success or cancelled turn shows no card.
        self._auth_failed = False
        self._closing = False
        self.alive = False
        # open()'s reply; set_model replaces and re-emits it once the CLI confirms the switch.
        self._started: SessionStarted | None = None
        # The initialize reply's own version (validated) and mode (any string), for start_facts.
        self._reported_version: str | None = None
        self._reported_mode: str | None = None

    @property
    def pid(self) -> int | None:
        return self._transport.pid

    @property
    def busy(self) -> bool:
        return self._busy

    async def inventory(self) -> dict[str, object]:
        """One fresh, bounded MCP status, plus the context snapshot as it stands once that status
        call returns; it may run during a turn. A failed status degrades only `mcp_servers`, to
        None with its reason."""
        if not self.alive:
            # Before the initialize reply, or once the process is gone, no status request goes out.
            rows, status_reason = None, "unavailable"
        else:
            try:
                rows = mcp_server_rows(await self._control.mcp_status())
                status_reason = None if rows is not None else "unrecognized"
            except TimeoutError:
                log.warning("MCP status request timed out")
                rows, status_reason = None, "timeout"
            except Exception:  # a gone process or an error reply; a cancellation still propagates
                rows, status_reason = None, "unavailable"
        snapshot = self._context_snapshot or {
            "observed_at": None,
            **dict.fromkeys(_SNAPSHOT_PARTS),
            "reasons": dict.fromkeys(_SNAPSHOT_PARTS, "not_observed"),
        }
        reasons = dict(cast(Mapping[str, str], snapshot["reasons"]))
        if status_reason is not None:
            reasons["mcp_servers"] = status_reason
        return {**snapshot, "mcp_servers": rows, "reasons": reasons}

    async def context(self) -> dict[str, object]:
        """The context report from the last turn's context read, or a `not_observed` one when no
        turn has ended yet. Makes no request of its own: only a turn's own read ever changes
        this."""
        return self._context_report or failed_report("not_observed")

    async def start_facts(self) -> StartFacts:
        """What the CLI reported when it started: the initialize reply's version and permission
        mode, its menu's values and, only for a launch with an app `--mcp-config`, the names of
        the dynamic servers one fresh, bounded MCP status lists. A timeout, a gone process, an
        error reply or a status without a server list leaves `mcp_servers` None; only a
        cancellation propagates."""
        servers: tuple[str, ...] | None = None
        if self._launch.mcp_config is not None and self.alive:
            try:
                servers = _app_servers(await self._control.mcp_status())
            except TimeoutError:
                log.warning("MCP status request timed out")
            except Exception:  # a gone process or an error reply; a cancellation still propagates
                servers = None
        started = self._started
        return StartFacts(
            version=self._reported_version,
            mode=self._reported_mode,
            models=tuple(option.value for option in started.models) if started else (),
            mcp_servers=servers,
        )

    async def open(self) -> SessionStarted:
        """Spawn claude, pass the billing gate, and describe the session. Raises StartError."""
        try:
            self._start_task = asyncio.create_task(self._control.start())
            info = await self._start_task
        except asyncio.CancelledError as exc:
            await self._close_control()
            if self._overflowed:
                raise StartError("frame_error", MSG_OVERFLOW) from exc
            raise
        except FileNotFoundError as exc:
            await self._close_control()
            raise StartError("cli_missing", MSG_CLI_MISSING) from exc
        except (ProcessError, CLIConnectionError) as exc:
            await self._close_control()
            if self._overflowed:
                raise StartError("frame_error", MSG_OVERFLOW) from exc
            code, message = classify_exit(self._transport.stderr_tail())
            if self._config.llm_route is not None and code == "not_logged_in":
                code = "bad_request"
                message = "Check this model connection in Providers in Settings."
            raise StartError(code, message) from exc
        except Exception as exc:  # the SDK raises a plain Exception for an initialize error
            await self._close_control()
            if self._overflowed:
                raise StartError("frame_error", MSG_OVERFLOW) from exc
            raise StartError("crashed", MSG_CRASHED) from exc
        finally:
            self._start_task = None
        if self._overflowed:
            await self._close_control()
            raise StartError("frame_error", MSG_OVERFLOW)
        try:
            if self._config.llm_route is None and self._config.subscription_only:
                check_billing(info)
        except StartError:
            await self._close_control()
            raise
        self.alive = True
        self._mode = str(info.get("current_permission_mode") or self._mode)  # "manual" → "default"
        reported_mode = info.get("current_permission_mode")
        self._reported_mode = reported_mode if isinstance(reported_mode, str) else None
        self._reported_version = _version_report(info.get("claude_code_version"))
        mapper = Mapper(vault=str(self._cwd), mode=self._mode, artifacts_enabled=True)
        self._pump_task = asyncio.create_task(self._pump(mapper))
        self._started = SessionStarted(
            model=self._model or "default",
            mode=self._mode,
            effort=self._effort,
            commands=menu_commands(info.get("commands") or [], self._config.skills),
            agents=[str(a["name"]) for a in info.get("agents") or [] if a.get("name")],
            models=[
                ModelOption(
                    value=str(m["value"]),
                    label=str(m.get("displayName") or m["value"]),
                    description=str(m.get("description", "")),
                )
                for m in info.get("models") or []
                if m.get("value")
            ],
            cli_version=self._reported_version or self._config.cli_version,
            resumed=self._launch.resume or bool(self._launch.fork_of),
            additional_folders=list(self._launch.add_dirs),
        )
        if route := self._config.llm_route:
            self._started = dataclasses.replace(
                self._started,
                models=[
                    ModelOption(
                        value=route.connection.ref,
                        label=f"{route.connection.name} · {route.connection.model}",
                        description="Custom model connection",
                    )
                ],
            )
        version, observe = self._reported_version, self._config.observe_version
        if version is not None and version != self._config.cli_version and observe is not None:
            try:
                observe(version)
            except Exception:  # a caller's observer must never fail the start
                log.warning("CLI version observer failed")
        return self._started

    async def send_turn(self, turn: TurnInput) -> None:
        await self.send(turn)

    async def send(self, turn: str | TurnInput) -> None:
        if self._overflowed:
            raise SendError("frame_error", MSG_OVERFLOW, unsent=True)
        if self._busy:
            raise RuntimeError("a turn is already running")
        self._busy, self._stop_wanted, self._killed = True, False, False
        self._in_turn.clear()  # the CLI has not picked this message up yet
        try:
            await self._control.send(turn, self._launch.session_id, self._config.resolve_attachment)
        except (CLIConnectionError, ProcessError) as exc:
            self._busy = False
            if self._overflowed:
                raise SendError("frame_error", MSG_OVERFLOW) from exc
            raise
        except AttachmentUnavailable:
            # Nothing was written, so a stop asked for while the images were read has no turn to
            # stop; left armed, it would interrupt or kill whatever turn comes next.
            self._busy = self._stop_wanted = False
            self._cancel_stop()
            raise
        except BaseException:
            self._busy = False
            raise

    async def steer(self, turn: TurnInput, steer_id: str, /) -> None:
        """Write `turn` into the running turn as a steer (`SteerDriver`); `_watch` ends it on
        its command_lifecycle frames. Raises SendError (unsent) once overflowed; otherwise
        AttachmentUnavailable and write failures raise as in `send`, and a failed steer is
        forgotten. It leaves `busy`, `_in_turn` and an armed stop as they are: the running turn
        holds them."""
        if self._overflowed:
            raise SendError("frame_error", MSG_OVERFLOW, unsent=True)
        self._steers[steer_id] = None  # before the write: a lifecycle frame may beat its return
        try:
            await self._control.send(
                turn, self._launch.session_id, self._config.resolve_attachment, steer_id=steer_id
            )
        except BaseException as exc:
            self._steers.pop(steer_id, None)
            if self._overflowed and isinstance(exc, CLIConnectionError | ProcessError):
                raise SendError("frame_error", MSG_OVERFLOW) from exc
            raise

    async def settle_steers(self, timeout_s: float, /) -> None:
        """Before a close (`SteerDriver`): wait at most `timeout_s` until no steer is pending and
        no turn a steer started is running, interrupting each such turn so that it ends
        cancelled (the interrupt goes out once its system/init or status shows the CLI inside
        it). An exit or overflow ends the wait; with no pending steer and no such turn it
        returns at once."""
        try:
            async with asyncio.timeout(timeout_s):
                while self.alive and (self._steers or self._steer_turn):
                    if self._steer_turn and self._busy and not self._stop_wanted:
                        # The CLI runs a waiting steer as its next turn, even after a stop.
                        await self.interrupt()
                    else:
                        self._steer_change.clear()
                        await self._steer_change.wait()
        except TimeoutError:
            pass  # what still waits is the caller's to drop

    async def interrupt(self) -> None:
        """Stop the running turn: now if the CLI is inside it, else as soon as it enters it.
        Asking again changes nothing; an unacknowledged stop kills the tree after the control
        timeout, and so does a CLI that never enters the turn at all."""
        if not self._busy or self._stop_wanted:
            return
        self._stop_wanted = True
        if self._in_turn.is_set():
            task = self._stop_task = asyncio.create_task(self._stop())
            await asyncio.wait({task})
            if not task.cancelled() and task.exception() is not None:
                log.warning("turn stop failed")
        else:
            self._stop_task = asyncio.create_task(self._stop_when_ready())

    def respond(self, tool_use_id: str, answer: Answer) -> bool:
        """Answer an open card. False if no such card is waiting (answered or cancelled)."""
        if self._overflowed:
            return False
        future = self._cards.get(tool_use_id)
        if future is None or future.done():
            return False
        self._permission_inputs.pop(tool_use_id, None)
        future.set_result(answer)
        return True

    def pending(self) -> list[str]:
        return [tool_use_id for tool_use_id, f in self._cards.items() if not f.done()]

    def permission_details(self, tool_use_id: str) -> dict[str, Any] | None:
        """A bounded copy of the unchanged input for this still-pending native future."""
        future = self._cards.get(tool_use_id)
        source = self._permission_inputs.get(tool_use_id)
        if (
            not self.alive
            or self._closing
            or self._overflowed
            or future is None
            or future.done()
            or source is None
        ):
            return None
        tool, tool_input, digest = source
        value = _permission_json(tool, tool_input)
        if value is None or hashlib.sha256(value.encode("ascii")).digest() != digest:
            self._permission_inputs.pop(tool_use_id, None)
            return None
        return cast(dict[str, Any], json.loads(value))

    def requests(self) -> list[dict[str, object]]:
        """Every pending card, in arrival order, with the run (a Task tool's tool_use_id) that
        raised it, or None for the main thread. Resolved at read time: a card's event leaves the
        frame queue after its own `task_started`, so a read here always sees it."""
        if self._overflowed:
            return []
        rows: list[dict[str, object]] = []
        for tool_use_id, future in self._cards.items():
            if future.done():
                continue
            agent_id, raised_at = self._card_meta.get(tool_use_id, (None, ""))
            run = self._task_tools.get(agent_id) if agent_id else None
            rows.append({"tool_use_id": tool_use_id, "run": run, "raised_at": raised_at})
        return rows

    async def stop_run(self, block_id: str) -> None:
        """Stop the run (a Task tool's tool_use_id) that `block_id` names.

        Raises RunStopError: not_running (not alive, closing or overflowed), unknown_run (no
        task is running for this block), timeout (no reply in time), or refused (any other
        control-call failure). A CancelledError propagates; nothing is logged, and a failure
        changes no state.
        """
        if not self.alive or self._closing or self._overflowed:
            raise RunStopError("not_running")
        task_id = self._tool_tasks.get(block_id)
        if task_id is None:
            raise RunStopError("unknown_run")
        try:
            await self._control.stop_task(task_id)
        except TimeoutError:
            raise RunStopError("timeout") from None
        except ProcessError, CLIConnectionError:
            raise RunStopError("not_running") from None
        except Exception:
            raise RunStopError("refused") from None

    async def set_mcp_server(self, name: str, enabled: bool) -> None:
        """Switch the MCP server `name` on or off; the CLI saves it for this chat's folder.

        Raises McpControlError. First, with no frame written: not_running (not alive, closing or
        overflowed), unknown_server (a read-only launch), busy (a turn is running), managed (an
        app server, when this launch has an `--mcp-config`). Then, one call at a time, a fresh
        MCP status: timeout, not_running, or refused (an error reply or an off-shape status);
        unknown_server if it does not list `name`; busy if a turn began meanwhile. Then the call,
        bounded by MCP_TOGGLE_TIMEOUT_S: timeout, not_running, not_approved, blocked, or for
        any other error reply refused (a disable) or not_connected (an enable: the CLI saved it
        before it tried to connect). A CancelledError propagates; nothing is logged, and no
        CLI text is kept.
        """
        await self._mcp_call(name, enabled)

    async def reconnect_mcp_server(self, name: str) -> None:
        """Reconnect the MCP server `name`; raises as `set_mcp_server` does for an enable."""
        await self._mcp_call(name, None)

    async def set_model(self, model: str) -> None:
        route = self._config.llm_route
        if route is not None:
            if model != route.connection.ref:
                raise ValueError("start_a_new_chat_to_change_connection")
            native = route.connection.model
        elif model.startswith(PREFIX):
            raise ValueError("start_a_new_chat_to_change_connection")
        else:
            native = model
        await self._control.set_model(native)
        self._model = model
        if self._started is not None:
            self._started = dataclasses.replace(self._started, model=model, mode=self._mode)
            self._emit(self._started)

    async def set_permission_mode(self, mode: str) -> None:
        """Ask the CLI to switch the permission mode.

        Probed with CLI 2.1.282 and SDK 0.2.159 (2026-10-20): each switch to auto, acceptEdits,
        plan or default is answered by a status frame carrying permissionMode, and "manual"
        while already in default sends none. The mode and the stored prefs follow that frame.
        """
        await self._control.set_permission_mode(mode)

    async def close(self) -> None:
        """End the process gracefully; claude flushes its transcript first."""
        self._closing = True
        self._permission_inputs.clear()
        await self._settle_stop()
        self._cancel_stop()
        for future in self._cards.values():
            future.cancel()
        await self._close_control()
        if self._pump_task:
            await asyncio.wait({self._pump_task}, timeout=self._config.grace_s)
            self._pump_task.cancel()
        self.alive = False
        self._discard_frames()

    # --- internals ------------------------------------------------------------------------------

    def _enqueue_frame(self, frame: dict[str, Any], source_bytes: int) -> None:
        """Admission never awaits capacity, so output cannot hold the SDK control reader."""
        if frame.get("type") == EXIT:
            self._frames.put_nowait((frame, 0))
        elif not self._overflowed:
            if frame.get("type") == FRAME_ERROR and frame.get("reason") == "too_large":
                self._overflow()
                return
            if (
                self._queued_count >= FRAME_QUEUE_ITEMS
                or self._queued_bytes + source_bytes > FRAME_QUEUE_BYTES
            ):
                self._overflow()
            else:
                self._queued_count += 1
                self._queued_bytes += source_bytes
                self._frames.put_nowait((frame, source_bytes))

    def _overflow(self) -> None:
        if self._overflowed:
            return
        self._overflowed = True
        self._permission_inputs.clear()
        # A permission reply may already be waiting on the stdin lock. Revoke readiness
        # synchronously so that no queued allow/user write can cross this terminal boundary.
        # SDK disconnect still belongs to open()/the pump, never to its reader callback.
        self._transport.abort()
        self._frames.put_nowait((None, 0))  # private sentinel, never a provider-controlled type
        # These are the pump/start's only long waits. Cancelling the owned calls wakes the
        # lifecycle owner; never close the SDK from inside its stdout reader or card callback.
        for task in (self._start_task, self._context_task):
            if task is not None:
                task.cancel()

    def _enqueue_card(self, event: Event) -> None:
        size = len(json.dumps(event.data(), ensure_ascii=True, separators=(",", ":")))
        self._enqueue_frame({"type": CARD, "event": event}, size)

    def _discard_frames(self) -> None:
        while not self._frames.empty():
            self._frames.get_nowait()
        self._queued_count = self._queued_bytes = 0

    async def _close_control(self) -> None:
        if self._config.llm_route is not None:
            self._config.llm_route.revoke()
        if self._config.agent_grant is not None:
            self._config.agent_grant.revoke()
        # Owner close can race overflow/exit. Share one SDK disconnect; abort is idempotent.
        if self._control_close_task is None:
            self._control_close_task = asyncio.create_task(self._control.close())
        await self._control_close_task

    def _on_overflow(self) -> None:
        self.alive = False
        self._steer_change.set()
        self._closing = True
        self._permission_inputs.clear()
        self._cancel_stop()
        self._in_turn.clear()
        for future in self._cards.values():
            if not future.done():
                future.cancel()
        for tool_use_id in self._visible_cards:
            self._emit(RequestCancelled(tool_use_id=tool_use_id))
        self._visible_cards.clear()
        self._cards.clear()
        if self._busy:
            self._busy = self._stop_wanted = False
            self._emit(TurnDone(status="error", usage=Usage()))
        self._emit(Error(code="frame_error", message=MSG_OVERFLOW, recoverable=False))

    async def _pump(self, mapper: Mapper) -> None:
        """Frames → mapper → emit, in order. Ends with the process."""
        while True:
            frame, source_bytes = await self._frames.get()
            if frame is None:
                self._on_overflow()
                try:
                    await self._close_control()
                finally:
                    self._discard_frames()
                return
            kind = frame.get("type")
            if kind != EXIT:
                self._queued_count -= 1
                self._queued_bytes -= source_bytes
            if self._overflowed:
                continue  # once output is missing, no queued result may claim success
            if kind == EXIT:
                self._on_exit(str(frame.get("stderr") or ""))
                if not self._closing:  # a crash or a stop-kill: release what close() would have
                    self._closing = True
                    await self._close_control()
                return
            if kind == CARD:
                event = frame["event"]
                if isinstance(event, PermissionRequested | QuestionAsked):
                    self._visible_cards.add(event.tool_use_id)
                elif isinstance(event, RequestAnswered | RequestCancelled):
                    self._visible_cards.discard(event.tool_use_id)
                self._emit(event)
                continue
            try:
                await self._handle(frame, mapper)
            except MapperOverflow:
                self._overflow()
            except Exception as exc:  # a frame we cannot map must not end the session
                # The traceback, not just the class name: the CLI writing a shape the mapper was
                # not written against is this app's likeliest break, and a bare "KeyError" against
                # a 680-line mapper names nothing. It costs nothing under hard rule 8 — a
                # Formatter renders no locals, and the only exception text this path can carry is
                # a key name or a malformed numeric field (int() on a token count), never the
                # prompt or the frame.
                log.warning(
                    "frame %s/%s failed: %s",
                    kind,
                    frame.get("subtype"),
                    type(exc).__name__,
                    exc_info=True,
                )
                self._emit(Error(code="internal", message=MSG_INTERNAL, recoverable=True))
                if kind == "result" and self._busy:
                    self._finish_turn(TurnDone(status="error", usage=Usage()))

    async def _handle(self, frame: dict[str, Any], mapper: Mapper) -> None:
        self._watch(frame)
        for event in mapper.frame(frame):
            if isinstance(event, Status) and event.mode:
                self._note_mode(event.mode)
            if isinstance(event, TurnDone):
                percent = await self._context_percent()
                if not self._overflowed:
                    self._finish_turn(dataclasses.replace(event, context_percent=percent))
            else:
                self._emit(event)

    def _finish_turn(self, done: TurnDone) -> None:
        self._busy = self._stop_wanted = self._steer_turn = False
        self._in_turn.clear()
        self._cancel_stop()  # the turn ended by itself; an armed stop must not outlive it
        self._emit(done)  # turn.done first, so the browser shows the sign-in card, not a notice
        route = self._config.llm_route
        if route is not None and route.failure_code is not None and done.status == "error":
            self._emit(
                Error(
                    code="bad_request",
                    message="Close and reopen this chat to retry.",
                    recoverable=False,
                )
            )
        elif self._auth_failed and done.status == "error":
            self._emit(
                Error(
                    code="bad_request" if self._config.llm_route else "not_logged_in",
                    message="Check this model connection in Providers in Settings."
                    if self._config.llm_route
                    else MSG_LOGIN,
                    recoverable=True,
                )
            )
        self._auth_failed = False  # a success or cancelled turn shows no card; the CLI stays up
        self._steer_change.set()

    def _watch(self, frame: dict[str, Any]) -> None:
        """Protocol facts the mapper does not turn into events."""
        kind = frame.get("type")
        if kind == "assistant" and frame.get("error") == AUTH_FAILED:
            # Main thread or subagent, either carries it the same way; _finish_turn reports this
            # once the turn ends, however many flagged frames arrived (or whatever text, if any,
            # the mapper made of the same frame).
            self._auth_failed = True
        if kind == "rate_limit_event":
            info = frame.get("rate_limit_info")
            observe = self._config.observe_limits
            if observe is not None and isinstance(info, Mapping):
                now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                reading = plan_limit_reading(info, now)
                if reading is not None:
                    try:
                        observe(reading)
                    except Exception:  # a caller's observer must never end the pump
                        log.warning("plan limit observer failed")
            return
        if kind == "command_lifecycle":
            self._steer_lifecycle(frame.get("command_uuid"), frame.get("state"))
        if kind != "system":
            return
        subtype = frame.get("subtype")
        if subtype == "init" and not self._busy:
            # system/init opens every turn, so one while idle is a turn the CLI started itself
            # (a background agent finished). No user.message: the user sent nothing.
            self._in_turn.set()
            self._cli_turn()
        elif subtype in ("init", "status") and self._busy and not self._in_turn.is_set():
            self._in_turn.set()  # the CLI has picked up our message: an armed stop goes out now
        elif subtype == "task_started" and frame.get("task_id"):
            task_id = str(frame["task_id"])
            self._agents[task_id] = str(frame.get("subagent_type") or self._agents.get(task_id, ""))
            tool_use_id = frame.get("tool_use_id")
            if isinstance(tool_use_id, str) and tool_use_id:
                self._task_tools[task_id] = tool_use_id
                self._tool_tasks[tool_use_id] = task_id
        elif subtype == "task_notification":
            self._drop_tool_task(frame.get("task_id"))
        elif subtype == "task_updated":
            patch = frame.get("patch")
            status = patch.get("status") if isinstance(patch, Mapping) else None
            if status in TERMINAL_TASK_STATUSES:
                self._drop_tool_task(frame.get("task_id"))
            elif status == "running":
                task_id = frame.get("task_id")
                if isinstance(task_id, str):
                    tool_use_id = self._task_tools.get(task_id)
                    if tool_use_id is not None:
                        self._tool_tasks[tool_use_id] = task_id

    def _cli_turn(self) -> None:
        """Open a turn the CLI started with no `send`: one from system/init while idle, or a
        steer's `started` after the turn it was written into has ended. The caller sets
        `_in_turn`: only a system/init or status frame shows the CLI inside the turn."""
        self._busy, self._stop_wanted, self._killed = True, False, False
        self._emit(TurnStarted(turn_id=str(uuid.uuid4())))

    def _steer_lifecycle(self, steer_id: object, state: object) -> None:
        """End a pending steer on its command_lifecycle frame. `started` ends it as started and,
        when no turn is running any more, opens the turn the CLI runs it in, which its own
        system/init or status then marks as entered; `completed` without `started` ends it as
        started and opens nothing; cancelled, discarded or refused drop it. Any other state, or
        an id that is not a pending steer, changes nothing."""
        if not isinstance(steer_id, str) or steer_id not in self._steers:
            return
        if state in ("started", "completed"):
            del self._steers[steer_id]
            self._emit(SteerEnded(steer_id=steer_id, outcome="started"))
            if state == "started" and not self._busy:
                # This frame comes before the turn's own system/init, so `_in_turn` stays clear
                # (as between turns) until that frame or a status sets it. A stop asked meanwhile
                # waits armed, as for a sent message: an earlier interrupt may be acknowledged
                # without cancelling.
                self._cli_turn()
                self._steer_turn = True
        elif state in ("cancelled", "discarded", "refused"):
            del self._steers[steer_id]
            self._emit(SteerEnded(steer_id=steer_id, outcome="dropped"))
        self._steer_change.set()

    def _drop_tool_task(self, task_id: object) -> None:
        """`_tool_tasks` holds a task only while it runs; this ends its entry once it is not."""
        if isinstance(task_id, str) and task_id:
            tool_use_id = self._task_tools.get(task_id)
            if tool_use_id is not None:
                self._tool_tasks.pop(tool_use_id, None)

    async def _stop_when_ready(self) -> None:
        """Wait for system/init or status before sending an early interrupt, then kill the
        process tree if readiness exceeds `stop_pickup_s`. CLI 2.1.261 added cancellation for
        interrupts before turn start, but waiting still avoids interrupting a turn that has not
        been accepted. The long deadline leaves room for hooks and MCP startup."""
        try:
            async with asyncio.timeout(self._config.stop_pickup_s):
                await self._in_turn.wait()
        except TimeoutError:
            if self._stop_task is asyncio.current_task() and self._busy and self._stop_wanted:
                self._killed = True  # the user's stop, not a spontaneous crash
                self._transport.kill()
            return
        await self._stop()

    async def _settle_stop(self) -> None:
        """Hold stdin open a moment for an armed stop that has not gone out yet.

        Closing straight through one was this driver's worst case: no interrupt had been sent,
        stdin shut under a turn the CLI was about to run at full speed, and the grace ran out on
        the whole tree — claude, its hooks, and whatever tool it had started — so a Write could
        be cut in half or a `git add` left without its commit. Waiting makes an early close
        behave like a close inside the turn: ask first, kill only if the ask goes unanswered.
        `stop_flush_s` is short because Close must not hang on a CLI that never starts; past it
        the shutdown goes on and the kill is all that is left.
        """
        if self._stop_task is not None and not self._stop_task.done():
            await asyncio.wait({self._stop_task}, timeout=self._config.stop_flush_s)

    def _cancel_stop(self) -> None:
        """Disarm a waiting stop. Its deadline outliving the turn it belongs to would kill a
        process that has already moved on."""
        task, self._stop_task = self._stop_task, None
        if task is not None:
            task.cancel()

    async def _stop(self) -> None:
        try:
            await self._control.interrupt()
        except ProcessError, CLIConnectionError:
            pass  # already gone; the exit is reported by the pump
        except Exception:  # not acknowledged (timeout or an error reply): the last layer
            if self._stop_task is asyncio.current_task() and self._busy and self._stop_wanted:
                self._killed = True
                self._transport.kill()

    async def _mcp_call(self, name: str, enabled: bool | None) -> None:
        """`set_mcp_server`'s checks and call; `enabled` None reconnects."""
        if not self.alive or self._closing or self._overflowed:
            raise McpControlError("not_running")
        if self._launch.read_only_tools:  # --strict-mcp-config and no --mcp-config: no server
            raise McpControlError("unknown_server")
        if self._busy:
            raise McpControlError("busy")
        if self._launch.mcp_config is not None and name.startswith(SERVER_PREFIX):
            raise McpControlError("managed")
        async with self._mcp_lock:
            try:
                rows = mcp_server_rows(await self._control.mcp_status())
            except TimeoutError:
                raise McpControlError("timeout") from None
            except ProcessError, CLIConnectionError:
                raise McpControlError("not_running") from None
            except Exception:
                raise McpControlError("refused") from None
            if rows is None:
                raise McpControlError("refused")
            if all(row["name"] != name for row in rows):
                raise McpControlError("unknown_server")
            if self._busy:  # a turn began while this call waited for the lock or the status
                raise McpControlError("busy")
            try:
                if enabled is None:
                    await self._control.reconnect_mcp_server(name)
                else:
                    await self._control.toggle_mcp_server(name, enabled)
            except TimeoutError:
                raise McpControlError("timeout") from None
            except ProcessError, CLIConnectionError:
                raise McpControlError("not_running") from None
            except Exception as error:  # an error reply: the SDK raises Exception(<CLI text>)
                text = str(error)
                if "is not approved for this project" in text:
                    raise McpControlError("not_approved") from None
                if "is blocked by enterprise managed policy" in text:
                    raise McpControlError("blocked") from None
                # An enable saved its choice before it tried to connect: only a disable is refused.
                raise McpControlError("refused" if enabled is False else "not_connected") from None

    async def _context_percent(self) -> int | None:
        now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        try:
            self._context_task = asyncio.create_task(self._control.context_usage())
            usage = await self._context_task
        except asyncio.CancelledError:
            if self._overflowed:
                return None
            raise
        except Exception as error:  # timeouts, a gone process, or an error reply: optional
            # Tools then says why nothing was observed; a later failure keeps what is there.
            reason = "timeout" if isinstance(error, TimeoutError) else "unavailable"
            if self._context_snapshot is None:
                self._context_snapshot = {
                    "observed_at": now,
                    **dict.fromkeys(_SNAPSHOT_PARTS),
                    "reasons": dict.fromkeys(_SNAPSHOT_PARTS, reason),
                }
            if self._context_report is None:
                self._context_report = failed_report(reason)
            return None
        finally:
            self._context_task = None
        self._context_snapshot = context_snapshot(usage, now)
        self._context_report = context_report(usage, now)
        percent = usage.get("percentage")
        return int(percent) if isinstance(percent, int | float) else None

    def _note_mode(self, mode: str) -> None:
        if mode == "plan" and self._mode != "plan":
            self._mode_before_plan = self._mode if self._mode in RESTORE_MODES else "default"
        self._mode = mode

    def _on_exit(self, stderr: str) -> None:
        self.alive = False
        # The key dies with its process even when a cancelled close never reached
        # _close_control, which _closing then keeps the pump from calling.
        if self._config.agent_grant is not None:
            self._config.agent_grant.revoke()
        self._steer_change.set()
        self._permission_inputs.clear()
        self._cancel_stop()  # nothing left to stop; a pending kill would outlive the process
        for tool_use_id, future in list(self._cards.items()):
            future.cancel()
            if not self._closing:
                self._emit(RequestCancelled(tool_use_id=tool_use_id))
        if self._closing:
            return
        for steer_id in self._steers:  # in write order
            self._emit(SteerEnded(steer_id=steer_id, outcome="dropped"))
        self._steers.clear()
        if self._busy:
            self._busy = False
            self._emit(TurnDone(status="cancelled" if self._killed else "error", usage=Usage()))
        # forced_stop: the user asked for this (a kill the app chose), never a crash. Otherwise
        # the process ended on its own; classify_exit reads stderr for what to tell the user.
        code, message = ("forced_stop", MSG_STOP_KILLED) if self._killed else classify_exit(stderr)
        self._emit(Error(code=code, message=message, recoverable=True))

    # --- cards ----------------------------------------------------------------------------------

    async def _can_use_tool(
        self, tool: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> PermissionResult:
        tool_use_id = ctx.tool_use_id or ""
        if self._overflowed or self._closing:
            return PermissionResultDeny(message="The session is ending after an intake failure.")
        if tool_use_id in self._cards:
            return PermissionResultDeny(
                message="A request with this tool-use ID is already pending."
            )
        if len(self._cards) >= MAX_PENDING_CARDS:
            self._overflow()
            return PermissionResultDeny(message="The session reached its pending-request limit.")
        if (
            tool in ("Bash", "PowerShell")
            and len(str(tool_input.get("command", ""))) > CARD_REST_LIMIT
        ):
            self._enqueue_card(RequestCancelled(tool_use_id=tool_use_id))
            self._enqueue_card(
                Error(code="bad_request", message=MSG_COMMAND_LIMIT, recoverable=False)
            )
            return PermissionResultDeny(message=MSG_COMMAND_LIMIT)
        details = _permission_json(tool, tool_input)
        card = self._card(tool, tool_input, ctx)
        future: asyncio.Future[Answer] = asyncio.get_running_loop().create_future()
        self._cards[tool_use_id] = future
        self._card_meta[tool_use_id] = (ctx.agent_id or None, _utc_stamp())
        if details is not None:
            self._permission_inputs[tool_use_id] = (
                tool,
                tool_input,
                hashlib.sha256(details.encode("ascii")).digest(),
            )
        del details  # Retain no serialized input while the native request awaits its answer.

        def forget(done: asyncio.Future[Answer]) -> None:
            if self._cards.get(tool_use_id) is done:
                self._permission_inputs.pop(tool_use_id, None)

        future.add_done_callback(forget)
        try:
            self._enqueue_card(card)
            answer = await future
        except asyncio.CancelledError:
            # The CLI withdrew the request (interrupt), or the process is ending.
            if self.alive and not self._closing:
                self._enqueue_card(RequestCancelled(tool_use_id=tool_use_id))
            raise
        else:
            # Say that the card is closed, in the stream, so that every view of this session
            # learns it and not just the window that answered. It goes through the frame queue
            # rather than self._emit for the same reason the cancellation does: the queue is what
            # keeps card events ordered against the tool rows around them.
            self._enqueue_card(RequestAnswered(tool_use_id=tool_use_id))
        finally:
            self._cards.pop(tool_use_id, None)
            self._card_meta.pop(tool_use_id, None)
            self._permission_inputs.pop(tool_use_id, None)
        if self._overflowed:
            return PermissionResultDeny(
                message="The session ended before this answer was delivered."
            )
        return self._decide(card, tool_input, answer)

    def _card(
        self, tool: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
    ) -> QuestionAsked | PermissionRequested:
        tool_use_id = ctx.tool_use_id or ""
        agent = self._agents.get(ctx.agent_id or "", "subagent") if ctx.agent_id else ""
        if tool == "AskUserQuestion" and not agent:
            return QuestionAsked(tool_use_id=tool_use_id, questions=_questions(tool_input))
        if tool == "ExitPlanMode":
            plan = str(tool_input.get("plan") or "")
            return PermissionRequested(
                tool_use_id=tool_use_id, kind="plan", tool=tool, summary="", agent=agent, plan=plan
            )
        # Shell commands keep their original whitespace and quotes; _can_use_tool refuses
        # one that cannot fit in full. Other tools retain the larger permission-card row.
        row = tool_row(tool, tool_input, str(self._cwd), limit=CARD_REST_LIMIT)
        return PermissionRequested(
            tool_use_id=tool_use_id,
            kind="tool",
            tool=tool,
            # The card draws the tool's name itself, in its own colour (a `WebFetch`
            # span, then the domain). Repeating `head` when it IS the tool name printed it
            # twice — "WebFetch WebFetch example.com". A shell's full command stays verbatim.
            summary=(
                str(tool_input.get("command", ""))
                if tool in ("Bash", "PowerShell")
                else row.rest
                if row.head == tool
                else f"{row.head} {row.rest}".strip()
            ),
            question=tool_question(tool),
            reason=ctx.decision_reason or ctx.title or "",
            agent=agent,
        )

    def _decide(
        self, card: QuestionAsked | PermissionRequested, tool_input: dict[str, Any], answer: Answer
    ) -> PermissionResult:
        if isinstance(card, QuestionAsked):
            if isinstance(answer, QuestionAnswer) and answer.answers:
                return PermissionResultAllow(
                    updated_input={**tool_input, "answers": answer.answers}
                )
            reply = answer.response if isinstance(answer, QuestionAnswer) else ""
            return PermissionResultDeny(message=reply or "The owner did not choose an answer.")
        if isinstance(answer, PermissionAnswer) and not answer.allow and answer.stop_at_draft:
            return PermissionResultDeny(message=MSG_STOP_AT_DRAFT)
        allowed = isinstance(answer, PermissionAnswer) and answer.allow
        if card.kind == "plan":
            if not allowed:
                return PermissionResultDeny(message="The owner wants to keep planning.")
            mode = cast(PermissionMode, self._mode_before_plan)  # one of RESTORE_MODES
            restore = PermissionUpdate(type="setMode", mode=mode, destination="session")
            return PermissionResultAllow(updated_permissions=[restore])
        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="The owner denied this request.")


def _questions(tool_input: Mapping[str, Any]) -> list[Question]:
    return [
        Question(
            header=str(q.get("header", "")),
            question=str(q.get("question", "")),
            options=[
                QuestionOption(
                    label=str(o.get("label", "")), description=str(o.get("description", ""))
                )
                for o in q.get("options") or []
            ],
            multi_select=bool(q.get("multiSelect", False)),
        )
        for q in tool_input.get("questions") or []
    ]
