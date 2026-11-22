"""Read-only reader for the vault's Claude Code transcripts: history pages, the sessions list and
search. No database and no persisted index: files are read on demand (the vault has ~75
transcripts, ~300 MB; a full parse takes under a second) and cached in memory only.

Transcripts are `~/.claude/projects/<project>/<session_id>.jsonl`, where `<project>` is the
vault's path with every character other than an ASCII letter or digit replaced by "-" (the
CLI's rule; Windows paths compare case-insensitively, so VS Code's "c--…" and this app's "C--…"
are one folder). On POSIX an absolute vault path is resolved first; on macOS it is then
NFC-normalized, matching the CLI's own build, while on Linux the raw realpath is kept, since the
Linux CLI names its transcripts folder that way. The reader never writes, never holds a file open,
and reads UTF-8 with errors="replace". Every public method blocks: call it in a thread.

Rules (tests/acceptance/test_catalog_accept.py pins them):

The conversation chain. Ported from claude-agent-sdk 0.2.152
(`_internal/sessions.py`, `_build_conversation_chain`; Copyright (c) 2025 Anthropic, PBC; MIT
License), keep this attribution with the port:
- Entries are JSON objects with a string `uuid` and a type among user, assistant, system,
  attachment, progress. Corrupt lines are skipped.
- The leaf: from every entry that no other entry names as its parentUuid, walk up to the nearest
  user or assistant entry. Prefer leaves that are not isSidechain, teamName or isMeta; among
  them take the latest in file order. A rewound branch (a reply the user edited away) is
  therefore not in the chain.
- Walk from the leaf to the root via parentUuid; stop at a uuid seen before. Unlike the SDK, a
  compact_boundary system entry (whose parentUuid is null) continues the walk at its
  logicalParentUuid, so older pages reach the conversation before a compaction. The boundary
  stays in the chain (the mapper turns it into `compacted`; it drops the isCompactSummary).

history(session_id, before_uuid=None, until=None) → HistoryPage:
- Kept entries: the chain minus isSidechain entries and minus entries timestamped at or after
  `until` (ISO 8601 UTC; the live log has those).
- A turn starts at each kept entry that maps to a user.message; entries before the first such
  entry form one leading turn. A page takes whole turns, newest first, until it holds
  PAGE_TURNS of them or has reached PAGE_ENTRIES entries — the turn that crosses either line
  comes whole, so a tool_use and its result never straddle a page.
- Newest page first: with before_uuid None the page ends at the last kept entry; otherwise just
  before the entry `before_uuid`. An unknown before_uuid, or a missing transcript, gives an
  empty page with next_before_uuid None.
- events: the page's entries oldest first through one Mapper for the page
  (Mapper(vault=str(vault), mode="default").entry), seeded with the subagent tool_use ids
  launched before the page so a background agent's close is not dropped. next_before_uuid: the
  uuid of the page's first entry when older kept entries exist, else None.

sessions() → list[SessionRow], newest `modified` first:
- One row per `<name>.jsonl` directly in the project folder. Skipped: a file whose first entry
  is isSidechain, and a file with no title and no first prompt.
- first_prompt: the first entry that maps to a user.message (mapper.entry rules), as typed:
  "/cmd args" for a command, else its text; whitespace runs collapsed to one space, cut to
  PROMPT_LEN characters ("…" last if cut).
- title: the last `custom-title` entry's customTitle, else the last `ai-title` entry's aiTitle,
  else first_prompt; collapsed and cut the same way to TITLE_LEN.
- entrypoint: the first user or assistant entry's `entrypoint`, "" if none; other: entrypoint
  not in MAIN_ENTRYPOINTS.
- modified: the newest `timestamp` in the file, else the file's mtime, as ISO 8601 UTC with
  milliseconds and "Z" (session.now_iso's format). cli_version: the last `version` in the file.

search(query) → list[SearchHit]:
- The query is NFKC-normalised and stripped; too short → []. The minimum is 2 characters when
  the normalised query is pure ASCII, else 1 (a single CJK character is already a selective
  search, unlike a single Latin letter). ASCII letters match case-insensitively, everything else
  exactly.
- Only sessions that are not `other`. A title containing the query is one hit (role "title",
  at ""); it comes from the listed row, so it stands even when the transcript behind it has
  gone. Transcript lines are scanned as bytes for the UTF-8 query or its JSON-escaped form —
  `"` and `\\` are stored escaped in the raw JSONL, so both forms are checked (ASCII lower-cased
  on both sides) — and only hit lines are parsed; a line counts only when the query is in text
  the user or VIKI wrote: the text of a user.message or text.done that mapper.entry makes from
  it (never tool input or output, thinking, or JSON keys). role: "user" or "assistant".
- snippet: that text as one line (whitespace collapsed), at most SNIPPET_LEN characters around
  the first match, with "…" where it was cut. at: the entry's timestamp.
- At most HITS_PER_SESSION hits per session (title first, then file order); sessions newest
  first; at most SEARCH_LIMIT hits in all.

exists(session_id): the project folder has `<session_id>.jsonl`.

Recorded roots. Unbound, only the vault's project is read. With a RootLookup bound
(bind_roots), a session VIKI recorded in another folder is read from that folder's project:
exists, transcript_path, history and conversation_chain find its file there, and history pages and
search anchors map tool paths relative to that folder. sessions() and search add each recorded
session whose project is not the vault's after the vault's own files, from one snapshot of the
lookup per listing and one per search; the deadline, ordering and `omitted` rules above cover
them. An unrecorded transcript of another folder is not listed. A snapshot that raises makes the
listing `error` (reason `read_failed`) carrying only the vault's own rows, so search answers
`error`; it logs one WARNING with no ids.

conversation_chain(session_id, until=None, cancelled=never) → ConversationChain: history's kept
entries minus any whose uuid is not a canonical UUID, taken from the same cached chain, with the
chain's status.

Caching (memory only): a session's row per (path, size, mtime), at most ROW_CACHE of them. A
changed file is rescanned within its read budget: growth and a newline at the old boundary do
not establish that its prefix is unchanged. The chains of the most recently read sessions are
checked against (size, mtime) before reuse and bounded by both CHAIN_CACHE entries and
CHAIN_CACHE_BYTES of source bytes — the newest entry is
never evicted. The caches are shared by the server's worker threads, so each takes a lock.

Read budgets. Every constant below is module-level and patchable in tests; the time
budgets take an injected `clock` and are checked between files, so one file's read can overshoot
a deadline by that file's own cost.
- A transcript at or under FULL_READ_BYTES is read whole. A larger one is read from its newest
  TAIL_WINDOW_BYTES only (history and search), plus its first HEAD_WINDOW_BYTES for the row scan.
  A window read is open → seek → read → close, and a tail window's leading partial line is
  dropped. The chain then ends where the window does, exactly as it ends at an unresolvable
  parentUuid, and every page built from it is status="partial", reason="tail_budget".
- A cold chain parse holds one of PARSE_PERMITS permits and re-checks the cache after acquiring
  it, so several readers of one cold file parse it once. A call waits at most PARSE_WAIT_S for
  the permit, in PARSE_WAIT_SLICE_S slices so that a cancelled caller stops within one, and at
  most PARSE_WAITERS calls wait at once; the rest are status="busy". Listing and search never
  take the permit: they hold one file's bytes at a time.
- list_sessions scans uncached files newest-mtime first and stops at LIST_DEADLINE_S. Unscanned
  and unreadable files are counted in `omitted`, which makes the listing `partial`; a file the
  CLI pruned mid-listing is an ordinary absence, not an error.
- run_search checks, between sessions, cancelled(), then SEARCH_DEADLINE_S, then
  SEARCH_SCAN_BYTES, and trims its encoded result to SEARCH_RESPONSE_BYTES. The reason that
  stopped it wins; last of all, a scan that reached every session but could not open one of their
  transcripts answers `read_failed`. QUERY_MAX_CHARS is the admission bound on the raw query:
  server.py refuses a longer one before calling here.

Memory note: one session can be hundreds of MB and grows every turn, which invalidates its cache
entries. So lines are iterated from the file's bytes, never split into a second copy, and the
sessions scan and search keep running values only; only a chain holds parsed entries. A chain
retains roughly half its file's bytes as live Python objects, which is why the chain cache is
bounded by bytes and not only by how many sessions are in it. None of the budgets above bounds
the Python heap or the process: they bound source bytes and counts. Measured on a dict-heavy
synthetic corpus a cold parse peaks at ~3.1x the bytes parsed and a retained chain holds
0.5-2.1x its source bytes, so the defaults put the catalog's own ceiling near 2.2 GiB — finite
and documented, not small.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from stat import S_ISREG
from typing import Any, Literal, Protocol

from viki_agent.drivers.claude.mapper import _SUBAGENT_TOOLS, HistoryMedia, Mapper
from viki_agent.events import (
    Event,
    SearchHit,
    SearchResult,
    SessionRow,
    SessionsResult,
    TextDone,
    UserMessage,
)

log = logging.getLogger(__name__)

MAIN_ENTRYPOINTS = frozenset({"claude-vscode", "cli", "viki-agent"})
PAGE_TURNS = 20
# The second bound on a page, and the one that binds on a modern session. PAGE_TURNS counts the
# user's prompts, and a turn is not small: measured on a real live transcript, the
# newest 20 turns are 4,715 events and 2.35 MB of JSON in one WebSocket frame, which the browser
# parses, folds through the reducer and mounts before anyone types anything. Entries, not events,
# because this is decided before any mapping — so the bound caps the work as well as the payload,
# and every event comes from exactly one entry. At the ~500 bytes per event measured there, this
# keeps an ordinary page inside WS_MAX_SIZE, the only size this repo has ever called reasonable
# for a frame. It rounds up to a whole turn and so cannot bound a single enormous turn — and
# that rounding is deliberate, not a shortcut: see _page_start.
PAGE_ENTRIES = 1000
TITLE_LEN = 80
PROMPT_LEN = 200
SNIPPET_LEN = 120
HITS_PER_SESSION = 3
SEARCH_LIMIT = 50
CHAIN_CACHE = 8
# The chain cache's real bound. CHAIN_CACHE counts files, which says nothing about memory: a
# cached chain retains roughly half its file's bytes as live Python objects (measured: 145 MB
# from a 276 MB transcript), so eight large sessions would hold over a gigabyte for the life of
# the process — nothing ever drops a chain, not even closing the session. Source bytes are the
# proxy because they are the one size known before the parse. Sized so a large real
# corpus (325 MB across 81 files, one of them 263 MB) still fits: a budget that evicts the
# session being read would turn every "load older" click into a full re-parse, which is worse
# than the unbounded cache it replaces. It exists to cap the growth case, not the usual one.
CHAIN_CACHE_BYTES = 512 * 1024 * 1024
# Read budgets (see the module docstring). Sized from measurements on a 200 MiB
# synthetic corpus: ~4.3 ms per MiB parsed, a heap peak of ~3.1x the bytes parsed, and ~1.2 ms
# per MiB for a search's byte scan.
# The largest real transcript measured is ~251 MiB, so it still parses whole (a ~0.8 GiB peak);
# 512 MiB would mean a ~1.6 GiB peak instead.
FULL_READ_BYTES = 256 * 1024 * 1024
TAIL_WINDOW_BYTES = 64 * 1024 * 1024
HEAD_WINDOW_BYTES = 4 * 1024 * 1024
# One permit, because the GIL serialises the parse anyway: two concurrent cold parses of one
# 96 MiB file took 0.96 s of wall time against 0.41 s alone, and held two 3x heap peaks at once.
# With one permit the re-check in _chain_for is single flight per file; raising this would need
# a per-file in-flight table to keep that true.
PARSE_PERMITS = 1
PARSE_WAIT_S = 5.0
# The permit wait runs in slices this long, so a waiting caller that has gone away (a
# superseded search) notices within about one slice and frees its request slot instead of
# holding it for the whole wait — measured at 49 to 101 ms while another request's parse runs,
# since the parse holds the interpreter. The request that holds the permit and parses keeps its
# slot until its own parse ends, even if it is itself superseded.
PARSE_WAIT_SLICE_S = 0.05
PARSE_WAITERS = 4
# LRU bound on the row cache. An entry is small (a row of seven short strings), so this is about
# not growing without limit across a long-running process, not a footprint anyone would notice.
ROW_CACHE = 4096
LIST_DEADLINE_S = 5.0
SEARCH_DEADLINE_S = 2.0
SEARCH_SCAN_BYTES = 2 * 1024 * 1024 * 1024
SEARCH_RESPONSE_BYTES = 256 * 1024
# Code points of the raw query. Enforced by server.py before any read (a 400), not here: this
# module's floor and NFKC rules still decide what a short query means.
QUERY_MAX_CHARS = 256
# The most roots one RootLookup snapshot lists, so a snapshot with fewer is complete. Mirrors
# session_roots.MAX_ROOT_ROWS, which a test pins equal: importing it would be an import cycle,
# since session_roots loads this module.
MAX_ROOT_ROWS = 4096

# A canonical, lowercase UUID (the shape of a transcript filename's stem). Anything else is
# treated as missing, defense in depth: session ids become file paths.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# The CLI's project-folder naming rule: every non-alphanumeric character becomes "-".
_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9]")
# Entry types that carry a uuid/parentUuid chain link (ported from the SDK's
# _TRANSCRIPT_ENTRY_TYPES).
_CHAIN_TYPES = frozenset({"user", "assistant", "progress", "system", "attachment"})
# What a read of one transcript could see. The same values HistoryPage.status reports: a page is
# only ever as complete as the chain it came from.
_ChainStatus = Literal["complete", "partial", "missing", "busy", "unreadable"]
# Mirrors events.SearchResult.reason. Named here so the local that carries one while run_search
# decides between them can be typed; a value events.py does not know would not type-check.
_SearchReason = Literal[
    "",
    "time_budget",
    "scan_budget",
    "file_budget",
    "response_budget",
    "listing_partial",
    "cancelled",
    "read_failed",
    "project_unreadable",
]


@dataclass(frozen=True)
class HistoryPage:
    events: list[Event]
    next_before_uuid: str | None  # the next older page's before_uuid; None at the start
    # How much of the transcript this page could see. `partial` pages come from the tail
    # window of an over-budget transcript; `busy`, `missing` and `unreadable` carry no events.
    status: _ChainStatus = "complete"
    reason: Literal["", "tail_budget", "read_failed"] = ""


@dataclass(frozen=True)
class ConversationChain:
    status: _ChainStatus
    entries: list[dict[str, Any]]


class RootLookup(Protocol):
    """The folders VIKI recorded its sessions in (session_roots.SessionRoots)."""

    def root_of(self, session_id: str) -> Path | None: ...

    def transcript_roots(self) -> Mapping[str, Path]: ...


def project_dir(claude_home: Path, vault: Path) -> Path:
    """The folder holding the vault's transcripts."""
    text = str(vault)
    if sys.platform != "win32" and os.path.isabs(text):
        # The SDK's rule: resolve symlinks first. macOS then NFC-normalizes, matching the CLI's
        # own build, which reads the on-disk (NFD-tolerant) name back as NFC; the Linux CLI names
        # the folder from the raw realpath, so Linux must not normalize.
        text = os.path.realpath(text)
        if sys.platform == "darwin":
            text = unicodedata.normalize("NFC", text)
    sanitized = _SANITIZE_RE.sub("-", text)
    return claude_home / "projects" / sanitized


