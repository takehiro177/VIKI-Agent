"""The Bot learning routes: window token and Origin first, exact bodies, refusals as codes."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, cast

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from viki_agent.messaging.bot_learning import LearningError
from viki_agent.messaging.bot_learning_api import BASE, bot_learning_routes

BOT = str(uuid.uuid4())
PROPOSAL = str(uuid.uuid4())


class _Learning:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def overview(self, bot_id: str) -> dict[str, Any]:
        if bot_id != BOT:
            raise LearningError("not_found")
        return {"bot_id": bot_id, "proposals": []}

    async def apply(self, proposal_id: str) -> str:
        self.calls.append(("apply", proposal_id))
        return "applied"

    async def reject(self, proposal_id: str) -> None:
        self.calls.append(("reject", proposal_id))

    async def undo(self, proposal_id: str) -> None:
        raise LearningError("not_undoable")

    def untaint(self, session_id: str) -> bool:
        self.calls.append(("untaint", session_id))
        return True

    def import_legacy(self, bot_id: str) -> Any:
        raise LearningError("nothing_to_import")


class _Runner:
    running = None

    async def start(self, bot_id: str, mode: str) -> Any:
        raise LearningError("run_running")


def _client(learning: _Learning) -> TestClient:
    def authorized(request: Request) -> bool:
        return request.headers.get("authorization") == "Bearer window"

    app = Starlette(
        routes=bot_learning_routes(
            cast(Any, learning),
            cast(Any, _Runner()),
            authorized=authorized,
            origins=frozenset({"http://127.0.0.1:8765"}),
            workers=set[asyncio.Task[Any]](),
        )
    )
    return TestClient(app)


AUTH = {"authorization": "Bearer window"}


def test_requests_without_the_window_token_or_from_another_origin_are_refused() -> None:
    learning = _Learning()
    client = _client(learning)
    assert client.get(f"{BASE}/{BOT}").status_code == 401
    other = {**AUTH, "origin": "https://example.com"}
    response = client.post(f"{BASE}/proposals/{PROPOSAL}", json={"action": "apply"}, headers=other)
    assert response.status_code == 403
    assert learning.calls == []


def test_bodies_are_exact_and_refusals_carry_codes() -> None:
    learning = _Learning()
    client = _client(learning)
    assert client.get(f"{BASE}/{BOT}", headers=AUTH).json() == {
        "bot_id": BOT,
        "proposals": [],
        "running": None,
    }
    assert client.get(f"{BASE}/{uuid.uuid4()}", headers=AUTH).status_code == 404
    assert client.get(f"{BASE}/not-a-uuid", headers=AUTH).status_code == 404
    bad = client.post(
        f"{BASE}/proposals/{PROPOSAL}", json={"action": "apply", "extra": 1}, headers=AUTH
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_input"
    ok = client.post(f"{BASE}/proposals/{PROPOSAL}", json={"action": "apply"}, headers=AUTH)
    assert ok.json() == {"proposal_id": PROPOSAL, "state": "applied"}
    assert ok.headers["cache-control"] == "no-store"
    undo = client.post(f"{BASE}/proposals/{PROPOSAL}", json={"action": "undo"}, headers=AUTH)
    assert undo.status_code == 409 and undo.json()["error"]["code"] == "not_undoable"
    busy = client.post(f"{BASE}/{BOT}/runs", json={"mode": "dream"}, headers=AUTH)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "run_running"
    assert client.post(f"{BASE}/{BOT}/runs", json={"mode": "all"}, headers=AUTH).status_code == 400
    session = str(uuid.uuid4())
    cleared = client.post(f"{BASE}/sessions/{session}/untaint", json={}, headers=AUTH)
    assert cleared.json() == {"session_id": session, "cleared": True}
    assert learning.calls == [("apply", PROPOSAL), ("untaint", session)]
    assert client.get(f"{BASE}/{BOT}?x=1", headers=AUTH).status_code == 400
