/**
 * useCardAnswer: writes an owner's Kanban answer back onto its card, holds the result open for a
 * 10 s undo, then (Decide/Reply/Send back only, never Approve) wakes the Bot that owns the card so
 * it can continue.
 *
 * At most one save is in flight, and at most one undo window is open, at a time, both guarded by a
 * ref set synchronously at the top of `answer` (`busyRef`) so a second call cannot slip in while
 * the first is still awaiting anything, finishing an earlier window's wake included. Calling
 * `answer` again while a window is open finishes that window first — its wake runs right away
 * rather than waiting out the rest of the 10 s — before starting the new one. Unmounting does the
 * same, whether a window was already open or its save was still settling: the wake still has to
 * reach the Bot, but nothing is left to show the result to, so every state update is skipped.
 * `write`, `wake`, `onSettled` and `nowMs` are read through refs, kept current every render, so
 * `answer` and `undo` keep one identity across renders.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { BotRow } from "../../clients/messaging/botsApi";
import type { VaultApi } from "../../state/vaultApi";
import { answerCard, type CardAnswer, type CardRead, cardDate } from "./kanbanBody";
import type { KanbanCard, KanbanNotice, WakeTarget } from "./kanbanCards";

/** How long a save can still be undone before its Bot (if any) is woken. */
export const UNDO_MS = 10_000;

export type WakeResult = "sent" | "busy" | "failed";

export interface KanbanWrite {
  api: Pick<VaultApi, "saveNote">;
  workspaceId: string;
  policyRevision: number;
  rootPhysicalId: string;
}

export interface UseCardAnswerOptions {
  write: KanbanWrite | null;
  wake?: (bot: BotRow, ref: string) => Promise<WakeResult>;
  onSettled: () => void;
  nowMs?: number;
}

export interface UseCardAnswerResult {
  notice: KanbanNotice | null;
  saving: boolean;
  lockedRef: string | null;
  answer: (
    card: KanbanCard,
    read: CardRead,
    target: WakeTarget,
    answer: CardAnswer,
  ) => Promise<void>;
  undo: () => Promise<void>;
}

/** Same technique as `state/conversationSearch.ts`'s private `revision` helper: a lowercase hex
 * SHA-256, the form `VaultApi.saveNote`'s `expectedSha256` takes. */
