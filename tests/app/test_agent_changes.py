"""The window's Apply for a chat's safety changes: `AgentChanges` (its holds, journal and `seq`)
and its two routes alone, over a plain Starlette app. Host/Origin middleware and the window
token's check are server.py's job; here `authorized` stands in for that check, so a chat's agent
key is one more bearer it refuses."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from viki_agent.app import agent_changes
from viki_agent.app.agent_changes import AgentChangeError, AgentChanges

BASE = "http://127.0.0.1:8765"
WINDOW_TOKEN = "window-token"
AUTH = {"authorization": f"Bearer {WINDOW_TOKEN}"}
WRITE = {**AUTH, "origin": BASE}
AGENT = {"authorization": "Bearer agent-key"}
PATH = "/api/v2/agent-changes"
GRANT = "grant-1"
SESSION = "session-1"
SUMMARY = {"key": "chat.default_mode", "from": "default", "to": "acceptEdits", "reset": False}
HANG_GUARD_S = 10.0  # a guard on events that fire in milliseconds, not a work budget


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Apply:
    """An apply callback that counts its calls, then returns `result` or raises `error`."""

    def __init__(
        self, result: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.calls = 0
        self.result = {"revision": 1} if result is None else result
        self.error = error

    async def __call__(self) -> dict[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def held(
    changes: AgentChanges,
    apply: Callable[[], Awaitable[dict[str, Any]]],
    *,
    grant: str = GRANT,
    session: str = SESSION,
) -> str:
    pending = changes.hold(
        grant, session, "settings.set", "chat.default_mode", "setting", SUMMARY, apply
    )
    return pending["change_id"]


def app_for(changes: AgentChanges, workers: set[asyncio.Task[Any]] | None = None) -> Starlette:
    routes = changes.routes(
        authorized=lambda request: request.headers.get("authorization") == AUTH["authorization"],
        origins=frozenset({BASE}),
        workers=set() if workers is None else workers,
    )
    return Starlette(routes=routes)


def decide(
    client: TestClient, change_id: str, decision: str = "apply", headers: dict[str, str] = WRITE
) -> Any:
    return client.post(f"{PATH}/{change_id}", headers=headers, json={"decision": decision})


def code(response: Any) -> str:
    """The refusal's code. `Any`: Starlette's TestClient answers with httpx2's Response when
    httpx2 is installed, and httpx.AsyncClient with httpx's."""
    return response.json()["error"]["code"]


def states(changes: AgentChanges) -> list[str]:
    return [entry["state"] for entry in changes.for_session(SESSION)["recent"]]


def test_the_window_lists_a_held_change_and_applies_it_once() -> None:
    clock = Clock()
    changes = AgentChanges(lambda grant_id: True, clock=clock)
    apply = Apply({"revision": 7})
    pending = changes.hold(
        GRANT, SESSION, "settings.set", "chat.default_mode", "setting", SUMMARY, apply
    )
    change_id = pending["change_id"]
    assert pending == {"change_id": change_id, "expires_in_s": 600}
    clock.now += 100.5
    workers: set[asyncio.Task[Any]] = set()
    client = TestClient(app_for(changes, workers), base_url=BASE)
    listed = client.get(PATH, headers=AUTH)
    assert listed.status_code == 200
    assert listed.json() == {
        "seq": 0,
        "changes": [
            {
                "change_id": change_id,
                "session_id": SESSION,
                "kind": "setting",
                "summary": SUMMARY,
                "expires_in_s": 500,
            }
        ],
    }
    assert apply.calls == 0

    applied = decide(client, change_id)
    assert applied.status_code == 200
    assert applied.json() == {"state": "applied", "result": {"revision": 7}}
    assert apply.calls == 1 and not workers
    assert client.get(PATH, headers=AUTH).json() == {"seq": 1, "changes": []}
    for decision in ("apply", "reject"):
        again = decide(client, change_id, decision)
        assert again.status_code == 409 and code(again) == "not_pending"
    assert apply.calls == 1
    # The hold's own journal entry moved on in place.
    assert changes.for_session(SESSION) == {
        "pending": [],
        "recent": [
            {
                "change_id": change_id,
                "session_id": SESSION,
                "operation": "settings.set",
                "target": "chat.default_mode",
                "state": "applied",
            }
        ],
    }


