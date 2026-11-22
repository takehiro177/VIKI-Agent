/** Bounded private Bot usage client: token/cost summaries and per-session traces. */
import { boundedJson } from "../boundedResponse";

export type UsageDays = 7 | 30 | 90;
export type UsageKind = "main" | "task" | "bot_thread" | "routine" | "subagent";
/** A real session's own kind: "subagent" is a summary-only bucket, never a session's kind. */
export type UsageSessionKind = "main" | "task" | "bot_thread" | "routine";

export interface UsageTotals {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  calls: number;
}
export interface UsageDailyRow {
  date: string;
  bot_id: string | null;
  tokens: number;
  calls: number;
}
export interface UsageBotRow {
  bot_id: string;
  profile_id: string;
  name: string | null;
  current: boolean;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  calls: number;
  sessions: number;
}
export interface UsageKindRow {
  kind: UsageKind;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  calls: number;
}
export interface UsageRouteRow {
  connection_id: string | null;
  connection_name: string | null;
  model: string;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  calls: number;
}
export interface UsageLargestRow {
  session_id: string;
  bot_id: string;
  kind: UsageSessionKind;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
  calls: number;
  first_at: string;
  last_at: string;
  transcript: boolean;
}
export interface UsageCoverage {
  sessions: number;
  log_only_sessions: number;
  log_only_calls: number;
  unlogged: number;
  pending: number;
  rows: number;
  oldest_at: string | null;
  max_rows: number;
  max_age_days: number;
}
export interface UsageSummary {
  workspace_id: string;
  days: UsageDays;
  dates: string[];
  totals: UsageTotals;
  daily: UsageDailyRow[];
  bots: UsageBotRow[];
  bots_omitted: number;
  kinds: UsageKindRow[];
  routes: UsageRouteRow[];
  routes_omitted: number;
  largest: UsageLargestRow[];
  coverage: UsageCoverage;
}
export interface UsageCallRow {
  at: string;
  prompt_id: string | null;
  model: string;
  agent: boolean;
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}
export interface UsageTrace {
  session_id: string;
  bot_id: string;
  kind: UsageSessionKind;
  connection_id: string | null;
  connection_name: string | null;
  transcript: boolean;
  /** Count of older calls not included in `calls`. */
  omitted: number;
  calls: UsageCallRow[];
}
export interface BotUsageApi {
  summary(days: UsageDays, signal?: AbortSignal): Promise<UsageSummary>;
  trace(sessionId: string, signal?: AbortSignal): Promise<UsageTrace>;
}
export type BotUsageErrorCode =
  | "unauthorized"
  | "forbidden_origin"
  | "invalid_input"
  | "overloaded"
  | "not_found"
  | "unavailable"
  | "corrupt"
  | "invalid_response"
  | "cancelled"
  | "intake_timeout"
  | "response_too_large";
export class BotUsageApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: BotUsageErrorCode,
  ) {
    super("The usage data could not be loaded.");
    this.name = "BotUsageApiError";
  }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const PROMPT_ID = /^[A-Za-z0-9_-]{1,64}$/;
