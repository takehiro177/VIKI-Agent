import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { BotRow, BotsApi } from "../../clients/messaging/botsApi";
import type { DisplayMetadata } from "../../state/noteMetadata";
import type { VaultApi, VaultMetadataBatch, VaultMetadataItem } from "../../state/vaultApi";
import { kanbanBodyRefs, kanbanLanes, kanbanNeedsYou } from "./kanbanCards";
import {
  KANBAN_RETRY_MAX_MS,
  KANBAN_RETRY_MS,
  type UseKanbanReadOptions,
  useKanbanRead,
} from "./useKanbanRead";

// Wraps (not replaces) kanbanBodyRefs so one test can force it to throw once, the way a real bug
// in a pure helper would — everything else, and every other test, keeps the real implementation.
vi.mock("./kanbanCards", async (importOriginal) => {
  const original = await importOriginal<typeof import("./kanbanCards")>();
  return { ...original, kanbanBodyRefs: vi.fn(original.kanbanBodyRefs) };
});

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
}
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((ok) => {
    resolve = ok;
  });
  return { promise, resolve };
}
/** Settles only by rejecting, when the signal aborts — like the real vaultApi/botsApi. */
function abortable<T>(signal: AbortSignal | undefined): Promise<T> {
  return new Promise<T>((_resolve, reject) => {
    signal?.addEventListener("abort", () => reject(new Error("cancelled")), { once: true });
  });
}
/** A real macrotask boundary (Node's `setImmediate`; not in this project's DOM-only lib types,
 * hence the cast), for a test to give an otherwise-unhandled rejection its chance to surface. */
function nextMacrotask(): Promise<void> {
  const node = globalThis as unknown as { setImmediate(callback: () => void): void };
  return new Promise((resolve) => {
    node.setImmediate(resolve);
  });
}

const meta = (over: Partial<DisplayMetadata> = {}): DisplayMetadata => ({
  state: "ready",
  frontmatter_state: "ready",
  heading_state: "absent",
  display_title: null,
  title_source: null,
  tags: [],
  type: "card",
  status: "todo",
  ...over,
});
const item = (ref: string, over: Partial<DisplayMetadata> = {}): VaultMetadataItem => ({
  ref,
  state: "ready",
  reason: null,
  read_bytes: 1,
  display_metadata: meta(over),
});
const batchOf = (items: VaultMetadataItem[]): VaultMetadataBatch => ({
  items,
  partial: false,
  partial_reasons: [],
  read_bytes: items.length,
  omitted: 0,
});
const listingOf = (refs: string[]): Record<string, unknown> => ({
  entries: refs.map((ref) => ({ ref })),
  partial: false,
  partial_reasons: [],
});
/** Like `listingOf`, but each entry carries its own listed modified time (P3's skip signal). */
const listingWithModified = (entries: Array<{ ref: string; modified?: number }>) => ({
  entries,
  partial: false,
  partial_reasons: [],
});
const SHA = "a".repeat(64);
/** A `note()` response that always parses: content doesn't matter where a test only checks call
 * counts or whether a body is held. */
const NOTE_OK: Record<string, unknown> = {
  note: { source: "---\ntype: card\n---\n", sha256: SHA },
};

type Api = Pick<VaultApi, "entries" | "metadata">;
const botNamed = (id: string): BotRow =>
  ({
    bot_id: id,
    profile_id: `p-${id}`,
    workspace_id: "w1",
    profile: {
      revision: 1,
      archived: false,
      identity: { name: id, purpose: "", avatar_asset_id: null, accent: "blue", shape: "circle" },
    },
  }) as BotRow;

