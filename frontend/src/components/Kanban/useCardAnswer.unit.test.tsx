import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { BotRow } from "../../clients/messaging/botsApi";
import type { VaultApi, VaultSaveOutcome } from "../../state/vaultApi";
import type { CardAnswer, CardRead } from "./kanbanBody";
import type { KanbanCard, WakeTarget } from "./kanbanCards";
import { type KanbanWrite, UNDO_MS, useCardAnswer, type WakeResult } from "./useCardAnswer";

const WORKSPACE = "11111111-0000-4000-8000-000000000001";
const POLICY = 1;
const ROOT = "root-1";
const REF = "kanban/card-1.md";
const SHA_READ = "a".repeat(64);
const NOW_MS = new Date(2026, 10, 19, 9, 0).getTime();
const CARD_TITLE = "Example card";
const BOT = bot("b1", "p1", "Chief");

function bot(id: string, profileId: string, name: string | null, workspace = "w1"): BotRow {
  return {
    bot_id: id,
    profile_id: profileId,
    workspace_id: workspace,
    profile: name
      ? {
          revision: 1,
          archived: false,
          identity: { name, purpose: "", avatar_asset_id: null, accent: "slate", shape: "rounded" },
        }
      : null,
  } as BotRow;
}

function card(ref: string, owners: string[] = ["chief"], title = CARD_TITLE): KanbanCard {
  return { ref, title, owners, status: "blocked", ageDays: 1, stale: false, unread: null };
}

function sourceText(): string {
  return (
    "---\n" +
    "type: card\n" +
    "status: blocked\n" +
    "modified: 2026-11-01\n" +
    "---\n" +
    "## Needs you\n" +
    "Which approach?\n" +
    "- A. Approach one\n" +
    "- B. Approach two (recommended)\n" +
    "\n" +
    "## Done when\n" +
    "- [ ] one thing\n"
  );
}

function cardRead(sha256: string): CardRead {
  return {
    source: sourceText(),
    sha256,
    body: { what: null, now: null, needs: null, done: null, verdict: null },
  };
}

function decide(): CardAnswer {
  return {
    kind: "decide",
    option: { letter: "A", label: "Approach one", recommended: false, effect: null },
  };
}

function outcome(status: VaultSaveOutcome["status"]): VaultSaveOutcome {
  return { operation_id: "op-1", status, cleanup_pending: false };
}

function writeOf(saveNote: VaultApi["saveNote"]): KanbanWrite {
  return {
    api: { saveNote },
    workspaceId: WORKSPACE,
    policyRevision: POLICY,
    rootPhysicalId: ROOT,
  };
}

