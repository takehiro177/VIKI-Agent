import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useLayoutEffect } from "react";
import { afterEach, expect, test, vi } from "vitest";
import type { StarterPlan, WorkspaceJson } from "../../clients/app/shellApi";
import { catalog as settingsCatalog } from "../../i18n/settings";
import { hasUnsavedWork } from "../../state/unsavedWork";
import { type VaultApi, VaultWriteError } from "../../state/vaultApi";
import { workspaceFixture } from "../../testShell";
import { PersonaPage } from "./PersonaPage";
import * as personaModule from "./persona";
import { PERSONA_CLOSE, PERSONA_OPEN, renderPersona } from "./persona";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const BODY = renderPersona({
  name: "VIKI",
  address: "you",
  tone: "calm",
  language: "English",
  proactivity: "coach",
  dos: ["Lead with the answer."],
  donts: ["Pad replies."],
});
const ADD_BODY = renderPersona({
  name: "VIKI",
  address: "you",
  tone: "calm",
  language: "English",
  proactivity: "coach",
  dos: [],
  donts: [],
});
const MARKED = `# AGENTS\n\n${PERSONA_OPEN}\n${BODY}\n${PERSONA_CLOSE}\n`;

type Outcome = string | Error | { note: { ref: string; text: unknown } };
function noteApi(
  agents: Outcome = MARKED,
  claude: Outcome = "@AGENTS.md\n",
): Pick<VaultApi, "note"> {
  return {
    note: vi.fn(async (_id, ref) => {
      const outcome = ref === "AGENTS.md" ? agents : claude;
      if (outcome instanceof Error) throw outcome;
      return typeof outcome === "string" ? { note: { ref, text: outcome } } : outcome;
    }),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
type SaveApi = NonNullable<VaultApi["saveInstruction"]>;
const SHA_READ = "a".repeat(64);
const SHA_SAVED = "b".repeat(64);
const ORDINARY = { root_physical_id: "12:34" };
/** An api whose notes carry the exact source and hash, and whose save succeeds. */
function editApi(agents: Outcome = MARKED, claude: Outcome = "@AGENTS.md\n") {
  const read = noteApi(agents, claude).note;
  return {
    note: vi.fn<VaultApi["note"]>(async (id, ref) => {
      const body = await read(id, ref);
      const note = body.note as { ref: string; text: string };
      return { note: { ...note, source: note.text, sha256: SHA_READ } };
    }),
    saveInstruction: vi.fn<SaveApi>(async () => ({
      operation_id: "op",
      status: "saved",
      cleanup_pending: false,
      sha256: SHA_SAVED,
    })),
  };
}
const editor = (name: "AGENTS.md" | "CLAUDE.md") =>
  screen.findByLabelText(`Edit ${name}`) as Promise<HTMLTextAreaElement>;
function page(
  api: Pick<VaultApi, "note" | "saveInstruction"> | null = noteApi(),
  overrides = {},
  onDraft = vi.fn(),
) {
  const vault = workspaceFixture(overrides);
  return {
    vault,
    onDraft,
    ...render(<PersonaPage api={api} vault={vault} onDraft={onDraft} onBack={vi.fn()} />),
  };
}
async function fullText(name: "AGENTS.md" | "CLAUDE.md") {
  return await screen.findByRole("group", { name: `Full text of ${name}`, hidden: true });
}

test("no vault, nonpersistent vault, and missing API show honest unavailable states", () => {
  const api = noteApi();
  const { rerender } = render(
    <PersonaPage api={api} vault={null} onDraft={vi.fn()} onBack={vi.fn()} />,
  );
  expect(screen.getByText(/Open a vault to see its persona/)).toBeTruthy();
  expect(api.note).not.toHaveBeenCalled();
  rerender(
    <PersonaPage
      api={api}
      vault={workspaceFixture({ persistent: false })}
      onDraft={vi.fn()}
      onBack={vi.fn()}
    />,
  );
  expect(screen.getByText(/Open a vault to see its persona/)).toBeTruthy();
  expect(api.note).not.toHaveBeenCalled();
  rerender(
    <PersonaPage api={null} vault={workspaceFixture()} onDraft={vi.fn()} onBack={vi.fn()} />,
  );
  expect(screen.getByText("Instruction files are unavailable right now.")).toBeTruthy();
  expect(screen.queryByText(/Reading instruction files/)).toBeNull();
});

test("pending read shows the catalogued loading text", () => {
  const api: Pick<VaultApi, "note"> = {
    note: vi.fn(() => new Promise<Record<string, unknown>>(() => {})),
  };
  page(api);
  expect(screen.getByRole("status").textContent).toBe(
    settingsCatalog.en["settings.persona.state.loading"],
  );
});

test("reads AGENTS then CLAUDE and Read again reloads both", async () => {
  const agents = deferred<Record<string, unknown>>();
  const claude = deferred<Record<string, unknown>>();
  const api: Pick<VaultApi, "note"> = {
    note: vi.fn((_id, ref) => (ref === "AGENTS.md" ? agents.promise : claude.promise)),
  };
  const { vault } = page(api);
  expect(api.note).toHaveBeenCalledTimes(1);
  expect(api.note).toHaveBeenCalledWith(vault.workspace_id, "AGENTS.md");
  await act(async () => agents.resolve({ note: { ref: "AGENTS.md", text: MARKED } }));
  expect(api.note).toHaveBeenCalledTimes(2);
  expect(api.note).toHaveBeenLastCalledWith(vault.workspace_id, "CLAUDE.md");
  expect(screen.queryByRole("group", { name: "Full text of AGENTS.md", hidden: true })).toBeNull();
  await act(async () => claude.resolve({ note: { ref: "CLAUDE.md", text: "@./AGENTS.md\n" } }));
  expect((await fullText("AGENTS.md")).textContent).toBe(MARKED);
  expect((await fullText("CLAUDE.md")).textContent).toBe("@./AGENTS.md\n");
  fireEvent.click(screen.getByRole("button", { name: "Read again" }));
  await waitFor(() => expect(api.note).toHaveBeenCalledTimes(4));
  expect((api.note as ReturnType<typeof vi.fn>).mock.calls.map((call) => call[1])).toEqual([
    "AGENTS.md",
    "CLAUDE.md",
    "AGENTS.md",
    "CLAUDE.md",
  ]);
});

test("CLAUDE linkage, missing, and error each keep AGENTS state", async () => {
  for (const [claude, pill, note] of [
    ["@AGENTS.md\n", "Linked", "Found a standalone"],
    ["A prose mention of @AGENTS.md.\n", "Unlinked", "Found no standalone"],
    [new Error("not_found"), "Missing", "No CLAUDE.md"],
    [new Error("unsafe_path"), "Unreadable", "Couldn't read CLAUDE.md (unsafe_path)"],
  ] as const) {
    const view = page(noteApi(MARKED, claude));
    await fullText("AGENTS.md");
    expect(screen.getByText(pill)).toBeTruthy();
    expect(screen.getByText((content) => content.includes(note))).toBeTruthy();
    expect(await screen.findByLabelText("Assistant name in this vault")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    view.unmount();
  }
});

test("AGENTS missing and errors still read and show CLAUDE text", async () => {
  for (const error of [
    "not_found",
    "invalid_encoding",
    "unsafe_path",
    "note_too_large",
    "forbidden",
  ]) {
    const view = page(noteApi(new Error(error), "@AGENTS.md\n"));
    expect((await fullText("CLAUDE.md")).textContent).toBe("@AGENTS.md\n");
    expect(
      screen.getAllByText(
        error === "not_found"
          ? "This vault has no AGENTS.md."
          : new RegExp(
              error === "invalid_encoding"
                ? "decoded as text"
                : error === "unsafe_path"
                  ? "read safely"
                  : error === "note_too_large"
                    ? "too large"
                    : "forbidden",
            ),
      ).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(error === "not_found" ? "Missing" : "Unreadable")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    view.unmount();
  }
});

test("mismatched ref and nontext note fail closed but read second file", async () => {
  for (const agents of [
    { note: { ref: "CLAUDE.md", text: MARKED } },
    { note: { ref: "AGENTS.md", text: 12 } },
  ]) {
    const view = page(noteApi(agents));
    await fullText("CLAUDE.md");
    expect(screen.getAllByText(/invalid_response/).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Assistant name in this vault")).toBeNull();
    view.unmount();
  }
});

test("valid marked form preserves fields, diff, validation, and draft-only message", async () => {
  const onDraft = vi.fn().mockReturnValue(true);
  page(noteApi(), {}, onDraft);
  await fullText("AGENTS.md");
  await screen.findByLabelText("Assistant name in this vault");
  expect((screen.getByLabelText("Assistant name in this vault") as HTMLInputElement).value).toBe(
    "VIKI",
  );
  expect((screen.getByLabelText("Address me as") as HTMLInputElement).value).toBe("you");
  expect(screen.getByText("No change yet.")).toBeTruthy();
  const draft = screen.getByRole("button", { name: "Draft the change in a new chat" });
  expect(draft.getAttribute("aria-disabled")).toBe("true");
  fireEvent.change(screen.getByLabelText("Assistant name in this vault"), {
    target: { value: "Nova" },
  });
  expect(draft.getAttribute("aria-disabled")).toBe("false");
  expect(screen.getByText(/\+ - Name: Nova/)).toBeTruthy();
  fireEvent.click(draft);
  expect(onDraft).toHaveBeenCalledTimes(1);
  expect(onDraft.mock.calls[0]?.[0]).toContain("Replace the lines between");
  expect(screen.getByText(/Drafted in a new chat editor/).textContent).not.toContain("approve");
  fireEvent.change(screen.getByLabelText("Do (one per line)"), {
    target: { value: Array.from({ length: 11 }, (_, i) => `Item ${i}`).join("\n") },
  });
  expect(screen.getByRole("alert").textContent).toContain("Do must have at most 10 items.");
  fireEvent.click(draft);
  expect(onDraft).toHaveBeenCalledTimes(1);
});

test("edited marked body offers repair with exact source; marker problems quote no span", async () => {
  const edited = BODY.replace("- Tone: calm", "- Tone: grumpy");
  const onDraft = vi.fn().mockReturnValue(true);
  const view = page(noteApi(`${PERSONA_OPEN}\n${edited}\n${PERSONA_CLOSE}`), {}, onDraft);
  await fullText("AGENTS.md");
  expect(screen.getByText("Marked")).toBeTruthy();
  expect(view.container.querySelector(".persona-edited-body")?.textContent).toBe(edited);
  expect(screen.queryByLabelText("Assistant name in this vault")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Draft the repair in a new chat" }));
  expect(onDraft.mock.calls[0]?.[0]).toContain(edited);
  view.unmount();
  for (const text of [
    PERSONA_OPEN,
    `${PERSONA_OPEN}\n${PERSONA_OPEN}\n${BODY}\n${PERSONA_CLOSE}`,
    `${PERSONA_CLOSE}\n${BODY}\n${PERSONA_OPEN}`,
  ]) {
    const next = page(noteApi(text), {}, onDraft);
    await fullText("AGENTS.md");
    expect(screen.getByText("Invalid")).toBeTruthy();
    expect(
      screen.getAllByText(/marker lines are missing, repeated, or out of order/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(BODY)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Draft the repair in a new chat" }));
    expect(onDraft.mock.lastCall?.[0]).toContain("Do not guess or quote any source span.");
    next.unmount();
  }
});

test("unmarked heading shows exact section and conversion draft; absent opens seeded add form", async () => {
  const onDraft = vi.fn().mockReturnValue(true);
  const section = "## Persona (VIKI)\nKeep this line.\n";
  const view = page(noteApi(`# AGENTS\n${section}## Other\n`), {}, onDraft);
  await fullText("AGENTS.md");
  expect(screen.getByText("Unmarked")).toBeTruthy();
  expect(view.container.querySelector(".persona-edited-body")?.textContent).toBe(section);
  expect(screen.queryByLabelText("Assistant name in this vault")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Draft the conversion in a new chat" }));
  expect(onDraft.mock.calls[0]?.[0]).toContain(section);
  view.unmount();
  const absentView = page(noteApi("# AGENTS\nNo section.\n"), {}, onDraft);
  await fullText("AGENTS.md");
  fireEvent.click(screen.getByRole("button", { name: "Add a Persona section…" }));
  expect((screen.getByLabelText("Assistant name in this vault") as HTMLInputElement).value).toBe(
    "VIKI",
  );
  expect((screen.getByLabelText("Address me as") as HTMLInputElement).value).toBe("you");
  expect(screen.getByText("+ ## Persona")).toBeTruthy();
  expect(
    Array.from(absentView.container.querySelectorAll(".line-diff-add"), (line) => line.textContent),
  ).toEqual(ADD_BODY.split("\n").map((line) => `+ ${line}`));
  expect(screen.queryByText(/^- /)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Draft adding it in a new chat" }));
  expect(onDraft.mock.lastCall?.[0]).toContain("Insert this complete new block");
  expect(onDraft.mock.lastCall?.[0]).toContain(PERSONA_OPEN);
});

test("add uses optional names and blocks invalid form", async () => {
  const onDraft = vi.fn();
  render(
    <PersonaPage
      api={noteApi("# AGENTS\n")}
      vault={workspaceFixture()}
      assistantName="Aster"
      displayName="Captain"
      onDraft={onDraft}
      onBack={vi.fn()}
    />,
  );
  await fullText("AGENTS.md");
  fireEvent.click(screen.getByRole("button", { name: "Add a Persona section…" }));
  expect((screen.getByLabelText("Assistant name in this vault") as HTMLInputElement).value).toBe(
    "Aster",
  );
  expect((screen.getByLabelText("Address me as") as HTMLInputElement).value).toBe("Captain");
  fireEvent.change(screen.getByLabelText("Assistant name in this vault"), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Draft adding it in a new chat" }));
  expect(onDraft).not.toHaveBeenCalled();
});

test("add form edits reuse the loaded parse; new reads parse their file", async () => {
  const parse = vi.spyOn(personaModule, "readPersona");
  const api = noteApi("# AGENTS\nNo section.\n");
  const view = page(api);
  await fullText("AGENTS.md");
  const firstReadCalls = parse.mock.calls.length;
  expect(firstReadCalls).toBeGreaterThan(0);

  fireEvent.click(screen.getByRole("button", { name: "Add a Persona section…" }));
  fireEvent.change(screen.getByLabelText("Assistant name in this vault"), {
    target: { value: "Nova" },
  });
  fireEvent.change(screen.getByLabelText("Address me as"), {
    target: { value: "friend" },
  });
  expect(parse).toHaveBeenCalledTimes(firstReadCalls);

  fireEvent.click(screen.getByRole("button", { name: "Read again" }));
  await waitFor(() => expect(api.note).toHaveBeenCalledTimes(4));
  await fullText("AGENTS.md");
  const afterReloadCalls = parse.mock.calls.length;
  expect(afterReloadCalls).toBeGreaterThan(firstReadCalls);

  const next = noteApi("# Another file\n");
  view.rerender(<PersonaPage api={next} vault={view.vault} onDraft={vi.fn()} onBack={vi.fn()} />);
  await fullText("AGENTS.md");
  expect(parse.mock.calls.length).toBeGreaterThan(afterReloadCalls);
  expect(parse).toHaveBeenCalledWith("# Another file\n");
});

test("protected vault keeps read-only form and raw files but has no draft controls", async () => {
  const onDraft = vi.fn();
  const view = page(noteApi(), { policy: "protected" }, onDraft);
  await fullText("AGENTS.md");
  const name = (await screen.findByLabelText("Assistant name in this vault")) as HTMLInputElement;
  expect(name.getAttribute("aria-disabled")).toBe("true");
  fireEvent.change(name, { target: { value: "Nova" } });
  expect(name.value).toBe("VIKI");
  expect(screen.getByText(/This vault is protected/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Draft|Add a Persona/ })).toBeNull();
  expect(onDraft).not.toHaveBeenCalled();
  view.unmount();
  for (const text of ["# AGENTS\n", "## Persona\nText\n", PERSONA_OPEN]) {
    const next = page(noteApi(text), { policy: "protected" }, onDraft);
    await fullText("AGENTS.md");
    expect(screen.queryByRole("button", { name: /Draft|Add a Persona/ })).toBeNull();
    expect(screen.queryByLabelText("Assistant name in this vault")).toBeNull();
    next.unmount();
  }
});

test("vault switch hides A's full text while B's reads are pending", async () => {
  const pendingB = deferred<Record<string, unknown>>();
  const api: Pick<VaultApi, "note"> = {
    note: vi.fn((id, ref) =>
      id === "B"
        ? pendingB.promise
        : Promise.resolve({ note: { ref, text: `${MARKED}\nprivate-A-marker` } }),
    ),
  };
  const a = workspaceFixture({ workspace_id: "A" });
  const b = workspaceFixture({ workspace_id: "B" });
  let host: HTMLElement | null = null;
  let beforeEffects = "";
  function Probe({ vault }: { vault: WorkspaceJson }) {
    useLayoutEffect(() => {
      if (vault.workspace_id === "B") beforeEffects = host?.textContent ?? "";
    }, [vault]);
    return <PersonaPage api={api} vault={vault} onDraft={vi.fn()} onBack={vi.fn()} />;
  }
  const view = render(<Probe vault={a} />);
  host = view.container;
  await fullText("AGENTS.md");
  expect((await fullText("AGENTS.md")).textContent).toContain("private-A-marker");
  view.rerender(<Probe vault={b} />);
  expect(beforeEffects).not.toContain("private-A-marker");
  expect(screen.queryAllByText(/private-A-marker/)).toHaveLength(0);
  expect(screen.queryByRole("group", { name: "Full text of AGENTS.md", hidden: true })).toBeNull();
  expect(api.note).toHaveBeenLastCalledWith("B", "AGENTS.md");
  await act(async () => pendingB.resolve({ note: { ref: "AGENTS.md", text: "# B" } }));
});

test("late old read after vault switch cannot replace new vault content", async () => {
  const old = deferred<Record<string, unknown>>();
  const api: Pick<VaultApi, "note"> = {
    note: vi.fn((id, ref) =>
      id === "A"
        ? old.promise
        : Promise.resolve({ note: { ref, text: ref === "AGENTS.md" ? "# B" : "B CLAUDE" } }),
    ),
  };
  const { rerender } = render(
    <PersonaPage
      api={api}
      vault={workspaceFixture({ workspace_id: "A" })}
      onDraft={vi.fn()}
      onBack={vi.fn()}
    />,
  );
  rerender(
    <PersonaPage
      api={api}
      vault={workspaceFixture({ workspace_id: "B" })}
      onDraft={vi.fn()}
      onBack={vi.fn()}
    />,
  );
  expect((await fullText("AGENTS.md")).textContent).toBe("# B");
  await act(async () => old.resolve({ note: { ref: "AGENTS.md", text: "private-A-marker" } }));
  expect(screen.queryByText(/private-A-marker/)).toBeNull();
  expect(api.note).not.toHaveBeenCalledWith("A", "CLAUDE.md");
});

test("API and root changes hide old file text before replacement reads resolve", async () => {
  const pending = deferred<Record<string, unknown>>();
  const first = noteApi(`${MARKED}\nold-api-marker`);
  const second: Pick<VaultApi, "note"> = { note: vi.fn(() => pending.promise) };
  const vault = workspaceFixture();
  const { rerender } = render(
    <PersonaPage api={first} vault={vault} onDraft={vi.fn()} onBack={vi.fn()} />,
  );
  expect((await fullText("AGENTS.md")).textContent).toContain("old-api-marker");
  rerender(<PersonaPage api={second} vault={vault} onDraft={vi.fn()} onBack={vi.fn()} />);
  expect(screen.queryAllByText(/old-api-marker/)).toHaveLength(0);
  expect(second.note).toHaveBeenCalledWith(vault.workspace_id, "AGENTS.md");
  rerender(
    <PersonaPage
      api={second}
      vault={{ ...vault, root: "/new/root" }}
      onDraft={vi.fn()}
      onBack={vi.fn()}
    />,
  );
  expect(screen.queryAllByText(/old-api-marker/)).toHaveLength(0);
  expect(second.note).toHaveBeenCalledTimes(2);
  await act(async () => pending.resolve({ note: { ref: "AGENTS.md", text: "# New root" } }));
});

test.each([
  ["D:\\VIKI\\Vaults\\My notes", "\\"],
  ["D:\\VIKI\\Vaults\\My notes\\", "\\"],
  ["/home/example/notes", "/"],
  ["/home/example/notes/", "/"],
])("displays and copies exact native paths for %s", async (root, separator) => {
  const copy = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: copy } });
  page(noteApi(), { root });
  await fullText("AGENTS.md");
  for (const name of ["AGENTS.md", "CLAUDE.md"]) {
    const expected = `${root}${root.endsWith(separator) ? "" : separator}${name}`;
    expect(screen.getByText(expected).tagName).toBe("DD");
    fireEvent.click(screen.getByRole("button", { name: `Copy ${name} path` }));
    expect(copy).toHaveBeenLastCalledWith(expected);
  }
});

test("file text shows open in a scroll box, with no disclosure", async () => {
  const view = page();
  expect((await fullText("AGENTS.md")).textContent).toBe(MARKED);
  expect(view.container.querySelector("details")).toBeNull();
  expect(screen.queryByText("Show full text")).toBeNull();
  expect(screen.getAllByText(/\d+ lines?/).length).toBe(2);
});

test("an ordinary vault with saveInstruction shows both files in editors, with the warning", async () => {
  const api = editApi();
  const view = page(api, ORDINARY);
  expect((await editor("AGENTS.md")).value).toBe(MARKED);
  expect((await editor("CLAUDE.md")).value).toBe("@AGENTS.md\n");
  expect(screen.getByText(settingsCatalog.en["settings.persona.editor.warning"])).toBeTruthy();
  expect(screen.getByText(/My notes.AGENTS\.md$/).tagName).toBe("DD");
  expect(screen.getByRole("button", { name: "Save AGENTS.md" })).toBeTruthy();
  expect(view.container.querySelector("details")).toBeNull();
  expect(api.saveInstruction).not.toHaveBeenCalled();
});

test.each([
  ["protected", { policy: "protected" }],
  ["existing_readonly", { policy: "existing_readonly" }],
  ["no physical id", { root_physical_id: undefined }],
  ["incomplete bootstrap", { ...ORDINARY, bootstrap: "pending" }],
])("%s vault shows the text and no editor", async (_label, overrides) => {
  const api = editApi();
  page(api, { ...ORDINARY, ...overrides });
  expect((await fullText("AGENTS.md")).textContent).toBe(MARKED);
  expect((await fullText("CLAUDE.md")).textContent).toBe("@AGENTS.md\n");
  expect(screen.queryByLabelText(/^Edit /)).toBeNull();
  expect(screen.queryByRole("button", { name: /^Save / })).toBeNull();
  expect(screen.queryByText(settingsCatalog.en["settings.persona.editor.warning"])).toBeNull();
});

test("without saveInstruction there is no editor", async () => {
  // Notes that carry the source and hash an editor needs, so only the missing save gates it.
  const { note } = editApi();
  page({ note }, ORDINARY);
  expect((await fullText("AGENTS.md")).textContent).toBe(MARKED);
  expect(screen.queryByLabelText(/^Edit /)).toBeNull();
  expect(screen.queryByText(settingsCatalog.en["settings.persona.editor.warning"])).toBeNull();
});

test("a missing or unreadable file gets no editor, the other file keeps its own", async () => {
  for (const error of ["not_found", "note_too_large"]) {
    const view = page(editApi(new Error(error)), ORDINARY);
    expect((await editor("CLAUDE.md")).value).toBe("@AGENTS.md\n");
    expect(screen.queryByLabelText("Edit AGENTS.md")).toBeNull();
    view.unmount();
  }
});

test("saving AGENTS.md re-seeds the form without a re-read and keeps a CLAUDE.md draft", async () => {
  const api = editApi();
  page(api, ORDINARY);
  const agents = await editor("AGENTS.md");
  const claude = await editor("CLAUDE.md");
  fireEvent.change(claude, { target: { value: "@AGENTS.md\nmore\n" } });
  fireEvent.change(agents, { target: { value: MARKED.replace("- Name: VIKI", "- Name: Nova") } });
  expect(hasUnsavedWork()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Save AGENTS.md" }));
  await screen.findByText("Saved.");
  expect(api.saveInstruction).toHaveBeenCalledTimes(1);
  expect(api.saveInstruction.mock.calls[0]?.[3]).toBe("AGENTS.md");
  expect(api.saveInstruction.mock.calls[0]?.[5]).toBe(SHA_READ);
  expect(api.note).toHaveBeenCalledTimes(2);
  await waitFor(() =>
    expect((screen.getByLabelText("Assistant name in this vault") as HTMLInputElement).value).toBe(
      "Nova",
    ),
  );
  expect(((await editor("CLAUDE.md")) as HTMLTextAreaElement).value).toBe("@AGENTS.md\nmore\n");
  expect(hasUnsavedWork()).toBe(true);
});

test.each(["saved", "no_change"] as const)(
  "an AGENTS.md save (%s) keeps unsent Persona form edits that the save did not touch",
  async (status) => {
    const api = editApi();
    api.saveInstruction.mockResolvedValue({
      operation_id: "op",
      status,
      cleanup_pending: false,
      sha256: SHA_SAVED,
    });
    page(api, ORDINARY);
    const agents = await editor("AGENTS.md");
    const address = (await screen.findByLabelText("Address me as")) as HTMLInputElement;
    fireEvent.change(address, { target: { value: "boss" } });
    fireEvent.change(agents, { target: { value: `${MARKED}\nOutside the persona.\n` } });
    fireEvent.click(screen.getByRole("button", { name: "Save AGENTS.md" }));
    await screen.findByText(status === "saved" ? "Saved." : "Nothing changed on disk.");
    await waitFor(() =>
      expect((screen.getByLabelText("Address me as") as HTMLInputElement).value).toBe("boss"),
    );
    // The kept edit is still a change against the saved file.
    expect(screen.getByRole("button", { name: "Draft the change in a new chat" })).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Draft the change in a new chat" })
        .getAttribute("aria-disabled"),
    ).toBe("false");
  },
);

test("an AGENTS.md save keeps the open add form's typed values while the file has no Persona section", async () => {
  const none = "# AGENTS\nNo section.\n";
  const api = editApi(none);
  page(api, ORDINARY);
  const agents = await editor("AGENTS.md");
  fireEvent.click(screen.getByRole("button", { name: "Add a Persona section…" }));
  fireEvent.change(screen.getByLabelText("Address me as"), { target: { value: "boss" } });
  fireEvent.change(agents, { target: { value: `${none}More.\n` } });
  fireEvent.click(screen.getByRole("button", { name: "Save AGENTS.md" }));
  await screen.findByText("Saved.");
  await waitFor(() =>
    expect((screen.getByLabelText("Address me as") as HTMLInputElement).value).toBe("boss"),
  );
});

test("an AGENTS.md save that changes the Persona section re-seeds an edited form from the file", async () => {
  const api = editApi();
  page(api, ORDINARY);
  const agents = await editor("AGENTS.md");
  fireEvent.change(await screen.findByLabelText("Address me as"), { target: { value: "boss" } });
  fireEvent.change(agents, { target: { value: MARKED.replace("- Name: VIKI", "- Name: Nova") } });
  fireEvent.click(screen.getByRole("button", { name: "Save AGENTS.md" }));
  await screen.findByText("Saved.");
  await waitFor(() =>
    expect((screen.getByLabelText("Address me as") as HTMLInputElement).value).toBe("you"),
  );
  expect((screen.getByLabelText("Assistant name in this vault") as HTMLInputElement).value).toBe(
    "Nova",
  );
});

test("Read again re-seeds an edited form from the file", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const api = editApi();
  page(api, ORDINARY);
  fireEvent.change(await screen.findByLabelText("Address me as"), { target: { value: "boss" } });
  fireEvent.click(screen.getByRole("button", { name: "Read again" }));
  await waitFor(() => expect(api.note).toHaveBeenCalledTimes(4));
  await waitFor(() =>
    expect((screen.getByLabelText("Address me as") as HTMLInputElement).value).toBe("you"),
  );
});

test("the warning says changes reach chats started or reopened after the save", () => {
  expect(settingsCatalog.en["settings.persona.editor.warning"]).toContain(
    "started or reopened after the save",
  );
});

test("a dirty editor counts as unsaved work and Read again asks first", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const api = editApi();
  page(api, ORDINARY);
  expect(hasUnsavedWork()).toBe(false);
  fireEvent.change(await editor("CLAUDE.md"), { target: { value: "draft" } });
  expect(hasUnsavedWork()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Read again" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(api.note).toHaveBeenCalledTimes(2);
  expect(((await editor("CLAUDE.md")) as HTMLTextAreaElement).value).toBe("draft");
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Read again" }));
  await waitFor(() => expect(api.note).toHaveBeenCalledTimes(4));
  expect(((await editor("CLAUDE.md")) as HTMLTextAreaElement).value).toBe("@AGENTS.md\n");
  expect(hasUnsavedWork()).toBe(false);
});

test("a save refused for an old registration reads the vault again, keeps the edit, and saves with the new revision", async () => {
  const api = editApi();
  api.saveInstruction.mockRejectedValueOnce(new VaultWriteError("workspace_changed"));
  const onRefreshVault = vi.fn();
  const vault = workspaceFixture(ORDINARY);
  const props = { api, onDraft: vi.fn(), onBack: vi.fn(), onRefreshVault };
  const { rerender } = render(<PersonaPage {...props} vault={vault} />);
  fireEvent.change(await editor("CLAUDE.md"), { target: { value: "draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save CLAUDE.md" }));
  await screen.findByRole("alert");
  expect(onRefreshVault).toHaveBeenCalledTimes(1);
  rerender(
    <PersonaPage {...props} vault={{ ...vault, policy_revision: vault.policy_revision + 1 }} />,
  );
  expect((await editor("CLAUDE.md")).value).toBe("draft");
  fireEvent.click(screen.getByRole("button", { name: "Save CLAUDE.md" }));
  await screen.findByText("Saved.");
  expect(api.saveInstruction.mock.calls.map((call) => call[1])).toEqual([
    vault.policy_revision,
    vault.policy_revision + 1,
  ]);
});

test("a fresh listing that finds the vault unavailable keeps an editor that holds typed text", async () => {
  const api = editApi();
  const vault = workspaceFixture(ORDINARY);
  const props = { api, onDraft: vi.fn(), onBack: vi.fn() };
  const { rerender } = render(<PersonaPage {...props} vault={vault} />);
  fireEvent.change(await editor("CLAUDE.md"), { target: { value: "draft" } });
  rerender(<PersonaPage {...props} vault={{ ...vault, root_available: false }} />);
  expect((await editor("CLAUDE.md")).value).toBe("draft");
  // With nothing typed, the page follows the listing and offers no editor.
  cleanup();
  const { rerender: again } = render(<PersonaPage {...props} vault={vault} />);
  await editor("CLAUDE.md");
  again(<PersonaPage {...props} vault={{ ...vault, root_available: false }} />);
  await waitFor(() => expect(screen.queryByLabelText("Edit CLAUDE.md")).toBeNull());
});

test("after a conflict the page's own copy of the file is the version on disk", async () => {
  const api = editApi();
  api.saveInstruction.mockResolvedValueOnce({
    operation_id: "op",
    status: "conflict",
    cleanup_pending: false,
    sha256: null,
  });
  page(api, ORDINARY);
  await editor("AGENTS.md");
  const disk = MARKED.replace("- Name: VIKI", "- Name: Disk");
  api.note.mockImplementation(async (_id, ref) => ({
    note: { ref, text: disk, source: disk, sha256: "d".repeat(64) },
  }));
  fireEvent.change(await editor("AGENTS.md"), { target: { value: `${MARKED}mine\n` } });
  fireEvent.click(screen.getByRole("button", { name: "Save AGENTS.md" }));
  await screen.findByRole("alert");
  // The Persona form reads the file as it is now, not as this page first read it.
  await waitFor(() =>
    expect((screen.getByLabelText("Assistant name in this vault") as HTMLInputElement).value).toBe(
      "Disk",
    ),
  );
});

test("next launch names selected vault while reading running vault", async () => {
  const api = noteApi();
  const vault = workspaceFixture({ name: "Running" });
  render(
    <PersonaPage api={api} vault={vault} nextLaunch="Next" onDraft={vi.fn()} onBack={vi.fn()} />,
  );
  await fullText("AGENTS.md");
  expect(
    screen.getByText("Selected for next launch: Next. Restart VIKI Agent to switch."),
  ).toBeTruthy();
  expect(
    screen.getByText("These are the instruction files in the running vault, Running."),
  ).toBeTruthy();
  expect(api.note).toHaveBeenCalledWith(vault.workspace_id, "AGENTS.md");
  expect(screen.queryByRole("alert")).toBeNull();
});

function starterPlanFixture(overrides: Partial<StarterPlan> = {}): StarterPlan {
  return {
    state: "available",
    from_version: 1,
    to_version: 2,
    notify: false,
    items: [],
    agents: null,
    ...overrides,
  };
}

test("without a starterPlan prop there is no Starter update section", async () => {
  page(editApi(), ORDINARY);
  await editor("AGENTS.md");
  expect(screen.queryByText(settingsCatalog.en["settings.starter.heading"])).toBeNull();
});

test("a starterPlan prop loads this vault's plan and shows it above the intro", async () => {
  const starterPlan = vi.fn(async () => starterPlanFixture());
  const vault = workspaceFixture();
  render(
    <PersonaPage
      api={noteApi()}
      vault={vault}
      starterPlan={starterPlan}
      onDraft={vi.fn()}
      onBack={vi.fn()}
    />,
  );
  await fullText("AGENTS.md");
  expect(starterPlan).toHaveBeenCalledWith(vault.workspace_id);
  const heading = await screen.findByText(settingsCatalog.en["settings.starter.heading"]);
  const intro = screen.getByText(/instruction files are shown in full below/);
  expect(heading.compareDocumentPosition(intro) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("a non-ordinary vault's Starter update gets no onReviewAgents, even with a proposal", async () => {
  const starterPlan = vi.fn(async () =>
    starterPlanFixture({ agents: { proposal: "new text", sha256: SHA_READ } }),
  );
  render(
    <PersonaPage
      api={noteApi()}
      vault={workspaceFixture({ policy: "existing_readonly" })}
      starterPlan={starterPlan}
      onDraft={vi.fn()}
      onBack={vi.fn()}
    />,
  );
  await fullText("AGENTS.md");
  await screen.findByText(settingsCatalog.en["settings.starter.heading"]);
  expect(screen.queryByText(settingsCatalog.en["settings.starter.open_agents"])).toBeNull();
});

test("an existing_readonly vault has no editor for an AGENTS.md rules change, so its hand-off carries the change instead", async () => {
  const onDraft = vi.fn().mockReturnValue(true);
  const starterPlan = vi.fn(async () =>
    starterPlanFixture({
      items: [
        {
          path: "AGENTS.md",
          action: "editor",
          diff: "--- a/AGENTS.md\n+++ b/AGENTS.md",
          text: null,
        },
      ],
    }),
  );
  render(
    <PersonaPage
      api={noteApi()}
      vault={workspaceFixture({ policy: "existing_readonly" })}
      starterPlan={starterPlan}
      onDraft={onDraft}
      onBack={vi.fn()}
    />,
  );
  await fullText("AGENTS.md");
  await screen.findByText(settingsCatalog.en["settings.starter.heading"]);
  expect(screen.queryByText(settingsCatalog.en["settings.starter.open_agents"])).toBeNull();
  fireEvent.click(screen.getByText(settingsCatalog.en["settings.starter.update_in_chat"]));
  expect(onDraft).toHaveBeenCalledTimes(1);
  expect(onDraft.mock.calls[0]?.[0]).toContain("### `AGENTS.md`");
});

test("an ordinary vault keeps its own AGENTS.md editor button and leaves the rules change out of the hand-off", async () => {
  const onDraft = vi.fn().mockReturnValue(true);
  const api = editApi();
  const starterPlan = vi.fn(async () =>
    starterPlanFixture({
      items: [
        {
          path: "AGENTS.md",
          action: "editor",
          diff: "--- a/AGENTS.md\n+++ b/AGENTS.md",
          text: null,
        },
      ],
      notify: true,
      agents: { proposal: "# Updated AGENTS", sha256: SHA_READ },
    }),
  );
  render(
    <PersonaPage
      api={api}
      vault={workspaceFixture(ORDINARY)}
      starterPlan={starterPlan}
      onDraft={onDraft}
      onBack={vi.fn()}
    />,
  );
  await editor("AGENTS.md");
  expect(await screen.findByText(settingsCatalog.en["settings.starter.open_agents"])).toBeTruthy();
  fireEvent.click(screen.getByText(settingsCatalog.en["settings.starter.update_in_chat"]));
  expect(onDraft).toHaveBeenCalledTimes(1);
  expect(onDraft.mock.calls[0]?.[0]).not.toContain("### `AGENTS.md`");
});

test("the Open AGENTS.md button loads the Starter update's proposal into the editor", async () => {
  const api = editApi();
  const starterPlan = vi.fn(async () =>
    starterPlanFixture({ agents: { proposal: "# Updated AGENTS", sha256: SHA_READ } }),
  );
  render(
    <PersonaPage
      api={api}
      vault={workspaceFixture(ORDINARY)}
      starterPlan={starterPlan}
      onDraft={vi.fn()}
      onBack={vi.fn()}
    />,
  );
  await editor("AGENTS.md");
  fireEvent.click(
    await screen.findByRole("button", { name: settingsCatalog.en["settings.starter.open_agents"] }),
  );
  expect((await editor("AGENTS.md")).value).toBe("# Updated AGENTS");
  expect(screen.getByText(settingsCatalog.en["settings.starter.agents_status"])).toBeTruthy();
});

test("an AGENTS.md save changes the Starter update's refresh key and reloads the plan", async () => {
  const api = editApi();
  const starterPlan = vi.fn(async () => starterPlanFixture({ state: "current", to_version: 1 }));
  render(
    <PersonaPage
      api={api}
      vault={workspaceFixture(ORDINARY)}
      starterPlan={starterPlan}
      onDraft={vi.fn()}
      onBack={vi.fn()}
    />,
  );
  await editor("AGENTS.md");
  await screen.findByText(settingsCatalog.en["settings.starter.current"].replace("{to}", "1"));
  const callsBeforeSave = starterPlan.mock.calls.length;
  fireEvent.change(await editor("AGENTS.md"), { target: { value: `${MARKED}more\n` } });
  fireEvent.click(screen.getByRole("button", { name: "Save AGENTS.md" }));
  await screen.findByText("Saved.");
  await waitFor(() => expect(starterPlan.mock.calls.length).toBeGreaterThan(callsBeforeSave));
});
