"""The app-open routine matrix: the full app over REST and a fake clock, each run a real
ClaudeDriver over the replay engine or a real fake_claude.py process, one child per launch;
no provider, no user data."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest

from tests.automation.test_routines import _document_raw
from tests.automation.test_routines_api import (
    NOW,
    FakeWindows,
    _claimed,
    _created,
    _full_app,
    _serving,
    _waits,
)
from tests.automation.test_scheduler import FakeClock
from tests.automation.test_tasks import DEAD, RECONCILE, SCOPE, _open, _ordinary, _seed_command
from tests.profiles.test_profile_document import document as profile_document
from tests.profiles.test_profile_launch_api import AUTH, HANG_GUARD_S, WRITE
from tests.profiles.test_profile_launch_api import Rig as LaunchRig
from tests.test_transport import _fake_env
from viki_agent import winproc
from viki_agent.automation.routines import record_tested, stamp
from viki_agent.automation.tasks import Attempt, Observation, TaskRunner, TaskStore
from viki_agent.drivers.base import Answer, PermissionAnswer, StartError
from viki_agent.drivers.claude.control import Launch
from viki_agent.drivers.claude.driver import MSG_STOP_AT_DRAFT, ClaudeDriver, DriverConfig
from viki_agent.events import Event
from viki_agent.profiles.profile_capabilities import FIXTURE_VERSION
from viki_agent.profiles.service import ProfileService
from viki_agent.session import SessionManager
from viki_agent.state import Prefs
from viki_agent.window import WindowManager

from replay import (
    SID,
    Entry,
    FakeSpawner,
    expect,
    init_frame,
    load_script,
    out,
    permission_request,
    permission_response_in,
    result_frame,
    user_in,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "claude"
SLOT = datetime(2026, 9, 28, 0, 1, tzinfo=UTC)  # a minute past the daily 09:00 Tokyo slot
TURN = [user_in(), out(init_frame()), out(result_frame())]
# A card the deadline or the closing app must answer; nothing follows its answer.
HELD_CARD = [
    user_in(),
    out(init_frame()),
    out(permission_request("req_card", tool="Write")),
    permission_response_in("req_card"),
]


def _signed_in(account: dict[str, Any]) -> list[Entry]:
    """A claude whose initialize reply carries `account`."""
    request = {"subtype": "initialize"}
    reply = {"subtype": "success", "request_id": "init", "response": {"account": account}}
    return [
        expect({"type": "control_request", "request_id": "init", "request": request}),
        out({"type": "control_response", "response": reply}),
    ]


class Matrix:
    """The full app on a fake clock. Each launch takes the next queued script, played by its own
    FakeSpawner behind a real ClaudeDriver; a queued StartError refuses that launch instead."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **extra: Any) -> None:
        self.clock = FakeClock(NOW)
        self.launches: list[list[Entry] | StartError | DriverConfig] = []
        self.spawners: list[FakeSpawner] = []
        self.drivers: list[ClaudeDriver] = []
        monkeypatch.setattr(
            LaunchRig,
            "manager",
            lambda rig: SessionManager(
                config=DriverConfig(cli=("claude.exe",), vault=rig.vault, env={}),
                state=rig.state,
                claude_home=rig.home,
                make_driver=self._driver,
            ),
        )
        self.launch, self.app = _full_app(tmp_path, self.clock, **extra)
        self.runner: TaskRunner = self.app.state.routine_tasks
        self.manager = self.runner.manager
        # Every run start and every envelope a run observes sets `changed`, so a test waits on
        # the event rather than spinning, which would starve the store's worker threads.
        self.changed = asyncio.Event()
        self.attempts: list[Attempt] = []
        self.types: list[str] = []
        start, observe = self.runner.start, Observation.__call__

        def started(attempt: Attempt) -> None:
            start(attempt)
            self.attempts.append(attempt)
            self.changed.set()

        def observed(seen: Observation, envelope: dict[str, Any]) -> None:
            observe(seen, envelope)
            self.types.append(envelope["type"])
            self.changed.set()

        monkeypatch.setattr(self.runner, "start", started)
        monkeypatch.setattr(Observation, "__call__", observed)

    def _driver(self, launch: Launch, prefs: Prefs, emit: Callable[[Event], None]) -> ClaudeDriver:
        script = self.launches.pop(0)
        if isinstance(script, StartError):
            raise script
        if isinstance(script, DriverConfig):
            config = script
        else:
            spawner = FakeSpawner(script)
            self.spawners.append(spawner)
            config = DriverConfig(
                cli=("claude.exe",),
                vault=self.launch.vault,
                env={},
                spawner=spawner,
                cli_version=FIXTURE_VERSION,
                grace_s=0.05,
                poll_s=0.05,
                control_timeout_s=0.05,
            )
        driver = ClaudeDriver(
            launch,
            mode=prefs.mode,
            effort=prefs.effort,
            model=prefs.model,
            config=config,
            emit=emit,
        )
        self.drivers.append(driver)
        return driver

    def frames(self, kind: str) -> list[dict[str, Any]]:
        """Every frame of `kind` the app wrote to any fake child."""
        return [
            frame
            for spawner in self.spawners
            for child in spawner.children
            for frame in child.process.replayer.seen
            if frame["type"] == kind
        ]

    def count(self, table: str) -> int:
        with self.launch.state.store._connection() as connection:
            return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def carded(self) -> bool:
        return any(seen.open_cards for seen in self.runner._runs.values())

    async def until(self, ready: Callable[[], object]) -> None:
        async with asyncio.timeout(HANG_GUARD_S):
            while not ready():
                self.changed.clear()
                await self.changed.wait()

    async def routine(self, client: httpx.AsyncClient, **overrides: Any) -> str:
        ids = {"profile_id": self.launch.profile_id, "workspace_id": self.launch.workspace_id}
        response = await client.post(
            "/api/v2/routines",
            headers=WRITE,
            json={"command_id": str(uuid4()), "document": _document_raw(**{**ids, **overrides})},
        )
        assert response.status_code == 201
        return response.json()["routine_id"]

    async def enabled(self, client: httpx.AsyncClient) -> str:
        """A routine tested at revision 1, in an ordinary root, enabled through REST."""
        routine_id = await self.routine(client)
        with self.launch.state.store._connection(write=True) as connection:
            record_tested(connection, routine_id, 1, stamp(NOW))
        _ordinary(self.launch)
        response = await client.post(
            f"/api/v2/routines/{routine_id}/enable",
            headers=WRITE,
            json={"command_id": str(uuid4()), "expected_state_revision": 2},
        )
        assert response.json()["state"] == "enabled"
        return routine_id

    async def run(self, client: httpx.AsyncClient, routine_id: str) -> dict[str, Any]:
        response = await client.post(
            f"/api/v2/routines/{routine_id}/run",
            headers=WRITE,
            json={"command_id": str(uuid4()), "expected_revision": 1},
        )
        assert response.status_code == 202
        return response.json()

    async def sent(self) -> None:
        """Until every command job has ended, as a send long has by a run's deadline."""
        await asyncio.wait_for(asyncio.gather(*self.runner.commands._jobs), HANG_GUARD_S)

    def slot(self) -> None:
        """Move both clocks past the next daily slot and wake the scheduler."""
        self.clock.advance((SLOT - NOW).total_seconds())
        self.app.state.scheduler.wake()

    async def ended(self, client: httpx.AsyncClient, routine_id: str) -> dict[str, Any]:
        """The routine's newest run, once every started run has written its outcome."""
        async with asyncio.timeout(HANG_GUARD_S):
            await asyncio.gather(*self.runner._runs)
        response = await client.get(f"/api/v2/routines/{routine_id}/runs", headers=AUTH)
        return response.json()["runs"][0]


