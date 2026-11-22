import { describe, expect, it } from "vitest";
import {
  botLabel,
  compactCount,
  dayLabel,
  formatCount,
  KIND_LABELS,
  localDayLabel,
  percentText,
  routeLabel,
  timeLabel,
  tokensOf,
} from "./usageFormat";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** The expected "Mon D" for a timestamp, read off the same `Date`'s own local fields rather than
 * through any `Intl` call — an oracle independent of `localDayLabel`'s own implementation, so the
 * test holds under whatever zone it runs in. */
function localCalendarDay(timestamp: string): string {
  const date = new Date(timestamp);
  return `${MONTHS[date.getMonth()]} ${date.getDate()}`;
}

describe("formatCount", () => {
  it("groups with commas, full precision", () => {
    expect(formatCount(12345)).toBe("12,345");
    expect(formatCount(0)).toBe("0");
  });
});

describe("compactCount", () => {
  it("compacts to at most one fraction digit", () => {
    expect(compactCount(1234567)).toBe("1.2M");
    expect(compactCount(999)).toBe("999");
  });
});

describe("percentText", () => {
  it("shows the empty dash when whole is 0", () => {
    expect(percentText(0, 0)).toBe("-");
    expect(percentText(5, 0)).toBe("-");
  });

  it("uses one decimal under 10%", () => {
    expect(percentText(42, 1000)).toBe("4.2%");
  });

  it("uses no decimal at 10% and above", () => {
    expect(percentText(10, 100)).toBe("10%");
    expect(percentText(37, 100)).toBe("37%");
  });
});

describe("tokensOf", () => {
  it("sums input, output and both cache types", () => {
    expect(tokensOf({ input: 10, output: 20, cache_read: 3, cache_write: 4 })).toBe(37);
    expect(tokensOf({ input: 0, output: 0, cache_read: 0, cache_write: 0 })).toBe(0);
  });
});

describe("KIND_LABELS", () => {
  it("labels each kind code, in the summary's display order", () => {
    expect(Object.entries(KIND_LABELS)).toEqual([
      ["main", "Main Bot chat"],
      ["task", "Task threads"],
      ["bot_thread", "Bot thread turns"],
      ["routine", "Routine runs"],
      ["subagent", "Subagents"],
    ]);
  });
});

describe("routeLabel", () => {
  it("is Claude Code for the native route, regardless of name", () => {
    expect(routeLabel(null, "ignored")).toBe("Claude Code");
    expect(routeLabel(null, undefined)).toBe("Claude Code");
  });

  it("is the connection's name when one is given", () => {
    expect(routeLabel("conn-1", "My Connection")).toBe("My Connection");
  });

  it("falls back to Removed connection when the name is missing or empty", () => {
    expect(routeLabel("conn-1", null)).toBe("Removed connection");
    expect(routeLabel("conn-1", undefined)).toBe("Removed connection");
    expect(routeLabel("conn-1", "")).toBe("Removed connection");
  });
});

describe("botLabel", () => {
  it("is the Bot's name when current", () => {
    expect(botLabel("Research Bot", true)).toBe("Research Bot");
  });

  it("appends (removed) when not current", () => {
    expect(botLabel("Research Bot", false)).toBe("Research Bot (removed)");
  });

  it("falls back to Unknown Bot when the name is missing or empty", () => {
    expect(botLabel(null, true)).toBe("Unknown Bot");
    expect(botLabel(undefined, true)).toBe("Unknown Bot");
    expect(botLabel("", false)).toBe("Unknown Bot (removed)");
  });
});

describe("dayLabel", () => {
  it("reads a day string as a UTC calendar date, never shifting under a local offset", () => {
    expect(dayLabel("2026-11-21")).toBe("Nov 21");
    expect(dayLabel("2026-01-05")).toBe("Jan 5");
  });
});

describe("timeLabel", () => {
  it("formats a timestamp in the viewer's local time", () => {
    const local = new Date(2026, 10, 21, 14, 3, 7);
    expect(timeLabel(local.toISOString())).toBe("Nov 21, 14:03:07");
  });
});

describe("localDayLabel", () => {
  it("reads a full timestamp's calendar day in the viewer's local time, in any zone", () => {
    // Near a UTC day boundary on purpose (F23): this is where a UTC-pinned reading of the same
    // instant would print a different day than the viewer's own local calendar.
    const timestamp = "2026-11-20T20:00:00.000Z";
    expect(localDayLabel(timestamp)).toBe(localCalendarDay(timestamp));
  });

  it("matches a plain local timestamp's own month and day", () => {
    const local = new Date(2026, 0, 5, 9, 0, 0);
    expect(localDayLabel(local.toISOString())).toBe(localCalendarDay(local.toISOString()));
  });
});
