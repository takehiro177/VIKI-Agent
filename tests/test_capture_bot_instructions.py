"""Scenario 43's offline gates and observations; no real Claude process, provider or network.

The scenario asks whether Claude Code skips a vault's CLAUDE.md, and the AGENTS.md it imports,
when a Bot's `--settings` file names them in `claudeMdExcludes`. Here a stand-in plays Claude Code
in the folder each launch starts in: it loads CLAUDE.md and its import, or a lone AGENTS.md, unless
a pattern matches, and answers with the code word it loaded."""

from __future__ import annotations

import glob
import json
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import capture  # noqa: E402

from viki_agent.messaging.bot_memory import instruction_excludes  # noqa: E402

CANARY = re.compile(r"VAULT_RULES_[0-9a-f]{32}")
STEPS = ["control", "excluded", "agents_only_control", "agents_only_excluded"]
MEMORY_ONLY = ["autoMemoryDirectory"]
BOTH = ["autoMemoryDirectory", "autoMemoryEnabled", "claudeMdExcludes"]
SETTINGS_KEYS = {
    "control": MEMORY_ONLY,
    "excluded": BOTH,
    "agents_only_control": MEMORY_ONLY,
    "agents_only_excluded": BOTH,
}
GATES = {
    "control_loaded",
    "excluded_skipped",
    "excluded_replied_none",
    "agents_only_loaded",
    "agents_only_skipped",
}
EXPECTED: dict[str, bool] = dict.fromkeys(GATES, True)
# Each mode: what the stand-in does differently, and the observations that differ from EXPECTED.
MODES: dict[str, dict[str, bool]] = {
    "ok": {},
    "ignores_excludes": {
        "excluded_skipped": False,
        "excluded_replied_none": False,
        "agents_only_skipped": False,
    },
    "fallback_ignores_excludes": {"agents_only_skipped": False},
    "no_fallback": {"agents_only_loaded": False},
    "control_reads_a_file": {"control_loaded": False},
    "excluded_reads_a_file": {"excluded_skipped": False, "excluded_replied_none": False},
    "transcript_holds_it": {"excluded_skipped": False},
    "no_transcript": {},
    "says_no_word": {"excluded_replied_none": False},
    "excluded_errors": {"excluded_skipped": False},
}


def _matched(patterns: list[str], path: Path) -> bool:
    """Claude Code's matcher, standing in: both sides with slashes, dot files included."""
    spelled = str(path).replace("\\", "/")
    return any(
        re.match(
            glob.translate(
                pattern.replace("\\", "/"), recursive=True, include_hidden=True, seps="/"
            ),
            spelled,
        )
        for pattern in patterns
    )


class _Claude:
    """Stands in for Capture: records each launch and its folder, then plays Claude Code there
    with the settings file the launch names. `mode` names one deviation."""

    def __init__(self, tmp_path: Path, mode: str) -> None:
        # A short folder: a transcript's path repeats its vault's path, which must stay under
        # Windows MAX_PATH even beneath pytest's default basetemp.
        self.run_dir = Path(tempfile.mkdtemp(dir=tmp_path.parent, prefix="c"))
        self.root = self.run_dir / "scratch"
        self.root.mkdir()
        self.cfg = SimpleNamespace(cli=self.run_dir / "claude", claude_home=self.run_dir / "h")
        self.mode = mode
        self.launches: list[Any] = []
        self.cwds: list[Path] = []
        self.argvs: list[list[str]] = []
        self.closed = 0

    async def open(self, _rec: Any, launch: Any, *, cwd: Path) -> Any:
        settings = Path(launch.bot_memory_settings)
        capture.protection.require_private_file(settings)
        self.launches.append(launch)
        self.cwds.append(cwd)
        self.argvs.append(
            capture.build_argv([str(self.cfg.cli)], launch, mode="auto", effort="low", model="x")
        )
        document = json.loads(settings.read_text(encoding="ascii"))
        return SimpleNamespace(session_id=launch.session_id, cwd=cwd, document=document)

    def _loaded(self, live: Any) -> str:
        """The instructions Claude Code would load in `live.cwd`."""
        patterns = (
            [] if self.mode == "ignores_excludes" else live.document.get("claudeMdExcludes", [])
        )
        claude, agents = live.cwd / "CLAUDE.md", live.cwd / "AGENTS.md"
        if claude.is_file():
            if _matched(patterns, claude):
                return ""
            text = claude.read_text(encoding="utf-8")
            if "@AGENTS.md" in text and not _matched(patterns, agents):
                text += agents.read_text(encoding="utf-8")
            return text
        if self.mode == "no_fallback" or not agents.is_file():
            return ""
        if self.mode != "fallback_ignores_excludes" and _matched(patterns, agents):
            return ""
        return agents.read_text(encoding="utf-8")

    async def turn(self, live: Any, text: str) -> list[dict[str, Any]]:
        assert text == capture._QUESTION_43
        step = STEPS[len(self.launches) - 1]
        word = CANARY.search(self._loaded(live))
        tools: list[dict[str, Any]] = []
        if (step, self.mode) in (
            ("control", "control_reads_a_file"),
            ("excluded", "excluded_reads_a_file"),
        ):
            tools = [{"type": "tool_use", "name": "Read", "id": "toolu_0", "input": {}}]
            word = CANARY.search((live.cwd / "AGENTS.md").read_text(encoding="utf-8"))
        said = word[0] if word else "NONE"
        if step == "excluded" and self.mode == "says_no_word" and not word:
            said = "I was given no code word."
        cfg = cast(capture.config.Config, self.cfg)  # a stand-in with claude_home
        transcript = capture._transcript(cfg, live.cwd, live.session_id)
        if self.mode != "no_transcript":
            transcript.parent.mkdir(parents=True, exist_ok=True)
            lines = [{"type": "assistant", "message": {"content": [{"text": said}]}}]
            if step == "excluded" and self.mode == "transcript_holds_it":
                agents = (live.cwd / "AGENTS.md").read_text(encoding="utf-8")
                lines.append({"type": "attachment", "content": agents})
            transcript.write_text("".join(json.dumps(line) + "\n" for line in lines), "utf-8")
        failed = step == "excluded" and self.mode == "excluded_errors"
        return [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": said}, *tools]},
            },
            {"type": "result", "subtype": "error_during_execution" if failed else "success"},
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


