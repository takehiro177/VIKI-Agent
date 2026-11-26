"""Live capture: run scripted scenarios against the real claude.exe and record every frame.

    uv run --directory <repo> python scripts/capture.py            # not refused or named-only
    uv run --directory <repo> python scripts/capture.py 01 06 08   # selected ones
    uv run --directory <repo> python scripts/capture.py --list

Each run seeds its own scratch folder, <run>\\scratch, with a few synthetic files (`seed_scratch`)
and never uses the vault. Each scenario starts its own session (fresh UUID) there with
scripted, read-only prompts, at sonnet / effort low unless it says otherwise. Every permission
request a scenario does not explicitly expect is denied, and a shell tool is only ever approved
for the exact command the scenario asked for (`expect_exact`).

What it reads: the user-level and managed settings, for the billing preflight (the scratch
folder has no .claude of its own); its own sessions' transcripts under
~/.claude/projects/<scratch slug> (scenario 43: its vault folders' slugs); and the provider
config folder the billing preflight scans, %APPDATA%\\Anthropic on Windows
(config._anthropic_dir names it per OS). What it writes: only the
run folder, %LOCALAPPDATA%\\viki-agent\\captures\\<run>. Raw frames go there as <nn>.jsonl, one
{scenario, dir, t_ms, frame} per line; observations and pass/fail go to summary.json beside them;
the run folder also holds a --mcp-config scenario's generated config file and the synthetic
stdio server script it names. Raw captures are never committed; only scrubbed fixtures of these
sessions may be (scripts/scrub_fixture.py --vault <run>\\scratch).

Scenario 41 (named only) is five paid turns whose Bot memory `--settings` name a folder under
<run>\\data, outside the scratch folder. It answers only a Read, Glob, Grep, Write, Edit or
MultiEdit request whose every path is inside that turn's memory folder (the write tools only in
a save turn), records each request's tool and whether it was inside, and keeps no path or model
text in its observations.

Scenario 43 (named only) is paid turns in two throwaway vault folders it seeds in the run folder,
beside the scratch folder, each named with a space: one whose CLAUDE.md imports an AGENTS.md that
holds a canary, one with an AGENTS.md alone. Each turn is a fresh session started in one of them
with a Bot settings file from `bot_memory`, with or without its `claudeMdExcludes`. It denies
every permission request, looks for the canary in the turn's frames and in that session's own
transcript, and keeps no path or model text in its observations.

Scenario 40 runs alone, only in a clean Linux guest signed in to the subscription in its own
default home (`_guest_refusal`). It writes no raw frames: besides the synthetic repository, code
copy and settings it seeds in its run folder, it writes only summary.json, built from a
whitelist (`_evidence_40`).

`REFUSED` scenarios stay out of a scratch run: they test the vault's own hooks or skills, fork
one of the user's sessions, or read their own process's file in the CLI's session registry,
~/.claude/sessions, outside the scratch folder. Their code stays as the record of how 2.1.259
was proven, and naming one refuses the whole run before anything starts.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import shlex
import socket
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any

import uvicorn
from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ToolPermissionContext,
)
from scrub_fixture import DO_NOT_COMMIT  # the sibling in scripts/, which is sys.path[0] here
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from viki_agent import catalog, config, protection, winproc
from viki_agent.code.worktrees import worktree_area
from viki_agent.drivers.base import StartError
from viki_agent.drivers.claude.control import ClaudeControl, Launch, build_argv, login_argv
from viki_agent.drivers.claude.driver import check_billing
from viki_agent.drivers.claude.transport import (
    EXIT,
    GRACE_S,
    MAX_FRAME,
    POLL_S,
    ClaudeTransport,
    Sink,
    Spawner,
    amend_initialize,
)
from viki_agent.llm.connections import Connection
from viki_agent.llm.gateway import Gateway, finish_cleanup
from viki_agent.llm.protocols import event
from viki_agent.messaging.bot_fence import MSG_RECORDS, FenceRoots, WriteFence
from viki_agent.messaging.bot_memory import (
    SETTINGS_FOLDER,
    app_memory_directory,
    memory_ref,
    write_settings,
)
from viki_agent.messaging.bot_memory_rule import bot_system_prompt
from viki_agent.profiles import mcp_config
from viki_agent.profiles.profile_capabilities import FIXTURE_VERSION

Frame = dict[str, Any]
Answer = PermissionResultAllow | PermissionResultDeny
TURN_TIMEOUT_S = 240.0
VSCODE_CLI_GLOB = ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe"
_ROUTE_ENV_KEYS = frozenset(
    {
        "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
    }
)
_SCRATCH_ENV_KEYS = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "TMP",
    "TEMP",
    "TMPDIR",
    "CLAUDE_CONFIG_DIR",
    "ANTHROPIC_CONFIG_DIR",
    "VIKI_DATA_DIR",
)


def _without_provider_env(env: Mapping[str, str]) -> dict[str, str]:
    prefixes = (
        "OPENAI_",
        "AWS_",
        "AZURE_",
        "GOOGLE_",
        "GEMINI_",
        "MISTRAL_",
        "COHERE_",
        "DEEPSEEK_",
        "HF_",
        "HUGGINGFACE_",
    )
    return {
        name: value
        for name, value in winproc.scrub_env(env).items()
        if not name.upper().startswith(prefixes)
        and name.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
        and not name.upper().endswith(
            ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_CREDENTIALS")
        )
    }


# --- a synthetic MCP server for a --mcp-config scenario -------------------------------------------

ECHO_SERVER = '''"""Synthetic MCP stdio server for a capture scenario: newline-delimited JSON-RPC
2.0 on stdin/stdout, stdlib only, no other purpose."""
import json
import sys

VERSION = "project" if "--variant=project" in sys.argv else "1"


def _send(message):
    print(json.dumps(message), flush=True)


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            params = request.get("params") or {}
            _send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "viki-echo", "version": VERSION},
                },
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [{
                        "name": "echo",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }],
                },
            })
        elif method == "tools/call":
            arguments = (request.get("params") or {}).get("arguments") or {}
            _send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": arguments.get("text")}]},
            })
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif request_id is not None:
            _send({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method not found"},
            })


if __name__ == "__main__":
    main()
'''


def write_echo_server(run_dir: Path) -> Path:
    """Write the synthetic MCP server to the run folder and return its absolute path."""
    path = run_dir / "echo_server.py"
    path.write_text(ECHO_SERVER, encoding="utf-8")
    return path


def write_mcp_config(folder: Path, server: Path) -> Path:
    """Write the one-server app MCP config a --mcp-config scenario passes, and return its
    absolute path (`mcp_config.write_config`'s private content-addressed file).

    `command` is `sys.executable` and one `args` entry is `server`'s path, both run through the
    schema's secret screen (`check_configuration`); a path holding a run of 32 or more word
    characters ([A-Za-z0-9_-]) -- the interpreter's own path or the run folder's -- is refused as
    `secret_value`, so the run fails closed before any process is spawned."""
    folder.mkdir(parents=True, exist_ok=True)  # the parents only; write_config makes `mcp` itself
    value = {
        "servers": [
            {
                "name": "echo",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(server)],
                "url": None,
                "note": None,
            }
        ]
    }
    return mcp_config.write_config(folder / "mcp", mcp_config.config_bytes(value))


# --- recording ----------------------------------------------------------------------------------


class Recorder:
    def __init__(self, path: Path, scenario: str) -> None:
        self._fh = path.open("a", encoding="utf-8")
        self._scenario = scenario
        self._t0 = time.monotonic()

    def __call__(self, direction: str, frame: Frame) -> None:
        t_ms = int((time.monotonic() - self._t0) * 1000)
        line = {"scenario": self._scenario, "dir": direction, "t_ms": t_ms, "frame": frame}
        self._fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class TappedTransport(ClaudeTransport):
    """ClaudeTransport that also records what we write and the control frames we read.

    The transport's parameters are spelled out, not passed through, so the type checker sees
    what `Capture.open` hands the transport: a sink with the wrong signature once got past it."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        sink: Sink,
        tap: Recorder,
        spawner: Spawner | None = None,
        grace_s: float = GRACE_S,
        poll_s: float = POLL_S,
        max_frame: int = MAX_FRAME,
        append_system_prompt: str | None = None,
    ) -> None:
        super().__init__(
            argv,
            cwd=cwd,
            env=env,
            sink=sink,
            spawner=spawner,
            grace_s=grace_s,
            poll_s=poll_s,
            max_frame=max_frame,
            append_system_prompt=append_system_prompt,
        )
        self._tap = tap

    async def write(self, data: str) -> None:
        # amend_initialize is idempotent, so tapping its result cannot double-declare what
        # super().write (below) sends; it only makes the tap show what claude actually receives.
        for line in amend_initialize(data, self._append_system_prompt).splitlines():
            if line.strip():
                self._tap("in", json.loads(line))
        await super().write(data)

    def read_messages(self) -> AsyncIterator[Frame]:
        return self._tapped(super().read_messages())

    async def _tapped(self, frames: AsyncIterator[Frame]) -> AsyncIterator[Frame]:
        async for frame in frames:
            self._tap("out", frame)
            yield frame


# --- a live session under capture ----------------------------------------------------------------


@dataclass
class Ask:
    tool: str
    input: dict[str, Any]
    ctx: ToolPermissionContext
    answer: asyncio.Future[Answer]


@dataclass
class Live:
    session_id: str
    control: ClaudeControl
    transport: TappedTransport
    inbox: asyncio.Queue[Frame | Ask]  # sink frames and permission asks, in arrival order
    init: dict[str, Any] = field(default_factory=dict)
    seen: list[Frame] = field(default_factory=list)  # every sink frame collected so far


def deny_all(ask: Ask) -> Answer:
    return PermissionResultDeny(message="Denied by the capture script.")


def expect_exact(tool: str, command: str, asked: list[str]) -> Callable[[Ask], Answer]:
    """Approve one exact tool call, deny everything else, and record which tools asked.

    Naming the tool is not enough. Scenarios run in a scratch folder, but the user-level
    settings and hooks still load, and no PreToolUse guard fires for PowerShell (there is no deny
    rule for it either), so for a shell scenario this callback is the last thing between a
    command the model improvised and a write anywhere the user's account can reach. Refusing a
    command that is merely close makes the scenario fail, which is the safe way to be wrong.
    `asked` also shows in the summary whether the host was consulted at all: in `auto` mode the
    CLI may never ask.
    """

    def answer(ask: Ask) -> Answer:
        asked.append(ask.tool)
        ok = ask.tool == tool and ask.input.get("command", "").strip() == command
        return PermissionResultAllow() if ok else deny_all(ask)

    return answer


class InterruptFailed(Exception):
    """`collect`'s interrupt call raised; the cause is the original exception."""


class Capture:
    def __init__(
        self, cfg: config.Config, run_dir: Path, root: Path, *, isolation_root: Path | None = None
    ) -> None:
        self.cfg = cfg
        self.run_dir = run_dir
        self.root = root  # the seeded scratch folder a session starts in unless `open` names one
        self.isolation_root = isolation_root
        self.opened: list[str] = []  # session ids, in open order, for summary.json
        self.argvs: list[list[str]] = []  # each spawn's argv, in open order
        # Sessions opened and not yet closed. A scenario that raises past its `cap.close` would
        # otherwise leave a real claude.exe mid-turn on the user's quota until the script exits.
        self._open: list[Live] = []

    async def open(
        self,
        rec: Recorder,
        launch: Launch,
        *,
        mode: str = "auto",
        effort: str = "low",
        model: str | None = "sonnet",
        cli: Path | None = None,
        entrypoint: str = config.ENTRYPOINT,
        child_overlay: Mapping[str, str] | None = None,
        routed: bool = False,
        cwd: Path | None = None,
    ) -> Live:
        inbox: asyncio.Queue[Frame | Ask] = asyncio.Queue()

        def sink(frame: Frame, source_bytes: int) -> None:
            rec("out", frame)
            inbox.put_nowait(frame)

        async def can_use_tool(
            tool: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
        ) -> Answer:
            ask = Ask(tool, tool_input, ctx, asyncio.get_running_loop().create_future())
            inbox.put_nowait(ask)
            try:
                async with asyncio.timeout(120):
                    return await ask.answer
            except TimeoutError:
                return deny_all(ask)

        argv = build_argv([str(cli or self.cfg.cli)], launch, mode=mode, effort=effort, model=model)
        self.argvs.append(argv)
        base = _without_provider_env(os.environ) if routed else None
        if base is not None and self.cfg.cli_source != "bundled":
            base.pop("DISABLE_AUTOUPDATER", None)
        child_env = winproc.child_env(
            entrypoint, base, pin_updates=not routed or self.cfg.cli_source == "bundled"
        )
        if routed:
            scratch = self.isolation_root
            if (
                scratch is None
                or any(
                    not os.environ.get(name)
                    or not Path(os.environ[name]).resolve().is_relative_to(scratch.resolve())
                    for name in _SCRATCH_ENV_KEYS
                )
                or child_overlay is None
                or set(child_overlay) != set(_ROUTE_ENV_KEYS)
                or child_overlay["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"] != "1"
                or child_overlay["ANTHROPIC_API_KEY"] != ""
                or not child_overlay["ANTHROPIC_AUTH_TOKEN"]
                or not child_overlay["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:")
            ):
                raise ValueError("unsafe routed capture environment")
            child_env.update(child_overlay)
            child_env["CLAUDE_CONFIG_DIR"] = os.environ["CLAUDE_CONFIG_DIR"]
            child_env["ANTHROPIC_CONFIG_DIR"] = os.environ["ANTHROPIC_CONFIG_DIR"]
        elif child_overlay is not None:
            # Scenario 40's Git configuration and identity only (`_git_env`); the subscription
            # check below still runs. Inherited Git config variables go, as in winproc.git.
            if not all(
                name.startswith(("GIT_CONFIG_", "GIT_AUTHOR_", "GIT_COMMITTER_"))
                for name in child_overlay
            ):
                raise ValueError("unsafe capture environment")
            child_env = {
                name: value
                for name, value in child_env.items()
                if not (name.upper() == "GIT_CONFIG" or name.upper().startswith("GIT_CONFIG_"))
            }
            child_env.update(child_overlay)
        transport = TappedTransport(
            argv,
            cwd=self.root if cwd is None else cwd,
            env=child_env,
            sink=sink,
            tap=rec,
            append_system_prompt=launch.append_system_prompt,
        )
        # Only a Bot session that fences its writes names hooks in initialize (scenario 44).
        control = (
            ClaudeControl(transport, can_use_tool)
            if launch.hooks is None
            else ClaudeControl(transport, can_use_tool, hooks=launch.hooks)
        )
        live = Live(launch.session_id, control, transport, inbox)
        self._open.append(live)
        self.opened.append(launch.session_id)
        live.init = await control.start()
        try:
            # The same gate the app applies to every session. config.billing_problems
            # in run() reads settings files; only the initialize reply shows what the login
            # actually is, and ~28 real sessions per run is not something to bill by accident.
            if not routed:
                check_billing(live.init)
        except StartError:
            await self.close(live)
            raise
        return live

    async def turn(
        self,
        live: Live,
        text: str,
        *,
        on_ask: Callable[[Ask], Answer] = deny_all,
        interrupt_when: Callable[[Frame], bool] | None = None,
        interrupt_delay: float = 0.0,
        timeout: float = TURN_TIMEOUT_S,
    ) -> list[Frame]:
        """Send one prompt and collect sink frames until its `result` (or the process exits)."""
        await live.control.send(text, live.session_id)
        return await self.collect(live, on_ask, interrupt_when, interrupt_delay, timeout)

    async def collect(
        self,
        live: Live,
        on_ask: Callable[[Ask], Answer] = deny_all,
        interrupt_when: Callable[[Frame], bool] | None = None,
        interrupt_delay: float = 0.0,
        timeout: float = TURN_TIMEOUT_S,
        stop_when: Callable[[Frame], bool] | None = None,
        on_interrupted: Callable[[], None] | None = None,
    ) -> list[Frame]:
        """Frames until the turn's `result`, the process exit, or `stop_when` matches.
        `on_interrupted` runs once the interrupt call has returned without raising."""
        got: list[Frame] = []
        interrupted = False
        async with asyncio.timeout(timeout):
            while True:
                item = await live.inbox.get()
                if isinstance(item, Ask):
                    item.answer.set_result(on_ask(item))
                    continue
                got.append(item)
                live.seen.append(item)
                if interrupt_when and not interrupted and interrupt_when(item):
                    interrupted = True
                    await asyncio.sleep(interrupt_delay)
                    try:
                        await live.control.interrupt()
                    except Exception as exc:
                        raise InterruptFailed(f"{type(exc).__name__}: {exc}") from exc
                    if on_interrupted:
                        on_interrupted()
                if item["type"] in ("result", EXIT) or (stop_when and stop_when(item)):
                    return got

    async def close(self, live: Live) -> None:
        self._open = [other for other in self._open if other is not live]
        await live.control.close()

    async def close_all(self, *, redacted: bool = False) -> None:
        """Close every session a scenario left open, whatever went wrong. Nothing else reaps
        them: `Job` has no finalizer and the SDK never closes a transport it did not open, so
        an abandoned claude.exe lives until the interpreter exits."""
        for live in list(self._open):
            try:
                await self.close(live)
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask the scenario's error
                detail = "" if redacted else f": {exc}"
                print(f"  (cleanup) {live.session_id}: {type(exc).__name__}{detail}", flush=True)
                self._open = [other for other in self._open if other is not live]


