"""Transport-level behavior of the viki-agent CLI: env/URL checks, exit-code mapping, AST pins
on the import allowlist and the single HTTPConnection call, and the real loopback headers."""

from __future__ import annotations

import ast
import http.server
import io
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from viki_agent import agent_cli

SAMPLE_GRANT = "11111111-1111-4111-8111-111111111111"
HANG_GUARD_S = 5.0

_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "argparse",
        "collections.abc",
        "http.client",
        "json",
        "os",
        "re",
        "sys",
        "typing",
        "uuid",
    }
)


def _agent_cli_source() -> str:
    path = agent_cli.__file__
    assert path is not None
    return Path(path).read_text(encoding="utf-8")


def _imported_modules(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_agent_cli_imports_only_the_allowed_stdlib_modules() -> None:
    assert _imported_modules(_agent_cli_source()) <= _ALLOWED_IMPORTS


def _httpconnection_call_sites(tree: ast.AST) -> list[ast.Call]:
    sites = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else None)
            )
            if name == "HTTPConnection":
                sites.append(node)
    return sites


def test_the_only_httpconnection_call_uses_127_0_0_1_first() -> None:
    sites = _httpconnection_call_sites(ast.parse(_agent_cli_source()))
    assert len(sites) == 1
    first_arg = sites[0].args[0]
    assert isinstance(first_arg, ast.Constant)
    assert first_arg.value == "127.0.0.1"


def test_no_interactive_input() -> None:
    source = _agent_cli_source()
    assert "getpass" not in source
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "input"
        if isinstance(node, ast.Attribute) and node.attr == "stdin":
            assert not (isinstance(node.value, ast.Name) and node.value.id == "sys")


def _unused_send(calls: list[Any]) -> agent_cli._Call:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        calls.append((method, path, body))
        return 200, {}

    return send


