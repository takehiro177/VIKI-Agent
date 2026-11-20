"""The "viki" starter at bootstrap, in `tmp_path`: what it writes, resumes, refuses and cancels.

Guards exactly the starter set, the blank template (the welcome note and, on request, a plain
manifest), the starter's manifest, resume and replay, cancel, no hooks, no shared instruction
hash, a missing resource and a protected root. Every root, home and data directory is synthetic.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import uuid
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, Literal

import pytest

from tests.app.test_workspaces import (
    COMMAND,
    OTHER_COMMAND,
    make_junction,
    protected_at,
    service_at,
    store_at,
)
from tests.vaults.test_starter import AGENTS, DENY_RULES
from tests.vaults.test_vault_storage import RESOURCES, bootstrap_at, snapshot
from viki_agent import osfs, rails, vault_storage
from viki_agent.domain import Workspace
from viki_agent.vault_storage import MANIFEST, VaultBootstrap
from viki_agent.vaults.starter import FILES, STARTER_VERSION, folders, source_of
from viki_agent.vaults.vault_mutation import MutationError
from viki_agent.vaults.workspace_mutation import WorkspaceMutationResolver
from viki_agent.workspaces import WorkspaceError, WorkspaceService


def tree(root: Path) -> tuple[set[str], set[str]]:
    """Every file and every folder under `root`, as POSIX paths relative to it."""
    files: set[str] = set()
    found: set[str] = set()
    for path in root.rglob("*"):
        (found if path.is_dir() else files).add(path.relative_to(root).as_posix())
    return files, found


def _dying_write_once(name: str) -> Any:
    """Die at exactly the write of `name`, the way a killed process would."""
    real = VaultBootstrap._write_once

    def call(path: Path, text: str) -> None:
        if path.name == name:
            raise OSError("synthetic process death")
        real(path, text)

    return staticmethod(call)


def _pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: Literal["blank", "viki"] = "viki"
) -> tuple[WorkspaceService, VaultBootstrap, Workspace]:
    """A portable bootstrap whose process died after its last write, before its row was
    completed: the whole set is on disk and the row stays pending."""
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)

    def died(*_: object) -> Workspace:
        raise OSError("synthetic process death")

    monkeypatch.setattr(service, "complete_bootstrap", died)
    with pytest.raises(OSError, match="synthetic process death"):
        bootstrap.create(COMMAND, "Vault", None, None, "en", True, template=template)
    monkeypatch.undo()
    pending = service.by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending"
    return service, bootstrap, pending


def _link(link: Path, target: Path) -> None:
    """A folder entry that points somewhere else: a junction on Windows, a symlink elsewhere."""
    if sys.platform == "win32":
        make_junction(link, target)
    else:
        os.symlink(target, link, target_is_directory=True)


def test_start_with_viki_writes_exactly_the_starter_set(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    bootstrap = VaultBootstrap(
        service, resources=RESOURCES, home=tmp_path / "home", identity=lambda: ("Ada", "Sam Lee")
    )
    days = {datetime.now().astimezone().date().isoformat()}
    # No portable metadata is asked for: a starter always carries its manifest.
    workspace, created, files = bootstrap.create(
        COMMAND, "My Vault", None, None, "en", False, template="viki"
    )
    days.add(datetime.now().astimezone().date().isoformat())
    assert created is True and files == [*FILES, MANIFEST]
    assert (workspace.policy, workspace.bootstrap) == ("ordinary", "complete")
    assert workspace.portable_id is not None
    destination = Path(workspace.root.display)

    assert tree(destination) == ({*FILES, MANIFEST}, {*folders(), ".viki"})
    for name in (*FILES, MANIFEST):
        data = (destination / name).read_bytes()
        assert b"\r" not in data and not data.startswith(b"\xef\xbb\xbf"), name
        assert "{{viki." not in data.decode("utf-8"), name

    # The manifest names the starter and fingerprints every file as it landed.
    document = json.loads((destination / MANIFEST).read_text(encoding="utf-8"))
    assert set(document) == {"schema", "vault_id", "name", "created_at", "starter"}
    assert (document["schema"], document["vault_id"], document["name"]) == (
        1,
        workspace.portable_id,
        "My Vault",
    )
    assert set(document["starter"]) == {"name", "version", "files"}
    assert (document["starter"]["name"], document["starter"]["version"]) == ("viki", 6)
    assert list(document["starter"]["files"]) == list(FILES)
    for name, digest in document["starter"]["files"].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest, name

    # The identity, the reply language, the local date and the vault id are filled in.
    agents_md = (destination / "AGENTS.md").read_text(encoding="utf-8")
    assert "- Name: Ada\n- Address the owner as: Sam Lee\n" in agents_md
    assert "- Reply language: English\n" in agents_md
    assert workspace.portable_id in agents_md
    assert workspace.portable_id in (destination / "CLAUDE.md").read_text(encoding="utf-8")
    home_md = (destination / "Home.md").read_text(encoding="utf-8")
    assert any(f"created: {day}\n" in home_md for day in days)

    # A chat started here sees the five agents; the settings hold deny rules only.
    assert [agent.name for agent in rails.agents(destination, {})] == sorted(AGENTS)
    assert {a.name: a.tools for a in rails.agents(destination, {})}["checker"] == [
        "Read",
        "Glob",
        "Grep",
    ]
    assert (destination / "kanban").is_dir() and list((destination / "kanban").iterdir()) == []
    settings = json.loads((destination / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"permissions": {"deny": list(DENY_RULES)}}
    assert service.inspect_policy(workspace.workspace_id).markers["hooks"] is False


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits; Windows folders get a DACL instead"
)
@pytest.mark.usefixtures("desktop_umask")
def test_every_starter_folder_is_0700_and_every_file_0600(tmp_path: Path) -> None:
    workspace, _, _ = bootstrap_at(tmp_path).create(
        COMMAND, "Vault", None, None, "en", True, template="viki"
    )
    destination = Path(workspace.root.display)
    for path in (destination, *destination.rglob("*")):
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(os.lstat(path).st_mode) == expected, path


def test_blank_stays_as_adr_013_wrote_it(tmp_path: Path) -> None:
    """No template, or "blank", writes what it wrote before the starter existed and never
    reads the identity: the welcome note and, when asked for, the four-key manifest."""
    service = service_at(tmp_path)
    bootstrap = VaultBootstrap(
        service,
        resources=RESOURCES,
        home=tmp_path / "home",
        identity=lambda: pytest.fail("a blank vault reads no identity"),
    )
    welcome = (RESOURCES / "welcome.en.md").read_text(encoding="utf-8").encode("utf-8")

    plain, created, files = bootstrap.create(COMMAND, "Plain", None, None, "en", True)
    assert created is True and files == ["Welcome.md", MANIFEST]
    root = Path(plain.root.display)
    assert tree(root) == ({"Welcome.md", MANIFEST}, {".viki"})
    assert (root / "Welcome.md").read_bytes() == welcome
    document = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    assert set(document) == {"schema", "vault_id", "name", "created_at"}
    replayed = bootstrap.create(COMMAND, "Plain", None, None, "en", True)
    assert replayed[1:] == (False, ["Welcome.md", MANIFEST])

    named, created, files = bootstrap.create(
        OTHER_COMMAND, "Named", None, None, "en", False, template="blank"
    )
    assert created is True and files == ["Welcome.md"] and named.portable_id is None
    root = Path(named.root.display)
    assert tree(root) == ({"Welcome.md"}, set())
    assert (root / "Welcome.md").read_bytes() == welcome


def test_a_starter_resume_adds_only_missing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = store_at(tmp_path)
    destination = tmp_path / "home" / "VIKI" / "Vaults" / "Vault"
    first = bootstrap_at(tmp_path, service_at(tmp_path, store))
    monkeypatch.setattr(VaultBootstrap, "_write_once", _dying_write_once("Home.md"))
    with pytest.raises(WorkspaceError) as died:
        first.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    assert died.value.code == "unavailable"
    monkeypatch.undo()
    pending = service_at(tmp_path, store).by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending"
    # The manifest and every folder land before the first file; files land in FILES order.
    landed = FILES[: FILES.index("Home.md")]
    missing = FILES[len(landed) :]
    assert (destination / MANIFEST).is_file()
    assert all((destination / name).is_dir() for name in folders())
    assert all((destination / name).is_file() for name in landed)
    assert not any((destination / name).exists() for name in missing)

    # What is already on disk is the user's now; the resume adds, it never rewrites.
    agents_md = destination / "AGENTS.md"
    agents_md.write_bytes(agents_md.read_bytes() + b"- my own rule\n")
    before = snapshot(destination)

    second = VaultBootstrap(
        service_at(tmp_path, store),
        resources=RESOURCES,
        home=tmp_path / "home",
        identity=lambda: ("VIKI", "a different owner"),
    )
    workspace, created, files = second.create(
        COMMAND, "Vault", None, None, "en", True, template="viki"
    )
    assert created is True and files == [*FILES, MANIFEST]
    assert workspace.workspace_id == pending.workspace_id and workspace.bootstrap == "complete"
    after = snapshot(destination)
    assert {name: after[name] for name in before} == before
    assert set(after) - set(before) == {str(Path(name)) for name in missing}

    # A replay of the completed command writes nothing and lists the same files.
    replayed, created_again, replay_files = second.create(
        COMMAND, "Vault", None, None, "en", True, template="viki"
    )
    assert created_again is False and replay_files == files
    assert replayed.workspace_id == workspace.workspace_id
    assert snapshot(destination) == after


def test_an_older_starter_refuses_resume_before_identity_or_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    with monkeypatch.context() as failure:
        failure.setattr(VaultBootstrap, "_write_once", _dying_write_once("Home.md"))
        with pytest.raises(WorkspaceError) as stopped:
            bootstrap.create(COMMAND, "Vault", None, None, "en", True, template="viki")
        assert stopped.value.code == "unavailable"
    pending = service.by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending"
    destination = Path(pending.root.display)
    # An older set, planted generically: its manifest lists a file this build does not ship,
    # whose bytes are on disk, and a folder this build does not make sits there empty.
    retired = destination / ".claude/skills/retired/SKILL.md"
    retired.parent.mkdir()
    retired.write_bytes(b"# A retired skill\n")
    (destination / "retired").mkdir()
    manifest = destination / MANIFEST
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["starter"]["version"] = STARTER_VERSION - 1
    document["starter"]["files"][".claude/skills/retired/SKILL.md"] = hashlib.sha256(
        retired.read_bytes()
    ).hexdigest()
    manifest.write_text(json.dumps(document), encoding="utf-8")
    before = tree(destination), snapshot(destination)
    reads: list[str] = []
    real_render = vault_storage.render

    def identity() -> tuple[str, str]:
        reads.append("identity")
        return "VIKI", ""

    def render(*args: Any) -> list[tuple[str, str]]:
        reads.append("render")
        return real_render(*args)

    monkeypatch.setattr(vault_storage, "render", render)
    retry = VaultBootstrap(service, resources=RESOURCES, home=tmp_path / "home", identity=identity)
    with pytest.raises(WorkspaceError) as refused:
        retry.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    assert refused.value.code == "destination_not_empty"
    assert refused.value.extra == {
        "hint": "cancel_bootstrap",
        "workspace_id": pending.workspace_id,
    }
    assert reads == []
    assert (tree(destination), snapshot(destination)) == before
    assert service.by_command(COMMAND) == pending
    retry.cancel(OTHER_COMMAND, pending.workspace_id)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert service.list() == []


def test_a_completed_older_starter_replays_without_adding_or_rewriting_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    workspace, _, _ = bootstrap.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    destination = Path(workspace.root.display)
    manifest = destination / MANIFEST
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["starter"]["version"] = STARTER_VERSION - 1
    document["starter"]["files"].pop(".claude/skills/kanban/SKILL.md", None)
    manifest.write_text(json.dumps(document), encoding="utf-8")
    kanban = destination / ".claude/skills/kanban/SKILL.md"
    if kanban.is_file():
        kanban.unlink()
        kanban.parent.rmdir()
    before = tree(destination), snapshot(destination)
    monkeypatch.setattr(
        VaultBootstrap,
        "_write_starter",
        lambda *_: pytest.fail("a completed starter must not enter the write path"),
    )

    replayed, created, files = bootstrap.create(
        COMMAND, "Vault", None, None, "en", True, template="viki"
    )
    assert replayed.workspace_id == workspace.workspace_id and created is False
    assert files == [name for name in FILES if name != ".claude/skills/kanban/SKILL.md"] + [
        MANIFEST
    ]
    assert (tree(destination), snapshot(destination)) == before


@pytest.mark.parametrize(
    ("changed", "stopped_at"),
    [
        ("identity", "Home.md"),
        ("date", "Home.md"),
        ("resource", "Home.md"),
    ],
)
def test_a_resume_refuses_changed_missing_files_and_still_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str, stopped_at: str
) -> None:
    class Clock:
        current = datetime(2026, 1, 1).astimezone()

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return cls.current.astimezone(tz)

    monkeypatch.setattr(vault_storage, "datetime", Clock)
    resources = tmp_path / "resources"
    shutil.copytree(RESOURCES, resources)
    service = service_at(tmp_path)
    current_identity = ("VIKI", "")
    bootstrap = VaultBootstrap(
        service,
        resources=resources,
        home=tmp_path / "home",
        identity=lambda: current_identity,
    )
    with monkeypatch.context() as failure:
        failure.setattr(VaultBootstrap, "_write_once", _dying_write_once(stopped_at))
        with pytest.raises(WorkspaceError) as stopped:
            bootstrap.create(COMMAND, "Vault", None, None, "en", True, template="viki")
        assert stopped.value.code == "unavailable"
    pending = service.by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending"
    destination = Path(pending.root.display)
    before = tree(destination), snapshot(destination)
    if changed == "identity":
        current_identity = ("Ada", "")
    elif changed == "date":
        Clock.current = datetime(2026, 1, 2).astimezone()
    else:
        card = resources / "starter/_templates/card.md"
        card.write_bytes(card.read_bytes() + b"\n<!-- updated template -->\n")
    # Without the guard the new bytes land before this second failure, leaving a manifest
    # whose hashes no longer match; the cancel would then refuse the app's own additions.
    monkeypatch.setattr(VaultBootstrap, "_write_once", _dying_write_once("SKILL.md"))
    with pytest.raises(WorkspaceError) as refused:
        bootstrap.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    assert refused.value.code == "destination_not_empty"
    assert refused.value.extra == {
        "hint": "cancel_bootstrap",
        "workspace_id": pending.workspace_id,
    }
    assert (tree(destination), snapshot(destination)) == before
    assert service.by_command(COMMAND) == pending
    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert service.list() == []


def test_a_write_that_fails_after_its_file_exists_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk mid-write (ENOSPC) still creates Home.md before it fails: `_write_once` must
    undo that create, so no half-written file sits on disk for a retry to mistake as finished
    work or for a cancel to refuse as the user's own edit."""
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    real_open = osfs.open_new_private

    class HalfThenFullDisk:
        """Wraps the real handle for one target: writes half its bytes, then ENOSPC."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __enter__(self) -> HalfThenFullDisk:
            return self

        def __exit__(self, *exc: object) -> None:
            self._inner.close()

        def write(self, data: bytes) -> int:
            self._inner.write(data[: len(data) // 2])
            self._inner.flush()
            raise OSError(errno.ENOSPC, "No space left on device (synthetic)")

    def failing_open(path: Path) -> Any:
        handle = real_open(path)
        return HalfThenFullDisk(handle) if path.name == "Home.md" else handle

    monkeypatch.setattr(osfs, "open_new_private", failing_open)
    with pytest.raises(WorkspaceError) as failed:
        bootstrap.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    assert failed.value.code == "unavailable"
    monkeypatch.undo()

    destination = tmp_path / "home" / "VIKI" / "Vaults" / "Vault"
    assert not (destination / "Home.md").exists()
    pending = service.by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending"

    # A half-written file left behind would fail its manifest hash and refuse the cancel as an
    # edited file; gone, the cancel clears the whole starter and the row with it.
    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert service.list() == []


def test_a_resume_keeps_the_reserved_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest, not the call, decides: a retry that names no template and asks for no
    portable metadata still finishes, and replays, the starter it reserved."""
    store = store_at(tmp_path)
    first = bootstrap_at(tmp_path, service_at(tmp_path, store))
    monkeypatch.setattr(VaultBootstrap, "_write_once", _dying_write_once("Home.md"))
    with pytest.raises(WorkspaceError):
        first.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    monkeypatch.undo()

    second = bootstrap_at(tmp_path, service_at(tmp_path, store))
    workspace, created, files = second.create(COMMAND, "Vault", None, None, "en", False)
    assert created is True and files == [*FILES, MANIFEST]
    destination = Path(workspace.root.display)
    document = json.loads((destination / MANIFEST).read_text(encoding="utf-8"))
    for name, digest in document["starter"]["files"].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest, name

    replayed = second.create(COMMAND, "Vault", None, None, "en", False, template="blank")
    assert replayed[1:] == (False, files)


