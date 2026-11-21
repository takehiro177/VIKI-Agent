/**
 * Tests for `BotUsage`, the dashboard container. The fake `BotUsageApi` returns manually-settled
 * promises so each test controls exactly when a `summary()`/`trace()` call answers — the same
 * deferred-promise pattern `Attachments.unit.test.tsx` and `MessagingView.unit.test.tsx` use to
 * test a superseded call.
 */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type {
  BotUsageApi,
  UsageDays,
  UsageSummary,
  UsageTrace as UsageTraceData,
} from "../../../clients/messaging/botUsageApi";
import { BotUsage } from "./BotUsage";

afterEach(() => {
  cleanup();
});

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function defer<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function fakeApi() {
  const summaryCalls: UsageDays[] = [];
  const summaryDeferreds: Deferred<UsageSummary>[] = [];
  const traceCalls: string[] = [];
  const traceDeferreds: Deferred<UsageTraceData>[] = [];
  const api: BotUsageApi = {
    summary(days) {
      summaryCalls.push(days);
      const one = defer<UsageSummary>();
      summaryDeferreds.push(one);
      return one.promise;
    },
    trace(sessionId) {
      traceCalls.push(sessionId);
      const one = defer<UsageTraceData>();
      traceDeferreds.push(one);
      return one.promise;
    },
  };
  return { api, summaryCalls, summaryDeferreds, traceCalls, traceDeferreds };
}

/** One Bot ("Research Bot"), one largest conversation ("session-1"); every token field traces
 * back to `totalInput` so each fixture's own total is easy to tell apart on screen. */
