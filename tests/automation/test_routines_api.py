"""Authenticated routine routes: first over real services, an unstarted scheduler and no
provider; then wired into the full app, whose lifespan starts and stops the scheduler."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tests.app.test_imports import FakePatch, envelope
from tests.app.test_imports import service as import_service
from tests.automation.test_routines import _daily_schedule, _document_raw, _manual_schedule
from tests.automation.test_scheduler import FakeClock
from tests.profiles.test_profile_document import document as profile_document
from tests.profiles.test_profile_launch_api import (
    AUTH,
    BASE,
    HANG_GUARD_S,
    TOKEN,
    WRITE,
    FakeDriver,
)
from tests.profiles.test_profile_launch_api import Rig as LaunchRig
from tests.test_window import FakeWindow
from viki_agent.app.imports import ImportServiceError
from viki_agent.app.settings import SettingsError
from viki_agent.automation import routines_api
from viki_agent.automation.routines import (
    RoutineError,
    RoutineService,
    parse_document,
    parse_stamp,
    record_tested,
    stamp,
)
from viki_agent.automation.routines_api import MAX_BODY, PREVIEW, routine_routes
from viki_agent.automation.scheduler import Scheduler
from viki_agent.automation.tasks import MAX_OCCURRENCES, Attempt, TaskRunner, TaskStore
from viki_agent.catalog import Catalog
from viki_agent.config import Config
from viki_agent.conversations.command_runtime import CommandRunner
from viki_agent.conversations.commands import CommandInput, CommandService
from viki_agent.conversations.drafts import DraftService, draft_scope
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.events import TurnDone, Usage
from viki_agent.messaging.bot_memory_rule import bot_system_prompt
from viki_agent.profiles.service import ProfileError, ProfileService
from viki_agent.server import create_app
from viki_agent.session import SessionManager
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError
from viki_agent.window import WindowManager

HEADERS = {"Authorization": "Bearer synthetic", "Origin": "http://localhost:8765"}
JSON = {**HEADERS, "Content-Type": "application/json"}
NOW = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
OWNER = "44444444-4444-4444-8444-4444444444ab"
BLOCKERS = ["profile_missing", "workspace_missing", "settings_unavailable"]


class Rig:
    """`launch` lends its profile, settings and registered, active workspace; without it the
    routines have neither settings nor workspaces."""

    def __init__(self, tmp_path: Path, launch: LaunchRig | None = None) -> None:
        vault, home = tmp_path / "vault", tmp_path / "home"
        if launch is None:
            vault.mkdir()
            home.mkdir()
            self.state = AppState(tmp_path / "data" / "state.json", Prefs())
        else:
            self.state = launch.state
        store = self.state.store
        clock = FakeClock(NOW)
        self.service = RoutineService(
            store,
            ProfileService(store),
            launch.settings if launch else None,
            launch.workspaces if launch else None,
            now=clock.wall,
        )
        manager = SessionManager(
            config=DriverConfig(cli=("must-not-run",), vault=vault, env={}),
            state=self.state,
            claude_home=home,
        )
        self.runner = runner = TaskRunner(
            TaskStore(store, draft_scope(vault), OWNER),
            self.service,
            CommandRunner(CommandService(self.state, vault, owner=OWNER), manager),
            manager,
            DraftService(self.state, vault),
            ProfileService(store),
            prepare=None,
            clock=clock,
            attended=lambda: True,
        )
        self.scheduler = Scheduler(self.service, runner, clock, lambda: True)
        self.wakes = 0
        self.api = TestClient(
            Starlette(
                routes=routine_routes(
                    self.service,
                    runner,
                    self.scheduler,
                    authorized=lambda request: (
                        request.headers.get("authorization") == "Bearer synthetic"
                    ),
                    origins=frozenset({"http://localhost:8765"}),
                    workers=set(),
                )
            )
        )

    def wake(self) -> None:
        self.wakes += 1


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    rig = Rig(tmp_path)
    monkeypatch.setattr(rig.scheduler, "wake", rig.wake)
    return rig


@pytest.fixture
def launched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Rig, LaunchRig]:
    launch = LaunchRig(tmp_path)
    rig = Rig(tmp_path, launch)
    monkeypatch.setattr(rig.scheduler, "wake", rig.wake)
    return rig, launch


def _no_store(response: Any) -> Any:
    assert response.headers["cache-control"] == "no-store"
    return response


def test_create_replay_read_and_revise(rig: Rig) -> None:
    request = {"command_id": str(uuid4()), "document": _document_raw()}
    with rig.api as api:
        first = _no_store(api.post("/api/v2/routines", headers=HEADERS, json=request))
        assert first.status_code == 201
        routine_id = first.json()["routine_id"]
        assert first.json() == {
            "routine_id": routine_id,
            "revision": 1,
            "state": "draft",
            "state_revision": 1,
        }
        replay = _no_store(api.post("/api/v2/routines", headers=HEADERS, json=request))
        assert (replay.status_code, replay.json()) == (200, first.json())
        assert rig.wakes == 1

        item = {
            "routine_id": routine_id,
            "name": "Daily brief",
            "state": "draft",
            "current_revision": 1,
            "state_revision": 1,
            "schedule": {"kind": "daily", "timezone": "Asia/Tokyo"},
            "next_run": None,
            "last_run": None,
            "last_attempt": None,
            "active_run": None,
        }
        listed = _no_store(api.get("/api/v2/routines", headers=HEADERS))
        assert listed.json() == {
            "routines": [item],
            "scheduler": {"state": "unavailable", "attended": True, "paused": False},
        }
        read = _no_store(api.get(f"/api/v2/routines/{routine_id}", headers=HEADERS)).json()
        assert {key: read[key] for key in item} == item
        assert read["document"] == _document_raw()
        assert (read["tested_revision"], read["activates_at"]) == (None, stamp(NOW))
        assert read["readiness"] == {
            "blockers": BLOCKERS,
            "run_blockers": ["profile_missing", "workspace_missing"],
            "warnings": [],
        }
        assert read["next_five"][0]["at"] == "2026-09-28T00:00:00.000Z"
        assert len(read["next_five"]) == 5

        revised = _no_store(
            api.patch(
                f"/api/v2/routines/{routine_id}",
                headers=HEADERS,
                json={
                    "command_id": str(uuid4()),
                    "expected_revision": 1,
                    "document": _document_raw(name="Evening brief"),
                },
            )
        )
        assert (revised.status_code, revised.json()["revision"]) == (200, 2)
        assert rig.wakes == 2


def test_only_an_enabled_routine_has_a_next_run(rig: Rig) -> None:
    routine_id = rig.service.create(str(uuid4()), _document_raw())[0]["routine_id"]
    with rig.state.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE routines SET state='enabled',enabled_since=? WHERE routine_id=?",
            (stamp(NOW), routine_id),
        )
    with rig.api as api:
        listed = api.get("/api/v2/routines", headers=HEADERS).json()["routines"]
        read = api.get(f"/api/v2/routines/{routine_id}", headers=HEADERS).json()
    assert listed[0]["next_run"] == read["next_run"] == read["next_five"][0]["at"]


def test_refusals_answer_typed_codes(rig: Rig) -> None:
    routine_id = rig.service.create(str(uuid4()), _document_raw())[0]["routine_id"]
    with rig.api as api:
        missing = api.get(f"/api/v2/routines/{uuid4()}", headers=HEADERS)
        assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")
        for bad in ("not-a-uuid", str(uuid4()).upper()):
            response = _no_store(api.get(f"/api/v2/routines/{bad}", headers=HEADERS))
            assert (response.status_code, response.json()["error"]["code"]) == (400, "invalid_id")
        stale = api.patch(
            f"/api/v2/routines/{routine_id}",
            headers=HEADERS,
            json={"command_id": str(uuid4()), "expected_revision": 5, "document": _document_raw()},
        )
        assert (stale.status_code, stale.json()["error"]["code"]) == (409, "conflict")
    assert rig.wakes == 0


def test_auth_origin_and_closed_input_are_enforced(rig: Rig) -> None:
    path = f"/api/v2/routines/{uuid4()}"
    with rig.api as api:
        assert _no_store(api.get("/api/v2/routines")).status_code == 401
        for method, target in (("POST", "/api/v2/routines"), ("PATCH", path)):
            for headers in (
                {"Authorization": "Bearer synthetic"},
                {**HEADERS, "Origin": "https://untrusted.example"},
            ):
                response = _no_store(api.request(method, target, headers=headers, json={}))
                assert response.status_code == 403
        nested = json.dumps(
            {"command_id": str(uuid4()), "document": json.loads("[" * 60 + "]" * 60)}
        )
        for raw in ('{"command_id":1,"command_id":2}', '{"document":NaN}', "[1]", nested, "{"):
            response = _no_store(api.post("/api/v2/routines", headers=JSON, content=raw))
            assert (response.status_code, response.json()["error"]["code"]) == (
                400,
                "invalid_input",
            )
        valid = json.dumps({"command_id": str(uuid4()), "document": _document_raw()})
        for headers in (
            {**HEADERS, "Content-Type": "text/plain"},
            {**JSON, "Content-Encoding": "gzip"},
        ):
            response = api.post("/api/v2/routines", headers=headers, content=valid)
            assert _code(response) == (400, "invalid_input")
        too_large = api.post("/api/v2/routines", headers=JSON, content=b" " * (MAX_BODY + 1))
        assert (_no_store(too_large).status_code, too_large.json()["error"]["code"]) == (
            413,
            "too_large",
        )
        for query in ("x=" + "a" * 512, "x=1&x=2"):
            assert (
                _no_store(api.get(f"/api/v2/routines?{query}", headers=HEADERS)).status_code == 400
            )
        assert api.get("/api/v2/routines", headers=HEADERS).json()["routines"] == []
    assert rig.wakes == 0


def test_private_routine_routes_reject_before_service_touch(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    routine_id = rig.service.create(str(uuid4()), _document_raw())[0]["routine_id"]
    with rig.state.store._connection(write=True) as connection:
        occurrence = rig.runner.store.insert_claim(
            connection,
            routine_id=routine_id,
            kind="schedule",
            revision=1,
            now=stamp(NOW),
            scheduled_for=stamp(NOW),
        )
    path = f"/api/v2/routines/{routine_id}"
    history = f"{path}/runs"
    with rig.api as api:
        assert len(api.get("/api/v2/routines", headers=HEADERS).json()["routines"]) == 1
        detail = api.get(path, headers=HEADERS).json()
        assert detail["current_revision"] == 1
        assert detail["document"]["name"] == "Daily brief"
        assert api.get(history, headers=HEADERS).json()["runs"][0]["occurrence_id"] == (
            occurrence.occurrence_id
        )

        touched: list[str] = []

        def forbidden(*_args: Any, **_kwargs: Any) -> Any:
            touched.append("routine service")
            raise AssertionError("an unauthorized request touched a routine service")

        for target, methods in (
            (rig.service, ("list", "plans", "get", "plan", "revise", "enable", "pause", "archive")),
            (rig.runner.store, ("summaries", "runs")),
            (rig.runner, ("run_now",)),
        ):
            for name in methods:
                monkeypatch.setattr(target, name, forbidden)

        command = str(uuid4())
        routes = (
            ("GET", "/api/v2/routines", None),
            ("GET", path, None),
            (
                "PATCH",
                path,
                {
                    "command_id": command,
                    "expected_revision": 1,
                    "document": _document_raw(),
                },
            ),
            ("GET", history, None),
            *(
                (
                    "POST",
                    f"{path}/{action}",
                    {"command_id": command, "expected_state_revision": 1},
                )
                for action in ("enable", "pause", "archive")
            ),
            ("POST", f"{path}/run", {"command_id": command, "expected_revision": 1}),
        )
        refusals = (
            ({"Origin": HEADERS["Origin"]}, (401, "unauthorized")),
            ({**HEADERS, "Authorization": "Bearer wrong"}, (401, "unauthorized")),
            ({**HEADERS, "Origin": "https://untrusted.example"}, (403, "forbidden_origin")),
        )
        for method, target, body in routes:
            for headers, expected in refusals:
                assert _code(api.request(method, target, headers=headers, json=body)) == expected
                assert touched == []


def _code(response: Any) -> tuple[int, str]:
    return _no_store(response).status_code, response.json()["error"]["code"]


def test_state_routes_enable_pause_and_archive(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    routine_id = rig.service.create(str(uuid4()), _document_raw())[0]["routine_id"]
    with rig.state.store._connection(write=True) as connection:
        record_tested(connection, routine_id, 1, stamp(NOW))
    path = f"/api/v2/routines/{routine_id}"
    with rig.api as api:

        def change(action: str, state_revision: int, command_id: str | None = None) -> Any:
            return _no_store(
                api.post(
                    f"{path}/{action}",
                    headers=HEADERS,
                    json={
                        "command_id": command_id or str(uuid4()),
                        "expected_state_revision": state_revision,
                    },
                )
            )

        blocked = change("enable", 2)
        assert _code(blocked) == (422, "not_ready")
        assert blocked.json()["error"]["blockers"] == BLOCKERS
        monkeypatch.setattr(rig.service, "readiness", lambda document, *, scheduled: [])
        assert _code(change("enable", 1)) == (409, "conflict")
        command_id = str(uuid4())
        enabled = change("enable", 2, command_id)
        assert (enabled.status_code, enabled.json()) == (
            200,
            {"routine_id": routine_id, "revision": 1, "state": "enabled", "state_revision": 3},
        )
        assert change("enable", 2, command_id).json() == enabled.json()
        assert change("pause", 3).json()["state"] == "paused"
        assert change("archive", 4).json()["state"] == "archived"
        assert _code(change("enable", 5)) == (409, "invalid_transition")
        assert _code(change("restore", 5)) == (404, "not_found")
        assert _code(api.get(f"{path}/enable", headers=HEADERS)) == (404, "not_found")
    assert rig.wakes == 3


def test_a_manual_run_is_one_open_attempt_per_routine(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rig.service, "readiness", lambda document, *, scheduled: [])
    monkeypatch.setattr(rig.runner.profiles, "get", lambda profile_id: {"current_revision": 1})
    release = asyncio.Event()

    async def held(_profile_id: str, _raw: dict[str, Any]) -> dict[str, Any]:
        await release.wait()
        return {"launchable": False}

    rig.runner.prepare = held
    first, second, third, archived = (
        rig.service.create(str(uuid4()), _document_raw())[0]["routine_id"] for _ in range(4)
    )
    rig.service.archive(archived, str(uuid4()), 1)
    with rig.api as api:

        def run(routine_id: str, command_id: str | None = None, revision: int = 1) -> Any:
            return _no_store(
                api.post(
                    f"/api/v2/routines/{routine_id}/run",
                    headers=HEADERS,
                    json={"command_id": command_id or str(uuid4()), "expected_revision": revision},
                )
            )

        assert _code(run(first)) == (503, "unavailable")
        rig.scheduler.state = "running"
        command_id = str(uuid4())
        started = run(first, command_id)
        assert started.status_code == 202
        ids = started.json()
        assert set(ids) == {"occurrence_id", "attempt_id", "session_id"}
        replay = run(first, command_id)
        assert (replay.status_code, replay.json()) == (200, ids)
        assert _code(run(first)) == (409, "run_active")
        assert run(second).status_code == 202
        assert _code(run(third)) == (409, "busy")
        assert _code(run(third, revision=2)) == (409, "conflict")
        assert _code(run(archived)) == (409, "invalid_transition")
        runs = api.get(f"/api/v2/routines/{first}/runs", headers=HEADERS).json()["runs"]
        assert [(row["occurrence_id"], row["kind"], row["attempt"]["state"]) for row in runs] == [
            (ids["occurrence_id"], "manual", "claimed")
        ]
        assert api.portal is not None
        api.portal.call(release.set)
        api.portal.call(rig.runner.close)
        assert rig.runner.manager._actors == {}
        with rig.state.store._connection() as connection:
            assert connection.execute("SELECT count(*) FROM commands").fetchone() == (0,)
    assert rig.wakes == 2


def test_a_manual_run_starts_while_paused_all(
    launched: tuple[Rig, LaunchRig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, launch = launched
    monkeypatch.setattr(rig.service, "readiness", lambda document, *, scheduled: [])
    monkeypatch.setattr(rig.runner.profiles, "get", lambda profile_id: {"current_revision": 1})
    release = asyncio.Event()

    async def held(_profile_id: str, _raw: dict[str, Any]) -> dict[str, Any]:
        await release.wait()
        return {"launchable": False}

    rig.runner.prepare = held
    launch.settings.patch(str(uuid4()), 0, {"routines.paused": True})
    routine_id = rig.service.create(str(uuid4()), _document_raw())[0]["routine_id"]
    rig.scheduler.state = "running"
    with rig.api as api:
        listed = api.get("/api/v2/routines", headers=HEADERS).json()
        assert listed["scheduler"] == {"state": "running", "attended": True, "paused": True}
        started = api.post(
            f"/api/v2/routines/{routine_id}/run",
            headers=HEADERS,
            json={"command_id": str(uuid4()), "expected_revision": 1},
        )
        assert started.status_code == 202
        runs = api.get(f"/api/v2/routines/{routine_id}/runs", headers=HEADERS).json()["runs"]
        assert [(row["kind"], row["attempt"]["state"]) for row in runs] == [("manual", "claimed")]
        assert api.portal is not None
        api.portal.call(release.set)
        api.portal.call(rig.runner.close)


def test_runs_page_newest_first_and_refuse_other_queries(rig: Rig) -> None:
    routine_id = rig.service.create(str(uuid4()), _document_raw())[0]["routine_id"]
    with rig.state.store._connection(write=True) as connection:
        occurrences = [
            rig.runner.store.insert_claim(
                connection,
                routine_id=routine_id,
                kind="schedule",
                revision=1,
                now=stamp(NOW + timedelta(minutes=minute)),
                scheduled_for=stamp(NOW + timedelta(minutes=minute)),
            ).occurrence_id
            for minute in range(3)
        ]
    path = f"/api/v2/routines/{routine_id}/runs"
    with rig.api as api:

        def page(query: str) -> list[str]:
            response = _no_store(api.get(f"{path}{query}", headers=HEADERS))
            assert list(response.json()) == ["runs"]
            return [row["occurrence_id"] for row in response.json()["runs"]]

        assert page("") == occurrences[::-1]
        assert page("?limit=2") == occurrences[:0:-1]
        assert page(f"?limit=2&before={occurrences[1]}") == occurrences[:1]
        for query in ("limit=0", "limit=51", "limit=02", "limit=%2B2", "x=1", f"before={uuid4()}"):
            assert _code(api.get(f"{path}?{query}", headers=HEADERS)) == (400, "invalid_input")


def test_a_response_over_the_cap_is_refused(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routines_api, "MAX_RESPONSE", 10)
    with rig.api as api:
        assert _code(api.get("/api/v2/routines", headers=HEADERS)) == (413, "too_large")


def test_an_imported_routine_is_flagged_and_never_applied(rig: Rig) -> None:
    patch = FakePatch()
    imports = import_service(patch=patch)
    routine = {**_document_raw(), "state": "enabled"}
    preview = imports.preview("settings", envelope({"routines": [routine]}))
    assert {"key": "routines", "reason": "disabled"} in preview.executable_definitions
    with pytest.raises(ImportServiceError) as err:
        imports.apply(
            preview.preview_id, command_id=str(uuid4()), expected_revision=0, keys=["routines"]
        )
    assert err.value.code == "invalid_selection"
    assert patch.calls == [] and rig.service.list() == []


# --- the rehearsal: preview, resolved task, warnings, stop time ---


def _preview(api: TestClient, document: dict[str, Any]) -> Any:
    return _no_store(api.post(PREVIEW, headers=HEADERS, json={"document": document}))


def _row_counts(rig: Rig) -> dict[str, int]:
    """Every table's row count, so a write anywhere in the store shows."""
    with rig.state.store._connection() as connection:
        tables = [
            name
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        return {
            table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }


def _inactive_workspace(launch: LaunchRig, tmp_path: Path) -> str:
    """A second registered vault, not the launch's active one."""
    other = tmp_path / "other"
    other.mkdir()
    return launch.workspaces.register_existing(str(uuid4()), str(other))[0].workspace_id


def test_preview_answers_rehearsal_and_writes_nothing(launched: tuple[Rig, LaunchRig]) -> None:
    rig, launch = launched
    document = _document_raw(profile_id=launch.profile_id, workspace_id=launch.workspace_id)
    workspace = launch.workspaces.get(launch.workspace_id)
    before = _row_counts(rig)
    with rig.api as api:
        response = _preview(api, document)
        manual = _preview(api, {**document, "schedule": _manual_schedule()})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"next_five", "readiness", "resolved"}
    assert body["next_five"][0]["at"] == "2026-09-28T00:00:00.000Z"
    assert len(body["next_five"]) == 5
    assert workspace.policy != "ordinary"
    assert {key: body[key] for key in ("readiness", "resolved")} == {
        "readiness": {"blockers": ["unattended_policy"], "run_blockers": [], "warnings": []},
        "resolved": {
            "profile": {
                "profile_id": launch.profile_id,
                "name": "Review helper",
                "revision": 1,
                "provider": "claude",
                "model": None,
                "permission_mode": "default",
                "mode_source": "settings",
                "connection_pinned": False,
            },
            "workspace": {
                "workspace_id": launch.workspace_id,
                "name": workspace.name,
                "policy": workspace.policy,
                "active": True,
            },
            "first_turn": "Review the change and cite concrete evidence.\n\n"
            "Summarize overnight news.",
            "system_prompt": None,
            "deadline_minutes": 30,
        },
    }
    assert (manual.status_code, manual.json()["next_five"]) == (200, [])
    assert _row_counts(rig) == before
    assert rig.wakes == 0