function setup(over: Partial<UseKanbanReadOptions> & Pick<UseKanbanReadOptions, "api">) {
  const initial: UseKanbanReadOptions = { workspaceId: "w1", changes: 0, online: true, ...over };
  return renderHook((props: UseKanbanReadOptions) => useKanbanRead(props), {
    initialProps: initial,
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

it("starts loading, then becomes ready once the listing and metadata resolve", async () => {
  const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf(["kanban/a.md"]));
  const metadata = vi
    .fn<NonNullable<VaultApi["metadata"]>>()
    .mockResolvedValue(batchOf([item("kanban/a.md")]));
  const { result } = setup({ api: { entries, metadata } });
  expect(result.current.state).toEqual({ phase: "loading" });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.state.phase).toBe("ready");
});

it("a listing throw on first load goes to error, retries after 1 s, and reaches ready", async () => {
  const entries = vi
    .fn<VaultApi["entries"]>()
    .mockRejectedValueOnce(new Error("boom"))
    .mockResolvedValue(listingOf([]));
  const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockResolvedValue(batchOf([]));
  const { result } = setup({ api: { entries, metadata } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.state).toEqual({ phase: "error" });
  expect(entries).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MS - 1);
  });
  expect(entries).toHaveBeenCalledTimes(1); // not yet

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(entries).toHaveBeenCalledTimes(2);
  expect(result.current.state.phase).toBe("ready");
});

it("coalesces five changes bumps during one pass into exactly one more pass", async () => {
  const first = deferred<Record<string, unknown>>();
  const entries = vi
    .fn<VaultApi["entries"]>()
    .mockReturnValueOnce(first.promise)
    .mockResolvedValue(listingOf([]));
  const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockResolvedValue(batchOf([]));
  const api: Api = { entries, metadata };
  const { rerender } = setup({ api });
  expect(entries).toHaveBeenCalledTimes(1); // the mount pass, started and pending

  for (let n = 1; n <= 5; n++) {
    act(() => {
      rerender({ api, workspaceId: "w1", changes: n, online: true });
    });
  }
  expect(entries).toHaveBeenCalledTimes(1); // all five coalesced; nothing new ran yet

  await act(async () => {
    first.resolve(listingOf([]));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(entries).toHaveBeenCalledTimes(2); // exactly one more pass, not five
});

it("reads once on mount and exactly once more when online flips false to true", async () => {
  const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf([]));
  const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockResolvedValue(batchOf([]));
  const api: Api = { entries, metadata };
  const { rerender } = setup({ api, online: false });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(entries).toHaveBeenCalledTimes(1);

  act(() => {
    rerender({ api, workspaceId: "w1", changes: 0, online: true });
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(entries).toHaveBeenCalledTimes(2);
});

it(
  "a later hard failure keeps the lanes, backs off 1/2/4 s capped at 30 s, a bump runs at once, " +
    "and a success resets",
  async () => {
    const outcomes: Array<"ok" | "fail"> = [
      "ok",
      "fail",
      "fail",
      "fail",
      "fail",
      "fail",
      "fail",
      "ok",
    ];
    const entries = vi.fn<VaultApi["entries"]>().mockImplementation(async () => {
      if (outcomes.shift() === "fail") throw new Error("boom");
      return listingOf(["kanban/a.md"]);
    });
    const metadata = vi
      .fn<NonNullable<VaultApi["metadata"]>>()
      .mockResolvedValue(batchOf([item("kanban/a.md")]));
    const { result } = setup({ api: { entries, metadata } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.state.phase).toBe("ready");
    const ready = result.current.state;
    if (ready.phase !== "ready") throw new Error("unreachable");
    const { listing, items } = ready;

    const expectUnchangedLanes = (): void => {
      const now = result.current.state;
      if (now.phase !== "ready") throw new Error("expected ready");
      expect(now.listing).toBe(listing); // same object: a hard failure never replaces it
      expect(now.items).toBe(items);
    };

    // Hard failure #1 (a manual bump): retry scheduled at 1 s; not yet stale.
    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(entries).toHaveBeenCalledTimes(2);
    expectUnchangedLanes();
    expect(result.current.state).toMatchObject({ stale: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MS - 1);
    });
    expect(entries).toHaveBeenCalledTimes(2); // not yet

    // Hard failure #2, auto-retried at the 1 s mark: now stale, and the next retry is at 2 s.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(entries).toHaveBeenCalledTimes(3);
    expectUnchangedLanes();
    expect(result.current.state).toMatchObject({ stale: true });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * KANBAN_RETRY_MS - 1);
    });
    expect(entries).toHaveBeenCalledTimes(3); // not yet

    // Hard failure #3, at the 2 s mark; next retry at 4 s.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(entries).toHaveBeenCalledTimes(4);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4 * KANBAN_RETRY_MS - 1);
    });
    expect(entries).toHaveBeenCalledTimes(4); // not yet

    // Hard failure #4, at the 4 s mark; this would retry at 8 s, but a bump runs at once instead.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(entries).toHaveBeenCalledTimes(5);
    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(entries).toHaveBeenCalledTimes(6); // hard failure #5, ran at once, not after 8 s
    expectUnchangedLanes();

    // The superseded 8 s timer must not also fire; the bump's own retry is scheduled at 16 s.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8 * KANBAN_RETRY_MS);
    });
    expect(entries).toHaveBeenCalledTimes(6);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8 * KANBAN_RETRY_MS);
    });
    expect(entries).toHaveBeenCalledTimes(7); // hard failure #6, at 16 s

    // failures=6 would compute 32 s; it must cap at KANBAN_RETRY_MAX_MS (30 s), not 32 s.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MAX_MS - 1);
    });
    expect(entries).toHaveBeenCalledTimes(7); // not yet
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(entries).toHaveBeenCalledTimes(8); // the final, scripted success
    expect(result.current.state).toMatchObject({ phase: "ready", stale: false });
    expect(outcomes).toHaveLength(0); // every scripted outcome was consumed, none left over
  },
);

