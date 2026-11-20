"""The agent surface: what an ordinary chat's `viki-agent` command may read and change.

One route, `/api/agent/{grant_id}/{operation}`, answers only the key `AgentGrants` issued for that
chat's Claude process. Admission runs in this order: exactly one `Authorization: Bearer` that
`AgentGrants.find` accepts, else one identical 401 `unauthorized`; any Origin, 403
`forbidden_origin`; a query string, 400 `invalid_input`; an operation outside `OPS`, 404
`not_found`; a JSON object with exactly the operation's keys, at most `MAX_BODY` bytes, read
within `BODY_TIMEOUT_S`; at most `MAX_IN_FLIGHT` operations at once, else 429 `overloaded`. Each
operation runs in its own task, which a client that leaves does not cancel, and its service calls
run in worker threads.

`OPS` gives each operation its level. A read changes nothing. A change applies at once and is
journaled with the calling chat's session id (`AgentChanges.record`). A safety change, a setting
outside `CHANGE_SETTINGS` or enabling a routine, waits for the window's Apply (`AgentChanges.hold`);
a held setting applies at the revision current at Apply unless its own stored value changed since
it was held. `settings` and `status` leave out every credential key (`is_credential_key`), and a
change to one gets 403 `secret_key`. `status` names a chat's pending holds without their
summaries' values (`_CHAT_VIEW`); only the window's Apply list shows those. A 5xx logs its code
only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Coroutine
from typing import Any

import anyio
from anyio.to_thread import run_sync
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.routing import Route

from viki_agent.api_json import json_response, unique_object
from viki_agent.app import settings_api
from viki_agent.app.agent_changes import AgentChangeError, AgentChanges
from viki_agent.app.agent_grants import AgentGrant, AgentGrants
from viki_agent.app.diagnostics import is_credential_key
from viki_agent.app.settings import (
    KEYS,
    SettingsConflict,
    SettingsError,
    SettingsService,
    _command_id,
    _validate_change,
    check_change,
    to_view,
)
from viki_agent.automation import routines_api
from viki_agent.automation.routines import (
    _TRANSITIONS,
    RoutineError,
    RoutineService,
    _integer,
    _uuid,
)
from viki_agent.automation.scheduler import Scheduler
from viki_agent.automation.tasks import TaskRunner
from viki_agent.conversations.command_runtime import storage_call
from viki_agent.domain import EffectiveSettings
from viki_agent.messaging import bots_api
from viki_agent.messaging.bots import BotError, BotService
from viki_agent.profiles.service import ProfileService
from viki_agent.workspaces import WorkspaceService

MAX_BODY = 128 << 10
BODY_TIMEOUT_S = 5.0
MAX_IN_FLIGHT = 4
# How much of a long string an Apply card carries: a held setting's `from` and `to`, a routine's
# prompt. The held change itself keeps the whole value.
SUMMARY_CHARS = 2000
# A hold's kind -> the summary fields its chat sees in `status`. Never a value: a routine's prompt
# is sealed from chats, and a setting's `from` and `to` are for the window's Apply card.
_CHAT_VIEW = {"setting": ("key",), "routine_enable": ("routine_id", "name")}

logger = logging.getLogger(__name__)

# (method, operation) -> its level. `PATCH settings` is a safety change for every key outside
# CHANGE_SETTINGS; enabling a routine always is.
OPS: dict[tuple[str, str], str] = {
    ("GET", "status"): "read",
    ("GET", "settings"): "read",
    ("PATCH", "settings"): "change",
    ("GET", "routines"): "read",
    ("POST", "routines"): "change",
    ("POST", "routines/{id}/run"): "change",
    ("POST", "routines/{id}/enable"): "safety",
    ("POST", "bots"): "change",
}

# (method, operation) -> the window routes, as (method, route path), whose work it does. A route
# path ending in `{action}` is named per action, with the action in its place.
COVERS: dict[tuple[str, str], frozenset[tuple[str, str]]] = {
    ("GET", "status"): frozenset(
        {
            ("GET", "/api/v2/settings"),
            ("GET", "/api/v2/routines"),
            ("GET", "/api/v2/profiles"),
            ("GET", "/api/v2/workspaces"),
            ("GET", "/api/v2/bots"),
        }
    ),
    ("GET", "settings"): frozenset({("GET", "/api/v2/settings")}),
    ("PATCH", "settings"): frozenset({("PATCH", "/api/v2/settings")}),
    ("GET", "routines"): frozenset({("GET", "/api/v2/routines")}),
    ("POST", "routines"): frozenset({("POST", "/api/v2/routines")}),
    ("POST", "routines/{id}/run"): frozenset({("POST", "/api/v2/routines/{routine_id}/run")}),
    ("POST", "routines/{id}/enable"): frozenset({("POST", "/api/v2/routines/{routine_id}/enable")}),
    ("POST", "bots"): frozenset({("POST", "/api/v2/bots")}),
}

# The settings a chat changes without Apply. A key is listed only when no value of it can widen
# what a chat or VIKI may do or touch, what runs unattended or what it costs, where data goes,
# what notices are sent, or what text reaches an instruction file or prompt; only the window acts
# on these. Every other non-credential key, one added to KEYS later included, waits for Apply.
CHANGE_SETTINGS = frozenset(
    {
        # How the window looks and moves.
        "appearance.theme",
        "appearance.density",
        "accessibility.font_scale",
        "accessibility.reduce_motion",
        # The rails' widths and the launcher's place and size, in pixels.
        "layout.left_rail_width",
        "layout.right_rail_width",
        "layout.launcher",
        "layout.launcher_panel",
        # How code reads in the editor.
        "workbench.code_font_size",
        "workbench.line_wrap",
        # A Bot's colour and emblem in the window.
        "bots.hues",
        "bots.icons",
        # Read-aloud speed and voice; the window speaks only with voices installed here.
        "voice.read_aloud_rate",
        "voice.read_aloud_voices",
    }
)
# Settings whose change waits for Apply: every key that is neither change level nor a credential.
SAFETY_SETTINGS = frozenset(
    key for key in KEYS if key not in CHANGE_SETTINGS and not is_credential_key(key)
)

# (method, operation) -> the exact keys of its JSON body; an operation not listed reads none.
_BODIES: dict[tuple[str, str], frozenset[str]] = {
    ("PATCH", "settings"): frozenset({"command_id", "expected_revision", "key", "value"}),
    ("POST", "routines"): frozenset({"command_id", "document"}),
    ("POST", "routines/{id}/run"): frozenset({"command_id", "expected_revision"}),
    ("POST", "routines/{id}/enable"): frozenset({"command_id", "expected_state_revision"}),
    ("POST", "bots"): frozenset(
        {"bot_id", "profile_id", "expected_profile_revision", "workspace_id"}
    ),
}

_REFUSALS = {
    "unauthorized": (401, "Can't authenticate. Use a chat opened in the VIKI Agent window."),
    "forbidden_origin": (403, "A browser can't use this."),
    "invalid_input": (400, "Check the request."),
    "not_found": (404, "Unknown operation."),
    "intake_timeout": (408, "The request did not arrive in time."),
    "too_large": (413, "The request is too large."),
    "overloaded": (429, "Busy; try again in a moment."),
    "secret_key": (403, "Credentials change only in the VIKI Agent window."),
}

_Handler = Callable[[AgentGrant, str | None, dict[str, Any]], Coroutine[Any, Any, Response]]


def _refused(code: str) -> AgentChangeError:
    status, message = _REFUSALS[code]
    return AgentChangeError(code, status, message)


def _answer(error: Exception) -> Response:
    """The refusal `error` stands for, with the service's status for its code."""
    extra: dict[str, Any] = {}
    if isinstance(error, AgentChangeError):
        code, status, message = error.code, error.status, error.message
    elif isinstance(error, SettingsError):
        code, status, message = error.code, settings_api._STATUS.get(error.code, 503), error.message
        if error.fields:
            extra["fields"] = error.fields
    elif isinstance(error, RoutineError):
        code, status = error.code, routines_api._STATUS.get(error.code, 400)
        message = "The routine operation could not be completed."
        if error.code == "not_ready":
            extra["blockers"] = list(error.blockers)
    elif isinstance(error, BotError) and error.code in bots_api._STATUS:
        code, status = error.code, bots_api._STATUS[error.code]
        message = "The Bot operation could not be completed."
    else:
        code, status, message = "unavailable", 503, "VIKI Agent couldn't do that just now."
    if status >= 500:
        # Hard rule 8: the code is the whole record, never a key or the request's values.
        logger.warning("agent operation refused: %s", code)
    return json_response({"error": {"code": code, "message": message, **extra}}, status)


