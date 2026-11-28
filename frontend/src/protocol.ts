/**
 * The browser contract (VIKI envelope v2). Mirrors src/viki_agent/events.py exactly;
 * tests/test_contract.py checks that every event type and data field matches.
 * Keep one `interface XxxData { ... }` per event with flat field lines, no nested braces.
 */

import type { Attachment, UnavailableAttachment } from "./turnInput";

export const PROTOCOL_VERSION = 2;

// --- nested shapes ------------------------------------------------------------------------------

export interface Command {
  name: string;
  description: string;
  argument_hint: string;
  skill: boolean;
}

export interface ModelOption {
  value: string;
  label: string;
  description: string;
}

export interface QuestionOption {
  label: string;
  description: string;
}

export interface Question {
  header: string;
  question: string;
  options: QuestionOption[];
  multi_select: boolean;
}

/** One ToolBlock row: `head` orange, `rest` plain, `tail` dim (e.g. "→ 7 files"). */
export interface ToolRow {
  head: string;
  rest: string;
  tail: string;
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
}

export interface OtherSession {
  session_id: string;
  entrypoint: string;
  pid: number;
  title: string;
}

/** One transcript in the sessions list (REST). Live state comes from the socket, not here. */
export interface SessionRow {
  session_id: string;
  title: string;
  first_prompt: string;
  entrypoint: string;
  modified: string;
  cli_version: string;
  other: boolean;
  bot?: boolean;
}

/** One search result (REST). `at` is "" for a title match. */
export interface SearchHit {
  session_id: string;
  title: string;
  role: "title" | "user" | "assistant";
  snippet: string;
  at: string;
}

export type SessionsState = "ready" | "partial" | "error";
export type SessionsReason = "" | "time_budget" | "read_failed" | "project_unreadable";
export type SearchState = "ready" | "partial" | "superseded" | "error";
export type SearchReason =
  | ""
  | "time_budget"
  | "scan_budget"
  | "file_budget"
  | "response_budget"
  | "listing_partial"
  | "cancelled"
  | "read_failed"
  | "project_unreadable";

/** The sessions list as an observation (REST /api/sessions): `partial` when files went
 * unread, `error` when the project folder could not be listed. `omitted` counts the transcripts
 * without a row because of `reason`; an unknown count is never shown as zero. */
export interface SessionsResult {
  sessions: SessionRow[];
  state: SessionsState;
  reason: SessionsReason;
  omitted: number;
}

/** A search as an observation (REST /api/search). `superseded` means a newer query
 * replaced this one before it scanned; `partial` names the budget that stopped it. `request_id`
 * echoes the client's id, or "" when none was sent. */
export interface SearchResult {
  hits: SearchHit[];
  state: SearchState;
  reason: SearchReason;
  scanned: number;
  total: number;
  request_id: string;
}

/** The rails (REST /api/rails): read-only facts about the vault and its agents. */
export interface AgentInfo {
  name: string;
  description: string;
  tools: string[];
  model: string;
  last_run: string;
}

export interface GateRow {
  file: string;
  id: string;
  item: string;
  label: string;
  owner_applies: boolean;
}

export interface Routine {
  when: string;
  name: string;
  planned: boolean;
}

export interface VaultCounts {
  knowledge: number;
  sources: number;
  inbox: number;
}

export interface GitChip {
  branch: string;
  head: string;
  committed_at: string;
  dirty: number | null;
  state: "known" | "unknown" | "not_repository";
  ref_state: "branch" | "detached" | "unknown";
}

export interface Rails {
  agents: AgentInfo[];
  /** Always [] — the gate feature is retired, but the field stays on the wire for frozen fakes
   * and server pins (src/viki_agent/rails.py). */
  gates: GateRow[];
  routines: Routine[];
  counts: VaultCounts;
  git: GitChip;
  counts_state?: "ready" | "error" | "unknown";
}

export type TurnStatus = "success" | "cancelled" | "error";
export type ErrorCode =
  | "cli_missing"
  | "not_logged_in"
  | "billing"
  /** The process ended on its own, never a stop-kill and never a failed send. */
  | "crashed"
  /** The app killed the process tree after a Stop could not be delivered or acknowledged. The
   * turn ended `cancelled` first, and the next send resumes. */
  | "forced_stop"
  /** A send never reached the process. Whether it arrived is unknown, so no resend is offered. */
  | "provider_disconnected"
  /** The transcript being resumed is gone. Deliberately not in ErrorCard's own copy table: a
   * code it does not know renders the server's message with no button, and a retry here could
   * only fail the same way. */
  | "transcript_missing"
  | "too_many_sessions"
  | "frame_error"
  | "bad_request"
  | "internal";

// --- event data (one interface per event type) --------------------------------------------------

export interface CodeCopy {
  path: string;
  folder: string;
  branch: string;
}

