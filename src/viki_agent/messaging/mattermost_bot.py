"""Independent remote conversations using the normal local approval runner."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import secrets
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from weakref import WeakKeyDictionary

from viki_agent.conversations.approval_runtime import ApprovalDispatchRejected, ApprovalRunner
from viki_agent.conversations.approvals import (
    ApprovalError,
    ChoiceInput,
    NativeRequest,
    PermissionChoice,
    QuestionChoice,
)
from viki_agent.messaging.mattermost_config import MattermostConfig
from viki_agent.messaging.mattermost_format import (
    _escape,
    chat_text,
    final_posts,
    literal_json,
)
from viki_agent.messaging.mattermost_http import (
    Binding,
    FixedJsonHttp,
    MattermostHttp,
    MattermostHttpError,
    ProgressHttp,
)
from viki_agent.messaging.mattermost_progress import MattermostProgress
from viki_agent.messaging.mattermost_transport import (
    _candidate,
    _failure_callback,
    posted,
    reaction_candidate,
)
from viki_agent.messaging.messaging_channel import (
    RemoteBinding,
    RemoteChannel,
    RemotePost,
    RemoteReaction,
    StopCause,
    StopReason,
)
from viki_agent.messaging.question_cards import pick_answers, pickable, question_post
from viki_agent.messaging.remote_status import _TURN_END, TurnStatus
from viki_agent.session import (
    CommandDispatchRejected,
    CommandDispatchResult,
    SessionActor,
    SessionManager,
    _finish_cleanup,
)

MAX_OUTPUT_MESSAGES = 32
MAX_OUTPUT_BYTES = 384 * 1024
MAX_INPUT_MESSAGES = 32
MAX_INPUT_BYTES = 384 * 1024
MAX_CARDS = 8
MAX_DESKTOP_CARDS = 8
MAX_POST_IDS = 4096
MAX_ACTORS = 16
_HELP = (
    "Send a message to start a dedicated VIKI Agent conversation. While Claude is working, a new "
    "message is added to the running turn.\n"
    "!viki help | !viki status | !viki new | !viki stop | !viki questions on | "
    "!viki questions off\n"
    "When a request from this conversation is the only request waiting, answer it in plain text: "
    "allow or deny for a permission, or option numbers or your own words for a question. On "
    "Mattermost, a thumbs-up or thumbs-down reaction on a permission request allows it once or "
    "denies it.\n"
    "Each request also shows its own commands: !viki allow or !viki deny for a permission, and "
    "!viki pick with option numbers or !viki answer with your own words for a question.\n"
    "!viki questions on also sends questions from your desktop chats here until this bot stops "
    "or you send !viki questions off; those stay answerable with their commands while they wait.\n"
    "Requests wait until you answer them or send !viki stop or !viki new. If this bot stops, it "
    "says why here when it can; turn it on again in Messaging. Workspace invalidation requires "
    "an app restart. Messages sent offline are not backfilled."
)
_GONE = (
    "That request is no longer waiting, so this message answered nothing and was not sent to "
    "Claude."
)
_BUSY = "Conversation is busy. Send your message again after the turn ends."
_SHOWING = (
    "A request is being shown here. Answer it once it has appeared; this message was not sent to "
    "Claude."
)
_NO_ALLOW = (
    "Allow once is not available here because this request was too long to show completely. "
    "Reply deny, or answer it in VIKI Agent desktop."
)
_REACT_HINT = (
    " On a permission request you can also react with a thumbs up to allow it once or a thumbs "
    "down to deny it."
)
_DESKTOP_TOO_LONG = (
    "A desktop chat is waiting on a question that is too long to show here. Open VIKI Agent "
    "desktop to answer it."
)
_READY = (
    "VIKI Agent is ready. Send a message to start, or !viki help for commands. Messages sent "
    "before the next whole server second are refused."
)
NoticeResult = Literal["sent", "unavailable", "failed", "unknown"]
STOP_NOTICE_DEADLINE_S = 3.0
_STOP_LINES: dict[StopCause, str] = {
    "limit_reached": (
        "VIKI Agent stopped this bot because it reached a safety limit, such as too many "
        "waiting requests or messages at once. Turn it on again in Messaging in VIKI Agent to "
        "continue."
    ),
    "conversation_failed": (
        "VIKI Agent stopped this bot because its Claude Code conversation reported an error or "
        "was closed. Check that conversation in VIKI Agent, then turn the bot on again in "
        "Messaging."
    ),
    "safety_check": (
        "VIKI Agent stopped this bot because a request changed after it was shown here, or a "
        "request or reply could not be shown completely. Check VIKI Agent, then turn the bot on "
        "again in Messaging."
    ),
    "internal_error": (
        "VIKI Agent stopped this bot after an unexpected error. Turn it on again in Messaging "
        "in VIKI Agent."
    ),
    "connection_lost": (
        "VIKI Agent stopped this bot because its connection to this chat service was lost. "
        "Turn it on again in Messaging in VIKI Agent."
    ),
    "turned_off": (
        "VIKI Agent stopped this bot because it was turned off or its settings changed in "
        "Messaging in VIKI Agent. Messages sent here are not answered until it is turned on "
        "again."
    ),
    "app_closing": (
        "VIKI Agent stopped this bot because the VIKI Agent window was closed or VIKI Agent "
        "shut down on the computer. Messages sent here are not answered until the bot runs "
        "again; open VIKI Agent and check Messaging."
    ),
    "restart_required": (
        "VIKI Agent stopped this bot because VIKI Agent needs a restart before this bot can "
        "run again."
    ),
}
_INTERNAL_STOPS = frozenset(
    {"limit_reached", "conversation_failed", "safety_check", "internal_error"}
)
_START_FAILURES: dict[str, StopReason] = {
    "mattermost_token_rejected": "token_rejected",
    "mattermost_unreachable": "server_unreachable",
    "mattermost_binding_failed": "account_check_failed",
}
# A concurrent factory cannot donate an idle actor to two remote generations.
_REMOTE_ACTORS: WeakKeyDictionary[SessionActor, str] = WeakKeyDictionary()


class RemoteCleanupError(RuntimeError):
    """A fixed failure that prevents replacing an incompletely retired conversation."""

    code = "remote_cleanup_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass
class _Notice:
    destination_key: str
    epoch: int
    current: Callable[[], Awaitable[bool]]
    receipt: asyncio.Future[NoticeResult]
    dispatched: bool = False


@dataclass
class _Card:
    actor: SessionActor
    generation: str
    tool_id: str
    native: dict[str, str]
    kind: str
    request: str
    complete: bool
    nonce: str
    deadline: float | None = None
    item: dict[str, Any] | None = None
    desktop: bool = False
    thread: str = ""
    # Every post shown for this card; a reaction on any of them may decide it.
    post_ids: set[str] = field(default_factory=set)


def _question_instructions(nonce: str, item: dict[str, Any] | None) -> str:
    """The lines under a question card's post, own or desktop: Pick only when every question
    has numbered options, then Other for the user's own words."""
    pick = (
        f"Pick: !viki pick {nonce} then one option number per question, in order. Join numbers "
        "with commas where several are allowed, like 1,3.\n"
    )
    return (pick if pickable(item) else "") + f"Other: !viki answer {nonce} then your own words."