async function sha256Hex(content: string): Promise<string> {
  const bytes = new TextEncoder().encode(content);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** A promise this test resolves or rejects by hand, to hold a `wake()` or `saveNote()` call open
 * across several `answer()`/`undo()` calls (R6-R8's concurrency and lifecycle probes). */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

it("answer's save uses the read's own sha256 as the expected one", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  const read = cardRead(SHA_READ);
  await act(async () => {
    await result.current.answer(card(REF), read, null, decide());
  });
  expect(saveNote).toHaveBeenCalledTimes(1);
  expect(saveNote.mock.calls[0]?.[5]).toBe(SHA_READ);
});

it("a conflicted save settles changed, with the card's own title, and never opens an undo window or wakes", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("conflict"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>();
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  expect(result.current.notice).toEqual({ kind: "changed", title: CARD_TITLE });
  expect(result.current.lockedRef).toBeNull();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(wake).not.toHaveBeenCalled();
});

it("settles failed, with the card's own title, when the save throws, and never opens a window", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockRejectedValue(new Error("boom"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>();
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  expect(result.current.notice).toEqual({ kind: "failed", title: CARD_TITLE });
  expect(result.current.saving).toBe(false);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(wake).not.toHaveBeenCalled();
});

it("is a no-op without a write target", async () => {
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>();
  const { result } = renderHook(() =>
    useCardAnswer({ write: null, wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  expect(result.current.notice).toBeNull();
  expect(wake).not.toHaveBeenCalled();
});

it("is a no-op while an earlier save has not resolved yet", async () => {
  let resolveFirst!: (value: VaultSaveOutcome) => void;
  const saveNote = vi.fn<VaultApi["saveNote"]>(
    () =>
      new Promise((resolve) => {
        resolveFirst = resolve;
      }),
  );
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  act(() => {
    void result.current.answer(card(REF), cardRead(SHA_READ), null, decide());
  });
  expect(result.current.saving).toBe(true);
  await act(async () => {
    await result.current.answer(card("kanban/other.md"), cardRead(SHA_READ), null, decide());
  });
  expect(saveNote).toHaveBeenCalledTimes(1);
  await act(async () => {
    resolveFirst(outcome("saved"));
    await Promise.resolve();
  });
});

it("a third answer is rejected while a second is still finishing an earlier window's wake, and only the two that answered are saved (R6, probe A)", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const botY = bot("by", "py", "Ops");
  const botZ = bot("bz", "pz", "Support");
  const wakeX = deferred<WakeResult>();
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>((b) =>
    b === BOT ? wakeX.promise : Promise.resolve("sent"),
  );
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card("kanban/x.md", ["chief"], "Card X"),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });

  // Y answers while X's window is open: this finishes X's window first, which calls wake(X) —
  // held pending by wakeX — before Y's own save runs.
  let yDone!: Promise<void>;
  act(() => {
    yDone = result.current.answer(
      card("kanban/y.md", ["chief"], "Card Y"),
      cardRead(SHA_READ),
      { bot: botY, name: "Ops" },
      decide(),
    );
  });
  // While Y is still finishing X's window, a third answer for Z must be rejected outright.
  await act(async () => {
    await result.current.answer(
      card("kanban/z.md", ["chief"], "Card Z"),
      cardRead(SHA_READ),
      { bot: botZ, name: "Support" },
      decide(),
    );
  });

  wakeX.resolve("sent");
  await act(async () => {
    await yDone;
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS); // lets Y's own window finish and wake botY
  });

  expect(saveNote.mock.calls.map((call) => call[3]).sort()).toEqual(["kanban/x.md", "kanban/y.md"]);
  expect(wake).toHaveBeenCalledTimes(2);
  expect(wake).toHaveBeenCalledWith(BOT, "kanban/x.md");
  expect(wake).toHaveBeenCalledWith(botY, "kanban/y.md");
  expect(result.current.notice).toEqual({ kind: "woke", title: "Card Y", bot: botY });
});

it("double-answering the same card while an earlier window's wake runs saves it only once (R6, probe B)", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const botY = bot("by", "py", "Ops");
  const wakeX = deferred<WakeResult>();
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>((b) =>
    b === BOT ? wakeX.promise : Promise.resolve("sent"),
  );
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card("kanban/x.md", ["chief"], "Card X"),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });

  let firstY!: Promise<void>;
  act(() => {
    firstY = result.current.answer(
      card("kanban/y.md", ["chief"], "Card Y"),
      cardRead(SHA_READ),
      { bot: botY, name: "Ops" },
      decide(),
    );
  });
  // A second click on the very same card while the first is still finishing X's window.
  await act(async () => {
    await result.current.answer(
      card("kanban/y.md", ["chief"], "Card Y"),
      cardRead(SHA_READ),
      { bot: botY, name: "Ops" },
      decide(),
    );
  });

  wakeX.resolve("sent");
  await act(async () => {
    await firstY;
  });

  expect(saveNote.mock.calls.filter((call) => call[3] === "kanban/y.md")).toHaveLength(1);
  expect(result.current.notice).toEqual({ kind: "saved", title: "Card Y", undo: true });
  expect(result.current.lockedRef).toBe("kanban/y.md");
});

it("a second card is rejected while an earlier window's timer-triggered wake is still running, and can be answered once it settles (R6 remainder, probe F)", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const botY = bot("by", "py", "Ops");
  const wakeX = deferred<WakeResult>();
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>((b) =>
    b === BOT ? wakeX.promise : Promise.resolve("sent"),
  );
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card("kanban/x.md", ["chief"], "Card X"),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });

  // The 10 s timer, not a click, finishes X's window: finish() clears openRef and starts wake(X),
  // held pending by wakeX.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(result.current.saving).toBe(true); // the UI's Undo button must disable here too
  expect(result.current.lockedRef).toBe("kanban/x.md"); // X still shows locked, wake not done yet

  await act(async () => {
    await result.current.answer(
      card("kanban/y.md", ["chief"], "Card Y"),
      cardRead(SHA_READ),
      { bot: botY, name: "Ops" },
      decide(),
    );
  });
  expect(saveNote).toHaveBeenCalledTimes(1); // Y was rejected, not saved
  expect(result.current.lockedRef).toBe("kanban/x.md"); // still X — Y never opened a window

  wakeX.resolve("sent");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0); // flushes finish()'s now-resolved wake
  });
  expect(result.current.saving).toBe(false); // busyRef/saving released once finish() settles
  expect(result.current.notice).toEqual({ kind: "woke", title: "Card X", bot: BOT });
  expect(result.current.lockedRef).toBeNull();

  // Busy is free again: Y can now be answered for real.
  await act(async () => {
    await result.current.answer(
      card("kanban/y.md", ["chief"], "Card Y"),
      cardRead(SHA_READ),
      { bot: botY, name: "Ops" },
      decide(),
    );
  });
  expect(saveNote).toHaveBeenCalledTimes(2);
  expect(result.current.lockedRef).toBe("kanban/y.md");
});