def test_preview_refuses_auth_origin_and_closed_input(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"document": _document_raw()}
    valid = json.dumps(body)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a refused preview touched the routine service")

    with rig.api as api:
        with monkeypatch.context() as patch:
            for name in ("readiness", "resolve", "warnings", "create"):
                patch.setattr(rig.service, name, forbidden)
            for headers, expected in (
                ({"Origin": HEADERS["Origin"]}, (401, "unauthorized")),
                ({**HEADERS, "Authorization": "Bearer wrong"}, (401, "unauthorized")),
                ({"Authorization": "Bearer synthetic"}, (403, "forbidden_origin")),
                ({**HEADERS, "Origin": "https://untrusted.example"}, (403, "forbidden_origin")),
            ):
                assert _code(api.post(PREVIEW, headers=headers, json=body)) == expected
            for headers in (
                {**HEADERS, "Content-Type": "text/plain"},
                {**JSON, "Content-Encoding": "gzip"},
            ):
                response = api.post(PREVIEW, headers=headers, content=valid)
                assert _code(response) == (400, "invalid_input")
            too_large = api.post(PREVIEW, headers=JSON, content=b" " * (MAX_BODY + 1))
            assert _code(too_large) == (413, "too_large")
            query = api.post(f"{PREVIEW}?x=1", headers=HEADERS, json=body)
            assert _code(query) == (400, "invalid_input")
        for raw, code in (
            ({}, "invalid_input"),
            ({**body, "extra": 1}, "invalid_input"),
            ({"document": {**_document_raw(), "extra": 1}}, "invalid_input"),
            ({"document": _document_raw(name="")}, "invalid_input"),
            (
                {"document": _document_raw(schedule=_daily_schedule(times=["9:00"]))},
                "invalid_schedule",
            ),
        ):
            assert _code(api.post(PREVIEW, headers=HEADERS, json=raw)) == (400, code)
        assert _code(api.get(PREVIEW, headers=HEADERS)) == (400, "invalid_id")
    assert rig.wakes == 0


