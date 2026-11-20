"""Launch VIKI Agent from a checkout or product wheel."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import html
import importlib.metadata
import logging
import logging.handlers
import os
import re
import secrets
import signal
import socket
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import FrameType

import uvicorn
from anyio.from_thread import run_sync as run_on_loop

from viki_agent import (
    agent_cli,
    compiled,
    config,
    osfs,
    protection,
    rails,
    server,
    window,
    window_host,
    winproc,
    workspaces,
)
from viki_agent.app import diagnostics, window_icon
from viki_agent.app.data_api import LocationsService, ResetService
from viki_agent.app.diagnostics import ProviderChecks, Readiness
from viki_agent.app.exports import ExportService
from viki_agent.app.imports import ImportService
from viki_agent.app.settings import KEYS, SettingsError, SettingsService
from viki_agent.catalog import Catalog
from viki_agent.compiled import COMPILED, INSTALL_DIR
from viki_agent.domain import ActiveWorkspace, Workspace
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.llm.magpie import Magpie
from viki_agent.session import SessionManager
from viki_agent.state import AppState, user_defaults
from viki_agent.storage import SessionStore, StateError
from viki_agent.vault_storage import VaultBootstrap
from viki_agent.workspaces import WorkspaceService

_REPO = INSTALL_DIR
_RESOURCES = Path(__file__).resolve().parent / "resources"


def _asset_paths(module_file: Path, compiled_app: bool = COMPILED) -> tuple[Path, Path, bool]:
    """Compiled, a Nuitka folder has no `src/` layer and no frontend/ beside it to find, so the
    packaged `resources/ui` serves even when one happens to sit there (e.g. beside a checkout)."""
    frontend = module_file.resolve().parents[2] / "frontend"
    if not compiled_app and (frontend / "package.json").is_file():
        return frontend / "dist", frontend / "public" / "favicon.ico", True
    ui = module_file.resolve().parent / "resources" / "ui"
    return ui, ui / "favicon.ico", False


STATIC_DIR, APP_ICON, _SOURCE_CHECKOUT = _asset_paths(Path(__file__))
DEV_URL = "http://127.0.0.1:5173"  # the Vite dev server (frontend/vite.config.ts)
APP_URL = "http://127.0.0.1:8765"
MUTEX_NAME = "Local\\viki-agent"
OPEN_EVENT = "Local\\viki-agent-open"
OPENED_EVENT = "Local\\viki-agent-opened"
WAKE_MUTEX = "Local\\viki-agent-wake"
WINDOW_GRACE_S = 300.0  # a turn that was running when the last window closed may finish
TASK_HOLD_S = 12 * 3600.0  # a retained chat keeps the app at most this long after the last window
WAKE_WAIT_S = 2.0  # a second launch gives the lock's holder this long to go away
BROWSER_RECHECK_S = 0.5  # a browser killed just now stops answering within milliseconds
LOG_NAME = "viki-agent.log"
LOG_MAX_BYTES = 1_000_000  # WARNING and above only, so four files is years of launches
LOG_BACKUPS = 3
MSG_PORT_TAKEN = "VIKI Agent can't start: port {port} is used by another program."
MSG_NO_EDGE = (
    "VIKI Agent can't start: Microsoft Edge was not found."
    if sys.platform == "win32"
    else (
        "VIKI Agent can't start: Microsoft Edge, Google Chrome and Chromium were not found. "
        "You can also set VIKI_EDGE to the executable of a Chromium-based browser."
    )
)
MSG_NOT_LISTENING = "VIKI Agent is already running, but a new window could not be opened."
MSG_NO_WINDOW = "VIKI Agent closed because its window could not be opened."
MSG_BAD_DATA_DIR = (
    "VIKI Agent can't start: its data folder is in a place it must not be "
    "(a vault, ~/.claude, ~/.codex and the like)."
)
MSG_NO_LOG_DIR = (
    "VIKI Agent can't start: its log folder can't be created.\n"
    "Check that this folder is writable and that no file has its name:\n"
    "{logs}"
)
MSG_DATA_DIR_IN_WORKSPACE = (
    "VIKI Agent's data folder is inside a registered folder.\n"
    "Delete this folder, set VIKI_DATA_DIR to another place, then open VIKI Agent again:\n"
    "{data}"
)
MSG_STARTUP_STATE = (
    "VIKI Agent can't start: its settings could not be saved.\n"
    "Check this folder's free space and permissions, then open VIKI Agent again:\n"
    "{data}"
)
MSG_BAD_SCRATCH = (
    "VIKI Agent can't start: the chat-only work folder can't be prepared safely.\n"
    "Check this folder and its parents for links or junctions:\n"
    "{scratch}"
)
MSG_NO_BUNDLE = (
    "VIKI Agent can't start: frontend/dist is not built yet.\nRun\n{build}\nthen open it again."
)
MSG_NO_PACKAGED_BUNDLE = (
    "VIKI Agent can't start: this installation has no built UI.\n"
    "Reinstall from a product wheel built with\n"
    "uv run python scripts/build_product.py"
)
MSG_RUNTIME_DIR = (
    "VIKI Agent can't start: its runtime folder is not private to this account.\n"
    "Make $XDG_RUNTIME_DIR/viki-agent (Linux), $TMPDIR/viki-agent (macOS) or "
    "/tmp/viki-agent-<uid> owner-only (chmod 700), or delete it, then open "
    "VIKI Agent again."
)


def _msg_browser_left_open(platform: str) -> str:
    """macOS keeps a browser with no window running until ⌘Q, so its text names the key."""
    quit_it = "Quit it with ⌘Q" if platform == "darwin" else "Quit it"
    return (
        f"A browser that an earlier VIKI Agent opened is still running, so a new "
        f"window can't open.\n{quit_it}, then open VIKI Agent again."
    )


MSG_BROWSER_LEFT_OPEN = _msg_browser_left_open(sys.platform)  # macOS and Linux only
# There is no message box for a missing vault, a missing CLI, a signed-out CLI or a billing
# problem: none of them stops a launch. They are readiness facts the shell
# shows, and a session start refuses with its own error code.
_MB_OK_ICONERROR = 0x00000010
# The launcher is exiting anyway, so a dialog may stay open until the user reads it.
ALERT_TIMEOUT_S = 600.0
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")


def token_from(env: Mapping[str, str]) -> str:
    """Return a fresh token for this launch; an inherited value could let another local process
    authenticate. `env` stays in the signature for the environment-derived call site."""
    del env
    return secrets.token_urlsafe(32)


def app_url(token: str, *, dev: bool, session_id: str | None = None) -> str:
    """APP_URL (DEV_URL with --dev), with launch identity and an optional session fragment."""
    base = DEV_URL if dev else APP_URL
    target = f"{base}/#token={token}"
    if session_id is not None:
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError("session target must be a canonical UUID")
        target += f"&session={session_id}"
    return target


def edge_argv(edge: Path, profile: Path, url: str) -> list[str]:
    """Edge as a dedicated app window, exactly: [edge, --user-data-dir=<profile>,
    --disable-extensions, --disable-sync, --no-first-run, --no-default-browser-check,
    --window-size=1440,900, --app=<url>]."""
    return [
        str(edge),
        f"--user-data-dir={profile}",
        "--disable-extensions",
        "--disable-sync",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1440,900",
        f"--app={url}",
    ]


def window_argv(edge: Path, profile: Path, url: str, platform: str) -> list[str]:
    """The app window's argv: edge_argv, plus --enable-speech-dispatcher before --app= on Linux,
    where Chromium lists speech-dispatcher's voices for read-aloud only with that switch
    (content/browser/speech/tts_linux.cc)."""
    argv = edge_argv(edge, profile, url)
    if platform == "linux":
        argv.insert(len(argv) - 1, "--enable-speech-dispatcher")
    return argv


def vault_skills(vault: Path) -> frozenset[str]:
    """The vault's skill names: directories under .claude/skills that hold a SKILL.md."""
    skills_dir = vault / ".claude" / "skills"
    if not skills_dir.is_dir():
        return frozenset()
    return frozenset(
        entry.name
        for entry in skills_dir.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    )


