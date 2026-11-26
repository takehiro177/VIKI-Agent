// @ts-expect-error no @types/node in this browser-focused tsconfig; see ../Workbench/editorBoundary.unit.test.ts
import { readdirSync, readFileSync } from "node:fs";
// @ts-expect-error see above
import { fileURLToPath } from "node:url";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { t, tf } from "../../i18n/t";
import { type InstructionName, type VaultApi, VaultWriteError } from "../../state/vaultApi";
import { InstructionEditor } from "./InstructionEditor";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const SHA_READ = "a".repeat(64);
const SHA_SAVED = "b".repeat(64);
const SHA_DISK = "d".repeat(64);
const SCOPE = { workspaceId: "w1", policyRevision: 3, rootPhysicalId: "12:34" };
const SOURCE = "\uFEFF# A\r\nB\r\nC";
const NAME: InstructionName = "AGENTS.md";

type Save = NonNullable<VaultApi["saveInstruction"]>;
type Options = {
  source?: string;
  name?: InstructionName;
  save?: Save;
  note?: VaultApi["note"];
};

const saved: Save = async () => ({
  operation_id: "op",
  status: "saved",
  cleanup_pending: false,
  sha256: SHA_SAVED,
});
const conflict = (cleanupPending = false): Save => {
  return async () => ({
    operation_id: "op",
    status: "conflict",
    cleanup_pending: cleanupPending,
    sha256: null,
  });
};
const diskNote =
  (source: string): VaultApi["note"] =>
  async (_id, ref) => ({ note: { ref, text: source, source, sha256: SHA_DISK } });

function setup(options: Options = {}) {
  const saveInstruction = vi.fn<Save>(options.save ?? saved);
  const note = vi.fn<VaultApi["note"]>(options.note ?? diskNote(""));
  const onFile = vi.fn();
  const onDirty = vi.fn();
  const view = render(
    <InstructionEditor
      api={{ note, saveInstruction }}
      scope={SCOPE}
      name={options.name ?? NAME}
      source={options.source ?? SOURCE}
      sha256={SHA_READ}
      onFile={onFile}
      onDirty={onDirty}
    />,
  );
  return { ...view, saveInstruction, note, onFile, onDirty };
}

const box = () => screen.getByLabelText(tf("settings.persona.editor.label", { name: NAME }));
const textarea = () => box() as HTMLTextAreaElement;
const saveButton = () =>
  screen.getByRole("button", { name: tf("settings.persona.editor.save", { name: NAME }) });
const replaceButton = () =>
  screen.getByRole("button", { name: t("settings.persona.editor.replace") });
const discardButton = () =>
  screen.getByRole("button", { name: t("settings.persona.editor.discard") });
const type = (value: string) => fireEvent.change(box(), { target: { value } });
const alertText = () => screen.getByRole("alert").textContent;

test("shows the text without its BOM or CR in a textarea named for the file", () => {
  setup();
  expect(textarea().value).toBe("# A\nB\nC");
  expect(textarea().readOnly).toBe(false);
});

test("typing marks the draft dirty and sends nothing", () => {
  const { saveInstruction, note, onDirty } = setup();
  expect(onDirty).toHaveBeenLastCalledWith(false);
  type("# A\nB\nX");
  expect(onDirty).toHaveBeenLastCalledWith(true);
  expect(screen.getByText(t("settings.persona.editor.dirty"))).toBeTruthy();
  type("# A\nB\nC");
  expect(onDirty).toHaveBeenLastCalledWith(false);
  expect(saveInstruction).not.toHaveBeenCalled();
  expect(note).not.toHaveBeenCalled();
});

