"""Prepare visible profile drafts and revalidate their immutable launch binding."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, replace
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from viki_agent import protection
from viki_agent.app.settings import SettingsService
from viki_agent.conversations.drafts import (
    MAX_DRAFT_KEYS,
    MAX_NONEMPTY_DRAFTS,
    MAX_SCOPE_BYTES,
    decode_draft,
    draft_scope,
    require_private_store,
)
from viki_agent.domain import ProviderReadiness, Workspace
from viki_agent.drivers.base import StartFacts
from viki_agent.llm.connections import PREFIX, ConnectionError, Connections
from viki_agent.messaging import bot_memory_rule
from viki_agent.messaging.bot_memory import app_memory_directory, memory_ref, write_settings
from viki_agent.profiles.mcp_config import (
    McpConfigError,
    canonical_servers,
    config_bytes,
    config_path,
    write_config,
)
from viki_agent.profiles.profile_capabilities import (
    ADAPTER_VERSION,
    FIXTURE_VERSION,
    START_CHECK_EVIDENCE,
    UNREVIEWED,
    baseline,
    evidence_version,
)
from viki_agent.profiles.profile_document import evaluate_profile, parse_profile_document
from viki_agent.profiles.provider_catalogs import ProviderCatalogService, evaluation_catalog
from viki_agent.profiles.service import (
    ProfileError,
    ProfileService,
    _avatar_ref,
    _decode,
    _id,
    _one,
    _revision,
)
from viki_agent.state import Prefs
from viki_agent.storage import MAX_SESSION_METADATA
from viki_agent.turn_input import TurnInput
from viki_agent.workspaces import WorkspaceError, WorkspaceService

MAX_BINDINGS = 4096
MAX_TITLES = MAX_SESSION_METADATA
# native_initialized's refusals when Claude Code's start-up report does not confirm a launch.
START_CHECK_CODES = frozenset(
    {"start_unconfirmed", "plan_mode_unconfirmed", "model_unconfirmed", "mcp_config_unconfirmed"}
)
LAUNCH_ORIGINS = ("unknown", "interactive", "routine")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _connection_refusal(error: ConnectionError) -> str:
    """The profile refusal for a pinned connection that does not resolve: read-only settings
    are an unreadable store, and every other failure but a missing ref is an invalid one."""
    return {
        "connection_missing": "connection_missing",
        "connections_unavailable": "connections_unavailable",
        "read_only": "connections_unavailable",
    }.get(error.code, "connection_invalid")


def _require_replay(
    prior: dict[str, Any], request_hash: str, session_id: str, launch_origin: str
) -> None:
    """An exact retry returns its committed binding, origin included: it never promotes an
    unknown origin, and a different known origin is another command."""
    if (
        prior["request_hash"] != request_hash
        or prior["session_id"] != session_id
        or (
            "unknown" not in (launch_origin, prior["launch_origin"])
            and launch_origin != prior["launch_origin"]
        )
    ):
        raise ProfileError("command_conflict")


def _reviewed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`rows` without the UNREVIEWED ones."""
    return [row for row in rows if row["id"] not in UNREVIEWED]


