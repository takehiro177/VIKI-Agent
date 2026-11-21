"""Authenticated local HTTP APIs and the generation-scoped v2 session socket.

Host and Origin are admitted before a socket is accepted. Its first frame must carry the
launch token and protocol version within five seconds. Tokens, prompts and frame bodies
never reach logs. JSON uses ASCII escaping, including unpaired Unicode surrogates.

One bounded writer preserves each connection's emission order. Recovery captures replay or a
bounded snapshot, subscribes, then acknowledges synchronization without an intervening await.
Session events identify actor generation and subscription request; history has its own request
correlation. Mutations require the current actor generation and a command ID. Approval choices
also bind the captured runtime, request and turn. No disconnected mutation queue is replayed.
Socket receipts describe local invocation only; durable turn acceptance is the separate
command_api.py / command_runtime.py service, not a guarantee of native pickup.

Connection slots include authentication. Per-connection and application task limits bound
admitted work; overflow or a failed/slow writer forces resynchronization. A history page the
catalog could not produce — busy, unreadable, missing under a session that should have a
transcript, or past its serialized response budget — returns a correlated error instead of
triggering a reconnect loop; a page read from a transcript's newest window carries its events
and says which budget cut it short. Disconnect detaches subscriptions; admitted mutations retain
application ownership. Shutdown closes command admission, cancels and joins owned socket work,
closes actors and drains storage.

Bearer-authenticated catalog and rails reads run off-loop, and the catalog routes answer with
the listing's or the search's own observation of itself: what it could not read is counted, not
silently dropped. A search query is length-bounded and its correlation id validated before any
file is opened. Draft and command APIs enforce their own bounded intake and Origin policy.
Static assets are public. OS openers accept only validated
web/mail links, in-vault files or catalog-derived transcripts.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import hashlib
import json
import logging
import secrets
import sys
import unicodedata
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

import anyio
from anyio.to_thread import run_sync
from starlette.applications import Starlette
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import BaseRoute, Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketDisconnect

from viki_agent import events, protection, rails, winproc
from viki_agent.api_json import unique_object
from viki_agent.app import folder_dialog
from viki_agent.app.agent_api import agent_routes
from viki_agent.app.agent_changes import AgentChanges
from viki_agent.app.agent_grants import AgentGrants
from viki_agent.app.data_api import LocationsService, ResetService, data_routes
from viki_agent.app.diagnostics import ProviderChecks, Readiness
from viki_agent.app.diagnostics_api import diagnostics_routes
from viki_agent.app.exports import ExportService
from viki_agent.app.imports import ImportService
from viki_agent.app.provider_login import provider_login_routes
from viki_agent.app.settings import SettingsService
from viki_agent.app.settings_api import settings_routes
from viki_agent.app.workspace_api import workspace_routes
from viki_agent.automation.folder_changes import notes_changed
from viki_agent.automation.kanban import card_waiting
from viki_agent.automation.routines import RoutineService
from viki_agent.automation.routines_api import routine_routes
from viki_agent.automation.scheduler import Clock, Scheduler, SystemClock
from viki_agent.automation.seat_host import SeatHost
from viki_agent.automation.tasks import TaskRunner, TaskStore
from viki_agent.catalog import QUERY_MAX_CHARS, Catalog, HistoryPage, project_dir
from viki_agent.code.bot_code import BotCode
from viki_agent.code.check_runs import CheckRunner
from viki_agent.code.checks_api import check_routes
from viki_agent.code.code_api import code_routes, code_save_routes
from viki_agent.code.code_checkout import GitSettings
from viki_agent.code.code_checks import CheckService
from viki_agent.code.code_commit import CodeCommitter
from viki_agent.code.code_files import CodeFiles
from viki_agent.code.code_git import HARDENING, CodeGit
from viki_agent.code.code_integration import CodeIntegration
from viki_agent.code.code_review import CodeReview
from viki_agent.code.code_save import CodeSaver
from viki_agent.code.integration_api import integration_routes
from viki_agent.code.preview_windows import PreviewWindows
from viki_agent.code.previews import PreviewService
from viki_agent.code.previews_api import preview_routes
from viki_agent.code.roots_api import root_routes
from viki_agent.code.session_roots import RootError, SessionRoots
from viki_agent.code.terminal_launch import terminal_launch_routes
from viki_agent.code.worktrees import SWEEP_INTERVAL_S, WorktreeService, worktree_area
from viki_agent.config import Config
from viki_agent.conversations.approval_api import approval_routes
from viki_agent.conversations.approval_runtime import ApprovalDispatchRejected, ApprovalRunner
from viki_agent.conversations.approvals import (
    ApprovalError,
    ApprovalService,
    ChoiceInput,
    NativeRequest,
    PermissionChoice,
    QuestionChoice,
)
from viki_agent.conversations.artifact_details import ArtifactDetails
from viki_agent.conversations.between_chats import between_chats
from viki_agent.conversations.between_chats_api import between_chats_routes
from viki_agent.conversations.chat_archives import ConversationArchiveService
from viki_agent.conversations.chat_archives_api import conversation_archive_routes
from viki_agent.conversations.command_api import command_routes
from viki_agent.conversations.command_runtime import CommandRunner, storage_call
from viki_agent.conversations.commands import CommandService
from viki_agent.conversations.context_api import context_routes
from viki_agent.conversations.conversation_search import ConversationSearch
from viki_agent.conversations.conversation_search_api import routes as conversation_search_routes
from viki_agent.conversations.draft_api import draft_routes
from viki_agent.conversations.drafts import DraftService, draft_scope, require_private_store
from viki_agent.conversations.inventory_api import inventory_routes
from viki_agent.conversations.limits_api import PlanLimits, limits_routes
from viki_agent.conversations.mcp_api import mcp_routes
from viki_agent.conversations.runs_api import run_routes
from viki_agent.conversations.session_metadata import SessionMetadataService
from viki_agent.conversations.session_metadata_api import session_metadata_routes
from viki_agent.conversations.session_prefs_api import session_prefs_routes
from viki_agent.conversations.session_windows_api import session_window_routes
from viki_agent.conversations.subagents_api import subagent_routes
from viki_agent.conversations.tool_sources import ToolSources
from viki_agent.decision_loop import detectors
from viki_agent.decision_loop.agenda import DecisionAgenda
from viki_agent.decision_loop.api import decision_loop_routes
from viki_agent.decision_loop.app_look import AppLookReader
from viki_agent.decision_loop.check_host import SavedCheckHost
from viki_agent.decision_loop.code_look import CodeLookReader
from viki_agent.decision_loop.contracts import (
    DecisionLoopError,
    DecisionSettings,
    ModelPort,
    parse_settings,
)
from viki_agent.decision_loop.engine import DecisionLoopEngine
from viki_agent.decision_loop.inbox_host import SuggestionHost
from viki_agent.decision_loop.key_store import DecisionKeyStore
from viki_agent.decision_loop.missions import MissionService
from viki_agent.decision_loop.model import DecisionModel, TypeSafeHttp
from viki_agent.decision_loop.presence import PresenceTracker
from viki_agent.decision_loop.service import DecisionLoopService
from viki_agent.decision_loop.stats import DecisionLoopStats
from viki_agent.decision_loop.store import DecisionLoopStore
from viki_agent.decision_loop.vault_look import VaultLookReader
from viki_agent.domain import Capabilities, StorageReadiness, Workspace
from viki_agent.drivers.base import StartError, StartFacts
from viki_agent.drivers.claude.control import EFFORTS, PERMISSION_MODES
from viki_agent.events import OtherSession, SessionStarted, VaultSessions
from viki_agent.llm.api import connection_routes
from viki_agent.llm.connections import Connections
from viki_agent.llm.gateway import Gateway
from viki_agent.llm.magpie import Magpie
from viki_agent.llm.magpie_api import magpie_routes
from viki_agent.media.assets import AssetService, collect_scopes
from viki_agent.media.assets_api import asset_routes
from viki_agent.media.avatars import AvatarService
from viki_agent.media.avatars_api import avatar_routes
from viki_agent.messaging.bot_memory_api import BotMemoryArea, bot_memory_routes
from viki_agent.messaging.bot_runtime import BotRuntime
from viki_agent.messaging.bot_threads import BotThreadService
from viki_agent.messaging.bot_usage import BotUsage
from viki_agent.messaging.bot_usage_api import UsageViews, bot_usage_routes, turn_end_capture
from viki_agent.messaging.bot_usage_report import Names
from viki_agent.messaging.bots import BotError, BotService
from viki_agent.messaging.bots_api import bot_routes
from viki_agent.messaging.discord_channel import DiscordChannel
from viki_agent.messaging.mattermost_bot import MattermostBot, RemoteBot
from viki_agent.messaging.mattermost_bot import MattermostBot as _MattermostBridge
from viki_agent.messaging.mattermost_config import MattermostConfig
from viki_agent.messaging.mattermost_http import MattermostHttp
from viki_agent.messaging.messaging_api import messaging_routes, messaging_settings_routes
from viki_agent.messaging.messaging_channel import Provider
from viki_agent.messaging.messaging_config import DiscordConfig, SlackConfig
from viki_agent.messaging.messaging_runtime import BridgeFactory, MessagingError, MessagingRuntime
from viki_agent.messaging.messaging_store import Config as MessagingConfig
from viki_agent.messaging.messaging_store import MessagingConfigStore, MessagingStoreError, Saved
from viki_agent.messaging.slack_channel import SlackChannel
from viki_agent.profiles.mcp_config import sweep_mcp_directory
from viki_agent.profiles.native_assets import NativeAssetInspector, claude_user_root
from viki_agent.profiles.native_assets_api import native_asset_routes
from viki_agent.profiles.profile_capabilities import (
    ADAPTER_VERSION,
    baseline,
    evidence_version,
    observed_models,
)
from viki_agent.profiles.profile_launch import START_CHECK_CODES, ProfileLaunchService
from viki_agent.profiles.profiles_api import profile_routes
from viki_agent.profiles.provider_catalogs import ProviderCatalogService
from viki_agent.profiles.service import ProfileError, ProfileService
from viki_agent.protection import ProtectionError
from viki_agent.session import (
    CommandDispatchRejected,
    Envelope,
    SessionActor,
    SessionManager,
    _finish_cleanup,
)
from viki_agent.state import MAX_MODEL_BYTES, MODEL_UNSPECIFIED, Prefs
from viki_agent.storage import SessionStore, StateError, check_file
from viki_agent.vault_storage import VaultBootstrap
from viki_agent.vaults.capture_api import capture_routes
from viki_agent.vaults.capture_suggest import CaptureSuggester
from viki_agent.vaults.captures import CaptureService
from viki_agent.vaults.decision_api import decision_routes
from viki_agent.vaults.decisions import DecisionService
from viki_agent.vaults.memory import MemoryService
from viki_agent.vaults.proposal_api import proposal_routes
from viki_agent.vaults.proposals import ProposalService
from viki_agent.vaults.skill_proposal_api import skill_proposal_routes
from viki_agent.vaults.skill_proposals import SkillProposalService
from viki_agent.vaults.vault import VaultService
from viki_agent.vaults.vault_api import vault_routes
from viki_agent.vaults.vault_attachments import VaultAttachmentService
from viki_agent.vaults.vault_backup import VaultBackupService
from viki_agent.vaults.vault_bookmark_api import bookmark_routes
from viki_agent.vaults.vault_bookmarks import BookmarkService
from viki_agent.vaults.vault_knowledge_api import vault_knowledge_routes
from viki_agent.vaults.vault_lifecycle import VaultLifecycleService
from viki_agent.vaults.vault_mutation import MutationCoordinator, MutationError
from viki_agent.vaults.vault_operations import OperationJournal, RecoveryStore
from viki_agent.vaults.vault_ops_api import vault_ops_routes
from viki_agent.vaults.vault_publication import PublicationJournal, VaultPublicationService
from viki_agent.vaults.vault_restore import BackupRestoreService
from viki_agent.vaults.vault_write_api import vault_write_routes
from viki_agent.vaults.workspace_mutation import WorkspaceMutationResolver, admits_note_writes
from viki_agent.voice.voice_api import voice_routes
from viki_agent.voice.voice_engine import WORKER as VOICE_WORKER
from viki_agent.voice.voice_pack_service import VoicePack
from viki_agent.voice.voice_service import VoiceService
from viki_agent.voice.voicebox import Voicebox
from viki_agent.window import Spawn, WindowManager
from viki_agent.window_host import WindowHost
from viki_agent.workspaces import WorkspaceError, WorkspaceService

# The four data services `data_routes` needs, in its own argument order.
DataServices = tuple[LocationsService, ExportService, ImportService, ResetService]

ALLOWED_HOSTS = frozenset({"127.0.0.1:8765", "localhost:8765"})
ORIGINS = frozenset({"http://127.0.0.1:8765", "http://localhost:8765"})
DEV_ORIGINS = frozenset({"http://127.0.0.1:5173", "http://localhost:5173"})
CSP = (
    "default-src 'self'; script-src 'self'; img-src 'self' data: blob:; connect-src 'self'; "
    "font-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
# The approval refusals that mean the record itself could not be written. The socket
# receipt names them all `storage_unavailable`, the reason the browser already reads as "nothing
# was saved, so nothing was handed over". Every other refusal reports its own code, including
# the `StateError` codes (`busy`, `incompatible`, `recovery_required`) that reach this seam
# through ApprovalService._access: they name a definite refusal that recorded nothing, and
# folding them into a storage reason would say less than the ledger knows.
STORAGE_REFUSALS = frozenset({"protection", "corrupt", "unsafe_path", "unavailable", "internal"})
UNAUTHORIZED = 4401  # WebSocket close code for a missing or wrong token; the client gives up
POLICY_VIOLATION = 1008  # WebSocket close code for a foreign Host/Origin, refused before accept
AUTH_TIMEOUT_S = 5.0
VAULT_POLL_S = 5.0
ASSET_POLL_S = 3600.0
WS_MAX_SIZE = 1 << 20  # bytes; given to uvicorn as ws_max_size
PROTOCOL_MISMATCH = 4406
RESYNC_REQUIRED = 4410
MAX_SOCKET_CONNECTIONS = 8
MAX_SOCKET_COMMANDS = 32
# In-flight `session.open` and `history.more` tasks per socket, checked against this alone and
# left out of MAX_SOCKET_COMMANDS and MAX_APP_SOCKET_COMMANDS: on each connect a window
# re-subscribes every chat it tracks at once (`socket.ts` `dial`) and asks each chat with no
# history yet for its first page as it syncs (`state/viki.ts`), and `chat.max_live_sessions`
# goes up to 999.
MAX_SOCKET_OPENS = 1024
MAX_APP_SOCKET_COMMANDS = 64
MAX_SOCKET_RECEIPTS = 1024
MAX_OUTBOUND_BYTES = 4 << 20
MAX_HISTORY_BYTES = 2 << 20
SOCKET_WRITE_TIMEOUT_S = 5.0
# Every bounded vault-lease wait: the vault clients abort at 15 s, so a lease refused at
# 10 s reaches the user as a typed `vault_locked`, not a timeout over a write that may land.
VAULT_LEASE_WAIT_S = 10.0

logger = logging.getLogger(__name__)


# --- security middleware ---------------------------------------------------------------------


class SecurityMiddleware:
    """Refuses a foreign Host (HTTP 400, WebSocket closed before accept) and a foreign
    WebSocket Origin; adds SECURITY_HEADERS and Cache-Control: no-store to admitted HTTP
    responses. Plain ASGI so it also sees the websocket scope; BaseHTTPMiddleware does not.
    """

    def __init__(self, app: ASGIApp, *, dev: bool) -> None:
        self.app = app
        self.origins = ORIGINS | (DEV_ORIGINS if dev else frozenset[str]())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if headers.get("host", "") not in ALLOWED_HOSTS:
            await self._refuse(scope, receive, send)
            return
        if scope["type"] == "websocket":
            if headers.get("origin") not in self.origins:
                await send({"type": "websocket.close", "code": POLICY_VIOLATION, "reason": ""})
                return
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(raw=message["headers"])
                for key, value in SECURITY_HEADERS.items():
                    out[key] = value
                out["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": POLICY_VIOLATION, "reason": ""})
        else:
            response = PlainTextResponse(
                "Bad Host header", status_code=400, headers=SECURITY_HEADERS
            )
            await response(scope, receive, send)


async def _server_error(_request: Request, _exc: Exception) -> Response:
    """Starlette's 500 comes from ServerErrorMiddleware, outside SecurityMiddleware, so it
    carries the security headers itself."""
    headers = {**SECURITY_HEADERS, "Cache-Control": "no-store"}
    return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)


# --- client message validation ----------------------------------------------------------------

_UUID_FIELDS = frozenset(
    {
        "session_id",
        "fork_id",
        "before_uuid",
        "generation",
        "request_id",
        "command_id",
        "turn_id",
        "workspace_id",
    }
)


def _is_canonical_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return str(parsed) == value


def _valid_field(name: str, value: Any) -> bool:
    if name in _UUID_FIELDS:
        return _is_canonical_uuid(value)
    if name == "v":
        return type(value) is int and value == events.VERSION
    if name == "native_request":
        return (
            isinstance(value, dict)
            and set(value) == {"runtime_id", "request_id", "turn_id"}
            and all(_is_canonical_uuid(v) for v in value.values())
        )
    if name == "after_seq":
        return type(value) is int and 0 <= value <= (1 << 53) - 1
    if name == "line":
        return type(value) is int and 1 <= value <= 9_999_999
    if name in {"allow", "existing_only"}:
        return isinstance(value, bool)
    if name == "expected_model":
        if value is None:
            return True
        if not isinstance(value, str) or not value:
            return False
        try:
            return len(value.encode("utf-8")) <= MAX_MODEL_BYTES
        except UnicodeError:
            return False
    if name == "answers":
        return isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        )
    return isinstance(value, str)


def _validate(raw: Any) -> tuple[str, dict[str, Any]] | None:
    """A structurally valid client message: a JSON object whose `type` is a known
    events.CLIENT_MESSAGES key and whose required (and present optional) fields have the right
    shape. Extra, unlisted keys are ignored."""
    if not isinstance(raw, dict):
        return None
    type_ = raw.get("type")
    if not isinstance(type_, str) or type_ not in events.CLIENT_MESSAGES:
        return None
    allowed = {"type", *(name.removeprefix("?") for name in events.CLIENT_MESSAGES[type_])}
    if not set(raw) <= allowed:
        return None
    for spec in events.CLIENT_MESSAGES[type_]:
        optional = spec.startswith("?")
        name = spec[1:] if optional else spec
        if name not in raw:
            if optional:
                continue
            return None
        if not _valid_field(name, raw[name]):
            return None
    return type_, raw


def _log_dropped(raw: Any) -> None:
    type_ = raw.get("type") if isinstance(raw, dict) else None
    # isinstance before the membership test, the same order _validate uses: `{"type": {}}` is
    # valid JSON, and `{} in <dict>` raises TypeError — from the line whose whole job is to keep
    # a bad message from killing the socket.
    shown = type_ if isinstance(type_, str) and type_ in events.CLIENT_MESSAGES else "?"
    logger.warning("dropped invalid client message: type=%s", shown)


class SessionTargetUnavailable(Exception):
    """A window target lacks a current transcript or eligible prepared profile binding."""


def existing_session_source(
    store: SessionStore,
    catalog: Catalog,
    profile_launch: ProfileLaunchService | None,
    session_id: str,
) -> tuple[bool, bool]:
    """Off-loop proof for a source-backed target: (transcript exists, profile origin).

    Window handoff and existing-only subscription share this check. A source changing during
    its reads is refused; a later explicit request can observe its new state.
    """
    origin = store.session_origin(session_id)
    exists = catalog.exists(session_id)
    profile = origin == "profile"
    if profile:
        if profile_launch is None:
            raise SessionTargetUnavailable
        try:
            profile_launch.reopenable(session_id, exists)
        except ProfileError:
            raise SessionTargetUnavailable from None
    elif not exists:
        raise SessionTargetUnavailable
    if store.session_origin(session_id) != origin or catalog.exists(session_id) != exists:
        raise SessionTargetUnavailable
    if profile:
        assert profile_launch is not None
        try:
            profile_launch.reopenable(session_id, exists)
        except ProfileError:
            raise SessionTargetUnavailable from None
    return exists, profile


def _log_task_exception(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("client command failed: %s", type(exc).__name__)


_URL_SCHEMES = ("http://", "https://", "mailto:")


def _valid_open_url(value: str) -> bool:
    """Whether this is a URL we open. Markdown.tsx decides whether to draw the link at all, and
    the two must not disagree: a link the user can click that then silently does nothing is the
    worst shape a mismatch can take. The scheme is compared case-insensitively because the
    renderer lowercases it before its own check; whitespace stays rejected outright, and the
    renderer refuses to draw those too rather than this side being loosened to match."""
    if not value or any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in value):
        return False
    return value.lower().startswith(_URL_SCHEMES)


def _vscode_file_url(path: Path, line: str = "") -> str:
    """The `vscode://file` URL that opens `path`. VS Code ends the path at "?" or "#" and
    percent-decodes it, so each of those reaches it escaped, "%" first: unescaped, a name
    holding one opens another path than the one checked."""
    escaped = path.as_posix().replace("%", "%25").replace("#", "%23").replace("?", "%3F")
    return f"vscode://file/{escaped}{line}"


def _vault_target(vault: Path, raw: str) -> Path | None:
    """The existing file inside `vault` that `raw` (vault-relative, "/" or "\\") names, or None
    when it is absolute, escapes the vault, or does not name a file."""
    if not raw:
        return None
    if PureWindowsPath(raw).anchor:
        # A drive ("C:\\..."), a bare root ("\\x"), or a UNC share ("\\\\host\\share\\x") must
        # never reach the filesystem: resolving a UNC path goes out to the SMB redirector, which
        # can block this call (and, since it runs off-thread but still one at a time, every
        # other session's stream behind it) for the SMB connect timeout against a remote host.
        return None
    if sys.platform != "win32":
        # The client may send "\" as a separator on every OS, so a POSIX note whose own name
        # holds a "\" is unreachable here.
        raw = raw.replace("\\", "/")
    try:
        candidate = (vault / raw).resolve()
        candidate.relative_to(vault.resolve())
    except OSError, ValueError:
        return None
    return candidate if candidate.is_file() else None


# --- REST auth --------------------------------------------------------------------------------


_BEARER_PREFIX = "Bearer "  # case-sensitive scheme, exactly one space; nothing else is accepted


def _bearer_ok(request: Request, token: str) -> bool:
    """Whether the request's Authorization header is exactly `Bearer <token>`, compared with
    secrets.compare_digest. Nothing else authenticates a REST call: not a query-string token,
    not a cookie. The header value is never logged."""
    header = request.headers.get("authorization", "")
    if not header.startswith(_BEARER_PREFIX):
        return False
    candidate = header[len(_BEARER_PREFIX) :]
    return secrets.compare_digest(candidate.encode(), token.encode())


def _unauthorized() -> Response:
    return Response(
        _dumps({"error": "unauthorized"}), status_code=401, media_type="application/json"
    )


def _bad_request(reason: str) -> Response:
    """A refused query parameter. `reason` names the parameter and what is wrong with it; the
    value itself never travels back (or into a log), since it is the user's search text."""
    return Response(_dumps({"error": reason}), status_code=400, media_type="application/json")