async function sha256Hex(content: string): Promise<string> {
  const bytes = new TextEncoder().encode(content);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** What a successful save is holding open: enough to undo it, and what to do once it finishes.
 * `title` is the card's own display title, carried through for `finish`/`undo` to put on a
 * notice — the hook holds no user-facing text itself; `frontend/src/i18n/kanban.ts` composes the
 * message shown around it (and around `botName(notice.bot)` for a Bot-naming notice). */
interface OpenWindow {
  ref: string;
  title: string;
  previous: string;
  writtenSha: string;
  isApprove: boolean;
  target: WakeTarget;
}

export function useCardAnswer({
  write,
  wake,
  onSettled,
  nowMs,
}: UseCardAnswerOptions): UseCardAnswerResult {
  const [notice, setNotice] = useState<KanbanNotice | null>(null);
  const [saving, setSaving] = useState(false);
  const [lockedRef, setLockedRef] = useState<string | null>(null);

  const writeRef = useRef(write);
  writeRef.current = write;
  const wakeRef = useRef(wake);
  wakeRef.current = wake;
  const onSettledRef = useRef(onSettled);
  onSettledRef.current = onSettled;
  const nowMsRef = useRef(nowMs);
  nowMsRef.current = nowMs;

  const openRef = useRef<OpenWindow | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  // Checked and set synchronously at the top of `answer`, before any `await`: `saving` (React
  // state) only flips on the next render, so a second call arriving while the first is still
  // finishing an earlier window — awaiting that window's own wake — would otherwise read the old,
  // stale `saving` and slip through the same instant the first call is working with `openRef` and
  // `timerRef`.
  const busyRef = useRef(false);

  const clearTimer = useCallback((): void => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  /** Ends the open window: Approve just drops its undo; Decide/Reply/Send back wake the target's
   * Bot, if any. Safe to call with no window open (the natural case once undo or a timer already
   * ended it); every state update is skipped once `mountedRef` is false (unmount; see module doc). */
  const finish = useCallback(async (): Promise<void> => {
    const open = openRef.current;
    if (!open) return;
    openRef.current = null;
    clearTimer();
    const settle = (next: KanbanNotice): void => {
      if (!mountedRef.current) return;
      setLockedRef(null);
      setNotice(next);
      onSettledRef.current();
    };
    if (open.isApprove) {
      settle({ kind: "saved", title: open.title, undo: false });
      return;
    }
    const { target } = open;
    if (target === null) {
      settle({ kind: "no_owner", title: open.title });
      return;
    }
    if ("seat" in target) {
      settle({ kind: "no_bot", title: open.title, seat: target.seat });
      return;
    }
    const { bot } = target;
    const wakeFn = wakeRef.current;
    let result: WakeResult = "failed";
    if (wakeFn) {
      try {
        result = await wakeFn(bot, open.ref);
      } catch {
        result = "failed";
      }
    }
    if (result === "sent") {
      settle({ kind: "woke", title: open.title, bot });
    } else if (result === "busy") {
      settle({ kind: "busy", title: open.title, bot });
    } else {
      settle({ kind: "wake_failed", title: open.title, bot });
    }
  }, [clearTimer]);

  // Unmount only: a window still open gets finished at once rather than left to its timer, which
  // would fire after this hook is gone. `finish` still wakes the Bot but touches no state (module
  // doc).
  useEffect(() => {
    mountedRef.current = true; // StrictMode's development remount runs the cleanup below once first
    return () => {
      mountedRef.current = false;
      void finish();
    };
  }, [finish]);

  const undo = useCallback(async (): Promise<void> => {
    const open = openRef.current;
    if (!open) return;
    openRef.current = null;
    clearTimer();
    const kw = writeRef.current;
    let status: "saved" | "no_change" | "conflict" | null = null;
    if (kw) {
      try {
        status = (
          await kw.api.saveNote(
            kw.workspaceId,
            kw.policyRevision,
            kw.rootPhysicalId,
            open.ref,
            open.previous,
            open.writtenSha,
          )
        ).status;
      } catch {
        status = null;
      }
    }
    if (!mountedRef.current) return;
    setLockedRef(null);
    setNotice(
      status === "saved"
        ? { kind: "undone", title: open.title }
        : { kind: "undo_refused", title: open.title },
    );
    onSettledRef.current();
  }, [clearTimer]);

  const answer = useCallback(
    async (
      card: KanbanCard,
      read: CardRead,
      target: WakeTarget,
      value: CardAnswer,
    ): Promise<void> => {
      const kw = writeRef.current;
      if (!kw || busyRef.current) return;
      busyRef.current = true;
      setSaving(true);
      try {
        if (openRef.current) await finish();
        const next = answerCard(
          read.source,
          value,
          cardDate(nowMsRef.current ?? Date.now()),
          card.owners[0] ?? null,
        );
        const outcome = await kw.api.saveNote(
          kw.workspaceId,
          kw.policyRevision,
          kw.rootPhysicalId,
          card.ref,
          next,
          read.sha256,
        );
        if (outcome.status === "conflict") {
          if (mountedRef.current) {
            setSaving(false);
            setNotice({ kind: "changed", title: card.title });
          }
          return;
        }
        const writtenSha = await sha256Hex(next);
        // The save already happened; the window is recorded regardless of mount state so its
        // wake is never dropped, even when the component unmounted while this save, or the sha
        // above, was still in flight.
        openRef.current = {
          ref: card.ref,
          title: card.title,
          previous: read.source,
          writtenSha,
          isApprove: value.kind === "approve",
          target,
        };
        if (!mountedRef.current) {
          void finish();
          return;
        }
        setSaving(false);
        setLockedRef(card.ref);
        setNotice({ kind: "saved", title: card.title, undo: true });
        timerRef.current = setTimeout(() => {
          // The timer, not `answer`, is what starts this `finish`, so nothing has set the busy
          // guard yet: without it, a second answer could run while this one's wake (seconds, once
          // the Bot's process has to start) is still in flight, and the Undo button would stay
          // enabled (it reads `saving`) for a window that already ended underneath it.
          busyRef.current = true;
          setSaving(true);
          void finish().finally(() => {
            busyRef.current = false;
            if (mountedRef.current) setSaving(false);
          });
        }, UNDO_MS);
      } catch {
        if (mountedRef.current) {
          setSaving(false);
          setNotice({ kind: "failed", title: card.title });
        }
      } finally {
        busyRef.current = false;
      }
    },
    [finish],
  );

  return { notice, saving, lockedRef, answer, undo };
}