def test_reject_never_applies() -> None:
    changes = AgentChanges(lambda grant_id: True)
    apply = Apply()
    change_id = held(changes, apply)
    client = TestClient(app_for(changes), base_url=BASE)
    rejected = decide(client, change_id, "reject")
    assert rejected.status_code == 200 and rejected.json() == {"state": "rejected"}
    late = decide(client, change_id)
    assert late.status_code == 409 and code(late) == "not_pending"
    assert apply.calls == 0 and changes.seq == 0
    assert client.get(PATH, headers=AUTH).json()["changes"] == []
    assert states(changes) == ["rejected"]


def test_a_hold_expires_after_its_ttl_and_never_applies() -> None:
    clock = Clock()
    changes = AgentChanges(lambda grant_id: True, clock=clock)
    apply = Apply()
    first = held(changes, apply)
    second = held(changes, apply)
    client = TestClient(app_for(changes), base_url=BASE)
    clock.now = 1599.5
    listed = client.get(PATH, headers=AUTH).json()["changes"]
    assert [(view["change_id"], view["expires_in_s"]) for view in listed] == [
        (first, 1),
        (second, 1),
    ]
    clock.now = 1600.0
    # The decision itself finds the deadline passed; nothing listed it first.
    expired = decide(client, first)
    assert expired.status_code == 409 and code(expired) == "expired"
    assert client.get(PATH, headers=AUTH).json() == {"seq": 0, "changes": []}
    assert changes.for_session(SESSION)["pending"] == []
    late = decide(client, second)
    assert late.status_code == 409 and code(late) == "expired"
    assert apply.calls == 0
    assert states(changes) == ["expired", "expired"]


def test_a_hold_whose_chat_ended_is_not_listed_and_never_applies() -> None:
    alive = {GRANT, "grant-2"}
    changes = AgentChanges(alive.__contains__)
    ended, running = Apply(), Apply()
    gone = held(changes, ended)
    kept = held(changes, running, grant="grant-2", session="session-2")
    client = TestClient(app_for(changes), base_url=BASE)
    alive.discard(GRANT)
    refused = decide(client, gone)
    assert refused.status_code == 409 and code(refused) == "expired"
    listed = client.get(PATH, headers=AUTH).json()["changes"]
    assert [view["change_id"] for view in listed] == [kept]
    assert decide(client, kept).status_code == 200
    assert (ended.calls, running.calls) == (0, 1)
    assert states(changes) == ["expired"]


def test_holds_cap_at_four_per_chat_and_sixteen_in_all() -> None:
    alive = {f"grant-{n}" for n in range(5)}
    changes = AgentChanges(alive.__contains__)

    def hold_for(n: int) -> str:
        return held(changes, Apply(), grant=f"grant-{n}", session=f"session-{n}")

    for _ in range(4):
        hold_for(0)
    with pytest.raises(AgentChangeError) as per_chat:
        hold_for(0)
    for n in (1, 2, 3):
        for _ in range(4):
            hold_for(n)
    with pytest.raises(AgentChangeError) as in_all:
        hold_for(4)
    for refused in (per_chat.value, in_all.value):
        assert (refused.code, refused.status) == ("too_many_pending", 429)
    # Holds that end give their slots back.
    alive.discard("grant-3")
    hold_for(4)


