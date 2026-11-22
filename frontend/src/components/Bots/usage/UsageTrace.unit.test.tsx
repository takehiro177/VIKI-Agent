import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import type {
  UsageCallRow,
  UsageTrace as UsageTraceData,
} from "../../../clients/messaging/botUsageApi";
import { UsageTrace } from "./UsageTrace";

afterEach(() => {
  cleanup();
});

function call(overrides: Partial<UsageCallRow> = {}): UsageCallRow {
  return {
    at: new Date(2026, 10, 20, 8, 0, 0).toISOString(),
    prompt_id: null,
    model: "claude-sonnet-5",
    agent: false,
    input: 0,
    output: 0,
    cache_read: 0,
    cache_write: 0,
    ...overrides,
  };
}

function trace(overrides: Partial<UsageTraceData> = {}): UsageTraceData {
  return {
    session_id: "11111111-1111-1111-1111-111111111111",
    bot_id: "22222222-2222-2222-2222-222222222222",
    kind: "main",
    connection_id: null,
    connection_name: null,
    transcript: true,
    omitted: 0,
    calls: [],
    ...overrides,
  };
}

function groupSummaries(): (string | null)[] {
  return Array.from(document.querySelectorAll(".bot-usage-trace-group > summary")).map(
    (node) => node.textContent,
  );
}

describe("UsageTrace", () => {
  test("focuses the heading on mount and the summary line reads bot, kind, route and calls", () => {
    render(
      <UsageTrace
        trace={trace({
          kind: "task",
          connection_id: "conn-1",
          connection_name: "My Gateway",
          calls: [call(), call(), call()],
        })}
        botName="Research Bot"
        onClose={() => {}}
      />,
    );
    const heading = screen.getByRole("heading", { name: "Conversation trace" });
    expect(document.activeElement).toBe(heading);
    expect(screen.getByText("Research Bot · Task threads · My Gateway · 3 calls")).toBeTruthy();
  });

  test("groups calls by prompt_id in first-appearance order, labels each group, and leaves only the last one open", () => {
    render(
      <UsageTrace
        trace={trace({
          calls: [
            call({ prompt_id: "turn-1", input: 100, output: 50, cache_read: 10, cache_write: 5 }),
            call({ prompt_id: "turn-1", input: 20, output: 10 }),
            call({ prompt_id: "sub-1", agent: true, input: 40, output: 5 }),
            call({ prompt_id: null, input: 7, output: 3 }),
            call({ prompt_id: "turn-2", input: 1, output: 1 }),
          ],
        })}
        botName="Research Bot"
        onClose={() => {}}
      />,
    );

    expect(groupSummaries()).toEqual([
      "Turn 1 · 2 calls · 195 tokens",
      "Subagent run · 1 call · 45 tokens",
      "Calls without a turn id · 1 call · 10 tokens",
      "Turn 2 · 1 call · 2 tokens",
    ]);

    const groups = Array.from(document.querySelectorAll(".bot-usage-trace-group"));
    expect(groups.map((node) => (node as HTMLDetailsElement).open)).toEqual([
      false,
      false,
      false,
      true,
    ]);
  });

  test("each turn group's table sits in its own scroll region named by that group's own summary line (F13)", () => {
    render(
      <UsageTrace
        trace={trace({
          calls: [
            call({ prompt_id: "turn-1", input: 100, output: 50 }),
            call({ prompt_id: "turn-2", input: 1, output: 1 }),
          ],
        })}
        botName="Research Bot"
        onClose={() => {}}
      />,
    );
    const regions = screen.getAllByRole("region");
    expect(regions).toHaveLength(2);
    for (const region of regions) {
      expect(region.tabIndex).toBe(0);
      expect(region.querySelector("table.bot-usage-trace-table")).toBeTruthy();
    }
    expect(screen.getByRole("region", { name: "Turn 1 · 1 call · 150 tokens" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Turn 2 · 1 call · 2 tokens" })).toBeTruthy();
  });

  test("each call row shows its time, model and thread, and Context sums input, cache reads and cache writes but not output", () => {
    render(
      <UsageTrace
        trace={trace({
          calls: [
            call({
              prompt_id: "turn-1",
              model: "claude-sonnet-5",
              agent: false,
              input: 100,
              output: 50,
              cache_read: 10,
              cache_write: 5,
            }),
            call({
              prompt_id: "turn-1",
              model: "claude-haiku-5",
              agent: true,
              input: 40,
              output: 5,
            }),
          ],
        })}
        botName="Research Bot"
        onClose={() => {}}
      />,
    );
    const rows = document.querySelectorAll("tbody tr");
    const cellsOf = (row: Element) =>
      Array.from(row.querySelectorAll("td")).map((cell) => cell.textContent);
    expect(cellsOf(rows[0] as HTMLElement)).toEqual([
      "Nov 20, 08:00:00",
      "claude-sonnet-5",
      "Main",
      "100",
      "50",
      "10",
      "5",
      "115",
    ]);
    expect(cellsOf(rows[1] as HTMLElement)).toEqual([
      "Nov 20, 08:00:00",
      "claude-haiku-5",
      "Subagent",
      "40",
      "5",
      "0",
      "0",
      "40",
    ]);
  });

  test("shows the omitted note only when older calls were left out", () => {
    const { rerender } = render(
      <UsageTrace trace={trace({ omitted: 7 })} botName="Research Bot" onClose={() => {}} />,
    );
    expect(screen.getByText("Older calls not shown: 7.")).toBeTruthy();

    rerender(
      <UsageTrace trace={trace({ omitted: 0 })} botName="Research Bot" onClose={() => {}} />,
    );
    expect(screen.queryByText(/Older calls not shown/)).toBeNull();
  });

  test("Close button calls onClose", () => {
    const onClose = vi.fn();
    render(<UsageTrace trace={trace()} botName="Research Bot" onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
