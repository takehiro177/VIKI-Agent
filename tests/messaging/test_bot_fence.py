"""The Bot write fence: which writes it refuses, in which sessions, and how its hooks behave."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest
from claude_agent_sdk import HookContext, HookInput

from viki_agent.drivers.claude.control import Launch, build_argv
from viki_agent.messaging import bot_fence
from viki_agent.messaging.bot_fence import (
    MSG_BOT_MEMORY,
    MSG_FAILED,
    MSG_RECORDS,
    MSG_TAINTED,
    FenceRoots,
    WriteFence,
    fence_reason,
)
from viki_agent.messaging.bot_taint import join_sources, taint_source


@pytest.fixture
def roots(tmp_path: Path) -> FenceRoots:
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    return FenceRoots(cwd=vault, vault=vault, app_memory=tmp_path / "data" / "bot-memory")


def _write(path: Path | str) -> dict[str, Any]:
    return {"file_path": str(path), "content": "x"}


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_bot_memory_and_records_are_refused_in_every_session(roots: FenceRoots, tool: str) -> None:
    vault = roots.vault
    for tainted in (False, True):
        assert (
            fence_reason(tool, _write(vault / "bot-memory" / "b" / "x.md"), roots, tainted=tainted)
            == MSG_BOT_MEMORY
        )
        assert (
            fence_reason(
                tool, _write(roots.app_memory / "w" / "b" / "MEMORY.md"), roots, tainted=tainted
            )
            == MSG_BOT_MEMORY
        )
        assert (
            fence_reason(tool, _write(vault / "memory" / "r.md"), roots, tainted=tainted)
            == MSG_RECORDS
        )
        assert fence_reason(tool, _write(vault / "notes" / "a.md"), roots, tainted=tainted) is None


def test_a_relative_path_and_a_respelled_folder_are_judged_where_they_land(
    roots: FenceRoots,
) -> None:
    assert fence_reason("Write", _write("Bot-Memory/b/x.md"), roots, tainted=False) == (
        MSG_BOT_MEMORY
    )
    assert fence_reason("Write", _write("notes/../memory/r.md"), roots, tainted=False) == (
        MSG_RECORDS
    )
    assert (
        fence_reason("NotebookEdit", {"notebook_path": "memory/n.ipynb"}, roots, tainted=False)
        == MSG_RECORDS
    )


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_link_into_bot_memory_is_followed(roots: FenceRoots) -> None:
    target = roots.vault / "bot-memory"
    target.mkdir()
    (roots.vault / "notes" / "alias").symlink_to(target, target_is_directory=True)
    assert fence_reason("Write", _write("notes/alias/x.md"), roots, tainted=False) == (
        MSG_BOT_MEMORY
    )


@pytest.mark.parametrize(
    "ref",
    [
        "system/memory/lessons.md",
        ".claude/skills/kanban/SKILL.md",
        "projects/x/.Claude/settings.json",
        "CLAUDE.md",
        "deep/folder/agents.md",
        "AGENTS.override.md",
        "GEMINI.md",
        "CLAUDE.local.md",
    ],
)
def test_a_tainted_session_cannot_change_instructions_skills_or_learned_memory(
    roots: FenceRoots, ref: str
) -> None:
    assert fence_reason("Edit", _write(roots.vault / ref), roots, tainted=False) is None
    assert fence_reason("Edit", _write(roots.vault / ref), roots, tainted=True) == MSG_TAINTED


def test_other_tools_and_shapes_pass(roots: FenceRoots) -> None:
    assert (
        fence_reason("Read", _write(roots.vault / "memory" / "r.md"), roots, tainted=True) is None
    )
    assert fence_reason("Bash", {"command": "echo > memory/r.md"}, roots, tainted=True) is None
    assert fence_reason("Write", {"content": "x"}, roots, tainted=True) is None
    assert fence_reason("Write", {"file_path": 3}, roots, tainted=True) is None


def test_taint_sources_and_their_stored_spelling() -> None:
    assert taint_source("WebFetch") == taint_source("WebSearch") == "web"
    assert taint_source("mcp__viki_notes__search") == "mcp"
    assert taint_source("Read") is None and taint_source(None) is None
    assert join_sources(["mcp", "web", "web"]) == "web,mcp"
    with pytest.raises(ValueError):
        join_sources([])


class _Taint:
    def __init__(self) -> None:
        self.stored: list[str] = []
        self.fail = False

    def tainted(self) -> bool:
        if self.fail:
            raise OSError("store")
        return bool(self.stored)

    def mark(self, source: str) -> None:
        if self.fail:
            raise OSError("store")
        self.stored.append(source)


def _input(tool: str, tool_input: dict[str, Any]) -> HookInput:
    return cast(
        HookInput,
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "tool_use_id": "toolu_1",
            "session_id": "s",
            "transcript_path": "t",
            "cwd": ".",
        },
    )


_CONTEXT = cast(HookContext, {"signal": None})


def _decision(output: object) -> dict[str, Any]:
    return cast(dict[str, Any], output)["hookSpecificOutput"]


async def test_the_hooks_record_taint_first_then_fence_on_it(roots: FenceRoots) -> None:
    taint = _Taint()
    fence = WriteFence(roots, tainted=taint.tainted, mark=taint.mark)
    hooks = fence.hooks()["PreToolUse"]
    assert [m.matcher for m in hooks] == [bot_fence.TAINT_MATCHER, bot_fence.WRITE_MATCHER]

    skill = _write(roots.vault / ".claude" / "skills" / "x" / "SKILL.md")
    assert await fence.fence(_input("Write", skill), "t1", _CONTEXT) == {}
    assert await fence.observe(_input("WebFetch", {"url": "https://x"}), "t2", _CONTEXT) == {}
    assert taint.stored == ["web"]
    denied = _decision(await fence.fence(_input("Write", skill), "t3", _CONTEXT))
    assert denied == {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": MSG_TAINTED,
    }


async def test_a_store_fault_keeps_the_taint_in_memory_and_refuses_writes(
    roots: FenceRoots,
) -> None:
    taint = _Taint()
    taint.fail = True
    fence = WriteFence(roots, tainted=taint.tainted, mark=taint.mark)
    assert await fence.observe(_input("mcp__x__y", {}), "t1", _CONTEXT) == {}
    assert fence.is_tainted()  # held in this process though the store refused it
    fresh = WriteFence(roots, tainted=taint.tainted, mark=taint.mark)
    note = _input("Write", _write(roots.vault / "notes" / "a.md"))
    denied = _decision(await fresh.fence(note, "t", _CONTEXT))
    assert denied["permissionDecisionReason"] == MSG_FAILED


def test_hooks_ride_only_a_bot_launch(tmp_path: Path) -> None:
    settings = tmp_path / "bot-memory-settings" / ("a" * 64 + ".json")
    hooks = WriteFence(
        FenceRoots(tmp_path, tmp_path, tmp_path / "m"), tainted=lambda: False, mark=lambda _s: None
    ).hooks()
    sid = "6c1a4f2e-0d6b-4c8b-9b1e-2f3a4b5c6d7e"
    argv = build_argv(
        ["claude"],
        Launch(sid, bot_memory_settings=str(settings), hooks=hooks),
        mode="default",
        effort="low",
    )
    # No element of their own: the SDK names them in initialize.
    assert argv == build_argv(
        ["claude"], Launch(sid, bot_memory_settings=str(settings)), mode="default", effort="low"
    )
    with pytest.raises(ValueError, match="app hooks"):
        build_argv(["claude"], Launch(sid, hooks=hooks), mode="default", effort="low")
    assert "hooks" not in repr(Launch(sid, hooks=hooks))
