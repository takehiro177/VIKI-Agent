/**
 * SessionLimitToast's own raise-limit line and button (P4 of chat-limit): shown only with
 * onRaiseLimit, sitting between .toast-sub and .toast-rows, and never closing a session or
 * dismissing the toast itself. sessionLimit.accept.test.tsx (frozen) pins the two sentences,
 * the row order and the per-row Close buttons; this file covers what it does not.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { type LiveSessionSummary, SessionLimitToast } from "./SessionLimitToast";

afterEach(cleanup);

function liveRows(count: number): LiveSessionSummary[] {
  return Array.from({ length: count }, (_, i) => ({ sessionId: `s${i}`, title: `Session ${i}` }));
}

test("with onRaiseLimit, the raise-limit sentence and its button precede .toast-rows", () => {
  const { container } = render(
    <SessionLimitToast
      live={liveRows(3)}
      onClose={vi.fn()}
      onDismiss={vi.fn()}
      onRaiseLimit={vi.fn()}
    />,
  );
  const raise = container.querySelector(".toast-raise") as HTMLElement;
  const rows = container.querySelector(".toast-rows") as HTMLElement;
  const button = screen.getByRole("button", { name: "Change limit" });

  expect(raise).not.toBeNull();
  expect(raise.textContent).toContain("Or raise the limit in Settings.");
  expect(raise.contains(button)).toBe(true);
  expect(raise.compareDocumentPosition(rows) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("Change limit calls onRaiseLimit once and closes nothing", () => {
  const onRaiseLimit = vi.fn();
  const onClose = vi.fn();
  const onDismiss = vi.fn();
  render(
    <SessionLimitToast
      live={liveRows(1)}
      onClose={onClose}
      onDismiss={onDismiss}
      onRaiseLimit={onRaiseLimit}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Change limit" }));
  expect(onRaiseLimit).toHaveBeenCalledTimes(1);
  expect(onClose).not.toHaveBeenCalled();
  expect(onDismiss).not.toHaveBeenCalled();
});

test("30 live sessions still give 30 Close buttons", () => {
  const { container } = render(
    <SessionLimitToast
      live={liveRows(30)}
      onClose={vi.fn()}
      onDismiss={vi.fn()}
      onRaiseLimit={vi.fn()}
    />,
  );
  expect(container.querySelectorAll(".toast-row")).toHaveLength(30);
  expect(screen.getAllByText("Close")).toHaveLength(30);
});

test("without onRaiseLimit, neither the sentence nor the button renders", () => {
  const { container } = render(
    <SessionLimitToast live={liveRows(2)} onClose={vi.fn()} onDismiss={vi.fn()} />,
  );
  expect(container.querySelector(".toast-raise")).toBeNull();
  expect(screen.queryByText("Or raise the limit in Settings.")).toBeNull();
  expect(screen.queryByRole("button", { name: "Change limit" })).toBeNull();
});
