"""Pins which `src/` modules may reach the network and which `DATA_FLOWS` row tells the user
about each, that the desktop window has no network code and reaches an updater only through
`desktop/updates.mjs` with no feed until GO (ADR-055), and that product text never calls VIKI
Agent a "companion".

A module reaches the network in one of three seen ways: it imports a network client
(`ALLOWED`), it subclasses or constructs the shared `FixedJsonHttp` client (`HTTPS_HOSTS`), or it
imports an `ALLOWED` module from outside that module's package (`OUTSIDE_REUSE`).
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

TESTS = Path(__file__).parent
REPO = TESTS.parent
SRC = REPO / "src"
FRONTEND = REPO / "frontend"
DOCS = REPO / "docs"
DESKTOP = REPO / "desktop"

# `network_imports` treats a module as network-reaching when it equals one of these, or is a
# dotted submodule of one (`m + "."`), imported anywhere in the file.
NETWORK_MODULES = (
    "urllib.request",
    "http.client",
    "httpx",
    "httpx2",
    "requests",
    "urllib3",
    "aiohttp",
    "websockets",
    "ftplib",
    "smtplib",
)

# The modules that import a network client themselves, posix paths relative to `src/`. Other
# modules reach the network through them (`HTTPS_HOSTS`, `OUTSIDE_REUSE`), so these are not all
# the hosts. A new path here must also update `frontend/src/productFacts.ts`'s `DATA_FLOWS` and
# the Help topics that repeat those facts — not just this allowlist.
ALLOWED: dict[str, str] = {
    "viki_agent/llm/gateway.py": "User-selected model endpoint, bounded HTTPS or loopback HTTP.",
    "viki_agent/llm/magpie.py": "Authenticated readiness check of bundled Magpie on loopback HTTP.",
    "viki_agent/messaging/mattermost_http.py": (
        "REST via httpx2 (FixedJsonHttp), shared by Slack and Discord HTTP."
    ),
    "viki_agent/messaging/mattermost_transport.py": "Mattermost's websocket transport.",
    "viki_agent/messaging/discord_transport.py": "Discord's Gateway websocket transport.",
    "viki_agent/voice/voice_pack_fetch.py": (
        "Pinned Natural voices downloads from GitHub and Hugging Face, https only."
    ),
    "viki_agent/voice/voicebox_http.py": "The user's own Voicebox app, loopback only.",
    "viki_agent/agent_cli.py": (
        "The viki-agent command in a chat, to VIKI Agent's own server on 127.0.0.1 only."
    ),
}


def network_imports(source: str) -> set[str]:
    """The `NETWORK_MODULES` entries `source` imports, at any depth.

    Matches `ast.Import` names, level-0 (absolute) `ast.ImportFrom` modules, and the literal
    first argument of `importlib.import_module`, `import_module` and `__import__` calls, when
    it equals, or is a dotted submodule of (`m + "."`), one of `NETWORK_MODULES`. A module name
    built at run time is not seen.
    """
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found |= _matching_modules(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found |= _matching_modules(node.module)
        elif isinstance(node, ast.Call) and _is_dynamic_import(node.func) and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found |= _matching_modules(first.value)
    return found


def _is_dynamic_import(func: ast.expr) -> bool:
    if isinstance(func, ast.Attribute):
        return func.attr == "import_module"
    return isinstance(func, ast.Name) and func.id in {"import_module", "__import__"}


def _matching_modules(module: str) -> set[str]:
    return {m for m in NETWORK_MODULES if module == m or module.startswith(m + ".")}


# --- the modules that import a network client are exactly the ALLOWED ones -----------------


def _src_network_hits() -> dict[str, set[str]]:
    hits: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        found = network_imports(path.read_text(encoding="utf-8"))
        if found:
            hits[path.relative_to(SRC).as_posix()] = found
    return hits


def test_network_imports_under_src_equal_allowed() -> None:
    """Every `src/**/*.py` module with a network-client import is in `ALLOWED`.

    A new path found here must also update `frontend/src/productFacts.ts`'s `DATA_FLOWS` and
    the Help topics that repeat those facts, in the same change as the allowlist update.
    """
    assert set(_src_network_hits()) == set(ALLOWED)


_PLANTED_SOURCE = """\
import httpx2
from websockets.asyncio.client import connect
import socket
import ssl
"""


def test_network_imports_detects_a_planted_import() -> None:
    assert network_imports(_PLANTED_SOURCE) == {"httpx2", "websockets"}


_PLANTED_DYNAMIC_SOURCE = """\
import importlib
from importlib import import_module

