"""Bounded, handle-validated, read-only Markdown vault service."""

from __future__ import annotations

import codecs
import ctypes
import errno
import hashlib
import itertools
import os
import re
import stat
import sys
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Literal, overload
from urllib.parse import unquote_to_bytes, urlsplit

from viki_agent import osfs
from viki_agent.conversations.conversation_search import Budget, match_span
from viki_agent.conversations.turn_parts import (
    EXCERPT_MAX_BYTES,
    EXCERPT_MAX_LINES,
    MAX_LINE,
    render_excerpt_block,
)
from viki_agent.domain import CanonicalRoot
from viki_agent.vaults.note_metadata import (
    DisplayMetadata,
    StructureState,
    extract_display,
    extract_review,
)
from viki_agent.vaults.vault_git import MAX_COMMITS, NoteHistory, note_history
from viki_agent.workspaces import WorkspaceError, WorkspaceService, root_key

MAX_ENTRIES = 5_000
MAX_VISITED_ITEMS = 20_000
MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_DISPLAY_PREFIX_BYTES = 32 * 1024
MAX_METADATA_BATCH_BYTES = 1024 * 1024
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_QUERY_CHARS = 256
MAX_EXCERPT_CHARS = 600
MAX_HITS = 100
MAX_LINKS = 256
MAX_DEPTH = 64
DEADLINE_SECONDS = 1.0
# The overview reads only the review folders: a note's first 4 KiB, a map's first 32 KiB.
MAPS_FOLDER = "50-maps"
BOT_MEMORY_FOLDER = "bot-memory"
OVERVIEW_FOLDERS = frozenset({"30-knowledge", "40-sources", MAPS_FOLDER})
OVERVIEW_PREFIX_BYTES = 4096
MAX_OVERVIEW_NOTES = 2_000
MAX_OVERVIEW_BYTES = 8 * 1024 * 1024
OVERVIEW_DEADLINE_SECONDS = 3.0

EXCLUDED_DIRS = frozenset({".git", ".claude", ".codex", ".viki"})
PRIVATE_NAMES = frozenset(
    {"credentials", "credentials.json", ".credentials.json", "secrets.json", ".env", ".env.local"}
)
NOTE_SUFFIXES = frozenset({".md", ".markdown"})
# Names the reader shows but no save may create: agent instruction files the providers load, and
# the writer's own stage and backup entries.
WRITE_ONLY_NAMES = frozenset(
    {"agents.md", "agents.override.md", "claude.md", "claude.local.md", "gemini.md"}
)
STAGE_PREFIX = ".viki-stage-"
BACKUP_PREFIX = ".viki-backup-"
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Every negated class excludes the delimiters, so a failed attempt stops at the next `[` (or `(`
# in a target) instead of backtracking across the note: link extraction stays linear in its length.
WIKI_NAME = r"[^\[\]|#\n]"
WIKI_HEADING = r"[^\[\]|\n]"
LINK_TEXT = r"[^\[\]]"
LINK_TARGET = r"[^()\[\]\s]"
WIKI = re.compile(rf"\[\[({WIKI_NAME}+)(?:#{WIKI_HEADING}+)?(?:\|{LINK_TEXT}+)?\]\]")
MARKDOWN_LINK = re.compile(rf"\[{LINK_TEXT}+\]\(({LINK_TARGET}+)\)")

PartialReason = Literal[
    "deadline",
    "entry_limit",
    "item_limit",
    "byte_limit",
    "depth_limit",
    "unsafe_entry",
    "unreadable",
    "changed",
    "hit_limit",
]
LinkState = Literal["resolved", "missing", "ambiguous", "external", "unsafe"]
MetadataState = Literal[
    "ready", "absent", "unsupported", "truncated", "changed", "unreadable", "unknown"
]


class VaultError(RuntimeError):
    def __init__(self, code: str, **extra: Any) -> None:
        self.code = code
        self.extra = extra
        super().__init__(code)


@dataclass(frozen=True)
class NoteEntry:
    ref: str
    title: str
    bytes: int
    modified: float


@dataclass(frozen=True)
class Note:
    ref: str
    title: str
    text: str
    source: str
    byte_size: int
    sha256: str
    frontmatter: dict[str, str]
    links: tuple[str, ...]
    frontmatter_error: str | None = None
    display_metadata: DisplayMetadata | None = None


@dataclass(frozen=True)
class MetadataItem:
    ref: str
    state: MetadataState
    reason: str | None
    read_bytes: int
    display_metadata: DisplayMetadata | None


@dataclass(frozen=True)
class MetadataBatch:
    items: tuple[MetadataItem, ...]
    partial: bool
    partial_reasons: tuple[str, ...]
    read_bytes: int
    omitted: int


@dataclass(frozen=True)
class OverviewNote:
    """One overview row: a review note's display and review fields, read from its prefix."""

    ref: str
    state: StructureState
    title: str | None
    type: str | None
    status: str | None
    confidence: str | None
    tags: list[str] | None
    created: str | None
    modified: str | None
    summary: str | None
    links: int | None  # distinct notes a map's body links to; None for any other note
    complete: bool  # the prefix is the whole file


@dataclass(frozen=True)
class Overview:
    notes: tuple[OverviewNote, ...]
    partial: bool
    partial_reasons: tuple[PartialReason, ...]
    read_bytes: int
    omitted: int


@dataclass(frozen=True)
class Excerpt:
    """One line span of a note: the wire `part`, its text and rendered block, and the note's
    revision (its SHA-256, not the excerpt's) and modification time."""

    part: dict[str, object]
    text: str
    block: str
    block_bytes: int
    revision: str
    modified_at: str


@dataclass(frozen=True)
class LinkResult:
    source: str
    target: str
    state: LinkState
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchHit:
    ref: str
    line: int
    start: int
    end: int
    excerpt_start: int
    excerpt: str
    exact: str
    links: tuple[LinkResult, ...]


@dataclass(frozen=True)
class ScanResult:
    entries: tuple[NoteEntry, ...]
    partial: bool
    partial_reasons: tuple[PartialReason, ...]
    scanned_bytes: int
    visited_items: int
    omitted: int


@dataclass(frozen=True)
class SearchResult(Sequence[SearchHit]):
    hits: tuple[SearchHit, ...]
    partial: bool
    partial_reasons: tuple[PartialReason, ...]
    scanned_bytes: int
    omitted: int

    @overload
    def __getitem__(self, index: int) -> SearchHit: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[SearchHit]: ...

    def __getitem__(self, index: int | slice) -> SearchHit | Sequence[SearchHit]:
        return self.hits[index]

    def __len__(self) -> int:
        return len(self.hits)


@dataclass(frozen=True)
class NoteInspection:
    note: Note
    links: tuple[LinkResult, ...]
    partial: bool
    partial_reasons: tuple[PartialReason, ...]


@dataclass(frozen=True)
class Backlink:
    source: str
    target: str
    line: int


@dataclass(frozen=True)
class BacklinkResult(Sequence[Backlink]):
    backlinks: tuple[Backlink, ...]
    partial: bool
    partial_reasons: tuple[PartialReason, ...]
    scanned_bytes: int
    omitted: int

    @overload
    def __getitem__(self, index: int) -> Backlink: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Backlink]: ...

    def __getitem__(self, index: int | slice) -> Backlink | Sequence[Backlink]:
        return self.backlinks[index]

    def __len__(self) -> int:
        return len(self.backlinks)