# --- reading and parsing (shared by history, sessions and search) --------------------------------


def _iter_lines(raw: bytes) -> Iterator[bytes]:
    """Non-blank lines of `raw`, CRLF-tolerant, one at a time via `io.BytesIO` rather than
    `bytes.splitlines()` — which would hold a second full-size copy of `raw` (as a list of line
    objects) alongside it. See the module docstring's memory note."""
    for line in io.BytesIO(raw):
        stripped = line.rstrip(b"\r\n")
        if stripped.strip():
            yield stripped


def _stat_for_read(path: Path) -> os.stat_result | Literal["missing", "unreadable"]:
    """`path`'s stat, or why it cannot be read. A transcript the CLI deleted or has not written
    yet is `missing`; anything else — a lock, an ACL, a drive that went away — is `unreadable`,
    which the caller reports instead of showing an empty conversation."""
    try:
        return path.stat()
    except FileNotFoundError, NotADirectoryError:
        return "missing"
    except OSError:
        return "unreadable"


def _read_cost(size: int) -> int:
    """The source bytes one read of a file this size holds: the whole file, or a single tail
    window and its leading boundary probe once it is over FULL_READ_BYTES."""
    return min(size, TAIL_WINDOW_BYTES + 1) if size > FULL_READ_BYTES else size


