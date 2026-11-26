"""The "viki" starter vault's data: its file set, tokens and renderer.

Pure: this module imports no other `viki_agent` code and touches no disk beyond the
`resources` tree it is handed. `vault_storage.py` owns the bootstrap that writes the rendered
result to a real vault; `__main__.py` owns reading the identity settings that feed `starter_fill`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

STARTER_NAME = "viki"
STARTER_VERSION = 8

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

# The 7 targets the owner's own notes own outright (plan section 4): an update never rewrites
# these, whatever the shipped text says.
OWNER_FILES: frozenset[str] = frozenset(
    {
        "Welcome.md",
        "Home.md",
        "50-maps/Second Brain MOC.md",
        "system/log.md",
        "system/memory/user.md",
        "system/memory/preferences.md",
        "system/memory/lessons.md",
    }
)

# Every FILES target an update can manage, in FILES order: FILES minus OWNER_FILES.
MANAGED: tuple[str, ...] = tuple(target for target in FILES if target not in OWNER_FILES)

# Managed targets an update never replaces outright: the owner edits these by hand.
MANUAL_FILES: frozenset[str] = frozenset({"CLAUDE.md", ".claude/settings.json"})

HISTORY_FILE = "starter-history.json"
RECORD_FILE = "system/starter-updates.md"

PERSONA_OPEN = "<!-- viki:persona -->"
PERSONA_CLOSE = "<!-- /viki:persona -->"

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


# --- starter-history.json ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class History:
    """A validated `starter-history.json`: every version's per-target shas, and the text behind
    each sha. Build only through `load_history`, which has already checked the invariants
    below."""

    texts: Mapping[str, str]
    versions: Mapping[int, Mapping[str, tuple[str, ...]]]

    def shipped(self, target: str) -> frozenset[str]:
        """Every sha ever shipped for `target`, across every recorded version."""
        return frozenset(
            sha for version in self.versions.values() for sha in version.get(target, ())
        )

    def text_at(self, version: int, target: str) -> str | None:
        """The text of `target`'s last sha in `version`, or None when that version never
        recorded `target`."""
        shas = self.versions.get(version, {}).get(target)
        if not shas:
            return None
        return self.texts.get(shas[-1])

    def targets(self) -> frozenset[str]:
        """Every target any version records."""
        return frozenset(target for version in self.versions.values() for target in version)


_VERSION_KEY = re.compile(r"[1-9][0-9]*")


def load_history(path: Path) -> History:
    """Reads and validates `starter-history.json`. Raises `ValueError` unless: the JSON is an
    object with exactly the keys `schema`, `texts`, `versions`; `schema == 1`; every `texts` key
    is the lowercase sha256 hex digest of its own value; `versions` is non-empty and its keys are
    the decimal strings "first".."N" with no gaps and `first >= 1` (not only "1".."N": the ship
    set can start above 1); and every version maps a target outside `OWNER_FILES` to a non-empty
    list of distinct shas that all exist in `texts`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"schema", "texts", "versions"}:
        raise ValueError("starter history must be an object with schema, texts, versions")
    if raw["schema"] != 1:
        raise ValueError("starter history schema must be 1")

    texts = raw["texts"]
    if not isinstance(texts, dict):
        raise ValueError("starter history texts must be an object")
    for sha, text in texts.items():
        if not isinstance(sha, str) or not isinstance(text, str) or sha != text_sha(text):
            raise ValueError(f"starter history text does not match its sha: {sha!r}")

    versions_raw = raw["versions"]
    if not isinstance(versions_raw, dict):
        raise ValueError("starter history versions must be an object")
    if not versions_raw:
        raise ValueError("starter history versions must not be empty")
    if not all(_VERSION_KEY.fullmatch(key) for key in versions_raw):
        raise ValueError("starter history version keys must be positive decimal integers")
    numbers = sorted(int(key) for key in versions_raw)
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        raise ValueError("starter history versions must be a contiguous range with no gaps")

    versions: dict[int, Mapping[str, tuple[str, ...]]] = {}
    for key, target_map in versions_raw.items():
        if not isinstance(target_map, dict):
            raise ValueError(f"starter history version {key} must map target to shas")
        parsed: dict[str, tuple[str, ...]] = {}
        for target, shas in target_map.items():
            if target in OWNER_FILES:
                raise ValueError(f"starter history must not record an owner target: {target}")
            if not isinstance(shas, list) or not shas or len(set(shas)) != len(shas):
                raise ValueError(f"starter history target {target} needs distinct, non-empty shas")
            if not all(isinstance(sha, str) and sha in texts for sha in shas):
                raise ValueError(f"starter history target {target} has a sha missing from texts")
            parsed[target] = tuple(shas)
        versions[int(key)] = parsed

    return History(texts=dict(texts), versions=versions)


