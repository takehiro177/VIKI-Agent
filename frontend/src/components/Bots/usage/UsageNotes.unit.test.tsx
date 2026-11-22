import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import type {
  UsageCoverage,
  UsageKindRow,
  UsageLargestRow,
  UsageSummary,
  UsageTotals,
} from "../../../clients/messaging/botUsageApi";
import { UsageNotes } from "./UsageNotes";

afterEach(() => {
  cleanup();
});

const BOT_ID = "11111111-1111-1111-1111-111111111111";
const SESSION_ID = "22222222-2222-2222-2222-222222222222";
const STAMP = "2026-11-10T08:00:00.000Z";

const ZERO_TOTALS: UsageTotals = { input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 };
const ZERO_COVERAGE: UsageCoverage = {
  sessions: 0,
  log_only_sessions: 0,
  log_only_calls: 0,
  unlogged: 0,
  pending: 0,
  rows: 0,
  oldest_at: null,
  max_rows: 200_000,
  max_age_days: 30,
};

function kindRow(kind: UsageKindRow["kind"], tokens: number, calls: number): UsageKindRow {
  return { kind, input: tokens, output: 0, cache_read: 0, cache_write: 0, calls };
}

function largestRow(tokens: number): UsageLargestRow {
  return {
    session_id: SESSION_ID,
    bot_id: BOT_ID,
    kind: "main",
    input: tokens,
    output: 0,
    cache_read: 0,
    cache_write: 0,
    calls: 1,
    first_at: STAMP,
    last_at: STAMP,
    transcript: true,
  };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** The expected "Mon D" for a timestamp, read off the same `Date`'s own local fields rather than
 * through any `Intl` call, so the test holds under whatever zone it runs in (F23). */
function localCalendarDay(timestamp: string): string {
  const date = new Date(timestamp);
  return `${MONTHS[date.getMonth()]} ${date.getDate()}`;
}

function summary(overrides: Partial<UsageSummary> = {}): UsageSummary {
  return {
    workspace_id: BOT_ID,
    days: 7,
    dates: [],
    totals: ZERO_TOTALS,
    daily: [],
    bots: [],
    bots_omitted: 0,
    kinds: [],
    routes: [],
    routes_omitted: 0,
    largest: [],
    coverage: ZERO_COVERAGE,
    ...overrides,
  };
}

describe("UsageNotes", () => {
  test("shows every measured fact when every denominator is positive", () => {
    render(
      <UsageNotes
        summary={summary({
          totals: { input: 300, output: 500, cache_read: 100, cache_write: 100, calls: 10 },
          kinds: [
            kindRow("main", 600, 5),
            kindRow("task", 0, 0),
            kindRow("bot_thread", 0, 0),
            kindRow("routine", 300, 3),
            kindRow("subagent", 100, 2),
          ],
          largest: [largestRow(250)],
        })}
      />,
    );
    expect(screen.getByRole("heading", { name: "Measured facts" })).toBeTruthy();
    expect(screen.getByText("Cache reads were 20% of input-side tokens.")).toBeTruthy();
    expect(screen.getByText("An average call carried 50 tokens of context.")).toBeTruthy();
    expect(screen.getByText("Routine runs used 30% of all tokens.")).toBeTruthy();
    expect(screen.getByText("Subagents used 10% of all tokens.")).toBeTruthy();
    expect(screen.getByText("The largest conversation used 25% of all tokens.")).toBeTruthy();
  });

  test("hides the cache-read share when there are no input-side tokens", () => {
    render(
      <UsageNotes
        summary={summary({
          totals: { input: 0, output: 500, cache_read: 0, cache_write: 0, calls: 10 },
          kinds: [kindRow("routine", 100, 1)],
        })}
      />,
    );
    expect(screen.queryByText(/Cache reads were/)).toBeNull();
    expect(screen.getByText(/Routine runs used/)).toBeTruthy();
  });

  test("hides the average-context fact when there are no calls", () => {
    render(
      <UsageNotes
        summary={summary({
          totals: { input: 200, output: 0, cache_read: 50, cache_write: 50, calls: 0 },
        })}
      />,
    );
    expect(screen.queryByText(/tokens of context/)).toBeNull();
    expect(screen.getByText(/Cache reads were/)).toBeTruthy();
  });

  test("hides every total-based fact when there is no usage at all", () => {
    render(<UsageNotes summary={summary()} />);
    expect(screen.queryByText(/Cache reads were/)).toBeNull();
    expect(screen.queryByText(/tokens of context/)).toBeNull();
    expect(screen.queryByText(/Routine runs used/)).toBeNull();
    expect(screen.queryByText(/Subagents used/)).toBeNull();
    expect(screen.queryByText(/largest conversation/)).toBeNull();
  });

  test("hides the largest-conversation fact when there is no largest-conversation row", () => {
    render(
      <UsageNotes
        summary={summary({
          totals: { input: 400, output: 0, cache_read: 0, cache_write: 0, calls: 4 },
          kinds: [kindRow("routine", 400, 4)],
          largest: [],
        })}
      />,
    );
    expect(screen.getByText(/Routine runs used/)).toBeTruthy();
    expect(screen.queryByText(/largest conversation/)).toBeNull();
  });

  test("always shows the fixed coverage notes, and hides every conditional line at zero", () => {
    render(<UsageNotes summary={summary()} />);
    expect(screen.getByRole("heading", { name: "About these numbers" })).toBeTruthy();
    expect(
      screen.getByText(
        "Counted from saved Claude Code conversations of this vault's Bots and kept in VIKI Agent's log for up to 30 days or 200,000 calls.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Claude Code removes saved conversations after 30 days unless cleanupPeriodDays is set.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Model connections that use Chat Completions or Responses report input and output tokens only.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/are only in the log/)).toBeNull();
    expect(screen.queryByText(/no saved conversation and no log rows/)).toBeNull();
    expect(screen.queryByText(/still being read/)).toBeNull();
    expect(screen.queryByText(/The log holds/)).toBeNull();
  });

  test("shows the removed-transcripts note, singular and plural", () => {
    const { rerender } = render(
      <UsageNotes
        summary={summary({
          coverage: { ...ZERO_COVERAGE, log_only_sessions: 1, log_only_calls: 4 },
        })}
      />,
    );
    expect(
      screen.getByText(
        "1 conversation (4 calls) is only in the log: its saved conversation was removed.",
      ),
    ).toBeTruthy();

    rerender(
      <UsageNotes
        summary={summary({
          coverage: { ...ZERO_COVERAGE, log_only_sessions: 3, log_only_calls: 9 },
        })}
      />,
    );
    expect(
      screen.getByText(
        "3 conversations (9 calls) are only in the log: their saved conversation was removed.",
      ),
    ).toBeTruthy();
  });

  test("shows the no-saved-conversation note, singular and plural", () => {
    const { rerender } = render(
      <UsageNotes summary={summary({ coverage: { ...ZERO_COVERAGE, unlogged: 1 } })} />,
    );
    expect(
      screen.getByText("1 started conversation has no saved conversation and no log rows."),
    ).toBeTruthy();

    rerender(<UsageNotes summary={summary({ coverage: { ...ZERO_COVERAGE, unlogged: 5 } })} />);
    expect(
      screen.getByText("5 started conversations have no saved conversation and no log rows."),
    ).toBeTruthy();
  });

  test("shows the still-reading note, singular and plural", () => {
    const { rerender } = render(
      <UsageNotes summary={summary({ coverage: { ...ZERO_COVERAGE, pending: 1 } })} />,
    );
    expect(
      screen.getByText("1 conversation is still being read. Refresh to continue."),
    ).toBeTruthy();

    rerender(<UsageNotes summary={summary({ coverage: { ...ZERO_COVERAGE, pending: 2 } })} />);
    expect(
      screen.getByText("2 conversations are still being read. Refresh to continue."),
    ).toBeTruthy();
  });

  test("shows the log's row count and oldest date only once the log holds rows", () => {
    // Near a UTC day boundary on purpose (F23): on this machine (Asia/Tokyo, UTC+9) this instant
    // is 2026-11-21 05:00 local, a day later than its UTC date — the case a UTC-pinned read
    // (dayLabel) gets wrong. The expected day is read off the same instant's own local fields, so
    // this still holds under whatever zone the suite runs in.
    const oldestAt = "2026-11-20T20:00:00.000Z";
    render(
      <UsageNotes
        summary={summary({
          coverage: { ...ZERO_COVERAGE, rows: 1234, oldest_at: oldestAt },
        })}
      />,
    );
    expect(
      screen.getByText(`The log holds 1,234 calls since ${localCalendarDay(oldestAt)}.`),
    ).toBeTruthy();
  });
});
