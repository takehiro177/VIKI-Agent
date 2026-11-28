"""Check the release version and stage the release assets for `.github/workflows/release.yml`.

Stdlib only. `version [--tag vX.Y.Z]` prints the app version when `pyproject.toml`,
`desktop/package.json` and `frontend/package.json` hold the same `X.Y.Z` (and the tag is that
version after a `v`); otherwise it prints each mismatch to stderr and exits 1.

`stage --version V --windows D --macos D --linux D --out D` copies exactly each OS's allowlisted
`dist/desktop` files (`allowlist`) from the three build folders into a new or empty `--out`,
renaming each SBOM to `sbom-<part>-<os>.cdx.json`; every other file is ignored. It refuses
(exit 1, one stderr line per problem, nothing copied) on a missing required file, an update feed
(`latest*.yml`) without exactly one `version: V` line or with a `url:` or `path:` line that does
not name one of that OS's staged installers, or a `SHA256SUMS-<os>.txt` that does not list
exactly that OS's installers with their SHA-256 digests. The feed is read line by line with
exact regexes rather than parsed as YAML: a line naming a `url` or `path` key, plain or quoted,
must be the plain form with a staged name. Usage errors exit 2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

INSTALLERS = {
    "windows": ("viki-agent-windows-x64-setup.exe",),
    "macos": ("viki-agent-macos-arm64.dmg", "viki-agent-macos-arm64.zip"),
    "linux": ("viki-agent-linux-amd64.deb",),
}
FEEDS = {"windows": "latest.yml", "macos": "latest-mac.yml", "linux": "latest-linux.yml"}
SBOM_PARTS = ("frontend", "desktop", "python", "magpie")

_VERSION = re.compile(r"\d+\.\d+\.\d+")
_FEED_VERSION_KEY = re.compile(r"\s*version\s*:")
# Any line that mentions a url or path key, in any YAML spelling, must be the plain form below.
_FEED_REF_KEY = re.compile(r"(?<![\w-])[\"']?(?:url|path)[\"']?\s*:")
_FEED_REF = re.compile(r" *(?:- )?(?:url|path): (?P<name>\S+)")
_SUM = re.compile(r"(?P<hex>[0-9a-f]{64})  (?P<name>\S+)")


def read_versions(repo: Path) -> dict[str, str]:
    """Each version field's file (repo-relative) to its value, `pyproject.toml` first."""
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    found = {"pyproject.toml": pyproject["project"]["version"]}
    for name in ("desktop/package.json", "frontend/package.json"):
        found[name] = json.loads((repo / name).read_text(encoding="utf-8"))["version"]
    return found


def version_problems(found: dict[str, str], tag: str | None) -> list[str]:
    version = found["pyproject.toml"]
    problems = [
        f"{name} is {value}, pyproject.toml is {version}"
        for name, value in found.items()
        if value != version
    ]
    if not _VERSION.fullmatch(version):
        problems.append(f"pyproject.toml version {version} is not X.Y.Z")
    if tag is not None and tag != f"v{version}":
        problems.append(f"tag {tag} is not v{version}")
    return problems


def allowlist(os_name: str) -> tuple[dict[str, str], dict[str, str]]:
    """(required, optional) build-folder names of `os_name`'s release files, each to the name it
    is staged under."""
    names = [*INSTALLERS[os_name], FEEDS[os_name]]
    names += [f"SHA256SUMS-{os_name}.txt", f"THIRD-PARTY-NOTICES-{os_name}.txt"]
    required = {name: name for name in names}
    for part in SBOM_PARTS:
        required[f"sbom-{part}.cdx.json"] = f"sbom-{part}-{os_name}.cdx.json"
    optional = {f"{name}.blockmap": f"{name}.blockmap" for name in INSTALLERS[os_name]}
    return required, optional


def feed_problems(text: str, version: str, installers: tuple[str, ...]) -> list[str]:
    lines = text.splitlines()
    problems = []
    if [line for line in lines if _FEED_VERSION_KEY.match(line)] != [f"version: {version}"]:
        problems.append(f'has no single "version: {version}" line')
    for line in lines:
        if _FEED_REF_KEY.search(line):
            match = _FEED_REF.fullmatch(line)
            if match is None or match["name"] not in installers:
                problems.append(f"line {line!r} names no staged installer")
    return problems


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checksum_problems(text: str, folder: Path, installers: tuple[str, ...]) -> list[str]:
    matches = [_SUM.fullmatch(line) for line in text.splitlines()]
    if None in matches:
        return ["has a line that is not '<sha256>  <name>'"]
    listed = {match["name"]: match["hex"] for match in matches if match}
    if sorted(match["name"] for match in matches if match) != sorted(installers):
        return ["names differ from the installers"]
    return [
        f"digest differs for {name}" for name in installers if sha256(folder / name) != listed[name]
    ]


def stage(version: str, folders: dict[str, Path], out: Path) -> tuple[list[str], list[str]]:
    """(staged names, problems); copies nothing unless there are no problems."""
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        return [], [f"--out {out} exists and is not an empty folder"]
    problems: list[str] = []
    plan: dict[Path, str] = {}
    for os_name, folder in folders.items():
        required, optional = allowlist(os_name)
        for name, staged in required.items():
            if (folder / name).is_file():
                plan[folder / name] = staged
            else:
                problems.append(f"{os_name}: missing {name}")
        plan |= {
            folder / name: staged for name, staged in optional.items() if (folder / name).is_file()
        }
        installers = INSTALLERS[os_name]
        feed, sums = folder / FEEDS[os_name], folder / f"SHA256SUMS-{os_name}.txt"
        if feed.is_file():
            text = feed.read_text(encoding="utf-8")
            problems += [
                f"{os_name}: {feed.name} {p}" for p in feed_problems(text, version, installers)
            ]
        if sums.is_file() and all((folder / name).is_file() for name in installers):
            text = sums.read_text(encoding="utf-8")
            problems += [
                f"{os_name}: {sums.name} {p}" for p in checksum_problems(text, folder, installers)
            ]
    if problems:
        return [], problems
    out.mkdir(parents=True, exist_ok=True)
    for source, staged in plan.items():
        shutil.copyfile(source, out / staged)
    return sorted(plan.values()), []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_assets.py", description="Check the release version and stage its assets."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("version", help="check the three version fields (and the tag)")
    check.add_argument("--tag", help="the pushed tag, vX.Y.Z")
    staging = commands.add_parser("stage", help="copy the release allowlist into --out")
    staging.add_argument("--version", required=True)
    for os_name in INSTALLERS:
        staging.add_argument(f"--{os_name}", required=True, type=Path, metavar="DIR")
    staging.add_argument("--out", required=True, type=Path, metavar="DIR")
    args = parser.parse_args(argv)

    if args.command == "version":
        found = read_versions(REPO)
        problems = version_problems(found, args.tag)
        printed = [] if problems else [found["pyproject.toml"]]
    else:
        folders = {os_name: getattr(args, os_name) for os_name in INSTALLERS}
        printed, problems = stage(args.version, folders, args.out)
    for line in printed:
        print(line)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
