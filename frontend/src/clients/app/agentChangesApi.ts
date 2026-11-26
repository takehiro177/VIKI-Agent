import { boundedJson } from "../boundedResponse";

// The wire format is the server's window-token-only Apply surface: `GET /api/v2/agent-changes`
// and `POST /api/v2/agent-changes/{change_id}`.
const BASE = "/api/v2/agent-changes";
// Up to 16 held changes, each carrying its values whole: a routine's prompt is at most 32,768
// UTF-8 bytes, and a held setting's two values at most 8,192 characters each. The server escapes
// every non-ASCII or control character as \uXXXX (a character outside the BMP as two), so one
// change measures at most about 192 KiB and 16 about 3 MiB. 4 MiB gives that room.
const MAX = 4 * 1024 * 1024;
const TIMEOUT = 15_000;

export interface SettingChangeSummary {
  key: string;
  /** The current value, whole; a reset discards it. */
  from: unknown;
  /** The value Apply stores, whole; null for a reset. */
  to: unknown;
  reset: boolean;
}
export interface ScheduleSummary {
  kind: "manual" | "daily" | "weekly";
  times: string[];
  days: number[];
  timezone: string | null;
}
export interface ProfileRef {
  profile_id: string;
  name: string;
}
export interface WorkspaceRef {
  workspace_id: string;
  name: string;
}
export interface RoutineEnableSummary {
  routine_id: string;
  name: string;
  revision: number;
  /** Omitted by an older server, or dropped here if malformed — never thrown on. */
  schedule?: ScheduleSummary;
  profile?: ProfileRef;
  workspace?: WorkspaceRef;
  /** The routine's whole prompt. */
  prompt?: string;
}
export type AgentChange =
  | {
      change_id: string;
      session_id: string;
      kind: "setting";
      summary: SettingChangeSummary;
      expires_in_s: number;
    }
  | {
      change_id: string;
      session_id: string;
      kind: "routine_enable";
      summary: RoutineEnableSummary;
      expires_in_s: number;
    };

/** The server's typed `{code, message}`; "unavailable" when the answer carries neither. */
export class AgentChangesApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export interface AgentChangesApi {
  list(signal?: AbortSignal): Promise<{ seq: number; changes: AgentChange[] }>;
  decide(id: string, decision: "apply" | "reject"): Promise<{ state: string }>;
}

const record = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const str = (value: unknown): value is string => typeof value === "string";
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const stringArray = (value: unknown): value is string[] => Array.isArray(value) && value.every(str);
const numberArray = (value: unknown): value is number[] =>
  Array.isArray(value) && value.every(finite);
const SCHEDULE_KINDS = new Set(["manual", "daily", "weekly"]);

/** Dropped (undefined) rather than thrown on: an older server sends no `schedule` at all, and a
 * malformed one is no better than a missing one. */
function parseSchedule(value: unknown): ScheduleSummary | undefined {
  const row = record(value);
  if (
    !row ||
    !str(row.kind) ||
    !SCHEDULE_KINDS.has(row.kind) ||
    !stringArray(row.times) ||
    !numberArray(row.days) ||
    !(row.timezone === null || str(row.timezone))
  )
    return undefined;
  return {
    kind: row.kind as ScheduleSummary["kind"],
    times: row.times,
    days: row.days,
    timezone: row.timezone,
  };
}

function parseProfile(value: unknown): ProfileRef | undefined {
  const row = record(value);
  if (!row || !str(row.profile_id) || !str(row.name)) return undefined;
  return { profile_id: row.profile_id, name: row.name };
}

function parseWorkspace(value: unknown): WorkspaceRef | undefined {
  const row = record(value);
  if (!row || !str(row.workspace_id) || !str(row.name)) return undefined;
  return { workspace_id: row.workspace_id, name: row.name };
}

/** An unrecognized `kind`, or a summary missing that kind's own fields, is dropped rather than
 * failing the whole list: a server ahead of this build may add a third kind. */
function parseChange(value: unknown): AgentChange | null {
  const row = record(value);
  if (!row || !str(row.change_id) || !str(row.session_id) || !finite(row.expires_in_s)) return null;
  const summary = record(row.summary);
  if (!summary) return null;
  if (row.kind === "setting" && str(summary.key) && typeof summary.reset === "boolean") {
    return {
      change_id: row.change_id,
      session_id: row.session_id,
      kind: "setting",
      summary: { key: summary.key, from: summary.from, to: summary.to, reset: summary.reset },
      expires_in_s: row.expires_in_s,
    };
  }
  if (
    row.kind === "routine_enable" &&
    str(summary.routine_id) &&
    str(summary.name) &&
    finite(summary.revision)
  ) {
    const routineSummary: RoutineEnableSummary = {
      routine_id: summary.routine_id,
      name: summary.name,
      revision: summary.revision,
    };
    const schedule = parseSchedule(summary.schedule);
    const profile = parseProfile(summary.profile);
    const workspace = parseWorkspace(summary.workspace);
    const prompt = str(summary.prompt) ? summary.prompt : undefined;
    // exactOptionalPropertyTypes: an absent optional field is fine, an explicit `undefined`
    // value is not, so a malformed or missing one is left off rather than set to undefined.
    if (schedule !== undefined) routineSummary.schedule = schedule;
    if (profile !== undefined) routineSummary.profile = profile;
    if (workspace !== undefined) routineSummary.workspace = workspace;
    if (prompt !== undefined) routineSummary.prompt = prompt;
    return {
      change_id: row.change_id,
      session_id: row.session_id,
      kind: "routine_enable",
      summary: routineSummary,
      expires_in_s: row.expires_in_s,
    };
  }
  return null;
}

export function createAgentChangesApi(
  token: string,
  fetchImpl: typeof fetch = fetch,
): AgentChangesApi {
  async function call(
    method: "GET" | "POST",
    url: string,
    body: unknown,
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    if (signal?.aborted) throw new Error("cancelled");
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, TIMEOUT);
    try {
      const response = await fetchImpl(url, {
        method,
        signal: controller.signal,
        headers: {
          authorization: `Bearer ${token}`,
          ...(body === undefined ? {} : { "content-type": "application/json" }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const answer = record(await boundedJson(response, MAX).catch(() => null));
      if (response.ok && answer) return answer;
      // A refusal carries {error: {code, message}}; both reach the caller. A plain-text 404 (the
      // surface itself is gone: an e2e or recovery server, or no CLI file) carries neither, but
      // is still a surface that will never come back, same as an explicit `not_found` code.
      const error = record(answer?.error);
      const code =
        error && str(error.code) && error.code
          ? error.code
          : response.status === 404
            ? "not_found"
            : "unavailable";
      const message = error && str(error.message) && error.message ? error.message : code;
      throw new AgentChangesApiError(code, message);
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }

  return {
    async list(signal) {
      const answer = await call("GET", BASE, undefined, signal);
      if (!finite(answer.seq) || !Array.isArray(answer.changes))
        throw new AgentChangesApiError("unavailable", "unavailable");
      const changes: AgentChange[] = [];
      for (const raw of answer.changes) {
        const parsed = parseChange(raw);
        if (parsed) changes.push(parsed);
      }
      return { seq: answer.seq, changes };
    },
    async decide(id, decision) {
      const answer = await call("POST", `${BASE}/${encodeURIComponent(id)}`, { decision });
      if (!str(answer.state)) throw new AgentChangesApiError("unavailable", "unavailable");
      return { state: answer.state };
    },
  };
}
