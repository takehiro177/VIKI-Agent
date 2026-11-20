/**
 * Unit tests for ErrorCard's too_many_sessions handling. It gets fixed title copy naming the
 * limit (the generic fallback says only "Reply failed") and, with onRaiseLimit, a way out —
 * mirroring SessionLimitToast's onRaiseLimit contract (SessionLimitToast.unit.test.tsx). It is
 * deliberately not added to ERROR_COPY: the line stays the server's own message verbatim, since
 * that is where the live limit number lives; everything above and below this one code's handling
 * (ErrorCard.unit.test.tsx, the frozen accept/m2b3/errors.accept.test.tsx) is untouched.
 */
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ErrorData } from "../../protocol";
import { ErrorCard } from "./ErrorCard";

afterEach(cleanup);

function renderCard(data: ErrorData, onRetry?: () => void, onRaiseLimit?: () => void): HTMLElement {
  // Spread rather than passing `undefined` literals: exactOptionalPropertyTypes makes the two
  // distinct, and every real caller omits a callback it has nothing for (ErrorCard.unit.test.tsx
  // uses the same pattern).
  const { container } = render(
    <ErrorCard
      data={data}
      {...(onRetry ? { onRetry } : {})}
      {...(onRaiseLimit ? { onRaiseLimit } : {})}
    />,
  );
  return container.querySelector(".error-card") as HTMLElement;
}

test("titles the card with the fixed limit copy, not the generic fallback", () => {
  const card = renderCard({ code: "too_many_sessions", message: "irrelevant", recoverable: true });
  expect(card.querySelector(".error-title")?.textContent).toBe("Session limit reached");
});

test("the line is the server's own message verbatim, number included", () => {
  const message =
    "You can run at most 12 sessions at the same time. Close one, or raise the limit in Settings › Chat, then send again.";
  const card = renderCard({ code: "too_many_sessions", message, recoverable: true });
  expect(card.querySelector(".error-line")?.textContent).toBe(message);
});

test("renders the message as literal text, never markup, even carrying a count", () => {
  const markup = '<img src=x onerror="window.__pwned=1">';
  const card = renderCard({ code: "too_many_sessions", message: markup, recoverable: true });
  expect(card.querySelector(".error-line")?.textContent).toBe(markup);
  expect(card.querySelector("img")).toBeNull();
});

test("offers no button without onRaiseLimit", () => {
  const card = renderCard({ code: "too_many_sessions", message: "x", recoverable: true });
  expect(card.querySelector("button")).toBeNull();
});

test("with onRaiseLimit, a card-actions button reads Change limit and calls it once", () => {
  const onRaiseLimit = vi.fn();
  const card = renderCard(
    { code: "too_many_sessions", message: "x", recoverable: true },
    undefined,
    onRaiseLimit,
  );
  const button = card.querySelector(".card-actions button") as HTMLButtonElement;
  expect(button.textContent).toBe("Change limit");
  fireEvent.click(button);
  expect(onRaiseLimit).toHaveBeenCalledTimes(1);
});

test("a recoverable crash keeps its own Send again button alone, even when onRaiseLimit is given", () => {
  const onRetry = vi.fn();
  const onRaiseLimit = vi.fn();
  const card = renderCard(
    { code: "crashed", message: "x", recoverable: true },
    onRetry,
    onRaiseLimit,
  );
  const buttons = card.querySelectorAll("button");
  expect(buttons.length).toBe(1);
  expect(buttons[0]?.textContent).toBe("Send again");
});

test("onRaiseLimit given for an unrelated fixed-copy code renders no button", () => {
  const onRaiseLimit = vi.fn();
  const card = renderCard(
    { code: "billing", message: "x", recoverable: false },
    undefined,
    onRaiseLimit,
  );
  expect(card.querySelector("button")).toBeNull();
});
