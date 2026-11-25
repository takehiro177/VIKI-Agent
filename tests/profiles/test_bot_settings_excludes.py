"""A Bot's settings name its vault's own instruction files, and nothing else, for Claude Code to
skip.

Claude Code matches `claudeMdExcludes` as globs (picomatch, dot files included) against absolute
paths, with backslashes turned into slashes in the pattern and in the path. `glob.translate`
stands in for that matcher here: it reads one-character classes and `**` the same way."""

from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

import pytest

from viki_agent import osfs
from viki_agent.messaging.bot_memory import (
    INSTRUCTION_GLOBS,
    SETTINGS_FOLDER,
    instruction_excludes,
    settings_bytes,
    write_settings,
)

OWNER = "00000000-0000-4000-8000-000000000001"
WORKSPACE = "00000000-0000-4000-8000-000000000002"
INSIDE = (
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    ".claude/CLAUDE.md",
    ".claude/AGENTS.md",
    ".claude/rules/style.md",
    ".claude/rules/deep/more.md",
    "notes/CLAUDE.md",
    "notes/deeper/AGENTS.md",
    "notes/CLAUDE.local.md",
    "notes/.claude/rules/style.md",
)
OUTSIDE = (
    "notes/plan.md",
    f"bot-memory/{OWNER}/MEMORY.md",
    f"bot-memory/{OWNER}/preferences.md",
    "CLAUDE.md.bak",
    ".claude/settings.json",
    ".claude/skills/kanban/SKILL.md",
)
NAMES = ("My Vault", "a [b] c", "x{y,z}", "p(q)r", "e+f!g@h", "a]b[c", "[!x]")


def matched(patterns: list[str], path: PurePath | str) -> bool:
    spelled = str(path).replace("\\", "/")
    return any(
        re.match(
            glob.translate(
                pattern.replace("\\", "/"), recursive=True, include_hidden=True, seps="/"
            ),
            spelled,
        )
        for pattern in patterns
    )


def test_a_windows_vault_is_named_with_its_drive_and_forward_slashes() -> None:
    # Claude Code matches C:/a/b and not /c/a/b (scripts/capture.py scenario 43, 2.1.281).
    assert instruction_excludes(PureWindowsPath(r"E:\Notes\My Vault")) == [
        "E:/Notes/My Vault/**/CLAUDE.md",
        "E:/Notes/My Vault/**/CLAUDE.local.md",
        "E:/Notes/My Vault/**/AGENTS.md",
        "E:/Notes/My Vault/**/.claude/rules/**",
    ]


def test_a_posix_vault_is_named_as_it_is() -> None:
    assert instruction_excludes(PurePosixPath("/srv/notes/My Vault")) == [
        "/srv/notes/My Vault/**/CLAUDE.md",
        "/srv/notes/My Vault/**/CLAUDE.local.md",
        "/srv/notes/My Vault/**/AGENTS.md",
        "/srv/notes/My Vault/**/.claude/rules/**",
    ]


def test_a_drive_root_and_a_share_keep_one_slash() -> None:
    assert instruction_excludes(PureWindowsPath("D:\\")) == [
        f"D:/{glob_}" for glob_ in INSTRUCTION_GLOBS
    ]
    assert instruction_excludes(PureWindowsPath(r"\\server\share\Vault")) == [
        f"//server/share/Vault/{glob_}" for glob_ in INSTRUCTION_GLOBS
    ]


def test_glob_characters_in_a_vault_name_are_one_character_classes() -> None:
    [pattern, *_] = instruction_excludes(PurePosixPath('/v/a*b?c[d]e{f}g(h)i|j+k"l!m@n'))
    assert pattern == '/v/a[*]b[?]c[[]d[]]e[{]f[}]g[(]h[)]i[|]j[+]k["]l!m@n/**/CLAUDE.md'
    assert "\\" not in pattern  # Claude Code turns a backslash in a pattern into a slash


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("windows", [True, False], ids=["windows", "posix"])
def test_the_globs_match_the_vaults_instruction_files_only(name: str, windows: bool) -> None:
    parent: PurePath = PureWindowsPath(r"E:\Notes") if windows else PurePosixPath("/srv/notes")
    vault = parent / name
    patterns = instruction_excludes(vault)
    for relative in INSIDE:
        assert matched(patterns, vault / relative), relative
    for relative in OUTSIDE:
        assert not matched(patterns, vault / relative), relative
    # Not a folder above the vault, a sibling, the app's code copies or a user-level file.
    others = [
        parent / "CLAUDE.md",
        parent / "AGENTS.md",
        parent / f"{name} copy" / "CLAUDE.md",
        parent / f"{name}x" / "AGENTS.md",
        parent / "data" / "worktrees" / OWNER / "CLAUDE.md",
        parent / ".claude" / "CLAUDE.md",
    ]
    loose = re.sub(r"[\[\]{}()+]", "", name)  # what a glob reading of the name would also match
    if loose != name:
        others.append(parent / loose / "CLAUDE.md")
    for path in others:
        assert not matched(patterns, path), path