def _end(row: dict[str, Any]) -> tuple[str, str]:
    return row["attempt"]["outcome"], row["attempt"]["reason"]


async def test_a_due_slot_runs_once_and_closes_its_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    matrix.launches.append(load_script(FIXTURES / "text-two-turns.jsonl"))
    async with _serving(matrix.app) as client:
        routine_id = await matrix.enabled(client)
        matrix.slot()
        await matrix.until(lambda: matrix.attempts)
        row = await matrix.ended(client, routine_id)
    assert (row["kind"], *_end(row)) == ("schedule", "succeeded", "")
    assert matrix.count("sessions") == matrix.count("commands") == len(matrix.spawners) == 1
    assert matrix.manager.get(row["attempt"]["session_id"]) is None
    assert matrix.spawners[0].children[0].process.returncode is not None


@pytest.mark.parametrize(
    ("script", "ended"),
    [
        pytest.param(
            [*TURN[:2], out(result_frame(subtype="error_during_execution"))],
            ("failed", "turn_error"),
            id="error_result",
        ),
        pytest.param(
            load_script(FIXTURES / "interrupt-mid-stream.jsonl"),
            ("cancelled", "stopped"),
            id="owner_stop",
        ),
        pytest.param(
            load_script(FIXTURES / "exit-mid-turn.jsonl"), ("failed", "crashed"), id="crash"
        ),
    ],
)
async def test_a_turn_ends_in_error_stop_or_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: list[Entry],
    ended: tuple[str, str],
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    matrix.launches.append(script)
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client)
        ids = await matrix.run(client, routine_id)
        if ended == ("cancelled", "stopped"):
            await matrix.until(lambda: "text.delta" in matrix.types)
            actor = matrix.manager.get(ids["session_id"])
            assert actor is not None
            await actor.interrupt()
        assert _end(await matrix.ended(client, routine_id)) == ended


