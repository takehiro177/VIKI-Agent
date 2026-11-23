"""SQLite table definitions for app-owned feature state."""

PROFILE_SCHEMAS = {
    "profiles": """CREATE TABLE profiles (
    profile_id TEXT PRIMARY KEY NOT NULL CHECK(length(profile_id) = 36),
    current_revision INTEGER NOT NULL CHECK(current_revision BETWEEN 1 AND 9007199254740991),
    state_revision INTEGER NOT NULL CHECK(state_revision BETWEEN 1 AND 9007199254740991),
    archived INTEGER NOT NULL CHECK(archived IN (0,1)),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24)
) STRICT""",
    "profile_revisions": """CREATE TABLE profile_revisions (
    profile_id TEXT NOT NULL CHECK(length(profile_id) = 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    format INTEGER NOT NULL CHECK(format BETWEEN 1 AND 2147483647),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 327680),
    body_bytes INTEGER NOT NULL CHECK(body_bytes BETWEEN 2 AND 262144),
    body_sha256 TEXT NOT NULL CHECK(length(body_sha256) = 64
        AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    PRIMARY KEY (profile_id, revision),
    FOREIGN KEY (profile_id) REFERENCES profiles(profile_id)
) STRICT""",
    "profile_commands": """CREATE TABLE profile_commands (
    command_id TEXT PRIMARY KEY NOT NULL CHECK(length(command_id) = 36),
    operation TEXT NOT NULL CHECK(operation IN ('create','revise','duplicate','archive')),
    target_profile_id TEXT NOT NULL CHECK(length(target_profile_id) = 36),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*'),
    result_revision INTEGER CHECK(result_revision IS NULL OR result_revision >= 1),
    result_state_revision INTEGER CHECK(result_state_revision IS NULL
        OR result_state_revision >= 1),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24)
) STRICT""",
    "provider_catalogs": """CREATE TABLE provider_catalogs (
    catalog_key TEXT PRIMARY KEY NOT NULL CHECK(length(catalog_key) = 64
        AND catalog_key NOT GLOB '*[^0-9a-f]*'),
    provider TEXT NOT NULL CHECK(length(provider) BETWEEN 1 AND 32),
    adapter_version TEXT NOT NULL CHECK(length(adapter_version) BETWEEN 1 AND 64),
    native_version TEXT NOT NULL CHECK(length(native_version) BETWEEN 1 AND 64),
    format INTEGER NOT NULL CHECK(format = 1),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 2 AND 327680),
    body_bytes INTEGER NOT NULL CHECK(body_bytes BETWEEN 2 AND 262144),
    body_sha256 TEXT NOT NULL CHECK(length(body_sha256) = 64
        AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
    observed_at TEXT NOT NULL CHECK(length(observed_at) = 24)
) STRICT""",
    "session_profile_bindings": """CREATE TABLE session_profile_bindings (
    session_id TEXT PRIMARY KEY NOT NULL CHECK(length(session_id) = 36),
    command_id TEXT NOT NULL UNIQUE CHECK(length(command_id) = 36),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*'),
    profile_id TEXT NOT NULL CHECK(length(profile_id) = 36),
    profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    workspace_revision INTEGER NOT NULL CHECK(workspace_revision >= 1),
    policy TEXT NOT NULL CHECK(policy IN ('ordinary','existing_readonly','protected')),
    policy_revision INTEGER NOT NULL CHECK(policy_revision >= 1),
    settings_revision INTEGER NOT NULL CHECK(settings_revision >= 0),
    runtime TEXT NOT NULL CHECK(length(runtime) BETWEEN 1 AND 32),
    capability_key TEXT NOT NULL CHECK(length(capability_key) = 64
        AND capability_key NOT GLOB '*[^0-9a-f]*'),
    launch_spec_hash TEXT NOT NULL CHECK(length(launch_spec_hash) = 64
        AND launch_spec_hash NOT GLOB '*[^0-9a-f]*'),
    native_started INTEGER NOT NULL DEFAULT 0 CHECK(native_started IN (0,1)),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    FOREIGN KEY (profile_id, profile_revision)
        REFERENCES profile_revisions(profile_id, revision)
) STRICT""",
}

