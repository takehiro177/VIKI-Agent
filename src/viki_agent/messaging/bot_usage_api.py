"""Bot usage routes: the usage dashboard and one session's trace, plus the turn-end scheduler
that keeps them warm.

`GET /api/v2/bot-usage/summary/{days}` and `GET /api/v2/bot-usage/sessions/{session_id}` answer
`UsageViews.summary`/`.trace`. Each is a blocking capture of the active vault
(`messaging/bot_usage.py`) followed by a read-only report (`messaging/bot_usage_report.py`), run
in a worker thread behind a small capacity limiter, the same shielded-task shape
`conversations/subagents_api.py` uses for its own read route. `turn_end_capture` returns the
loop-side hook a chat turn's end calls with its session id: it batches ids into one drain task
that feeds `UsageViews.capture_ids`, so a dashboard opened right after reads warm data without
every turn paying for its own capture.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

import anyio
from anyio.to_thread import run_sync
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from viki_agent.api_json import failure, json_response
from viki_agent.catalog import _UUID_RE
from viki_agent.messaging import bot_usage, bot_usage_report
from viki_agent.messaging.bot_usage import Attribution, BotUsageError, Capture
from viki_agent.messaging.bot_usage_report import Names
from viki_agent.messaging.bots_api import _headers
from viki_agent.storage import SessionStore

MAX_REQUESTS = 2
MAX_ANSWER = 1 << 20  # the browser refuses a larger body
CAPTURE_DEADLINE_S = 3
TURN_END_MAX_PENDING = 64
BASE = "/api/v2/bot-usage"
_DAYS = {"7": 7, "30": 30, "90": 90}
_STATUS = {"not_found": 404, "unavailable": 503, "corrupt": 503}
logger = logging.getLogger(__name__)


class _Usage(Protocol):
    """The `BotUsage` surface `UsageViews` calls."""

    def sessions(self, workspace_id: str) -> list[Attribution]: ...

    def capture(self, workspace_id: str, session_ids: Iterable[str] | None = None) -> Capture: ...


class _Views(Protocol):
    """The `UsageViews` surface the routes and the turn-end scheduler call."""

    def summary(self, days: int) -> dict[str, Any]: ...

    def trace(self, session_id: str) -> dict[str, Any]: ...

    def capture_ids(self, ids: Iterable[str]) -> None: ...


class UsageViews:
    """Blocking reads over the active vault's Bot usage: a capture, then a read-only report.
    Call `summary`, `trace` and `capture_ids` off the event loop (a worker thread): each runs
    `BotUsage.capture`, which blocks on file reads and the store's write lock."""

    def __init__(
        self,
        usage: _Usage,
        store: SessionStore,
        *,
        active: Callable[[], str],
        names: Callable[[], Names],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.usage = usage
        self.store = store
        self.active = active
        self.names = names
        self.now = now
        self.monotonic = monotonic

    def _names(self) -> Names:
        try:
            return self.names()
        except Exception:
            return Names(profiles={}, bots=frozenset(), connections={})

    def _logged(self, workspace_id: str) -> frozenset[str]:
        """The session ids `bot_usage_sessions` already holds for `workspace_id`."""
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT session_id FROM bot_usage_sessions WHERE workspace_id=?", (workspace_id,)
            ).fetchall()
        return frozenset(row[0] for row in rows)

    def summary(self, days: int) -> dict[str, Any]:
        """The active vault's usage dashboard. Repeats the capture while it leaves sessions
        pending and CAPTURE_DEADLINE_S has not yet passed since the first one began, so a first
        open after the upgrade reads most history in one answer. The deadline is timed on the
        injected monotonic clock, never the wall clock, so a clock step backwards cannot stretch
        it; a capture that adds no rows and leaves pending no lower than the one before it made
        no progress, and repeating it would not help, so the loop stops there too."""
        workspace_id = self.active()
        deadline = self.monotonic() + CAPTURE_DEADLINE_S
        capture = self.usage.capture(workspace_id)
        while capture.pending and self.monotonic() < deadline:
            previous = capture
            capture = self.usage.capture(workspace_id)
            if capture.added == 0 and capture.pending >= previous.pending:
                break
        logged = self._logged(workspace_id)
        unlogged = sum(
            1
            for session in self.usage.sessions(workspace_id)
            if session.started
            and session.session_id not in capture.present
            and session.session_id not in logged
        )
        return bot_usage_report.summary(
            self.store,
            workspace_id,
            days,
            now=self.now(),
            present=capture.present,
            pending=capture.pending,
            unlogged=unlogged,
            names=self._names(),
            max_rows=bot_usage.MAX_CALLS,
            max_age_days=bot_usage.MAX_AGE_DAYS,
        )

    def trace(self, session_id: str) -> dict[str, Any]:
        """One session's own call log, after a capture of just that session."""
        workspace_id = self.active()
        capture = self.usage.capture(workspace_id, [session_id])
        result = bot_usage_report.trace(
            self.store, workspace_id, session_id, present=capture.present, names=self._names()
        )
        if result is None:
            raise BotUsageError("not_found")
        return result

    def capture_ids(self, ids: Iterable[str]) -> None:
        """Record the new calls of `ids` among the active vault's Bot chats."""
        self.usage.capture(self.active(), ids)


