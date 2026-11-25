"""Reflect and dream runs end to end against a scripted read-only session, and their automatic
starts: what the run sends, what it keeps, what it refuses, and when it starts on its own."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from tests.messaging.test_bot_learning import BOT, _learning
from tests.vaults.test_memory import NOON, Harness, _harness
from viki_agent.automation.routines import stamp
from viki_agent.messaging.bot_episodes import Episode
from viki_agent.messaging.bot_learning import BotLearning, LearningError
from viki_agent.messaging.bot_reflect import (
    DREAM_MIN_EPISODES,
    QUIET,
    REFLECT_MIN_EPISODES,
    AutoLearning,
    LearningRunner,
)

NOW = datetime.fromtimestamp(NOON / 1e9, UTC)
QUOTE = "I checked the abstract page and the version was v3"


class _Session:
    def __init__(self, reply: str, *, card: bool = False, finish: bool = True) -> None:
        self.reply, self.card, self.finish = reply, card, finish
        self.sent: list[str] = []
        self.denied = 0
        self.interrupted = False
        self.subscriber: Callable[[dict[str, Any]], None] | None = None

    def subscribe(self, subscriber: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self.subscriber = subscriber
        return lambda: None

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        assert self.subscriber is not None
        emit = self.subscriber
        if self.card:
            emit({"type": "permission.requested", "data": {"tool_use_id": "t"}})
        emit({"type": "text.delta", "data": {"block_id": "b", "text": self.reply}})
        if self.finish:
            emit({"type": "turn.done", "data": {"status": "success"}})
        return True

    def deny_pending(self) -> None:
        self.denied += 1

    async def interrupt(self) -> None:
        self.interrupted = True


class _State:
    def __init__(self) -> None:
        self.remembered: dict[str, dict[str, Any]] = {}

    def remember(self, session_id: str, **changes: Any) -> None:
        self.remembered[session_id] = changes


class _Host:
    def __init__(self, session: _Session) -> None:
        self.session = session
        self.app_state = _State()
        self.created: list[tuple[str, bool]] = []
        self.closed: list[str] = []

    def native_model(self) -> str | None:
        return "claude-sonnet"

    def create(self, session_id: str, *, read_only_tools: bool) -> _Session:
        self.created.append((session_id, read_only_tools))
        return self.session

    async def close(self, session_id: str) -> None:
        self.closed.append(session_id)


def _transcript(tmp_path: Path, session_id: str, at: datetime) -> Path:
    path = tmp_path / "claude" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    when = at.isoformat().replace("+00:00", "Z")
    lines = [
        {"type": "user", "timestamp": when, "message": {"content": "Cite the paper's number"}},
        {
            "type": "assistant",
            "timestamp": when,
            "message": {"content": [{"type": "text", "text": QUOTE + ", so I refetched it."}]},
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def _episodes(learning: BotLearning, harness: Harness, tmp_path: Path, count: int) -> list[str]:
    sessions = []
    for n in range(count):
        session_id = str(uuid.uuid4())
        finished = NOW - timedelta(hours=2, minutes=n)
        _transcript(tmp_path, session_id, finished - timedelta(seconds=30))
        learning.episodes.record(
            Episode(
                str(uuid.uuid4()),
                harness.wid,
                BOT,
                session_id,
                "main",
                stamp(finished - timedelta(minutes=1)),
                stamp(finished),
                "success",
                False,
                1,
                (),
                False,
            )
        )
        sessions.append(session_id)
    return sessions


def _paths(tmp_path: Path) -> Callable[[list[str]], dict[str, Path]]:
    return lambda ids: {sid: tmp_path / "claude" / f"{sid}.jsonl" for sid in ids}


def _reply(ops: list[dict[str, Any]]) -> str:
    return "```viki-learning\n" + json.dumps({"ops": ops}) + "\n```"


@pytest.fixture
def rig(tmp_path: Path) -> tuple[Harness, BotLearning]:
    harness = _harness(tmp_path, "ordinary")
    return harness, _learning(harness, tmp_path)


async def _finish(runner: LearningRunner, tasks: set[asyncio.Task[Any]]) -> None:
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    assert runner.running is None


async def test_a_reflect_run_sends_one_read_only_turn_and_seals_what_passes(
    tmp_path: Path, rig: tuple[Harness, BotLearning]
) -> None:
    harness, learning = rig
    _episodes(learning, harness, tmp_path, 2)
    ops = [
        {
            "op": "lesson_add",
            "trigger": "Citing a paper's number",
            "action": "Check the newest version first",
            "evidence": [{"episode": "E1", "quote": QUOTE}],
            "why": "It found v3",
        },
        {
            "op": "fact_add",
            "claim": "Invented",
            "why": "w",
            "evidence": [{"episode": "E1", "quote": "nothing like this was said"}],
        },
    ]
    session = _Session(_reply(ops), card=True)
    host = _Host(session)
    tasks: set[asyncio.Task[Any]] = set()
    runner = LearningRunner(cast(Any, host), learning, _paths(tmp_path), tasks=tasks)

    run = await runner.start(BOT, "reflect")
    with pytest.raises(LearningError, match="run_running"):
        await runner.start(BOT, "reflect")
    await _finish(runner, tasks)

    assert host.created == [(run.session_id, True)]  # read-only tools only
    assert host.closed == [run.session_id]
    assert host.app_state.remembered[cast(str, run.session_id)]["effort"] == "low"
    assert session.denied == 1
    (prompt,) = session.sent
    assert "VIKI Agent reflect run" in prompt and QUOTE in prompt
    (stored,) = learning.proposals.listing(BOT)
    assert stored.op == "lesson_add" and "Check the newest version first" in stored.body["claim"]
    (done,) = learning.proposals.runs(BOT)
    assert (done.state, done.episodes, done.proposed, done.rejected) == ("done", 2, 1, 1)
    assert learning.episodes.count(BOT, after=None, reflected=False) == 0
    with pytest.raises(LearningError, match="no_episodes"):
        await runner.start(BOT, "reflect")


async def test_a_run_without_a_block_or_past_its_deadline_proposes_nothing(
    tmp_path: Path, rig: tuple[Harness, BotLearning]
) -> None:
    harness, learning = rig
    _episodes(learning, harness, tmp_path, 1)
    tasks: set[asyncio.Task[Any]] = set()
    runner = LearningRunner(
        cast(Any, _Host(_Session("I'd rather not."))), learning, _paths(tmp_path), tasks=tasks
    )
    await runner.start(BOT, "dream")
    await _finish(runner, tasks)
    assert learning.proposals.runs(BOT)[0].reason == "no_reply"

    session = _Session("", finish=False)
    runner = LearningRunner(
        cast(Any, _Host(session)), learning, _paths(tmp_path), tasks=tasks, deadline_s=0.01
    )
    await runner.start(BOT, "dream")
    await _finish(runner, tasks)
    assert learning.proposals.runs(BOT)[0].reason == "deadline" and session.interrupted
    assert learning.proposals.listing(BOT) == []


async def test_a_run_needs_the_active_ordinary_vault(
    tmp_path: Path, rig: tuple[Harness, BotLearning]
) -> None:
    harness, learning = rig
    _episodes(learning, harness, tmp_path, 1)
    other = harness.spaces.add(tmp_path, "ordinary")
    harness.spaces.active = other
    runner = LearningRunner(cast(Any, _Host(_Session(""))), learning, _paths(tmp_path), tasks=set())
    with pytest.raises(LearningError, match="not_active"):
        await runner.start(BOT, "reflect")


async def test_automatic_runs_wait_for_their_setting_quiet_and_enough_work(
    tmp_path: Path, rig: tuple[Harness, BotLearning]
) -> None:
    harness, learning = rig
    learning.bots = cast(Any, _Roster(harness.wid))
    tasks: set[asyncio.Task[Any]] = set()
    runner = LearningRunner(
        cast(Any, _Host(_Session(_reply([])))), learning, _paths(tmp_path), tasks=tasks
    )
    settings: dict[str, object] = {}
    auto = AutoLearning(runner, learning, lambda: settings)

    _episodes(learning, harness, tmp_path, REFLECT_MIN_EPISODES - 1)
    settings["bots.auto_reflect"] = True
    assert await auto.pass_once() is None  # too few unread episodes
    _episodes(learning, harness, tmp_path, 1)
    run = await auto.pass_once()
    assert run is not None and (run.mode, run.trigger) == ("reflect", "auto")
    await _finish(runner, tasks)

    settings["bots.auto_dream"] = True
    _episodes(learning, harness, tmp_path, DREAM_MIN_EPISODES)
    learning.now = lambda: NOW - timedelta(hours=2) + QUIET / 2  # the Bot worked just now
    assert await auto.pass_once() is None
    learning.now = lambda: NOW
    run = await auto.pass_once()
    assert run is not None and run.mode == "dream"
    await _finish(runner, tasks)
    settings["bots.auto_reflect"] = False
    assert await auto.pass_once() is None  # one dream a day


class _Roster:
    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id

    def get(self, bot_id: str) -> dict[str, Any]:
        return {"bot_id": bot_id, "workspace_id": self.workspace_id, "profile": None}

    def list(self) -> list[dict[str, Any]]:
        return [self.get(BOT)]