JOURNAL_SCHEMA = """CREATE TABLE vault_publications (
    operation_id TEXT PRIMARY KEY NOT NULL CHECK(length(operation_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    root_physical_id TEXT NOT NULL CHECK(length(root_physical_id) BETWEEN 3 AND 80),
    policy_revision INTEGER NOT NULL CHECK(policy_revision BETWEEN 1 AND 9007199254740991),
    ref TEXT NOT NULL CHECK(length(ref) BETWEEN 1 AND 512),
    kind TEXT NOT NULL CHECK(kind IN ('create','replace')),
    stage_name TEXT NOT NULL UNIQUE CHECK(length(stage_name) = 52),
    backup_name TEXT NOT NULL UNIQUE CHECK(length(backup_name) = 53),
    stage_physical_id TEXT CHECK(
        stage_physical_id IS NULL OR length(stage_physical_id) BETWEEN 3 AND 80
    ),
    stage_sha256 TEXT NOT NULL CHECK(
        length(stage_sha256) = 64 AND stage_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    stage_bytes INTEGER NOT NULL CHECK(stage_bytes BETWEEN 0 AND 20971520),
    target_physical_id TEXT CHECK(
        target_physical_id IS NULL OR length(target_physical_id) BETWEEN 3 AND 80
    ),
    expected_sha256 TEXT CHECK(
        expected_sha256 IS NULL OR (
            length(expected_sha256) = 64 AND expected_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    target_bytes INTEGER CHECK(target_bytes IS NULL OR target_bytes BETWEEN 0 AND 2097152),
    state TEXT NOT NULL CHECK(
        state IN ('reserved','staged','prepared','backed_up','published','conflict')
    ),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    UNIQUE(workspace_id, ref),
    CHECK(
        (kind = 'create' AND expected_sha256 IS NULL
            AND target_physical_id IS NULL AND target_bytes IS NULL
            AND state IN ('reserved','staged','published'))
        OR (kind = 'replace' AND expected_sha256 IS NOT NULL)
    ),
    CHECK(
        (state = 'reserved' AND stage_physical_id IS NULL)
        OR (state <> 'reserved' AND stage_physical_id IS NOT NULL)
    ),
    CHECK(
        kind = 'create'
        OR (state IN ('reserved','staged')
            AND target_physical_id IS NULL AND target_bytes IS NULL)
        OR (state IN ('prepared','backed_up','published','conflict')
            AND target_physical_id IS NOT NULL AND target_bytes IS NOT NULL)
    )
) STRICT"""

PROFILE_SCHEMAS["vault_publications"] = JOURNAL_SCHEMA

PROFILE_SCHEMAS.update(
    {
        "avatars": """CREATE TABLE avatars (
    avatar_id TEXT PRIMARY KEY NOT NULL CHECK(length(avatar_id) = 36),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*'),
    master_size INTEGER NOT NULL CHECK(master_size BETWEEN 1 AND 1024),
    master_bytes INTEGER NOT NULL CHECK(master_bytes BETWEEN 1 AND 5242880),
    master_sha256 TEXT NOT NULL CHECK(length(master_sha256) = 64
        AND master_sha256 NOT GLOB '*[^0-9a-f]*'),
    total_bytes INTEGER NOT NULL CHECK(total_bytes BETWEEN 6 AND 8388608),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24)
) STRICT""",
        "avatar_variants": """CREATE TABLE avatar_variants (
    avatar_id TEXT NOT NULL CHECK(length(avatar_id) = 36),
    size INTEGER NOT NULL CHECK(size IN (24,32,64,128,256)),
    byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 1 AND 1048576),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64
        AND sha256 NOT GLOB '*[^0-9a-f]*'),
    PRIMARY KEY (avatar_id, size),
    FOREIGN KEY (avatar_id) REFERENCES avatars(avatar_id)
) STRICT""",
        "profile_avatar_refs": """CREATE TABLE profile_avatar_refs (
    profile_id TEXT NOT NULL CHECK(length(profile_id) = 36),
    profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
    avatar_id TEXT NOT NULL CHECK(length(avatar_id) = 36),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    PRIMARY KEY (profile_id, profile_revision),
    FOREIGN KEY (profile_id, profile_revision)
        REFERENCES profile_revisions(profile_id, revision),
    FOREIGN KEY (avatar_id) REFERENCES avatars(avatar_id)
) STRICT""",
    }
)

