"""App-owned Bot links and result metadata. Provider history and permissions stay native."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from viki_agent.automation.routines import (
    MAX_ROUTINES,
    RoutineError,
    RoutineService,
    decode_revision,
    parse_stamp,
    stamp,
)
from viki_agent.code.code_files import CodeError
from viki_agent.code.code_git import CodeGit
from viki_agent.conversations.drafts import require_private_store
from viki_agent.decision_loop.rules import ELIGIBLE, REASONS
from viki_agent.persistence.bot_schemas import LEARNING_TABLES
from viki_agent.profiles.service import MAX_INTEGER, ProfileError, ProfileService
from viki_agent.profiles.service import _id as profile_id
from viki_agent.profiles.service import _revision as profile_revision
from viki_agent.storage import SessionStore
from viki_agent.vaults.workspace_mutation import admits_note_writes
from viki_agent.workspaces import WorkspaceError, WorkspaceService

MAX_BOTS = 64
MAX_BOT_THREADS = 50
DELIVERY_STATES = ("held", "off", "unavailable", "sending", "sent", "failed", "unknown")
DELIVERY_REASONS = (
    "none",
    "off",
    "held",
    "not_connected",
    "workspace_mismatch",
    "stale_bot",
    "stale_consent",
    "policy_changed",
    "app_closing",
    "send_failed",
    "send_unknown",
    "unsupported_outcome",
    "capacity",
)
# CodeGit's repository and HEAD refusals, as the code-folder grant names them.
_CODE_FOLDER_ERRORS = {
    "unknown_workspace": "code_folder_missing",
    "root_unavailable": "code_folder_missing",
    "trust_required": "code_folder_untrusted",
    "not_repository": "code_folder_not_repository",
    "not_repository_root": "code_folder_not_repository",
    "no_commits": "code_folder_no_commits",
    "git_unavailable": "code_folder_git_unavailable",
    "git_settings_changed": "git_settings_changed",
}


class BotError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _id(value: object, *, code: str = "invalid_input") -> str:
    try:
        return profile_id(value)
    except ProfileError:
        raise BotError(code) from None


def _revision(value: object, *, code: str = "invalid_input") -> int:
    try:
        return profile_revision(value)
    except ProfileError:
        raise BotError(code) from None


def _record(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("bot_id", "profile_id", "workspace_id", "chat_session_id"):
        _id(result[key], code="corrupt")
    _revision(result["state_revision"], code="corrupt")
    digest = result["request_hash"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest)
    ):
        raise BotError("corrupt")
    try:
        parse_stamp(result["created_at"])
        parse_stamp(result["updated_at"])
    except ValueError:
        raise BotError("corrupt") from None
    return result


def _delivery_values(attention: str, reason: str, state: str, delivery_reason: str) -> None:
    if (
        attention not in ELIGIBLE
        or reason not in REASONS
        or state not in DELIVERY_STATES
        or delivery_reason not in DELIVERY_REASONS
    ):
        raise BotError("invalid_input")


class BotService:
    def __init__(
        self,
        store: SessionStore,
        profiles: ProfileService,
        routines: RoutineService,
        workspaces: WorkspaceService,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store, self.profiles, self.routines, self.workspaces = (
            store,
            profiles,
            routines,
            workspaces,
        )
        self.now = now
        self.code_git: CodeGit | None = None

    def bind_code_git(self, code_git: CodeGit) -> None:
        """Check grants with the server's one baseline-aware CodeGit; unbound, grants refuse."""
        self.code_git = code_git

    def _stamp(self) -> str:
        moment = self.now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise BotError("unavailable")
        value = stamp(moment)
        try:
            parse_stamp(value)
        except ValueError:
            raise BotError("unavailable") from None
        return value

    @staticmethod
    def _one(connection: sqlite3.Connection, bot_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM bots WHERE bot_id=?", (bot_id,)).fetchone()
        if row is None:
            raise BotError("not_found")
        return _record(row)

    @staticmethod
    def _registry(connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM bots ORDER BY created_at,bot_id LIMIT 65"
        ).fetchall()
        if len(rows) > MAX_BOTS:
            raise BotError("corrupt")
        return [_record(row) for row in rows]

    @staticmethod
    def _public_record(row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key != "request_hash"}

    def record(self, bot_id: str) -> dict[str, Any]:
        bot_id = _id(bot_id)
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            return self._public_record(self._one(connection, bot_id))

    def find(self, profile_id: str, workspace_id: str) -> dict[str, Any] | None:
        profile_id, workspace_id = _id(profile_id), _id(workspace_id)
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM bots WHERE profile_id=? AND workspace_id=?",
                (profile_id, workspace_id),
            ).fetchone()
            return None if row is None else self._public_record(_record(row))

    def _routine_rows(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT r.routine_id,r.state,r.current_revision,r.state_revision,v.revision,v.format,"
            "v.schedule,v.body,v.body_bytes,v.body_sha256 FROM routines r "
            "LEFT JOIN routine_revisions v ON v.routine_id=r.routine_id "
            "AND v.revision=r.current_revision ORDER BY r.created_at,r.routine_id LIMIT 65"
        ).fetchall()
        if len(rows) > MAX_ROUTINES:
            raise BotError("corrupt")
        result = []
        for row in rows:
            current = dict(row)
            _id(current["routine_id"], code="corrupt")
            _revision(current["current_revision"], code="corrupt")
            _revision(current["state_revision"], code="corrupt")
            if current["revision"] != current["current_revision"]:
                raise BotError("corrupt")
            try:
                current["document"] = decode_revision(current)
            except RoutineError as error:
                raise BotError(error.code) from None
            result.append(current)
        return result

    def _project(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        try:
            active_id = self.workspaces.resolve_active().workspace.workspace_id
        except WorkspaceError:
            active_id = None
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            routines = self._routine_rows(connection)
        result = []
        for row in rows:
            try:
                profile = self.profiles.get(row["profile_id"])
            except ProfileError as error:
                if error.code != "not_found":
                    raise BotError(error.code) from None
                profile = None
            with self.store._connection() as connection:
                connection.row_factory = sqlite3.Row
                workspace = connection.execute(
                    "SELECT * FROM workspaces WHERE workspace_id=?", (row["workspace_id"],)
                ).fetchone()
                projected_workspace = None
                if workspace is not None:
                    if workspace["kind"] != "vault":
                        raise BotError("corrupt")
                    projected_workspace = {
                        "name": workspace["name"],
                        "policy": workspace["policy"],
                        "active": active_id == row["workspace_id"],
                    }
                chat = self._chat(connection, row, workspace)
                latest = self._latest(connection, row["bot_id"])
                folder = connection.execute(
                    "SELECT f.workspace_id,w.name,w.kind FROM bot_code_folders f "
                    "JOIN workspaces w ON w.workspace_id=f.workspace_id WHERE f.bot_id=?",
                    (row["bot_id"],),
                ).fetchone()
                if folder is not None and folder["kind"] != "code":
                    raise BotError("corrupt")
            pair = (row["profile_id"], row["workspace_id"])
            result.append(
                {
                    **self._public_record(row),
                    "profile": None
                    if profile is None
                    else {
                        "revision": profile["revision"],
                        "archived": profile["archived"],
                        "identity": profile["document"]["identity"],
                    },
                    "workspace": projected_workspace,
                    "chat": chat,
                    "routine_ids": [
                        item["routine_id"]
                        for item in routines
                        if (item["document"]["profile_id"], item["document"]["workspace_id"])
                        == pair
                    ],
                    "delivery": {"enabled": False, "available": False, "reason": "off"},
                    "latest_delivery": latest,
                    "code_folder": None
                    if folder is None
                    else {"workspace_id": folder["workspace_id"], "name": folder["name"]},
                }
            )
        return result

    @staticmethod
    def _chat(
        connection: sqlite3.Connection, row: dict[str, Any], workspace: sqlite3.Row | None
    ) -> dict[str, Any]:
        session = connection.execute(
            "SELECT origin FROM sessions WHERE session_id=?", (row["chat_session_id"],)
        ).fetchone()
        binding = connection.execute(
            "SELECT profile_id,profile_revision,workspace_id,native_started "
            "FROM session_profile_bindings WHERE session_id=?",
            (row["chat_session_id"],),
        ).fetchone()
        if (session is None) != (binding is None) or (
            session is not None and session["origin"] != "profile"
        ):
            raise BotError("corrupt")
        revision = None
        started = False
        if binding is not None:
            if (
                binding["profile_id"] != row["profile_id"]
                or binding["workspace_id"] != row["workspace_id"]
            ):
                raise BotError("corrupt")
            revision = _revision(binding["profile_revision"], code="corrupt")
            started = bool(binding["native_started"])
        archived = False
        if workspace is not None:
            scope = hashlib.sha256(workspace["canonical_key"].encode("utf-8")).hexdigest()
            metadata = connection.execute(
                "SELECT archived FROM session_metadata WHERE scope=? AND session_id=?",
                (scope, row["chat_session_id"]),
            ).fetchone()
            archived = metadata is not None and bool(metadata["archived"])
        return {"profile_revision": revision, "started": started, "archived": archived}

    def get(self, bot_id: str) -> dict[str, Any]:
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = self._one(connection, _id(bot_id))
        return self._project([row])[0]

    def list(self) -> list[dict[str, Any]]:
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = self._registry(connection)
        return self._project(rows)

    def chat_session_ids(self) -> set[str]:
        """Chats a current Bot owns, by the owner rule `threads` uses, older profile-pair chats
        included. A removed Bot's chats are not Bot chats, even when a new Bot takes its seat.

        A Bot thread's member sessions are bound chats, so the memory and pair arms cover them.
        """
        require_private_store(self.store)
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT chat_session_id FROM bots "
                "UNION SELECT m.session_id FROM session_bot_memory m "
                "JOIN bots bot ON bot.bot_id=m.bot_id "
                "UNION SELECT b.session_id FROM session_profile_bindings b "
                "JOIN bots bot ON bot.profile_id=b.profile_id AND bot.workspace_id=b.workspace_id "
                "WHERE NOT EXISTS (SELECT 1 FROM session_bot_memory owner "
                "WHERE owner.session_id=b.session_id AND owner.bot_id!=bot.bot_id)"
            ).fetchall()
        return {_id(row[0], code="corrupt") for row in rows}

    def threads(self, bot_id: str) -> dict[str, Any]:
        """Chats for this Bot's profile and vault, unless owned by another Bot or a routine.

        A known routine origin stays excluded after its attempts are pruned; an older binding
        of unknown origin keeps the attempt and owner filters without being called interactive.
        """
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            bot = self._one(connection, _id(bot_id))
            workspace = connection.execute(
                "SELECT canonical_key FROM workspaces WHERE workspace_id=?",
                (bot["workspace_id"],),
            ).fetchone()
            if workspace is None:
                raise BotError("workspace_missing")
            scope = hashlib.sha256(workspace["canonical_key"].encode("utf-8")).hexdigest()
            rows = connection.execute(
                "SELECT b.session_id,b.profile_revision,b.created_at,b.native_started,"
                "coalesce(m.title,'') AS title,coalesce(m.archived,0) AS archived "
                "FROM session_profile_bindings b JOIN sessions s ON s.session_id=b.session_id "
                "LEFT JOIN session_metadata m ON m.session_id=b.session_id AND m.scope=? "
                "WHERE b.profile_id=? AND b.workspace_id=? AND s.origin='profile' "
                "AND b.session_id!=? AND b.launch_origin<>'routine' AND NOT EXISTS "
                "(SELECT 1 FROM task_attempts t WHERE t.session_id=b.session_id) "
                "AND NOT EXISTS (SELECT 1 FROM session_bot_memory owner "
                "WHERE owner.session_id=b.session_id AND owner.bot_id!=?) "
                "ORDER BY b.created_at DESC,b.session_id DESC LIMIT ?",
                (
                    scope,
                    bot["profile_id"],
                    bot["workspace_id"],
                    bot["chat_session_id"],
                    bot["bot_id"],
                    MAX_BOT_THREADS + 1,
                ),
            ).fetchall()
        threads = []
        for row in rows[:MAX_BOT_THREADS]:
            _id(row["session_id"], code="corrupt")
            _revision(row["profile_revision"], code="corrupt")
            try:
                parse_stamp(row["created_at"])
            except ValueError:
                raise BotError("corrupt") from None
            title = row["title"]
            if not isinstance(title, str) or len(title) > 256 or any(ord(c) < 32 for c in title):
                raise BotError("corrupt")
            threads.append(
                {
                    "session_id": row["session_id"],
                    "profile_revision": row["profile_revision"],
                    "created_at": row["created_at"],
                    "started": bool(row["native_started"]),
                    "title": title,
                    "archived": bool(row["archived"]),
                }
            )
        return {
            "bot_id": bot["bot_id"],
            "threads": threads,
            "has_more": len(rows) > MAX_BOT_THREADS,
        }

    def create(
        self, bot_id: str, profile_id: str, expected_profile_revision: int, workspace_id: str
    ) -> tuple[dict[str, Any], bool]:
        bot_id, profile_id, workspace_id = _id(bot_id), _id(profile_id), _id(workspace_id)
        expected_profile_revision = _revision(expected_profile_revision)
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "bot_id": bot_id,
                    "profile_id": profile_id,
                    "expected_profile_revision": expected_profile_revision,
                    "workspace_id": workspace_id,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            replay = connection.execute("SELECT * FROM bots WHERE bot_id=?", (bot_id,)).fetchone()
            if replay is not None:
                saved = _record(replay)
                if saved["request_hash"] != request_hash:
                    raise BotError("command_conflict")
                return self._project([saved])[0], True
        try:
            profile = self.profiles.get(profile_id)
        except ProfileError as error:
            raise BotError("profile_missing" if error.code == "not_found" else error.code) from None
        if profile["archived"]:
            raise BotError("profile_archived")
        if profile["current_revision"] != expected_profile_revision:
            raise BotError("profile_changed")
        try:
            workspace = self.workspaces.get(workspace_id)
        except WorkspaceError as error:
            raise BotError(
                "workspace_missing"
                if error.code == "unknown_workspace"
                else "workspace_unavailable"
            ) from None
        if not workspace.persistent or workspace.kind != "vault":
            raise BotError("workspace_unavailable")
        now, chat_id = self._stamp(), str(uuid.uuid4())
        replayed = False
        with self.store._connection(write=True) as connection:
            connection.row_factory = sqlite3.Row
            replay = connection.execute("SELECT * FROM bots WHERE bot_id=?", (bot_id,)).fetchone()
            if replay is not None:
                if _record(replay)["request_hash"] != request_hash:
                    raise BotError("command_conflict")
                replayed = True
            else:
                if connection.execute(
                    "SELECT 1 FROM session_bot_memory WHERE bot_id=? LIMIT 1", (bot_id,)
                ).fetchone():
                    # Historical bindings retain the removed Bot's identity and memory folder.
                    raise BotError("command_conflict")
                current = connection.execute(
                    "SELECT current_revision,archived FROM profiles WHERE profile_id=?",
                    (profile_id,),
                ).fetchone()
                if current is None:
                    raise BotError("profile_missing")
                if current["archived"]:
                    raise BotError("profile_archived")
                if current["current_revision"] != expected_profile_revision:
                    raise BotError("profile_changed")
                registered = connection.execute(
                    "SELECT revision,kind FROM workspaces WHERE workspace_id=?", (workspace_id,)
                ).fetchone()
                if registered is None:
                    raise BotError("workspace_missing")
                if registered["revision"] != workspace.revision or registered["kind"] != "vault":
                    raise BotError("workspace_unavailable")
                if connection.execute(
                    "SELECT 1 FROM bots WHERE profile_id=? AND workspace_id=?",
                    (profile_id, workspace_id),
                ).fetchone():
                    raise BotError("conflict")
                if connection.execute("SELECT count(*) FROM bots").fetchone()[0] >= MAX_BOTS:
                    raise BotError("capacity")
                connection.execute(
                    "INSERT INTO bots VALUES (?,?,?,?,?,1,?,?)",
                    (bot_id, profile_id, workspace_id, chat_id, request_hash, now, now),
                )
        return self.get(bot_id), replayed

    def _change(
        self, bot_id: str, expected_state_revision: int, *, new_chat: bool
    ) -> dict[str, Any]:
        bot_id, expected = _id(bot_id), _revision(expected_state_revision)
        now = self._stamp()
        require_private_store(self.store)
        with self.store._connection(write=True) as connection:
            connection.row_factory = sqlite3.Row
            row = self._one(connection, bot_id)
            if row["state_revision"] != expected:
                raise BotError("conflict")
            if expected == MAX_INTEGER:
                raise BotError("capacity")
            changed = connection.execute(
                "UPDATE bots SET state_revision=?,chat_session_id=?,updated_at=? "
                "WHERE bot_id=? AND state_revision=?",
                (
                    expected + 1,
                    str(uuid.uuid4()) if new_chat else row["chat_session_id"],
                    now,
                    bot_id,
                    expected,
                ),
            ).rowcount
            if changed != 1:
                raise BotError("conflict")
        return self.get(bot_id)

    def bump_revision(self, bot_id: str, expected_state_revision: int) -> dict[str, Any]:
        return self._change(bot_id, expected_state_revision, new_chat=False)

    def new_chat(self, bot_id: str, expected_state_revision: int) -> dict[str, Any]:
        return self._change(bot_id, expected_state_revision, new_chat=True)

    def _admit_code_folder(self, vault_id: str, workspace_id: str) -> str:
        """The folder's registered identity, after the Bot's vault and the repository checks."""
        try:
            vault = self.workspaces.get(vault_id)
        except WorkspaceError:
            raise BotError("code_folder_needs_ordinary") from None
        if not admits_note_writes(vault):
            raise BotError("code_folder_needs_ordinary")
        if self.code_git is None:
            raise BotError("code_folder_git_unavailable")
        try:
            # Read before Git runs, so a registration changed meanwhile is refused at commit.
            physical_id = self.workspaces.get(workspace_id, kind="code").root.physical_id
            self.code_git.head(self.code_git.repository(workspace_id))
        except WorkspaceError:
            raise BotError("code_folder_missing") from None
        except CodeError as error:
            raise BotError(_CODE_FOLDER_ERRORS.get(error.code, "unavailable")) from None
        return physical_id

    def set_code_folder(
        self, bot_id: str, expected_state_revision: int, workspace_id: str | None
    ) -> dict[str, Any]:
        """Grant one trusted code folder, or none. Existing copies keep their binding."""
        bot_id, expected = _id(bot_id), _revision(expected_state_revision)
        if workspace_id is not None:
            workspace_id = _id(workspace_id)
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = self._one(connection, bot_id)
        if row["state_revision"] != expected:
            raise BotError("conflict")
        physical_id = (
            None
            if workspace_id is None
            else self._admit_code_folder(row["workspace_id"], workspace_id)
        )
        now = self._stamp()
        with self.store._connection(write=True) as connection:
            connection.row_factory = sqlite3.Row
            row = self._one(connection, bot_id)
            if row["state_revision"] != expected:
                raise BotError("conflict")
            if expected == MAX_INTEGER:
                raise BotError("capacity")
            if workspace_id is None:
                connection.execute("DELETE FROM bot_code_folders WHERE bot_id=?", (bot_id,))
            else:
                vault = connection.execute(
                    "SELECT kind,policy,bootstrap FROM workspaces WHERE workspace_id=?",
                    (row["workspace_id"],),
                ).fetchone()
                if vault is None or tuple(vault) != ("vault", "ordinary", "complete"):
                    raise BotError("code_folder_needs_ordinary")
                folder = connection.execute(
                    "SELECT kind,policy,physical_id FROM workspaces WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()
                if folder is None or folder["kind"] != "code":
                    raise BotError("code_folder_missing")
                if folder["policy"] != "ordinary":
                    raise BotError("code_folder_untrusted")
                if folder["physical_id"] != physical_id:
                    raise BotError("code_folder_missing")
                connection.execute(
                    "INSERT OR REPLACE INTO bot_code_folders VALUES (?,?,?)",
                    (bot_id, workspace_id, now),
                )
            connection.execute(
                "UPDATE bots SET state_revision=?,updated_at=? WHERE bot_id=? AND state_revision=?",
                (expected + 1, now, bot_id, expected),
            )
        return self.get(bot_id)

    def _forget(
        self, connection: sqlite3.Connection, rows: list[dict[str, Any]], now: str
    ) -> list[str]:
        if not rows:
            return []
        pairs = {(row["profile_id"], row["workspace_id"]) for row in rows}
        owned = [
            row
            for row in self._routine_rows(connection)
            if row["state"] == "enabled"
            and (row["document"]["profile_id"], row["document"]["workspace_id"]) in pairs
        ]
        for routine in owned:
            if routine["state_revision"] == MAX_INTEGER:
                raise BotError("capacity")
            changed = connection.execute(
                "UPDATE routines SET state='paused',state_revision=state_revision+1,"
                "enabled_since=NULL,updated_at=? WHERE routine_id=? "
                "AND current_revision=? AND state_revision=? AND state='enabled'",
                (
                    now,
                    routine["routine_id"],
                    routine["current_revision"],
                    routine["state_revision"],
                ),
            ).rowcount
            if changed != 1:
                raise BotError("conflict")
        for row in rows:
            connection.execute("DELETE FROM bot_deliveries WHERE bot_id=?", (row["bot_id"],))
            # Every chat copy of this Bot, main and task; a Bot thread's copy stays its group's.
            connection.execute(
                "UPDATE bot_code_copies SET state='released' "
                "WHERE bot_id=? AND state='bound' AND session_id IS NOT NULL",
                (row["bot_id"],),
            )
            connection.execute("DELETE FROM bot_code_folders WHERE bot_id=?", (row["bot_id"],))
            # What the Bot learned from and the proposals it left; approved records stay notes.
            for table in LEARNING_TABLES:
                connection.execute(f"DELETE FROM {table} WHERE bot_id=?", (row["bot_id"],))
            changed = connection.execute(
                "DELETE FROM bots WHERE bot_id=? AND state_revision=?",
                (row["bot_id"], row["state_revision"]),
            ).rowcount
            if changed != 1:
                raise BotError("conflict")
        return [row["routine_id"] for row in owned]

    def remove(self, bot_id: str, expected_state_revision: int) -> dict[str, Any]:
        bot_id, expected = _id(bot_id), _revision(expected_state_revision)
        require_private_store(self.store)
        with self.store._connection(write=True) as connection:
            connection.row_factory = sqlite3.Row
            row = self._one(connection, bot_id)
            if row["state_revision"] != expected:
                raise BotError("conflict")
            paused = self._forget(connection, [row], self._stamp())
        return {"removed": True, "bot_id": bot_id, "paused_routine_ids": paused}

    def reset(self, workspace_id: str) -> dict[str, Any]:
        workspace_id = _id(workspace_id)
        require_private_store(self.store)
        with self.store._connection(write=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = [
                row for row in self._registry(connection) if row["workspace_id"] == workspace_id
            ]
            paused = self._forget(connection, rows, self._stamp())
        return {
            "workspace_id": workspace_id,
            "removed_bot_ids": [row["bot_id"] for row in rows],
            "paused_routine_ids": paused,
        }

    def _attempt_owner(
        self, connection: sqlite3.Connection, attempt_id: str, row: dict[str, Any]
    ) -> None:
        attempt = connection.execute(
            "SELECT a.state,a.outcome,o.routine_id,o.revision,v.format,v.schedule,v.body,"
            "v.body_bytes,v.body_sha256 FROM task_attempts a "
            "LEFT JOIN routine_occurrences o ON o.occurrence_id=a.origin_id "
            "LEFT JOIN routine_revisions v ON v.routine_id=o.routine_id "
            "AND v.revision=o.revision WHERE a.attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if attempt is None:
            raise BotError("not_found")
        if attempt["state"] != "done":
            raise BotError("conflict")
        if attempt["outcome"] is None or attempt["routine_id"] is None or attempt["format"] is None:
            raise BotError("corrupt")
        try:
            document = decode_revision(dict(attempt))
        except RoutineError as error:
            raise BotError(error.code) from None
        if (
            document["profile_id"] != row["profile_id"]
            or document["workspace_id"] != row["workspace_id"]
        ):
            raise BotError("conflict")

    def record_delivery(
        self,
        attempt_id: str,
        bot_id: str,
        *,
        attention: str,
        reason: str,
        state: str,
        delivery_reason: str,
    ) -> bool:
        attempt_id, bot_id = _id(attempt_id), _id(bot_id)
        _delivery_values(attention, reason, state, delivery_reason)
        require_private_store(self.store)
        with self.store._connection(write=True) as connection:
            connection.row_factory = sqlite3.Row
            row = self._one(connection, bot_id)
            self._attempt_owner(connection, attempt_id, row)
            if connection.execute(
                "SELECT 1 FROM bot_deliveries WHERE attempt_id=?", (attempt_id,)
            ).fetchone():
                return False
            now = self._stamp()
            connection.execute(
                "INSERT INTO bot_deliveries VALUES (?,?,?,?,?,?,?,?)",
                (attempt_id, bot_id, attention, reason, state, delivery_reason, now, now),
            )
        return True

    def update_delivery(
        self,
        attempt_id: str,
        *,
        expected_state: str,
        attention: str,
        reason: str,
        state: str,
        delivery_reason: str,
    ) -> bool:
        attempt_id = _id(attempt_id)
        _delivery_values(attention, reason, state, delivery_reason)
        if expected_state not in DELIVERY_STATES:
            raise BotError("invalid_input")
        require_private_store(self.store)
        with self.store._connection(write=True) as connection:
            connection.row_factory = sqlite3.Row
            receipt = connection.execute(
                "SELECT * FROM bot_deliveries WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if receipt is None or receipt["state"] != expected_state:
                return False
            row = self._one(connection, receipt["bot_id"])
            self._attempt_owner(connection, attempt_id, row)
            return (
                connection.execute(
                    "UPDATE bot_deliveries SET attention=?,reason=?,state=?,delivery_reason=?,"
                    "updated_at=? WHERE attempt_id=? AND state=?",
                    (
                        attention,
                        reason,
                        state,
                        delivery_reason,
                        self._stamp(),
                        attempt_id,
                        expected_state,
                    ),
                ).rowcount
                == 1
            )

    def recover_deliveries(self) -> int:
        require_private_store(self.store)
        with self.store._connection(write=True) as connection:
            return connection.execute(
                "UPDATE bot_deliveries SET state='unknown',delivery_reason='send_unknown',"
                "updated_at=? WHERE state='sending'",
                (self._stamp(),),
            ).rowcount

    def _receipt(self, connection: sqlite3.Connection, receipt: sqlite3.Row) -> dict[str, Any]:
        result = dict(receipt)
        _id(result["attempt_id"], code="corrupt")
        _id(result["bot_id"], code="corrupt")
        try:
            _delivery_values(
                result["attention"], result["reason"], result["state"], result["delivery_reason"]
            )
            parse_stamp(result["created_at"])
            parse_stamp(result["updated_at"])
        except ValueError:
            raise BotError("corrupt") from None
        self._attempt_owner(
            connection, result["attempt_id"], self._one(connection, result["bot_id"])
        )
        return result

    def delivery(self, attempt_id: str) -> dict[str, Any] | None:
        attempt_id = _id(attempt_id)
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM bot_deliveries WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            return None if row is None else self._receipt(connection, row)

    def _latest(self, connection: sqlite3.Connection, bot_id: str) -> dict[str, Any] | None:
        receipt = connection.execute(
            "SELECT * FROM bot_deliveries WHERE bot_id=? "
            "ORDER BY created_at DESC,attempt_id DESC LIMIT 1",
            (bot_id,),
        ).fetchone()
        if receipt is None:
            return None
        result = self._receipt(connection, receipt)
        attempt = connection.execute(
            "SELECT a.session_id,a.outcome,o.routine_id FROM task_attempts a "
            "JOIN routine_occurrences o ON o.occurrence_id=a.origin_id WHERE a.attempt_id=?",
            (result["attempt_id"],),
        ).fetchone()
        if attempt is None:
            raise BotError("corrupt")
        _id(attempt["session_id"], code="corrupt")
        return {key: value for key, value in result.items() if key != "bot_id"} | dict(attempt)