importlib.import_module("httpx2")
import_module("aiohttp.web")
__import__("requests")
importlib.import_module("json")
"""


def test_network_imports_detects_a_planted_dynamic_import() -> None:
    assert network_imports(_PLANTED_DYNAMIC_SOURCE) == {"httpx2", "aiohttp", "requests"}


# --- hosts reached through the shared HTTPS boundary, and reuse outside its package ---------

# Every module that subclasses or constructs `FixedJsonHttp` (mattermost_http.py's fixed-host
# HTTPS client), with the host it reaches and the `DATA_FLOWS` label that tells the user. A host
# written as a URL must appear in that module's source.
HTTPS_HOSTS: dict[str, tuple[str, str]] = {
    "viki_agent/messaging/mattermost_http.py": ("the Mattermost server you set up", "Messaging"),
    "viki_agent/messaging/slack_http.py": ("https://slack.com/api", "Messaging"),
    "viki_agent/messaging/discord_http.py": ("https://discord.com/api/v10", "Messaging"),
    "viki_agent/decision_loop/model.py": ("https://api.typesafe.ai", "Decision"),
}

# Every `src/` module that imports an `ALLOWED` module from outside that module's own package,
# with why and the `DATA_FLOWS` label that covers it.
OUTSIDE_REUSE: dict[str, tuple[str, str]] = {
    "viki_agent/__main__.py": (
        "stops a Magpie a crashed launch left running, in recovery mode; sends nothing",
        "Model connections",
    ),
    "viki_agent/app/settings.py": ("Voicebox's loopback URL constants; sends nothing", "Speech"),
    "viki_agent/server.py": ("builds the messaging and model clients", "Model connections"),
    "viki_agent/decision_loop/model.py": ("TypeSafe's client, on FixedJsonHttp", "Decision"),
    "viki_agent/drivers/claude/driver.py": ("uses a per-process model route", "Model connections"),
    "viki_agent/session.py": ("issues an explicitly selected model route", "Model connections"),
}


def _module_name(relative: str) -> str:
    return relative.removesuffix(".py").replace("/", ".")


def imported_modules(source: str, relative: str) -> set[str]:
    """The dotted modules `source` (the `src/`-relative file `relative`) imports, at any depth.

    `from pkg import name` yields both `pkg` and `pkg.name`; relative imports resolve against
    the file's own package.
    """
    package = _module_name(relative).split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            found.add(module)
            found |= {f"{module}.{alias.name}" for alias in node.names}
    return found


def https_boundary_users(source: str) -> bool:
    """True when `source` subclasses or constructs `FixedJsonHttp`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and any(_named(b, "FixedJsonHttp") for b in node.bases):
            return True
        if isinstance(node, ast.Call) and _named(node.func, "FixedJsonHttp"):
            return True
    return False


def _named(node: ast.expr, name: str) -> bool:
    return (isinstance(node, ast.Name) and node.id == name) or (
        isinstance(node, ast.Attribute) and node.attr == name
    )


def _src_files() -> dict[str, str]:
    return {
        path.relative_to(SRC).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*.py"))
    }


def _data_flow_labels() -> set[str]:
    facts = (FRONTEND / "src" / "productFacts.ts").read_text(encoding="utf-8")
    return set(re.findall(r'label: "([^"]+)"', facts))


def test_https_boundary_users_equal_reviewed_hosts() -> None:
    """A new host reached through `FixedJsonHttp` fails here until it is listed with its row."""
    users = {relative for relative, source in _src_files().items() if https_boundary_users(source)}
    assert users == set(HTTPS_HOSTS)


def test_listed_hosts_appear_in_their_modules() -> None:
    files = _src_files()
    for relative, (host, _label) in HTTPS_HOSTS.items():
        if host.startswith("https://"):
            assert host in files[relative], relative


def test_allowed_modules_reused_outside_their_package_are_listed() -> None:
    allowed = {_module_name(relative): relative for relative in ALLOWED}
    reusers = set()
    for relative, source in _src_files().items():
        if relative in ALLOWED:
            continue
        own_package = relative.rsplit("/", 1)[0]
        for module in imported_modules(source, relative) & set(allowed):
            if allowed[module].rsplit("/", 1)[0] != own_package:
                reusers.add(relative)
    assert reusers == set(OUTSIDE_REUSE)


def test_every_listed_network_path_names_a_data_flows_row() -> None:
    labels = _data_flow_labels()
    assert labels
    for relative, (_what, label) in {**HTTPS_HOSTS, **OUTSIDE_REUSE}.items():
        assert label in labels, f"{relative}: {label}"


_PLANTED_BOUNDARY_SOURCE = """\
from viki_agent.messaging import mattermost_http


class ExampleHttp(mattermost_http.FixedJsonHttp):
    pass
"""


def test_the_boundary_checks_detect_planted_reuse() -> None:
    assert https_boundary_users(_PLANTED_BOUNDARY_SOURCE)
    assert https_boundary_users("client = FixedJsonHttp('https://example.invalid', {})\n")
    assert not https_boundary_users("from x import FixedHttpError\n")
    relative_import = "from ..messaging.mattermost_http import FixedJsonHttp\n"
    assert "viki_agent.messaging.mattermost_http" in imported_modules(
        relative_import, "viki_agent/decision_loop/model.py"
    )


# --- the desktop window has no network module or updater -----------------------------------

# Node's network built-ins, banned with or without the `node:` prefix: Node resolves a bare
# "http" to the same module as "node:http".
_DESKTOP_BANNED_BUILTINS = ("http", "https", "http2", "net", "tls", "dgram")
_DESKTOP_BANNED_TOKENS = (
    "crashReporter",
    "net.request",
    "net.fetch",
    "fetch(",
)
# The specifier of `import … from "x"`, `export … from "x"`, `import "x"`, `import("x")` and
# `require("x")`.
_SPECIFIER = re.compile(r"""(?:\bfrom|\bimport\s*\(?|\brequire\s*\()\s*["']([^"']+)["']""")


