"""Smoke-test the compiled app itself (ADR-054): start it the way a user would, with no
arguments, and prove the whole stack works with no Python, uv or Node on PATH, and no Claude
Code inside the compiled folder.

    uv run --directory <repo> --group build python scripts/compiled_smoke_app.py [--app <binary>]

Stand-ins replace the two things a real launch would otherwise need installed:
`tests/fake_claude.py` answers like Claude Code's own `--version` probe
(`tests/fake_claude.py::FAKE_VERSION`), and `tests/stand_in_browser.py` stands in for the
browser window. On Windows, where the compiled app cannot run a `.py` file, both are compiled
standalone by Nuitka first; on POSIX a private 0700 `#!/bin/sh` wrapper execs this session's own
Python on the script directly. Neither stand-in ships inside the compiled folder itself (the
dist scan in compiled_smoke.py already proves that); they live in this run's own temp folder.

Refuses at once, before doing anything else, when 127.0.0.1:8765 already answers: that is this
machine's one running VIKI Agent (the single-instance lock), and starting a second copy on
the same port would wake it instead of smoke-testing a clean one -- never the compiled app's own
job, so close VIKI Agent before running this.

Never prints the launch token, the app URL, or the stand-in browser's private redirect page: any
of the three, pasted anywhere, could open a tab into a real running VIKI Agent. A failing check's
log tail is printed with every run of 20 or more URL-safe characters blanked first
(`redact_secrets`): a token, an asset id and most UUIDs all look exactly like one, so this is
deliberately over-eager (hard rule 8) rather than exact.
"""

from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Mapping, Sequence
from pathlib import Path
from urllib.parse import quote

import compiled_smoke
from websockets.sync.client import connect as ws_connect
from websockets.typing import Origin

from viki_agent import agent_cli, events, winproc

REPO = Path(__file__).resolve().parents[1]
APP_HOST = "127.0.0.1"
APP_PORT = 8765
APP_ORIGIN = f"http://{APP_HOST}:{APP_PORT}"
CLAUDE_VERSION = "2.1.259"  # tests/fake_claude.py::FAKE_VERSION; __main__.cli_version keeps this

START_TIMEOUT_S = 120.0  # waiting for the stand-in browser's recorded argv
HTTP_READY_TIMEOUT_S = 20.0  # the first REST contact, retried while the app finishes starting
STOP_TIMEOUT_S = 60.0  # the app's own exit after `quit` is dropped

_TOKEN_RE = re.compile(r"#token=([A-Za-z0-9_-]{20,})")
# A launch token is 43 URL-safe characters (secrets.token_urlsafe(32)); an asset id or a UUID
# (hyphens included -- they are in this character class too) looks exactly like a shorter one.
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{20,}")
_ENV_EXACT_DROPPED = frozenset({"VIRTUAL_ENV", "PYTHONHOME"})


# --- pure helpers, unit tested -----------------------------------------------------------------


def token(argv: Sequence[str]) -> str:
    """The launch token from the stand-in browser's recorded argv (`__main__._windows`'s own
    `argv()`): Windows carries it directly in the `--app=` URL's fragment; POSIX carries a
    `file:` URI to a private redirect page instead (`__main__._redirect_url`), whose own meta
    refresh target holds the real, token-bearing URL. Raises ValueError when neither is found;
    never includes the token, the URL or the page text in that message (hard rule 8)."""
    app_arg = next((a for a in argv if a.startswith("--app=")), None)
    if app_arg is None:
        raise ValueError("no --app= argument in the stand-in's recorded argv")
    target = app_arg[len("--app=") :]
    text = target
    if target.startswith("file:"):
        text = Path.from_uri(target).read_text(encoding="utf-8")
    match = _TOKEN_RE.search(text)
    if match is None:
        raise ValueError("no launch token found in the stand-in's recorded argv")
    return match.group(1)


def redact_secrets(text: str) -> str:
    """Blank every run of 20+ URL-safe characters. Used on anything this script prints about a
    failure (hard rule 8): over-redaction, never a miss, the same choice `diagnostics.redact`
    makes for the user-facing export."""
    return _SECRET_RE.sub("<redacted>", text)


def data_root() -> Path:
    """A fresh temp folder, resolved to its real path, for this run's own VIKI_DATA_DIR (and
    everything beside it). On macOS the system temp root (/var) is itself a symlink to
    /private/var; `storage.check_file` refuses to serve a data directory behind a symlinked
    ancestor (`unsafe_path`), which silently drops the app into its read-only recovery shell --
    no /ws, no sessions -- exactly what a macOS run hit. Resolving once,
    here, keeps every path this script builds from it already real."""
    return Path(tempfile.mkdtemp(prefix="viki-smoke-")).resolve()


