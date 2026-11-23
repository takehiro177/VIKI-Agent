"""The folder each session runs in: recorded once, checked again at every spawn.

A row in `session_roots` ties a session to a trusted code folder, or to a worktree of one, by
the exact path its spawn uses as its cwd. `prepare` records a chosen root (advisory: it only
records), `resolve` is the spawn check that decides, and the catalog reads the same rows to find
each session's transcripts. `folders` names the ticked code folders a spawn without a row adds
(ADR-050).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from viki_agent.domain import Workspace
from viki_agent.drivers.base import StartError
from viki_agent.session import now_iso
from viki_agent.storage import SessionRootRow, SessionStore, StateError
from viki_agent.workspaces import WorkspaceError, WorkspaceService

logger = logging.getLogger(__name__)

MAX_ROOT_ROWS = 4096
MAX_ROOT_CONTEXT_BYTES = 64 * 1024
MSG_ROOT_UNTRUSTED = (
    "This session's folder is not trusted, or was removed from VIKI Agent, so it can't start. "
    "Trust it in Settings and send again, or start a new chat."
)
MSG_ROOT_MISSING = (
    "This session's folder can't be found, so it can't start. "
    "Put the folder back where it was, then send again."
)
MSG_WORKTREE_UNAVAILABLE = (
    "This session's worktree was removed or isn't available right now, so it can't start. "
    "If cleanup archived it, restore it from the Code view's Worktrees tab, then send again."
)


class RootError(Exception):
    """A refused `prepare`; `code` is one the routes map to a status."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SessionRoot:
    session_id: str
    workspace_id: str
    worktree_id: str | None
    additional_workspace_ids: tuple[str, ...] = ()


class LiveActor(Protocol):
    @property
    def live(self) -> bool: ...


def _root(row: SessionRootRow) -> SessionRoot:
    return SessionRoot(
        row["session_id"],
        row["workspace_id"],
        row["worktree_id"],
        tuple(root["workspace_id"] for root in row.get("additional_roots", [])),
    )


def _trusted(workspace: Workspace) -> bool:
    return workspace.policy == "ordinary" and workspace.persistent


