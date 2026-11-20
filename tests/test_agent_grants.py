"""The per-process VIKI Agent CLI key: only an ordinary chat's Claude process gets one, each
process a new one, and it stops working when that process ends."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.conversations.test_session import FORK, eventually, watch
from viki_agent import session as session_module
from viki_agent import winproc
from viki_agent.app import agent_grants
from viki_agent.app.agent_grants import ENV_CLI, ENV_KEY, ENV_URL, AgentGrant, AgentGrants
from viki_agent.drivers.claude.control import Launch, build_argv
from viki_agent.drivers.claude.driver import ClaudeDriver, DriverConfig
from viki_agent.session import SessionManager
from viki_agent.state import AppState, Prefs

from replay import SID, Entry, FakeSpawner, init_frame, out, result_frame, user_in

CLI = Path("/synthetic/bin/viki-agent")
URL = "http://127.0.0.1:8765/api/agent/"
TURN = [user_in(), out(init_frame()), out(result_frame())]
CRASH = [
    user_in(),
    out(init_frame()),
    out({"type": "fake.stderr", "text": "segfault\n"}),
    out({"type": "fake.exit", "code": 3}),
]


class Spawns:
    """A FakeSpawner that keeps each process's argv and environment."""

    def __init__(self, script: list[Entry]) -> None:
        self.fake = FakeSpawner(script)
        self.argvs: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    async def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> Any:
        self.argvs.append(list(argv))
        self.envs.append(dict(env))
        return await self.fake(argv, cwd, env)


def _manager(
    tmp_path: Path, script: list[Entry], *, bind: bool = True
) -> tuple[SessionManager, Spawns, AgentGrants]:
    spawns = Spawns(script)
    (tmp_path / "home" / "sessions").mkdir(parents=True, exist_ok=True)
    manager = SessionManager(
        config=DriverConfig(
            cli=("claude.exe",),
            vault=tmp_path,
            env={"PATH": "synthetic"},
            spawner=spawns,
            grace_s=0.05,
            poll_s=0.05,
            control_timeout_s=0.05,
        ),
        state=AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low")),
        claude_home=tmp_path / "home",
    )
    grants = AgentGrants(CLI)
    if bind:
        manager.bind_agent_cli(grants)
    return manager, spawns, grants


def _key(env: Mapping[str, str]) -> tuple[str, str]:
    """The grant id (the URL's last segment) and the key one process got."""
    assert env[ENV_URL].startswith(URL) and env[ENV_CLI] == str(CLI)
    return env[ENV_URL].removeprefix(URL), env[ENV_KEY]


@pytest.mark.parametrize("made_by", ["create", "open", "fork"])
async def test_an_ordinary_chat_gets_a_key_that_ends_with_its_process(
    tmp_path: Path, made_by: str
) -> None:
    manager, spawns, grants = _manager(tmp_path, TURN)
    if made_by == "create":
        actor = manager.create(SID, agent_cli=True)
    elif made_by == "open":
        actor = manager.open(SID, agent_cli=True)
    else:
        actor = manager.fork(SID, FORK, agent_cli=True)
    seen = watch(actor)
    assert await actor.send("hello")
    await seen.until("turn.done")
    [env] = spawns.envs
    grant_id, key = _key(env)
    assert env["PATH"] == "synthetic" and len(key) >= 43  # token_urlsafe(32)
    grant = grants.find(grant_id, key)
    assert grant is not None and grant.session_id == actor.session_id
    for wrong in ("", key[:-1], key + "x", "é", "\ud800"):
        assert grants.find(grant_id, wrong) is None
    # The key rides the environment only: any process on the machine can read an argv.
    assert not any(key in arg or grant_id in arg for arg in spawns.argvs[0])
    await actor.close()
    assert grants.find(grant_id, key) is None and not grants.live(grant_id)


async def test_a_crash_revokes_the_key_and_the_resumed_process_gets_a_new_one(
    tmp_path: Path,
) -> None:
    manager, spawns, grants = _manager(tmp_path, CRASH)
    actor = manager.create(SID, agent_cli=True)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("error")
    first = _key(spawns.envs[0])
    assert grants.find(*first) is None  # the crash's EXIT revoked it
    await actor.send("again")  # the crash rebuilt the launch as a resume
    await seen.until("error", count=2)
    assert f"--resume={SID}" in spawns.argvs[1]
    second = _key(spawns.envs[1])
    assert second[0] != first[0] and second[1] != first[1]
    assert grants.find(*second) is None


