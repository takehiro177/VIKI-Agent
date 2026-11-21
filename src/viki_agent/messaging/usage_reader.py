"""Byte-budgeted reader for Claude Code transcript lines holding token usage.

Reads a `.jsonl` transcript from a byte `offset`, in whole lines only, turning each assistant
message that carries a `usage` block into a `Call`. `budget` can run far bigger than one window,
so a read loops window after window until `budget` bytes are consumed or the file ends, rather
than buffering the whole budget at once. At any moment a read holds one `READ_WINDOW_BYTES`
window, or one line longer than a window but within `MAX_LINE_BYTES`, read whole, with its
decoded text and parsed objects: about three times the line, and up to eight times for a line
of characters outside the Basic Multilingual Plane (decoded at four bytes each). So an untrusted
or still-growing transcript (the live log the vault's own transcripts are, per `catalog.py`)
cannot force an unbounded read: a line over `MAX_LINE_BYTES`, wherever it falls, is walked to
its end `_SCAN_CHUNK_BYTES` at a time and skipped instead of parsed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from viki_agent.automation.routines import stamp

MAX_LINE_BYTES = 32 * 1024 * 1024  # a line over this is skipped, never buffered whole
READ_WINDOW_BYTES = 8 * 1024 * 1024  # one window's memory bound; a read loops windows to `budget`
_SCAN_CHUNK_BYTES = 1024 * 1024  # the step used to find an overlong line's newline
_MAX_COUNT = (1 << 53) - 1  # the largest integer a browser's Number holds exactly
_PROMPT_LINE_LIMIT = 1024 * 1024

_MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_PROMPT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MODEL_RE = re.compile(r"[^\x00-\x1f\x7f-\x9f\ud800-\udfff]{1,256}")


@dataclass(frozen=True)
class Call:
    """One assistant message's token usage."""

    message_id: str
    prompt_id: str | None
    at: str
    model: str
    agent: bool
    input: int
    output: int
    cache_read: int
    cache_write: int


@dataclass(frozen=True)
class Read:
    """One `read_calls` result: the calls found, and where to resume."""

    calls: tuple[Call, ...]
    offset: int
    prompt_id: str | None
    done: bool
    skipped: int


def read_calls(path: Path, offset: int, budget: int, *, agent: bool, prompt_id: str | None) -> Read:
    """The `Call`s in `path` from byte `offset`, within `budget` bytes.

    Whole lines only: a final line with no newline yet stays unread, so a transcript still being
    written is never parsed mid-line. `done` is true once every complete line up to `offset` has
    been read — false means `budget` ran out first and a later call, at the returned `offset`,
    picks up where this one stopped. `agent` marks every call as agent work regardless of
    `isSidechain`; `prompt_id` seeds the prompt id a window starting mid-session would otherwise
    miss, and the result's `prompt_id` carries it to the next call. A repeated message id within
    one call merges: the max of each count, the earliest `at`, the first non-empty model and
    prompt id. A call whose four counts sum to 0 is dropped.

    Raises OSError: `path` cannot be opened or read.
    """
    calls: dict[str, Call] = {}
    skipped = 0
    pos = offset
    done = True
    with path.open("rb") as fh:
        consumed = 0
        while consumed < budget:
            fh.seek(offset + consumed)
            want = min(READ_WINDOW_BYTES, budget - consumed)
            window = fh.read(want)
            if not window:
                done = True  # EOF exactly at a line boundary, maybe after an earlier full window
                break
            exhausted = len(window) < want
            newline_at = window.rfind(b"\n")
            if newline_at != -1:
                complete_len = newline_at + 1
                for raw in _iter_window_lines(window, complete_len):
                    if len(raw) > MAX_LINE_BYTES:
                        skipped += 1
                        continue
                    prompt_id, bad = _process_line(
                        raw, agent=agent, prompt_id=prompt_id, calls=calls
                    )
                    skipped += bad
                consumed += complete_len
                pos = offset + consumed
                done = exhausted
                if exhausted:
                    break
                continue  # more of the file may remain: loop for another window
            if exhausted:
                done = True  # an unterminated tail at EOF stays wholly unread
                break
            # No newline anywhere in this window-sized read: this one line runs past `want`
            # bytes. Keep looking for its end without holding it all in memory, so an
            # attacker-sized line costs one bounded chunk at a time, not one allocation.
            length = len(window)
            found = False
            while True:
                fh.seek(offset + consumed + length)
                chunk = fh.read(_SCAN_CHUNK_BYTES)
                if not chunk:
                    break
                at = chunk.find(b"\n")
                if at != -1:
                    length += at
                    found = True
                    break
                length += len(chunk)
            if found:
                if length > MAX_LINE_BYTES:
                    skipped += 1
                else:
                    fh.seek(offset + consumed)
                    prompt_id, bad = _process_line(
                        fh.read(length), agent=agent, prompt_id=prompt_id, calls=calls
                    )
                    skipped += bad
                # Go on past this line: a run of long lines (a pasted image's base64, say) must
                # not cost one capture each. A line that crossed `budget` ends the loop not done.
                consumed += length + 1
                pos = offset + consumed
                done = False
                continue
            # Unterminated even at end of file: leave it wholly unread, `pos` at its last
            # complete-line boundary.
            done = True
            break
    final_calls = tuple(
        c for c in calls.values() if c.input + c.output + c.cache_read + c.cache_write
    )
    return Read(calls=final_calls, offset=pos, prompt_id=prompt_id, done=done, skipped=skipped)