class SessionRoots:
    def __init__(
        self,
        store: SessionStore,
        workspaces: WorkspaceService,
        *,
        exists: Callable[[str], bool],
        actor: Callable[[str], LiveActor | None],
        ticked: Callable[[], tuple[str, ...]] = tuple,
    ) -> None:
        self._store = store
        self._workspaces = workspaces
        self._exists = exists
        self._actor = actor
        self._ticked = ticked  # the user's `chat.workspace_ids`, read in a worker at each spawn
        self._blocked: set[str] = set()  # worktree ids a removal holds; touched only on the loop

    # --- recording ---------------------------------------------------------------------------

    async def prepare(
        self,
        session_id: str,
        workspace_id: str,
        worktree_id: str | None,
        additional_workspace_ids: tuple[str, ...] = (),
    ) -> tuple[SessionRoot, bool]:
        """Record a new session's root; the bool says this call created the row."""
        if self._actor(session_id) is not None:
            raise RootError("session_exists")
        return await asyncio.to_thread(
            self._prepare, session_id, workspace_id, worktree_id, additional_workspace_ids
        )

    def _prepare(
        self,
        session_id: str,
        workspace_id: str,
        worktree_id: str | None,
        additional_workspace_ids: tuple[str, ...] = (),
    ) -> tuple[SessionRoot, bool]:
        if self._exists(session_id):
            raise RootError("session_exists")
        if (
            not isinstance(additional_workspace_ids, tuple)
            or len(additional_workspace_ids) > 7
            or (additional_workspace_ids and worktree_id is not None)
        ):
            raise RootError("invalid_body")
        try:
            valid = all(
                isinstance(wid, str) and str(uuid.UUID(wid)) == wid
                for wid in additional_workspace_ids
            )
        except ValueError:
            raise RootError("invalid_body") from None
        if (
            not valid
            or len(set(additional_workspace_ids)) != len(additional_workspace_ids)
            or workspace_id in additional_workspace_ids
        ):
            raise RootError("invalid_body")
        selected = []
        for wid in (workspace_id, *additional_workspace_ids):
            try:
                workspace = self._workspaces.get(wid, kind="code")
            except WorkspaceError:
                raise RootError("unknown_workspace") from None
            if not _trusted(workspace):
                raise RootError("trust_required")
            if self._workspaces.current_root(workspace) != workspace.root:
                raise RootError("root_unavailable")
            selected.append(workspace)
        workspace = selected[0]
        display = workspace.root.display
        if worktree_id is not None:
            worktree = self._store.get_worktree(worktree_id)
            if worktree is None or worktree["workspace_id"] != workspace_id:
                raise RootError("unknown_worktree")
            copy = self._store.copy_states((worktree_id,)).get(worktree_id)
            if copy is not None and copy["state"] != "former":  # a former copy is no Bot's
                raise RootError("bot_copy_root")
            if worktree["state"] != "ready" or not Path(worktree["path"]).is_dir():
                raise RootError("worktree_not_ready")
            display = worktree["path"]
        row = {
            "session_id": session_id,
            "workspace_id": workspace_id,
            "worktree_id": worktree_id,
            "display_root": display,
            "created_at": now_iso(),
        }
        if additional_workspace_ids:
            row["additional_roots"] = [
                {
                    "workspace_id": extra.workspace_id,
                    "display_root": extra.root.display,
                    "physical_id": extra.root.physical_id,
                }
                for extra in selected[1:]
            ]
        try:
            created = self._store.put_session_root(row)
        except StateError as exc:
            if exc.code == "conflict":
                raise RootError("session_exists") from None
            raise
        return _root(row), created

    def copy_for_fork(self, source_id: str, fork_id: str) -> None:
        """Give a fork its source's root before its first spawn; a source without one has none."""
        source = self._store.get_session_root(source_id)
        if source is None:
            return
        self._store.put_session_root({**source, "session_id": fork_id, "created_at": now_iso()})

    # --- listing -----------------------------------------------------------------------------

    def list(self, limit: int = MAX_ROOT_ROWS) -> tuple[list[SessionRoot], bool]:
        rows, truncated = self._store.list_session_roots(limit)
        return [_root(row) for row in rows], truncated

    def sessions_in(self, worktree_id: str) -> list[str]:
        return self._store.session_roots_in_worktree(worktree_id)

    # --- the catalog's lookup (catalog.RootLookup), called from blocking catalog threads -----

    def root_of(self, session_id: str) -> Path | None:
        """The recorded cwd of a session's spawns, so its transcripts' project folder follows."""
        try:
            row = self._store.get_session_root(session_id)
        except StateError as exc:
            logger.warning("session root unreadable: %s", exc.code)
            return None
        return Path(row["display_root"]) if row is not None else None

    def roots_for(self, session_ids: list[str]) -> dict[str, Path]:
        """Those of `session_ids` with a recorded root, to its cwd, read in chunked bulk
        statements rather than one read per id. Raises StateError on an unreadable store, as
        `transcript_roots` does."""
        rows = self._store.session_root_paths(session_ids)
        return {session_id: Path(root) for session_id, root in rows.items()}

    def transcript_roots(self) -> dict[str, Path]:
        """Every recorded session's id to its cwd, for the catalog's listing and search.
        Raises StateError on an unreadable store: the catalog is what decides what that means
        for a listing, and logs about it; this layer does not."""
        rows, _ = self._store.list_session_roots(MAX_ROOT_ROWS)
        return {row["session_id"]: Path(row["display_root"]) for row in rows}

    # --- removal blocking --------------------------------------------------------------------

    def block(self, worktree_id: str) -> None:
        self._blocked.add(worktree_id)

    def unblock(self, worktree_id: str) -> None:
        self._blocked.discard(worktree_id)

    def blocked(self, worktree_id: str) -> bool:
        """On the loop: whether a removal holds this worktree, so a check or terminal start
        refuses it."""
        return worktree_id in self._blocked

    async def live_in(self, worktree_id: str) -> bool:
        ids = await asyncio.to_thread(self._store.sessions_using_worktree, worktree_id)
        return any((actor := self._actor(sid)) is not None and actor.live for sid in ids)

    # --- the spawn check (session.RootResolver) ----------------------------------------------

    async def resolve(self, session_id: str) -> Path | None:
        """The cwd of this spawn, or None for the launch's own folder; refuses a root that is no
        longer trusted, present or ready."""
        row = await asyncio.to_thread(self._check, session_id)
        if row is None:
            return None
        # Last, on the loop, with no await before the return: the actor goes live synchronously
        # after this returns, so a removal that blocks and then asks live_in either refuses this
        # spawn here or sees it live.
        if row["worktree_id"] is not None and row["worktree_id"] in self._blocked:
            raise StartError("bad_request", MSG_WORKTREE_UNAVAILABLE)
        return Path(row["display_root"])

    async def context(self, session_id: str) -> str | None:
        """Revalidate a multi-folder selection for this turn and render its paths as JSON data."""
        return await asyncio.to_thread(self._context, session_id)

    def _context(self, session_id: str) -> str | None:
        row = self._store.get_session_root(session_id)
        if row is None or not row.get("additional_roots"):
            return None  # Single-folder live sessions retain their existing launch checks.
        self._check_row(row)
        paths = json.dumps(
            {
                "primary_cwd": row["display_root"],
                "additional_folders": [root["display_root"] for root in row["additional_roots"]],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        context = (
            f"Selected code folders for this conversation:\n{paths}\n"
            "Native permissions govern access to these folders."
        )
        if len(context.encode("utf-8")) > MAX_ROOT_CONTEXT_BYTES:
            raise StartError("bad_request", MSG_ROOT_MISSING)
        return context

    async def folders(self, session_id: str) -> tuple[Path, ...]:
        """The ticked code folders this spawn adds (`--add-dir`): () for a session with a root
        or a launch in a code folder, and a ticked id that is not a trusted code folder still
        at its root is skipped."""
        return await asyncio.to_thread(self._folders, session_id)

    def _folders(self, session_id: str) -> tuple[Path, ...]:
        if (
            self._store.get_session_root(session_id) is not None
            or self._workspaces.resolve_active().workspace.kind == "code"
        ):
            return ()
        folders = []
        for workspace_id in self._ticked():
            try:
                workspace = self._workspaces.get(workspace_id, kind="code")
            except WorkspaceError:
                continue  # unknown, removed, or a vault
            if _trusted(workspace) and self._workspaces.current_root(workspace) == workspace.root:
                folders.append(Path(workspace.root.display))
        return tuple(folders)

    def _check(self, session_id: str) -> SessionRootRow | None:
        row = self._store.get_session_root(session_id)
        if row is None:
            self._record_active(session_id)
            return None
        return self._check_row(row)

    def _check_row(self, row: SessionRootRow) -> SessionRootRow:
        additional = row.get("additional_roots", [])
        if additional and row["worktree_id"] is not None:
            raise StartError("bad_request", MSG_WORKTREE_UNAVAILABLE)
        for pinned in (row, *additional):
            try:
                workspace = self._workspaces.get(pinned["workspace_id"], kind="code")
            except WorkspaceError:
                raise StartError("bad_request", MSG_ROOT_UNTRUSTED) from None
            if not _trusted(workspace):
                raise StartError("bad_request", MSG_ROOT_UNTRUSTED)
            current = self._workspaces.current_root(workspace)
            if current is None or current != workspace.root:
                raise StartError("bad_request", MSG_ROOT_MISSING)
            if (pinned is not row or row["worktree_id"] is None) and pinned[
                "display_root"
            ] != workspace.root.display:
                raise StartError("bad_request", MSG_ROOT_MISSING)
            if pinned is not row and pinned["physical_id"] != workspace.root.physical_id:
                raise StartError("bad_request", MSG_ROOT_MISSING)
        if row["worktree_id"] is None:
            return row
        worktree = self._store.get_worktree(row["worktree_id"])
        if worktree is None or worktree["state"] != "ready" or not Path(worktree["path"]).is_dir():
            raise StartError("bad_request", MSG_WORKTREE_UNAVAILABLE)
        return row

    def _record_active(self, session_id: str) -> None:
        """A session first spawned in the active code folder stays with that folder after
        another workspace is activated; a session in a vault keeps no row."""
        active = self._workspaces.resolve_active()
        if active.workspace.kind != "code" or not active.workspace.persistent:
            return
        self._store.put_session_root(
            {
                "session_id": session_id,
                "workspace_id": active.workspace.workspace_id,
                "worktree_id": None,
                "display_root": str(active.cwd),
                "created_at": now_iso(),
            }
        )
