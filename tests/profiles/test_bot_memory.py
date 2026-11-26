"""Bot ownership persists independently of chat IDs and profile labels."""

from __future__ import annotations

import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tests.profiles.test_profile_document import document
from tests.profiles.test_profile_launch import ident
from tests.profiles.test_profile_launch import setup as setup
from viki_agent import winproc
from viki_agent.conversations.drafts import DraftService
from viki_agent.drivers.claude.control import Launch, build_argv
from viki_agent.messaging.bot_memory import (
    SETTINGS_FOLDER,
    app_memory_area,
    app_memory_directory,
    instruction_excludes,
    memory_ref,
    settings_bytes,
    write_settings,
)
from viki_agent.messaging.bot_memory_rule import bot_system_prompt
from viki_agent.profiles.profile_launch import ProfileLaunchService
from viki_agent.profiles.service import ProfileError


def bot(service: ProfileLaunchService, profile_id: str, workspace_id: str) -> str:
    bot_id = ident()
    with service.store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO bots VALUES (?,?,?,?,?,1,?,?)",
            (
                bot_id,
                profile_id,
                workspace_id,
                ident(),
                "a" * 64,
                "2026-11-05T00:00:00.000Z",
                "2026-11-05T00:00:00.000Z",
            ),
        )
    return bot_id


@pytest.fixture
def rig(setup: Any, tmp_path: Path):
    service, state, vault, pid, wid = setup
    with state.store._connection(write=True) as connection:
        connection.execute("UPDATE workspaces SET policy='ordinary' WHERE workspace_id=?", (wid,))
    service.bind_bot_settings_directory(tmp_path / "data" / SETTINGS_FOLDER)
    return service, state, vault, pid, wid


def test_bot_tasks_share_memory_after_rename_and_service_restart(rig: Any) -> None:
    service, state, vault, pid, wid = rig
    owner = bot(service, pid, wid)
    sessions = [ident() for _ in range(3)]
    for sid in sessions:
        preview = service.prepare(pid, ident(), sid, 1, wid, owner)
        assert preview["bot_memory"] == {
            "bot_id": owner,
            "ref": memory_ref(owner),
            "routine": False,
        }
    paths = [service.bot_memory_settings_path(sid) for sid in sessions]
    assert paths[0] and len(set(paths)) == 1
    path = Path(paths[0])
    assert json.loads(path.read_text(encoding="ascii")) == {
        "autoMemoryDirectory": str(vault / memory_ref(owner)),
        "autoMemoryEnabled": False,
        "claudeMdExcludes": instruction_excludes(vault),
    }
    assert not (vault / "bot-memory").exists()  # the app writes only the settings file
    renamed = document()
    renamed["identity"]["name"] = "New label"
    service.profiles.revise(pid, ident(), 1, renamed)
    restarted = ProfileLaunchService(
        service.profiles,
        service.settings,
        service.workspaces,
        service.catalogs,
        service.readiness,
        vault,
        lambda _: False,
        connections=service.connections,
        route_can_start=service.route_can_start,
    )
    restarted.bind_bot_settings_directory(path.parent)
    for sid in sessions:
        restarted.validate_launch(sid, state.prefs(sid))
        assert restarted.bot_memory_settings_path(sid) == str(path)


