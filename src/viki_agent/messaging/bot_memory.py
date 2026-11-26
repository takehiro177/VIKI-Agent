"""Native Bot memory locations and the app-generated Claude settings file a Bot session starts
with: its memory folder, auto memory turned off (ADR-087), and the vault instruction files Claude
Code is to skip."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path, PurePath

from viki_agent import osfs
from viki_agent.profiles.mcp_config import write_config
from viki_agent.vaults.vault import BOT_MEMORY_FOLDER

SETTINGS_FOLDER = "bot-memory-settings"
# The instruction files Claude Code reads in the folder a session runs in, and in a folder below
# it once a file there is read: `**/` matches at the top level and at any depth.
INSTRUCTION_GLOBS = ("**/CLAUDE.md", "**/CLAUDE.local.md", "**/AGENTS.md", "**/.claude/rules/**")
# What Claude Code's matcher (picomatch) reads as glob syntax in a pattern. Claude Code turns every
# backslash in a pattern into a slash, which would undo a backslash escape, so each is written as
# a one-character class instead. `!` needs none: it is special only at a pattern's start, where
# these patterns have a drive or a slash, or before `(`, which is escaped.
_GLOB_SYNTAX = frozenset('*?[]{}()|+"')


def _canonical(value: str, name: str) -> str:
    # Each id becomes a path component: only the lowercase hyphenated UUID form passes.
    if str(uuid.UUID(value)) != value:
        raise ValueError(f"invalid {name} identity")
    return value


def memory_ref(bot_id: str) -> str:
    return f"{BOT_MEMORY_FOLDER}/{_canonical(bot_id, 'Bot')}"


def app_memory_area(data: Path, workspace_id: str) -> Path:
    """The folder under the app's data folder that holds one workspace's Bot memory folders."""
    return data / BOT_MEMORY_FOLDER / _canonical(workspace_id, "workspace")


def app_memory_directory(data: Path, workspace_id: str, bot_id: str) -> Path:
    """A Bot's memory folder for a session prepared outside an ordinary vault."""
    return app_memory_area(data, workspace_id) / _canonical(bot_id, "Bot")


def _literal(path: str) -> str:
    return "".join(f"[{char}]" if char in _GLOB_SYNTAX else char for char in path)


def instruction_excludes(*vaults: PurePath) -> list[str]:
    """`claudeMdExcludes` globs naming the instruction files in the vault and its subfolders, once
    per spelling in `vaults`. They spell the path with forward slashes, as Claude Code matches it:
    a Windows vault as C:/a/b (scripts/capture.py scenario 43 found that /c/a/b matches nothing).
    They match nothing outside the vault: not the folders above it, a code copy in the app's data
    folder or a user-level file."""
    roots = dict.fromkeys(_literal(vault.as_posix().rstrip("/")) for vault in vaults)
    return [f"{root}/{glob}" for root in roots for glob in INSTRUCTION_GLOBS]


def _spellings(vault: Path) -> tuple[Path, ...]:
    """`vault` as registered and, on macOS and Linux, as the system spells that folder now.

    Claude Code matches the folder it starts in after `realpathSync`. On macOS that carries the
    name on disk, so a vault renamed in letter case since it was registered has a second
    spelling. On Windows it keeps the spelling it was started in, the registered one.
    """
    if sys.platform == "win32":
        return (vault,)
    try:
        fd = os.open(vault, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return (vault,)
    try:
        now = Path(osfs.final_path(fd))
    except OSError:
        return (vault,)
    finally:
        os.close(fd)
    return (vault,) if now == vault else (vault, now)


def settings_bytes(
    vault: Path, bot_id: str, *, workspace_id: str | None = None, data: Path | None = None
) -> bytes:
    """The settings of a Bot session that runs in `vault`: auto memory off, its folder in
    `<vault>/bot-memory/<bot>`, or, with a workspace id and the app's data folder, in
    `<data>/bot-memory/<workspace>/<bot>` outside the vault; and the vault's own instruction files
    for Claude Code to skip (`instruction_excludes`), whichever folder holds the memory. Every
    existing level of the memory folder must be an ordinary folder; nothing is created."""
    if workspace_id is None:
        if data is not None:
            raise ValueError("a data folder needs a workspace id")
        root = vault
        levels = [vault / BOT_MEMORY_FOLDER, vault / memory_ref(bot_id)]
    else:
        if data is None:
            raise ValueError("a workspace id needs a data folder")
        root = data
        directory = app_memory_directory(data, workspace_id, bot_id)
        levels = [directory.parent.parent, directory.parent, directory]
    if not root.is_absolute() or not vault.is_absolute():
        raise ValueError("memory root must be absolute")
    for path in levels:
        if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir()):
            raise ValueError("memory directory is not an ordinary folder")
    return json.dumps(
        {
            "autoMemoryDirectory": str(levels[-1]),
            # Bots keep no auto memory (ADR-087): what they learn is proposed and approved as
            # memory records. The folder stays named, so a setting that turns auto memory back
            # on still keeps each Bot's notes apart.
            "autoMemoryEnabled": False,
            "claudeMdExcludes": instruction_excludes(*_spellings(vault)),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")


def write_settings(
    directory: Path, vault: Path, bot_id: str, workspace_id: str | None = None
) -> str:
    """Write `settings_bytes` to their private, hash-named file in `directory`
    (`<data>/bot-memory-settings`). With a workspace id the memory folder is under the app's data
    folder, `directory`'s parent."""
    if directory.name != SETTINGS_FOLDER or not directory.is_absolute():
        raise ValueError("invalid Bot settings directory")
    data = None if workspace_id is None else directory.parent
    return str(
        write_config(directory, settings_bytes(vault, bot_id, workspace_id=workspace_id, data=data))
    )
