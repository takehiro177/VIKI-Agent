"""Process plumbing: the only module allowed to start a process (hard rule 2, on every OS).

`spawn()` starts a long-lived child (claude.exe) windowless, with byte pipes, inside a `Job`
that owns its tree. On Windows the Job is a kill-on-close Job Object, so closing it reaps the
whole tree, including hook processes that claude.exe started; on POSIX it is the child's own
process group (the child leads a new session), killed as a group, with no kill-on-close, but it
holds only the descendants that stay in that group: the bundled CLI starts its hooks and its
Bash tool shells in their own sessions, so a killed claude can leave them running.
`Child.wait_tree()` ends when that whole tree has (a browser hands off to its own tree and the
launched pid exits at once). `run()` and `git()` run short commands the same way with
UTF-8 output, and `untrusted_git()` makes `git()` read a folder nobody has trusted;
`open_external()` hands a URL to the default handler.
`start_shell()` runs a check's command through cmd.exe on Windows or /bin/sh on POSIX, inside a
Job. `powershell_argv()` names Windows PowerShell under `system_dir()`. `open_terminal()`
opens the OS terminal in a folder, outside any Job. `open_console()` runs one command
in a terminal window of its own, outside any Job, and keeps it open: on Windows such a console,
each argument a PowerShell literal; on macOS Terminal, through a self-removing `.command` file,
so Terminal's own environment applies; on Linux the first of `xdg-terminal-exec` and
`x-terminal-emulator` on the given PATH. For a preview's port
ownership, `tcp_listeners()` gives the owning pids of a port's TCP listeners and
`Job.holds()` tells whether a pid is inside a Job. `single_instance()` and
`Signal` are the named mutex and event behind the one-running-app rule: a second launch
takes neither the lock nor the port, it just sets the signal and the running app opens another
window. On POSIX they are a flock and a FIFO in a private runtime directory, and
`process_start_time()` reads the CLI's procStart form: macOS `ps`, Linux /proc/<pid>/stat.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import io
import logging
import os
import re
import select
import shlex
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Collection, Iterator, Mapping, MutableMapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010

_log = logging.getLogger(__name__)

# The POSIX runtime folder's name: Signal keeps its FIFOs in it, and single_instance() its locks.
RUNTIME_FOLDER = "viki-agent"

# Variables a parent Claude Code session (or SDK host) leaks into its children. Left in place
# they make a spawned claude.exe believe it is nested, reuse the parent's session or effort,
# or bill through an API key instead of the user's subscription login.
_STRIP_EXACT = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_PID",
        "CLAUDE_EFFORT",
        "CLAUDE_SESSION_ID",
        "AI_AGENT",
        "TRACEPARENT",
        "TRACESTATE",
        "MCP_CONNECTION_NONBLOCKING",
        "ENABLE_IDE_INTEGRATION",
    }
)
# Provider markers, optional bridge configuration and an inherited VIKI Agent CLI key do not
# belong in child environments; a chat's own key joins its process's environment later.
_STRIP_PREFIXES = (
    "CLAUDE_CODE_",
    "CLAUDE_AGENT_SDK_",
    "ANTHROPIC_",
    "VIKI_MATTERMOST_",
    "VIKI_SLACK_",
    "VIKI_DISCORD_",
    "VIKI_AGENT_",
)


def _is_leaked(name: str) -> bool:
    upper = name.upper()  # Windows environment names are case-insensitive.
    return upper in _STRIP_EXACT or upper.startswith(_STRIP_PREFIXES)


def scrub_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return a copy without inherited provider markers, bridge configuration or CLI keys."""
    return {k: v for k, v in env.items() if not _is_leaked(k)}


def child_env(
    entrypoint: str, base: Mapping[str, str] | None = None, *, pin_updates: bool = True
) -> dict[str, str]:
    """Environment for a spawned claude.exe: the scrubbed base plus VIKI's own markers.

    `pin_updates` sets DISABLE_AUTOUPDATER=1, for the SDK's copy of the CLI: the build the SDK
    pins inside its own environment. False is for the user's own install, so its own update
    setting holds and updates reach users who chat only through VIKI: the base's value, or its
    absence, stays."""
    env = scrub_env(os.environ if base is None else base)
    if pin_updates:
        env["DISABLE_AUTOUPDATER"] = "1"
    env["CLAUDE_AGENT_SDK_CLIENT_APP"] = "viki-agent"
    env["CLAUDE_CODE_ENTRYPOINT"] = entrypoint
    return env


def prepend_user_bin(
    env: MutableMapping[str, str],
    *,
    platform: str = sys.platform,
    home: Path | None = None,
    isdir: Callable[[str], bool] = os.path.isdir,
) -> None:
    """On macOS and Linux, put the usual user program folders that exist and are not on PATH yet
    in front of it: `~/.local/bin`, `/opt/homebrew/bin`, `/opt/homebrew/sbin`, `/usr/local/bin`.

    An app opened from Finder, the Dock or a desktop menu gets the system's minimal PATH (a
    shell's rc files never ran), so saved checks and the MCP servers and hooks of Claude Code
    could not find `npm`, `uv` or Homebrew tools. A launch from a terminal already has these
    folders, so nothing changes there. The user's shell is not run to read its PATH."""
    if platform == "win32":
        return
    try:
        folders = [str((home or Path.home()) / ".local" / "bin")]
    except RuntimeError:  # no home folder to resolve
        folders = []
    folders += ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
    path = env.get("PATH") or os.defpath
    entries = path.split(os.pathsep)
    missing = [f for f in folders if f not in entries and isdir(f)]
    if missing:
        env["PATH"] = os.pathsep.join([*missing, path])


def resolve_exe(path: str | os.PathLike[str]) -> Path:
    """Validate an executable for direct use: a real file, never a Windows batch shim, and
    executable on POSIX."""
    exe = Path(path)
    if sys.platform == "win32" and exe.suffix.lower() in {".cmd", ".bat"}:
        raise ValueError(f"refusing batch shim {exe}: point at the native .exe instead")
    if not exe.is_file() or (sys.platform != "win32" and not os.access(exe, os.X_OK)):
        raise FileNotFoundError(f"executable not found: {exe}")
    return exe


def find_on_path(name: str, path: str | None) -> Path | None:
    """The first `name` in an entry of `path` (a PATH value) that `resolve_exe` accepts, or
    None. Each entry loses its surrounding whitespace, and on Windows its surrounding quotes;
    then only absolute entries count, so neither an empty or relative entry nor the current
    folder can answer."""
    for raw in (path or "").split(os.pathsep):
        entry = raw.strip()
        if sys.platform == "win32":
            entry = entry.strip('"')
        if not os.path.isabs(entry):  # an empty entry is not absolute either
            continue
        try:
            return resolve_exe(Path(entry) / name)
        except ValueError, OSError:
            continue
    return None