def test_concurrent_bots_have_distinct_settings(rig: Any) -> None:
    service, _state, vault, pid, wid = rig
    other = ident()
    service.profiles.create(other, ident(), document())
    owners = [(pid, bot(service, pid, wid)), (other, bot(service, other, wid))]
    sessions = []
    # The app serializes preparation under admission; the later spawns can overlap.
    for profile_id, owner in owners:
        sid = ident()
        service.prepare(profile_id, ident(), sid, 1, wid, owner)
        sessions.append(sid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        paths = list(pool.map(service.bot_memory_settings_path, sessions))
    assert paths[0] and paths[1] and paths[0] != paths[1]
    for path, (_pid, owner) in zip(paths, owners, strict=True):
        assert path and Path(path).read_bytes() == settings_bytes(vault, owner)
        argv = build_argv(
            ["claude"], Launch(ident(), bot_memory_settings=path), mode="plan", effort="high"
        )
        assert [arg for arg in argv if arg.startswith("--settings")] == [f"--settings={path}"]


def test_bot_bound_session_gets_its_pinned_instructions_as_system_prompt(rig: Any) -> None:
    service, state, vault, pid, wid = rig
    owner, cid, sid = bot(service, pid, wid), ident(), ident()
    preview = service.prepare(pid, cid, sid, 1, wid, owner)
    draft = DraftService(state, vault).get(sid)
    assert (draft.text, draft.revision) == ("", 0)
    assert preview["draft"]["text"] == ""
    assert service.bot_system_prompt(sid) == bot_system_prompt(document()["instructions"])
    assert service.prepare(pid, cid, sid, 1, wid, owner) == preview
    service.validate_launch(sid, state.prefs(sid))
    # A later profile revision changes new chats only.
    service.profiles.revise(pid, ident(), 1, {**document(), "instructions": "Later text."})
    assert service.bot_system_prompt(sid) == bot_system_prompt(document()["instructions"])


def test_bot_bound_system_prompt_without_instructions_is_the_rules_alone(rig: Any) -> None:
    service, state, vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    service.profiles.revise(pid, ident(), 1, {**document(), "instructions": ""})
    service.prepare(pid, ident(), sid, 2, wid, owner)
    assert DraftService(state, vault).get(sid).text == ""
    assert service.bot_system_prompt(sid) == bot_system_prompt("")


def test_bot_system_prompt_gains_the_kanban_rule_once_the_vault_has_the_skill(rig: Any) -> None:
    service, _state, vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    service.prepare(pid, ident(), sid, 1, wid, owner)
    assert service.bot_system_prompt(sid) == bot_system_prompt(document()["instructions"])
    skill = vault / ".claude" / "skills" / "kanban" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("content", encoding="utf-8")
    assert service.bot_system_prompt(sid) == bot_system_prompt(
        document()["instructions"], kanban=True
    )


def test_bot_system_prompt_ends_with_what_the_bot_recalls(rig: Any) -> None:
    # ADR-087: recall follows the rules, asked again at every spawn with the session's ids.
    service, _state, _vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    service.prepare(pid, ident(), sid, 1, wid, owner)
    asked: list[tuple[str, str, str]] = []

    def recall(workspace_id: str, bot_id: str, session_id: str) -> str:
        asked.append((workspace_id, bot_id, session_id))
        return "<facts>\n- x\n</facts>" if len(asked) == 1 else ""

    service.bind_recall(recall)
    rules = bot_system_prompt(document()["instructions"])
    assert service.bot_system_prompt(sid) == f"{rules}\n\n<facts>\n- x\n</facts>"
    assert service.bot_system_prompt(sid) == rules
    assert asked == [(wid, owner, sid)] * 2


def test_a_profile_session_without_a_bot_keeps_its_instructions_in_the_draft(rig: Any) -> None:
    service, state, vault, pid, wid = rig
    sid = ident()
    service.prepare(pid, ident(), sid, 1, wid)
    assert DraftService(state, vault).get(sid).text == document()["instructions"]
    assert service.bot_system_prompt(sid) is None


def test_regular_profiles_and_existing_bindings_keep_inherited_memory(rig: Any) -> None:
    service, state, vault, pid, wid = rig
    sid = ident()
    service.prepare(pid, ident(), sid, 1, wid)
    bot(service, pid, wid)
    assert DraftService(state, vault).get(sid).text == document()["instructions"]
    assert service.bot_memory_settings_path(sid) is None
    assert "bot_memory" not in service.inspect_session(sid)


def test_the_binding_says_whether_the_chat_is_a_routine_run(rig: Any) -> None:
    # A client past the Bot's thread listing tells a task thread from a routine run by this
    # alone: both carry the same memory binding.
    service, _state, _vault, pid, wid = rig
    owner = bot(service, pid, wid)
    thread, run = ident(), ident()
    service.prepare(pid, ident(), thread, 1, wid, owner, launch_origin="interactive")
    service.prepare(pid, ident(), run, 1, wid, owner, launch_origin="routine")
    assert service.inspect_session(thread)["bot_memory"]["routine"] is False
    assert service.inspect_session(run)["bot_memory"]["routine"] is True


def test_a_routine_run_skips_the_vault_instructions_like_every_bot_session(rig: Any) -> None:
    # Whatever gets the Bot rules in its system prompt also gets the settings naming the vault's
    # instruction files: a routine run of the Bot's profile as much as its main chat.
    service, _state, vault, pid, wid = rig
    owner, chat, run = bot(service, pid, wid), ident(), ident()
    service.prepare(pid, ident(), chat, 1, wid, owner, launch_origin="interactive")
    service.prepare(pid, ident(), run, 1, wid, owner, launch_origin="routine")
    for sid in (chat, run):
        path = service.bot_memory_settings_path(sid)
        assert path
        assert json.loads(Path(path).read_text(encoding="ascii"))[
            "claudeMdExcludes"
        ] == instruction_excludes(vault)
        assert service.bot_system_prompt(sid) == bot_system_prompt(document()["instructions"])


def test_removing_a_bot_revokes_its_binding_instead_of_retargeting_it(rig: Any) -> None:
    service, state, _vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    service.prepare(pid, ident(), sid, 1, wid, owner)
    with service.store._connection(write=True) as connection:
        connection.execute("DELETE FROM bots WHERE bot_id=?", (owner,))
    assert bot(service, pid, wid) != owner
    with pytest.raises(ProfileError, match="launch_changed"):
        service.bot_memory_settings_path(sid)
    with pytest.raises(ProfileError, match="launch_changed"):
        service.validate_launch(sid, state.prefs(sid))


def test_owner_must_match_the_profile_and_workspace(rig: Any) -> None:
    service, _state, _vault, pid, wid = rig
    owner = bot(service, pid, wid)
    other = ident()
    service.profiles.create(other, ident(), document())
    with pytest.raises(ProfileError, match="conflict"):
        service.prepare(other, ident(), ident(), 1, wid, owner)


@pytest.mark.parametrize("change", ["revision=revision+1", "policy='protected'"])
def test_workspace_changes_refuse_before_writing_a_settings_file(rig: Any, change: str) -> None:
    service, state, _vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    service.prepare(pid, ident(), sid, 1, wid, owner)
    with state.store._connection(write=True) as connection:
        connection.execute(f"UPDATE workspaces SET {change} WHERE workspace_id=?", (wid,))
    with pytest.raises(ProfileError, match="launch_changed"):
        service.bot_memory_settings_path(sid)
    assert not list(service.bot_settings_directory.glob("*.json"))


def files(root: Path) -> list[Path]:
    return sorted(root.rglob("*"))


def set_policy(state: Any, wid: str, policy: str) -> None:
    with state.store._connection(write=True) as connection:
        connection.execute("UPDATE workspaces SET policy=? WHERE workspace_id=?", (policy, wid))


@pytest.mark.parametrize("policy", ["protected", "existing_readonly"])
def test_nonordinary_bot_sessions_keep_memory_in_app_data(rig: Any, policy: str) -> None:
    service, state, vault, pid, wid = rig
    owner = bot(service, pid, wid)
    set_policy(state, wid, policy)
    before = files(vault)
    data = service.bot_settings_directory.parent
    target = data / "bot-memory" / wid / owner
    sid = ident()
    expected = {"bot_id": owner, "path": str(target), "routine": False}
    assert service.prepare(pid, ident(), sid, 1, wid, owner)["bot_memory"] == expected
    assert DraftService(state, vault).get(sid).text == ""
    assert service.bot_system_prompt(sid) == bot_system_prompt(document()["instructions"])
    assert service.inspect_session(sid)["bot_memory"] == expected
    path = service.bot_memory_settings_path(sid)
    assert path and Path(path).parent == service.bot_settings_directory
    # The session still runs in the vault, so its instruction files are named all the same.
    assert Path(path).read_bytes() == json.dumps(
        {
            "autoMemoryDirectory": str(target),
            "autoMemoryEnabled": False,
            "claudeMdExcludes": instruction_excludes(vault),
        },
        separators=(",", ":"),
    ).encode("ascii")
    argv = build_argv(
        ["claude"], Launch(sid, bot_memory_settings=path), mode="default", effort="high"
    )
    assert [arg for arg in argv if arg.startswith("--settings")] == [f"--settings={path}"]
    # Claude Code alone creates the memory folder: the app made none and left the vault alone.
    assert not (data / "bot-memory").exists() and not (vault / "bot-memory").exists()
    assert files(vault) == before


def test_existing_nonordinary_binding_uses_app_memory_on_next_spawn(rig: Any) -> None:
    service, state, vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    set_policy(state, wid, "protected")
    service.prepare(pid, ident(), sid, 1, wid, owner)
    service.native_initialized(sid)  # a chat that already started, now resumed
    service.validate_launch(sid, state.prefs(sid))
    path = service.bot_memory_settings_path(sid)
    assert path and json.loads(Path(path).read_text(encoding="ascii")) == {
        "autoMemoryDirectory": str(
            service.bot_settings_directory.parent / "bot-memory" / wid / owner
        ),
        "autoMemoryEnabled": False,
        "claudeMdExcludes": instruction_excludes(vault),
    }


def make_link(path: Path, target: Path, kind: str) -> None:
    if kind == "file":
        path.write_text("not a directory", encoding="utf-8")
    elif kind == "junction":
        if sys.platform != "win32":
            pytest.skip("junctions are Windows only")
        done = winproc.run(
            ["cmd", "/c", "mklink", "/J", str(path), str(target)],
            cwd=str(path.parent),
            timeout=10.0,
        )
        if done.returncode != 0:
            pytest.skip("this volume refuses junctions")
    else:
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("Creating directory symlinks requires host privileges")


@pytest.mark.parametrize("kind", ["symlink", "junction", "file"])
@pytest.mark.parametrize("level", ["parent", "area", "bot"])
def test_app_memory_links_are_refused(rig: Any, tmp_path: Path, level: str, kind: str) -> None:
    service, state, _vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    set_policy(state, wid, "protected")
    service.prepare(pid, ident(), sid, 1, wid, owner)
    outside = tmp_path / "outside"
    outside.mkdir()
    path = service.bot_settings_directory.parent / "bot-memory"
    for part in {"parent": (), "area": (wid,), "bot": (wid, owner)}[level]:
        path.mkdir()
        path = path / part
    make_link(path, outside, kind)
    with pytest.raises(ProfileError, match="bot_memory_unavailable"):
        service.bot_memory_settings_path(sid)
    assert not list(outside.iterdir())
    assert not list(service.bot_settings_directory.glob("*.json"))


@pytest.mark.parametrize(
    "workspace_id",
    # Fixed values: a random id in a parameter gives each pytest-xdist worker a different test id.
    [
        "0B5E1C7A-3F2D-4A6B-9C8E-1D2F3A4B5C6D",
        "../" * 12,
        "{0b5e1c7a-3f2d-4a6b-9c8e-1d2f3a4b5c6d}",
    ],
    ids=["uppercase", "dot-dot", "braced"],
)
def test_app_memory_refuses_a_noncanonical_workspace_id(tmp_path: Path, workspace_id: str) -> None:
    data, vault, owner = tmp_path / "data", tmp_path / "vault", str(uuid.uuid4())
    settings = data / SETTINGS_FOLDER
    for call in (
        lambda: write_settings(settings, vault, owner, workspace_id=workspace_id),
        lambda: settings_bytes(vault, owner, workspace_id=workspace_id, data=data),
        lambda: app_memory_area(data, workspace_id),
        lambda: app_memory_directory(data, workspace_id, owner),
    ):
        with pytest.raises(ValueError):
            call()
    with pytest.raises(ValueError):
        app_memory_directory(data, str(uuid.uuid4()), owner.upper())
    assert not data.exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"read_only_tools": True},
        {"add_dirs": ("/example",)},
        {"fork_of": "example"},
    ],
)
def test_memory_override_refuses_other_special_launches(tmp_path: Path, changes: Any) -> None:
    if "add_dirs" in changes:
        changes = {"add_dirs": (str(tmp_path),)}
    path = str(tmp_path / SETTINGS_FOLDER / ("a" * 64 + ".json"))
    with pytest.raises(ValueError, match="Bot memory"):
        build_argv(
            ["claude"],
            Launch(ident(), bot_memory_settings=path, **changes),
            mode="plan",
            effort="high",
        )


