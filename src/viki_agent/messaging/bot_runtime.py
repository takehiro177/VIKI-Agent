"""Launch-only Bot consent and bounded transient routine results; no native authority."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from viki_agent.automation.tasks import Attempt, Outcome
from viki_agent.conversations.command_runtime import storage_call
from viki_agent.decision_loop.rules import Verdict, decide
from viki_agent.decision_loop.store import DecisionLoopStore
from viki_agent.messaging.bots import MAX_BOTS, BotError, BotService
from viki_agent.messaging.mattermost_bot import MattermostBot
from viki_agent.messaging.mattermost_format import chat_text
from viki_agent.profiles.service import ProfileError
from viki_agent.session import SessionActor
from viki_agent.workspaces import WorkspaceError

MAX_CONTEXTS = 42
MAX_NOTICE_BYTES = 8 * 1024
MAX_BLOCK_IDS = 64
MAX_BLOCK_EVENTS = 256
_SHORTENED = "\n\nReply shortened. Open VIKI Agent desktop for the complete reply."


@dataclass(frozen=True)
class _Consent:
    generation: int
    revision: int
    destination: str


@dataclass
class _Context:
    attempt: Attempt
    profile_id: str
    workspace_id: str
    bot_id: str | None = None
    revision: int = 0
    consent: _Consent | None = None
    name: str = "VIKI Agent"
    generation: str | None = None
    sequence: int = 0
    turn: str | None = None
    collecting: bool = False
    prepared: bool = False
    terminal: bool = False
    shortened: bool = False
    text: str = ""
    text_bytes: int = 0
    budget: int = 0
    block_ids: set[str] = field(default_factory=set)
    block_events: int = 0
    outcome: Outcome = "unknown"

    def clear_collection(self) -> None:
        self.collecting = False
        self.generation = self.turn = None
        self.block_ids.clear()


def _name(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise BotError("corrupt")
    try:
        if len(value.encode("utf-8")) <= 128:
            return value
    except UnicodeError:
        pass
    raise BotError("corrupt")


def _head(context: _Context, outcome: Outcome) -> str:
    return f"{context.name} · Routine {outcome}\n\n"


def _tail(context: _Context) -> str:
    return f"\n\nRun: {context.attempt.session_id}"


class BotRuntime:
    def __init__(
        self,
        service: BotService,
        preferences: DecisionLoopStore,
        *,
        bridge: Callable[[], MattermostBot | None],
        bridge_workspace: Callable[[], str | None],
        attended: Callable[[], bool],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.service, self.preferences = service, preferences
        self.bridge, self.bridge_workspace, self.attended, self.now = (
            bridge,
            bridge_workspace,
            attended,
            now,
        )
        self._consents: dict[str, _Consent] = {}
        self._contexts: dict[str, _Context] = {}
        self._generation = 0
        self._closing = False
        self._storage_lock = asyncio.Lock()

    async def _storage[T](self, call: Callable[[], T]) -> T:
        # A shared-writer drain can settle many receipts together; serialize their SQLite calls.
        async with self._storage_lock:
            return await storage_call(call)

    def revoke(self, bot_id: str) -> None:
        self._generation += 1
        self._consents.pop(bot_id, None)

    def revoke_all(self) -> None:
        self._generation += 1
        self._consents.clear()

    def close(self) -> None:
        self._closing = True
        self.revoke_all()

    def delivery_status(self, bot: dict[str, Any]) -> dict[str, Any]:
        consent = self._consents.get(bot["bot_id"])
        enabled = consent is not None and consent.revision == bot["state_revision"]
        bridge = self.bridge()
        reason = "off"
        if self._closing or not self.attended():
            reason = "app_closing"
        elif (
            bot.get("profile") is None or bot["profile"]["archived"] or bot.get("workspace") is None
        ):
            reason = "bot_unavailable"
        elif bridge is None:
            reason = "not_connected"
        elif self.bridge_workspace() != bot["workspace_id"]:
            reason = "workspace_mismatch"
        else:
            key = bridge.destination_key()
            if key is None or (enabled and consent is not None and key != consent.destination):
                reason = "not_connected"
        available = reason == "off"
        return {
            "enabled": enabled,
            "available": available,
            "reason": "ready" if available and enabled else reason,
        }

    async def set_delivery(
        self, bot_id: str, expected_state_revision: int, enabled: bool
    ) -> dict[str, Any]:
        self.revoke(bot_id)
        generation = self._generation
        try:
            if type(enabled) is not bool:
                raise BotError("invalid_input")
            if self._closing:
                raise BotError("app_closing")
            bot = await self._storage(
                lambda: self.service.bump_revision(bot_id, expected_state_revision)
            )
        except BaseException:
            self.revoke(bot_id)
            raise
        if enabled:
            status = self.delivery_status(bot)
            bridge = self.bridge()
            key = bridge.destination_key() if bridge is not None else None
            if generation != self._generation or not status["available"] or key is None:
                raise BotError("delivery_unavailable")
            if len(self._consents) >= MAX_BOTS:
                raise BotError("capacity")
            self._consents[bot_id] = _Consent(generation, bot["state_revision"], key)
        return {**bot, "delivery": self.delivery_status(bot)}

    async def begin_attempt(self, attempt: Attempt, document: dict[str, Any]) -> None:
        if (
            self._closing
            or attempt.attempt_id in self._contexts
            or len(self._contexts) >= MAX_CONTEXTS
        ):
            return
        context = _Context(attempt, document["profile_id"], document["workspace_id"])
        self._contexts[attempt.attempt_id] = context
        generation = self._generation

        def owner() -> tuple[dict[str, Any] | None, str]:
            bot = self.service.find(context.profile_id, context.workspace_id)
            if bot is None:
                return None, "VIKI Agent"
            profile = self.service.profiles.get(context.profile_id)
            if profile["archived"]:
                raise BotError("profile_archived")
            return bot, _name(profile["document"]["identity"]["name"])

        try:
            bot, name = await self._storage(owner)
            if self._contexts.get(attempt.attempt_id) is not context:
                return
            if bot is None:
                self.discard_attempt(attempt)
                return
            context.bot_id, context.revision, context.name = (
                bot["bot_id"],
                bot["state_revision"],
                name,
            )
            consent = self._consents.get(bot["bot_id"])
            if (
                consent is not None
                and consent.generation <= generation
                and consent.revision == context.revision
            ):
                context.consent = consent
        except BaseException:
            self.discard_attempt(attempt)
            raise

    def _runtime_reason(self, context: _Context) -> str | None:
        if self._closing or not self.attended():
            return "app_closing"
        if context.consent is None:
            return "off"
        current = self._consents.get(context.bot_id or "")
        if (
            self._contexts.get(context.attempt.attempt_id) is not context
            or current != context.consent
        ):
            return "stale_consent"
        bridge = self.bridge()
        if bridge is None:
            return "not_connected"
        if self.bridge_workspace() != context.workspace_id:
            return "workspace_mismatch"
        if bridge.destination_key() != context.consent.destination:
            return "not_connected"
        return None

    def observe_attempt(
        self, attempt: Attempt, actor: SessionActor, preview: dict[str, Any]
    ) -> Callable[[dict[str, Any]], None] | None:
        context = self._contexts.get(attempt.attempt_id)
        if context is None or context.terminal or self._runtime_reason(context) is not None:
            return None
        identity = preview.get("identity")
        revision = preview.get("profile_revision")
        if (
            actor.session_id != attempt.session_id
            or preview.get("session_id") != actor.session_id
            or preview.get("generation") != actor.generation
            or preview.get("profile_id") != context.profile_id
            or preview.get("workspace_id") != context.workspace_id
            or type(revision) is not int
            or revision <= 0
            or not isinstance(identity, dict)
            or identity.get("profile_revision") != revision
        ):
            return None
        try:
            context.name = _name(identity.get("name"))
        except BotError:
            return None
        context.generation, context.sequence = actor.generation, actor.seq
        context.prepared = context.collecting = True
        reserved = chat_text(_head(context, "succeeded") + _SHORTENED + _tail(context))
        context.budget = MAX_NOTICE_BYTES - len(reserved.encode("utf-8"))
        return lambda envelope: self._observe(context, envelope)

    def _observe(self, context: _Context, envelope: dict[str, Any]) -> None:
        if not context.collecting or context.terminal:
            return
        sequence = envelope.get("seq")
        if (
            envelope.get("v") != 2
            or envelope.get("session_id") != context.attempt.session_id
            or envelope.get("generation") != context.generation
            or type(sequence) is not int
            or sequence <= context.sequence
        ):
            return
        context.sequence = sequence
        kind, data = envelope.get("type"), envelope.get("data")
        if not isinstance(data, dict):
            return
        if kind == "turn.started":
            turn = data.get("turn_id")
            if context.turn is not None:
                context.collecting = False
            elif isinstance(turn, str) and len(turn) == 36:
                try:
                    if str(uuid.UUID(turn)) == turn:
                        context.turn = turn
                except ValueError:
                    pass
            return
        if context.turn is None:
            return
        if kind == "turn.done":
            context.collecting = False
            return
        if kind != "text.done":
            return
        context.block_events += 1
        block_id, text = data.get("block_id"), data.get("text")
        try:
            if (
                context.block_events > MAX_BLOCK_EVENTS
                or not isinstance(block_id, str)
                or len(block_id) > 128
                or len(block_id.encode("utf-8")) > 128
                or not isinstance(text, str)
            ):
                raise ValueError
            if block_id in context.block_ids:
                return
            if len(context.block_ids) >= MAX_BLOCK_IDS:
                raise ValueError
            context.block_ids.add(block_id)
            if not text:
                return
            separator = "\n\n" if context.text else ""
            remaining = context.budget - context.text_bytes - len(separator)
            parts: list[str] = []
            overflow = False
            for char in text:
                size = len(char.encode("utf-8"))
                if size > remaining:
                    overflow = True
                    break
                parts.append(char)
                remaining -= size
            fragment = "".join(parts)
            head, tail = _head(context, "succeeded"), _tail(context)

            def fits(length: int) -> bool:
                body = context.text + (separator + fragment[:length] if length else "")
                try:
                    rendered = chat_text(head + body + _SHORTENED + tail)
                except ValueError:
                    return False
                return len(rendered.encode("utf-8")) <= MAX_NOTICE_BYTES

            if not fits(len(fragment)):
                low, high = 0, len(fragment)
                while low < high:
                    middle = (low + high + 1) // 2
                    if fits(middle):
                        low = middle
                    else:
                        high = middle - 1
                fragment = fragment[:low]
                overflow = True
            if fragment:
                context.text += separator + fragment
                context.text_bytes += len(separator) + len(fragment.encode("utf-8"))
            if overflow:
                context.shortened = True
                context.collecting = False
        except ValueError, UnicodeError:
            context.shortened = True
            context.collecting = False

    def _evaluate(self, context: _Context) -> tuple[Verdict, str, str]:
        assert context.bot_id is not None
        bot = self.service.record(context.bot_id)
        current = (
            bot["state_revision"] == context.revision
            and bot["profile_id"] == context.profile_id
            and bot["workspace_id"] == context.workspace_id
        )
        try:
            current = current and not self.service.profiles.get(context.profile_id)["archived"]
            current = current and self.service.workspaces.get(context.workspace_id).persistent
        except ProfileError, WorkspaceError:
            current = False
        now = self.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise BotError("unavailable")
        local = now.astimezone()
        prefs = self.preferences.preferences(context.workspace_id, local)
        decision = decide("agent_runs", {"working": 0, "review": 1}, local, prefs)
        runtime_reason = self._runtime_reason(context)
        if runtime_reason is not None:
            state = (
                "unavailable"
                if runtime_reason in {"not_connected", "workspace_mismatch"}
                else "off"
            )
            return decision, state, runtime_reason
        if not current:
            return decision, "off", "stale_bot"
        if context.outcome not in {"succeeded", "failed", "blocked", "expired"} or (
            context.outcome == "succeeded" and not context.prepared
        ):
            return decision, "off", "unsupported_outcome"
        if decision.attention != "notify":
            return decision, "held", "held"
        return decision, "sending", "none"

    def _update(self, context: _Context, decision: Verdict, state: str, reason: str) -> bool:
        return self.service.update_delivery(
            context.attempt.attempt_id,
            expected_state="sending",
            attention=decision.attention,
            reason=decision.reason,
            state=state,
            delivery_reason=reason,
        )

    async def _guard(self, context: _Context) -> bool:
        try:
            decision, state, reason = await self._storage(lambda: self._evaluate(context))
        except BotError as error:
            if error.code == "not_found":
                return False
            raise
        runtime_reason = self._runtime_reason(context)
        if state == "sending" and runtime_reason is not None:
            state = (
                "unavailable"
                if runtime_reason in {"not_connected", "workspace_mismatch"}
                else "off"
            )
            reason = runtime_reason
        if state != "sending":
            await self._storage(lambda: self._update(context, decision, state, reason))
            return False
        return True

    def _message(self, context: _Context) -> str:
        head, tail = _head(context, context.outcome), _tail(context)
        if context.outcome == "succeeded":
            body = (
                context.text or "The routine completed. Open VIKI Agent desktop to review the run."
            )
            if context.shortened:
                body += _SHORTENED
        else:
            body = {
                "failed": "The routine did not complete successfully.",
                "blocked": "Desktop review or approval is needed before the routine can continue.",
                "expired": "The routine reached its deadline.",
            }[context.outcome]
            body += "\nOpen VIKI Agent desktop to review the run."
        message = head + body + tail
        if len(chat_text(message).encode("utf-8")) > MAX_NOTICE_BYTES:
            raise BotError("capacity")
        return message

    async def attempt_finished(self, attempt: Attempt, outcome: Outcome, reason: str) -> None:
        context = self._contexts.get(attempt.attempt_id)
        if context is None or context.terminal or context.bot_id is None:
            return
        context.terminal, context.outcome = True, outcome
        context.clear_collection()
        try:
            try:
                decision, state, delivery_reason = await self._storage(
                    lambda: self._evaluate(context)
                )
            except BotError as error:
                if error.code == "not_found":
                    return
                raise
            if (
                not await self._storage(
                    lambda: self.service.record_delivery(
                        attempt.attempt_id,
                        context.bot_id or "",
                        attention=decision.attention,
                        reason=decision.reason,
                        state=state,
                        delivery_reason=delivery_reason,
                    )
                )
                or state != "sending"
            ):
                return
            message = self._message(context)
            context.text = ""
            bridge = self.bridge()
            assert context.consent is not None
            if bridge is None:
                result = "unavailable"
            else:
                try:
                    result = await bridge.send_notice(
                        message,
                        destination_key=context.consent.destination,
                        current=lambda: self._guard(context),
                    )
                except asyncio.CancelledError:
                    await self._storage(
                        lambda: self._update(context, decision, "unknown", "send_unknown")
                    )
                    raise
            result_reason = {
                "sent": "none",
                "failed": "send_failed",
                "unknown": "send_unknown",
                "unavailable": "not_connected",
            }[result]
            await self._storage(lambda: self._update(context, decision, result, result_reason))
        finally:
            self.discard_attempt(attempt)

    def discard_attempt(self, attempt: Attempt) -> None:
        context = self._contexts.pop(attempt.attempt_id, None)
        if context is not None:
            context.terminal = True
            context.clear_collection()
            context.text = ""