def cli_argv(cli: Path | None, env: Mapping[str, str]) -> tuple[str, ...]:
    """Only the resolved executable, and () when there is nothing this launch may spawn. Test
    argv belongs to scripts/e2e_server.py.

    Keep the env argument for callers, but inherited test variables never affect production
    process arguments, including the version probe before any session starts."""
    del env
    return (str(cli),) if cli is not None else ()


def git_version() -> str:
    """The first dotted version number `git --version` prints, "" when git cannot be run."""
    try:
        printed = winproc.git(Path.home(), "--version")
    except Exception:  # not installed, or a hung git (TID251 bans naming TimeoutExpired)
        return ""
    match = _VERSION_RE.search(printed)
    return match.group(0) if match else ""


def cli_version(argv: Sequence[str]) -> str:
    """The first dotted version number (e.g. "2.1.259") that `<argv> --version` prints, via
    winproc.run; "" when it cannot be run or prints none. Takes the whole argv, not just the
    exe, so the version reported to the UI comes from whatever actually runs the sessions."""
    try:
        result = winproc.run([*argv, "--version"], cwd=Path(argv[0]).parent)
    except Exception:  # missing file, or a hung CLI (TimeoutExpired; TID251 bans naming it)
        return ""
    match = _VERSION_RE.search(result.stdout) or _VERSION_RE.search(result.stderr)
    return match.group(0) if match else ""


