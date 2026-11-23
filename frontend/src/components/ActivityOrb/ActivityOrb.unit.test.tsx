import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { narrowBlock, readCss, ruleIn } from "../../testing/css";
import { ActivityOrb } from "./ActivityOrb";
import { paintTileOrb, stillSeconds } from "./tileOrb";

vi.mock("./tileOrb", async (importOriginal) => {
  const original = await importOriginal<typeof import("./tileOrb")>();
  return { ...original, paintTileOrb: vi.fn(original.paintTileOrb) };
});

const frames = new Map<number, FrameRequestCallback>();
const media = new Map<string, { matches: boolean; listeners: Set<() => void> }>();
let nextId = 0;
let now = 0;
let ctx: CanvasRenderingContext2D;

beforeEach(() => {
  frames.clear();
  media.clear();
  nextId = 0;
  now = 0;
  vi.mocked(paintTileOrb).mockClear();
  ctx = {
    setTransform: vi.fn(),
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    rect: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    closePath: vi.fn(),
    fill: vi.fn(),
  } as unknown as CanvasRenderingContext2D;
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx);
  // jsdom applies no stylesheet: stand in for tokens.css's `--or` per theme.
  vi.spyOn(window, "getComputedStyle").mockImplementation(
    () =>
      ({
        getPropertyValue: (name: string) =>
          name === "--or"
            ? document.documentElement.dataset.theme === "light"
              ? "#000000"
              : "#f8f8ff"
            : "",
      }) as unknown as CSSStyleDeclaration,
  );
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++nextId, callback);
    return nextId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    frames.delete(id);
  });
  vi.stubGlobal("matchMedia", (query: string) => {
    const value = media.get(query) ?? { matches: false, listeners: new Set<() => void>() };
    media.set(query, value);
    return {
      get matches() {
        return value.matches;
      },
      addEventListener: (_event: string, callback: () => void) => value.listeners.add(callback),
      removeEventListener: (_event: string, callback: () => void) =>
        value.listeners.delete(callback),
    };
  });
});

afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.motion;
  delete document.documentElement.dataset.theme;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function step(timestamp: number) {
  now = timestamp;
  const pending = [...frames.values()];
  frames.clear();
  act(() => {
    for (const callback of pending) callback(timestamp);
  });
}

/** The clock the last paint drew. */
const lastSeconds = () => vi.mocked(paintTileOrb).mock.calls.at(-1)?.[1];

test("the tile painter receives the still clock, the theme's ink and the 2x pixel ratio", () => {
  vi.stubGlobal("devicePixelRatio", 3);
  const { container, queryByRole } = render(<ActivityOrb state="idle" />);
  expect(paintTileOrb).toHaveBeenCalledWith(ctx, stillSeconds("idle"), "#f8f8ff", 2, "idle");
  expect(ctx.rect).toHaveBeenCalled();
  const canvas = container.querySelector("canvas");
  expect(canvas?.width).toBe(128);
  expect(canvas?.height).toBe(128);
  expect(canvas?.dataset.renderer).toBe("viki-tiles");
  expect(canvas?.dataset.preset).toBeUndefined();
  expect(canvas?.parentElement?.getAttribute("aria-hidden")).toBe("true");
  expect(queryByRole("img")).toBeNull();
  expect(container.querySelector(".activity-orb-state")?.getAttribute("focusable")).toBe("false");
  expect(
    container.querySelector("button, a, [tabindex], .activity-orb-particle, .activity-orb-bands"),
  ).toBeNull();
  expect(container.textContent).toBe("");
});

test.each(["idle", "waiting", "offline", "error"] as const)(
  "%s paints one still frame of its own look without starting work",
  (state) => {
    const { container } = render(<ActivityOrb state={state} />);
    expect(ctx.clearRect).toHaveBeenCalledTimes(1);
    expect(paintTileOrb).toHaveBeenCalledWith(ctx, stillSeconds(state), "#f8f8ff", 1, state);
    expect(frames.size).toBe(0);
    expect(container.querySelector(".activity-orb")?.getAttribute("data-paused")).toBe("true");
  },
);

test.each(["idle", "offline", "error"] as const)(
  "a turn from %s opens with the film's start; an approval pause resumes where it stopped",
  (rest) => {
    const { rerender } = render(<ActivityOrb state={rest} />);
    rerender(<ActivityOrb state="active" />);
    expect(lastSeconds()).toBe(0);
    step(17);
    step(34);
    step(51);
    const played = lastSeconds() ?? 0;
    expect(played).toBeGreaterThan(0.04);
    rerender(<ActivityOrb state="waiting" />);
    expect(lastSeconds()).toBe(stillSeconds("waiting"));
    rerender(<ActivityOrb state="active" />);
    expect(lastSeconds()).toBe(played);
    rerender(<ActivityOrb state={rest} />);
    rerender(<ActivityOrb state="active" />);
    expect(lastSeconds()).toBe(0);
  },
);