def test_two_starter_vaults_never_share_an_instruction_hash(tmp_path: Path) -> None:
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    made = []
    for command, parent in ((COMMAND, tmp_path / "one"), (OTHER_COMMAND, tmp_path / "two")):
        parent.mkdir()
        workspace, _, _ = bootstrap.create(
            command, "Vault", str(parent), None, "en", True, template="viki"
        )
        made.append(workspace)
    a, b = made
    assert a.portable_id is not None and b.portable_id is not None
    a_root, b_root = Path(a.root.display), Path(b.root.display)
    # Identical inputs: each vault's own id is the only difference between the two.
    for name in ("AGENTS.md", "CLAUDE.md"):
        a_text = (a_root / name).read_text(encoding="utf-8")
        b_text = (b_root / name).read_text(encoding="utf-8")
        assert a.portable_id in a_text and b.portable_id in b_text, name
        assert a_text.replace(a.portable_id, "") == b_text.replace(b.portable_id, ""), name
    a_hashes, b_hashes = a.identity.instruction_hashes, b.identity.instruction_hashes
    assert set(a_hashes) == set(b_hashes) == {"AGENTS.md", "CLAUDE.md"}
    assert set(a_hashes.values()).isdisjoint(b_hashes.values())

    service.set_policy(str(uuid.uuid4()), a.workspace_id, a.policy_revision, "protected", [])
    resolve = WorkspaceMutationResolver(service, git_available=lambda: False)
    b_snapshot = (b.workspace_id, b.policy_revision, b.root.physical_id)
    assert resolve(*b_snapshot).workspace_id == b.workspace_id

    # The control: with A's CLAUDE.md bytes, B is bound to the protected A and refused.
    (b_root / "CLAUDE.md").write_bytes((a_root / "CLAUDE.md").read_bytes())
    with pytest.raises(MutationError, match="ordinary_workspace_required"):
        resolve(*b_snapshot)


