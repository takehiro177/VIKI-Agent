"""Bounded, authenticated workspace, vault, policy and folder-picker routes. Nothing here spawns a
provider."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from typing import Any, Literal

import anyio
from anyio.to_thread import run_sync
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.routing import Route

from viki_agent.api_json import failure as _failure
from viki_agent.api_json import json_response as _json
from viki_agent.api_json import unique_object as _unique
from viki_agent.app.diagnostics import _workspace_json as _capabilities_workspace_json
from viki_agent.app.folder_dialog import PickerError, PickerStatus
from viki_agent.domain import SCRATCH_WORKSPACE_ID, Workspace
from viki_agent.protection import ACCOUNT_HINT, ProtectionError
from viki_agent.storage import StateError
from viki_agent.vault_storage import VaultBootstrap
from viki_agent.vaults.workspace_mutation import admits_note_writes
from viki_agent.workspaces import WorkspaceError, WorkspaceService

MAX_WORKSPACE_REQUESTS = 4
MAX_WORKSPACE_BODY_BYTES = 16 << 10
WORKSPACE_BODY_TIMEOUT_S = 5.0
MAX_NAME_CHARS = 256
MAX_PATH_CHARS = 4096
MAX_PICKER_TITLE_CHARS = 120
# The same shape `vault_write_api.py` requires: a pending bootstrap's `pending:<command uuid>`
# never matches, so the key is omitted rather than sent as a value the client's regex refuses.
_PHYSICAL_ID = re.compile(r"[0-9]+:[0-9]+")

logger = logging.getLogger(__name__)

_STATUS = {
    "invalid_path": 400,
    "invalid_name": 400,
    "not_found": 400,
    "not_directory": 400,
    "unsafe_path": 400,
    "invalid_transition": 400,
    "denied_location": 403,
    "protected_overlap": 403,
    "protected_root": 403,
    "unknown_workspace": 404,
    "destination_not_empty": 409,
    "overlaps_registration": 409,
    "case_collision": 409,
    "stale_revision": 409,
    "kind_mismatch": 409,
    "trust_required": 409,
    "workspace_active": 409,
    "acknowledgement_required": 409,
    "root_unavailable": 409,
    "not_app_created": 409,
    "not_removable": 409,
    "workspace_in_use": 409,
    "worktrees_present": 409,
    "unsupported_location": 422,
}
_INVALID_BODY = "The request was malformed."
_PICKER_UNAVAILABLE = "This computer can't show a folder picker. Type the path instead."
_PICKER_FAILED = "The folder picker couldn't open. Type the path instead."
_PROTECTION_MESSAGE = (
    "Couldn't protect the save data. Check the save location's access rights and "
    + ACCOUNT_HINT
    + "."
)


def _identity_json(workspace: Workspace) -> dict[str, object]:
    return {
        "established": workspace.identity.established,
        "git_root_oids": len(workspace.identity.git_root_oids),
        "inherits_from": workspace.identity.inherits_from,
        "probed_at": workspace.identity.probed_at,
    }


def _workspace_json(workspace: Workspace, service: WorkspaceService) -> dict[str, object]:
    current = None if not workspace.persistent else service.current_root(workspace)
    payload: dict[str, object] = {
        "workspace_id": workspace.workspace_id,
        "kind": workspace.kind,
        "root": workspace.root.display,
        "root_available": True if not workspace.persistent else current is not None,
        "aliased": current.aliased if current is not None else workspace.root.aliased,
        "name": workspace.name,
        "policy": workspace.policy,
        "policy_revision": workspace.policy_revision,
        "revision": workspace.revision,
        "source": workspace.source,
        "layout": workspace.layout,
        "portable_id": workspace.portable_id,
        "identity": _identity_json(workspace),
        "face_dir": workspace.face_dir,
        "bootstrap": workspace.bootstrap,
        "persistent": workspace.persistent,
        "created_at": workspace.created_at,
        "updated_at": workspace.updated_at,
    }
    # A pending bootstrap's `pending:<command uuid>` has no physical id yet; the key is
    # omitted rather than sent malformed, so one pending row never breaks the whole listing.
    if _PHYSICAL_ID.fullmatch(workspace.root.physical_id) is not None:
        payload["root_physical_id"] = workspace.root.physical_id
    return payload


def _invalid_body() -> _Refused:
    # A field of the wrong shape is a malformed body, the same refusal a duplicate key gets.
    # The domain codes (`invalid_path`, `stale_revision`, ...) are reserved for a well-formed
    # request the service refused, so the shell never sends the user after a path or a
    # revision that was never the problem.
    return _Refused(_failure("invalid_body", _INVALID_BODY, 400))


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise _invalid_body()
    if not 1 <= len(value) <= limit:
        raise WorkspaceError("invalid_path")
    return value


def _command_id(raw: dict[str, Any]) -> str:
    value = raw.get("command_id")
    if not isinstance(value, str) or len(value) != 36:
        raise _invalid_body()
    return value


def workspace_routes(
    workspaces: WorkspaceService,
    bootstrap: VaultBootstrap,
    *,
    authorized: Callable[[Request], bool],
    origins: frozenset[str],
    workers: set[asyncio.Task[Any]],
    pick: Callable[[str, bool], list[str] | None] | None = None,
    picker_status: Callable[[], PickerStatus] | None = None,
) -> list[Route]:
    # Admission covers intake and blocking work alike, with no queue of waiting requests;
    # the shielded worker owns the permit once storage or the filesystem starts.
    capacity = anyio.CapacityLimiter(MAX_WORKSPACE_REQUESTS)
    # One folder dialog at a time. The pick's worker takes this without waiting and gives it
    # back when the dialog closes, so a dialog whose client has left still refuses a second.
    # An open dialog keeps its worker thread and one of the MAX_WORKSPACE_REQUESTS permits
    # until it closes: the user closes it, or off Windows folder_dialog.PICK_TIMEOUT_S does.
    # This lock bounds that to one permit; every other pick is refused at once as busy.
    picking = threading.Lock()

    async def endpoint(request: Request) -> Response:
        if not authorized(request):
            return _failure("unauthorized", "Can't authenticate. Open VIKI Agent again.", 401)
        origin = request.headers.get("origin")
        if (origin is not None and origin not in origins) or (
            request.method in {"POST", "PATCH"} and origin not in origins
        ):
            return _failure("forbidden_origin", "This window can't do that.", 403)
        permit = object()
        delegated = False
        try:
            capacity.acquire_on_behalf_of_nowait(permit)
        except anyio.WouldBlock:
            return _failure("overloaded", "Busy; try again in a moment.", 429)

        async def run(call: Callable[[], Any]) -> Any:
            nonlocal delegated
            worker = asyncio.create_task(run_sync(call))
            workers.add(worker)
            delegated = True

            def done(task: asyncio.Task[Any]) -> None:
                workers.discard(task)
                capacity.release_on_behalf_of(permit)
                # A disconnected caller cannot observe an exception; retrieve it without
                # logging content. A later authenticated GET recovers the committed state.
                if not task.cancelled():
                    task.exception()

            worker.add_done_callback(done)
            return await asyncio.shield(worker)

        try:
            return await perform(request, run)
        except WorkspaceError as exc:
            status = _STATUS.get(exc.code, 503)
            body: dict[str, object] = {"code": exc.code, "message": exc.message}
            body.update(exc.extra)
            return _json({"error": body}, status)
        except StateError as exc:
            logger.warning("workspace route refused: %s", exc.code)
            # A CHECK or UNIQUE refusal means a value this layer admitted does not fit the
            # store's contract: that is the caller's input, not an unavailable store.
            return _failure(exc.code, exc.message, 400 if exc.code == "invalid_input" else 503)
        except ProtectionError:
            # The exception text is a fixed message today; this route states its own anyway,
            # so no native or path-bearing string can ever reach the browser through it.
            logger.warning("workspace route refused: protection")
            return _failure("unavailable", _PROTECTION_MESSAGE, 503)
        except ClientDisconnect:
            return _failure("disconnected", "The request was interrupted.", 400)
        finally:
            if not delegated:
                capacity.release_on_behalf_of(permit)

    async def body(request: Request, keys: set[str], optional: set[str]) -> dict[str, Any]:
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise _Refused(_failure("invalid_representation", "Send the request as JSON.", 415))
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise _Refused(
                _failure("invalid_representation", "Send the request uncompressed.", 415)
            )
        raw_length = request.headers.get("content-length")
        if raw_length is not None and (
            not raw_length.isascii()
            or not raw_length.isdecimal()
            or len(raw_length) > 10
            or int(raw_length) > MAX_WORKSPACE_BODY_BYTES
        ):
            raise _Refused(_failure("too_large", "The request is too large.", 413))
        buffer = bytearray()
        try:
            with anyio.fail_after(WORKSPACE_BODY_TIMEOUT_S):
                async for chunk in request.stream():
                    if len(chunk) > MAX_WORKSPACE_BODY_BYTES - len(buffer):
                        raise _Refused(_failure("too_large", "The request is too large.", 413))
                    buffer.extend(chunk)
        except TimeoutError:
            raise _Refused(
                _failure("intake_timeout", "The request did not arrive in time.", 408)
            ) from None
        try:
            parsed = json.loads(buffer.decode("utf-8"), object_pairs_hook=_unique)
        except ValueError, RecursionError:
            raise _Refused(_failure("invalid_body", _INVALID_BODY, 400)) from None
        if not isinstance(parsed, dict) or not keys <= set(parsed) <= keys | optional:
            raise _Refused(_failure("invalid_body", _INVALID_BODY, 400))
        return parsed

    async def perform(
        request: Request, run: Callable[[Callable[[], Any]], Awaitable[Any]]
    ) -> Response:
        try:
            return await route(request, run)
        except _Refused as refusal:
            return refusal.response

    async def route(
        request: Request, run: Callable[[Callable[[], Any]], Awaitable[Any]]
    ) -> Response:
        path, method = request.scope["path"], request.method
        if path.endswith("/folders/pick"):
            if method in {"GET", "HEAD"}:
                return await run(lambda: _json(asdict(_picker_status())))
            raw = await body(request, {"multiple", "title"}, set())
            return await run(lambda: _pick(raw))
        # Only the routes whose template carries {workspace_id} reach the branches below it.
        workspace_id = str(request.path_params.get("workspace_id", ""))
        if method in {"GET", "HEAD"}:
            if path.endswith("/suggestion"):
                return await run(lambda: _suggestion(request))
            if not workspace_id:
                return await run(_listing)
            if path.endswith("/starter"):
                return await run(lambda: _json(bootstrap.starter_plan(workspace_id)))
            if path.endswith("/policy"):
                return await run(lambda: _json(asdict(workspaces.inspect_policy(workspace_id))))
            return await run(
                lambda: _json(_workspace_json(workspaces.get(workspace_id, kind=None), workspaces))
            )
        if path.endswith("/select"):
            raw = await body(request, {"command_id", "workspace_id"}, set())
            return await run(lambda: _select(raw))
        if path.endswith("/create"):
            raw = await body(
                request,
                {"command_id", "name"},
                {"parent", "folder", "portable_metadata", "template"},
            )
            return await run(lambda: _create(raw))
        if path.endswith("/open"):
            raw = await body(request, {"command_id", "path"}, set())
            return await run(lambda: _open(raw))
        if path.endswith("/workspaces/code"):
            raw = await body(request, {"command_id", "path"}, set())
            return await run(lambda: _open(raw, kind="code"))
        if path.endswith("/cancel-bootstrap"):
            raw = await body(request, {"command_id"}, set())
            return await run(lambda: _cancel(raw, workspace_id))
        if path.endswith("/remove"):
            raw = await body(request, {"command_id"}, set())
            return await run(lambda: _remove(raw, workspace_id))
        raw = await body(
            request, {"command_id", "expected_policy_revision", "policy", "acknowledged"}, set()
        )
        return await run(lambda: _set_policy(raw, workspace_id))

    def _listing() -> Response:
        active = workspaces.resolve_active()
        return _json(
            {
                "workspaces": [
                    _workspace_json(workspace, workspaces)
                    for workspace in workspaces.list(kind=None)
                ],
                "active_id": active.workspace.workspace_id,
                # The launch keeps the workspace it resolved; the selection is read fresh so a
                # choice made in this window shows at once with `restart_required`.
                "selected_id": workspaces.selected_id(),
                "selected_by": active.selected_by,
                "chat_only": active.chat_only,
                "scratch": {
                    "workspace_id": SCRATCH_WORKSPACE_ID,
                    "kind": "scratch",
                    "shared": True,
                },
            }
        )

    def _suggestion(request: Request) -> Response:
        query = request.query_params
        if set(query) - {"name"} or len(request.scope.get("query_string", b"")) > 1024:
            raise WorkspaceError("invalid_name")
        parent, folder = bootstrap.suggestion(query.get("name", ""))
        return _json({"parent": str(parent), "folder": folder, "path": str(parent / folder)})

    def _select(raw: dict[str, Any]) -> Response:
        command_id = _command_id(raw)
        target = raw["workspace_id"]
        if target is not None and not isinstance(target, str):
            raise _invalid_body()
        if target is not None and len(target) != 36:
            raise WorkspaceError("unknown_workspace")
        workspaces.select(command_id, target)
        # What the store now holds, never what the caller asked for; and the same restart rule
        # `/api/v2/capabilities` applies to it, so re-selecting the running workspace says no.
        active = replace(workspaces.resolve_active(), selected_id=workspaces.selected_id())
        capabilities = _capabilities_workspace_json(active, root_available=True)
        return _json(
            {
                "selected_id": active.selected_id,
                "active_id": active.workspace.workspace_id,
                "restart_required": capabilities["restart_required"],
            }
        )

    def _create(raw: dict[str, Any]) -> Response:
        command_id = _command_id(raw)
        name = raw["name"]
        if not isinstance(name, str):
            raise _invalid_body()
        if not 1 <= len(name) <= MAX_NAME_CHARS:
            raise WorkspaceError("invalid_name")
        parent = raw.get("parent")
        if parent is not None:
            parent = _text(parent, MAX_PATH_CHARS)
        folder = raw.get("folder")
        if folder is not None and not isinstance(folder, str):
            raise _invalid_body()
        portable = raw.get("portable_metadata", False)
        if not isinstance(portable, bool):
            raise _invalid_body()
        template = raw.get("template", "blank")
        if template not in ("blank", "viki"):
            raise _invalid_body()
        workspace, created, files = bootstrap.create(
            command_id, name, parent, folder, "en", portable, template=template
        )
        return _json(
            {"workspace": _workspace_json(workspace, workspaces), "created_files": files},
            201 if created else 200,
        )

    def _open(raw: dict[str, Any], kind: Literal["vault", "code"] = "vault") -> Response:
        command_id = _command_id(raw)
        workspace, created = workspaces.register_existing(
            command_id, _text(raw["path"], MAX_PATH_CHARS), kind=kind
        )
        return _json(
            {
                "workspace": _workspace_json(workspace, workspaces),
                "inspection": asdict(workspaces.inspect_policy(workspace.workspace_id)),
            },
            201 if created else 200,
        )

    def _cancel(raw: dict[str, Any], workspace_id: str) -> Response:
        bootstrap.cancel(_command_id(raw), workspace_id)
        return _json({"workspace_id": workspace_id, "cancelled": True})

    def _remove(raw: dict[str, Any], workspace_id: str) -> Response:
        _command_id(raw)
        workspaces.remove(workspace_id)
        return _json({"workspace_id": workspace_id, "removed": True})

    def _picker_status() -> PickerStatus:
        if picker_status is None:
            return PickerStatus(False, False, "unsupported")
        return picker_status()

    def _pick(raw: dict[str, Any]) -> Response:
        multiple, title = raw["multiple"], raw["title"]
        if (
            not isinstance(multiple, bool)
            or not isinstance(title, str)
            or not 1 <= len(title) <= MAX_PICKER_TITLE_CHARS
            or any(unicodedata.category(ch) == "Cc" for ch in title)
            or title.startswith("-")
        ):
            raise _invalid_body()
        if pick is None or not _picker_status().available:
            raise _Refused(_failure("picker_unavailable", _PICKER_UNAVAILABLE, 503))
        if not picking.acquire(blocking=False):
            raise _Refused(_failure("picker_busy", "A folder picker is already open.", 409))
        try:
            paths = pick(title, multiple)
        except PickerError as exc:
            logger.warning("folder picker failed: %s", exc.code)
            raise _Refused(_failure("picker_failed", _PICKER_FAILED, 503)) from None
        finally:
            picking.release()
        # The paths only: registering a folder stays with `vaults/open` and `workspaces/code`.
        return _json({"paths": paths or []})

    def _set_policy(raw: dict[str, Any], workspace_id: str) -> Response:
        command_id = _command_id(raw)
        expected = raw["expected_policy_revision"]
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise _invalid_body()
        policy = raw["policy"]
        if policy not in ("ordinary", "existing_readonly", "protected"):
            raise _invalid_body()
        acknowledged = raw["acknowledged"]
        if not isinstance(acknowledged, list) or not all(
            isinstance(item, str) and len(item) <= 64 for item in acknowledged
        ):
            raise _invalid_body()
        workspace = workspaces.set_policy(command_id, workspace_id, expected, policy, acknowledged)
        return _json(
            {
                "policy": workspace.policy,
                "policy_revision": workspace.policy_revision,
                "revision": workspace.revision,
                "note_write": admits_note_writes(workspace),
            }
        )

    return [
        Route("/api/v2/workspaces", endpoint, methods=["GET"]),
        Route("/api/v2/workspaces/select", endpoint, methods=["POST"]),
        Route("/api/v2/workspaces/code", endpoint, methods=["POST"]),
        Route("/api/v2/workspaces/{workspace_id}", endpoint, methods=["GET"]),
        Route("/api/v2/workspaces/{workspace_id}/remove", endpoint, methods=["POST"]),
        Route("/api/v2/vaults/suggestion", endpoint, methods=["GET"]),
        Route("/api/v2/vaults/create", endpoint, methods=["POST"]),
        Route("/api/v2/vaults/open", endpoint, methods=["POST"]),
        Route("/api/v2/vaults/{workspace_id}/cancel-bootstrap", endpoint, methods=["POST"]),
        Route("/api/v2/vaults/{workspace_id}/starter", endpoint, methods=["GET"]),
        Route("/api/v2/vaults/{workspace_id}/policy", endpoint, methods=["GET", "PATCH"]),
        Route("/api/v2/folders/pick", endpoint, methods=["GET", "POST"]),
    ]


class _Refused(Exception):
    """One fixed response decided during intake, raised so `perform` returns it unchanged."""

    def __init__(self, response: Response) -> None:
        self.response = response
        super().__init__(response.status_code)
