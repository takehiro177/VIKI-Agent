"""`read_calls`'s budget, merge, skip and prompt-id rules, checked against the real
background-agent fixtures (tests/fixtures/transcripts and tests/fixtures/claude) and synthetic
transcripts."""

from __future__ import annotations

import json
import random
import tracemalloc
from pathlib import Path

import pytest
from synthetic_transcripts import session_id, write_transcript

from viki_agent.messaging import usage_reader
from viki_agent.messaging.usage_reader import Call, read_calls

FIXTURES = Path(__file__).parents[1] / "fixtures"
TRANSCRIPT_FIXTURE = FIXTURES / "transcripts" / "background-agent.jsonl"
STREAM_FIXTURE = FIXTURES / "claude" / "background-agent.jsonl"


def _read_all(path: Path, *, agent: bool = False, budget: int = 1 << 20) -> list[Call]:
    """Every call in `path`, looping `read_calls` until it reports done."""
    calls: list[Call] = []
    offset = 0
    prompt_id: str | None = None
    while True:
        result = read_calls(path, offset, budget, agent=agent, prompt_id=prompt_id)
        calls.extend(result.calls)
        offset, prompt_id = result.offset, result.prompt_id
        if result.done:
            return calls


def _stream_model_usage(path: Path, model: str) -> dict[str, object]:
    """The named model's totals from the stream fixture's last `result` frame."""
    usage: dict[str, object] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        frame = json.loads(line)["frame"]
        if frame.get("type") == "result" and model in frame.get("modelUsage", {}):
            usage = frame["modelUsage"][model]
    assert usage is not None, f"no result frame named {model}"
    return usage


def _usage_line(i: int) -> str:
    """A minimal assistant/usage line `read_calls` accepts, token counts scaled by `i` (>= 1 so
    its four counts never sum to 0)."""
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": False,
            "timestamp": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}.000Z",
            "message": {
                "id": f"msg_{i:06d}",
                "model": "claude-sonnet-5",
                "usage": {
                    "input_tokens": i,
                    "output_tokens": i * 2,
                    "cache_read_input_tokens": i * 3,
                    "cache_creation_input_tokens": i * 4,
                },
            },
        }
    )


def _prompt_line(prompt_id: str) -> str:
    return json.dumps({"type": "user", "promptId": prompt_id})


def test_fixture_totals_match_the_stream_fixtures_model_usage() -> None:
    """The real transcript's calls (isSidechain: false throughout, so the main model only) sum
    to exactly the stream fixture's last, cumulative `claude-sonnet-5` usage."""
    calls = _read_all(TRANSCRIPT_FIXTURE)
    usage = _stream_model_usage(STREAM_FIXTURE, "claude-sonnet-5")

    assert sum(c.input for c in calls) == usage["inputTokens"]
    assert sum(c.output for c in calls) == usage["outputTokens"]
    assert sum(c.cache_read for c in calls) == usage["cacheReadInputTokens"]
    assert sum(c.cache_write for c in calls) == usage["cacheCreationInputTokens"]


def test_a_copied_line_with_a_higher_output_merges_to_the_max(tmp_path: Path) -> None:
    """A duplicate of a real call, output bumped up, merges into one call with the max of each
    count — the other fields (input, cache_read, cache_write) are untouched by the duplicate, so
    the max is just the original value for them."""
    lines = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8").splitlines()
    target = next(line for line in lines if "msg_011CewpACLxTHSYWLPLiRodv" in line)
    assert '"output_tokens": 5' in target
    bumped = target.replace('"output_tokens": 5', '"output_tokens": 999')
    path = tmp_path / "dup.jsonl"
    path.write_text("\n".join([*lines, bumped]) + "\n", encoding="utf-8", newline="\n")

    calls = _read_all(path)
    merged = next(c for c in calls if c.message_id == "msg_011CewpACLxTHSYWLPLiRodv")
    assert merged.output == 999
    assert merged.input == 2
    assert merged.cache_read == 60569
    assert merged.cache_write == 6644


@pytest.mark.parametrize(
    "timestamp", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00", "2026-01-01T00:00:00"]
)
def test_a_timestamp_with_no_utc_form_skips_only_its_line(tmp_path: Path, timestamp: str) -> None:
    """An offset at either end of the calendar overflows the conversion to UTC, and a naive time
    has none: that line is skipped and counted, and the next call still reads."""
    bad = json.loads(_usage_line(1))
    bad["timestamp"] = timestamp
    path = tmp_path / "edge.jsonl"
    path.write_text(json.dumps(bad) + "\n" + _usage_line(2) + "\n", encoding="utf-8", newline="\n")

    result = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)

    assert [call.message_id for call in result.calls] == ["msg_000002"]
    assert result.skipped == 1


