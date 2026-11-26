"""The workspace, vault and policy routes over a synthetic store; no port is ever bound."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient

from tests.app.test_workspaces import COMMAND, OTHER_COMMAND, make_junction, service_at, write_lf
from tests.profiles.test_profile_launch_api import HANG_GUARD_S
from tests.test_server import BEARER, ORIGIN, _p1_app
from tests.vaults.test_vault_storage import RESOURCES, _dying_mkdir, snapshot
from viki_agent import __main__ as launcher
from viki_agent import vault_storage
from viki_agent.app import folder_dialog, workspace_api
from viki_agent.app.folder_dialog import PickerError, PickerStatus
from viki_agent.app.settings import SettingsError, SettingsService
from viki_agent.app.workspace_api import workspace_routes
from viki_agent.domain import SCRATCH_WORKSPACE_ID
from viki_agent.protection import ProtectionError
from viki_agent.storage import StateError
from viki_agent.vault_storage import VaultBootstrap
from viki_agent.workspaces import CODE_EDITOR_REFUSES, WorkspaceError, WorkspaceService

BASE = "http://127.0.0.1:8765"
TOKEN = "workspace-test-token"
AUTH = {"authorization": f"Bearer {TOKEN}"}
MUTATION = {**AUTH, "origin": BASE}


def app_at(
    tmp_path: Path,
    service: WorkspaceService | None = None,
    *,
    pick: Callable[[str, bool], list[str] | None] | None = None,
    picker_status: Callable[[], PickerStatus] | None = None,
    workers: set[asyncio.Task[Any]] | None = None,
) -> tuple[Any, WorkspaceService]:
    workspaces = service if service is not None else service_at(tmp_path)
    bootstrap = VaultBootstrap(workspaces, resources=RESOURCES, home=tmp_path / "home")

    def authorized(request: Request) -> bool:
        return request.headers.get("authorization") == f"Bearer {TOKEN}"

    routes = workspace_routes(
        workspaces,
        bootstrap,
        authorized=authorized,
        origins=frozenset({BASE}),
        workers=set() if workers is None else workers,
        pick=pick,
        picker_status=picker_status,
    )
    return Starlette(routes=routes), workspaces


def test_every_route_requires_the_token_and_mutations_require_the_origin(tmp_path: Path) -> None:
    app, _ = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    for method, path in (
        ("get", "/api/v2/workspaces"),
        ("get", "/api/v2/vaults/suggestion"),
        ("get", "/api/v2/vaults/x/starter"),
        ("post", "/api/v2/workspaces/select"),
        ("post", "/api/v2/vaults/create"),
        ("post", "/api/v2/vaults/open"),
    ):
        answer = client.request(method.upper(), path, json={})
        assert answer.status_code == 401, path
        assert answer.json()["error"]["code"] == "unauthorized"
    for path in ("/api/v2/vaults/create", "/api/v2/vaults/open", "/api/v2/workspaces/select"):
        answer = client.post(path, headers=AUTH, json={})
        assert answer.status_code == 403
        assert answer.json()["error"]["code"] == "forbidden_origin"
    stranger = client.get("/api/v2/workspaces", headers={**AUTH, "origin": "http://evil"})
    assert stranger.status_code == 403
    assert stranger.json()["error"]["code"] == "forbidden_origin"


def test_listing_names_the_scratch_workspace_and_the_active_selection(tmp_path: Path) -> None:
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    empty = client.get("/api/v2/workspaces", headers=AUTH).json()
    assert empty["workspaces"] == [] and empty["chat_only"] is True
    assert empty["active_id"] == SCRATCH_WORKSPACE_ID and empty["selected_by"] == "builtin"
    assert empty["scratch"] == {
        "workspace_id": SCRATCH_WORKSPACE_ID,
        "kind": "scratch",
        "shared": True,
    }

    root = tmp_path / "vault"
    root.mkdir()
    opened = client.post(
        "/api/v2/vaults/open", headers=MUTATION, json={"command_id": COMMAND, "path": str(root)}
    )
    assert opened.status_code == 201
    workspace = opened.json()["workspace"]
    assert workspace["policy"] == "existing_readonly" and workspace["root"] == str(root)
    assert workspace["root_available"] is True and workspace["aliased"] is False
    assert opened.json()["inspection"]["workspace_id"] == workspace["workspace_id"]

    again = client.post(
        "/api/v2/vaults/open",
        headers=MUTATION,
        json={"command_id": OTHER_COMMAND, "path": str(root)},
    )
    assert again.status_code == 200
    assert again.json()["workspace"]["workspace_id"] == workspace["workspace_id"]

    chosen = client.post(
        "/api/v2/workspaces/select",
        headers=MUTATION,
        json={"command_id": COMMAND, "workspace_id": workspace["workspace_id"]},
    )
    assert chosen.status_code == 200
    assert chosen.json() == {
        "selected_id": workspace["workspace_id"],
        "active_id": SCRATCH_WORKSPACE_ID,  # this launch keeps the workspace it started with
        "restart_required": True,
    }
    listing = client.get("/api/v2/workspaces", headers=AUTH).json()
    assert listing["selected_id"] == workspace["workspace_id"]
    single = client.get(f"/api/v2/workspaces/{workspace['workspace_id']}", headers=AUTH)
    assert single.json()["workspace_id"] == workspace["workspace_id"]
    missing = client.get(f"/api/v2/workspaces/{uuid.uuid4()}", headers=AUTH)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "unknown_workspace"


def test_create_suggestion_replay_and_cancel(tmp_path: Path) -> None:
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    suggestion = client.get("/api/v2/vaults/suggestion", headers=AUTH, params={"name": "My Vault"})
    assert suggestion.json() == {
        "parent": str(tmp_path / "home" / "VIKI" / "Vaults"),
        "folder": "My Vault",
        "path": str(tmp_path / "home" / "VIKI" / "Vaults" / "My Vault"),
    }
    bad = client.get("/api/v2/vaults/suggestion", headers=AUTH, params={"name": "   "})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_name"

    created = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": COMMAND, "name": "My Vault", "portable_metadata": True},
    )
    assert created.status_code == 201
    assert created.json()["created_files"] == ["Welcome.md", ".viki/vault.json"]
    workspace = created.json()["workspace"]
    assert workspace["policy"] == "ordinary" and workspace["bootstrap"] == "complete"
    replay = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": COMMAND, "name": "My Vault", "portable_metadata": True},
    )
    assert replay.status_code == 200
    assert replay.json()["workspace"]["workspace_id"] == workspace["workspace_id"]

    clash = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": OTHER_COMMAND, "name": "My Vault"},
    )
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "destination_not_empty"
    assert clash.json()["error"]["hint"] == "open_existing"

    cancelled = client.post(
        f"/api/v2/vaults/{workspace['workspace_id']}/cancel-bootstrap",
        headers=MUTATION,
        json={"command_id": OTHER_COMMAND},
    )
    assert cancelled.status_code == 409
    assert cancelled.json()["error"]["code"] == "not_app_created"
    assert (Path(workspace["root"]) / "Welcome.md").is_file()


def test_create_with_the_viki_template_writes_the_starter_set(tmp_path: Path) -> None:
    app, _ = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    created = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": COMMAND, "name": "My Vault", "template": "viki"},
    )
    assert created.status_code == 201
    assert created.json()["created_files"] == [*vault_storage.FILES, vault_storage.MANIFEST]
    assert created.json()["workspace"]["bootstrap"] == "complete"


def test_starter_route_reports_current_none_and_refuses_an_unknown_workspace(
    tmp_path: Path,
) -> None:
    app, _ = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)

    viki = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": COMMAND, "name": "Viki Vault", "template": "viki"},
    )
    viki_id = viki.json()["workspace"]["workspace_id"]
    current = client.get(f"/api/v2/vaults/{viki_id}/starter", headers=AUTH)
    assert current.status_code == 200
    assert current.json()["state"] == "current"
    assert current.json()["items"] == [] and current.json()["notify"] is False

    blank = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": OTHER_COMMAND, "name": "Blank Vault"},
    )
    blank_id = blank.json()["workspace"]["workspace_id"]
    none = client.get(f"/api/v2/vaults/{blank_id}/starter", headers=AUTH)
    assert none.status_code == 200
    assert none.json() == {
        "state": "none",
        "from_version": None,
        "to_version": vault_storage.STARTER_VERSION,
        "notify": False,
        "items": [],
        "agents": None,
    }

    missing = client.get(f"/api/v2/vaults/{uuid.uuid4()}/starter", headers=AUTH)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "unknown_workspace"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_starter_route_never_reads_a_manifest_through_a_junction(tmp_path: Path) -> None:
    app, workspaces = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    created = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": COMMAND, "name": "Viki Vault", "template": "viki"},
    )
    workspace_id = created.json()["workspace"]["workspace_id"]
    root = Path(workspaces.get(workspace_id).root.display)
    outside = tmp_path / "outside-viki"
    shutil.copytree(root / ".viki", outside)
    shutil.rmtree(root / ".viki")
    make_junction(root / ".viki", outside)

    plan = client.get(f"/api/v2/vaults/{workspace_id}/starter", headers=AUTH)
    assert plan.status_code == 200
    assert plan.json()["state"] == "none"


def test_a_pending_bootstrap_row_never_breaks_the_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    destination = tmp_path / "home" / "VIKI" / "Vaults" / "Vault"

    # A death during the write (full disk, a killed process) leaves the reservation pending:
    # `root_physical_id` is `pending:<command uuid>`, never `<digits>:<digits>`.
    monkeypatch.setattr(vault_storage, "_mkdir", _dying_mkdir(destination))
    failed = client.post(
        "/api/v2/vaults/create", headers=MUTATION, json={"command_id": COMMAND, "name": "Vault"}
    )
    assert failed.status_code == 503
    monkeypatch.undo()
    assert not destination.exists()

    pending = service.by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending"

    listing = client.get("/api/v2/workspaces", headers=AUTH)
    assert listing.status_code == 200
    rows = listing.json()["workspaces"]
    assert [row["workspace_id"] for row in rows] == [pending.workspace_id]
    assert rows[0]["bootstrap"] == "pending"
    assert "root_physical_id" not in rows[0]

    single = client.get(f"/api/v2/workspaces/{pending.workspace_id}", headers=AUTH)
    assert single.status_code == 200
    assert "root_physical_id" not in single.json()

    # A registered vault still carries the key, and still parses as `<digits>:<digits>`.
    other = tmp_path / "other"
    other.mkdir()
    opened = client.post(
        "/api/v2/vaults/open",
        headers=MUTATION,
        json={"command_id": OTHER_COMMAND, "path": str(other)},
    )
    assert opened.status_code == 201
    complete_listing = client.get("/api/v2/workspaces", headers=AUTH).json()["workspaces"]
    complete = next(row for row in complete_listing if row["workspace_id"] != pending.workspace_id)
    assert re.fullmatch(r"[0-9]+:[0-9]+", complete["root_physical_id"])


def test_policy_inspection_and_admission_over_http(tmp_path: Path) -> None:
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    root = tmp_path / "vault"
    root.mkdir()
    write_lf(root / "CLAUDE.md", "# synthetic\n")
    opened = client.post(
        "/api/v2/vaults/open", headers=MUTATION, json={"command_id": COMMAND, "path": str(root)}
    )
    workspace_id = opened.json()["workspace"]["workspace_id"]
    inspection = client.get(f"/api/v2/vaults/{workspace_id}/policy", headers=AUTH).json()
    assert inspection["acknowledgements_required"] == ["instructions_present"]
    assert inspection["instructions"][0]["head"] == "# synthetic\n"
    assert inspection["excluded_from_writes"] == ["CLAUDE.md", ".claude/", ".git/"]

    code_root = tmp_path / "code"
    code_root.mkdir()
    code_opened = client.post(
        "/api/v2/workspaces/code",
        headers=MUTATION,
        json={"command_id": COMMAND, "path": str(code_root)},
    )
    code_id = code_opened.json()["workspace"]["workspace_id"]
    code_inspection = client.get(f"/api/v2/vaults/{code_id}/policy", headers=AUTH).json()
    assert code_inspection["excluded_from_writes"] == list(CODE_EDITOR_REFUSES)

    stale = client.patch(
        f"/api/v2/vaults/{workspace_id}/policy",
        headers=MUTATION,
        json={
            "command_id": COMMAND,
            "expected_policy_revision": 5,
            "policy": "ordinary",
            "acknowledged": ["instructions_present"],
        },
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_revision"

    admitted = client.patch(
        f"/api/v2/vaults/{workspace_id}/policy",
        headers=MUTATION,
        json={
            "command_id": COMMAND,
            "expected_policy_revision": 1,
            "policy": "ordinary",
            "acknowledged": ["instructions_present"],
        },
    )
    assert admitted.status_code == 200
    assert admitted.json() == {
        "policy": "ordinary",
        "policy_revision": 2,
        "revision": 2,
        "note_write": False,
    }


def test_policy_patch_reports_whether_notes_are_writable(tmp_path: Path) -> None:
    """`note_write` is the effective capability: only an ordinary, complete, persistent vault."""
    app, _ = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    created = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": COMMAND, "name": "Vault"},
    )
    workspace = created.json()["workspace"]
    assert workspace["bootstrap"] == "complete"
    policy = f"/api/v2/vaults/{workspace['workspace_id']}/policy"
    revision = workspace["policy_revision"]
    for target, writable in (
        ("existing_readonly", False),
        ("ordinary", True),  # the note publication backend now runs on every OS
        ("protected", False),
    ):
        answer = client.patch(
            policy,
            headers=MUTATION,
            json={
                "command_id": str(uuid.uuid4()),
                "expected_policy_revision": revision,
                "policy": target,
                "acknowledged": [],
            },
        )
        assert answer.status_code == 200, (target, answer.text)
        assert answer.json()["policy"] == target
        assert answer.json()["note_write"] is writable, target
        revision = answer.json()["policy_revision"]


def test_protected_root_via_alias_stays_protected_and_untouched(tmp_path: Path) -> None:
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    vault = tmp_path / "VIKI"
    vault.mkdir()
    (vault / "CLAUDE.md").write_text("# governed\n", encoding="utf-8")
    (vault / "note.md").write_text("owner's note\n", encoding="utf-8")
    opened = client.post(
        "/api/v2/vaults/open", headers=MUTATION, json={"command_id": COMMAND, "path": str(vault)}
    )
    workspace_id = opened.json()["workspace"]["workspace_id"]
    service.set_policy(COMMAND, workspace_id, 1, "protected", [])
    before = snapshot(vault)

    # The alias mechanisms are per OS: a junction and an upper-case spelling are Windows-only;
    # the upper-case spelling also reaches the row on macOS, since the key casefolds and the
    # display comes from the disk, but not on case-sensitive Linux.
    if sys.platform == "win32":
        link = tmp_path / "alias"
        make_junction(link, vault)
        aliases = [str(link), str(vault).upper(), str(vault)]
    elif sys.platform == "darwin":
        aliases = [str(vault).upper(), str(vault)]
    else:
        aliases = [str(vault)]
    for raw in aliases:
        through = client.post(
            "/api/v2/vaults/open",
            headers=MUTATION,
            json={"command_id": str(uuid.uuid4()), "path": raw},
        )
        assert through.status_code == 200, raw
        assert through.json()["workspace"]["workspace_id"] == workspace_id
        assert through.json()["workspace"]["policy"] == "protected"

    if sys.platform != "win32":
        symlinked = tmp_path / "symlink-alias"
        os.symlink(vault, symlinked, target_is_directory=True)
        through_symlink = client.post(
            "/api/v2/vaults/open",
            headers=MUTATION,
            json={"command_id": str(uuid.uuid4()), "path": str(symlinked)},
        )
        assert through_symlink.status_code == 400
        assert through_symlink.json()["error"]["code"] == "unsafe_path"

    refused = client.patch(
        f"/api/v2/vaults/{workspace_id}/policy",
        headers=MUTATION,
        json={
            "command_id": COMMAND,
            "expected_policy_revision": 2,
            "policy": "ordinary",
            "acknowledged": ["instructions_present"],
        },
    )
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "protected_root"

    inside = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": OTHER_COMMAND, "name": "child", "parent": str(vault)},
    )
    assert inside.status_code == 403
    assert inside.json()["error"]["code"] == "protected_overlap"

    assert snapshot(vault) == before
    assert sorted(path.name for path in vault.iterdir()) == ["CLAUDE.md", "note.md"]
    assert len(service.list()) == 1


def test_bodies_are_bounded_and_strict(tmp_path: Path) -> None:
    app, _ = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    for payload in (
        {"command_id": COMMAND},
        {"command_id": COMMAND, "path": str(tmp_path), "x": 1},
    ):
        answer = client.post("/api/v2/vaults/open", headers=MUTATION, json=payload)
        assert answer.status_code == 400
        assert answer.json()["error"]["code"] == "invalid_body"
    short = client.post(
        "/api/v2/vaults/open", headers=MUTATION, json={"command_id": "x", "path": "C:\\x"}
    )
    assert short.status_code == 400 and short.json()["error"]["code"] == "invalid_body"
    oversize = client.post(
        "/api/v2/vaults/open",
        headers=MUTATION,
        json={"command_id": COMMAND, "path": "C:\\" + "x" * (32 << 10)},
    )
    assert oversize.status_code == 413
    plain = client.post(
        "/api/v2/vaults/open",
        headers={**MUTATION, "content-type": "text/plain"},
        content=b"{}",
    )
    assert plain.status_code == 415


def test_a_store_refusal_is_typed_and_a_constraint_is_the_caller_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    root = tmp_path / "vault"
    root.mkdir()

    # A protection failure answers this route's own fixed message, not the raising module's.
    def refuse_protection(*args: Any, **kwargs: Any) -> None:
        raise ProtectionError()

    monkeypatch.setattr(service, "register_existing", refuse_protection)
    protection = client.post(
        "/api/v2/vaults/open",
        headers=MUTATION,
        json={"command_id": COMMAND, "path": str(root)},
    )
    assert protection.status_code == 503
    assert protection.json()["error"]["code"] == "unavailable"
    assert protection.json()["error"]["message"] == workspace_api._PROTECTION_MESSAGE
    assert "draft" not in protection.text.lower()

    for code, status in (("invalid_input", 400), ("busy", 503)):

        def refuse(*args: Any, _code: str = code, **kwargs: Any) -> None:
            raise StateError(_code)

        monkeypatch.setattr(service, "register_existing", refuse)
        answer = client.post(
            "/api/v2/vaults/open",
            headers=MUTATION,
            json={"command_id": COMMAND, "path": str(root)},
        )
        assert answer.status_code == status, code
        assert answer.json()["error"]["code"] == code


def test_a_file_at_the_destination_answers_409_not_500(tmp_path: Path) -> None:
    app, _ = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    parents = tmp_path / "parents"
    parents.mkdir()
    (parents / "Taken").write_text("a file where a folder would go\n", encoding="utf-8")
    answer = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": COMMAND, "name": "Taken", "parent": str(parents)},
    )
    assert answer.status_code == 409
    assert answer.json()["error"] == {
        "code": "destination_not_empty",
        "message": answer.json()["error"]["message"],
        "hint": "open_existing",
    }
    assert "Taken" not in answer.text  # no path, no traceback


def test_select_reports_the_selection_the_store_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    root = tmp_path / "vault"
    root.mkdir()
    opened = client.post(
        "/api/v2/vaults/open", headers=MUTATION, json={"command_id": COMMAND, "path": str(root)}
    )
    workspace_id = opened.json()["workspace"]["workspace_id"]
    # A store that kept its previous selection (the accessor restores it for an unknown id)
    # must not be reported back as though the request had taken effect.
    monkeypatch.setattr(service, "select", lambda command_id, target: None)
    answer = client.post(
        "/api/v2/workspaces/select",
        headers=MUTATION,
        json={"command_id": COMMAND, "workspace_id": workspace_id},
    )
    assert answer.status_code == 200
    assert answer.json()["selected_id"] is None
    # No selection and the scratch launch is the baseline, so nothing needs a restart either.
    assert answer.json()["restart_required"] is False


def test_select_computes_restart_required_the_way_capabilities_does(tmp_path: Path) -> None:
    """A fixed `true` told the user to restart after re-selecting the workspace already
    running. The rule is the one `/api/v2/capabilities` applies to the same store: a restart is
    needed only when the persisted selection differs from the launch's active workspace."""
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    root = tmp_path / "vault"
    root.mkdir()
    opened = client.post(
        "/api/v2/vaults/open", headers=MUTATION, json={"command_id": COMMAND, "path": str(root)}
    )
    workspace_id = opened.json()["workspace"]["workspace_id"]

    def select(target: str | None) -> dict[str, Any]:
        answer = client.post(
            "/api/v2/workspaces/select",
            headers=MUTATION,
            json={"command_id": str(uuid.uuid4()), "workspace_id": target},
        )
        assert answer.status_code == 200
        return answer.json()

    assert service.resolve_active().workspace.workspace_id == SCRATCH_WORKSPACE_ID
    assert select(workspace_id)["restart_required"] is True
    assert select(None) == {
        "selected_id": None,
        "active_id": SCRATCH_WORKSPACE_ID,
        "restart_required": False,
    }