def _configure_logging(log_dir: Path) -> None:
    if not log_dir.parent.exists():
        log_dir.parent.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Logging precedes AppState. Creating parents through logs.mkdir would otherwise
            # publish a broadly inherited data directory before the storage creator sees it.
            protection.create_private_directory(log_dir.parent)
        except protection.ProtectionError:
            raise OSError("cannot create private app storage") from None
    log_dir.mkdir(parents=True, exist_ok=True)
    # Rotated, not plain: nothing ever trims this file, no launch truncates it, and a driver
    # that logs a warning per turn would grow it without any bound the user would ever notice.
    handler = logging.handlers.RotatingFileHandler(
        log_dir / LOG_NAME, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(handler)


def _listen(host: str, port: int) -> socket.socket | None:
    """A listening socket on `host:port`, or None when something else already holds it.

    The launcher binds the port itself and hands the socket to uvicorn instead of checking the
    port and letting uvicorn bind it seconds later: uvicorn's startup runs the ASGI lifespan —
    which opens the first Edge window — *before* loop.create_server, and turns a bind failure
    there into sys.exit(3). SystemExit is not an Exception, so a port lost in that gap flashed a
    window open, killed it again, and left past every handler that would have shown
    MSG_PORT_TAKEN. Owning the socket makes the check and the bind one operation.

    No SO_REUSEADDR on Windows (uvicorn's own Config.bind_socket() sets it, which is why this
    does not use it): there that option lets a bind succeed over a port another process already
    holds, which would defeat the very check this is. POSIX needs it: without it a relaunch
    cannot rebind while the last run's connections sit in TIME_WAIT, and it cannot take over an
    active listener. Known limit: on macOS a bind to this specific address may coexist with
    another program's wildcard listener on the same port, which is then not reported. Listening
    right away also matters — the window opens before uvicorn serves, so its first request needs
    a backlog to land in."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen()
    except OSError:
        sock.close()
        return None
    return sock


def _port_free(host: str, port: int) -> bool:
    """Probe whether a listener can bind `host:port` without retaining the socket."""
    sock = _listen(host, port)
    if sock is None:
        return False
    sock.close()
    return True


def _raise_nofile_limit() -> None:
    """POSIX only: a deep vault scan holds two descriptors per level, and up to four scans can
    run at once, which can exceed macOS's default soft RLIMIT_NOFILE of 256 and fail an unrelated
    SQLite open, log write or socket accept with EMFILE. Raises the soft limit to min(hard, 4096)
    when it is lower; never lowers it (an unlimited soft limit, which CPython reports as -1 on
    Linux, is left alone), never touches the hard limit. A setrlimit failure is logged and the
    launch goes on."""
    if sys.platform == "win32":
        return
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = 4096 if hard == resource.RLIM_INFINITY else min(hard, 4096)
    if soft != resource.RLIM_INFINITY and soft < target:
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except (ValueError, OSError) as exc:
            logging.warning("could not raise RLIMIT_NOFILE (%s)", type(exc).__name__)


def _alert_argvs(platform: str, text: str) -> list[list[str]]:
    """The dialog commands to try in order on `platform`, none on Windows (a message box). The
    text is always one argument of its own, never part of a script or a shell line."""
    if platform == "win32":
        return []
    if platform == "darwin":
        return [
            [
                "/usr/bin/osascript",
                "-e",
                "on run argv",
                "-e",
                'display alert "VIKI Agent" message (item 1 of argv) as critical',
                "-e",
                "end run",
                text,
            ]
        ]
    return [
        ["zenity", "--error", "--no-markup", "--title=VIKI Agent", "--text=" + text],
        ["kdialog", "--title", "VIKI Agent", "--error", text],
    ]


def _alert(text: str) -> None:
    """A modal error box: the windowless launcher's only way to tell the user why it quit.

    POSIX: stderr (when there is one), then the first dialog program that starts. A missing
    program tries the next; any other failure ends the attempt, since the reason is already in
    the log."""
    if sys.platform == "win32":
        ctypes.WinDLL("user32").MessageBoxW(None, text, "VIKI Agent", _MB_OK_ICONERROR)
        return
    if sys.stderr is not None:
        # A stderr whose terminal has closed raises on write; the dialog must still be tried.
        with contextlib.suppress(OSError):
            print(text, file=sys.stderr)
    for argv in _alert_argvs(sys.platform, text):
        try:
            winproc.run(argv, cwd=Path.home(), timeout=ALERT_TIMEOUT_S)
        except OSError:
            continue
        except Exception:  # a hung dialog (TimeoutExpired; TID251 bans naming it)
            return
        return


def _give_up(message: str, code: int, later: list[str]) -> int:
    """Log the reason, queue the message box, and return the exit code for it. The box is
    shown by main() after the single-instance lock is released: a modal held open while this
    launch still owns the lock would make the next launch think the app is alive but deaf."""
    logging.error(message)
    later.append(message)
    return code


def _give_up_startup_state(exc: Exception, data_dir: Path, later: list[str]) -> int:
    """The store opened, then a startup read or write of app state failed: a full disk, a lock,
    a DPAPI refusal. Under pythonw a traceback out of main() reaches nobody; this is a box
    naming the folder to check. StateError and SettingsError carry a code, ProtectionError
    only its type; neither says anything about the content that failed to be written."""
    logging.error("startup state failed (%s)", getattr(exc, "code", type(exc).__name__))
    return _give_up(MSG_STARTUP_STATE.format(data=data_dir), 2, later)


def _wake_running_instance() -> bool:
    """True only after tracked admission; this does not prove native page delivery."""
    signal = winproc.Signal.open(OPEN_EVENT)
    if signal is None:
        return False
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(signal.close)
        caller = winproc.single_instance(WAKE_MUTEX)
        if caller is None:
            return False
        cleanup.callback(caller.release)
        previous = winproc.Signal.open(OPENED_EVENT)
        if previous is not None:
            # A timed-out Windows reply remains named while the server holds its handle.
            previous.close()
            return False
        reply = winproc.Signal.create(OPENED_EVENT)
        cleanup.callback(reply.close)  # POSIX creator unlinks before releasing the caller lock.
        reply.wait(0)
        signal.set()
        return reply.wait(int(WAKE_WAIT_S * 1000))


def _lock_when_free(
    name: str, timeout_s: float, poll_s: float = 0.1
) -> winproc.InstanceLock | None:
    """The lock once its holder has gone, or None after `timeout_s`."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(poll_s)
        lock = winproc.single_instance(name)
        if lock is not None:
            return lock
    return None


def _redirect_url(run_dir: Path, target: str) -> str:
    """A new 0600 page in the private folder `run_dir` whose meta refresh sends the window on to
    `target`, as a file: URI. The page holds the token, so it is never logged or shown."""
    try:
        protection.create_private_directory(run_dir)  # False when it exists; checked either way
        protection.require_private_directory(run_dir)
    except protection.ProtectionError:
        logging.warning("run folder is not private (%s)", run_dir)
        raise
    page = run_dir / f"open-{secrets.token_hex(16)}.html"
    with osfs.open_new_private(page) as out:
        out.write(
            (
                '<!doctype html><meta charset="utf-8">'
                '<meta name="referrer" content="no-referrer">'
                f'<meta http-equiv="refresh" content="0;url={html.escape(target)}">'
            ).encode()
        )
    return page.absolute().as_uri()


def _clear_redirects(run_dir: Path) -> None:
    """Remove every redirect page in `run_dir`. Each page's token ended with its launch, so this
    is tidying and never stops a launch: a missing or unreadable folder is left as it is, a
    folder that is a link is not followed, and a page that is a link loses only the link."""
    with contextlib.suppress(OSError):
        if run_dir.is_symlink():
            return
        for page in run_dir.glob("open-*.html"):
            with contextlib.suppress(OSError):
                page.unlink()


def _windows(
    cfg: config.Config,
    edge: Path | None,
    token: str,
    *,
    dev: bool,
    env: Mapping[str, str],
    signal: winproc.Signal,
    on_last_closed: Callable[[], Awaitable[None]],
    host: window_host.WindowHost | None = None,
) -> window.WindowManager:
    """The app's windows: the desktop app's own, when `host` is given, otherwise Edge's."""

    def url(session_id: str | None = None) -> str:
        target = app_url(token, dev=dev, session_id=session_id)
        if not dev or host is not None:
            # A fragment does not change the cache key of an older cached entry document.
            target = target.replace("/#", f"/?ui={uuid.uuid4().hex}#", 1)
        return target

    if host is not None:
        return window.WindowManager(
            spawn=host.spawn,
            argv=lambda: [url()],
            target_argv=lambda session_id: [url(session_id)],
            cwd=cfg.data_dir,
            env=env,
            on_last_closed=on_last_closed,
            signal=signal,
            reply=lambda: winproc.Signal.open(OPENED_EVENT),
        )

    assert edge is not None
    profile = cfg.edge_profile
    profile.mkdir(parents=True, exist_ok=True)

    def argv(session_id: str | None = None) -> list[str]:
        target = url(session_id)
        if sys.platform != "win32":
            # Any local account can read a POSIX process's argv (ps, /proc), so the browser is
            # given a private page that carries the token instead. A fresh page per window.
            target = _redirect_url(cfg.data_dir / "run", target)
        return window_argv(edge, profile, target, sys.platform)

    return window.WindowManager(
        spawn=winproc.spawn,
        argv=argv,
        target_argv=argv,
        cwd=profile,
        env=env,
        on_last_closed=on_last_closed,
        signal=signal,
        reply=lambda: winproc.Signal.open(OPENED_EVENT),
        keep_icon=_keep_icon if sys.platform == "win32" else None,
    )


def _keep_icon(holds: Sequence[Callable[[int], bool]]) -> int:
    """window_icon.apply on APP_ICON: the app windows' taskbar icon, sharp at any DPI."""
    return window_icon.apply(APP_ICON, holds)


def _browser_left_open(profile: Path, pause: Callable[[float], None] = time.sleep) -> bool:
    """Whether a browser still answers on `profile`'s singleton socket, and still does after
    BROWSER_RECHECK_S. Chromium hands a later launch on the same profile to whatever listens
    there and exits at once, so a browser an earlier launch left running (the launcher was
    killed before it could stop its windows) would take this launch's window, and the launch
    would end at once with nothing on screen. The socket rather than the pid in SingletonLock:
    after a restart that pid can name an unrelated process, while a socket nobody listens on
    refuses the connection. The second look: a launch stopped by a signal kills its windows
    without waiting for them and releases the lock a few milliseconds before a killed browser
    stops answering, so a relaunch right behind it would otherwise count a dying browser."""
    if sys.platform == "win32":
        return False
    link = profile / "SingletonSocket"
    try:
        # The link's target, a short path in the temp folder, read once as Chromium's own client
        # does: a socket path is limited to about 104 bytes, which a profile path can exceed.
        path = str(profile / os.readlink(link))
    except OSError:
        path = str(link)

    def answers() -> bool:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1.0)
            try:
                probe.connect(path)
            except OSError:
                return False
        return True

    if not answers():
        return False
    pause(BROWSER_RECHECK_S)
    return answers()