async def test_a_card_open_past_the_deadline_is_only_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    matrix.launches.append(load_script(FIXTURES / "write-permission.jsonl"))
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client)
        await matrix.run(client, routine_id)
        await matrix.until(matrix.carded)
        await matrix.sent()
        # The run's deadline reads only the monotonic clock: no daily slot comes due.
        matrix.clock.advance(60 * _document_raw()["deadline_minutes"], wall=False)
        matrix.clock.tick()
        assert _end(await matrix.ended(client, routine_id)) == ("expired", "waiting_for_user")
    answers = [frame["response"]["response"] for frame in matrix.frames("control_response")]
    assert [answer["behavior"] for answer in answers] == ["deny"]


@pytest.mark.parametrize(
    ("code", "launch"),
    [
        ("cli_missing", StartError("cli_missing", "synthetic")),
        ("billing", _signed_in({"apiProvider": "bedrock", "subscriptionType": "Claude Max"})),
        ("not_logged_in", _signed_in({})),
        ("too_many_sessions", None),
    ],
)
async def test_a_refused_start_is_blocked_and_never_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    launch: list[Entry] | StartError | None,
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    async with _serving(matrix.app) as client:
        if launch is None:  # eight ordinary sessions hold every live slot
            matrix.manager.bind_max_live(lambda: 8)
            for _ in range(8):
                matrix.launches.append(TURN[:2])
                assert await matrix.manager.create(str(uuid4())).send("hello")
        else:
            matrix.launches.append(launch)
        routine_id = await matrix.routine(client)
        await matrix.run(client, routine_id)
        assert _end(await matrix.ended(client, routine_id)) == ("blocked", code)
    assert matrix.count("commands") == 1 and matrix.launches == []
    assert len(matrix.spawners) == {"cli_missing": 0, "too_many_sessions": 8}.get(code, 1)


async def test_a_rejected_rate_limit_is_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limit = {"status": "rejected", "rateLimitType": "five_hour", "resetsAt": 1790000000}
    matrix = Matrix(tmp_path, monkeypatch)
    matrix.launches.append(
        [
            *TURN[:2],
            out({"type": "rate_limit_event", "rate_limit_info": limit, "session_id": SID}),
            out(result_frame(subtype="error_during_execution")),
        ]
    )
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client)
        await matrix.run(client, routine_id)
        assert _end(await matrix.ended(client, routine_id)) == ("blocked", "quota")


async def test_an_exit_mid_turn_is_crashed_and_not_respawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    matrix.launches.append(load_script(FIXTURES / "exit-mid-turn.jsonl"))
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client)
        ids = await matrix.run(client, routine_id)
        assert _end(await matrix.ended(client, routine_id)) == ("failed", "crashed")
        assert matrix.manager.get(ids["session_id"]) is None
    assert matrix.count("commands") == len(matrix.spawners) == 1


async def test_a_dead_launchs_attempts_end_by_the_table_and_nothing_is_resent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    routine_id = _created(matrix.app, matrix.launch)
    dead = TaskStore(matrix.launch.state.store, SCOPE, DEAD)
    expected: dict[str, tuple[str, str]] = {}
    for minute, (command, (state, result)) in enumerate(
        (command, item) for command, table in RECONCILE.items() for item in table.items()
    ):
        attempt = _open(dead, routine_id, minute, state)
        if command is not None:
            _seed_command(dead, attempt.attempt_id, command)
        expected[attempt.attempt_id] = (result, "restarted")
    async with _serving(matrix.app) as client:
        await _waits(matrix.clock, 1)  # the startup pass is done
        response = await client.get(f"/api/v2/routines/{routine_id}/runs", headers=AUTH)
    runs = response.json()["runs"]
    assert {row["attempt"]["attempt_id"]: _end(row) for row in runs} == expected
    assert len(runs) == 15 and matrix.spawners == [] and matrix.count("commands") == 12


