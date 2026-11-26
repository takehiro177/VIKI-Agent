"""Safety changes a chat asks for wait here for the window's Apply.

The agent surface holds a safety change with `AgentChanges.hold` and records each change it
applies itself with `record`; every hold and every record lands in a 64-entry journal with the
calling chat's session id, and `seq` rises per applied change. `made` answers whether a chat's
session id recorded an operation on a target since launch, from a set the journal's cap does not
trim, so a resumed chat (a new key for the same session) keeps what it made. A hold expires
after `ttl_s` or once its chat's process has ended (`live(grant_id)` is false): it is then no
longer listed and a decision on it gets 409 `expired`. The two Apply routes answer only requests
`authorized` accepts, meant to be the window token alone, and a POST needs an allowed Origin. A
hold applies at most once: the decision marks it `applying` before its first await, and its
apply runs in a task tracked in `workers` under `asyncio.shield`, so a client that leaves does
not cancel it midway. Holds, the journal and what `made` reads live in memory only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.routing import Route

from viki_agent.api_json import failure, json_response, unique_object

MAX_PENDING_PER_CHAT = 4
MAX_PENDING = 16
JOURNAL_SIZE = 64
RECENT_SIZE = 20
MAX_DECISION_BYTES = 1 << 10

logger = logging.getLogger(__name__)

_REFUSALS = {
    "unauthorized": (401, "Can't authenticate. Open VIKI Agent again."),
    "forbidden_origin": (403, "This window can't do that."),
    "invalid_input": (400, "Send one decision: apply or reject."),
    "not_found": (404, "No change has that id."),
    "expired": (409, "That change expired."),
    "not_pending": (409, "That change was already decided."),
}


def _refusal(code: str) -> Response:
    status, message = _REFUSALS[code]
    return failure(code, message, status)


class AgentChangeError(Exception):
    """A refusal answered with `status` and `{"error": {"code", "message"}}`."""

    def __init__(self, code: str, status: int, message: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message


@dataclass(slots=True)
class _Hold:
    grant_id: str
    kind: str
    summary: dict[str, Any]
    apply: Callable[[], Awaitable[dict[str, Any]]]
    deadline: float
    entry: dict[str, Any]  # its journal entry; `state` moves on in place


def _entry(session_id: str, operation: str, target: str, state: str) -> dict[str, Any]:
    return {
        "change_id": str(uuid.uuid4()),
        "session_id": session_id,
        "operation": operation,
        "target": target,
        "state": state,
    }


async def _decision(request: Request) -> str | None:
    """The body's decision, or None unless it is exactly `{"decision": "apply" | "reject"}`
    within `MAX_DECISION_BYTES`."""
    body = bytearray()
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_DECISION_BYTES:
                return None
    except ClientDisconnect:
        return None
    try:
        raw = json.loads(body.decode("utf-8"), object_pairs_hook=unique_object)
    except ValueError, RecursionError:
        return None
    if not isinstance(raw, dict) or set(raw) != {"decision"}:
        return None
    return raw["decision"] if raw["decision"] in ("apply", "reject") else None


class AgentChanges:
    """Holds, the journal and `seq`. One lock guards them and is never held across an await,
    so a caller on a worker thread is safe too."""

    def __init__(
        self,
        live: Callable[[str], bool],
        *,
        clock: Callable[[], float] = time.monotonic,
        ttl_s: float = 600.0,
    ) -> None:
        self._live = live
        self._clock = clock
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._holds: dict[str, _Hold] = {}
        self._journal: deque[dict[str, Any]] = deque(maxlen=JOURNAL_SIZE)
        # (session id, operation, target) of every `record`, never trimmed: what `made` reads.
        self._made: set[tuple[str, str, str]] = set()
        self.seq = 0

    def record(self, grant_id: str, session_id: str, operation: str, target: str) -> None:
        """Journals a change the agent surface applied itself."""
        with self._lock:
            self._journal.append(_entry(session_id, operation, target, "applied"))
            self._made.add((session_id, operation, target))
            self.seq += 1

    def made(self, session_id: str, operation: str, target: str) -> bool:
        """Whether `record` recorded `operation` on `target` for the chat `session_id` since
        launch, under any of its keys; a held change never counts."""
        with self._lock:
            return (session_id, operation, target) in self._made

    def hold(
        self,
        grant_id: str,
        session_id: str,
        operation: str,
        target: str,
        kind: str,
        summary: dict[str, Any],
        apply: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Keeps `apply` until the window decides. Over a cap: 429 `too_many_pending`."""
        with self._lock:
            now = self._clock()
            self._expire(now)
            mine = sum(hold.entry["session_id"] == session_id for hold in self._holds.values())
            if mine >= MAX_PENDING_PER_CHAT or len(self._holds) >= MAX_PENDING:
                raise AgentChangeError(
                    "too_many_pending", 429, "Too many changes are waiting for Apply."
                )
            entry = _entry(session_id, operation, target, "pending")
            self._journal.append(entry)
            self._holds[entry["change_id"]] = _Hold(
                grant_id, kind, summary, apply, now + self._ttl_s, entry
            )
            return {"change_id": entry["change_id"], "expires_in_s": math.ceil(self._ttl_s)}

    def for_session(self, session_id: str) -> dict[str, Any]:
        """One chat's waiting holds and its newest journal entries, newest first."""
        with self._lock:
            pending = [view for view in self._pending() if view["session_id"] == session_id]
            recent = [dict(entry) for entry in self._journal if entry["session_id"] == session_id]
        return {"pending": pending, "recent": recent[::-1][:RECENT_SIZE]}

    def _expire(self, now: float) -> None:
        """Under the lock: a waiting hold past its deadline, or whose chat's process has
        ended, becomes `expired` and is dropped."""
        for change_id, hold in list(self._holds.items()):
            if hold.entry["state"] == "pending" and (
                now >= hold.deadline or not self._live(hold.grant_id)
            ):
                hold.entry["state"] = "expired"
                del self._holds[change_id]

    def _pending(self) -> list[dict[str, Any]]:
        """Under the lock: the holds still waiting for a decision."""
        now = self._clock()
        self._expire(now)
        return [
            {
                "change_id": hold.entry["change_id"],
                "session_id": hold.entry["session_id"],
                "kind": hold.kind,
                "summary": hold.summary,
                "expires_in_s": math.ceil(hold.deadline - now),
            }
            for hold in self._holds.values()
            if hold.entry["state"] == "pending"
        ]

    def _claim(self, change_id: str, state: str) -> _Hold | str:
        """Moves a waiting hold to `state` ("applying" or "rejected") and returns it, else
        the refusal's code. Atomic under the lock with no await, so of two decisions on one
        hold only the first claims it. An `applying` hold stays until its apply ends."""
        with self._lock:
            self._expire(self._clock())
            hold = self._holds.get(change_id)
            if hold is not None and hold.entry["state"] == "pending":
                hold.entry["state"] = state
                if state != "applying":
                    del self._holds[change_id]
                return hold
            if hold is not None:
                return "not_pending"
            known = [entry["state"] for entry in self._journal if entry["change_id"] == change_id]
        if not known:
            return "not_found"
        return "expired" if known[0] == "expired" else "not_pending"

    async def _apply(self, hold: _Hold) -> dict[str, Any]:
        state = "failed"
        try:
            result = await hold.apply()
            state = "applied"
            return result
        except AgentChangeError:
            raise
        except Exception as error:
            # Hard rule 8: the type is the whole record, never the change's values.
            logger.warning("agent change failed (%s)", type(error).__name__)
            raise AgentChangeError("unavailable", 503, "Couldn't apply the change.") from None
        finally:
            with self._lock:
                hold.entry["state"] = state
                del self._holds[hold.entry["change_id"]]
                if state == "applied":
                    self.seq += 1

    def routes(
        self,
        *,
        authorized: Callable[[Request], bool],
        origins: frozenset[str],
        workers: set[asyncio.Task[Any]],
    ) -> list[Route]:
        """The window's Apply surface; `authorized` accepts the window token only."""

        async def listing(request: Request) -> Response:
            if not authorized(request):
                return _refusal("unauthorized")
            origin = request.headers.get("origin")
            if origin is not None and origin not in origins:
                return _refusal("forbidden_origin")
            with self._lock:
                body = {"seq": self.seq, "changes": self._pending()}
            return json_response(body)

        async def decide(request: Request) -> Response:
            if not authorized(request):
                return _refusal("unauthorized")
            if request.headers.get("origin") not in origins:
                return _refusal("forbidden_origin")
            decision = await _decision(request)
            if decision is None:
                return _refusal("invalid_input")
            state = "applying" if decision == "apply" else "rejected"
            claimed = self._claim(request.path_params["change_id"], state)
            if isinstance(claimed, str):
                return _refusal(claimed)
            if decision == "reject":
                return json_response({"state": "rejected"})
            worker = asyncio.create_task(self._apply(claimed))
            workers.add(worker)

            def done(task: asyncio.Task[dict[str, Any]]) -> None:
                workers.discard(task)
                # A client that left cannot observe the outcome; retrieve it so asyncio
                # reports nothing.
                if not task.cancelled():
                    task.exception()

            worker.add_done_callback(done)
            try:
                result = await asyncio.shield(worker)
            except AgentChangeError as error:
                return failure(error.code, error.message, error.status)
            return json_response({"state": "applied", "result": result})

        return [
            Route("/api/v2/agent-changes", listing, methods=["GET"]),
            Route("/api/v2/agent-changes/{change_id}", decide, methods=["POST"]),
        ]