ROUTINE_SCHEMAS = {
    "routines": """CREATE TABLE routines (
    routine_id TEXT PRIMARY KEY NOT NULL CHECK(length(routine_id) = 36),
    current_revision INTEGER NOT NULL CHECK(current_revision BETWEEN 1 AND 9007199254740991),
    state TEXT NOT NULL CHECK(state IN ('draft','tested','enabled','paused','archived')),
    state_revision INTEGER NOT NULL CHECK(state_revision BETWEEN 1 AND 9007199254740991),
    tested_revision INTEGER CHECK(tested_revision IS NULL OR tested_revision >= 1),
    enabled_since TEXT CHECK(enabled_since IS NULL OR length(enabled_since) = 24),
    last_slot TEXT CHECK(last_slot IS NULL OR length(last_slot) = 24),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    CHECK((state = 'enabled') = (enabled_since IS NOT NULL))
) STRICT""",
    "routine_revisions": """CREATE TABLE routine_revisions (
    routine_id TEXT NOT NULL CHECK(length(routine_id) = 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    format INTEGER NOT NULL CHECK(format = 1),
    schedule TEXT NOT NULL CHECK(length(schedule) BETWEEN 2 AND 1024),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 81920),
    body_bytes INTEGER NOT NULL CHECK(body_bytes BETWEEN 2 AND 65536),
    body_sha256 TEXT NOT NULL CHECK(length(body_sha256) = 64
        AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
    activates_at TEXT NOT NULL CHECK(length(activates_at) = 24),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    PRIMARY KEY (routine_id, revision),
    FOREIGN KEY (routine_id) REFERENCES routines(routine_id)
) STRICT""",
    "routine_commands": """CREATE TABLE routine_commands (
    command_id TEXT PRIMARY KEY NOT NULL CHECK(length(command_id) = 36),
    operation TEXT NOT NULL CHECK(operation IN
        ('create','revise','enable','pause','archive','restore','run')),
    routine_id TEXT NOT NULL CHECK(length(routine_id) = 36),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*'),
    result_revision INTEGER NOT NULL CHECK(result_revision BETWEEN 1 AND 9007199254740991),
    result_state_revision INTEGER CHECK(result_state_revision IS NULL
        OR result_state_revision BETWEEN 1 AND 9007199254740991),
    result_state TEXT CHECK(result_state IS NULL OR result_state IN
        ('draft','tested','enabled','paused','archived')),
    result_occurrence_id TEXT CHECK(result_occurrence_id IS NULL
        OR length(result_occurrence_id) = 36),
    result_attempt_id TEXT CHECK(result_attempt_id IS NULL
        OR length(result_attempt_id) = 36),
    result_session_id TEXT CHECK(result_session_id IS NULL
        OR length(result_session_id) = 36),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    CHECK((operation = 'run'
        AND result_state_revision IS NULL AND result_state IS NULL
        AND result_occurrence_id IS NOT NULL AND result_attempt_id IS NOT NULL
        AND result_session_id IS NOT NULL)
        OR (operation <> 'run'
        AND result_state_revision IS NOT NULL AND result_state IS NOT NULL
        AND result_occurrence_id IS NULL AND result_attempt_id IS NULL
        AND result_session_id IS NULL))
) STRICT""",
    "routine_occurrences": """CREATE TABLE routine_occurrences (
    occurrence_id TEXT PRIMARY KEY NOT NULL CHECK(length(occurrence_id) = 36),
    routine_id TEXT NOT NULL CHECK(length(routine_id) = 36),
    kind TEXT NOT NULL CHECK(kind IN ('schedule','manual','decision')),
    scheduled_for TEXT CHECK(scheduled_for IS NULL OR length(scheduled_for) = 24),
    trigger_id TEXT UNIQUE CHECK(trigger_id IS NULL OR length(trigger_id) = 36),
    request_hash TEXT CHECK(request_hash IS NULL OR (length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    state TEXT NOT NULL CHECK(state IN ('claimed','skipped')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32 AND (state = 'claimed') = (reason = '')),
    recorded_at TEXT NOT NULL CHECK(length(recorded_at) = 24),
    UNIQUE (routine_id, scheduled_for),
    CHECK((kind = 'schedule') = (scheduled_for IS NOT NULL)),
    CHECK((kind = 'manual') = (trigger_id IS NOT NULL AND request_hash IS NOT NULL)),
    FOREIGN KEY (routine_id, revision) REFERENCES routine_revisions(routine_id, revision)
) STRICT""",
    "task_attempts": """CREATE TABLE task_attempts (
    attempt_id TEXT PRIMARY KEY NOT NULL CHECK(length(attempt_id) = 36),
    origin_kind TEXT NOT NULL CHECK(origin_kind IN ('routine')),
    origin_id TEXT NOT NULL UNIQUE CHECK(length(origin_id) = 36),
    scope TEXT NOT NULL CHECK(length(scope) = 64 AND scope NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT NOT NULL UNIQUE CHECK(length(session_id) = 36),
    owner TEXT NOT NULL CHECK(length(owner) = 36),
    state TEXT NOT NULL CHECK(state IN ('claimed','dispatching','running','done')),
    outcome TEXT CHECK(outcome IS NULL OR outcome IN
        ('succeeded','failed','cancelled','expired','blocked','unknown')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32),
    profile_id TEXT CHECK(profile_id IS NULL OR length(profile_id) = 36),
    profile_revision INTEGER CHECK(profile_revision IS NULL OR profile_revision >= 1),
    native_version TEXT CHECK(native_version IS NULL OR length(native_version) BETWEEN 1 AND 64),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    dispatched_at TEXT CHECK(dispatched_at IS NULL OR length(dispatched_at) = 24),
    finished_at TEXT CHECK(finished_at IS NULL OR length(finished_at) = 24),
    CHECK((state = 'done') = (outcome IS NOT NULL)),
    CHECK((state = 'done') = (finished_at IS NOT NULL))
) STRICT""",
}

