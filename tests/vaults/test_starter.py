"""Tests for `viki_agent.vaults.starter`: the pure starter-vault data module.

Guards fill/render correctness, that no personal string ships, the agent and skill set, the
exact deny list, the resource tree mapping one-to-one onto FILES via `source_of`, and the Python
side of the persona section's shared grammar.

`scrub_fixture` lives in scripts/, outside the installed package, so this file follows
test_fixtures_clean.py's sys.path convention to import it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

import viki_agent
from tests.automation.test_brief import WID, service, write
from viki_agent.automation.kanban import card_waiting
from viki_agent.decision_loop import rules, seats
from viki_agent.vaults import starter

TESTS = Path(__file__).parents[1]
REPO = TESTS.parent
sys.path.insert(0, str(REPO / "scripts"))

import scrub_fixture  # noqa: E402

RESOURCES = Path(viki_agent.__file__).parent / "resources" / "starter"

AGENTS = frozenset({"librarian", "curator", "connector", "researcher", "checker"})
SKILLS = frozenset(
    {
        "capture",
        "inbox",
        "daily",
        "weekly",
        "think",
        "research",
        "learn",
        "connect",
        "lint",
        "retro",
        "kanban",
        "standup",
        "viki-agent",
    }
)
MEMORY_FILES = ("system/memory/user.md", "system/memory/preferences.md", "system/memory/lessons.md")

# A generic identity, never a real person's: used to exercise render() end to end.
SAMPLE = starter.starter_fill(
    "Ada", "Sam Lee", "11111111-1111-4111-8111-111111111111", "2026-06-15"
)


@pytest.fixture
def rendered() -> dict[str, str]:
    return dict(starter.render(RESOURCES, SAMPLE))


def _resource_files() -> dict[str, str]:
    return {
        path.relative_to(RESOURCES).as_posix(): path.read_text(encoding="utf-8")
        for path in RESOURCES.rglob("*")
        if path.is_file()
    }


# --- the resource tree is exactly the starter set ----------------------------------------------


def test_the_resource_tree_is_exactly_the_starter_set() -> None:
    assert len(starter.FILES) == len(set(starter.FILES)) == 39
    found = set(_resource_files())
    assert found == {starter.source_of(target) for target in starter.FILES}
    assert all(".claude" not in Path(name).parts for name in found)
    assert all(Path(name).name.lower() not in {"agents.md", "claude.md"} for name in found)


# --- fill() and render() -------------------------------------------------------------------


def test_fill_substitutes_every_known_token(rendered: dict[str, str]) -> None:
    assert all("{{viki." not in text for text in rendered.values())


def test_fill_raises_on_an_unknown_token() -> None:
    with pytest.raises(ValueError):
        starter.fill("{{viki.nope}}", SAMPLE.values())


def test_fill_leaves_non_viki_tokens_alone() -> None:
    # _templates/*.md carry a bare {{date}} for the human copying the template, not a starter
    # token: the pattern is anchored on the "viki." prefix, so fill() must never touch it.
    assert starter.fill("keep {{date}} as is", SAMPLE.values()) == "keep {{date}} as is"


def test_fill_only_the_filled_targets_carry_tokens() -> None:
    sources = _resource_files()
    tokenised = {
        target for target in starter.FILES if "{{viki." in sources[starter.source_of(target)]
    }
    assert tokenised == starter.FILLED


def test_render_keeps_non_filled_targets_byte_identical(rendered: dict[str, str]) -> None:
    sources = _resource_files()
    for target in starter.FILES:
        if target not in starter.FILLED:
            assert rendered[target] == sources[starter.source_of(target)]


# --- starter_fill normalisation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Ada  Lovelace ", "Ada Lovelace"),
        ("", "VIKI"),
        ("   ", "VIKI"),
        ("x" * 33, "VIKI"),  # outside 1-32 chars
        ("x", "x"),
        ("x" * 32, "x" * 32),
        # A leading/trailing BOM folds into the whitespace collapse.
        (chr(0xFEFF) + "Ada" + chr(0xFEFF), "Ada"),
        # A mid-string BOM becomes a separating space.
        ("Ada" + chr(0xFEFF) + "Lovelace", "Ada Lovelace"),
        # 17 code points pass a code-point count, but 34 UTF-16 units fail NAME_MAX_LENGTH (32).
        ("\U0001f642" * 17, "VIKI"),
    ],
)
def test_starter_fill_normalises_the_assistant_name(raw: str, expected: str) -> None:
    assert starter.starter_fill(raw, "Sam", "v", "2026-01-01").assistant_name == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" Ada  Lovelace ", "Ada Lovelace"),
        ("", "you"),
        ("   ", "you"),
        # A leading/trailing BOM folds into the whitespace collapse.
        (chr(0xFEFF) + "Sam" + chr(0xFEFF), "Sam"),
        # 40 code points, 80 UTF-16 units: over ADDRESS_MAX_LENGTH (64).
        ("\U0001f642" * 40, "you"),
    ],
)
def test_starter_fill_normalises_the_display_name(raw: str, expected: str) -> None:
    assert starter.starter_fill("VIKI", raw, "v", "2026-01-01").address == expected


def test_starter_fill_replies_in_english() -> None:
    assert starter.starter_fill("VIKI", "Sam", "v", "2026-01-01").language == "English"


def test_starter_fill_passes_vault_id_and_date_through() -> None:
    fill = starter.starter_fill("VIKI", "Sam", "vault-123", "2026-01-02")
    assert (fill.vault_id, fill.date) == ("vault-123", "2026-01-02")


def test_values_matches_the_viki_token_names() -> None:
    assert set(SAMPLE.values()) == {"assistant_name", "address", "language", "vault_id", "date"}


# --- no personal string ships --------------------------------------------------------------

# CJK/kana ideograph blocks, plus the fullwidth-forms block (also catches halfwidth kana). Built
# from code points, not literal characters, so the source file itself stays plain ASCII.
_CJK_BLOCKS = (
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3040, 0x30FF),  # hiragana + katakana
    (0x3400, 0x4DBF),  # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xFF00, 0xFFEF),  # halfwidth and fullwidth forms
)
CJK = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _CJK_BLOCKS) + "]")


def test_cjk_pattern_catches_every_block_and_only_those() -> None:
    # Guards the guard: each block's own boundary characters must trip it, plain ASCII must not.
    boundaries = [chr(lo) for lo, _ in _CJK_BLOCKS] + [chr(hi) for _, hi in _CJK_BLOCKS]
    assert all(CJK.search(sample) for sample in boundaries)
    assert CJK.search("plain ASCII text, nothing to see here") is None


PROVENANCE_TAG = re.compile(r"\(owner (instruction|correction|request|decision)\b")
# \b before the drive letter: without it, any "...s://" URL scheme (http, https, ...) would match,
# since a bare [A-Za-z] matches the scheme's last letter regardless of what precedes it.
HOST_PATH = re.compile(r"\b[A-Za-z]:[\\/]|/Users/|/home/")
PERSONAL_DATE = re.compile(r"\b(19|20)\d\d-\d\d")
# Personal words stay out of this public file: the `starter_words` a contributor keeps in
# scrub_fixture.PRIVATE_FILE are banned anywhere in the starter set, as plain substrings in any
# letter case.
BANNED_WORDS = tuple(word.lower() for word in scrub_fixture.PRIVATE.get("starter_words", ()))


def _static_problems(text: str) -> list[str]:
    """Every personal-string check that holds whether or not the one legitimate date was
    inserted."""
    found = list(scrub_fixture.problems(text))
    found += CJK.findall(text)
    found += [m.group(0) for m in PROVENANCE_TAG.finditer(text)]
    found += [m.group(0) for m in HOST_PATH.finditer(text)]
    lowered = text.lower()
    found += [word for word in BANNED_WORDS if word in lowered]
    return found


def test_no_personal_string_ships(rendered: dict[str, str]) -> None:
    sources = _resource_files()
    for text in sources.values():
        assert _static_problems(text) == []
        assert PERSONAL_DATE.search(text) is None
        assert "japanese" not in text.lower()
    for text in rendered.values():
        assert _static_problems(text) == []
        assert "japanese" not in text.lower()

    # The only date-shaped text anywhere in the rendered output is the one SAMPLE itself supplied
    # (Home.md, Second Brain MOC.md and system/log.md's {{viki.date}}); anything else would be a
    # leaked, real provenance date.
    leaked = {m.group(0) for text in rendered.values() for m in PERSONAL_DATE.finditer(text)}
    assert leaked == {"2026-06"}

    for name in MEMORY_FILES:
        assert re.search(r"(?m)^[ \t]*-[ \t]", rendered[name]) is None, name
        # Pin the shape exactly: a heading and one comment, so no bullet style or prose slips in.
        assert re.fullmatch(r"# Memory: [a-z ]+\n\n<!-- [^\n]* -->\n", rendered[name]), name


def test_the_agents_and_skills_are_exactly_the_decided_set() -> None:
    files_agents = {
        t.removeprefix(".claude/agents/").removesuffix(".md")
        for t in starter.FILES
        if t.startswith(".claude/agents/")
    }
    files_skills = {
        t.removeprefix(".claude/skills/").removesuffix("/SKILL.md")
        for t in starter.FILES
        if t.startswith(".claude/skills/")
    }
    assert files_agents == AGENTS
    assert files_skills == SKILLS

    sources = _resource_files()
    agent_names = {Path(name).stem for name in sources if name.startswith("dot-claude/agents/")}
    skill_names = {Path(name).parts[2] for name in sources if name.startswith("dot-claude/skills/")}
    assert agent_names == AGENTS
    assert skill_names == SKILLS


def test_every_agent_and_skill_names_itself() -> None:
    sources = _resource_files()
    for name in AGENTS:
        _check_named(sources[f"dot-claude/agents/{name}.md"], name)
    for name in SKILLS:
        _check_named(sources[f"dot-claude/skills/{name}/SKILL.md"], name)


def _check_named(source: str, name: str) -> None:
    prefix = f"---\nname: {name}\ndescription: "
    assert source.startswith(prefix), name
    description = source.removeprefix(prefix).split("\n", 1)[0]
    assert description and ": " not in description and " #" not in description, name


def test_welcome_and_moc_name_exactly_the_skill_set() -> None:
    # Parsed from the raw sources, not the rendered output: both lists are plain prose, no tokens.
    sources = _resource_files()
    welcome = sources["Welcome.md"]
    match = re.search(r"skills \(([^)]*)\)", welcome)
    assert match is not None
    assert frozenset(name.strip() for name in match.group(1).split(",")) == SKILLS

    moc = sources["50-maps/Second Brain MOC.md"]
    section = re.search(r"## Skills\n(.*?)\n\n", moc, re.DOTALL)
    assert section is not None
    assert frozenset(re.findall(r"^- /([\w-]+)", section.group(1), re.MULTILINE)) == SKILLS


@pytest.mark.parametrize("name", ["Welcome.md", "system/log.md"])
def test_rendered_files_name_the_current_starter_version(
    rendered: dict[str, str], name: str
) -> None:
    assert re.findall(r"VIKI starter v(\d+)", rendered[name]) == [str(starter.STARTER_VERSION)]


# --- the exact deny list --------------------------------------------------------------------

# Claude Code checks every file-editing tool against Edit rules, so an Edit rule also refuses a
# new file, and it accepts a Write rule without ever matching one. Hence no Write rules, and no
# rule on _raw/: its originals stay create-only through AGENTS.md hard rule 1, so /learn and the
# researcher can still deposit them.
DENY_RULES = (
    "Edit(CLAUDE.md)",
    "Edit(.claude/settings.json)",
    "Edit(.claude/hooks/**)",
    "Edit(.viki/**)",
    "Bash(rm -rf:*)",
    "Bash(git push --force:*)",
    "Bash(git push -f:*)",
    "Bash(git reset --hard:*)",
    "Bash(git clean -fd:*)",
    # The same commands in Claude Code's PowerShell tool, which Bash rules do not match: the only
    # shell on Windows without Git Bash, and opt-in on macOS and Linux. Aliases such as rm and del
    # match as Remove-Item; the two Remove-Item rules catch -Recurse and its short forms anywhere.
    "PowerShell(Remove-Item -r*)",
    "PowerShell(Remove-Item * -r*)",
    "PowerShell(git push --force:*)",
    "PowerShell(git push -f:*)",
    "PowerShell(git reset --hard:*)",
    "PowerShell(git clean -fd:*)",
)


def test_settings_hold_deny_rules_only(rendered: dict[str, str]) -> None:
    assert len(DENY_RULES) == 15
    parsed = json.loads(rendered[".claude/settings.json"])
    assert parsed == {"permissions": {"deny": list(DENY_RULES)}}


def test_no_deny_rule_refuses_a_new_original_in_raw(rendered: dict[str, str]) -> None:
    """/learn and the researcher create originals in _raw/; a rule there would refuse them too."""
    deny = json.loads(rendered[".claude/settings.json"])["permissions"]["deny"]
    assert [rule for rule in deny if "_raw" in rule or rule.startswith("Write(")] == []


# --- the team Kanban's contract --------------------------------------------------------------


def test_the_card_template_holds_the_kanban_contract(rendered: dict[str, str]) -> None:
    card = rendered["_templates/card.md"]
    front = card.split("---\n")[1].splitlines()
    assert {"type: card", "status: todo", "tags: [owner/<seat>]"} <= set(front)
    assert sum(line.startswith("up: ") for line in front) == 1
    headings = ["## Summary", "## Needs you", "## Done when", "## Handoff", "## Check"]
    lines = card.splitlines()
    assert [lines.index(h) for h in headings] == sorted(lines.index(h) for h in headings)
    assert all(lines.count(h) == 1 for h in headings)
    assert any(line.startswith("What: ") for line in lines)
    assert any(line.startswith("Now: ") for line in lines)


def test_a_card_made_from_the_template_waits_for_its_seat(
    rendered: dict[str, str], tmp_path: Path
) -> None:
    """The template keeps the title in its `# ` heading and the tags on one line, which the card
    gate reads even for a title with a comma, colon and apostrophe; the same card with a `title:`
    line, or with its tags as a list, leaves the gate undecided, so the /kanban skill says so."""
    title = "Fix the login bug, then deploy: it's urgent"
    made = (
        rendered["_templates/card.md"]
        .replace("<seat>", "research")
        .replace("<Outcome in plain words, about 60 characters>", title)
    )
    variants = {
        "made": made,
        "titled": made.replace("type: card\n", f"type: card\ntitle: {title}\n", 1),
        "listed": made.replace("tags: [owner/research]\n", "tags:\n- owner/research\n", 1),
    }
    answers: dict[str, bool | None] = {}
    for name, text in variants.items():
        write(tmp_path / name / "kanban" / "card.md", text)
        answers[name] = card_waiting(service(tmp_path / name), WID, "research")
    assert answers == {"made": True, "titled": None, "listed": None}
    flat = re.sub(r"\s+", " ", rendered[".claude/skills/kanban/SKILL.md"])
    assert "Keep `tags:` on that one line" in flat and "add no `title:` line" in flat


def test_agents_md_defines_the_kanban(rendered: dict[str, str]) -> None:
    text = rendered["AGENTS.md"]
    lines = text.splitlines()
    assert lines.count("## Team") == 1
    assert text.index("## Team") > text.index("<!-- /viki:persona -->")
    assert [line for line in lines if line.startswith("type: note")][0].endswith(
        "| thinking | card"
    )
    assert "cards: todo|working|blocked|review|done" in next(
        line for line in lines if line.startswith("status: draft")
    )
    assert sum(line.startswith("| `kanban/` |") for line in lines) == 1
    # The Team section is an overview for the owner's own chats: a Bot skips this file and
    # reads the card format and the seats' rules in the /kanban skill.
    team = text.split("## Team", 1)[1]
    assert "/kanban" in team and "`## Handoff`" not in team


def test_the_kanban_skill_holds_the_card_format_and_every_seats_rules(
    rendered: dict[str, str],
) -> None:
    flat = re.sub(r"\s+", " ", rendered[".claude/skills/kanban/SKILL.md"])
    for token in (
        # the card format
        "`status: todo | working | blocked | review | done | cancelled`",
        "`tags: [owner/<seat>]`",
        "`## Summary`",
        "`## Needs you`",
        "`## Done when`",
        "`## Handoff`",
        "`## Check`",
        # the seats
        "**Chief** is the front door",
        "**Ops** runs the standup and the weekly sweep",
        "**Specialists** each own one domain",
        "checks work in its own context and edits nothing",
        # the rules a seat no longer finds in AGENTS.md
        "Card work starts from a card. Work that goes to another seat, or that needs "
        "the owner's decision, goes on a card",
        "Any other request without a card goes to Chief first.",
        "Work one card at a time.",
        "The next seat reads the card, not your chat",
        "stop at the first that cannot",
        "grants no permission",
        "Fetch any fact that could change an action",
        "propose lessons for /retro, and do not edit `system/memory/` yourself",
        "`system/log.md`",
        # the checker loop
        "in place of any earlier one",
        "A card made without `## Summary` gets one now.",
    ):
        assert token in flat, token
    assert "prime directive" not in flat


def test_a_cancelled_card_is_not_waiting_stale_or_moved_work(
    rendered: dict[str, str], tmp_path: Path
) -> None:
    """SN section G: a cancelled card must not read as waiting, stale or moved work, wherever a
    rule reads card status by list."""
    card = (
        rendered["_templates/card.md"]
        .replace("status: todo", "status: cancelled")
        .replace("<seat>", "research")
        .replace("<Outcome in plain words, about 60 characters>", "Ship the thing")
    )
    write(tmp_path / "kanban" / "card.md", card)
    assert card_waiting(service(tmp_path), WID, "research") is False
    assert rules.present("card_needs_you", {"status": "cancelled"}) is False
    assert rules.present("card_stale", {"status": "cancelled"}) is False
    assert "cancelled" not in seats.MOVED


def test_the_checker_starts_its_verdict(rendered: dict[str, str]) -> None:
    assert "Start with one line: `Verdict: PASS`" in rendered[".claude/agents/checker.md"]


# --- writing for the owner -------------------------------------------------------------------


def test_agents_md_has_the_writing_for_the_owner_section(rendered: dict[str, str]) -> None:
    text = rendered["AGENTS.md"]
    assert text.count("## Writing for the owner") == 1
    assert (
        text.index("<!-- /viki:persona -->")
        < text.index("## Writing for the owner")
        < text.index("## Team")
    )


# --- the persona section's shared grammar --------------------------------------------------

PERSONA_MARKERS = re.compile(r"<!-- viki:persona -->.*?<!-- /viki:persona -->", re.DOTALL)

PERSONA_BLOCK = "\n".join(
    [
        "<!-- viki:persona -->",
        "## Persona",
        "",
        "- Name: {{viki.assistant_name}}",
        "- Address the owner as: {{viki.address}}",
        "- Tone: calm",
        "- Reply language: {{viki.language}}",
        "- Proactivity: coach",
        "",
        "### Do",
        "- Lead with the answer, then the reasoning.",
        "- Check the vault or the original source before answering from memory.",
        "",
        "### Don't",
        "- Pad replies with filler or hype.",
        "<!-- /viki:persona -->",
    ]
)

FILLED_PERSONA_BLOCK = "\n".join(
    [
        "<!-- viki:persona -->",
        "## Persona",
        "",
        "- Name: Ada",
        "- Address the owner as: Sam Lee",
        "- Tone: calm",
        "- Reply language: English",
        "- Proactivity: coach",
        "",
        "### Do",
        "- Lead with the answer, then the reasoning.",
        "- Check the vault or the original source before answering from memory.",
        "",
        "### Don't",
        "- Pad replies with filler or hype.",
        "<!-- /viki:persona -->",
    ]
)


def test_the_persona_fence_has_the_page_grammar() -> None:
    source = (RESOURCES / starter.source_of("AGENTS.md")).read_text(encoding="utf-8")
    match = PERSONA_MARKERS.search(source)
    assert match is not None
    assert match.group(0) == PERSONA_BLOCK

    fill = starter.starter_fill("Ada", "Sam Lee", "vault-x", "2026-01-01")
    assert starter.fill(PERSONA_BLOCK, fill.values()) == FILLED_PERSONA_BLOCK


# --- folders() and source_of() -------------------------------------------------------------


def test_folders_lists_parents_before_children() -> None:
    found = starter.folders()
    assert len(found) == len(set(found))
    index = {folder: position for position, folder in enumerate(found)}
    for folder in found:
        if "/" in folder:
            parent = folder.rsplit("/", 1)[0]
            assert index[parent] < index[folder]
    assert set(starter.EMPTY_FOLDERS) <= set(found)
    assert {".claude/skills/think", ".claude/agents", "system/memory"} <= set(found)


def test_source_of_maps_instruction_files_and_dot_claude() -> None:
    assert starter.source_of("AGENTS.md") == "AGENTS.template.md"
    assert starter.source_of("CLAUDE.md") == "CLAUDE.template.md"
    assert starter.source_of(".claude/settings.json") == "dot-claude/settings.json"
    assert starter.source_of(".claude/skills/think/SKILL.md") == "dot-claude/skills/think/SKILL.md"
    assert starter.source_of("Welcome.md") == "Welcome.md"
    assert starter.source_of("system/log.md") == "system/log.md"


def test_fingerprints_hashes_the_rendered_bytes(rendered: dict[str, str]) -> None:
    prints = starter.fingerprints(list(rendered.items()))
    assert prints.keys() == rendered.keys()
    for target, text in rendered.items():
        assert prints[target] == hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- new vaults say they are an AI ---------------------------------------------------------


def test_the_starter_says_it_is_an_ai(rendered: dict[str, str]) -> None:
    # Utah disclosure: the starter tells the assistant to say it is an AI when asked.
    sentence = (
        "You are an AI assistant, not a person. Whenever anyone asks whether they are "
        "talking to a person or an AI, say that you are an AI."
    )
    normalized = re.sub(r"\s+", " ", rendered["AGENTS.md"])
    assert sentence in normalized
