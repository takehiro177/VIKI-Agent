"""The agent surface's routine operations over a real `RoutineService` (the rig of
`test_agent_api`): add, list, test through the stub runner, and enable, which waits for the
window's Apply. A chat adds and tests routines only for a plain profile (no MCP servers, no Bot,
no pinned permission mode that skips asking), and tests only the routines its own session
added."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.app.test_agent_api import NOW, SESSION, Rig, code, ident
from tests.automation.test_routines import _document_raw, _manual_schedule
from tests.profiles.test_profile_document import document as profile_document
from tests.profiles.test_profiles_api import with_server
from viki_agent.app.agent_api import ASKING_MODES
from viki_agent.automation.routines import record_tested, stamp
from viki_agent.drivers.claude.control import PERMISSION_MODES

NOT_ADDED_HERE = "A chat can test only a routine it added itself."
NOT_PLAIN = (
    "A chat can't add or test a routine for a profile with MCP servers, a Bot, or a permission "
    "mode that skips asking (Accept edits or Auto)."
)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    rig = Rig(tmp_path)
    with rig.client:
        yield rig


def _document(rig: Rig, **overrides: Any) -> dict[str, Any]:
    return _document_raw(profile_id=rig.profile_id, workspace_id=rig.workspace_id, **overrides)


def _tested(rig: Rig, document: dict[str, Any]) -> str:
    """A routine whose first revision passed its manual run: state `tested`, state revision 2."""
    routine_id = rig.routines.create(ident(), document)[0]["routine_id"]
    with rig.state.store._connection(write=True) as connection:
        record_tested(connection, routine_id, 1, stamp(NOW))
    return routine_id


def _enable(rig: Rig, routine_id: str, state_revision: int = 2) -> Any:
    return rig.send(
        "POST",
        f"routines/{routine_id}/enable",
        {"command_id": ident(), "expected_state_revision": state_revision},
    )


def _add(rig: Rig, document: dict[str, Any]) -> Any:
    return rig.send("POST", "routines", {"command_id": ident(), "document": document})


def _added(rig: Rig, document: dict[str, Any]) -> str:
    """A draft routine this chat (SESSION) added through the agent surface."""
    added = _add(rig, document)
    assert added.status_code == 201
    return added.json()["routine"]["routine_id"]


def _run(rig: Rig, routine_id: str, revision: int = 1) -> Any:
    return rig.send(
        "POST",
        f"routines/{routine_id}/run",
        {"command_id": ident(), "expected_revision": revision},
    )


def _bot(rig: Rig, profile_id: str) -> None:
    """The owner binds a Bot to `profile_id` in the rig's vault, from the window."""
    assert rig.bots is not None
    rig.bots.create(ident(), profile_id, 1, rig.workspace_id)


def _refused_with(response: Any, status: int, code_: str, message: str) -> None:
    assert response.status_code == status
    assert response.json() == {"error": {"code": code_, "message": message}}


def test_add_makes_a_draft_and_a_journal_entry(rig: Rig) -> None:
    body = {"command_id": ident(), "document": _document(rig)}
    created = rig.send("POST", "routines", body)
    assert created.status_code == 201
    routine = created.json()["routine"]
    assert routine == {
        "routine_id": routine["routine_id"],
        "revision": 1,
        "state": "draft",
        "state_revision": 1,
    }
    assert rig.routines.get(routine["routine_id"])["state"] == "draft"
    replay = rig.send("POST", "routines", body)
    assert (replay.status_code, replay.json()) == (200, created.json())
    assert rig.scheduler.wakes == 1
    [entry] = rig.journal()
    assert (entry["session_id"], entry["operation"], entry["target"], entry["state"]) == (
        SESSION,
        "routines.add",
        routine["routine_id"],
        "applied",
    )
    invalid = rig.send("POST", "routines", {"command_id": ident(), "document": {"name": "x"}})
    assert invalid.status_code == 400
    assert len(rig.routines.list()) == 1