def _window(
    path: Path, start: int, length: int, cancelled: Callable[[], bool] | None = None
) -> bytes:
    """`length` bytes of `path` from `start`: open, seek, read, close. The handle lives inside
    this call and nothing else, so a window read holds a file open for less time than reading it
    whole does (hard rule 1)."""
    with path.open("rb") as fh:
        fh.seek(start)
        if cancelled is None:
            return fh.read(length)
        buffer = io.BytesIO()
        while length:
            if cancelled():
                raise InterruptedError
            chunk = fh.read(min(length, 1 << 20))
            if not chunk:
                break
            buffer.write(chunk)
            length -= len(chunk)
        return buffer.getvalue()


def _tail_window(path: Path, size: int, window: int | None = None) -> bytes:
    """The newest `window` bytes of `path` (TAIL_WINDOW_BYTES, read at call time, when None),
    from the first whole line in them.

    A window usually starts mid-line, and that fragment is dropped whole. Most fragments are not
    JSON objects and would be dropped as corrupt anyway, but a torn write can leave a suffix that
    parses, and half an entry must never reach a chain. The read starts one byte early so the
    single rule "everything after the first newline" also covers a window that happens to begin
    exactly at a line start: that byte is then the newline ending the previous line, and the
    window keeps its first line instead of losing it to the rule."""
    if window is None:
        window = TAIL_WINDOW_BYTES
    begin = size - window
    if begin <= 0:
        return _window(path, 0, size)
    raw = _window(path, begin - 1, window + 1)
    cut = raw.find(b"\n")
    return raw[cut + 1 :] if cut != -1 else b""


def _parse_lines(raw: bytes) -> list[dict[str, Any]]:
    """Every line of `raw` that parses as a JSON object, in order. Corrupt lines and non-object
    lines are skipped; CRLF and invalid UTF-8 bytes (replaced) are tolerated."""
    entries: list[dict[str, Any]] = []
    for line in _iter_lines(raw):
        try:
            obj = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError, RecursionError:
            continue
        if isinstance(obj, dict):
            obj["_viki_source_revision"] = hashlib.sha256(line).hexdigest()
            entries.append(obj)
    return entries


def _read_entries(path: Path) -> list[dict[str, Any]]:
    """The entries of `path` within the read budget: every line of the file when it is at or
    under FULL_READ_BYTES, else every line of its newest TAIL_WINDOW_BYTES.

    Raises OSError: a transcript that cannot be read is an observation the caller reports, not
    an empty conversation. The size comes from this call's own stat rather than the caller's, so
    the one place that decides "whole or window" is the one place that reads."""
    size = path.stat().st_size
    if size <= FULL_READ_BYTES:
        return _parse_lines(_window(path, 0, size))
    return _parse_lines(_tail_window(path, size))


