"""HTTP contract for /api/v2/settings: the routes function alone, over a synthetic scratch
store. Host/Origin middleware and SECURITY_HEADERS are server.py's job (create_app wires in
settings_routes); this file proves what settings_api.py itself is responsible for:
Bearer/Origin admission, body shape, and the GET/PATCH/409/413 wire contract."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from viki_agent.app.settings import SettingsService
from viki_agent.app.settings_api import settings_routes
from viki_agent.state import AppState, Prefs
from viki_agent.storage import SessionStore

BASE = "http://127.0.0.1:8765"
TOKEN = "settings-test-token"
AUTH = {"authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "origin": BASE}
PATH = "/api/v2/settings"


def app_at(
    tmp_path: Path, *, defaults: Prefs | None = None, sink: list[Prefs] | None = None
) -> tuple[Starlette, SessionStore]:
    store = AppState(tmp_path / "state.json", Prefs()).store
    chosen = defaults if defaults is not None else Prefs()
    service = SettingsService(
        store,
        provider_defaults=lambda: chosen,
        on_chat_defaults=(sink.append if sink is not None else lambda prefs: None),
    )
    routes = settings_routes(
        service,
        authorized=lambda request: request.headers.get("authorization") == f"Bearer {TOKEN}",
        origins=frozenset({BASE}),
        workers=set[asyncio.Task[Any]](),
    )
    return Starlette(routes=routes), store


def test_get_returns_defaults_on_an_empty_store(tmp_path: Path) -> None:
    app, _store = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    body = client.get(PATH, headers=AUTH).json()
    assert body["version"] == 1
    assert body["revision"] == 0
    assert body["read_only"] is False
    assert body["reason"] is None
    assert body["values"] == {}
    assert body["effective"]["appearance.theme"] == "system"
    assert body["sources"]["appearance.theme"] == "default"
    assert body["keys"]["appearance.theme"] == {
        "scope": "app",
        "applies": "now",
        "kind": "enum",
        "allowed": ["dark", "light", "system"],
        "default": "system",
        "personal": False,
    }
    assert "key" not in body["keys"]["appearance.theme"]
    assert "max_len" not in body["keys"]["appearance.theme"]
    assert "bounds" not in body["keys"]["appearance.theme"]
    assert body["keys"]["chat.max_live_sessions"] == {
        "scope": "app",
        "applies": "now",
        "kind": "number",
        "allowed": None,
        "default": 30,
        "personal": False,
        "bounds": [1, 999],
    }
    assert body["effective"]["chat.max_live_sessions"] == 30


def test_patch_roundtrip_replay_and_stale_revision(tmp_path: Path) -> None:
    app, _store = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    cid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    saved = client.patch(
        PATH,
        headers=WRITE,
        json={
            "command_id": cid,
            "expected_revision": 0,
            "changes": {"appearance.theme": "dark"},
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["revision"] == 1
    assert body["effective"]["appearance.theme"] == "dark"
    assert body["command_id"] == cid
    assert body["replayed"] is False

    replay = client.patch(
        PATH,
        headers=WRITE,
        json={
            "command_id": cid,
            "expected_revision": 0,
            "changes": {"appearance.theme": "dark"},
        },
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["revision"] == 1

    stale = client.patch(
        PATH,
        headers=WRITE,
        json={
            "command_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "expected_revision": 0,
            "changes": {"appearance.theme": "light"},
        },
    )
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "stale_revision"
    assert error["current_revision"] == 1
    assert error["current"]["effective"]["appearance.theme"] == "dark"


def test_patch_field_errors_and_oversize_never_touch_storage(tmp_path: Path) -> None:
    app, _store = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    invalid = client.patch(
        PATH,
        headers=WRITE,
        json={
            "command_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            "expected_revision": 0,
            "changes": {"appearance.theme": "purple"},
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_settings"
    assert invalid.json()["error"]["fields"] == {"appearance.theme": "not_allowed"}
    assert client.get(PATH, headers=AUTH).json()["revision"] == 0


def test_routes_require_bearer_and_patch_requires_origin(tmp_path: Path) -> None:
    app, _store = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    unauthenticated = client.get(PATH)
    assert unauthenticated.status_code == 401
    body = {
        "command_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "expected_revision": 0,
        "changes": {},
    }
    no_origin = client.patch(PATH, headers=AUTH, json=body)
    assert no_origin.status_code == 403
    foreign_origin = client.patch(PATH, headers={**AUTH, "origin": "https://evil.test"}, json=body)
    assert foreign_origin.status_code == 403
    # A plain GET admits no Origin at all (matches draft_api/command_api).
    get_no_origin = client.get(PATH, headers=AUTH)
    assert get_no_origin.status_code == 200


def test_malformed_body_is_refused_before_the_service_sees_it(tmp_path: Path) -> None:
    app, _store = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    wrong_shape = client.patch(PATH, headers=WRITE, json={"command_id": "x", "changes": {}})
    assert wrong_shape.status_code == 400
    assert wrong_shape.json()["error"]["code"] == "invalid_body"
    not_json = client.patch(
        PATH, headers={**WRITE, "content-type": "text/plain"}, content=b"not json"
    )
    assert not_json.status_code == 415


def test_on_chat_defaults_fires_only_for_a_committed_chat_change(tmp_path: Path) -> None:
    sink: list[Prefs] = []
    app, _store = app_at(tmp_path, sink=sink)
    client = TestClient(app, base_url=BASE)
    client.patch(
        PATH,
        headers=WRITE,
        json={
            "command_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            "expected_revision": 0,
            "changes": {"appearance.theme": "dark"},
        },
    )
    assert sink == []
    client.patch(
        PATH,
        headers=WRITE,
        json={
            "command_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
            "expected_revision": 1,
            "changes": {"chat.default_effort": "low"},
        },
    )
    assert len(sink) == 1
    assert sink[0].effort == "low"


@pytest.mark.parametrize("value", [0, -1, "not-an-int"])
def test_expected_revision_must_be_a_nonnegative_int(tmp_path: Path, value: object) -> None:
    app, _store = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    response = client.patch(
        PATH,
        headers=WRITE,
        json={
            "command_id": "11111111-1111-4111-8111-111111111111",
            "expected_revision": value,
            "changes": {},
        },
    )
    if value == 0:
        assert response.status_code == 200
        # An empty patch against a fresh store is a no-op: no document is ever materialised.
        assert response.json()["revision"] == 0
    else:
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_settings"


def test_patch_body_over_16kib_is_413_before_reaching_the_service(tmp_path: Path) -> None:
    app, _store = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    body = json.dumps(
        {
            "command_id": "22222222-2222-4222-8222-222222222222",
            "expected_revision": 0,
            "changes": {"identity.display_name": "x" * (17 * 1024)},
        }
    ).encode("utf-8")
    assert len(body) > 16 * 1024
    response = client.patch(
        PATH, headers={**WRITE, "content-type": "application/json"}, content=body
    )
    assert response.status_code == 413
    # Refused before intake ever reached the service: still no document in the store.
    assert client.get(PATH, headers=AUTH).json()["revision"] == 0