if sys.platform != "win32":
    # SIGHUP is what a closed terminal sends; SIGQUIT is Ctrl+\.
    _STOP_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM)

    class _GracefulServer(uvicorn.Server):
        """uvicorn.Server whose every stop signal asks for the one graceful stop, however many
        arrive: the lifespan then always kills the windows, and `_serve`'s finally always runs.
        On macOS and Linux nothing else kills a browser this process leaves behind.

        uvicorn's own handlers turn a second SIGINT into a forced exit that skips the lifespan,
        leave SIGHUP and SIGQUIT to end the process outright, and re-raise the signal once
        serving ends, which ends the process before `_serve` removes the redirect pages and the
        open-window FIFO. These handlers are never re-raised and stay installed after serving;
        `_serve` puts back the ones it found as its very last step. A signal the launch
        inherited ignored (nohup, a background job) cannot end the process, so it stays
        ignored. `before_exit` runs on the event loop as the shutdown begins, never inside a
        handler."""

        def __init__(self, config: uvicorn.Config, *, before_exit: Callable[[], None]) -> None:
            super().__init__(config)
            self._before_exit = before_exit

        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            for signum in _STOP_SIGNALS:
                if signal.getsignal(signum) != signal.SIG_IGN:
                    signal.signal(signum, self._stop)
            yield

        def _stop(self, signum: int, frame: FrameType | None) -> None:
            del signum, frame
            # Nothing more: a handler runs inside whatever the main thread was doing, so one
            # that took a lock could wait for ever on a lock that code (or an outer call of this
            # handler) holds.
            self.should_exit = True

        async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
            self._before_exit()
            await super().shutdown(sockets=sockets)


def _server(server_config: uvicorn.Config, *, before_exit: Callable[[], None]) -> uvicorn.Server:
    """The app's uvicorn server; on macOS and Linux one that no signal can end ungracefully.
    `before_exit` runs once, on the event loop, before any stop drains the server."""
    if sys.platform == "win32":
        return uvicorn.Server(server_config)
    return _GracefulServer(server_config, before_exit=before_exit)


def _stop_opening(windows: window.WindowManager) -> None:
    """Before any stop drains the server: no window may open any more, so a relaunch
    during the drain gets no window from the stopping app. It waits up to WAKE_WAIT_S for the
    lock instead: when this app is gone by then it takes the lock and opens its own window, and
    otherwise it reports failure without claiming a window opened.
    The open-window signal itself stays open until `_serve`'s finally, after the lifespan has
    joined the watcher: closing it under the watcher's wait races the watcher's read.

    This hook runs when uvicorn's main loop next checks `should_exit`, up to one server tick
    (0.1 s) after a stop signal, not inside the signal handler itself — once serving has
    begun. A signal caught during uvicorn's own start-up, before that loop runs
    (uvicorn/server.py:96-100), is not bound by this tick. A wake that lands inside the tick
    can still open a window on the app that is stopping, and the stop then kills it; a
    relaunch started at the signal loses that race, because its own imports take longer than
    the tick."""
    windows._stop.set()  # the watcher ends within one poll, and open() refuses from now on


def _kill_windows(windows: window.WindowManager) -> None:
    """Kill every window still tracked. The lifespan's stop does this on a graceful exit; this
    also covers ways out that skip it. Uvicorn raising OSError when it cannot serve the socket
    handed to it is not one of them: the lifespan's own stop already ran first there, so this
    call finds nothing left to kill."""
    for tracked in list(windows._windows):
        tracked.kill_tree()
        tracked.release()


WINDOW_HOST_FLAG = "--window-host="


def _window_host_pid(args: Sequence[str]) -> int | None:
    """The desktop app's process id from a `--window-host=<pid>` argument, or None without one.
    Raises ValueError for a value that is not decimal digits, below 1, or given more than once."""
    values = [
        arg.removeprefix(WINDOW_HOST_FLAG) for arg in args if arg.startswith(WINDOW_HOST_FLAG)
    ]
    if not values:
        return None
    if len(values) > 1:
        raise ValueError(f"{WINDOW_HOST_FLAG} given more than once")
    if not re.fullmatch(r"[0-9]+", values[0]):
        raise ValueError(f"{WINDOW_HOST_FLAG} needs decimal digits")
    pid = int(values[0])
    if pid < 1:
        raise ValueError(f"{WINDOW_HOST_FLAG} needs a pid of at least 1")
    return pid


def _host_gone(windows: window.WindowManager, uvi: uvicorn.Server | None) -> None:
    """The app closed its end, so stop now, without the grace."""
    _stop_opening(windows)
    if uvi is not None:
        uvi.should_exit = True


def _serve(
    cfg: config.Config,
    env: Mapping[str, str],
    *,
    args: Sequence[str],
    later: list[str],
) -> int:
    host_pid = _window_host_pid(args)
    if host_pid is None:
        if cfg.edge is None:
            return _give_up(MSG_NO_EDGE, 5, later)
        if sys.platform != "win32" and _browser_left_open(cfg.edge_profile):
            return _give_up(MSG_BROWSER_LEFT_OPEN, 9, later)
    sock = _listen(config.HOST, config.PORT)
    if sock is None:
        return _give_up(MSG_PORT_TAKEN.format(port=config.PORT), 4, later)
    run_dir = cfg.data_dir / "run"
    if sys.platform != "win32":
        _clear_redirects(run_dir)  # pages a crashed launch left behind
        found = {signum: signal.getsignal(signum) for signum in _STOP_SIGNALS}
    wake = winproc.Signal.create(OPEN_EVENT)
    try:
        return _serve_on(
            cfg,
            cfg.edge,
            env,
            args=args,
            signal=wake,
            later=later,
            sock=sock,
            host_pid=host_pid,
        )
    finally:
        wake.close()
        # uvicorn closes a socket it was handed, but only once it got as far as shutdown(); any
        # give-up or failure before uvi.run() would otherwise leave config.PORT bound for the
        # rest of this process — which, with a message box up, is as long as the user takes to
        # click OK. Closing twice is a no-op.
        sock.close()
        if sys.platform != "win32":
            _clear_redirects(run_dir)
            # Last: until here a stop signal only asks _GracefulServer for a graceful stop.
            for signum, handler in found.items():
                if handler is not None:
                    signal.signal(signum, handler)