async def test_no_window_claims_nothing_and_a_closing_app_denies_and_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows = FakeWindows()
    matrix = Matrix(tmp_path, monkeypatch, windows=cast(WindowManager, windows))
    matrix.launches.append(HELD_CARD)
    async with _serving(matrix.app) as client:
        routine_id = await matrix.enabled(client)
        waited = len(matrix.clock.waits)
        matrix.slot()
        await _waits(matrix.clock, waited + 1)
        assert _claimed(matrix.launch) == []
        windows.count = 1
        matrix.app.state.scheduler.wake()
        await matrix.until(matrix.carded)
        windows.count = 0  # the last window closed: the launcher's settle, then close_all
        await matrix.manager.settle(0.0, until=lambda: windows.count == 0)
        await matrix.manager.close_all()
        assert _end(await matrix.ended(client, routine_id)) == ("cancelled", "app_closed")
    answers = [frame["response"]["response"] for frame in matrix.frames("control_response")]
    assert [answer["behavior"] for answer in answers] == ["deny"]


async def test_a_routine_follows_its_profile_id_across_rename_duplicate_and_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    matrix.launches.extend([TURN, TURN])
    profiles, profile_id = ProfileService(matrix.launch.state.store), matrix.launch.profile_id
    renamed = profile_document()
    renamed["identity"]["name"] = "Renamed"
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client)
        rows = []
        for between in (
            lambda: None,
            lambda: (
                profiles.revise(profile_id, str(uuid4()), 1, renamed),
                profiles.duplicate(profile_id, str(uuid4()), 2, str(uuid4()), "claude"),
            ),
            lambda: profiles.archive(profile_id, str(uuid4()), 1, True),
        ):
            between()
            matrix.clock.advance(1)  # a later receipt instant, so each run lists newest
            await matrix.run(client, routine_id)
            rows.append(await matrix.ended(client, routine_id))
    assert [
        (*_end(row), row["attempt"]["profile_id"], row["attempt"]["profile_revision"])
        for row in rows
    ] == [
        ("succeeded", "", profile_id, 1),
        ("succeeded", "", profile_id, 2),
        ("blocked", "profile_archived", None, None),
    ]
    assert len(matrix.spawners) == 2


async def test_an_inactive_workspace_or_a_changed_policy_blocks_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    workspaces = matrix.launch.workspaces
    (tmp_path / "other").mkdir()
    other, _ = workspaces.register_existing(str(uuid4()), str(tmp_path / "other"))
    async with _serving(matrix.app) as client:
        inactive = await matrix.routine(client, workspace_id=other.workspace_id)
        await matrix.run(client, inactive)
        restart = await matrix.ended(client, inactive)
        routine_id = await matrix.enabled(client)
        revision = workspaces.get(matrix.launch.workspace_id).policy_revision
        workspaces.set_policy(
            str(uuid4()), matrix.launch.workspace_id, revision, "existing_readonly", []
        )
        matrix.slot()
        await matrix.until(lambda: len(matrix.attempts) == 2)
        policy = await matrix.ended(client, routine_id)
    assert [_end(restart), _end(policy)] == [
        ("blocked", "workspace_restart_required"),
        ("blocked", "unattended_policy"),
    ]
    assert matrix.spawners == [] and matrix.count("commands") == 0


# --- the same rows over a real process: fake_claude.py through winproc.spawn ------------------


def _subprocess_config(fake_cli: Sequence[str], vault: Path, fixture: str) -> DriverConfig:
    """A per-launch config with no spawner, so the driver starts `fixture` as a real process."""
    return DriverConfig(
        cli=tuple(fake_cli),
        vault=vault,
        env=_fake_env(FIXTURES / fixture),
        cli_version=FIXTURE_VERSION,
        grace_s=0.05,
        poll_s=0.05,
        control_timeout_s=0.05,
    )


def _no_leftover_process(matrix: Matrix) -> None:
    pid = matrix.drivers[0].pid
    assert pid is not None and winproc.process_start_time(pid) is None


async def test_a_due_slot_runs_once_over_a_real_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: list[str]
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    config = _subprocess_config(fake_cli, matrix.launch.vault, "text-two-turns.jsonl")
    matrix.launches.append(config)
    async with _serving(matrix.app) as client:
        routine_id = await matrix.enabled(client)
        matrix.slot()
        await matrix.until(lambda: matrix.attempts)
        row = await matrix.ended(client, routine_id)
    assert (row["kind"], *_end(row)) == ("schedule", "succeeded", "")
    assert matrix.count("sessions") == matrix.count("commands") == 1
    assert matrix.manager.get(row["attempt"]["session_id"]) is None
    _no_leftover_process(matrix)


