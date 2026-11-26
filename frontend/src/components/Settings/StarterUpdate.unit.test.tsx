import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { StarterItem, StarterPlan } from "../../clients/app/shellApi";
import { t, tf } from "../../i18n/t";
import { StarterUpdate, type StarterUpdateProps } from "./StarterUpdate";
import { starterHandoff } from "./starterHandoff";

afterEach(cleanup);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function planWith(items: StarterItem[], overrides: Partial<StarterPlan> = {}): StarterPlan {
  return {
    state: "available",
    from_version: 5,
    to_version: 7,
    notify: false,
    items,
    agents: null,
    ...overrides,
  };
}

function renderUpdate(overrides: Partial<StarterUpdateProps> = {}) {
  const load = overrides.load ?? vi.fn(async () => planWith([]));
  const onDraft = overrides.onDraft ?? vi.fn(() => true);
  const props: StarterUpdateProps = {
    workspaceId: "ws-1",
    refreshKey: "1",
    protectedVault: false,
    onDraft,
    load,
    ...overrides,
  };
  const result = render(<StarterUpdate {...props} />);
  return { ...result, load, onDraft, props };
}

const ADD: StarterItem = {
  path: ".claude/skills/example/SKILL.md",
  action: "add",
  diff: null,
  text: "new skill",
};
const REPLACE: StarterItem = {
  path: "system/log.md",
  action: "replace",
  diff: "--- a/system/log.md\n+++ b/system/log.md",
  text: null,
};
const EDITOR: StarterItem = {
  path: "AGENTS.md",
  action: "editor",
  diff: "--- a/AGENTS.md\n+++ b/AGENTS.md",
  text: null,
};
const MERGE: StarterItem = {
  path: "30-knowledge/some-note.md",
  action: "merge",
  diff: "--- a/x\n+++ b/x",
  text: null,
};
const MANUAL: StarterItem = {
  path: "CLAUDE.md",
  action: "manual",
  diff: "--- a/CLAUDE.md\n+++ b/CLAUDE.md",
  text: null,
};
const REMOVE: StarterItem = {
  path: "_templates/old-template.md",
  action: "remove",
  diff: null,
  text: null,
};
const SKIP: StarterItem = { path: "system/unreadable.md", action: "skip", diff: null, text: null };
const ALL_ACTIONS = [ADD, REPLACE, EDITOR, MERGE, MANUAL, REMOVE, SKIP];

test("renders nothing while the plan is loading", () => {
  const { container } = renderUpdate({ load: () => new Promise(() => {}) });
  expect(container.firstChild).toBeNull();
});

test("renders nothing for state none", async () => {
  const entry = deferred<StarterPlan>();
  const { container } = renderUpdate({ load: () => entry.promise });
  expect(container.firstChild).toBeNull();
  await act(async () => {
    entry.resolve(planWith([], { state: "none" }));
    await entry.promise;
  });
  expect(container.firstChild).toBeNull();
});

test("shows the read-failure message when load rejects", async () => {
  renderUpdate({ load: async () => Promise.reject(new Error("boom")) });
  expect(await screen.findByText(t("settings.starter.error"))).toBeTruthy();
});

test("shows the newer-than-this-app message", async () => {
  renderUpdate({ load: async () => planWith([], { state: "newer" }) });
  expect(await screen.findByText(t("settings.starter.newer"))).toBeTruthy();
});

test("shows the up-to-date message with the version interpolated", async () => {
  renderUpdate({ load: async () => planWith([], { state: "current", to_version: 9 }) });
  expect(await screen.findByText(tf("settings.starter.current", { to: 9 }))).toBeTruthy();
});

test("current with no items (or only skip items) shows only the up-to-date line", async () => {
  renderUpdate({ load: async () => planWith([SKIP], { state: "current", to_version: 9 }) });
  expect(await screen.findByText(tf("settings.starter.current", { to: 9 }))).toBeTruthy();
  expect(document.querySelector(".settings-details")).toBeNull();
  expect(screen.queryByText(t("settings.starter.update_in_chat"))).toBeNull();
});