def test_routines_lists_each_with_its_schedule_last_run_and_running(rig: Rig) -> None:
    daily = rig.routines.create(ident(), _document(rig))[0]["routine_id"]
    manual = rig.routines.create(
        ident(), _document(rig, name="On demand", schedule=_manual_schedule())
    )[0]["routine_id"]
    last_run = {"occurrence_id": ident(), "state": "finished"}
    rig.runner.summaries = {
        daily: {"last_run": last_run, "last_attempt": None, "active_run": {"attempt_id": ident()}}
    }
    listed = rig.send("GET", "routines")
    assert listed.status_code == 200
    assert {item["routine_id"]: item for item in listed.json()["routines"]} == {
        daily: {
            "routine_id": daily,
            "name": "Daily brief",
            "state": "draft",
            "current_revision": 1,
            "state_revision": 1,
            "schedule": {"kind": "daily", "timezone": "Asia/Tokyo"},
            "last_run": last_run,
            "running": True,
        },
        manual: {
            "routine_id": manual,
            "name": "On demand",
            "state": "draft",
            "current_revision": 1,
            "state_revision": 1,
            "schedule": {"kind": "manual", "timezone": None},
            "last_run": None,
            "running": False,
        },
    }
    assert rig.journal() == []


def test_a_test_runs_the_current_revision_through_the_runner(rig: Rig) -> None:
    routine_id = _added(rig, _document(rig))
    rig.routines.revise(routine_id, ident(), 1, _document(rig, prompt="Second revision."))
    [listed] = rig.send("GET", "routines").json()["routines"]
    command_id = ident()
    response = rig.send(
        "POST",
        f"routines/{routine_id}/run",
        {"command_id": command_id, "expected_revision": listed["current_revision"]},
    )
    assert response.status_code == 202
    assert set(response.json()["run"]) == {"occurrence_id", "attempt_id", "session_id"}
    assert rig.runner.calls == [(routine_id, command_id, 2, True)]
    rig.scheduler.state = "stopped"
    rig.send("POST", f"routines/{routine_id}/run", {"command_id": ident(), "expected_revision": 2})
    assert rig.runner.calls[-1][3] is False
    assert [(entry["operation"], entry["target"]) for entry in rig.journal()] == [
        ("routines.test", routine_id),
        ("routines.test", routine_id),
        ("routines.add", routine_id),
    ]


def test_a_chat_tests_only_a_routine_its_own_session_added(rig: Rig) -> None:
    owner_s = rig.routines.create(ident(), _document(rig))[0]["routine_id"]
    _refused_with(_run(rig, owner_s), 403, "not_added_here", NOT_ADDED_HERE)
    other = rig.grants.issue(ident())
    assert other is not None
    another_chat_s = rig.client.post(
        f"/api/agent/{other.id}/routines",
        headers={"authorization": f"Bearer {other.token}"},
        json={"command_id": ident(), "document": _document(rig, name="Theirs")},
    ).json()["routine"]["routine_id"]
    _refused_with(_run(rig, another_chat_s), 403, "not_added_here", NOT_ADDED_HERE)
    assert rig.runner.calls == []
    assert [entry["operation"] for entry in rig.journal()] == []
    # This chat's own, from a resumed chat: a new key for the same session.
    mine = _added(rig, _document(rig, name="Mine"))
    rig.grant.revoke()
    resumed = rig.grants.issue(SESSION)
    assert resumed is not None
    rig.grant = resumed
    assert _run(rig, mine).status_code == 202
    assert [call[0] for call in rig.runner.calls] == [mine]


def test_a_test_is_refused_once_the_routine_s_profile_gets_a_bot_or_mcp_servers(rig: Rig) -> None:
    routine_id = _added(rig, _document(rig))
    _bot(rig, rig.profile_id)
    _refused_with(_run(rig, routine_id), 403, "profile_not_allowed", NOT_PLAIN)
    plain = ident()
    rig.profiles.create(plain, ident(), profile_document())
    other = _added(rig, _document_raw(profile_id=plain, workspace_id=rig.workspace_id))
    rig.profiles.revise(plain, ident(), 1, with_server())
    _refused_with(_run(rig, other), 403, "profile_not_allowed", NOT_PLAIN)
    assert rig.runner.calls == []
    assert {entry["operation"] for entry in rig.journal()} == {"routines.add"}


