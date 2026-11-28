/** Generation-scoped subscriptions and correlated, nondurable control operations.
 *
 * Card answers are not here. A permission or question answer is recorded in the
 * durable ledger over REST before it is delivered, so `state/approvals.ts` owns that path and
 * this store never sends `permission.respond` or `question.answer`. The two actions that did
 * had no caller left; the frozen `viki.accept.test.ts` was the only thing
 * still calling them, and it was amended (2026-09-18) so they could go. The server still
 * routes both message types as a compatibility transport, and nothing in this app produces one.
 */
import { useSyncExternalStore } from "react";
import type { ClientMessage, ControlReceipt, ServerMessage } from "../protocol";
import { SocketClient, type WebSocketLike } from "./socket";
import { AppStore } from "./store";

export type ControlOutcome = ControlReceipt["data"]["status"];
export interface Actions {
  openSession(sessionId: string): void;
  openExistingSession(sessionId: string): void;
  observeHistorySession?(sessionId: string): () => void;
  newSession(sessionId?: string, expectedModel?: string | null): string;
  prepareTarget(sessionId: string): Promise<{ session_id: string; generation: string } | null>;
  createFork(
    sourceId: string,
    forkId: string,
  ): Promise<{ session_id: string; generation: string } | null>;
  loadOlder(sessionId: string): void;
  /** Explicit retry for a "busy" history page: never sent automatically. */
  reloadHistory(sessionId: string): void;
  /** True is a completed local invocation, never durable or native acceptance. */
  send(sessionId: string, text: string): Promise<boolean>;
  fork(sourceId: string, text: string): Promise<string | null>;
  interrupt(sessionId: string): Promise<ControlOutcome>;
  setModel(sessionId: string, model: string): Promise<ControlOutcome>;
  setEffort(sessionId: string, effort: string): Promise<ControlOutcome>;
  setMode(sessionId: string, mode: string): Promise<ControlOutcome>;
  /** A refused view can be dismissed locally unless an expected turn requires a close receipt. */
  closeSession(
    sessionId: string,
    expected?: { generation: string; turnId: string },
  ): Promise<ControlOutcome>;
  openUrl(url: string): void;
  openVault(path: string): void;
  openCode(workspaceId: string, path: string, line: number | null): void;
  openBotMemory(workspaceId: string, path: string): void;
  openTranscript(sessionId: string): void;
  /** True proves browser socket handoff only, same as `send`: never durable or native
   * acceptance. Reads the window's `ui` nonce from `location.search` itself (`MagpiePanel`'s own
   * pattern) and sends nothing for a missing or malformed one. */
  checkForUpdates(): boolean;
}
export interface Viki {
  store: AppStore;
  client: SocketClient;
  actions: Actions;
}
export interface VikiOptions {
  url: string;
  token: string;
  createSocket?: (url: string) => WebSocketLike;
  /** Bounds waits for a synchronized target and local control receipts. Tests inject timers. */
  controlTimeoutMs?: number;
}
type Mutation = Extract<ClientMessage, { command_id: string }>;
interface PendingControl {
  sessionId: string;
  generation: string;
  epoch: number;
  finish(receipt: ControlReceipt | null): void;
}
const TOKEN_KEY = "viki.token";
const MAX_PENDING_CONTROLS = 64;

