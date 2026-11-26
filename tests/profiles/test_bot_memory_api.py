"""Bot chat and task preparation carry the same owner through the session actor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from tests.profiles.test_profile_document import document
from tests.profiles.test_profile_launch_api import (
    BASE,
    HANG_GUARD_S,
    WRITE,
    Rig,
    ident,
    send_prepared,
    terminal_signal,
)
from viki_agent.messaging.bot_memory import instruction_excludes
from viki_agent.messaging.bot_memory_rule import bot_system_prompt


def send_typed(client: TestClient, preview: dict[str, Any], command_id: str) -> None:
    """Saves and sends a request the user typed: a Bot chat's draft starts empty, as its
    instructions go to Claude Code as the system prompt (ADR-084)."""
    assert preview["draft"]["text"] == ""
    saved = client.put(
        f"/api/v2/drafts/{preview['session_id']}",
        headers=WRITE,
        json={"expected_revision": preview["draft"]["revision"], "text": "Synthetic request"},
    )
    assert saved.status_code == 200, saved.text
    response = client.post(
        "/api/v2/commands",
        headers=WRITE,
        json={
            "command_id": command_id,
            "session_id": preview["session_id"],
            "generation": preview["generation"],
            "draft_revision": saved.json()["revision"],
        },
    )
    assert response.status_code == 202, response.text


def bot_rig(tmp_path: Path, policy: str) -> tuple[Rig, str, str]:
    rig = Rig(tmp_path)
    owner, main = ident(), ident()
    with rig.state.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE workspaces SET policy=? WHERE workspace_id=?", (policy, rig.workspace_id)
        )
        connection.execute(
            "INSERT INTO bots VALUES (?,?,?,?,?,1,?,?)",
            (
                owner,
                rig.profile_id,
                rig.workspace_id,
                main,
                "a" * 64,
                "2026-11-05T00:00:00.000Z",
                "2026-11-05T00:00:00.000Z",
            ),
        )
    return rig, owner, main


def test_protected_bot_spawn_requests_app_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, owner, _main = bot_rig(tmp_path, "protected")
    before = sorted(rig.vault.rglob("*"))
    target = rig.data / "bot-memory" / rig.workspace_id / owner
    with TestClient(rig.app(rig.manager()), base_url=BASE) as client:
        main = client.post(
            f"/api/v2/bots/{owner}/chat", headers=WRITE, json={"expected_state_revision": 1}
        )
        assert main.status_code == 200
        task = client.post(
            f"/api/v2/profiles/{rig.profile_id}/start-preview",
            headers=WRITE,
            json={
                "command_id": ident(),
                "session_id": ident(),
                "expected_profile_revision": 1,
                "workspace_id": rig.workspace_id,
                "bot_id": owner,
            },
        )
        assert task.status_code == 200
        previews = [main.json()["preview"], task.json()]
        for preview in previews:
            assert preview["bot_memory"] == {"bot_id": owner, "path": str(target), "routine": False}
            command = ident()
            with terminal_signal(monkeypatch, command) as done:
                send_typed(client, preview, command)
                assert done.wait(HANG_GUARD_S)
    assert len(rig.drivers) == 2 and all(driver.sent.is_set() for driver in rig.drivers)
    prompt = bot_system_prompt(document()["instructions"])
    assert [driver.launch.append_system_prompt for driver in rig.drivers] == [prompt, prompt]
    configs = {driver.launch.bot_memory_settings for driver in rig.drivers}
    assert len(configs) == 1
    (config,) = configs
    assert config is not None and Path(config).parent == rig.data / "bot-memory-settings"
    # A protected vault keeps the memory in the data folder, and its instruction files skipped.
    assert Path(config).read_bytes() == json.dumps(
        {
            "autoMemoryDirectory": str(target),
            "autoMemoryEnabled": False,
            "claudeMdExcludes": instruction_excludes(rig.vault),
        },
        separators=(",", ":"),
    ).encode("ascii")
    assert not (rig.data / "bot-memory").exists() and not (rig.vault / "bot-memory").exists()
    assert sorted(rig.vault.rglob("*")) == before


def test_main_and_task_spawns_share_only_their_bots_native_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    owner, main, task, regular = ident(), ident(), ident(), ident()
    with rig.state.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE workspaces SET policy='ordinary' WHERE workspace_id=?", (rig.workspace_id,)
        )
        connection.execute(
            "INSERT INTO bots VALUES (?,?,?,?,?,1,?,?)",
            (
                owner,
                rig.profile_id,
                rig.workspace_id,
                main,
                "a" * 64,
                "2026-11-05T00:00:00.000Z",
                "2026-11-05T00:00:00.000Z",
            ),
        )
    with TestClient(rig.app(rig.manager()), base_url=BASE) as client:
        canonical = client.post(
            f"/api/v2/bots/{owner}/chat",
            headers=WRITE,
            json={"expected_state_revision": 1},
        )
        assert canonical.status_code == 200
        main_preview = canonical.json()["preview"]
        response = client.post(
            f"/api/v2/profiles/{rig.profile_id}/start-preview",
            headers=WRITE,
            json={
                "command_id": ident(),
                "session_id": task,
                "expected_profile_revision": 1,
                "workspace_id": rig.workspace_id,
                "bot_id": owner,
            },
        )
        assert response.status_code == 200
        task_preview = response.json()
        regular_preview = rig.preview(client, regular, ident())
        assert (
            main_preview["bot_memory"]
            == task_preview["bot_memory"]
            == {
                "bot_id": owner,
                "ref": f"bot-memory/{owner}",
                "routine": False,
            }
        )
        assert "bot_memory" not in regular_preview
        assert not list((rig.data / "bot-memory-settings").glob("*.json"))
        for preview, send in (
            (main_preview, send_typed),
            (task_preview, send_typed),
            # A profile chat without a Bot still sends its prepared instructions draft.
            (
                regular_preview,
                lambda client, preview, command: send_prepared(
                    client, preview["session_id"], preview["generation"], command
                ),
            ),
        ):
            command = ident()
            with terminal_signal(monkeypatch, command) as done:
                send(client, preview, command)
                assert done.wait(HANG_GUARD_S)
    assert len(rig.drivers) == 3 and all(driver.sent.is_set() for driver in rig.drivers)
    configs = [driver.launch.bot_memory_settings for driver in rig.drivers]
    assert configs[0] is not None and configs[0] == configs[1] and configs[2] is None
    prompt = bot_system_prompt(document()["instructions"])
    prompts = [driver.launch.append_system_prompt for driver in rig.drivers]
    assert prompts == [prompt, prompt, None]
    assert json.loads(Path(configs[0]).read_text(encoding="ascii")) == {
        "autoMemoryDirectory": str(rig.vault / "bot-memory" / owner),
        "autoMemoryEnabled": False,
        "claudeMdExcludes": instruction_excludes(rig.vault),
    }
    assert not (rig.vault / "bot-memory").exists()
