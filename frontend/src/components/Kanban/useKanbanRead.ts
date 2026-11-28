/**
 * useKanbanRead: the Kanban read loop. One pass (listing, metadata batches, bodies one at a time,
 * Bots list) runs at a time; a `request()` while a pass is running queues exactly one more rather
 * than aborting the pass in flight, so changes faster than one pass can't starve it.
 *
 * A pass only reads a held, non-Needs-you body again when its listed modified time moved; a
 * Needs-you body is always read fresh. `bodies` and the runner-local `bodyAt` (each body's last
 * read-succeeded modified time) are trimmed at commit to exactly `kanbanBodyRefs`, so a card that
 * leaves the body set (e.g. to done) loses its cached body. `waitingSince` tracks, per Needs-you
 * card, the first modified time Kanban saw it waiting at — kept through later edits, dropped once
 * the card stops waiting (or, if it is only transiently unread this pass — see `lastReadable`
 * below — kept through the unread lane too, so it does not lose its place before `kanbanLanes`
 * even sees it return).
 *
 * On a later pass, a ref whose fresh metadata item reports a transient mid-write hiccup (`state`
 * `changed`/`unknown`, or `unreadable` with `reason` `missing_note`/`target_busy`/`read_failed` —
 * a note moving, locked, or failing mid-read) and that read readable in the last committed pass
 * keeps that previous item for this one pass, via the runner-local `lastReadable` map (the items
 * the last commit read readable, like `bodies`/`bodyAt`): the card stays in its lane and in
 * Needs-you with its old content rather than flashing to unread, and the pass counts as unclean
 * so it retries soon. A miss that repeats on the retry shows the card as unread.
 *
 * `botsApi` and `nowMs` are read through refs, kept current every render, so a pass started
 * earlier always uses the latest values without needing the effect to restart. `request` keeps one
 * identity across renders, the same technique `useCardAnswer` uses for `answer`/`undo`. The Bots
 * list is requested at the top of the pass, same as everything else, but its own success/failure
 * handler is attached right there too — not deferred to a later await — so it applies `bots` the
 * moment it resolves (not only once the body loop, which can run up to 48 reads, finishes) and so
 * its rejection on an abort mid-flight is always handled, never left to surface as an unhandled
 * rejection just because the rest of the pass returned early.
 *
 * A single runner (one `AbortController`, one retry timer, one failure count) lives for as long as
 * `(api, workspaceId)` stay the same; it is torn down and replaced whenever either changes. Within
 * one runner: the first pass ("first load", no lanes committed yet) reports a listing throw as
 * `error` and still commits a batch throw's partial items, same as the read this hook replaces.
 * Once lanes exist, a later pass's listing or batch throw is a "hard failure" that leaves state
 * untouched (the lanes stay on screen) rather than erasing them; two hard failures in a row mark
 * the state `stale`, cleared by the next clean pass. A pass that neither throws nor stops its body
 * loop early, keeps no stale item in place of a fresh one, and whose lanes carry no
 * `retry`/`stopped`/`changed` card, is clean and resets the failure count; anything else schedules
 * a retry after `KANBAN_RETRY_MS * 2 ** (failures - 1)`, capped at `KANBAN_RETRY_MAX_MS`. A pass
 * that throws somewhere not already guarded (a bug in a pure helper, say) is caught at the outer
 * `runPass` and counted the same way, so `running` always clears and later requests never just
 * pile up unseen.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { BotRow, BotsApi } from "../../clients/messaging/botsApi";
import type { VaultApi, VaultMetadataItem } from "../../state/vaultApi";
import { type CardRead, readCard } from "./kanbanBody";
import {
  KANBAN_BATCH,
  type KanbanLane,
  type KanbanListing,
  kanbanBodyRefs,
  kanbanLanes,
  kanbanListing,
  kanbanNeedsYou,
  kanbanReadRefs,
  type UnreadCode,
} from "./kanbanCards";

export const KANBAN_RETRY_MS = 1_000;
export const KANBAN_RETRY_MAX_MS = 30_000;

export type KanbanReadState =
  | { phase: "loading" }
  | { phase: "error" }
  | {
      phase: "ready";
      workspaceId: string;
      listing: KanbanListing;
      items: ReadonlyMap<string, VaultMetadataItem>;
      bodies: ReadonlyMap<string, CardRead>;
      reading: boolean;
      stale: boolean;
      /** Per Needs-you card, the modified time Kanban first saw it waiting at. */
      waitingSince: ReadonlyMap<string, number>;
    };