def test_43_is_named_only() -> None:
    assert "43" in capture.SCENARIOS
    assert "43" in capture.NAMED_ONLY
    assert "43" not in capture.REFUSED
    assert "paid" in capture.NAMED_ONLY["43"] and "recorded go" in capture.NAMED_ONLY["43"]


async def test_43_starts_in_a_vault_named_with_a_space_before_any_provider(
    tmp_path: Path,
) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    opened: list[tuple[Any, dict[str, Any]]] = []

    async def stop_before_provider(_rec: object, launch: Any, **kwargs: Any) -> None:
        opened.append((launch, kwargs))
        raise RuntimeError("provider deliberately absent")

    open_session = AsyncMock(side_effect=stop_before_provider)
    cfg = SimpleNamespace(cli=tmp_path / "bin" / "claude", claude_home=tmp_path / "home")
    cap = cast(
        capture.Capture, SimpleNamespace(root=root, run_dir=tmp_path, cfg=cfg, open=open_session)
    )
    with pytest.raises(RuntimeError, match="provider deliberately absent"):
        await capture.s43(cap, cast(capture.Recorder, None))
    open_session.assert_awaited_once()

    [(launch, kwargs)] = opened
    vault = kwargs["cwd"]
    assert set(kwargs) == {"cwd"} and " " in vault.name and vault.parent == tmp_path
    assert (vault / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    assert CANARY.search((vault / "AGENTS.md").read_text(encoding="utf-8"))
    assert not launch.resume and not launch.add_dirs and launch.mcp_config is None
    settings = Path(launch.bot_memory_settings)
    assert settings.parent == tmp_path / "data" / "bot-memory-settings"
    capture.protection.require_private_directory(settings.parent)
    capture.protection.require_private_file(settings)
    # The first turn is the control: the memory-only file, without the exclusion.
    document = json.loads(settings.read_text(encoding="ascii"))
    assert list(document) == ["autoMemoryDirectory"]
    assert Path(document["autoMemoryDirectory"]).is_relative_to(vault)
    # The app's own file, written beside it, names the vault's instruction files.
    [app] = [
        loaded
        for path in settings.parent.glob("*.json")
        if "claudeMdExcludes" in (loaded := json.loads(path.read_text(encoding="ascii")))
        and Path(loaded["autoMemoryDirectory"]).is_relative_to(vault)
    ]
    assert app == {
        **document,
        "autoMemoryEnabled": False,
        "claudeMdExcludes": instruction_excludes(vault),
    }


@pytest.mark.parametrize("mode", list(MODES))
async def test_43_observations(mode: str, tmp_path: Path) -> None:
    cap = _Claude(tmp_path, mode)
    result = await capture.s43(cast(capture.Capture, cap), capture._NoRawRecorder())

    expected = {**EXPECTED, **MODES[mode]}
    assert {key: result[key] for key in expected} == expected
    assert result["pass"] is all(expected.values())
    assert set(result) == {"pass", "turns", *expected}
    assert cap.closed == len(STEPS)

    turns = result["turns"]
    assert list(turns) == STEPS
    vault, agents_only = cap.cwds[0], cap.cwds[-1]
    assert " " in vault.name and " " in agents_only.name and vault != agents_only
    assert not (agents_only / "CLAUDE.md").exists()
    assert cap.cwds == [vault] * (len(STEPS) - 2) + [agents_only] * 2
    for step, launch, argv in zip(STEPS, cap.launches, cap.argvs, strict=True):
        turn = turns[step]
        assert turn["settings_keys"] == SETTINGS_KEYS[step]
        assert turn["argv"] == _shape(argv, cap.cfg.cli)
        assert sum(arg.startswith("--settings=") for arg in turn["argv"]) == 1
        assert not any(arg.startswith(("--add-dir", "--mcp-config")) for arg in turn["argv"])
        assert launch.resume is False
        document = json.loads(Path(launch.bot_memory_settings).read_text(encoding="ascii"))
        patterns = document.get("claudeMdExcludes", [])
        assert turn["patterns"] == len(patterns)
        if step == "excluded":
            assert patterns == instruction_excludes(vault)
        if step == "agents_only_excluded":
            assert patterns == instruction_excludes(agents_only)
        assert turn["transcript_found"] is (mode != "no_transcript")
    ids = [launch.session_id for launch in cap.launches]
    assert len(set(ids)) == len(STEPS)  # a fresh session for every turn

    text = json.dumps(result)
    assert cap.run_dir.name not in text and "VAULT_RULES_" not in text and "vault" not in text
    assert not any(session_id in text for session_id in ids)