def test_preview_never_creates_a_routine(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the preview created a routine")

    monkeypatch.setattr(rig.service, "create", forbidden)
    document = _document_raw()
    with rig.api as api:
        shaped = api.post(
            PREVIEW, headers=HEADERS, json={"command_id": str(uuid4()), "document": document}
        )
        assert _code(shaped) == (400, "invalid_input")
        assert _preview(api, document).status_code == 200
        assert api.get("/api/v2/routines", headers=HEADERS).json()["routines"] == []
    assert rig.wakes == 0


def test_resolved_first_turn_is_instructions_blank_line_prompt(
    launched: tuple[Rig, LaunchRig],
) -> None:
    rig, launch = launched
    document = _document_raw(
        prompt="Line one.\nLine two.",
        profile_id=launch.profile_id,
        workspace_id=launch.workspace_id,
    )
    with rig.api as api:
        first = _preview(api, document).json()["resolved"]
        edits = profile_document()
        edits["instructions"] = ""
        ProfileService(launch.state.store).revise(launch.profile_id, str(uuid4()), 1, edits)
        bare = _preview(api, document).json()["resolved"]
    assert first["first_turn"] == (
        "Review the change and cite concrete evidence.\n\nLine one.\nLine two."
    )
    assert (bare["first_turn"], bare["profile"]["revision"]) == ("Line one.\nLine two.", 2)


def _resolved_turns(resolved: dict[str, Any]) -> tuple[str, str | None]:
    return resolved["first_turn"], resolved["system_prompt"]


def test_resolved_bot_profile_previews_instructions_and_memory_rule_as_system_prompt(
    launched: tuple[Rig, LaunchRig],
) -> None:
    # A run of a profile with a Bot in its workspace gets them as its system prompt (ADR-084),
    # so its first turn is the prompt alone.
    rig, launch = launched
    document = _document_raw(
        prompt="Line one.", profile_id=launch.profile_id, workspace_id=launch.workspace_id
    )
    with rig.api as api:
        before = _resolved_turns(_preview(api, document).json()["resolved"])
        with launch.state.store._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO bots VALUES (?,?,?,?,?,1,?,?)",
                (
                    str(uuid4()),
                    launch.profile_id,
                    launch.workspace_id,
                    str(uuid4()),
                    "a" * 64,
                    "2026-11-05T00:00:00.000Z",
                    "2026-11-05T00:00:00.000Z",
                ),
            )
        bound = _resolved_turns(_preview(api, document).json()["resolved"])
        edits = profile_document()
        edits["instructions"] = ""
        ProfileService(launch.state.store).revise(launch.profile_id, str(uuid4()), 1, edits)
        bare = _resolved_turns(_preview(api, document).json()["resolved"])
    instructions = profile_document()["instructions"]
    assert before == (f"{instructions}\n\nLine one.", None)
    assert bound == ("Line one.", bot_system_prompt(instructions))
    assert bare == ("Line one.", bot_system_prompt(""))


