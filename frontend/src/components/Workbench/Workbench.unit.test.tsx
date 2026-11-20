import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { WorkspaceJson } from "../../clients/app/shellApi";
import type { ChecksApi } from "../../clients/code/checksApi";
import { type CodeApi, CodeApiError, type CodeFile } from "../../clients/code/codeApi";
import type { PreviewsApi } from "../../clients/code/previewsApi";
import type { ReviewApi } from "../../clients/code/reviewApi";
import { RootsApiError, type WorktreeJson } from "../../clients/code/rootsApi";
import type { TerminalApi } from "../../clients/code/terminalApi";
import { dirtyRefs, dropBuffer, getBuffer, putBuffer } from "../../state/codeBuffers";
import { resetRoots } from "../../state/sessionRoots";
import type { EditorOptions } from "./codemirror";
import {
  Workbench,
  type WorkbenchChecks,
  type WorkbenchProps,
  type WorkbenchReview,
  type WorkbenchRoots,
} from "./Workbench";

// The real editor is a lazy chunk and needs layout; a textarea stands in for it.
vi.mock("./codemirror", () => ({
  createEditor: (options: EditorOptions) => {
    const area = document.createElement("textarea");
    area.setAttribute("aria-label", options.label);
    area.value = options.text;
    area.addEventListener("input", () => options.onDirty(area.value !== options.base));
    options.parent.append(area);
    return Promise.resolve({
      text: () => area.value,
      setText: (text: string) => {
        area.value = text;
      },
      setBase: () => {},
      setWrap: () => {},
      setFontSize: () => {},
      focus: () => {},
      destroy: () => area.remove(),
    });
  },
}));

const A = "00000000-0000-4000-8000-00000000000a";
const B = "00000000-0000-4000-8000-00000000000b";

afterEach(() => {
  cleanup();
  resetRoots();
  // A buffer left by one test would arm the next one's editor.
  for (const id of [A, B]) for (const ref of dirtyRefs(id)) dropBuffer(id, ref);
});

const folder = (id: string, name: string, policy = "ordinary", rootId: string | null = "r1") =>
  ({
    workspace_id: id,
    kind: "code",
    name,
    policy,
    policy_revision: 3,
    root_physical_id: rootId ?? undefined,
    root: "x",
    root_available: true,
  }) as WorkspaceJson;

const file = (path: string, text = "one\ntwo\nthree", over: Partial<CodeFile> = {}): CodeFile => ({
  path,
  size: text.length,
  sha256: "h1",
  encoding: "utf-8",
  newline: "lf",
  text,
  reason: null,
  ...over,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((a) => {
    resolve = a;
  });
  return { promise, resolve };
}

function fakeApi(over: Partial<CodeApi> = {}): CodeApi {
  return {
    tree: vi.fn().mockResolvedValue({
      path: "",
      truncated: false,
      entries: [{ name: "a.ts", type: "file", size: 3 }],
    }),
    file: vi.fn().mockImplementation(async (_w: string, p: string) => file(p)),
    search: vi.fn().mockResolvedValue({ hits: [], truncated: false }),
    changes: vi.fn().mockResolvedValue({
      state: "ready",
      branch: "main",
      head: "abcdef123456",
      detached: false,
      upstream: null,
      ahead: null,
      behind: null,
      truncated: false,
      entries: [
        {
          path: "c.ts",
          orig_path: null,
          index: ".",
          worktree: "M",
          kind: "tracked",
          private: false,
        },
      ],
    }),
    diff: vi
      .fn()
      .mockResolvedValue({ path: "c.ts", text: "@@ -1 +1 @@\n+added line", reason: null }),
    save: vi.fn(),
    ...over,
  };
}

function setup(
  over: Partial<CodeApi> = {},
  folders = [folder(A, "Alpha"), folder(B, "Beta")],
  activeId: string | null = A,
  extra: Partial<WorkbenchProps> = {},
) {
  const api = fakeApi(over);
  const props = {
    api,
    folders,
    activeId,
    onQuote: vi.fn(),
    onOpenInEditor: vi.fn(),
    onOpenSettings: vi.fn(),
    onBack: vi.fn(),
    fontSize: 13,
    lineWrap: false,
    ...extra,
  };
  const { container } = render(<Workbench {...props} />);
  return { ...props, container };
}
const settle = () => act(async () => {});
const pickFile = async () => {
  await settle();
  fireEvent.click(
    within(screen.getByRole("tabpanel", { name: "Files" })).getByRole("button", {
      name: "a.ts",
    }),
  );
  await settle();
};

test("heading takes focus and Back to chat calls onBack", () => {
  const { onBack } = setup();
  expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1, name: "Code" }));
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(onBack).toHaveBeenCalled();
});

test("the active folder is listed first, with a trust chip", () => {
  setup({}, [folder(A, "Alpha", "protected"), folder(B, "Beta")], B);
  const options = within(screen.getByLabelText("Code folder")).getAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual(["Beta", "Alpha"]);
  expect(screen.getByText("Trusted")).toBeTruthy();
});

test("a non-ordinary folder shows Not trusted and disables quick open", async () => {
  setup({}, [folder(A, "Alpha", "existing_readonly")]);
  expect(screen.getByText("Not trusted")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Search" }));
  expect((screen.getByLabelText("Find a file") as HTMLInputElement).disabled).toBe(true);
});

test("no folders shows the text and the settings button", () => {
  const { onOpenSettings } = setup({}, []);
  expect(screen.getByText("No code folders yet. Open one in Settings → Workspaces.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open Workspaces settings" }));
  expect(onOpenSettings).toHaveBeenCalled();
});

test("tabs switch and Refresh reloads the changes", async () => {
  const { api } = setup();
  const [files, changes] = screen.getAllByRole("tab");
  expect(files?.getAttribute("aria-selected")).toBe("true");
  fireEvent.click(screen.getByRole("tab", { name: "Changes" }));
  await settle();
  expect(changes?.getAttribute("aria-selected")).toBe("true");
  expect(api.changes).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await settle();
  expect(api.changes).toHaveBeenCalledTimes(2);
});

test("the file tree keeps its expanded folders across tab switches", async () => {
  const tree = vi.fn().mockImplementation(async (_w: string, path: string) => ({
    path,
    truncated: false,
    entries: path
      ? [{ name: "inner.ts", type: "file", size: 1 }]
      : [{ name: "src", type: "dir", size: null }],
  }));
  const { api } = setup({ tree });
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "src" }));
  await settle();
  expect(screen.getByText("inner.ts")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Changes" }));
  await settle();
  expect(screen.getByRole("tabpanel", { name: "Changes" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  await settle();
  expect(screen.getByText("inner.ts")).toBeTruthy();
  expect(api.tree).toHaveBeenCalledTimes(2);
});

test("no changes request runs until Changes is opened", async () => {
  const { api } = setup();
  await settle();
  expect(api.changes).not.toHaveBeenCalled();
});

test("choosing a tree file loads and shows it", async () => {
  const { api } = setup();
  expect(screen.getByText("Choose a file to view it.")).toBeTruthy();
  await pickFile();
  expect(api.file).toHaveBeenCalledWith(A, "a.ts", expect.any(AbortSignal));
  expect(screen.getByText("three")).toBeTruthy();
  expect(screen.getByRole("tabpanel", { name: "Files" }).hasAttribute("hidden")).toBe(false);
});

const openFiles = () => screen.getByRole("group", { name: "Open files" });

test("file tabs deduplicate exact refs and keep the tree mounted", async () => {
  const tree = vi.fn().mockResolvedValue({
    path: "",
    truncated: false,
    entries: [
      { name: "a.ts", type: "file", size: 1 },
      { name: "b.ts", type: "file", size: 1 },
    ],
  });
  const { api } = setup({ tree });
  await settle();
  const files = screen.getByRole("tabpanel", { name: "Files" });
  fireEvent.click(within(files).getByRole("button", { name: "a.ts" }));
  await settle();
  fireEvent.click(within(files).getByRole("button", { name: "a.ts" }));
  fireEvent.click(within(files).getByRole("button", { name: "b.ts" }));
  await settle();
  expect(openFiles().querySelectorAll(".workbench-file-select")).toHaveLength(2);
  expect(
    within(openFiles()).getByRole("button", { name: "b.ts" }).getAttribute("aria-pressed"),
  ).toBe("true");
  fireEvent.click(within(openFiles()).getByRole("button", { name: "a.ts" }));
  await settle();
  expect(within(files).getByRole("button", { name: "a.ts" }).getAttribute("aria-current")).toBe(
    "true",
  );
  expect(api.tree).toHaveBeenCalledTimes(1);
});

test("file tabs retain full refs for duplicate basenames", async () => {
  const tree = vi.fn().mockImplementation(async (_w: string, path: string) => ({
    path,
    truncated: false,
    entries: path
      ? [{ name: "Same.ts", type: "file", size: 1 }]
      : [
          { name: "left", type: "dir", size: null },
          { name: "right", type: "dir", size: null },
        ],
  }));
  setup({ tree });
  await settle();
  for (const dir of ["left", "right"]) {
    const folderButton = screen.getByRole("button", { name: dir });
    fireEvent.click(folderButton);
    await settle();
    fireEvent.click(
      within(folderButton.closest("li") as HTMLElement).getByRole("button", {
        name: "Same.ts",
      }),
    );
    await settle();
  }
  const tabs = openFiles();
  expect(within(tabs).getByRole("button", { name: "left/Same.ts" })).toBeTruthy();
  expect(within(tabs).getByRole("button", { name: "right/Same.ts" })).toBeTruthy();
  fireEvent.click(within(tabs).getByRole("button", { name: "left/Same.ts" }));
  await settle();
  expect(
    within(tabs).getByRole("button", { name: "left/Same.ts" }).getAttribute("aria-pressed"),
  ).toBe("true");
});

test("a changed entry shows the diff first, with a toggle to the file", async () => {
  const { api } = setup();
  fireEvent.click(screen.getByRole("tab", { name: "Changes" }));
  await settle();
  fireEvent.click(screen.getByRole("button", { name: /c\.ts/ }));
  await settle();
  expect(api.diff).toHaveBeenCalledWith(A, "c.ts", expect.any(AbortSignal));
  expect(screen.getByText("+added line")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "File" }));
  expect(screen.getByText("three")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Diff" }));
  expect(screen.getByText("+added line")).toBeTruthy();
});

test("a changed file restores Diff after visiting another tab, while Files prefers File", async () => {
  setup();
  fireEvent.click(screen.getByRole("tab", { name: "Changes" }));
  await settle();
  fireEvent.click(screen.getByRole("button", { name: /c\.ts/ }));
  await settle();
  expect(screen.getByText("+added line")).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  await pickFile();
  fireEvent.click(within(openFiles()).getByRole("button", { name: "c.ts" }));
  await settle();
  expect(screen.getByText("+added line")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "File" }));
  fireEvent.click(within(openFiles()).getByRole("button", { name: "a.ts" }));
  await settle();
  fireEvent.click(within(openFiles()).getByRole("button", { name: "c.ts" }));
  await settle();
  expect(screen.getByText("three")).toBeTruthy();
});

