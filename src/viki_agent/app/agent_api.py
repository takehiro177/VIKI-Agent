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
summaries' values (`_CHAT_VIEW`); only the window's Apply list shows those, whole: a held
setting's current and proposed value, a routine's whole prompt. A setting value longer than
`REVIEW_CHARS` gets 422 `too_long_to_review` and is not held. A 5xx logs its code only.

A run under a profile carries that profile's MCP servers and, with a Bot bound to it, the Bot's
identity and recall, so the routine and Bot operations narrow what the window's own routes allow,
each checked before anything is made (`plain_profile`): a chat adds or tests a routine only for a
profile with no MCP servers and no Bot in any workspace, else 403 `profile_not_allowed`; it tests
only a routine its own session added here since launch (`AgentChanges.made`, so a resumed chat
keeps its own), else 403 `not_added_here`; and it creates a Bot only for a profile with no routine
that is not archived, else 409 `profile_has_routines`. Adding a routine and creating a Bot take
one lock, so two chats cannot slip a routine and a Bot onto one profile at once.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
    parse_document,
)
from viki_agent.automation.scheduler import Scheduler
from viki_agent.automation.tasks import TaskRunner
from viki_agent.catalog import _UUID_RE
from viki_agent.conversations.command_runtime import storage_call
from viki_agent.domain import EffectiveSettings
from viki_agent.messaging import bot_usage_api, bots_api
from viki_agent.messaging.bot_usage import BotUsageError
from viki_agent.messaging.bots import BotError, BotService
from viki_agent.profiles.service import ProfileError, ProfileService
from viki_agent.vault_storage import VaultBootstrap
from viki_agent.workspaces import WorkspaceError, WorkspaceService

MAX_BODY = 128 << 10
BODY_TIMEOUT_S = 5.0
MAX_IN_FLIGHT = 4
# The longest setting value an Apply card shows, in characters; a longer one is refused, never
# held cut. No key a hold can name gets near it (the longest, `layout.panes`, takes 8,192), and a
# routine's prompt is at most 32,768 UTF-8 bytes (`routines._prompt`), so a card shows it whole.
REVIEW_CHARS = 64 << 10
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
    ("GET", "usage/{days}"): "read",
    ("GET", "usage/sessions/{id}"): "read",
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
            ("GET", "/api/v2/vaults/{workspace_id}/starter"),
        }
    ),
    ("GET", "settings"): frozenset({("GET", "/api/v2/settings")}),
    ("PATCH", "settings"): frozenset({("PATCH", "/api/v2/settings")}),
    ("GET", "routines"): frozenset({("GET", "/api/v2/routines")}),
    ("POST", "routines"): frozenset({("POST", "/api/v2/routines")}),
    ("POST", "routines/{id}/run"): frozenset({("POST", "/api/v2/routines/{routine_id}/run")}),
    ("POST", "routines/{id}/enable"): frozenset({("POST", "/api/v2/routines/{routine_id}/enable")}),
    ("POST", "bots"): frozenset({("POST", "/api/v2/bots")}),
    ("GET", "usage/{days}"): frozenset({("GET", "/api/v2/bot-usage/summary/{days}")}),
    ("GET", "usage/sessions/{id}"): frozenset({("GET", "/api/v2/bot-usage/sessions/{session_id}")}),
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
    "not_added_here": (403, "A chat can test only a routine it added itself."),
    "profile_not_allowed": (
        403,
        "A chat can't add or test a routine for a profile with MCP servers or a Bot.",
    ),
    "profile_has_routines": (409, "A chat can't create a Bot for a profile that runs routines."),
    "too_long_to_review": (422, "The value is too long to review on the Apply card."),
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
    elif isinstance(error, BotUsageError) and error.code in bot_usage_api._STATUS:
        code, status = error.code, bot_usage_api._STATUS[error.code]
        message = "The Bot usage request could not be completed."
    else:
        code, status, message = "unavailable", 503, "VIKI Agent couldn't do that just now."
    if status >= 500:
        # Hard rule 8: the code is the whole record, never a key or the request's values.
        logger.warning("agent operation refused: %s", code)
    return json_response({"error": {"code": code, "message": message, **extra}}, status)


def _operation(method: str, path: str) -> tuple[tuple[str, str] | None, str | None]:
    """The `OPS` key a request names, with the routine id a routine action, or the day count or
    session id a usage operation, carries."""
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "routines":
        operation, target = (method, f"routines/{{id}}/{parts[2]}"), parts[1]
    elif len(parts) == 3 and parts[0] == "usage" and parts[1] == "sessions":
        operation, target = (method, "usage/sessions/{id}"), parts[2]
    elif len(parts) == 2 and parts[0] == "usage":
        operation, target = (method, "usage/{days}"), parts[1]
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