export interface SessionStartedData {
  model: string;
  mode: string;
  effort: string;
  commands: Command[];
  agents: string[];
  models: ModelOption[];
  cli_version: string;
  resumed: boolean;
  additional_folders?: string[];
  code_copy?: CodeCopy;
}
export interface SessionBlockedData {
  reason: "live_elsewhere";
  holder: OtherSession;
}
export interface SessionClosedData {
  reason: "requested" | "browser_gone";
}
export interface UserMessageData {
  attachments?: readonly Attachment[];
  unavailable_attachments?: readonly UnavailableAttachment[];
  text: string;
  command: string;
  at: string;
}
export interface TurnStartedData {
  turn_id: string;
}
export interface TextDeltaData {
  block_id: string;
  text: string;
}
export interface TextDoneData {
  block_id: string;
  text: string;
}
export interface ThinkingDeltaData {
  block_id: string;
  text: string;
}
export interface ThinkingDoneData {
  block_id: string;
  text: string;
  duration_ms: number;
}
export interface ArtifactStart {
  artifact_id: string;
  kind: "file" | "file_change" | "diff" | "test";
  operation: "read" | "write" | "edit" | "test";
  path: string;
  path_state: "workspace_relative" | "unknown";
}
export interface ArtifactDetailRef {
  entry_uuid: string;
  tool_use_id: string;
  source_revision: string;
}
export interface ArtifactResult {
  artifact_id: string;
  observation: "result" | "error" | "unknown";
  byte_size: number;
  sha256: string;
  preview: string;
  truncated: boolean;
  source_revision: string;
  detail_ref: ArtifactDetailRef | null;
}
export interface ToolStartedData {
  block_id: string;
  tool: string;
  row: ToolRow;
  parent_id: string;
  artifact?: ArtifactStart;
}
export interface ToolDoneData {
  block_id: string;
  is_error: boolean;
  summary: string;
  preview: string;
  tail: string;
  artifact?: ArtifactResult;
}
export interface SubagentStartedData {
  block_id: string;
  agent: string;
  model: string;
  description: string;
  reason: string;
}
export interface SubagentDoneData {
  block_id: string;
  status: "completed" | "failed" | "stopped";
  tool_uses?: number;
  total_tokens?: number;
  duration_ms?: number;
  summary?: string;
}
/** A message Claude Code has not read yet: accepted to inject into the running turn before the
 * CLI confirms it started. `text` is the actor's bounded display copy. */
export interface SteerPendingData {
  steer_id: string;
  text: string;
  command: string;
  images: number;
  at: string;
}
export interface SteerEndedData {
  steer_id: string;
  outcome: "started" | "dropped";
}
export interface QuestionAskedData {
  tool_use_id: string;
  questions: Question[];
}
export interface PermissionRequestedData {
  tool_use_id: string;
  kind: "tool" | "plan";
  tool: string;
  summary: string;
  /** The card's headline, in English, worded from the tool by mapper.tool_question. "" on a
   * plan card and on any frame written before this field existed; the card falls back then. */
  question: string;
  reason: string;
  agent: string;
  plan: string;
}
export interface RequestCancelledData {
  tool_use_id: string;
}
/** The card was answered. Carries no answer: what the user chose is between them and the CLI,
 * and every other view of the session only needs to know the card is closed. */
export interface RequestAnsweredData {
  tool_use_id: string;
}
export interface PermissionDeniedData {
  tool: string;
  reason: string;
}
export interface HookData {
  name: string;
  outcome: "blocked" | "error";
  message: string;
}
export interface StatusData {
  state: string;
  mode: string;
}
export interface CompactedData {
  trigger: string;
  pre_tokens: number;
}
export interface RateLimitData {
  /** "allowed_warning" (the turn worked; the window is close) or "rejected" (it was refused).
   * The CLI also has "allowed", which mapper.py drops rather than sending. */
  status: string;
  kind: string;
  resets_at: number;
}
export interface VaultSessionsData {
  others: OtherSession[];
  live: string[];
}
export interface KanbanChangedData {
  workspace_id: string;
}
export interface TurnDoneData {
  status: TurnStatus;
  usage: Usage;
  context_percent: number | null;
  duration_ms: number;
}
export interface ErrorData {
  code: ErrorCode;
  message: string;
  recoverable: boolean;
}

/** Event type → its data. The keys are the wire `type` strings. */
export interface EventMap {
  "session.open.refused": SessionOpenRefusedData;
  "session.started": SessionStartedData;
  "session.blocked": SessionBlockedData;
  "session.closed": SessionClosedData;
  "user.message": UserMessageData;
  "turn.started": TurnStartedData;
  "text.delta": TextDeltaData;
  "text.done": TextDoneData;
  "thinking.delta": ThinkingDeltaData;
  "thinking.done": ThinkingDoneData;
  "tool.started": ToolStartedData;
  "tool.done": ToolDoneData;
  "subagent.started": SubagentStartedData;
  "subagent.done": SubagentDoneData;
  "steer.pending": SteerPendingData;
  "steer.ended": SteerEndedData;
  "question.asked": QuestionAskedData;
  "permission.requested": PermissionRequestedData;
  "request.cancelled": RequestCancelledData;
  "request.answered": RequestAnsweredData;
  "permission.denied": PermissionDeniedData;
  hook: HookData;
  status: StatusData;
  compacted: CompactedData;
  rate_limit: RateLimitData;
  "vault.sessions": VaultSessionsData;
  "kanban.changed": KanbanChangedData;
  "turn.done": TurnDoneData;
  error: ErrorData;
}