def test_a_field_of_the_wrong_type_is_a_malformed_body_not_a_domain_refusal(
    tmp_path: Path,
) -> None:
    """The domain codes send the user after a path, a revision or a workspace. A number where
    a string belongs is none of those: it is the body, and the code has to say so."""
    app, _ = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    root = tmp_path / "vault"
    root.mkdir()
    opened = client.post(
        "/api/v2/vaults/open", headers=MUTATION, json={"command_id": COMMAND, "path": str(root)}
    )
    workspace_id = opened.json()["workspace"]["workspace_id"]
    policy = f"/api/v2/vaults/{workspace_id}/policy"
    good_policy = {
        "command_id": COMMAND,
        "expected_policy_revision": 1,
        "policy": "ordinary",
        "acknowledged": [],
    }
    create = {"command_id": COMMAND, "name": "v", "parent": str(tmp_path)}
    for method, path, payload in (
        ("POST", "/api/v2/workspaces/select", {"command_id": COMMAND, "workspace_id": 5}),
        ("POST", "/api/v2/vaults/open", {"command_id": COMMAND, "path": 5}),
        ("POST", "/api/v2/vaults/open", {"command_id": 5, "path": str(root)}),
        ("POST", "/api/v2/vaults/create", {**create, "name": 5}),
        ("POST", "/api/v2/vaults/create", {**create, "folder": 5}),
        # A vault has no language of its own: the field is refused like any unknown one.
        ("POST", "/api/v2/vaults/create", {**create, "language": "en"}),
        ("POST", "/api/v2/vaults/create", {**create, "portable_metadata": "yes"}),
        ("POST", "/api/v2/vaults/create", {**create, "template": "pro"}),
        ("PATCH", policy, {**good_policy, "expected_policy_revision": "1"}),
        ("PATCH", policy, {**good_policy, "expected_policy_revision": True}),
        ("PATCH", policy, {**good_policy, "policy": "writable"}),
        ("PATCH", policy, {**good_policy, "acknowledged": "instructions_present"}),
    ):
        answer = client.request(method, path, headers=MUTATION, json=payload)
        assert answer.status_code == 400, (path, payload, answer.text)
        assert answer.json()["error"]["code"] == "invalid_body", (path, payload)
    # A well-formed request the service refuses keeps its domain code.
    stale = client.patch(
        policy, headers=MUTATION, json={**good_policy, "expected_policy_revision": 7}
    )
    assert stale.json()["error"]["code"] == "stale_revision"
    assert not (tmp_path / "v").exists()


