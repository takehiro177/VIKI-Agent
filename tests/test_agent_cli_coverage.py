"""AC-11's coverage ratchet: `agent_api.COVERS` stays inside the real window API surface (every
`/api/` route but the agent surface itself, the model gateway's runtime proxy and the window's
Apply routes), and the routes no `viki-agent` command reaches yet are exactly the frozen,
sorted list below. A route in `ACTIONS` counts once per action it serves."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Route

from tests.test_server import _p1_app
from viki_agent.app.agent_api import COVERS
from viki_agent.automation import routines_api

# (method, route path ending in `{action}`) -> the actions that method serves there (routines_api
# `perform`). Each action counts as its own route, so covering `enable` never claims `pause`.
ACTIONS: dict[tuple[str, str], frozenset[str]] = {
    ("GET", "/api/v2/routines/{routine_id}/{action}"): frozenset({"runs"}),
    ("POST", "/api/v2/routines/{routine_id}/{action}"): frozenset(
        {"run", "enable", "pause", "archive"}
    ),
}

# Generated once (2026-11-20) from _p1_app's live route set, with agent_cli set and each ACTIONS
# route counted per action, minus every pair agent_api.COVERS already claims. Entries may only
# be REMOVED, as a later viki-agent command gains coverage of one — never added: a new route
# with no CLI coverage is a gap to close, not a reason to widen this ratchet.
UNCOVERED: list[tuple[str, str]] = [
    ("DELETE", "/api/v2/bot-threads/{workspace_id}/{thread_id}"),
    ("DELETE", "/api/v2/bots/{bot_id}"),
    ("DELETE", "/api/v2/conversation-archives/{archive_id}"),
    ("DELETE", "/api/v2/decision/episodes"),
    ("DELETE", "/api/v2/decision/key"),
    ("DELETE", "/api/v2/llm/connections/{connection_id}"),
    ("DELETE", "/api/v2/messaging/discord/config"),
    ("DELETE", "/api/v2/messaging/mattermost/config"),
    ("DELETE", "/api/v2/messaging/slack/config"),
    ("DELETE", "/api/v2/session-metadata/{session_id}"),
    ("DELETE", "/api/v2/vaults/{workspace_id}/bookmarks/{ref:path}"),
    ("GET", "/api/rails"),
    ("GET", "/api/search"),
    ("GET", "/api/sessions"),
    ("GET", "/api/v2/approvals"),
    ("GET", "/api/v2/approvals/{choice_id}"),
    ("GET", "/api/v2/approvals/{choice_id}/choice"),
    ("GET", "/api/v2/assets/{asset_id}"),
    ("GET", "/api/v2/attachment-capabilities"),
    ("GET", "/api/v2/avatars/{avatar_id}/{size}"),
    ("GET", "/api/v2/between-chats"),
    ("GET", "/api/v2/bot-memory/{workspace_id}/entries"),
    ("GET", "/api/v2/bot-memory/{workspace_id}/notes/{ref:path}"),
    ("GET", "/api/v2/bot-threads/{workspace_id}"),
    ("GET", "/api/v2/bots/{bot_id}"),
    ("GET", "/api/v2/bots/{bot_id}/threads"),
    ("GET", "/api/v2/capabilities"),
    ("GET", "/api/v2/check-runs/{run_id}/output"),
    ("GET", "/api/v2/checks/{check_id}/runs"),
    ("GET", "/api/v2/code/{workspace_id}/changes"),
    ("GET", "/api/v2/code/{workspace_id}/checks"),
    ("GET", "/api/v2/code/{workspace_id}/diff"),
    ("GET", "/api/v2/code/{workspace_id}/file"),
    ("GET", "/api/v2/code/{workspace_id}/git-settings"),
    ("GET", "/api/v2/code/{workspace_id}/previews"),
    ("GET", "/api/v2/code/{workspace_id}/review"),
    ("GET", "/api/v2/code/{workspace_id}/review/file"),
    ("GET", "/api/v2/code/{workspace_id}/search"),
    ("GET", "/api/v2/code/{workspace_id}/tree"),
    ("GET", "/api/v2/code/{workspace_id}/worktree-archives"),
    ("GET", "/api/v2/code/{workspace_id}/worktree-cleanup"),
    ("GET", "/api/v2/commands"),
    ("GET", "/api/v2/commands/{command_id}"),
    ("GET", "/api/v2/commands/{command_id}/submission"),
    ("GET", "/api/v2/conversation-archives"),
    ("GET", "/api/v2/conversation-archives/{archive_id}"),
    ("GET", "/api/v2/data/locations"),
    ("GET", "/api/v2/decision/calls"),
    ("GET", "/api/v2/decision/calls/{call_id}"),
    ("GET", "/api/v2/decision/data"),
    ("GET", "/api/v2/decision/launcher"),
    ("GET", "/api/v2/decision/missions"),
    ("GET", "/api/v2/decision/page"),
    ("GET", "/api/v2/decision/proposals/{proposal_id}"),
    ("GET", "/api/v2/decisions"),
    ("GET", "/api/v2/decisions/proposals/{proposal_id}"),
    ("GET", "/api/v2/decisions/skills/{proposal_id}"),
    ("GET", "/api/v2/diagnostics"),
    ("GET", "/api/v2/drafts"),
    ("GET", "/api/v2/drafts/{key}"),
    ("GET", "/api/v2/folders/pick"),
    ("GET", "/api/v2/limits"),
    ("GET", "/api/v2/llm/connections"),
    ("GET", "/api/v2/llm/magpie"),
    ("GET", "/api/v2/messaging"),
    ("GET", "/api/v2/messaging/config"),
    ("GET", "/api/v2/native-assets"),
    ("GET", "/api/v2/previews/{preview_id}/output"),
    ("GET", "/api/v2/profile-sessions/{session_id}"),
    ("GET", "/api/v2/profiles/{profile_id}"),
    ("GET", "/api/v2/profiles/{profile_id}/{action}"),
    ("GET", "/api/v2/providers/{provider}/{provider_action}"),
    ("GET", "/api/v2/routines/{routine_id}"),
    ("GET", "/api/v2/routines/{routine_id}/runs"),
    ("GET", "/api/v2/session-metadata"),
    ("GET", "/api/v2/session-roots"),
    ("GET", "/api/v2/sessions/{session_id}/approval"),
    ("GET", "/api/v2/sessions/{session_id}/context"),
    ("GET", "/api/v2/sessions/{session_id}/conversation-search"),
    ("GET", "/api/v2/sessions/{session_id}/draft-command"),
    ("GET", "/api/v2/sessions/{session_id}/inventory"),
    ("GET", "/api/v2/sessions/{session_id}/prefs"),
    ("GET", "/api/v2/sessions/{session_id}/requests"),
    ("GET", "/api/v2/sessions/{session_id}/subagents/{tool_use_id}"),
    ("GET", "/api/v2/sessions/{session_id}/target"),
    ("GET", "/api/v2/vault-publications/recovery"),
    ("GET", "/api/v2/vaults/suggestion"),
    ("GET", "/api/v2/vaults/{workspace_id}/backlinks/{backlink_ref:path}"),
    ("GET", "/api/v2/vaults/{workspace_id}/bookmarks"),
    ("GET", "/api/v2/vaults/{workspace_id}/captures"),
    ("GET", "/api/v2/vaults/{workspace_id}/captures/suggest"),
    ("GET", "/api/v2/vaults/{workspace_id}/entries"),
    ("GET", "/api/v2/vaults/{workspace_id}/excerpt"),
    ("GET", "/api/v2/vaults/{workspace_id}/history/{history_ref:path}"),
    ("GET", "/api/v2/vaults/{workspace_id}/notes/{ref:path}"),
    ("GET", "/api/v2/vaults/{workspace_id}/overview"),
    ("GET", "/api/v2/vaults/{workspace_id}/policy"),
    ("GET", "/api/v2/vaults/{workspace_id}/recovery"),
    ("GET", "/api/v2/voice"),
    ("GET", "/api/v2/voice/pack"),
    ("GET", "/api/v2/voice/voicebox"),
    ("GET", "/api/v2/workspaces/{workspace_id}"),
    ("GET", "/api/v2/worktrees"),
    ("PATCH", "/api/v2/llm/connections/{connection_id}"),
    ("PATCH", "/api/v2/profiles/{profile_id}"),
    ("PATCH", "/api/v2/routines/{routine_id}"),
    ("PATCH", "/api/v2/session-metadata/{session_id}"),
    ("PATCH", "/api/v2/sessions/{session_id}/prefs"),
    ("PATCH", "/api/v2/vaults/{workspace_id}/policy"),
    ("POST", "/api/v2/approvals"),
    ("POST", "/api/v2/assets"),
    ("POST", "/api/v2/assets/{asset_id}/relink"),
    ("POST", "/api/v2/assets/{asset_id}/remove"),
    ("POST", "/api/v2/avatars"),
    ("POST", "/api/v2/bot-memory/{workspace_id}/metadata"),
    ("POST", "/api/v2/bots/reset"),
    ("POST", "/api/v2/bots/{bot_id}/{action}"),
    ("POST", "/api/v2/check-runs/{run_id}/cancel"),
    ("POST", "/api/v2/checks/{check_id}/delete"),
    ("POST", "/api/v2/checks/{check_id}/runs"),
    ("POST", "/api/v2/code/{workspace_id}/checks"),
    ("POST", "/api/v2/code/{workspace_id}/commit"),
    ("POST", "/api/v2/code/{workspace_id}/git-settings"),
    ("POST", "/api/v2/code/{workspace_id}/previews"),
    ("POST", "/api/v2/code/{workspace_id}/push"),
    ("POST", "/api/v2/code/{workspace_id}/push/check"),
    ("POST", "/api/v2/code/{workspace_id}/terminal"),
    ("POST", "/api/v2/code/{workspace_id}/worktrees"),
    ("POST", "/api/v2/commands"),
    ("POST", "/api/v2/conversation-archives"),
    ("POST", "/api/v2/data/reset"),
    ("POST", "/api/v2/decision/changes/{proposal_id}"),
    ("POST", "/api/v2/decision/checks/{workspace_id}/run"),
    ("POST", "/api/v2/decision/checks/{workspace_id}/stop"),
    ("POST", "/api/v2/decision/data/delete"),
    ("POST", "/api/v2/decision/data/record"),
    ("POST", "/api/v2/decision/data/records"),
    ("POST", "/api/v2/decision/feedback"),
    ("POST", "/api/v2/decision/inbox/suggest"),
    ("POST", "/api/v2/decision/key"),
    ("POST", "/api/v2/decision/launcher/open"),
    ("POST", "/api/v2/decision/missions"),
    ("POST", "/api/v2/decision/missions/stop"),
    ("POST", "/api/v2/decision/missions/{mission_id}/stop"),
    ("POST", "/api/v2/decision/observe"),
    ("POST", "/api/v2/decision/preferences"),
    ("POST", "/api/v2/decision/presence"),
    ("POST", "/api/v2/decision/proposals"),
    ("POST", "/api/v2/decision/proposals/{proposal_id}"),
    ("POST", "/api/v2/decision/reports"),
    ("POST", "/api/v2/decision/seats/{routine_id}/start"),
    ("POST", "/api/v2/decision/seats/{routine_id}/stop"),
    ("POST", "/api/v2/decision/test"),
    ("POST", "/api/v2/decisions/proposals/{proposal_id}"),
    ("POST", "/api/v2/decisions/skills/{proposal_id}"),
    ("POST", "/api/v2/diagnostics/checks"),
    ("POST", "/api/v2/exports"),
    ("POST", "/api/v2/exports/preview"),
    ("POST", "/api/v2/folders/pick"),
    ("POST", "/api/v2/imports/preview"),
    ("POST", "/api/v2/imports/{preview_id}/apply"),
    ("POST", "/api/v2/llm/connections"),
    ("POST", "/api/v2/llm/connections/models"),
    ("POST", "/api/v2/llm/connections/test"),
    ("POST", "/api/v2/llm/magpie/close"),
    ("POST", "/api/v2/llm/magpie/open"),
    ("POST", "/api/v2/llm/magpie/view"),
    ("POST", "/api/v2/messaging/discord/disable"),
    ("POST", "/api/v2/messaging/discord/enable"),
    ("POST", "/api/v2/messaging/mattermost/disable"),
    ("POST", "/api/v2/messaging/mattermost/enable"),
    ("POST", "/api/v2/messaging/slack/disable"),
    ("POST", "/api/v2/messaging/slack/enable"),
    ("POST", "/api/v2/previews/{preview_id}/open"),
    ("POST", "/api/v2/previews/{preview_id}/remove"),
    ("POST", "/api/v2/previews/{preview_id}/stop"),
    ("POST", "/api/v2/profiles"),
    ("POST", "/api/v2/profiles/{profile_id}/{action}"),
    ("POST", "/api/v2/provider/login"),
    ("POST", "/api/v2/routines/preview"),
    ("POST", "/api/v2/routines/{routine_id}/archive"),
    ("POST", "/api/v2/routines/{routine_id}/pause"),
    ("POST", "/api/v2/session-roots"),
    ("POST", "/api/v2/session-windows"),
    ("POST", "/api/v2/sessions/{session_id}/artifact-detail"),
    ("POST", "/api/v2/sessions/{session_id}/history-anchor"),
    ("POST", "/api/v2/sessions/{session_id}/mcp/reconnect"),
    ("POST", "/api/v2/sessions/{session_id}/mcp/toggle"),
    ("POST", "/api/v2/sessions/{session_id}/runs/{tool_use_id}/stop"),
    ("POST", "/api/v2/sessions/{session_id}/tool-sources"),
    ("POST", "/api/v2/vault-operations/{operation_id}/resolve"),
    ("POST", "/api/v2/vault-publications/{operation_id}/resolve"),
    ("POST", "/api/v2/vaults/create"),
    ("POST", "/api/v2/vaults/open"),
    ("POST", "/api/v2/vaults/{workspace_id}/cancel-bootstrap"),
    ("POST", "/api/v2/vaults/{workspace_id}/captures/suggest"),
    ("POST", "/api/v2/vaults/{workspace_id}/metadata"),
    ("POST", "/api/v2/vaults/{workspace_id}/operations"),
    ("POST", "/api/v2/vaults/{workspace_id}/operations/preview"),
    ("POST", "/api/v2/vaults/{workspace_id}/proposals"),
    ("POST", "/api/v2/vaults/{workspace_id}/skill-proposals"),
    ("POST", "/api/v2/voice/fallback/clear"),
    ("POST", "/api/v2/voice/handsfree/report"),
    ("POST", "/api/v2/voice/pack/cancel"),
    ("POST", "/api/v2/voice/pack/check"),
    ("POST", "/api/v2/voice/pack/install"),
    ("POST", "/api/v2/voice/pack/remove"),
    ("POST", "/api/v2/voice/pack/repair"),
    ("POST", "/api/v2/voice/pack/retry"),
    ("POST", "/api/v2/voice/pack/warm"),
    ("POST", "/api/v2/voice/speech-settings"),
    ("POST", "/api/v2/voice/voicebox/speech"),
    ("POST", "/api/v2/workspaces/code"),
    ("POST", "/api/v2/workspaces/select"),
    ("POST", "/api/v2/workspaces/{workspace_id}/remove"),
    ("POST", "/api/v2/worktrees/{worktree_id}/integrate"),
    ("POST", "/api/v2/worktrees/{worktree_id}/remove"),
    ("POST", "/api/v2/worktrees/{worktree_id}/restore"),
    ("PUT", "/api/v2/bot-threads/{workspace_id}/{thread_id}"),
    ("PUT", "/api/v2/code/{workspace_id}/file"),
    ("PUT", "/api/v2/drafts/{key}"),
    ("PUT", "/api/v2/messaging/discord/config"),
    ("PUT", "/api/v2/messaging/mattermost/config"),
    ("PUT", "/api/v2/messaging/slack/config"),
    ("PUT", "/api/v2/vaults/{workspace_id}/bookmarks/{ref:path}"),
    ("PUT", "/api/v2/vaults/{workspace_id}/instructions/{name}"),
    ("PUT", "/api/v2/vaults/{workspace_id}/notes/{ref:path}"),
]


def _window_routes(app: Starlette) -> set[tuple[str, str]]:
    """Every `/api/` `Route` the window itself can call: not the agent surface (`/api/agent/`),
    the model gateway's own runtime proxy (`/api/llm/runtime/`) or the window's Apply routes
    (`/api/v2/agent-changes*`) — none of which a `viki-agent` command reaches through a window
    call, and the last two of which answer only the window's own token, never a grant's."""
    window: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, Route) or not route.path.startswith("/api/"):
            continue
        if (
            route.path.startswith("/api/agent/")
            or route.path.startswith("/api/llm/runtime/")
            or route.path.startswith("/api/v2/agent-changes")
        ):
            continue
        for method in route.methods or set():
            if method == "HEAD":  # Starlette's own mirror of GET, not a distinct operation
                continue
            actions = ACTIONS.get((method, route.path))
            if actions is None:
                window.add((method, route.path))
            else:
                prefix = route.path.removesuffix("{action}")
                window.update((method, prefix + action) for action in actions)
    return window


def _covered() -> set[tuple[str, str]]:
    covered: set[tuple[str, str]] = set()
    for pairs in COVERS.values():
        covered |= pairs
    return covered


def test_covers_stays_inside_the_real_window_surface(tmp_path: Path) -> None:
    app = _p1_app(tmp_path, agent_cli=tmp_path / "viki-agent")
    assert _covered() <= _window_routes(app)
    # A whole action route would claim every action; list its actions in ACTIONS first.
    assert not [path for _method, path in _covered() if path.endswith("{action}")]


def test_actions_are_every_action_routines_api_compares() -> None:
    compared = {
        constant.value
        for node in ast.walk(ast.parse(inspect.getsource(routines_api)))
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Name)
        and node.left.id == "action"
        for constant in ast.walk(node)
        if isinstance(constant, ast.Constant) and isinstance(constant.value, str)
    }
    assert compared == {action for actions in ACTIONS.values() for action in actions}


def test_uncovered_window_routes_match_the_frozen_ratchet(tmp_path: Path) -> None:
    app = _p1_app(tmp_path, agent_cli=tmp_path / "viki-agent")
    uncovered = sorted(_window_routes(app) - _covered())
    assert uncovered == UNCOVERED


def test_covers_never_reaches_the_apply_routes() -> None:
    for pairs in COVERS.values():
        for _method, path in pairs:
            assert not path.startswith("/api/v2/agent-changes")