test("Save with nothing changed sends nothing", () => {
  const { saveInstruction } = setup();
  expect(saveButton().getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(saveButton());
  expect(saveInstruction).not.toHaveBeenCalled();
});

test("Save makes exactly one call with the restored BOM and CRLF, and the read hash", async () => {
  const { saveInstruction, note, onFile } = setup();
  type("# A\nB\nX");
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.saved"));
  expect(saveInstruction).toHaveBeenCalledTimes(1);
  expect(saveInstruction).toHaveBeenCalledWith(
    "w1",
    3,
    "12:34",
    "AGENTS.md",
    "\uFEFF# A\r\nB\r\nX",
    SHA_READ,
  );
  expect(onFile).toHaveBeenCalledTimes(1);
  expect(onFile).toHaveBeenCalledWith("\uFEFF# A\r\nB\r\nX", SHA_SAVED);
  expect(note).not.toHaveBeenCalled();
  expect(saveButton().getAttribute("aria-disabled")).toBe("true");
});

test("after a save the next Save sends the new hash", async () => {
  const { saveInstruction } = setup();
  type("one");
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.saved"));
  type("two");
  fireEvent.click(saveButton());
  await waitFor(() => expect(saveInstruction).toHaveBeenCalledTimes(2));
  expect(saveInstruction.mock.calls[1]?.[5]).toBe(SHA_SAVED);
  expect(saveInstruction.mock.calls[1]?.[4]).toBe("\uFEFFtwo");
});

test("the textarea is read-only while the save is in flight, and a second click sends nothing", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const { saveInstruction } = setup({
    save: async (...args) => {
      await gate;
      return saved(...args);
    },
  });
  type("x");
  fireEvent.click(saveButton());
  expect(textarea().readOnly).toBe(true);
  fireEvent.click(saveButton());
  release();
  await screen.findByText(t("settings.persona.editor.saved"));
  expect(saveInstruction).toHaveBeenCalledTimes(1);
  expect(textarea().readOnly).toBe(false);
});

test("a no_change answer says so and keeps the draft as the base", async () => {
  const { onFile } = setup({
    save: async () => ({
      operation_id: "op",
      status: "no_change",
      cleanup_pending: false,
      sha256: SHA_SAVED,
    }),
  });
  type("x");
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.no_change"));
  expect(onFile).toHaveBeenCalledWith("\uFEFFx", SHA_SAVED);
  expect(screen.queryByRole("button", { name: t("settings.persona.editor.undo") })).toBeNull();
});

test("Discard asks first; cancel keeps the draft, confirm restores the base, and nothing is sent", () => {
  const { saveInstruction, note } = setup();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  type("changed");
  fireEvent.click(discardButton());
  expect(confirm).toHaveBeenCalledWith(
    tf("settings.persona.editor.discard_confirm", { name: NAME }),
  );
  expect(textarea().value).toBe("changed");
  confirm.mockReturnValue(true);
  fireEvent.click(discardButton());
  expect(textarea().value).toBe("# A\nB\nC");
  expect(saveInstruction).not.toHaveBeenCalled();
  expect(note).not.toHaveBeenCalled();
});

test("a conflict keeps the draft, alerts, and shows the disk text as text", async () => {
  const markup = '<img src="x" onerror="alert(1)">\r\n';
  const { saveInstruction, note, container } = setup({
    save: conflict(),
    note: diskNote(markup),
  });
  type("mine");
  fireEvent.click(saveButton());
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  expect(alertText()).toBe(tf("settings.persona.editor.conflict", { name: NAME }));
  expect(note).toHaveBeenCalledWith("w1", "AGENTS.md");
  expect(textarea().value).toBe("mine");
  expect(container.querySelector("img")).toBeNull();
  const pre = screen.getByRole("group", { name: t("settings.persona.editor.disk_version") });
  expect(pre.tagName).toBe("PRE");
  expect(pre.textContent).toBe(markup);
  expect(saveInstruction).toHaveBeenCalledTimes(1);
});

