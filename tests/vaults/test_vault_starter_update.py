"""Acceptance test for `VaultBootstrap.starter_plan` (plan section 4): a real "viki" vault
pinned at a historical version, with one owner edit and one owner deletion, classified end to
end against the real shipped `starter-history.json` — then "reviewed" to the current version
and re-classified, proving the whole update loop closes.

The vault is built from the real history (`starter.load_history`, not a synthetic `History`,
which `test_starter_update.py` already covers): every version-5 target's own historical text,
with AGENTS.md and CLAUDE.md filled the way `starter.render` fills them. `starter_plan` itself
must never write anything: a tree snapshot before and after each call is asserted equal.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tests.app.test_workspaces import COMMAND, OTHER_COMMAND, service_at
from tests.vaults.test_vault_storage import RESOURCES, _dying_mkdir, bootstrap_at, snapshot
from viki_agent import vault_storage
from viki_agent.vault_storage import MANIFEST, VaultBootstrap
from viki_agent.vaults import starter, starter_update
from viki_agent.workspaces import WorkspaceError

HISTORY = starter.load_history(RESOURCES / starter.HISTORY_FILE)
FROM_VERSION = 5

# One of the five skills whose shipped text actually changed between v5 and the current
# version (verified against the real history below): the owner's own edit lands here, so the
# plan must call it "merge", never "replace".
EDITED_SKILL = ".claude/skills/kanban/SKILL.md"
# A template whose shipped text never changed: deleting it must read as "the owner removed it".
DELETED_TEMPLATE = "_templates/area.md"
# Shipped only from the current version on: absent from a v5 vault by construction, so the
# plan must offer to "add" it.
NEW_SKILL = ".claude/skills/viki-agent/SKILL.md"
OWNER_NOTE = "\n<!-- the owner's own note: not part of any shipped version -->\n"

assert EDITED_SKILL in HISTORY.versions[FROM_VERSION]
assert DELETED_TEMPLATE in HISTORY.versions[FROM_VERSION]
assert NEW_SKILL not in HISTORY.versions[FROM_VERSION]
assert HISTORY.text_at(FROM_VERSION, EDITED_SKILL) != HISTORY.text_at(
    starter.STARTER_VERSION, EDITED_SKILL
)
assert HISTORY.text_at(FROM_VERSION, DELETED_TEMPLATE) == HISTORY.text_at(
    starter.STARTER_VERSION, DELETED_TEMPLATE
)


def _text_at(version: int, target: str, vault_id: str, persona_values: dict[str, str]) -> str:
    """`target`'s own last text at `version`, filled the way `starter.render` fills it."""
    text = HISTORY.text_at(version, target)
    assert text is not None
    if target == "AGENTS.md":
        return starter.fill(text, persona_values)
    if target == "CLAUDE.md":
        return starter.fill_vault_id(text, vault_id)
    return text


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _build_v5_vault(tmp_path: Path) -> tuple[VaultBootstrap, str, Path, str]:
    """A "viki" vault whose every `MANAGED` file is its own v5 text (AGENTS.md and CLAUDE.md
    filled), with a manifest naming version 5 and those files' own hashes, `EDITED_SKILL`
    overwritten with the owner's own text, `DELETED_TEMPLATE` removed, and `NEW_SKILL` never
    written at all (it ships only from a later version)."""
    bootstrap = bootstrap_at(tmp_path)
    workspace, _created, _files = bootstrap.create(
        COMMAND, "V5 Vault", None, None, "en", True, template="viki"
    )
    destination = Path(workspace.root.display)
    vault_id = workspace.portable_id
    assert vault_id is not None
    persona_values = starter.StarterFill(
        assistant_name="Aria",
        address="Boss",
        language="English",
        vault_id=vault_id,
        date="2026-01-01",
    ).values()

    manifest_files: dict[str, str] = {}
    for target in HISTORY.versions[FROM_VERSION]:
        text = _text_at(FROM_VERSION, target, vault_id, persona_values)
        _write(destination.joinpath(*target.split("/")), text)
        manifest_files[target] = starter.text_sha(text)

    destination.joinpath(*NEW_SKILL.split("/")).unlink()
    edited_path = destination.joinpath(*EDITED_SKILL.split("/"))
    _write(edited_path, edited_path.read_text(encoding="utf-8") + OWNER_NOTE)
    destination.joinpath(*DELETED_TEMPLATE.split("/")).unlink()

    manifest_path = destination / MANIFEST
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["starter"] = {
        "name": starter.STARTER_NAME,
        "version": FROM_VERSION,
        "files": manifest_files,
    }
    _write(manifest_path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return bootstrap, workspace.workspace_id, destination, vault_id


# FX5 N1: `NEW_SKILL` got two shas at v7 itself (commit 1b263798 edited it mid-version, without
# a version bump) -- a vault created from the older one must still be offered the newer text.
assert len(HISTORY.versions[7][NEW_SKILL]) == 2


def _build_v7_vault(
    tmp_path: Path, skill_text: str | None = None
) -> tuple[VaultBootstrap, str, Path, str]:
    """A "viki" vault whose every `MANAGED` file is its own last-v7 text (AGENTS.md and
    CLAUDE.md filled), with a manifest naming version 7 and those files' own hashes. With
    `skill_text`, `NEW_SKILL` is written as that text instead of its last-v7 text."""
    bootstrap = bootstrap_at(tmp_path)
    workspace, _created, _files = bootstrap.create(
        COMMAND, "V7 Vault", None, None, "en", True, template="viki"
    )
    destination = Path(workspace.root.display)
    vault_id = workspace.portable_id
    assert vault_id is not None
    persona_values = starter.StarterFill(
        assistant_name="Aria",
        address="Boss",
        language="English",
        vault_id=vault_id,
        date="2026-01-01",
    ).values()

    manifest_files: dict[str, str] = {}
    for target in HISTORY.versions[7]:
        if target == NEW_SKILL and skill_text is not None:
            text = skill_text
        else:
            text = _text_at(7, target, vault_id, persona_values)
        _write(destination.joinpath(*target.split("/")), text)
        manifest_files[target] = starter.text_sha(text)

    manifest_path = destination / MANIFEST
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["starter"] = {"name": starter.STARTER_NAME, "version": 7, "files": manifest_files}
    _write(manifest_path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return bootstrap, workspace.workspace_id, destination, vault_id


def test_v5_vault_plans_replace_merge_add_editor_then_settles_current(tmp_path: Path) -> None:
    bootstrap, workspace_id, destination, vault_id = _build_v5_vault(tmp_path)

    before = snapshot(destination)
    result = bootstrap.starter_plan(workspace_id)
    assert snapshot(destination) == before  # starter_plan never writes

    assert result["state"] == "available"
    assert result["from_version"] == FROM_VERSION
    assert result["to_version"] == starter.STARTER_VERSION
    assert result["notify"] is True

    items = result["items"]
    assert isinstance(items, list)
    by_path = {item["path"]: item for item in items}

    changed = {
        target
        for target in HISTORY.versions[FROM_VERSION]
        if HISTORY.text_at(FROM_VERSION, target) != HISTORY.text_at(starter.STARTER_VERSION, target)
    }
    replaced = changed - {EDITED_SKILL, "AGENTS.md"}
    assert replaced  # at least the untouched changed skills and the changed agent file

    for target in replaced:
        assert by_path[target]["action"] == "replace", target
        assert by_path[target]["text"] is None

    assert by_path[EDITED_SKILL]["action"] == "merge"
    assert by_path["AGENTS.md"]["action"] == "editor"
    current_new_skill = starter_update._current_text(RESOURCES, NEW_SKILL, vault_id)
    assert by_path[NEW_SKILL] == {
        "path": NEW_SKILL,
        "action": "add",
        "diff": None,
        "text": current_new_skill,
    }
    assert DELETED_TEMPLATE not in by_path  # the owner deleted it; leave it gone

    unchanged = set(HISTORY.versions[FROM_VERSION]) - changed - {DELETED_TEMPLATE}
    for target in unchanged:
        assert target not in by_path, target  # untouched and byte-identical: nothing to do
    # .claude/settings.json is MANUAL_FILES, but its v5 and current texts are the same shipped
    # bytes (verified against the real history above the fixtures), so it is one of `unchanged`
    # here rather than a "manual" item; this assertion fails loudly if that ever stops holding.
    assert ".claude/settings.json" in unchanged

    assert set(by_path) == replaced | {EDITED_SKILL, "AGENTS.md", NEW_SKILL}
    owner_files = {"Welcome.md", "Home.md", "50-maps/Second Brain MOC.md", "system/log.md"}
    assert not (owner_files & set(by_path))

    agents = result["agents"]
    assert isinstance(agents, dict)
    current_agents = starter_update._current_text(RESOURCES, "AGENTS.md", vault_id)
    disk_agents = (destination / "AGENTS.md").read_text(encoding="utf-8")
    persona = starter.persona_split(disk_agents)
    assert persona is not None
    assert agents["proposal"] == starter.splice(current_agents, persona[1])
    assert agents["sha256"] == hashlib.sha256(disk_agents.encode("utf-8")).hexdigest()

    # --- apply the plan: write the current text for every replace/add target, accept the
    # AGENTS.md proposal, and record that the owner has reviewed up to the current version ---
    for target in replaced | {NEW_SKILL}:
        current_text = starter_update._current_text(RESOURCES, target, vault_id)
        _write(destination.joinpath(*target.split("/")), current_text)
    _write(destination / "AGENTS.md", agents["proposal"])
    _write(
        destination / starter.RECORD_FILE,
        f"---\nstarter_version: {starter.STARTER_VERSION}\n---\n\nReviewed.\n",
    )

    before = snapshot(destination)
    settled = bootstrap.starter_plan(workspace_id)
    assert snapshot(destination) == before  # starter_plan never writes

    assert settled == {
        "state": "current",
        "from_version": starter.STARTER_VERSION,
        "to_version": starter.STARTER_VERSION,
        "notify": False,
        "items": [],
        "agents": None,
    }


def test_v7_vault_with_last_v7_texts_is_current(tmp_path: Path) -> None:
    """FX5 point 7(a): a vault whose every file is v7's own last text, reviewed at v7, is
    "current" at v8 -- the only shipped change since v7 is AGENTS.md's version-number line,
    which the version-insensitive rules comparison ignores."""
    bootstrap, workspace_id, destination, _vault_id = _build_v7_vault(tmp_path)
    before = snapshot(destination)
    result = bootstrap.starter_plan(workspace_id)
    assert snapshot(destination) == before  # starter_plan never writes

    assert result["state"] == "current"
    assert result["items"] == []
    assert result["notify"] is False


def test_v7_vault_with_owner_edited_agents_rules_gets_no_merge_for_the_bump(
    tmp_path: Path,
) -> None:
    """R1 round 3 N3: an AGENTS.md whose rules the owner edited takes the merge path, and the
    merge base (v7's last template) differs from the current one only by the version number on
    line 1, which the comparison ignores: the bump alone offers no merge."""
    bootstrap, workspace_id, destination, _vault_id = _build_v7_vault(tmp_path)
    agents = destination / "AGENTS.md"
    _write(agents, agents.read_text(encoding="utf-8") + "\n- My own rule, added by the owner.\n")
    before = snapshot(destination)
    result = bootstrap.starter_plan(workspace_id)
    assert snapshot(destination) == before  # starter_plan never writes

    assert result["state"] == "current"
    assert result["items"] == []
    assert result["agents"] is None


def test_v7_vault_with_the_first_v7_skill_text_offers_one_replace(tmp_path: Path) -> None:
    """FX5 point 7(b), N1's own scenario: a vault created early in v7 -- before commit
    1b263798 edited `.claude/skills/viki-agent/SKILL.md` mid-version -- must still be offered
    that edit, even though both texts predate STARTER_VERSION."""
    first_sha = HISTORY.versions[7][NEW_SKILL][0]
    first_text = HISTORY.texts[first_sha]
    bootstrap, workspace_id, destination, _vault_id = _build_v7_vault(
        tmp_path, skill_text=first_text
    )
    before = snapshot(destination)
    result = bootstrap.starter_plan(workspace_id)
    assert snapshot(destination) == before  # starter_plan never writes

    assert result["state"] == "available"
    assert result["notify"] is True
    items = result["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["path"] == NEW_SKILL
    assert items[0]["action"] == "replace"


def test_a_starter_vault_registered_through_open_still_plans(tmp_path: Path) -> None:
    """F8: a vault registered through Open (bootstrap "none" — opened on another machine,
    restored, or re-added) must still learn of starter updates; only a reservation still
    "pending" gives the empty "none" plan."""
    creating = bootstrap_at(tmp_path, service_at(tmp_path))
    workspace, _created, _files = creating.create(
        COMMAND, "Fresh Vault", None, None, "en", True, template="viki"
    )
    destination = Path(workspace.root.display)

    # A different registry (another machine's, or this one's after a restore) that never ran
    # `create`: registering the same real folder through Open leaves `bootstrap == "none"`.
    opener = service_at(tmp_path / "other-machine")
    opened, _reused = opener.register_existing(OTHER_COMMAND, str(destination))
    assert opened.bootstrap == "none"
    opened_bootstrap = bootstrap_at(tmp_path, opener)

    before = snapshot(destination)
    plan = opened_bootstrap.starter_plan(opened.workspace_id)
    assert snapshot(destination) == before  # starter_plan never writes

    assert plan["state"] != "none"
    assert plan["state"] == "current"  # a fresh v8 vault, nothing to classify as changed


def test_a_pending_bootstrap_still_plans_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F8: unlike "none" and "complete", a bootstrap reserved but never finished ("pending")
    has no vault on disk to read yet, so it still gives the empty "none" plan."""
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    parents = tmp_path / "parents"
    parents.mkdir()
    destination = parents / "Dying Vault"

    monkeypatch.setattr(vault_storage, "_mkdir", _dying_mkdir(destination))
    with pytest.raises(WorkspaceError):
        bootstrap.create(COMMAND, "Dying Vault", str(parents), None, "en", True, template="viki")
    monkeypatch.undo()

    pending = service.by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending"
    assert bootstrap.starter_plan(pending.workspace_id) == starter_update.empty_plan("none", None)


def test_plan_at_a_pre_public_manifest_version_merges_without_raising(tmp_path: Path) -> None:
    """FX6 point 3: the shipped history now starts at version 4 (v1-v3 were private-prototype
    only), so a vault whose manifest still names an older version (2) must still plan without
    raising: the owner-edited file gets `merge` with no base (`diff` None) rather than a lookup
    error, since `History.text_at` and every version lookup in `starter_update.py` return None
    for a version the history never recorded."""
    vault_id = "22222222-2222-4222-8222-222222222222"
    target = EDITED_SKILL
    current_text = starter_update._current_text(RESOURCES, target, vault_id)
    _write(tmp_path.joinpath(*target.split("/")), current_text + OWNER_NOTE)
    document = {
        "vault_id": vault_id,
        "starter": {"name": starter.STARTER_NAME, "version": 2, "files": {}},
    }
    result = starter_update.plan(tmp_path, document, HISTORY, RESOURCES)
    items = result["items"]
    assert isinstance(items, list)
    by_path = {item["path"]: item for item in items}
    assert by_path[target] == {
        "path": target,
        "action": "merge",
        "diff": None,
        "text": current_text,
    }


def test_a_vault_folder_replaced_by_a_copy_plans_none(tmp_path: Path) -> None:
    """F11: a probe from the reviewer — rename the vault folder away, then copy it back to the
    same path. The path still holds a byte-identical starter vault, so a plan that merely reads
    `Path(workspace.root.display)` would classify it as before; but its identity (the thing
    `WorkspaceService.current_root` re-checks, the same way every other vault reader does)
    is now a different folder, so the registration no longer resolves and the plan must be
    "none", not whatever the copy's bytes would otherwise classify as."""
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    workspace, _created, _files = bootstrap.create(
        COMMAND, "Swapped Vault", None, None, "en", True, template="viki"
    )
    destination = Path(workspace.root.display)
    assert service.current_root(workspace) is not None

    moved_away = tmp_path / "moved-away"
    destination.rename(moved_away)
    shutil.copytree(moved_away, destination)  # a new folder, same path, same bytes
    assert service.current_root(workspace) is None  # identity no longer matches

    before = snapshot(destination)
    plan = bootstrap.starter_plan(workspace.workspace_id)
    assert snapshot(destination) == before  # starter_plan never writes

    assert plan == starter_update.empty_plan("none", None)