async def test_a_card_open_past_the_deadline_is_only_denied_over_a_real_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: list[str]
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    config = _subprocess_config(fake_cli, matrix.launch.vault, "write-permission.jsonl")
    matrix.launches.append(config)
    answers: list[Answer] = []
    respond = ClaudeDriver.respond

    def recorded(driver: ClaudeDriver, tool_use_id: str, answer: Answer) -> bool:
        answers.append(answer)
        return respond(driver, tool_use_id, answer)

    monkeypatch.setattr(ClaudeDriver, "respond", recorded)
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client)
        await matrix.run(client, routine_id)
        await matrix.until(matrix.carded)
        await matrix.sent()
        # The run's deadline reads only the monotonic clock: no daily slot comes due.
        matrix.clock.advance(60 * _document_raw()["deadline_minutes"], wall=False)
        matrix.clock.tick()
        row = await matrix.ended(client, routine_id)
    assert _end(row) == ("expired", "waiting_for_user")
    assert answers == [PermissionAnswer(allow=False)]
    assert matrix.manager.get(row["attempt"]["session_id"]) is None
    _no_leftover_process(matrix)


async def test_an_exit_mid_turn_is_crashed_over_a_real_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_cli: list[str]
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    matrix.launches.append(_subprocess_config(fake_cli, matrix.launch.vault, "exit-mid-turn.jsonl"))
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client)
        await matrix.run(client, routine_id)
        row = await matrix.ended(client, routine_id)
    assert _end(row) == ("failed", "crashed")
    assert matrix.count("sessions") == matrix.count("commands") == 1
    assert matrix.manager.get(row["attempt"]["session_id"]) is None
    _no_leftover_process(matrix)


async def test_a_stop_routine_ends_needs_decision_over_the_real_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    matrix.launches.append(load_script(FIXTURES / "write-permission.jsonl"))
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client, on_confirmation="stop")
        await matrix.run(client, routine_id)
        row = await matrix.ended(client, routine_id)
        detail = await client.get(f"/api/v2/routines/{routine_id}", headers=AUTH)
    assert _end(row) == ("blocked", "needs_decision")
    answers = [frame["response"]["response"] for frame in matrix.frames("control_response")]
    assert answers == [{"behavior": "deny", "message": MSG_STOP_AT_DRAFT}]
    assert len(matrix.spawners) == 1
    assert detail.json()["tested_revision"] == 1


async def test_a_card_gated_routine_skips_without_a_card_and_runs_with_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matrix = Matrix(tmp_path, monkeypatch)
    async with _serving(matrix.app) as client:
        routine_id = await matrix.routine(client, run_only_if={"kind": "card", "seat": "research"})
        with matrix.launch.state.store._connection(write=True) as connection:
            record_tested(connection, routine_id, 1, stamp(NOW))
        _ordinary(matrix.launch)
        enabled = await client.post(
            f"/api/v2/routines/{routine_id}/enable",
            headers=WRITE,
            json={"command_id": str(uuid4()), "expected_state_revision": 2},
        )
        assert enabled.json()["state"] == "enabled"
        waited = len(matrix.clock.waits)
        matrix.slot()
        await _waits(matrix.clock, waited + 1)
        skipped = (await client.get(f"/api/v2/routines/{routine_id}/runs", headers=AUTH)).json()
        assert [(r["state"], r["reason"]) for r in skipped["runs"][:1]] == [("skipped", "no_card")]
        assert matrix.count("sessions") == matrix.count("commands") == len(matrix.spawners) == 0
        matrix.launches.append(load_script(FIXTURES / "text-two-turns.jsonl"))
        await matrix.run(client, routine_id)
        assert _end(await matrix.ended(client, routine_id)) == ("succeeded", "")
        kanban = matrix.launch.vault / "kanban"
        kanban.mkdir()
        (kanban / "find-sources.md").write_text(
            "---\ntype: card\nstatus: todo\ntags: [owner/research]\n---\n# Find sources\n",
            encoding="utf-8",
        )
        matrix.launches.append(load_script(FIXTURES / "text-two-turns.jsonl"))
        attempts = len(matrix.attempts)
        matrix.clock.advance(24 * 60 * 60)
        matrix.app.state.scheduler.wake()
        await matrix.until(lambda: len(matrix.attempts) == attempts + 1)
        row = await matrix.ended(client, routine_id)
    assert (row["kind"], *_end(row)) == ("schedule", "succeeded", "")
