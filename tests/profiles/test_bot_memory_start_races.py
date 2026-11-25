"""Bot launch checkpoints refuse authority revoked while a synthetic start awaits."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.profiles.test_bot_memory import bot
from tests.profiles.test_bot_memory import rig as rig
from tests.profiles.test_profile_document import document
from tests.profiles.test_profile_launch import ident, native_started
from tests.profiles.test_profile_launch import setup as setup
from tests.profiles.test_profile_launch_api import HANG_GUARD_S
from tests.test_server import EchoDriver
from viki_agent.automation.routines import RoutineService
from viki_agent.code.session_roots import SessionRoots
from viki_agent.drivers.base import StartError, StartFacts
from viki_agent.drivers.claude.control import Launch
from viki_agent.events import Event, SessionStarted
from viki_agent.messaging.bot_memory import instruction_excludes
from viki_agent.messaging.bot_memory_rule import bot_system_prompt
from viki_agent.messaging.bots import BotService
from viki_agent.profiles.service import ProfileError
from viki_agent.session import SessionActor
from viki_agent.state import Prefs
from viki_agent.turn_input import TurnInput


class Startup:
    def __init__(self, rig: Any, *, barrier: str = "", resume: bool = False) -> None:
        self.service, self.state, self.vault, pid, self.wid = rig
        self.owner, self.sid = bot(self.service, pid, self.wid), ident()
        assert self.service.prepare(pid, ident(), self.sid, 1, self.wid, self.owner)["launchable"]
        if resume:
            self.service.native_initialized(self.sid)
        self.transcript_exists = resume
        self.service.transcript_exists = lambda _: self.transcript_exists
        self.barrier = barrier
        self.paused, self.release = asyncio.Event(), asyncio.Event()
        self.drivers: list[EchoDriver] = []
        self.launches: list[Launch] = []
        self.sent: list[str] = []
        self.intents: list[str] = []
        self.refusals: list[str] = []
        roots = SessionRoots(
            self.state.store, self.service.workspaces, exists=lambda _: False, actor=lambda _: None
        )

        async def guard(session_id: str, _explicit: bool, prefs: Prefs) -> None:
            try:
                await asyncio.to_thread(self.service.validate_launch, session_id, prefs)
            except ProfileError as error:
                self.refusals.append(error.code)
                raise StartError("bad_request", error.code) from None

        async def memory(session_id: str) -> str | None:
            try:
                return await asyncio.to_thread(self.service.bot_memory_settings_path, session_id)
            except ProfileError as error:
                self.refusals.append(error.code)
                raise StartError("bad_request", error.code) from None

        async def prompt(session_id: str) -> str | None:
            return await asyncio.to_thread(self.service.bot_system_prompt, session_id)

        async def holder(_session_id: str) -> None:
            await self.wait_at("holder")

        async def root(session_id: str) -> Path | None:
            await self.wait_at("root")
            return await roots.resolve(session_id)

        async def initialized(session_id: str, facts: StartFacts | None) -> None:
            try:
                await asyncio.to_thread(self.service.native_initialized, session_id, facts)
            except ProfileError as error:
                self.refusals.append(error.code)
                raise StartError("bad_request", error.code) from None

        startup = self

        class Driver(EchoDriver):
            async def open(self) -> SessionStarted:
                await startup.wait_at("open")
                startup.transcript_exists = True
                return await super().open()

            async def send(self, text: str) -> None:
                startup.sent.append(text)

        def factory(launch: Launch, _prefs: Prefs, emit: Callable[[Event], None]) -> EchoDriver:
            assert launch.bot_memory_settings is not None
            assert json.loads(Path(launch.bot_memory_settings).read_text(encoding="ascii")) == {
                "autoMemoryDirectory": str(self.vault / "bot-memory" / self.owner),
                "autoMemoryEnabled": False,
                "claudeMdExcludes": instruction_excludes(self.vault),
            }
            driver = Driver(launch, emit)
            self.drivers.append(driver)
            self.launches.append(launch)
            return driver

        self.actor = SessionActor(
            self.sid,
            Launch(self.sid, resume=resume),
            state=self.state,
            make_driver=factory,
            admit=lambda _: True,
            holder=holder,
            profile=True,
            launch_guard=guard,
            initialized=initialized,
            bot_memory_resolver=memory,
            bot_prompt_resolver=prompt,
            root_resolver=root,
        )

    async def wait_at(self, name: str) -> None:
        if self.barrier == name:
            self.paused.set()
            await self.release.wait()

    async def send(self):
        async def accept() -> TurnInput:
            return TurnInput("Synthetic Bot startup prompt")

        async def intent() -> None:
            self.intents.append(self.sid)

        return await self.actor.send_command(
            expected_generation=self.actor.generation, accept=accept, dispatch_intent=intent
        )

    def revoke(self, change: str) -> None:
        if change == "protect":
            workspace = self.service.workspaces.get(self.wid)
            changed = self.service.workspaces.set_policy(
                ident(), self.wid, workspace.policy_revision, "protected", ()
            )
            assert changed.policy == "protected"
            return
        routines = RoutineService(
            self.state.store, self.service.profiles, self.service.settings, self.service.workspaces
        )
        bots = BotService(
            self.state.store, self.service.profiles, routines, self.service.workspaces
        )
        if change == "remove":
            assert bots.remove(self.owner, 1)["removed"]
        else:
            assert bots.reset(self.wid)["removed_bot_ids"] == [self.owner]


@pytest.mark.parametrize("change", ["remove", "reset", "protect"])
@pytest.mark.parametrize(
    ("barrier", "resume"),
    [("root", False), ("root", True), ("holder", True), ("open", False), ("open", True)],
)
async def test_revoked_start_refuses_before_send(
    rig: Any, change: str, barrier: str, resume: bool
) -> None:
    startup = Startup(rig, barrier=barrier, resume=resume)
    attempt = asyncio.create_task(startup.send())
    try:
        await asyncio.wait_for(startup.paused.wait(), HANG_GUARD_S)
        assert bool(startup.drivers) == (barrier == "open")
        startup.revoke(change)
        with pytest.raises(ProfileError, match="launch_changed"):
            startup.service.validate_launch(startup.sid, startup.state.prefs(startup.sid))
    finally:
        startup.release.set()
        result = await attempt
        await startup.actor.close()
    assert result.status == "not_dispatched"
    assert startup.refusals == ["launch_changed"]
    assert not startup.sent and not startup.intents
    assert all(not driver.alive for driver in startup.drivers)
    assert len(startup.drivers) == (1 if barrier == "open" else 0)
    assert native_started(startup.state, startup.sid) == int(resume)
    assert not (startup.vault / "bot-memory").exists()


async def test_first_native_open_can_create_its_transcript_before_checkpoint(rig: Any) -> None:
    startup = Startup(rig)
    try:
        assert (await startup.send()).status == "unknown"
        assert startup.transcript_exists and startup.sent and startup.intents
        assert not startup.refusals
        assert native_started(startup.state, startup.sid) == 1
    finally:
        await startup.actor.close()


@pytest.mark.parametrize("resume", [False, True])
async def test_every_start_carries_the_bot_instructions_as_system_prompt(
    rig: Any, resume: bool
) -> None:
    # ADR-084: a resume sends it again, as Claude Code re-renders the prompt after compaction.
    startup = Startup(rig, resume=resume)
    try:
        assert (await startup.send()).status == "unknown"
        assert [launch.append_system_prompt for launch in startup.launches] == [
            bot_system_prompt(document()["instructions"])
        ]
        assert startup.sent == ["Synthetic Bot startup prompt"]
    finally:
        await startup.actor.close()


@pytest.mark.parametrize("change", ["remove", "reset", "protect"])
async def test_initialized_live_process_keeps_its_launch(rig: Any, change: str) -> None:
    startup = Startup(rig)
    try:
        await startup.send()
        startup.revoke(change)
        assert (await startup.send()).status == "unknown"
        assert len(startup.drivers) == 1 and len(startup.sent) == 2
        assert not startup.refusals
    finally:
        await startup.actor.close()
