"""SettingsService: the one app-wide document, its revision guard and the provider overlay.
No vault or `~/.claude` write."""

from __future__ import annotations

import json
import sqlite3
import threading
import types
import uuid
from pathlib import Path
from typing import Any

import pytest

from viki_agent import storage
from viki_agent.app import settings as settings_module
from viki_agent.app.settings import KEYS, SettingsConflict, SettingsError, SettingsService
from viki_agent.state import AppState, Prefs, user_defaults
from viki_agent.storage import SessionStore, StateError
from viki_agent.voice import voice_models, voicebox_http

CID_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CID_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CID_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def ident() -> str:
    return str(uuid.uuid4())


def _store(tmp_path: Path) -> SessionStore:
    return AppState(tmp_path / "state.json", Prefs()).store


def _service(
    store: SessionStore, *, defaults: Prefs | None = None, sink: list[Prefs] | None = None
) -> SettingsService:
    chosen = defaults if defaults is not None else Prefs()
    return SettingsService(
        store,
        provider_defaults=lambda: chosen,
        on_chat_defaults=(sink.append if sink is not None else lambda prefs: None),
    )


def test_invalid_changes_return_field_errors_and_change_nothing(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(
            CID_A,
            0,
            {
                "appearance.theme": "purple",  # not_allowed
                "accessibility.font_scale": "big",  # invalid_type
                "identity.assistant_name": "",  # too_long (min 1)
                "no.such.key": 1,  # unknown_key
            },
        )
    assert failure.value.code == "invalid_settings"
    assert failure.value.fields == {
        "appearance.theme": "not_allowed",
        "accessibility.font_scale": "invalid_type",
        "identity.assistant_name": "too_long",
        "no.such.key": "unknown_key",
    }
    assert service.read() == before


def test_keys_map_reports_scope_and_applies(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    keys = service.read().keys
    assert set(keys) == set(KEYS)
    assert keys["appearance.theme"].scope == "app"
    assert keys["appearance.theme"].applies == "now"
    assert keys["chat.default_mode"].applies == "next_session"
    assert keys["identity.display_name"].personal is True
    assert keys["appearance.theme"].personal is False


def test_null_change_restores_the_inherited_value_and_source(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(CID_A, 0, {"appearance.theme": "dark"})
    assert saved.effective["appearance.theme"] == "dark"
    assert saved.sources["appearance.theme"] == "setting"
    restored, _replayed = service.patch(CID_B, saved.document.revision, {"appearance.theme": None})
    assert restored.effective["appearance.theme"] == "system"
    assert restored.sources["appearance.theme"] == "default"
    assert "appearance.theme" not in (restored.document.values or {})


def test_chat_defaults_report_provider_setting_and_default_sources(tmp_path: Path) -> None:
    home, vault = tmp_path / "home", tmp_path / "vault"
    (vault / ".claude").mkdir(parents=True)
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"effortLevel": "low"}), encoding="utf-8")
    provider = user_defaults(home, vault, env={})
    assert provider == Prefs(mode="default", effort="low", model=None)
    service = _service(_store(tmp_path), defaults=provider)
    effective = service.read()
    # effortLevel differs from the built-in Prefs() default: provider wins the source label.
    assert effective.effective["chat.default_effort"] == "low"
    assert effective.sources["chat.default_effort"] == "provider"
    # mode was never set anywhere: provider's value equals the built-in default.
    assert effective.effective["chat.default_mode"] == "default"
    assert effective.sources["chat.default_mode"] == "default"
    saved, _replayed = service.patch(CID_A, 0, {"chat.default_effort": "max"})
    assert saved.effective["chat.default_effort"] == "max"
    assert saved.sources["chat.default_effort"] == "setting"


def test_chat_workspace_ids_key_sits_after_default_model() -> None:
    keys = list(KEYS)
    assert keys.index("chat.workspace_ids") == keys.index("chat.default_model") + 1
    spec = KEYS["chat.workspace_ids"]
    assert (spec.kind, spec.allowed, spec.default) == ("string", None, "[]")
    assert (spec.applies, spec.personal, spec.max_len) == ("next_session", True, 512)


def test_chat_workspace_ids_saves_sorted_canonical_and_reads_back(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    assert service.workspace_ids() == ()
    a, b = ident(), ident()
    ordered = tuple(sorted((a, b)))
    saved, _replayed = service.patch(CID_A, 0, {"chat.workspace_ids": json.dumps([b, a])})
    assert saved.effective["chat.workspace_ids"] == json.dumps(list(ordered), separators=(",", ":"))
    assert service.workspace_ids() == ordered
    reset, _replayed = service.patch(CID_B, saved.document.revision, {"chat.workspace_ids": None})
    assert reset.effective["chat.workspace_ids"] == "[]"
    assert service.workspace_ids() == ()


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        "{}",
        json.dumps([str(uuid.UUID(int=i)) for i in range(8)]),
        json.dumps([CID_A, CID_A]),
        json.dumps([CID_A.upper()]),
        json.dumps(["not-a-uuid"]),
    ],
)
def test_chat_workspace_ids_refusals(tmp_path: Path, value: str) -> None:
    service = _service(_store(tmp_path))
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"chat.workspace_ids": value})
    assert failure.value.fields == {"chat.workspace_ids": "not_allowed"}


def test_workspace_ids_is_empty_on_a_read_only_document(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',?,?,?,?)",
            (
                2,
                1,
                json.dumps({"chat.workspace_ids": json.dumps([CID_A])}),
                "2026-01-01T00:00:00.000Z",
            ),
        )
        connection.commit()
    assert _service(store).workspace_ids() == ()


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        json.dumps(sorted(str(uuid.UUID(int=i)) for i in range(8)), separators=(",", ":")),
        json.dumps(["A" * 36]),
    ],
)
def test_a_stored_value_the_schema_refuses_reads_as_no_ticks(tmp_path: Path, raw: str) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',?,?,?,?)",
            (
                settings_module.SETTINGS_VERSION,
                1,
                json.dumps({"chat.workspace_ids": raw}),
                "2026-01-01T00:00:00.000Z",
            ),
        )
        connection.commit()
    assert _service(store).workspace_ids() == ()


def test_chat_max_live_sessions_key_sits_after_other_sign_in_with_its_bounds() -> None:
    keys = list(KEYS)
    assert keys.index("chat.max_live_sessions") == keys.index("chat.other_sign_in") + 1
    spec = KEYS["chat.max_live_sessions"]
    assert (spec.kind, spec.allowed, spec.default) == ("number", None, 30)
    assert (spec.applies, spec.personal, spec.bounds) == ("now", False, (1, 999))
    assert settings_module.SETTINGS_VERSION == 1


def test_chat_max_live_sessions_saves_whole_numbers_in_bounds_and_resets(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    revision = 0
    for value in (1, 30, 999):
        saved, _replayed = service.patch(ident(), revision, {"chat.max_live_sessions": value})
        assert saved.effective["chat.max_live_sessions"] == value
        assert saved.sources["chat.max_live_sessions"] == "setting"
        revision = saved.document.revision
    reset, _replayed = service.patch(ident(), revision, {"chat.max_live_sessions": None})
    assert reset.effective["chat.max_live_sessions"] == 30
    assert reset.sources["chat.max_live_sessions"] == "default"


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (0, "not_allowed"),
        (1000, "not_allowed"),
        (-5, "not_allowed"),
        (30.0, "invalid_type"),
        (2.5, "invalid_type"),
        (True, "invalid_type"),
        ("30", "invalid_type"),
    ],
)
def test_chat_max_live_sessions_refuses_out_of_bounds_and_non_integers(
    tmp_path: Path, bad: object, code: str
) -> None:
    service = _service(_store(tmp_path))
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"chat.max_live_sessions": bad})
    assert failure.value.fields == {"chat.max_live_sessions": code}
    assert service.read() == before


@pytest.mark.parametrize("raw", [5000, "x"])
def test_chat_max_live_sessions_stored_bad_value_reads_as_default(
    tmp_path: Path, raw: object
) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',?,?,?,?)",
            (
                settings_module.SETTINGS_VERSION,
                1,
                json.dumps({"chat.max_live_sessions": raw}),
                "2026-01-01T00:00:00.000Z",
            ),
        )
        connection.commit()
    service = _service(store)
    assert service.read().effective["chat.max_live_sessions"] == 30
    assert service.max_live_sessions() == 30


def test_max_live_sessions_reads_stored_value_and_falls_back_on_newer_or_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    clock = [0.0]
    monkeypatch.setattr(settings_module, "_monotonic", lambda: clock[0])
    assert service.max_live_sessions() == 30
    saved, _replayed = service.patch(CID_A, 0, {"chat.max_live_sessions": 12})
    assert saved.effective["chat.max_live_sessions"] == 12
    assert service.max_live_sessions() == 12

    # Past the cache window before each raw-SQL mutation: proves the read-only fallback itself,
    # not stale cache that would mask it.
    clock[0] += settings_module.MAX_LIVE_CACHE_S
    with sqlite_connection(store) as connection:
        connection.execute("UPDATE settings SET version=2 WHERE scope='app'")
        connection.commit()
    assert service.max_live_sessions() == 30  # a newer document is read-only: gives the default

    clock[0] += settings_module.MAX_LIVE_CACHE_S
    with sqlite_connection(store) as connection:
        connection.execute(
            "UPDATE settings SET version=1, document=? WHERE scope='app'", ("not json",)
        )
        connection.commit()
    assert service.max_live_sessions() == 30  # corrupt is read-only too