@pytest.mark.parametrize("template", ["blank", "viki"])
def test_a_created_vault_is_english(tmp_path: Path, template: str) -> None:
    app, _ = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)
    created = client.post(
        "/api/v2/vaults/create",
        headers=MUTATION,
        json={"command_id": COMMAND, "name": "Vault", "template": template},
    )
    assert created.status_code == 201
    root = Path(created.json()["workspace"]["root"])
    assert (root / "Welcome.md").read_text(encoding="utf-8").startswith("# Welcome")
    if template == "viki":
        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        assert "- Reply language: English\n" in agents


def test_starter_identity_reads_settings_and_falls_back_when_unreadable() -> None:
    """`__main__._starter_identity` is the identity callable `VaultBootstrap`'s starter writes
    with: the two `identity.*` settings when they can be read, `VaultBootstrap`'s own default
    pair otherwise."""
    readable = cast(
        SettingsService,
        SimpleNamespace(
            read=lambda: SimpleNamespace(
                effective={"identity.assistant_name": "Ada", "identity.display_name": "Sam Lee"}
            )
        ),
    )
    assert launcher._starter_identity(readable) == ("Ada", "Sam Lee")

    def _refused() -> Any:
        raise SettingsError("read_only")

    unreadable = cast(SettingsService, SimpleNamespace(read=_refused))
    assert launcher._starter_identity(unreadable) == ("VIKI", "")


