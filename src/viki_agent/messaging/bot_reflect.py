"""Reflect and dream runs: one read-only learning turn for one Bot, and their automatic starts.

`LearningRunner.start` runs one at a time per app, for a Bot of the active, ordinary vault:

1. it picks the episodes to learn from: for `reflect` the Bot's newest untainted episodes no
   reflect run has read (at most REFLECT_EPISODES), for `dream` the untainted ones finished since
   its last dream (at most DREAM_EPISODES);
2. reads their excerpts (`bot_excerpts`) and the Bot's records, and builds the prompt
   (`dream_reply.build_prompt`);
3. creates a new session with only Read, Glob and Grep and no MCP server (`read_only_tools`,
   hard rule 4's third exception, ADR-090), sends that one turn, denies every card it raises
   and interrupts it at its deadline, then closes it;
4. reads the reply strictly (`dream_reply.parse_reply`), checks it against the snapshot the prompt
   showed (`dream_ops.check`) and seals what passes as proposals for the owner
   (`bot_proposals`); a dream adds code's own retirements of dormant lessons. Lesson scores are
   counted at once: they change no vault file.

`AutoLearning` starts the same runs without a click while `bots.auto_reflect` or
`bots.auto_dream` is on, checked every TICK_S: a reflect once REFLECT_MIN_EPISODES unread episodes
wait, a dream once DREAM_MIN_EPISODES new episodes and DREAM_GAP since the last dream, and only
after the Bot has been quiet for QUIET. Each start counts against a daily limit per Bot.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from anyio.to_thread import run_sync

from viki_agent.automation.routines import parse_stamp, stamp
from viki_agent.messaging.bot_excerpts import Excerpt, read_excerpts
from viki_agent.messaging.bot_learning import BotLearning, LearningError
from viki_agent.messaging.bot_proposals import Run
from viki_agent.messaging.dream_ops import check, dormant_retirements, views
from viki_agent.messaging.dream_reply import Mode, build_prompt, parse_reply
from viki_agent.vaults.memory import MemoryServiceError

log = logging.getLogger(__name__)

REFLECT_EPISODES = 5
DREAM_EPISODES = 20
DEADLINE_S = 600.0
MAX_REPLY_CHARS = 262_144
EFFORT: Mapping[Mode, str] = {"reflect": "low", "dream": "medium"}
TICK_S = 600.0
REFLECT_MIN_EPISODES = 3
DREAM_MIN_EPISODES = 10
DREAM_GAP = timedelta(hours=24)
QUIET = timedelta(minutes=15)
DAILY_REFLECTS = 6
DAILY_DREAMS = 1
_DRIVER_REASONS = frozenset(
    {"too_many_sessions", "cli_missing", "not_logged_in", "billing", "crashed"}
)


class _Session(Protocol):
    def subscribe(self, subscriber: Callable[[dict[str, Any]], None]) -> Callable[[], None]: ...

    async def send(self, text: str) -> bool | None: ...

    def deny_pending(self) -> None: ...

    async def interrupt(self) -> None: ...


class _State(Protocol):
    def remember(self, session_id: str, **changes: Any) -> object: ...


class _Host(Protocol):
    @property
    def app_state(self) -> _State: ...

    def native_model(self) -> str | None: ...

    def create(self, session_id: str, *, read_only_tools: bool) -> _Session: ...

    async def close(self, session_id: str) -> None: ...


@dataclass(eq=False)  # hashable by identity: an actor keeps its subscribers in a set
class _Turn:
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    card: bool = False
    status: str | None = None
    error: str | None = None
    closed: str | None = None
    order: list[str] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(self.texts[block] for block in self.order)[:MAX_REPLY_CHARS]

    def __call__(self, envelope: dict[str, Any]) -> None:
        kind, data = envelope.get("type"), envelope.get("data") or {}
        if kind in ("text.delta", "text.done"):
            block = str(data.get("block_id", ""))
            if block not in self.texts:
                self.order.append(block)
                self.texts[block] = ""
            if kind == "text.delta":
                if len(self.texts[block]) < MAX_REPLY_CHARS:
                    self.texts[block] += str(data.get("text", ""))
            elif not self.texts[block]:
                self.texts[block] = str(data.get("text", ""))
            return
        if kind in ("permission.requested", "question.asked"):
            self.card = True
        elif kind == "turn.done":
            self.status = self.status or data.get("status")
        elif kind == "error":
            code = data.get("code")
            self.error = self.error or (code if code in _DRIVER_REASONS else "error")
        elif kind == "session.closed":
            self.closed = self.closed or data.get("reason")
        else:
            return
        self.wake.set()


@dataclass(frozen=True)
class _Plan:
    run: Run
    mode: Mode
    excerpts: tuple[Excerpt, ...]
    prompt: str
    records: tuple[Any, ...]
    items: dict[str, Any]
    bot_name: str


class LearningRunner:
    def __init__(
        self,
        host: _Host,
        learning: BotLearning,
        paths_for: Callable[[list[str]], dict[str, Path]],
        *,
        tasks: set[asyncio.Task[Any]],
        deadline_s: float = DEADLINE_S,
    ) -> None:
        self._host = host
        self._learning = learning
        self._paths_for = paths_for
        self._tasks = tasks
        self._deadline_s = deadline_s
        self._lock = asyncio.Lock()
        self._running: Run | None = None

    @property
    def running(self) -> Run | None:
        return self._running

    async def start(
        self, bot_id: str, mode: Mode, trigger: Literal["user", "auto"] = "user"
    ) -> Run:
        """Start one run; LearningError says why not (`run_running`, `not_active`,
        `vault_not_ordinary`, `no_episodes`, `no_excerpts`, or a store or vault fault)."""
        async with self._lock:
            if self._running is not None:
                raise LearningError("run_running")
            plan = await run_sync(lambda: self._plan(bot_id, mode, trigger))
            session = self._host.create(plan.run.session_id or "", read_only_tools=True)
            self._running = plan.run
            task = asyncio.create_task(self._drive(plan, session))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return plan.run

    def _plan(self, bot_id: str, mode: Mode, trigger: Literal["user", "auto"]) -> _Plan:
        learning = self._learning
        bot = learning.bot(bot_id)
        workspace_id = bot["workspace_id"]
        learning.admission(workspace_id)
        try:
            active = learning.workspaces.resolve_active().workspace.workspace_id
        except Exception:
            active = None
        if active != workspace_id:
            raise LearningError("not_active")
        if mode == "reflect":
            episodes = learning.episodes.episodes(bot_id, limit=REFLECT_EPISODES, reflected=False)
        else:
            last = learning.proposals.last_run(bot_id, "dream")
            episodes = learning.episodes.episodes(
                bot_id, limit=DREAM_EPISODES, after=None if last is None else last.started_at
            )
        if not episodes:
            raise LearningError("no_episodes")
        excerpts = tuple(read_excerpts(self._paths_for, list(reversed(episodes))))
        if not excerpts:
            raise LearningError("no_excerpts")
        try:
            items = learning.memories.list(workspace_id).items
        except MemoryServiceError as error:
            raise LearningError(error.code) from None
        records = tuple(views(items, bot_id, with_profile=mode == "dream"))
        identity = (bot.get("profile") or {}).get("identity") or {}
        bot_name = str(identity.get("name") or "Bot")
        prompt = build_prompt(
            mode=mode,
            bot_name=bot_name,
            purpose=str(identity.get("purpose") or ""),
            records=[(r.label, "lesson" if r.lesson else "fact", r.claim) for r in records],
            excerpts=excerpts,
            max_proposals=3 if mode == "reflect" else 40,
        )
        session_id = str(uuid.uuid4())
        state = self._host.app_state
        state.remember(
            session_id, model=self._host.native_model(), mode="default", effort=EFFORT[mode]
        )
        run = learning.proposals.start_run(
            workspace_id, bot_id, mode, trigger, session_id, stamp(learning.now())
        )
        return _Plan(
            run,
            mode,
            excerpts,
            prompt,
            records,
            {item.fields.id: item for item in items},
            bot_name,
        )

    async def _drive(self, plan: _Plan, session: _Session) -> None:
        turn = _Turn()
        unsubscribe = session.subscribe(turn)
        outcome: tuple[str, int, int] = ("internal", 0, 0)
        try:
            outcome = await self._outcome(plan, session, turn)
        except asyncio.CancelledError:
            outcome = ("app_closed", 0, 0)
            raise
        except Exception as error:
            log.warning("Bot learning run failed: %s", type(error).__name__)
        finally:
            unsubscribe()
            self._running = None
            with contextlib.suppress(Exception):
                await self._host.close(plan.run.session_id or "")
            reason, proposed, rejected = outcome
            learning = self._learning
            with contextlib.suppress(Exception):
                await run_sync(
                    lambda: learning.proposals.finish_run(
                        plan.run.run_id,
                        reason=reason,
                        episodes=len(plan.excerpts),
                        proposed=proposed,
                        rejected=rejected,
                        now=stamp(learning.now()),
                    )
                )

    async def _outcome(self, plan: _Plan, session: _Session, turn: _Turn) -> tuple[str, int, int]:
        """(reason, "" when done; proposals stored; ops refused)."""
        deadline = asyncio.get_running_loop().time() + self._deadline_s
        if not await session.send(plan.prompt):
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
            return turn.error or ("cancelled" if turn.status == "cancelled" else "error"), 0, 0
        parsed = parse_reply(turn.text, plan.mode)
        if parsed is None:
            return "no_reply", 0, 0
        ops, misshapen = parsed
        checked = check(ops, mode=plan.mode, excerpts=plan.excerpts, records=plan.records)
        learning = self._learning
        run = plan.run

        def store() -> int:
            proposals = list(checked.proposals)
            if plan.mode == "dream":
                proposals += dormant_retirements(
                    plan.records,
                    plan.items,
                    learning.proposals.stats(run.workspace_id, run.bot_id),
                    learning.now(),
                    open_targets=learning.proposals.open_targets(run.bot_id)
                    | {p.target_id for p in proposals if p.target_id},
                )
            stored = learning.proposals.add(run, proposals, stamp(learning.now()))
            learning.proposals.score(run.workspace_id, run.bot_id, checked.scores)
            if plan.mode == "reflect":
                learning.episodes.mark_reflected(e.episode.episode_id for e in plan.excerpts)
            else:
                learning.offer_skills(run.workspace_id, run.bot_id, plan.bot_name)
            return stored

        stored = await run_sync(store)
        rejected = misshapen + checked.rejected
        if checked.reason is not None:
            return checked.reason, 0, rejected
        return ("" if stored else "nothing_new"), stored, rejected


class AutoLearning:
    """Starts reflect and dream runs on its own, per the two settings; never raises."""

    def __init__(
        self,
        runner: LearningRunner,
        learning: BotLearning,
        settings: Callable[[], Mapping[str, object] | None],
        *,
        tick_s: float = TICK_S,
    ) -> None:
        self._runner = runner
        self._learning = learning
        self._settings = settings
        self._tick_s = tick_s
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.pass_once()
            except Exception as error:
                log.warning("Automatic Bot learning failed: %s", type(error).__name__)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._tick_s):
                    await self._stop.wait()

    async def pass_once(self) -> Run | None:
        """Start at most one run: the first Bot of the active vault that is due."""
        settings = await run_sync(self._settings)
        if not settings or self._runner.running is not None:
            return None
        reflect, dream = (
            bool(settings.get("bots.auto_reflect")),
            bool(settings.get("bots.auto_dream")),
        )
        if not reflect and not dream:
            return None
        due = await run_sync(lambda: self._due(reflect=reflect, dream=dream))
        if due is None:
            return None
        bot_id, mode = due
        try:
            return await self._runner.start(bot_id, mode, "auto")
        except LearningError:
            return None

    def _due(self, *, reflect: bool, dream: bool) -> tuple[str, Mode] | None:
        learning = self._learning
        now = learning.now()
        try:
            active = learning.workspaces.resolve_active().workspace.workspace_id
        except Exception:
            return None
        day = stamp(now - timedelta(days=1))
        for bot in learning.bots.list():
            if bot["workspace_id"] != active:
                continue
            bot_id = bot["bot_id"]
            last = learning.episodes.last_finished(bot_id)
            if last is None or now - parse_stamp(last) < QUIET:
                continue
            if dream and learning.proposals.runs_since(bot_id, "dream", day) < DAILY_DREAMS:
                previous = learning.proposals.last_run(bot_id, "dream")
                after = None if previous is None else previous.started_at
                if (
                    previous is None or now - parse_stamp(previous.started_at) >= DREAM_GAP
                ) and learning.episodes.count(bot_id, after=after) >= DREAM_MIN_EPISODES:
                    return bot_id, "dream"
            if (
                reflect
                and learning.proposals.runs_since(bot_id, "reflect", day) < DAILY_REFLECTS
                and learning.episodes.count(bot_id, after=None, reflected=False)
                >= REFLECT_MIN_EPISODES
            ):
                return bot_id, "reflect"
        return None
