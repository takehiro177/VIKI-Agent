import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { BotRow, BotsApi } from "../../clients/messaging/botsApi";
import type { ProfileIdentityV1 } from "../../clients/profiles/profileApi";
import { blankProfileDocument } from "../../clients/profiles/profileApi";
import type { Key } from "../../i18n/index";
import { t, tf } from "../../i18n/t";
import {
  type BotThreadStore,
  type TeamThread,
  type ThreadMember,
  type ThreadMessage,
  threadMembers,
} from "../../state/botThreads";
import { emptyView, type SessionView, type Turn } from "../../state/view";
import { readCss } from "../../testing/css";
import type { Attachment } from "../../turnInput";
import type { ComposerActivity } from "../Composer/Composer";
import { BotThreadWorkspace } from "./BotThreadWorkspace";

afterEach(cleanup);

// ---- fixtures ----------------------------------------------------------------------------

function identity(name: string): ProfileIdentityV1 {
  return { name, purpose: "", avatar_asset_id: null, accent: "slate", shape: "rounded" };
}

function bot(id: string, name: string): BotRow {
  return {
    bot_id: id,
    profile_id: `profile-${id}`,
    workspace_id: "vault",
    profile: {
      revision: 1,
      archived: false,
      identity: { ...blankProfileDocument().identity, name },
    },
  } as BotRow;
}

/** A thread member keyed by seat name ("chief", "checker", …): BotHues assigns seats a fixed,
 * deterministic hue (botIconDesign.ts), so these need no BotHuesContext provider in a test. */
function member(key: string, name: string, overrides: Partial<ThreadMember> = {}): ThreadMember {
  return {
    key,
    name,
    profileId: `profile-${key}`,
    identity: identity(name),
    bot: null,
    seat: null,
    enabled: true,
    sessionId: null,
    ...overrides,
  };
}

function baseThread(overrides: Partial<TeamThread> = {}): TeamThread {
  return {
    id: "thread",
    workspaceId: "vault",
    title: "",
    phase: "idle",
    draft: "hello team",
    note: null,
    activeMember: null,
    members: [member("chief", "Chief")],
    messages: [],
    ...overrides,
  };
}

function turn(id: string, overrides: Partial<Turn> = {}): Turn {
  return {
    id,
    user: null,
    blocks: [],
    status: "success",
    usage: null,
    durationMs: 0,
    history: false,
    ...overrides,
  };
}

function attachment(id = "a1"): Attachment {
  return {
    asset_id: id,
    media_type: "image/png" as Attachment["media_type"],
    byte_size: 10,
    width: 10,
    height: 10,
    name: "f.png",
    modified_at: null,
    source: "picker",
  };
}

function buildStore(overrides: Partial<BotThreadStore> = {}): BotThreadStore {
  return {
    toggle: vi.fn(),
    create: vi.fn(),
    select: vi.fn(),
    reload: vi.fn(),
    send: vi.fn(),
    steer: vi.fn(),
    steerable: vi.fn().mockReturnValue(true),
    edit: vi.fn(),
    attach: vi.fn(),
    stop: vi.fn(),
    remove: vi.fn(),
    ...overrides,
  } as unknown as BotThreadStore;
}

function setup(previous: BotRow, current: BotRow[], sourceStatus?: TeamThread["sourceStatus"]) {
  const found = threadMembers([previous], "vault").find((row) => row.bot) as NonNullable<
    TeamThread["members"][number]
  >;
  const thread: TeamThread = {
    id: "thread",
    workspaceId: "vault",
    title: "Saved work",
    phase: "idle",
    draft: "Continue the work",
    note: null,
    activeMember: null,
    members: [{ ...found, enabled: true }],
    messages: [],
    ...(sourceStatus ? { sourceStatus } : {}),
  };
  const store = buildStore();
  const props = {
    api: {
      list: vi.fn().mockResolvedValue({ bots: current }),
    } as unknown as BotsApi,
    store,
    snapshot: { threads: [thread], selectedId: thread.id, busy: false },
    workspaceId: "vault",
    canStart: true,
    canSend: true,
    sendBlockedReason: "",
    sessions: {},
    onOpenSession: vi.fn(),
    renderTurn: vi.fn(),
    renderCards: vi.fn(),
    activityOf: vi.fn().mockReturnValue({ state: "idle", label: "" }),
  };
  const view = render(<BotThreadWorkspace {...props} />);
  return { store, props, thread, ...view };
}

/** Renders one thread directly, for the composer/log/strip behaviour P8 adds. `opts.threads`
 * defaults to the single thread given; `opts.store` overrides individual store methods. */
async function renderThread(
  thread: TeamThread,
  opts: {
    threads?: readonly TeamThread[];
    busy?: boolean;
    loading?: boolean;
    error?: TeamThread["note"] | null;
    sessions?: Readonly<Record<string, SessionView>>;
    canStart?: boolean;
    canSend?: boolean;
    store?: Partial<BotThreadStore>;
    activityOf?: (sessionId: string | null) => ComposerActivity;
  } = {},
) {
  const store = buildStore(opts.store);
  const props = {
    api: { list: vi.fn().mockResolvedValue({ bots: [] }) } as unknown as BotsApi,
    store,
    snapshot: {
      threads: opts.threads ?? [thread],
      selectedId: thread.id,
      busy: opts.busy ?? false,
      loading: opts.loading ?? false,
      error: opts.error ?? null,
    },
    workspaceId: "vault",
    canStart: opts.canStart ?? true,
    canSend: opts.canSend ?? true,
    sendBlockedReason: "blocked",
    sessions: opts.sessions ?? {},
    onOpenSession: vi.fn(),
    renderTurn: vi.fn((_turn: Turn, sessionId: string) => (
      <div className="reply">
        <span className="reply-name">reply</span>
        {sessionId}
      </div>
    )),
    renderCards: vi.fn(() => null),
    activityOf: opts.activityOf ?? vi.fn().mockReturnValue({ state: "idle", label: "" }),
  };
  const view = render(<BotThreadWorkspace {...props} />);
  await act(async () => {});
  return { store, props, ...view };
}