def test_adding_a_routine_for_a_profile_with_mcp_servers_or_a_bot_is_refused(rig: Rig) -> None:
    with_mcp = ident()
    rig.profiles.create(with_mcp, ident(), with_server())
    _refused_with(
        _add(rig, _document_raw(profile_id=with_mcp, workspace_id=rig.workspace_id)),
        403,
        "profile_not_allowed",
        NOT_PLAIN,
    )
    # A Bot in this vault refuses a routine for its profile in any workspace.
    _bot(rig, rig.profile_id)
    _refused_with(
        _add(rig, _document_raw(profile_id=rig.profile_id, workspace_id=ident())),
        403,
        "profile_not_allowed",
        NOT_PLAIN,
    )
    assert rig.routines.list() == []
    assert rig.journal() == []
    assert rig.scheduler.wakes == 0
    # A plain profile, or one that is not there, names and pins nothing.
    plain = ident()
    rig.profiles.create(plain, ident(), profile_document())
    for profile_id in (plain, ident()):
        _added(rig, _document_raw(profile_id=profile_id, workspace_id=ident()))
    assert len(rig.routines.list()) == 2


def _pinned(rig: Rig, mode: str | None) -> str:
    """A new profile whose `runtime.permission_mode` pins `mode` (None: inherits)."""
    profile_id, document = ident(), profile_document()
    document["runtime"]["permission_mode"] = mode
    rig.profiles.create(profile_id, ident(), document)
    return profile_id


def test_the_modes_that_skip_asking_are_accept_edits_and_auto() -> None:
    """What the refusal and the docs name: every pinnable mode outside `ASKING_MODES`."""
    assert {"acceptEdits", "auto"} == PERMISSION_MODES - ASKING_MODES


@pytest.mark.parametrize("mode", sorted(PERMISSION_MODES - ASKING_MODES))
def test_adding_a_routine_for_a_profile_pinned_to_a_mode_that_skips_asking_is_refused(
    rig: Rig, mode: str
) -> None:
    """A test is a manual run, which runs in the profile's pinned mode, whatever the chat's."""
    profile_id = _pinned(rig, mode)
    _refused_with(
        _add(rig, _document_raw(profile_id=profile_id, workspace_id=rig.workspace_id)),
        403,
        "profile_not_allowed",
        NOT_PLAIN,
    )
    assert rig.routines.list() == []
    assert rig.journal() == []


@pytest.mark.parametrize("mode", ["default", "manual", "plan", None])
def test_a_profile_that_asks_or_plans_is_added_and_tested_until_its_pin_skips_asking(
    rig: Rig, mode: str | None
) -> None:
    """`default` and `manual` ask before every tool, `plan` acts on nothing, and no pin follows
    the app's default chat mode, as a new chat does."""
    profile_id = _pinned(rig, mode)
    routine_id = _added(rig, _document_raw(profile_id=profile_id, workspace_id=rig.workspace_id))
    assert _run(rig, routine_id).status_code == 202
    widened = profile_document()
    widened["runtime"]["permission_mode"] = "acceptEdits"
    rig.profiles.revise(profile_id, ident(), 1, widened)
    _refused_with(_run(rig, routine_id), 403, "profile_not_allowed", NOT_PLAIN)
    assert [call[0] for call in rig.runner.calls] == [routine_id]


def test_enabling_a_draft_gets_409_and_holds_nothing(rig: Rig) -> None:
    routine_id = rig.routines.create(ident(), _document(rig))[0]["routine_id"]
    assert code(_enable(rig, routine_id, 1)) == (409, "invalid_transition")
    assert rig.routines.get(routine_id)["state"] == "draft"
    assert rig.held() == []
    assert rig.journal() == []