# Step 29 rebuilt routine_occurrences to admit the `decision` kind, since a STRICT CHECK cannot
# be altered in place. Stores at 8 to 28 hold, and are validated against, the text they shipped
# with (ADR-065).
ROUTINE_SCHEMAS_BEFORE_29 = {
    **ROUTINE_SCHEMAS,
    "routine_occurrences": """CREATE TABLE routine_occurrences (
    occurrence_id TEXT PRIMARY KEY NOT NULL CHECK(length(occurrence_id) = 36),
    routine_id TEXT NOT NULL CHECK(length(routine_id) = 36),
    kind TEXT NOT NULL CHECK(kind IN ('schedule','manual')),
    scheduled_for TEXT CHECK(scheduled_for IS NULL OR length(scheduled_for) = 24),
    trigger_id TEXT UNIQUE CHECK(trigger_id IS NULL OR length(trigger_id) = 36),
    request_hash TEXT CHECK(request_hash IS NULL OR (length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    state TEXT NOT NULL CHECK(state IN ('claimed','skipped')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32 AND (state = 'claimed') = (reason = '')),
    recorded_at TEXT NOT NULL CHECK(length(recorded_at) = 24),
    UNIQUE (routine_id, scheduled_for),
    CHECK((kind = 'schedule') = (scheduled_for IS NOT NULL)),
    CHECK((kind = 'manual') = (trigger_id IS NOT NULL AND request_hash IS NOT NULL)),
    FOREIGN KEY (routine_id, revision) REFERENCES routine_revisions(routine_id, revision)
) STRICT""",
}

