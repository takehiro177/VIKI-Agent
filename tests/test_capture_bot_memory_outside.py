"""Scenario 41's offline gates and observations; no real Claude process, provider or network.

The scenario asks whether Claude Code saves to, indexes, recalls from and resumes with a Bot
memory folder outside its working folder. Here a stand-in plays Claude Code in the folder the
launch's `--settings` file names."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import capture  # noqa: E402

from viki_agent.messaging.bot_memory import (  # noqa: E402
    app_memory_directory,
    instruction_excludes,
)

CANARY = re.compile(r"BOT_OUTSIDE_[0-9a-f]{32}")
TURNS = 5
SAVE_TURNS = (1, 2)  # acceptEdits save of C1, default-mode save of C3
GATES = {
    "folder_absent_before",
    "folder_created",
    "saved_in_folder",
    "index_written",
    "fresh_recalled",
    "turns_succeeded",
}
REPORTED = {
    "saved_in_working_folder": False,
    "default_mode_save_landed": True,
    "default_mode_asked": True,
    "fresh_recalled_default_save": True,
    "resume_recalled_own": True,
    "resume_recalled_previous": False,
}
EXPECTED = {**dict.fromkeys(GATES, True), **REPORTED}
# One request the stand-in makes: its tool, where its path points, and the record the scenario
# must keep for it (inside the turn's own Bot folder, allowed).
Request = tuple[str, str, bool, bool]
BASE: dict[int, list[Request]] = {
    1: [("Write", "folder", True, True)],
    2: [("Write", "folder", True, True)],
    3: [("Read", "folder", True, True)],
    4: [],
    5: [("Read", "folder", True, True)],
}
# Each mode: the turns whose requests differ from BASE, one thing the stand-in does differently,
# and the observations that differ from EXPECTED.
FIRST_SAVE_FAILS = {
    "folder_created": False,
    "saved_in_folder": False,
    "index_written": False,
    "fresh_recalled": False,
}
MODES: dict[str, tuple[dict[int, list[Request]], str, dict[str, bool]]] = {
    "ok": ({}, "", {}),
    "no_ask": ({1: [], 2: [], 3: [], 5: []}, "", {"default_mode_asked": False}),
    "default_save_without_an_ask": ({2: []}, "", {"default_mode_asked": False}),
    # Claude Code saves the topic file only and never writes MEMORY.md: no index, so no recall.
    "no_index": (
        {},
        "no_index",
        {
            "index_written": False,
            "fresh_recalled": False,
            "fresh_recalled_default_save": False,
            "resume_recalled_own": False,
        },
    ),
    "default_save_asks_outside": (
        {2: [("Write", "working", False, False)]},
        "",
        {"default_mode_save_landed": False, "fresh_recalled_default_save": False},
    ),
    "bash_then_write": (
        {1: [("Bash", "command", False, False), ("Write", "folder", True, True)]},
        "",
        {},
    ),
    "write_in_a_read_turn": ({3: [("Write", "folder", True, False)]}, "", {}),
    "glob_without_a_path": ({3: [("Glob", "none", False, False)]}, "", {}),
    "read_the_other_bot": ({5: [("Read", "other_bot", False, False)]}, "", {}),
    "write_in_working_folder": ({1: [("Write", "working", False, False)]}, "", FIRST_SAVE_FAILS),
    "write_relative": ({1: [("Write", "relative", False, False)]}, "", FIRST_SAVE_FAILS),
    "write_dotdot": ({1: [("Write", "dotdot", False, False)]}, "", FIRST_SAVE_FAILS),
    "write_sibling": ({1: [("Write", "sibling", False, False)]}, "", FIRST_SAVE_FAILS),
    "silent_save_in_working_folder": (
        {1: [], 2: []},
        "save_in_working_folder",
        {
            **FIRST_SAVE_FAILS,
            "saved_in_working_folder": True,
            "default_mode_save_landed": False,
            "default_mode_asked": False,
            "fresh_recalled_default_save": False,
            "resume_recalled_own": False,
        },
    ),
    "no_recall": (
        {},
        "no_recall",
        {"fresh_recalled": False, "fresh_recalled_default_save": False},
    ),
    # "ok" is a resume that recalls its own canaries only.
    "resume_previous_only": (
        {},
        "resume_previous_only",
        {"resume_recalled_own": False, "resume_recalled_previous": True},
    ),
    "resume_both": ({}, "resume_both", {"resume_recalled_previous": True}),
    "resume_neither": ({}, "resume_neither", {"resume_recalled_own": False}),
    "turn_error": ({}, "turn_error", {"turns_succeeded": False}),
}


class _Claude:
    """Stands in for Capture around the five turns: checks what exists before each open, then
    plays Claude Code in the memory folder the launch names. `does` names one deviation."""

    def __init__(self, tmp_path: Path, mode: str) -> None:
        self.run_dir = tmp_path
        self.root = tmp_path / "scratch"
        self.root.mkdir()
        self.cfg = SimpleNamespace(cli=tmp_path / "bin" / "claude")
        self.requests, self.does, _ = MODES[mode]
        self.launches: list[Any] = []
        self.modes: list[str] = []
        self.argvs: list[list[str]] = []
        self.closed = 0
        self.folder_b = Path()

    async def open(self, _rec: Any, launch: Any, *, mode: str) -> Any:
        settings = Path(launch.bot_memory_settings)
        capture.protection.require_private_file(settings)
        folder = Path(json.loads(settings.read_text(encoding="ascii"))["autoMemoryDirectory"])
        turn = len(self.launches) + 1
        if turn == 1:  # Claude Code alone creates the folder: not even its parents exist
            assert not folder.parent.parent.exists()
        if turn == 4:  # the harness seeded Bot B's folder after the first three turns
            self.folder_b = folder
            assert CANARY.search((folder / "MEMORY.md").read_text(encoding="utf-8"))
        self.launches.append(launch)
        self.modes.append(mode)
        self.argvs.append(
            capture.build_argv([str(self.cfg.cli)], launch, mode=mode, effort="low", model="sonnet")
        )
        return SimpleNamespace(folder=folder)

    def _input(self, where: str, folder: Path) -> dict[str, Any]:
        if where == "command":
            return {"command": f"mkdir {folder}"}
        if where == "none":
            return {"pattern": "*.md"}
        path = {
            "folder": folder / "MEMORY.md",
            "working": self.root / "MEMORY.md",
            "relative": Path("MEMORY.md"),
            "dotdot": folder / ".." / "MEMORY.md",
            "sibling": Path(f"{folder}-copy") / "MEMORY.md",
            "other_bot": self.folder_b / "MEMORY.md",
        }[where]
        return {"file_path": str(path)}

    def _save(self, folder: Path, canary: str) -> None:
        """Save `canary` in a topic file and, unless `does` says Claude Code skips it, index it."""
        target = self.root if self.does == "save_in_working_folder" else folder
        target.mkdir(parents=True, exist_ok=True)
        name = "synthetic_canary.md" if self.does == "no_index" else "MEMORY.md"
        with (target / name).open("a", encoding="utf-8") as note:
            note.write(f"- {canary}\n")

    async def turn(self, live: Any, text: str, *, on_ask: Any) -> list[dict[str, Any]]:
        turn, folder = len(self.launches), live.folder
        saved = CANARY.search(text)
        assert (saved is not None) is (turn in SAVE_TURNS)  # only the save prompts hold one
        requests = self.requests.get(turn, BASE[turn])
        allowed: list[tuple[str, str]] = []
        for tool, where, _inside, _allowed in requests:
            future = capture.asyncio.get_running_loop().create_future()
            context = capture.ToolPermissionContext(tool_use_id="toolu_0")
            ask = capture.Ask(tool, self._input(where, folder), context, future)
            if isinstance(on_ask(ask), capture.PermissionResultAllow):
                allowed.append((tool, where))
        if saved and (not requests or ("Write", "folder") in allowed):
            self._save(folder, saved[0])
        index = folder / "MEMORY.md"  # Claude Code loads only MEMORY.md at start
        said = "ready" if turn == 4 else "absent"
        if turn == 3 and self.does != "no_recall" and index.is_file():
            said = index.read_text(encoding="utf-8")
        if turn == 5:
            own = self.does not in ("resume_neither", "resume_previous_only")
            if own and index.is_file():
                said = index.read_text(encoding="utf-8")
            if self.does in ("resume_both", "resume_previous_only"):
                said += (self.folder_b / "MEMORY.md").read_text(encoding="utf-8")
        subtype = "error_during_execution" if self.does == "turn_error" and turn == 4 else "success"
        return [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": said}]}},
            {"type": "result", "subtype": subtype},
        ]

    async def close(self, _live: Any) -> None:
        self.closed += 1


def _shape(argv: list[str], cli: Path) -> list[str]:
    """`argv` with the CLI, each session id and each settings path as its placeholder."""
    out = []
    for arg in argv:
        name = arg.split("=", 1)[0]
        if arg == str(cli):
            out.append("<cli>")
        elif name in ("--session-id", "--resume"):
            out.append(f"{name}=<session-id>")
        elif name == "--settings":
            out.append("--settings=<bot-memory-settings>")
        else:
            out.append(arg)
    return out


def test_41_is_named_only() -> None:
    assert "41" in capture.SCENARIOS
    assert "41" in capture.NAMED_ONLY
    assert "41" not in capture.REFUSED
    assert "paid" in capture.NAMED_ONLY["41"] and "recorded go" in capture.NAMED_ONLY["41"]


async def test_41_settings_name_an_app_folder_outside_the_working_folder_before_any_provider(
    tmp_path: Path,
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    opened: list[tuple[Any, dict[str, Any]]] = []

    async def stop_before_provider(_rec: object, launch: Any, **kwargs: Any) -> None:
        opened.append((launch, kwargs))
        raise RuntimeError("provider deliberately absent")

    open_session = AsyncMock(side_effect=stop_before_provider)
    cfg = SimpleNamespace(cli=tmp_path / "bin" / "claude")
    cap = cast(
        capture.Capture, SimpleNamespace(root=root, run_dir=tmp_path, cfg=cfg, open=open_session)
    )
    with pytest.raises(RuntimeError, match="provider deliberately absent"):
        await capture.s41(cap, cast(capture.Recorder, None))
    open_session.assert_awaited_once()

    [(launch, kwargs)] = opened
    assert kwargs == {"mode": "acceptEdits"}
    assert launch.resume is False and not launch.add_dirs and launch.mcp_config is None
    settings = Path(launch.bot_memory_settings)
    data = tmp_path / "data"
    assert settings.parent == data / "bot-memory-settings"
    capture.protection.require_private_directory(settings.parent)
    capture.protection.require_private_file(settings)
    document = json.loads(settings.read_text(encoding="ascii"))
    # The memory is outside the working folder, whose instruction files are named all the same.
    assert document["claudeMdExcludes"] == instruction_excludes(root)
    assert set(document) == {"autoMemoryDirectory", "autoMemoryEnabled", "claudeMdExcludes"}
    assert document["autoMemoryEnabled"] is False
    folder = Path(document["autoMemoryDirectory"])
    assert folder == app_memory_directory(data, folder.parent.name, folder.name)
    assert not folder.is_relative_to(root) and not data.is_relative_to(root)
    assert not (data / "bot-memory").exists()  # Claude Code alone creates it
    argv = capture.build_argv([str(cfg.cli)], launch, mode="acceptEdits", effort="low")
    assert [arg for arg in argv if arg.startswith("--settings")] == [f"--settings={settings}"]


@pytest.mark.parametrize("mode", list(MODES))
async def test_41_observations(mode: str, tmp_path: Path) -> None:
    cap = _Claude(tmp_path, mode)
    result = await capture.s41(cast(capture.Capture, cap), capture._NoRawRecorder())

    expected = {**EXPECTED, **MODES[mode][2]}
    assert {key: result[key] for key in expected} == expected
    assert result["pass"] is all(expected[key] for key in GATES)  # report-only keys never gate
    assert set(result) == {"pass", "turns", *expected}
    assert cap.closed == TURNS

    turns = result["turns"]
    assert [(t["mode"], t["resume"]) for t in turns] == [
        ("acceptEdits", False),
        ("default", False),
        ("default", False),
        ("default", False),
        ("default", True),
    ]
    for number, turn in enumerate(turns, start=1):
        planned = MODES[mode][0].get(number, BASE[number])
        assert [(a["tool"], a["inside_folder"], a["allowed"]) for a in turn["asks"]] == [
            (tool, inside, allowed) for tool, _where, inside, allowed in planned
        ]
        assert turn["success"] is (not (mode == "turn_error" and number == 4))
        assert turn["argv"] == _shape(cap.argvs[number - 1], cap.cfg.cli)
        assert sum(arg.startswith("--settings=") for arg in turn["argv"]) == 1
        assert not any(arg.startswith(("--add-dir", "--mcp-config")) for arg in turn["argv"])

    ids = [launch.session_id for launch in cap.launches]
    assert ids[3] == ids[4] and len(set(ids)) == 4  # turn 5 resumes turn 4's session
    assert [launch.resume for launch in cap.launches] == [False] * 4 + [True]
    settings = [launch.bot_memory_settings for launch in cap.launches]
    assert settings[0] == settings[1] == settings[2] == settings[4] != settings[3]  # A, A, A, B, A

    text = json.dumps(result)
    assert tmp_path.name not in text and "MEMORY.md" not in text
    assert "BOT_OUTSIDE_" not in text and ids[0] not in text