def _iter_window_lines(window: bytes, end: int) -> Iterator[bytes]:
    """Each newline-terminated line of `window[:end]`, the newline stripped, found by index
    rather than by slicing out that whole prefix and splitting it — which would hold a second
    full-size copy alongside `window` (the reason `catalog._iter_lines` avoids it too)."""
    start = 0
    while start < end:
        newline = window.index(b"\n", start, end)
        yield window[start:newline]
        start = newline + 1


def _process_line(
    line: bytes, *, agent: bool, prompt_id: str | None, calls: dict[str, Call]
) -> tuple[str | None, int]:
    """Handle one complete line: maybe update the running prompt id, maybe merge in a parsed
    `Call`. Returns the (possibly updated) prompt id, and 1 if the line held both `"usage"` and
    `"assistant"` but failed to parse as a call, else 0."""
    line = line.rstrip(b"\r")
    if not line.strip():
        return prompt_id, 0
    if len(line) <= _PROMPT_LINE_LIMIT and b'"promptId"' in line:
        found = _extract_prompt_id(line)
        if found is not None:
            prompt_id = found
    if b'"usage"' in line and b'"assistant"' in line:
        call = _parse_call(line, agent=agent, prompt_id=prompt_id)
        if call is None:
            return prompt_id, 1
        _merge_call(calls, call)
    return prompt_id, 0


def _extract_prompt_id(line: bytes) -> str | None:
    try:
        obj = json.loads(line.decode("utf-8", errors="replace"))
    except ValueError, RecursionError:
        return None
    if not isinstance(obj, dict):
        return None
    value = obj.get("promptId")
    return value if isinstance(value, str) and _PROMPT_ID_RE.fullmatch(value) else None


def _parse_call(line: bytes, *, agent: bool, prompt_id: str | None) -> Call | None:
    try:
        obj = json.loads(line.decode("utf-8", errors="replace"))
    except ValueError, RecursionError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    message_id = message.get("id")
    if not isinstance(message_id, str) or not _MESSAGE_ID_RE.fullmatch(message_id):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = _count(usage, "input_tokens", required=True)
    output_tokens = _count(usage, "output_tokens", required=True)
    cache_read = _count(usage, "cache_read_input_tokens", required=False)
    cache_write = _count(usage, "cache_creation_input_tokens", required=False)
    if input_tokens is None or output_tokens is None or cache_read is None or cache_write is None:
        return None
    at = _parse_timestamp(obj.get("timestamp"))
    if at is None:
        return None
    model_raw = message.get("model")
    model = model_raw if isinstance(model_raw, str) and _MODEL_RE.fullmatch(model_raw) else ""
    return Call(
        message_id=message_id,
        prompt_id=prompt_id,
        at=at,
        model=model,
        agent=agent or obj.get("isSidechain") is True,
        input=input_tokens,
        output=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
    )


def _count(usage: dict[str, Any], key: str, *, required: bool) -> int | None:
    """`usage[key]` as a bounded, non-bool int; a missing key is 0 unless `required`."""
    if key not in usage:
        return None if required else 0
    value = usage[key]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_COUNT:
        return None
    return value


def _parse_timestamp(value: object) -> str | None:
    """`value` as the canonical `stamp()` form, or None unless it is an aware ISO time."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        # An offset at either end of the calendar ("0001-01-01T00:00:00+05:00") overflows the
        # conversion to UTC: skip the line rather than let one crafted entry stop every read.
        return stamp(parsed) if parsed.tzinfo is not None else None
    except ValueError, OverflowError:
        return None


def _merge_call(calls: dict[str, Call], call: Call) -> None:
    existing = calls.get(call.message_id)
    if existing is None:
        calls[call.message_id] = call
        return
    calls[call.message_id] = Call(
        message_id=call.message_id,
        prompt_id=existing.prompt_id or call.prompt_id,
        at=min(existing.at, call.at),
        model=existing.model or call.model,
        agent=existing.agent or call.agent,
        input=max(existing.input, call.input),
        output=max(existing.output, call.output),
        cache_read=max(existing.cache_read, call.cache_read),
        cache_write=max(existing.cache_write, call.cache_write),
    )
