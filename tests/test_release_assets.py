"""The release workflow's version check and asset stager, on synthetic build folders, and the
workflow's trust rules, read line by line from `.github/workflows/release.yml`."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import release_assets  # noqa: E402

WORKFLOW = REPO / ".github" / "workflows" / "release.yml"
VERSION = "0.2.0"
INSTALLERS = {
    "windows": ["viki-agent-windows-x64-setup.exe"],
    "macos": ["viki-agent-macos-arm64.dmg", "viki-agent-macos-arm64.zip"],
    "linux": ["viki-agent-linux-amd64.deb"],
}
FEEDS = {"windows": "latest.yml", "macos": "latest-mac.yml", "linux": "latest-linux.yml"}
PARTS = ["frontend", "desktop", "python", "magpie"]


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = release_assets.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# -- version ------------------------------------------------------------------------------------


def versions_repo(root: Path, pyproject: str, desktop: str, frontend: str) -> Path:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "synthetic"\nversion = "{pyproject}"\n', encoding="utf-8"
    )
    for tree, value in (("desktop", desktop), ("frontend", frontend)):
        (root / tree).mkdir()
        (root / tree / "package.json").write_text(json.dumps({"version": value}), encoding="utf-8")
    return root


@pytest.fixture
def synthetic_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[str, str, str], None]:
    def make(pyproject: str, desktop: str, frontend: str) -> None:
        monkeypatch.setattr(
            release_assets, "REPO", versions_repo(tmp_path, pyproject, desktop, frontend)
        )

    return make


def test_version_match_prints_it(synthetic_versions, capsys) -> None:
    synthetic_versions("1.4.2", "1.4.2", "1.4.2")
    assert run(capsys, "version") == (0, "1.4.2\n", "")


def test_version_mismatch_names_each_file(synthetic_versions, capsys) -> None:
    synthetic_versions("1.4.2", "1.4.3", "1.5.0")
    code, out, err = run(capsys, "version")
    assert (code, out) == (1, "")
    assert err.splitlines() == [
        "desktop/package.json is 1.4.3, pyproject.toml is 1.4.2",
        "frontend/package.json is 1.5.0, pyproject.toml is 1.4.2",
    ]


@pytest.mark.parametrize(
    ("tag", "code"), [("v1.4.2", 0), ("v1.4.3", 1), ("1.4.2", 1), ("v1.4.2-rc.1", 1), ("", 1)]
)
def test_version_tag_must_be_v_plus_the_version(synthetic_versions, capsys, tag, code) -> None:
    synthetic_versions("1.4.2", "1.4.2", "1.4.2")
    got, out, err = run(capsys, "version", "--tag", tag)
    assert got == code
    assert (out, err) == (("1.4.2\n", "") if code == 0 else ("", f"tag {tag} is not v1.4.2\n"))


def test_version_must_be_three_numbers(synthetic_versions, capsys) -> None:
    synthetic_versions("1.4.2-rc.1", "1.4.2-rc.1", "1.4.2-rc.1")
    assert run(capsys, "version") == (1, "", "pyproject.toml version 1.4.2-rc.1 is not X.Y.Z\n")


def test_version_fields_of_this_repo_agree(capsys) -> None:
    code, out, _ = run(capsys, "version")
    assert code == 0
    assert re.fullmatch(r"\d+\.\d+\.\d+\n", out)


# -- stage --------------------------------------------------------------------------------------


def feed(os_name: str, version: str = VERSION) -> str:
    files = "".join(
        f"  - url: {name}\n    sha512: c3ludGhldGlj\n    size: 9\n" for name in INSTALLERS[os_name]
    )
    return (
        f"version: {version}\nfiles:\n{files}path: {INSTALLERS[os_name][0]}\n"
        "sha512: c3ludGhldGlj\nreleaseDate: '2026-11-28T00:00:00.000Z'\n"
    )


def build_folder(root: Path, os_name: str, *, blockmaps: bool) -> Path:
    """One OS's `dist/desktop` as the build job uploads it, plus files stage must ignore."""
    folder = root / os_name
    folder.mkdir(parents=True)
    sums = []
    for name in INSTALLERS[os_name]:
        data = f"synthetic {name}".encode()
        (folder / name).write_bytes(data)
        sums.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
        if blockmaps:
            (folder / f"{name}.blockmap").write_bytes(b"synthetic blockmap")
    (folder / f"SHA256SUMS-{os_name}.txt").write_text("".join(sums), encoding="utf-8")
    (folder / f"THIRD-PARTY-NOTICES-{os_name}.txt").write_text("notices\n", encoding="utf-8")
    for part in PARTS:
        (folder / f"sbom-{part}.cdx.json").write_text(f'{{"part": "{part}"}}\n', encoding="utf-8")
    (folder / FEEDS[os_name]).write_text(feed(os_name), encoding="utf-8")
    (folder / "builder-debug.yml").write_text("x: 1\n", encoding="utf-8")
    (folder / "viki-agent-old.exe").write_bytes(b"not on the allowlist")
    (folder / f"{os_name}-unpacked").mkdir()
    return folder