def test_max_live_sessions_store_failure_keeps_the_last_value_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    clock = [0.0]
    monkeypatch.setattr(settings_module, "_monotonic", lambda: clock[0])
    with monkeypatch.context() as patch:
        patch.setattr(
            store, "get_settings", lambda: (_ for _ in ()).throw(StateError("unavailable"))
        )
        assert service.max_live_sessions() == 30  # the default before any successful read
    saved, _replayed = service.patch(CID_A, 0, {"chat.max_live_sessions": 12})
    assert saved.effective["chat.max_live_sessions"] == 12
    assert service.max_live_sessions() == 12
    # Past the cache window: the next call attempts a real read instead of reusing the cache.
    clock[0] += settings_module.MAX_LIVE_CACHE_S
    with monkeypatch.context() as patch:
        patch.setattr(
            store, "get_settings", lambda: (_ for _ in ()).throw(StateError("unavailable"))
        )
        assert service.max_live_sessions() == 12  # last value this method read successfully


def test_max_live_sessions_caches_a_read_within_one_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    original = store.get_settings
    calls: list[None] = []

    def spy() -> storage.SettingsRow | None:
        calls.append(None)
        return original()

    monkeypatch.setattr(store, "get_settings", spy)
    clock = [0.0]
    monkeypatch.setattr(settings_module, "_monotonic", lambda: clock[0])

    assert service.max_live_sessions() == 30
    clock[0] += settings_module.MAX_LIVE_CACHE_S - 0.001
    assert service.max_live_sessions() == 30
    assert len(calls) == 1  # the second call, inside the window, reused the cache


def test_max_live_sessions_reads_again_once_the_cache_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    original = store.get_settings
    calls: list[None] = []

    def spy() -> storage.SettingsRow | None:
        calls.append(None)
        return original()

    monkeypatch.setattr(store, "get_settings", spy)
    clock = [0.0]
    monkeypatch.setattr(settings_module, "_monotonic", lambda: clock[0])

    assert service.max_live_sessions() == 30
    clock[0] += settings_module.MAX_LIVE_CACHE_S
    assert service.max_live_sessions() == 30
    assert len(calls) == 2  # exactly at the window edge: no longer "under", so it reads again


def test_max_live_sessions_patch_of_the_key_expires_the_cache_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    clock = [0.0]
    monkeypatch.setattr(settings_module, "_monotonic", lambda: clock[0])
    assert service.max_live_sessions() == 30
    saved, _replayed = service.patch(CID_A, 0, {"chat.max_live_sessions": 12})
    assert saved.effective["chat.max_live_sessions"] == 12
    assert service.max_live_sessions() == 12  # no clock change: the patch itself expired it


def test_max_live_sessions_reset_expires_the_cache_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    clock = [0.0]
    monkeypatch.setattr(settings_module, "_monotonic", lambda: clock[0])
    service.patch(CID_A, 0, {"chat.max_live_sessions": 12})
    assert service.max_live_sessions() == 12
    service.reset(CID_B)
    assert service.max_live_sessions() == 30  # no clock change: the reset itself expired it


def test_two_writers_the_second_commit_gets_409(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first, second = _service(store), _service(store)
    revision = first.read().document.revision
    first.patch(CID_A, revision, {"appearance.theme": "dark"})
    with pytest.raises(SettingsConflict) as failure:
        second.patch(CID_B, revision, {"appearance.theme": "light"})
    assert failure.value.code == "stale_revision"
    assert failure.value.current_revision == revision + 1
    assert failure.value.current.effective["appearance.theme"] == "dark"
    assert (
        failure.value.current.effective["appearance.theme"]
        == second.read().effective["appearance.theme"]
    )


def test_newer_or_corrupt_document_refuses_patch_reset_and_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',?,?,?,?)",
            (2, 1, json.dumps({"appearance.theme": "dark"}), "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    for outcome in (
        lambda: service.patch(CID_A, 1, {"appearance.theme": "light"}),
        lambda: service.reset(CID_A),
    ):
        with pytest.raises(SettingsError) as failure:
            outcome()
        assert failure.value.code == "read_only"
    with pytest.raises(StateError):
        store.delete_settings(_refuse_if_read_only)
    read_back = service.read()
    assert read_back.document.read_only is True
    assert read_back.document.reason == "newer_version"
    # The raw value is still shown verbatim under "values", but a newer document must never
    # leak into "effective"/"sources" as if it had been applied: sessions (chat_defaults) and
    # the shell (read) must agree on what actually took effect.
    assert read_back.document.values is not None
    assert read_back.document.values["appearance.theme"] == "dark"
    assert read_back.effective["appearance.theme"] != "dark"
    assert read_back.sources["appearance.theme"] == "default"
    assert set(read_back.sources.values()) <= {"provider", "default"}

    with sqlite_connection(store) as connection:
        connection.execute(
            "UPDATE settings SET version=1, document=? WHERE scope='app'", ("not json",)
        )
        connection.commit()
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_B, 1, {"appearance.theme": "dark"})
    assert failure.value.code == "read_only"
    with pytest.raises(StateError):
        store.delete_settings(_refuse_if_read_only)
    corrupt = service.read()
    assert corrupt.document.read_only is True
    assert corrupt.document.reason == "corrupt"
    assert corrupt.document.values is None
    # A corrupt document never reports a "setting" source: only provider/default.
    assert set(corrupt.sources.values()) <= {"provider", "default"}