def test_resolved_bot_profile_previews_the_kanban_rule_once_the_vault_has_the_skill(
    launched: tuple[Rig, LaunchRig],
) -> None:
    rig, launch = launched
    document = _document_raw(
        prompt="Line one.", profile_id=launch.profile_id, workspace_id=launch.workspace_id
    )
    with launch.state.store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO bots VALUES (?,?,?,?,?,1,?,?)",
            (
                str(uuid4()),
                launch.profile_id,
                launch.workspace_id,
                str(uuid4()),
                "a" * 64,
                "2026-11-05T00:00:00.000Z",
                "2026-11-05T00:00:00.000Z",
            ),
        )
    with rig.api as api:
        before = _resolved_turns(_preview(api, document).json()["resolved"])
        skill = launch.vault / ".claude" / "skills" / "kanban" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("content", encoding="utf-8")
        after = _resolved_turns(_preview(api, document).json()["resolved"])
    instructions = profile_document()["instructions"]
    assert before == ("Line one.", bot_system_prompt(instructions))
    assert after == ("Line one.", bot_system_prompt(instructions, kanban=True))


def test_resolved_mode_and_model_fall_back_to_settings(
    launched: tuple[Rig, LaunchRig], monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, launch = launched
    document = parse_document(
        _document_raw(profile_id=launch.profile_id, workspace_id=launch.workspace_id)
    )[0]
    profiles = ProfileService(launch.state.store)
    edits = profile_document()

    def revise(revision: int, **runtime: Any) -> None:
        edits["runtime"].update(runtime)
        profiles.revise(launch.profile_id, str(uuid4()), revision, edits)

    def picked() -> tuple[Any, ...]:
        found = rig.service.resolve(document)["profile"]
        return found["model"], found["permission_mode"], found["mode_source"]

    changes = {"chat.default_mode": "plan", "chat.default_model": "settings-model"}
    launch.settings.patch(str(uuid4()), 0, changes)
    assert picked() == ("settings-model", "plan", "settings")
    revise(1, model="profile-model", permission_mode="default")
    assert picked() == ("profile-model", "default", "profile")
    revise(2, model=None)
    assert picked() == ("settings-model", "default", "profile")

    def unreadable() -> Any:
        raise SettingsError("corrupt")

    monkeypatch.setattr(launch.settings, "read", unreadable)
    assert picked() == (None, "default", "profile")
    revise(3, permission_mode=None)
    assert picked() == (None, None, None)

    def corrupt(_profile_id: str) -> Any:
        raise ProfileError("corrupt")

    monkeypatch.setattr(rig.service.profiles, "get", corrupt)
    with pytest.raises(RoutineError, match="^unavailable$"):
        rig.service.resolve(document)


def test_workspace_not_active_is_a_warning(
    launched: tuple[Rig, LaunchRig], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, launch = launched
    inactive = _inactive_workspace(launch, tmp_path)
    with rig.api as api:

        def rehearsed(workspace_id: str) -> tuple[Any, ...]:
            raw = _document_raw(profile_id=launch.profile_id, workspace_id=workspace_id)
            body = _preview(api, raw).json()
            workspace = body["resolved"]["workspace"]
            return (
                body["readiness"]["warnings"],
                body["readiness"]["run_blockers"],
                workspace and workspace["active"],
            )

        assert rehearsed(inactive) == (["workspace_not_active"], [], False)
        assert rehearsed(launch.workspace_id) == ([], [], True)
        assert rehearsed(str(uuid4())) == ([], ["workspace_missing"], None)

        def unresolved() -> Any:
            raise StateError("unavailable")

        monkeypatch.setattr(launch.workspaces, "resolve_active", unresolved)
        assert rehearsed(launch.workspace_id) == (["workspace_not_active"], [], False)


def test_detail_reports_run_blockers_and_warnings(
    launched: tuple[Rig, LaunchRig], tmp_path: Path
) -> None:
    rig, launch = launched
    inactive = _inactive_workspace(launch, tmp_path)

    def created(profile_id: str, workspace_id: str) -> str:
        raw = _document_raw(profile_id=profile_id, workspace_id=workspace_id)
        return rig.service.create(str(uuid4()), raw)[0]["routine_id"]

    ready = created(launch.profile_id, launch.workspace_id)
    elsewhere = created(launch.profile_id, inactive)
    orphan = created(str(uuid4()), launch.workspace_id)
    with rig.api as api:

        def read(routine_id: str) -> dict[str, Any]:
            return _no_store(api.get(f"/api/v2/routines/{routine_id}", headers=HEADERS)).json()

        detail = read(ready)
        assert detail["readiness"] == {
            "blockers": ["unattended_policy"],
            "run_blockers": [],
            "warnings": [],
        }
        assert detail["resolved"] == _preview(api, detail["document"]).json()["resolved"]
        assert read(elsewhere)["readiness"] == {
            "blockers": ["unattended_policy"],
            "run_blockers": [],
            "warnings": ["workspace_not_active"],
        }
        lost = read(orphan)
        assert lost["readiness"] == {
            "blockers": ["profile_missing", "unattended_policy"],
            "run_blockers": ["profile_missing"],
            "warnings": [],
        }
        assert (lost["resolved"]["profile"], lost["resolved"]["first_turn"]) == (None, None)


def test_active_run_stop_by_is_claim_time_plus_its_revision_deadline(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = rig.service.create(str(uuid4()), _document_raw(deadline_minutes=45))[0]
    routine_id = created["routine_id"]
    rig.service.revise(routine_id, str(uuid4()), 1, _document_raw(deadline_minutes=90))
    claimed_at = NOW + timedelta(minutes=7, milliseconds=250)
    with rig.state.store._connection(write=True) as connection:
        attempt = rig.runner.store.insert_claim(
            connection,
            routine_id=routine_id,
            kind="schedule",
            revision=1,
            now=stamp(claimed_at),
            scheduled_for=stamp(NOW),
        )
    expected = {
        "attempt_id": attempt.attempt_id,
        "session_id": attempt.session_id,
        "state": "claimed",
        "stop_by": stamp(claimed_at + timedelta(minutes=45)),
    }
    with rig.api as api:

        def active() -> tuple[Any, Any]:
            listed = api.get("/api/v2/routines", headers=HEADERS).json()["routines"]
            read = api.get(f"/api/v2/routines/{routine_id}", headers=HEADERS).json()
            return listed[0]["active_run"], read["active_run"]

        assert active() == (expected, expected)

        def unreadable(*_args: Any) -> Any:
            raise RoutineError("corrupt")

        monkeypatch.setattr(rig.service, "frozen", unreadable)
        assert active() == ({**expected, "stop_by": None}, {**expected, "stop_by": None})


def test_last_attempt_is_the_newest_run_with_an_attempt_behind_a_newer_skip(rig: Rig) -> None:
    routine_id = rig.service.create(str(uuid4()), _document_raw())[0]["routine_id"]
    later = stamp(NOW + timedelta(minutes=30))
    with rig.state.store._connection(write=True) as connection:
        attempt = rig.runner.store.insert_claim(
            connection,
            routine_id=routine_id,
            kind="schedule",
            revision=1,
            now=stamp(NOW),
            scheduled_for=stamp(NOW),
        )
        rig.runner.store.insert_skip(
            connection,
            routine_id=routine_id,
            scheduled_for=later,
            revision=1,
            reason="overlap",
            now=later,
        )
    skipped, tried = rig.runner.store.runs(routine_id, 2, None)
    assert (skipped["reason"], tried["attempt"]["attempt_id"]) == ("overlap", attempt.attempt_id)
    with rig.api as api:
        listed = api.get("/api/v2/routines", headers=HEADERS).json()["routines"][0]
        read = api.get(f"/api/v2/routines/{routine_id}", headers=HEADERS).json()
    for item in (listed, read):
        assert (item["last_run"], item["last_attempt"]) == (skipped, tried)


# --- the routine services wired into the full app ---


class FakeWindows:
    """The app windows the scheduler asks about: only how many are open."""

    count = 0

    def __init__(self) -> None:
        self.admission_lock = asyncio.Lock()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _WindowGate(asyncio.Lock):
    """Record the holder so process calls can run while another task holds admission."""

    owner: asyncio.Task[Any] | None = None

    async def acquire(self) -> Literal[True]:
        await super().acquire()
        self.owner = asyncio.current_task()
        return True

    def release(self) -> None:
        self.owner = None
        super().release()


def _gated_windows() -> tuple[WindowManager, FakeWindow, _WindowGate, list[str]]:
    gate = _WindowGate()
    calls: list[str] = []

    def outside(name: str) -> None:
        assert asyncio.current_task() is not gate.owner
        calls.append(name)

    class Process(FakeWindow):
        async def wait_tree(self) -> None:
            outside("wait")
            await super().wait_tree()

        def kill_tree(self) -> None:
            outside("kill")
            super().kill_tree()

        def release(self) -> None:
            outside("release")
            super().release()

    window = Process()

    async def spawn(*_args: Any, **_kwargs: Any) -> FakeWindow:
        outside("spawn")
        return window

    async def closed() -> None:
        outside("closed")

    manager = WindowManager(
        spawn=spawn,
        argv=lambda: ["synthetic-browser"],
        cwd=Path("."),
        env={},
        on_last_closed=closed,
    )
    assert isinstance(manager.admission_lock, asyncio.Lock)
    manager.admission_lock = gate
    return manager, window, gate, calls


async def _window_turn() -> None:
    cycled = asyncio.Event()
    asyncio.get_running_loop().call_soon(cycled.set)
    await cycled.wait()


@pytest.mark.parametrize("transition", ["add", "discard", "clear"])
async def test_window_set_transitions_wait_for_admission(transition: str) -> None:
    manager, window, gate, calls = _gated_windows()
    pending: asyncio.Task[Any] | None = None
    try:
        if transition != "add":
            assert await manager.open()
            await _window_turn()
        async with gate:
            if transition == "add":
                pending = asyncio.create_task(manager.open())
            elif transition == "discard":
                pending = next(iter(manager._tasks))
                window.exited.set()
            else:
                pending = asyncio.create_task(manager.stop())
            await _window_turn()
            assert not pending.done()
            assert manager.count == (0 if transition == "add" else 1)
            assert calls.count("spawn") == 1  # spawning never waits for admission
        await asyncio.wait_for(pending, HANG_GUARD_S)
        await _window_turn()
        assert manager.count == (1 if transition == "add" else 0)
        assert (
            calls
            == {
                "add": ["spawn", "wait"],
                "discard": ["spawn", "wait", "release", "closed"],
                "clear": ["spawn", "wait", "kill", "release"],
            }[transition]
        )
    finally:
        await manager.stop()
        await asyncio.gather(*manager._tasks, return_exceptions=True)
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)


async def test_window_add_rechecks_stop_before_registering_its_tracker() -> None:
    manager, window, gate, calls = _gated_windows()
    pending: list[asyncio.Task[Any]] = []
    try:
        async with gate:
            pending.append(asyncio.create_task(manager.open()))
            await _window_turn()
            assert calls == ["spawn"] and manager.count == 0
            pending.append(asyncio.create_task(manager.stop()))
            await _window_turn()
            assert manager._stop.is_set() and not any(task.done() for task in pending)
        assert await asyncio.wait_for(asyncio.gather(*pending), HANG_GUARD_S) == [False, None]
        assert manager.count == 0 and manager._tasks == set()
        assert window.killed and window.released
        assert calls == ["spawn", "kill", "release"]
    finally:
        await manager.stop()
        await asyncio.gather(*pending, *manager._tasks, return_exceptions=True)


async def test_cancelled_window_add_releases_the_unregistered_process() -> None:
    manager, window, gate, calls = _gated_windows()
    opening: asyncio.Task[bool] | None = None
    try:
        async with gate:
            opening = asyncio.create_task(manager.open())
            await _window_turn()
            assert calls == ["spawn"] and manager.count == 0
            opening.cancel()
            [result] = await asyncio.wait_for(
                asyncio.gather(opening, return_exceptions=True), HANG_GUARD_S
            )
            assert isinstance(result, asyncio.CancelledError)
            assert window.killed and window.released
            assert calls == ["spawn", "kill", "release"]
        assert manager.count == 0 and manager._tasks == set()
    finally:
        await manager.stop()
        await asyncio.gather(*manager._tasks, return_exceptions=True)
        if opening is not None:
            await asyncio.gather(opening, return_exceptions=True)


async def test_cancelled_window_stop_finishes_cleanup_before_cancellation_returns() -> None:
    manager, window, gate, calls = _gated_windows()
    closing: asyncio.Task[None] | None = None
    try:
        assert await manager.open()
        await _window_turn()
        async with gate:
            closing = asyncio.create_task(manager.stop())
            await _window_turn()
            assert manager._stop.is_set() and manager.count == 1
            for _ in range(2):
                closing.cancel()
                await _window_turn()
                assert not closing.done()
                assert manager.count == 1 and not window.killed and not window.released
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, HANG_GUARD_S)
        assert manager.count == 0 and window.killed and window.released
        assert calls == ["spawn", "wait", "kill", "release"]
        await manager.stop()
        assert calls == ["spawn", "wait", "kill", "release"]
    finally:
        await manager.stop()
        await asyncio.gather(*manager._tasks, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)


async def test_cancelled_window_stop_joins_the_watcher_once_for_all_callers() -> None:
    manager, window, gate, calls = _gated_windows()
    loop = asyncio.get_running_loop()
    joining = asyncio.Event()
    allow_join = threading.Event()
    joined = threading.Event()
    joins: list[None] = []
    pending: list[asyncio.Task[None]] = []

    class Watcher(threading.Thread):
        def join(self, timeout: float | None = None) -> None:
            assert not gate.locked()
            joins.append(None)
            loop.call_soon_threadsafe(joining.set)
            assert allow_join.wait(HANG_GUARD_S)
            joined.set()

    try:
        assert await manager.open()
        await _window_turn()
        manager._thread = Watcher()
        closing = asyncio.create_task(manager.stop())
        pending.append(closing)
        await asyncio.wait_for(joining.wait(), HANG_GUARD_S)
        pending.append(asyncio.create_task(manager.stop()))
        for _ in range(2):
            closing.cancel()
            await _window_turn()
            assert not any(task.done() for task in pending)
            assert not joined.is_set() and manager.count == 1
            assert not window.killed and not window.released
        allow_join.set()
        cancelled, other = await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), HANG_GUARD_S
        )
        assert isinstance(cancelled, asyncio.CancelledError) and other is None
        assert joined.is_set() and manager._thread is None and joins == [None]
        assert manager.count == 0 and window.killed and window.released
        assert calls == ["spawn", "wait", "kill", "release"]
    finally:
        allow_join.set()
        await manager.stop()
        await asyncio.gather(*pending, *manager._tasks, return_exceptions=True)