it("undo during an earlier window's timer-triggered wake does nothing, since the window already ended (R6 remainder, probe G)", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wakeX = deferred<WakeResult>();
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>(() => wakeX.promise);
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS); // the timer fires; finish() starts wake(X)
  });
  // The UI's Undo button reads `saving` to decide whether it is enabled; it must be disabled
  // here, even though `notice` still shows the pre-finish "saved" with undo: true.
  expect(result.current.saving).toBe(true);
  expect(result.current.notice).toEqual({ kind: "saved", title: CARD_TITLE, undo: true });

  await act(async () => {
    await result.current.undo(); // the window already ended: a click here must do nothing
  });
  expect(saveNote).toHaveBeenCalledTimes(1); // no write-back happened
  expect(result.current.notice).toEqual({ kind: "saved", title: CARD_TITLE, undo: true }); // unchanged

  wakeX.resolve("sent");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.saving).toBe(false);
  expect(result.current.notice).toEqual({ kind: "woke", title: CARD_TITLE, bot: BOT });
});

it("undo within the window writes the previous text back with the written sha, keeps the card's title, and never wakes", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>().mockResolvedValue("sent");
  const read = cardRead(SHA_READ);
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(card(REF), read, { bot: BOT, name: "Chief" }, decide());
  });
  const next = saveNote.mock.calls[0]?.[4] as string;
  const writtenSha = await sha256Hex(next);
  expect(result.current.lockedRef).toBe(REF);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(9000);
  });
  await act(async () => {
    await result.current.undo();
  });
  expect(saveNote).toHaveBeenCalledTimes(2);
  expect(saveNote.mock.calls[1]).toEqual([WORKSPACE, POLICY, ROOT, REF, read.source, writtenSha]);
  expect(result.current.notice).toEqual({ kind: "undone", title: CARD_TITLE });
  expect(result.current.lockedRef).toBeNull();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000); // well past the original 10 s mark
  });
  expect(wake).not.toHaveBeenCalled();
});

it("settles undo_refused, with the card's own title, when the write-back does not report saved", async () => {
  const saveNote = vi
    .fn<VaultApi["saveNote"]>()
    .mockResolvedValueOnce(outcome("saved"))
    .mockResolvedValueOnce(outcome("conflict"));
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(card(REF), cardRead(SHA_READ), null, decide());
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(9000);
  });
  await act(async () => {
    await result.current.undo();
  });
  expect(result.current.notice).toEqual({ kind: "undo_refused", title: CARD_TITLE });
});

it("wakes the target Bot exactly once once the undo window elapses, and keeps the card's title", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>().mockResolvedValue("sent");
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  expect(wake).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(wake).toHaveBeenCalledTimes(1);
  expect(wake).toHaveBeenCalledWith(BOT, REF);
  expect(result.current.notice).toEqual({ kind: "woke", title: CARD_TITLE, bot: BOT });
});

it("Approve never wakes, even with a Bot target, once its window elapses, and keeps the card's title", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>();
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      { kind: "approve" },
    );
  });
  expect(result.current.notice).toEqual({ kind: "saved", title: CARD_TITLE, undo: true });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(wake).not.toHaveBeenCalled();
  expect(result.current.notice).toEqual({ kind: "saved", title: CARD_TITLE, undo: false });
});

it("settles no_owner, with the card's own title, once the window elapses on a card with no owner", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>();
  const onSettled = vi.fn();
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled, nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(card(REF), cardRead(SHA_READ), null, decide());
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(result.current.notice).toEqual({ kind: "no_owner", title: CARD_TITLE });
  expect(wake).not.toHaveBeenCalled();
  expect(onSettled).toHaveBeenCalledTimes(1);
});