def test_unknown_keys_survive_patches_untouched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',1,1,?,?)",
            (json.dumps({"future.key": "from-a-later-app"}), "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    service = _service(store)
    saved, _replayed = service.patch(CID_A, 1, {"appearance.theme": "dark"})
    assert saved.document.values is not None
    assert saved.document.values["future.key"] == "from-a-later-app"
    assert saved.document.values["appearance.theme"] == "dark"


def test_a_stored_retired_key_is_ignored_on_read_and_kept_on_patch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',1,1,?,?)",
            (json.dumps({"decision.watch.retired": False}), "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    service = _service(store)
    read = service.read()
    assert "decision.watch.retired" not in read.effective
    assert "decision.watch.retired" not in read.sources
    assert read.effective["decision.watch.kanban"] is True
    saved, _replayed = service.patch(CID_A, 1, {"decision.watch.kanban": False})
    assert saved.document.values is not None
    assert saved.document.values["decision.watch.retired"] is False


def test_reset_drops_a_stored_retired_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',1,1,?,?)",
            (json.dumps({"decision.watch.retired": False}), "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    service = _service(store)
    result = service.reset(CID_A)
    assert result.document.values == {}
    assert "decision.watch.retired" not in (result.document.values or {})
    assert service.read().effective["decision.watch.kanban"] is True


def _plant_near_max_document(store: SessionStore) -> None:
    """Just under the 64 KiB cap on its own -- the merge a caller then attempts crosses it by a
    few bytes. Compact separators, matching _serialize exactly: the CHECK constraint counts
    these raw bytes."""
    with sqlite_connection(store) as connection:
        padding = json.dumps(
            {"future.padding": "x" * (settings_module.MAX_DOCUMENT_BYTES - 57)},
            separators=(",", ":"),
        )
        assert len(padding) <= settings_module.MAX_DOCUMENT_BYTES
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',1,1,?,?)",
            (padding, "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()


def test_oversize_document_is_413_without_a_write(tmp_path: Path) -> None:
    """A document already near the 64 KiB cap (planted directly, as a long run of small patches
    would build up) refuses one more patch before any writer transaction opens."""
    store = _store(tmp_path)
    _plant_near_max_document(store)
    service = _service(store)
    before = service.read().document.revision
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_A, before, {"identity.assistant_name": "Overflow"})
    assert failure.value.code == "settings_too_large"
    assert int(failure.value.extra["current_bytes"]) > settings_module.MAX_DOCUMENT_BYTES  # type: ignore[arg-type]
    assert service.read().document.revision == before


def test_oversize_precheck_never_opens_the_writer_or_records_a_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinguishes the pre-writer size measurement from the in-transaction backstop: the
    writer must never even open, so no receipt is recorded for the refused command_id either."""
    store = _store(tmp_path)
    _plant_near_max_document(store)
    service = _service(store)
    before = service.read().document.revision

    calls: list[str] = []
    original = store.update_settings

    def spy(command_id: str, changed: Any) -> Any:
        calls.append(command_id)
        return original(command_id, changed)

    monkeypatch.setattr(store, "update_settings", spy)
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_A, before, {"identity.assistant_name": "Overflow"})
    assert failure.value.code == "settings_too_large"
    assert calls == []
    assert service.read().document.revision == before
    with sqlite_connection(store) as connection:
        count = connection.execute(
            "SELECT count(*) FROM setting_commands WHERE command_id=?", (CID_A,)
        ).fetchone()[0]
    assert count == 0


def test_on_chat_defaults_skips_a_stale_attempt_and_a_replay(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sink: list[Prefs] = []
    service = _service(store, sink=sink)
    saved, _replayed = service.patch(CID_A, 0, {"chat.default_effort": "low"})
    assert len(sink) == 1
    other = _service(store)
    other.patch(CID_B, saved.document.revision, {"appearance.theme": "dark"})
    with pytest.raises(SettingsConflict):
        service.patch(CID_C, saved.document.revision, {"chat.default_effort": "high"})
    assert len(sink) == 1  # a refused (stale) chat change must not refresh session defaults
    _replayed_result, was_replayed = service.patch(CID_A, 0, {"chat.default_effort": "low"})
    assert was_replayed is True
    assert len(sink) == 1  # replaying an already-applied change must not fire it again


def test_command_conflict_when_same_command_id_carries_different_changes(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    service.patch(CID_A, 0, {"appearance.theme": "dark"})
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_A, 0, {"appearance.theme": "light"})
    assert failure.value.code == "command_conflict"


def test_orphan_receipt_falls_through_to_a_fresh_apply(tmp_path: Path) -> None:
    """A receipt with no settings row (only reachable from data older than the delete_settings
    fix that clears both tables together) must not be reported as corrupt: it reapplies fresh."""
    store = _store(tmp_path)
    patch_hash = settings_module._patch_hash(0, {"appearance.theme": "dark"})
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO setting_commands (command_id,revision,patch_hash,applied_at) "
            "VALUES (?,?,?,?)",
            (CID_A, 5, patch_hash, "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    service = _service(store)
    saved, replayed = service.patch(CID_A, 0, {"appearance.theme": "dark"})
    assert replayed is False
    assert saved.document.revision == 1
    assert saved.effective["appearance.theme"] == "dark"


def test_reset_orphan_receipt_falls_through_to_a_fresh_empty_apply(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO setting_commands (command_id,revision,patch_hash,applied_at) "
            "VALUES (?,?,?,?)",
            (CID_A, 5, settings_module._RESET_PATCH_HASH, "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    result = _service(store).reset(CID_A)
    assert result.document.revision == 1
    assert result.document.values == {}


def test_empty_patch_on_a_fresh_store_is_a_pure_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    saved, replayed = service.patch(CID_A, 0, {})
    assert saved.document.revision == 0
    assert replayed is False
    with sqlite_connection(store) as connection:
        rows = connection.execute("SELECT count(*) FROM settings").fetchone()[0]
    assert rows == 0
    # A stale expected_revision against the same empty store is still a genuine conflict.
    with pytest.raises(SettingsConflict) as failure:
        service.patch(CID_B, 1, {})
    assert failure.value.current_revision == 0


def test_export_values_returns_verbatim_for_newer_document_and_empty_for_corrupt(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',2,1,?,?)",
            (json.dumps({"appearance.theme": "dark"}), "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    assert service.export_values() == {"appearance.theme": "dark"}
    with sqlite_connection(store) as connection:
        connection.execute(
            "UPDATE settings SET version=1, document=? WHERE scope='app'", ("not json",)
        )
        connection.commit()
    assert service.export_values() == {}


@pytest.mark.parametrize(
    ("version", "document", "reason"),
    [
        (1, '{"appearance.theme":"light","future.key":{"kept":[1,2]}}', None),
        (2, '{"appearance.theme":"light","future.key":{"kept":[1,2]}}', "newer_version"),
        (1, "not json", "corrupt"),
    ],
)
def test_import_snapshot_is_one_raw_provider_free_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: int,
    document: str,
    reason: str | None,
) -> None:
    def refuse() -> Prefs:
        raise AssertionError("provider_defaults read")

    store = _store(tmp_path)
    service = SettingsService(store, provider_defaults=refuse, on_chat_defaults=lambda _prefs: None)
    original = store.get_settings
    reads: list[None] = []

    def read() -> storage.SettingsRow | None:
        reads.append(None)
        return original()

    monkeypatch.setattr(store, "get_settings", read)
    empty = service.import_snapshot()
    assert empty.revision == 0 and empty.values == {} and empty.read_only is False
    assert reads == [None]
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',?,7,?,?)",
            (version, document, "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()

    current = service.import_snapshot()
    assert reads == [None, None]
    assert current.version == version and current.revision == 7
    assert current.values == (None if reason == "corrupt" else json.loads(document))
    assert current.read_only is (reason is not None)
    assert current.reason == reason
    row = original()
    assert row is not None and row["document"] == document


def test_appearance_and_accessibility_keys_accept_only_listed_values(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(
        CID_A,
        0,
        {
            "appearance.theme": "dark",
            "appearance.density": "compact",
            "accessibility.font_scale": 1.3,
            "accessibility.reduce_motion": "reduce",
        },
    )
    assert saved.effective["appearance.theme"] == "dark"
    assert saved.effective["appearance.density"] == "compact"
    assert saved.effective["accessibility.font_scale"] == 1.3
    assert saved.effective["accessibility.reduce_motion"] == "reduce"
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_B, saved.document.revision, {"appearance.density": "roomy"})
    assert failure.value.fields == {"appearance.density": "not_allowed"}
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_C, saved.document.revision, {"accessibility.font_scale": 2})
    assert failure.value.fields == {"accessibility.font_scale": "not_allowed"}


def test_privacy_flags_are_booleans(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(
        CID_A, 0, {"privacy.hide_previews": True, "privacy.diagnostics_include_paths": True}
    )
    assert saved.effective["privacy.hide_previews"] is True
    assert saved.effective["privacy.diagnostics_include_paths"] is True
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_B, saved.document.revision, {"privacy.hide_previews": "yes"})
    assert failure.value.fields == {"privacy.hide_previews": "invalid_type"}


def test_workbench_keys_have_their_specs(tmp_path: Path) -> None:
    size = KEYS["workbench.code_font_size"]
    wrap = KEYS["workbench.line_wrap"]
    assert (size.kind, size.default, size.applies, size.personal) == ("number", 13, "now", False)
    assert size.allowed == tuple(range(11, 21))
    assert (wrap.kind, wrap.default, wrap.applies, wrap.personal) == ("bool", False, "now", False)
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(
        CID_A, 0, {"workbench.code_font_size": 11, "workbench.line_wrap": True}
    )
    assert saved.effective["workbench.code_font_size"] == 11
    assert saved.effective["workbench.line_wrap"] is True
    saved, _replayed = service.patch(
        CID_B, saved.document.revision, {"workbench.code_font_size": 20}
    )
    assert saved.effective["workbench.code_font_size"] == 20
    revision = saved.document.revision
    for bad in (10, 21, 13.5, "13", True):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), revision, {"workbench.code_font_size": bad})
        assert list(failure.value.fields) == ["workbench.code_font_size"]
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), revision, {"workbench.line_wrap": 1})
    assert failure.value.fields == {"workbench.line_wrap": "invalid_type"}


def test_workbench_worktree_cleanup_key_defaults_true_and_round_trips(tmp_path: Path) -> None:
    spec = KEYS["workbench.worktree_cleanup"]
    assert (spec.kind, spec.default, spec.applies, spec.personal) == ("bool", True, "now", False)
    service = _service(_store(tmp_path))
    assert service.read().effective["workbench.worktree_cleanup"] is True
    assert service.flag("workbench.worktree_cleanup") is True
    saved, _replayed = service.patch(CID_A, 0, {"workbench.worktree_cleanup": False})
    assert saved.effective["workbench.worktree_cleanup"] is False
    assert service.flag("workbench.worktree_cleanup") is False
    restored, _replayed = service.patch(
        CID_B, saved.document.revision, {"workbench.worktree_cleanup": None}
    )
    assert restored.effective["workbench.worktree_cleanup"] is True
    assert service.flag("workbench.worktree_cleanup") is True
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_C, restored.document.revision, {"workbench.worktree_cleanup": "no"})
    assert failure.value.fields == {"workbench.worktree_cleanup": "invalid_type"}


def test_read_aloud_keys_have_their_specs_and_rate_values(tmp_path: Path) -> None:
    rate = KEYS["voice.read_aloud_rate"]
    voices = KEYS["voice.read_aloud_voices"]
    seen = KEYS["voice.read_aloud_notice_seen"]
    assert (rate.kind, rate.default, rate.applies, rate.personal) == ("number", 1, "now", False)
    assert rate.allowed == (0.75, 1, 1.25, 1.5, 2)
    assert (voices.kind, voices.default, voices.max_len) == ("string", "{}", 4096)
    assert (seen.kind, seen.default, seen.personal) == ("bool", False, False)
    service = _service(_store(tmp_path))
    revision = 0
    for value in (0.75, 1, 1.25, 1.5, 2):
        saved, _replayed = service.patch(ident(), revision, {"voice.read_aloud_rate": value})
        assert saved.effective["voice.read_aloud_rate"] == value
        revision = saved.document.revision
    for bad in (0.5, 3, "1", True):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), revision, {"voice.read_aloud_rate": bad})
        assert list(failure.value.fields) == ["voice.read_aloud_rate"]
    saved, _replayed = service.patch(ident(), revision, {"voice.read_aloud_notice_seen": True})
    assert saved.effective["voice.read_aloud_notice_seen"] is True
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), saved.document.revision, {"voice.read_aloud_notice_seen": 1})
    assert failure.value.fields == {"voice.read_aloud_notice_seen": "invalid_type"}


_DICTATION_ALLOWED = {
    "voice.dictation_language": voice_models.DICTATION_LANGUAGES,
    "voice.model": ("auto", *voice_models.MODELS),
    "voice.backend": ("auto", "cpu", "cuda", "metal"),
}


def test_voice_keys_have_their_specs_and_save_every_allowed_value(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    view: dict[str, Any] = settings_module.to_view(service.read())
    revision = 0
    for key, allowed in _DICTATION_ALLOWED.items():
        spec = KEYS[key]
        assert (spec.kind, spec.allowed, spec.default) == ("enum", allowed, "auto")
        assert (spec.applies, spec.personal) == ("now", False)
        assert view["keys"][key]["default"] == "auto"
        assert view["effective"][key] == "auto"
        for value in allowed:
            saved, _replayed = service.patch(ident(), revision, {key: value})
            assert saved.effective[key] == value
            revision = saved.document.revision


def test_voice_keys_refuse_unknown_values(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    bad_values = {
        "voice.dictation_language": "xx",
        "voice.model": "large-v3-q5_0",
        "voice.backend": "vulkan",
    }
    for key, bad in bad_values.items():
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), 0, {key: bad})
        assert failure.value.fields == {key: "not_allowed"}
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), 0, {key: 3})
        assert failure.value.fields == {key: "invalid_type"}


def test_voice_pause_s_key_has_its_spec_and_accepts_allowed_values(tmp_path: Path) -> None:
    spec = KEYS["voice.pause_s"]
    assert (spec.kind, spec.default, spec.applies, spec.personal) == ("number", 3, "now", False)
    assert spec.allowed == (1.2, 2, 3, 4, 5)
    service = _service(_store(tmp_path))
    revision = 0
    for value in (1.2, 2, 3, 4, 5):
        saved, _replayed = service.patch(ident(), revision, {"voice.pause_s": value})
        assert saved.effective["voice.pause_s"] == value
        revision = saved.document.revision
    for bad in (1, 1.5, 6, "3", True):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), revision, {"voice.pause_s": bad})
        assert list(failure.value.fields) == ["voice.pause_s"]


def test_voicebox_keys_have_their_specs_and_defaults(tmp_path: Path) -> None:
    profile = KEYS["voice.voicebox_profile"]
    size = KEYS["voice.voicebox_model_size"]
    url = KEYS["voice.voicebox_url"]
    flag = KEYS["voice.speak_while_writing"]
    assert (profile.kind, profile.default, profile.applies, profile.personal) == (
        "string",
        None,
        "now",
        False,
    )
    assert profile.max_len == 36
    assert (size.kind, size.allowed, size.default) == ("enum", voicebox_http.MODEL_SIZES, "0.6B")
    assert (size.applies, size.personal) == ("now", False)
    assert (url.kind, url.default, url.applies, url.personal) == (
        "string",
        voicebox_http.DEFAULT_URL,
        "now",
        False,
    )
    assert url.max_len == 32
    assert (flag.kind, flag.default, flag.applies, flag.personal) == ("bool", False, "now", False)
    service = _service(_store(tmp_path))
    view: dict[str, Any] = settings_module.to_view(service.read())
    assert view["effective"]["voice.voicebox_profile"] is None
    assert view["effective"]["voice.voicebox_model_size"] == "0.6B"
    assert view["effective"]["voice.voicebox_url"] == voicebox_http.DEFAULT_URL
    assert view["effective"]["voice.speak_while_writing"] is False


def test_speech_engine_key_has_its_spec() -> None:
    spec = KEYS["voice.speech_engine"]
    assert (spec.kind, spec.allowed, spec.default) == ("enum", ("system", "pack", "voicebox"), None)
    assert (spec.applies, spec.personal) == ("now", False)


def test_voicebox_profile_accepts_a_uuid_without_an_avatar_and_resets_on_null(
    tmp_path: Path,
) -> None:
    service = _service(_store(tmp_path))
    profile_id = ident()  # no avatar seeded anywhere: a Voicebox profile names no avatar image
    saved, _replayed = service.patch(CID_A, 0, {"voice.voicebox_profile": profile_id})
    assert saved.document.values is not None
    assert saved.document.values["voice.voicebox_profile"] == profile_id
    assert saved.effective["voice.voicebox_profile"] == profile_id
    reset, _replayed = service.patch(
        CID_B, saved.document.revision, {"voice.voicebox_profile": None}
    )
    assert reset.effective["voice.voicebox_profile"] is None
    assert "voice.voicebox_profile" not in (reset.document.values or {})


def test_voicebox_profile_refuses_non_canonical_values_with_no_avatar_check(
    tmp_path: Path,
) -> None:
    service = _service(_store(tmp_path))
    before = service.read()
    # Upper-case and junk are refused as non-canonical; braces push the 36-char id over its own
    # max_len, so that one is too_long instead -- neither code is ever avatar_missing.
    for bad, code in (
        (ident().upper(), "not_allowed"),
        ("{" + ident() + "}", "too_long"),
        ("not-a-uuid", "not_allowed"),
    ):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), 0, {"voice.voicebox_profile": bad})
        assert failure.value.fields == {"voice.voicebox_profile": code}
    assert service.read() == before


def test_voicebox_model_size_refuses_unknown_sizes(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_A, 0, {"voice.voicebox_model_size": "2B"})
    assert failure.value.fields == {"voice.voicebox_model_size": "not_allowed"}
    revision = 0
    for value in voicebox_http.MODEL_SIZES:
        saved, _replayed = service.patch(ident(), revision, {"voice.voicebox_model_size": value})
        assert saved.effective["voice.voicebox_model_size"] == value
        revision = saved.document.revision


def test_voicebox_url_accepts_loopback_hosts_and_the_default(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    revision = 0
    for value in (voicebox_http.DEFAULT_URL, "http://localhost:1", "http://[::1]:65535"):
        saved, _replayed = service.patch(ident(), revision, {"voice.voicebox_url": value})
        assert saved.effective["voice.voicebox_url"] == value
        revision = saved.document.revision


def test_voicebox_url_refuses_a_lan_host(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_A, 0, {"voice.voicebox_url": "http://192.168.1.2:17493"})
    assert failure.value.fields == {"voice.voicebox_url": "not_allowed"}
    assert service.read() == before


def test_voicebox_url_refuses_non_http_and_extra_path_and_empty(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    for bad in ("https://127.0.0.1:17493", "http://127.0.0.1:17493/x"):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), 0, {"voice.voicebox_url": bad})
        assert failure.value.fields == {"voice.voicebox_url": "not_allowed"}
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"voice.voicebox_url": ""})
    assert failure.value.fields == {"voice.voicebox_url": "too_long"}


def test_speak_while_writing_is_a_boolean(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(CID_A, 0, {"voice.speak_while_writing": True})
    assert saved.effective["voice.speak_while_writing"] is True
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_B, saved.document.revision, {"voice.speak_while_writing": "yes"})
    assert failure.value.fields == {"voice.speak_while_writing": "invalid_type"}


def test_read_aloud_voice_map_is_stored_canonical_and_resets(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(
        ident(), 0, {"voice.read_aloud_voices": '{ "ja": "Haruka 日本", "en": "Zira" }'}
    )
    assert saved.effective["voice.read_aloud_voices"] == '{"en":"Zira","ja":"Haruka 日本"}'
    saved, _replayed = service.patch(
        ident(), saved.document.revision, {"voice.read_aloud_voices": None}
    )
    assert saved.effective["voice.read_aloud_voices"] == "{}"


def test_read_aloud_voice_uri_is_kept_as_sent_without_nfc(tmp_path: Path) -> None:
    # A voice URI is matched by exact equality with the browser's voiceURI, so no NFC.
    nfd = "Amélie"
    nfc = "Amélie"
    service = _service(_store(tmp_path))
    raw, _replayed = service.patch(ident(), 0, {"voice.read_aloud_voices": '{"fr":"' + nfd + '"}'})
    assert json.loads(str(raw.effective["voice.read_aloud_voices"])) == {"fr": nfd}
    escaped, _replayed = service.patch(
        ident(), raw.document.revision, {"voice.read_aloud_voices": '{"fr":"Ame\\u0301lie"}'}
    )
    assert escaped.effective["voice.read_aloud_voices"] == raw.effective["voice.read_aloud_voices"]
    precomposed, _replayed = service.patch(
        ident(), escaped.document.revision, {"voice.read_aloud_voices": '{"fr":"' + nfc + '"}'}
    )
    assert json.loads(str(precomposed.effective["voice.read_aloud_voices"])) == {"fr": nfc}


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "not_allowed"),
        ("[]", "not_allowed"),
        ("null", "not_allowed"),
        ('{"ja":NaN}', "not_allowed"),
        ('{"JA":"x"}', "not_allowed"),
        ('{"jpn1":"x"}', "not_allowed"),
        ('{"ja":""}', "not_allowed"),
        ('{"ja":1}', "not_allowed"),
        ('{"ja":"' + "x" * 257 + '"}', "not_allowed"),
        (
            "{" + ",".join(f'"{chr(97 + i // 26)}{chr(97 + i % 26)}":"x"' for i in range(33)) + "}",
            "not_allowed",
        ),
        ('{"ja":"x","ja":"y"}', "not_allowed"),
        ('{"ja":"a\x07b"}', "control_characters"),
        ("x" * 4097, "too_long"),
    ],
)
def test_read_aloud_voice_map_refusals(tmp_path: Path, value: str, code: str) -> None:
    service = _service(_store(tmp_path))
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"voice.read_aloud_voices": value})
    assert failure.value.fields == {"voice.read_aloud_voices": code}


def test_pack_voices_key_has_its_spec_and_sits_after_speech_engine() -> None:
    keys = list(KEYS)
    assert keys.index("voice.pack_voices") == keys.index("voice.speech_engine") + 1
    spec = KEYS["voice.pack_voices"]
    assert (spec.kind, spec.allowed, spec.default) == ("string", None, "{}")
    assert (spec.applies, spec.personal, spec.max_len) == ("now", False, 512)


def test_pack_voices_saves_a_canonical_map(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(
        ident(), 0, {"voice.pack_voices": '{ "de": "system", "en": "af_bella" }'}
    )
    assert saved.effective["voice.pack_voices"] == '{"de":"system","en":"af_bella"}'
    reset, _replayed = service.patch(ident(), saved.document.revision, {"voice.pack_voices": None})
    assert reset.effective["voice.pack_voices"] == "{}"


@pytest.mark.parametrize(
    "value",
    [
        '{"ja":"jf_alpha"}',  # Kokoro doesn't speak Japanese in this bundle
        '{"ja":"af_bella"}',  # af_bella speaks English, not Japanese
        '{"he":"system"}',  # not a voice_pack.LANGUAGES member
        '{"en":"ryan"}',  # presets are case-sensitive; the preset is "Ryan"
        '{"en":1}',
        "[]",
        '{"en":"x","en":"y"}',  # repeated key
        '{"en":NaN}',
    ],
)
def test_pack_voices_refuses_other_values(tmp_path: Path, value: str) -> None:
    service = _service(_store(tmp_path))
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"voice.pack_voices": value})
    assert failure.value.fields == {"voice.pack_voices": "not_allowed"}


def test_pack_voices_stored_bad_value_reads_as_default(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',1,1,?,?)",
            (json.dumps({"voice.pack_voices": "not json"}), "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    service = _service(store)
    assert service.read().effective["voice.pack_voices"] == "{}"


def test_identity_names_are_bounded_and_normalised(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    # NFC: combining ring above + a -> the single precomposed codepoint.
    decomposed = "å"
    saved, _replayed = service.patch(
        CID_A, 0, {"identity.display_name": decomposed, "identity.assistant_name": "Aria"}
    )
    assert saved.document.values is not None
    assert saved.document.values["identity.display_name"] == "å"
    assert saved.effective["identity.assistant_name"] == "Aria"
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_B, saved.document.revision, {"identity.assistant_name": ""})
    assert failure.value.fields == {"identity.assistant_name": "too_long"}
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_C, saved.document.revision, {"identity.display_name": "a\x00b"})
    assert failure.value.fields == {"identity.display_name": "control_characters"}
    with pytest.raises(SettingsError) as failure:
        service.patch(
            "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            saved.document.revision,
            {"identity.display_name": "x" * 65},
        )
    assert failure.value.fields == {"identity.display_name": "too_long"}


def test_the_retired_language_key_is_refused(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    assert "locale.language" in settings_module.RETIRED_KEYS
    assert settings_module.RETIRED_KEYS.isdisjoint(KEYS)
    for value in ("ja", "en", "system"):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), 0, {"locale.language": value})
        assert failure.value.fields == {"locale.language": "unknown_key"}
    assert service.read().document.revision == 0


def test_a_stored_language_value_is_ignored_and_dropped_on_the_next_save(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    stored = json.dumps({"appearance.theme": "dark", "locale.language": "ja"})
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',?,?,?,?)",
            (settings_module.SETTINGS_VERSION, 4, stored, "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()

    current = service.read()
    assert current.document.read_only is False
    assert current.document.values == {"appearance.theme": "dark"}
    assert current.effective["appearance.theme"] == "dark"
    assert "locale.language" not in current.effective
    assert "locale.language" not in current.keys
    assert service.export_values() == {"appearance.theme": "dark"}
    assert service.import_snapshot().values == {"appearance.theme": "dark"}

    # A save that changes nothing keeps the stored row as it is ...
    unchanged, _replayed = service.patch(CID_A, 4, {"appearance.theme": "dark"})
    assert unchanged.document.revision == 4
    row = store.get_settings()
    assert row is not None and row["document"] == stored
    # ... and the next real change writes the document without the retired key.
    saved, _replayed = service.patch(CID_B, 4, {"appearance.density": "compact"})
    assert saved.document.revision == 5
    row = store.get_settings()
    assert row is not None
    assert json.loads(row["document"]) == {
        "appearance.theme": "dark",
        "appearance.density": "compact",
    }


def test_settings_survive_restart(tmp_path: Path) -> None:
    saved, _replayed = _service(_store(tmp_path)).patch(
        CID_A, 0, {"appearance.theme": "dark", "onboarding.completed": True}
    )
    reopened = _service(_store(tmp_path)).read()
    assert reopened.document.revision == saved.document.revision
    assert reopened.effective["appearance.theme"] == "dark"
    assert reopened.effective["onboarding.completed"] is True


def test_locked_or_full_store_keeps_last_good(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    before, _replayed = service.patch(CID_A, 0, {"appearance.theme": "dark"})
    connect = sqlite3.connect

    class Failing(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
            if sql.startswith("INSERT INTO settings") or sql.startswith("COMMIT"):
                raise sqlite3.OperationalError("synthetic disk full")
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(
            storage.sqlite3, "connect", lambda *a, **kw: connect(*a, factory=Failing, **kw)
        )
        with pytest.raises(SettingsError) as failure:
            service.patch(CID_B, before.document.revision, {"appearance.theme": "light"})
        assert failure.value.code == "unavailable"
        assert "disk full" not in str(failure.value)
    after = service.read()
    assert after.document.revision == before.document.revision
    assert after.effective["appearance.theme"] == "dark"


def _refuse_if_read_only(row: Any) -> None:
    _values, read_only, _reason = settings_module._parse_row(row)
    if read_only:
        raise StateError("recovery_required")


class sqlite_connection:
    """A short-lived write connection outside the service, to plant rows a normal patch never
    would (a future schema version, or hand-corrupted JSON) — the same shape test_storage.py
    uses to reach states the service's own validation cannot produce."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._connection = sqlite3.connect(self._store.path)
        return self._connection

    def __exit__(self, *exc_info: object) -> None:
        assert self._connection is not None
        self._connection.close()


def seed_avatar(store: SessionStore, variants: tuple[int, ...] = (24, 32, 64, 128, 256)) -> str:
    """Plants one committed avatar (its row plus the given variant rows) with raw SQL, the same
    shape test_profiles.py uses; the settings service never writes these tables itself."""
    avatar_id, digest, now = ident(), "0" * 64, "2026-09-27T00:00:00.000Z"
    with store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO avatars VALUES (?,?,?,?,?,?,?)",
            (avatar_id, digest, 256, 1, digest, 6, now),
        )
        for size in variants:
            connection.execute(
                "INSERT INTO avatar_variants VALUES (?,?,?,?)", (avatar_id, size, 1, digest)
            )
    return avatar_id


def test_a_committed_avatars_id_saves_and_reads_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    avatar_id = seed_avatar(store)
    saved, _replayed = service.patch(CID_A, 0, {"identity.user_avatar_id": avatar_id})
    assert saved.document.values is not None
    assert saved.document.values["identity.user_avatar_id"] == avatar_id
    assert saved.effective["identity.user_avatar_id"] == avatar_id
    assert service.read().effective["identity.user_avatar_id"] == avatar_id


def test_avatar_ids_must_be_canonical_lower_case_uuids(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(
            CID_A,
            0,
            {
                "identity.user_avatar_id": ident().upper(),
                "identity.assistant_avatar_id": "x",
            },
        )
    assert failure.value.fields == {
        "identity.user_avatar_id": "not_allowed",
        "identity.assistant_avatar_id": "not_allowed",
    }
    assert service.read() == before


def test_avatar_ids_require_a_committed_avatar(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_A, 0, {"identity.user_avatar_id": ident()})
    assert failure.value.code == "invalid_settings"
    assert failure.value.fields == {"identity.user_avatar_id": "avatar_missing"}
    after = service.read()
    assert after.document.revision == before.document.revision
    assert after.effective["identity.user_avatar_id"] is None


def test_avatar_ids_require_a_committed_avatar_both_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(
            CID_A,
            0,
            {"identity.user_avatar_id": ident(), "identity.assistant_avatar_id": ident()},
        )
    assert failure.value.code == "invalid_settings"
    assert failure.value.fields == {
        "identity.user_avatar_id": "avatar_missing",
        "identity.assistant_avatar_id": "avatar_missing",
    }
    after = service.read()
    assert after.document.revision == before.document.revision


def test_avatar_ids_require_a_committed_avatar_assistant_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_A, 0, {"identity.assistant_avatar_id": ident()})
    assert failure.value.code == "invalid_settings"
    assert failure.value.fields == {"identity.assistant_avatar_id": "avatar_missing"}
    after = service.read()
    assert after.document.revision == before.document.revision
    assert after.effective["identity.assistant_avatar_id"] is None


def test_avatar_id_null_resets_to_default_null(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    avatar_id = seed_avatar(store)
    saved, _replayed = service.patch(CID_A, 0, {"identity.assistant_avatar_id": avatar_id})
    reset, _replayed = service.patch(
        CID_B, saved.document.revision, {"identity.assistant_avatar_id": None}
    )
    assert reset.effective["identity.assistant_avatar_id"] is None
    assert reset.document.values is not None
    assert "identity.assistant_avatar_id" not in reset.document.values


def test_identity_avatar_ids_reads_valid_empty_corrupt_and_newer_version_documents() -> None:
    user_id, assistant_id = ident(), ident()
    valid = json.dumps(
        {"identity.user_avatar_id": user_id, "identity.assistant_avatar_id": assistant_id}
    )
    assert settings_module.identity_avatar_ids(valid) == frozenset({user_id, assistant_id})
    assert settings_module.identity_avatar_ids("{}") == frozenset()
    assert settings_module.identity_avatar_ids("not json") is None
    # A document a future app wrote may carry keys this version has never heard of; its avatar
    # ids still count, since keeping more during retention is the safe direction.
    newer_version = json.dumps(
        {"identity.user_avatar_id": user_id, "future.key": "from-a-later-app"}
    )
    assert settings_module.identity_avatar_ids(newer_version) == frozenset({user_id})


def test_to_view_lists_the_avatar_keys_with_default_null(tmp_path: Path) -> None:
    view: dict[str, Any] = settings_module.to_view(_service(_store(tmp_path)).read())
    assert view["keys"]["identity.user_avatar_id"]["default"] is None
    assert view["keys"]["identity.assistant_avatar_id"]["default"] is None
    assert view["effective"]["identity.user_avatar_id"] is None
    assert view["effective"]["identity.assistant_avatar_id"] is None


def test_a_settings_document_nested_too_deep_reads_as_corrupt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = _service(store)
    service.patch(CID_A, 0, {"appearance.theme": "dark"})
    too_deep = "[" * 65_536  # the store's whole document budget, still far past the recursion limit
    with sqlite_connection(store) as connection:
        connection.execute(
            "UPDATE settings SET version=1, document=? WHERE scope='app'", (too_deep,)
        )
        connection.commit()
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_B, 1, {"appearance.theme": "light"})
    assert failure.value.code == "read_only"
    corrupt = service.read()
    assert corrupt.document.read_only is True
    assert corrupt.document.reason == "corrupt"
    assert corrupt.document.values is None


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_a_stored_nan_or_infinity_reads_as_corrupt(tmp_path: Path, token: str) -> None:
    store = _store(tmp_path)
    service = _service(store)
    service.patch(CID_A, 0, {"appearance.theme": "dark"})
    document = f'{{"appearance.theme": {token}}}'
    with sqlite_connection(store) as connection:
        connection.execute(
            "UPDATE settings SET version=1, document=? WHERE scope='app'", (document,)
        )
        connection.commit()
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_B, 1, {"appearance.theme": "light"})
    assert failure.value.code == "read_only"
    corrupt = service.read()
    assert corrupt.document.read_only is True
    assert corrupt.document.reason == "corrupt"
    assert corrupt.document.values is None


def test_safe_parse_refuses_an_undumpable_document(monkeypatch: pytest.MonkeyPatch) -> None:
    def _cannot_dump(*_arguments: object, **_keywords: object) -> str:
        raise RecursionError("too deep")

    monkeypatch.setattr(
        settings_module,
        "json",
        types.SimpleNamespace(loads=json.loads, dumps=_cannot_dump),
    )
    assert settings_module._safe_parse('{"appearance.theme": "dark"}') is None


def test_safe_parse_refuses_nan_when_the_dump_would_not(monkeypatch: pytest.MonkeyPatch) -> None:
    # the load itself refuses NaN and Infinity, even where a dump would not
    def _never_refuses(*_arguments: object, **_keywords: object) -> str:
        return ""

    monkeypatch.setattr(
        settings_module,
        "json",
        types.SimpleNamespace(loads=json.loads, dumps=_never_refuses),
    )
    assert settings_module._safe_parse('{"appearance.theme": NaN}') is None


def test_identity_avatar_ids_is_none_for_a_document_nested_too_deep() -> None:
    assert settings_module.identity_avatar_ids("[" * 200_000) is None


def test_routine_and_notification_keys_have_their_specs() -> None:
    for key in ("routines.paused", "notifications.enabled"):
        assert (KEYS[key].kind, KEYS[key].default, KEYS[key].applies) == ("bool", False, "now")
        assert KEYS[key].personal is False
    assert len(settings_module.QUIET_TIMES) == 48
    assert settings_module.QUIET_TIMES[:3] == ("00:00", "00:30", "01:00")
    assert settings_module.QUIET_TIMES[-1] == "23:30"
    for key in ("notifications.quiet_start", "notifications.quiet_end"):
        assert KEYS[key].kind == "enum"
        assert KEYS[key].allowed == settings_module.QUIET_TIMES
        assert KEYS[key].default is None
        assert (KEYS[key].applies, KEYS[key].personal) == ("now", False)


def test_quiet_times_accept_half_hours_only(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    for bad in ("22:15", "24:00", "7:00"):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), 0, {"notifications.quiet_start": bad})
        assert failure.value.fields == {"notifications.quiet_start": "not_allowed"}
    saved, _replayed = service.patch(ident(), 0, {"notifications.quiet_end": "22:30"})
    assert saved.effective["notifications.quiet_end"] == "22:30"