TASK_ATTEMPTS_ACTIVE_INDEX = (
    "CREATE INDEX task_attempts_active ON task_attempts(origin_id) WHERE state<>'done'"
)

BOOKMARK_SCHEMAS = {
    "vault_bookmarks": """CREATE TABLE vault_bookmarks (
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    ref TEXT NOT NULL CHECK(length(ref) BETWEEN 1 AND 1024),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    PRIMARY KEY (workspace_id, ref)
) STRICT""",
}

# `retro_decisions` holds the user's own draft decision for one retro item — the choice, the
# snooze and the hand-off time, each bound to the item hash it was made against — and never the
# item's text nor an "applied" state: the retro file owns the outcome. `vault_proposals` holds one
# open proposal per note, its text sealed in `body`, so no proposal path opens a vault for writing.
# A STRICT table's primary key is implicitly NOT NULL, so `proposal_id` cannot be null.
# No code reads or writes `retro_decisions`; the table and its rows stay.
DECISION_SCHEMAS = {
    "retro_decisions": """CREATE TABLE retro_decisions (
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    source TEXT NOT NULL CHECK(length(source) BETWEEN 1 AND 1024),
    item_id TEXT NOT NULL CHECK(length(item_id) BETWEEN 1 AND 128),
    choice TEXT CHECK(choice IS NULL OR choice IN ('approve','reject','edit','defer')),
    decided_hash TEXT CHECK(decided_hash IS NULL OR
        (length(decided_hash) = 64 AND decided_hash NOT GLOB '*[^0-9a-f]*')),
    handed_at TEXT CHECK(handed_at IS NULL OR length(handed_at) = 24),
    snoozed_hash TEXT CHECK(snoozed_hash IS NULL OR
        (length(snoozed_hash) = 64 AND snoozed_hash NOT GLOB '*[^0-9a-f]*')),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (workspace_id, source, item_id),
    CHECK((choice IS NULL) = (decided_hash IS NULL)),
    CHECK(handed_at IS NULL OR choice IS NOT NULL),
    CHECK(choice IS NOT NULL OR snoozed_hash IS NOT NULL)
) STRICT""",
    "vault_proposals": """CREATE TABLE vault_proposals (
    proposal_id TEXT PRIMARY KEY CHECK(length(proposal_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    ref TEXT NOT NULL CHECK(length(ref) BETWEEN 1 AND 1024),
    origin TEXT NOT NULL CHECK(origin IN ('note_edit','memory_promotion')),
    base_sha256 TEXT CHECK(base_sha256 IS NULL OR
        (length(base_sha256) = 64 AND base_sha256 NOT GLOB '*[^0-9a-f]*')),
    proposed_sha256 TEXT NOT NULL CHECK(length(proposed_sha256) = 64
        AND proposed_sha256 NOT GLOB '*[^0-9a-f]*'),
    body BLOB NOT NULL,
    text_bytes INTEGER NOT NULL CHECK(text_bytes BETWEEN 0 AND 1048576),
    handed_at TEXT CHECK(handed_at IS NULL OR length(handed_at) = 24),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    UNIQUE (workspace_id, ref)
) STRICT""",
}

# Step 13 rebuilt these two tables to widen one CHECK in each, since a STRICT CHECK cannot be
# altered in place: a stage may hold a 20 MiB attachment, and a proposal may promote a memory
# record. A store before step 13 holds, and is validated against, the text it shipped with.
SCHEMAS_BEFORE_13 = {
    "vault_publications": JOURNAL_SCHEMA.replace(
        "CHECK(stage_bytes BETWEEN 0 AND 20971520)", "CHECK(stage_bytes BETWEEN 0 AND 2097152)"
    ),
    "vault_proposals": DECISION_SCHEMAS["vault_proposals"].replace(
        "CHECK(origin IN ('note_edit','memory_promotion'))", "CHECK(origin IN ('note_edit'))"
    ),
}

