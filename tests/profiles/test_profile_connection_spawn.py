"""Bot entry points over the real session/driver graph and the synthetic replay engine."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from tests.automation.test_routines import _document_raw, _manual_schedule
from tests.conversations.test_session import _login
from tests.llm.test_connections import FORM
from tests.profiles.test_inventory import fixture_status, status_script
from tests.profiles.test_profile_launch import _server, with_servers
from tests.profiles.test_profile_launch_api import (
    AUTH,
    BASE,
    HANG_GUARD_S,
    WRITE,
    Rig,
    authed,
    fetch_command,
    ident,
    open_session,
    terminal_signal,
)
from tests.test_driver_reports import initialize
from viki_agent.drivers.claude import driver as claude_driver
from viki_agent.drivers.claude.control import Launch, build_argv
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.llm.connections import Connections, parse_connection
from viki_agent.messaging.bot_memory import instruction_excludes
from viki_agent.profiles.profile_capabilities import ADAPTER_VERSION, FIXTURE_VERSION, baseline
from viki_agent.profiles.provider_catalogs import ProviderCatalogService
from viki_agent.profiles.service import ProfileService
from viki_agent.session import SessionManager

from replay import FakeSpawner, init_frame, out, result_frame, user_in


class ConnectionRig(Rig):
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(tmp_path)
        self.workspaces.set_policy(ident(), self.workspace_id, 1, "ordinary", [])
        self.connection = parse_connection(FORM)
        self.connections = Connections(self.settings)
        self.connections.write(0, [self.connection])
        self.settings.patch(ident(), 1, {"chat.default_model": "sonnet"})
        raw = with_servers(_server())
        raw["runtime"]["model"] = self.connection.ref
        ProfileService(self.state.store).revise(self.profile_id, ident(), 1, raw)
        self.catalogs = ProviderCatalogService(self.state.store)
        self.catalogs.observe(
            "claude",
            ADAPTER_VERSION,
            FIXTURE_VERSION,
            baseline(FIXTURE_VERSION, models_observed=True),
            [{"value": "sonnet", "label": "Sonnet", "description": "Synthetic native model"}],
        )
        # Deliberately nonempty raw account and native menu: neither is route evidence.
        self.spawner = FakeSpawner(
            [
                *initialize(current_permission_mode="auto"),
                *status_script({"response": fixture_status()}),
                user_in(),
                out(init_frame()),
                out(result_frame()),
            ]
        )
        self.spawns: list[tuple[list[str], Path, dict[str, str]]] = []
        self.account_calls: list[object] = []
        self.catalog_calls: list[object] = []
        self.billing_calls: list[object] = []
        account = self.readiness.observe_account
        observe = ProviderCatalogService.observe
        billing = claude_driver.check_billing

        def observed_account(value: Any) -> None:
            self.account_calls.append(value)
            account(value)

        def observed_catalog(service: Any, *args: Any, **kwargs: Any) -> Any:
            self.catalog_calls.append(args)
            return observe(service, *args, **kwargs)

        def checked_billing(value: Any) -> None:
            self.billing_calls.append(value)
            billing(value)

        monkeypatch.setattr(self.readiness, "observe_account", observed_account)
        monkeypatch.setattr(ProviderCatalogService, "observe", observed_catalog)
        monkeypatch.setattr(claude_driver, "check_billing", checked_billing)
        self.manager_instance = SessionManager(
            config=DriverConfig(
                cli=("synthetic-claude",),
                vault=self.vault,
                env={},
                cli_version=FIXTURE_VERSION,
                spawner=self.spawn,
                grace_s=0.01,
                poll_s=0.01,
                control_timeout_s=0.05,
            ),
            state=self.state,
            claude_home=self.home,
            readiness=self.readiness,
        )
        self.application = self.app(self.manager_instance)
        self.turn_done = threading.Event()
        app_observer = self.manager_instance._observer
        assert app_observer is not None

        def completed(actor: Any, event: Any) -> None:
            app_observer(actor, event)
            if event["type"] == "turn.done":
                self.catalog.transcripts.add(actor.session_id)
                self.turn_done.set()

        self.manager_instance.bind_observer(completed)

    async def spawn(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> Any:
        self.spawns.append((list(argv), cwd, dict(env)))
        return await self.spawner(argv, cwd, env)

    def bot(self, client: TestClient) -> dict[str, Any]:
        response = client.post(
            "/api/v2/bots",
            headers=WRITE,
            json={
                "bot_id": ident(),
                "profile_id": self.profile_id,
                "workspace_id": self.workspace_id,
                "expected_profile_revision": 2,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["bot"]

    def task(self, client: TestClient, bot: dict[str, Any]) -> dict[str, Any]:
        response = client.post(
            f"/api/v2/profiles/{self.profile_id}/start-preview",
            headers=WRITE,
            json={
                "command_id": ident(),
                "session_id": ident(),
                "expected_profile_revision": 2,
                "workspace_id": self.workspace_id,
                "bot_id": bot["bot_id"],
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def send(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        preview: dict[str, Any],
        text: str,
    ) -> str:
        session_id, command_id = preview["session_id"], ident()
        draft = client.get(f"/api/v2/drafts/{session_id}", headers=AUTH).json()
        saved = client.put(
            f"/api/v2/drafts/{session_id}",
            headers=WRITE,
            json={"expected_revision": draft["revision"], "text": text},
        )
        assert saved.status_code == 200, saved.text
        self.turn_done.clear()
        with terminal_signal(monkeypatch, command_id) as done:
            accepted = client.post(
                "/api/v2/commands",
                headers=WRITE,
                json={
                    "command_id": command_id,
                    "session_id": session_id,
                    "generation": preview["generation"],
                    "draft_revision": saved.json()["revision"],
                },
            )
            assert accepted.status_code == 202, accepted.text
            assert done.wait(HANG_GUARD_S)
        receipt = fetch_command(client, command_id)
        # Native send has no durable pickup acknowledgment; successful sends keep this receipt.
        assert (receipt["state"], receipt["reason"]) == ("unknown", "native_unconfirmed")
        assert self.turn_done.wait(HANG_GUARD_S)
        actor = self.manager_instance.get(session_id)
        assert actor is not None
        events = actor.events_after(0)
        assert not [event for event in events if event["type"] == "error"]
        assert [event["data"]["status"] for event in events if event["type"] == "turn.done"] == [
            "success"
        ]
        return command_id

    def assert_routed(self, preview: dict[str, Any], owner: str, *, resume: bool = False) -> None:
        session_id = preview["session_id"]
        actor = self.manager_instance.get(session_id)
        assert actor is not None and actor.started is not None
        assert actor.started.model == self.connection.ref
        assert [option.value for option in actor.started.models] == [self.connection.ref]
        assert self.state.prefs(session_id).model == self.connection.ref
        argv, cwd, env = self.spawns[-1]
        mcp = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--mcp-config="))
        memory = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--settings="))
        prefs = self.state.prefs(session_id)
        assert argv == build_argv(
            ("synthetic-claude",),
            Launch(session_id, resume=resume, mcp_config=mcp, bot_memory_settings=memory),
            mode=prefs.mode,
            effort=prefs.effort,
            model=self.connection.model,
        )
        assert cwd == self.vault
        assert json.loads(Path(memory).read_text(encoding="ascii")) == {
            "autoMemoryDirectory": str(self.vault / "bot-memory" / owner),
            "autoMemoryEnabled": False,
            "claudeMdExcludes": instruction_excludes(self.vault),
        }
        assert Path(mcp).parent == self.data / "mcp"
        assert env["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"] == "1"
        assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:8765/api/llm/runtime/")
        assert env["ANTHROPIC_AUTH_TOKEN"]
        assert FORM["api_key"] not in env.values()
        assert self.billing_calls == self.account_calls == self.catalog_calls == []
        with self.state.store._connection() as connection:
            assert connection.execute(
                "SELECT native_started FROM session_profile_bindings WHERE session_id=?",
                (session_id,),
            ).fetchone() == (1,)


@pytest.mark.parametrize("runtime_billing_refused", [False, True])
def test_main_task_and_shared_thread_use_pinned_connection_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runtime_billing_refused: bool
) -> None:
    rig = ConnectionRig(tmp_path, monkeypatch)
    rig.readiness.observe_start_error("not_logged_in")
    if runtime_billing_refused:
        rig.readiness.observe_start_error("billing")
    with TestClient(rig.application, base_url=BASE) as client:
        bot = rig.bot(client)
        # Canonical main chat uses the Bot adapter, not the generic prepare helper.
        main = client.post(
            f"/api/v2/bots/{bot['bot_id']}/chat",
            headers=WRITE,
            json={"expected_state_revision": 1},
        )
        assert main.status_code == 200, main.text
        preview = main.json()["preview"]
        assert preview["session_id"] == bot["chat_session_id"]
        rig.send(client, monkeypatch, preview, "Synthetic main chat")
        rig.assert_routed(preview, bot["bot_id"])

        # New task matches BotThreads.create: start-preview with explicit owner, then durable send.
        task = rig.task(client, bot)
        rig.send(client, monkeypatch, task, "Synthetic Bot task")
        rig.assert_routed(task, bot["bot_id"])
        listed = client.get(f"/api/v2/bots/{bot['bot_id']}/threads", headers=AUTH)
        assert [row["session_id"] for row in listed.json()["threads"]] == [task["session_id"]]

        # Shared thread mirrors botThreads.prepare/persist/send: member binding and command refs.
        thread_id, message_id = ident(), ident()
        path = f"/api/v2/bot-threads/{rig.workspace_id}/{thread_id}"
        state = {
            "title": "Synthetic shared thread",
            "members": [
                {
                    "key": "chief",
                    "bot_id": bot["bot_id"],
                    "profile_id": rig.profile_id,
                    "session_id": None,
                    "enabled": True,
                }
            ],
            "messages": [],
            "phase": "idle",
        }
        assert (
            client.put(
                path, headers=WRITE, json={"expected_revision": 0, "state": state}
            ).status_code
            == 200
        )
        detail = client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH).json()
        thread = rig.task(client, detail["bot"])
        state["members"][0]["session_id"] = thread["session_id"]
        assert (
            client.put(
                path, headers=WRITE, json={"expected_revision": 1, "state": state}
            ).status_code
            == 200
        )
        command = rig.send(
            client,
            monkeypatch,
            thread,
            f"Shared Bot thread {thread_id}; message {message_id}\nSynthetic handoff",
        )
        rig.assert_routed(thread, bot["bot_id"])
        state["messages"] = [
            {
                "id": message_id,
                "at": "2026-11-10T00:00:00.000Z",
                "member_key": "chief",
                "session_id": thread["session_id"],
                "command_id": command,
            }
        ]
        state["phase"] = "done"
        saved = client.put(path, headers=WRITE, json={"expected_revision": 2, "state": state})
        assert saved.status_code == 200, saved.text
        groups = client.get(f"/api/v2/bot-threads/{rig.workspace_id}", headers=AUTH).json()
        assert groups["threads"][0]["state"] == state
        assert len({preview["session_id"], task["session_id"], thread["session_id"]}) == 3

        # Reopen has a real resume spawn, even with the runtime subscription refusal still latched.
        assert client.portal is not None
        client.portal.call(rig.manager_instance.close, preview["session_id"])
        reopened = client.post(
            f"/api/v2/bots/{bot['bot_id']}/chat",
            headers=WRITE,
            json={"expected_state_revision": 1},
        )
        assert reopened.status_code == 200, reopened.text
        rig.send(client, monkeypatch, reopened.json()["preview"], "Synthetic resumed main")
        rig.assert_routed(reopened.json()["preview"], bot["bot_id"], resume=True)
        assert _login(rig.readiness) == "signed_out"
    assert all(child.released for child in rig.spawner.children)
    gateway = rig.manager_instance._llm_gateway
    assert gateway is not None
    assert not gateway._routes


def test_removed_connection_keeps_bot_preview_blockers_and_refuses_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = ConnectionRig(tmp_path, monkeypatch)
    with TestClient(rig.application, base_url=BASE) as client:
        bot = rig.bot(client)
        prepared = client.post(
            f"/api/v2/bots/{bot['bot_id']}/chat",
            headers=WRITE,
            json={"expected_state_revision": 1},
        ).json()["preview"]
        rig.send(client, monkeypatch, prepared, "Synthetic initial main")
        assert client.portal is not None
        client.portal.call(rig.manager_instance.close, prepared["session_id"])
        rig.connections.write(2, [])
        inspected = client.get(f"/api/v2/profile-sessions/{prepared['session_id']}", headers=AUTH)
        assert inspected.status_code == 200, inspected.text
        blockers = inspected.json()["blockers"]
        assert blockers == ["runtime.model", "connection_missing"]
        detail = client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)
        # Bot status names the connection reason in place of the field it blocks.
        assert detail.json()["chat_status"] == {
            "state": "blocked",
            "blockers": ["connection_missing"],
        }
        reopened = client.post(
            f"/api/v2/bots/{bot['bot_id']}/chat",
            headers=WRITE,
            json={"expected_state_revision": 1},
        )
        assert reopened.status_code == 200, reopened.text
        prepared = reopened.json()["preview"]
        assert not prepared["launchable"] and prepared["blockers"] == blockers
        saved = client.put(
            f"/api/v2/drafts/{prepared['session_id']}",
            headers=WRITE,
            json={
                "expected_revision": prepared["draft"]["revision"],
                "text": "Synthetic refused resume",
            },
        )
        assert saved.status_code == 200, saved.text
        command_id = ident()
        with terminal_signal(monkeypatch, command_id) as done:
            accepted = client.post(
                "/api/v2/commands",
                headers=WRITE,
                json={
                    "command_id": command_id,
                    "session_id": prepared["session_id"],
                    "generation": prepared["generation"],
                    "draft_revision": saved.json()["revision"],
                },
            )
            assert accepted.status_code == 202, accepted.text
            assert done.wait(HANG_GUARD_S)
        assert fetch_command(client, command_id)["reason"] == "start_failed"
        actor = rig.manager_instance.get(prepared["session_id"])
        assert actor is not None
        errors = [event for event in actor.events_after(0) if event["type"] == "error"]
        assert errors[-1]["data"]["message"] == (
            "Profile launch refused (connection_missing). "
            "Choose an available model connection in Providers."
        )
        assert len(rig.spawns) == 1 and not rig.billing_calls


def test_billing_preflight_conflict_blocks_a_pinned_bot_chat_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = ConnectionRig(tmp_path, monkeypatch)
    # A conflicting launch setting, not a runtime subscription refusal: the route gate holds.
    rig.readiness.observe_billing(["billing_env_key"])
    with TestClient(rig.application, base_url=BASE) as client:
        bot = rig.bot(client)
        response = client.post(
            f"/api/v2/bots/{bot['bot_id']}/chat",
            headers=WRITE,
            json={"expected_state_revision": 1},
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "provider_unavailable"
        status = client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH).json()["chat_status"]
        assert status["state"] == "unprepared"
    assert rig.spawns == [] and not rig.billing_calls


def test_native_routine_and_ordinary_positive_control_keep_subscription_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = ConnectionRig(tmp_path, monkeypatch)
    rig.readiness.observe_start_error("not_logged_in")
    with TestClient(rig.application, base_url=BASE) as client:
        bot = rig.bot(client)
        # The same pinned profile runs through the routine API and TaskRunner's native adapter.
        rig.connections.write(2, [])
        created = client.post(
            "/api/v2/routines",
            headers=WRITE,
            json={
                "command_id": ident(),
                "document": _document_raw(
                    profile_id=rig.profile_id,
                    workspace_id=rig.workspace_id,
                    schedule=_manual_schedule(),
                ),
            },
        )
        assert created.status_code == 201, created.text
        routine_id = created.json()["routine_id"]
        run = client.post(
            f"/api/v2/routines/{routine_id}/run",
            headers=WRITE,
            json={"command_id": ident(), "expected_revision": 1},
        )
        assert run.status_code == 202, run.text
        assert client.portal is not None

        async def joined() -> None:
            await asyncio.gather(*list(rig.application.state.routine_tasks._runs))

        client.portal.call(joined)
        sid = run.json()["session_id"]
        assert rig.state.prefs(sid).model == "sonnet"
        argv, _, env = rig.spawns[0]
        assert "--model=sonnet" in argv and not any(rig.connection.ref in arg for arg in argv)
        assert not any(key.startswith("ANTHROPIC_") for key in env)
        assert len(rig.billing_calls) == 1 and len(rig.account_calls) == 2
        assert rig.catalog_calls == []  # Profile starts do not record a native menu.
        runs = client.get(f"/api/v2/routines/{routine_id}/runs", headers=AUTH).json()
        assert runs["runs"][0]["attempt"]["outcome"] == "succeeded"
        with rig.state.store._connection() as connection:
            assert connection.execute(
                "SELECT bot_id FROM session_bot_memory WHERE session_id=?", (sid,)
            ).fetchone() == (bot["bot_id"],)

        # An ordinary native start exercises the actual account and catalog observers together.
        ordinary = ident()
        with authed(client) as ws:
            generation = open_session(ws, ordinary)
            rig.turn_done.clear()
            ws.send_json(
                {
                    "type": "turn.send",
                    "session_id": ordinary,
                    "generation": generation,
                    "command_id": ident(),
                    "text": "Synthetic native control",
                }
            )
            assert rig.turn_done.wait(HANG_GUARD_S)
        assert len(rig.billing_calls) == 2 and len(rig.account_calls) == 4
        assert len(rig.catalog_calls) == 1
        models = client.get("/api/v2/providers/claude/models", headers=AUTH).json()["models"]
        assert [row["value"] for row in models] == ["default", "sonnet"]