test("a second click on Save after a conflict sends nothing and keeps the conflict on screen", async () => {
  const { saveInstruction } = setup({ save: conflict(), note: diskNote("# disk\r\nline\r\n") });
  type("mine\nmore");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  saveInstruction.mockImplementation(saved);
  expect(saveButton().getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(saveButton());
  fireEvent.click(saveButton());
  // Typing more does not turn Save back on: the disk version is still unanswered.
  type("mine\nmore\nstill");
  fireEvent.click(saveButton());
  await Promise.resolve();
  expect(saveInstruction).toHaveBeenCalledTimes(1);
  expect(alertText()).toBe(tf("settings.persona.editor.conflict", { name: NAME }));
  expect(
    screen.getByRole("group", { name: t("settings.persona.editor.disk_version") }).textContent,
  ).toBe("# disk\r\nline\r\n");
  expect(textarea().value).toBe("mine\nmore\nstill");
});

test("after a conflict Replace sends the disk hash and the disk's line endings, and says so", async () => {
  const { saveInstruction, onFile } = setup({
    save: conflict(),
    note: diskNote("# disk\r\nline\r\n"),
  });
  type("mine\nmore");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  // The caller's copy of the file follows the disk, not the text this page first read.
  expect(onFile).toHaveBeenLastCalledWith("# disk\r\nline\r\n", SHA_DISK);
  saveInstruction.mockImplementation(saved);
  replaceButton().focus();
  fireEvent.click(replaceButton());
  await screen.findByText(t("settings.persona.editor.replaced"));
  expect(saveInstruction).toHaveBeenCalledTimes(2);
  expect(saveInstruction.mock.calls[1]?.slice(4)).toEqual(["mine\r\nmore", SHA_DISK]);
  expect(onFile).toHaveBeenLastCalledWith("mine\r\nmore", SHA_SAVED);
  expect(
    screen.queryByRole("group", { name: t("settings.persona.editor.disk_version") }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: t("settings.persona.editor.replace") })).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(document.activeElement).toBe(textarea());
  // With the conflict answered, Save is the ordinary save again.
  type("next");
  fireEvent.click(saveButton());
  await waitFor(() => expect(saveInstruction).toHaveBeenCalledTimes(3));
  expect(saveInstruction.mock.calls[2]?.[5]).toBe(SHA_SAVED);
});

test("Replace while its save is in flight sends nothing more", async () => {
  const { saveInstruction } = setup({ save: conflict(), note: diskNote("disk") });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  saveInstruction.mockImplementation(async (...args) => {
    await gate;
    return saved(...args);
  });
  fireEvent.click(replaceButton());
  fireEvent.click(replaceButton());
  fireEvent.click(saveButton());
  release();
  await screen.findByText(t("settings.persona.editor.replaced"));
  expect(saveInstruction).toHaveBeenCalledTimes(2);
});

test("a second conflict on Replace shows the newer disk version and replaces nothing", async () => {
  const note = vi
    .fn<VaultApi["note"]>()
    .mockImplementationOnce(diskNote("first"))
    .mockImplementationOnce(async (_id, ref) => ({
      note: { ref, text: "second", source: "second", sha256: SHA_SAVED },
    }));
  const { saveInstruction } = setup({ save: conflict(), note });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  fireEvent.click(replaceButton());
  await waitFor(() => expect(note).toHaveBeenCalledTimes(2));
  await waitFor(() =>
    expect(
      screen.getByRole("group", { name: t("settings.persona.editor.disk_version") }).textContent,
    ).toBe("second"),
  );
  expect(saveInstruction.mock.calls.map((call) => call[5])).toEqual([SHA_READ, SHA_DISK]);
  expect(textarea().value).toBe("mine");
});

test("after a conflict Discard takes the disk text", async () => {
  const { saveInstruction } = setup({ save: conflict(), note: diskNote("# disk\r\nline\r\n") });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(discardButton());
  expect(textarea().value).toBe("# disk\nline\n");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(
    screen.queryByRole("group", { name: t("settings.persona.editor.disk_version") }),
  ).toBeNull();
  expect(saveInstruction).toHaveBeenCalledTimes(1);
});

test("a disk version that cannot be edited keeps Save off", async () => {
  const { saveInstruction } = setup({ save: conflict(), note: diskNote("a\rb") });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toBe(tf("settings.persona.editor.cannot_edit", { name: NAME }));
  expect(textarea().value).toBe("mine");
  expect(
    screen.getByRole("group", { name: t("settings.persona.editor.disk_version") }),
  ).toBeTruthy();
  fireEvent.click(saveButton());
  expect(saveButton().getAttribute("aria-disabled")).toBe("true");
  expect(saveInstruction).toHaveBeenCalledTimes(1);
});

test("a conflict that holds a pending recovery row does not re-read", async () => {
  const { note } = setup({ save: conflict(true) });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toBe(tf("settings.persona.editor.refusal.recovery", { name: NAME }));
  expect(note).not.toHaveBeenCalled();
});