def test_a_missing_resource_writes_nothing(tmp_path: Path) -> None:
    resources = tmp_path / "resources"
    shutil.copytree(RESOURCES, resources)
    # The last file in write order: had any earlier one landed, rendering was not done first.
    (resources / "starter" / source_of(FILES[-1])).unlink()
    service = service_at(tmp_path)
    bootstrap = VaultBootstrap(service, resources=resources, home=tmp_path / "home")

    with pytest.raises(WorkspaceError) as refused:
        bootstrap.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    assert refused.value.code == "unavailable"
    assert not (tmp_path / "home" / "VIKI" / "Vaults" / "Vault").exists()
    pending = service.by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending"
    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert service.list() == []


def test_a_starter_inside_a_protected_root_writes_nothing(tmp_path: Path) -> None:
    guarded = tmp_path / "guarded"
    (guarded / "inner").mkdir(parents=True)
    (guarded / "note.md").write_text("owner's note\n", encoding="utf-8")
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    protected = protected_at(service, guarded)
    before = tree(guarded), snapshot(guarded)

    for parent in (guarded, guarded / "inner"):
        with pytest.raises(WorkspaceError) as refused:
            bootstrap.create(COMMAND, "Vault", str(parent), None, "en", True, template="viki")
        assert refused.value.code == "protected_overlap"
        assert refused.value.extra["workspace_id"] == protected
        assert (tree(guarded), snapshot(guarded)) == before
        assert service.by_command(COMMAND) is None

    # Above: a protected root registered beneath the destination while its folder is away.
    parents = tmp_path / "parents"
    beneath = parents / "Vault" / "inner"
    beneath.mkdir(parents=True)
    away = protected_at(service, beneath)
    beneath.rmdir()
    beneath.parent.rmdir()
    with pytest.raises(WorkspaceError) as refused:
        bootstrap.create(COMMAND, "Vault", str(parents), None, "en", True, template="viki")
    assert refused.value.code == "protected_overlap"
    assert refused.value.extra["workspace_id"] == away
    assert list(parents.iterdir()) == []
    assert service.by_command(COMMAND) is None


