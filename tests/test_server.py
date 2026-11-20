"""server: edge cases the acceptance suite leaves open — URL/path parsing pitfalls, a binary
frame, the check-then-act race in _actor_for, a socket that disconnects mid-command, and the
vault poller surviving an exception."""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlencode

import anyio
import pytest
from claude_agent_sdk import CLIConnectionError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.types import Message
from starlette.websockets import WebSocketDisconnect

from viki_agent import __main__ as launcher
from viki_agent import rails, server
from viki_agent.app.diagnostics import ProviderChecks, Readiness
from viki_agent.app.settings import SettingsService
from viki_agent.catalog import QUERY_MAX_CHARS, Catalog, HistoryPage
from viki_agent.config import Config
from viki_agent.conversations.drafts import draft_scope
from viki_agent.conversations.turn_parts import ExcerptPart, render_excerpt_block
from viki_agent.drivers.claude.control import Launch
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.events import (
    AgentInfo,
    Event,
    GitChip,
    Rails,
    SearchHit,
    SearchResult,
    SessionRow,
    SessionsResult,
    SessionStarted,
    VaultCounts,
)
from viki_agent.profiles.profile_capabilities import FIXTURE_VERSION
from viki_agent.session import SessionManager
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError
from viki_agent.turn_input import TurnInput
from viki_agent.vault_storage import VaultBootstrap
from viki_agent.workspaces import WorkspaceService

NEW = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TOKEN = "test-token"
BASE = "http://127.0.0.1:8765"
# The per-launch ledger owner the launcher mints; the reset service and both ledgers share it.
P1_OWNER = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
# A second, valid-shaped ledger owner that must never be accepted alongside data's own P1_OWNER.
MISMATCHED_OWNER = "ffffffff-ffff-4fff-8fff-ffffffffffff"
ORIGIN = "http://127.0.0.1:8765"
# TestClient.websocket_connect ignores base_url (Host "testserver"); name the real host.
WS_HEADERS = {"host": "127.0.0.1:8765", "origin": ORIGIN}
BEARER = {"authorization": f"Bearer {TOKEN}"}


class EchoDriver:
    """Opens and finishes at once; records nothing beyond `alive`."""

    pid: int | None = None
    busy = False

    def __init__(self, launch: Launch, emit: Callable[[Event], None]) -> None:
        self.launch = launch
        self.alive = False
        self._emit = emit

    async def open(self) -> SessionStarted:
        self.alive = True
        return SessionStarted(
            model="default", mode="auto", effort="low", commands=[], agents=[], models=[],
            cli_version="1.0.0",
        )  # fmt: skip

    async def send(self, text: str) -> None:
        pass

    async def interrupt(self) -> None:
        pass

    def respond(self, tool_use_id: str, answer: Any) -> bool:
        return True

    def pending(self) -> list[str]:
        return []

    async def set_model(self, model: str) -> None:
        pass

    async def close(self) -> None:
        self.alive = False


class GatedDriver:
    """Like EchoDriver, but `open()` blocks on a thread-safe gate until the test releases it —
    for exercising a command that is still in flight when its socket disconnects."""

    pid: int | None = None
    busy = False

    def __init__(
        self,
        launch: Launch,
        gate: threading.Event,
        calls: queue.Queue[str],
        emit: Callable[[Event], None],
    ) -> None:
        self.launch = launch
        self.alive = False
        self._gate = gate
        self._calls = calls
        self._emit = emit

    async def open(self) -> SessionStarted:
        await asyncio.to_thread(self._gate.wait)
        self.alive = True
        self._calls.put("opened")
        return SessionStarted(
            model="default", mode="auto", effort="low", commands=[], agents=[], models=[],
            cli_version="1.0.0",
        )  # fmt: skip

    async def send(self, text: str) -> None:
        pass

    async def interrupt(self) -> None:
        pass

    def respond(self, tool_use_id: str, answer: Any) -> bool:
        return True

    def pending(self) -> list[str]:
        return []

    async def set_model(self, model: str) -> None:
        pass

    async def close(self) -> None:
        self.alive = False


class EmptyCatalog(Catalog):
    def exists(self, session_id: str) -> bool:
        return False

    def history(
        self, session_id: str, *, before_uuid: str | None = None, until: str | None = None
    ) -> HistoryPage:
        return HistoryPage(events=[], next_before_uuid=None)


class SlowExistsCatalog(Catalog):
    """`exists()` blocks until exactly two threads have called it, then answers False. Used to
    force two commands' `_actor_for` calls for the same brand-new session id to race past the
    awaited catalog.exists() check at the same moment, regardless of scheduling."""

    def __init__(self, home: Path, vault: Path, barrier: threading.Barrier) -> None:
        super().__init__(home, vault)
        self._barrier = barrier

    def exists(self, session_id: str) -> bool:
        self._barrier.wait(timeout=3)
        return False

    def history(
        self, session_id: str, *, before_uuid: str | None = None, until: str | None = None
    ) -> HistoryPage:
        return HistoryPage(events=[], next_before_uuid=None)


@dataclass
class App:
    client: TestClient
    manager: SessionManager
    vault: Path
    home: Path
    opened: queue.Queue[str]