def test_flag_reads_stored_default_and_untrusted_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = _service(store)
    assert service.flag("routines.paused") is False
    service.patch(ident(), 0, {"routines.paused": True})
    assert service.flag("routines.paused") is True
    assert service.flag("notifications.enabled") is False
    with pytest.raises(ValueError):
        service.flag("notifications.quiet_start")
    with pytest.raises(ValueError):
        service.flag("no.such_key")
    with sqlite_connection(store) as connection:
        connection.execute("UPDATE settings SET version=2 WHERE scope='app'")
        connection.commit()
    assert service.flag("routines.paused") is None
    with sqlite_connection(store) as connection:
        connection.execute(
            "UPDATE settings SET version=1, document=? WHERE scope='app'", ("not json",)
        )
        connection.commit()
    assert service.flag("routines.paused") is None
    with monkeypatch.context() as patch:
        patch.setattr(
            store, "get_settings", lambda: (_ for _ in ()).throw(StateError("unavailable"))
        )
        assert service.flag("routines.paused") is None


def test_flag_never_reads_provider_defaults(tmp_path: Path) -> None:
    def refuse() -> Prefs:
        raise AssertionError("provider_defaults read")

    store = _store(tmp_path)
    service = SettingsService(store, provider_defaults=refuse, on_chat_defaults=lambda p: None)
    assert service.flag("routines.paused") is False
    _service(store).patch(ident(), 0, {"routines.paused": True})
    assert service.flag("routines.paused") is True


