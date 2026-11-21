"""App shutdown stops the Bot usage capture before it closes the chats, so a capture started once
shutdown has begun reads no transcript and writes nothing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.messaging.test_bot_integration import Integration
from tests.messaging.test_usage_reader import TRANSCRIPT_FIXTURE
from viki_agent import server
from viki_agent.catalog import project_dir
from viki_agent.messaging import bot_usage
from viki_agent.messaging.bot_usage import BotUsage, Capture
from viki_agent.messaging.bot_usage_api import UsageViews


async def test_shutdown_stops_the_usage_capture_before_closing_the_chats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    views: list[UsageViews] = []
    reads: list[Path] = []
    turn_end_capture = server.turn_end_capture
    stop, read_calls = BotUsage.stop, bot_usage.read_calls

    def recording(usage_views: UsageViews, tasks: set[asyncio.Task[Any]]) -> Callable[[str], None]:
        views.append(usage_views)
        return turn_end_capture(usage_views, tasks)

    def stopping(usage: BotUsage) -> None:
        order.append("stop")
        stop(usage)

    def reading(path: Path, *args: Any, **kwargs: Any) -> Any:
        reads.append(path)
        return read_calls(path, *args, **kwargs)

    monkeypatch.setattr(server, "turn_end_capture", recording)
    monkeypatch.setattr(BotUsage, "stop", stopping)
    monkeypatch.setattr(bot_usage, "read_calls", reading)
    stack = Integration(tmp_path, monkeypatch)
    [usage_views] = views
    started: list[Capture] = []
    close_all = stack.manager.close_all

    async def closing() -> None:
        order.append("close_all")
        started.append(usage_views.usage.capture(stack.rig.workspace_id))
        await close_all()

    monkeypatch.setattr(stack.manager, "close_all", closing)
    async with stack.app.router.lifespan_context(stack.app), stack.client:
        chat = (await stack.create())["chat_session_id"]
        # The Bot chat's transcript holds the fixture's calls, none of them logged yet.
        path = project_dir(stack.rig.home, stack.rig.vault) / f"{chat}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(TRANSCRIPT_FIXTURE.read_bytes())
        assert stack.rig.catalog.transcript_path(chat) == path
    assert order == ["stop", "close_all"]
    assert started == [Capture(frozenset(), 0, 0)] and reads == []
    with stack.rig.state.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM bot_usage_calls").fetchone() == (0,)