def test_a_partial_final_line_is_read_once_completed(tmp_path: Path) -> None:
    """A transcript whose last line has no newline yet (still being written) yields that call
    only once the line is completed and re-read from the offset the first read returned."""
    lines = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8").splitlines()
    whole = "\n".join(lines[:-1]) + "\n"
    last = lines[-1]
    path = tmp_path / "partial.jsonl"
    path.write_text(whole + last[: len(last) // 2], encoding="utf-8", newline="\n")

    first = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)
    assert first.done is True
    assert all(c.message_id != "msg_011CewpB256ziPc2EzLiPaTK" for c in first.calls)

    path.write_text(whole + last + "\n", encoding="utf-8", newline="\n")
    finished = read_calls(path, first.offset, 1 << 20, agent=False, prompt_id=first.prompt_id)
    assert any(c.message_id == "msg_011CewpB256ziPc2EzLiPaTK" for c in finished.calls)
    assert finished.done is True


def test_small_budgets_resume_to_the_same_calls(tmp_path: Path) -> None:
    """Reading the whole file in one budget and reading it through many 1 KiB budgets, each
    resuming from the last offset and prompt id, find the same calls in the same order."""
    path = tmp_path / "many.jsonl"
    lines = []
    for i in range(1, 61):
        lines.append(_prompt_line(f"prompt-{i:04d}"))
        lines.append(_usage_line(i))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    whole = _read_all(path, budget=1 << 20)
    chunked = _read_all(path, budget=1024)

    assert len(whole) == 60
    assert chunked == whole


def test_an_oversized_line_is_skipped_and_the_next_call_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With MAX_LINE_BYTES patched down, a 2 MiB line is skipped (counted, never parsed) and the
    ordinary call right after it is still read."""
    monkeypatch.setattr(usage_reader, "MAX_LINE_BYTES", 1 << 20)
    huge = json.dumps({"type": "user", "padding": "x" * (2 * 1024 * 1024)})
    path = tmp_path / "oversized.jsonl"
    path.write_text(huge + "\n" + _usage_line(1) + "\n", encoding="utf-8", newline="\n")

    calls: list[Call] = []
    skipped = 0
    offset = 0
    prompt_id: str | None = None
    while True:
        result = read_calls(path, offset, 4096, agent=False, prompt_id=prompt_id)
        calls.extend(result.calls)
        skipped += result.skipped
        offset, prompt_id = result.offset, result.prompt_id
        if result.done:
            break

    assert skipped == 1
    assert [c.message_id for c in calls] == ["msg_000001"]


def test_fixture_lines_spliced_into_a_synthetic_transcript_read_the_same_calls(
    tmp_path: Path,
) -> None:
    """The real fixture's calls still come out the same when appended after a `write_transcript`
    file's own turns, none of which carry a `usage` block."""
    written = write_transcript(tmp_path, session_id(1), target_bytes=4096, rng=random.Random(1))
    fixture_lines = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8").splitlines()
    with written.path.open("a", encoding="utf-8", newline="\n") as fh:
        for line in fixture_lines:
            fh.write(line + "\n")

    spliced = {c.message_id: c for c in _read_all(written.path)}
    expected = {c.message_id: c for c in _read_all(TRANSCRIPT_FIXTURE)}
    assert expected  # the fixture itself still yields calls
    for message_id, call in expected.items():
        assert spliced[message_id] == call


def test_a_lone_surrogate_model_reads_as_empty_and_the_call_is_kept(tmp_path: Path) -> None:
    """A model carrying a lone surrogate (a JSON escape with no valid pairing) cannot be bound
    by the store once written; `_MODEL_RE` now refuses it too, so the model reads as "" and the
    call itself is still kept."""
    line = _usage_line(1).replace('"claude-sonnet-5"', r'"\ud800"')
    path = tmp_path / "surrogate_model.jsonl"
    path.write_text(line + "\n", encoding="utf-8", newline="\n")

    result = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)

    assert [c.model for c in result.calls] == [""]
    assert result.skipped == 0