# --- outbound JSON ---------------------------------------------------------------------------


def _dumps(obj: Any) -> str:
    """The one encoder for every JSON payload the server sends. Starlette's websocket.send_json
    uses ensure_ascii=False, so a lone UTF-16 surrogate (a driver frame's text cut mid-pair)
    survives into the str; uvicorn's websockets-sansio then raises UnicodeEncodeError encoding
    it to bytes for the wire, which kills the writer task while the socket stays open and silent.
    ensure_ascii=True (the default, kept here) escapes it as \\udXXX instead, which the
    browser's JSON.parse turns into U+FFFD."""
    return json.dumps(obj, separators=(",", ":"))


def _history_data(page: HistoryPage, *, expects_transcript: bool) -> dict[str, Any]:
    """The `data` of one history.page frame. A page the catalog could not produce
    names why, so the conversation can say which of them happened instead of showing an empty
    history: `busy` (another read holds the parse permit, and the note offers a reload — never
    an automatic retry), `unavailable` (unreadable), `transcript_missing` (the file is gone from
    under a session that should have one) and `too_large` (over the serialized budget; the
    browser cannot be sent a frame it would only fail to render). A `partial` page carries its
    events like any other and adds the note that older turns are beyond the tail budget.

    `expects_transcript` is the actor's own knowledge: for a brand-new session the CLI writes
    the file on the first turn, so "no file" is an empty history and not a loss."""
    if page.status == "busy":
        return {"events": [], "next_before_uuid": None, "error": "busy"}
    if page.status == "unreadable":
        return {"events": [], "next_before_uuid": None, "error": "unavailable"}
    if page.status == "missing" and expects_transcript:
        return {"events": [], "next_before_uuid": None, "error": "transcript_missing"}
    data: dict[str, Any] = {
        "events": [events.history_item(e) for e in page.events],
        "next_before_uuid": page.next_before_uuid,
    }
    if page.status == "partial":
        # The one partiality a page has, and the only value protocol.ts admits:
        # the chain was built from a transcript's newest window, so it ends at that window.
        data["partial"] = "tail_budget"
    if len(_dumps(data).encode("utf-8")) > MAX_HISTORY_BYTES:
        return {"events": [], "next_before_uuid": None, "error": "too_large"}
    return data


# --- the vault.sessions poller ------------------------------------------------------------------


class _VaultHub:
    """The single vault.sessions poller's current view, and every socket listening for
    changes to it. `live` (this app's own live sessions) is read fresh for every snapshot, so
    a reloaded page sees them at once; changes to either list go out at the next poll."""

    def __init__(self, live: Callable[[], list[str]]) -> None:
        self.others: list[OtherSession] = []
        self._live = live
        self.generation = str(uuid.uuid4())
        self.seq = 0
        self._last: Envelope = self.snapshot()
        self._broadcast = self._last
        self._subscribers: set[Callable[[Envelope], None]] = set()

    def snapshot(self) -> Envelope:
        data = VaultSessions(others=self.others, live=self._live()).data()
        if hasattr(self, "_last") and data != self._last["data"]:
            self.seq += 1
        envelope = {
            "v": 2,
            "type": "vault.sessions",
            "session_id": "",
            "generation": self.generation,
            "request_id": "",
            "seq": self.seq,
            "data": data,
        }
        self._last = envelope
        return envelope

    def subscribe(self, subscriber: Callable[[Envelope], None]) -> Callable[[], None]:
        self._subscribers.add(subscriber)
        return lambda: self._subscribers.discard(subscriber)

    def update(self, others: list[OtherSession]) -> None:
        self.others = others
        envelope = self.snapshot()
        # A connect snapshot records its change in _last without broadcasting it, so comparing
        # data with the last broadcast misses a later change back to that data; seq does not.
        if envelope["seq"] == self._broadcast["seq"]:
            return
        self._broadcast = envelope
        for subscriber in list(self._subscribers):
            subscriber(envelope)


async def _poll_vault_once(hub: _VaultHub, manager: SessionManager) -> None:
    others = await asyncio.to_thread(manager.others_in_vault)
    hub.update(others)


async def _poll_vault_loop(hub: _VaultHub, manager: SessionManager, poll_s: float) -> None:
    while True:
        await asyncio.sleep(poll_s)
        try:
            await _poll_vault_once(hub, manager)
        except Exception:  # a bad registry file must not end the poller
            logger.warning("vault session poll failed")


# --- app -----------------------------------------------------------------------------------------


def storage_readiness(
    store: SessionStore | None, data_dir: Path, *, refusal: str = "recovery_required"
) -> StorageReadiness:
    """What the app store can do right now: a trivial read proves it answers, and the data
    directory's own ACL is the same check the diagnostics document reports.

    One function so that `/api/v2/capabilities` and the diagnostics document never disagree
    about the store in the same launch. `None` is recovery mode: the store is what failed, and
    `refusal` is the code of the StateError the launcher caught. `recovery_required` names one
    refusal, the hot rollback journal the troubleshooting guide walks through; a corrupt, newer
    or busy store has its own code and its own procedure, so the shell must not call every
    failure a recovery.
    """
    if store is None:
        return StorageReadiness(state="unavailable", reason=refusal, private=None)
    try:
        store.get_settings()
    except StateError as exc:
        return StorageReadiness(state="unavailable", reason=exc.code, private=None)
    try:
        protection.require_private_directory(data_dir)
    except protection.ProtectionError:
        return StorageReadiness(state="read_only", reason="insecure_permissions", private=False)
    return StorageReadiness(state="ready", reason=None, private=True)