const KINDS: readonly UsageKind[] = ["main", "task", "bot_thread", "routine", "subagent"];
const SESSION_KINDS: readonly UsageSessionKind[] = ["main", "task", "bot_thread", "routine"];
// Server-mappable codes only: "cancelled", "intake_timeout" and "response_too_large" are raised
// by this client itself (never parsed from a response body), and "invalid_response" likewise.
const ERRORS = new Set<BotUsageErrorCode>([
  "unauthorized",
  "forbidden_origin",
  "invalid_input",
  "overloaded",
  "not_found",
  "unavailable",
  "corrupt",
]);
const BOTS_MAX = 32;
const ROUTES_MAX = 20;
const LARGEST_MAX = 10;
const DAILY_ROWS_PER_DAY = 33;
const TRACE_CALLS_MAX = 2000;
const RESPONSE_MAX = 1 << 20; // 1 MiB
const TIMEOUT = 30_000; // 30 s

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function exact(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return (
    object(value) && Object.keys(value).length === keys.length && keys.every((key) => key in value)
  );
}
function uuid(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}
function nullableUuid(value: unknown): value is string | null {
  return value === null || uuid(value);
}
function count(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}
function boolStrict(value: unknown): value is boolean {
  return typeof value === "boolean";
}
function str(value: unknown): value is string {
  return typeof value === "string" && value.isWellFormed();
}
function nullableStr(value: unknown): value is string | null {
  return value === null || str(value);
}
function boundedStr(value: unknown, maximum: number): value is string {
  return str(value) && [...value].length <= maximum;
}
function promptId(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && PROMPT_ID.test(value));
}
function dateOnly(value: unknown): value is string {
  if (typeof value !== "string" || !DATE_ONLY.test(value)) return false;
  const time = Date.parse(`${value}T00:00:00.000Z`);
  return Number.isFinite(time) && new Date(time).toISOString() === `${value}T00:00:00.000Z`;
}
function stamp(value: unknown): value is string {
  if (typeof value !== "string" || !STAMP.test(value)) return false;
  const time = Date.parse(value);
  return Number.isFinite(time) && new Date(time).toISOString() === value;
}
function isDays(value: unknown): value is UsageDays {
  return value === 7 || value === 30 || value === 90;
}
function isKind(value: unknown): value is UsageKind {
  return typeof value === "string" && (KINDS as readonly string[]).includes(value);
}
function isSessionKind(value: unknown): value is UsageSessionKind {
  return typeof value === "string" && (SESSION_KINDS as readonly string[]).includes(value);
}
function datesArray(value: unknown, days: number): value is string[] {
  return Array.isArray(value) && value.length === days && value.every(dateOnly);
}
function totalsRow(value: unknown): value is UsageTotals {
  return (
    exact(value, ["input", "output", "cache_read", "cache_write", "calls"]) &&
    count(value.input) &&
    count(value.output) &&
    count(value.cache_read) &&
    count(value.cache_write) &&
    count(value.calls)
  );
}
function dailyRow(value: unknown): value is UsageDailyRow {
  return (
    exact(value, ["date", "bot_id", "tokens", "calls"]) &&
    dateOnly(value.date) &&
    nullableUuid(value.bot_id) &&
    count(value.tokens) &&
    count(value.calls)
  );
}
function dailyArray(
  value: unknown,
  dates: readonly string[],
  days: number,
): value is UsageDailyRow[] {
  return (
    Array.isArray(value) &&
    value.length <= days * DAILY_ROWS_PER_DAY &&
    value.every((row) => dailyRow(row) && dates.includes(row.date))
  );
}
function botRow(value: unknown): value is UsageBotRow {
  return (
    exact(value, [
      "bot_id",
      "profile_id",
      "name",
      "current",
      "input",
      "output",
      "cache_read",
      "cache_write",
      "calls",
      "sessions",
    ]) &&
    uuid(value.bot_id) &&
    uuid(value.profile_id) &&
    nullableStr(value.name) &&
    boolStrict(value.current) &&
    count(value.input) &&
    count(value.output) &&
    count(value.cache_read) &&
    count(value.cache_write) &&
    count(value.calls) &&
    count(value.sessions)
  );
}
function botsArray(value: unknown): value is UsageBotRow[] {
  return Array.isArray(value) && value.length <= BOTS_MAX && value.every(botRow);
}
function kindRow(value: unknown): value is UsageKindRow {
  return (
    exact(value, ["kind", "input", "output", "cache_read", "cache_write", "calls"]) &&
    isKind(value.kind) &&
    count(value.input) &&
    count(value.output) &&
    count(value.cache_read) &&
    count(value.cache_write) &&
    count(value.calls)
  );
}
function kindsArray(value: unknown): value is UsageKindRow[] {
  return (
    Array.isArray(value) &&
    value.length === KINDS.length &&
    value.every((row, index) => kindRow(row) && row.kind === KINDS[index])
  );
}
function routeRow(value: unknown): value is UsageRouteRow {
  return (
    exact(value, [
      "connection_id",
      "connection_name",
      "model",
      "input",
      "output",
      "cache_read",
      "cache_write",
      "calls",
    ]) &&
    nullableUuid(value.connection_id) &&
    nullableStr(value.connection_name) &&
    boundedStr(value.model, 256) &&
    count(value.input) &&
    count(value.output) &&
    count(value.cache_read) &&
    count(value.cache_write) &&
    count(value.calls)
  );
}
function routesArray(value: unknown): value is UsageRouteRow[] {
  return Array.isArray(value) && value.length <= ROUTES_MAX && value.every(routeRow);
}
function largestRow(value: unknown): value is UsageLargestRow {
  return (
    exact(value, [
      "session_id",
      "bot_id",
      "kind",
      "input",
      "output",
      "cache_read",
      "cache_write",
      "calls",
      "first_at",
      "last_at",
      "transcript",
    ]) &&
    uuid(value.session_id) &&
    uuid(value.bot_id) &&
    isSessionKind(value.kind) &&
    count(value.input) &&
    count(value.output) &&
    count(value.cache_read) &&
    count(value.cache_write) &&
    count(value.calls) &&
    stamp(value.first_at) &&
    stamp(value.last_at) &&
    boolStrict(value.transcript)
  );
}
function largestArray(value: unknown): value is UsageLargestRow[] {
  return Array.isArray(value) && value.length <= LARGEST_MAX && value.every(largestRow);
}
function coverageRow(value: unknown): value is UsageCoverage {
  return (
    exact(value, [
      "sessions",
      "log_only_sessions",
      "log_only_calls",
      "unlogged",
      "pending",
      "rows",
      "oldest_at",
      "max_rows",
      "max_age_days",
    ]) &&
    count(value.sessions) &&
    count(value.log_only_sessions) &&
    count(value.log_only_calls) &&
    count(value.unlogged) &&
    count(value.pending) &&
    count(value.rows) &&
    (value.oldest_at === null || stamp(value.oldest_at)) &&
    count(value.max_rows) &&
    count(value.max_age_days)
  );
}
function callRow(value: unknown): value is UsageCallRow {
  return (
    exact(value, [
      "at",
      "prompt_id",
      "model",
      "agent",
      "input",
      "output",
      "cache_read",
      "cache_write",
    ]) &&
    stamp(value.at) &&
    promptId(value.prompt_id) &&
    boundedStr(value.model, 256) &&
    boolStrict(value.agent) &&
    count(value.input) &&
    count(value.output) &&
    count(value.cache_read) &&
    count(value.cache_write)
  );
}
function callsArray(value: unknown): value is UsageCallRow[] {
  return Array.isArray(value) && value.length <= TRACE_CALLS_MAX && value.every(callRow);
}
function invalid(code: "invalid_input" | "invalid_response"): never {
  throw new BotUsageApiError(0, code);
}
function serverError(status: number, body: unknown): BotUsageApiError {
  const code = object(body) && object(body.error) ? body.error.code : null;
  return new BotUsageApiError(
    status,
    typeof code === "string" && ERRORS.has(code as BotUsageErrorCode)
      ? (code as BotUsageErrorCode)
      : "unavailable",
  );
}