export type EventType = keyof EventMap;

/** App identities bind one native request, including driver replacement within an actor. */
export interface NativeRequest {
  runtime_id: string;
  request_id: string;
  turn_id: string;
}

/** Session frames carry the subscription request ID; global frames use an empty request ID. */
export type Envelope = {
  [K in EventType]: {
    v: 2;
    type: K;
    session_id: string;
    generation: string;
    request_id: string;
    seq: number;
    data: EventMap[K];
    native_request?: NativeRequest;
  };
}[EventType];

export type HistoryItem = { [K in EventType]: { type: K; data: EventMap[K] } }[EventType];
export type SnapshotItem = HistoryItem & { native_request?: NativeRequest };

export interface SnapshotState {
  live: boolean;
  max_live_sessions?: number;
  info: SessionStartedData | null;
  mode: string;
  blocked: SessionBlockedData | null;
  closed: boolean;
  error: ErrorData | null;
  compacting: boolean;
  context_percent: number | null;
  rate_limit: RateLimitData | null;
}

export interface SessionSnapshot {
  v: 2;
  type: "session.snapshot";
  session_id: string;
  generation: string;
  request_id: string;
  seq: number;
  data: { events: SnapshotItem[]; state: SnapshotState; truncated: boolean };
}

export interface SessionSynced {
  v: 2;
  type: "session.synced";
  session_id: string;
  generation: string;
  request_id: string;
  seq: number;
  data: Record<string, never>;
}

export interface HistoryPage {
  v: 2;
  type: "history.page";
  session_id: string;
  generation: string;
  request_id: string;
  data: {
    events: HistoryItem[];
    next_before_uuid: string | null;
    error?: "too_large" | "unavailable" | "busy" | "transcript_missing";
    /** The page came from the tail window of an over-budget transcript. */
    partial?: "tail_budget";
  };
}

export interface ControlReceipt {
  v: 2;
  type: "control.receipt";
  session_id: string;
  generation: string;
  command_id: string;
  data: {
    status: "applied" | "rejected" | "unknown";
    reason: string;
    target?: { session_id: string; generation: string };
  };
}

export interface SessionOpenRefusedData {
  reason: "unavailable" | "storage_unavailable" | "model_review_required";
}

export type SessionOpenRefused = Extract<Envelope, { type: "session.open.refused" }>;

export type ServerMessage =
  | Envelope
  | SessionSnapshot
  | SessionSynced
  | HistoryPage
  | ControlReceipt;

// --- client to server ---------------------------------------------------------------------------

export type ClientMessage =
  | { type: "auth"; token: string; v: 2 }
  | {
      type: "session.open";
      session_id: string;
      request_id: string;
      generation?: string;
      after_seq?: number;
      existing_only?: boolean;
      expected_model?: string | null;
    }
  | {
      type: "history.more";
      session_id: string;
      generation: string;
      request_id: string;
      before_uuid?: string;
    }
  | { type: "session.detach"; session_id: string; request_id: string }
  | {
      type: "session.fork";
      session_id: string;
      generation: string;
      command_id: string;
      fork_id: string;
    }
  | { type: "turn.send"; session_id: string; generation: string; command_id: string; text: string }
  | { type: "turn.interrupt"; session_id: string; generation: string; command_id: string }
  | {
      type: "question.answer";
      session_id: string;
      generation: string;
      command_id: string;
      tool_use_id: string;
      native_request: NativeRequest;
      answers: Record<string, string>;
      response?: string;
    }
  | {
      type: "permission.respond";
      session_id: string;
      generation: string;
      command_id: string;
      tool_use_id: string;
      native_request: NativeRequest;
      allow: boolean;
    }
  | {
      type: "session.set_model";
      session_id: string;
      generation: string;
      command_id: string;
      model: string;
    }
  | {
      type: "session.set_effort";
      session_id: string;
      generation: string;
      command_id: string;
      effort: string;
    }
  | {
      type: "session.set_mode";
      session_id: string;
      generation: string;
      command_id: string;
      mode: string;
    }
  | {
      type: "session.close";
      session_id: string;
      generation: string;
      command_id: string;
      turn_id?: string;
    }
  | { type: "open.url"; url: string }
  | { type: "open.vault"; path: string }
  | { type: "open.code"; workspace_id: string; path: string; line?: number }
  | { type: "open.bot_memory"; workspace_id: string; path: string }
  | { type: "open.transcript"; session_id: string };
