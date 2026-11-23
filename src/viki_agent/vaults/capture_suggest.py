"""One inbox triage suggestion run: a single VIKI turn in a new background session.

`CaptureSuggester.start` refuses while a run is running anywhere in the app; otherwise it lists a
vault's captures, refuses with `inbox_changed` when a start bound to the state it checked
(`bound_listing`) lists another one, stores the new session's mode and effort, and creates the
session with read-only tools (`Launch.read_only_tools`) itself, under its lock, before starting a
run as a task in the caller's `tasks` set and handing out the run's id: the read-only actor exists
under that id before anyone outside can learn it. The task sends `build_prompt`'s text,
denies every card from the task itself and interrupts the turn at its deadline. Only `parse_reply`
turns the reply into suggestions: a successful run replaces that vault's map, and a failed run
keeps it.
With a `SuggestionMemory`, each vault's latest run and map outlive a restart: they are loaded as the
suggester is built, saved before a run's session is created and again once its session is closed.
A run still running when VIKI Agent closed loads as failed with `app_closed`.
Every ending closes the session, and the log lines here name an exception type at most, never
capture, prompt or reply text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from anyio.to_thread import run_sync

from viki_agent.automation.routines import parse_stamp, stamp
from viki_agent.vaults.capture_reply import (
    DESTINATIONS,
    MAX_WHY_CHARS,
    Suggestion,
    build_prompt,
    parse_reply,
)
from viki_agent.vaults.captures import CaptureError, CaptureListing
from viki_agent.vaults.suggestion_memory import SuggestionMemory

log = logging.getLogger(__name__)

# What a start VIKI makes may send: Decision's inbox host sets it around its own call
# (`SuggestionHost.start`), so the task that start runs in carries it. It answers whether the fresh
# listing is the inbox state whose Never send the cycle checked; a capture written since, perhaps
# under a held folder, must not reach the prompt. The user's own Ask VIKI to suggest never sets
# it and sends what it lists.
bound_listing: ContextVar[Callable[[CaptureListing], bool] | None] = ContextVar(
    "bound_listing", default=None
)

DEADLINE_S = 300.0
MAX_REPLY_CHARS = 262_144
MODE = "default"
EFFORT = "low"


class _State(Protocol):
    def remember(self, session_id: str, **changes: Any) -> object: ...


class _Session(Protocol):
    def subscribe(self, subscriber: Callable[[dict[str, Any]], None]) -> Callable[[], None]: ...

    async def send(self, text: str) -> bool | None: ...

    def deny_pending(self) -> None: ...

    async def interrupt(self) -> None: ...


class _Host(Protocol):
    """What a run needs of `SessionManager`."""

    @property
    def app_state(self) -> _State: ...

    def native_model(self) -> str | None: ...

    def create(self, session_id: str, *, read_only_tools: bool) -> _Session: ...

    async def close(self, session_id: str) -> None: ...


class _Captures(Protocol):
    def list(self, workspace_id: str) -> CaptureListing: ...


@dataclass(frozen=True)
class SuggestionRun:
    run_id: str
    workspace_id: str
    session_id: str
    state: Literal["running", "done", "failed"]
    started_at: str
    finished_at: str | None = None
    reason: str | None = None
    suggested: int = 0
    rejected: int = 0


# The run-reason list the UI has a fixed sentence for. A driver error
# code outside it (e.g. bad_request, provider_disconnected, frame_error, forced_stop,
# transcript_missing) is recorded as "error" instead, so that sentence always applies.
_RUN_REASONS = frozenset(
    {
        "too_many_sessions",
        "cli_missing",
        "not_logged_in",
        "billing",
        "crashed",
        "not_sent",
        "deadline",
        "cancelled",
        "error",
        "no_suggestions",
        "app_closed",
        "internal",
    }
)


@dataclass(eq=False)  # hashable by identity: the actor keeps its subscribers in a set
class _Turn:
    """A session subscriber that records what the run reads and wakes it; it calls nothing."""

    wake: asyncio.Event = field(default_factory=asyncio.Event)
    card: bool = False
    status: str | None = None
    error: str | None = None
    closed: str | None = None
    order: list[str] = field(default_factory=list)
    deltas: dict[str, str] = field(default_factory=dict)
    finished: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Every block's text, in the order each first arrived: its finished text, or, for a
        block the turn ended before finishing, its text so far from `deltas`. Joined as today (a
        line break between blocks keeps a fence that opens a block on its own line) and capped
        as a whole."""
        return "\n".join(
            self.finished.get(block_id, self.deltas.get(block_id, "")) for block_id in self.order
        )[:MAX_REPLY_CHARS]

    def __call__(self, envelope: dict[str, Any]) -> None:
        kind, data = envelope["type"], envelope["data"]
        if kind == "text.delta":
            self._delta(data["block_id"], data.get("text", ""))
        elif kind == "text.done":
            self._finish(data["block_id"], data.get("text", ""))
        elif kind in ("permission.requested", "question.asked"):
            self.card = True
        elif kind == "turn.done":
            self.status = self.status or data.get("status")
        elif kind == "error":
            if not self.error:
                code = data.get("code")
                self.error = code if code in _RUN_REASONS else "error"
        elif kind == "session.closed":
            self.closed = self.closed or data.get("reason")
        else:
            return
        self.wake.set()

    def _mark(self, block_id: str) -> None:
        if block_id not in self.deltas and block_id not in self.finished:
            self.order.append(block_id)

    def _delta(self, block_id: str, chunk: str) -> None:
        self._mark(block_id)
        text = self.deltas.get(block_id, "")
        if len(text) < MAX_REPLY_CHARS:
            self.deltas[block_id] = text + chunk

    def _finish(self, block_id: str, text: str) -> None:
        self._mark(block_id)
        # A block that streamed deltas keeps them: the mapper's own text.done for it is cut at
        # MAX_TEXT_CHARS and can lose a long viki-inbox block's closing fence.
        self.finished[block_id] = self.deltas.get(block_id, text)


