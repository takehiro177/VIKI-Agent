"""Per-command business logic of the viki-agent CLI, against an injected `send`: the settings
value typing, the routine document shapes, the workspace/profile lookups, and the per-command
fix overrides."""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from typing import Any

from viki_agent import agent_cli

SAMPLE_GRANT = "11111111-1111-4111-8111-111111111111"


def _status_env() -> dict[str, str]:
    return {
        "VIKI_AGENT_URL": f"http://127.0.0.1:8765/api/agent/{SAMPLE_GRANT}",
        "VIKI_AGENT_KEY": "k" * 20,
    }


def _run(argv: list[str], send: agent_cli._Call | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = agent_cli.main(argv, env=_status_env(), send=send, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def _unused_send(calls: list[Any]) -> agent_cli._Call:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        calls.append((method, path, body))
        return 200, {}

    return send


def _settings_view(keys: dict[str, dict[str, Any]], revision: int = 7) -> dict[str, Any]:
    return {
        "revision": revision,
        "read_only": False,
        "values": {},
        "effective": {},
        "sources": {},
        "keys": keys,
    }


# --- settings set: typed values ------------------------------------------------------------


def test_settings_set_types_a_bool_value() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, _settings_view({"decision.enabled": {"kind": "bool"}})
        sent.update(body or {})
        return 200, {"applied": True, "revision": 8}

    code, _out, _err = _run(["settings", "set", "decision.enabled", "true", "--json"], send)
    assert code == 0
    assert sent == {
        "command_id": sent["command_id"],
        "expected_revision": 7,
        "key": "decision.enabled",
        "value": True,
    }
    uuid.UUID(sent["command_id"])  # a real uuid4 command id


def test_settings_set_types_an_int_value() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, _settings_view({"decision.limit.daily": {"kind": "number"}})
        sent.update(body or {})
        return 200, {"applied": True}

    code, _out, _err = _run(["settings", "set", "decision.limit.daily", "42", "--json"], send)
    assert code == 0
    assert sent["value"] == 42
    assert isinstance(sent["value"], int)


def test_settings_set_types_a_float_value() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, _settings_view({"x": {"kind": "number"}})
        sent.update(body or {})
        return 200, {"applied": True}

    code, _out, _err = _run(["settings", "set", "x", "1.5", "--json"], send)
    assert code == 0
    assert sent["value"] == 1.5
    assert isinstance(sent["value"], float)


def test_settings_set_keeps_a_string_value_as_is() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, _settings_view({"chat.default_mode": {"kind": "string"}})
        sent.update(body or {})
        return 200, {"applied": True}

    code, _out, _err = _run(["settings", "set", "chat.default_mode", "auto", "--json"], send)
    assert code == 0
    assert sent["value"] == "auto"


def test_settings_set_reset_sends_null() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, _settings_view({"chat.default_mode": {"kind": "string"}})
        sent.update(body or {})
        return 200, {"applied": True}

    code, _out, _err = _run(["settings", "set", "chat.default_mode", "--reset", "--json"], send)
    assert code == 0
    assert sent["value"] is None


def test_settings_set_unknown_key_exits_1_without_patch() -> None:
    patched = False

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        nonlocal patched
        if method == "GET":
            return 200, _settings_view({})
        patched = True
        return 200, {}

    code, _out, _err = _run(["settings", "set", "nope.nope", "x"], send)
    assert code == 1
    assert patched is False


def test_settings_set_invalid_settings_fix_names_the_key() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, _settings_view({"chat.default_mode": {"kind": "string"}})
        return 400, {"error": {"code": "invalid_settings", "message": "bad", "fields": ["x"]}}

    code, _out, err = _run(["settings", "set", "chat.default_mode", "nope"], send)
    assert code == 1
    assert "fix: Run `viki-agent settings get chat.default_mode --json`.\n" in err


def test_settings_set_invalid_settings_surfaces_fields() -> None:
    """W4: `invalid_settings`'s `fields` (field name -> problem code) must reach the chat."""

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, _settings_view({"chat.default_mode": {"kind": "string"}})
        return 400, {
            "error": {
                "code": "invalid_settings",
                "message": "bad",
                "fields": {"chat.default_mode": "invalid_type"},
            }
        }

    code, out, _err = _run(["settings", "set", "chat.default_mode", "nope", "--json"], send)
    assert code == 1
    payload = json.loads(out)
    assert payload["error"]["fields"] == {"chat.default_mode": "invalid_type"}

    code, _out, err = _run(["settings", "set", "chat.default_mode", "nope"], send)
    assert code == 1
    assert "fields: chat.default_mode=invalid_type\n" in err
    assert "fix: Run `viki-agent settings get chat.default_mode --json`.\n" in err


def test_settings_get_with_key_filters_to_that_key() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 200, _settings_view({"a": {"kind": "string"}, "b": {"kind": "bool"}})

    code, out, _err = _run(["settings", "get", "a", "--json"], send)
    assert code == 0
    payload = json.loads(out)
    assert set(payload["keys"]) == {"a"}


def test_settings_get_unknown_key_exits_1() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 200, _settings_view({"a": {"kind": "string"}})

    code, _out, _err = _run(["settings", "get", "nope"], send)
    assert code == 1


# --- routines add: document shapes ----------------------------------------------------------


def _created_routine() -> tuple[int, dict[str, Any]]:
    return 201, {
        "routine": {"routine_id": "r1", "revision": 1, "state": "draft", "state_revision": 1}
    }


def test_routines_add_manual_document() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        sent.update(body or {})
        return _created_routine()

    code, _out, _err = _run(
        [
            "routines",
            "add",
            "--name",
            "Morning review",
            "--prompt",
            "Summarize today.",
            "--profile",
            "p1",
            "--workspace",
            "w1",
            "--json",
        ],
        send,
    )
    assert code == 0
    document = sent["document"]
    assert document["name"] == "Morning review"
    assert document["prompt"] == "Summarize today."
    assert document["profile_id"] == "p1"
    assert document["workspace_id"] == "w1"
    assert document["deadline_minutes"] == 30
    assert document["schedule"] == {
        "kind": "manual",
        "times": [],
        "days": [],
        "timezone": None,
        "start_date": None,
        "end_date": None,
        "catch_up": "skip",
    }
    assert "on_confirmation" not in document


def test_routines_add_stop_at_confirmation_sets_on_confirmation() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        sent.update(body or {})
        return _created_routine()

    code, _out, _err = _run(
        [
            "routines",
            "add",
            "--name",
            "N",
            "--prompt",
            "Go.",
            "--profile",
            "p1",
            "--workspace",
            "w1",
            "--stop-at-confirmation",
            "--json",
        ],
        send,
    )
    assert code == 0
    assert sent["document"]["on_confirmation"] == "stop"


def test_routines_add_daily_document() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        sent.update(body or {})
        return _created_routine()

    code, _out, _err = _run(
        [
            "routines",
            "add",
            "--name",
            "Daily",
            "--prompt",
            "Go.",
            "--profile",
            "p1",
            "--workspace",
            "w1",
            "--daily",
            "18:00,09:00,09:00",
            "--timezone",
            "Asia/Tokyo",
            "--json",
        ],
        send,
    )
    assert code == 0
    schedule = sent["document"]["schedule"]
    assert schedule["kind"] == "daily"
    assert schedule["times"] == ["09:00", "18:00"]  # sorted, deduped
    assert schedule["days"] == []
    assert schedule["timezone"] == "Asia/Tokyo"


def test_routines_add_weekly_document() -> None:
    """Weekly's times come from --at, not --daily; this fails if that wiring is broken."""
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        sent.update(body or {})
        return _created_routine()

    code, _out, _err = _run(
        [
            "routines",
            "add",
            "--name",
            "Weekly",
            "--prompt",
            "Go.",
            "--profile",
            "p1",
            "--workspace",
            "w1",
            "--weekly",
            "5,1,1,3",
            "--at",
            "09:00,08:00",
            "--timezone",
            "Asia/Tokyo",
            "--json",
        ],
        send,
    )
    assert code == 0
    schedule = sent["document"]["schedule"]
    assert schedule["kind"] == "weekly"
    assert schedule["days"] == [1, 3, 5]  # sorted, deduped
    assert schedule["times"] == ["08:00", "09:00"]  # from --at, sorted, deduped


def test_routines_add_weekly_without_at_exits_1() -> None:
    calls: list[Any] = []
    code, _out, err = _run(
        [
            "routines",
            "add",
            "--name",
            "Weekly",
            "--prompt",
            "Go.",
            "--profile",
            "p1",
            "--workspace",
            "w1",
            "--weekly",
            "1,3",
            "--timezone",
            "Asia/Tokyo",
        ],
        _unused_send(calls),
    )
    assert code == 1
    assert calls == []
    assert "--weekly needs --at" in err


def test_routines_add_prompt_file_is_read_as_utf8(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("日次レビューをお願い。", encoding="utf-8")
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        sent.update(body or {})
        return _created_routine()

    code, _out, _err = _run(
        [
            "routines",
            "add",
            "--name",
            "From file",
            "--prompt-file",
            str(prompt_file),
            "--profile",
            "p1",
            "--workspace",
            "w1",
            "--json",
        ],
        send,
    )
    assert code == 0
    assert sent["document"]["prompt"] == "日次レビューをお願い。"


def test_routines_add_daily_without_timezone_exits_1_with_fix() -> None:
    calls: list[Any] = []
    code, _out, err = _run(
        [
            "routines",
            "add",
            "--name",
            "Daily",
            "--prompt",
            "Go.",
            "--profile",
            "p1",
            "--workspace",
            "w1",
            "--daily",
            "09:00",
        ],
        _unused_send(calls),
    )
    assert code == 1
    assert calls == []
    assert "fix: Add --timezone, for example --timezone Asia/Tokyo\n" in err


def test_routines_add_weekly_without_timezone_exits_1_with_fix() -> None:
    calls: list[Any] = []
    code, _out, err = _run(
        [
            "routines",
            "add",
            "--name",
            "Weekly",
            "--prompt",
            "Go.",
            "--profile",
            "p1",
            "--workspace",
            "w1",
            "--weekly",
            "1",
            "--at",
            "09:00",
        ],
        _unused_send(calls),
    )
    assert code == 1
    assert calls == []
    assert "fix: Add --timezone, for example --timezone Asia/Tokyo\n" in err


def test_routines_add_without_workspace_uses_the_active_one() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, {
                "workspaces": [
                    {"workspace_id": "w0", "name": "x", "policy": "ordinary", "active": False},
                    {"workspace_id": "w1", "name": "y", "policy": "ordinary", "active": True},
                ]
            }
        sent.update(body or {})
        return _created_routine()

    code, _out, _err = _run(
        ["routines", "add", "--name", "N", "--prompt", "Go.", "--profile", "p1", "--json"], send
    )
    assert code == 0
    assert sent["document"]["workspace_id"] == "w1"


# --- routines test/enable: the revisions read, and enable's fix -----------------------------


def _routines_list(current_revision: int = 3, state_revision: int = 9) -> dict[str, Any]:
    return {
        "routines": [
            {
                "routine_id": "r1",
                "name": "N",
                "state": "tested",
                "current_revision": current_revision,
                "state_revision": state_revision,
            }
        ]
    }


def test_routines_test_sends_the_current_revision() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path.endswith("/routines"):
            return 200, _routines_list()
        sent["path"], sent["body"] = path, body
        return 202, {"run": {"occurrence_id": "o1", "attempt_id": "a1", "session_id": "s1"}}

    code, _out, _err = _run(["routines", "test", "r1", "--json"], send)
    assert code == 0  # 202 with "run": the test run has started, not a held safety change
    assert sent["path"].endswith("/routines/r1/run")
    assert sent["body"]["expected_revision"] == 3


def test_routines_enable_sends_the_state_revision() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path.endswith("/routines"):
            return 200, _routines_list()
        sent["path"], sent["body"] = path, body
        return 202, {"pending": {"change_id": "c1", "expires_in_s": 600}}

    code, _out, _err = _run(["routines", "enable", "r1", "--json"], send)
    assert code == 3
    assert sent["path"].endswith("/routines/r1/enable")
    assert sent["body"]["expected_state_revision"] == 9


def test_routines_test_run_active_from_the_server_gets_the_wait_fix() -> None:
    """W9: unlike `enable`, the real `run` route can answer 409 `run_active` -- an attempt for
    this routine is already open (tasks.py's `occupancy`) -- and it must get the wait fix, not
    the generic default (fails without the fix_for mapping in _cmd_routines_test)."""

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path.endswith("/routines"):
            return 200, _routines_list()
        return 409, {"error": {"code": "run_active", "message": "busy"}}

    code, _out, err = _run(["routines", "test", "r1"], send)
    assert code == 1
    assert (
        'fix: Check `viki-agent status --json` until the routine shows "running": false. Then '
        "tested: enable it. Still draft: the test failed, tell the owner.\n" in err
    )


def test_routines_test_refusals_name_the_owner_s_way_instead() -> None:
    """The agent surface's narrowing: a routine another chat or the owner added, and one whose
    profile has since gained MCP servers or a Bot, each get a fix that sends the chat to the
    owner rather than "again"."""
    for refused, fix in (
        (
            "not_added_here",
            "Only a routine this chat added can be tested from a chat. "
            "Ask the owner to run it from Routines.",
        ),
        (
            "profile_not_allowed",
            "This routine's profile now has MCP servers or a Bot. "
            "Ask the owner to run it from Routines.",
        ),
    ):

        def send(
            method: str, path: str, body: dict[str, Any] | None, code_: str = refused
        ) -> tuple[int, dict[str, Any]]:
            if method == "GET" and path.endswith("/routines"):
                return 200, _routines_list()
            return 403, {"error": {"code": code_, "message": "refused"}}

        code, out, _err = _run(["routines", "test", "r1", "--json"], send)
        assert code == 1
        assert json.loads(out)["error"] == {"code": refused, "message": "refused", "fix": fix}


def test_routines_add_for_a_profile_with_mcp_servers_or_a_bot_gets_its_fix() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 403, {"error": {"code": "profile_not_allowed", "message": "refused"}}

    code, _out, err = _run(
        [
            "routines",
            "add",
            "--name",
            "N",
            "--prompt",
            "P",
            "--profile",
            "p1",
            "--workspace",
            "w1",
        ],
        send,
    )
    assert code == 1
    assert (
        "fix: A chat can add routines only for a profile without MCP servers or a Bot. "
        "Ask the owner to add it in Routines.\n" in err
    )


def test_routines_test_unknown_id_exits_1() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 200, _routines_list()

    code, _out, err = _run(["routines", "test", "unknown-id"], send)
    assert code == 1
    assert "fix: Check the ID with viki-agent status --json\n" in err


def test_routines_enable_invalid_transition_fix() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path.endswith("/routines"):
            return 200, _routines_list()
        return 409, {"error": {"code": "invalid_transition", "message": "not ready"}}

    code, _out, err = _run(["routines", "enable", "r1"], send)
    assert code == 1
    assert "fix: Test it first: viki-agent routines test ID\n" in err


def test_routines_enable_not_ready_names_the_blockers_and_a_fix() -> None:
    """W4: `not_ready`'s `blockers` must reach the chat, with a fix that says to act on them,
    not the generic default."""

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path.endswith("/routines"):
            return 200, _routines_list()
        return 422, {
            "error": {
                "code": "not_ready",
                "message": "blocked",
                "blockers": ["profile_archived", "workspace_missing"],
            }
        }

    code, out, _err = _run(["routines", "enable", "r1", "--json"], send)
    assert code == 1
    payload = json.loads(out)
    assert payload["error"]["blockers"] == ["profile_archived", "workspace_missing"]

    code, _out, err = _run(["routines", "enable", "r1"], send)
    assert code == 1
    assert "blockers: profile_archived, workspace_missing\n" in err
    assert (
        "fix: Change what the blockers name in VIKI Agent (for example a profile's "
        "permission mode or the workspace), then try again.\n" in err
    )


def test_routines_enable_while_the_test_run_is_active_exits_1_without_enabling() -> None:
    """W3/W7/W9: the routine list already shows an open test run (`running: true`, the shape
    the real server sends); enable must refuse locally with the wait fix -- naming "running":
    false and the draft case, since a running test need not end tested -- never send the enable
    request, and never say "Test it first" (the run has already started)."""
    posted = False

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        nonlocal posted
        if method == "GET" and path.endswith("/routines"):
            routines = _routines_list()
            routines["routines"][0]["running"] = True
            return 200, routines
        posted = True
        return 202, {"pending": {"change_id": "c1", "expires_in_s": 600}}

    code, _out, err = _run(["routines", "enable", "r1"], send)
    assert code == 1
    assert posted is False
    assert "error: The test run has not finished yet.\n" in err
    assert (
        'fix: Check `viki-agent status --json` until the routine shows "running": false. Then '
        "tested: enable it. Still draft: the test failed, tell the owner.\n" in err
    )


def test_routines_enable_run_active_from_the_server_is_not_specially_mapped() -> None:
    """W9: `RoutineService._change` and `enabling` never raise `run_active` for the enable op
    (only `run`'s `occupancy` check does), so `_cmd_routines_enable`'s fix_for no longer maps
    it; an unexpected one falls through to the generic fix, like any other unmapped code."""

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path.endswith("/routines"):
            return 200, _routines_list()
        return 409, {"error": {"code": "run_active", "message": "busy"}}

    code, _out, err = _run(["routines", "enable", "r1"], send)
    assert code == 1
    assert "fix: Try again, or use the VIKI Agent window.\n" in err


# --- bots create: the bot id shape -----------------------------------------------------------


def test_bots_create_sends_a_fresh_uuid4_bot_id_and_profile_revision() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path.endswith("/status"):
            return 200, {
                "profiles": [{"profile_id": "p1", "name": "x", "current_revision": 4}],
                "workspaces": [
                    {"workspace_id": "w1", "name": "x", "policy": "ordinary", "active": True}
                ],
            }
        sent.update(body or {})
        return 201, {
            "bot": {"bot_id": sent.get("bot_id"), "profile_id": "p1", "workspace_id": "w1"}
        }

    code, _out, _err = _run(["bots", "create", "--profile", "p1", "--json"], send)
    assert code == 0
    assert sent["profile_id"] == "p1"
    assert sent["expected_profile_revision"] == 4
    assert sent["workspace_id"] == "w1"
    assert isinstance(sent["bot_id"], str)
    assert uuid.UUID(sent["bot_id"]).version == 4


def test_bots_create_unknown_profile_exits_1() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 200, {"profiles": [], "workspaces": []}

    code, _out, err = _run(["bots", "create", "--profile", "nope"], send)
    assert code == 1
    assert "fix: Check the ID with viki-agent status --json\n" in err


def test_bots_create_without_bots_feature_exits_1() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, {
                "profiles": [{"profile_id": "p1", "name": "x", "current_revision": 1}],
                "workspaces": [
                    {"workspace_id": "w1", "name": "x", "policy": "ordinary", "active": True}
                ],
            }
        return 503, {"error": {"code": "unavailable", "message": "Bots are off."}}

    code, _out, err = _run(["bots", "create", "--profile", "p1"], send)
    assert code == 1
    assert "fix: Try again, or use the VIKI Agent window.\n" in err


def test_bots_create_conflict_names_status_check_not_run_again() -> None:
    """A duplicate profile+workspace Bot is a 409 `conflict` that can never clear by retrying
    (a fresh bot_id each time still collides); the fix must point at status, not "again" (R7b).
    """

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, {
                "profiles": [{"profile_id": "p1", "name": "x", "current_revision": 1}],
                "workspaces": [
                    {"workspace_id": "w1", "name": "x", "policy": "ordinary", "active": True}
                ],
            }
        return 409, {"error": {"code": "conflict", "message": "A Bot already exists."}}

    code, _out, err = _run(["bots", "create", "--profile", "p1"], send)
    assert code == 1
    assert (
        "fix: A Bot already exists for that profile and workspace; check "
        "`viki-agent status --json`.\n" in err
    )


def test_bots_create_for_a_profile_that_runs_routines_gets_its_fix() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, {
                "profiles": [{"profile_id": "p1", "name": "x", "current_revision": 1}],
                "workspaces": [
                    {"workspace_id": "w1", "name": "x", "policy": "ordinary", "active": True}
                ],
            }
        return 409, {"error": {"code": "profile_has_routines", "message": "refused"}}

    code, _out, err = _run(["bots", "create", "--profile", "p1"], send)
    assert code == 1
    assert (
        "fix: That profile already runs routines. Ask the owner to create this Bot in Agents.\n"
        in err
    )


def test_a_setting_too_long_to_review_gets_its_fix() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET":
            return 200, _settings_view({"identity.display_name": {"kind": "string"}})
        return 422, {"error": {"code": "too_long_to_review", "message": "refused"}}

    code, _out, err = _run(["settings", "set", "identity.display_name", "x"], send)
    assert code == 1
    assert "fix: Too long to review here; change it in Settings.\n" in err


def test_other_commands_conflict_fix_is_unchanged() -> None:
    """R7b's override is scoped to `bots create`; `routines test`'s own 409 conflict keeps the
    generic fix from _FIX_BY_CODE."""

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path.endswith("/routines"):
            return 200, _routines_list()
        return 409, {"error": {"code": "conflict", "message": "stale"}}

    code, _out, err = _run(["routines", "test", "r1"], send)
    assert code == 1
    assert "fix: Run the command again.\n" in err


# --- bots usage / trace: the op path and default days -----------------------------------------


def test_bots_usage_defaults_to_30_days() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        sent["method"], sent["path"] = method, path
        return 200, {"days": 30}

    code, out, _err = _run(["bots", "usage", "--json"], send)
    assert code == 0
    assert sent["method"] == "GET"
    assert sent["path"].endswith("/usage/30")
    assert json.loads(out) == {"days": 30}


def test_bots_usage_days_sends_the_chosen_value() -> None:
    sent: dict[str, Any] = {}

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        sent["path"] = path
        return 200, {"days": 7}

    code, _out, _err = _run(["bots", "usage", "--days", "7", "--json"], send)
    assert code == 0
    assert sent["path"].endswith("/usage/7")


def test_bots_usage_rejects_a_day_count_outside_the_choices() -> None:
    calls: list[Any] = []
    code, _out, _err = _run(["bots", "usage", "--days", "10"], _unused_send(calls))
    assert code == 2
    assert calls == []


def test_bots_usage_without_the_usage_feature_exits_1() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 503, {"error": {"code": "unavailable", "message": "Bot usage isn't available."}}

    code, _out, err = _run(["bots", "usage"], send)
    assert code == 1
    assert "fix: Try again, or use the VIKI Agent window.\n" in err


def test_bots_trace_sends_the_session_id_in_the_op_path() -> None:
    sent: dict[str, Any] = {}
    session_id = "11111111-1111-4111-8111-111111111111"

    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        sent["method"], sent["path"] = method, path
        return 200, {"session_id": session_id}

    code, out, _err = _run(["bots", "trace", session_id, "--json"], send)
    assert code == 0
    assert sent["method"] == "GET"
    assert sent["path"].endswith(f"/usage/sessions/{session_id}")
    assert json.loads(out) == {"session_id": session_id}


def test_bots_trace_not_found_exits_1() -> None:
    def send(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        return 404, {"error": {"code": "not_found", "message": "not found"}}

    code, _out, err = _run(["bots", "trace", "11111111-1111-4111-8111-111111111111"], send)
    assert code == 1
    assert "fix: Try again, or use the VIKI Agent window.\n" in err