def test_non_directory_memory_parent_is_refused(rig: Any) -> None:
    service, _state, vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    service.prepare(pid, ident(), sid, 1, wid, owner)
    (vault / "bot-memory").write_text("not a directory", encoding="utf-8")
    with pytest.raises(ProfileError, match="bot_memory_unavailable"):
        service.bot_memory_settings_path(sid)


@pytest.mark.parametrize("level", ["parent", "bot"])
def test_memory_directory_symlinks_are_refused(rig: Any, tmp_path: Path, level: str) -> None:
    service, _state, vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    service.prepare(pid, ident(), sid, 1, wid, owner)
    outside = tmp_path / "outside"
    outside.mkdir()
    path = vault / "bot-memory"
    if level == "bot":
        path.mkdir()
        path = path / owner
    try:
        path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks requires host privileges")
    with pytest.raises(ProfileError, match="bot_memory_unavailable"):
        service.bot_memory_settings_path(sid)
    assert not list(outside.iterdir())


def test_changed_settings_bytes_refuse_the_next_spawn(rig: Any) -> None:
    service, _state, _vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    assert service.bot_for(pid, wid) == owner
    assert service.bot_for(ident(), wid) is None
    service.prepare(pid, ident(), sid, 1, wid, owner)
    path = service.bot_memory_settings_path(sid)
    assert path
    Path(path).write_text('{"permissions":{}}', encoding="ascii")
    with pytest.raises(ProfileError, match="bot_memory_unavailable"):
        service.bot_memory_settings_path(sid)


def test_a_missing_new_binding_cannot_fall_back_to_shared_project_memory(rig: Any) -> None:
    service, state, _vault, pid, wid = rig
    owner, sid = bot(service, pid, wid), ident()
    service.prepare(pid, ident(), sid, 1, wid, owner)
    with state.store._connection(write=True) as connection:
        connection.execute("DELETE FROM session_bot_memory WHERE session_id=?", (sid,))
    with pytest.raises(ProfileError, match="corrupt"):
        service.bot_memory_settings_path(sid)
