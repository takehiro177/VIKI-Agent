"""Classifies a starter vault's files against the shipped `starter-history.json`, for the
Settings page's "check for starter updates" action (plan section 4).

Read-only and offline: `plan` reads the vault's own files (bounded, without following links)
and a validated `History`, and never writes anything. The caller has already confirmed the
manifest names the "viki" starter (`starter.STARTER_NAME`) before calling `plan`.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from viki_agent import osfs
from viki_agent.vaults import starter
from viki_agent.workspaces import MAX_INSTRUCTION_BYTES, chain_has_reparse

_PROPOSAL_LIMIT = 1 << 20  # 1 MiB, the bound on the response's one echoed owner block.

# The uuid4 shape `vault_storage.py`'s bootstrap always writes for a vault's `vault_id`
# (`_write_starter`'s `workspace.portable_id or str(uuid.uuid4())`, and `portable_id` itself is
# set only as `str(uuid.uuid4())`): lowercase hex, dash-separated 8-4-4-4-12. `plan` must not use
# a `vault_id` of any other shape (a lone surrogate, an embedded marker line, ...): it is
# untrusted manifest content read straight from the vault's `.viki/vault.json`.
_VAULT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# The single line that stands in for the owner's Persona block on both sides of an `editor`
# diff. The block is the same list of lines on both sides already (the proposal is spliced from
# the disk file's own block), so the diff never needs to show it; replacing it before calling
# difflib bounds the diff's cost by the shipped template's size, not the block's. The
# `agents.proposal` returned to the caller still carries the full, real block.
_PERSONA_PLACEHOLDER = "<!-- viki:persona (unchanged) -->"

# The read protocol's three outcomes: an existing, readable, in-bounds UTF-8 regular file; a
# path with nothing there; or anything this module refuses to trust (a link in the chain, a
# non-regular file, one over the size cap, or bytes that are not valid UTF-8).
_OK = "ok"
_ABSENT = "absent"
_SKIP = "skip"


def empty_plan(state: Literal["none", "newer"], version: int | None) -> dict[str, object]:
    """A plan with nothing to do: the manifest was missing or malformed (`"none"`), or the
    record or the manifest names a version past this build's `starter.STARTER_VERSION`
    (`"newer"`) — either way nothing here is read closely enough to plan anything."""
    return {
        "state": state,
        "from_version": version,
        "to_version": starter.STARTER_VERSION,
        "notify": False,
        "items": [],
        "agents": None,
    }


def plan(
    root: Path, document: Mapping[str, object], history: starter.History, resources: Path
) -> dict[str, object]:
    """Classify every `starter.MANAGED` target and every obsolete history target under `root`
    against `history`, as of `document` (the vault's `.viki/vault.json`, whose `starter.name`
    the caller has already checked). `resources` is the app's resources root (the parent of
    `resources/starter`, the same tree `vault_storage.py` renders from)."""
    vault_id = document.get("vault_id")
    if not isinstance(vault_id, str) or _VAULT_ID.fullmatch(vault_id) is None:
        return empty_plan("none", None)
    section = document.get("starter")
    version = section.get("version") if isinstance(section, Mapping) else None
    if not isinstance(version, int) or isinstance(version, bool):
        return empty_plan("none", None)
    manifest_files = section.get("files") if isinstance(section, Mapping) else None
    if not isinstance(manifest_files, Mapping):
        manifest_files = {}

    record = _record_version(root)
    if record is not None and record > starter.STARTER_VERSION:
        return empty_plan("newer", record)
    if version > starter.STARTER_VERSION:
        return empty_plan("newer", version)
    in_range = record is not None and version <= record <= starter.STARTER_VERSION
    reviewed = record if in_range and record is not None else version

    items: list[dict[str, object]] = []
    agents: dict[str, object] | None = None
    for target in starter.MANAGED:
        if target == "AGENTS.md":
            item, agents = _agents_item(
                root, vault_id, resources, history, reviewed, version, manifest_files
            )
        else:
            item = _generic_item(
                root, target, vault_id, resources, history, reviewed, version, manifest_files
            )
        if item is not None:
            items.append(item)
    for target in sorted(history.targets() - set(starter.FILES)):
        item = _obsolete_item(root, target, history)
        if item is not None:
            items.append(item)

    # A declined change is not offered again (owner decision, F12): once the reviewed version
    # reaches `STARTER_VERSION`, the vault is "current" and never notifies, however many
    # non-skip items bytes still turn up. Those items are still returned, so the page can list
    # them as kept rather than hide them.
    available = reviewed < starter.STARTER_VERSION and any(
        item["action"] != "skip" for item in items
    )
    state: Literal["current", "available"] = "available" if available else "current"
    return {
        "state": state,
        "from_version": reviewed,
        "to_version": starter.STARTER_VERSION,
        "notify": available,
        "items": items,
        "agents": agents,
    }


def _read(root: Path, target: str) -> tuple[str, bytes | None, str | None]:
    """The read protocol every target (and `starter.RECORD_FILE`) shares. Never raises: a
    reparse point anywhere in the chain, a missing path, a non-regular file, one over
    `MAX_INSTRUCTION_BYTES`, or bytes that fail strict UTF-8 all come back as `_SKIP` or
    `_ABSENT`, never an exception."""
    path = root.joinpath(*target.split("/"))
    if chain_has_reparse(path):
        return _SKIP, None, None
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _ABSENT, None, None
    except OSError:
        return _SKIP, None, None
    if not stat.S_ISREG(info.st_mode):
        return _SKIP, None, None
    try:
        data = osfs.read_regular(path, MAX_INSTRUCTION_BYTES + 1, follow_symlinks=False)
    except OSError:
        return _SKIP, None, None
    if len(data) > MAX_INSTRUCTION_BYTES:
        return _SKIP, None, None
    text = starter.normal_text(data)
    if text is None:
        return _SKIP, None, None
    return _OK, data, text


def _record_version(root: Path) -> int | None:
    """The record's `starter_version`, or `None` on anything short of a clean read and a
    well-formed frontmatter line — the fallback this module treats the same as no record."""
    kind, _, text = _read(root, starter.RECORD_FILE)
    if kind != _OK or text is None:
        return None
    return starter.record_version(text)


def _current_text(resources: Path, target: str, vault_id: str) -> str:
    """The target's current shipped text: the resource as it reads, `fill_vault_id`'d for
    `AGENTS.md`/`CLAUDE.md` only (every other target carries no such token to fill)."""
    raw = (resources / "starter" / starter.source_of(target)).read_text(encoding="utf-8")
    if target in ("AGENTS.md", "CLAUDE.md"):
        return starter.fill_vault_id(raw, vault_id)
    return raw


def _shipped_filled(history: starter.History, target: str, vault_id: str) -> dict[str, str]:
    """Every text `target` has ever shipped, keyed by its sha256 after the same vault_id fill
    `_current_text` applies — so a disk hash can be looked up against either uniformly."""
    fill = target in ("AGENTS.md", "CLAUDE.md")
    result: dict[str, str] = {}
    for sha in history.shipped(target):
        raw = history.texts[sha]
        text = starter.fill_vault_id(raw, vault_id) if fill else raw
        result[starter.text_sha(text)] = text
    return result


def _base_text(
    history: starter.History,
    reviewed: int,
    manifest_version: int,
    manifest_files: Mapping[str, object],
    target: str,
) -> str | None:
    """The base an edited file is diffed against: the exact creation text when the reviewed
    version is the manifest's own and the manifest's own sha is a known one, else the last text
    `history` shipped `target` at the reviewed version (`None` when it shipped none)."""
    if reviewed == manifest_version:
        sha = manifest_files.get(target)
        if isinstance(sha, str) and sha in history.texts:
            return history.texts[sha]
    return history.text_at(reviewed, target)


def _unified_diff(a: str, b: str, path: str) -> str:
    return "\n".join(
        difflib.unified_diff(a.splitlines(), b.splitlines(), f"a/{path}", f"b/{path}", lineterm="")
    )


def _generic_item(
    root: Path,
    target: str,
    vault_id: str,
    resources: Path,
    history: starter.History,
    reviewed: int,
    manifest_version: int,
    manifest_files: Mapping[str, object],
) -> dict[str, object] | None:
    """Every `MANAGED` target but `AGENTS.md`, which compares rules instead (see
    `_agents_item`). `CLAUDE.md` runs the same steps as every other target here, just with its
    one `{{viki.vault_id}}` token filled on every text compared, per `_current_text` /
    `_shipped_filled` / the vault_id fill `_base_text`'s result gets below."""
    kind, _, disk_text = _read(root, target)
    if kind == _SKIP:
        return {"path": target, "action": "skip", "diff": None, "text": None}

    current_text = _current_text(resources, target, vault_id)
    if kind == _ABSENT:
        if target in manifest_files or target in history.versions.get(reviewed, {}):
            return None  # the owner deleted it; leave it gone.
        return {"path": target, "action": "add", "diff": None, "text": current_text}

    assert disk_text is not None
    current_hash = starter.text_sha(current_text)
    disk_hash = starter.text_sha(disk_text)
    if disk_hash == current_hash:
        return None

    shipped = _shipped_filled(history, target, vault_id)
    manual = target in starter.MANUAL_FILES
    if disk_hash in shipped:
        diff = _unified_diff(shipped[disk_hash], current_text, target)
        action = "manual" if manual else "replace"
        text = current_text if manual else None
        return {"path": target, "action": action, "diff": diff, "text": text}

    base_raw = _base_text(history, reviewed, manifest_version, manifest_files, target)
    if base_raw is None:
        return {"path": target, "action": "merge", "diff": None, "text": current_text}
    fill = target in ("AGENTS.md", "CLAUDE.md")
    base_text = starter.fill_vault_id(base_raw, vault_id) if fill else base_raw
    if starter.text_sha(base_text) == current_hash:
        return None
    diff = _unified_diff(base_text, current_text, target)
    action = "manual" if manual else "merge"
    text = current_text if manual else None
    return {"path": target, "action": action, "diff": diff, "text": text}