test.each([
  [
    "is not this file's answer",
    async () => ({ note: { ref: "other.md", text: "x", source: "x", sha256: SHA_DISK } }),
  ],
  [
    "fails",
    async () => {
      throw new Error("invalid_encoding");
    },
  ],
] as const)(
  "a conflict whose re-read %s says the file changed and keeps the draft",
  async (_case, note) => {
    const { saveInstruction } = setup({ save: conflict(), note });
    type("mine");
    fireEvent.click(saveButton());
    await screen.findByRole("alert");
    expect(alertText()).toBe(tf("settings.persona.editor.conflict_unread", { name: NAME }));
    expect(alertText()).toContain("changed on disk");
    expect(textarea().value).toBe("mine");
    expect(screen.queryByRole("button", { name: t("settings.persona.editor.replace") })).toBeNull();
    // Nothing was learned about the disk version, so a later Save still carries the hash first read.
    fireEvent.click(saveButton());
    await waitFor(() => expect(saveInstruction).toHaveBeenCalledTimes(2));
    expect(saveInstruction.mock.calls[1]?.[5]).toBe(SHA_READ);
  },
);

const REFUSALS: [string, string][] = [
  ["read_only_target", "settings.persona.editor.refusal.read_only_windows"],
  ["unsafe_target", "settings.persona.editor.refusal.unsafe"],
  ["unsafe_parent", "settings.persona.editor.refusal.unsafe"],
  ["excluded_path", "settings.persona.editor.refusal.unsafe"],
  ["missing_target", "settings.persona.editor.refusal.missing"],
  ["target_busy", "settings.persona.editor.refusal.busy"],
  ["parent_busy", "settings.persona.editor.refusal.busy"],
  ["stage_busy", "settings.persona.editor.refusal.busy"],
  ["unsupported_filesystem", "settings.persona.editor.refusal.filesystem"],
  ["workspace_changed", "settings.persona.editor.refusal.changed"],
  ["ordinary_workspace_required", "settings.persona.editor.refusal.changed"],
  ["stale_root", "settings.persona.editor.refusal.changed"],
  ["root_unavailable", "settings.persona.editor.refusal.changed"],
  ["recovery_required", "settings.persona.editor.refusal.recovery"],
  ["publication_busy", "settings.persona.editor.refusal.recovery"],
  ["vault_recovery_required", "settings.persona.editor.refusal.operation"],
  ["vault_locked", "settings.persona.editor.refusal.locked"],
  ["protected_instructions", "settings.persona.editor.refusal.protected"],
  ["overloaded", "settings.persona.editor.refusal.overloaded"],
  ["publication_limit", "settings.persona.editor.refusal.overloaded"],
  ["publication_too_large", "settings.persona.editor.refusal.too_large"],
  ["too_large", "settings.persona.editor.refusal.too_large"],
  ["invalid_publication", "settings.persona.editor.refusal.other"],
  ["something_new", "settings.persona.editor.refusal.other"],
];

test.each(REFUSALS)("the %s refusal shows its copy and keeps the draft", async (code, key) => {
  const { saveInstruction } = setup({
    save: async () => {
      throw new VaultWriteError(code, "op");
    },
  });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toBe(tf(key as Parameters<typeof tf>[0], { name: NAME }));
  expect(textarea().value).toBe("mine");
  expect(saveInstruction).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(t("settings.persona.editor.saved"))).toBeNull();
});

test("a failure that is not a VaultWriteError shows the generic copy", async () => {
  setup({
    save: async () => {
      throw new TypeError("network");
    },
  });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toBe(tf("settings.persona.editor.refusal.other", { name: NAME }));
});

test("a read-only refusal names the platform's fix", async () => {
  vi.spyOn(window.navigator, "platform", "get").mockReturnValue("Linux x86_64");
  setup({
    save: async () => {
      throw new VaultWriteError("read_only_target");
    },
  });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toBe(tf("settings.persona.editor.refusal.read_only_posix", { name: NAME }));
});

test.each([
  // A lone CR is written as its code, so the reason the file is read-only can be seen.
  ["a\rb", "line_endings", "a<U+000D>b"],
  ["a\r\nb\nc", "line_endings", "a\r\nb\nc"],
  ["\uFEFFa\u0007", "control", "a<U+0007>"],
  ["a\u0007", "control", "a<U+0007>"],
])("the source %j shows read-only with its reason and no Save", (source, reason, shown) => {
  const { container } = setup({ source });
  expect(container.querySelector("textarea")).toBeNull();
  expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
  const key = `settings.persona.editor.readonly.${reason}` as Parameters<typeof tf>[0];
  expect(screen.getByText(tf(key, { name: NAME }))).toBeTruthy();
  const pre = screen.getByRole("group", {
    name: tf("settings.persona.files.full_text_label", { name: NAME }),
  });
  expect(pre.textContent).toBe(shown);
});

