/**
 * splitPlanDetails: where a plan's "## Details" heading cuts the owner's part from the
 * evidence, and the cases where it must not cut at all.
 */
import { expect, test } from "vitest";
import { splitPlanDetails } from "./planDetails";

test("splits at a `## Details` heading, dropping the heading line itself", () => {
  const text = "Finding and proposal.\n\n## Details\n\nEvidence line.\n";
  expect(splitPlanDetails(text)).toEqual({
    top: "Finding and proposal.",
    details: "\nEvidence line.\n",
  });
});

test("matches `### details` case-insensitively, at a different heading level", () => {
  const text = "Top line.\n\n### details\n\nMore evidence.";
  expect(splitPlanDetails(text)).toEqual({
    top: "Top line.",
    details: "\nMore evidence.",
  });
});

test("drops every trailing blank line before the heading, not just one", () => {
  const text = "Line one.\n\n\n\n## Details\n\nEvidence.\n";
  expect(splitPlanDetails(text)).toEqual({
    top: "Line one.",
    details: "\nEvidence.\n",
  });
});

test("ignores a Details-shaped line quoted inside a fenced code block", () => {
  const text = "Top line.\n\n```\n## Details\n```\n\nMore top.\n\n## Details\n\nReal evidence.\n";
  expect(splitPlanDetails(text)).toEqual({
    top: "Top line.\n\n```\n## Details\n```\n\nMore top.",
    details: "\nReal evidence.\n",
  });
});

test("a plan with no Details heading stays whole", () => {
  const text = "Just a plan with no heading at all.\n\n- one step\n- another step\n";
  expect(splitPlanDetails(text)).toEqual({ top: text, details: null });
});

test("a plan that starts with Details stays whole, since nothing precedes it", () => {
  const text = "## Details\n\nEvidence only, nothing else.\n";
  expect(splitPlanDetails(text)).toEqual({ top: text, details: null });
});

test("an empty plan stays whole", () => {
  expect(splitPlanDetails("")).toEqual({ top: "", details: null });
});

test("F4: a closer must match its opener's delimiter type, so a ~~~ inside a ``` fence cannot end it, and the only Details heading stays hidden inside", () => {
  const text =
    "Top line.\n\n```\nExample:\n~~~\n## Details\n~~~\n```\n\nMore top, no real Details section here.";
  expect(splitPlanDetails(text)).toEqual({ top: text, details: null });
});
