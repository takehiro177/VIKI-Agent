import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { WorkspaceJson } from "../../clients/app/shellApi";
import {
  type ArchiveJson,
  type DecisionJson,
  type RootsApi,
  RootsApiError,
  type WorktreeJson,
} from "../../clients/code/rootsApi";
import { en as EN } from "../../i18n/index";
import { resetRoots } from "../../state/sessionRoots";
import { Worktrees } from "./Worktrees";

const W = "00000000-0000-4000-8000-0000000000a1";
const OTHER = "00000000-0000-4000-8000-0000000000a2";
const OID = "0123456789abcdef0123456789abcdef01234567";

afterEach(() => {
  cleanup();
  resetRoots();
  vi.restoreAllMocks();
});

const folder = (rootId: string | null = "1:2") =>
  ({
    workspace_id: W,
    kind: "code",
    name: "Alpha",
    policy: "ordinary",
    policy_revision: 3,
    root_physical_id: rootId ?? undefined,
    root: "x",
    root_available: true,
  }) as WorkspaceJson;

let counter = 0;
const worktree = (over: Partial<WorktreeJson> = {}): WorktreeJson => {
  counter += 1;
  return {
    worktree_id: `00000000-0000-4000-8000-${String(counter).padStart(12, "0")}`,
    workspace_id: W,
    branch: `viki/b${counter}`,
    base_oid: OID,
    path: `C:\\area\\wt${counter}`,
    state: "ready",
    created_at: `2026-10-03T10:00:0${counter % 10}Z`,
    updated_at: "2026-10-03T10:00:00Z",
    ...over,
  };
};

/** A stub whose list is what the "server" holds: create and remove change it. */
function stub(held: WorktreeJson[] = [], over: Partial<RootsApi> = {}): RootsApi {
  return {
    roots: vi.fn().mockResolvedValue({ roots: [], truncated: false }),
    worktrees: vi.fn().mockImplementation(async () => ({ worktrees: [...held], truncated: false })),
    record: vi.fn(),
    create: vi.fn().mockImplementation(async (_w: string, body: { branch: string }) => {
      const made = worktree({ branch: body.branch });
      held.unshift(made);
      return made;
    }),
    remove: vi.fn(),
    archives: vi.fn().mockResolvedValue({ enabled: true, archives: [] }),
    cleanup: vi.fn().mockResolvedValue([]),
    restore: vi.fn(),
    ...over,
  };
}

const settle = () => act(async () => {});

async function setup(
  api: RootsApi,
  props: {
    folder?: WorkspaceJson;
    start?: () => Promise<string | null>;
    onReview?: (worktreeId: string) => void;
    onRaiseLimit?: () => void;
  } = {},
) {
  const onStartSession = vi.fn(props.start ?? (async () => null));
  render(
    <Worktrees
      api={api}
      folder={props.folder ?? folder()}
      onStartSession={onStartSession}
      {...(props.onReview ? { onReview: props.onReview } : {})}
      {...(props.onRaiseLimit ? { onRaiseLimit: props.onRaiseLimit } : {})}
    />,
  );
  await settle();
  return { onStartSession };
}

const rowOf = (text: string) => {
  const row = screen.getByText(text).closest("li");
  if (!row) throw new Error(`no row for ${text}`);
  return within(row);
};
const branchBox = () => screen.getByLabelText("Branch name") as HTMLInputElement;
const create = async (branch?: string) => {
  if (branch !== undefined) fireEvent.change(branchBox(), { target: { value: branch } });
  fireEvent.click(screen.getByRole("button", { name: "Create worktree" }));
  await settle();
};

test("the form starts with a dated branch name", async () => {
  await setup(stub());
  expect(branchBox().value).toMatch(/^viki\/\d{8}-[0-9a-f]{4}$/);
  expect(screen.getByText(/not a sandbox/)).toBeTruthy();
});

test("create sends the branch and both identity fields and shows the new ready row", async () => {
  const api = stub();
  await setup(api);
  await create("viki/feature");
  expect(api.create).toHaveBeenCalledWith(W, {
    policy_revision: 3,
    root_physical_id: "1:2",
    branch: "viki/feature",
  });
  const row = rowOf("viki/feature");
  expect(row.getByText("Ready")).toBeTruthy();
  expect(row.getByText("C:\\area\\wt1")).toBeTruthy();
  expect(row.getByText(OID.slice(0, 12))).toBeTruthy();
  // The next name is fresh, so a second create does not hit branch_exists.
  expect(branchBox().value).not.toBe("viki/feature");
});

test("the button reads Creating… and is disabled while the call runs", async () => {
  let finish!: (value: WorktreeJson) => void;
  const api = stub([], {
    create: vi.fn().mockReturnValue(new Promise<WorktreeJson>((done) => (finish = done))),
  });
  await setup(api);
  await create("viki/slow");
  const busy = screen.getByRole("button", { name: "Creating…" }) as HTMLButtonElement;
  expect(busy.disabled).toBe(true);
  await act(async () => finish(worktree({ branch: "viki/slow" })));
  expect(
    (screen.getByRole("button", { name: "Create worktree" }) as HTMLButtonElement).disabled,
  ).toBe(false);
});

const REFUSALS: [string, string][] = [
  ["invalid_branch", "Use letters, digits and . _ - / (up to 100)."],
  ["branch_exists", "A branch with this name exists. Choose another name."],
  ["no_commits", "This folder has no commit yet."],
  ["worktree_limit", "VIKI keeps at most 32 worktrees. Remove one first."],
  ["trust_required", "Sessions start only in a trusted folder."],
  ["workspace_changed", "This folder changed. Reopen the Code view and try again."],
  ["root_unavailable", "This folder changed. Reopen the Code view and try again."],
  ["not_repository", "Worktrees need the repository's top folder."],
  ["not_repository_root", "Worktrees need the repository's top folder."],
  ["vault_locked", "The folder is busy. Try again in a moment."],
  ["git_unavailable", "Git could not do this. Try again later."],
  ["git_failed", "Git could not do this. Try again later."],
  ["git_common_unavailable", "Git could not do this. Try again later."],
  ["unsupported_filesystem", "This folder's drive doesn't support worktrees from VIKI."],
  ["session_exists", "That did not work. Try again."],
  ["unavailable", "That did not work. Try again."],
];

test.each(REFUSALS)("create refused with %s says its words under the form", async (code, words) => {
  const api = stub([], { create: vi.fn().mockRejectedValue(new RootsApiError(code)) });
  await setup(api);
  await create("viki/x");
  expect(within(screen.getByRole("alert")).getByText(words)).toBeTruthy();
  // The button is usable again.
  expect(screen.getByRole("button", { name: "Create worktree" })).toBeTruthy();
});

