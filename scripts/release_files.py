"""Filename and resource checks shared by wheel and standalone build boundaries."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from viki_agent.vaults.starter import FILES, source_of

_RULES = json.loads(Path(__file__).with_name("private-files.json").read_text(encoding="utf-8"))
_PRIVATE = [re.compile(pattern) for pattern in _RULES["private_patterns"]]
CERTIFICATE_LIMIT: int = _RULES["certificate_limit"]
_PRIVATE_DIRECTORIES = set(_RULES["private_directories"])
_RESOURCES = {"welcome.en.md", "starter-history.json"} | {
    "starter/" + source_of(target) for target in FILES
}
_RESOURCE_DIRS = {
    parent.as_posix()
    for name in _RESOURCES
    for parent in PurePosixPath(name).parents
    if parent != PurePosixPath(".")
}


def private_path_problem(relative: str) -> str | None:
    """Return a filename-only refusal reason, without opening the named payload."""
    parts = relative.removesuffix("/").split("/")
    if any(part in {"", ".", ".."} or "\\" in part or ":" in part for part in parts):
        return "unsafe release path"
    for part in parts:
        if any(pattern.search(part.lower()) for pattern in _PRIVATE):
            if relative == "certifi/cacert.pem":
                continue
            return "private release filename"
    return None


def resource_path_problem(relative: str, *, directory: bool = False) -> str | None:
    """The non-UI resource set comes from the starter renderer's own source mapping."""
    if relative == "ui" or relative.startswith("ui/"):
        return None
    if relative not in (_RESOURCE_DIRS if directory else _RESOURCES):
        return "unexpected product resource"
    return None


def certificate_bundle_problem(data: bytes) -> str | None:
    """Allow certificate PEM blocks and certifi's comment lines, with no other payload."""
    if len(data) > CERTIFICATE_LIMIT:
        return "oversized public certificate bundle"
    if re.search(rb"-----(?:BEGIN|END) [A-Z0-9 ]*PRIVATE KEY-----", data, re.IGNORECASE):
        return "invalid public certificate bundle"
    blocks = 0
    body: list[bytes] | None = None
    for raw_line in data.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(b"#"):
            continue
        if line == b"-----BEGIN CERTIFICATE-----" and body is None:
            body = []
        elif line == b"-----END CERTIFICATE-----" and body is not None:
            payload = b"".join(body)
            if not re.fullmatch(rb"[A-Za-z0-9+/]+={0,2}", payload) or len(payload) % 4:
                return "invalid public certificate bundle"
            blocks += 1
            body = None
        elif body is not None and re.fullmatch(rb"[A-Za-z0-9+/]+={0,2}", line):
            body.append(line)
        else:
            return "invalid public certificate bundle"
    return None if blocks and body is None else "invalid public certificate bundle"


def check_compiled_tree(root: Path) -> list[Path]:
    """Check every standalone entry; contained framework links need no second traversal."""
    root = Path(os.path.abspath(root))
    if root.is_symlink() or root.is_junction() or root.resolve(strict=True) != root:
        raise ValueError("link in compiled release root")
    files: list[Path] = []
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            relative = path.relative_to(root).as_posix()
            if problem := private_path_problem(relative):
                raise ValueError(f"{problem}: {relative}")
            if any(part.lower() in _PRIVATE_DIRECTORIES for part in relative.split("/")):
                raise ValueError(f"private release directory: {relative}")
            linked = path.is_symlink() or path.is_junction()
            target = path.resolve(strict=True) if linked else path
            if not target.is_relative_to(root) or target == root:
                raise ValueError(f"outside compiled release link: {relative}")
            if problem := private_path_problem(target.relative_to(root).as_posix()):
                raise ValueError(f"{problem}: {relative}")
            if any(part.lower() in _PRIVATE_DIRECTORIES for part in target.relative_to(root).parts):
                raise ValueError(f"private release directory: {relative}")
            mode = target.stat().st_mode
            if stat.S_ISDIR(mode):
                if not linked:
                    pending.append(path)
            elif stat.S_ISREG(mode):
                if relative == "certifi/cacert.pem":
                    with path.open("rb") as stream:
                        problem = certificate_bundle_problem(stream.read(CERTIFICATE_LIMIT + 1))
                    if problem:
                        raise ValueError(f"{problem}: {relative}")
                files.append(path)
            else:
                raise ValueError(f"unexpected compiled release file: {relative}")
    return files
