"""The write fence: PreToolUse hooks every Bot-bound session runs with (ADR-089).

Claude Code calls two hooks of the app's before a Bot session's tool runs, through the SDK's
initialize request, so they hold in every permission mode a Bot runs in:

- The taint hook (`TAINT_MATCHER`) records a session as tainted (`bot_taint`) before its first
  WebFetch, WebSearch or MCP tool call runs, and lets the call go on to its permission card.
- The fence hook (`WRITE_MATCHER`) refuses a Write, Edit, MultiEdit or NotebookEdit whose target
  is Bot memory (`<vault>/bot-memory/`, `<data>/bot-memory/`) or the owner's memory records
  (`<vault>/memory/`) in any session, and, in a tainted one, also the vault's learned memory
  (`system/memory/`), anything under a `.claude` folder and an instruction file (`CLAUDE.md`,
  `CLAUDE.local.md`, `AGENTS.md`, `AGENTS.override.md`, `GEMINI.md`) at any depth.

A path is compared after resolving links and with letter case folded. On Windows, that
comparison alone is not enough: for a file that does not yet exist, `realpath` keeps a literal
spelling NTFS would rewrite on creation (an alternate-data-stream name such as
`CLAUDE.local.md::$DATA`, or a trailing dot or space such as `CLAUDE.md.`), which can land on a
rule's exact name without ever spelling it. So, on Windows, any target whose raw or resolved path
has such a component is refused outright, rule or not. A hook that fails refuses the write rather
than let it through. The hooks see only the file tools: a Bash command that writes a file is not
stopped here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, cast

from anyio.to_thread import run_sync
from claude_agent_sdk import HookContext, HookInput, HookJSONOutput, HookMatcher
from claude_agent_sdk.types import HookEvent

from viki_agent.messaging.bot_taint import taint_source

log = logging.getLogger(__name__)

WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
WRITE_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"
TAINT_MATCHER = "WebFetch|WebSearch|mcp__.*|ListMcpResourcesTool|ReadMcpResourceTool"
INSTRUCTION_NAMES = frozenset(
    {"claude.md", "claude.local.md", "agents.md", "agents.override.md", "gemini.md"}
)

MSG_BOT_MEMORY = (
    "VIKI Agent keeps Bot memory itself, so Bots don't write memory files. Say in your reply "
    "what should be remembered; VIKI Agent asks the owner before it keeps anything."
)
MSG_RECORDS = (
    "memory/ holds the owner's approved memory records. Only VIKI Agent writes them, after the "
    "owner approves."
)
MSG_TAINTED = (
    "This session read web or MCP content, so it can't change instruction, skill or memory "
    "files. Ask the owner to make this change."
)
MSG_SPELLING = (
    "VIKI Agent refuses this file name spelling: a stream name, or a trailing dot or space. Use "
    "the plain file name."
)
MSG_FAILED = "VIKI Agent couldn't check this write, so it was refused. Try again."


@dataclass(frozen=True)
class FenceRoots:
    """Where a Bot session runs and the folders the fence keeps: its working folder (the vault),
    the vault, and the app's own Bot memory area (`<data>/bot-memory`)."""

    cwd: Path
    vault: Path
    app_memory: Path


def _target(tool_input: Mapping[str, Any]) -> str | None:
    value = tool_input.get("file_path", tool_input.get("notebook_path"))
    return value if isinstance(value, str) and value and "\x00" not in value else None


def _key(path: Path | str) -> str:
    return os.path.realpath(path).casefold().rstrip("\\/")


def _within(path: str, root: Path) -> bool:
    base = _key(root)
    return path == base or path.startswith(base + os.sep.casefold()) or path.startswith(base + "/")


def _windows_spelling_problem(raw: str) -> bool:
    """Whether `raw`, read as a Windows path, has a component NTFS would rewrite on creation: a
    stream name (a colon past the drive or UNC/``\\\\?\\`` anchor) or a trailing dot or space.
    `.` and `..` themselves are not flagged. A pure function over `PureWindowsPath`, so it can be
    tested on every platform; `fence_reason` calls it only when `os.name == "nt"`."""
    pure = PureWindowsPath(raw)
    parts = pure.parts[1:] if pure.anchor else pure.parts
    return any(
        part not in (".", "..") and (":" in part or part.endswith((".", " "))) for part in parts
    )


def fence_reason(
    tool: object, tool_input: Mapping[str, Any], roots: FenceRoots, *, tainted: bool
) -> str | None:
    """Why the fence refuses this call, or None to let it go on to its permission card."""
    if tool not in WRITE_TOOLS:
        return None
    raw = _target(tool_input)
    if raw is None:
        return None
    path = Path(raw)
    resolved = _key(path if path.is_absolute() else roots.cwd / path)
    if os.name == "nt" and (_windows_spelling_problem(raw) or _windows_spelling_problem(resolved)):
        return MSG_SPELLING
    if _within(resolved, roots.vault / "bot-memory") or _within(resolved, roots.app_memory):
        return MSG_BOT_MEMORY
    if _within(resolved, roots.vault / "memory"):
        return MSG_RECORDS
    if not tainted:
        return None
    parts = Path(resolved).parts
    if (
        _within(resolved, roots.vault / "system" / "memory")
        or ".claude" in parts
        or (parts and parts[-1] in INSTRUCTION_NAMES)
    ):
        return MSG_TAINTED
    return None


def _deny(reason: str) -> HookJSONOutput:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


class WriteFence:
    """One Bot session's hooks. `tainted` reads the session's taint as stored (its Bot thread's
    included); `mark` stores a new source. Both block, so the hooks call them in a thread."""

    def __init__(
        self,
        roots: FenceRoots,
        *,
        tainted: Callable[[], bool],
        mark: Callable[[str], None],
    ) -> None:
        self.roots = roots
        self._tainted = tainted
        self._mark = mark
        self._seen: set[str] = set()

    def hooks(self) -> dict[HookEvent, list[HookMatcher]]:
        """The `hooks` value of this session's `ClaudeAgentOptions`."""
        return {
            "PreToolUse": [
                HookMatcher(matcher=TAINT_MATCHER, hooks=[self.observe]),
                HookMatcher(matcher=WRITE_MATCHER, hooks=[self.fence]),
            ]
        }

    def is_tainted(self) -> bool:
        return bool(self._seen) or self._tainted()

    async def observe(
        self, input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        source = taint_source(cast(Mapping[str, Any], input_data).get("tool_name"))
        if source is None or source in self._seen:
            return {}
        # Held here first, so a write later in this process is fenced even if storing fails.
        self._seen.add(source)
        try:
            await run_sync(self._mark, source)
        except Exception as error:
            log.warning("Bot taint was not stored: %s", type(error).__name__)
        return {}

    async def fence(
        self, input_data: HookInput, tool_use_id: str | None, context: HookContext
    ) -> HookJSONOutput:
        data = cast(Mapping[str, Any], input_data)
        tool_input = data.get("tool_input")
        if not isinstance(tool_input, Mapping):
            return {}
        try:
            tainted = await run_sync(self.is_tainted)
            reason = fence_reason(
                data.get("tool_name"),
                cast(Mapping[str, Any], tool_input),
                self.roots,
                tainted=tainted,
            )
        except Exception as error:
            log.warning("Bot write fence failed: %s", type(error).__name__)
            return _deny(MSG_FAILED)
        return {} if reason is None else _deny(reason)


FenceResolver = Callable[[str], Awaitable[WriteFence | None]]
