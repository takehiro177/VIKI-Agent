"""Workspace, settings and capability types shared by the services.

Declarations only: no behaviour and no I/O, so every service can import these names
without importing each other. The storage rows stay plain dicts; these frozen dataclasses
are what the services hand to the API layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PolicyKind = Literal["ordinary", "existing_readonly", "protected"]
WorkspaceKind = Literal["vault", "code", "scratch"]  # "scratch" is in-memory only; never a row
WorkspaceSource = Literal["created", "opened", "migrated", "environment", "builtin"]
# "environment" and "builtin" are in-memory only; the CHECK admits the first three
Layout = Literal["legacy_para", "unknown", "scratch"]  # "scratch" is in-memory only
Bootstrap = Literal["none", "pending", "complete"]
SCRATCH_WORKSPACE_ID = "165b132f-7fcb-5c95-8c6c-86eff07bfe13"
# Persisted: rows are keyed by this fixed id, so it must not change; a test pins it.


@dataclass(frozen=True)
class CanonicalRoot:
    key: str  # workspaces.root_key(display): the comparison key on this OS
    display: str  # the final path name: on Windows the on-disk case and long names;
    # on POSIX the resolved path (on macOS its F_GETPATH spelling)
    physical_id: str  # f"{st_dev}:{st_ino}", decimal; required
    aliased: bool  # the raw chain contained a reparse point (informational)


@dataclass(frozen=True)
class RootIdentity:
    git_root_oids: tuple[str, ...]  # sorted; () when Git absent/refused/not a repo
    git_common_dir: str | None  # canonical key of --git-common-dir, else None
    instruction_hashes: Mapping[str, str]  # {"CLAUDE.md": sha256hex, "AGENTS.md": ...}
    established: bool  # any OID or common dir observed
    inherits_from: str | None  # protected workspace_id this row was bound from
    probed_at: str  # ISO 8601 UTC, 24 chars


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    kind: WorkspaceKind
    root: CanonicalRoot
    name: str
    policy: PolicyKind
    policy_revision: int
    revision: int
    source: WorkspaceSource
    layout: Layout
    portable_id: str | None
    identity: RootIdentity
    face_dir: str | None  # legacy relative-path value retained for storage and wire compatibility
    bootstrap: Bootstrap
    persistent: bool  # False for scratch and environment workspaces
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ActiveWorkspace:
    workspace: Workspace
    cwd: Path  # == Path(workspace.root.display) for kind "vault";
    # == the scratch root for kind "scratch"; nothing else
    selected_id: str | None  # the persisted selection; differs from workspace.workspace_id
    # (or is None) when a restart is required
    selected_by: Literal["environment", "registration", "builtin"]
    chat_only: bool


SETTINGS_VERSION = 1
SettingSource = Literal["default", "setting", "provider", "session"]
Applies = Literal["now", "next_session", "next_launch"]


@dataclass(frozen=True)
class SettingSpec:
    key: str
    scope: Literal["app"]  # the only scope
    kind: Literal["enum", "bool", "string", "number"]
    allowed: tuple[object, ...] | None
    default: object
    applies: Applies
    personal: bool  # redacted from exports unless include_identity
    max_len: int = 0
    bounds: tuple[int, int] | None = None  # whole-number min..max; only kind "number", allowed=None


@dataclass(frozen=True)
class SettingsDocument:
    version: int
    revision: int
    values: Mapping[str, object] | None  # stored values, unknown keys included; None when corrupt
    read_only: bool
    reason: Literal["newer_version", "corrupt"] | None


@dataclass(frozen=True)
class EffectiveSettings:
    document: SettingsDocument
    effective: Mapping[str, object]  # corrupt document: provider values for chat.*, built-in
    # defaults for every other key; never a "setting" source
    sources: Mapping[str, SettingSource]
    keys: Mapping[str, SettingSpec]


ProviderProblem = Literal[
    "not_found",
    "batch_shim",
    "config_dir_override",
    "api_key_helper",
    "billing_env_key",
    "federation_profile",
    "other",
]


@dataclass(frozen=True)
class ProviderReadiness:
    cli: Literal["ready", "missing", "unusable", "config_dir_override"]
    version: str
    login: Literal["unknown", "signed_in", "signed_out"]
    billing: Literal["clear", "blocked", "unknown"]
    last_checked_at: str | None
    can_start: bool  # cli == "ready" and billing != "blocked" and storage ready
    problems: tuple[ProviderProblem, ...]
    source: Literal["override", "bundled", "installed", ""] = ""
    version_status: Literal["unknown", "tested", "older", "newer"] = "unknown"


@dataclass(frozen=True)
class GitReadiness:
    state: Literal["ready", "missing", "unknown", "overridden"]
    version: str


@dataclass(frozen=True)
class StorageReadiness:
    state: Literal["ready", "read_only", "unavailable"]
    reason: str | None  # a storage.StateError code or None
    private: bool | None


@dataclass(frozen=True)
class Capabilities:
    app: Mapping[str, object]
    workspace: Mapping[str, object]
    provider: Mapping[str, ProviderReadiness]
    git: GitReadiness
    storage: StorageReadiness
    features: Mapping[str, bool]
    launch: Mapping[str, object]
