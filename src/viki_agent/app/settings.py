"""The one app-wide settings document: defaults, provider overlay, revisioned
patches and the read-only guard for a document a newer or damaged app left behind.

This module never touches the vault or `~/.claude`: `provider_defaults` is an injected read of
the CLI's own merged settings, and it writes only the app's own SQLite `settings` row -- a patch
naming an avatar also reads the avatar rows, through `profiles.avatar_committed`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime

from viki_agent.domain import SETTINGS_VERSION as SETTINGS_VERSION
from viki_agent.domain import EffectiveSettings, SettingsDocument, SettingSource, SettingSpec
from viki_agent.drivers.claude.control import EFFORTS, PERMISSION_MODES
from viki_agent.profiles import service as profiles
from viki_agent.state import Prefs
from viki_agent.storage import CommandReceipt, SessionStore, SettingsRow, StateError
from viki_agent.voice import pack_voices, voice_models
from viki_agent.voice.voicebox_http import DEFAULT_URL, MODEL_SIZES, loopback

MAX_DOCUMENT_BYTES = 65536  # matches storage._SETTINGS_SCHEMA's `document` CHECK upper bound
MAX_CHAT_WORKSPACES = 7  # chat.workspace_ids: ADR-050's cap on ticked workspaces per chat
# chat.max_live_sessions is read at every admission: SettingsService.max_live_sessions() caches
# its result for this long so that hot path skips the store. A module-level name so tests can
# monkeypatch the clock.
MAX_LIVE_CACHE_S = 1.0
_monotonic = time.monotonic

_MESSAGES = {
    "invalid_settings": "Check the highlighted settings.",
    "read_only": "Settings are read-only; see the recovery guidance.",
    "command_conflict": "This request was already processed with different content. "
    "Review the current values.",
    "stale_revision": "Changed elsewhere; review the current values.",
    "settings_too_large": "The settings data is too large. Reduce the content, then save.",
}

# Every non-null string a key can hold, keyed by SettingSpec.kind; the callable's `min_len` is
# 1 for the three keys below and 0 for every other string key (there is no min_len on SettingSpec,
# so it stays local to the one place that needs it).
_NONEMPTY_STRING_KEYS = frozenset(
    {"identity.assistant_name", "chat.default_model", "voice.voicebox_url"}
)
# Stored as a canonical lower-case UUID (an avatar id) or null; checked in `_validate_change`.
_UUID_KEYS = frozenset({"identity.user_avatar_id", "identity.assistant_avatar_id"})
# Opaque strings only the frontend parses: kept exactly as sent (no NFC normalisation) so the
# ids and refs inside them round-trip byte-for-byte; checked in `_validate_change`.
_KEEP_AS_SENT = frozenset({"voice.read_aloud_voices", "layout.panes"})
_CHAT_FIELDS = {
    "chat.default_mode": "mode",
    "chat.default_effort": "effort",
    "chat.default_model": "model",
}
_RESET_PATCH_HASH = hashlib.sha256(b"VIKI settings reset v1").hexdigest()
# decision.paused_until/decision.focus_until: checked in `_validate_change` by `_valid_instant`.
_INSTANT_KEYS = frozenset({"decision.paused_until", "decision.focus_until"})
# layout.launcher/layout.launcher_panel: "R,B"/"W,H" pixel pairs, checked in `_validate_change`.
_PIXEL_PAIR_KEYS = frozenset({"layout.launcher", "layout.launcher_panel"})
# decision.never_send: the stored list's own caps (contracts.py's parse_settings carries the
# same two numbers independently; checked in `_never_send_list`).
_NEVER_SEND_ENTRIES = 32
_NEVER_SEND_CHARS = 128
# decision.never_send only: a stored value `_validate_change` can no longer use must still read
# as "set", never the permissive default "[]" -- this sentinel is not valid JSON, so
# `contracts.parse_settings` reports `never_send_invalid` and the engine sends nothing (fails
# closed, never open); checked in `_stored_value`.
_NEVER_SEND_UNREADABLE = "!unreadable"
# bots.hues: the closed set of selectable Bot colours (`_bot_hues`); checked in `_validate_change`.
_BOT_HUES = frozenset({"blue", "green", "orange", "purple", "red", "teal", "amber", "pink"})
# bots.icons: the closed set of Bot emblems in botIconDesign.ts (`_bot_icons`).
_BOT_ICONS = frozenset(
    {
        "door",
        "loop",
        "check",
        "lens",
        "nib",
        "wrench",
        "tray",
        "bolt",
        "star",
        "heart",
        "leaf",
        "bell",
        "note",
        "bubble",
        "key",
        "flag",
        "flask",
        "shield",
        "globe",
        "pin",
        "cloud",
        "book",
        "chart",
        "spark",
        "moon",
        "gear",
        "target",
    }
)


def _spec(
    key: str,
    kind: str,
    allowed: tuple[object, ...] | None,
    default: object,
    applies: str,
    personal: bool,
    max_len: int = 0,
    bounds: tuple[int, int] | None = None,
) -> SettingSpec:
    return SettingSpec(
        key=key,
        scope="app",
        kind=kind,  # type: ignore[arg-type]
        allowed=allowed,
        default=default,
        applies=applies,  # type: ignore[arg-type]
        personal=personal,
        max_len=max_len,
        bounds=bounds,
    )


QUIET_TIMES = tuple(f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30))

# Document version 1: every key has scope "app".
KEYS: Mapping[str, SettingSpec] = {
    "appearance.theme": _spec(
        "appearance.theme", "enum", ("dark", "light", "system"), "system", "now", False
    ),
    "appearance.density": _spec(
        "appearance.density", "enum", ("comfortable", "compact"), "comfortable", "now", False
    ),
    "accessibility.font_scale": _spec(
        "accessibility.font_scale", "number", (0.85, 1, 1.15, 1.3, 1.5), 1, "now", False
    ),
    "accessibility.reduce_motion": _spec(
        "accessibility.reduce_motion", "enum", ("allow", "reduce", "system"), "system", "now", False
    ),
    "identity.display_name": _spec(
        "identity.display_name", "string", None, "", "now", True, max_len=64
    ),
    "identity.assistant_name": _spec(
        "identity.assistant_name", "string", None, "VIKI", "now", True, max_len=32
    ),
    "identity.user_avatar_id": _spec(
        "identity.user_avatar_id", "string", None, None, "now", True, max_len=36
    ),
    "identity.assistant_avatar_id": _spec(
        "identity.assistant_avatar_id", "string", None, None, "now", True, max_len=36
    ),
    "privacy.hide_previews": _spec("privacy.hide_previews", "bool", None, False, "now", False),
    "privacy.diagnostics_include_paths": _spec(
        "privacy.diagnostics_include_paths", "bool", None, False, "now", False
    ),
    "chat.default_mode": _spec(
        "chat.default_mode", "enum", tuple(sorted(PERMISSION_MODES)), None, "next_session", False
    ),
    "chat.default_effort": _spec(
        "chat.default_effort", "enum", tuple(sorted(EFFORTS)), None, "next_session", False
    ),
    "chat.default_model": _spec(
        "chat.default_model", "string", None, None, "next_session", False, max_len=1024
    ),
    "chat.workspace_ids": _spec(
        "chat.workspace_ids", "string", None, "[]", "next_session", True, max_len=512
    ),
    "chat.llm_connections": _spec(
        "chat.llm_connections", "string", None, None, "next_session", True, max_len=60000
    ),
    # Read once at launch (`__main__._other_sign_in`): lets native sessions start on any sign-in
    # Claude Code is set up with, not only a Claude subscription. On by default, because an app
    # that runs Claude Code may not restrict the sign-in methods built into it.
    "chat.other_sign_in": _spec("chat.other_sign_in", "bool", None, True, "next_launch", True),
    # Read at every admission (`SessionManager.bind_max_live`).
    "chat.max_live_sessions": _spec(
        "chat.max_live_sessions", "number", None, 30, "now", False, bounds=(1, 999)
    ),
    "onboarding.completed": _spec("onboarding.completed", "bool", None, False, "now", False),
    "routines.paused": _spec("routines.paused", "bool", None, False, "now", False),
    "notifications.enabled": _spec("notifications.enabled", "bool", None, False, "now", False),
    "notifications.quiet_start": _spec(
        "notifications.quiet_start", "enum", QUIET_TIMES, None, "now", False
    ),
    "notifications.quiet_end": _spec(
        "notifications.quiet_end", "enum", QUIET_TIMES, None, "now", False
    ),
    "layout.left_rail_width": _spec(
        "layout.left_rail_width", "number", tuple(range(220, 481)), 272, "now", False
    ),
    "layout.right_rail_width": _spec(
        "layout.right_rail_width", "number", tuple(range(240, 561)), 320, "now", False
    ),
    # Opaque layout the frontend parses; kept as sent so refs round-trip. Personal: the chat ids
    # and refs it holds are machine-local.
    "layout.panes": _spec("layout.panes", "string", None, "", "now", True, max_len=8192),
    "workbench.code_font_size": _spec(
        "workbench.code_font_size", "number", tuple(range(11, 21)), 13, "now", False
    ),
    "workbench.line_wrap": _spec("workbench.line_wrap", "bool", None, False, "now", False),
    "workbench.worktree_cleanup": _spec(
        "workbench.worktree_cleanup", "bool", None, True, "now", False
    ),
    "voice.read_aloud_rate": _spec(
        "voice.read_aloud_rate", "number", (0.75, 1, 1.25, 1.5, 2), 1, "now", False
    ),
    "voice.read_aloud_voices": _spec(
        "voice.read_aloud_voices", "string", None, "{}", "now", False, max_len=4096
    ),
    "voice.read_aloud_notice_seen": _spec(
        "voice.read_aloud_notice_seen", "bool", None, False, "now", False
    ),
    "voice.dictation_language": _spec(
        "voice.dictation_language",
        "enum",
        voice_models.DICTATION_LANGUAGES,
        "auto",
        "now",
        False,
    ),
    "voice.pause_s": _spec("voice.pause_s", "number", (1.2, 2, 3, 4, 5), 3, "now", False),
    "voice.model": _spec(
        "voice.model", "enum", ("auto", *voice_models.MODELS), "auto", "now", False
    ),
    "voice.backend": _spec(
        "voice.backend", "enum", ("auto", "cpu", "cuda", "metal"), "auto", "now", False
    ),
    "voice.speech_engine": _spec(
        "voice.speech_engine", "enum", ("system", "pack", "voicebox"), None, "now", False
    ),
    "voice.pack_voices": _spec(
        "voice.pack_voices", "string", None, "{}", "now", False, max_len=512
    ),
    "voice.voicebox_profile": _spec(
        "voice.voicebox_profile", "string", None, None, "now", False, max_len=36
    ),
    "voice.voicebox_model_size": _spec(
        "voice.voicebox_model_size", "enum", MODEL_SIZES, "0.6B", "now", False
    ),
    "voice.voicebox_url": _spec(
        "voice.voicebox_url", "string", None, DEFAULT_URL, "now", False, max_len=32
    ),
    "voice.speak_while_writing": _spec(
        "voice.speak_while_writing", "bool", None, False, "now", False
    ),
    # Decision loop settings: hidden on Settings pages; the frontend implements that.
    "decision.enabled": _spec("decision.enabled", "bool", None, False, "now", False),
    # "" or an exact 24-character `YYYY-MM-DDTHH:MM:SS.mmmZ` UTC stamp (`_valid_instant`).
    "decision.paused_until": _spec(
        "decision.paused_until", "string", None, "", "now", False, max_len=24
    ),
    "decision.focus_until": _spec(
        "decision.focus_until", "string", None, "", "now", False, max_len=24
    ),
    "decision.send_level": _spec(
        "decision.send_level",
        "enum",
        ("titles", "excerpts", "sections"),
        "excerpts",
        "now",
        False,
    ),
    # JSON array of at most _NEVER_SEND_ENTRIES strings of at most _NEVER_SEND_CHARS characters:
    # 32*(128+2) + 31 commas + 2 brackets = 4193, the exact worst case; stored canonical.
    "decision.never_send": _spec(
        "decision.never_send", "string", None, "[]", "now", False, max_len=4193
    ),
    "decision.watch.tasks": _spec("decision.watch.tasks", "bool", None, True, "now", False),
    "decision.watch.inbox": _spec("decision.watch.inbox", "bool", None, True, "now", False),
    "decision.watch.vault_changes": _spec(
        "decision.watch.vault_changes", "bool", None, True, "now", False
    ),
    "decision.watch.approvals": _spec("decision.watch.approvals", "bool", None, True, "now", False),
    "decision.watch.chats": _spec("decision.watch.chats", "bool", None, True, "now", False),
    "decision.watch.routines": _spec("decision.watch.routines", "bool", None, True, "now", False),
    "decision.watch.improve": _spec("decision.watch.improve", "bool", None, True, "now", False),
    "decision.watch.kanban": _spec("decision.watch.kanban", "bool", None, True, "now", False),
    "decision.watch.vault_health": _spec(
        "decision.watch.vault_health", "bool", None, True, "now", False
    ),
    "decision.watch.messaging": _spec("decision.watch.messaging", "bool", None, True, "now", False),
    "decision.cadence": _spec(
        "decision.cadence", "enum", ("change", "5m", "15m"), "change", "now", False
    ),
    "decision.daily_cap_usd": _spec(
        "decision.daily_cap_usd", "number", (0.1, 0.25, 0.5, 1, 2), 0.5, "now", False
    ),
    "decision.nudges_per_hour": _spec(
        "decision.nudges_per_hour", "number", (0, 1, 2), 2, "now", False
    ),
    "decision.alerts": _spec("decision.alerts", "bool", None, False, "now", False),
    "decision.in_context": _spec("decision.in_context", "bool", None, True, "now", False),
    "decision.model_version": _spec(
        "decision.model_version",
        "enum",
        ("pinned", "latest", "preview"),
        "pinned",
        "now",
        False,
    ),
    "decision.grant.prepare": _spec(
        "decision.grant.prepare", "enum", ("off", "ask", "allowed"), "allowed", "now", False
    ),
    "decision.grant.run": _spec(
        "decision.grant.run", "enum", ("off", "ask", "allowed"), "ask", "now", False
    ),
    "decision.grant.team": _spec(
        "decision.grant.team", "enum", ("off", "ask", "allowed"), "ask", "now", False
    ),
    "decision.grant.look": _spec(
        "decision.grant.look", "enum", ("off", "ask", "allowed"), "allowed", "now", False
    ),
    "decision.grant.second_look": _spec(
        "decision.grant.second_look", "enum", ("off", "ask", "allowed"), "allowed", "now", False
    ),
    "decision.grant.project_plan": _spec(
        "decision.grant.project_plan", "enum", ("off", "ask", "allowed"), "allowed", "now", False
    ),
    "decision.grant.check": _spec(
        "decision.grant.check", "enum", ("off", "ask", "allowed"), "ask", "now", False
    ),
    "decision.grant.inbox_triage": _spec(
        "decision.grant.inbox_triage", "enum", ("off", "ask", "allowed"), "allowed", "now", False
    ),
    "decision.limit.drafts": _spec(
        "decision.limit.drafts", "number", tuple(range(11)), 5, "now", False
    ),
    "decision.limit.chats": _spec(
        "decision.limit.chats", "number", tuple(range(11)), 3, "now", False
    ),
    "decision.limit.changes": _spec(
        "decision.limit.changes", "number", tuple(range(11)), 3, "now", False
    ),
    # Percent of a plan window's use at which VIKI holds its own starts.
    "decision.plan_brake": _spec(
        "decision.plan_brake", "enum", ("off", "50", "80", "95"), "80", "now", False
    ),
    "decision.limit.team": _spec(
        "decision.limit.team", "number", tuple(range(11)), 4, "now", False
    ),
    "decision.team.handoffs": _spec("decision.team.handoffs", "bool", None, False, "now", False),
    "decision.limit.look": _spec(
        "decision.limit.look", "number", tuple(range(11)), 3, "now", False
    ),
    "decision.limit.second_look": _spec(
        "decision.limit.second_look", "number", tuple(range(11)), 3, "now", False
    ),
    "decision.limit.project_plan": _spec(
        "decision.limit.project_plan", "number", tuple(range(11)), 3, "now", False
    ),
    "decision.limit.message_draft": _spec(
        "decision.limit.message_draft", "number", tuple(range(11)), 3, "now", False
    ),
    "decision.limit.check": _spec(
        "decision.limit.check", "number", tuple(range(11)), 3, "now", False
    ),
    "decision.limit.inbox_triage": _spec(
        "decision.limit.inbox_triage", "number", tuple(range(11)), 3, "now", False
    ),
    "decision.learning": _spec("decision.learning", "bool", None, True, "now", False),
    "decision.shadow": _spec(
        "decision.shadow",
        "enum",
        ("off", "pinned", "latest", "preview"),
        "off",
        "now",
        False,
    ),
    # "" or a canonical lower-case UUID; personal (machine-local), like the identity avatar ids,
    # but never folded into `_UUID_KEYS`: a launcher session names no avatar.
    "decision.launcher_session": _spec(
        "decision.launcher_session", "string", None, "", "now", True, max_len=36
    ),
    # "R,B" pixels from the bottom-right corner (each 0..9999); not read by the Decision loop.
    "layout.launcher": _spec("layout.launcher", "string", None, "", "now", False, max_len=9),
    # "W,H" pixels, VIKI's panel size (each 0..9999); "" is the 380x480 default; not read by
    # the Decision loop.
    "layout.launcher_panel": _spec(
        "layout.launcher_panel", "string", None, "", "now", False, max_len=9
    ),
    # Profile id -> Bot colour; personal, machine-local ids.
    "bots.hues": _spec("bots.hues", "string", None, "{}", "now", True, max_len=4096),
    # Profile id -> Bot emblem; personal, machine-local ids.
    "bots.icons": _spec("bots.icons", "string", None, "{}", "now", True, max_len=4096),
}

# Keys an earlier version stored and this one no longer has: `locale.language` chose the UI
# language, and the UI is English only. A stored document or a settings export may still carry
# one. Reading drops it, so it is never applied, shown or exported, and the next saved change
# leaves it out of the stored document; an import skips it without a preview row.
RETIRED_KEYS = frozenset({"locale.language"})


def identity_avatar_ids(document: str) -> frozenset[str] | None:
    """The avatar ids the identity settings name, or None for an unparseable document
    (an unreadable document proves nothing about what it keeps alive, so callers keep everything
    rather than delete on a guess)."""
    values = _safe_parse(document)
    if values is None:
        return None
    ids: set[str] = set()
    for key in _UUID_KEYS:
        if isinstance(stored := _override(values, key), str):
            ids.add(stored)
    return frozenset(ids)


class SettingsError(RuntimeError):
    """A stable, safe service/API error; caller-supplied content never reaches its text."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        fields: Mapping[str, str] | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message if message is not None else _MESSAGES.get(code, code)
        self.fields = dict(fields) if fields else {}
        self.extra = dict(extra) if extra else {}
        super().__init__(self.message)


