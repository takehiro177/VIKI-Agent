/** Bounded private Bot learning client: a Bot's runs, sealed proposals and the owner's decisions. */
import { boundedJson } from "../boundedResponse";

export type LearningMode = "reflect" | "dream";
export type ProposalOp =
  | "lesson_add"
  | "fact_add"
  | "profile_add"
  | "record_supersede"
  | "record_retire";
export type ProposalState = "open" | "applied" | "rejected" | "stale" | "undone";
export type ProposalAction = "apply" | "reject" | "undo";

export interface LearningRun {
  run_id: string;
  mode: "reflect" | "dream" | "import";
  trigger: "user" | "auto";
  state: "running" | "done" | "failed";
  reason: string;
  episodes: number;
  proposed: number;
  rejected: number;
  started_at: string;
  finished_at: string | null;
}
export interface LearningProposal {
  proposal_id: string;
  op: ProposalOp;
  state: ProposalState;
  claim: string;
  target_claim: string;
  why: string;
  evidence: { session_id: string | null; quote: string | null }[];
  result_id: string | null;
  created_at: string;
  decided_at: string | null;
}
export interface LearnedRecord {
  memory_id: string;
  ref: string;
  state: string;
  claim: string;
  uses: number;
  helpful: number;
  harmful: number;
  last_used_at: string | null;
}
export interface LearningOverview {
  bot_id: string;
  running: string | null;
  records_readable: boolean;
  runs: LearningRun[];
  proposals: LearningProposal[];
  records: LearnedRecord[];
  tainted_sessions: { session_id: string; sources: string; tainted_at: string }[];
  episodes: { unreflected: number; last_finished_at: string | null };
  legacy_files: number;
}

export interface BotLearningApi {
  overview(botId: string, signal?: AbortSignal): Promise<LearningOverview>;
  start(botId: string, mode: LearningMode): Promise<LearningRun>;
  importLegacy(botId: string): Promise<LearningRun>;
  decide(proposalId: string, action: ProposalAction): Promise<ProposalState>;
  untaint(sessionId: string): Promise<boolean>;
}

export class BotLearningApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly detail: string = "",
  ) {
    super(code);
  }
}

const TIMEOUT = 15_000;
const RESPONSE_MAX = 2 << 20;
const BASE = "/api/v2/bot-learning";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const OPS = new Set(["lesson_add", "fact_add", "profile_add", "record_supersede", "record_retire"]);
const STATES = new Set(["open", "applied", "rejected", "stale", "undone"]);

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new BotLearningApiError(0, "invalid_response");
  return value as Record<string, unknown>;
}

function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new BotLearningApiError(0, "invalid_response");
  return value;
}

function text(value: unknown): string {
  if (typeof value !== "string") throw new BotLearningApiError(0, "invalid_response");
  return value;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0)
    throw new BotLearningApiError(0, "invalid_response");
  return value;
}

function nullableText(value: unknown): string | null {
  return value === null ? null : text(value);
}

function parseRun(value: unknown): LearningRun {
  const row = record(value);
  return {
    run_id: text(row.run_id),
    mode: text(row.mode) as LearningRun["mode"],
    trigger: text(row.trigger) as LearningRun["trigger"],
    state: text(row.state) as LearningRun["state"],
    reason: text(row.reason),
    episodes: count(row.episodes),
    proposed: count(row.proposed),
    rejected: count(row.rejected),
    started_at: text(row.started_at),
    finished_at: nullableText(row.finished_at),
  };
}

function parseProposal(value: unknown): LearningProposal {
  const row = record(value);
  const op = text(row.op);
  const state = text(row.state);
  if (!OPS.has(op) || !STATES.has(state)) throw new BotLearningApiError(0, "invalid_response");
  return {
    proposal_id: text(row.proposal_id),
    op: op as ProposalOp,
    state: state as ProposalState,
    claim: text(row.claim),
    target_claim: text(row.target_claim),
    why: text(row.why),
    evidence: list(row.evidence).map((item) => {
      const entry = record(item);
      return { session_id: nullableText(entry.session_id), quote: nullableText(entry.quote) };
    }),
    result_id: nullableText(row.result_id),
    created_at: text(row.created_at),
    decided_at: nullableText(row.decided_at),
  };
}

export function parseOverview(value: unknown): LearningOverview {
  const row = record(value);
  const episodes = record(row.episodes);
  return {
    bot_id: text(row.bot_id),
    running: nullableText(row.running),
    records_readable: row.records_readable === true,
    runs: list(row.runs).map(parseRun),
    proposals: list(row.proposals).map(parseProposal),
    records: list(row.records).map((item) => {
      const entry = record(item);
      return {
        memory_id: text(entry.memory_id),
        ref: text(entry.ref),
        state: text(entry.state),
        claim: text(entry.claim),
        uses: count(entry.uses),
        helpful: count(entry.helpful),
        harmful: count(entry.harmful),
        last_used_at: nullableText(entry.last_used_at),
      };
    }),
    tainted_sessions: list(row.tainted_sessions).map((item) => {
      const entry = record(item);
      return {
        session_id: text(entry.session_id),
        sources: text(entry.sources),
        tainted_at: text(entry.tainted_at),
      };
    }),
    episodes: {
      unreflected: count(episodes.unreflected),
      last_finished_at: nullableText(episodes.last_finished_at),
    },
    legacy_files: count(row.legacy_files),
  };
}

export function createBotLearningApi(
  token: string,
  fetchImpl: typeof fetch = fetch,
): BotLearningApi {
  async function request(path: string, body?: object, signal?: AbortSignal): Promise<unknown> {
    if (signal?.aborted) throw new BotLearningApiError(0, "cancelled");
    const controller = new AbortController();
    const relay = (): void => controller.abort();
    signal?.addEventListener("abort", relay, { once: true });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, TIMEOUT);
    try {
      const response = await fetchImpl(`${BASE}${path}`, {
        method: body === undefined ? "GET" : "POST",
        signal: controller.signal,
        headers: {
          authorization: `Bearer ${token}`,
          ...(body === undefined ? {} : { "content-type": "application/json" }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const parsed: unknown = await boundedJson(response, RESPONSE_MAX);
      if (!response.ok) {
        const error = record(record(parsed).error);
        throw new BotLearningApiError(
          response.status,
          typeof error.code === "string" ? error.code : "unavailable",
          typeof error.message === "string" ? error.message : "",
        );
      }
      return parsed;
    } catch (error) {
      if (error instanceof BotLearningApiError) throw error;
      if (signal?.aborted) throw new BotLearningApiError(0, "cancelled");
      if (timedOut) throw new BotLearningApiError(408, "intake_timeout");
      throw new BotLearningApiError(0, "unavailable");
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", relay);
    }
  }
  function id(value: string): string {
    if (!UUID.test(value)) throw new BotLearningApiError(0, "invalid_input");
    return value;
  }
  return {
    async overview(botId, signal) {
      return parseOverview(await request(`/${id(botId)}`, undefined, signal));
    },
    async start(botId, mode) {
      return parseRun(record(await request(`/${id(botId)}/runs`, { mode })).run);
    },
    async importLegacy(botId) {
      return parseRun(record(await request(`/${id(botId)}/import`, {})).run);
    },
    async decide(proposalId, action) {
      const state = text(record(await request(`/proposals/${id(proposalId)}`, { action })).state);
      if (!STATES.has(state)) throw new BotLearningApiError(0, "invalid_response");
      return state as ProposalState;
    },
    async untaint(sessionId) {
      return record(await request(`/sessions/${id(sessionId)}/untaint`, {})).cleared === true;
    },
  };
}