async def test_window_exit_queued_before_stop_does_not_report_last_closed() -> None:
    manager, window, gate, calls = _gated_windows()
    closing: asyncio.Task[None] | None = None
    try:
        assert await manager.open()
        await _window_turn()
        tracking = next(iter(manager._tasks))
        async with gate:
            window.exited.set()
            await _window_turn()  # the exit takes the first place behind the holder
            closing = asyncio.create_task(manager.stop())
            await _window_turn()
            assert manager._stop.is_set() and manager.count == 1
        await asyncio.wait_for(asyncio.gather(tracking, closing), HANG_GUARD_S)
        assert manager.count == 0 and window.released
        assert calls == ["spawn", "wait", "release"]
    finally:
        await manager.stop()
        await asyncio.gather(*manager._tasks, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)


def _full_app(tmp_path: Path, clock: FakeClock, **extra: Any) -> tuple[LaunchRig, Starlette]:
    launch = LaunchRig(tmp_path)
    app = create_app(
        config=launch.config,
        token=TOKEN,
        manager=launch.manager(),
        catalog=launch.catalog,
        static_dir=None,
        settings=launch.settings,
        workspaces=launch.workspaces,
        readiness=launch.readiness,
        vault_poll_s=60,
        routine_clock=clock,
        **extra,
    )
    return launch, app