@pytest.fixture
def folders(tmp_path: Path) -> dict[str, Path]:
    return {
        "windows": build_folder(tmp_path / "in", "windows", blockmaps=True),
        "macos": build_folder(tmp_path / "in", "macos", blockmaps=True),
        "linux": build_folder(tmp_path / "in", "linux", blockmaps=False),
    }


def stage(capsys, folders: dict[str, Path], out: Path) -> tuple[int, str, str]:
    paths = [arg for os_name in INSTALLERS for arg in (f"--{os_name}", str(folders[os_name]))]
    return run(capsys, "stage", "--version", VERSION, *paths, "--out", str(out))


def allowlist(blockmap_oses: set[str]) -> set[str]:
    names: set[str] = set()
    for os_name, installers in INSTALLERS.items():
        names |= set(installers) | {FEEDS[os_name]}
        names |= {f"SHA256SUMS-{os_name}.txt", f"THIRD-PARTY-NOTICES-{os_name}.txt"}
        names |= {f"sbom-{part}-{os_name}.cdx.json" for part in PARTS}
        if os_name in blockmap_oses:
            names |= {f"{name}.blockmap" for name in installers}
    return names


def test_stage_copies_exactly_the_allowlist(folders, tmp_path, capsys) -> None:
    out = tmp_path / "release"
    code, listed, err = stage(capsys, folders, out)
    assert (code, err) == (0, "")
    staged = {path.name for path in out.iterdir()}
    assert staged == allowlist({"windows", "macos"})
    assert len(staged) == 25 + 3
    assert listed.splitlines() == sorted(staged)
    macos = folders["macos"]
    assert (out / "sbom-python-macos.cdx.json").read_bytes() == (
        macos / "sbom-python.cdx.json"
    ).read_bytes()
    for name in INSTALLERS["macos"]:
        assert (out / name).read_bytes() == (macos / name).read_bytes()


def test_stage_accepts_an_existing_empty_out(folders, tmp_path, capsys) -> None:
    out = tmp_path / "release"
    out.mkdir()
    assert stage(capsys, folders, out)[0] == 0
    assert {path.name for path in out.iterdir()} == allowlist({"windows", "macos"})


def test_stage_refuses_an_out_that_is_not_empty(folders, tmp_path, capsys) -> None:
    out = tmp_path / "release"
    out.mkdir()
    (out / "keep.txt").write_text("kept\n", encoding="utf-8")
    code, listed, err = stage(capsys, folders, out)
    assert (code, listed) == (1, "")
    assert err == f"--out {out} exists and is not an empty folder\n"
    assert [path.name for path in out.iterdir()] == ["keep.txt"]


def rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def resum(path: Path, edit: Callable[[list[str]], list[str]]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(edit(lines)), encoding="utf-8")


WIN_EXE = INSTALLERS["windows"][0]
MAC_DMG, MAC_ZIP = INSTALLERS["macos"]
DEB = INSTALLERS["linux"][0]

