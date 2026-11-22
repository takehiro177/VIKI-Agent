import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BotUsageApiError,
  createBotUsageApi,
  parseSummary,
  parseTrace,
  type UsageDays,
} from "./botUsageApi";

const WORKSPACE = "00000000-0000-4000-8000-00000000000a";
const BOT = "00000000-0000-4000-8000-00000000000b";
const PROFILE = "00000000-0000-4000-8000-00000000000c";
const SESSION = "00000000-0000-4000-8000-00000000000d";
const CONNECTION = "00000000-0000-4000-8000-00000000000e";
const OTHER = "00000000-0000-6000-c000-00000000000f";
const STAMP_A = "2026-11-20T10:00:00.000Z";
const STAMP_B = "2026-11-21T11:30:00.123Z";
const KIND_NAMES = ["main", "task", "bot_thread", "routine", "subagent"] as const;
const TOTALS = { input: 100, output: 200, cache_read: 10, cache_write: 5, calls: 30 };
const COVERAGE = {
  sessions: 5,
  log_only_sessions: 1,
  log_only_calls: 2,
  unlogged: 0,
  pending: 0,
  rows: 100,
  oldest_at: STAMP_A,
  max_rows: 100_000,
  max_age_days: 90,
};

function dates(days: number): string[] {
  return Array.from({ length: days }, (_, index) =>
    new Date(Date.UTC(2026, 10, 21 - (days - 1 - index))).toISOString().slice(0, 10),
  );
}
function today(days: number): string {
  return dates(days)[days - 1] as string;
}
function omit(value: Record<string, unknown>, key: string): Record<string, unknown> {
  const clone = { ...value };
  delete clone[key];
  return clone;
}
function botRow(overrides: Record<string, unknown> = {}) {
  return {
    bot_id: BOT,
    profile_id: PROFILE,
    name: "Planner",
    current: true,
    input: 50,
    output: 60,
    cache_read: 1,
    cache_write: 1,
    calls: 12,
    sessions: 3,
    ...overrides,
  };
}
function kindsRows() {
  return KIND_NAMES.map((kind) => ({
    kind,
    input: 10,
    output: 10,
    cache_read: 0,
    cache_write: 0,
    calls: 1,
  }));
}
function routeRow(overrides: Record<string, unknown> = {}) {
  return {
    connection_id: CONNECTION,
    connection_name: "OpenAI",
    model: "gpt-5.1",
    input: 20,
    output: 30,
    cache_read: 0,
    cache_write: 0,
    calls: 5,
    ...overrides,
  };
}
function largestRow(overrides: Record<string, unknown> = {}) {
  return {
    session_id: SESSION,
    bot_id: BOT,
    kind: "main",
    input: 70,
    output: 80,
    cache_read: 2,
    cache_write: 2,
    calls: 9,
    first_at: STAMP_A,
    last_at: STAMP_B,
    transcript: true,
    ...overrides,
  };
}
function summary(days: UsageDays, overrides: Record<string, unknown> = {}) {
  const ds = dates(days);
  return {
    workspace_id: WORKSPACE,
    days,
    dates: ds,
    totals: TOTALS,
    daily: [
      { date: ds[ds.length - 1], bot_id: BOT, tokens: 40, calls: 4 },
      { date: ds[ds.length - 1], bot_id: null, tokens: 5, calls: 1 },
    ],
    bots: [botRow()],
    bots_omitted: 0,
    kinds: kindsRows(),
    routes: [routeRow()],
    routes_omitted: 0,
    largest: [largestRow()],
    coverage: COVERAGE,
    ...overrides,
  };
}
function callRow(overrides: Record<string, unknown> = {}) {
  return {
    at: STAMP_A,
    prompt_id: "abc_123-XYZ",
    model: "claude-sonnet-5",
    agent: false,
    input: 10,
    output: 20,
    cache_read: 0,
    cache_write: 0,
    ...overrides,
  };
}
function trace(overrides: Record<string, unknown> = {}) {
  return {
    session_id: SESSION,
    bot_id: BOT,
    kind: "main",
    connection_id: CONNECTION,
    connection_name: "OpenAI",
    transcript: true,
    omitted: 0,
    calls: [callRow(), callRow({ at: STAMP_B, prompt_id: null })],
    ...overrides,
  };
}
const ok = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
function expectInvalidResponse(run: () => unknown): void {
  expect(run).toThrow(BotUsageApiError);
  try {
    run();
    expect.unreachable("expected run() to throw");
  } catch (error) {
    expect(error).toMatchObject({ code: "invalid_response" });
  }
}

afterEach(() => vi.useRealTimers());

