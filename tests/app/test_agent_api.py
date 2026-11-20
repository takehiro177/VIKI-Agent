"""The agent surface over real settings, routine and Bot services on a synthetic `AppState`, with a
stub task runner and scheduler: its admission, its levels, its credential filter and its journal.
The window's Apply is `AgentChanges.routes` in the same app behind a stand-in window token;
server.py's wiring of both is not exercised here."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tests.automation.test_routines import _document_raw
from tests.persistence.test_storage import _workspace
from tests.profiles.test_profile_document import document
from viki_agent.app.agent_api import CHANGE_SETTINGS, COVERS, OPS, SAFETY_SETTINGS, agent_routes
from viki_agent.app.agent_changes import AgentChanges
from viki_agent.app.agent_grants import AgentGrants
from viki_agent.app.diagnostics import is_credential_key
from viki_agent.app.settings import KEYS, SettingsError, SettingsService, check_change
from viki_agent.automation.routines import RoutineService
from viki_agent.automation.scheduler import Scheduler
from viki_agent.automation.tasks import Attempt, TaskRunner
from viki_agent.domain import SettingsDocument
from viki_agent.messaging.bots import BotService
from viki_agent.profiles.service import ProfileService
from viki_agent.state import AppState, Prefs
from viki_agent.workspaces import WorkspaceService, root_key

BASE = "http://127.0.0.1:8765"
WINDOW = {"authorization": "Bearer window-token"}
APPLY = {**WINDOW, "origin": BASE}
SESSION = "11111111-1111-4111-8111-111111111111"
VERSION = "9.8.7"
NOW = datetime(2026, 11, 20, tzinfo=UTC)
SENTINEL = "sentinel-sealed-bundle-7f3a9c"
HANG_GUARD_S = 10.0  # a guard on events that fire in milliseconds, not a work budget
CHANGED = "The setting changed since the chat asked; review its current value."


def ident() -> str:
    return str(uuid.uuid4())


class StubRunner:
    """`run_now` and `store.summaries` alone. Each run is recorded with what `scheduler_ready`
    answered, and waits for `gate` when one is set."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int, bool]] = []
        self.summaries: dict[str, dict[str, Any]] = {}
        self.store = SimpleNamespace(summaries=lambda: self.summaries)
        self.gate: asyncio.Event | None = None

    async def run_now(
        self,
        routine_id: str,
        command_id: str,
        expected_revision: int,
        *,
        scheduler_ready: Callable[[], bool],
    ) -> tuple[Attempt, bool]:
        self.calls.append((routine_id, command_id, expected_revision, scheduler_ready()))
        if self.gate is not None:
            await self.gate.wait()
        attempt = Attempt(
            attempt_id=ident(),
            occurrence_id=ident(),
            routine_id=routine_id,
            revision=expected_revision,
            kind="manual",
            session_id=ident(),
        )
        return attempt, True


class StubScheduler:
    def __init__(self) -> None:
        self.state = "running"
        self.wakes = 0

    def wake(self) -> None:
        self.wakes += 1