function summaryFixture(days: UsageDays, totalInput: number): UsageSummary {
  return {
    workspace_id: "workspace-1",
    days,
    dates: ["2026-11-19", "2026-11-20", "2026-11-21"],
    totals: { input: totalInput, output: 0, cache_read: 0, cache_write: 0, calls: 10 },
    daily: [{ date: "2026-11-19", bot_id: "bot-a", tokens: totalInput, calls: 10 }],
    bots: [
      {
        bot_id: "bot-a",
        profile_id: "profile-a",
        name: "Research Bot",
        current: true,
        input: totalInput,
        output: 0,
        cache_read: 0,
        cache_write: 0,
        calls: 10,
        sessions: 1,
      },
    ],
    bots_omitted: 0,
    kinds: [
      { kind: "main", input: totalInput, output: 0, cache_read: 0, cache_write: 0, calls: 10 },
      { kind: "task", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
      { kind: "bot_thread", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
      { kind: "routine", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
      { kind: "subagent", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
    ],
    routes: [
      {
        connection_id: null,
        connection_name: null,
        model: "claude-sonnet-5",
        input: totalInput,
        output: 0,
        cache_read: 0,
        cache_write: 0,
        calls: 10,
      },
    ],
    routes_omitted: 0,
    largest: [
      {
        session_id: "session-1",
        bot_id: "bot-a",
        kind: "main",
        input: totalInput,
        output: 0,
        cache_read: 0,
        cache_write: 0,
        calls: 10,
        first_at: new Date(2026, 10, 19, 8, 0, 0).toISOString(),
        last_at: new Date(2026, 10, 21, 9, 0, 0).toISOString(),
        transcript: true,
      },
    ],
    coverage: {
      sessions: 1,
      log_only_sessions: 0,
      log_only_calls: 0,
      unlogged: 0,
      pending: 0,
      rows: 10,
      oldest_at: null,
      max_rows: 5000,
      max_age_days: 30,
    },
  };
}

function heroValue(): string | null {
  return document.querySelector(".bot-usage-hero-value")?.textContent ?? null;
}

function bodyBusy(): string | null {
  return document.querySelector(".bot-usage-body")?.getAttribute("aria-busy") ?? null;
}

describe("BotUsage", () => {
  test("asks for 30 days first, shows Loading, then every section once it lands", async () => {
    const { api, summaryCalls, summaryDeferreds } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);

    expect(summaryCalls).toEqual([30]);
    expect(screen.getByText("Loading usage…")).toBeTruthy();
    expect(screen.getByRole("group", { name: "Period" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Last 30 days" }).getAttribute("aria-pressed")).toBe(
      "true",
    );

    summaryDeferreds[0]?.resolve(summaryFixture(30, 900));
    await flush();

    expect(screen.queryByText("Loading usage…")).toBeNull();
    expect(heroValue()).toBe("900");
    for (const name of [
      "Daily tokens",
      "By Bot",
      "Bots",
      "Models and routes",
      "Conversation kinds",
      "Largest conversations",
      "Measured facts",
      "About these numbers",
    ]) {
      expect(screen.getByRole("heading", { name })).toBeTruthy();
    }
  });

  test("focuses the heading on mount", () => {
    const { api } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { name: "Bot usage", level: 1 }),
    );
  });

  test("switching period keeps the old numbers, aria-busy, until the new answer lands", async () => {
    const { api, summaryCalls, summaryDeferreds } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);
    summaryDeferreds[0]?.resolve(summaryFixture(30, 900));
    await flush();
    expect(heroValue()).toBe("900");
    expect(bodyBusy()).toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    expect(summaryCalls).toEqual([30, 7]);
    expect(heroValue()).toBe("900");
    expect(bodyBusy()).toBe("true");
    expect(screen.getByRole("button", { name: "Last 7 days" }).getAttribute("aria-pressed")).toBe(
      "true",
    );

    summaryDeferreds[1]?.resolve(summaryFixture(7, 1500));
    await flush();
    expect(heroValue()).toBe("1.5K");
    expect(bodyBusy()).toBe("false");
  });

  test("a stale answer that resolves after being superseded is ignored", async () => {
    const { api, summaryDeferreds } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);
    summaryDeferreds[0]?.resolve(summaryFixture(30, 900));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    fireEvent.click(screen.getByRole("button", { name: "Last 90 days" }));

    // The stale (days=7) answer lands after it was superseded by the days=90 request.
    summaryDeferreds[1]?.resolve(summaryFixture(7, 1500));
    await flush();
    expect(heroValue()).toBe("900");

    summaryDeferreds[2]?.resolve(summaryFixture(90, 2500));
    await flush();
    expect(heroValue()).toBe("2.5K");
  });

  test("Refresh re-asks for the current period", async () => {
    const { api, summaryCalls, summaryDeferreds } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);
    summaryDeferreds[0]?.resolve(summaryFixture(30, 900));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(summaryCalls).toEqual([30, 30]);
    expect(bodyBusy()).toBe("true");

    summaryDeferreds[1]?.resolve(summaryFixture(30, 950));
    await flush();
    expect(heroValue()).toBe("950");
  });

  test("a failed load shows the alert; Refresh retries", async () => {
    const { api, summaryCalls, summaryDeferreds } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);

    summaryDeferreds[0]?.reject(new Error("boom"));
    await flush();
    expect(screen.getByRole("alert").textContent).toBe(
      "Usage could not be loaded. Refresh to try again.",
    );

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(summaryCalls).toEqual([30, 30]);

    summaryDeferreds[1]?.resolve(summaryFixture(30, 900));
    await flush();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("heading", { name: "Daily tokens" })).toBeTruthy();
  });

  test("a reload that fails after a success shows the alert, not the stale 30-day answer (F22)", async () => {
    const { api, summaryCalls, summaryDeferreds } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);
    summaryDeferreds[0]?.resolve(summaryFixture(30, 900));
    await flush();
    expect(heroValue()).toBe("900");

    fireEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    expect(summaryCalls).toEqual([30, 7]);
    summaryDeferreds[1]?.reject(new Error("boom"));
    await flush();

    expect(screen.getByRole("alert").textContent).toBe(
      "Usage could not be loaded. Refresh to try again.",
    );
    expect(document.querySelector(".bot-usage-body")).toBeNull();
    expect(screen.getByRole("button", { name: "Last 7 days" }).getAttribute("aria-pressed")).toBe(
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(summaryCalls).toEqual([30, 7, 7]);
    // Nothing is on screen for this attempt, so it is a first-load-style wait, not a dimmed body.
    expect(screen.getByText("Loading usage…")).toBeTruthy();
    expect(document.querySelector(".bot-usage-body")).toBeNull();

    summaryDeferreds[2]?.resolve(summaryFixture(7, 1500));
    await flush();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(heroValue()).toBe("1.5K");
  });

  test("Open calls onOpenSession with the session id", async () => {
    const { api, summaryDeferreds } = fakeApi();
    const onOpenSession = vi.fn();
    render(<BotUsage api={api} onOpenSession={onOpenSession} />);
    summaryDeferreds[0]?.resolve(summaryFixture(30, 900));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Open Research Bot" }));
    expect(onOpenSession).toHaveBeenCalledWith("session-1");
  });

  test("Trace loads the session trace below the tables; Close returns focus to the Trace button", async () => {
    const { api, summaryDeferreds, traceCalls, traceDeferreds } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);
    summaryDeferreds[0]?.resolve(summaryFixture(30, 900));
    await flush();

    const traceButton = screen.getByRole("button", { name: "Trace Research Bot" });
    traceButton.focus();
    fireEvent.click(traceButton);
    expect(traceCalls).toEqual(["session-1"]);
    expect(screen.queryByRole("heading", { name: "Conversation trace" })).toBeNull();

    traceDeferreds[0]?.resolve({
      session_id: "session-1",
      bot_id: "bot-a",
      kind: "main",
      connection_id: null,
      connection_name: null,
      transcript: true,
      omitted: 0,
      calls: [],
    });
    await flush();

    expect(screen.getByRole("heading", { name: "Conversation trace" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("heading", { name: "Conversation trace" })).toBeNull();
    expect(document.activeElement).toBe(traceButton);
  });

  test("By Bot's Show table reveals one combined table (All Bots plus each panel)", async () => {
    const { api, summaryDeferreds } = fakeApi();
    render(<BotUsage api={api} onOpenSession={() => {}} />);
    summaryDeferreds[0]?.resolve(summaryFixture(30, 900));
    await flush();

    const showTable = screen.getByRole("button", { name: "Show table" });
    expect(showTable.getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector("table.bot-usage-columns-table")).toBeNull();

    fireEvent.click(showTable);
    expect(showTable.getAttribute("aria-expanded")).toBe("true");
    const dailyTable = document.querySelector("table.bot-usage-columns-table") as HTMLTableElement;
    expect(
      within(dailyTable)
        .getAllByRole("columnheader")
        .map((node) => node.textContent),
    ).toEqual(["Date", "All Bots", "Research Bot"]);
  });

  test("By Bot caps at the top 7 Bots and folds rank 8+ plus null rows into Other Bots", async () => {
    const { api, summaryDeferreds } = fakeApi();
    // 8 named Bots (summary.bots is already ordered by tokens desc) plus a null-bot_id daily
    // row: Bot H (rank 8) and the null row both fold into Other Bots; Bots A-G keep their own
    // panel.
    const names = ["A", "B", "C", "D", "E", "F", "G", "H"];
    const summary: UsageSummary = {
      workspace_id: "workspace-1",
      days: 30,
      dates: ["2026-11-19"],
      totals: { input: 900, output: 0, cache_read: 0, cache_write: 0, calls: 18 },
      daily: [
        ...names.map((name, index) => ({
          date: "2026-11-19",
          bot_id: `bot-${name}`,
          tokens: 100 - index,
          calls: 1,
        })),
        { date: "2026-11-19", bot_id: null, tokens: 5, calls: 1 },
      ],
      bots: names.map((name, index) => ({
        bot_id: `bot-${name}`,
        profile_id: `profile-${name}`,
        name: `Bot ${name}`,
        current: true,
        input: 100 - index,
        output: 0,
        cache_read: 0,
        cache_write: 0,
        calls: 1,
        sessions: 1,
      })),
      bots_omitted: 0,
      kinds: [
        { kind: "main", input: 900, output: 0, cache_read: 0, cache_write: 0, calls: 18 },
        { kind: "task", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
        { kind: "bot_thread", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
        { kind: "routine", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
        { kind: "subagent", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
      ],
      routes: [],
      routes_omitted: 0,
      largest: [],
      coverage: {
        sessions: 8,
        log_only_sessions: 0,
        log_only_calls: 0,
        unlogged: 0,
        pending: 0,
        rows: 18,
        oldest_at: null,
        max_rows: 5000,
        max_age_days: 30,
      },
    };
    render(<BotUsage api={api} onOpenSession={() => {}} />);
    summaryDeferreds[0]?.resolve(summary);
    await flush();

    const grid = document.querySelector(".bot-usage-bybot-grid") as HTMLElement;
    const captions = within(grid)
      .getAllByText(/^Bot [A-H]$|^Other Bots$/)
      .map((node) => node.textContent);
    expect(captions).toEqual([
      "Bot A",
      "Bot B",
      "Bot C",
      "Bot D",
      "Bot E",
      "Bot F",
      "Bot G",
      "Other Bots",
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Show table" }));
    const dailyTable = document.querySelector("table.bot-usage-columns-table") as HTMLTableElement;
    expect(
      within(dailyTable)
        .getAllByRole("columnheader")
        .map((node) => node.textContent),
    ).toEqual([
      "Date",
      "All Bots",
      "Bot A",
      "Bot B",
      "Bot C",
      "Bot D",
      "Bot E",
      "Bot F",
      "Bot G",
      "Other Bots",
    ]);
    // Other Bots = Bot H (93 tokens) + the null row (5 tokens): the day's one body row ends
    // with the Other Bots column's value, 98.
    const dayRow = within(dailyTable).getByRole("rowheader", { name: "Nov 19" }).closest("tr");
    expect(dayRow?.textContent?.endsWith("98")).toBe(true);
  });
});