def create_app(
    *,
    config: Config,
    token: str,
    manager: SessionManager,
    catalog: Catalog,
    static_dir: Path | None,
    dev: bool = False,
    opener: Callable[[str], None] = winproc.open_external,
    auth_timeout_s: float = AUTH_TIMEOUT_S,
    vault_poll_s: float = VAULT_POLL_S,
    asset_poll_s: float = ASSET_POLL_S,
    worktree_sweep_s: float = SWEEP_INTERVAL_S,
    windows: WindowManager | None = None,
    settings: SettingsService | None = None,
    workspaces: WorkspaceService | None = None,
    agent_cli: Path | None = None,
    bootstrap: VaultBootstrap | None = None,
    readiness: Readiness | None = None,
    checks: ProviderChecks | None = None,
    diagnostics_document: Callable[[], dict[str, object]] | None = None,
    data: DataServices | None = None,
    owner: str | None = None,
    routine_clock: Clock | None = None,
    denied_roots: Callable[[], Sequence[Path]] | None = None,
    app_version: str = "",
    preview_spawn: Spawn | None = None,
    voice_worker: Path | None = None,
    voice_pack: VoicePack | None = None,
    decision_model: ModelPort | None = None,
    decision_clock: Clock | None = None,
    window_host: WindowHost | None = None,
) -> Starlette:
    """The app. The services for settings, workspaces and bootstrap, readiness, checks,
    diagnostics and data are optional and keyword-only, and each registers its route list only
    when supplied. The session routes, the route lists for commands, approvals, drafts,
    conversation search and artifacts, assets, avatars, session metadata, archives,
    inventory and profiles, and the routine routes always register; the native-asset list and
    the vault read and write lists register with `workspaces`, and the knowledge list
    (attachments, memory, backup, restore) with `workspaces` and `denied_roots`, the folders an
    export, backup or restore never writes into. The Decision routes and loop come with
    `workspaces` too: the loop asks `decision_model`, by default `DecisionModel(TypeSafeHttp())`,
    and runs on `decision_clock`, by default the system clock: not `routine_clock`, which the
    scheduler waits on. The agent surface and the window's Apply routes register together with
    `agent_cli` and `settings` (ADR-086); every ordinary chat's spawn then carries a grant into
    that surface (`manager.bind_agent_cli`)."""
    if config.vault is None:
        # The launcher replaces `vault` with the active workspace's working directory before it
        # gets here, and every scope, transcript folder and rails read is derived from it. No
        # working directory means there is nothing to serve, not a half-built app.
        raise ValueError("create_app needs a resolved working directory")
    if data is not None and owner is None:
        # The data routes carry the reset, and a ResetService whose owner matches no row deletes
        # exactly the in-flight rows the narrowing exists to keep — and reports them as deleted.
        # Failing to build the app is the only safe answer; a minted fallback would be silent.
        raise ValueError("create_app needs the launch owner when it serves the data routes")
    if data is not None and owner != data[3].owner:
        # A caller that built its ResetService for one owner and then named a different one here
        # would stamp the command/approval ledgers with `owner`, while the reset it serves
        # narrows on the other: the same `DELETE ... WHERE owner<>?` that is supposed to spare
        # every row this launch just wrote would instead take them, and report them deleted.
        # "The launch owner" is one fact, not two arguments that happen to agree.
        raise ValueError("create_app's owner must match the owner its ResetService was built with")
    vault = config.vault
    magpie = Magpie(config.data_dir, host=window_host, opener=opener)
    hub = _VaultHub(live=lambda: [actor.session_id for actor in manager.live()])
    background: set[asyncio.Task[None]] = set()
    opening: set[asyncio.Task[None]] = set()  # every socket's MAX_SOCKET_OPENS tasks, also here
    socket_slots = anyio.CapacityLimiter(MAX_SOCKET_CONNECTIONS)
    draft_workers: set[asyncio.Task[Any]] = set()
    command_workers: set[asyncio.Task[Any]] = set()
    approval_workers: set[asyncio.Task[Any]] = set()
    settings_workers: set[asyncio.Task[Any]] = set()
    workspace_workers: set[asyncio.Task[Any]] = set()
    diagnostics_workers: set[asyncio.Task[Any]] = set()
    data_workers: set[asyncio.Task[Any]] = set()
    asset_workers: set[asyncio.Task[Any]] = set()
    avatar_workers: set[asyncio.Task[Any]] = set()
    metadata_workers: set[asyncio.Task[Any]] = set()
    window_workers: set[asyncio.Task[Any]] = set()
    archive_workers: set[asyncio.Task[Any]] = set()
    conversation_workers: set[asyncio.Task[Any]] = set()
    vault_workers: set[asyncio.Task[Any]] = set()
    profile_workers: set[asyncio.Task[Any]] = set()
    routine_workers: set[asyncio.Task[Any]] = set()
    bot_workers: set[asyncio.Task[Any]] = set()
    voice_workers: set[asyncio.Task[Any]] = set()
    agent_workers: set[asyncio.Task[Any]] = set()

    def read_voice_settings() -> Mapping[str, object]:
        return settings.read().effective if settings is not None else {}

    voice = VoiceService(
        config.data_dir, read_settings=read_voice_settings, worker=voice_worker or VOICE_WORKER
    )
    pack = voice_pack or VoicePack.create(config.data_dir)
    voicebox = Voicebox(read_voice_settings, pack=pack)
    profiles = ProfileService(manager.app_state.store)
    provider_catalogs = ProviderCatalogService(manager.app_state.store)
    model_connections = Connections(settings) if settings is not None else None
    profile_launch: ProfileLaunchService | None = None
    publications: VaultPublicationService | None = None
    session_roots: SessionRoots | None = None
    worktree_service: WorktreeService | None = None
    check_runner: CheckRunner | None = None
    preview_windows: PreviewWindows | None = None
    previews: PreviewService | None = None
    lifecycle: VaultLifecycleService | None = None
    recovery: RecoveryStore | None = None
    coordinator: MutationCoordinator | None = None
    resolve_root: WorkspaceMutationResolver | None = None
    code_files: CodeFiles | None = None
    memory_area: BotMemoryArea | None = None
    rails_git: CodeGit | None = None
    decision_client: ModelPort | None = None
    decision_missions: MissionService | None = None
    decision_engine: DecisionLoopEngine | None = None
    inbox_host: SuggestionHost | None = None
    scope = draft_scope(vault)
    assets = AssetService(config.data_dir / "assets", manager.app_state.store, scope)
    avatars = AvatarService(config.data_dir / "assets" / "avatars", manager.app_state.store, assets)
    manager.bind_assets(assets.resolve)
    plan_limits = PlanLimits()
    manager.bind_limits(plan_limits.observe)
    if settings is not None:
        manager.bind_max_live(settings.max_live_sessions)
    catalog.bind_history_media(assets.find_by_digest)
    catalog.bind_artifacts()

    def conversation_source(session_id: str) -> tuple[str, str | None]:
        actor = manager.get(session_id)
        return (actor.history_source, actor.since) if actor is not None else (session_id, None)

    conversation_search = ConversationSearch(catalog, conversation_source)
    # One owner per launch, shared by both ledgers: "this launch" is one fact, and the reset
    # service is handed the same value so it can tell a live in-flight row from a dead launch's.
    # This launch-local default keeps the reset guard from seeing an unset owner.
    ledger_owner = owner if owner is not None else str(uuid.uuid4())
    commands = CommandRunner(CommandService(manager.app_state, vault, owner=ledger_owner), manager)
    assets.bind_history_metadata(commands.service.attachment_metadata)
    messaging_runtime: MessagingRuntime
    bridge_workspace_id: str | None = None
    messaging_workers: set[asyncio.Task[Any]] = set()
    approvals = ApprovalRunner(
        ApprovalService(manager.app_state, vault, owner=ledger_owner), manager
    )
    clock = routine_clock or SystemClock()
    admission_lock = windows.admission_lock if windows is not None else asyncio.Lock()

    def attended() -> bool:
        # A routine runs only while a window is open; a test app without windows is attended.
        return windows is None or windows.count > 0

    routines = RoutineService(
        manager.app_state.store, profiles, settings, workspaces, now=clock.wall
    )
    bots = (
        BotService(manager.app_state.store, profiles, routines, workspaces, now=clock.wall)
        if workspaces is not None
        else None
    )
    # Bot token usage: the dashboard's views over the active vault's Bot chats, and the hook that
    # schedules a capture at each chat turn's end (`observe`, below); a capture logs Bot chats only.
    usage: BotUsage | None = None
    usage_views: UsageViews | None = None
    schedule_capture: Callable[[str], None] | None = None
    if bots is not None and workspaces is not None:

        def usage_names() -> Names:
            """The names the usage views show: every profile's, the current Bots' ids and the
            model connections'. A connection list that cannot be read names no connection."""
            connections: dict[str, str] = {}
            if model_connections is not None:
                with contextlib.suppress(Exception):
                    connections = {row.id: row.name for row in model_connections.read()[1]}
            return Names(
                profiles={
                    profile["profile_id"]: profile["identity"]["name"]
                    for archived in (False, True)
                    for profile in profiles.list(archived)
                },
                bots=frozenset(bot["bot_id"] for bot in bots.list()),
                connections=connections,
            )

        usage = BotUsage(manager.app_state.store, catalog, now=clock.wall)
        usage_views = UsageViews(
            usage,
            manager.app_state.store,
            active=lambda: workspaces.resolve_active().workspace.workspace_id,
            names=usage_names,
            now=clock.wall,
        )
        schedule_capture = turn_end_capture(usage_views, bot_workers)

    def mattermost_bridge() -> MattermostBot | None:
        current = messaging_runtime.bridge("mattermost")
        return current if isinstance(current, _MattermostBridge) else None

    decision_store = DecisionLoopStore(manager.app_state.store)
    bot_runtime = (
        BotRuntime(
            bots,
            decision_store,
            bridge=mattermost_bridge,
            bridge_workspace=lambda: bridge_workspace_id,
            attended=attended,
            now=clock.wall,
        )
        if bots is not None
        else None
    )
    bot_admission_closed = False

    def close_bots() -> None:
        nonlocal bot_admission_closed
        bot_admission_closed = True
        if bot_runtime is not None:
            bot_runtime.close()

    if bot_runtime is not None:
        manager.bind_settle(bot_runtime.revoke_all)

    def _track(task: asyncio.Task[None]) -> None:
        background.add(task)
        task.add_done_callback(lambda t: (background.discard(t), _log_task_exception(t)))

    async def release_copies() -> None:
        service = worktree_service
        if service is not None:
            await service.release_copies()  # logs, never raises

    def copies_released() -> None:
        """On the loop, once a deletion or reset that may have released Bot code copies has
        committed: settle them now, outside that transaction. A copy still in use, or one the
        settle cannot finish, stays released for the next sweep's retry."""
        _track(asyncio.create_task(release_copies()))

    def capabilities() -> Capabilities:
        if readiness is None:
            # Unreachable: the only route that calls this is registered with the same object.
            raise ValueError("capabilities needs readiness")
        active = None
        available = True
        if workspaces is not None:
            # The launch keeps the workspace it resolved, but the selection is read fresh, as
            # the workspaces listing reads it: a choice made in this window must show here as
            # `restart_required` too, or the two routes disagree about the same store.
            active = dataclasses.replace(
                workspaces.resolve_active(), selected_id=workspaces.selected_id()
            )
            # The scratch workspace's root lives under the data directory, which the deny set
            # refuses on purpose, so only a persisted registration is re-canonicalised here.
            if active.workspace.persistent:
                available = workspaces.current_root(active.workspace) is not None
        return readiness.capabilities(
            active,
            storage_readiness(manager.app_state.store, config.data_dir),
            root_available=available,
        )

    if settings is not None and workspaces is not None and readiness is not None:
        assert model_connections is not None
        profile_launch = ProfileLaunchService(
            profiles,
            settings,
            workspaces,
            provider_catalogs,
            lambda: capabilities().provider["claude"],
            vault,
            catalog.exists,
            connections=model_connections,
            route_can_start=lambda: (
                readiness.may_spawn()
                and not readiness.billing_preflight_blocked
                and storage_readiness(manager.app_state.store, config.data_dir).state == "ready"
            ),
        )
        launch_service = profile_launch

        async def guard_profile(session_id: str, prefs: Prefs) -> None:
            try:
                await asyncio.to_thread(launch_service.validate_launch, session_id, prefs)
            except ProfileError as error:
                way_on = {
                    "provider_unavailable": "Check Settings, then send again.",
                    "workspace_unavailable": "Check the vault in Settings, then send again.",
                    "connection_missing": "Choose an available model connection in Providers.",
                    "connections_unavailable": "Check model connections in Providers.",
                    "connection_invalid": "Choose a valid model connection in Providers.",
                    "connection_bot_only": "Start this profile from its Bot.",
                }.get(error.code, "Open the profile in Agents and continue from there.")
                raise StartError(
                    "bad_request",
                    f"Profile launch refused ({error.code}). {way_on}",
                ) from None
            except ProtectionError:
                raise StartError("internal", "Profile storage is unavailable.") from None

        async def initialized_profile(session_id: str, facts: StartFacts | None = None) -> None:
            try:
                await storage_call(lambda: launch_service.native_initialized(session_id, facts))
            except ProfileError as error:
                if error.code in START_CHECK_CODES:
                    raise StartError(
                        "bad_request",
                        f"Profile launch refused ({error.code}). Claude Code did not confirm "
                        "this profile's settings when it started, so nothing was sent. Edit "
                        "the profile in Agents, then start again.",
                    ) from None
                if error.code == "capability_changed":
                    raise StartError(
                        "bad_request",
                        "Profile launch refused (capability_changed). Open the profile in "
                        "Agents and continue from there.",
                    ) from None
                raise StartError("internal", "The profile launch could not be recorded.") from None
            except ProtectionError:
                raise StartError("internal", "The profile launch could not be recorded.") from None

        async def observe_profile_catalog(started: SessionStarted) -> None:
            label = evidence_version(readiness.version)
            if evidence_version(started.cli_version) != label:
                # A spawn's version and the readiness check's can fall in different evidence
                # classes: the CLI moves between the two reads. Only the readiness version
                # starts a prepared session, so an observation labelled with the other one
                # would, once the retained catalogs are all bound, take a prepared session's
                # catalog as the only row of another label — and that session would answer
                # `capability_changed`. The next start at an agreed version observes it.
                return
            await asyncio.to_thread(
                provider_catalogs.observe,
                "claude",
                ADAPTER_VERSION,
                label,
                baseline(readiness.version, models_observed=bool(started.models)),
                observed_models(started),
            )

        manager.bind_profiles(
            guard_profile, observe_profile_catalog, initialized=initialized_profile
        )

    async def prepare_profile(
        profile_id: str,
        raw: dict[str, Any],
        *,
        admitted: Callable[[], bool] | None = None,
        launch_origin: Literal["interactive", "routine"] = "interactive",
    ) -> dict[str, Any]:
        """`launch_origin` is this server's own fact, never a body field: every chat the user
        prepares (public, Bot or Bot-thread) is `interactive`; only `prepare_routine` passes
        `routine`. A Bot code copy is made only for an interactive one."""
        if profile_launch is None:
            raise ProfileError("provider_unavailable")
        session_id = raw["session_id"]
        if isinstance(session_id, str) and manager.get(session_id) is not None:
            origin = await asyncio.to_thread(manager.app_state.store.session_origin, session_id)
            # Re-check after the await: a close can have dropped the actor in between.
            if manager.get(session_id) is not None and origin != "profile":
                raise ProfileError("session_exists")
        # A body key of the same name is a duplicate keyword here, never an override.
        result = await asyncio.to_thread(
            profile_launch.prepare, profile_id, **raw, launch_origin=launch_origin
        )
        if admitted is not None and not admitted():
            raise BotError("app_closing")
        if "session_id" in result:
            # A result that names the session is its committed profile binding.
            exists = await asyncio.to_thread(catalog.exists, session_id)
            await asyncio.to_thread(profile_launch.reopenable, session_id, exists)
            if admitted is not None and not admitted():
                raise BotError("app_closing")
            actor = manager.get(session_id) or (
                manager.open(session_id, profile=True)
                if exists
                else manager.attach_prepared(session_id, profile=True)
            )
            result["generation"] = actor.generation
        return result

    async def prepare_routine(profile_id: str, raw: dict[str, Any]) -> dict[str, Any]:
        if profile_launch is not None:
            bot_id = await asyncio.to_thread(
                profile_launch.bot_for, profile_id, raw["workspace_id"]
            )
            if bot_id is not None:
                raw = {**raw, "bot_id": bot_id}
        return await prepare_profile(profile_id, raw, launch_origin="routine")

    tasks = TaskRunner(
        TaskStore(manager.app_state.store, scope, ledger_owner),
        routines,
        commands,
        manager,
        DraftService(manager.app_state, vault),
        profiles,
        prepare=prepare_routine if profile_launch is not None else None,
        clock=clock,
        attended=attended,
        admission_lock=admission_lock,
        begin_attempt=bot_runtime.begin_attempt if bot_runtime is not None else None,
        observe_attempt=bot_runtime.observe_attempt if bot_runtime is not None else None,
        attempt_finished=bot_runtime.attempt_finished if bot_runtime is not None else None,
        discard_attempt=bot_runtime.discard_attempt if bot_runtime is not None else None,
    )
    scheduler = Scheduler(
        routines,
        tasks,
        clock,
        attended,
        admission_lock=admission_lock,
        card_waiting=functools.partial(card_waiting, VaultService(workspaces))
        if workspaces is not None
        else None,
        notes_changed=functools.partial(notes_changed, VaultService(workspaces))
        if workspaces is not None
        else None,
    )
    seat_host = SeatHost(tasks, scheduler, manager)

    def provider_view(provider: str, action: str) -> dict[str, Any]:
        if provider != "claude" or action not in {"models", "capabilities"}:
            raise ProfileError("not_found")
        version = capabilities().provider["claude"].version
        observed = (
            provider_catalogs.latest(provider, ADAPTER_VERSION, evidence_version(version))
            if version
            else None
        )
        common = {
            "provider": provider,
            "adapter_version": ADAPTER_VERSION,
            "native_version": version or None,
            "observed_at": observed["observed_at"] if observed else None,
            "catalog_key": observed["catalog_key"] if observed else None,
        }
        if action == "models":
            models = observed["models"] if observed else []
            return {**common, "status": "supported" if models else "unverified", "models": models}
        return {
            **common,
            "capabilities": observed["capabilities"] if observed else baseline(version),
        }

    def image_input() -> dict[str, str | None]:
        # The baseline's verdict for the CLI version this launch observed. Reads readiness's
        # observed version directly: capabilities() also reads the workspace store, which a
        # committing writer can hold past this route's budget.
        version = readiness.version if readiness is not None else ""
        row = next(row for row in baseline(version) if row["id"] == "image_input")
        return {"status": row["status"], "reason": row["reason"]}

    def inspect_profile_session(session_id: str) -> dict[str, Any]:
        if (
            profile_launch is None
            or manager.app_state.store.session_origin(session_id) != "profile"
        ):
            raise ProfileError("not_found")
        result = profile_launch.inspect_session(session_id)
        actor = manager.get(session_id)
        result["generation"] = actor.generation if actor is not None else None
        return result

    def require_bot_chat(bot: dict[str, Any]) -> None:
        if bot_admission_closed or not attended():
            raise BotError("app_closing")
        if profile_launch is None:
            raise BotError("provider_unavailable")
        if bot["profile"] is None:
            raise BotError("profile_missing")
        if bot["profile"]["archived"]:
            raise BotError("profile_archived")
        if bot["workspace"] is None:
            raise BotError("workspace_missing")
        if bot["chat"]["archived"]:
            raise BotError("profile_blocked")

    async def bot_chat_source(
        bot: dict[str, Any],
    ) -> tuple[dict[str, Any], bool, SessionActor | None]:
        require_bot_chat(bot)
        assert profile_launch is not None
        session_id = bot["chat_session_id"]
        held = manager.get(session_id)
        preview = await asyncio.to_thread(profile_launch.inspect_session, session_id)
        if (
            preview["profile_id"] != bot["profile_id"]
            or preview["workspace_id"] != bot["workspace_id"]
            or preview["profile_revision"] != bot["chat"]["profile_revision"]
        ):
            raise BotError("corrupt")
        try:
            exists, profile = await asyncio.to_thread(
                existing_session_source,
                manager.app_state.store,
                catalog,
                profile_launch,
                session_id,
            )
        except SessionTargetUnavailable:
            # The first native process can initialize before its first transcript write. Only
            # that still-held profile actor is a source; its UUID cannot create a replacement.
            if (
                preview["started"]
                and held is not None
                and manager.get(session_id) is held
                and held._profile
                and held.history_source == session_id
                and held.live
                and not held.transcript_expected
            ):
                return preview, False, held
            raise BotError(
                "transcript_missing" if preview["started"] else "session_exists"
            ) from None
        if not profile:
            raise BotError("corrupt")
        return preview, exists, None

    async def prepare_bot_chat(bot: dict[str, Any]) -> dict[str, Any]:
        require_bot_chat(bot)
        session_id = bot["chat_session_id"]
        if bot["chat"]["profile_revision"] is None:
            result = await prepare_profile(
                bot["profile_id"],
                {
                    "command_id": session_id,
                    "session_id": session_id,
                    "expected_profile_revision": bot["profile"]["revision"],
                    "workspace_id": bot["workspace_id"],
                    "bot_id": bot["bot_id"],
                },
                admitted=lambda: not bot_admission_closed and attended(),
            )
            return result
        preview, exists, source_actor = await bot_chat_source(bot)
        require_bot_chat(bot)
        actor = manager.get(session_id)
        if source_actor is not None and actor is not source_actor:
            raise BotError("transcript_missing")
        if actor is not None and (not actor._profile or actor.history_source != session_id):
            raise BotError("session_exists")
        actor = actor or (
            manager.open(session_id, profile=True)
            if exists
            else manager.attach_prepared(session_id, profile=True)
        )
        preview["generation"] = actor.generation
        return preview

    async def bot_chat_status(bot: dict[str, Any]) -> dict[str, Any]:
        if bot["chat"]["archived"]:
            return {"state": "archived", "blockers": []}
        try:
            require_bot_chat(bot)
            if bot["chat"]["profile_revision"] is None:
                if await asyncio.to_thread(catalog.exists, bot["chat_session_id"]):
                    raise BotError("session_exists")
                if not bot["workspace"]["active"]:
                    raise BotError("workspace_restart_required")
                return {"state": "unprepared", "blockers": []}
            preview, _exists, _held = await bot_chat_source(bot)
            # A model field blocked by its connection sends that reason, which has a sentence,
            # in place of the field name.
            reason = next(
                (
                    field["reason"]
                    for field in preview["fields"]
                    if field["field"] == "runtime.model"
                    and field["reason"]
                    in {"connection_missing", "connections_unavailable", "connection_invalid"}
                ),
                None,
            )
            blockers = list(
                dict.fromkeys(
                    reason if blocker == "runtime.model" and reason else blocker
                    for blocker in preview["blockers"]
                )
            )
            return {
                "state": (
                    "blocked"
                    if not preview["launchable"]
                    else "reopenable"
                    if preview["started"]
                    else "prepared"
                ),
                "blockers": blockers,
            }
        except (BotError, ProfileError) as error:
            if error.code == "transcript_missing":
                state = "transcript_missing"
            elif error.code in {
                "profile_archived",
                "workspace_restart_required",
                "workspace_unavailable",
                "profile_blocked",
                "launch_blocked",
                "launch_changed",
                "capability_changed",
            }:
                state = "blocked"
            else:
                state = "unavailable"
            return {"state": state, "blockers": [error.code]}

    def bot_chat_busy(session_id: str) -> bool:
        actor = manager.get(session_id)
        return actor is not None and (actor.busy or actor.waiting)

    # Registers with the profile launch service: a profile session's every spawn writes its
    # pinned app MCP servers' config after the guard; an ordinary chat never calls this.
    if profile_launch is not None:
        mcp_service = profile_launch
        mcp_service.bind_mcp_directory(config.data_dir / "mcp")
        mcp_service.bind_bot_settings_directory(config.data_dir / "bot-memory-settings")

        async def resolve_bot_memory(session_id: str) -> str | None:
            try:
                return await asyncio.to_thread(mcp_service.bot_memory_settings_path, session_id)
            except ProfileError:
                raise StartError(
                    "bad_request",
                    "Bot memory is unavailable. Check the Bot's workspace and memory folder.",
                ) from None
            except ProtectionError, OSError:
                raise StartError("internal", "Bot memory settings are unavailable.") from None

        manager.bind_bot_memory(resolve_bot_memory)

        async def resolve_bot_prompt(session_id: str) -> str | None:
            try:
                return await asyncio.to_thread(mcp_service.bot_system_prompt, session_id)
            except ProfileError:
                raise StartError(
                    "bad_request",
                    "This Bot's instructions are unavailable. Open the Bot and start again.",
                ) from None
            except ProtectionError, OSError:
                raise StartError("internal", "Profile storage is unavailable.") from None

        manager.bind_bot_prompt(resolve_bot_prompt)

        async def resolve_profile_mcp(session_id: str) -> str | None:
            try:
                return await asyncio.to_thread(mcp_service.mcp_config_path, session_id)
            except ProfileError as error:
                way_on = {
                    # Sending again clears a failed write, since the writer removes a file it
                    # started and could not finish; a restart's sweep clears a generated name
                    # another writer left; the rest is the folder's own privacy.
                    "mcp_config_unavailable": (
                        "Send again. If it fails again, restart VIKI Agent; if it still fails, "
                        "check that the data folder's mcp folder is private to you."
                    ),
                }.get(error.code, "Open the profile in Agents and continue from there.")
                raise StartError(
                    "bad_request",
                    f"Profile launch refused ({error.code}). {way_on}",
                ) from None
            except ProtectionError, OSError:
                raise StartError("internal", "Profile storage is unavailable.") from None

        manager.bind_mcp(resolve_profile_mcp)

    # Registers with `workspaces`: the read-only native inspector. Its `claude:user` root is
    # resolved once, before serving, and is None when CLAUDE_CONFIG_DIR sends the CLI elsewhere.
    native_inspector: NativeAssetInspector | None = None
    native_home = None
    if workspaces is not None:
        native_home = claude_user_root(
            config.claude_home, config_dir_set=config.claude_config_dir_set
        )
        native_inspector = NativeAssetInspector(workspaces, claude_home=native_home)

    def native_sources() -> list[dict[str, str]]:
        sources = []
        if native_home is not None:
            sources.append({"source_id": "claude:user", "provider": "claude", "scope": "user"})
        if workspaces is not None:
            active = workspaces.resolve_active()
            if (
                not active.chat_only
                and active.workspace.kind == "vault"
                and active.workspace.persistent
            ):
                source_id = f"claude:workspace:{active.workspace.workspace_id}"
                sources.append({"source_id": source_id, "provider": "claude", "scope": "workspace"})
        return sources

    if workspaces is not None:
        # One coordinator and one resolver for every vault writer: with two coordinators a save
        # and an operation could hold the same note at once.
        coordinator = MutationCoordinator()
        resolve_root = WorkspaceMutationResolver(
            workspaces,
            git_available=lambda: readiness is not None and readiness.git_state == "ready",
        )
        publication_journal = PublicationJournal(manager.app_state.store)
        operations_journal = OperationJournal(manager.app_state.store)
        recovery = RecoveryStore(config.data_dir / "vault-recovery", operations_journal)
        lifecycle = VaultLifecycleService(
            operations_journal,
            recovery,
            resolve_root,
            coordinator,
            lease_wait_s=VAULT_LEASE_WAIT_S,
            publications=publication_journal,
        )
        # A `recovery_required` operation refuses every save to its workspace.
        # A journal row does not record its root's kind, so startup recovery must resolve a vault
        # or a code folder; `save` and `save_code` each refuse the other kind's root before any
        # reservation. Every other writer keeps the vault-only `resolve_root`.
        publications = VaultPublicationService(
            publication_journal,
            functools.partial(resolve_root, kind=None),
            coordinator,
            workspace_gate=lifecycle.require_writable,
        )

    async def sessions(request: Request) -> Response:
        """GET /api/sessions → the SessionsResult as JSON: the rows in the catalog's
        order plus the listing's own observation — `state`, the `reason` that stopped it and the
        number of transcripts it could not read — from catalog.list_sessions() run in a thread;
        401 {"error": "unauthorized"} without the Bearer token. The observation travels with the
        rows so the browser can say "incomplete" instead of painting an unknown count as zero.
        Every JSON body goes through _dumps."""
        if not _bearer_ok(request, token):
            return _unauthorized()
        listing = await asyncio.to_thread(catalog.list_sessions)
        if bots is not None:
            bot_sessions = await storage_call(bots.chat_session_ids)
            listing = dataclasses.replace(
                listing,
                sessions=[
                    dataclasses.replace(row, bot=row.session_id in bot_sessions)
                    for row in listing.sessions
                ],
            )
        return Response(
            _dumps(dataclasses.asdict(listing)), status_code=200, media_type="application/json"
        )

    # Search re-reads transcripts for each query. Serialize scans to bound memory and avoid
    # concurrent disk and CPU work stalling the event loop; a frontend gate would not cover
    # requests from another window.
    search_gate = asyncio.Lock()

    async def search(request: Request) -> Response:
        """GET /api/search?q=<text>[&request_id=<canonical uuid>] → the SearchResult as JSON
        from catalog.run_search(q) run in a thread, q passed through untouched, with
        `request_id` echoed back so a late answer can be told from the current one ("" when the
        client sent none). 400 {"error": "q is required"} when q is missing or blank,
        {"error": "q is too long"} above QUERY_MAX_CHARS, {"error": "request_id is invalid"}
        for a non-canonical id — all after the 401 and before any read. One scan at a time,
        with each admitted query receiving its own result. The query text never enters a log
        line or an error body."""
        if not _bearer_ok(request, token):
            return _unauthorized()
        q = request.query_params.get("q", "")
        if not q.strip():
            return _bad_request("q is required")
        if len(q) > QUERY_MAX_CHARS:
            # Code points of the raw q, refused before the catalog opens a single file: the
            # palette's own maxLength is a courtesy, and a pasted page would otherwise be
            # carried through every transcript in the vault as a byte pattern that cannot match.
            return _bad_request("q is too long")
        request_id = request.query_params.get("request_id", "")
        if request_id and not _is_canonical_uuid(request_id):
            # Echoed into the response, so it is admitted on the same terms as every other
            # identifier the client sends over the socket.
            return _bad_request("request_id is invalid")
        async with search_gate:
            result = await asyncio.to_thread(catalog.run_search, q)
        body = dataclasses.asdict(dataclasses.replace(result, request_id=request_id))
        return Response(_dumps(body), status_code=200, media_type="application/json")

    async def rails_route(request: Request) -> Response:
        """GET /api/rails → the Rails dataclass as JSON, read in a thread (it runs git)."""
        if not _bearer_ok(request, token):
            return _unauthorized()

        # project_dir resolves symlinks on macOS and Linux, so it runs in the thread too.
        def read() -> rails.Rails:
            folder = project_dir(config.claude_home, vault)
            if rails_git is not None:
                return rails.rails_read(
                    vault, folder, hardening=HARDENING, operation=rails_git.operation
                )
            # Avoid passing an unset hardening option so the reader uses its default path.
            return rails.rails_read(vault, folder)

        data = await asyncio.to_thread(read)
        payload = dataclasses.asdict(data)
        if data.counts_state == "unknown":
            payload.pop("counts_state")
        body = _dumps(payload)
        return Response(body, status_code=200, media_type="application/json")

    async def _actor_for(
        session_id: str,
        *,
        start_if_new: bool,
        existing_only: bool = False,
        expected_model: object = MODEL_UNSPECIFIED,
    ) -> SessionActor:
        actor = manager.get(session_id)
        if actor is not None:
            return actor
        if existing_only:
            try:
                exists, profile = await asyncio.to_thread(
                    existing_session_source,
                    manager.app_state.store,
                    catalog,
                    profile_launch,
                    session_id,
                )
            except Exception:
                # An ordinary open may have admitted a held actor during the source read.
                actor = manager.get(session_id)
                if actor is not None:
                    return actor
                raise
            actor = manager.get(session_id)
            if actor is not None:
                return actor
            # The worker made the complete source proof; no await separates this held-actor
            # recheck from allocation, and neither attachment starts a provider.
            return (
                manager.open(session_id, profile=profile, agent_cli=True)
                if exists
                else manager.attach_prepared(session_id, profile=True)
            )
        exists = await asyncio.to_thread(catalog.exists, session_id)
        origin = await asyncio.to_thread(manager.app_state.store.session_origin, session_id)
        # An id with any label row (pinned, titled, archived, or an all-default row left by an
        # unpin) whose transcript is gone is a missing source, not a new chat: starting a fresh
        # process under it would fabricate a conversation the label never described. Read before
        # the re-check below, so no await separates that re-check from manager.create().
        labelled = (
            not exists
            and origin != "profile"
            and await asyncio.to_thread(
                manager.app_state.store.get_session_metadata, scope, session_id
            )
            is not None
        )
        # Re-check: another command for the same new session id can have run manager.create()
        # while this one was awaiting catalog.exists() (dispatch runs each message as its own
        # task). manager.create() is not idempotent, so without this check two commands for one
        # unknown id would each make their own actor, and the second silently orphans the first.
        actor = manager.get(session_id)
        if actor is not None:
            return actor
        if origin == "profile":
            if profile_launch is None:
                raise StateError("corrupt")
            try:
                await asyncio.to_thread(profile_launch.reopenable, session_id, exists)
            except ProfileError:
                raise StateError("corrupt") from None
            actor = manager.get(session_id)
            if actor is not None:
                return actor
        if exists:
            return manager.open(session_id, profile=origin == "profile", agent_cli=True)
        if origin == "profile":
            return manager.attach_prepared(session_id, profile=True)
        if labelled:
            # A resume actor that never starts here: its history answers transcript_missing,
            # and a send reaches the CLI's own "no conversation found".
            return manager.open(session_id, profile=False, agent_cli=True)
        actor = (
            manager.create(session_id, agent_cli=True)
            if expected_model is MODEL_UNSPECIFIED
            else manager.create(session_id, expected_model=expected_model, agent_cli=True)
        )
        if start_if_new:
            await actor.start()
        return actor

    async def window_target_available(session_id: str) -> bool:
        def read() -> tuple[bool, bool]:
            require_private_store(manager.app_state.store)
            manager.app_state.store.get_settings()  # prove the admitted store still answers
            return existing_session_source(
                manager.app_state.store, catalog, profile_launch, session_id
            )

        try:
            await run_sync(read)
        except SessionTargetUnavailable:
            # A held actor is a target even before its first transcript appears. Recheck after
            # the source read so closing that actor cannot admit an invented replacement.
            return manager.get(session_id) is not None
        return True

    async def ws_endpoint(websocket: WebSocket) -> None:
        try:
            socket_slots.acquire_nowait()
        except anyio.WouldBlock:
            await websocket.close(code=1013)
            return
        try:
            await websocket.accept()
            if not await _authenticate(websocket):
                return
            await connected_socket(websocket)
        finally:
            socket_slots.release()

    async def connected_socket(websocket: WebSocket) -> None:
        queue: asyncio.Queue[tuple[str, int] | None] = asyncio.Queue()
        space_available = asyncio.Event()
        queued_bytes = 0
        closed = False
        overflow = False
        subs: dict[str, Callable[[], None]] = {}
        subscriptions: dict[str, str] = {}
        local_tasks: set[asyncio.Task[None]] = set()
        open_tasks: set[asyncio.Task[None]] = set()
        receipts: dict[str, tuple[str, Envelope | None]] = {}

        def require_resync() -> None:
            nonlocal queued_bytes, overflow
            if overflow:
                return
            overflow = True
            space_available.set()
            while not queue.empty():
                item = queue.get_nowait()
                if item is not None:
                    queued_bytes -= item[1]
            queue.put_nowait(None)

        def enqueue(envelope: Envelope) -> None:
            nonlocal queued_bytes
            if closed or overflow:
                return
            try:
                encoded = _dumps(envelope)
                size = len(encoded.encode("utf-8"))
            except TypeError, ValueError, RecursionError:
                # Subscriber callbacks must not unwind the provider's emitter. A broken
                # frame closes this view; another view and the actor retain their ownership.
                require_resync()
                return
            if queued_bytes + size > MAX_OUTBOUND_BYTES:
                require_resync()
                return
            queued_bytes += size
            queue.put_nowait((encoded, size))

        enqueue(hub.snapshot())
        unsub_vault = hub.subscribe(enqueue)

        def receipt(
            msg: dict[str, Any], status: str, reason: str = "", target: SessionActor | None = None
        ) -> Envelope:
            result: Envelope = {
                "v": 2,
                "type": "control.receipt",
                "session_id": msg["session_id"],
                "generation": msg["generation"],
                "command_id": msg["command_id"],
                "data": {"status": status, "reason": reason},
            }
            if target is not None:
                result["data"]["target"] = {
                    "session_id": target.session_id,
                    "generation": target.generation,
                }
            return result

        async def session_open(msg: dict[str, Any]) -> None:
            sid, request_id = msg["session_id"], msg["request_id"]
            existing_only = msg.get("existing_only", False)
            expected_model = msg.get("expected_model", MODEL_UNSPECIFIED)
            subscriptions[sid] = request_id
            old = subs.pop(sid, None)
            if old:
                old()

            def refuse(
                reason: Literal["unavailable", "storage_unavailable", "model_review_required"],
            ) -> None:
                if not closed and not overflow and subscriptions.get(sid) == request_id:
                    enqueue(
                        {
                            **events.SessionOpenRefused(reason=reason).envelope(sid, 0),
                            "request_id": request_id,
                        }
                    )

            try:
                actor = await _actor_for(
                    sid,
                    start_if_new=not bool(msg.get("generation")),
                    existing_only=existing_only,
                    expected_model=expected_model,
                )
                if expected_model is not MODEL_UNSPECIFIED:
                    actor.register(expected_model=expected_model)
            except SessionTargetUnavailable:
                refuse("unavailable")
                return
            except ProtectionError:
                if expected_model is not MODEL_UNSPECIFIED:
                    refuse("model_review_required")
                    return
                if existing_only:
                    refuse("storage_unavailable")
                    return
                raise
            except StateError as exc:
                if expected_model is not MODEL_UNSPECIFIED:
                    refuse("model_review_required")
                    return
                if existing_only:
                    refuse("storage_unavailable")
                    return
                actor = manager.get(sid)
                if actor is None:
                    return
                actor.report_state_error(exc)
            except Exception:
                if existing_only:
                    refuse("storage_unavailable")
                    return
                raise
            # Several recovered sessions share one writer. Wait before the final capture,
            # rather than overflowing a healthy connection with simultaneous snapshots.
            # Re-capture after every wait: live events may have changed the actor meanwhile.
            try:
                with anyio.fail_after(SOCKET_WRITE_TIMEOUT_S):
                    while True:
                        if closed or overflow or subscriptions.get(sid) != request_id:
                            return
                        if manager.get(sid) is not actor:
                            # This current request never subscribed, so it could not observe
                            # the old actor's close. Refuse explicitly instead of leaving the
                            # browser waiting forever for a barrier that cannot arrive.
                            if existing_only:
                                refuse("unavailable")
                            else:
                                require_resync()
                            return
                        synced: Envelope = {
                            "v": 2,
                            "type": "session.synced",
                            "session_id": sid,
                            "generation": actor.generation,
                            "request_id": request_id,
                            "seq": actor.seq,
                            "data": {},
                        }
                        recovery = actor.recovery(msg.get("generation"), msg.get("after_seq", 0))
                        frames = [{**env, "request_id": request_id} for env in recovery] + [synced]
                        size = sum(len(_dumps(frame)) for frame in frames)
                        if size > MAX_OUTBOUND_BYTES:
                            # Replay retention exceeds a connection's output budget. Sending
                            # that same suffix on every reconnect would never advance its cursor.
                            recovery = actor.recovery(None, 0)
                            frames = [{**env, "request_id": request_id} for env in recovery] + [
                                synced
                            ]
                            size = sum(len(_dumps(frame)) for frame in frames)
                        if size > MAX_OUTBOUND_BYTES:
                            require_resync()
                            return
                        if queued_bytes + size <= MAX_OUTBOUND_BYTES:
                            break
                        space_available.clear()
                        await space_available.wait()
            except TimeoutError, TypeError, ValueError, RecursionError:
                require_resync()
                return

            # No await from the successful capture through subscription and its barrier.
            def forward(envelope: Envelope) -> None:
                if subscriptions.get(sid) == request_id:
                    enqueue({**envelope, "request_id": request_id})

            for frame in frames:
                enqueue(frame)
            subs[sid] = actor.subscribe(forward)

        async def history_more(msg: dict[str, Any]) -> None:
            actor = manager.get(msg["session_id"])
            if actor is None or actor.generation != msg["generation"]:
                return
            subscription = subscriptions.get(actor.session_id)
            if subscription is None:
                return
            data: dict[str, Any]
            try:
                page = await asyncio.to_thread(
                    catalog.history,
                    actor.history_source,
                    before_uuid=msg.get("before_uuid"),
                    until=actor.since,
                )
                data = _history_data(page, expects_transcript=actor.transcript_expected)
            except Exception:
                data = {"events": [], "next_before_uuid": None, "error": "unavailable"}
            if (
                closed
                or manager.get(actor.session_id) is not actor
                or subscriptions.get(actor.session_id) != subscription
            ):
                return
            enqueue(
                {
                    "v": 2,
                    "type": "history.page",
                    "session_id": actor.session_id,
                    "generation": actor.generation,
                    "request_id": msg["request_id"],
                    "data": data,
                }
            )

        async def answer_card(msg: dict[str, Any], kind: str, generation: str) -> Envelope:
            """Record the user's choice, deliver it once, and report what the ledger holds.

            Every refusal is converted here. The generic dispatch fallback turns an unrecognised
            exception into `unknown`, which means "possibly delivered" — and an answer that was
            refused before anything was written was certainly not.
            """
            value = ChoiceInput(
                choice_id=msg["command_id"],
                session_id=msg["session_id"],
                generation=generation,
                tool_use_id=msg["tool_use_id"],
                native=NativeRequest(**msg["native_request"]),
                choice=(
                    QuestionChoice(answers=msg["answers"], response=msg.get("response", ""))
                    if kind == "question.answer"
                    else PermissionChoice(allow=msg["allow"])
                ),
            )
            try:
                summary, _created = await approvals.decide(value)
            except ApprovalDispatchRejected as exc:
                return receipt(msg, "rejected", exc.code)
            except ApprovalError as exc:
                return receipt(
                    msg,
                    "rejected",
                    "storage_unavailable" if exc.code in STORAGE_REFUSALS else exc.code,
                )
            except Exception:
                # decide() raises only when nothing was recorded, so an exception it does not
                # type itself is still a definite refusal here, never the generic dispatch
                # fallback's `unknown/internal`, which would claim "possibly delivered".
                return receipt(msg, "rejected", "storage_unavailable")
            if summary.state == "delivered_locally":
                return receipt(msg, "applied")
            if summary.state == "unknown":
                return receipt(msg, "unknown", summary.reason)
            return receipt(msg, "rejected", summary.reason)

        async def mutate(msg: dict[str, Any]) -> Envelope:
            sid, kind = msg["session_id"], msg["type"]
            actor = manager.get(sid)
            if actor is None:
                return receipt(msg, "rejected", "no_target")
            generation = msg["generation"]
            if actor.generation != generation:
                return receipt(msg, "rejected", "stale_generation")

            def current() -> bool:
                return manager.get(sid) is actor

            guard: dict[str, Any] = {"expected_generation": generation, "current": current}
            if kind == "session.fork":
                exists = await asyncio.to_thread(catalog.exists, msg["fork_id"])
                origin = await asyncio.to_thread(manager.app_state.store.session_origin, sid)
                if (
                    not current()
                    or actor.generation != generation
                    or actor.command_context()["closed"]
                ):
                    return receipt(msg, "rejected", "stale_generation")
                if exists or manager.get(msg["fork_id"]) is not None:
                    return receipt(msg, "rejected", "conflict")
                if session_roots is not None:
                    # After the re-check, so an existing session never takes the source's root,
                    # and synchronous, like manager.fork's own store write: no await between.
                    try:
                        session_roots.copy_for_fork(sid, msg["fork_id"])
                    except StateError as exc:
                        if exc.code == "conflict":
                            return receipt(msg, "rejected", "conflict")
                        raise
                try:
                    fork = manager.fork(
                        sid, msg["fork_id"], source_profile=origin == "profile", agent_cli=True
                    )
                except StateError as exc:
                    failed = manager.get(msg["fork_id"])
                    if failed is not None:
                        failed.report_state_error(exc)
                    raise
                return receipt(msg, "applied", target=fork)
            if kind == "turn.send":
                handed_off = await actor.send(msg["text"], **guard)
                if handed_off is False:
                    return receipt(msg, "rejected", "not_sent")
                return receipt(msg, "applied" if handed_off else "unknown", "native_unconfirmed")
            if kind == "turn.interrupt":
                await actor.interrupt(**guard)
                return receipt(msg, "unknown", "native_unconfirmed")
            if kind in {"question.answer", "permission.respond"}:
                # These two messages are a compatibility transport now: the same seam the REST
                # route uses records the choice and commits its delivery intent first, so no
                # transport can hand the driver a user choice that leaves no trace.
                return await answer_card(msg, kind, generation)
            if kind == "session.set_model":
                await actor.set_model(msg["model"], **guard)
                return receipt(msg, "unknown", "native_unconfirmed")
            if kind == "session.set_effort":
                if msg["effort"] not in EFFORTS:
                    return receipt(msg, "rejected", "invalid_effort")
                saved = actor.set_effort(msg["effort"], **guard)
                return receipt(
                    msg, "applied" if saved else "unknown", "" if saved else "storage_unavailable"
                )
            if kind == "session.set_mode":
                if msg["mode"] not in PERMISSION_MODES:
                    return receipt(msg, "rejected", "invalid_mode")
                await actor.set_mode(msg["mode"], **guard)
                return receipt(msg, "unknown", "native_unconfirmed")
            if kind == "session.close":
                await manager.close(sid, expected_turn_id=msg.get("turn_id"), **guard)
                old = subs.pop(sid, None)
                if old:
                    old()
                subscriptions.pop(sid, None)
                return receipt(msg, "applied")
            return receipt(msg, "rejected", "invalid_command")

        async def dispatch(type_: str, msg: dict[str, Any]) -> None:
            result = None
            try:
                if type_ == "session.open":
                    await session_open(msg)
                elif type_ == "session.detach":
                    sid = msg["session_id"]
                    if subscriptions.get(sid) == msg["request_id"]:
                        subscriptions.pop(sid, None)
                        old = subs.pop(sid, None)
                        if old:
                            old()
                elif type_ == "history.more":
                    await history_more(msg)
                elif "command_id" in msg:
                    result = await mutate(msg)
                elif type_ == "open.url":
                    if _valid_open_url(msg["url"]):
                        await asyncio.to_thread(opener, msg["url"])
                elif type_ == "open.vault":
                    target = await asyncio.to_thread(_vault_target, vault, msg["path"])
                    if target is not None:
                        await asyncio.to_thread(opener, _vscode_file_url(target))
                elif type_ == "open.code":
                    if code_files is not None:
                        target = await asyncio.to_thread(
                            code_files.open_target, msg["workspace_id"], msg["path"]
                        )
                        if target is not None:
                            line = f":{msg['line']}" if "line" in msg else ""
                            await asyncio.to_thread(opener, _vscode_file_url(target, line))
                elif type_ == "open.bot_memory":
                    if memory_area is not None:
                        target = await asyncio.to_thread(
                            memory_area.open_target, msg["workspace_id"], msg["path"]
                        )
                        if target is not None:
                            await asyncio.to_thread(opener, _vscode_file_url(target))
                elif type_ == "open.transcript":
                    record = await asyncio.to_thread(catalog.transcript_path, msg["session_id"])
                    if record is not None:
                        await asyncio.to_thread(opener, _vscode_file_url(record))
            except CommandDispatchRejected as exc:
                if "command_id" in msg:
                    result = receipt(msg, "rejected", exc.code)
            except StateError:
                if "command_id" in msg:
                    result = receipt(msg, "unknown", "storage_unavailable")
            except asyncio.CancelledError:
                if "command_id" in msg:
                    result = receipt(msg, "unknown", "cancelled")
                raise
            except Exception:
                if "command_id" in msg:
                    result = receipt(msg, "unknown", "internal")
                else:
                    logger.warning("socket read failed")
            finally:
                if result is not None:
                    command_id = msg["command_id"]
                    receipts[command_id] = (receipts[command_id][0], result)
                    enqueue(result)

        async def writer() -> None:
            nonlocal queued_bytes
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        await websocket.close(code=RESYNC_REQUIRED)
                        return
                    encoded, size = item
                    try:
                        with anyio.fail_after(SOCKET_WRITE_TIMEOUT_S):
                            await websocket.send_text(encoded)
                    finally:
                        queued_bytes -= size
                        space_available.set()
            except WebSocketDisconnect:
                pass
            except Exception:
                with contextlib.suppress(Exception):
                    await websocket.close(code=RESYNC_REQUIRED)

        writer_task = asyncio.create_task(writer())
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                raw = None
                text = message.get("text")
                try:
                    if text is None or len(text.encode("utf-8")) > WS_MAX_SIZE:
                        raise ValueError
                    raw = json.loads(text, object_pairs_hook=unique_object)
                except ValueError, RecursionError:
                    _log_dropped(None)
                    continue
                parsed = _validate(raw)
                if parsed is None or parsed[0] == "auth":
                    _log_dropped(raw)
                    continue
                type_, msg = parsed
                if "command_id" in msg:
                    command_id = msg["command_id"]
                    fingerprint = hashlib.sha256(
                        json.dumps(msg, sort_keys=True, ensure_ascii=True).encode()
                    ).hexdigest()
                    previous = receipts.get(command_id)
                    if previous is not None:
                        old_hash, old_receipt = previous
                        if old_hash != fingerprint:
                            enqueue(receipt(msg, "rejected", "conflict"))
                        elif old_receipt is not None:
                            enqueue(old_receipt)
                        else:
                            enqueue(receipt(msg, "unknown", "in_progress"))
                        continue
                    if len(receipts) >= MAX_SOCKET_RECEIPTS:
                        await websocket.close(code=RESYNC_REQUIRED)
                        break
                    receipts[command_id] = (fingerprint, None)
                # A connect's burst of opens and first history asks passes MAX_SOCKET_COMMANDS
                # once many chats run, so both have their own bound (MAX_SOCKET_OPENS).
                syncing = type_ in ("session.open", "history.more")
                if syncing:
                    tasks, full = open_tasks, len(open_tasks) >= MAX_SOCKET_OPENS
                else:
                    tasks = local_tasks
                    full = (
                        len(local_tasks) >= MAX_SOCKET_COMMANDS
                        or len(background) - len(opening) >= MAX_APP_SOCKET_COMMANDS
                    )
                if full:
                    if "command_id" in msg:
                        result = receipt(msg, "rejected", "overloaded")
                        receipts[msg["command_id"]] = (receipts[msg["command_id"]][0], result)
                        enqueue(result)
                    else:
                        await websocket.close(code=RESYNC_REQUIRED)
                        break
                    continue
                task = asyncio.create_task(dispatch(type_, msg))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                if syncing:
                    opening.add(task)
                    task.add_done_callback(opening.discard)
                _track(task)
        finally:
            closed = True
            space_available.set()
            unsub_vault()
            for unsub in subs.values():
                unsub()
            writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await writer_task

    async def _authenticate(websocket: WebSocket) -> bool:
        try:
            with anyio.fail_after(auth_timeout_s):
                message = await websocket.receive()
        except TimeoutError:
            await websocket.close(code=UNAUTHORIZED)
            return False
        if message["type"] == "websocket.disconnect":
            return False
        ok = False
        raw = None
        text = message.get("text")
        if text is not None:
            try:
                raw = json.loads(text, object_pairs_hook=unique_object)
            except ValueError, RecursionError:
                raw = None
            if isinstance(raw, dict) and raw.get("type") == "auth":
                candidate = raw.get("token")
                if isinstance(candidate, str):  # bytes: str compare_digest rejects non-ASCII
                    try:
                        ok = secrets.compare_digest(candidate.encode(), token.encode())
                    except UnicodeEncodeError:
                        ok = False
        if not ok:
            await websocket.close(code=UNAUTHORIZED)
            return False
        if not isinstance(raw, dict) or type(raw.get("v")) is not int or raw["v"] != events.VERSION:
            await websocket.close(code=PROTOCOL_MISMATCH)
            return False
        return True

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        # Before serving: an earlier launch's generated MCP config files go. Logs, never raises.
        await asyncio.to_thread(sweep_mcp_directory, config.data_dir / "mcp")
        await asyncio.to_thread(sweep_mcp_directory, config.data_dir / "bot-memory-settings")
        try:
            # A launch that was killed leaves its Magpie running on macOS and Linux.
            await magpie.reap_leftover()
        except Exception:
            logger.warning("magpie leftover check unavailable")
        if publications is not None:
            try:
                await publications.recover_pending()
            except Exception:
                logger.warning("vault publication recovery unavailable")
        if lifecycle is not None:
            try:
                await lifecycle.reconcile()
            except Exception:
                logger.warning("vault operation recovery unavailable")
        if recovery is not None:
            try:
                # Like the assets folder, the recovery folder exists only after a first delete:
                # a sweep would create it, so a launch with nothing kept writes nothing.
                if await asyncio.to_thread(recovery.root.exists):
                    await asyncio.to_thread(recovery.sweep, datetime.now(UTC))
            except Exception:
                logger.warning("vault recovery sweep unavailable")
        worktree_sweep_task: asyncio.Task[None] | None = None
        if worktree_service is not None:
            try:
                await worktree_service.recover()
            except Exception:
                logger.warning("worktree recovery unavailable")
            worktree_sweep_task = asyncio.create_task(worktree_service.run_sweeps(worktree_sweep_s))
        if check_runner is not None:
            try:
                await check_runner.recover()
            except Exception:
                logger.warning("check recovery unavailable")
        if preview_windows is not None:
            try:
                await asyncio.to_thread(preview_windows.sweep)
            except Exception:
                logger.warning("preview profile sweep unavailable")
        if decision_missions is not None and decision_engine is not None:
            # VIKI's chats an earlier launch left open end before the engine can start any.
            try:
                await decision_missions.reconcile()
            except Exception:
                logger.warning("decision mission recovery unavailable")
            decision_engine.start()
        try:
            if bots is not None:
                await storage_call(bots.recover_deliveries)
            await tasks.reconcile()
        except Exception:
            # Without a reconciled ledger a dead launch's open attempt could hold a slot, so
            # nothing is scheduled or run this launch.
            logger.warning("routine recovery unavailable")
        else:
            scheduler.start()
        try:
            await _poll_vault_once(hub, manager)
        except Exception:  # a bad registry file must not stop the app from starting
            logger.warning("vault session poll failed")
        poll_task = asyncio.create_task(_poll_vault_loop(hub, manager, vault_poll_s))

        async def collect_assets() -> None:
            services: dict[str, AssetService] = {}
            cursor = ""
            while True:
                # A fresh text-only installation has no media to collect. Keep its
                # startup read-only until the first deliberate upload creates storage.
                if await asyncio.to_thread((config.data_dir / "assets").exists):
                    try:
                        await assets.collect_due()
                    except Exception:
                        logger.warning("asset retention pass unavailable")
                    # Like the assets folder, the avatars folder exists only after a first save.
                    if await asyncio.to_thread((config.data_dir / "assets" / "avatars").exists):
                        try:
                            await avatars.collect_due()
                        except Exception:
                            logger.warning("avatar retention pass unavailable")
                    try:
                        cursor = await collect_scopes(
                            config.data_dir / "assets",
                            manager.app_state.store,
                            services,
                            cursor,
                            skip=scope,
                        )
                    except Exception:
                        logger.warning("asset retention pass unavailable")
                await asyncio.sleep(asset_poll_s)

        asset_collection_task = asyncio.create_task(collect_assets())
        try:
            if windows is not None:
                await windows.start()
                scheduler.wake()  # the loop's first turn saw no window, and nothing else wakes it
                if decision_engine is not None:
                    decision_engine.wake("app_open")  # its first cycle saw no window either
            reconnect_saved()
            yield
        finally:
            # First, the Bot usage capture: a running one stops before its next write
            # transaction, and one started from here returns at once.
            if usage is not None:
                usage.stop()
            # Next, before any await: on macOS and Linux a stop that is killed part-way leaves
            # Magpie running, and from here no closing chat may start it again.
            magpie.stop()
            close_bots()
            messaging_runtime.disarm_all()

            async def cleanup() -> None:
                if decision_engine is not None:
                    # First, so no cycle starts one of VIKI's chats while the chats close.
                    await decision_engine.stop()
                if inbox_host is not None:
                    # The engine's stop leaves a suggestion start in flight (it is shielded):
                    # cancel it here, before close_all(), so it creates no chat during shutdown.
                    await inbox_host.close()
                if decision_client is not None:
                    try:
                        await decision_client.close()
                    except Exception:
                        logger.warning("decision model cleanup unavailable")
                try:
                    await messaging_runtime.close()
                except Exception:
                    logger.warning("messaging cleanup unavailable (unavailable)")
                if windows is not None:
                    await windows.stop()
                for task in list(window_workers):
                    task.cancel()
                if window_workers:
                    await asyncio.gather(*window_workers, return_exceptions=True)
                await voice.close()
                await pack.close()
                if check_runner is not None:
                    await check_runner.close()
                if previews is not None:
                    await previews.close()
                if preview_windows is not None:
                    await preview_windows.close_all()
                poll_task.cancel()
                asset_collection_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_task
                with contextlib.suppress(asyncio.CancelledError):
                    await asset_collection_task
                await scheduler.close()
                await commands.close()
                # Before close_all() denies the cards that are left: a choice already recorded must
                # reach its own finalization rather than racing an app-generated denial.
                await approvals.close()
                for task in list(background):
                    task.cancel()
                if background:
                    await asyncio.gather(*background, return_exceptions=True)
                if worktree_sweep_task is not None:
                    worktree_sweep_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await worktree_sweep_task
                try:
                    await manager.close_all()
                finally:
                    await magpie.close()
                await tasks.close()
                if draft_workers:
                    await asyncio.gather(*draft_workers, return_exceptions=True)
                for pending in (
                    command_workers,
                    approval_workers,
                    settings_workers,
                    workspace_workers,
                    diagnostics_workers,
                    data_workers,
                    asset_workers,
                    avatar_workers,
                    metadata_workers,
                    archive_workers,
                    conversation_workers,
                    vault_workers,
                    profile_workers,
                    routine_workers,
                    voice_workers,
                    messaging_workers,
                    bot_workers,
                    agent_workers,
                ):
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)

            await _finish_cleanup(cleanup())

    routes: list[BaseRoute] = [
        Route("/api/sessions", sessions, methods=["GET"]),
        Route("/api/search", search, methods=["GET"]),
        Route("/api/rails", rails_route, methods=["GET"]),
        WebSocketRoute("/ws", ws_endpoint),
    ]
    routes.extend(
        command_routes(
            commands,
            authorized=lambda request: _bearer_ok(request, token),
            origins=ORIGINS | (DEV_ORIGINS if dev else frozenset()),
            workers=command_workers,
        )
    )
    routes.extend(
        approval_routes(
            approvals,
            authorized=lambda request: _bearer_ok(request, token),
            origins=ORIGINS | (DEV_ORIGINS if dev else frozenset()),
            workers=approval_workers,
        )
    )
    routes.extend(
        draft_routes(
            DraftService(manager.app_state, vault),
            authorized=lambda request: _bearer_ok(request, token),
            origins=ORIGINS | (DEV_ORIGINS if dev else frozenset()),
            workers=draft_workers,
        )
    )

    def authorized(request: Request) -> bool:
        return _bearer_ok(request, token)

    origins = ORIGINS | (DEV_ORIGINS if dev else frozenset())
    routes.extend(magpie_routes(magpie, authorized=authorized, origins=origins))

    def own_transcript(session_id: str) -> tuple[str, str | None]:
        """The tool-sources lookup's resolver. The chips ask about the open chat's own turns,
        this launch's included, so there is no `until` cut: the session's own transcript,
        or a fork's source while the fork's own file does not exist yet."""
        actor = manager.get(session_id)
        if actor is None or catalog.exists(session_id):
            return (session_id, None)
        return (actor.history_source, None)

    routes.extend(
        conversation_search_routes(
            conversation_search,
            authorized=authorized,
            origins=origins,
            workers=conversation_workers,
            artifacts=ArtifactDetails(catalog, conversation_source),
            tool_sources=ToolSources(catalog, own_transcript),
        )
    )
    routes.extend(
        subagent_routes(
            catalog, manager, authorized=authorized, origins=origins, workers=conversation_workers
        )
    )
    routes.extend(
        between_chats_routes(
            lambda: between_chats(catalog),
            authorized=authorized,
            origins=origins,
            workers=conversation_workers,
        )
    )
    routes.extend(run_routes(manager, authorized=authorized, origins=origins))
    routes.extend(
        asset_routes(
            assets,
            authorized=authorized,
            origins=origins,
            workers=asset_workers,
            image_input=image_input,
        )
    )
    routes.extend(
        avatar_routes(avatars, authorized=authorized, origins=origins, workers=avatar_workers)
    )
    routes.extend(
        session_metadata_routes(
            SessionMetadataService(manager.app_state.store, scope),
            authorized=authorized,
            origins=origins,
            workers=metadata_workers,
            copies_released=copies_released,
        )
    )
    routes.extend(
        session_window_routes(
            windows,
            available=window_target_available,
            authorized=authorized,
            origins=origins,
            workers=window_workers,
        )
    )
    routes.extend(
        conversation_archive_routes(
            ConversationArchiveService(manager.app_state.store, scope, assets.inspect_bytes),
            authorized=authorized,
            origins=origins,
            workers=archive_workers,
        )
    )
    # Always registers: a running chat's live tool inventory.
    routes.extend(
        inventory_routes(manager, authorized=authorized, origins=origins, workers=profile_workers)
    )
    routes.extend(
        mcp_routes(manager, authorized=authorized, origins=origins, workers=profile_workers)
    )
    routes.extend(
        context_routes(manager, authorized=authorized, origins=origins, workers=profile_workers)
    )
    routes.extend(limits_routes(plan_limits, authorized=authorized, origins=origins))
    routes.extend(
        session_prefs_routes(
            manager.app_state,
            manager,
            catalog.exists,
            authorized=authorized,
            origins=origins,
            workers=profile_workers,
        )
    )
    # Registers with `workspaces`: the native agent and skill sources and their catalogs.
    if native_inspector is not None:
        routes.extend(
            native_asset_routes(
                native_inspector,
                native_sources,
                authorized=authorized,
                origins=origins,
                workers=profile_workers,
            )
        )
    routes.extend(
        profile_routes(
            profiles,
            authorized=authorized,
            origins=origins,
            workers=profile_workers,
            start_preview=prepare_profile if profile_launch is not None else None,
            provider_view=provider_view if readiness is not None else None,
            inspect_session=inspect_profile_session if profile_launch is not None else None,
        )
    )
    routes.extend(
        routine_routes(
            routines,
            tasks,
            scheduler,
            authorized=authorized,
            origins=origins,
            workers=routine_workers,
        )
    )
    if bots is not None and bot_runtime is not None:
        routes.extend(
            bot_routes(
                bots,
                authorized=authorized,
                origins=origins,
                workers=bot_workers,
                admission_lock=admission_lock,
                prepare_chat=prepare_bot_chat,
                chat_status=bot_chat_status,
                busy=bot_chat_busy,
                scheduler_status=scheduler.status,
                delivery_status=bot_runtime.delivery_status,
                set_delivery=bot_runtime.set_delivery,
                revoke=bot_runtime.revoke,
                wake=scheduler.wake,
                copies_released=copies_released,
            )
        )
    if usage_views is not None:
        routes.extend(
            bot_usage_routes(
                usage_views, authorized=authorized, origins=origins, workers=bot_workers
            )
        )
    if settings is not None:
        assert model_connections is not None
        model_gateway = Gateway(before_request=magpie.ensure_connection)
        manager.bind_model_connections(model_connections, model_gateway)
        routes.extend(model_gateway.routes())
        routes.extend(
            connection_routes(
                model_connections,
                model_gateway,
                authorized=authorized,
                origins=origins,
            )
        )
        routes.extend(
            settings_routes(
                settings, authorized=authorized, origins=origins, workers=settings_workers
            )
        )
        if agent_cli is not None:
            assert workspaces is not None
            grants = AgentGrants(agent_cli)
            changes = AgentChanges(grants.live)
            manager.bind_agent_cli(grants)
            routes.extend(
                agent_routes(
                    grants,
                    changes,
                    settings=settings,
                    routines=routines,
                    runner=tasks,
                    scheduler=scheduler,
                    bots=bots,
                    profiles=profiles,
                    workspaces=workspaces,
                    admission_lock=admission_lock,
                    app_version=app_version,
                    workers=agent_workers,
                    usage=usage_views,
                )
            )
            routes.extend(
                changes.routes(authorized=authorized, origins=origins, workers=agent_workers)
            )
    if workspaces is not None:
        reader = VaultService(workspaces)
        # Bound once, here: the active workspace is resolved once per launch and a session reads
        # the pair when it is created, so without this every turn with excerpt parts is refused.
        manager.bind_excerpts(reader, workspaces.resolve_active().workspace.workspace_id)
        # The proposals come first: the decision inbox lists the vault and skill proposals they
        # produce.
        proposals = ProposalService(workspaces, manager.app_state.store, reader)
        # `native_inspector` is always set here too (both follow the same `workspaces is not
        # None` launch check, above); the guard only tells the type checker so.
        skill_proposals: SkillProposalService | None = None
        if native_inspector is not None:
            skill_proposals = SkillProposalService(
                workspaces, manager.app_state.store, native_inspector
            )

        def proposal_items() -> list[dict[str, Any]]:
            items = list(proposals.inbox_items())
            if skill_proposals is not None:
                items.extend(skill_proposals.inbox_items())
            # Then the active workspace's question wording proposals and its vault-change
            # proposals, kept by the Decision engine built below, before any request can read
            # this inbox. While either cannot be read (a `DecisionLoopError`) the rest is still
            # listed.
            if decision_engine is not None:
                scope = workspaces.resolve_active().workspace.workspace_id
                with contextlib.suppress(DecisionLoopError):
                    items.extend(decision_engine.proposals.inbox_items(scope))
                with contextlib.suppress(DecisionLoopError):
                    items.extend(decision_engine.changes.inbox_items(scope))
            return items

        decisions = DecisionService(workspaces, proposal_items=proposal_items)
        captures = CaptureService(workspaces, reader)
        suggester = CaptureSuggester(manager, captures, tasks=background)
        # Decision's cycle and its Suggest destinations route start the user's own suggestion run
        # through this one suggester, so the run lock and the kept suggestions are shared.
        inbox_host = SuggestionHost(suggester)
        routes.extend(
            vault_routes(
                reader,
                authorized=authorized,
                origins=origins,
                workers=vault_workers,
            )
        )
        # The Bot memory Claude Code keeps under the data folder for non-ordinary vaults.
        memory_area = BotMemoryArea(workspaces, config.data_dir)
        routes.extend(
            bot_memory_routes(
                memory_area, authorized=authorized, origins=origins, workers=vault_workers
            )
        )
        code_files = CodeFiles(workspaces)
        # The one CodeGit: every app Git call checks a code folder's Bot-copy baseline, Bot
        # grants included (bot_routes, built above, reaches it through the bound service).
        code_git = CodeGit(
            workspaces,
            code_files,
            git_available=lambda: readiness is not None and readiness.git_state == "ready",
            bot_code_store=manager.app_state.store,
        )
        if bots is not None:
            bots.bind_code_git(code_git)
        # A code folder's repository config is untrusted, so the rails chip reads it hardened,
        # and only through its settings check once the folder has a Bot-copy baseline.
        if workspaces.resolve_active().workspace.kind == "code":
            rails_git = code_git
        routes.extend(
            code_routes(
                code_files, code_git, authorized=authorized, origins=origins, workers=vault_workers
            )
        )
        session_roots = SessionRoots(
            manager.app_state.store,
            workspaces,
            exists=catalog.exists,
            actor=manager.get,
            ticked=settings.workspace_ids if settings is not None else tuple,
        )
        manager.bind_roots(
            session_roots.resolve, context=session_roots.context, folders=session_roots.folders
        )
        catalog.bind_roots(session_roots)
        check_runner = CheckRunner(manager.app_state.store, workspaces, code_git, session_roots)
        # Decision starts saved checks through the routes' own runner, so the root gate and the
        # run limit are the Checks tab's.
        check_host = SavedCheckHost(manager.app_state.store, workspaces, code_git, check_runner)
        # List, create and start pass the root gate (`code_checks.trusted_root`) first.
        routes.extend(
            check_routes(
                CheckService(manager.app_state.store, workspaces),
                check_runner,
                authorized=authorized,
                origins=origins,
                workers=vault_workers,
            )
        )
        pending_terminal_launches: dict[str, int] = {}
        routes.extend(
            terminal_launch_routes(
                manager.app_state.store,
                workspaces,
                session_roots,
                authorized=authorized,
                origins=origins,
                workers=vault_workers,
                pending=pending_terminal_launches,
            )
        )
        preview_windows = PreviewWindows(
            config.edge, config.data_dir, spawn=preview_spawn or winproc.spawn
        )
        previews = PreviewService(
            manager.app_state.store, workspaces, session_roots, preview_windows
        )
        routes.extend(
            preview_routes(previews, authorized=authorized, origins=origins, workers=vault_workers)
        )
        if coordinator is not None and resolve_root is not None:
            worktree_service = WorktreeService(
                manager.app_state.store,
                workspaces,
                code_git,
                coordinator,
                resolve_root,
                session_roots,
                area=worktree_area(config.data_dir),
                # A Bot copy's driver counts until its own close completes, even once a deletion
                # erased the membership `live_in` reads (SessionManager.code_copy_in_use).
                in_use=lambda worktree_id: (
                    check_runner.in_worktree(worktree_id)
                    or previews.in_worktree(worktree_id)
                    or worktree_id in pending_terminal_launches
                    or manager.code_copy_in_use(worktree_id)
                ),
                enabled=lambda: (
                    settings is not None and settings.flag("workbench.worktree_cleanup") is True
                ),
            )
            # The Bot-copy spawn hook: makes a granted Bot chat's copy on its first send and
            # rechecks a bound copy before every spawn, with this one worktree service.
            bot_code = BotCode(
                manager.app_state.store,
                workspaces,
                session_roots,
                profiles,
                BotThreadService(manager.app_state.store, workspaces),
                actor=manager.get,
                area=worktree_area(config.data_dir),
                worktrees=worktree_service,
            )
            manager.bind_bot_code(bot_code.resolve)
            # The spawn check (`SessionRoots.resolve`, at every spawn) decides where a session
            # runs; the session-roots POST only records the user's choice.
            routes.extend(
                root_routes(
                    session_roots,
                    worktree_service,
                    authorized=authorized,
                    origins=origins,
                    workers=vault_workers,
                    describe=bot_code.describe,
                    git_settings=GitSettings(
                        workspaces,
                        manager.app_state.store,
                        code_git,
                        coordinator,
                        resolve_root,
                        admission_lock=admission_lock,
                    ),
                )
            )
            review = CodeReview(manager.app_state.store, code_git)

            def turn_running(session_id: str) -> bool:
                actor = manager.get(session_id)
                return actor is not None and actor.busy

            # Every route resolves trust first (`CodeGit.repository`), before any Git call.
            routes.extend(
                integration_routes(
                    review,
                    CodeCommitter(
                        review,
                        code_git,
                        manager.app_state.store,
                        coordinator,
                        resolve_root,
                        busy=turn_running,
                    ),
                    CodeIntegration(
                        code_git,
                        manager.app_state.store,
                        coordinator,
                        resolve_root,
                        busy=turn_running,
                    ),
                    authorized=authorized,
                    origins=origins,
                    workers=vault_workers,
                )
            )
        routes.extend(
            bookmark_routes(
                BookmarkService(manager.app_state, workspaces),
                authorized=authorized,
                origins=origins,
                workers=vault_workers,
            )
        )
        routes.extend(
            decision_routes(
                decisions, authorized=authorized, origins=origins, workers=vault_workers
            )
        )
        routes.extend(
            proposal_routes(
                proposals, authorized=authorized, origins=origins, workers=vault_workers
            )
        )
        if skill_proposals is not None:
            routes.extend(
                skill_proposal_routes(
                    skill_proposals, authorized=authorized, origins=origins, workers=vault_workers
                )
            )
        routes.extend(
            capture_routes(
                captures, suggester, authorized=authorized, origins=origins, workers=vault_workers
            )
        )
        loop_clock = decision_clock or SystemClock()

        def read_decision_settings() -> DecisionSettings:
            return parse_settings(settings.read().effective if settings is not None else {})

        decision_client = (
            decision_model if decision_model is not None else DecisionModel(TypeSafeHttp())
        )
        decision_agenda = DecisionAgenda(manager.app_state.store)
        decision_keys = DecisionKeyStore(manager.app_state.store, now=loop_clock.wall)
        decision_presence = PresenceTracker(monotonic=loop_clock.monotonic)
        look_roots = session_roots

        async def look_root(session_id: str, workspace_id: str) -> bool:
            # A Look chat runs only in a code folder the user trusted; an untrusted, moved or
            # unreadable one is a refusal the mission reports, not an error.
            try:
                await look_roots.prepare(session_id, workspace_id, None)
            except RootError, StateError:
                return False
            return True

        decision_missions = MissionService(
            manager.app_state.store,
            manager,
            read_decision_settings,
            wall=loop_clock.wall,
            roots=look_root,
        )
        vault_look = (
            VaultLookReader(
                PublicationJournal(manager.app_state.store),
                OperationJournal(manager.app_state.store),
                recovery,
                manager.app_state.store,
                reader,
            )
            if recovery is not None
            else None
        )
        decision_engine = DecisionLoopEngine(
            decision_store,
            decision_agenda,
            decision_keys,
            decision_client,
            decision_missions,
            decision_presence,
            read_decision_settings,
            functools.partial(
                detectors.gather,
                workspaces,
                reader,
                decisions,
                catalog=native_inspector.inspect if native_inspector is not None else None,
                look=vault_look.read if vault_look is not None else None,
            ),
            detectors.detect,
            clock=loop_clock,
            attended=attended,
            plan_reading=plan_limits.latest,
            seats=seat_host,
            looks=AppLookReader(
                manager, commands.service, messaging=lambda: messaging_runtime.status()
            ),
            code=CodeLookReader(workspaces, code_git),
            checks=check_host,
            inbox=inbox_host,
        )
        tasks.bind_cards(lambda: decision_engine.wake("change"))
        routes.extend(
            decision_loop_routes(
                DecisionLoopService(
                    workspaces,
                    decision_engine,
                    decision_store,
                    decision_agenda,
                    DecisionLoopStats(manager.app_state.store),
                    decision_keys,
                    decision_missions,
                    decision_presence,
                    read_decision_settings,
                    now=loop_clock.wall,
                    plan_reading=plan_limits.latest,
                    seats=seat_host,
                    checks=check_host,
                    inbox=inbox_host,
                ),
                authorized=authorized,
                origins=origins,
                workers=vault_workers,
            )
        )
        if publications is not None and lifecycle is not None and recovery is not None:
            # A note save waits for the lease as long as every other vault writer does.
            routes.extend(
                vault_write_routes(
                    publications,
                    authorized=authorized,
                    origins=origins,
                    workers=vault_workers,
                    lease_wait_s=VAULT_LEASE_WAIT_S,
                )
            )
            routes.extend(
                code_save_routes(
                    CodeSaver(workspaces, publications),
                    authorized=authorized,
                    origins=origins,
                    workers=vault_workers,
                )
            )
            routes.extend(
                vault_ops_routes(
                    lifecycle,
                    recovery,
                    publications,
                    authorized=authorized,
                    origins=origins,
                    workers=vault_workers,
                    lease_wait_s=VAULT_LEASE_WAIT_S,
                )
            )
        if (
            publications is not None
            and coordinator is not None
            and resolve_root is not None
            and denied_roots is not None
        ):

            @contextlib.asynccontextmanager
            async def backup_lease(workspace: Workspace) -> AsyncIterator[object]:
                # A backup copies a whole vault, so in a vault a save, an operation, a keep
                # or a memory write can reach it holds the whole root (no refs) on the shared
                # coordinator, with the same deadline, and no note changes mid-copy. Every one of
                # those writers passes the same stored gate and resolver, so a vault the gate
                # does not admit, or the resolver refuses `ordinary_workspace_required` (one
                # sharing identity with a protected vault), takes no app write: there is nothing
                # to wait for, and it is copied unleased under every policy. Any other
                # refusal, `workspace_changed` or the lease's `vault_locked`, is the backup's.
                root = None
                if admits_note_writes(workspace):
                    try:
                        root = await asyncio.to_thread(
                            resolve_root,
                            workspace.workspace_id,
                            workspace.policy_revision,
                            workspace.root.physical_id,
                        )
                    except MutationError as exc:
                        if exc.code != "ordinary_workspace_required":
                            raise
                if root is None:
                    yield None
                    return
                async with coordinator.lease(root, (), wait_s=VAULT_LEASE_WAIT_S) as lease:
                    yield lease

            routes.extend(
                vault_knowledge_routes(
                    VaultAttachmentService(
                        workspaces,
                        assets,
                        publications,
                        resolve_root,
                        coordinator,
                        lease_wait_s=VAULT_LEASE_WAIT_S,
                    ),
                    MemoryService(
                        workspaces,
                        manager.app_state.store,
                        reader,
                        publications,
                        proposals,
                        lease_wait_s=VAULT_LEASE_WAIT_S,
                    ),
                    VaultBackupService(
                        workspaces,
                        lease=backup_lease,
                        denied_roots=denied_roots,
                        app_version=app_version,
                    ),
                    BackupRestoreService(workspaces, denied_roots=denied_roots),
                    authorized=authorized,
                    origins=origins,
                    workers=vault_workers,
                )
            )
    if workspaces is not None and bootstrap is not None:
        routes.extend(
            workspace_routes(
                workspaces,
                bootstrap,
                authorized=authorized,
                origins=origins,
                workers=workspace_workers,
                # The dialog belongs to the app window the user is in, when one is open.
                pick=lambda title, multiple: folder_dialog.pick(
                    title,
                    multiple,
                    folder_dialog.owner_window(windows.holds() if windows is not None else ()),
                ),
                picker_status=folder_dialog.status,
            )
        )
    if checks is not None and readiness is not None and diagnostics_document is not None:
        routes.extend(
            diagnostics_routes(
                checks,
                capabilities=capabilities,
                document=diagnostics_document,
                authorized=authorized,
                origins=origins,
                workers=diagnostics_workers,
            )
        )
    if readiness is not None:
        ready = readiness  # the narrowed type, for the lambda
        # The sign-in console starts the CLI, so each click asks the spawn predicate before it does.
        routes.extend(
            provider_login_routes(
                cli=lambda: config.cli if ready.may_spawn() else None,
                authorized=authorized,
                origins=origins,
                workers=diagnostics_workers,
            )
        )
    if data is not None:
        routes.extend(
            data_routes(
                *data,
                authorized=authorized,
                origins=origins,
                workers=data_workers,
                copies_released=copies_released,
            )
        )
    routes.extend(
        voice_routes(
            voice,
            authorized=authorized,
            origins=origins,
            workers=voice_workers,
            authenticate=_authenticate,
            voicebox=voicebox,
            pack=pack,
            opener=opener,
        )
    )

    def invalidate_messaging_root() -> None:
        messaging_runtime.invalidate("mattermost")
        messaging_runtime.invalidate("slack")
        messaging_runtime.invalidate("discord")

    def pinned_root() -> Path:
        raise StartError("bad_request", "The startup workspace is unavailable. Restart VIKI Agent.")

    async def create_bot_actor() -> SessionActor:
        invalidate_messaging_root()
        raise StartError("bad_request", "The startup workspace is unavailable. Restart VIKI Agent.")

    root_available = False
    if workspaces is not None and session_roots is not None:
        spaces, roots = workspaces, session_roots
        captured = spaces.resolve_active().workspace

        def pinned_root() -> Path:
            try:
                if vault != Path(captured.root.display):
                    raise WorkspaceError("root_unavailable")
                current = (
                    spaces.get(captured.workspace_id, kind=None)
                    if captured.persistent
                    else captured
                )
                if (
                    current.kind != captured.kind
                    or current.policy != captured.policy
                    or current.policy_revision != captured.policy_revision
                    or current.revision != captured.revision
                    or current.root != captured.root
                    or (
                        current.kind == "code"
                        and (not current.persistent or current.policy != "ordinary")
                    )
                ):
                    raise WorkspaceError("root_unavailable")
                if captured.kind == "scratch":
                    check_file(vault / ".mattermost-root-check")
                    info = vault.stat()
                    if (
                        not vault.is_dir()
                        or f"{info.st_dev}:{info.st_ino}" != captured.root.physical_id
                    ):
                        raise WorkspaceError("root_unavailable")
                elif spaces.current_root(current) != captured.root:
                    raise WorkspaceError("root_unavailable")
                return vault
            except WorkspaceError, StateError, OSError:
                raise StartError(
                    "bad_request", "The startup workspace is unavailable. Restart VIKI Agent."
                ) from None

        async def resolve_bot_root(session_id: str) -> Path:
            try:
                if captured.kind == "code" and await roots.resolve(session_id) != vault:
                    raise StartError(
                        "bad_request", "The startup workspace is unavailable. Restart VIKI Agent."
                    )
                return await asyncio.to_thread(pinned_root)
            except StartError:
                # The root guard reads in a worker; authority is revoked here on the event loop.
                invalidate_messaging_root()
                raise

        async def create_bot_actor() -> SessionActor:
            session_id = str(uuid.uuid4())
            try:
                await asyncio.to_thread(pinned_root)
                if captured.kind == "code":
                    await roots.prepare(session_id, captured.workspace_id, None)
                # auto lets Claude Code's classifier review actions instead of DM cards; requests
                # it still makes reach the DM, and where auto is unavailable Claude Code starts in
                # Manual.
                await asyncio.to_thread(manager.app_state.remember, session_id, mode="auto")
                await resolve_bot_root(session_id)
                return manager.create(session_id, root_resolver=resolve_bot_root)
            except BaseException as error:
                if isinstance(error, StartError):
                    invalidate_messaging_root()
                await _finish_cleanup(manager.close(session_id))
                raise

        try:
            pinned_root()
        except StartError:
            pass
        else:
            bridge_workspace_id = captured.workspace_id
            root_available = True

    def require_messaging_root() -> None:
        try:
            pinned_root()
        except StartError:
            invalidate_messaging_root()
            raise

    def bridge_factory(provider_config: MessagingConfig) -> BridgeFactory:
        def make(on_disarm: Callable[[], None]) -> RemoteBot:
            require_messaging_root()
            match provider_config:
                case MattermostConfig():
                    return MattermostBot(
                        provider_config,
                        MattermostHttp(provider_config),
                        manager,
                        approvals,
                        create_actor=create_bot_actor,
                        on_disarm=on_disarm,
                    )
                case SlackConfig():
                    return RemoteBot(
                        SlackChannel(provider_config),
                        manager,
                        approvals,
                        create_actor=create_bot_actor,
                        on_disarm=on_disarm,
                    )
                case DiscordConfig():
                    return RemoteBot(
                        DiscordChannel(provider_config),
                        manager,
                        approvals,
                        create_actor=create_bot_actor,
                        on_disarm=on_disarm,
                    )

        return make

    messaging_settings = MessagingConfigStore(manager.app_state.store)

    def load_saved(provider: Provider) -> Saved | None:
        try:
            return messaging_settings.load(provider)
        except MessagingStoreError as error:
            logger.warning("%s saved settings unavailable (%s)", provider, error.code)
            return None

    saved: dict[Provider, Saved | None] = {
        provider: load_saved(provider) for provider in ("mattermost", "slack", "discord")
    }
    factories = {
        provider: bridge_factory(entry.config) if entry is not None else None
        for provider, entry in saved.items()
    }

    async def remember(provider: Provider, on: bool) -> None:
        """Store the startup workspace on Enable and clear it on Disable."""
        if on and bridge_workspace_id is None:
            raise RuntimeError("no_startup_workspace")
        await asyncio.to_thread(
            messaging_settings.remember, provider, bridge_workspace_id if on else None
        )

    def reconnected(provider: Provider, task: asyncio.Task[Any]) -> None:
        messaging_workers.discard(task)
        error = None if task.cancelled() else task.exception()
        if isinstance(error, MessagingError):
            logger.warning("%s reconnect unavailable (%s)", provider, error.code)

    def reconnect_saved() -> None:
        """A bot saved as on in this startup workspace reconnects through the normal Enable
        path; startup does not await it, and a refusal leaves the saved flag as it was."""
        for provider, entry in saved.items():
            if (
                entry is not None
                and bridge_workspace_id is not None
                and entry.enabled_workspace == bridge_workspace_id
            ):
                task = asyncio.create_task(messaging_runtime.set_enabled(provider, True))
                messaging_workers.add(task)
                task.add_done_callback(functools.partial(reconnected, provider))

    def cooldown_until(provider: Provider, bot: RemoteBot) -> float:
        if provider == "mattermost":
            return getattr(getattr(bot, "http", None), "cooldown_until", 0.0)
        if provider == "slack" and isinstance(bot.channel, SlackChannel):
            return bot.channel.http.cooldown_until
        if provider == "discord" and isinstance(bot.channel, DiscordChannel):
            return bot.channel.http.cooldown_until
        raise ValueError("invalid_messaging_channel")

    messaging_runtime = MessagingRuntime(
        mattermost=factories["mattermost"],
        slack=factories["slack"],
        discord=factories["discord"],
        revoke_mattermost=bot_runtime.revoke_all if bot_runtime is not None else lambda: None,
        cooldown_until=cooldown_until,
        remember=remember,
    )
    if not root_available:
        invalidate_messaging_root()

    def observe(actor: SessionActor, envelope: Envelope) -> None:
        """The manager's one observer: every envelope goes to the messaging runtime first, then
        a turn's end schedules a Bot usage capture of its chat, even when the runtime raised."""
        try:
            messaging_runtime.observe(actor, envelope)
        finally:
            if schedule_capture is not None and envelope["type"] == "turn.done":
                schedule_capture(actor.session_id)

    manager.bind_settle(messaging_runtime.disarm_all)
    manager.bind_observer(observe)
    routes.extend(
        messaging_routes(
            runtime=messaging_runtime,
            authorized=authorized,
            origins=origins,
            workers=messaging_workers,
        )
    )
    routes.extend(
        messaging_settings_routes(
            runtime=messaging_runtime,
            authorized=authorized,
            origins=origins,
            workers=messaging_workers,
            settings=messaging_settings,
            factory_for=bridge_factory,
        )
    )
    if static_dir is not None:
        routes.append(Mount("/", app=StaticFiles(directory=static_dir, html=True), name="static"))

    app = Starlette(
        routes=routes,
        middleware=[Middleware(SecurityMiddleware, dev=dev)],
        exception_handlers={Exception: _server_error},
        lifespan=lifespan,
    )
    app.state.commands = commands
    app.state.routines = routines
    app.state.routine_tasks = tasks
    app.state.bots = bots
    app.state.bot_runtime = bot_runtime
    app.state.messaging_runtime = messaging_runtime
    app.state.scheduler = scheduler
    app.state.approvals = approvals
    app.state.voice = voice
    # The lifespan's own stop runs only after the server has drained its connections;
    # `magpie.stop()` awaits nothing, so a stop that begins earlier can call it here.
    app.state.magpie = magpie
    return app