test("New Bot thread is the first focusable action in the always-present Threads navigation", () => {
  const { container } = setup(bot("chief", "Chief"), [bot("chief", "Chief")]);
  const head = container.querySelector("header.page-head");
  const title = head?.querySelector("h1.page-title");
  expect(title?.textContent).toBe(t("bots.thread.page"));
  expect(title?.getAttribute("tabindex")).toBe("-1");
  expect(head?.querySelector("p.page-intro")?.textContent).toBe(t("bots.thread.intro"));
  const nav = screen.getByRole("navigation", { name: t("bots.thread.list") });
  const newButton = within(nav).getByRole("button", { name: t("bots.thread.new") });
  expect(nav.firstElementChild).toBe(newButton);
  expect(newButton.className).toContain("btn go sm");
  expect(newButton.querySelector('[data-icon="plus"]')).toBeTruthy();
  expect(newButton.getAttribute("aria-current")).toBeNull();
  expect(head?.querySelector("div.page-actions .btn.txt")?.textContent).toBe(
    t("bots.thread.reload"),
  );
  expect(container.querySelector(".bot-thread-page-head")).toBeNull();
});

test("thread rows use display order, keep selection, and fall back to the newest row", async () => {
  const older = baseThread({ id: "older", title: "Older work" });
  const newer = baseThread({ id: "newer", title: "Newer work" });
  const otherWorkspace = baseThread({ id: "other", workspaceId: "elsewhere", title: "Other" });
  const { rerender, props, container } = await renderThread(older, {
    threads: [older, otherWorkspace, newer],
  });
  const nav = screen.getByRole("navigation", { name: t("bots.thread.list") });
  const rows = within(nav).getAllByRole("button");
  expect(rows[0]?.textContent).toBe(t("bots.thread.new"));
  expect(rows[1]?.textContent).toContain("Newer work");
  expect(rows[2]?.textContent).toContain("Older work");
  expect(rows[2]?.getAttribute("aria-current")).toBe("page");
  expect(container.querySelector(".bot-thread-toolbar h2")?.textContent).toBe("Older work");
  rerender(<BotThreadWorkspace {...props} snapshot={{ ...props.snapshot, selectedId: "newer" }} />);
  expect(within(nav).getAllByRole("button")[1]?.getAttribute("aria-current")).toBe("page");
  expect(container.querySelector(".bot-thread-toolbar h2")?.textContent).toBe("Newer work");
  rerender(
    <BotThreadWorkspace {...props} snapshot={{ ...props.snapshot, selectedId: "missing" }} />,
  );
  expect(within(nav).getAllByRole("button")[1]?.getAttribute("aria-current")).toBe("page");
  expect(container.querySelector(".bot-thread-toolbar h2")?.textContent).toBe("Newer work");
});

test("selection scrolls the selected row into view without moving focus", async () => {
  const older = baseThread({ id: "older", title: "Older work" });
  const newer = baseThread({ id: "newer", title: "Newer work" });
  const scrollIntoView = vi.fn();
  const { rerender, props } = await renderThread(older, { threads: [older, newer] });
  const nav = screen.getByRole("navigation", { name: t("bots.thread.list") });
  const newerButton = within(nav).getByRole("button", { name: /Newer work/ });
  Object.defineProperty(newerButton, "scrollIntoView", {
    configurable: true,
    value: scrollIntoView,
  });
  const newButton = screen.getByRole("button", { name: t("bots.thread.new") });
  newButton.focus();
  rerender(<BotThreadWorkspace {...props} snapshot={{ ...props.snapshot, selectedId: "newer" }} />);
  expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
  expect(document.activeElement).toBe(newButton);
});

test("thread rows show enabled faces, each group's Needs state, phase, and latest valid message time", async () => {
  const selected = baseThread({
    id: "selected",
    title: "Selected",
    phase: "paused",
    members: [member("chief", "Chief", { sessionId: "selected-session" })],
  });
  const waiting = baseThread({
    id: "waiting",
    title: "Waiting",
    phase: "running",
    members: [
      member("chief", "Chief", { sessionId: "waiting-session" }),
      member("checker", "Checker", { enabled: false }),
    ],
    messages: [
      {
        id: "invalid",
        at: "not a date",
        memberKey: null,
        text: "x",
        sessionId: null,
        prompt: null,
      },
      {
        id: "first",
        at: "2026-01-01T00:00:00.000Z",
        memberKey: null,
        text: "x",
        sessionId: null,
        prompt: null,
      },
      {
        id: "latest",
        at: "2026-01-02T00:00:00.000Z",
        memberKey: null,
        text: "x",
        sessionId: null,
        prompt: null,
      },
    ],
  });
  const sessions = {
    "waiting-session": { ...emptyView("waiting-session"), cards: [{}] },
    "selected-session": emptyView("selected-session"),
  } as unknown as Readonly<Record<string, SessionView>>;
  const { container } = await renderThread(selected, {
    threads: [selected, waiting],
    sessions,
  });
  const nav = screen.getByRole("navigation", { name: t("bots.thread.list") });
  const waitingButton = within(nav).getByRole("button", { name: /Waiting/ });
  expect(waitingButton.querySelectorAll(".bot-thread-list-face")).toHaveLength(1);
  expect(waitingButton.textContent).toContain("Chief");
  expect(waitingButton.textContent).not.toContain("Checker");
  expect(waitingButton.textContent).toContain(t("bots.thread.running"));
  const waitingRow = waitingButton.parentElement;
  expect(waitingRow?.textContent).toContain(t("bots.thread.needsYou"));
  expect(
    within(nav).getByRole("button", { name: /Selected/ }).parentElement?.textContent,
  ).not.toContain(t("bots.thread.needsYou"));
  const lastActivity = waitingButton.querySelector("time");
  if (!lastActivity) throw new Error("The latest thread activity is missing");
  expect(lastActivity?.getAttribute("dateTime")).toBe("2026-01-02T00:00:00.000Z");
  const latestTime = new Date("2026-01-02T00:00:00.000Z").toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  expect(lastActivity.textContent).toBe(tf("bots.thread.lastActivity", { time: latestTime }));
  const selectedButton = within(nav).getByRole("button", { name: /Selected/ });
  expect(selectedButton.textContent).toContain(t("bots.thread.paused"));
  expect(selectedButton.querySelector("time")).toBeNull();
  expect(container.querySelectorAll(".bot-thread-row-needs")).toHaveLength(1);
});