test("content over 2 MiB is refused before any request", async () => {
  const { saveInstruction } = setup();
  type("a".repeat(2 * 1024 * 1024 + 1));
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toBe(tf("settings.persona.editor.refusal.too_large", { name: NAME }));
  expect(saveInstruction).not.toHaveBeenCalled();
});

test.each([
  ["a form feed", "one\ntwo\u000cthree", 2, 7],
  ["half of a surrogate pair", "\ud83d", 1, 0],
])(
  "a draft with %s is refused before any request, with its line, and selected",
  async (_name, text, line, at) => {
    const { saveInstruction } = setup();
    type(text);
    fireEvent.click(saveButton());
    await screen.findByRole("alert");
    expect(alertText()).toBe(tf("settings.persona.editor.refusal.character", { name: NAME, line }));
    expect(alertText()).toContain(`Line ${line} has a character that can't be saved here`);
    expect(saveInstruction).not.toHaveBeenCalled();
    expect(textarea().value).toBe(text);
    expect(document.activeElement).toBe(textarea());
    expect([textarea().selectionStart, textarea().selectionEnd]).toEqual([at, at + 1]);
  },
);

test("the refusal for another note's unfinished operation names where to resolve it, not this file's recovery", async () => {
  setup({
    save: async () => {
      throw new VaultWriteError("vault_recovery_required");
    },
  });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  // The text names the Vault view's heading as that view shows it.
  expect(alertText()).toContain("Unfinished operations");
  expect(alertText()).not.toContain("Saves stopped partway through");
  expect(alertText()).not.toContain("may be missing");
  expect(alertText()).toContain("copy it first");
});

test.each([
  ["vault_locked", "busy right now"],
  ["protected_instructions", "would lock this vault"],
])("the %s refusal says %j and that the edit is kept", async (code, words) => {
  setup({
    save: async () => {
      throw new VaultWriteError(code);
    },
  });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toContain(words);
  expect(alertText()).toContain("our edit is kept");
});

test.each([
  ["workspace_changed", 1],
  ["ordinary_workspace_required", 1],
  ["stale_root", 1],
  ["root_unavailable", 1],
  ["target_busy", 0],
])(
  "the %s refusal asks for the vault to be read again %i time(s), and never to discard the edit",
  async (code, times) => {
    const onStaleScope = vi.fn();
    render(
      <InstructionEditor
        api={{
          note: diskNote(""),
          saveInstruction: async () => {
            throw new VaultWriteError(code);
          },
        }}
        scope={SCOPE}
        name={NAME}
        source={SOURCE}
        sha256={SHA_READ}
        onFile={vi.fn()}
        onDirty={vi.fn()}
        onStaleScope={onStaleScope}
      />,
    );
    type("mine");
    fireEvent.click(saveButton());
    await screen.findByRole("alert");
    expect(onStaleScope).toHaveBeenCalledTimes(times);
    expect(alertText()).not.toContain("Read the files again");
  },
);

test("a later save uses the scope the caller hands in after a stale-vault refusal", async () => {
  const saveInstruction = vi.fn<Save>(async () => {
    throw new VaultWriteError("workspace_changed");
  });
  const props = {
    api: { note: diskNote(""), saveInstruction },
    name: NAME,
    source: SOURCE,
    sha256: SHA_READ,
    onFile: vi.fn(),
    onDirty: vi.fn(),
  };
  const { rerender } = render(<InstructionEditor {...props} scope={SCOPE} />);
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  rerender(<InstructionEditor {...props} scope={{ ...SCOPE, policyRevision: 4 }} />);
  expect(textarea().value).toBe("mine");
  saveInstruction.mockImplementation(saved);
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.saved"));
  expect(saveInstruction.mock.calls.map((call) => call[1])).toEqual([3, 4]);
});