def test_cancel_removes_only_the_starter_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files first, then folders deepest first, then the manifest: a cancel stopped midway
    keeps its row and its manifest, and a retry finishes it. The destination stays."""
    service, bootstrap, pending = _pending(tmp_path, monkeypatch)
    destination = Path(pending.root.display)
    real_rmdir = Path.rmdir

    def in_use(path: Path) -> None:
        if path.name == ".claude":  # the last folder in deepest-first order
            raise PermissionError("synthetic: the folder is in use")
        real_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", in_use)
    with pytest.raises(WorkspaceError) as stopped:
        bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert stopped.value.code == "not_app_created"
    monkeypatch.undo()
    assert tree(destination) == ({MANIFEST}, {".claude", ".viki"})
    assert service.by_command(COMMAND) is not None

    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert service.list() == []


def test_a_refused_completion_undoes_the_starter_and_keeps_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The completion gate: a starter folder that resolves into a protected root is undone by
    create's own cancel, and the call reports that refusal, not `not_app_created`."""
    guarded = tmp_path / "guarded"
    inside = guarded / "resolved-here"
    inside.mkdir(parents=True)
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    protected = protected_at(service, guarded)
    destination = tmp_path / "home" / "VIKI" / "Vaults" / "Vault"
    real = service.canonicalise

    def resolves_into_the_protected_root(raw: str) -> Any:
        return real(str(inside)) if raw == str(destination) else real(raw)

    monkeypatch.setattr(service, "canonicalise", resolves_into_the_protected_root)
    with pytest.raises(WorkspaceError) as refused:
        bootstrap.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    assert refused.value.code == "protected_overlap"
    monkeypatch.undo()
    assert [row.workspace_id for row in service.list()] == [protected]
    assert list(destination.iterdir()) == [] and list(inside.iterdir()) == []


