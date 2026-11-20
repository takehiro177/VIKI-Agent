"""The agent surface's routine operations over a real `RoutineService` (the rig of
`test_agent_api`): add, list, test through the stub runner, and enable, which waits for the
window's Apply."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.app.test_agent_api import NOW, SESSION, Rig, code, ident
from tests.automation.test_routines import _document_raw, _manual_schedule
from viki_agent.automation.routines import record_tested, stamp


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
    routine_id = rig.routines.create(ident(), _document(rig))[0]["routine_id"]
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
        ("routines.test", routine_id)
    ] * 2


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
            "prompt_truncated": False,
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


def test_the_enable_summary_shows_at_most_2000_characters_of_the_prompt(rig: Rig) -> None:
    shown = "é" * 2000  # two UTF-8 bytes each: the cut counts characters, not bytes
    exact = _tested(rig, _document(rig, prompt=shown))
    longer = _tested(rig, _document(rig, prompt=shown + "x"))
    assert _enable(rig, exact).status_code == _enable(rig, longer).status_code == 202
    summaries = {row["summary"]["routine_id"]: row["summary"] for row in rig.held()}
    assert (summaries[exact]["prompt"], summaries[exact]["prompt_truncated"]) == (shown, False)
    assert (summaries[longer]["prompt"], summaries[longer]["prompt_truncated"]) == (shown, True)


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