def test_a_surrogate_message_id_or_prompt_id_is_refused_as_today(tmp_path: Path) -> None:
    """`_MESSAGE_ID_RE` and `_PROMPT_ID_RE` are ASCII-only patterns, so a lone surrogate never
    matches them: a call whose id carries one is refused like any other bad id, and a promptId
    carrying one never updates the running prompt id."""
    bad_id = _usage_line(1).replace('"msg_000001"', r'"\ud800"')
    path = tmp_path / "surrogate_id.jsonl"
    path.write_text(bad_id + "\n", encoding="utf-8", newline="\n")

    result = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)

    assert result.calls == ()
    assert result.skipped == 1

    bad_prompt = _prompt_line("x").replace('"x"', r'"\ud800"')
    path2 = tmp_path / "surrogate_prompt.jsonl"
    path2.write_text(bad_prompt + "\n" + _usage_line(2) + "\n", encoding="utf-8", newline="\n")

    result2 = read_calls(path2, 0, 1 << 20, agent=False, prompt_id=None)

    assert [c.prompt_id for c in result2.calls] == [None]


@pytest.mark.parametrize("budget", [8 * 1024 * 1024, 4096])
def test_an_oversized_assistant_line_within_the_window_is_skipped_and_the_next_call_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int
) -> None:
    """A line over MAX_LINE_BYTES that still fits inside the read window must be skipped like
    any other oversized line — AC-3 held only for a line longer than the remaining budget. This
    one is an otherwise-valid assistant/usage line, so it used to be parsed instead of skipped."""
    monkeypatch.setattr(usage_reader, "MAX_LINE_BYTES", 1 << 20)
    obj = json.loads(_usage_line(1))
    obj["message"]["padding"] = "x" * (2 * 1024 * 1024)
    huge = json.dumps(obj)
    path = tmp_path / "huge_assistant.jsonl"
    path.write_text(huge + "\n" + _usage_line(2) + "\n", encoding="utf-8", newline="\n")

    calls: list[Call] = []
    skipped = 0
    offset = 0
    prompt_id: str | None = None
    while True:
        result = read_calls(path, offset, budget, agent=False, prompt_id=prompt_id)
        calls.extend(result.calls)
        skipped += result.skipped
        offset, prompt_id = result.offset, result.prompt_id
        if result.done:
            break

    assert skipped == 1
    assert [c.message_id for c in calls] == ["msg_000002"]