@pytest.mark.parametrize(
    "planted",
    [
        ".claude/skills/x/SKILL.md",  # a file in a folder the starter never makes
        ".claude/skills/capture/notes.md",  # a file beside a starter skill's own
        "kanban/A card.md",  # a user's card in the folder the starter ships empty
        "AGENTS.md",  # a starter file the user edited: its bytes no longer match the manifest
    ],
)
def test_a_foreign_entry_anywhere_refuses_the_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planted: str
) -> None:
    """Below the top level too: one file the manifest does not list, or one edited starter file,
    and the cancel removes nothing and keeps the row."""
    service, bootstrap, pending = _pending(tmp_path, monkeypatch)
    destination = Path(pending.root.display)
    entry = destination / planted
    pristine = entry.read_bytes() if entry.is_file() else None
    if pristine is not None:
        entry.write_bytes(pristine + b"- my own rule\n")
    else:
        entry.parent.mkdir(exist_ok=True)
        entry.write_text("the owner's own file\n", encoding="utf-8")
    before = tree(destination), snapshot(destination)

    with pytest.raises(WorkspaceError) as refused:
        bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert refused.value.code == "not_app_created"
    assert (tree(destination), snapshot(destination)) == before
    assert service.by_command(COMMAND) is not None

    # The control: without that one entry, the same tree is the app's own and it goes.
    if pristine is not None:
        entry.write_bytes(pristine)
    else:
        while entry.relative_to(destination).as_posix() not in folders():
            if entry.is_dir():
                entry.rmdir()
            else:
                entry.unlink()
            entry = entry.parent
    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert list(destination.iterdir()) == [] and service.list() == []