def _operation(method: str, path: str) -> tuple[tuple[str, str] | None, str | None]:
    """The `OPS` key a request names, with the routine id a routine action carries."""
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "routines":
        operation, target = (method, f"routines/{{id}}/{parts[2]}"), parts[1]
    else:
        operation, target = (method, path), None
    return (operation, target) if operation in OPS else (None, None)


async def _body(request: Request, keys: frozenset[str]) -> dict[str, Any]:
    """The request's JSON object, which must hold exactly `keys`."""
    data = bytearray()
    try:
        with anyio.fail_after(BODY_TIMEOUT_S):
            async for chunk in request.stream():
                if len(chunk) > MAX_BODY - len(data):
                    raise _refused("too_large")
                data.extend(chunk)
    except TimeoutError:
        raise _refused("intake_timeout") from None
    except ClientDisconnect:
        raise _refused("invalid_input") from None
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    except ValueError, RecursionError:
        raise _refused("invalid_input") from None
    if not isinstance(value, dict) or set(value) != keys:
        raise _refused("invalid_input")
    return value


def _settings_view(effective: EffectiveSettings) -> dict[str, object]:
    """`to_view` without any credential key."""
    view = to_view(effective)
    for field in ("values", "effective", "sources", "keys"):
        section = view[field]
        if isinstance(section, dict):
            view[field] = {key: item for key, item in section.items() if not is_credential_key(key)}
    return view


