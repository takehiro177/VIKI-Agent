"""A window re-subscribes every chat it tracks in one burst on each (re)connect (`socket.ts`
`dial`), then asks each chat with no history yet for its first page as it syncs
(`state/viki.ts`): forty live chats come back as forty `session.synced`, and history asks from
one window of 33 chats or three windows of 30 all get their pages, with no 4410 close."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import WebSocketTestSession
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect

from tests.test_server import authed, recv, running
from viki_agent import server
from viki_agent.session import SessionManager

IDS = [str(uuid.UUID(int=index)) for index in range(1, 41)]


class Wire:
    """Hands socket frames to the app the way uvicorn's websockets protocol
    (`WebSocketsSansIOProtocol`) does: every frame of one network read is queued before the app
    runs, and `receive` takes a queued frame without yielding (`asyncio.Queue.get`), so a
    window's back-to-back sends meet the receive loop as one burst. The test client's own
    stream yields on every receive, which would let each task finish before the next frame
    arrived."""

    def __init__(self) -> None:
        self.frames: dict[int, asyncio.Queue[Message]] = {}  # by id() of each socket's scope

    def wrap(self, app: ASGIApp) -> ASGIApp:
        async def wired(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "websocket":
                await app(scope, receive, send)
                return
            frames: asyncio.Queue[Message] = asyncio.Queue()
            self.frames[id(scope)] = frames

            async def pump() -> None:
                while True:
                    frames.put_nowait(await receive())

            pumping = asyncio.create_task(pump())
            try:
                await app(scope, frames.get, send)
            finally:
                pumping.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pumping

        return wired

    def burst(self, *bursts: tuple[WebSocketTestSession, list[dict[str, Any]]]) -> None:
        """On the loop: queue each socket's frames in one step, as one network read does."""
        for ws, messages in bursts:
            for message in messages:
                self.frames[id(ws.scope)].put_nowait(
                    {"type": "websocket.receive", "text": json.dumps(message)}
                )


def wired(monkeypatch: pytest.MonkeyPatch) -> Wire:
    """`running` builds its app through `server.create_app`; wrap that app in a Wire."""
    wire = Wire()
    create_app = server.create_app
    monkeypatch.setattr(server, "create_app", lambda **kwargs: wire.wrap(create_app(**kwargs)))
    return wire


async def start_all(manager: SessionManager, ids: list[str]) -> None:
    for sid in ids:
        assert await manager.create(sid).start()


def open_message(sid: str, **cursor: Any) -> dict[str, Any]:
    """The `session.open` `dial` sends for one tracked chat: `after_seq` always, and the
    `generation` once the window has one."""
    return {
        "type": "session.open",
        "session_id": sid,
        "request_id": str(uuid.uuid4()),
        "after_seq": 0,
        **cursor,
    }


def history_message(sid: str, generation: str | None = None) -> dict[str, Any]:
    """The `history.more` the shell sends for a chat's first page once it syncs. Without the
    chat's generation it reads nothing, which a bound test needs no more than that."""
    return {
        "type": "history.more",
        "session_id": sid,
        "generation": generation or str(uuid.uuid4()),
        "request_id": str(uuid.uuid4()),
    }


def detach_message(sid: str) -> dict[str, Any]:
    return {"type": "session.detach", "session_id": sid, "request_id": str(uuid.uuid4())}


def turn_message(sid: str, generation: str) -> dict[str, Any]:
    return {
        "type": "turn.send",
        "session_id": sid,
        "generation": generation,
        "command_id": str(uuid.uuid4()),
        "text": "synthetic",
    }


def answered(
    ws: WebSocketTestSession, kind: str, wanted: Iterable[str], key: str = "request_id"
) -> dict[str, dict[str, Any]]:
    """Read until a `kind` frame has arrived for each wanted `key` value; fail on a close."""
    waiting = set(wanted)
    answers: dict[str, dict[str, Any]] = {}
    try:
        while len(answers) < len(waiting):
            frame = recv(ws)
            if frame["type"] == kind and frame.get(key) in waiting:
                answers[frame[key]] = frame
    except WebSocketDisconnect as closed:
        pytest.fail(f"socket closed {closed.code} after {len(answers)} of {len(waiting)} {kind}")
    return answers