@pytest.mark.parametrize(("template", "linked"), [("viki", ".claude/skills"), ("blank", ".viki")])
def test_a_link_in_the_tree_refuses_the_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: Literal["blank", "viki"],
    linked: str,
) -> None:
    """A link below the destination, in a starter or in a blank vault's `.viki`, refuses the
    resume and the cancel alike, and its target keeps every byte; without the link the same
    tree is cancelled."""
    service, bootstrap, pending = _pending(tmp_path, monkeypatch, template)
    destination = Path(pending.root.display)
    away = tmp_path / "away"
    (destination / linked).rename(away)
    _link(destination / linked, away)
    before = tree(away), snapshot(away)

    with pytest.raises(WorkspaceError) as resumed:
        bootstrap.create(COMMAND, "Vault", None, None, "en", True, template=template)
    assert resumed.value.code == "not_app_created"
    with pytest.raises(WorkspaceError) as refused:
        bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert refused.value.code == "not_app_created"
    assert (tree(away), snapshot(away)) == before
    assert service.by_command(COMMAND) is not None

    (destination / linked).unlink()  # the link itself; its target is untouched
    away.rename(destination / linked)
    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert list(destination.iterdir()) == [] and service.list() == []


def test_cancel_removes_an_extra_listed_file_and_an_empty_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setup's own manifest decides what the cancel removes: a file it lists that this build
    does not ship goes, as does an empty folder; edited, that file refuses the cancel."""
    service, bootstrap, pending = _pending(tmp_path, monkeypatch)
    destination = Path(pending.root.display)
    extra = destination / "extra/notes/listed.md"
    extra.parent.mkdir(parents=True)
    extra.write_bytes(b"listed by the manifest\n")
    (destination / "empty").mkdir()
    manifest = destination / MANIFEST
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["starter"]["files"]["extra/notes/listed.md"] = hashlib.sha256(
        extra.read_bytes()
    ).hexdigest()
    manifest.write_text(json.dumps(document), encoding="utf-8")

    extra.write_bytes(b"listed by the manifest, then edited\n")
    before = tree(destination), snapshot(destination)
    with pytest.raises(WorkspaceError) as refused:
        bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert refused.value.code == "not_app_created"
    assert (tree(destination), snapshot(destination)) == before
    assert service.by_command(COMMAND) is not None

    extra.write_bytes(b"listed by the manifest\n")
    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert service.list() == []


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            "a?b.md",
            marks=pytest.mark.skipif(sys.platform != "win32", reason="a name Windows cannot hold"),
        ),
        pytest.param(
            "AGENTS.md/x",
            marks=pytest.mark.skipif(sys.platform == "win32", reason="ENOTDIR is POSIX's refusal"),
        ),
    ],
)
def test_a_listed_name_the_disk_cannot_hold_is_skipped_by_the_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """A listed name no file can have is not on disk to delete: the cancel removes the files its
    walk found, listed before or after that name, and finishes."""
    service, bootstrap, pending = _pending(tmp_path, monkeypatch)
    destination = Path(pending.root.display)
    manifest = destination / MANIFEST
    document = json.loads(manifest.read_text(encoding="utf-8"))
    # First in the manifest's order, so it is reached before the file it would sit under.
    document["starter"]["files"] = {name: "0" * 64, **document["starter"]["files"]}
    manifest.write_text(json.dumps(document), encoding="utf-8")

    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert service.list() == []


def test_a_listed_name_that_is_a_folder_refuses_the_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed name that is on disk but is not a regular file, here an empty folder, refuses the
    cancel before anything is deleted; as a file with the recorded bytes, it goes."""
    service, bootstrap, pending = _pending(tmp_path, monkeypatch)
    destination = Path(pending.root.display)
    listed = destination / "listed.md"
    listed.mkdir()
    manifest = destination / MANIFEST
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["starter"]["files"]["listed.md"] = hashlib.sha256(
        b"listed by the manifest\n"
    ).hexdigest()
    manifest.write_text(json.dumps(document), encoding="utf-8")
    before = tree(destination), snapshot(destination)

    with pytest.raises(WorkspaceError) as refused:
        bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert refused.value.code == "not_app_created"
    assert (tree(destination), snapshot(destination)) == before
    assert service.by_command(COMMAND) is not None

    listed.rmdir()
    listed.write_bytes(b"listed by the manifest\n")
    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert service.list() == []