class Rig:
    """Real services on one synthetic store with an active ordinary vault, one profile and one
    live grant for SESSION; `bots=False` stands for an app without Bots."""

    def __init__(self, tmp_path: Path, *, bots: bool = True) -> None:
        self.state = AppState(tmp_path / "data" / "state.json", Prefs())
        store = self.state.store
        self.settings = SettingsService(
            store, provider_defaults=Prefs, on_chat_defaults=self.state.set_defaults
        )
        self.workspaces = WorkspaceService(
            store,
            env={},
            data_dir=tmp_path / "data",
            home=tmp_path / "home",
            install_dir=tmp_path / "install",
            git_available=lambda: False,
            hide_previews=lambda: False,
        )
        root = tmp_path / "vault"
        root.mkdir()
        info = root.stat()
        self.workspace_id = ident()
        row = _workspace(
            self.workspace_id,
            canonical_key=root_key(root.resolve()),
            display_root=str(root),
            physical_id=f"{info.st_dev}:{info.st_ino}",
            bootstrap="complete",
            active=1,
        )
        store.update_workspace(self.workspace_id, lambda _current, _rows: row)
        self.profiles = ProfileService(store)
        self.profile_id = ident()
        self.profiles.create(self.profile_id, ident(), document())
        self.routines = RoutineService(
            store, self.profiles, self.settings, self.workspaces, now=lambda: NOW
        )
        self.bots = (
            BotService(store, self.profiles, self.routines, self.workspaces, now=lambda: NOW)
            if bots
            else None
        )
        self.runner = StubRunner()
        self.scheduler = StubScheduler()
        self.grants = AgentGrants(tmp_path / "viki-agent")
        self.changes = AgentChanges(self.grants.live)
        grant = self.grants.issue(SESSION)
        assert grant is not None
        self.grant = grant
        routes = agent_routes(
            self.grants,
            self.changes,
            settings=self.settings,
            routines=self.routines,
            runner=cast(TaskRunner, self.runner),
            scheduler=cast(Scheduler, self.scheduler),
            bots=self.bots,
            profiles=self.profiles,
            workspaces=self.workspaces,
            admission_lock=asyncio.Lock(),
            app_version=VERSION,
            workers=set(),
        )
        routes += self.changes.routes(
            authorized=lambda request: (
                request.headers.get("authorization") == WINDOW["authorization"]
            ),
            origins=frozenset({BASE}),
            workers=set(),
        )
        self.app = Starlette(routes=routes)
        self.client = TestClient(self.app, base_url=BASE)

    def path(self, operation: str) -> str:
        return f"/api/agent/{self.grant.id}/{operation}"

    def key(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.grant.token}"}

    def send(self, method: str, operation: str, body: dict[str, Any] | None = None) -> Any:
        return self.client.request(method, self.path(operation), headers=self.key(), json=body)

    def decide(self, change_id: str, decision: str = "apply") -> Any:
        return self.client.post(
            f"/api/v2/agent-changes/{change_id}", headers=APPLY, json={"decision": decision}
        )

    def held(self) -> list[dict[str, Any]]:
        return self.client.get("/api/v2/agent-changes", headers=WINDOW).json()["changes"]

    def journal(self) -> list[dict[str, Any]]:
        return self.changes.for_session(SESSION)["recent"]


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    rig = Rig(tmp_path)
    with rig.client:
        yield rig


def code(response: Any) -> tuple[int, str]:
    """The status and refusal code. `Any`: Starlette's TestClient answers with httpx2's Response,
    httpx.AsyncClient with httpx's."""
    return response.status_code, response.json()["error"]["code"]


def setting(key: str, value: object, revision: int = 0) -> dict[str, Any]:
    return {"command_id": ident(), "expected_revision": revision, "key": key, "value": value}


def test_ops_and_their_levels_are_the_pinned_table() -> None:
    assert OPS == {
        ("GET", "status"): "read",
        ("GET", "settings"): "read",
        ("PATCH", "settings"): "change",
        ("GET", "routines"): "read",
        ("POST", "routines"): "change",
        ("POST", "routines/{id}/run"): "change",
        ("POST", "routines/{id}/enable"): "safety",
        ("POST", "bots"): "change",
    }
    assert set(COVERS) == set(OPS)
    # The Apply card's list, D-1's additions, then every other `decision.*` key.
    assert {
        "chat.default_mode",
        "decision.enabled",
        "decision.grant.run",
        "decision.limit.chats",
        "decision.team.handoffs",
        "decision.plan_brake",
        "chat.default_model",
        "chat.other_sign_in",
        "chat.workspace_ids",
        "routines.paused",
        "decision.send_level",
        "decision.never_send",
        "decision.watch.inbox",
        "decision.daily_cap_usd",
    } <= SAFETY_SETTINGS
    assert {key for key in KEYS if key.startswith("decision.")} <= SAFETY_SETTINGS
    # The change level, pinned: every other key waits for Apply until it is listed here.
    assert {
        "appearance.theme",
        "appearance.density",
        "accessibility.font_scale",
        "accessibility.reduce_motion",
        "layout.left_rail_width",
        "layout.right_rail_width",
        "layout.launcher",
        "layout.launcher_panel",
        "workbench.code_font_size",
        "workbench.line_wrap",
        "bots.hues",
        "bots.icons",
        "voice.read_aloud_rate",
        "voice.read_aloud_voices",
    } == CHANGE_SETTINGS