def _serve_on(
    cfg: config.Config,
    edge: Path | None,
    env: Mapping[str, str],
    *,
    args: Sequence[str],
    signal: winproc.Signal,
    later: list[str],
    sock: socket.socket,
    host_pid: int | None = None,
) -> int:
    dev = "--dev" in args
    if not dev and not (STATIC_DIR / "index.html").is_file():
        # A checkout can build frontend/dist; an installed wheel needs packaged resources.
        # Check index.html because a half-written directory can otherwise serve only 404s.
        return _give_up(
            MSG_NO_BUNDLE.format(build=f'npm --prefix "{STATIC_DIR.parent}" run build')
            if _SOURCE_CHECKOUT
            else MSG_NO_PACKAGED_BUNDLE,
            7,
            later,
        )
    token = token_from(env)
    log_file = cfg.data_dir / "logs" / LOG_NAME
    launch_id = str(uuid.uuid4())
    # Readiness reads only the provider fields of `cfg`, which the active-workspace replacement
    # below does not touch, so it can be built before the store opens — recovery mode needs it.
    # It probes `git --version` once here; a later `git` check updates it, and the identity
    # probes read `readiness.git_state` rather than a copy of the launch-time answer.
    readiness = Readiness(
        config=cfg,
        launch_id=launch_id,
        git_available=lambda: bool(git_version()),
        git_overridden=rails._has_git_scope_override,
    )
    try:
        state = AppState(
            cfg.state_file,
            user_defaults(cfg.claude_home, cfg.vault, env=env),
        )
    except StateError as exc:
        # A damaged or newer store is not an empty first run, and it is not a reason to show
        # nothing either: serve the read-only recovery shell instead. Do not start providers or
        # log exception bodies, which can contain data from an unreadable source.
        logging.error("app-state startup refused (%s)", exc.code)
        return _serve_recovery(
            cfg,
            edge,
            env,
            token=token,
            dev=dev,
            signal=signal,
            sock=sock,
            later=later,
            readiness=readiness,
            storage_refusal=exc.code,
            log_file=log_file,
            host_pid=host_pid,
        )
    # `services` is built below, after the active workspace is known; the lambda reads it when
    # a policy inspection asks, never now (WorkspaceService calls `hide_previews` only while
    # serving a request). `services is None` is therefore unreachable in practice and is written
    # out rather than asserted, so a future caller order can never raise instead of hiding a
    # preview.
    services: Services | None = None
    spaces = WorkspaceService(
        state.store,
        # config.load() already judged the override; handing a refused one on would have the
        # service canonicalise it again and log the same refusal a second time.
        env=env if cfg.vault_problem is None else _without_vault_override(env),
        data_dir=cfg.data_dir,
        home=Path.home(),
        install_dir=_REPO,
        git_available=lambda: readiness.git_state == "ready",
        hide_previews=lambda: services is not None and _hide_previews(services.settings),
    )
    try:
        held = spaces.contains_data_dir(cfg.data_dir)
        if held is not None:
            # The data directory was moved by environment into a root that is already
            # registered. The log folder, and for a new store app.sqlite3, exist there by now;
            # the message names the folder to remove. The workspace itself is identified in the
            # log, never on screen.
            logging.error("data directory lies inside registered workspace %s", held.workspace_id)
            return _give_up(MSG_DATA_DIR_IN_WORKSPACE.format(data=cfg.data_dir), 2, later)
    except (StateError, protection.ProtectionError) as exc:
        return _give_up_startup_state(exc, cfg.data_dir, later)
    try:
        active = spaces.resolve_active()
    except (StateError, protection.ProtectionError) as exc:
        # resolve_active refuses a link anywhere in the chain of the chat-only scratch root, or
        # cannot create that root privately, and without a working directory there is nothing
        # to serve. No registration is involved, so the message names that folder rather than
        # talking about a workspace the user has not got.
        logging.error("active workspace refused (%s)", getattr(exc, "code", type(exc).__name__))
        scratch = workspaces.scratch_path(cfg.data_dir)
        return _give_up(MSG_BAD_SCRATCH.format(scratch=scratch), 2, later)
    cfg = replace(cfg, vault=active.cwd)
    # The preflight runs against the workspace the CLI will actually run in, and is never fatal
    # any more: it blocks session starts through readiness, not the launch.
    problems = config.billing_problems(cfg, env)
    for problem in problems:
        logging.error(problem)
    readiness.observe_billing(problems)
    # macOS and Linux keep the sealing key in the OS keychain (a no-op on Windows). Visited once
    # here: after every refusal but a failed first settings read inside build_services (exit 2)
    # and a first window that never opens (exit 6), and before the services and the app, where
    # the first seal can happen (build_services itself seals nothing), so a locked or slow
    # keychain is never found for the first time inside a store write transaction, where it
    # would hold that transaction open and every other writer would see `busy`. The recovery
    # shell never seals and never visits.
    protection.visit_key_source()
    try:
        services = build_services(
            cfg,
            env,
            state=state,
            spaces=spaces,
            active=active,
            readiness=readiness,
            launch_id=launch_id,
            log_file=log_file,
            # Both readers run on anyio worker threads while the actor table belongs to the loop,
            # which pops actors as sessions end: the read hops to the loop instead of iterating a
            # dict another thread may be resizing. `manager` is bound a few lines down and read
            # only when a locations request asks.
            scratch_in_use=lambda: active.chat_only and run_on_loop(lambda: bool(manager.live())),
        )
    except SettingsError as exc:
        return _give_up_startup_state(exc, cfg.data_dir, later)
    readiness.observe_sign_in_choice(_other_sign_in(services.settings))
    manager = SessionManager(
        config=DriverConfig(
            cli=services.argv_cli,
            vault=active.cwd,
            env=winproc.child_env(config.ENTRYPOINT, pin_updates=cfg.cli_source == "bundled"),
            cli_version=services.version,
            skills=vault_skills(active.cwd),
        ),
        state=state,
        claude_home=cfg.claude_home,
        readiness=readiness,
    )
    catalog = Catalog(cfg.claude_home, active.cwd)
    uvi: uvicorn.Server | None = None

    async def last_window_closed() -> None:
        # `until` is checked every poll, not just at the end: a second launch opens a window
        # into this same process and the reconnected page replays whatever is still streaming,
        # so from that moment on there IS somebody to answer a card and settle must stop
        # denying them. Without it the user would watch their own cards be refused a second
        # after they appeared, silently, for the rest of the grace.
        await manager.settle(WINDOW_GRACE_S, until=lambda: windows.admitted_count == 0)
        if windows.admitted_count:
            return  # a second launch opened a window during the grace: serve on
        try:
            if manager.retaining():
                # A chat the manager retains keeps its process, its cards and the app: the
                # others close now, and the hold, denying nothing, lasts until a window is back,
                # no live chat is retained any more or TASK_HOLD_S has passed.
                await manager.close_unretained()
                await manager.hold(TASK_HOLD_S, until=lambda: windows.admitted_count == 0)
                if windows.admitted_count:
                    return  # a second launch opened a window during the hold: serve on
            await manager.close_all()
        finally:
            # Checked again after the close, not only after the settle: closing every session
            # takes long enough for a second launch to land in it (settle returns at once when
            # nothing is running, so "closed it by accident, reopened it straight away" is the
            # ordinary way in). Exiting here would kill the window that just opened.
            if uvi is not None and windows.admitted_count == 0:
                uvi.should_exit = True

    host = (
        None
        if host_pid is None
        else window_host.WindowHost.from_stdio(
            host_pid, wake=signal.set, gone=lambda: _host_gone(windows, uvi)
        )
    )
    windows = _windows(
        cfg,
        edge,
        token,
        dev=dev,
        env=env,
        signal=signal,
        on_last_closed=last_window_closed,
        host=host,
    )
    app = server.create_app(
        config=cfg,
        token=token,
        manager=manager,
        catalog=catalog,
        static_dir=None if dev else STATIC_DIR,
        dev=dev,
        windows=windows,
        settings=services.settings,
        workspaces=spaces,
        agent_cli=compiled.agent_cli(),
        bootstrap=services.bootstrap,
        readiness=readiness,
        checks=services.checks,
        diagnostics_document=services.diagnostics_document,
        data=services.data,
        owner=services.owner,
        denied_roots=services.denied_roots,
        app_version=_app_version(),
        window_host=host,
    )

    def stop_magpie() -> None:
        # Not in the lifespan only: that runs after uvicorn has drained its connections, and a
        # forced quit during a long reply ends the process before the drain does. `create_app`
        # always sets `state.magpie`; a stand-in app without one still shuts down.
        magpie = getattr(getattr(app, "state", None), "magpie", None)
        if magpie is None:
            return
        try:
            magpie.stop()
        except Exception as exc:  # the shutdown goes on whatever Magpie's stop does
            logging.warning("magpie stop failed (%s)", type(exc).__name__)

    def before_exit() -> None:
        _stop_opening(windows)
        stop_magpie()

    uvi = _server(
        uvicorn.Config(
            app,
            host=config.HOST,  # not what binds any more (uvi.run takes the socket); kept true
            port=config.PORT,
            ws_max_size=server.WS_MAX_SIZE,
            reload=False,
            access_log=False,
            log_config=None,  # the default formatter calls sys.stdout.isatty(); None under
            # pythonw (the windowless launcher), which crashes it before anything can serve.
        ),
        before_exit=before_exit,
    )
    try:
        uvi.run(sockets=[sock])
    finally:
        if sys.platform != "win32":
            stop_magpie()
            _kill_windows(windows)
    if windows.first_failed:
        # The window never opened, so the app served nothing and has just shut itself down.
        return _give_up(MSG_NO_WINDOW, 6, later)
    return 0