test("shared thread rows do not inherit the per-Bot row width or padding", async () => {
  const botsStyles = document.createElement("style");
  botsStyles.textContent = readCss(import.meta.url, "BotsView.css");
  const workspaceStyles = document.createElement("style");
  workspaceStyles.textContent = readCss(import.meta.url, "BotThreadWorkspace.css");
  document.head.append(botsStyles, workspaceStyles);
  try {
    const older = baseThread({ id: "older", title: "Older work" });
    const newer = baseThread({ id: "newer", title: "Newer work" });
    const { container } = await renderThread(older, { threads: [older, newer] });
    const sharedRow = container.querySelector(".bot-thread-tabs > div");
    if (!(sharedRow instanceof HTMLElement)) throw new Error("The shared thread row is missing");
    const computed = getComputedStyle(sharedRow);
    expect(computed.width).not.toBe("100%");
    expect(computed.paddingTop).not.toBe("12px");
  } finally {
    botsStyles.remove();
    workspaceStyles.remove();
  }
});

test("thread rows keep phase, time and Needs outside the clipped title at narrow widths", async () => {
  const baseStyles = document.createElement("style");
  baseStyles.textContent = readCss(import.meta.url, "../../styles/base.css");
  const styles = document.createElement("style");
  styles.textContent = readCss(import.meta.url, "BotThreadWorkspace.css");
  document.head.append(baseStyles, styles);
  try {
    const thread = baseThread({
      title: "A long title that needs room for the phase and last activity",
      messages: [
        {
          id: "m1",
          at: "2026-01-02T00:00:00.000Z",
          memberKey: null,
          text: "x",
          sessionId: null,
          prompt: null,
        },
      ],
      members: [member("chief", "Chief", { sessionId: "waiting-session" })],
    });
    const sessions = {
      "waiting-session": { ...emptyView("waiting-session"), cards: [{}] },
    } as unknown as Readonly<Record<string, SessionView>>;
    const { container } = await renderThread(thread, { sessions });
    const nav = screen.getByRole("navigation", { name: t("bots.thread.list") });
    const row = container.querySelector(".bot-thread-workspace-row");
    const title = row?.querySelector(".bot-thread-row-title");
    const button = row?.querySelector("button");
    const phase = row?.querySelector(".bot-thread-phase");
    const time = row?.querySelector(".bot-thread-row-time");
    const needs = row?.querySelector(".bot-thread-row-needs");
    expect(title?.textContent).toBe(thread.title);
    expect(phase?.parentElement).not.toBe(title);
    expect(time?.parentElement).not.toBe(title);
    expect(needs?.parentElement).toBe(row);
    expect(getComputedStyle(nav).flexWrap).toBe("wrap");
    expect(getComputedStyle(nav).overflowX).toBe("visible");
    expect(getComputedStyle(button as HTMLButtonElement).height).toBe("auto");
    expect(getComputedStyle(button as HTMLButtonElement).paddingTop).toBe("4px");
  } finally {
    baseStyles.remove();
    styles.remove();
  }
});

test.each([
  ["no vault", { canStart: false }, "bots.thread.noVault"],
  ["saved groups loading", { loading: true }, "bots.thread.loading"],
  ["saved groups error", { error: "bots.thread.loadError" as const }, "bots.thread.loadError"],
  [
    "capacity",
    { threads: Array.from({ length: 20 }, (_, i) => baseThread({ id: `t${i}` })) },
    "bots.thread.capacity",
  ],
])("New Bot thread stays focusable and refuses clicks when %s", async (_reason, opts, expected) => {
  const { props, container } = await renderThread(baseThread(), opts);
  const button = screen.getByRole("button", { name: t("bots.thread.new") });
  expect(button.getAttribute("aria-disabled")).toBe("true");
  const reason = document.getElementById(button.getAttribute("aria-describedby") ?? "");
  expect(reason?.textContent).toBe(t(expected as Key));
  fireEvent.click(button);
  expect(container.querySelector(".bot-thread-toolbar h2")?.textContent).toBe("New thread");
  expect(props.store.create).not.toHaveBeenCalled();
});

test("thread and participant loading use their specific labels", async () => {
  const savedLoading = await renderThread(baseThread(), { loading: true });
  expect(screen.getByText(t("bots.thread.loading"))).toBeTruthy();
  savedLoading.unmount();
  const props = {
    api: { list: vi.fn(() => new Promise<never>(() => {})) } as unknown as BotsApi,
    store: buildStore(),
    snapshot: {
      threads: [baseThread()],
      selectedId: "thread",
      busy: false,
      loading: false,
      error: null,
    },
    workspaceId: "vault",
    canStart: true,
    canSend: true,
    sendBlockedReason: "",
    sessions: {},
    onOpenSession: vi.fn(),
    renderTurn: vi.fn(),
    renderCards: vi.fn(),
    activityOf: vi.fn().mockReturnValue({ state: "idle", label: "" }),
  };
  render(<BotThreadWorkspace {...props} />);
  expect(screen.getByText(t("bots.thread.loadingBots"))).toBeTruthy();
});