# --- text and persona-block helpers ---------------------------------------------------------


def normal_text(data: bytes) -> str | None:
    """Strict UTF-8 decode of `data` (None on any decode error), with one leading U+FEFF dropped
    and CRLF normalised to LF."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.startswith(chr(0xFEFF)):
        text = text[1:]
    return text.replace("\r\n", "\n")


def text_sha(text: str) -> str:
    """sha256 hex digest of `text`'s UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fill_vault_id(text: str, vault_id: str) -> str:
    """Replaces only the `{{viki.vault_id}}` token; every other token is left untouched."""
    return text.replace("{{viki.vault_id}}", vault_id)


def persona_split(text: str) -> tuple[list[str], list[str], list[str]] | None:
    """Splits `text` on its Persona marker pair. A marker is a line whose `rstrip()` equals
    `PERSONA_OPEN`/`PERSONA_CLOSE`; a marker not flush with the start of its line is not a
    marker. Returns None unless there is exactly one open marker strictly before exactly one
    close marker; otherwise the three parts are the lines before the open marker, the marker
    pair's own lines (both markers included), and the lines after the close marker."""
    lines = text.split("\n")
    opens = [index for index, line in enumerate(lines) if line.rstrip() == PERSONA_OPEN]
    closes = [index for index, line in enumerate(lines) if line.rstrip() == PERSONA_CLOSE]
    if len(opens) != 1 or len(closes) != 1 or opens[0] >= closes[0]:
        return None
    open_index, close_index = opens[0], closes[0]
    return lines[:open_index], lines[open_index : close_index + 1], lines[close_index + 1 :]


def rules_of(text: str) -> str | None:
    """`text` with its Persona block collapsed to a single `\\x00` line, so two texts that differ
    only inside the Persona block compare equal. None when `text` has no valid marker pair."""
    split = persona_split(text)
    if split is None:
        return None
    before, _, after = split
    return "\n".join(before + ["\x00"] + after)


_VERSION_RUN = re.compile(r"starter version [0-9]+")


def rules_key(text: str) -> str | None:
    """`rules_of(text)` with any `starter version <N>` run on its first line normalised to a
    fixed placeholder, so comparing two texts' rules ignores a version-number-only difference on
    that line (F: the viki-agent skill changing mid-version without a bump must still be caught
    by the history guard, not papered over here — this only forgives the version line itself).
    None exactly when `rules_of` returns None."""
    rules = rules_of(text)
    if rules is None:
        return None
    lines = rules.split("\n")
    lines[0] = _VERSION_RUN.sub("starter version N", lines[0], count=1)
    return "\n".join(lines)


def splice(template: str, block: list[str]) -> str | None:
    """`template` with its own Persona block (both markers included) replaced by `block`. None
    when `template` has no valid marker pair."""
    split = persona_split(template)
    if split is None:
        return None
    before, _, after = split
    return "\n".join(before + block + after)


# `starter_version: ` followed by 1-4 digits, no leading zero: STARTER_VERSION's own shape.
_RECORD_VERSION = re.compile(r"starter_version: ([1-9][0-9]{0,3})")


def record_version(text: str) -> int | None:
    """The `starter_version` a `RECORD_FILE` entry's frontmatter names, or None when `text` is
    not frontmatter-shaped: first line `---`, a closing `---` within the next 40 lines, and
    exactly one frontmatter line fully matching `starter_version: ([1-9][0-9]{0,3})` after
    `rstrip()`."""
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        return None
    close = next((index for index in range(1, min(len(lines), 41)) if lines[index] == "---"), None)
    if close is None:
        return None
    matches = [
        match
        for line in lines[1:close]
        if (match := _RECORD_VERSION.fullmatch(line.rstrip())) is not None
    ]
    if len(matches) != 1:
        return None
    return int(matches[0].group(1))