REFUSALS: dict[str, tuple[Callable[[dict[str, Path]], object], str]] = {
    "missing installer": (
        lambda f: (f["macos"] / MAC_ZIP).unlink(),
        f"macos: missing {MAC_ZIP}",
    ),
    "missing sbom": (
        lambda f: (f["linux"] / "sbom-magpie.cdx.json").unlink(),
        "linux: missing sbom-magpie.cdx.json",
    ),
    "missing feed": (
        lambda f: (f["windows"] / "latest.yml").unlink(),
        "windows: missing latest.yml",
    ),
    "missing notices": (
        lambda f: (f["windows"] / "THIRD-PARTY-NOTICES-windows.txt").unlink(),
        "windows: missing THIRD-PARTY-NOTICES-windows.txt",
    ),
    "missing sums": (
        lambda f: (f["linux"] / "SHA256SUMS-linux.txt").unlink(),
        "linux: missing SHA256SUMS-linux.txt",
    ),
    "no version line": (
        lambda f: rewrite(f["linux"] / "latest-linux.yml", f"version: {VERSION}\n", ""),
        f'linux: latest-linux.yml has no single "version: {VERSION}" line',
    ),
    "two version lines": (
        lambda f: rewrite(
            f["macos"] / "latest-mac.yml", "files:\n", f"version: {VERSION}\nfiles:\n"
        ),
        f'macos: latest-mac.yml has no single "version: {VERSION}" line',
    ),
    "other version": (
        lambda f: rewrite(f["windows"] / "latest.yml", f"version: {VERSION}", "version: 0.1.9"),
        f'windows: latest.yml has no single "version: {VERSION}" line',
    ),
    "url not staged": (
        lambda f: rewrite(f["macos"] / "latest-mac.yml", f"url: {MAC_DMG}", "url: other.dmg"),
        "macos: latest-mac.yml line '  - url: other.dmg' names no staged installer",
    ),
    "url to a staged non-installer": (
        lambda f: rewrite(f["windows"] / "latest.yml", f"url: {WIN_EXE}", "url: latest.yml"),
        "windows: latest.yml line '  - url: latest.yml' names no staged installer",
    ),
    "path not staged": (
        lambda f: rewrite(f["linux"] / "latest-linux.yml", f"path: {DEB}", "path: ../x.deb"),
        "linux: latest-linux.yml line 'path: ../x.deb' names no staged installer",
    ),
    "quoted url": (
        lambda f: rewrite(f["linux"] / "latest-linux.yml", f"url: {DEB}", f"url: '{DEB}'"),
        f"linux: latest-linux.yml line \"  - url: '{DEB}'\" names no staged installer",
    ),
    "flow-style url": (
        lambda f: rewrite(
            f["linux"] / "latest-linux.yml", "files:\n", f"files: [{{url: {DEB}}}]\n"
        ),
        f"linux: latest-linux.yml line 'files: [{{url: {DEB}}}]' names no staged installer",
    ),
    "sums lack an installer": (
        lambda f: resum(f["macos"] / "SHA256SUMS-macos.txt", lambda lines: lines[:1]),
        "macos: SHA256SUMS-macos.txt names differ from the installers",
    ),
    "sums name an extra file": (
        lambda f: resum(
            f["windows"] / "SHA256SUMS-windows.txt",
            lambda lines: [*lines, f"{'0' * 64}  viki-agent-old.exe\n"],
        ),
        "windows: SHA256SUMS-windows.txt names differ from the installers",
    ),
    "sums name an installer twice": (
        lambda f: resum(f["linux"] / "SHA256SUMS-linux.txt", lambda lines: lines * 2),
        "linux: SHA256SUMS-linux.txt names differ from the installers",
    ),
    "sums digest differs": (
        lambda f: (f["linux"] / DEB).write_bytes(b"changed after the checksum"),
        f"linux: SHA256SUMS-linux.txt digest differs for {DEB}",
    ),
    "sums line malformed": (
        lambda f: rewrite(f["windows"] / "SHA256SUMS-windows.txt", "  ", " *"),
        "windows: SHA256SUMS-windows.txt has a line that is not '<sha256>  <name>'",
    ),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_stage_refuses(folders, tmp_path, capsys, case) -> None:
    damage, problem = REFUSALS[case]
    damage(folders)
    out = tmp_path / "release"
    assert stage(capsys, folders, out) == (1, "", problem + "\n")
    assert not out.exists()


def test_stage_reports_one_line_per_problem(folders, tmp_path, capsys) -> None:
    (folders["windows"] / WIN_EXE).unlink()
    rewrite(folders["linux"] / "latest-linux.yml", f"version: {VERSION}", "version: 9.9.9")
    code, _, err = stage(capsys, folders, tmp_path / "release")
    assert code == 1
    assert err.splitlines() == [
        f"windows: missing {WIN_EXE}",
        f'linux: latest-linux.yml has no single "version: {VERSION}" line',
    ]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["publish"],
        ["version", "--tag"],
        ["stage", "--version", VERSION, "--windows", "w", "--macos", "m", "--linux", "l"],
    ],
)
def test_usage_errors_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        release_assets.main(argv)
    assert stopped.value.code == 2