test.each([
  [
    "a right-to-left override",
    "- Ask before \u202E\u2066reading\u2069; never \u2066deleting\u2069\u202C.",
  ],
  ["a zero-width space", "never\u200B delete"],
  ["a Unicode tag", "safe\u{e0061}\u{e0062}"],
])(
  "a draft with %s warns and shows the line with each such character as its code",
  (_name, line) => {
    const { saveInstruction } = setup({ source: "# Rules\nplain\n" });
    const lines = () =>
      screen.queryByRole("group", { name: t("settings.persona.editor.hidden_lines") });
    expect(lines()).toBeNull();
    type(`# Rules\n${line}\nplain`);
    expect(lines()?.textContent).toMatch(/^2: /);
    expect(lines()?.textContent).toMatch(/<U\+[0-9A-F]{4,5}>/);
    expect(lines()?.textContent).not.toMatch(/[\u200B\u202A-\u202E\u2066-\u2069]|\u{e0061}/u);
    expect(lines()?.textContent).not.toContain("plain");
    const warning = screen.getByText(tf("settings.persona.editor.hidden", { count: 20 }));
    expect(box().getAttribute("aria-describedby")).toBe(warning.id);
    // The warning changes nothing in the text: the draft holds the characters as typed.
    expect(textarea().value).toBe(`# Rules\n${line}\nplain`);
    expect(saveInstruction).not.toHaveBeenCalled();
  },
);

test("a save keeps hidden characters byte for byte", async () => {
  const { saveInstruction } = setup({ source: "a\r\n" });
  type("a\u202Eb\u200B\n");
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.saved"));
  expect(saveInstruction.mock.calls[0]?.[4]).toBe("a\u202Eb\u200B\r\n");
});

test("the version on disk after a conflict shows hidden characters as codes, with the warning", async () => {
  setup({ save: conflict(), note: diskNote("never\u202E ask") });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  const disk = screen.getByRole("group", { name: t("settings.persona.editor.disk_version") });
  expect(disk.textContent).toBe("never<U+202E> ask");
  expect(screen.getByText(t("settings.persona.files.hidden"))).toBeTruthy();
});

test("a refused save keeps the text from before the last save on offer", async () => {
  const { saveInstruction } = setup();
  type("one");
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.saved"));
  saveInstruction.mockImplementation(async () => {
    throw new VaultWriteError("target_busy");
  });
  type("two");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  // Back at the saved text, the earlier text can still be put back: the refused save changed nothing.
  type("one");
  fireEvent.click(screen.getByRole("button", { name: t("settings.persona.editor.undo") }));
  expect(textarea().value).toBe("# A\nB\nC");
});

test("unmounting a dirty editor sends nothing and clears the dirty flag", () => {
  const { saveInstruction, note, onDirty, unmount } = setup();
  type("changed");
  expect(onDirty).toHaveBeenLastCalledWith(true);
  unmount();
  expect(onDirty).toHaveBeenLastCalledWith(false);
  expect(saveInstruction).not.toHaveBeenCalled();
  expect(note).not.toHaveBeenCalled();
});

test("Undo puts the text from before the save back as an unsaved draft, with no request", async () => {
  const { saveInstruction, note, onDirty } = setup();
  type("# A\nB\nX");
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.saved"));
  const undo = screen.getByRole("button", { name: t("settings.persona.editor.undo") });
  fireEvent.click(undo);
  expect(textarea().value).toBe("# A\nB\nC");
  expect(onDirty).toHaveBeenLastCalledWith(true);
  expect(screen.getByText(t("settings.persona.editor.dirty"))).toBeTruthy();
  expect(screen.queryByRole("button", { name: t("settings.persona.editor.undo") })).toBeNull();
  expect(saveInstruction).toHaveBeenCalledTimes(1);
  expect(note).not.toHaveBeenCalled();
  // Saving the restored draft is a new, explicit save against the hash the first save returned.
  fireEvent.click(saveButton());
  await waitFor(() => expect(saveInstruction).toHaveBeenCalledTimes(2));
  expect(saveInstruction.mock.calls[1]?.slice(4)).toEqual(["\uFEFF# A\r\nB\r\nC", SHA_SAVED]);
});

test("after Undo the keyboard focus is in the text box", async () => {
  setup();
  type("one");
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.saved"));
  const undo = screen.getByRole("button", { name: t("settings.persona.editor.undo") });
  undo.focus();
  fireEvent.click(undo);
  expect(document.activeElement).toBe(textarea());
});

test("the recovery text names the heading as the Vault view shows it", async () => {
  setup({ save: conflict(true) });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toContain("Saves stopped partway through");
});

test("the recovery text says the file may be missing, and that discarding the record does not bring it back", async () => {
  setup({ save: conflict(true) });
  type("mine");
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  expect(alertText()).toContain(`${NAME} may be missing until VIKI Agent restarts`);
  expect(alertText()).toContain("unless something changed in the meantime (for example the vault");
  expect(alertText()).toContain(`does not bring ${NAME} back; if another program wrote it,`);
});

