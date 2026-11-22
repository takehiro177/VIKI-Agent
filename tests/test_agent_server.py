"""server.py's agent_cli wiring (ADR-086): without `agent_cli`, the agent surface and the
window's Apply routes are absent; with it, they register and an ordinary chat's spawn, resume
and fork all ask for a CLI key; a grant minted from the manager's own bound registry is honored.
`__main__.main` dispatches a `viki-agent` command before any app start-up runs, and
`compiled.agent_cli()` resolves the right binary in each mode."""

from __future__ import annotations

import sysconfig
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from tests.persistence.test_storage import _workspace
from tests.profiles.test_profile_launch_api import (
    HANG_GUARD_S,
    Rig,
    open_session,
    send_prepared,
)
from tests.profiles.test_profile_launch_api import authed as authed_profile
from tests.test_server import (
    BASE,
    ORIGIN,
    TOKEN,
    EchoDriver,
    EmptyCatalog,
    ExistingCatalog,
    _open_v2,
    _p1_app,
    authed,
    recv,
    running,
)
from viki_agent import __main__ as launcher
from viki_agent import agent_cli, compiled, server
from viki_agent.app.agent_api import agent_routes
from viki_agent.app.agent_grants import AgentGrant, AgentGrants
from viki_agent.app.diagnostics import Readiness
from viki_agent.app.settings import SettingsService
from viki_agent.config import Config
from viki_agent.drivers.claude.control import Launch
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.events import Event
from viki_agent.profiles.service import ProfileService
from viki_agent.session import SessionManager
from viki_agent.state import AppState, Prefs
from viki_agent.workspaces import WorkspaceService, root_key


@dataclass
class AgentApp:
    app: Starlette
    client: TestClient
    manager: SessionManager
    settings: SettingsService
    workspaces: WorkspaceService
    profiles: ProfileService
    workspace_id: str


def _agent_app(tmp_path: Path, *, make_driver: Callable[..., Any] | None = None) -> AgentApp:
    """`create_app` wired with real settings, one active ordinary workspace, readiness and
    `agent_cli` — enough for the agent surface and a routine's manual run, without bootstrap,
    checks or diagnostics."""
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    config = Config(vault=vault, claude_home=home, cli=Path("fake"), edge=None, data_dir=data)
    state = AppState(data / "state.json", Prefs())
    manager = SessionManager(
        config=DriverConfig(cli=(), vault=vault, env={}),
        state=state,
        claude_home=home,
        make_driver=make_driver
        or (lambda *_: pytest.fail("no route in this test may start a provider")),
    )
    spaces = WorkspaceService(
        state.store,
        env={},
        data_dir=data,
        home=tmp_path / "profile",
        install_dir=tmp_path / "install",
        git_available=lambda: False,
        hide_previews=lambda: False,
    )
    info = vault.stat()
    workspace_id = str(uuid.uuid4())
    row = _workspace(
        workspace_id,
        canonical_key=root_key(vault.resolve()),
        display_root=str(vault),
        physical_id=f"{info.st_dev}:{info.st_ino}",
        bootstrap="complete",
        active=1,
    )
    state.store.update_workspace(workspace_id, lambda _current, _rows: row)
    settings = SettingsService(
        state.store, provider_defaults=Prefs, on_chat_defaults=state.set_defaults
    )
    profiles = ProfileService(state.store)
    readiness = Readiness(config=config, launch_id=str(uuid.uuid4()), git_available=lambda: False)
    app = server.create_app(
        config=config,
        token=TOKEN,
        manager=manager,
        catalog=EmptyCatalog(home, vault),
        static_dir=None,
        settings=settings,
        workspaces=spaces,
        agent_cli=tmp_path / "viki-agent",
        readiness=readiness,
    )
    return AgentApp(
        app, TestClient(app, base_url=BASE), manager, settings, spaces, profiles, workspace_id
    )