# -- the workflow's text ------------------------------------------------------------------------


def workflow() -> list[str]:
    return WORKFLOW.read_text(encoding="utf-8").splitlines()


def block(lines: list[str], header: str) -> list[str]:
    """`header` and the lines under it, up to the next line at its own indent or less."""
    start = lines.index(header)
    indent = len(header) - len(header.lstrip())
    body = [header]
    for line in lines[start + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return [line for line in body if line.strip()]


def job(name: str) -> list[str]:
    return block(workflow(), f"  {name}:")


def test_workflow_triggers_only_on_v_tags_and_dispatch() -> None:
    assert block(workflow(), "on:") == [
        "on:",
        "  push:",
        "    tags: ['v*']",
        "  workflow_dispatch:",
    ]
    assert not [line for line in workflow() if "pull_request" in line]


def test_workflow_pins_every_action_by_sha() -> None:
    # Quoted keys count too: GitHub reads `'uses':` as `uses:`.
    uses = [line for line in workflow() if re.search(r"(?<![\w-])[\"']?uses[\"']?\s*:", line)]
    assert uses
    pinned = re.compile(r" +(?:- )?uses: actions/[a-z-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+")
    assert [line for line in uses if not pinned.fullmatch(line)] == []


def test_workflow_grants_nothing_by_default_and_write_only_to_release() -> None:
    lines = workflow()
    assert "permissions: {}" in lines
    assert [line for line in lines if re.search(r":\s*write\b", line)] == ["      contents: write"]
    assert "      contents: write" in job("release")
    for name in ("version", "windows", "macos", "linux", "stage"):
        assert block(job(name), "    permissions:") == ["    permissions:", "      contents: read"]


def test_workflow_uses_no_secrets_and_never_publishes_from_a_build() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"\bsecrets\b", text) is None  # also `secrets['NAME']`
    assert "--publish" not in text


def test_workflow_releases_a_draft_only_on_a_tag_push() -> None:
    release = job("release")
    assert "    if: github.event_name == 'push'" in release
    assert "    needs: [version, stage]" in release
    assert not [line for line in release if "actions/checkout@" in line]
    create = [line for line in release if "gh release create" in line]
    assert len(create) == 1
    assert '"$GITHUB_REF_NAME" release/* --draft --verify-tag' in create[0]


def test_workflow_builds_wait_for_the_version_check() -> None:
    for name in ("windows", "macos", "linux"):
        assert "    needs: version" in job(name)
    assert "    needs: [version, windows, macos, linux]" in job("stage")


def test_workflow_checkouts_keep_no_credentials() -> None:
    lines = workflow()
    checkouts = [i for i, line in enumerate(lines) if "uses: actions/checkout@" in line]
    assert len(checkouts) == 5  # version, the three builds and stage; never release
    for i in checkouts:
        assert [line.strip() for line in lines[i + 1 : i + 3]] == [
            "with:",
            "persist-credentials: false",
        ]