def test_enabling_a_tested_routine_waits_for_apply_then_wakes_the_scheduler(rig: Rig) -> None:
    routine_id = _tested(rig, _document(rig))
    held = _enable(rig, routine_id)
    assert held.status_code == 202
    pending = held.json()["pending"]
    assert pending == {"change_id": pending["change_id"], "expires_in_s": 600}
    assert rig.routines.get(routine_id)["state"] == "tested"
    assert rig.scheduler.wakes == 0
    [listed] = rig.held()
    assert (listed["session_id"], listed["kind"], listed["summary"]) == (
        SESSION,
        "routine_enable",
        {
            "routine_id": routine_id,
            "name": "Daily brief",
            "revision": 1,
            "schedule": {
                "kind": "daily",
                "times": ["09:00"],
                "days": [],
                "timezone": "Asia/Tokyo",
            },
            "profile": {"profile_id": rig.profile_id, "name": "Review helper"},
            "workspace": {"workspace_id": rig.workspace_id, "name": "synthetic"},
            "prompt": "Summarize overnight news.",
        },
    )
    applied = rig.decide(pending["change_id"])
    assert (applied.status_code, applied.json()) == (
        200,
        {
            "state": "applied",
            "result": {
                "routine_id": routine_id,
                "revision": 1,
                "state": "enabled",
                "state_revision": 3,
            },
        },
    )
    assert rig.routines.get(routine_id)["state"] == "enabled"
    assert rig.scheduler.wakes == 1
    [entry] = rig.journal()
    assert (entry["operation"], entry["target"], entry["state"]) == (
        "routines.enable",
        routine_id,
        "applied",
    )


def test_the_enable_summary_carries_the_whole_prompt(rig: Rig) -> None:
    # The longest prompt a routine takes (32,768 UTF-8 bytes), its end unlike its start.
    longest = "é" * 16381 + "‮end"
    assert len(longest.encode("utf-8")) == 32768
    routine_id = _tested(rig, _document(rig, prompt=longest))
    assert _enable(rig, routine_id).status_code == 202
    [listed] = rig.held()
    assert listed["summary"]["prompt"] == longest
    assert "prompt_truncated" not in listed["summary"]


def test_a_chat_never_reads_a_routine_s_prompt_through_its_hold(rig: Rig) -> None:
    secret = "owner-only-detail-7c1e"
    routine_id = _tested(rig, _document(rig, prompt=f"Summarize mail from {secret}."))
    listed, held, status, settings = (
        rig.send("GET", "routines"),
        _enable(rig, routine_id),
        rig.send("GET", "status"),
        rig.send("GET", "settings"),
    )
    assert held.status_code == 202
    assert not [reply for reply in (listed, held, status, settings) if secret in reply.text]
    assert status.json()["changes"]["pending"] == [
        {
            "change_id": held.json()["pending"]["change_id"],
            "kind": "routine_enable",
            "routine_id": routine_id,
            "name": "Daily brief",
            "expires_in_s": 600,
        }
    ]
    [card] = rig.held()  # the window's Apply card still shows it
    assert secret in card["summary"]["prompt"]


def test_enabling_a_routine_that_is_not_ready_gets_422_with_its_blockers(rig: Rig) -> None:
    routine_id = _tested(rig, _document_raw(profile_id=ident(), workspace_id=ident()))
    refused = _enable(rig, routine_id)
    assert code(refused) == (422, "not_ready")
    assert refused.json()["error"]["blockers"] == ["profile_missing", "workspace_missing"]
    assert rig.routines.get(routine_id)["state"] == "tested"
    assert rig.held() == []


def test_a_routine_changed_before_apply_refuses_the_apply(rig: Rig) -> None:
    routine_id = _tested(rig, _document(rig))
    pending = _enable(rig, routine_id).json()["pending"]
    rig.routines.revise(routine_id, ident(), 1, _document(rig, prompt="Changed."))
    refused = rig.decide(pending["change_id"])
    assert code(refused) == (409, "conflict")
    assert rig.routines.get(routine_id)["state"] == "draft"
    assert rig.scheduler.wakes == 0


def test_an_unknown_routine_gets_404_and_a_malformed_id_400(rig: Rig) -> None:
    assert code(_enable(rig, ident())) == (404, "not_found")
    assert code(_enable(rig, "not-a-routine-id")) == (400, "invalid_id")
    malformed = rig.send(
        "POST",
        f"routines/{_tested(rig, _document(rig))}/enable",
        {"command_id": ident(), "expected_state_revision": "2"},
    )
    assert code(malformed) == (400, "invalid_revision")
    run = {"command_id": ident(), "expected_revision": 1}
    assert code(rig.send("POST", "routines/not-a-routine-id/run", run)) == (400, "invalid_id")
    assert rig.runner.calls == []
    assert rig.held() == []