test("an empty branch name shows the branch rule without asking the server", async () => {
  const api = stub();
  await setup(api);
  await create("");
  expect(screen.getByRole("alert").textContent).toBe(
    "Use letters, digits and . _ - / (up to 100).",
  );
  expect(api.create).not.toHaveBeenCalled();
});

test("the branch input stops at 100 characters", async () => {
  await setup(stub());
  expect(branchBox().maxLength).toBe(100);
});

test("a failure with no code says the plain words", async () => {
  const api = stub([], { create: vi.fn().mockRejectedValue(new Error("network")) });
  await setup(api);
  await create("viki/x");
  expect(screen.getByRole("alert").textContent).toBe("That did not work. Try again.");
});

test("a folder without a physical id disables the form and says why", async () => {
  await setup(stub(), { folder: folder(null) });
  expect(branchBox().disabled).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Create worktree" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(screen.getByText("This folder can't be changed now.")).toBeTruthy();
});

test("a branch name with markup stays text", async () => {
  const evil = "<script>alert(1)</script>";
  const api = stub([worktree({ branch: evil, path: "<b>p</b>" })]);
  await setup(api);
  expect(screen.getByText(evil)).toBeTruthy();
  expect(screen.getByText("<b>p</b>")).toBeTruthy();
  expect(document.querySelector("script, b")).toBeNull();
});

test("rows of this folder only, newest first, removed ones last and dimmed", async () => {
  const old = worktree({ branch: "viki/old", created_at: "2026-10-01T00:00:00Z" });
  const gone = worktree({
    branch: "viki/gone",
    state: "removed",
    created_at: "2026-10-04T00:00:00Z",
  });
  const fresh = worktree({ branch: "viki/fresh", created_at: "2026-10-02T00:00:00Z" });
  const elsewhere = worktree({ branch: "viki/elsewhere", workspace_id: OTHER });
  await setup(stub([old, gone, fresh, elsewhere]));
  const names = screen
    .getAllByRole("listitem")
    .map((li) => li.querySelector("strong")?.textContent);
  expect(names).toEqual(["viki/fresh", "viki/old", "viki/gone"]);
  expect(screen.getByText("viki/gone").closest("li")?.getAttribute("data-state")).toBe("removed");
  expect(screen.queryByText("viki/elsewhere")).toBeNull();
});

test("each state has its word and only a ready row starts a session", async () => {
  const rows = (["creating", "removing", "kept", "removed"] as const).map((state) =>
    worktree({ state, branch: `viki/${state}` }),
  );
  await setup(stub(rows));
  expect(rowOf("viki/creating").getByText("Creating")).toBeTruthy();
  expect(rowOf("viki/removing").getByText("Removing")).toBeTruthy();
  expect(rowOf("viki/kept").getByText("Kept: removal did not finish")).toBeTruthy();
  expect(rowOf("viki/removed").getByText("Removed")).toBeTruthy();
  expect(screen.queryAllByRole("button", { name: "New session here" })).toHaveLength(0);
  // A kept row offers Remove again; the others offer nothing.
  expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(1);
  expect(rowOf("viki/kept").getByRole("button", { name: "Remove" })).toBeTruthy();
});

test("New session here passes both ids and shows a returned refusal beside the button", async () => {
  const row = worktree({ branch: "viki/go" });
  const { onStartSession } = await setup(stub([row]), {
    start: async () => "too_many_sessions",
  });
  fireEvent.click(rowOf("viki/go").getByRole("button", { name: "New session here" }));
  await settle();
  expect(onStartSession).toHaveBeenCalledWith({
    workspace_id: W,
    worktree_id: row.worktree_id,
  });
  expect(rowOf("viki/go").getByRole("alert").textContent).toBe(
    "The most sessions allowed at once are running. Close one, or raise the limit in Settings › Chat.",
  );
  expect(rowOf("viki/go").queryByRole("button", { name: "Change limit" })).toBeNull();
});

test("a too_many_sessions refusal with onRaiseLimit shows Change limit after the alert", async () => {
  const row = worktree({ branch: "viki/cap" });
  const onRaiseLimit = vi.fn();
  await setup(stub([row]), { start: async () => "too_many_sessions", onRaiseLimit });
  fireEvent.click(rowOf("viki/cap").getByRole("button", { name: "New session here" }));
  await settle();
  const alert = rowOf("viki/cap").getByRole("alert");
  const button = rowOf("viki/cap").getByRole("button", { name: "Change limit" });
  expect(alert.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(button);
  expect(onRaiseLimit).toHaveBeenCalledTimes(1);
});

test("a refusal other than too_many_sessions shows no Change limit button, even with onRaiseLimit", async () => {
  const row = worktree({ branch: "viki/busy" });
  await setup(stub([row]), { start: async () => "worktree_in_use", onRaiseLimit: vi.fn() });
  fireEvent.click(rowOf("viki/busy").getByRole("button", { name: "New session here" }));
  await settle();
  expect(rowOf("viki/busy").getByRole("alert")).toBeTruthy();
  expect(rowOf("viki/busy").queryByRole("button", { name: "Change limit" })).toBeNull();
});

test("a started session shows no refusal", async () => {
  await setup(stub([worktree({ branch: "viki/go" })]));
  fireEvent.click(screen.getByRole("button", { name: "New session here" }));
  await settle();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a cancelled confirm calls nothing", async () => {
  const api = stub([worktree({ branch: "viki/keep" })]);
  await setup(api);
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  expect(document.activeElement).toBe(
    within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }),
  );
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
  await settle();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(api.remove).not.toHaveBeenCalled();
});