@contextlib.asynccontextmanager
async def _serving(app: Starlette) -> AsyncIterator[httpx.AsyncClient]:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client,
    ):
        yield client


async def _waits(clock: FakeClock, count: int) -> None:
    """Until the scheduler loop has reached its `count`th wait, the end of a loop turn."""
    async with asyncio.timeout(HANG_GUARD_S):
        while len(clock.waits) < count:
            await asyncio.sleep(0)


def _created(app: Starlette, launch: LaunchRig) -> str:
    raw = _document_raw(profile_id=launch.profile_id, workspace_id=launch.workspace_id)
    return app.state.routines.create(str(uuid4()), raw)[0]["routine_id"]


def _claimed(launch: LaunchRig) -> list[Any]:
    with launch.state.store._connection() as connection:
        return connection.execute(
            "SELECT occurrence_id FROM routine_occurrences UNION ALL "
            "SELECT attempt_id FROM task_attempts"
        ).fetchall()


async def test_profile_checkpoint_joins_its_real_write_before_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch, app = _full_app(tmp_path, FakeClock(NOW))
    manager = app.state.commands.manager
    callback = manager._profile_initialized
    assert callback is not None
    store = launch.state.store
    original_connection = store._connection
    loop = asyncio.get_running_loop()

    def hold_checkpoint() -> tuple[asyncio.Event, threading.Event, threading.Event]:
        entered = asyncio.Event()
        release = threading.Event()
        committed = threading.Event()

        @contextlib.contextmanager
        def held_connection(*args: Any, **kwargs: Any) -> Iterator[Any]:
            targeted = False
            try:
                with original_connection(*args, **kwargs) as connection:
                    if kwargs.get("write"):

                        def record(statement: str) -> None:
                            nonlocal targeted
                            if statement.startswith(
                                "UPDATE session_profile_bindings SET native_started=1"
                            ):
                                targeted = True

                        connection.set_trace_callback(record)
                    yield connection
                    if targeted:
                        loop.call_soon_threadsafe(entered.set)
                        assert release.wait(HANG_GUARD_S)
            finally:
                if targeted:
                    committed.set()

        monkeypatch.setattr(store, "_connection", held_connection)
        return entered, release, committed

    async with _serving(app) as client:

        async def preview(session_id: str) -> dict[str, Any]:
            response = await client.post(
                f"/api/v2/profiles/{launch.profile_id}/start-preview",
                headers=WRITE,
                json={
                    "command_id": str(uuid4()),
                    "session_id": session_id,
                    "expected_profile_revision": 1,
                    "workspace_id": launch.workspace_id,
                },
            )
            assert response.status_code == 200
            return response.json()

        session_id = str(uuid4())
        await preview(session_id)
        entered, release, committed = hold_checkpoint()
        direct = asyncio.create_task(callback(session_id))
        try:
            async with asyncio.timeout(HANG_GUARD_S):
                await entered.wait()
            direct.cancel()
            pending = loop.create_future()
            loop.call_soon(lambda: pending.set_result(not direct.done()))
            assert await pending  # cancellation must wait for the held transaction
        finally:
            release.set()
            await asyncio.gather(direct, return_exceptions=True)
            cancelled_after_commit = committed.is_set()
            assert await asyncio.to_thread(committed.wait, HANG_GUARD_S)
        assert direct.cancelled()
        assert cancelled_after_commit
        with store._connection() as connection:
            assert connection.execute(
                "SELECT native_started FROM session_profile_bindings WHERE session_id=?",
                (session_id,),
            ).fetchone() == (1,)

        second_id = str(uuid4())
        prepared = await preview(second_id)
        entered, release, committed = hold_checkpoint()
        finished: list[tuple[str, bool]] = []
        command = CommandInput(str(uuid4()), second_id, prepared["generation"], 1)
        closing: asyncio.Task[None] | None = None
        try:
            _record, created = await app.state.commands.submit(
                command, finished=lambda state: finished.append((state, committed.is_set()))
            )
            assert created
            async with asyncio.timeout(HANG_GUARD_S):
                await entered.wait()
            closing = asyncio.create_task(app.state.commands.close())
            pending = loop.create_future()
            loop.call_soon(lambda: pending.set_result((closing.done(), finished.copy())))
            assert await pending == (False, [])
        finally:
            release.set()
            if closing is not None:
                await closing
            assert await asyncio.to_thread(committed.wait, HANG_GUARD_S)
        assert finished == [("not_dispatched", True)]
        with store._connection() as connection:
            assert connection.execute(
                "SELECT native_started FROM session_profile_bindings WHERE session_id=?",
                (second_id,),
            ).fetchone() == (1,)


async def test_a_foreign_host_reads_nothing_and_logs_hold_no_routine_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    clock = FakeClock(NOW)
    launch, app = _full_app(tmp_path, clock)
    store = launch.state.store
    path = f"/api/v2/routines/{uuid4()}"
    async with _serving(app) as client:
        await _waits(clock, 1)  # the startup pass has read the store
        opened: list[bool] = []
        connection = store._connection
        monkeypatch.setattr(
            store,
            "_connection",
            lambda *args, **kwargs: (opened.append(True), connection(*args, **kwargs))[1],
        )
        for method, target in (
            ("GET", "/api/v2/routines"),
            ("POST", "/api/v2/routines"),
            ("GET", path),
            ("PATCH", path),
            ("POST", f"{path}/run"),
            ("GET", f"{path}/runs"),
        ):
            response = await client.request(
                method, target, headers={**WRITE, "host": "evil.test"}, json={}
            )
            assert response.status_code == 400
        assert opened == []
        listed = await client.get("/api/v2/routines", headers=AUTH)
        assert opened  # the spy sees the reads an admitted request makes
        assert listed.json()["scheduler"] == {"state": "running", "attended": True, "paused": False}

        markers = ("name-5d1c9e", "purpose-5d1c9e", "prompt-5d1c9e", "5d1c9e-note.md")
        document = _document_raw(
            name=markers[0],
            purpose=markers[1],
            prompt=f"{markers[2]} /private/{markers[3]}",
            profile_id=launch.profile_id,
            workspace_id=launch.workspace_id,
        )
        created = await client.post(
            "/api/v2/routines",
            headers=WRITE,
            json={"command_id": str(uuid4()), "document": document},
        )
        assert created.status_code == 201
        routine_id = created.json()["routine_id"]
        assert (await client.get(f"/api/v2/routines/{routine_id}", headers=AUTH)).status_code == 200
        stale = await client.patch(
            f"/api/v2/routines/{routine_id}",
            headers=WRITE,
            json={"command_id": str(uuid4()), "expected_revision": 9, "document": document},
        )
        assert stale.status_code == 409
    logged = caplog.text
    assert TOKEN not in logged and "/private/" not in logged
    assert not [marker for marker in markers if marker in logged]