# --- the folder picker and removing a code folder ---------------------------------------------

PICK = "/api/v2/folders/pick"
PICK_BODY = {"multiple": True, "title": "Choose folders"}
AVAILABLE = PickerStatus(True, True, None)


class FakeDialog:
    """`folder_dialog.pick` without a dialog: records each (title, multiple) and answers
    `paths`, None meaning the user cancelled."""

    def __init__(self, paths: list[str] | None) -> None:
        self.paths = paths
        self.asked: list[tuple[str, bool]] = []

    def __call__(self, title: str, multiple: bool) -> list[str] | None:
        self.asked.append((title, multiple))
        return self.paths


def _refusal(answer: Any) -> tuple[int, str]:
    return answer.status_code, answer.json()["error"]["code"]


def test_pick_returns_paths_and_registers_nothing(tmp_path: Path) -> None:
    """The chosen folders come back as paths and nothing is registered: that stays with
    `vaults/open` and `workspaces/code`. A cancelled dialog is `[]`."""
    chosen = [tmp_path / "a", tmp_path / "b"]
    for folder in chosen:
        folder.mkdir()
    dialog = FakeDialog([str(folder) for folder in chosen])
    app, service = app_at(tmp_path, pick=dialog, picker_status=lambda: AVAILABLE)
    client = TestClient(app, base_url=BASE)
    picked = client.post(PICK, headers=MUTATION, json=PICK_BODY)
    assert picked.status_code == 200
    assert picked.json() == {"paths": [str(folder) for folder in chosen]}
    dialog.paths = None
    cancelled = client.post(PICK, headers=MUTATION, json={"multiple": False, "title": "フォルダ"})
    assert cancelled.status_code == 200 and cancelled.json() == {"paths": []}
    assert dialog.asked == [("Choose folders", True), ("フォルダ", False)]
    assert service.list(kind=None) == []