it("marks stale after two consecutive hard failures and clears it on the next success", async () => {
  const entries = vi
    .fn<VaultApi["entries"]>()
    .mockResolvedValueOnce(listingOf(["kanban/a.md"]))
    .mockRejectedValueOnce(new Error("boom"))
    .mockRejectedValueOnce(new Error("boom"))
    .mockResolvedValue(listingOf(["kanban/a.md"]));
  const metadata = vi
    .fn<NonNullable<VaultApi["metadata"]>>()
    .mockResolvedValue(batchOf([item("kanban/a.md")]));
  const { result } = setup({ api: { entries, metadata } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.state).toMatchObject({ phase: "ready", stale: false });

  act(() => result.current.request()); // hard failure #1
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.state).toMatchObject({ phase: "ready", stale: false });

  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MS); // hard failure #2, auto-retried
  });
  expect(result.current.state).toMatchObject({ phase: "ready", stale: true });

  await act(async () => {
    await vi.advanceTimersByTimeAsync(2 * KANBAN_RETRY_MS); // next retry: the scripted success
  });
  expect(result.current.state).toMatchObject({ phase: "ready", stale: false });
});

it("a lane card coded retry schedules an automatic retry on its own", async () => {
  const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf(["kanban/a.md"]));
  // The batch omits the ref entirely: kanbanLanes' unreadOf falls through to code "retry".
  const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockResolvedValue(batchOf([]));
  const { result } = setup({ api: { entries, metadata } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.state.phase).toBe("ready");
  expect(entries).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MS);
  });
  expect(entries).toHaveBeenCalledTimes(2); // retried on its own, with no throw at all
});

it("a lane card coded unsupported does not retry on its own", async () => {
  const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf(["kanban/a.md"]));
  const metadata = vi
    .fn<NonNullable<VaultApi["metadata"]>>()
    .mockResolvedValue(batchOf([item("kanban/a.md", { frontmatter_state: "unsupported" })]));
  const { result } = setup({ api: { entries, metadata } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.state.phase).toBe("ready");
  expect(entries).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MAX_MS);
  });
  expect(entries).toHaveBeenCalledTimes(1); // clean pass, no automatic retry
});

it("reads Bots every pass and keeps the last list when a read fails", async () => {
  const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf([]));
  const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockResolvedValue(batchOf([]));
  const botA = botNamed("a");
  const list = vi
    .fn<BotsApi["list"]>()
    .mockResolvedValueOnce({
      bots: [botA],
      scheduler: { state: "running", attended: true, paused: false },
    })
    .mockRejectedValueOnce(new Error("boom"));
  const { result } = setup({ api: { entries, metadata }, botsApi: { list } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.bots).toEqual([botA]);

  act(() => result.current.request());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(list).toHaveBeenCalledTimes(2);
  expect(result.current.bots).toEqual([botA]); // kept, not cleared, on the failed read
});