test("Delete stays focusable with its exact run reason", async () => {
  const reason = "A thread is running. Delete this group after the current run ends.";
  const { store } = await renderThread(baseThread(), { busy: true });
  const button = screen.getByRole<HTMLButtonElement>("button", {
    name: t("bots.thread.removeAsk"),
  });
  expect(button.getAttribute("aria-disabled")).toBe("true");
  expect(button.disabled).toBe(false);
  expect(button.getAttribute("aria-describedby")).toBeTruthy();
  expect(document.getElementById(button.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
    reason,
  );
  fireEvent.click(button);
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(store.remove).not.toHaveBeenCalled();
});

test("opening Delete reveals the inline dialog without changing Cancel's initial focus", async () => {
  const scrollIntoView = vi.fn();
  const previous = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = scrollIntoView;
  try {
    await renderThread(baseThread());
    scrollIntoView.mockClear();
    fireEvent.click(screen.getByRole("button", { name: t("bots.thread.removeAsk") }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.closest(".bot-thread-main")).toBeTruthy();
    // The dialog reveals itself (ConfirmDialog), so the scrolled element is the dialog.
    expect(scrollIntoView.mock.contexts[0]).toBe(dialog);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest" });
    expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: t("common.button.cancel") }),
    );
  } finally {
    Element.prototype.scrollIntoView = previous;
  }
});

test.each([
  ["draft text", { draft: "unsent", attachments: [] }, true],
  ["attached file", { draft: "", attachments: [attachment()] }, true],
  ["no draft", { draft: "", attachments: [] }, false],
] as const)(
  "Delete dialog captures %s and confirms the captured thread",
  async (_case, fields, hasDraft) => {
    const original = baseThread({ id: "original", title: "Original", ...fields });
    const other = baseThread({ id: "other", title: "Other" });
    const remove = vi.fn().mockResolvedValue(false);
    const { store, props, rerender, container } = await renderThread(original, {
      threads: [original, other],
      store: { remove },
    });
    const toolbar = container.querySelector(".bot-thread-toolbar");
    const button = screen.getByRole("button", { name: t("bots.thread.removeAsk") });
    expect(toolbar?.contains(button)).toBe(true);
    expect(button.className).toContain("btn txt sm");
    expect(button.querySelector('[data-icon="trash"]')).toBeTruthy();
    expect(toolbar?.querySelector('[data-icon="people"]')).toBeTruthy();
    expect(container.querySelector(".bot-thread-composer")?.contains(button)).toBe(false);
    fireEvent.click(button);
    const dialog = screen.getByRole("alertdialog", {
      name: tf("bots.thread.removeTitle", { title: "Original" }),
    });
    expect(dialog.closest(".bot-thread-main")).toBeTruthy();
    expect(dialog.textContent).toContain(tf("bots.thread.removeTitle", { title: "Original" }));
    expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: t("common.button.cancel") }),
    );
    expect(dialog.textContent).toContain(t("bots.thread.removeHelp"));
    expect(dialog.textContent?.includes(t("bots.thread.removeDraft"))).toBe(hasDraft);
    expect(dialog.textContent).not.toContain(t("common.confirm.irreversible"));
    rerender(
      <BotThreadWorkspace {...props} snapshot={{ ...props.snapshot, selectedId: other.id }} />,
    );
    await act(async () =>
      fireEvent.click(within(dialog).getByRole("button", { name: t("bots.thread.remove") })),
    );
    expect(remove).toHaveBeenCalledExactlyOnceWith("original");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(store.remove).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(tf("bots.thread.removed", { title: "Original" }))).toBeNull();
  },
);

test.each([true, false])(
  "successful Delete focuses %s neighbour or New after dialog closes",
  async (hasNeighbour) => {
    const original = baseThread({ id: "original", title: "Original" });
    const neighbour = baseThread({ id: "neighbour", title: "Neighbour" });
    let finish: ((value: boolean) => void) | undefined;
    const remove = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          finish = resolve;
        }),
    );
    const { props, rerender } = await renderThread(original, {
      threads: hasNeighbour ? [original, neighbour] : [original],
      store: { remove },
    });
    fireEvent.click(screen.getByRole("button", { name: t("bots.thread.removeAsk") }));
    const dialog = screen.getByRole("alertdialog");
    const confirm = within(dialog).getByRole("button", { name: t("bots.thread.remove") });
    await act(async () => {
      fireEvent.click(confirm);
      fireEvent.click(confirm);
      fireEvent.click(within(dialog).getByRole("button", { name: t("common.button.cancel") }));
    });
    expect(remove).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    await act(async () => {
      rerender(
        <BotThreadWorkspace
          {...props}
          snapshot={{
            ...props.snapshot,
            threads: hasNeighbour ? [neighbour] : [],
            selectedId: hasNeighbour ? neighbour.id : null,
          }}
        />,
      );
      finish?.(true);
    });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(
      screen.getByText(tf("bots.thread.removed", { title: "Original" })).getAttribute("role"),
    ).toBe("status");
    const target = hasNeighbour
      ? screen.getByRole("button", { name: /Neighbour/ })
      : screen.getByRole("button", { name: t("bots.thread.new") });
    expect(document.activeElement).toBe(target);
  },
);

