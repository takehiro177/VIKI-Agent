import { afterEach, describe, expect, it, vi } from "vitest";
import { FakeSocket, GENERATION } from "../accept/m3b/fakes";
import { DurableApiError } from "../clients/conversations/durableApi";
import type { BotRow, BotsApi } from "../clients/messaging/botsApi";
import { type SavedBotThread, savedBotThread } from "../clients/messaging/botThreadGroups";
import { blankProfileDocument, type ProfileApi } from "../clients/profiles/profileApi";
import type {
  PreparedProfilePreview,
  ProfileLaunchApi,
} from "../clients/profiles/profileLaunchApi";
import type { Envelope, HistoryItem } from "../protocol";
import { FAKE_GENERATION, fakeDurable } from "../testDurable";
import type { Attachment } from "../turnInput";
import {
  createBotThreadStore,
  displayThreadTurn,
  isSteerWaiting,
  steerPrompt,
  type TeamThread,
  type ThreadMember,
  type ThreadMessage,
  threadDirective,
  threadDisplayOrder,
  threadMembers,
  threadOptions,
  threadTurn,
} from "./botThreads";
import { createEditorStore } from "./editors";
import { applyEnvelope } from "./sessionReducer";
import { AppStore } from "./store";
import { promptContext, readHandoff, type ThreadHandoff } from "./threadHandoff";
import type { SessionView, Turn } from "./view";
import { createViki, type Viki } from "./viki";

const id = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const AT = "2026-10-01T00:00:00.000Z";
const ENGINEER_ID = "3f1c9a6e-5b2d-4e8f-9a07-c41d2b6e8f53";
function bot(n: number): BotRow {
  return {
    bot_id: id(n),
    profile_id: id(n + 10),
    workspace_id: id(90),
    chat_session_id: id(n + 20),
    state_revision: 1,
    created_at: AT,
    updated_at: AT,
    profile: {
      revision: 1,
      archived: false,
      identity: { ...blankProfileDocument().identity, name: `Bot ${n}` },
    },
    workspace: { name: "Example vault", policy: "ordinary", active: true },
    chat: { profile_revision: 1, started: false, archived: false },
    routine_ids: [],
    delivery: { enabled: false, available: false, reason: "off" },
    latest_delivery: null,
  };
}
function named(n: number, name: string, profileId?: string): BotRow {
  const row = bot(n);
  if (row.profile) row.profile.identity.name = name;
  return profileId ? { ...row, profile_id: profileId } : row;
}
function attachment(n: number): Attachment {
  return {
    asset_id: id(n + 900),
    media_type: "image/png",
    byte_size: 10,
    width: 10,
    height: 10,
    name: `file-${n}.png`,
    modified_at: null,
    source: "picker",
  };
}
const cleanups: (() => void)[] = [];
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function setup(
  reply: (count: number) => string = () => "Done.\nVIKI_DONE",
  seed = "",
  withChecker = false,
) {
  const durable = fakeDurable();
  const editors = createEditorStore(durable, { storage: null });
  const app = new AppStore();
  app.setStatus("open");
  const bots = [bot(1), bot(2)];
  if (withChecker) {
    const checker = bot(3);
    if (checker.profile) checker.profile.identity.name = "Checker";
    bots.push(checker);
  }
  const api = {
    get: vi.fn(async (botId: string) => ({
      bot: bots.find((row) => row.bot_id === botId),
      chat_status: { state: "prepared" as const, blockers: [] },
    })),
  } as unknown as BotsApi;
  const launch = {
    prepare: vi.fn<ProfileLaunchApi["prepare"]>(
      async (profileId, _command, sessionId, profileRevision, _workspace, _signal, botId) => {
        durable.drafts.set(sessionId, {
          key: sessionId,
          revision: seed ? 1 : 0,
          text: seed,
          saved_at: null,
        });
        return {
          session_id: sessionId,
          profile_id: profileId,
          workspace_id: id(90),
          profile_revision: profileRevision,
          launchable: true,
          blockers: [],
          fields: [],
          warnings: [],
          prefs: { mode: "default", effort: "high", model: null },
          generation: FAKE_GENERATION,
          workspace_revision: 1,
          policy: "ordinary",
          policy_revision: 1,
          settings_revision: 0,
          runtime: "claude",
          capability_key: "a".repeat(64),
          started: false,
          ...(botId
            ? { bot_memory: { bot_id: botId, ref: `bot-memory/${botId}`, routine: false } }
            : {}),
          draft: {
            key: sessionId,
            revision: seed ? 1 : 0,
            text: seed,
            saved_at: null,
            attachments: [],
          },
        } satisfies PreparedProfilePreview;
      },
    ),
  } as unknown as ProfileLaunchApi;
  const interrupt = vi.fn(async () => "applied" as const);
  const canRun = vi.fn(() => true);
  const profiles = {} as ProfileApi;
  const viki = {
    store: app,
    actions: {
      openExistingSession: vi.fn((sessionId: string) =>
        app.update(sessionId, (view) => ({ ...view, synced: true, generation: FAKE_GENERATION })),
      ),
      prepareTarget: vi.fn(async (sessionId: string) => ({
        session_id: sessionId,
        generation: FAKE_GENERATION,
      })),
      interrupt,
      closeSession: vi.fn(async (sessionId: string) => {
        app.remove(sessionId);
        return "applied";
      }),
    },
  } as unknown as Viki;
  let count = 0;
  const submit = durable.submitCommand.bind(durable);
  const send = vi.spyOn(durable, "submitCommand").mockImplementation(async (input) => {
    const receipt = await submit(input);
    const submission = await durable.getSubmission(input.command_id);
    const answer = reply(count++);
    app.update(input.session_id, (view) => ({
      ...view,
      turns: [
        ...view.turns,
        {
          id: input.command_id,
          user: { text: submission.text, command: "", at: AT },
          blocks: [{ id: "text", kind: "text", text: answer, done: true }],
          status: "success",
          usage: null,
          durationMs: 0,
          history: false,
        } satisfies Turn,
      ],
    }));
    return receipt;
  });
  const store = createBotThreadStore({
    viki,
    editors,
    durable,
    api,
    profiles,
    launch,
    onPrepared: vi.fn(),
    canRun,
    startTimeoutMs: 50,
  });
  cleanups.push(() => store.dispose());
  store.setVisible(true);
  store.setContext(id(90), true);
  const members = threadMembers(bots, id(90)).filter((row) => row.bot);
  const threadId = store.create(members) as string;
  return {
    store,
    threadId,
    members,
    send,
    durable,
    editors,
    app,
    launch,
    api,
    interrupt,
    viki,
    canRun,
    profiles,
  };
}

function savedThread(thread: TeamThread): SavedBotThread {
  return {
    thread_id: thread.id,
    workspace_id: thread.workspaceId,
    revision: thread.revision ?? 1,
    created_at: AT,
    updated_at: AT,
    state: {
      title: thread.title,
      phase: thread.phase,
      members: thread.members.map((row) => ({
        key: row.key,
        bot_id: row.bot?.bot_id ?? row.botId ?? null,
        profile_id: row.profileId || null,
        session_id: row.sessionId,
        enabled: row.enabled,
      })),
      messages: thread.messages
        .filter((row) => row.commandId && row.sessionId)
        .map((row) => ({
          id: row.id,
          at: row.at,
          member_key: row.memberKey,
          command_id: row.commandId as string,
          session_id: row.sessionId as string,
        })),
    },
  };
}

function savedRows(f: ReturnType<typeof setup>, count: number): SavedBotThread[] {
  const base = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
  return Array.from({ length: count }, (_, index) => ({
    ...structuredClone(base),
    thread_id: id(200 + index),
    state: { ...structuredClone(base.state), title: `Thread ${index}` },
  }));
}

function restore(f: ReturnType<typeof setup>, rows: SavedBotThread[], groups?: BotsApi["groups"]) {
  f.api.groups = groups ?? vi.fn(async () => ({ threads: rows }));
  f.api.list = vi.fn(async () => ({
    bots: f.members.flatMap((row) => (row.bot ? [row.bot] : [])),
    scheduler: { state: "running" as const, attended: true, paused: false },
  }));
  const store = createBotThreadStore({
    viki: f.viki,
    editors: f.editors,
    durable: f.durable,
    api: f.api,
    profiles: f.profiles,
    launch: f.launch,
    onPrepared: vi.fn(),
    canRun: f.canRun,
    startTimeoutMs: 50,
  });
  cleanups.push(() => store.dispose());
  store.setVisible(true);
  store.setContext(id(90), true);
  return store;
}

const WAIT = { timeout: 50, interval: 1 };
const USAGE = { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0 };

/** One native event, through the real reducer. */
function event(app: AppStore, sessionId: string, item: HistoryItem) {
  app.update(sessionId, (view) =>
    applyEnvelope(view, {
      ...item,
      v: 2,
      session_id: sessionId,
      generation: FAKE_GENERATION,
      request_id: "native",
      seq: view.seq + 1,
    } as Envelope),
  );
}

function answer(app: AppStore, sessionId: string, text: string) {
  const seq = app.getSnapshot().sessions[sessionId]?.seq ?? 0;
  event(app, sessionId, { type: "text.done", data: { block_id: `text-${seq}`, text } });
  event(app, sessionId, {
    type: "turn.done",
    data: { status: "success", usage: USAGE, context_percent: null, duration_ms: 0 },
  });
}

/** The first Bot's chat runs T1 until the test ends it; its later commands are steers that wait,
 * shown as steer.pending unless `shown` is false. Other chats answer at once. */
function holdFirst(f: ReturnType<typeof setup>, shown = true) {
  const held = { sessionId: "", t1: "", steers: [] as string[] };
  const atOnce = f.send.getMockImplementation();
  f.send.mockImplementation(async (input) => {
    const text = f.durable.drafts.get(input.session_id)?.text ?? "";
    const receipt = (await atOnce?.(input)) as Awaited<ReturnType<typeof f.send>>;
    held.sessionId ||= input.session_id;
    if (input.session_id !== held.sessionId) return receipt;
    f.app.update(input.session_id, (view) => ({ ...view, turns: view.turns.slice(0, -1) }));
    if (!held.t1) {
      held.t1 = input.command_id;
      event(f.app, input.session_id, { type: "user.message", data: { text, command: "", at: AT } });
      event(f.app, input.session_id, { type: "turn.started", data: { turn_id: held.t1 } });
    } else {
      const steerId = id(700 + held.steers.length);
      held.steers.push(steerId);
      if (shown)
        event(f.app, input.session_id, {
          type: "steer.pending",
          data: { steer_id: steerId, text: text.slice(0, 2048), command: "", images: 0, at: AT },
        });
    }
    return receipt;
  });
  return held;
}

/** Claude Code reads the steer: steer.ended, then its echo, folded or as a new turn. */
function readSteer(f: ReturnType<typeof setup>, held: ReturnType<typeof holdFirst>, n = 0) {
  event(f.app, held.sessionId, {
    type: "steer.ended",
    data: { steer_id: held.steers[n] as string, outcome: "started" },
  });
  const text = [...f.durable.commands.values()][n + 1]?.text ?? "";
  event(f.app, held.sessionId, { type: "user.message", data: { text, command: "", at: AT } });
}

/** The working Bot's chat refuses each steer post, as a busy profile chat answers. */
function refuseSteers(f: ReturnType<typeof setup>, held: ReturnType<typeof holdFirst>) {
  const holding = f.send.getMockImplementation();
  f.send.mockImplementation(async (input) => {
    if (held.t1 && input.session_id === held.sessionId)
      throw new DurableApiError(409, "turn_running");
    return holding?.(input) as ReturnType<typeof f.send>;
  });
}

function saves(f: ReturnType<typeof setup>) {
  const rows: SavedBotThread[] = [];
  f.api.saveGroup = vi.fn(async (workspaceId, threadId, revision, state) => {
    const row = {
      workspace_id: workspaceId,
      thread_id: threadId,
      revision: revision + 1,
      state: structuredClone(state),
      created_at: AT,
      updated_at: AT,
    };
    rows.push(row);
    return row;
  });
  return rows;
}

async function sourcePages() {
  const f = setup(() => "Review the result.");
  await f.store.send(f.threadId, "original request");
  const thread = f.store.getSnapshot().threads[0] as TeamThread;
  const message = thread.messages[1] as ThreadMessage;
  f.launch.inspect = vi.fn(
    async () =>
      (await vi.mocked(f.launch.prepare).mock.results[0]?.value) as PreparedProfilePreview,
  );
  const socket = new FakeSocket(false);
  const viki = createViki({
    url: "ws://127.0.0.1:8765/ws",
    token: "synthetic",
    createSocket: () => socket,
    controlTimeoutMs: 50,
  });
  viki.client.connect();
  socket.open();
  cleanups.push(() => viki.client.close());
  const store = restore({ ...f, viki }, [savedThread(thread)]);
  await vi.waitFor(
    () => expect(socket.sent.some((row) => row.type === "session.open")).toBe(true),
    WAIT,
  );
  socket.synchronize(message.sessionId as string);
  const requests = () => socket.sent.filter((row) => row.type === "history.more");
  const page = (events: HistoryItem[], cursor: string | null) => {
    const request = requests().at(-1);
    socket.receive({
      v: 2,
      type: "history.page",
      session_id: message.sessionId,
      generation: GENERATION,
      request_id: request?.request_id,
      data: { events, next_before_uuid: cursor },
    });
  };
  const events = (prompt: string, reply: string): HistoryItem[] => [
    { type: "user.message", data: { text: prompt, command: "", at: AT } },
    { type: "text.done", data: { block_id: "text", text: reply } },
  ];
  return { ...f, store, socket, viki, message, requests, page, events };
}

