"""compiled_smoke_app.py's pure helpers (the launch-token reader, the scrubbed app environment,
the temp data root, the secret redaction, the port probes, the stand-in's Nuitka argv) and the
stand-in browser's own child-process behaviour.

The full check list needs a real compiled binary and 127.0.0.1:8765 free, neither of which
a unit test can supply here: that is `scripts/compiled_smoke_app.py`'s own `__main__` run,
proved separately against the real build (ADR-054). This file still proves, without any of
that, that the script refuses at once when the port is already taken.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import secrets
import shutil
import socket
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.stand_in_browser import ARGV_FILE_NAME, HANG_GUARD_S, QUIT_FILE_NAME
from viki_agent import agent_cli, winproc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import compiled_smoke_app  # noqa: E402

_STAND_IN = Path(__file__).resolve().parent / "stand_in_browser.py"


# --- token() --------------------------------------------------------------------------------


def test_token_reads_a_windows_style_app_argument() -> None:
    tok = secrets.token_urlsafe(32)
    argv = [
        "--disable-extensions",
        f"--app=http://127.0.0.1:8765/?ui={uuid.uuid4().hex}#token={tok}",
    ]
    assert compiled_smoke_app.token(argv) == tok


def test_token_reads_a_posix_redirect_page(tmp_path: Path) -> None:
    """Mirrors `__main__._redirect_url`'s own page shape: a `file:` URI to an HTML file whose
    meta refresh target, not the URI itself, carries the token."""
    tok = secrets.token_urlsafe(32)
    page = tmp_path / "open-abc123.html"
    target = f"http://127.0.0.1:8765/?ui={uuid.uuid4().hex}#token={tok}"
    page.write_text(
        '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
        f'<meta http-equiv="refresh" content="0;url={target}">',
        encoding="utf-8",
    )
    argv = ["--disable-sync", f"--app={page.as_uri()}"]
    assert compiled_smoke_app.token(argv) == tok


def test_token_refuses_an_argv_with_no_app_argument() -> None:
    with pytest.raises(ValueError):
        compiled_smoke_app.token(["--disable-extensions"])


def test_token_refuses_a_target_with_no_token() -> None:
    with pytest.raises(ValueError):
        compiled_smoke_app.token(["--app=http://127.0.0.1:8765/"])


# --- app_env() / system_path() ----------------------------------------------------------------


def test_app_env_drops_virtualenv_pythonhome_and_every_uv_prefixed_variable(
    tmp_path: Path,
) -> None:
    base = {
        "VIRTUAL_ENV": str(tmp_path / "venv"),
        "PYTHONHOME": str(tmp_path / "home-python"),
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_PYTHON": "3.14",
        "LANG": "en_US.UTF-8",
        "PATH": str(tmp_path / "venv" / "bin"),
    }
    data_dir = tmp_path / "data"

    env = compiled_smoke_app.app_env(
        base,
        data_dir=data_dir,
        home=tmp_path / "home",
        claude_cli=tmp_path / "bin" / "claude",
        edge=tmp_path / "bin" / "edge",
        platform="linux",
    )

    assert "VIRTUAL_ENV" not in env
    assert "PYTHONHOME" not in env
    assert not any(key.startswith("UV_") for key in env)
    assert env["LANG"] == "en_US.UTF-8"  # an unrelated variable survives
    assert env["PATH"] == compiled_smoke_app.system_path("linux") != base["PATH"]
    assert env["HOME"] == str(tmp_path / "home") == env["USERPROFILE"]
    assert env["VIKI_DATA_DIR"] == str(data_dir)
    assert env["VIKI_CLAUDE_CLI"] == str(tmp_path / "bin" / "claude")
    assert env["VIKI_EDGE"] == str(tmp_path / "bin" / "edge")
    assert env["XDG_RUNTIME_DIR"] == str(data_dir.parent)
    assert "TMPDIR" not in env


def test_app_env_runtime_variable_is_tmpdir_on_macos_and_neither_on_windows(
    tmp_path: Path,
) -> None:
    kwargs = {
        "base": {},
        "data_dir": tmp_path / "data",
        "home": tmp_path / "home",
        "claude_cli": tmp_path / "claude",
        "edge": tmp_path / "edge",
    }

    mac_env = compiled_smoke_app.app_env(**kwargs, platform="darwin")
    assert mac_env["TMPDIR"] == str((tmp_path / "data").parent)
    assert "XDG_RUNTIME_DIR" not in mac_env

    windows_env = compiled_smoke_app.app_env(**kwargs, platform="win32")
    assert "TMPDIR" not in windows_env
    assert "XDG_RUNTIME_DIR" not in windows_env
    assert windows_env["PATH"] == compiled_smoke_app.system_path("win32")


def test_system_path_is_platform_specific_and_never_calls_a_live_windows_api() -> None:
    """`system_path("win32")` must answer safely from this Linux/macOS/Windows check alike
    (ADR-054: the unit tests never invoke a Windows-only API), unlike `winproc.system_dir()`.
    Its separator is always ``;`` -- the target (Windows), never this host's own `os.pathsep`,
    which is ``:`` on the POSIX runner this very assertion may be running on."""
    posix = compiled_smoke_app.system_path("linux")
    assert posix == compiled_smoke_app.system_path("darwin") == "/usr/bin:/bin:/usr/sbin:/sbin"
    windows = compiled_smoke_app.system_path("win32")
    assert windows.count(";") == 2
    assert "/" not in windows  # a real Windows PATH value, backslash-separated throughout
    assert windows.lower().count("system32") == 2  # the folder itself, then its Wbem child


# --- data_root() ------------------------------------------------------------------------------


def test_data_root_has_no_symlinked_ancestor() -> None:
    root = compiled_smoke_app.data_root()
    try:
        assert os.path.realpath(root) == str(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --- redact_secrets() -------------------------------------------------------------------------


def test_redact_secrets_blanks_long_runs_but_keeps_short_text() -> None:
    tok = secrets.token_urlsafe(32)
    line = f"FAIL diagnostics: Authorization Bearer {tok} was refused"

    redacted = compiled_smoke_app.redact_secrets(line)

    assert tok not in redacted
    assert "<redacted>" in redacted
    assert compiled_smoke_app.redact_secrets("PASS diagnostics") == "PASS diagnostics"


# --- port_answers() / port_bindable() ----------------------------------------------------------


def test_port_answers_and_bindable_reflect_a_real_listener() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        assert compiled_smoke_app.port_answers("127.0.0.1", port) is True
        assert compiled_smoke_app.port_bindable("127.0.0.1", port) is False
    finally:
        listener.close()
    assert compiled_smoke_app.port_answers("127.0.0.1", port) is False
    assert compiled_smoke_app.port_bindable("127.0.0.1", port) is True


def test_main_refuses_at_once_when_the_port_already_answers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The DANGER check, exercised against a throwaway port -- never the real 8765."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    monkeypatch.setattr(compiled_smoke_app, "APP_PORT", port)
    try:
        assert compiled_smoke_app.main([]) == 2
    finally:
        listener.close()
    assert "already answers" in capsys.readouterr().err


# --- stand_in_nuitka_argv() ---------------------------------------------------------------------


def test_stand_in_nuitka_argv_is_minimal_and_never_downloads(tmp_path: Path) -> None:
    script = tmp_path / "stand_in_browser.py"
    out_dir = tmp_path / "out"

    argv = compiled_smoke_app.stand_in_nuitka_argv("python3", script, out_dir)

    assert argv == [
        "python3",
        "-m",
        "nuitka",
        "--mode=standalone",
        "--experimental=force-dependencies-pefile",
        "--lto=no",
        "--remove-output",
        f"--output-dir={out_dir}",
        str(script),
    ]
    assert not any("onefile" in arg for arg in argv)
    assert not any("--include-distribution-metadata" in arg for arg in argv)
    assert "--assume-yes-for-downloads" not in argv


# --- cli_dispatch_result() --------------------------------------------------------------------


def test_cli_dispatch_result_passes_agent_clis_own_no_agent_chat_reply() -> None:
    """Feeds `cli_dispatch_result` `agent_cli.main`'s own real reply to `status --json` with no
    VIKI_AGENT_* variable (an empty env) -- AC-10's exact contract -- rather than a hand-written
    JSON string that could drift from it."""
    out, err = io.StringIO(), io.StringIO()
    code = agent_cli.main(["status", "--json"], env={}, out=out, err=err)

    assert compiled_smoke_app.cli_dispatch_result(code, out.getvalue(), err.getvalue()) == (
        True,
        "",
    )


def test_cli_dispatch_result_fails_on_the_wrong_exit_code() -> None:
    out = io.StringIO()
    agent_cli.main(["status", "--json"], env={}, out=out, err=io.StringIO())

    ok, detail = compiled_smoke_app.cli_dispatch_result(0, out.getvalue(), "")

    assert ok is False
    assert "expected 5" in detail


def test_cli_dispatch_result_fails_on_more_than_one_stdout_line() -> None:
    out = io.StringIO()
    agent_cli.main(["status", "--json"], env={}, out=out, err=io.StringIO())

    ok, detail = compiled_smoke_app.cli_dispatch_result(
        agent_cli.EXIT_NO_KEY, out.getvalue() + out.getvalue(), ""
    )

    assert ok is False
    assert "stdout line" in detail


def test_cli_dispatch_result_fails_on_invalid_json() -> None:
    ok, detail = compiled_smoke_app.cli_dispatch_result(agent_cli.EXIT_NO_KEY, "not json\n", "")

    assert ok is False
    assert "not JSON" in detail


def test_cli_dispatch_result_fails_on_the_wrong_error_code() -> None:
    ok, detail = compiled_smoke_app.cli_dispatch_result(
        agent_cli.EXIT_NO_KEY, '{"error":{"code":"unreachable"}}\n', ""
    )

    assert ok is False
    assert "no_agent_chat" in detail


# --- _check_cli_dispatch() -----------------------------------------------------------------


def _no_agent_chat_stdout() -> str:
    """`agent_cli.main`'s own real `status --json` reply with no chat connection (no subprocess
    involved): what a correctly dispatching binary would print to stdout."""
    out = io.StringIO()
    agent_cli.main(["status", "--json"], env={}, out=out, err=io.StringIO())
    return out.getvalue()


def test_check_cli_dispatch_env_points_at_the_temp_folders_with_no_viki_agent_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W8: the dispatch step must never run in the session's own environment -- a temp
    VIKI_DATA_DIR/HOME/USERPROFILE, a minimal PATH, and no inherited VIKI_AGENT_* -- so a
    regressed dispatch cannot reach a developer's real data, lock, port or browser."""
    data_dir, home = tmp_path / "data", tmp_path / "home"
    home.mkdir()
    stdout = _no_agent_chat_stdout()
    captured: dict[str, dict[str, str]] = {}

    def fake_run(
        argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
    ) -> SimpleNamespace:
        assert env is not None
        captured["env"] = env
        return SimpleNamespace(returncode=agent_cli.EXIT_NO_KEY, stdout=stdout, stderr="")

    monkeypatch.setattr(compiled_smoke_app.winproc, "run", fake_run)
    monkeypatch.setenv("VIKI_AGENT_URL", "http://127.0.0.1:1/api/agent/x")
    monkeypatch.setenv("VIKI_AGENT_KEY", "k" * 20)

    ok, detail = compiled_smoke_app._check_cli_dispatch(tmp_path / "app.exe", data_dir, home)

    assert (ok, detail) == (True, "")
    env = captured["env"]
    assert env["VIKI_DATA_DIR"] == str(data_dir)
    assert env["HOME"] == str(home) == env["USERPROFILE"]
    assert env["PATH"] == compiled_smoke_app.system_path()
    assert not any(key.startswith("VIKI_AGENT_") for key in env)