def test_every_key_is_exactly_one_of_credential_change_or_safety() -> None:
    for key in KEYS:
        levels = [is_credential_key(key), key in CHANGE_SETTINGS, key in SAFETY_SETTINGS]
        assert levels.count(True) == 1, key
    assert set(KEYS) >= CHANGE_SETTINGS | SAFETY_SETTINGS


def test_a_missing_wrong_window_revoked_or_unknown_key_gets_one_identical_401(rig: Rig) -> None:
    revoked = rig.grants.issue(SESSION)
    assert revoked is not None
    revoked.revoke()
    live = ("authorization", f"Bearer {rig.grant.token}")
    attempts: list[tuple[str, list[tuple[str, str]]]] = [
        (rig.grant.id, []),
        (rig.grant.id, [("authorization", "Bearer wrong")]),
        (rig.grant.id, [("authorization", WINDOW["authorization"])]),
        (revoked.id, [("authorization", f"Bearer {revoked.token}")]),
        (ident(), [live]),
        (rig.grant.id, [live, live]),
        (rig.grant.id, [("authorization", rig.grant.token)]),
    ]
    answers = set()
    for grant_id, headers in attempts:
        response = rig.client.patch(
            f"/api/agent/{grant_id}/settings",
            headers=headers,
            json=setting("appearance.theme", "dark"),
        )
        assert code(response) == (401, "unauthorized")
        assert response.headers["cache-control"] == "no-store"
        answers.add(response.content)
    assert len(answers) == 1
    assert rig.settings.read().document.revision == 0
    assert rig.journal() == []


def test_any_origin_gets_403_and_changes_nothing(rig: Rig) -> None:
    for origin in (BASE, "https://example.com", "null"):
        headers = {**rig.key(), "origin": origin}
        assert code(rig.client.get(rig.path("status"), headers=headers)) == (
            403,
            "forbidden_origin",
        )
        refused = rig.client.patch(
            rig.path("settings"), headers=headers, json=setting("appearance.theme", "dark")
        )
        assert code(refused) == (403, "forbidden_origin")
    assert rig.settings.read().document.revision == 0


def test_a_query_an_unknown_operation_or_a_malformed_body_is_refused(rig: Rig) -> None:
    status = rig.client.get(rig.path("status") + "?detail=1", headers=rig.key())
    assert code(status) == (400, "invalid_input")
    for method, operation in (
        ("GET", "nope"),
        ("POST", "status"),
        ("PATCH", "routines"),
        ("GET", f"routines/{ident()}/run"),
        ("POST", f"routines/{ident()}/archive"),
        ("POST", "routines/run"),
    ):
        response = rig.client.request(method, rig.path(operation), headers=rig.key())
        assert code(response) == (404, "not_found")
    good = setting("appearance.theme", "dark")
    for content in (
        json.dumps({**good, "extra": 1}),
        json.dumps({key: good[key] for key in ("command_id", "expected_revision", "key")}),
        "not json",
        "[]",
        '{"key":"a","key":"b","command_id":"c","expected_revision":0,"value":1}',
    ):
        response = rig.client.patch(rig.path("settings"), headers=rig.key(), content=content)
        assert code(response) == (400, "invalid_input")
    huge = json.dumps({**good, "value": "x" * (128 << 10)})
    assert code(rig.client.patch(rig.path("settings"), headers=rig.key(), content=huge)) == (
        413,
        "too_large",
    )
    assert rig.settings.read().document.revision == 0


def test_status_and_settings_leave_out_the_sealed_connections(rig: Rig) -> None:
    rig.settings.patch(ident(), 0, {"chat.llm_connections": SENTINEL, "appearance.theme": "dark"})
    settings, status = rig.send("GET", "settings"), rig.send("GET", "status")
    for response in (settings, status):
        assert response.status_code == 200
        assert SENTINEL not in response.text
        assert "chat.llm_connections" not in response.text
    view = settings.json()
    assert view["values"] == {"appearance.theme": "dark"}
    assert view["effective"]["appearance.theme"] == "dark"
    assert set(view["keys"]) == set(view["effective"]) == set(KEYS) - {"chat.llm_connections"}
    assert view["revision"] == status.json()["settings_revision"] == 1