async def test_without_a_window_a_due_slot_waits_until_one_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(NOW)
    windows = FakeWindows()
    launch, app = _full_app(tmp_path, clock, windows=cast(WindowManager, windows))
    started: list[Attempt] = []
    monkeypatch.setattr(app.state.routine_tasks, "start", started.append)
    routine_id = _created(app, launch)
    with launch.state.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE routines SET state='enabled',enabled_since=? WHERE routine_id=?",
            (stamp(NOW), routine_id),
        )
    clock.advance((datetime(2026, 9, 28, 0, 1, tzinfo=UTC) - NOW).total_seconds())  # 09:01 JST
    async with _serving(app) as client:
        listed = await client.get("/api/v2/routines", headers=AUTH)
        assert listed.json()["scheduler"] == {
            "state": "running",
            "attended": False,
            "paused": False,
        }
        await _waits(clock, 1)
        app.state.scheduler.wake()
        await _waits(clock, 2)
        assert started == [] and _claimed(launch) == []
        windows.count = 1
        app.state.scheduler.wake()
        await _waits(clock, 3)
    assert [attempt.routine_id for attempt in started] == [routine_id]


async def test_the_first_window_wakes_the_scheduler_for_a_slot_already_due(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class OpeningWindows(FakeWindows):
        async def start(self) -> None:
            self.count = 1

    clock = FakeClock(NOW)
    launch, app = _full_app(tmp_path, clock, windows=cast(WindowManager, OpeningWindows()))
    started: list[Attempt] = []
    monkeypatch.setattr(app.state.routine_tasks, "start", started.append)
    routine_id = _created(app, launch)
    with launch.state.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE routines SET state='enabled',enabled_since=? WHERE routine_id=?",
            (stamp(NOW), routine_id),
        )
    slot = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)  # 09:00 JST
    clock.advance((slot - NOW).total_seconds() + 3)
    async with _serving(app):
        await _waits(clock, 2)  # the turn before the window opened, then the pass it woke
    assert [attempt.routine_id for attempt in started] == [routine_id]
    with launch.state.store._connection() as connection:
        [(scheduled_for, recorded_at)] = connection.execute(
            "SELECT scheduled_for,recorded_at FROM routine_occurrences"
        ).fetchall()
    assert scheduled_for == stamp(slot)
    assert parse_stamp(recorded_at) - slot <= timedelta(seconds=5)


def test_full_app_shares_one_admission_lock_with_or_without_a_window_manager(
    tmp_path: Path,
) -> None:
    private, with_windows = tmp_path / "private", tmp_path / "windows"
    private.mkdir()
    with_windows.mkdir()
    _launch, app = _full_app(private, FakeClock(NOW))
    assert app.state.routine_tasks._admission_lock is app.state.scheduler._admission_lock

    windows, _window, gate, _calls = _gated_windows()
    _launch, app = _full_app(with_windows, FakeClock(NOW), windows=windows)
    assert app.state.routine_tasks._admission_lock is gate
    assert app.state.scheduler._admission_lock is gate


async def test_last_window_removal_before_manual_admission_keeps_replay_in_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows, window, gate, _calls = _gated_windows()
    launch, app = _full_app(tmp_path, FakeClock(NOW), windows=windows)
    runner: TaskRunner = app.state.routine_tasks
    started: list[Attempt] = []
    monkeypatch.setattr(runner, "start", started.append)
    routine_id = _created(app, launch)
    path = f"/api/v2/routines/{routine_id}/run"
    prior = {"command_id": str(uuid4()), "expected_revision": 1}
    fresh = {"command_id": str(uuid4()), "expected_revision": 1}
    lookup = runner.store.lookup_manual
    entered, release = threading.Event(), threading.Event()

    def held_lookup(routine: str, command: str, revision: int) -> Attempt | None:
        if command == fresh["command_id"]:
            entered.set()  # first lookup is outside admission
            assert release.wait(HANG_GUARD_S)
        return lookup(routine, command, revision)

    monkeypatch.setattr(runner.store, "lookup_manual", held_lookup)
    pending: asyncio.Task[httpx.Response] | None = None
    async with _serving(app) as client:
        try:
            original = await client.post(path, headers=WRITE, json=prior)
            assert original.status_code == 202 and len(started) == 1
            pending = asyncio.create_task(client.post(path, headers=WRITE, json=fresh))
            assert await asyncio.wait_for(
                asyncio.to_thread(entered.wait, HANG_GUARD_S), HANG_GUARD_S
            )
            assert not gate.locked()
            window.exited.set()
            await asyncio.wait_for(next(iter(windows._tasks)), HANG_GUARD_S)
            assert windows.count == 0  # last-window removal linearized before claim admission
            release.set()
            denied = await asyncio.wait_for(pending, HANG_GUARD_S)
            assert _code(denied) == (503, "unavailable")
            replay = await client.post(path, headers=WRITE, json=prior)
            assert (replay.status_code, replay.json()) == (200, original.json())
            assert len(started) == 1 and len(_claimed(launch)) == 2
        finally:
            release.set()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)


async def test_last_window_removal_before_scheduled_admission_leaves_no_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(NOW)
    windows, window, gate, _calls = _gated_windows()
    launch, app = _full_app(tmp_path, clock, windows=windows)
    started: list[Attempt] = []
    monkeypatch.setattr(app.state.routine_tasks, "start", started.append)
    routine_id = _created(app, launch)
    with launch.state.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE routines SET state='enabled',enabled_since=? WHERE routine_id=?",
            (stamp(NOW), routine_id),
        )
    read = app.state.routines.plans
    entered, release = threading.Event(), threading.Event()

    def held_plan() -> Any:
        entered.set()  # planning runs outside the admission lock
        assert release.wait(HANG_GUARD_S)
        return read()

    async with _serving(app):
        await _waits(clock, 2)
        monkeypatch.setattr(app.state.routines, "plans", held_plan)
        due = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
        clock.advance((due - clock.wall()).total_seconds() + 1)
        app.state.scheduler.wake()
        try:
            assert await asyncio.wait_for(
                asyncio.to_thread(entered.wait, HANG_GUARD_S), HANG_GUARD_S
            )
            assert not gate.locked()
            window.exited.set()
            await asyncio.wait_for(next(iter(windows._tasks)), HANG_GUARD_S)
            assert windows.count == 0
            release.set()
            await _waits(clock, 3)
            assert started == [] and _claimed(launch) == []
        finally:
            release.set()


@pytest.mark.parametrize("kind", ["manual", "scheduled"])
async def test_last_window_removal_waits_for_committed_claim_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: Literal["manual", "scheduled"]
) -> None:
    clock = FakeClock(NOW)
    windows, window, gate, _calls = _gated_windows()
    launch, app = _full_app(tmp_path, clock, windows=windows)
    runner: TaskRunner = app.state.routine_tasks
    scheduler: Scheduler = app.state.scheduler
    started: list[tuple[Attempt, int]] = []

    def recorded(attempt: Attempt) -> None:
        started.append((attempt, windows.count))

    monkeypatch.setattr(runner, "start", recorded)
    routine_id = _created(app, launch)
    if kind == "scheduled":
        with launch.state.store._connection(write=True) as connection:
            connection.execute(
                "UPDATE routines SET state='enabled',enabled_since=? WHERE routine_id=?",
                (stamp(NOW), routine_id),
            )
    committed, release = threading.Event(), threading.Event()
    pending: asyncio.Task[httpx.Response] | None = None
    async with _serving(app) as client:
        if kind == "scheduled":
            await _waits(clock, 2)
            decide = scheduler._decide

            def held_decision(*args: Any) -> Any:
                result = decide(*args)  # the claim has committed before window removal starts
                committed.set()
                assert release.wait(HANG_GUARD_S)
                return result

            monkeypatch.setattr(scheduler, "_decide", held_decision)
            due = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
            clock.advance((due - clock.wall()).total_seconds() + 1)
            scheduler.wake()
        else:
            claim = runner.store.claim_manual

            def held_claim(*args: Any) -> tuple[Attempt, bool]:
                result = claim(*args)  # the claim has committed before window removal starts
                committed.set()
                assert release.wait(HANG_GUARD_S)
                return result

            monkeypatch.setattr(runner.store, "claim_manual", held_claim)
            pending = asyncio.create_task(
                client.post(
                    f"/api/v2/routines/{routine_id}/run",
                    headers=WRITE,
                    json={"command_id": str(uuid4()), "expected_revision": 1},
                )
            )
        try:
            assert await asyncio.wait_for(
                asyncio.to_thread(committed.wait, HANG_GUARD_S), HANG_GUARD_S
            )
            assert gate.locked() and windows.count == 1 and len(_claimed(launch)) == 2
            window.exited.set()
            tracking = next(iter(windows._tasks))
            await _window_turn()
            assert not tracking.done() and windows.count == 1
            release.set()
            if pending is not None:
                response = await asyncio.wait_for(pending, HANG_GUARD_S)
                assert response.status_code == 202
            else:
                await _waits(clock, 3)
            await asyncio.wait_for(tracking, HANG_GUARD_S)
            assert windows.count == 0 and len(_claimed(launch)) == 2
            assert len(started) == 1 and started[0][0].routine_id == routine_id
            assert started[0][1] == 1  # runner registration preceded zero-window removal
        finally:
            release.set()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)