def test_the_picker_status_says_what_this_computer_can_show(tmp_path: Path) -> None:
    one_at_a_time = PickerStatus(True, False, None)
    app, _ = app_at(tmp_path / "kdialog", pick=FakeDialog([]), picker_status=lambda: one_at_a_time)
    status = TestClient(app, base_url=BASE).get(PICK, headers=AUTH)
    assert status.status_code == 200
    assert status.json() == {"available": True, "multiple": False, "reason": None}
    bare, _ = app_at(tmp_path / "bare")
    unsupported = TestClient(bare, base_url=BASE).get(PICK, headers=AUTH)
    assert unsupported.json() == {"available": False, "multiple": False, "reason": "unsupported"}


def test_the_picker_needs_the_token_and_a_pick_needs_the_origin(tmp_path: Path) -> None:
    dialog = FakeDialog([])
    app, _ = app_at(tmp_path, pick=dialog, picker_status=lambda: AVAILABLE)
    client = TestClient(app, base_url=BASE)
    for method in ("GET", "POST"):
        assert _refusal(client.request(method, PICK, json=PICK_BODY)) == (401, "unauthorized")
    stranger = {**AUTH, "origin": "http://evil"}
    for method, headers in (("POST", AUTH), ("POST", stranger), ("GET", stranger)):
        answer = client.request(method, PICK, headers=headers, json=PICK_BODY)
        assert _refusal(answer) == (403, "forbidden_origin"), (method, headers)
    assert dialog.asked == []


