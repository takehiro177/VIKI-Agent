"""Tests for the P1 starter-update building blocks in `viki_agent.vaults.starter`: the
managed/owner file split, `History`/`load_history`, and the text and persona-block helpers a
starter update needs (`normal_text`, `text_sha`, `fill_vault_id`, `persona_split`, `rules_of`,
`splice`, `record_version`). `test_starter.py` keeps the render/fill/deny-list coverage; this
module is only the update-side additions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from viki_agent.vaults import starter

# --- file classification --------------------------------------------------------------------


def test_owner_files_are_exactly_section_4s_seven_targets() -> None:
    assert (
        frozenset(
            {
                "Welcome.md",
                "Home.md",
                "50-maps/Second Brain MOC.md",
                "system/log.md",
                "system/memory/user.md",
                "system/memory/preferences.md",
                "system/memory/lessons.md",
            }
        )
        == starter.OWNER_FILES
    )


def test_managed_is_files_minus_owner_files_in_files_order() -> None:
    assert (
        tuple(target for target in starter.FILES if target not in starter.OWNER_FILES)
        == starter.MANAGED
    )
    assert len(starter.MANAGED) == 32
    assert set(starter.MANAGED) & starter.FILLED == {"AGENTS.md", "CLAUDE.md"}
    assert set(starter.MANAGED).isdisjoint(starter.OWNER_FILES)


def test_manual_files_are_claude_md_and_settings() -> None:
    assert frozenset({"CLAUDE.md", ".claude/settings.json"}) == starter.MANUAL_FILES


def test_markers_and_filenames_are_the_named_literals() -> None:
    assert starter.HISTORY_FILE == "starter-history.json"
    assert starter.RECORD_FILE == "system/starter-updates.md"
    assert starter.PERSONA_OPEN == "<!-- viki:persona -->"
    assert starter.PERSONA_CLOSE == "<!-- /viki:persona -->"


# --- History ----------------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_history_shipped_text_at_and_targets() -> None:
    a1, a2, b1 = "a v1", "a v2", "b v1"
    history = starter.History(
        texts={_sha(a1): a1, _sha(a2): a2, _sha(b1): b1},
        versions={
            1: {"AGENTS.md": (_sha(a1),), "CLAUDE.md": (_sha(b1),)},
            2: {"AGENTS.md": (_sha(a1), _sha(a2))},
        },
    )
    assert history.shipped("AGENTS.md") == frozenset({_sha(a1), _sha(a2)})
    assert history.shipped("CLAUDE.md") == frozenset({_sha(b1)})
    assert history.shipped("nope.md") == frozenset()
    assert history.text_at(1, "AGENTS.md") == a1
    assert history.text_at(2, "AGENTS.md") == a2  # last sha of the version's tuple
    assert history.text_at(1, "CLAUDE.md") == b1
    assert history.text_at(2, "CLAUDE.md") is None  # version 2 never touched it
    assert history.text_at(3, "AGENTS.md") is None  # no such version
    assert history.targets() == {"AGENTS.md", "CLAUDE.md"}


# --- load_history: the happy path -------------------------------------------------------------


def test_load_history_parses_a_well_formed_file(tmp_path: Path) -> None:
    text = "hello starter"
    path = tmp_path / starter.HISTORY_FILE
    path.write_text(
        json.dumps(
            {"schema": 1, "texts": {_sha(text): text}, "versions": {"1": {"x.md": [_sha(text)]}}}
        ),
        encoding="utf-8",
    )
    history = starter.load_history(path)
    assert history.texts == {_sha(text): text}
    assert history.versions == {1: {"x.md": (_sha(text),)}}


# --- load_history: refusals ------------------------------------------------------------------


def _write_history(tmp_path: Path, payload: object, name: str = "h.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_history_rejects_a_text_whose_sha_does_not_match(tmp_path: Path) -> None:
    path = _write_history(tmp_path, {"schema": 1, "texts": {"a" * 64: "hello"}, "versions": {}})
    with pytest.raises(ValueError):
        starter.load_history(path)


def test_load_history_rejects_a_gap_in_the_version_numbering(tmp_path: Path) -> None:
    text = "t"
    path = _write_history(
        tmp_path,
        {
            "schema": 1,
            "texts": {_sha(text): text},
            "versions": {"1": {"x.md": [_sha(text)]}, "3": {"x.md": [_sha(text)]}},
        },
    )
    with pytest.raises(ValueError):
        starter.load_history(path)


def test_load_history_accepts_a_contiguous_range_starting_above_one(tmp_path: Path) -> None:
    # FX6: the shipped history now starts at version 4 (v1-v3 were private-prototype only), so
    # a range need not start at 1.
    text = "t"
    path = _write_history(
        tmp_path,
        {
            "schema": 1,
            "texts": {_sha(text): text},
            "versions": {"4": {"x.md": [_sha(text)]}, "5": {"x.md": [_sha(text)]}},
        },
    )
    history = starter.load_history(path)
    assert set(history.versions) == {4, 5}


def test_load_history_rejects_a_gap_in_a_range_starting_above_one(tmp_path: Path) -> None:
    text = "t"
    path = _write_history(
        tmp_path,
        {
            "schema": 1,
            "texts": {_sha(text): text},
            "versions": {"4": {"x.md": [_sha(text)]}, "6": {"x.md": [_sha(text)]}},
        },
    )
    with pytest.raises(ValueError):
        starter.load_history(path)


def test_load_history_rejects_empty_versions(tmp_path: Path) -> None:
    path = _write_history(tmp_path, {"schema": 1, "texts": {}, "versions": {}})
    with pytest.raises(ValueError):
        starter.load_history(path)


def test_load_history_rejects_an_owner_target(tmp_path: Path) -> None:
    text = "t"
    path = _write_history(
        tmp_path,
        {"schema": 1, "texts": {_sha(text): text}, "versions": {"1": {"Home.md": [_sha(text)]}}},
    )
    with pytest.raises(ValueError):
        starter.load_history(path)


def test_load_history_rejects_an_unknown_top_level_key(tmp_path: Path) -> None:
    path = _write_history(tmp_path, {"schema": 1, "texts": {}, "versions": {}, "extra": 1})
    with pytest.raises(ValueError):
        starter.load_history(path)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 2, "texts": {}, "versions": {}},  # wrong schema
        {"texts": {}, "versions": {}},  # missing schema key entirely
        [],  # not an object at all
        {"schema": 1, "texts": {}, "versions": {"1": {"x.md": []}}},  # empty sha list
        # duplicate shas in one target's list
        {
            "schema": 1,
            "texts": {_sha("t"): "t"},
            "versions": {"1": {"x.md": [_sha("t"), _sha("t")]}},
        },
        # a sha the target list names but texts never defines
        {"schema": 1, "texts": {_sha("t"): "t"}, "versions": {"1": {"x.md": ["b" * 64]}}},
    ],
)
def test_load_history_rejects_other_malformed_shapes(tmp_path: Path, payload: object) -> None:
    path = _write_history(tmp_path, payload)
    with pytest.raises(ValueError):
        starter.load_history(path)


# --- normal_text --------------------------------------------------------------------------


def test_normal_text_decodes_strict_utf8() -> None:
    assert starter.normal_text(b"plain text") == "plain text"


def test_normal_text_drops_one_leading_bom() -> None:
    assert starter.normal_text(chr(0xFEFF).encode("utf-8") + b"body") == "body"


def test_normal_text_normalises_crlf_to_lf() -> None:
    assert starter.normal_text(b"line1\r\nline2\r\n") == "line1\nline2\n"


def test_normal_text_returns_none_on_invalid_utf8() -> None:
    assert starter.normal_text(b"\xff\xfe\x00\x01") is None


# --- text_sha / fill_vault_id ---------------------------------------------------------------


def test_text_sha_is_sha256_hex_of_utf8_bytes() -> None:
    assert starter.text_sha("abc") == hashlib.sha256(b"abc").hexdigest()


def test_fill_vault_id_replaces_only_that_token() -> None:
    text = "id={{viki.vault_id}} name={{viki.assistant_name}} id again={{viki.vault_id}}"
    assert starter.fill_vault_id(text, "V-1") == (
        "id=V-1 name={{viki.assistant_name}} id again=V-1"
    )


# --- persona_split / rules_of / splice -----------------------------------------------------


def _persona_text(
    open_line: str = starter.PERSONA_OPEN, close_line: str = starter.PERSONA_CLOSE
) -> str:
    return "\n".join(["before", open_line, "body line", close_line, "after"])


def test_persona_split_returns_before_block_and_after() -> None:
    text = _persona_text()
    assert starter.persona_split(text) == (
        ["before"],
        [starter.PERSONA_OPEN, "body line", starter.PERSONA_CLOSE],
        ["after"],
    )


def test_persona_split_tolerates_trailing_whitespace_on_a_marker() -> None:
    text = _persona_text(
        open_line=starter.PERSONA_OPEN + "  ", close_line=starter.PERSONA_CLOSE + "\t"
    )
    split = starter.persona_split(text)
    assert split is not None
    assert split[1][0] == starter.PERSONA_OPEN + "  "
    assert split[1][-1] == starter.PERSONA_CLOSE + "\t"


def test_persona_split_rejects_a_marker_not_flush_with_line_start() -> None:
    text = _persona_text(open_line=" " + starter.PERSONA_OPEN)
    assert starter.persona_split(text) is None


def test_persona_split_rejects_two_open_markers() -> None:
    text = "\n".join([starter.PERSONA_OPEN, starter.PERSONA_OPEN, "body", starter.PERSONA_CLOSE])
    assert starter.persona_split(text) is None


def test_persona_split_rejects_two_close_markers() -> None:
    text = "\n".join([starter.PERSONA_OPEN, "body", starter.PERSONA_CLOSE, starter.PERSONA_CLOSE])
    assert starter.persona_split(text) is None


def test_persona_split_rejects_close_before_open() -> None:
    text = "\n".join([starter.PERSONA_CLOSE, "body", starter.PERSONA_OPEN])
    assert starter.persona_split(text) is None


def test_persona_split_rejects_text_with_no_markers() -> None:
    assert starter.persona_split("just plain prose, no markers at all") is None


def test_rules_of_collapses_the_persona_block_to_one_sentinel_line() -> None:
    text = _persona_text()
    assert starter.rules_of(text) == "before\n\x00\nafter"


def test_rules_of_makes_two_texts_that_differ_only_inside_the_block_equal() -> None:
    other_body = "\n".join(
        ["before", starter.PERSONA_OPEN, "a wildly different body", starter.PERSONA_CLOSE, "after"]
    )
    assert starter.rules_of(_persona_text()) == starter.rules_of(other_body)


def test_rules_of_returns_none_without_a_valid_marker_pair() -> None:
    assert starter.rules_of("no markers here") is None


def test_splice_replaces_the_persona_block_in_place() -> None:
    text = _persona_text()
    replaced = starter.splice(text, [starter.PERSONA_OPEN, "new body", starter.PERSONA_CLOSE])
    assert (
        replaced
        == "before\n" + starter.PERSONA_OPEN + "\nnew body\n" + starter.PERSONA_CLOSE + "\nafter"
    )


def test_splice_returns_none_without_a_valid_marker_pair_in_the_template() -> None:
    assert starter.splice("no markers here", [starter.PERSONA_OPEN, starter.PERSONA_CLOSE]) is None


# --- record_version -------------------------------------------------------------------------


def test_record_version_reads_the_frontmatter_field() -> None:
    text = "---\nstarter_version: 7\nother: 1\n---\nbody"
    assert starter.record_version(text) == 7


def test_record_version_tolerates_trailing_whitespace() -> None:
    text = "---\nstarter_version: 7   \n---\n"
    assert starter.record_version(text) == 7


def test_record_version_none_without_an_opening_dashes_line() -> None:
    assert starter.record_version("starter_version: 7\n---\n") is None


def test_record_version_none_without_a_closing_dashes_line() -> None:
    assert starter.record_version("---\nstarter_version: 7\nbody with no close") is None


def test_record_version_none_when_closing_dashes_is_past_40_lines() -> None:
    too_far = "\n".join(
        ["---", "starter_version: 7"] + [f"filler{i}: 1" for i in range(40)] + ["---"]
    )
    within_bounds = "\n".join(
        ["---", "starter_version: 7"] + [f"filler{i}: 1" for i in range(38)] + ["---"]
    )
    assert starter.record_version(too_far) is None
    assert starter.record_version(within_bounds) == 7


def test_record_version_none_on_a_malformed_version_line() -> None:
    assert starter.record_version("---\nstarter_version:7\n---\n") is None  # missing space
    assert starter.record_version("---\nstarter_version: 07\n---\n") is None  # leading zero
    assert starter.record_version("---\nstarter_version: abc\n---\n") is None  # not a number
    assert starter.record_version("---\nno version field here\n---\n") is None


def test_record_version_none_on_a_duplicated_field() -> None:
    text = "---\nstarter_version: 7\nstarter_version: 8\n---\n"
    assert starter.record_version(text) is None