it("unmounting aborts the signal and fires no further timer", async () => {
  let capturedSignal: AbortSignal | undefined;
  const entries = vi.fn<VaultApi["entries"]>().mockImplementation(async (_id, _q, signal) => {
    capturedSignal = signal;
    throw new Error("boom");
  });
  const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockResolvedValue(batchOf([]));
  const { unmount } = setup({ api: { entries, metadata } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0); // the first pass fails; a retry is scheduled at 1 s
  });
  expect(entries).toHaveBeenCalledTimes(1);
  expect(capturedSignal?.aborted).toBe(false);

  unmount();
  expect(capturedSignal?.aborted).toBe(true);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MAX_MS);
  });
  expect(entries).toHaveBeenCalledTimes(1); // the pending retry timer never fired
});

it(
  "skips re-reading a working card's note when its listed time is unchanged, reads it once " +
    "that time changes, and reports reading false on the skip-only pass",
  async () => {
    const ref = "kanban/w.md";
    let modified = 100;
    const entries = vi
      .fn<VaultApi["entries"]>()
      .mockImplementation(async () => listingWithModified([{ ref, modified }]));
    const metadata = vi
      .fn<NonNullable<VaultApi["metadata"]>>()
      .mockResolvedValue(batchOf([item(ref, { status: "working" })]));
    const note = vi.fn<NonNullable<VaultApi["note"]>>().mockResolvedValue(NOTE_OK);
    const { result } = setup({ api: { entries, metadata, note } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(note).toHaveBeenCalledTimes(1);

    // Same listed time: a later pass must not re-read it, and has nothing to read at all.
    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(note).toHaveBeenCalledTimes(1);
    expect(result.current.state).toMatchObject({ phase: "ready", reading: false });

    // The listed time changes: the next pass reads it again.
    modified = 200;
    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(note).toHaveBeenCalledTimes(2);
  },
);

it("re-reads a Needs-you card's note every pass even when its listed time is unchanged", async () => {
  const ref = "kanban/b.md";
  const entries = vi
    .fn<VaultApi["entries"]>()
    .mockResolvedValue(listingWithModified([{ ref, modified: 100 }]));
  const metadata = vi
    .fn<NonNullable<VaultApi["metadata"]>>()
    .mockResolvedValue(batchOf([item(ref, { status: "blocked" })]));
  const note = vi.fn<NonNullable<VaultApi["note"]>>().mockResolvedValue(NOTE_OK);
  const { result } = setup({ api: { entries, metadata, note } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(note).toHaveBeenCalledTimes(1);

  act(() => result.current.request());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(note).toHaveBeenCalledTimes(2); // Needs-you is never skipped, same listed time or not
  expect(result.current.state).toMatchObject({ phase: "ready" });
});

it("drops a card's body once it moves outside the body set (e.g. to done)", async () => {
  const ref = "kanban/w.md";
  let status = "working";
  const entries = vi
    .fn<VaultApi["entries"]>()
    .mockResolvedValue(listingWithModified([{ ref, modified: 100 }]));
  const metadata = vi
    .fn<NonNullable<VaultApi["metadata"]>>()
    .mockImplementation(async () => batchOf([item(ref, { status })]));
  const note = vi.fn<NonNullable<VaultApi["note"]>>().mockResolvedValue(NOTE_OK);
  const { result } = setup({ api: { entries, metadata, note } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  if (result.current.state.phase !== "ready") throw new Error("expected ready");
  expect(result.current.state.bodies.has(ref)).toBe(true);

  status = "done";
  act(() => result.current.request());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  if (result.current.state.phase !== "ready") throw new Error("expected ready");
  expect(result.current.state.bodies.has(ref)).toBe(false);
});

it(
  "waitingSince keeps a Needs-you card's first-seen time through a later change, and drops it " +
    "once it leaves",
  async () => {
    const ref = "kanban/b.md";
    let modified = 100;
    let status = "blocked";
    const entries = vi
      .fn<VaultApi["entries"]>()
      .mockImplementation(async () => listingWithModified([{ ref, modified }]));
    const metadata = vi
      .fn<NonNullable<VaultApi["metadata"]>>()
      .mockImplementation(async () => batchOf([item(ref, { status })]));
    const note = vi.fn<NonNullable<VaultApi["note"]>>().mockResolvedValue(NOTE_OK);
    const { result } = setup({ api: { entries, metadata, note } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    if (result.current.state.phase !== "ready") throw new Error("expected ready");
    expect(result.current.state.waitingSince.get(ref)).toBe(100);

    modified = 200; // the card changes while still waiting
    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    if (result.current.state.phase !== "ready") throw new Error("expected ready");
    expect(result.current.state.waitingSince.get(ref)).toBe(100); // keeps the first time, not 200

    status = "working"; // the card leaves Needs-you
    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    if (result.current.state.phase !== "ready") throw new Error("expected ready");
    expect(result.current.state.waitingSince.has(ref)).toBe(false);
  },
);

it("a throw escaping the pass (e.g. a kanbanBodyRefs bug) still releases running for the next request", async () => {
  vi.mocked(kanbanBodyRefs).mockImplementationOnce(() => {
    throw new Error("boom");
  });
  const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf(["kanban/a.md"]));
  const metadata = vi
    .fn<NonNullable<VaultApi["metadata"]>>()
    .mockResolvedValue(batchOf([item("kanban/a.md")]));
  const { result } = setup({ api: { entries, metadata } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.state).toEqual({ phase: "loading" }); // the buggy pass never committed
  expect(entries).toHaveBeenCalledTimes(1);

  act(() => result.current.request());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(entries).toHaveBeenCalledTimes(2); // running was released; the next request actually ran
  expect(result.current.state.phase).toBe("ready");
});

it(
  "applies the Bots list as soon as it resolves, while bodies are still being read, not only " +
    "once the whole body loop ends (F1, PA inverted)",
  async () => {
    const ref = "kanban/b.md";
    const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf([ref]));
    const metadata = vi
      .fn<NonNullable<VaultApi["metadata"]>>()
      .mockResolvedValue(batchOf([item(ref, { status: "blocked" })]));
    const heldNote = deferred<Record<string, unknown>>();
    const note = vi.fn<NonNullable<VaultApi["note"]>>().mockReturnValue(heldNote.promise);
    const botA = botNamed("a");
    const list = vi.fn<BotsApi["list"]>().mockResolvedValue({
      bots: [botA],
      scheduler: { state: "running", attended: true, paused: false },
    });
    const { result } = setup({ api: { entries, metadata, note }, botsApi: { list } });

    await act(async () => {
      // The commit lands and the body read for `ref` starts and stays held; the Bots list, started
      // alongside it, resolves on its own and must already be visible here, mid-loop.
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(note).toHaveBeenCalledTimes(1);
    expect(result.current.state).toMatchObject({ phase: "ready", reading: true });
    expect(result.current.bots).toEqual([botA]);

    heldNote.resolve(NOTE_OK);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.state).toMatchObject({ phase: "ready", reading: false });
  },
);

it("fires no unhandled rejection when unmounting while the Bots list is still in flight (F1, PB)", async () => {
  // Plain functions, not vi.fn: a vi.fn() spy attaches its own handler to the promise it returns
  // (to track mock.results), which would quietly mark the rejection handled and hide the exact
  // bug this checks for — unlike the real vaultApi/botsApi, both stay pending until the signal
  // aborts, so the pass is still awaiting `entries()` (botsPending started alongside it, never
  // yet awaited) when unmount fires.
  const events = process as unknown as {
    on(event: "unhandledRejection", listener: (reason: unknown) => void): void;
    off(event: "unhandledRejection", listener: (reason: unknown) => void): void;
  };
  const seen: string[] = [];
  const onRejection = (reason: unknown): void => {
    seen.push(String(reason));
  };
  events.on("unhandledRejection", onRejection);
  try {
    let listCalls = 0;
    const entries = (_id: string, _q: unknown, signal?: AbortSignal) =>
      abortable<Record<string, unknown>>(signal);
    const metadata = async () => batchOf([]);
    const list = (signal?: AbortSignal) => {
      listCalls += 1;
      return abortable<Awaited<ReturnType<BotsApi["list"]>>>(signal);
    };
    const { unmount } = setup({ api: { entries, metadata }, botsApi: { list } });
    expect(listCalls).toBe(1);

    unmount(); // aborts the signal: entries() and the list's own fetch both reject "cancelled"
    for (let k = 0; k < 3; k++) {
      await nextMacrotask();
    }
  } finally {
    events.off("unhandledRejection", onRejection);
  }
  expect(seen).toEqual([]);
});

it("a pass in flight at unmount never runs again (F3, PD)", async () => {
  const entries = vi
    .fn<VaultApi["entries"]>()
    .mockImplementation((_id, _q, signal) => abortable(signal));
  const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockResolvedValue(batchOf([]));
  const { unmount } = setup({ api: { entries, metadata } });
  expect(entries).toHaveBeenCalledTimes(1);

  unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MAX_MS * 2);
  });
  expect(entries).toHaveBeenCalledTimes(1); // the aborted pass never retries from the dead runner
});

it("a clean pass after a failed one schedules nothing more (F3, PE)", async () => {
  const entries = vi
    .fn<VaultApi["entries"]>()
    .mockRejectedValueOnce(new Error("boom"))
    .mockResolvedValue(listingOf([]));
  const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockResolvedValue(batchOf([]));
  setup({ api: { entries, metadata } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MS); // the auto-retry succeeds: failures -> 0
  });
  expect(entries).toHaveBeenCalledTimes(2);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MAX_MS * 2);
  });
  expect(entries).toHaveBeenCalledTimes(2); // a recovered pass must not keep re-reading forever
});

it("a body read that throws retries the whole pass after 1 s (F3, PF)", async () => {
  const entries = vi
    .fn<VaultApi["entries"]>()
    .mockResolvedValue(listingWithModified([{ ref: "kanban/w.md", modified: 1 }]));
  const metadata = vi
    .fn<NonNullable<VaultApi["metadata"]>>()
    .mockResolvedValue(batchOf([item("kanban/w.md", { status: "working" })]));
  const note = vi
    .fn<NonNullable<VaultApi["note"]>>()
    .mockRejectedValueOnce(new Error("boom"))
    .mockResolvedValue(NOTE_OK);
  setup({ api: { entries, metadata, note } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(note).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MS);
  });
  expect(entries).toHaveBeenCalledTimes(2); // a stopped body loop alone must still retry the pass
  expect(note).toHaveBeenCalledTimes(2);
});