def _serve_recovery(
    cfg: config.Config,
    edge: Path | None,
    env: Mapping[str, str],
    *,
    token: str,
    dev: bool,
    signal: winproc.Signal,
    sock: socket.socket,
    later: list[str],
    readiness: Readiness,
    log_file: Path,
    storage_refusal: str,
    host_pid: int | None = None,
) -> int:
    """Serve the read-only recovery shell for a store that could not be opened.

    Nothing here touches that store: no workspace, no settings, no sessions. The user gets the
    shell and the readiness document, and the launch ends with its window like any other —
    exit 0, because the app did open and did say what is wrong. A window that never opens is
    the one exception, and it reports exactly as it does for an ordinary launch: a shell nobody
    can see is not a recovery, it is the silent icon the message box exists for.
    """
    # The same probe an ordinary launch runs, behind the same predicate: `claude --version`
    # starts no session and bills nothing, and a healthy CLI reported as `failed` on the
    # recovery screen would send the user after a second problem they do not have.
    argv_cli = cli_argv(cfg.cli if readiness.may_spawn() else None, env)
    checks = ProviderChecks(
        readiness=readiness,
        cli_version=lambda: cli_version(argv_cli) if argv_cli else "",
        git_version=git_version,
    )
    uvi: uvicorn.Server | None = None

    async def last_window_closed() -> None:
        if uvi is not None and windows.admitted_count == 0:
            uvi.should_exit = True

    host = (
        None
        if host_pid is None
        else window_host.WindowHost.from_stdio(
            host_pid, wake=signal.set, gone=lambda: _host_gone(windows, uvi)
        )
    )
    windows = _windows(
        cfg,
        edge,
        token,
        dev=dev,
        env=env,
        signal=signal,
        on_last_closed=last_window_closed,
        host=host,
    )
    app = server.create_recovery_app(
        config=cfg,
        token=token,
        readiness=readiness,
        checks=checks,
        diagnostics_document=_diagnostics_document(
            readiness, cfg, None, None, None, log_file, storage_refusal=storage_refusal
        ),
        storage_refusal=storage_refusal,
        static_dir=None if dev else STATIC_DIR,
        dev=dev,
        windows=windows,
    )
    uvi = _server(
        uvicorn.Config(
            app,
            host=config.HOST,
            port=config.PORT,
            ws_max_size=server.WS_MAX_SIZE,
            reload=False,
            access_log=False,
            log_config=None,
        ),
        before_exit=lambda: _stop_opening(windows),
    )
    _reap_magpie(cfg.data_dir)
    try:
        uvi.run(sockets=[sock])
    finally:
        if sys.platform != "win32":
            _kill_windows(windows)
    if windows.first_failed:
        return _give_up(MSG_NO_WINDOW, 6, later)
    return 0


def _reap_magpie(data_dir: Path) -> None:
    """End the Magpie a killed earlier launch left running, as the ordinary app does as it
    starts, so its ports and loaded keys do not outlive that launch while the store cannot be
    opened. It reads only Magpie's own record, never the store. Never raises."""
    try:
        asyncio.run(Magpie(data_dir).reap_leftover())
    except Exception:
        logging.warning("magpie leftover check unavailable")


@dataclass(frozen=True, slots=True)
class Services:
    """Everything `create_app` needs besides the manager, the catalog and the windows."""

    settings: SettingsService
    bootstrap: VaultBootstrap
    checks: ProviderChecks
    diagnostics_document: Callable[..., dict[str, object]]
    data: server.DataServices
    argv_cli: tuple[str, ...]
    version: str
    owner: str
    # The one deny list, shared by the export service and the knowledge routes' backup and
    # restore, so the two cannot drift.
    denied_roots: Callable[[], Sequence[Path]]