test("current with a kept item shows the collapsed group and no Update in a chat button", async () => {
  renderUpdate({
    load: async () => planWith([REPLACE, SKIP], { state: "current", to_version: 9 }),
  });
  expect(await screen.findByText(tf("settings.starter.kept", { count: 1 }))).toBeTruthy();
  expect(screen.getByText(REPLACE.path)).toBeTruthy();
  expect(screen.queryByText(SKIP.path)).toBeNull();
  expect(screen.queryByText(t("settings.starter.update_in_chat"))).toBeNull();
});

test("current state still shows Open AGENTS.md with the update for an ordinary vault", async () => {
  const proposal = { proposal: "new text", sha256: "a".repeat(64) };
  const onReviewAgents = vi.fn();
  renderUpdate({
    load: async () => planWith([], { state: "current", to_version: 9, agents: proposal }),
    onReviewAgents,
    protectedVault: false,
  });
  fireEvent.click(await screen.findByText(t("settings.starter.open_agents")));
  expect(onReviewAgents).toHaveBeenCalledWith(proposal);
  expect(await screen.findByText(t("settings.starter.agents_status"))).toBeTruthy();
});

test("available: heading and intro line, with 'unknown' when from_version is null", async () => {
  renderUpdate({
    load: async () => planWith([], { from_version: null, to_version: 7, notify: true }),
  });
  expect(await screen.findByText(t("settings.starter.heading"))).toBeTruthy();
  expect(
    await screen.findByText(tf("settings.starter.intro", { to: 7, from: "unknown" })),
  ).toBeTruthy();
});

test("available: groups render in the fixed order, only when non-empty, each row as details/summary/pre", async () => {
  renderUpdate({ load: async () => planWith(ALL_ACTIONS) });
  await screen.findByText(t("settings.starter.heading"));

  const headings = screen.getAllByRole("heading", { level: 4 }).map((node) => node.textContent);
  expect(headings).toEqual([
    t("settings.starter.group.add"),
    t("settings.starter.group.replace"),
    t("settings.starter.group.editor"),
    t("settings.starter.group.merge"),
    t("settings.starter.group.manual"),
    t("settings.starter.group.remove"),
    t("settings.starter.group.skip"),
  ]);

  for (const item of ALL_ACTIONS) {
    const summary = screen.getByText(item.path);
    expect(summary.tagName).toBe("SUMMARY");
    const details = summary.closest("details");
    expect(details?.className).toBe("settings-details");
    const pre = details?.querySelector("pre");
    expect(pre?.textContent).toBe(item.diff ?? item.text ?? "");
  }
});

test("a group with no items is not rendered", async () => {
  renderUpdate({ load: async () => planWith([ADD]) });
  await screen.findByText(t("settings.starter.heading"));
  expect(screen.getAllByRole("heading", { level: 4 })).toHaveLength(1);
  expect(screen.queryByText(t("settings.starter.group.replace"))).toBeNull();
});

test("the chat button is absent with nothing to dispatch and notify false", async () => {
  renderUpdate({ load: async () => planWith([MANUAL, SKIP], { notify: false }) });
  await screen.findByText(t("settings.starter.heading"));
  expect(screen.queryByText(t("settings.starter.update_in_chat"))).toBeNull();
});

test("the chat button appears from notify alone, even with nothing dispatchable", async () => {
  renderUpdate({ load: async () => planWith([MANUAL], { notify: true }) });
  expect(await screen.findByText(t("settings.starter.update_in_chat"))).toBeTruthy();
});

test("the chat button calls onDraft with starterHandoff(plan, protectedVault) and shows the sent status", async () => {
  const plan = planWith([ADD]);
  const onDraft = vi.fn(() => true);
  renderUpdate({ load: async () => plan, onDraft, protectedVault: true });
  fireEvent.click(await screen.findByText(t("settings.starter.update_in_chat")));
  expect(onDraft).toHaveBeenCalledWith(starterHandoff(plan, true));
  expect(await screen.findByText(t("settings.starter.drafted"))).toBeTruthy();
});

test("the chat button shows the no-room alert when onDraft refuses", async () => {
  const onDraft = vi.fn(() => false);
  renderUpdate({ load: async () => planWith([ADD]), onDraft });
  fireEvent.click(await screen.findByText(t("settings.starter.update_in_chat")));
  expect(await screen.findByText(t("settings.starter.no_room"))).toBeTruthy();
});