@pytest.mark.parametrize(
    "name",
    ["../keep.md", "absolute", "C:/x.md", "a\\b.md", "./Welcome.md", "a//b.md"],
)
def test_a_manifest_name_that_could_leave_the_folder_refuses_the_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Every manifest name is checked before the first deletion: one that could reach outside
    the folder, or spell a listed name a second way, refuses the whole cancel."""
    service, bootstrap, pending = _pending(tmp_path, monkeypatch)
    destination = Path(pending.root.display)
    keep = destination.parent / "keep.md"
    keep.write_text("the owner's note beside the vault\n", encoding="utf-8")
    if name == "absolute":
        # The note's own path from its drive's root: absolute on POSIX, and on Windows a
        # root-relative path that pathlib joins onto the destination's drive.
        name = "/" + keep.relative_to(keep.anchor).as_posix()
    manifest = destination / MANIFEST
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["starter"]["files"][name] = hashlib.sha256(keep.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(document), encoding="utf-8")
    before = tree(destination), snapshot(destination)

    with pytest.raises(WorkspaceError) as refused:
        bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert refused.value.code == "not_app_created"
    assert (tree(destination), snapshot(destination)) == before
    assert keep.read_text(encoding="utf-8") == "the owner's note beside the vault\n"
    pending_again = service.by_command(COMMAND)
    assert pending_again is not None and pending_again.bootstrap == "pending"


def test_a_viki_retry_of_a_failed_blank_reservation_finishes_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Retry resumes the template its reservation began with, never the retrying call's own: a
    blank, non-portable reservation that died before any byte landed still finishes blank -- the
    welcome note only, no `.viki` -- when the user switches to Start with VIKI and presses
    Retry."""
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    monkeypatch.setattr(VaultBootstrap, "_write_once", _dying_write_once("Welcome.md"))
    with pytest.raises(WorkspaceError) as died:
        bootstrap.create(COMMAND, "Vault", None, None, "en", False, template="blank")
    assert died.value.code == "unavailable"
    monkeypatch.undo()
    pending = service.by_command(COMMAND)
    assert pending is not None and pending.bootstrap == "pending" and pending.portable_id is None
    destination = Path(pending.root.display)
    assert tree(destination) == (set(), set())  # nothing landed before the death

    workspace, created, files = bootstrap.create(
        COMMAND, "Vault", None, None, "en", True, template="viki"
    )
    assert created is True and files == ["Welcome.md"]
    assert workspace.workspace_id == pending.workspace_id and workspace.portable_id is None
    assert tree(destination) == ({"Welcome.md"}, set())


