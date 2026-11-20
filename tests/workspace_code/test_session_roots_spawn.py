"""The per-spawn root: a bound resolver picks the folder each claude process runs in, the argv
stays the same (hard rule 4: a cwd is not a flag), and an unbound manager runs in the vault."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tests.conversations.test_session import watch
from viki_agent.drivers.base import StartError
from viki_agent.drivers.claude.control import Launch, build_argv
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.session import SessionManager
from viki_agent.state import AppState, Prefs
from viki_agent.storage import StateError

from replay import SID, Entry, FakeChild, FakeSpawner, init_frame, out, user_in

VAULT = Path("C:/Vault")
ROOT = Path("C:/Code/app")
OTHER = Path("C:/Code/other")
OLD = "22222222-2222-4222-8222-222222222222"


class CwdSpawner(FakeSpawner):
    """A FakeSpawner that also keeps the cwd each child was spawned in."""

    def __init__(self, script: list[Entry], **options: Any) -> None:
        super().__init__(script, **options)
        self.cwds: list[Path] = []

    async def __call__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> FakeChild:
        self.cwds.append(cwd)
        return await super().__call__(argv, cwd, env)


class Roots:
    """A root resolver answering from `answers` in turn (the last one repeats); it records
    every session id it was asked about."""

    def __init__(self, *answers: Path | None | Exception) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    async def __call__(self, session_id: str) -> Path | None:
        self.asked.append(session_id)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _manager(tmp_path: Path, script: list[Entry]) -> tuple[SessionManager, CwdSpawner]:
    spawner = CwdSpawner(script)
    config = DriverConfig(
        cli=("claude.exe",),
        vault=VAULT,
        env={},
        spawner=spawner,
        grace_s=0.05,
        poll_s=0.05,
        control_timeout_s=0.05,
    )
    state = AppState(tmp_path / "state.json", Prefs(mode="auto", effort="low"))
    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True)
    return SessionManager(config=config, state=state, claude_home=home), spawner


CRASH = [
    user_in(),
    out(init_frame()),
    out({"type": "fake.stderr", "text": "segfault\n"}),
    out({"type": "fake.exit", "code": 3}),
]


def test_build_argv_ignores_the_cwd() -> None:
    for launch in (Launch(SID), Launch(SID, resume=True), Launch(SID, fork_of=OLD)):
        here = Launch(launch.session_id, launch.resume, launch.fork_of, cwd=ROOT)
        assert build_argv(["claude.exe"], here, mode="auto", effort="low") == build_argv(
            ["claude.exe"], launch, mode="auto", effort="low"
        )


async def test_a_bound_root_is_the_spawn_cwd(tmp_path: Path) -> None:
    manager, spawner = _manager(tmp_path, [])
    roots = Roots(ROOT)
    manager.bind_roots(roots)
    actor = manager.create(SID)
    assert await actor.start()
    assert spawner.cwds == [ROOT] and roots.asked == [SID]
    await actor.close()


async def test_an_unbound_manager_spawns_in_the_vault(tmp_path: Path) -> None:
    manager, spawner = _manager(tmp_path, [])
    actor = manager.create(SID)
    assert await actor.start()
    assert spawner.cwds == [VAULT]
    await actor.close()


async def test_a_card_path_reads_relative_to_the_root(tmp_path: Path) -> None:
    request = {
        "subtype": "can_use_tool",
        "tool_name": "Write",
        "input": {"file_path": "C:/Code/app/src/main.py", "content": "x"},
        "tool_use_id": "toolu_1",
    }
    script = [
        user_in(),
        out(init_frame()),
        out({"type": "control_request", "request_id": "rec-1", "request": request}),
    ]
    manager, _ = _manager(tmp_path, script)
    manager.bind_roots(Roots(ROOT))
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    card = (await seen.until("permission.requested"))["data"]
    assert card["summary"] == "src/main.py"
    await actor.close()


async def test_the_root_is_resolved_again_for_the_respawn(tmp_path: Path) -> None:
    manager, spawner = _manager(tmp_path, CRASH)
    roots = Roots(ROOT, OTHER)
    manager.bind_roots(roots)
    actor = manager.create(SID)
    seen = watch(actor)
    await actor.send("hello")
    await seen.until("error")
    assert not actor.live  # the crash dropped the driver
    await actor.send("again")
    await seen.until("error", count=2)
    assert spawner.cwds == [ROOT, OTHER] and roots.asked == [SID, SID]
    assert f"--resume={SID}" in spawner.children[1].argv


async def test_a_start_error_from_the_resolver_builds_no_driver(tmp_path: Path) -> None:
    manager, spawner = _manager(tmp_path, [])
    manager.bind_roots(Roots(StartError("bad_request", "x")))
    actor = manager.create(SID)
    seen = watch(actor)
    assert not await actor.start()
    error = (await seen.until("error"))["data"]
    assert error == {"code": "bad_request", "message": "x", "recoverable": True}
    assert spawner.children == [] and not actor.live


async def test_a_state_error_from_the_resolver_is_reported(tmp_path: Path) -> None:
    manager, spawner = _manager(tmp_path, [])
    manager.bind_roots(Roots(StateError("corrupt")))
    actor = manager.create(SID)
    seen = watch(actor)
    assert not await actor.start()
    error = (await seen.until("error"))["data"]
    assert error["code"] == "internal" and error["message"] == StateError("corrupt").message
    assert spawner.children == [] and not actor.live


async def test_the_live_limit_holds_across_roots(tmp_path: Path) -> None:
    manager, spawner = _manager(tmp_path, [])
    manager.bind_max_live(lambda: 3)
    ids = [f"{i:08d}-0000-4000-8000-000000000000" for i in range(4)]
    folders = {sid: Path(f"C:/Code/r{i}") for i, sid in enumerate(ids)}

    async def per_session(session_id: str) -> Path | None:
        return folders[session_id]

    manager.bind_roots(per_session)
    actors = [manager.create(sid) for sid in ids]
    seen = watch(actors[-1])
    for actor in actors[:-1]:
        assert await actor.start()
    assert not await actors[-1].start()
    assert (await seen.until("error"))["data"]["code"] == "too_many_sessions"
    assert spawner.cwds == [folders[sid] for sid in ids[:-1]]
    await manager.close_all()