async def test_a_close_cancelled_while_it_waits_still_revokes_the_key_at_exit(
    tmp_path: Path,
) -> None:
    """A close first waits a moment for a stop armed before the CLI picked the message up. If
    that close is cancelled meanwhile, it never reaches _close_control and the process runs on;
    its key still stops working once that process exits."""
    manager, spawns, grants = _manager(tmp_path, [user_in()])  # the CLI never picks it up
    actor = manager.create(SID, agent_cli=True)
    assert await actor.send("hello")
    driver = actor._driver
    assert isinstance(driver, ClaudeDriver) and driver.busy
    grant_id, key = _key(spawns.envs[0])
    closing = asyncio.create_task(actor.close())
    await eventually(lambda: driver._closing)  # now waiting for the armed stop to go out
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert grants.find(grant_id, key) is not None  # the process still runs
    spawns.fake.children[0].process.exit(0)
    await eventually(lambda: not driver.alive)
    assert grants.find(grant_id, key) is None
    await driver.close()  # what the cancelled close left undone


async def test_a_driver_that_cannot_be_built_revokes_its_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, spawns, grants = _manager(tmp_path, TURN)
    built: list[AgentGrant | None] = []

    def explode(*_args: Any, config: DriverConfig, **_kwargs: Any) -> Any:
        built.append(config.agent_grant)
        raise ValueError("synthetic build failure")

    monkeypatch.setattr(session_module, "ClaudeDriver", explode)
    actor = manager.create(SID, agent_cli=True)
    seen = watch(actor)
    assert not await actor.start()
    assert (await seen.until("error"))["data"]["code"] == "internal"
    [grant] = built
    assert grant is not None and not grants.live(grant.id) and spawns.envs == []


@pytest.mark.parametrize("kind", ["unflagged", "read_only", "profile", "root", "unbound"])
async def test_no_key_outside_a_flagged_ordinary_chat(tmp_path: Path, kind: str) -> None:
    manager, spawns, grants = _manager(tmp_path, TURN, bind=kind != "unbound")

    async def allow(*_args: object) -> None:
        return None

    async def root(_session_id: str) -> Path | None:
        return tmp_path

    if kind == "unflagged":
        actor = manager.create(SID)
    elif kind == "read_only":
        actor = manager.create(SID, read_only_tools=True, agent_cli=True)
    elif kind == "profile":
        manager.bind_profiles(allow, allow, initialized=allow)
        actor = manager.open(SID, profile=True, agent_cli=True)
    elif kind == "root":
        actor = manager.create(SID, root_resolver=root, agent_cli=True)
    else:
        actor = manager.create(SID, agent_cli=True)
    seen = watch(actor)
    assert await actor.send("hello")
    await seen.until("turn.done")
    [env] = spawns.envs
    assert not any(name.upper().startswith("VIKI_AGENT_") for name in env)
    assert not grants._grants
    await actor.close()


async def test_a_bot_launch_gets_no_key_even_when_flagged(tmp_path: Path) -> None:
    manager, _spawns, grants = _manager(tmp_path, TURN)
    settings = str(tmp_path / "bot-memory-settings" / f"{'a' * 64}.json")
    launch = Launch(SID, agent_cli=True, bot_memory_settings=settings)
    manager._claude_driver(launch, Prefs(mode="auto", effort="low"), lambda _event: None)
    assert not grants._grants


@pytest.mark.parametrize(
    "launch", [Launch(SID), Launch(SID, resume=True), Launch(FORK, fork_of=SID)]
)
def test_the_flag_adds_nothing_to_the_argv(launch: Launch) -> None:
    flagged = replace(launch, agent_cli=True)
    plain = build_argv(["claude.exe"], launch, mode="auto", effort="low")
    assert build_argv(["claude.exe"], flagged, mode="auto", effort="low") == plain


def test_an_inherited_key_is_scrubbed_in_any_letter_case() -> None:
    env = {"PATH": "synthetic", ENV_URL: "u", "viki_agent_key": "k", "Viki_Agent_Cli": "c"}
    assert winproc.scrub_env(env) == {"PATH": "synthetic"}


def test_no_repr_shows_the_key(tmp_path: Path) -> None:
    grants = AgentGrants(CLI)
    grant = grants.issue(SID)
    assert grant is not None
    config = DriverConfig(cli=("claude.exe",), vault=tmp_path, env={}, agent_grant=grant)
    for text in (repr(grant), str(grant), repr(config), repr(grants)):
        assert grant.token not in text and grant.id not in text


def test_past_the_cap_a_chat_gets_no_key_and_one_warning_without_ids(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(agent_grants, "MAX_GRANTS", 2)
    grants = AgentGrants(CLI)
    first, second = grants.issue(SID), grants.issue(FORK)
    assert first is not None and second is not None
    with caplog.at_level(logging.WARNING, logger=agent_grants.__name__):
        assert grants.issue(SID) is None
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    for value in (first.id, first.token, second.id, second.token, SID, FORK):
        assert value not in caplog.text
    first.revoke()
    assert not grants.live(first.id) and grants.issue(SID) is not None