@pytest.mark.parametrize(
    ("key", "minimum", "maximum", "default"),
    [("layout.left_rail_width", 220, 480, 272), ("layout.right_rail_width", 240, 560, 320)],
)
def test_rail_width_specs_defaults_and_admitted_boundaries(
    tmp_path: Path, key: str, minimum: int, maximum: int, default: int
) -> None:
    service = _service(_store(tmp_path))
    initial = service.read()
    spec = initial.keys[key]
    assert (spec.scope, spec.kind, spec.default, spec.applies, spec.personal) == (
        "app",
        "number",
        default,
        "now",
        False,
    )
    assert spec.allowed == tuple(range(minimum, maximum + 1))
    view: dict[str, Any] = settings_module.to_view(initial)
    assert view["effective"][key] == default
    assert view["sources"][key] == "default"
    assert view["keys"][key]["allowed"] == list(range(minimum, maximum + 1))
    for value in (minimum, maximum, float(default)):
        saved, replayed = service.patch(ident(), service.read().document.revision, {key: value})
        assert saved.effective[key] == value
        assert saved.sources[key] == "setting"
        assert replayed is False


@pytest.mark.parametrize(
    ("key", "minimum", "maximum"),
    [("layout.left_rail_width", 220, 480), ("layout.right_rail_width", 240, 560)],
)
def test_rail_width_invalid_values_do_not_change_the_row(
    tmp_path: Path, key: str, minimum: int, maximum: int
) -> None:
    service = _service(_store(tmp_path))
    before = service.read()
    for value, code in (
        (True, "invalid_type"),
        (False, "invalid_type"),
        (str(minimum), "invalid_type"),
        (minimum - 1, "not_allowed"),
        (maximum + 1, "not_allowed"),
        (minimum + 0.5, "not_allowed"),
        (float("nan"), "not_allowed"),
        (float("inf"), "not_allowed"),
    ):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), 0, {key: value})
        assert failure.value.fields == {key: code}
        assert service.read() == before