@contextmanager
def running(
    tmp_path: Path,
    *,
    make_driver: Callable[..., Any] | None = None,
    manager: SessionManager | None = None,
    catalog: Catalog | None = None,
    vault_poll_s: float = 60.0,
    raise_server_exceptions: bool = True,
) -> Iterator[App]:
    vault = tmp_path / "vault"
    (vault / "30-knowledge").mkdir(parents=True)
    (vault / "30-knowledge" / "x.md").write_text("# x\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True)
    opened: queue.Queue[str] = queue.Queue()
    state = AppState(tmp_path / "data" / "state.json", Prefs(mode="auto", effort="low"))
    if manager is None:
        manager = SessionManager(
            config=DriverConfig(cli=("fake",), vault=vault, env={}),
            state=state,
            claude_home=home,
            make_driver=make_driver or (lambda launch, prefs, emit: EchoDriver(launch, emit)),
        )
    if catalog is None:
        catalog = EmptyCatalog(home, vault)
    config = Config(
        vault=vault,
        claude_home=home,
        cli=Path("fake"),
        edge=None,
        data_dir=tmp_path / "data",
    )
    app = server.create_app(
        config=config,
        token=TOKEN,
        manager=manager,
        catalog=catalog,
        static_dir=None,
        opener=opened.put,
        auth_timeout_s=0.05,
        vault_poll_s=vault_poll_s,
    )
    with TestClient(app, base_url=BASE, raise_server_exceptions=raise_server_exceptions) as client:
        yield App(client, manager, vault, home, opened)


async def _next_message(ws: WebSocketTestSession, timeout: float) -> Message:
    with anyio.fail_after(timeout):
        return await ws._send_rx.receive()


def recv(ws: WebSocketTestSession, timeout: float = 3.0) -> dict[str, Any]:
    message = ws.portal.call(_next_message, ws, timeout)
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(message.get("code", 1000))
    return json.loads(message["text"])


@contextmanager
def authed(client: TestClient) -> Iterator[WebSocketTestSession]:
    with client.websocket_connect("/ws", headers=WS_HEADERS) as ws:
        ws.send_json({"type": "auth", "token": TOKEN, "v": 2})
        assert recv(ws)["type"] == "vault.sessions"
        yield ws


def _open_v2(
    ws: WebSocketTestSession,
    session_id: str = NEW,
    *,
    generation: str | None = None,
    after_seq: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    message: dict[str, Any] = {
        "type": "session.open",
        "session_id": session_id,
        "request_id": str(uuid.uuid4()),
        "after_seq": after_seq,
    }
    if generation is not None:
        message["generation"] = generation
    ws.send_json(message)
    snapshot, synced = recv(ws), recv(ws)
    assert (snapshot["type"], synced["type"]) == ("session.snapshot", "session.synced")
    assert snapshot["session_id"] == synced["session_id"] == session_id
    assert snapshot["request_id"] == synced["request_id"] == message["request_id"]
    assert snapshot["generation"] == synced["generation"]
    return snapshot, synced


def _mutate_v2(ws: WebSocketTestSession, target: dict[str, Any], type_: str, **fields: Any) -> str:
    command_id = str(uuid.uuid4())
    ws.send_json(
        {
            "type": type_,
            "session_id": target["session_id"],
            "generation": target["generation"],
            "command_id": command_id,
            **fields,
        }
    )
    return command_id


def _receipt_v2(ws: WebSocketTestSession, command_id: str) -> dict[str, Any]:
    value = recv(ws)
    assert (value["type"], value["command_id"]) == ("control.receipt", command_id)
    return value


def _history_v2(ws: WebSocketTestSession, target: dict[str, Any]) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    ws.send_json(
        {
            "type": "history.more",
            "session_id": target["session_id"],
            "generation": target["generation"],
            "request_id": request_id,
        }
    )
    page = recv(ws)
    assert (page["type"], page["request_id"], page["generation"]) == (
        "history.page",
        request_id,
        target["generation"],
    )
    return page


# --- open.url parsing pitfalls ----------------------------------------------------------------


def test_open_url_rejects_a_colon_without_slashes(tmp_path: Path) -> None:
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.send_json({"type": "open.url", "url": "https:evil.example"})
        ws.send_json({"type": "open.url", "url": "https://good.example"})
        assert app.opened.get(timeout=3) == "https://good.example"
        assert app.opened.empty()


def test_failed_new_session_save_still_subscribes_and_retries_without_resuming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[Launch] = []

    def make(launch: Launch, _prefs: Prefs, emit: Callable[[Event], None]) -> EchoDriver:
        launches.append(launch)
        return EchoDriver(launch, emit)

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise StateError("unavailable")

    with running(tmp_path, make_driver=make) as app, authed(app.client) as ws:
        with monkeypatch.context() as patch:
            patch.setattr(AppState, "remember", unavailable)
            snapshot, target = _open_v2(ws)
            assert snapshot["seq"] == 1
            assert (
                snapshot["data"]["state"]["error"]["message"] == StateError("unavailable").message
            )
            actor = app.manager.get(NEW)
            assert actor is not None and not actor.live and not launches
            _history_v2(ws, target)
        command = _mutate_v2(ws, target, "turn.send", text="retry")
        assert [(e["type"], e["seq"]) for e in (recv(ws, 0.05) for _ in range(3))] == [
            ("session.started", 2),
            ("user.message", 3),
            ("turn.started", 4),
        ]
        assert _receipt_v2(ws, command)["data"]["status"] == "applied"
        assert len(launches) == 1 and not launches[0].resume
        assert app.manager.get(NEW) is actor


def test_failed_fork_save_replays_its_error_and_preserves_its_source_on_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fork_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    launches: list[Launch] = []

    def make(launch: Launch, _prefs: Prefs, emit: Callable[[Event], None]) -> EchoDriver:
        launches.append(launch)
        return EchoDriver(launch, emit)

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise StateError("unavailable")

    with running(tmp_path, make_driver=make) as app, authed(app.client) as ws:
        ws.portal.call(app.manager.create, NEW)
        _, source_target = _open_v2(ws)
        with monkeypatch.context() as patch:
            patch.setattr(AppState, "remember", unavailable)
            command = _mutate_v2(ws, source_target, "session.fork", fork_id=fork_id)
            assert _receipt_v2(ws, command)["data"] == {
                "status": "unknown",
                "reason": "storage_unavailable",
            }
            assert not launches
            snapshot, fork_target = _open_v2(ws, fork_id)
            assert snapshot["seq"] == 1
            assert (
                snapshot["data"]["state"]["error"]["message"] == StateError("unavailable").message
            )
            _history_v2(ws, fork_target)
        command = _mutate_v2(ws, fork_target, "turn.send", text="retry")
        assert [recv(ws, timeout=0.05)["seq"] for _ in range(3)] == [2, 3, 4]
        assert _receipt_v2(ws, command)["data"]["status"] == "applied"
        assert len(launches) == 1 and launches[0] == Launch(fork_id, fork_of=NEW, agent_cli=True)


def test_open_url_opens_an_uppercase_scheme_because_the_renderer_draws_it(tmp_path: Path) -> None:
    """Markdown.tsx lowercases a href's scheme before deciding whether to make it an anchor, so
    `HTTPS://x` is a live link on screen. This used to reject it, which is the one failure a
    link can have that the user cannot diagnose: they click, and nothing at all happens."""
    good = ["HTTPS://example.com", "MailTo:someone@example.com"]
    with running(tmp_path) as app, authed(app.client) as ws:
        for url in good:
            ws.send_json({"type": "open.url", "url": url})
        # Each message runs as its own task, and each opener runs in a worker thread, so the
        # two opens have no defined order (as the frozen acceptance test
        # test_open_url_passes_only_web_and_mail_links compares its opens order-independently).
        assert sorted(app.opened.get(timeout=3) for _ in good) == sorted(good)
        assert app.opened.empty()


# --- open.vault path pitfalls ------------------------------------------------------------------


def test_open_vault_rejects_a_different_drive_letter(tmp_path: Path) -> None:
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.send_json({"type": "open.vault", "path": "Z:\\nonexistent\\file.md"})
        ws.send_json({"type": "open.vault", "path": "30-knowledge/x.md"})
        target = "vscode://file/" + (app.vault / "30-knowledge" / "x.md").resolve().as_posix()
        assert app.opened.get(timeout=3) == target
        assert app.opened.empty()


def test_open_vault_allows_dot_dot_inside_but_never_leaves_the_vault(tmp_path: Path) -> None:
    (tmp_path / "outside-of-vault.md").write_text("x", encoding="utf-8")  # exists: only
    # containment can refuse it
    with running(tmp_path) as app, authed(app.client) as ws:
        target = "vscode://file/" + (app.vault / "30-knowledge" / "x.md").resolve().as_posix()
        ws.send_json({"type": "open.vault", "path": "30-knowledge/../30-knowledge/x.md"})
        assert app.opened.get(timeout=3) == target
        ws.send_json({"type": "open.vault", "path": "../outside-of-vault.md"})
        ws.send_json({"type": "open.vault", "path": "30-knowledge/../../outside-of-vault.md"})
        ws.send_json({"type": "open.vault", "path": "30-knowledge/x.md"})  # barrier
        assert app.opened.get(timeout=3) == target
        assert app.opened.empty()


def test_open_vault_escapes_what_a_vscode_url_parses_specially(tmp_path: Path) -> None:
    """VS Code ends a `vscode://file` path at "#" or "?" and percent-decodes it: unescaped, a
    memory file Claude Code named `feedback_c#_style.md` would open `feedback_c`, and `%2F` in a
    name would reach another folder than the one the containment check saw."""
    with running(tmp_path) as app, authed(app.client) as ws:
        memory = app.vault / "bot-memory" / "11111111-1111-4111-8111-111111111111"
        memory.mkdir(parents=True)
        for name in ("feedback_c#_style.md", "x%41y.md"):
            (memory / name).write_text("x\n", encoding="utf-8")
        ref = memory.relative_to(app.vault).as_posix()
        ws.send_json({"type": "open.vault", "path": f"{ref}/feedback_c#_style.md"})
        first = app.opened.get(timeout=3)
        ws.send_json({"type": "open.vault", "path": f"{ref}/x%41y.md"})
        second = app.opened.get(timeout=3)
        assert app.opened.empty()
    folder = "vscode://file/" + memory.resolve().as_posix()
    assert (first, second) == (f"{folder}/feedback_c%23_style.md", f"{folder}/x%2541y.md")


def test_open_vault_escapes_the_whole_path_and_a_question_mark_whatever_the_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Windows file name holds "?", so resolution is stubbed: this pins the escaping alone,
    over the whole resolved path, a vault root holding "#" included."""
    monkeypatch.setattr(server, "_vault_target", lambda _vault, _raw: Path("C:/my#vault/a?b%c.md"))
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.send_json({"type": "open.vault", "path": "30-knowledge/x.md"})
        assert app.opened.get(timeout=3) == "vscode://file/C:/my%23vault/a%3Fb%25c.md"
        assert app.opened.empty()


def test_open_transcript_escapes_its_record_path_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = Path("C:/home/#1/a?b%c.jsonl")
    monkeypatch.setattr(EmptyCatalog, "transcript_path", lambda _self, _session_id: record)
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.send_json({"type": "open.transcript", "session_id": NEW})
        assert app.opened.get(timeout=3) == "vscode://file/C:/home/%231/a%3Fb%25c.jsonl"
        assert app.opened.empty()


def test_vault_target_rejects_anchored_paths_before_touching_the_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """a UNC path ("\\\\host\\share\\x") must never reach Path.resolve() — on loopback it
    still goes to the SMB redirector, and against a real remote host that blocks the event loop
    (every session's stream, not only this command) for the SMB connect timeout. A drive-letter
    or bare-root path must be rejected the same way, before any filesystem call, not only after
    resolving it turns out to land outside the vault."""

    def must_not_resolve(self: Path, *a: Any, **k: Any) -> Path:
        raise AssertionError(f"resolve() must not be called for an anchored path: {self}")

    monkeypatch.setattr(Path, "resolve", must_not_resolve)
    vault = tmp_path / "vault"
    for anchored in (
        "\\\\attacker.example\\share\\x.md",
        "Z:\\nonexistent\\file.md",
        "C:\\Windows\\win.ini",
        "\\bare-root.md",
    ):
        assert server._vault_target(vault, anchored) is None


async def _thread_ident() -> int:
    return threading.get_ident()


def test_open_vault_resolves_off_the_event_loop_thread(tmp_path: Path) -> None:
    """_vault_target (resolve() + is_file(), filesystem calls) must run off the event loop
    thread — dispatch used to call it directly, so a slow resolve (a real UNC host, a slow
    disk) would block every session's stream, not only this open.vault command. Compares
    against the app's own event-loop thread (via its TestClient portal), not the test's thread:
    the TestClient already runs the whole app on a thread other than the test's."""
    caller_threads: queue.Queue[int] = queue.Queue()
    real = server._vault_target

    def spy(vault: Path, raw: str) -> Path | None:
        caller_threads.put(threading.get_ident())
        return real(vault, raw)

    with running(tmp_path) as app, authed(app.client) as ws:
        loop_thread = ws.portal.call(_thread_ident)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(server, "_vault_target", spy)
            ws.send_json({"type": "open.vault", "path": "30-knowledge/x.md"})
            target = "vscode://file/" + (app.vault / "30-knowledge" / "x.md").resolve().as_posix()
            assert app.opened.get(timeout=3) == target
    assert caller_threads.get(timeout=3) != loop_thread


# --- malformed frames -------------------------------------------------------------------------


def test_a_binary_frame_is_dropped_and_the_socket_stays_open(tmp_path: Path) -> None:
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.send_bytes(b"\x00\x01\x02not-json-either")
        snapshot, _ = _open_v2(ws)
        assert snapshot["data"]["state"]["info"]["cli_version"] == "1.0.0"


# --- the check-then-act race in _actor_for ------------------------------------------------------


def test_concurrent_opens_and_post_sync_turn_share_one_actor(tmp_path: Path) -> None:
    """dispatch runs every client message as its own task, so two session.open requests
    sent back-to-back for the same brand-new session id each call _actor_for concurrently. A
    slow (barrier-gated) catalog.exists() forces both to be in flight at once. manager.create()
    is not idempotent, so without a re-check after the await, each would make its own actor and
    the second would silently orphan the first (and its socket subscription)."""
    barrier = threading.Barrier(2)
    opens: queue.Queue[str] = queue.Queue()

    def make_driver(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> Any:
        opens.put(launch.session_id)
        return EchoDriver(launch, emit)

    catalog = SlowExistsCatalog(tmp_path / "home", tmp_path / "vault", barrier)
    with running(tmp_path, make_driver=make_driver, catalog=catalog) as app:
        with authed(app.client) as ws:
            ws.send_json(
                {
                    "type": "session.open",
                    "session_id": NEW,
                    "request_id": str(uuid.uuid4()),
                }
            )
            snapshot, target = _open_v2(ws)
            # Both allocation reads meet at the barrier. Only the latest subscription may
            # synchronize; the now-known target admits its first turn after that barrier.
            command = _mutate_v2(ws, target, "turn.send", text="hi")
            expected = ["user.message", "turn.started"]
            if snapshot["data"]["state"]["info"] is None:
                expected.insert(0, "session.started")
            assert [recv(ws)["type"] for _ in expected] == expected
            assert _receipt_v2(ws, command)["data"]["status"] == "applied"
        # Exactly one actor/driver was ever created for NEW, however the two tasks interleaved.
        assert opens.get(timeout=3) == NEW
        assert opens.empty()


# --- a labelled id with no transcript -----------------------------------------------------------


class MissingCatalog(EmptyCatalog):
    """No transcript for any id, and a history read that says so."""

    def history(
        self, session_id: str, *, before_uuid: str | None = None, until: str | None = None
    ) -> HistoryPage:
        return HistoryPage(events=[], next_before_uuid=None, status="missing")


class ExistingCatalog(EmptyCatalog):
    def exists(self, session_id: str) -> bool:
        return True


def test_a_labelled_id_with_no_transcript_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pinned label whose transcript was pruned opens as a resume actor that never starts a
    process: its history says the transcript is missing. An unlabelled new id still starts."""
    other = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    started: queue.Queue[str] = queue.Queue()
    on_loop: list[str] = []

    def make_driver(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> Any:
        started.put(launch.session_id)
        return EchoDriver(launch, emit)

    catalog = MissingCatalog(tmp_path / "home", tmp_path / "vault")
    with running(tmp_path, make_driver=make_driver, catalog=catalog) as app:
        label = {
            "session_id": NEW,
            "revision": 1,
            "title": "t",
            "pinned": 1,
            "archived": 0,
            "unread": 0,
            "color": None,
        }
        app.manager.app_state.store.update_session_metadata(
            draft_scope(app.vault), NEW, lambda _: label
        )
        store = app.manager.app_state.store
        read = store.get_session_metadata

        def spy(scope_: str, session_id: str) -> dict[str, Any] | None:
            try:
                asyncio.get_running_loop()
                on_loop.append(session_id)
            except RuntimeError:
                pass
            return read(scope_, session_id)

        monkeypatch.setattr(store, "get_session_metadata", spy)
        with authed(app.client) as ws:
            snapshot, target = _open_v2(ws)
            assert snapshot["data"]["state"]["info"] is None
            assert _history_v2(ws, target)["data"]["error"] == "transcript_missing"
            actor = app.manager.get(NEW)
            assert actor is not None and actor.transcript_expected and not actor.live
            _open_v2(ws, other)
            assert started.get(timeout=3) == other
        assert started.empty()
    assert on_loop == []


def test_opening_an_existing_transcript_reads_its_origin_off_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_loop: list[str] = []

    with running(tmp_path, catalog=ExistingCatalog(tmp_path / "home", tmp_path / "vault")) as app:
        store = app.manager.app_state.store
        read = store.session_origin

        def spy(session_id: str) -> str | None:
            try:
                asyncio.get_running_loop()
                on_loop.append(session_id)
            except RuntimeError:
                pass
            return read(session_id)

        monkeypatch.setattr(store, "session_origin", spy)
        with authed(app.client) as ws:
            _open_v2(ws)
        assert app.manager.get(NEW) is not None
    assert on_loop == []


def test_manager_open_with_a_known_origin_reads_no_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = AppState(tmp_path / "data" / "state.json", Prefs(mode="auto", effort="low"))
    manager = SessionManager(
        config=DriverConfig(cli=("fake",), vault=tmp_path / "vault", env={}),
        state=state,
        claude_home=tmp_path / "home",
        make_driver=lambda *_: pytest.fail("opening an actor must not start a provider"),
    )
    reads: list[str] = []
    real = state.store.session_origin

    def spy(session_id: str) -> str | None:
        reads.append(session_id)
        return real(session_id)

    monkeypatch.setattr(state.store, "session_origin", spy)
    manager.open(NEW, profile=False)
    manager.open("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", profile=True)
    assert reads == []
    manager.open("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    assert reads == ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"]


def test_a_fork_reads_its_source_origin_off_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fork_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    on_loop: list[str] = []

    with running(tmp_path) as app, authed(app.client) as ws:
        ws.portal.call(app.manager.create, NEW)
        _, source_target = _open_v2(ws)
        store = app.manager.app_state.store
        read = store.session_origin

        def spy(session_id: str) -> str | None:
            try:
                asyncio.get_running_loop()
                on_loop.append(session_id)
            except RuntimeError:
                pass
            return read(session_id)

        monkeypatch.setattr(store, "session_origin", spy)
        command = _mutate_v2(ws, source_target, "session.fork", fork_id=fork_id)
        assert _receipt_v2(ws, command)["data"]["status"] == "applied"
        assert app.manager.get(fork_id) is not None
    assert on_loop == []


# --- a socket that disconnects mid-command ------------------------------------------------------


def test_a_running_command_outlives_its_socket_disconnecting(tmp_path: Path) -> None:
    gate = threading.Event()
    calls: queue.Queue[str] = queue.Queue()

    def make_driver(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> Any:
        return GatedDriver(launch, gate, calls, emit)

    with running(tmp_path, make_driver=make_driver) as app:
        # gate.set() in `finally`, INSIDE the app's `with`: the app's shutdown closes every
        # live actor, and a close while the driver is still opening waits on the same lock
        # `_start()` holds (session.py). An assertion failing before the gate opens would
        # otherwise deadlock the teardown, and the whole suite with it (seen once, under load).
        try:
            with authed(app.client) as ws:
                ws.send_json(
                    {
                        "type": "session.open",
                        "session_id": NEW,
                        "request_id": str(uuid.uuid4()),
                    }
                )
                # Exit the `with` block right away, before GatedDriver.open() unblocks: the
                # socket disconnects while the session.open command is still in flight.
            assert calls.empty()
            # The command runs as its own task on the app's loop; give it a moment to get the
            # actor live (a loaded machine can schedule it after this thread gets here). Wait
            # for the condition actually asserted below, not just for the actor to exist:
            # _actor_for creates it and only then awaits actor.start(), so a poll that stops at
            # "exists" can look in through that gap and read live=False on a healthy run.
            actor = app.manager.get(NEW)
            for _ in range(300):
                if actor is not None and actor.live:
                    break
                threading.Event().wait(0.01)
                actor = app.manager.get(NEW)
            # Already "live" (a driver exists and is starting), even though open() has not
            # returned yet — the command was not cancelled by the socket disconnecting.
            assert actor is not None and actor.live
            gate.set()
            assert calls.get(timeout=3) == "opened"
            assert actor.live
            # session.open's replay+subscribe resumed only after the socket was already
            # gone (finally already ran and closed=True) — it must not subscribe a dead queue
            # for the rest of the actor's life, since nothing would ever unsubscribe it.
            assert len(actor._subscribers) == 0
        finally:
            gate.set()


# --- the vault poller ---------------------------------------------------------------------------


def test_the_vault_poller_survives_an_exception_and_keeps_polling(tmp_path: Path) -> None:
    ticks: queue.Queue[int] = queue.Queue()
    count = {"n": 0}

    def flaky_others_in_vault() -> list[Any]:
        count["n"] += 1
        ticks.put(count["n"])
        if count["n"] <= 2:
            raise OSError("registry file vanished mid-read")
        return []

    with running(tmp_path, vault_poll_s=0.02) as app:
        app.manager.others_in_vault = flaky_others_in_vault  # type: ignore[method-assign]
        # Bounded waits, not sleeps: the poller must call others_in_vault three times on
        # schedule, even though the first two calls raise.
        seen = [ticks.get(timeout=1) for _ in range(3)]
        assert seen == [1, 2, 3]


def test_the_app_starts_even_when_the_first_poll_fails(tmp_path: Path) -> None:
    def always_raises() -> list[Any]:
        raise OSError("boom")

    state = AppState(tmp_path / "data" / "state.json", Prefs(mode="auto", effort="low"))
    manager = SessionManager(
        config=DriverConfig(cli=("fake",), vault=tmp_path / "vault", env={}),
        state=state,
        claude_home=tmp_path / "home",
        make_driver=lambda launch, prefs, emit: EchoDriver(launch, emit),
    )
    manager.others_in_vault = always_raises  # type: ignore[method-assign]
    with running(tmp_path, manager=manager, vault_poll_s=60.0) as app, authed(app.client) as ws:
        # A fresh connection still gets the (empty, default) vault.sessions snapshot.
        ws.send_json({"type": "open.url", "url": "https://still-working.example"})
        assert app.opened.get(timeout=3) == "https://still-working.example"


# --- foreign hosts on the token check -----------------------------------------------------------


def test_bad_json_first_message_is_unauthorized(tmp_path: Path) -> None:
    with running(tmp_path) as app:
        connect = app.client.websocket_connect("/ws", headers=WS_HEADERS)
        with connect as ws:
            ws.send_text("not json at all")
            with pytest.raises(WebSocketDisconnect) as closed:
                recv(ws)
            assert closed.value.code == server.UNAUTHORIZED


# --- request hardening: tokens, hosts, logged types, control characters, fork ids ---------------


def test_a_non_ascii_token_is_refused_with_4401_not_a_crash(tmp_path: Path) -> None:
    with running(tmp_path) as app, app.client.websocket_connect("/ws", headers=WS_HEADERS) as ws:
        ws.send_json({"type": "auth", "token": "é", "v": 2})
        with pytest.raises(WebSocketDisconnect) as closed:
            recv(ws)
        assert closed.value.code == server.UNAUTHORIZED


def test_a_foreign_host_gets_400_with_the_security_headers(tmp_path: Path) -> None:
    with running(tmp_path) as app:
        response = app.client.get("/", headers={"host": "evil.example:8765"})
        assert response.status_code == 400
        assert response.headers["content-security-policy"] == server.CSP


def test_the_face_route_is_absent_with_security_headers(tmp_path: Path) -> None:
    with running(tmp_path) as app:
        assert all(
            not isinstance(route, Route) or route.path != "/api/face/{size:int}"
            for route in cast(Starlette, app.client.app).routes
        )
        response = app.client.get("/api/face/64")
        assert response.status_code == 404
        assert response.headers["content-security-policy"] == server.CSP
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_dropped_messages_never_log_a_client_chosen_type(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.send_json({"type": "my private prompt\nFAKE ERROR line"})
        ws.send_json({"type": "open.url", "url": "https://example.com"})  # barrier
        assert app.opened.get(timeout=3) == "https://example.com"
    assert "private prompt" not in caplog.text and "type=?" in caplog.text


def test_open_url_refuses_every_control_character(tmp_path: Path) -> None:
    with running(tmp_path) as app, authed(app.client) as ws:
        for bad in ("https://example.com/\x7f", "https://example.com/\x9b"):
            ws.send_json({"type": "open.url", "url": bad})
        ws.send_json({"type": "open.url", "url": "https://example.com"})
        assert app.opened.get(timeout=3) == "https://example.com"
        assert app.opened.empty()


def test_a_fork_id_that_names_an_existing_transcript_is_refused(tmp_path: Path) -> None:
    other = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    class OneTranscript(EmptyCatalog):
        def exists(self, session_id: str) -> bool:
            return session_id == other

    home, vault = tmp_path / "home", tmp_path / "vault"
    with running(tmp_path, catalog=OneTranscript(home, vault)) as app, authed(app.client) as ws:
        _, target = _open_v2(ws)
        command = _mutate_v2(ws, target, "session.fork", fork_id=other)
        assert _receipt_v2(ws, command)["data"] == {"status": "rejected", "reason": "conflict"}
        assert app.manager.get(other) is None


# --- wire encoding, the auth timeout, no vault writes, and the face read ------------------------


def test_dumps_escapes_a_lone_surrogate_instead_of_crashing() -> None:
    """Starlette's send_json (ensure_ascii=False) would leave the raw surrogate code point
    in the str, and uvicorn's websockets-sansio raises UnicodeEncodeError encoding that to bytes
    for the wire — the exact failure _dumps exists to avoid. ensure_ascii=True (the default,
    used here) escapes it as plain ASCII instead, so the text always encodes cleanly."""
    text = server._dumps({"data": {"text": "\ud83d"}})
    assert "\\ud83d" in text
    text.encode()  # must not raise UnicodeEncodeError, unlike ensure_ascii=False would


def test_writer_closes_the_socket_on_an_unsendable_envelope(tmp_path: Path) -> None:
    """An envelope json.dumps cannot encode must not leave the writer task dead with the
    socket sitting OPEN and silent forever — close it with 4410 so the client's reconnect
    backoff kicks in instead."""
    with running(tmp_path) as app, authed(app.client) as ws:
        _, target = _open_v2(ws)
        actor = app.manager.get(NEW)
        assert actor is not None and actor._subscribers

        def emit_unsendable() -> None:
            for subscriber in list(actor._subscribers):
                subscriber(
                    {
                        "v": 2,
                        "type": "status",
                        "session_id": NEW,
                        "generation": target["generation"],
                        "seq": 999,
                        "data": {"bad": object()},  # not JSON-serializable at any setting
                    }
                )

        ws.portal.call(emit_unsendable)
        with pytest.raises(WebSocketDisconnect) as closed:
            recv(ws)
        assert closed.value.code == 4410


def test_auth_timeout_defaults_to_five_seconds() -> None:
    """AGENTS.md's 5 s auth default (hard rule 7) is pinned here: AUTH_TIMEOUT_S=60 would still
    pass check.py without this."""
    assert server.AUTH_TIMEOUT_S == 5.0


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


class _RecordingDriver:
    """Like EchoDriver, but close() reports back so the test can wait for the whole flow to
    finish before taking the "after" filesystem snapshot."""

    pid: int | None = None
    busy = False

    def __init__(
        self, launch: Launch, calls: queue.Queue[str], emit: Callable[[Event], None]
    ) -> None:
        self.launch = launch
        self.alive = False
        self._calls = calls
        self._emit = emit

    async def open(self) -> SessionStarted:
        self.alive = True
        return SessionStarted(
            model="default", mode="auto", effort="low", commands=[], agents=[], models=[],
            cli_version="1.0.0",
        )  # fmt: skip

    async def send(self, text: str) -> None:
        pass

    async def interrupt(self) -> None:
        pass

    def respond(self, tool_use_id: str, answer: Any) -> bool:
        return True

    def pending(self) -> list[str]:
        return []

    async def set_model(self, model: str) -> None:
        pass

    async def close(self) -> None:
        self.alive = False
        self._calls.put("closed")


def test_a_full_session_flow_never_touches_the_vault_or_home(tmp_path: Path) -> None:
    """AGENTS.md's hard rule 1 (no writes to the vault or ~/.claude), pinned end to end on the
    server side. Every file's path, size and mtime_ns under the test vault and home must be
    identical before and after a full flow: open a new session, a turn, history.more,
    open.vault, and close."""
    calls: queue.Queue[str] = queue.Queue()

    def make_driver(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> Any:
        return _RecordingDriver(launch, calls, emit)

    with running(tmp_path, make_driver=make_driver) as app:
        before_vault = _tree_snapshot(app.vault)
        before_home = _tree_snapshot(app.home)
        with authed(app.client) as ws:
            snapshot, target = _open_v2(ws)
            assert snapshot["data"]["state"]["info"]["cli_version"] == "1.0.0"
            command = _mutate_v2(ws, target, "turn.send", text="hi")
            assert {recv(ws)["type"] for _ in range(2)} == {"user.message", "turn.started"}
            assert _receipt_v2(ws, command)["data"]["status"] == "applied"
            _history_v2(ws, target)
            ws.send_json({"type": "open.vault", "path": "30-knowledge/x.md"})
            opened_url = (
                "vscode://file/" + (app.vault / "30-knowledge" / "x.md").resolve().as_posix()
            )
            assert app.opened.get(timeout=3) == opened_url
            command = _mutate_v2(ws, target, "session.close")
            assert calls.get(timeout=3) == "closed"
            assert recv(ws)["type"] == "session.closed"
            assert _receipt_v2(ws, command)["data"]["status"] == "applied"
        assert _tree_snapshot(app.vault) == before_vault
        assert _tree_snapshot(app.home) == before_home


# --- REST auth: _bearer_ok header forms ----------------------------------------------------------


def _request_with(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "query_string": b"", "method": "GET"})


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"authorization": f"Bearer {TOKEN}"}, True),
        ({}, False),  # no header at all
        ({"authorization": TOKEN}, False),  # bare token, no scheme
        ({"authorization": f"bearer {TOKEN}"}, False),  # scheme is case-sensitive
        ({"authorization": f"BEARER {TOKEN}"}, False),
        ({"authorization": f"Bearer  {TOKEN}"}, False),  # two spaces: not the exact form
        ({"authorization": f"Bearer{TOKEN}"}, False),  # no space at all
        ({"authorization": "Bearer nope"}, False),  # wrong token
        ({"authorization": f"Bearer {TOKEN}x"}, False),  # extra trailing character
        ({"authorization": f"Bearer {TOKEN[:-1]}"}, False),  # truncated
        ({"authorization": "Bearer "}, False),  # scheme with an empty token
    ],
)
def test_bearer_ok_accepts_only_the_exact_bearer_form(
    headers: dict[str, str], expected: bool
) -> None:
    assert server._bearer_ok(_request_with(headers), TOKEN) is expected


# --- a catalog exception on a REST route -----------------------------------------------------


class RaisingCatalog(EmptyCatalog):
    """The typed reads raise, to check that a catalog failure surfaces as the app's existing
    500 handler (_server_error) instead of hanging the request or crashing bare. An OSError the
    catalog did not turn into an observation is exactly such a failure."""

    def list_sessions(self, **_kwargs: Any) -> SessionsResult:
        raise OSError("disk fell over")

    def run_search(self, query: str, **_kwargs: Any) -> SearchResult:
        raise OSError("disk fell over")


def test_a_catalog_exception_on_a_rest_route_becomes_a_500(tmp_path: Path) -> None:
    home, vault = tmp_path / "home", tmp_path / "vault"
    # raise_server_exceptions=False so the response the app actually sent can be inspected,
    # instead of TestClient re-raising the exception ServerErrorMiddleware always re-raises.
    with running(
        tmp_path, catalog=RaisingCatalog(home, vault), raise_server_exceptions=False
    ) as app:
        for url in ("/api/sessions", "/api/search?q=x"):
            response = app.client.get(url, headers=BEARER)
            assert response.status_code == 500, url
            assert response.text == "Internal Server Error"
            # _server_error adds these itself: the exception is caught above SecurityMiddleware,
            # so nothing else would.
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["content-security-policy"] == server.CSP


# --- the 401 is decided before any catalog work ----------------------------------------------


class CountingCatalog(EmptyCatalog):
    """Counts the reads the two REST routes make: a request the server refuses — for its token
    or for its query parameters — must never reach them."""

    def __init__(self, home: Path, vault: Path) -> None:
        super().__init__(home, vault)
        self.calls = 0

    def list_sessions(self, **_kwargs: Any) -> SessionsResult:
        self.calls += 1
        return SessionsResult(sessions=[], state="ready")

    def run_search(self, query: str, **_kwargs: Any) -> SearchResult:
        self.calls += 1
        return SearchResult(hits=[], state="ready")


def test_an_unauthenticated_rest_request_never_reaches_the_catalog(tmp_path: Path) -> None:
    catalog = CountingCatalog(tmp_path / "home", tmp_path / "vault")
    refused = ({}, {"authorization": f"Bearer {TOKEN}x"}, {"authorization": f"bearer {TOKEN}"})
    with running(tmp_path, catalog=catalog) as app:
        for url in ("/api/sessions", "/api/search?q=x", "/api/search"):
            for headers in refused:
                assert app.client.get(url, headers=headers).status_code == 401, (url, headers)
        assert catalog.calls == 0
        assert app.client.get("/api/search?q=x", headers=BEARER).status_code == 200
        assert catalog.calls == 1


# --- the catalog routes carry the read's own observation ----------------------------


class ListingCatalog(EmptyCatalog):
    """list_sessions() answers with one configured observation, whatever it says."""

    def __init__(self, home: Path, vault: Path, listing: SessionsResult) -> None:
        super().__init__(home, vault)
        self.listing = listing

    def list_sessions(self, **_kwargs: Any) -> SessionsResult:
        return self.listing


def test_sessions_route_returns_the_listing_observation(tmp_path: Path) -> None:
    """The rows and the listing's own account of itself travel in one body: how many transcripts
    it could not read, and why. A listing that failed must not reach the browser as an ordinary
    empty list, which would paint an unknown count as zero."""
    row = SessionRow(
        session_id=NEW,
        title="Buy crème fraîche",
        first_prompt="Remember to buy crème fraîche",
        entrypoint="cli",
        modified="2026-09-11T08:00:00.000Z",
        cli_version="2.1.266",
        other=False,
    )
    listings = [
        SessionsResult(sessions=[row], state="ready"),
        SessionsResult(sessions=[row], state="partial", reason="time_budget", omitted=12),
        SessionsResult(sessions=[], state="error", reason="project_unreadable"),
    ]
    expected = [
        {"sessions": [asdict(row)], "state": "ready", "reason": "", "omitted": 0},
        {"sessions": [asdict(row)], "state": "partial", "reason": "time_budget", "omitted": 12},
        {"sessions": [], "state": "error", "reason": "project_unreadable", "omitted": 0},
    ]
    for index, (listing, body) in enumerate(zip(listings, expected, strict=True)):
        catalog = ListingCatalog(tmp_path / "home", tmp_path / "vault", listing)
        with running(tmp_path / str(index), catalog=catalog) as app:
            response = app.client.get("/api/sessions", headers=BEARER)
            assert response.status_code == 200, listing.state
            assert response.json() == body
            assert response.content.isascii()  # the one encoder, on the observed body too


def test_search_refuses_an_overlong_query_before_reading(tmp_path: Path) -> None:
    """A query is refused by length before the catalog opens a file: the palette's maxLength is
    a courtesy, and a pasted page would otherwise be carried through every transcript in the
    vault. The bound counts code points of the raw q, so an emoji costs one, not two."""
    catalog = CountingCatalog(tmp_path / "home", tmp_path / "vault")
    with running(tmp_path, catalog=catalog) as app:
        for query in ("a" * (QUERY_MAX_CHARS + 1), "🐈" * (QUERY_MAX_CHARS + 1)):
            response = app.client.get("/api/search", params={"q": query}, headers=BEARER)
            assert response.status_code == 400, len(query)
            assert response.json() == {"error": "q is too long"}
            assert query not in response.text  # the user's text never comes back in an error
        assert catalog.calls == 0  # refused before any read
        for query in ("a" * QUERY_MAX_CHARS, "🐈" * QUERY_MAX_CHARS):
            assert (
                app.client.get("/api/search", params={"q": query}, headers=BEARER).status_code
                == 200
            ), len(query)
        assert catalog.calls == 2  # the bound itself is admitted, surrogate pairs included


def test_search_echoes_a_canonical_request_id_and_refuses_others(tmp_path: Path) -> None:
    """The id the palette uses to tell its own answer from a late one is echoed back, and is
    admitted on the same terms as every identifier the socket carries: canonical form only."""
    catalog = CountingCatalog(tmp_path / "home", tmp_path / "vault")
    request_id = str(uuid.uuid4())
    with running(tmp_path, catalog=catalog) as app:
        response = app.client.get(
            "/api/search", params={"q": "milk", "request_id": request_id}, headers=BEARER
        )
        assert response.status_code == 200
        assert response.json()["request_id"] == request_id
        # No id at all, and the empty value a query string cannot tell from none: "".
        for params in ({"q": "milk"}, {"q": "milk", "request_id": ""}):
            body = app.client.get("/api/search", params=params, headers=BEARER).json()
            assert body["request_id"] == "", params
        assert catalog.calls == 3
        refused = (
            "not-a-uuid",
            request_id.upper(),  # canonical means lower case
            f"{{{request_id}}}",  # a braced id: uuid.UUID parses it, the wire never carries it
            request_id.replace("-", ""),
            f"{request_id} ",
        )
        for candidate in refused:
            response = app.client.get(
                "/api/search", params={"q": "milk", "request_id": candidate}, headers=BEARER
            )
            assert response.status_code == 400, candidate
            assert response.json() == {"error": "request_id is invalid"}
        assert catalog.calls == 3  # refused before any read


# --- the one-encoder invariant, also pinned for /api/search and /api/rails ---------------------


class SurrogateCatalog(EmptyCatalog):
    """run_search() returns a hit carrying a lone surrogate, the way a transcript cut mid-emoji
    would: proves the response body still goes through _dumps' ensure_ascii=True encoder rather
    than a hand-rolled JSONResponse that would raise UnicodeEncodeError on it."""

    def run_search(self, query: str, **_kwargs: Any) -> SearchResult:
        hit = SearchHit(
            session_id=NEW, title="milk \ud800 run", role="user", snippet="milk \ud800 run",
            at="2026-09-01T00:00:00Z",
        )  # fmt: skip
        return SearchResult(hits=[hit], state="ready", scanned=1, total=1)


def test_search_response_stays_ascii_with_a_lone_surrogate_in_a_hit(tmp_path: Path) -> None:
    home, vault = tmp_path / "home", tmp_path / "vault"
    with running(tmp_path, catalog=SurrogateCatalog(home, vault)) as app:
        response = app.client.get("/api/search?q=milk", headers=BEARER)
        assert response.status_code == 200
        assert response.content.isascii()  # the one encoder: a lone surrogate cannot kill it
        assert response.json()["hits"][0]["title"] == "milk \ud800 run"


def test_rails_response_stays_ascii_with_a_lone_surrogate_in_an_agent(tmp_path: Path) -> None:
    def fake_read(vault: Path, project: Path) -> Rails:
        return Rails(
            agents=[
                AgentInfo(name="a", description="milk \ud800 desc", tools=[], model="", last_run="")
            ],
            gates=[],
            routines=rails.routines(),
            counts=VaultCounts(knowledge=0, sources=0, inbox=0),
            git=GitChip(branch="", head="", committed_at="", dirty=0),
        )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(rails, "rails_read", fake_read)
        with running(tmp_path) as app:
            response = app.client.get("/api/rails", headers=BEARER)
            assert response.status_code == 200
            assert response.content.isascii()
            assert response.json()["agents"][0]["description"] == "milk \ud800 desc"


# --- turn.send receipts ---------------------------------------------------------------------------


def _until_receipt(ws: WebSocketTestSession, command_id: str) -> tuple[list[str], dict[str, Any]]:
    """The event types a turn.send produced before its receipt, and the receipt's data."""
    types: list[str] = []
    while (value := recv(ws))["type"] != "control.receipt":
        types.append(value["type"])
    assert value["command_id"] == command_id
    return types, value["data"]


def test_a_turn_refused_before_it_left_the_app_is_rejected_not_sent(tmp_path: Path) -> None:
    with running(tmp_path) as app, authed(app.client) as ws:
        _, target = _open_v2(ws)
        command = str(uuid.uuid4())
        # A browser's JSON.stringify escapes a lone surrogate, so it arrives as `\ud83d`.
        message = {
            "type": "turn.send",
            "session_id": target["session_id"],
            "generation": target["generation"],
            "command_id": command,
            "text": "cut \ud83d",
        }
        ws.send_text(json.dumps(message, ensure_ascii=True))
        types, data = _until_receipt(ws, command)
        assert data == {"status": "rejected", "reason": "not_sent"}
        assert "error" in types and "user.message" not in types


def test_a_turn_whose_pipe_failed_is_unknown(tmp_path: Path) -> None:
    class PipeFailureDriver(EchoDriver):
        async def send(self, text: str) -> None:
            raise CLIConnectionError("synthetic pipe failure")

    with (
        running(
            tmp_path, make_driver=lambda launch, prefs, emit: PipeFailureDriver(launch, emit)
        ) as app,
        authed(app.client) as ws,
    ):
        _, target = _open_v2(ws)
        command = _mutate_v2(ws, target, "turn.send", text="hi")
        _, data = _until_receipt(ws, command)
        assert data == {"status": "unknown", "reason": "native_unconfirmed"}


# --- a client whose seq outran its actor ---------------------------------------------------------


def _turn_of_three(ws: WebSocketTestSession) -> dict[str, Any]:
    """Open NEW and run one turn on it: session.started, user.message, turn.started — the actor
    is left at seq 3, and so is this socket's view of it."""
    snapshot, target = _open_v2(ws)
    assert snapshot["seq"] == 1
    command = _mutate_v2(ws, target, "turn.send", text="hi")
    assert [recv(ws)["seq"] for _ in range(2)] == [2, 3]
    assert _receipt_v2(ws, command)["data"]["status"] == "applied"
    return target


def test_a_client_whose_seq_outran_a_forgotten_actor_gets_a_new_generation_snapshot(
    tmp_path: Path,
) -> None:
    """A cursor from a forgotten actor belongs to its old generation. Recovery installs the
    new actor's snapshot without starting a provider merely to replace a stale browser view."""
    opens: queue.Queue[str] = queue.Queue()

    def make_driver(launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> Any:
        opens.put(launch.session_id)
        return EchoDriver(launch, emit)

    with running(tmp_path, make_driver=make_driver) as app:
        with authed(app.client) as other:
            old_target = _turn_of_three(other)
            # The other window closes the session. Awaiting manager.close on the app's own loop
            # is how this test reaches the state cmd_session_close leaves behind — the actor
            # forgotten — without racing its task; the command itself is pinned elsewhere.
            other.portal.call(app.manager.close, NEW)
            assert recv(other)["type"] == "session.closed"
        assert app.manager.get(NEW) is None
        assert opens.get(timeout=3) == NEW

        with authed(app.client) as returning:
            snapshot, target = _open_v2(returning, generation=old_target["generation"], after_seq=3)
            assert snapshot["seq"] == 0
            assert snapshot["data"]["state"]["info"] is None
            assert target["generation"] != old_target["generation"]
            replacement = app.manager.get(NEW)
            assert replacement is not None and not replacement.live
            assert replacement.generation == target["generation"]
        assert opens.empty()


def test_a_client_whose_seq_outran_a_replacement_actor_subscribes_to_its_generation(
    tmp_path: Path,
) -> None:
    """A replacement is already live at seq 1. Its snapshot and generation replace the old
    cursor, and recovering this window neither closes nor restarts the live actor."""
    with running(tmp_path) as app:
        with authed(app.client) as other:
            old_target = _turn_of_three(other)
            other.portal.call(app.manager.close, NEW)
            assert recv(other)["type"] == "session.closed"
            snapshot, _ = _open_v2(other)
            assert snapshot["seq"] == 1
            replacement = app.manager.get(NEW)
            assert replacement is not None and replacement.seq == 1

        with authed(app.client) as returning:
            snapshot, target = _open_v2(returning, generation=old_target["generation"], after_seq=3)
            assert snapshot["seq"] == 1
            assert target["generation"] == replacement.generation
            assert target["generation"] != old_target["generation"]
        assert app.manager.get(NEW) is replacement and replacement.live


# --- one vault scan at a time --------------------------------------------------------------------


class GatedSearchCatalog(EmptyCatalog):
    """run_search() blocks until `release` is set, records the queries that really scanned,
    counts how many are inside a scan at once, and records what `cancelled()` said while the
    scan was still running — the question a real scan asks between sessions."""

    def __init__(self, home: Path, vault: Path, release: threading.Event) -> None:
        super().__init__(home, vault)
        self._release = release
        self._lock = threading.Lock()
        self.entered = threading.Event()
        self.scanned: queue.Queue[str] = queue.Queue()
        self.cancelled_inside: dict[str, bool] = {}
        self.inside = 0
        self.peak = 0

    def run_search(
        self, query: str, *, cancelled: Callable[[], bool] | None = None, **_kwargs: Any
    ) -> SearchResult:
        with self._lock:
            self.inside += 1
            self.peak = max(self.peak, self.inside)
        self.scanned.put(query)
        self.entered.set()
        self._release.wait(timeout=3)
        self.cancelled_inside[query] = cancelled is not None and cancelled()
        with self._lock:
            self.inside -= 1
        hit = SearchHit(session_id=NEW, title=query, role="user", snippet=query, at="")
        return SearchResult(hits=[hit], state="ready", scanned=1, total=1)


def _search_request(q: str, request_id: str = "") -> Request:
    params = {"q": q} | ({"request_id": request_id} if request_id else {})
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/search",
            "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
            "query_string": urlencode(params).encode(),
        }
    )


def _search_route(app: App) -> Route:
    """The /api/search endpoint itself. TestClient answers one request at a time, so the pile-up
    these tests stage cannot be driven through it."""
    starlette_app = cast(Starlette, app.client.app)
    return next(r for r in starlette_app.routes if isinstance(r, Route) and r.path == "/api/search")


def test_searches_run_one_at_a_time_without_inferring_query_ownership(tmp_path: Path) -> None:
    """Search is a full-vault scan with no index by design, so one per keystroke, all of them in
    flight together, is what makes the palette unusable while a turn streams — and puts several
    copies of the biggest transcript in memory at once, where a scan is meant to keep peak
    memory near the largest file's size.

    Five independent requests in flight: the first holds the gate, the rest queue behind it.
    A shared prefix says nothing about which palette issued them, so each receives its own
    scan, coverage and correlation id. The palette drops its own stale replies by id and text.

    The route is called directly because TestClient answers one request at a time, so the
    pile-up this guards against cannot be staged through it."""
    release = threading.Event()
    catalog = GatedSearchCatalog(tmp_path / "home", tmp_path / "vault", release)
    queries = ("ka", "kan", "zzz", "kanj", "kanji")
    request_ids = [str(uuid.uuid4()) for _ in queries]
    with running(tmp_path, catalog=catalog) as app:
        route = _search_route(app)

        async def drive() -> list[Any]:
            tasks = [
                asyncio.create_task(route.endpoint(_search_request(q, request_id)))
                for q, request_id in zip(queries, request_ids, strict=True)
            ]
            try:
                await asyncio.to_thread(catalog.entered.wait, 3.0)
                # One explicit turn of the loop, so the ordering the assertions below rest on
                # is stated rather than inferred from how the wait above was scheduled: the
                # first query is inside the scan and the other four have run up to the gate.
                await asyncio.sleep(0)
                assert catalog.inside == 1
                assert catalog.scanned.get_nowait() == "ka"
                assert catalog.scanned.empty()
            finally:
                release.set()  # never leave the worker threads parked on a failed assertion
            return await asyncio.gather(*tasks)

        responses = asyncio.run(drive())

    assert [r.status_code for r in responses] == [200] * 5
    bodies = [json.loads(r.body) for r in responses]
    titles = [[hit["title"] for hit in body["hits"]] for body in bodies]
    assert titles == [[q] for q in queries]
    assert [
        (body["state"], body["reason"], body["scanned"], body["total"], body["request_id"])
        for body in bodies
    ] == [("ready", "", 1, 1, request_id) for request_id in request_ids]
    assert [catalog.scanned.get_nowait() for _ in range(4)] == list(queries[1:])
    assert catalog.scanned.empty()
    assert catalog.peak == 1  # never two whole-vault scans resident at once
    assert catalog.cancelled_inside == dict.fromkeys(queries, False)


class CancellingSearchCatalog(EmptyCatalog):
    """A scan that honours cancellation: it blocks until released, then reports what a real
    scan would — the sessions it got through, and `cancelled` as the reason it stopped."""

    def __init__(self, home: Path, vault: Path, release: threading.Event) -> None:
        super().__init__(home, vault)
        self._release = release
        self.entered = threading.Event()

    def run_search(
        self, query: str, *, cancelled: Callable[[], bool] | None = None, **_kwargs: Any
    ) -> SearchResult:
        self.entered.set()
        self._release.wait(timeout=3)
        hit = SearchHit(session_id=NEW, title=query, role="user", snippet=query, at="")
        if cancelled is not None and cancelled():
            return SearchResult(hits=[hit], state="partial", reason="cancelled", scanned=1, total=3)
        return SearchResult(hits=[hit], state="ready", scanned=3, total=3)


def test_an_independent_prefix_query_does_not_cancel_a_scan(tmp_path: Path) -> None:
    """Window A still wants cat when window B searches catalog. Even a catalog that honours a
    cancellation callback must answer both palettes with their hits, coverage and own ids."""
    release = threading.Event()
    catalog = CancellingSearchCatalog(tmp_path / "home", tmp_path / "vault", release)
    queries = ("cat", "catalog")
    request_ids = [str(uuid.uuid4()) for _ in queries]
    with running(tmp_path, catalog=catalog) as app:
        route = _search_route(app)

        async def drive() -> tuple[Any, Any]:
            scanning = asyncio.create_task(
                route.endpoint(_search_request(queries[0], request_ids[0]))
            )
            try:
                await asyncio.to_thread(catalog.entered.wait, 3.0)  # cat is inside the scan
                independent = asyncio.create_task(
                    route.endpoint(_search_request(queries[1], request_ids[1]))
                )
                # Let window B reach the gate before releasing window A's scan.
                await asyncio.sleep(0)
            finally:
                release.set()  # never leave the worker threads parked on a failed assertion
            return await asyncio.gather(scanning, independent)

        responses = asyncio.run(drive())

    assert [r.status_code for r in responses] == [200, 200]
    for response, q, request_id in zip(responses, queries, request_ids, strict=True):
        assert json.loads(response.body) == {
            "hits": [{"session_id": NEW, "title": q, "role": "user", "snippet": q, "at": ""}],
            "state": "ready",
            "reason": "",
            "scanned": 3,
            "total": 3,
            "request_id": request_id,
        }


# --- the route lists in P1_PATHS, registered only once every service is supplied -------------

P1_PATHS = frozenset(
    {
        "/api/v2/settings",
        "/api/v2/capabilities",
        "/api/v2/diagnostics",
        "/api/v2/diagnostics/checks",
        "/api/v2/workspaces",
        "/api/v2/workspaces/select",
        "/api/v2/workspaces/{workspace_id}",
        "/api/v2/vaults/suggestion",
        "/api/v2/vaults/create",
        "/api/v2/vaults/open",
        "/api/v2/vaults/{workspace_id}/cancel-bootstrap",
        "/api/v2/vaults/{workspace_id}/policy",
        "/api/v2/data/locations",
        "/api/v2/data/reset",
        "/api/v2/exports",
        "/api/v2/exports/preview",
        "/api/v2/imports/preview",
        "/api/v2/imports/{preview_id}/apply",
    }
)


def _p1_app(
    tmp_path: Path,
    *,
    owner: str | None = P1_OWNER,
    cli_version: str = "",
    prepare: Callable[[WorkspaceService], None] = lambda _spaces: None,
    make_driver: Callable[..., Any] | None = None,
    agent_cli: Path | None = None,
) -> Starlette:
    """create_app with every optional service wired (settings, workspaces, bootstrap, readiness,
    checks, diagnostics, data), the way the launcher wires them.

    The adapters come from the launcher itself rather than being restated here: they are what
    joins the service modules together, and a test that built its own copies would pass while the
    real ones were wrong. `prepare` runs on the workspaces before the active one is resolved,
    which is when a registration still decides it.
    """
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
    prepare(spaces)
    settings = SettingsService(
        state.store, provider_defaults=Prefs, on_chat_defaults=lambda _prefs: None
    )
    readiness = Readiness(config=config, launch_id="launch-1", git_available=lambda: False)
    if cli_version:
        readiness.observe_version(cli_version)
    log_file = data / "logs" / "viki-agent.log"
    document = launcher._diagnostics_document(
        readiness, config, settings, spaces, state.store, log_file
    )
    return server.create_app(
        config=config,
        token=TOKEN,
        manager=manager,
        catalog=EmptyCatalog(home, vault),
        static_dir=None,
        settings=settings,
        workspaces=spaces,
        agent_cli=agent_cli,
        bootstrap=VaultBootstrap(
            spaces, resources=tmp_path / "resources", home=tmp_path / "profile"
        ),
        readiness=readiness,
        checks=ProviderChecks(readiness=readiness, cli_version=lambda: "", git_version=lambda: ""),
        diagnostics_document=document,
        data=launcher._data_services(
            config,
            settings,
            spaces,
            spaces.resolve_active(),
            store=state.store,
            document=document,
            log_file=log_file,
            launch_id="launch-1",
            scratch_in_use=lambda: False,
            owner=P1_OWNER,
            denied_roots=launcher._denied_roots(config, spaces, spaces.resolve_active()),
        ),
        owner=owner,
    )


def test_a_note_save_waits_for_the_vault_lease_as_long_as_every_other_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vault clients give up at 15 s; one server deadline keeps every vault writer's lease
    wait below that, the note save's included."""
    seen: list[dict[str, Any]] = []
    real = server.vault_write_routes

    def recording(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(server, "vault_write_routes", recording)
    _p1_app(tmp_path)
    assert [kwargs.get("lease_wait_s") for kwargs in seen] == [server.VAULT_LEASE_WAIT_S]


def _v2_routes(app: Starlette) -> list[tuple[str, str]]:
    return sorted(
        (route.path, method)
        for route in app.routes
        if isinstance(route, Route) and route.path.startswith("/api/v2/")
        for method in sorted(route.methods or set())
        if method not in ("HEAD", "OPTIONS")
    )


def test_a_p1_route_list_is_registered_only_when_its_service_is_supplied(tmp_path: Path) -> None:
    """Every service parameter of create_app is keyword-only and defaults to None: the frozen
    acceptance fixture passes none of them and must still get exactly the v1 app it always got."""
    with running(tmp_path) as app:
        bare = {path for path, _ in _v2_routes(cast(Starlette, app.client.app))}
    assert bare & P1_PATHS == set()
    assert bare, "the v1 v2-routes (drafts, commands, approvals) must still be registered"
    wired = {path for path, _ in _v2_routes(_p1_app(tmp_path / "p1"))}
    assert wired >= P1_PATHS
    assert bare <= wired  # nothing the v1 app had is dropped when the services arrive


def test_the_bookmark_and_history_routes_are_registered_only_with_workspaces(
    tmp_path: Path,
) -> None:
    vault_paths = {
        "/api/v2/vaults/{workspace_id}/bookmarks",
        "/api/v2/vaults/{workspace_id}/bookmarks/{ref:path}",
        "/api/v2/vaults/{workspace_id}/history/{history_ref:path}",
    }
    with running(tmp_path) as app:
        bare = {path for path, _ in _v2_routes(cast(Starlette, app.client.app))}
    assert bare & vault_paths == set()
    wired_app = _p1_app(tmp_path / "p1")
    assert {path for path, _ in _v2_routes(wired_app)} >= vault_paths
    with TestClient(wired_app, base_url=BASE) as client:
        for tail in ("bookmarks", "history/notes/a.md"):
            response = client.get(f"/api/v2/vaults/{NEW}/{tail}", headers=BEARER)
            assert response.status_code == 404, tail
            assert response.json()["error"]["code"] == "unknown_workspace"


DECISION_PATHS = {
    "/api/v2/decisions",
    "/api/v2/decisions/proposals/{proposal_id}",
    "/api/v2/vaults/{workspace_id}/proposals",
}
# These two routes were removed with the retro-decision surface.
RETRO_DECISION_PATHS = {
    "/api/v2/decisions/retro",
    "/api/v2/decisions/retro/{proposal_id}",
}
RETRO_NOTE = """# Retro report

## Gate summary

| # | Item | Conf. | Owner action |
|---|---|---|---|
| M1 | The marked item | high | approve/reject |
"""
NOTE_TEXT = "alpha\nbeta\n"
POST = {**BEARER, "origin": ORIGIN}


def _admit_protected_vault(root: Path, spaces: WorkspaceService) -> str:
    """Register `root` with a retro report and one note, protected, and select it."""
    (root / "system" / "retro").mkdir(parents=True)
    (root / "system" / "retro" / "2026-10-01.md").write_text(RETRO_NOTE, encoding="utf-8")
    (root / "a.md").write_bytes(NOTE_TEXT.encode())
    workspace, _ = spaces.register_existing(str(uuid.uuid4()), str(root))
    spaces.set_policy(str(uuid.uuid4()), workspace.workspace_id, 1, "protected", [])
    spaces.select(str(uuid.uuid4()), workspace.workspace_id)
    return workspace.workspace_id


def _protected_vault_app(tmp_path: Path, **kwargs: Any) -> tuple[Starlette, str]:
    """The wired app whose active vault is a protected one; the second item is its id."""
    ids: list[str] = []
    app = _p1_app(
        tmp_path,
        prepare=lambda spaces: ids.append(_admit_protected_vault(tmp_path / "notes", spaces)),
        **kwargs,
    )
    return app, ids[0]


def _propose(client: TestClient, workspace_id: str) -> str:
    created = client.post(
        f"/api/v2/vaults/{workspace_id}/proposals",
        headers=POST,
        json={
            "ref": "a.md",
            "text": "gamma\n",
            "base_revision": hashlib.sha256(NOTE_TEXT.encode()).hexdigest(),
        },
    )
    assert created.status_code == 201
    return created.json()["proposal"]["id"]


def test_the_decision_and_proposal_routes_are_registered_only_with_workspaces(
    tmp_path: Path,
) -> None:
    with running(tmp_path) as app:
        bare = {path for path, _ in _v2_routes(cast(Starlette, app.client.app))}
    assert bare & DECISION_PATHS == set()
    wired = {path for path, _ in _v2_routes(_p1_app(tmp_path / "p1"))}
    assert wired >= DECISION_PATHS
    assert wired & RETRO_DECISION_PATHS == set()


def test_the_decision_and_proposal_routes_answer_through_the_app(tmp_path: Path) -> None:
    app, workspace_id = _protected_vault_app(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        proposal_id = _propose(client, workspace_id)
        inbox = client.get("/api/v2/decisions", headers=BEARER)
        proposal_detail = client.get(f"/api/v2/decisions/proposals/{proposal_id}", headers=BEARER)
        withdrawn = client.post(
            f"/api/v2/decisions/proposals/{proposal_id}",
            headers=POST,
            json={"action": "withdraw"},
        )

    assert inbox.status_code == 200
    assert proposal_detail.status_code == 200
    assert proposal_detail.json()["proposal"]["proposed_text"] == "gamma\n"
    assert withdrawn.status_code == 200 and withdrawn.json() == {"withdrawn": True}


def test_the_inbox_carries_only_the_proposal_items(tmp_path: Path) -> None:
    """The retro routes are gone, so the pending Gate summary row that `_admit_protected_vault`
    writes under `system/retro` never reaches the inbox — only the vault/skill proposals do."""
    app, workspace_id = _protected_vault_app(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        proposal_id = _propose(client, workspace_id)
        inbox = client.get("/api/v2/decisions", headers=BEARER)

    body = inbox.json()
    assert inbox.status_code == 200
    assert set(body) == {"items"}
    assert [item["kind"] for item in body["items"]] == ["vault_proposal"]
    assert body["items"][0]["id"] == proposal_id


async def test_the_excerpt_reader_is_bound_to_the_active_vault(tmp_path: Path) -> None:
    """Without `bind_excerpts` the turn below is refused `excerpt_unavailable` and nothing is
    sent; bound, the note is re-read from the active vault and joined behind the question."""
    sent: list[str] = []

    class Recorder(EchoDriver):
        async def send(self, text: str) -> None:
            sent.append(text)

    app, workspace_id = _protected_vault_app(
        tmp_path, make_driver=lambda launch, _prefs, emit: Recorder(launch, emit)
    )
    span = "alpha\nbeta"
    part = ExcerptPart(workspace_id, "a.md", 1, 2, hashlib.sha256(span.encode()).hexdigest())
    turn = TurnInput("Summarize this", (), (part,))

    async def accept() -> TurnInput:
        return turn

    async def intent() -> None:
        return None

    actor = app.state.commands.manager.open(NEW)
    await actor.send_command(
        expected_generation=actor.generation, accept=accept, dispatch_intent=intent
    )

    assert sent == ["Summarize this\n" + render_excerpt_block("a.md", 1, 2, span)]
    await actor.close()


def test_both_ledgers_share_the_launch_owner_create_app_was_given(tmp_path: Path) -> None:
    """The two independently minted UUIDs collapse into one, so the reset service can be handed
    the same fact the ledgers compare their rows against."""
    app = _p1_app(tmp_path)
    assert app.state.commands.service._owner == P1_OWNER
    assert app.state.approvals.service._owner == P1_OWNER


def test_create_app_refuses_data_routes_without_a_launch_owner(tmp_path: Path) -> None:
    """The data routes carry the reset, and a ResetService whose owner matches no row deletes
    exactly the in-flight rows the narrowing exists to keep — and reports them as deleted. A
    minted fallback would be silent, so the app must not be built at all."""
    with pytest.raises(ValueError) as failure:
        _p1_app(tmp_path / "unowned", owner=None)
    assert "owner" in str(failure.value)
    # With neither, the default stands and nothing can reach a reset: this is the shape the
    # frozen acceptance fixture builds.
    with running(tmp_path / "bare") as app:
        assert app.client is not None


def test_create_app_refuses_an_owner_that_disagrees_with_its_reset_service(
    tmp_path: Path,
) -> None:
    """A ResetService built for P1_OWNER, handed to create_app under a different owner, would
    stamp the two ledgers with one fact and narrow the reset on another: the DELETE that is
    supposed to spare every row this launch just wrote would take them instead, and report them
    deleted. The mismatch must refuse the same way the missing-owner shape does."""
    with pytest.raises(ValueError) as failure:
        _p1_app(tmp_path / "mismatched", owner=MISMATCHED_OWNER)
    assert "owner" in str(failure.value)


def test_every_v2_route_requires_host_origin_and_bearer(tmp_path: Path) -> None:
    """Hard rule 7 across the assembled app rather than one module at a time: no /api/v2 route
    is reachable without the launch token or from a foreign Host, and no mutation is reachable
    from a foreign Origin."""
    app = _p1_app(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        checked = 0
        for path, method in _v2_routes(app):
            # Every path parameter this app has is an id; a canonical uuid is valid for all of
            # them, and none of these requests is meant to get past authentication anyway.
            url = re.sub(r"\{[^}]*\}", NEW, path)
            no_token = client.request(method, url, headers={"origin": ORIGIN})
            assert no_token.status_code == 401, (method, url)
            foreign_host = client.request(
                method, url, headers={**BEARER, "origin": ORIGIN, "host": "evil.example:8765"}
            )
            assert foreign_host.status_code == 400, (method, url)
            if method != "GET":
                foreign_origin = client.request(
                    method, url, headers={**BEARER, "origin": "https://evil.example"}
                )
                assert foreign_origin.status_code == 403, (method, url)
            checked += 1
        assert checked >= len(P1_PATHS)


def test_attachment_capabilities_carry_the_observed_cli_image_input_status(
    tmp_path: Path,
) -> None:
    """The composer learns from the server whether native image input is proven for the Claude
    CLI this launch observed; without readiness the version is unknown. Native image input is
    supported on the proven CLI version, and unverified on every other observed version."""
    app = _p1_app(tmp_path, cli_version=FIXTURE_VERSION)
    with TestClient(app, base_url=BASE) as client:
        body = client.get("/api/v2/attachment-capabilities", headers=BEARER).json()
    assert body["image_input"] == {"status": "supported", "reason": None}
    with running(tmp_path / "bare") as bare:
        body = bare.client.get("/api/v2/attachment-capabilities", headers=BEARER).json()
    assert body["image_input"] == {"status": "unverified", "reason": "evidence_unavailable"}


def test_attachment_capabilities_survive_a_writer_holding_the_store(tmp_path: Path) -> None:
    """A committing writer's BEGIN EXCLUSIVE must not turn attachment-capabilities into a 500.
    The observed CLI version is already held by readiness, so the route needs no store read to
    answer image_input."""
    app = _p1_app(tmp_path, cli_version=FIXTURE_VERSION)
    writer = sqlite3.connect(tmp_path / "data" / "app.sqlite3")
    writer.execute("BEGIN EXCLUSIVE")
    try:
        with TestClient(app, base_url=BASE) as client:
            response = client.get("/api/v2/attachment-capabilities", headers=BEARER)
        assert response.status_code == 200
        assert response.json()["image_input"] == {"status": "supported", "reason": None}
    finally:
        writer.rollback()
        writer.close()


def test_capabilities_reads_the_selection_fresh_like_the_workspaces_listing(
    tmp_path: Path,
) -> None:
    """The launch keeps the workspace it resolved, but a selection made in this window has to
    show on both routes at once: the listing already read it fresh, while capabilities kept
    reporting the launch-time selection and `restart_required: false` for the same store."""
    app = _p1_app(tmp_path)
    with TestClient(app, base_url=BASE) as client:
        opened = client.post(
            "/api/v2/vaults/open",
            headers={**BEARER, "origin": ORIGIN},
            json={"command_id": NEW, "path": str(tmp_path / "vault")},
        )
        assert opened.status_code == 201, opened.text
        workspace_id = opened.json()["workspace"]["workspace_id"]
        chosen = client.post(
            "/api/v2/workspaces/select",
            headers={**BEARER, "origin": ORIGIN},
            json={"command_id": NEW, "workspace_id": workspace_id},
        )
        assert chosen.status_code == 200, chosen.text
        listing = client.get("/api/v2/workspaces", headers=BEARER).json()
        workspace = client.get("/api/v2/capabilities", headers=BEARER).json()["workspace"]
        assert workspace["selected_id"] == listing["selected_id"] == workspace_id
        assert workspace["active"]["id"] == listing["active_id"]
        assert workspace["restart_required"] is True


def test_the_recovery_shell_reports_the_refusal_that_opened_it(tmp_path: Path) -> None:
    """`recovery_required` is one StateError, the hot rollback journal with its own procedure
    in the troubleshooting guide. A corrupt or newer store is refused with its own code, and a
    shell that called every refusal a recovery would send the user down the wrong page."""
    data = tmp_path / "data"
    config = Config(vault=None, claude_home=tmp_path / "home", cli=None, edge=None, data_dir=data)
    readiness = Readiness(config=config, launch_id="launch-1", git_available=lambda: False)
    app = server.create_recovery_app(
        config=config,
        token=TOKEN,
        readiness=readiness,
        checks=ProviderChecks(readiness=readiness, cli_version=lambda: "", git_version=lambda: ""),
        diagnostics_document=lambda: {},
        static_dir=None,
        storage_refusal="corrupt",
    )
    with TestClient(app, base_url=BASE) as client:
        body = client.get("/api/v2/capabilities", headers=BEARER).json()
    assert body["app"] == {"mode": "recovery"}
    assert body["storage"] == {"state": "unavailable", "reason": "corrupt", "private": None}


def test_the_recovery_shell_serves_readiness_and_nothing_else(tmp_path: Path) -> None:
    """The recovery shell for a store that could not be opened. It is the app's entire surface in
    that state, so what is missing matters as much as what is there: no session socket, no
    settings, no workspaces, nothing that could write to the store that just failed. The
    storage state it reports is what tells the shell to say the store is unreadable rather than
    empty, and it has to come out of the app rather than out of the caller's own Readiness."""
    data = tmp_path / "data"
    config = Config(vault=None, claude_home=tmp_path / "home", cli=None, edge=None, data_dir=data)
    readiness = Readiness(config=config, launch_id="launch-1", git_available=lambda: False)
    app = server.create_recovery_app(
        config=config,
        token=TOKEN,
        readiness=readiness,
        checks=ProviderChecks(readiness=readiness, cli_version=lambda: "", git_version=lambda: ""),
        diagnostics_document=launcher._diagnostics_document(
            readiness, config, None, None, None, data / "logs" / "viki-agent.log"
        ),
        static_dir=None,
    )

    assert sorted(route.path for route in app.routes if isinstance(route, Route)) == [
        "/api/v2/capabilities",
        "/api/v2/diagnostics",
        "/api/v2/diagnostics/checks",
    ]
    # No WebSocketRoute and no Mount: with static_dir None these three are the whole app.
    assert [route for route in app.routes if not isinstance(route, Route)] == []

    with TestClient(app, base_url=BASE) as client:
        capabilities = client.get("/api/v2/capabilities", headers=BEARER)
        assert capabilities.status_code == 200
        body = capabilities.json()
        assert body["app"] == {"mode": "recovery"}
        assert body["storage"] == {
            "state": "unavailable",
            "reason": "recovery_required",
            "private": None,
        }
        assert client.get("/api/v2/diagnostics", headers=BEARER).status_code == 200
        # Hard rule 7 holds here too: the shell is served on the same loopback port as the app.
        assert client.get("/api/v2/capabilities").status_code == 401
        assert client.get("/api/v2/diagnostics").status_code == 401
        assert (
            client.get(
                "/api/v2/capabilities", headers={**BEARER, "host": "evil.example:8765"}
            ).status_code
            == 400
        )
        assert client.get("/api/v2/settings", headers=BEARER).status_code == 404
        assert client.get("/ws", headers=BEARER).status_code == 404
        face = client.get("/api/face/64")
        assert face.status_code == 404
        assert face.headers["content-security-policy"] == server.CSP
        assert face.headers["x-content-type-options"] == "nosniff"
        assert face.headers["referrer-policy"] == "no-referrer"


def test_the_vault_hub_resends_a_live_list_that_changed_back_after_a_connect_snapshot() -> None:
    live: list[str] = []
    hub = server._VaultHub(lambda: list(live))
    sent: list[dict[str, Any]] = []
    hub.subscribe(sent.append)
    hub.update([])  # idle: nothing changed, nothing is sent
    assert sent == []
    x = "session-x"
    live.append(x)
    hub.snapshot()  # a page connects and sees live == [x]; nothing is broadcast
    live.clear()
    hub.update([])  # live changed back to []: one broadcast
    assert [e["data"]["live"] for e in sent] == [[]]
    hub.update([])  # nothing changed since: no second broadcast
    assert len(sent) == 1


@pytest.mark.parametrize("counts_state", ["ready", "error", "unknown"])
def test_rails_wire_reports_explicit_count_state_but_preserves_legacy_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    counts_state: Literal["ready", "error", "unknown"],
) -> None:
    agent = AgentInfo(name="Café author", description="cut \ud800", tools=[], model="", last_run="")
    value = Rails(
        agents=[agent],
        gates=[],
        routines=rails.routines(),
        counts=VaultCounts(knowledge=11, sources=23, inbox=7),
        git=GitChip(branch="main", head="abc123", committed_at="", dirty=0),
    )
    if counts_state != "unknown":
        value = replace(value, counts_state=counts_state)
    seen: list[tuple[Path, int]] = []

    def read(vault: Path, project: Path) -> Rails:
        seen.append((vault, threading.get_ident()))
        return value

    monkeypatch.setattr(rails, "rails_read", read)
    with running(tmp_path) as app:
        assert app.client.get("/api/rails").status_code == 401
        assert (
            app.client.get("/api/rails", headers={"authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert seen == []
        response = app.client.get("/api/rails", headers=BEARER)
        assert response.status_code == 200 and response.content.isascii()
        expected: dict[str, Any] = {
            "agents": [asdict(agent)],
            "gates": [],
            "routines": [asdict(row) for row in rails.routines()],
            "counts": {"knowledge": 11, "sources": 23, "inbox": 7},
            "git": asdict(value.git),
        }
        if counts_state != "unknown":
            expected["counts_state"] = counts_state
        assert response.json() == expected
        assert value.counts_state == counts_state
        assert len(seen) == 1 and seen[0][0] == app.vault
        assert app.client.portal is not None
        assert seen[0][1] != app.client.portal.call(threading.get_ident)


@pytest.mark.parametrize("fails", [False, True], ids=["count-returned", "count-raised"])
def test_rails_route_reports_the_actual_count_call_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    original = rails.counts
    seen: list[Path] = []

    def observed(root: Path) -> VaultCounts:
        seen.append(root)
        if fails:
            raise OSError("synthetic counting failure")
        return original(root)

    monkeypatch.setattr(rails, "counts", observed)
    monkeypatch.setattr(
        rails,
        "git_chip",
        lambda *_args, **_kwargs: GitChip(branch="", head="", committed_at="", dirty=0),
    )
    with running(tmp_path) as app:
        response = app.client.get("/api/rails", headers=BEARER)
        assert response.status_code == 200
        assert seen == [app.vault]
        assert response.json()["counts_state"] == ("error" if fails else "ready")
        assert response.json()["counts"] == {
            "knowledge": 0 if fails else 1,
            "sources": 0,
            "inbox": 0,
        }
