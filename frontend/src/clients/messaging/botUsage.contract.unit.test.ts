import { describe, expect, it } from "vitest";
import golden from "./botUsage.contract.json";
import { parseSummary, parseTrace } from "./botUsageApi";

// The server's answers for one Bot chat turn over the background-agent transcript fixture, as
// tests/messaging/test_bot_usage_contract.py records them: the parsers must accept them unchanged.
const FIELDS = ["input", "output", "cache_read", "cache_write", "calls"] as const;

describe("Bot usage wire contract", () => {
  it("parses the server's summary and trace, whose kinds add up to the fixture's totals", () => {
    const summary = parseSummary(golden.summary);
    expect(() => parseTrace(golden.trace)).not.toThrow();
    expect(summary.totals).toEqual({
      input: 6,
      output: 232,
      cache_read: 127782,
      cache_write: 67732,
      calls: 3,
    });
    for (const field of FIELDS) {
      const sum = summary.kinds.reduce((total, row) => total + row[field], 0);
      expect(sum).toBe(summary.totals[field]);
    }
  });
});
