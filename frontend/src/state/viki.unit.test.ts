/**
 * Unit tests pinning viki.ts rules that viki.accept.test.ts leaves implicit: readToken's
 * fragment parsing against other params, percent-encoding and a throwing storage, and
 * openSession discarding a leftover view before tracking.
 */
import { expect, test, vi } from "vitest";
import { FakeSocket, GENERATION, snapshotData } from "../accept/m3b/fakes";
import { createViki, readToken } from "./viki";

function connected() {
  const socket = new FakeSocket();
  const viki = createViki({
    url: "ws://127.0.0.1:8765/ws",
    token: "tok",
    createSocket: () => socket,
  });
  viki.client.connect();
  socket.onopen?.({});
  socket.sent.length = 0; // drop auth
  return { viki, socket };
}

function memoryStorage(): Pick<Storage, "getItem" | "setItem"> {
  const map = new Map<string, string>();
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
  };
}

test("readToken finds the token among other fragment params, on either side", () => {
  const storage = memoryStorage();
  const history = { replaceState: vi.fn() };
  const token = readToken(
    { hash: "#foo=1&token=abc&bar=2", pathname: "/x", search: "?y=1" },
    storage,
    history,
  );
  expect(token).toBe("abc");
  expect(history.replaceState).toHaveBeenCalledWith(null, "", "/x?y=1");
});

test("readToken decodes a percent-encoded token", () => {
  const storage = memoryStorage();
  const history = { replaceState: vi.fn() };
  const token = readToken(
    { hash: "#token=a%2Fb%3D%26c", pathname: "/", search: "" },
    storage,
    history,
  );
  expect(token).toBe("a/b=&c");
});

test("readToken still returns the fragment token even when storage.setItem throws", () => {
  const storage = {
    getItem: () => null,
    setItem: () => {
      throw new Error("quota exceeded");
    },
  };
  const history = { replaceState: vi.fn() };
  expect(readToken({ hash: "#token=abc", pathname: "/", search: "" }, storage, history)).toBe(
    "abc",
  );
});

test("readToken returns null, not a throw, when storage.getItem throws and there is no fragment", () => {
  const storage = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {},
  };
  const history = { replaceState: vi.fn() };
  expect(readToken({ hash: "", pathname: "/", search: "" }, storage, history)).toBeNull();
});

test("readToken returns null with no fragment and nothing stored", () => {
  const storage = memoryStorage();
  const history = { replaceState: vi.fn() };
  expect(readToken({ hash: "", pathname: "/", search: "" }, storage, history)).toBeNull();
});

test("openCode sends open.code and leaves out line when it is null", () => {
  const { viki, socket } = connected();
  const id = "11111111-1111-4111-8111-111111111111";
  viki.actions.openCode(id, "src/a.py", 12);
  viki.actions.openCode(id, "src/a.py", null);
  expect(socket.sent).toEqual([
    { type: "open.code", workspace_id: id, path: "src/a.py", line: 12 },
    { type: "open.code", workspace_id: id, path: "src/a.py" },
  ]);
});

test("openSession discards any leftover view for that id before tracking it", () => {
  const { viki, socket } = connected();
  viki.store.update("a", (v) => ({
    ...v,
    turns: [
      {
        id: "stale",
        user: null,
        blocks: [],
        status: "success",
        usage: null,
        durationMs: 0,
        history: false,
      },
    ],
  }));
  viki.actions.openSession("a");
  expect(viki.store.getSnapshot().sessions.a?.turns).toEqual([]);
  expect(socket.sent).toEqual([
    { type: "session.open", session_id: "a", after_seq: 0, request_id: expect.any(String) },
    {
      type: "history.more",
      session_id: "a",
      generation: expect.any(String),
      request_id: expect.any(String),
    },
  ]);
});