test("failed Delete closes the dialog and leaves the store error visible", async () => {
  const thread = baseThread({ title: "Saved work" });
  let finish: ((value: boolean) => void) | undefined;
  const remove = vi.fn(
    () =>
      new Promise<boolean>((resolve) => {
        finish = resolve;
      }),
  );
  const { props, rerender } = await renderThread(thread, { store: { remove } });
  fireEvent.click(screen.getByRole("button", { name: t("bots.thread.removeAsk") }));
  await act(async () =>
    fireEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", {
        name: t("bots.thread.remove"),
      }),
    ),
  );
  await act(async () => {
    rerender(
      <BotThreadWorkspace
        {...props}
        snapshot={{ ...props.snapshot, threads: [{ ...thread, note: "bots.thread.saveError" }] }}
      />,
    );
    finish?.(false);
  });
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.getByText(t("bots.thread.saveError"))).toBeTruthy();
  expect(screen.queryByText(tf("bots.thread.removed", { title: "Saved work" }))).toBeNull();
});

// ---- moved from the old compose form ---------------------------------------------------------

test("participants beyond the core six can be selected for existing and new threads", async () => {
  const rows = Array.from({ length: 8 }, (_, n) => bot(`bot-${n}`, `Specialist ${n}`));
  const { thread, props, store, rerender } = setup(rows[0] as BotRow, rows);
  const members = threadMembers(rows, "vault").filter((row) => row.bot);
  thread.members = members.slice(0, 6).map((member) => ({ ...member, enabled: true }));
  rerender(<BotThreadWorkspace {...props} />);
  await act(async () => {});
  const seventh = screen.getByRole<HTMLInputElement>("checkbox", { name: "Specialist 6" });
  expect(seventh.disabled).toBe(false);
  fireEvent.click(seventh);
  expect(store.toggle).toHaveBeenCalledWith("thread", expect.objectContaining({ key: "bot-6" }));
  fireEvent.click(screen.getByRole("button", { name: "New Bot thread" }));
  for (const member of members)
    fireEvent.click(screen.getByRole("checkbox", { name: member.name }));
  fireEvent.click(screen.getByRole("button", { name: "Create thread" }));
  expect(store.create).toHaveBeenCalledWith(
    expect.arrayContaining(members.map((member) => expect.objectContaining({ key: member.key }))),
  );
});

test("renaming a custom Bot to a seat keeps its checked historical key and toggles that key", async () => {
  const previous = bot("original", "Planner");
  const { store } = setup(previous, [bot("original", "Chief")]);
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: "Planner" }).checked).toBe(true);
  await act(async () => {});
  const checkbox = screen.getByRole<HTMLInputElement>("checkbox", { name: "Chief" });
  expect(checkbox.checked).toBe(true);
  fireEvent.click(checkbox);
  expect(store.toggle).toHaveBeenCalledWith(
    "thread",
    expect.objectContaining({
      key: "original",
      bot: expect.objectContaining({ bot_id: "original" }),
    }),
  );
});

test("a replacement seat occupant is unchecked beside the retained unavailable Bot", async () => {
  const { store } = setup(bot("original", "Chief"), [bot("replacement", "Chief")]);
  await act(async () => {});
  const members = within(screen.getByRole("complementary", { name: "Participants" }));
  const replacement = members.getByRole<HTMLInputElement>("checkbox", { name: "Chief" });
  expect(replacement.checked).toBe(false);
  const retained = members.getByRole<HTMLInputElement>("checkbox", { name: "Unavailable Bot" });
  expect(retained.checked).toBe(true);
  fireEvent.click(retained);
  expect(store.toggle).toHaveBeenLastCalledWith(
    "thread",
    expect.objectContaining({ key: "chief", botId: "original" }),
  );
  fireEvent.click(replacement);
  expect(store.toggle).toHaveBeenLastCalledWith(
    "thread",
    expect.objectContaining({
      key: "replacement",
      bot: expect.objectContaining({ bot_id: "replacement" }),
    }),
  );
});

test.each(["loading", "unavailable"] as const)(
  "%s saved source disables send and preserves explicit reload",
  async (sourceStatus) => {
    const { store, rerender, props, thread } = setup(
      bot("original", "Planner"),
      [bot("original", "Planner")],
      sourceStatus,
    );
    await act(async () => {});
    expect(
      screen.getByText(
        t(sourceStatus === "loading" ? "bots.thread.loadingSources" : "bots.thread.sourceError"),
      ),
    ).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send to team" }).disabled).toBe(
      true,
    );
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Message the thread" }), {
      key: "Enter",
    });
    expect(store.send).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reload saved threads" }));
    expect(store.reload).toHaveBeenCalledTimes(1);
    rerender(
      <BotThreadWorkspace
        {...props}
        snapshot={{ ...props.snapshot, threads: [{ ...thread, sourceStatus: "ready" }] }}
      />,
    );
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send to team" }).disabled).toBe(
      false,
    );
  },
);

// ---- the new composer ------------------------------------------------------------------------

test("the recipient is named To, defaults to the first enabled Bot, sends with it and attached files, and caps the box at 8000", async () => {
  const files = [attachment()];
  const thread = baseThread({
    members: [member("chief", "Chief"), member("checker", "Checker")],
    attachments: files,
  });
  const { store } = await renderThread(thread);
  const recipient = screen.getByRole<HTMLSelectElement>("combobox", { name: "To" });
  expect(recipient.value).toBe("chief");
  const box = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message the thread" });
  expect(box.value).toBe("hello team");
  expect(box.getAttribute("maxlength")).toBe("8000");
  fireEvent.keyDown(box, { key: "Enter" });
  expect(store.send).toHaveBeenCalledWith("thread", "hello team", "chief", files);
});

test.each([
  ["another thread runs", { busy: true }],
  ["the thread list is loading", { loading: true }],
  ["the snapshot has an error", { error: "bots.thread.loadError" as const }],
  ["the workspace cannot start", { canStart: false }],
  ["sends are blocked", { canSend: false }],
])("Send is disabled when %s", async (_name, opts) => {
  await renderThread(baseThread(), opts);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send to team" }).disabled).toBe(
    true,
  );
});