def _bounded(value: dict[str, Any]) -> Response:
    response = json_response(value)
    if len(response.body) > MAX_ANSWER:
        raise BotUsageError("unavailable")
    return response


def bot_usage_routes(
    views: _Views,
    *,
    authorized: Callable[[Request], bool],
    origins: frozenset[str],
    workers: set[asyncio.Task[Any]],
) -> list[Route]:
    capacity = anyio.CapacityLimiter(MAX_REQUESTS)

    async def perform(request: Request) -> Response:
        days = request.path_params.get("days")
        if days is not None:
            return _bounded(await run_sync(views.summary, _DAYS[days]))
        return _bounded(await run_sync(views.trace, request.path_params["session_id"]))

    async def safe_perform(request: Request) -> Response:
        try:
            response = await perform(request)
        except Exception as error:
            code = getattr(error, "code", None)
            if not isinstance(code, str) or code not in _STATUS:
                logger.warning("Bot usage request failed: %s", type(error).__name__)
                code = "unavailable"
            response = failure(code, "The Bot usage request could not be completed.", _STATUS[code])
        response.headers["Cache-Control"] = "no-store"
        return response

    async def admitted(request: Request) -> Response:
        permit = object()
        try:
            capacity.acquire_on_behalf_of_nowait(permit)
        except anyio.WouldBlock:
            return failure("overloaded", "Bot usage is busy.", 429)
        task = asyncio.create_task(safe_perform(request))
        workers.add(task)

        def done(future: asyncio.Task[Response]) -> None:
            workers.discard(future)
            capacity.release_on_behalf_of(permit)
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def endpoint(request: Request) -> Response:
        origins_given = request.headers.getlist("origin")
        days = request.path_params.get("days")
        session_id = request.path_params.get("session_id")
        if len(request.headers.getlist("authorization")) != 1 or not authorized(request):
            response = failure("unauthorized", "Authentication is required.", 401)
        elif len(origins_given) > 1 or (origins_given and origins_given[0] not in origins):
            response = failure("forbidden_origin", "This origin is not allowed.", 403)
        elif (
            not _headers(request)
            or (days is not None and days not in _DAYS)
            or (session_id is not None and not _UUID_RE.match(session_id))
        ):
            response = failure("invalid_input", "The Bot usage request is invalid.", 400)
        else:
            response = await admitted(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return [
        Route(BASE + "/summary/{days}", endpoint, methods=["GET"]),
        Route(BASE + "/sessions/{session_id}", endpoint, methods=["GET"]),
    ]


def turn_end_capture(views: _Views, workers: set[asyncio.Task[Any]]) -> Callable[[str], None]:
    """The loop-side hook a chat turn's end calls with its session id. Batches ids into one
    drain task that feeds them to `views.capture_ids` in a thread, so a dashboard opened right
    after reads warm data without every turn paying for its own capture. Never raises."""
    pending: set[str] = set()
    task: asyncio.Task[None] | None = None

    async def drain() -> None:
        nonlocal task
        try:
            while pending:
                batch = list(pending)
                pending.clear()
                try:
                    await run_sync(views.capture_ids, batch)
                except Exception as error:
                    logger.warning("Bot usage turn-end capture failed: %s", type(error).__name__)
        finally:
            task = None

    def schedule(session_id: str) -> None:
        nonlocal task
        if session_id in pending or len(pending) < TURN_END_MAX_PENDING:
            pending.add(session_id)
        if task is None:
            task = asyncio.create_task(drain())
            workers.add(task)
            task.add_done_callback(workers.discard)

    return schedule