it(
  "waitingSince keeps a Needs-you card's time through a pass where it is durably unread — not " +
    "an F4 transient code — and the F2 unread-lane rule alone covers it",
  async () => {
    const b = "kanban/b.md";
    const a = "kanban/a.md";
    const statuses = ["blocked", "unsupported", "blocked"]; // pass 1, 2 (durable unread), 3
    let call = 0;
    const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(
      listingWithModified([
        { ref: b, modified: 100 },
        { ref: a, modified: 150 },
      ]),
    );
    const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockImplementation(async () => {
      const status = statuses[Math.min(call, statuses.length - 1)] ?? "blocked";
      call += 1;
      const bItem =
        status === "unsupported"
          ? item(b, { frontmatter_state: "unsupported" })
          : item(b, { status: "blocked" });
      return batchOf([bItem, item(a, { status: "blocked" })]);
    });
    const { result } = setup({ api: { entries, metadata } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    if (result.current.state.phase !== "ready") throw new Error("expected ready");
    expect(result.current.state.waitingSince.get(b)).toBe(100);

    // Pass 2: b's own frontmatter becomes unsupported — durably unread, not one of F4's transient
    // codes, so F4 never substitutes it back; only F2's unread-lane rule can keep its place.
    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    if (result.current.state.phase !== "ready") throw new Error("expected ready");
    expect(result.current.state.waitingSince.get(b)).toBe(100); // kept despite being unread now

    // Pass 3: b recovers; its place (100), not a fresh one, is what it comes back with.
    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    if (result.current.state.phase !== "ready") throw new Error("expected ready");
    expect(result.current.state.waitingSince.get(b)).toBe(100);
  },
);

it(
  "a Needs-you card's transient 'changed' metadata keeps its last readable item, retries after " +
    "1 s, and the next item lands (F4)",
  async () => {
    const ref = "kanban/b.md";
    const responses: VaultMetadataItem[] = [
      item(ref, { status: "blocked" }),
      { ref, state: "changed", reason: null, read_bytes: 1, display_metadata: null },
      item(ref, { status: "working" }),
    ];
    let call = 0;
    const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf([ref]));
    const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockImplementation(async () => {
      const next = responses[Math.min(call, responses.length - 1)];
      call += 1;
      return batchOf(next ? [next] : []);
    });
    const { result } = setup({ api: { entries, metadata } });
    const needsYouRefs = (): string[] => {
      if (result.current.state.phase !== "ready") throw new Error("expected ready");
      const { listing, items } = result.current.state;
      return kanbanNeedsYou(kanbanLanes(listing, items, 0), listing).map((card) => card.ref);
    };

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(needsYouRefs()).toEqual([ref]); // pass 1: blocked, a Needs-you card

    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(metadata).toHaveBeenCalledTimes(2);
    expect(needsYouRefs()).toEqual([ref]); // pass 2: "changed" kept the last readable (blocked) item

    await act(async () => {
      await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MS - 1);
    });
    expect(metadata).toHaveBeenCalledTimes(2); // not yet

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(metadata).toHaveBeenCalledTimes(3); // pass 3, auto-retried at 1 s
    expect(needsYouRefs()).toEqual([]); // the fresh "working" item landed; no longer Needs-you
    if (result.current.state.phase !== "ready") throw new Error("expected ready");
    const lanes = kanbanLanes(result.current.state.listing, result.current.state.items, 0);
    expect(lanes.find((lane) => lane.id === "working")?.cards.map((card) => card.ref)).toEqual([
      ref,
    ]);
  },
);