def test_a_credential_key_gets_403_and_stores_nothing(rig: Rig) -> None:
    for key in ("chat.llm_connections", "chat.api_key", "decision.token"):
        response = rig.send("PATCH", "settings", setting(key, SENTINEL))
        assert code(response) == (403, "secret_key")
    effective = rig.settings.read()
    assert (effective.document.revision, effective.document.values) == (0, {})
    assert rig.held() == []
    assert rig.journal() == []


def test_check_change_names_the_code_a_patch_refuses_with(rig: Rig) -> None:
    assert check_change("appearance.theme", "dark") is None
    assert check_change("appearance.theme", None) is None
    assert check_change("appearance.theme", 1) == "invalid_type"
    assert check_change("no.such_key", True) == "unknown_key"
    with pytest.raises(SettingsError) as refused:
        rig.settings.patch(ident(), 0, {"appearance.theme": "neon"})
    assert refused.value.fields == {"appearance.theme": check_change("appearance.theme", "neon")}
    for key, value, field in (
        ("appearance.theme", "neon", "not_allowed"),
        ("chat.default_mode", "bypassPermissions", "not_allowed"),
        ("no.such_key", True, "unknown_key"),
    ):
        response = rig.send("PATCH", "settings", setting(key, value))
        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "invalid_settings",
            "message": "Check the highlighted settings.",
            "fields": {key: field},
        }
    assert rig.settings.read().document.revision == 0
    assert rig.held() == []


def test_a_change_key_applies_at_once_and_journals_the_chat(rig: Rig) -> None:
    body = setting("appearance.theme", "dark")
    first = rig.send("PATCH", "settings", body)
    assert (first.status_code, first.json()) == (200, {"applied": True, "revision": 1})
    assert rig.settings.read().effective["appearance.theme"] == "dark"
    replay = rig.send("PATCH", "settings", body)
    assert (replay.status_code, replay.json()) == (200, {"applied": True, "revision": 1})
    [entry] = rig.send("GET", "status").json()["changes"]["recent"]
    assert entry == {
        "change_id": entry["change_id"],
        "session_id": SESSION,
        "operation": "settings.set",
        "target": "appearance.theme",
        "state": "applied",
    }
    assert rig.changes.seq == 1
    stale = rig.send("PATCH", "settings", setting("appearance.theme", "light"))
    assert code(stale) == (409, "stale_revision")
    assert rig.held() == []


def test_a_safety_key_waits_for_the_window_s_apply(rig: Rig) -> None:
    held = rig.send("PATCH", "settings", setting("chat.default_mode", "plan"))
    assert held.status_code == 202
    pending = held.json()["pending"]
    assert pending == {"change_id": pending["change_id"], "expires_in_s": 600}
    assert rig.settings.read().effective["chat.default_mode"] == "default"
    assert rig.settings.read().document.revision == 0
    [listed] = rig.held()
    assert listed == {
        "change_id": pending["change_id"],
        "session_id": SESSION,
        "kind": "setting",
        "summary": {"key": "chat.default_mode", "from": "default", "to": "plan", "reset": False},
        "expires_in_s": 600,
    }
    # The chat sees its hold by key only; the values are for the window's Apply card.
    assert rig.send("GET", "status").json()["changes"]["pending"] == [
        {
            "change_id": pending["change_id"],
            "kind": "setting",
            "key": "chat.default_mode",
            "expires_in_s": 600,
        }
    ]
    applied = rig.decide(pending["change_id"])
    assert (applied.status_code, applied.json()) == (
        200,
        {"state": "applied", "result": {"revision": 1}},
    )
    assert rig.settings.read().effective["chat.default_mode"] == "plan"
    assert rig.state.defaults.mode == "plan"
    assert [(entry["target"], entry["state"]) for entry in rig.journal()] == [
        ("chat.default_mode", "applied")
    ]
    assert rig.decide(pending["change_id"]).status_code == 409

    reset = rig.send("PATCH", "settings", setting("chat.default_mode", None, 1)).json()["pending"]
    [listed] = rig.held()
    assert listed["summary"] == {
        "key": "chat.default_mode",
        "from": "plan",
        "to": None,
        "reset": True,
    }
    assert rig.decide(reset["change_id"], "reject").json() == {"state": "rejected"}
    assert rig.settings.read().effective["chat.default_mode"] == "plan"