def system_path(platform: str = sys.platform) -> str:
    """The minimal PATH a real double-click launch would have: never this session's own
    uv/Python/Node-prefixed one, which would quietly let the compiled app find the very tools
    it is meant to run without. The Windows triad reads SYSTEMROOT the same way
    `media_worker._worker_env`'s own allowlist does (never `winproc.system_dir()`'s live
    Win32 API, which only answers for the host actually running this), so it is safe to build
    for any `platform` value on any host, including the Linux check of this unit test.

    Built with plain string formatting rather than `pathlib.Path`: `Path` follows the *host's*
    separator convention, not `platform`'s, so a `PosixPath` built from this on a Linux or macOS
    runner would silently mix a literal backslash from SYSTEMROOT with its own forward-slash
    joins instead of producing a real Windows-style, semicolon-joined PATH value."""
    if platform == "win32":
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows").rstrip("\\")
        system32 = f"{system_root}\\System32"
        return ";".join((system32, system_root, f"{system32}\\Wbem"))
    return "/usr/bin:/bin:/usr/sbin:/sbin"


def app_env(
    base: Mapping[str, str],
    *,
    data_dir: Path,
    home: Path,
    claude_cli: Path,
    edge: Path,
    platform: str = sys.platform,
) -> dict[str, str]:
    """The environment a real launch -- double-clicked, with no dev tooling around it -- would
    have: this session's own VIRTUAL_ENV/PYTHONHOME/every UV_* dropped, PATH replaced with the
    OS's own minimal one, a private temp HOME/USERPROFILE, and VIKI_DATA_DIR/VIKI_CLAUDE_CLI/
    VIKI_EDGE pointed at this run's own temp folder and stand-ins (`config.load`,
    `config._browser_for`: VIKI_EDGE "may name any Chromium-family executable" and wins on every
    OS, with no further check). On POSIX only, XDG_RUNTIME_DIR (TMPDIR on macOS) also points
    inside this run's own temp folder, so the single-instance lock never touches a shared one."""
    env = {
        key: value
        for key, value in base.items()
        if key not in _ENV_EXACT_DROPPED and not key.startswith("UV_")
    }
    env["PATH"] = system_path(platform)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["VIKI_DATA_DIR"] = str(data_dir)
    env["VIKI_CLAUDE_CLI"] = str(claude_cli)
    env["VIKI_EDGE"] = str(edge)
    if platform != "win32":
        env["TMPDIR" if platform == "darwin" else "XDG_RUNTIME_DIR"] = str(data_dir.parent)
    return env