def test_small_read_windows_match_a_large_window_within_a_bounded_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With READ_WINDOW_BYTES patched down, a budget bigger than the window forces several
    windows inside one call; the calls found match a single large-window read exactly, and the
    small-window read's traced peak stays bounded by the window, not by the size of the file. A
    synthetic transcript of ordinary turns carries no `usage` block, so the few real calls
    appended after it are the read's only output — what grows here is the file, not the result,
    which is the form the window bound (not the dict of merged calls) has to hold up against."""
    written = write_transcript(
        tmp_path, session_id(1), target_bytes=1_000_000, rng=random.Random(2), filler=(10, 30)
    )
    with written.path.open("a", encoding="utf-8", newline="\n") as fh:
        for i in range(1, 6):
            fh.write(_usage_line(i) + "\n")
    path = written.path
    budget = path.stat().st_size + 1024
    longest_line = max(len(line) for line in path.read_bytes().split(b"\n") if line)

    large_window = read_calls(path, 0, budget, agent=False, prompt_id=None)
    assert large_window.done is True
    assert len(large_window.calls) == 5

    monkeypatch.setattr(usage_reader, "READ_WINDOW_BYTES", 64 * 1024)
    tracemalloc.start()
    try:
        small_window = read_calls(path, 0, budget, agent=False, prompt_id=None)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert small_window == large_window
    # +4096: fixed overhead that does not scale with the window or the file (the handful of
    # merged Call objects, dict/tuple bookkeeping, bytes-object headers); everything that does
    # scale with size is covered by the window term and the one longest line.
    assert peak < 4 * 64 * 1024 + longest_line + 4096


def test_a_later_duplicate_with_a_lower_count_keeps_the_earlier_higher_one(
    tmp_path: Path,
) -> None:
    """A later line that repeats a message id with a LOWER output count must not win: the merge
    takes the max of each count, not whichever line came last. (The fixture test right above
    this one can't tell "max" apart from "always keep the later value," since there the later
    value is also the higher one.)"""
    lines = TRANSCRIPT_FIXTURE.read_text(encoding="utf-8").splitlines()
    target = next(line for line in lines if "msg_011CewpACLxTHSYWLPLiRodv" in line)
    assert '"output_tokens": 5' in target
    lowered = target.replace('"output_tokens": 5', '"output_tokens": 1')
    path = tmp_path / "dup_lower.jsonl"
    path.write_text("\n".join([*lines, lowered]) + "\n", encoding="utf-8", newline="\n")

    calls = _read_all(path)
    merged = next(c for c in calls if c.message_id == "msg_011CewpACLxTHSYWLPLiRodv")
    assert merged.output == 5


@pytest.mark.parametrize(
    "field",
    ["input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"],
)
@pytest.mark.parametrize("bad_value", [True, -1, 1 << 53])
def test_a_count_outside_its_bool_and_range_check_is_refused(
    tmp_path: Path, field: str, bad_value: object
) -> None:
    """`_count` refuses a bool (an int subclass otherwise indistinguishable from 1), a negative
    count, and a count at 2**53 (one past the largest integer a browser's Number holds exactly)
    — for each of the four usage fields. The line is skipped and the next good call still reads."""
    bad = json.loads(_usage_line(1))
    bad["message"]["usage"][field] = bad_value
    path = tmp_path / "bad_count.jsonl"
    path.write_text(json.dumps(bad) + "\n" + _usage_line(2) + "\n", encoding="utf-8", newline="\n")

    result = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)

    assert [c.message_id for c in result.calls] == ["msg_000002"]
    assert result.skipped == 1


def test_a_message_id_over_128_characters_is_refused(tmp_path: Path) -> None:
    """`_MESSAGE_ID_RE` bounds an id to 128 characters; one more is refused."""
    bad = json.loads(_usage_line(1))
    bad["message"]["id"] = "m" * 129
    path = tmp_path / "long_id.jsonl"
    path.write_text(json.dumps(bad) + "\n" + _usage_line(2) + "\n", encoding="utf-8", newline="\n")

    result = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)

    assert [c.message_id for c in result.calls] == ["msg_000002"]
    assert result.skipped == 1


def test_a_message_id_with_a_disallowed_character_is_refused(tmp_path: Path) -> None:
    """`_MESSAGE_ID_RE` allows only `[A-Za-z0-9_-]`; a space is refused."""
    bad = json.loads(_usage_line(1))
    bad["message"]["id"] = "msg 000001"
    path = tmp_path / "bad_char_id.jsonl"
    path.write_text(json.dumps(bad) + "\n" + _usage_line(2) + "\n", encoding="utf-8", newline="\n")

    result = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)

    assert [c.message_id for c in result.calls] == ["msg_000002"]
    assert result.skipped == 1


@pytest.mark.parametrize("tail", ["none", "partial", "longer than a window"])
def test_a_read_that_meets_the_end_of_the_file_in_a_later_window_is_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tail: str
) -> None:
    """The first window holds exactly the complete lines, so the read goes on to a second one
    that holds nothing, a partial last line, or the start of an unterminated line longer than a
    window: every complete line was read, so it is done, with the offset after the last
    complete line."""
    text = "".join(f"{_usage_line(i)}\n" for i in range(1, 40)).encode()
    monkeypatch.setattr(usage_reader, "READ_WINDOW_BYTES", len(text))
    tails = {"none": b"", "partial": b'{"type": "assistant"', "longer than a window": b"x" * 20_000}
    assert len(tails["longer than a window"]) > len(text)
    path = tmp_path / "window_end.jsonl"
    path.write_bytes(text + tails[tail])

    result = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)

    assert (result.done, result.offset, len(result.calls)) == (True, len(text), 39)


def test_lines_longer_than_a_window_do_not_end_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A line longer than one window but within MAX_LINE_BYTES is read whole, and the read goes
    on to the next line: one read at a large budget returns every call and is done."""
    monkeypatch.setattr(usage_reader, "READ_WINDOW_BYTES", 512)
    filler = json.dumps({"type": "user", "message": {"content": "x" * 2000}})
    lines = [line for i in range(1, 7) for line in (filler, _usage_line(i))]
    path = tmp_path / "long_lines.jsonl"
    path.write_bytes(("\n".join(lines) + "\n").encode())

    result = read_calls(path, 0, 1 << 20, agent=False, prompt_id=None)

    assert (result.done, len(result.calls), result.offset) == (True, 6, path.stat().st_size)
