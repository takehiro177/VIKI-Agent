"""App-owned Bot links and delivery receipts; native conversations stay with the provider."""

from __future__ import annotations

BOT_SCHEMA_STEP = 22
BOT_MEMORY_SCHEMA_STEP = 37

BOT_CODE_SCHEMA_STEP = 40
BOT_CODE_SCHEMAS = {
    "bot_code_folders": """CREATE TABLE bot_code_folders (
    bot_id TEXT PRIMARY KEY NOT NULL CHECK(length(bot_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    FOREIGN KEY (bot_id) REFERENCES bots(bot_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
) STRICT""",
    "bot_code_copies": """CREATE TABLE bot_code_copies (
    worktree_id TEXT PRIMARY KEY NOT NULL CHECK(length(worktree_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36
        AND length(CAST(bot_id AS BLOB)) = 36
        AND bot_id GLOB '????????-????-????-????-????????????'
        AND length(replace(bot_id, '-', '')) = 32
        AND replace(bot_id, '-', '') NOT GLOB '*[^0-9a-f]*'),
    session_id TEXT CHECK(session_id IS NULL OR length(session_id) = 36),
    thread_id TEXT CHECK(thread_id IS NULL OR length(thread_id) = 36),
    state TEXT NOT NULL CHECK(state IN ('bound','released','former')),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    CHECK((session_id IS NULL) <> (thread_id IS NULL)),
    FOREIGN KEY (worktree_id) REFERENCES worktrees(worktree_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
) STRICT""",
    "bot_code_baselines": """CREATE TABLE bot_code_baselines (
    workspace_id TEXT PRIMARY KEY NOT NULL CHECK(length(workspace_id) = 36),
    git_fingerprint TEXT NOT NULL CHECK(length(git_fingerprint) = 64
        AND length(CAST(git_fingerprint AS BLOB)) = 64
        AND git_fingerprint NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
) STRICT""",
}
BOT_CODE_INDEXES = {
    "bot_code_bound_session": "CREATE UNIQUE INDEX bot_code_bound_session "
    "ON bot_code_copies(session_id) WHERE state='bound' AND session_id IS NOT NULL",
    "bot_code_bound_thread_folder": "CREATE UNIQUE INDEX bot_code_bound_thread_folder "
    "ON bot_code_copies(thread_id,workspace_id) WHERE state='bound' AND thread_id IS NOT NULL",
}

BOT_USAGE_SCHEMA_STEP = 41
BOT_USAGE_SCHEMAS = {
    "bot_usage_sessions": """CREATE TABLE bot_usage_sessions (
    session_id TEXT PRIMARY KEY NOT NULL CHECK(length(session_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36),
    profile_id TEXT NOT NULL CHECK(length(profile_id) = 36),
    kind TEXT NOT NULL CHECK(kind IN ('main','task','bot_thread','routine')),
    connection_id TEXT CHECK(connection_id IS NULL OR length(connection_id) = 36),
    read_ns INTEGER CHECK(read_ns IS NULL OR read_ns BETWEEN 0 AND 9223372036854775807)
) STRICT""",
    "bot_usage_calls": """CREATE TABLE bot_usage_calls (
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    message_id TEXT NOT NULL CHECK(length(message_id) BETWEEN 1 AND 128),
    prompt_id TEXT CHECK(prompt_id IS NULL OR length(prompt_id) BETWEEN 1 AND 64),
    at TEXT NOT NULL CHECK(length(at) = 24),
    model TEXT NOT NULL CHECK(length(model) <= 256),
    agent INTEGER NOT NULL CHECK(agent IN (0,1)),
    input_tokens INTEGER NOT NULL CHECK(input_tokens BETWEEN 0 AND 9007199254740991),
    output_tokens INTEGER NOT NULL CHECK(output_tokens BETWEEN 0 AND 9007199254740991),
    cache_read_tokens INTEGER NOT NULL CHECK(cache_read_tokens BETWEEN 0 AND 9007199254740991),
    cache_write_tokens INTEGER NOT NULL CHECK(cache_write_tokens BETWEEN 0 AND 9007199254740991),
    UNIQUE (session_id, message_id),
    FOREIGN KEY (session_id) REFERENCES bot_usage_sessions(session_id)
) STRICT""",
}
BOT_USAGE_INDEXES = {"bot_usage_calls_at": "CREATE INDEX bot_usage_calls_at ON bot_usage_calls(at)"}

