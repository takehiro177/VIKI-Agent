"""The compiled-mode contract shared by the main program and every worker launch site (ADR-054).

``scripts/compile.py`` builds VIKI Agent with Nuitka's ``--mode=standalone`` into one binary,
``viki-agent``/``viki-agent.exe``, entered through ``src/viki_agent_main.py``. That binary's
``--viki-worker <name>`` subcommand replaces the four ``[python, "-I", "-B", <script>, ...]``
child processes a source run starts for the voice, media and voice-pack-setup helpers. This
module holds what both the main program and every call site need: which worker names exist, the
compiled binary's own file name, where the app is installed, and the argv a call site builds for
either mode. Stdlib only, so it can be imported from anywhere, including the voice worker, whose
own import contract stays stdlib-only until the engine loads.
"""

from __future__ import annotations

import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path

COMPILED = "__compiled__" in globals()
WORKER_FLAG = "--viki-worker"
WORKERS = frozenset({"voice", "media", "voice-pack-setup"})
APP_BINARY = "viki-agent.exe" if sys.platform == "win32" else "viki-agent"


def install_dir(module_file: Path, compiled: bool) -> Path:
    """The app's install folder, derived from `module_file` (callers pass ``Path(__file__)``).

    From a source checkout, `module_file` is two levels under the repo's `src/` (as for this
    file: `src/viki_agent/compiled.py`), so its folder's grandparent is the checkout root. A
    compiled Nuitka folder has no `src/` layer: `module_file` sits one level under the dist
    folder (`<dist>/viki_agent/compiled.py`), so that folder's parent is the dist folder itself.
    Taking `parents[2]` there would instead name the dist folder's *parent* — e.g. the user's
    whole home folder — which is why `compiled` is a parameter here rather than read globally.
    """
    return module_file.resolve().parents[1 if compiled else 2]


INSTALL_DIR = install_dir(Path(__file__), COMPILED)


def worker_argv(name: str, args: Sequence[str], *, python: str, script: Path) -> list[str]:
    """The argv to start worker `name` with `args`.

    Compiled, this is the installed binary's own `--viki-worker` subcommand; `python` and
    `script` are then ignored. From source, it is isolated Python running `script` directly, as
    every helper does today. Raises ValueError for a name outside `WORKERS` in either mode.
    `COMPILED` and `INSTALL_DIR` are read here as module globals, not bound as defaults, so a
    test can monkeypatch either and see `worker_argv` follow.
    """
    if name not in WORKERS:
        raise ValueError(name)
    if COMPILED:
        return [str(INSTALL_DIR / APP_BINARY), WORKER_FLAG, name, *args]
    return [python, "-I", "-B", str(script), *args]


def agent_cli() -> Path | None:
    """The absolute path of the `viki-agent` command this install can run, or None when there is
    no such file and the agent CLI feature is off: compiled, the standalone folder's own binary
    (`INSTALL_DIR / APP_BINARY`); from a source checkout or an installed wheel, the venv's
    console script (`sysconfig`'s own scripts directory / `APP_BINARY`). `COMPILED` and
    `INSTALL_DIR` are read here as module globals, not bound as defaults, so a test can
    monkeypatch either and see this follow, as `worker_argv` does."""
    path = (
        INSTALL_DIR / APP_BINARY if COMPILED else Path(sysconfig.get_path("scripts")) / APP_BINARY
    )
    return path if path.is_file() else None
