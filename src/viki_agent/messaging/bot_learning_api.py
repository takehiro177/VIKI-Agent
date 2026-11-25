"""The window's Bot learning routes: what a Bot learned, its proposals, and the owner's decisions.

Window token only; any other Origin is refused before work starts; a request body is a JSON object
of exactly its route's keys within MAX_BODY bytes, read within INTAKE_TIMEOUT_S. At most
MAX_REQUESTS run at once; each runs in its own task, which a client that leaves does not cancel.

    GET  /api/v2/bot-learning/{bot_id}                     the Bot's overview
    POST /api/v2/bot-learning/{bot_id}/runs                {"mode": "reflect" | "dream"}
    POST /api/v2/bot-learning/{bot_id}/import              {} - earlier memory files as proposals
    POST /api/v2/bot-learning/proposals/{proposal_id}      {"action": "apply" | "reject" | "undo"}
    POST /api/v2/bot-learning/sessions/{session_id}/untaint  {}

Only `apply` and `undo` write the vault, each through the memory service's journalled save.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from anyio.to_thread import run_sync
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from viki_agent.api_json import failure, json_response, unique_object
from viki_agent.messaging.bot_learning import BotLearning, LearningError
from viki_agent.messaging.bot_proposals import ProposalError
from viki_agent.messaging.bot_reflect import LearningRunner
from viki_agent.storage import StateError
from viki_agent.vaults.memory import MemoryServiceError

log = logging.getLogger(__name__)

BASE = "/api/v2/bot-learning"
MAX_BODY = 1024
MAX_REQUESTS = 4
INTAKE_TIMEOUT_S = 5

_REFUSALS: dict[str, tuple[int, str]] = {
    "invalid_input": (400, "That request isn't one this page sends."),
    "intake_timeout": (408, "The request took too long to arrive. Try again."),
    "not_found": (404, "That Bot or proposal isn't there anymore."),
    "workspace_missing": (404, "This Bot's vault isn't registered anymore."),
    "vault_not_ordinary": (409, "Bots learn only in an ordinary vault VIKI Agent may write."),
    "not_active": (409, "Open this Bot's vault first, then start the run."),
    "run_running": (409, "Another learning run is going. Try again when it ends."),
    "no_episodes": (409, "This Bot has no finished work to learn from yet."),
    "no_excerpts": (409, "The finished work couldn't be read from its chats."),
    "nothing_to_import": (409, "This Bot has no earlier memory files to import."),
    "conflict": (409, "This proposal was decided already. Reload to see where it stands."),
    "not_undoable": (409, "Only an applied addition can be undone."),
    "vault_protected": (409, "This vault is protected, so VIKI Agent can't write it."),
    "read_only": (409, "This vault is read-only, so VIKI Agent can't write it."),
    "overloaded": (429, "Busy with other Bot learning requests. Try again shortly."),
    "unavailable": (503, "Bot learning is unavailable right now. Try again."),
    "corrupt": (500, "This proposal can't be read."),
}


def _refusal(code: str) -> Response:
    status, message = _REFUSALS.get(code, (500, "That couldn't be done. Try again."))
    if status >= 500:
        log.warning("Bot learning request failed: %s", code)
    return failure(code if code in _REFUSALS else "internal", message, status)


def _uuid(value: str) -> str:
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except ValueError:
        pass
    raise LearningError("not_found")


async def _body(request: Request, keys: frozenset[str]) -> dict[str, Any]:
    raw = bytearray()
    try:
        with anyio.fail_after(INTAKE_TIMEOUT_S):
            async for chunk in request.stream():
                if len(chunk) > MAX_BODY - len(raw):
                    raise LearningError("invalid_input")
                raw.extend(chunk)
    except TimeoutError:
        raise LearningError("intake_timeout") from None
    try:
        value = json.loads(raw.decode("utf-8") or "{}", object_pairs_hook=unique_object)
    except ValueError, RecursionError:
        raise LearningError("invalid_input") from None
    if not isinstance(value, dict) or set(value) != keys:
        raise LearningError("invalid_input")
    return value


def bot_learning_routes(
    learning: BotLearning,
    runner: LearningRunner,
    *,
    authorized: Callable[[Request], bool],
    origins: frozenset[str],
    workers: set[asyncio.Task[Any]],
) -> list[Route]:
    capacity = anyio.CapacityLimiter(MAX_REQUESTS)

    async def overview(request: Request) -> Response:
        bot_id = _uuid(request.path_params["bot_id"])
        value = await run_sync(learning.overview, bot_id)
        running = runner.running
        value["running"] = None if running is None or running.bot_id != bot_id else running.run_id
        return json_response(value)

    async def start(request: Request) -> Response:
        bot_id = _uuid(request.path_params["bot_id"])
        mode = (await _body(request, frozenset({"mode"})))["mode"]
        if mode not in ("reflect", "dream"):
            raise LearningError("invalid_input")
        run = await runner.start(bot_id, mode)
        return json_response({"run": run.__dict__}, 202)

    async def import_legacy(request: Request) -> Response:
        bot_id = _uuid(request.path_params["bot_id"])
        await _body(request, frozenset())
        run = await run_sync(learning.import_legacy, bot_id)
        return json_response({"run": run.__dict__})

    async def decide(request: Request) -> Response:
        proposal_id = _uuid(request.path_params["proposal_id"])
        action = (await _body(request, frozenset({"action"})))["action"]
        if action == "apply":
            state = await learning.apply(proposal_id)
        elif action == "reject":
            await learning.reject(proposal_id)
            state = "rejected"
        elif action == "undo":
            await learning.undo(proposal_id)
            state = "undone"
        else:
            raise LearningError("invalid_input")
        return json_response({"proposal_id": proposal_id, "state": state})

    async def untaint(request: Request) -> Response:
        session_id = _uuid(request.path_params["session_id"])
        await _body(request, frozenset())
        cleared = await run_sync(learning.untaint, session_id)
        return json_response({"session_id": session_id, "cleared": cleared})

    def guarded(handler: Callable[[Request], Awaitable[Response]]) -> Callable[..., Any]:
        async def perform(request: Request) -> Response:
            try:
                return await handler(request)
            except LearningError as error:
                return _refusal(error.code)
            except ProposalError as error:
                return _refusal(error.code)
            except MemoryServiceError as error:
                return _refusal(error.code)
            except StateError:
                return _refusal("unavailable")

        async def endpoint(request: Request) -> Response:
            origin = request.headers.get("origin")
            if not authorized(request):
                response = failure(
                    "unauthorized", "Can't authenticate. Open VIKI Agent again.", 401
                )
            elif origin is not None and origin not in origins:
                response = failure("forbidden_origin", "This window can't show that.", 403)
            elif request.scope.get("query_string", b""):
                response = _refusal("invalid_input")
            else:
                permit = object()
                try:
                    capacity.acquire_on_behalf_of_nowait(permit)
                except anyio.WouldBlock:
                    response = _refusal("overloaded")
                else:
                    worker = asyncio.create_task(perform(request))
                    workers.add(worker)

                    def done(task: asyncio.Task[Response]) -> None:
                        workers.discard(task)
                        capacity.release_on_behalf_of(permit)
                        if not task.cancelled():
                            task.exception()

                    worker.add_done_callback(done)
                    response = await asyncio.shield(worker)
            response.headers["Cache-Control"] = "no-store"
            return response

        return endpoint

    return [
        Route(BASE + "/proposals/{proposal_id}", guarded(decide), methods=["POST"]),
        Route(BASE + "/sessions/{session_id}/untaint", guarded(untaint), methods=["POST"]),
        Route(BASE + "/{bot_id}", guarded(overview), methods=["GET"]),
        Route(BASE + "/{bot_id}/runs", guarded(start), methods=["POST"]),
        Route(BASE + "/{bot_id}/import", guarded(import_legacy), methods=["POST"]),
    ]