async def test_a_second_decision_while_applying_is_refused_and_apply_runs_once() -> None:
    entered, gate = asyncio.Event(), asyncio.Event()
    calls = 0

    async def apply() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:  # only the first call waits, so a second one would answer at once
            entered.set()
            await gate.wait()
        return {"revision": calls}

    changes = AgentChanges(lambda grant_id: True)
    url = f"{PATH}/{held(changes, apply)}"
    workers: set[asyncio.Task[Any]] = set()
    transport = httpx.ASGITransport(app=app_for(changes, workers))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        first = asyncio.create_task(client.post(url, headers=WRITE, json={"decision": "apply"}))
        await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        for decision in ("apply", "reject"):
            second = await client.post(url, headers=WRITE, json={"decision": decision})
            assert second.status_code == 409 and code(second) == "not_pending"
        assert len(workers) == 1
        gate.set()
        answer = await asyncio.wait_for(first, HANG_GUARD_S)
    assert answer.json() == {"state": "applied", "result": {"revision": 1}}
    assert calls == 1 and changes.seq == 1 and not workers


async def test_a_client_that_leaves_does_not_cancel_the_apply() -> None:
    entered, gate = asyncio.Event(), asyncio.Event()

    async def apply() -> dict[str, Any]:
        entered.set()
        await gate.wait()
        return {"revision": 1}

    changes = AgentChanges(lambda grant_id: True)
    url = f"{PATH}/{held(changes, apply)}"
    workers: set[asyncio.Task[Any]] = set()
    transport = httpx.ASGITransport(app=app_for(changes, workers))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        request = asyncio.create_task(client.post(url, headers=WRITE, json={"decision": "apply"}))
        await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        [worker] = workers
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        # The client is gone; the apply still finishes and is journaled.
        gate.set()
        await asyncio.wait([worker], timeout=HANG_GUARD_S)
    assert not worker.cancelled() and worker.result() == {"revision": 1}
    assert changes.seq == 1 and not workers
    assert states(changes) == ["applied"]


def test_a_refused_apply_answers_its_own_code_and_is_not_retried() -> None:
    changes = AgentChanges(lambda grant_id: True)
    apply = Apply(error=AgentChangeError("stale_revision", 409, "The setting changed."))
    change_id = held(changes, apply)
    client = TestClient(app_for(changes), base_url=BASE)
    failed = decide(client, change_id)
    assert failed.status_code == 409
    assert failed.json() == {"error": {"code": "stale_revision", "message": "The setting changed."}}
    again = decide(client, change_id)
    assert again.status_code == 409 and code(again) == "not_pending"
    assert apply.calls == 1 and changes.seq == 0
    assert states(changes) == ["failed"]


def test_an_unexpected_apply_error_answers_unavailable_and_logs_only_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    changes = AgentChanges(lambda grant_id: True)
    change_id = held(changes, Apply(error=RuntimeError("acceptEdits sk-sentinel")))
    client = TestClient(app_for(changes), base_url=BASE)
    with caplog.at_level(logging.WARNING):
        failed = decide(client, change_id)
    assert failed.status_code == 503 and code(failed) == "unavailable"
    logged = [r.getMessage() for r in caplog.records if r.name == agent_changes.__name__]
    assert logged == ["agent change failed (RuntimeError)"]
    assert "sk-sentinel" not in caplog.text + failed.text
    assert changes.seq == 0 and states(changes) == ["failed"]


def test_only_the_window_token_reaches_the_routes_and_a_post_needs_an_allowed_origin() -> None:
    changes = AgentChanges(lambda grant_id: True)
    apply = Apply()
    change_id = held(changes, apply)
    client = TestClient(app_for(changes), base_url=BASE)
    for headers in ({}, AGENT, {**AGENT, "origin": BASE}):
        listed = client.get(PATH, headers=headers)
        posted = decide(client, change_id, "apply", headers)
        for refused in (listed, posted):
            assert refused.status_code == 401 and code(refused) == "unauthorized"
    for headers in (AUTH, {**AUTH, "origin": "http://evil.example"}):
        refused = decide(client, change_id, "apply", headers)
        assert refused.status_code == 403 and code(refused) == "forbidden_origin"
    foreign = client.get(PATH, headers={**AUTH, "origin": "http://evil.example"})
    assert foreign.status_code == 403 and code(foreign) == "forbidden_origin"
    # A GET needs no Origin; one it carries must be allowed.
    for headers in (AUTH, WRITE):
        assert len(client.get(PATH, headers=headers).json()["changes"]) == 1
    assert apply.calls == 0