class ProfileLaunchService:
    def __init__(
        self,
        profiles: ProfileService,
        settings: SettingsService,
        workspaces: WorkspaceService,
        catalogs: ProviderCatalogService,
        readiness: Callable[[], ProviderReadiness],
        vault: Path,
        transcript_exists: Callable[[str], bool],
        *,
        connections: Connections,
        route_can_start: Callable[[], bool],
    ) -> None:
        self.profiles, self.settings, self.workspaces = profiles, settings, workspaces
        self.catalogs, self.readiness = catalogs, readiness
        self.store, self.scope = profiles.store, draft_scope(vault)
        self.transcript_exists = transcript_exists
        self.connections, self.route_can_start = connections, route_can_start
        self.mcp_directory: Path | None = None
        self.bot_settings_directory: Path | None = None
        self._recall: Callable[[str, str, str], str] | None = None

    def bind_mcp_directory(self, directory: Path) -> None:
        """Name the folder that holds generated `--mcp-config` files (`<data>/mcp`)."""
        self.mcp_directory = directory

    def bind_bot_settings_directory(self, directory: Path) -> None:
        self.bot_settings_directory = directory

    def _workspace(self, workspace_id: str) -> Workspace:
        active = self.workspaces.resolve_active()
        if workspace_id != active.workspace.workspace_id:
            raise ProfileError("workspace_restart_required")
        # The first consumer binds registered vaults. Scratch has no durable policy row.
        try:
            workspace = self.workspaces.get(workspace_id)
        except WorkspaceError:
            raise ProfileError("workspace_unavailable") from None
        if not workspace.persistent or workspace.kind != "vault":
            raise ProfileError("workspace_unavailable")
        root = self.workspaces.current_root(workspace)
        if root is None or root.physical_id != active.workspace.root.physical_id:
            raise ProfileError("workspace_unavailable")
        return replace(workspace, root=root)

    def _catalog(self, *, connection_route: bool = False) -> dict[str, Any]:
        readiness = self.readiness()
        if connection_route:
            if not self._route_ready(readiness):
                raise ProfileError("provider_unavailable")
        elif not readiness.can_start:
            raise ProfileError("provider_unavailable")
        # Every newer 2.x shares one label, so its catalog and model menu outlive an update.
        label = evidence_version(readiness.version)
        catalog = self.catalogs.latest("claude", ADAPTER_VERSION, label)
        # Static or start-check evidence can support inherited settings without a discovery
        # session. It adds no model values; only successful initialization supplies those. A
        # catalog an earlier build stored is re-observed with this build's rows, keeping its models.
        models = catalog["models"] if catalog else []
        rows = baseline(readiness.version, models_observed=bool(models))
        if catalog is None or catalog["capabilities"] != rows:
            # A full store makes room by evicting the oldest unbound catalog, then a bound one
            # of another label, then the oldest bound one of this label, whose sessions answer
            # `capability_changed` at their next start. So `capacity` remains only for an
            # observation over its size bound, and the stored rows are still no fallback.
            catalog = self.catalogs.observe("claude", ADAPTER_VERSION, label, rows, models)
        return catalog

    def _route_ready(self, readiness: ProviderReadiness) -> bool:
        checked_version = re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", readiness.version)
        return (
            self.route_can_start()
            and readiness.cli == "ready"
            and checked_version is not None
            and tuple(int(part) for part in readiness.version.split("."))
            >= tuple(int(part) for part in FIXTURE_VERSION.split("."))
        )

    def _evaluation(
        self,
        document: dict[str, Any],
        catalog: dict[str, Any] | None,
        workspace: Workspace,
        prefs: Prefs,
        bot_id: str | None = None,
    ) -> dict[str, Any]:
        evaluation = evaluate_profile(
            parse_profile_document(document),
            evaluation_catalog(catalog) if catalog is not None else None,
        )
        fields = [
            {item.name: _plain(getattr(field, item.name)) for item in dataclass_fields(field)}
            for field in evaluation.fields
        ]
        for field in fields:
            if (
                field["field"] == "runtime.model"
                and field["reason"] == "connection_resolution_required"
            ):
                if bot_id is None:
                    field.update(status="unsupported", reason="connection_bot_only")
                else:
                    try:
                        self.connections.resolve(document["runtime"]["model"])
                    except ConnectionError as error:
                        field.update(status="unsupported", reason=_connection_refusal(error))
                    else:
                        field.update(status="supported", reason=None)
            if field["field"] == "workspace.allowed_workspace_ids":
                allowed = document["workspace"]["allowed_workspace_ids"]
                permitted = not allowed or workspace.workspace_id in allowed
                field.update(
                    status="supported" if permitted else "unsupported",
                    reason=None if permitted else "workspace_not_allowed",
                )
            # App MCP servers run only in an ordinary workspace; protected and read-only
            # workspaces never launch them.
            if (
                field["field"] == "tools.mcp_configuration"
                and field["status"] == "supported"
                and field["source"] == "profile"
                and workspace.policy != "ordinary"
            ):
                field.update(status="unsupported", reason="mcp_policy_blocked")
            key = {"runtime.model": "model", "runtime.permission_mode": "mode"}.get(field["field"])
            if key and field["source"] == "inherited":
                field["effective_value"] = getattr(prefs, key)
        blockers = [field["field"] for field in fields if field["status"] != "supported"]
        return {"fields": fields, "blockers": blockers, "launchable": not blockers}

    @staticmethod
    def _launch_document(document: dict[str, Any], prefs: Prefs) -> dict[str, Any]:
        model = document["runtime"]["model"]
        if isinstance(model, str) and model.startswith(PREFIX) and prefs.model != model:
            return {**document, "runtime": {**document["runtime"], "model": None}}
        return document

    @staticmethod
    def _spec(
        profile_id: str,
        revision: int,
        document: dict[str, Any],
        prefs: Prefs,
        workspace: Workspace,
        settings_revision: int,
        catalog: dict[str, Any],
        bot_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "profile_id": profile_id,
            "profile_revision": revision,
            "document_hash": _hash(document),
            "prefs": asdict(prefs),
            "workspace_id": workspace.workspace_id,
            "workspace_revision": workspace.revision,
            "physical_id": workspace.root.physical_id,
            "policy": workspace.policy,
            "policy_revision": workspace.policy_revision,
            "settings_revision": settings_revision,
            "runtime": document["runtime"]["provider"],
            "capability_key": catalog["catalog_key"],
            "adapter_version": catalog["adapter_version"],
            "native_version": catalog["native_version"],
            **({"bot_id": bot_id} if bot_id is not None else {}),
        }

    def prepare(
        self,
        profile_id: str,
        command_id: str,
        session_id: str,
        expected_profile_revision: int,
        workspace_id: str,
        bot_id: str | None = None,
        *,
        launch_origin: Literal["unknown", "interactive", "routine"] = "unknown",
    ) -> dict[str, Any]:
        """Commit a new profile session's binding and draft, or return an exact retry's.

        `launch_origin` comes only from the server's own control flow, never a request body:
        `interactive` for a chat the user prepares, `routine` for a routine run. It is stamped
        in the transaction that creates the binding, and no method here changes it after; a
        code copy needs `interactive` (`_code_copy`).
        """
        profile_id, command_id, session_id, workspace_id = map(
            _id, (profile_id, command_id, session_id, workspace_id)
        )
        revision = _revision(expected_profile_revision)
        if launch_origin not in LAUNCH_ORIGINS:
            raise ProfileError("invalid_input")
        if bot_id is not None:
            bot_id = _id(bot_id)
        request_hash = _hash(
            [profile_id, command_id, session_id, revision, workspace_id]
            + ([bot_id] if bot_id is not None else [])
        )
        require_private_store(self.store)
        # An exact retry uses its committed snapshot, even after a new profile revision is saved.
        with self.store._connection() as connection:
            prior = _one(
                connection,
                "SELECT * FROM session_profile_bindings WHERE command_id=?",
                (command_id,),
            )
        if prior:
            _require_replay(prior, request_hash, session_id, launch_origin)
            return self.inspect_session(session_id)
        if self.transcript_exists(session_id):
            raise ProfileError("session_exists")
        profile = self.profiles.get(profile_id, revision)
        if profile["archived"] or profile["current_revision"] != revision:
            raise ProfileError("conflict")
        document = profile["document"]
        settings = self.settings.read()
        if settings.document.read_only:
            raise ProfileError("settings_unavailable")
        runtime = document["runtime"]
        with self.store._connection() as connection:
            routine = (
                connection.execute(
                    "SELECT 1 FROM task_attempts WHERE session_id=?", (session_id,)
                ).fetchone()
                is not None
            )
            if bot_id is not None:
                owner = connection.execute(
                    "SELECT profile_id,workspace_id FROM bots WHERE bot_id=?", (bot_id,)
                ).fetchone()
                if owner != (profile_id, workspace_id):
                    raise ProfileError("conflict")
        connection_ref = isinstance(runtime["model"], str) and runtime["model"].startswith(PREFIX)
        launch_document = document
        if routine and connection_ref:
            launch_document = {**document, "runtime": {**runtime, "model": None}}
        prefs = Prefs(
            mode=runtime["permission_mode"] or str(settings.effective["chat.default_mode"]),
            effort=runtime["effort"],
            model=launch_document["runtime"]["model"]
            or self.settings.native_chat_model(
                cast(str | None, settings.effective["chat.default_model"])
            ),
        )
        workspace = self._workspace(workspace_id)
        resolved_route = False
        if connection_ref and bot_id is not None and not routine:
            try:
                self.connections.resolve(runtime["model"])
            except ConnectionError:
                pass  # _evaluation returns the specific, path-free refusal.
            else:
                resolved_route = True
        if connection_ref and not routine and not resolved_route and not self.readiness().can_start:
            evaluation = self._evaluation(launch_document, None, workspace, prefs, bot_id)
            return {**evaluation, "profile_id": profile_id, "profile_revision": revision}
        catalog = self._catalog(connection_route=True) if resolved_route else self._catalog()
        evaluation = self._evaluation(launch_document, catalog, workspace, prefs, bot_id)
        if not evaluation["launchable"]:
            return {**evaluation, "profile_id": profile_id, "profile_revision": revision}
        spec = self._spec(
            profile_id,
            revision,
            document,
            prefs,
            workspace,
            settings.document.revision,
            catalog,
            bot_id,
        )
        # A Bot-bound session's instructions are in every spawn's system prompt instead
        # (bot_system_prompt), so its draft starts empty.
        text = document["instructions"] if bot_id is None else ""
        turn = TurnInput(text)
        cipher = (
            protection.protect(
                turn.encode(),
                context=f"VIKI draft v2\0{self.scope}\0{session_id}\0{1}".encode("ascii"),
            )
            if text
            else None
        )
        now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if self.transcript_exists(session_id):
            raise ProfileError("session_exists")
        with self.store._connection(write=True) as connection:
            if bot_id is not None:
                owner = connection.execute(
                    "SELECT profile_id,workspace_id FROM bots WHERE bot_id=?", (bot_id,)
                ).fetchone()
                if owner != (profile_id, workspace_id):
                    raise ProfileError("conflict")
            prior = _one(
                connection,
                "SELECT * FROM session_profile_bindings WHERE command_id=?",
                (command_id,),
            )
            if prior:
                _require_replay(prior, request_hash, session_id, launch_origin)
            else:
                if connection.execute(
                    "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
                ).fetchone() or (
                    # A routine's session is never an interactive one.
                    launch_origin == "interactive"
                    and connection.execute(
                        "SELECT 1 FROM task_attempts WHERE session_id=?", (session_id,)
                    ).fetchone()
                ):
                    raise ProfileError("conflict")
                current = connection.execute(
                    "SELECT current_revision,archived FROM profiles WHERE profile_id=?",
                    (profile_id,),
                ).fetchone()
                saved = _one(
                    connection,
                    "SELECT * FROM profile_revisions WHERE profile_id=? AND revision=?",
                    (profile_id, revision),
                )
                effective_revision = connection.execute("SELECT revision FROM settings").fetchone()
                current_workspace = _one(
                    connection, "SELECT * FROM workspaces WHERE workspace_id=?", (workspace_id,)
                )
                saved_catalog = connection.execute(
                    "SELECT 1 FROM provider_catalogs WHERE catalog_key=?",
                    (catalog["catalog_key"],),
                ).fetchone()
                if (
                    current != (revision, 0)
                    or saved is None
                    or _decode(saved) != document
                    or (effective_revision[0] if effective_revision else 0)
                    != settings.document.revision
                    or current_workspace is None
                    or any(
                        current_workspace[key] != value
                        for key, value in {
                            "revision": workspace.revision,
                            "physical_id": workspace.root.physical_id,
                            "policy": workspace.policy,
                            "policy_revision": workspace.policy_revision,
                        }.items()
                    )
                    or saved_catalog is None
                ):
                    raise ProfileError("conflict")
                if (
                    connection.execute("SELECT count(*) FROM session_profile_bindings").fetchone()[
                        0
                    ]
                    >= MAX_BINDINGS
                ):
                    raise ProfileError("capacity")
                if (
                    connection.execute(
                        "SELECT count(*) FROM session_metadata WHERE scope=?", (self.scope,)
                    ).fetchone()[0]
                    >= MAX_TITLES
                ):
                    raise ProfileError("capacity")
                if cipher:
                    counts = connection.execute(
                        "SELECT count(*),coalesce(sum(text_bytes>0),0),"
                        "coalesce(sum(text_bytes),0) FROM drafts WHERE scope=?",
                        (self.scope,),
                    ).fetchone()
                    if (
                        counts[0] >= MAX_DRAFT_KEYS
                        or counts[1] >= MAX_NONEMPTY_DRAFTS
                        or counts[2] + turn.text_bytes > MAX_SCOPE_BYTES
                    ):
                        raise ProfileError("capacity")
                connection.execute(
                    "INSERT INTO sessions(session_id,mode,effort,model,origin) "
                    "VALUES (?,?,?,?,'profile')",
                    (session_id, prefs.mode, prefs.effort, prefs.model),
                )
                connection.execute(
                    "INSERT INTO session_profile_bindings "
                    "(session_id,command_id,request_hash,profile_id,profile_revision,workspace_id,"
                    "workspace_revision,policy,policy_revision,settings_revision,runtime,"
                    "capability_key,launch_spec_hash,created_at,launch_origin) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        session_id,
                        command_id,
                        request_hash,
                        profile_id,
                        revision,
                        workspace_id,
                        workspace.revision,
                        workspace.policy,
                        workspace.policy_revision,
                        settings.document.revision,
                        runtime["provider"],
                        catalog["catalog_key"],
                        _hash(spec),
                        now,
                        launch_origin,
                    ),
                )
                if bot_id is not None:
                    connection.execute(
                        "INSERT INTO session_bot_memory(session_id,bot_id) VALUES (?,?)",
                        (session_id, bot_id),
                    )
                connection.execute(
                    "INSERT INTO session_metadata "
                    "(scope,session_id,revision,title,pinned,archived) VALUES (?,?,1,?,0,0)",
                    (self.scope, session_id, document["identity"]["name"]),
                )
                if cipher:
                    connection.execute(
                        "INSERT INTO drafts(scope,key,revision,body,format,text_bytes,saved_at) "
                        "VALUES (?,?,1,?,2,?,?)",
                        (self.scope, session_id, cipher, turn.text_bytes, now),
                    )
        return self.inspect_session(session_id)

    def inspect_session(self, session_id: str) -> dict[str, Any]:
        binding, document, prefs = self._binding(session_id)
        workspace = self._workspace(binding["workspace_id"])
        catalog = self.catalogs.get(binding["capability_key"])
        # A full store may evict a bound catalog, one of another native version first. With
        # nothing to evaluate against, the checks below supply the blocker (validate_launch's
        # capability_changed), so the review says what a start would answer.
        evaluation = (
            {"fields": [], "blockers": [], "launchable": False}
            if catalog is None
            else self._evaluation(
                self._launch_document(document, prefs),
                catalog,
                workspace,
                prefs,
                binding["bot_id"],
            )
        )
        warnings: list[str] = []
        # The revocation check runs on its own, so another blocker never hides a revoked
        # server and its preview.
        for check in (
            lambda: self.validate_launch(session_id, prefs),
            lambda: self._require_unrevoked(
                binding["profile_id"], document["tools"]["mcp_configuration"]
            ),
        ):
            try:
                check()
            except ProfileError as error:
                if error.code not in warnings:  # the client refuses a repeated code
                    warnings.append(error.code)
        try:
            code_copy = self._code_copy(binding, workspace)
        except ProfileError as error:
            code_copy = None
            if error.code not in warnings:
                warnings.append(error.code)
        if warnings:
            evaluation = {
                **evaluation,
                "launchable": False,
                "blockers": list(dict.fromkeys([*evaluation["blockers"], *warnings])),
            }
        record = self.store.get_draft(self.scope, session_id)
        draft = decode_draft(self.scope, session_id, record) if record else None
        with self.store._connection() as connection:
            avatar_asset_id = _avatar_ref(
                connection, binding["profile_id"], binding["profile_revision"], document
            )
        identity = document["identity"]
        return {
            **evaluation,
            "session_id": session_id,
            "profile_id": binding["profile_id"],
            "profile_revision": binding["profile_revision"],
            "prefs": asdict(prefs),
            "workspace_id": binding["workspace_id"],
            "workspace_revision": binding["workspace_revision"],
            "policy": binding["policy"],
            "policy_revision": binding["policy_revision"],
            "settings_revision": binding["settings_revision"],
            "runtime": binding["runtime"],
            "capability_key": binding["capability_key"],
            "started": bool(binding["native_started"]),
            "identity": {
                "name": identity["name"],
                "avatar_asset_id": avatar_asset_id,
                "accent": identity["accent"],
                "shape": identity["shape"],
                "profile_revision": binding["profile_revision"],
            },
            "warnings": warnings,
            "draft": {
                "key": session_id,
                "revision": draft.revision if draft else 0,
                "text": draft.text if draft else "",
                "saved_at": draft.saved_at if draft else None,
                "attachments": [item.data() for item in draft.attachments] if draft else [],
            },
            "mcp_config": None if "mcp_revoked" in warnings else self._mcp_preview(document),
            **self._bot_memory_preview(binding),
            **({"code_copy": code_copy} if code_copy is not None else {}),
        }

    def _bot_memory_preview(self, binding: dict[str, Any]) -> dict[str, Any]:
        """The Bot memory folder a start names: a vault ref for a session prepared in an
        ordinary vault, else the absolute folder under the app's data folder (none while the
        settings folder is unbound). `routine` says the chat is one of the Bot's routine runs,
        which carry the same binding as its task threads."""
        bot_id = binding["bot_id"]
        if bot_id is None:
            return {}
        routine = binding["routine"]
        if binding["policy"] == "ordinary":
            return {"bot_memory": {"bot_id": bot_id, "ref": memory_ref(bot_id), "routine": routine}}
        if self.bot_settings_directory is None:
            return {}
        data = self.bot_settings_directory.parent
        path = app_memory_directory(data, binding["workspace_id"], bot_id)
        return {"bot_memory": {"bot_id": bot_id, "path": str(path), "routine": routine}}

    def _code_copy(
        self, binding: dict[str, Any], workspace: Workspace
    ) -> dict[str, str | None] | None:
        """The code copy this main chat or task thread of a granted Bot works in: its folder,
        and its branch once its first send made it (None before). None when it gets no copy:
        an origin other than interactive or a retained routine attempt, a Bot-thread seat,
        no grant, a vault or folder that is not ordinary, a chat that started without one, a
        released copy, or a bound copy that is not ready or not of the granted folder. A copy
        bound under other provenance or to another Bot raises `launch_changed`: its start
        refuses (`BotCode.resolve`) rather than fall back."""
        session_id, bot_id = binding["session_id"], binding["bot_id"]
        with self.store._connection() as connection:
            copies = connection.execute(
                "SELECT c.state,c.bot_id,c.workspace_id,w.state,w.branch FROM bot_code_copies c "
                "LEFT JOIN worktrees w ON w.worktree_id=c.worktree_id WHERE c.session_id=?",
                (session_id,),
            ).fetchall()
            routine = connection.execute(
                "SELECT 1 FROM task_attempts WHERE session_id=?", (session_id,)
            ).fetchone()
            seat = connection.execute(
                "SELECT 1 FROM bot_thread_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            grant = connection.execute(
                "SELECT workspace_id FROM bot_code_folders WHERE bot_id=?", (bot_id,)
            ).fetchone()
        interactive = binding["launch_origin"] == "interactive" and routine is None
        bound = [row for row in copies if row[0] == "bound"]
        if bound and (not interactive or bot_id is None or bound[0][1] != bot_id):
            raise ProfileError("launch_changed")
        if (
            not interactive
            or bot_id is None
            or seat is not None
            or grant is None
            or binding["policy"] != "ordinary"
            or workspace.policy != "ordinary"
        ):
            return None
        if bound:
            _state, _bot, folder_id, worktree_state, branch = bound[0]
            if folder_id != grant[0] or worktree_state != "ready":
                return None
        elif copies or binding["native_started"]:
            return None  # only a first send makes a copy, and a released one is not replaced
        else:
            folder_id, branch = grant[0], None
        try:
            folder = self.workspaces.get(folder_id, kind="code")
        except WorkspaceError:
            return None
        if folder.policy != "ordinary" or not folder.persistent:
            return None
        return {"workspace_id": folder_id, "name": folder.name, "branch": branch}

    def _mcp_preview(self, document: dict[str, Any]) -> dict[str, str] | None:
        """The exact file and argv element a start would pass; nothing is written."""
        pinned = document["tools"]["mcp_configuration"]
        if pinned is None or self.mcp_directory is None:
            return None
        try:
            data = config_bytes(pinned)
        except McpConfigError:
            return None
        return {
            "json": data.decode("ascii"),
            "argv_element": f"--mcp-config={config_path(self.mcp_directory, data)}",
        }

    def _require_unrevoked(self, profile_id: str, pinned: object) -> None:
        """Raise `mcp_revoked` when a pinned server is revoked; writes nothing.

        A server is revoked when the profile is archived or its current revision no longer
        holds that exact server (any change to it, its note included): the pinned revision
        cannot bring back a server the user has since removed or edited.
        """
        if pinned is None:
            return
        current = self.profiles.get(profile_id)
        try:
            allowed = set(canonical_servers(current["document"]["tools"]["mcp_configuration"]))
        except McpConfigError:
            allowed = set()  # no conforming servers in the current revision
        if current["archived"] or not allowed:
            raise ProfileError("mcp_revoked")
        try:
            servers = set(canonical_servers(pinned))
        except McpConfigError:
            return  # a pinned value outside the schema is the caller's to refuse
        if not servers <= allowed:
            raise ProfileError("mcp_revoked")

    def mcp_config_path(self, session_id: str) -> str | None:
        """Write the pinned revision's MCP config file and return its absolute path, or None
        when the pinned revision lists no servers. Runs after the launch guard, on every spawn,
        and refuses a revoked server (`_require_unrevoked`).
        """
        binding, document, _prefs = self._binding(session_id)
        pinned = document["tools"]["mcp_configuration"]
        if pinned is None:
            return None
        if self.mcp_directory is None:
            raise ProfileError("mcp_config_unavailable")
        self._require_unrevoked(binding["profile_id"], pinned)
        try:
            data = config_bytes(pinned)
        except McpConfigError:
            raise ProfileError("mcp_config_unavailable") from None
        try:
            return str(write_config(self.mcp_directory, data))
        except OSError, protection.ProtectionError:
            raise ProfileError("mcp_config_unavailable") from None

    def _binding(self, session_id: str) -> tuple[dict[str, Any], dict[str, Any], Prefs]:
        require_private_store(self.store)
        with self.store._connection() as connection:
            session = _one(
                connection, "SELECT * FROM sessions WHERE session_id=?", (_id(session_id),)
            )
            binding = _one(
                connection,
                "SELECT * FROM session_profile_bindings WHERE session_id=?",
                (session_id,),
            )
            if session is None or session["origin"] != "profile" or binding is None:
                raise ProfileError("corrupt")
            row = _one(
                connection,
                "SELECT * FROM profile_revisions WHERE profile_id=? AND revision=?",
                (binding["profile_id"], binding["profile_revision"]),
            )
            if row is None:
                raise ProfileError("corrupt")
            owner = connection.execute(
                "SELECT bot_id FROM session_bot_memory WHERE session_id=?", (session_id,)
            ).fetchone()
            binding["bot_id"] = owner[0] if owner else None
            # The same test the Bot's thread listing applies, so a client past that listing's
            # cap can still tell a routine run, which carries the same memory binding, from a
            # task thread.
            binding["routine"] = binding["launch_origin"] == "routine" or (
                connection.execute(
                    "SELECT 1 FROM task_attempts WHERE session_id=? LIMIT 1", (session_id,)
                ).fetchone()
                is not None
            )
        document = _decode(row)
        if binding["runtime"] != document["runtime"]["provider"] or binding[
            "request_hash"
        ] != _hash(
            [
                _id(binding["profile_id"]),
                _id(binding["command_id"]),
                session_id,
                _revision(binding["profile_revision"]),
                _id(binding["workspace_id"]),
            ]
            + ([binding["bot_id"]] if binding["bot_id"] is not None else [])
        ):
            raise ProfileError("corrupt")
        return binding, document, Prefs(session["mode"], session["effort"], session["model"])

    def bot_memory_settings_path(self, session_id: str) -> str | None:
        binding, _document, _prefs = self._binding(session_id)
        if binding["bot_id"] is None:
            return None
        workspace = self._bot_workspace(binding)
        directory = self.bot_settings_directory
        if directory is None:
            raise ProfileError("launch_changed")
        # The session runs in the vault at every policy, so every file names the vault's
        # instruction files for Claude Code to skip. Outside an ordinary vault a workspace id puts
        # the memory folder under the app's data folder, the settings folder's parent, never in
        # the vault.
        workspace_id = None if binding["policy"] == "ordinary" else binding["workspace_id"]
        try:
            return write_settings(
                directory, Path(workspace.root.display), binding["bot_id"], workspace_id
            )
        except OSError, ValueError, protection.ProtectionError:
            raise ProfileError("bot_memory_unavailable") from None

    def bot_system_prompt(self, session_id: str) -> str | None:
        """The text every spawn of this Bot session, resume included, appends to Claude Code's
        system prompt (ADR-084): the pinned revision's instructions, then the memory rule and the
        Bot rules, as every Bot-bound session gets its Bot's memory folder and skips the vault's
        instruction files (bot_memory_settings_path). None without a Bot. Sent on resume too:
        Claude Code reuses a session's recorded system prompt only until compaction. A Bot
        session binds only a FIXTURE_VERSION or start-checked catalog, as its required
        `runtime.effort` needs `effort_selection` evidence."""
        binding, document, _prefs = self._binding(session_id)
        if binding["bot_id"] is None:
            return None
        text = bot_memory_rule.bot_system_prompt(
            document["instructions"],
            kanban=bot_memory_rule.kanban_skill_present(
                Path(self._bot_workspace(binding).root.display)
            ),
        )
        # The approved memory this Bot recalls (ADR-087), as data after the rules; none until
        # the server binds recall, and none when the vault's records can't be read now.
        if self._recall is not None:
            recalled = self._recall(binding["workspace_id"], binding["bot_id"], session_id)
            if recalled:
                text = f"{text}\n\n{recalled}"
        return text

    def bind_recall(self, recall: Callable[[str, str, str], str]) -> None:
        """Install the recall `bot_system_prompt` appends: (workspace id, Bot id, session id)
        to its tagged blocks, or ""."""
        self._recall = recall

    def _bot_workspace(self, binding: dict[str, Any]) -> Workspace:
        workspace = self._workspace(binding["workspace_id"])
        self._require_bot(binding)
        if (
            workspace.revision != binding["workspace_revision"]
            or workspace.policy != binding["policy"]
            or workspace.policy_revision != binding["policy_revision"]
        ):
            raise ProfileError("launch_changed")
        return workspace

    def _require_bot(self, binding: dict[str, Any]) -> None:
        if binding["bot_id"] is None:
            return
        with self.store._connection() as connection:
            owner = connection.execute(
                "SELECT profile_id,workspace_id FROM bots WHERE bot_id=?", (binding["bot_id"],)
            ).fetchone()
        if owner != (binding["profile_id"], binding["workspace_id"]):
            raise ProfileError("launch_changed")

    def bot_for(self, profile_id: str, workspace_id: str) -> str | None:
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT bot_id FROM bots WHERE profile_id=? AND workspace_id=?",
                (_id(profile_id), _id(workspace_id)),
            ).fetchone()
        return row[0] if row else None

    def _require_unchanged(
        self,
        binding: dict[str, Any],
        document: dict[str, Any],
        prefs: Prefs,
        workspace: Workspace,
        catalog: dict[str, Any],
    ) -> None:
        spec = self._spec(
            binding["profile_id"],
            binding["profile_revision"],
            document,
            prefs,
            workspace,
            binding["settings_revision"],
            catalog,
            binding["bot_id"],
        )
        if _hash(spec) != binding["launch_spec_hash"] or any(
            spec[key] != binding[key] for key in ("workspace_revision", "policy", "policy_revision")
        ):
            raise ProfileError("launch_changed")

    def validate_launch(self, session_id: str, prefs: Prefs) -> None:
        binding, document, stored_prefs = self._binding(session_id)
        self._require_bot(binding)
        runtime_model = document["runtime"]["model"]
        connection_ref = isinstance(runtime_model, str) and runtime_model.startswith(PREFIX)
        if not connection_ref and prefs.model and prefs.model.startswith(PREFIX):
            raise ProfileError("launch_blocked")
        if prefs != stored_prefs:
            raise ProfileError("launch_changed")
        launch_document = self._launch_document(document, stored_prefs)
        connection_route = connection_ref and launch_document is document
        if binding["native_started"] == 0 and self.transcript_exists(session_id):
            raise ProfileError("session_exists")
        workspace, readiness = self._workspace(binding["workspace_id"]), self.readiness()
        catalog = self.catalogs.get(binding["capability_key"])
        if not (self._route_ready(readiness) if connection_route else readiness.can_start):
            raise ProfileError("provider_unavailable")
        # The binding holds across CLI updates and model-menu observations only while this
        # version's evidence label and rows are the ones the session was reviewed with, the
        # UNREVIEWED rows aside: no review reads them, so a change to one keeps the binding.
        if (
            catalog is None
            or (catalog["provider"], catalog["adapter_version"], catalog["native_version"])
            != ("claude", ADAPTER_VERSION, evidence_version(readiness.version))
            or _reviewed(catalog["capabilities"])
            != _reviewed(baseline(readiness.version, models_observed=bool(catalog["models"])))
        ):
            raise ProfileError("capability_changed")
        self._require_unchanged(binding, document, prefs, workspace, catalog)
        if connection_route:
            with self.store._connection() as connection:
                routine = connection.execute(
                    "SELECT 1 FROM task_attempts WHERE session_id=?", (session_id,)
                ).fetchone()
            if binding["bot_id"] is None or routine is not None:
                raise ProfileError("launch_blocked")
            try:
                self.connections.resolve(runtime_model)
            except ConnectionError as error:
                raise ProfileError(_connection_refusal(error)) from None
        if not self._evaluation(launch_document, catalog, workspace, prefs, binding["bot_id"])[
            "launchable"
        ]:
            raise ProfileError("launch_blocked")

    def native_initialized(self, session_id: str, facts: StartFacts | None = None) -> None:
        """Mandatory post-initialize checkpoint before any profile text can be submitted.

        `facts` is what Claude Code itself reported when this process started. Every refusal
        raises before `native_started` is set: `capability_changed` for a missing bound catalog
        or a reported version of another evidence class than the bound catalog's; for
        start-checked rows, one of START_CHECK_CODES for a missing or unconfirming report; and,
        whenever `facts` is given, `model_unconfirmed` for a pinned native model or a routine's
        native preference absent from its menu, for a static binding too, which otherwise needs
        no report at all. Connection refs do not require a native-menu entry.
        """
        binding, document, prefs = self._binding(session_id)
        if binding["bot_id"] is not None:
            self._bot_workspace(binding)
        catalog = self.catalogs.get(binding["capability_key"])
        if catalog is None:
            raise ProfileError("capability_changed")
        if (
            facts is not None
            and facts.version is not None
            and evidence_version(facts.version) != catalog["native_version"]
        ):
            raise ProfileError("capability_changed")
        self._require_unchanged(
            binding, document, prefs, self._workspace(binding["workspace_id"]), catalog
        )
        launch_document = self._launch_document(document, prefs)
        model = prefs.model if launch_document is not document else document["runtime"]["model"]
        if isinstance(model, str) and model.startswith(PREFIX):
            model = None
        if any(row["evidence"] == START_CHECK_EVIDENCE for row in catalog["capabilities"]):
            self._require_confirmed(launch_document, prefs, facts, model=model)
        elif facts is not None and model is not None and model not in facts.models:
            # A static binding needs no report, but baseline()'s model_selection row only needs
            # a non-empty menu, never the pinned model's presence in it: check that here too.
            raise ProfileError("model_unconfirmed")
        with self.store._connection(write=True) as connection:
            changed = connection.execute(
                "UPDATE session_profile_bindings SET native_started=1 WHERE session_id=? "
                "AND native_started IN (0,1) AND EXISTS "
                "(SELECT 1 FROM sessions WHERE session_id=? AND origin='profile')",
                (session_id, session_id),
            ).rowcount
            if changed != 1:
                raise ProfileError("corrupt")

    @staticmethod
    def _require_confirmed(
        document: dict[str, Any], prefs: Prefs, facts: StartFacts | None, *, model: str | None
    ) -> None:
        """Raise one of START_CHECK_CODES unless `facts` confirm the bound launch: its plan
        mode, its native model and every pinned app server (`viki_<name>`)."""
        if facts is None:
            raise ProfileError("start_unconfirmed")
        if prefs.mode == "plan" and facts.mode != "plan":
            raise ProfileError("plan_mode_unconfirmed")
        if model is not None and model not in facts.models:
            raise ProfileError("model_unconfirmed")
        pinned = document["tools"]["mcp_configuration"]
        if pinned is None:
            return
        try:
            # The server names in the generated --mcp-config file this process was given.
            names = set(json.loads(config_bytes(pinned))["mcpServers"])
        except McpConfigError:
            raise ProfileError("mcp_config_unconfirmed") from None
        if facts.mcp_servers is None or not names <= set(facts.mcp_servers):
            raise ProfileError("mcp_config_unconfirmed")

    def reopenable(self, session_id: str, exists: bool) -> None:
        binding, _document, _prefs = self._binding(session_id)
        if bool(binding["native_started"]) != exists:
            raise ProfileError("session_exists" if exists else "transcript_missing")
