"""Bot learning evaluation: run a task set under each memory arm and compare the arms.

    uv run python scripts/bot_eval.py TASKS.json --plan                 # what would run; no spawn
    uv run python scripts/bot_eval.py TASKS.json --go "<recorded go>"   # paid live turns
    uv run python scripts/bot_eval.py --report RESULTS.json             # metrics of a saved run

A task file lives outside the repository (it is built from the owner's own logs: hard rule 9
keeps such text out of fixtures). Its shape:

    {"instructions": "<the Bot's profile instructions>",
     "arms": {"A": {}, "C": {"profile": ["..."], "facts": ["..."],
                    "lessons": [{"trigger": "...", "action": "...", "example": "..."}]}},
     "tasks": [{"id": "t1", "group": "research", "prompt": "...",
                "check": {"contains": ["..."], "not_contains": ["..."]}}]}

Each arm names the memory a session starts with, recalled the way a Bot recalls it
(`bot_recall.compose`) after the Bot rules (`bot_memory_rule.bot_system_prompt`). For each arm
with lessons, `<arm>-filler` is added: the same lessons with every word replaced by filler of the
same length, which tells whether a lesson's content is what helps. Every task runs `--repeat`
times per arm (k, 4 by default) in a fresh throwaway vault with a Bot's settings (auto memory
off) and every permission card denied.

The report gives per arm and group: success rate, pass^k (all k runs passed), the tasks that went
from pass in the baseline arm (`--baseline`, A by default) to fail, and the recalled characters
per session. Without `--go` nothing is spawned: a live or paid run needs the requester's recorded
go (AGENTS.md), and the text given is written into the results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from viki_agent.drivers.claude.control import Launch  # noqa: E402
from viki_agent.messaging.bot_memory import write_settings  # noqa: E402
from viki_agent.messaging.bot_memory_rule import bot_system_prompt  # noqa: E402
from viki_agent.messaging.bot_recall import compose, creator_for  # noqa: E402
from viki_agent.vaults import lesson_body  # noqa: E402
from viki_agent.vaults.memory import MemoryItem  # noqa: E402
from viki_agent.vaults.memory_record import Fields  # noqa: E402

BOT = "e7a1c0de-0000-4000-8000-000000000001"
GROUPS = ("chat", "routine_change", "routine_run", "research", "injection")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class Task:
    task_id: str
    group: str
    prompt: str
    contains: tuple[str, ...]
    not_contains: tuple[str, ...]

    def passed(self, reply: str) -> bool:
        folded = reply.casefold()
        return all(word.casefold() in folded for word in self.contains) and not any(
            word.casefold() in folded for word in self.not_contains
        )


@dataclass(frozen=True)
class Arm:
    name: str
    profile: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    lessons: tuple[lesson_body.Lesson, ...] = ()


@dataclass
class Plan:
    instructions: str
    arms: list[Arm]
    tasks: list[Task]
    repeat: int
    baseline: str
    prompts: dict[str, str] = field(default_factory=dict)


def _strings(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def filler(text: str) -> str:
    """`text` with each word replaced by filler of the same length: shape kept, meaning gone."""
    return _WORD.sub(lambda match: "x" * len(match.group(0)), text)


def load(raw: Mapping[str, Any], *, repeat: int, baseline: str) -> Plan:
    if set(raw) - {"instructions", "arms", "tasks"} or "tasks" not in raw or "arms" not in raw:
        raise ValueError("a task file has instructions, arms and tasks")
    arms: list[Arm] = []
    for name, value in raw["arms"].items():
        if not isinstance(value, dict) or set(value) - {"profile", "facts", "lessons"}:
            raise ValueError(f"arm {name}: only profile, facts and lessons")
        lessons = []
        for item in value.get("lessons", []):
            example = (item["example"],) if item.get("example") else ()
            lessons.append(lesson_body.Lesson(item["trigger"], item["action"], example))
            lesson_body.compose(lessons[-1])  # refuses a lesson the grammar would not keep
        arm = Arm(
            name,
            _strings(value.get("profile", []), f"{name}.profile"),
            _strings(value.get("facts", []), f"{name}.facts"),
            tuple(lessons),
        )
        arms.append(arm)
        if arm.lessons:
            arms.append(
                Arm(
                    f"{name}-filler",
                    arm.profile,
                    arm.facts,
                    tuple(
                        lesson_body.Lesson(
                            filler(lesson.trigger),
                            filler(lesson.action),
                            tuple(filler(example) for example in lesson.examples),
                        )
                        for lesson in arm.lessons
                    ),
                )
            )
    names = [arm.name for arm in arms]
    if baseline not in names or len(set(names)) != len(names):
        raise ValueError("arm names must be distinct and include the baseline")
    tasks = []
    for item in raw["tasks"]:
        check = item.get("check", {})
        if item.get("group") not in GROUPS:
            raise ValueError(f"task {item.get('id')}: group is one of {', '.join(GROUPS)}")
        tasks.append(
            Task(
                str(item["id"]),
                item["group"],
                str(item["prompt"]),
                _strings(check.get("contains", []), "contains"),
                _strings(check.get("not_contains", []), "not_contains"),
            )
        )
    plan = Plan(str(raw.get("instructions", "")), arms, tasks, repeat, baseline)
    plan.prompts = {arm.name: system_prompt(plan.instructions, arm) for arm in arms}
    return plan


def _item(claim: str, creator: str | None) -> MemoryItem:
    memory_id = str(uuid.uuid4())
    fields = Fields(memory_id, "assistant", "vault", "approved", _stamp(), creator=creator)
    return MemoryItem(f"memory/{memory_id}.md", "0" * 64, "active", fields, claim, None)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def system_prompt(instructions: str, arm: Arm) -> str:
    """What a Bot of this arm appends to Claude Code's system prompt."""
    mine = creator_for(BOT)
    items = [_item(claim, None) for claim in arm.profile]
    items += [_item(claim, mine) for claim in arm.facts]
    items += [_item(lesson_body.compose(lesson), mine) for lesson in arm.lessons]
    recalled = compose(items, BOT, {}, datetime.now(UTC)).text
    rules = bot_system_prompt(instructions)
    return f"{rules}\n\n{recalled}" if recalled else rules


