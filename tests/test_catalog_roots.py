"""The catalog over recorded session roots: a session VIKI recorded in another folder
lists, searches and pages from that folder's project, and only such a session. Synthetic
transcripts in two project folders under tmp_path; the lookup is a dict standing in for
session_roots.SessionRoots."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from viki_agent import catalog as catalog_module
from viki_agent.catalog import Catalog, project_dir
from viki_agent.conversations.conversation_search import ConversationSearch
from viki_agent.events import ToolStarted

VAULT = Path("C:/Vault")
OTHER = Path("C:/Code/app")
ACTIVE = "aaaaaaaa-1111-4111-8111-111111111111"
RECORDED = "bbbbbbbb-2222-4222-8222-222222222222"
UNRECORDED = "cccccccc-3333-4333-8333-333333333333"


def _ts(n: int) -> str:
    return f"2026-10-03T00:00:{n:02d}.000Z"


def _uuid(seed: int, n: int) -> str:
    return f"{seed:08x}-{n:04x}-4000-8000-000000000000"


def _turn(seed: int, prompt: str, reply: str, tool_path: str) -> list[dict[str, Any]]:
    """A prompt, a Read tool call and its result, then a text reply."""
    ids = [_uuid(seed, n) for n in range(4)]
    tool_id = f"toolu_{seed}"
    base = {"isSidechain": False, "entrypoint": "cli"}
    return [
        {
            **base,
            "type": "user",
            "uuid": ids[0],
            "parentUuid": None,
            "timestamp": _ts(seed),
            "message": {"role": "user", "content": prompt},
        },
        {
            **base,
            "type": "assistant",
            "uuid": ids[1],
            "parentUuid": ids[0],
            "timestamp": _ts(seed),
            "message": {
                "id": ids[1],
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": "Read",
                        "input": {"file_path": tool_path},
                    }
                ],
            },
        },
        {
            **base,
            "type": "user",
            "uuid": ids[2],
            "parentUuid": ids[1],
            "timestamp": _ts(seed),
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}],
            },
        },
        {
            **base,
            "type": "assistant",
            "uuid": ids[3],
            "parentUuid": ids[2],
            "timestamp": _ts(seed),
            "message": {
                "id": ids[3],
                "role": "assistant",
                "content": [{"type": "text", "text": reply}],
            },
        },
    ]


def _write(home: Path, root: Path, session_id: str, lines: list[dict[str, Any]]) -> Path:
    folder = project_dir(home, root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    path.write_bytes("".join(json.dumps(line) + "\n" for line in lines).encode("utf-8"))
    return path


class Roots:
    """A RootLookup over a dict, counting its calls."""

    def __init__(self, roots: dict[str, Path]) -> None:
        self.roots = roots
        self.snapshots = 0

    def root_of(self, session_id: str) -> Path | None:
        return self.roots.get(session_id)

    def roots_for(self, session_ids: list[str]) -> Mapping[str, Path]:
        return {sid: self.roots[sid] for sid in session_ids if sid in self.roots}

    def transcript_roots(self) -> Mapping[str, Path]:
        self.snapshots += 1
        return dict(self.roots)


class BrokenRoots(Roots):
    def transcript_roots(self) -> Mapping[str, Path]:
        raise RuntimeError(f"store gone for {RECORDED} at {OTHER}")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    _write(home, VAULT, ACTIVE, _turn(1, "vault prompt needle", "vault reply", "C:/Vault/a.md"))
    _write(
        home,
        OTHER,
        RECORDED,
        _turn(2, "code prompt needle", "code reply needle", "C:/Code/app/src/main.py"),
    )
    _write(home, OTHER, UNRECORDED, _turn(3, "stray prompt needle", "stray reply", "x.py"))
    return home


def _bound(home: Path, roots: Roots | None = None) -> Catalog:
    catalog = Catalog(home, VAULT)
    catalog.bind_roots(roots or Roots({RECORDED: OTHER}))
    return catalog


def _ids(catalog: Catalog) -> list[str]:
    return [row.session_id for row in catalog.list_sessions().sessions]


def test_a_recorded_session_lists_from_its_own_project(home: Path) -> None:
    result = _bound(home).list_sessions()

    assert (result.state, result.omitted) == ("ready", 0)
    assert sorted(row.session_id for row in result.sessions) == [ACTIVE, RECORDED]


def test_the_active_rows_are_unchanged_and_never_listed_twice(home: Path) -> None:
    unbound = Catalog(home, VAULT).list_sessions().sessions
    # The active session recorded against the vault itself is the active project's own file.
    bound = _bound(home, Roots({RECORDED: OTHER, ACTIVE: VAULT})).list_sessions().sessions

    assert [row for row in bound if row.session_id == ACTIVE] == unbound
    assert sorted(row.session_id for row in bound) == [ACTIVE, RECORDED]


def test_unbound_gives_todays_results(home: Path) -> None:
    catalog = Catalog(home, VAULT)

    assert _ids(catalog) == [ACTIVE]
    assert not catalog.exists(RECORDED) and catalog.transcript_path(RECORDED) is None
    assert {hit.session_id for hit in catalog.search("needle")} == {ACTIVE}
    assert catalog.history(RECORDED).status == "missing"
    assert catalog.root_for(RECORDED) == VAULT


def test_an_unrecorded_transcript_of_another_folder_stays_invisible(home: Path) -> None:
    catalog = _bound(home)

    assert UNRECORDED not in _ids(catalog)
    assert not catalog.exists(UNRECORDED) and catalog.transcript_path(UNRECORDED) is None
    assert UNRECORDED not in {hit.session_id for hit in catalog.search("needle")}


def test_a_recorded_session_answers_exists_and_its_transcript_path(home: Path) -> None:
    catalog = _bound(home)

    assert catalog.exists(RECORDED)
    assert catalog.transcript_path(RECORDED) == project_dir(home, OTHER) / f"{RECORDED}.jsonl"
    assert catalog.root_for(RECORDED) == OTHER and catalog.root_for(ACTIVE) == VAULT


def test_a_recorded_session_searches_its_title_and_body(home: Path) -> None:
    roots = Roots({RECORDED: OTHER})
    catalog = _bound(home, roots)

    hits = [hit for hit in catalog.run_search("needle").hits if hit.session_id == RECORDED]

    assert [hit.role for hit in hits] == ["title", "user", "assistant"]
    assert hits[2].snippet == "code reply needle"
    # One snapshot for the listing and one for the search: never one per row.
    assert roots.snapshots == 2


def test_history_and_an_anchor_map_tool_paths_relative_to_the_recorded_root(home: Path) -> None:
    catalog = _bound(home)

    page = catalog.history(RECORDED)
    tools = [event.row.rest for event in page.events if isinstance(event, ToolStarted)]
    assert (page.status, tools) == ("complete", ["src/main.py"])

    service = ConversationSearch(catalog, lambda sid: (sid, None))
    [hit, *_] = service.search(RECORDED, "needle")["hits"]
    window = service.anchor(RECORDED, hit["anchor"])
    rests = [
        item["data"]["row"]["rest"] for item in window["events"] if item["type"] == "tool.started"
    ]
    assert (window["state"], rests) == ("ready", ["src/main.py"])


def test_a_recorded_sessions_cached_row_survives_the_next_listing(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _bound(home)
    first = catalog.list_sessions().sessions
    scanned: list[Path] = []
    original = catalog_module._row_lines  # noqa: SLF001 - own module, own test

    def counting(path: Path, size: int) -> Iterator[bytes]:
        scanned.append(path)
        return original(path, size)

    monkeypatch.setattr(catalog_module, "_row_lines", counting)

    assert catalog.list_sessions().sessions == first
    assert catalog.list_sessions().sessions == first
    assert scanned == []


def test_a_raising_lookup_answers_error_with_the_active_rows_and_logs_no_ids(
    home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    catalog = _bound(home, BrokenRoots({RECORDED: OTHER}))

    with caplog.at_level(logging.WARNING, logger="viki_agent.catalog"):
        result = catalog.list_sessions()

    assert (result.state, result.reason, result.omitted) == ("error", "read_failed", 0)
    assert [row.session_id for row in result.sessions] == [ACTIVE]

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert RECORDED not in record.getMessage() and str(OTHER) not in record.getMessage()


def test_a_raising_lookup_fails_the_search(home: Path) -> None:
    catalog = _bound(home, BrokenRoots({RECORDED: OTHER}))

    assert catalog.run_search("needle").state == "error"


def test_a_lookup_naming_no_canonical_id_is_ignored(home: Path) -> None:
    catalog = _bound(home, Roots({RECORDED: OTHER, "../../escape": OTHER}))

    assert sorted(_ids(catalog)) == [ACTIVE, RECORDED]


def _ticking(step: float) -> Callable[[], float]:
    now = 0.0

    def clock() -> float:
        nonlocal now
        now += step
        return now

    return clock


def test_a_deadline_mid_listing_is_partial_with_recorded_files_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    active = [_uuid(10 + i, 0) for i in range(2)]
    recorded = [_uuid(20 + i, 0) for i in range(3)]
    paths = [
        _write(home, VAULT, sid, _turn(10 + i, f"p{i}", "r", "a.md"))
        for i, sid in enumerate(active)
    ] + [
        _write(home, OTHER, sid, _turn(20 + i, f"q{i}", "r", "b.py"))
        for i, sid in enumerate(recorded)
    ]
    # The two newest files are recorded ones, so they are the two scanned before the deadline.
    for rank, index in enumerate([2, 3, 0, 4, 1]):
        stamp = 1_800_000_000 - rank * 100
        os.utime(paths[index], (stamp, stamp))
    monkeypatch.setattr(catalog_module, "LIST_DEADLINE_S", 0.05)
    catalog = _bound(home, Roots(dict.fromkeys(recorded, OTHER)))

    cut = catalog.list_sessions(clock=_ticking(0.02))

    assert (cut.state, cut.reason, cut.omitted) == ("partial", "time_budget", 3)
    assert {row.session_id for row in cut.sessions} == {recorded[0], recorded[1]}
    full = catalog.list_sessions(clock=lambda: 0.0)
    assert (full.state, len(full.sessions)) == ("ready", 5)