@dataclass
class _PrefixBudget:
    deadline: float
    clock: Callable[[], float] = time.monotonic
    read_bytes: int = 0
    # Read when a budget is made, not at import, so the module's current cap applies.
    limit: int = field(default_factory=lambda: MAX_METADATA_BATCH_BYTES)

    def check(self) -> None:
        if self.clock() >= self.deadline:
            raise VaultError("deadline")
        if self.read_bytes >= self.limit:
            raise VaultError("byte_limit")

    def amount(self, wanted: int) -> int:
        self.check()
        return min(wanted, self.limit - self.read_bytes)

    def charge(self, actual: int) -> None:
        self.read_bytes += actual


@dataclass
class _Budget:
    expires_at: float
    entries: list[NoteEntry]
    reasons: set[PartialReason]
    visited_items: int = 0
    omitted: int = 0
    stopped: bool = False

    def reason(self, reason: PartialReason, *, omitted: int = 0) -> None:
        self.reasons.add(reason)
        self.omitted += omitted

    def check(self, omitted: int = 1) -> bool:
        if time.monotonic() >= self.expires_at:
            self.reason("deadline", omitted=omitted)
            self.stopped = True
        elif self.visited_items >= MAX_VISITED_ITEMS:
            self.reason("item_limit", omitted=omitted)
            self.stopped = True
        elif len(self.entries) >= MAX_ENTRIES:
            self.reason("entry_limit", omitted=omitted)
            self.stopped = True
        return not self.stopped


def _reason_tuple(reasons: set[PartialReason]) -> tuple[PartialReason, ...]:
    order: tuple[PartialReason, ...] = (
        "deadline",
        "entry_limit",
        "item_limit",
        "byte_limit",
        "depth_limit",
        "unsafe_entry",
        "unreadable",
        "changed",
        "hit_limit",
    )
    return tuple(reason for reason in order if reason in reasons)


def _unsafe_name(name: str) -> bool:
    """A raw entry name the client could never accept: a control code point (below 32) or a
    lone (unpaired) surrogate (U+D800-U+DFFF). Checked before anything else reads the name."""
    return any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF for character in name)


def excluded_part(part: str, *, write: bool) -> bool:
    """Whether one ref component is off limits; callers test every component, at any depth."""
    lowered = part.casefold()
    if lowered in EXCLUDED_DIRS or lowered in PRIVATE_NAMES or lowered.startswith(".env."):
        return True
    return write and (
        lowered in WRITE_ONLY_NAMES or lowered.startswith((STAGE_PREFIX, BACKUP_PREFIX))
    )


def is_bot_memory_ref(ref: str) -> bool:
    return ref.partition("/")[0].casefold() == BOT_MEMORY_FOLDER


def _counts_as_note(name: str) -> bool:
    """Whether a raw entry name counts as an omitted note when a stopped scan never visits it:
    the same note-suffix, non-excluded test the scan applies to an entry it does visit."""
    return PurePosixPath(name).suffix.casefold() in NOTE_SUFFIXES and not excluded_part(
        name, write=False
    )