def _starter_view(plan: dict[str, object]) -> dict[str, object] | None:
    """`bootstrap.starter_plan`'s plan, with every file name, diff, text and proposal left out:
    the vault's starter state and versions, whether an update is available, and how many of its
    `items` each action covers. `None` once the plan itself has nothing to report."""
    if plan["state"] == "none":
        return None
    items = plan["items"]
    counts: dict[str, int] = {}
    if isinstance(items, list):
        for item in items:
            counts[item["action"]] = counts.get(item["action"], 0) + 1
    return {
        "state": plan["state"],
        "from_version": plan["from_version"],
        "to_version": plan["to_version"],
        "notify": plan["notify"],
        "counts": dict(sorted(counts.items())),
    }


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
    usage: bot_usage_api.UsageViews | None = None,
    bootstrap: VaultBootstrap | None = None,
) -> list[Route]:
    """The agent surface's one route; `bots` and `usage` are None when the app has no Bots, and
    `status` reports no vault's starter state when `bootstrap` is None."""
    capacity = anyio.CapacityLimiter(MAX_IN_FLIGHT)
    # Held from the check to the write in `add_routine` and `create_bot`.
    binding = asyncio.Lock()

    def plain_profile(profile_id: str) -> bool:
        """Whether a run under `profile_id` reaches no further than the chat itself: the profile
        names no MCP servers (`tools.mcp_configuration`) and no Bot, in any workspace, is bound
        to it. A profile that is not there names none."""
        try:
            profile = profiles.get(profile_id)
        except ProfileError as error:
            if error.code != "not_found":
                raise
        else:
            if profile["document"]["tools"]["mcp_configuration"] is not None:
                return False
        return bots is None or all(bot["profile_id"] != profile_id for bot in bots.list())

    def runs_routines(profile_id: object) -> bool:
        """Whether any routine that is not archived runs under `profile_id`."""
        return any(
            routines.get(row["routine_id"])["document"]["profile_id"] == profile_id
            for row in routines.list()
            if row["state"] != "archived"
        )

    def status(grant: AgentGrant) -> dict[str, Any]:
        active = workspaces.resolve_active().workspace.workspace_id
        mine = changes.for_session(grant.session_id)
        runs = runner.store.summaries()

        def starter(workspace_id: str) -> dict[str, object] | None:
            if bootstrap is None:
                return None
            try:
                plan = bootstrap.starter_plan(workspace_id)
            except WorkspaceError, OSError:
                return None
            return _starter_view(plan)

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
                    "starter": starter(workspace.workspace_id),
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
        schedule = document["schedule"]
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
            "prompt": document["prompt"],  # whole: the chat wrote it, so the owner reads it all
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

        # Both whole: `from` the current value (a reset discards it), `to` as the store will keep
        # it (NFC, a re-spaced JSON list normalised), so the card shows what Apply writes.
        summary = {
            "key": key,
            "from": current.effective[key],
            "to": _validate_change(key, value),
            "reset": value is None,
        }
        if any(
            isinstance(text, str) and len(text) > REVIEW_CHARS
            for text in (summary["from"], summary["to"])
        ):
            raise _refused("too_long_to_review")
        pending = changes.hold(
            grant.id, grant.session_id, "settings.set", key, "setting", summary, apply
        )
        return json_response({"pending": pending}, 202)

    async def get_routines(
        _grant: AgentGrant, _target: str | None, _body: dict[str, Any]
    ) -> Response:
        return json_response(await run_sync(routine_list))

    async def add_routine(grant: AgentGrant, _target: str | None, body: dict[str, Any]) -> Response:
        command_id, raw = body["command_id"], body["document"]

        def create() -> tuple[dict[str, Any], bool]:
            # Checked as `create` checks them, so a malformed request is refused as before.
            _uuid(command_id)
            if not plain_profile(parse_document(raw)[0]["profile_id"]):
                raise _refused("profile_not_allowed")
            return routines.create(command_id, raw)

        async with binding:
            result, created = await run_sync(create)
        if created:
            scheduler.wake()
            changes.record(grant.id, grant.session_id, "routines.add", result["routine_id"])
        return json_response({"routine": result}, 201 if created else 200)

    async def run_routine(grant: AgentGrant, target: str | None, body: dict[str, Any]) -> Response:
        routine_id = _uuid(target)
        if not changes.made(grant.session_id, "routines.add", routine_id):
            raise _refused("not_added_here")

        def check() -> None:
            # Again at each test: the owner may since have given its profile MCP servers or a Bot.
            if not plain_profile(routines.get(routine_id)["document"]["profile_id"]):
                raise _refused("profile_not_allowed")

        await run_sync(check)
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
        async with binding:
            # A Bot turns its profile's routines, the owner's enabled ones too, into Bot runs.
            if await run_sync(runs_routines, body["profile_id"]):
                raise _refused("profile_has_routines")
            async with admission_lock:
                bot, replayed = await storage_call(lambda: create(**body))
        if not replayed:
            changes.record(grant.id, grant.session_id, "bots.create", bot["bot_id"])
        view = {key: bot[key] for key in ("bot_id", "profile_id", "workspace_id")}
        return json_response({"bot": view}, 200 if replayed else 201)

    async def get_usage_summary(
        _grant: AgentGrant, target: str | None, _body: dict[str, Any]
    ) -> Response:
        if target is None or target not in bot_usage_api._DAYS:
            raise _refused("invalid_input")
        if usage is None:
            raise AgentChangeError("unavailable", 503, "Bot usage isn't available.")
        return json_response(await run_sync(usage.summary, bot_usage_api._DAYS[target]))

    async def get_usage_trace(
        _grant: AgentGrant, target: str | None, _body: dict[str, Any]
    ) -> Response:
        if target is None or not _UUID_RE.match(target):
            raise _refused("invalid_input")
        if usage is None:
            raise AgentChangeError("unavailable", 503, "Bot usage isn't available.")
        return json_response(await run_sync(usage.trace, target))

    handlers: dict[tuple[str, str], _Handler] = {
        ("GET", "status"): get_status,
        ("GET", "settings"): get_settings,
        ("PATCH", "settings"): set_setting,
        ("GET", "routines"): get_routines,
        ("POST", "routines"): add_routine,
        ("POST", "routines/{id}/run"): run_routine,
        ("POST", "routines/{id}/enable"): enable_routine,
        ("POST", "bots"): create_bot,
        ("GET", "usage/{days}"): get_usage_summary,
        ("GET", "usage/sessions/{id}"): get_usage_trace,
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
