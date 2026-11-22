import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type { UsageSummary } from "../../../clients/messaging/botUsageApi";
import { UsageTables } from "./UsageTables";

afterEach(() => {
  cleanup();
});

function summary(overrides: Partial<UsageSummary> = {}): UsageSummary {
  return {
    workspace_id: "workspace-1",
    days: 7,
    dates: [
      "2026-11-15",
      "2026-11-16",
      "2026-11-17",
      "2026-11-18",
      "2026-11-19",
      "2026-11-20",
      "2026-11-21",
    ],
    totals: { input: 1000, output: 0, cache_read: 0, cache_write: 0, calls: 50 },
    daily: [],
    bots: [
      {
        bot_id: "bot-a",
        profile_id: "profile-a",
        name: "Research Bot",
        current: true,
        input: 400,
        output: 200,
        cache_read: 50,
        cache_write: 20,
        calls: 12,
        sessions: 5,
      },
      {
        bot_id: "bot-b",
        profile_id: "profile-b",
        name: null,
        current: false,
        input: 60,
        output: 30,
        cache_read: 5,
        cache_write: 5,
        calls: 4,
        sessions: 1,
      },
    ],
    bots_omitted: 3,
    kinds: [
      { kind: "main", input: 600, output: 0, cache_read: 0, cache_write: 0, calls: 10 },
      { kind: "task", input: 250, output: 0, cache_read: 0, cache_write: 0, calls: 5 },
      { kind: "bot_thread", input: 100, output: 0, cache_read: 0, cache_write: 0, calls: 3 },
      { kind: "routine", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 0 },
      { kind: "subagent", input: 0, output: 0, cache_read: 0, cache_write: 0, calls: 1 },
    ],
    routes: [
      {
        connection_id: null,
        connection_name: null,
        model: "claude-sonnet-5",
        input: 350,
        output: 150,
        cache_read: 80,
        cache_write: 20,
        calls: 9,
      },
      {
        connection_id: "conn-1",
        connection_name: "My Gateway",
        model: "gpt-5",
        input: 100,
        output: 50,
        cache_read: 20,
        cache_write: 10,
        calls: 4,
      },
    ],
    routes_omitted: 2,
    largest: [
      {
        session_id: "session-1",
        bot_id: "bot-a",
        kind: "main",
        input: 400,
        output: 200,
        cache_read: 50,
        cache_write: 20,
        calls: 12,
        first_at: new Date(2026, 10, 18, 8, 0, 0).toISOString(),
        last_at: new Date(2026, 10, 20, 9, 15, 0).toISOString(),
        transcript: true,
      },
      {
        session_id: "session-2",
        bot_id: "bot-missing",
        kind: "task",
        input: 60,
        output: 30,
        cache_read: 5,
        cache_write: 5,
        calls: 4,
        first_at: new Date(2026, 10, 19, 8, 0, 0).toISOString(),
        last_at: new Date(2026, 10, 21, 14, 3, 7).toISOString(),
        transcript: false,
      },
    ],
    coverage: {
      sessions: 10,
      log_only_sessions: 0,
      log_only_calls: 0,
      unlogged: 0,
      pending: 0,
      rows: 100,
      oldest_at: null,
      max_rows: 5000,
      max_age_days: 30,
    },
    ...overrides,
  };
}

function tableAt(index: number): HTMLTableElement {
  return document.querySelectorAll("table.bot-usage-table")[index] as HTMLTableElement;
}

function rowTexts(table: HTMLTableElement): (string | null)[][] {
  return Array.from(table.querySelectorAll("tbody tr")).map((row) =>
    Array.from(row.querySelectorAll("th, td")).map((cell) => cell.textContent),
  );
}