class CaptureSuggester:
    """Starts suggestion runs, one at a time per app, and keeps each vault's last suggestions."""

    def __init__(
        self,
        host: _Host,
        captures: _Captures,
        *,
        tasks: set[asyncio.Task[None]],
        memory: SuggestionMemory | None = None,
        deadline_s: float = DEADLINE_S,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._host = host
        self._captures = captures
        self._tasks = tasks
        self._memory = memory
        self._deadline_s = deadline_s
        self._wall = wall
        self._lock = asyncio.Lock()
        self._runs: dict[str, SuggestionRun] = {}  # each vault's latest run
        self._suggestions: dict[str, dict[str, Suggestion]] = {}
        loaded = None if memory is None else memory.load("suggestions")
        for workspace_id, value in (loaded or {}).items():
            kept = _decode(workspace_id, value, stamp(wall()))
            if kept is not None:
                run, suggestions = kept
                if run is not None:
                    self._runs[workspace_id] = run
                if suggestions:
                    self._suggestions[workspace_id] = suggestions

    def run_for(self, workspace_id: str) -> SuggestionRun | None:
        return self._runs.get(workspace_id)

    def suggestions(self, workspace_id: str) -> Mapping[str, Suggestion]:
        return self._suggestions.get(workspace_id, {})

    async def start(self, workspace_id: str) -> SuggestionRun:
        async with self._lock:
            running = next((run for run in self._runs.values() if run.state == "running"), None)
            if running is not None:
                raise CaptureError("suggestion_running", run_id=running.run_id)
            listing = await run_sync(self._captures.list, workspace_id)
            if not listing.captures:
                raise CaptureError("no_captures")
            bound = bound_listing.get()
            if bound is not None and not bound(listing):
                raise CaptureError("inbox_changed")
            session_id = str(uuid.uuid4())
            state = self._host.app_state
            await run_sync(
                lambda: state.remember(
                    session_id, model=self._host.native_model(), mode=MODE, effort=EFFORT
                )
            )
            run = SuggestionRun(
                run_id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                session_id=session_id,
                state="running",
                started_at=stamp(self._wall()),
            )
            # Saved before the session exists: a cancel while saving leaves no session behind,
            # and a run saved as running that never ends loads as failed after a restart.
            await self._save(workspace_id, run)
            # Capture text is untrusted and nobody answers this turn's cards: the session starts
            # with only Read, Glob and Grep and no MCP server (hard rule 4's
            # third exception). Created here, under the lock, before the id is handed out in the 202
            # below: an Open chat reaching `_actor_for(start_if_new=True)` in a gap after that
            # would otherwise make its own normal-tools actor, later orphaned when this run's own
            # create replaced it.
            session = self._host.create(session_id, read_only_tools=True)
            self._runs[workspace_id] = run
            task = asyncio.create_task(self._drive(run, session, listing))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return run

    async def _drive(self, run: SuggestionRun, session: _Session, listing: CaptureListing) -> None:
        session_id = run.session_id
        deadline = asyncio.get_running_loop().time() + self._deadline_s
        turn = _Turn()
        unsubscribe: Callable[[], None] | None = None
        try:
            unsubscribe = session.subscribe(turn)
            self._end(run, *await self._outcome(run, session, turn, listing, deadline))
        except asyncio.CancelledError:
            self._end(run, "app_closed")
            raise
        except Exception as exc:
            log.warning("capture suggestion run failed: %s", type(exc).__name__)
            self._end(run, "internal")
        finally:
            if unsubscribe is not None:
                unsubscribe()
            try:
                await self._host.close(session_id)
            except Exception as exc:
                log.warning("capture suggestion session close failed: %s", type(exc).__name__)
            await self._save(run.workspace_id)

    async def _outcome(
        self,
        run: SuggestionRun,
        session: _Session,
        turn: _Turn,
        listing: CaptureListing,
        deadline: float,
    ) -> tuple[str | None, int, int]:
        """(failure reason, or None for done; suggested; rejected). A done run stores its map."""
        if not await session.send(build_prompt(listing)):
            return turn.error or "not_sent", 0, 0
        while True:
            turn.wake.clear()
            if turn.card:
                turn.card = False
                session.deny_pending()
            if turn.status is not None or turn.closed is not None:
                break
            try:
                async with asyncio.timeout_at(deadline):
                    await turn.wake.wait()
            except TimeoutError:
                with contextlib.suppress(Exception):
                    await session.interrupt()
                return "deadline", 0, 0
        if turn.status != "success":
            # Closed before the turn ended: by the app when the browser left, else on request.
            closed = "app_closed" if turn.closed == "browser_gone" else "cancelled"
            return turn.error or turn.status or closed, 0, 0
        parsed = parse_reply(turn.text, listing, stamp(self._wall()))
        if not parsed or not parsed[0]:
            return "no_suggestions", 0, parsed[1] if parsed else 0
        suggestions, rejected = parsed
        self._suggestions[run.workspace_id] = {item.ref: item for item in suggestions}
        return None, len(suggestions), rejected

    async def _save(self, workspace_id: str, starting: SuggestionRun | None = None) -> None:
        """Save the vault's run (`starting`, else its latest as the save writes) and its current
        suggestions, when there is a memory."""
        if self._memory is None:
            return

        def value() -> object:
            run = starting or self._runs.get(workspace_id)
            kept = self._suggestions.get(workspace_id, {})
            return {
                "run": None if run is None else asdict(run),
                "suggestions": [asdict(item) for item in kept.values()],
            }

        await self._memory.save(workspace_id, "suggestions", value)

    def _end(
        self, run: SuggestionRun, reason: str | None, suggested: int = 0, rejected: int = 0
    ) -> None:
        self._runs[run.workspace_id] = replace(
            run,
            state="done" if reason is None else "failed",
            finished_at=stamp(self._wall()),
            reason=reason,
            suggested=suggested,
            rejected=rejected,
        )


def _decode(
    workspace_id: str, value: object, now: str
) -> tuple[SuggestionRun | None, dict[str, Suggestion]] | None:
    """A saved vault's run and suggestions, or None when the value is not the shape `_save`
    writes. A run saved as running ended when VIKI Agent closed: it reads as failed."""
    if not isinstance(value, dict) or set(value) != {"run", "suggestions"}:
        return None
    raw_run, raw_items = value["run"], value["suggestions"]
    run = None if raw_run is None else _run(raw_run, workspace_id)
    if (raw_run is not None and run is None) or not isinstance(raw_items, list):
        return None
    if run is not None and run.state == "running":
        run = replace(run, state="failed", finished_at=now, reason="app_closed")
    suggestions: dict[str, Suggestion] = {}
    for raw in raw_items:
        item = _suggestion(raw)
        if item is None:
            return None
        suggestions[item.ref] = item
    return run, suggestions


def _run(raw: object, workspace_id: str) -> SuggestionRun | None:
    names = {field.name for field in fields(SuggestionRun)}
    if not isinstance(raw, dict) or set(raw) != names:
        return None
    try:
        run = SuggestionRun(**raw)
        for value in (run.run_id, run.session_id):
            if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                return None
        parse_stamp(run.started_at)
        if run.finished_at is not None:
            parse_stamp(run.finished_at)
    except TypeError, ValueError:
        return None
    if (
        run.workspace_id != workspace_id
        or run.state not in ("running", "done", "failed")
        or (run.reason is not None and run.reason not in _RUN_REASONS)
        or not all(isinstance(n, int) and n >= 0 for n in (run.suggested, run.rejected))
    ):
        return None
    return run


def _suggestion(raw: object) -> Suggestion | None:
    names = {field.name for field in fields(Suggestion)}
    if not isinstance(raw, dict) or set(raw) != names:
        return None
    item = Suggestion(**raw)
    optional = (item.revision, item.project, item.why)
    if (
        not all(isinstance(text, str) for text in (item.ref, item.modified, item.to, item.at))
        or not all(text is None or isinstance(text, str) for text in optional)
        or item.to not in DESTINATIONS
        or (item.project is None) == (item.to == "task")
        or (item.why is not None and len(item.why) > MAX_WHY_CHARS)
    ):
        return None
    return item