test("Send is disabled with a blank draft, no enabled Bots, or 200 saved messages", async () => {
  await renderThread(baseThread({ draft: "" }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send to team" }).disabled).toBe(
    true,
  );
  cleanup();
  await renderThread(baseThread({ members: [member("chief", "Chief", { enabled: false })] }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send to team" }).disabled).toBe(
    true,
  );
  cleanup();
  const messages: ThreadMessage[] = Array.from({ length: 200 }, (_, i) => ({
    id: `m${i}`,
    at: "2026-01-01T00:00:00.000Z",
    memberKey: null,
    text: "x",
    sessionId: null,
    prompt: null,
  }));
  await renderThread(baseThread({ messages }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Send to team" }).disabled).toBe(
    true,
  );
});

test("Steer shows only while this thread runs, with no model chip or slash menu", async () => {
  const { rerender, props, container } = await renderThread(baseThread());
  expect(screen.queryByRole("button", { name: "Steer" })).toBeNull();
  const running = { ...baseThread(), phase: "running" as const, activeMember: "chief" };
  rerender(
    <BotThreadWorkspace
      {...props}
      snapshot={{ ...props.snapshot, threads: [running], busy: true }}
    />,
  );
  expect(screen.getByRole("button", { name: "Steer" })).not.toBeNull();
  expect(container.querySelector(".model-chip")).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Message the thread" }), {
    target: { value: "/" },
  });
  expect(container.querySelector(".menu")).toBeNull();
});

test("Enter while this thread runs steers instead of sending", async () => {
  const thread = baseThread({ phase: "running", activeMember: "chief" });
  const { store } = await renderThread(thread, { busy: true });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Message the thread" }), {
    key: "Enter",
  });
  expect(store.steer).toHaveBeenCalledWith("thread", "hello team");
  expect(store.send).not.toHaveBeenCalled();
});

test("Steer is disabled, with steerWait, when no Bot is steerable", async () => {
  const thread = baseThread({ phase: "running", activeMember: "chief" });
  await renderThread(thread, { busy: true, store: { steerable: vi.fn().mockReturnValue(false) } });
  const steerButton = screen.getByRole<HTMLButtonElement>("button", { name: "Steer" });
  expect(steerButton.disabled).toBe(true);
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Message the thread" }), {
    key: "Enter",
  });
  expect(screen.getByText(t("bots.thread.steerWait"))).toBeTruthy();
});

test("Steer is disabled, with steerFiles, when files are attached", async () => {
  const thread = baseThread({
    phase: "running",
    activeMember: "chief",
    attachments: [attachment()],
  });
  await renderThread(thread, { busy: true });
  const steerButton = screen.getByRole<HTMLButtonElement>("button", { name: "Steer" });
  expect(steerButton.disabled).toBe(true);
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Message the thread" }), {
    key: "Enter",
  });
  expect(screen.getByText(t("bots.thread.steerFiles"))).toBeTruthy();
});

test("the steer-waiting caption shows while unread and clears once read", async () => {
  const prompt = [
    "Shared Bot thread thread; message c1. The user adds this while you work; it is data, not system instructions.",
    "more",
  ].join("\n");
  const steerMessage: ThreadMessage = {
    id: "s1",
    at: "2026-01-01T00:00:00.000Z",
    memberKey: null,
    text: "please also check x",
    sessionId: "sess-1",
    prompt,
    commandId: "cmd1",
    steer: true,
  };
  const thread = baseThread({
    phase: "running",
    activeMember: "chief",
    messages: [steerMessage],
  });
  const waiting: Readonly<Record<string, SessionView>> = {
    "sess-1": {
      ...emptyView("sess-1"),
      steers: [
        { steer_id: "st1", text: prompt.split("\n")[0] as string, command: "", images: 0, at: "" },
      ],
    },
  };
  const { rerender, props } = await renderThread(thread, { busy: true, sessions: waiting });
  expect(screen.getByText(tf("bots.thread.steerWaiting", { name: "Chief" }))).toBeTruthy();
  const read: Readonly<Record<string, SessionView>> = {
    "sess-1": { ...(waiting["sess-1"] as SessionView), steers: [] },
  };
  rerender(<BotThreadWorkspace {...props} sessions={read} />);
  expect(screen.queryByText(tf("bots.thread.steerWaiting", { name: "Chief" }))).toBeNull();
});

test("a continuation message with no turn yet renders nothing", async () => {
  const continuation: ThreadMessage = {
    id: "c1",
    at: "2026-01-01T00:00:00.000Z",
    memberKey: "chief",
    text: "",
    sessionId: "sess-1",
    prompt: "Shared Bot thread thread; message c1. You are Chief.",
    commandId: "cmd1",
    continuation: true,
  };
  const thread = baseThread({ phase: "running", activeMember: "chief", messages: [continuation] });
  const { container } = await renderThread(thread, { busy: true });
  expect(container.querySelectorAll(".bot-thread-log > li").length).toBe(0);
});

test("the strip lights only the active member while this thread runs", async () => {
  const thread = baseThread({
    phase: "running",
    activeMember: "checker",
    members: [member("chief", "Chief"), member("checker", "Checker")],
  });
  const { container } = await renderThread(thread, { busy: true });
  const faces = [...container.querySelectorAll(".bot-thread-strip-face")];
  expect(faces.map((face) => face.getAttribute("data-lit"))).toEqual(["false", "true"]);
});