it(
  "a transient miss that repeats keeps the last readable item for one pass only, then shows " +
    "the card as unread and stops retrying",
  async () => {
    const ref = "kanban/b.md";
    const miss: VaultMetadataItem = {
      ref,
      state: "unreadable",
      reason: "read_failed",
      read_bytes: 0,
      display_metadata: null,
    };
    const responses: VaultMetadataItem[] = [item(ref, { status: "blocked" }), miss];
    let call = 0;
    const entries = vi.fn<VaultApi["entries"]>().mockResolvedValue(listingOf([ref]));
    const metadata = vi.fn<NonNullable<VaultApi["metadata"]>>().mockImplementation(async () => {
      const next = responses[Math.min(call, responses.length - 1)];
      call += 1;
      return batchOf(next ? [next] : []);
    });
    const { result } = setup({ api: { entries, metadata } });
    const laneOf = (): string | undefined => {
      if (result.current.state.phase !== "ready") throw new Error("expected ready");
      const { listing, items } = result.current.state;
      return kanbanLanes(listing, items, 0).find((lane) =>
        lane.cards.some((card) => card.ref === ref),
      )?.id;
    };

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(laneOf()).toBe("blocked"); // pass 1: readable

    act(() => result.current.request());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(metadata).toHaveBeenCalledTimes(2);
    expect(laneOf()).toBe("blocked"); // pass 2: the miss keeps the last readable item

    await act(async () => {
      await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MS);
    });
    expect(metadata).toHaveBeenCalledTimes(3); // pass 3: the retry
    expect(laneOf()).toBe("unread"); // the repeated miss is not covered again

    await act(async () => {
      await vi.advanceTimersByTimeAsync(KANBAN_RETRY_MAX_MS * 2);
    });
    expect(metadata).toHaveBeenCalledTimes(3); // an unreadable card is not retried
  },
);