test("a guarded new chat clears its expectation only after an accepted session barrier", () => {
  vi.useFakeTimers();
  const socket = new FakeSocket(false);
  const viki = createViki({
    url: "ws://synthetic",
    token: "synthetic",
    createSocket: () => socket,
  });
  try {
    viki.client.connect();
    socket.open();
    viki.actions.newSession("a", null);
    expect(socket.sent.at(-1)).toMatchObject({ type: "session.open", expected_model: null });
    expect(viki.client.hasModelExpectation("a")).toBe(true);
    socket.receive({
      v: 2,
      type: "session.synced",
      session_id: "a",
      request_id: viki.client.subscriptionId("a"),
      generation: "not-the-session-generation",
      seq: 0,
      data: {},
    });
    expect(viki.client.hasModelExpectation("a")).toBe(true);
    socket.synchronize("a");
    expect(viki.client.hasModelExpectation("a")).toBe(false);
    socket.onclose?.({ code: 1006 });
    vi.advanceTimersByTime(500);
    socket.open();
    expect(socket.sent.at(-1)).not.toHaveProperty("expected_model");
  } finally {
    viki.client.close();
    vi.useRealTimers();
  }
});

test("a model-review-required refusal stops readiness and reconnect retries until an explicit reopen", async () => {
  vi.useFakeTimers();
  const socket = new FakeSocket(false);
  const viki = createViki({
    url: "ws://synthetic",
    token: "synthetic",
    createSocket: () => socket,
  });
  try {
    viki.client.connect();
    socket.open();
    viki.actions.newSession("a");
    socket.synchronize("a");
    viki.actions.newSession("a", "model-a");
    expect(viki.store.getSnapshot().sessions.a?.synced).toBe(false);
    const ready = viki.actions.prepareTarget("a");
    const request_id = viki.client.subscriptionId("a");
    socket.receive({
      v: 2,
      type: "session.open.refused",
      session_id: "a",
      request_id: "old-request",
      generation: "",
      seq: 0,
      data: { reason: "model_review_required" },
    });
    expect(viki.store.getSnapshot().sessions.a?.openRefused).not.toBe("model_review_required");
    expect(viki.client.hasModelExpectation("a")).toBe(true);
    socket.receive({
      v: 2,
      type: "session.open.refused",
      session_id: "a",
      request_id,
      generation: "",
      seq: 0,
      data: { reason: "model_review_required" },
    });
    expect(viki.store.getSnapshot().sessions.a?.openRefused).toBe("model_review_required");
    expect(await ready).toBeNull();
    expect(await viki.actions.send("a", "must remain a draft")).toBe(false);
    const opens = socket.sent.filter((frame) => frame.type === "session.open").length;
    socket.onclose?.({ code: 1006 });
    vi.advanceTimersByTime(500);
    socket.open();
    expect(socket.sent.filter((frame) => frame.type === "session.open")).toHaveLength(opens);
    expect(viki.store.getSnapshot().sessions.a?.openRefused).toBe("model_review_required");
    expect(socket.sent.some((frame) => frame.type === "turn.send")).toBe(false);
    viki.actions.newSession("a"); // the explicit Reopen action carries no old comparison
    expect(socket.sent.at(-1)).toMatchObject({ type: "session.open", session_id: "a" });
    expect(socket.sent.at(-1)).not.toHaveProperty("expected_model");
    socket.synchronize("a");
    expect(viki.store.getSnapshot().sessions.a?.synced).toBe(true);
    expect(viki.store.getSnapshot().sessions.a?.openRefused ?? null).toBeNull();
    expect(socket.sent.some((frame) => frame.type === "turn.send")).toBe(false);
  } finally {
    viki.client.close();
    vi.useRealTimers();
  }
});

// Manual mode (unlike connected()'s automatic socket) so the close's own control.receipt is
// delivered without an accompanying session.closed: the closing set here comes only from
// closeSession's own "applied" branch, never from closedSeen (which session.closed alone feeds).
test("a close receipt with no session.closed keeps the id out of the window until a vault.sessions frame drops it", async () => {
  const socket = new FakeSocket(false);
  const viki = createViki({
    url: "ws://127.0.0.1:8765/ws",
    token: "tok",
    createSocket: () => socket,
  });
  viki.client.connect();
  socket.onopen?.({});
  const id = viki.actions.newSession();
  socket.synchronize(id);
  socket.sent.length = 0;
  const closingResult = viki.actions.closeSession(id);
  const closeCommand = socket.sent.find((message) => message.type === "session.close");
  if (!closeCommand) throw new Error("missing session.close");
  socket.acknowledge(closeCommand, "applied");
  expect(await closingResult).toBe("applied");
  expect(viki.store.getSnapshot().sessions[id]).toBeUndefined();
  socket.sent.length = 0;
  let globalSeq = 0;
  const vaultSessions = (live: string[]) =>
    socket.onmessage?.({
      data: JSON.stringify({
        v: 2,
        generation: "11111111-1111-4111-8111-111111111111",
        request_id: "",
        type: "vault.sessions",
        session_id: "",
        seq: ++globalSeq,
        data: { others: [], live },
      }),
    });
  vaultSessions([id]); // the poll has not caught up with the close yet
  expect(socket.sent).toEqual([]);
  vaultSessions([]); // the poll catches up
  vaultSessions([id]); // live again elsewhere: discovered like any other session
  // Manual mode: reopening resubscribes but does not auto-synchronize, so only session.open
  // is sent (history.more waits for a session.synced this test never delivers).
  expect(socket.sent).toEqual([
    { type: "session.open", session_id: id, after_seq: 0, request_id: expect.any(String) },
  ]);
});