def test_the_settings_name_the_vault_wherever_the_memory_is(tmp_path: Path) -> None:
    vault, data = tmp_path / "My Vault", tmp_path / "data"
    vault.mkdir()
    in_vault = settings_bytes(vault, OWNER)
    outside = settings_bytes(vault, OWNER, workspace_id=WORKSPACE, data=data)
    excludes = instruction_excludes(vault)
    assert in_vault == json.dumps(
        {
            "autoMemoryDirectory": str(vault / "bot-memory" / OWNER),
            "autoMemoryEnabled": False,
            "claudeMdExcludes": excludes,
        },
        separators=(",", ":"),
    ).encode("ascii")
    assert json.loads(outside) == {
        "autoMemoryDirectory": str(data / "bot-memory" / WORKSPACE / OWNER),
        "autoMemoryEnabled": False,
        "claudeMdExcludes": excludes,
    }
    for document in map(json.loads, (in_vault, outside)):
        memory = Path(document["autoMemoryDirectory"])
        assert not matched(document["claudeMdExcludes"], memory / "MEMORY.md")
    folder = data / SETTINGS_FOLDER  # with a workspace id the data folder is its parent
    data.mkdir()
    written = Path(write_settings(folder, vault, OWNER, WORKSPACE))
    assert written.parent == folder and written.read_bytes() == outside
    assert not (vault / "bot-memory").exists() and not (data / "bot-memory").exists()


def test_each_spelling_of_the_vault_is_named_once() -> None:
    registered, on_disk = PurePosixPath("/Users/me/Vault"), PurePosixPath("/Users/me/VAULT")
    assert instruction_excludes(registered, on_disk) == [
        *(f"/Users/me/Vault/{glob_}" for glob_ in INSTRUCTION_GLOBS),
        *(f"/Users/me/VAULT/{glob_}" for glob_ in INSTRUCTION_GLOBS),
    ]
    assert instruction_excludes(registered, registered) == instruction_excludes(registered)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS and Linux report the spelling now")
def test_the_settings_also_name_the_folder_as_the_system_spells_it_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On macOS a vault renamed in letter case since it was registered is reported by its name on
    # disk, and Claude Code starts from that spelling.
    vault, renamed = tmp_path / "Vault", tmp_path / "VAULT"
    vault.mkdir()
    monkeypatch.setattr(osfs, "final_path", lambda fd: str(renamed))
    assert json.loads(settings_bytes(vault, OWNER))["claudeMdExcludes"] == instruction_excludes(
        vault, renamed
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows keeps the spelling it starts in")
def test_on_windows_the_settings_keep_the_registered_spelling(tmp_path: Path) -> None:
    # Claude Code 2.1.281 on Windows skipped the instruction files of a vault renamed in letter
    # case after registration through its registered spelling, which the app starts it in.
    vault = tmp_path / "Case Vault"
    vault.mkdir()
    vault.rename(tmp_path / "CASE VAULT")
    excludes = json.loads(settings_bytes(vault, OWNER))["claudeMdExcludes"]
    assert excludes == instruction_excludes(vault)


def test_a_vault_that_cannot_be_opened_keeps_its_registered_spelling(tmp_path: Path) -> None:
    missing = tmp_path / "Missing"
    excludes = json.loads(settings_bytes(missing, OWNER))["claudeMdExcludes"]
    assert excludes == instruction_excludes(missing)


def test_a_data_folder_and_a_workspace_id_go_together(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    for call in (
        lambda: settings_bytes(vault, OWNER, workspace_id=WORKSPACE),
        lambda: settings_bytes(vault, OWNER, data=tmp_path / "data"),
        lambda: settings_bytes(Path("Vault"), OWNER),
        lambda: settings_bytes(Path("Vault"), OWNER, workspace_id=WORKSPACE, data=tmp_path),
    ):
        with pytest.raises(ValueError):
            call()