def metrics(results: Sequence[Mapping[str, Any]], baseline: str) -> dict[str, Any]:
    """Per arm: success rate, pass^k and recalled characters, by group too; and the tasks that
    passed every run in the baseline arm but not in this one."""
    by_arm: dict[str, dict[str, list[bool]]] = {}
    sizes: dict[str, int] = {}
    groups: dict[str, str] = {}
    for row in results:
        by_arm.setdefault(row["arm"], {}).setdefault(row["task"], []).append(bool(row["passed"]))
        sizes[row["arm"]] = row["recalled_chars"]
        groups[row["task"]] = row["group"]
    report: dict[str, Any] = {}
    base = {task: all(runs) for task, runs in by_arm.get(baseline, {}).items()}
    for arm, tasks in by_arm.items():
        runs = [ok for outcomes in tasks.values() for ok in outcomes]
        per_group: dict[str, dict[str, float]] = {}
        for group in sorted({groups[task] for task in tasks}):
            mine = [tasks[task] for task in tasks if groups[task] == group]
            flat = [ok for outcomes in mine for ok in outcomes]
            per_group[group] = {
                "success": sum(flat) / len(flat),
                "pass_k": sum(all(o) for o in mine) / len(mine),
            }
        report[arm] = {
            "success": sum(runs) / len(runs),
            "pass_k": sum(all(outcomes) for outcomes in tasks.values()) / len(tasks),
            "flipped": sorted(t for t, ok in base.items() if ok and not all(tasks.get(t, [False]))),
            "recalled_chars": sizes[arm],
            "groups": per_group,
        }
    return report


async def run_live(plan: Plan, go: str, out: Path) -> list[dict[str, Any]]:
    """Every task, `plan.repeat` times per arm, each in a fresh throwaway vault."""
    import capture  # the scripted harness: its billing gate and recorded frames

    cfg = capture.config.load()
    if cfg.cli is None or cfg.claude_config_dir_set:
        raise SystemExit("No usable claude CLI, or CLAUDE_CONFIG_DIR is set.")
    run_dir = cfg.captures_dir / f"bot-eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True)
    results: list[dict[str, Any]] = []
    for arm in plan.arms:
        prompt = plan.prompts[arm.name]
        for task in plan.tasks:
            for attempt in range(plan.repeat):
                root = run_dir / f"{arm.name}-{task.task_id}-{attempt}"
                capture.seed_scratch(root)
                problems = capture.config.billing_problems(capture.replace(cfg, vault=root))
                if problems:
                    raise SystemExit("Billing preflight failed: " + "; ".join(problems))
                cap = capture.Capture(cfg, run_dir, root)
                settings = write_settings(run_dir / "bot-memory-settings", root, BOT)
                rec = capture.Recorder(root.with_suffix(".jsonl"), "eval")
                try:
                    live = await cap.open(
                        rec,
                        Launch(
                            capture.new_id(),
                            bot_memory_settings=settings,
                            append_system_prompt=prompt,
                        ),
                        mode="default",
                    )
                    frames = await cap.turn(live, task.prompt)
                    reply = capture.text_of(frames)
                    error = None
                except Exception as exc:  # noqa: BLE001 - one broken run must not stop the set
                    reply, error = "", type(exc).__name__
                finally:
                    await cap.close_all()
                    rec.close()
                results.append(
                    {
                        "arm": arm.name,
                        "task": task.task_id,
                        "group": task.group,
                        "attempt": attempt,
                        "passed": error is None and task.passed(reply),
                        "error": error,
                        "recalled_chars": len(prompt),
                    }
                )
                out.write_text(
                    json.dumps({"go": go, "results": results}, indent=1), encoding="utf-8"
                )
    return results


def main(argv: Sequence[str] | None = None, *, live: Callable[..., Any] = run_live) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("tasks", nargs="?", type=Path)
    parser.add_argument("--plan", action="store_true", help="print what would run; spawn nothing")
    parser.add_argument("--go", help="the requester's recorded go for paid live turns")
    parser.add_argument("--repeat", type=int, default=4)
    parser.add_argument("--baseline", default="A")
    parser.add_argument("--out", type=Path, default=Path("bot-eval-results.json"))
    parser.add_argument("--report", type=Path, help="print the metrics of a saved run")
    args = parser.parse_args(argv)
    if args.report is not None:
        saved = json.loads(args.report.read_text(encoding="utf-8"))
        print(json.dumps(metrics(saved["results"], args.baseline), indent=1))
        return 0
    if args.tasks is None:
        parser.error("a task file is needed")
    plan = load(
        json.loads(args.tasks.read_text(encoding="utf-8")),
        repeat=args.repeat,
        baseline=args.baseline,
    )
    if args.plan or not args.go:
        for arm in plan.arms:
            print(f"arm {arm.name}: {len(plan.prompts[arm.name])} system-prompt characters")
        print(f"{len(plan.tasks)} tasks x {plan.repeat} runs x {len(plan.arms)} arms")
        if not args.plan:
            print("Nothing ran: a live run needs --go with the requester's recorded go.")
            return 2
        return 0
    results = asyncio.run(live(plan, args.go, args.out))
    print(json.dumps(metrics(results, plan.baseline), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