# --- helpers for observations ---------------------------------------------------------------------


def types_of(frames: list[Frame]) -> list[str]:
    return [
        f"{f['type']}/{f.get('subtype') or f.get('event', {}).get('type', '')}".rstrip("/")
        for f in frames
    ]


def result_of(frames: list[Frame]) -> Frame:
    return next((f for f in reversed(frames) if f["type"] == "result"), {})


def text_of(frames: list[Frame]) -> str:
    parts = []
    for f in frames:
        if f["type"] == "assistant":
            parts += [
                b.get("text", "")
                for b in f["message"].get("content", [])
                if b.get("type") == "text"
            ]
    return "".join(parts)


def deltas(frames: list[Frame], kind: str) -> list[str]:
    out = []
    for f in frames:
        delta = f.get("event", {}).get("delta", {}) if f["type"] == "stream_event" else {}
        if delta.get("type") == kind:
            out.append(delta.get("text") or delta.get("thinking") or "")
    return out


def keys_shape(value: Any, depth: int = 2) -> Any:
    """Structure without content: dict keys (recursively) and list lengths."""
    if isinstance(value, dict) and depth > 0:
        return {k: keys_shape(v, depth - 1) for k, v in value.items()}
    if isinstance(value, list):
        return [keys_shape(value[0], depth - 1), f"x{len(value)}"] if value else []
    return type(value).__name__


def first_text_delta(frame: Frame) -> bool:
    return (
        frame["type"] == "stream_event"
        and frame.get("event", {}).get("delta", {}).get("type") == "text_delta"
    )


def new_id() -> str:
    return str(uuid.uuid4())


def _drain(live: Live) -> list[Frame | Ask]:
    return [live.inbox.get_nowait() for _ in range(live.inbox.qsize())]


def names(values: Any) -> list[str]:
    """Sorted distinct non-empty values as strings (for summaries)."""
    return sorted({str(v) for v in values if v})


# --- scenarios ------------------------------------------------------------------------------------

Obs = dict[str, Any]
Scenario = Callable[[Capture, Recorder], Awaitable[Obs]]
SCENARIOS: dict[str, tuple[str, Scenario]] = {}
SHARED: dict[str, str] = {}  # session ids later scenarios reuse


def scenario(key: str, title: str) -> Callable[[Scenario], Scenario]:
    def register(fn: Scenario) -> Scenario:
        SCENARIOS[key] = (title, fn)
        return fn

    return register


@scenario("01", "initialize reply; when system/init arrives; deltas; CLAUDE.md loaded; two turns")
async def s01(cap: Capture, rec: Recorder) -> Obs:
    sid = new_id()
    live = await cap.open(rec, Launch(sid))
    await asyncio.sleep(2)  # does anything (system/init?) arrive before the first user message?
    before_send = [f for f in _drain(live) if not isinstance(f, Ask)]
    t1 = await cap.turn(
        live,
        "Without using any tools: what is prime directive 1 in your project instructions? "
        "One sentence.",
    )
    t2 = await cap.turn(live, "Reply with exactly the letter B.")
    await cap.close(live)
    SHARED["two_turns"] = sid
    init = live.init
    return {
        "pass": result_of(t1).get("subtype") == "success"
        and result_of(t2).get("subtype") == "success",
        "session_id": sid,
        "initialize_keys": sorted(init),
        "account": init.get("account"),
        "pid_in_init": init.get("pid"),
        "transport_pid": live.transport.pid,
        "models": [m.get("value") for m in init.get("models", [])],
        "agents": [a.get("name") for a in init.get("agents", [])],
        "commands_count": len(init.get("commands", [])),
        "command_shape": keys_shape(init.get("commands", [{}])[0])
        if init.get("commands")
        else None,
        "frames_before_first_send": types_of(before_send),
        "turn1_types": types_of(t1)[:12],
        "init_frame_keys": sorted(next((f for f in t1 if f.get("subtype") == "init"), {})),
        "permission_mode_in_init": next(
            (f.get("permissionMode") for f in t1 if f.get("subtype") == "init"), None
        ),
        "claude_md_grounded": "_raw" in text_of(t1),
        "text_deltas": len(deltas(t1, "text_delta")),
        "result_keys": sorted(result_of(t1)),
        "turn2_text": text_of(t2)[:40],
    }