test("a protected vault folds an editor-only plan's rules change into the chat button", async () => {
  // With no other chat-dispatchable item and notify false, only protectedVault=true (which
  // chatItems treats as "no dedicated AGENTS.md editor exists") pulls the editor item in.
  renderUpdate({
    load: async () => planWith([EDITOR], { notify: false }),
    protectedVault: true,
  });
  expect(await screen.findByText(t("settings.starter.update_in_chat"))).toBeTruthy();
});

test("the Open AGENTS.md button is absent with no agents proposal", async () => {
  renderUpdate({
    load: async () => planWith([], { agents: null }),
    onReviewAgents: vi.fn(),
  });
  await screen.findByText(t("settings.starter.heading"));
  expect(screen.queryByText(t("settings.starter.open_agents"))).toBeNull();
});

test("the Open AGENTS.md button is absent in a protected vault even with a proposal", async () => {
  renderUpdate({
    load: async () => planWith([], { agents: { proposal: "new text", sha256: "a".repeat(64) } }),
    onReviewAgents: vi.fn(),
    protectedVault: true,
  });
  await screen.findByText(t("settings.starter.heading"));
  expect(screen.queryByText(t("settings.starter.open_agents"))).toBeNull();
});

test("the Open AGENTS.md button is absent with no onReviewAgents handler", async () => {
  renderUpdate({
    load: async () => planWith([], { agents: { proposal: "new text", sha256: "a".repeat(64) } }),
  });
  await screen.findByText(t("settings.starter.heading"));
  expect(screen.queryByText(t("settings.starter.open_agents"))).toBeNull();
});

test("the Open AGENTS.md button calls onReviewAgents with the proposal and shows its status", async () => {
  const proposal = { proposal: "new text", sha256: "a".repeat(64) };
  const onReviewAgents = vi.fn();
  renderUpdate({
    load: async () => planWith([], { agents: proposal }),
    onReviewAgents,
    protectedVault: false,
  });
  fireEvent.click(await screen.findByText(t("settings.starter.open_agents")));
  expect(onReviewAgents).toHaveBeenCalledWith(proposal);
  expect(await screen.findByText(t("settings.starter.agents_status"))).toBeTruthy();
});

test("reloads when workspaceId changes and when refreshKey changes, dropping a stale earlier answer", async () => {
  const pending = new Map<string, ReturnType<typeof deferred<StarterPlan>>>();
  const load = vi.fn((id: string) => {
    const entry = deferred<StarterPlan>();
    pending.set(id, entry);
    return entry.promise;
  });

  const { rerender } = render(
    <StarterUpdate
      workspaceId="ws-a"
      refreshKey="1"
      protectedVault={false}
      onDraft={vi.fn()}
      load={load}
    />,
  );
  expect(load).toHaveBeenCalledTimes(1);
  expect(load).toHaveBeenLastCalledWith("ws-a");

  // Switch workspace before the first answer arrives, then resolve the OLD one last: its answer
  // must not overwrite what the new workspace's load already settled.
  rerender(
    <StarterUpdate
      workspaceId="ws-b"
      refreshKey="1"
      protectedVault={false}
      onDraft={vi.fn()}
      load={load}
    />,
  );
  expect(load).toHaveBeenCalledTimes(2);
  expect(load).toHaveBeenLastCalledWith("ws-b");

  const wsBEntry = pending.get("ws-b");
  const wsAEntry = pending.get("ws-a");
  await act(async () => {
    wsBEntry?.resolve(planWith([], { state: "current", to_version: 22 }));
    await wsBEntry?.promise;
  });
  expect(screen.getByText(tf("settings.starter.current", { to: 22 }))).toBeTruthy();

  await act(async () => {
    wsAEntry?.resolve(planWith([], { state: "current", to_version: 11 }));
    await wsAEntry?.promise;
  });
  expect(screen.queryByText(tf("settings.starter.current", { to: 11 }))).toBeNull();
  expect(screen.getByText(tf("settings.starter.current", { to: 22 }))).toBeTruthy();

  // A refreshKey change with the same workspace reloads too.
  rerender(
    <StarterUpdate
      workspaceId="ws-b"
      refreshKey="2"
      protectedVault={false}
      onDraft={vi.fn()}
      load={load}
    />,
  );
  expect(load).toHaveBeenCalledTimes(3);
  expect(load).toHaveBeenLastCalledWith("ws-b");
});
