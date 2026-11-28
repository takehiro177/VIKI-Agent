"""The Kanban watch: `VaultService.folder_stamp` over the active vault's `kanban/` folder, and the
server's poll that sends `kanban.changed` to every socket when that stamp changes."""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import inspect
import io
import os
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tests.profiles.test_profile_launch_api import (
    BASE,
    HANG_GUARD_S,
    TOKEN,
    Rig,
    authed,
    ident,
    recv,
)
from tests.vaults.test_vault import service
from viki_agent import events, server
from viki_agent.automation.kanban import KANBAN_FOLDER
from viki_agent.server import create_app
from viki_agent.vaults import vault as vault_module
from viki_agent.vaults.vault import VaultError, VaultService
from viki_agent.winproc import run

# Two stamps of the same one-card folder, the card modified in between.
ONE = (("a.md", 1, 0o100644, 1, 3, 1_000, 0),)
TWO = (("a.md", 1, 0o100644, 1, 3, 2_000, 0),)


def stamp(root: Path) -> tuple[tuple[str, int, int, int, int, int, int], ...]:
    return service(root).folder_stamp("w", KANBAN_FOLDER)


def put(card: Path, text: str) -> None:
    """Write a card whole under a name the stamp skips, then rename it into place: a pass sees the
    old card or the new one, so each write is one change."""
    staged = card.with_suffix(".tmp")
    staged.write_text(text, encoding="utf-8")
    os.replace(staged, card)


def test_folder_stamp_changes_when_a_card_is_added_edited_renamed_or_removed(
    tmp_path: Path,
) -> None:
    folder = tmp_path / KANBAN_FOLDER
    folder.mkdir()
    card = folder / "a.md"
    card.write_text("one", encoding="utf-8")
    info = card.lstat()
    row = ("a.md", info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns)
    seen = [stamp(tmp_path)]
    assert seen == [((*row, getattr(info, "st_file_attributes", 0)),)]
    assert stamp(tmp_path) == seen[0]  # nothing changed, nothing differs
    (folder / "b.md").write_text("two", encoding="utf-8")
    seen.append(stamp(tmp_path))
    assert [name for name, *_ in seen[-1]] == ["a.md", "b.md"]
    os.utime(card, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))  # edited in place
    seen.append(stamp(tmp_path))
    (folder / "b.md").rename(folder / "c.md")
    seen.append(stamp(tmp_path))
    (folder / "c.md").unlink()
    seen.append(stamp(tmp_path))
    assert len(set(seen)) == len(seen) == 5


def test_folder_stamp_counts_only_notes_directly_in_it(tmp_path: Path) -> None:
    folder = tmp_path / KANBAN_FOLDER
    folder.mkdir()
    (folder / "a.md").write_text("card", encoding="utf-8")
    (folder / "b.Markdown").write_text("card", encoding="utf-8")
    before = stamp(tmp_path)
    assert [name for name, *_ in before] == ["a.md", "b.Markdown"]
    (folder / "sub").mkdir()
    (folder / "sub" / "c.md").write_text("deeper", encoding="utf-8")
    (folder / "x.tmp").write_text("not a note", encoding="utf-8")
    assert stamp(tmp_path) == before
    for ref in ("kanban/sub", "", "../kanban"):
        with pytest.raises(VaultError) as refused:
            service(tmp_path).folder_stamp("w", ref)
        assert refused.value.code == "invalid_ref", ref


def test_folder_stamp_reads_no_note_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / KANBAN_FOLDER
    folder.mkdir()
    (folder / "a.md").write_text("secret card text", encoding="utf-8")
    vault = service(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the stamp read a file")

    with monkeypatch.context() as patched:
        for owner, name in ((os, "read"), (builtins, "open"), (io, "open")):
            patched.setattr(owner, name, refuse)
        patched.setattr(vault_module, "_ReadFile", refuse, raising=False)  # Windows only
        rows = vault.folder_stamp("w", KANBAN_FOLDER)
    assert [name for name, *_ in rows] == ["a.md"]


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param(
            "junction",
            marks=pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows only"),
        ),
        "symlink",
    ],
)
def test_folder_stamp_refuses_a_linked_folder(tmp_path: Path, kind: str) -> None:
    vault_root, outside = tmp_path / "vault", tmp_path / "outside"
    vault_root.mkdir()
    outside.mkdir()
    (outside / "a.md").write_text("outside", encoding="utf-8")
    link = vault_root / KANBAN_FOLDER
    if kind == "junction":
        argv = ["cmd", "/c", "mklink", "/J", str(link), str(outside)]
        done = run(argv, cwd=tmp_path, timeout=10.0)
        assert done.returncode == 0, done.stderr
    else:
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError, NotImplementedError:
            pytest.skip("symlinks unavailable")
    with pytest.raises(VaultError) as refused:
        stamp(vault_root)
    assert refused.value.code == "unsafe_path"


async def watch(
    folder_stamp: Callable[[str, str], object], passes: int, workspace_id: str = "w"
) -> list[str]:
    """The workspace ids `_watch_kanban_loop` sent kanban.changed for over `passes` passes of
    `folder_stamp`, with no wait between passes."""
    loop = asyncio.get_running_loop()
    asked: list[tuple[str, str]] = []
    changed: list[str] = []

    def counted(watched: str, folder: str) -> object:
        asked.append((watched, folder))
        if len(asked) > passes:
            loop.call_soon_threadsafe(task.cancel)  # this pass is cancelled before it compares
            return None
        return folder_stamp(watched, folder)

    hub = cast(server._VaultHub, SimpleNamespace(kanban_changed=changed.append))
    reader = cast(VaultService, SimpleNamespace(folder_stamp=counted))
    task = asyncio.create_task(server._watch_kanban_loop(hub, reader, workspace_id, 0))
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert set(asked) == {(workspace_id, KANBAN_FOLDER)}
    return changed


