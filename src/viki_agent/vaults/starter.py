"""The "viki" starter vault's data: its file set, tokens and renderer.

Pure: this module imports no other `viki_agent` code and touches no disk beyond the
`resources` tree it is handed. `vault_storage.py` owns the bootstrap that writes the rendered
result to a real vault; `__main__.py` owns reading the identity settings that feed `starter_fill`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

STARTER_NAME = "viki"
STARTER_VERSION = 6

# Write order: every file the starter vault ships, as the destination path a chat session sees.
FILES: tuple[str, ...] = (
    "Welcome.md",
    "AGENTS.md",
    "CLAUDE.md",
    "Home.md",
    "50-maps/Second Brain MOC.md",
    "_templates/area.md",
    "_templates/card.md",
    "_templates/daily.md",
    "_templates/moc.md",
    "_templates/note.md",
    "_templates/person.md",
    "_templates/project.md",
    "_templates/source.md",
    "_templates/tasks.md",
    "_templates/thinking.md",
    "_templates/weekly.md",
    "system/log.md",
    "system/memory/user.md",
    "system/memory/preferences.md",
    "system/memory/lessons.md",
    ".claude/settings.json",
    ".claude/agents/librarian.md",
    ".claude/agents/curator.md",
    ".claude/agents/connector.md",
    ".claude/agents/researcher.md",
    ".claude/agents/checker.md",
    ".claude/skills/capture/SKILL.md",
    ".claude/skills/inbox/SKILL.md",
    ".claude/skills/daily/SKILL.md",
    ".claude/skills/weekly/SKILL.md",
    ".claude/skills/think/SKILL.md",
    ".claude/skills/research/SKILL.md",
    ".claude/skills/learn/SKILL.md",
    ".claude/skills/connect/SKILL.md",
    ".claude/skills/lint/SKILL.md",
    ".claude/skills/retro/SKILL.md",
    ".claude/skills/kanban/SKILL.md",
    ".claude/skills/standup/SKILL.md",
    ".claude/skills/viki-agent/SKILL.md",
)

# Folders that ship empty: no FILES entry ever lives under them.
EMPTY_FOLDERS: tuple[str, ...] = (
    "00-inbox",
    "10-projects",
    "20-areas",
    "30-knowledge",
    "40-sources",
    "60-calendar",
    "70-people",
    "90-archive",
    "_raw",
    "_attachments",
    "system/retro",
    "kanban",
)

# The targets whose source text carries `{{viki.*}}` tokens; every other FILES entry ships as-is.
FILLED: frozenset[str] = frozenset(
    {
        "Welcome.md",
        "AGENTS.md",
        "CLAUDE.md",
        "Home.md",
        "50-maps/Second Brain MOC.md",
        "system/log.md",
    }
)

_TOKEN = re.compile(r"\{\{viki\.([a-z_]+)\}\}")


@dataclass(frozen=True, slots=True)
class StarterFill:
    """The five `{{viki.*}}` token values, already normalised by `starter_fill`."""

    assistant_name: str
    address: str
    language: str
    vault_id: str
    date: str

    def values(self) -> dict[str, str]:
        return asdict(self)


def _utf16_units(text: str) -> int:
    """UTF-16 code unit count of `text` — what persona.ts's NAME_MAX_LENGTH/ADDRESS_MAX_LENGTH
    bounds measure via JS's native string `.length`, not Python's code-point `len()` (so an
    astral character, a surrogate pair in UTF-16, counts as 2 here, as it does for persona.ts)."""
    return len(text.encode("utf-16-le")) // 2


def starter_fill(assistant_name: str, display_name: str, vault_id: str, date: str) -> StarterFill:
    """Normalise the raw identity settings into the five token values. The persona replies in
    English; its AGENTS.md also tells the assistant to mirror an owner who writes in another
    language."""
    # U+FEFF folds into a space before the whitespace collapse: JS's trim() (persona.ts's
    # valueProblems) already treats it as whitespace, so Python must too or a BOM surviving
    # here reads back as an "edited" persona the page cannot round-trip.
    name = " ".join(assistant_name.replace(chr(0xFEFF), " ").split())
    if not 1 <= _utf16_units(name) <= 32:
        name = "VIKI"
    address = " ".join(display_name.replace(chr(0xFEFF), " ").split())
    if not 1 <= _utf16_units(address) <= 64:
        address = "you"
    return StarterFill(
        assistant_name=name,
        address=address,
        language="English",
        vault_id=vault_id,
        date=date,
    )


def folders() -> tuple[str, ...]:
    """Every folder the starter creates: `EMPTY_FOLDERS` plus each `FILES` entry's parents,
    parents first, sorted by depth then name."""
    found = set(EMPTY_FOLDERS)
    for file in FILES:
        segments = file.split("/")[:-1]
        found.update("/".join(segments[:depth]) for depth in range(1, len(segments) + 1))
    return tuple(sorted(found, key=lambda path: (path.count("/"), path)))


def source_of(target: str) -> str:
    """The path under `resources/starter/` that renders to `target`."""
    if target == "AGENTS.md":
        return "AGENTS.template.md"
    if target == "CLAUDE.md":
        return "CLAUDE.template.md"
    if target.startswith(".claude/"):
        return "dot-claude/" + target.removeprefix(".claude/")
    return target


def fill(text: str, values: Mapping[str, str]) -> str:
    """Substitute every `{{viki.<name>}}` token in `text`. An unknown name is a bug upstream
    (a resource referencing a token `StarterFill` never produces), never a silent gap."""

    def _one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"unknown starter token: {name}")
        return values[name]

    return _TOKEN.sub(_one, text)


def render(resources: Path, values: StarterFill) -> list[tuple[str, str]]:
    """Every `FILES` target with its rendered text, in write order. `FILLED` targets are
    token-filled; every other target ships byte-for-byte as its resource reads."""
    mapping = values.values()
    rendered: list[tuple[str, str]] = []
    for target in FILES:
        text = (resources / source_of(target)).read_text(encoding="utf-8")
        rendered.append((target, fill(text, mapping) if target in FILLED else text))
    return rendered


def fingerprints(rendered: Sequence[tuple[str, str]]) -> dict[str, str]:
    """sha256 hex digest of each rendered file's UTF-8 bytes, keyed by target path."""
    return {target: hashlib.sha256(text.encode("utf-8")).hexdigest() for target, text in rendered}