def desktop_hits(text: str) -> list[str]:
    """The banned network built-ins `text` imports or requires, then the banned tokens it names."""
    specifiers = {spec.removeprefix("node:") for spec in _SPECIFIER.findall(text)}
    builtins = sorted(specifiers & set(_DESKTOP_BANNED_BUILTINS))
    return builtins + [token for token in _DESKTOP_BANNED_TOKENS if token in text]


_PLANTED_DESKTOP_SOURCE = """\
import { request } from "http";
import tls from "node:tls";
import { join } from "node:path";
const dgram = await import("dgram");
const { connect } = require("net");
"""


def test_desktop_hits_detects_planted_network_modules() -> None:
    assert desktop_hits(_PLANTED_DESKTOP_SOURCE) == ["dgram", "http", "net", "tls"]


def test_desktop_top_level_modules_have_no_network_or_updater() -> None:
    """Each top-level `desktop/*.mjs` has no network module, request helper or updater call.

    The updater is allowed only through `desktop/updates.mjs`, gated on a feed file that no
    build ships until GO (ADR-055); `test_no_update_feed_until_go` keeps `DATA_FLOWS` row 2
    ("No VIKI account, payment, usage reporting or update check. Nothing goes to VIKI Agent's
    maker.") true until then.
    """
    modules = sorted(DESKTOP.glob("*.mjs"))
    assert modules
    for path in modules:
        hits = desktop_hits(path.read_text(encoding="utf-8"))
        assert hits == [], f"{path.name}: {hits}"


def publish_paths(value: object, prefix: str) -> list[str]:
    """Dotted paths to every `"publish"` key under `value`, walking dicts and lists at any depth.

    `prefix` labels the root (`"build"` for `build.publish`); a list item's path gets an index
    suffix (`build.win.target[0].publish`). `value`'s own `"publish"` key counts too, so a
    clean `build` with only its own `publish` set yields exactly one path.
    """
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if key == "publish":
                found.append(path)
            found.extend(publish_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(publish_paths(item, f"{prefix}[{index}]"))
    return found


def _feed_files() -> list[Path]:
    """`app-update.yml` / `dev-app-update.yml` files under `desktop/`, outside `node_modules`."""
    names = {"app-update.yml", "dev-app-update.yml"}
    found: list[Path] = []
    for folder, directories, files in os.walk(DESKTOP):
        directories[:] = [name for name in directories if name != "node_modules"]
        found.extend(Path(folder) / name for name in files if name in names)
    return found


def test_no_update_feed_until_go() -> None:
    """No build ships an update feed before ADR-055's GO switch.

    `build.publish` is the only `"publish"` key anywhere inside `build`, and it is `null`; no
    `app-update.yml` or `dev-app-update.yml` feed file is committed under `desktop/`; and among
    the top-level `desktop/*.mjs` files, only `updates.mjs` names `electron-updater`.
    """
    build = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["build"]
    assert publish_paths(build, "build") == ["build.publish"]
    assert build["publish"] is None

    assert _feed_files() == []

    modules = sorted(DESKTOP.glob("*.mjs"))
    assert modules
    updater_users = [
        path.name for path in modules if "electron-updater" in path.read_text(encoding="utf-8")
    ]
    assert updater_users == ["updates.mjs"]


_PLANTED_BUILD_WITH_NESTED_PUBLISH = {
    "publish": None,
    "deb": {"publish": {"provider": "github"}},
}


def test_publish_paths_detects_a_planted_nested_publish() -> None:
    assert publish_paths(_PLANTED_BUILD_WITH_NESTED_PUBLISH, "build") == [
        "build.publish",
        "build.deb.publish",
    ]
    assert publish_paths({"publish": None}, "build") == ["build.publish"]


# --- product text never calls VIKI Agent a "companion" -------------------------------------

_TEXT_SUFFIXES = {".md", ".html", ".ts", ".tsx", ".css", ".py", ".mjs", ".json"}
_COMPANION = re.compile("companion", re.IGNORECASE)


def _text_files(root: Path, *, exclude: Path | None = None, skip_tests: bool = False) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        if exclude is not None and exclude in path.parents:
            continue
        if skip_tests and ".test." in path.name:
            continue
        found.append(path)
    return found


def _companion_scan_targets() -> list[Path]:
    fixed = [REPO / "README.md", FRONTEND / "index.html"]
    return (
        fixed
        + _text_files(DOCS, exclude=DOCS / "work")
        + _text_files(FRONTEND / "src", skip_tests=True)
        + _text_files(SRC)
        + sorted(DESKTOP.glob("*.mjs"))
    )


def test_no_companion_framing() -> None:
    targets = _companion_scan_targets()
    assert targets
    hits = {
        path.relative_to(REPO).as_posix()
        for path in targets
        if _COMPANION.search(path.read_text(encoding="utf-8"))
    }
    assert hits == set()
