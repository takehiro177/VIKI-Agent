"""The Bot learning evaluation script: task files, filler arms, metrics, and no spawn without a
recorded go."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bot_eval  # noqa: E402

TASKS: dict[str, Any] = {
    "instructions": "You help with research.",
    "arms": {
        "A": {},
        "C": {
            "profile": ["Lives in Tokyo"],
            "lessons": [
                {
                    "trigger": "Citing a paper",
                    "action": "Check its newest version",
                    "example": "the version was v3",
                }
            ],
        },
    },
    "tasks": [
        {
            "id": "t1",
            "group": "research",
            "prompt": "Cite it",
            "check": {"contains": ["v3"], "not_contains": ["v1"]},
        },
    ],
}


def test_a_lesson_arm_gets_a_filler_twin_of_the_same_shape() -> None:
    plan = bot_eval.load(TASKS, repeat=2, baseline="A")
    assert [arm.name for arm in plan.arms] == ["A", "C", "C-filler"]
    twin = plan.arms[2].lessons[0]
    assert twin.trigger == "xxxxxx x xxxxx" and twin.examples == ("xxx xxxxxxx xxx xx",)
    assert "<lessons>" in plan.prompts["C"] and "Citing a paper" in plan.prompts["C"]
    assert "Citing a paper" not in plan.prompts["C-filler"]
    assert "<profile>\n" not in plan.prompts["A"]
    assert "Memory rule from VIKI Agent" in plan.prompts["A"]
    assert plan.tasks[0].passed("It is V3.") and not plan.tasks[0].passed("v3, not v1")
    with pytest.raises(ValueError):
        bot_eval.load(
            {**TASKS, "tasks": [{**TASKS["tasks"][0], "group": "other"}]}, repeat=1, baseline="A"
        )


def test_metrics_give_success_pass_k_and_flips() -> None:
    rows = [
        {
            "arm": arm,
            "task": task,
            "group": "research",
            "attempt": n,
            "passed": ok,
            "recalled_chars": 10,
        }
        for arm, task, outcomes in (
            ("A", "t1", (True, True)),
            ("A", "t2", (False, True)),
            ("C", "t1", (True, False)),
            ("C", "t2", (True, True)),
        )
        for n, ok in enumerate(outcomes)
    ]
    report = bot_eval.metrics(rows, "A")
    assert report["A"]["success"] == 0.75 and report["A"]["pass_k"] == 0.5
    assert report["C"]["flipped"] == ["t1"] and report["C"]["pass_k"] == 0.5


def test_nothing_spawns_without_a_recorded_go(tmp_path: Path, capsys: Any) -> None:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(TASKS), encoding="utf-8")

    def never(*_args: Any) -> Any:
        raise AssertionError("spawned")

    assert bot_eval.main([str(path)], live=never) == 2
    assert "recorded go" in capsys.readouterr().out
    assert bot_eval.main([str(path), "--plan"], live=never) == 0