describe("UsageTables", () => {
  test("renders the four section headings", () => {
    render(<UsageTables summary={summary()} onTrace={() => {}} onOpen={() => {}} />);
    expect(screen.getByRole("heading", { name: "Bots" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Models and routes" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Conversation kinds" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Largest conversations" })).toBeTruthy();
  });

  test("Bots, Models and routes, and Largest conversations tables each sit in their own focusable scroll region named by their heading (F13); Conversation kinds does not", () => {
    render(<UsageTables summary={summary()} onTrace={() => {}} onOpen={() => {}} />);
    for (const name of ["Bots", "Models and routes", "Largest conversations"]) {
      const region = screen.getByRole("region", { name });
      expect(region.tabIndex).toBe(0);
      expect(region.querySelector("table.bot-usage-table")).toBeTruthy();
    }
    expect(screen.queryByRole("region", { name: "Conversation kinds" })).toBeNull();
    expect(screen.getAllByRole("region")).toHaveLength(3);
  });

  test("Bots table rows carry tokens, share and every count, right-aligned", () => {
    render(<UsageTables summary={summary()} onTrace={() => {}} onOpen={() => {}} />);
    const table = tableAt(0);
    expect(rowTexts(table)).toEqual([
      ["Research Bot", "670", "67%", "400", "200", "50", "20", "12", "5"],
      ["Unknown Bot (removed)", "100", "10%", "60", "30", "5", "5", "4", "1"],
    ]);
    expect(table.querySelectorAll("tbody .bot-usage-table-num")).toHaveLength(16);
  });

  test("Bots note is plural for more than one omitted Bot, singular for one, absent for none", () => {
    const { rerender } = render(
      <UsageTables summary={summary()} onTrace={() => {}} onOpen={() => {}} />,
    );
    expect(screen.getByText("3 more Bots are counted under Other Bots.")).toBeTruthy();

    rerender(
      <UsageTables summary={summary({ bots_omitted: 1 })} onTrace={() => {}} onOpen={() => {}} />,
    );
    expect(screen.getByText("1 more Bot is counted under Other Bots.")).toBeTruthy();

    rerender(
      <UsageTables summary={summary({ bots_omitted: 0 })} onTrace={() => {}} onOpen={() => {}} />,
    );
    expect(screen.queryByText(/more Bot/)).toBeNull();
  });

  test("Bots table shows the empty message when there is no Bot usage", () => {
    render(
      <UsageTables
        summary={summary({ bots: [], bots_omitted: 0 })}
        onTrace={() => {}}
        onOpen={() => {}}
      />,
    );
    expect(screen.getAllByText("No usage in this period.")).toHaveLength(1);
    expect(document.querySelectorAll("table.bot-usage-table")).toHaveLength(3);
  });

  test("Models and routes table shows Route, Model and every count", () => {
    render(<UsageTables summary={summary()} onTrace={() => {}} onOpen={() => {}} />);
    const table = tableAt(1);
    expect(rowTexts(table)).toEqual([
      ["Claude Code", "claude-sonnet-5", "600", "60%", "350", "150", "80", "20", "9"],
      ["My Gateway", "gpt-5", "180", "18%", "100", "50", "20", "10", "4"],
    ]);
  });

  test("routes note is plural for more than one omitted route, singular for one, absent for none", () => {
    const { rerender } = render(
      <UsageTables summary={summary()} onTrace={() => {}} onOpen={() => {}} />,
    );
    expect(screen.getByText("2 more routes are not shown.")).toBeTruthy();

    rerender(
      <UsageTables summary={summary({ routes_omitted: 1 })} onTrace={() => {}} onOpen={() => {}} />,
    );
    expect(screen.getByText("1 more route is not shown.")).toBeTruthy();

    rerender(
      <UsageTables summary={summary({ routes_omitted: 0 })} onTrace={() => {}} onOpen={() => {}} />,
    );
    expect(screen.queryByText(/more route/)).toBeNull();
  });

  test("Models and routes table shows the empty message when there is no route usage", () => {
    render(
      <UsageTables
        summary={summary({ routes: [], routes_omitted: 0 })}
        onTrace={() => {}}
        onOpen={() => {}}
      />,
    );
    expect(screen.getAllByText("No usage in this period.")).toHaveLength(1);
  });

  test("Conversation kinds table lists all five kinds and a Total row equal to totals", () => {
    render(<UsageTables summary={summary()} onTrace={() => {}} onOpen={() => {}} />);
    const table = tableAt(2);
    expect(rowTexts(table)).toEqual([
      ["Main Bot chat", "600", "60%", "10"],
      ["Task threads", "250", "25%", "5"],
      ["Bot thread turns", "100", "10%", "3"],
      ["Routine runs", "0", "0.0%", "0"],
      ["Subagents", "0", "0.0%", "1"],
      ["Total", "1,000", "100%", "50"],
    ]);
  });

  test("Largest conversations looks up the Bot by id, falls back when folded away, and shows Transcript removed", () => {
    const onTrace = vi.fn();
    const onOpen = vi.fn();
    render(<UsageTables summary={summary()} onTrace={onTrace} onOpen={onOpen} />);
    const table = tableAt(3);
    const rows = table.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);

    const first = rows[0] as HTMLElement;
    expect(first.querySelector("th")?.textContent).toBe("Research Bot");
    expect(Array.from(first.querySelectorAll("td")).map((cell) => cell.textContent)).toEqual([
      "Main Bot chat",
      "670",
      "12",
      "Nov 20, 09:15:00",
      "Trace",
      "Open",
    ]);

    const second = rows[1] as HTMLElement;
    expect(second.querySelector("th")?.textContent).toBe("Unknown Bot");
    expect(Array.from(second.querySelectorAll("td")).map((cell) => cell.textContent)).toEqual([
      "Task threads",
      "100",
      "4",
      "Nov 21, 14:03:07",
      "Trace",
      "Transcript removed",
    ]);
    expect(second.querySelectorAll("button")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Trace Research Bot" }));
    expect(onTrace).toHaveBeenCalledWith("session-1");
    fireEvent.click(screen.getByRole("button", { name: "Open Research Bot" }));
    expect(onOpen).toHaveBeenCalledWith("session-1");
    fireEvent.click(screen.getByRole("button", { name: "Trace Unknown Bot" }));
    expect(onTrace).toHaveBeenCalledWith("session-2");
  });

  test("Largest conversations table shows the empty message when there is no activity", () => {
    render(<UsageTables summary={summary({ largest: [] })} onTrace={() => {}} onOpen={() => {}} />);
    expect(screen.getAllByText("No usage in this period.")).toHaveLength(1);
  });
});
