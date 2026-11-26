"""Tests for the generated `starter-history.json` and `scripts/starter_history.py`.

AC-1: every `MANAGED` resource text at `STARTER_VERSION` is in the history and
`versions[STARTER_VERSION]` lists exactly `MANAGED`; a resource edit without regenerating fails.
AC-2: history integrity -- each key is its text's sha256, versions 4..`STARTER_VERSION` (the
public-era range, FX6) are contiguous, no `OWNER_FILES` target is recorded, every text passes
the starter's personal-string checks, and the generator's output is byte-identical on rerun.
FX5 N1 guard: `versions[STARTER_VERSION]` lists exactly one sha per target, so a resource edited
without a version bump (what happened to `.claude/skills/viki-agent/SKILL.md` at v7, commit
1b263798, caught only by a reviewer) fails here instead of silently shipping unnoticed.

`starter_history` lives in scripts/, outside the installed package, so this file follows
test_starter.py's sys.path convention to import it.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

import viki_agent
from tests.vaults.test_starter import PERSONAL_DATE, _static_problems
from viki_agent.vaults import starter

TESTS = Path(__file__).parents[1]
REPO = TESTS.parent
sys.path.insert(0, str(REPO / "scripts"))

import starter_history  # noqa: E402

RESOURCES = Path(viki_agent.__file__).parent / "resources"
HISTORY_PATH = RESOURCES / starter.HISTORY_FILE


def _history() -> starter.History:
    return starter.load_history(HISTORY_PATH)


def _current_text(target: str) -> str:
    """The current, unfilled source text a `MANAGED` target ships -- what the history should
    have recorded at `STARTER_VERSION`. A separate read path from the generator's own, so a
    monkeypatch of this one function proves the history test actually exercises current bytes."""
    return (RESOURCES / "starter" / starter.source_of(target)).read_text(encoding="utf-8")


def test_load_history_of_the_shipped_file_succeeds() -> None:
    history = _history()
    assert history.texts
    assert history.versions


def test_current_resources_are_in_the_history() -> None:
    history = _history()
    assert set(history.versions[starter.STARTER_VERSION]) == set(starter.MANAGED)
    for target in starter.MANAGED:
        sha = starter.text_sha(_current_text(target))
        assert sha in history.versions[starter.STARTER_VERSION][target], (
            f"{target} changed without regenerating scripts/starter_history.py"
        )


def test_every_history_text_passes_the_personal_string_checks() -> None:
    history = _history()
    for text in history.texts.values():
        assert _static_problems(text) == []
        assert PERSONAL_DATE.search(text) is None
        assert "japanese" not in text.lower()


def test_versions_are_contiguous_and_exclude_owner_targets() -> None:
    # FX6: the history now ships the public-era range only (v1-v3 were private-prototype only).
    history = _history()
    assert set(history.versions) == set(range(4, starter.STARTER_VERSION + 1))
    for per_target in history.versions.values():
        assert not set(per_target) & starter.OWNER_FILES


def test_every_text_key_is_its_own_sha256() -> None:
    # load_history already enforces this; re-asserted here as the integrity property AC-2 names.
    history = _history()
    for sha, text in history.texts.items():
        assert sha == starter.text_sha(text)


def test_rerun_on_a_copy_of_the_shipped_file_is_byte_identical(tmp_path: Path) -> None:
    output = tmp_path / starter.HISTORY_FILE
    shutil.copyfile(HISTORY_PATH, output)
    before = output.read_bytes()
    assert starter_history.main(["--output", str(output)]) == 0
    assert output.read_bytes() == before


# --- FX5 N1 guard: no target changes at STARTER_VERSION without a version bump --------------


def _assert_one_sha_per_target(history: starter.History) -> None:
    for target, shas in history.versions[starter.STARTER_VERSION].items():
        assert len(shas) == 1, (
            f"{target} changed at starter version {starter.STARTER_VERSION} without a version "
            f"bump: raise STARTER_VERSION in src/viki_agent/vaults/starter.py, then rerun "
            f"scripts/starter_history.py"
        )


def test_every_target_at_starter_version_has_exactly_one_sha() -> None:
    _assert_one_sha_per_target(_history())


@pytest.mark.parametrize("change", ["new target", "new text"])
def test_generator_refuses_to_change_a_shipped_version(tmp_path: Path, change: str) -> None:
    # R1 round 3 N4: a target added to FILES (here: missing from the shipped version) or a
    # text edited at the current version must not regenerate silently; it needs a bump.
    payload = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    current = payload["versions"][str(starter.STARTER_VERSION)]
    target = next(iter(current))
    if change == "new target":
        del current[target]
    else:
        other = "an older text\n"
        payload["texts"][starter.text_sha(other)] = other
        current[target] = [starter.text_sha(other)]
    output = tmp_path / starter.HISTORY_FILE
    body = json.dumps(payload)
    output.write_text(body, encoding="utf-8")

    assert starter_history.main(["--output", str(output)]) == 1
    assert output.read_text(encoding="utf-8") == body


def test_guard_fails_when_a_target_gets_a_second_sha_at_the_current_version(
    tmp_path: Path,
) -> None:
    # Fail-first: the shape commit 1b263798 actually produced at v7 -- one target carrying two
    # shas at the same version -- must trip the guard above, not pass silently.
    payload = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    version_key = str(starter.STARTER_VERSION)
    target = next(iter(payload["versions"][version_key]))
    extra_text = "a second, unbumped edit\n"
    extra_sha = starter.text_sha(extra_text)
    payload["texts"][extra_sha] = extra_text
    payload["versions"][version_key][target].append(extra_sha)
    output = tmp_path / starter.HISTORY_FILE
    output.write_text(json.dumps(payload), encoding="utf-8")

    mutated = starter.load_history(output)
    with pytest.raises(AssertionError):
        _assert_one_sha_per_target(mutated)