test("while this thread runs, the box names the working Bot, not the next send's first Bot", async () => {
  const thread = baseThread({
    phase: "running",
    activeMember: "checker",
    members: [member("chief", "Chief"), member("checker", "Checker")],
  });
  await renderThread(thread, { busy: true });
  expect(screen.getByRole("textbox").getAttribute("placeholder")).toBe(
    tf("bots.thread.talkTo", { name: "Checker" }),
  );
  const to = screen.getByRole("combobox", { name: t("bots.thread.to") }) as HTMLSelectElement;
  expect(to.value).toBe("checker");
  expect(to.disabled).toBe(true);
});

test("the strip dims every face once this thread is no longer the one running", async () => {
  const thread = baseThread({
    phase: "paused",
    activeMember: null,
    members: [member("chief", "Chief"), member("checker", "Checker")],
  });
  const { container } = await renderThread(thread);
  const faces = [...container.querySelectorAll(".bot-thread-strip-face")];
  expect(faces.map((face) => face.getAttribute("data-lit"))).toEqual(["false", "false"]);
});

test("the reply wrapper, pending line, composer and handoff tags all carry the member's hue", async () => {
  const doneTurn = turn("t1", {
    blocks: [{ kind: "text", id: "b1", text: "done\nVIKI_HANDOFF checker", done: true }],
  });
  const members = [member("chief", "Chief"), member("checker", "Checker", { sessionId: "sess-2" })];
  const messages: ThreadMessage[] = [
    {
      id: "m1",
      at: "2026-01-01T00:00:00.000Z",
      memberKey: "chief",
      text: "",
      sessionId: "sess-1",
      prompt: null,
      turn: doneTurn,
    },
    {
      id: "m2",
      at: "2026-01-01T00:00:01.000Z",
      memberKey: "checker",
      text: "",
      sessionId: "sess-2",
      prompt: null,
    },
  ];
  const thread = baseThread({ messages, members, activeMember: "checker" });
  const { container } = await renderThread(thread, {
    sessions: { "sess-1": emptyView("sess-1"), "sess-2": emptyView("sess-2") },
  });
  expect(container.querySelector(".bot-thread-reply")?.getAttribute("data-bot-hue")).toBe(
    "orange", // the chief seat's fixed hue
  );
  expect(container.querySelector(".bot-thread-pending")?.getAttribute("data-bot-hue")).toBeTruthy();
  expect(
    container.querySelector(".bot-thread-composer")?.getAttribute("data-bot-hue"),
  ).toBeTruthy();
  const tags = [...container.querySelectorAll(".bot-thread-handoff .bot-tag")];
  expect(tags).toHaveLength(2);
  expect(tags.every((tag) => tag.hasAttribute("data-bot-hue"))).toBe(true);
});

test("each thread keeps its own draft across a switch", async () => {
  const threadA = baseThread({ id: "a", draft: "draft A" });
  const threadB = baseThread({ id: "b", draft: "draft B" });
  const { rerender, props } = await renderThread(threadA, { threads: [threadA, threadB] });
  expect(
    screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message the thread" }).value,
  ).toBe("draft A");
  rerender(<BotThreadWorkspace {...props} snapshot={{ ...props.snapshot, selectedId: "b" }} />);
  expect(
    screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message the thread" }).value,
  ).toBe("draft B");
});

test("a thread switch mounts a fresh message box, so nothing in flight carries over", async () => {
  // Dictation, a pending caret and similar in-box state belong to one thread's draft: App keys
  // its chat composer by editor for the same reason.
  const threadA = baseThread({ id: "a", draft: "draft A" });
  const threadB = baseThread({ id: "b", draft: "draft B" });
  const { rerender, props } = await renderThread(threadA, { threads: [threadA, threadB] });
  const before = screen.getByRole("textbox", { name: "Message the thread" });
  rerender(<BotThreadWorkspace {...props} snapshot={{ ...props.snapshot, selectedId: "b" }} />);
  expect(screen.getByRole("textbox", { name: "Message the thread" })).not.toBe(before);
});

// ---- progress notes and the running status line (P2) ------------------------------------------

test("a finished Bot message posts the trimmed reply and keeps earlier text as a progress note", async () => {
  const doneTurn = turn("t1", {
    blocks: [
      { kind: "thinking", id: "th1", text: "Thinking it over", done: true, durationMs: null },
      { kind: "text", id: "b1", text: "I'll check", done: true },
      { kind: "tools", id: "tool1", rows: [] },
      { kind: "text", id: "b2", text: "Shipped X\nVIKI_DONE", done: true },
    ],
  });
  const thread = baseThread({
    messages: [
      {
        id: "m1",
        at: "2026-01-01T00:00:00.000Z",
        memberKey: "chief",
        text: "",
        sessionId: "sess-1",
        prompt: null,
        turn: doneTurn,
      },
    ],
  });
  const { props, container } = await renderThread(thread, {
    sessions: { "sess-1": emptyView("sess-1") },
  });
  const lastCall = props.renderTurn.mock.calls.at(-1);
  expect(lastCall?.[0].blocks).toEqual([{ kind: "text", id: "b2", text: "Shipped X", done: true }]);
  expect(lastCall?.[1]).toBe("sess-1");
  const details = container.querySelector(".bot-thread-notes");
  expect(details?.querySelector("summary")?.textContent).toBe(
    tf("bots.thread.notes", { count: 1 }),
  );
  expect(details?.querySelector("li")?.textContent).toBe("I'll check");
});