test("a diff with no text opens on the file, with the toggle kept", async () => {
  setup({
    diff: vi.fn().mockResolvedValue({ path: "c.ts", text: null, reason: "no_diff" }),
  });
  fireEvent.click(screen.getByRole("tab", { name: "Changes" }));
  await settle();
  fireEvent.click(screen.getByRole("button", { name: /c\.ts/ }));
  await settle();
  expect(screen.getByText("three")).toBeTruthy();
  expect(screen.getByRole("button", { name: "File" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("button", { name: "Diff" }).getAttribute("aria-pressed")).toBe("false");
});

test("a failed diff falls back to the file with a note", async () => {
  setup({ diff: vi.fn().mockRejectedValue(new Error("x")) });
  fireEvent.click(screen.getByRole("tab", { name: "Changes" }));
  await settle();
  fireEvent.click(screen.getByRole("button", { name: /c\.ts/ }));
  await settle();
  expect(screen.getByText("The diff could not be loaded.")).toBeTruthy();
  expect(screen.getByText("three")).toBeTruthy();
});

test("a file failure shows its typed message", async () => {
  const { CodeApiError } = await import("../../clients/code/codeApi");
  setup({ file: vi.fn().mockRejectedValue(new CodeApiError("private_path")) });
  await pickFile();
  expect(screen.getByText("This file is private and is not shown.")).toBeTruthy();
});

const lineButton = (n: number) => screen.getByText(String(n), { selector: ".code-gutter" });

test("quote text is one line or a range", async () => {
  const { onQuote } = setup();
  await pickFile();
  fireEvent.click(lineButton(2));
  fireEvent.click(screen.getByRole("button", { name: "Quote in chat" }));
  expect(onQuote).toHaveBeenLastCalledWith("`a.ts:2`");
  fireEvent.click(lineButton(3), { shiftKey: true });
  fireEvent.click(screen.getByRole("button", { name: "Quote in chat" }));
  expect(onQuote).toHaveBeenLastCalledWith("`a.ts:2-3`");
});

test("a folder other than the active one cannot quote", async () => {
  const { onQuote } = setup();
  fireEvent.change(screen.getByLabelText("Code folder"), { target: { value: B } });
  await pickFile();
  fireEvent.click(lineButton(1));
  const quote = screen.getByRole("button", { name: "Quote in chat" });
  expect(quote.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(quote);
  expect(onQuote).not.toHaveBeenCalled();
});

test("Open in VS Code passes the folder, path and line", async () => {
  const { onOpenInEditor } = setup();
  await pickFile();
  fireEvent.click(screen.getByRole("button", { name: "Open in VS Code" }));
  expect(onOpenInEditor).toHaveBeenLastCalledWith(A, "a.ts", null);
  fireEvent.click(lineButton(2));
  fireEvent.click(screen.getByRole("button", { name: "Open in VS Code" }));
  expect(onOpenInEditor).toHaveBeenLastCalledWith(A, "a.ts", 2);
});

const editButton = () => screen.getByRole("button", { name: "Edit" });
const editorArea = (ref = "a.ts") => screen.queryByRole("textbox", { name: `Edit ${ref}` });

/** Edit stays put and names its reason; a click opens nothing. */
function expectEditRefused(reason: string) {
  const button = editButton();
  expect(button.getAttribute("aria-disabled")).toBe("true");
  const hint = screen.getByText(reason);
  expect(button.getAttribute("aria-describedby")).toBe(hint.id);
  fireEvent.click(button);
  expect(editorArea()).toBeNull();
  expect(screen.getByRole("button", { name: "Open in VS Code" })).toBeTruthy();
}

test("Edit is refused in a folder that is not trusted", async () => {
  setup({}, [folder(A, "Alpha", "existing_readonly")]);
  await pickFile();
  expectEditRefused("Trust this folder to edit its files.");
});

test("Edit is refused when the folder has no root id", async () => {
  setup({}, [folder(A, "Alpha", "ordinary", null)]);
  await pickFile();
  expectEditRefused("This folder can't be saved to now.");
});

test("a mixed-newline file opens read-only", async () => {
  setup({
    file: vi
      .fn()
      .mockImplementation(async (_w: string, p: string) =>
        file(p, "a\r\nb\n", { newline: "mixed" }),
      ),
  });
  await pickFile();
  expectEditRefused("This file mixes line endings, so it opens read-only. Edit it in VS Code.");
});

test("Edit is refused for a path over 512 characters", async () => {
  const long = "d/".repeat(256).concat("a.ts");
  setup({ file: vi.fn().mockImplementation(async () => file(long)) });
  await pickFile();
  expect(long.length).toBeGreaterThan(512);
  const button = editButton();
  expect(button.getAttribute("aria-disabled")).toBe("true");
  expect(screen.getByText("This file's path is too long to save from VIKI.")).toBeTruthy();
});

test("the first reason that holds is the one shown", async () => {
  setup({}, [folder(A, "Alpha", "existing_readonly", null)], A);
  await pickFile();
  expect(screen.getByText("Trust this folder to edit its files.")).toBeTruthy();
  expect(screen.queryByText("This folder can't be saved to now.")).toBeNull();
});

test("a file with no text has no Edit button", async () => {
  setup({
    file: vi
      .fn()
      .mockImplementation(async (_w: string, p: string) =>
        file(p, "", { text: null, reason: "binary" }),
      ),
  });
  await pickFile();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

test("Edit opens the editor in place of the file view, and Close reads the file again", async () => {
  const { api } = setup();
  await pickFile();
  fireEvent.click(editButton());
  await settle();
  expect((editorArea() as HTMLTextAreaElement).value).toBe("one\ntwo\nthree");
  expect(screen.queryByRole("button", { name: "Open in VS Code" })).toBeNull();
  expect(api.file).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await settle();
  expect(api.file).toHaveBeenCalledTimes(2);
  expect(editorArea()).toBeNull();
  expect(screen.getByRole("button", { name: "Open in VS Code" })).toBeTruthy();
});

test("Close moves focus to the Edit button once the file is read again", async () => {
  setup();
  await pickFile();
  fireEvent.click(editButton());
  await settle();
  screen.getByRole("button", { name: "Close" }).focus(); // a click focuses it; fireEvent does not
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await settle();
  expect(document.activeElement).toBe(editButton());
});

test("Close moves focus to the heading when the read fails", async () => {
  const read = vi.fn().mockImplementation(async (_w: string, p: string) => file(p));
  setup({ file: read });
  await pickFile();
  fireEvent.click(editButton());
  await settle();
  read.mockRejectedValue(new CodeApiError("not_found"));
  screen.getByRole("button", { name: "Close" }).focus(); // a click focuses it; fireEvent does not
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await settle();
  expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
});

test("a clean Edit tab reopens in Edit and Close leaves its tab in File view", async () => {
  setup({
    tree: vi.fn().mockResolvedValue({
      path: "",
      truncated: false,
      entries: [
        { name: "a.ts", type: "file", size: 1 },
        { name: "b.ts", type: "file", size: 1 },
      ],
    }),
  });
  await pickFile();
  fireEvent.click(editButton());
  await settle();
  fireEvent.click(
    within(screen.getByRole("tabpanel", { name: "Files" })).getByRole("button", {
      name: "b.ts",
    }),
  );
  await settle();
  fireEvent.click(within(openFiles()).getByRole("button", { name: "a.ts" }));
  await settle();
  expect(editorArea()).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await settle();
  expect(editorArea()).toBeNull();
  expect(within(openFiles()).getByRole("button", { name: "a.ts" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Open in VS Code" })).toBeTruthy();
});

test("dirty tabs keep independent drafts and refuse Close until Revert", async () => {
  setup({
    tree: vi.fn().mockResolvedValue({
      path: "",
      truncated: false,
      entries: [
        { name: "a.ts", type: "file", size: 1 },
        { name: "b.ts", type: "file", size: 1 },
      ],
    }),
  });
  await pickFile();
  fireEvent.click(editButton());
  await settle();
  fireEvent.input(editorArea() as HTMLTextAreaElement, { target: { value: "unsaved a" } });
  fireEvent.click(
    within(screen.getByRole("tabpanel", { name: "Files" })).getByRole("button", {
      name: "b.ts",
    }),
  );
  await settle();
  fireEvent.click(editButton());
  await settle();
  fireEvent.input(editorArea("b.ts") as HTMLTextAreaElement, { target: { value: "unsaved b" } });
  expect(getBuffer(A, "a.ts")?.text).toBe("unsaved a");
  expect(getBuffer(A, "b.ts")?.text).toBe("unsaved b");
  const close = within(openFiles()).getByRole("button", { name: "Close a.ts" });
  expect(close.getAttribute("aria-disabled")).toBe("true");
  expect(close.getAttribute("aria-describedby")).toBeTruthy();
  expect(close.getAttribute("title")).toBe("Save or revert first");
  fireEvent.click(close);
  expect(within(openFiles()).getByRole("button", { name: "a.ts" })).toBeTruthy();
  fireEvent.click(within(openFiles()).getByRole("button", { name: "a.ts" }));
  await settle();
  expect((editorArea() as HTMLTextAreaElement).value).toBe("unsaved a");
  fireEvent.click(within(openFiles()).getByRole("button", { name: "b.ts" }));
  await settle();
  expect((editorArea("b.ts") as HTMLTextAreaElement).value).toBe("unsaved b");
  fireEvent.click(within(openFiles()).getByRole("button", { name: "a.ts" }));
  await settle();
  expect((editorArea() as HTMLTextAreaElement).value).toBe("unsaved a");
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(true);
  fireEvent.click(screen.getByRole("button", { name: "Revert" }));
  confirm.mockRestore();
  await settle();
  fireEvent.click(within(openFiles()).getByRole("button", { name: "Close a.ts" }));
  await settle();
  expect(within(openFiles()).queryByRole("button", { name: "a.ts" })).toBeNull();
  expect(getBuffer(A, "a.ts")).toBeUndefined();
  expect(getBuffer(A, "b.ts")?.text).toBe("unsaved b");
  expect(dirtyRefs(A)).toEqual(["b.ts"]);
  expect((editorArea("b.ts") as HTMLTextAreaElement).value).toBe("unsaved b");
  expect(
    within(openFiles()).getByRole("button", { name: "Close b.ts" }).getAttribute("aria-disabled"),
  ).toBe("true");
});

test("a pending Save retains its exact target and rebases after switching tabs", async () => {
  const held = deferred<Awaited<ReturnType<CodeApi["save"]>>>();
  const { api } = setup({
    save: vi.fn(() => held.promise),
    tree: vi.fn().mockResolvedValue({
      path: "",
      truncated: false,
      entries: [
        { name: "a.ts", type: "file", size: 1 },
        { name: "b.ts", type: "file", size: 1 },
      ],
    }),
  });
  await pickFile();
  fireEvent.click(editButton());
  await settle();
  fireEvent.input(editorArea() as HTMLTextAreaElement, { target: { value: "saved while away" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const args = vi.mocked(api.save).mock.calls[0];
  fireEvent.click(
    within(screen.getByRole("tabpanel", { name: "Files" })).getByRole("button", {
      name: "b.ts",
    }),
  );
  await settle();
  expect(args).toEqual([
    A,
    "a.ts",
    {
      expected_sha256: "h1",
      policy_revision: 3,
      root_physical_id: "r1",
      content: "saved while away",
      encoding: "utf-8",
      newline: "lf",
    },
  ]);
  expect(vi.mocked(api.save).mock.calls[0]).toEqual(args);
  await act(async () =>
    held.resolve({ operation_id: A, status: "saved", sha256: "h2", cleanup_pending: false }),
  );
  expect(getBuffer(A, "a.ts")).toBeUndefined();
  expect(
    within(openFiles()).getByRole("button", { name: "b.ts" }).getAttribute("aria-pressed"),
  ).toBe("true");
});

test("closing inactive and active tabs chooses the right neighbor and restores focus", async () => {
  const { api } = setup({
    tree: vi.fn().mockResolvedValue({
      path: "",
      truncated: false,
      entries: ["a.ts", "b.ts", "c.ts"].map((name) => ({ name, type: "file", size: 1 })),
    }),
  });
  await settle();
  const files = screen.getByRole("tabpanel", { name: "Files" });
  for (const ref of ["a.ts", "b.ts", "c.ts"]) {
    fireEvent.click(within(files).getByRole("button", { name: ref }));
    await settle();
  }
  const reads = vi.mocked(api.file).mock.calls.length;
  fireEvent.click(within(openFiles()).getByRole("button", { name: "Close a.ts" }));
  expect(api.file).toHaveBeenCalledTimes(reads);
  fireEvent.click(within(openFiles()).getByRole("button", { name: "b.ts" }));
  await settle();
  fireEvent.click(within(openFiles()).getByRole("button", { name: "Close b.ts" }));
  await settle();
  expect(document.activeElement).toBe(within(openFiles()).getByRole("button", { name: "c.ts" }));
  fireEvent.click(within(files).getByRole("button", { name: "a.ts" }));
  await settle();
  fireEvent.click(within(openFiles()).getByRole("button", { name: "Close a.ts" }));
  await settle();
  expect(document.activeElement).toBe(within(openFiles()).getByRole("button", { name: "c.ts" }));
  fireEvent.click(within(openFiles()).getByRole("button", { name: "Close c.ts" }));
  await settle();
  expect(screen.queryByRole("group", { name: "Open files" })).toBeNull();
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Files" }));
});

const dirtyBuffer = (workspaceId = A) =>
  putBuffer({
    workspaceId,
    ref: "a.ts",
    base: { text: "one\ntwo\nthree", sha256: "h1", encoding: "utf-8", newline: "lf" },
    text: "one\nCHANGED",
  });
const unsaved = () => screen.getByRole("group", { name: "Unsaved changes" });

test("a buffered file is listed, marks its folder, and reopens in the editor", async () => {
  dirtyBuffer();
  setup();
  const options = within(screen.getByLabelText("Code folder")).getAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual(["Alpha (unsaved)", "Beta"]);
  fireEvent.click(within(unsaved()).getByRole("button", { name: "a.ts" }));
  await settle();
  expect((editorArea() as HTMLTextAreaElement).value).toBe("one\nCHANGED");
});

test("choosing a buffered file in the tree opens the editor", async () => {
  dirtyBuffer();
  setup();
  await settle();
  const tree = screen.getByRole("tabpanel", { name: "Files" });
  fireEvent.click(within(tree).getByRole("button", { name: "a.ts" }));
  await settle();
  expect((editorArea() as HTMLTextAreaElement).value).toBe("one\nCHANGED");
});

test("a folder without buffers has no unsaved list", () => {
  setup();
  expect(screen.queryByRole("group", { name: "Unsaved changes" })).toBeNull();
});

test("a buffered file whose read fails opens from the buffer's base, under the read's error", async () => {
  dirtyBuffer();
  setup({ file: vi.fn().mockRejectedValue(new CodeApiError("not_found")) });
  fireEvent.click(within(unsaved()).getByRole("button", { name: "a.ts" }));
  await settle();
  expect(screen.getByText("This file no longer exists.")).toBeTruthy();
  expect((editorArea() as HTMLTextAreaElement).value).toBe("one\nCHANGED");
});

test.each([false, true])(
  "an unheard Save's undo survives reopening with a failed read: %s",
  async (readFails) => {
    let rejectSave!: (error: unknown) => void;
    const pending = new Promise<Awaited<ReturnType<CodeApi["save"]>>>((_resolve, reject) => {
      rejectSave = reject;
    });
    const read = vi.fn<CodeApi["file"]>(async (_workspace, path) => file(path));
    setup({
      file: read,
      save: vi.fn(() => pending),
      tree: vi.fn().mockResolvedValue({
        path: "",
        truncated: false,
        entries: ["a.ts", "b.ts"].map((name) => ({ name, type: "file", size: 1 })),
      }),
    });
    await pickFile();
    fireEvent.click(editButton());
    await settle();
    fireEvent.input(editorArea() as HTMLTextAreaElement, { target: { value: "submitted" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.input(editorArea() as HTMLTextAreaElement, {
      target: { value: "one\ntwo\nthree" },
    });
    const files = screen.getByRole("tabpanel", { name: "Files" });
    fireEvent.click(within(files).getByRole("button", { name: "b.ts" }));
    await settle();
    rejectSave(new TypeError("unheard result"));
    await settle();
    if (readFails) read.mockRejectedValue(new CodeApiError("not_found"));
    fireEvent.click(within(openFiles()).getByRole("button", { name: "a.ts" }));
    await settle();
    expect((editorArea() as HTMLTextAreaElement).value).toBe("one\ntwo\nthree");
    if (readFails) expect(screen.getByText("This file no longer exists.")).toBeTruthy();
    expect(getBuffer(A, "a.ts")).toMatchObject({
      text: "one\ntwo\nthree",
      base: { text: "one\ntwo\nthree", sha256: "h1" },
    });
    expect(screen.getByRole("button", { name: "Close" }).getAttribute("aria-disabled")).toBe(
      "true",
    );
    fireEvent.click(within(files).getByRole("button", { name: "b.ts" }));
    await settle();
    expect(getBuffer(A, "a.ts")?.text).toBe("one\ntwo\nthree");
  },
);

test("the font size is a CSS variable and wrap is a data attribute", () => {
  const { container } = setup({}, undefined, A, { fontSize: 17, lineWrap: true });
  const root = container.querySelector(".workbench") as HTMLElement;
  expect(root.style.getPropertyValue("--code-font-size")).toBe("17px");
  expect(root.hasAttribute("data-wrap")).toBe(true);
});

test("no wrap leaves the data attribute off", () => {
  const { container } = setup();
  expect((container.querySelector(".workbench") as HTMLElement).hasAttribute("data-wrap")).toBe(
    false,
  );
});

test("a script file stays text", async () => {
  const text = "<script>alert(1)</script>";
  const { container } = setup({
    file: vi.fn().mockImplementation(async (_w: string, p: string) => file(p, text)),
  });
  await pickFile();
  expect(container.querySelector(".code-text")?.textContent).toBe(text);
  expect(container.querySelector("script")).toBeNull();
});

test("a save stuck behind a recovery record refreshes the stopped saves", async () => {
  const list = vi.fn().mockResolvedValue([]);
  const save = vi.fn().mockResolvedValue({
    operation_id: "op",
    status: "conflict",
    sha256: null,
    cleanup_pending: true,
  });
  setup({ save }, undefined, A, { recovery: { list, forget: vi.fn() } });
  await pickFile();
  expect(list).toHaveBeenCalledTimes(1);
  fireEvent.click(editButton());
  await settle();
  fireEvent.input(editorArea() as HTMLTextAreaElement, { target: { value: "changed" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await settle();
  expect(save).toHaveBeenCalledWith(
    A,
    "a.ts",
    expect.objectContaining({ policy_revision: 3, root_physical_id: "r1" }),
  );
  expect(list).toHaveBeenCalledTimes(2);
});

test("a stale file answer is dropped after switching folders", async () => {
  const slow = deferred<CodeFile>();
  setup({ file: vi.fn().mockReturnValue(slow.promise) });
  await pickFile();
  fireEvent.change(screen.getByLabelText("Code folder"), { target: { value: B } });
  await settle();
  slow.resolve(file("a.ts", "STALE"));
  await settle();
  expect(screen.queryByText("STALE")).toBeNull();
  expect(screen.getByText("Choose a file to view it.")).toBeTruthy();
});

test("a late read after switching tabs cannot replace the active pane", async () => {
  const slow = deferred<CodeFile>();
  const read = vi
    .fn()
    .mockImplementation((_w: string, path: string) =>
      path === "a.ts" ? slow.promise : Promise.resolve(file(path, "CURRENT")),
    );
  setup({
    file: read,
    tree: vi.fn().mockResolvedValue({
      path: "",
      truncated: false,
      entries: [
        { name: "a.ts", type: "file", size: 1 },
        { name: "b.ts", type: "file", size: 1 },
      ],
    }),
  });
  await settle();
  const files = screen.getByRole("tabpanel", { name: "Files" });
  fireEvent.click(within(files).getByRole("button", { name: "a.ts" }));
  const oldSignal = read.mock.calls[0]?.[2] as AbortSignal;
  fireEvent.click(within(files).getByRole("button", { name: "b.ts" }));
  await settle();
  expect(oldSignal.aborted).toBe(true);
  await act(async () => slow.resolve(file("a.ts", "STALE")));
  expect(screen.getByText("CURRENT")).toBeTruthy();
  expect(screen.queryByText("STALE")).toBeNull();
});

test("closing a loading tab aborts and rejects its late file result", async () => {
  const slow = deferred<CodeFile>();
  const { api } = setup({ file: vi.fn(() => slow.promise) });
  await settle();
  fireEvent.click(
    within(screen.getByRole("tabpanel", { name: "Files" })).getByRole("button", {
      name: "a.ts",
    }),
  );
  const signal = vi.mocked(api.file).mock.calls[0]?.[2];
  fireEvent.click(within(openFiles()).getByRole("button", { name: "Close a.ts" }));
  expect(signal?.aborted).toBe(true);
  await act(async () => slow.resolve(file("a.ts", "STALE CLOSED")));
  expect(screen.queryByText("STALE CLOSED")).toBeNull();
  expect(screen.getByText("Choose a file to view it.")).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Files" }));
});

test("folder selection clears tabs but keeps exact-ref drafts", async () => {
  dirtyBuffer();
  setup();
  await pickFile();
  expect(within(openFiles()).getByRole("button", { name: "a.ts" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Code folder"), { target: { value: B } });
  await settle();
  expect(screen.queryByRole("group", { name: "Open files" })).toBeNull();
  expect(getBuffer(A, "a.ts")?.text).toBe("one\nCHANGED");
});

function rootsStub(over: Partial<WorkbenchRoots> = {}): WorkbenchRoots {
  return {
    api: {
      roots: vi.fn().mockResolvedValue({ roots: [], truncated: false }),
      worktrees: vi.fn().mockResolvedValue({ worktrees: [], truncated: false }),
      record: vi.fn(),
      create: vi.fn(),
      remove: vi.fn(),
      archives: vi.fn(async () => {
        throw new RootsApiError("unavailable");
      }),
      cleanup: vi.fn(async () => {
        throw new RootsApiError("unavailable");
      }),
      restore: vi.fn(async () => {
        throw new RootsApiError("unavailable");
      }),
    },
    onStartSession: vi.fn().mockResolvedValue(null),
    ...over,
  };
}
const startButton = () => screen.getByRole("button", { name: "New session in this folder" });

test("four tabs with roots, three without", () => {
  setup();
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
    "Files",
    "Changes",
    "Search",
  ]);
  expect(screen.queryByRole("button", { name: "New session in this folder" })).toBeNull();
  cleanup();
  setup({}, undefined, A, { roots: rootsStub() });
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
    "Files",
    "Changes",
    "Search",
    "Worktrees",
  ]);
});

test("the Worktrees tab opens the panel for the folder shown", async () => {
  const roots = rootsStub();
  setup({}, undefined, A, { roots });
  fireEvent.click(screen.getByRole("tab", { name: "Worktrees" }));
  await settle();
  expect(screen.getByRole("tabpanel", { name: "Worktrees" })).toBeTruthy();
  expect(screen.getByLabelText("Branch name")).toBeTruthy();
  expect(roots.api.worktrees).toHaveBeenCalled();
});

test("New session in this folder starts in the folder shown, with no worktree", async () => {
  const roots = rootsStub();
  setup({}, undefined, A, { roots });
  fireEvent.change(screen.getByLabelText("Code folder"), { target: { value: B } });
  fireEvent.click(startButton());
  await settle();
  expect(roots.onStartSession).toHaveBeenCalledWith({ workspace_id: B, worktree_id: null });
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a refusal from starting is shown beside the button and clears on the next try", async () => {
  const onStartSession = vi
    .fn()
    .mockResolvedValueOnce("too_many_sessions")
    .mockResolvedValueOnce(null);
  setup({}, undefined, A, { roots: rootsStub({ onStartSession }) });
  fireEvent.click(startButton());
  await settle();
  expect(screen.getByRole("alert").textContent).toBe(
    "The most sessions allowed at once are running. Close one, or raise the limit in Settings › Chat.",
  );
  expect(screen.queryByRole("button", { name: "Change limit" })).toBeNull();
  fireEvent.click(startButton());
  await settle();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a too_many_sessions refusal with onRaiseLimit shows Change limit after the alert", async () => {
  const onStartSession = vi.fn().mockResolvedValue("too_many_sessions");
  const onRaiseLimit = vi.fn();
  setup({}, undefined, A, { roots: rootsStub({ onStartSession, onRaiseLimit }) });
  fireEvent.click(startButton());
  await settle();
  const alert = screen.getByRole("alert");
  const button = screen.getByRole("button", { name: "Change limit" });
  expect(alert.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(button);
  expect(onRaiseLimit).toHaveBeenCalledTimes(1);
});

test("a refusal other than too_many_sessions shows no Change limit button, even with onRaiseLimit", async () => {
  const onStartSession = vi.fn().mockResolvedValue("workspace_changed");
  setup({}, undefined, A, { roots: rootsStub({ onStartSession, onRaiseLimit: vi.fn() }) });
  fireEvent.click(startButton());
  await settle();
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Change limit" })).toBeNull();
});

test("an untrusted folder disables New session in this folder and says why", async () => {
  const roots = rootsStub();
  setup({}, [folder(A, "Alpha", "existing_readonly")], A, { roots });
  const button = startButton();
  expect(button.getAttribute("aria-disabled")).toBe("true");
  const reason = screen.getByText("Sessions start only in a trusted folder.");
  expect(button.getAttribute("aria-describedby")).toBe(reason.id);
  fireEvent.click(button);
  await settle();
  expect(roots.onStartSession).not.toHaveBeenCalled();
});

test("a trusted folder leaves the button enabled and the reason out", () => {
  setup({}, undefined, A, { roots: rootsStub() });
  expect(startButton().getAttribute("aria-disabled")).toBe("false");
  expect(screen.queryByText("Sessions start only in a trusted folder.")).toBeNull();
});

const OID = "0123456789abcdef0123456789abcdef01234567";
const readyWorktree = (n: number): WorktreeJson => ({
  worktree_id: `00000000-0000-4000-8000-00000000010${n}`,
  workspace_id: A,
  branch: `viki/w${n}`,
  base_oid: OID,
  path: `C:/area/wt${n}`,
  state: "ready",
  created_at: `2026-10-03T10:00:0${n}Z`,
  updated_at: "2026-10-03T10:00:00Z",
});

function reviewStub(): WorkbenchReview {
  return {
    api: { review: vi.fn().mockReturnValue(new Promise(() => {})) } as unknown as ReviewApi,
    onOpenUrl: vi.fn(),
  };
}

test("five tabs with review and roots, four without review", () => {
  setup({}, undefined, A, { roots: rootsStub(), review: reviewStub() });
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
    "Files",
    "Changes",
    "Search",
    "Worktrees",
    "Review",
  ]);
  cleanup();
  setup({}, undefined, A, { roots: rootsStub() });
  expect(screen.getAllByRole("tab")).toHaveLength(4);
});

test("the Review tab spans the view, and an open file does not hide the nav", async () => {
  const { container } = setup({}, undefined, A, { review: reviewStub() });
  await pickFile();
  const root = container.querySelector(".workbench") as HTMLElement;
  expect(root.hasAttribute("data-open")).toBe(true);
  expect(root.hasAttribute("data-review")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  expect(root.hasAttribute("data-review")).toBe(true);
  expect(root.hasAttribute("data-wide")).toBe(true);
  expect(root.hasAttribute("data-open")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  expect(root.hasAttribute("data-review")).toBe(false);
  expect(root.hasAttribute("data-open")).toBe(true);
});

function checksStub(): WorkbenchChecks {
  return {
    api: { list: vi.fn().mockReturnValue(new Promise(() => {})) } as unknown as ChecksApi,
    canRun: true,
  };
}

test("six tabs with checks, five without", () => {
  setup({}, undefined, A, { roots: rootsStub(), review: reviewStub(), checks: checksStub() });
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
    "Files",
    "Changes",
    "Search",
    "Worktrees",
    "Review",
    "Checks",
  ]);
  cleanup();
  setup({}, undefined, A, { roots: rootsStub(), review: reviewStub() });
  expect(screen.getAllByRole("tab")).toHaveLength(5);
});

test("the Checks tab spans the Code view", async () => {
  const { container } = setup({}, undefined, A, { checks: checksStub() });
  await pickFile();
  const root = container.querySelector(".workbench") as HTMLElement;
  expect(root.hasAttribute("data-open")).toBe(true);
  expect(root.hasAttribute("data-wide")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "Checks" }));
  expect(root.hasAttribute("data-wide")).toBe(true);
  expect(root.hasAttribute("data-review")).toBe(false);
  expect(root.hasAttribute("data-open")).toBe(false);
  expect(screen.getByRole("tabpanel", { name: "Checks" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  expect(root.hasAttribute("data-wide")).toBe(false);
  expect(root.hasAttribute("data-open")).toBe(true);
});

test("seven tabs with terminal, and the Terminal tab spans the Code view", async () => {
  const terminal = { api: {} as TerminalApi, canOpen: true };
  const { container } = setup({}, undefined, A, {
    roots: rootsStub(),
    review: reviewStub(),
    checks: checksStub(),
    terminal,
  });
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
    "Files",
    "Changes",
    "Search",
    "Worktrees",
    "Review",
    "Checks",
    "Terminal",
  ]);
  fireEvent.click(screen.getByRole("tab", { name: "Terminal" }));
  const root = container.querySelector(".workbench") as HTMLElement;
  expect(root.hasAttribute("data-wide")).toBe(true);
  expect(root.hasAttribute("data-open")).toBe(false);
  expect(screen.getByRole("tabpanel", { name: "Terminal" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Open terminal here" })).toBeTruthy();
});

function previewsStub() {
  return {
    api: { list: vi.fn().mockReturnValue(new Promise(() => {})) } as unknown as PreviewsApi,
    canRun: true,
  };
}

test("eight tabs with previews, seven without, and the Previews tab spans the Code view", async () => {
  const all = { roots: rootsStub(), review: reviewStub(), checks: checksStub() };
  const terminal = { api: {} as TerminalApi, canOpen: true };
  setup({}, undefined, A, { ...all, terminal });
  expect(screen.getAllByRole("tab")).toHaveLength(7);
  cleanup();
  const { container } = setup({}, undefined, A, { ...all, terminal, previews: previewsStub() });
  const names = screen.getAllByRole("tab").map((t) => t.textContent);
  expect(names).toHaveLength(8);
  expect(names[7]).toBe("Previews");
  await pickFile();
  const root = container.querySelector(".workbench") as HTMLElement;
  expect(root.hasAttribute("data-wide")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "Previews" }));
  expect(root.hasAttribute("data-wide")).toBe(true);
  expect(root.hasAttribute("data-open")).toBe(false);
  expect(screen.getByRole("tabpanel", { name: "Previews" })).toBeTruthy();
});

test("opening an unsaved file while Review is selected switches to Files and shows it", async () => {
  dirtyBuffer();
  const { container } = setup({}, undefined, A, { review: reviewStub() });
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  const root = container.querySelector(".workbench") as HTMLElement;
  expect(root.hasAttribute("data-review")).toBe(true);
  fireEvent.click(within(unsaved()).getByRole("button", { name: "a.ts" }));
  await settle();
  expect(screen.getByRole("tab", { name: "Files" }).getAttribute("aria-selected")).toBe("true");
  expect(root.hasAttribute("data-review")).toBe(false);
  expect(root.hasAttribute("data-open")).toBe(true);
});

test.each(["Checks", "Terminal", "Previews"])(
  "opening an unsaved file while %s is selected switches to Files and shows it",
  async (name) => {
    dirtyBuffer();
    const { container } = setup({}, undefined, A, {
      checks: checksStub(),
      terminal: { api: {} as TerminalApi, canOpen: true },
      previews: previewsStub(),
    });
    fireEvent.click(screen.getByRole("tab", { name }));
    const root = container.querySelector(".workbench") as HTMLElement;
    expect(root.hasAttribute("data-wide")).toBe(true);
    fireEvent.click(within(unsaved()).getByRole("button", { name: "a.ts" }));
    await settle();
    expect(screen.getByRole("tab", { name: "Files" }).getAttribute("aria-selected")).toBe("true");
    expect(root.hasAttribute("data-wide")).toBe(false);
    expect(root.hasAttribute("data-open")).toBe(true);
  },
);

const entry = { path: "src/a.ts", change: "modified", kind: "text", digest: "a".repeat(64) };
function reviewWithEntries(): WorkbenchReview {
  const answer = {
    workspace_id: A,
    worktree_id: null,
    branch: "main",
    head_oid: OID,
    in_progress: false,
    entries: [entry],
    truncated: false,
    remote: null,
    remote_state: "none",
    remote_oid: null,
    pull_request_url: null,
    integration: null,
  };
  return {
    api: { review: vi.fn().mockResolvedValue(answer) } as unknown as ReviewApi,
    onOpenUrl: vi.fn(),
  };
}

test("the Review panel is not mounted, and nothing is reviewed, until the tab is first opened", async () => {
  const review = reviewWithEntries();
  setup({}, undefined, A, { review });
  await settle();
  expect(review.api.review).not.toHaveBeenCalled();
  expect(document.getElementById("workbench-panel-review")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  await settle();
  expect(review.api.review).toHaveBeenCalledTimes(1);
});

test("the commit message and ticked files survive a trip to another tab", async () => {
  const review = reviewWithEntries();
  setup({}, undefined, A, { review });
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  await settle();
  fireEvent.change(screen.getByLabelText("Commit message"), { target: { value: "Fix a" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Include src/a.ts" }));
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  const panel = document.getElementById("workbench-panel-review") as HTMLElement;
  expect(panel.hidden).toBe(true);
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  await settle();
  expect(panel.hidden).toBe(false);
  expect((screen.getByLabelText("Commit message") as HTMLTextAreaElement).value).toBe("Fix a");
  expect(
    (screen.getByRole("checkbox", { name: "Include src/a.ts" }) as HTMLInputElement).checked,
  ).toBe(true);
  expect(review.api.review).toHaveBeenCalledTimes(1);
});

test("choosing another folder drops the hidden Review panel until the tab is opened again", async () => {
  const review = reviewWithEntries();
  setup({}, undefined, A, { review });
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  await settle();
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  fireEvent.change(screen.getByLabelText("Code folder"), { target: { value: B } });
  await settle();
  expect(review.api.review).toHaveBeenCalledTimes(1);
  expect(document.getElementById("workbench-panel-review")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  await settle();
  expect(review.api.review).toHaveBeenCalledTimes(2);
  expect(review.api.review).toHaveBeenLastCalledWith(B, null, expect.anything());
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  fireEvent.change(screen.getByLabelText("Code folder"), { target: { value: A } });
  await settle();
  expect(document.getElementById("workbench-panel-review")).toBeNull();
  expect(review.api.review).toHaveBeenCalledTimes(2);
});

async function withWorktrees(review: WorkbenchReview) {
  const roots = rootsStub();
  const rows = [readyWorktree(1), readyWorktree(2)];
  (roots.api.worktrees as ReturnType<typeof vi.fn>).mockResolvedValue({
    worktrees: rows,
    truncated: false,
  });
  setup({}, undefined, A, { roots, review });
  fireEvent.click(screen.getByRole("tab", { name: "Worktrees" }));
  await settle();
  return rows;
}
const reviewIn = (branch: string) =>
  within(screen.getByText(branch).closest("li") as HTMLElement).getByRole("button", {
    name: "Review",
  });

test("a worktree's Review opens the tab on it", async () => {
  const review = reviewStub();
  const rows = await withWorktrees(review);
  fireEvent.click(reviewIn("viki/w2"));
  await settle();
  expect(screen.getByRole("tab", { name: "Review" }).getAttribute("aria-selected")).toBe("true");
  expect(review.api.review).toHaveBeenCalledWith(A, rows[1]?.worktree_id, expect.anything());
});

test("Review on another worktree while the tab is open asks for that worktree", async () => {
  const review = reviewStub();
  const rows = await withWorktrees(review);
  fireEvent.click(reviewIn("viki/w1"));
  await settle();
  expect(review.api.review).toHaveBeenLastCalledWith(A, rows[0]?.worktree_id, expect.anything());
  fireEvent.click(screen.getByRole("tab", { name: "Worktrees" }));
  await settle();
  fireEvent.click(reviewIn("viki/w2"));
  await settle();
  expect(review.api.review).toHaveBeenLastCalledWith(A, rows[1]?.worktree_id, expect.anything());
});

test("the Review checks read again when the tab is shown again, after a run in the Checks tab", async () => {
  const check = (last_run: unknown) => ({
    check_id: "00000000-0000-4000-8000-000000000c01",
    name: "ok",
    command: "x",
    timeout_s: 600,
    created_at: "2026-10-04T10:00:00.000Z",
    readable: true,
    last_run,
  });
  const passed = {
    run_id: "00000000-0000-4000-8000-000000000d01",
    check_id: "00000000-0000-4000-8000-000000000c01",
    workspace_id: A,
    worktree_id: null,
    head_oid: OID,
    dirty: false,
    outcome: "passed",
    exit_code: 0,
    duration_ms: 1,
    output_bytes: 0,
    started_at: "2026-10-04T10:00:00.000Z",
    ended_at: "2026-10-04T10:00:01.000Z",
  };
  let answer = [check(null)];
  const list = vi.fn().mockImplementation(() => Promise.resolve(answer));
  const review = reviewWithEntries();
  setup({}, undefined, A, {
    review,
    checks: { api: { list } as unknown as ChecksApi, canRun: true },
  });
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  await settle();
  const panel = document.getElementById("workbench-panel-review") as HTMLElement;
  expect(within(panel).getByText("ok — not run here yet")).toBeTruthy();
  expect(list).toHaveBeenCalledTimes(1);
  answer = [check(passed)];
  fireEvent.click(screen.getByRole("tab", { name: "Checks" }));
  await settle();
  expect(list).toHaveBeenCalledTimes(2); // the Checks tab's own read; Review's stays quiet
  fireEvent.click(screen.getByRole("tab", { name: "Review" }));
  await settle();
  expect(list).toHaveBeenCalledTimes(3);
  expect(within(panel).getByText("ok — Passed · matches this commit")).toBeTruthy();
});

test("a supplied header Back replaces the default and preserves Code drafts on leave", async () => {
  dirtyBuffer();
  const onBack = vi.fn(() => cleanup());
  const { api } = setup({}, undefined, A, {
    onBack,
    renderBack: (back) => (
      <button type="button" onClick={back}>
        Back
      </button>
    ),
  });
  expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Code" }));
  expect(screen.queryByRole("button", { name: "Back to chat" })).toBeNull();
  expect(screen.getAllByRole("button", { name: "Back" })).toHaveLength(1);
  fireEvent.click(within(unsaved()).getByRole("button", { name: "a.ts" }));
  await settle();
  expect((editorArea() as HTMLTextAreaElement).value).toBe("one\nCHANGED");
  const held = getBuffer(A, "a.ts");
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(onBack).toHaveBeenCalledTimes(1);
  expect(api.save).not.toHaveBeenCalled();
  expect(screen.queryByRole("heading", { name: "Code" })).toBeNull();
  expect(getBuffer(A, "a.ts")).toEqual(held);
  expect(getBuffer(A, "a.ts")).toMatchObject({ workspaceId: A, ref: "a.ts", text: "one\nCHANGED" });
});

async function sizedCode(overrides: Partial<CodeApi> = {}, extra: Partial<WorkbenchProps> = {}) {
  const observers: ResizeObserverCallback[] = [];
  vi.stubGlobal("innerWidth", 1200);
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe = vi.fn();
      disconnect = vi.fn();
      constructor(callback: ResizeObserverCallback) {
        observers.push(callback);
      }
    },
  );
  const view = setup(overrides, undefined, A, extra);
  await pickFile();
  const root = view.container.querySelector<HTMLElement>(".workbench");
  const pane = view.container.querySelector<HTMLElement>(".workbench-pane");
  if (!root || !pane) throw new Error("Missing actual Code panes");
  const resize = (width: number, viewport = 1200) => {
    Object.defineProperty(root, "clientWidth", { configurable: true, value: width });
    const current = root.querySelector<HTMLElement>(".workbench-pane");
    if (current)
      Object.defineProperties(current, {
        offsetTop: { configurable: true, value: 80 },
        clientHeight: { configurable: true, value: 300 },
      });
    vi.stubGlobal("innerWidth", viewport);
    act(() => {
      for (const callback of observers) callback([], {} as ResizeObserver);
    });
  };
  return { ...view, root, pane, resize };
}
function cleanSizedCode() {
  cleanup();
  vi.unstubAllGlobals();
}
const codeSplit = () => screen.getByRole("separator", { name: "Resize Code panes" });
test("Code inner split preserves natural geometry and Reset without reads or navigation", async () => {
  const { root, resize, api, onBack, onOpenSettings, onOpenInEditor, onQuote } = await sizedCode();
  try {
    expect(screen.queryByRole("separator")).toBeNull();
    resize(908);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("");
    expect(codeSplit().getAttribute("aria-valuenow")).toBe("300");
    expect(
      document.getElementById(codeSplit().getAttribute("aria-controls") ?? "")?.className,
    ).toBe("workbench-nav");
    fireEvent.keyDown(codeSplit(), { key: "ArrowRight" });
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("310px");
    fireEvent.click(screen.getByRole("button", { name: "Reset Code panes" }));
    resize(1808);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("");
    expect(codeSplit().getAttribute("aria-valuenow")).toBe("600");
    expect(api.tree).toHaveBeenCalledTimes(1);
    expect(api.file).toHaveBeenCalledTimes(1);
    expect(api.save).not.toHaveBeenCalled();
    for (const callback of [onBack, onOpenSettings, onOpenInEditor, onQuote])
      expect(callback).not.toHaveBeenCalled();
  } finally {
    cleanSizedCode();
  }
});

function codeSplitPointer(handle: HTMLElement, type: string, clientX: number) {
  const event = new MouseEvent(type, { bubbles: true, button: 0, clientX });
  Object.defineProperties(event, { pointerId: { value: 42 }, isPrimary: { value: true } });
  fireEvent(handle, event);
}
function captureCodeSplit(handle: HTMLElement) {
  let held = false;
  const release = vi.fn(() => {
    held = false;
  });
  Object.assign(handle, {
    setPointerCapture: () => {
      held = true;
    },
    hasPointerCapture: () => held,
    releasePointerCapture: release,
  });
  return release;
}
test("Code keyboard clamps and passive shortages retain its local explicit request", async () => {
  const { root, resize } = await sizedCode();
  try {
    resize(908);
    expect(codeSplit().getAttribute("aria-valuemin")).toBe("240");
    expect(codeSplit().getAttribute("aria-valuemax")).toBe("660");
    for (const [key, shiftKey, expected] of [
      ["Home", false, 240],
      ["ArrowLeft", true, 240],
      ["ArrowRight", true, 290],
      ["ArrowLeft", false, 280],
      ["End", false, 660],
      ["ArrowRight", true, 660],
    ] as const) {
      fireEvent.keyDown(codeSplit(), { key, shiftKey });
      expect(root.style.getPropertyValue("--workbench-nav-width")).toBe(`${expected}px`);
    }
    resize(528);
    expect(codeSplit().getAttribute("aria-valuemax")).toBe("280");
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("280px");
    resize(487);
    expect(screen.queryByRole("separator")).toBeNull();
    resize(908);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("660px");
  } finally {
    cleanSizedCode();
  }
});
test("Code pointer final sample has one zoom conversion and scale change rolls back", async () => {
  const scale = document.documentElement.style.getPropertyValue("--font-scale");
  const { root, resize } = await sizedCode();
  try {
    resize(908);
    document.documentElement.style.setProperty("--font-scale", "1.5");
    const handle = codeSplit();
    const release = captureCodeSplit(handle);
    codeSplitPointer(handle, "pointerdown", 100);
    codeSplitPointer(handle, "pointermove", 145);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("330px");
    codeSplitPointer(handle, "pointerup", 160);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("340px");
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("350px");
    codeSplitPointer(handle, "pointerdown", 100);
    codeSplitPointer(handle, "pointermove", 145);
    document.documentElement.style.setProperty("--font-scale", "1");
    codeSplitPointer(handle, "pointerup", 190);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("350px");
    expect(release).toHaveBeenCalledTimes(2);
    codeSplitPointer(handle, "pointerdown", 100);
    codeSplitPointer(handle, "pointerup", -1000);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("240px");
    codeSplitPointer(handle, "pointerdown", 100);
    codeSplitPointer(handle, "pointerup", 3000);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("660px");
    expect(release).toHaveBeenCalledTimes(4);
  } finally {
    if (scale) document.documentElement.style.setProperty("--font-scale", scale);
    else document.documentElement.style.removeProperty("--font-scale");
    cleanSizedCode();
  }
});

test.each(["Escape", "blur", "pointercancel", "lostpointercapture"])(
  "Code %s retains natural/explicit widths and refuses late release",
  async (kind) => {
    for (const explicit of [false, true]) {
      const { root, resize } = await sizedCode();
      try {
        resize(908);
        const handle = codeSplit();
        if (explicit) fireEvent.keyDown(handle, { key: "ArrowRight" });
        const before = root.style.getPropertyValue("--workbench-nav-width");
        const release = captureCodeSplit(handle);
        codeSplitPointer(handle, "pointerdown", 100);
        codeSplitPointer(handle, "pointermove", 140);
        if (kind === "Escape") fireEvent.keyDown(window, { key: "Escape" });
        else if (kind === "blur") fireEvent.blur(window);
        else codeSplitPointer(handle, kind, 140);
        codeSplitPointer(handle, "pointerup", 180);
        expect(root.style.getPropertyValue("--workbench-nav-width")).toBe(before);
        expect(release).toHaveBeenCalledTimes(1);
        resize(1808);
        expect(codeSplit().getAttribute("aria-valuenow")).toBe(explicit ? "310" : "600");
      } finally {
        cleanSizedCode();
      }
    }
  },
);
test.each([false, true])(
  "Code stationary/return/captured Reset remains natural (%s)",
  async (moved) => {
    const { root, resize } = await sizedCode();
    try {
      resize(908);
      const handle = codeSplit();
      const release = captureCodeSplit(handle);
      codeSplitPointer(handle, "pointerdown", 100);
      if (moved) codeSplitPointer(handle, "pointermove", 130);
      codeSplitPointer(handle, "pointerup", 100);
      expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("");
      codeSplitPointer(handle, "pointerdown", 100);
      if (moved) codeSplitPointer(handle, "pointermove", 130);
      fireEvent.click(screen.getByRole("button", { name: "Reset Code panes" }));
      // Capture mocks need the native release event explicitly.
      codeSplitPointer(handle, "lostpointercapture", 130);
      codeSplitPointer(handle, "pointerup", 180);
      resize(1808);
      expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("");
      expect(codeSplit().getAttribute("aria-valuenow")).toBe("600");
      expect(release).toHaveBeenCalledTimes(2);
      codeSplitPointer(handle, "pointerdown", 100);
      codeSplitPointer(handle, "pointerup", 120);
      expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("620px");
    } finally {
      cleanSizedCode();
    }
  },
);
test("Code bound changes cancel a preview without replacing the retained preference", async () => {
  const { root, resize } = await sizedCode();
  try {
    resize(908);
    fireEvent.keyDown(codeSplit(), { key: "End" });
    const handle = codeSplit();
    const release = captureCodeSplit(handle);
    codeSplitPointer(handle, "pointerdown", 100);
    codeSplitPointer(handle, "pointermove", 70);
    resize(528);
    expect(release).toHaveBeenCalledTimes(1);
    codeSplitPointer(handle, "pointerup", 40);
    resize(908);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("660px");
  } finally {
    cleanSizedCode();
  }
});

test("Code resize and Reset retain its dirty editor, selection, scroll and buffer owner", async () => {
  const { root, pane, resize, api } = await sizedCode();
  try {
    resize(908);
    fireEvent.click(editButton());
    await settle();
    const area = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Edit a.ts" });
    fireEvent.input(area, { target: { value: "literal sample code" } });
    area.setSelectionRange(2, 8);
    pane.scrollTop = 91;
    const handle = codeSplit();
    captureCodeSplit(handle);
    codeSplitPointer(handle, "pointerdown", 100);
    codeSplitPointer(handle, "pointerup", 160);
    fireEvent.click(screen.getByRole("button", { name: "Reset Code panes" }));
    expect(screen.getByRole("textbox", { name: "Edit a.ts" })).toBe(area);
    expect(area.value).toBe("literal sample code");
    expect([area.selectionStart, area.selectionEnd]).toEqual([2, 8]);
    expect(pane.scrollTop).toBe(91);
    expect(getBuffer(A, "a.ts")?.text).toBe("literal sample code");
    expect(dirtyRefs(A)).toEqual(["a.ts"]);
    expect(dirtyRefs(B)).toEqual([]);
    expect(api.file).toHaveBeenCalledTimes(1);
    expect(api.save).not.toHaveBeenCalled();
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("");
  } finally {
    cleanSizedCode();
  }
});
test("Code narrow and full-width tabs hide captured controls without losing editor or width", async () => {
  const { root, resize } = await sizedCode({}, { review: reviewWithEntries() });
  try {
    resize(908);
    fireEvent.click(editButton());
    await settle();
    const area = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Edit a.ts" });
    fireEvent.input(area, { target: { value: "held across panels" } });
    area.setSelectionRange(2, 7);
    const handle = codeSplit();
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    const release = captureCodeSplit(handle);
    codeSplitPointer(handle, "pointerdown", 100);
    codeSplitPointer(handle, "pointermove", 130);
    resize(908, 799);
    expect(screen.queryByRole("separator")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1, name: "Code" }));
    expect(release).toHaveBeenCalledTimes(1);
    codeSplitPointer(handle, "pointerup", 180);
    resize(908);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("310px");
    area.focus();
    resize(908, 799);
    expect(document.activeElement).toBe(area);
    expect(screen.getByRole("tabpanel", { name: "Files" }).hasAttribute("hidden")).toBe(false);
    resize(908);
    fireEvent.click(screen.getByRole("tab", { name: "Review" }));
    await settle();
    resize(908);
    expect(root.hasAttribute("data-wide")).toBe(true);
    expect(screen.queryByRole("separator")).toBeNull();
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("");
    fireEvent.click(screen.getByRole("tab", { name: "Files" }));
    await settle();
    resize(908);
    expect(root.style.getPropertyValue("--workbench-nav-width")).toBe("310px");
    expect(screen.getByRole("textbox", { name: "Edit a.ts" })).toBe(area);
    expect(area.value).toBe("held across panels");
    expect([area.selectionStart, area.selectionEnd]).toEqual([2, 7]);
  } finally {
    cleanSizedCode();
  }
});

test("Code resizing keeps an outstanding Save exact and retains its mounted draft", async () => {
  const held = deferred<Awaited<ReturnType<CodeApi["save"]>>>();
  const { resize, api } = await sizedCode({ save: vi.fn(() => held.promise) });
  try {
    resize(908);
    fireEvent.click(editButton());
    await settle();
    const area = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Edit a.ts" });
    fireEvent.input(area, { target: { value: "held Code save" } });
    area.setSelectionRange(1, 4);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const args = vi.mocked(api.save).mock.calls[0];
    fireEvent.keyDown(codeSplit(), { key: "End" });
    fireEvent.click(screen.getByRole("button", { name: "Reset Code panes" }));
    expect(screen.getByRole("textbox", { name: "Edit a.ts" })).toBe(area);
    expect(area.value).toBe("held Code save");
    expect([area.selectionStart, area.selectionEnd]).toEqual([1, 4]);
    expect(screen.getByRole("button", { name: "Save" }).getAttribute("aria-disabled")).toBe("true");
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.save).mock.calls[0]).toEqual(args);
    expect(args?.slice(0, 2)).toEqual([A, "a.ts"]);
    expect(args?.[2]).toEqual({
      expected_sha256: "h1",
      policy_revision: 3,
      root_physical_id: "r1",
      content: "held Code save",
      encoding: "utf-8",
      newline: "lf",
    });
    expect(api.file).toHaveBeenCalledTimes(1);
    await act(async () =>
      held.resolve({ operation_id: A, status: "conflict", sha256: null, cleanup_pending: false }),
    );
    expect(screen.getByRole("textbox", { name: "Edit a.ts" })).toBe(area);
    expect(area.value).toBe("held Code save");
    expect(api.save).toHaveBeenCalledTimes(1);
  } finally {
    cleanSizedCode();
  }
});
test("Code split unmount releases actual drag listeners and rejects late events", async () => {
  const view = await sizedCode();
  const add = vi.spyOn(window, "addEventListener");
  const remove = vi.spyOn(window, "removeEventListener");
  try {
    view.resize(908);
    const handle = codeSplit();
    const release = captureCodeSplit(handle);
    codeSplitPointer(handle, "pointerdown", 100);
    const bindings = add.mock.calls.filter(([type]) => type === "keydown" || type === "blur");
    expect(bindings).toHaveLength(2);
    cleanup();
    for (const [type, listener] of bindings) expect(remove).toHaveBeenCalledWith(type, listener);
    expect(release).toHaveBeenCalledTimes(1);
    codeSplitPointer(handle, "pointerup", 180);
    view.resize(1808);
    expect(screen.queryByRole("separator")).toBeNull();
  } finally {
    add.mockRestore();
    remove.mockRestore();
    cleanSizedCode();
  }
});
test("Code CSS preserves the default ratio, scroll roots and full-width views", async () => {
  const { readCss } = await import("../../testing/css");
  const css = readCss(import.meta.url, "Workbench.css");
  expect(css).toMatch(/var\(--workbench-nav-width, minmax\(240px, 1fr\)\) minmax\(0, 2fr\)/);
  expect(css).toMatch(/\.workbench-resize-controls\s*\{[^}]*position:\s*absolute/);
  expect(css).toMatch(
    /\.workbench-nav,\s*\.workbench-pane\s*\{[^}]*overflow:\s*auto;[^}]*max-height:\s*70vh/,
  );
  expect(css).not.toMatch(/\.workbench\[data-open\] \.workbench-nav\s*\{[^}]*display:\s*none/);
  expect(css).toMatch(/@media not all and \(min-width: 800px\)\s*\{[^}]*\.workbench-pane/);
  expect(css).toMatch(/\.workbench\[data-wide\] \.workbench-nav\s*\{[^}]*grid-column:\s*1 \/ -1/);
  expect(css).toMatch(/\.workbench\[data-wide\] \.workbench-pane\s*\{[^}]*display:\s*none/);
});

test("Code sizing keeps a pending file read and admits its original result once", async () => {
  const held = deferred<CodeFile>();
  const { api, resize } = await sizedCode({ file: vi.fn(() => held.promise) });
  try {
    resize(908);
    const signal = vi.mocked(api.file).mock.calls[0]?.[2];
    fireEvent.keyDown(codeSplit(), { key: "End" });
    fireEvent.click(screen.getByRole("button", { name: "Reset Code panes" }));
    expect(signal?.aborted).toBe(false);
    expect(api.file).toHaveBeenCalledTimes(1);
    await act(async () => held.resolve(file("a.ts", "original held read")));
    expect(document.querySelector(".code-text")?.textContent).toBe("original held read");
    expect(api.file).toHaveBeenCalledExactlyOnceWith(A, "a.ts", expect.any(AbortSignal));
    expect(api.save).not.toHaveBeenCalled();
  } finally {
    cleanSizedCode();
  }
});
test("Code exact long duplicate paths stay selectable across sizing without extra tree reads", async () => {
  const left = "日本語".repeat(20);
  const right = "Another".repeat(15);
  const tree = vi.fn<CodeApi["tree"]>(async (_workspace, path) => ({
    path,
    truncated: false,
    entries:
      path === ""
        ? [
            { name: "a.ts", type: "file", size: 1 },
            { name: left, type: "dir", size: null },
            { name: right, type: "dir", size: null },
          ]
        : [{ name: "Same.ts", type: "file", size: 1 }],
  }));
  const { api, resize } = await sizedCode({ tree });
  try {
    for (const dir of [left, right]) {
      const opener = screen.getByRole("button", { name: dir });
      fireEvent.click(opener);
      await settle();
      const row = within(opener.closest("li") as HTMLElement).getByRole("button", {
        name: "Same.ts",
      });
      fireEvent.click(row);
      await settle();
      const reads = vi.mocked(api.tree).mock.calls.length;
      resize(908);
      fireEvent.keyDown(codeSplit(), { key: "ArrowRight" });
      fireEvent.click(screen.getByRole("button", { name: "Reset Code panes" }));
      expect(row.getAttribute("aria-current")).toBe("true");
      expect(api.file).toHaveBeenLastCalledWith(A, `${dir}/Same.ts`, expect.any(AbortSignal));
      expect(api.tree).toHaveBeenCalledTimes(reads);
    }
    expect(api.file).toHaveBeenCalledTimes(3);
    expect(api.save).not.toHaveBeenCalled();
  } finally {
    cleanSizedCode();
  }
});
test("Code has no split or new reads without a registered folder", () => {
  const { api } = setup({}, []);
  expect(screen.queryByRole("separator")).toBeNull();
  expect(screen.getByText("No code folders yet. Open one in Settings → Workspaces.")).toBeTruthy();
  expect(api.tree).not.toHaveBeenCalled();
  expect(api.file).not.toHaveBeenCalled();
  expect(api.save).not.toHaveBeenCalled();
});