def safe_ref(ref: str) -> tuple[str, ...]:
    if (
        not isinstance(ref, str)
        or not 1 <= len(ref) <= 512
        or "\x00" in ref
        or "\\" in ref
        or ":" in ref
    ):
        raise VaultError("invalid_ref")
    # Split by hand: PurePosixPath would silently drop the empty part of `a//b.md` and the `.`
    # of `a/./b.md`, and a leading `/` leaves an empty first part here.
    parts = tuple(ref.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise VaultError("invalid_ref")
    if any(excluded_part(part, write=False) for part in parts):
        raise VaultError("private_path")
    return parts


def _check_excerpt_range(start: int, end: int) -> None:
    if (
        type(start) is not int
        or type(end) is not int
        or not 1 <= start <= end <= MAX_LINE
        or end - start >= EXCERPT_MAX_LINES
    ):
        raise VaultError("invalid_range")


def _stamp(modified: float) -> str:
    """A note's modification time as an ISO-8601 UTC instant, to the millisecond.

    Arithmetic on the Unix epoch, not `datetime.fromtimestamp`, which on Windows raises an untyped
    `OSError` for a time below about -43200 s or at/after year 9999 — a tar restore's pre-1970
    mtime, or the 1601 a filesystem with no last-write time reports. A value `datetime` itself
    cannot hold is a `read_failed`, the code `_read_stamped` already gives an unreadable note.
    """
    try:
        at = UNIX_EPOCH + timedelta(seconds=modified)
    except OverflowError as exc:
        raise VaultError("read_failed") from exc
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _physical_id(info: os.stat_result) -> str:
    return f"{info.st_dev}:{info.st_ino}"


def _link_targets(text: str) -> tuple[str, ...]:
    targets: list[str] = []
    for pattern in (WIKI, MARKDOWN_LINK):
        for match in pattern.finditer(text):
            target = match.group(1).split("#", 1)[0].strip().strip("<>")
            if target and target not in targets:
                targets.append(target)
                if len(targets) >= MAX_LINKS:
                    return tuple(targets)
    return tuple(targets)


def _frontmatter(text: str) -> tuple[dict[str, str], str | None]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, None
    parsed: dict[str, str] = {}
    for line in lines[1:257]:
        if line.strip() == "---":
            return parsed, None
        if ":" not in line:
            return parsed, "invalid_line"
        key, value = line.split(":", 1)
        name, content = key.strip(), value.strip()
        if not 1 <= len(name) <= 64 or len(content) > 512:
            return parsed, "invalid_line"
        if name in parsed:
            return parsed, "duplicate_key"
        parsed[name] = content
    return parsed, "too_many_lines" if len(lines) > 257 else "unterminated"


def _link_key(text: str, platform: str) -> str:
    """How a link names a note. macOS apps and HFS+ copies store some names decomposed (NFD)
    while an IME types them composed (NFC), so there both sides are composed first."""
    if platform == "darwin":
        text = unicodedata.normalize("NFC", text)
    return text.casefold()


class _Index:
    def __init__(self, entries: tuple[NoteEntry, ...], platform: str) -> None:
        self.platform = platform
        self.exact: dict[str, list[str]] = {}
        self.stems: dict[str, list[str]] = {}
        for entry in entries:
            self.exact.setdefault(self.key(entry.ref), []).append(entry.ref)
            self.stems.setdefault(self.key(PurePosixPath(entry.ref).stem), []).append(entry.ref)

    def key(self, text: str) -> str:
        return _link_key(text, self.platform)

    def resolve(self, source: str, target: str) -> LinkResult:
        raw = target.strip()
        try:
            split = urlsplit(raw)
        except ValueError:
            return LinkResult(source, target, "unsafe")
        if split.scheme or split.netloc or raw.startswith(("/", "\\", "#")):
            state: LinkState = "external" if split.scheme or split.netloc else "unsafe"
            return LinkResult(source, target, state)
        try:
            decoded = unquote_to_bytes(split.path).decode("utf-8")
        except UnicodeError:
            return LinkResult(source, target, "unsafe")
        if "\\" in decoded or ":" in decoded or "\x00" in decoded:
            return LinkResult(source, target, "unsafe")
        relative = _join_relative(list(PurePosixPath(source).parent.parts), decoded)
        if relative is None or any(excluded_part(part, write=False) for part in relative):
            return LinkResult(source, target, "unsafe")
        candidates: list[str] = []
        requested = PurePosixPath(*relative)
        if requested.suffix.casefold() in NOTE_SUFFIXES:
            candidates.extend(self.exact.get(self.key(requested.as_posix()), ()))
        else:
            # Any suffix that isn't a note suffix is part of the name (a dotted title, not a real
            # extension), so append rather than `with_suffix`, which would replace it.
            for suffix in sorted(NOTE_SUFFIXES):
                candidate = self.key((requested.parent / (requested.name + suffix)).as_posix())
                candidates.extend(self.exact.get(candidate, ()))
            # A target that already sits at the vault root (however it got there: a bare name, or
            # `.`/`..` navigation that collapsed down to one part) also tries a name-only lookup.
            if not candidates and (
                len(relative) == 1 or ("/" not in decoded and decoded not in {".", ".."})
            ):
                candidates.extend(self.stems.get(self.key(requested.name), ()))
        unique = tuple(sorted(dict.fromkeys(candidates), key=str.casefold))
        state = "resolved" if len(unique) == 1 else "missing" if not unique else "ambiguous"
        return LinkResult(source, target, state, unique)


def _join_relative(base: list[str], target: str) -> tuple[str, ...] | None:
    parts = base
    for part in target.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return tuple(parts) if parts else None


def _map_links(index: _Index, ref: str, prefix: bytes, complete: bool) -> int:
    """How many distinct notes the links in a map's body (after any frontmatter) resolve to,
    the map itself excluded."""
    lines = codecs.getincrementaldecoder("utf-8")().decode(prefix, final=complete).split("\n")
    if lines[0].removeprefix("\ufeff").removesuffix("\r") == "---":
        close = next(
            (at for at, line in enumerate(lines) if at and line.removesuffix("\r") == "---"),
            len(lines),
        )
        lines = lines[close + 1 :]
    targets = {
        link.candidates[0]
        for link in (index.resolve(ref, target) for target in _link_targets("\n".join(lines)))
        if link.state == "resolved" and link.candidates[0] != ref
    }
    return len(targets)


class VaultService:
    def __init__(self, workspaces: WorkspaceService, *, platform: str = sys.platform) -> None:
        self._workspaces = workspaces
        self._platform = platform

    def _root(self, workspace_id: str) -> CanonicalRoot:
        try:
            workspace = self._workspaces.get(workspace_id)
        except WorkspaceError as exc:
            raise VaultError("unknown_workspace") from exc
        root = self._workspaces.current_root(workspace)
        if root is None:
            raise VaultError("root_unavailable")
        return root

    def scan(self, workspace_id: str, *, deadline: float = DEADLINE_SECONDS) -> ScanResult:
        if not isinstance(deadline, int | float) or not 0 < deadline <= 10:
            raise VaultError("invalid_deadline")
        return self._scan_root(self._root(workspace_id), time.monotonic() + deadline)

    def _scan_root(self, root: CanonicalRoot, expires_at: float) -> ScanResult:
        budget = _Budget(expires_at, [], set())
        root_path = Path(root.display)
        try:
            if sys.platform == "win32":
                with _open_registered_root(root):
                    self._scan_directory(root_path, (), 0, budget)
            else:
                with _open_root_fd(root) as root_fd:
                    _scan_fd(root_fd, (), 0, budget)
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("root_unavailable") from exc
        entries = tuple(sorted(budget.entries, key=lambda item: item.ref.casefold()))
        reasons = _reason_tuple(budget.reasons)
        return ScanResult(
            entries,
            bool(reasons),
            reasons,
            0,  # the listing reads no note content, so nothing is charged against a byte budget
            budget.visited_items,
            budget.omitted,
        )

    def _scan_directory(
        self,
        folder: Path,
        relative: tuple[str, ...],
        depth: int,
        budget: _Budget,
    ) -> None:
        if not budget.check():
            return
        if depth >= MAX_DEPTH:
            budget.reason("depth_limit", omitted=1)
            return
        try:
            iterator = os.scandir(folder)
        except OSError:
            budget.reason("unreadable", omitted=1)
            return
        with iterator:
            for item in iterator:
                if not budget.check(omitted=int(_counts_as_note(item.name))):
                    # The listing itself reads no content; count what this directory still holds
                    # unvisited, bounded by the item budget so a huge folder cannot add unbounded
                    # work after the scan has already stopped. This is a lower bound, not an
                    # exact count: entries past MAX_VISITED_ITEMS, and every entry a still-open
                    # subfolder would have held, are never counted.
                    budget.omitted += sum(
                        1
                        for entry in itertools.islice(iterator, MAX_VISITED_ITEMS)
                        if _counts_as_note(entry.name)
                    )
                    return
                budget.visited_items += 1
                if _unsafe_name(item.name):
                    budget.reason("unsafe_entry", omitted=1)
                    continue
                if excluded_part(item.name, write=False):
                    continue
                child_parts = (*relative, item.name)
                child_path = folder / item.name
                try:
                    # Windows DirEntry.stat may report zero file IDs/link counts.  The parent
                    # directory is held against rename, so lstat the current child path instead.
                    info = child_path.lstat()
                except OSError:
                    budget.reason("changed", omitted=1)
                    continue
                if _is_reparse(info):
                    budget.reason("unsafe_entry", omitted=1)
                    continue
                if stat.S_ISDIR(info.st_mode):
                    try:
                        with _open_directory(child_path):
                            current = child_path.lstat()
                            if (
                                _is_reparse(current)
                                or not stat.S_ISDIR(current.st_mode)
                                or _physical_id(current) != _physical_id(info)
                            ):
                                budget.reason("changed", omitted=1)
                                continue
                            self._scan_directory(child_path, child_parts, depth + 1, budget)
                    except VaultError:
                        budget.reason("unsafe_entry", omitted=1)
                    except OSError:
                        budget.reason("changed", omitted=1)
                    if budget.stopped:
                        return
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    budget.reason("unsafe_entry", omitted=1)
                    continue
                if PurePosixPath(item.name).suffix.casefold() not in NOTE_SUFFIXES:
                    continue
                ref = PurePosixPath(*child_parts).as_posix()
                try:
                    safe_ref(ref)
                except VaultError:
                    budget.reason("unsafe_entry", omitted=1)
                    continue
                budget.entries.append(
                    NoteEntry(
                        ref,
                        PurePosixPath(item.name).stem,
                        info.st_size,
                        info.st_mtime,
                    )
                )

    def folder_stamp(
        self, workspace_id: str, folder: str
    ) -> tuple[tuple[str, int, int, int, int, int, int], ...]:
        """The notes directly in one top-level folder as they stand on disk, for a watch to
        compare pass by pass: per name `_counts_as_note` accepts, sorted, its inode, mode, link
        count, size, modification time in ns and file attributes (0 off Windows).

        The folder is opened through the vault's own root chain, as a note read opens its parent
        folders, so a missing or linked one raises `unsafe_path`; each child is an lstat, so no
        note is read and no link followed. At most `MAX_VISITED_ITEMS` entries are examined, and a
        child whose lstat fails is left out. Any other `OSError` raises `unreadable`."""
        parts = safe_ref(folder)
        if len(parts) != 1:
            raise VaultError("invalid_ref")
        root = self._root(workspace_id)
        rows: list[tuple[str, int, int, int, int, int, int]] = []
        try:
            with ExitStack() as stack:
                if sys.platform == "win32":
                    path = stack.enter_context(_open_ancestor_chain(root, parts))

                    def lstat(name: str) -> os.stat_result:
                        return (path / name).lstat()

                    listing = stack.enter_context(os.scandir(path))
                else:
                    fd = _open_dir_at(stack.enter_context(_open_root_fd(root)), folder)
                    stack.callback(os.close, fd)

                    def lstat(name: str) -> os.stat_result:
                        return os.stat(name, dir_fd=fd, follow_symlinks=False)

                    listing = stack.enter_context(os.scandir(fd))
                for item in itertools.islice(listing, MAX_VISITED_ITEMS):
                    if not _counts_as_note(item.name):
                        continue
                    try:
                        info = lstat(item.name)
                    except OSError:
                        continue  # gone since it was listed
                    rows.append(
                        (
                            item.name,
                            info.st_ino,
                            info.st_mode,
                            info.st_nlink,
                            info.st_size,
                            info.st_mtime_ns,
                            getattr(info, "st_file_attributes", 0),
                        )
                    )
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("unreadable") from exc
        return tuple(sorted(rows))

    def read(self, workspace_id: str, ref: str) -> Note:
        return self._read_root(self._root(workspace_id), ref)

    def _read_root(self, root: CanonicalRoot, ref: str) -> Note:
        return self._read_stamped(root, ref)[0]

    def metadata(self, workspace_id: str, refs: list[str]) -> MetadataBatch:
        if not isinstance(refs, list) or not 1 <= len(refs) <= 32:
            raise VaultError("invalid_metadata_request")
        refs = list(refs)
        seen, private = set(), set()
        for ref in refs:
            if (
                not isinstance(ref, str)
                or ref in seen
                or any(unicodedata.category(char) in {"Cc", "Cs"} for char in ref)
                or PurePosixPath(ref).suffix.casefold() not in NOTE_SUFFIXES
            ):
                raise VaultError("invalid_metadata_request")
            try:
                safe_ref(ref)
            except VaultError as exc:
                if exc.code != "private_path":
                    raise VaultError("invalid_metadata_request") from None
                private.add(ref)
            seen.add(ref)
        clock = time.monotonic
        budget = _PrefixBudget(clock() + DEADLINE_SECONDS, clock=clock)
        root = self._root(workspace_id)
        items: list[MetadataItem] = []
        reasons: set[str] = set()
        stopped: str | None = None
        for ref in refs:
            start = budget.read_bytes
            state: MetadataState = "unknown"
            reason, display = stopped, None
            if stopped is None:
                try:
                    budget.check()
                    if ref in private:
                        raise VaultError("private_path")
                    prefix, complete = self._read_prefix(root, ref, budget)
                    display = extract_display(prefix, complete=complete)
                    state = display.state
                    reason = {
                        "unsupported": "unsupported_metadata",
                        "truncated": "prefix_limit",
                    }.get(state)
                except UnicodeDecodeError:
                    state, reason = "unreadable", "invalid_encoding"
                except VaultError as exc:
                    if exc.code in {"unknown_workspace", "root_changed", "root_unavailable"}:
                        raise
                    if exc.code in {"deadline", "byte_limit"}:
                        stopped = reason = exc.code
                    elif exc.code == "changed":
                        state, reason = "changed", "changed"
                    else:
                        state = "unreadable"
                        reason = (
                            exc.code
                            if exc.code
                            in {
                                "missing_note",
                                "private_path",
                                "unsafe_path",
                                "note_too_large",
                                "target_busy",
                                "read_failed",
                                "invalid_encoding",
                            }
                            else "read_failed"
                        )
            if state == "unknown" and reason is not None:
                reasons.add(reason)
            elif state not in {"ready", "absent"}:
                reasons.add(state)
            items.append(MetadataItem(ref, state, reason, budget.read_bytes - start, display))
        order = (
            "deadline",
            "byte_limit",
            "response_limit",
            "unsupported",
            "truncated",
            "changed",
            "unreadable",
        )
        return MetadataBatch(
            tuple(items),
            bool(reasons),
            tuple(reason for reason in order if reason in reasons),
            budget.read_bytes,
            sum(item.state == "unknown" for item in items),
        )

    def overview(self, workspace_id: str) -> Overview:
        """A row per readable review-folder note from its prefix, read maps first and then
        newest file first, until `MAX_OVERVIEW_NOTES`, `MAX_OVERVIEW_BYTES` or the deadline
        stops the reads."""
        clock = time.monotonic
        start = clock()
        root = self._root(workspace_id)
        scan = self._scan_root(root, start + DEADLINE_SECONDS)
        index = _Index(scan.entries, self._platform)
        entries = sorted(
            (entry for entry in scan.entries if entry.ref.split("/", 1)[0] in OVERVIEW_FOLDERS),
            key=lambda entry: (not entry.ref.startswith(f"{MAPS_FOLDER}/"), -entry.modified),
        )
        reasons: set[PartialReason] = set(scan.partial_reasons)
        # The client refuses the whole response if any ref holds a Cc/Cs code point (the same
        # check `metadata()` applies), so such an entry never becomes a row.
        safe_entries = [
            entry
            for entry in entries
            if not any(unicodedata.category(char) in {"Cc", "Cs"} for char in entry.ref)
        ]
        unsafe = len(entries) - len(safe_entries)
        if unsafe:
            reasons.add("unsafe_entry")
        entries = safe_entries
        if len(entries) > MAX_OVERVIEW_NOTES:
            reasons.add("item_limit")
        budget = _PrefixBudget(
            start + OVERVIEW_DEADLINE_SECONDS, clock=clock, limit=MAX_OVERVIEW_BYTES
        )
        notes: list[OverviewNote] = []
        for entry in entries[:MAX_OVERVIEW_NOTES]:
            is_map = entry.ref.startswith(f"{MAPS_FOLDER}/")
            try:
                prefix, complete = self._read_prefix(
                    root,
                    entry.ref,
                    budget,
                    limit=MAX_DISPLAY_PREFIX_BYTES if is_map else OVERVIEW_PREFIX_BYTES,
                )
                display = extract_display(prefix, complete=complete)
                review = extract_review(prefix, complete=complete)
                links = _map_links(index, entry.ref, prefix, complete) if is_map else None
            except UnicodeDecodeError:
                reasons.add("unreadable")
                continue
            except VaultError as exc:
                if exc.code in {"unknown_workspace", "root_changed", "root_unavailable"}:
                    raise
                if exc.code in ("deadline", "byte_limit"):
                    reasons.add(exc.code)  # this note and every later one stay unlisted
                    break
                changed = exc.code in {"changed", "missing_note", "target_busy"}
                reasons.add("changed" if changed else "unreadable")
                continue
            notes.append(
                OverviewNote(
                    ref=entry.ref,
                    state=display.state,
                    title=display.display_title,
                    type=display.type,
                    status=display.status,
                    confidence=review.confidence,
                    tags=display.tags,
                    created=review.created,
                    modified=review.modified,
                    summary=review.summary,
                    links=links,
                    complete=complete,
                )
            )
        # The later of created and modified, newest first; undated last; ties by ref casefold.
        notes.sort(key=lambda note: note.ref.casefold())
        notes.sort(key=lambda note: max(note.created or "", note.modified or ""), reverse=True)
        ordered = _reason_tuple(reasons)
        return Overview(
            tuple(notes),
            bool(ordered),
            ordered,
            budget.read_bytes,
            scan.omitted + unsafe + len(entries) - len(notes),
        )

    def _read_prefix(
        self,
        root: CanonicalRoot,
        ref: str,
        budget: _PrefixBudget,
        *,
        limit: int = MAX_DISPLAY_PREFIX_BYTES,
    ) -> tuple[bytes, bool]:
        parts = safe_ref(ref)
        if any(_unsafe_name(part) for part in parts):
            raise VaultError("invalid_ref")
        if PurePosixPath(parts[-1]).suffix.casefold() not in NOTE_SUFFIXES:
            raise VaultError("unsupported_note")
        if sys.platform != "win32":
            return _read_prefix_at(root, parts, budget, limit)
        observations: list[tuple[int, tuple[int, ...]]] = []

        def observe(handle: int) -> None:
            observations.append((handle, _prefix_stamp(handle, directory=True)))

        try:
            with _open_ancestor_chain(
                root, parts[:-1], checkpoint=budget.check, observe=observe
            ) as parent:
                budget.check()
                path = parent / parts[-1]
                with _open_note(path) as handle:
                    before = _prefix_stamp(handle)
                    size = (before[-4] << 32) | before[-3]
                    data, lines = bytearray(), 0
                    while len(data) < min(size, limit) and lines < 256:
                        amount = budget.amount(min(4096, size - len(data), limit - len(data)))
                        buffer, transferred = ctypes.create_string_buffer(amount), wintypes.DWORD()
                        ok = _ReadFile(handle, buffer, amount, ctypes.byref(transferred), None)
                        budget.charge(transferred.value)
                        if not ok:
                            raise ctypes.WinError(ctypes.get_last_error(), "ReadFile")
                        if transferred.value == 0 or transferred.value > amount:
                            raise VaultError("changed")
                        chunk = buffer.raw[: transferred.value]
                        data.extend(chunk)
                        lines += chunk.count(b"\n")
                    _verify_root(root, Path(root.display))
                    if before != _prefix_stamp(handle) or not same_final_path(handle, str(path)):
                        raise VaultError("changed")
                    if any(
                        stamp != _prefix_stamp(held, directory=True) for held, stamp in observations
                    ):
                        raise VaultError("changed")
                    return bytes(data), len(data) == size
        except OSError as exc:
            raise VaultError("read_failed") from exc

    def _read_stamped(self, root: CanonicalRoot, ref: str) -> tuple[Note, float]:
        """One open-read-close of a note and the modification time of the file it read."""
        parts = safe_ref(ref)
        if PurePosixPath(parts[-1]).suffix.casefold() not in NOTE_SUFFIXES:
            raise VaultError("unsupported_note")
        try:
            if sys.platform == "win32":
                with _open_ancestor_chain(root, parts[:-1]) as parent:
                    path = parent / parts[-1]
                    with _open_note(path) as handle:
                        data, modified = _read_note_handle(handle)
            else:
                data, modified = _read_note_at(root, parts)
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("read_failed") from exc
        try:
            text = data.decode("utf-8-sig")
            source = data.decode("utf-8")
            display_metadata = extract_display(
                data[:MAX_DISPLAY_PREFIX_BYTES], complete=len(data) <= MAX_DISPLAY_PREFIX_BYTES
            )
        except UnicodeDecodeError as exc:
            raise VaultError("invalid_encoding", read_bytes=len(data)) from exc
        frontmatter, frontmatter_error = _frontmatter(text)
        normalized_ref = PurePosixPath(*parts).as_posix()
        note = Note(
            normalized_ref,
            PurePosixPath(parts[-1]).stem,
            text,
            source,
            len(data),
            hashlib.sha256(data).hexdigest(),
            frontmatter,
            _link_targets(text),
            frontmatter_error,
            display_metadata,
        )
        return note, modified

    def excerpt(self, workspace_id: str, ref: str, start: int, end: int) -> Excerpt:
        """Lines `start..end` (1-based, inclusive) of a note of the active vault."""
        _check_excerpt_range(start, end)
        return self.excerpt_at(self.active_root(workspace_id), workspace_id, ref, start, end)

    def active_root(self, workspace_id: str) -> CanonicalRoot:
        """The resolved root of the active vault: what `excerpt_at` takes, so a caller reading
        several excerpts resolves it once."""
        if self._workspaces.resolve_active().workspace.workspace_id != workspace_id:
            raise VaultError("not_active_vault")
        return self._root(workspace_id)

    def excerpt_at(
        self, root: CanonicalRoot, workspace_id: str, ref: str, start: int, end: int
    ) -> Excerpt:
        """`excerpt` over a root `active_root` already resolved."""
        _check_excerpt_range(start, end)
        note, modified = self._read_stamped(root, ref)
        # `note.text` is decoded as utf-8-sig, so its one leading BOM is already gone.
        lines = note.text.split("\n")
        if lines[-1] == "":  # the last line's terminator, not a line of its own
            lines.pop()
        if end > len(lines):
            raise VaultError("invalid_range")
        text = "\n".join(line.removesuffix("\r") for line in lines[start - 1 : end])
        if len(text.encode("utf-8")) > EXCERPT_MAX_BYTES:
            raise VaultError("excerpt_too_large")
        block = render_excerpt_block(note.ref, start, end, text)
        part: dict[str, object] = {
            "kind": "vault_excerpt",
            "vault_id": workspace_id,
            "ref": note.ref,
            "start_line": start,
            "end_line": end,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        return Excerpt(
            part,
            text,
            block,
            len(block.encode("utf-8")),
            note.sha256,
            _stamp(modified),
        )

    def inspect(self, workspace_id: str, ref: str) -> NoteInspection:
        root = self._root(workspace_id)
        expires = time.monotonic() + DEADLINE_SECONDS
        scan = self._scan_root(root, expires)
        note = self._read_root(root, ref)
        return NoteInspection(
            note,
            self._resolve_from_index(note, _Index(scan.entries, self._platform)),
            scan.partial,
            scan.partial_reasons,
        )

    def history(self, workspace_id: str, ref: str, *, limit: int = MAX_COMMITS) -> NoteHistory:
        """The Git history of one ref. It reads no note, so any validated ref is asked."""
        root = self._root(workspace_id)
        safe_ref(ref)  # also refuses a private component at any depth
        return note_history(Path(root.display), ref, limit=limit)

    def search(
        self, workspace_id: str, query: str, *, deadline: float = DEADLINE_SECONDS
    ) -> SearchResult:
        if not isinstance(query, str) or "\x00" in query or len(query) > MAX_QUERY_CHARS:
            raise VaultError("invalid_query")
        query = unicodedata.normalize("NFKC", query).strip()
        # The floor guards against 1-letter Latin noise; one CJK character is already selective.
        if len(query) < (2 if query.isascii() else 1):
            raise VaultError("invalid_query")
        if not isinstance(deadline, int | float) or not 0 < deadline <= 10:
            raise VaultError("invalid_deadline")
        root = self._root(workspace_id)
        expires = time.monotonic() + deadline
        scan = self._scan_root(root, expires)
        index = _Index(scan.entries, self._platform)
        reasons = set(scan.partial_reasons)
        omitted = scan.omitted
        consumed = 0
        hits: list[SearchHit] = []
        budget = Budget(expires, lambda: False, time.monotonic)
        total = len(scan.entries)
        for position, entry in enumerate(scan.entries):
            if time.monotonic() >= expires:
                reasons.add("deadline")
                omitted += total - position
                break
            try:
                note = self._read_root(root, entry.ref)
            except VaultError as exc:
                reason: PartialReason = (
                    "changed" if exc.code in {"missing_note", "target_busy"} else "unreadable"
                )
                reasons.add(reason)
                read_bytes = exc.extra.get("read_bytes", 0)
                if read_bytes > MAX_SCAN_BYTES - consumed:
                    reasons.add("byte_limit")
                    omitted += total - position
                    break
                consumed += read_bytes
                omitted += 1
                continue
            if note.byte_size > MAX_SCAN_BYTES - consumed:
                reasons.add("byte_limit")
                omitted += total - position
                break
            consumed += note.byte_size
            try:
                span = match_span(note.text, query, budget)
            except TimeoutError:
                reasons.add("deadline")
                omitted += total - position
                break
            if span is None:
                continue
            start, end = span
            excerpt_start = max(0, start - MAX_EXCERPT_CHARS // 2)
            hits.append(
                SearchHit(
                    entry.ref,
                    note.text.count("\n", 0, start) + 1,
                    start,
                    end,
                    excerpt_start,
                    note.text[excerpt_start : excerpt_start + MAX_EXCERPT_CHARS],
                    note.text[start:end],
                    self._resolve_from_index(note, index),
                )
            )
            if len(hits) >= MAX_HITS:
                reasons.add("hit_limit")
                omitted += total - position - 1
                break
        ordered = _reason_tuple(reasons)
        return SearchResult(tuple(hits), bool(ordered), ordered, consumed, omitted)

    def resolve_links(self, workspace_id: str, note: Note) -> tuple[LinkResult, ...]:
        scan = self.scan(workspace_id)
        return self._resolve_from_index(note, _Index(scan.entries, self._platform))

    @staticmethod
    def _resolve_from_index(note: Note, index: _Index) -> tuple[LinkResult, ...]:
        return tuple(index.resolve(note.ref, target) for target in note.links)

    def backlinks(
        self, workspace_id: str, ref: str, *, deadline: float = DEADLINE_SECONDS
    ) -> BacklinkResult:
        if not isinstance(deadline, int | float) or not 0 < deadline <= 10:
            raise VaultError("invalid_deadline")
        target_ref = PurePosixPath(*safe_ref(ref)).as_posix()
        root = self._root(workspace_id)
        expires = time.monotonic() + deadline
        scan = self._scan_root(root, expires)
        index = _Index(scan.entries, self._platform)
        target_key = index.key(target_ref)
        if target_key not in index.exact:
            raise VaultError("missing_note")
        reasons = set(scan.partial_reasons)
        omitted = scan.omitted
        consumed = 0
        found: list[Backlink] = []
        total = len(scan.entries)
        for position, entry in enumerate(scan.entries):
            if time.monotonic() >= expires:
                reasons.add("deadline")
                omitted += total - position
                break
            try:
                note = self._read_root(root, entry.ref)
            except VaultError as exc:
                reasons.add("changed")
                read_bytes = exc.extra.get("read_bytes", 0)
                if read_bytes > MAX_SCAN_BYTES - consumed:
                    reasons.add("byte_limit")
                    omitted += total - position
                    break
                consumed += read_bytes
                omitted += 1
                continue
            if note.byte_size > MAX_SCAN_BYTES - consumed:
                reasons.add("byte_limit")
                omitted += total - position
                break
            consumed += note.byte_size
            for resolved in self._resolve_from_index(note, index):
                if resolved.state == "resolved" and any(
                    index.key(candidate) == target_key for candidate in resolved.candidates
                ):
                    found.append(
                        Backlink(entry.ref, resolved.target, _link_line(note.text, resolved.target))
                    )
                    break
            if len(found) >= MAX_HITS:
                reasons.add("hit_limit")
                omitted += total - position - 1
                break
        ordered = _reason_tuple(reasons)
        return BacklinkResult(tuple(found), bool(ordered), ordered, consumed, omitted)


def _link_line(text: str, target: str) -> int:
    at = text.find(target)
    return text.count("\n", 0, max(at, 0)) + 1


if sys.platform == "win32":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _fn(name: str, restype: Any, *argtypes: Any) -> Any:
        function = getattr(_kernel32, name)
        function.restype = restype
        function.argtypes = argtypes
        return function

    _CreateFileW = _fn(
        "CreateFileW",
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _CloseHandle = _fn("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    _ReadFile = _fn(
        "ReadFile",
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPDWORD,
        wintypes.LPVOID,
    )
    _GetFileInformationByHandle = _fn(
        "GetFileInformationByHandle", wintypes.BOOL, wintypes.HANDLE, wintypes.LPVOID
    )
    _GetFinalPathNameByHandleW = _fn(
        "GetFinalPathNameByHandleW",
        wintypes.DWORD,
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x1
    _FILE_SHARE_WRITE = 0x2
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_DIRECTORY = 0x10
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_PATH_NOT_FOUND = 3
    _ERROR_SHARING_VIOLATION = 32
    _FILE_NAME_NORMALIZED = 0
    _MAX_FINAL_PATH = 32_768  # the longest path Win32 returns, in UTF-16 units

    class _ByHandleInfo(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("created", wintypes.FILETIME),
            ("accessed", wintypes.FILETIME),
            ("written", wintypes.FILETIME),
            ("volume", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    @contextmanager
    def _handle(path: Path, *, directory: bool) -> Iterator[int]:
        flags = _FILE_FLAG_OPEN_REPARSE_POINT | (_FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
        share = _FILE_SHARE_READ | _FILE_SHARE_WRITE if directory else _FILE_SHARE_READ
        handle = _CreateFileW(str(path), _GENERIC_READ, share, None, _OPEN_EXISTING, flags, None)
        if handle == _INVALID_HANDLE_VALUE:
            error = ctypes.get_last_error()
            if error in {_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND}:
                raise VaultError("missing_note" if not directory else "unsafe_path")
            if error == _ERROR_SHARING_VIOLATION:
                raise VaultError("target_busy")
            raise ctypes.WinError(error, "CreateFileW")
        try:
            yield int(handle)
        finally:
            _CloseHandle(handle)

    def _handle_info(handle: int) -> _ByHandleInfo:
        info = _ByHandleInfo()
        if not _GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error(), "GetFileInformationByHandle")
        return info

    def same_final_path(handle: int, expected: str) -> bool:
        """Whether the opened handle's normalized final path is `expected`.

        An 8.3 short name (`CLAUDE~1`) or a trailing period or space (`.claude.`) opens an entry
        under another name than the one a name check saw; only the handle knows which entry it is.
        A failed query answers False, so the caller refuses.
        """
        buffer = ctypes.create_unicode_buffer(_MAX_FINAL_PATH)
        length = _GetFinalPathNameByHandleW(handle, buffer, _MAX_FINAL_PATH, _FILE_NAME_NORMALIZED)
        if not 0 < length < _MAX_FINAL_PATH:
            return False
        final = buffer.value.removeprefix("\\\\?\\")
        return os.path.normcase(final) == os.path.normcase(expected)

    @contextmanager
    def _open_directory(path: Path) -> Iterator[int]:
        with _handle(path, directory=True) as handle:
            info = _handle_info(handle)
            if (
                not info.attributes & _FILE_ATTRIBUTE_DIRECTORY
                or info.attributes & _FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise VaultError("unsafe_path")
            if not same_final_path(handle, str(path)):
                raise VaultError("private_path")
            yield handle

    @contextmanager
    def _open_note(path: Path) -> Iterator[int]:
        with _handle(path, directory=False) as handle:
            info = _handle_info(handle)
            if (
                info.attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT)
                or info.links != 1
            ):
                raise VaultError("unsafe_path")
            if not same_final_path(handle, str(path)):
                raise VaultError("private_path")
            size = (int(info.size_high) << 32) | int(info.size_low)
            if size > MAX_NOTE_BYTES:
                raise VaultError("note_too_large")
            yield handle

    def _prefix_stamp(handle: int, *, directory: bool = False) -> tuple[int, ...]:
        info = _handle_info(handle)
        identity = (
            int(info.volume),
            int(info.index_high),
            int(info.index_low),
            int(info.attributes),
            int(info.links),
            int(info.created.dwHighDateTime),
            int(info.created.dwLowDateTime),
        )
        if directory:
            return identity
        return identity + (
            int(info.size_high),
            int(info.size_low),
            int(info.written.dwHighDateTime),
            int(info.written.dwLowDateTime),
        )

    def _read_note_handle(handle: int) -> tuple[bytes, float]:
        info = _handle_info(handle)
        size = (int(info.size_high) << 32) | int(info.size_low)
        if size > MAX_NOTE_BYTES:
            raise VaultError("note_too_large")
        data = bytearray()
        while len(data) < size:
            amount = min(size - len(data), 64 * 1024)
            buffer = ctypes.create_string_buffer(amount)
            read = wintypes.DWORD()
            if not _ReadFile(handle, buffer, amount, ctypes.byref(read), None):
                raise ctypes.WinError(ctypes.get_last_error(), "ReadFile")
            if read.value == 0:
                raise VaultError("changed")
            data.extend(buffer.raw[: read.value])
        # FILETIME counts 100 ns ticks from 1601-01-01; the Unix epoch is 11_644_473_600 s later.
        ticks = (int(info.written.dwHighDateTime) << 32) | int(info.written.dwLowDateTime)
        return bytes(data), ticks / 10_000_000 - 11_644_473_600

else:
    # Every POSIX open is relative to an open directory descriptor and refuses a symlink, so a
    # rename or a link swapped in mid-read cannot redirect it. A FIFO or a device opens without
    # blocking and is refused by its fstat before any byte is read.
    _DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    _NOTE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    if sys.platform == "linux":
        # Opening a folder to list it needs read permission; walking through it needs only
        # search, as any path lookup does, so a vault under a traverse-only parent (a 0711 home)
        # stays readable. macOS has no O_PATH.
        _ANCESTOR_FLAGS = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    else:
        _ANCESTOR_FLAGS = _DIRECTORY_FLAGS

    @contextmanager
    def _open_directory(path: Path) -> Iterator[int]:
        # Named only by the Windows path walk (_scan_directory, _open_registered_root,
        # _open_ancestor_chain), which never runs here; it refuses rather than walk a path.
        raise VaultError("root_unavailable")
        yield  # a generator, so the refusal comes on entry as the Win32 open's does

    def same_final_path(handle: int, expected: str) -> bool:
        return False

    def _refuse_alias(fd: int) -> None:
        """macOS opens `.CLAUDE` (or another case or normalization spelling) as `.claude`, so
        the name on disk behind the descriptor is judged too."""
        if sys.platform == "darwin":
            if excluded_part(os.path.basename(osfs.final_path(fd)), write=False):
                raise VaultError("private_path")

    @contextmanager
    def _open_root_fd(
        root: CanonicalRoot,
        *,
        checkpoint: Callable[[], None] | None = None,
        observe: Callable[[int, int | None, str], None] | None = None,
    ) -> Iterator[int]:
        path = PurePosixPath(root.display)
        if not path.is_absolute() or root_key(root.display) != root.key:
            raise VaultError("root_changed")
        held: list[int] = []
        try:
            if checkpoint is not None:
                checkpoint()
            parent = os.open("/", _ANCESTOR_FLAGS)
            if observe is not None:
                held.append(parent)
                observe(parent, None, "/")
            try:
                for part in path.parts[1:-1]:
                    if checkpoint is not None:
                        checkpoint()
                    child = os.open(part, _ANCESTOR_FLAGS, dir_fd=parent)
                    if observe is None:
                        os.close(parent)
                    else:
                        held.append(child)
                        observe(child, parent, part)
                    parent = child
                # For the root `/` itself the last part is absolute, and dir_fd is ignored.
                if checkpoint is not None:
                    checkpoint()
                fd = os.open(path.parts[-1], _DIRECTORY_FLAGS, dir_fd=parent)
            finally:
                if observe is None:
                    os.close(parent)
            try:
                info = os.fstat(fd)
                if observe is not None:
                    observe(fd, parent, path.parts[-1])
            except BaseException:
                os.close(fd)
                raise
        except BaseException as exc:
            for ancestor in reversed(held):
                os.close(ancestor)
            if isinstance(exc, OSError):
                raise VaultError("root_unavailable") from exc
            raise
        try:
            if _physical_id(info) != root.physical_id:
                raise VaultError("root_changed")
            yield fd
        finally:
            try:
                os.close(fd)
            finally:
                for ancestor in reversed(held):
                    os.close(ancestor)

    def _open_dir_at(
        parent_fd: int, name: str, *, checkpoint: Callable[[], None] | None = None
    ) -> int:
        if checkpoint is not None:
            checkpoint()
        try:
            fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.ENOENT}:
                raise VaultError("unsafe_path") from exc
            raise
        try:
            _refuse_alias(fd)
        except BaseException:
            os.close(fd)
            raise
        return fd

    # APFS counts every directory entry, not just subdirectories, in st_nlink.
    _DIRECTORY_NLINK = sys.platform != "darwin"

    def _posix_prefix_stamp(info: os.stat_result, *, directory: bool = False) -> tuple[int, ...]:
        if directory:
            birth = getattr(
                info, "st_birthtime_ns", int(getattr(info, "st_birthtime", 0) * 1_000_000_000)
            )
            if _DIRECTORY_NLINK:
                return info.st_dev, info.st_ino, info.st_mode, info.st_nlink, int(birth)
            return info.st_dev, info.st_ino, info.st_mode, int(birth)
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_nlink,
            info.st_mode,
        )

    def _read_prefix_at(
        root: CanonicalRoot, parts: tuple[str, ...], budget: _PrefixBudget, limit: int
    ) -> tuple[bytes, bool]:
        observations: list[tuple[int, int | None, str, tuple[int, ...]]] = []

        def observe(fd: int, parent: int | None, name: str) -> None:
            observations.append(
                (fd, parent, name, _posix_prefix_stamp(os.fstat(fd), directory=True))
            )

        try:
            with (
                _open_root_fd(root, checkpoint=budget.check, observe=observe) as root_fd,
                ExitStack() as stack,
            ):
                parent = root_fd
                for part in parts[:-1]:
                    child = _open_dir_at(parent, part, checkpoint=budget.check)
                    stack.callback(os.close, child)
                    observe(child, parent, part)
                    parent = child
                budget.check()
                try:
                    fd = os.open(parts[-1], _NOTE_FLAGS, dir_fd=parent)
                except FileNotFoundError as exc:
                    raise VaultError("missing_note") from exc
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise VaultError("unsafe_path") from exc
                    raise
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise VaultError("unsafe_path")
                _refuse_alias(fd)
                if info.st_size > MAX_NOTE_BYTES:
                    raise VaultError("note_too_large")
                before = _posix_prefix_stamp(info)
                data, lines = bytearray(), 0
                while len(data) < min(info.st_size, limit) and lines < 256:
                    amount = budget.amount(min(4096, info.st_size - len(data), limit - len(data)))
                    chunk = os.read(fd, amount)
                    budget.charge(len(chunk))
                    if not chunk or len(chunk) > amount:
                        raise VaultError("changed")
                    data.extend(chunk)
                    lines += chunk.count(b"\n")
                _verify_root(root, Path(root.display))
                try:
                    if before != _posix_prefix_stamp(os.fstat(fd)) or before != _posix_prefix_stamp(
                        os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                    ):
                        raise VaultError("changed")
                    for held, binding_parent, name, stamp in observations:
                        if stamp != _posix_prefix_stamp(
                            os.fstat(held), directory=True
                        ) or stamp != (
                            _posix_prefix_stamp(
                                os.stat(name, dir_fd=binding_parent, follow_symlinks=False),
                                directory=True,
                            )
                        ):
                            raise VaultError("changed")
                except OSError as exc:
                    raise VaultError("changed") from exc
                return bytes(data), len(data) == info.st_size
        except OSError as exc:
            raise VaultError("read_failed") from exc

    def _read_file_at(parent_fd: int, name: str, limit: int) -> tuple[bytes, os.stat_result]:
        try:
            fd = os.open(name, _NOTE_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise VaultError("missing_note") from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise VaultError("unsafe_path") from exc
            raise
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise VaultError("unsafe_path")
            _refuse_alias(fd)
            if info.st_size > limit:
                raise VaultError("note_too_large")
            data = bytearray()
            while len(data) < info.st_size:
                chunk = os.read(fd, min(info.st_size - len(data), 64 * 1024))
                if not chunk:
                    raise VaultError("changed")
                data += chunk
            return bytes(data), info
        finally:
            os.close(fd)

    def _read_note_at(root: CanonicalRoot, parts: tuple[str, ...]) -> tuple[bytes, float]:
        with _open_root_fd(root) as root_fd, ExitStack() as stack:
            parent = root_fd
            for part in parts[:-1]:
                parent = _open_dir_at(parent, part)
                stack.callback(os.close, parent)
            data, info = _read_file_at(parent, parts[-1], MAX_NOTE_BYTES)
            return data, info.st_mtime

    def _scan_fd(folder: int, relative: tuple[str, ...], depth: int, budget: _Budget) -> None:
        """`VaultService._scan_directory` over descriptors: the same rules and budget, each
        entry judged by an lstat relative to the listed directory."""
        if not budget.check():
            return
        if depth >= MAX_DEPTH:
            budget.reason("depth_limit", omitted=1)
            return
        try:
            iterator = os.scandir(folder)
        except OSError:
            budget.reason("unreadable", omitted=1)
            return
        with iterator:
            for item in iterator:
                if not budget.check(omitted=int(_counts_as_note(item.name))):
                    budget.omitted += sum(
                        1
                        for entry in itertools.islice(iterator, MAX_VISITED_ITEMS)
                        if _counts_as_note(entry.name)
                    )
                    return
                budget.visited_items += 1
                if _unsafe_name(item.name):
                    budget.reason("unsafe_entry", omitted=1)
                    continue
                if excluded_part(item.name, write=False):
                    continue
                child_parts = (*relative, item.name)
                try:
                    info = os.stat(item.name, dir_fd=folder, follow_symlinks=False)
                except OSError:
                    budget.reason("changed", omitted=1)
                    continue
                if _is_reparse(info):
                    budget.reason("unsafe_entry", omitted=1)
                    continue
                if stat.S_ISDIR(info.st_mode):
                    try:
                        child = _open_dir_at(folder, item.name)
                        try:
                            if _physical_id(os.fstat(child)) == _physical_id(info):
                                _scan_fd(child, child_parts, depth + 1, budget)
                            else:
                                budget.reason("changed", omitted=1)
                        finally:
                            os.close(child)
                    except VaultError:
                        budget.reason("unsafe_entry", omitted=1)
                    except OSError:
                        budget.reason("changed", omitted=1)
                    if budget.stopped:
                        return
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    budget.reason("unsafe_entry", omitted=1)
                    continue
                if PurePosixPath(item.name).suffix.casefold() not in NOTE_SUFFIXES:
                    continue
                ref = PurePosixPath(*child_parts).as_posix()
                try:
                    safe_ref(ref)
                except VaultError:
                    budget.reason("unsafe_entry", omitted=1)
                    continue
                budget.entries.append(
                    NoteEntry(ref, PurePosixPath(item.name).stem, info.st_size, info.st_mtime)
                )


def _verify_root(root: CanonicalRoot, path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise VaultError("root_unavailable") from exc
    if (
        root_key(str(path.resolve(strict=True))) != root.key
        or _physical_id(info) != root.physical_id
        or not stat.S_ISDIR(info.st_mode)
        or _is_reparse(info)
    ):
        raise VaultError("root_changed")


@contextmanager
def _open_registered_root(
    root: CanonicalRoot,
    *,
    checkpoint: Callable[[], None] | None = None,
    observe: Callable[[int], None] | None = None,
) -> Iterator[Path]:
    root_path = Path(root.display)
    anchor = Path(root_path.anchor)
    if not root_path.is_absolute() or not root_path.anchor:
        raise VaultError("root_changed")
    with ExitStack() as stack:
        current = anchor
        if checkpoint is not None:
            checkpoint()
        handle = stack.enter_context(_open_directory(current))
        if observe is not None:
            observe(handle)
        for part in root_path.parts[1:]:
            current /= part
            if checkpoint is not None:
                checkpoint()
            handle = stack.enter_context(_open_directory(current))
            if observe is not None:
                observe(handle)
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
                raise VaultError("unsafe_path")
        _verify_root(root, root_path)
        yield root_path


@contextmanager
def _open_ancestor_chain(
    root: CanonicalRoot,
    parent_parts: tuple[str, ...],
    *,
    checkpoint: Callable[[], None] | None = None,
    observe: Callable[[int], None] | None = None,
) -> Iterator[Path]:
    with ExitStack() as stack:
        current = stack.enter_context(
            _open_registered_root(root, checkpoint=checkpoint, observe=observe)
        )
        for part in parent_parts:
            current /= part
            if checkpoint is not None:
                checkpoint()
            handle = stack.enter_context(_open_directory(current))
            if observe is not None:
                observe(handle)
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
                raise VaultError("unsafe_path")
        yield current