def port_answers(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """Whether something already accepts a TCP connection on `host:port` -- the DANGER check: a
    second launch on 8765 would wake the user's own running app rather than test a clean
    one. A pure connect probe: this never enumerates or reads another process's command line."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_bindable(host: str, port: int) -> bool:
    """Whether `host:port` can be bound fresh right now (`__main__._listen`'s own technique):
    the stronger claim the post-shutdown check needs, as opposed to `port_answers`'s "did a
    connect succeed"."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen()
    except OSError:
        return False
    finally:
        sock.close()
    return True


def stand_in_nuitka_argv(python: str, script: Path, out_dir: Path) -> list[str]:
    """The ``python -m nuitka`` argv that compiles one test stand-in standalone on Windows, where
    the compiled app refuses to run a ``.py`` file directly. Lighter than `compile.py`'s own
    `nuitka_argv`: no GUI subsystem, icon or distribution-metadata flags, just what avoids an
    interactive download prompt (`--experimental=force-dependencies-pefile`) and keeps the
    build quick (`--lto=no`). The entry script is always the last argument."""
    return [
        python,
        "-m",
        "nuitka",
        "--mode=standalone",
        "--experimental=force-dependencies-pefile",
        "--lto=no",
        "--remove-output",
        f"--output-dir={out_dir}",
        str(script),
    ]


def cli_dispatch_result(returncode: int, stdout: str, stderr: str) -> tuple[bool, str]:
    """Whether a `[app, "status", "--json"]` run, with every VIKI_AGENT_* variable removed,
    matches AC-10's promise: exit `agent_cli.EXIT_NO_KEY`, exactly one stdout line, parsing to
    a JSON object whose `error.code` is `agent_cli`'s own "no chat" refusal, `"no_agent_chat"`."""
    if returncode != agent_cli.EXIT_NO_KEY:
        return False, f"exit {returncode}, expected {agent_cli.EXIT_NO_KEY}: {stderr[-400:]!r}"
    lines = stdout.splitlines()
    if len(lines) != 1:
        return False, f"{len(lines)} stdout line(s), expected 1: {stdout!r}"
    try:
        answer = json.loads(lines[0])
    except ValueError:
        return False, f"not JSON: {lines[0]!r}"
    error = answer.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if code != "no_agent_chat":
        return False, f"error code {code!r}, expected 'no_agent_chat': {answer}"
    return True, ""


# --- stand-in preparation (no test invokes Nuitka; this path is exercised live only) ------------


def _compile_stand_in(script: Path, out_dir: Path) -> Path:
    argv = stand_in_nuitka_argv(sys.executable, script, out_dir)
    result = subprocess.run(argv, cwd=REPO, check=False)
    if result.returncode:
        raise RuntimeError(f"nuitka exited {result.returncode} compiling {script.name}")
    exe = out_dir / f"{script.stem}.dist" / f"{script.stem}.exe"
    if not exe.is_file():
        raise RuntimeError(f"missing compiled stand-in: {exe}")
    return exe


def _posix_wrapper(path: Path, script: Path) -> Path:
    """A private 0700 `#!/bin/sh` wrapper that execs this session's own Python on `script`,
    argv passed through unchanged -- never the script file itself, so it needs no shebang or
    exec bit of its own and stays importable as plain Python for its unit tests. `-E -s` keep the
    stand-in itself out of the isolation probe: the app passes the probe's PYTHONPATH and
    PYTHONUSERBASE on to its children, and a stand-in that honoured them would fire the markers
    that are meant to catch only the compiled app (seen on macOS 15). `-X utf8` replaces
    the PYTHONUTF8 that `-E` drops."""
    path.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -E -s -X utf8 "{script}" "$@"\n', encoding="utf-8"
    )
    path.chmod(0o700)
    return path


def _prepare_stand_ins(root: Path) -> tuple[Path, Path]:
    """(claude_cli, edge): the two stand-ins `app_env` points VIKI_CLAUDE_CLI and VIKI_EDGE at."""
    fake_claude = REPO / "tests" / "fake_claude.py"
    stand_in = REPO / "tests" / "stand_in_browser.py"
    if sys.platform == "win32":
        out_dir = root / "nuitka-out"
        return _compile_stand_in(fake_claude, out_dir), _compile_stand_in(stand_in, out_dir)
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    return (
        _posix_wrapper(bin_dir / "claude", fake_claude),
        _posix_wrapper(bin_dir / "edge", stand_in),
    )


# --- the installed binary's own CLI dispatch (AC-10): no port, no app data ---------------------


def _check_cli_dispatch(app: Path, data_dir: Path, home: Path) -> tuple[bool, str]:
    """`__main__.main` dispatches a CLI command (`agent_cli.COMMANDS`) before anything else,
    including the VIKI_AGENT_* scrub and the single-instance/port/data start-up below it, so
    this runs apart from the steps that need the running server: it never binds a port or
    touches app data. Runs against this run's own temp `data_dir` and `home`, a minimal PATH,
    and every inherited VIKI_AGENT_* variable removed (`winproc.scrub_env`) -- so a regressed
    dispatch starts, at worst, a throwaway app rather than a developer's own data, lock, port
    8765 or browser. `status --json` must still refuse the way a plain terminal's
    `agent_cli.main` does with no chat connection (`cli_dispatch_result` checks the reply), and
    `data_dir` must still hold nothing afterward, which proves the dispatch ran before any
    start-up ever reached it."""
    env = winproc.scrub_env(os.environ)
    env["PATH"] = system_path()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["VIKI_DATA_DIR"] = str(data_dir)
    done = winproc.run([str(app), "status", "--json"], cwd=app.parent, env=env)
    ok, detail = cli_dispatch_result(done.returncode, done.stdout, done.stderr)
    if data_dir.exists() and any(data_dir.iterdir()):
        return False, f"the temp data folder was not empty after cli_dispatch: {data_dir}"
    return ok, detail


# --- the app's own HTTP/WebSocket surface --------------------------------------------------------


def _http(
    method: str,
    path: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(APP_HOST, APP_PORT, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=dict(headers or {}))
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


async def _wait_for_path(path: Path, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out after {timeout_s:.0f}s waiting for {path.name}")
        await asyncio.sleep(0.1)


async def _wait_for_http(timeout_s: float) -> int:
    """Poll until the app answers at all, retrying only a connection refusal while it finishes
    starting; returns that first response's (unauthenticated) status."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            status, _body = _http("GET", "/api/v2/diagnostics")
            return status
        except OSError:
            await asyncio.sleep(0.2)
    raise TimeoutError(f"the app never answered on {APP_HOST}:{APP_PORT}")


async def _check_diagnostics(bearer: str) -> tuple[bool, str]:
    status = await _wait_for_http(HTTP_READY_TIMEOUT_S)
    if status != 401:
        return False, f"unauthenticated diagnostics answered {status}, expected 401"
    status, body = _http(
        "GET", "/api/v2/diagnostics", headers={"Authorization": f"Bearer {bearer}"}
    )
    if status != 200:
        return False, f"authenticated diagnostics answered {status}, expected 200"
    document = json.loads(body)
    version = document.get("provider", {}).get("claude", {}).get("version")
    if version != CLAUDE_VERSION:
        return False, f"provider.claude.version was {version!r}, expected {CLAUDE_VERSION!r}"
    return True, ""


def _check_static() -> tuple[bool, str]:
    status, body = _http("GET", "/")
    if status != 200 or b"<html" not in body.lower():
        return False, f"GET / answered {status} with {len(body)} byte(s), expected 200 HTML"
    status, _body = _http("GET", "/favicon.ico")
    if status != 200:
        return False, f"GET /favicon.ico answered {status}, expected 200"
    return True, ""


def _check_websocket(bearer: str) -> tuple[bool, str]:
    uri = f"ws://{APP_HOST}:{APP_PORT}/ws"
    with ws_connect(uri, origin=Origin(APP_ORIGIN)) as connection:
        connection.send(json.dumps({"type": "auth", "token": bearer, "v": events.VERSION}))
        first = json.loads(connection.recv(timeout=10))
    if first.get("type") != "vault.sessions":
        return False, f"first frame was {first.get('type')!r}, expected 'vault.sessions'"
    return True, ""


def _check_assets(bearer: str) -> tuple[bool, str]:
    body = compiled_smoke.png(8000, 5000)
    headers = {
        "Authorization": f"Bearer {bearer}",
        "Origin": APP_ORIGIN,
        "x-file-name": quote("source.png"),
        "Content-Type": "image/png",
    }
    status, data = _http("POST", "/api/v2/assets", headers=headers, body=body, timeout=30.0)
    if status != 201:
        return False, f"upload answered {status}, expected 201"
    answer = json.loads(data)
    shape = (answer.get("media_type"), answer.get("width"), answer.get("height"))
    if shape != ("image/png", 8000, 5000):
        return False, f"unexpected upload answer shape: {shape}"
    return True, ""


def _check_voice(bearer: str, data_dir: Path) -> tuple[bool, str]:
    compiled_smoke.fake_voice_models(data_dir / "voice")
    status, data = _http("GET", "/api/v2/voice", headers={"Authorization": f"Bearer {bearer}"})
    if status != 200:
        return False, f"status {status}, expected 200"
    state = json.loads(data).get("voice", {}).get("state")
    if state != "ready":
        return False, f"voice state was {state!r}, expected 'ready'"
    return True, ""


async def _check_shutdown(child: winproc.Child, data_dir: Path) -> tuple[bool, str]:
    (data_dir / "edge" / "quit").write_bytes(b"")
    try:
        async with asyncio.timeout(STOP_TIMEOUT_S):
            await child.wait_tree()
    except TimeoutError:
        return False, f"the app did not exit within {STOP_TIMEOUT_S:.0f}s of quit"
    assert child.process.returncode is not None
    if child.process.returncode != 0:
        return False, f"the app exited {child.process.returncode}, expected 0"
    if not port_bindable(APP_HOST, APP_PORT):
        return False, f"{APP_HOST}:{APP_PORT} does not bind after the app exited"
    if sys.platform != "win32":
        stray = sorted((data_dir / "run").glob("open-*.html"))
        if stray:
            return False, f"{len(stray)} redirect page(s) left behind: {[p.name for p in stray]}"
    return True, ""


async def _check_markers(isolation_root: Path) -> tuple[bool, str]:
    found = compiled_smoke.markers(isolation_root)
    if found:
        return False, f"unexpected isolation markers fired: {found}"
    return True, ""


# --- orchestration --------------------------------------------------------------------------


async def _check_started(data_dir: Path) -> tuple[bool, str, str | None]:
    argv_file = data_dir / "edge" / "stand-in.argv.json"
    try:
        await _wait_for_path(argv_file, START_TIMEOUT_S)
    except TimeoutError as exc:
        return False, str(exc), None
    try:
        recorded = json.loads(argv_file.read_text(encoding="utf-8"))
        launch_token = token(recorded)
    except Exception:
        return False, "could not read a launch token from the stand-in browser's argv", None
    return True, "", launch_token


async def _run(app: Path, root: Path) -> tuple[list[tuple[str, bool, str]], Path]:
    results: list[tuple[str, bool, str]] = []
    data_dir = root / "data"
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    isolation_root = root / "isolation"
    probe_env = compiled_smoke.isolation_probe(isolation_root)
    child: winproc.Child | None = None

    async def step(name: str, awaitable: Awaitable[tuple[bool, str]]) -> bool:
        try:
            ok, detail = await awaitable
        except Exception as exc:  # one failing check must still reach the teardown below
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, ok, detail))
        return ok

    # Apart from the steps below that need the running server: never binds a port or touches
    # app data (the CLI dispatch happens before any start-up), so it runs first and unguarded.
    await step("cli_dispatch", asyncio.to_thread(_check_cli_dispatch, app, data_dir, home))

    try:
        claude_cli, edge = await asyncio.to_thread(_prepare_stand_ins, root)
    except Exception as exc:
        results.append(("stand_ins", False, f"{type(exc).__name__}: {exc}"))
        return results, data_dir
    results.append(("stand_ins", True, ""))

    preflight = await asyncio.to_thread(winproc.run, [str(claude_cli), "--version"], cwd=root)
    version_ok = CLAUDE_VERSION in preflight.stdout
    results.append(
        ("claude_stand_in_version", version_ok, "" if version_ok else "unexpected --version reply")
    )
    if not version_ok:
        return results, data_dir

    env = app_env(os.environ, data_dir=data_dir, home=home, claude_cli=claude_cli, edge=edge)
    env.update(probe_env)

    try:
        child = await winproc.spawn([str(app)], cwd=root, env=env, pipes=False)
    except Exception as exc:
        results.append(("app_start", False, f"{type(exc).__name__}: {exc}"))
        return results, data_dir

    try:
        started_ok, started_detail, launch_token = await _check_started(data_dir)
        results.append(("app_start", started_ok, started_detail))
        if not started_ok or launch_token is None:
            return results, data_dir
        if not await step("diagnostics", _check_diagnostics(launch_token)):
            return results, data_dir
        if not await step("static_assets", asyncio.to_thread(_check_static)):
            return results, data_dir
        if not await step("websocket", asyncio.to_thread(_check_websocket, launch_token)):
            return results, data_dir
        if not await step("asset_upload", asyncio.to_thread(_check_assets, launch_token)):
            return results, data_dir
        if not await step("voice", asyncio.to_thread(_check_voice, launch_token, data_dir)):
            return results, data_dir
        if not await step("shutdown", _check_shutdown(child, data_dir)):
            return results, data_dir
        await step("isolation_markers", _check_markers(isolation_root))
    finally:
        if child is not None:
            # Safe at any point, including after a clean exit (both Job.terminate()
            # implementations are documented idempotent / a no-op on an empty job): the one
            # place that must always run before anything is printed (DANGER, hard rule 5).
            child.kill_tree()
            child.release()
    return results, data_dir


def _log_tail(log_file: Path, lines: int = 40) -> str:
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no log file)"
    return "\n".join(text.splitlines()[-lines:])


async def _main(app: Path) -> int:
    root = data_root()
    try:
        results, data_dir = await _run(app, root)
        ok_all = bool(results) and all(ok for _name, ok, _detail in results)
        for name, ok, detail in results:
            # Check names are this script's own constants; only the detail can carry a secret.
            print(f"PASS {name}" if ok else f"FAIL {name}: {redact_secrets(detail)}")
        if not ok_all:
            print(redact_secrets(_log_tail(data_dir / "logs" / "viki-agent.log")))
        return 0 if ok_all else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=compiled_smoke.DEFAULT_APP)
    args = parser.parse_args(argv)
    if port_answers(APP_HOST, APP_PORT):
        print(
            f"compiled app smoke refused: {APP_HOST}:{APP_PORT} already answers "
            "(another VIKI Agent is already running on this machine)",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(_main(args.app))


if __name__ == "__main__":
    sys.exit(main())
