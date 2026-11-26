"""Generate or refresh `src/viki_agent/resources/starter-history.json`.

    .venv/Scripts/python.exe scripts/starter_history.py [--refs REF ...] [--output PATH]

With `--refs`, walks `git log` for every commit that touched the starter resources or the
`starter.py` module across those refs, recording each managed target's text under the
`STARTER_VERSION` in force at that commit. Always appends the working tree's current
`STARTER_VERSION` and its `MANAGED` source texts last. Loads the existing output file first
(when present) and only ever adds shas: a rerun with no `--refs`, or with refs that add nothing
new, produces byte-identical output.

Public history only (ship-only-public-era decision): a starter resource and `starter.py` have
each lived at exactly one path across all of public history (`git log public/main -- <path>`
for both), so this walks that single root rather than any earlier, private-prototype naming.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from viki_agent import winproc
from viki_agent.vaults.starter import (
    MANAGED,
    OWNER_FILES,
    STARTER_VERSION,
    load_history,
    normal_text,
    source_of,
    text_sha,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO / "src" / "viki_agent" / "resources" / "starter-history.json"
STARTER_RESOURCES = REPO / "src" / "viki_agent" / "resources" / "starter"

# The one root a starter resource has ever lived under, and the one path `starter.py` has ever
# lived at, across all of public history.
RESOURCE_BASE = "src/viki_agent/resources/starter"
STARTER_PY = "src/viki_agent/vaults/starter.py"
_VERSION_LINE = re.compile(r"^STARTER_VERSION = (\d+)$", re.MULTILINE)

Versions = dict[int, dict[str, list[str]]]


def _commits(refs: Sequence[str]) -> list[str]:
    out = winproc.git(
        REPO,
        "log",
        "--reverse",
        "--date-order",
        "--format=%H",
        *refs,
        "--",
        RESOURCE_BASE,
        STARTER_PY,
    )
    return [line for line in out.splitlines() if line]


def _version_at(sha: str) -> int | None:
    try:
        shown = winproc.git(REPO, "show", f"{sha}:{STARTER_PY}")
    except RuntimeError:
        return None
    if match := _VERSION_LINE.search(shown):
        return int(match.group(1))
    return None


def _tree_paths(sha: str) -> list[str]:
    out = winproc.git(REPO, "ls-tree", "-r", "--name-only", sha, "--", RESOURCE_BASE)
    return [line for line in out.splitlines() if line]


def _blob_text(sha: str, path: str) -> str | None:
    """`path`'s text at `sha`, normalised, or None when it is not valid UTF-8. Every blob this
    walk has ever read under `RESOURCE_BASE` is plain UTF-8 text (checked against every blob
    public history ever recorded there), so `git show`'s own UTF-8 decode is exact here -- never
    lossy the way it would be for arbitrary binary content."""
    return normal_text(winproc.git(REPO, "show", f"{sha}:{path}").encode("utf-8"))


def target_of(path: str) -> str:
    """Inverse of `starter.source_of`: a resource-tree path to its `FILES` target."""
    prefix = RESOURCE_BASE + "/"
    if not path.startswith(prefix):
        raise ValueError(f"path outside the starter resource base: {path}")
    relative = path.removeprefix(prefix)
    if relative == "AGENTS.template.md":
        return "AGENTS.md"
    if relative == "CLAUDE.template.md":
        return "CLAUDE.md"
    if relative.startswith("dot-claude/"):
        return ".claude/" + relative.removeprefix("dot-claude/")
    return relative


def _record(
    texts: dict[str, str], versions: Versions, version: int, target: str, text: str
) -> None:
    if target in OWNER_FILES:
        return
    sha = text_sha(text)
    texts.setdefault(sha, text)
    shas = versions.setdefault(version, {}).setdefault(target, [])
    if sha not in shas:
        shas.append(sha)


def _from_history(refs: Sequence[str], texts: dict[str, str], versions: Versions) -> None:
    for sha in _commits(refs):
        version = _version_at(sha)
        if version is None:
            continue
        for path in _tree_paths(sha):
            target = target_of(path)
            text = _blob_text(sha, path)
            if text is None:
                continue
            _record(texts, versions, version, target, text)


def _from_worktree(texts: dict[str, str], versions: Versions) -> None:
    for target in MANAGED:
        text = (STARTER_RESOURCES / source_of(target)).read_text(encoding="utf-8")
        _record(texts, versions, STARTER_VERSION, target, text)


def _dump(texts: dict[str, str], versions: Versions) -> str:
    payload = {
        "schema": 1,
        "texts": {sha: texts[sha] for sha in sorted(texts)},
        "versions": {
            str(version): dict(sorted(versions[version].items())) for version in sorted(versions)
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refs", nargs="*", default=[], help="git refs to walk for history")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    if args.output.exists():
        history = load_history(args.output)
        texts: dict[str, str] = dict(history.texts)
        versions: Versions = {
            version: {target: list(shas) for target, shas in per_target.items()}
            for version, per_target in history.versions.items()
        }
    else:
        texts, versions = {}, {}

    # A version already in the file is closed: a new target or text there means a starter
    # file changed without a version bump, which current-version vaults would never be offered.
    shipped = {target: list(shas) for target, shas in versions.get(STARTER_VERSION, {}).items()}
    if args.refs:
        _from_history(args.refs, texts, versions)
    _from_worktree(texts, versions)
    if shipped and versions[STARTER_VERSION] != shipped:
        changed = sorted(
            target
            for target, shas in versions[STARTER_VERSION].items()
            if shipped.get(target) != shas
        )
        print(
            f"starter version {STARTER_VERSION} already shipped; changed: {', '.join(changed)}. "
            "Raise STARTER_VERSION in src/viki_agent/vaults/starter.py, then rerun.",
            file=sys.stderr,
        )
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_dump(texts, versions), encoding="utf-8", newline="\n")
    pairs = sum(len(per_target) for per_target in versions.values())
    print(
        f"wrote {args.output}: {len(texts)} texts, {len(versions)} versions, "
        f"{pairs} version/target pairs"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