def resubscribe(
    wire: Wire, ws: WebSocketTestSession, cursors: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Send every chat's `session.open` in one burst and return each chat's `session.synced`."""
    messages = [open_message(sid, **cursor) for sid, cursor in cursors.items()]
    ws.portal.call(wire.burst, (ws, messages))
    synced = answered(ws, "session.synced", (message["request_id"] for message in messages))
    assert {request: frame["session_id"] for request, frame in synced.items()} == {
        message["request_id"]: message["session_id"] for message in messages
    }
    return {frame["session_id"]: frame for frame in synced.values()}


def test_forty_live_chats_resubscribed_in_one_burst_all_sync_on_connect_and_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire = wired(monkeypatch)
    with running(tmp_path) as app:
        app.manager.bind_max_live(lambda: 64)  # the built-in 30 would refuse ten of the starts
        with authed(app.client) as ws:
            ws.portal.call(start_all, app.manager, IDS)
            assert len(app.manager.live()) == len(IDS)
            first = resubscribe(wire, ws, {sid: {} for sid in IDS})
        cursors = {
            sid: {"generation": frame["generation"], "after_seq": frame["seq"]}
            for sid, frame in first.items()
        }
        with authed(app.client) as ws:  # the window's reconnect, each chat with its cursor
            again = resubscribe(wire, ws, cursors)
        assert {sid: frame["generation"] for sid, frame in again.items()} == {
            sid: cursor["generation"] for sid, cursor in cursors.items()
        }


@pytest.mark.parametrize(("windows", "chats"), [(1, 33), (3, 30)])
def test_every_window_asking_each_synced_chat_for_history_at_once_stays_connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: int, chats: int
) -> None:
    """Each window re-subscribes the same chats, then asks every one for its first page in one
    burst, all windows in the same step: 33 asks pass one socket's MAX_SOCKET_COMMANDS, and
    three windows of 30 pass MAX_APP_SOCKET_COMMANDS."""
    wire = wired(monkeypatch)
    ids = IDS[:chats]
    with running(tmp_path) as app, contextlib.ExitStack() as stack:
        app.manager.bind_max_live(lambda: 64)
        sockets = [stack.enter_context(authed(app.client)) for _ in range(windows)]
        sockets[0].portal.call(start_all, app.manager, ids)
        asks = [
            [
                history_message(sid, frame["generation"])
                for sid, frame in resubscribe(wire, ws, {sid: {} for sid in ids}).items()
            ]
            for ws in sockets
        ]
        sockets[0].portal.call(wire.burst, *zip(sockets, asks, strict=True))
        for ws, messages in zip(sockets, asks, strict=True):
            answered(ws, "history.page", (message["request_id"] for message in messages))


@pytest.mark.parametrize("bound", ["MAX_SOCKET_COMMANDS", "MAX_APP_SOCKET_COMMANDS"])
def test_a_command_past_its_bound_is_still_refused_beside_history_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    """At a bound of 2, three history asks then three sends in one burst: the asks count only
    against MAX_SOCKET_OPENS, so the first two sends are admitted and the third is refused
    `overloaded` as before."""
    wire = wired(monkeypatch)
    monkeypatch.setattr(server, bound, 2)
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.portal.call(start_all, app.manager, IDS[:3])
        synced = resubscribe(wire, ws, {sid: {} for sid in IDS[:3]})
        asks = [history_message(sid, frame["generation"]) for sid, frame in synced.items()]
        sends = [turn_message(sid, frame["generation"]) for sid, frame in synced.items()]
        ws.portal.call(wire.burst, (ws, asks + sends))
        receipts = answered(
            ws, "control.receipt", (send["command_id"] for send in sends), key="command_id"
        )
    outcomes = [receipts[send["command_id"]]["data"] for send in sends]
    assert outcomes[2] == {"status": "rejected", "reason": "overloaded"}
    assert all(outcome["status"] != "rejected" for outcome in outcomes[:2])


@pytest.mark.parametrize(
    ("bound", "message"),
    [
        ("MAX_SOCKET_OPENS", open_message),
        ("MAX_SOCKET_OPENS", history_message),
        ("MAX_SOCKET_COMMANDS", detach_message),
    ],
)
def test_one_message_past_its_own_in_flight_bound_in_a_burst_closes_the_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bound: str,
    message: Callable[[str], dict[str, Any]],
) -> None:
    wire = wired(monkeypatch)
    monkeypatch.setattr(server, bound, 2)
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.portal.call(start_all, app.manager, IDS[:3])
        ws.portal.call(wire.burst, (ws, [message(sid) for sid in IDS[:3]]))
        with pytest.raises(WebSocketDisconnect) as closed:
            for _ in range(8):
                recv(ws)
        assert closed.value.code == server.RESYNC_REQUIRED