def _build_chain(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The leaf-and-walk chain (see the module docstring), root first."""
    if not entries:
        return []
    by_uuid = {e["uuid"]: e for e in entries}
    index = {e["uuid"]: i for i, e in enumerate(entries)}

    def resolve_parent(entry: dict[str, Any]) -> dict[str, Any] | None:
        parent = entry.get("parentUuid")
        if parent:
            # An unresolvable parentUuid ends the walk here, exactly as the SDK port: real
            # transcripts never have one (see the module docstring's chain-walk rule).
            return by_uuid.get(parent)
        if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
            logical = entry.get("logicalParentUuid")
            return by_uuid.get(logical) if logical else None
        return None

    parent_uuids = {e["parentUuid"] for e in entries if e.get("parentUuid")}
    terminals = [e for e in entries if e["uuid"] not in parent_uuids]

    leaves: list[dict[str, Any]] = []
    for terminal in terminals:
        cur: dict[str, Any] | None = terminal
        seen: set[str] = set()
        while cur is not None:
            uid = cur["uuid"]
            if uid in seen:
                break
            seen.add(uid)
            if cur.get("type") in ("user", "assistant"):
                leaves.append(cur)
                break
            cur = resolve_parent(cur)
    if not leaves:
        return []

    main_leaves = [
        leaf
        for leaf in leaves
        if not leaf.get("isSidechain") and not leaf.get("teamName") and not leaf.get("isMeta")
    ]
    candidates = main_leaves or leaves
    leaf = max(candidates, key=lambda e: index[e["uuid"]])

    chain: list[dict[str, Any]] = []
    seen2: set[str] = set()
    cur = leaf
    while cur is not None:
        uid = cur["uuid"]
        if uid in seen2:
            break
        seen2.add(uid)
        chain.append(cur)
        cur = resolve_parent(cur)
    chain.reverse()
    return chain


def _log_id(value: Any) -> str:
    """A session id or entry uuid for the log, or "?" when it is not one. Never the raw field:
    transcript fields are attacker-influenced, and a value carrying a newline would forge whole
    lines in viki-agent.log. The same trade server.py makes when it logs a dropped client
    message's type — show it only when it is a value we recognise."""
    return value if isinstance(value, str) and _UUID_RE.match(value) else "?"


def _map_entry(mapper: Mapper, entry: dict[str, Any], *, source_revision: str = "") -> list[Event]:
    """mapper.entry(), with one bad entry costing one entry.

    Everything read here is attacker-influenced: the user pastes web pages into sessions, and
    a transcript can also be truncated mid-write or left half-formed by a crash. The mapper is
    written against the shapes claude actually emits, so an off-shape entry can raise — and
    unguarded that took down the whole read, turning one damaged line into a session that would
    not open, a history page that would not load, or a search that returned nothing."""
    try:
        return list(mapper.entry(entry, source_revision=source_revision))
    except Exception as exc:
        # The likeliest way this app breaks is the CLI writing a shape the mapper was not
        # written against, and this handler is the only place that break surfaces: sessions(),
        # history() and search() all funnel through it, and the symptom is a page quietly short
        # of turns, never an error. A constant sentence left a log full of identical lines that
        # could not say which session, which entry, or which of the mapper's ~690 lines gave up.
        #
        # The raise site, not exc_info: a rendered traceback also carries str(exc), and
        # Mapper.entry's `int(meta.get("preTokens", 0))` raises a ValueError that quotes the
        # transcript's own value verbatim. That is a frame body, which hard rule 8 keeps out of
        # the log file; file:line names the broken rule just as well and can carry nothing.
        site = traceback.extract_tb(exc.__traceback__)[-1]
        etype = entry.get("type")
        log.warning(
            "skipped an unreadable transcript entry: session=%s uuid=%s type=%s %s at %s:%s",
            _log_id(entry.get("sessionId")),
            _log_id(entry.get("uuid")),
            # Allowlisted for the same reason _log_id exists. Every caller already narrows the
            # type to this set, so a value outside it is a malformed line, not information.
            etype if isinstance(etype, str) and etype in _CHAIN_TYPES else "?",
            type(exc).__name__,
            Path(site.filename).name,
            site.lineno,
        )
        return []


def _subagent_ids_before(entries: list[dict[str, Any]]) -> set[str]:
    """The subagent tool_use ids launched in `entries` — what a Mapper reading them would have
    recorded. Total by design: it runs over the same off-shape entries _map_entry guards, so it
    must never raise. See history() for why a page needs the launches that precede it."""
    ids: set[str] = set()
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        # Mapper._history_assistant maps content[0] and nothing else, so a launch is only there.
        block = content[0] if isinstance(content, list) and content else None
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if block.get("name") in _SUBAGENT_TOOLS and "id" in block:
            ids.add(str(block["id"]))
    return ids


def _page_start(turn_bounds: list[int], end: int) -> int:
    """The first entry of the newest page: whole turns taken newest first, until the page holds
    PAGE_TURNS of them or has reached PAGE_ENTRIES entries.

    A turn is never split — keeping a tool_use and its result on one page is what the page rule
    exists for — so the entry bound rounds up: the turn that crosses it comes whole, and one
    workflow turn with thousands of tool calls is still a single page. Rounding down instead
    would be a trap rather than a tighter bound: a short turn after an enormous one would open
    the session on two lines that do not fill the column, and the conversation asks for the next
    page only from a scroll event, which a column that does not overflow never fires."""
    candidates = turn_bounds[-PAGE_TURNS:]
    start = candidates[-1]  # the newest turn ships whole, however large
    for bound in reversed(candidates[:-1]):
        if end - start >= PAGE_ENTRIES:
            break
        start = bound
    return start


def _starts_turn(entry: dict[str, Any], mapper: Mapper) -> bool:
    """Whether `entry` maps to a user.message (mapper.entry rules): a new turn starts here."""
    if entry.get("type") != "user":
        return False
    return any(isinstance(event, UserMessage) for event in _map_entry(mapper, entry))


def _at_or_after(entry: dict[str, Any], until: str | None) -> bool:
    if until is None:
        return False
    ts = entry.get("timestamp")
    return isinstance(ts, str) and ts >= until


def _clip(text: str, limit: int) -> str:
    """Whitespace runs collapsed to one space, cut to `limit` chars ("…" last if cut)."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _mtime_iso(mtime: float) -> str:
    return (
        datetime.fromtimestamp(mtime, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _row_lines(path: Path, size: int) -> Iterator[bytes]:
    """The lines a row scan reads: every line of the file when it is within FULL_READ_BYTES,
    else its first HEAD_WINDOW_BYTES (the first prompt and the entrypoint) followed by its newest
    TAIL_WINDOW_BYTES (the titles, the version and the newest timestamp).

    Between the two windows lies the part of an over-budget transcript no row ever sees: a
    custom-title line written in the middle of one is missed and the row keeps the older title.
    Raises OSError from the first line, so a caller's `except OSError` around the scan covers it.
    """
    if size <= FULL_READ_BYTES:
        yield from _iter_lines(_window(path, 0, size))
        return
    # The head window's own last line is cut short; a piece of a JSON object does not parse, so
    # the scan skips it for the same reason it skips a corrupt line.
    yield from _iter_lines(_window(path, 0, HEAD_WINDOW_BYTES))
    yield from _iter_lines(_tail_window(path, size))


@dataclass(frozen=True)
class _RowState:
    """The running values one streaming row scan carries from one line to the next."""

    first_entry_seen: bool = False
    sidechain: bool = False
    first_prompt_raw: str = ""
    custom_title: str | None = None
    ai_title: str | None = None
    entrypoint: str = ""
    entrypoint_seen: bool = False
    version: str = ""
    newest_ts: str = ""


def _fold_row_lines(state: _RowState, lines: Iterator[bytes], vault: Path) -> _RowState:
    """`state` advanced over `lines`, in one streaming pass that never holds a parsed-entries
    list (see the module docstring's memory note). A fresh Mapper per call is enough: whether a
    user entry becomes a UserMessage depends on that entry alone."""
    mapper = Mapper(vault=str(vault), mode="default")
    first_entry_seen = state.first_entry_seen
    first_prompt_raw = state.first_prompt_raw
    custom_title = state.custom_title
    ai_title = state.ai_title
    entrypoint, entrypoint_seen = state.entrypoint, state.entrypoint_seen
    version = state.version
    newest_ts = state.newest_ts
    for line in lines:
        try:
            entry = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError, RecursionError:
            continue
        if not isinstance(entry, dict):
            continue
        if not first_entry_seen:
            first_entry_seen = True
            if entry.get("isSidechain"):
                return replace(state, first_entry_seen=True, sidechain=True)
        etype = entry.get("type")
        if etype == "user" and not first_prompt_raw:
            for event in _map_entry(mapper, entry):
                if isinstance(event, UserMessage):
                    first_prompt_raw = f"{event.command} {event.text}".strip()
                    break
        elif etype == "custom-title" and isinstance(entry.get("customTitle"), str):
            custom_title = entry["customTitle"]
        elif etype == "ai-title" and isinstance(entry.get("aiTitle"), str):
            ai_title = entry["aiTitle"]
        if not entrypoint_seen and etype in ("user", "assistant"):
            entrypoint = str(entry.get("entrypoint") or "")
            entrypoint_seen = True
        version_field = entry.get("version")
        if isinstance(version_field, str):
            version = version_field
        ts = entry.get("timestamp")
        if isinstance(ts, str) and ts > newest_ts:
            newest_ts = ts
    return replace(
        state,
        first_entry_seen=first_entry_seen,
        first_prompt_raw=first_prompt_raw,
        custom_title=custom_title,
        ai_title=ai_title,
        entrypoint=entrypoint,
        entrypoint_seen=entrypoint_seen,
        version=version,
        newest_ts=newest_ts,
    )


def _scan_row(path: Path, stat: os.stat_result, vault: Path) -> _RowState:
    """The running state of reading every entry of `path` within the read budget: first prompt,
    titles, entrypoint, version and newest timestamp.

    A file that could not be read raises OSError: that is an unread transcript, which the listing
    counts, not a session the vault does not have."""
    state = _RowState()
    return _fold_row_lines(state, _row_lines(path, stat.st_size), vault)


def _row_from_state(state: _RowState, session_id: str, stat: os.stat_result) -> SessionRow | None:
    """The row those running values make, or None for a file that is no session (a sidechain, or
    nothing to title it by)."""
    if state.sidechain or not state.first_entry_seen:
        return None
    title = _clip(state.custom_title or state.ai_title or state.first_prompt_raw, TITLE_LEN)
    if not title:
        return None
    return SessionRow(
        session_id=session_id,
        title=title,
        first_prompt=_clip(state.first_prompt_raw, PROMPT_LEN),
        entrypoint=state.entrypoint,
        modified=state.newest_ts or _mtime_iso(stat.st_mtime),
        cli_version=state.version,
        other=state.entrypoint not in MAIN_ENTRYPOINTS,
    )


def _search_result(
    hits: list[SearchHit], reason: _SearchReason, scanned: int, total: int, *, unread: bool
) -> SearchResult:
    """The result the server will send, inside SEARCH_RESPONSE_BYTES, carrying the two weakest
    reasons: `response_budget` (a hit had to be dropped) outranks `read_failed`, and a
    `reason` passed in outranks both.

    Trailing hits go first: hits arrive newest session first, so the last one is the least likely
    to be what the user is looking for, and dropping any makes the result partial. Encoded the
    way server.py encodes it (ASCII-escaped, no spaces); the `request_id` it fills in afterwards
    adds at most 36 bytes. With clipped snippets and at most SEARCH_LIMIT hits this is an
    invariant, not a path anything takes: SEARCH_LIMIT hits with a title and snippet clipped to
    their limits encode to 16 KB in ASCII and 65 KB in CJK text — six bytes a character, because
    the encoder escapes to ASCII — against a 256 KiB cap.

    `unread` is the last arm of all: a scan that reached every session but could not open one of
    their transcripts answers `read_failed`, so a result short of a session says so instead of
    passing for complete. Every stronger reason above it wins, this one included."""
    kept = list(hits)
    while True:
        dropped = len(kept) < len(hits)
        weakest: _SearchReason = "response_budget" if dropped else ("read_failed" if unread else "")
        result = SearchResult(
            hits=kept,
            state="partial" if reason or weakest else "ready",
            reason=reason or weakest,
            scanned=scanned,
            total=total,
        )
        if not kept or len(json.dumps(asdict(result), separators=(",", ":"))) <= (
            SEARCH_RESPONSE_BYTES
        ):
            return result
        kept.pop()


def _snippet(text: str, match_start: int, match_end: int, limit: int) -> str:
    """`text` (already one line) cut to at most `limit` chars around [match_start, match_end),
    with "…" where it was cut."""
    n = len(text)
    if n <= limit:
        return text
    budget = max(limit - (match_end - match_start), 0)
    left = budget // 2
    start = max(match_start - left, 0)
    end = min(match_end + (budget - left), n)
    lead, trail = start > 0, end < n
    if lead:
        start += 1
    if trail:
        end -= 1
    snippet = text[start:end]
    if lead:
        snippet = "…" + snippet
    if trail:
        snippet += "…"
    return snippet


class Catalog:
    def __init__(self, claude_home: Path, vault: Path) -> None:
        self.vault = vault
        self.claude_home = claude_home
        self._project_dir = project_dir(claude_home, vault)
        self._roots: RootLookup | None = None
        # (size, mtime_ns, row), least recently used first; bounded by ROW_CACHE.
        self._row_cache: OrderedDict[str, tuple[int, int, SessionRow]] = OrderedDict()
        # (size, mtime_ns, chain, from a tail window), least recently used first.
        self._chain_cache: OrderedDict[str, tuple[int, int, list[dict[str, Any]], bool]] = (
            OrderedDict()
        )
        self._chain_lock = threading.Lock()  # guards the cache and the waiter count, never a parse
        # One permit for every cold parse in the process: the GIL serialises them anyway, so a
        # second concurrent parse buys no speed and doubles the peak heap. _parse_waiters counts
        # only the calls blocked on it, so PARSE_WAITERS is a queue bound, not a concurrency one.
        self._parse_permit = threading.BoundedSemaphore(PARSE_PERMITS)
        self._parse_waiters = 0
        # Listings serialise: the second caller of two then finds the row cache warm instead of
        # scanning the same files again, and the row cache needs no lock of its own.
        self._listing_lock = threading.Lock()
        self._history_media: HistoryMedia | None = None
        self._artifacts_enabled = False

    def bind_artifacts(self) -> None:
        """Enable additive artifact observations for the current application launch."""
        self._artifacts_enabled = True

    def bind_roots(self, roots: RootLookup) -> None:
        """Read each session VIKI recorded in another folder from that folder's project."""
        self._roots = roots

    def root_for(self, session_id: str) -> Path:
        """The folder `session_id` ran in: its recorded root, else the vault."""
        root = self._roots.root_of(session_id) if self._roots is not None else None
        return self.vault if root is None else root

    def _recorded_roots(self) -> Mapping[str, Path] | None:
        """One snapshot of the recorded roots, or None when the lookup fails: the listing then
        answers `error` and carries the vault's own rows only (see the module docstring)."""
        if self._roots is None:
            return {}
        try:
            return self._roots.transcript_roots()
        except Exception as exc:
            log.warning("session roots unreadable: %s", type(exc).__name__)
            return None

    def history_mapper(self, root: Path | None = None) -> Mapper:
        """Use the same launch-bound history grammar for paging and searched windows."""
        return Mapper(
            vault=str(root or self.vault),
            mode="default",
            history_media=self._history_media,
            artifacts_enabled=self._artifacts_enabled,
        )

    def bind_history_media(self, resolver: HistoryMedia) -> None:
        """Bind the launch-scoped, read-only asset correlator after construction."""
        self._history_media = resolver

    def _transcript(self, session_id: str, recorded: Path | None) -> Path:
        """The transcript of a session run in its `recorded` root, or in the vault for None."""
        folder = self._project_dir if recorded is None else project_dir(self.claude_home, recorded)
        return folder / f"{session_id}.jsonl"

    def _path_for(self, session_id: str) -> Path:
        root = self._roots.root_of(session_id) if self._roots is not None else None
        return self._transcript(session_id, root)

    def exists(self, session_id: str) -> bool:
        """Whether the session's project (its recorded root's, else the vault's) has a
        transcript for `session_id`."""
        if not _UUID_RE.match(session_id):
            return False
        return self._path_for(session_id).is_file()

    def transcript_path(self, session_id: str) -> Path | None:
        """The file backing `session_id`, or None when the project has none. Session ids reach
        this from the client, so the UUID guard in exists() — not the caller — is what keeps an
        id from naming a path outside the project folder."""
        return self._path_for(session_id) if self.exists(session_id) else None

    def paths_for(self, session_ids: list[str]) -> dict[str, Path]:
        """Each canonical-UUID id's transcript path, the file there or not: in its recorded
        root's project, from one snapshot of the recorded roots, else in the vault's. A snapshot
        of fewer than MAX_ROOT_ROWS roots is complete. One that long may have stopped short, so
        an id it lacks whose vault file is absent is looked up alone (_path_for), as every id is
        when the snapshot fails. An id that is not a canonical UUID gets no path, as exists()
        refuses it."""
        roots = self._recorded_roots()
        paths: dict[str, Path] = {}
        for session_id in session_ids:
            if not _UUID_RE.match(session_id):
                continue
            if roots is not None and session_id in roots:
                paths[session_id] = self._transcript(session_id, roots[session_id])
                continue
            path = self._transcript(session_id, None)
            if roots is None or (len(roots) >= MAX_ROOT_ROWS and not path.is_file()):
                path = self._path_for(session_id)
            paths[session_id] = path
        return paths

    def _cached_chain(
        self, session_id: str, stat: os.stat_result
    ) -> tuple[_ChainStatus, list[dict[str, Any]]] | None:
        """The chain cached for exactly this (size, mtime_ns), or None once any stale entry for
        the session has been dropped."""
        with self._chain_lock:
            cached = self._chain_cache.get(session_id)
            if cached is not None and cached[:2] == (stat.st_size, stat.st_mtime_ns):
                self._chain_cache.move_to_end(session_id)
                return ("partial" if cached[3] else "complete"), cached[2]
            # Drop the stale entry before the parse, not by overwriting it after. The ordinary
            # case is the session the user is in: claude appended a turn, so (size, mtime_ns)
            # no longer matches and the next "load older" re-parses; were the stale entry kept,
            # the old chain, the file's bytes and the new entries list would all be resident
            # together. Measured on a 263 MB transcript that was ~145 MB of an ~850 MB peak, held
            # for a chain that was about to be replaced anyway.
            self._chain_cache.pop(session_id, None)
        return None

    def _chain_for(
        self, session_id: str, cancelled: Callable[[], bool]
    ) -> tuple[_ChainStatus, list[dict[str, Any]]]:
        """The session's conversation chain and how much of the transcript it could be built
        from: "partial" is a chain from the tail window of a transcript over FULL_READ_BYTES.
        The three failure statuses carry no chain and no wait. A caller waiting for the
        permit is answered "busy" once `cancelled()` is true."""
        if not _UUID_RE.match(session_id):
            return "missing", []
        path = self._path_for(session_id)
        stat = _stat_for_read(path)
        if isinstance(stat, str):
            return stat, []
        cached = self._cached_chain(session_id, stat)
        if cached is not None:
            return cached

        if not self._parse_permit.acquire(blocking=False):
            with self._chain_lock:
                if self._parse_waiters >= PARSE_WAITERS:
                    # The queue is full, so this call is told so now. Joining it would hold a
                    # worker thread for up to PARSE_WAITERS × PARSE_WAIT_S, and the browser
                    # would have nothing to show for the wait but a spinner.
                    return "busy", []
                self._parse_waiters += 1
            deadline = time.monotonic() + PARSE_WAIT_S
            acquired = False
            try:
                while not acquired and not cancelled():
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    acquired = self._parse_permit.acquire(timeout=min(PARSE_WAIT_SLICE_S, left))
            finally:
                with self._chain_lock:
                    self._parse_waiters -= 1
            if not acquired:
                return "busy", []
        try:
            # Re-stat and re-check under the permit. A call that waited behind a parse of this
            # same file finds the leader's chain and returns it: that re-check, not the permit
            # count, is what makes one permit single flight per file.
            fresh = _stat_for_read(path)
            if isinstance(fresh, str):
                return fresh, []
            cached = self._cached_chain(session_id, fresh)
            if cached is not None:
                return cached
            try:
                raw = _read_entries(path)
            except FileNotFoundError:
                # Pruned between the re-stat above and _read_entries' own: the CLI deletes
                # transcripts, and an absence a microsecond later is still an absence. Without
                # this arm the browser shows "could not load" for a file that is simply gone.
                return "missing", []
            except OSError:
                return "unreadable", []
            # From this call's stat, which is also the cache key: if the file crossed the budget
            # between the two stats the flag and the bytes are from different moments, and the
            # next read re-parses anyway because size and mtime no longer match.
            partial = fresh.st_size > FULL_READ_BYTES
            typed = [
                e for e in raw if e.get("type") in _CHAIN_TYPES and isinstance(e.get("uuid"), str)
            ]
            chain = _build_chain(typed)
            with self._chain_lock:
                self._chain_cache[session_id] = (
                    fresh.st_size,
                    fresh.st_mtime_ns,
                    chain,
                    partial,
                )
                self._chain_cache.move_to_end(session_id)
                self._evict()
            return ("partial" if partial else "complete"), chain
        finally:
            self._parse_permit.release()

    def conversation_chain(
        self,
        session_id: str,
        until: str | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> ConversationChain:
        """The session's main chain for revisioned search, anchor and artifact detail: the chain
        without isSidechain entries, entries at or after `until`, and entries whose uuid is not a
        canonical UUID. A failure status carries no entries.

        Served from the chain cache, so a second reader of a cold file waits on the parse permit
        (PARSE_WAIT_S, or until `cancelled()`, then "busy") and reuses the chain instead of
        parsing it again. The entries are the cache's own dicts, shared with every other reader:
        callers never mutate them. Each carries `_viki_source_revision`, the SHA-256 of its raw
        line, which is why no raw line bytes are kept beside it."""
        status, chain = self._chain_for(session_id, cancelled)
        if status not in ("complete", "partial"):
            return ConversationChain(status, [])
        return ConversationChain(
            status,
            [
                entry
                for entry in chain
                if not entry.get("isSidechain")
                and not _at_or_after(entry, until)
                and _UUID_RE.fullmatch(entry["uuid"])
            ],
        )

    def _evict(self) -> None:
        """Oldest first until the cache is inside both bounds. Called holding `_chain_lock`.

        The newest entry always stays, whatever it weighs: it is the session being read, and the
        caller is about to page through it. Evicting it would make every "load older" click a
        fresh parse of the largest file in the vault — a byte budget that trades a bounded
        footprint for an unbounded re-parse rate is worse than no budget at all.

        A chain from a tail window weighs that window, not its file: the bytes it was built from
        are the ones it retains a share of."""
        total = sum(_read_cost(size) for size, _, _, _ in self._chain_cache.values())
        while len(self._chain_cache) > 1 and (
            len(self._chain_cache) > CHAIN_CACHE or total > CHAIN_CACHE_BYTES
        ):
            _, (size, _, _, _) = self._chain_cache.popitem(last=False)
            total -= _read_cost(size)

    def history(
        self, session_id: str, *, before_uuid: str | None = None, until: str | None = None
    ) -> HistoryPage:
        """One page of a session's conversation, newest page first (see the module rules).

        The page carries the chain's own status: every page of a chain built from a tail window
        is partial, including the empty one an unknown before_uuid gives, because an entry this
        far back may be outside the window rather than absent from the session."""
        status, chain = self._chain_for(session_id, lambda: False)
        if status in ("missing", "busy", "unreadable"):
            reason = "read_failed" if status == "unreadable" else ""
            return HistoryPage(events=[], next_before_uuid=None, status=status, reason=reason)
        partial = status == "partial"
        empty = HistoryPage(
            events=[],
            next_before_uuid=None,
            status=status,
            reason="tail_budget" if partial else "",
        )
        if not chain:
            return empty
        kept = [e for e in chain if not e.get("isSidechain") and not _at_or_after(e, until)]
        if not kept:
            return empty

        if before_uuid is None:
            end = len(kept)
        else:
            found_end = next((i for i, e in enumerate(kept) if e["uuid"] == before_uuid), None)
            if found_end is None:
                return empty
            end = found_end

        root = self.root_for(session_id)
        classifier = Mapper(vault=str(root), mode="default")
        starts = [i for i in range(end) if _starts_turn(kept[i], classifier)]
        turn_bounds = starts if starts and starts[0] == 0 else [0, *starts]
        start = _page_start(turn_bounds, end)

        mapper = Mapper(
            vault=str(root),
            mode="default",
            history_media=self._history_media,
            artifacts_enabled=self._artifacts_enabled,
        )
        # A background agent's tool_result says async_launched and leaves its routing row open;
        # what closes it is the <task-notification> injected whenever it finishes, however many
        # turns later. Mapper only closes a row whose launch that same instance saw, so a page
        # holding just the notification would drop the subagent.done the live path emits for the
        # same entries — and, worse, would map the launch's own tool_result as an ordinary
        # tool.done. Hand it the launches that precede the page, and only those: the Mapper's
        # closed-subagent set must stay empty here, or this page's own closes are suppressed.
        mapper.note_subagents(_subagent_ids_before(kept[:start]))
        events: list[Event] = []
        for entry in kept[start:end]:
            events.extend(
                _map_entry(
                    mapper,
                    entry,
                    source_revision=entry.get("_viki_source_revision", "")
                    if self._artifacts_enabled
                    else "",
                )
            )
        # The window's oldest kept entry ends the walk back, and the page after it has None:
        # older turns exist in the file but not in anything this read could see.
        next_before_uuid = kept[start]["uuid"] if start > 0 else None
        return HistoryPage(
            events=events,
            next_before_uuid=next_before_uuid,
            status=status,
            reason="tail_budget" if partial else "",
        )

    def _cached_row(self, session_id: str, stat: os.stat_result) -> SessionRow | None:
        cached = self._row_cache.get(session_id)
        if cached is not None and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            self._row_cache.move_to_end(session_id)
            return cached[2]
        return None

    def _scan_and_cache(self, path: Path, stat: os.stat_result, root: Path) -> SessionRow | None:
        """The row for `path`, a transcript of a session run in `root`, scanned and cached.
        Raises OSError if the file cannot be read or changes during the scan."""
        session_id = path.stem
        state = _scan_row(path, stat, root)
        # A captured-size read may have stopped before a newly appended suffix. Do not cache
        # its values as a complete observation of the changed file.
        fresh = path.stat()
        if (fresh.st_size, fresh.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            raise OSError("transcript changed during read")
        row = _row_from_state(state, session_id, stat)
        if row is None:  # a sidechain file, or nothing to title it by: not a session
            self._row_cache.pop(session_id, None)
            return None
        self._row_cache[session_id] = (stat.st_size, stat.st_mtime_ns, row)
        self._row_cache.move_to_end(session_id)
        # A project with more transcripts than ROW_CACHE evicts rows this same listing cached, so
        # the next one rescans them. At 4,096 against a vault of ~75 files that cannot happen;
        # the bound exists so a long-lived process cannot grow a row cache without limit.
        while len(self._row_cache) > ROW_CACHE:
            self._row_cache.popitem(last=False)
        return row

    def sessions(self) -> list[SessionRow]:
        """Every transcript in the vault's project, newest first (see the module rules)."""
        return self.list_sessions().sessions

    def list_sessions(self, *, clock: Callable[[], float] = time.monotonic) -> SessionsResult:
        """The sessions list as a typed observation: what could be read of the vault's
        project, and how much of it went unread.

        Cached rows always travel; only files whose row must be scanned cost anything, and they
        are scanned newest-mtime first so a listing cut short by LIST_DEADLINE_S carries the rows
        the user is most likely looking for. `omitted` counts transcripts with no row because
        this listing could not read them — never a file that is no session, which is a known
        answer and not a gap."""
        with self._listing_lock:
            return self._list(clock)

    def _list(self, clock: Callable[[], float]) -> SessionsResult:
        started = clock()
        try:
            entries = list(self._project_dir.iterdir())
        except FileNotFoundError:
            # No project folder is a known zero: the vault has never been opened in claude. The
            # sessions VIKI recorded in other folders still list.
            entries = []
        except OSError:
            return SessionsResult(sessions=[], state="error", reason="project_unreadable")

        candidates = [(path, self.vault) for path in entries if path.suffix == ".jsonl"]
        # Then each session VIKI recorded in another folder, from that folder's project; an
        # unrecorded transcript there is never listed. The id guard is defense in depth, since a
        # recorded id becomes a path.
        roots = self._recorded_roots()
        folders: dict[Path, Path] = {}
        for sid, root in (roots or {}).items():
            if root not in folders:
                folders[root] = project_dir(self.claude_home, root)
            if _UUID_RE.match(sid) and folders[root] != self._project_dir:
                candidates.append((folders[root] / f"{sid}.jsonl", root))

        rows: list[SessionRow] = []
        unscanned: list[tuple[Path, os.stat_result, Path]] = []
        seen: set[str] = set()
        unreadable = 0
        for path, root in candidates:
            if path.stem in seen:
                continue
            stat = _stat_for_read(path)
            if isinstance(stat, str):
                # "missing" is the CLI pruning a transcript between iterdir and this stat, which
                # is an ordinary absence; anything else is a transcript this listing did not read.
                if stat == "unreadable":
                    unreadable += 1
                continue
            if not S_ISREG(stat.st_mode):
                continue
            seen.add(path.stem)
            cached = self._cached_row(path.stem, stat)
            if cached is not None:
                rows.append(cached)
            else:
                unscanned.append((path, stat, root))

        # Rows the user may never have seen do not justify holding them forever: a file that is
        # no longer in the project cannot become a row again without being scanned again.
        for gone in [sid for sid in self._row_cache if sid not in seen]:
            del self._row_cache[gone]

        unscanned.sort(key=lambda item: item[1].st_mtime_ns, reverse=True)
        overdue = 0
        for index, (path, stat, root) in enumerate(unscanned):
            if clock() - started >= LIST_DEADLINE_S:
                overdue = len(unscanned) - index
                break
            try:
                row = self._scan_and_cache(path, stat, root)
            except FileNotFoundError:
                continue  # pruned between the stat and the read: still an absence, not a failure
            except OSError:
                unreadable += 1
                continue
            if row is not None:
                rows.append(row)

        rows.sort(key=lambda r: r.modified, reverse=True)
        if roots is None:
            # A `ready` list would present chats run in other folders as gone.
            return SessionsResult(sessions=rows, state="error", reason="read_failed")
        # `partial` and a count travel together, in both directions, and one expression decides
        # both: the rail's only sentence for a partial listing names the number it could not
        # read, so `partial` with nothing omitted has nothing truthful to say, and `ready` with
        # something omitted would show an incomplete list as if it were the whole one. Only
        # `error` above is free of the rule, because it shows no count at all.
        omitted = overdue + unreadable
        if omitted == 0:
            return SessionsResult(sessions=rows, state="ready")
        # The deadline wins when both happened: it is the one the next listing will fix by
        # itself, and it says something the user can act on (wait, or look at fewer sessions).
        reason = "time_budget" if overdue else "read_failed"
        return SessionsResult(sessions=rows, state="partial", reason=reason, omitted=omitted)

    def _search_transcript(
        self,
        path: Path,
        size: int,
        row: SessionRow,
        needle: bytes,
        probe: bytes,
        escaped: bytes,
        needle_text: str,
        limit: int,
        root: Path | None = None,
    ) -> list[SearchHit]:
        """At most `limit` hits from one transcript of a session run in `root` (None: the vault):
        the whole file within FULL_READ_BYTES, else its newest tail window. Raises OSError, so a
        caller can tell an unread file from one with no matches."""
        raw = _window(path, 0, size) if size <= FULL_READ_BYTES else _tail_window(path, size)
        mapper = Mapper(vault=str(root or self.vault), mode="default")
        out: list[SearchHit] = []
        # `bytes.lower()` folds A-Z and nothing else, so when neither form we look for holds an
        # ASCII letter no fold can change whether a line matches — and skipping it drops a
        # second full-size copy of every line of the file. A query with no ASCII letter (one in
        # CJK script, say) takes that path, and the file this runs over can be hundreds of MB.
        fold = any(0x61 <= byte <= 0x7A for byte in probe + escaped)
        for line in _iter_lines(raw):
            if len(out) >= limit:
                break
            low = line.lower() if fold else line
            # `probe`, not `needle`: see search() on why the whole query is the wrong thing to
            # look for in a raw line.
            if probe not in low and escaped not in low:
                continue
            try:
                entry = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError, RecursionError:
                continue
            if not isinstance(entry, dict) or entry.get("type") not in ("user", "assistant"):
                continue
            at = str(entry.get("timestamp", ""))
            for event in _map_entry(mapper, entry):
                if len(out) >= limit:
                    break
                role: Literal["user", "assistant"]
                if isinstance(event, UserMessage):
                    role, raw_text = "user", event.text
                elif isinstance(event, TextDone):
                    role, raw_text = "assistant", event.text
                else:
                    continue
                text = " ".join(raw_text.split())
                hay = text.encode("utf-8", "replace").lower()  # see search() on "replace"
                idx = hay.find(needle)
                if idx == -1:
                    continue
                match_start = len(hay[:idx].decode("utf-8"))
                match_end = match_start + len(needle_text)
                out.append(
                    SearchHit(
                        session_id=row.session_id,
                        title=row.title,
                        role=role,
                        snippet=_snippet(text, match_start, match_end, SNIPPET_LEN),
                        at=at,
                    )
                )
        return out

    def search(self, query: str) -> list[SearchHit]:
        """Titles and the text the user and VIKI wrote (see the module rules)."""
        return self.run_search(query).hits

    def run_search(
        self,
        query: str,
        *,
        cancelled: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> SearchResult:
        """Search as a typed observation: the hits, how much of the vault they came
        from, and which budget stopped the scan.

        The budgets are checked between sessions, in this order: cancelled() first,
        so a query the user has already typed past costs no further file; then the deadline,
        measured from this call and so including the listing, because that is the wait they see;
        then the byte budget. One file's scan can overshoot the deadline by its own cost —
        measured at 0.38 s for 96 MiB of the worst case.
        """
        started = clock()
        # A lone surrogate (JSON can carry one: a JS string cut mid-pair) has no UTF-8 form;
        # "replace" makes each one "?", one byte for one character, so offsets stay aligned.
        normalized = unicodedata.normalize("NFKC", query).strip()
        normalized = normalized.encode("utf-8", "replace").decode("utf-8")
        # The 2-character floor guards against 1-letter Latin noise ("x"); that reasoning is
        # ASCII-shaped and doesn't apply to a single CJK character, which is already a selective
        # query, so the floor only applies while every character is ASCII.
        minimum = 2 if normalized.isascii() else 1
        if len(normalized) < minimum:
            return SearchResult(hits=[], state="ready")
        needle = normalized.encode("utf-8").lower()
        needle_text = needle.decode("utf-8")
        # What the LINE prefilter looks for, and why it is not the whole query. The prefilter
        # reads the raw JSON line; the real matcher further down reads the mapped text with its
        # whitespace collapsed to single spaces. A query spanning a line break - "milk eggs"
        # against a message that wrote those two words on separate lines - matches downstream
        # but never survives a prefilter that looks for a literal space, because the line holds
        # an escape sequence there instead. The longest whitespace-free run of the query cannot
        # make that mistake: it is a substring of the needle, so it never adds a hit, and it
        # contains no whitespace, so nothing can have rewritten it.
        probe_text = max(normalized.split(), key=len, default=normalized)
        probe = probe_text.encode("utf-8").lower()
        # Transcript lines are raw JSON text, where `"` and `\` are stored as `\"` and `\\`; an
        # unescaped probe alone can never match those bytes, so the line prefilter below also
        # checks this JSON-escaped form (the actual match, further down, is always against the
        # mapped, unescaped text, and against the whole needle — never against `escaped`).
        escaped = json.dumps(probe_text, ensure_ascii=False)[1:-1].encode("utf-8").lower()

        listing = self.list_sessions(clock=clock)
        if listing.state == "error":
            # Nothing was searched because nothing could be listed; say which, not "no matches".
            return SearchResult(hits=[], state="error", reason=listing.reason)
        eligible = [row for row in listing.sessions if not row.other]
        # One snapshot for every row: a listed session that is not in it is the vault's own.
        roots = self._recorded_roots() or {}

        hits: list[SearchHit] = []
        scanned = 0
        scanned_bytes = 0
        windowed = False
        unread = False  # a session the scan reached and could not open
        stopped: _SearchReason = ""
        for row in eligible:
            if len(hits) >= SEARCH_LIMIT:
                break  # a full page of hits is an answer, not a budget stopping the scan
            if cancelled is not None and cancelled():
                # The user has typed past this query: its hits are for a question they are no
                # longer asking, so they are dropped rather than painted over the newer ones.
                return SearchResult(
                    hits=[],
                    state="partial",
                    reason="cancelled",
                    scanned=scanned,
                    total=len(eligible),
                )
            if clock() - started >= SEARCH_DEADLINE_S:
                stopped = "time_budget"
                break
            recorded = roots.get(row.session_id)
            path = self._transcript(row.session_id, recorded)
            # The title hit comes from the row, which is already in memory. It is computed before
            # the file is touched so that a transcript the CLI pruned between the listing and now
            # costs the session its body hits and not its title match — the module rule says a
            # title containing the query is a hit, with no condition about the file.
            session_hits: list[SearchHit] = []
            if needle in row.title.encode("utf-8", "replace").lower():
                session_hits.append(
                    SearchHit(
                        session_id=row.session_id,
                        title=row.title,
                        role="title",
                        snippet=row.title,
                        at="",
                    )
                )
            stat = _stat_for_read(path)
            if isinstance(stat, str):
                # Gone or locked since the listing. Its title match stands; there is no file left
                # to search, none is counted as scanned, and the answer says a session went unread.
                unread = True
                hits.extend(session_hits)
                continue
            cost = _read_cost(stat.st_size)
            if scanned_bytes + cost > SEARCH_SCAN_BYTES:
                # The scan ends here, so this session is not searched at all and contributes
                # nothing — the same as one the deadline stopped us before reaching.
                stopped = "scan_budget"
                break
            scanned_bytes += cost
            remaining = HITS_PER_SESSION - len(session_hits)
            read = False
            if remaining > 0:
                try:
                    body_hits = self._search_transcript(
                        path,
                        stat.st_size,
                        row,
                        needle,
                        probe,
                        escaped,
                        needle_text,
                        remaining,
                        recorded,
                    )
                    fresh = path.stat()
                    if (fresh.st_size, fresh.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                        raise OSError("transcript changed during read")
                    session_hits += body_hits
                    read = True
                except OSError:
                    unread = True  # no hits from it, and no claim that it was searched
            if read:
                scanned += 1
                windowed = windowed or stat.st_size > FULL_READ_BYTES
            hits.extend(session_hits)

        # The reason that stopped the loop wins; a scan that ran to the end still reports the
        # largest thing it could not see. `unread` is weaker than either of these and than the
        # response cap, so _search_result resolves it last.
        reason: _SearchReason = stopped or (
            "file_budget" if windowed else ("listing_partial" if listing.state == "partial" else "")
        )
        return _search_result(hits[:SEARCH_LIMIT], reason, scanned, len(eligible), unread=unread)
