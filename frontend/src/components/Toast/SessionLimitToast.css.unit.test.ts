/**
 * SessionLimitToast.css facts that jsdom cannot see from a render, pinned against the source
 * text the same way LeftRail.css.unit.test.ts and RightRail.css.unit.test.ts pin theirs.
 */
import { expect, test } from "vitest";
import { readCss, ruleIn } from "../../testing/css";

const css = readCss(import.meta.url, "SessionLimitToast.css");

test("the toast is positioned within the conversation column, not the window", () => {
  // `fixed` measures `right: 24px` from the window edge, landing the toast on top
  // of the right rail. `absolute` inside Shell's `.conversation` (already `position: relative`)
  // matches the design's pixels instead.
  const toast = ruleIn(css, ".toast");
  expect(toast).toMatch(/position:\s*absolute/);
  expect(toast).not.toMatch(/position:\s*fixed/);
});

test("a row's dot is ringed by default and filled while writing", () => {
  const dot = ruleIn(css, ".toast-row-dot");
  expect(dot).toMatch(/border:\s*1px solid var\(--or\)/);
  const writing = ruleIn(css, ".toast-row-dot-writing");
  expect(writing).toMatch(/background:\s*var\(--or\)/);
});

test(".toast stays the one scroller, bounded by its own pane, not a flex column", () => {
  const toast = ruleIn(css, ".toast");
  expect(toast).toMatch(/max-height:\s*calc\(100cqh - 112px\)/);
  expect(toast).toMatch(/overflow-y:\s*auto/);
  expect(toast).not.toMatch(/display:\s*flex/);
  expect(toast).not.toMatch(/flex-direction:\s*column/);
});

test(".toast-rows has no overflow rule of its own; the toast above it is the only scroller", () => {
  const rows = ruleIn(css, ".toast-rows");
  expect(rows).not.toMatch(/overflow-y/);
  expect(rows).not.toMatch(/overscroll-behavior/);
  expect(rows).not.toMatch(/max-height/);
  expect(rows).not.toMatch(/min-height/);
});