@scenario("05", "Bash + hook events + guard canary")
async def s05(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    canary = "echo probe >> no-such-dir/settings.json"
    asked: list[str] = []
    frames = await cap.turn(
        live,
        f"Run exactly this Bash command once and report what happened: {canary}",
        on_ask=expect_exact("Bash", canary, asked),
    )
    await cap.close(live)
    hooks = [f for f in frames if f["type"] == "system" and "hook" in str(f.get("subtype", ""))]
    return {
        "pass": bool(hooks),
        "hook_subtypes": names(f.get("subtype") for f in hooks),
        "hook_frame_shape": keys_shape(hooks[0], 1) if hooks else None,
        "hook_names": sorted({str(f.get("hook_name") or f.get("hook_event")) for f in hooks}),
        "asked": asked,
        "types": types_of(frames)[:40],
        "reply": text_of(frames)[:300],
    }


@scenario("05b", "PowerShell tool: does any PreToolUse hook fire?")
async def s05b(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    probe = "Write-Output probe"
    asked: list[str] = []
    frames = await cap.turn(
        live,
        f"Use the PowerShell tool (not Bash) to run exactly: {probe}",
        on_ask=expect_exact("PowerShell", probe, asked),
    )
    await cap.close(live)
    tools = [
        b.get("name")
        for f in frames
        if f["type"] == "assistant"
        for b in f["message"]["content"]
        if b.get("type") == "tool_use"
    ]
    hooks = [f for f in frames if f["type"] == "system" and "hook" in str(f.get("subtype", ""))]
    return {
        "pass": "PowerShell" in tools,
        "tools_used": tools,
        "asked": asked,  # empty means the CLI auto-approved without consulting the host
        "hook_events": [
            (f.get("subtype"), f.get("hook_name") or f.get("hook_event")) for f in hooks
        ],
    }


@scenario("06", "subagent (Explore, haiku)")
async def s06(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    frames = await cap.turn(
        live,
        "Use the Agent tool with subagent_type Explore and model haiku to find which file in "
        "_templates/ is the daily note template. Report only the file name.",
    )
    await cap.close(live)
    agent_calls = [
        b
        for f in frames
        if f["type"] == "assistant" and not f.get("parent_tool_use_id")
        for b in f["message"]["content"]
        if b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task")
    ]
    sub_frames = [f for f in frames if f.get("parent_tool_use_id")]
    tasks = [
        f for f in frames if f["type"] == "system" and str(f.get("subtype", "")).startswith("task_")
    ]
    return {
        "pass": bool(agent_calls),
        "agent_tool_input": agent_calls[0].get("input") if agent_calls else None,
        "agent_tool_name": agent_calls[0].get("name") if agent_calls else None,
        "subagent_frame_types": sorted(set(types_of(sub_frames))),
        "task_subtypes": names(f.get("subtype") for f in tasks),
        "task_frame_shape": keys_shape(tasks[0], 1) if tasks else None,
        "reply": text_of(frames)[:200],
    }


@scenario("07", "AskUserQuestion answered through updatedInput.answers")
async def s07(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    asked: list[dict[str, Any]] = []

    def answer_tea(ask: Ask) -> Answer:
        if ask.tool != "AskUserQuestion":
            return deny_all(ask)
        asked.append({"input": ask.input, "tool_use_id": ask.ctx.tool_use_id})
        questions = ask.input.get("questions", [])
        answers = {q["question"]: "Tea" for q in questions}
        return PermissionResultAllow(updated_input={**ask.input, "answers": answers})

    frames = await cap.turn(
        live,
        "Use the AskUserQuestion tool to ask me one question: do I prefer Tea or Coffee "
        "(exactly two options, labels 'Tea' and 'Coffee'). Then tell me what I chose in one "
        "sentence.",
        on_ask=answer_tea,
    )
    await cap.close(live)
    return {
        "pass": bool(asked) and "tea" in text_of(frames).lower(),
        "question_input_shape": keys_shape(asked[0]["input"], 3) if asked else None,
        "tool_use_id_present": bool(asked and asked[0]["tool_use_id"]),
        "reply": text_of(frames)[:200],
    }


@scenario("08", "permission modes: default/manual names, WebFetch denied, back to auto")
async def s08(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    accepted: dict[str, str] = {}
    for name in ("default", "manual"):
        try:
            await live.control.set_permission_mode(name)
            accepted[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - recording what the CLI says is the point
            accepted[name] = f"{type(exc).__name__}: {exc}"[:200]
    asked: list[Any] = []

    def deny_fetch(ask: Ask) -> Answer:
        asked.append(
            {
                "tool": ask.tool,
                "suggestions": [s.to_dict() for s in ask.ctx.suggestions],
                "reason": ask.ctx.decision_reason,
                "title": ask.ctx.title,
            }
        )
        return PermissionResultDeny(message="The owner declined this fetch.")

    frames = await cap.turn(
        live, "Use WebFetch on https://example.com and tell me the page title.", on_ask=deny_fetch
    )
    await live.control.set_permission_mode("auto")
    modes = [f.get("permissionMode") or f.get("mode") for f in frames if f["type"] == "system"]
    await cap.close(live)
    argv_modes = {}
    for name in ("default", "manual"):
        try:
            probe = await cap.open(rec, Launch(new_id()), mode=name)
            argv_modes[name] = "starts"
            await cap.close(probe)
        except Exception as exc:  # noqa: BLE001
            argv_modes[name] = f"{type(exc).__name__}: {exc}"[:200]
    return {
        "pass": bool(asked) and asked[0]["tool"] == "WebFetch",
        "set_permission_mode": accepted,
        "argv_permission_mode": argv_modes,
        "ask": asked[:1],
        "system_modes_seen": modes,
        "denial_frames": [f for f in frames if f.get("subtype") == "permission_denied"][:2],
        "reply": text_of(frames)[:200],
    }


@scenario("09", "interrupt mid-stream, then a next turn")
async def s09(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    t0 = time.monotonic()
    t1 = await cap.turn(
        live,
        "Count from 1 to 300, one number per line, no other text.",
        interrupt_when=first_text_delta,
        interrupt_delay=1.0,
    )
    stop_s = time.monotonic() - t0
    t2 = await cap.turn(live, "Reply with exactly: done")
    await cap.close(live)
    r1 = result_of(t1)
    return {
        "pass": r1.get("terminal_reason") in ("aborted_streaming", "aborted_tools")
        or r1.get("subtype") != "success",
        "result1": {
            k: r1.get(k) for k in ("subtype", "is_error", "terminal_reason", "stop_reason")
        },
        "turn1_seconds": round(stop_s, 1),
        "turn2_ok": result_of(t2).get("subtype") == "success",
        "turn2_text": text_of(t2)[:20],
    }


@scenario("10", "early interrupt (<1 s after send)")
async def s10(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    await live.control.send("Write a 300-word story about a lighthouse.", live.session_id)
    await asyncio.sleep(0.3)
    await live.control.interrupt()
    t1 = await cap.collect(live)
    t2 = await cap.turn(live, "Reply with exactly: ok")
    await cap.close(live)
    return {
        "pass": result_of(t2).get("subtype") == "success",
        "result1": {k: result_of(t1).get(k) for k in ("subtype", "is_error", "terminal_reason")},
        "turn1_types": types_of(t1)[:15],
        "turn2_text": text_of(t2)[:20],
    }


@scenario("11", "resume and fork of the session scenario 01 created")
async def s11(cap: Capture, rec: Recorder) -> Obs:
    base = SHARED.get("two_turns")
    if not base:
        return {"pass": False, "skipped": "needs scenario 01 in the same run"}
    live = await cap.open(rec, Launch(base, resume=True))
    t1 = await cap.turn(
        live, "What single letter did you reply with last time? Reply with just it."
    )
    await cap.close(live)
    fork_id = new_id()
    fork = await cap.open(rec, Launch(fork_id, fork_of=base))
    t2 = await cap.turn(fork, "Reply with exactly the letter C.")
    await cap.close(fork)
    fork_sids = names(f.get("session_id") for f in t2)
    SHARED["fork"] = fork_id
    return {
        "pass": result_of(t1).get("subtype") == "success" and fork_sids == [fork_id],
        "resume_reply": text_of(t1)[:20],
        "fork_session_ids_in_frames": fork_sids,
        "fork_id_requested": fork_id,
        "fork_transcript_exists": _transcript(cap.cfg, cap.root, fork_id).exists(),
    }


@scenario("12", "set_model live; --effort respawn before and after the first turn")
async def s12(cap: Capture, rec: Recorder) -> Obs:
    sid = new_id()
    live = await cap.open(rec, Launch(sid))
    await cap.close(live)
    transcript_before_turn = _transcript(cap.cfg, cap.root, sid).exists()
    # Effort change before any turn: respawn with the same --session-id (a --resume would fail,
    # since nothing was written yet).
    live = await cap.open(rec, Launch(sid), effort="medium")
    await live.control.set_model("haiku")
    t1 = await cap.turn(live, "Reply with exactly: hi")
    await cap.close(live)
    live = await cap.open(rec, Launch(sid, resume=True), effort="high")
    t2 = await cap.turn(live, "Reply with exactly: again")
    await cap.close(live)
    models = [f["message"].get("model") for f in t1 if f["type"] == "assistant"]
    return {
        "pass": result_of(t1).get("subtype") == "success"
        and result_of(t2).get("subtype") == "success",
        "transcript_exists_before_first_turn": transcript_before_turn,
        "models_after_set_model": sorted(set(models)),
        "resume_after_turn": result_of(t2).get("subtype"),
    }


@scenario(
    "23",
    "version skew: fork a small recent >=2.1.266 session, resume the copy on 2.1.259 and VS Code",
)
async def s23(cap: Capture, rec: Recorder) -> Obs:
    """Records structure only (steps, versions, entry types). The source is the user's own
    session, so nothing from this scenario may ever be committed, scrubbed or not: `_capture_name`
    marks the file and scrub_fixture refuses it. The fork also leaves a copy of that session in
    ~/.claude, which hard rule 1 stops this script deleting; `cleanup` in summary.json says so."""
    source = _small_recent_session(cap.cfg, cap.root, min_version=(2, 1, 266))
    if source is None:
        return {"pass": False, "skipped": "no recent session written by >=2.1.266"}
    copy = new_id()
    steps: Obs = {
        "source_bytes": source.stat().st_size,
        "source_versions": sorted(_transcript_field(source, "version") - {"None"}),
    }
    fork = await cap.open(rec, Launch(copy, fork_of=source.stem))
    usage = await fork.control.context_usage()
    steps["context_percentage"] = usage.get("percentage")
    if (usage.get("percentage") or 0) > 60:
        await cap.close(fork)
        return {"pass": False, "skipped": "source context too large", **steps}
    steps["fork_turn"] = result_of(await cap.turn(fork, "Reply with exactly: ok")).get("subtype")
    await cap.close(fork)
    for label, cli in (("resume_2.1.259", None), ("resume_vscode_cli", _vscode_cli())):
        if label.endswith("vscode_cli") and cli is None:
            steps[label] = "no VS Code CLI found"
            continue
        try:
            live = await cap.open(rec, Launch(copy, resume=True), cli=cli)
            steps[label] = result_of(await cap.turn(live, "Reply with exactly: ok")).get("subtype")
            await cap.close(live)
        except Exception as exc:  # noqa: BLE001
            steps[label] = f"{type(exc).__name__}: {exc}"[:300]
    copy_path = _transcript(cap.cfg, cap.root, copy)
    steps["copy_versions"] = sorted(_transcript_field(copy_path, "version") - {"None"})
    steps["copy_entry_types"] = sorted(_transcript_field(copy_path, "type"))
    steps["copy_session_id"] = copy
    steps["copy_transcript"] = str(copy_path)
    # The fork leaves a copy of the user's conversation in ~/.claude, which hard rule 1 forbids
    # this repo from deleting. Say so, because the app's session list shows it as a phantom
    # duplicate of the original.
    steps["cleanup"] = "delete copy_transcript by hand: it is a copy of one of your own sessions"
    steps["pass"] = all(
        steps.get(k) == "success" for k in ("fork_turn", "resume_2.1.259", "resume_vscode_cli")
    )
    return steps


@scenario("13", "get_context_usage")
async def s13(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    usage = await live.control.context_usage()
    await cap.close(live)
    return {
        "pass": "percentage" in usage or "totalTokens" in usage,
        "keys": sorted(usage),
        "percentage": usage.get("percentage"),
        "maxTokens": usage.get("maxTokens"),
    }


@scenario("15", "entrypoint sdk-py vs viki-agent")
async def s15(cap: Capture, rec: Recorder) -> Obs:
    result: Obs = {"pass": True}
    for entry in ("sdk-py", "viki-agent"):
        sid = new_id()
        live = await cap.open(rec, Launch(sid), entrypoint=entry)
        frames = await cap.turn(live, "Reply with exactly: ok")
        registry = _registry_entry(cap.cfg, live.transport.pid)
        await cap.close(live)
        transcript = _transcript(cap.cfg, cap.root, sid)
        entries = _transcript_field(transcript, "entrypoint")
        result[entry] = {
            "agents": [a.get("name") for a in live.init.get("agents", [])],
            "claude_code_guide": any(
                a.get("name") == "claude-code-guide" for a in live.init.get("agents", [])
            ),
            "registry": {k: registry.get(k) for k in ("kind", "entrypoint", "version")}
            if registry
            else None,
            "transcript_entrypoints": sorted(entries),
            "ok": result_of(frames).get("subtype") == "success",
        }
    return result


@scenario("16", "Job kill during a running tool (unverified; capture disabled)")
async def s16(cap: Capture, rec: Recorder) -> Obs:
    return {
        "pass": False,
        "running_tool_verified": False,
        "incomplete": True,
        "note": (
            "Unverified: tool_use and descendant cleanup do not establish a running tool "
            "process. A compatible observable probe and a fresh live capture "
            "are required."
        ),
    }


@scenario("20", "-p sessions register in ~/.claude/sessions")
async def s20(cap: Capture, rec: Recorder) -> Obs:
    sid = new_id()
    live = await cap.open(rec, Launch(sid))
    await cap.turn(live, "Reply with exactly: ok")
    registry = _registry_entry(cap.cfg, live.transport.pid)
    started = winproc.process_start_time(live.transport.pid or 0)  # while it is still alive
    await cap.close(live)
    return {
        "pass": bool(registry) and registry.get("sessionId") == sid,
        "registry_keys": sorted(registry) if registry else None,
        "kind": registry.get("kind") if registry else None,
        "entrypoint": registry.get("entrypoint") if registry else None,
        "procStart_matches": bool(registry) and str(started) == registry.get("procStart"),
        "registry_removed_on_close": not (
            cap.cfg.claude_home / "sessions" / f"{live.transport.pid}.json"
        ).exists(),
        "transcript_exists": _transcript(cap.cfg, cap.root, sid).exists(),
        "transcript_entry_types": sorted(
            _transcript_field(_transcript(cap.cfg, cap.root, sid), "type")
        ),
    }


@scenario("22", "thinking text arrives with --thinking-display summarized")
async def s22(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()), effort="max")
    frames = await cap.turn(
        live,
        "Think it through step by step before answering, then reply with just the number: "
        "what is the sum of all primes p below 200 such that p + 2 is also prime?",
    )
    await cap.close(live)
    thinking = deltas(frames, "thinking_delta")
    blocks = [
        b
        for f in frames
        if f["type"] == "assistant"
        for b in f["message"]["content"]
        if b.get("type") == "thinking"
    ]
    return {
        "pass": bool("".join(thinking).strip()) or any(b.get("thinking") for b in blocks),
        "thinking_deltas": len(thinking),
        "thinking_chars": len("".join(thinking)),
        "thinking_block_keys": sorted(blocks[0]) if blocks else None,
        "delta_types": sorted(
            {
                f.get("event", {}).get("delta", {}).get("type")
                for f in frames
                if f["type"] == "stream_event"
            }
            - {None}
        ),
        "thinking_token_frames": sum(1 for f in frames if f.get("subtype") == "thinking_tokens"),
        "reply": text_of(frames)[:40],
    }


@scenario("24", "/think with an argument (a vault skill; every write is denied)")
async def s24(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    frames = await cap.turn(live, "/think what makes a daily note worth rereading?")
    await cap.close(live)
    skill_calls = [
        b.get("input")
        for f in frames
        if f["type"] == "assistant"
        for b in f["message"]["content"]
        if b.get("type") == "tool_use" and b.get("name") == "Skill"
    ]
    user_frames = [f for f in frames if f["type"] == "user"]
    return {
        "pass": result_of(frames).get("subtype") == "success",
        "skill_tool_calls": skill_calls,
        "user_frame_count": len(user_frames),
        "user_frame_shapes": [keys_shape(f, 2) for f in user_frames[:2]],
        "types": types_of(frames)[:30],
    }


@scenario("25", "/context, /compact, /model as prompts")
async def s25(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    obs: Obs = {"pass": True}
    await cap.turn(live, "Reply with exactly: warm")
    for command in ("/context", "/compact", "/model"):
        frames = await cap.turn(live, command, timeout=300)
        obs[command] = {
            "types": types_of(frames)[:20],
            "result": {k: result_of(frames).get(k) for k in ("subtype", "is_error", "result")},
            "compact_frames": [
                keys_shape(f, 1) for f in frames if "compact" in json.dumps(f.get("subtype", ""))
            ][:2],
        }
    await cap.close(live)
    return obs


@scenario("26", "plan mode → ExitPlanMode card → approve → mode restored")
async def s26(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    await live.control.set_permission_mode("plan")
    asked: list[Any] = []

    def approve_plan(ask: Ask) -> Answer:
        if ask.tool != "ExitPlanMode":
            return deny_all(ask)
        asked.append(keys_shape(ask.input, 2))
        return PermissionResultAllow(
            updated_permissions=[
                PermissionUpdate(type="setMode", mode="auto", destination="session")
            ]
        )

    frames = await cap.turn(
        live,
        "Make a two-step plan for listing the files in _templates/ (do not list them yet), "
        "then call ExitPlanMode with the plan.",
        on_ask=approve_plan,
    )
    await cap.close(live)
    modes = [
        (f.get("subtype"), f.get("permissionMode") or f.get("mode"))
        for f in frames
        if f["type"] == "system"
    ]
    return {
        "pass": bool(asked),
        "exit_plan_input_shape": asked[:1],
        "system_modes_seen": modes,
        "types": types_of(frames)[:40],
    }


@scenario("27", "a subagent's permission request carries agent_id")
async def s27(cap: Capture, rec: Recorder) -> Obs:
    live = await cap.open(rec, Launch(new_id()))
    await live.control.set_permission_mode("default")
    asked: list[Any] = []

    def deny_record(ask: Ask) -> Answer:
        asked.append(
            {"tool": ask.tool, "agent_id": ask.ctx.agent_id, "tool_use_id": ask.ctx.tool_use_id}
        )
        return PermissionResultDeny(message="The owner declined this fetch.")

    frames = await cap.turn(
        live,
        "Use the Agent tool with subagent_type general-purpose to fetch https://example.com "
        "with WebFetch and report its title.",
        on_ask=deny_record,
    )
    await cap.close(live)
    return {
        "pass": any(a["agent_id"] for a in asked),
        "asks": asked,
        "reply": text_of(frames)[:200],
    }


@scenario("28", "a background agent's completion wakes the parent for a continuation turn")
async def s28(cap: Capture, rec: Recorder) -> Obs:
    sid = new_id()
    live = await cap.open(rec, Launch(sid))
    turn1 = await cap.turn(
        live,
        "Use the Agent tool with subagent_type Explore, model haiku and run_in_background true "
        "to find which file in _templates/ is the daily note template. Right after launching "
        "it, reply with exactly: launched. When its result comes back, reply with only the "
        "file name.",
    )
    try:  # no send: whatever arrives now is the CLI's own continuation turn
        after = await cap.collect(live, timeout=180)
    except TimeoutError:
        after = []
    await cap.close(live)

    def agent_results(frames: list[Frame]) -> list[Any]:
        return [
            keys_shape(f.get("tool_use_result"), 1) | {"status": f["tool_use_result"].get("status")}
            for f in frames
            if f["type"] == "user" and isinstance(f.get("tool_use_result"), dict)
        ]

    notes = [f for f in turn1 + after if f.get("subtype") == "task_notification"]
    entries = [
        json.loads(line)
        for line in _transcript(cap.cfg, cap.root, sid).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    notice = [
        e
        for e in entries
        if e.get("type") == "user" and "<task-notification>" in json.dumps(e.get("message"))
    ]
    body = json.dumps(notice[0].get("message")) if notice else ""
    return {
        "pass": bool(after) and result_of(after).get("type") == "result",
        "session_id": sid,
        "turn1_types": types_of(turn1)[:40],
        "turn1_agent_results": agent_results(turn1),
        "after_types": types_of(after)[:60],
        "after_user_frames": [keys_shape(f, 2) for f in after if f["type"] == "user"][:3],
        "task_notification_shape": keys_shape(notes[0], 1) if notes else None,
        "task_notification_status": [n.get("status") for n in notes],
        "continuation_result": {k: result_of(after).get(k) for k in ("subtype", "is_error")},
        "transcript_notice_entries": len(notice),
        "notice_entry_keys": sorted(notice[0]) if notice else None,
        "notice_has_tags": {
            tag: f"<{tag}>" in body for tag in ("task-id", "tool-use-id", "status", "summary")
        },
        "notice_is_meta": [e.get("isMeta") for e in notice],
        "reply_after": text_of(after)[:80],
    }


# --- --mcp-config scenarios: the app MCP config against a real claude.exe -------------------------


def _echo_entries(status: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in status.get("mcpServers", []) if s.get("name") == "viki_echo"]


def _tool_names(tools: Any) -> list[str]:
    """Sorted tool names from a `tools` list of `McpToolInfo` dicts (name, description?,
    annotations?): the SDK's get_mcp_status shape. A dict entry contributes its string `name`
    only; anything that is not a dict with one, such entries are skipped rather than stringified
    whole, so a tool's description and annotations never reach an observation."""
    return sorted(
        {
            t["name"]
            for t in tools or []
            if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]
        }
    )


def _echo_sample(status: dict[str, Any]) -> Obs:
    entries = _echo_entries(status)
    entry = entries[0] if entries else None
    return {
        "status": entry.get("status") if entry else None,
        "scope": entry.get("scope") if entry else None,
        "tools": _tool_names(entry.get("tools")) if entry else None,
    }


@scenario("29", "--mcp-config: status timeline, context usage, file read only at start")
async def s29(cap: Capture, rec: Recorder) -> Obs:
    server = write_echo_server(cap.run_dir)
    config_path = write_mcp_config(cap.run_dir / "data", server)
    live = await cap.open(rec, Launch(new_id(), mcp_config=str(config_path)))
    samples: list[Obs] = []
    elapsed = 0.0
    for offset in (0.0, 0.5, 1.0, 2.0, 5.0):
        if offset > elapsed:
            await asyncio.sleep(offset - elapsed)
        elapsed = offset
        samples.append(_echo_sample(await live.control.mcp_status()))
    usage = await live.control.context_usage()
    config_path.unlink()
    await asyncio.sleep(2)  # reconnect_mcp_server is never called: the status should not change
    after_delete = _echo_sample(await live.control.mcp_status())
    await cap.close(live)
    return {
        "pass": any(s["status"] == "connected" for s in samples)
        and after_delete["status"] == "connected",
        "samples": samples,
        "skills_shape": keys_shape(usage.get("skills"), 3),
        "system_tools_present": "systemTools" in usage,
        "after_delete_status": after_delete["status"],
        "sideload_warning": "disableSideloadFlags" in live.transport.stderr_tail(),
    }


@scenario("30", "--mcp-config against a project .mcp.json declaring viki_echo")
async def s30(cap: Capture, rec: Recorder) -> Obs:
    server = write_echo_server(cap.run_dir)
    config_path = write_mcp_config(cap.run_dir / "data", server)
    project_config = cap.root / ".mcp.json"
    project_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "viki_echo": {
                        "command": sys.executable,
                        "args": [str(server), "--variant=project"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    try:
        live = await cap.open(rec, Launch(new_id(), mcp_config=str(config_path)))
        await asyncio.sleep(2)
        status = await live.control.mcp_status()
        await cap.close(live)
        entries = _echo_entries(status)
        return {
            "pass": isinstance(status.get("mcpServers"), list),
            "entries": [
                {"scope": e.get("scope"), "version": (e.get("serverInfo") or {}).get("version")}
                for e in entries
            ],
        }
    finally:
        project_config.unlink(missing_ok=True)  # scenarios after this one share cap.root


@scenario("31", "--mcp-config path under a folder with a space and Japanese characters")
async def s31(cap: Capture, rec: Recorder) -> Obs:
    server = write_echo_server(cap.run_dir)
    config_path = write_mcp_config(cap.run_dir / "mcp 日本語" / "data", server)
    live = await cap.open(rec, Launch(new_id(), mcp_config=str(config_path)))
    await asyncio.sleep(2)
    sample = _echo_sample(await live.control.mcp_status())
    stderr = live.transport.stderr_tail()
    await cap.close(live)
    return {
        "pass": sample["status"] == "connected",
        "status": sample["status"],
        "split_path_error": any(token in stderr for token in ("not found", "ENOENT")),
    }


@scenario("32", "--resume of a missing id: do SessionStart hooks run first?")
async def s32(cap: Capture, rec: Recorder) -> Obs:
    exc_name: str | None = None
    try:
        live = await cap.open(rec, Launch(new_id(), resume=True))
    except Exception as exc:  # noqa: BLE001 - a missing id is expected to fail; recording how
        exc_name = type(exc).__name__
        live = cap._open[-1]
    drained = [f for f in _drain(live) if not isinstance(f, Ask)]
    hook_frames = sum(1 for t in types_of(drained) if t.startswith("system/hook_"))
    no_conversation = "no conversation found" in live.transport.stderr_tail().lower()
    await cap.close(live)
    return {
        "pass": no_conversation,
        "hook_frames": hook_frames,
        "no_conversation": no_conversation,
        "open_exception": exc_name,
    }


@scenario("33", "Stop at the first text delta of a CLI-started continuation turn")
async def s33(cap: Capture, rec: Recorder) -> Obs:
    sid = new_id()
    live = await cap.open(rec, Launch(sid))
    await cap.turn(
        live,
        "Use the Agent tool with subagent_type Explore, model haiku and run_in_background true "
        "to find which file in _templates/ is the daily note template. Right after launching "
        "it, reply with exactly: launched. When its result comes back, reply with only the "
        "file name.",
    )
    interrupt_sent = False
    interrupt_returned = False
    collect_timed_out = False
    interrupt_error: str | None = None

    def interrupt_when(frame: Frame) -> bool:
        nonlocal interrupt_sent
        matched = first_text_delta(frame)
        interrupt_sent = interrupt_sent or matched
        return matched

    def interrupt_done() -> None:
        nonlocal interrupt_returned
        interrupt_returned = True

    try:  # no send: whatever arrives now is the CLI's own continuation turn
        after = await cap.collect(
            live, interrupt_when=interrupt_when, timeout=180, on_interrupted=interrupt_done
        )
    except InterruptFailed as exc:
        interrupt_error = str(exc)[:200]
        after = []
    except TimeoutError:  # the collect's own bound, not the interrupt's
        collect_timed_out = True
        after = []
    await cap.close(live)
    result = result_of(after)
    exited = any(f["type"] == EXIT for f in after)
    return {
        "pass": bool(result) or exited,
        "session_id": sid,
        "text_delta_arrived": any(first_text_delta(f) for f in after),
        "interrupt_sent": interrupt_sent,
        "interrupt_ok": interrupt_returned,
        "interrupt_error": interrupt_error,
        "collect_timed_out": collect_timed_out,
        "result_subtype": result.get("subtype"),
        "terminal_reason": result.get("terminal_reason"),
        "is_error": result.get("is_error"),
        "after_types": types_of(after)[:60],
        "process_exited": exited,
    }


async def _status_outcome(control: ClaudeControl, settled: Callable[[], bool]) -> Obs:
    """One `mcp_status` request and what came back: a reply, an error or a timeout. `settled`
    says whether the event under test (the initialize reply, the turn's result) has already
    happened; it is read as the request goes out and again when the answer arrives."""
    out: Obs = {"settled_at_send": settled()}
    try:
        out["echo"] = _echo_sample(await control.mcp_status())
        out["outcome"] = "reply"
    except TimeoutError:
        out["outcome"] = "timeout"
    except Exception as exc:  # noqa: BLE001 - an error reply is one of the answers to record
        out["outcome"] = "error"
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    out["settled_at_answer"] = settled()
    return out


@scenario("34", "mcp_status before the initialize reply and during a running turn")
async def s34(cap: Capture, rec: Recorder) -> Obs:
    server = write_echo_server(cap.run_dir)
    config_path = write_mcp_config(cap.run_dir / "data", server)
    opening = asyncio.create_task(cap.open(rec, Launch(new_id(), mcp_config=str(config_path))))
    while not cap._open and not opening.done():  # open() lists the Live before the handshake
        await asyncio.sleep(0.005)
    before: Obs = {"outcome": "not_attempted"}
    if cap._open:
        control = cap._open[-1].control
        while True:  # the client refuses a call until it has built its Query, just before the
            # initialize request; retry that refusal only while the handshake is pending
            before = await _status_outcome(control, opening.done)
            refused = str(before.get("error", "")).startswith("CLIConnectionError")
            if not refused or opening.done():
                break
            await asyncio.sleep(0.005)
    live = await opening
    await live.control.send(
        "Without using any tools, write the numbers 1 to 80, one per line, and nothing else.",
        live.session_id,
    )
    head = await cap.collect(live, stop_when=first_text_delta)
    during = asyncio.create_task(_status_outcome(live.control, lambda: bool(result_of(live.seen))))
    ended = head[-1]["type"] in ("result", EXIT)
    tail = [] if ended else await cap.collect(live)
    during_turn = await during
    await cap.close(live)
    frames = head + tail
    result = result_of(frames)
    return {
        "pass": result.get("subtype") == "success",
        "before_initialize_reply": before,
        "during_turn": during_turn,
        "turn_types": types_of(frames)[:40],
        "result_subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
    }


@scenario("35", "a user line sent mid-turn: does it join the running turn?")
async def s35(cap: Capture, rec: Recorder) -> Obs:
    sid = new_id()
    live = await cap.open(rec, Launch(sid))
    cmd = "Start-Sleep -Seconds 8; Write-Output slept"
    asked: list[str] = []
    on_ask = expect_exact("PowerShell", cmd, asked)

    def tool_started(frame: Frame) -> bool:
        blocks = frame["message"]["content"] if frame["type"] == "assistant" else []
        return any(b.get("type") == "tool_use" for b in blocks)

    await live.control.send(
        f"Run exactly this PowerShell command and nothing else: {cmd}. Then reply with the "
        "single word done.",
        sid,
    )
    await cap.collect(live, on_ask=on_ask, stop_when=tool_started)
    steer = "After that, also reply with the word steered."
    await live.control.send(steer, sid)
    mid = await cap.collect(live, on_ask=on_ask)
    try:
        tail = await cap.collect(live, on_ask=on_ask, timeout=90)
    except TimeoutError:
        tail = []
    after = mid + tail
    await cap.close(live)
    results = [i for i, f in enumerate(after) if f["type"] == "result"]
    steered = [i for i, f in enumerate(after) if "steered" in text_of([f]).lower()]
    steered_before_result = bool(steered) and bool(results) and steered[0] < results[0]
    entries = [
        json.loads(line)
        for line in _transcript(cap.cfg, cap.root, sid).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    steer_entries = [e for e in entries if steer in json.dumps(e, ensure_ascii=False)]
    return {
        "pass": bool(results) and bool(steered),
        "session_id": sid,
        "asked": asked,
        "result_count": len(results),
        "after_types": types_of(after)[:60],
        "after_user_frames": [keys_shape(f, 2) for f in after if f["type"] == "user"][:5],
        "steered_before_first_result": steered_before_result,
        "steer_entries": [
            {"type": e.get("type"), "keys": sorted(e), "isMeta": e.get("isMeta")}
            for e in steer_entries
        ],
        "queue_operation_entries": sum(1 for e in entries if e.get("type") == "queue-operation"),
        "attachment_entries": sum(1 for e in entries if e.get("type") == "attachment"),
    }


@scenario("36", "stop_task stops one background agent; a Stop mid-turn spares the other")
async def s36(cap: Capture, rec: Recorder) -> Obs:
    sid = new_id()
    live = await cap.open(rec, Launch(sid))
    sleep_a = "Start-Sleep -Seconds 40; Write-Output a-done"
    sleep_b = "Start-Sleep -Seconds 25; Write-Output b-done"
    asked: list[str] = []

    def approve_sleep(ask: Ask) -> Answer:
        asked.append(ask.tool)
        command = ask.input.get("command", "").strip()
        ok = ask.tool == "PowerShell" and command in (sleep_a, sleep_b)
        return PermissionResultAllow() if ok else deny_all(ask)

    turn1 = await cap.turn(
        live,
        "Use the Agent tool twice, each with subagent_type general-purpose, model haiku and "
        "run_in_background true. One agent's prompt: use the PowerShell tool to run exactly "
        f"this command: {sleep_a}. The other agent's prompt: use the PowerShell tool to run "
        f"exactly this command: {sleep_b}. Launch both, then reply with exactly: launched.",
        on_ask=approve_sleep,
    )
    started = [f for f in turn1 if f.get("subtype") == "task_started"]
    if len(started) < 2:
        await cap.close(live)
        return {
            "pass": False,
            "note": "both task_started frames never arrived in turn 1",
            "task_started_count": len(started),
            "asked_turn1": asked,
            "turn1_types": types_of(turn1)[:60],
        }

    # Which started task is A: the top-level Agent/Task launch whose own input still carries A's
    # exact command verbatim (we asked the model to write that command into the subagent's own
    # prompt). A nested subagent's tool_use blocks are excluded by the parent_tool_use_id check.
    agent_calls = {
        b["id"]: b.get("input", {})
        for f in turn1
        if f["type"] == "assistant" and not f.get("parent_tool_use_id")
        for b in f["message"]["content"]
        if b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task")
    }
    a_started = next(
        (f for f in started if sleep_a in json.dumps(agent_calls.get(f.get("tool_use_id"), {}))),
        None,
    )
    b_started = next(
        (f for f in started if sleep_b in json.dumps(agent_calls.get(f.get("tool_use_id"), {}))),
        None,
    )
    if a_started is None or b_started is None or a_started is b_started:
        await cap.close(live)
        return {
            "pass": False,
            "note": "could not tell agent A from agent B",
            "started": started[:2],
            "agent_call_shapes": keys_shape(agent_calls, 2),
        }
    a_task_id = a_started.get("task_id")
    a_tool_id = a_started.get("tool_use_id")
    b_task_id = b_started.get("task_id")
    b_tool_id = b_started.get("tool_use_id")
    if not (
        isinstance(a_task_id, str)
        and isinstance(a_tool_id, str)
        and isinstance(b_task_id, str)
        and isinstance(b_tool_id, str)
    ):
        await cap.close(live)
        return {
            "pass": False,
            "note": "a task_started frame is missing its task_id or tool_use_id",
            "started": started[:2],
        }

    try:
        await live.control.stop_task(a_task_id)
        stop_outcome = "reply"
        stop_error = None
    except TimeoutError:
        stop_outcome = "timeout"
        stop_error = None
    except Exception as exc:  # noqa: BLE001 - an error reply is one of the answers to record
        stop_outcome = "error"
        stop_error = f"{type(exc).__name__}: {exc}"[:200]

    sleep_15 = "Start-Sleep -Seconds 15; Write-Output slept"
    asked2: list[str] = []

    def tool_use_started(frame: Frame) -> bool:
        blocks = frame["message"]["content"] if frame["type"] == "assistant" else []
        return any(b.get("type") == "tool_use" for b in blocks)

    turn2 = await cap.turn(
        live,
        f"Run exactly this PowerShell command and nothing else: {sleep_15}. Then reply "
        "with the single word done.",
        on_ask=expect_exact("PowerShell", sleep_15, asked2),
        interrupt_when=tool_use_started,
    )
    try:  # no send: B's own completion (or A's, if the stop was too slow) wakes the parent
        tail = await cap.collect(live, timeout=120)
    except TimeoutError:
        tail = []
    await cap.close(live)

    frames = turn1 + turn2 + tail

    def notification_statuses(tool_use_id: str | None) -> list[Any]:
        return [
            f.get("status")
            for f in frames
            if f.get("subtype") == "task_notification" and f.get("tool_use_id") == tool_use_id
        ]

    def updated_statuses(task_id: str | None) -> list[Any]:
        return [
            f.get("patch", {}).get("status")
            for f in frames
            if f.get("subtype") == "task_updated" and f.get("task_id") == task_id
        ]

    a_notifications = notification_statuses(a_tool_id)
    a_updates = updated_statuses(a_task_id)
    b_notifications = notification_statuses(b_tool_id)
    b_updates = updated_statuses(b_task_id)
    # The task_updated patch says "killed" for a stopped task; only task_notification says
    # "stopped", and that notification may be suppressed. A background task may also end only
    # through task_updated, with no task_notification at all, so both sides read either source.
    a_stopped = "stopped" in a_notifications or "killed" in a_updates
    b_completed = "completed" in b_notifications or "completed" in b_updates
    result2 = result_of(turn2)
    return {
        "pass": stop_outcome == "reply" and a_stopped and b_completed,
        "session_id": sid,
        "asked_turn1": asked,
        "stop_outcome": stop_outcome,
        "stop_error": stop_error,
        "a_task_notification_statuses": a_notifications,
        "a_task_updated_statuses": a_updates,
        "b_task_notification_statuses": b_notifications,
        "b_task_updated_statuses": b_updates,
        "b_completed": b_completed,
        "turn2_result_subtype": result2.get("subtype"),
        "turn2_asked": asked2,
        "a_stopped_by_user": _agent_meta_stopped_by_user(cap.cfg, cap.root, sid, a_tool_id),
        "b_stopped_by_user": _agent_meta_stopped_by_user(cap.cfg, cap.root, sid, b_tool_id),
        "tail_types": types_of(tail)[:40],
    }


def _settings_variant(settings: str) -> str:
    """The Bot settings file at `settings` without its `claudeMdExcludes` and `autoMemoryEnabled`:
    the memory-only file Bot sessions started with before ADR-085. Written beside it, private and
    hash-named the same way."""
    path = Path(settings)
    document = json.loads(path.read_text(encoding="ascii"))
    del document["claudeMdExcludes"]
    del document["autoMemoryEnabled"]
    data = json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return str(mcp_config.write_config(path.parent, data))


@scenario("37", "per-Bot native auto memory: separate identities, fresh chat and resume")
async def s37(cap: Capture, rec: Recorder) -> Obs:
    owners = [new_id(), new_id()]
    canaries = [f"BOT_MEMORY_{uuid.uuid4().hex}" for _ in owners]
    paths: list[str] = []
    data = cap.run_dir / "data"
    protection.create_private_directory(data)
    protection.require_private_directory(data)
    for owner, canary in zip(owners, canaries, strict=True):
        directory = cap.root / memory_ref(owner)
        directory.mkdir(parents=True)
        (directory / "MEMORY.md").write_text(
            f"# Bot memory\n\nThe synthetic auto-memory canary is {canary}.\n", encoding="utf-8"
        )
        # Its gates read the scratch CLAUDE.md, which the app's Bot settings skip (scenario 43),
        # so it keeps the memory-only file it was written with.
        paths.append(_settings_variant(write_settings(data / SETTINGS_FOLDER, cap.root, owner)))

    prompt = (
        "Without using any tools, give the synthetic auto-memory canary already in your "
        "loaded memory, then prime directive 1 from CLAUDE.md. If either is absent, say absent."
    )
    observations: list[dict[str, Any]] = []
    first_id = new_id()
    for index, session_id, resume in (
        (0, first_id, False),
        (1, new_id(), False),
        (0, new_id(), False),
        (0, first_id, True),
    ):
        live = await cap.open(
            rec, Launch(session_id, resume=resume, bot_memory_settings=paths[index])
        )
        frames = await cap.turn(live, prompt)
        await cap.close(live)
        tools = [
            block.get("name")
            for frame in frames
            if frame["type"] == "assistant"
            for block in frame["message"].get("content", [])
            if block.get("type") == "tool_use"
        ]
        text = text_of(frames)
        observations.append(
            {
                "owner": index,
                "resume": resume,
                "own_canary": canaries[index] in text,
                "other_canary": canaries[1 - index] in text,
                "project_instructions": "_raw" in text,
                "tools": tools,
                "success": result_of(frames).get("subtype") == "success",
            }
        )
    return {
        "pass": all(
            item["own_canary"]
            and not item["other_canary"]
            and item["project_instructions"]
            and not item["tools"]
            and item["success"]
            for item in observations
        ),
        "observations": observations,
    }


@scenario("44", "Bot settings turn auto memory off and the write fence refuses memory writes")
async def s44(cap: Capture, rec: Recorder) -> Obs:
    """ADR-087 and ADR-089 in a throwaway vault: a canary in the Bot's memory folder stays
    unread now that its settings turn auto memory off, and the fence's hook refuses a Write
    into `memory/` before any permission card, while the write's target stays absent."""
    owner, canary = new_id(), f"BOT_MEMORY_{uuid.uuid4().hex}"
    data = cap.run_dir / "data"
    protection.create_private_directory(data)
    protection.require_private_directory(data)
    folder = cap.root / memory_ref(owner)
    folder.mkdir(parents=True)
    (folder / "MEMORY.md").write_text(
        f"# Bot memory\n\nThe synthetic auto-memory canary is {canary}.\n", encoding="utf-8"
    )
    settings = write_settings(data / SETTINGS_FOLDER, cap.root, owner)
    marks: list[str] = []
    fence = WriteFence(
        FenceRoots(cap.root, cap.root, data / "bot-memory"),
        tainted=lambda: False,
        mark=marks.append,
    )
    probe = cap.root / "memory" / f"fence-{uuid.uuid4().hex}.md"
    observations: dict[str, Any] = {}
    live = await cap.open(
        rec, Launch(new_id(), bot_memory_settings=settings, hooks=fence.hooks()), mode="acceptEdits"
    )
    try:
        recall = await cap.turn(
            live,
            "Without using any tools, give the synthetic auto-memory canary in your loaded "
            "memory, or say absent.",
        )
        write = await cap.turn(
            live,
            f"Use the Write tool once to create {probe.relative_to(cap.root).as_posix()} with "
            "the text probe. Then say what happened, quoting any reason you were given.",
        )
    finally:
        await cap.close(live)
    results = [
        str(block.get("content"))
        for frame in write
        if frame["type"] == "user"
        for block in frame.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    observations = {
        "canary_recalled": canary in text_of(recall),
        "write_refused": any(MSG_RECORDS[:40] in result for result in results),
        "probe_absent": not probe.exists(),
        "success": result_of(write).get("subtype") == "success",
    }
    return {
        "pass": not observations["canary_recalled"]
        and observations["write_refused"]
        and observations["probe_absent"]
        and observations["success"],
        "observations": observations,
    }


@asynccontextmanager
async def _loopback_app(app: Starlette) -> AsyncIterator[int]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("loopback listener did not start")
                await asyncio.sleep(0.01)
        yield listener.getsockname()[1]
    finally:
        server.should_exit = True

        async def stop() -> None:
            try:
                try:
                    async with asyncio.timeout(5):
                        await task
                except TimeoutError:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            finally:
                listener.close()

        await finish_cleanup(stop())


async def _mcp_listed(live: Live) -> bool:
    """Whether `mcp_status` lists the synthetic echo server's tools, asked again while it may
    still be connecting."""
    for delay in (0.0, 0.5, 1.0, 2.0):
        if delay:
            await asyncio.sleep(delay)
        status = await live.control.mcp_status()
        if _tool_names((_echo_entries(status) or [{}])[0].get("tools")):
            return True
    return False


@scenario("39", "synthetic model gateway with Bot memory and app MCP")
async def s39(cap: Capture, rec: Recorder) -> Obs:
    if sys.platform != "linux" or cap.isolation_root is None:
        return {"pass": False, "linux": False}
    owner = new_id()
    canary = f"BOT_MEMORY_{uuid.uuid4().hex}"
    directory = cap.root / memory_ref(owner)
    directory.mkdir(parents=True)
    (directory / "MEMORY.md").write_text(
        f"# Bot memory\n\nSynthetic memory canary: {canary}.\n", encoding="utf-8"
    )
    data = cap.run_dir / "data"
    protection.create_private_directory(data)
    settings_path = write_settings(data / SETTINGS_FOLDER, cap.root, owner)
    mcp_path = write_mcp_config(data, write_echo_server(cap.run_dir))
    upstream = {"reached": False, "memory": False}

    async def messages(request: Request) -> Response:
        body = await request.json()
        upstream["reached"] = True
        upstream["memory"] = canary in json.dumps(
            {"system": body.get("system"), "messages": body.get("messages")},
            ensure_ascii=False,
        )
        reply = (
            event(
                "message_start",
                {
                    "message": {
                        "id": "msg_synthetic",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": "synthetic-model",
                        "stop_reason": None,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                },
            )
            + event(
                "content_block_start",
                {
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            + event(
                "content_block_delta",
                {
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Synthetic reply."},
                },
            )
            + event("content_block_stop", {"index": 0})
            + event(
                "message_delta",
                {
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 1},
                },
            )
            + event("message_stop", {})
        )
        return Response(reply, media_type="text/event-stream")

    gateway = Gateway()
    stub = Starlette(routes=[Route("/v1/messages", messages, methods=["POST"])])
    async with _loopback_app(stub) as stub_port:
        connection = Connection(
            new_id(),
            "Synthetic",
            f"http://127.0.0.1:{stub_port}",
            "anthropic",
            "synthetic-model",
        )
        route = gateway.issue(connection)
        try:
            async with _loopback_app(Starlette(routes=gateway.routes())) as gateway_port:
                route_env = route.environment()
                route_env["ANTHROPIC_BASE_URL"] = route_env["ANTHROPIC_BASE_URL"].replace(
                    ":8765/", f":{gateway_port}/", 1
                )
                live = await cap.open(
                    rec,
                    Launch(new_id(), bot_memory_settings=settings_path, mcp_config=str(mcp_path)),
                    model=connection.model,
                    child_overlay=route_env,
                    routed=True,
                )
                mcp_listed = await _mcp_listed(live)
                frames = await cap.turn(
                    live,
                    "Without using tools, answer using your loaded Bot memory "
                    "and project instructions.",
                )
                await cap.close(live)
                stream_success = result_of(frames).get("subtype") == "success"
                observations = {
                    "linux": True,
                    "route_id": route.id,
                    "model": connection.model,
                    "memory_in_request": upstream["memory"],
                    "mcp_listed": mcp_listed,
                    "host_managed_auth": upstream["reached"],
                    "upstream_reached": upstream["reached"],
                    "stream_success": stream_success,
                    "initialize_account_metadata_present": "account" in live.init,
                    "initialize_models_metadata_present": "models" in live.init,
                }
                return {
                    "pass": all(
                        value
                        for key, value in observations.items()
                        if key
                        not in {
                            "route_id",
                            "model",
                            "initialize_account_metadata_present",
                            "initialize_models_metadata_present",
                        }
                    ),
                    **observations,
                }
        finally:
            route.revoke()


# --- scenario 40: a Bot code copy on the native subscription (G5) ---------------------------------

_GIT_NAME = "VIKI Capture"
_GIT_EMAIL = "capture@example.com"
_GIT_AUTHOR = f"{_GIT_NAME} <{_GIT_EMAIL}>"
_COPY_NOTE = "BOT_COPY_NOTE.txt"
_COPY_SCRIPT = "copy-note.sh"


def _git_env(hooks: Path) -> dict[str, str]:
    """Scenario 40's Git settings for the native child, the same as its setup's: no global or
    system config, hooks in an empty private folder, no signing, and the synthetic author."""
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(hooks),
        "GIT_CONFIG_KEY_1": "commit.gpgsign",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_AUTHOR_NAME": _GIT_NAME,
        "GIT_AUTHOR_EMAIL": _GIT_EMAIL,
        "GIT_COMMITTER_NAME": _GIT_NAME,
        "GIT_COMMITTER_EMAIL": _GIT_EMAIL,
    }


def _script_text(copy: Path) -> str:
    """Scenario 40's synthetic setup script: write the note in the copy and commit it there. The
    model only runs it, so Claude Code's own commit guidance has no command to rewrite."""
    return (
        "#!/bin/sh\n"
        "set -e\n"
        f"cd {shlex.quote(str(copy))}\n"
        f"echo synthetic > {_COPY_NOTE}\n"
        f"git add {_COPY_NOTE}\n"
        "git commit -m synthetic\n"
    )


def _script_command(script: PurePath) -> str:
    """The one Bash command scenario 40 approves: run its setup script."""
    return f"sh {shlex.quote(str(script))}"


_PROMPT_40 = (
    "Make exactly one tool call in this turn: a single Bash call whose command is the line in "
    "the code block below, copied character for character, without quoting, rewording or "
    "extending it. It runs a synthetic test setup script that needs no inspection; nothing "
    "else is needed before or after it.\n"
    "```bash\n{command}\n```\n"
    "When it returns, make no further tool call: reply with the synthetic auto-memory canary "
    "already in your loaded memory, or say absent."
)


def _bash_command(ask: Ask) -> str:
    """A Bash request's command, or "" for any other request or a command that is not text."""
    value = ask.input.get("command") if ask.tool == "Bash" else None
    return value if isinstance(value, str) else ""


def _loose(command: str) -> str:
    """`command` without quote characters and with each whitespace run as one space."""
    return " ".join(command.replace('"', "").replace("'", "").split())


def _answer_40(command: str, asked: list[Ask], approved: list[Ask]) -> Callable[[Ask], Answer]:
    """Approve the first Bash request for exactly `command`, and deny every other request, a
    repeat of it included, so the synthetic command runs at most once however many requests
    the model makes. Every request is kept in `asked` and the approved one in `approved`, for
    the summary's diagnosis."""

    def answer(ask: Ask) -> Answer:
        asked.append(ask)
        if not approved and _bash_command(ask).strip() == command:
            approved.append(ask)
            return PermissionResultAllow()
        return deny_all(ask)

    return answer


@scenario("40", "Bot code copy: one native turn commits in its copy, with Bot memory and app MCP")
async def s40(cap: Capture, rec: Recorder) -> Obs:
    owner, session_id = new_id(), new_id()
    canary = f"BOT_MEMORY_{uuid.uuid4().hex}"
    memory = cap.root / memory_ref(owner)
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text(
        f"# Bot memory\n\nThe synthetic auto-memory canary is {canary}.\n", encoding="utf-8"
    )
    data, hooks = cap.run_dir / "data", cap.run_dir / "git-hooks-off"
    area = worktree_area(data)
    for folder in (data, hooks, area):
        protection.create_private_directory(folder)
        protection.require_private_directory(folder)
    settings = write_settings(data / SETTINGS_FOLDER, cap.root, owner)
    mcp = write_mcp_config(data, write_echo_server(cap.run_dir))
    repo, copy, marker = cap.run_dir / "repo", area / new_id(), cap.run_dir / "hook-ran"
    hooks_off = ("-c", f"core.hooksPath={hooks}", "-c", "commit.gpgsign=false")

    def git(cwd: Path, *args: str) -> str:
        return winproc.git(cwd, *hooks_off, *args, isolated_config=True)

    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    # The same settings in the repository's own config, which its linked worktree shares, so
    # the native commit stays correct even if Claude Code's Bash drops the child's Git variables.
    # Written before the base commit, so the comparison below starts from them.
    for key, value in (
        ("user.name", _GIT_NAME),
        ("user.email", _GIT_EMAIL),
        ("core.hooksPath", str(hooks)),
        ("commit.gpgsign", "false"),
    ):
        git(repo, "config", key, value)
    (repo / ".git" / "hooks").mkdir(exist_ok=True)
    for hook in ("post-checkout", "post-commit"):  # only a hook that ran leaves the marker
        path = repo / ".git" / "hooks" / hook
        path.write_text(
            f"#!/bin/sh\n: > {shlex.quote(marker.as_posix())}\n", encoding="utf-8", newline="\n"
        )
        path.chmod(0o700)
    (repo / "README.md").write_text("Synthetic repository.\n", encoding="utf-8")
    # A synthetic skill in the copy: listed at init only if --add-dir reached Claude Code.
    skill = f"copy-probe-{uuid.uuid4().hex[:8]}"
    (repo / ".claude" / "skills" / skill).mkdir(parents=True)
    (repo / ".claude" / "skills" / skill / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: Synthetic capture probe, not for any task.\n---\n",
        encoding="utf-8",
    )
    git(repo, "add", "README.md", ".claude")
    git(repo, "commit", "-q", "-m", "synthetic base")
    base = git(repo, "rev-parse", "HEAD").strip()
    branch = f"viki/{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:4]}"
    git(repo, "worktree", "add", "-q", "-b", branch, str(copy), base)
    # In the session's own folder, outside the repository and the copy's tracked tree.
    script = cap.root / _COPY_SCRIPT
    script.write_text(_script_text(copy), encoding="utf-8", newline="\n")
    script.chmod(0o700)

    launch = Launch(
        session_id, bot_memory_settings=settings, mcp_config=str(mcp), code_worktree=str(copy)
    )
    placeholders = {
        str(cap.cfg.cli): "<cli>",
        f"--session-id={session_id}": "--session-id=<session-id>",
        f"--add-dir={copy}": "--add-dir=<code-copy>",
        f"--settings={settings}": "--settings=<bot-memory-settings>",
        f"--mcp-config={mcp}": "--mcp-config=<app-mcp-config>",
    }
    try:
        live = await cap.open(rec, launch, mode="default", child_overlay=_git_env(hooks))
    except StartError as exc:  # the initialize reply refused: no turn is sent
        return {
            "argv": [placeholders.get(arg, arg) for arg in cap.argvs[-1]],
            "native_login": False,
            "not_logged_in": exc.code == "not_logged_in",
        }
    argv = [placeholders.get(arg, arg) for arg in cap.argvs[-1]]  # what open spawned
    mcp_listed = await _mcp_listed(live)
    command = _script_command(script)
    asked: list[Ask] = []
    approved: list[Ask] = []
    frames = await cap.turn(
        live, _PROMPT_40.format(command=command), on_ask=_answer_40(command, asked, approved)
    )
    await cap.close(live)
    inits = [f for f in frames if f["type"] == "system" and f.get("subtype") == "init"]
    tools = [
        block.get("name")
        for frame in frames
        if frame["type"] == "assistant"
        for block in frame["message"].get("content", [])
        if block.get("type") == "tool_use"
    ]
    parent, *people = git(copy, "log", "-1", "--format=%P%n%an <%ae>%n%cn <%ce>").splitlines()
    copy_commit = (
        parent == base
        and git(copy, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split()
        == [_COPY_NOTE]
        and git(copy, "symbolic-ref", "--short", "HEAD").strip() == branch
    )
    observations = {
        "native_login": True,  # Capture.open's check_billing accepted the initialize reply
        "mcp_listed": mcp_listed,
        "memory_loaded": canary in text_of(frames),
        "exact_approved_once": len(approved) == 1,
        "turn_success": result_of(frames).get("subtype") == "success",
        "copy_file": (copy / _COPY_NOTE).is_file()
        and (copy / _COPY_NOTE).read_bytes() == b"synthetic\n",
        "copy_commit": copy_commit,
        "synthetic_author": copy_commit and people == [_GIT_AUTHOR, _GIT_AUTHOR],
        "original_untouched": git(repo, "rev-parse", "HEAD").strip() == base
        and git(repo, "symbolic-ref", "--short", "HEAD").strip() == "main"
        and not git(repo, "status", "--porcelain").strip()
        and not (repo / _COPY_NOTE).exists(),
        "hooks_off": not marker.exists(),
        "cwd_scratch": any(f.get("cwd") == str(cap.root) for f in inits),
    }
    # The tool result Claude Code reports for the approved call, matched by its tool use id.
    approved_id = approved[0].ctx.tool_use_id if approved else None
    approved_errored = approved_id is not None and any(
        bool(block.get("is_error"))
        for frame in frames
        if frame["type"] == "user"
        for block in frame.get("message", {}).get("content") or []
        if isinstance(block, dict)
        and block.get("type") == "tool_result"
        and block.get("tool_use_id") == approved_id
    )
    bash_commands = [_bash_command(ask) for ask in asked if ask.tool == "Bash"]
    return {
        "pass": all(observations.values()),
        "argv": argv,
        **observations,
        # Reported, not gating: whether the turn was exactly one asked and made Bash call, and
        # whether the copy's synthetic skill was listed at init.
        "exact_command_only": [ask.tool for ask in asked] == ["Bash"] and tools == ["Bash"],
        "add_dir_skill_listed": any(skill in (f.get("skills") or []) for f in inits),
        # Counts and flags only, for a failed run: `_evidence_40` keeps nothing else.
        "diagnosis": {
            "asks": len(asked),
            "bash_asks": len(bash_commands),
            "exact_asks": sum(text.strip() == command for text in bash_commands),
            "near_asks": sum(
                text.strip() != command and _loose(text) == _loose(command)
                for text in bash_commands
            ),
            "script_asks": sum(
                text.strip() != command and _COPY_SCRIPT in text for text in bash_commands
            ),
            "tool_calls": len(tools),
            "bash_calls": tools.count("Bash"),
            "approved_errored": approved_errored,
            "note_exists": (copy / _COPY_NOTE).exists(),
            "commits_after_base": int(git(copy, "rev-list", "--count", f"{base}..HEAD")),
        },
    }


# --- scenario 41: a Bot's memory folder outside the working folder --------------------------------

_READ_41 = frozenset({"Read", "Glob", "Grep"})
_SAVE_41 = _READ_41 | {"Write", "Edit", "MultiEdit"}
_PROMPT_41_SAVE = (
    "Save this one fact to your auto memory now, in the memory folder your instructions name, "
    "so that a later chat of yours can recall it: the synthetic canary is {canary}. Save it in "
    "that folder and add a one-line entry for it to MEMORY.md there, creating MEMORY.md if it "
    "is missing. Then reply saved."
)
_PROMPT_41_FRESH = (
    "List every synthetic canary that starts with BOT_OUTSIDE_ and is in your auto memory, or "
    "say absent. Read your memory folder only if you need to."
)
_PROMPT_41_READY = "Reply with the single word ready."
_PROMPT_41_RESUMED = (
    "List every synthetic canary that starts with BOT_OUTSIDE_ and is in your auto memory or "
    "earlier in this chat, or say absent. Read your memory folder only if you need to."
)


def _inside_41(folder: Path, cwd: Path, value: Any) -> bool:
    """Whether `value`, a path a request names (a relative one starts in `cwd`), is in `folder`."""
    if not isinstance(value, str) or not value:
        return False
    try:
        return (cwd / value).resolve().is_relative_to(folder.resolve())
    except OSError, ValueError:
        return False


def _answer_41(
    folder: Path, cwd: Path, tools: frozenset[str], asks: list[dict[str, Any]]
) -> Callable[[Ask], Answer]:
    """Allow once a request of one of `tools` whose every path is inside `folder`, and deny every
    other, a Bash request included. Each request is kept in `asks` as its tool name and whether
    it was inside, never its path."""

    def answer(ask: Ask) -> Answer:
        paths = [
            ask.input[key] for key in ("file_path", "path", "notebook_path") if key in ask.input
        ]
        inside = bool(paths) and all(_inside_41(folder, cwd, path) for path in paths)
        allowed = inside and ask.tool in tools
        asks.append({"tool": ask.tool, "inside_folder": inside, "allowed": allowed})
        return PermissionResultAllow() if allowed else deny_all(ask)

    return answer


def _holds(folder: Path, canary: str) -> bool:
    """Whether a Markdown file anywhere under `folder` names `canary`."""
    return any(
        canary in path.read_text(encoding="utf-8", errors="replace")
        for path in folder.rglob("*.md")
        if path.is_file()
    )


def _argv_shape(argv: Sequence[str], cli: Path | None) -> list[str]:
    """`argv` with the CLI, the session id and the settings path as placeholders."""
    shaped = []
    for arg in argv:
        flag = arg.split("=", 1)[0]
        if arg == str(cli):
            shaped.append("<cli>")
        elif flag in ("--session-id", "--resume"):
            shaped.append(f"{flag}=<session-id>")
        elif flag == "--settings":
            shaped.append("--settings=<bot-memory-settings>")
        else:
            shaped.append(arg)
    return shaped


@scenario("41", "Bot memory outside the working folder: save, recall and resume")
async def s41(cap: Capture, rec: Recorder) -> Obs:
    workspace, bot_a, bot_b, session_x = new_id(), new_id(), new_id(), new_id()
    canary_a, canary_b, canary_c = (f"BOT_OUTSIDE_{uuid.uuid4().hex}" for _ in range(3))
    data = cap.run_dir / "data"  # beside the scratch folder, not in it: the memory is outside
    protection.create_private_directory(data)
    protection.require_private_directory(data)
    folder_a = app_memory_directory(data, workspace, bot_a)
    settings_a = write_settings(data / SETTINGS_FOLDER, cap.root, bot_a, workspace_id=workspace)
    # Nothing under <data>/bot-memory exists: only Claude Code can create Bot A's folder.
    absent_before = not folder_a.parent.parent.exists()
    turns: list[dict[str, Any]] = []

    async def turn(
        launch: Launch, mode: str, prompt: str, folder: Path, tools: frozenset[str]
    ) -> str:
        live = await cap.open(rec, launch, mode=mode)
        asks: list[dict[str, Any]] = []
        frames = await cap.turn(live, prompt, on_ask=_answer_41(folder, cap.root, tools, asks))
        await cap.close(live)
        turns.append(
            {
                "mode": mode,
                "resume": launch.resume,
                "argv": _argv_shape(cap.argvs[-1], cap.cfg.cli),
                "success": result_of(frames).get("subtype") == "success",
                "asks": asks,
            }
        )
        return text_of(frames)

    await turn(
        Launch(new_id(), bot_memory_settings=settings_a),
        "acceptEdits",
        _PROMPT_41_SAVE.format(canary=canary_a),
        folder_a,
        _SAVE_41,
    )
    created = folder_a.is_dir()
    saved = _holds(folder_a, canary_a)
    index = folder_a / "MEMORY.md"
    index_written = index.is_file() and canary_a in index.read_text(
        encoding="utf-8", errors="replace"
    )
    saved_in_working_folder = _holds(cap.root, canary_a)
    # The same save in default mode, a new session: whether a request for a path inside the
    # folder arrives is read from this turn's `asks`.
    await turn(
        Launch(new_id(), bot_memory_settings=settings_a),
        "default",
        _PROMPT_41_SAVE.format(canary=canary_c),
        folder_a,
        _SAVE_41,
    )
    default_mode_save_landed = _holds(folder_a, canary_c)
    fresh = await turn(
        Launch(new_id(), bot_memory_settings=settings_a),
        "default",
        _PROMPT_41_FRESH,
        folder_a,
        _READ_41,
    )
    # Bot B's folder is seeded only now, so Bot A's turns above found nothing else under <data>.
    folder_b = app_memory_directory(data, workspace, bot_b)
    folder_b.mkdir(parents=True, exist_ok=True)
    (folder_b / "MEMORY.md").write_text(
        f"# Bot memory\n\nThe synthetic canary is {canary_b}.\n", encoding="utf-8"
    )
    settings_b = write_settings(data / SETTINGS_FOLDER, cap.root, bot_b, workspace_id=workspace)
    await turn(
        Launch(session_x, bot_memory_settings=settings_b),
        "default",
        _PROMPT_41_READY,
        folder_b,
        _READ_41,
    )
    resumed = await turn(
        Launch(session_x, resume=True, bot_memory_settings=settings_a),
        "default",
        _PROMPT_41_RESUMED,
        folder_a,
        _READ_41,
    )
    gates = {
        "folder_absent_before": absent_before,
        "folder_created": created,
        "saved_in_folder": saved,
        "index_written": index_written,
        "fresh_recalled": canary_a in fresh,
        "turns_succeeded": all(item["success"] for item in turns),
    }
    return {
        "pass": all(gates.values()),
        **gates,
        # Reported, not gating: a save that landed in the working folder; the default-mode save
        # (landed, and whether any request came); the fresh recall of that save; and whether the
        # resumed session, which started with Bot B's settings, listed the canary of the folder
        # its resume names (A's: C1 or C3) or of the folder it started with (B's: C2).
        "saved_in_working_folder": saved_in_working_folder,
        "default_mode_save_landed": default_mode_save_landed,
        "default_mode_asked": bool(turns[1]["asks"]),
        "fresh_recalled_default_save": canary_c in fresh,
        "resume_recalled_own": canary_a in resumed or canary_c in resumed,
        "resume_recalled_previous": canary_b in resumed,
        "turns": turns,
    }


# --- profile-chat steer (ADR-062): does a mid-turn steer fold in, or wait out a Stop? ------------

STOP_DELAY_S = 3.0  # scenario 38 only: how long the Stop probe waits after its steer before
# interrupting, so the steer is still pending in the driver's _steers table when the turn ends.


def _command_lifecycle_of(frames: list[Frame], steer_id: str) -> list[dict[str, Any]]:
    """Every `command_lifecycle` frame naming `steer_id`, as its state and frame index."""
    return [
        {"state": f.get("state"), "index": i}
        for i, f in enumerate(frames)
        if f["type"] == "command_lifecycle" and f.get("command_uuid") == steer_id
    ]


def _results_of(frames: list[Frame]) -> list[dict[str, Any]]:
    return [
        {"subtype": f.get("subtype"), "index": i}
        for i, f in enumerate(frames)
        if f["type"] == "result"
    ]


def _inits_of(frames: list[Frame]) -> list[int]:
    return [i for i, f in enumerate(frames) if f["type"] == "system" and f.get("subtype") == "init"]


def _canary_at(frames: list[Frame], canary: str) -> int | None:
    return next((i for i, f in enumerate(frames) if canary in text_of([f])), None)


async def _steer_probe(
    cap: Capture, rec: Recorder, *, mode: str, sleep_seconds: int
) -> tuple[Live, str, Callable[[Ask], Answer], list[str], str, str]:
    """Open one session and run its PowerShell turn up to the tool_use frame (s35's
    `stop_when`), then send a steer naming a fresh canary word. Returns the live session, its
    session id, the permission callback (for the caller's own later collects), the tool-ask
    log, the steer id and the canary; the caller does the mode-specific collecting after this."""
    sid = new_id()
    live = await cap.open(rec, Launch(sid), mode=mode, effort="low", model="sonnet")
    cmd = f"Start-Sleep -Seconds {sleep_seconds}; Write-Output slept"
    asked: list[str] = []
    on_ask = expect_exact("PowerShell", cmd, asked)

    def tool_started(frame: Frame) -> bool:
        blocks = frame["message"]["content"] if frame["type"] == "assistant" else []
        return any(b.get("type") == "tool_use" for b in blocks)

    await live.control.send(
        f"Run exactly this PowerShell command and nothing else: {cmd}. Then reply with the "
        "single word done.",
        sid,
    )
    await cap.collect(live, on_ask=on_ask, stop_when=tool_started)
    steer_id = str(uuid.uuid4())
    canary = f"STEER_{uuid.uuid4().hex[:12]}"
    await live.control.send(f"Also reply with the word {canary}.", sid, steer_id=steer_id)
    return live, sid, on_ask, asked, steer_id, canary


_QUESTION_42 = (
    "Without using any tools, give the Bot canary from your instructions, then prime directive 1 "
    "from CLAUDE.md. If either is absent, say absent."
)


@scenario("42", "Bot system prompt: initialize appendSystemPrompt through /compact and resume")
async def s42(cap: Capture, rec: Recorder) -> Obs:
    # ADR-084: a Bot session's instructions and memory rule ride initialize's appendSystemPrompt
    # on every spawn. Its canary must reach the model beside the scratch CLAUDE.md on a first
    # turn, after /compact and after a resume, and a Bot session without it must not see it.
    # Each Bot gets its own memory folder, so a saved canary cannot reach the control session.
    # The app's Bot settings now skip that CLAUDE.md (scenario 43), so both keep the memory-only
    # file this was written with.
    canary = f"BOT_PROMPT_{uuid.uuid4().hex}"
    prompt = bot_system_prompt(f"You are the synthetic Bot. The Bot canary is {canary}.")
    data = cap.run_dir / "data"
    protection.create_private_directory(data)
    protection.require_private_directory(data)
    owner, control = new_id(), new_id()
    settings = _settings_variant(write_settings(data / SETTINGS_FOLDER, cap.root, owner))
    control_settings = _settings_variant(write_settings(data / SETTINGS_FOLDER, cap.root, control))
    session_id = new_id()
    turns: list[dict[str, Any]] = []

    def observe(step: str, frames: list[Frame]) -> None:
        text = text_of(frames)
        turns.append(
            {
                "step": step,
                "canary": canary in text,
                "project_instructions": "_raw" in text,
                "success": result_of(frames).get("subtype") == "success",
                # The prompt is no argv element: neither a flag nor the user's text.
                "argv_clean": not [
                    arg for arg in cap.argvs[-1] if "system-prompt" in arg or canary in arg
                ],
            }
        )

    launch = Launch(session_id, bot_memory_settings=settings, append_system_prompt=prompt)
    live = await cap.open(rec, launch)
    observe("first", await cap.turn(live, _QUESTION_42))
    compacted = await cap.turn(live, "/compact", timeout=300)
    compact_success = result_of(compacted).get("subtype") == "success"
    observe("after_compact", await cap.turn(live, _QUESTION_42))
    await cap.close(live)
    live = await cap.open(rec, replace(launch, resume=True))
    observe("resume", await cap.turn(live, _QUESTION_42))
    await cap.close(live)
    live = await cap.open(rec, Launch(new_id(), bot_memory_settings=control_settings))
    observe("control", await cap.turn(live, _QUESTION_42))
    await cap.close(live)
    prompted = [item for item in turns if item["step"] != "control"]
    return {
        "pass": compact_success
        and all(item["canary"] for item in prompted)
        and not any(item["canary"] for item in turns if item["step"] == "control")
        and all(
            item["project_instructions"] and item["success"] and item["argv_clean"]
            for item in turns
        ),
        "compact_success": compact_success,
        "turns": turns,
    }


# --- scenario 43: Bot settings skip the vault's own instruction files (ADR-085) -------------------

_QUESTION_43 = (
    "Without using any tools, reply with the code word your project instructions give, or NONE."
)


@scenario("43", "Bot settings skip the vault's CLAUDE.md and AGENTS.md")
async def s43(cap: Capture, rec: Recorder) -> Obs:
    # The app's Bot settings name the vault's instruction files in `claudeMdExcludes`. A canary in
    # an AGENTS.md that the vault's CLAUDE.md imports must reach a session whose settings lack the
    # key, and appear nowhere in one whose settings have it. The vault folder is named with a space.
    data = cap.run_dir / "data"
    protection.create_private_directory(data)
    protection.require_private_directory(data)
    canary, lone = (f"VAULT_RULES_{uuid.uuid4().hex}" for _ in range(2))
    vault, agents_only = cap.run_dir / "Bot vault 43", cap.run_dir / "Agents only 43"
    for folder, files in (
        (vault, {"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": f"The code word is {canary}.\n"}),
        (agents_only, {"AGENTS.md": f"The code word is {lone}.\n"}),
    ):
        folder.mkdir()
        for name, text in files.items():
            (folder / name).write_text(text, encoding="utf-8")
    settings = write_settings(data / SETTINGS_FOLDER, vault, new_id())
    lone_settings = write_settings(data / SETTINGS_FOLDER, agents_only, new_id())
    turns: dict[str, dict[str, Any]] = {}

    async def turn(step: str, folder: Path, path: str, word: str) -> None:
        session_id = new_id()
        live = await cap.open(rec, Launch(session_id, bot_memory_settings=path), cwd=folder)
        frames = await cap.turn(live, _QUESTION_43)
        await cap.close(live)
        transcript = _transcript(cap.cfg, folder, session_id)
        document = json.loads(Path(path).read_text(encoding="ascii"))
        turns[step] = {
            "settings_keys": sorted(document),
            "patterns": len(document.get("claudeMdExcludes", [])),
            "canary": word in json.dumps(frames, ensure_ascii=False),
            "transcript_found": transcript.is_file(),
            "transcript_canary": transcript.is_file()
            and word in transcript.read_text(encoding="utf-8", errors="replace"),
            "replied_none": re.search(r"\bnone\b", text_of(frames), re.IGNORECASE) is not None,
            "tools": [
                block.get("name")
                for frame in frames
                if frame["type"] == "assistant"
                for block in frame["message"].get("content", [])
                if block.get("type") == "tool_use"
            ],
            "success": result_of(frames).get("subtype") == "success",
            "argv": _argv_shape(cap.argvs[-1], cap.cfg.cli),
        }

    def clean(step: str) -> bool:
        """Whether that turn succeeded without a tool call, so only loaded text could answer."""
        return turns[step]["success"] and not turns[step]["tools"]

    def skipped(step: str) -> bool:
        item = turns[step]
        return clean(step) and not item["canary"] and not item["transcript_canary"]

    await turn("control", vault, _settings_variant(settings), canary)
    await turn("excluded", vault, settings, canary)
    await turn("agents_only_control", agents_only, _settings_variant(lone_settings), lone)
    await turn("agents_only_excluded", agents_only, lone_settings, lone)
    # Each pair proves the key: the control loads the canary, so its absence with the key is the
    # key's doing. The second pair is a vault with an AGENTS.md and no CLAUDE.md.
    gates = {
        "control_loaded": clean("control") and turns["control"]["canary"],
        "excluded_skipped": skipped("excluded"),
        "excluded_replied_none": turns["excluded"]["replied_none"],
        "agents_only_loaded": clean("agents_only_control")
        and turns["agents_only_control"]["canary"],
        "agents_only_skipped": skipped("agents_only_excluded"),
    }
    return {"pass": all(gates.values()), **gates, "turns": turns}


@scenario(
    "38",
    "profile-chat steer: a steer folds into a running tool turn; one waiting at Stop runs next",
)
async def s38(cap: Capture, rec: Recorder) -> Obs:
    # Fold: Claude Code is still mid-tool when the steer arrives, so it reads into that turn.
    fold_live, fold_sid, fold_ask, fold_asked, fold_steer, fold_canary = await _steer_probe(
        cap, rec, mode="default", sleep_seconds=8
    )
    await cap.collect(fold_live, on_ask=fold_ask)
    with contextlib.suppress(TimeoutError):
        await cap.collect(fold_live, on_ask=fold_ask, timeout=60)
    await cap.close(fold_live)
    fold_frames = fold_live.seen
    fold_text = f"Also reply with the word {fold_canary}."
    entries = [
        json.loads(line)
        for line in _transcript(cap.cfg, cap.root, fold_sid)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    steer_entries = [e for e in entries if fold_text in json.dumps(e, ensure_ascii=False)]
    fold_results = _results_of(fold_frames)
    fold_lifecycle = _command_lifecycle_of(fold_frames, fold_steer)
    fold_canary_at = _canary_at(fold_frames, fold_canary)
    fold_obs: Obs = {
        "lifecycle": fold_lifecycle,
        "results": fold_results,
        "inits": _inits_of(fold_frames),
        "canary_at": fold_canary_at,
        "asked": fold_asked,
        "steer_entries": [
            {
                "type": e.get("type"),
                "keys": sorted(e),
                "isMeta": e.get("isMeta"),
                "attachment_type": (e.get("attachment") or {}).get("type"),
                "commandMode": (e.get("attachment") or {}).get("commandMode"),
                "origin_present": "origin" in (e.get("attachment") or {}),
            }
            for e in steer_entries
        ],
    }
    fold_pass = (
        len(fold_results) == 1
        and fold_results[0]["subtype"] == "success"
        and any(
            entry["state"] in ("started", "completed") and entry["index"] < fold_results[0]["index"]
            for entry in fold_lifecycle
        )
        and fold_canary_at is not None
        and fold_canary_at < fold_results[0]["index"]
    )

    # Stop: the turn is interrupted before the steer is read, so it waits for the next one.
    # Claude Code 2.1.281 refuses a foreground `Start-Sleep 30` at once ("Blocked: ... use
    # Monitor" seen live 2026-11-06), so this probe reuses the Fold probe's 8 s step, which
    # actually runs; the interrupt lands a few seconds into it, while the steer is still queued.
    stop_live, _stop_sid, stop_ask, stop_asked, stop_steer, stop_canary = await _steer_probe(
        cap, rec, mode="default", sleep_seconds=8
    )
    await asyncio.sleep(STOP_DELAY_S)
    await stop_live.control.interrupt()
    await cap.collect(stop_live, on_ask=stop_ask)
    with contextlib.suppress(TimeoutError):
        await cap.collect(stop_live, on_ask=stop_ask, timeout=120)
    await cap.close(stop_live)
    stop_frames = stop_live.seen
    stop_results = _results_of(stop_frames)
    stop_lifecycle = _command_lifecycle_of(stop_frames, stop_steer)
    stop_inits = _inits_of(stop_frames)
    stop_canary_at = _canary_at(stop_frames, stop_canary)
    stop_obs: Obs = {
        "lifecycle": stop_lifecycle,
        "results": stop_results,
        "inits": stop_inits,
        "canary_at": stop_canary_at,
        "asked": stop_asked,
    }
    started = [entry for entry in stop_lifecycle if entry["state"] == "started"]
    stop_pass = (
        len(stop_results) == 2
        and stop_results[1]["subtype"] == "success"
        and bool(started)
        and started[0]["index"] > stop_results[0]["index"]
        and any(started[0]["index"] < i < stop_results[1]["index"] for i in stop_inits)
        and stop_canary_at is not None
        and stop_results[0]["index"] < stop_canary_at < stop_results[1]["index"]
    )

    native_version = next(
        (
            f.get("claude_code_version")
            for f in fold_frames + stop_frames
            if f["type"] == "system" and f.get("subtype") == "init"
        ),
        None,
    )
    return {
        "pass": fold_pass and stop_pass and native_version == FIXTURE_VERSION,
        "native_version": native_version,
        "fold_pass": fold_pass,
        "stop_pass": stop_pass,
        "fold": fold_obs,
        "stop": stop_obs,
    }


# --- ~/.claude reads (read-only): this run's transcripts, and the registry of refused scenarios --


def _project_dir(cfg: config.Config, root: Path) -> Path:
    return catalog.project_dir(cfg.claude_home, root)


def _transcript(cfg: config.Config, root: Path, session_id: str) -> Path:
    return _project_dir(cfg, root) / f"{session_id}.jsonl"


def _transcript_field(path: Path, key: str) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as fh:
        return {str(json.loads(line).get(key)) for line in fh if line.strip()}


_OUR_ENTRYPOINTS = frozenset({config.ENTRYPOINT, "sdk-py"})


def _small_recent_session(
    cfg: config.Config, root: Path, *, min_version: tuple[int, ...]
) -> Path | None:
    """The smallest transcript touched in the last 3 days whose entries include a CLI version
    at or above `min_version`, which no live process holds, and which this script did not write.

    That last clause matters for scenario 23: its fork leaves a copy behind, and a copy is always
    smaller and newer than the session it came from. Without the check, the next run picks its own
    leftover and reports that version skew works after testing a copy of a copy."""
    live_ids = {e.get("sessionId") for e in _registry(cfg)}
    cutoff = time.time() - 3 * 86400
    best: Path | None = None
    for path in _project_dir(cfg, root).glob("*.jsonl"):
        stat = path.stat()
        if (
            stat.st_mtime < cutoff
            or path.stem in live_ids
            or (best and stat.st_size >= best.stat().st_size)
        ):
            continue
        versions = _transcript_field(path, "version") - {"None"}
        if not any(
            tuple(int(p) for p in v.split(".")[:3]) >= min_version
            for v in versions
            if v[:1].isdigit()
        ):
            continue
        if _transcript_field(path, "entrypoint") & _OUR_ENTRYPOINTS:
            continue  # written by this script or the app, not a session the user had
        best = path
    return best


def _registry(cfg: config.Config) -> list[dict[str, Any]]:
    entries = []
    for path in (cfg.claude_home / "sessions").glob("*.json"):
        try:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
        except OSError, ValueError:
            continue
    return entries


def _vscode_cli() -> Path | None:
    found = sorted(Path.home().glob(VSCODE_CLI_GLOB))
    return found[-1] if found else None


def _registry_entry(cfg: config.Config, pid: int | None) -> dict[str, Any]:
    path = cfg.claude_home / "sessions" / f"{pid}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}


def _agent_meta_stopped_by_user(
    cfg: config.Config, root: Path, session_id: str, tool_use_id: str | None
) -> Any:
    """The `stoppedByUser` key of the `agent-<id>.meta.json` under `<session_id>/subagents/`
    whose `toolUseId` is `tool_use_id` (subagents_api._find's own match), or None if no such
    file matches. Reads that one key only."""
    folder = _project_dir(cfg, root) / session_id / "subagents"
    if tool_use_id is None or not folder.is_dir():
        return None
    for meta in folder.glob("agent-*.meta.json"):
        try:
            document = json.loads(meta.read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        if isinstance(document, dict) and document.get("toolUseId") == tool_use_id:
            return document.get("stoppedByUser")
    return None


def _sleepers() -> list[str]:
    """PIDs of shells running the scenario-16 sleep (excluding this probe's own shell)."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe' OR Name='pwsh.exe'\" | "
        "Where-Object { $_.ProcessId -ne $PID -and "
        "$_.CommandLine -like ('*Start-' + 'Sleep 30*') } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    done = winproc.run(["powershell", "-NoProfile", "-Command", ps], cwd=Path.cwd(), timeout=30)
    return [pid for pid in done.stdout.split() if pid.isdigit()]


# --- main -----------------------------------------------------------------------------------------


def _capture_name(key: str) -> str:
    """The capture file's stem. Scenario 23 forks one of the user's own sessions, so its file
    carries the marker scrub_fixture refuses: the rule was a docstring nothing enforced, and on
    disk that file looked exactly like the twenty that may be committed."""
    return f"{key}-owner-session-{DO_NOT_COMMIT}" if key == "23" else key


# Scenarios a scratch run refuses, and why. Each needs the vault, one of the user's own
# sessions, or the CLI's session registry under ~/.claude, none of which this script touches.
REFUSED: dict[str, str] = {
    "05": "tests the vault's hooks",
    "05b": "tests the vault's hooks",
    "24": "runs a vault skill",
    "23": "forks one of your own sessions",
    "15": "reads its own process's ~/.claude/sessions registry file",
    "20": "reads its own process's ~/.claude/sessions registry file",
}

# Scenarios a default run skips but naming them still runs: each needs a maintainer's recorded go
# (AGENTS.md hard rule 4 (b)), not a vault or session-registry read REFUSED stays out for.
NAMED_ONLY: dict[str, str] = {
    "29": "passes --mcp-config or resumes a missing id: run only on a recorded go",
    "30": "passes --mcp-config or resumes a missing id: run only on a recorded go",
    "31": "passes --mcp-config or resumes a missing id: run only on a recorded go",
    "32": "passes --mcp-config or resumes a missing id: run only on a recorded go",
    "33": "a paid live Stop test",
    "34": "a paid live turn and a pre-initialize control call: run only on a recorded go",
    "35": "a paid live turn with a mid-turn send: run only on a recorded go",
    "36": (
        "a paid live run with two background agents, a stop_task and a Stop: run only on "
        "a recorded go"
    ),
    "37": "four paid live turns with per-Bot --settings: run only on a recorded go",
    "38": "three paid live turns probing profile-chat steer: run only on a recorded go",
    "39": "synthetic gateway, Bot memory and app MCP: run only on a recorded go",
    "40": (
        "one paid native turn in a Bot code copy, with Bot memory and app MCP, alone in the "
        "clean Linux guest: run only on a recorded go"
    ),
    "41": (
        "five paid live turns with per-Bot --settings naming a memory folder outside the "
        "working folder: run only on a recorded go"
    ),
    "42": (
        "four paid live turns and a /compact with a Bot's initialize appendSystemPrompt: run "
        "only on a recorded go"
    ),
    "43": (
        "four paid live turns in throwaway vault folders with Bot --settings with and without "
        "claudeMdExcludes: run only on a recorded go"
    ),
    "44": (
        "two paid live turns with a Bot's settings and write-fence hooks in a throwaway vault: "
        "run only on a recorded go"
    ),
}

# The whole scratch project: what scenario 01 asks about (`_raw/`) and what 06, 26 and 28 look
# for (`_templates/`), synthetic and nothing more. No .claude, hooks, skills or Git.
SCRATCH_FILES: dict[str, str] = {
    "CLAUDE.md": (
        "# Scratch project\n\nPrime directive 1: raw sources live in _raw/ and are never edited.\n"
    ),
    "_templates/daily-note.md": "Daily note template.\n",
    "_templates/weekly.md": "Weekly review template.\n",
    "_templates/meeting.md": "Meeting notes template.\n",
}


def seed_scratch(root: Path) -> None:
    for name, text in SCRATCH_FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@contextlib.contextmanager
def _isolated_39(cli: Path, *, parent: Path | None = None) -> Iterator[Path]:
    inherited = dict(os.environ)
    parent = parent or (
        Path(tempfile.gettempdir()) if sys.platform == "win32" else Path.home() / ".cache"
    )
    parent.mkdir(parents=True, exist_ok=True)
    scratch = parent / f"viki-c39-{uuid.uuid4().hex[:16]}"
    protection.create_private_directory(scratch)
    protection.require_private_directory(scratch)
    home, data, temp = scratch / "home", scratch / "data", scratch / "temp"
    appdata = home / "AppData"
    for path in (
        home,
        data,
        temp,
        home / ".claude",
        home / ".anthropic",
        appdata,
        appdata / "Roaming",
        appdata / "Local",
        scratch / "xdg-config",
    ):
        protection.create_private_directory(path)
        protection.require_private_directory(path)
    isolated = _without_provider_env(inherited)
    isolated.pop("DISABLE_AUTOUPDATER", None)
    isolated.pop("VIKI_VAULT", None)
    isolated.pop("VIKI_EDGE", None)
    isolated.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(appdata / "Roaming"),
            "LOCALAPPDATA": str(appdata / "Local"),
            "XDG_CONFIG_HOME": str(scratch / "xdg-config"),
            "XDG_DATA_HOME": str(data),
            "TMP": str(temp),
            "TEMP": str(temp),
            "TMPDIR": str(temp),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"),
            "ANTHROPIC_CONFIG_DIR": str(home / ".anthropic"),
            "NO_PROXY": "127.0.0.1,localhost",
            "VIKI_DATA_DIR": str(data),
            "VIKI_CLAUDE_CLI": str(cli),
        }
    )
    try:
        os.environ.clear()
        os.environ.update(isolated)
        yield scratch
    finally:
        os.environ.clear()
        os.environ.update(inherited)


async def _run_39() -> int:
    override = os.environ.get("VIKI_CLAUDE_CLI")
    if override and not Path(override).is_absolute():
        print("39 requires an absolute CLI override.", file=sys.stderr)
        return 2
    cli, _problem, _source = config._cli(os.environ)
    if cli is None or not cli.is_absolute():
        print("39 requires a usable absolute CLI path.", file=sys.stderr)
        return 2
    with _isolated_39(cli) as scratch:
        cfg = config.load()
        if cfg.cli != cli:
            print("39 CLI changed during isolation.", file=sys.stderr)
            return 2
        run_dir = cfg.captures_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        root = run_dir / "scratch"
        run_dir.mkdir(parents=True, exist_ok=True)
        seed_scratch(root)
        version_env = winproc.child_env(config.ENTRYPOINT, pin_updates=cfg.cli_source == "bundled")
        version_env["ANTHROPIC_CONFIG_DIR"] = os.environ["ANTHROPIC_CONFIG_DIR"]
        version_env["ANTHROPIC_API_KEY"] = ""
        version = winproc.run(
            [str(cli), "--version"], cwd=root, env=version_env, timeout=10
        ).stdout.strip()
        match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)
        if match is None or tuple(map(int, match.groups())) < (2, 1, 281):
            print("39 requires checked Claude Code 2.1.281 or later.", file=sys.stderr)
            return 2
        cap = Capture(cfg, run_dir, root, isolation_root=scratch)
        rec = Recorder(run_dir / "39.jsonl", "39")
        try:
            try:
                obs: Obs = await s39(cap, rec)
            except Exception:  # noqa: BLE001 - failed compatibility is an observation, not a pass
                obs = {"pass": False}
        finally:
            try:
                await finish_cleanup(cap.close_all(redacted=True))
            finally:
                rec.close()
        obs["session_ids"] = list(dict.fromkeys(cap.opened))
        summary = {
            "scenario": "39",
            "platform": sys.platform,
            "native_version": match.group(0),
            "observations": obs,
        }
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[39] {'PASS' if obs.get('pass') else 'FAIL'}")
        print(f"summary: {run_dir / 'summary.json'}")
        return 0 if obs.get("pass") else 1


class _NoRawRecorder(Recorder):
    """Scenario 40's recorder persists nothing: the scenario reads its frames in memory, and only
    `_evidence_40`'s whitelist reaches disk."""

    def __init__(self) -> None:  # unlike Recorder, opens no file
        pass

    def __call__(self, direction: str, frame: Frame) -> None:
        pass

    def close(self) -> None:
        pass


def _guest_refusal() -> str | None:
    """Why this process is not scenario 40's clean Linux guest, judged before any discovery,
    or None. The guest's own default home holds the sign-in, and one absolute executable serves
    both that sign-in and the capture."""
    if sys.platform != "linux":
        return "it runs only in the clean Linux guest."
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return "CLAUDE_CONFIG_DIR is set; the guest's default home must hold the sign-in."
    forwarded = sorted(set(os.environ) - set(_without_provider_env(os.environ)))
    if forwarded:  # names only, never values
        return "provider, credential or proxy variables are set: " + ", ".join(forwarded)
    if not Path(os.environ.get("VIKI_CLAUDE_CLI", "")).is_absolute():
        return "VIKI_CLAUDE_CLI must name the guest's one absolute Claude Code executable."
    return None


_EVIDENCE_40 = (
    "pass",
    "native_login",
    "not_logged_in",
    "mcp_listed",
    "memory_loaded",
    "exact_approved_once",
    "turn_success",
    "copy_file",
    "copy_commit",
    "synthetic_author",
    "original_untouched",
    "hooks_off",
    "cwd_scratch",
    "exact_command_only",  # reported only; `pass` does not include it
    "add_dir_skill_listed",  # reported only; `pass` does not include it
)
# Each diagnosis field and its type: a flag, or a count reported as 0..9 (9 means 9 or more).
_DIAGNOSIS_40: dict[str, type] = {
    "asks": int,
    "bash_asks": int,
    "exact_asks": int,
    "near_asks": int,
    "script_asks": int,
    "tool_calls": int,
    "bash_calls": int,
    "approved_errored": bool,
    "note_exists": bool,
    "commits_after_base": int,
}
_ARGV_SHAPE = re.compile(r"[-a-z=<>]+")  # flags and placeholders: no path, id or digit


def _diagnostic(kind: type, value: Any) -> bool | int:
    """A flag is True only for True; a count is an int clamped to 0..9, and any other value,
    a bool included, is 0. So no content can leave through a diagnosis field."""
    if kind is bool:
        return value is True
    return min(max(value, 0), 9) if type(value) is int else 0


def _evidence_40(version: str, obs: Obs) -> dict[str, Any]:
    """Scenario 40's summary, built only from its whitelists before anything is written: the CLI
    version, the argv shape (any element that is not a flag or placeholder is redacted), the
    fixed synthetic author, booleans and small counts. Nothing else in `obs` is copied."""
    argv = obs.get("argv")
    diagnosis = obs.get("diagnosis")
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    return {
        "scenario": "40",
        "platform": sys.platform,
        "native_version": version,
        "synthetic_author": _GIT_AUTHOR,
        "argv": [
            arg if isinstance(arg, str) and _ARGV_SHAPE.fullmatch(arg) else "<redacted>"
            for arg in (argv if isinstance(argv, list) else [])
        ],
        "observations": {key: obs.get(key) is True for key in _EVIDENCE_40},
        "diagnosis": {
            key: _diagnostic(kind, diagnosis.get(key)) for key, kind in _DIAGNOSIS_40.items()
        },
    }


async def _run_40() -> int:
    refusal = _guest_refusal()
    if refusal:
        print(f"40 refused: {refusal}", file=sys.stderr)
        return 2
    cli = Path(os.environ["VIKI_CLAUDE_CLI"])
    cfg = config.load()
    if cfg.cli != cli:
        print("40 refused: VIKI_CLAUDE_CLI is not a usable executable.", file=sys.stderr)
        return 2
    run_dir = cfg.captures_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    root = run_dir / "scratch"
    # The same preflight as every other run, over the guest's own settings.
    problems = config.billing_problems(replace(cfg, vault=root))
    if problems:
        print("Billing preflight failed:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True)
    seed_scratch(root)
    version = winproc.run([str(cli), "--version"], cwd=root, timeout=10).stdout
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", version)
    if match is None or tuple(map(int, match.groups())) < (2, 1, 281):
        print("40 requires checked Claude Code 2.1.281 or later.", file=sys.stderr)
        return 2
    cap = Capture(cfg, run_dir, root)
    try:
        try:
            obs = await s40(cap, _NoRawRecorder())
        except Exception:  # noqa: BLE001 - a failed run is evidence, not a pass
            obs = {}
    finally:
        await finish_cleanup(cap.close_all(redacted=True))
    summary = _evidence_40(match.group(0), obs)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    passed = summary["observations"]["pass"]
    print(f"[40] {'PASS' if passed else 'FAIL'}")
    print(f"summary: {run_dir / 'summary.json'}")
    if obs.get("native_login") is False:
        # Any refusal: a signed-out guest may answer with a non-empty account (`billing`).
        print(
            "40 stopped before any turn: the initialize reply did not show a subscription "
            "login. If this guest is not signed in to your Claude subscription, sign in "
            "yourself, in your own terminal attached to this guest, as this user with this "
            "default home:\n  "
            + shlex.join(login_argv([str(cli)]))
            + "\nOpen the URL it prints in your browser and paste the code back only into that "
            "terminal. Then run scenario 40 again.",
            file=sys.stderr,
        )
        return 2
    return 0 if passed else 1


async def run(keys: list[str]) -> int:
    if "39" in keys:
        if keys != ["39"]:
            print("39 must run alone.", file=sys.stderr)
            return 2
        return await _run_39()
    if "40" in keys:
        if keys != ["40"]:
            print("40 must run alone.", file=sys.stderr)
            return 2
        return await _run_40()
    refused = [key for key in keys if key in REFUSED]
    for key in refused:
        print(f"[{key}] refused: {REFUSED[key]}", file=sys.stderr)
    if refused:
        return 2
    cfg = config.load()
    if cfg.cli is None:
        print(f"No usable claude CLI: {cfg.cli_problem}", file=sys.stderr)
        return 2
    if cfg.claude_config_dir_set:
        # The CLI would read its settings and write its transcripts in that folder, while the
        # preflight and the transcript checks look under ~/.claude. Never print its value.
        print("CLAUDE_CONFIG_DIR is set; unset it before a capture run.", file=sys.stderr)
        return 2
    run_dir = cfg.captures_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
    root = run_dir / "scratch"
    # The scratch root stands in for the vault, so its project settings are the ones read: it
    # has none, and only the user-level and managed settings count.
    problems = config.billing_problems(replace(cfg, vault=root))
    if problems:
        print("Billing preflight failed:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    run_dir.mkdir(parents=True, exist_ok=True)
    seed_scratch(root)
    cli_version = winproc.run([str(cfg.cli), "--version"], cwd=root, timeout=10).stdout.strip()
    cap = Capture(cfg, run_dir, root)
    summary: dict[str, Any] = {
        "cli": str(cfg.cli),
        "cli_version": cli_version,
        "run_dir": str(run_dir),
        "scratch_root": str(root),
        "results": {},
    }
    for key in keys:
        title, fn = SCENARIOS[key]
        rec = Recorder(run_dir / f"{_capture_name(key)}.jsonl", key)
        print(f"[{key}] {title} ...", flush=True)
        cap.opened.clear()
        try:
            obs = await fn(cap, rec)
        except Exception as exc:  # noqa: BLE001 - one broken scenario must not stop the run
            obs = {"pass": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
        finally:
            await cap.close_all()  # before rec.close(), so a closing frame still has a file
            rec.close()
        obs["session_ids"] = list(dict.fromkeys(cap.opened))  # a respawn reopens the same id
        summary["results"][key] = {"title": title, **obs}
        print(f"[{key}] {'PASS' if obs.get('pass') else 'FAIL'}", flush=True)
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"summary: {run_dir / 'summary.json'}")
    return 0


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # the console may be cp1252
    parser = argparse.ArgumentParser(description="Live Claude Code capture scenarios")
    parser.add_argument(
        "keys", nargs="*", help="scenario keys (default: all not refused and not named-only)"
    )
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for key, (title, _) in SCENARIOS.items():
            print(f"{key}  {title}" + ("  [named-only]" if key in NAMED_ONLY else ""))
        return
    unknown = [k for k in args.keys if k not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenarios: {unknown}")
    default = [key for key in SCENARIOS if key not in REFUSED and key not in NAMED_ONLY]
    sys.exit(asyncio.run(run(args.keys or default)))


if __name__ == "__main__":
    main()