export interface UseKanbanReadOptions {
  api: Pick<VaultApi, "entries" | "metadata"> & Partial<Pick<VaultApi, "note">>;
  workspaceId: string;
  botsApi?: Pick<BotsApi, "list">;
  /** Bumped by the caller for each `kanban.changed` frame naming this workspace. */
  changes: number;
  /** Whether the window socket is open. */
  online: boolean;
  nowMs?: number;
}

export interface UseKanbanReadResult {
  state: KanbanReadState;
  bots: BotRow[];
  request: () => void;
}

/** Unread codes a retry can clear on its own, with no owner action: a transient read hiccup
 * rather than an unsupported shape or a name/cap the vault can't change by reading again. */
const RETRY_UNREAD_CODES: ReadonlySet<UnreadCode> = new Set(["retry", "stopped", "changed"]);

function hasRetryCard(lanes: readonly KanbanLane[]): boolean {
  return lanes.some((lane) =>
    lane.cards.some((card) => card.unread !== null && RETRY_UNREAD_CODES.has(card.unread.code)),
  );
}

function isReadableItem(item: VaultMetadataItem | undefined): boolean {
  const front = item?.display_metadata?.frontmatter_state;
  return front === "ready" || front === "absent";
}

/** `unreadable` reasons a retry alone can clear: the note moved, is locked, or failed mid-read —
 * as opposed to a shape the vault refuses outright (`private_path`, `note_too_large`, ...). */
const TRANSIENT_UNREADABLE_REASONS: ReadonlySet<string> = new Set([
  "missing_note",
  "target_busy",
  "read_failed",
]);

/** A metadata item reporting a transient mid-write hiccup (F4): the note moved, is locked, or
 * failed mid-read, rather than a durable problem a retry alone cannot fix. */
function isTransientMiss(item: VaultMetadataItem): boolean {
  if (item.state === "changed" || item.state === "unknown") return true;
  return (
    item.state === "unreadable" &&
    item.reason !== null &&
    TRANSIENT_UNREADABLE_REASONS.has(item.reason)
  );
}