# Mirrors `instructionText.ts`'s `editableText`/`unsavableAt`: the Instructions editor refuses
# to open a file with a C0 control character (besides tab, LF and CR) or with mixed or lone-CR
# line endings, since a textarea could not round-trip either unchanged. Lone surrogates are the
# editor's other refusal, but `_read`'s strict UTF-8 decode already rules those out before this
# runs. A file the editor refuses must never get `editor` here either.
_CONTROL_CHAR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_LONE_CR = re.compile(r"\r(?!\n)")
_BARE_LF = re.compile(r"(?<!\r)\n")


def _editor_refuses(raw_bytes: bytes) -> bool:
    """Whether the Instructions editor would refuse `raw_bytes` (already valid UTF-8, per
    `_read`), on the same text `editableText` checks: the raw bytes with one leading BOM
    dropped, before any CRLF normalisation."""
    text = raw_bytes.decode("utf-8")
    if text.startswith(chr(0xFEFF)):
        text = text[1:]
    if _CONTROL_CHAR.search(text) is not None:
        return True
    return "\r" in text and (_LONE_CR.search(text) is not None or _BARE_LF.search(text) is not None)


def _agents_item(
    root: Path,
    vault_id: str,
    resources: Path,
    history: starter.History,
    reviewed: int,
    manifest_version: int,
    manifest_files: Mapping[str, object],
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """`AGENTS.md`: compared on its rules (the text with the Persona block cut out), not its
    raw bytes, since the Persona block is the owner's to customise freely. Returns the item (or
    `None`) and the `agents` proposal (non-`None` only for an `editor` item)."""
    target = "AGENTS.md"
    kind, raw_bytes, disk_text = _read(root, target)
    if kind == _SKIP:
        return {"path": target, "action": "skip", "diff": None, "text": None}, None

    current_text = _current_text(resources, target, vault_id)
    if kind == _ABSENT:
        if target in manifest_files or target in history.versions.get(reviewed, {}):
            return None, None
        return {"path": target, "action": "add", "diff": None, "text": current_text}, None

    assert disk_text is not None and raw_bytes is not None
    # `rules_key`, not `rules_of`: a version bump alone (the shipped template's first line)
    # must never read as an edit nobody was offered (F: N1/N2's version-insensitive compare).
    current_rules = starter.rules_key(current_text)
    disk_rules = starter.rules_key(disk_text)
    if disk_rules is not None and disk_rules == current_rules:
        return None, None

    matched = (
        not _editor_refuses(raw_bytes)
        and disk_rules is not None
        and any(
            starter.rules_key(starter.fill_vault_id(history.texts[sha], vault_id)) == disk_rules
            for sha in history.shipped(target)
        )
    )
    if matched:
        persona = starter.persona_split(disk_text)
        if persona is not None:
            proposal = starter.splice(current_text, persona[1])
            if proposal is not None and len(proposal.encode("utf-8")) <= _PROPOSAL_LIMIT:
                # Bound the diff's cost by the shipped template's size: the owner's Persona
                # block is the same list of lines on both sides already, so stand it in with one
                # placeholder line before handing either text to difflib (F6).
                diff_disk = starter.splice(disk_text, [_PERSONA_PLACEHOLDER])
                diff_proposal = starter.splice(proposal, [_PERSONA_PLACEHOLDER])
                assert diff_disk is not None and diff_proposal is not None
                diff = _unified_diff(diff_disk, diff_proposal, target)
                agents: dict[str, object] = {
                    "proposal": proposal,
                    "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                }
                return {"path": target, "action": "editor", "diff": diff, "text": None}, agents

    base_raw = _base_text(history, reviewed, manifest_version, manifest_files, target)
    if base_raw is None:
        return {"path": target, "action": "merge", "diff": None, "text": current_text}, None
    base_text = starter.fill_vault_id(base_raw, vault_id)
    if starter.rules_key(base_text) == current_rules:
        return None, None
    diff = _unified_diff(base_text, current_text, target)
    return {"path": target, "action": "merge", "diff": diff, "text": None}, None


def _obsolete_item(root: Path, target: str, history: starter.History) -> dict[str, object] | None:
    """A target `history` once shipped that `starter.FILES` no longer names: removed only when
    it is still, byte for byte, one of the versions this app shipped; left alone (an owner file
    could have reused the name, or edited it) otherwise."""
    kind, _, text = _read(root, target)
    if kind != _OK or text is None:
        return None
    if starter.text_sha(text) in history.shipped(target):
        return {"path": target, "action": "remove", "diff": None, "text": None}
    return None
