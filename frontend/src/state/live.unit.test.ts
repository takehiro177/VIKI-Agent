/**
 * Unit tests for liveSessionIds: what makes a session count against the three-session cap, and
 * the two ways a slot is given back. They drive a real AppStore rather than hand-built snapshots,
 * because the ordering guard that keeps a five-second-old server list from retiring a process it
 * never saw lives half in each file — the store records which session.started frames postdate the
 * list, live.ts reads that record.
 */
import { expect, test } from "vitest";
import type { KeySpec, SettingsView } from "../clients/app/shellApi";
import type { Envelope, SessionStartedData } from "../protocol";
import { settingsFixture } from "../testShell";
import { liveSessionIds, maxLiveFrom } from "./live";
import { AppStore } from "./store";

const INFO: SessionStartedData = {
  model: "sonnet",
  mode: "auto",
  effort: "low",
  commands: [],
  agents: [],
  models: [],
  cli_version: "2.1.259",
  resumed: false,
};

let seq = 0;

function frame(message: Omit<Envelope, "v" | "seq" | "generation" | "request_id">): Envelope {
  seq += 1;
  return {
    v: 2,
    generation: "11111111-1111-4111-8111-111111111111",
    request_id: "",
    seq,
    ...message,
  } as Envelope;
}

/** A real process reporting itself. */
function started(store: AppStore, id: string): void {
  store.dispatch(frame({ type: "session.started", session_id: id, data: INFO }));
}

/** The server's answer to "which sessions have a claude process", as of its last vault poll. */
function serverLive(store: AppStore, ids: string[]): void {
  store.dispatch(
    frame({ type: "vault.sessions", session_id: "", data: { others: [], live: ids } }),
  );
}

function errored(store: AppStore, id: string): void {
  store.dispatch(
    frame({
      type: "error",
      session_id: id,
      data: { code: "cli_missing", message: "claude.exe was not found", recoverable: true },
    }),
  );
}

const idsOf = (store: AppStore, startedHere: string[] = []) =>
  [...liveSessionIds(store.getSnapshot(), new Set(startedHere))].sort();

test("a process that reported itself counts, and closing the session stops it counting", () => {
  const store = new AppStore();
  started(store, "a");
  store.dispatch(
    frame({ type: "user.message", session_id: "b", data: { text: "hi", command: "", at: "" } }),
  );
  expect(idsOf(store)).toEqual(["a"]); // b has a view, but nothing ever said it has a process
  store.dispatch(frame({ type: "session.closed", session_id: "a", data: { reason: "requested" } }));
  expect(idsOf(store)).toEqual([]);
});

test("a brand-new session holds its slot from the click, against a list that predates it", () => {
  const store = new AppStore();
  // The click: viki.newSession seeds the view, App.tsx remembers the id, and the server starts
  // the process eagerly. The list the page already has was built before any of that happened, so
  // it says nothing about this id — and must not be read as saying the process is gone.
  serverLive(store, []);
  store.update("n", (view) => ({ ...view, historyCursor: null }));
  expect(idsOf(store, ["n"])).toEqual(["n"]);
  started(store, "n");
  expect(idsOf(store, ["n"])).toEqual(["n"]);
});

test("the server's list retires a session whose process died", () => {
  const store = new AppStore();
  started(store, "a");
  started(store, "b");
  expect(idsOf(store)).toEqual(["a", "b"]);
  // b's claude.exe exited: the server dropped its driver and the next poll stops listing it.
  // Nothing in b's own frames says so, which is why this list is the only thing that can.
  serverLive(store, ["a"]);
  expect(idsOf(store)).toEqual(["a"]);
  // And the slot comes back the moment b is resumed, without waiting for the next poll.
  started(store, "b");
  expect(idsOf(store)).toEqual(["a", "b"]);
});

test("a list built before session.started does not retire the process it never saw", () => {
  const store = new AppStore();
  // Three resumed sessions sent to inside one poll interval: each starts a process at once, and
  // session.started is the only word the page has about it for the next five seconds. Reading
  // the stale list as truth here would admit a fourth session the server then refuses to send.
  serverLive(store, []);
  started(store, "a");
  started(store, "b");
  started(store, "c");
  expect(idsOf(store)).toEqual(["a", "b", "c"]);
  // The next list postdates all three, so this one does get a vote.
  serverLive(store, ["a", "b"]);
  expect(idsOf(store)).toEqual(["a", "b"]);
});

test("a start that never produced a process gives its slot back", () => {
  const store = new AppStore();
  store.update("n", (view) => ({ ...view, historyCursor: null }));
  expect(idsOf(store, ["n"])).toEqual(["n"]);
  // StartError: a broken CLI install, billing, or the server's own cap. A start that worked would
  // have sent session.started first, so an error reaching a session that never started is that
  // failure — and the slot it is holding has no process behind it.
  errored(store, "n");
  expect(idsOf(store, ["n"])).toEqual([]);
  // Sending again starts one for real: session.started clears the error and the slot is real again.
  started(store, "n");
  expect(idsOf(store, ["n"])).toEqual(["n"]);
});

test("an error never retires a session that did start", () => {
  const store = new AppStore();
  started(store, "a");
  serverLive(store, ["a"]);
  // `internal` and `frame_error` come from perfectly live drivers too, so a browser reading codes
  // cannot tell a bad turn from a dead process. The server's list is what decides, and it still
  // lists a.
  errored(store, "a");
  expect(idsOf(store, ["a"])).toEqual(["a"]);
});

test("with no vault.sessions yet there is nothing to retire with", () => {
  const store = new AppStore();
  started(store, "a");
  expect(idsOf(store)).toEqual(["a"]);
});

/* maxLiveFrom: the live-session cap, resolved document-first. `chat.max_live_sessions` never
 * joins testShell's fixed fifteen keys (a frozen App test depends on its absence), so each case
 * here adds it to the fixture's document itself, as the server would send it. */

const BOUNDED_SPEC: KeySpec = {
  scope: "app",
  applies: "now",
  kind: "number",
  allowed: null,
  default: 30,
  personal: false,
  bounds: [1, 999],
};

function documentWithLimit(value: unknown): SettingsView {
  const base = settingsFixture();
  return {
    ...base,
    effective: { ...base.effective, "chat.max_live_sessions": value },
    keys: { ...base.keys, "chat.max_live_sessions": BOUNDED_SPEC },
  };
}

test("the document's value wins once the key is there and usable", () => {
  expect(maxLiveFrom(documentWithLimit(5), 8)).toBe(5);
});

test("an older document without the key falls back to the reported snapshot value", () => {
  expect(maxLiveFrom(settingsFixture(), 8)).toBe(8);
});

test("with no usable document at all, the reported snapshot value carries the app through", () => {
  expect(maxLiveFrom(null, 8)).toBe(8);
});

test("with neither a usable document nor a reported value, 3 is what this app always used", () => {
  expect(maxLiveFrom(null, undefined)).toBe(3);
  expect(maxLiveFrom(settingsFixture(), undefined)).toBe(3);
});

test.each([0, 2.5, "30", null])(
  "a document value that is not a usable safe integer (%p) is ignored, same as absent",
  (value) => {
    expect(maxLiveFrom(documentWithLimit(value), 8)).toBe(8);
  },
);