export function useKanbanRead({
  api,
  workspaceId,
  botsApi,
  changes,
  online,
  nowMs,
}: UseKanbanReadOptions): UseKanbanReadResult {
  const [state, setState] = useState<KanbanReadState>({ phase: "loading" });
  const [bots, setBots] = useState<BotRow[]>([]);

  const botsApiRef = useRef(botsApi);
  botsApiRef.current = botsApi;
  const nowMsRef = useRef(nowMs);
  nowMsRef.current = nowMs;
  // Read once, at setup, only to decide whether a new (api, workspaceId) runner keeps the last
  // one's lanes (an api-identity change with the same workspace) or starts at loading (a
  // different workspace) — a ref so the effect below does not need `state` as a dependency.
  const stateRef = useRef(state);
  stateRef.current = state;

  // `request` keeps one identity; it delegates to whichever runner the effect below currently owns.
  const requestImplRef = useRef<() => void>(() => {});
  const request = useCallback((): void => {
    requestImplRef.current();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    const now = stateRef.current;
    const priorReady = now.phase === "ready" && now.workspaceId === workspaceId ? now : null;
    if (!priorReady) setState({ phase: "loading" });
    let hasLanes = priorReady !== null;
    let running = false;
    let queued = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // Runner-local bookkeeping, kept across passes but never itself exposed as React state:
    // `bodies` mirrors the published map so a held/unheld decision never has to wait on a render;
    // `bodyAt` is each held body's modified time as of its last successful read; `lastReadable` is
    // the metadata items the last commit read readable, for a transient miss (F4) on the next pass
    // to fall back to.
    const bodies = new Map<string, CardRead>(priorReady?.bodies ?? []);
    const bodyAt = new Map<string, number>();
    let waitingSince = new Map<string, number>(priorReady?.waitingSince ?? []);
    let lastReadable = new Map<string, VaultMetadataItem>(
      priorReady ? [...priorReady.items].filter(([, item]) => isReadableItem(item)) : [],
    );

    const clearTimer = (): void => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    function finishPass(clean: boolean, hardFailure: boolean): void {
      running = false;
      failures = clean ? 0 : failures + 1;
      if (hardFailure && failures >= 2) {
        setState((now) => (now.phase === "ready" ? { ...now, stale: true } : now));
      }
      if (queued) {
        queued = false;
        void runPass();
        return;
      }
      if (failures > 0) {
        const delay = Math.min(KANBAN_RETRY_MAX_MS, KANBAN_RETRY_MS * 2 ** (failures - 1));
        timer = setTimeout(() => {
          timer = null;
          void runPass();
        }, delay);
      }
    }

    async function attemptPass(): Promise<void> {
      const firstLoad = !hasLanes;
      let threw = false;
      let stoppedEarly = false;

      // F1: the handlers are attached here, at pass start, not deferred to a later await — so the
      // list applies the moment it resolves (not only once the body loop ends) and its rejection
      // on an abort mid-flight is always handled, even if the rest of this pass returns early and
      // never reaches the `await botsPending` below.
      const botsClient = botsApiRef.current;
      const botsPending = botsClient
        ? botsClient.list(signal).then(
            (list) => {
              if (!signal.aborted) setBots(list.bots);
            },
            () => {
              // keep the last list
            },
          )
        : null;

      let listing: KanbanListing | null = null;
      try {
        listing = kanbanListing(await api.entries(workspaceId, undefined, signal));
      } catch {
        threw = true;
      }
      if (signal.aborted) return;

      const items = new Map<string, VaultMetadataItem>();
      if (listing) {
        const refs = kanbanReadRefs(listing.refs);
        try {
          for (
            let from = 0;
            api.metadata && !signal.aborted && from < refs.length;
            from += KANBAN_BATCH
          ) {
            const batch = await api.metadata(
              workspaceId,
              refs.slice(from, from + KANBAN_BATCH),
              signal,
            );
            for (const entry of batch.items) items.set(entry.ref, entry);
          }
        } catch {
          threw = true;
        }
      }
      if (signal.aborted) return;

      // First load always commits (even a partial batch, as today's read did); a later pass
      // commits only on a clean listing+metadata fetch — a throw there is a hard failure instead.
      if (!(listing && (firstLoad || !threw))) {
        if (firstLoad) setState({ phase: "error" });
        await botsPending;
        if (signal.aborted) return;
        finishPass(false, !firstLoad);
        return;
      }

      // Only items that read readable this pass are remembered, so the fallback below lasts one
      // pass: a miss that repeats shows the card as unread instead of keeping stale content, and
      // stopping later cards' body reads at it, for as long as the miss lasts.
      const freshReadable = new Map<string, VaultMetadataItem>();
      for (const [ref, item] of items) if (isReadableItem(item)) freshReadable.set(ref, item);

      // F4: a later pass's transient miss (the note moving, locked, or failing mid-read) falls
      // back to the ref's last readable item instead, so the card does not flash to unread.
      let keptStaleItem = false;
      if (!firstLoad) {
        for (const [ref, item] of items) {
          if (!isTransientMiss(item)) continue;
          const previous = lastReadable.get(ref);
          if (previous) {
            items.set(ref, previous);
            keptStaleItem = true;
          }
        }
      }
      lastReadable = freshReadable;

      const lanes = kanbanLanes(listing, items, nowMsRef.current ?? Date.now());

      const bodyRefs = kanbanBodyRefs(lanes, listing);
      const bodyRefSet = new Set(bodyRefs);
      // Drop a held body (and its remembered time) once its card leaves the body set entirely —
      // moved to done/cancelled/other, or past the listing/body caps.
      for (const ref of [...bodies.keys()]) if (!bodyRefSet.has(ref)) bodies.delete(ref);
      for (const ref of [...bodyAt.keys()]) if (!bodyRefSet.has(ref)) bodyAt.delete(ref);

      const needsYouCards = kanbanNeedsYou(lanes, listing);
      const needsYouRefs = new Set(needsYouCards.map((card) => card.ref));
      const toRead = bodyRefs.filter((ref) => {
        if (needsYouRefs.has(ref) || !bodies.has(ref)) return true;
        const modifiedAt = listing.modified.get(ref);
        // ponytail: a todo/working card whose text changes with an unchanged file time keeps
        // its old text until the next visit — the listed modified time is the only "did it
        // change" signal available here; upgrade path, if that ever matters, is a real content
        // hash from the vault instead of relying on mtime.
        return !(typeof modifiedAt === "number" && modifiedAt === bodyAt.get(ref));
      });

      const nextWaitingSince = new Map<string, number>();
      for (const card of needsYouCards) {
        const value = waitingSince.get(card.ref) ?? listing.modified.get(card.ref);
        if (value !== undefined) nextWaitingSince.set(card.ref, value);
      }
      // F2: a ref that still lands in the unread lane this pass (for any reason F4 does not
      // already keep readable) keeps whatever waiting-since time it already had, so a card does
      // not lose its place in line just because this one pass could not read it.
      for (const card of lanes.find((lane) => lane.id === "unread")?.cards ?? []) {
        const kept = waitingSince.get(card.ref);
        if (kept !== undefined) nextWaitingSince.set(card.ref, kept);
      }
      waitingSince = nextWaitingSince;

      const note = api.note;
      const willRead = note !== undefined && toRead.length > 0;
      hasLanes = true;
      setState({
        phase: "ready",
        workspaceId,
        listing,
        items,
        bodies: new Map(bodies),
        waitingSince,
        reading: willRead,
        stale: false,
      });

      if (note && toRead.length > 0) {
        // One body at a time, needs-you first: a throw or an unparseable body stops the rest.
        for (const ref of toRead) {
          if (signal.aborted) return;
          let raw: Record<string, unknown>;
          try {
            raw = await note(workspaceId, ref, signal);
          } catch {
            stoppedEarly = true;
            break;
          }
          const read = readCard(raw);
          if (read === null) {
            stoppedEarly = true;
            break;
          }
          if (signal.aborted) return;
          bodies.set(ref, read);
          const modifiedAt = listing.modified.get(ref);
          if (typeof modifiedAt === "number") bodyAt.set(ref, modifiedAt);
          else bodyAt.delete(ref);
          setState((now) => (now.phase === "ready" ? { ...now, bodies: new Map(bodies) } : now));
        }
        if (signal.aborted) return;
        setState((now) => (now.phase === "ready" ? { ...now, reading: false } : now));
      }

      await botsPending;
      if (signal.aborted) return;
      finishPass(!threw && !stoppedEarly && !keptStaleItem && !hasRetryCard(lanes), false);
    }

    async function runPass(): Promise<void> {
      running = true;
      try {
        await attemptPass();
      } catch {
        // A bug in a pure helper (kanbanLanes, kanbanBodyRefs, ...) must not leave `running` true
        // forever — that would make every later request() just queue, with nothing to ever run it.
        if (signal.aborted) return;
        finishPass(false, false);
      }
    }

    requestImplRef.current = (): void => {
      if (running) {
        queued = true;
        return;
      }
      clearTimer();
      void runPass();
    };
    void runPass();

    return () => {
      controller.abort();
      clearTimer();
      requestImplRef.current = () => {};
    };
  }, [api, workspaceId]);

  // `changes` differs from the last value seen (updated below on every difference, not pinned to
  // the mount value) → one more pass.
  const lastChangesRef = useRef(changes);
  useEffect(() => {
    if (changes !== lastChangesRef.current) {
      lastChangesRef.current = changes;
      request();
    }
  }, [changes, request]);

  // Reconnecting (false -> true only) re-reads once.
  const lastOnlineRef = useRef(online);
  useEffect(() => {
    if (online && !lastOnlineRef.current) request();
    lastOnlineRef.current = online;
  }, [online, request]);

  return { state, bots, request };
}