def build_services(
    cfg: config.Config,
    env: Mapping[str, str],
    *,
    state: AppState,
    spaces: WorkspaceService,
    active: ActiveWorkspace,
    readiness: Readiness,
    launch_id: str,
    log_file: Path,
    scratch_in_use: Callable[[], bool],
) -> Services:
    """Build the `Services` for a store that is open and a workspace that is resolved.

    Extracted from `_serve_on` so the end-to-end harness serves the same routes the launcher
    does: without it `/api/v2/settings`, `/workspaces`, `/capabilities`, `/diagnostics*` and
    `/data/*` answer 404 in every real-backend run. `cfg` must already carry
    the active workspace (`vault=active.cwd`), because `provider_defaults` reads it lazily.

    Raises `SettingsError` when the store cannot answer the first chat-defaults read; the caller
    decides what to say about that.
    """
    # The per-launch ledger owner, minted here because this is the one function that builds both
    # the reset service and the `Services` the app's two ledgers are wired from, so the two
    # cannot drift. `launch_id` is deliberately not reused: it is a diagnostics identity the
    # export bundle and the diagnostics document publish, and giving it a durable-storage meaning
    # would couple two things that must be free to change independently.
    owner = str(uuid.uuid4())
    settings = SettingsService(
        state.store,
        # Read when called, never now: `user_defaults` probes the provider home, which is the
        # kind of work a launch should not repeat on every read.
        provider_defaults=lambda: user_defaults(cfg.claude_home, cfg.vault, env=env),
        on_chat_defaults=state.set_defaults,
    )
    state.set_defaults(settings.chat_defaults())
    # The single spawn predicate: with anything wrong on the provider side the argv is
    # empty, the version probe is skipped, and a session start is refused before any spawn.
    argv_cli = cli_argv(cfg.cli if readiness.may_spawn() else None, env)
    # One probe per launch, shared by the status bar and the sessions: `claude --version`
    # starts no session and bills nothing, but it is still a process.
    version = cli_version(argv_cli) if argv_cli else ""
    if argv_cli:
        readiness.observe_version(version)
    document = _diagnostics_document(readiness, cfg, settings, spaces, state.store, log_file)
    denied_roots = _denied_roots(cfg, spaces, active)
    return Services(
        settings=settings,
        bootstrap=VaultBootstrap(
            spaces,
            resources=_RESOURCES,
            home=Path.home(),
            identity=lambda: _starter_identity(settings),
        ),
        checks=ProviderChecks(
            readiness=readiness,
            cli_version=lambda: cli_version(argv_cli),
            git_version=git_version,
        ),
        diagnostics_document=document,
        data=_data_services(
            cfg,
            settings,
            spaces,
            active,
            store=state.store,
            document=document,
            log_file=log_file,
            launch_id=launch_id,
            scratch_in_use=scratch_in_use,
            owner=owner,
            denied_roots=denied_roots,
        ),
        argv_cli=argv_cli,
        version=version,
        owner=owner,
        denied_roots=denied_roots,
    )


# --- the adapters the services are built from ------------------------------------------------
#
# Each service module is written without importing its peers, so the callables that join them
# up live here, where every service is in scope, and in none of those modules.


def _diagnostics_document(
    readiness: Readiness,
    cfg: config.Config,
    settings: SettingsService | None,
    spaces: WorkspaceService | None,
    store: SessionStore | None,
    log_file: Path,
    *,
    storage_refusal: str = "recovery_required",
) -> Callable[..., dict[str, object]]:
    """The getter the diagnostics route reads with no arguments and the export service reads
    with its own two include flags.

    Paths are in it when the user's `privacy.diagnostics_include_paths` setting says so or the
    caller asks; with no readable settings (recovery) neither flag is on, which is the safer
    default. The storage state is the same one `/api/v2/capabilities` reports — which is why
    `storage_refusal` is here: with no store there is nothing left to ask, so the recovery
    caller passes the StateError code that refused it, exactly as it passes it to
    `create_recovery_app`. It is read only when `store is None`; an ordinary launch has a store
    to answer for itself and never supplies one.
    """

    def build(include_paths: bool = False, include_identity: bool = False) -> dict[str, object]:
        try:
            effective = None if settings is None else settings.read()
        except SettingsError:
            # The document is what the user reads to find out why settings cannot be read;
            # it renders with the settings block absent rather than failing with them.
            effective = None
        if effective is not None:
            include_paths = include_paths or bool(
                effective.effective.get("privacy.diagnostics_include_paths")
            )
        return diagnostics.diagnostics_document(
            readiness,
            storage=server.storage_readiness(store, cfg.data_dir, refusal=storage_refusal),
            settings=effective,
            workspaces=[] if spaces is None else spaces.list(),
            data_dir=cfg.data_dir,
            log_path=log_file,
            include_paths=include_paths,
            include_identity=include_identity,
        )

    return build


def _hide_previews(settings: SettingsService) -> bool:
    """Hidden when the setting says so, and hidden when the setting cannot be read: a store that
    answers nothing must not turn into instruction-file previews by default."""
    try:
        return bool(settings.read().effective.get("privacy.hide_previews"))
    except SettingsError:
        return True


def _other_sign_in(settings: SettingsService) -> bool:
    """Read once per launch: a change applies on the next launch. Off when the setting cannot be
    read, so an unreadable store keeps sessions on the Claude subscription."""
    return settings.flag("chat.other_sign_in") is True


def _starter_identity(settings: SettingsService) -> tuple[str, str]:
    """(assistant name, the user's display name): the two `identity.*` settings the "viki" starter
    renders into its `{{viki.*}}` tokens (starter.py's `starter_fill`). `VaultBootstrap`'s own
    default ("VIKI", "") covers a settings document that cannot be read."""
    try:
        effective = settings.read().effective
    except SettingsError:
        return "VIKI", ""
    return str(effective["identity.assistant_name"]), str(effective["identity.display_name"])


def _without_vault_override(env: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in env.items() if key != "VIKI_VAULT"}


def _redaction_roots(
    cfg: config.Config, spaces: WorkspaceService, active: ActiveWorkspace
) -> list[tuple[str, str]]:
    """Label every root an exported file could name. `diagnostics.redact` decides the order it
    applies them in; this only has to name them all.

    `active.cwd` is listed separately because a `VIKI_VAULT` override of an unregistered folder
    is an ephemeral workspace that is never a row, so `spaces.list()` does not contain it — and
    it is exactly the folder whose paths an export would otherwise carry out verbatim. Labels
    are deduplicated on the comparison key, so the ordinary case (the active root is also a
    registration) does not get two names for one directory.
    """
    labelled = [
        (str(cfg.data_dir), "<data>"),
        (str(cfg.claude_home), "<claude>"),
        (str(active.cwd), "<workspace>"),
        *(
            (workspace.root.display, f"<workspace-{index}>")
            for index, workspace in enumerate(spaces.list(kind=None), start=1)
        ),
        (str(Path.home()), "<home>"),
    ]
    seen: dict[str, tuple[str, str]] = {}
    for path, placeholder in labelled:
        seen.setdefault(os.path.normcase(path), (path, placeholder))
    return list(seen.values())


def _portable_workspace(workspace: Workspace) -> dict[str, object]:
    """The five fields an export carries about a registration; never its identity or command."""
    return {
        "name": workspace.name,
        "display_root": workspace.root.display,
        "policy": workspace.policy,
        "layout": workspace.layout,
        "portable_id": workspace.portable_id,
    }


def _app_version() -> str:
    try:
        return importlib.metadata.version("viki-agent")
    except importlib.metadata.PackageNotFoundError:
        return ""