test("a closed id leaves closing at the first vault.sessions frame after session.closed, so a later live frame re-tracks it", () => {
  const { viki, socket } = connected();
  viki.actions.openSession("a");
  const requestId = viki.client.subscriptionId("a");
  socket.sent.length = 0;
  const vaultSessions = (live: string[], seq: number) =>
    socket.onmessage?.({
      data: JSON.stringify({
        v: 2,
        generation: "11111111-1111-4111-8111-111111111111",
        request_id: "",
        type: "vault.sessions",
        session_id: "",
        seq,
        data: { others: [], live },
      }),
    });
  vaultSessions([], 1); // a not-live frame lands before the close
  socket.onmessage?.({
    data: JSON.stringify({
      v: 2,
      generation: "11111111-1111-4111-8111-111111111111",
      request_id: requestId,
      session_id: "a",
      type: "session.closed",
      seq: 1,
      data: { reason: "requested" },
    }),
  });
  expect(viki.store.getSnapshot().sessions.a).toBeUndefined();
  socket.sent.length = 0;
  // Duplicates of the same frame (the store's own stale-seq guard drops these without
  // recommitting): "a" still leaves closing at the first one, not only when data changes.
  vaultSessions([], 1);
  vaultSessions([], 1);
  expect(socket.sent).toEqual([]);
  vaultSessions(["a"], 2); // live again elsewhere
  expect(socket.sent).toEqual([
    { type: "session.open", session_id: "a", after_seq: 0, request_id: expect.any(String) },
    {
      type: "history.more",
      session_id: "a",
      generation: expect.any(String),
      request_id: expect.any(String),
    },
  ]);
});

// Busy keeps the cursor (paging can resume) instead of nulling it like every other
// history.page error; only an explicit reloadHistory call may retry it.
test("a busy first page keeps the cursor; reloadHistory clears the error and re-requests", () => {
  const { viki, socket } = connected();
  viki.actions.openSession("a");
  const first = socket.sent.find((f) => f.type === "history.more") as {
    generation: string;
    request_id: string;
  };
  socket.receive({
    v: 2,
    generation: first.generation,
    request_id: first.request_id,
    type: "history.page",
    session_id: "a",
    seq: 1,
    data: { events: [], next_before_uuid: null, error: "busy" },
  });
  expect(viki.store.getSnapshot().sessions.a?.historyError).toBe("busy");
  // A first page never had a cursor to keep, so it stays undefined (never null: null means
  // "no older history", which busy has not established).
  expect(viki.store.getSnapshot().sessions.a?.historyCursor).toBeUndefined();

  socket.sent.length = 0;
  viki.actions.reloadHistory("a");
  expect(viki.store.getSnapshot().sessions.a?.historyError).toBeNull();
  expect(socket.sent).toEqual([
    {
      type: "history.more",
      session_id: "a",
      generation: expect.any(String),
      request_id: expect.any(String),
    },
  ]);
});

