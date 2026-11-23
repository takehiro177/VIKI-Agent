"""What the inbox suggestion run keeps for each vault, sealed in the app's own store.

The suggester (`capture_suggest.CaptureSuggester`) keeps each vault's latest run and its
suggestions under the part "suggestions"; Decision's inbox host
(`decision_loop.inbox_host.SuggestionHost`) keeps the item keys it started under "started". Each
part is one row of `inbox_suggestions`: its owner's canonical JSON, sealed under a context that
names the vault and the part, so a body copied into another row does not open.

`load` reads one part of every vault once, as its owner is built, and answers None when the store
or its sealing cannot be used; a row that does not open or parse is left out. `save` writes one
vault's part, taking the value from its owner only once it holds the write lock, so concurrent
saves land in order and the last one writes the newest value. A failed save is logged by exception
type and leaves the owner's memory as it is. Nothing here logs a ref, a reason or any body.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from anyio.to_thread import run_sync

from viki_agent import protection
from viki_agent.automation.routines import stamp
from viki_agent.conversations.drafts import require_private_store
from viki_agent.storage import SessionStore, StateError

log = logging.getLogger(__name__)

Part = Literal["suggestions", "started"]

_FAILURES = (StateError, protection.ProtectionError, sqlite3.Error, ValueError, TypeError)


class SuggestionMemory:
    def __init__(
        self, store: SessionStore, *, wall: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._store = store
        self._wall = wall
        self._lock = asyncio.Lock()

    def load(self, part: Part) -> dict[str, object] | None:
        """Every vault's `part`, decoded; None when the store cannot be read."""
        try:
            require_private_store(self._store)
            with self._store._connection() as connection:
                rows = connection.execute(
                    "SELECT workspace_id,body FROM inbox_suggestions WHERE part=?", (part,)
                ).fetchall()
        except _FAILURES as exc:
            log.warning("inbox suggestions not loaded: %s", type(exc).__name__)
            return None
        loaded: dict[str, object] = {}
        for workspace_id, body in rows:
            try:
                plain = protection.unprotect(body, context=_context(workspace_id, part))
                loaded[workspace_id] = json.loads(plain.decode("ascii"))
            except protection.ProtectionError, ValueError:
                continue  # this row stays unread; the next save replaces it
        return loaded

    async def save(self, workspace_id: str, part: Part, value: Callable[[], object]) -> None:
        """Write `value()` as the vault's `part`; a failure is logged, never raised."""
        async with self._lock:
            try:
                plain = json.dumps(value(), sort_keys=True, separators=(",", ":")).encode("ascii")
                await run_sync(self._write, workspace_id, part, plain)
            except _FAILURES as exc:
                log.warning("inbox suggestions not saved: %s", type(exc).__name__)

    def _write(self, workspace_id: str, part: Part, plain: bytes) -> None:
        body = protection.protect(plain, context=_context(workspace_id, part))
        require_private_store(self._store)
        with self._store._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO inbox_suggestions (workspace_id,part,body,updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(workspace_id,part) DO UPDATE SET "
                "body=excluded.body,updated_at=excluded.updated_at",
                (workspace_id, part, body, stamp(self._wall())),
            )


def _context(workspace_id: str, part: str) -> bytes:
    return f"viki-agent/inbox-suggestions/{workspace_id}/{part}".encode()