def _run(
    argv: list[str], env: dict[str, str], send: agent_cli._Call | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = agent_cli.main(argv, env=env, send=send, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _status_env() -> dict[str, str]:
    return {
        "VIKI_AGENT_URL": f"http://127.0.0.1:8765/api/agent/{SAMPLE_GRANT}",
        "VIKI_AGENT_KEY": "k" * 20,
    }


def test_missing_env_exits_5_without_sending() -> None:
    calls: list[Any] = []
    code, _out, _err = _run(["status", "--json"], {}, _unused_send(calls))
    assert code == 5
    assert calls == []


BAD_URLS = [
    pytest.param(f"http://localhost:8765/api/agent/{SAMPLE_GRANT}", id="localhost"),
    pytest.param(f"http://[::1]:8765/api/agent/{SAMPLE_GRANT}", id="ipv6"),
    pytest.param(f"https://127.0.0.1:8765/api/agent/{SAMPLE_GRANT}", id="https"),
    pytest.param(f"http://10.0.0.5:8765/api/agent/{SAMPLE_GRANT}", id="other-host"),
    pytest.param(f"http://user@127.0.0.1:8765/api/agent/{SAMPLE_GRANT}", id="userinfo"),
    pytest.param(f"http://127.0.0.1:8765/api/agent/{SAMPLE_GRANT}?x=1", id="query"),
    pytest.param(f"http://127.0.0.1:99999/api/agent/{SAMPLE_GRANT}", id="port-over-65535"),
    pytest.param("http://127.0.0.1:8765/api/agent/not-a-grant", id="short-grant"),
]


@pytest.mark.parametrize("url", BAD_URLS)
def test_bad_url_exits_5_without_sending(url: str) -> None:
    calls: list[Any] = []
    env = {"VIKI_AGENT_URL": url, "VIKI_AGENT_KEY": "k" * 20}
    code, _out, _err = _run(["status", "--json"], env, _unused_send(calls))
    assert code == 5
    assert calls == []


BAD_KEYS = [
    pytest.param("short", id="too-short"),
    pytest.param("k" * 129, id="too-long"),
    pytest.param("has a space" + "x" * 10, id="bad-chars"),
]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_bad_key_exits_5_without_sending(key: str) -> None:
    calls: list[Any] = []
    env = {
        "VIKI_AGENT_URL": f"http://127.0.0.1:8765/api/agent/{SAMPLE_GRANT}",
        "VIKI_AGENT_KEY": key,
    }
    code, _out, _err = _run(["status", "--json"], env, _unused_send(calls))
    assert code == 5
    assert calls == []


def test_2xx_exits_0_and_prints_the_body() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 200, {"chat": {"session_id": "s"}}

    code, out, err = _run(["status", "--json"], _status_env(), send)
    assert code == 0
    assert err == ""
    assert json.loads(out) == {"chat": {"session_id": "s"}}


def test_success_without_json_is_indented() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 200, {"a": 1}

    code, out, err = _run(["status"], _status_env(), send)
    assert code == 0
    assert out == '{\n  "a": 1\n}\n'
    assert err == ""


def test_202_exits_3_and_prints_the_pending_body() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 202, {"pending": {"change_id": "c", "expires_in_s": 600}}

    code, out, _err = _run(["status", "--json"], _status_env(), send)
    assert code == 3
    assert json.loads(out)["pending"]["change_id"] == "c"


def test_202_with_a_run_body_exits_0() -> None:
    """A 202 without `pending` (a run already under way, not a held safety change) is not
    "waiting for Apply": it exits 0 like any other success."""

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 202, {"run": {"occurrence_id": "o1", "attempt_id": "a1", "session_id": "s1"}}

    code, out, _err = _run(["status", "--json"], _status_env(), send)
    assert code == 0
    assert json.loads(out)["run"]["occurrence_id"] == "o1"


def test_401_exits_5() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 401, {"error": {"code": "unauthorized", "message": "no"}}

    code, _out, _err = _run(["status", "--json"], _status_env(), send)
    assert code == 5


def test_409_exits_1_with_exactly_code_message_fix() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 409, {"error": {"code": "conflict", "message": "stale"}}

    code, out, err = _run(["status", "--json"], _status_env(), send)
    assert code == 1
    assert err == ""
    payload = json.loads(out)
    assert set(payload["error"]) == {"code", "message", "fix"}
    assert payload["error"] == {
        "code": "conflict",
        "message": "stale",
        "fix": "Run the command again.",
    }


def test_refused_connection_exits_4_without_json() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        raise ConnectionRefusedError

    code, out, err = _run(["status"], _status_env(), send)
    assert code == 4
    assert out == ""
    assert "error: VIKI Agent is not answering.\n" in err
    assert "fix: Open VIKI Agent, then run the command again.\n" in err


def test_refused_connection_exits_4_with_json() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        raise ConnectionRefusedError

    code, out, err = _run(["status", "--json"], _status_env(), send)
    assert code == 4
    assert err == ""
    payload = json.loads(out)
    assert payload["error"]["code"] == "unreachable"
    assert payload["error"]["fix"] == "Open VIKI Agent, then run the command again."


def test_no_reply_after_sending_exits_4_with_its_own_message() -> None:
    """`_NoReply` is `_default_sender`'s own signal that the request may have landed; `main`
    must route it to its own message, code and fix, never the refused-connection branch."""

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        raise agent_cli._NoReply("boom")

    code, out, err = _run(["status", "--json"], _status_env(), send)
    assert code == 4
    assert err == ""
    payload = json.loads(out)
    assert payload["error"]["code"] == "no_reply"
    assert payload["error"]["message"] == (
        "VIKI Agent did not answer in time; the change may have been made."
    )
    assert payload["error"]["fix"] == (
        "Run `viki-agent status --json` to check before running it again."
    )


def test_key_never_appears_in_output() -> None:
    key = "s3cr3tkeyvalue0123456789ABCDEF"
    env = {
        "VIKI_AGENT_URL": f"http://127.0.0.1:8765/api/agent/{SAMPLE_GRANT}",
        "VIKI_AGENT_KEY": key,
    }

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 401, {"error": {"code": "unauthorized", "message": "The key is wrong or expired."}}

    out, err = io.StringIO(), io.StringIO()
    agent_cli.main(["status", "--json"], env=env, send=send, out=out, err=err)
    agent_cli.main(["status"], env=env, send=send, out=out, err=err)
    assert key not in out.getvalue()
    assert key not in err.getvalue()


def test_default_sender_sends_bearer_and_no_origin() -> None:
    seen: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen["authorization"] = self.headers.get("Authorization", "")
            seen["origin"] = self.headers.get("Origin", "<absent>")
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # keep test output quiet

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        env = {
            "VIKI_AGENT_URL": f"http://127.0.0.1:{port}/api/agent/{SAMPLE_GRANT}",
            "VIKI_AGENT_KEY": "k" * 20,
        }
        code, out, _err = _run(["status", "--json"], env)  # send=None: the real transport
    finally:
        server.shutdown()
        thread.join(HANG_GUARD_S)
        server.server_close()

    assert code == 0
    assert json.loads(out) == {"ok": True}
    assert seen["authorization"] == "Bearer " + "k" * 20
    assert seen["origin"] == "<absent>"


def test_a_real_refused_connection_exits_4_with_the_unchanged_message() -> None:
    """Nothing was sent, through the real transport (not a fake `send` that merely raises
    ConnectionRefusedError): today's message, fix and code stay exactly as they were."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # bound a moment ago, never listened: the port now refuses a connection

    env = {
        "VIKI_AGENT_URL": f"http://127.0.0.1:{port}/api/agent/{SAMPLE_GRANT}",
        "VIKI_AGENT_KEY": "k" * 20,
    }
    code, out, err = _run(["status"], env)  # send=None: the real transport

    assert code == 4
    assert out == ""
    assert "error: VIKI Agent is not answering.\n" in err
    assert "fix: Open VIKI Agent, then run the command again.\n" in err


def test_timeout_after_the_request_was_sent_exits_4_with_its_own_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server accepts the connection and reads the request whole; it just never answers
    (e.g. a slow admission lock). Only the reply-wait timeout can fire here, never the
    refused-connection path, so this pins the second branch of R7(a) through the real
    transport. REPLY_TIMEOUT_S is cut to 50ms so the test waits only that long for real."""
    monkeypatch.setattr(agent_cli, "REPLY_TIMEOUT_S", 0.05)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    accepted: list[socket.socket] = []

    def accept_and_read() -> None:
        connection, _addr = listener.accept()
        accepted.append(connection)
        connection.recv(65536)  # read the request whole; never reply

    thread = threading.Thread(target=accept_and_read, daemon=True)
    thread.start()
    try:
        env = {
            "VIKI_AGENT_URL": f"http://127.0.0.1:{port}/api/agent/{SAMPLE_GRANT}",
            "VIKI_AGENT_KEY": "k" * 20,
        }
        code, out, err = _run(["status"], env)  # send=None: the real transport
    finally:
        thread.join(HANG_GUARD_S)
        for connection in accepted:
            connection.close()
        listener.close()

    assert code == 4
    assert out == ""
    assert "error: VIKI Agent did not answer in time; the change may have been made.\n" in err
    assert "fix: Run `viki-agent status --json` to check before running it again.\n" in err


def test_commands_and_exit_constants_match_the_design() -> None:
    assert {"status", "settings", "routines", "bots", "-h", "--help"} == agent_cli.COMMANDS
    assert (
        agent_cli.EXIT_OK,
        agent_cli.EXIT_REFUSED,
        agent_cli.EXIT_USAGE,
        agent_cli.EXIT_PENDING,
        agent_cli.EXIT_UNREACHABLE,
        agent_cli.EXIT_NO_KEY,
    ) == (0, 1, 2, 3, 4, 5)


def test_help_exits_0() -> None:
    # argparse's own -h/--help action prints to the real stdout and exits; only the exit code
    # is this CLI's own contract to pin here.
    code, _out, _err = _run(["--help"], {})
    assert code == 0
