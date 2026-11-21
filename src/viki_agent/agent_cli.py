"""The viki-agent CLI: a chat's own loopback client to VIKI Agent's agent surface (ADR-086).

Stdlib only. Reads `VIKI_AGENT_URL`/`VIKI_AGENT_KEY` from the chat's own environment, sends one
request per call to `127.0.0.1`, and never prompts or reads stdin.
"""

from __future__ import annotations

import argparse
import http.client  # noqa: TID251 -- loopback-only agent surface (ADR-086)
import json
import os
import re
import sys
import uuid
from collections.abc import Callable, Mapping
from typing import Any, TextIO

COMMANDS = {"status", "settings", "routines", "bots", "-h", "--help"}

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_PENDING = 3
EXIT_UNREACHABLE = 4
EXIT_NO_KEY = 5

MAX_REPLY_BYTES = 1024 * 1024

# The connect phase stays short: a refused or unroutable address should fail fast, and nothing
# has been sent yet. Once connected, the request is on its way, so the reply gets more room
# before the CLI gives up waiting for it.
CONNECT_TIMEOUT_S = 10
REPLY_TIMEOUT_S = 60

_URL_RE = re.compile(r"http://127\.0\.0\.1:([1-9]\d{0,4})/api/agent/([0-9a-f-]{36})")
_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_TIME_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")

_NO_CHAT_FIX = "Use a chat opened in the VIKI Agent window."
_UNREACHABLE_FIX = "Open VIKI Agent, then run the command again."
_NO_REPLY_FIX = "Run `viki-agent status --json` to check before running it again."
# Shared by _cmd_routines_test (the server's own 409 run_active, an attempt already open) and
# _cmd_routines_enable (the local test_running refusal, read from the routine list before any
# request): a run still shown "running": true may yet end tested or draft, so the fix must cover
# both, not claim "tested" is the only outcome (W9).
_WAIT_FIX = (
    'Check `viki-agent status --json` until the routine shows "running": false. Then '
    "tested: enable it. Still draft: the test failed, tell the owner."
)

# code -> fix, for a server-refused request (status not 2xx/202/401); an unlisted code falls
# back to _DEFAULT_FIX. A command may override a code's fix (see _cmd_routines_enable).
_FIX_BY_CODE = {
    "stale_revision": "Run the command again.",
    "conflict": "Run the command again.",
    "secret_key": "Change it in Settings.",
    "read_only": "Open Settings.",
    "too_many_pending": "Ask the owner to decide the waiting changes.",
}
_DEFAULT_FIX = "Try again, or use the VIKI Agent window."

# A call to the agent surface: method, "op/path" under the grant, optional JSON body -> (status,
# parsed JSON object). Loose on purpose: callers invoke it with 2 or 3 positional arguments.
_Call = Callable[..., tuple[int, dict[str, Any]]]


class _CliError(Exception):
    """A request this CLI refuses to send, or a value it cannot make sense of. Exit 1."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        self.code, self.message, self.fix = code, message, fix
        super().__init__(code)


class _HttpFailure(Exception):
    """A preparatory GET came back other than 2xx; `main` renders it like any other reply."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status, self.body = status, body
        super().__init__(str(status))


class _NoReply(OSError):
    """The socket connected, so the request was sent (or was on its way); no whole reply came
    back before the reply timeout, or the connection dropped while waiting for one. A refused
    or failed connection is a plain OSError instead: nothing was sent then."""