it("settles no_bot, with the card's own title and the seat, when the owner's seat has no Bot", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>();
  const target: WakeTarget = { seat: "ops" };
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(card(REF), cardRead(SHA_READ), target, decide());
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(result.current.notice).toEqual({ kind: "no_bot", seat: "ops", title: CARD_TITLE });
  expect(wake).not.toHaveBeenCalled();
});

it.each([
  ["sent", "woke"],
  ["busy", "busy"],
  ["failed", "wake_failed"],
] satisfies [WakeResult, string][])(
  "a wake() that resolves %s settles %s, with the card's own title (never a message)",
  async (resolved, kind) => {
    const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
    const wake = vi
      .fn<(bot: BotRow, ref: string) => Promise<WakeResult>>()
      .mockResolvedValue(resolved);
    const { result } = renderHook(() =>
      useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
    );
    await act(async () => {
      await result.current.answer(
        card(REF),
        cardRead(SHA_READ),
        { bot: BOT, name: "Chief" },
        decide(),
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(UNDO_MS);
    });
    expect(result.current.notice).toEqual({ kind, title: CARD_TITLE, bot: BOT });
  },
);

it("settles wake_failed, with the card's own title, when the wake call rejects", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi
    .fn<(bot: BotRow, ref: string) => Promise<WakeResult>>()
    .mockRejectedValue(new Error("network"));
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(result.current.notice).toEqual({ kind: "wake_failed", title: CARD_TITLE, bot: BOT });
});

it("settles wake_failed, with the card's own title, with no wake() to call at all", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(result.current.notice).toEqual({ kind: "wake_failed", title: CARD_TITLE, bot: BOT });
});

it("answering a second card finishes the first's open window immediately, and the notice ends with the second card's own title", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>().mockResolvedValue("sent");
  const bot2 = bot("b2", "p2", "Ops");
  const { result } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card("kanban/a.md", ["chief"], "Card A"),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  await act(async () => {
    await result.current.answer(
      card("kanban/b.md", ["chief"], "Card B"),
      cardRead(SHA_READ),
      { bot: bot2, name: "Ops" },
      decide(),
    );
  });
  expect(wake).toHaveBeenCalledTimes(1);
  expect(wake).toHaveBeenCalledWith(BOT, "kanban/a.md");
  expect(result.current.lockedRef).toBe("kanban/b.md");
  expect(result.current.notice).toEqual({ kind: "saved", title: "Card B", undo: true });
});

it("unmounting mid-window finishes it at once, waking the target", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>().mockResolvedValue("sent");
  const { result, unmount } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  expect(wake).not.toHaveBeenCalled();
  unmount();
  expect(wake).toHaveBeenCalledTimes(1);
  expect(wake).toHaveBeenCalledWith(BOT, REF);
});

it("unmounting while the save is still in flight still wakes the target once it resolves (R7, probe C)", async () => {
  const pending = deferred<VaultSaveOutcome>();
  const saveNote = vi.fn<VaultApi["saveNote"]>(() => pending.promise);
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>().mockResolvedValue("sent");
  const { result, unmount } = renderHook(() =>
    useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
  );
  let answered!: Promise<void>;
  act(() => {
    answered = result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  unmount();
  pending.resolve(outcome("saved"));
  await act(async () => {
    await answered;
  });
  expect(saveNote).toHaveBeenCalledTimes(1);
  expect(wake).toHaveBeenCalledTimes(1);
  expect(wake).toHaveBeenCalledWith(BOT, REF);
});

it("works under StrictMode's dev-only double-invoked mount effect (R8, probe D)", async () => {
  const saveNote = vi.fn<VaultApi["saveNote"]>().mockResolvedValue(outcome("saved"));
  const wake = vi.fn<(bot: BotRow, ref: string) => Promise<WakeResult>>().mockResolvedValue("sent");
  const { result } = renderHook(
    () => useCardAnswer({ write: writeOf(saveNote), wake, onSettled: vi.fn(), nowMs: NOW_MS }),
    { reactStrictMode: true },
  );
  await act(async () => {
    await result.current.answer(
      card(REF),
      cardRead(SHA_READ),
      { bot: BOT, name: "Chief" },
      decide(),
    );
  });
  expect(result.current.saving).toBe(false);
  expect(result.current.notice).toEqual({ kind: "saved", title: CARD_TITLE, undo: true });
  expect(result.current.lockedRef).toBe(REF);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(UNDO_MS);
  });
  expect(wake).toHaveBeenCalledTimes(1);
  expect(wake).toHaveBeenCalledWith(BOT, REF);
});