def test_check_cli_dispatch_fails_when_the_temp_data_dir_is_left_non_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W8: even a correct exit/stdout must not pass once something wrote into the temp data
    dir -- the one proof that the dispatch really ran before any start-up."""
    data_dir, home = tmp_path / "data", tmp_path / "home"
    home.mkdir()
    stdout = _no_agent_chat_stdout()

    def fake_run(
        argv: list[str], *, cwd: Path, env: dict[str, str] | None = None
    ) -> SimpleNamespace:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "store.sqlite3").write_bytes(b"")  # a regressed dispatch starts here
        return SimpleNamespace(returncode=agent_cli.EXIT_NO_KEY, stdout=stdout, stderr="")

    monkeypatch.setattr(compiled_smoke_app.winproc, "run", fake_run)

    ok, detail = compiled_smoke_app._check_cli_dispatch(tmp_path / "app.exe", data_dir, home)

    assert ok is False
    assert "not empty" in detail


async def test_the_posix_wrapper_runs_its_script_outside_the_isolation_probe(
    tmp_path: Path,
) -> None:
    ran, marker, probe = tmp_path / "ran", tmp_path / "marker", tmp_path / "probe"
    script = tmp_path / "child.py"
    script.write_text(f"open({str(ran)!r}, 'w').close()\n", encoding="utf-8")

    wrapper = compiled_smoke_app._posix_wrapper(tmp_path / "child", script)

    assert f'"{sys.executable}" -E -s -X utf8 "{script}"' in wrapper.read_text(encoding="utf-8")
    if sys.platform == "win32":
        return  # No /bin/sh here; the POSIX rows run the wrapper itself.
    probe.mkdir()
    sitecustomize = probe / "sitecustomize.py"
    sitecustomize.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(probe)}
    child = await winproc.spawn([str(wrapper)], cwd=tmp_path, env=env, pipes=False)
    try:
        await asyncio.wait_for(child.wait_tree(), HANG_GUARD_S)
        assert child.process.returncode == 0
    finally:
        child.kill_tree()
        child.release()
    assert ran.exists()
    assert not marker.exists(), "the stand-in honoured the probe's PYTHONPATH"


# --- the stand-in browser, as a real child process ----------------------------------------------


async def test_stand_in_records_its_argv_and_exits_after_quit(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    given = [f"--user-data-dir={profile}", "--window-size=1440,900", "--app=http://x/#token=y"]
    child = await winproc.spawn(
        [sys.executable, str(_STAND_IN), *given], cwd=tmp_path, env=dict(os.environ), pipes=False
    )
    try:
        argv_file = profile / ARGV_FILE_NAME
        deadline = time.monotonic() + HANG_GUARD_S
        while not argv_file.exists():
            assert time.monotonic() < deadline, "the stand-in never recorded its argv"
            await asyncio.sleep(0.02)
        assert json.loads(argv_file.read_text(encoding="utf-8")) == given

        (profile / QUIT_FILE_NAME).write_bytes(b"")
        await asyncio.wait_for(child.wait_tree(), HANG_GUARD_S)
        assert child.process.returncode == 0
    finally:
        child.kill_tree()
        child.release()