def _leaf(sub: Any, name: str) -> argparse.ArgumentParser:
    leaf = sub.add_parser(name)
    leaf.add_argument("--json", action="store_true")
    return leaf


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="viki-agent")
    sub = top.add_subparsers(dest="command", required=True)

    _leaf(sub, "status").set_defaults(handler=_cmd_status)

    settings_parser = sub.add_parser("settings")
    settings_sub = settings_parser.add_subparsers(dest="settings_command", required=True)

    settings_get = _leaf(settings_sub, "get")
    settings_get.add_argument("key", nargs="?", default=None)
    settings_get.set_defaults(handler=_cmd_settings_get)

    settings_set = _leaf(settings_sub, "set")
    settings_set.add_argument("key")
    settings_set.add_argument("value", nargs="?", default=None)
    settings_set.add_argument("--reset", action="store_true")
    settings_set.set_defaults(handler=_cmd_settings_set)

    routines_parser = sub.add_parser("routines")
    routines_sub = routines_parser.add_subparsers(dest="routines_command", required=True)

    routines_add = _leaf(routines_sub, "add")
    routines_add.add_argument("--name", required=True)
    prompt_group = routines_add.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-file")
    routines_add.add_argument("--profile", required=True)
    routines_add.add_argument("--workspace")
    routines_add.add_argument("--purpose", default="")
    routines_add.add_argument("--deadline-minutes", type=int, default=30)
    schedule_group = routines_add.add_mutually_exclusive_group()
    schedule_group.add_argument("--daily")
    schedule_group.add_argument("--weekly")
    routines_add.add_argument("--at")
    routines_add.add_argument("--timezone")
    routines_add.add_argument("--stop-at-confirmation", action="store_true")
    routines_add.set_defaults(handler=_cmd_routines_add)

    routines_test = _leaf(routines_sub, "test")
    routines_test.add_argument("id")
    routines_test.set_defaults(handler=_cmd_routines_test)

    routines_enable = _leaf(routines_sub, "enable")
    routines_enable.add_argument("id")
    routines_enable.set_defaults(handler=_cmd_routines_enable)

    bots_parser = sub.add_parser("bots")
    bots_sub = bots_parser.add_subparsers(dest="bots_command", required=True)

    bots_create = _leaf(bots_sub, "create")
    bots_create.add_argument("--profile", required=True)
    bots_create.add_argument("--workspace")
    bots_create.set_defaults(handler=_cmd_bots_create)

    bots_usage = _leaf(bots_sub, "usage")
    bots_usage.add_argument("--days", type=int, choices=(7, 30, 90), default=30)
    bots_usage.set_defaults(handler=_cmd_bots_usage)

    bots_trace = _leaf(bots_sub, "trace")
    bots_trace.add_argument("id")
    bots_trace.set_defaults(handler=_cmd_bots_trace)

    return top