def _label(choice: PermissionChoice | QuestionChoice) -> str:
    """What a choice did, for the start of its receipt."""
    if isinstance(choice, PermissionChoice):
        return "Allowed once." if choice.allow else "Denied."
    if not choice.answers:
        return "Answered in your own words."
    labels = " ".join("; ".join(choice.answers.values()).split())
    return f"Picked: {labels if len(labels) <= 200 else labels[:200] + '…'}."


@dataclass(frozen=True)
class _Output:
    message: str
    root: str
    card: _Card | None = None
    activates: bool = False
    ready: bool = False
    notice: _Notice | None = None
    epoch: int = 0
    final: bool = True


@dataclass(frozen=True)
class _Input:
    post: RemotePost | RemoteReaction
    size: int
    busy: bool
    epoch: int
    card: _Card | None
    waiting: int = 0
    showing: bool = False


class RemoteBot:
    _reactions = False

    def __init__(
        self,
        channel: RemoteChannel,
        manager: SessionManager,
        approvals: ApprovalRunner,
        *,
        create_actor: Callable[[], Awaitable[SessionActor]],
        clock: Callable[[], float] = time.monotonic,
        on_disarm: Callable[[], None] | None = None,
    ) -> None:
        self.channel, self.manager, self.approvals = channel, manager, approvals
        self._create_actor, self._clock = create_actor, clock
        self._on_disarm = on_disarm
        self._armed = True
        self._started = False
        self._ready_sent = False
        self._farewell_sent = False
        self._start_failure: StopReason | None = None
        self._stop: StopCause | None = None
        self._remote_binding: RemoteBinding | None = None
        self._nonce_scope = uuid.uuid4().hex
        self._cutoff = 0
        self._actor: SessionActor | None = None
        self._owned: dict[SessionActor, Callable[[], None]] = {}
        self._actor_ids: set[str] = set()
        self._actor_count = 0
        self._epoch = 0
        self._thread = ""
        self._turn_enabled = False
        self._cards_enabled = False
        self._control_active = False
        self._final = ""
        self._final_bytes = 0
        self._shortened = False
        self._cards: dict[str, _Card] = {}
        # Desktop chats' question cards, kept apart from the own cards in _cards, which
        # `!viki new|stop`, _event's pruning and MAX_CARDS act on.
        self._desktop: dict[str, _Card] = {}
        self._questions_on = False
        self._deciding_card: _Card | None = None
        self._seen: set[str] = set()
        self._pending_post_ids: set[str] = set()
        self._prompt_post_id: str | None = None
        self._input: asyncio.Queue[_Input] = asyncio.Queue()
        self._input_count = self._input_bytes = 0
        self._output: asyncio.Queue[_Output] = asyncio.Queue()
        self._output_count = self._output_bytes = 0
        self._active_notice: _Notice | None = None
        self._stopped = asyncio.Event()
        self._native_task: asyncio.Task[None] | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._write_task: asyncio.Task[None] | None = None
        self._input_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._status = TurnStatus()
        # Best-effort progress calls (Mattermost only); they carry no authority.
        self._progress: MattermostProgress | None = None
        self._turn_prompt: str | None = None

    @property
    def _binding(self) -> RemoteBinding | None:
        return self._remote_binding

    @_binding.setter
    def _binding(self, value: RemoteBinding | None) -> None:
        if value is not None and not isinstance(value, RemoteBinding):
            raise ValueError("remote_binding_invalid")
        self._remote_binding = value

    @property
    def state(self) -> Literal["connecting", "connected", "stopped"]:
        if not self._armed:
            return "stopped"
        return "connected" if self._ready_sent else "connecting"

    @property
    def stop_reason(self) -> StopReason:
        if self._stop in _INTERNAL_STOPS:
            return cast(StopReason, self._stop)
        return (
            "connection_lost"
            if self._ready_sent
            else (self._start_failure or self.channel.failure() or "start_failed")
        )

    def destination_key(self) -> str | None:
        """Identify the verified ready destination without exposing connection values."""
        binding = self._binding
        if not self._armed or not self._ready_sent or binding is None:
            return None
        return self.channel.destination_key(binding)

    async def send_notice(
        self,
        text: str,
        *,
        destination_key: str,
        current: Callable[[], Awaitable[bool]],
    ) -> NoticeResult:
        """Queue a guarded chat notice and await its actual delivery receipt."""
        if self.destination_key() is None or destination_key != self.destination_key():
            return "unavailable"
        try:
            message = chat_text(text)
        except ValueError:
            return "failed"
        notice = _Notice(
            destination_key, self._epoch, current, asyncio.get_running_loop().create_future()
        )
        if not self._enqueue(message, "", notice=notice):
            return "unavailable"
        try:
            return await asyncio.shield(notice.receipt)
        except asyncio.CancelledError:
            if notice.dispatched:
                self._settle_notice(notice, "unknown")
            else:
                notice.receipt.cancel()
            raise

    @staticmethod
    def _settle_notice(notice: _Notice | None, result: NoticeResult) -> None:
        if notice is not None and not notice.receipt.done():
            notice.receipt.set_result(result)

    def _current_notice(self, notice: _Notice) -> bool:
        return (
            not notice.receipt.done()
            and notice.epoch == self._epoch
            and notice.destination_key == self.destination_key()
        )

    async def run(self) -> None:
        if self._started:
            return
        self._started = True
        try:
            if not self._armed:
                return
            self._binding = await self.channel.bind()
            if self._armed:
                if self._progress is not None:
                    self._progress.start()
                self._write_task = asyncio.create_task(self._write())
                self._input_task = asyncio.create_task(self._admit())
                self._listen_task = asyncio.create_task(self._listen())
                await self._stopped.wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if isinstance(error, MattermostHttpError):
                self._start_failure = _START_FAILURES.get(error.code)
            self.disarm()
        finally:
            await self.close()

    def _invalidate_cards(self) -> None:
        self._cards = {}

    def disarm(self, reason: StopCause | None = None) -> None:
        """Revoke authority and deny existing cards before cleanup can await anything. Only the
        cards of this bridge's own conversations are denied; desktop chats' questions stay open
        on the desktop. The first reason given while armed is kept."""
        if self._armed:
            self._stop = reason
        self._armed = False
        self._epoch += 1
        self._turn_enabled = False
        self._cards_enabled = False
        self._invalidate_cards()
        self._questions_on = False
        self._desktop.clear()
        active = self._active_notice
        self._settle_notice(
            active, "unknown" if active is not None and active.dispatched else "unavailable"
        )
        while not self._output.empty():
            output = self._output.get_nowait()
            self._settle_notice(output.notice, "unavailable")
            self._output_count -= 1
            self._output_bytes -= len(output.message.encode("utf-8"))
        for actor in tuple(self._owned):
            with contextlib.suppress(Exception):
                actor.deny_pending()
        if self._native_task is not None:
            self._native_task.cancel()
        if self._write_task is not None:
            self._write_task.cancel()
        if self._progress is not None:
            self._progress.stop()
        self._stopped.set()
        on_disarm, self._on_disarm = self._on_disarm, None
        if on_disarm is not None:
            with contextlib.suppress(Exception):
                on_disarm()

    async def close(self) -> None:
        self.disarm()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._cleanup())
        await asyncio.shield(self._close_task)

    async def _farewell(self) -> None:
        """Post one line saying why a bot that had announced itself has stopped. It is best
        effort: a slow or failing send must not keep the bridge from closing."""
        if not self._ready_sent or self._farewell_sent:
            return
        self._farewell_sent = True
        with contextlib.suppress(Exception):
            line = _STOP_LINES[self._stop or "connection_lost"]
            async with asyncio.timeout(STOP_NOTICE_DEADLINE_S):
                await self.channel.send(self.channel.render(chat_text(line))[0], "")

    async def _cleanup(self) -> None:
        # The stop line is posted while the conversation is being stopped, never before it: a
        # slow chat service must not keep Claude's turn running.
        farewell = asyncio.create_task(self._farewell())
        try:
            # The reader stays until the line is out: cancelling it closes a Slack or Discord
            # channel under the line.
            tasks = [self._input_task, self._write_task, self._native_task]
            for task in tasks:
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in tasks if task is not None), return_exceptions=True
            )
            if self._progress is not None:
                # Closes the progress client's own HTTP connection, even when bind never ran.
                with contextlib.suppress(Exception):
                    await self._progress.close()
            failed = False
            try:
                for actor in tuple(self._owned):
                    try:
                        await self._close_actor(actor)
                    except Exception:
                        failed = True
            finally:
                self._actor = None
                while not self._output.empty():
                    self._output.get_nowait()
                self._output_count = self._output_bytes = 0
                while not self._input.empty():
                    self._input.get_nowait()
                self._input_count = self._input_bytes = 0
                self._pending_post_ids.clear()
                self._prompt_post_id = None
                await asyncio.gather(farewell, return_exceptions=True)
                if self._listen_task is not None:
                    self._listen_task.cancel()
                    await asyncio.gather(self._listen_task, return_exceptions=True)
                try:
                    await self.channel.close()
                except Exception:
                    failed = True
            if failed or self._owned:
                raise RemoteCleanupError
        finally:
            # Cancelled before the line was awaited: neither the line nor the reader outlives
            # the bridge.
            farewell.cancel()
            if self._listen_task is not None:
                self._listen_task.cancel()

    async def _close_actor(self, actor: SessionActor) -> None:
        actor.deny_pending()
        await _finish_cleanup(
            self.manager.close(
                actor.session_id,
                expected_generation=actor.generation,
                current=lambda: self.manager.get(actor.session_id) is actor,
            )
        )
        if self.manager.get(actor.session_id) is not actor and not actor.live and not actor.pids:
            unsubscribe = self._owned.pop(actor, None)
            if unsubscribe is not None:
                unsubscribe()

    def _enqueue(
        self,
        message: str,
        root: str,
        card: _Card | None = None,
        *,
        activates: bool = False,
        ready: bool = False,
        notice: _Notice | None = None,
    ) -> bool:
        if not self._armed:
            return False
        try:
            parts = self._render_parts((message,))
        except ValueError, UnicodeError:
            if notice is None:
                self.disarm("safety_check")
            return False
        return self._queue_parts(parts, root, card, activates=activates, ready=ready, notice=notice)

    def _render_parts(self, messages: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
        result: list[tuple[str, int]] = []
        total = 0
        for message in messages:
            parts = self.channel.render(message)
            if not parts or len(result) + len(parts) > MAX_OUTPUT_MESSAGES:
                raise ValueError("remote_render_invalid")
            for part in parts:
                size = len(part.encode("utf-8"))
                total += size
                if size > 12 * 1024 or total > MAX_OUTPUT_BYTES:
                    raise ValueError("remote_render_invalid")
                result.append((part, size))
        return tuple(result)

    def _fits(
        self,
        parts: tuple[tuple[str, int], ...],
        *,
        max_messages: int = MAX_OUTPUT_MESSAGES,
        max_bytes: int = MAX_OUTPUT_BYTES,
    ) -> bool:
        """The rendered parts fit beside the output already queued or in flight, within the
        given budget (the full output budget by default; `_relay` passes half of it, so a
        sweep of desktop questions cannot crowd out the bridge's own next card)."""
        return (
            self._output_count + len(parts) <= max_messages
            and self._output_bytes + sum(size for _, size in parts) <= max_bytes
        )

    def _queue_parts(
        self,
        parts: tuple[tuple[str, int], ...],
        root: str,
        card: _Card | None = None,
        *,
        activates: bool = False,
        ready: bool = False,
        notice: _Notice | None = None,
    ) -> bool:
        if not self._armed:
            return False
        if not self._fits(parts):
            if notice is None:
                self.disarm("limit_reached")
            return False
        self._output_count += len(parts)
        self._output_bytes += sum(size for _, size in parts)
        for index, (message, _) in enumerate(parts, 1):
            final = index == len(parts)
            self._output.put_nowait(
                _Output(
                    message,
                    root,
                    card,
                    activates and final,
                    ready and final,
                    notice,
                    self._epoch,
                    final,
                )
            )
        return True

    def _reply(self, text: str, root: str) -> None:
        self._enqueue(chat_text(text), root)

    def _current_card(self, card: _Card) -> bool:
        if card.desktop:
            return (
                self._armed
                and self._questions_on
                and self._desktop.get(card.nonce) is card
                and self.manager.get(card.actor.session_id) is card.actor
                and card.actor.generation == card.generation
                and card.actor.request_pending(card.tool_id, card.native)
            )
        return (
            self._armed
            and self._actor is card.actor
            and self.manager.get(card.actor.session_id) is card.actor
            and card.actor.generation == card.generation
            and self._cards.get(card.nonce) is card
            and card.actor.request_pending(card.tool_id, card.native)
        )

    def _lookup(self, nonce: str) -> _Card | None:
        """The card a nonce names: this conversation's own first, then a desktop chat's."""
        return self._cards.get(nonce) or self._desktop.get(nonce)

    def _stale(self, output: _Output) -> bool:
        """The output may not be sent: the bridge stopped, its conversation changed, or its card
        is no longer current. A desktop card's post belongs to no conversation, so `!viki new`
        and `!viki stop` leave it to its card."""
        card = output.card
        if card is not None and card.desktop:
            return not self._current_card(card)
        return (
            not self._armed
            or output.epoch != self._epoch
            or (card is not None and not self._current_card(card))
        )

    async def _write(self) -> None:
        try:
            while self._armed:
                output = await self._output.get()
                notice = self._active_notice = output.notice
                completed = False
                try:
                    if self._stale(output):
                        continue
                    if notice is not None:
                        if not self._current_notice(notice):
                            continue
                        try:
                            permitted = await notice.current()
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            self._settle_notice(notice, "failed")
                            continue
                        if permitted is not True or not self._current_notice(notice):
                            continue
                    if self._stale(output):
                        continue
                    assert self._binding is not None
                    if notice is not None:
                        notice.dispatched = True
                    sent = await self.channel.send(output.message, output.root)
                    if isinstance(sent, str) and output.card is not None:
                        output.card.post_ids.add(sent)
                    if self._stale(output):
                        continue
                    completed = True
                    if output.final:
                        self._settle_notice(notice, "sent")
                    if output.ready and self._armed:
                        self._ready_sent = True
                    card = output.card
                    if output.activates and card is not None and self._current_card(card):
                        # A displayed request waits for the user; nothing expires it.
                        card.deadline = math.inf
                finally:
                    if not completed or output.final:
                        self._settle_notice(
                            notice,
                            "unknown"
                            if notice is not None and notice.dispatched
                            else "unavailable",
                        )
                    self._active_notice = None
                    self._output_count -= 1
                    self._output_bytes -= len(output.message.encode("utf-8"))
                    if self._output_count == 0 and self._progress is not None:
                        # Progress waits for authority output; resume it once none is left.
                        self._progress.wake()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.disarm()

    async def _fresh_cutoff(self) -> int:
        cutoff = await self.channel.fresh_cutoff()
        if type(cutoff) is not int or cutoff <= 0 or cutoff % 1000:
            raise ValueError("mattermost_invalid_cutoff")
        if self._armed:
            self._cutoff = cutoff
            self._enqueue(chat_text(_READY), "", ready=True)
        return cutoff

    async def _listen(self) -> None:
        assert self._binding is not None
        try:
            async for event in self.channel.receive(
                fresh_cutoff=self._fresh_cutoff, on_failure=self.disarm
            ):
                if not self._armed:
                    break
                candidate = self.channel.validate(event, self._cutoff)
                reacted: _Card | None = None
                if isinstance(candidate, RemoteReaction):
                    # A reaction counts only on a post of an own permission card that waits.
                    reacted = next(
                        (c for c in self._cards.values() if candidate.post_id in c.post_ids), None
                    )
                    if (
                        reacted is None
                        or reacted.kind != "permission.requested"
                        or reacted.deadline is None
                        or not self._current_card(reacted)
                    ):
                        continue
                if (
                    candidate is None
                    or candidate.id in self._seen
                    or candidate.id in self._pending_post_ids
                ):
                    continue
                size = candidate.size_bytes
                if (
                    self._input_count >= MAX_INPUT_MESSAGES
                    or self._input_bytes + size > MAX_INPUT_BYTES
                ):
                    self.disarm("limit_reached")
                    break
                self._pending_post_ids.add(candidate.id)
                self._input_count += 1
                self._input_bytes += size
                if isinstance(candidate, RemoteReaction):
                    self._input.put_nowait(_Input(candidate, size, False, self._epoch, reacted))
                    continue
                busy = self._busy() or self._prompt_post_id is not None
                ordinary = self._ordinary(candidate.message)
                # Plain text answers the one own displayed request waiting when it arrives.
                shown = [
                    card
                    for card in self._cards.values()
                    if card.deadline is not None and self._current_card(card)
                ]
                waiting = len(shown) if ordinary else 0
                if waiting:
                    # Any other request still current beside the shown own one, on screen or still
                    # being posted (an own card, or a relayed desktop question), makes plain text
                    # ambiguous: the owner may be reading that one, so it then answers nothing.
                    # A card whose request is gone (say, answered on the desktop before its post
                    # finished) counts no more.
                    waiting += sum(
                        1
                        for card in self._cards.values()
                        if card.deadline is None and self._current_card(card)
                    )
                    waiting += sum(1 for card in self._desktop.values() if self._current_card(card))
                # A card still being posted is not waiting yet, but text meant for it must not
                # be steered into the turn either.
                showing = ordinary and bool(self._cards)
                if ordinary and not busy and not waiting:
                    self._prompt_post_id = candidate.id
                words = candidate.message.split(maxsplit=3)
                card = self._lookup(words[2]) if len(words) >= 3 and words[0] == "!viki" else None
                if waiting == 1:
                    card = shown[0]
                if card is not None and (card.deadline is None or not self._current_card(card)):
                    card = None
                self._input.put_nowait(
                    _Input(candidate, size, busy, self._epoch, card, waiting, showing)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.disarm()
        finally:
            self.disarm()

    async def _admit(self) -> None:
        try:
            while self._armed:
                item = await self._input.get()
                candidate = item.post
                try:
                    if isinstance(candidate, RemoteReaction):
                        confirmed = await self._confirm_reaction(candidate)
                    else:
                        confirmed = await self.channel.confirm(candidate) == candidate
                    if not self._armed:
                        return
                    if not confirmed:
                        continue
                    if len(self._seen) >= MAX_POST_IDS:
                        self.disarm("limit_reached")
                        return
                    self._seen.add(candidate.id)
                    if isinstance(candidate, RemoteReaction):
                        assert item.card is not None
                        await self._react(candidate, item.card)
                    elif (
                        self._ordinary(candidate.message)
                        and not item.waiting
                        and item.epoch != self._epoch
                    ):
                        self._reply(
                            "Conversation changed. Send your message again.",
                            candidate.root_id or candidate.id,
                        )
                    else:
                        await self._intake(
                            {
                                "id": candidate.id,
                                "message": candidate.message,
                                "root_id": candidate.root_id,
                            },
                            busy=item.busy,
                            received_card=item.card,
                            waiting=item.waiting,
                            showing=item.showing,
                        )
                finally:
                    self._input_count -= 1
                    self._input_bytes -= item.size
                    self._pending_post_ids.discard(candidate.id)
                    if self._prompt_post_id == candidate.id:
                        self._prompt_post_id = None
        except asyncio.CancelledError:
            raise
        except Exception:
            self.disarm("internal_error")

    async def _confirm_reaction(self, reaction: RemoteReaction) -> bool:
        """Only a channel whose provider can read a post's reactions back confirms one."""
        return False

    @staticmethod
    def _ordinary(text: str) -> bool:
        return bool(text.strip()) and not text.lstrip().startswith(("/", "!"))

    def _busy(self) -> bool:
        return (
            self._control_active
            or (self._native_task is not None and not self._native_task.done())
            or (self._actor is not None and self._actor.busy)
        )

    async def _intake(
        self,
        post: dict[str, Any],
        *,
        busy: bool = False,
        received_card: _Card | None = None,
        waiting: int = 0,
        showing: bool = False,
    ) -> None:
        text, root = post["message"], post.get("root_id") or post["id"]
        words = text.split(maxsplit=3)
        if words and words[0] == "!viki":
            command = words[1] if len(words) > 1 else ""
            if command in {"allow", "deny", "answer", "pick"}:
                await self._choose(words, root, received_card)
            elif command == "questions" and len(words) == 3 and words[2] in {"on", "off"}:
                self._desktop_questions(words[2] == "on", root)
            elif len(words) != 2 or command not in {"help", "status", "new", "stop"}:
                self._reply("Command refused. Use !viki help.", root)
            elif command == "help":
                self._reply(_HELP, root)
            elif command == "status":
                busy = self._busy() or self._prompt_post_id is not None
                # Only a turn being posted here shows its line; one Claude Code started by itself
                # after `!viki stop` or `!viki new` is not posted.
                if self._status.running and self._turn_enabled:
                    self._reply(self._status.line(self._clock(), waiting=self._card_waits()), root)
                else:
                    self._reply(
                        "VIKI Agent conversation is busy." if busy else "VIKI Agent is ready.", root
                    )
            else:
                self._control_active = True
                try:
                    await self._control(command, root)
                finally:
                    self._control_active = False
        elif not text.strip() or text.lstrip().startswith(("/", "!")):
            self._reply("Message refused. Use ordinary text or a command from !viki help.", root)
        elif waiting == 1:
            await self._answer(text, root, received_card)
        elif waiting > 1:
            self._reply(
                "Several requests are waiting, so this message answered none of them and was not "
                "sent to Claude. Use the command shown under the request you mean."
                + (_REACT_HINT if self._reactions else ""),
                root,
            )
        elif busy or self._busy():
            if showing:
                self._reply(_SHOWING, root)
            elif self._steerable():
                await self._steer(text, root, post["id"])
            else:
                self._reply(_BUSY, root)
        else:
            self._thread = root
            self._turn_enabled = True
            self._cards_enabled = True
            self._final, self._final_bytes, self._shortened = "", 0, False
            self._turn_prompt = post["id"]
            if self._progress is not None:
                # 👀 is queued now: the first turn waits for the actor and `claude` to start.
                self._progress.accepted(post["id"])
            self._native_task = asyncio.create_task(self._send(text, self._epoch))

    def _card_waits(self) -> bool:
        """An own displayed request waits for its owner; desktop questions do not count."""
        return any(
            card.deadline is not None and self._current_card(card) for card in self._cards.values()
        )

    def _steerable(self) -> bool:
        # After `!viki stop` or `!viki new` a steer would run as a turn nobody sees, since the
        # CLI runs a waiting steer as its next turn even after a stop.
        return (
            self._cards_enabled
            and self._actor is not None
            and self._actor.busy
            and not self._control_active
            and (self._native_task is None or self._native_task.done())
        )

    async def _steer(self, text: str, root: str, post_id: str) -> None:
        """Write a message into the running turn. It is awaited inline, never as _native_task,
        so a control command cannot cancel the write and intake stays serial. No retry."""
        actor, epoch = self._actor, self._epoch
        assert actor is not None

        def running() -> None:
            # A turn that ended while send_command waited (on the actor lock, or on the folder
            # context read after accept) would make this a new turn the owner did not ask for.
            # dispatch_intent is send_command's last await before it picks steer or new turn.
            if not actor.busy:
                raise CommandDispatchRejected("turn_running")

        async def accept() -> str:
            running()
            return text

        async def dispatch_intent() -> None:
            running()

        try:
            result = await actor.send_command(
                expected_generation=actor.generation,
                accept=accept,
                dispatch_intent=dispatch_intent,
            )
        except CommandDispatchRejected:
            self._reply(_BUSY, root)
            return
        if not self._armed or epoch != self._epoch:
            return
        if result == CommandDispatchResult("unknown", "native_unconfirmed"):
            self._steered(post_id, root)
        elif result.status == "not_dispatched":
            self._reply(
                "Your message was not added to the running turn. Send it again after the reply.",
                root,
            )
        # Any other result follows an error event from the actor, which stops this bridge.

    def _steered(self, post_id: str, root: str) -> None:
        """The one place a steer the actor wrote is acknowledged."""
        self._reply("Added to the running turn.", root)

    async def _send(self, text: str, epoch: int) -> None:
        made: SessionActor | None = None
        adopted = False
        try:
            actor = self._actor
            if actor is None:
                if self._actor_count >= MAX_ACTORS:
                    self.disarm("limit_reached")
                    return
                self._actor_count += 1
                actor = await self._create_actor()
                canonical = str(uuid.UUID(actor.session_id)) == actor.session_id
                if (
                    not canonical
                    or actor.live
                    or actor.command_context()["closed"]
                    or actor.session_id in self._actor_ids
                    or self.manager.get(actor.session_id) is not actor
                    or actor in _REMOTE_ACTORS
                ):
                    self.disarm("internal_error")
                    return
                made = actor
                _REMOTE_ACTORS[actor] = self._nonce_scope
                self._owned[actor] = actor.subscribe(lambda event: self._event(actor, event))
                self._actor_ids.add(actor.session_id)
                if not self._armed or epoch != self._epoch:
                    actor.deny_pending()
                    return
                self._actor = actor
                adopted = True
            result = await actor.send(
                text,
                expected_generation=actor.generation,
                current=lambda: (
                    self._armed
                    and epoch == self._epoch
                    and self._actor is actor
                    and self.manager.get(actor.session_id) is actor
                ),
            )
            if result is not True:
                self.disarm("conversation_failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.disarm("internal_error")
        finally:
            if made is not None and not adopted:
                await self._close_actor(made)
                if self._armed and epoch == self._epoch:
                    self.disarm("internal_error")

    async def _control(self, command: str, root: str) -> None:
        self._epoch += 1
        self._turn_enabled = False
        self._cards_enabled = False
        self._invalidate_cards()
        now = self._clock()
        self._status.end("stopped", now)
        if self._progress is not None:
            # A prompt whose turn has not started yet still gets its ⏹.
            self._progress.turn_ended(
                "stopped", chat_text(self._status.line(now)), prompt=self._turn_prompt
            )
        self._turn_prompt = None
        actor = self._actor
        if actor is not None:
            actor.deny_pending()
        if self._native_task is not None:
            self._native_task.cancel()
            await asyncio.gather(self._native_task, return_exceptions=True)
        if not self._armed:
            return
        if actor is not None:
            if command == "new":
                self._actor = None
                await self._close_actor(actor)
            else:
                await actor.interrupt(
                    expected_generation=actor.generation,
                    current=lambda: self._armed and self._actor is actor,
                )
        self._reply(
            "Send a message to start a new conversation."
            if command == "new"
            else "Stop requested for this conversation.",
            root,
        )

    def _desktop_questions(self, on: bool, root: str) -> None:
        """`!viki questions on|off`: relay desktop chats' questions to this connection or stop.
        Turning it on also posts the questions already waiting; new ones arrive via observe()."""
        if not on:
            self._questions_on = False
            self._desktop.clear()
            self._reply("Desktop chat questions are off.", root)
        elif self._questions_on:
            self._reply("Desktop chat questions are already on.", root)
        else:
            self._questions_on = True
            self._reply(
                "Desktop chat questions are on until this bot stops or you send "
                "!viki questions off.",
                root,
            )
            for actor in self.manager.live():
                if actor in _REMOTE_ACTORS:
                    continue
                for item in actor.pending_cards():
                    if item["type"] == "question.asked":
                        self._relay(actor, item["data"]["tool_use_id"], item["native_request"])

    def _request(self, actor: SessionActor, tool_id: str, native: dict[str, str]) -> str | None:
        item = actor.displayed_request(tool_id, native)
        details = actor.remote_permission(tool_id, native)
        if item is None or details is None:
            return None
        if (
            len(json.dumps(details, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
            > 8192
        ):
            return None
        # The full native input supplies the plan/questions as well as hidden tool arguments.
        return literal_json(details)

    def _nonce(self, actor: SessionActor, tool_id: str, native: dict[str, str]) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    self._nonce_scope,
                    actor.generation,
                    tool_id,
                    native,
                    secrets.token_hex(16),
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:32]

    def _event(self, actor: SessionActor, event: dict[str, Any]) -> None:
        try:
            kind, data = event["type"], event["data"]
            if kind in {"permission.requested", "question.asked"} and (
                not self._armed or actor is not self._actor or not self._cards_enabled
            ):
                actor.deny_pending()
                return
            if (
                not self._armed
                or actor is not self._actor
                or event.get("session_id") != actor.session_id
                or event.get("generation") != actor.generation
            ):
                return
            now = self._clock()
            self._status.observe(kind, data, now)
            for nonce, card in tuple(self._cards.items()):
                if not actor.request_pending(card.tool_id, card.native):
                    self._cards.pop(nonce)
                    if self._progress is not None:
                        self._progress.wake()  # Typing resumes once no own card waits.
            if kind in {"permission.requested", "question.asked"}:
                deciding = (
                    self._deciding_card is not None
                    and self._deciding_card.actor.request_pending(
                        self._deciding_card.tool_id, self._deciding_card.native
                    )
                )
                if len(self._cards) + int(deciding) >= MAX_CARDS:
                    self.disarm("limit_reached")
                    return
                native, tool_id = dict(event["native_request"]), data["tool_use_id"]
                detail_parts: tuple[tuple[str, int], ...] = ()
                item: dict[str, Any] | None = None
                try:
                    # The native JSON stays the completeness gate and the revalidation value; a
                    # question shows its readable projected card in place of that JSON.
                    request = self._request(actor, tool_id, native)
                    if request is not None and kind == "question.asked":
                        item = actor.displayed_request(tool_id, native)
                        detail_parts = self._render_parts((question_post(item, desktop=False),))
                    elif request is not None:
                        detail_parts = self._render_parts((request,))
                except ValueError:
                    request = None
                if request is None and kind == "question.asked":
                    self.disarm("safety_check")
                    return
                nonce = self._nonce(actor, tool_id, native)
                card = _Card(
                    actor,
                    actor.generation,
                    tool_id,
                    native,
                    kind,
                    request or "",
                    bool(request),
                    nonce,
                    item=item,
                    thread=self._thread,
                )
                self._cards[nonce] = card
                instructions = (
                    _question_instructions(nonce, item)
                    if kind == "question.asked"
                    else (
                        f"Allow once: !viki allow {nonce}\n"
                        if request is not None
                        else "Complete request unavailable. Use VIKI Agent desktop for approval.\n"
                    )
                    + f"Deny: !viki deny {nonce}"
                )
                instruction_parts = self._render_parts((chat_text(instructions),))
                self._queue_parts(
                    detail_parts + instruction_parts, self._thread, card, activates=True
                )
            elif kind == "turn.started" and self._cards_enabled:
                # Every own turn is projected while the conversation is on, including one a
                # steer started after the turn it was written into had ended.
                self._turn_enabled = True
                self._final, self._final_bytes, self._shortened = "", 0, False
                if self._progress is not None:
                    self._progress.turn_started(self._thread, self._turn_prompt)
                self._turn_prompt = None
            elif kind == "steer.ended" and data["outcome"] == "dropped":
                self._reply(
                    "A message you sent during the turn was not added to it. Send it again.",
                    self._thread,
                )
            elif kind == "text.done" and self._turn_enabled and not self._shortened:
                kept: list[str] = []
                text = ("\n\n" if self._final and data["text"] else "") + data["text"]
                for char in text:
                    size = len(_escape(char).encode("utf-8"))
                    if self._final_bytes + size > 64 * 1024:
                        self._shortened = True
                        break
                    kept.append(char)
                    self._final_bytes += size
                self._final += "".join(kept)
            elif kind == "turn.done" and self._turn_enabled:
                self._turn_enabled = False
                posts: list[str] = []
                if self._final.strip() or self._shortened:
                    text = self._final + (
                        "\nReply shortened. Open VIKI Agent desktop." if self._shortened else ""
                    )
                    posts = [message for message in final_posts(text) if message.strip()]
                if posts:
                    for message in posts:
                        self._enqueue(message, self._thread)
                else:
                    self._reply("Turn ended. Open VIKI Agent desktop for details.", self._thread)
                if self._progress is not None:
                    # Queued after the final posts; the scheduler waits until they are sent.
                    self._progress.turn_ended(
                        _TURN_END.get(str(data.get("status")), "error"),
                        chat_text(self._status.line(now)),
                    )
            elif kind in {"error", "session.closed", "session.blocked"}:
                self.disarm("conversation_failed")
        except Exception:
            self.disarm("internal_error")

    def observe(self, actor: SessionActor, envelope: dict[str, Any]) -> None:
        """Relay a desktop chat's new question once `!viki questions on` was sent here.

        Every chat's event delivery calls this synchronously (SessionManager.bind_observer), so
        it filters on the event type first and never raises: any failure disarms this bridge,
        which denies only its own conversations' cards. Bridge conversations are left to their
        bridge."""
        try:
            if (
                envelope.get("type") != "question.asked"
                or not self._questions_on
                or not self._armed
                or actor in _REMOTE_ACTORS
                or envelope.get("session_id") != actor.session_id
                or envelope.get("generation") != actor.generation
            ):
                return
            native = envelope.get("native_request")
            if isinstance(native, dict):
                self._relay(actor, envelope["data"]["tool_use_id"], dict(native))
        except Exception:
            self.disarm("internal_error")

    def _relay(self, actor: SessionActor, tool_id: str, native: dict[str, str]) -> None:
        """Post one waiting desktop question top-level with a fresh nonce, or a fixed notice
        without one when it is too long to show. A duplicate, a full set of held cards or full
        output skips it; none of them disarms."""
        for nonce, card in tuple(self._desktop.items()):
            if not self._current_card(card):
                del self._desktop[nonce]
        if len(self._desktop) >= MAX_DESKTOP_CARDS or any(
            card.actor is actor and card.tool_id == tool_id and card.native == native
            for card in self._desktop.values()
        ):
            return
        item = actor.displayed_request(tool_id, native)
        if item is None or item["type"] != "question.asked":
            return
        nonce = self._nonce(actor, tool_id, native)
        parts: tuple[tuple[str, int], ...] = ()
        try:
            # As on own cards, the native JSON is the completeness gate and revalidation value.
            request = self._request(actor, tool_id, native)
            if request is not None:
                parts = self._render_parts(
                    (
                        question_post(item, desktop=True),
                        chat_text(_question_instructions(nonce, item)),
                    )
                )
        except ValueError:
            request = None
        card = (
            _Card(
                actor,
                actor.generation,
                tool_id,
                native,
                "question.asked",
                request,
                True,
                nonce,
                item=item,
                desktop=True,
            )
            if request is not None
            else None
        )
        if card is None:
            parts = self._render_parts((chat_text(_DESKTOP_TOO_LONG),))
        # Desktop posts share the output budget with the bridge's own conversation; queue only
        # within half of it so a sweep of waiting questions cannot crowd out the next own card.
        if not self._fits(
            parts, max_messages=MAX_OUTPUT_MESSAGES // 2, max_bytes=MAX_OUTPUT_BYTES // 2
        ):
            return
        if self._queue_parts(parts, "", card, activates=card is not None) and card is not None:
            self._desktop[nonce] = card

    async def _choose(self, words: list[str], root: str, received_card: _Card | None) -> None:
        command = words[1]
        nonce = words[2] if len(words) >= 3 else ""
        card = self._lookup(nonce)
        question = card is not None and card.kind == "question.asked"
        answers = (
            pick_answers(card.item, words[3])
            if card is not None and command == "pick" and len(words) == 4
            else None
        )
        if (
            card is None
            or card is not received_card
            or card.deadline is None
            or not self._current_card(card)
            or (command in {"answer", "pick"}) != question
            or len(words) != (4 if question else 3)
            or (command == "allow" and not card.complete)
            or (command == "pick" and answers is None)
        ):
            self._reply(
                "Choice refused. Use a current displayed request and its matching command.", root
            )
            return
        choice = (
            QuestionChoice(answers, "")
            if answers is not None
            else QuestionChoice({}, words[3])
            if question
            else PermissionChoice(command == "allow")
        )
        await self._decide(card, choice, root, _label(choice))

    async def _answer(self, text: str, root: str, card: _Card | None) -> None:
        """Answer the one own request that was waiting when this plain message arrived. A hint
        or refusal sends nothing to Claude and records no choice."""
        if card is None or card.desktop or card.deadline is None or not self._current_card(card):
            self._reply(_GONE, root)
            return
        answer = text.strip()
        choice: PermissionChoice | QuestionChoice
        if card.kind == "question.asked":
            answers = pick_answers(card.item, answer)
            choice = QuestionChoice({}, answer) if answers is None else QuestionChoice(answers, "")
        elif answer.lower() not in {"allow", "deny"}:
            self._reply(
                "A permission request is waiting. Reply allow or deny to answer it; this message "
                "was not sent to Claude." + (_REACT_HINT if self._reactions else ""),
                root,
            )
            return
        elif answer.lower() == "allow" and not card.complete:
            self._reply(_NO_ALLOW, root)
            return
        else:
            choice = PermissionChoice(answer.lower() == "allow")
        await self._decide(card, choice, root, _label(choice))

    async def _react(self, reaction: RemoteReaction, card: _Card) -> None:
        """Decide the own permission card a confirmed reaction was bound to on arrival."""
        if card.deadline is None or not self._current_card(card):
            self._reply("That request is no longer waiting.", card.thread)
        elif reaction.choice == "allow" and not card.complete:
            self._reply(_NO_ALLOW, card.thread)
        else:
            choice = PermissionChoice(reaction.choice == "allow")
            await self._decide(card, choice, card.thread, _label(choice))

    async def _decide(
        self, card: _Card, choice: PermissionChoice | QuestionChoice, root: str, what: str
    ) -> None:
        """Consume a current card and make its one runner delivery; `what` opens the receipt."""
        # Consumption and full native/content revalidation precede the runner's first await.
        (self._desktop if card.desktop else self._cards).pop(card.nonce)
        try:
            current = (
                self._request(card.actor, card.tool_id, card.native) if card.complete else None
            )
            if card.desktop and current is None:
                # A closing chat, a gone driver, or a request that changed past the
                # completeness gate leaves nothing to revalidate against: the desk keeps
                # the card instead of this pick being decided on no current details.
                self._reply("Choice not recorded. Check VIKI Agent desktop.", root)
                return
            if (
                not self._armed
                or (not self._questions_on if card.desktop else self._actor is not card.actor)
                or self.manager.get(card.actor.session_id) is not card.actor
                or card.actor.generation != card.generation
                or not card.actor.request_pending(card.tool_id, card.native)
                or (card.complete and current != card.request)
                or (
                    card.item is not None
                    and card.actor.displayed_request(card.tool_id, card.native) != card.item
                )
            ):
                self.disarm("safety_check")
                return
            value = ChoiceInput(
                str(uuid.uuid4()),
                card.actor.session_id,
                card.generation,
                card.tool_id,
                NativeRequest(**card.native),
                choice,
            )
            if not card.desktop:
                self._deciding_card = card
            try:
                summary, _ = await self.approvals.decide(value)
            except ApprovalError, ApprovalDispatchRejected:
                if not card.desktop:
                    raise
                # The desk may have answered first: stay armed and leave the card to the desktop.
                self._reply("Choice not recorded. Check VIKI Agent desktop.", root)
                return
            receipt = f"{what} Choice receipt: {summary.state}."
            if summary.state != "delivered_locally":
                receipt += " Check VIKI Agent desktop."
            self._reply(receipt, root)
        except Exception:
            self.disarm("internal_error")
        finally:
            self._deciding_card = None


def _mattermost_candidate(post: object, binding: Binding, cutoff: int) -> dict[str, Any] | None:
    if cutoff <= 0 or not isinstance(post, dict):
        return None
    return _candidate(
        {
            "event": "posted",
            "broadcast": {"channel_id": binding.channel_id},
            "data": {"post": json.dumps(post, ensure_ascii=True, allow_nan=False)},
        },
        binding,
        cutoff,
    )


class _MattermostChannel:
    def __init__(
        self, config: Callable[[], MattermostConfig], http: Callable[[], MattermostHttp]
    ) -> None:
        self._config, self._http = config, http
        self._binding: Binding | None = None
        self._remote: RemoteBinding | None = None
        self._cutoff = 0

    async def bind(self) -> RemoteBinding:
        binding = self._binding = await self._http().bind()
        self._remote = RemoteBinding(
            "mattermost",
            (self._config().url,),
            binding.bot_user_id,
            binding.owner_user_id,
            binding.channel_id,
        )
        return self._remote

    async def fresh_cutoff(self) -> int:
        return await self._http().fresh_cutoff()

    def validate(self, candidate: object, cutoff: int) -> RemotePost | RemoteReaction | None:
        binding, remote = self._binding, self._remote
        if binding is None or remote is None:
            return None
        if type(candidate) is dict and candidate.get("_reaction") is True:
            self._cutoff = cutoff
            # `posted` checked the owner, channel and deletion and dropped those fields; any of
            # them still present is checked again.
            owner = {"user_id": binding.owner_user_id, "channel_id": binding.channel_id}
            return self.reaction({**owner, "delete_at": 0, **candidate})
        post = _mattermost_candidate(candidate, binding, cutoff)
        if post is None:
            return None
        self._cutoff = cutoff
        fields = (
            "id",
            "user_id",
            "channel_id",
            "message",
            "create_at",
            "edit_at",
            "delete_at",
            "type",
            "original_id",
            "root_id",
            "props",
            "file_ids",
            "metadata",
        )
        admitted = {key: post.get(key) for key in fields}
        encoded = json.dumps(
            admitted, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        identity = json.dumps(
            [
                remote.provider,
                remote.scope,
                remote.bot_user_id,
                remote.owner_user_id,
                remote.channel_id,
                admitted,
            ],
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        # Charge all admitted source fields, including props, to preserve Mattermost's
        # conservative input cap while retaining only the normalized post and fingerprint.
        return RemotePost(
            post["id"],
            post["message"],
            post["root_id"],
            post["create_at"],
            hashlib.sha256(identity).hexdigest(),
            len(encoded),
        )

    def reaction(self, item: object) -> RemoteReaction | None:
        """A reaction admitted under the WebSocket rule: the owner, the bound channel, a thumbs
        emoji, created at or after the cutoff and not deleted."""
        binding = self._binding
        found = None if binding is None else reaction_candidate(item, binding, self._cutoff)
        if found is None:
            return None
        encoded = json.dumps(
            found, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return RemoteReaction(
            found["post_id"], found["choice"], found["emoji_name"], found["create_at"], len(encoded)
        )

    async def confirm(self, candidate: RemotePost) -> RemotePost | None:
        found = self.validate(await self._http().post(candidate.id), self._cutoff)
        return found if isinstance(found, RemotePost) else None

    async def send(self, part: str, reply_to: str) -> str:
        if self._binding is None:
            raise ValueError("mattermost_unbound")
        return await self._http().send(self._binding, part, reply_to)

    async def receive(
        self,
        *,
        fresh_cutoff: Callable[[], Awaitable[int]],
        on_failure: Callable[[], None] | None = None,
    ) -> AsyncIterator[object]:
        notify = _failure_callback(on_failure)
        http = self._http()
        if isinstance(http, FixedJsonHttp):
            http.set_failure_handler(notify)
        if self._binding is None:
            notify()
            raise ValueError("mattermost_unbound")
        stream = cast(
            AsyncGenerator[dict[str, Any]],
            posted(
                self._config(),
                self._binding,
                fresh_cutoff=fresh_cutoff,
                ssl_context=http.ssl_context,
                on_failure=notify,
            ),
        )
        try:
            async with contextlib.aclosing(stream):
                try:
                    async for post in stream:
                        yield post
                finally:
                    notify()
        finally:
            notify()

    def render(self, formatted: str) -> tuple[str, ...]:
        return (formatted,)

    def destination_key(self, binding: RemoteBinding) -> str:
        encoded = json.dumps(
            [self._config().url, binding.bot_user_id, binding.owner_user_id, binding.channel_id],
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def failure(self) -> StopReason | None:
        return None  # MattermostBot names its own bind-failure codes via _start_failure.

    async def close(self) -> None:
        await self._http().close()


class MattermostBot(RemoteBot):
    _reactions = True

    def __init__(
        self,
        config: MattermostConfig,
        http: MattermostHttp,
        manager: SessionManager,
        approvals: ApprovalRunner,
        *,
        create_actor: Callable[[], Awaitable[SessionActor]],
        clock: Callable[[], float] = time.monotonic,
        on_disarm: Callable[[], None] | None = None,
        progress_http: ProgressHttp | None = None,
    ) -> None:
        self.config, self.http = config, http
        if progress_http is None and isinstance(http, MattermostHttp):
            progress_http = http.progress_client()
        self._progress_http = progress_http
        self._mattermost = _MattermostChannel(lambda: self.config, lambda: self.http)
        super().__init__(
            self._mattermost,
            manager,
            approvals,
            create_actor=create_actor,
            clock=clock,
            on_disarm=on_disarm,
        )
        if progress_http is not None:
            self._progress = MattermostProgress(
                progress_http,
                line=lambda: chat_text(self._status.line(clock(), waiting=self._card_waits())),
                waiting=self._card_waits,
                idle=lambda: self._armed and self._output_count == 0,
                clock=clock,
            )

    @RemoteBot._binding.setter
    def _binding(self, value: Binding | RemoteBinding | None) -> None:
        if isinstance(value, Binding):
            value = RemoteBinding(
                "mattermost",
                (self.config.url,),
                value.bot_user_id,
                value.owner_user_id,
                value.channel_id,
            )
        if value is not None and (
            value.provider != "mattermost" or value.scope != (self.config.url,)
        ):
            raise ValueError("remote_binding_invalid")
        if value is not None and self._progress_http is not None:
            self._progress_http.bind(value.bot_user_id, value.channel_id)
        self._remote_binding = value

    async def _confirm_reaction(self, reaction: RemoteReaction) -> bool:
        """The REST list of the reacted post holds this owner reaction. Another HTTP failure
        reaches the client's failure handler, as a post confirmation does."""
        try:
            items = await self.http.reactions(reaction.post_id)
        except MattermostHttpError as error:
            if error.code != "mattermost_post_invalid":
                raise
            return False
        # Mattermost keeps a re-added reaction's first create_at in REST while the frame carries
        # the new one, so create_at is checked only against the cutoff.
        want = (reaction.post_id, reaction.emoji_name, reaction.choice)
        return any(
            (found.post_id, found.emoji_name, found.choice) == want
            for found in map(self._mattermost.reaction, items)
            if found is not None
        )

    def _validated(self, post: dict[str, Any]) -> dict[str, Any] | None:
        binding = self._binding
        if binding is None:
            return None
        return _mattermost_candidate(
            post,
            Binding(binding.bot_user_id, binding.owner_user_id, binding.channel_id),
            self._cutoff,
        )
