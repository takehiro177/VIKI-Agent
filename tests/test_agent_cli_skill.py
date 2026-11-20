"""AC-12: the starter's viki-agent skill stays honest about the CLI it teaches. Every
`"$VIKI_AGENT_CLI" ...` and `& $env:VIKI_AGENT_CLI ...` example line in its SKILL.md must parse
with the real `agent_cli.parser()`, and every leaf command the parser defines must have at least
one such example."""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

import pytest

import viki_agent
from viki_agent import agent_cli
from viki_agent.app.agent_api import CHANGE_SETTINGS

SKILL = (
    Path(viki_agent.__file__).parent
    / "resources"
    / "starter"
    / "dot-claude"
    / "skills"
    / "viki-agent"
    / "SKILL.md"
)

# A stand-in for any <...> placeholder (a routine id, a profile id, a key): parser() places no
# format constraint on these beyond being a string, so one fixed sample value exercises them all.
SAMPLE_UUID = "11111111-1111-4111-8111-111111111111"
PLACEHOLDER = re.compile(r"<[^>]+>")

BASH_PREFIX = '"$VIKI_AGENT_CLI"'
POWERSHELL_PREFIX = "& $env:VIKI_AGENT_CLI"
CODE_SPAN = re.compile(r"`([^`\n]+)`")


def _examples(text: str) -> list[str]:
    """Every backtick-quoted span that is itself a CLI invocation (starts with one of the two
    prefixes), never a prose mention such as `` `$VIKI_AGENT_KEY` `` or the `` `status` `` aside,
    or the `[ -n "$VIKI_AGENT_CLI" ]` guard, which starts with `[`, not the CLI prefix."""
    return [
        span
        for span in CODE_SPAN.findall(text)
        if span.startswith(BASH_PREFIX) or span.startswith(POWERSHELL_PREFIX)
    ]


def _argv(example: str) -> list[str]:
    rest = (
        example.removeprefix(BASH_PREFIX)
        if example.startswith(BASH_PREFIX)
        else example.removeprefix(POWERSHELL_PREFIX)
    )
    return shlex.split(PLACEHOLDER.sub(SAMPLE_UUID, rest))


def _leaves(node: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Every leaf command `parser()` defines, as its dest path (e.g. `("settings", "get")`),
    found by walking its subparsers tree rather than a hardcoded list, so a future command the
    skill forgets to document is itself a failure here."""
    sub_action = next(
        (action for action in node._actions if isinstance(action, argparse._SubParsersAction)),
        None,
    )
    if sub_action is None:
        return {prefix}
    found: set[tuple[str, ...]] = set()
    for name, child in sub_action.choices.items():
        found |= _leaves(child, (*prefix, name))
    return found


def _leaf_of(namespace: argparse.Namespace) -> tuple[str, ...]:
    for sub_dest in ("settings_command", "routines_command", "bots_command"):
        value = getattr(namespace, sub_dest, None)
        if value is not None:
            return (namespace.command, value)
    return (namespace.command,)


def test_every_skill_example_line_parses_with_the_cli_parser() -> None:
    examples = _examples(SKILL.read_text(encoding="utf-8"))
    assert examples, 'no "$VIKI_AGENT_CLI" / PowerShell example line found in SKILL.md'
    parser = agent_cli.parser()
    for example in examples:
        try:
            parser.parse_args(_argv(example))
        except SystemExit as exit_signal:
            pytest.fail(f"{example!r} does not parse (argparse exit {exit_signal.code})")


def test_every_leaf_command_has_a_skill_example() -> None:
    examples = _examples(SKILL.read_text(encoding="utf-8"))
    parser = agent_cli.parser()
    covered = {_leaf_of(parser.parse_args(_argv(example))) for example in examples}
    assert covered == _leaves(agent_cli.parser())


def test_every_settings_set_example_names_a_change_level_key() -> None:
    """The skill's `settings set` example teaches the owner that running it just works. A key
    outside `CHANGE_SETTINGS` would instead hold the change for the owner's Apply (R5's
    allowlist narrowed what changes at once; `chat.max_live_sessions`, this test's previous
    example, is now safety-level)."""
    examples = _examples(SKILL.read_text(encoding="utf-8"))
    parser = agent_cli.parser()
    for example in examples:
        namespace = parser.parse_args(_argv(example))
        if _leaf_of(namespace) == ("settings", "set"):
            assert namespace.key in CHANGE_SETTINGS, (
                f"{example!r} names a key outside CHANGE_SETTINGS, so it would wait for Apply"
            )