def main(
    argv: list[str],
    *,
    env: Mapping[str, str] | None = None,
    send: _Call | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    environment = os.environ if env is None else env
    top = parser()
    try:
        args = top.parse_args(list(argv))
        if args.handler is _cmd_settings_set:
            if args.reset and args.value is not None:
                top.error("settings set takes either VALUE or --reset, not both.")
            if not args.reset and args.value is None:
                top.error("settings set needs VALUE or --reset.")
    except SystemExit as exit_signal:
        code = exit_signal.code
        return code if isinstance(code, int) else EXIT_USAGE

    as_json = bool(getattr(args, "json", False))
    handler = getattr(args, "handler", None)
    if handler is None:
        top.print_help(out)
        return EXIT_USAGE

    url_match = _URL_RE.fullmatch(environment.get("VIKI_AGENT_URL", ""))
    key = environment.get("VIKI_AGENT_KEY", "")
    if url_match is None or int(url_match.group(1)) > 65535 or not _KEY_RE.fullmatch(key):
        return _emit_error(
            as_json,
            out,
            err,
            EXIT_NO_KEY,
            "no_agent_chat",
            "This chat has no VIKI Agent connection.",
            _NO_CHAT_FIX,
        )

    base_path = f"/api/agent/{url_match.group(2)}"
    sender = send if send is not None else _default_sender(int(url_match.group(1)), key)

    def call(
        method: str, op: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        return sender(method, f"{base_path}/{op}", body)

    try:
        return handler(args, call, as_json, out, err)
    except _HttpFailure as failure:
        return _respond(failure.body, failure.status, as_json, out, err)
    except _CliError as cli_error:
        return _emit_error(
            as_json, out, err, EXIT_REFUSED, cli_error.code, cli_error.message, cli_error.fix
        )
    except _NoReply:
        return _emit_error(
            as_json,
            out,
            err,
            EXIT_UNREACHABLE,
            "no_reply",
            "VIKI Agent did not answer in time; the change may have been made.",
            _NO_REPLY_FIX,
        )
    except OSError:
        return _emit_error(
            as_json,
            out,
            err,
            EXIT_UNREACHABLE,
            "unreachable",
            "VIKI Agent is not answering.",
            _UNREACHABLE_FIX,
        )


def _default_sender(port: int, key: str) -> _Call:
    def send(
        method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        payload = None if body is None else json.dumps(body, ensure_ascii=True).encode("ascii")
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(  # noqa: TID251
            "127.0.0.1", port, timeout=CONNECT_TIMEOUT_S
        )
        try:
            connection.connect()  # a refusal or failure here: nothing was sent.
            assert connection.sock is not None
            connection.sock.settimeout(REPLY_TIMEOUT_S)
            try:
                connection.request(method, path, body=payload, headers=headers)
                response = connection.getresponse()
                data = bytearray()
                while len(data) <= MAX_REPLY_BYTES:
                    chunk = response.read(MAX_REPLY_BYTES + 1 - len(data))
                    if not chunk:
                        break
                    data += chunk
                if len(data) > MAX_REPLY_BYTES:
                    raise OSError("VIKI Agent's reply was too large.")
                try:
                    parsed = json.loads(data.decode("utf-8"))
                except ValueError as error:
                    raise OSError("VIKI Agent's reply was not valid JSON.") from error
                if not isinstance(parsed, dict):
                    raise OSError("VIKI Agent's reply was not a JSON object.")
                return response.status, parsed
            except (http.client.HTTPException, OSError) as error:  # noqa: TID251
                raise _NoReply(str(error)) from error
        finally:
            connection.close()

    return send


def _print_body(body: dict[str, Any], as_json: bool, out: TextIO) -> None:
    if as_json:
        out.write(json.dumps(body, ensure_ascii=True, separators=(",", ":")))
    else:
        out.write(json.dumps(body, ensure_ascii=True, indent=2))
    out.write("\n")


def _emit_error(
    as_json: bool,
    out: TextIO,
    err: TextIO,
    exit_code: int,
    code: str,
    message: str,
    fix: str,
    extra: dict[str, Any] | None = None,
) -> int:
    if as_json:
        _print_body(
            {"error": {"code": code, "message": message, "fix": fix, **(extra or {})}}, True, out
        )
    else:
        err.write(f"error: {message}\n")
        blockers = (extra or {}).get("blockers")
        if isinstance(blockers, list):
            err.write(f"blockers: {', '.join(str(item) for item in blockers)}\n")
        fields = (extra or {}).get("fields")
        if isinstance(fields, dict):
            err.write(f"fields: {', '.join(f'{key}={value}' for key, value in fields.items())}\n")
        err.write(f"fix: {fix}\n")
    return exit_code


def _default_fix(code: str) -> str:
    return _FIX_BY_CODE.get(code, _DEFAULT_FIX)


def _respond(
    body: dict[str, Any],
    status: int,
    as_json: bool,
    out: TextIO,
    err: TextIO,
    *,
    fix_for: Callable[[str], str | None] | None = None,
) -> int:
    """Render one server reply: a 202 holding `pending` (a held safety change) exits 3; any
    other 2xx, a 202 with `run` included (a run already under way), prints the body as usual
    and exits 0; else an error with a fix."""
    if status == 202 and isinstance(body.get("pending"), dict):
        _print_body(body, as_json, out)
        return EXIT_PENDING
    if 200 <= status < 300:
        _print_body(body, as_json, out)
        return EXIT_OK
    error = body.get("error")
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code", "error"))
    message = str(error.get("message", "VIKI Agent refused the request."))
    if status == 401:
        return _emit_error(as_json, out, err, EXIT_NO_KEY, code, message, _NO_CHAT_FIX)
    fix = (fix_for(code) if fix_for is not None else None) or _default_fix(code)
    extra = {key: error[key] for key in ("blockers", "fields") if key in error}
    return _emit_error(as_json, out, err, EXIT_REFUSED, code, message, fix, extra)


def _get(call: _Call, op: str) -> dict[str, Any]:
    status, body = call("GET", op)
    if not (200 <= status < 300):
        raise _HttpFailure(status, body)
    return body


def _typed(value: str, info: object) -> bool | int | float | str:
    kind = info.get("kind") if isinstance(info, dict) else None
    if kind == "bool":
        if value in ("true", "false"):
            return value == "true"
        raise _CliError("invalid_value", f"{value!r} is not true or false.", "Use true or false.")
    if kind == "number":
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            raise _CliError(
                "invalid_value", f"{value!r} is not a number.", "Use a number."
            ) from None
    return value


def _filtered_settings(body: dict[str, Any], key: str) -> dict[str, Any]:
    result = dict(body)
    for field in ("values", "effective", "sources", "keys"):
        section = body.get(field)
        if isinstance(section, dict):
            result[field] = {key: section[key]} if key in section else {}
    return result


def _parse_times(raw: str) -> list[str]:
    values = [item for item in raw.split(",") if item]
    if not values:
        raise _CliError("invalid_time", "No times given.", "Use HH:MM, for example 09:00.")
    for item in values:
        if not _TIME_RE.fullmatch(item):
            raise _CliError(
                "invalid_time", f"{item!r} is not an HH:MM time.", "Use 24-hour times like 09:00."
            )
    return sorted(set(values))


def _parse_days(raw: str) -> list[int]:
    values = [item for item in raw.split(",") if item]
    if not values:
        raise _CliError("invalid_day", "No days given.", "Use 1-7, Monday=1, like 1,3,5.")
    days: list[int] = []
    for item in values:
        if not item.isdigit() or not 1 <= int(item) <= 7:
            raise _CliError(
                "invalid_day", f"{item!r} is not a day 1-7 (Monday=1).", "Use 1-7, Monday=1."
            )
        days.append(int(item))
    return sorted(set(days))


def _active_workspace(status_body: dict[str, Any]) -> str:
    for workspace in status_body.get("workspaces") or []:
        if isinstance(workspace, dict) and workspace.get("active"):
            workspace_id = workspace.get("workspace_id")
            if isinstance(workspace_id, str):
                return workspace_id
    raise _CliError(
        "no_active_workspace", "No active workspace.", "Open VIKI Agent and pick a workspace."
    )


def _profile_revision(status_body: dict[str, Any], profile_id: str) -> int:
    for profile in status_body.get("profiles") or []:
        if isinstance(profile, dict) and profile.get("profile_id") == profile_id:
            revision = profile.get("current_revision")
            if isinstance(revision, int):
                return revision
    raise _CliError(
        "unknown_profile",
        f"Unknown profile ID: {profile_id}.",
        "Check the ID with viki-agent status --json",
    )


def _find_routine(call: _Call, routine_id: str) -> dict[str, Any]:
    body = _get(call, "routines")
    for routine in body.get("routines") or []:
        if isinstance(routine, dict) and routine.get("routine_id") == routine_id:
            return routine
    raise _CliError(
        "unknown_routine",
        f"Unknown routine ID: {routine_id}.",
        "Check the ID with viki-agent status --json",
    )


def _cmd_status(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    status, body = call("GET", "status")
    return _respond(body, status, as_json, out, err)


def _cmd_settings_get(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    body = _get(call, "settings")
    if args.key is not None:
        keys = body.get("keys")
        keys = keys if isinstance(keys, dict) else {}
        if args.key not in keys:
            raise _CliError(
                "unknown_key",
                f"Unknown setting key: {args.key}.",
                "Run `viki-agent settings get --json` to list keys.",
            )
        body = _filtered_settings(body, args.key)
    _print_body(body, as_json, out)
    return EXIT_OK


def _cmd_settings_set(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    body = _get(call, "settings")
    keys = body.get("keys")
    keys = keys if isinstance(keys, dict) else {}
    if args.key not in keys:
        raise _CliError(
            "unknown_key",
            f"Unknown setting key: {args.key}.",
            "Run `viki-agent settings get --json` to list keys.",
        )
    value: Any = None if args.reset else _typed(args.value, keys[args.key])
    status, result = call(
        "PATCH",
        "settings",
        {
            "command_id": str(uuid.uuid4()),
            "expected_revision": body.get("revision"),
            "key": args.key,
            "value": value,
        },
    )

    def fix_for(code: str) -> str | None:
        return (
            f"Run `viki-agent settings get {args.key} --json`."
            if code == "invalid_settings"
            else None
        )

    return _respond(result, status, as_json, out, err, fix_for=fix_for)


def _cmd_routines_add(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    if args.prompt is not None:
        prompt = args.prompt
    else:
        try:
            with open(args.prompt_file, encoding="utf-8") as handle:
                prompt = handle.read()
        except OSError as error:
            raise _CliError(
                "prompt_file", f"Can't read {args.prompt_file}.", "Check the file path."
            ) from error

    if args.at is not None and args.weekly is None:
        raise _CliError(
            "unexpected_at",
            "--at only applies with --weekly.",
            "Add --weekly D[,...], or drop --at.",
        )

    if args.daily is not None:
        kind, times, days = "daily", _parse_times(args.daily), []
    elif args.weekly is not None:
        if args.at is None:
            raise _CliError(
                "missing_at",
                "--weekly needs --at HH:MM[,...].",
                "Add --at, for example --at 09:00.",
            )
        kind, times, days = "weekly", _parse_times(args.at), _parse_days(args.weekly)
    else:
        kind, times, days = "manual", [], []

    if kind == "manual":
        timezone = None
    elif args.timezone is None:
        raise _CliError(
            "missing_timezone",
            "Daily and weekly routines need a timezone.",
            "Add --timezone, for example --timezone Asia/Tokyo",
        )
    else:
        timezone = args.timezone

    workspace_id = (
        args.workspace if args.workspace is not None else _active_workspace(_get(call, "status"))
    )

    document: dict[str, Any] = {
        "name": args.name,
        "purpose": args.purpose,
        "prompt": prompt,
        "profile_id": args.profile,
        "workspace_id": workspace_id,
        "deadline_minutes": args.deadline_minutes,
        "schedule": {
            "kind": kind,
            "times": times,
            "days": days,
            "timezone": timezone,
            "start_date": None,
            "end_date": None,
            "catch_up": "skip",
        },
    }
    if args.stop_at_confirmation:
        document["on_confirmation"] = "stop"

    status, body = call("POST", "routines", {"command_id": str(uuid.uuid4()), "document": document})
    return _respond(body, status, as_json, out, err)


def _cmd_routines_test(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    routine = _find_routine(call, args.id)
    status, body = call(
        "POST",
        f"routines/{args.id}/run",
        {"command_id": str(uuid.uuid4()), "expected_revision": routine["current_revision"]},
    )

    def fix_for(code: str) -> str | None:
        # The real refusal for an attempt already open (tasks.py's `occupancy`); `enable` can
        # never answer this code, only `run` (W9).
        return _WAIT_FIX if code == "run_active" else None

    return _respond(body, status, as_json, out, err, fix_for=fix_for)


def _cmd_routines_enable(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    routine = _find_routine(call, args.id)
    if routine.get("running"):
        raise _CliError("test_running", "The test run has not finished yet.", _WAIT_FIX)
    status, body = call(
        "POST",
        f"routines/{args.id}/enable",
        {"command_id": str(uuid.uuid4()), "expected_state_revision": routine["state_revision"]},
    )

    def fix_for(code: str) -> str | None:
        if code == "invalid_transition":
            return "Test it first: viki-agent routines test ID"
        if code == "not_ready":
            return (
                "Change what the blockers name in VIKI Agent (for example a profile's "
                "permission mode or the workspace), then try again."
            )
        return None

    return _respond(body, status, as_json, out, err, fix_for=fix_for)


def _cmd_bots_create(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    status_body = _get(call, "status")
    profile_revision = _profile_revision(status_body, args.profile)
    workspace_id = args.workspace if args.workspace is not None else _active_workspace(status_body)
    status, body = call(
        "POST",
        "bots",
        {
            "bot_id": str(uuid.uuid4()),
            "profile_id": args.profile,
            "expected_profile_revision": profile_revision,
            "workspace_id": workspace_id,
        },
    )

    def fix_for(code: str) -> str | None:
        return (
            "A Bot already exists for that profile and workspace; check `viki-agent status --json`."
            if code == "conflict"
            else None
        )

    return _respond(body, status, as_json, out, err, fix_for=fix_for)


def _cmd_bots_usage(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    status, body = call("GET", f"usage/{args.days}")
    return _respond(body, status, as_json, out, err)


def _cmd_bots_trace(
    args: argparse.Namespace, call: _Call, as_json: bool, out: TextIO, err: TextIO
) -> int:
    status, body = call("GET", f"usage/sessions/{args.id}")
    return _respond(body, status, as_json, out, err)