def scripted(*steps: object) -> Callable[[str, str], object]:
    """A `folder_stamp` that answers each pass with its next step, raising it if it is one."""
    queue = list(steps)

    def folder_stamp(_workspace_id: str, _folder: str) -> object:
        step = queue.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    return folder_stamp


async def test_the_watch_broadcasts_each_change_once() -> None:
    missing = VaultError("unsafe_path")  # a missing or linked folder: no stamp
    assert await watch(scripted(ONE, ONE), 2) == []  # the first pass is only the baseline
    steps = (ONE, ONE, TWO, TWO, missing, missing, ONE)
    assert await watch(scripted(*steps), len(steps)) == ["w"] * 3  # changed, went, came back
    assert await watch(scripted(missing, TWO), 2) == ["w"]  # a folder appearing is one change


async def test_watch_logs_once_per_failure_streak(caplog: pytest.LogCaptureFixture) -> None:
    boom = RuntimeError("synthetic")  # anything but a VaultError
    # Three failures in a row log once. The good pass after them compares with the stamp from
    # before them (unchanged, so nothing is sent) and ends the streak: the next failure logs again.
    steps = (ONE, boom, boom, boom, ONE, boom, TWO)
    assert await watch(scripted(*steps), len(steps)) == ["w"]
    logged = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == server.logger.name]
    assert logged == [("WARNING", "kanban watch failed")] * 2


async def test_a_missing_folder_stays_quiet(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = Rig(tmp_path)  # a registered vault with no kanban/ folder
    reader = VaultService(rig.workspaces)
    for workspace_id, code in ((rig.workspace_id, "unsafe_path"), (ident(), "unknown_workspace")):
        with pytest.raises(VaultError) as refused:
            reader.folder_stamp(workspace_id, KANBAN_FOLDER)
        assert refused.value.code == code
        assert await watch(reader.folder_stamp, 3, workspace_id) == []
    assert caplog.records == []


def test_kanban_envelope() -> None:
    hub = server._VaultHub(live=lambda: [])
    stays: list[dict[str, object]] = []
    leaves: list[dict[str, object]] = []
    hub.subscribe(stays.append)
    leave = hub.subscribe(leaves.append)
    hub.kanban_changed("w1")
    leave()
    hub.kanban_changed("w1")
    first = {
        "v": 2,
        "type": "kanban.changed",
        "session_id": "",
        "generation": hub.generation,
        "request_id": "",
        "seq": 1,
        "data": {"workspace_id": "w1"},
    }
    assert leaves == [first]
    assert stays == [first, {**first, "seq": 2}]
    assert hub.snapshot()["seq"] == 0  # vault.sessions keeps its own numbering
    assert events.EVENTS["kanban.changed"] is events.KanbanChanged


def kanban_app(rig: Rig) -> Starlette:
    """The rig's app with the Kanban watch polling every 50 ms."""
    return create_app(
        config=rig.config,
        token=TOKEN,
        manager=rig.manager(),
        catalog=rig.catalog,
        static_dir=None,
        settings=rig.settings,
        workspaces=rig.workspaces,
        readiness=rig.readiness,
        vault_poll_s=60,
        kanban_poll_s=0.05,
    )


def test_a_card_written_on_disk_reaches_the_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    folder = rig.vault / KANBAN_FOLDER
    folder.mkdir()
    baseline = threading.Event()
    original = VaultService.folder_stamp

    def observed(self: VaultService, workspace_id: str, name: str):
        # Only a pass the watch counts (a stamp, or a refusal it reads as no stamp) is its
        # baseline; after any other failure the next good pass is, so the card waits for that one.
        try:
            stamp = original(self, workspace_id, name)
        except VaultError:
            baseline.set()
            raise
        baseline.set()
        return stamp

    monkeypatch.setattr(VaultService, "folder_stamp", observed)
    with TestClient(kanban_app(rig), base_url=BASE) as client, authed(client) as ws:
        assert baseline.wait(HANG_GUARD_S)  # the first pass saw the empty folder
        put(folder / "a.md", "---\nstatus: todo\n---\n")
        added = recv(ws, HANG_GUARD_S)
        put(folder / "a.md", "---\nstatus: review\n---\n")
        edited = recv(ws, HANG_GUARD_S)
    uuid.UUID(added["generation"])
    assert added == {
        "v": 2,
        "type": "kanban.changed",
        "session_id": "",
        "generation": added["generation"],
        "request_id": "",
        "seq": 1,
        "data": {"workspace_id": rig.workspace_id},
    }
    assert edited == {**added, "seq": 2}


@pytest.mark.parametrize("selected", [True, False], ids=["vault", "chat_only"])
async def test_the_app_stops_the_watch(tmp_path: Path, selected: bool) -> None:
    rig = Rig(tmp_path)
    if not selected:
        rig.workspaces.select(ident(), None)  # the built-in chat-only workspace is active
    app = kanban_app(rig)
    async with app.router.lifespan_context(app):
        watches = [
            task
            for task in asyncio.all_tasks()
            if getattr(task.get_coro(), "__qualname__", "") == "_watch_kanban_loop"
        ]
        assert len(watches) == int(selected)
    assert all(task.cancelled() for task in watches)
    # The period the app polls at: `__main__` never passes it, so the default is what runs.
    assert inspect.signature(create_app).parameters["kanban_poll_s"].default == 1.0
    assert server.KANBAN_POLL_S == 1.0
