"""Pins the SDK wire: the initialize request and the user frames the client writes, so an SDK
upgrade that changes the wire fails here first.

A regression here means the SDK version under test built the wire differently from what the
rest of the app (mapper, fixtures, docs) assumes. If this fails on the pinned SDK version, stop
and report the frame's keys instead of adjusting the assertion.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

from tests.test_driver_limits import make_driver
from viki_agent.drivers.claude.control import ClaudeControl, Launch, build_argv
from viki_agent.drivers.claude.driver import ClaudeDriver, DriverConfig
from viki_agent.drivers.claude.transport import ClaudeTransport
from viki_agent.messaging.bot_fence import (
    TAINT_MATCHER,
    WRITE_MATCHER,
    FenceRoots,
    WriteFence,
)
from viki_agent.turn_input import Attachment, TurnInput

from replay import SID, FakeSpawner, init_frame, out, result_frame, user_in

FAST: dict[str, Any] = {"grace_s": 0.05, "poll_s": 0.05}
STEER_ID = "22222222-2222-4222-8222-222222222222"
ATTACHMENT = Attachment(
    "00000000-0000-4000-8000-000000000012", "image/png", 4, 1, 1, "synthetic.png"
)


async def _png(value: Attachment) -> bytes:
    return b"\x89PNG"


async def _allow(
    tool: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    return PermissionResultAllow()


def _control(
    spawner: FakeSpawner,
    sink: Callable[[dict[str, Any]], None],
) -> tuple[ClaudeControl, ClaudeTransport]:
    argv = build_argv(["claude.exe"], Launch(SID), mode="auto", effort="low")
    transport = ClaudeTransport(
        argv,
        cwd=Path.cwd(),
        env={},
        sink=lambda frame, size: sink(frame),
        spawner=spawner,
        **FAST,
    )
    return ClaudeControl(transport, _allow, control_timeout_s=0.05), transport


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys |= set(value.keys())
        for v in value.values():
            keys |= _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            keys |= _walk_keys(v)
    return keys


async def test_initialize_request_is_exactly_subtype_hooks_and_the_stop_declaration() -> None:
    # `perTaskStopAffordance` is ours, not the SDK's: ClaudeTransport.write declares it, so an
    # interrupt (the chat's Stop) spares background agents.
    spawner = FakeSpawner([])
    control, _ = _control(spawner, lambda frame: None)
    await control.start()
    seen = spawner.children[0].process.replayer.seen
    assert len(seen) == 1
    assert seen[0]["type"] == "control_request"
    assert seen[0]["request"] == {
        "subtype": "initialize",
        "hooks": None,
        "perTaskStopAffordance": True,
    }
    await control.close()


async def test_a_bot_driver_sends_its_system_prompt_in_initialize_and_no_flag() -> None:
    # ADR-084: a Bot session's instructions and memory rule ride initialize's
    # `appendSystemPrompt`, so argv holds no system-prompt element and no user text.
    prompt = "Be brief.\n\nMemory rule."
    settings = Path.cwd() / "bot-memory-settings" / f"{'a' * 64}.json"
    spawner = FakeSpawner([])
    driver = ClaudeDriver(
        Launch(SID, bot_memory_settings=str(settings), append_system_prompt=prompt),
        mode="auto",
        effort="low",
        model=None,
        config=DriverConfig(
            cli=("claude.exe",),
            vault=Path.cwd(),
            env={},
            spawner=spawner,
            grace_s=0.01,
            poll_s=0.01,
            control_timeout_s=0.05,
        ),
        emit=lambda event: None,
    )
    try:
        await driver.open()
        seen = spawner.children[0].process.replayer.seen
        assert seen[0]["request"] == {
            "subtype": "initialize",
            "hooks": None,
            "perTaskStopAffordance": True,
            "appendSystemPrompt": prompt,
        }
        argv = spawner.children[0].argv
        assert not [arg for arg in argv if "system-prompt" in arg or "Be brief" in arg]
    finally:
        await driver.close()


async def test_a_bot_driver_names_its_write_fence_hooks_in_initialize() -> None:
    # ADR-089: the fence's two PreToolUse hooks ride the SDK's own initialize request as
    # callback ids; nothing reaches argv, and the transport's own amendments stay as they were.
    settings = Path.cwd() / "bot-memory-settings" / f"{'a' * 64}.json"
    fence = WriteFence(
        FenceRoots(Path.cwd(), Path.cwd(), Path.cwd() / "data" / "bot-memory"),
        tainted=lambda: False,
        mark=lambda _source: None,
    )
    spawner = FakeSpawner([])
    driver = ClaudeDriver(
        Launch(SID, bot_memory_settings=str(settings), hooks=fence.hooks()),
        mode="auto",
        effort="low",
        model=None,
        config=DriverConfig(
            cli=("claude.exe",),
            vault=Path.cwd(),
            env={},
            spawner=spawner,
            grace_s=0.01,
            poll_s=0.01,
            control_timeout_s=0.05,
        ),
        emit=lambda event: None,
    )
    try:
        await driver.open()
        request = spawner.children[0].process.replayer.seen[0]["request"]
        assert request["perTaskStopAffordance"] is True
        matchers = request["hooks"]["PreToolUse"]
        assert [m["matcher"] for m in matchers] == [TAINT_MATCHER, WRITE_MATCHER]
        assert all(len(m["hookCallbackIds"]) == 1 for m in matchers)
        assert set(request) == {"subtype", "hooks", "perTaskStopAffordance"}
    finally:
        await driver.close()


async def test_text_turn_user_frame_has_exactly_the_expected_keys() -> None:
    script = [user_in(), out(init_frame()), out(result_frame())]
    spawner = FakeSpawner(script)
    control, _ = _control(spawner, lambda frame: None)
    await control.start()
    await control.send("hello", SID)
    seen = spawner.children[0].process.replayer.seen
    user_frames = [f for f in seen if f.get("type") == "user"]
    assert len(user_frames) == 1
    assert user_frames[0] == {
        "type": "user",
        "message": {"role": "user", "content": "hello"},
        "parent_tool_use_id": None,
        "session_id": SID,
    }
    await control.close()


async def test_image_turn_user_frame_has_exactly_the_expected_keys() -> None:
    spawner = FakeSpawner([])
    control, _ = _control(spawner, lambda frame: None)
    await control.start()
    attachment = Attachment(
        "00000000-0000-4000-8000-000000000012", "image/png", 4, 1, 1, "synthetic.png"
    )

    async def resolve(value: Attachment) -> bytes:
        return b"\x89PNG"

    await control.send(TurnInput("describe", (attachment,)), SID, resolve)
    seen = spawner.children[0].process.replayer.seen
    user_frames = [f for f in seen if f.get("type") == "user"]
    assert len(user_frames) == 1
    frame = user_frames[0]
    assert set(frame.keys()) == {"type", "message", "session_id"}
    content = frame["message"]["content"]
    assert content[0]["type"] == "text"
    assert [block["type"] for block in content[1:]] == ["image"]
    assert content[1]["source"]["type"] == "base64"
    await control.close()


async def test_text_steer_user_frame_has_exactly_the_expected_keys() -> None:
    spawner = FakeSpawner([])
    control, _ = _control(spawner, lambda frame: None)
    await control.start()
    await control.send("hello", SID, steer_id=STEER_ID)
    seen = spawner.children[0].process.replayer.seen
    assert [f for f in seen if f.get("type") == "user"] == [
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "parent_tool_use_id": None,
            "priority": "next",
            "uuid": STEER_ID,
            "session_id": SID,
        }
    ]
    await control.close()


async def test_image_steer_user_frame_has_exactly_the_expected_keys() -> None:
    spawner = FakeSpawner([])
    control, _ = _control(spawner, lambda frame: None)
    await control.start()
    await control.send(TurnInput("describe", (ATTACHMENT,)), SID, _png, steer_id=STEER_ID)
    seen = spawner.children[0].process.replayer.seen
    user_frames = [f for f in seen if f.get("type") == "user"]
    assert len(user_frames) == 1
    frame = user_frames[0]
    assert set(frame.keys()) == {
        "type",
        "message",
        "parent_tool_use_id",
        "priority",
        "uuid",
        "session_id",
    }
    steer = (frame["parent_tool_use_id"], frame["priority"], frame["uuid"], frame["session_id"])
    assert steer == (None, "next", STEER_ID, SID)
    content = frame["message"]["content"]
    assert content[0] == {"type": "text", "text": "describe"}
    assert [block["type"] for block in content[1:]] == ["image"]
    assert content[1]["source"]["type"] == "base64"
    await control.close()


async def test_a_steer_while_busy_leaves_busy_true() -> None:
    driver, spawner, _ = make_driver([user_in(), out(init_frame()), user_in()])
    try:
        await driver.open()
        await driver.send("synthetic")
        await driver.steer(TurnInput("go"), STEER_ID)
        assert driver.busy
        seen = spawner.children[0].process.replayer.seen
        steers = [f for f in seen if f.get("type") == "user" and "priority" in f]
        assert [(f["priority"], f["uuid"]) for f in steers] == [("next", STEER_ID)]
    finally:
        await driver.close()


async def test_no_client_written_frame_carries_a_client_composed_key() -> None:
    script = [user_in(), out(init_frame()), out(result_frame())]
    spawner = FakeSpawner(script)
    control, _ = _control(spawner, lambda frame: None)
    await control.start()
    await control.send("hello", SID)
    seen = spawner.children[0].process.replayer.seen
    for frame in seen:
        assert "client_composed" not in _walk_keys(frame)
    await control.close()