def test_a_portable_viki_retry_past_a_welcome_note_finishes_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blank, portable reservation carries a `portable_id` too -- the same shape a starter
    reservation has -- so `workspace.portable_id is not None` alone cannot tell a viki Retry the
    reservation began as a starter. This one dies right after its welcome note lands, before any
    manifest does. A starter always writes its manifest before any other file, so a welcome note
    on disk without one proves the reservation began blank, whatever template the Retry now
    names: a Retry still finishes it blank, and a Cancel of the same precondition still removes
    everything."""
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    monkeypatch.setattr(VaultBootstrap, "_write_once", _dying_write_once("vault.json"))
    for command, name in ((COMMAND, "Vault"), (OTHER_COMMAND, "Vault 2")):
        with pytest.raises(WorkspaceError) as died:
            bootstrap.create(command, name, None, None, "en", True, template="blank")
        assert died.value.code == "unavailable"
    monkeypatch.undo()

    first = service.by_command(COMMAND)
    assert first is not None and first.portable_id is not None
    first_destination = Path(first.root.display)
    assert tree(first_destination) == ({"Welcome.md"}, {".viki"})

    workspace, created, files = bootstrap.create(
        COMMAND, "Vault", None, None, "en", True, template="viki"
    )
    assert created is True and files == ["Welcome.md", MANIFEST]
    assert workspace.workspace_id == first.workspace_id
    assert tree(first_destination) == ({"Welcome.md", MANIFEST}, {".viki"})
    document = json.loads((first_destination / MANIFEST).read_text(encoding="utf-8"))
    assert "starter" not in document

    second = service.by_command(OTHER_COMMAND)
    assert second is not None
    second_destination = Path(second.root.display)
    assert tree(second_destination) == ({"Welcome.md"}, {".viki"})
    bootstrap.cancel(COMMAND, second.workspace_id)
    assert second_destination.is_dir() and list(second_destination.iterdir()) == []
    assert service.by_command(OTHER_COMMAND) is None


def test_a_portable_viki_retry_past_a_plain_manifest_finishes_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same confusion, this time after the blank set finished writing in full -- welcome note
    and plain manifest both on disk -- but the process died completing the row. The manifest on
    disk is not the starter's, so a viki Retry must still finish it blank rather than leaving a
    mismatched starter manifest, or the other 33 starter files, around it."""
    service, bootstrap, pending = _pending(tmp_path, monkeypatch, "blank")
    destination = Path(pending.root.display)
    assert tree(destination) == ({"Welcome.md", MANIFEST}, {".viki"})
    before = json.loads((destination / MANIFEST).read_text(encoding="utf-8"))
    assert "starter" not in before

    workspace, created, files = bootstrap.create(
        COMMAND, "Vault", None, None, "en", True, template="viki"
    )
    assert created is True and files == ["Welcome.md", MANIFEST]
    assert workspace.workspace_id == pending.workspace_id
    assert tree(destination) == ({"Welcome.md", MANIFEST}, {".viki"})
    after = json.loads((destination / MANIFEST).read_text(encoding="utf-8"))
    assert after == before  # untouched: `_write_once` never rewrites a file already there


def test_a_viki_retry_that_fails_again_still_cancels_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reservation decides the template on every resume, not just the first: when the
    viki Retry of a blank reservation fails too, the row never picks up a manifest id of its own
    that it cannot record, so cancel is no longer refused `not_app_created` forever."""
    service = service_at(tmp_path)
    bootstrap = bootstrap_at(tmp_path, service)
    monkeypatch.setattr(VaultBootstrap, "_write_once", _dying_write_once("Welcome.md"))
    with pytest.raises(WorkspaceError):
        bootstrap.create(COMMAND, "Vault", None, None, "en", False, template="blank")
    pending = service.by_command(COMMAND)
    assert pending is not None and pending.portable_id is None
    with pytest.raises(WorkspaceError):
        bootstrap.create(COMMAND, "Vault", None, None, "en", True, template="viki")
    monkeypatch.undo()

    destination = Path(pending.root.display)
    bootstrap.cancel(OTHER_COMMAND, pending.workspace_id)
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert service.list() == []
