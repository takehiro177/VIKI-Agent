"""The viki-agent CLI (P5) against the real agent surface (P3), wired by `create_app` (P4): each
command runs through `agent_cli.main`, with `send` adapted into the same app's Starlette
TestClient, against real settings, routine and Bot services on a synthetic store under
tmp_path. Work file Amendments, 17:58: P5 inferred some reply shapes before P3 built them
(settings `revision`, `keys[KEY].kind`, the routines list wrapper, `status`'s fields); only a
test that crosses the CLI/surface boundary proves the two still agree."""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.automation.test_routines import _document_raw
from tests.profiles.test_profile_document import document
from tests.test_agent_server import AgentApp, _agent_app
from tests.test_server import TOKEN
from viki_agent import agent_cli
from viki_agent.automation.routines import RoutineService, record_tested, stamp
from viki_agent.automation.tasks import TaskStore

NOW = datetime(2026, 11, 20, tzinfo=UTC)
# claim_manual's scope (64 hex chars) and owner (36 chars); summaries() reads neither, so any
# value meeting the store's CHECK constraints proves the same thing.
SCOPE = "cd" * 32
OWNER = "55555555-5555-4555-8555-555555555555"


def _ident() -> str:
    return str(uuid.uuid4())


def _run(agent: AgentApp, session_id: str, argv: list[str]) -> tuple[int, str]:
    """`agent_cli.main` against `agent`'s own app, authenticated as a fresh grant for
    `session_id`; (exit code, the captured --json stdout)."""
    grant = agent.manager._agent_grants.issue(session_id)  # type: ignore[union-attr]
    assert grant is not None

    headers = {"authorization": f"Bearer {grant.token}"}

    def send(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        response = agent.client.request(method, path, headers=headers, json=body)
        return response.status_code, response.json()

    out = io.StringIO()
    err = io.StringIO()
    code = agent_cli.main([*argv, "--json"], env=grant.environment(), send=send, out=out, err=err)
    return code, out.getvalue()


def _profiled(agent: AgentApp) -> str:
    """A real profile, so `routines add`/`bots create` have one to point at."""
    profile_id = _ident()
    agent.profiles.create(profile_id, _ident(), document())
    return profile_id


def test_status_reports_the_calling_chat_and_the_active_workspace(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        session_id = _ident()
        code, out = _run(agent, session_id, ["status"])
    assert code == 0
    body = json.loads(out)
    assert body["chat"] == {"session_id": session_id}
    assert [w["workspace_id"] for w in body["workspaces"] if w["active"]] == [agent.workspace_id]


def test_settings_get_reads_the_surface(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        code, out = _run(agent, _ident(), ["settings", "get"])
    assert code == 0
    body = json.loads(out)
    assert "appearance.theme" in body["keys"]
    assert isinstance(body["keys"]["appearance.theme"].get("kind"), str)
    assert body["revision"] == 0


def test_a_change_level_settings_set_applies_at_once(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        code, out = _run(agent, _ident(), ["settings", "set", "appearance.theme", "dark"])
    assert code == 0
    assert json.loads(out) == {"applied": True, "revision": 1}
    assert agent.settings.read().effective["appearance.theme"] == "dark"


def test_a_safety_level_settings_set_waits_for_apply_and_changes_nothing(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        code, out = _run(agent, _ident(), ["settings", "set", "chat.default_mode", "plan"])
        assert agent.settings.read().effective["chat.default_mode"] == "default"
    assert code == agent_cli.EXIT_PENDING == 3
    assert "pending" in json.loads(out)


def test_routines_add_makes_a_draft(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        profile_id = _profiled(agent)
        code, out = _run(
            agent,
            _ident(),
            [
                "routines",
                "add",
                "--name",
                "Daily brief",
                "--prompt",
                "Summarize the day",
                "--profile",
                profile_id,
                "--workspace",
                agent.workspace_id,
            ],
        )
    assert code == 0
    assert json.loads(out)["routine"]["state"] == "draft"


def test_routines_enable_of_a_tested_routine_waits_for_apply(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        profile_id = _profiled(agent)
        routines = RoutineService(
            agent.manager.app_state.store, agent.profiles, agent.settings, agent.workspaces
        )
        routine_id = routines.create(
            _ident(), _document_raw(profile_id=profile_id, workspace_id=agent.workspace_id)
        )[0]["routine_id"]
        with agent.manager.app_state.store._connection(write=True) as connection:
            record_tested(connection, routine_id, 1, stamp(NOW))
        code, out = _run(agent, _ident(), ["routines", "enable", routine_id])
        assert routines.get(routine_id)["state"] == "tested"
    assert code == agent_cli.EXIT_PENDING == 3
    assert "pending" in json.loads(out)


def test_routines_enable_while_a_run_is_active_exits_1_with_test_running(tmp_path: Path) -> None:
    """W7: `GET routines` never carries `active_run` (only `running`); the CLI's local check
    must read `running`, so it refuses before the request ever reaches the server, with code
    `test_running`. Fails on today's agent_cli.py, which checks `active_run` and so never
    refuses here: the request reaches the server and comes back 409 `invalid_transition`."""
    agent = _agent_app(tmp_path)
    with agent.client:
        profile_id = _profiled(agent)
        routines = RoutineService(
            agent.manager.app_state.store, agent.profiles, agent.settings, agent.workspaces
        )
        routine_id = routines.create(
            _ident(), _document_raw(profile_id=profile_id, workspace_id=agent.workspace_id)
        )[0]["routine_id"]
        # The real claim the runner itself makes (tasks.py's `run_now`), without scheduling the
        # background run: the attempt stays "claimed" (active) for the rest of this test.
        TaskStore(agent.manager.app_state.store, SCOPE, OWNER).claim_manual(
            routine_id, _ident(), 1, stamp(NOW)
        )
        code, out = _run(agent, _ident(), ["routines", "enable", routine_id])
    assert code == agent_cli.EXIT_REFUSED == 1
    assert json.loads(out)["error"]["code"] == "test_running"


def test_bots_create_when_bots_exist(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        profile_id = _profiled(agent)
        code, out = _run(
            agent,
            _ident(),
            ["bots", "create", "--profile", profile_id, "--workspace", agent.workspace_id],
        )
    assert code == 0
    bot = json.loads(out)["bot"]
    assert (bot["profile_id"], bot["workspace_id"]) == (profile_id, agent.workspace_id)


def _added(agent: AgentApp, session_id: str, profile_id: str) -> tuple[int, dict[str, Any]]:
    """`routines add` of a manual draft under `profile_id`, from the chat `session_id`."""
    code, out = _run(
        agent,
        session_id,
        [
            "routines",
            "add",
            "--name",
            "On demand",
            "--prompt",
            "Summarize the day",
            "--profile",
            profile_id,
            "--workspace",
            agent.workspace_id,
        ],
    )
    return code, json.loads(out)


def test_routines_test_of_a_draft_exits_0_not_3(tmp_path: Path) -> None:
    """Fix round R2's rule: a 202 reply exits 3 only when its body holds `pending`; `routines
    test` answers 202 with a `run` body, so it exits 0. Run last (per the work file's
    Amendments, 17:58), after the other commands have proven the boundary agrees elsewhere; if
    this still exits 3, the bug is in agent_cli.py's `_respond`, not here or in agent_api.py.
    `_run` issues a fresh key per command, so the chat that tests here is the one that added the
    routine, resumed."""
    agent = _agent_app(tmp_path)
    with agent.client:
        profile_id = _profiled(agent)
        session_id = _ident()
        code, added = _added(agent, session_id, profile_id)
        assert code == 0
        code, out = _run(agent, session_id, ["routines", "test", added["routine"]["routine_id"]])
    assert code == 0, f"routines test exited {code}, expected 0 (body: {out})"
    assert "run" in json.loads(out)


def test_routines_test_of_a_routine_this_chat_did_not_add_exits_1_with_its_fix(
    tmp_path: Path,
) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        profile_id = _profiled(agent)
        routines = RoutineService(
            agent.manager.app_state.store, agent.profiles, agent.settings, agent.workspaces
        )
        routine_id = routines.create(
            _ident(), _document_raw(profile_id=profile_id, workspace_id=agent.workspace_id)
        )[0]["routine_id"]
        code, out = _run(agent, _ident(), ["routines", "test", routine_id])
    assert code == agent_cli.EXIT_REFUSED
    assert json.loads(out)["error"] == {
        "code": "not_added_here",
        "message": "A chat can test only a routine it added itself.",
        "fix": "Only a routine this chat added can be tested from a chat. "
        "Ask the owner to run it from Routines.",
    }


def test_routines_add_for_a_bot_s_profile_exits_1_with_its_fix(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        profile_id = _profiled(agent)
        code, _out = _run(agent, _ident(), ["bots", "create", "--profile", profile_id])
        assert code == 0
        code, body = _added(agent, _ident(), profile_id)
    assert code == agent_cli.EXIT_REFUSED
    assert body["error"] == {
        "code": "profile_not_allowed",
        "message": "A chat can't add or test a routine for a profile with MCP servers or a Bot.",
        "fix": "A chat can add routines only for a profile without MCP servers or a Bot. "
        "Ask the owner to add it in Routines.",
    }


def test_bots_create_for_a_profile_that_runs_routines_exits_1_with_its_fix(
    tmp_path: Path,
) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        profile_id = _profiled(agent)
        assert _added(agent, _ident(), profile_id)[0] == 0
        code, out = _run(agent, _ident(), ["bots", "create", "--profile", profile_id])
    assert code == agent_cli.EXIT_REFUSED
    assert json.loads(out)["error"] == {
        "code": "profile_has_routines",
        "message": "A chat can't create a Bot for a profile that runs routines.",
        "fix": "That profile already runs routines. Ask the owner to create this Bot in Agents.",
    }


# --- P18: bots usage / trace against the window's own bot-usage routes ------------------------


def test_bots_usage_matches_the_summary_window_route(tmp_path: Path) -> None:
    agent = _agent_app(tmp_path)
    with agent.client:
        code, out = _run(agent, _ident(), ["bots", "usage", "--days", "30"])
        window = agent.client.get(
            "/api/v2/bot-usage/summary/30", headers={"authorization": f"Bearer {TOKEN}"}
        )
    assert code == 0
    assert window.status_code == 200
    assert json.loads(out) == window.json()


def test_bots_trace_matches_the_sessions_window_route(tmp_path: Path) -> None:
    """Neither route has ever captured this session, so both answer the same `not_found` --
    proving the agent surface maps `BotUsageError` the way the window route does."""
    agent = _agent_app(tmp_path)
    session_id = _ident()
    with agent.client:
        code, out = _run(agent, _ident(), ["bots", "trace", session_id])
        window = agent.client.get(
            f"/api/v2/bot-usage/sessions/{session_id}",
            headers={"authorization": f"Bearer {TOKEN}"},
        )
    assert code == 1
    assert window.status_code == 404
    cli_error, window_error = json.loads(out)["error"], window.json()["error"]
    assert cli_error["code"] == window_error["code"] == "not_found"
    assert cli_error["message"] == window_error["message"]