@pytest.mark.parametrize(
    ("key", "value", "default"),
    [("layout.left_rail_width", 480, 272), ("layout.right_rail_width", 560, 320)],
)
def test_rail_width_revision_replay_and_null_reset(
    tmp_path: Path, key: str, value: int, default: int
) -> None:
    store = _store(tmp_path)
    sink: list[Prefs] = []
    service = _service(store, sink=sink)
    saved, _ = service.patch(CID_A, 0, {key: value})
    row = store.get_settings()
    assert row is not None
    assert row["revision"] == 1
    assert json.loads(row["document"])[key] == value
    replayed, was_replayed = service.patch(CID_A, 0, {key: value})
    assert was_replayed and replayed.document.revision == saved.document.revision
    with pytest.raises(SettingsError) as repeated:
        service.patch(CID_A, 0, {key: default})
    assert repeated.value.code == "command_conflict"
    with pytest.raises(SettingsConflict) as failure:
        service.patch(CID_B, 0, {key: default})
    assert failure.value.current.effective[key] == value
    reset, _ = service.patch(CID_B, saved.document.revision, {key: None})
    assert reset.effective[key] == default
    assert reset.sources[key] == "default"
    assert key not in (reset.document.values or {})
    assert reset.document.revision == 2
    assert sink == []


@pytest.mark.parametrize(
    ("version", "document", "reason"),
    [(2, json.dumps({"layout.left_rail_width": 480}), "newer_version"), (1, "not json", "corrupt")],
)
def test_rail_width_patch_keeps_newer_or_corrupt_document_read_only(
    tmp_path: Path, version: int, document: str, reason: str
) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',?,1,?,?)",
            (version, document, "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    service = _service(store)
    before = service.read()
    assert before.document.reason == reason
    with pytest.raises(SettingsError) as failure:
        service.patch(CID_A, 1, {"layout.left_rail_width": 300})
    assert failure.value.code == "read_only"
    assert service.read() == before
    assert before.effective["layout.left_rail_width"] == 272
    assert before.effective["layout.right_rail_width"] == 320


def test_rail_widths_are_in_portable_settings_without_personal_identity(tmp_path: Path) -> None:
    from viki_agent.app.exports import ExportService

    service = _service(_store(tmp_path))
    widths = {"layout.left_rail_width": 300, "layout.right_rail_width": 410}
    service.patch(CID_A, 0, {**widths, "identity.display_name": "Synthetic User"})
    exporter = ExportService(
        diagnostics_document=lambda _paths, _identity: {},
        redact=lambda text, _roots: (text, 0),
        roots=lambda: (),
        settings_export=service.export_values,
        settings_specs=KEYS,
        workspaces_export=lambda: (),
        denied_roots=lambda: (),
        destination_guard=threading.RLock(),
        log_path=tmp_path / "unused.log",
        app_version="synthetic",
        launch_id="synthetic",
    )
    payload = json.loads(exporter.preview("settings", {}).content[1])
    assert payload["settings"]["values"] == widths
    assert payload["settings"]["version"] == settings_module.SETTINGS_VERSION


def test_layout_panes_key_has_its_spec(tmp_path: Path) -> None:
    spec = KEYS["layout.panes"]
    assert (spec.kind, spec.allowed, spec.default) == ("string", None, "")
    assert (spec.applies, spec.personal, spec.max_len) == ("now", True, 8192)
    assert _service(_store(tmp_path)).read().effective["layout.panes"] == ""


def test_layout_panes_is_kept_as_sent_without_nfc(tmp_path: Path) -> None:
    # Like a voice URI, a layout ref must round-trip unchanged: no NFC normalisation.
    nfd = "café"
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(ident(), 0, {"layout.panes": nfd})
    assert saved.effective["layout.panes"] == nfd


def test_layout_panes_length_boundary(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(ident(), 0, {"layout.panes": "x" * 8192})
    assert saved.effective["layout.panes"] == "x" * 8192
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), saved.document.revision, {"layout.panes": "x" * 8193})
    assert failure.value.fields == {"layout.panes": "too_long"}


def test_layout_panes_refuses_control_characters(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"layout.panes": "a\x00b"})
    assert failure.value.fields == {"layout.panes": "control_characters"}


def test_layout_panes_is_personal_and_omitted_from_portable_export(tmp_path: Path) -> None:
    from viki_agent.app.exports import ExportService

    service = _service(_store(tmp_path))
    service.patch(
        CID_A,
        0,
        {
            "layout.panes": '{"tree":"leaf"}',
            "bots.icons": f'{{"{CID_A}":"door"}}',
            "appearance.theme": "dark",
        },
    )
    exporter = ExportService(
        diagnostics_document=lambda _paths, _identity: {},
        redact=lambda text, _roots: (text, 0),
        roots=lambda: (),
        settings_export=service.export_values,
        settings_specs=KEYS,
        workspaces_export=lambda: (),
        denied_roots=lambda: (),
        destination_guard=threading.RLock(),
        log_path=tmp_path / "unused.log",
        app_version="synthetic",
        launch_id="synthetic",
    )
    payload = json.loads(exporter.preview("settings", {}).content[1])
    assert payload["settings"]["values"] == {"appearance.theme": "dark"}


