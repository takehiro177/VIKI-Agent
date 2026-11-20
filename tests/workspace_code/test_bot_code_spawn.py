"""The final Bot-copy spawn seam uses the real manager, actor and synthetic drivers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from tests.conversations.test_session import HANG_GUARD_S, watch
from tests.test_server import EchoDriver
from tests.workspace_code.test_session_roots_spawn import _manager
from viki_agent import events
from viki_agent.drivers.base import StartError, StartFacts
from viki_agent.drivers.claude.control import Launch
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.session import SessionActor, SessionManager
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

from replay import SID

OTHER = "22222222-2222-4222-8222-222222222222"
COPY_ID = "33333333-3333-4333-8333-333333333333"


class Startup:
    def __init__(self, tmp_path: Path) -> None:
        self.order: list[str] = []
        self.launches: list[Launch] = []
        self.preferences: list[Prefs] = []
        self.sent: list[str] = []
        self.settings = str(tmp_path / "bot-memory-settings" / ("a" * 64 + ".json"))
        self.copy_path = str(tmp_path / "project-worktrees" / COPY_ID)
        self.memory: str | None = self.settings
        self.root: Path | None = None
        self.open_gate: asyncio.Event | None = None
        self.opened = asyncio.Event()
        self.state = AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low"))
        startup = self

        class Driver(EchoDriver):
            async def open(self) -> events.SessionStarted:
                startup.order.append("open")
                startup.opened.set()
                if startup.open_gate is not None:
                    await startup.open_gate.wait()
                return await super().open()

            async def send(self, text: str) -> None:
                startup.sent.append(text)

        def factory(
            launch: Launch, prefs: Prefs, emit: Callable[[events.Event], None]
        ) -> EchoDriver:
            self.order.append("factory")
            self.launches.append(launch)
            self.preferences.append(prefs)
            return Driver(launch, emit)

        self.manager = SessionManager(
            config=DriverConfig(cli=("claude.exe",), vault=tmp_path / "vault", env={}),
            state=self.state,
            claude_home=tmp_path / "synthetic-home",
            make_driver=factory,
        )
        self.manager.bind_profiles(self.guard, self.catalog, initialized=self.initialized)
        self.manager.bind_roots(self.roots)
        self.manager.bind_mcp(self.mcp)
        self.manager.bind_bot_memory(self.resolve_memory)

    async def roots(self, _session_id: str) -> Path | None:
        self.order.append("root")
        return self.root

    async def guard(self, _session_id: str, _prefs: Prefs) -> None:
        self.order.append("guard")

    async def catalog(self, _started: events.SessionStarted) -> None:
        self.order.append("catalog")

    async def initialized(self, _session_id: str, _facts: StartFacts | None) -> None:
        self.order.append("initialized")

    async def mcp(self, _session_id: str) -> str:
        self.order.append("mcp")
        return str(Path(self.settings).parent.parent / "mcp" / "config.json")

    async def resolve_memory(self, _session_id: str) -> str | None:
        self.order.append("memory")
        return self.memory

    def copy(self) -> events.CodeCopy:
        return events.CodeCopy(path=self.copy_path, folder="project", branch="bot/synthetic")

    def actor(self, session_id: str = SID) -> SessionActor:
        self.state.remember(session_id, mode="auto", effort="low")
        return self.manager.attach_prepared(session_id, profile=True)


async def test_copy_is_last_and_full_named_wire_survives_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    startup = Startup(tmp_path)
    actor = startup.actor()
    expected = startup.copy()
    read_prefs, admit = startup.state.launch_prefs, actor._admit

    def prefs(session_id: str) -> Prefs:
        startup.order.append("prefs")
        return read_prefs(session_id)

    def admitted(candidate: SessionActor) -> bool:
        startup.order.append("admit")
        return admit(candidate)

    monkeypatch.setattr(startup.state, "launch_prefs", prefs)
    monkeypatch.setattr(actor, "_admit", admitted)

    async def resolve(session_id: str, settings_file: str) -> events.CodeCopy:
        assert session_id == SID and settings_file == startup.settings
        assert not actor.live
        startup.order.append("copy")
        asyncio.get_running_loop().call_soon(startup.order.append, "next-loop")
        return expected

    # Binding after actor attachment has the same behavior as binding before attachment.
    startup.manager.bind_bot_code(resolve)
    seen = watch(actor)
    try:
        await actor.send("synthetic prompt")
        assert startup.order[:12] == [
            "prefs",
            "root",
            "guard",
            "mcp",
            "memory",
            "admit",
            "copy",
            "prefs",
            "admit",
            "factory",
            "open",
            "initialized",
        ]
        if "next-loop" in startup.order:
            assert startup.order.index("factory") < startup.order.index("next-loop")
        launch = startup.launches[0]
        assert launch.code_worktree == expected.path
        assert launch.bot_memory_settings == startup.settings
        assert launch.add_dirs == () and launch.cwd is None
        assert actor.started is not None and actor.started.code_copy is expected
        wire = next(event["data"] for event in seen if event["type"] == "session.started")
        assert wire["code_copy"] == {
            "path": expected.path,
            "folder": "project",
            "branch": "bot/synthetic",
        }
        assert wire["additional_folders"] == []
        assert "catalog" not in startup.order
        assert startup.sent == ["synthetic prompt"]
    finally:
        await startup.manager.close_all()


def test_code_copy_is_frozen_and_absent_wire_is_unchanged() -> None:
    copy = events.CodeCopy(path="copy", folder="project", branch="branch")
    with pytest.raises(FrozenInstanceError):
        copy.path = "other"  # pyright: ignore[reportAttributeAccessIssue]
    started = events.SessionStarted(
        model="default",
        mode="auto",
        effort="low",
        commands=[],
        agents=[],
        models=[],
        cli_version="1.0.0",
    )
    assert started.data() == {
        "model": "default",
        "mode": "auto",
        "effort": "low",
        "commands": [],
        "agents": [],
        "models": [],
        "cli_version": "1.0.0",
        "resumed": False,
        "additional_folders": [],
    }
    assert replace(started, code_copy=copy).data()["code_copy"] == {
        "path": "copy",
        "folder": "project",
        "branch": "branch",
    }


@pytest.mark.parametrize(
    "error", [StartError("bad_request", "copy unavailable"), StateError("corrupt")]
)
async def test_copy_refusal_reaches_subscriber_without_driver(
    tmp_path: Path, error: StartError | StateError
) -> None:
    startup = Startup(tmp_path)

    async def resolve(_session_id: str, _settings_file: str) -> events.CodeCopy:
        raise error

    startup.manager.bind_bot_code(resolve)
    actor = startup.actor()
    seen = watch(actor)
    await actor.send("synthetic prompt")
    assert not actor.live and not startup.launches and not startup.sent
    refusal = next(event["data"] for event in seen if event["type"] == "error")
    assert refusal == {
        "code": "bad_request" if isinstance(error, StartError) else "internal",
        "message": error.message,
        "recoverable": True,
    }
    await startup.manager.close_all()


@pytest.mark.parametrize("checkpoint", ["guard", "mcp", "memory", "admission"])
async def test_existing_preflight_refusal_does_not_create_copy(
    tmp_path: Path, checkpoint: str
) -> None:
    startup = Startup(tmp_path)
    calls: list[str] = []

    async def resolve(session_id: str, _settings_file: str) -> events.CodeCopy:
        calls.append(session_id)
        return startup.copy()

    async def guard(_session_id: str, _prefs: Prefs) -> None:
        raise StartError("bad_request", "preflight refused")

    async def settings(_session_id: str) -> str | None:
        raise StartError("bad_request", "preflight refused")

    startup.manager.bind_bot_code(resolve)
    if checkpoint == "guard":
        startup.manager.bind_profiles(guard, startup.catalog, initialized=startup.initialized)
    elif checkpoint == "mcp":
        startup.manager.bind_mcp(settings)
    elif checkpoint == "memory":
        startup.manager.bind_bot_memory(settings)
    else:
        startup.manager.bind_max_live(lambda: 3)
        for i in range(3):
            assert await startup.manager.create(f"{i:08d}-0000-4000-8000-000000000000").start()
    actor = startup.actor()
    seen = watch(actor)
    try:
        assert await actor.send("synthetic prompt") is False
        assert calls == [] and not actor.live and not startup.sent
        assert any(event["type"] == "error" for event in seen)
    finally:
        await startup.manager.close_all()


@pytest.mark.parametrize("conflict", ["live", "blocked"])
async def test_final_copy_check_and_driver_live_are_one_loop_turn(
    tmp_path: Path, conflict: str
) -> None:
    startup = Startup(tmp_path)
    startup.open_gate = asyncio.Event()
    first, second = startup.actor(), startup.actor(OTHER)
    created, release = asyncio.Event(), asyncio.Event()
    blocked = False
    calls = 0

    async def resolve(_session_id: str, _settings_file: str) -> events.CodeCopy:
        nonlocal calls
        calls += 1
        if calls == 2:
            created.set()
        await release.wait()
        # This is the resolver's final on-loop check after asynchronous creation.
        if blocked or startup.manager.live():
            raise StartError("bad_request", "copy unavailable")
        return startup.copy()

    startup.manager.bind_bot_code(resolve)
    seen = watch(second)
    one, two = asyncio.create_task(first.send("first")), asyncio.create_task(second.send("second"))
    try:
        await asyncio.wait_for(created.wait(), HANG_GUARD_S)
        blocked = conflict == "blocked"
        release.set()
        if conflict == "live":
            await asyncio.wait_for(startup.opened.wait(), HANG_GUARD_S)
            assert first.live and len(startup.manager.live()) == 1
        startup.open_gate.set()
        await asyncio.gather(one, two)
        assert len(startup.launches) == (1 if conflict == "live" else 0)
        assert startup.sent == (["first"] if conflict == "live" else [])
        assert any(e["type"] == "error" for e in seen)
        assert not second.live
    finally:
        release.set()
        startup.open_gate.set()
        await asyncio.gather(one, two, return_exceptions=True)
        await startup.manager.close_all()


@pytest.mark.parametrize(
    "change",
    [
        "interrupt",
        "cancel",
        "configuration",
        "stored_configuration",
        "same_configuration",
        "unreadable",
    ],
)
async def test_creation_await_rechecks_cancellation_and_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    startup = Startup(tmp_path)
    actor = startup.actor()
    created, release = asyncio.Event(), asyncio.Event()

    async def resolve(_session_id: str, _settings_file: str) -> events.CodeCopy:
        created.set()
        await release.wait()
        return startup.copy()

    startup.manager.bind_bot_code(resolve)
    task = asyncio.create_task(actor.send("synthetic prompt"))
    try:
        await asyncio.wait_for(created.wait(), HANG_GUARD_S)
        if change == "interrupt":
            await actor.interrupt()
        elif change == "cancel":
            task.cancel()
        else:
            # Profile controls cannot edit prefs; inject committed-state drift at the existing
            # revision seam to prove the post-await check refuses changed launch preferences.
            if change != "same_configuration":
                startup.state.remember(SID, effort="high")
            if change != "stored_configuration":
                actor._configuration_revision += 1
            if change == "unreadable":

                def unreadable(_session_id: str) -> Prefs:
                    raise StateError("corrupt")

                monkeypatch.setattr(startup.state, "launch_prefs", unreadable)
        release.set()
        if change == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        assert len(startup.launches) == int(change == "same_configuration")
        if change == "same_configuration":
            assert startup.preferences[0].effort == "low"
        else:
            assert not actor.live and not startup.sent
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await startup.manager.close_all()


async def test_admission_is_rechecked_after_creation(tmp_path: Path) -> None:
    startup = Startup(tmp_path)
    startup.manager.bind_max_live(lambda: 3)
    actor = startup.actor()
    created, release = asyncio.Event(), asyncio.Event()

    async def resolve(_session_id: str, _settings_file: str) -> events.CodeCopy:
        created.set()
        await release.wait()
        return startup.copy()

    startup.manager.bind_bot_code(resolve)
    others = [startup.manager.create(f"{i:08d}-0000-4000-8000-000000000000") for i in range(3)]
    for other in others[:-1]:
        await other.start()
    task = asyncio.create_task(actor.send("synthetic prompt"))
    seen = watch(actor)
    try:
        await asyncio.wait_for(created.wait(), HANG_GUARD_S)
        assert await others[-1].start()
        release.set()
        await task
        assert len(startup.launches) == 3 and not actor.live
        assert any(e["type"] == "error" and e["data"]["code"] == "too_many_sessions" for e in seen)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await startup.manager.close_all()


async def test_concurrent_sends_resolve_and_spawn_once(tmp_path: Path) -> None:
    startup = Startup(tmp_path)
    calls: list[str] = []

    async def resolve(session_id: str, _settings_file: str) -> events.CodeCopy:
        calls.append(session_id)
        return startup.copy()

    startup.manager.bind_bot_code(resolve)
    actor = startup.actor()
    try:
        await asyncio.gather(actor.send("first"), actor.send("second"))
        assert calls == [SID] and len(startup.launches) == 1
        assert startup.sent == ["first", "second"]
    finally:
        await startup.manager.close_all()


@pytest.mark.parametrize(
    "excluded", ["ordinary", "cwd", "fork", "read_only_tools", "no_memory", "unbound"]
)
async def test_resolver_exclusions_and_absence_are_inert(tmp_path: Path, excluded: str) -> None:
    startup = Startup(tmp_path)
    calls: list[str] = []

    async def resolve(session_id: str, _settings_file: str) -> events.CodeCopy:
        calls.append(session_id)
        return startup.copy()

    if excluded != "unbound":
        startup.manager.bind_bot_code(resolve)
    if excluded == "ordinary":
        actor = startup.manager.create(SID)
    elif excluded in {"fork", "read_only_tools"}:
        startup.state.remember(SID, mode="auto", effort="low")
        launch = Launch(
            SID,
            fork_of=OTHER if excluded == "fork" else None,
            read_only_tools=excluded == "read_only_tools",
        )
        actor = startup.manager._actor(SID, launch, profile=True)
    else:
        actor = startup.actor()
        if excluded == "cwd":
            startup.root = tmp_path / "code"
        if excluded == "no_memory":
            startup.memory = None
    try:
        await actor.send("synthetic prompt")
        assert calls == [] and len(startup.launches) == 1
        assert startup.launches[0].code_worktree is None
        assert actor.started is not None and "code_copy" not in actor.started.data()
    finally:
        await actor.close()
        await startup.manager.close_all()


async def test_real_driver_keeps_copy_out_of_additional_folders(tmp_path: Path) -> None:
    manager, spawner = _manager(tmp_path, [])
    settings = str(tmp_path / "bot-memory-settings" / ("a" * 64 + ".json"))
    expected = events.CodeCopy(
        path=str(tmp_path / "project-worktrees" / COPY_ID), folder="project", branch="bot/synthetic"
    )

    async def guard(_session_id: str, _prefs: Prefs) -> None:
        pass

    async def initialized(_session_id: str, _facts: StartFacts | None) -> None:
        pass

    async def observer(_started: events.SessionStarted) -> None:
        pass

    async def memory(_session_id: str) -> str:
        return settings

    async def resolve(_session_id: str, settings_file: str) -> events.CodeCopy:
        assert settings_file == settings
        return expected

    manager.bind_profiles(guard, observer, initialized=initialized)
    manager.bind_bot_memory(memory)
    manager.bind_bot_code(resolve)
    manager.app_state.remember(SID, mode="auto", effort="low")
    actor = manager.attach_prepared(SID, profile=True)
    try:
        await actor.send("synthetic prompt")
        assert len(spawner.children) == 1
        argv = spawner.children[0].argv
        assert [arg for arg in argv if arg.startswith("--add-dir=")] == [
            f"--add-dir={expected.path}"
        ]
        assert actor.started is not None
        assert actor.started.code_copy is expected and actor.started.additional_folders == []
    finally:
        await manager.close_all()
