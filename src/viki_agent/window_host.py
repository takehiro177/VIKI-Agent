"""The window-host link: the desktop app that started the server shows its windows.

One JSON object per line each way. Server to app, stdout: `{"type":"open","id":N,"url":U}`,
`{"type":"close","id":N}`. App to server, stdin: `{"type":"closed","id":N}`, `{"type":"wake"}`.
The server shows and hides Magpie with `{"type":"magpie","id":N,"url":U}` and
`{"type":"magpie_close","id":N}`. Its external links come back as
`{"type":"magpie_open_url","id":N,"url":U}` and open through `winproc`.
`{"type":"update_check","id":N,"manual":B}` asks the app to check for an update in window N:
`manual` is true when the window asked (`updates.check`), false for `run_update_checks`.
End of stdin: the app is gone.
`U` = `http://127.0.0.1:8765/?ui=<32 hex>#token=<token>[&session=<uuid>]`.

`WindowHost.spawn` is `WindowManager`'s `Spawn` (argv = `[U]`). `HostWindow.wait_tree` ends on
`closed`; `kill_tree` sends `close`; `HostWindow.job` = `HostProcess(host_pid)`, so `holds()` and
the picker owner name the app's main process. `from_stdio` keeps a private dup of fd 1 for
messages and points fd 1 at the null device, so no stray print can corrupt or leak a message.

A line from the app that is neither message, or longer than MAX_LINE bytes, is logged with fixed
text and skipped. No log line or exception here carries the URL.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import functools
import itertools
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qs, urlsplit

from viki_agent import winproc

log = logging.getLogger(__name__)

MAX_LINE = 4096  # bytes in one line from the app, its newline included
IGNORED = "ignored a message the desktop app sent"
UPDATE_FIRST_S = 60.0  # from the server's start to the first automatic update check
UPDATE_EVERY_S = 21600.0  # six hours between automatic update checks


def _app_gone() -> OSError:
    return OSError(errno.EPIPE, "the desktop app is gone")


def _external_url(url: str) -> bool:
    if (
        len(url) > 2048
        or "\\" in url
        or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in url)
    ):
        return False
    try:
        value = urlsplit(url)
        return (
            value.scheme in ("http", "https")
            and bool(value.hostname)
            and value.username is None
            and value.password is None
            and (value.hostname, value.port)
            not in (("127.0.0.1", 8765), ("localhost", 8765), ("::1", 8765))
        )
    except ValueError:
        return False


def _call(callback: Callable[[], None]) -> None:
    """Run the launcher's callback on the loop; its exception is logged by type name only."""
    try:
        callback()
    except Exception as exc:
        log.warning("a window-host callback failed (%s)", type(exc).__name__)


@dataclass(frozen=True)
class HostProcess:
    """The desktop app's main process, in the place of a window's Job."""

    pid: int

    def holds(self, pid: int) -> bool:
        return pid == self.pid


class HostWindow:
    """One window the desktop app shows, in the place of a winproc.Child."""

    def __init__(
        self, window_id: int, job: HostProcess, send: Callable[[dict[str, object]], None]
    ) -> None:
        self.id = window_id
        self.job = job
        self._send = send
        self._closed = asyncio.Event()

    async def wait_tree(self) -> None:
        """Return once the app says this window closed."""
        await self._closed.wait()

    def kill_tree(self) -> None:
        """Ask the app to close this window. An app that cannot take the message is gone, and
        its windows with it."""
        with contextlib.suppress(OSError):
            self._send({"type": "close", "id": self.id})

    def release(self) -> None:
        """Nothing to release: the window is the app's."""


