"""Session roots: recording a session's folder and the spawn check, on a real store and service."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests.app.test_workspaces import COMMAND, service_at, store_at
from tests.workspace_code.test_workspaces_code import trusted
from viki_agent.catalog import RootLookup
from viki_agent.code.session_roots import (
    MSG_ROOT_MISSING,
    MSG_ROOT_UNTRUSTED,
    MSG_WORKTREE_UNAVAILABLE,
    RootError,
    SessionRoot,
    SessionRoots,
)
from viki_agent.drivers.base import StartError
from viki_agent.session import now_iso
from viki_agent.storage import SessionStore, StateError
from viki_agent.workspaces import WorkspaceService

SESSION = "33333333-3333-4333-8333-333333333333"
FORK = "44444444-4444-4444-8444-444444444444"
REVOKE = "55555555-5555-4555-8555-555555555555"
WORKTREE = "66666666-6666-4666-8666-666666666666"
OTHER_WORKTREE = "77777777-7777-4777-8777-777777777777"
BOT = "88888888-8888-4888-8888-888888888888"
THREAD = "99999999-9999-4999-8999-999999999999"


def bot_copy(
    store: SessionStore,
    workspace_id: str,
    worktree_id: str,
    *,
    state: str = "bound",
    thread: bool = False,
) -> None:
    """Synthetic ownership with no session root, including all shared-thread members."""
    stamp = now_iso()
    with store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO bot_code_copies VALUES (?,?,?,?,?,?,?)",
            (
                worktree_id,
                workspace_id,
                BOT,
                None if thread else SESSION,
                THREAD if thread else None,
                state,
                stamp,
            ),
        )
        if thread:
            connection.execute(
                "INSERT INTO bot_threads VALUES (?,?,1,?,1,?,?,?)",
                (THREAD, workspace_id, b"sealed", "a" * 64, stamp, stamp),
            )
            connection.executemany(
                "INSERT INTO bot_thread_sessions VALUES (?,?)", [(THREAD, SESSION), (THREAD, FORK)]
            )


@dataclass
class FakeActor:
    live: bool


@dataclass
class Env:
    tmp_path: Path
    store: SessionStore
    service: WorkspaceService
    actors: dict[str, FakeActor] = field(default_factory=dict)
    known: set[str] = field(default_factory=set)  # sessions the catalog already has

    @property
    def roots(self) -> SessionRoots:
        return SessionRoots(
            self.store, self.service, exists=self.known.__contains__, actor=self.actors.get
        )

    def code(self, name: str = "repo", *, trust: bool = True) -> str:
        root = self.tmp_path / name
        root.mkdir()
        if trust:
            return trusted(self.service, root)
        workspace, _ = self.service.register_existing(COMMAND, str(root), kind="code")
        return workspace.workspace_id

    def display(self, workspace_id: str) -> str:
        return self.service.get(workspace_id, kind="code").root.display

    def worktree(
        self,
        workspace_id: str,
        worktree_id: str = WORKTREE,
        *,
        state: str = "ready",
        folder: bool = True,
    ) -> Path:
        path = self.tmp_path / "worktrees" / worktree_id
        if folder:
            path.mkdir(parents=True)
        stamp = now_iso()
        self.store.insert_worktree(
            {
                "worktree_id": worktree_id,
                "workspace_id": workspace_id,
                "path": str(path),
                "branch": "viki/test",
                "base_oid": "a" * 40,
                "state": "creating",
                "created_at": stamp,
                "updated_at": stamp,
            },
            limit=32,
        )
        if state != "creating":
            self.store.update_worktree_state(worktree_id, "creating", state, stamp)
        return path

    def put(self, workspace_id: str, display_root: str, worktree_id: str | None = None) -> None:
        self.store.put_session_root(
            {
                "session_id": SESSION,
                "workspace_id": workspace_id,
                "worktree_id": worktree_id,
                "display_root": display_root,
                "created_at": now_iso(),
            }
        )


@pytest.fixture
def env(tmp_path: Path) -> Env:
    store = store_at(tmp_path)
    return Env(tmp_path, store, service_at(tmp_path, store))


async def refused(roots: SessionRoots, workspace_id: str, worktree_id: str | None = None) -> str:
    with pytest.raises(RootError) as failure:
        await roots.prepare(SESSION, workspace_id, worktree_id)
    return failure.value.code


async def start_refusal(roots: SessionRoots) -> str:
    with pytest.raises(StartError) as failure:
        await roots.resolve(SESSION)
    assert failure.value.code == "bad_request"
    return failure.value.message


# --- prepare ----------------------------------------------------------------------------------


async def test_prepare_records_a_trusted_folder_once(env: Env) -> None:
    workspace_id = env.code()
    roots = env.roots
    root = SessionRoot(SESSION, workspace_id, None)
    assert await roots.prepare(SESSION, workspace_id, None) == (root, True)
    assert await roots.prepare(SESSION, workspace_id, None) == (root, False)
    row = env.store.get_session_root(SESSION)
    assert row is not None and row["display_root"] == env.display(workspace_id)
    assert len(row["created_at"]) == 24
    assert roots.list() == ([root], False)


async def test_prepare_records_a_ready_worktree_by_its_path(env: Env) -> None:
    workspace_id = env.code()
    path = env.worktree(workspace_id)
    roots = env.roots
    assert await roots.prepare(SESSION, workspace_id, WORKTREE) == (
        SessionRoot(SESSION, workspace_id, WORKTREE),
        True,
    )
    row = env.store.get_session_root(SESSION)
    assert row is not None and row["display_root"] == str(path)
    assert roots.sessions_in(WORKTREE) == [SESSION]
    assert roots.sessions_in(OTHER_WORKTREE) == []


@pytest.mark.parametrize("state", ["bound", "released"])
@pytest.mark.parametrize("thread", [False, True])
async def test_prepare_refuses_a_bot_copy(env: Env, state: str, thread: bool) -> None:
    workspace_id = env.code()
    env.worktree(workspace_id)
    bot_copy(env.store, workspace_id, WORKTREE, state=state, thread=thread)
    assert await refused(env.roots, workspace_id, WORKTREE) == "bot_copy_root"
    assert env.store.get_session_root(SESSION) is None


async def test_prepare_refuses_a_session_the_app_already_has(env: Env) -> None:
    workspace_id = env.code()
    env.actors[SESSION] = FakeActor(live=False)  # an actor at all, live or not
    assert await refused(env.roots, workspace_id) == "session_exists"
    env.actors.clear()
    env.known.add(SESSION)
    assert await refused(env.roots, workspace_id) == "session_exists"
    assert env.roots.list() == ([], False)


async def test_prepare_refuses_a_vault_or_unknown_id(env: Env) -> None:
    notes = env.tmp_path / "notes"
    notes.mkdir()
    vault, _ = env.service.register_existing(COMMAND, str(notes))
    assert await refused(env.roots, vault.workspace_id) == "unknown_workspace"
    assert await refused(env.roots, str(uuid.uuid4())) == "unknown_workspace"
    assert env.roots.list() == ([], False)


async def test_prepare_refuses_an_untrusted_folder(env: Env) -> None:
    workspace_id = env.code(trust=False)
    assert await refused(env.roots, workspace_id) == "trust_required"
    assert env.roots.list() == ([], False)


async def test_prepare_refuses_a_moved_folder(env: Env) -> None:
    workspace_id = env.code()
    (env.tmp_path / "repo").rename(env.tmp_path / "moved")
    assert await refused(env.roots, workspace_id) == "root_unavailable"
    assert env.roots.list() == ([], False)


async def test_prepare_refuses_an_unknown_or_foreign_worktree(env: Env) -> None:
    workspace_id = env.code()
    other_id = env.code("other")
    env.worktree(other_id)
    assert await refused(env.roots, workspace_id, OTHER_WORKTREE) == "unknown_worktree"
    assert await refused(env.roots, workspace_id, WORKTREE) == "unknown_worktree"
    assert env.roots.list() == ([], False)


@pytest.mark.parametrize(
    ("state", "folder"), [("creating", True), ("removing", True), ("kept", True), ("ready", False)]
)
async def test_prepare_refuses_a_worktree_not_ready(env: Env, state: str, folder: bool) -> None:
    workspace_id = env.code()
    env.worktree(workspace_id, state=state, folder=folder)
    assert await refused(env.roots, workspace_id, WORKTREE) == "worktree_not_ready"
    assert env.roots.list() == ([], False)


async def test_prepare_refuses_another_root_for_a_recorded_session(env: Env) -> None:
    workspace_id = env.code()
    other_id = env.code("other")
    await env.roots.prepare(SESSION, workspace_id, None)
    assert await refused(env.roots, other_id) == "session_exists"
    assert env.roots.list() == ([SessionRoot(SESSION, workspace_id, None)], False)


# --- forks ------------------------------------------------------------------------------------


async def test_a_fork_copies_its_source_root(env: Env) -> None:
    workspace_id = env.code()
    env.worktree(workspace_id)
    await env.roots.prepare(SESSION, workspace_id, WORKTREE)
    env.roots.copy_for_fork(SESSION, FORK)
    source, fork = env.store.get_session_root(SESSION), env.store.get_session_root(FORK)
    assert source is not None and fork is not None
    fields = ("workspace_id", "worktree_id", "display_root")
    assert [fork[name] for name in fields] == [source[name] for name in fields]


def test_a_fork_of_a_session_without_a_root_gets_none(env: Env) -> None:
    env.roots.copy_for_fork(SESSION, FORK)
    assert env.roots.list() == ([], False)


# --- resolve: no row --------------------------------------------------------------------------


async def test_resolve_without_a_row_in_a_vault_writes_nothing(env: Env) -> None:
    notes = env.tmp_path / "notes"
    notes.mkdir()
    vault, _ = env.service.register_existing(COMMAND, str(notes))
    env.service.select(COMMAND, vault.workspace_id)
    assert env.service.resolve_active().workspace.kind == "vault"
    assert await env.roots.resolve(SESSION) is None
    assert env.roots.list() == ([], False)


async def test_resolve_without_a_row_records_the_active_code_folder(env: Env) -> None:
    workspace_id = env.code()
    env.service.select(COMMAND, workspace_id)
    active = env.service.resolve_active()
    assert active.workspace.kind == "code"
    assert await env.roots.resolve(SESSION) is None
    row = env.store.get_session_root(SESSION)
    assert row is not None
    assert (row["workspace_id"], row["worktree_id"], row["display_root"]) == (
        workspace_id,
        None,
        str(active.cwd),
    )
    # The next spawn finds the row and runs in that folder, whatever is active by then.
    assert await env.roots.resolve(SESSION) == active.cwd


# --- resolve: a recorded root -----------------------------------------------------------------


async def test_resolve_gives_the_recorded_folder(env: Env) -> None:
    workspace_id = env.code()
    roots = env.roots
    await roots.prepare(SESSION, workspace_id, None)
    assert await roots.resolve(SESSION) == Path(env.display(workspace_id))


async def test_resolve_gives_a_ready_worktree(env: Env) -> None:
    workspace_id = env.code()
    path = env.worktree(workspace_id)
    roots = env.roots
    await roots.prepare(SESSION, workspace_id, WORKTREE)
    assert await roots.resolve(SESSION) == path


async def test_resolve_refuses_a_folder_no_longer_trusted(env: Env) -> None:
    workspace_id = env.code()
    roots = env.roots
    await roots.prepare(SESSION, workspace_id, None)
    env.service.set_policy(REVOKE, workspace_id, 2, "existing_readonly", [])
    assert await start_refusal(roots) == MSG_ROOT_UNTRUSTED


async def test_resolve_refuses_a_row_naming_no_code_folder(env: Env) -> None:
    notes = env.tmp_path / "notes"
    notes.mkdir()
    vault, _ = env.service.register_existing(COMMAND, str(notes))
    env.put(vault.workspace_id, str(notes))
    assert await start_refusal(env.roots) == MSG_ROOT_UNTRUSTED
    assert env.store.get_session_root(SESSION) is not None  # only the spawn is refused


async def test_resolve_refuses_an_unregistered_folder(env: Env) -> None:
    env.put(str(uuid.uuid4()), str(env.tmp_path))
    assert await start_refusal(env.roots) == MSG_ROOT_UNTRUSTED


async def test_resolve_refuses_a_renamed_folder(env: Env) -> None:
    workspace_id = env.code()
    roots = env.roots
    await roots.prepare(SESSION, workspace_id, None)
    (env.tmp_path / "repo").rename(env.tmp_path / "moved")
    assert await start_refusal(roots) == MSG_ROOT_MISSING


async def test_resolve_refuses_a_row_off_its_folder(env: Env) -> None:
    workspace_id = env.code()
    env.put(workspace_id, str(env.tmp_path / "elsewhere"))
    assert await start_refusal(env.roots) == MSG_ROOT_MISSING


@pytest.mark.parametrize(
    ("state", "folder"),
    [("removed", False), ("removing", True), ("kept", True), ("creating", True), ("ready", False)],
)
async def test_resolve_refuses_a_worktree_not_ready(env: Env, state: str, folder: bool) -> None:
    workspace_id = env.code()
    path = env.worktree(workspace_id, state=state, folder=folder)
    env.put(workspace_id, str(path), WORKTREE)
    assert await start_refusal(env.roots) == MSG_WORKTREE_UNAVAILABLE


async def test_resolve_refuses_a_worktree_row_that_is_gone(env: Env) -> None:
    workspace_id = env.code()
    env.put(workspace_id, str(env.tmp_path / "worktrees" / WORKTREE), WORKTREE)
    assert await start_refusal(env.roots) == MSG_WORKTREE_UNAVAILABLE


async def test_resolve_refuses_a_worktree_turned_removed_after_prepare(env: Env) -> None:
    workspace_id = env.code()
    env.worktree(workspace_id)
    roots = env.roots
    await roots.prepare(SESSION, workspace_id, WORKTREE)
    env.store.update_worktree_state(WORKTREE, "ready", "removed", now_iso())
    assert await start_refusal(roots) == MSG_WORKTREE_UNAVAILABLE


async def test_resolve_refuses_a_blocked_worktree_while_ready(env: Env) -> None:
    workspace_id = env.code()
    path = env.worktree(workspace_id)
    roots = env.roots
    await roots.prepare(SESSION, workspace_id, WORKTREE)
    roots.block(WORKTREE)
    assert await start_refusal(roots) == MSG_WORKTREE_UNAVAILABLE
    row = env.store.get_worktree(WORKTREE)
    assert row is not None and row["state"] == "ready"
    roots.unblock(WORKTREE)
    assert await roots.resolve(SESSION) == path


async def test_resolve_lets_a_store_failure_through(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(env.store, "get_session_root", failing_read)
    with pytest.raises(StateError):
        await env.roots.resolve(SESSION)


# --- removal --------------------------------------------------------------------------------


async def test_live_in_needs_a_live_actor_in_that_worktree(env: Env) -> None:
    workspace_id = env.code()
    env.worktree(workspace_id)
    env.worktree(workspace_id, OTHER_WORKTREE)
    roots = env.roots
    await roots.prepare(SESSION, workspace_id, WORKTREE)
    assert not await roots.live_in(WORKTREE)  # no actor
    env.actors[SESSION] = FakeActor(live=False)
    assert not await roots.live_in(WORKTREE)
    env.actors[SESSION].live = True
    assert await roots.live_in(WORKTREE)
    assert not await roots.live_in(OTHER_WORKTREE)


@pytest.mark.parametrize("thread", [False, True])
async def test_bot_users_are_live_without_becoming_session_roots(env: Env, thread: bool) -> None:
    workspace_id = env.code()
    env.worktree(workspace_id)
    bot_copy(env.store, workspace_id, WORKTREE, thread=thread)
    roots = env.roots
    live_id = FORK if thread else SESSION
    env.actors[SESSION] = FakeActor(live=False)
    assert not await roots.live_in(WORKTREE)
    env.actors[live_id] = FakeActor(live=True)
    assert await roots.live_in(WORKTREE)
    assert not await roots.live_in(OTHER_WORKTREE)
    assert roots.sessions_in(WORKTREE) == []
    assert roots.root_of(live_id) is None and roots.transcript_roots() == {}
    assert roots.list() == ([], False)
    assert await roots.resolve(live_id) is None
    assert await roots.context(live_id) is None
    with env.store._connection(write=True) as connection:
        connection.execute("UPDATE bot_code_copies SET state='released'")
    assert not await roots.live_in(WORKTREE)


# --- the catalog's lookup ---------------------------------------------------------------------


def failing_read(*_args: object) -> object:
    raise StateError("unavailable")


async def test_root_of_and_transcript_roots_give_the_spawn_cwd(env: Env) -> None:
    workspace_id = env.code()
    path = env.worktree(workspace_id)
    roots: RootLookup = env.roots  # pyright pins the structural match with the catalog
    assert isinstance(roots, SessionRoots)
    await roots.prepare(SESSION, workspace_id, None)
    await roots.prepare(FORK, workspace_id, WORKTREE)
    folder = await roots.resolve(SESSION)
    assert roots.root_of(SESSION) == folder == Path(env.display(workspace_id))
    assert roots.root_of(FORK) == path
    assert roots.root_of(str(uuid.uuid4())) is None
    assert roots.transcript_roots() == {SESSION: folder, FORK: path}


def test_lookup_store_failures_answer_nothing_and_log_no_id(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(env.store, "get_session_root", failing_read)
    monkeypatch.setattr(env.store, "list_session_roots", failing_read)
    roots = env.roots
    with caplog.at_level(logging.WARNING, logger="viki_agent.code.session_roots"):
        assert roots.root_of(SESSION) is None
        with pytest.raises(StateError):
            roots.transcript_roots()
    assert len(caplog.records) == 1
    assert all(SESSION not in record.getMessage() for record in caplog.records)
