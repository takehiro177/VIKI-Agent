"""Tests for `viki_agent.vaults.starter_update`: `plan`'s classification of a starter vault
against a shipped `History` (plan section 4).

The `History` here is synthetic, built directly from the dataclass (not `starter.load_history`,
which has its own guard tests): two invented versions, 5 and 7, where version 7's texts are the
*real* current resource texts (so "current" means exactly what ships today) and version 5's
are small, deliberate variants of them (so "replace"/"merge" diffs are meaningful). `AREA.*`
targets `_templates/area.md` (an ordinary managed file); `SETTINGS.*` targets
`.claude/settings.json` (manual, not filled); `AGENTS_*`/`CLAUDE_*` target the two filled
targets. `starter.STARTER_VERSION` has since moved past 7 (FX5 bumped it to 8); version 7 here
is just this fixture's own label for "current" -- its texts are still the *real* current
resource texts, so "current" still means exactly what ships today.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

import viki_agent
from viki_agent.vaults import starter, starter_update

RESOURCES = Path(viki_agent.__file__).parent / "resources"
VAULT_ID = "11111111-1111-4111-8111-111111111111"

AREA_TEMPLATE = (RESOURCES / "starter" / "_templates" / "area.md").read_text(encoding="utf-8")
AGENTS_TEMPLATE = (RESOURCES / "starter" / "AGENTS.template.md").read_text(encoding="utf-8")
CLAUDE_TEMPLATE = (RESOURCES / "starter" / "CLAUDE.template.md").read_text(encoding="utf-8")
SETTINGS_TEMPLATE = (RESOURCES / "starter" / "dot-claude" / "settings.json").read_text(
    encoding="utf-8"
)

AREA_OLD = AREA_TEMPLATE.replace("<Area name>", "<Area name> (old)", 1)
AREA_EDITED = AREA_TEMPLATE.replace("<Area name>", "<Area name> (mine)", 1)
AGENTS_V5 = AGENTS_TEMPLATE.replace(
    "# VIKI vault: agent instructions", "# VIKI vault: agent instructions (v5)", 1
)
CLAUDE_V5 = CLAUDE_TEMPLATE.replace("@AGENTS.md", "@AGENTS.md (v5 note)", 1)
SETTINGS_OLD = SETTINGS_TEMPLATE.replace(
    '"Bash(rm -rf:*)",', '"Bash(rm -rf:*)",\n      "Bash(old-command:*)",', 1
)
SETTINGS_EDITED = SETTINGS_TEMPLATE.replace(
    '"Bash(rm -rf:*)",', '"Bash(rm -rf:*)",\n      "Bash(my-custom-rule:*)",', 1
)

assert len({AREA_TEMPLATE, AREA_OLD, AREA_EDITED}) == 3
assert len({CLAUDE_TEMPLATE, CLAUDE_V5}) == 2
assert len({SETTINGS_TEMPLATE, SETTINGS_OLD, SETTINGS_EDITED}) == 3
assert len({AGENTS_TEMPLATE, AGENTS_V5}) == 2

HISTORY = starter.History(
    texts={
        starter.text_sha(AREA_OLD): AREA_OLD,
        starter.text_sha(AREA_TEMPLATE): AREA_TEMPLATE,
        starter.text_sha(AGENTS_V5): AGENTS_V5,
        starter.text_sha(AGENTS_TEMPLATE): AGENTS_TEMPLATE,
        starter.text_sha(CLAUDE_V5): CLAUDE_V5,
        starter.text_sha(CLAUDE_TEMPLATE): CLAUDE_TEMPLATE,
        starter.text_sha(SETTINGS_OLD): SETTINGS_OLD,
        starter.text_sha(SETTINGS_TEMPLATE): SETTINGS_TEMPLATE,
    },
    versions={
        5: {
            "_templates/area.md": (starter.text_sha(AREA_OLD),),
            "AGENTS.md": (starter.text_sha(AGENTS_V5),),
            "CLAUDE.md": (starter.text_sha(CLAUDE_V5),),
            ".claude/settings.json": (starter.text_sha(SETTINGS_OLD),),
        },
        7: {
            "_templates/area.md": (starter.text_sha(AREA_TEMPLATE),),
            "AGENTS.md": (starter.text_sha(AGENTS_TEMPLATE),),
            "CLAUDE.md": (starter.text_sha(CLAUDE_TEMPLATE),),
            ".claude/settings.json": (starter.text_sha(SETTINGS_TEMPLATE),),
        },
    },
)

# A stand-in Persona block: never equal to any shipped template's default, so splicing it in
# changes only the Persona block and nothing else.
PERSONA_LINES = [
    starter.PERSONA_OPEN,
    "## Persona",
    "",
    "- Name: Aria",
    "- Address the owner as: Boss",
    "- Tone: calm",
    "- Reply language: English",
    "- Proactivity: coach",
    starter.PERSONA_CLOSE,
]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _write_current_vault(root: Path, vault_id: str) -> None:
    """Every `MANAGED` target at its current text: a clean baseline a test can then disturb."""
    for target in starter.MANAGED:
        text = starter_update._current_text(RESOURCES, target, vault_id)
        _write(root.joinpath(*target.split("/")), text)


def _items(result: dict[str, object]) -> list[dict[str, object]]:
    """`result["items"]`, typed: `plan`'s return is a loose `dict[str, object]` (it mirrors a
    JSON response), so tests that filter or iterate the item list narrow it here once."""
    items = result["items"]
    assert isinstance(items, list)
    return items


def _document(
    version: object, files: dict[str, str] | None = None, vault_id: object = VAULT_ID
) -> dict[str, object]:
    return {
        "vault_id": vault_id,
        "starter": {"name": starter.STARTER_NAME, "version": version, "files": files or {}},
    }


# --- empty_plan --------------------------------------------------------------------------


def test_empty_plan_none_has_no_version_and_no_items() -> None:
    assert starter_update.empty_plan("none", None) == {
        "state": "none",
        "from_version": None,
        "to_version": starter.STARTER_VERSION,
        "notify": False,
        "items": [],
        "agents": None,
    }


def test_empty_plan_newer_carries_the_version_that_triggered_it() -> None:
    assert starter_update.empty_plan("newer", 9)["from_version"] == 9
    assert starter_update.empty_plan("newer", 9)["state"] == "newer"


# --- _base_text: the "exact creation base" shortcut vs the version lookup -----------------


def test_base_text_uses_the_manifests_own_sha_as_the_exact_creation_base() -> None:
    older, newer = "old revision\n", "newer revision\n"
    history = starter.History(
        texts={starter.text_sha(older): older, starter.text_sha(newer): newer},
        versions={7: {"x.md": (starter.text_sha(older), starter.text_sha(newer))}},
    )
    # The version's own last text is "newer" (last of the tuple), but this vault was created
    # from "older" specifically: the manifest's own sha must win over that version lookup.
    manifest_files = {"x.md": starter.text_sha(older)}
    assert starter_update._base_text(history, 7, 7, manifest_files, "x.md") == older
    assert starter_update._base_text(history, 7, 7, {}, "x.md") == newer


def test_base_text_falls_back_to_the_version_lookup_off_the_manifest_version() -> None:
    manifest_files = {"_templates/area.md": starter.text_sha(AREA_TEMPLATE)}
    # R (5) isn't the manifest version (7): the shortcut never applies, even though the
    # manifest's own sha is a known one.
    base = starter_update._base_text(HISTORY, 5, 7, manifest_files, "_templates/area.md")
    assert base == AREA_OLD


def test_base_text_is_none_when_the_reviewed_version_never_shipped_the_target() -> None:
    assert starter_update._base_text(HISTORY, 3, 3, {}, "_templates/area.md") is None


# --- _generic_item: absent -----------------------------------------------------------------


def test_generic_item_absent_and_in_manifest_files_does_nothing(tmp_path: Path) -> None:
    manifest_files = {"_templates/area.md": "anything"}
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, manifest_files
    )
    assert item is None


def test_generic_item_absent_and_in_manifest_files_with_no_history_record_does_nothing(
    tmp_path: Path,
) -> None:
    # F7(b): isolates the `target in manifest_files` clause from `target in
    # history.versions.get(reviewed, {})` by using a reviewed version (3) the history never
    # tracked this target at, so only the manifest-files clause can account for the result.
    manifest_files = {"_templates/area.md": "anything"}
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 3, 3, manifest_files
    )
    assert item is None


def test_generic_item_absent_and_in_reviewed_versions_does_nothing(tmp_path: Path) -> None:
    # Reviewed at 7 (where the history tracks this target), created at an older version (3,
    # before the target existed): the owner deleted it after it was reviewed in.
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 3, {}
    )
    assert item is None


def test_generic_item_absent_and_untracked_adds_the_current_text(tmp_path: Path) -> None:
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 3, 3, {}
    )
    assert item == {
        "path": "_templates/area.md",
        "action": "add",
        "diff": None,
        "text": AREA_TEMPLATE,
    }


# --- _generic_item: skip (link, non-regular, oversize, invalid UTF-8) ----------------------


def test_generic_item_skips_a_symlinked_file(tmp_path: Path) -> None:
    real = tmp_path / "real.md"
    _write(real, "actual content\n")
    link = tmp_path / "_templates" / "area.md"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unsupported in this environment")
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item == {"path": "_templates/area.md", "action": "skip", "diff": None, "text": None}


def test_generic_item_skips_when_an_ancestor_directory_is_a_symlink(tmp_path: Path) -> None:
    real_dir = tmp_path / "real_templates"
    real_dir.mkdir()
    _write(real_dir / "area.md", AREA_EDITED)
    link_dir = tmp_path / "_templates"
    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unsupported in this environment")
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item == {"path": "_templates/area.md", "action": "skip", "diff": None, "text": None}


def test_generic_item_skips_a_directory_in_the_files_own_place(tmp_path: Path) -> None:
    (tmp_path / "_templates" / "area.md").mkdir(parents=True)
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item == {"path": "_templates/area.md", "action": "skip", "diff": None, "text": None}


def test_generic_item_skips_a_file_over_the_byte_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(starter_update, "MAX_INSTRUCTION_BYTES", 8)
    _write(tmp_path / "_templates" / "area.md", "a" * 20)
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item == {"path": "_templates/area.md", "action": "skip", "diff": None, "text": None}


def test_generic_item_skips_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "_templates" / "area.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00\x01")
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item == {"path": "_templates/area.md", "action": "skip", "diff": None, "text": None}


def test_generic_item_crlf_and_bom_classify_like_lf(tmp_path: Path) -> None:
    path = tmp_path / "_templates" / "area.md"
    path.parent.mkdir(parents=True)
    path.write_bytes((chr(0xFEFF) + AREA_TEMPLATE.replace("\n", "\r\n")).encode("utf-8"))
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item is None  # normalises to exactly the current text


# --- _generic_item: present, not FILLED -----------------------------------------------------


def test_generic_item_nothing_when_disk_matches_current(tmp_path: Path) -> None:
    _write(tmp_path / "_templates" / "area.md", AREA_TEMPLATE)
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item is None


def test_generic_item_replace_when_disk_is_an_old_shipped_version(tmp_path: Path) -> None:
    _write(tmp_path / "_templates" / "area.md", AREA_OLD)
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item is not None
    assert item["action"] == "replace"
    assert item["text"] is None
    diff = item["diff"]
    assert isinstance(diff, str) and "area.md" in diff


def test_generic_item_manual_replace_for_a_manual_file(tmp_path: Path) -> None:
    _write(tmp_path / ".claude" / "settings.json", SETTINGS_OLD)
    item = starter_update._generic_item(
        tmp_path, ".claude/settings.json", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item is not None
    assert item["action"] == "manual"
    assert item["text"] == SETTINGS_TEMPLATE
    assert item["diff"]


def test_generic_item_nothing_when_edited_but_base_matches_current(tmp_path: Path) -> None:
    _write(tmp_path / "_templates" / "area.md", AREA_EDITED)
    manifest_files = {"_templates/area.md": starter.text_sha(AREA_TEMPLATE)}
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, manifest_files
    )
    assert item is None


def test_generic_item_merge_with_text_when_there_is_no_base(tmp_path: Path) -> None:
    _write(tmp_path / "_templates" / "area.md", AREA_EDITED)
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 3, 3, {}
    )
    assert item == {
        "path": "_templates/area.md",
        "action": "merge",
        "diff": None,
        "text": AREA_TEMPLATE,
    }


def test_generic_item_merge_with_diff_against_the_reviewed_versions_base(tmp_path: Path) -> None:
    _write(tmp_path / "_templates" / "area.md", AREA_EDITED)
    item = starter_update._generic_item(
        tmp_path, "_templates/area.md", VAULT_ID, RESOURCES, HISTORY, 5, 7, {}
    )
    assert item is not None
    assert item["action"] == "merge"
    assert item["text"] is None
    diff = item["diff"]
    assert isinstance(diff, str) and "area.md" in diff


def test_generic_item_manual_merge_with_diff_for_a_manual_file(tmp_path: Path) -> None:
    _write(tmp_path / ".claude" / "settings.json", SETTINGS_EDITED)
    item = starter_update._generic_item(
        tmp_path, ".claude/settings.json", VAULT_ID, RESOURCES, HISTORY, 5, 7, {}
    )
    assert item is not None
    assert item["action"] == "manual"
    assert item["text"] == SETTINGS_TEMPLATE
    assert item["diff"]


# --- _generic_item: CLAUDE.md compares the vault_id-filled texts ---------------------------


def test_generic_item_claude_md_nothing_when_filled_text_matches(tmp_path: Path) -> None:
    current_filled = starter_update._current_text(RESOURCES, "CLAUDE.md", VAULT_ID)
    _write(tmp_path / "CLAUDE.md", current_filled)
    item = starter_update._generic_item(
        tmp_path, "CLAUDE.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item is None


def test_generic_item_claude_md_manual_replace_when_disk_is_an_old_shipped_version(
    tmp_path: Path,
) -> None:
    old_filled = starter.fill_vault_id(CLAUDE_V5, VAULT_ID)
    _write(tmp_path / "CLAUDE.md", old_filled)
    item = starter_update._generic_item(
        tmp_path, "CLAUDE.md", VAULT_ID, RESOURCES, HISTORY, 7, 7, {}
    )
    assert item is not None
    assert item["action"] == "manual"
    assert item["text"] == starter_update._current_text(RESOURCES, "CLAUDE.md", VAULT_ID)
    assert item["diff"]


def test_generic_item_claude_md_manual_merge_when_edited(tmp_path: Path) -> None:
    current_filled = starter_update._current_text(RESOURCES, "CLAUDE.md", VAULT_ID)
    disk_text = current_filled + "\nExtra owner note.\n"
    _write(tmp_path / "CLAUDE.md", disk_text)
    item = starter_update._generic_item(
        tmp_path, "CLAUDE.md", VAULT_ID, RESOURCES, HISTORY, 5, 7, {}
    )
    assert item is not None
    assert item["action"] == "manual"
    assert item["text"] == current_filled
    assert item["diff"]


# --- _agents_item: AGENTS.md compares rules, Persona block cut out -------------------------


def test_agents_item_absent_and_in_reviewed_versions_does_nothing(tmp_path: Path) -> None:
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 7, 7, {})
    assert item is None
    assert agents is None


def test_agents_item_absent_and_untracked_adds_the_current_template(tmp_path: Path) -> None:
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 3, 3, {})
    assert agents is None
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    assert item == {"path": "AGENTS.md", "action": "add", "diff": None, "text": current_filled}


def test_agents_item_skips_a_symlinked_file(tmp_path: Path) -> None:
    real = tmp_path / "real.md"
    _write(real, "actual content\n")
    link = tmp_path / "AGENTS.md"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unsupported in this environment")
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 7, 7, {})
    assert agents is None
    assert item == {"path": "AGENTS.md", "action": "skip", "diff": None, "text": None}


def test_agents_item_nothing_for_a_persona_only_edit(tmp_path: Path) -> None:
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    disk_text = starter.splice(current_filled, PERSONA_LINES)
    assert disk_text is not None
    _write(tmp_path / "AGENTS.md", disk_text)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 7, 7, {})
    assert item is None
    assert agents is None


def test_agents_item_nothing_when_disk_rules_match_current_except_the_version_number(
    tmp_path: Path,
) -> None:
    # FX5 point 3(a): a v7 AGENTS.md (vault_id filled, any Persona) whose rules otherwise match
    # the current (v8) template gives no item -- the version-number line is the only
    # difference, and the comparison (`rules_key`) ignores it. Reviewed/manifest at 3 (a
    # version `HISTORY` never recorded this target at): without the fix, nothing else -- not
    # the "matched an older shipped template" branch, not the base-text merge fallback -- would
    # also land on "no item", so this isolates the version-insensitive compare itself.
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    v7_filled = re.sub(r"starter version \d+", "starter version 7", current_filled, count=1)
    assert v7_filled != current_filled
    disk_text = starter.splice(v7_filled, PERSONA_LINES)
    assert disk_text is not None
    _write(tmp_path / "AGENTS.md", disk_text)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 3, 3, {})
    assert item is None
    assert agents is None


def test_agents_item_nothing_when_only_the_version_number_was_edited(tmp_path: Path) -> None:
    # FX5 point 3(c): editing nothing but the version number on AGENTS.md's first line is not
    # an edit the plan offers back -- the normalised rules still match current exactly. Reviewed
    # at 3 for the same reason as above: no fallback path can paper over a real difference here.
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    disk_text = re.sub(r"starter version \d+", "starter version 1", current_filled, count=1)
    assert disk_text != current_filled
    _write(tmp_path / "AGENTS.md", disk_text)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 3, 3, {})
    assert item is None
    assert agents is None


def test_agents_item_editor_when_rules_match_an_older_shipped_template(tmp_path: Path) -> None:
    agents_v5_filled = starter.fill_vault_id(AGENTS_V5, VAULT_ID)
    disk_text = starter.splice(agents_v5_filled, PERSONA_LINES)
    assert disk_text is not None
    _write(tmp_path / "AGENTS.md", disk_text)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 7, 7, {})
    assert item is not None
    assert item["action"] == "editor"
    assert item["text"] is None
    assert item["diff"]
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    expected_proposal = starter.splice(current_filled, PERSONA_LINES)
    assert agents == {
        "proposal": expected_proposal,
        "sha256": hashlib.sha256(disk_text.encode("utf-8")).hexdigest(),
    }


def test_agents_item_editor_diff_bounds_a_huge_persona_block(tmp_path: Path) -> None:
    # F6: the Persona block (lines, not bytes, repeated) is well under the 1 MiB cap, but
    # running difflib over it directly is unbounded; the diff must hide it behind a placeholder
    # and stay fast regardless. The one shipped-text difference from current sits on the line
    # immediately before the Persona marker, so the placeholder falls inside the diff's own
    # context and this proves it stands in for the block rather than the diff just omitting a
    # huge unchanged run on its own.
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    split = starter.persona_split(current_filled)
    assert split is not None
    before, _, after = split
    old_before = [*before[:-1], before[-1] + " (v5)"]
    old_text = "\n".join(
        old_before + [starter.PERSONA_OPEN, "## Persona", starter.PERSONA_CLOSE] + after
    )
    history = starter.History(
        texts={
            starter.text_sha(old_text): old_text,
            starter.text_sha(current_filled): current_filled,
        },
        versions={
            5: {"AGENTS.md": (starter.text_sha(old_text),)},
            7: {"AGENTS.md": (starter.text_sha(current_filled),)},
        },
    )
    big_block = [starter.PERSONA_OPEN, *(["- a"] * 200_000), starter.PERSONA_CLOSE]
    disk_text = starter.splice(old_text, big_block)
    assert disk_text is not None
    assert len(disk_text.encode("utf-8")) <= 1 << 20
    _write(tmp_path / "AGENTS.md", disk_text)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, history, 7, 7, {})
    assert item is not None
    assert item["action"] == "editor"
    diff = item["diff"]
    assert isinstance(diff, str)
    assert "<!-- viki:persona (unchanged) -->" in diff
    # Not a plain substring check: "- a" is also a substring of the diff's own "--- a/..."
    # header line. Only a real content line (context, added or removed) counts.
    content_lines = [line[1:] for line in diff.splitlines() if line[:1] in (" ", "+", "-")]
    assert "- a" not in content_lines
    assert agents is not None
    proposal = agents["proposal"]
    assert isinstance(proposal, str) and "- a" in proposal  # the proposal keeps the real block


def test_agents_item_lone_cr_in_persona_block_refuses_editor_and_merges(tmp_path: Path) -> None:
    # F10: the Instructions editor refuses a file with a lone CR anywhere in it, so this must
    # not get `editor` even though the rules (outside the Persona block) match v5.
    agents_v5_filled = starter.fill_vault_id(AGENTS_V5, VAULT_ID)
    disk_text = starter.splice(agents_v5_filled, PERSONA_LINES)
    assert disk_text is not None
    raw = disk_text.replace("- Name: Aria", "- Name: Aria\r- extra", 1).encode("utf-8")
    path = tmp_path / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 5, 7, {})
    assert agents is None
    assert item is not None
    assert item["action"] == "merge"


def test_agents_item_mixed_line_endings_refuse_editor_and_merge(tmp_path: Path) -> None:
    # F10: CRLF rules with an LF-only Persona block is a mix the editor refuses too.
    agents_v5_filled = starter.fill_vault_id(AGENTS_V5, VAULT_ID)
    split = starter.persona_split(agents_v5_filled)
    assert split is not None
    before, block, after = split
    raw = ("\r\n".join(before) + "\r\n" + "\n".join(block) + "\n" + "\r\n".join(after)).encode(
        "utf-8"
    )
    path = tmp_path / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 5, 7, {})
    assert agents is None
    assert item is not None
    assert item["action"] == "merge"


def test_agents_item_control_character_in_persona_block_refuses_editor_and_merges(
    tmp_path: Path,
) -> None:
    # F10: a C0 control character is `unsavableAt`'s other refusal reason.
    agents_v5_filled = starter.fill_vault_id(AGENTS_V5, VAULT_ID)
    disk_text = starter.splice(agents_v5_filled, PERSONA_LINES)
    assert disk_text is not None
    raw = disk_text.replace("- Name: Aria", "- Name: Aria\x00", 1).encode("utf-8")
    path = tmp_path / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 5, 7, {})
    assert agents is None
    assert item is not None
    assert item["action"] == "merge"


def test_agents_item_pure_crlf_still_gives_editor(tmp_path: Path) -> None:
    # F10: a uniformly CRLF file is editable (the editor keeps and restores CRLF), so it must
    # still get `editor` when its rules match a shipped template.
    agents_v5_filled = starter.fill_vault_id(AGENTS_V5, VAULT_ID)
    disk_text = starter.splice(agents_v5_filled, PERSONA_LINES)
    assert disk_text is not None
    raw = disk_text.replace("\n", "\r\n").encode("utf-8")
    path = tmp_path / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 7, 7, {})
    assert item is not None
    assert item["action"] == "editor"
    assert agents is not None


def test_agents_item_editor_sha256_is_the_raw_disk_bytes_not_normalised(tmp_path: Path) -> None:
    # F7(a): a BOM+CRLF AGENTS.md whose rules match a shipped template still gives `editor`,
    # with an LF proposal and `agents.sha256` over the raw disk bytes (BOM and CRLF included),
    # never over the normalised text.
    agents_v5_filled = starter.fill_vault_id(AGENTS_V5, VAULT_ID)
    disk_text = starter.splice(agents_v5_filled, PERSONA_LINES)
    assert disk_text is not None
    raw = (chr(0xFEFF) + disk_text.replace("\n", "\r\n")).encode("utf-8")
    path = tmp_path / "AGENTS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 7, 7, {})
    assert item is not None
    assert item["action"] == "editor"
    assert agents is not None
    proposal = agents["proposal"]
    assert isinstance(proposal, str) and "\r" not in proposal  # the proposal is LF
    assert agents["sha256"] == hashlib.sha256(raw).hexdigest()


def test_agents_item_oversized_proposal_falls_back_to_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(starter_update, "_PROPOSAL_LIMIT", 10)
    agents_v5_filled = starter.fill_vault_id(AGENTS_V5, VAULT_ID)
    disk_text = starter.splice(agents_v5_filled, PERSONA_LINES)
    assert disk_text is not None
    _write(tmp_path / "AGENTS.md", disk_text)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 5, 7, {})
    assert agents is None
    assert item is not None
    assert item["action"] == "merge"


def test_agents_item_broken_markers_merges_against_the_base_template(tmp_path: Path) -> None:
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    disk_text = current_filled.replace(
        starter.PERSONA_OPEN, starter.PERSONA_OPEN + "\n" + starter.PERSONA_OPEN, 1
    )
    assert starter.persona_split(disk_text) is None
    _write(tmp_path / "AGENTS.md", disk_text)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 5, 7, {})
    assert agents is None
    assert item is not None
    assert item["action"] == "merge"
    assert item["text"] is None
    assert item["diff"]


def test_agents_item_broken_markers_but_base_matches_current_does_nothing(tmp_path: Path) -> None:
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    disk_text = current_filled.replace(
        starter.PERSONA_OPEN, starter.PERSONA_OPEN + "\n" + starter.PERSONA_OPEN, 1
    )
    _write(tmp_path / "AGENTS.md", disk_text)
    # Reviewed at 7: the base template is the current one, so there is nothing to merge
    # regardless of the disk file's broken markers.
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 7, 7, {})
    assert item is None
    assert agents is None


def test_agents_item_lone_cr_line_endings_are_treated_as_broken_markers(tmp_path: Path) -> None:
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    mac_text = current_filled.replace("\n", "\r")  # no "\n" survives: one giant "line"
    assert starter.persona_split(mac_text) is None
    path = tmp_path / "AGENTS.md"
    path.write_bytes(mac_text.encode("utf-8"))
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 5, 7, {})
    assert agents is None
    assert item is not None
    assert item["action"] == "merge"


def test_agents_item_merges_against_base_when_rules_were_edited_to_match_nothing(
    tmp_path: Path,
) -> None:
    current_filled = starter_update._current_text(RESOURCES, "AGENTS.md", VAULT_ID)
    disk_text = current_filled.replace(
        "You are an AI assistant, not a person.", "You are a wholly different assistant.", 1
    )
    assert disk_text != current_filled
    _write(tmp_path / "AGENTS.md", disk_text)
    item, agents = starter_update._agents_item(tmp_path, VAULT_ID, RESOURCES, HISTORY, 5, 7, {})
    assert agents is None
    assert item is not None
    assert item["action"] == "merge"
    assert item["diff"]
    assert item["text"] is None


# --- _obsolete_item -------------------------------------------------------------------------


def test_obsolete_item_removes_an_untouched_shipped_file(tmp_path: Path) -> None:
    history = starter.History(
        texts={starter.text_sha(AREA_OLD): AREA_OLD},
        versions={3: {"old-note.md": (starter.text_sha(AREA_OLD),)}},
    )
    _write(tmp_path / "old-note.md", AREA_OLD)
    item = starter_update._obsolete_item(tmp_path, "old-note.md", history)
    assert item == {"path": "old-note.md", "action": "remove", "diff": None, "text": None}


def test_obsolete_item_leaves_an_edited_file_alone(tmp_path: Path) -> None:
    history = starter.History(
        texts={starter.text_sha(AREA_OLD): AREA_OLD},
        versions={3: {"old-note.md": (starter.text_sha(AREA_OLD),)}},
    )
    _write(tmp_path / "old-note.md", "the owner's own content now\n")
    assert starter_update._obsolete_item(tmp_path, "old-note.md", history) is None


def test_obsolete_item_absent_does_nothing(tmp_path: Path) -> None:
    history = starter.History(
        texts={starter.text_sha(AREA_OLD): AREA_OLD},
        versions={3: {"old-note.md": (starter.text_sha(AREA_OLD),)}},
    )
    assert starter_update._obsolete_item(tmp_path, "old-note.md", history) is None


def test_obsolete_item_never_emits_skip_for_an_unreadable_file(tmp_path: Path) -> None:
    # Unlike a MANAGED target, an obsolete one that can't be trusted is simply left alone -
    # there is no "skip" action for obsolete targets.
    history = starter.History(
        texts={starter.text_sha(AREA_OLD): AREA_OLD},
        versions={3: {"old-note.md": (starter.text_sha(AREA_OLD),)}},
    )
    (tmp_path / "old-note.md").mkdir()
    assert starter_update._obsolete_item(tmp_path, "old-note.md", history) is None


# --- plan(): document validation -------------------------------------------------------------


def test_plan_none_when_vault_id_is_missing_or_not_a_string(tmp_path: Path) -> None:
    document: dict[str, object] = {
        "starter": {"name": starter.STARTER_NAME, "version": 7, "files": {}}
    }
    assert starter_update.plan(tmp_path, document, HISTORY, RESOURCES) == starter_update.empty_plan(
        "none", None
    )
    document["vault_id"] = 123
    assert starter_update.plan(tmp_path, document, HISTORY, RESOURCES) == starter_update.empty_plan(
        "none", None
    )


def test_plan_none_when_vault_id_is_not_the_bootstraps_uuid4_shape(tmp_path: Path) -> None:
    # F9: a `vault_id` outside the uuid4 shape `vault_storage.py`'s bootstrap writes must never
    # reach any read or comparison below — a lone surrogate, a value with a newline (so it
    # cannot sneak past a naive end-anchored check), and a Persona marker line, none of them
    # a str that fullmatches the shape.
    for bad_vault_id in ("\ud800", VAULT_ID + "\n", starter.PERSONA_OPEN):
        document = _document(starter.STARTER_VERSION, vault_id=bad_vault_id)
        result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
        assert result == starter_update.empty_plan("none", None)


def test_plan_runs_normally_with_a_valid_vault_id(tmp_path: Path) -> None:
    _write_current_vault(tmp_path, VAULT_ID)
    document = _document(starter.STARTER_VERSION)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result["state"] == "current"


def test_plan_none_when_version_is_missing_non_int_or_bool(tmp_path: Path) -> None:
    missing = {"vault_id": VAULT_ID, "starter": {"name": starter.STARTER_NAME, "files": {}}}
    assert starter_update.plan(tmp_path, missing, HISTORY, RESOURCES) == starter_update.empty_plan(
        "none", None
    )
    assert starter_update.plan(
        tmp_path, _document(True), HISTORY, RESOURCES
    ) == starter_update.empty_plan("none", None)
    assert starter_update.plan(
        tmp_path, _document("7"), HISTORY, RESOURCES
    ) == starter_update.empty_plan("none", None)


# --- plan(): newer ----------------------------------------------------------------------------


def test_plan_newer_when_manifest_version_exceeds_starter_version(tmp_path: Path) -> None:
    document = _document(starter.STARTER_VERSION + 1)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result == starter_update.empty_plan("newer", starter.STARTER_VERSION + 1)


def test_plan_newer_when_record_exceeds_starter_version(tmp_path: Path) -> None:
    _write(
        tmp_path / "system" / "starter-updates.md",
        f"---\nstarter_version: {starter.STARTER_VERSION + 2}\n---\n",
    )
    document = _document(starter.STARTER_VERSION)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result == starter_update.empty_plan("newer", starter.STARTER_VERSION + 2)


# --- plan(): a clean, fully-current vault ----------------------------------------------------


def test_plan_state_current_on_a_freshly_matching_vault(tmp_path: Path) -> None:
    _write_current_vault(tmp_path, VAULT_ID)
    document = _document(starter.STARTER_VERSION)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result["state"] == "current"
    assert result["items"] == []
    assert result["notify"] is False
    assert result["from_version"] == starter.STARTER_VERSION
    assert result["agents"] is None


def test_plan_falls_back_to_manifest_version_without_a_record(tmp_path: Path) -> None:
    _write_current_vault(tmp_path, VAULT_ID)
    document = _document(starter.STARTER_VERSION)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result["from_version"] == starter.STARTER_VERSION


def test_plan_ignores_a_malformed_record(tmp_path: Path) -> None:
    _write_current_vault(tmp_path, VAULT_ID)
    _write(tmp_path / "system" / "starter-updates.md", "not frontmatter at all\n")
    document = _document(starter.STARTER_VERSION)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result["from_version"] == starter.STARTER_VERSION


# --- plan(): notify and the record's effect on it --------------------------------------------


def test_plan_notifies_when_available_and_reviewed_is_behind(tmp_path: Path) -> None:
    _write_current_vault(tmp_path, VAULT_ID)
    _write(tmp_path / "_templates" / "area.md", AREA_OLD)
    document = _document(5)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result["state"] == "available"
    assert result["from_version"] == 5
    assert result["notify"] is True
    area_items = [item for item in _items(result) if item["path"] == "_templates/area.md"]
    assert len(area_items) == 1
    assert area_items[0]["action"] == "replace"


def test_plan_valid_record_clears_notify_but_keeps_items_from_bytes(tmp_path: Path) -> None:
    _write_current_vault(tmp_path, VAULT_ID)
    _write(tmp_path / "_templates" / "area.md", AREA_OLD)  # still an old, un-upgraded file
    _write(
        tmp_path / "system" / "starter-updates.md",
        f"---\nstarter_version: {starter.STARTER_VERSION}\n---\n\nReviewed; area.md left as is.\n",
    )
    document = _document(5)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result["from_version"] == starter.STARTER_VERSION
    # F12: reviewed has reached STARTER_VERSION, so the vault is "current" and declined changes
    # are not offered again, even though the replace item is still there (bytes still differ).
    assert result["state"] == "current"
    assert result["notify"] is False
    area_items = [item for item in _items(result) if item["path"] == "_templates/area.md"]
    assert area_items and area_items[0]["action"] == "replace"


def test_plan_state_current_at_starter_version_even_with_a_replace_item(tmp_path: Path) -> None:
    # F12 (owner decision): "a declined change is not offered again" — once reviewed reaches
    # STARTER_VERSION the state is "current" and notify is false, however many non-skip items
    # the vault's bytes still turn up; the page lists them as kept.
    _write_current_vault(tmp_path, VAULT_ID)
    _write(tmp_path / "_templates" / "area.md", AREA_OLD)
    document = _document(starter.STARTER_VERSION)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result["from_version"] == starter.STARTER_VERSION
    assert result["state"] == "current"
    assert result["notify"] is False
    area_items = [item for item in _items(result) if item["path"] == "_templates/area.md"]
    assert area_items and area_items[0]["action"] == "replace"


# --- plan(): items in MANAGED order, then OBSOLETE sorted ------------------------------------


def test_plan_orders_items_managed_then_obsolete_sorted(tmp_path: Path) -> None:
    old_sha = starter.text_sha(AREA_OLD)
    history = starter.History(
        texts={old_sha: AREA_OLD},
        versions={1: {"zzz-note.md": (old_sha,), "aaa-note.md": (old_sha,)}},
    )
    _write(tmp_path / "zzz-note.md", AREA_OLD)
    _write(tmp_path / "aaa-note.md", AREA_OLD)
    document = _document(1)
    result = starter_update.plan(tmp_path, document, history, RESOURCES)
    paths = [item["path"] for item in _items(result)]
    assert paths[: len(starter.MANAGED)] == list(starter.MANAGED)
    assert paths[len(starter.MANAGED) :] == ["aaa-note.md", "zzz-note.md"]


# --- plan(): agents is non-null only with an editor item --------------------------------------


def test_plan_agents_is_non_null_only_with_an_editor_item(tmp_path: Path) -> None:
    _write_current_vault(tmp_path, VAULT_ID)
    agents_v5_filled = starter.fill_vault_id(AGENTS_V5, VAULT_ID)
    disk_text = starter.splice(agents_v5_filled, PERSONA_LINES)
    assert disk_text is not None
    _write(tmp_path / "AGENTS.md", disk_text)
    document = _document(starter.STARTER_VERSION)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    agents_items = [item for item in _items(result) if item["path"] == "AGENTS.md"]
    assert len(agents_items) == 1
    assert agents_items[0]["action"] == "editor"
    agents = result["agents"]
    assert isinstance(agents, dict) and agents["proposal"]
    non_agents_items = [item for item in _items(result) if item["path"] != "AGENTS.md"]
    assert non_agents_items == []  # the rest of the clean vault stays "current"


def test_plan_state_current_when_every_item_is_a_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_current_vault(tmp_path, VAULT_ID)
    monkeypatch.setattr(starter_update, "MAX_INSTRUCTION_BYTES", 4)
    document = _document(starter.STARTER_VERSION)
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    assert result["items"]
    assert all(item["action"] == "skip" for item in _items(result))
    assert result["state"] == "current"
    assert result["notify"] is False


# --- plan(): never touches an owner file, even if a (malformed) history mentions one ----------


def test_plan_ignores_an_owner_target_even_if_history_lists_one(tmp_path: Path) -> None:
    history = starter.History(texts={}, versions={1: {"Home.md": ("deadbeef",)}})
    document = _document(1)
    result = starter_update.plan(tmp_path, document, history, RESOURCES)
    assert all(item["path"] != "Home.md" for item in _items(result))