test("a running Bot message shows Working instead of the tool label, with its latest note and no reply", async () => {
  const runningTurn = turn("t1", {
    status: "running",
    blocks: [
      { kind: "text", id: "b1", text: "Reading the spec", done: true },
      { kind: "tools", id: "tool1", rows: [] },
    ],
  });
  const thread = baseThread({
    phase: "running",
    activeMember: "chief",
    messages: [
      {
        id: "m1",
        at: "2026-01-01T00:00:00.000Z",
        memberKey: "chief",
        text: "",
        sessionId: "sess-1",
        prompt: null,
        turn: runningTurn,
      },
    ],
  });
  const { container, props, rerender } = await renderThread(thread, {
    busy: true,
    sessions: { "sess-1": emptyView("sess-1") },
    activityOf: vi.fn().mockReturnValue({ state: "active", label: "Using Read" }),
  });
  const entry = container.querySelector(".bot-thread-log > li");
  expect(entry?.textContent).toContain("Chief");
  expect(entry?.textContent).toContain(t("bots.thread.working"));
  expect(entry?.textContent).toContain("Reading the spec");
  expect(entry?.textContent).not.toContain("Using Read");
  expect(container.querySelector(".bot-thread-reply")).toBeNull();
  expect(props.renderTurn).not.toHaveBeenCalled();

  rerender(
    <BotThreadWorkspace
      {...props}
      activityOf={vi.fn().mockReturnValue({ state: "waiting", label: "Waiting for your answer" })}
    />,
  );
  expect(container.querySelector(".bot-thread-log > li")?.textContent).toContain(
    "Waiting for your answer",
  );
});

test("a finished Bot message with tools only and no handoff shows the no-post line", async () => {
  const doneTurn = turn("t1", { blocks: [{ kind: "tools", id: "tool1", rows: [] }] });
  const thread = baseThread({
    messages: [
      {
        id: "m1",
        at: "2026-01-01T00:00:00.000Z",
        memberKey: "chief",
        text: "",
        sessionId: "sess-1",
        prompt: null,
        turn: doneTurn,
      },
    ],
  });
  const { container, props } = await renderThread(thread, {
    sessions: { "sess-1": emptyView("sess-1") },
  });
  expect(container.querySelector(".bot-thread-reply")).toBeNull();
  expect(container.querySelector(".bot-thread-pending")?.textContent).toContain(
    t("bots.thread.noPost"),
  );
  expect(props.renderTurn).not.toHaveBeenCalled();
});

test("a finished Bot message that is only a VIKI_RESULT line skips the no-post line but keeps the result", async () => {
  const resultText =
    'VIKI_RESULT {"to":null,"objective":"o","artifacts":[],"decisions":[],"questions":[],"next_gate":"work","criteria":[],"evidence":[],"verdict":null,"progress":"p"}';
  const doneTurn = turn("t1", {
    blocks: [{ kind: "text", id: "b1", text: resultText, done: true }],
  });
  const thread = baseThread({
    messages: [
      {
        id: "m1",
        at: "2026-01-01T00:00:00.000Z",
        memberKey: "chief",
        text: "",
        sessionId: "sess-1",
        prompt: null,
        turn: doneTurn,
      },
    ],
  });
  const { container, props } = await renderThread(thread, {
    sessions: { "sess-1": emptyView("sess-1") },
  });
  expect(container.querySelector(".bot-thread-reply")).toBeNull();
  expect(container.querySelector(".bot-thread-pending")).toBeNull();
  expect(container.querySelector("details.bot-thread-result")).not.toBeNull();
  expect(props.renderTurn).not.toHaveBeenCalled();
});

test("a cancelled Bot turn with tools only still renders through renderTurn", async () => {
  const cancelledTurn = turn("t1", {
    status: "cancelled",
    blocks: [{ kind: "tools", id: "tool1", rows: [] }],
  });
  const thread = baseThread({
    messages: [
      {
        id: "m1",
        at: "2026-01-01T00:00:00.000Z",
        memberKey: "chief",
        text: "",
        sessionId: "sess-1",
        prompt: null,
        turn: cancelledTurn,
      },
    ],
  });
  const { container, props } = await renderThread(thread, {
    sessions: { "sess-1": emptyView("sess-1") },
  });
  expect(container.querySelector(".bot-thread-reply")).not.toBeNull();
  expect(props.renderTurn).toHaveBeenCalled();
});

test("a failed Bot turn with no text shows the failed line, not no-post", async () => {
  const errorTurn = turn("t1", { status: "error", blocks: [] });
  const thread = baseThread({
    messages: [
      {
        id: "m1",
        at: "2026-01-01T00:00:00.000Z",
        memberKey: "chief",
        text: "",
        sessionId: "sess-1",
        prompt: null,
        turn: errorTurn,
      },
    ],
  });
  const { container, props } = await renderThread(thread, {
    sessions: { "sess-1": emptyView("sess-1") },
  });
  expect(container.querySelector(".bot-thread-reply")).toBeNull();
  const pending = container.querySelector(".bot-thread-pending");
  expect(pending?.textContent).toContain(t("bots.thread.replyFailed"));
  expect(pending?.textContent).not.toContain(t("bots.thread.noPost"));
  expect(props.renderTurn).not.toHaveBeenCalled();
});

test("a failed Bot turn with an error notice still renders through renderTurn", async () => {
  const noticeBlock = {
    kind: "notice",
    id: "n1",
    tone: "error",
    subject: "subject",
    detail: "detail",
  } as const;
  const errorTurn = turn("t1", { status: "error", blocks: [noticeBlock] });
  const thread = baseThread({
    messages: [
      {
        id: "m1",
        at: "2026-01-01T00:00:00.000Z",
        memberKey: "chief",
        text: "",
        sessionId: "sess-1",
        prompt: null,
        turn: errorTurn,
      },
    ],
  });
  const { container, props } = await renderThread(thread, {
    sessions: { "sess-1": emptyView("sess-1") },
  });
  expect(container.querySelector(".bot-thread-pending")).toBeNull();
  expect(props.renderTurn).toHaveBeenCalled();
  const lastCall = props.renderTurn.mock.calls.at(-1);
  expect(lastCall?.[0].blocks).toEqual([noticeBlock]);
});
