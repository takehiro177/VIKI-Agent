"""Scenario 44 is named-only and paid; before any provider it opens one Bot session whose
settings turn auto memory off and whose launch carries the write fence's hooks."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import capture  # noqa: E402

from viki_agent.messaging.bot_fence import TAINT_MATCHER, WRITE_MATCHER  # noqa: E402


def test_44_is_named_only() -> None:
    assert "44" in capture.SCENARIOS
    assert "44" in capture.NAMED_ONLY and "44" not in capture.REFUSED
    assert "paid" in capture.NAMED_ONLY["44"] and "recorded go" in capture.NAMED_ONLY["44"]


async def test_44_opens_a_fenced_bot_session_before_any_provider(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    opened: list[tuple[Any, dict[str, Any]]] = []

    async def stop_before_provider(_rec: object, launch: Any, **kwargs: Any) -> None:
        opened.append((launch, kwargs))
        raise RuntimeError("provider deliberately absent")

    cap = cast(
        capture.Capture,
        SimpleNamespace(
            root=root, run_dir=tmp_path, open=AsyncMock(side_effect=stop_before_provider)
        ),
    )
    with pytest.raises(RuntimeError, match="provider deliberately absent"):
        await capture.SCENARIOS["44"][1](cap, cast(capture.Recorder, lambda *_args: None))

    ((launch, kwargs),) = opened
    assert kwargs == {"mode": "acceptEdits"}
    settings = json.loads(Path(launch.bot_memory_settings).read_text(encoding="ascii"))
    assert settings["autoMemoryEnabled"] is False
    assert [m.matcher for m in launch.hooks["PreToolUse"]] == [TAINT_MATCHER, WRITE_MATCHER]
    assert not (root / "memory").exists()