export function parseSummary(value: unknown): UsageSummary {
  if (
    !exact(value, [
      "workspace_id",
      "days",
      "dates",
      "totals",
      "daily",
      "bots",
      "bots_omitted",
      "kinds",
      "routes",
      "routes_omitted",
      "largest",
      "coverage",
    ]) ||
    !uuid(value.workspace_id) ||
    !isDays(value.days) ||
    !datesArray(value.dates, value.days) ||
    !totalsRow(value.totals) ||
    !dailyArray(value.daily, value.dates, value.days) ||
    !botsArray(value.bots) ||
    !count(value.bots_omitted) ||
    !kindsArray(value.kinds) ||
    !routesArray(value.routes) ||
    !count(value.routes_omitted) ||
    !largestArray(value.largest) ||
    !coverageRow(value.coverage)
  )
    return invalid("invalid_response");
  return value as unknown as UsageSummary;
}
export function parseTrace(value: unknown): UsageTrace {
  if (
    !exact(value, [
      "session_id",
      "bot_id",
      "kind",
      "connection_id",
      "connection_name",
      "transcript",
      "omitted",
      "calls",
    ]) ||
    !uuid(value.session_id) ||
    !uuid(value.bot_id) ||
    !isSessionKind(value.kind) ||
    !nullableUuid(value.connection_id) ||
    !nullableStr(value.connection_name) ||
    !boolStrict(value.transcript) ||
    !count(value.omitted) ||
    !callsArray(value.calls)
  )
    return invalid("invalid_response");
  return value as unknown as UsageTrace;
}

export function createBotUsageApi(token: string, fetchImpl: typeof fetch = fetch): BotUsageApi {
  const base = "/api/v2/bot-usage";
  async function request(
    path: string,
    signal?: AbortSignal,
  ): Promise<{ status: number; body: unknown }> {
    if (signal?.aborted) throw new BotUsageApiError(0, "cancelled");
    const controller = new AbortController();
    const relay = (): void => controller.abort();
    signal?.addEventListener("abort", relay, { once: true });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, TIMEOUT);
    try {
      const response = await fetchImpl(path, {
        method: "GET",
        signal: controller.signal,
        headers: { authorization: `Bearer ${token}` },
      });
      let parsed: unknown;
      try {
        parsed = await boundedJson(response, RESPONSE_MAX);
      } catch (error) {
        if (controller.signal.aborted) throw error;
        if (error instanceof Error && error.message === "response_too_large")
          throw new BotUsageApiError(0, "response_too_large");
        if (!response.ok) throw serverError(response.status, null);
        return invalid("invalid_response");
      }
      if (!response.ok) throw serverError(response.status, parsed);
      return { status: response.status, body: parsed };
    } catch (error) {
      if (error instanceof BotUsageApiError) throw error;
      if (signal?.aborted) throw new BotUsageApiError(0, "cancelled");
      if (timedOut) throw new BotUsageApiError(408, "intake_timeout");
      throw new BotUsageApiError(0, "unavailable");
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", relay);
    }
  }
  return {
    async summary(days, signal) {
      if (!isDays(days)) return invalid("invalid_input");
      const { status, body } = await request(`${base}/summary/${days}`, signal);
      if (status !== 200) return invalid("invalid_response");
      const result = parseSummary(body);
      if (result.days !== days) return invalid("invalid_response");
      return result;
    },
    async trace(sessionId, signal) {
      if (!uuid(sessionId)) return invalid("invalid_input");
      const { status, body } = await request(`${base}/sessions/${sessionId}`, signal);
      if (status !== 200) return invalid("invalid_response");
      const result = parseTrace(body);
      if (result.session_id !== sessionId) return invalid("invalid_response");
      return result;
    },
  };
}