_DECISION_BOOL_KEYS = {
    "decision.enabled": False,
    "decision.watch.tasks": True,
    "decision.watch.inbox": True,
    "decision.watch.vault_changes": True,
    "decision.watch.approvals": True,
    "decision.watch.chats": True,
    "decision.watch.routines": True,
    "decision.watch.improve": True,
    "decision.watch.kanban": True,
    "decision.watch.vault_health": True,
    "decision.watch.messaging": True,
    "decision.alerts": False,
    "decision.in_context": True,
    "decision.learning": True,
    "decision.team.handoffs": False,
}


def test_decision_bool_keys_have_their_specs_and_accept_both_values(tmp_path: Path) -> None:
    for key, default in _DECISION_BOOL_KEYS.items():
        spec = KEYS[key]
        assert (spec.kind, spec.default, spec.applies, spec.personal) == (
            "bool",
            default,
            "now",
            False,
        )
    service = _service(_store(tmp_path))
    revision = 0
    for key in _DECISION_BOOL_KEYS:
        for value in (True, False):
            saved, _replayed = service.patch(ident(), revision, {key: value})
            assert saved.effective[key] is value
            revision = saved.document.revision
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), revision, {key: "yes"})
        assert failure.value.fields == {key: "invalid_type"}


_DECISION_ENUM_KEYS = {
    "decision.send_level": (("titles", "excerpts", "sections"), "excerpts"),
    "decision.cadence": (("change", "5m", "15m"), "change"),
    "decision.model_version": (("pinned", "latest", "preview"), "pinned"),
    "decision.grant.prepare": (("off", "ask", "allowed"), "allowed"),
    "decision.grant.run": (("off", "ask", "allowed"), "ask"),
    "decision.shadow": (("off", "pinned", "latest", "preview"), "off"),
    "decision.plan_brake": (("off", "50", "80", "95"), "80"),
    "decision.grant.team": (("off", "ask", "allowed"), "ask"),
    "decision.grant.look": (("off", "ask", "allowed"), "allowed"),
    "decision.grant.second_look": (("off", "ask", "allowed"), "allowed"),
    "decision.grant.project_plan": (("off", "ask", "allowed"), "allowed"),
    "decision.grant.check": (("off", "ask", "allowed"), "ask"),
    "decision.grant.inbox_triage": (("off", "ask", "allowed"), "allowed"),
}


def test_decision_enum_keys_have_their_specs_and_accept_every_allowed_value(
    tmp_path: Path,
) -> None:
    service = _service(_store(tmp_path))
    revision = 0
    for key, (allowed, default) in _DECISION_ENUM_KEYS.items():
        spec = KEYS[key]
        assert (spec.kind, spec.allowed, spec.default, spec.applies, spec.personal) == (
            "enum",
            allowed,
            default,
            "now",
            False,
        )
        for value in allowed:
            saved, _replayed = service.patch(ident(), revision, {key: value})
            assert saved.effective[key] == value
            revision = saved.document.revision
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), revision, {key: "unknown"})
        assert failure.value.fields == {key: "not_allowed"}


_DECISION_NUMBER_KEYS = {
    "decision.daily_cap_usd": ((0.1, 0.25, 0.5, 1, 2), 0.5),
    "decision.nudges_per_hour": ((0, 1, 2), 2),
    "decision.limit.drafts": (tuple(range(11)), 5),
    "decision.limit.chats": (tuple(range(11)), 3),
    "decision.limit.changes": (tuple(range(11)), 3),
    "decision.limit.team": (tuple(range(11)), 4),
    "decision.limit.look": (tuple(range(11)), 3),
    "decision.limit.second_look": (tuple(range(11)), 3),
    "decision.limit.project_plan": (tuple(range(11)), 3),
    "decision.limit.message_draft": (tuple(range(11)), 3),
    "decision.limit.check": (tuple(range(11)), 3),
    "decision.limit.inbox_triage": (tuple(range(11)), 3),
}


def test_decision_number_keys_have_their_specs_and_accept_every_allowed_value(
    tmp_path: Path,
) -> None:
    service = _service(_store(tmp_path))
    revision = 0
    for key, (allowed, default) in _DECISION_NUMBER_KEYS.items():
        spec = KEYS[key]
        assert (spec.kind, spec.allowed, spec.default, spec.applies, spec.personal) == (
            "number",
            allowed,
            default,
            "now",
            False,
        )
        for value in allowed:
            saved, _replayed = service.patch(ident(), revision, {key: value})
            assert saved.effective[key] == value
            revision = saved.document.revision
        for bad in (max(allowed) + 1, True, str(default)):
            with pytest.raises(SettingsError) as failure:
                service.patch(ident(), revision, {key: bad})
            assert list(failure.value.fields) == [key]


_INSTANT_KEYS_UNDER_TEST = ("decision.paused_until", "decision.focus_until")


def test_decision_instant_keys_have_their_specs_and_accept_empty_or_an_exact_stamp(
    tmp_path: Path,
) -> None:
    service = _service(_store(tmp_path))
    revision = 0
    for key in _INSTANT_KEYS_UNDER_TEST:
        spec = KEYS[key]
        assert (spec.kind, spec.default, spec.applies, spec.personal, spec.max_len) == (
            "string",
            "",
            "now",
            False,
            24,
        )
        for value in ("2026-10-21T10:00:00.000Z", "2026-10-21T09:30:00.500Z", ""):
            saved, _replayed = service.patch(ident(), revision, {key: value})
            assert saved.effective[key] == value
            revision = saved.document.revision


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("2026-10-21T10:00:00Z", "not_allowed"),  # missing milliseconds: right range, wrong shape
        ("2026-10-21 10:00:00.000Z", "not_allowed"),  # space, not "T": fromisoformat round-trips
        ("2026-13-21T10:00:00.000Z", "not_allowed"),  # no such month
        ("2026-10-21T10:00:00.000+00:00", "too_long"),  # 29 characters, over max_len=24
        (1_761_040_800, "invalid_type"),  # an epoch int, not a string
    ],
)
def test_decision_instant_keys_refuse_other_shapes(
    tmp_path: Path, value: object, code: str
) -> None:
    service = _service(_store(tmp_path))
    for key in _INSTANT_KEYS_UNDER_TEST:
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), 0, {key: value})
        assert failure.value.fields == {key: code}


def test_decision_launcher_session_accepts_empty_or_a_canonical_uuid(tmp_path: Path) -> None:
    spec = KEYS["decision.launcher_session"]
    assert (spec.kind, spec.default, spec.applies, spec.personal, spec.max_len) == (
        "string",
        "",
        "now",
        True,
        36,
    )
    service = _service(_store(tmp_path))
    session_id = ident()  # never seeded as an avatar: proves this key sits outside _UUID_KEYS
    saved, _replayed = service.patch(CID_A, 0, {"decision.launcher_session": session_id})
    assert saved.effective["decision.launcher_session"] == session_id
    reset, _replayed = service.patch(
        CID_B, saved.document.revision, {"decision.launcher_session": ""}
    )
    assert reset.effective["decision.launcher_session"] == ""
    for bad, code in (
        (session_id.upper(), "not_allowed"),
        ("{" + session_id + "}", "too_long"),
        ("not-a-uuid", "not_allowed"),
        (42, "invalid_type"),
    ):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), reset.document.revision, {"decision.launcher_session": bad})
        assert failure.value.fields == {"decision.launcher_session": code}


def test_layout_launcher_accepts_empty_or_r_b_pixels_and_refuses_other_shapes(
    tmp_path: Path,
) -> None:
    spec = KEYS["layout.launcher"]
    assert (spec.kind, spec.default, spec.applies, spec.personal, spec.max_len) == (
        "string",
        "",
        "now",
        False,
        9,
    )
    service = _service(_store(tmp_path))
    revision = 0
    for value in ("", "0,0", "24,48", "9999,9999"):
        saved, _replayed = service.patch(ident(), revision, {"layout.launcher": value})
        assert saved.effective["layout.launcher"] == value
        revision = saved.document.revision
    for bad, code in (
        ("10", "not_allowed"),
        ("10,", "not_allowed"),
        ("-1,0", "not_allowed"),
        ("1.5,2", "not_allowed"),
        ("10,20,30", "not_allowed"),
        ("99999,9999", "too_long"),  # 10 characters, over max_len=9
    ):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), revision, {"layout.launcher": bad})
        assert failure.value.fields == {"layout.launcher": code}


def test_layout_launcher_panel_accepts_empty_or_w_h_pixels_and_refuses_other_shapes(
    tmp_path: Path,
) -> None:
    spec = KEYS["layout.launcher_panel"]
    assert (spec.kind, spec.default, spec.applies, spec.personal, spec.max_len) == (
        "string",
        "",
        "now",
        False,
        9,
    )
    service = _service(_store(tmp_path))
    revision = 0
    for value in ("", "0,0", "24,48", "9999,9999"):
        saved, _replayed = service.patch(ident(), revision, {"layout.launcher_panel": value})
        assert saved.effective["layout.launcher_panel"] == value
        revision = saved.document.revision
    for bad, code in (
        ("10", "not_allowed"),
        ("10,", "not_allowed"),
        ("-1,0", "not_allowed"),
        ("1.5,2", "not_allowed"),
        ("10,20,30", "not_allowed"),
        ("99999,9999", "too_long"),  # 10 characters, over max_len=9
    ):
        with pytest.raises(SettingsError) as failure:
            service.patch(ident(), revision, {"layout.launcher_panel": bad})
        assert failure.value.fields == {"layout.launcher_panel": code}


