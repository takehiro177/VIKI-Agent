import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { type ComponentProps, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { hasAnyUnsavedWork, hasUnsavedWork } from "../../state/unsavedWork";
import type { VaultApi } from "../../state/vaultApi";
import { BotMemoryDialog } from "./BotMemoryDialog";
import { replacementRequest } from "./BotMemoryReplacement";

const WORKSPACE = "11111111-1111-4111-8111-111111111111";
const BOT = "22222222-2222-4222-8222-222222222222";
const folder = `bot-memory/${BOT}`;

afterEach(cleanup);

const EMPTY = "Mori has no auto memory notes.";

function listing() {
  return {
    entries: [
      { ref: `${folder}/MEMORY.md`, title: "MEMORY.md", modified: 1 },
      { ref: `${folder}/topic.md`, title: "Topic", modified: 2 },
    ],
    partial: false,
    partial_reasons: [],
    omitted: 0,
  };
}

function note(ref: string, text: string) {
  return {
    note: {
      ref,
      title: ref.endsWith("MEMORY.md") ? "MEMORY.md" : "Topic",
      text,
      source: text,
      byte_size: new TextEncoder().encode(text).length,
      sha256: "a".repeat(64),
      frontmatter: {},
      frontmatter_error: null,
    },
    resolved_links: [],
  };
}

function metadata(refs: readonly string[]) {
  return {
    items: refs.map((ref) => ({
      ref,
      state: "absent",
      reason: null,
      read_bytes: 0,
      display_metadata: {
        state: "absent",
        frontmatter_state: "absent",
        heading_state: "absent",
        display_title: null,
        title_source: null,
        tags: [],
        type: null,
        status: null,
      },
    })),
    partial: false,
    partial_reasons: [],
    read_bytes: 0,
    omitted: 0,
  };
}

function vault(overrides: Partial<VaultApi> = {}): VaultApi {
  return {
    entries: vi.fn(async () => listing()),
    metadata: vi.fn(async (_workspace, refs) => metadata(refs)),
    note: vi.fn(async (_workspace, ref) => note(ref, `# ${ref}\n\nBody`)),
    backlinks: vi.fn(),
    history: vi.fn(),
    bookmarks: vi.fn(),
    addBookmark: vi.fn(),
    removeBookmark: vi.fn(),
    createNote: vi.fn(),
    saveNote: vi.fn(),
    recovery: vi.fn(),
    ...overrides,
  } as unknown as VaultApi;
}

function renderDialog(
  api: VaultApi,
  onClose = vi.fn(),
  onOpenInVault = vi.fn(),
  extra: Partial<ComponentProps<typeof BotMemoryDialog>> = {},
) {
  function Harness() {
    const [open, setOpen] = useState(true);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)} data-testid="opener">
          Open memory
        </button>
        {open ? (
          <BotMemoryDialog
            api={api}
            workspaceId={WORKSPACE}
            bot={{ bot_id: BOT, name: "Mori" }}
            onClose={() => {
              onClose();
              setOpen(false);
            }}
            onOpenInVault={onOpenInVault}
            onOpenUrl={vi.fn()}
            {...extra}
          />
        ) : null}
      </>
    );
  }
  return render(<Harness />);
}