class SettingsConflict(SettingsError):
    def __init__(self, current_revision: int, current: EffectiveSettings) -> None:
        self.current_revision = current_revision
        self.current = current
        super().__init__("stale_revision")


class _FieldError(Exception):
    """Internal signal for one invalid change; `patch` collects these into `fields`."""

    def __init__(self, code: str) -> None:
        self.code = code


class _StaleSignal(Exception):
    """Internal signal carrying the row `patch` saw when `expected_revision` did not match.

    Raised inside the writer transaction (the comparison is decided there); the expensive part
    (reading `keys`/`effective` needs `provider_defaults`, which may touch the filesystem) is
    assembled by `patch` after the transaction has closed, never while holding the write lock.
    """

    def __init__(self, document: SettingsDocument) -> None:
        self.document = document


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _refuse_constant(token: str) -> None:
    raise ValueError(token)


def _no_repeated_key(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("repeated key")
    return result


def _voice_map(text: str) -> str:
    """The canonical JSON of a language -> voice URI map, or `_FieldError`."""
    try:
        parsed = json.loads(
            text, parse_constant=_refuse_constant, object_pairs_hook=_no_repeated_key
        )
    except ValueError, RecursionError:
        raise _FieldError("not_allowed") from None
    if not isinstance(parsed, dict) or len(parsed) > 32:
        raise _FieldError("not_allowed")
    for language, voice in parsed.items():
        if (
            not re.fullmatch(r"[a-z]{2,3}", language)
            or not isinstance(voice, str)
            or not 1 <= len(voice) <= 256
            or any(unicodedata.category(ch) == "Cc" for ch in voice)
        ):
            raise _FieldError("not_allowed")
    canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(canonical) > KEYS["voice.read_aloud_voices"].max_len:
        raise _FieldError("too_long")
    return canonical


def _bot_hues(text: str) -> str:
    """The canonical JSON of a profile id -> Bot hue map, or `_FieldError`."""
    try:
        parsed = json.loads(
            text, parse_constant=_refuse_constant, object_pairs_hook=_no_repeated_key
        )
    except ValueError, RecursionError:
        raise _FieldError("not_allowed") from None
    if not isinstance(parsed, dict) or len(parsed) > 64:
        raise _FieldError("not_allowed")
    for profile_id, hue in parsed.items():
        try:
            canonical = str(uuid.UUID(profile_id)) == profile_id
        except ValueError:
            canonical = False
        if not canonical or not isinstance(hue, str) or hue not in _BOT_HUES:
            raise _FieldError("not_allowed")
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    if len(canonical) > KEYS["bots.hues"].max_len:
        raise _FieldError("too_long")
    return canonical


def _bot_icons(text: str) -> str:
    """The canonical JSON of a profile id -> Bot emblem map, or `_FieldError`."""
    try:
        parsed = json.loads(
            text, parse_constant=_refuse_constant, object_pairs_hook=_no_repeated_key
        )
    except ValueError, RecursionError:
        raise _FieldError("not_allowed") from None
    if not isinstance(parsed, dict) or len(parsed) > 64:
        raise _FieldError("not_allowed")
    for profile_id, icon in parsed.items():
        try:
            canonical = str(uuid.UUID(profile_id)) == profile_id
        except ValueError:
            canonical = False
        if not canonical or not isinstance(icon, str) or icon not in _BOT_ICONS:
            raise _FieldError("not_allowed")
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    if len(canonical) > KEYS["bots.icons"].max_len:
        raise _FieldError("too_long")
    return canonical


def _workspace_id_list(text: str) -> str:
    """The canonical JSON of a `chat.workspace_ids` value (ADR-050): a JSON array of at most
    MAX_CHAT_WORKSPACES unique canonical-UUID strings, stored sorted. Raises `_FieldError`
    otherwise."""
    try:
        parsed = json.loads(text, parse_constant=_refuse_constant)
    except ValueError, RecursionError:
        raise _FieldError("not_allowed") from None
    if (
        not isinstance(parsed, list)
        or len(parsed) > MAX_CHAT_WORKSPACES
        or not all(isinstance(item, str) for item in parsed)
    ):
        raise _FieldError("not_allowed")
    for item in parsed:
        try:
            canonical = str(uuid.UUID(item)) == item
        except ValueError:
            canonical = False
        if not canonical:
            raise _FieldError("not_allowed")
    if len(set(parsed)) != len(parsed):
        raise _FieldError("not_allowed")
    return json.dumps(sorted(parsed), separators=(",", ":"))


def _valid_instant(value: str) -> bool:
    """Whether `value` is an exact 24-character `YYYY-MM-DDTHH:MM:SS.mmmZ` UTC stamp (`_now`'s
    own format; the same check as `decision_loop.contracts._instant`, kept local here)."""
    if len(value) != 24 or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") == value


def _never_send_folder(entry: str) -> bool:
    """A vault-relative folder: "/"-separated segments, ending "/", no leading "/" and no ".."
    segment."""
    if entry.startswith("/") or not entry.endswith("/"):
        return False
    segments = entry[:-1].split("/")
    return all(segment and segment != ".." for segment in segments)


def _never_send_tag(entry: str) -> bool:
    """A "#" followed by a non-empty tag with no whitespace."""
    tag = entry.removeprefix("#")
    return tag != entry and tag != "" and not any(ch.isspace() for ch in tag)


def _never_send_list(text: str) -> str:
    """The canonical JSON of a `decision.never_send` value: a JSON array of at most
    `_NEVER_SEND_ENTRIES` strings, each 1 to `_NEVER_SEND_CHARS` characters, a folder or a tag
    (`_never_send_folder`/`_never_send_tag`), with no control characters. Duplicates are dropped
    and order is kept. Raises `_FieldError` otherwise."""
    try:
        parsed = json.loads(text, parse_constant=_refuse_constant)
    except ValueError, RecursionError:
        raise _FieldError("not_allowed") from None
    if not isinstance(parsed, list) or len(parsed) > _NEVER_SEND_ENTRIES:
        raise _FieldError("not_allowed")
    deduped: list[str] = []
    seen: set[str] = set()
    for entry in parsed:
        if (
            not isinstance(entry, str)
            or not 1 <= len(entry) <= _NEVER_SEND_CHARS
            or any(unicodedata.category(ch) == "Cc" for ch in entry)
            or not (_never_send_folder(entry) or _never_send_tag(entry))
        ):
            raise _FieldError("not_allowed")
        if entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    canonical = json.dumps(deduped, ensure_ascii=False, separators=(",", ":"))
    if len(canonical) > KEYS["decision.never_send"].max_len:
        raise _FieldError("too_long")
    return canonical


def _safe_parse(document: str) -> dict[str, object] | None:
    try:
        data = json.loads(document, parse_constant=_refuse_constant)
        # the writers dump allow_nan=False; a NaN/Infinity or depth would raise on any patch
        # that keeps it
        json.dumps(data, allow_nan=False)
    # json.loads raises RecursionError, not ValueError, on JSON nested too deep.
    except ValueError, RecursionError:
        return None
    return data if isinstance(data, dict) else None


def _parse_row(row: SettingsRow | None) -> tuple[dict[str, object] | None, bool, str | None]:
    """The row's stored values, without `RETIRED_KEYS`, plus the read-only guard.

    `newer_version` wins over an incidental parse failure: a document a future app wrote is
    untrusted regardless of whether this app's own JSON decoder happens to choke on it.
    """
    if row is None:
        return {}, False, None
    parsed = _safe_parse(row["document"])
    if parsed is not None:
        parsed = {key: value for key, value in parsed.items() if key not in RETIRED_KEYS}
    if row["version"] > SETTINGS_VERSION:
        return parsed, True, "newer_version"
    if parsed is None:
        return None, True, "corrupt"
    return parsed, False, None


def _document_from_row(row: SettingsRow | None) -> SettingsDocument:
    values, read_only, reason = _parse_row(row)
    return SettingsDocument(
        version=row["version"] if row is not None else SETTINGS_VERSION,
        revision=row["revision"] if row is not None else 0,
        values=values,
        read_only=read_only,
        reason=reason,  # type: ignore[arg-type]
    )


def _serialize(values: Mapping[str, object]) -> str:
    # ensure_ascii=True keeps the stored text 7-bit ASCII, so its Python `len()`, its UTF-8 byte
    # count and SQLite's own `length()` (character-counted) all agree with MAX_DOCUMENT_BYTES.
    return json.dumps(
        dict(values), ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _merge(values: Mapping[str, object], changes: Mapping[str, object | None]) -> dict[str, object]:
    merged = dict(values)
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)  # null removes the key: reset to the inherited value
        else:
            merged[key] = value
    return merged


def _validate_change(key: str, value: object) -> object | None:
    """The normalised value to store, or None for a reset. Raises `_FieldError` otherwise."""
    spec = KEYS.get(key)
    if spec is None:
        raise _FieldError("unknown_key")
    if value is None:
        return None
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise _FieldError("invalid_type")
        return value
    if spec.kind == "enum":
        if not isinstance(value, str):
            raise _FieldError("invalid_type")
        if spec.allowed is None or value not in spec.allowed:
            raise _FieldError("not_allowed")
        return value
    if spec.kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _FieldError("invalid_type")
        if spec.bounds is not None:
            if type(value) is not int:
                raise _FieldError("invalid_type")
            minimum, maximum = spec.bounds
            if not minimum <= value <= maximum:
                raise _FieldError("not_allowed")
            return value
        if spec.allowed is None or not any(value == candidate for candidate in spec.allowed):
            raise _FieldError("not_allowed")
        return value
    if spec.kind == "string":
        if not isinstance(value, str):
            raise _FieldError("invalid_type")
        # A voice URI or a layout.panes ref is an opaque id matched by exact equality.
        keep_as_sent = key in _KEEP_AS_SENT or (
            key == "chat.default_model" and value.startswith("viki-llm/")
        )
        normalized = value if keep_as_sent else unicodedata.normalize("NFC", value)
        if any(unicodedata.category(ch) == "Cc" for ch in normalized):
            raise _FieldError("control_characters")
        length = len(normalized.encode("utf-8")) if key == "chat.default_model" else len(normalized)
        minimum = 1 if key in _NONEMPTY_STRING_KEYS else 0
        if not minimum <= length <= spec.max_len:
            raise _FieldError("too_long")
        if key in _UUID_KEYS:
            try:
                canonical = str(uuid.UUID(normalized)) == normalized
            except ValueError:
                canonical = False
            if not canonical:
                raise _FieldError("not_allowed")
        # Its own check, never folded into _UUID_KEYS: that set also drives patch()'s
        # avatar_committed lookup, and a Voicebox profile id names no avatar.
        if key == "voice.voicebox_profile":
            try:
                canonical = str(uuid.UUID(normalized)) == normalized
            except ValueError:
                canonical = False
            if not canonical:
                raise _FieldError("not_allowed")
        if key == "voice.voicebox_url" and loopback(normalized) is None:
            raise _FieldError("not_allowed")
        if key in _INSTANT_KEYS and normalized != "" and not _valid_instant(normalized):
            raise _FieldError("not_allowed")
        if key == "decision.launcher_session" and normalized != "":
            try:
                canonical = str(uuid.UUID(normalized)) == normalized
            except ValueError:
                canonical = False
            if not canonical:
                raise _FieldError("not_allowed")
        if (
            key in _PIXEL_PAIR_KEYS
            and normalized != ""
            and not re.fullmatch(r"\d{1,4},\d{1,4}", normalized)
        ):
            raise _FieldError("not_allowed")
        if key == "voice.read_aloud_voices":
            return _voice_map(normalized)
        if key == "bots.hues":
            return _bot_hues(normalized)
        if key == "bots.icons":
            return _bot_icons(normalized)
        if key == "voice.pack_voices":
            parsed = pack_voices.parse(normalized)
            if parsed is None:
                raise _FieldError("not_allowed")
            return json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        if key == "chat.workspace_ids":
            return _workspace_id_list(normalized)
        if key == "decision.never_send":
            return _never_send_list(normalized)
        return normalized
    raise _FieldError("invalid_type")  # pragma: no cover - every KEYS entry has a known kind


def check_change(key: str, value: object) -> str | None:
    """The field code `_validate_change` refuses `value` for `key` with, or None when valid."""
    try:
        _validate_change(key, value)
    except _FieldError as exc:
        return exc.code
    return None


def _stored_value(key: str, raw: object) -> object | None:
    try:
        return _validate_change(key, raw)
    except _FieldError:
        if key == "decision.never_send":
            return _NEVER_SEND_UNREADABLE
        return None  # a value the current schema cannot use is treated as not set, never a crash


def _override(values: Mapping[str, object] | None, key: str) -> object | None:
    if values is None or key not in values:
        return None
    return _stored_value(key, values[key])


def _command_id(value: object) -> str:
    if isinstance(value, str) and len(value) == 36:
        try:
            if str(uuid.UUID(value)) == value:
                return value
        except ValueError:
            pass
    raise SettingsError("invalid_settings", fields={"command_id": "invalid_type"})


def _patch_hash(expected_revision: int, changes: Mapping[str, object | None]) -> str:
    canonical = json.dumps(
        {"expected_revision": expected_revision, "changes": changes},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _max_live_default() -> int:
    """`chat.max_live_sessions`'s registered default, narrowed from `SettingSpec.default`'s
    `object` type."""
    default = KEYS["chat.max_live_sessions"].default
    return default if isinstance(default, int) else 30


class SettingsService:
    def __init__(
        self,
        store: SessionStore,
        *,
        provider_defaults: Callable[[], Prefs],
        on_chat_defaults: Callable[[Prefs], None],
    ) -> None:
        self._store = store
        self._provider_defaults = provider_defaults
        self._on_chat_defaults = on_chat_defaults
        # chat.max_live_sessions: the last value max_live_sessions() read, or the default
        # before its first read.
        self._last_max_live = _max_live_default()
        # When that read happened (_monotonic() time), or None before the first read or right
        # after a committed patch()/reset(): max_live_sessions()'s cache.
        self._max_live_cached_at: float | None = None

    @contextlib.contextmanager
    def _access(self) -> Iterator[None]:
        try:
            yield
        except StateError as exc:
            raise SettingsError(exc.code, exc.message) from None

    def _effective(
        self, values: Mapping[str, object] | None, *, read_only: bool
    ) -> tuple[dict[str, object], dict[str, SettingSource]]:
        # A read-only document (newer or corrupt) never contributes a "setting" layer: the
        # session and the GET response must agree that nothing here was actually applied.
        baseline = Prefs()
        provider: Prefs | None = None
        effective: dict[str, object] = {}
        sources: dict[str, SettingSource] = {}
        for key, spec in KEYS.items():
            stored = None if read_only else _override(values, key)
            if stored is not None:
                effective[key] = stored
                sources[key] = "setting"
                continue
            chat_field = _CHAT_FIELDS.get(key)
            if chat_field is not None:
                if provider is None:
                    provider = self._provider_defaults()
                value = getattr(provider, chat_field)
                effective[key] = value
                sources[key] = "provider" if value != getattr(baseline, chat_field) else "default"
                continue
            effective[key] = spec.default
            sources[key] = "default"
        return effective, sources

    def _settings_from_row(self, row: SettingsRow | None) -> EffectiveSettings:
        document = _document_from_row(row)
        effective, sources = self._effective(document.values, read_only=document.read_only)
        return EffectiveSettings(document=document, effective=effective, sources=sources, keys=KEYS)

    def read(self) -> EffectiveSettings:
        with self._access():
            row = self._store.get_settings()
        return self._settings_from_row(row)

    def flag(self, key: str) -> bool | None:
        """A bool key's stored value, or its default when unset; None when the document is
        read-only or the store fails. Unlike `read`, it never calls `provider_defaults`, so a
        scheduler pass does not touch the CLI's settings files."""
        spec = KEYS.get(key)
        if spec is None or spec.kind != "bool":
            raise ValueError(key)
        try:
            with self._access():
                row = self._store.get_settings()
        except SettingsError:
            return None
        values, read_only, _reason = _parse_row(row)
        if read_only:
            return None
        stored = _override(values, key)
        return stored if isinstance(stored, bool) else bool(spec.default)

    def workspace_ids(self) -> tuple[str, ...]:
        """The user's ticked `chat.workspace_ids` (ADR-050), or () when unset, invalid,
        read-only, or the store fails. Like `flag`, it never calls `provider_defaults`."""
        try:
            with self._access():
                row = self._store.get_settings()
        except SettingsError:
            return ()
        values, read_only, _reason = _parse_row(row)
        if read_only:
            return ()
        stored = _override(values, "chat.workspace_ids")
        return tuple(json.loads(stored)) if isinstance(stored, str) else ()

    def max_live_sessions(self) -> int:
        """`chat.max_live_sessions`'s stored value when the document is writable, else its
        registered default; a read-only (newer or corrupt) document also gives the default. On a
        `SettingsError`/`StateError` from the read, the last value this method read successfully
        (the default before any). Like `flag`, it never calls `provider_defaults`. Cached for
        `MAX_LIVE_CACHE_S` seconds, since `session.py` calls this at every admission; a
        committed `patch()`/`reset()` expires the cache at once, but an import, another
        `SettingsService` instance or a restore may still read up to `MAX_LIVE_CACHE_S` seconds
        stale."""
        if self._max_live_cached_at is not None and (
            _monotonic() - self._max_live_cached_at < MAX_LIVE_CACHE_S
        ):
            return self._last_max_live
        try:
            with self._access():
                row = self._store.get_settings()
        except SettingsError:
            return self._last_max_live
        values, read_only, _reason = _parse_row(row)
        stored = None if read_only else _override(values, "chat.max_live_sessions")
        self._last_max_live = stored if isinstance(stored, int) else _max_live_default()
        self._max_live_cached_at = _monotonic()
        return self._last_max_live

    def patch(
        self, command_id: str, expected_revision: int, changes: Mapping[str, object]
    ) -> tuple[EffectiveSettings, bool]:
        command_id = _command_id(command_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise SettingsError("invalid_settings", fields={"expected_revision": "invalid_type"})
        if not isinstance(changes, Mapping):
            raise SettingsError("invalid_settings", fields={"changes": "invalid_type"})
        validated: dict[str, object | None] = {}
        fields: dict[str, str] = {}
        for key, value in changes.items():
            if not isinstance(key, str):
                fields[str(key)] = "unknown_key"
                continue
            try:
                validated[key] = _validate_change(key, value)
            except _FieldError as exc:
                fields[key] = exc.code
        if fields:
            raise SettingsError("invalid_settings", fields=fields)
        missing: dict[str, str] = {}
        with self._access(), self._store._connection() as connection:
            for key in KEYS:
                if key not in _UUID_KEYS:
                    continue
                avatar_id = validated.get(key)
                if not isinstance(avatar_id, str):
                    continue
                if not profiles.avatar_committed(connection, avatar_id):
                    missing[key] = "avatar_missing"
        if missing:
            raise SettingsError("invalid_settings", fields=missing)

        patch_hash = _patch_hash(expected_revision, validated)

        # Measured against the latest snapshot before contending for the write lock:
        # a merge the transaction itself would also reach, since a revision match down there
        # proves nothing else committed between this read and the write.
        with self._access():
            preview_row = self._store.get_settings()
        preview_values, preview_read_only, _preview_reason = _parse_row(preview_row)
        if preview_row is None and not _merge(preview_values or {}, validated):
            # A store that never had a document, patched into still having none, is a
            # pure no-op -- skip the writer rather than materialise an empty row and bump the
            # revision. Doesn't check setting_commands for this command_id first (reachable only
            # by reusing an id from an earlier no-op before any document ever existed); add that
            # check if it needs covering. A real conflict (the caller's revision was never 0)
            # still gets reported.
            if expected_revision != 0:
                raise SettingsConflict(0, self._settings_from_row(None))
            return self._settings_from_row(None), False
        if not preview_read_only:
            candidate = _serialize(_merge(preview_values or {}, validated))
            if len(candidate) > MAX_DOCUMENT_BYTES:
                raise SettingsError("settings_too_large", extra={"current_bytes": len(candidate)})

        replayed = False
        committed = False

        def changed(
            current: SettingsRow | None, existing: CommandReceipt | None
        ) -> tuple[SettingsRow, str]:
            nonlocal replayed, committed
            values, read_only, _reason = _parse_row(current)
            if read_only:
                raise SettingsError("read_only")
            values = values or {}
            if existing is not None:
                if existing["patch_hash"] != patch_hash:
                    raise SettingsError("command_conflict")
                if current is not None:
                    replayed = True
                    return current, patch_hash
                # An orphan receipt (current is None): only possible from data written before
                # delete_settings started clearing setting_commands too (storage.py's
                # update_settings only short-circuits when both a receipt and a row exist).
                # Falls through and builds a fresh document, exactly as if never recorded.
            revision = current["revision"] if current is not None else 0
            if revision != expected_revision:
                raise _StaleSignal(_document_from_row(current))
            merged = _merge(values, validated)
            if current is not None and merged == values:
                return current, patch_hash  # unchanged document: a no-op, receipt still recorded
            document = _serialize(merged)
            if len(document) > MAX_DOCUMENT_BYTES:
                raise SettingsError("settings_too_large", extra={"current_bytes": len(document)})
            committed = True
            return {
                "version": SETTINGS_VERSION,
                "revision": revision + 1,
                "document": document,
                "updated_at": _now(),
            }, patch_hash

        try:
            with self._access():
                row, _receipt = self._store.update_settings(command_id, changed)
        except _StaleSignal as stale:
            effective, sources = self._effective(
                stale.document.values, read_only=stale.document.read_only
            )
            current = EffectiveSettings(
                document=stale.document, effective=effective, sources=sources, keys=KEYS
            )
            raise SettingsConflict(stale.document.revision, current) from None

        if committed:
            self._max_live_cached_at = None
        if committed and any(key in _CHAT_FIELDS for key in validated):
            self._on_chat_defaults(self.chat_defaults())
        return self._settings_from_row(row), replayed

    def reset(self, command_id: str) -> EffectiveSettings:
        command_id = _command_id(command_id)
        committed = False

        def changed(
            current: SettingsRow | None, existing: CommandReceipt | None
        ) -> tuple[SettingsRow, str]:
            nonlocal committed
            _values, read_only, _reason = _parse_row(current)
            if read_only:
                raise SettingsError("read_only")
            if existing is not None:
                if existing["patch_hash"] != _RESET_PATCH_HASH:
                    raise SettingsError("command_conflict")
                if current is not None:
                    return current, _RESET_PATCH_HASH
                # An orphan receipt: falls through to a fresh (empty) apply, same as patch().
            revision = current["revision"] if current is not None else 0
            if current is not None and current["document"] == "{}":
                return current, _RESET_PATCH_HASH  # already empty: no-op, receipt still recorded
            committed = True
            return {
                "version": SETTINGS_VERSION,
                "revision": revision + 1,
                "document": "{}",
                "updated_at": _now(),
            }, _RESET_PATCH_HASH

        with self._access():
            row, _receipt = self._store.update_settings(command_id, changed)
        if committed:
            self._on_chat_defaults(self.chat_defaults())
            self._max_live_cached_at = None
        return self._settings_from_row(row)

    def import_snapshot(self) -> SettingsDocument:
        """Raw app settings and their revision from one read, without the provider overlay."""
        with self._access():
            row = self._store.get_settings()
        return _document_from_row(row)

    def export_values(self) -> dict[str, object]:
        with self._access():
            row = self._store.get_settings()
        values, _read_only, _reason = _parse_row(row)
        # A newer document's values are still real data (just not ours to apply): export them as
        # stored, bar `RETIRED_KEYS`. Only a corrupt, unparseable document (values is None) has
        # nothing to give.
        return dict(values) if values is not None else {}

    def chat_defaults_available(self) -> bool:
        """Whether a new chat may safely inherit the app's model default."""
        try:
            return not self.import_snapshot().read_only
        except SettingsError:
            return False

    def chat_defaults(self) -> Prefs:
        with self._access():
            row = self._store.get_settings()
        values, read_only, _reason = _parse_row(row)
        provider = self._provider_defaults()
        if read_only:
            return provider
        mode = _override(values, "chat.default_mode")
        effort = _override(values, "chat.default_effort")
        model = _override(values, "chat.default_model")
        return Prefs(
            mode=mode if isinstance(mode, str) else provider.mode,
            effort=effort if isinstance(effort, str) else provider.effort,
            model=model if isinstance(model, str) else provider.model,
        )

    def native_chat_model(self, model: str | None) -> str | None:
        """Profiles inherit native defaults; endpoint defaults apply to ordinary chats."""
        if model is None or not model.startswith("viki-llm/"):
            return model
        provider = self._provider_defaults().model
        return None if provider and provider.startswith("viki-llm/") else provider


def to_view(effective: EffectiveSettings) -> dict[str, object]:
    """The GET/PATCH response shape: flattens `document` and trims `keys` to the six
    fields the shell needs (never `key`, redundant with the mapping's own key, or `max_len`)."""
    document = effective.document
    return {
        "version": document.version,
        "revision": document.revision,
        "read_only": document.read_only,
        "reason": document.reason,
        "values": dict(document.values) if document.values is not None else None,
        "effective": dict(effective.effective),
        "sources": dict(effective.sources),
        "keys": {
            key: {
                "scope": spec.scope,
                "applies": spec.applies,
                "kind": spec.kind,
                "allowed": list(spec.allowed) if spec.allowed is not None else None,
                "default": spec.default,
                "personal": spec.personal,
                **({"bounds": list(spec.bounds)} if spec.bounds is not None else {}),
            }
            for key, spec in effective.keys.items()
        },
    }