def test_an_identity_name_waits_for_apply(rig: Rig) -> None:
    held = rig.send("PATCH", "settings", setting("identity.display_name", "Taro"))
    assert held.status_code == 202
    assert rig.settings.read().document.revision == 0
    [listed] = rig.held()
    assert listed["summary"] == {
        "key": "identity.display_name",
        "from": "",
        "to": "Taro",
        "reset": False,
    }
    assert rig.decide(held.json()["pending"]["change_id"]).status_code == 200
    assert rig.settings.read().effective["identity.display_name"] == "Taro"


def test_a_held_setting_survives_unrelated_writes_but_not_a_change_to_its_own_key(
    rig: Rig,
) -> None:
    pending = rig.send("PATCH", "settings", setting("routines.paused", True)).json()["pending"]
    # Before Apply: the chat's own next change, then the window's pane layout save.
    assert rig.send("PATCH", "settings", setting("appearance.theme", "dark")).status_code == 200
    rig.settings.patch(ident(), 1, {"layout.panes": '{"v":1}'})
    applied = rig.decide(pending["change_id"])
    assert (applied.status_code, applied.json()) == (
        200,
        {"state": "applied", "result": {"revision": 3}},
    )
    assert rig.settings.read().effective["routines.paused"] is True
    held = rig.send("PATCH", "settings", setting("chat.default_mode", "plan", 3))
    rig.settings.patch(ident(), 3, {"chat.default_mode": "acceptEdits"})
    refused = rig.decide(held.json()["pending"]["change_id"])
    assert refused.status_code == 409
    assert refused.json()["error"] == {"code": "stale_revision", "message": CHANGED}
    assert rig.settings.read().effective["chat.default_mode"] == "acceptEdits"
    malformed = setting("routines.paused", True)
    for field, value in (("command_id", "not-a-uuid"), ("expected_revision", "1")):
        response = rig.send("PATCH", "settings", {**malformed, field: value})
        assert response.json()["error"]["fields"] == {field: "invalid_type"}
    assert rig.held() == []


def test_two_settings_held_at_one_revision_both_apply(rig: Rig) -> None:
    first = rig.send("PATCH", "settings", setting("decision.enabled", True)).json()["pending"]
    second = rig.send("PATCH", "settings", setting("decision.grant.check", "allowed")).json()
    assert rig.decide(first["change_id"]).json() == {"state": "applied", "result": {"revision": 1}}
    assert rig.decide(second["pending"]["change_id"]).json() == {
        "state": "applied",
        "result": {"revision": 2},
    }
    effective = rig.settings.read().effective
    assert (effective["decision.enabled"], effective["decision.grant.check"]) == (True, "allowed")
    assert [(entry["target"], entry["state"]) for entry in rig.journal()] == [
        ("decision.grant.check", "applied"),
        ("decision.enabled", "applied"),
    ]


def test_a_held_setting_s_card_shows_at_most_2000_characters_of_each_value(rig: Rig) -> None:
    rig.settings.patch(ident(), 0, {"layout.panes": "f" * 2001})
    exact = rig.send("PATCH", "settings", setting("layout.panes", "t" * 2000, 1)).json()
    longer = rig.send("PATCH", "settings", setting("layout.panes", "t" * 2001, 1)).json()
    summaries = {row["change_id"]: row["summary"] for row in rig.held()}
    shown = {"key": "layout.panes", "from": "f" * 2000, "to": "t" * 2000, "reset": False}
    assert summaries[exact["pending"]["change_id"]] == {**shown, "from_truncated": True}
    assert summaries[longer["pending"]["change_id"]] == {
        **shown,
        "from_truncated": True,
        "to_truncated": True,
    }
    assert rig.decide(longer["pending"]["change_id"]).status_code == 200
    assert rig.settings.read().effective["layout.panes"] == "t" * 2001