export function createViki(options: VikiOptions): Viki {
  const store = new AppStore();
  const client = new SocketClient(options);
  const tracked = new Set<string>();
  const explicit = new Set<string>();
  const historyObservers = new Map<string, Set<object>>();
  // Closing/untracking a target cannot widen its admission on a later global update.
  const existingOnly = new Set<string>();
  const closing = new Set<string>();
  // Ids session.closed added to closing and closedSeen: released from both at the first
  // vault.sessions frame after, whether or not it still lists the id. closeSession's applied
  // branch adds the id to closing; session.closed, when accepted, adds closedSeen too and
  // releases it the same way. Only an applied close whose session.closed the handler dropped (a
  // stale subscription, generation or seq) leaves closing alone until a frame drops it.
  const closedSeen = new Set<string>();
  const controls = new Map<string, PendingControl>();
  let pendingReadiness = 0;
  const timeoutMs = options.controlTimeoutMs ?? 30_000;
  const view = (id: string) => store.getSnapshot().sessions[id];
  const cursorOf = (id: string) => () => ({
    generation: view(id)?.generation ?? null,
    seq: view(id)?.seq ?? 0,
  });

  function sendHistory(id: string): void {
    const current = view(id);
    if (!current?.synced || !current.generation || current.historyRequestId) return;
    const requestId = crypto.randomUUID();
    store.update(id, (v) => ({ ...v, historyLoading: true, historyRequestId: requestId }));
    const sent = client.send({
      type: "history.more",
      session_id: id,
      generation: current.generation,
      request_id: requestId,
      ...(typeof current.historyCursor === "string" ? { before_uuid: current.historyCursor } : {}),
    });
    if (!sent) store.update(id, (v) => ({ ...v, historyRequestId: null }));
  }
  function requestHistory(id: string): void {
    const current = view(id);
    if (current?.historyCursor === null || current?.historyRequestId) return;
    store.update(id, (v) => ({ ...v, historyLoading: true }));
    sendHistory(id);
  }
  function untrack(id: string, detach = false): void {
    tracked.delete(id);
    explicit.delete(id);
    historyObservers.delete(id);
    client.untrack(id, detach);
    store.remove(id);
  }
  function openSession(id: string, only = false): void {
    explicit.add(id);
    trackSession(id, only);
  }
  function trackSession(id: string, only = false): void {
    if (only) existingOnly.add(id);
    if (tracked.has(id)) {
      if (only) {
        // Only this explicit action retries a refusal; ordinary global discovery does not.
        if (view(id)?.openRefused) client.untrack(id);
        client.track(id, cursorOf(id), true);
        const requestId = client.subscriptionId(id);
        store.update(id, (v) =>
          v.requestId === requestId ? v : { ...v, requestId, synced: false, openRefused: null },
        );
      }
      return;
    }
    closing.delete(id);
    store.remove(id);
    tracked.add(id);
    store.update(id, (v) => ({ ...v, historyLoading: true }));
    client.track(id, cursorOf(id), existingOnly.has(id));
    const requestId = client.subscriptionId(id);
    store.update(id, (v) => ({ ...v, requestId }));
  }
  function ready(id: string, epoch: number): boolean {
    const current = view(id);
    return Boolean(
      client.isCurrentConnection(epoch) &&
        current?.synced &&
        current.generation &&
        current.requestId === client.subscriptionId(id),
    );
  }
  function waitReady(id: string, epoch: number): Promise<boolean> {
    if (ready(id, epoch)) return Promise.resolve(true);
    if (
      !client.isCurrentConnection(epoch) ||
      !tracked.has(id) ||
      view(id)?.openRefused ||
      controls.size + pendingReadiness >= MAX_PENDING_CONTROLS
    )
      return Promise.resolve(false);
    pendingReadiness += 1;
    return new Promise((resolve) => {
      let settled = false;
      let unsubscribe = () => {};
      let unsubscribeStatus = () => {};
      const finish = (value: boolean) => {
        if (settled) return;
        settled = true;
        pendingReadiness -= 1;
        clearTimeout(timer);
        unsubscribe();
        unsubscribeStatus();
        resolve(value);
      };
      const check = () => {
        if (!client.isCurrentConnection(epoch) || !tracked.has(id) || view(id)?.openRefused)
          finish(false);
        else if (ready(id, epoch)) finish(true);
      };
      const timer = setTimeout(() => finish(false), timeoutMs);
      unsubscribe = store.subscribe(check);
      unsubscribeStatus = client.onStatus(check);
      check();
    });
  }
  function command(message: Mutation, epoch: number): Promise<ControlReceipt | null> {
    if (
      !ready(message.session_id, epoch) ||
      controls.size + pendingReadiness >= MAX_PENDING_CONTROLS
    )
      return Promise.resolve(null);
    return new Promise((resolve) => {
      const timer = setTimeout(() => finish(null), timeoutMs);
      const finish = (receipt: ControlReceipt | null) => {
        if (!controls.delete(message.command_id)) return;
        clearTimeout(timer);
        resolve(receipt);
      };
      controls.set(message.command_id, {
        sessionId: message.session_id,
        generation: message.generation,
        epoch,
        finish,
      });
      if (!client.send(message)) finish(null);
    });
  }
  async function control(
    sessionId: string,
    build: (generation: string, commandId: string) => Mutation,
  ): Promise<ControlOutcome> {
    const epoch = client.connectionId;
    const current = view(sessionId);
    if (
      !ready(sessionId, epoch) ||
      !current?.generation ||
      controls.size + pendingReadiness >= MAX_PENDING_CONTROLS
    )
      return "rejected";
    const receipt = await command(build(current.generation, crypto.randomUUID()), epoch);
    return receipt?.data.status ?? "unknown";
  }
  client.onStatus((status) => {
    if (status !== "open") {
      for (const pending of [...controls.values()]) pending.finish(null);
      for (const id of tracked)
        store.update(id, (v) => ({ ...v, synced: false, historyRequestId: null }));
    } else {
      for (const id of tracked)
        store.update(id, (v) => ({
          ...v,
          synced: v.synced && v.requestId === client.subscriptionId(id),
          requestId: client.subscriptionId(id),
          openRefused: client.subscriptionId(id) === null ? (v.openRefused ?? null) : null,
        }));
    }
    store.setStatus(status);
  });

  client.onMessage((message: ServerMessage) => {
    if (message.type === "control.receipt") {
      const pending = controls.get(message.command_id);
      if (
        pending &&
        pending.sessionId === message.session_id &&
        pending.generation === message.generation &&
        client.isCurrentConnection(pending.epoch)
      )
        pending.finish(message);
      return;
    }
    if (message.type === "vault.sessions") {
      const before = store.getSnapshot();
      store.dispatch(message);
      let released = false;
      for (const id of closedSeen) {
        closing.delete(id);
        closedSeen.delete(id);
        released = true;
      }
      if (!released && before === store.getSnapshot()) return;
      const live = new Set(message.data.live);
      for (const id of closing) if (!live.has(id)) closing.delete(id);
      for (const id of live) if (!closing.has(id)) trackSession(id);
      return;
    }
    if (!tracked.has(message.session_id)) return;
    const id = message.session_id;
    if (message.type === "history.page") {
      store.dispatch(message);
      return;
    }
    if (message.request_id !== client.subscriptionId(id)) return;
    if (message.type === "session.open.refused") {
      const modelReviewRequired = message.data?.reason === "model_review_required";
      if (
        (modelReviewRequired ? !client.hasModelExpectation(id) : !existingOnly.has(id)) ||
        !client.isCurrentConnection(client.connectionId) ||
        message.generation !== "" ||
        message.seq !== 0 ||
        (message.data?.reason !== "unavailable" &&
          message.data?.reason !== "storage_unavailable" &&
          !modelReviewRequired)
      )
        return;
      // A refused comparison waits for an explicit Reopen; reconnect must not retry it.
      if (modelReviewRequired) client.untrack(id);
      store.update(id, (v) => ({
        ...v,
        synced: false,
        historyLoading: false,
        historyRequestId: null,
        openRefused: message.data.reason,
      }));
      return;
    }
    if (message.type === "session.snapshot") {
      store.dispatch(message);
      return;
    }
    const current = view(id);
    if (!current || current.generation !== message.generation) return;
    if (message.type === "session.closed") {
      if (message.seq <= current.seq) return;
      closing.add(id);
      closedSeen.add(id);
      untrack(id);
      return;
    }
    store.dispatch(message);
    if (message.type === "session.synced" && view(id)?.synced) {
      client.confirmSubscription(id, message.request_id);
      const current = view(id);
      // A "busy" first page also leaves historyCursor undefined; only the explicit reload
      // button may retry it, never a resync (no automatic retry).
      if (
        current?.historyError !== "busy" &&
        (current?.historyLoading || current?.historyCursor === undefined)
      )
        requestHistory(id);
    }
  });

  const actions: Actions = {
    openSession,
    openExistingSession: (id) => openSession(id, true),
    observeHistorySession(id) {
      let observers = historyObservers.get(id);
      if (!observers) {
        observers = new Set();
        historyObservers.set(id, observers);
      }
      const lease = {};
      observers.add(lease);
      if (!tracked.has(id)) trackSession(id, true);
      return () => {
        if (!observers.delete(lease)) return;
        if (observers.size === 0 && historyObservers.get(id) === observers) {
          historyObservers.delete(id);
          if (!explicit.has(id)) untrack(id, true);
        }
      };
    },
    async prepareTarget(sessionId) {
      const epoch = client.connectionId;
      if (!(await waitReady(sessionId, epoch))) return null;
      const generation = view(sessionId)?.generation;
      return generation && ready(sessionId, epoch) ? { session_id: sessionId, generation } : null;
    },
    async createFork(sourceId, id) {
      const epoch = client.connectionId;
      if (!(await waitReady(sourceId, epoch))) return null;
      const generation = view(sourceId)?.generation;
      if (!generation) return null;
      const receipt = await command(
        {
          type: "session.fork",
          session_id: sourceId,
          generation,
          command_id: crypto.randomUUID(),
          fork_id: id,
        },
        epoch,
      );
      if (
        receipt?.data.status !== "applied" ||
        receipt.data.target?.session_id !== id ||
        !client.isCurrentConnection(epoch)
      )
        return null;
      openSession(id);
      if (!(await waitReady(id, epoch)) || view(id)?.generation !== receipt.data.target.generation)
        return null;
      return receipt.data.target;
    },
    newSession(id = crypto.randomUUID(), expectedModel) {
      explicit.add(id);
      tracked.add(id);
      store.update(id, (v) => ({
        ...v,
        historyCursor: null,
        ...(v.openRefused || expectedModel !== undefined
          ? { openRefused: null, synced: false }
          : {}),
      }));
      client.track(id, cursorOf(id), existingOnly.has(id), expectedModel);
      store.update(id, (v) => ({ ...v, requestId: client.subscriptionId(id) }));
      return id;
    },
    loadOlder(id) {
      const current = view(id);
      // A kept "busy" cursor must not auto-retry on scroll; only reloadHistory's explicit click
      // does.
      if (typeof current?.historyCursor === "string" && current.historyError !== "busy")
        requestHistory(id);
    },
    reloadHistory(id) {
      if (!view(id)) return;
      store.update(id, (v) => ({ ...v, historyError: null }));
      requestHistory(id);
    },
    async send(sessionId, text) {
      const epoch = client.connectionId;
      if (!(await waitReady(sessionId, epoch))) return false;
      const generation = view(sessionId)?.generation;
      if (!generation) return false;
      const receipt = await command(
        {
          type: "turn.send",
          session_id: sessionId,
          generation,
          command_id: crypto.randomUUID(),
          text,
        },
        epoch,
      );
      return receipt?.data.status === "applied";
    },
    async fork(sourceId, text) {
      const epoch = client.connectionId;
      const source = view(sourceId);
      if (!ready(sourceId, epoch) || !source?.generation) return null;
      const id = crypto.randomUUID();
      const receipt = await command(
        {
          type: "session.fork",
          session_id: sourceId,
          generation: source.generation,
          command_id: crypto.randomUUID(),
          fork_id: id,
        },
        epoch,
      );
      if (
        receipt?.data.status !== "applied" ||
        receipt.data.target?.session_id !== id ||
        !client.isCurrentConnection(epoch)
      )
        return null;
      openSession(id);
      if (!(await waitReady(id, epoch)) || view(id)?.generation !== receipt.data.target.generation)
        return null;
      const turn = await command(
        {
          type: "turn.send",
          session_id: id,
          generation: receipt.data.target.generation,
          command_id: crypto.randomUUID(),
          text,
        },
        epoch,
      );
      return turn?.data.status === "applied" ? id : null;
    },
    interrupt: (id) =>
      control(id, (generation, command_id) => ({
        type: "turn.interrupt",
        session_id: id,
        generation,
        command_id,
      })),
    setModel: (id, model) =>
      control(id, (generation, command_id) => ({
        type: "session.set_model",
        session_id: id,
        generation,
        command_id,
        model,
      })),
    setEffort: (id, effort) =>
      control(id, (generation, command_id) => ({
        type: "session.set_effort",
        session_id: id,
        generation,
        command_id,
        effort,
      })),
    setMode: (id, mode) =>
      control(id, (generation, command_id) => ({
        type: "session.set_mode",
        session_id: id,
        generation,
        command_id,
        mode,
      })),
    async closeSession(id, expected) {
      if (expected && view(id)?.generation !== expected.generation) return "rejected";
      // A refused open has no subscribed actor to close.
      if (!expected && view(id)?.openRefused) {
        untrack(id);
        return "applied";
      }
      const outcome = await control(id, (generation, command_id) => ({
        type: "session.close",
        session_id: id,
        generation,
        command_id,
        ...(expected ? { turn_id: expected.turnId } : {}),
      }));
      if (outcome === "applied") {
        closing.add(id);
        untrack(id);
      }
      return outcome;
    },
    openUrl(url) {
      client.send({ type: "open.url", url });
    },
    openVault(path) {
      client.send({ type: "open.vault", path });
    },
    openCode(workspace_id, path, line) {
      client.send({ type: "open.code", workspace_id, path, ...(line === null ? {} : { line }) });
    },
    openBotMemory(workspace_id, path) {
      client.send({ type: "open.bot_memory", workspace_id, path });
    },
    openTranscript(session_id) {
      client.send({ type: "open.transcript", session_id });
    },
    checkForUpdates() {
      const ui = new URLSearchParams(window.location.search).get("ui");
      return ui !== null && /^[0-9a-f]{32}$/.test(ui) && client.send({ type: "updates.check", ui });
    },
  };
  return { store, client, actions };
}

export function readToken(
  location: { hash: string; pathname: string; search: string },
  storage: Pick<Storage, "getItem" | "setItem">,
  history: Pick<History, "replaceState">,
): string | null {
  const match = /(?:^#|[#&])token=([^&]+)/.exec(location.hash);
  if (match?.[1]) {
    const token = decodeURIComponent(match[1]);
    try {
      storage.setItem(TOKEN_KEY, token);
    } catch {
      // storage refused (private mode, quota): the token still serves this page load
    }
    history.replaceState(null, "", `${location.pathname}${location.search}`);
    return token;
  }
  try {
    return storage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/** React access to an external store's snapshot (the AppStore, or the RestStore). */
export function useSnapshot<T>(store: {
  subscribe(listener: () => void): () => void;
  getSnapshot(): T;
}): T {
  return useSyncExternalStore(store.subscribe, store.getSnapshot);
}