def test_a_pick_body_is_strict(tmp_path: Path) -> None:
    dialog = FakeDialog([])
    app, _ = app_at(tmp_path, pick=dialog, picker_status=lambda: AVAILABLE)
    client = TestClient(app, base_url=BASE)
    for payload in (
        {"multiple": True},
        {"title": "Choose folders"},
        {**PICK_BODY, "x": 1},
        {**PICK_BODY, "multiple": "yes"},
        {**PICK_BODY, "multiple": 1},
        {**PICK_BODY, "multiple": None},
        {**PICK_BODY, "title": 5},
        {**PICK_BODY, "title": None},
        {**PICK_BODY, "title": ""},
        {**PICK_BODY, "title": "x" * 121},
        {**PICK_BODY, "title": "-e tell app"},
        {**PICK_BODY, "title": "Choose\nfolders"},
        {**PICK_BODY, "title": "Choose\x7ffolders"},
    ):
        answer = client.post(PICK, headers=MUTATION, json=payload)
        assert _refusal(answer) == (400, "invalid_body"), payload
    assert dialog.asked == []
    longest = client.post(PICK, headers=MUTATION, json={**PICK_BODY, "title": "x" * 120})
    assert longest.status_code == 200 and dialog.asked == [("x" * 120, True)]


def test_a_picker_that_cannot_show_or_fails_answers_503_and_logs_no_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    dialog = FakeDialog([])
    no_display = PickerStatus(False, False, "no_display")
    for name, app in (
        ("bare", app_at(tmp_path / "bare")[0]),
        ("pick only", app_at(tmp_path / "pick", pick=dialog)[0]),
        ("status only", app_at(tmp_path / "status", picker_status=lambda: AVAILABLE)[0]),
        ("no display", app_at(tmp_path / "x", pick=dialog, picker_status=lambda: no_display)[0]),
    ):
        answer = TestClient(app, base_url=BASE).post(PICK, headers=MUTATION, json=PICK_BODY)
        assert answer.status_code == 503, name
        assert answer.json()["error"] == {
            "code": "picker_unavailable",
            "message": "This computer can't show a folder picker. Type the path instead.",
        }, name
    assert dialog.asked == []

    def broken(title: str, multiple: bool) -> list[str] | None:
        raise PickerError("failed")

    caplog.set_level(logging.WARNING, logger=workspace_api.__name__)
    app, _ = app_at(tmp_path / "broken", pick=broken, picker_status=lambda: AVAILABLE)
    client = TestClient(app, base_url=BASE)
    title = "Choose the secret-project folders"
    failed = client.post(PICK, headers=MUTATION, json={**PICK_BODY, "title": title})
    assert failed.status_code == 503
    assert failed.json()["error"] == {
        "code": "picker_failed",
        "message": "The folder picker couldn't open. Type the path instead.",
    }
    logged = [record for record in caplog.records if record.name == workspace_api.__name__]
    assert [record.levelno for record in logged] == [logging.WARNING]
    assert title not in caplog.text and str(tmp_path) not in caplog.text
    # The failed dialog gave its lock back: the next pick fails the same way, never busy.
    assert _refusal(client.post(PICK, headers=MUTATION, json=PICK_BODY)) == (503, "picker_failed")