# `vault_operations` is the recovery record of one note lifecycle operation (the journalled
# publication of hard rule 1). Its `steps` JSON is the only place the operation's paths and their
# `pre`/`post` hashes live, so the filesystem stays the progress record — a path holding `post` is
# applied, one holding `pre` is not — and there is no per-step row to disagree with the disk.
# `vault_recovery` indexes the private copies a delete keeps: a hash and a byte count, never bytes.
OPERATION_SCHEMAS = {
    "vault_operations": """CREATE TABLE vault_operations (
    operation_id TEXT PRIMARY KEY NOT NULL CHECK(length(operation_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    root_physical_id TEXT NOT NULL CHECK(length(root_physical_id) BETWEEN 3 AND 80),
    policy_revision INTEGER NOT NULL CHECK(policy_revision BETWEEN 1 AND 9007199254740991),
    kind TEXT NOT NULL CHECK(kind IN ('rename','move','archive','delete','restore')),
    request_sha256 TEXT NOT NULL CHECK(length(request_sha256) = 64
        AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    steps TEXT NOT NULL CHECK(length(steps) BETWEEN 2 AND 4096),
    state TEXT NOT NULL CHECK(state IN
        ('planned','preserved','applying','done','failed','conflict','recovery_required')),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24)
) STRICT""",
    "vault_recovery": """CREATE TABLE vault_recovery (
    recovery_id TEXT PRIMARY KEY NOT NULL CHECK(length(recovery_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    kind TEXT NOT NULL CHECK(kind IN ('deleted','preimage')),
    ref TEXT NOT NULL CHECK(length(ref) BETWEEN 1 AND 512),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_size INTEGER NOT NULL CHECK(byte_size BETWEEN 0 AND 2097152),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    expires_at TEXT NOT NULL CHECK(length(expires_at) = 24)
) STRICT""",
}

# `memory_reviews` records that this device approved exactly these bytes of one memory record, so
# devices sharing a vault never rewrite each other's approvals. It holds a hash, never the text.
MEMORY_SCHEMAS = {
    "memory_reviews": """CREATE TABLE memory_reviews (
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    memory_id TEXT NOT NULL CHECK(length(memory_id) = 36),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    reviewed_at TEXT NOT NULL CHECK(length(reviewed_at) = 24),
    PRIMARY KEY (workspace_id, memory_id)
) STRICT""",
}

# Keep the shipped <=39 binding text intact. SQLite ADD COLUMN inserts before table
# constraints, so this is also the exact SQL emitted by the 39->40 ALTER.
LAUNCH_ORIGIN_COLUMN = (
    "launch_origin TEXT NOT NULL DEFAULT 'unknown' "
    "CHECK(launch_origin IN ('unknown','interactive','routine'))"
)
PROFILE_SCHEMAS_V40 = {
    **PROFILE_SCHEMAS,
    "session_profile_bindings": PROFILE_SCHEMAS["session_profile_bindings"].replace(
        "    created_at TEXT NOT NULL CHECK(length(created_at) = 24),",
        "    created_at TEXT NOT NULL CHECK(length(created_at) = 24), "
        + LAUNCH_ORIGIN_COLUMN
        + ",",
    ),
}

INBOX_SUGGESTION_SCHEMA_STEP = 42

# Step 42: what the inbox suggestion run keeps for each vault, so it outlives a restart. One sealed
# JSON body per vault and part: "suggestions" (the suggester's latest run and its suggestions)
# and "started" (the item keys Decision's inbox host started).
INBOX_SUGGESTION_SCHEMAS = {
    "inbox_suggestions": """CREATE TABLE inbox_suggestions (
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    part TEXT NOT NULL CHECK(part IN ('suggestions','started')),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 2 AND 1048576),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    PRIMARY KEY (workspace_id, part),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
) STRICT""",
}