def test_layout_launcher_panel_rollback_survives_an_older_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build that still knows the key plants it; rolling back to a build that does not (the
    492d86fb base) must still show it in `document.values`, keep it off `effective`, and keep it
    through a later patch of another key."""
    store = _store(tmp_path)
    service = _service(store)
    planted, _replayed = service.patch(CID_A, 0, {"layout.launcher_panel": "640,720"})
    # setattr, not delitem: delitem's undo re-inserts the key at the dict's new end, reordering
    # the shared KEYS singleton for every later test; setattr's undo restores the original object.
    monkeypatch.setattr(
        settings_module,
        "KEYS",
        {k: v for k, v in settings_module.KEYS.items() if k != "layout.launcher_panel"},
    )
    read_back = service.read()
    assert "layout.launcher_panel" not in read_back.effective
    assert read_back.document.values is not None
    assert read_back.document.values["layout.launcher_panel"] == "640,720"
    patched, _replayed = service.patch(
        CID_B, planted.document.revision, {"appearance.theme": "dark"}
    )
    assert patched.document.values is not None
    assert patched.document.values["layout.launcher_panel"] == "640,720"
    assert patched.effective["appearance.theme"] == "dark"


def test_decision_never_send_key_has_its_spec(tmp_path: Path) -> None:
    spec = KEYS["decision.never_send"]
    assert (spec.kind, spec.allowed, spec.default) == ("string", None, "[]")
    assert (spec.applies, spec.personal, spec.max_len) == ("now", False, 4193)
    assert _service(_store(tmp_path)).read().effective["decision.never_send"] == "[]"


def test_decision_never_send_canonicalizes_drops_duplicates_and_keeps_order(
    tmp_path: Path,
) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(
        ident(),
        0,
        {"decision.never_send": '["#health", "Private/", "#health", "Clients/Acme/"]'},
    )
    assert saved.effective["decision.never_send"] == '["#health","Private/","Clients/Acme/"]'
    reset, _replayed = service.patch(
        ident(), saved.document.revision, {"decision.never_send": None}
    )
    assert reset.effective["decision.never_send"] == "[]"


def test_decision_never_send_keeps_non_ascii_entries_literal(tmp_path: Path) -> None:
    # Unlike voice.read_aloud_voices (an opaque browser id, kept as sent), a folder/tag name is
    # human text: NFC applies, but canonical JSON is not ASCII-escaped, so it stays readable.
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(ident(), 0, {"decision.never_send": '["日本/"]'})
    assert saved.effective["decision.never_send"] == '["日本/"]'


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("not json", "not_allowed"),
        ("{}", "not_allowed"),
        ("[1]", "not_allowed"),
        ('["Private"]', "not_allowed"),  # neither folder nor tag
        ('[""]', "not_allowed"),
        ('["/"]', "not_allowed"),  # leading "/"
        ('["a/../b/"]', "not_allowed"),  # ".." segment
        ('["#"]', "not_allowed"),  # empty tag
        ('["# health"]', "not_allowed"),  # whitespace in the tag
        ('["a\\u0007/"]', "not_allowed"),  # a control character once JSON-decoded
        (json.dumps([f"f{index}/" for index in range(33)]), "not_allowed"),  # 33 entries
        (json.dumps(["a" * 128 + "/"]), "not_allowed"),  # 129-character entry
    ],
)
def test_decision_never_send_refuses_bad_entries_and_shapes(
    tmp_path: Path, value: str, code: str
) -> None:
    service = _service(_store(tmp_path))
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"decision.never_send": value})
    assert failure.value.fields == {"decision.never_send": code}
    assert service.read() == before


def test_decision_never_send_admits_exactly_thirty_two_entries_of_128_characters(
    tmp_path: Path,
) -> None:
    entries = [f"{index:03d}" + "a" * 124 + "/" for index in range(32)]
    assert len(entries) == 32 and all(len(entry) == 128 for entry in entries)
    submitted = json.dumps(entries, separators=(",", ":"))  # compact: stays at max_len=4193
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(ident(), 0, {"decision.never_send": submitted})
    assert json.loads(str(saved.effective["decision.never_send"])) == entries


def test_decision_keys_sit_together_after_voice_speak_while_writing() -> None:
    keys = list(KEYS)
    start = keys.index("voice.speak_while_writing") + 1
    assert keys[start] == "decision.enabled"
    assert keys[-1] == "bots.icons"
    assert keys[-2] == "bots.hues"
    assert keys[-3] == "layout.launcher_panel"
    assert keys[-4] == "layout.launcher"
    assert keys[-5] == "decision.launcher_session"


def test_decision_plan_brake_sits_after_limit_changes() -> None:
    keys = list(KEYS)
    assert keys.index("decision.plan_brake") == keys.index("decision.limit.changes") + 1


def test_team_keys_default_to_ask_four_and_off_and_sit_in_order() -> None:
    keys = list(KEYS)
    assert keys.index("decision.grant.team") == keys.index("decision.grant.run") + 1
    assert keys.index("decision.limit.team") == keys.index("decision.plan_brake") + 1
    assert keys.index("decision.team.handoffs") == keys.index("decision.limit.team") + 1
    assert [KEYS[key].default for key in keys if key.endswith(".team")] == ["ask", 4]
    assert KEYS["decision.team.handoffs"].default is False


def test_decision_watch_improve_sits_after_watch_routines() -> None:
    keys = list(KEYS)
    assert keys.index("decision.watch.improve") == keys.index("decision.watch.routines") + 1


def test_decision_watch_kanban_sits_after_watch_improve() -> None:
    keys = list(KEYS)
    assert keys.index("decision.watch.kanban") == keys.index("decision.watch.improve") + 1


def test_bot_hues_map_is_stored_canonical_and_resets(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    saved, _replayed = service.patch(
        ident(), 0, {"bots.hues": '{ "' + CID_B + '": "green", "' + CID_A + '": "blue" }'}
    )
    assert saved.effective["bots.hues"] == f'{{"{CID_A}":"blue","{CID_B}":"green"}}'
    saved, _replayed = service.patch(ident(), saved.document.revision, {"bots.hues": None})
    assert saved.effective["bots.hues"] == "{}"


@pytest.mark.parametrize(
    "value",
    [
        "[]",
        f'{{"{CID_A}":"blue","{CID_A}":"green"}}',  # repeated key
        json.dumps({str(uuid.UUID(int=i)): "blue" for i in range(65)}),  # 65 entries
        f'{{"{CID_A.upper()}":"blue"}}',  # upper-case key
        '{"not-a-uuid":"blue"}',  # non-UUID key
        f'{{"{CID_A}":"black"}}',  # unknown hue
        f'{{"{CID_A}":NaN}}',
    ],
)
def test_bot_hues_refusals(tmp_path: Path, value: str) -> None:
    service = _service(_store(tmp_path))
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"bots.hues": value})
    assert failure.value.fields == {"bots.hues": "not_allowed"}


def test_bot_hues_stored_bad_value_reads_as_default(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',1,1,?,?)",
            (json.dumps({"bots.hues": "not json"}), "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    service = _service(store)
    assert service.read().effective["bots.hues"] == "{}"


def test_bot_icons_key_and_closed_ids(tmp_path: Path) -> None:
    spec = KEYS["bots.icons"]
    assert (spec.kind, spec.default, spec.applies, spec.personal, spec.max_len) == (
        "string",
        "{}",
        "now",
        True,
        4096,
    )
    icons = (
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
    )
    service = _service(_store(tmp_path))
    assert service.read().effective["bots.icons"] == "{}"
    values = {str(uuid.UUID(int=index)): icon for index, icon in enumerate(icons)}
    assert len(values) == 27
    saved, _replayed = service.patch(ident(), 0, {"bots.icons": json.dumps(values)})
    assert json.loads(str(saved.effective["bots.icons"])) == values


def test_bot_icons_map_round_trips_and_resets(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    submitted = '{ "' + CID_B + '": "target", "' + CID_A + '": "door" }'
    saved, _replayed = service.patch(ident(), 0, {"bots.icons": submitted})
    canonical = f'{{"{CID_A}":"door","{CID_B}":"target"}}'
    assert saved.effective["bots.icons"] == canonical
    assert saved.document.values is not None
    assert saved.document.values["bots.icons"] == canonical
    assert service.read().effective["bots.icons"] == canonical
    reset, _replayed = service.patch(ident(), saved.document.revision, {"bots.icons": None})
    assert reset.effective["bots.icons"] == "{}"
    assert reset.sources["bots.icons"] == "default"
    assert "bots.icons" not in (reset.document.values or {})


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        "[]",
        "null",
        f'{{"{CID_A}":"door","{CID_A}":"loop"}}',
        json.dumps({str(uuid.UUID(int=i)): "door" for i in range(65)}),
        f'{{"{CID_A.upper()}":"door"}}',
        '{"not-a-uuid":"door"}',
        f'{{"{CID_A}":"builder"}}',
        f'{{"{CID_A}":1}}',
        f'{{"{CID_A}":NaN}}',
    ],
)
def test_bot_icons_refusals(tmp_path: Path, value: str) -> None:
    service = _service(_store(tmp_path))
    before = service.read()
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"bots.icons": value})
    assert failure.value.fields == {"bots.icons": "not_allowed"}
    assert service.read() == before


def test_bot_icons_length_refusal(tmp_path: Path) -> None:
    service = _service(_store(tmp_path))
    with pytest.raises(SettingsError) as failure:
        service.patch(ident(), 0, {"bots.icons": " " * 4097})
    assert failure.value.fields == {"bots.icons": "too_long"}


def test_bot_icons_stored_bad_value_reads_as_default(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite_connection(store) as connection:
        connection.execute(
            "INSERT INTO settings (scope,version,revision,document,updated_at) "
            "VALUES ('app',1,1,?,?)",
            (json.dumps({"bots.icons": "not json"}), "2026-01-01T00:00:00.000Z"),
        )
        connection.commit()
    read_back = _service(store).read()
    assert read_back.effective["bots.icons"] == "{}"
    assert read_back.sources["bots.icons"] == "default"