// A scroll-triggered loadOlder must not silently retry a busy page (no automatic
// retry); only the note's explicit reload button does.
test("loadOlder does not resend a busy later page; reloadHistory does", () => {
  const { viki, socket } = connected();
  viki.actions.openSession("a");
  const first = socket.sent.find((f) => f.type === "history.more") as {
    generation: string;
    request_id: string;
  };
  socket.receive({
    v: 2,
    generation: first.generation,
    request_id: first.request_id,
    type: "history.page",
    session_id: "a",
    seq: 1,
    data: { events: [], next_before_uuid: "cursor-1" },
  });
  expect(viki.store.getSnapshot().sessions.a?.historyCursor).toBe("cursor-1");

  socket.sent.length = 0;
  viki.actions.loadOlder("a");
  const second = socket.sent.find((f) => f.type === "history.more") as {
    generation: string;
    request_id: string;
  };
  expect(second).toBeTruthy();
  socket.receive({
    v: 2,
    generation: second.generation,
    request_id: second.request_id,
    type: "history.page",
    session_id: "a",
    seq: 2,
    data: { events: [], next_before_uuid: null, error: "busy" },
  });
  expect(viki.store.getSnapshot().sessions.a?.historyError).toBe("busy");
  expect(viki.store.getSnapshot().sessions.a?.historyCursor).toBe("cursor-1");

  socket.sent.length = 0;
  viki.actions.loadOlder("a"); // a further scroll near the top must not auto-retry
  expect(socket.sent).toEqual([]);

  viki.actions.reloadHistory("a"); // the note's own button does
  expect(viki.store.getSnapshot().sessions.a?.historyError).toBeNull();
  expect(socket.sent).toEqual([
    {
      type: "history.more",
      session_id: "a",
      generation: expect.any(String),
      request_id: expect.any(String),
      before_uuid: "cursor-1",
    },
  ]);
});

test("reconnect and replay retain the limit while stale snapshots cannot change it", () => {
  vi.useFakeTimers();
  const sockets: FakeSocket[] = [];
  const viki = createViki({
    url: "ws://127.0.0.1:8765/ws",
    token: "synthetic",
    createSocket: () => {
      const socket = new FakeSocket(false);
      sockets.push(socket);
      return socket;
    },
  });
  try {
    viki.client.connect();
    const socket = sockets[0];
    if (!socket) throw new Error("missing socket");
    socket.open();
    const id = viki.actions.newSession("a");
    const data = snapshotData();
    data.state.max_live_sessions = 8;
    socket.synchronize(id, GENERATION, 1, data);
    expect(viki.store.getSnapshot().maxLiveSessions).toBe(8);
    const stale = {
      v: 2,
      type: "session.snapshot",
      session_id: id,
      generation: GENERATION,
      request_id: viki.client.subscriptionId(id),
      seq: 99,
      data: { ...data, state: { ...data.state, max_live_sessions: 1 } },
    };
    socket.onclose?.({ code: 1006 });
    expect(viki.store.getSnapshot().maxLiveSessions).toBe(8);
    vi.advanceTimersByTime(500);
    const next = sockets[1];
    if (!next) throw new Error("missing reconnect");
    next.open();
    const requestId = viki.client.subscriptionId(id);
    expect(requestId).not.toBe(stale.request_id);
    const before = viki.store.getSnapshot();
    socket.receive({ ...stale, request_id: requestId });
    expect(viki.store.getSnapshot()).toBe(before);
    next.receive(stale);
    expect(viki.store.getSnapshot()).toBe(before);
    next.receive({
      v: 2,
      type: "vault.sessions",
      session_id: "",
      generation: GENERATION,
      request_id: "",
      seq: 1,
      data: { others: [], live: [id] },
    });
    const identity = {
      v: 2,
      session_id: id,
      generation: GENERATION,
      request_id: requestId,
      seq: 2,
    };
    next.receive({
      ...identity,
      type: "user.message",
      data: { text: "replayed", command: "", at: "" },
    });
    next.receive({ ...identity, type: "session.synced", data: {} });
    expect(viki.store.getSnapshot().sessions[id]).toMatchObject({ synced: true, seq: 2 });
    expect(viki.store.getSnapshot().maxLiveSessions).toBe(8);
    next.synchronize(id, GENERATION, 3);
    expect(viki.store.getSnapshot().maxLiveSessions).toBe(3);
  } finally {
    viki.client.close();
    vi.useRealTimers();
  }
});

test("kanban.changed reaches the store even with no tracked session (routed before the tracked check)", () => {
  const { viki, socket } = connected();
  socket.receive({
    v: 2,
    type: "kanban.changed",
    session_id: "",
    generation: GENERATION,
    request_id: "",
    seq: 1,
    data: { workspace_id: "w1" },
  });
  expect(viki.store.getSnapshot().kanban).toEqual({ w1: 1 });
});
