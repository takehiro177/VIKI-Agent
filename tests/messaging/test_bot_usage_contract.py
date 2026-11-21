"""The Bot usage wire on the assembled app: a Bot chat's turn end logs its transcript's calls, and
the summary and trace routes answer exactly the golden the browser's parsers read too
(`frontend/src/clients/messaging/botUsage.contract.json`)."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.messaging.test_bot_integration import Integration, ident
from tests.messaging.test_usage_reader import TRANSCRIPT_FIXTURE
from tests.profiles.test_profile_launch_api import AUTH, HANG_GUARD_S
from viki_agent import server
from viki_agent.automation.routines import stamp
from viki_agent.catalog import project_dir
from viki_agent.events import TurnDone, Usage
from viki_agent.session import SessionActor

GOLDEN = (
    Path(__file__).parents[2]
    / "frontend"
    / "src"
    / "clients"
    / "messaging"
    / "botUsage.contract.json"
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _normalized(answer: dict[str, Any]) -> Any:
    """`answer` with each distinct UUID, in order of first appearance, replaced by
    00000000-0000-4000-8000-0000000000NN."""
    seen: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        return seen.setdefault(match[0], f"00000000-0000-4000-8000-{len(seen) + 1:012d}")

    return json.loads(_UUID.sub(replace, json.dumps(answer)))


def _calls(stack: Integration) -> int:
    with stack.rig.state.store._connection() as connection:
        return connection.execute("SELECT count(*) FROM bot_usage_calls").fetchone()[0]


async def _turn(stack: Integration, actor: SessionActor, at: str) -> None:
    """One turn of `actor`'s chat to `turn.done`. Before the end, its transcript becomes the
    fixture's lines with the chat's id and every timestamp `at`, as Claude Code writes a turn's
    lines before the turn ends."""
    stack.script = ()  # the driver writes its own transcript and does not end the turn
    assert await actor.send("Synthetic owner message")
    lines = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8").splitlines()
    rows = [{**json.loads(line), "sessionId": actor.session_id, "timestamp": at} for line in lines]
    path = project_dir(stack.rig.home, stack.rig.vault) / f"{actor.session_id}.jsonl"
    text = "".join(json.dumps(row) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8", newline="\n")
    [driver] = [item for item in stack.rig.drivers if item.launch.session_id == actor.session_id]
    driver.emit(TurnDone(status="success", usage=Usage()))


async def test_a_bot_turn_logs_its_calls_and_the_usage_routes_answer_the_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workers: list[set[asyncio.Task[Any]]] = []
    real = server.turn_end_capture

    def recording(views: Any, tasks: set[asyncio.Task[Any]]) -> Callable[[str], None]:
        workers.append(tasks)
        return real(views, tasks)

    monkeypatch.setattr(server, "turn_end_capture", recording)
    stack = Integration(tmp_path, monkeypatch)
    [bot_workers] = workers
    # Noon UTC for the clock and every call: one local date in each zone from UTC-12 to UTC+11.
    stack.clock.advance(12 * 60 * 60, monotonic=False)
    at = stamp(stack.clock.wall())
    async with stack.app.router.lifespan_context(stack.app), stack.client:
        bot = await stack.create()
        sid = (await stack.action(bot, "chat")).json()["preview"]["session_id"]
        chat = stack.manager.get(sid)
        assert chat is not None
        await _turn(stack, chat, at)
        await asyncio.wait_for(asyncio.gather(*bot_workers), HANG_GUARD_S)
        assert _calls(stack) == 3
        await _turn(stack, stack.manager.create(ident()), at)  # an ordinary chat
        await asyncio.wait_for(asyncio.gather(*bot_workers), HANG_GUARD_S)
        assert _calls(stack) == 3
        summary = await stack.client.get("/api/v2/bot-usage/summary/30", headers=AUTH)
        trace = await stack.client.get(f"/api/v2/bot-usage/sessions/{sid}", headers=AUTH)
    assert summary.status_code == trace.status_code == 200, (summary.text, trace.text)
    answer = _normalized({"summary": summary.json(), "trace": trace.json()})
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert answer == golden, "normalized answer:\n" + json.dumps(answer, indent=2)