class WindowHost:
    """The server's end of the link. Its windows and its end are touched on the event loop
    only; the reader thread hands each message there."""

    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        host_pid: int,
        wake: Callable[[], None],
        gone: Callable[[], None],
        open_url: Callable[[str], None] = winproc.open_external,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._job = HostProcess(host_pid)
        self._wake = wake
        self._gone = gone
        self._open_url = open_url
        self._ended = False
        self._ids = itertools.count(1)
        self._windows: dict[int, HostWindow] = {}
        self._uis: dict[int, str] = {}
        self._magpie_views: dict[int, dict[str, object] | None] = {}
        self._magpie_canceled: dict[int, set[str] | None] = {}
        self._thread: threading.Thread | None = None

    @classmethod
    def from_stdio(
        cls, host_pid: int, *, wake: Callable[[], None], gone: Callable[[], None]
    ) -> WindowHost:
        """The link over this process's stdin and stdout, which the app holds. Call it once per
        process: a second call would take the null device for its stdout."""
        if sys.stdout is not None:
            sys.stdout.flush()
        writer = os.fdopen(os.dup(1), "wb")
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, 1)
        finally:
            os.close(null)
        return cls(open(0, "rb", closefd=False), writer, host_pid=host_pid, wake=wake, gone=gone)

    async def spawn(
        self, argv: Sequence[str], *, cwd: str | Path, env: Mapping[str, str], pipes: bool
    ) -> HostWindow:
        """Ask the app to open a window on `argv`'s one element, the app URL. Raises OSError
        EPIPE once the app is gone or when it cannot take the message."""
        del cwd, env, pipes  # the window is the app's: no process starts here
        if isinstance(argv, str) or len(argv) != 1 or not isinstance(argv[0], str):
            raise ValueError("a hosted window opens exactly one URL")
        if self._ended:
            raise _app_gone()
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._read,
                args=(asyncio.get_running_loop(),),
                name="viki-window-host",
                daemon=True,
            )
            self._thread.start()
        window = HostWindow(next(self._ids), self._job, self._send)
        self._windows[window.id] = window
        ui = parse_qs(urlsplit(argv[0]).query).get("ui", [])
        if len(ui) == 1 and re.fullmatch(r"[0-9a-f]{32}", ui[0]):
            self._uis[window.id] = ui[0]
        try:
            self._send({"type": "open", "id": window.id, "url": argv[0]})
        except OSError:
            del self._windows[window.id]
            self._uis.pop(window.id, None)
            raise _app_gone() from None
        return window

    def has_ui(self, ui: str | None) -> bool:
        return not self._ended and ui is not None and ui in self._uis.values()

    def _ui_window(self, ui: str | None) -> int:
        if self.has_ui(ui):
            return next(window_id for window_id, value in self._uis.items() if value == ui)
        raise ValueError("the desktop window is unavailable")

    def prepare_magpie(self, ui: str | None, view: dict[str, object]) -> bool:
        """Claim this opening before startup awaits, so navigation can cancel its presentation."""
        window_id = self._ui_window(ui)
        canceled = self._magpie_canceled.get(window_id, set())
        if canceled is None:
            return False
        view_id = str(view["id"])
        if view_id in canceled:
            canceled.remove(view_id)
            return False
        self._magpie_views[window_id] = view
        return True

    def show_magpie(self, ui: str | None, url: str, view_id: str | None = None) -> None:
        window_id = self._ui_window(ui)
        message: dict[str, object] = {"type": "magpie", "id": window_id, "url": url}
        if view_id is not None:
            view = self._magpie_views.get(window_id)
            if view is None or view.get("id") != view_id:
                return
            message["view"] = view
        self._send(message)

    def update_magpie(self, ui: str | None, view: dict[str, object]) -> None:
        window_id = self._ui_window(ui)
        current = self._magpie_views.get(window_id)
        if current is not None and current.get("id") == view.get("id"):
            self._magpie_views[window_id] = view
            self._send({"type": "magpie_view", "id": window_id, "view": view})

    def hide_magpie(self, ui: str | None, view_id: str | None = None) -> None:
        window_id = self._ui_window(ui)
        message: dict[str, object] = {"type": "magpie_close", "id": window_id}
        if view_id is not None:
            current = self._magpie_views.get(window_id)
            if current is None or current.get("id") != view_id:
                # A close can arrive before its opening POST finishes reading. Keep that
                # cancellation until prepare consumes it; saturation refuses presentation.
                canceled = self._magpie_canceled.setdefault(window_id, set())
                if canceled is not None:
                    canceled.add(view_id)
                    if len(canceled) > 128:
                        self._magpie_canceled[window_id] = None
                return
            message["view_id"] = view_id
        elif self._magpie_views.get(window_id) is not None:
            return
        self._magpie_views.pop(window_id, None)
        self._send(message)

    def ask_update_check(self, ui: str | None, *, manual: bool) -> bool:
        """Ask the app to check for an update in the window whose ui nonce is `ui`, or in the
        newest window when `ui` is None. False, with nothing sent, when the link has ended, no
        such window is open, or the app cannot take the message."""
        if self._ended:
            return False
        if ui is None:
            window_id = max(self._windows, default=None)
        elif self.has_ui(ui):
            window_id = self._ui_window(ui)
        else:
            return False
        if window_id is None:
            return False
        try:
            self._send({"type": "update_check", "id": window_id, "manual": manual})
        except OSError:
            return False
        return True

    def _magpie_open_url(self, window_id: int, url: str) -> None:
        if self._ended or window_id not in self._windows:
            return

        async def open_url() -> None:
            try:
                await asyncio.to_thread(self._open_url, url)
            except Exception:
                log.warning("couldn't open a Magpie link")

        asyncio.create_task(open_url())

    def _send(self, message: dict[str, object]) -> None:
        self._writer.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
        self._writer.flush()

    def _read(self, loop: asyncio.AbstractEventLoop) -> None:
        """The reader thread: each accepted message, then the end of the link, runs on `loop`."""
        try:
            for message in self._messages():
                loop.call_soon_threadsafe(message)
            loop.call_soon_threadsafe(self._end)
        except RuntimeError:
            return  # the loop is closed: nobody is left to tell

    def _messages(self) -> Iterator[Callable[[], None]]:
        """What each accepted line asks of the loop, until the end of stdin or a read error."""
        try:
            while line := self._reader.readline(MAX_LINE):
                if not line.endswith(b"\n"):
                    # Longer than MAX_LINE, or cut off by the end of stdin: drop the rest of it,
                    # up to its newline.
                    while (rest := self._reader.readline(MAX_LINE)) and not rest.endswith(b"\n"):
                        pass
                    message = None
                else:
                    message = self._accepted(line)
                if message is None:
                    log.warning(IGNORED)
                else:
                    yield message
        except OSError:
            return  # a broken stdin: the app is gone, as at its end

    def _accepted(self, line: bytes) -> Callable[[], None] | None:
        """What `line` asks of the loop when it is exactly one supported app message."""
        try:
            message = json.loads(line)
        except ValueError, RecursionError:  # RecursionError: nesting too deep for the stack
            return None
        match message:
            case {"type": "wake", **rest} if not rest:
                return functools.partial(_call, self._wake)
            case {"type": "closed", "id": window_id, **rest} if (
                not rest and type(window_id) is int and window_id >= 1
            ):
                return functools.partial(self._closed, window_id)
            case {"type": "magpie_open_url", "id": window_id, "url": url, **rest} if (
                not rest
                and type(window_id) is int
                and window_id >= 1
                and isinstance(url, str)
                and _external_url(url)
            ):
                return functools.partial(self._magpie_open_url, window_id, url)
        return None

    def _closed(self, window_id: int) -> None:
        """On the loop: the app closed window `window_id`; an id it never had is ignored."""
        if (window := self._windows.pop(window_id, None)) is not None:
            self._uis.pop(window_id, None)
            self._magpie_views.pop(window_id, None)
            self._magpie_canceled.pop(window_id, None)
            window._closed.set()

    def _end(self) -> None:
        """On the loop, once: the app closed its end of the link."""
        self._ended = True
        _call(self._gone)


async def run_update_checks(
    host: WindowHost, allowed: Callable[[], bool | None], *, first_s: float, every_s: float
) -> None:
    """The automatic update check, until cancelled: after `first_s`, then every `every_s`, ask
    the newest window while `allowed()`, run off the loop, is True. None (an unreadable
    setting) asks nothing; a failure is logged with fixed text and the next turn runs."""
    await asyncio.sleep(first_s)
    while True:
        try:
            if await asyncio.to_thread(allowed) is True:
                host.ask_update_check(None, manual=False)
        except Exception:
            log.warning("update check unavailable")
        await asyncio.sleep(every_s)