test("fractional device pixels resize the backing canvas once rather than resetting every frame", () => {
  vi.stubGlobal("devicePixelRatio", 1.1);
  const width = vi.spyOn(HTMLCanvasElement.prototype, "width", "set");
  const height = vi.spyOn(HTMLCanvasElement.prototype, "height", "set");
  const { container } = render(<ActivityOrb state="active" />);
  expect(container.querySelector("canvas")?.width).toBe(70);
  expect(container.querySelector("canvas")?.height).toBe(70);
  expect(width).toHaveBeenCalledTimes(1);
  expect(height).toHaveBeenCalledTimes(1);
  step(17);
  step(34);
  expect(ctx.clearRect).toHaveBeenCalledTimes(3);
  expect(width).toHaveBeenCalledTimes(1);
  expect(height).toHaveBeenCalledTimes(1);
});

test("active paint is bounded near 60fps; waiting preserves the canvas and stops queued callbacks", () => {
  const { container, rerender, unmount } = render(<ActivityOrb state="active" />);
  const canvas = container.querySelector("canvas");
  step(7);
  step(14);
  expect(ctx.clearRect).toHaveBeenCalledTimes(1);
  step(17);
  expect(ctx.clearRect).toHaveBeenCalledTimes(2);
  const queued = [...frames.values()];
  rerender(<ActivityOrb state="waiting" />);
  expect(container.querySelector("canvas")).toBe(canvas);
  expect(frames.size).toBe(0);
  const calls = vi.mocked(ctx.clearRect).mock.calls.length;
  for (const callback of queued) callback(1000);
  expect(ctx.clearRect).toHaveBeenCalledTimes(calls);
  unmount();
  for (const callback of queued) callback(2000);
  expect(ctx.clearRect).toHaveBeenCalledTimes(calls);
  expect(frames.size).toBe(0);
});

test.each([
  "(prefers-reduced-motion: reduce)",
  "(forced-colors: active)",
  "(prefers-contrast: more)",
])("%s cancels painting and restores active motion", (query) => {
  const { container } = render(<ActivityOrb state="active" />);
  const preference = media.get(query);
  expect(preference).toBeDefined();
  if (!preference) return;
  act(() => {
    preference.matches = true;
    for (const callback of preference.listeners) callback();
  });
  expect(frames.size).toBe(0);
  expect(container.querySelector(".activity-orb")?.getAttribute("data-paused")).toBe("true");
  act(() => {
    preference.matches = false;
    for (const callback of preference.listeners) callback();
  });
  expect(frames.size).toBe(1);
});

test("app motion and document visibility gate work, while theme changes keep the same canvas", async () => {
  let hidden = false;
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  const { container } = render(<ActivityOrb state="active" />);
  const canvas = container.querySelector("canvas");
  act(() => {
    hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(frames.size).toBe(0);
  expect(container.querySelector(".activity-orb")?.getAttribute("data-visible")).toBe("false");
  act(() => {
    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(frames.size).toBe(1);
  await act(async () => {
    document.documentElement.dataset.motion = "reduce";
  });
  expect(frames.size).toBe(0);
  await act(async () => {
    document.documentElement.dataset.theme = "light";
  });
  expect(vi.mocked(paintTileOrb).mock.calls.at(-1)?.[2]).toBe("#000000");
  expect(container.querySelector("canvas")).toBe(canvas);
});

test("stale observer/media deliveries after pause or unmount cannot revive the painter", () => {
  let notify: IntersectionObserverCallback = () => {};
  const observer = { observe: vi.fn(), disconnect: vi.fn() };
  vi.stubGlobal(
    "IntersectionObserver",
    // biome-ignore lint/complexity/useArrowFunction: IntersectionObserver is constructed with new.
    vi.fn(function (callback: IntersectionObserverCallback) {
      notify = callback;
      return observer;
    }),
  );
  const { container, rerender, unmount } = render(<ActivityOrb state="active" />);
  act(() =>
    notify(
      [{ isIntersecting: false } as IntersectionObserverEntry],
      observer as unknown as IntersectionObserver,
    ),
  );
  expect(frames.size).toBe(0);
  rerender(<ActivityOrb state="waiting" />);
  act(() =>
    notify(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      observer as unknown as IntersectionObserver,
    ),
  );
  expect(frames.size).toBe(0);
  expect(container.querySelector(".activity-orb")?.getAttribute("data-paused")).toBe("true");
  const stale = notify;
  const staleMedia = [...media.values()].flatMap((preference) => [...preference.listeners]);
  unmount();
  const calls = vi.mocked(ctx.clearRect).mock.calls.length;
  act(() => {
    stale(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      observer as unknown as IntersectionObserver,
    );
    for (const callback of staleMedia) callback();
  });
  step(1000);
  expect(ctx.clearRect).toHaveBeenCalledTimes(calls);
  expect(frames.size).toBe(0);
  expect(observer.disconnect).toHaveBeenCalled();
});

test("forced colors retain the explicit state badge while the decorative canvas stays out of the way", () => {
  const css = readCss(import.meta.url, "ActivityOrb.css");
  expect(ruleIn(css, ".activity-orb")).toContain("width: 64px");
  expect(ruleIn(css, ".activity-orb")).toContain("pointer-events: none");
  const forced = narrowBlock(css, "@media (forced-colors: active)");
  expect(ruleIn(forced, ".activity-orb canvas")).toContain("visibility: hidden");
  expect(forced).toContain("color: CanvasText");
  expect(forced).toContain("fill: Canvas");
});
