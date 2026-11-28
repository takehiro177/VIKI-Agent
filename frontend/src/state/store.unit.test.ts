/**
 * Unit tests pinning AppStore rules that store.accept.test.ts leaves implicit: independent
 * listener unsubscription, remove() of a session that was never opened, snapshot identity
 * across a dropped-duplicate dispatch, and the live report vault.sessions leaves behind for
 * live.ts to read.
 */
import { expect, test, vi } from "vitest";
import { snapshotData } from "../accept/m3b/fakes";
import type { Envelope, SessionSnapshot } from "../protocol";
import { AppStore } from "./store";

const AT = "2026-09-11T08:00:00.000Z";

function userMessage(sessionId: string, seq: number): Envelope {
  return {
    v: 2,
    generation: "11111111-1111-4111-8111-111111111111",
    request_id: "",
    type: "user.message",
    session_id: sessionId,
    seq,
    data: { text: `hi ${sessionId}`, command: "", at: AT },
  };
}

test("unsubscribing one listener leaves the others still notified", () => {
  const store = new AppStore();
  const a = vi.fn();
  const b = vi.fn();
  const stopA = store.subscribe(a);
  store.subscribe(b);
  store.setStatus("open");
  expect(a).toHaveBeenCalledTimes(1);
  expect(b).toHaveBeenCalledTimes(1);
  stopA();
  store.setStatus("reconnecting");
  expect(a).toHaveBeenCalledTimes(1);
  expect(b).toHaveBeenCalledTimes(2);
});

test("remove() of a session that was never opened changes nothing and notifies nobody", () => {
  const store = new AppStore();
  const listener = vi.fn();
  store.subscribe(listener);
  const before = store.getSnapshot();
  store.remove("ghost");
  expect(store.getSnapshot()).toBe(before);
  expect(listener).not.toHaveBeenCalled();
});

test("a duplicate envelope leaves the snapshot identical and calls no listener", () => {
  const store = new AppStore();
  store.dispatch(userMessage("a", 1));
  const listener = vi.fn();
  store.subscribe(listener);
  const before = store.getSnapshot();
  store.dispatch(userMessage("a", 1)); // seq <= the view's own seq: a duplicate
  expect(store.getSnapshot()).toBe(before);
  expect(store.getSnapshot().sessions.a).toBe(before.sessions.a);
  expect(listener).not.toHaveBeenCalled();
});

test("update() whose change returns the same view commits nothing and calls no listener", () => {
  const store = new AppStore();
  store.dispatch(userMessage("a", 1));
  const listener = vi.fn();
  store.subscribe(listener);
  const before = store.getSnapshot();
  store.update("a", (view) => view);
  expect(store.getSnapshot()).toBe(before);
  expect(listener).not.toHaveBeenCalled();
});

test("a delta for a block that is already done keeps the turns array's identity", () => {
  const store = new AppStore();
  store.dispatch(userMessage("a", 1));
  store.dispatch({
    v: 2,
    generation: "11111111-1111-4111-8111-111111111111",
    request_id: "",
    type: "text.done",
    session_id: "a",
    seq: 2,
    data: { block_id: "t1", text: "done" },
  });
  const before = store.getSnapshot().sessions.a;
  store.dispatch({
    v: 2,
    generation: "11111111-1111-4111-8111-111111111111",
    request_id: "",
    type: "text.delta",
    session_id: "a",
    seq: 3,
    data: { block_id: "t1", text: "late" },
  });
  const after = store.getSnapshot().sessions.a;
  expect(after?.seq).toBe(3);
  expect(after?.turns).toBe(before?.turns);
});

const INFO = {
  model: "sonnet",
  mode: "auto",
  effort: "low",
  commands: [],
  agents: [],
  models: [],
  cli_version: "2.1.259",
  resumed: false,
};

function vaultSessions(live: string[], seq = 0): Envelope {
  return {
    v: 2,
    generation: "11111111-1111-4111-8111-111111111111",
    request_id: "",
    type: "vault.sessions",
    session_id: "",
    seq,
    data: { others: [], live },
  };
}

function sessionStarted(sessionId: string, seq: number): Envelope {
  return {
    v: 2,
    generation: "11111111-1111-4111-8111-111111111111",
    request_id: "",
    type: "session.started",
    session_id: sessionId,
    seq,
    data: INFO,
  };
}

test("vault.sessions keeps the server's live list beside `others`", () => {
  const store = new AppStore();
  expect(store.getSnapshot().live).toBeUndefined(); // nothing to read until the server answers
  store.dispatch(vaultSessions(["a"]));
  const report = store.getSnapshot().live;
  expect([...(report?.ids ?? [])]).toEqual(["a"]);
  expect([...(report?.startedSince ?? [])]).toEqual([]);
});

test("a session.started after that list is marked newer, and the next list clears the mark", () => {
  const store = new AppStore();
  store.dispatch(vaultSessions(["a"]));
  const before = store.getSnapshot().live;
  store.dispatch(sessionStarted("b", 1));
  const after = store.getSnapshot().live;
  // The list itself is untouched — only the knowledge that b's process postdates it.
  expect(after?.ids).toBe(before?.ids);
  expect([...(after?.startedSince ?? [])]).toEqual(["b"]);
  store.dispatch(vaultSessions(["a", "b"], 1));
  expect([...(store.getSnapshot().live?.startedSince ?? [])]).toEqual([]);
});