async def test_a_failed_recovery_leaves_the_scheduler_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(_store: TaskStore, _now: str) -> int:
        raise RuntimeError("synthetic reconcile failure")

    monkeypatch.setattr(TaskStore, "reconcile", broken)
    caplog.set_level(logging.WARNING)
    launch, app = _full_app(tmp_path, FakeClock(NOW))
    routine_id = _created(app, launch)
    async with _serving(app) as client:
        listed = await client.get("/api/v2/routines", headers=AUTH)
        assert listed.json()["scheduler"] == {
            "state": "unavailable",
            "attended": True,
            "paused": False,
        }
        run = await client.post(
            f"/api/v2/routines/{routine_id}/run",
            headers=WRITE,
            json={"command_id": str(uuid4()), "expected_revision": 1},
        )
        assert (run.status_code, run.json()["error"]["code"]) == (503, "unavailable")
    assert _claimed(launch) == []
    assert "routine recovery unavailable" in [record.getMessage() for record in caplog.records]


async def test_manual_http_replay_survives_pruning_restart_and_failed_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    send = FakeDriver.send

    async def succeed(driver: FakeDriver, text: str) -> None:
        await send(driver, text)
        sent.append(driver.launch.session_id)
        driver.emit(TurnDone(status="success", usage=Usage()))

    monkeypatch.setattr(FakeDriver, "send", succeed)
    launch, app = _full_app(tmp_path, FakeClock(NOW))
    routine_id = _created(app, launch)
    path = f"/api/v2/routines/{routine_id}/run"
    request = {"command_id": str(uuid4()), "expected_revision": 1}
    runner: TaskRunner = app.state.routine_tasks
    async with _serving(app) as client:
        first = _no_store(await client.post(path, headers=WRITE, json=request))
        assert first.status_code == 202
        ids = first.json()
        assert set(ids) == {"occurrence_id", "attempt_id", "session_id"}
        await asyncio.wait_for(asyncio.gather(*runner._runs), HANG_GUARD_S)
        assert runner.store.runs(routine_id, 1, None)[0]["attempt"]["outcome"] == "succeeded"
    with launch.state.store._connection(write=True) as connection:
        for minute in range(1, MAX_OCCURRENCES + 1):
            at = stamp(NOW + timedelta(minutes=minute))
            runner.store.insert_skip(
                connection,
                routine_id=routine_id,
                scheduled_for=at,
                revision=1,
                reason="missed",
                now=at,
            )
        runner.store.prune(connection, routine_id)
        assert (
            connection.execute(
                "SELECT 1 FROM routine_occurrences WHERE occurrence_id=?", (ids["occurrence_id"],)
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT count(*) FROM task_attempts").fetchone() == (0,)

    def broken(_store: TaskStore, _now: str) -> int:
        raise RuntimeError("synthetic reconcile failure")

    monkeypatch.setattr(TaskStore, "reconcile", broken)
    launch.state = AppState(launch.data / "state.json", Prefs())
    restarted = launch.app(launch.manager())
    assert restarted.state.routine_tasks.store.store is not runner.store.store
    async with _serving(restarted) as client:
        listed = await client.get("/api/v2/routines", headers=AUTH)
        assert listed.json()["scheduler"]["state"] == "unavailable"
        replay = _no_store(await client.post(path, headers=WRITE, json=request))
        assert (replay.status_code, replay.json()) == (200, ids)
        fresh = _no_store(
            await client.post(
                path, headers=WRITE, json={"command_id": str(uuid4()), "expected_revision": 1}
            )
        )
        assert _code(fresh) == (503, "unavailable")
        conflict = _no_store(
            await client.post(path, headers=WRITE, json={**request, "expected_revision": 2})
        )
        assert _code(conflict) == (409, "command_conflict")
        with launch.state.store._connection() as connection:
            assert connection.execute("SELECT count(*) FROM routine_occurrences").fetchone() == (
                MAX_OCCURRENCES,
            )
            assert connection.execute("SELECT count(*) FROM task_attempts").fetchone() == (0,)
            assert connection.execute(
                "SELECT count(*) FROM routine_commands WHERE operation='run'"
            ).fetchone() == (1,)
        assert restarted.state.routine_tasks._runs == {}
    assert sent == [ids["session_id"]] and len(launch.drivers) == 1


async def test_shutdown_stops_the_scheduler_first_and_the_runner_after_the_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    for owner, name in (
        (Scheduler, "close"),
        (SessionManager, "close_all"),
        (TaskRunner, "close"),
    ):
        original = getattr(owner, name)

        async def recorded(
            self: object, *args: Any, _original: Any = original, _label: str = name, **kwargs: Any
        ) -> Any:
            calls.append(f"{type(self).__name__}.{_label}")
            return await _original(self, *args, **kwargs)

        monkeypatch.setattr(owner, name, recorded)
    _launch, app = _full_app(tmp_path, FakeClock(NOW))
    async with _serving(app):
        pass
    assert calls == [
        "Scheduler.close",
        "SessionManager.close_all",
        "TaskRunner.close",
    ]


async def test_the_minimal_app_serves_routines_with_the_scheduler_unavailable(
    tmp_path: Path,
) -> None:
    data, vault, home = tmp_path / "data", tmp_path / "vault", tmp_path / "home"
    vault.mkdir()
    app = create_app(
        config=Config(vault=vault, claude_home=home, cli=Path("absent"), edge=None, data_dir=data),
        token=TOKEN,
        manager=SessionManager(
            config=DriverConfig(cli=("must-not-run",), vault=vault, env={}),
            state=AppState(data / "state.json", Prefs()),
            claude_home=home,
        ),
        catalog=Catalog(home, vault),
        static_dir=None,
    )
    async with _serving(app) as client:
        created = await client.post(
            "/api/v2/routines",
            headers=WRITE,
            json={"command_id": str(uuid4()), "document": _document_raw()},
        )
        assert created.status_code == 201
        listed = await client.get("/api/v2/routines", headers=AUTH)
        assert listed.json()["scheduler"] == {
            "state": "unavailable",
            "attended": True,
            "paused": False,
        }
        run = await client.post(
            f"/api/v2/routines/{created.json()['routine_id']}/run",
            headers=WRITE,
            json={"command_id": str(uuid4()), "expected_revision": 1},
        )
        assert (run.status_code, run.json()["error"]["code"]) == (503, "unavailable")


async def test_preview_logs_no_routine_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    launch, app = _full_app(tmp_path, FakeClock(NOW))
    markers = ("name-8c3f1a", "purpose-8c3f1a", "prompt-8c3f1a", "instructions-8c3f1a")
    edits = profile_document()
    edits["instructions"] = markers[3]
    ProfileService(launch.state.store).revise(launch.profile_id, str(uuid4()), 1, edits)
    document = _document_raw(
        name=markers[0],
        purpose=markers[1],
        prompt=markers[2],
        profile_id=launch.profile_id,
        workspace_id=launch.workspace_id,
    )
    async with _serving(app) as client:
        previewed = await client.post(PREVIEW, headers=WRITE, json={"document": document})
        assert previewed.status_code == 200
        assert previewed.json()["resolved"]["first_turn"] == f"{markers[3]}\n\n{markers[2]}"
        for raw in (
            {"document": {**document, "deadline_minutes": 1}},
            {"command_id": str(uuid4()), "document": document},
        ):
            refused = await client.post(PREVIEW, headers=WRITE, json=raw)
            assert refused.status_code == 400
    logged = caplog.text
    assert TOKEN not in logged
    assert not [marker for marker in markers if marker in logged]