def _denied_roots(
    cfg: config.Config, spaces: WorkspaceService, active: ActiveWorkspace
) -> Callable[[], list[Path]]:
    """The folders an export, a backup or a restore never writes into, read when called."""
    return lambda: [
        cfg.data_dir,
        cfg.claude_home,
        Path.home() / ".codex",
        # The working directory this launch actually serves. A `VIKI_VAULT` override of an
        # unregistered folder is an ephemeral workspace and never a row, so the listing below
        # cannot see it — and without this an export could be written into the live vault the
        # session is running in.
        active.cwd,
        *(Path(w.root.display) for w in spaces.list(kind=None)),
    ]


def _data_services(
    cfg: config.Config,
    settings: SettingsService,
    spaces: WorkspaceService,
    active: ActiveWorkspace,
    *,
    store: SessionStore,
    document: Callable[..., dict[str, object]],
    log_file: Path,
    launch_id: str,
    scratch_in_use: Callable[[], bool],
    owner: str,
    denied_roots: Callable[[], Sequence[Path]],
) -> server.DataServices:
    return (
        LocationsService(
            workspaces=spaces.list,
            data_dir=cfg.data_dir,
            claude_home=cfg.claude_home,
            scratch_in_use=scratch_in_use,
        ),
        ExportService(
            diagnostics_document=document,
            redact=lambda text, roots: diagnostics.redact(text, roots=roots),
            roots=lambda: _redaction_roots(cfg, spaces, active),
            settings_export=settings.export_values,
            settings_specs=KEYS,
            workspaces_export=lambda: [_portable_workspace(w) for w in spaces.list()],
            denied_roots=denied_roots,
            destination_guard=spaces.registration_lock,
            log_path=log_file,
            app_version=_app_version(),
            launch_id=launch_id,
        ),
        ImportService(
            settings_specs=KEYS,
            current_settings=settings.import_snapshot,
            patch=settings.patch,
        ),
        ResetService(
            store=store,
            active_workspace=lambda: active,
            settings_reset=settings.reset,
            # Never `workspaces.scratch_root()` here: it creates the folder and walks its chain,
            # and a vault launch has no business doing either (nor crashing on a chain it never
            # uses). A chat-only launch already holds the created, checked root as its cwd; for
            # any other launch this is the path a reset would clear, and a folder that was never
            # created reads as empty.
            scratch_root=active.cwd if active.chat_only else workspaces.scratch_path(cfg.data_dir),
            scratch_in_use=scratch_in_use,
            owner=owner,
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    # Dispatched before anything else, including the VIKI_AGENT_* scrub below: a chat's own
    # shell runs `viki-agent <command>` as this same entry point, and the CLI reads those
    # variables from its own environment (ADR-086, AC-10).
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in agent_cli.COMMANDS:
        return agent_cli.main(list(args))
    # Every give-up path below is windowless: there is no console and no parent to report to, so
    # a bare `return 2` leaves the user double-clicking an icon that does nothing at all. The
    # boxes are queued rather than shown here for the reason _give_up documents, and these run
    # before the single-instance lock is taken, so a modal cannot make the next launch think the
    # app is alive but deaf.
    later: list[str] = []
    try:
        return _main(argv, later)
    finally:
        for message in later:
            _alert(message)


def _main(argv: Sequence[str] | None, later: list[str]) -> int:
    # Once, in the process environment, so every child (checks, claude and its MCP servers and
    # hooks) inherits it.
    winproc.prepend_user_bin(os.environ)
    env = winproc.scrub_env(os.environ)
    # scrub_env returns a copy; the contract is to scrub the launcher's own process environment,
    # since anything winproc.open_external hands off (open.url's vscode:// handler, for one) or
    # that reads os.environ directly (the SDK's CLAUDE_CODE_STREAM_CLOSE_TIMEOUT) inherits it,
    # not this local variable.
    for key in set(os.environ) - env.keys():
        del os.environ[key]
    try:
        log_dir = config.data_dir(env) / "logs"
    except config.ConfigError as exc:
        # A VIKI_DATA_DIR inside a vault, ~/.claude or ~/.codex: no log file may be created
        # there (hard rule 1). Admit the default fallback separately: that location may itself
        # be protected. Reusing data_dir would repeat the refused override.
        # A windowless launch still reports the original failure through _alert if no log can
        # be written.
        # Unwritable too (a read-only drive, a locked profile): the message box below is then
        # the only thing the user gets, which is exactly why it exists.
        with contextlib.suppress(config.ConfigError, OSError):
            fallback = config.admit_data_dir(config.default_data_dir(env), env)
            _configure_logging(fallback / "logs")
        logging.error("%s", exc)
        return _give_up(MSG_BAD_DATA_DIR, 2, later)
    # Before config.load(): whatever it logs must reach the log file, since the windowless
    # launcher has no stderr on Windows (on macOS and Linux only _alert's copy of the box text
    # reaches a stderr when there is one). This can fail on its own — a read-only drive, a name
    # taken by a file — and under pythonw an exception out of main() is seen by nobody at all, so
    # it gets a box of its own: the folder is allowed, it just cannot be written, and the box
    # names it because there is no log file to look in.
    try:
        _configure_logging(log_dir)
    except OSError as exc:
        logging.error("log directory unusable (%s)", type(exc).__name__)
        return _give_up(MSG_NO_LOG_DIR.format(logs=log_dir), 2, later)
    if sys.platform != "win32":
        _raise_nofile_limit()
    # A missing vault, a missing or unusable CLI and CLAUDE_CONFIG_DIR are recorded in the
    # Config rather than raised, and the billing preflight runs after the active workspace
    # is known: none of them stops the launch.
    cfg = config.load(env)

    lock: winproc.InstanceLock | None = None
    try:
        try:
            lock = winproc.single_instance(MUTEX_NAME)
        except PermissionError:
            if sys.platform == "win32":
                raise  # a mutex this account may not open, not a runtime folder: logged below
            logging.error("runtime folder is not private")
            return _give_up(MSG_RUNTIME_DIR, 2, later)
        if lock is None:
            if _wake_running_instance():
                return 0
            lock = _lock_when_free(MUTEX_NAME, WAKE_WAIT_S)  # the holder may be on its way out
            if lock is None:
                return _give_up(MSG_NOT_LISTENING, 3, later)
        if sys.platform != "win32":
            # The sealing key lives in the OS keychain. Installed holding the lock, and visited
            # only in _serve_on, after every refusal but a failed first settings read and a
            # first window that never opens. A second launch that only wakes the running one
            # returns above and never reaches this line, so it never visits the keychain.
            protection.set_key_source(protection.keychain_source(cfg.data_dir))
        return _serve(
            cfg,
            env,
            args=sys.argv[1:] if argv is None else argv,
            later=later,
        )
    except Exception:
        # Nothing else surfaces this in the windowless launcher: no console, no parent to report
        # to. Logged with the full traceback, then re-raised so the exit code still reflects it.
        logging.exception("viki-agent failed to start")
        raise
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
