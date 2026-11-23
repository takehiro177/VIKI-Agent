"""App-owned session metadata and encrypted drafts. Native history never enters this store.

The initial database is built beside its destination, closed and published without replacing
an existing file. Failed/interrupted imports leave the original JSON available for recovery.
Checks reject existing redirected paths; they do not sandbox a hostile same-user process
that replaces filesystem entries concurrently.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from viki_agent import osfs, protection
from viki_agent.persistence.bot_schemas import (
    BOT_CODE_INDEXES,
    BOT_CODE_SCHEMA_STEP,
    BOT_CODE_SCHEMAS,
    BOT_MEMORY_SCHEMA_STEP,
    BOT_MEMORY_SCHEMAS,
    BOT_SCHEMA_STEP,
    BOT_SCHEMAS,
    BOT_THREAD_SCHEMA_STEP,
    BOT_THREAD_SCHEMAS,
    BOT_USAGE_INDEXES,
    BOT_USAGE_SCHEMA_STEP,
    BOT_USAGE_SCHEMAS,
)
from viki_agent.persistence.decision_loop_schemas import (
    DECISION_LOOP_SCHEMA_STEP,
    DECISION_LOOP_SCHEMAS,
    DECISION_PROPOSAL_SCHEMAS,
    DECISION_PROPOSALS_SCHEMA_STEP,
)
from viki_agent.persistence.feature_schemas import (
    BOOKMARK_SCHEMAS,
    DECISION_SCHEMAS,
    INBOX_SUGGESTION_SCHEMA_STEP,
    INBOX_SUGGESTION_SCHEMAS,
    LAUNCH_ORIGIN_COLUMN,
    MEMORY_SCHEMAS,
    OPERATION_SCHEMAS,
    PROFILE_SCHEMAS,
    PROFILE_SCHEMAS_V40,
    ROUTINE_SCHEMAS,
    ROUTINE_SCHEMAS_BEFORE_29,
    SCHEMAS_BEFORE_13,
    TASK_ATTEMPTS_ACTIVE_INDEX,
)

APPLICATION_ID = 0x56494B49  # VIKI
# SQLite's busy handler sleeps in growing steps (1, 2, 5, 10, 15, 20 ms, ...) until this
# budget is spent. Windows rounds each sleep up to its 15.6 ms timer tick, so 25 ms there
# really waits 50 to 86 ms; POSIX honours the budget exactly while its commits, which fsync,
# are slower, so it gets a budget that waits about as long.
BUSY_TIMEOUT_S = 0.025 if sys.platform == "win32" else 0.075
MAX_SESSION_METADATA = 4096  # Labels per scope: the insert cap and the listing bound.
_SESSION_SCHEMA_V6 = """CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    mode TEXT NOT NULL,
    effort TEXT NOT NULL,
    model TEXT
) STRICT"""
_SCHEMA = """CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY NOT NULL,
    mode TEXT NOT NULL,
    effort TEXT NOT NULL,
    model TEXT,
    origin TEXT NOT NULL DEFAULT 'app' CHECK(origin IN ('app','profile'))
) STRICT"""
_DRAFT_SCHEMA_V10 = """CREATE TABLE drafts (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    key TEXT NOT NULL CHECK(length(key) BETWEEN 1 AND 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1114112),
    format INTEGER NOT NULL DEFAULT 1 CHECK(format IN (1,2,3)),
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 0 AND 1048576),
    saved_at TEXT NOT NULL CHECK(length(saved_at) = 24),
    PRIMARY KEY (scope, key)
) STRICT"""
_DRAFT_SCHEMA_V6 = """CREATE TABLE drafts (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    key TEXT NOT NULL CHECK(length(key) BETWEEN 1 AND 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1114112),
    format INTEGER NOT NULL DEFAULT 1 CHECK(format IN (1,2)),
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 0 AND 1048576),
    saved_at TEXT NOT NULL CHECK(length(saved_at) = 24),
    PRIMARY KEY (scope, key)
) STRICT"""
_DRAFT_SCHEMA = """CREATE TABLE drafts (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    key TEXT NOT NULL CHECK(length(key) BETWEEN 1 AND 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1114112),
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 0 AND 1048576),
    saved_at TEXT NOT NULL CHECK(length(saved_at) = 24),
    PRIMARY KEY (scope, key)
) STRICT"""
_COMMAND_SCHEMA_V10 = """CREATE TABLE commands (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    command_id TEXT NOT NULL CHECK(length(command_id) = 36),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    generation TEXT NOT NULL CHECK(length(generation) = 36),
    source_draft_id TEXT NOT NULL CHECK(length(source_draft_id) = 36),
    draft_revision INTEGER NOT NULL CHECK(draft_revision BETWEEN 1 AND 9007199254740991),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    owner TEXT NOT NULL CHECK(length(owner) = 36),
    state TEXT NOT NULL CHECK(state IN ('accepted','dispatching','unknown','not_dispatched')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1114112),
    format INTEGER NOT NULL DEFAULT 1 CHECK(format IN (1,2,3)),
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 0 AND 1048576),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (scope, command_id),
    UNIQUE (scope, session_id, source_draft_id, draft_revision)
) STRICT"""
_COMMAND_SCHEMA_V6 = """CREATE TABLE commands (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    command_id TEXT NOT NULL CHECK(length(command_id) = 36),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    generation TEXT NOT NULL CHECK(length(generation) = 36),
    source_draft_id TEXT NOT NULL CHECK(length(source_draft_id) = 36),
    draft_revision INTEGER NOT NULL CHECK(draft_revision BETWEEN 1 AND 9007199254740991),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    owner TEXT NOT NULL CHECK(length(owner) = 36),
    state TEXT NOT NULL CHECK(state IN ('accepted','dispatching','unknown','not_dispatched')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1114112),
    format INTEGER NOT NULL DEFAULT 1 CHECK(format IN (1,2)),
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 0 AND 1048576),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (scope, command_id),
    UNIQUE (scope, session_id, source_draft_id, draft_revision)
) STRICT"""
_COMMAND_SCHEMA = """CREATE TABLE commands (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    command_id TEXT NOT NULL CHECK(length(command_id) = 36),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    generation TEXT NOT NULL CHECK(length(generation) = 36),
    draft_revision INTEGER NOT NULL CHECK(draft_revision BETWEEN 1 AND 9007199254740991),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    owner TEXT NOT NULL CHECK(length(owner) = 36),
    state TEXT NOT NULL CHECK(state IN ('accepted','dispatching','unknown','not_dispatched')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1114112),
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 1 AND 1048576),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (scope, command_id)
) STRICT"""
_ASSET_SCHEMA = """CREATE TABLE assets (
    scope TEXT NOT NULL CHECK(length(scope) BETWEEN 1 AND 128),
    asset_id TEXT NOT NULL CHECK(length(asset_id) = 36),
    media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 1 AND 128),
    byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 20971520),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    width INTEGER NOT NULL CHECK(width BETWEEN 1 AND 1000000),
    height INTEGER NOT NULL CHECK(height BETWEEN 1 AND 1000000),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    local_state TEXT NOT NULL DEFAULT 'stored' CHECK(local_state IN ('stored','expired')),
    local_removed_at TEXT CHECK(local_removed_at IS NULL OR length(local_removed_at) = 24),
    CHECK((local_state = 'stored' AND local_removed_at IS NULL) OR
          (local_state = 'expired' AND local_removed_at IS NOT NULL)),
    PRIMARY KEY (scope, asset_id)
) STRICT"""
_ASSET_REF_SCHEMA = """CREATE TABLE asset_refs (
    scope TEXT NOT NULL CHECK(length(scope) BETWEEN 1 AND 128),
    asset_id TEXT NOT NULL CHECK(length(asset_id) = 36),
    owner_kind TEXT NOT NULL CHECK(length(owner_kind) BETWEEN 1 AND 64),
    owner_id TEXT NOT NULL CHECK(length(owner_id) BETWEEN 1 AND 128),
    role TEXT NOT NULL CHECK(length(role) BETWEEN 1 AND 64),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    PRIMARY KEY (scope, owner_kind, owner_id, role),
    FOREIGN KEY (scope, asset_id) REFERENCES assets(scope, asset_id)
) STRICT"""
_CONVERSATION_ARCHIVE_SCHEMA = """CREATE TABLE conversation_archives (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    archive_id TEXT NOT NULL CHECK(length(archive_id) = 36),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 8454144),
    byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 8388608),
    PRIMARY KEY (scope, archive_id)
) STRICT"""
_QUEUED_TURN_SCHEMA_V10 = """CREATE TABLE queued_turns (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    item_id TEXT NOT NULL CHECK(length(item_id) = 36),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 4095),
    source_draft_id TEXT NOT NULL CHECK(length(source_draft_id) BETWEEN 1 AND 36),
    source_draft_revision INTEGER NOT NULL
        CHECK(source_draft_revision BETWEEN 1 AND 9007199254740991),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1114112),
    format INTEGER NOT NULL CHECK(format IN (2,3)),
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 0 AND 1048576),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (scope, item_id),
    UNIQUE (scope, session_id, ordinal),
    UNIQUE (scope, session_id, source_draft_id, source_draft_revision)
) STRICT"""
_QUEUED_TURN_SCHEMA = """CREATE TABLE queued_turns (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    item_id TEXT NOT NULL CHECK(length(item_id) = 36),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 0 AND 4095),
    source_draft_id TEXT NOT NULL CHECK(length(source_draft_id) BETWEEN 1 AND 36),
    source_draft_revision INTEGER NOT NULL
        CHECK(source_draft_revision BETWEEN 1 AND 9007199254740991),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1114112),
    format INTEGER NOT NULL CHECK(format = 2),
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 0 AND 1048576),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (scope, item_id),
    UNIQUE (scope, session_id, ordinal),
    UNIQUE (scope, session_id, source_draft_id, source_draft_revision)
) STRICT"""
_TURN_QUEUE_SCHEMA = """CREATE TABLE turn_queues (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    paused_reason TEXT NOT NULL CHECK(length(paused_reason) <= 32),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (scope, session_id)
) STRICT"""
_SESSION_METADATA_SCHEMA_V20 = """CREATE TABLE session_metadata (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    title TEXT NOT NULL CHECK(length(title) <= 256),
    pinned INTEGER NOT NULL CHECK(pinned IN (0,1)),
    archived INTEGER NOT NULL CHECK(archived IN (0,1)),
    PRIMARY KEY (scope, session_id)
) STRICT"""
_SESSION_METADATA_SCHEMA = """CREATE TABLE session_metadata (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    title TEXT NOT NULL CHECK(length(title) <= 256),
    pinned INTEGER NOT NULL CHECK(pinned IN (0,1)),
    archived INTEGER NOT NULL CHECK(archived IN (0,1)),
    unread INTEGER NOT NULL DEFAULT 0 CHECK(unread IN (0,1)),
    color TEXT DEFAULT NULL CHECK(color IN ('blue','green','orange','purple','red','slate')),
    PRIMARY KEY (scope, session_id)
) STRICT"""
_APPROVAL_SCHEMA = """CREATE TABLE approvals (
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    choice_id TEXT NOT NULL CHECK(length(choice_id) = 36),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    generation TEXT NOT NULL CHECK(length(generation) = 36),
    runtime_id TEXT NOT NULL CHECK(length(runtime_id) = 36),
    request_id TEXT NOT NULL CHECK(length(request_id) = 36),
    turn_id TEXT NOT NULL CHECK(length(turn_id) = 36),
    tool_use_id TEXT NOT NULL CHECK(length(tool_use_id) <= 256),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    owner TEXT NOT NULL CHECK(length(owner) = 36),
    state TEXT NOT NULL CHECK(state IN
        ('recorded','delivering','delivered_locally','not_delivered','unknown')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 1245184),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (scope, choice_id),
    UNIQUE (scope, session_id, runtime_id, request_id)
) STRICT"""
_LEGACY_BINDING_SCHEMA = """CREATE TABLE legacy_binding (
    id INTEGER PRIMARY KEY NOT NULL CHECK(id = 1),
    result TEXT NOT NULL CHECK(result IN ('bound','absent','unusable','protected_row_exists')),
    workspace_id TEXT CHECK(workspace_id IS NULL OR length(workspace_id) = 36),
    code TEXT CHECK(code IS NULL OR length(code) BETWEEN 1 AND 64),
    recorded_at TEXT NOT NULL CHECK(length(recorded_at) = 24)
) STRICT"""
_WORKSPACE_SCHEMA = """CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY NOT NULL CHECK(length(workspace_id) = 36),
    kind TEXT NOT NULL CHECK(kind = 'vault'),
    canonical_key TEXT NOT NULL UNIQUE CHECK(length(canonical_key) BETWEEN 3 AND 4096),
    display_root TEXT NOT NULL CHECK(length(display_root) BETWEEN 3 AND 4096),
    physical_id TEXT NOT NULL UNIQUE CHECK(length(physical_id) BETWEEN 3 AND 80),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 256),
    policy TEXT NOT NULL CHECK(policy IN ('ordinary','existing_readonly','protected')),
    policy_revision INTEGER NOT NULL CHECK(policy_revision BETWEEN 1 AND 9007199254740991),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    source TEXT NOT NULL CHECK(source IN ('created','opened','migrated')),
    layout TEXT NOT NULL CHECK(layout IN ('legacy_para','unknown')),
    portable_id TEXT CHECK(portable_id IS NULL OR length(portable_id) = 36),
    identity TEXT NOT NULL CHECK(length(identity) BETWEEN 2 AND 4096),
    face_dir TEXT CHECK(face_dir IS NULL OR length(face_dir) BETWEEN 1 AND 512),
    bootstrap TEXT NOT NULL CHECK(bootstrap IN ('none','pending','complete')),
    command_id TEXT CHECK(command_id IS NULL OR length(command_id) = 36),
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24)
) STRICT"""
# Step 14 widens the kind CHECK to admit code folders; versions 5 to 13 keep the text above.
_WORKSPACE_SCHEMA_V14 = """CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY NOT NULL CHECK(length(workspace_id) = 36),
    kind TEXT NOT NULL CHECK(kind IN ('vault','code')),
    canonical_key TEXT NOT NULL UNIQUE CHECK(length(canonical_key) BETWEEN 3 AND 4096),
    display_root TEXT NOT NULL CHECK(length(display_root) BETWEEN 3 AND 4096),
    physical_id TEXT NOT NULL UNIQUE CHECK(length(physical_id) BETWEEN 3 AND 80),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 256),
    policy TEXT NOT NULL CHECK(policy IN ('ordinary','existing_readonly','protected')),
    policy_revision INTEGER NOT NULL CHECK(policy_revision BETWEEN 1 AND 9007199254740991),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    source TEXT NOT NULL CHECK(source IN ('created','opened','migrated')),
    layout TEXT NOT NULL CHECK(layout IN ('legacy_para','unknown')),
    portable_id TEXT CHECK(portable_id IS NULL OR length(portable_id) = 36),
    identity TEXT NOT NULL CHECK(length(identity) BETWEEN 2 AND 4096),
    face_dir TEXT CHECK(face_dir IS NULL OR length(face_dir) BETWEEN 1 AND 512),
    bootstrap TEXT NOT NULL CHECK(bootstrap IN ('none','pending','complete')),
    command_id TEXT CHECK(command_id IS NULL OR length(command_id) = 36),
    active INTEGER NOT NULL CHECK(active IN (0,1)),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24)
) STRICT"""
# One selected workspace at a time, enforced by the store rather than by the caller.
_WORKSPACE_ACTIVE_INDEX = (
    "CREATE UNIQUE INDEX workspaces_active ON workspaces(active) WHERE active = 1"
)
_SETTINGS_SCHEMA = """CREATE TABLE settings (
    scope TEXT PRIMARY KEY NOT NULL CHECK(scope = 'app'),
    version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 1000000),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    document TEXT NOT NULL CHECK(length(document) BETWEEN 2 AND 65536),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24)
) STRICT"""
_SETTING_COMMAND_SCHEMA = """CREATE TABLE setting_commands (
    command_id TEXT PRIMARY KEY NOT NULL CHECK(length(command_id) = 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    patch_hash TEXT NOT NULL CHECK(length(patch_hash) = 64),
    applied_at TEXT NOT NULL CHECK(length(applied_at) = 24)
) STRICT"""
# Step 15: each session's working root, and the app-created Git worktrees a root can name.
ROOT_SCHEMAS = {
    "session_roots": """CREATE TABLE session_roots (
    session_id TEXT PRIMARY KEY NOT NULL CHECK(length(session_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    worktree_id TEXT CHECK(worktree_id IS NULL OR length(worktree_id) = 36),
    display_root TEXT NOT NULL CHECK(length(display_root) BETWEEN 3 AND 4096),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24)
) STRICT""",
    "worktrees": """CREATE TABLE worktrees (
    worktree_id TEXT PRIMARY KEY NOT NULL CHECK(length(worktree_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    path TEXT NOT NULL UNIQUE CHECK(length(path) BETWEEN 3 AND 4096),
    branch TEXT NOT NULL CHECK(length(branch) BETWEEN 1 AND 100),
    base_oid TEXT NOT NULL CHECK(length(base_oid) IN (40, 64)
        AND base_oid NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL CHECK(state IN ('creating','ready','removing','removed','kept')),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24)
) STRICT""",
}
# Step 20 keeps the primary cwd and pins the additional folders in the same session row.
ROOT_SCHEMAS_V20 = {
    **ROOT_SCHEMAS,
    "session_roots": """CREATE TABLE session_roots (
    session_id TEXT PRIMARY KEY NOT NULL CHECK(length(session_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    worktree_id TEXT CHECK(worktree_id IS NULL OR length(worktree_id) = 36),
    display_root TEXT NOT NULL CHECK(length(display_root) BETWEEN 3 AND 4096),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    additional_roots TEXT NOT NULL DEFAULT '[]'
        CHECK(length(CAST(additional_roots AS BLOB)) BETWEEN 2 AND 32768)
) STRICT""",
}
# Step 16: user-written checks and their runs.
CHECK_SCHEMAS = {
    "check_runs": """CREATE TABLE check_runs (
    run_id TEXT PRIMARY KEY NOT NULL CHECK(length(run_id) = 36),
    check_id TEXT NOT NULL CHECK(length(check_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    worktree_id TEXT CHECK(worktree_id IS NULL OR length(worktree_id) = 36),
    head_oid TEXT CHECK(head_oid IS NULL OR (length(head_oid) IN (40, 64)
        AND head_oid NOT GLOB '*[^0-9a-f]*')),
    dirty INTEGER CHECK(dirty IS NULL OR dirty IN (0, 1)),
    outcome TEXT NOT NULL CHECK(outcome IN
        ('running','passed','failed','cancelled','timed_out','interrupted')),
    exit_code INTEGER CHECK(exit_code IS NULL
        OR exit_code BETWEEN -2147483648 AND 4294967295),
    duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms BETWEEN 0 AND 86400000),
    output_bytes INTEGER CHECK(output_bytes IS NULL
        OR output_bytes BETWEEN 0 AND 9007199254740991),
    tail BLOB CHECK(tail IS NULL OR length(tail) BETWEEN 1 AND 81920),
    started_at TEXT NOT NULL CHECK(length(started_at) = 24),
    ended_at TEXT CHECK(ended_at IS NULL OR length(ended_at) = 24),
    CHECK((outcome = 'running') = (ended_at IS NULL)),
    CHECK((head_oid IS NULL) = (dirty IS NULL))
) STRICT""",
    "code_checks": """CREATE TABLE code_checks (
    check_id TEXT PRIMARY KEY NOT NULL CHECK(length(check_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 65536),
    timeout_s INTEGER NOT NULL CHECK(timeout_s BETWEEN 60 AND 3600),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24)
) STRICT""",
}
# Step 17: skill proposals
SKILL_SCHEMAS = {
    "skill_proposals": """CREATE TABLE skill_proposals (
    proposal_id TEXT PRIMARY KEY NOT NULL CHECK(length(proposal_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 64 AND name NOT GLOB '*[^a-z0-9-]*'),
    trigger TEXT NOT NULL CHECK(trigger IN
        ('button','long_session','repeated_request','correction')),
    sessions TEXT NOT NULL CHECK(length(sessions) BETWEEN 40 AND 391),
    base_sha256 TEXT CHECK(base_sha256 IS NULL OR
        (length(base_sha256) = 64 AND base_sha256 NOT GLOB '*[^0-9a-f]*')),
    proposed_sha256 TEXT NOT NULL CHECK(length(proposed_sha256) = 64
        AND proposed_sha256 NOT GLOB '*[^0-9a-f]*'),
    body BLOB NOT NULL,
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 1 AND 131072),
    approved_at TEXT CHECK(approved_at IS NULL OR length(approved_at) = 24),
    rejected_at TEXT CHECK(rejected_at IS NULL OR length(rejected_at) = 24),
    reason TEXT CHECK(reason IS NULL OR length(reason) BETWEEN 1 AND 200),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    UNIQUE (workspace_id, name),
    CHECK(approved_at IS NULL OR rejected_at IS NULL),
    CHECK(reason IS NULL OR rejected_at IS NOT NULL)
) STRICT""",
}
# Step 18's task_flows table. Nothing reads or writes it; it stays because _validate expects it
# from version 18, in this build and in older ones.
TASK_SCHEMAS = {
    "task_flows": """CREATE TABLE task_flows (
    task_id TEXT PRIMARY KEY NOT NULL CHECK(length(task_id) = 36),
    command_id TEXT NOT NULL UNIQUE CHECK(length(command_id) = 36),
    session_id TEXT NOT NULL UNIQUE CHECK(length(session_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    worktree_id TEXT NOT NULL CHECK(length(worktree_id) = 36),
    template TEXT NOT NULL CHECK(template IN ('bug_fix','feature','research','refactor')),
    slug TEXT NOT NULL CHECK(length(slug) BETWEEN 1 AND 60),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 65536),
    budget_s INTEGER NOT NULL CHECK(budget_s BETWEEN 600 AND 86400),
    used_s INTEGER NOT NULL CHECK(used_s BETWEEN 0 AND 172800),
    state TEXT NOT NULL CHECK(state IN ('starting','working','waiting','stopped','done')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32),
    owner TEXT NOT NULL CHECK(length(owner) = 36),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    finished_at TEXT CHECK(finished_at IS NULL OR length(finished_at) = 24),
    CHECK((state IN ('stopped','done')) = (finished_at IS NOT NULL))
) STRICT""",
}
# Step 19: JEV brief episodes and the user's brief preferences. The primary key alone keeps
# (scope, created_at, episode_id) unique; that UNIQUE is there for its index, which
# `jev_store._prune` walks newest first instead of sorting the whole scope. Step 24 moves their
# rows into the decision-loop tables and drops both, so stores 19 to 23 alone hold them.
JEV_SCHEMA_STEP = 19
JEV_SCHEMAS = {
    "jev_episodes": """CREATE TABLE jev_episodes (
    episode_id TEXT PRIMARY KEY NOT NULL CHECK(length(episode_id) = 36),
    scope TEXT NOT NULL CHECK(length(scope) = 36),
    kind TEXT NOT NULL CHECK(kind IN ('decision','report')),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    day TEXT NOT NULL CHECK(length(day) = 10),
    trigger TEXT NOT NULL CHECK(trigger IN ('app_open','new_chat','review')),
    candidate TEXT NOT NULL CHECK(candidate IN
        ('approvals','tasks_due','agent_runs','inbox','uncommitted','routine','unwatched')),
    evidence TEXT NOT NULL CHECK(length(evidence) BETWEEN 2 AND 2048),
    evidence_key TEXT NOT NULL CHECK(length(evidence_key) = 64
        AND evidence_key NOT GLOB '*[^0-9a-f]*'),
    eligible TEXT CHECK(eligible IS NULL OR length(eligible) BETWEEN 2 AND 256),
    work TEXT CHECK(work IS NULL OR work IN ('none','draft','dispatch')),
    attention TEXT CHECK(attention IS NULL OR attention IN ('silent','defer','notify','ask')),
    defer_until TEXT CHECK(defer_until IS NULL OR length(defer_until) = 24),
    reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 64),
    policy_version TEXT NOT NULL CHECK(length(policy_version) BETWEEN 1 AND 32),
    mode TEXT NOT NULL CHECK(mode IN ('B0','B1','B2','B3')),
    block TEXT NOT NULL CHECK(length(block) <= 16),
    dedupe_key TEXT NOT NULL CHECK(length(dedupe_key) = 64 AND dedupe_key NOT GLOB '*[^0-9a-f]*'),
    brief_exposures INTEGER NOT NULL CHECK(brief_exposures BETWEEN 0 AND 1000000),
    rail_exposures INTEGER NOT NULL CHECK(rail_exposures BETWEEN 0 AND 1000000),
    first_exposed_at TEXT CHECK(first_exposed_at IS NULL OR length(first_exposed_at) = 24),
    last_exposed_at TEXT CHECK(last_exposed_at IS NULL OR length(last_exposed_at) = 24),
    opened_at TEXT CHECK(opened_at IS NULL OR length(opened_at) = 24),
    reviewed_at TEXT CHECK(reviewed_at IS NULL OR length(reviewed_at) = 24),
    observed_until TEXT NOT NULL CHECK(length(observed_until) = 24),
    channel_status TEXT NOT NULL CHECK(channel_status IN ('available','unavailable')),
    feedback TEXT CHECK(feedback IS NULL
        OR feedback IN ('useful','wrong','later','mute','missed','right_to_hold')),
    feedback_source TEXT CHECK(feedback_source IS NULL
        OR feedback_source IN ('brief','rail','review')),
    feedback_revision INTEGER NOT NULL CHECK(feedback_revision BETWEEN 0 AND 1000000),
    feedback_at TEXT CHECK(feedback_at IS NULL OR length(feedback_at) = 24),
    UNIQUE(scope, dedupe_key),
    UNIQUE(scope, created_at, episode_id),
    CHECK((kind = 'decision') = (attention IS NOT NULL)),
    CHECK((attention IS NULL) = (work IS NULL) AND (attention IS NULL) = (eligible IS NULL)),
    CHECK((attention IS 'defer') = (defer_until IS NOT NULL)),
    CHECK((feedback IS NULL) = (feedback_at IS NULL)
        AND (feedback IS NULL) = (feedback_source IS NULL))
) STRICT""",
    "jev_preferences": """CREATE TABLE jev_preferences (
    scope TEXT NOT NULL CHECK(length(scope) = 36),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 32),
    value TEXT NOT NULL CHECK(length(value) <= 64),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 1000000),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY(scope, name)
) STRICT""",
}
# Step 23: UI-saved messaging settings, sealed at rest (ADR-046). One row per provider.
MESSAGING_SCHEMA_STEP = 23
MESSAGING_SCHEMAS = {
    "messaging_config": """CREATE TABLE messaging_config (
    provider TEXT PRIMARY KEY NOT NULL CHECK(provider IN ('mattermost','slack','discord')),
    format INTEGER NOT NULL CHECK(format = 1),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 2 AND 65536),
    enabled_workspace TEXT CHECK(enabled_workspace IS NULL OR length(enabled_workspace) = 36),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24)
) STRICT""",
}
# Step 24 moves every step-19 episode into decision_episodes. Its kind becomes the entry; its
# attention a level (silent and defer to silent, notify and ask to list), which is also the rules'
# level, and a timing ('at' for defer, 'now' otherwise, none for a report); draft and dispatch
# work become prepare and run; its two exposure counts are summed; the rules decided it. Every
# column the copy does not name stays NULL, and mode, block, eligible and channel_status are not
# carried over. Each CASE lists every step-19 value, so any other value leaves a NULL the new
# table's CHECKs refuse.
_EPISODES_FROM_STEP_19 = """INSERT INTO decision_episodes (
    episode_id, scope, entry, created_at, day, trigger, candidate, evidence, evidence_key,
    policy, policy_version, work, level, timing, defer_until, reason, rules_level, exposures,
    first_exposed_at, last_exposed_at, opened_at, reviewed_at, observed_until, feedback,
    feedback_source, feedback_revision, feedback_at, dedupe_key)
SELECT episode_id, scope, kind, created_at, day, trigger, candidate, evidence, evidence_key,
    'rules', policy_version,
    CASE work WHEN 'none' THEN 'none' WHEN 'draft' THEN 'prepare' WHEN 'dispatch' THEN 'run' END,
    level,
    CASE attention WHEN 'silent' THEN 'now' WHEN 'defer' THEN 'at' WHEN 'notify' THEN 'now'
        WHEN 'ask' THEN 'now' END,
    defer_until, reason, level, brief_exposures + rail_exposures, first_exposed_at,
    last_exposed_at, opened_at, reviewed_at, observed_until, feedback, feedback_source,
    feedback_revision, feedback_at, dedupe_key
FROM (SELECT *, CASE attention WHEN 'silent' THEN 'silent' WHEN 'defer' THEN 'silent'
    WHEN 'notify' THEN 'list' WHEN 'ask' THEN 'list' END AS level FROM jev_episodes)"""
# Every step-19 preference but `study` moves unchanged.
_PREFERENCES_FROM_STEP_19 = (
    "INSERT INTO decision_preferences (scope, name, value, revision, updated_at) "
    "SELECT scope, name, value, revision, updated_at FROM jev_preferences WHERE name <> 'study'"
)
# Qualified model refs must be fenced from subscription-only readers through version 37.
SCHEMA_VERSION = INBOX_SUGGESTION_SCHEMA_STEP
_SETTING_COMMAND_LIMIT = 1024
_SETTINGS_FIELDS = ("version", "revision", "document", "updated_at")
_SETTING_COMMAND_FIELDS = ("command_id", "revision", "patch_hash", "applied_at")
_LEGACY_BINDING_FIELDS = ("result", "workspace_id", "code", "recorded_at")
_WORKSPACE_COLUMNS = (
    "workspace_id",
    "kind",
    "canonical_key",
    "display_root",
    "physical_id",
    "name",
    "policy",
    "policy_revision",
    "revision",
    "source",
    "layout",
    "portable_id",
    "identity",
    "face_dir",
    "bootstrap",
    "command_id",
    "active",
    "created_at",
    "updated_at",
)
_RECOGNISED_VERSIONS = (
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
    40,
    41,
    SCHEMA_VERSION,
)
_APPROVAL_FIELDS = (
    "choice_id",
    "session_id",
    "generation",
    "runtime_id",
    "request_id",
    "turn_id",
    "tool_use_id",
    "request_hash",
    "owner",
    "state",
    "reason",
    "created_at",
    "updated_at",
)
_COMMAND_FIELDS = (
    "command_id",
    "session_id",
    "generation",
    "source_draft_id",
    "draft_revision",
    "request_hash",
    "owner",
    "state",
    "reason",
    "format",
    "text_bytes",
    "created_at",
    "updated_at",
)
_SESSION_METADATA_FIELDS = (
    "session_id",
    "revision",
    "title",
    "pinned",
    "archived",
    "unread",
    "color",
)
_VAULT_PROPOSAL_FIELDS = (
    "proposal_id",
    "workspace_id",
    "ref",
    "origin",
    "base_sha256",
    "proposed_sha256",
    "body",
    "text_bytes",
    "handed_at",
    "created_at",
)
_SKILL_PROPOSAL_FIELDS = (
    "proposal_id",
    "workspace_id",
    "name",
    "trigger",
    "sessions",
    "base_sha256",
    "proposed_sha256",
    "body",
    "text_bytes",
    "approved_at",
    "rejected_at",
    "reason",
    "created_at",
    "updated_at",
)
_SKILL_PROPOSAL_CHANGEABLE = frozenset(
    (
        "body",
        "proposed_sha256",
        "base_sha256",
        "text_bytes",
        "approved_at",
        "rejected_at",
        "reason",
        "updated_at",
    )
)
# The states in which a row may still be handed to a driver future. `commands.py` and
# `approvals.py` own these names; storage cannot import either (both import storage), so a test
# pins each list against its owning module's definition instead.
_LIVE_COMMANDS = ("accepted", "dispatching")
_LIVE_APPROVALS = ("recorded", "delivering")
_SIDECARS = ("-journal", "-wal", "-shm")
_MESSAGES = {
    "busy": "The app store is busy. Close the other app that has it open, then try again.",
    "not_found": "Record not found.",
    "unavailable": (
        "Couldn't read or write the data. "
        "Check the save location's access rights and the disk's free space."
    ),
    "invalid_input": "This value doesn't meet the store's requirements. Check your input.",
    "corrupt": "The app store is damaged. Keep the original file and check the recovery steps.",
    "incompatible": (
        "This app store can't be opened by this version of VIKI. Open it with a compatible version."
    ),
    "invalid_legacy": (
        "Can't read the previous state.json. Keep the original file and check the recovery steps."
    ),
    "invalid_preferences": (
        "Session preferences contained a value that couldn't be used, so they weren't saved."
    ),
    "model_defaults_unavailable": (
        "Saved model settings are unavailable. Choose a model for this chat before starting."
    ),
    "model_choice_required": (
        "Choose a model for this existing chat before starting it with a model connection."
    ),
    "model_preview_changed": ("The model changed. Review it before sending again."),
    "recovery_required": (
        "The app store needs recovery. "
        "Quit VIKI, keep every related file in place, and check the recovery steps."
    ),
    "unsafe_path": (
        "The save location contains a link or a reparse point. "
        "Use a local, ordinary folder and file."
    ),
    "capacity": "The app store is full. Remove data you no longer need, then try again.",
    "conflict": (
        "The app store was updated by another operation. Check the latest state, then try again."
    ),
    "attachment_unavailable": (
        "The attached image is no longer on this PC, so it can't be used. "
        "Remove the attachment, then try again."
    ),
}


class StateError(RuntimeError):
    """A stable, safe error for startup and command boundaries; never a raw SQLite message."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(self.message)


SessionRecord = dict[str, Any]
DraftRecord = dict[str, Any]
CommandRow = dict[str, Any]
AssetRow = dict[str, Any]
ConversationArchiveRow = dict[str, Any]
ApprovalRow = dict[str, Any]
SettingsRow = dict[str, Any]
CommandReceipt = dict[str, Any]
WorkspaceRow = dict[str, Any]
LegacyBindingRow = dict[str, Any]
SessionRootRow = dict[str, Any]
WorktreeRow = dict[str, Any]


class BotCodeFolderRow(TypedDict):
    bot_id: str
    workspace_id: str
    created_at: str


class BotCodeCopyRow(TypedDict):
    worktree_id: str
    workspace_id: str
    bot_id: str
    session_id: str | None
    thread_id: str | None
    # `former`: its release finished (`finish_release`); still a Bot copy to Git, no Bot's else.
    state: Literal["bound", "released", "former"]
    created_at: str


class BotCodeBaselineRow(TypedDict):
    workspace_id: str
    git_fingerprint: str
    created_at: str


_BOT_CODE_FOLDER_FIELDS = ("bot_id", "workspace_id", "created_at")
_BOT_CODE_COPY_FIELDS = (
    "worktree_id",
    "workspace_id",
    "bot_id",
    "session_id",
    "thread_id",
    "state",
    "created_at",
)
_BOT_CODE_BASELINE_FIELDS = ("workspace_id", "git_fingerprint", "created_at")

_SESSION_ROOT_FIELDS = ("session_id", "workspace_id", "worktree_id", "display_root", "created_at")
_SESSION_ROOT_COLUMNS = (*_SESSION_ROOT_FIELDS, "additional_roots")
ROOT_LOOKUP_CHUNK = 500  # session ids one session_root_paths statement looks up
_WORKTREE_FIELDS = (
    "worktree_id",
    "workspace_id",
    "path",
    "branch",
    "base_oid",
    "state",
    "created_at",
    "updated_at",
)


class CheckRow(TypedDict):
    check_id: str
    workspace_id: str
    body: bytes
    timeout_s: int
    created_at: str


class CheckRunRow(TypedDict):
    run_id: str
    check_id: str
    workspace_id: str
    worktree_id: str | None
    head_oid: str | None
    dirty: int | None
    outcome: str
    exit_code: int | None
    duration_ms: int | None
    output_bytes: int | None
    tail: bytes | None
    started_at: str
    ended_at: str | None


# A run without its output: what a reader judging commits needs, without each run's sealed tail.
class CheckRunState(TypedDict):
    check_id: str
    worktree_id: str | None
    head_oid: str | None
    outcome: str


_CHECK_FIELDS = ("check_id", "workspace_id", "body", "timeout_s", "created_at")
_CHECK_RUN_FIELDS = (
    "run_id",
    "check_id",
    "workspace_id",
    "worktree_id",
    "head_oid",
    "dirty",
    "outcome",
    "exit_code",
    "duration_ms",
    "output_bytes",
    "tail",
    "started_at",
    "ended_at",
)
_CHECK_RUN_STATE_FIELDS = ("check_id", "worktree_id", "head_oid", "outcome")

_ASSET_FIELDS = (
    "scope",
    "asset_id",
    "media_type",
    "byte_size",
    "sha256",
    "width",
    "height",
    "created_at",
    "local_state",
    "local_removed_at",
)


def _error(exc: OSError | sqlite3.Error) -> StateError:
    code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
    if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return StateError("busy")
    if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return StateError("corrupt")
    if code == sqlite3.SQLITE_CONSTRAINT:
        # A CHECK/UNIQUE refusal is the caller's bad input, never a disk or permissions fault.
        return StateError("invalid_input")
    return StateError("unavailable")


def check_file(path: Path) -> bool:
    """Check the file and its existing ancestors without resolving through a link."""
    if path.drive.startswith("\\\\"):
        raise StateError("unsafe_path")
    exists = False
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise StateError("unsafe_path")
        if candidate == path:
            exists = True
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StateError("unsafe_path")
        elif not stat.S_ISDIR(info.st_mode):
            raise StateError("unsafe_path")
    return exists


if sys.platform != "win32":
    # Closing any descriptor on a file drops every POSIX lock this process holds on it, SQLite's
    # own included ("How To Corrupt An SQLite Database File", 2.2). So a store's header is read
    # through one shared descriptor per file, closed only once its last user has left.
    _header_lock = threading.Lock()
    _header_fds: dict[str, list[int]] = {}  # store path -> [descriptor, users]

    @contextlib.contextmanager
    def _header_descriptor(path: Path) -> Iterator[int]:
        key = str(path)
        with _header_lock:
            held = _header_fds.get(key)
            if held is None:
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                held = _header_fds[key] = [os.open(path, flags), 0]
            held[1] += 1
        try:
            yield held[0]
        finally:
            with _header_lock:
                held[1] -= 1
                if held[1] == 0:
                    del _header_fds[key]
                    os.close(held[0])


class SessionStore:
    def __init__(
        self,
        path: Path,
        initial_sessions: Callable[[], dict[str, SessionRecord]],
    ) -> None:
        self.path = path.absolute()
        try:
            exists = self._check_paths()
            if not exists:
                # Parse before creating anything: malformed JSON is never an empty first run.
                records = initial_sessions()
                if not self.path.parent.exists():
                    self.path.parent.parent.mkdir(parents=True, exist_ok=True)
                    # Set the private descriptor only while creating a new app-owned leaf.
                    # Existing destinations are never chmod'd; draft admission checks their ACLs.
                    protection.create_private_directory(self.path.parent)
                self._create(records)
            with self._connection(versions=_RECOGNISED_VERSIONS) as connection:
                if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise StateError("corrupt")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                self._migrate()
        except OSError as exc:
            raise _error(exc) from None
        except protection.ProtectionError:
            raise StateError("unavailable") from None

    def _check_paths(self) -> bool:
        exists = check_file(self.path)
        sidecars = [check_file(Path(f"{self.path}{suffix}")) for suffix in _SIDECARS]
        if not exists and any(sidecars):
            # A journal with no main database is recovery material, not ours to discard.
            raise StateError("corrupt")
        return exists

    def _validate(
        self, connection: sqlite3.Connection, versions: tuple[int, ...] = (SCHEMA_VERSION,)
    ) -> None:
        identity = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY name"
        ).fetchall()
        session_schema = _SCHEMA if version >= 7 else _SESSION_SCHEMA_V6
        # The three tables that carry a format token were rebuilt twice: at step 6, which added
        # the column, and at step 10, which widened its CHECK to format 3.
        if version < 6:
            draft_schema, command_schema = _DRAFT_SCHEMA, _COMMAND_SCHEMA
        elif version < 10:
            draft_schema, command_schema = _DRAFT_SCHEMA_V6, _COMMAND_SCHEMA_V6
        else:
            draft_schema, command_schema = _DRAFT_SCHEMA_V10, _COMMAND_SCHEMA_V10
        queued_turn_schema = _QUEUED_TURN_SCHEMA if version < 10 else _QUEUED_TURN_SCHEMA_V10
        workspace_schema = _WORKSPACE_SCHEMA if version < 14 else _WORKSPACE_SCHEMA_V14
        metadata_schema = (
            _SESSION_METADATA_SCHEMA if version >= 21 else _SESSION_METADATA_SCHEMA_V20
        )
        expected = [("table", "sessions", "sessions", session_schema)]
        if version >= 2:
            expected.insert(0, ("table", "drafts", "drafts", draft_schema))
        if version >= 3:
            expected.insert(0, ("table", "commands", "commands", command_schema))
        if version >= 4:
            expected.insert(0, ("table", "approvals", "approvals", _APPROVAL_SCHEMA))
        if version >= 5:
            # sqlite_schema is read in name order, so each version 5 object goes where it sorts:
            # legacy_binding between drafts and sessions, the rest after sessions.
            expected.insert(
                3, ("table", "legacy_binding", "legacy_binding", _LEGACY_BINDING_SCHEMA)
            )
            expected.extend(
                (
                    ("table", "setting_commands", "setting_commands", _SETTING_COMMAND_SCHEMA),
                    ("table", "settings", "settings", _SETTINGS_SCHEMA),
                    ("table", "workspaces", "workspaces", workspace_schema),
                    ("index", "workspaces_active", "workspaces", _WORKSPACE_ACTIVE_INDEX),
                )
            )
        if version >= 6:
            # SQLite orders these after approvals and before commands.  Keeping the exact schema
            # check means a partial asset migration is never accepted as an ordinary database.
            expected.insert(1, ("table", "asset_refs", "asset_refs", _ASSET_REF_SCHEMA))
            expected.insert(2, ("table", "assets", "assets", _ASSET_SCHEMA))
            expected.insert(
                4,
                (
                    "table",
                    "conversation_archives",
                    "conversation_archives",
                    _CONVERSATION_ARCHIVE_SCHEMA,
                ),
            )
            expected.insert(7, ("table", "session_metadata", "session_metadata", metadata_schema))
            expected.append(("table", "queued_turns", "queued_turns", queued_turn_schema))
            expected.append(("table", "turn_queues", "turn_queues", _TURN_QUEUE_SCHEMA))
            expected.sort(key=lambda value: value[1])
        if version >= 7:
            profiles = PROFILE_SCHEMAS_V40 if version >= BOT_CODE_SCHEMA_STEP else PROFILE_SCHEMAS
            expected.extend(("table", name, name, sql) for name, sql in profiles.items())
            expected.sort(key=lambda value: value[1])
        if version >= 8:
            routines = ROUTINE_SCHEMAS if version >= 29 else ROUTINE_SCHEMAS_BEFORE_29
            expected.extend(("table", name, name, sql) for name, sql in routines.items())
            expected.append(
                ("index", "task_attempts_active", "task_attempts", TASK_ATTEMPTS_ACTIVE_INDEX)
            )
            expected.sort(key=lambda value: value[1])
        if version >= 9:
            expected.extend(("table", name, name, sql) for name, sql in BOOKMARK_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= 11:
            expected.extend(("table", name, name, sql) for name, sql in DECISION_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= 12:
            expected.extend(("table", name, name, sql) for name, sql in OPERATION_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= 13:
            expected.extend(("table", name, name, sql) for name, sql in MEMORY_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        else:
            # Step 13 rebuilt two tables to widen one CHECK in each; older stores keep the text.
            expected = [
                (kind, name, table, SCHEMAS_BEFORE_13.get(name, sql))
                for kind, name, table, sql in expected
            ]
        if version >= 15:
            roots = ROOT_SCHEMAS_V20 if version >= 20 else ROOT_SCHEMAS
            expected.extend(("table", name, name, sql) for name, sql in roots.items())
            expected.sort(key=lambda value: value[1])
        if version >= 16:
            expected.extend(("table", name, name, sql) for name, sql in CHECK_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= 17:
            expected.extend(("table", name, name, sql) for name, sql in SKILL_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= 18:
            expected.extend(("table", name, name, sql) for name, sql in TASK_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if JEV_SCHEMA_STEP <= version < DECISION_LOOP_SCHEMA_STEP:
            expected.extend(("table", name, name, sql) for name, sql in JEV_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= BOT_MEMORY_SCHEMA_STEP:
            expected.extend(("table", name, name, sql) for name, sql in BOT_MEMORY_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= BOT_SCHEMA_STEP:
            expected.extend(("table", name, name, sql) for name, sql in BOT_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= BOT_THREAD_SCHEMA_STEP:
            expected.extend(("table", name, name, sql) for name, sql in BOT_THREAD_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= BOT_CODE_SCHEMA_STEP:
            expected.extend(("table", name, name, sql) for name, sql in BOT_CODE_SCHEMAS.items())
            expected.extend(
                ("index", name, "bot_code_copies", sql) for name, sql in BOT_CODE_INDEXES.items()
            )
            expected.sort(key=lambda value: value[1])
        if version >= BOT_USAGE_SCHEMA_STEP:
            expected.extend(("table", name, name, sql) for name, sql in BOT_USAGE_SCHEMAS.items())
            expected.extend(
                ("index", name, "bot_usage_calls", sql) for name, sql in BOT_USAGE_INDEXES.items()
            )
            expected.sort(key=lambda value: value[1])
        if version >= INBOX_SUGGESTION_SCHEMA_STEP:
            expected.extend(
                ("table", name, name, sql) for name, sql in INBOX_SUGGESTION_SCHEMAS.items()
            )
            expected.sort(key=lambda value: value[1])
        if version >= MESSAGING_SCHEMA_STEP:
            expected.extend(("table", name, name, sql) for name, sql in MESSAGING_SCHEMAS.items())
            expected.sort(key=lambda value: value[1])
        if version >= DECISION_LOOP_SCHEMA_STEP:
            expected.extend(
                ("table", name, name, sql) for name, sql in DECISION_LOOP_SCHEMAS.items()
            )
            expected.sort(key=lambda value: value[1])
        if version >= DECISION_PROPOSALS_SCHEMA_STEP:
            expected.extend(
                ("table", name, name, sql) for name, sql in DECISION_PROPOSAL_SCHEMAS.items()
            )
            expected.sort(key=lambda value: value[1])
        if (
            identity != APPLICATION_ID
            or version not in versions
            or schema != expected
            or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
        ):
            raise StateError("incompatible")

    @contextlib.contextmanager
    def _connection(
        self, *, write: bool = False, versions: tuple[int, ...] = (SCHEMA_VERSION,)
    ) -> Iterator[sqlite3.Connection]:
        # Holds the POSIX header descriptor until the `finally` below has closed the connection.
        with contextlib.ExitStack() as held:
            connection: sqlite3.Connection | None = None
            try:
                if not self._check_paths():
                    raise StateError("unavailable")
                if sys.platform == "win32":
                    with self.path.open("rb") as source:
                        header = source.read(100)
                else:
                    fd = held.enter_context(_header_descriptor(self.path))
                    if not os.path.samestat(os.fstat(fd), os.lstat(self.path)):
                        raise StateError("unavailable")  # The path now names another file.
                    header = os.pread(fd, 100, 0)
                if len(header) != 100 or header[:16] != b"SQLite format 3\x00":
                    raise StateError("corrupt")
                if (
                    int.from_bytes(header[68:72], "big") != APPLICATION_ID
                    or int.from_bytes(header[60:64], "big") not in versions
                    or header[18:20] != b"\x01\x01"
                ):
                    # A read-only SQLite connection can still create WAL shared-memory files.
                    # Reject foreign/versioned/WAL headers before SQLite sees the path at all.
                    raise StateError("incompatible")
                # Validate read-only first. Even an automatic hot-journal recovery must not write
                # an unvalidated store; a store needing that recovery fails safely for repair.
                connection = sqlite3.connect(
                    f"{self.path.as_uri()}?mode=ro",
                    uri=True,
                    timeout=BUSY_TIMEOUT_S,
                    isolation_level=None,
                )
                self._validate(connection, versions)
                if write:
                    connection.close()
                    connection = None
                    self._check_paths()
                    connection = sqlite3.connect(
                        f"{self.path.as_uri()}?mode=rw",
                        uri=True,
                        timeout=BUSY_TIMEOUT_S,
                        isolation_level=None,
                    )
                    connection.execute("PRAGMA synchronous=FULL")
                    self._validate(connection, versions)
                    connection.execute("BEGIN IMMEDIATE")
                    self._validate(connection, versions)
                yield connection
                if write:
                    connection.execute("COMMIT")
            except (OSError, sqlite3.Error) as exc:
                if (
                    isinstance(exc, sqlite3.Error)
                    and getattr(exc, "sqlite_errorcode", 0) == sqlite3.SQLITE_READONLY_ROLLBACK
                ):
                    raise StateError("recovery_required") from None
                raise _error(exc) from None
            finally:
                if connection is not None:
                    # Closing rolls back an unfinished transaction, including failure at COMMIT.
                    connection.close()

    def _create(self, records: dict[str, SessionRecord]) -> None:
        descriptor, name = tempfile.mkstemp(
            prefix=".viki-state-", suffix=".sqlite3", dir=self.path.parent
        )
        os.close(descriptor)
        stage = Path(name)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(stage, isolation_level=None, timeout=BUSY_TIMEOUT_S)
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.execute(_SCHEMA)
            connection.execute(_DRAFT_SCHEMA_V10)
            connection.execute(_COMMAND_SCHEMA_V10)
            connection.execute(_APPROVAL_SCHEMA)
            connection.execute(_ASSET_SCHEMA)
            connection.execute(_ASSET_REF_SCHEMA)
            connection.execute(_CONVERSATION_ARCHIVE_SCHEMA)
            connection.execute(_QUEUED_TURN_SCHEMA_V10)
            connection.execute(_TURN_QUEUE_SCHEMA)
            connection.execute(_SESSION_METADATA_SCHEMA)
            connection.execute(_LEGACY_BINDING_SCHEMA)
            connection.execute(_WORKSPACE_SCHEMA_V14)
            connection.execute(_WORKSPACE_ACTIVE_INDEX)
            connection.execute(_SETTINGS_SCHEMA)
            connection.execute(_SETTING_COMMAND_SCHEMA)
            for sql in PROFILE_SCHEMAS_V40.values():
                connection.execute(sql)
            for sql in ROUTINE_SCHEMAS.values():
                connection.execute(sql)
            connection.execute(TASK_ATTEMPTS_ACTIVE_INDEX)
            for sql in BOOKMARK_SCHEMAS.values():
                connection.execute(sql)
            for sql in DECISION_SCHEMAS.values():
                connection.execute(sql)
            for sql in OPERATION_SCHEMAS.values():
                connection.execute(sql)
            for sql in MEMORY_SCHEMAS.values():
                connection.execute(sql)
            for sql in ROOT_SCHEMAS_V20.values():
                connection.execute(sql)
            for sql in CHECK_SCHEMAS.values():
                connection.execute(sql)
            for sql in SKILL_SCHEMAS.values():
                connection.execute(sql)
            for sql in TASK_SCHEMAS.values():
                connection.execute(sql)
            for sql in BOT_SCHEMAS.values():
                connection.execute(sql)
            for sql in BOT_MEMORY_SCHEMAS.values():
                connection.execute(sql)
            for sql in BOT_THREAD_SCHEMAS.values():
                connection.execute(sql)
            for sql in (*BOT_CODE_SCHEMAS.values(), *BOT_CODE_INDEXES.values()):
                connection.execute(sql)
            for sql in (*BOT_USAGE_SCHEMAS.values(), *BOT_USAGE_INDEXES.values()):
                connection.execute(sql)
            for sql in INBOX_SUGGESTION_SCHEMAS.values():
                connection.execute(sql)
            for sql in MESSAGING_SCHEMAS.values():
                connection.execute(sql)
            for sql in DECISION_LOOP_SCHEMAS.values():
                connection.execute(sql)
            for sql in DECISION_PROPOSAL_SCHEMAS.values():
                connection.execute(sql)
            connection.executemany(
                "INSERT INTO sessions (session_id, mode, effort, model) VALUES (?, ?, ?, ?)",
                [(sid, row["mode"], row["effort"], row["model"]) for sid, row in records.items()],
            )
            if connection.execute("SELECT count(*) FROM sessions").fetchone()[0] != len(records):
                raise StateError("corrupt")
            connection.execute("COMMIT")
            self._validate(connection)
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise StateError("corrupt")
            connection.close()
            connection = None
            if self._check_paths():
                return  # Another constructor won; validate its database in __init__.
            # A concurrent publisher is validated by the constructor.
            with contextlib.suppress(FileExistsError):
                # The rename refuses an existing destination on every OS, so a store another
                # constructor published first is never replaced.
                osfs.rename_no_replace(stage, self.path)
            osfs.fsync_dir(self.path.parent)
        except (OSError, sqlite3.Error) as exc:
            raise _error(exc) from None
        finally:
            if connection is not None:
                connection.close()
            # These are uniquely named files this call created, never the selected database
            # or any pre-existing recovery file. Crash leftovers can remain for inspection.
            for leftover in (stage, *(Path(f"{stage}{suffix}") for suffix in _SIDECARS)):
                with contextlib.suppress(OSError):
                    leftover.unlink(missing_ok=True)

    def _migrate(self) -> None:
        # Bounded to every step one call can cross: anything below 4 reaches 4 in one step, then
        # one step per version up to SCHEMA_VERSION, plus the final confirmation. Each step is its
        # own reserved transaction with its own validated backup, so an interruption between them
        # leaves a store that validates at the version its newest backup names.
        for _ in range(SCHEMA_VERSION - 2):
            with self._connection(write=True, versions=_RECOGNISED_VERSIONS) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version == SCHEMA_VERSION:
                    return  # A concurrent constructor already migrated the validated store.
                self._migrate_step(connection, version)
        raise StateError("corrupt")

    def _migrate_step(self, connection: sqlite3.Connection, version: int) -> None:
        # The writer reservation excludes other migrations and preference commits. Readers
        # can still inspect v1 while a separate read-only connection makes its backup.
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table, since in (
                ("sessions", 1),
                ("drafts", 2),
                ("commands", 3),
                ("approvals", 4),
                ("workspaces", 5),
                ("queued_turns", 6),
                ("session_metadata", 6),
                ("vault_publications", 7),
                ("vault_proposals", 11),
                ("session_roots", 15),
                ("session_profile_bindings", 7),
                ("session_bot_memory", 37),
            )
            if version >= since
        }
        self._backup(version)
        if version < 4:
            if version == 1:
                connection.execute(_DRAFT_SCHEMA)
            if version <= 2:
                connection.execute(_COMMAND_SCHEMA)
            connection.execute(_APPROVAL_SCHEMA)
            target, added = 4, ("approvals",)
        elif version == 4:
            connection.execute(_LEGACY_BINDING_SCHEMA)
            connection.execute(_WORKSPACE_SCHEMA)
            connection.execute(_WORKSPACE_ACTIVE_INDEX)
            connection.execute(_SETTINGS_SCHEMA)
            connection.execute(_SETTING_COMMAND_SCHEMA)
            target = 5
            added = ("legacy_binding", "workspaces", "settings", "setting_commands")
        elif version == 5:
            # Rebuild rather than ALTER: validation compares the full canonical CREATE text, and
            # explicit format values distinguish legacy encrypted text from v2 TurnInput JSON.
            connection.execute("ALTER TABLE drafts RENAME TO drafts_v5")
            connection.execute("ALTER TABLE commands RENAME TO commands_v5")
            connection.execute(_DRAFT_SCHEMA_V6)
            connection.execute(_COMMAND_SCHEMA_V6)
            connection.execute(
                "INSERT INTO drafts (scope,key,revision,body,format,text_bytes,saved_at) "
                "SELECT scope,key,revision,body,1,text_bytes,saved_at FROM drafts_v5"
            )
            connection.execute(
                "INSERT INTO commands "
                "(scope,command_id,session_id,generation,source_draft_id,draft_revision,request_hash,owner,"
                "state,reason,body,format,text_bytes,created_at,updated_at) "
                "SELECT scope,command_id,session_id,generation,session_id,draft_revision,"
                "request_hash,owner,"
                "state,reason,body,1,text_bytes,created_at,updated_at FROM commands_v5"
            )
            connection.execute("DROP TABLE drafts_v5")
            connection.execute("DROP TABLE commands_v5")
            connection.execute(_ASSET_SCHEMA)
            connection.execute(_ASSET_REF_SCHEMA)
            connection.execute(_CONVERSATION_ARCHIVE_SCHEMA)
            connection.execute(_QUEUED_TURN_SCHEMA)
            connection.execute(_TURN_QUEUE_SCHEMA)
            connection.execute(_SESSION_METADATA_SCHEMA_V20)
            target = 6
            added = (
                "assets",
                "asset_refs",
                "conversation_archives",
                "queued_turns",
                "turn_queues",
                "session_metadata",
            )
        elif version == 6:
            connection.execute("ALTER TABLE sessions RENAME TO sessions_v6")
            connection.execute(_SCHEMA)
            connection.execute(
                "INSERT INTO sessions (session_id,mode,effort,model,origin) "
                "SELECT session_id,mode,effort,model,'app' FROM sessions_v6"
            )
            connection.execute("DROP TABLE sessions_v6")
            for name, sql in PROFILE_SCHEMAS.items():
                connection.execute(SCHEMAS_BEFORE_13.get(name, sql))
            target, added = 7, tuple(PROFILE_SCHEMAS)
        elif version == 7:
            for sql in ROUTINE_SCHEMAS_BEFORE_29.values():
                connection.execute(sql)
            connection.execute(TASK_ATTEMPTS_ACTIVE_INDEX)
            target, added = 8, tuple(ROUTINE_SCHEMAS)
        elif version == 8:
            for sql in BOOKMARK_SCHEMAS.values():
                connection.execute(sql)
            target, added = 9, tuple(BOOKMARK_SCHEMAS)
        elif version == 9:
            # Rebuild again, as step 6 did: validation compares the full canonical CREATE text,
            # so widening the format CHECK to the excerpt-carrying format 3 is a new table and
            # a copy of every column, never an ALTER.
            connection.execute("ALTER TABLE drafts RENAME TO drafts_v9")
            connection.execute("ALTER TABLE commands RENAME TO commands_v9")
            connection.execute("ALTER TABLE queued_turns RENAME TO queued_turns_v9")
            connection.execute(_DRAFT_SCHEMA_V10)
            connection.execute(_COMMAND_SCHEMA_V10)
            connection.execute(_QUEUED_TURN_SCHEMA_V10)
            connection.execute(
                "INSERT INTO drafts (scope,key,revision,body,format,text_bytes,saved_at) "
                "SELECT scope,key,revision,body,format,text_bytes,saved_at FROM drafts_v9"
            )
            connection.execute(
                "INSERT INTO commands "
                "(scope,command_id,session_id,generation,source_draft_id,draft_revision,"
                "request_hash,owner,state,reason,body,format,text_bytes,created_at,updated_at) "
                "SELECT scope,command_id,session_id,generation,source_draft_id,draft_revision,"
                "request_hash,owner,state,reason,body,format,text_bytes,created_at,updated_at "
                "FROM commands_v9"
            )
            connection.execute(
                "INSERT INTO queued_turns "
                "(scope,item_id,session_id,ordinal,source_draft_id,source_draft_revision,body,"
                "format,text_bytes,created_at,updated_at) "
                "SELECT scope,item_id,session_id,ordinal,source_draft_id,source_draft_revision,"
                "body,format,text_bytes,created_at,updated_at FROM queued_turns_v9"
            )
            connection.execute("DROP TABLE drafts_v9")
            connection.execute("DROP TABLE commands_v9")
            connection.execute("DROP TABLE queued_turns_v9")
            target, added = 10, ()
        elif version == 10:
            for name, sql in DECISION_SCHEMAS.items():
                connection.execute(SCHEMAS_BEFORE_13.get(name, sql))
            target, added = 11, tuple(DECISION_SCHEMAS)
        elif version == 11:
            for sql in OPERATION_SCHEMAS.values():
                connection.execute(sql)
            target, added = 12, tuple(OPERATION_SCHEMAS)
        elif version == 12:
            # Rebuild, as steps 6 and 10 did: a STRICT CHECK cannot be widened in place, so the
            # larger stage bound and the memory-promotion origin are new tables and a copy of
            # every column. The counts above cover both tables, so a lost row fails the step.
            for table, ddl, columns in (
                (
                    "vault_publications",
                    PROFILE_SCHEMAS["vault_publications"],
                    "operation_id,workspace_id,root_physical_id,policy_revision,ref,kind,"
                    "stage_name,backup_name,stage_physical_id,stage_sha256,stage_bytes,"
                    "target_physical_id,expected_sha256,target_bytes,state,created_at,updated_at",
                ),
                (
                    "vault_proposals",
                    DECISION_SCHEMAS["vault_proposals"],
                    "proposal_id,workspace_id,ref,origin,base_sha256,proposed_sha256,body,"
                    "text_bytes,handed_at,created_at",
                ),
            ):
                connection.execute(f"ALTER TABLE {table} RENAME TO {table}_v12")
                connection.execute(ddl)
                connection.execute(
                    f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {table}_v12"
                )
                connection.execute(f"DROP TABLE {table}_v12")
            for sql in MEMORY_SCHEMAS.values():
                connection.execute(sql)
            target, added = 13, tuple(MEMORY_SCHEMAS)
        elif version == 13:
            # Rebuild once more to admit code folders in the kind CHECK. The old table's index
            # goes with it, so it is re-created after the drop; the counts above cover the rows.
            connection.execute("ALTER TABLE workspaces RENAME TO workspaces_v13")
            connection.execute(_WORKSPACE_SCHEMA_V14)
            columns = ",".join(_WORKSPACE_COLUMNS)
            connection.execute(
                f"INSERT INTO workspaces ({columns}) SELECT {columns} FROM workspaces_v13"
            )
            connection.execute("DROP TABLE workspaces_v13")
            connection.execute(_WORKSPACE_ACTIVE_INDEX)
            target, added = 14, ()
        elif version == 14:
            for sql in ROOT_SCHEMAS.values():
                connection.execute(sql)
            target, added = 15, tuple(ROOT_SCHEMAS)
        elif version == 15:
            for sql in CHECK_SCHEMAS.values():
                connection.execute(sql)
            target, added = 16, tuple(CHECK_SCHEMAS)
        elif version == 16:
            for sql in SKILL_SCHEMAS.values():
                connection.execute(sql)
            target, added = 17, tuple(SKILL_SCHEMAS)
        elif version == 17:
            for sql in TASK_SCHEMAS.values():
                connection.execute(sql)
            target, added = 18, tuple(TASK_SCHEMAS)
        elif version == 18:
            for sql in JEV_SCHEMAS.values():
                connection.execute(sql)
            target, added = JEV_SCHEMA_STEP, tuple(JEV_SCHEMAS)
        elif version == 19:
            connection.execute("ALTER TABLE session_roots RENAME TO session_roots_v19")
            connection.execute(ROOT_SCHEMAS_V20["session_roots"])
            columns = ",".join(_SESSION_ROOT_FIELDS)
            connection.execute(
                f"INSERT INTO session_roots ({columns}) SELECT {columns} FROM session_roots_v19"
            )
            connection.execute("DROP TABLE session_roots_v19")
            target, added = 20, ()
        elif version == 20:
            connection.execute("ALTER TABLE session_metadata RENAME TO session_metadata_v20")
            connection.execute(_SESSION_METADATA_SCHEMA)
            columns = "scope,session_id,revision,title,pinned,archived"
            connection.execute(
                f"INSERT INTO session_metadata ({columns},unread,color) "
                f"SELECT {columns},0,NULL FROM session_metadata_v20"
            )
            connection.execute("DROP TABLE session_metadata_v20")
            target, added = 21, ()
        elif version == 21:
            for sql in BOT_SCHEMAS.values():
                connection.execute(sql)
            target, added = BOT_SCHEMA_STEP, tuple(BOT_SCHEMAS)
        elif version == 22:
            for sql in MESSAGING_SCHEMAS.values():
                connection.execute(sql)
            target, added = MESSAGING_SCHEMA_STEP, tuple(MESSAGING_SCHEMAS)
        elif version == 23:
            # Move the step-19 rows, then drop their tables. The count after this chain covers
            # neither new table, so each copy is counted here against its own source.
            for sql in DECISION_LOOP_SCHEMAS.values():
                connection.execute(sql)
            for copy, table, source in (
                (_EPISODES_FROM_STEP_19, "decision_episodes", "SELECT count(*) FROM jev_episodes"),
                (
                    _PREFERENCES_FROM_STEP_19,
                    "decision_preferences",
                    "SELECT count(*) FROM jev_preferences WHERE name <> 'study'",
                ),
            ):
                connection.execute(copy)
                if (
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()
                    != connection.execute(source).fetchone()
                ):
                    raise StateError("corrupt")
            connection.execute("DROP TABLE jev_episodes")
            connection.execute("DROP TABLE jev_preferences")
            target, added = (
                DECISION_LOOP_SCHEMA_STEP,
                ("decision_items", "decision_calls", "decision_missions", "decision_key"),
            )
        elif version == 24:
            # Version 24: add the proposal table; no row moves.
            for sql in DECISION_PROPOSAL_SCHEMAS.values():
                connection.execute(sql)
            target, added = DECISION_PROPOSALS_SCHEMA_STEP, tuple(DECISION_PROPOSAL_SCHEMAS)
        elif version == 25:
            # Version 25: a fence only, no table change (ADR-059). With no step here, a
            # schema-25 (PR C) build could open a 26-stamped store and misread its new rows
            # (e.g. improve.py::_worst_topic_feature raises KeyError once a new type gets 5
            # wrong labels, and _close_window catches only DecisionLoopError). This step makes
            # that build refuse the store outright, as 24 and 25 do: rollback means reverting to
            # that build and restoring this step's verified backup, dropping every row written
            # after the upgrade (ADR-058's story for 25).
            target, added = 26, ()
        elif version == 26:
            # Version 26: a fence only, no table change (ADR-061). With no step here, an older
            # build could open a 27-stamped store and misread its new skill_suggest rows. This
            # step makes that build refuse the store outright, as 24, 25 and 26 do: rollback
            # means reverting to that build and restoring this step's verified backup, dropping
            # every row written after the upgrade.
            target, added = 27, ()
        elif version == 27:
            # Version 27: a fence only, no table change (ADR-064). With no step here, a
            # schema-27 build could open a 28-stamped store and misread its new card_needs_you
            # and card_stale rows (improve.py::_worst_topic_feature raises KeyError once a card
            # type gets 5 wrong labels, and parse.ts rejects a candidate outside CANDIDATES).
            # This step makes that build refuse the store outright, as 24 to 27 do: rollback
            # means reverting to that build and restoring this step's verified backup, dropping
            # every row written after the upgrade.
            target, added = 28, ()
        elif version == 28:
            # Version 28: rebuild routine_occurrences to admit the decision kind, the one record
            # of a run Decision starts for a seat (ADR-065). A STRICT CHECK cannot be altered in
            # place, so the rows move to the new text and are counted against their source; the
            # counts above do not cover this table. The step also fences contracts.SEATS: a
            # schema-28 build refuses the store outright rather than misread a decision row or
            # a seat item.
            # Rollback means reverting to that build and restoring this step's verified backup,
            # dropping every row written after the upgrade.
            connection.execute("ALTER TABLE routine_occurrences RENAME TO routine_occurrences_v28")
            connection.execute(ROUTINE_SCHEMAS["routine_occurrences"])
            columns = (
                "occurrence_id,routine_id,kind,scheduled_for,trigger_id,request_hash,"
                "revision,state,reason,recorded_at"
            )
            connection.execute(
                f"INSERT INTO routine_occurrences ({columns}) "
                f"SELECT {columns} FROM routine_occurrences_v28"
            )
            if (
                connection.execute("SELECT count(*) FROM routine_occurrences").fetchone()
                != connection.execute("SELECT count(*) FROM routine_occurrences_v28").fetchone()
            ):
                raise StateError("corrupt")
            connection.execute("DROP TABLE routine_occurrences_v28")
            target, added = 29, ()
        elif version == 29:
            # Version 29: a fence only, no table change (ADR-066). With no step here, a
            # schema-29 build could open a 30-stamped store and misread its new Look item rows
            # (improve.py::_worst_topic_feature raises KeyError once a Look type gets 5 wrong
            # labels, and parse.ts rejects a candidate outside CANDIDATES). This step makes that
            # build refuse the store outright, as 24 to 29 do: rollback means reverting to that
            # build and restoring this step's verified backup, dropping every row written after
            # the upgrade.
            target, added = 30, ()
        elif version == 30:
            # Version 30: a fence only, no table change (ADR-067). With no step here, a
            # schema-30 build could open a 31-stamped store and misread its new code_look rows
            # (improve.py::_worst_topic_feature raises KeyError once a Look type gets 5 wrong
            # labels, and parse.ts rejects a candidate outside CANDIDATES). This step makes that
            # build refuse the store outright, as 24 to 30 do: rollback means reverting to that
            # build and restoring this step's verified backup, dropping every row written after
            # the upgrade.
            target, added = 31, ()
        elif version == 31:
            # Version 31: a fence only, no table change (ADR-068). With no step here, a
            # schema-31 build could open a 32-stamped store and misread its new long_task_look
            # and correction_look rows (improve.py::_worst_topic_feature raises KeyError once a
            # second-look type gets 5 wrong labels, and parse.ts rejects a candidate outside
            # CANDIDATES). This step makes that build refuse the store outright, as 24 to 31 do:
            # rollback means reverting to that build and restoring this step's verified backup,
            # dropping every row written after the upgrade.
            target, added = 32, ()
        elif version == 32:
            # Version 32: a fence only, no table change (ADR-069). With no step here, a
            # schema-32 build could open a 33-stamped store and misread its new stalled_project
            # and overdue_tasks rows (improve.py::_worst_topic_feature raises KeyError once a
            # project-plan type gets 5 wrong labels, and parse.ts rejects a candidate outside
            # CANDIDATES). This step makes that build refuse the store outright, as 24 to 32 do:
            # rollback means reverting to that build and restoring this step's verified backup,
            # dropping every row written after the upgrade.
            target, added = 33, ()
        elif version == 33:
            # Version 33: a fence only, no table change (ADR-070). With no step here, a
            # schema-33 build could open a 34-stamped store and misread its new context_handoff
            # and source_note rows (improve.py::_worst_topic_feature raises KeyError once a
            # message-draft type gets 5 wrong labels, and parse.ts rejects a candidate outside
            # CANDIDATES). This step makes that build refuse the store outright, as 24 to 33 do:
            # rollback means reverting to that build and restoring this step's verified backup,
            # dropping every row written after the upgrade.
            target, added = 34, ()
        elif version == 34:
            # Version 34: a fence only, no table change (ADR-071). With no step here, a
            # schema-34 build could open a 35-stamped store and misread its new commit_checks
            # rows (improve.py::_worst_topic_feature raises KeyError once a saved-check type gets
            # 5 wrong labels, and parse.ts rejects a candidate outside CANDIDATES). This step
            # makes that build refuse the store outright, as 24 to 34 do: rollback means
            # reverting to that build and restoring this step's verified backup, dropping every
            # row written after the upgrade.
            target, added = 35, ()
        elif version == 35:
            # Version 35: a fence only, no table change (ADR-072). With no step here, a
            # schema-35 build could open a 36-stamped store and misread its new inbox_triage
            # rows (improve.py::_worst_topic_feature raises KeyError once an inbox type gets 5
            # wrong labels, and parse.ts rejects a candidate outside CANDIDATES). This step makes
            # that build refuse the store outright, as 24 to 35 do: rollback means reverting to
            # that build and restoring this step's verified backup, dropping every row written
            # after the upgrade.
            target, added = 36, ()
        elif version == 36:
            for sql in BOT_MEMORY_SCHEMAS.values():
                connection.execute(sql)
            target, added = BOT_MEMORY_SCHEMA_STEP, tuple(BOT_MEMORY_SCHEMAS)
        elif version == 37:
            # Qualified model refs select sealed endpoint connections. Older builds treat them
            # as native Claude models, so readers through 37 must refuse it before reading prefs.
            target, added = 38, ()
        elif version == 38:
            for sql in BOT_THREAD_SCHEMAS.values():
                connection.execute(sql)
            target, added = BOT_THREAD_SCHEMA_STEP, tuple(BOT_THREAD_SCHEMAS)
        elif version == 40:
            for sql in (*BOT_USAGE_SCHEMAS.values(), *BOT_USAGE_INDEXES.values()):
                connection.execute(sql)
            target, added = BOT_USAGE_SCHEMA_STEP, tuple(BOT_USAGE_SCHEMAS)
        elif version == 41:
            for sql in INBOX_SUGGESTION_SCHEMAS.values():
                connection.execute(sql)
            target, added = INBOX_SUGGESTION_SCHEMA_STEP, tuple(INBOX_SUGGESTION_SCHEMAS)
        else:
            connection.execute(
                "ALTER TABLE session_profile_bindings ADD COLUMN " + LAUNCH_ORIGIN_COLUMN
            )
            # Retained attempts are positive routine evidence even for a Bot's main chat.
            connection.execute(
                "UPDATE session_profile_bindings SET launch_origin='routine' "
                "WHERE session_id IN (SELECT session_id FROM task_attempts)"
            )
            connection.execute(
                "UPDATE session_profile_bindings SET launch_origin='interactive' "
                "WHERE launch_origin='unknown' AND EXISTS ("
                "SELECT 1 FROM bots b "
                "JOIN session_bot_memory m ON m.bot_id=b.bot_id "
                "AND m.session_id=b.chat_session_id "
                "JOIN sessions s ON s.session_id=m.session_id AND s.origin='profile' "
                "JOIN workspaces w ON w.workspace_id=b.workspace_id AND w.kind='vault' "
                "WHERE b.chat_session_id=session_profile_bindings.session_id "
                "AND b.profile_id=session_profile_bindings.profile_id "
                "AND b.workspace_id=session_profile_bindings.workspace_id)"
            )
            for sql in (*BOT_CODE_SCHEMAS.values(), *BOT_CODE_INDEXES.values()):
                connection.execute(sql)
            target, added = BOT_CODE_SCHEMA_STEP, tuple(BOT_CODE_SCHEMAS)
        connection.execute(f"PRAGMA user_version={target}")
        self._validate(connection, (target,))
        expected = {**counts, **dict.fromkeys(added, 0)}
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)] or any(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] != value
            for table, value in expected.items()
        ):
            raise StateError("corrupt")

    def _backup(self, version: int) -> None:
        descriptor, name = tempfile.mkstemp(
            prefix=".viki-migration-", suffix=".sqlite3", dir=self.path.parent
        )
        os.close(descriptor)
        stage = Path(name)
        destination = self.path.with_name(
            f"{self.path.name}.v{version}-backup-{uuid.uuid4()}.sqlite3"
        )
        try:
            # backup() cannot use the connection holding the write transaction as its source.
            with (
                self._connection(versions=(version,)) as source,
                contextlib.closing(sqlite3.connect(stage, isolation_level=None)) as backup,
            ):
                backup.execute("PRAGMA synchronous=FULL")
                source.backup(backup, pages=256, sleep=0)
                self._validate(backup, (version,))
                if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise StateError("corrupt")
            if check_file(destination):
                raise StateError("unavailable")
            osfs.rename_no_replace(stage, destination)  # Never replaces, as for initial creation.
            osfs.fsync_dir(destination.parent)
            # Validate the published recovery file before changing the source store.
            if not check_file(destination):
                raise StateError("unavailable")
            with contextlib.closing(
                sqlite3.connect(f"{destination.as_uri()}?mode=ro", uri=True)
            ) as published:
                self._validate(published, (version,))
                if published.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise StateError("corrupt")
        finally:
            for leftover in (stage, *(Path(f"{stage}{suffix}") for suffix in _SIDECARS)):
                with contextlib.suppress(OSError):
                    leftover.unlink(missing_ok=True)

    def get(self, session_id: str) -> SessionRecord | None:
        with self._connection() as connection:
            return self._get(connection, session_id)

    def session_origin(self, session_id: str) -> Literal["app", "profile"] | None:
        """Return the durable session kind, refusing a missing or stray profile binding."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT s.origin,b.session_id FROM sessions s "
                "LEFT JOIN session_profile_bindings b ON b.session_id=s.session_id "
                "WHERE s.session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                if connection.execute(
                    "SELECT 1 FROM session_profile_bindings WHERE session_id=?", (session_id,)
                ).fetchone():
                    raise StateError("corrupt")
                return None
        origin, binding = row
        if origin not in {"app", "profile"} or ((origin == "profile") != (binding is not None)):
            raise StateError("corrupt")
        return origin

    def profile_launch_origin(
        self, session_id: str
    ) -> Literal["unknown", "interactive", "routine"] | None:
        """Read the binding's durable provenance without inferring it from other metadata."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT launch_origin FROM session_profile_bindings WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _get(connection: sqlite3.Connection, session_id: str) -> SessionRecord | None:
        row = connection.execute(
            "SELECT mode, effort, model FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return dict(zip(("mode", "effort", "model"), row, strict=True)) if row else None

    def update(
        self,
        session_id: str,
        changed: Callable[[SessionRecord | None], SessionRecord],
    ) -> SessionRecord:
        with self._connection(write=True) as connection:
            record = changed(self._get(connection, session_id))
            connection.execute(
                "INSERT INTO sessions (session_id, mode, effort, model) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "mode=excluded.mode, effort=excluded.effort, model=excluded.model",
                (session_id, record["mode"], record["effort"], record["model"]),
            )
        return record  # The context committed successfully; no in-memory success precedes it.

    @staticmethod
    def _get_draft(connection: sqlite3.Connection, scope: str, key: str) -> DraftRecord | None:
        row = connection.execute(
            "SELECT revision,body,format,text_bytes,saved_at FROM drafts WHERE scope=? AND key=?",
            (scope, key),
        ).fetchone()
        return (
            dict(zip(("revision", "body", "format", "text_bytes", "saved_at"), row, strict=True))
            if row
            else None
        )

    def get_draft(self, scope: str, key: str) -> DraftRecord | None:
        with self._connection() as connection:
            return self._get_draft(connection, scope, key)

    def list_drafts(self, scope: str, after: str, limit: int) -> list[DraftRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT key, revision, text_bytes, saved_at FROM drafts "
                "WHERE scope=? AND key>? AND (text_bytes>0 OR EXISTS "
                "(SELECT 1 FROM asset_refs WHERE scope=drafts.scope AND owner_kind='draft' "
                "AND owner_id=drafts.key)) ORDER BY key LIMIT ?",
                (scope, after, limit),
            ).fetchall()
        return [
            dict(zip(("key", "revision", "text_bytes", "saved_at"), row, strict=True))
            for row in rows
        ]

    def update_draft(
        self,
        scope: str,
        key: str,
        changed: Callable[[DraftRecord | None, tuple[int, int, int]], DraftRecord | None],
    ) -> DraftRecord | None:
        with self._connection(write=True) as connection:
            current = self._get_draft(connection, scope, key)
            usage = connection.execute(
                "SELECT count(*), COALESCE(sum(text_bytes>0 OR EXISTS "
                "(SELECT 1 FROM asset_refs WHERE scope=drafts.scope AND owner_kind='draft' "
                "AND owner_id=drafts.key)),0), COALESCE(sum(text_bytes),0) "
                "FROM drafts WHERE scope=?",
                (scope,),
            ).fetchone()
            candidate = changed(current, usage)
            if candidate is not None and candidate != current:
                connection.execute(
                    "INSERT INTO drafts (scope,key,revision,body,format,text_bytes,saved_at) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET "
                    "revision=excluded.revision,body=excluded.body,"
                    "format=excluded.format,text_bytes=excluded.text_bytes,saved_at=excluded.saved_at",
                    (
                        scope,
                        key,
                        candidate["revision"],
                        candidate["body"],
                        candidate["format"],
                        candidate["text_bytes"],
                        candidate["saved_at"],
                    ),
                )
                # This is the legacy text-only writer. A v2 attachment draft edited through an
                # older window becomes text-only, so its owner-set must disappear atomically.
                self._replace_asset_refs(
                    connection,
                    scope,
                    "draft",
                    key,
                    (),
                    created_at=candidate["saved_at"],
                )
        return candidate

    @staticmethod
    def _asset_rows(
        connection: sqlite3.Connection,
        scope: str,
        asset_ids: tuple[str, ...],
        *,
        require_stored: bool = False,
    ) -> list[AssetRow]:
        if len(asset_ids) > 32 or len(set(asset_ids)) != len(asset_ids):
            raise StateError("invalid_input")
        if not asset_ids:
            return []
        rows = connection.execute(
            f"SELECT {','.join(_ASSET_FIELDS)} FROM assets "
            f"WHERE scope=? AND asset_id IN ({','.join('?' for _ in asset_ids)})",
            (scope, *asset_ids),
        ).fetchall()
        found: dict[str, AssetRow] = {}
        for value in rows:
            row: AssetRow = dict(zip(_ASSET_FIELDS, value, strict=True))
            found[row["asset_id"]] = row
        # A draft or a submission may only hold bytes still on this PC; a row that is
        # gone or expired is the user's attachment problem, not a storage failure.
        if require_stored and (
            len(found) != len(asset_ids)
            or any(row["local_state"] != "stored" for row in found.values())
        ):
            raise StateError("attachment_unavailable")
        if len(found) != len(asset_ids):
            raise StateError("not_found")
        return [found[asset_id] for asset_id in asset_ids]

    @staticmethod
    def _replace_asset_refs(
        connection: sqlite3.Connection,
        scope: str,
        owner_kind: str,
        owner_id: str,
        refs: tuple[tuple[str, str], ...],
        *,
        created_at: str,
        require_stored: bool = False,
    ) -> tuple[str, ...]:
        if (
            not 1 <= len(scope) <= 128
            or not 1 <= len(owner_kind) <= 64
            or not 1 <= len(owner_id) <= 128
            or len(refs) > 32
            or any(
                not 1 <= len(asset_id) == 36 or not 1 <= len(role) <= 64 for asset_id, role in refs
            )
            or len({role for _, role in refs}) != len(refs)
        ):
            raise StateError("invalid_input")
        SessionStore._asset_rows(
            connection,
            scope,
            tuple(asset_id for asset_id, _ in refs),
            require_stored=require_stored,
        )
        removed = tuple(
            row[0]
            for row in connection.execute(
                "SELECT asset_id FROM asset_refs WHERE scope=? AND owner_kind=? AND owner_id=? "
                "ORDER BY role",
                (scope, owner_kind, owner_id),
            ).fetchall()
        )
        connection.execute(
            "DELETE FROM asset_refs WHERE scope=? AND owner_kind=? AND owner_id=?",
            (scope, owner_kind, owner_id),
        )
        connection.executemany(
            "INSERT INTO asset_refs (scope,asset_id,owner_kind,owner_id,role,created_at) "
            "VALUES (?,?,?,?,?,?)",
            [(scope, asset_id, owner_kind, owner_id, role, created_at) for asset_id, role in refs],
        )
        return removed

    def update_draft_input(
        self,
        scope: str,
        key: str,
        refs: tuple[tuple[str, str], ...],
        changed: Callable[
            [DraftRecord | None, tuple[int, int, int], list[AssetRow]], DraftRecord | None
        ],
    ) -> DraftRecord | None:
        """Update one v2 draft and its complete asset owner-set in one transaction."""
        with self._connection(write=True) as connection:
            current = self._get_draft(connection, scope, key)
            usage = connection.execute(
                "SELECT count(), COALESCE(sum(text_bytes>0 OR EXISTS "
                "(SELECT 1 FROM asset_refs WHERE scope=drafts.scope AND owner_kind='draft' "
                "AND owner_id=drafts.key)),0), COALESCE(sum(text_bytes),0) "
                "FROM drafts WHERE scope=?",
                (scope,),
            ).fetchone()
            metadata = self._asset_rows(
                connection, scope, tuple(asset_id for asset_id, _ in refs), require_stored=True
            )
            candidate = changed(current, usage, metadata)
            if candidate is not None and candidate != current:
                connection.execute(
                    "INSERT INTO drafts (scope,key,revision,body,format,text_bytes,saved_at) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET "
                    "revision=excluded.revision,body=excluded.body,format=excluded.format,"
                    "text_bytes=excluded.text_bytes,saved_at=excluded.saved_at",
                    (
                        scope,
                        key,
                        candidate["revision"],
                        candidate["body"],
                        candidate["format"],
                        candidate["text_bytes"],
                        candidate["saved_at"],
                    ),
                )
            if candidate != current:
                self._replace_asset_refs(
                    connection,
                    scope,
                    "draft",
                    key,
                    refs,
                    created_at=(candidate or current or {"saved_at": ""})["saved_at"],
                )
        return candidate

    def put_asset(self, row: AssetRow) -> None:
        """Publish validated app-owned asset metadata; assets.py owns filesystem publication."""
        base_fields = _ASSET_FIELDS[:8]
        if set(row) != set(base_fields) and set(row) != set(_ASSET_FIELDS):
            raise StateError("invalid_input")
        value = {field: row[field] for field in base_fields}
        value["local_state"] = row.get("local_state", "stored")
        value["local_removed_at"] = row.get("local_removed_at")
        if value["local_state"] != "stored" or value["local_removed_at"] is not None:
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            connection.execute(
                f"INSERT INTO assets ({','.join(_ASSET_FIELDS)}) "
                f"VALUES ({','.join('?' for _ in _ASSET_FIELDS)})",
                tuple(value[field] for field in _ASSET_FIELDS),
            )

    def asset_metadata(self, scope: str, asset_ids: tuple[str, ...]) -> list[AssetRow]:
        with self._connection() as connection:
            return self._asset_rows(connection, scope, asset_ids)

    def find_assets_by_digest(
        self, scope: str, sha256: str, media_type: str, byte_size: int, *, limit: int = 10
    ) -> list[AssetRow]:
        """Bounded same-scope candidates for read-only native-history correlation."""
        if (
            not 1 <= limit <= 10
            or len(sha256) != 64
            or any(char not in "0123456789abcdef" for char in sha256)
            or not isinstance(media_type, str)
            or type(byte_size) is not int
            or byte_size < 1
        ):
            raise StateError("invalid_input")
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_ASSET_FIELDS)} FROM assets "
                "WHERE scope=? AND sha256=? AND media_type=? AND byte_size=? "
                "ORDER BY CASE local_state WHEN 'stored' THEN 0 ELSE 1 END, "
                "created_at DESC,asset_id ASC LIMIT ?",
                (scope, sha256, media_type, byte_size, limit),
            ).fetchall()
        return [dict(zip(_ASSET_FIELDS, row, strict=True)) for row in rows]

    def asset_usage(self, scope: str) -> tuple[int, int]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT count(*), COALESCE(sum(byte_size),0) FROM assets "
                "WHERE scope=? AND local_state='stored'",
                (scope,),
            ).fetchone()
        return row[0], row[1]

    def replace_asset_refs(
        self,
        scope: str,
        owner_kind: str,
        owner_id: str,
        refs: tuple[tuple[str, str], ...],
        created_at: str,
    ) -> tuple[str, ...]:
        with self._connection(write=True) as connection:
            return self._replace_asset_refs(
                connection, scope, owner_kind, owner_id, refs, created_at=created_at
            )

    def list_unreferenced_assets(self, scope: str, before: str, limit: int) -> list[AssetRow]:
        if not 1 <= limit <= 100:
            raise StateError("invalid_input")
        with self._connection() as connection:
            # One read of the scope's refs: asset_refs has no (scope, asset_id) index and the
            # schema is frozen, so a correlated subquery would rescan them for every asset.
            rows = connection.execute(
                f"SELECT {','.join(f'a.{field}' for field in _ASSET_FIELDS)} "
                "FROM assets a WHERE a.scope=? AND a.created_at<? AND a.asset_id NOT IN "
                "(SELECT asset_id FROM asset_refs WHERE scope=?) "
                "ORDER BY a.created_at,a.asset_id LIMIT ?",
                (scope, before, scope, limit),
            ).fetchall()
        return [dict(zip(_ASSET_FIELDS, row, strict=True)) for row in rows]

    def list_stored_command_assets(self, scope: str, before: str, limit: int) -> list[AssetRow]:
        """List assets whose only refs are command snapshots older than ``before``."""
        if not 1 <= limit <= 100:
            raise StateError("invalid_input")
        with self._connection() as connection:
            # One grouped read of the scope's refs, not three correlated scans per asset
            # (asset_refs has no (scope, asset_id) index and the schema is frozen): at the caps
            # that read held the store 60 s.
            rows = connection.execute(
                f"SELECT {','.join(f'a.{field}' for field in _ASSET_FIELDS)} FROM assets a "
                "JOIN (SELECT asset_id,max(owner_kind='command') AS cmd,"
                "max(owner_kind<>'command') AS other,"
                "max(owner_kind='command' AND created_at>=?) AS recent "
                "FROM asset_refs WHERE scope=? GROUP BY asset_id) r ON r.asset_id=a.asset_id "
                "WHERE a.scope=? AND a.local_state='stored' AND a.created_at<? "
                "AND r.cmd AND NOT r.other AND NOT r.recent "
                "ORDER BY a.created_at,a.asset_id LIMIT ?",
                (before, scope, scope, before, limit),
            ).fetchall()
        return [dict(zip(_ASSET_FIELDS, row, strict=True)) for row in rows]

    def expire_asset_if_due(
        self, scope: str, asset_id: str, command_before: str, removed_at: str
    ) -> bool:
        """Atomically tombstone a command-only stored asset once all command refs are old."""
        with self._connection(write=True) as connection:
            result = connection.execute(
                "UPDATE assets SET local_state='expired',local_removed_at=? "
                "WHERE scope=? AND asset_id=? AND local_state='stored' "
                "AND EXISTS (SELECT 1 FROM asset_refs r WHERE r.scope=assets.scope "
                "AND r.asset_id=assets.asset_id AND r.owner_kind='command') "
                "AND NOT EXISTS (SELECT 1 FROM asset_refs r WHERE r.scope=assets.scope "
                "AND r.asset_id=assets.asset_id AND r.owner_kind<>'command') "
                "AND NOT EXISTS (SELECT 1 FROM asset_refs r WHERE r.scope=assets.scope "
                "AND r.asset_id=assets.asset_id AND r.owner_kind='command' AND r.created_at>=?)",
                (removed_at, scope, asset_id, command_before),
            )
        return result.rowcount == 1

    def delete_asset_if_unreferenced(
        self, scope: str, asset_id: str, *, created_before: str | None = None
    ) -> bool:
        """Delete one unreferenced row; `created_before` refuses a row a restore made fresh."""
        sql = (
            "DELETE FROM assets WHERE scope=? AND asset_id=? AND NOT EXISTS "
            "(SELECT 1 FROM asset_refs WHERE scope=? AND asset_id=?)"
        )
        values: tuple[str, ...] = (scope, asset_id, scope, asset_id)
        if created_before is not None:
            sql += " AND created_at<?"
            values += (created_before,)
        with self._connection(write=True) as connection:
            result = connection.execute(sql, values)
        return result.rowcount == 1

    def asset_scopes(self, after: str, limit: int) -> list[str]:
        """Page through the scopes that have asset rows, in order, strictly after `after`."""
        if not 1 <= limit <= 100:
            raise StateError("invalid_input")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT scope FROM assets WHERE scope>? ORDER BY scope LIMIT ?",
                (after, limit),
            ).fetchall()
        return [row[0] for row in rows]

    def restore_expired_asset(self, row: AssetRow, created_at: str) -> bool:
        """Mark an expired asset stored again once its bytes are back, stamped `created_at`.

        Only a row whose type, size, digest and dimensions all equal `row`'s changes, so a
        different file never revives the asset. Returns whether one row changed.
        """
        if not isinstance(created_at, str) or len(created_at) != 24:
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            result = connection.execute(
                "UPDATE assets SET local_state='stored',local_removed_at=NULL,created_at=? "
                "WHERE scope=? AND asset_id=? AND local_state='expired' AND media_type=? "
                "AND byte_size=? AND sha256=? AND width=? AND height=?",
                # scope, asset_id, media_type, byte_size, sha256, width, height, in order.
                (created_at, *(row[field] for field in _ASSET_FIELDS[:7])),
            )
        return result.rowcount == 1

    def insert_conversation_archive(self, scope: str, row: ConversationArchiveRow) -> None:
        """Store one already-encrypted readonly archive, bounded with the capacity check."""
        fields = ("archive_id", "created_at", "body", "byte_size")
        if set(row) != set(fields) or not isinstance(row.get("body"), bytes):
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            count, total = connection.execute(
                "SELECT count(*),COALESCE(sum(byte_size),0) FROM conversation_archives "
                "WHERE scope=?",
                (scope,),
            ).fetchone()
            if count >= 64 or total + row["byte_size"] > 64 << 20:
                raise StateError("capacity")
            connection.execute(
                "INSERT INTO conversation_archives (scope,archive_id,created_at,body,byte_size) "
                "VALUES (?,?,?,?,?)",
                (scope, *(row[field] for field in fields)),
            )

    def list_conversation_archives(self, scope: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT archive_id,created_at,byte_size FROM conversation_archives "
                "WHERE scope=? ORDER BY created_at DESC,archive_id DESC LIMIT 65",
                (scope,),
            ).fetchall()
        if len(rows) > 64:
            raise StateError("corrupt")
        return [
            dict(zip(("archive_id", "created_at", "byte_size"), row, strict=True)) for row in rows
        ]

    def get_conversation_archive(
        self, scope: str, archive_id: str
    ) -> ConversationArchiveRow | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT archive_id,created_at,body,byte_size FROM conversation_archives "
                "WHERE scope=? AND archive_id=?",
                (scope, archive_id),
            ).fetchone()
        fields = ("archive_id", "created_at", "body", "byte_size")
        return dict(zip(fields, row, strict=True)) if row else None

    def remove_conversation_archive(self, scope: str, archive_id: str) -> bool:
        with self._connection(write=True) as connection:
            result = connection.execute(
                "DELETE FROM conversation_archives WHERE scope=? AND archive_id=?",
                (scope, archive_id),
            )
        return result.rowcount == 1

    @staticmethod
    def _get_session_metadata(
        connection: sqlite3.Connection, scope: str, session_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            f"SELECT {','.join(_SESSION_METADATA_FIELDS)} FROM session_metadata "
            "WHERE scope=? AND session_id=?",
            (scope, session_id),
        ).fetchone()
        return dict(zip(_SESSION_METADATA_FIELDS, row, strict=True)) if row else None

    def get_session_metadata(self, scope: str, session_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return self._get_session_metadata(connection, scope, session_id)

    def list_session_metadata(self, scope: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_SESSION_METADATA_FIELDS)} FROM session_metadata "
                "WHERE scope=? ORDER BY pinned DESC,session_id LIMIT ?",
                (scope, MAX_SESSION_METADATA + 1),
            ).fetchall()
        if len(rows) > MAX_SESSION_METADATA:
            raise StateError("corrupt")
        return [dict(zip(_SESSION_METADATA_FIELDS, row, strict=True)) for row in rows]

    def update_session_metadata(
        self,
        scope: str,
        session_id: str,
        changed: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._connection(write=True) as connection:
            current = self._get_session_metadata(connection, scope, session_id)
            row = changed(current)
            if set(row) != set(_SESSION_METADATA_FIELDS) or row.get("session_id") != session_id:
                raise StateError("invalid_input")
            if current is None:
                count = connection.execute(
                    "SELECT count(*) FROM session_metadata WHERE scope=?", (scope,)
                ).fetchone()[0]
                if count >= MAX_SESSION_METADATA:
                    raise StateError("capacity")
            if row != current:
                fields = ("scope", *_SESSION_METADATA_FIELDS)
                connection.execute(
                    f"INSERT INTO session_metadata ({','.join(fields)}) "
                    f"VALUES ({','.join('?' for _ in fields)}) ON CONFLICT(scope,session_id) "
                    "DO UPDATE SET revision=excluded.revision,title=excluded.title,"
                    "pinned=excluded.pinned,archived=excluded.archived,"
                    "unread=excluded.unread,color=excluded.color",
                    (scope, *(row[field] for field in _SESSION_METADATA_FIELDS)),
                )
        return row

    def delete_session_metadata(
        self,
        scope: str,
        session_id: str,
        guard: Callable[[dict[str, Any] | None], None],
    ) -> bool:
        """Delete one label if `guard`, shown the current row or None, does not raise.

        The read, the guard and the delete share one write transaction, so the guard's
        revision check cannot race another writer. Returns whether a row was deleted.
        """
        with self._connection(write=True) as connection:
            guard(self._get_session_metadata(connection, scope, session_id))
            copies = connection.execute(
                "SELECT c.worktree_id,w.canonical_key FROM bot_code_copies c "
                "JOIN session_profile_bindings b ON b.session_id=c.session_id "
                "JOIN workspaces w ON w.workspace_id=b.workspace_id "
                "WHERE c.state='bound' AND c.session_id=?",
                (session_id,),
            ).fetchall()
            affected = connection.execute(
                "SELECT r.thread_id,w.canonical_key FROM bot_thread_sessions r "
                "JOIN bot_threads t ON t.thread_id=r.thread_id "
                "JOIN workspaces w ON w.workspace_id=t.workspace_id "
                "WHERE r.session_id=?",
                (session_id,),
            ).fetchall()
            threads = [
                thread_id
                for thread_id, canonical_key in affected
                if hashlib.sha256(canonical_key.encode("utf-8")).hexdigest() == scope
            ]
            released = [
                worktree_id
                for worktree_id, canonical_key in copies
                if hashlib.sha256(canonical_key.encode("utf-8")).hexdigest() == scope
            ]
            for thread_id in threads:
                released.extend(
                    row[0]
                    for row in connection.execute(
                        "SELECT worktree_id FROM bot_code_copies "
                        "WHERE state='bound' AND thread_id=?",
                        (thread_id,),
                    )
                )
            self._release_copies(connection, released)
            for thread_id in threads:
                connection.execute(
                    "DELETE FROM bot_thread_sessions WHERE thread_id=?", (thread_id,)
                )
                connection.execute("DELETE FROM bot_threads WHERE thread_id=?", (thread_id,))
            return (
                connection.execute(
                    "DELETE FROM session_metadata WHERE scope=? AND session_id=?",
                    (scope, session_id),
                ).rowcount
                == 1
            )

    @staticmethod
    def _get_command(
        connection: sqlite3.Connection, scope: str, command_id: str
    ) -> CommandRow | None:
        fields = (*_COMMAND_FIELDS, "body")
        row = connection.execute(
            f"SELECT {','.join(fields)} FROM commands WHERE scope=? AND command_id=?",
            (scope, command_id),
        ).fetchone()
        return dict(zip(fields, row, strict=True)) if row else None

    def get_command(self, scope: str, command_id: str) -> CommandRow | None:
        with self._connection() as connection:
            return self._get_command(connection, scope, command_id)

    def asset_source_command(self, scope: str, asset_id: str) -> CommandRow | None:
        """Read one encrypted snapshot retaining name and provenance after asset expiry."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT c.command_id FROM asset_refs r JOIN commands c "
                "ON c.scope=r.scope AND c.command_id=r.owner_id "
                "WHERE r.scope=? AND r.asset_id=? AND r.owner_kind='command' "
                "ORDER BY c.created_at,c.command_id LIMIT 1",
                (scope, asset_id),
            ).fetchone()
            return self._get_command(connection, scope, row[0]) if row else None

    def list_commands(self, scope: str, after: str, limit: int) -> list[CommandRow]:
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_COMMAND_FIELDS)} FROM commands "
                "WHERE scope=? AND command_id>? ORDER BY command_id LIMIT ?",
                (scope, after, limit),
            ).fetchall()
        return [dict(zip(_COMMAND_FIELDS, row, strict=True)) for row in rows]

    @staticmethod
    def _find_draft_command(
        connection: sqlite3.Connection,
        scope: str,
        session_id: str,
        source_draft_id: str,
        draft_revision: int,
    ) -> CommandRow | None:
        row = connection.execute(
            f"SELECT {','.join(_COMMAND_FIELDS)} FROM commands "
            "WHERE scope=? AND session_id=? AND source_draft_id=? AND draft_revision=? "
            "ORDER BY created_at, command_id LIMIT 1",
            (scope, session_id, source_draft_id, draft_revision),
        ).fetchone()
        return dict(zip(_COMMAND_FIELDS, row, strict=True)) if row else None

    def find_draft_command(
        self, scope: str, session_id: str, source_draft_id: str, draft_revision: int
    ) -> CommandRow | None:
        """Find the earliest retained acceptance without loading protected submitted text."""
        with self._connection() as connection:
            return self._find_draft_command(
                connection, scope, session_id, source_draft_id, draft_revision
            )

    def accept_command(
        self,
        scope: str,
        command_id: str,
        session_id: str,
        draft_revision: int,
        source_draft_id: str,
        changed: Callable[
            [CommandRow | None, DraftRecord | None, tuple[int, int], CommandRow | None], CommandRow
        ],
        asset_refs: Callable[[DraftRecord | None], tuple[tuple[str, str], ...]] | None = None,
    ) -> tuple[CommandRow, bool]:
        with self._connection(write=True) as connection:
            current = self._get_command(connection, scope, command_id)
            draft = self._get_draft(connection, scope, session_id)
            usage = connection.execute(
                "SELECT count(*), COALESCE(sum(text_bytes),0) FROM commands WHERE scope=?",
                (scope,),
            ).fetchone()
            prior = (
                self._find_draft_command(
                    connection, scope, session_id, source_draft_id, draft_revision
                )
                if current is None
                else None
            )
            row = changed(current, draft, usage, prior)
            if current is None:
                fields = ("scope", *_COMMAND_FIELDS, "body")
                connection.execute(
                    f"INSERT INTO commands ({','.join(fields)}) "
                    f"VALUES ({','.join('?' for _ in fields)})",
                    (scope, *(row[field] for field in fields[1:])),
                )
                if asset_refs is not None:
                    self._replace_asset_refs(
                        connection,
                        scope,
                        "command",
                        command_id,
                        asset_refs(draft),
                        created_at=row["created_at"],
                        require_stored=True,
                    )
        return row, current is None

    def update_command(
        self,
        scope: str,
        command_id: str,
        changed: Callable[[CommandRow | None], CommandRow],
    ) -> CommandRow:
        with self._connection(write=True) as connection:
            current = self._get_command(connection, scope, command_id)
            row = changed(current)
            if row != current:
                connection.execute(
                    "UPDATE commands SET state=?,reason=?,updated_at=? "
                    "WHERE scope=? AND command_id=?",
                    (row["state"], row["reason"], row["updated_at"], scope, command_id),
                )
        return row

    @staticmethod
    def _get_approval(
        connection: sqlite3.Connection, scope: str, choice_id: str
    ) -> ApprovalRow | None:
        fields = (*_APPROVAL_FIELDS, "body")
        row = connection.execute(
            f"SELECT {','.join(fields)} FROM approvals WHERE scope=? AND choice_id=?",
            (scope, choice_id),
        ).fetchone()
        return dict(zip(fields, row, strict=True)) if row else None

    def get_approval(self, scope: str, choice_id: str) -> ApprovalRow | None:
        with self._connection() as connection:
            return self._get_approval(connection, scope, choice_id)

    def list_approvals(self, scope: str, after: str, limit: int) -> list[ApprovalRow]:
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_APPROVAL_FIELDS)} FROM approvals "
                "WHERE scope=? AND choice_id>? ORDER BY choice_id LIMIT ?",
                (scope, after, limit),
            ).fetchall()
        return [dict(zip(_APPROVAL_FIELDS, row, strict=True)) for row in rows]

    @staticmethod
    def _find_request_approval(
        connection: sqlite3.Connection,
        scope: str,
        session_id: str,
        runtime_id: str,
        request_id: str,
    ) -> ApprovalRow | None:
        row = connection.execute(
            f"SELECT {','.join(_APPROVAL_FIELDS)} FROM approvals "
            "WHERE scope=? AND session_id=? AND runtime_id=? AND request_id=?",
            (scope, session_id, runtime_id, request_id),
        ).fetchone()
        return dict(zip(_APPROVAL_FIELDS, row, strict=True)) if row else None

    def find_request_approval(
        self, scope: str, session_id: str, runtime_id: str, request_id: str
    ) -> ApprovalRow | None:
        """The one recorded choice for a native request, without loading its protected body."""
        with self._connection() as connection:
            return self._find_request_approval(
                connection, scope, session_id, runtime_id, request_id
            )

    def record_approval(
        self,
        scope: str,
        choice_id: str,
        session_id: str,
        runtime_id: str,
        request_id: str,
        changed: Callable[[ApprovalRow | None, ApprovalRow | None, tuple[int, int]], ApprovalRow],
    ) -> tuple[ApprovalRow, bool]:
        """Insert one choice, or return the committed row already under this ID.

        The same-ID row, the same-request row and the scope usage are all read inside the
        writer reservation, so the per-request uniqueness rule cannot be raced past.
        """
        with self._connection(write=True) as connection:
            current = self._get_approval(connection, scope, choice_id)
            prior = (
                self._find_request_approval(connection, scope, session_id, runtime_id, request_id)
                if current is None
                else None
            )
            usage = connection.execute(
                # Ciphertext bytes, never a plaintext length: a stored plaintext size would
                # publish the one-byte difference between an allow and a deny.
                "SELECT count(*), COALESCE(sum(length(body)),0) FROM approvals WHERE scope=?",
                (scope,),
            ).fetchone()
            row = changed(current, prior, usage)
            if current is None:
                fields = ("scope", *_APPROVAL_FIELDS, "body")
                connection.execute(
                    f"INSERT INTO approvals ({','.join(fields)}) "
                    f"VALUES ({','.join('?' for _ in fields)})",
                    (scope, *(row[field] for field in fields[1:])),
                )
        return row, current is None

    def update_approval(
        self,
        scope: str,
        choice_id: str,
        changed: Callable[[ApprovalRow | None], ApprovalRow],
    ) -> ApprovalRow:
        with self._connection(write=True) as connection:
            current = self._get_approval(connection, scope, choice_id)
            row = changed(current)
            if row != current:
                connection.execute(
                    "UPDATE approvals SET state=?,reason=?,updated_at=? "
                    "WHERE scope=? AND choice_id=?",
                    (row["state"], row["reason"], row["updated_at"], scope, choice_id),
                )
        return row

    def reclaim_approvals(
        self, scope: str, owner: str, *, max_rows: int, max_bytes: int, incoming: int
    ) -> int:
        """Delete the oldest terminal rows a previous launch wrote, until one more row of
        `incoming` ciphertext bytes fits under both quotas. Returns how many were deleted.

        `owner<>?` only: a row this launch wrote may still be the one a card on screen looks up,
        may still be the id a tab holds for an unheard POST, and its native request may still be
        live, so deleting it would let a second choice be recorded for a request that is given
        exactly one. A previous launch's runtime_id died with that launch's driver process, and
        only one launch can hold this store, so none of those are true of its rows. Terminal only:
        a nonterminal row is the only durable evidence of a choice whose delivery was never
        observed.

        The read, the walk and the delete share one `BEGIN IMMEDIATE`, so the set cannot change
        under the walk, and the `LIMIT` subquery re-derives the same eligible prefix in the same
        order. `created_at` is fixed-width ISO-8601 `Z`, so it sorts lexicographically, and
        `choice_id` breaks same-millisecond ties; the order is therefore total. Both quota numbers
        and the incoming size come from the caller, so no policy constant enters the store.
        """
        if len(owner) != 36:
            raise StateError("invalid_input")
        marks = ",".join("?" for _ in _LIVE_APPROVALS)
        with self._connection(write=True) as connection:
            # One pass carries both the scope totals and the eligibility flags the walk needs;
            # a separate count(*)/sum() aggregate would read the same rows a second time.
            observed = connection.execute(
                f"SELECT length(body), (owner<>? AND state NOT IN ({marks})) FROM approvals "
                "WHERE scope=? ORDER BY created_at, choice_id",
                (owner, *_LIVE_APPROVALS, scope),
            ).fetchall()
            rows = len(observed)
            total = sum(size for size, _ in observed)
            take = 0
            for size, eligible in observed:
                if rows < max_rows and total + incoming <= max_bytes:
                    break
                if not eligible:
                    continue
                rows -= 1
                total -= size
                take += 1
            if not take:
                return 0
            # A LIMIT subquery rather than a 4,096-placeholder IN list, so nothing depends on
            # SQLITE_MAX_VARIABLE_NUMBER, and rather than `DELETE ... LIMIT`, which needs a
            # compile option CPython's bundled SQLite does not promise.
            return connection.execute(
                "DELETE FROM approvals WHERE scope=? AND choice_id IN ("
                "SELECT choice_id FROM approvals WHERE scope=? AND owner<>? "
                f"AND state NOT IN ({marks}) ORDER BY created_at, choice_id LIMIT ?)",
                (scope, scope, owner, *_LIVE_APPROVALS, take),
            ).rowcount

    # The accessors below stay thin: the CHECK constraints are the only rule they enforce.
    # Every policy decision belongs to the callback, which runs inside the writer reservation
    # and aborts the transaction by raising.

    @staticmethod
    def _get_settings(connection: sqlite3.Connection) -> SettingsRow | None:
        row = connection.execute(
            f"SELECT {','.join(_SETTINGS_FIELDS)} FROM settings WHERE scope='app'"
        ).fetchone()
        return dict(zip(_SETTINGS_FIELDS, row, strict=True)) if row else None

    def get_settings(self) -> SettingsRow | None:
        with self._connection() as connection:
            return self._get_settings(connection)

    def update_settings(
        self,
        command_id: str,
        changed: Callable[[SettingsRow | None, CommandReceipt | None], tuple[SettingsRow, str]],
    ) -> tuple[SettingsRow, CommandReceipt]:
        """Write one settings document and the receipt that proves this command applied it.

        The receipt for a replayed command_id is read before the callback and wins over a
        second write: the callback still runs (it may raise `command_conflict` on a mismatched
        hash), but a matching replay returns the already-committed row and receipt unchanged
        rather than rewriting the document with whatever the callback recomputed.
        """
        with self._connection(write=True) as connection:
            current = self._get_settings(connection)
            found = connection.execute(
                f"SELECT {','.join(_SETTING_COMMAND_FIELDS)} FROM setting_commands "
                "WHERE command_id=?",
                (command_id,),
            ).fetchone()
            existing: CommandReceipt | None = (
                dict(zip(_SETTING_COMMAND_FIELDS, found, strict=True)) if found else None
            )
            row, patch_hash = changed(current, existing)
            if existing is not None and current is not None:
                # An orphan receipt with no document (only possible on data from before
                # `delete_settings` cleared `setting_commands` too) falls through and reapplies.
                return current, existing
            receipt: CommandReceipt = {
                "command_id": command_id,
                "revision": row["revision"],
                "patch_hash": patch_hash,
                "applied_at": row["updated_at"],
            }
            connection.execute(
                "INSERT INTO settings (scope,version,revision,document,updated_at) "
                "VALUES ('app',?,?,?,?) ON CONFLICT(scope) DO UPDATE SET "
                "version=excluded.version,revision=excluded.revision,"
                "document=excluded.document,updated_at=excluded.updated_at",
                tuple(row[field] for field in _SETTINGS_FIELDS),
            )
            connection.execute(
                "INSERT INTO setting_commands (command_id,revision,patch_hash,applied_at) "
                "VALUES (?,?,?,?) ON CONFLICT(command_id) DO NOTHING",
                tuple(receipt[field] for field in _SETTING_COMMAND_FIELDS),
            )
            # Receipts are a replay guard, not a history: keep a bounded newest window, but
            # never the row just inserted — a tie in applied_at (or a caller clock stepping
            # back) must not let the fresh receipt be the one the prune deletes.
            connection.execute(
                "DELETE FROM setting_commands WHERE command_id <> ? AND command_id NOT IN "
                "(SELECT command_id FROM setting_commands WHERE command_id <> ? "
                "ORDER BY applied_at DESC, command_id DESC LIMIT ?)",
                (command_id, command_id, _SETTING_COMMAND_LIMIT - 1),
            )
        return row, receipt

    def delete_settings(self, guard: Callable[[SettingsRow | None], None]) -> None:
        with self._connection(write=True) as connection:
            guard(self._get_settings(connection))  # Raising here refuses before any write.
            connection.execute("DELETE FROM settings WHERE scope='app'")
            # Without this, a command_id from before the delete would replay against the
            # gone document and `update_settings` would hand back a receipt this store no
            # longer backs, instead of applying fresh.
            connection.execute("DELETE FROM setting_commands")

    @staticmethod
    def _get_legacy_binding(connection: sqlite3.Connection) -> LegacyBindingRow | None:
        row = connection.execute(
            f"SELECT {','.join(_LEGACY_BINDING_FIELDS)} FROM legacy_binding WHERE id=1"
        ).fetchone()
        return dict(zip(_LEGACY_BINDING_FIELDS, row, strict=True)) if row else None

    def get_legacy_binding(self) -> LegacyBindingRow | None:
        with self._connection() as connection:
            return self._get_legacy_binding(connection)

    def record_legacy_binding_once(
        self,
        result: str,
        recorded_at: str,
        *,
        workspace_id: str | None = None,
        code: str | None = None,
    ) -> LegacyBindingRow:
        """Record the one-time outcome of binding the legacy store; a recorded row wins."""
        with self._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO legacy_binding (id,result,workspace_id,code,recorded_at) "
                "VALUES (1,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (result, workspace_id, code, recorded_at),
            )
            row = self._get_legacy_binding(connection)
            if row is None:  # The insert is unconditional; no row means the store is not sane.
                raise StateError("corrupt")
        return row

    @staticmethod
    def _get_workspace(connection: sqlite3.Connection, workspace_id: str) -> WorkspaceRow | None:
        row = connection.execute(
            f"SELECT {','.join(_WORKSPACE_COLUMNS)} FROM workspaces WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        return dict(zip(_WORKSPACE_COLUMNS, row, strict=True)) if row else None

    @staticmethod
    def _list_workspaces(connection: sqlite3.Connection) -> list[WorkspaceRow]:
        rows = connection.execute(
            f"SELECT {','.join(_WORKSPACE_COLUMNS)} FROM workspaces ORDER BY workspace_id"
        ).fetchall()
        return [dict(zip(_WORKSPACE_COLUMNS, row, strict=True)) for row in rows]

    def list_workspaces(self) -> list[WorkspaceRow]:
        with self._connection() as connection:
            return self._list_workspaces(connection)

    def get_workspace(self, workspace_id: str) -> WorkspaceRow | None:
        with self._connection() as connection:
            return self._get_workspace(connection, workspace_id)

    def update_workspace(
        self,
        workspace_id: str,
        changed: Callable[[WorkspaceRow | None, list[WorkspaceRow]], WorkspaceRow | None],
    ) -> WorkspaceRow | None:
        """Insert, replace or delete one registration; the callback sees every committed row.

        Identity, canonical-key and overlap rules need the whole table, and they need it
        inside the same reservation as the write they decide.
        """
        with self._connection(write=True) as connection:
            current = self._get_workspace(connection, workspace_id)
            row = changed(current, self._list_workspaces(connection))
            if (row is None or row != current) and connection.execute(
                "SELECT 1 FROM vault_publications UNION ALL SELECT 1 FROM vault_operations LIMIT 1"
            ).fetchone():
                # A durable publication or operation freezes the registration safety boundary.
                # The save rechecks every protected relationship after reserving, so changes
                # committed before the reservation are caught and later changes are refused.
                raise StateError("recovery_required")
            if row is not None and row["workspace_id"] != workspace_id:
                # A row for a different id would insert or overwrite the wrong registration
                # under this argument's identity; refuse before either DELETE or INSERT runs.
                raise StateError("unavailable")
            if row is None:
                copies = connection.execute(
                    "SELECT worktree_id FROM bot_code_copies WHERE state='bound' AND "
                    "(workspace_id=? OR session_id IN (SELECT session_id "
                    "FROM session_profile_bindings WHERE workspace_id=?) OR thread_id IN "
                    "(SELECT thread_id FROM bot_threads WHERE workspace_id=?))",
                    (workspace_id, workspace_id, workspace_id),
                ).fetchall()
                self._release_copies(connection, [copy[0] for copy in copies])
                connection.execute(
                    "DELETE FROM bot_code_folders WHERE workspace_id=?", (workspace_id,)
                )
                connection.execute(
                    "DELETE FROM bot_code_baselines WHERE workspace_id=?", (workspace_id,)
                )
                connection.execute(
                    "DELETE FROM inbox_suggestions WHERE workspace_id=?", (workspace_id,)
                )
                connection.execute(
                    "DELETE FROM bot_thread_sessions WHERE thread_id IN "
                    "(SELECT thread_id FROM bot_threads WHERE workspace_id=?)",
                    (workspace_id,),
                )
                connection.execute("DELETE FROM bot_threads WHERE workspace_id=?", (workspace_id,))
                connection.execute("DELETE FROM workspaces WHERE workspace_id=?", (workspace_id,))
            else:
                assignments = ",".join(f"{c}=excluded.{c}" for c in _WORKSPACE_COLUMNS[1:])
                connection.execute(
                    f"INSERT INTO workspaces ({','.join(_WORKSPACE_COLUMNS)}) "
                    f"VALUES ({','.join('?' for _ in _WORKSPACE_COLUMNS)}) "
                    f"ON CONFLICT(workspace_id) DO UPDATE SET {assignments}",
                    tuple(row[column] for column in _WORKSPACE_COLUMNS),
                )
        return row

    def select_workspace(self, workspace_id: str | None) -> None:
        """Clear and set in one transaction, so the partial unique index never sees two.

        `WorkspaceService.select` already checks the id exists before calling this, so a miss
        here only means that row was removed in the gap between that check and this write. The
        select route has no error for this path, so a lost race restores the previous
        choice instead of leaving no workspace selected.
        """
        with self._connection(write=True) as connection:
            previous = connection.execute(
                "SELECT workspace_id FROM workspaces WHERE active=1"
            ).fetchone()
            connection.execute("UPDATE workspaces SET active=0 WHERE active=1")
            if workspace_id is not None:
                updated = connection.execute(
                    "UPDATE workspaces SET active=1 WHERE workspace_id=?", (workspace_id,)
                ).rowcount
                if updated != 1 and previous is not None:
                    connection.execute(
                        "UPDATE workspaces SET active=1 WHERE workspace_id=?", previous
                    )

    def delete_scope_rows(
        self, table: Literal["drafts", "commands", "approvals"], scope: str, owner: str
    ) -> tuple[int, int]:
        """Drop one workspace scope's rows from a scoped table; report deleted and kept.

        `owner` is the current launch's ledger owner and is always required, including for
        `drafts`, which has no owner column and ignores the value: one caller, one habit, and a
        caller that has no owner to give has no business deleting `commands` or `approvals`
        either. The length check below applies to every table for the same reason.

        What each table keeps differs, because what a row means differs:

        `commands` keeps only the turns this launch admitted and has not finished. Deleting one
        loses a message the user believes was sent: the browser already holds its receipt and has
        cleared the draft, so the `dispatching` write that follows finds nothing and the text
        exists nowhere. A dead launch's unfinished rows carry another owner and go with the rest.

        `approvals` keeps every row this launch wrote, whatever its state. Each native
        request gets exactly one recorded choice, ever, and a deleted terminal row of a request
        that is still pending would let a second choice be recorded — and delivered — under a new
        id. Only a previous launch's rows are dropped, and their native requests died with that
        launch's driver process.

        `drafts` has neither state nor owner and loses the whole scope.

        The keep test is part of the DELETE rather than a check before it, so no admission can land
        in a gap: `BEGIN IMMEDIATE` orders every acceptance and every recording either wholly
        before this write, where it is seen and kept, or wholly after it, where it is untouched.
        """
        if len(owner) != 36:
            # A malformed owner matches no row and would silently widen every DELETE back over the
            # rows the narrowing exists to keep. Refusing is the failure this must have.
            raise StateError("invalid_input")
        params: tuple[Any, ...]
        if table == "drafts":
            clause, params = "", (scope,)
        elif table == "commands":
            marks = ",".join("?" for _ in _LIVE_COMMANDS)
            clause = f" AND NOT (state IN ({marks}) AND owner=?)"
            params = (scope, *_LIVE_COMMANDS, owner)
        elif table == "approvals":
            clause, params = " AND owner<>?", (scope, owner)
        else:
            # The name is interpolated into SQL; a caller outside the annotation never reaches it.
            raise StateError("unavailable")
        with self._connection(write=True) as connection:
            present = connection.execute(
                f"SELECT count(*) FROM {table} WHERE scope=?", (scope,)
            ).fetchone()[0]
            affected_groups: list[str] = []
            if table == "commands":
                retained = "state IN (" + ",".join("?" for _ in _LIVE_COMMANDS) + ") AND owner=?"
                groups = connection.execute(
                    "SELECT DISTINCT r.thread_id,w.canonical_key FROM bot_thread_sessions r "
                    "JOIN bot_threads t ON t.thread_id=r.thread_id "
                    "JOIN workspaces w ON w.workspace_id=t.workspace_id "
                    "JOIN commands c ON c.session_id=r.session_id "
                    f"WHERE c.scope=?{clause} AND NOT EXISTS "
                    "(SELECT 1 FROM bot_thread_sessions member JOIN commands kept "
                    "ON kept.session_id=member.session_id WHERE member.thread_id=r.thread_id "
                    f"AND kept.scope=? AND {retained})",
                    (*params, scope, *_LIVE_COMMANDS, owner),
                ).fetchall()
                affected_groups = [
                    thread_id
                    for thread_id, canonical_key in groups
                    if hashlib.sha256(canonical_key.encode("utf-8")).hexdigest() == scope
                ]
                copies = connection.execute(
                    "SELECT copy.worktree_id,w.canonical_key FROM bot_code_copies copy "
                    "JOIN session_profile_bindings b ON b.session_id=copy.session_id "
                    "JOIN workspaces w ON w.workspace_id=b.workspace_id "
                    "WHERE copy.state='bound' AND copy.session_id IN "
                    f"(SELECT session_id FROM commands WHERE scope=?{clause}) AND NOT EXISTS "
                    "(SELECT 1 FROM commands WHERE session_id=copy.session_id "
                    f"AND scope=? AND {retained})",
                    (*params, scope, *_LIVE_COMMANDS, owner),
                ).fetchall()
                released = [
                    worktree_id
                    for worktree_id, canonical_key in copies
                    if hashlib.sha256(canonical_key.encode("utf-8")).hexdigest() == scope
                ]
                for thread_id in affected_groups:
                    released.extend(
                        row[0]
                        for row in connection.execute(
                            "SELECT worktree_id FROM bot_code_copies "
                            "WHERE state='bound' AND thread_id=?",
                            (thread_id,),
                        )
                    )
                self._release_copies(connection, released)
            # SQLite foreign keys are deliberately not the authority here: the migration and
            # reset paths must make their own owner selection visible and atomic.  Select the
            # exact rows this reset will remove before deleting either rows or references.
            if table in {"drafts", "commands"}:
                owner_column = "key" if table == "drafts" else "command_id"
                owners = [
                    row[0]
                    for row in connection.execute(
                        f"SELECT {owner_column} FROM {table} WHERE scope=?{clause}", params
                    ).fetchall()
                ]
                connection.executemany(
                    "DELETE FROM asset_refs WHERE scope=? AND owner_kind=? AND owner_id=?",
                    [(scope, table[:-1], row) for row in owners],
                )
            deleted = connection.execute(
                f"DELETE FROM {table} WHERE scope=?{clause}", params
            ).rowcount
            if table == "commands":
                # A group is only references. Drop groups whose source sends were reset;
                # leave the provider's histories and any retained in-flight sends alone.
                for thread_id in affected_groups:
                    connection.execute(
                        "DELETE FROM bot_thread_sessions WHERE thread_id=?", (thread_id,)
                    )
                    connection.execute("DELETE FROM bot_threads WHERE thread_id=?", (thread_id,))
        return deleted, present - deleted

    @staticmethod
    def _release_copies(connection: sqlite3.Connection, worktree_ids: Sequence[str]) -> None:
        # Select owners before deleting their membership; this update shares that transaction.
        connection.executemany(
            "UPDATE bot_code_copies SET state='released' WHERE worktree_id=? AND state='bound'",
            [(worktree_id,) for worktree_id in worktree_ids],
        )

    def list_vault_proposals(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_VAULT_PROPOSAL_FIELDS)} FROM vault_proposals "
                "WHERE workspace_id=? ORDER BY ref",
                (workspace_id,),
            ).fetchall()
        return [dict(zip(_VAULT_PROPOSAL_FIELDS, row, strict=True)) for row in rows]

    def read_vault_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_VAULT_PROPOSAL_FIELDS)} FROM vault_proposals "
                "WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        return dict(zip(_VAULT_PROPOSAL_FIELDS, row, strict=True)) if row else None

    def write_vault_proposal(self, row: dict[str, Any]) -> None:
        """Insert or replace one sealed proposal; a second open proposal for one ref is refused.

        The UNIQUE (workspace_id, ref) refuses it too, as `invalid_input`. Reading the holder
        inside the same write transaction names the collision for what it is instead: another
        proposal already holds that ref, and replacing it is the service's decision, not a
        constraint failure's.
        """
        if set(row) != set(_VAULT_PROPOSAL_FIELDS) or not isinstance(row.get("body"), bytes):
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            held = connection.execute(
                "SELECT proposal_id FROM vault_proposals WHERE workspace_id=? AND ref=?",
                (row["workspace_id"], row["ref"]),
            ).fetchone()
            if held is not None and held[0] != row["proposal_id"]:
                raise StateError("conflict")
            connection.execute(
                f"INSERT OR REPLACE INTO vault_proposals ({','.join(_VAULT_PROPOSAL_FIELDS)}) "
                f"VALUES ({','.join('?' * len(_VAULT_PROPOSAL_FIELDS))})",
                tuple(row[field] for field in _VAULT_PROPOSAL_FIELDS),
            )

    def mark_vault_proposal_handed(self, proposal_id: str, stamp: str) -> bool:
        """Stamp one proposal as handed, answering False when no row carries that id.

        An `UPDATE` rather than a replacement of a row read earlier, so a withdraw that commits
        between that read and this call leaves the proposal deleted instead of re-inserting it.
        """
        with self._connection(write=True) as connection:
            result = connection.execute(
                "UPDATE vault_proposals SET handed_at=? WHERE proposal_id=?", (stamp, proposal_id)
            )
        return result.rowcount == 1

    def delete_vault_proposal(self, proposal_id: str) -> bool:
        with self._connection(write=True) as connection:
            result = connection.execute(
                "DELETE FROM vault_proposals WHERE proposal_id=?", (proposal_id,)
            )
        return result.rowcount == 1

    def put_session_root(self, row: SessionRootRow) -> bool:
        """Record a session's root once; answer whether this call inserted it.

        The primary root and additional-folder snapshots are pinned together. An equal
        selection is a no-op answering False and a different one is `conflict`. The read and the
        insert share one write transaction, so two callers racing for one session cannot both
        see no row.
        """
        if set(row) not in (set(_SESSION_ROOT_FIELDS), set(_SESSION_ROOT_COLUMNS)):
            raise StateError("invalid_input")
        additional = self._additional_roots_json(
            row.get("additional_roots", []), row["workspace_id"], code="invalid_input"
        )
        with self._connection(write=True) as connection:
            held = connection.execute(
                f"SELECT {','.join(_SESSION_ROOT_COLUMNS)} FROM session_roots WHERE session_id=?",
                (row["session_id"],),
            ).fetchone()
            if held is not None:
                pinned = self._session_root_row(held)
                if any(
                    pinned[field] != row[field]
                    for field in ("workspace_id", "worktree_id", "display_root")
                ) or pinned.get("additional_roots", []) != json.loads(additional):
                    raise StateError("conflict")
                return False
            connection.execute(
                f"INSERT INTO session_roots ({','.join(_SESSION_ROOT_COLUMNS)}) "
                f"VALUES ({','.join('?' * len(_SESSION_ROOT_COLUMNS))})",
                (*(row[field] for field in _SESSION_ROOT_FIELDS), additional),
            )
        return True

    @staticmethod
    def _additional_roots_json(roots: Any, workspace_id: str, *, code: str) -> str:
        if not isinstance(roots, list) or len(roots) > 7:
            raise StateError(code)
        seen = {workspace_id}
        for root in roots:
            if not isinstance(root, dict) or set(root) != {
                "workspace_id",
                "display_root",
                "physical_id",
            }:
                raise StateError(code)
            for field, lower, upper in (
                ("workspace_id", 36, 36),
                ("display_root", 3, 4096),
                ("physical_id", 3, 80),
            ):
                value = root[field]
                if (
                    not isinstance(value, str)
                    or not lower <= len(value) <= upper
                    or "\x00" in value
                ):
                    raise StateError(code)
            extra_id = root["workspace_id"]
            try:
                if str(uuid.UUID(extra_id)) != extra_id or extra_id in seen:
                    raise StateError(code)
            except ValueError:
                raise StateError(code) from None
            seen.add(extra_id)
        text = json.dumps(roots, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if len(text.encode("ascii")) > 32768:
            raise StateError(code)
        return text

    @classmethod
    def _session_root_row(cls, values: Sequence[Any]) -> SessionRootRow:
        row: SessionRootRow = dict(zip(_SESSION_ROOT_COLUMNS, values, strict=True))
        raw = row.pop("additional_roots")
        if (
            not isinstance(raw, str)
            or not raw.isascii()
            or not 2 <= len(raw.encode("ascii")) <= 32768
        ):
            raise StateError("corrupt")
        try:
            additional = json.loads(raw)
        except ValueError, RecursionError:
            raise StateError("corrupt") from None
        cls._additional_roots_json(additional, row["workspace_id"], code="corrupt")
        if additional:
            row["additional_roots"] = additional
        return row

    def get_session_root(self, session_id: str) -> SessionRootRow | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_SESSION_ROOT_COLUMNS)} FROM session_roots WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return self._session_root_row(row) if row else None

    def list_session_roots(self, limit: int) -> tuple[list[SessionRootRow], bool]:
        """Newest roots first, ties by id; the bool says rows past `limit` exist."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_SESSION_ROOT_COLUMNS)} FROM session_roots "
                "ORDER BY created_at DESC,session_id LIMIT ?",
                (limit + 1,),
            ).fetchall()
        return [self._session_root_row(row) for row in rows[:limit]], len(rows) > limit

    def session_root_paths(self, session_ids: list[str]) -> dict[str, str]:
        """Each of `session_ids` with a recorded root, to its `display_root`. One short read
        statement per ROOT_LOOKUP_CHUNK ids, each on its own connection, so a long id list never
        holds the store's read lock for long."""
        found: dict[str, str] = {}
        unique = sorted(set(session_ids))
        for start in range(0, len(unique), ROOT_LOOKUP_CHUNK):
            chunk = unique[start : start + ROOT_LOOKUP_CHUNK]
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT session_id,display_root FROM session_roots "
                    f"WHERE session_id IN ({','.join('?' * len(chunk))})",
                    chunk,
                ).fetchall()
            found.update((row[0], row[1]) for row in rows)
        return found

    def session_roots_in_worktree(self, worktree_id: str) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT session_id FROM session_roots WHERE worktree_id=? ORDER BY session_id",
                (worktree_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def session_roots_in_folder(self, workspace_id: str) -> list[str]:
        """Sessions using the folder as their primary cwd or an additional folder, by id."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_SESSION_ROOT_COLUMNS)} FROM session_roots "
                "WHERE (workspace_id=? AND worktree_id IS NULL) OR additional_roots<>'[]' "
                "ORDER BY session_id",
                (workspace_id,),
            )
            matching = []
            for values in rows:
                row = self._session_root_row(values)
                if (row["workspace_id"] == workspace_id and row["worktree_id"] is None) or any(
                    root["workspace_id"] == workspace_id for root in row.get("additional_roots", [])
                ):
                    matching.append(row["session_id"])
        return matching

    def bot_code_folder(self, bot_id: str) -> BotCodeFolderRow | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_BOT_CODE_FOLDER_FIELDS)} FROM bot_code_folders WHERE bot_id=?",
                (bot_id,),
            ).fetchone()
        return (
            cast(BotCodeFolderRow, dict(zip(_BOT_CODE_FOLDER_FIELDS, row, strict=True)))
            if row
            else None
        )

    def insert_bot_copy(
        self, worktree: WorktreeRow, binding: BotCodeCopyRow, *, limit: int
    ) -> None:
        """Reserve a creating worktree and its bound owner in one shared-cap transaction. The
        Bot must still exist with this code folder as its grant, read in the same transaction,
        so a Bot deleted or regranted since the caller's read is `conflict` and leaves no copy
        that no release reaches."""
        if (
            set(worktree) != set(_WORKTREE_FIELDS)
            or worktree["state"] != "creating"
            or set(binding) != set(_BOT_CODE_COPY_FIELDS)
            or binding["state"] != "bound"
            or binding["worktree_id"] != worktree["worktree_id"]
            or binding["workspace_id"] != worktree["workspace_id"]
        ):
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            held = connection.execute(
                "SELECT 1 FROM bot_code_copies WHERE worktree_id=? OR "
                "(state='bound' AND (session_id=? OR (thread_id=? AND workspace_id=?)))",
                (
                    binding["worktree_id"],
                    binding["session_id"],
                    binding["thread_id"],
                    binding["workspace_id"],
                ),
            ).fetchone()
            granted = connection.execute(
                "SELECT 1 FROM bots b JOIN bot_code_folders f ON f.bot_id=b.bot_id "
                "WHERE b.bot_id=? AND f.workspace_id=?",
                (binding["bot_id"], binding["workspace_id"]),
            ).fetchone()
            if held or not granted:
                raise StateError("conflict")
            (count,) = connection.execute(
                "SELECT count(*) FROM worktrees WHERE state<>'removed'"
            ).fetchone()
            if count >= limit:
                raise StateError("capacity")
            connection.execute(
                f"INSERT INTO worktrees ({','.join(_WORKTREE_FIELDS)}) "
                f"VALUES ({','.join('?' * len(_WORKTREE_FIELDS))})",
                tuple(worktree[field] for field in _WORKTREE_FIELDS),
            )
            connection.execute(
                f"INSERT INTO bot_code_copies ({','.join(_BOT_CODE_COPY_FIELDS)}) "
                f"VALUES ({','.join('?' * len(_BOT_CODE_COPY_FIELDS))})",
                tuple(binding[field] for field in _BOT_CODE_COPY_FIELDS),
            )

    def bound_copy(self, session_id: str) -> BotCodeCopyRow | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_BOT_CODE_COPY_FIELDS)} FROM bot_code_copies "
                "WHERE state='bound' AND session_id=?",
                (session_id,),
            ).fetchone()
        return (
            cast(BotCodeCopyRow, dict(zip(_BOT_CODE_COPY_FIELDS, row, strict=True)))
            if row
            else None
        )

    def bound_thread_copy(self, thread_id: str, workspace_id: str) -> BotCodeCopyRow | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_BOT_CODE_COPY_FIELDS)} FROM bot_code_copies "
                "WHERE state='bound' AND thread_id=? AND workspace_id=?",
                (thread_id, workspace_id),
            ).fetchone()
        return (
            cast(BotCodeCopyRow, dict(zip(_BOT_CODE_COPY_FIELDS, row, strict=True)))
            if row
            else None
        )

    def copy_states(self, worktree_ids: Sequence[str]) -> dict[str, BotCodeCopyRow]:
        if not worktree_ids:
            return {}
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_BOT_CODE_COPY_FIELDS)} FROM bot_code_copies "
                f"WHERE worktree_id IN ({','.join('?' * len(worktree_ids))})",
                tuple(worktree_ids),
            ).fetchall()
        return {
            row[0]: cast(BotCodeCopyRow, dict(zip(_BOT_CODE_COPY_FIELDS, row, strict=True)))
            for row in rows
        }

    def released_copies(self) -> list[BotCodeCopyRow]:
        """Every pending release, including old copies behind removed worktree history."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_BOT_CODE_COPY_FIELDS)} FROM bot_code_copies "
                "WHERE state='released' ORDER BY created_at,worktree_id"
            ).fetchall()
        return [
            cast(BotCodeCopyRow, dict(zip(_BOT_CODE_COPY_FIELDS, row, strict=True))) for row in rows
        ]

    def finish_release(self, worktree_id: str) -> bool:
        """Finish a pending release after its terminal worktree handling succeeds. The binding
        stays, `former`, for as long as the worktree row does: no release settles it again,
        and the tree an archive restore brings back is still a Bot copy to Git."""
        with self._connection(write=True) as connection:
            return (
                connection.execute(
                    "UPDATE bot_code_copies SET state='former' "
                    "WHERE worktree_id=? AND state='released'",
                    (worktree_id,),
                ).rowcount
                == 1
            )

    def bot_code_baseline(self, workspace_id: str) -> BotCodeBaselineRow | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_BOT_CODE_BASELINE_FIELDS)} FROM bot_code_baselines "
                "WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        return (
            cast(BotCodeBaselineRow, dict(zip(_BOT_CODE_BASELINE_FIELDS, row, strict=True)))
            if row
            else None
        )

    def insert_bot_code_baseline(self, row: BotCodeBaselineRow) -> bool:
        """Keep the first baseline; concurrent creators cannot replace its fingerprint."""
        if set(row) != set(_BOT_CODE_BASELINE_FIELDS):
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            return (
                connection.execute(
                    f"INSERT INTO bot_code_baselines ({','.join(_BOT_CODE_BASELINE_FIELDS)}) "
                    "VALUES (?,?,?) ON CONFLICT(workspace_id) DO NOTHING",
                    tuple(row[field] for field in _BOT_CODE_BASELINE_FIELDS),
                ).rowcount
                == 1
            )

    def replace_bot_code_baseline(
        self, workspace_id: str, expected_fingerprint: str, fingerprint: str, created_at: str
    ) -> bool:
        """Replace only the baseline reviewed by the caller; an absent baseline stays absent."""
        with self._connection(write=True) as connection:
            return (
                connection.execute(
                    "UPDATE bot_code_baselines SET git_fingerprint=?,created_at=? "
                    "WHERE workspace_id=? AND git_fingerprint=?",
                    (fingerprint, created_at, workspace_id, expected_fingerprint),
                ).rowcount
                == 1
            )

    def sessions_using_worktree(self, worktree_id: str) -> list[str]:
        """Primary roots, bound sessions and every source session of a bound Bot thread."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT session_id FROM session_roots WHERE worktree_id=? "
                "UNION SELECT session_id FROM bot_code_copies "
                "WHERE worktree_id=? AND state='bound' AND session_id IS NOT NULL "
                "UNION SELECT member.session_id FROM bot_thread_sessions member "
                "JOIN bot_code_copies copy ON copy.thread_id=member.thread_id "
                "WHERE copy.worktree_id=? AND copy.state='bound' ORDER BY session_id",
                (worktree_id, worktree_id, worktree_id),
            ).fetchall()
        return [row[0] for row in rows]

    def worktree_by_branch(self, workspace_id: str, branch: str) -> WorktreeRow | None:
        """Resolve one current branch mapping, refusing an ambiguous active mapping."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_WORKTREE_FIELDS)} FROM worktrees "
                "WHERE workspace_id=? AND branch=? AND state<>'removed' LIMIT 2",
                (workspace_id, branch),
            ).fetchall()
        if len(rows) > 1:
            raise StateError("conflict")
        return dict(zip(_WORKTREE_FIELDS, rows[0], strict=True)) if rows else None

    def worktrees_in_states(self, states: Sequence[str]) -> list[WorktreeRow]:
        """Select lifecycle rows by state without a newest-history window."""
        if not states:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_WORKTREE_FIELDS)} FROM worktrees "
                f"WHERE state IN ({','.join('?' * len(states))}) ORDER BY created_at,worktree_id",
                tuple(states),
            ).fetchall()
        return [dict(zip(_WORKTREE_FIELDS, row, strict=True)) for row in rows]

    def insert_worktree(self, row: WorktreeRow, *, limit: int) -> None:
        """Record a worktree about to be created, bounded by the rows not yet `removed`.

        The count and the insert share one write transaction, so two creations racing for the
        last slot admit exactly one.
        """
        if set(row) != set(_WORKTREE_FIELDS) or row["state"] != "creating":
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            (count,) = connection.execute(
                "SELECT count(*) FROM worktrees WHERE state<>'removed'"
            ).fetchone()
            if count >= limit:
                raise StateError("capacity")
            connection.execute(
                f"INSERT INTO worktrees ({','.join(_WORKTREE_FIELDS)}) "
                f"VALUES ({','.join('?' * len(_WORKTREE_FIELDS))})",
                tuple(row[field] for field in _WORKTREE_FIELDS),
            )

    def get_worktree(self, worktree_id: str) -> WorktreeRow | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_WORKTREE_FIELDS)} FROM worktrees WHERE worktree_id=?",
                (worktree_id,),
            ).fetchone()
        return dict(zip(_WORKTREE_FIELDS, row, strict=True)) if row else None

    def list_worktrees(self, limit: int) -> tuple[list[WorktreeRow], bool]:
        """Newest worktrees first, ties by id; the bool says rows past `limit` exist."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_WORKTREE_FIELDS)} FROM worktrees WHERE state<>'removed' "
                "ORDER BY created_at DESC,worktree_id LIMIT ?",
                (limit + 1,),
            ).fetchall()
        return [dict(zip(_WORKTREE_FIELDS, row, strict=True)) for row in rows[:limit]], len(
            rows
        ) > limit

    def update_worktree_state(
        self, worktree_id: str, expected: str, state: str, stamp: str
    ) -> None:
        """Move one worktree from `expected` to `state`, as a compare-and-set.

        The state test is part of the UPDATE, so of two changes racing from one state exactly
        one lands; the loser is `conflict`, and an absent id is `not_found`. A removal that
        finishes (`removing` to `removed`) drops, in the same transaction, the `bound` binding of
        a Bot copy no session started in (`_forget_unstarted_copy`).
        """
        with self._connection(write=True) as connection:
            changed = connection.execute(
                "UPDATE worktrees SET state=?,updated_at=? WHERE worktree_id=? AND state=?",
                (state, stamp, worktree_id, expected),
            ).rowcount
            if changed != 1:
                raise self._worktree_miss(connection, worktree_id)
            if (expected, state) == ("removing", "removed"):
                self._forget_unstarted_copy(connection, worktree_id)

    @staticmethod
    def _forget_unstarted_copy(connection: sqlite3.Connection, worktree_id: str) -> None:
        """Drop the `bound` binding of a removed copy whose chat, or every member of whose Bot
        thread, never started natively (`native_started`): no Claude Code ran with it, so the
        owner's next first send makes a new copy. A started owner keeps its binding and
        refuses rather than silently work in another copy."""
        connection.execute(
            "DELETE FROM bot_code_copies WHERE worktree_id=? AND state='bound' AND ("
            "session_id IN (SELECT session_id FROM session_profile_bindings "
            "WHERE native_started=0) OR (thread_id IS NOT NULL AND NOT EXISTS("
            "SELECT 1 FROM bot_thread_sessions m JOIN session_profile_bindings p "
            "ON p.session_id=m.session_id "
            "WHERE m.thread_id=bot_code_copies.thread_id AND p.native_started=1)))",
            (worktree_id,),
        )

    def revive_worktree(self, worktree_id: str, stamp: str, *, limit: int) -> None:
        """Move one `removed` worktree back to `creating` for a restore, bounded as
        `insert_worktree` is by the rows not `removed`.

        The count and the compare-and-set share one write transaction, so a restore racing a
        creation or another restore for the last slot admits exactly one. A row in any other
        state is `conflict`, an absent id `not_found`.
        """
        with self._connection(write=True) as connection:
            (count,) = connection.execute(
                "SELECT count(*) FROM worktrees WHERE state<>'removed'"
            ).fetchone()
            if count >= limit:
                raise StateError("capacity")
            changed = connection.execute(
                "UPDATE worktrees SET state='creating',updated_at=? "
                "WHERE worktree_id=? AND state='removed'",
                (stamp, worktree_id),
            ).rowcount
            if changed != 1:
                raise self._worktree_miss(connection, worktree_id)

    def delete_worktree(self, worktree_id: str, *, expected_state: str) -> None:
        """Drop one worktree row only while it is still in `expected_state`. A `creating` row
        was never `ready`, so no session ran in it: its `bound` Bot-copy binding goes in the
        same transaction, and its chat's or Bot thread's next first send makes a new copy."""
        with self._connection(write=True) as connection:
            changed = connection.execute(
                "DELETE FROM worktrees WHERE worktree_id=? AND state=?",
                (worktree_id, expected_state),
            ).rowcount
            if changed != 1:
                raise self._worktree_miss(connection, worktree_id)
            if expected_state == "creating":
                connection.execute(
                    "DELETE FROM bot_code_copies WHERE worktree_id=? AND state='bound'",
                    (worktree_id,),
                )

    @staticmethod
    def _worktree_miss(connection: sqlite3.Connection, worktree_id: str) -> StateError:
        # Read inside the same transaction as the statement that missed, so the answer is
        # about the row that statement saw.
        present = connection.execute(
            "SELECT 1 FROM worktrees WHERE worktree_id=?", (worktree_id,)
        ).fetchone()
        return StateError("conflict" if present else "not_found")

    def insert_check(self, row: CheckRow, *, limit: int) -> None:
        """Record one check, bounded per folder.

        The count and the insert share one write transaction, so two additions racing for the
        last place admit exactly one.
        """
        if set(row) != set(_CHECK_FIELDS):
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            (count,) = connection.execute(
                "SELECT count(*) FROM code_checks WHERE workspace_id=?", (row["workspace_id"],)
            ).fetchone()
            if count >= limit:
                raise StateError("capacity")
            connection.execute(
                f"INSERT INTO code_checks ({','.join(_CHECK_FIELDS)}) "
                f"VALUES ({','.join('?' * len(_CHECK_FIELDS))})",
                tuple(row[field] for field in _CHECK_FIELDS),
            )

    def get_check(self, check_id: str) -> CheckRow | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_CHECK_FIELDS)} FROM code_checks WHERE check_id=?",
                (check_id,),
            ).fetchone()
        return cast(CheckRow, dict(zip(_CHECK_FIELDS, row, strict=True))) if row else None

    def list_checks(self, workspace_id: str) -> list[CheckRow]:
        """The folder's checks, oldest first, ties by id."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_CHECK_FIELDS)} FROM code_checks WHERE workspace_id=? "
                "ORDER BY created_at,check_id",
                (workspace_id,),
            ).fetchall()
        return [cast(CheckRow, dict(zip(_CHECK_FIELDS, row, strict=True))) for row in rows]

    def delete_check(self, check_id: str) -> bool:
        """Drop one check and its runs in one transaction; False when there was no check."""
        with self._connection(write=True) as connection:
            connection.execute("DELETE FROM check_runs WHERE check_id=?", (check_id,))
            return (
                connection.execute("DELETE FROM code_checks WHERE check_id=?", (check_id,)).rowcount
                == 1
            )

    def insert_check_run(self, row: CheckRunRow, *, keep: int) -> None:
        """Record one run of an existing check, then trim that check's finished runs to `keep`.

        The check test, the insert and the trim share one write transaction, so a run cannot
        land after its check's delete. A `running` row is never trimmed.
        """
        if set(row) != set(_CHECK_RUN_FIELDS):
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            if not connection.execute(
                "SELECT 1 FROM code_checks WHERE check_id=?", (row["check_id"],)
            ).fetchone():
                raise StateError("not_found")
            connection.execute(
                f"INSERT INTO check_runs ({','.join(_CHECK_RUN_FIELDS)}) "
                f"VALUES ({','.join('?' * len(_CHECK_RUN_FIELDS))})",
                tuple(row[field] for field in _CHECK_RUN_FIELDS),
            )
            connection.execute(
                "DELETE FROM check_runs WHERE check_id=? AND outcome<>'running' "
                "AND run_id NOT IN (SELECT run_id FROM check_runs "
                "WHERE check_id=? AND outcome<>'running' "
                "ORDER BY started_at DESC,run_id DESC LIMIT ?)",
                (row["check_id"], row["check_id"], keep),
            )

    def finish_check_run(
        self,
        run_id: str,
        *,
        outcome: str,
        exit_code: int | None,
        duration_ms: int | None,
        output_bytes: int | None,
        tail: bytes | None,
        ended_at: str,
        keep: int,
    ) -> bool:
        """Settle one run as a compare-and-set from `running`; True when this call settled it.

        A settle trims that run's check's finished runs to `keep` in the same write transaction,
        ordered as `insert_check_run` orders them; a compare-and-set that fails deletes nothing.
        """
        with self._connection(write=True) as connection:
            if (
                connection.execute(
                    "UPDATE check_runs SET outcome=?,exit_code=?,duration_ms=?,output_bytes=?,"
                    "tail=?,ended_at=? WHERE run_id=? AND outcome='running'",
                    (outcome, exit_code, duration_ms, output_bytes, tail, ended_at, run_id),
                ).rowcount
                != 1
            ):
                return False
            (check_id,) = connection.execute(
                "SELECT check_id FROM check_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            connection.execute(
                "DELETE FROM check_runs WHERE check_id=? AND outcome<>'running' "
                "AND run_id NOT IN (SELECT run_id FROM check_runs "
                "WHERE check_id=? AND outcome<>'running' "
                "ORDER BY started_at DESC,run_id DESC LIMIT ?)",
                (check_id, check_id, keep),
            )
            return True

    def get_check_run(self, run_id: str) -> CheckRunRow | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_CHECK_RUN_FIELDS)} FROM check_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return cast(CheckRunRow, dict(zip(_CHECK_RUN_FIELDS, row, strict=True))) if row else None

    def check_runs_of(self, check_id: str, limit: int) -> list[CheckRunRow]:
        """One check's runs, newest first, ties by id descending."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_CHECK_RUN_FIELDS)} FROM check_runs WHERE check_id=? "
                "ORDER BY started_at DESC,run_id DESC LIMIT ?",
                (check_id, limit),
            ).fetchall()
        return [cast(CheckRunRow, dict(zip(_CHECK_RUN_FIELDS, row, strict=True))) for row in rows]

    def last_check_runs(self, workspace_id: str, worktree_id: str | None) -> dict[str, CheckRunRow]:
        """Per check of the folder, its newest run in that checkout (`None`: the folder itself)."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_CHECK_RUN_FIELDS)} FROM check_runs WHERE worktree_id IS ? "
                "AND check_id IN (SELECT check_id FROM code_checks WHERE workspace_id=?) "
                "ORDER BY started_at,run_id",
                (worktree_id, workspace_id),
            ).fetchall()
        # Oldest first, so each check's newest run is the one left in the dict.
        latest: dict[str, CheckRunRow] = {}
        for row in rows:
            run = cast(CheckRunRow, dict(zip(_CHECK_RUN_FIELDS, row, strict=True)))
            latest[run["check_id"]] = run
        return latest

    def check_run_states(self, workspace_id: str) -> list[CheckRunState]:
        """Every run of the folder's checks, in every checkout, newest first as `check_runs_of`
        orders them, without the output columns."""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_CHECK_RUN_STATE_FIELDS)} FROM check_runs "
                "WHERE check_id IN (SELECT check_id FROM code_checks WHERE workspace_id=?) "
                "ORDER BY started_at DESC,run_id DESC",
                (workspace_id,),
            ).fetchall()
        return [
            cast(CheckRunState, dict(zip(_CHECK_RUN_STATE_FIELDS, row, strict=True)))
            for row in rows
        ]

    def count_running_check_runs(self, run_ids: Sequence[str]) -> int:
        """How many of the runs named are still `running`, in one query; an id with no row
        counts none, and no id reads nothing."""
        if not run_ids:
            return 0
        with self._connection() as connection:
            (count,) = connection.execute(
                "SELECT count(*) FROM check_runs WHERE outcome='running' "
                f"AND run_id IN ({','.join('?' * len(run_ids))})",
                tuple(run_ids),
            ).fetchone()
        return count

    def interrupt_check_runs(self, ended_at: str, *, keep: int) -> int:
        """End every `running` row as `interrupted` with no result; the count of rows ended.

        The same write transaction then trims every check's finished runs to `keep`, ordered as
        `insert_check_run` orders them.
        """
        with self._connection(write=True) as connection:
            ended = connection.execute(
                "UPDATE check_runs SET outcome='interrupted',exit_code=NULL,duration_ms=NULL,"
                "output_bytes=NULL,tail=NULL,ended_at=? WHERE outcome='running'",
                (ended_at,),
            ).rowcount
            connection.execute(
                "DELETE FROM check_runs WHERE run_id IN (SELECT run_id FROM (SELECT run_id,"
                "ROW_NUMBER() OVER (PARTITION BY check_id ORDER BY started_at DESC,run_id DESC) "
                "AS place FROM check_runs WHERE outcome<>'running') WHERE place>?)",
                (keep,),
            )
            return ended

    def list_skill_proposals(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {','.join(_SKILL_PROPOSAL_FIELDS)} FROM skill_proposals "
                "WHERE workspace_id=? ORDER BY name",
                (workspace_id,),
            ).fetchall()
        return [dict(zip(_SKILL_PROPOSAL_FIELDS, row, strict=True)) for row in rows]

    def read_skill_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {','.join(_SKILL_PROPOSAL_FIELDS)} FROM skill_proposals "
                "WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        return dict(zip(_SKILL_PROPOSAL_FIELDS, row, strict=True)) if row else None

    def insert_skill_proposal(self, row: dict[str, Any], *, limit: int) -> None:
        """Insert one proposal, bounded per workspace.

        `proposal_id` and `(workspace_id, name)` are each UNIQUE in the table, so either one
        already held would otherwise surface as `invalid_input`; reading them first inside the
        same write transaction names the collision for what it is instead: `conflict`.
        """
        if set(row) != set(_SKILL_PROPOSAL_FIELDS) or not isinstance(row.get("body"), bytes):
            raise StateError("invalid_input")
        with self._connection(write=True) as connection:
            held = connection.execute(
                "SELECT 1 FROM skill_proposals WHERE proposal_id=? OR (workspace_id=? AND name=?)",
                (row["proposal_id"], row["workspace_id"], row["name"]),
            ).fetchone()
            if held is not None:
                raise StateError("conflict")
            (count,) = connection.execute(
                "SELECT count(*) FROM skill_proposals WHERE workspace_id=?",
                (row["workspace_id"],),
            ).fetchone()
            if count >= limit:
                raise StateError("capacity")
            connection.execute(
                f"INSERT INTO skill_proposals ({','.join(_SKILL_PROPOSAL_FIELDS)}) "
                f"VALUES ({','.join('?' * len(_SKILL_PROPOSAL_FIELDS))})",
                tuple(row[field] for field in _SKILL_PROPOSAL_FIELDS),
            )

    def update_skill_proposal(
        self, proposal_id: str, expected_sha256: str, changes: dict[str, Any]
    ) -> bool:
        """Apply a compare-and-set update; True iff exactly one row matched and changed.

        A moved revision or a missing id is not an error: both answer False, and neither
        touches the store. A CHECK refusal (`invalid_input`) still leaves the store untouched,
        the write transaction never reaching `COMMIT`.
        """
        if (
            not changes
            or not set(changes) <= _SKILL_PROPOSAL_CHANGEABLE
            or "updated_at" not in changes
        ):
            raise StateError("invalid_input")
        columns = tuple(changes)
        with self._connection(write=True) as connection:
            result = connection.execute(
                f"UPDATE skill_proposals SET {','.join(f'{column}=?' for column in columns)} "
                "WHERE proposal_id=? AND proposed_sha256=?",
                (*(changes[column] for column in columns), proposal_id, expected_sha256),
            )
        return result.rowcount == 1

    def delete_skill_proposal(self, proposal_id: str, expected_sha256: str) -> bool:
        """Delete as the same compare-and-set as `update_skill_proposal`."""
        with self._connection(write=True) as connection:
            result = connection.execute(
                "DELETE FROM skill_proposals WHERE proposal_id=? AND proposed_sha256=?",
                (proposal_id, expected_sha256),
            )
        return result.rowcount == 1