async def test_a_second_pick_is_busy_until_the_first_dialog_closes_even_after_its_client_left(
    tmp_path: Path,
) -> None:
    """One dialog at a time: the lock belongs to the open dialog, not to its request, so a
    client that leaves does not free it; the dialog closing does."""
    entered, close = threading.Event(), threading.Event()
    asked: list[str] = []

    def pick(title: str, multiple: bool) -> list[str] | None:
        asked.append(title)
        if len(asked) == 1:
            entered.set()
            close.wait(HANG_GUARD_S)
            return [str(tmp_path)]
        return None

    workers: set[asyncio.Task[Any]] = set()
    app, _ = app_at(tmp_path, pick=pick, picker_status=lambda: AVAILABLE, workers=workers)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:

        async def post() -> httpx.Response:
            return await client.post(PICK, headers=MUTATION, json=PICK_BODY)

        first = asyncio.create_task(post())
        try:
            assert await asyncio.to_thread(entered.wait, HANG_GUARD_S)
            [dialog] = workers
            busy = await post()
            assert busy.status_code == 409
            assert busy.json()["error"] == {
                "code": "picker_busy",
                "message": "A folder picker is already open.",
            }
            first.cancel()  # the client leaves; its dialog stays open
            with pytest.raises(asyncio.CancelledError):
                await first
            assert _refusal(await post()) == (409, "picker_busy")
        finally:
            close.set()  # never leave the worker thread parked on a failed assertion
        await asyncio.wait([dialog], timeout=HANG_GUARD_S)
        assert json.loads(dialog.result().body) == {"paths": [str(tmp_path)]}
        again = await post()
        assert again.status_code == 200 and again.json() == {"paths": []}
    assert asked == ["Choose folders", "Choose folders"] and not workers