BOT_MEMORY_SCHEMAS = {
    "session_bot_memory": """CREATE TABLE session_bot_memory (
    session_id TEXT PRIMARY KEY NOT NULL CHECK(length(session_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36),
    FOREIGN KEY (session_id) REFERENCES session_profile_bindings(session_id) ON DELETE CASCADE
) STRICT""",
}

BOT_THREAD_SCHEMA_STEP = 39
BOT_THREAD_SCHEMAS = {
    "bot_threads": """CREATE TABLE bot_threads (
    thread_id TEXT PRIMARY KEY NOT NULL CHECK(length(thread_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 131072),
    body_bytes INTEGER NOT NULL CHECK(body_bytes BETWEEN 1 AND 65536),
    body_hash TEXT NOT NULL CHECK(length(body_hash) = 64),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
) STRICT""",
    "bot_thread_sessions": """CREATE TABLE bot_thread_sessions (
    thread_id TEXT NOT NULL CHECK(length(thread_id) = 36),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    PRIMARY KEY (thread_id, session_id),
    FOREIGN KEY (thread_id) REFERENCES bot_threads(thread_id) ON DELETE CASCADE
) STRICT""",
}

BOT_SCHEMAS = {
    "bots": """CREATE TABLE bots (
    bot_id TEXT PRIMARY KEY NOT NULL CHECK(length(bot_id) = 36),
    profile_id TEXT NOT NULL CHECK(length(profile_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    chat_session_id TEXT NOT NULL UNIQUE CHECK(length(chat_session_id) = 36),
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*'),
    state_revision INTEGER NOT NULL CHECK(state_revision BETWEEN 1 AND 9007199254740991),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    UNIQUE(profile_id, workspace_id),
    FOREIGN KEY (profile_id) REFERENCES profiles(profile_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
) STRICT""",
    "bot_deliveries": """CREATE TABLE bot_deliveries (
    attempt_id TEXT PRIMARY KEY NOT NULL CHECK(length(attempt_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36),
    attention TEXT NOT NULL CHECK(attention IN ('silent','defer','notify')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32),
    state TEXT NOT NULL CHECK(state IN
        ('held','off','unavailable','sending','sent','failed','unknown')),
    delivery_reason TEXT NOT NULL CHECK(length(delivery_reason) <= 32),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    updated_at TEXT NOT NULL CHECK(length(updated_at) = 24),
    FOREIGN KEY (attempt_id) REFERENCES task_attempts(attempt_id),
    FOREIGN KEY (bot_id) REFERENCES bots(bot_id)
) STRICT""",
}