if sys.platform == "win32":
    # --- Win32 via ctypes ---------------------------------------------------------------------
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    CREATE_SUSPENDED = 0x00000004
    _CREATION_FLAGS = CREATE_NO_WINDOW | CREATE_SUSPENDED
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SUSPEND_RESUME = 0x0800
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _WAIT_TIMEOUT = 0x102
    _WAIT_OBJECT_0 = 0
    _ERROR_ALREADY_EXISTS = 183
    _EVENT_MODIFY_STATE = 0x0002

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in ("Read", "Write", "Other", "ReadBytes", "WriteBytes", "OtherBytes")
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BasicAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", wintypes.LARGE_INTEGER),
            ("TotalKernelTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
            ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    def _fn(name: str, restype: Any, *argtypes: Any, dll: Any = _kernel32) -> Any:
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = argtypes
        return fn

    _CreateJobObjectW = _fn("CreateJobObjectW", wintypes.HANDLE, wintypes.LPVOID, wintypes.LPCWSTR)
    _SetInformationJobObject = _fn(
        "SetInformationJobObject",
        wintypes.BOOL,
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    _AssignProcessToJobObject = _fn(
        "AssignProcessToJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE
    )
    _QueryInformationJobObject = _fn(
        "QueryInformationJobObject",
        wintypes.BOOL,
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPDWORD,
    )
    _TerminateJobObject = _fn("TerminateJobObject", wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)
    _OpenProcess = _fn(
        "OpenProcess", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
    )
    _CloseHandle = _fn("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
    _WaitForSingleObject = _fn(
        "WaitForSingleObject", wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD
    )
    # Resumes every thread of a process created suspended (ntdll; stable since Windows XP).
    _NtResumeProcess = _fn("NtResumeProcess", ctypes.c_long, wintypes.HANDLE, dll=_ntdll)
    _GetProcessTimes = _fn(
        "GetProcessTimes",
        wintypes.BOOL,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    _CreateMutexW = _fn(
        "CreateMutexW", wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR
    )
    _ReleaseMutex = _fn("ReleaseMutex", wintypes.BOOL, wintypes.HANDLE)
    _CreateEventW = _fn(
        "CreateEventW",
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    _OpenEventW = _fn(
        "OpenEventW", wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR
    )
    _SetEvent = _fn("SetEvent", wintypes.BOOL, wintypes.HANDLE)
    _GetSystemDirectoryW = _fn("GetSystemDirectoryW", wintypes.UINT, wintypes.LPWSTR, wintypes.UINT)
    _IsProcessInJob = _fn(
        "IsProcessInJob", wintypes.BOOL, wintypes.HANDLE, wintypes.HANDLE, wintypes.PBOOL
    )
    _GetExtendedTcpTable = _fn(
        "GetExtendedTcpTable",
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.PDWORD,
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
        dll=ctypes.WinDLL("iphlpapi", use_last_error=True),
    )
    _TCP_TABLE_OWNER_PID_LISTENER = 3
    _AF_INET6 = 23  # Windows' value; socket.AF_INET6 differs elsewhere
    _ERROR_INSUFFICIENT_BUFFER = 122

    class _TcpRow(ctypes.Structure):  # MIB_TCPROW_OWNER_PID
        _fields_ = [
            (name, wintypes.DWORD)
            for name in (
                "dwState",
                "dwLocalAddr",
                "dwLocalPort",
                "dwRemoteAddr",
                "dwRemotePort",
                "dwOwningPid",
            )
        ]

    class _Tcp6Row(ctypes.Structure):  # MIB_TCP6ROW_OWNER_PID
        _fields_ = [
            ("ucLocalAddr", ctypes.c_ubyte * 16),
            ("dwLocalScopeId", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("ucRemoteAddr", ctypes.c_ubyte * 16),
            ("dwRemoteScopeId", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwState", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    def _listener_rows(family: int, row: type[ctypes.Structure]) -> ctypes.Array[Any]:
        """One family's listener table: asked with no room first, which answers the size, then
        up to 3 more times while it grows between calls."""
        size = wintypes.DWORD(0)
        buffer = ctypes.create_string_buffer(0)
        for _ in range(4):
            rc = _GetExtendedTcpTable(buffer, size, False, family, _TCP_TABLE_OWNER_PID_LISTENER, 0)
            if rc == 0:
                count = wintypes.DWORD.from_buffer(buffer).value
                return (row * count).from_buffer(buffer, ctypes.sizeof(wintypes.DWORD))
            if rc != _ERROR_INSUFFICIENT_BUFFER:
                raise ctypes.WinError(rc, "GetExtendedTcpTable")
            buffer = ctypes.create_string_buffer(size.value)
        raise ctypes.WinError(_ERROR_INSUFFICIENT_BUFFER, "GetExtendedTcpTable")

    def _check(ok: object, what: str) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error(), what)

    class Job:
        """A kill-on-close Job Object. Closing or terminating it ends every process inside."""

        def __init__(self, *, memory_limit_bytes: int | None = None) -> None:
            if memory_limit_bytes is not None and (
                type(memory_limit_bytes) is not int or not 1 <= memory_limit_bytes <= (1 << 63) - 1
            ):
                raise ValueError("invalid job memory limit")
            handle = _CreateJobObjectW(None, None)
            _check(handle, "CreateJobObjectW")
            self._handle: int | None = handle
            info = _ExtendedLimits()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if memory_limit_bytes is not None:
                info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
                info.JobMemoryLimit = memory_limit_bytes
            ok = _SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                err = ctypes.get_last_error()
                self.close()
                raise ctypes.WinError(err, "SetInformationJobObject")

        def adopt(self, pid: int) -> None:
            """Put a process created with CREATE_SUSPENDED into the job, then let it run. It cannot
            start anything before it is inside, so nothing it ever starts escapes the job."""
            proc = _OpenProcess(
                _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_SUSPEND_RESUME, False, pid
            )
            _check(proc, "OpenProcess")
            try:
                _check(_AssignProcessToJobObject(self._handle, proc), "AssignProcessToJobObject")
                if (status := _NtResumeProcess(proc)) != 0:
                    raise OSError(f"NtResumeProcess failed: NTSTATUS 0x{status & 0xFFFFFFFF:08X}")
            finally:
                _CloseHandle(proc)

        def active_processes(self) -> int:
            """How many processes are alive inside the job right now (0 once it is closed)."""
            if self._handle is None:
                return 0
            info = _BasicAccounting()
            ok = _QueryInformationJobObject(
                self._handle,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
                None,
            )
            _check(ok, "QueryInformationJobObject")
            return int(info.ActiveProcesses)

        def holds(self, pid: int) -> bool:
            """Whether process `pid` is inside the job: False once the job is closed or when
            the process cannot be opened (gone, or not ours to query)."""
            if self._handle is None:
                return False
            proc = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not proc:
                return False
            try:
                inside = wintypes.BOOL()
                _check(_IsProcessInJob(proc, self._handle, ctypes.byref(inside)), "IsProcessInJob")
                return bool(inside.value)
            finally:
                _CloseHandle(proc)

        def terminate(self, exit_code: int = 1) -> None:
            """Kill every process in the job now. Safe to call more than once."""
            if self._handle is not None:
                _TerminateJobObject(self._handle, exit_code)

        def close(self) -> None:
            """Release the job; kill-on-close reaps anything still inside. Idempotent."""
            if self._handle is not None:
                handle, self._handle = self._handle, None
                _CloseHandle(handle)

    def process_start_time(pid: int) -> int | str | None:
        """Creation time of a running process as FILETIME ticks, or None if it isn't running.

        Claude Code records the same value as `procStart` in ~/.claude/sessions/<pid>.json,
        so equality proves the pid was not recycled by an unrelated process.
        """
        proc = _OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
        if not proc:
            return None
        try:
            if _WaitForSingleObject(proc, 0) != _WAIT_TIMEOUT:
                return None  # signalled: the process has exited
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            ok = _GetProcessTimes(
                proc,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            _CloseHandle(proc)

    def process_start_times(pids: Collection[int]) -> dict[int, int | str]:
        """`process_start_time` for each of `pids`, keeping only the ones still alive."""
        result: dict[int, int | str] = {}
        for pid in pids:
            started = process_start_time(pid)
            if started is not None:
                result[pid] = started
        return result

    # --- one running app --------------------------------------------------------------------------

    class InstanceLock:
        """The named mutex that makes this process the only running app. release() (idempotent)
        lets the next launch take it; the OS releases it anyway when the process ends."""

        def __init__(self, handle: int) -> None:
            self._handle: int | None = handle

        def release(self) -> None:
            if self._handle is not None:
                handle, self._handle = self._handle, None
                _ReleaseMutex(handle)
                _CloseHandle(handle)

    def single_instance(name: str) -> InstanceLock | None:
        """Take the mutex `name`, or None when another process already holds it."""
        handle = _CreateMutexW(None, True, name)
        _check(handle, "CreateMutexW")
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            _CloseHandle(handle)  # a handle to theirs, without ownership
            return None
        return InstanceLock(handle)

    class Signal:
        """A named auto-reset event: a set() is remembered until one wait() consumes it, and sets
        that arrive with no wait in between collapse into one wake."""

        def __init__(self, handle: int) -> None:
            self._handle: int | None = handle

        @classmethod
        def create(cls, name: str) -> Signal:
            handle = _CreateEventW(None, False, False, name)
            _check(handle, "CreateEventW")
            return cls(handle)

        @classmethod
        def open(cls, name: str) -> Signal | None:
            """The event `name` another process created, or None when there is none."""
            handle = _OpenEventW(_EVENT_MODIFY_STATE | _SYNCHRONIZE, False, name)
            return cls(handle) if handle else None

        def set(self) -> None:
            if self._handle is not None:
                _check(_SetEvent(self._handle), "SetEvent")

        def wait(self, timeout_ms: int) -> bool:
            """Block the calling thread up to `timeout_ms`: True when the event was set, False on
            the timeout. Raises once the signal is closed or the wait itself fails, so a waiter
            never spins on a dead handle."""
            if self._handle is None:
                raise RuntimeError("the signal is closed")
            rc = _WaitForSingleObject(self._handle, timeout_ms)
            if rc == _WAIT_OBJECT_0:
                return True
            if rc == _WAIT_TIMEOUT:
                return False
            raise ctypes.WinError(ctypes.get_last_error(), "WaitForSingleObject")

        def close(self) -> None:
            if self._handle is not None:
                handle, self._handle = self._handle, None
                _CloseHandle(handle)

else:
    # --- POSIX: a process group stands in for the Job -------------------------------------
    import fcntl
    import termios

    # Popen takes no creation flags off Windows; 0 is its default.
    _CREATION_FLAGS = 0
    # How long a group that answers EPERM (a setuid sandbox helper inside it) still counts as
    # alive before it is treated as ended, so wait_tree cannot hang on it forever.
    _EPERM_GRACE_S = 5.0
    _monotonic = time.monotonic
    # The desktop's default-handler command for open_external().
    _OPENER: tuple[str, ...] = ("/usr/bin/open",) if sys.platform == "darwin" else ("xdg-open",)
    # The runtime-directory fallback's base, when TMPDIR/XDG_RUNTIME_DIR is unusable. A constant
    # (not tempfile.gettempdir(), read again) so tests can point it at tmp_path.
    _TMP_BASE = Path("/tmp")

    class Job:
        """The process group of a child started with `start_new_session=True`: it holds
        everything the child starts unless a descendant calls setsid itself. There is no
        kill-on-close, so after an app crash the group outlives the app."""

        def __init__(self, *, memory_limit_bytes: int | None = None) -> None:
            if memory_limit_bytes is not None and (
                type(memory_limit_bytes) is not int or not 1 <= memory_limit_bytes <= (1 << 63) - 1
            ):
                raise ValueError("invalid job memory limit")
            # The ceiling is validated but not applied: only preexec_fn could apply it, and that
            # is unsafe in a threaded process. On Linux the image worker applies its own
            # address-space ceiling; macOS has none.
            self._pgid: int | None = None
            self._gone = False
            self._eperm_since: float | None = None

        def adopt(self, pid: int) -> None:
            """Track the child's group: it leads its own session, so its pgid is its pid."""
            self._pgid = pid

        def active_processes(self) -> int:
            """1 while the group has a member, 0 once it was seen empty (then never probed
            again, so a recycled pgid is never mistaken for it)."""
            if self._pgid is None or self._gone:
                return 0
            try:
                os.killpg(self._pgid, 0)
            except ProcessLookupError:
                self._gone = True
                return 0
            except PermissionError:
                now = _monotonic()
                if self._eperm_since is None:
                    self._eperm_since = now
                if now - self._eperm_since < _EPERM_GRACE_S:
                    return 1
                _log.warning("process group unreachable; treated as ended")
                self._gone = True
                return 0
            self._eperm_since = None
            return 1

        def holds(self, pid: int) -> bool:
            """Whether `pid` is in the tracked group; a gone or invalid pid is not."""
            if (
                type(pid) is not int
                or not 0 < pid <= 0x7FFFFFFF
                or self._pgid is None
                or self._gone
            ):
                return False
            try:
                return os.getpgid(pid) == self._pgid
            except OSError:
                return False

        def terminate(self, exit_code: int = 1) -> None:
            """SIGKILL the whole group now (`exit_code` has no POSIX meaning). Safe to call more
            than once."""
            if self._pgid is None or self._gone:
                return
            try:
                # A group that emptied unseen could, in theory, have its pgid reused
                # by a new session leader; pids are not handed out while a group still uses one.
                os.killpg(self._pgid, signal.SIGKILL)
            except ProcessLookupError:
                self._gone = True
            except PermissionError:
                pass

        def close(self) -> None:
            """Kill any survivors, then forget the group. Idempotent."""
            self.terminate()
            self._pgid = None

    if sys.platform == "darwin":

        def process_start_time(pid: int) -> int | str | None:
            """The stripped `ps -o lstart=` line for a live pid, or None if it is not running or
            `ps` fails. `LC_ALL=C`/`TZ=UTC` fix the format so it equals the CLI's `procStart`."""
            try:
                done = run(
                    ["env", "LC_ALL=C", "TZ=UTC", "ps", "-o", "lstart=", "-p", str(pid)],
                    cwd="/",
                    timeout=1.0,
                )
            except Exception:
                return None
            if done.returncode != 0:
                return None
            line = done.stdout.strip()
            return line or None

        def process_start_times(pids: Collection[int]) -> dict[int, int | str]:
            """One `ps` call for every pid in `pids`, keeping only the ones still alive."""
            wanted = sorted(pids)
            if not wanted:
                return {}
            argv = ["env", "LC_ALL=C", "TZ=UTC", "ps", "-o", "pid=", "-o", "lstart="]
            for pid in wanted:
                argv += ["-p", str(pid)]
            try:
                done = run(argv, cwd="/", timeout=2.0)
            except Exception:
                return {}
            wanted_set = set(wanted)
            result: dict[int, int | str] = {}
            for line in done.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                pid_text, rest = parts
                try:
                    pid = int(pid_text)
                except ValueError:
                    continue
                if pid in wanted_set:
                    result[pid] = rest.strip()
            return result

    else:

        def process_start_time(pid: int) -> int | str | None:
            """Field 22 of /proc/<pid>/stat (starttime, in clock ticks since boot) as read, the
            string the Linux CLI writes as `procStart`, unchecked as the CLI leaves it; None if
            the pid is not running or the file cannot be read or parsed. The fields are counted
            after the last ")", since the command name before it may hold one."""
            try:
                with open(f"/proc/{pid}/stat", "rb") as f:
                    return f.read().rsplit(b")", 1)[1].split()[19].decode("ascii")
            except OSError, IndexError, UnicodeDecodeError:
                return None

        def process_start_times(pids: Collection[int]) -> dict[int, int | str]:
            """`process_start_time` for each of `pids`, keeping only the ones still alive."""
            result: dict[int, int | str] = {}
            for pid in pids:
                started = process_start_time(pid)
                if started is not None:
                    result[pid] = started
            return result

    # --- one running app: a flock and a FIFO in a private runtime directory ---------------------

    def _safe_name(name: str) -> str:
        """`name` as a file name: the Windows `Local\\...` names carry a backslash."""
        return re.sub(r"[^A-Za-z0-9._-]", "_", name)

    def _runtime_dir() -> Path:
        """The per-user directory RUNTIME_FOLDER for the lock and the FIFO, created 0700 and
        refused unless it is a real directory the current user owns with no group or other
        bits."""
        base = os.environ.get("TMPDIR" if sys.platform == "darwin" else "XDG_RUNTIME_DIR", "")
        # A relative base would put the lock wherever the launch's cwd is: two launches, two
        # locks. A stale or foreign value (left over after su, naming a directory this user
        # cannot write, or one that no longer exists) falls back the same way, rather than
        # raising.
        if os.path.isabs(base) and os.path.isdir(base) and os.access(base, os.W_OK | os.X_OK):
            path = Path(base) / RUNTIME_FOLDER
        else:
            path = _TMP_BASE / f"{RUNTIME_FOLDER}-{os.getuid()}"
        with contextlib.suppress(FileExistsError):
            os.mkdir(path, 0o700)
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("the runtime directory is not private")
        return path

    class InstanceLock:
        """An exclusive flock that makes this process the only running app. release()
        (idempotent) lets the next launch take it; the OS releases it anyway when the process
        ends. The file is never unlinked: unlinking a lock file races the next locker."""

        def __init__(self, fd: int) -> None:
            self._fd: int | None = fd

        def release(self) -> None:
            if self._fd is not None:
                fd, self._fd = self._fd, None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def single_instance(name: str) -> InstanceLock | None:
        """Take the lock `name` in the runtime directory, or None when another open of it (this
        process included) already holds it."""
        fd = os.open(
            _runtime_dir() / f"{_safe_name(name)}.lock",
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        except BaseException:
            os.close(fd)
            raise
        return InstanceLock(fd)

    class Signal:
        """A FIFO standing in for the named auto-reset event: a set() is a byte that stays in
        the pipe until one wait() drains it, so sets with no wait in between collapse into one
        wake. Only the creator (the lock holder) reads; it also holds a write end, so a
        writer's close never shows its read end EOF."""

        def __init__(self, read_fd: int | None, write_fd: int, path: Path | None) -> None:
            self._read = read_fd
            self._write: int | None = write_fd
            self._path = path  # the creator's: close() unlinks it

        @classmethod
        def create(cls, name: str) -> Signal:
            path = _runtime_dir() / f"{_safe_name(name)}.fifo"
            try:
                if not stat.S_ISFIFO(os.lstat(path).st_mode):
                    raise FileExistsError(errno.EEXIST, "not a FIFO", str(path))
                os.unlink(path)  # a stale FIFO a crashed app left
            except FileNotFoundError:
                pass
            os.mkfifo(path, 0o600)
            read_fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                write_fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except BaseException:
                os.close(read_fd)
                raise
            return cls(read_fd, write_fd, path)

        @classmethod
        def open(cls, name: str) -> Signal | None:
            """The FIFO another process created, or None when there is none or nobody reads
            it."""
            try:
                fd = os.open(
                    _runtime_dir() / f"{_safe_name(name)}.fifo",
                    os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC,
                )
            except FileNotFoundError:
                return None
            except OSError as exc:
                if exc.errno == errno.ENXIO:  # a FIFO with no read end
                    return None
                raise
            info = os.fstat(fd)
            if not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid():
                os.close(fd)
                return None
            return cls(None, fd, None)

        def set(self) -> None:
            # A full pipe means a set is already pending. A creator that closed in the meantime
            # reads nothing more, as a Windows event nobody waits on: the set is lost either way.
            if self._write is not None:
                with contextlib.suppress(BlockingIOError, BrokenPipeError):
                    os.write(self._write, b"\0")

        def wait(self, timeout_ms: int) -> bool:
            """Block the calling thread up to `timeout_ms`: True when the signal was set, False
            on the timeout. Raises once the signal is closed, or on an opened one, which has no
            read end, so a waiter never spins on a dead descriptor."""
            if self._read is None:
                raise RuntimeError("the signal is closed or was not created here")
            # `select()` raises ValueError for a descriptor of 1024 or more; the app
            # creates this right after the port bind, with a handful open. Upgrade to
            # select.poll() (no descriptor ceiling) if that ever changes.
            ready, _, _ = select.select([self._read], [], [], timeout_ms / 1000)
            if not ready:
                return False
            try:
                while os.read(self._read, 4096):
                    pass
            except BlockingIOError:
                pass
            return True

        def close(self) -> None:
            if self._path is not None:
                path, self._path = self._path, None
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(path)
            for fd in (self._read, self._write):
                if fd is not None:
                    os.close(fd)
            self._read = self._write = None


# --- spawning ---------------------------------------------------------------------------------


@dataclass(eq=False)
class Child:
    """A spawned process and the Job that owns its tree."""

    process: asyncio.subprocess.Process
    job: Job

    @property
    def pid(self) -> int:
        return self.process.pid

    async def wait_tree(self, poll_s: float = 0.5) -> None:
        """Return once the launched process and everything it started have all ended: the
        job's active-process count is polled every `poll_s` after the launched pid exits."""
        await self.process.wait()
        while self.job.active_processes() > 0:
            await asyncio.sleep(poll_s)

    def kill_tree(self) -> None:
        self.job.terminate()

    def release(self) -> None:
        """Close the job handle, reaping any survivors. Call once the child is done."""
        self.job.close()


async def spawn(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    env: Mapping[str, str],
    pipes: bool = True,
    memory_limit_bytes: int | None = None,
) -> Child:
    """Start `argv` windowless inside a fresh Job, with byte pipes for
    stdin/stdout/stderr (callers decode UTF-8 per complete line), or with all three on the
    null device when `pipes` is False (a browser window: nothing drains its output).

    On Windows the child is created suspended and only resumed once it is inside the job; on
    POSIX it leads a new session before exec, so everything it starts is in its group, unless
    something in that tree calls setsid itself (the bundled CLI's hooks and Bash tool shells do)."""
    job = Job() if memory_limit_bytes is None else Job(memory_limit_bytes=memory_limit_bytes)
    stdio = asyncio.subprocess.PIPE if pipes else asyncio.subprocess.DEVNULL
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdio,
            stdout=stdio,
            stderr=stdio,
            cwd=cwd,
            env=dict(env),
            creationflags=_CREATION_FLAGS,
            start_new_session=sys.platform != "win32",
        )
    except BaseException:
        job.close()
        raise
    try:
        job.adopt(process.pid)
    except BaseException:
        process.kill()
        job.close()
        raise
    return Child(process, job)


def run(
    argv: Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    timeout: float = 15.0,
    input: str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a short command windowless and capture its UTF-8 output. Blocking: wrap in
    `asyncio.to_thread` from async code. `input`, when given, is written to its stdin;
    otherwise stdin is the null device. `env`, when given, goes to Popen as given; None
    inherits the caller's environment, as today.

    The command runs inside a kill-on-close Job like `spawn()`: git.exe is a wrapper whose
    grandchild inherits the pipes, so killing the launched pid alone would leave `timeout`
    waiting on the grandchild's write ends (a 1 s timeout measured 8 s). Terminating the job
    ends the whole tree on Windows, so the timeout is a real bound there. On POSIX the Job is
    the command's process group: a timeout kills the group, and closing the Job after a normal
    exit kills whatever the command left behind in it, but a descendant that already left the
    group (by calling setsid itself) is not in it to kill; the timeout's own second
    `communicate()` then bounds `run()` instead, waiting for every remaining holder of the
    pipes."""
    job = Job()
    # None omits the kwarg entirely (not env=None), so a caller that fakes Popen and keys off
    # whether "env" was passed at all sees no `env` argument when no override is given.
    popen_kwargs: dict[str, Any] = {} if env is None else {"env": dict(env)}
    try:
        with subprocess.Popen(
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATION_FLAGS,
            start_new_session=sys.platform != "win32",
            **popen_kwargs,
        ) as process:
            try:
                job.adopt(process.pid)
                stdout, stderr = process.communicate(input=input, timeout=timeout)
            except subprocess.TimeoutExpired:
                job.terminate()
                # No timeout here, so a descendant that left the group (setsid)
                # and still holds a pipe end bounds this call instead of `timeout`; upgrade
                # by passing a timeout too and closing the pipes on its own expiry.
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(
                    process.args, timeout, output=stdout, stderr=stderr
                ) from None
            except BaseException:
                process.kill()  # a suspended pid that adopt() rejected would never exit
                raise
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    finally:
        job.close()


def system_dir() -> Path:
    """The Windows system directory, from the OS rather than an environment variable."""
    if sys.platform != "win32":
        raise OSError(errno.ENOSYS, "the system directory is Windows-only")
    size = 32768
    buffer = ctypes.create_unicode_buffer(size)
    length = _GetSystemDirectoryW(buffer, size)
    _check(length, "GetSystemDirectoryW")
    return Path(buffer.value)


def tcp_listeners(port: int) -> frozenset[int]:
    """The owning pids of every TCP listener on `port`, on any local IPv4 or IPv6 address."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid port")
    if sys.platform != "win32":
        raise OSError(errno.ENOSYS, "tcp_listeners is Windows-only")
    rows = [*_listener_rows(socket.AF_INET, _TcpRow), *_listener_rows(_AF_INET6, _Tcp6Row)]
    return frozenset(r.dwOwningPid for r in rows if socket.ntohs(r.dwLocalPort & 0xFFFF) == port)


def powershell_argv() -> list[str]:
    """The terminal's shell: Windows PowerShell from the system directory, without its banner."""
    exe = resolve_exe(system_dir() / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    return [str(exe), "-NoLogo"]


@dataclass(eq=False)
class ShellRun:
    """A command in the OS shell and its Job; stdout and stderr share one pipe.

    POSIX output is nonblocking: release freezes the available tail, so an escaped descendant
    holding or writing the pipe cannot keep the supervisor's reader alive.
    """

    process: subprocess.Popen[bytes]
    job: Job
    _released: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _read_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _remaining: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if sys.platform != "win32":
            assert self.process.stdout is not None
            os.set_blocking(self.process.stdout.fileno(), False)

    @property
    def pid(self) -> int:
        return self.process.pid

    def read(self, size: int = 65536) -> bytes:
        """The next chunk of output, at most `size` bytes; b"" at EOF."""
        stdout = cast(io.BufferedReader, self.process.stdout)
        if sys.platform == "win32":
            return stdout.read1(size)
        if size == 0:
            return b""
        size = 65536 if size < 0 else size
        with self._read_lock:
            if stdout.closed:
                return b""
            fd = stdout.fileno()
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        while True:
            with self._read_lock:
                if stdout.closed:
                    return b""
                released = self._released.is_set()
                if released and self._remaining == 0:
                    stdout.close()
                    return b""
                try:
                    chunk = os.read(fd, min(size, self._remaining) if released else size)
                except BlockingIOError:
                    if released:
                        stdout.close()
                        return b""
                else:
                    if released:
                        self._remaining -= len(chunk)
                    if not chunk:
                        stdout.close()
                    return chunk
            poller.poll(50)

    def wait(self, timeout: float | None) -> int | None:
        """The exit code, or None when `timeout` passes first."""
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill_tree(self) -> None:
        self.job.terminate()

    def release(self) -> None:
        """Close the Job and finish POSIX output after the available tail. Idempotent."""
        self.job.close()
        if sys.platform != "win32":
            with self._read_lock:
                if self._released.is_set():
                    return
                self._released.set()
                stdout = self.process.stdout
                if stdout is not None and not stdout.closed:
                    # Snapshot only bytes already available. Draining until EAGAIN could run
                    # forever when a descendant left the group and keeps writing.
                    with contextlib.suppress(OSError):
                        pending = fcntl.ioctl(stdout.fileno(), termios.FIONREAD, bytes(4))
                        self._remaining = max(0, struct.unpack("i", pending)[0])
                    if self._remaining == 0:
                        stdout.close()


def start_shell(command: str, *, cwd: str | os.PathLike[str], env: Mapping[str, str]) -> ShellRun:
    """Run one command line through cmd.exe or /bin/sh, inside a fresh Job.

    On Windows Popen gets a string, so `list2cmdline` never re-quotes the command: `/s /c`
    strips only the outer quotes this adds, and cmd.exe reads the rest as typed.
    NoDefaultCurrentDirectoryInExePath=1 makes cmd.exe look for a bare program name on PATH only,
    so a folder's own `git.bat` cannot answer for `git`; a program in the folder runs by its path
    (`.\\build.bat`). POSIX uses /bin/sh -c in a new session and keeps only absolute PATH
    entries, so an empty or relative entry cannot find a program in the working directory.
    """
    if any(c in command for c in "\r\n\0"):
        raise ValueError("a shell command is one line without NUL")
    argv: str | list[str]
    if sys.platform == "win32":
        child_env = {
            k: v for k, v in env.items() if k.upper() != "NODEFAULTCURRENTDIRECTORYINEXEPATH"
        }
        child_env["NoDefaultCurrentDirectoryInExePath"] = "1"
        comspec = resolve_exe(system_dir() / "cmd.exe")
        argv = f'"{comspec}" /d /s /c "{command}"'
    else:
        child_env = dict(env)
        child_env["PATH"] = (
            os.pathsep.join(
                entry
                for entry in env.get("PATH", os.defpath).split(os.pathsep)
                if os.path.isabs(entry)
            )
            or os.defpath
        )  # an empty PATH would search the working directory too
        argv = ["/bin/sh", "-c", command]
    job = Job()
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=_CREATION_FLAGS if sys.platform == "win32" else 0,
            start_new_session=sys.platform != "win32",
        )
    except BaseException:
        job.close()
        raise
    try:
        job.adopt(process.pid)
        return ShellRun(process, job)
    except BaseException:
        with process:  # closes the pipe and reaps the suspended pid adopt() rejected
            process.kill()
        job.close()
        raise


def _git_exe() -> Path:
    """Find native Git only in explicit absolute PATH directories, without a cwd fallback."""
    name = "git.exe" if sys.platform == "win32" else "git"
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(entry.strip('"') if sys.platform == "win32" else entry)
        if not entry or not directory.is_absolute():
            continue
        try:
            return resolve_exe(directory / name)
        except OSError:
            continue
    raise FileNotFoundError("git executable not found in absolute PATH entries")


# Set by `untrusted_git()` for the Git commands of one thread or task.
_UNTRUSTED_GIT: ContextVar[bool] = ContextVar("viki_untrusted_git", default=False)
# No fsmonitor hook and no signature program, and a folder without a `.git` entry is no
# repository: Git opens a bare layout of plain files (`HEAD`, `objects/`, `refs/`) as one.
_UNTRUSTED_OPTIONS = (
    "-c",
    "core.fsmonitor=false",
    "-c",
    "log.showSignature=false",
    "-c",
    "safe.bareRepository=explicit",
)
# Every transport refused, so a missing object starts no promisor fetch, which would run the
# folder's own `uploadpack` and write packs into it. A repository's own `protocol.<name>.allow`
# overrides `protocol.allow`, never this variable; a Git that knows `GIT_NO_LAZY_FETCH` skips
# the fetch outright.
_UNTRUSTED_ENVIRONMENT = {"GIT_ALLOW_PROTOCOL": "none", "GIT_NO_LAZY_FETCH": "1"}
# The filter drivers one untrusted command switches off, four `-c` options each.
MAX_FILTER_DRIVERS = 32


@contextlib.contextmanager
def untrusted_git() -> Iterator[None]:
    """Run every `git()` this thread or task starts inside the block as a read of a folder whose
    own repository settings nobody has trusted: a vault, at any policy, or a folder before its
    trust step. Its settings then start no program, write nothing and reach no network.

    Each command runs with `_UNTRUSTED_OPTIONS`, `_UNTRUSTED_ENVIRONMENT` and every filter
    driver the configuration names switched off, after one `git config` read. A `status` must
    still pass `--ignore-submodules=all`: a submodule's own settings name drivers this folder's
    do not. Switched off, a filter cannot vouch for a file whose stat data changed, so `status`
    lists such a file as modified."""
    token = _UNTRUSTED_GIT.set(True)
    try:
        yield
    finally:
        _UNTRUSTED_GIT.reset(token)


def _filters_off(
    argv: list[str], cwd: str | os.PathLike[str], env: Mapping[str, str], timeout: float
) -> list[str]:
    """`-c` options that switch off every filter driver the configuration names, the user's own
    included: a filter is a program Git starts on the folder's files, and Git LFS's clean also
    writes the file into the folder's `.git`. RuntimeError for a name `-c` cannot spell (an
    `=`, or bytes that are not UTF-8 and so read back as U+FFFD) or for too many."""
    done = run([*argv, "config", "--list", "--name-only", "-z"], cwd=cwd, timeout=timeout, env=env)
    if done.returncode != 0:
        raise RuntimeError(f"git config failed: {done.stderr.strip()}")
    names = dict.fromkeys(
        key[len("filter.") : key.rindex(".")]
        for key in done.stdout.split("\0")
        if key.startswith("filter.") and key.count(".") >= 2
    )
    if len(names) > MAX_FILTER_DRIVERS or any(
        "=" in name or "\N{REPLACEMENT CHARACTER}" in name for name in names
    ):
        raise RuntimeError("git filter drivers no option can switch off")
    return [
        part
        for name in names
        for setting in ("clean=", "smudge=", "process=", "required=false")
        for part in ("-c", f"filter.{name}.{setting}")
    ]


def git(
    cwd: str | os.PathLike[str],
    *args: str,
    timeout: float = 15.0,
    index: Path | None = None,
    isolated_config: bool = False,
) -> str:
    """Run a git command: read-only, apart from the worktree service's `worktree add` and
    `worktree remove`, the review services' `add`, `commit`, `merge --ff-only` and
    `push`, the worktree cleanup's `commit-tree` and `update-ref` of `refs/viki/archive/*`
    and of a swept branch, a restore's `worktree add --no-checkout`, `read-tree` and
    `checkout-index`, and, when `index` is given, a `read-tree`, `write-tree` or `add -A`
    into that private temporary index. `--no-optional-locks` keeps git from taking
    index.lock next to a live agent; `core.quotepath=off` keeps non-ASCII paths readable.

    `index`, when given, must be an absolute path: a relative one raises ValueError before any
    process starts. Its parent must be a fresh private folder holding nothing else, because the
    command then runs against that file instead of the repo's own index, through
    `GIT_INDEX_FILE` in the child's environment alone (`os.environ` itself is never written):
    `core.sparseCheckout=false` keeps the write from skipping paths outside a cone-mode sparse
    checkout, a quirk of git before 2.43 (measured), and `core.hooksPath=<index.parent>` points
    hooks at that empty folder so no repo hook, `post-index-change` included, fires from it.

    `isolated_config`, off by default, keeps the user's system and global Git configuration out
    of the command, in the child's environment alone: Git reads both at startup, includes and
    all, before it parses even a `config --file`. The child gets no inherited `GIT_CONFIG` or
    `GIT_CONFIG_*` variable in any letter case (the environment's injected config included),
    `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` at `os.devnull`, which Git documents as reading
    nothing, and `GIT_CONFIG_NOSYSTEM=1`. Git before 2.32 ignores `GIT_CONFIG_GLOBAL` and
    reads `~/.gitconfig` and the XDG config instead, so `HOME` and `XDG_CONFIG_HOME` name an
    empty private folder that exists for this call only (an empty folder rather than a file:
    Git for Windows fails on a device-name `HOME`). The repository's own config still applies.

    A command whose options include `protocol.allow=never` runs without an inherited
    `GIT_ALLOW_PROTOCOL`, which Git would let outrank that refusal.

    Inside `untrusted_git()` the command reads a folder nobody has trusted: see there. Its
    settings read and the command share `timeout`."""
    if index is not None and not index.is_absolute():
        raise ValueError(f"index must be an absolute path: {index}")
    argv = [str(_git_exe()), "--no-optional-locks", "-c", "core.quotepath=off"]
    env = None
    home = (
        tempfile.TemporaryDirectory(prefix="viki-git-home-", ignore_cleanup_errors=True)
        if isolated_config
        else contextlib.nullcontext()
    )
    with home as empty:
        if empty is not None:
            env = {
                name: value
                for name, value in os.environ.items()
                if not (name.upper() == "GIT_CONFIG" or name.upper().startswith("GIT_CONFIG_"))
            }
            env |= {
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": empty,
                "XDG_CONFIG_HOME": empty,
            }
        if index is not None:
            argv += [
                "-c",
                "core.sparseCheckout=false",
                "-c",
                f"core.hooksPath={os.fspath(index.parent)}",
            ]
            env = {**(os.environ if env is None else env), "GIT_INDEX_FILE": os.fspath(index)}
        if "protocol.allow=never" in args:
            # Git lets an inherited `GIT_ALLOW_PROTOCOL` outrank `protocol.allow`, so a command
            # that refuses every transport through its options gets none, in any letter case.
            env = {
                name: value
                for name, value in (os.environ if env is None else env).items()
                if name.upper() != "GIT_ALLOW_PROTOCOL"
            }
        if _UNTRUSTED_GIT.get():
            deadline = time.monotonic() + timeout
            argv += _UNTRUSTED_OPTIONS
            env = {**(os.environ if env is None else env), **_UNTRUSTED_ENVIRONMENT}
            argv += _filters_off(argv, cwd, env, timeout)
            timeout = max(deadline - time.monotonic(), 0.0)
        done = run([*argv, *args], cwd=cwd, timeout=timeout, env=env)
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0] if args else ''} failed: {done.stderr.strip()}")
    return done.stdout


def open_external(target: str) -> None:
    """Hand a URL (http, https, mailto, vscode://file) to the user's default handler.
    Callers validate `target`; this only keeps process-starting code in one module."""
    if sys.platform == "win32":
        os.startfile(target)  # ShellExecute: no shell, no console
        return
    # Detached and never killed: xdg-open may exec the user's browser in its own process, so a
    # timeout that killed its group (as run() does) would kill the browser. A daemon thread
    # reaps it.
    process = subprocess.Popen(
        [*_OPENER, target],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    threading.Thread(target=process.wait, name="viki-opener", daemon=True).start()


def _detached(
    argv: Sequence[str], *, cwd: str | os.PathLike[str], env: dict[str, str], creationflags: int
) -> None:
    """Start `argv` with no pipes and outside any Job; a daemon thread reaps it. On POSIX it
    also leads a new session with its stdio on the null device, so it shares neither the app's
    process group nor its terminal or pipes; on Windows its stdio is left for the console."""
    null = None if sys.platform == "win32" else subprocess.DEVNULL
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        creationflags=creationflags,
        close_fds=True,
        stdin=null,
        stdout=null,
        stderr=null,
        start_new_session=sys.platform != "win32",
    )
    threading.Thread(target=process.wait, name="viki-terminal", daemon=True).start()


def open_terminal(cwd: str | os.PathLike[str], env: Mapping[str, str]) -> None:
    """Open the OS terminal in `cwd`, outside any Job so the user's window outlives VIKI."""
    if sys.platform == "win32":
        _detached(powershell_argv(), cwd=cwd, env=dict(env), creationflags=CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        _mac_terminal((), cwd=cwd, env=env)
    else:
        _detached(
            [
                *_linux_terminal(env),
                "/bin/sh",
                "-c",
                'cd -- "$1" || exit; exec "${SHELL:-/bin/sh}"',
                "viki-terminal",
                os.fspath(cwd),
            ],
            cwd=cwd,
            env=dict(env),
            creationflags=0,
        )


# PowerShell reads each quote here as a single quote, which ends a '...' literal; `[ ] * ?` are
# wildcards when `&` resolves the command path, so a different file that matches could run. A
# lone backtick there is just a literal character, but `&`'s command lookup unescapes the
# resolved path even when it holds no wildcard, and that turns "``" (or any longer run) into one
# fewer backtick, so a path holding a doubled backtick can resolve to a different file.
_POWERSHELL_REFUSED = frozenset("'\u2018\u2019\u201a\u201b[]*?")
# Linux: the terminal launchers looked for on PATH, in order, each with the flags before the
# command it runs: xdg-terminal-exec takes the command as its own arguments, the Debian
# alternative x-terminal-emulator takes it after -e.
_LINUX_TERMINALS = (("xdg-terminal-exec", ()), ("x-terminal-emulator", ("-e",)))
# `/bin/sh -c` script that runs the argv ($0 and "$@"), then the user's shell, so the window
# stays open after the command ends.
_KEEP_OPEN = '"$0" "$@"; exec "${SHELL:-/bin/sh}"'


def _linux_terminal(env: Mapping[str, str]) -> list[str]:
    for name, flags in _LINUX_TERMINALS:
        terminal = find_on_path(name, env.get("PATH"))
        if terminal is not None:
            return [str(terminal), *flags]
    raise OSError(errno.ENOENT, "no terminal emulator found")


def _command_file(
    argv: Sequence[str], cwd: str | os.PathLike[str], directory: str | None = None
) -> Path:
    """A new 0700 `.command` script for Terminal, in `directory` (default: the user's temp
    folder): it removes itself, enters `cwd`, runs `argv` (each element shell-quoted), then
    the user's login shell, so the window stays open. An empty `argv` opens just that shell;
    a failed directory change exits before any command. A failed write removes the file."""
    lines = [
        "#!/bin/sh",
        'rm -f -- "$0"',
        f"cd -- {shlex.quote(str(cwd))} || exit",
        " ".join(shlex.quote(arg) for arg in argv),
        'exec "${SHELL:-/bin/sh}" -l',
    ]
    fd, name = tempfile.mkstemp(prefix="viki-sign-in-", suffix=".command", dir=directory)
    try:
        # fchmod inside the with block, so its failure closes the descriptor too.
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as script:
            os.fchmod(fd, 0o700)
            script.write("".join(f"{line}\n" for line in lines))
    except BaseException:
        os.unlink(name)
        raise
    return Path(name)


def _mac_terminal(
    argv: Sequence[str], *, cwd: str | os.PathLike[str], env: Mapping[str, str]
) -> None:
    script = _command_file(argv, cwd)
    try:
        _detached(
            ["/usr/bin/open", "-a", "Terminal", str(script)],
            cwd=cwd,
            env=dict(env),
            creationflags=0,
        )
    except BaseException:
        with contextlib.suppress(OSError):
            script.unlink()
        raise


def open_console(
    argv: Sequence[str], *, cwd: str | os.PathLike[str], env: Mapping[str, str]
) -> None:
    """Run `argv` in a terminal window of its own that stays open after it ends: the user's
    own window, which takes what the user types and outlives VIKI, so it starts outside any
    Job.

    - Windows: Windows PowerShell in a new console, each element a PowerShell single-quoted
      literal.
    - macOS: Terminal, through `/usr/bin/open` and a `.command` file (`_command_file`). `env`
      goes to `open`; the command runs in Terminal's own environment.
    - Linux: the first of `xdg-terminal-exec` and `x-terminal-emulator -e` found on `env`'s
      PATH, running `argv` through /bin/sh and then the user's shell. With neither it raises
      OSError(ENOENT) before anything starts.

    On every OS an empty argv, or an element holding a character below U+0020, raises
    ValueError before anything starts: it is refused, not escaped. On Windows so does an
    element holding one of PowerShell's single quotes, a wildcard character (`[`, `]`, `*` or
    `?`, which `&` would match against other paths) or two or more consecutive backticks
    (which `&`'s command lookup unescapes into fewer, letting a different file run)."""
    if not argv or any(ord(c) < 0x20 for arg in argv for c in arg):
        raise ValueError("an empty argv, or one holding a control character")
    if sys.platform == "win32":
        if any("``" in arg or any(c in _POWERSHELL_REFUSED for c in arg) for arg in argv):
            raise ValueError("an argv PowerShell cannot take as literals")
        command = "& " + " ".join(f"'{arg}'" for arg in argv)
        _detached(
            [*powershell_argv(), "-NoExit", "-Command", command],
            cwd=cwd,
            env=dict(env),
            creationflags=CREATE_NEW_CONSOLE,
        )
    elif sys.platform == "darwin":
        _mac_terminal(argv, cwd=cwd, env=env)
    else:
        _detached(
            [*_linux_terminal(env), "/bin/sh", "-c", _KEEP_OPEN, *argv],
            cwd=cwd,
            env=dict(env),
            creationflags=0,
        )