def test_a_decision_body_is_exactly_one_known_decision_within_1_kib() -> None:
    changes = AgentChanges(lambda grant_id: True)
    apply = Apply()
    url = f"{PATH}/{held(changes, apply)}"
    client = TestClient(app_for(changes), base_url=BASE)
    headers = {**WRITE, "content-type": "application/json"}
    for body in (
        b'{"decision":"apply","extra":1}',
        b'{"decision":"later"}',
        b'{"decision":"reject","decision":"apply"}',
        b'["apply"]',
        b"apply",
        b"",
        b'{"decision":"apply"}'.ljust(1025),
    ):
        refused = client.post(url, headers=headers, content=body)
        assert refused.status_code == 400 and code(refused) == "invalid_input", body
    assert apply.calls == 0
    apply_body = b'{"decision":"apply"}'
    unknown = client.post(f"{PATH}/no-such-change", headers=headers, content=apply_body)
    assert unknown.status_code == 404 and code(unknown) == "not_found"
    applied = client.post(url, headers=headers, content=apply_body.ljust(1024))
    assert applied.status_code == 200 and apply.calls == 1


def test_seq_rises_once_per_applied_change() -> None:
    changes = AgentChanges(lambda grant_id: True)
    client = TestClient(app_for(changes), base_url=BASE)
    changes.record(GRANT, SESSION, "bots.create", "bot-1")
    assert client.get(PATH, headers=AUTH).json()["seq"] == 1
    decide(client, held(changes, Apply()), "reject")
    decide(client, held(changes, Apply(error=AgentChangeError("stale_revision", 409))))
    assert changes.seq == 1
    decide(client, held(changes, Apply()))
    assert client.get(PATH, headers=AUTH).json()["seq"] == 2


def test_for_session_shows_one_chats_pending_holds_and_its_20_newest_entries() -> None:
    changes = AgentChanges(lambda grant_id: True)
    for n in range(22):
        changes.record(GRANT, SESSION, "routines.add", f"routine-{n}")
    changes.record("grant-2", "session-2", "bots.create", "bot-1")
    change_id = held(changes, Apply())
    held(changes, Apply(), grant="grant-2", session="session-2")
    mine = changes.for_session(SESSION)
    assert [view["change_id"] for view in mine["pending"]] == [change_id]
    assert mine["recent"][0] == {
        "change_id": change_id,
        "session_id": SESSION,
        "operation": "settings.set",
        "target": "chat.default_mode",
        "state": "pending",
    }
    assert [entry["target"] for entry in mine["recent"][1:]] == [
        f"routine-{n}" for n in range(21, 2, -1)
    ]
    assert {entry["state"] for entry in mine["recent"][1:]} == {"applied"}


def test_the_journal_keeps_the_last_64_entries() -> None:
    changes = AgentChanges(lambda grant_id: True)
    client = TestClient(app_for(changes), base_url=BASE)
    change_id = held(changes, Apply())
    decide(client, change_id, "reject")
    for n in range(63):
        changes.record(GRANT, SESSION, "routines.add", f"routine-{n}")
    # The rejected hold is still the oldest of 64 entries.
    assert code(decide(client, change_id)) == "not_pending"
    changes.record(GRANT, SESSION, "routines.add", "routine-63")
    forgotten = decide(client, change_id)
    assert forgotten.status_code == 404 and code(forgotten) == "not_found"