# What Bots learn from (episodes, session taint) and what learning runs propose; identifiers,
# states, hashes, stamps and counts only. Conversation text stays in Claude Code's transcripts and
# a proposal's text is sealed (ADR-088).
BOT_LEARNING_SCHEMA_STEP = 43
BOT_LEARNING_SCHEMAS = {
    "bot_episodes": """CREATE TABLE bot_episodes (
    episode_id TEXT PRIMARY KEY NOT NULL CHECK(length(episode_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36),
    session_id TEXT NOT NULL CHECK(length(session_id) = 36),
    kind TEXT NOT NULL CHECK(kind IN ('main','task','bot_thread','routine')),
    started_at TEXT NOT NULL CHECK(length(started_at) = 24),
    finished_at TEXT NOT NULL CHECK(length(finished_at) = 24),
    status TEXT NOT NULL CHECK(status IN ('success','cancelled','error')),
    tainted INTEGER NOT NULL CHECK(tainted IN (0,1)),
    tools INTEGER NOT NULL CHECK(tools BETWEEN 0 AND 100000),
    lessons TEXT NOT NULL CHECK(length(lessons) <= 190 AND lessons NOT GLOB '*[^0-9a-f,-]*'),
    reflected INTEGER NOT NULL CHECK(reflected IN (0,1))
) STRICT""",
    "bot_session_taint": """CREATE TABLE bot_session_taint (
    session_id TEXT PRIMARY KEY NOT NULL CHECK(length(session_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36),
    sources TEXT NOT NULL CHECK(sources IN ('web','mcp','web,mcp')),
    tainted_at TEXT NOT NULL CHECK(length(tainted_at) = 24)
) STRICT""",
    "bot_runs": """CREATE TABLE bot_runs (
    run_id TEXT PRIMARY KEY NOT NULL CHECK(length(run_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36),
    mode TEXT NOT NULL CHECK(mode IN ('reflect','dream','import')),
    trigger TEXT NOT NULL CHECK(trigger IN ('user','auto')),
    session_id TEXT CHECK(session_id IS NULL OR length(session_id) = 36),
    state TEXT NOT NULL CHECK(state IN ('running','done','failed')),
    reason TEXT NOT NULL CHECK(length(reason) <= 32),
    episodes INTEGER NOT NULL CHECK(episodes BETWEEN 0 AND 1000),
    proposed INTEGER NOT NULL CHECK(proposed BETWEEN 0 AND 1000),
    rejected INTEGER NOT NULL CHECK(rejected BETWEEN 0 AND 1000),
    started_at TEXT NOT NULL CHECK(length(started_at) = 24),
    finished_at TEXT CHECK(finished_at IS NULL OR length(finished_at) = 24),
    CHECK((state = 'running') = (finished_at IS NULL))
) STRICT""",
    "bot_proposals": """CREATE TABLE bot_proposals (
    proposal_id TEXT PRIMARY KEY NOT NULL CHECK(length(proposal_id) = 36),
    run_id TEXT NOT NULL CHECK(length(run_id) = 36),
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36),
    op TEXT NOT NULL CHECK(op IN
        ('lesson_add','fact_add','profile_add','record_supersede','record_retire')),
    target_id TEXT CHECK(target_id IS NULL OR length(target_id) = 36),
    target_sha256 TEXT CHECK(target_sha256 IS NULL OR (length(target_sha256) = 64
        AND target_sha256 NOT GLOB '*[^0-9a-f]*')),
    body BLOB NOT NULL CHECK(length(body) BETWEEN 1 AND 131072),
    body_sha256 TEXT NOT NULL CHECK(length(body_sha256) = 64
        AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL CHECK(state IN ('open','applied','rejected','stale','undone')),
    result_id TEXT CHECK(result_id IS NULL OR length(result_id) = 36),
    created_at TEXT NOT NULL CHECK(length(created_at) = 24),
    decided_at TEXT CHECK(decided_at IS NULL OR length(decided_at) = 24),
    CHECK((state = 'open') = (decided_at IS NULL)),
    CHECK((target_id IS NULL) = (target_sha256 IS NULL)),
    FOREIGN KEY (run_id) REFERENCES bot_runs(run_id)
) STRICT""",
    "bot_lesson_stats": """CREATE TABLE bot_lesson_stats (
    workspace_id TEXT NOT NULL CHECK(length(workspace_id) = 36),
    memory_id TEXT NOT NULL CHECK(length(memory_id) = 36),
    bot_id TEXT NOT NULL CHECK(length(bot_id) = 36),
    uses INTEGER NOT NULL CHECK(uses BETWEEN 0 AND 1000000000),
    helpful INTEGER NOT NULL CHECK(helpful BETWEEN 0 AND 1000000000),
    harmful INTEGER NOT NULL CHECK(harmful BETWEEN 0 AND 1000000000),
    last_used_at TEXT CHECK(last_used_at IS NULL OR length(last_used_at) = 24),
    PRIMARY KEY (workspace_id, memory_id)
) STRICT""",
}
# Delete order for a removed Bot or workspace: proposals before the runs they name.
LEARNING_TABLES = (
    "bot_proposals",
    "bot_runs",
    "bot_episodes",
    "bot_session_taint",
    "bot_lesson_stats",
)
BOT_LEARNING_INDEXES = {
    "bot_episodes_bot": "CREATE INDEX bot_episodes_bot ON bot_episodes(bot_id,finished_at)",
}