test("the line-ending text says a lone CR refuses, not only a mix", () => {
  setup({ source: "a\rb" });
  expect(screen.getByText(/lone CR/)).toBeTruthy();
});

test("Undo is not offered once the user types after the save", async () => {
  setup();
  type("one");
  fireEvent.click(saveButton());
  await screen.findByText(t("settings.persona.editor.saved"));
  type("two");
  expect(screen.queryByRole("button", { name: t("settings.persona.editor.undo") })).toBeNull();
});

const proposalProps = (save: Save = saved) => ({
  api: { note: diskNote(""), saveInstruction: save },
  scope: SCOPE,
  name: NAME,
  source: SOURCE,
  sha256: SHA_READ,
  onFile: vi.fn(),
  onDirty: vi.fn(),
});

test("a proposal whose hash matches the base loads into the draft", () => {
  const props = proposalProps();
  const { rerender } = render(<InstructionEditor {...props} proposal={null} />);
  expect(textarea().value).toBe("# A\nB\nC");
  rerender(<InstructionEditor {...props} proposal={{ text: "# Proposed", sha256: SHA_READ }} />);
  expect(textarea().value).toBe("# Proposed");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a proposal whose hash no longer matches the base warns and keeps the draft", () => {
  const props = proposalProps();
  const { rerender } = render(<InstructionEditor {...props} proposal={null} />);
  rerender(<InstructionEditor {...props} proposal={{ text: "# Proposed", sha256: SHA_SAVED }} />);
  expect(alertText()).toBe(t("settings.persona.editor.proposal_stale"));
  expect(textarea().value).toBe("# A\nB\nC");
});

test("a proposal is applied once per object: the same object does not undo a later edit", () => {
  const props = proposalProps();
  const proposal = { text: "# Proposed", sha256: SHA_READ };
  const { rerender } = render(<InstructionEditor {...props} proposal={null} />);
  rerender(<InstructionEditor {...props} proposal={proposal} />);
  expect(textarea().value).toBe("# Proposed");
  type("# Proposed, then edited");
  rerender(<InstructionEditor {...props} proposal={proposal} />);
  expect(textarea().value).toBe("# Proposed, then edited");
});

test("a proposal that arrives while a save is in flight is not applied", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const props = proposalProps(async (...args) => {
    await gate;
    return saved(...args);
  });
  const { rerender } = render(<InstructionEditor {...props} proposal={null} />);
  type("mine");
  fireEvent.click(saveButton());
  expect(textarea().readOnly).toBe(true);
  rerender(<InstructionEditor {...props} proposal={{ text: "# Proposed", sha256: SHA_READ }} />);
  expect(textarea().value).toBe("mine");
  release();
  await screen.findByText(t("settings.persona.editor.saved"));
  expect(textarea().value).toBe("mine");
});

test("only the editor calls saveInstruction, in source outside the tests", () => {
  // `new URL(literal, import.meta.url)` written directly is Vite's asset-URL pattern.
  const here = import.meta.url;
  const srcDir = fileURLToPath(new URL("../../", here)) as string;
  const entries = readdirSync(srcDir, { recursive: true }) as string[];
  const files = entries
    .map((rel) => rel.replace(/\\/g, "/"))
    .filter((rel) => /\.tsx?$/.test(rel) && !rel.split("/").pop()?.includes(".test."))
    .sort();
  expect(files).toContain("components/Settings/InstructionEditor.tsx");
  // Every mention, not only calls, so a destructured alias or a stored reference changes a count.
  // vaultApi.ts: the interface signature and the definition. PersonaPage.tsx: the two prop types,
  // and the four that only pick the function up and pass it on to the editor.
  // InstructionEditor.tsx: its prop type and its one call.
  const mention = /\bsaveInstruction\b/g;
  const counts: Record<string, number> = {};
  for (const rel of files) {
    const found = (readFileSync(srcDir + rel, "utf-8") as string).match(mention);
    if (found !== null) counts[rel] = found.length;
  }
  expect(counts).toEqual({
    "components/Settings/InstructionEditor.tsx": 2,
    "components/Settings/PersonaPage.tsx": 6,
    "state/vaultApi.ts": 2,
  });
});
