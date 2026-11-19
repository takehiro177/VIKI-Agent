"""Routine definitions, sealed immutable revisions, lifecycle and readiness.

Nothing here starts a process.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import json
import re
import sqlite3
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from viki_agent import protection
from viki_agent.app.settings import SettingsError, SettingsService
from viki_agent.conversations.drafts import require_private_store
from viki_agent.llm.connections import PREFIX
from viki_agent.messaging.bot_memory_rule import bot_system_prompt, kanban_skill_present
from viki_agent.profiles.service import ProfileError, ProfileService
from viki_agent.storage import SessionStore, StateError
from viki_agent.workspaces import WorkspaceError, WorkspaceService

MAX_ROUTINES = 64
MAX_REVISIONS = 256
MAX_RECEIPTS = 65536
MAX_BODY_BYTES = 65536
MAX_INTEGER = (1 << 53) - 1
# Each mode asks first or, in auto, has Claude Code's classifier review shell commands outside
# the read-only set; acceptEdits runs rm, mv, cp, sed on in-scope paths unreviewed, so it stays out.
UNATTENDED_MODES = frozenset({"default", "manual", "plan", "auto"})

_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_SCHEDULE_KEYS = frozenset(
    {"kind", "times", "days", "timezone", "start_date", "end_date", "catch_up"}
)
_DOCUMENT_KEYS = frozenset(
    {"name", "purpose", "prompt", "profile_id", "workspace_id", "deadline_minutes", "schedule"}
)
# Each is omitted at its default, so a document without them seals to the same bytes as before.
_OPTIONAL_KEYS = frozenset({"run_only_if", "on_confirmation"})
_SEAT = re.compile(r"[a-z][a-z0-9-]{0,31}")
MAX_FOLDER_CHARS = 255
# operation -> (the states it leaves, the state it enters)
_TRANSITIONS = {
    "enable": (("tested", "paused"), "enabled"),
    "pause": (("enabled",), "paused"),
    "archive": (("draft", "tested", "enabled", "paused"), "archived"),
}


class RoutineError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...] = ()) -> None:
        self.code = code
        self.blockers = blockers
        super().__init__(code)


def _uuid(value: object) -> str:
    if isinstance(value, str) and len(value) == 36:
        try:
            if str(uuid.UUID(value)) == value:
                return value
        except ValueError:
            pass
    raise RoutineError("invalid_id")


def stamp(value: datetime) -> str:
    """The store's 24-character UTC form ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_stamp(value: object) -> datetime:
    """The exact inverse of `stamp`; anything that is not a 24-character stamp raises."""
    if not isinstance(value, str) or len(value) != 24:
        raise ValueError("invalid_stamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ValueError("invalid_stamp") from None
    if stamp(parsed) != value:
        raise ValueError("invalid_stamp")
    return parsed


def _times(value: object, kind: str) -> list[str]:
    if kind == "manual":
        if value != []:
            raise RoutineError("invalid_schedule")
        return []
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise RoutineError("invalid_schedule")
    for item in value:
        if not isinstance(item, str) or not _TIME_RE.fullmatch(item):
            raise RoutineError("invalid_schedule")
    if len(set(value)) != len(value) or value != sorted(value):
        raise RoutineError("invalid_schedule")
    return list(value)


def _days(value: object, kind: str) -> list[int]:
    if kind != "weekly":
        if value != []:
            raise RoutineError("invalid_schedule")
        return []
    if not isinstance(value, list) or not 1 <= len(value) <= 7:
        raise RoutineError("invalid_schedule")
    for item in value:
        if type(item) is not int or not 1 <= item <= 7:
            raise RoutineError("invalid_schedule")
    if len(set(value)) != len(value) or value != sorted(value):
        raise RoutineError("invalid_schedule")
    return list(value)


@functools.cache
def _iana_keys() -> frozenset[str]:
    return frozenset(available_timezones()) - {"localtime"}


def _timezone(value: object, kind: str) -> str | None:
    if kind == "manual":
        if value is not None:
            raise RoutineError("invalid_schedule")
        return None
    # An OS-only key (localtime, posixrules) can vanish in an OS update; every read re-parses it.
    if not isinstance(value, str) or value not in _iana_keys():
        raise RoutineError("invalid_timezone")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError, ValueError, OSError:
        raise RoutineError("invalid_timezone") from None
    return value


def _sched_date(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RoutineError("invalid_schedule")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise RoutineError("invalid_schedule") from None
    if parsed.isoformat() != value:
        raise RoutineError("invalid_schedule")
    return value


def parse_schedule(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.keys() != _SCHEDULE_KEYS:
        raise RoutineError("invalid_input")
    kind = raw["kind"]
    if kind not in ("manual", "daily", "weekly"):
        raise RoutineError("invalid_schedule")
    times = _times(raw["times"], kind)
    days = _days(raw["days"], kind)
    timezone = _timezone(raw["timezone"], kind)
    start_date = _sched_date(raw["start_date"])
    end_date = _sched_date(raw["end_date"])
    if kind == "manual" and (start_date is not None or end_date is not None):
        raise RoutineError("invalid_schedule")
    if start_date is not None and end_date is not None and end_date < start_date:
        raise RoutineError("invalid_schedule")
    if raw["catch_up"] != "skip":
        raise RoutineError("invalid_schedule")
    return {
        "kind": kind,
        "times": times,
        "days": days,
        "timezone": timezone,
        "start_date": start_date,
        "end_date": end_date,
        "catch_up": "skip",
    }


def schedule_text(schedule: dict[str, Any]) -> str:
    return json.dumps(schedule, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _encoded(value: str) -> bytes:
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise RoutineError("invalid_input") from None


def _name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise RoutineError("invalid_input")
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise RoutineError("invalid_input")
    _encoded(value)
    return value


def _purpose(value: object) -> str:
    if not isinstance(value, str) or not 0 <= len(value) <= 1000:
        raise RoutineError("invalid_input")
    _encoded(value)
    return value


def _prompt(value: object) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise RoutineError("invalid_input")
    length = len(_encoded(value))
    if not 1 <= length <= 32768:
        raise RoutineError("invalid_input")
    return value


def _deadline_minutes(value: object) -> int:
    if type(value) is not int or not 5 <= value <= 240:
        raise RoutineError("invalid_input")
    return value


def _folder(value: object) -> str:
    """A vault-relative folder such as `00-inbox` or `10-projects/site`: no empty, `.` or `..`
    part, no hidden part (the vault listing never shows one, so its notes could never count), no
    backslash, colon or control character."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_FOLDER_CHARS:
        raise RoutineError("invalid_input")
    if "\\" in value or ":" in value or any(unicodedata.category(ch) == "Cc" for ch in value):
        raise RoutineError("invalid_input")
    if any(part == "" or part.startswith(".") for part in value.split("/")):
        raise RoutineError("invalid_input")
    _encoded(value)
    return value


def _run_only_if(value: object) -> dict[str, str]:
    """The scheduled-run condition: a todo card waiting for a seat, or a note changed under a
    folder since the routine's last completed run."""
    if not isinstance(value, dict):
        raise RoutineError("invalid_input")
    if value.keys() == {"kind", "seat"} and value["kind"] == "card":
        seat = value["seat"]
        if not isinstance(seat, str) or not _SEAT.fullmatch(seat):
            raise RoutineError("invalid_input")
        return {"kind": "card", "seat": seat}
    if value.keys() == {"kind", "folder"} and value["kind"] == "changed":
        return {"kind": "changed", "folder": _folder(value["folder"])}
    raise RoutineError("invalid_input")


def parse_document(raw: object) -> tuple[dict[str, Any], bytes]:
    if not (
        isinstance(raw, dict) and _DOCUMENT_KEYS <= raw.keys() <= _DOCUMENT_KEYS | _OPTIONAL_KEYS
    ):
        raise RoutineError("invalid_input")
    document: dict[str, Any] = {
        "name": _name(raw["name"]),
        "purpose": _purpose(raw["purpose"]),
        "prompt": _prompt(raw["prompt"]),
        "profile_id": _uuid(raw["profile_id"]),
        "workspace_id": _uuid(raw["workspace_id"]),
        "deadline_minutes": _deadline_minutes(raw["deadline_minutes"]),
        "schedule": parse_schedule(raw["schedule"]),
    }
    run_only_if = raw.get("run_only_if")
    if run_only_if is not None:
        if document["schedule"]["kind"] == "manual":
            raise RoutineError("invalid_input")
        document["run_only_if"] = _run_only_if(run_only_if)
    on_confirmation = raw.get("on_confirmation", "wait")
    if on_confirmation == "stop":
        document["on_confirmation"] = "stop"
    elif on_confirmation != "wait":
        raise RoutineError("invalid_input")
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(canonical) > MAX_BODY_BYTES:
        raise RoutineError("invalid_input")
    return document, canonical


def _context(routine_id: str, revision: int) -> bytes:
    return f"VIKI routine v1\0{routine_id}\0{revision}".encode("ascii")


def seal(routine_id: str, revision: int, canonical: bytes) -> bytes:
    try:
        sealed = protection.protect(canonical, context=_context(routine_id, revision))
    except protection.ProtectionError:
        raise RoutineError("protection") from None
    if not 1 <= len(sealed) <= 81920:
        raise RoutineError("capacity")
    return sealed


def decode_revision(row: dict[str, Any]) -> dict[str, Any]:
    try:
        if row["format"] != 1:
            raise RoutineError("newer_version")
        context = _context(row["routine_id"], row["revision"])
        try:
            plain = protection.unprotect(row["body"], context=context)
        except protection.ProtectionError:
            raise RoutineError("protection") from None
        if (
            len(plain) != row["body_bytes"]
            or hashlib.sha256(plain).hexdigest() != row["body_sha256"]
        ):
            raise RoutineError("corrupt")
        document, canonical = parse_document(json.loads(plain))
        if canonical != plain:
            raise RoutineError("corrupt")
        if schedule_text(document["schedule"]) != row["schedule"]:
            raise RoutineError("corrupt")
        return document
    except RoutineError as err:
        if err.code in ("newer_version", "protection"):
            raise
        raise RoutineError("corrupt") from None
    except ValueError, TypeError, KeyError:
        raise RoutineError("corrupt") from None


@dataclass(frozen=True)
class RevisionPlan:
    revision: int
    activates_at: str
    schedule: dict[str, Any]


@dataclass(frozen=True)
class RoutinePlan:
    routine_id: str
    state: str
    enabled_since: str | None
    last_slot: str | None
    revisions: tuple[RevisionPlan, ...]


def _integer(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_INTEGER


def _replay(
    connection: sqlite3.Connection, command_id: str, operation: str, request_hash: str
) -> dict[str, Any] | None:
    receipt = connection.execute(
        "SELECT * FROM routine_commands WHERE command_id=?", (command_id,)
    ).fetchone()
    if receipt is None:
        return None
    if receipt["operation"] != operation or receipt["request_hash"] != request_hash:
        raise RoutineError("command_conflict")
    if not all(_integer(receipt[key]) for key in ("result_revision", "result_state_revision")):
        raise RoutineError("corrupt")
    if receipt["result_state"] not in ("draft", "tested", "enabled", "paused", "archived"):
        raise RoutineError("corrupt")
    return {
        "routine_id": receipt["routine_id"],
        "revision": receipt["result_revision"],
        "state": receipt["result_state"],
        "state_revision": receipt["result_state_revision"],
    }


def record_tested(connection: sqlite3.Connection, routine_id: str, revision: int, now: str) -> None:
    """Record a succeeded manual run; only a draft tested at its current revision turns tested."""
    connection.execute(
        "UPDATE routines SET tested_revision=?,"
        "state=CASE WHEN state='draft' AND current_revision=? THEN 'tested' ELSE state END,"
        "state_revision=state_revision+"
        "(CASE WHEN state='draft' AND current_revision=? THEN 1 ELSE 0 END),"
        "updated_at=? WHERE routine_id=? AND state<>'archived'",
        (revision, revision, revision, now, routine_id),
    )


class RoutineService:
    def __init__(
        self,
        store: SessionStore,
        profiles: ProfileService,
        settings: SettingsService | None,
        workspaces: WorkspaceService | None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.profiles = profiles
        self.settings = settings
        self.workspaces = workspaces
        self.now = now

    def create(self, command_id: str, document: object) -> tuple[dict[str, Any], bool]:
        return self._change("create", None, command_id, None, document)

    def revise(
        self, routine_id: str, command_id: str, expected_revision: int, document: object
    ) -> tuple[dict[str, Any], bool]:
        return self._change("revise", routine_id, command_id, expected_revision, document)

    def enable(
        self, routine_id: str, command_id: str, expected_state_revision: int
    ) -> tuple[dict[str, Any], bool]:
        return self._change("enable", routine_id, command_id, None, None, expected_state_revision)

    def pause(
        self, routine_id: str, command_id: str, expected_state_revision: int
    ) -> tuple[dict[str, Any], bool]:
        return self._change("pause", routine_id, command_id, None, None, expected_state_revision)

    def archive(
        self, routine_id: str, command_id: str, expected_state_revision: int
    ) -> tuple[dict[str, Any], bool]:
        return self._change("archive", routine_id, command_id, None, None, expected_state_revision)

    def readiness(self, document: dict[str, Any], *, scheduled: bool) -> list[str]:
        """Blocker codes, in a fixed order; a check whose input is missing is skipped."""
        blockers: list[str] = []
        mode = None
        try:
            profile = self.profiles.get(document["profile_id"])
        except ProfileError as err:
            if err.code != "not_found":
                raise RoutineError("unavailable") from None
            blockers.append("profile_missing")
        else:
            if profile["archived"]:
                blockers.append("profile_archived")
            runtime = profile["document"]["runtime"]
            if runtime["provider"] != "claude":
                blockers.append("provider_unsupported")
            mode = runtime["permission_mode"]
        try:
            workspace = self.workspaces.get(document["workspace_id"]) if self.workspaces else None
        except WorkspaceError:
            workspace = None
        if workspace is None:
            blockers.append("workspace_missing")
        if not scheduled:
            return blockers
        # Unattended writes are proven only in an ordinary root. A scheduled mode must ask
        # first, so the deadline denies what the user's own rules do not already allow, or be
        # auto, where Claude Code's classifier reviews those actions instead.
        if workspace is not None and workspace.policy != "ordinary":
            blockers.append("unattended_policy")
        if mode is None:
            defaults = self._settings()
            if defaults is None:
                blockers.append("settings_unavailable")
                return blockers
            mode = defaults["chat.default_mode"]
        if mode not in UNATTENDED_MODES:
            blockers.append("unattended_mode")
        return blockers

    def _settings(self) -> Mapping[str, object] | None:
        """The effective settings a launch reads, or None when they cannot be trusted."""
        try:
            effective = self.settings.read() if self.settings else None
        except SettingsError:
            return None
        if effective is None or effective.document.read_only:
            return None
        return effective.effective

    def resolve(self, document: dict[str, Any]) -> dict[str, Any]:
        """What a run of `document` would bind now. Reads only.

        The profile's current revision, with a mode or model it leaves unset taken from settings
        as `ProfileLaunchService.prepare` takes it; the workspace; and the first turn
        `TaskRunner._run` composes from the profile's instructions and the prompt. For a profile
        with a Bot in the workspace, as `server.prepare_routine` binds it, the instructions, the
        Bot memory rule and the Bot rules are the system prompt instead
        (`ProfileLaunchService.bot_system_prompt`) and the first turn is the prompt alone.
        """
        try:
            profile = self.profiles.get(document["profile_id"])
        except ProfileError as err:
            if err.code != "not_found":
                raise RoutineError("unavailable") from None
            profile = None
        resolved = first_turn = system_prompt = None
        if profile is not None:
            runtime = profile["document"]["runtime"]
            mode, model = runtime["permission_mode"], runtime["model"]
            connection_pinned = isinstance(model, str) and model.startswith(PREFIX)
            source = "profile" if mode else None
            if connection_pinned:
                model = None
            if not (mode and model) or connection_pinned:
                defaults = self._settings()
                if defaults is not None:
                    source = source or "settings"
                    mode = mode or str(defaults["chat.default_mode"])
                    model = model or (
                        self.settings.native_chat_model(
                            cast(str | None, defaults["chat.default_model"])
                        )
                        if self.settings is not None
                        else None
                    )
            resolved = {
                "profile_id": document["profile_id"],
                "name": profile["document"]["identity"]["name"],
                "revision": profile["revision"],
                "provider": runtime["provider"],
                "model": model,
                "permission_mode": mode,
                "mode_source": source,
                "connection_pinned": connection_pinned,
            }
            instructions, prompt = profile["document"]["instructions"], document["prompt"]
            if self._bot_bound(document["profile_id"], document["workspace_id"]):
                kanban = self._kanban(document["workspace_id"])
                system_prompt, instructions = bot_system_prompt(instructions, kanban=kanban), ""
            first_turn = f"{instructions}\n\n{prompt}" if instructions else prompt
        return {
            "profile": resolved,
            "workspace": self._workspace(document["workspace_id"]),
            "first_turn": first_turn,
            "system_prompt": system_prompt,
            "deadline_minutes": document["deadline_minutes"],
        }

    def _bot_bound(self, profile_id: str, workspace_id: str) -> bool:
        require_private_store(self.store)
        with self.store._connection() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM bots WHERE profile_id=? AND workspace_id=?",
                    (profile_id, workspace_id),
                ).fetchone()
                is not None
            )

    def _kanban(self, workspace_id: str) -> bool:
        """Whether `workspace_id`'s current root has the /kanban skill now, read-only; False
        without a workspace service, an unknown workspace, or a root that no longer resolves."""
        if self.workspaces is None:
            return False
        try:
            workspace = self.workspaces.get(workspace_id)
        except WorkspaceError:
            return False
        root = self.workspaces.current_root(workspace)
        if root is None:
            return False
        return kanban_skill_present(Path(root.display))

    def warnings(self, document: dict[str, Any]) -> list[str]:
        """What would stop a run without blocking enable: a profile session starts only in the
        launch's active workspace (`ProfileLaunchService._workspace`)."""
        workspace = self._workspace(document["workspace_id"])
        return ["workspace_not_active"] if workspace is not None and not workspace["active"] else []

    def _workspace(self, workspace_id: str) -> dict[str, Any] | None:
        if self.workspaces is None:
            return None
        try:
            workspace = self.workspaces.get(workspace_id)
        except WorkspaceError:
            return None
        try:
            active = self.workspaces.resolve_active().workspace.workspace_id == workspace_id
        except WorkspaceError, StateError, protection.ProtectionError:
            active = False
        return {
            "workspace_id": workspace_id,
            "name": workspace.name,
            "policy": workspace.policy,
            "active": active,
        }

    def frozen(self, routine_id: str, revision: int) -> dict[str, Any]:
        routine_id = _uuid(routine_id)
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM routine_revisions WHERE routine_id=? AND revision=?",
                (routine_id, revision),
            ).fetchone()
        if row is None:
            raise RoutineError("not_found")
        return decode_revision(dict(row))

    def plan(self, routine_id: str) -> RoutinePlan:
        found = self._plans("r.routine_id=?", (_uuid(routine_id),))
        if not found:
            raise RoutineError("not_found")
        return found[0]

    def plans(self) -> list[RoutinePlan]:
        return self._plans("r.state='enabled'", ())

    def _plans(self, where: str, values: tuple[str, ...]) -> list[RoutinePlan]:
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT r.routine_id,r.state,r.enabled_since,r.last_slot,v.revision,"
                "v.activates_at,v.schedule FROM routines r JOIN routine_revisions v "
                f"ON v.routine_id=r.routine_id WHERE {where} ORDER BY r.routine_id,v.revision",
                values,
            ).fetchall()
        result = []
        for routine_id, group in itertools.groupby(rows, key=lambda row: row["routine_id"]):
            group = list(group)
            try:
                revisions = tuple(
                    RevisionPlan(
                        row["revision"],
                        row["activates_at"],
                        parse_schedule(json.loads(row["schedule"])),
                    )
                    for row in group
                )
            except ValueError, TypeError:
                raise RoutineError("corrupt") from None
            result.append(
                RoutinePlan(
                    routine_id,
                    group[0]["state"],
                    group[0]["enabled_since"],
                    group[0]["last_slot"],
                    revisions,
                )
            )
        return result

    def get(self, routine_id: str) -> dict[str, Any]:
        routine_id = _uuid(routine_id)
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM routines WHERE routine_id=?", (routine_id,)
            ).fetchone()
            if row is None:
                raise RoutineError("not_found")
            revision = connection.execute(
                "SELECT * FROM routine_revisions WHERE routine_id=? AND revision=?",
                (routine_id, row["current_revision"]),
            ).fetchone()
        if revision is None:
            raise RoutineError("corrupt")
        return {
            **dict(row),
            "revision": revision["revision"],
            "activates_at": revision["activates_at"],
            "document": decode_revision(dict(revision)),
        }

    def list(self) -> list[dict[str, Any]]:
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT r.routine_id,r.state,r.current_revision,r.state_revision,"
                "r.enabled_since,r.last_slot,v.revision,v.format,v.schedule,v.body,"
                "v.body_bytes,v.body_sha256 FROM routines r LEFT JOIN routine_revisions v "
                "ON v.routine_id=r.routine_id AND v.revision=r.current_revision "
                "ORDER BY r.created_at,r.routine_id LIMIT 65"
            ).fetchall()
        if len(rows) > MAX_ROUTINES:
            raise RoutineError("corrupt")
        result = []
        for row in rows:
            if row["revision"] is None:
                raise RoutineError("corrupt")
            document = decode_revision(dict(row))
            result.append(
                {
                    "routine_id": row["routine_id"],
                    "name": document["name"],
                    "state": row["state"],
                    "current_revision": row["current_revision"],
                    "state_revision": row["state_revision"],
                    "schedule": {
                        "kind": document["schedule"]["kind"],
                        "timezone": document["schedule"]["timezone"],
                    },
                    "enabled_since": row["enabled_since"],
                    "last_slot": row["last_slot"],
                }
            )
        return result

    def _change(
        self,
        operation: str,
        routine_id: str | None,
        command_id: str,
        expected_revision: int | None,
        raw: object,
        expected_state_revision: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Create and revise carry a document; the state operations carry only the state CAS."""
        command_id = _uuid(command_id)
        if routine_id is not None:
            routine_id = _uuid(routine_id)
        needed = expected_revision if operation == "revise" else expected_state_revision
        if operation != "create" and not _integer(needed):
            raise RoutineError("invalid_revision")
        document, canonical = (
            parse_document(raw) if operation in ("create", "revise") else (None, None)
        )
        request = json.dumps(
            [
                operation,
                routine_id,
                expected_revision,
                expected_state_revision,
                None if canonical is None else canonical.decode(),
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(request.encode()).hexdigest()
        require_private_store(self.store)
        with self.store._connection() as connection:
            connection.row_factory = sqlite3.Row
            replay = _replay(connection, command_id, operation, request_hash)
            if replay is not None:
                return replay, False
        target = routine_id or str(uuid.uuid4())
        revision = 1 if expected_revision is None else expected_revision + 1
        body = None if canonical is None else seal(target, revision, canonical)
        readied = None  # the revision enable's readiness check read
        if operation == "enable":
            routine = self.get(target)
            readied = routine["current_revision"]
            blockers = self.readiness(routine["document"], scheduled=True)
            if blockers:
                raise RoutineError("not_ready", tuple(blockers))
        moment = self.now()
        now = activates_at = stamp(moment)
        with self.store._connection(write=True) as connection:
            connection.row_factory = sqlite3.Row
            replay = _replay(connection, command_id, operation, request_hash)
            if replay is not None:
                return replay, False
            if connection.execute("SELECT count(*) FROM routine_commands").fetchone()[0] >= (
                MAX_RECEIPTS
            ):
                raise RoutineError("capacity")
            current = connection.execute(
                "SELECT * FROM routines WHERE routine_id=?", (target,)
            ).fetchone()
            if routine_id is None:
                if connection.execute("SELECT count(*) FROM routines").fetchone()[0] >= (
                    MAX_ROUTINES
                ):
                    raise RoutineError("capacity")
                state, state_revision = "draft", 1
                connection.execute(
                    "INSERT INTO routines VALUES (?,1,?,?,NULL,NULL,NULL,?,?)",
                    (target, state, state_revision, now, now),
                )
            elif current is None:
                raise RoutineError("not_found")
            elif expected_state_revision is not None:
                moved = readied is not None and readied != current["current_revision"]
                if current["state_revision"] != expected_state_revision or moved:
                    raise RoutineError("conflict")
                sources, state = _TRANSITIONS[operation]
                if current["state"] not in sources:
                    raise RoutineError("invalid_transition")
                revision = current["current_revision"]
                state_revision = expected_state_revision + 1
                connection.execute(
                    "UPDATE routines SET state=?,state_revision=?,enabled_since=?,updated_at=? "
                    "WHERE routine_id=?",
                    (state, state_revision, now if state == "enabled" else None, now, target),
                )
            else:
                if current["state"] == "archived":
                    raise RoutineError("invalid_transition")
                if current["current_revision"] != expected_revision:
                    raise RoutineError("conflict")
                count, previous = connection.execute(
                    "SELECT count(*),max(CASE WHEN revision=? THEN activates_at END) "
                    "FROM routine_revisions WHERE routine_id=?",
                    (expected_revision, target),
                ).fetchone()
                if count >= MAX_REVISIONS:
                    raise RoutineError("capacity")
                try:
                    floor = parse_stamp(previous) + timedelta(milliseconds=1)
                except ValueError:
                    raise RoutineError("corrupt") from None
                activates_at = stamp(max(moment, floor))
                # A tested routine was tested at its old revision; a new body needs a new test.
                state = "draft" if current["state"] == "tested" else current["state"]
                state_revision = current["state_revision"] + (state != current["state"])
                connection.execute(
                    "UPDATE routines SET current_revision=?,state=?,state_revision=?,updated_at=? "
                    "WHERE routine_id=?",
                    (revision, state, state_revision, now, target),
                )
            if document is not None and canonical is not None:
                connection.execute(
                    "INSERT INTO routine_revisions VALUES (?,?,1,?,?,?,?,?,?)",
                    (
                        target,
                        revision,
                        schedule_text(document["schedule"]),
                        body,
                        len(canonical),
                        hashlib.sha256(canonical).hexdigest(),
                        activates_at,
                        now,
                    ),
                )
            connection.execute(
                "INSERT INTO routine_commands VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,?)",
                (command_id, operation, target, request_hash, revision, state_revision, state, now),
            )
        result = {
            "routine_id": target,
            "revision": revision,
            "state": state,
            "state_revision": state_revision,
        }
        return result, True
