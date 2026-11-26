"""The one bootstrap that creates a vault folder: a welcome note and an optional manifest, or
the "viki" starter set (`starter.py`) under a manifest that names it.

Every other path in the app is read-only toward a vault. This module writes only inside a
leaf it creates itself, under a parent that passed the deny set, and it can undo exactly what
it wrote. Creation installs no hooks and needs no Git, CLI or login; the starter copies only
the app's own resources.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unicodedata
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from viki_agent import osfs
from viki_agent.domain import CanonicalRoot, Workspace
from viki_agent.vaults import starter_update
from viki_agent.vaults.starter import (
    FILES,
    HISTORY_FILE,
    STARTER_NAME,
    STARTER_VERSION,
    History,
    fingerprints,
    folders,
    load_history,
    render,
    starter_fill,
)
from viki_agent.workspaces import (
    MAX_INSTRUCTION_BYTES,
    RESERVED_NAMES,
    WorkspaceError,
    WorkspaceService,
    chain_has_reparse,
    root_key,
)

WELCOME_FILES = ("Welcome.md",)
MANIFEST = ".viki/vault.json"
MANIFEST_SCHEMA = 1
MAX_NAME_CHARS = 64
_ILLEGAL = '<>:"/\\|?*'
_MANIFEST_DIR = ".viki"
_MAX_MANIFEST_BYTES = 64 * 1024
# The two steps a killed process can stop between. They are named here so a test can make
# one fail without patching `os.mkdir` or `json.dumps` for every other caller in the process.
_mkdir = os.mkdir
_dumps = json.dumps


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _make_dir(path: Path) -> None:
    """`os.mkdir`, at 0700 on POSIX. Windows passes no mode: since Python 3.13 a 0o700 mode
    there gives the new folder a protected DACL (owner, SYSTEM and Administrators) in place
    of the inherited one, which would change what bootstrap does on Windows today."""
    if sys.platform == "win32":
        _mkdir(path)
    else:
        _mkdir(path, 0o700)


def _mkdir_leaf(path: Path) -> None:
    """Create one folder if absent, at 0700 on POSIX; an existing folder keeps its own mode.

    A path that already exists when this is called returns at once, file or directory alike:
    nothing here checks or raises for a pre-existing file in its place. Only a race that lands
    something at the path between that check and `_make_dir` can raise: an already-a-directory
    `EEXIST` from that race is swallowed, the same as pathlib's `exist_ok`; any other race
    re-raises.
    """
    if path.exists():
        return
    try:
        _make_dir(path)
    except FileExistsError:
        if not path.is_dir():
            raise


def _mkdir_chain(path: Path) -> None:
    """Create `path` and any missing ancestor, each new folder at 0700 on POSIX.

    An ancestor that already exists (the suggested parent's user-chosen home, for example)
    is never chmod'd. Recursion stops at a root whose parent is itself (a missing drive), so
    that a vanished volume raises from the mkdir below instead of recursing forever.
    """
    if path.exists():
        return
    if path.parent != path:
        _mkdir_chain(path.parent)
    _mkdir_leaf(path)


def folder_name(name: str) -> str:
    """The folder a vault name may create: NFC, printable, 1-64 chars, no trailing dot/space."""
    if not isinstance(name, str) or len(name) > 4096:
        raise WorkspaceError("invalid_name")
    text = unicodedata.normalize("NFC", name)
    kept = "".join(c for c in text if c.isprintable() and c not in _ILLEGAL)
    cleaned = " ".join(kept.split()).rstrip(" .")
    if (
        not 1 <= len(cleaned) <= MAX_NAME_CHARS
        or cleaned.split(".", 1)[0].upper() in RESERVED_NAMES
    ):
        raise WorkspaceError("invalid_name")
    return cleaned


def suggested_destination(home: Path, name: str) -> tuple[Path, str]:
    """`%USERPROFILE%\\VIKI\\Vaults` and the folder the name would take inside it."""
    return home / "VIKI" / "Vaults", folder_name(name)


def _same_path(candidate: str, suggested: Path) -> bool:
    """True when `candidate` spells the suggested parent, before either need exist."""
    try:
        return root_key(os.path.abspath(candidate)) == root_key(suggested)
    except OSError, ValueError:
        return False


class VaultBootstrap:
    def __init__(
        self,
        workspaces: WorkspaceService,
        *,
        resources: Path,
        home: Path,
        identity: Callable[[], tuple[str, str]] | None = None,
    ) -> None:
        self._workspaces = workspaces
        self._resources = resources
        self._home = home
        # (assistant name, user's display name), read at every starter write.
        self._identity = identity if identity is not None else (lambda: ("VIKI", ""))
        # The shipped starter history, read once and kept: every call after the first reuses it.
        self._history: History | None = None

    def suggestion(self, name: str) -> tuple[Path, str]:
        return suggested_destination(self._home, name)

    def create(
        self,
        command_id: str,
        name: str,
        parent: str | None,
        folder: str | None,
        language: str,
        portable_metadata: bool,
        *,
        template: Literal["blank", "viki"] = "blank",
    ) -> tuple[Workspace, bool, list[str]]:
        """Create one new vault folder, or resume/replay the same command's earlier attempt.

        `language` is ignored: the welcome note and the starter's persona are always English.
        """
        with self._workspaces.registration_lock:
            if not isinstance(name, str) or not name.strip():
                raise WorkspaceError("invalid_name")
            portable_metadata = portable_metadata or template == "viki"  # a starter has a manifest
            leaf = folder_name(folder if folder is not None else name)
            reserved = self._workspaces.by_command(command_id)
            if reserved is not None and reserved.bootstrap == "complete":
                return reserved, False, self._present(Path(reserved.root.display))
            if reserved is not None:
                # The same command always finishes the destination it reserved, whatever the
                # caller repeats in `parent`, `folder` and `portable_metadata`: the reservation
                # decides, never the retrying call.
                destination = Path(reserved.root.display)
                workspace = reserved
                portable_metadata = reserved.portable_id is not None
            else:
                destination = self._destination(parent, leaf)
                self._refuse_link(destination)
                if self._occupied(destination):
                    raise WorkspaceError("destination_not_empty", hint="open_existing")
                if destination.exists():
                    # An existing empty leaf may have different letter case on disk. Reserve
                    # its canonical spelling so completion keeps the destination unchanged.
                    destination = Path(self._workspaces.canonicalise(str(destination)).display)
                workspace = self._workspaces.register_created(
                    command_id,
                    CanonicalRoot(
                        key=root_key(destination),
                        display=str(destination),
                        physical_id=f"pending:{command_id}",
                        aliased=False,
                    ),
                    name.strip()[:256],
                    portable_id=str(uuid.uuid4()) if portable_metadata else None,
                )
                if workspace.bootstrap == "complete":  # a replay that raced this window
                    return workspace, False, self._present(Path(workspace.root.display))
            # A destination whose manifest names the starter resumes as one, whatever this call
            # names: the manifest is the only record of the template a reservation began with. On a
            # fresh reservation `workspace.portable_id` already reflects this call's own template
            # (just reserved above), so the second clause mostly just adds the starter path there --
            # except a blank reservation can carry a `portable_id` too, the same shape a starter's
            # own reservation has. A starter always writes its manifest before any other file, so a
            # welcome note or any manifest already on disk, without the starter's own section,
            # proves the reservation began blank regardless of what a retry now names; the second
            # clause only ever fires while neither is there yet.
            starter = self._is_starter(destination) or (
                template == "viki"
                and workspace.portable_id is not None
                and not (destination / MANIFEST).exists()
                and not (destination / WELCOME_FILES[0]).exists()
            )
            self._write(destination, workspace, name.strip(), portable_metadata, starter)
            try:
                root = self._workspaces.canonicalise(str(destination))
                completed = self._workspaces.complete_bootstrap(workspace.workspace_id, root)
            except WorkspaceError:
                # A destination that cannot be canonicalised, or that resolved into a place no
                # registration may go, never becomes one; what the app wrote is undone first.
                self.cancel(command_id, workspace.workspace_id)
                raise
            return completed, True, self._present(destination)

    def cancel(self, command_id: str, workspace_id: str) -> None:
        """Undo a bootstrap: only the app's own artifacts, and only while nothing else is there.

        `command_id` names the cancelling command, not the creating one: a stale reservation
        is cancelled by the window that met it, which carries its own id.
        """
        with self._workspaces.registration_lock:
            workspace = self._workspaces.get(workspace_id)
            if (
                workspace.bootstrap != "pending"
                or workspace.source != "created"
                or workspace.policy != "ordinary"
            ):
                raise WorkspaceError("not_app_created")
            destination = Path(workspace.root.display)
            # A manifest was written only when this registration reserved a portable id, so a
            # `.viki` folder beside a row without one belongs to someone else.
            portable = workspace.portable_id is not None
            if destination.is_dir():
                section = self._starter_section(destination) if portable else None
                files: tuple[str, ...] = WELCOME_FILES
                emptied: tuple[str, ...] = ()
                if section is None:
                    self._require_app_created(destination, portable=portable)
                else:
                    # The setup's own manifest names what it wrote, whichever starter version
                    # wrote it; every check runs before the first deletion. Only the listed files
                    # the walk found are deleted, so a listed name no file can have (`a?b.md` on
                    # Windows, `<listed file>/x`) is skipped rather than failing midway.
                    listed = self._manifest_files(section)
                    walked, files = self._require_app_created(
                        destination, portable=portable, starter_files=listed
                    )
                    self._require_unedited(destination, section["files"], listed)
                    emptied = tuple(  # deepest first
                        sorted(walked, key=lambda name: (name.count("/"), name), reverse=True)
                    )
                try:
                    for name in files:
                        (destination / name).unlink(missing_ok=True)
                    for name in emptied:
                        if (destination / name).is_dir():
                            (destination / name).rmdir()
                    # The manifest goes last: a cancel stopped midway still names the starter, so
                    # its retry walks the same set.
                    manifest = destination / _MANIFEST_DIR
                    if portable and manifest.is_dir():
                        (manifest / "vault.json").unlink(missing_ok=True)
                        manifest.rmdir()
                except OSError:
                    # What went stays gone; the row stays pending for a retry.
                    raise WorkspaceError("not_app_created") from None
                # The destination folder itself may have existed empty before the bootstrap, so
                # it stays; only what the app wrote is removed.
            self._workspaces.remove_pending(workspace_id)

    def starter_plan(self, workspace_id: str) -> dict[str, object]:
        """The starter update plan for `workspace_id`'s vault: `starter_update.plan` against
        the shipped history, or the empty "none" plan when the workspace isn't yet a usable
        "viki" starter vault (a bootstrap still pending, a root that no longer re-canonicalises
        to its registered identity, an unsafe root, no manifest, or a manifest with no starter
        section). A "complete" bootstrap and the "none" one Open leaves on an existing vault
        (opened on another machine, restored, or re-added) both plan; only "pending" (a
        bootstrap reserved but not yet finished) never does."""
        workspace = self._workspaces.get(workspace_id)
        if workspace.bootstrap == "pending":
            return starter_update.empty_plan("none", None)
        root = self._workspaces.current_root(workspace)
        if root is None:
            return starter_update.empty_plan("none", None)
        destination = Path(root.display)
        # The manifest is read without following links too: a `.viki` junction or a linked
        # `vault.json` would point the plan at another folder's manifest.
        if chain_has_reparse(destination / MANIFEST):
            return starter_update.empty_plan("none", None)
        document = self._manifest_document(destination, follow_symlinks=False)
        if document is None:
            return starter_update.empty_plan("none", None)
        section = document.get("starter")
        if not (isinstance(section, dict) and section.get("name") == STARTER_NAME):
            return starter_update.empty_plan("none", None)
        if self._history is None:
            self._history = load_history(self._resources / HISTORY_FILE)
        return starter_update.plan(destination, document, self._history, self._resources)

    def _destination(self, parent: str | None, leaf: str) -> Path:
        suggested = self._home / "VIKI" / "Vaults"
        if parent is None or _same_path(parent, suggested):
            # The form sends the prefilled suggestion back verbatim; an explicit parent that is
            # the app's own default location must behave exactly as an omitted one, or the
            # first create on a clean profile is refused with `not_found`.
            # Creating the suggested parent is itself a write. Judge where it would land -
            # through its nearest existing ancestor, so a junctioned home resolves first -
            # and refuse a place inside a registration before any directory is made.
            self._workspaces.refuse_inside_registration(self._workspaces.planned_key(suggested))
            try:
                _mkdir_chain(suggested)
            except OSError:
                raise WorkspaceError("unavailable") from None
            root = self._workspaces.canonicalise(str(suggested))
        else:
            root = self._workspaces.canonicalise(parent)
        self._workspaces.refuse_inside_registration(root.key)
        return Path(root.display, leaf)

    @staticmethod
    def _refuse_link(destination: Path) -> None:
        """A destination that is a link is not a destination: it is a write somewhere else.

        `exists()`, `iterdir()` and `open()` all follow reparse points, so this is what keeps
        a junction at the leaf from turning `Welcome.md` into a write inside another root.
        """
        if chain_has_reparse(destination):
            raise WorkspaceError("unsafe_path")

    @staticmethod
    def _occupied(destination: Path) -> bool:
        """True unless the destination is absent or an empty directory."""
        try:
            if not destination.exists():
                return False
            return not destination.is_dir() or any(destination.iterdir())
        except OSError:
            # A file, a device name or an unreadable directory: the caller opens an existing
            # folder instead, and the path never reaches a traceback.
            return True

    def _write(
        self,
        destination: Path,
        workspace: Workspace,
        name: str,
        portable_metadata: bool,
        starter: bool,
    ) -> None:
        # Last gate before any byte: a link planted at the leaf between the reservation
        # and this write would send the note wherever it points, protected roots included.
        self._refuse_link(destination)
        if destination.exists():
            # A starter's tree is judged by its own manifest, so an older one reaches
            # `_write_starter` and its `cancel_bootstrap` hint; with no manifest yet, no file.
            names: tuple[str, ...] | None = None
            if starter:
                section = self._starter_section(destination)
                names = () if section is None else self._manifest_files(section)
            self._require_app_created(destination, portable=portable_metadata, starter_files=names)
        try:
            if starter:
                self._write_starter(destination, workspace, name)
            else:
                self._write_files(destination, workspace, name, portable_metadata)
        except OSError:
            # A death during the write, a full disk and a path the filesystem refuses (long paths
            # without the policy enabled) all leave the reservation for a resume or a cancel.
            raise WorkspaceError("unavailable") from None

    def _write_files(
        self,
        destination: Path,
        workspace: Workspace,
        name: str,
        portable_metadata: bool,
    ) -> None:
        # A race that lands a folder here, between _write's check above and this one, is
        # adopted, not refused: `exists()` alone is true, so `_make_dir` never runs and no
        # EEXIST is ever raised here.
        if not destination.exists():
            _make_dir(destination)
        source = self._resources / "welcome.en.md"
        with source.open("r", encoding="utf-8") as reader:
            text = reader.read()
        self._write_once(destination / WELCOME_FILES[0], text)
        if not portable_metadata:
            return
        manifest = destination / _MANIFEST_DIR
        _mkdir_leaf(manifest)
        body = _dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "vault_id": workspace.portable_id or str(uuid.uuid4()),
                "name": name,
                "created_at": _now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        self._write_once(manifest / "vault.json", body + "\n")

    def _write_starter(self, destination: Path, workspace: Workspace, name: str) -> None:
        """The starter set, in an order a resume can finish.

        Every resource is rendered before any byte lands, so a missing one writes nothing.
        Then come the destination, `.viki` and the manifest that names the starter, then
        every folder, then each file that is not there yet.
        """
        section = self._starter_section(destination)
        if section is not None and section.get("version") != STARTER_VERSION:
            raise WorkspaceError(
                "destination_not_empty",
                hint="cancel_bootstrap",
                workspace_id=workspace.workspace_id,
            )
        vault_id = workspace.portable_id or str(uuid.uuid4())
        assistant_name, display_name = self._identity()
        today = datetime.now().astimezone().date().isoformat()
        values = starter_fill(assistant_name, display_name, vault_id, today)
        rendered = render(self._resources / "starter", values)
        hashes = fingerprints(rendered)
        if section is not None:
            recorded = section.get("files")
            recorded = recorded if isinstance(recorded, dict) else {}
            # Existing files belong to the user now. Missing ones must still match the
            # manifest, or a later interruption would leave additions that cancel cannot undo.
            if any(
                not (destination / target).exists() and digest != recorded.get(target)
                for target, digest in hashes.items()
            ):
                raise WorkspaceError(
                    "destination_not_empty",
                    hint="cancel_bootstrap",
                    workspace_id=workspace.workspace_id,
                )
        if not destination.exists():
            _make_dir(destination)
        manifest = destination / _MANIFEST_DIR
        _mkdir_leaf(manifest)
        body = _dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "vault_id": vault_id,
                "name": name,
                "created_at": _now(),
                "starter": {
                    "name": STARTER_NAME,
                    "version": STARTER_VERSION,
                    "files": hashes,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        self._write_once(manifest / "vault.json", body + "\n")
        for path in folders():
            _mkdir_leaf(destination / path)
        for target, text in rendered:
            self._write_once(destination / target, text)

    @staticmethod
    def _write_once(path: Path, text: str) -> None:
        """Create the file or leave it alone: a resumed bootstrap never rewrites its own work.

        Creating the file and writing its bytes are two separate steps, so a failure in the
        second one (a full disk mid-write) can undo the first: the file this call just created
        is unlinked and the error re-raised, so it never sits on disk half written for a resume
        to adopt as finished work or a cancel to refuse as the user's own edit. A hard kill
        between the two steps is not covered here and can still leave an empty file behind.
        """
        try:
            writer = osfs.open_new_private(path)
        except FileExistsError:
            return
        try:
            with writer:
                writer.write(text.encode("utf-8"))
        except OSError:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _require_app_created(
        destination: Path, *, portable: bool, starter_files: tuple[str, ...] | None = None
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Every entry in the whole tree must be one this command writes, and none a link.

        Without `starter_files` those are the welcome note, and `.viki` and its `vault.json`
        when a manifest was asked for. With them, a starter's, the files are the manifest and
        the names it lists, and any folder may hold them. Any other file or folder at any depth,
        a manifest we never asked for, or a link or reparse point at or above any entry refuses;
        the walk never descends through one. Returns the folders walked, `.viki` excepted, and
        the files found, the manifest excepted, each named as the walk met it on disk.
        """
        starter = starter_files is not None
        known_files: set[str] = set(WELCOME_FILES)
        known_folders: set[str] = set()
        if starter_files is not None:
            known_files = {MANIFEST, *starter_files}
        elif portable:
            known_files.add(MANIFEST)
            known_folders.add(_MANIFEST_DIR)
        walked: list[str] = []
        found: list[str] = []
        try:
            unvisited = [destination]
            while unvisited:
                for entry in unvisited.pop().iterdir():
                    name = entry.relative_to(destination).as_posix()
                    if chain_has_reparse(entry):
                        raise WorkspaceError("not_app_created")
                    if entry.is_dir() and (starter or name in known_folders):
                        unvisited.append(entry)
                        if name != _MANIFEST_DIR:
                            walked.append(name)
                    elif not (entry.is_file() and name in known_files):
                        raise WorkspaceError("not_app_created")
                    elif name != MANIFEST:
                        found.append(name)
        except OSError:
            raise WorkspaceError("not_app_created") from None
        return tuple(walked), tuple(found)

    @staticmethod
    def _require_unedited(
        destination: Path, recorded: dict[str, Any], names: tuple[str, ...]
    ) -> None:
        """Each listed file still on disk must hold the bytes its manifest fingerprinted: one
        edited since is the user's now, so the cancel removes nothing. A listed name that is
        there but not a regular file refuses too."""
        try:
            for name in names:
                path = destination / name
                if path.is_file():
                    # A file past the bound reads truncated, so its digest never matches.
                    data = osfs.read_regular(path, MAX_INSTRUCTION_BYTES, follow_symlinks=False)
                    if hashlib.sha256(data).hexdigest() != recorded.get(name):
                        raise WorkspaceError("not_app_created")
                elif path.exists():
                    raise WorkspaceError("not_app_created")
        except OSError:
            raise WorkspaceError("not_app_created") from None

    @staticmethod
    def _manifest_files(section: dict[str, Any]) -> tuple[str, ...]:
        """The names a starter manifest lists, each a plain relative POSIX path inside the
        folder: no `\\`, `:` or NUL, no leading `/`, and no empty, `.` or `..` segment. Any
        other name refuses the whole set, so nothing is deleted on its say-so."""
        files = section.get("files")
        if not isinstance(files, dict):
            raise WorkspaceError("not_app_created")
        for name in files:
            if (
                not isinstance(name, str)
                or not name
                or any(c in name for c in "\\:\0")
                or name.startswith("/")
                or any(part in ("", ".", "..") for part in name.split("/"))
            ):
                raise WorkspaceError("not_app_created")
        return tuple(files)

    @staticmethod
    def _manifest_document(
        destination: Path, *, follow_symlinks: bool = True
    ) -> dict[str, Any] | None:
        """The manifest's JSON object, when the manifest is a regular file of at most 64 KiB
        holding one; None otherwise (missing, oversized, unreadable, or not a JSON object)."""
        try:
            data = osfs.read_regular(
                destination / MANIFEST, _MAX_MANIFEST_BYTES + 1, follow_symlinks=follow_symlinks
            )
            if len(data) > _MAX_MANIFEST_BYTES:
                return None
            document = json.loads(data.decode("utf-8"))
        except OSError, ValueError, RecursionError:
            return None
        return document if isinstance(document, dict) else None

    @staticmethod
    def _starter_section(destination: Path) -> dict[str, Any] | None:
        """The manifest's `starter` section, when the manifest is a regular file of at most
        64 KiB holding a JSON object whose `starter.name` is the starter's; None otherwise."""
        document = VaultBootstrap._manifest_document(destination)
        section = document.get("starter") if document is not None else None
        if isinstance(section, dict) and section.get("name") == STARTER_NAME:
            return section
        return None

    @classmethod
    def _is_starter(cls, destination: Path) -> bool:
        """True when the manifest names the starter (`_starter_section`)."""
        return cls._starter_section(destination) is not None

    def _present(self, destination: Path) -> list[str]:
        if self._is_starter(destination):
            return [name for name in FILES if (destination / name).is_file()] + [MANIFEST]
        files = [name for name in WELCOME_FILES if (destination / name).is_file()]
        if (destination / ".viki" / "vault.json").is_file():
            files.append(MANIFEST)
        return files