def _usageless_agent_app(tmp_path: Path) -> tuple[AgentGrant, TestClient]:
    """A minimal agent surface built straight from `agent_routes`, with no Bot usage wired in.
    `create_app` only ever builds `usage_views` together with `workspaces` (both follow `bots is
    not None`), and the agent surface itself needs `workspaces` too, so `_agent_app` can never
    reach a `usage is None` surface; this is the one way to exercise that branch (F17)."""
    grants = AgentGrants(tmp_path / "viki-agent")
    grant = grants.issue(str(uuid.uuid4()))
    assert grant is not None
    routes = agent_routes(
        grants,
        cast(Any, None),  # changes: unused outside the settings/routines/bots handlers
        settings=cast(Any, None),
        routines=cast(Any, None),
        runner=cast(Any, None),
        scheduler=cast(Any, None),
        bots=None,
        profiles=cast(Any, None),
        workspaces=cast(Any, None),
        admission_lock=cast(Any, None),
        app_version="0",
        workers=set(),
    )
    return grant, TestClient(Starlette(routes=routes), base_url=BASE)


# --- create_app's gate: agent_cli and settings together ---------------------------------------


def test_without_agent_cli_the_surface_and_apply_routes_are_absent(tmp_path: Path) -> None:
    app = _p1_app(tmp_path)
    paths = [route.path for route in app.routes if isinstance(route, Route)]
    assert not any(path.startswith("/api/agent/") for path in paths)
    assert not any(path.startswith("/api/v2/agent-changes") for path in paths)


def test_with_agent_cli_the_surface_and_apply_routes_register(tmp_path: Path) -> None:
    app = _p1_app(tmp_path, agent_cli=tmp_path / "viki-agent")
    paths = [route.path for route in app.routes if isinstance(route, Route)]
    assert "/api/agent/{grant_id}/{operation:path}" in paths
    assert "/api/v2/agent-changes" in paths
    assert "/api/v2/agent-changes/{change_id}" in paths


# --- an ordinary chat's spawn carries the flag -------------------------------------------------


def test_a_new_session_opened_over_the_ws_asks_for_an_agent_cli_key(tmp_path: Path) -> None:
    """Fails without server.py's change: `_actor_for`'s `manager.create` would leave
    `Launch.agent_cli` at its default `False`."""
    launches: list[Launch] = []

    def make(launch: Launch, _prefs: Prefs, emit: Callable[[Event], None]) -> EchoDriver:
        launches.append(launch)
        return EchoDriver(launch, emit)

    with running(tmp_path, make_driver=make) as app, authed(app.client) as ws:
        _open_v2(ws)
    assert launches and launches[-1].agent_cli is True


# --- resuming or reopening an existing transcript carries the flag too ------------------------


def test_opening_an_existing_transcript_over_the_ws_asks_for_an_agent_cli_key(
    tmp_path: Path,
) -> None:
    """Fails without server.py's change: `_actor_for`'s two `manager.open(...)` calls for an
    existing transcript (plain, and with `existing_only`) would leave `Launch.agent_cli` at its
    default `False` (finding R6, mutant M1): a resumed or reopened chat would silently lose its
    key, though the server suites still pass."""
    launches: list[Launch] = []

    def make(launch: Launch, _prefs: Prefs, emit: Callable[[Event], None]) -> EchoDriver:
        launches.append(launch)
        return EchoDriver(launch, emit)

    plain_id = "22222222-2222-4222-8222-222222222222"
    existing_only_id = "33333333-3333-4333-8333-333333333333"
    catalog = ExistingCatalog(tmp_path / "home", tmp_path / "vault")
    with (
        running(tmp_path, make_driver=make, catalog=catalog) as app,
        authed(app.client) as ws,
    ):
        _open_v2(ws, plain_id)

        request_id = str(uuid.uuid4())
        ws.send_json(
            {
                "type": "session.open",
                "session_id": existing_only_id,
                "request_id": request_id,
                "existing_only": True,
            }
        )
        snapshot, synced = recv(ws), recv(ws)
        assert (snapshot["type"], synced["type"]) == ("session.snapshot", "session.synced")
        assert snapshot["session_id"] == synced["session_id"] == existing_only_id
        assert snapshot["request_id"] == synced["request_id"] == request_id

        # Resuming starts the process only on first send: force it for both, after every open
        # has drained its frames, so a started session's own events cannot be mistaken for the
        # next open's snapshot/synced pair on this one socket.
        plain_actor = app.manager.get(plain_id)
        existing_only_actor = app.manager.get(existing_only_id)
        assert plain_actor is not None and existing_only_actor is not None
        ws.portal.call(plain_actor.start)
        ws.portal.call(existing_only_actor.start)

    assert [launch.agent_cli for launch in launches] == [True, True]