def test_the_app_picks_through_the_os_dialog_owned_by_the_app_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_app` serves `folder_dialog.status` and picks through `folder_dialog.pick`, owned
    by the window `owner_window` names; with no window manager it has no window to claim."""
    seen: list[object] = []

    def owner_window(holds: Iterable[Callable[[int], bool]]) -> int | None:
        seen.append(list(holds))
        return 7

    def pick(title: str, multiple: bool, owner: int | None = None) -> list[str] | None:
        seen.append((title, multiple, owner))
        return [r"C:\work\a"]

    monkeypatch.setattr(folder_dialog, "status", lambda: AVAILABLE)  # bound when the app is built
    monkeypatch.setattr(folder_dialog, "owner_window", owner_window)
    monkeypatch.setattr(folder_dialog, "pick", pick)
    with TestClient(_p1_app(tmp_path), base_url=BASE) as client:
        status = client.get(PICK, headers=BEARER)
        picked = client.post(PICK, headers={**BEARER, "origin": ORIGIN}, json=PICK_BODY)
    assert status.json() == asdict(AVAILABLE)
    assert picked.status_code == 200 and picked.json() == {"paths": [r"C:\work\a"]}
    assert seen == [[], ("Choose folders", True, 7)]


def test_remove_drops_only_a_free_code_folder_s_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, service = app_at(tmp_path)
    client = TestClient(app, base_url=BASE)

    def opened(route: str, name: str) -> str:
        root = tmp_path / name
        root.mkdir()
        answer = client.post(
            route, headers=MUTATION, json={"command_id": str(uuid.uuid4()), "path": str(root)}
        )
        assert answer.status_code == 201, answer.text
        return answer.json()["workspace"]["workspace_id"]

    def remove(workspace_id: str, headers: dict[str, str] = MUTATION, body: object = None) -> Any:
        return client.post(
            f"/api/v2/workspaces/{workspace_id}/remove",
            headers=headers,
            json={"command_id": COMMAND} if body is None else body,
        )

    repo = opened("/api/v2/workspaces/code", "repo")
    assert _refusal(remove(repo, AUTH)) == (403, "forbidden_origin")
    for body in ({"command_id": "x"}, {"command_id": COMMAND, "x": 1}, {}):
        assert _refusal(remove(repo, body=body)) == (400, "invalid_body"), body
    removed = remove(repo)
    assert removed.status_code == 200
    assert removed.json() == {"workspace_id": repo, "removed": True}
    assert service.list(kind=None) == [] and (tmp_path / "repo").is_dir()
    assert _refusal(remove(repo)) == (404, "unknown_workspace")

    vault = opened("/api/v2/vaults/open", "notes")
    assert _refusal(remove(vault)) == (409, "not_removable")
    service.set_policy(str(uuid.uuid4()), vault, 1, "protected", [])
    assert _refusal(remove(vault)) == (403, "protected_root")

    selected = opened("/api/v2/workspaces/code", "selected")
    service.set_policy(str(uuid.uuid4()), selected, 1, "ordinary", [])
    service.select(str(uuid.uuid4()), selected)
    assert _refusal(remove(selected)) == (409, "workspace_in_use")

    # The service's own tests build a live worktree; here only its status is pinned.
    def worktrees_present(workspace_id: str) -> None:
        raise WorkspaceError("worktrees_present")

    monkeypatch.setattr(service, "remove", worktrees_present)
    assert _refusal(remove(selected)) == (409, "worktrees_present")
    assert len(service.list(kind=None)) == 2