@pytest.mark.parametrize(
    ("stored", "sent", "shown"),
    [
        # Neither longer than 2,000 characters: both whole, and the summary's shape unchanged.
        ("a" * 1990, "a" * 1990 + "b" * 10, {"from": "a" * 1990, "to": "a" * 1990 + "b" * 10}),
        # A change at the very end of a long value: both from 100 characters before it.
        (
            "a" * 3000,
            "a" * 3000 + "b",
            {"from": "a" * 100, "to": "a" * 100 + "b", "from_offset": 2900, "to_offset": 2900},
        ),
    ],
    ids=["short", "change_at_the_end"],
)
def test_a_held_setting_s_card_shows_where_its_value_changes(
    rig: Rig, stored: str, sent: str, shown: dict[str, object]
) -> None:
    rig.settings.patch(ident(), 0, {"layout.panes": stored})
    assert rig.send("PATCH", "settings", setting("layout.panes", sent, 1)).status_code == 202
    [listed] = rig.held()
    assert listed["summary"] == {"key": "layout.panes", "reset": False, **shown}


def test_a_change_past_2000_equal_characters_shows_on_the_card(rig: Rig) -> None:
    # 32 never-send folders; the proposal keeps the first 20, swaps the last 12, and is
    # re-spaced, so its raw text already differs from the stored one at the first comma.
    kept = [f"private/client-{n:03d}-{'x' * 100}/" for n in range(32)]
    swapped = [f"archive/old-{n:03d}-{'y' * 100}/" for n in range(12)]
    stored = json.dumps(kept, separators=(",", ":"))
    proposed = json.dumps(kept[:20] + swapped, separators=(",", ":"))
    rig.settings.patch(ident(), 0, {"decision.never_send": stored})
    held = rig.send(
        "PATCH", "settings", setting("decision.never_send", json.dumps(kept[:20] + swapped), 1)
    )
    [listed] = rig.held()
    start = proposed.index(swapped[0]) - 100
    assert listed["summary"] == {
        "key": "decision.never_send",
        "from": stored[start : start + 2000],
        "to": proposed[start : start + 2000],
        "reset": False,
        "from_offset": start,
        "to_offset": start,
    }
    assert all(entry in listed["summary"]["from"] for entry in kept[20:])
    assert all(entry in listed["summary"]["to"] for entry in swapped)
    assert rig.decide(held.json()["pending"]["change_id"]).status_code == 200
    assert rig.settings.read().effective["decision.never_send"] == proposed