describe("Bot usage API", () => {
  it("reads the exact summary URL and headers and parses a valid answer", async () => {
    const body = summary(7);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok(body));
    const api = createBotUsageApi("secret", fetcher);
    expect(await api.summary(7)).toEqual(body);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v2/bot-usage/summary/7");
    expect(fetcher.mock.calls[0]?.[1]?.method).toBe("GET");
    expect(fetcher.mock.calls[0]?.[1]?.headers).toEqual({ authorization: "Bearer secret" });
    expect(fetcher.mock.calls[0]?.[1]?.body).toBeUndefined();
  });

  it.each([7, 30, 90] as const)("builds the exact summary URL for %i days", async (days) => {
    const body = summary(days);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok(body));
    const api = createBotUsageApi("secret", fetcher);
    expect(await api.summary(days)).toEqual(body);
    expect(fetcher.mock.calls[0]?.[0]).toBe(`/api/v2/bot-usage/summary/${days}`);
  });

  it("reads the exact trace URL and headers and parses a valid answer", async () => {
    const body = trace();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok(body));
    const api = createBotUsageApi("secret", fetcher);
    expect(await api.trace(SESSION)).toEqual(body);
    expect(fetcher.mock.calls[0]?.[0]).toBe(`/api/v2/bot-usage/sessions/${SESSION}`);
    expect(fetcher.mock.calls[0]?.[1]?.method).toBe("GET");
    expect(fetcher.mock.calls[0]?.[1]?.headers).toEqual({ authorization: "Bearer secret" });
  });

  it("parseSummary and parseTrace return a valid payload unchanged", () => {
    const s = summary(30);
    expect(parseSummary(s)).toEqual(s);
    const t = trace();
    expect(parseTrace(t)).toEqual(t);
  });

  it("accepts values at the exact caps and boundary lengths", () => {
    const routes20 = Array.from({ length: 20 }, (_, index) =>
      routeRow(index === 0 ? { model: "m".repeat(256) } : {}),
    );
    const s = summary(7, {
      bots: Array.from({ length: 32 }, () => botRow()),
      routes: routes20,
      largest: Array.from({ length: 10 }, () => largestRow()),
      daily: Array.from({ length: 7 * 33 }, () => ({
        date: today(7),
        bot_id: null,
        tokens: 1,
        calls: 1,
      })),
    });
    expect(parseSummary(s)).toEqual(s);
    const t = trace({
      omitted: 37,
      calls: Array.from({ length: 2000 }, (_, index) =>
        callRow(index === 0 ? { prompt_id: "p".repeat(64) } : {}),
      ),
    });
    expect(parseTrace(t)).toEqual(t);
  });

  it.each(["main", "task", "bot_thread", "routine"] as const)(
    "accepts %s as a trace session kind",
    (kind) => {
      const t = trace({ kind });
      expect(parseTrace(t)).toEqual(t);
    },
  );

  it.each([
    { ...summary(7), extra: true },
    omit(summary(7), "bots_omitted"),
    { ...summary(7), workspace_id: WORKSPACE.toUpperCase() },
    { ...summary(7), days: 14 },
    { ...summary(7), dates: dates(6) },
    { ...summary(7), dates: [...dates(6), "2026-13-01"] },
    { ...summary(7), daily: [{ date: "2099-01-01", bot_id: null, tokens: 1, calls: 1 }] },
    {
      ...summary(7),
      daily: Array.from({ length: 7 * 33 + 1 }, () => ({
        date: today(7),
        bot_id: null,
        tokens: 1,
        calls: 1,
      })),
    },
    { ...summary(7), bots: Array.from({ length: 33 }, () => botRow()) },
    { ...summary(7), routes: Array.from({ length: 21 }, () => routeRow()) },
    { ...summary(7), largest: Array.from({ length: 11 }, () => largestRow()) },
    { ...summary(7), bots: [botRow({ current: "true" })] },
    { ...summary(7), bots: [botRow({ name: 7 })] },
    { ...summary(7), bots: [botRow({ bot_id: BOT.toUpperCase() })] },
    { ...summary(7), kinds: [...kindsRows()].reverse() },
    { ...summary(7), kinds: kindsRows().slice(0, 4) },
    { ...summary(7), routes: [routeRow({ model: "a".repeat(257) })] },
    { ...summary(7), routes: [routeRow({ connection_id: "bad" })] },
    { ...summary(7), largest: [largestRow({ first_at: "2026-02-30T00:00:00.000Z" })] },
    { ...summary(7), largest: [largestRow({ transcript: 1 })] },
    { ...summary(7), largest: [largestRow({ kind: "subagent" })] },
    { ...summary(7), coverage: { ...COVERAGE, oldest_at: "not-a-stamp" } },
    { ...summary(7), coverage: omit(COVERAGE, "pending") },
    { ...summary(7), bots_omitted: -1 },
    { ...summary(7), routes_omitted: 1.5 },
    { ...summary(7), totals: { ...TOTALS, calls: Number.MAX_SAFE_INTEGER + 1 } },
  ])("rejects an off-contract summary answer %#", (body) => {
    expectInvalidResponse(() => parseSummary(body));
  });

  it.each([
    { ...trace(), extra: true },
    omit(trace(), "omitted"),
    { ...trace(), session_id: SESSION.toUpperCase() },
    { ...trace(), bot_id: "not-a-uuid" },
    { ...trace(), kind: "unknown" },
    { ...trace(), kind: "subagent" },
    { ...trace(), connection_id: "bad" },
    { ...trace(), connection_name: 7 },
    { ...trace(), transcript: 1 },
    { ...trace(), omitted: true },
    { ...trace(), omitted: -1 },
    { ...trace(), omitted: 1.5 },
    { ...trace(), omitted: "0" },
    { ...trace(), calls: Array.from({ length: 2001 }, () => callRow()) },
    { ...trace(), calls: [callRow({ prompt_id: "" })] },
    { ...trace(), calls: [callRow({ prompt_id: "has a space" })] },
    { ...trace(), calls: [callRow({ prompt_id: "x".repeat(65) })] },
    { ...trace(), calls: [callRow({ model: "a".repeat(257) })] },
    { ...trace(), calls: [callRow({ agent: "no" })] },
    { ...trace(), calls: [callRow({ at: "2026-11-20T10:00:00.00Z" })] },
    { ...trace(), calls: [callRow({ at: "2026-02-30T00:00:00.000Z" })] },
    { ...trace(), calls: [{ ...callRow(), extra: true }] },
  ])("rejects an off-contract trace answer %#", (body) => {
    expectInvalidResponse(() => parseTrace(body));
  });

  it.each([
    [401, "unauthorized"],
    [403, "forbidden_origin"],
    [400, "invalid_input"],
    [429, "overloaded"],
    [404, "not_found"],
    [500, "unavailable"],
    [422, "corrupt"],
  ] as const)("keeps the fixed server error code %i %s", async (status, code) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(ok({ error: { code, message: "private" } }, status));
    await expect(createBotUsageApi("secret", fetcher).summary(7)).rejects.toMatchObject({
      status,
      code,
    });
  });

  it("folds an unlisted or missing server code and a network rejection into unavailable", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok({ error: { code: "weird_but_unlisted" }, message: "private" }, 503))
      .mockResolvedValueOnce(ok({ oops: true }, 500))
      .mockRejectedValueOnce(new Error("private"))
      .mockRejectedValueOnce(new Error("private again"));
    const api = createBotUsageApi("secret", fetcher);
    await expect(api.summary(7)).rejects.toMatchObject({ status: 503, code: "unavailable" });
    await expect(api.summary(7)).rejects.toMatchObject({ status: 500, code: "unavailable" });
    await expect(api.summary(7)).rejects.toMatchObject({
      code: "unavailable",
      message: "The usage data could not be loaded.",
    });
    await expect(api.summary(7)).rejects.toBeInstanceOf(BotUsageApiError);
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("rejects a mismatched days or session_id echoed back", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(ok(summary(30)))
      .mockResolvedValueOnce(ok(trace({ session_id: OTHER })));
    const api = createBotUsageApi("secret", fetcher);
    await expect(api.summary(7)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.trace(SESSION)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects a non-200 status on an otherwise well-formed body", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(ok(summary(7), 201));
    const api = createBotUsageApi("secret", fetcher);
    await expect(api.summary(7)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects invalid input before fetch", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const api = createBotUsageApi("secret", fetcher);
    await expect(api.summary(14 as unknown as UsageDays)).rejects.toMatchObject({
      code: "invalid_input",
    });
    await expect(api.trace("not-a-uuid")).rejects.toMatchObject({ code: "invalid_input" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("bounds the raw response to 1 MiB", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(" ".repeat((1 << 20) + 1)));
    const api = createBotUsageApi("secret", fetcher);
    await expect(api.summary(7)).rejects.toMatchObject({ code: "response_too_large" });
  });

  it("rejects invalid JSON or UTF-8 without leaking its body", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("private"))
      .mockResolvedValueOnce(new Response(new Uint8Array([0xff])));
    const api = createBotUsageApi("secret", fetcher);
    await expect(api.summary(7)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(api.summary(7)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("distinguishes caller abort from the 30 s deadline and cleans its timers", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      (_path, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new Error("aborted")), {
            once: true,
          });
        }),
    );
    const api = createBotUsageApi("secret", fetcher);
    const controller = new AbortController();
    const caller = expect(api.summary(7, controller.signal)).rejects.toMatchObject({
      code: "cancelled",
    });
    controller.abort();
    await caller;
    const deadline = expect(api.summary(7)).rejects.toMatchObject({
      status: 408,
      code: "intake_timeout",
    });
    await vi.advanceTimersByTimeAsync(30_000);
    await deadline;
    expect(vi.getTimerCount()).toBe(0);
    const preAborted = new AbortController();
    preAborted.abort();
    await expect(api.summary(7, preAborted.signal)).rejects.toMatchObject({ code: "cancelled" });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