def test_reopening_a_profile_origin_transcript_over_the_ws_gets_no_agent_cli_key(
    tmp_path: Path,
) -> None:
    """`SessionManager._actor` ANDs `agent_cli` with `not profile`, so a profile-origin session
    never gets a grant even though `_actor_for` passes `agent_cli=True` into every
    `manager.open(...)` for an existing transcript regardless of origin. `storage.py`'s
    `session_origin` (`sessions.origin='profile'` plus a `session_profile_bindings` row) is the
    same durable origin a routine run or a Bot-bound chat gets too: `messaging/bots.py`'s own
    `BotService.threads` filters its chats with `s.origin='profile'`, and `profile_launch.py`'s
    launch-registration flow threads a `bot_id` through the same binding, so both share this
    table, differing only in `launch_origin` ("interactive"/"routine"/"unknown")."""
    rig = Rig(tmp_path)
    session_id, command_id = str(uuid.uuid4()), str(uuid.uuid4())
    first_manager = rig.manager()
    with TestClient(rig.app(first_manager), base_url=BASE) as client:
        preview = rig.preview(client, session_id, command_id)
        send_prepared(client, session_id, preview["generation"], str(uuid.uuid4()))
        assert rig.driver_created.wait(HANG_GUARD_S)
        assert rig.drivers[0].sent.wait(HANG_GUARD_S)

    # A second manager (an app reload) holds no actor for the session; its transcript now
    # exists (and the profile binding's native_started is set), so reopening it over the WS
    # must resume through manager.open(profile=True, ...), the same line test (a) pins for an
    # ordinary chat, never attach_prepared's fresh-launch path.
    rig.driver_created.clear()
    second_manager = rig.manager()
    with (
        TestClient(rig.app(second_manager), base_url=BASE) as client,
        authed_profile(client) as ws,
    ):
        generation = open_session(ws, session_id, HANG_GUARD_S)
        ws.send_json(
            {
                "type": "turn.send",
                "session_id": session_id,
                "generation": generation,
                "command_id": str(uuid.uuid4()),
                "text": "hi",
            }
        )
        assert rig.driver_created.wait(HANG_GUARD_S)
        assert rig.drivers[-1].sent.wait(HANG_GUARD_S)

    assert rig.drivers[-1].launch.resume is True
    assert rig.drivers[-1].launch.agent_cli is False


# --- a grant from the bound manager --------------------------------------------------------