describe("shared Bot threads", () => {
  it("keeps explicitly started work running when the thread view closes", async () => {
    const f = setup();
    const held = holdFirst(f);
    const running = f.store.send(f.threadId, "Keep working");
    await vi.waitFor(() => expect(held.t1).not.toBe(""), WAIT);
    f.store.setVisible(false);
    expect(f.interrupt).not.toHaveBeenCalled();
    expect(f.store.getSnapshot().busy).toBe(true);
    answer(f.app, held.sessionId, "Please inspect the result.");
    await running;
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().busy).toBe(false);
  });

  it("creates and toggles up to 64 participants without preparing their chats", () => {
    const f = setup();
    const members = threadMembers(
      Array.from({ length: 65 }, (_, n) => bot(n + 1)),
      id(90),
    ).filter((member) => member.bot);
    expect(f.store.create(members)).toBeNull();
    const threadId = f.store.create(members.slice(0, 64));
    expect(threadId).not.toBeNull();
    if (!threadId) return;
    const enabled = () =>
      f.store
        .getSnapshot()
        .threads.find((thread) => thread.id === threadId)
        ?.members.filter((member) => member.enabled);
    expect(enabled()).toHaveLength(64);
    f.store.toggle(threadId, members[64] as ThreadMember);
    expect(enabled()).toHaveLength(64);
    f.store.toggle(threadId, members[63] as ThreadMember);
    expect(enabled()).toHaveLength(63);
    f.store.toggle(threadId, members[63] as ThreadMember);
    expect(enabled()).toHaveLength(64);
    expect(f.launch.prepare).not.toHaveBeenCalled();
  });

  it("orders only the requested workspace newest first without mutating input", () => {
    const f = setup();
    const base = f.store.getSnapshot().threads[0];
    if (!base) throw new Error("setup did not create a thread");
    const threads = [
      { ...base, id: "old", workspaceId: "a" },
      { ...base, id: "other", workspaceId: "b" },
      { ...base, id: "new", workspaceId: "a" },
    ];
    expect(threadDisplayOrder(threads, "a").map((row) => row.id)).toEqual(["new", "old"]);
    expect(threadDisplayOrder(threads, null)).toEqual([]);
    expect(threads.map((row) => row.id)).toEqual(["old", "other", "new"]);
  });

  it("selects newest on context change and keeps a valid selection on reload", async () => {
    const f = setup();
    const newest = f.store.create(f.members) as string;
    f.store.setContext(id(91), true);
    f.store.setContext(id(90), true);
    expect(f.store.getSnapshot().selectedId).toBe(newest);

    const rows = savedRows(f, 3);
    const store = restore(f, rows);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    const middle = threadDisplayOrder(store.getSnapshot().threads, id(90))[1]?.id as string;
    store.select(middle);
    store.reload();
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(store.getSnapshot().selectedId).toBe(middle);
  });

  it.each(["fresh", "restored", "archived main"])(
    "fills an unbound %s seat without preparing its unused main chat",
    async (kind) => {
      const f = setup(() => "Review the result.");
      const seat = threadMembers([], id(90))[0] as ThreadMember;
      const filled = bot(1);
      filled.profile_id = seat.profileId;
      filled.chat = { ...filled.chat, archived: true };
      f.api.list = vi.fn(async () => ({
        bots: kind === "archived main" ? [filled] : [],
        scheduler: { state: "running" as const, attended: true, paused: false },
      }));
      f.api.create = vi.fn(async () => ({ bot: filled, replayed: false }));
      f.api.chat = vi.fn().mockRejectedValue(new Error("main chat is archived"));
      f.profiles.list = vi.fn(async () => []);
      f.profiles.create = vi.fn().mockResolvedValue({});
      const threadId = f.store.create([seat]) as string;
      const store =
        kind === "restored"
          ? restore(f, [
              savedThread(
                f.store.getSnapshot().threads.find((row) => row.id === threadId) as TeamThread,
              ),
            ])
          : f.store;
      if (kind === "restored")
        await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
      await store.send(threadId, "work in a dedicated source");
      expect(f.api.chat).not.toHaveBeenCalled();
      expect(f.launch.prepare).toHaveBeenCalledTimes(1);
      expect(f.send).toHaveBeenCalledTimes(1);
      expect(
        store.getSnapshot().threads.find((row) => row.id === threadId)?.members[0]?.botId,
      ).toBe(filled.bot_id);
      expect(f.api.create).toHaveBeenCalledTimes(kind === "archived main" ? 0 : 1);
    },
  );

  it("pages native source history past twenty newer steer replies", async () => {
    const f = await sourcePages();
    const first = Array.from({ length: 20 }, (_, n) =>
      f.events(steerPrompt(f.threadId, id(500 + n), `newer steer ${n}`), `reply ${n}`),
    ).flat();
    f.page(first, id(600));
    expect(f.requests()).toHaveLength(2);
    expect(f.requests()[1]?.before_uuid).toBe(id(600));
    expect(f.store.getSnapshot().threads[0]?.sourceStatus).toBe("loading");
    f.page(f.events(f.message.prompt as string, "older original reply"), null);
    await vi.waitFor(
      () => expect(f.store.getSnapshot().threads[0]?.sourceStatus).toBe("ready"),
      WAIT,
    );
    expect(f.store.getSnapshot().threads[0]?.messages[1]?.turn?.blocks[0]).toMatchObject({
      text: "older original reply",
    });
    expect(f.socket.sent.filter((row) => row.type === "session.detach")).toHaveLength(1);
    expect(f.viki.store.getSnapshot().sessions[f.message.sessionId as string]).toBeUndefined();
  });

  it.each(["repeat", "unique", "error", "cancel", "exhausted", "timeout"])(
    "bounds source pages and releases history on %s",
    async (kind) => {
      if (kind === "timeout") vi.useFakeTimers();
      const f = await sourcePages();
      f.page([], id(600));
      expect(f.requests()).toHaveLength(2);
      if (kind === "repeat") f.page([], id(600));
      else if (kind === "unique") {
        for (let n = 1; n < 20 && f.store.getSnapshot().threads[0]?.sourceStatus === "loading"; n++)
          f.page([], id(600 + n));
        expect(f.requests()).toHaveLength(17);
      } else if (kind === "error") {
        f.viki.store.update(f.message.sessionId as string, (view) => ({
          ...view,
          historyError: "busy",
          historyLoading: false,
        }));
      } else if (kind === "cancel") f.store.setContext(id(91), true);
      else if (kind === "exhausted") f.page([], null);
      else if (kind === "timeout") {
        await vi.advanceTimersByTimeAsync(25);
        f.page([], id(601));
        await vi.advanceTimersByTimeAsync(25);
      }
      if (kind !== "cancel")
        await vi.waitFor(
          () => expect(f.store.getSnapshot().threads[0]?.sourceStatus).toBe("unavailable"),
          WAIT,
        );
      await vi.waitFor(
        () => expect(f.socket.sent.filter((row) => row.type === "session.detach")).toHaveLength(1),
        WAIT,
      );
      expect(f.viki.store.getSnapshot().sessions[f.message.sessionId as string]).toBeUndefined();
      expect(f.requests().length).toBeLessThanOrEqual(17);
    },
  );

  it.each(["empty", "tool", "thinking", "no assistant blocks"])(
    "keeps a matched native source with %s output",
    async (kind) => {
      const f = await sourcePages();
      const events: HistoryItem[] = [
        { type: "user.message", data: { text: f.message.prompt as string, command: "", at: AT } },
      ];
      if (kind === "empty")
        events.push({ type: "text.done", data: { block_id: "text", text: "" } });
      else if (kind === "thinking")
        events.push({
          type: "thinking.done",
          data: { block_id: "thinking", text: "considered", duration_ms: 0 },
        });
      else if (kind === "tool")
        events.push(
          {
            type: "tool.started",
            data: {
              block_id: "tool",
              tool: "Read",
              row: { head: "Read", rest: "example.md", tail: "" },
              parent_id: "",
            },
          },
          {
            type: "tool.done",
            data: { block_id: "tool", is_error: false, summary: "read", preview: "", tail: "" },
          },
        );
      f.page(events, id(600));
      await vi.waitFor(
        () => expect(f.store.getSnapshot().threads[0]?.sourceStatus).toBe("ready"),
        WAIT,
      );
      expect(f.requests()).toHaveLength(1);
      expect(f.store.getSnapshot().threads[0]?.messages[1]?.turn).toBeDefined();
    },
  );

  it("waits for pending saves before reload and keeps the acknowledged member state", async () => {
    const f = setup();
    let current = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
    const ack = deferred<void>();
    const groups = vi.fn(async () => ({ threads: [structuredClone(current)] }));
    const store = restore(f, [current], groups);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    f.api.saveGroup = vi.fn(async (workspaceId, threadId, revision, state) => {
      await ack.promise;
      current = {
        ...current,
        workspace_id: workspaceId,
        thread_id: threadId,
        revision: revision + 1,
        state: structuredClone(state),
      };
      return current;
    });
    store.edit(f.threadId, "reload discards this unsent draft");
    store.toggle(f.threadId, f.members[0] as ThreadMember);
    await vi.waitFor(() => expect(f.api.saveGroup).toHaveBeenCalledTimes(1), WAIT);
    store.reload();
    expect(store.getSnapshot().loading).toBe(true);
    expect(groups).toHaveBeenCalledTimes(1);
    ack.resolve();
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(store.getSnapshot().threads[0]).toMatchObject({ revision: 2, draft: "" });
    expect(store.getSnapshot().threads[0]?.members[0]?.enabled).toBe(false);
    store.toggle(f.threadId, f.members[1] as ThreadMember);
    await vi.waitFor(() => expect(f.api.saveGroup).toHaveBeenCalledTimes(2), WAIT);
    expect(current.state.members.map((row) => row.enabled)).toEqual([false, false]);
  });

  it("clears a pending save failure only after a successful manual reload", async () => {
    const f = setup();
    const saved = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
    const store = restore(f, [saved]);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    const pending = deferred<SavedBotThread>();
    f.api.saveGroup = vi
      .fn()
      .mockReturnValueOnce(pending.promise)
      .mockImplementation(async (_workspaceId, _threadId, revision, state) => ({
        ...saved,
        revision: revision + 1,
        state,
      }));
    store.toggle(f.threadId, f.members[0] as ThreadMember);
    await vi.waitFor(() => expect(f.api.saveGroup).toHaveBeenCalledTimes(1), WAIT);
    store.reload();
    pending.reject(new Error("save failed"));
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    store.toggle(f.threadId, f.members[1] as ThreadMember);
    await vi.waitFor(() => expect(f.api.saveGroup).toHaveBeenCalledTimes(2), WAIT);
    expect(store.getSnapshot().threads[0]?.note).not.toBe("bots.thread.saveError");
  });

  it("blocks mutations while an older GET is unresolved", async () => {
    const f = setup();
    const saved = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
    const pending = deferred<{ threads: SavedBotThread[] }>();
    const groups = vi
      .fn()
      .mockResolvedValueOnce({ threads: [saved] })
      .mockReturnValueOnce(pending.promise);
    const store = restore(f, [saved], groups);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    f.api.saveGroup = vi.fn();
    f.api.removeGroup = vi.fn();
    store.reload();
    store.toggle(f.threadId, f.members[0] as ThreadMember);
    expect(store.create(f.members)).toBeNull();
    await store.send(f.threadId, "must wait for GET");
    expect(await store.remove(f.threadId)).toBe(false);
    expect(f.api.saveGroup).not.toHaveBeenCalled();
    expect(f.api.removeGroup).not.toHaveBeenCalled();
    expect(f.send).not.toHaveBeenCalled();
    pending.resolve({ threads: [saved] });
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(store.getSnapshot().threads[0]?.members[0]?.enabled).toBe(true);
  });

  it("waits for deletion before reloading and blocks writes during removal", async () => {
    const f = setup();
    let rows = [savedThread(f.store.getSnapshot().threads[0] as TeamThread)];
    const deleted = deferred<void>();
    const groups = vi.fn(async () => ({ threads: structuredClone(rows) }));
    const store = restore(f, rows, groups);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    f.api.saveGroup = vi.fn();
    f.api.removeGroup = vi.fn(async () => {
      await deleted.promise;
      rows = [];
    });
    const removing = store.remove(f.threadId);
    store.toggle(f.threadId, f.members[0] as ThreadMember);
    expect(store.create(f.members)).toBeNull();
    await store.send(f.threadId, "must wait for DELETE");
    await vi.waitFor(() => expect(f.api.removeGroup).toHaveBeenCalledTimes(1), WAIT);
    store.reload();
    expect(groups).toHaveBeenCalledTimes(1);
    expect(f.api.saveGroup).not.toHaveBeenCalled();
    expect(f.send).not.toHaveBeenCalled();
    deleted.resolve();
    expect(await removing).toBe(true);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(groups).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot().threads).toEqual([]);
  });

  it.each([
    ["first", 0, 3, id(201)],
    ["middle", 1, 3, id(200)],
    ["last", 2, 3, id(201)],
    ["only", 0, 1, null],
  ] as const)(
    "selects next then previous display row after deleting %s",
    async (_name, index, count, expected) => {
      const f = setup();
      const store = restore(f, savedRows(f, count));
      await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
      const ordered = threadDisplayOrder(store.getSnapshot().threads, id(90));
      const target = ordered[index]?.id as string;
      if (index !== 0) store.select(target);
      const emissions = vi.fn();
      store.subscribe(emissions);
      expect(await store.remove(target)).toBe(true);
      expect(store.getSnapshot().selectedId).toBe(expected);
      expect(store.getSnapshot().threads.some((row) => row.id === target)).toBe(false);
      expect(emissions).toHaveBeenCalledTimes(1);
    },
  );

  it("keeps selection when deleting an unselected row", async () => {
    const f = setup();
    const store = restore(f, savedRows(f, 3));
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    const ordered = threadDisplayOrder(store.getSnapshot().threads, id(90));
    store.select(ordered[0]?.id as string);
    const selected = store.getSnapshot().selectedId;
    expect(await store.remove(ordered[1]?.id as string)).toBe(true);
    expect(store.getSnapshot().selectedId).toBe(selected);
  });

  it("keeps the selected neighbor when reload races a selected deletion", async () => {
    const f = setup();
    let rows = savedRows(f, 3);
    const groups = vi.fn(async () => ({ threads: structuredClone(rows) }));
    const store = restore(f, rows, groups);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    const ordered = threadDisplayOrder(store.getSnapshot().threads, id(90));
    const target = ordered[1]?.id as string;
    const neighbor = ordered[2]?.id as string;
    store.select(target);
    const deleted = deferred<void>();
    f.api.removeGroup = vi.fn(async () => {
      await deleted.promise;
      rows = rows.filter((row) => row.thread_id !== target);
    });
    const removing = store.remove(target);
    await vi.waitFor(() => expect(f.api.removeGroup).toHaveBeenCalledTimes(1), WAIT);
    store.reload();
    deleted.resolve();
    expect(await removing).toBe(true);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(store.getSnapshot().selectedId).toBe(neighbor);
    expect(groups).toHaveBeenCalledTimes(2);
  });

  it("returns false and keeps selection when deletion fails", async () => {
    const f = setup();
    const store = restore(f, savedRows(f, 2));
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    const selected = store.getSnapshot().selectedId as string;
    f.api.removeGroup = vi.fn(async () => {
      throw new Error("delete failed");
    });
    expect(await store.remove(selected)).toBe(false);
    expect(store.getSnapshot()).toMatchObject({ selectedId: selected });
    expect(store.getSnapshot().threads.find((row) => row.id === selected)?.note).toBe(
      "bots.thread.saveError",
    );
  });

  it("hydrates the chosen neighbor after removal clears", async () => {
    const f = setup(() => "Review the result.");
    await f.store.send(f.threadId, "original request");
    const saved = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
    const neighbor = {
      ...structuredClone(saved),
      thread_id: id(75),
      state: { ...structuredClone(saved.state), title: "Neighbor" },
    };
    const target = {
      ...structuredClone(saved),
      state: { ...structuredClone(saved.state), messages: [] },
    };
    const getSubmission = vi.spyOn(f.durable, "getSubmission");
    const store = restore(f, [neighbor, target]);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(getSubmission).not.toHaveBeenCalled();
    expect(await store.remove(target.thread_id)).toBe(true);
    await vi.waitFor(
      () => expect(getSubmission).toHaveBeenCalledTimes(neighbor.state.messages.length),
      WAIT,
    );
    expect(store.getSnapshot().selectedId).toBe(neighbor.thread_id);
  });

  it.each(["succeeds", "fails"])(
    "hydrates a selection changed during removal when deletion %s",
    async (outcome) => {
      const f = setup(() => "Review the result.");
      await f.store.send(f.threadId, "original request");
      const saved = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
      const selected = {
        ...structuredClone(saved),
        state: { ...structuredClone(saved.state), messages: saved.state.messages.slice(0, 1) },
      };
      const target = {
        ...structuredClone(saved),
        thread_id: id(75),
        state: { ...structuredClone(saved.state), messages: [] },
      };
      const getSubmission = vi.spyOn(f.durable, "getSubmission");
      const store = restore(f, [selected, target]);
      await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
      const deleted = deferred<void>();
      f.api.removeGroup = vi.fn(async () => {
        await deleted.promise;
        if (outcome === "fails") throw new Error("delete failed");
      });
      const removing = store.remove(target.thread_id);
      await vi.waitFor(() => expect(f.api.removeGroup).toHaveBeenCalledTimes(1), WAIT);
      store.select(selected.thread_id);
      expect(getSubmission).not.toHaveBeenCalled();
      deleted.resolve();
      expect(await removing).toBe(outcome === "succeeds");
      await vi.waitFor(
        () =>
          expect(
            store.getSnapshot().threads.find((row) => row.id === selected.thread_id),
          ).toMatchObject({ messages: [{ text: "original request" }], sourceStatus: "ready" }),
        WAIT,
      );
      expect(store.getSnapshot().selectedId).toBe(selected.thread_id);
      expect(getSubmission).toHaveBeenCalledTimes(1);
      if (outcome === "fails") {
        expect(store.getSnapshot().threads.find((row) => row.id === target.thread_id)?.note).toBe(
          "bots.thread.saveError",
        );
      }
    },
  );

  it("reloads clean reset groups from the authoritative workspace", async () => {
    const f = setup();
    let rows = [savedThread(f.store.getSnapshot().threads[0] as TeamThread)];
    const groups = vi.fn(async () => ({ threads: structuredClone(rows) }));
    const store = restore(f, rows, groups);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    rows = [];
    store.invalidate(id(90));
    await vi.waitFor(() => expect(groups).toHaveBeenCalledTimes(2), WAIT);
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(store.getSnapshot()).toMatchObject({ threads: [], selectedId: null, error: null });
  });

  it.each(["unselected draft", "attachments only"])(
    "preserves every reset input and latches %s until manual reload",
    async (kind) => {
      const f = setup();
      const second = f.store.create(f.members) as string;
      let rows = f.store.getSnapshot().threads.map(savedThread);
      const groups = vi.fn(async (_workspaceId: string) => ({ threads: structuredClone(rows) }));
      const store = restore(f, rows, groups);
      await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
      if (kind === "unselected draft") store.edit(second, "keep unselected input");
      else store.attach(second, [attachment(1)]);
      f.api.saveGroup = vi.fn();
      f.api.removeGroup = vi.fn();
      rows = [];
      store.invalidate(id(90));
      await vi.waitFor(
        () => expect(store.getSnapshot().error).toBe("bots.thread.resetRequired"),
        WAIT,
      );
      expect(groups).toHaveBeenCalledTimes(1);
      expect(store.getSnapshot().threads).toHaveLength(2);
      expect(store.getSnapshot().threads.find((row) => row.id === second)).toMatchObject(
        kind === "unselected draft"
          ? { draft: "keep unselected input" }
          : { attachments: [attachment(1)] },
      );
      store.toggle(f.threadId, f.members[0] as ThreadMember);
      expect(store.create(f.members)).toBeNull();
      await store.send(f.threadId, "must reload");
      await store.remove(f.threadId);
      expect(f.api.saveGroup).not.toHaveBeenCalled();
      expect(f.api.removeGroup).not.toHaveBeenCalled();
      expect(f.send).not.toHaveBeenCalled();
      store.edit(f.threadId, "editing remains available");
      expect(store.getSnapshot().threads[0]?.draft).toBe("editing remains available");
      store.edit(f.threadId, "");
      store.edit(second, "");
      store.attach(second, []);
      store.invalidate(id(90));
      await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
      expect(store.getSnapshot().threads).toHaveLength(2);
      expect(store.getSnapshot().error).toBe("bots.thread.resetRequired");
      store.setContext(id(91), true);
      await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
      store.setContext(id(90), true);
      expect(store.getSnapshot().error).toBe("bots.thread.resetRequired");
      expect(groups.mock.calls.map((call) => call[0])).toEqual([id(90), id(91)]);
      store.reload();
      await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
      expect(store.getSnapshot()).toMatchObject({ threads: [], error: null });
    },
  );

  it("does not invalidate another workspace's outstanding GET", async () => {
    const f = setup();
    const saved = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
    const pending = deferred<{ threads: SavedBotThread[] }>();
    const groups = vi.fn(() => pending.promise);
    const store = restore(f, [], groups);
    store.invalidate(id(91));
    pending.resolve({ threads: [saved] });
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(store.getSnapshot().threads[0]?.id).toBe(f.threadId);
    expect(groups).toHaveBeenCalledTimes(1);
  });

  it("keeps another workspace's history observer alive across invalidation", async () => {
    const f = await sourcePages();
    f.store.invalidate(id(91));
    expect(f.socket.sent.filter((row) => row.type === "session.detach")).toHaveLength(0);
    f.page(f.events(f.message.prompt as string, "source reply"), null);
    await vi.waitFor(
      () => expect(f.store.getSnapshot().threads[0]?.sourceStatus).toBe("ready"),
      WAIT,
    );
    expect(f.socket.sent.filter((row) => row.type === "session.detach")).toHaveLength(1);
  });

  it("releases invalidated source history and ignores its late page", async () => {
    const f = await sourcePages();
    f.api.groups = vi.fn(async () => ({ threads: [] }));
    f.store.invalidate(id(90));
    await vi.waitFor(() => expect(f.store.getSnapshot().threads).toEqual([]), WAIT);
    await vi.waitFor(
      () => expect(f.socket.sent.filter((row) => row.type === "session.detach")).toHaveLength(1),
      WAIT,
    );
    f.page(f.events(f.message.prompt as string, "late source reply"), null);
    expect(f.store.getSnapshot().threads).toEqual([]);
  });

  it("does not fence another workspace's deferred source retirement", async () => {
    const f = setup(() => "Review the result.");
    const held = holdFirst(f);
    const running = f.store.send(f.threadId, "stop this source");
    await vi.waitFor(() => expect(held.t1).not.toBe(""), WAIT);
    f.store.stop();
    await running;
    f.store.invalidate(id(91));
    answer(f.app, held.sessionId, "Review the result.");
    await vi.waitFor(() => expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(1), WAIT);
  });

  it("fences a reset target's outstanding GET", async () => {
    const f = setup();
    const saved = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
    const pending = deferred<{ threads: SavedBotThread[] }>();
    const groups = vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValue({ threads: [] });
    const store = restore(f, [], groups);
    store.invalidate(id(90));
    await vi.waitFor(() => expect(groups).toHaveBeenCalledTimes(2), WAIT);
    pending.resolve({ threads: [saved] });
    await pending.promise;
    await vi.waitFor(() => expect(store.getSnapshot().loading).toBe(false), WAIT);
    expect(store.getSnapshot().threads).toEqual([]);
  });

  it("queues an active reset until run cleanup and its final save drain", async () => {
    const f = setup(() => "Review the result.");
    const held = holdFirst(f);
    const final = deferred<void>();
    const savingFinal = vi.fn();
    f.api.saveGroup = vi.fn(async (workspaceId, threadId, revision, state) => {
      if (state.phase === "paused" && state.messages.length) {
        savingFinal();
        await final.promise;
      }
      return {
        workspace_id: workspaceId,
        thread_id: threadId,
        revision: revision + 1,
        state,
        created_at: AT,
        updated_at: AT,
      };
    });
    f.api.groups = vi.fn(async () => ({ threads: [] }));
    f.api.list = vi.fn(async () => ({
      bots: [],
      scheduler: { state: "running" as const, attended: true, paused: false },
    }));
    const running = f.store.send(f.threadId, "working during reset");
    await vi.waitFor(() => expect(held.t1).not.toBe(""), WAIT);
    f.store.invalidate(id(90));
    expect(f.interrupt).not.toHaveBeenCalled();
    expect(f.api.groups).not.toHaveBeenCalled();
    answer(f.app, held.sessionId, "Review the result.");
    await vi.waitFor(() => expect(savingFinal).toHaveBeenCalledTimes(1), WAIT);
    expect(f.api.groups).not.toHaveBeenCalled();
    expect(f.interrupt).not.toHaveBeenCalled();
    final.resolve();
    await running;
    await vi.waitFor(() => expect(f.api.groups).toHaveBeenCalledTimes(1), WAIT);
    await vi.waitFor(() => expect(f.store.getSnapshot().loading).toBe(false), WAIT);
    expect(f.store.getSnapshot().threads).toEqual([]);
    expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(1);
  });
  it("strips only the validated final line with bounded rendering cost", () => {
    const prefix = `VIKI_RESULT ${" ".repeat(16000)}`;
    const turn = {
      id: "bounded",
      user: null,
      status: "success",
      usage: null,
      durationMs: 0,
      blocks: [{ id: "text", kind: "text", done: true, text: `${prefix}\nVIKI_DONE` }],
    } as Turn;
    for (let i = 0; i < 24; i++) {
      expect(displayThreadTurn(turn).blocks).toEqual([
        { id: "text", kind: "text", done: true, text: prefix.trimEnd() },
      ]);
    }
  }, 50);

  const packet = (change: Partial<ThreadHandoff> = {}) =>
    `VIKI_RESULT ${JSON.stringify({
      to: null,
      objective: "Compare the options",
      artifacts: ["notes/comparison.md"],
      decisions: ["Use the documented option"],
      questions: [],
      next_gate: "review",
      criteria: ["Both options have sourced evidence"],
      evidence: ["Reviewed source refs and both option sections"],
      verdict: null,
      progress: "Comparison written",
      ...change,
    })}`;

  it("normalizes title codepoints while retaining the exact request", async () => {
    const f = setup(() => "Please review the result.");
    f.api.saveGroup = vi.fn(async (workspaceId, threadId, revision, state) => {
      if (
        [...state.title].some((char) => (char.codePointAt(0) ?? 0) < 32) ||
        !state.title.isWellFormed()
      )
        throw new Error("invalid_title");
      return {
        workspace_id: workspaceId,
        thread_id: threadId,
        revision: revision + 1,
        state,
        created_at: AT,
        updated_at: AT,
      };
    });
    const request = ` \n\t${"a".repeat(62)}😀last\u0000 tail  `;
    await f.store.send(f.threadId, request);
    const title = f.store.getSnapshot().threads[0]?.title;
    expect(title).toBe(`${"a".repeat(62)}😀l`);
    expect(title?.isWellFormed()).toBe(true);
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(promptContext([...f.durable.commands.values()][0]?.text ?? "")?.request).toBe(request);
    expect(f.store.getSnapshot().threads[0]?.messages[0]?.text).toBe(request);
  });

  it.each([undefined, "newer draft", ""])(
    "restores definitely unposted compose without replacing newer edits (%s)",
    async (newer) => {
      const f = setup();
      f.store.edit(f.threadId, "unsent request");
      vi.mocked(f.launch.prepare).mockImplementation(async () => {
        if (newer !== undefined) f.store.edit(f.threadId, newer);
        throw new Error("prepare_failed");
      });
      await f.store.send(f.threadId, "unsent request");
      expect(f.send).not.toHaveBeenCalled();
      expect(f.store.getSnapshot().threads[0]).toMatchObject({
        draft: newer ?? "unsent request",
        messages: [],
        phase: "paused",
      });
    },
  );

  it("gives files only to the first member's editor and submit; a handoff member gets none", async () => {
    const f = setup((n) => (n === 0 ? `Continue.\nVIKI_HANDOFF ${id(2)}` : "Done.\nVIKI_DONE"));
    const attachments = [attachment(1)];
    const attachSpy = vi.spyOn(f.editors, "attach");
    const submitSpy = vi.spyOn(f.editors, "submit");
    await f.store.send(f.threadId, "request", undefined, attachments);
    expect(f.send).toHaveBeenCalledTimes(2);
    const sessions = vi.mocked(f.launch.prepare).mock.calls.map((args) => args[2]);
    expect(attachSpy).toHaveBeenCalledTimes(1);
    expect(attachSpy).toHaveBeenCalledWith(sessions[0], attachments);
    expect(submitSpy.mock.calls[0]?.[3]).toEqual(attachments);
    expect(submitSpy.mock.calls[1]?.[3]).toEqual([]);
  });

  it("restores unsent text and files when a send is refused before submit", async () => {
    const f = setup();
    const attachments = [attachment(1)];
    vi.mocked(f.launch.prepare).mockRejectedValue(new Error("prepare_failed"));
    await f.store.send(f.threadId, "unsent request", undefined, attachments);
    expect(f.send).not.toHaveBeenCalled();
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      draft: "unsent request",
      attachments,
      messages: [],
      phase: "paused",
    });
  });

  it.each(["edit", "attach"] as const)(
    "keeps the newer draft when a %s happens during a refused send",
    async (action) => {
      const f = setup();
      const attachments = [attachment(1)];
      const newerAttachments = [attachment(2)];
      vi.mocked(f.launch.prepare).mockImplementation(async () => {
        if (action === "edit") f.store.edit(f.threadId, "newer draft");
        else f.store.attach(f.threadId, newerAttachments);
        throw new Error("prepare_failed");
      });
      await f.store.send(f.threadId, "unsent request", undefined, attachments);
      expect(f.send).not.toHaveBeenCalled();
      const thread = f.store.getSnapshot().threads[0];
      expect(thread?.draft).toBe(action === "edit" ? "newer draft" : "");
      expect(thread?.attachments).toEqual(action === "edit" ? [] : newerAttachments);
    },
  );

  it("never carries compose attachments into the saved thread state", async () => {
    const f = setup();
    const attachments = [attachment(1)];
    let saved: SavedBotThread | null = null;
    f.api.saveGroup = vi.fn(async (workspaceId, threadId, revision, state) => {
      saved = {
        workspace_id: workspaceId,
        thread_id: threadId,
        revision: revision + 1,
        state: structuredClone(state),
        created_at: AT,
        updated_at: AT,
      };
      return saved;
    });
    await f.store.send(f.threadId, "request", undefined, attachments);
    expect((saved as SavedBotThread | null)?.state).not.toHaveProperty("attachments");
  });

  it("final admission stops a send cancelled during durable draft preparation", async () => {
    const f = setup();
    const save = f.durable.saveDraft.bind(f.durable);
    vi.spyOn(f.durable, "saveDraft").mockImplementation(async (...args) => {
      const result = await save(...args);
      f.store.stop();
      return result;
    });
    await f.store.send(f.threadId, "unsent request");
    expect(f.send).not.toHaveBeenCalled();
    expect(f.store.getSnapshot().threads[0]?.draft).toBe("unsent request");
    expect(
      Object.values(f.editors.getSnapshot().editors).every((row) => row.pendingCommand === null),
    ).toBe(true);
  });

  it("saves a unique prepared session before two windows can dispatch", async () => {
    const f = setup(() => "Ask the user.");
    const original = f.store.getSnapshot().threads[0] as TeamThread;
    original.revision = 1;
    const initial = savedThread(original);
    initial.revision = 1;
    const second = restore(f, [structuredClone(initial)]);
    await vi.waitFor(() => expect(second.getSnapshot().loading).toBe(false), {
      timeout: 50,
      interval: 1,
    });
    const windowsReady = deferred<void>();
    let waiting = 0;
    let saved = initial;
    f.api.saveGroup = vi.fn<NonNullable<BotsApi["saveGroup"]>>(
      async (workspaceId, threadId, expected, state) => {
        // The production save deliberately echoes a same-state expected+1 replay.
        if (state.members.every((row) => row.session_id === null) && state.phase === "running") {
          if (++waiting === 2) windowsReady.resolve();
          await windowsReady.promise;
        }
        if (expected !== saved.revision) {
          if (
            expected + 1 === saved.revision &&
            JSON.stringify(state) === JSON.stringify(saved.state)
          )
            return structuredClone(saved);
          throw new Error("conflict");
        }
        saved = {
          ...saved,
          workspace_id: workspaceId,
          thread_id: threadId,
          revision: expected + 1,
          state: structuredClone(state),
        };
        return structuredClone(saved);
      },
    );
    await Promise.all([
      f.store.send(f.threadId, "same request"),
      second.send(f.threadId, "same request"),
    ]);
    expect(f.launch.prepare).toHaveBeenCalledTimes(2);
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(
      saved.state.messages.some((row) => row.command_id === [...f.durable.commands.keys()][0]),
    ).toBe(true);
  });

  it("binds an accepted source before handling Stop during its POST response", async () => {
    const f = setup();
    let saved: SavedBotThread | null = null;
    f.api.saveGroup = vi.fn(async (workspaceId, threadId, revision, state) => {
      saved = {
        workspace_id: workspaceId,
        thread_id: threadId,
        revision: revision + 1,
        state: structuredClone(state),
        created_at: AT,
        updated_at: AT,
      };
      return saved;
    });
    const prior = f.send.getMockImplementation();
    f.send.mockImplementation(async (input) => {
      const receipt = await prior?.(input);
      f.store.stop();
      return receipt as Awaited<ReturnType<typeof f.send>>;
    });
    await f.store.send(f.threadId, "accepted request");
    const command = [...f.durable.commands.values()][0];
    expect(f.send).toHaveBeenCalledTimes(1);
    expect((saved as SavedBotThread | null)?.state.messages).toEqual([
      expect.objectContaining({
        command_id: command?.command_id,
        session_id: command?.session_id,
        member_key: null,
      }),
      expect.objectContaining({
        command_id: command?.command_id,
        session_id: command?.session_id,
        member_key: id(1),
      }),
    ]);
    expect(f.store.getSnapshot().threads[0]?.draft).toBe("");
    expect(f.viki.actions.closeSession).toHaveBeenCalledWith(command?.session_id, {
      generation: FAKE_GENERATION,
      turnId: command?.command_id,
    });
    expect(f.store.getSnapshot().threads[0]?.messages[1]?.turn).toBeDefined();
  });

  it("retires a matching failed native turn and releases its editor", async () => {
    const f = setup();
    const prior = f.send.getMockImplementation();
    f.send.mockImplementation(async (input) => {
      const receipt = await prior?.(input);
      f.app.update(input.session_id, (view) => ({
        ...view,
        error: { code: "crashed", message: "Native turn failed", recoverable: true },
        turns: view.turns.map((turn) => ({ ...turn, status: "error" })),
      }));
      return receipt as Awaited<ReturnType<typeof f.send>>;
    });
    await f.store.send(f.threadId, "request");
    expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(1);
    expect(f.editors.getSnapshot().editors).toEqual({});
    expect(f.store.getSnapshot().threads[0]?.messages[1]?.turn?.status).toBe("error");
  });

  it("retires its stopped turn even while its own question is open", async () => {
    const f = setup();
    const prior = f.send.getMockImplementation();
    f.send.mockImplementation(async (input) => {
      const receipt = await prior?.(input);
      f.app.update(input.session_id, (view) => ({
        ...view,
        turns: view.turns.map((turn) => ({ ...turn, status: "running" })),
        cards: [
          { kind: "question", data: { tool_use_id: id(77), questions: [] }, nativeRequest: null },
        ],
      }));
      f.store.stop();
      return receipt as Awaited<ReturnType<typeof f.send>>;
    });
    await f.store.send(f.threadId, "request");
    expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().threads[0]?.messages[1]?.turn?.status).toBe("cancelled");
  });

  it("retires a known accepted source when saving its reference fails", async () => {
    const f = setup();
    f.api.saveGroup = vi.fn(async (workspaceId, threadId, revision, state) => {
      if (state.messages.length) throw new Error("save_failed");
      return {
        workspace_id: workspaceId,
        thread_id: threadId,
        revision: revision + 1,
        state,
        created_at: AT,
        updated_at: AT,
      };
    });
    await f.store.send(f.threadId, "accepted request");
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().threads[0]?.messages[1]?.commandId).toBeDefined();
    expect(f.store.getSnapshot().threads[0]?.note).toBe("bots.thread.saveError");
  });

  it("correlates only a unique explicitly cut prompt with its visible source marker", async () => {
    const f = setup(() => "Review the result.", `\u002fkanban ${"seed".repeat(3900)}`);
    await f.store.send(f.threadId, "x".repeat(7000));
    const message = f.store.getSnapshot().threads[0]?.messages[1];
    const command = [...f.durable.commands.values()][0];
    expect(command?.text.length).toBeGreaterThan(16_384);
    const prompt = command?.text ?? "";
    const prefix = [...prompt].slice(0, 16_384).join("");
    const cut: Turn = {
      ...(message?.turn as Turn),
      id: "history-cut",
      history: true,
      userCut: true,
      user: { command: "/kanban", text: prefix.slice(8), at: AT },
    };
    f.app.update(message?.sessionId ?? "", (view) => ({ ...view, turns: [cut] }));
    const uncached = { ...message, turn: undefined } as NonNullable<typeof message>;
    expect(threadTurn(uncached, f.app.getSnapshot().sessions)).toBe(cut);
    f.app.update(message?.sessionId ?? "", (view) => ({
      ...view,
      turns: [{ ...cut, userCut: false }],
    }));
    expect(threadTurn(uncached, f.app.getSnapshot().sessions)).toBeUndefined();
    f.app.update(message?.sessionId ?? "", (view) => ({
      ...view,
      turns: [cut, { ...cut, id: "duplicate" }],
    }));
    expect(threadTurn(uncached, f.app.getSnapshot().sessions)).toBeUndefined();
    const hiddenMarker = `Shared Bot thread ${id(76)}; message ${id(77)}. You are Chief.\n${"seed".repeat(17000)}\n${prompt}`;
    f.app.update(message?.sessionId ?? "", (view) => ({
      ...view,
      turns: [{ ...cut, user: { command: "", text: hiddenMarker.slice(0, 16_384), at: AT } }],
    }));
    expect(
      threadTurn({ ...uncached, prompt: hiddenMarker }, f.app.getSnapshot().sessions),
    ).toBeUndefined();
    f.app.update(message?.sessionId ?? "", (view) => ({
      ...view,
      turns: [{ ...cut, user: { command: "", text: "", at: AT } }],
    }));
    expect(threadTurn(uncached, f.app.getSnapshot().sessions)).toBeUndefined();
  });

  it("bounds the entire final reply and pauses when a clipped result has no packet", async () => {
    const f = setup(() => "Result details. ".repeat(1200).slice(0, 16_384));
    await f.store.send(f.threadId, "request");
    expect([...f.durable.commands.values()][0]?.text).toContain(
      "Keep the entire final reply, including the VIKI_RESULT line, below 16000 characters",
    );
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      note: "bots.thread.waiting",
    });
  });

  it("retains old Bot identity and source keys when its seat gets a replacement", async () => {
    const f = setup(() => "Ask the user.");
    const chief = bot(1);
    if (chief.profile) chief.profile.identity.name = "Chief";
    const members = threadMembers([chief], id(90)).filter((row) => row.bot);
    const threadId = f.store.create(members) as string;
    await f.store.send(threadId, "original request", "chief");
    const historical = f.store
      .getSnapshot()
      .threads.find((row) => row.id === threadId) as TeamThread;
    const replacement = bot(2);
    if (replacement.profile) replacement.profile.identity.name = "Chief";
    const option = threadMembers([replacement], id(90)).find((row) => row.bot) as ThreadMember;
    const reconciled = threadOptions(historical, [option]);
    expect(reconciled).toMatchObject([
      { key: "chief", botId: id(1), bot: null, name: "Unavailable Bot" },
      { key: id(2), bot: { bot_id: id(2) } },
    ]);
    f.store.toggle(threadId, reconciled[0] as ThreadMember);
    f.store.toggle(threadId, reconciled[1] as ThreadMember);
    const current = f.store.getSnapshot().threads.find((row) => row.id === threadId);
    expect(current?.members).toMatchObject([
      { key: "chief", botId: id(1), enabled: false },
      { key: id(2), bot: { bot_id: id(2) }, enabled: true },
    ]);
    expect(current?.messages[1]?.memberKey).toBe("chief");
    const renamed = { ...option, name: "Current Chief" };
    expect(threadOptions(current, [renamed])[1]).toMatchObject({
      key: id(2),
      name: "Current Chief",
    });
  });

  it("restores a Bot by UUID while retaining its former seat key", async () => {
    const f = setup();
    const chief = bot(1);
    if (chief.profile) chief.profile.identity.name = "Chief";
    const threadId = f.store.create(
      threadMembers([chief], id(90)).filter((row) => row.bot),
    ) as string;
    const saved = savedThread(
      f.store.getSnapshot().threads.find((row) => row.id === threadId) as TeamThread,
    );
    const restored = restore(f, [saved]);
    await vi.waitFor(() => expect(restored.getSnapshot().loading).toBe(false), {
      timeout: 50,
      interval: 1,
    });
    expect(restored.getSnapshot().threads[0]?.members[0]).toMatchObject({
      key: "chief",
      bot: { bot_id: id(1) },
      name: "Bot 1",
      enabled: true,
    });
  });

  it("keeps an unbound placeholder separate from a newly bound seat option", () => {
    const f = setup();
    const placeholder = threadMembers([], id(90)).find(
      (row) => row.key === "chief",
    ) as ThreadMember;
    const threadId = f.store.create([placeholder]) as string;
    const replacement = bot(4);
    if (replacement.profile) replacement.profile.identity.name = "Chief";
    const option = threadMembers([replacement], id(90)).find((row) => row.bot) as ThreadMember;
    const thread = f.store.getSnapshot().threads.find((row) => row.id === threadId);
    expect(threadOptions(thread, [option])).toMatchObject([
      { key: "chief", bot: null, botId: null },
      { key: id(4), bot: { bot_id: id(4) } },
    ]);
  });

  it("routes review to an explicitly selected replacement Checker UUID", async () => {
    const f = setup((n) => packet(n ? { verdict: "PASS", next_gate: "done" } : {}), "", true);
    const old = f.members.find((row) => row.key === "checker") as ThreadMember;
    f.store.toggle(f.threadId, old);
    const replacement = bot(4);
    if (replacement.profile) replacement.profile.identity.name = "Checker";
    const get = f.api.get.bind(f.api);
    f.api.get = vi.fn<BotsApi["get"]>(async (botId, signal) =>
      botId === id(4)
        ? { bot: replacement, chat_status: { state: "prepared", blockers: [] } }
        : get(botId, signal),
    );
    const option = threadMembers([replacement], id(90)).find((row) => row.bot) as ThreadMember;
    f.store.toggle(f.threadId, option);
    await f.store.send(f.threadId, "request", id(1));
    expect(f.send).toHaveBeenCalledTimes(2);
    expect(vi.mocked(f.launch.prepare).mock.calls[1]?.[0]).toBe(replacement.profile_id);
    expect(vi.mocked(f.launch.prepare).mock.calls[1]?.[6]).toBe(replacement.bot_id);
    expect(f.store.getSnapshot().threads[0]?.phase).toBe("done");
  });

  it("binds every fresh Chief and Checker source to its stable Bot UUID", async () => {
    const f = setup((n) => packet(n % 2 ? { verdict: "PASS", next_gate: "done" } : {}));
    const chief = bot(1);
    const checker = bot(3);
    if (chief.profile) chief.profile.identity.name = "Chief";
    if (checker.profile) checker.profile.identity.name = "Checker";
    const rows = [chief, checker];
    f.api.get = vi.fn<BotsApi["get"]>(async (botId) => ({
      bot: rows.find((row) => row.bot_id === botId) as BotRow,
      chat_status: { state: "prepared", blockers: [] },
    }));
    const threadId = f.store.create(threadMembers(rows, id(90)).filter((row) => row.bot)) as string;
    await f.store.send(threadId, "first request", "chief");
    await f.store.send(threadId, "second request", "chief");
    const calls = vi.mocked(f.launch.prepare).mock.calls;
    expect(f.send).toHaveBeenCalledTimes(4);
    expect(calls.map((args) => args[6])).toEqual([
      chief.bot_id,
      checker.bot_id,
      chief.bot_id,
      checker.bot_id,
    ]);
    expect(new Set(calls.map((args) => args[2])).size).toBe(4);
    const previews = await Promise.all(
      vi.mocked(f.launch.prepare).mock.results.map((row) => row.value),
    );
    expect(
      previews.map((preview) => ("bot_memory" in preview ? preview.bot_memory?.bot_id : null)),
    ).toEqual(calls.map((args) => args[6]));
  });

  it("keeps a replacement Checker's Bot memory distinct when it shares the former profile", async () => {
    const f = setup((n) => packet(n === 1 ? {} : { verdict: "PASS", next_gate: "done" }), "", true);
    await f.store.send(f.threadId, "first review", "checker");
    const old = f.members.find((row) => row.key === "checker") as ThreadMember;
    f.store.toggle(f.threadId, old);
    const replacement = { ...bot(4), profile_id: old.profileId };
    if (replacement.profile) replacement.profile.identity.name = "Checker";
    const get = f.api.get.bind(f.api);
    f.api.get = vi.fn<BotsApi["get"]>(async (botId, signal) =>
      botId === replacement.bot_id
        ? { bot: replacement, chat_status: { state: "prepared", blockers: [] } }
        : get(botId, signal),
    );
    const option = threadMembers([replacement], id(90)).find((row) => row.bot) as ThreadMember;
    f.store.toggle(f.threadId, option);
    await f.store.send(f.threadId, "new work", id(1));
    const calls = vi.mocked(f.launch.prepare).mock.calls;
    expect(calls.map((args) => args[6])).toEqual([id(3), id(1), replacement.bot_id]);
    expect(calls[0]?.[0]).toBe(calls[2]?.[0]);
    expect(calls[0]?.[2]).not.toBe(calls[2]?.[2]);
    const messages = f.store.getSnapshot().threads[0]?.messages.filter((row) => row.memberKey);
    expect(messages?.map((row) => row.memberKey)).toEqual(["checker", id(1), replacement.bot_id]);
    expect(f.store.getSnapshot().threads[0]?.phase).toBe("done");
  });

  it("refuses another Bot's returned memory binding before opening a fresh source", async () => {
    const f = setup();
    const prepare = vi.mocked(f.launch.prepare).getMockImplementation();
    vi.mocked(f.launch.prepare).mockImplementation(async (...args) => ({
      ...((await prepare?.(...args)) as PreparedProfilePreview),
      bot_memory: { bot_id: id(79), ref: `bot-memory/${id(79)}`, routine: false },
    }));
    await f.store.send(f.threadId, "unsent request");
    expect(f.send).not.toHaveBeenCalled();
    expect(f.viki.actions.openExistingSession).not.toHaveBeenCalled();
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      draft: "unsent request",
    });
  });

  it("pauses recovery when source memory belongs to another Bot with the same profile", async () => {
    const f = setup(() => "Review the result.");
    await f.store.send(f.threadId, "original request");
    const saved = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
    const preview = (await vi.mocked(f.launch.prepare).mock.results[0]
      ?.value) as PreparedProfilePreview;
    f.launch.inspect = vi.fn(async () => ({
      ...preview,
      bot_memory: { bot_id: id(79), ref: `bot-memory/${id(79)}`, routine: false },
    }));
    f.viki.actions.observeHistorySession = vi.fn(() => () => {});
    const restored = restore(f, [saved]);
    await vi.waitFor(
      () => expect(restored.getSnapshot().threads[0]?.sourceStatus).toBe("unavailable"),
      { timeout: 50, interval: 1 },
    );
    expect(f.viki.actions.observeHistorySession).not.toHaveBeenCalled();
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(restored.getSnapshot().threads[0]?.note).toBe("bots.thread.sourceError");
  });

  it.each([false, true])(
    "bounds retained members and prunes only unused entries (%s)",
    async (referenced) => {
      const f = setup();
      const source = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
      source.state.members = Array.from({ length: 64 }, (_, n) => ({
        key: id(n + 100),
        bot_id: id(n + 100),
        profile_id: id(n + 300),
        enabled: false,
        session_id: referenced ? id(n + 500) : null,
      }));
      const restored = restore(f, [source]);
      await vi.waitFor(() => expect(restored.getSnapshot().loading).toBe(false), {
        timeout: 50,
        interval: 1,
      });
      restored.edit(f.threadId, "keep this draft");
      restored.toggle(f.threadId, f.members[0] as ThreadMember);
      const thread = restored.getSnapshot().threads[0];
      expect(thread?.members).toHaveLength(referenced ? 64 : 1);
      expect(thread?.draft).toBe("keep this draft");
      if (referenced) expect(thread?.note).toBe("bots.thread.memberLimit");
      else expect(thread?.members[0]?.bot?.bot_id).toBe(id(1));
    },
  );

  it.each(["objective", "progress", "artifacts", "decisions", "questions", "criteria", "evidence"])(
    "rejects whitespace-only %s without rewriting valid values",
    async (field) => {
      const change = field === "objective" || field === "progress" ? " \n\t " : [" \n\t "];
      const invalid = packet({ verdict: "PASS", next_gate: "done", [field]: change });
      expect(readHandoff(invalid)).toBeNull();
      const valid = packet({ objective: "  authored objective  ", progress: "  proof  " });
      expect(readHandoff(valid)).toMatchObject({
        objective: "  authored objective  ",
        progress: "  proof  ",
      });
      const f = setup(() => invalid, "", true);
      await f.store.send(f.threadId, "request", "checker");
      expect(f.store.getSnapshot().threads[0]?.phase).toBe("paused");
    },
  );

  it.each(["newer-turn", "newer-draft", "cut", "unknown"])(
    "preserves %s native sources",
    async (kind) => {
      const f = setup();
      const prior = f.send.getMockImplementation();
      f.send.mockImplementation(async (input) => {
        const receipt = await prior?.(input);
        f.app.update(input.session_id, (view) => ({
          ...view,
          turns:
            kind === "newer-turn"
              ? [...view.turns, { ...(view.turns[0] as Turn), id: id(78) }]
              : view.turns.map((turn) => ({
                  ...turn,
                  ...(kind === "cut" ? { userCut: true } : {}),
                })),
        }));
        if (kind === "newer-draft") f.editors.edit(input.session_id, "keep new draft");
        return {
          ...receipt,
          ...(kind === "unknown" ? { state: "unknown" as const } : {}),
        } as Awaited<ReturnType<typeof f.send>>;
      });
      await f.store.send(f.threadId, "request");
      expect(f.viki.actions.closeSession).not.toHaveBeenCalled();
      expect(f.send).toHaveBeenCalledTimes(1);
    },
  );

  it("frees dedicated editors after more than 64 sequential handoffs", async () => {
    const f = setup(() => "Please ask the user.");
    for (let n = 0; n < 66; n++) await f.store.send(f.threadId, `request ${n}`);
    expect(f.send).toHaveBeenCalledTimes(66);
    expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(66);
    expect(f.editors.getSnapshot().editors).toEqual({});
    expect(f.editors.capacityAvailable(id(85))).toBe(true);
    await f.editors.ensure(id(85));
  });

  it("ignores stale A-B-A load success and error without changing current admission", async () => {
    const f = setup();
    const first = deferred<{ threads: SavedBotThread[] }>();
    const second = deferred<{ threads: SavedBotThread[] }>();
    const third = deferred<{ threads: SavedBotThread[] }>();
    const requests = [first, second, third];
    const groups = vi.fn(() => requests.shift()?.promise as Promise<{ threads: SavedBotThread[] }>);
    const restored = restore(f, [], groups);
    restored.setContext(id(91), true);
    restored.setContext(id(90), true);
    expect(groups).toHaveBeenCalledTimes(3);
    const current = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
    current.state.title = "newest";
    third.resolve({ threads: [current] });
    await vi.waitFor(() => expect(restored.getSnapshot().threads[0]?.title).toBe("newest"), {
      timeout: 50,
      interval: 1,
    });
    first.resolve({ threads: [{ ...current, state: { ...current.state, title: "stale" } }] });
    second.reject(new Error("old workspace error"));
    await Promise.allSettled([first.promise, second.promise]);
    expect(restored.getSnapshot()).toMatchObject({
      loading: false,
      error: null,
      selectedId: current.thread_id,
    });
    expect(restored.getSnapshot().threads[0]?.title).toBe("newest");
    expect(restored.create(f.members)).not.toBeNull();
  });

  it.each(["reload", "dispose"])("fences a stale load after %s", async (action) => {
    const f = setup();
    const first = deferred<{ threads: SavedBotThread[] }>();
    const second = deferred<{ threads: SavedBotThread[] }>();
    const groups = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const restored = restore(f, [], groups);
    if (action === "reload") {
      restored.reload();
      expect(groups).toHaveBeenCalledTimes(2);
      second.resolve({ threads: [] });
      await vi.waitFor(() => expect(restored.getSnapshot().loading).toBe(false), {
        timeout: 50,
        interval: 1,
      });
    } else restored.dispose();
    first.reject(new Error("stale failure"));
    await Promise.allSettled([first.promise]);
    expect(restored.getSnapshot()).toMatchObject({ loading: false, error: null, threads: [] });
  });

  it("fences restored sends until history settles and releases the history lease", async () => {
    const f = setup(() => "Review the result.");
    await f.store.send(f.threadId, "original request");
    const thread = f.store.getSnapshot().threads[0] as TeamThread;
    const saved = savedThread(thread);
    const source = thread.messages[1];
    const preview = (await vi.mocked(f.launch.prepare).mock.results[0]
      ?.value) as PreparedProfilePreview;
    f.launch.inspect = vi.fn(async () => preview);
    const release = vi.fn(() => f.app.remove(source?.sessionId ?? ""));
    f.viki.actions.observeHistorySession = vi.fn((sessionId) => {
      f.app.update(sessionId, (view) => ({ ...view, historyLoading: true }));
      return release;
    });
    const restored = restore(f, [saved]);
    await vi.waitFor(() => expect(f.viki.actions.observeHistorySession).toHaveBeenCalledTimes(1), {
      timeout: 50,
      interval: 1,
    });
    expect(restored.getSnapshot().threads[0]?.sourceStatus).toBe("loading");
    restored.edit(f.threadId, "keep unsent");
    await restored.send(f.threadId, "do not post yet");
    expect(f.send).toHaveBeenCalledTimes(1);
    f.app.update(source?.sessionId ?? "", (view) => ({
      ...view,
      synced: true,
      historyLoading: false,
      historyCursor: null,
      turns: [{ ...(source?.turn as Turn), history: true }],
    }));
    await vi.waitFor(() => expect(restored.getSnapshot().threads[0]?.sourceStatus).toBe("ready"), {
      timeout: 50,
      interval: 1,
    });
    expect(release).toHaveBeenCalledTimes(1);
    expect(restored.getSnapshot().threads[0]?.messages[1]?.turn?.history).toBe(true);
    expect(restored.getSnapshot().threads[0]?.draft).toBe("keep unsent");
    expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(1);
  });

  it.each(["select", "remove", "reload", "hide", "dispose"])(
    "releases unsettled source history ownership on %s",
    async (action) => {
      const f = setup(() => "Review the result.");
      await f.store.send(f.threadId, "original request");
      const saved = savedThread(f.store.getSnapshot().threads[0] as TeamThread);
      const empty = { ...saved, thread_id: id(75), state: { ...saved.state, messages: [] } };
      const preview = (await vi.mocked(f.launch.prepare).mock.results[0]
        ?.value) as PreparedProfilePreview;
      f.launch.inspect = vi.fn(async () => preview);
      const release = vi.fn();
      f.viki.actions.observeHistorySession = vi.fn(() => release);
      const restored = restore(f, [empty, saved]);
      await vi.waitFor(() => expect(f.viki.actions.observeHistorySession).toHaveBeenCalled(), {
        timeout: 50,
        interval: 1,
      });
      if (action === "select") restored.select(id(75));
      else if (action === "remove") await restored.remove(saved.thread_id);
      else if (action === "reload") {
        f.api.groups = vi.fn(async () => ({ threads: [] }));
        restored.reload();
      } else if (action === "hide") restored.setVisible(false);
      else restored.dispose();
      await vi.waitFor(() => expect(release).toHaveBeenCalledTimes(1), {
        timeout: 50,
        interval: 1,
      });
      expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(1);
    },
  );

  it("pauses unavailable source recovery until explicit reload", async () => {
    const f = setup(() => "Review the result.");
    await f.store.send(f.threadId, "original request");
    const thread = f.store.getSnapshot().threads[0] as TeamThread;
    const saved = savedThread(thread);
    const preview = (await vi.mocked(f.launch.prepare).mock.results[0]
      ?.value) as PreparedProfilePreview;
    f.launch.inspect = vi.fn(async () => preview);
    const get = f.durable.getSubmission.bind(f.durable);
    const source = vi.spyOn(f.durable, "getSubmission").mockRejectedValue(new Error("unavailable"));
    const restored = restore(f, [saved]);
    await vi.waitFor(
      () => expect(restored.getSnapshot().threads[0]?.sourceStatus).toBe("unavailable"),
      { timeout: 50, interval: 1 },
    );
    await restored.send(f.threadId, "must wait");
    expect(f.send).toHaveBeenCalledTimes(1);
    source.mockImplementation(get);
    f.viki.actions.observeHistorySession = vi.fn((sessionId) => {
      f.app.update(sessionId, (view) => ({
        ...view,
        synced: true,
        turns: [{ ...(thread.messages[1]?.turn as Turn), history: true }],
      }));
      return () => {};
    });
    restored.reload();
    await vi.waitFor(() => expect(restored.getSnapshot().threads[0]?.sourceStatus).toBe("ready"), {
      timeout: 50,
      interval: 1,
    });
    expect(f.send).toHaveBeenCalledTimes(1);
  });

  it("hands off only the structured result and requires an independent Checker PASS", async () => {
    const { store, threadId, send, durable, launch } = setup(
      (n) =>
        n === 0
          ? `Builder's private transcript is not a handoff.\n${packet()}`
          : `Checked both source refs.\n${packet({ next_gate: "done", verdict: "PASS", progress: "Sources independently checked" })}`,
      "",
      true,
    );
    await store.send(threadId, "Compare the options", id(1));
    const submissions = [...durable.commands.values()];
    expect(send).toHaveBeenCalledTimes(2);
    expect(submissions[1]?.text).not.toContain("Builder's private transcript");
    expect(promptContext(submissions[1]?.text ?? "")?.handoff).toEqual(readHandoff(packet()));
    expect(submissions[1]?.session_id).not.toBe(submissions[0]?.session_id);
    expect(store.getSnapshot().threads[0]?.phase).toBe("done");
    await store.send(threadId, "Check another comparison", "checker");
    expect(launch.prepare).toHaveBeenCalledTimes(3);
    expect([...durable.commands.values()].at(-1)?.session_id).not.toBe(submissions[1]?.session_id);
  });

  it("does not accept a worker's PASS or a Checker's verdict without evidence", async () => {
    const { store, threadId, send } = setup(
      (n) => packet({ next_gate: "done", verdict: "PASS", evidence: n ? [] : ["worker claim"] }),
      "",
      true,
    );
    await store.send(threadId, "Compare", id(1));
    expect(send).toHaveBeenCalledTimes(2);
    expect(store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      note: "bots.thread.waiting",
    });
  });

  it("returns failed checks to their owner and uses a fresh review session for the next check", async () => {
    const { store, threadId, durable } = setup(
      (n) =>
        n === 1
          ? packet({
              to: id(1),
              verdict: "FAIL",
              next_gate: "work",
              progress: "Source for option B is missing",
            })
          : n === 3
            ? packet({ verdict: "PASS", next_gate: "done", progress: "Both refs inspected" })
            : packet({ progress: n ? "Added option B source" : "First comparison" }),
      "",
      true,
    );
    await store.send(threadId, "Compare", id(1));
    const sends = [...durable.commands.values()];
    expect(sends).toHaveLength(4);
    expect(sends[0]?.session_id).not.toBe(sends[2]?.session_id);
    expect(sends[1]?.session_id).not.toBe(sends[3]?.session_id);
    expect(promptContext(sends[2]?.text ?? "")?.handoff?.verdict).toBe("FAIL");
    expect(store.getSnapshot().threads[0]?.phase).toBe("done");
  });

  it("pauses after two handoffs without new progress", async () => {
    const { store, threadId, send } = setup(() => packet({ to: id(1), next_gate: "work" }));
    await store.send(threadId, "Compare", id(1));
    expect(send).toHaveBeenCalledTimes(3);
    expect(store.getSnapshot().threads[0]?.note).toBe("bots.thread.noProgress");
  });

  it("runs eight participants serially while releasing every completed live slot", async () => {
    const seats = ["Chief", "Ops", "Research", "Writer", "Desk", "Builder", "Reviewer", "Checker"];
    const rows = seats.map((name, index) => {
      const row = bot(index + 1);
      if (row.profile) row.profile.identity.name = name;
      return row;
    });
    const f = setup((n) =>
      packet(
        n === seats.length - 1
          ? { verdict: "PASS", next_gate: "done", progress: "Independent review passed" }
          : {
              to: threadMembers(rows, id(90)).find((row) => row.name === seats[n + 1])?.key ?? null,
              next_gate: n === seats.length - 2 ? "review" : "work",
              progress: `Stage ${n + 1} completed`,
            },
      ),
    );
    f.api.get = vi.fn(async (botId) => ({
      bot: rows.find((row) => row.bot_id === botId) as BotRow,
      chat_status: { state: "prepared" as const, blockers: [] },
    }));
    const live = new Set<string>();
    vi.mocked(f.viki.actions.prepareTarget).mockImplementation(async (sessionId) => {
      if (live.size) return null;
      live.add(sessionId);
      return { session_id: sessionId, generation: FAKE_GENERATION };
    });
    vi.mocked(f.viki.actions.closeSession).mockImplementation(async (sessionId) => {
      live.delete(sessionId);
      return "applied";
    });
    const threadId = f.store.create(threadMembers(rows, id(90)).filter((row) => row.bot)) as string;
    await f.store.send(threadId, "Compare", "chief");
    expect(f.send).toHaveBeenCalledTimes(seats.length);
    expect(f.viki.actions.closeSession).toHaveBeenCalledTimes(seats.length);
    expect(live.size).toBe(0);
    expect(f.store.getSnapshot().threads.find((row) => row.id === threadId)?.phase).toBe("done");
  });

  it("keys the Engineer seat by its profile UUID and the other seats by their words", () => {
    const words = ["chief", "ops", "research", "writer", ENGINEER_ID, "desk", "checker"];
    const keys = (bots: BotRow[]) => threadMembers(bots, id(90)).map((row) => row.key);
    expect(keys([])).toEqual(words);
    expect(keys([named(1, "Engineer")])).toEqual(words);
    expect(keys([named(1, "Bot 1", ENGINEER_ID)])).toEqual(words);
    expect(keys([named(1, "Engineer"), bot(2)])).toEqual([...words, id(2)]);
  });

  it.each([
    ["its UUID", () => packet({ to: ENGINEER_ID, next_gate: "work", progress: "Handed on" })],
    [
      "the word engineer",
      () => packet({ to: "engineer", next_gate: "work", progress: "Handed on" }),
    ],
    ["a VIKI_HANDOFF word", () => "Handed on.\nVIKI_HANDOFF engineer"],
  ])("hands off to the Engineer seat by %s", async (_name, handoff) => {
    const rows = [named(1, "Chief"), named(2, "Engineer"), named(3, "Checker")];
    const f = setup((n) =>
      n === 0
        ? handoff()
        : n === 1
          ? packet({ next_gate: "review", progress: "Built" })
          : packet({ verdict: "PASS", next_gate: "done", progress: "Checked" }),
    );
    f.api.get = vi.fn(async (botId) => ({
      bot: rows.find((row) => row.bot_id === botId) as BotRow,
      chat_status: { state: "prepared" as const, blockers: [] },
    }));
    const threadId = f.store.create(threadMembers(rows, id(90)).filter((row) => row.bot)) as string;
    await f.store.send(threadId, "Write the script", "chief");
    expect(vi.mocked(f.launch.prepare).mock.calls.map((args) => args[6])).toEqual([
      id(1),
      id(2),
      id(3),
    ]);
    expect(f.store.getSnapshot().threads.find((row) => row.id === threadId)?.phase).toBe("done");
  });

  it("pauses when two enabled members hold the word a handoff names", async () => {
    const rows = [named(1, "Chief"), named(2, "Engineer")];
    const f = setup(() => packet({ to: "engineer", next_gate: "work" }));
    f.api.get = vi.fn(async (botId) => ({
      bot: rows.find((row) => row.bot_id === botId) as BotRow,
      chat_status: { state: "prepared" as const, blockers: [] },
    }));
    const threadId = f.store.create(threadMembers(rows, id(90)).filter((row) => row.bot)) as string;
    const second = threadMembers([named(4, "Engineer")], id(90)).find((row) => row.bot);
    f.store.toggle(threadId, second as ThreadMember);
    const members = f.store.getSnapshot().threads.find((row) => row.id === threadId)?.members;
    expect(members?.filter((row) => row.enabled && row.seat?.seat === "engineer")).toHaveLength(2);
    await f.store.send(threadId, "Write the script", "chief");
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().threads.find((row) => row.id === threadId)).toMatchObject({
      phase: "paused",
      note: "bots.thread.failed",
    });
  });

  it("hands a seat word to the enabled Engineer only", async () => {
    const rows = [named(1, "Chief"), named(2, "Engineer"), named(4, "Engineer")];
    const f = setup((n) =>
      n === 0 ? packet({ to: "engineer", next_gate: "work" }) : packet({ next_gate: "work" }),
    );
    f.api.get = vi.fn(async (botId) => ({
      bot: rows.find((row) => row.bot_id === botId) as BotRow,
      chat_status: { state: "prepared" as const, blockers: [] },
    }));
    const options = threadMembers(rows.slice(0, 2), id(90)).filter((row) => row.bot);
    const threadId = f.store.create(options) as string;
    const second = threadMembers([rows[2] as BotRow], id(90)).find((row) => row.bot);
    f.store.toggle(threadId, second as ThreadMember);
    f.store.toggle(threadId, options[1] as ThreadMember);
    const members = f.store.getSnapshot().threads.find((row) => row.id === threadId)?.members;
    expect(members?.filter((row) => row.enabled && row.seat?.seat === "engineer")).toHaveLength(1);
    await f.store.send(threadId, "Write the script", "chief");
    expect(vi.mocked(f.launch.prepare).mock.calls.map((args) => args[6])).toEqual([id(1), id(4)]);
    expect(f.store.getSnapshot().threads.find((row) => row.id === threadId)?.note).not.toBe(
      "bots.thread.failed",
    );
  });

  it("keeps a Checker from reviewing itself under a resolved key", async () => {
    const rows = [named(1, "Chief"), named(3, "Checker")];
    const f = setup((n) =>
      n === 0
        ? packet({ next_gate: "review" })
        : packet({ verdict: "FAIL", to: "checker", next_gate: "work" }),
    );
    f.api.get = vi.fn(async (botId) => ({
      bot: rows.find((row) => row.bot_id === botId) as BotRow,
      chat_status: { state: "prepared" as const, blockers: [] },
    }));
    const members = threadMembers(rows, id(90))
      .filter((row) => row.bot)
      .map((row) => (row.seat?.seat === "checker" ? { ...row, key: id(3) } : row));
    expect(members.some((row) => row.key === "checker")).toBe(false);
    const threadId = f.store.create(members) as string;
    await f.store.send(threadId, "Write the script", "chief");
    expect(f.send).toHaveBeenCalledTimes(2);
    expect(vi.mocked(f.launch.prepare).mock.calls.map((args) => args[6])).toEqual([id(1), id(3)]);
    expect(f.store.getSnapshot().threads.find((row) => row.id === threadId)).toMatchObject({
      phase: "paused",
      note: "bots.thread.waiting",
    });
  });

  it("keeps the Engineer key through a save and a restore", async () => {
    const f = setup();
    const engineer = named(1, "Engineer");
    const members = threadMembers([engineer], id(90)).filter((row) => row.bot);
    const threadId = f.store.create(members) as string;
    const saved = savedThread(
      f.store.getSnapshot().threads.find((row) => row.id === threadId) as TeamThread,
    );
    expect(saved.state.members[0]?.key).toBe(ENGINEER_ID);
    expect(savedBotThread(saved)).toBe(true);
    const restored = restore({ ...f, members }, [saved]);
    await vi.waitFor(() => expect(restored.getSnapshot().loading).toBe(false), WAIT);
    expect(restored.getSnapshot().threads[0]?.members[0]).toMatchObject({
      key: ENGINEER_ID,
      bot: { bot_id: id(1) },
      enabled: true,
    });
  });

  it("allows all seven filled seats when creating or extending a thread", () => {
    const names = ["Chief", "Ops", "Research", "Writer", "Engineer", "Desk", "Checker"];
    const rows = names.map((name, index) => named(index + 1, name));
    const f = setup();
    const options = threadMembers(rows, id(90));
    expect(options.filter((row) => row.bot)).toHaveLength(7);
    expect(f.store.create(options)).toBeTruthy();
    const threadId = f.store.create(options.slice(0, 6)) as string;
    expect(threadId).toBeTruthy();
    f.store.toggle(threadId, options[6] as ThreadMember);
    const members = f.store.getSnapshot().threads.find((row) => row.id === threadId)?.members;
    expect(members).toHaveLength(7);
    expect(members?.find((row) => row.key === options[6]?.key)?.enabled).toBe(true);
  });

  it("stops when releasing a completed chat cannot be confirmed", async () => {
    const f = setup(() => packet({ to: id(2), next_gate: "work" }));
    vi.mocked(f.viki.actions.closeSession).mockResolvedValueOnce("unknown");
    await f.store.send(f.threadId, "Compare", id(1));
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().threads[0]?.phase).toBe("paused");
  });

  it("pauses on a persistence conflict before dispatching another handoff", async () => {
    const { store, threadId, api, send } = setup(() => packet({ to: id(2), next_gate: "work" }));
    api.saveGroup = vi.fn().mockRejectedValue(new Error("conflict"));
    await store.send(threadId, "Compare", id(1));
    expect(send).not.toHaveBeenCalled();
    expect(store.getSnapshot().threads[0]?.note).toBe("bots.thread.saveError");
  });

  it("restores only source references, opens paused and never resends a saved message", async () => {
    const f = setup(() => "A useful result. Ask the user.");
    let saved: SavedBotThread | null = null;
    f.api.saveGroup = vi.fn(async (workspaceId, threadId, revision, state) => {
      saved = {
        workspace_id: workspaceId,
        thread_id: threadId,
        revision: revision + 1,
        created_at: AT,
        updated_at: AT,
        state: structuredClone(state),
      };
      return saved;
    });
    await f.store.send(f.threadId, "Keep the original request", id(1));
    expect(JSON.stringify((saved as SavedBotThread | null)?.state.messages)).not.toContain(
      "Keep the original request",
    );
    expect(JSON.stringify(saved)).not.toContain("A useful result");
    // The title is display metadata; the sent request is recovered from its source command.
    expect(saved).toBeTruthy();
    f.api.groups = vi.fn(async () => ({ threads: saved ? [saved] : [] }));
    f.api.list = vi.fn(async () => ({
      bots: [bot(1), bot(2)],
      scheduler: { state: "running" as const, attended: true, paused: false },
    }));
    const previews = await Promise.all(
      vi.mocked(f.launch.prepare).mock.results.map((row) => row.value),
    );
    f.launch.inspect = vi.fn(
      async (sid) => previews.find((row) => row.session_id === sid) as PreparedProfilePreview,
    );
    const completedTurn = f.store.getSnapshot().threads[0]?.messages[1]?.turn;
    f.app.update([...f.durable.commands.values()][0]?.session_id ?? "", (view) => ({
      ...view,
      turns: completedTurn ? [{ ...completedTurn, history: true }] : [],
    }));
    const restored = createBotThreadStore({
      viki: f.viki,
      editors: f.editors,
      durable: f.durable,
      api: f.api,
      profiles: {} as ProfileApi,
      launch: f.launch,
      onPrepared: vi.fn(),
      canRun: () => true,
    });
    cleanups.push(() => restored.dispose());
    restored.setVisible(true);
    restored.setContext(id(90), true);
    await vi.waitFor(
      () =>
        expect(restored.getSnapshot().threads[0]?.messages[0]?.text).toBe(
          "Keep the original request",
        ),
      { timeout: 50, interval: 1 },
    );
    const thread = restored.getSnapshot().threads[0];
    expect(thread?.phase).toBe("paused");
    expect(
      threadTurn(
        thread?.messages[1] as NonNullable<typeof thread>["messages"][number],
        f.app.getSnapshot().sessions,
      )?.history,
    ).toBe(true);
    expect(f.send).toHaveBeenCalledTimes(1);
    await restored.remove(f.threadId);
    expect(restored.getSnapshot().threads).toHaveLength(0);
    expect(f.durable.commands.size).toBe(1);
  });
  it("routes legacy handoffs but requires Checker before completion", async () => {
    const { store, threadId, send, durable } = setup((n) =>
      n === 0 ? `Research brief.\nVIKI_HANDOFF ${id(2)}` : "Checked the brief.\nVIKI_DONE",
    );
    await store.send(threadId, "Compare the options");
    expect(send).toHaveBeenCalledTimes(2);
    const submissions = [...durable.commands.values()];
    expect(submissions[1]?.text).not.toContain("Research brief.");
    expect(submissions[1]?.session_id).not.toBe(submissions[0]?.session_id);
    expect(submissions.every((row) => row.session_id !== bot(1).chat_session_id)).toBe(true);
    expect(store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      note: "bots.thread.checkerRequired",
      messages: [{ memberKey: null }, { memberKey: id(1) }, { memberKey: id(2) }],
    });
    expect([...durable.drafts.values()].every((draft) => draft.text === "")).toBe(true);
  });

  it("starts each handoff with its own profile draft and releases the completed live slot", async () => {
    const { store, threadId, send, durable, launch } = setup(
      (n) => (n === 0 ? `Continue.\nVIKI_HANDOFF ${id(1)}` : "Complete.\nVIKI_DONE"),
      "Use the profile's checklist.",
    );
    await store.send(threadId, "Check this task");
    expect(send).toHaveBeenCalledTimes(2);
    const submissions = [...durable.commands.values()];
    expect(submissions[0]?.text.startsWith("Use the profile's checklist.\n\n")).toBe(true);
    expect(submissions[1]?.text.startsWith("Use the profile's checklist.")).toBe(true);
    expect(submissions[1]?.session_id).not.toBe(submissions[0]?.session_id);
    expect(launch.prepare).toHaveBeenCalledTimes(2);
  });

  it("pauses an unselected handoff without sending to another Bot", async () => {
    const { store, threadId, members, send } = setup(() => `Pass it on.\nVIKI_HANDOFF ${id(2)}`);
    store.toggle(threadId, members[1] as (typeof members)[number]);
    await store.send(threadId, "Review");
    expect(send).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().threads[0]?.phase).toBe("paused");
  });

  it("bounds a handoff loop without retrying a turn", async () => {
    const { store, threadId, send } = setup(() => `More work.\nVIKI_HANDOFF ${id(1)}`);
    await store.send(threadId, "Review");
    expect(send).toHaveBeenCalledTimes(24);
    expect(new Set(send.mock.calls.map(([input]) => input.command_id)).size).toBe(24);
    expect(store.getSnapshot().threads[0]?.note).toBe("bots.thread.limit");
  });

  it("does not resend an uncertain send or a failed Bot turn", async () => {
    const { store, threadId, send } = setup();
    send.mockResolvedValueOnce({
      command_id: id(80),
      session_id: id(81),
      generation: FAKE_GENERATION,
      draft_revision: 1,
      state: "unknown",
      reason: "",
      created_at: AT,
      updated_at: AT,
      bytes: 1,
    });
    await store.send(threadId, "Review");
    expect(send).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().threads[0]?.note).toBe("bots.thread.failed");
  });

  it.each(["stop", "opt-out", "workspace", "disconnect"])(
    "stops handoffs on %s while a Bot is working",
    async (action) => {
      const { store, threadId, send, app, members, interrupt } = setup();
      const savedSubmit = send.getMockImplementation();
      send.mockImplementation(async (input) => {
        const receipt = await savedSubmit?.(input);
        app.update(input.session_id, (view) => ({
          ...view,
          turns: view.turns.map((turn) => ({ ...turn, status: "running" })),
        }));
        return receipt as Awaited<ReturnType<typeof send>>;
      });
      const sending = store.send(threadId, "Review");
      for (
        let n = 0;
        n < 50 &&
        !app.getSnapshot().sessions[send.mock.calls[0]?.[0].session_id ?? ""]?.turns.length;
        n++
      )
        await Promise.resolve();
      if (action === "stop") store.stop();
      else if (action === "opt-out") store.toggle(threadId, members[0] as (typeof members)[number]);
      else if (action === "workspace") store.setContext(id(91), true);
      else app.setStatus("reconnecting");
      await sending;
      expect(send).toHaveBeenCalledTimes(1);
      expect(store.getSnapshot().threads[0]?.phase).toBe("paused");
      if (action !== "disconnect") expect(interrupt).toHaveBeenCalled();
    },
  );

  it("takes directives only from the final complete text line", () => {
    expect(threadDirective("VIKI_HANDOFF chief\nJust quoted that.")).toEqual({
      done: false,
      target: null,
    });
    expect(threadDirective("Answer\nVIKI_DONE\n")).toEqual({ done: true, target: null });
  });

  it("keeps the original request without forwarding the conversation transcript", async () => {
    const { store, threadId, durable } = setup(() => `Useful work.\nVIKI_HANDOFF ${id(1)}`);
    await store.send(threadId, "Finish the original requested outcome");
    expect([...durable.commands.values()].at(-1)?.text).toContain(
      '"request":"Finish the original requested outcome"',
    );
  });

  it("keeps drafts and prior messages when selection changes and limits new threads", async () => {
    const { store, threadId, members } = setup();
    store.edit(threadId, "Keep this draft");
    const other = store.create(members) as string;
    store.select(threadId);
    expect(store.getSnapshot().selectedId).toBe(threadId);
    expect(store.getSnapshot().threads[0]?.draft).toBe("Keep this draft");
    store.setContext(id(91), true);
    store.select(threadId);
    expect(store.create([])).toBeNull();
    store.setContext(id(90), true);
    store.select(other);
    for (let n = 2; n < 20; n++) expect(store.create(members)).not.toBeNull();
    expect(store.create(members)).toBeNull();
    expect(store.getSnapshot().threads).toHaveLength(20);
  });

  it("pauses on a native failure without accepting its handoff", async () => {
    const { store, threadId, app, send } = setup(() => `Unfinished.\nVIKI_HANDOFF ${id(2)}`);
    const prior = send.getMockImplementation();
    send.mockImplementation(async (input) => {
      const receipt = await prior?.(input);
      app.update(input.session_id, (view) => ({
        ...view,
        turns: view.turns.map((turn) => ({ ...turn, status: "error" })),
      }));
      return receipt as Awaited<ReturnType<typeof send>>;
    });
    await store.send(threadId, "Review");
    expect(send).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().threads[0]?.note).toBe("bots.thread.failed");
  });

  it("omits the handoff instruction from the displayed reply without changing native text", () => {
    const turn: Turn = {
      id: "turn",
      user: null,
      blocks: [{ kind: "text", id: "reply", done: true, text: "Result.\nVIKI_DONE" }],
      status: "success",
      usage: null,
      history: false,
      durationMs: 0,
    };
    expect(displayThreadTurn(turn).blocks[0]).toMatchObject({ text: "Result." });
    expect(turn.blocks[0]).toMatchObject({ text: "Result.\nVIKI_DONE" });
  });

  it("checks the current workspace before dispatch even before the UI updates its context", async () => {
    const { store, threadId, canRun, send } = setup();
    const sending = store.send(threadId, "Review");
    canRun.mockReturnValue(false);
    await sending;
    expect(send).not.toHaveBeenCalled();
    expect(store.getSnapshot().threads[0]?.phase).toBe("paused");
  });

  it("names each participant's purpose on one line so a handoff can pick the right Bot", async () => {
    const f = setup();
    const first = f.members[0]?.identity;
    if (first) first.purpose = "Finds sources\n  and checks   dates";
    const second = f.members[1]?.identity;
    if (second) second.purpose = "x".repeat(300);
    await f.store.send(f.threadId, "Plan the week");
    const prompt = [...f.durable.commands.values()][0]?.text ?? "";
    expect(prompt).toContain(`${f.members[0]?.key}: Bot 1 (Finds sources and checks dates)`);
    expect(prompt).toContain(`${f.members[1]?.key}: Bot 2 (${"x".repeat(199)}…)`);
  });

  it("correlates a starter prompt containing a native slash command", () => {
    const app = new AppStore();
    const turn: Turn = {
      id: "turn",
      user: { command: "/kanban", text: "status\nShared request", at: AT },
      blocks: [],
      status: "success",
      usage: null,
      history: false,
      durationMs: 0,
    };
    app.update(id(1), (view) => ({ ...view, turns: [turn] }));
    expect(
      threadTurn(
        {
          id: "message",
          at: AT,
          memberKey: "chief",
          text: "",
          sessionId: id(1),
          prompt: "/kanban status\nShared request",
        },
        app.getSnapshot().sessions,
      ),
    ).toBe(turn);
  });

  it("uses the current profile on a fresh handoff while keeping the prior source prompt", async () => {
    const { store, threadId, api, launch, durable } = setup((n) =>
      n === 0 ? `Check again.\nVIKI_HANDOFF ${id(1)}` : "Done.\nVIKI_DONE",
    );
    let reads = 0;
    vi.mocked(api.get).mockImplementation(async () => {
      const current = bot(1);
      if (reads++ > 0 && current.profile)
        current.profile = {
          ...current.profile,
          revision: 2,
          identity: { ...current.profile.identity, name: "Renamed Bot" },
        };
      return { bot: current, chat_status: { state: "prepared", blockers: [] } };
    });
    await store.send(threadId, "Check");
    expect(launch.prepare).toHaveBeenCalledTimes(2);
    expect([...durable.commands.values()][0]?.text).toContain("You are Bot 1.");
    expect([...durable.commands.values()].at(-1)?.text).toContain("You are Renamed Bot.");
    expect(store.getSnapshot().threads[0]?.members[0]?.name).toBe("Renamed Bot");
  });

  it("pauses when someone redirects the Bot in its own chat", async () => {
    const f = setup();
    const held = holdFirst(f);
    const sending = f.store.send(f.threadId, "Review");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    const handoff = `Pass the result.\nVIKI_HANDOFF ${id(2)}`;
    event(f.app, held.sessionId, { type: "text.done", data: { block_id: "t1", text: handoff } });
    // The message folds into T1, and the reply to it ends the chain without a handoff.
    event(f.app, held.sessionId, {
      type: "user.message",
      data: { text: "Ask me before passing it on.", command: "", at: AT },
    });
    answer(f.app, held.sessionId, "Understood. What should change first?");
    await sending;
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      note: "bots.thread.waiting",
    });
  });

  it("folds a steer into the working Bot's turn and hands off from its continuation", async () => {
    const f = setup(
      (n) => (n === 2 ? packet({ verdict: "PASS", next_gate: "done" }) : ""),
      "",
      true,
    );
    const rows = saves(f);
    const held = holdFirst(f);
    const submit = vi.spyOn(f.editors, "submit");
    const sending = f.store.send(f.threadId, "original request", id(1));
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    f.store.edit(f.threadId, "also compare option B");
    expect(await f.store.steer(f.threadId, "also compare option B")).toBe(true);
    const command = [...f.durable.commands.values()][1];
    const thread = f.store.getSnapshot().threads[0];
    const continuation = thread?.messages[3];
    expect(command?.text).toBe(
      steerPrompt(f.threadId, continuation?.id ?? "", "also compare option B"),
    );
    expect(submit.mock.calls.at(-1)?.slice(0, 5)).toEqual([
      held.sessionId,
      FAKE_GENERATION,
      command?.text,
      [],
      [],
    ]);
    expect(thread?.draft).toBe("");
    expect(thread?.messages.slice(2)).toMatchObject([
      { memberKey: null, text: "also compare option B", steer: true, sessionId: held.sessionId },
      { memberKey: id(1), text: "", continuation: true, prompt: command?.text },
    ]);
    expect(thread?.messages.slice(2).map((row) => row.commandId)).toEqual([
      command?.command_id,
      command?.command_id,
    ]);
    expect(
      rows.at(-1)?.state.messages.filter((row) => row.command_id === command?.command_id),
    ).toMatchObject([{ member_key: null }, { member_key: id(1) }]);
    const owner = thread?.messages[2] as ThreadMessage;
    expect(isSteerWaiting(owner, f.app.getSnapshot().sessions)).toBe(true);
    readSteer(f, held);
    expect(isSteerWaiting(owner, f.app.getSnapshot().sessions)).toBe(false);
    expect(continuation && threadTurn(continuation, f.app.getSnapshot().sessions)?.id).toBe(
      `${held.t1}:steer`,
    );
    expect(threadTurn(owner, f.app.getSnapshot().sessions)).toBeUndefined();
    expect(
      f.app.getSnapshot().sessions[held.sessionId]?.turns.map((turn) => [turn.id, turn.steered]),
    ).toEqual([
      [held.t1, true],
      [`${held.t1}:steer`, undefined],
    ]);
    answer(f.app, held.sessionId, packet({ to: "checker", progress: "Option B compared" }));
    await sending;
    const checker = promptContext([...f.durable.commands.values()][2]?.text ?? "");
    expect(checker?.request).toBe("original request");
    expect(checker?.handoff?.progress).toBe("Option B compared");
    expect(f.store.getSnapshot().threads[0]?.phase).toBe("done");
  });

  it.each(["fold", "late"] as const)(
    "retires a %s chain on the server's turn id with the steer's command",
    async (kind) => {
      const f = setup();
      const held = holdFirst(f);
      const release = vi.spyOn(f.editors, "releaseCompleted");
      const sending = f.store.send(f.threadId, "request");
      await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
      expect(await f.store.steer(f.threadId, "and the appendix")).toBe(true);
      if (kind === "late") {
        answer(f.app, held.sessionId, `First pass.\nVIKI_HANDOFF ${id(2)}`);
        for (let n = 0; n < 20; n++) await Promise.resolve();
        // The finished turn waits for its steer; nothing retires it yet.
        expect(f.viki.actions.closeSession).not.toHaveBeenCalled();
      }
      readSteer(f, held);
      if (kind === "late")
        event(f.app, held.sessionId, { type: "turn.started", data: { turn_id: id(79) } });
      answer(f.app, held.sessionId, "Appendix covered. Ask the user.");
      const turns = f.app.getSnapshot().sessions[held.sessionId]?.turns ?? [];
      await sending;
      const command = [...f.durable.commands.values()][1];
      expect(f.send).toHaveBeenCalledTimes(2);
      expect(f.viki.actions.closeSession).toHaveBeenCalledWith(held.sessionId, {
        generation: FAKE_GENERATION,
        turnId: kind === "fold" ? held.t1 : id(79),
      });
      expect(release).toHaveBeenCalledWith(held.sessionId, command?.command_id);
      expect(f.editors.getSnapshot().editors[held.sessionId]).toBeUndefined();
      const thread = f.store.getSnapshot().threads[0];
      expect([thread?.messages[1]?.turn?.id, thread?.messages[3]?.turn?.id]).toEqual(
        turns.map((turn) => turn.id),
      );
      expect(thread).toMatchObject({ phase: "paused", note: "bots.thread.waiting" });
    },
  );

  it("holds a finished turn for a steer still being submitted and reads that steer's turn", async () => {
    const f = setup();
    const held = holdFirst(f);
    const gate = deferred<void>();
    let posting = false;
    const holding = f.send.getMockImplementation();
    f.send.mockImplementation(async (input) => {
      if (held.t1) {
        posting = true;
        await gate.promise;
      }
      return holding?.(input) as ReturnType<typeof f.send>;
    });
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    const steering = f.store.steer(f.threadId, "one more source");
    await vi.waitFor(() => expect(posting).toBe(true), WAIT);
    answer(f.app, held.sessionId, `First pass.\nVIKI_HANDOFF ${id(2)}`);
    for (let n = 0; n < 20; n++) await Promise.resolve();
    gate.resolve();
    expect(await steering).toBe(true);
    readSteer(f, held);
    event(f.app, held.sessionId, { type: "turn.started", data: { turn_id: id(79) } });
    answer(f.app, held.sessionId, "Added it. Ask the user.");
    await sending;
    expect(f.send).toHaveBeenCalledTimes(2);
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      note: "bots.thread.waiting",
    });
  });

  it("pauses with the chat open when an accepted steer never shows while the chat idles", async () => {
    const f = setup();
    const held = holdFirst(f, false);
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    expect(await f.store.steer(f.threadId, "are you there?")).toBe(true);
    answer(f.app, held.sessionId, "First pass.\nVIKI_DONE");
    await sending;
    const thread = f.store.getSnapshot().threads[0];
    expect(thread).toMatchObject({ phase: "paused", note: "bots.thread.steerUnread" });
    expect(f.viki.actions.closeSession).not.toHaveBeenCalled();
    expect(thread?.members[0]?.sessionId).toBe(held.sessionId);
  });

  it.each(["resolved", "between", "idle"] as const)(
    "refuses a steer %s, puts its text back and lets the handoff proceed",
    async (moment) => {
      const f = setup((n) => (n === 0 ? `Continue.\nVIKI_HANDOFF ${id(2)}` : "Ask the user."));
      const seen: unknown[] = [];
      const attempt = async () => {
        f.store.edit(f.threadId, "late words");
        const sends = f.send.mock.calls.length;
        const result = await f.store.steer(f.threadId, "late words");
        const thread = f.store.getSnapshot().threads[0];
        seen.push([result, thread?.note, thread?.draft, f.send.mock.calls.length - sends]);
      };
      if (moment === "resolved") {
        const close = vi.mocked(f.viki.actions.closeSession).getMockImplementation();
        vi.mocked(f.viki.actions.closeSession).mockImplementationOnce(async (...args) => {
          await attempt();
          return close?.(...args) as ReturnType<typeof f.viki.actions.closeSession>;
        });
      } else if (moment === "between") {
        const prepare = vi.mocked(f.launch.prepare).getMockImplementation();
        vi.mocked(f.launch.prepare).mockImplementation(async (...args) => {
          if (vi.mocked(f.launch.prepare).mock.calls.length === 2) await attempt();
          return prepare?.(...args) as ReturnType<ProfileLaunchApi["prepare"]>;
        });
      }
      await f.store.send(f.threadId, "request");
      if (moment === "idle") await attempt();
      expect(seen).toEqual([[false, "bots.thread.steerRefused", "late words", 0]]);
      expect(f.send).toHaveBeenCalledTimes(2);
      expect(f.store.getSnapshot().threads[0]?.messages.some((row) => row.steer)).toBe(false);
    },
  );

  it("ends the chain on T1 when Claude Code drops its steer", async () => {
    const f = setup((n) => (n === 2 ? "Ask the user." : ""));
    const held = holdFirst(f);
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    expect(await f.store.steer(f.threadId, "never mind")).toBe(true);
    answer(f.app, held.sessionId, `First pass.\nVIKI_HANDOFF ${id(2)}`);
    event(f.app, held.sessionId, {
      type: "steer.ended",
      data: { steer_id: held.steers[0] as string, outcome: "dropped" },
    });
    await sending;
    expect(f.viki.actions.closeSession).toHaveBeenNthCalledWith(1, held.sessionId, {
      generation: FAKE_GENERATION,
      turnId: held.t1,
    });
    expect(f.send).toHaveBeenCalledTimes(3);
    // The owner's message stays; nothing replied to it, so its continuation goes.
    expect(
      f.store.getSnapshot().threads[0]?.messages.map((row) => [row.memberKey, !!row.steer]),
    ).toEqual([
      [null, false],
      [id(1), false],
      [null, true],
      [id(2), false],
    ]);
  });

  it("lets a waiting steer run after Stop, then closes its chat", async () => {
    const f = setup();
    const held = holdFirst(f);
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    expect(await f.store.steer(f.threadId, "then wrap up")).toBe(true);
    f.store.stop();
    await sending;
    const member = () => f.store.getSnapshot().threads[0]?.members[0];
    expect(f.interrupt).toHaveBeenCalledTimes(1);
    expect(member()?.sessionId).toBe(held.sessionId);
    // Claude Code ends the stopped turn, then runs the waiting steer as its next turn.
    event(f.app, held.sessionId, {
      type: "turn.done",
      data: { status: "cancelled", usage: USAGE, context_percent: null, duration_ms: 0 },
    });
    readSteer(f, held);
    event(f.app, held.sessionId, { type: "turn.started", data: { turn_id: id(79) } });
    expect(f.viki.actions.closeSession).not.toHaveBeenCalled();
    answer(f.app, held.sessionId, "Wrapped up.");
    await vi.waitFor(() => expect(member()?.sessionId).toBeNull(), WAIT);
    expect(f.viki.actions.closeSession).toHaveBeenCalledWith(held.sessionId, {
      generation: FAKE_GENERATION,
      turnId: id(79),
    });
    expect(f.interrupt).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      note: "bots.thread.stopped",
    });
  });

  it("restores the steer and continuation marks from their sources", async () => {
    const f = setup();
    const rows = saves(f);
    const held = holdFirst(f);
    const sending = f.store.send(f.threadId, "original request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    expect(await f.store.steer(f.threadId, "also this")).toBe(true);
    readSteer(f, held);
    answer(f.app, held.sessionId, "Both done. Ask the user.");
    await sending;
    const thread = f.store.getSnapshot().threads[0] as TeamThread;
    const preview = (await vi.mocked(f.launch.prepare).mock.results[0]
      ?.value) as PreparedProfilePreview;
    f.launch.inspect = vi.fn(async () => preview);
    f.viki.actions.observeHistorySession = vi.fn((sessionId) => {
      f.app.update(sessionId, (view) => ({
        ...view,
        synced: true,
        historyCursor: null,
        turns: thread.messages.flatMap((row) => (row.turn ? [{ ...row.turn, history: true }] : [])),
      }));
      return () => {};
    });
    const restored = restore(f, [rows.at(-1) as SavedBotThread]);
    await vi.waitFor(
      () => expect(restored.getSnapshot().threads[0]?.sourceStatus).toBe("ready"),
      WAIT,
    );
    const messages = restored.getSnapshot().threads[0]?.messages ?? [];
    expect(messages.map((row) => [row.text, !!row.steer, !!row.continuation])).toEqual([
      ["original request", false, false],
      ["", false, false],
      ["also this", true, false],
      ["", false, true],
    ]);
    expect(messages[3]?.turn?.blocks).toMatchObject([{ text: "Both done. Ask the user." }]);
  });

  it("refuses a steer with fewer than two message slots left", async () => {
    const f = setup();
    const held = holdFirst(f);
    (f.store.getSnapshot().threads[0] as TeamThread).messages.push(
      ...Array.from({ length: 197 }, (_, n) => ({
        id: id(2000 + n),
        at: AT,
        memberKey: null,
        text: "earlier",
        sessionId: null,
        prompt: null,
      })),
    );
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    expect(f.store.getSnapshot().threads[0]?.messages).toHaveLength(199);
    f.store.edit(f.threadId, "one more");
    expect(await f.store.steer(f.threadId, "one more")).toBe(false);
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      draft: "one more",
      note: "bots.thread.steerRefused",
    });
    answer(f.app, held.sessionId, "Done. Ask the user.");
    await sending;
  });

  it("still retires the working Bot's chat after its chat refuses a steer", async () => {
    const f = setup((n) => (n === 1 ? "Ask the user." : ""));
    const held = holdFirst(f);
    refuseSteers(f, held);
    const release = vi.spyOn(f.editors, "releaseCompleted");
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    const turnReceipt = f.editors.getSnapshot().editors[held.sessionId]?.lastReceipt;
    f.store.edit(f.threadId, "too late?");
    expect(await f.store.steer(f.threadId, "too late?")).toBe(false);
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      draft: "too late?",
      note: "bots.thread.steerRefused",
    });
    expect(f.editors.getSnapshot().editors[held.sessionId]).toMatchObject({
      text: "",
      status: "saved",
      dispatchRefusal: null,
      lastReceipt: turnReceipt,
    });
    answer(f.app, held.sessionId, `First pass.\nVIKI_HANDOFF ${id(2)}`);
    await sending;
    expect(f.viki.actions.closeSession).toHaveBeenNthCalledWith(1, held.sessionId, {
      generation: FAKE_GENERATION,
      turnId: held.t1,
    });
    expect(release).toHaveBeenCalledWith(held.sessionId, held.t1);
    expect(f.editors.getSnapshot().editors[held.sessionId]).toBeUndefined();
    expect(f.send).toHaveBeenCalledTimes(3);
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      note: "bots.thread.waiting",
    });
  });

  it("closes the chat after its turn when Stop lands between a steer's save and its post", async () => {
    const f = setup();
    const held = holdFirst(f);
    const save = f.durable.saveDraft.bind(f.durable);
    let stopOnSave = false;
    vi.spyOn(f.durable, "saveDraft").mockImplementation(async (...args) => {
      const saved = await save(...args);
      if (stopOnSave && args[2]) {
        stopOnSave = false;
        f.store.stop();
      }
      return saved;
    });
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    const turnReceipt = f.editors.getSnapshot().editors[held.sessionId]?.lastReceipt;
    stopOnSave = true;
    expect(await f.store.steer(f.threadId, "and then?")).toBe(false);
    await sending;
    expect(f.send).toHaveBeenCalledTimes(1);
    expect(f.interrupt).toHaveBeenCalledTimes(1);
    expect(f.editors.getSnapshot().editors[held.sessionId]).toMatchObject({
      text: "",
      status: "saved",
      lastReceipt: turnReceipt,
    });
    expect(f.viki.actions.closeSession).not.toHaveBeenCalled();
    event(f.app, held.sessionId, {
      type: "turn.done",
      data: { status: "cancelled", usage: USAGE, context_percent: null, duration_ms: 0 },
    });
    await vi.waitFor(
      () => expect(f.store.getSnapshot().threads[0]?.members[0]?.sessionId).toBeNull(),
      WAIT,
    );
    expect(f.viki.actions.closeSession).toHaveBeenCalledWith(held.sessionId, {
      generation: FAKE_GENERATION,
      turnId: held.t1,
    });
    expect(f.editors.getSnapshot().editors[held.sessionId]).toBeUndefined();
    expect(f.store.getSnapshot().threads[0]?.phase).toBe("paused");
  });

  it("never clears what the owner types in the Bot's own chat during a refused steer", async () => {
    const f = setup();
    const held = holdFirst(f);
    refuseSteers(f, held);
    const refuse = f.send.getMockImplementation();
    f.send.mockImplementation(async (input) => {
      if (held.t1) f.editors.edit(input.session_id, "my own note");
      return refuse?.(input) as ReturnType<typeof f.send>;
    });
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    expect(await f.store.steer(f.threadId, "steer text")).toBe(false);
    expect(f.editors.getSnapshot().editors[held.sessionId]?.text).toBe("my own note");
    answer(f.app, held.sessionId, "First pass.\nVIKI_DONE");
    await sending;
    expect(f.editors.getSnapshot().editors[held.sessionId]?.text).toBe("my own note");
    expect(f.viki.actions.closeSession).not.toHaveBeenCalled();
  });

  it("ends the chain on T1 when Claude Code drops a steer before its receipt arrives", async () => {
    const f = setup((n) => (n === 2 ? "Ask the user." : ""));
    const held = holdFirst(f);
    const holding = f.send.getMockImplementation();
    f.send.mockImplementation(async (input) => {
      const receipt = await holding?.(input);
      // Shown, then dropped, both before the post's receipt reaches steer().
      if (input.session_id === held.sessionId && held.steers.length)
        event(f.app, held.sessionId, {
          type: "steer.ended",
          data: { steer_id: held.steers.at(-1) as string, outcome: "dropped" },
        });
      return receipt as Awaited<ReturnType<typeof f.send>>;
    });
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    expect(await f.store.steer(f.threadId, "never mind")).toBe(true);
    answer(f.app, held.sessionId, `First pass.\nVIKI_HANDOFF ${id(2)}`);
    await sending;
    expect(f.send).toHaveBeenCalledTimes(3);
    expect(f.viki.actions.closeSession).toHaveBeenNthCalledWith(1, held.sessionId, {
      generation: FAKE_GENERATION,
      turnId: held.t1,
    });
    expect(f.store.getSnapshot().threads[0]).toMatchObject({
      phase: "paused",
      note: "bots.thread.waiting",
    });
  });

  it("reloads a thread paused on an unread steer and hands on the turn before it", async () => {
    const f = setup();
    const rows = saves(f);
    const held = holdFirst(f, false);
    const sending = f.store.send(f.threadId, "request");
    await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
    expect(await f.store.steer(f.threadId, "are you there?")).toBe(true);
    answer(f.app, held.sessionId, packet({ progress: "First pass" }));
    await sending;
    expect(f.store.getSnapshot().threads[0]?.note).toBe("bots.thread.steerUnread");
    const preview = (await vi.mocked(f.launch.prepare).mock.results[0]
      ?.value) as PreparedProfilePreview;
    f.launch.inspect = vi.fn(async () => preview);
    const live = f.app.getSnapshot().sessions[held.sessionId]?.turns ?? [];
    f.viki.actions.observeHistorySession = vi.fn((sessionId) => {
      f.app.update(sessionId, (view) => ({
        ...view,
        synced: true,
        historyCursor: null,
        turns: live.map((turn) => ({ ...turn, history: true })),
      }));
      return () => {};
    });
    const restored = restore(f, [rows.at(-1) as SavedBotThread]);
    await vi.waitFor(
      () => expect(restored.getSnapshot().threads[0]?.sourceStatus).toBe("ready"),
      WAIT,
    );
    const back = restored.getSnapshot().threads[0];
    expect(back?.note).toBe("bots.thread.restored");
    // The steer's reply never came: its continuation stays, with no turn to show.
    expect(back?.messages.slice(2)).toMatchObject([
      { memberKey: null, text: "are you there?", steer: true },
      { memberKey: id(1), continuation: true },
    ]);
    expect(back?.messages[3]?.turn).toBeUndefined();
    const sends = f.send.mock.calls.length;
    await restored.send(f.threadId, "try again");
    expect(f.send).toHaveBeenCalledTimes(sends + 1);
    const prompt = [...f.durable.commands.values()].at(-1)?.text ?? "";
    expect(promptContext(prompt)?.handoff?.progress).toBe("First pass");
  });

  it.each(["disposeRunning", "dispose", "reload", "closed", "blocked", "generation"] as const)(
    "gives up closing a stopped Bot's chat on %s before its steer's turn ends",
    async (kind) => {
      vi.useFakeTimers();
      cleanups.push(() => vi.useRealTimers());
      const f = setup();
      const held = holdFirst(f);
      const subscribe = f.app.subscribe;
      let watching = 0;
      vi.spyOn(f.app, "subscribe").mockImplementation((listener) => {
        const off = subscribe(listener);
        let on = true;
        watching += 1;
        return () => {
          if (on) watching -= 1;
          on = false;
          off();
        };
      });
      const sending = f.store.send(f.threadId, "request");
      await vi.waitFor(() => expect(f.store.steerable(f.threadId)).toBe(true), WAIT);
      expect(await f.store.steer(f.threadId, "then wrap up")).toBe(true);
      // Disposing a running store stops it too, so its watcher starts disposed.
      if (kind === "disposeRunning") f.store.dispose();
      else f.store.stop();
      await sending;
      expect(watching).toBe(kind === "disposeRunning" ? 0 : 1);
      if (kind === "dispose") {
        f.store.dispose();
        expect(watching).toBe(0);
      }
      if (kind === "reload") f.store.reload();
      if (kind === "closed")
        event(f.app, held.sessionId, { type: "session.closed", data: { reason: "requested" } });
      if (kind === "blocked")
        f.app.update(held.sessionId, (view) => ({
          ...view,
          blocked: { reason: "live_elsewhere" } as SessionView["blocked"],
        }));
      if (kind === "generation")
        f.app.update(held.sessionId, (view) => ({ ...view, generation: id(98) }));
      event(f.app, held.sessionId, {
        type: "turn.done",
        data: { status: "cancelled", usage: USAGE, context_percent: null, duration_ms: 0 },
      });
      expect(watching).toBe(0);
      readSteer(f, held);
      event(f.app, held.sessionId, { type: "turn.started", data: { turn_id: id(79) } });
      answer(f.app, held.sessionId, "Wrapped up.");
      expect(f.viki.actions.closeSession).not.toHaveBeenCalled();
      expect(watching).toBe(0);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  describe("Bot code copies", () => {
    const COPY_REQUEST = "If the handoff names another viki/bot- branch, run git merge --ff-only";
    /** Grants the first Bot a code folder; the first Bot's chat is sent but never starts a turn. */
    function granted(f: ReturnType<typeof setup>, index = 0) {
      const row = f.members[index]?.bot as BotRow;
      row.code_folder = { workspace_id: id(91), name: "App" };
      const atOnce = f.send.getMockImplementation();
      const seen = { sessionId: "", turns: [] as Turn[] };
      f.send.mockImplementation(async (input) => {
        const receipt = (await atOnce?.(input)) as Awaited<ReturnType<typeof f.send>>;
        seen.sessionId = input.session_id;
        seen.turns = f.app.getSnapshot().sessions[input.session_id]?.turns ?? [];
        f.app.update(input.session_id, (view) => ({ ...view, turns: [] }));
        return receipt;
      });
      return seen;
    }
    const note = (f: ReturnType<typeof setup>) => f.store.getSnapshot().threads[0]?.note;
    const lastPrompt = (f: ReturnType<typeof setup>) =>
      [...f.durable.commands.values()].at(-1)?.text ?? "";

    it("keeps the pre-start timer for an ungranted Bot", async () => {
      vi.useFakeTimers();
      const f = setup();
      const atOnce = f.send.getMockImplementation();
      f.send.mockImplementation(async (input) => {
        const receipt = (await atOnce?.(input)) as Awaited<ReturnType<typeof f.send>>;
        f.app.update(input.session_id, (view) => ({ ...view, turns: [] }));
        return receipt;
      });
      const sending = f.store.send(f.threadId, "request");
      await vi.advanceTimersByTimeAsync(60);
      await sending;
      expect(note(f)).toBe("bots.thread.failed");
      expect(f.store.getSnapshot().busy).toBe(false);
      expect(vi.getTimerCount()).toBe(0);
    });

    it("waits past the start timer for a granted Bot's copy, then finishes on its turn", async () => {
      vi.useFakeTimers();
      const f = setup();
      const seen = granted(f);
      const sending = f.store.send(f.threadId, "request");
      await vi.advanceTimersByTimeAsync(5_000);
      expect(f.store.getSnapshot().busy).toBe(true);
      expect(f.store.getSnapshot().threads[0]?.phase).toBe("running");
      f.app.update(seen.sessionId, (view) => ({ ...view, turns: seen.turns }));
      await vi.advanceTimersByTimeAsync(5_000);
      await sending;
      expect(f.store.getSnapshot().busy).toBe(false);
      expect(vi.getTimerCount()).toBe(0);
    });

    it.each(["refused", "error", "closed", "stop", "disconnect"] as const)(
      "ends a granted Bot's wait on %s with no retry",
      async (kind) => {
        vi.useFakeTimers();
        const f = setup();
        const seen = granted(f);
        const sending = f.store.send(f.threadId, "request");
        await vi.advanceTimersByTimeAsync(5_000);
        expect(f.send).toHaveBeenCalledTimes(1);
        expect(f.store.getSnapshot().busy).toBe(true);
        if (kind === "refused")
          f.app.update(
            seen.sessionId,
            (view) => ({ ...view, openRefused: "unavailable" }) as never,
          );
        if (kind === "error")
          f.app.update(seen.sessionId, (view) => ({ ...view, error: "unavailable" }) as never);
        if (kind === "closed")
          event(f.app, seen.sessionId, { type: "session.closed", data: { reason: "requested" } });
        if (kind === "stop") f.store.stop();
        if (kind === "disconnect") f.app.setStatus("closed");
        await vi.advanceTimersByTimeAsync(0);
        await sending;
        expect(f.send).toHaveBeenCalledTimes(1);
        expect(f.store.getSnapshot().busy).toBe(false);
        expect(f.store.getSnapshot().threads[0]?.phase).toBe("paused");
        expect(vi.getTimerCount()).toBe(0);
      },
    );

    it("takes the grant from the turn's own member, not another one's", async () => {
      vi.useFakeTimers();
      const f = setup();
      granted(f, 1);
      const sending = f.store.send(f.threadId, "request");
      await vi.advanceTimersByTimeAsync(60);
      await sending;
      expect(note(f)).toBe("bots.thread.failed");
      expect(vi.getTimerCount()).toBe(0);
    });

    it("asks only a granted worker to fast-forward a named branch in its copy", async () => {
      const f = setup(() => "Done.\nVIKI_DONE", "", true);
      const [, worker, other] = f.members;
      (worker?.bot as BotRow).code_folder = { workspace_id: id(91), name: "App" };
      await f.store.send(f.threadId, "request", worker?.key);
      // The worker's prompt, then the Checker's review it hands to.
      const prompts = () => [...f.durable.commands.values()].map((row) => row.text);
      expect(prompts()[0]).toContain(COPY_REQUEST);
      expect(prompts()[0]).toContain("list both branches in artifacts.");
      expect(prompts()[1]).not.toContain(COPY_REQUEST);
      await f.store.send(f.threadId, "again", other?.key);
      expect(prompts()[2]).not.toContain(COPY_REQUEST);
    });

    it("gives a granted Checker no merge instruction", async () => {
      const f = setup(() => "Done.\nVIKI_DONE", "", true);
      const checker = f.members.find((row) => row.seat?.seat === "checker");
      if (checker?.bot) checker.bot.code_folder = { workspace_id: id(91), name: "App" };
      await f.store.send(f.threadId, "request", checker?.key);
      expect(lastPrompt(f)).not.toContain(COPY_REQUEST);
    });
  });
});