def test_apply_retries_once_when_a_write_lands_between_its_read_and_its_patch(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = rig.settings.import_snapshot
    races: list[dict[str, object]] = []  # each read lets the next of these land right after it

    def racing_snapshot() -> SettingsDocument:
        document = snapshot()
        if races:
            rig.settings.patch(ident(), document.revision, races.pop(0))
        return document

    monkeypatch.setattr(rig.settings, "import_snapshot", racing_snapshot)
    races.append({"layout.panes": "1"})
    pending = rig.send("PATCH", "settings", setting("routines.paused", True)).json()["pending"]
    applied = rig.decide(pending["change_id"])
    assert (applied.status_code, applied.json()) == (
        200,
        {"state": "applied", "result": {"revision": 2}},
    )
    races.extend([{"layout.panes": "2"}, {"layout.panes": "3"}])
    pending = rig.send("PATCH", "settings", setting("chat.default_mode", "plan")).json()["pending"]
    assert code(rig.decide(pending["change_id"])) == (409, "stale_revision")
    races.append({"chat.default_mode": "acceptEdits"})
    pending = rig.send("PATCH", "settings", setting("chat.default_mode", "plan")).json()["pending"]
    refused = rig.decide(pending["change_id"])
    assert refused.json()["error"] == {"code": "stale_revision", "message": CHANGED}
    assert races == []
    assert rig.settings.read().effective["chat.default_mode"] == "acceptEdits"


def test_status_names_the_chat_and_what_its_commands_can_target(rig: Rig) -> None:
    routine_id = rig.routines.create(
        ident(), _document_raw(profile_id=rig.profile_id, workspace_id=rig.workspace_id)
    )[0]["routine_id"]
    response = rig.send("GET", "status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "chat": {"session_id": SESSION},
        "app": {"version": VERSION},
        "settings_revision": 0,
        "workspaces": [
            {
                "workspace_id": rig.workspace_id,
                "name": "synthetic",
                "policy": "ordinary",
                "active": True,
            }
        ],
        "profiles": [
            {"profile_id": rig.profile_id, "name": "Review helper", "current_revision": 1}
        ],
        "routines": [
            {
                "routine_id": routine_id,
                "name": "Daily brief",
                "state": "draft",
                "current_revision": 1,
                "state_revision": 1,
                "running": False,
            }
        ],
        "bots": [],
        "changes": {"pending": [], "recent": []},
    }


def test_a_bot_is_created_and_journaled(rig: Rig) -> None:
    body = {
        "bot_id": ident(),
        "profile_id": rig.profile_id,
        "expected_profile_revision": 1,
        "workspace_id": rig.workspace_id,
    }
    view = {key: body[key] for key in ("bot_id", "profile_id", "workspace_id")}
    created = rig.send("POST", "bots", body)
    assert (created.status_code, created.json()) == (201, {"bot": view})
    replay = rig.send("POST", "bots", body)
    assert (replay.status_code, replay.json()) == (200, {"bot": view})
    assert rig.send("GET", "status").json()["bots"] == [view]
    assert [(entry["operation"], entry["target"]) for entry in rig.journal()] == [
        ("bots.create", body["bot_id"])
    ]
    assert code(rig.send("POST", "bots", {**body, "bot_id": ident()})) == (409, "conflict")


def test_without_bots_creating_one_gets_503(tmp_path: Path) -> None:
    rig = Rig(tmp_path, bots=False)
    body = {
        "bot_id": ident(),
        "profile_id": rig.profile_id,
        "expected_profile_revision": 1,
        "workspace_id": rig.workspace_id,
    }
    with rig.client:
        assert code(rig.send("POST", "bots", body)) == (503, "unavailable")
        assert rig.send("GET", "status").json()["bots"] == []
    assert rig.journal() == []


def test_no_log_record_holds_the_key(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path, bots=False)
    token = rig.grant.token
    with rig.client:
        rig.send("GET", "status")
        rig.send("GET", "settings")
        rig.send("PATCH", "settings", setting("appearance.theme", "dark"))
        rig.send("PATCH", "settings", setting("decision.enabled", True, 1))
        rig.send("PATCH", "settings", setting("chat.llm_connections", token))
        bot = {
            "bot_id": ident(),
            "profile_id": rig.profile_id,
            "expected_profile_revision": 1,
            "workspace_id": rig.workspace_id,
        }
        rig.send("POST", "bots", bot)
        rig.client.get(rig.path("status"), headers={"authorization": f"Bearer {token}x"})
    assert "agent operation refused: unavailable" in caplog.text
    assert token not in caplog.text
    assert all(token not in repr(record.args) for record in caplog.records)


async def test_a_fifth_operation_in_flight_gets_429(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    gate = rig.runner.gate = asyncio.Event()
    transport = httpx.ASGITransport(app=rig.app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:

        async def run() -> httpx.Response:
            body = {"command_id": ident(), "expected_revision": 1}
            return await client.post(
                rig.path(f"routines/{ident()}/run"), headers=rig.key(), json=body
            )

        running = [asyncio.create_task(run()) for _ in range(4)]
        async with asyncio.timeout(HANG_GUARD_S):
            while len(rig.runner.calls) < 4:
                await asyncio.sleep(0)
        assert code(await client.get(rig.path("status"), headers=rig.key())) == (
            429,
            "overloaded",
        )
        gate.set()
        assert [response.status_code for response in await asyncio.gather(*running)] == [202] * 4
        assert (await client.get(rig.path("status"), headers=rig.key())).status_code == 200