describe("BotMemoryDialog", () => {
  it("lists exact refs, reads selected note only, opens selected ref, refreshes, and closes on Escape", async () => {
    const api = vault();
    const onClose = vi.fn();
    const onOpenInVault = vi.fn();
    renderDialog(api, onClose, onOpenInVault);

    expect((await screen.findByRole("navigation")).textContent).toContain(`${folder}/MEMORY.md`);
    expect(await screen.findByText("Body")).toBeTruthy();
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(api.note).toHaveBeenCalledTimes(1);
    expect(api.note).toHaveBeenCalledWith(
      WORKSPACE,
      `${folder}/MEMORY.md`,
      expect.any(AbortSignal),
    );
    fireEvent.click(screen.getByRole("button", { name: "Open in Vault" }));
    expect(onOpenInVault).toHaveBeenCalledWith(`${folder}/MEMORY.md`);
    fireEvent.click(screen.getByRole("button", { name: "Refresh memory" }));
    await waitFor(() => expect(api.entries).toHaveBeenCalledTimes(2));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("ignores a late note response after selection changes", async () => {
    let finishFirst!: (value: Record<string, unknown>) => void;
    const api = vault({
      note: vi.fn((_workspace: string, ref: string) =>
        ref.endsWith("MEMORY.md")
          ? new Promise<Record<string, unknown>>((resolve) => {
              finishFirst = resolve;
            })
          : Promise.resolve(note(ref, "# Topic\n\nCurrent body")),
      ),
    });
    renderDialog(api);
    const topic = `${folder}/topic.md`;

    fireEvent.click(await screen.findByRole("button", { name: new RegExp(topic) }));
    expect(await screen.findByText("Current body")).toBeTruthy();
    await act(async () => {
      finishFirst(note(`${folder}/MEMORY.md`, "# MEMORY.md\n\nStale body"));
    });
    expect(screen.queryByText("Stale body")).toBeNull();
    expect(screen.getByText("Current body")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  });

  it("ignores a late note response after workspace changes", async () => {
    const otherWorkspace = "33333333-3333-4333-8333-333333333333";
    let finishFirst!: (value: Record<string, unknown>) => void;
    const api = vault({
      note: vi.fn((workspace: string, ref: string) =>
        workspace === WORKSPACE
          ? new Promise<Record<string, unknown>>((resolve) => {
              finishFirst = resolve;
            })
          : Promise.resolve(note(ref, "# Topic\n\nSecond workspace")),
      ),
    });
    const props = {
      api,
      bot: { bot_id: BOT, name: "Mori" },
      onClose: vi.fn(),
      onOpenInVault: vi.fn(),
      onOpenUrl: vi.fn(),
    };
    const view = render(<BotMemoryDialog {...props} workspaceId={WORKSPACE} />);
    expect(await screen.findByRole("navigation")).toBeTruthy();
    view.rerender(<BotMemoryDialog {...props} workspaceId={otherWorkspace} />);
    expect(await screen.findByText("Second workspace")).toBeTruthy();
    await act(async () => {
      finishFirst(note(`${folder}/MEMORY.md`, "# MEMORY.md\n\nStale workspace"));
    });
    expect(screen.queryByText("Stale workspace")).toBeNull();
    view.unmount();
    await act(async () => {});
  });

  it("updates selected row title from full-note metadata without reordering rows", async () => {
    const indexRef = `${folder}/MEMORY.md`;
    const api = vault({
      note: vi.fn(async () => ({
        ...note(indexRef, "# MEMORY.md\n\nBody"),
        note: {
          ...note(indexRef, "# MEMORY.md\n\nBody").note,
          display_metadata: {
            state: "ready",
            frontmatter_state: "ready",
            heading_state: "absent",
            display_title: "Authored index title",
            title_source: "title",
            tags: [],
            type: null,
            status: null,
          },
        },
      })),
    });
    renderDialog(api);
    const navigation = await screen.findByRole("navigation");
    const orderBefore = Array.from(
      navigation.querySelectorAll("li code"),
      (item) => item.textContent,
    );
    expect((await screen.findAllByText("Authored index title")).length).toBeGreaterThan(0);
    const orderAfter = Array.from(
      navigation.querySelectorAll("li code"),
      (item) => item.textContent,
    );
    expect(orderAfter).toEqual(orderBefore);
    expect(navigation.textContent).toContain(`${folder}/topic.md`);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  });

  it("applies a refresh that finishes after the user picked another file and keeps that pick", async () => {
    let finishRefresh!: (value: ReturnType<typeof listing>) => void;
    const entries = vi
      .fn()
      .mockImplementationOnce(async () => listing())
      .mockImplementationOnce(
        () => new Promise<ReturnType<typeof listing>>((resolve) => (finishRefresh = resolve)),
      );
    const api = vault({ entries });
    renderDialog(api);
    const topic = `${folder}/topic.md`;
    const added = `${folder}/added.md`;
    await screen.findByRole("navigation");

    fireEvent.click(screen.getByRole("button", { name: "Refresh memory" }));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(topic) }));
    await act(async () => {
      finishRefresh({
        ...listing(),
        entries: [...listing().entries, { ref: added, title: "Added", modified: 3 }],
      });
    });

    const navigation = screen.getByRole("navigation");
    expect(navigation.textContent).toContain(added);
    expect(
      screen.getByRole("button", { name: new RegExp(topic) }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(await screen.findByText("Body")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  });

  it.each([
    ["partial", true, ["depth_limit"], 0],
    ["omitted", false, [], 1],
  ])(
    "does not call an incomplete empty scan empty when %s",
    async (_kind, partial, reasons, omitted) => {
      const api = vault({
        entries: vi.fn(async () => ({
          entries: [],
          partial,
          partial_reasons: reasons,
          omitted,
        })),
      });
      renderDialog(api);

      expect(await screen.findByText("This list may be incomplete.")).toBeTruthy();
      expect(screen.queryByText(EMPTY)).toBeNull();
      fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    },
  );

  it("shows empty only for a complete empty scan", async () => {
    const api = vault({
      entries: vi.fn(async () => ({
        entries: [],
        partial: false,
        partial_reasons: [],
        omitted: 0,
      })),
    });
    renderDialog(api);

    expect(await screen.findByText(EMPTY)).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  });

  it.each([
    [
      "swapped ref",
      (ref: string) => ({
        ...note(ref, "body"),
        note: { ...note(ref, "body").note, ref: `${folder}/other.md` },
      }),
    ],
    [
      "oversized title",
      (ref: string) => ({
        ...note(ref, "body"),
        note: { ...note(ref, "body").note, title: "x".repeat(513) },
      }),
    ],
    [
      "oversized text",
      (ref: string) => ({
        ...note(ref, "body"),
        note: { ...note(ref, "body").note, text: "😀".repeat(524_289) },
      }),
    ],
    [
      "oversized source",
      (ref: string) => ({
        ...note(ref, "body"),
        note: { ...note(ref, "body").note, source: "😀".repeat(524_289) },
      }),
    ],
    [
      "malformed hash",
      (ref: string) => ({
        ...note(ref, "body"),
        note: { ...note(ref, "body").note, sha256: "bad" },
      }),
    ],
    [
      "malformed metadata",
      (ref: string) => ({
        ...note(ref, "body"),
        note: { ...note(ref, "body").note, display_metadata: { display_title: "Bad" } },
      }),
    ],
  ])("rejects %s selected-note envelope", async (_name, response) => {
    const api = vault({ note: vi.fn(async (_workspace, ref) => response(ref) as never) });
    renderDialog(api);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("body")).toBeNull();
    expect(screen.getByRole("navigation").textContent).toContain(`${folder}/MEMORY.md`);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  });

  it("shows a memory file's front matter as plain text above its body", async () => {
    const text = "---\nname: tea\ndescription: <b>likes</b> tea\ntype: user\n---\n\n# Tea\n\nBody";
    renderDialog(vault({ note: vi.fn(async (_workspace, ref) => note(ref, text)) as never }));

    const block = await screen.findByText(/name: tea/);
    expect(block.tagName).toBe("PRE");
    expect(block.textContent).toBe(
      "---\nname: tea\ndescription: <b>likes</b> tea\ntype: user\n---",
    );
    expect(block.querySelector("b")).toBeNull();
    expect(await screen.findByText("Body")).toBeTruthy();
  });

  it("shows no front matter block for a file without a closed one", async () => {
    renderDialog(
      vault({
        note: vi.fn(async (_workspace, ref) => note(ref, "---\nname: open\n\nBody")) as never,
      }),
    );
    await screen.findByRole("navigation");
    await screen.findByRole("article");
    expect(document.querySelector("pre.bot-memory-frontmatter")).toBeNull();
  });

  it("opens the selected file in VS Code only with the callback", async () => {
    const onOpenInEditor = vi.fn();
    const first = renderDialog(vault(), vi.fn(), vi.fn(), { onOpenInEditor });
    await screen.findByRole("navigation");
    expect(screen.getByText(/VS Code edits the file directly/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open in VS Code" }));
    expect(onOpenInEditor).toHaveBeenCalledExactlyOnceWith(`${folder}/MEMORY.md`);
    first.unmount();

    renderDialog(vault());
    await screen.findByRole("navigation");
    expect(screen.queryByRole("button", { name: "Open in VS Code" })).toBeNull();
    expect(screen.queryByText(/VS Code edits the file directly/)).toBeNull();
  });

  describe("app-kept mode", () => {
    const AREA = "/data/bot-memory/11111111-1111-4111-8111-111111111111";
    const appRef = `${BOT}/MEMORY.md`;
    const appVault = (area: string | null) =>
      vault({
        entries: vi.fn(async () => ({
          entries: [{ ref: appRef, title: "MEMORY.md", modified: 1 }],
          partial: false,
          partial_reasons: [],
          omitted: 0,
          ...(area ? { folder: area } : {}),
        })) as never,
        note: vi.fn(async (_workspace, ref) => note(ref, "old line\n")) as never,
      });

    function renderApp(
      area: string | null,
      onDraftReplacement: (request: string) => Promise<boolean>,
    ) {
      return render(
        <BotMemoryDialog
          api={appVault(area)}
          workspaceId={WORKSPACE}
          bot={{ bot_id: BOT, name: "Mori" }}
          kept="app"
          onClose={vi.fn()}
          onOpenUrl={vi.fn()}
          onDraftReplacement={onDraftReplacement}
        />,
      );
    }

    it("names the folder, hides Open in Vault and drafts the full path", async () => {
      const onDraftReplacement = vi.fn(async (_request: string) => true);
      renderApp(AREA, onDraftReplacement);
      await screen.findByText("old line");
      expect(screen.getByText(`Source ${AREA}`)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Open in Vault" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Edit" }));
      fireEvent.change(screen.getByRole("textbox", { name: "New text of this file" }), {
        target: { value: "new line\n" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText(/unsent draft/);
      expect(onDraftReplacement.mock.calls[0]?.[0]).toBe(
        replacementRequest(`${AREA}/${appRef}`, "a".repeat(64), "new line\n"),
      );
    });

    it("shows the kept line and no Edit when the reader names no folder", async () => {
      renderApp(
        null,
        vi.fn(async () => true),
      );
      await screen.findByText("old line");
      expect(screen.getByText(/Kept on this computer/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    });
  });

  describe("editing as a drafted request", () => {
    const memoryRef = `${folder}/MEMORY.md`;

    async function openEditor(
      onDraftReplacement: (request: string) => Promise<boolean>,
      api = vault({ note: vi.fn(async (_workspace, ref) => note(ref, "old line\n")) as never }),
    ) {
      renderDialog(api, vi.fn(), vi.fn(), { onDraftReplacement });
      await screen.findByText("old line");
      fireEvent.click(screen.getByRole("button", { name: "Edit" }));
      return {
        api,
        box: screen.getByRole("textbox", { name: "New text of this file" }) as HTMLTextAreaElement,
      };
    }

    it("offers Edit only with the draft callback", async () => {
      renderDialog(vault());
      await screen.findByRole("navigation");
      expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    });

    it("says plainly that Save prepares an unsent message and does not write the file", async () => {
      await openEditor(vi.fn(async () => true));
      expect(screen.getByText(/Save does not write the file/).textContent).toMatch(
        /unsent message to Mori.*Mori changes the file only after you send the message, under Claude Code's own permission settings/,
      );
    });

    it("keeps Save off until the text differs from what was shown", async () => {
      const { box } = await openEditor(vi.fn(async () => true));
      expect(box.value).toBe("old line\n");
      const save = screen.getByRole("button", { name: "Save" }) as HTMLButtonElement;
      expect(save.disabled).toBe(true);
      fireEvent.change(box, { target: { value: "new line\n" } });
      expect(save.disabled).toBe(false);
      fireEvent.change(box, { target: { value: "  \n" } });
      expect(save.disabled).toBe(true);
    });

    it("prepares the exact-replacement message with the shown hash, and writes nothing itself", async () => {
      const onDraftReplacement = vi.fn(async (_request: string) => true);
      const { api, box } = await openEditor(onDraftReplacement);
      const next = "new line\n```\ncode\n```\n";
      fireEvent.change(box, { target: { value: next } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByText(/unsent draft/)).toBeTruthy();
      expect(onDraftReplacement).toHaveBeenCalledExactlyOnceWith(
        replacementRequest(memoryRef, "a".repeat(64), next),
      );
      expect(onDraftReplacement.mock.calls[0]?.[0]).toContain(`\n\`\`\`\`\n${next}\n\`\`\`\``);
      expect(screen.getByText(/The file is unchanged until you send it/)).toBeTruthy();
      expect(screen.queryByRole("textbox")).toBeNull();
      expect(api.saveNote).not.toHaveBeenCalled();
      expect(api.createNote).not.toHaveBeenCalled();
    });

    it("keeps the edit and says nothing changed when the message could not be prepared", async () => {
      const { box } = await openEditor(vi.fn(async () => false));
      fireEvent.change(box, { target: { value: "new line\n" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      expect((await screen.findByRole("alert")).textContent).toBe(
        "The message could not be prepared. Nothing was changed.",
      );
      expect(
        (screen.getByRole("textbox", { name: "New text of this file" }) as HTMLTextAreaElement)
          .value,
      ).toBe("new line\n");
      expect(screen.queryByText(/unsent draft/)).toBeNull();
    });

    it("treats a thrown callback as a failure", async () => {
      const { box } = await openEditor(
        vi.fn(async () => {
          throw new Error("offline");
        }),
      );
      fireEvent.change(box, { target: { value: "new line\n" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(await screen.findByText(/Nothing was changed/)).toBeTruthy();
    });

    it("locks the file list and refresh while editing, and Cancel returns to the viewer", async () => {
      const { api, box } = await openEditor(vi.fn(async () => true));
      fireEvent.change(box, { target: { value: "unsaved" } });
      const topic = screen.getByRole("button", { name: new RegExp(`${folder}/topic.md`) });
      expect((topic as HTMLButtonElement).disabled).toBe(true);
      expect(
        (screen.getByRole("button", { name: "Refresh memory" }) as HTMLButtonElement).disabled,
      ).toBe(true);
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("textbox")).toBeNull();
      expect(await screen.findByText("old line")).toBeTruthy();
      expect((topic as HTMLButtonElement).disabled).toBe(false);
      expect(api.entries).toHaveBeenCalledTimes(1);
    });

    it("moves the keyboard into the text on Edit, and back to Edit on Cancel and after Save", async () => {
      const { box } = await openEditor(vi.fn(async () => true));
      expect(document.activeElement).toBe(box);
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      const edit = screen.getByRole("button", { name: "Edit" });
      expect(document.activeElement).toBe(edit);
      fireEvent.click(edit);
      const again = screen.getByRole("textbox", { name: "New text of this file" });
      expect(document.activeElement).toBe(again);
      fireEvent.change(again, { target: { value: "new line\n" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText(/unsent draft/);
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit" }));
    });

    it("keeps Edit and Refresh off until a refresh has settled", async () => {
      let finishRefresh!: (value: ReturnType<typeof listing>) => void;
      const entries = vi
        .fn()
        .mockImplementationOnce(async () => listing())
        .mockImplementationOnce(
          () => new Promise<ReturnType<typeof listing>>((resolve) => (finishRefresh = resolve)),
        );
      renderDialog(
        vault({
          entries,
          note: vi.fn(async (_workspace, ref) => note(ref, "old line\n")) as never,
        }),
        vi.fn(),
        vi.fn(),
        { onDraftReplacement: vi.fn(async () => true) },
      );
      await screen.findByText("old line");
      const edit = () => screen.getByRole("button", { name: "Edit" }) as HTMLButtonElement;
      const refresh = () =>
        screen.getByRole("button", { name: "Refresh memory" }) as HTMLButtonElement;
      fireEvent.click(refresh());
      expect(edit().disabled).toBe(true);
      expect(refresh().disabled).toBe(true);
      fireEvent.click(edit());
      expect(screen.queryByRole("textbox")).toBeNull();
      await act(async () => finishRefresh(listing()));
      await waitFor(() => expect(edit().disabled).toBe(false));
      expect(refresh().disabled).toBe(false);
    });

    it("keeps the text read-only while Save is preparing the message", async () => {
      let finish!: (value: boolean) => void;
      const { box } = await openEditor(
        () =>
          new Promise<boolean>((resolve) => {
            finish = resolve;
          }),
      );
      fireEvent.change(box, { target: { value: "new line\n" } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      expect(box.readOnly).toBe(true);
      await act(async () => finish(false));
      expect(box.readOnly).toBe(false);
      expect(box.value).toBe("new line\n");
    });

    it("counts typed text for the window's unload guard only, not as a page's unsaved work", async () => {
      const { box } = await openEditor(vi.fn(async () => true));
      expect(hasAnyUnsavedWork()).toBe(false);
      fireEvent.change(box, { target: { value: "new line\n" } });
      expect(hasAnyUnsavedWork()).toBe(true);
      expect(hasUnsavedWork()).toBe(false);
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(hasAnyUnsavedWork()).toBe(false);
    });

    it.each([
      ["a lone CR", "- tea\r- cake\n"],
      ["CRLF beside LF", "- tea\r\n- cake\n"],
    ])("offers no Edit for a file with %s, and says why", async (_name, source) => {
      renderDialog(
        vault({
          note: vi.fn(async (_workspace, ref) => {
            const base = note(ref, "- tea\n- cake\n");
            return { ...base, note: { ...base.note, source } };
          }) as never,
        }),
        vi.fn(),
        vi.fn(),
        { onDraftReplacement: vi.fn(async () => true) },
      );
      expect(await screen.findByText(/has a lone CR or mixes line ending styles/)).toBeTruthy();
      expect((screen.getByRole("button", { name: "Edit" }) as HTMLButtonElement).disabled).toBe(
        true,
      );
    });

    describe("dismissing with typed text", () => {
      async function typed(onClose = vi.fn()) {
        const api = vault({
          note: vi.fn(async (_workspace, ref) => note(ref, "old line\n")) as never,
        });
        renderDialog(api, onClose, vi.fn(), { onDraftReplacement: vi.fn(async () => true) });
        await screen.findByText("old line");
        fireEvent.click(screen.getByRole("button", { name: "Edit" }));
        const box = screen.getByRole("textbox", { name: "New text of this file" });
        fireEvent.change(box, { target: { value: "new line\n" } });
        return { box: box as HTMLTextAreaElement, onClose };
      }

      it("closes at once on Escape and on Close while the text is unchanged", async () => {
        const onClose = vi.fn();
        renderDialog(vault(), onClose, vi.fn(), { onDraftReplacement: vi.fn(async () => true) });
        await screen.findByRole("navigation");
        fireEvent.keyDown(screen.getByRole("button", { name: "Close" }), { key: "Escape" });
        expect(onClose).toHaveBeenCalledTimes(1);
        expect(screen.queryByRole("alertdialog")).toBeNull();
      });

      it("asks before Escape drops edited text, and keeps the text on Cancel", async () => {
        const { box, onClose } = await typed();
        fireEvent.keyDown(box, { key: "Escape" });
        const ask = screen.getByRole("alertdialog", { name: "Discard this edit?" });
        expect(onClose).not.toHaveBeenCalled();
        expect(ask.textContent).toContain("Your edit of this memory file is not saved");
        fireEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
        expect(screen.queryByRole("alertdialog")).toBeNull();
        expect(onClose).not.toHaveBeenCalled();
        expect(box.value).toBe("new line\n");
      });

      it("treats Escape on the confirmation as Cancel, not as a second dismissal", async () => {
        const { box, onClose } = await typed();
        fireEvent.keyDown(box, { key: "Escape" });
        fireEvent.keyDown(screen.getByRole("alertdialog"), { key: "Escape" });
        expect(screen.queryByRole("alertdialog")).toBeNull();
        expect(onClose).not.toHaveBeenCalled();
        expect(box.value).toBe("new line\n");
      });

      it("discards the edit and closes only on Discard edit, also from the Close button", async () => {
        const { onClose } = await typed();
        fireEvent.click(screen.getByRole("button", { name: "Close" }));
        expect(onClose).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "Discard edit" }));
        expect(onClose).toHaveBeenCalledTimes(1);
      });
    });

    describe("files with their own line endings", () => {
      const crlf = "- tea\r\n- cake\r\n";
      async function open(over: Record<string, unknown>) {
        const onDraftReplacement = vi.fn(async (_request: string) => true);
        const api = vault({
          note: vi.fn(async (_workspace, ref) => {
            const base = note(ref, "- tea\n");
            return { ...base, note: { ...base.note, ...over } };
          }) as never,
        });
        renderDialog(api, vi.fn(), vi.fn(), { onDraftReplacement });
        await screen.findByRole("button", { name: "Edit" });
        await waitFor(() =>
          expect((screen.getByRole("button", { name: "Edit" }) as HTMLButtonElement).disabled).toBe(
            false,
          ),
        );
        fireEvent.click(screen.getByRole("button", { name: "Edit" }));
        fireEvent.change(screen.getByRole("textbox", { name: "New text of this file" }), {
          target: { value: "- coffee\n" },
        });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await screen.findByText(/unsent draft/);
        return onDraftReplacement.mock.calls[0]?.[0] ?? "";
      }

      it("tells the user and the Bot to keep CRLF line endings", async () => {
        const request = await open({ text: crlf, source: crlf });
        expect(request).toContain("keep the file's line endings and byte order mark");
        expect(request.endsWith("\n```\n- coffee\n\n```")).toBe(true);
      });

      it("shows the sentence in the editor for a CRLF file and for a BOM, not for plain LF", async () => {
        renderDialog(
          vault({
            note: vi.fn(async (_workspace, ref) => {
              const base = note(ref, "- tea\n");
              return { ...base, note: { ...base.note, source: "\uFEFF- tea\n" } };
            }) as never,
          }),
          vi.fn(),
          vi.fn(),
          { onDraftReplacement: vi.fn(async () => true) },
        );
        await waitFor(() =>
          expect((screen.getByRole("button", { name: "Edit" }) as HTMLButtonElement).disabled).toBe(
            false,
          ),
        );
        fireEvent.click(screen.getByRole("button", { name: "Edit" }));
        expect(
          screen.getByText(
            "This file uses CRLF line endings or starts with a byte order mark. The message asks Mori to keep the file's line endings.",
          ),
        ).toBeTruthy();
        cleanup();

        renderDialog(
          vault({ note: vi.fn(async (_workspace, ref) => note(ref, "- tea\n")) as never }),
          vi.fn(),
          vi.fn(),
          { onDraftReplacement: vi.fn(async () => true) },
        );
        await waitFor(() =>
          expect((screen.getByRole("button", { name: "Edit" }) as HTMLButtonElement).disabled).toBe(
            false,
          ),
        );
        fireEvent.click(screen.getByRole("button", { name: "Edit" }));
        expect(screen.queryByText(/byte order mark/)).toBeNull();
      });

      it("leaves the request without the sentence for an LF file", async () => {
        const request = await open({});
        expect(request).not.toContain("byte order mark");
      });
    });

    it("refuses a request larger than the draft limit before calling back", async () => {
      const onDraftReplacement = vi.fn(async (_request: string) => true);
      const { box } = await openEditor(onDraftReplacement);
      const big = `${"x".repeat((1 << 20) + 1)}\n`;
      fireEvent.change(box, { target: { value: big } });
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      expect((await screen.findByRole("alert")).textContent).toBe(
        "This text is too large to prepare as one message. Shorten it and try again. Nothing was changed.",
      );
      expect(onDraftReplacement).not.toHaveBeenCalled();
      expect(box.value).toBe(big);
    });

    describe("when the shown file changes under the edit", () => {
      const OTHER = "33333333-3333-4333-8333-333333333333";

      function dialog(
        api: VaultApi,
        botId: string,
        onDraftReplacement: (request: string) => Promise<boolean>,
      ) {
        return (
          <BotMemoryDialog
            api={api}
            workspaceId={WORKSPACE}
            bot={{ bot_id: botId, name: "Mori" }}
            onClose={vi.fn()}
            onOpenInVault={vi.fn()}
            onOpenUrl={vi.fn()}
            onDraftReplacement={onDraftReplacement}
          />
        );
      }

      async function editing(onDraftReplacement: (request: string) => Promise<boolean>) {
        const first = vault({
          note: vi.fn(async (_workspace, ref) => note(ref, "old line\n")) as never,
        });
        const view = render(dialog(first, BOT, onDraftReplacement));
        await screen.findByText("old line");
        fireEvent.click(screen.getByRole("button", { name: "Edit" }));
        fireEvent.change(screen.getByRole("textbox", { name: "New text of this file" }), {
          target: { value: "typed over the old file" },
        });
        return view;
      }

      it("drops the edit when the file's SHA-256 changes, and Save then names the new hash", async () => {
        const onDraftReplacement = vi.fn(async (_request: string) => true);
        const view = await editing(onDraftReplacement);
        const changed = vault({
          note: vi.fn(async (_workspace, ref) => ({
            ...note(ref, "newer line\n"),
            note: { ...note(ref, "newer line\n").note, sha256: "b".repeat(64) },
          })) as never,
        });
        view.rerender(dialog(changed, BOT, onDraftReplacement));
        expect(await screen.findByText("newer line")).toBeTruthy();
        expect(screen.queryByRole("textbox")).toBeNull();

        fireEvent.click(screen.getByRole("button", { name: "Edit" }));
        const box = screen.getByRole("textbox", { name: "New text of this file" });
        expect((box as HTMLTextAreaElement).value).toBe("newer line\n");
        fireEvent.change(box, { target: { value: "newest\n" } });
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        await screen.findByText(/unsent draft/);
        expect(onDraftReplacement).toHaveBeenCalledExactlyOnceWith(
          replacementRequest(memoryRef, "b".repeat(64), "newest\n"),
        );
      });

      it("does not show one Bot's edit over another Bot's file with the same hash", async () => {
        const onDraftReplacement = vi.fn(async () => true);
        const view = await editing(onDraftReplacement);
        const otherFolder = `bot-memory/${OTHER}`;
        const other = vault({
          entries: vi.fn(async () => ({
            entries: [{ ref: `${otherFolder}/MEMORY.md`, title: "MEMORY.md", modified: 1 }],
            partial: false,
            partial_reasons: [],
            omitted: 0,
          })) as never,
          note: vi.fn(async (_workspace, ref) => note(ref, "old line\n")) as never,
        });
        view.rerender(dialog(other, OTHER, onDraftReplacement));
        await waitFor(() =>
          expect((screen.getByRole("button", { name: "Edit" }) as HTMLButtonElement).disabled).toBe(
            false,
          ),
        );
        expect(screen.queryByRole("textbox")).toBeNull();
        expect(screen.queryByDisplayValue("typed over the old file")).toBeNull();
      });
    });
  });
});