def test_a_grant_from_the_bound_manager_is_honored(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        grant = agent.manager._agent_grants.issue(str(uuid.uuid4()))  # type: ignore[union-attr]
        assert grant is not None
        response = agent.client.get(
            f"/api/agent/{grant.id}/status", headers={"authorization": f"Bearer {grant.token}"}
        )
    assert response.status_code == 200
    assert response.json()["chat"] == {"session_id": grant.session_id}


# --- the usage operations' own target checks, and their 503 without usage (F17) ---------------


def test_usage_operations_refuse_an_invalid_target_with_400(tmp_path: Path) -> None:
    """Pins P18's target checks at the agent trust boundary. Mutant A dropped `target not in
    bot_usage_api._DAYS`: `usage/10` would reach `bot_usage_api._DAYS[target]` and answer 503
    `unavailable` with a WARNING logged, instead of 400 `invalid_input`. Mutant B dropped `not
    _UUID_RE.match(target)`: `usage/sessions/x` would reach `usage.trace` and answer 404 instead
    of 400; the same check also refuses a session id of the right shape but the wrong case."""
    agent = _agent_app(tmp_path)
    with agent.client:
        grant = agent.manager._agent_grants.issue(str(uuid.uuid4()))  # type: ignore[union-attr]
        assert grant is not None
        headers = {"authorization": f"Bearer {grant.token}"}
        wrong_case = str(uuid.uuid4()).upper()
        for operation in ("usage/10", "usage/sessions/not-a-uuid", f"usage/sessions/{wrong_case}"):
            response = agent.client.get(f"/api/agent/{grant.id}/{operation}", headers=headers)
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "invalid_input"


def test_usage_operations_answer_503_without_usage_configured(tmp_path: Path) -> None:
    """The `usage is None` 503 branch (F17): `_agent_app`'s real `create_app` wiring always pairs
    the agent surface with a configured `usage`, so only `_usageless_agent_app` reaches it, for
    both usage operations. The message is pinned too: drop the branch and `usage.summary`/
    `usage.trace` would raise `AttributeError` on the `None`, which `_answer`'s generic fallback
    also maps to 503 `unavailable` — same status and code, but its message is the generic
    "VIKI Agent couldn't do that just now.", not this branch's own, so a status/code check alone
    would pass by accident on that mutant."""
    grant, client = _usageless_agent_app(tmp_path)
    headers = {"authorization": f"Bearer {grant.token}"}
    valid_session_id = str(uuid.uuid4())
    with client:
        for operation in ("usage/7", f"usage/sessions/{valid_session_id}"):
            response = client.get(f"/api/agent/{grant.id}/{operation}", headers=headers)
            assert response.status_code == 503
            assert response.json()["error"] == {
                "code": "unavailable",
                "message": "Bot usage isn't available.",
            }


# --- a hold cannot outlive its chat's revoked grant ---------------------------------------------


def test_revoking_a_grant_expires_its_hold_for_listing_and_apply(tmp_path: Path) -> None:
    """Fails without server.py's change: `AgentChanges(grants.live)` becoming
    `AgentChanges(lambda _grant_id: True)` would let a hold outlive its chat's process (finding
    R6, mutant M2): once the grant that held it is revoked, the window's listing must drop it
    and its Apply must answer 409 `expired`, though the server suites still pass."""
    agent = _agent_app(tmp_path)
    with agent.client:
        grant = agent.manager._agent_grants.issue(str(uuid.uuid4()))  # type: ignore[union-attr]
        assert grant is not None
        revision = agent.settings.read().document.revision
        held = agent.client.patch(
            f"/api/agent/{grant.id}/settings",
            headers={"authorization": f"Bearer {grant.token}"},
            json={
                "command_id": str(uuid.uuid4()),
                "expected_revision": revision,
                "key": "chat.default_mode",
                "value": "plan",
            },
        )
        assert held.status_code == 202
        change_id = held.json()["pending"]["change_id"]

        grant.revoke()

        listing = agent.client.get(
            "/api/v2/agent-changes", headers={"authorization": f"Bearer {TOKEN}"}
        )
        assert listing.status_code == 200
        assert listing.json()["changes"] == []

        decided = agent.client.post(
            f"/api/v2/agent-changes/{change_id}",
            headers={"authorization": f"Bearer {TOKEN}", "origin": ORIGIN},
            json={"decision": "apply"},
        )
    assert decided.status_code == 409
    assert decided.json()["error"]["code"] == "expired"


# --- __main__.main's dispatch -----------------------------------------------------------------


def test_a_cli_command_dispatches_before_any_app_start_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(agent_cli, "main", lambda argv, **kwargs: calls.append(argv) or 0)
    monkeypatch.setattr(launcher, "_main", lambda *a, **k: pytest.fail("must not reach _main"))
    assert launcher.main(["status"]) == 0
    assert calls == [["status"]]


def test_no_args_reaches_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(launcher, "_main", lambda argv, later: calls.append(argv) or 0)
    assert launcher.main([]) == 0
    assert calls == [[]]


# --- compiled.agent_cli() ----------------------------------------------------------------------


def test_agent_cli_path_from_a_checkout_or_venv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(compiled, "COMPILED", False)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    binary = scripts_dir / compiled.APP_BINARY
    binary.write_text("", encoding="utf-8")
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(scripts_dir))
    assert compiled.agent_cli() == binary


def test_agent_cli_path_when_compiled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(compiled, "COMPILED", True)
    monkeypatch.setattr(compiled, "INSTALL_DIR", tmp_path)
    binary = tmp_path / compiled.APP_BINARY
    binary.write_text("", encoding="utf-8")
    assert compiled.agent_cli() == binary


def test_agent_cli_path_is_none_for_a_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(compiled, "COMPILED", True)
    monkeypatch.setattr(compiled, "INSTALL_DIR", tmp_path)
    assert compiled.agent_cli() is None