test("before the first vault.sessions a session.started has nothing to outrank", () => {
  const store = new AppStore();
  store.dispatch(sessionStarted("a", 1));
  expect(store.getSnapshot().live).toBeUndefined();
});

test("a duplicate session.started commits nothing, report included", () => {
  const store = new AppStore();
  store.dispatch(vaultSessions([]));
  store.dispatch(sessionStarted("a", 2));
  const before = store.getSnapshot();
  const listener = vi.fn();
  store.subscribe(listener);
  store.dispatch(sessionStarted("a", 2)); // seq <= the view's own seq
  expect(store.getSnapshot()).toBe(before);
  expect(listener).not.toHaveBeenCalled();
});

function sessionSnapshot(maxLiveSessions?: number): SessionSnapshot {
  const data = snapshotData();
  if (maxLiveSessions !== undefined) data.state.max_live_sessions = maxLiveSessions;
  return {
    v: 2,
    generation: "11111111-1111-4111-8111-111111111111",
    request_id: "",
    type: "session.snapshot",
    session_id: "a",
    seq: 1,
    data,
  };
}

test.each([1, 8, Number.MAX_SAFE_INTEGER])(
  "a snapshot projects the advertised positive safe-integer limit %s",
  (limit) => {
    const store = new AppStore();
    expect(store.getSnapshot().maxLiveSessions).toBeUndefined();
    store.dispatch(sessionSnapshot(limit));
    expect(store.getSnapshot().maxLiveSessions).toBe(limit);
  },
);

test("a fresh legacy snapshot resets the previously advertised limit to 3", () => {
  const store = new AppStore();
  store.dispatch(sessionSnapshot(8));
  expect(store.getSnapshot().maxLiveSessions).toBe(8);
  store.dispatch(sessionSnapshot());
  expect(store.getSnapshot().maxLiveSessions).toBe(3);
});

test.each([
  0,
  -1,
  1.5,
  Number.NaN,
  Infinity,
  -Infinity,
  Number.MAX_SAFE_INTEGER + 1,
  "8",
  null,
  true,
])("an invalid snapshot limit %s falls back to 3", (limit) => {
  const store = new AppStore();
  store.dispatch(sessionSnapshot(8));
  const invalid = sessionSnapshot();
  Object.assign(invalid.data.state, { max_live_sessions: limit });
  store.dispatch(invalid);
  expect(store.getSnapshot().maxLiveSessions).toBe(3);
});

function kanbanChanged(
  workspaceId: unknown,
  seq: number,
  generation = "11111111-1111-4111-8111-111111111111",
): Envelope {
  return {
    v: 2,
    generation,
    request_id: "",
    type: "kanban.changed",
    session_id: "",
    seq,
    data: { workspace_id: workspaceId },
  } as unknown as Envelope;
}

test("kanban.changed commits kanban[id] = prev + 1, counted per workspace", () => {
  const store = new AppStore();
  store.dispatch(kanbanChanged("w1", 1));
  expect(store.getSnapshot().kanban).toEqual({ w1: 1 });
  store.dispatch(kanbanChanged("w1", 2));
  expect(store.getSnapshot().kanban).toEqual({ w1: 2 });
  store.dispatch(kanbanChanged("w2", 3));
  expect(store.getSnapshot().kanban).toEqual({ w1: 2, w2: 1 });
});

test("kanban.changed drops a frame from another generation, and one whose seq is not ahead", () => {
  const store = new AppStore();
  store.dispatch(kanbanChanged("w1", 1, "11111111-1111-4111-8111-111111111111"));
  store.dispatch(kanbanChanged("w1", 2, "33333333-3333-4333-8333-333333333333")); // wrong generation
  expect(store.getSnapshot().kanban).toEqual({ w1: 1 });
  store.dispatch(kanbanChanged("w1", 1, "11111111-1111-4111-8111-111111111111")); // seq <= last
  expect(store.getSnapshot().kanban).toEqual({ w1: 1 });
});

test("setStatus resets kanban's generation/seq, so reconnect's first frame is accepted above seq 1", () => {
  const store = new AppStore();
  store.dispatch(kanbanChanged("w1", 1, "11111111-1111-4111-8111-111111111111"));
  store.setStatus("reconnecting");
  store.dispatch(kanbanChanged("w1", 5, "33333333-3333-4333-8333-333333333333"));
  expect(store.getSnapshot().kanban).toEqual({ w1: 2 });
});

test("kanban.changed with a non-string workspace_id commits nothing and notifies nobody", () => {
  const store = new AppStore();
  const listener = vi.fn();
  store.subscribe(listener);
  store.dispatch(kanbanChanged(42, 1));
  expect(store.getSnapshot().kanban).toBeUndefined();
  expect(listener).not.toHaveBeenCalled();
});

test("kanban's generation/seq tracking stays apart from vault.sessions's", () => {
  const store = new AppStore();
  store.dispatch(vaultSessions(["a"], 5));
  store.dispatch(kanbanChanged("w1", 1)); // its own seq starts at 1 regardless of vault.sessions's
  expect(store.getSnapshot().kanban).toEqual({ w1: 1 });
  store.dispatch(vaultSessions(["a"], 6));
  store.dispatch(kanbanChanged("w1", 2));
  expect(store.getSnapshot().kanban).toEqual({ w1: 2 });
});