def agent_routes(
    grants: AgentGrants,
    changes: AgentChanges,
    *,
    settings: SettingsService,
    routines: RoutineService,
    runner: TaskRunner,
    scheduler: Scheduler,
    bots: BotService | None,
    profiles: ProfileService,
    workspaces: WorkspaceService,
    admission_lock: asyncio.Lock,
    app_version: str,
    workers: set[asyncio.Task[Any]],
) -> list[Route]:
    """The agent surface's one route; `bots` is None when the app has no Bots."""
    capacity = anyio.CapacityLimiter(MAX_IN_FLIGHT)

    def status(grant: AgentGrant) -> dict[str, Any]:
        active = workspaces.resolve_active().workspace.workspace_id
        mine = changes.for_session(grant.session_id)
        runs = runner.store.summaries()
        return {
            "chat": {"session_id": grant.session_id},
            "app": {"version": app_version},
            "settings_revision": settings.import_snapshot().revision,
            "workspaces": [
                {
                    "workspace_id": workspace.workspace_id,
                    "name": workspace.name,
                    "policy": workspace.policy,
                    "active": workspace.workspace_id == active,
                }
                for workspace in workspaces.list()
            ],
            "profiles": [
                {
                    "profile_id": profile["profile_id"],
                    "name": profile["identity"]["name"],
                    "current_revision": profile["current_revision"],
                }
                for profile in profiles.list()
            ],
            "routines": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "routine_id",
                            "name",
                            "state",
                            "current_revision",
                            "state_revision",
                        )
                    },
                    "running": runs.get(row["routine_id"], {}).get("active_run") is not None,
                }
                for row in routines.list()
            ],
            "bots": []
            if bots is None
            else [
                {key: bot[key] for key in ("bot_id", "profile_id", "workspace_id")}
                for bot in bots.list()
            ],
            "changes": {
                "pending": [
                    {
                        "change_id": hold["change_id"],
                        "kind": hold["kind"],
                        **{field: hold["summary"][field] for field in _CHAT_VIEW[hold["kind"]]},
                        "expires_in_s": hold["expires_in_s"],
                    }
                    for hold in mine["pending"]
                ],
                "recent": mine["recent"],
            },
        }

    def routine_list() -> dict[str, Any]:
        runs = runner.store.summaries()
        return {
            "routines": [
                {
                    **{
                        key: row[key]
                        for key in (
                            "routine_id",
                            "name",
                            "state",
                            "current_revision",
                            "state_revision",
                            "schedule",
                        )
                    },
                    "last_run": runs.get(row["routine_id"], {}).get("last_run"),
                    "running": runs.get(row["routine_id"], {}).get("active_run") is not None,
                }
                for row in routines.list()
            ]
        }

    def enabling(routine_id: str) -> dict[str, Any]:
        """The Apply card's summary of the routine, once it may be enabled: tested or paused,
        and ready to run unattended."""
        routine = routines.get(routine_id)
        if routine["state"] not in _TRANSITIONS["enable"][0]:
            raise RoutineError("invalid_transition")
        document = routine["document"]
        blockers = routines.readiness(document, scheduled=True)
        if blockers:
            raise RoutineError("not_ready", tuple(blockers))
        profile = profiles.get(document["profile_id"])
        workspace = workspaces.get(document["workspace_id"])
        schedule, prompt = document["schedule"], document["prompt"]
        return {
            "routine_id": routine_id,
            "name": document["name"],
            "revision": routine["current_revision"],
            "schedule": {key: schedule[key] for key in ("kind", "times", "days", "timezone")},
            "profile": {
                "profile_id": document["profile_id"],
                "name": profile["document"]["identity"]["name"],
            },
            "workspace": {"workspace_id": document["workspace_id"], "name": workspace.name},
            "prompt": prompt[:SUMMARY_CHARS],
            "prompt_truncated": len(prompt) > SUMMARY_CHARS,
        }

    async def get_status(grant: AgentGrant, _target: str | None, _body: dict[str, Any]) -> Response:
        return json_response(await run_sync(lambda: status(grant)))

    async def get_settings(
        _grant: AgentGrant, _target: str | None, _body: dict[str, Any]
    ) -> Response:
        return json_response(_settings_view(await run_sync(settings.read)))

    async def set_setting(grant: AgentGrant, _target: str | None, body: dict[str, Any]) -> Response:
        command_id, expected, key, value = (
            body["command_id"],
            body["expected_revision"],
            body["key"],
            body["value"],
        )
        if not isinstance(key, str):
            raise _refused("invalid_input")
        if is_credential_key(key):
            raise _refused("secret_key")
        code = check_change(key, value)
        if code is not None:
            raise SettingsError("invalid_settings", fields={key: code})
        if key in CHANGE_SETTINGS:
            result, replayed = await run_sync(
                lambda: settings.patch(command_id, expected, {key: value})
            )
            if not replayed:
                changes.record(grant.id, grant.session_id, "settings.set", key)
            return json_response({"applied": True, "revision": result.document.revision})
        # Checked as `patch` checks them, so a held change takes the body an applied one does.
        _command_id(command_id)
        if type(expected) is not int or expected < 0:
            raise SettingsError("invalid_settings", fields={"expected_revision": "invalid_type"})
        current = await run_sync(settings.read)
        stored = (current.document.values or {}).get(key)  # None while the key is unset

        async def attempt() -> dict[str, Any]:
            document = await run_sync(settings.import_snapshot)
            if (document.values or {}).get(key) != stored:
                raise AgentChangeError(
                    "stale_revision",
                    409,
                    "The setting changed since the chat asked; review its current value.",
                )
            result, _replayed = await run_sync(
                settings.patch, command_id, document.revision, {key: value}
            )
            return {"revision": result.document.revision}

        async def apply() -> dict[str, Any]:
            # At the revision current at Apply, so a write to another key since the hold does
            # not void it; a write that lands between the read and the patch gets one retry.
            try:
                try:
                    return await attempt()
                except SettingsConflict:
                    return await attempt()
            except SettingsError as error:
                status = settings_api._STATUS.get(error.code, 503)
                raise AgentChangeError(error.code, status, error.message) from None

        # `to` as the store will keep it, so it lines up with the stored `from` character by
        # character (a re-spaced JSON list must not move where the two first differ).
        summary = {
            "key": key,
            "from": current.effective[key],
            "to": _validate_change(key, value),
            "reset": value is None,
        }
        texts = [text if isinstance(text, str) else "" for text in (summary["from"], summary["to"])]
        if max(map(len, texts)) > SUMMARY_CHARS:
            # Both shown from 100 characters before where they first differ, so a change past
            # the cut cannot hide behind an equal start.
            start = max(0, len(os.path.commonprefix(texts)) - 100)
            for field in ("from", "to"):
                shown = summary[field]
                if isinstance(shown, str):
                    summary[field] = shown[start : start + SUMMARY_CHARS]
                    if start:
                        summary[f"{field}_offset"] = start
                    if start + SUMMARY_CHARS < len(shown):
                        summary[f"{field}_truncated"] = True
        pending = changes.hold(
            grant.id, grant.session_id, "settings.set", key, "setting", summary, apply
        )
        return json_response({"pending": pending}, 202)

    async def get_routines(
        _grant: AgentGrant, _target: str | None, _body: dict[str, Any]
    ) -> Response:
        return json_response(await run_sync(routine_list))

    async def add_routine(grant: AgentGrant, _target: str | None, body: dict[str, Any]) -> Response:
        result, created = await run_sync(
            lambda: routines.create(body["command_id"], body["document"])
        )
        if created:
            scheduler.wake()
            changes.record(grant.id, grant.session_id, "routines.add", result["routine_id"])
        return json_response({"routine": result}, 201 if created else 200)

    async def run_routine(grant: AgentGrant, target: str | None, body: dict[str, Any]) -> Response:
        routine_id = _uuid(target)
        attempt, created = await runner.run_now(
            routine_id,
            body["command_id"],
            body["expected_revision"],
            scheduler_ready=lambda: scheduler.state == "running",
        )
        if created:
            scheduler.wake()  # as the window's run route does
            changes.record(grant.id, grant.session_id, "routines.test", routine_id)
        run = {
            "occurrence_id": attempt.occurrence_id,
            "attempt_id": attempt.attempt_id,
            "session_id": attempt.session_id,
        }
        return json_response({"run": run}, 202 if created else 200)

    async def enable_routine(
        grant: AgentGrant, target: str | None, body: dict[str, Any]
    ) -> Response:
        routine_id = _uuid(target)
        command_id, expected = _uuid(body["command_id"]), body["expected_state_revision"]
        if not _integer(expected):
            raise RoutineError("invalid_revision")
        summary = await run_sync(lambda: enabling(routine_id))

        async def apply() -> dict[str, Any]:
            try:
                result, _created = await run_sync(
                    lambda: routines.enable(routine_id, command_id, expected)
                )
            except RoutineError as error:
                status = routines_api._STATUS.get(error.code, 400)
                raise AgentChangeError(error.code, status, "Couldn't enable the routine.") from None
            scheduler.wake()
            return result

        pending = changes.hold(
            grant.id,
            grant.session_id,
            "routines.enable",
            routine_id,
            "routine_enable",
            summary,
            apply,
        )
        return json_response({"pending": pending}, 202)

    async def create_bot(grant: AgentGrant, _target: str | None, body: dict[str, Any]) -> Response:
        if bots is None:
            raise AgentChangeError("unavailable", 503, "Bots aren't available.")
        create = bots.create
        async with admission_lock:
            bot, replayed = await storage_call(lambda: create(**body))
        if not replayed:
            changes.record(grant.id, grant.session_id, "bots.create", bot["bot_id"])
        view = {key: bot[key] for key in ("bot_id", "profile_id", "workspace_id")}
        return json_response({"bot": view}, 200 if replayed else 201)

    handlers: dict[tuple[str, str], _Handler] = {
        ("GET", "status"): get_status,
        ("GET", "settings"): get_settings,
        ("PATCH", "settings"): set_setting,
        ("GET", "routines"): get_routines,
        ("POST", "routines"): add_routine,
        ("POST", "routines/{id}/run"): run_routine,
        ("POST", "routines/{id}/enable"): enable_routine,
        ("POST", "bots"): create_bot,
    }

    async def admitted(request: Request) -> Response:
        authorization = request.headers.getlist("authorization")
        grant = None
        if len(authorization) == 1 and authorization[0].startswith("Bearer "):
            grant = grants.find(request.path_params["grant_id"], authorization[0][7:])
        if grant is None:
            raise _refused("unauthorized")
        if request.headers.getlist("origin"):
            raise _refused("forbidden_origin")
        if request.scope.get("query_string"):
            raise _refused("invalid_input")
        operation, target = _operation(request.method, request.path_params["operation"])
        if operation is None:
            raise _refused("not_found")
        keys = _BODIES.get(operation)
        body = {} if keys is None else await _body(request, keys)
        permit = object()
        try:
            capacity.acquire_on_behalf_of_nowait(permit)
        except anyio.WouldBlock:
            raise _refused("overloaded") from None
        task = asyncio.create_task(handlers[operation](grant, target, body))
        workers.add(task)

        def done(future: asyncio.Task[Response]) -> None:
            workers.discard(future)
            capacity.release_on_behalf_of(permit)
            # A client that left cannot observe the outcome; retrieve it so asyncio reports
            # nothing.
            if not future.cancelled():
                future.exception()

        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def endpoint(request: Request) -> Response:
        try:
            response = await admitted(request)
        except Exception as error:
            response = _answer(error)
        response.headers["Cache-Control"] = "no-store"
        return response

    return [
        Route("/api/agent/{grant_id}/{operation:path}", endpoint, methods=["GET", "POST", "PATCH"])
    ]