def create_recovery_app(
    *,
    config: Config,
    token: str,
    readiness: Readiness,
    checks: ProviderChecks,
    diagnostics_document: Callable[[], dict[str, object]],
    static_dir: Path | None,
    dev: bool = False,
    windows: WindowManager | None = None,
    storage_refusal: str = "recovery_required",
) -> Starlette:
    """The read-only shell served when the app store cannot be opened.

    A damaged or newer store is not an empty first run and never starts a session, so this app
    has no manager, catalog or socket; it serves the built frontend when supplied and exposes
    readiness and diagnostics routes. Writing that
    document out is an export, and an export needs the store this app could not open.
    `app.mode` is "recovery", which is how the shell knows to say so, and `storage_refusal` is
    the StateError code that refused the store, which is how it knows what to say.
    """
    workers: set[asyncio.Task[Any]] = set()

    def capabilities() -> Capabilities:
        return readiness.capabilities(
            None,
            storage_readiness(None, config.data_dir, refusal=storage_refusal),
            mode="recovery",
        )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette):
        if windows is not None:
            await windows.start()
        try:
            yield
        finally:
            if windows is not None:
                await windows.stop()
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)

    routes: list[BaseRoute] = [
        *diagnostics_routes(
            checks,
            capabilities=capabilities,
            document=diagnostics_document,
            authorized=lambda request: _bearer_ok(request, token),
            origins=ORIGINS | (DEV_ORIGINS if dev else frozenset()),
            workers=workers,
        ),
    ]
    if static_dir is not None:
        routes.append(
            Mount(
                "/",
                routes=[Route("/{path:path}", StaticFiles(directory=static_dir, html=True))],
                name="static",
            )
        )
    return Starlette(
        routes=routes,
        middleware=[Middleware(SecurityMiddleware, dev=dev)],
        exception_handlers={Exception: _server_error},
        lifespan=lifespan,
    )