test("remove asks, then shows the kept outcome", async () => {
  const row = worktree({ branch: "viki/dirty" });
  const api = stub([row], {
    remove: vi
      .fn()
      .mockResolvedValue({ worktree: row, outcome: "kept", reason: "dirty", changes: 3 }),
  });
  await setup(api);
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  const dialog = screen.getByRole("alertdialog");
  expect(dialog.textContent).toContain(
    "This deletes the worktree's folder, including ignored files such as build output. The branch viki/dirty and its commits stay.",
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
  await settle();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(api.remove).toHaveBeenCalledWith(row.worktree_id, {
    policy_revision: 3,
    root_physical_id: "1:2",
  });
  expect(api.remove).toHaveBeenCalledTimes(1);
  const status = screen.getByRole("status");
  expect(status.textContent).toBe(
    "Not removed: 3 changed or new files. Commit or discard them first.",
  );
  expect(status.getAttribute("aria-live")).toBe("polite");
  // The row stays ready, so Remove is offered again.
  expect(rowOf("viki/dirty").getByText("Ready")).toBeTruthy();
});

test("a removed outcome updates the row and names the kept branch", async () => {
  const row = worktree({ branch: "viki/done" });
  const api = stub([row], {
    remove: vi.fn().mockImplementation(async () => {
      row.state = "removed";
      return { worktree: { ...row }, outcome: "removed", reason: null, changes: null };
    }),
  });
  await setup(api);
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
  await settle();
  expect(screen.getByRole("status").textContent).toBe(
    "Removed the worktree. The branch viki/done stays.",
  );
  expect(rowOf("viki/done").getByText("Removed")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Remove" })).toBeNull();
});

test.each([
  ["git_refused", 0],
  ["unknown_state", null],
])("a kept outcome with reason %s says Git did not remove it", async (reason, changes) => {
  const row = worktree({ branch: "viki/k" });
  const api = stub([row], {
    remove: vi
      .fn()
      .mockResolvedValue({ worktree: { ...row, state: "kept" }, outcome: "kept", reason, changes }),
  });
  await setup(api);
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
  await settle();
  expect(screen.getByRole("status").textContent).toBe(
    "Git did not remove the worktree. Check its folder, then try again.",
  );
});

test("a refused remove says its words in the live region", async () => {
  const api = stub([worktree({ branch: "viki/busy" })], {
    remove: vi.fn().mockRejectedValue(new RootsApiError("worktree_in_use")),
  });
  await setup(api);
  fireEvent.click(screen.getByRole("button", { name: "Remove" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove" }));
  await settle();
  expect(screen.getByRole("status").textContent).toBe(
    "A session is running in this worktree. Close it first.",
  );
});

test("a refresh runs on mount and after each action, and is aborted on unmount", async () => {
  const api = stub();
  const signals: (AbortSignal | undefined)[] = [];
  (api.worktrees as ReturnType<typeof vi.fn>).mockImplementation(async (signal?: AbortSignal) => {
    signals.push(signal);
    return { worktrees: [], truncated: false };
  });
  await setup(api);
  expect(signals).toHaveLength(1);
  await create("viki/r");
  expect(signals).toHaveLength(2);
  cleanup();
  expect(signals.every((signal) => signal?.aborted)).toBe(true);
});

test("a ready row offers Review only with onReview, and not a kept row", async () => {
  const ready = worktree({ branch: "viki/r" });
  const kept = worktree({ branch: "viki/k", state: "kept" });
  await setup(stub([ready, kept]));
  expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
  cleanup();
  const onReview = vi.fn();
  await setup(stub([ready, kept]), { onReview });
  expect(screen.getAllByRole("button", { name: "Review" })).toHaveLength(1);
  fireEvent.click(rowOf("viki/r").getByRole("button", { name: "Review" }));
  expect(onReview).toHaveBeenCalledWith(ready.worktree_id);
});

const archive = (over: Partial<ArchiveJson> = {}): ArchiveJson => ({
  worktree_id: "00000000-0000-4000-8000-0000000000a1",
  oid: OID,
  archived_at: "2026-10-20T00:00:00Z",
  ...over,
});

test("a removed row shows the Archived chip and Restore only when an archive exists for it", async () => {
  const gone = worktree({ branch: "viki/gone", state: "removed" });
  const bare = worktree({ branch: "viki/bare", state: "removed" });
  const live = worktree({ branch: "viki/live" });
  const api = stub([gone, bare, live], {
    archives: vi
      .fn()
      .mockResolvedValue({ enabled: true, archives: [archive({ worktree_id: gone.worktree_id })] }),
  });
  await setup(api);
  expect(api.archives).toHaveBeenCalledWith(W, expect.anything());
  // On by default: the full cleanup sentence shows while an archive exists.
  expect(screen.getByText(/^Idle worktrees are archived and removed/)).toBeTruthy();
  const goneRow = rowOf("viki/gone");
  expect(
    goneRow.getByText(`Archived ${new Date("2026-10-20T00:00:00Z").toLocaleString()}`),
  ).toBeTruthy();
  expect(goneRow.getByRole("button", { name: "Restore" })).toBeTruthy();
  expect(rowOf("viki/bare").queryByText(/^Archived /)).toBeNull();
  expect(rowOf("viki/bare").queryByRole("button", { name: "Restore" })).toBeNull();
  expect(rowOf("viki/live").queryByRole("button", { name: "Restore" })).toBeNull();
});

test("the cleanup sentence turns off when the setting is off", async () => {
  const api = stub([], { archives: vi.fn().mockResolvedValue({ enabled: false, archives: [] }) });
  await setup(api);
  expect(
    screen.getByText(
      "Automatic cleanup of idle worktrees is off; turn it on in Settings › General. Copies in use by a Bot chat are kept, and copies of deleted chats are still removed, or archived when they hold changes. A copy VIKI Agent can't safely clean up stays here, marked Released.",
    ),
  ).toBeTruthy();
});

test("a failed archives read shows no chip or Restore for a removed row", async () => {
  const gone = worktree({ branch: "viki/fail", state: "removed" });
  const api = stub([gone], {
    archives: vi.fn().mockRejectedValue(new RootsApiError("unavailable")),
  });
  await setup(api);
  expect(rowOf("viki/fail").queryByRole("button", { name: "Restore" })).toBeNull();
});

test("a rejecting archives read shows neither cleanup sentence nor an Archived chip", async () => {
  const gone = worktree({ branch: "viki/none", state: "removed" });
  const api = stub([gone], {
    archives: vi.fn().mockRejectedValue(new RootsApiError("unavailable")),
  });
  await setup(api);
  expect(screen.queryByText(/^Idle worktrees are archived and removed/)).toBeNull();
  expect(
    screen.queryByText(
      "Automatic cleanup of idle worktrees is off; turn it on in Settings › General. Copies in use by a Bot chat are kept, and copies of deleted chats are still removed, or archived when they hold changes. A copy VIKI Agent can't safely clean up stays here, marked Released.",
    ),
  ).toBeNull();
  expect(rowOf("viki/none").queryByText(/^Archived /)).toBeNull();
});

test("Restore sends both identity fields with no confirm and shows the restored message", async () => {
  const gone = worktree({ branch: "viki/r", state: "removed" });
  const api = stub([gone], {
    archives: vi
      .fn()
      .mockResolvedValue({ enabled: true, archives: [archive({ worktree_id: gone.worktree_id })] }),
    restore: vi.fn().mockImplementation(async () => {
      gone.state = "ready";
      return { ...gone };
    }),
  });
  await setup(api);
  fireEvent.click(rowOf("viki/r").getByRole("button", { name: "Restore" }));
  await settle();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(api.restore).toHaveBeenCalledWith(gone.worktree_id, {
    policy_revision: 3,
    root_physical_id: "1:2",
  });
  expect(screen.getByRole("status").textContent).toBe(
    "Restored the worktree with its uncommitted changes.",
  );
  expect(rowOf("viki/r").getByText("Ready")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Restore" })).toBeNull();
});

const RESTORE_REFUSALS: [string, string][] = [
  ["no_archive", "This worktree has no archive to restore."],
  ["branch_moved", "Its branch has moved since the archive. Restore it with Git by hand."],
  ["branch_in_use", "Its branch is checked out in another worktree."],
];

test.each(RESTORE_REFUSALS)("a restore refused with %s says its words", async (code, words) => {
  const gone = worktree({ branch: "viki/x", state: "removed" });
  const api = stub([gone], {
    archives: vi
      .fn()
      .mockResolvedValue({ enabled: true, archives: [archive({ worktree_id: gone.worktree_id })] }),
    restore: vi.fn().mockRejectedValue(new RootsApiError(code)),
  });
  await setup(api);
  fireEvent.click(rowOf("viki/x").getByRole("button", { name: "Restore" }));
  await settle();
  expect(rowOf("viki/x").getByRole("alert").textContent).toBe(words);
  // The row stays removed with its archive, so Restore is offered again.
  expect(rowOf("viki/x").getByRole("button", { name: "Restore" })).toBeTruthy();
});

test("Preview cleanup shows Checking…, then the folder's decisions in words", async () => {
  const row = worktree({ branch: "viki/p" });
  let resolveCleanup!: (value: DecisionJson[]) => void;
  const api = stub([row], {
    cleanup: vi.fn().mockReturnValue(
      new Promise<DecisionJson[]>((resolve) => {
        resolveCleanup = resolve;
      }),
    ),
  });
  await setup(api);
  fireEvent.click(screen.getByRole("button", { name: "Preview cleanup" }));
  await settle();
  expect(api.cleanup).toHaveBeenCalledWith(W, expect.anything());
  const busy = screen.getByRole("button", { name: "Checking…" }) as HTMLButtonElement;
  expect(busy.disabled).toBe(true);
  await act(async () =>
    resolveCleanup([
      {
        worktree_id: row.worktree_id,
        action: "archive",
        reason: "dirty",
        idle_since: "2026-10-01T00:00:00Z",
        eligible_at: null,
        bytes: 2048,
      },
      {
        worktree_id: "00000000-0000-4000-8000-00000000009a",
        action: "keep",
        reason: "recent",
        idle_since: "2026-10-19T00:00:00Z",
        eligible_at: "2026-10-22T00:00:00Z",
        bytes: null,
      },
    ]),
  );
  expect(screen.getByRole("button", { name: "Preview cleanup" }) as HTMLButtonElement).toBeTruthy();
  const decisions = within(document.querySelector(".worktrees-decisions") as HTMLElement);
  expect(decisions.getByText("viki/p")).toBeTruthy();
  expect(decisions.getByText(/Will be archived, then removed/)).toBeTruthy();
  expect(decisions.getByText(/It has uncommitted changes\./)).toBeTruthy();
  expect(decisions.getByText(/2\.0 KB/)).toBeTruthy();
  // A row with no matching branch (stale data) falls back to its id rather than crashing.
  expect(decisions.getByText("00000000-0000-4000-8000-00000000009a")).toBeTruthy();
  expect(decisions.getByText(/Stays/)).toBeTruthy();
  expect(
    decisions.getByText(
      `Changed recently; eligible from ${new Date("2026-10-22T00:00:00Z").toLocaleString()}.`,
      { exact: false },
    ),
  ).toBeTruthy();
});

test("Preview cleanup says there is nothing to clean up when the list is empty", async () => {
  const api = stub([], { cleanup: vi.fn().mockResolvedValue([]) });
  await setup(api);
  fireEvent.click(screen.getByRole("button", { name: "Preview cleanup" }));
  await settle();
  expect(screen.getByText("Nothing to clean up.")).toBeTruthy();
});

// --- Bot copies ----------------------------------------------------------------------------

const botCopy = (over: Partial<NonNullable<WorktreeJson["bot_copy"]>> = {}) => ({
  kind: "session" as const,
  bot_name: "Ada",
  title: "Fix tests",
  released: false,
  ...over,
});
const text = (node: HTMLElement) => node.textContent ?? "";

test.each([
  ["session", botCopy(), "Bot copy: Ada · Fix tests"],
  ["task", botCopy({ kind: "task", title: null }), "Bot copy: Ada"],
  ["thread", botCopy({ kind: "thread", title: "Review plan" }), "Bot thread copy: Review plan"],
])(
  "a %s copy names its Bot or thread, hides New session here and says why",
  async (_, bot, words) => {
    const row = worktree({ branch: "viki/bot", bot_copy: bot });
    const plain = worktree({ branch: "viki/plain" });
    await setup(stub([row, plain]));
    const botRow = rowOf("viki/bot");
    expect(botRow.getByText(words)).toBeTruthy();
    expect(botRow.queryByRole("button", { name: "New session here" })).toBeNull();
    expect(botRow.getByRole("button", { name: "Remove" })).toBeTruthy();
    expect(botRow.queryByText("Released")).toBeNull();
    expect(rowOf("viki/plain").getByRole("button", { name: "New session here" })).toBeTruthy();
    expect(rowOf("viki/plain").queryByText(/^Bot (thread )?copy/)).toBeNull();
  },
);

test("a released copy says Released and still offers no session", async () => {
  const row = worktree({ branch: "viki/rel", bot_copy: botCopy({ released: true }) });
  await setup(stub([row]));
  expect(rowOf("viki/rel").getByText("Released")).toBeTruthy();
  expect(rowOf("viki/rel").queryByRole("button", { name: "New session here" })).toBeNull();
});

test("an unknown Bot or thread name shows no label and never the id", async () => {
  const rows = [
    worktree({ branch: "viki/nobot", bot_copy: botCopy({ bot_name: null }) }),
    worktree({ branch: "viki/notitle", bot_copy: botCopy({ kind: "thread", title: null }) }),
  ];
  await setup(stub(rows));
  for (const row of rows) {
    const li = screen.getByText(row.branch).closest("li") as HTMLElement;
    expect(text(li)).not.toMatch(/Bot (thread )?copy/);
    expect(text(li)).not.toContain(row.worktree_id);
    expect(within(li).queryByRole("button", { name: "New session here" })).toBeNull();
  }
});

test("Bot and chat names with markup stay text", async () => {
  const evil = '<img src=x onerror="alert(1)">';
  const row = worktree({
    branch: "viki/evil",
    bot_copy: botCopy({ bot_name: evil, title: "<b>x</b>" }),
  });
  await setup(stub([row]));
  expect(screen.getByText(`Bot copy: ${evil} · <b>x</b>`)).toBeTruthy();
  expect(document.querySelector("img, b")).toBeNull();
});

test("Removing a Bot copy adds the consequence to the confirm; another row's does not", async () => {
  const rows = [
    worktree({ branch: "viki/bot", bot_copy: botCopy() }),
    worktree({ branch: "viki/plain" }),
  ];
  const api = stub(rows);
  await setup(api);
  const warning = EN["worktrees.bot.remove_warning"];
  fireEvent.click(rowOf("viki/bot").getByRole("button", { name: "Remove" }));
  const dialog = screen.getByRole("alertdialog");
  expect(text(dialog)).toContain(warning);
  expect(text(dialog)).toContain("The branch viki/bot and its commits stay.");
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  fireEvent.click(rowOf("viki/plain").getByRole("button", { name: "Remove" }));
  expect(text(screen.getByRole("alertdialog"))).not.toContain(warning);
  expect(api.remove).not.toHaveBeenCalled();
});

test("a cleanup decision of bot_chat says the copy is in use", async () => {
  const row = worktree({ branch: "viki/bot", bot_copy: botCopy() });
  const decision: DecisionJson = {
    worktree_id: row.worktree_id,
    action: "keep",
    reason: "bot_chat",
    idle_since: null,
    eligible_at: null,
    bytes: null,
  };
  await setup(stub([row], { cleanup: vi.fn().mockResolvedValue([decision]) }));
  fireEvent.click(screen.getByRole("button", { name: "Preview cleanup" }));
  await settle();
  const list = within(document.querySelector(".worktrees-decisions") as HTMLElement);
  expect(list.getByText(/In use by a Bot chat/)).toBeTruthy();
});

test("a Bot copy's folder refusal on starting a session has the catalog words", async () => {
  await setup(stub([worktree({ branch: "viki/go" })]), { start: async () => "bot_copy_root" });
  fireEvent.click(screen.getByRole("button", { name: "New session here" }));
  await settle();
  expect(rowOf("viki/go").getByRole("alert").textContent).toBe(
    "This worktree belongs to a Bot chat. Open that chat instead.",
  );
});

// --- Git settings ---------------------------------------------------------------------------

const SAVED = "ab".repeat(32);
const CURRENT = "cd".repeat(32);
const CHANGED_WORDS = EN["worktrees.refusal.git_settings_changed"];
const changedError = () => new RootsApiError("git_settings_changed");

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

/** A stub whose Create is refused for changed Git settings, with the two new methods. */
function settingsStub(over: Partial<RootsApi> = {}, held: WorktreeJson[] = []) {
  return stub(held, {
    create: vi.fn().mockRejectedValue(changedError()),
    gitSettings: vi.fn().mockResolvedValue(SAVED),
    confirmGitSettings: vi.fn().mockResolvedValue(CURRENT),
    ...over,
  });
}
const confirmButton = () => screen.getByRole("button", { name: "Confirm the current settings" });
const dialogButton = (name: string) =>
  within(screen.getByRole("alertdialog")).getByRole("button", { name });
const openDialog = async () => {
  fireEvent.click(confirmButton());
  await settle();
};

test("a refused Create shows the folder's settings sentence once, with the way out", async () => {
  const api = settingsStub();
  await setup(api);
  await create("viki/x");
  expect(screen.getAllByText(CHANGED_WORDS)).toHaveLength(1);
  expect(screen.getByRole("alert").textContent).toBe(CHANGED_WORDS);
  expect(confirmButton()).toBeTruthy();
  // Nothing is read or sent until the button is clicked.
  expect(api.gitSettings).not.toHaveBeenCalled();
  expect(api.confirmGitSettings).not.toHaveBeenCalled();
});

test("without the two client methods the sentence shows and no confirm is offered", async () => {
  await setup(stub([], { create: vi.fn().mockRejectedValue(changedError()) }));
  await create("viki/x");
  expect(screen.getByRole("alert").textContent).toBe(CHANGED_WORDS);
  expect(screen.queryByRole("button", { name: "Confirm the current settings" })).toBeNull();
  cleanup();
  await setup(
    stub([], {
      create: vi.fn().mockRejectedValue(changedError()),
      gitSettings: vi.fn().mockResolvedValue(SAVED),
    }),
  );
  await create("viki/x");
  expect(screen.queryByRole("button", { name: "Confirm the current settings" })).toBeNull();
});

test("a settings refusal on the opening archives read shows the way out without a click", async () => {
  await setup(settingsStub({ archives: vi.fn().mockRejectedValue(changedError()) }));
  expect(screen.getByRole("alert").textContent).toBe(CHANGED_WORDS);
  expect(confirmButton()).toBeTruthy();
});

test("another refusal on the opening archives read offers no confirm", async () => {
  await setup(
    settingsStub({ archives: vi.fn().mockRejectedValue(new RootsApiError("unavailable")) }),
  );
  expect(screen.queryByRole("button", { name: "Confirm the current settings" })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

const gone = () => worktree({ branch: "viki/gone", state: "removed" });
const live = () => worktree({ branch: "viki/live" });
test.each([
  [
    "Remove",
    async () => {
      const row = live();
      const api = settingsStub({ remove: vi.fn().mockRejectedValue(changedError()) }, [row]);
      await setup(api);
      fireEvent.click(screen.getByRole("button", { name: "Remove" }));
      fireEvent.click(dialogButton("Remove"));
      await settle();
    },
  ],
  [
    "New session here",
    async () => {
      await setup(settingsStub({}, [live()]), { start: async () => "git_settings_changed" });
      fireEvent.click(screen.getByRole("button", { name: "New session here" }));
      await settle();
    },
  ],
  [
    "Restore",
    async () => {
      const row = gone();
      const api = settingsStub(
        {
          restore: vi.fn().mockRejectedValue(changedError()),
          archives: vi.fn().mockResolvedValue({
            enabled: true,
            archives: [
              { worktree_id: row.worktree_id, oid: OID, archived_at: "2026-10-20T00:00:00Z" },
            ],
          }),
        },
        [row],
      );
      await setup(api);
      fireEvent.click(screen.getByRole("button", { name: "Restore" }));
      await settle();
    },
  ],
  [
    "Preview cleanup",
    async () => {
      await setup(settingsStub({ cleanup: vi.fn().mockRejectedValue(changedError()) }));
      fireEvent.click(screen.getByRole("button", { name: "Preview cleanup" }));
      await settle();
    },
  ],
])("a %s refused for changed settings shows the sentence once, with the button", async (_, run) => {
  await run();
  expect(screen.getAllByText(CHANGED_WORDS)).toHaveLength(1);
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(confirmButton()).toBeTruthy();
});

test("the next action clears the settings sentence until it is refused again", async () => {
  const made = worktree({ branch: "viki/ok" });
  const refused = vi.fn().mockRejectedValueOnce(changedError()).mockResolvedValueOnce(made);
  await setup(settingsStub({ create: refused }));
  await create("viki/x");
  expect(screen.queryAllByText(CHANGED_WORDS)).toHaveLength(1);
  await create("viki/y");
  expect(screen.queryByText(CHANGED_WORDS)).toBeNull();
  expect(screen.queryByRole("button", { name: "Confirm the current settings" })).toBeNull();
});

test("the button reads the saved hash, then asks in a dialog with Cancel focused", async () => {
  const api = settingsStub();
  await setup(api);
  await create("viki/x");
  await openDialog();
  expect(api.gitSettings).toHaveBeenCalledWith(W, expect.any(AbortSignal));
  expect(api.confirmGitSettings).not.toHaveBeenCalled();
  const dialog = screen.getByRole("alertdialog");
  expect(dialog.getAttribute("aria-label")).toBe("Confirm the current settings");
  expect(text(dialog)).toContain(EN["worktrees.git_settings.confirm_body"]);
  // Nothing says this cannot be undone.
  expect(dialog.querySelector(".confirm-dialog-irreversible")).toBeNull();
  expect(document.activeElement).toBe(dialogButton("Cancel"));
});

test("Cancel sends nothing, closes the dialog and keeps the way out", async () => {
  const api = settingsStub();
  await setup(api);
  await create("viki/x");
  await openDialog();
  fireEvent.click(dialogButton("Cancel"));
  await settle();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(api.confirmGitSettings).not.toHaveBeenCalled();
  expect(screen.getByText(CHANGED_WORDS)).toBeTruthy();
  expect(confirmButton()).toBeTruthy();
});

test("Confirm sends the reviewed hash with both identity fields, then refreshes", async () => {
  const api = settingsStub();
  await setup(api);
  await create("viki/x");
  const roots = vi.mocked(api.worktrees).mock.calls.length;
  const archives = vi.mocked(api.archives).mock.calls.length;
  await openDialog();
  fireEvent.click(dialogButton("Confirm the current settings"));
  await settle();
  expect(api.confirmGitSettings).toHaveBeenCalledTimes(1);
  expect(api.confirmGitSettings).toHaveBeenCalledWith(
    W,
    { policy_revision: 3, root_physical_id: "1:2", expected_fingerprint: SAVED },
    expect.any(AbortSignal),
  );
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByText(CHANGED_WORDS)).toBeNull();
  expect(screen.getByRole("status").textContent).toBe("The current settings are confirmed.");
  expect(vi.mocked(api.worktrees).mock.calls.length).toBeGreaterThan(roots);
  expect(vi.mocked(api.archives).mock.calls.length).toBeGreaterThan(archives);
});

test("while it confirms, the dialog is busy, Cancel does nothing and a second click sends nothing", async () => {
  const pending = deferred<string>();
  const api = settingsStub({ confirmGitSettings: vi.fn().mockReturnValue(pending.promise) });
  await setup(api);
  await create("viki/x");
  await openDialog();
  fireEvent.click(dialogButton("Confirm the current settings"));
  await settle();
  const busy = dialogButton("Confirming…");
  expect(busy.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(busy);
  fireEvent.click(dialogButton("Cancel"));
  await settle();
  expect(api.confirmGitSettings).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("alertdialog")).toBeTruthy();
  // The way out behind the dialog is disabled too.
  expect(
    (screen.getByRole("button", { name: "Confirm the current settings" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await act(async () => pending.resolve(CURRENT));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(api.confirmGitSettings).toHaveBeenCalledTimes(1);
});

test("a stale confirm (409) closes the dialog, says it plainly and is not retried", async () => {
  const api = settingsStub({
    confirmGitSettings: vi.fn().mockRejectedValue(new RootsApiError("baseline_changed")),
  });
  await setup(api);
  await create("viki/x");
  await openDialog();
  fireEvent.click(dialogButton("Confirm the current settings"));
  await settle();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(api.confirmGitSettings).toHaveBeenCalledTimes(1);
  // The folder is still refused; the failure is a second alert and the button is usable again.
  const alerts = screen.getAllByRole("alert").map(text);
  expect(alerts).toEqual([CHANGED_WORDS, "That did not work. Try again."]);
  expect((confirmButton() as HTMLButtonElement).disabled).toBe(false);
  // A new try reads the hash again rather than reusing the stale one.
  vi.mocked(api.gitSettings as NonNullable<RootsApi["gitSettings"]>).mockResolvedValue(CURRENT);
  await openDialog();
  expect(api.gitSettings).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("That did not work. Try again.")).toBeNull();
  expect(screen.getByRole("alertdialog")).toBeTruthy();
});

test.each(["git_settings_unavailable", "trust_required", "workspace_changed", "unavailable"])(
  "a confirm failing with %s shows its words beside the way out and changes nothing else",
  async (code) => {
    const api = settingsStub({
      confirmGitSettings: vi.fn().mockRejectedValue(new RootsApiError(code)),
    });
    await setup(api);
    await create("viki/x");
    await openDialog();
    fireEvent.click(dialogButton("Confirm the current settings"));
    await settle();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getAllByRole("alert")).toHaveLength(2);
    expect(screen.getByText(CHANGED_WORDS)).toBeTruthy();
    expect(screen.queryByText("The current settings are confirmed.")).toBeNull();
  },
);

test("no saved baseline or a failed read opens no dialog and says so", async () => {
  const api = settingsStub({ gitSettings: vi.fn().mockResolvedValue(null) });
  await setup(api);
  await create("viki/x");
  await openDialog();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(api.confirmGitSettings).not.toHaveBeenCalled();
  expect(screen.getAllByRole("alert")).toHaveLength(2);
  cleanup();
  const failing = settingsStub({ gitSettings: vi.fn().mockRejectedValue(new Error("network")) });
  await setup(failing);
  await create("viki/x");
  await openDialog();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(failing.confirmGitSettings).not.toHaveBeenCalled();
  expect(screen.getByText("That did not work. Try again.")).toBeTruthy();
});

test("a read in flight cannot be repeated, and its button says Confirming…", async () => {
  const read = deferred<string | null>();
  const api = settingsStub({ gitSettings: vi.fn().mockReturnValue(read.promise) });
  await setup(api);
  await create("viki/x");
  fireEvent.click(confirmButton());
  await settle();
  const busy = screen.getByRole("button", { name: "Confirming…" }) as HTMLButtonElement;
  expect(busy.disabled).toBe(true);
  fireEvent.click(busy);
  expect(api.gitSettings).toHaveBeenCalledTimes(1);
  await act(async () => read.resolve(SAVED));
  expect(screen.getByRole("alertdialog")).toBeTruthy();
});

function renderWithProps(api: RootsApi, folderProp: WorkspaceJson) {
  const props = { onStartSession: async () => null };
  const view = render(<Worktrees api={api} folder={folderProp} {...props} />);
  return (nextApi: RootsApi, nextFolder: WorkspaceJson) =>
    view.rerender(<Worktrees api={nextApi} folder={nextFolder} {...props} />);
}

test.each(["folder", "api"])(
  "an answer that lands after the %s changed updates nothing on screen",
  async (what) => {
    const pending = deferred<string>();
    const api = settingsStub({ confirmGitSettings: vi.fn().mockReturnValue(pending.promise) });
    const rerender = renderWithProps(api, folder());
    await settle();
    await create("viki/x");
    await openDialog();
    fireEvent.click(dialogButton("Confirm the current settings"));
    await settle();
    expect(api.confirmGitSettings).toHaveBeenCalledWith(W, expect.anything(), expect.anything());
    const other = { ...folder(), workspace_id: OTHER } as WorkspaceJson;
    const otherApi = stub();
    if (what === "folder") rerender(api, other);
    else rerender(otherApi, folder());
    await settle();
    const reads = vi.mocked(what === "folder" ? api.worktrees : otherApi.worktrees).mock.calls
      .length;
    await act(async () => pending.resolve(CURRENT));
    expect(screen.queryByText("The current settings are confirmed.")).toBeNull();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(
      vi.mocked(what === "folder" ? api.worktrees : otherApi.worktrees).mock.calls.length,
    ).toBe(reads);
    expect(api.confirmGitSettings).toHaveBeenCalledTimes(1);
  },
);

test("leaving the tab aborts the read and the confirm", async () => {
  const signals: (AbortSignal | undefined)[] = [];
  const pending = deferred<string>();
  const api = settingsStub({
    gitSettings: vi.fn().mockImplementation(async (_w: string, signal?: AbortSignal) => {
      signals.push(signal);
      return SAVED;
    }),
    confirmGitSettings: vi
      .fn()
      .mockImplementation((_w: string, _b: unknown, signal?: AbortSignal) => {
        signals.push(signal);
        return pending.promise;
      }),
  });
  await setup(api);
  await create("viki/x");
  await openDialog();
  fireEvent.click(dialogButton("Confirm the current settings"));
  await settle();
  expect(signals).toHaveLength(2);
  expect(signals.every((signal) => signal && !signal.aborted)).toBe(true);
  cleanup();
  expect(signals.every((signal) => signal?.aborted)).toBe(true);
  await act(async () => pending.reject(new Error("aborted")));
});

// --- Archived changes -----------------------------------------------------------------------

const A1 = "00000000-0000-4000-8000-0000000000c1";
const A2 = "00000000-0000-4000-8000-0000000000c2";
const date = (iso: string) => new Date(iso).toLocaleString();

test("archives show with an empty active list, by date, and never by id", async () => {
  const api = stub([], {
    archives: vi.fn().mockResolvedValue({
      enabled: true,
      archives: [
        archive({ worktree_id: A1, archived_at: "2026-10-01T00:00:00Z" }),
        archive({ worktree_id: A2, archived_at: "2026-10-20T00:00:00Z" }),
      ],
    }),
  });
  await setup(api);
  const section = within(screen.getByRole("region", { name: "Archived changes" }));
  expect(section.getByText("Archived changes")).toBeTruthy();
  const buttons = section.getAllByRole("button");
  // Newest first, each named by its date.
  expect(buttons.map((button) => button.getAttribute("aria-label"))).toEqual([
    `Restore archived changes from ${date("2026-10-20T00:00:00Z")}`,
    `Restore archived changes from ${date("2026-10-01T00:00:00Z")}`,
  ]);
  expect(section.getByText(`Archived ${date("2026-10-20T00:00:00Z")}`)).toBeTruthy();
  expect(text(screen.getByRole("region", { name: "Archived changes" }))).not.toMatch(/0000000/);
  // The cleanup sentence and its absence of rows do not hide the archives.
  expect(screen.getByText(/^Idle worktrees are archived and removed/)).toBeTruthy();
});

test("no section without archives, with a failed read, or with an off cleanup and none", async () => {
  await setup(stub());
  expect(screen.queryByRole("region", { name: "Archived changes" })).toBeNull();
  cleanup();
  await setup(stub([], { archives: vi.fn().mockRejectedValue(new RootsApiError("unavailable")) }));
  expect(screen.queryByRole("region", { name: "Archived changes" })).toBeNull();
});

test("an archive with cleanup off still lists and restores", async () => {
  const api = stub([], {
    archives: vi
      .fn()
      .mockResolvedValue({ enabled: false, archives: [archive({ worktree_id: A1 })] }),
  });
  await setup(api);
  expect(screen.getAllByRole("button", { name: /^Restore archived changes from / })).toHaveLength(
    1,
  );
});

test("an archive a listed removed row already shows is not listed twice", async () => {
  const row = worktree({ branch: "viki/gone", state: "removed" });
  const api = stub([row], {
    archives: vi.fn().mockResolvedValue({
      enabled: true,
      archives: [archive({ worktree_id: row.worktree_id }), archive({ worktree_id: A1 })],
    }),
  });
  await setup(api);
  expect(rowOf("viki/gone").getByRole("button", { name: "Restore" })).toBeTruthy();
  expect(screen.getAllByRole("button", { name: /^Restore archived changes from / })).toHaveLength(
    1,
  );
  expect(screen.getAllByRole("button", { name: /^Restore/ })).toHaveLength(2);
});

test("restoring an archive sends its worktree id and identity, then reads archives and roots again", async () => {
  const restored = worktree({ branch: "viki/back", workspace_id: W });
  const archives = vi
    .fn()
    .mockResolvedValueOnce({ enabled: true, archives: [archive({ worktree_id: A1 })] })
    .mockResolvedValue({ enabled: true, archives: [] });
  const held: WorktreeJson[] = [];
  const api = stub(held, {
    archives,
    restore: vi.fn().mockImplementation(async () => {
      held.unshift(restored);
      return restored;
    }),
  });
  await setup(api);
  const roots = vi.mocked(api.worktrees).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: /^Restore archived changes from / }));
  await settle();
  expect(api.restore).toHaveBeenCalledTimes(1);
  expect(api.restore).toHaveBeenCalledWith(A1, { policy_revision: 3, root_physical_id: "1:2" });
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.getByRole("status").textContent).toBe(
    "Restored the worktree with its uncommitted changes.",
  );
  // The row came from the answer and the roots; the archive is gone after the second read.
  expect(rowOf("viki/back").getByText("Ready")).toBeTruthy();
  expect(archives).toHaveBeenCalledTimes(2);
  expect(vi.mocked(api.worktrees).mock.calls.length).toBeGreaterThan(roots);
  expect(screen.queryByRole("region", { name: "Archived changes" })).toBeNull();
});

test("a restored copy that no Bot uses is not labelled as a Bot's", async () => {
  const restored = worktree({ branch: "viki/former" });
  const api = stub([restored], {
    archives: vi
      .fn()
      .mockResolvedValue({ enabled: true, archives: [archive({ worktree_id: A1 })] }),
    restore: vi.fn().mockResolvedValue(restored),
  });
  await setup(api);
  fireEvent.click(screen.getByRole("button", { name: /^Restore archived changes from / }));
  await settle();
  const row = rowOf("viki/former");
  expect(text(screen.getByText("viki/former").closest("li") as HTMLElement)).not.toMatch(
    /Bot (thread )?copy|Released/,
  );
  expect(row.getByRole("button", { name: "New session here" })).toBeTruthy();
});

test.each([
  ["no_archive", "This worktree has no archive to restore."],
  ["branch_moved", "Its branch has moved since the archive. Restore it with Git by hand."],
  ["branch_in_use", "Its branch is checked out in another worktree."],
  ["unavailable", "That did not work. Try again."],
])(
  "an archive restore refused with %s says its words beside that archive only",
  async (code, words) => {
    const api = stub([], {
      archives: vi.fn().mockResolvedValue({
        enabled: true,
        archives: [
          archive({ worktree_id: A1 }),
          archive({ worktree_id: A2, archived_at: "2026-10-01T00:00:00Z" }),
        ],
      }),
      restore: vi.fn().mockRejectedValue(new RootsApiError(code)),
    });
    await setup(api);
    fireEvent.click(
      screen.getByRole("button", {
        name: `Restore archived changes from ${date("2026-10-20T00:00:00Z")}`,
      }),
    );
    await settle();
    const alerts = screen.getAllByRole("alert");
    expect(alerts.map(text)).toEqual([words]);
    expect(alerts[0]?.closest("li")?.textContent).toContain(date("2026-10-20T00:00:00Z"));
    // Still offered, still named by its date.
    expect(screen.getAllByRole("button", { name: /^Restore archived changes from / })).toHaveLength(
      2,
    );
  },
);

test("only one restore runs at a time and a folder without a physical id offers none", async () => {
  const pending = deferred<WorktreeJson>();
  const api = stub([], {
    archives: vi.fn().mockResolvedValue({
      enabled: true,
      archives: [
        archive({ worktree_id: A1 }),
        archive({ worktree_id: A2, archived_at: "2026-10-01T00:00:00Z" }),
      ],
    }),
    restore: vi.fn().mockReturnValue(pending.promise),
  });
  await setup(api);
  const [first, second] = screen.getAllByRole("button", {
    name: /^Restore archived changes from /,
  });
  fireEvent.click(first as HTMLElement);
  await settle();
  expect(text(first as HTMLElement)).toBe("Restoring…");
  expect((second as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(second as HTMLElement);
  expect(api.restore).toHaveBeenCalledTimes(1);
  await act(async () => pending.reject(new RootsApiError("no_archive")));
  cleanup();
  await setup(api, { folder: folder(null) });
  for (const button of screen.getAllByRole("button", { name: /^Restore archived changes from / }))
    expect((button as HTMLButtonElement).disabled).toBe(true);
});

test("an archives read that finishes after a newer one does not replace it", async () => {
  const slow = deferred<{ enabled: boolean; archives: ArchiveJson[] }>();
  const archives = vi
    .fn()
    .mockReturnValueOnce(slow.promise)
    .mockResolvedValue({ enabled: true, archives: [] });
  await setup(settingsStub({ archives }));
  await create("viki/x");
  await openDialog();
  fireEvent.click(dialogButton("Confirm the current settings"));
  await settle();
  expect(archives).toHaveBeenCalledTimes(2);
  await act(async () => slow.resolve({ enabled: true, archives: [archive({ worktree_id: A1 })] }));
  expect(screen.queryByRole("region", { name: "Archived changes" })).toBeNull();
});

test("the archives read is aborted on unmount", async () => {
  const signals: (AbortSignal | undefined)[] = [];
  const api = stub([], {
    archives: vi.fn().mockImplementation(async (_w: string, signal?: AbortSignal) => {
      signals.push(signal);
      return { enabled: true, archives: [] };
    }),
  });
  await setup(api);
  expect(signals).toHaveLength(1);
  cleanup();
  expect(signals[0]?.aborted).toBe(true);
});

test("an archive date is text even beside markup in a listed branch", async () => {
  const evil = "<script>alert(1)</script>";
  const api = stub([worktree({ branch: evil })], {
    archives: vi
      .fn()
      .mockResolvedValue({ enabled: true, archives: [archive({ worktree_id: A1 })] }),
  });
  await setup(api);
  expect(screen.getByText(evil)).toBeTruthy();
  expect(document.querySelector("script")).toBeNull();
});
