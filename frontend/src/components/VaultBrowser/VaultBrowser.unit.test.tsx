import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CapturesApi } from "../../clients/vaults/capturesApi";
import { catalog as vaultCatalog } from "../../i18n/vault";
import { type CreatedProposal, DecisionsApiError } from "../../state/decisionsApi";
import { createExcerptApi, type VaultExcerpt } from "../../state/excerptApi";
import type { DisplayMetadata } from "../../state/noteMetadata";
import { hasUnsavedWork } from "../../state/unsavedWork";
import {
  type OverviewNote,
  type VaultBookmarkApi,
  type VaultOverview,
  type VaultReadApi,
  type VaultWriteApi,
  VaultWriteError,
} from "../../state/vaultApi";
import type { VaultKnowledgeApi } from "../../state/vaultKnowledgeApi";
import type { VaultOpsApi } from "../../state/vaultOpsApi";
import { resetBlockedWritesForTests } from "../../state/vaultWriteLocks";
import { narrowBlock, readCss, ruleIn } from "../../testing/css";
import {
  VaultBrowser,
  type VaultNavigationHandle,
  type VaultNavigationSnapshot,
} from "./VaultBrowser";
import * as wikilinksModule from "./wikilinks";

const W1 = "00000000-0000-4000-8000-000000000001";
const W2 = "00000000-0000-4000-8000-000000000002";
// The server stores created_at as a 24-character ISO-8601 string.
const CREATED_AT = "2026-10-02T07:26:33.001Z";
const complete = (body: Record<string, unknown>): Record<string, unknown> => ({
  ...body,
  partial: false,
  partial_reasons: [],
  omitted: 0,
});
const scan = (entries: { ref: string; title: string }[]): Record<string, unknown> =>
  complete({ entries });
const noteWithHash = (ref: string, text: string, sha256: string): Record<string, unknown> =>
  complete({
    note: {
      ref,
      title: ref,
      text,
      source: text,
      sha256,
      frontmatter_error: null,
    },
    resolved_links: [],
  });
const note = (ref: string, text = ref): Record<string, unknown> =>
  noteWithHash(ref, text, "a".repeat(64));
const backs = (): Record<string, unknown> => complete({ backlinks: [] });
// The fixed copy for these server error codes (shared between a read failure and a write
// failure, VaultBrowser.tsx CODE_COPY).
const CODE_COPY: [code: string, copy: string][] = [
  ["note_too_large", "This note is too large to display."],
  ["target_busy", "Another app has this note open. Close it and try again."],
  ["invalid_encoding", "This note is not in a format the app can read."],
  ["overloaded", "The vault is busy. Wait a moment and try again."],
  [
    "unsafe_parent",
    "This note's folder is missing, or is a link or file the app cannot save through.",
  ],
  [
    "publication_busy",
    "Another save to this note has not finished. Check recovery status; the app tries again at its next start.",
  ],
  ["workspace_changed", "This workspace has changed. Reopen it and try again."],
  ["invalid_ref", "This note reference is invalid."],
  ["excluded_path", "This file cannot be edited in the app."],
  ["unsupported_filesystem", "This vault's drive does not support saving notes here."],
  ["invalid_publication", "This note has a control character, or is more than 2 MiB."],
  [
    "read_only_target",
    "This note is read-only. Clear Read-only in its Properties, then save again.",
  ],
  // These three have a CODE_COPY line of their own rather than the generic message.
  ["stage_busy", "Another app briefly has this note open. Try again."],
  ["parent_busy", "Another app briefly has this note's folder open. Try again."],
  ["missing_target", "This note was moved or deleted outside the app."],
  // The save route refuses with this code while an unfinished note operation is pending.
  [
    "vault_recovery_required",
    "A note operation did not finish, so saving is blocked. Resolve it under Unfinished operations, then save again.",
  ],
  // The save waited its bounded time for the vault lease and wrote nothing.
  ["vault_locked", "Another save or operation is in progress. Try again in a moment."],
];
// Off Windows, read_only_target names a POSIX remedy; on Windows it names the Properties
// dialog (its CODE_COPY line).
const READ_ONLY_TARGET_CASES: [platform: string, copy: string][] = [
  ["Win32", "This note is read-only. Clear Read-only in its Properties, then save again."],
  [
    "MacIntel",
    "This note is read-only or locked. Make it writable in your file manager, then save again.",
  ],
  [
    "Linux x86_64",
    "This note is read-only or locked. Make it writable in your file manager, then save again.",
  ],
];

/** jsdom exposes `navigator.platform` through a prototype getter, so this file's own property is
 * removed afterwards rather than restored — there is no own descriptor to put back. */
function setPlatform(value: string): () => void {
  Object.defineProperty(window.navigator, "platform", { value, configurable: true });
  return () => {
    delete (window.navigator as { platform?: string }).platform;
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

type BrowserApi = VaultReadApi & VaultBookmarkApi;
function vaultApi(overrides: Partial<BrowserApi> = {}): BrowserApi {
  return {
    entries: vi.fn(async () => scan([])),
    note: vi.fn(async (_id, ref) => note(ref)),
    backlinks: vi.fn(async () => backs()),
    history: vi.fn(async () => complete({ commits: [], state: "ready" })),
    bookmarks: vi.fn(async () => ({ bookmarks: [] })),
    addBookmark: vi.fn(async (_id, ref) => ({ bookmark: { ref, created_at: CREATED_AT } })),
    removeBookmark: vi.fn(async () => undefined),
    ...overrides,
  };
}

function writeApi(overrides: Partial<VaultWriteApi> = {}): VaultWriteApi {
  return {
    createNote: vi.fn(async () => ({
      operation_id: W1,
      status: "saved" as const,
      cleanup_pending: false,
    })),
    saveNote: vi.fn(async () => ({
      operation_id: W1,
      status: "saved" as const,
      cleanup_pending: false,
    })),
    recovery: vi.fn(async () => []),
    ...overrides,
  };
}

const write = (api: VaultWriteApi) => ({ api, policyRevision: 4, rootPhysicalId: "12:34" });

async function debounce(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250);
  });
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  resetBlockedWritesForTests();
});

describe("VaultBrowser", () => {
  it("two SKILL files have distinct visible identities", async () => {
    const files = [
      { ref: "skills/alpha/SKILL.md", title: "SKILL" },
      { ref: "tools/beta/SKILL.md", title: "SKILL.md" },
    ];
    const api = vaultApi({
      entries: vi.fn(async () => scan(files)),
      note: vi.fn(async (_id, ref) => {
        const result = note(ref);
        return { ...result, note: { ...(result.note as object), title: "SKILL" } };
      }),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    const notes = within(screen.getByRole("navigation", { name: "Notes" }));
    const alpha = notes.getByRole("button", { name: "alpha / SKILL.md" });
    expect(notes.getByRole("button", { name: "beta / SKILL.md" })).toBeTruthy();
    expect(notes.getByText("skills/alpha/SKILL.md")).toBeTruthy();
    expect(notes.getByText("tools/beta/SKILL.md")).toBeTruthy();
    expect(alpha.getAttribute("aria-describedby")).toBe(
      notes.getByText("skills/alpha/SKILL.md").id,
    );
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(api.note).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(alpha));
    expect(api.note).toHaveBeenCalledWith(W1, "skills/alpha/SKILL.md", expect.anything());
    const selected = within(screen.getByRole("article", { name: "Selected note" }));
    expect(selected.getByRole("heading", { name: "alpha / SKILL.md" })).toBeTruthy();
    expect(selected.getByText("skills/alpha/SKILL.md", { selector: ".vault-ref" })).toBeTruthy();
    expect(api.note).toHaveBeenCalledTimes(1);
    expect(api.backlinks).toHaveBeenCalledTimes(1);
    expect(api.history).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["guide/README.md", "README", "guide / README.md"],
    ["nested/docs/index.markdown", "index", "docs / index.markdown"],
    ["README.md", "README", "README.md"],
    ["guide/README.md", "Handbook", "Handbook"],
  ])("identifies %s while preserving authored titles", async (ref, title, display) => {
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi({ entries: vi.fn(async () => scan([{ ref, title }])) })}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    const notes = within(screen.getByRole("navigation", { name: "Notes" }));
    expect(notes.getByRole("button", { name: display })).toBeTruthy();
    expect(notes.getByText(ref, { selector: ".vault-ref" })).toBeTruthy();
  });

  it("shows an empty partial listing as incomplete", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => ({
        entries: [],
        partial: true,
        partial_reasons: ["deadline"],
        omitted: 3,
      })),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    expect(
      screen.getByText(/Results may be incomplete \(deadline; at least 3 omitted\)/),
    ).toBeTruthy();
    expect(screen.getByText("No notes found in the scanned results.")).toBeTruthy();
  });

  it("ignores a stale search result even when the API ignores abort", async () => {
    const first = deferred<Record<string, unknown>>();
    const second = deferred<Record<string, unknown>>();
    const entries = vi
      .fn<VaultReadApi["entries"]>()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    render(<VaultBrowser workspaceId={W1} api={vaultApi({ entries })} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
      target: { value: "new" },
    });
    await debounce();
    await act(async () => second.resolve(scan([{ ref: "new.md", title: "New" }])));
    expect(screen.getByRole("button", { name: "New" })).toBeTruthy();
    await act(async () => first.resolve(scan([{ ref: "old.md", title: "Old" }])));
    expect(screen.queryByRole("button", { name: "Old" })).toBeNull();
  });

  it("clears and rejects a late listing from the previous workspace", async () => {
    const old = deferred<Record<string, unknown>>();
    const entries = vi.fn<VaultReadApi["entries"]>((id) =>
      id === W1 ? old.promise : Promise.resolve(scan([{ ref: "two.md", title: "Two" }])),
    );
    const api = vaultApi({ entries });
    const view = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    view.rerender(<VaultBrowser workspaceId={W2} api={api} onOpenUrl={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Two" })).toBeNull();
    await debounce();
    expect(screen.getByRole("button", { name: "Two" })).toBeTruthy();
    await act(async () => old.resolve(scan([{ ref: "one.md", title: "One" }])));
    expect(screen.queryByRole("button", { name: "One" })).toBeNull();
  });

  it("does not inspect a late listing after unmount when the API ignores abort", async () => {
    const pending = deferred<Record<string, unknown>>();
    const view = render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi({ entries: vi.fn(() => pending.promise) })}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    view.unmount();
    let reads = 0;
    const raw = complete({});
    Object.defineProperty(raw, "entries", {
      get: () => {
        reads += 1;
        return [];
      },
    });
    await act(async () => pending.resolve(raw));
    expect(reads).toBe(0);
  });

  it.each([
    ["ref", complete({ entries: [{ ref: "../bad.md", title: "Bad" }] })],
    ["empty title", complete({ entries: [{ ref: "bad.md", title: "" }] })],
    ["title", complete({ entries: [{ ref: "bad.md", title: "x".repeat(513) }] })],
    ["excerpt", complete({ hits: [{ ref: "bad.md", line: 1, excerpt: "x".repeat(601) }] })],
    ["line", complete({ hits: [{ ref: "bad.md", line: 0, excerpt: "bad" }] })],
  ])("rejects an invalid listing %s", async (_field, body) => {
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi({ entries: vi.fn(async () => body) })}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    expect(screen.getByRole("alert").textContent).toBe("The vault returned an invalid response.");
  });

  it("keeps the newest selected note when an older read finishes last", async () => {
    const a = deferred<Record<string, unknown>>();
    const b = deferred<Record<string, unknown>>();
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "a.md", title: "A" },
          { ref: "b.md", title: "B" },
        ]),
      ),
      note: vi.fn((_id, ref) => (ref === "a.md" ? a.promise : b.promise)),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "A" }));
    fireEvent.click(screen.getByRole("button", { name: "B" }));
    await act(async () => b.resolve(note("b.md", "newest")));
    expect(screen.getByText("newest")).toBeTruthy();
    await act(async () => a.resolve(note("a.md", "stale")));
    expect(screen.queryByText("stale")).toBeNull();
  });

  it.each([
    ["ref mismatch", note("other.md")],
    [
      "empty title",
      complete({
        note: { ref: "one.md", title: "", text: "body", frontmatter_error: null },
        resolved_links: [],
      }),
    ],
    [
      "long title",
      complete({
        note: { ref: "one.md", title: "x".repeat(513), text: "body", frontmatter_error: null },
        resolved_links: [],
      }),
    ],
    [
      "text",
      complete({
        note: {
          ref: "one.md",
          title: "One",
          text: "x".repeat(2 * 1024 * 1024 + 1),
          frontmatter_error: null,
        },
        resolved_links: [],
      }),
    ],
    [
      "frontmatter error",
      complete({
        note: { ref: "one.md", title: "One", text: "body", frontmatter_error: "secret" },
        resolved_links: [],
      }),
    ],
    [
      "link target",
      complete({
        note: { ref: "one.md", title: "One", text: "body", frontmatter_error: null },
        resolved_links: [
          { target: "x".repeat(2 * 1024 * 1024 + 1), state: "missing", candidates: [] },
        ],
      }),
    ],
    [
      "link state",
      complete({
        note: { ref: "one.md", title: "One", text: "body", frontmatter_error: null },
        resolved_links: [{ target: "two", state: "secret", candidates: [] }],
      }),
    ],
    [
      "link candidates",
      complete({
        note: { ref: "one.md", title: "One", text: "body", frontmatter_error: null },
        resolved_links: [{ target: "two", state: "ambiguous", candidates: ["two.md", "two.md"] }],
      }),
    ],
  ])("rejects an invalid note %s", async (_field, body) => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => body),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    expect(screen.getByRole("alert").textContent).toBe("The vault returned an invalid response.");
  });

  it.each([
    ["resolved without one candidate", "resolved", []],
    ["resolved with many candidates", "resolved", ["a.md", "b.md"]],
    ["ambiguous without many candidates", "ambiguous", ["a.md"]],
    ["missing with candidates", "missing", ["a.md"]],
    ["external with candidates", "external", ["a.md"]],
    ["unsafe with candidates", "unsafe", ["a.md"]],
  ])("rejects %s", async (_case, state, candidates) => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () =>
        complete({
          note: { ref: "one.md", title: "One", text: "body", frontmatter_error: null },
          resolved_links: [{ target: "target", state, candidates }],
        }),
      ),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    expect(screen.getByRole("alert").textContent).toBe("The vault returned an invalid response.");
  });

  it.each([
    ["source", complete({ backlinks: [{ source: "../bad.md", line: 1 }] })],
    ["line", complete({ backlinks: [{ source: "source.md", line: 0 }] })],
  ])("rejects an invalid backlink %s", async (_field, body) => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      backlinks: vi.fn(async () => body),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    expect(screen.getByRole("alert").textContent).toBe("The vault returned an invalid response.");
  });

  it("qualifies an empty partial backlink result", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      backlinks: vi.fn(async () => ({
        backlinks: [],
        partial: true,
        partial_reasons: ["deadline"],
        omitted: 1,
      })),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    expect(screen.getByText("No backlinks found in the scanned results.")).toBeTruthy();
  });

  it("uses the shared safe Markdown renderer for images and URLs", async () => {
    const onOpenUrl = vi.fn();
    const text =
      "![spy](https://example.test/spy.png) [bad](javascript:alert(1)) [safe](https://example.test/)";
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "safe.md", title: "Safe" }])),
      note: vi.fn(async () => note("safe.md", text)),
    });
    const { container } = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={onOpenUrl} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "Safe" }));
    await act(async () => Promise.resolve());
    expect(screen.queryByRole("button", { name: "New note" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit note" })).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("[spy]");
    expect(container.textContent).toContain("bad (javascript:alert(1))");
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "safe" }));
    expect(onOpenUrl).toHaveBeenCalledWith("https://example.test/");
  });

  it("explicitly saves the full source with the read SHA and policy snapshot", async () => {
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "---\ntitle: One\n---\nBody")),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => ({
      operation_id: W1,
      status: "saved",
      cleanup_pending: false,
    }));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const editor = screen.getByRole("textbox", { name: "Markdown" });
    expect((editor as HTMLTextAreaElement).value).toBe("---\ntitle: One\n---\nBody");
    fireEvent.change(editor, { target: { value: "---\ntitle: One\n---\nChanged" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    expect(saveNote).toHaveBeenCalledWith(
      W1,
      4,
      "12:34",
      "one.md",
      "---\ntitle: One\n---\nChanged",
      "a".repeat(64),
    );
  });

  it("keeps the draft on an edit conflict, re-reads the note, and leaves no lock", async () => {
    const readNote = vi
      .fn<VaultReadApi["note"]>()
      .mockResolvedValueOnce(note("one.md", "original"))
      .mockResolvedValueOnce(noteWithHash("one.md", "server changed", "b".repeat(64)));
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: readNote,
    });
    const saveNote = vi
      .fn<VaultWriteApi["saveNote"]>()
      .mockResolvedValueOnce({ operation_id: W1, status: "conflict", cleanup_pending: false })
      .mockResolvedValueOnce({ operation_id: W2, status: "saved", cleanup_pending: false });
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local draft" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    expect((screen.getByRole("textbox", { name: "Markdown" }) as HTMLTextAreaElement).value).toBe(
      "local draft",
    );
    expect(screen.getByRole("alert").textContent).toBe(
      "This note changed elsewhere. Your draft is still here.",
    );
    expect(readNote).toHaveBeenCalledTimes(2);
    // No lock left by the conflict: no recovery check offered, and a retry is not blocked.
    expect(screen.queryByRole("button", { name: "Check recovery status" })).toBeNull();
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(false);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    expect(saveNote).toHaveBeenNthCalledWith(
      2,
      W1,
      4,
      "12:34",
      "one.md",
      "local draft",
      "b".repeat(64),
    );
  });

  it("locks the ref on a step-6 conflict (cleanup_pending) instead of re-reading the note", async () => {
    const readNote = vi
      .fn<VaultReadApi["note"]>()
      .mockResolvedValueOnce(note("one.md", "original"));
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: readNote,
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => ({
      operation_id: W2,
      status: "conflict",
      cleanup_pending: true,
    }));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local draft" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    // No re-read: the server still holds the ref, so the view must not overwrite the draft.
    expect(readNote).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert").textContent).toContain(`operation ${W2}`);
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Check recovery status" })).toBeTruthy();
  });

  it("locks a step-6 conflict that arrives after Cancel", async () => {
    const pending = deferred<{
      operation_id: string;
      status: "conflict";
      cleanup_pending: boolean;
    }>();
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(() => pending.promise);
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () =>
      pending.resolve({ operation_id: W2, status: "conflict", cleanup_pending: true }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Check recovery status" })).toBeTruthy();
  });

  it("locks the ref on a publication_busy answer that arrives after Cancel", async () => {
    const pending = deferred<never>();
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(() => pending.promise);
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => {
      pending.reject(new VaultWriteError("publication_busy"));
      await pending.promise.catch(() => {});
    });
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Check recovery status" })).toBeTruthy();
  });

  it("locks the ref on publication_busy and clears once recovery reports it gone", async () => {
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "original")),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => {
      throw new VaultWriteError("publication_busy");
    });
    const recovery = vi.fn<VaultWriteApi["recovery"]>(async () => []);
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote, recovery }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local draft" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Check recovery status" })).toBeTruthy();
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Check recovery status" })),
    );
    expect(recovery).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(false);
    expect(screen.queryByRole("button", { name: "Check recovery status" })).toBeNull();
  });

  it("shows the create-conflict copy and leaves no lock", async () => {
    const createNote = vi.fn<VaultWriteApi["createNote"]>(async () => ({
      operation_id: W1,
      status: "conflict",
      cleanup_pending: false,
    }));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi()}
        write={write(writeApi({ createNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "new/note.md" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "# New" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create note" })));
    expect(screen.getByRole("alert").textContent).toBe("A note already exists at this path.");
    expect(screen.queryByRole("button", { name: "Check recovery status" })).toBeNull();
    expect(screen.getByRole("button", { name: "Create note" }).hasAttribute("disabled")).toBe(
      false,
    );
  });

  // A save that lands in error, stale or recovery inside the
  // useHeld hold window (300-700 ms) must drop the busy spinner at once, since BusyLabel's
  // `failed` prop overrides the hold once the alert is already on screen.
  it("drops the busy-spinner at once when the create-conflict error lands at ~360 ms", async () => {
    const held = deferred<Awaited<ReturnType<VaultWriteApi["createNote"]>>>();
    const createNote = vi.fn<VaultWriteApi["createNote"]>(() => held.promise);
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi()}
        write={write(writeApi({ createNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "new/note.md" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "# New" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create note" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(360); // inside the 300-700 hold window
      held.resolve({ operation_id: W1, status: "conflict", cleanup_pending: false });
      await held.promise;
    });
    expect(screen.getByRole("alert").textContent).toBe("A note already exists at this path.");
    expect(
      screen.getByRole("button", { name: "Create note" }).querySelector(".busy-spinner"),
    ).toBeNull();
  });

  it.each(CODE_COPY)("shows fixed copy for a %s read failure", async (code, copy) => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn<VaultReadApi["note"]>(async () => {
        throw new Error(code);
      }),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    expect(screen.getByRole("alert").textContent).toBe(copy);
  });

  it.each(CODE_COPY)("shows fixed copy for a %s save failure", async (code, copy) => {
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "original")),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => {
      throw new VaultWriteError(code);
    });
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    expect(screen.getByRole("alert").textContent).toBe(`${copy} Your draft is still here.`);
  });

  it.each(READ_ONLY_TARGET_CASES)(
    "names the platform's remedy for a read-only note on %s (read failure)",
    async (platform, copy) => {
      const restore = setPlatform(platform);
      try {
        const api = vaultApi({
          entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
          note: vi.fn<VaultReadApi["note"]>(async () => {
            throw new Error("read_only_target");
          }),
        });
        render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
        await debounce();
        fireEvent.click(screen.getByRole("button", { name: "One" }));
        await act(async () => Promise.resolve());
        expect(screen.getByRole("alert").textContent).toBe(copy);
      } finally {
        restore();
      }
    },
  );

  it.each(READ_ONLY_TARGET_CASES)(
    "names the platform's remedy for a read-only note on %s (save failure)",
    async (platform, copy) => {
      const restore = setPlatform(platform);
      try {
        const read = vaultApi({
          entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
          note: vi.fn(async () => note("one.md", "original")),
        });
        const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => {
          throw new VaultWriteError("read_only_target");
        });
        render(
          <VaultBrowser
            workspaceId={W1}
            api={read}
            write={write(writeApi({ saveNote }))}
            onOpenUrl={vi.fn()}
          />,
        );
        await debounce();
        fireEvent.click(screen.getByRole("button", { name: "One" }));
        await act(async () => Promise.resolve());
        fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
        fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
          target: { value: "local" },
        });
        await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
        expect(screen.getByRole("alert").textContent).toBe(`${copy} Your draft is still here.`);
      } finally {
        restore();
      }
    },
  );

  it("blocks a recovery-required draft and retains its operation id", async () => {
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "original")),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => {
      throw new VaultWriteError("recovery_required", W2);
    });
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local draft" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    expect(screen.getByRole("alert").textContent).toContain(`operation ${W2}`);
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(true);
    expect(saveNote).toHaveBeenCalledTimes(1);
  });

  it("records a recovery_required lock even after a cancelled save's generation moved on", async () => {
    let reject!: (error: unknown) => void;
    const pending = new Promise<never>((_resolve, doReject) => {
      reject = doReject;
    });
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(() => pending);
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    // Cancel stays enabled while saving, and bumps saveGeneration before the answer arrives.
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => {
      reject(new VaultWriteError("recovery_required", W2));
      await pending.catch(() => {});
    });
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(true);
  });

  it("ignores a save completion after the workspace changes", async () => {
    const pending = deferred<{
      operation_id: string;
      status: "saved";
      cleanup_pending: boolean;
    }>();
    const read = vaultApi({
      entries: vi.fn(async (id) => scan([{ ref: `${id}.md`, title: id }])),
      note: vi.fn(async (_id, ref) => note(ref, "source")),
    });
    const writes = write(
      writeApi({ saveNote: vi.fn<VaultWriteApi["saveNote"]>(() => pending.promise) }),
    );
    const view = render(
      <VaultBrowser workspaceId={W1} api={read} write={writes} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: W1 }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    view.rerender(<VaultBrowser workspaceId={W2} api={read} write={writes} onOpenUrl={vi.fn()} />);
    await act(async () =>
      pending.resolve({ operation_id: W1, status: "saved", cleanup_pending: false }),
    );
    expect(screen.queryByRole("textbox", { name: "Markdown" })).toBeNull();
    expect(screen.queryByText("Saved.")).toBeNull();
  });

  it("creates only after an explicit new-note submission", async () => {
    const createNote = vi.fn<VaultWriteApi["createNote"]>(async () => ({
      operation_id: W1,
      status: "conflict",
      cleanup_pending: true,
    }));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi()}
        write={write(writeApi({ createNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "new/note.md" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "# New" },
    });
    expect(createNote).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create note" })));
    expect(createNote).toHaveBeenCalledWith(W1, 4, "12:34", "new/note.md", "# New");
    expect((screen.getByRole("textbox", { name: "Markdown" }) as HTMLTextAreaElement).value).toBe(
      "# New",
    );
  });

  it("hides Edit note for a ref the writer refuses, and shows a fixed line instead", async () => {
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "CLAUDE.md", title: "CLAUDE.md" },
          { ref: "x.markdown", title: "x.markdown" },
        ]),
      ),
      note: vi.fn(async (_id, ref) => note(ref)),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "CLAUDE.md" }));
    await act(async () => Promise.resolve());
    expect(screen.queryByRole("button", { name: "Edit note" })).toBeNull();
    expect(screen.getByText("This file is not edited in the app.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "x.markdown" }));
    await act(async () => Promise.resolve());
    expect(screen.queryByRole("button", { name: "Edit note" })).toBeNull();
    expect(screen.getByText("This file is not edited in the app.")).toBeTruthy();
  });

  it("disables Create note for a ref the writer refuses", async () => {
    const createNote = vi.fn<VaultWriteApi["createNote"]>(async () => ({
      operation_id: W1,
      status: "saved" as const,
      cleanup_pending: false,
    }));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi()}
        write={write(writeApi({ createNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    // Before anything is typed, the disabled reason stays hidden: nothing was refused yet.
    expect(screen.queryByText("This file is not edited in the app.")).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "CLAUDE.md" },
    });
    expect(screen.getByRole("button", { name: "Create note" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("This file is not edited in the app.")).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "new/note.md" },
    });
    expect(screen.getByRole("button", { name: "Create note" }).hasAttribute("disabled")).toBe(
      false,
    );
    expect(screen.queryByText("This file is not edited in the app.")).toBeNull();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create note" })));
    expect(createNote).toHaveBeenCalledTimes(1);
  });

  it("names the .md rule for an incomplete path, and keeps the refused-name reason", async () => {
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi()}
        write={write(writeApi())}
        onOpenUrl={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "ideas/today" },
    });
    expect(screen.getByText("Note paths end in .md.")).toBeTruthy();
    expect(screen.queryByText("This file is not edited in the app.")).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "CLAUDE.md" },
    });
    expect(screen.getByText("This file is not edited in the app.")).toBeTruthy();
    expect(screen.queryByText("Note paths end in .md.")).toBeNull();
  });

  it("shows no stray recovery button for a New note path named constructor or __proto__ (N3)", async () => {
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi()}
        write={write(writeApi())}
        onOpenUrl={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "constructor" },
    });
    expect(screen.queryByRole("button", { name: "Check recovery status" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "__proto__" },
    });
    expect(screen.queryByRole("button", { name: "Check recovery status" })).toBeNull();
  });

  it("keeps CRLF line endings on save, and leaves an LF note as LF", async () => {
    const read = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "crlf.md", title: "CRLF" },
          { ref: "lf.md", title: "LF" },
        ]),
      ),
      note: vi.fn(async (_id, ref) =>
        ref === "crlf.md" ? note(ref, "line1\r\nline2\r\n") : note(ref, "line1\nline2\n"),
      ),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => ({
      operation_id: W1,
      status: "saved",
      cleanup_pending: false,
    }));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read}
        write={write(writeApi({ saveNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "CRLF" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    // A <textarea> normalizes CRLF to LF; the user appends one line.
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "line1\nline2\nline3" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    expect(saveNote).toHaveBeenCalledWith(
      W1,
      4,
      "12:34",
      "crlf.md",
      "line1\r\nline2\r\nline3",
      "a".repeat(64),
    );

    fireEvent.click(screen.getByRole("button", { name: "LF" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "line1\nline2\nline3" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    expect(saveNote).toHaveBeenLastCalledWith(
      W1,
      4,
      "12:34",
      "lf.md",
      "line1\nline2\nline3",
      "a".repeat(64),
    );
  });

  it("asks before discarding a dirty draft when another note is clicked, and keeps it on cancel", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "one.md", title: "One" },
          { ref: "two.md", title: "Two" },
        ]),
      ),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Two" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect((screen.getByRole("textbox", { name: "Markdown" }) as HTMLTextAreaElement).value).toBe(
      "changed",
    );
    confirmSpy.mockRestore();
  });

  it("switches notes once the discard is confirmed", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "one.md", title: "One" },
          { ref: "two.md", title: "Two" },
        ]),
      ),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Two" }));
    await act(async () => Promise.resolve());
    expect(screen.queryByRole("textbox", { name: "Markdown" })).toBeNull();
    expect(screen.getByRole("heading", { name: "two.md" })).toBeTruthy();
    confirmSpy.mockRestore();
  });

  it("does not ask when the draft is clean", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "one.md", title: "One" },
          { ref: "two.md", title: "Two" },
        ]),
      ),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.click(screen.getByRole("button", { name: "Two" }));
    await act(async () => Promise.resolve());
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("asks before discarding a dirty draft when New note is clicked", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "original")),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect((screen.getByRole("textbox", { name: "Markdown" }) as HTMLTextAreaElement).value).toBe(
      "changed",
    );
    expect(screen.queryByRole("textbox", { name: "Note path" })).toBeNull();
    confirmSpy.mockRestore();
  });

  it("asks before discarding a dirty draft when a link candidate is clicked", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) =>
        ref === "one.md"
          ? {
              ...note("one.md", "original"),
              resolved_links: [{ target: "two.md", state: "resolved", candidates: ["two.md"] }],
            }
          : note(ref, "target"),
      ),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
    // The Links region names its buttons by `titleOf`: "two.md" reads as "two".
    fireEvent.click(screen.getByRole("button", { name: "two" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect((screen.getByRole("textbox", { name: "Markdown" }) as HTMLTextAreaElement).value).toBe(
      "changed",
    );
    confirmSpy.mockRestore();
  });

  it("asks before discarding a dirty draft when a backlink is clicked", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
      backlinks: vi.fn(async () => complete({ backlinks: [{ source: "src.md", line: 3 }] })),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
    // The Backlinks region names its buttons by `titleOf`: "src.md" reads as "src".
    fireEvent.click(screen.getByRole("button", { name: "src" }));
    expect(confirmSpy).toHaveBeenCalled();
    expect((screen.getByRole("textbox", { name: "Markdown" }) as HTMLTextAreaElement).value).toBe(
      "changed",
    );
    confirmSpy.mockRestore();
  });

  it("relists after a successful create, without changing the search text", async () => {
    const entries = vi
      .fn<VaultReadApi["entries"]>()
      .mockResolvedValueOnce(scan([]))
      .mockResolvedValueOnce(scan([{ ref: "new/note.md", title: "New" }]));
    const createNote = vi.fn<VaultWriteApi["createNote"]>(async () => ({
      operation_id: W1,
      status: "saved" as const,
      cleanup_pending: false,
    }));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi({ entries })}
        write={write(writeApi({ createNote }))}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "new/note.md" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "# New" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create note" })));
    await debounce();
    expect(screen.getByRole("button", { name: "New" })).toBeTruthy();
    expect(entries).toHaveBeenLastCalledWith(W1, undefined, expect.anything());
    expect(
      (screen.getByRole("textbox", { name: "Search note contents" }) as HTMLInputElement).value,
    ).toBe("");
  });

  it("keeps a save lock across unmount and remount, in any ref case, scoped to its workspace", async () => {
    const read = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
    });
    const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => {
      throw new VaultWriteError("recovery_required", W1);
    });
    const writeCtx = write(writeApi({ saveNote }));

    const first = render(
      <VaultBrowser workspaceId={W1} api={read} write={writeCtx} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "local" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    first.unmount();

    const second = render(
      <VaultBrowser workspaceId={W1} api={read} write={writeCtx} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(true);
    // A case variant of the locked ref, entered as a new-note path, is blocked too.
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "ONE.MD" },
    });
    expect(screen.getByRole("button", { name: "Create note" }).hasAttribute("disabled")).toBe(true);
    second.unmount();

    const other = render(
      <VaultBrowser workspaceId={W2} api={read} write={writeCtx} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    expect(screen.getByRole("button", { name: "Save note" }).hasAttribute("disabled")).toBe(false);
    other.unmount();
  });

  it("does not call a CRLF note's LF draft of the same text dirty", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "crlf.md", title: "CRLF" },
          { ref: "two.md", title: "Two" },
        ]),
      ),
      note: vi.fn(async (_id, ref) =>
        ref === "crlf.md" ? note(ref, "line1\r\nline2\r\n") : note(ref),
      ),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "CRLF" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    // A keystroke and its undo: the draft ends as the same text, LF-normalized by the textarea.
    const box = screen.getByRole("textbox", { name: "Markdown" });
    fireEvent.change(box, { target: { value: "line1\nline2\nx" } });
    fireEvent.change(box, { target: { value: "line1\nline2\n" } });
    expect(hasUnsavedWork()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Two" }));
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("reports an unsaved draft to the app, and clears it on cancel and on unmount", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) => note(ref, "original")),
    });
    const view = render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    expect(hasUnsavedWork()).toBe(false);
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
    expect(hasUnsavedWork()).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(hasUnsavedWork()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "new.md" },
    });
    expect(hasUnsavedWork()).toBe(true);
    view.unmount();
    expect(hasUnsavedWork()).toBe(false);
  });

  it("focuses its heading on entry and offers the way back to the chat", async () => {
    const onBack = vi.fn();
    render(<VaultBrowser workspaceId={W1} api={vaultApi()} onOpenUrl={vi.fn()} onBack={onBack} />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(document.activeElement).toBe(heading);
    expect(heading.getAttribute("tabindex")).toBe("-1");
    expect(heading.classList.contains("page-title")).toBe(true);
    expect(heading.closest(".page-head")).not.toBeNull();
    expect(heading.parentElement?.querySelector(".page-intro")).not.toBeNull();
    // The catalog row every non-chat view uses; the catalog is English until a setting says ja.
    fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

describe.each(["create", "save", "propose"] as const)("VaultBrowser pending %s", (kind) => {
  it.each(["success", "failure", "conflict"] as const)(
    "keeps submitted fields read-only and selectable until %s, then handles the result",
    async (result) => {
      const pendingWrite = deferred<Awaited<ReturnType<VaultWriteApi["saveNote"]>>>();
      const pendingProposal = deferred<CreatedProposal>();
      const createNote = vi.fn<VaultWriteApi["createNote"]>(() => pendingWrite.promise);
      const saveNote = vi.fn<VaultWriteApi["saveNote"]>(() => pendingWrite.promise);
      const createProposal = vi.fn(() => pendingProposal.promise);
      const original = "Original synthetic note.";
      const submitted = "Submitted synthetic draft A.";
      const ref = kind === "create" ? "notes/submitted.md" : "one.md";
      let storedText = original;
      const api = vaultApi({
        entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
        note: vi.fn(async (_id, requestedRef) => note(requestedRef, storedText)),
      });
      render(
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          {...(kind === "propose"
            ? { proposalApi: { createProposal } }
            : { write: write(writeApi({ createNote, saveNote })) })}
        />,
      );
      await debounce();
      if (kind === "create") {
        fireEvent.click(screen.getByRole("button", { name: "New note" }));
        fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
          target: { value: ref },
        });
      } else {
        await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
        fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
      }
      const editor = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
      expect(editor.readOnly).toBe(false);
      fireEvent.change(editor, { target: { value: submitted } });
      const submit = screen.getByRole<HTMLButtonElement>("button", {
        name: kind === "create" ? "Create note" : kind === "save" ? "Save note" : "Propose",
      });
      fireEvent.click(submit);
      expect(submit.disabled).toBe(true);
      fireEvent.click(submit);
      expect(createNote).toHaveBeenCalledTimes(kind === "create" ? 1 : 0);
      expect(saveNote).toHaveBeenCalledTimes(kind === "save" ? 1 : 0);
      expect(createProposal).toHaveBeenCalledTimes(kind === "propose" ? 1 : 0);
      if (kind === "create")
        expect(createNote).toHaveBeenCalledWith(W1, 4, "12:34", ref, submitted);
      else if (kind === "save")
        expect(saveNote).toHaveBeenCalledWith(W1, 4, "12:34", ref, submitted, "a".repeat(64));
      else expect(createProposal).toHaveBeenCalledWith(W1, ref, submitted, "a".repeat(64));

      expect(editor.readOnly).toBe(true);
      expect(editor.disabled).toBe(false);
      editor.focus();
      editor.select();
      expect(document.activeElement).toBe(editor);
      expect(editor.value.slice(editor.selectionStart, editor.selectionEnd)).toBe(submitted);
      if (kind === "create") {
        const path = screen.getByRole<HTMLInputElement>("textbox", { name: "Note path" });
        expect(path.readOnly).toBe(true);
        expect(path.disabled).toBe(false);
        path.focus();
        path.select();
        expect(document.activeElement).toBe(path);
        expect(path.value.slice(path.selectionStart ?? 0, path.selectionEnd ?? 0)).toBe(ref);
      }
      expect(hasUnsavedWork()).toBe(true);

      await act(async () => {
        if (result === "failure") {
          const error = new Error("synthetic submission refused");
          if (kind === "propose") pendingProposal.reject(error);
          else pendingWrite.reject(error);
        } else if (kind === "propose") {
          if (result === "conflict") {
            pendingProposal.reject(new DecisionsApiError("proposal_conflict"));
          } else {
            pendingProposal.resolve({
              id: W2,
              replayed: false,
              item: {
                kind: "vault_proposal",
                id: W2,
                origin: { workspace_id: W1, session_id: null },
                revision: "c".repeat(64),
                state: "open",
                attention: true,
                title: ref,
                ref,
                origin_kind: "note_edit",
                base_revision: "a".repeat(64),
                text_bytes: submitted.length,
                handed_at: null,
                created_at: CREATED_AT,
              },
            });
          }
        } else {
          if (result === "success") storedText = submitted;
          pendingWrite.resolve({
            operation_id: W1,
            status: result === "success" ? "saved" : "conflict",
            cleanup_pending: false,
          });
        }
      });

      if (result === "success") {
        expect(screen.queryByRole("textbox", { name: "Markdown" })).toBeNull();
        expect(screen.queryByRole("textbox", { name: "Note path" })).toBeNull();
        expect(hasUnsavedWork()).toBe(false);
        expect(
          screen
            .getByText(
              kind === "propose"
                ? "Recorded the proposal. It waits under Needs you in the Chat view's right panel."
                : "Saved.",
            )
            .getAttribute("role"),
        ).toBe("status");
        fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
        const reopened = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
        expect(reopened.readOnly).toBe(false);
        expect(reopened.value).toBe(kind === "propose" ? original : submitted);
      } else {
        expect(screen.getByRole("alert")).toBeTruthy();
        expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
        expect(editor.readOnly).toBe(false);
        expect(editor.value).toBe(submitted);
        expect(submit.disabled).toBe(false);
        const later = `${submitted} Later unsent text B.`;
        fireEvent.change(editor, { target: { value: later } });
        expect(editor.value).toBe(later);
        if (kind === "create") {
          const path = screen.getByRole<HTMLInputElement>("textbox", { name: "Note path" });
          expect(path.readOnly).toBe(false);
          expect(path.value).toBe(ref);
          fireEvent.change(path, { target: { value: "notes/later.md" } });
          expect(path.value).toBe("notes/later.md");
        }
        expect(hasUnsavedWork()).toBe(true);
      }
      expect(createNote).toHaveBeenCalledTimes(kind === "create" ? 1 : 0);
      expect(saveNote).toHaveBeenCalledTimes(kind === "save" ? 1 : 0);
      expect(createProposal).toHaveBeenCalledTimes(kind === "propose" ? 1 : 0);
    },
  );
});

describe("VaultBrowser folders, sort and bookmarks", () => {
  const FILES = [
    { ref: "b.md", title: "b.md", modified: 20 },
    { ref: "alpha/c.md", title: "alpha/c.md", modified: 30 },
    { ref: "alpha/a.md", title: "alpha/a.md", modified: 10 },
    { ref: "beta/d.md", title: "beta/d.md", modified: 40 },
  ];
  const listed = (
    extra: Record<string, unknown> = {},
    entries: unknown[] = FILES,
  ): Record<string, unknown> => ({ ...complete({ entries }), ...extra });
  const bookmarksOf = (refs: string[]) => ({
    bookmarks: refs.map((ref) => ({ ref, created_at: CREATED_AT })),
  });
  // The note buttons in the list, in order; a star button is the one that carries aria-pressed.
  const order = (): string[] =>
    within(screen.getByRole("navigation", { name: "Notes" }))
      .getAllByRole("button")
      .filter((button) => !button.hasAttribute("aria-pressed"))
      .map((button) => button.textContent ?? "");
  const star = (ref: string): HTMLElement =>
    screen.getByRole("button", { name: new RegExp(`^(Remove|Add) bookmark for ${ref}$`) });
  const choose = (name: RegExp, value: string): void => {
    fireEvent.change(screen.getByRole("combobox", { name }), { target: { value } });
  };
  async function mount(overrides: Partial<BrowserApi> = {}): Promise<BrowserApi> {
    const api = vaultApi({ entries: vi.fn(async () => listed()), ...overrides });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    return api;
  }

  it("lists every note by name until a folder is chosen, then only that folder's notes", async () => {
    await mount();
    expect(order()).toEqual(["alpha/a.md", "alpha/c.md", "b.md", "beta/d.md"]);
    const folder = screen.getByRole("combobox", { name: /Folder/ });
    expect(
      within(folder)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["All", "alpha", "beta"]);
    choose(/Folder/, "alpha");
    expect(order()).toEqual(["alpha/a.md", "alpha/c.md"]);
    choose(/Folder/, "");
    expect(order()).toHaveLength(4);
  });

  it("sorts by name by default and by most recent change on request", async () => {
    await mount();
    expect((screen.getByRole("combobox", { name: /Sort/ }) as HTMLSelectElement).value).toBe(
      "name",
    );
    choose(/Sort/, "recent");
    expect(order()).toEqual(["beta/d.md", "alpha/c.md", "b.md", "alpha/a.md"]);
    choose(/Sort/, "name");
    expect(order()).toEqual(["alpha/a.md", "alpha/c.md", "b.md", "beta/d.md"]);
  });

  it("stars and unstars a note, reading the bookmarks again after each change", async () => {
    let marked: string[] = [];
    const bookmarks = vi.fn(async () => bookmarksOf(marked));
    const addBookmark = vi.fn(async (_id: string, ref: string) => {
      marked = [...marked, ref];
      return { bookmark: { ref, created_at: CREATED_AT } };
    });
    const removeBookmark = vi.fn(async (_id: string, ref: string) => {
      marked = marked.filter((item) => item !== ref);
    });
    await mount({ bookmarks, addBookmark, removeBookmark });
    expect(bookmarks).toHaveBeenCalledTimes(1);
    expect(star("alpha/a.md").getAttribute("aria-pressed")).toBe("false");
    await act(async () => {
      fireEvent.click(star("alpha/a.md"));
    });
    expect(addBookmark).toHaveBeenCalledWith(W1, "alpha/a.md");
    expect(bookmarks).toHaveBeenCalledTimes(2);
    expect(star("alpha/a.md").getAttribute("aria-pressed")).toBe("true");
    expect(star("alpha/c.md").getAttribute("aria-pressed")).toBe("false");
    await act(async () => {
      fireEvent.click(star("alpha/a.md"));
    });
    expect(removeBookmark).toHaveBeenCalledWith(W1, "alpha/a.md");
    expect(bookmarks).toHaveBeenCalledTimes(3);
    expect(star("alpha/a.md").getAttribute("aria-pressed")).toBe("false");
  });

  it("takes a created_at that is an ISO string and rejects a number as invalid_response", async () => {
    await mount({ bookmarks: vi.fn(async () => bookmarksOf(["alpha/a.md"])) });
    expect(CREATED_AT).toHaveLength(24);
    expect(star("alpha/a.md").getAttribute("aria-pressed")).toBe("true");
    cleanup();
    const numeric = { bookmarks: [{ ref: "alpha/a.md", created_at: 1 }] };
    await mount({ bookmarks: vi.fn(async () => numeric) });
    expect(star("alpha/a.md").getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByRole("alert").textContent).toBe("Couldn't process the bookmark.");
  });

  it("shows only the bookmarked notes under the toggle, together with a folder choice", async () => {
    await mount({ bookmarks: vi.fn(async () => bookmarksOf(["alpha/c.md", "beta/d.md"])) });
    const toggle = screen.getByRole("checkbox", { name: /Bookmarks only/ });
    fireEvent.click(toggle);
    expect(order()).toEqual(["alpha/c.md", "beta/d.md"]);
    choose(/Folder/, "alpha");
    expect(order()).toEqual(["alpha/c.md"]);
    fireEvent.click(toggle);
    expect(order()).toEqual(["alpha/a.md", "alpha/c.md"]);
  });

  it("leaves the row unstarred and shows the typed code's copy when a star fails", async () => {
    const bookmarks = vi.fn(async () => bookmarksOf([]));
    const addBookmark = vi.fn(async () => {
      throw new Error("bookmarks_full");
    });
    await mount({ bookmarks, addBookmark });
    await act(async () => {
      fireEvent.click(star("b.md"));
    });
    expect(screen.getByRole("alert").textContent).toBe(
      "Bookmarks are full. Remove one you no longer need, then try again.",
    );
    expect(star("b.md").getAttribute("aria-pressed")).toBe("false");
    expect(bookmarks).toHaveBeenCalledTimes(1);
  });

  it("never renders a server string from a failed star, only the fixed copy", async () => {
    const addBookmark = vi.fn(async () => {
      throw new Error("<img src=x onerror=alert(1)>");
    });
    await mount({ addBookmark });
    await act(async () => {
      fireEvent.click(star("b.md"));
    });
    expect(screen.getByRole("alert").textContent).toBe("Couldn't process the bookmark.");
    expect(document.querySelector("img")).toBeNull();
  });

  it("keeps the partial-state line, with the server's reasons for the whole listing, under any filter", async () => {
    const line = /Results may be incomplete \(deadline, entry_limit; at least 3 omitted\)/;
    await mount({
      entries: vi.fn(async () => ({
        entries: FILES,
        partial: true,
        partial_reasons: ["deadline", "entry_limit"],
        omitted: 3,
      })),
    });
    expect(screen.getByText(line)).toBeTruthy();
    choose(/Folder/, "beta");
    expect(order()).toEqual(["beta/d.md"]);
    expect(screen.getByText(line)).toBeTruthy();
    // No bookmarks, so the toggle empties the list: the line must still stand.
    fireEvent.click(screen.getByRole("checkbox", { name: /Bookmarks only/ }));
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
    expect(screen.getByText("No notes match these filters.")).toBeTruthy();
    expect(screen.getByText(line)).toBeTruthy();
  });

  it("search scope is explicit and keeps browsing filters", async () => {
    const entries = vi.fn(async (_id: string, query?: string) =>
      query === undefined
        ? listed()
        : complete({ hits: [{ ref: "zeta.md", excerpt: "match", line: 3 }] }),
    );
    await mount({ entries, bookmarks: vi.fn(async () => bookmarksOf(["alpha/a.md"])) });
    choose(/Folder/, "alpha");
    choose(/Sort/, "recent");
    fireEvent.click(screen.getByRole("checkbox", { name: /Bookmarks only/ }));
    fireEvent.change(screen.getByLabelText(/Search note contents/), { target: { value: "match" } });
    expect(screen.getByText(/Searching all readable note contents\./)).toBeTruthy();
    await debounce();
    expect(
      screen.getByText(
        'Searching all readable note contents. Saved browsing filters: folder "alpha"; bookmarks only; most recent first.',
      ),
    ).toBeTruthy();
    expect(entries).toHaveBeenLastCalledWith(W1, "match", expect.anything());
    expect(order()).toEqual(["zeta.md — line 3: match"]);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(document.querySelector("[aria-pressed]")).toBeNull();
    fireEvent.change(screen.getByLabelText(/Search note contents/), { target: { value: "" } });
    await debounce();
    expect(screen.queryByText(/Searching all readable note contents\./)).toBeNull();
    expect((screen.getByRole("combobox", { name: /Folder/ }) as HTMLSelectElement).value).toBe(
      "alpha",
    );
    expect((screen.getByRole("combobox", { name: /Sort/ }) as HTMLSelectElement).value).toBe(
      "recent",
    );
    expect(
      (screen.getByRole("checkbox", { name: /Bookmarks only/ }) as HTMLInputElement).checked,
    ).toBe(true);
    expect(order()).toEqual(["alpha/a.md"]);
  });
});

describe("VaultBrowser initialRef", () => {
  const FILES = [
    { ref: "b.md", title: "b.md" },
    { ref: "alpha/c.md", title: "alpha/c.md" },
    { ref: "alpha/a.md", title: "alpha/a.md" },
    { ref: "beta/d.md", title: "beta/d.md" },
  ];
  const settle = async (): Promise<void> => {
    await debounce();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  };
  async function mount(props: { initialRef?: string; initialFolder?: string } = {}) {
    const api = vaultApi({ entries: vi.fn(async () => scan(FILES)) });
    const view = (extra: { initialRef?: string; initialFolder?: string }) => (
      <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} {...extra} />
    );
    const { rerender } = render(view(props));
    await settle();
    return { api, rerender: () => rerender(view(props)) };
  }
  const selectedTitle = (): string | undefined =>
    screen.queryByRole("article", { name: "Selected note" })
      ? screen.getByRole("heading", { level: 2 }).textContent
      : undefined;
  const folderValue = (): string =>
    (screen.getByRole("combobox", { name: /Folder/ }) as HTMLSelectElement).value;

  it("opens the named note, and moves the folder to the note's own", async () => {
    const { api } = await mount({ initialRef: "alpha/c.md", initialFolder: "beta" });
    expect(api.note).toHaveBeenCalledWith(W1, "alpha/c.md", expect.anything());
    expect(selectedTitle()).toBe("alpha/c.md");
    expect(folderValue()).toBe("alpha");
    expect(screen.getByRole("button", { name: "alpha/c.md" }).getAttribute("aria-current")).toBe(
      "true",
    );
  });

  it("opens a note at the vault root, and shows every folder", async () => {
    await mount({ initialRef: "b.md", initialFolder: "beta" });
    expect(selectedTitle()).toBe("b.md");
    expect(folderValue()).toBe("");
  });

  it("leaves the view as it is without the ref, and keeps initialFolder", async () => {
    const { api } = await mount({ initialRef: "alpha/gone.md", initialFolder: "beta" });
    expect(api.note).not.toHaveBeenCalled();
    expect(selectedTitle()).toBeUndefined();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(folderValue()).toBe("beta");
  });

  it("opens nothing when no ref is given", async () => {
    const { api } = await mount();
    expect(api.note).not.toHaveBeenCalled();
    expect(selectedTitle()).toBeUndefined();
  });

  it.each([false, true])(
    "discloses an unobserved initial folder with partial=%s",
    async (partial) => {
      const api = vaultApi({
        entries: vi.fn(async () => ({
          ...scan(FILES),
          partial,
          partial_reasons: partial ? ["deadline"] : [],
        })),
      });
      render(
        <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} initialFolder="missing" />,
      );
      await settle();
      expect(folderValue()).toBe("");
      expect(
        screen.getByText(
          `Folder "missing" was not found in this ${partial ? "partial listing" : "listing"}. Showing all folders.`,
        ),
      ).toBeTruthy();
      const notes = within(screen.getByRole("navigation", { name: "Notes" }));
      expect(notes.getByRole("button", { name: "alpha/a.md" })).toBeTruthy();
      expect(notes.getByRole("button", { name: "beta/d.md" })).toBeTruthy();
    },
  );

  it("applies once: a later render does not drag the user back to the ref", async () => {
    const { api, rerender } = await mount({ initialRef: "alpha/c.md" });
    fireEvent.click(screen.getByRole("button", { name: "alpha/a.md" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(selectedTitle()).toBe("alpha/a.md");
    rerender();
    await settle();
    expect(selectedTitle()).toBe("alpha/a.md");
    expect(api.note).toHaveBeenCalledTimes(2);
  });

  it("does not reopen the ref after the user returns to the list", async () => {
    const { rerender } = await mount({ initialRef: "alpha/c.md" });
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    rerender();
    await settle();
    expect(selectedTitle()).toBeUndefined();
  });
});

describe("VaultBrowser history", () => {
  const AT = "2026-03-04T12:00:00+00:00";
  const day = new Date(AT).toLocaleDateString();
  const commit = (short: string, subject: string) => ({ short, at: AT, subject });
  async function openRow(overrides: Partial<BrowserApi> = {}): Promise<BrowserApi> {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "a.md", title: "a.md" }])),
      ...overrides,
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "a.md" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    return api;
  }
  // The History section is closed by default (see the dedicated test below); every other test
  // here is about its content, so this helper opens it the same way the user would.
  const summary = () => screen.getByText("History", { selector: "summary" });
  async function open(overrides: Partial<BrowserApi> = {}): Promise<BrowserApi> {
    const api = await openRow(overrides);
    fireEvent.click(summary());
    return api;
  }
  const section = () => screen.getByRole("region", { name: "History" });

  it("starts closed, behind its own summary", async () => {
    await openRow({
      history: vi.fn(async () => complete({ state: "ready", commits: [commit("abc1234", "x")] })),
    });
    const details = section().querySelector("details");
    expect(details?.open).toBe(false);
    fireEvent.click(summary());
    expect(details?.open).toBe(true);
    expect(within(section()).getByRole("listitem")).toBeTruthy();
  });

  it("lists three commits with short hash, local date and subject", async () => {
    await open({
      history: vi.fn(async () =>
        complete({
          state: "ready",
          commits: [
            commit("c3c3c3c", "third"),
            commit("b2b2b2b", "second"),
            commit("a1a1a1a", "first"),
          ],
        }),
      ),
    });
    const rows = within(section()).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      `c3c3c3c ${day} third`,
      `b2b2b2b ${day} second`,
      `a1a1a1a ${day} first`,
    ]);
  });

  it("says the history could not be read for unknown, with no rows", async () => {
    await open({ history: vi.fn(async () => complete({ state: "unknown", commits: [] })) });
    expect(within(section()).getByText("History isn't available for this note.")).toBeTruthy();
    expect(within(section()).queryByText("No commits found for this note.")).toBeNull();
    expect(within(section()).queryAllByRole("listitem")).toHaveLength(0);
  });

  it("says no commit was found for an empty ready history", async () => {
    await open();
    expect(within(section()).getByText("No commits found for this note.")).toBeTruthy();
    expect(within(section()).queryByText("History isn't available for this note.")).toBeNull();
  });

  it("cancels the previous read when the selection switches", async () => {
    const signals: AbortSignal[] = [];
    const api = await open({
      entries: vi.fn(async () =>
        scan([
          { ref: "a.md", title: "a.md" },
          { ref: "b.md", title: "b.md" },
        ]),
      ),
      history: vi.fn(async (_id: string, _ref: string, signal?: AbortSignal) => {
        if (signal) signals.push(signal);
        return complete({ state: "ready", commits: [] });
      }),
    });
    fireEvent.click(screen.getByRole("button", { name: "b.md" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.history).toHaveBeenCalledTimes(2);
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
  });

  it("renders a subject as text, never as HTML", async () => {
    await open({
      history: vi.fn(async () =>
        complete({ state: "ready", commits: [commit("abc1234", "<img src=x onerror=alert(1)>")] }),
      ),
    });
    expect(within(section()).getByText(/<img src=x onerror=alert\(1\)>/)).toBeTruthy();
    expect(section().querySelector("img")).toBeNull();
  });

  it("shows an error line for a malformed payload", async () => {
    await open({
      history: vi.fn(async () => complete({ state: "ready", commits: [{ short: "x" }] })),
    });
    expect(within(section()).getByRole("alert").textContent).toBe("Couldn't read the history.");
  });

  it("marks the view open and returns to the list through the back control", async () => {
    await open();
    const view = screen.getByRole("region", { name: "Vault browser" });
    expect(view.className).toBe("vault-browser");
    expect(view.hasAttribute("data-open")).toBe(true);
    expect(screen.getByRole("article", { name: "Selected note" }).className).toBe(
      "vault-inspector",
    );
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    expect(view.hasAttribute("data-open")).toBe(false);
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    expect(screen.getByRole("navigation", { name: "Notes" }).className).toBe("vault-list");
  });
});

describe("VaultBrowser excerpt attach", () => {
  const SERVER_SHA = "e".repeat(64);
  const text = "one\ntwo\nthree\n";
  const served = (start: number, end: number, body = "one\ntwo"): VaultExcerpt => ({
    part: {
      kind: "vault_excerpt",
      vault_id: W1,
      ref: "a.md",
      start_line: start,
      end_line: end,
      sha256: SERVER_SHA,
    },
    text: body,
    block: `[vault excerpt] a.md, lines ${start}-${end}, sha256 eeeeeeeeeeee\n${body}`,
    block_bytes: body.length,
    revision: "r1",
    modified_at: "2026-10-02T00:00:00Z",
  });
  const flush = () =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  async function openRow(
    excerpt: ExcerptReader,
    onAttachExcerpt: (part: VaultExcerpt["part"]) => void = vi.fn(),
    noteText = text,
  ): Promise<void> {
    const api = {
      ...vaultApi({
        entries: vi.fn(async () => scan([{ ref: "a.md", title: "a.md" }])),
        note: vi.fn(async (_id, ref) => note(ref, noteText)),
      }),
      excerpt,
    };
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        onOpenUrl={vi.fn()}
        onAttachExcerpt={onAttachExcerpt}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "a.md" }));
    await flush();
  }
  // Attach excerpt lives in the Note actions menu: open the note, then the menu, then
  // its one menu item.
  async function openNote(
    excerpt: ExcerptReader,
    onAttachExcerpt: (part: VaultExcerpt["part"]) => void = vi.fn(),
    noteText = text,
  ): Promise<void> {
    await openRow(excerpt, onAttachExcerpt, noteText);
    fireEvent.click(screen.getByRole("button", { name: "Note actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Attach excerpt" }));
    await flush();
  }
  type ExcerptReader = (
    id: string,
    ref: string,
    start: number,
    end: number,
    signal?: AbortSignal,
  ) => Promise<VaultExcerpt>;
  const setLine = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  const alertText = () => screen.getByRole("alert").textContent;

  it("hands up the exact part the route returned, not one built here", async () => {
    const response = served(1, 2);
    const onAttach = vi.fn();
    await openNote(
      vi.fn(async () => response),
      onAttach,
    );
    setLine("Last line", "2");
    await flush();
    expect(
      screen.getByText("This content is attached after your message and sent to Claude."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Attach" }));
    expect(onAttach).toHaveBeenCalledTimes(1);
    // Identity, not equality: a client-side rebuild would be a different object, and would not
    // hold the server's digest.
    expect(onAttach.mock.calls[0]?.[0]).toBe(response.part);
    expect(onAttach.mock.calls[0]?.[0].sha256).toBe(SERVER_SHA);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("bounds both line inputs by the note's line count", async () => {
    await openNote(vi.fn(async () => served(1, 1)));
    for (const label of ["First line", "Last line"]) {
      const input = screen.getByLabelText(label) as HTMLInputElement;
      expect([input.min, input.max]).toEqual(["1", "3"]);
    }
  });

  it("shows the invalid_range copy for a span past the end and attaches nothing", async () => {
    const onAttach = vi.fn();
    await openNote(
      vi.fn(async (_id, _ref, _start, end) => {
        if (end > 3) throw new Error("invalid_range");
        return served(1, end);
      }),
      onAttach,
    );
    setLine("Last line", "99");
    await flush();
    expect(alertText()).toBe("Check the line range.");
    expect(screen.queryByRole("button", { name: "Attach" })?.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Attach" }));
    expect(onAttach).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeNull();
  });

  it("shows the excerpt_too_large copy for a 33 KiB span read through the real client", async () => {
    const big = Array.from({ length: 33 }, () => "x".repeat(1023)).join("\n");
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: { code: "excerpt_too_large" } }), {
          status: 413,
          headers: { "content-type": "application/json" },
        }),
    );
    const onAttach = vi.fn();
    await openNote(createExcerptApi("token", fetchImpl as typeof fetch).excerpt, onAttach, big);
    setLine("Last line", "33");
    await flush();
    expect(fetchImpl).toHaveBeenCalled();
    expect(alertText()).toBe("The selected range is too large.");
    fireEvent.click(screen.getByRole("button", { name: "Attach" }));
    expect(onAttach).not.toHaveBeenCalled();
  });

  it("closes on Escape with nothing attached", async () => {
    const onAttach = vi.fn();
    await openNote(
      vi.fn(async () => served(1, 1)),
      onAttach,
    );
    expect(screen.queryByRole("dialog")).not.toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onAttach).not.toHaveBeenCalled();
  });

  it("closes through Close with nothing attached", async () => {
    const onAttach = vi.fn();
    await openNote(
      vi.fn(async () => served(1, 1)),
      onAttach,
    );
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onAttach).not.toHaveBeenCalled();
  });

  it("renders the preview as a text node inside a scrolling pre", async () => {
    const body = "<img src=x onerror=alert(1)>";
    await openNote(vi.fn(async () => served(1, 1, body)));
    const dialog = screen.getByRole("dialog");
    const pre = dialog.querySelector("pre");
    expect(pre?.textContent).toBe(body);
    expect(pre?.childNodes).toHaveLength(1);
    expect(pre?.firstChild?.nodeType).toBe(Node.TEXT_NODE);
    expect(dialog.querySelector("img")).toBeNull();
    expect(pre?.style.overflow).toBe("auto");
  });

  it("offers no control without onAttachExcerpt", async () => {
    const api = {
      ...vaultApi({ entries: vi.fn(async () => scan([{ ref: "a.md", title: "a.md" }])) }),
      excerpt: vi.fn(async () => served(1, 1)),
    };
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "a.md" }));
    await flush();
    expect(screen.queryByRole("button", { name: "Note actions" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Attach excerpt" })).toBeNull();
  });

  it("shows a one-item Note actions menu for a read-only vault with no write client", async () => {
    await openRow(vi.fn(async () => served(1, 1)));
    fireEvent.click(screen.getByRole("button", { name: "Note actions" }));
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Attach excerpt",
    ]);
  });

  it("keeps bot-memory notes readable and bookmarkable without write actions", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "bot-memory/MEMORY.md", title: "Memory" }])),
    });
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        write={{
          ...write(writeApi()),
          ops: {
            preview: vi.fn(),
            apply: vi.fn(),
            recovery: vi.fn(async () => ({ entries: [], stuck: [] })),
            forgetOperation: vi.fn(),
            resolve: vi.fn(),
          } as unknown as VaultOpsApi,
        }}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    await flush();
    expect(screen.getByRole("region", { name: "Note content" })).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: /bookmark.*bot-memory\/MEMORY.md/i }),
    ).toHaveLength(2);
    expect(
      screen.getByText("Bot memory is read-only in Vault. Ask the Bot to change it from its page."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit note" })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Markdown" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Note actions" })).toBeNull();
  });

  it("uses the Bot-memory read-only copy only for top-level Bot-memory refs", async () => {
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "BoT-MeMoRy/bot/MEMORY.md", title: "Memory" },
          { ref: "notes/bot-memory/note.md", title: "Nested" },
        ]),
      ),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "Memory" }));
    await flush();
    expect(screen.getByText(vaultCatalog.en["vault.reader.bot_memory_read_only"])).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Nested" }));
    await flush();
    expect(screen.getByRole("button", { name: "Edit note" })).toBeTruthy();
    expect(screen.queryByText(vaultCatalog.en["vault.reader.bot_memory_read_only"])).toBeNull();
  });
});

describe("VaultBrowser reader body", () => {
  it("renders the body without its frontmatter, dropping a duplicate H1", async () => {
    const readerText = "---\ntitle: one.md\n---\n\n# one.md\n\nBody text.\n";
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "one.md" }])),
      note: vi.fn(async () => note("one.md", readerText)),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "one.md" })));
    const content = screen.getByRole("region", { name: "Note content" });
    expect(content.textContent?.trim()).toBe("Body text.");
  });

  it("shows the body whole when the frontmatter never closes", async () => {
    const readerText = "---\ntitle: one.md\nstill open\n\nBody text.\n";
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "one.md" }])),
      note: vi.fn(async () => note("one.md", readerText)),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "one.md" })));
    expect(screen.getByRole("region", { name: "Note content" }).textContent).toContain(
      "title: one.md",
    );
  });

  it("skips the wikilink rewrite above Markdown's render cap, showing the source as written", async () => {
    const bigBody = `[[Alpha_Note]]\n\n${"word ".repeat(60_000)}`;
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "big.md", title: "Big" }])),
      note: vi.fn(async () => note("big.md", bigBody)),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Big" })));
    const content = screen.getByRole("region", { name: "Note content" }).textContent ?? "";
    expect(content).toContain("[[Alpha_Note]]");
    expect(content).not.toContain("viki-note:");
    expect(content).not.toContain("\\_");
  });

  it("gates on the rewrite's own size: a body just under the cap whose linked form crosses it renders unrewritten", async () => {
    const { MAX_MARKDOWN_BYTES } = await import("../Conversation/Markdown");
    const links = "[[Alpha]] ".repeat(400); // grows past the cap only once rewritten
    const filler = "w".repeat(MAX_MARKDOWN_BYTES - 1_000 - links.length);
    const bigBody = `${links}\n\n${filler}`;
    expect(new TextEncoder().encode(bigBody).byteLength).toBeLessThan(MAX_MARKDOWN_BYTES);
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "big.md", title: "Big" }])),
      note: vi.fn(async () =>
        complete({
          note: {
            ref: "big.md",
            title: "big.md",
            text: bigBody,
            source: bigBody,
            sha256: "a".repeat(64),
            frontmatter_error: null,
          },
          resolved_links: [{ target: "Alpha", state: "resolved", candidates: ["alpha.md"] }],
        }),
      ),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Big" })));
    const content = screen.getByRole("region", { name: "Note content" }).textContent ?? "";
    expect(content).not.toContain("viki-note:");
  });

  it("memoizes the reader text so an unrelated re-render does not re-run the wikilink rewrite", async () => {
    const spy = vi.spyOn(wikilinksModule, "linkWikilinks");
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "See [[Other]].")),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    expect(spy).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
      target: { value: "x" },
    });
    expect(spy).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });
});

describe("VaultBrowser kanban card reader", () => {
  // CardReader.unit.test.tsx covers the fold behaviour itself; this only proves VaultBrowser.tsx
  // routes a kanban/ card to it (and an otherwise-identical non-card note to plain NoteMarkdown).
  const CARD_TEXT = `---
type: card
status: blocked
---

## Summary
Ship the thing.

## Needs you
Ship now?

## Done when
- [x] one
- [ ] two

## Handoff
- 2026-11-20 · engineer · blocked · this card · waits · none

## Check
Verdict: PASS
`;

  it("folds a kanban/ card's Done when, Handoff and Check in the reader", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "kanban/x.md", title: "x.md" }])),
      note: vi.fn(async () => note("kanban/x.md", CARD_TEXT)),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "x.md" })));
    expect(screen.getByText("Ship now?").closest("details")).toBeNull();
    const details = screen
      .getByText("Done when · 1 of 2 done", { selector: "summary" })
      .closest("details");
    expect(details?.open).toBe(false);
    expect(screen.getByText("Handoff · 1 entry", { selector: "summary" })).toBeTruthy();
    expect(screen.getByText("Check · PASS", { selector: "summary" })).toBeTruthy();
  });

  it("renders a non-card note with the same headings, unfolded", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "kanban/x.md", title: "x.md" }])),
      note: vi.fn(async () => note("kanban/x.md", CARD_TEXT.replace("type: card", "type: note"))),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "x.md" })));
    expect(screen.queryByText("Done when · 1 of 2 done", { selector: "summary" })).toBeNull();
    expect(screen.getAllByText("Done when")[0]?.closest("details")).toBeNull();
  });
});

describe("VaultBrowser note chips", () => {
  it("lists section, type, status and up to six tags, with an overflow count", async () => {
    const tags = Array.from({ length: 8 }, (_, index) => `t${index}`);
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "alpha/one.md", title: "One" }])),
      note: vi.fn(async () =>
        noteProjection("alpha/one.md", displayProjection({ type: "note", status: "draft", tags })),
      ),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    const chips = within(screen.getByRole("list", { name: "About this note" }))
      .getAllByRole("listitem")
      .map((item) => item.textContent);
    expect(chips).toEqual([
      "alpha",
      "note",
      "draft",
      "#t0",
      "#t1",
      "#t2",
      "#t3",
      "#t4",
      "#t5",
      "+2 more tags",
    ]);
  });

  it.each([
    ["Knowledge", "30-knowledge/one.md", "note"],
    ["Source", "40-sources/one.md", "source"],
    ["Map", "50-maps/one.md", "moc"],
  ])("hides a %s note's own default %s type as redundant (F17)", async (section, ref, type) => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref, title: "One" }])),
      overview: vi.fn(async () => overviewWithRef(ref)),
      note: vi.fn(async () => noteProjection(ref, displayProjection({ type }))),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "All notes" }));
    await act(async () =>
      fireEvent.click(
        within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", {
          name: "One",
        }),
      ),
    );
    expect(
      within(screen.getByRole("list", { name: "About this note" }))
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([section]);
  });

  it("still shows a type that is not the section's implied default", async () => {
    const ref = "30-knowledge/one.md";
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref, title: "One" }])),
      overview: vi.fn(async () => overviewWithRef(ref)),
      note: vi.fn(async () => noteProjection(ref, displayProjection({ type: "thinking" }))),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "All notes" }));
    await act(async () =>
      fireEvent.click(
        within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", {
          name: "One",
        }),
      ),
    );
    expect(
      within(screen.getByRole("list", { name: "About this note" }))
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["Knowledge", "thinking"]);
  });
});

describe("VaultBrowser connections", () => {
  it("lists a missing link as dim text and leaves external and unsafe links unlisted", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => ({
        ...note("one.md"),
        resolved_links: [
          { target: "Gone", state: "missing", candidates: [] },
          { target: "https://example.com", state: "external", candidates: [] },
          { target: "file:///etc/passwd", state: "unsafe", candidates: [] },
        ],
      })),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    const links = screen.getByRole("region", { name: "Links" });
    expect(within(links).getByText("Gone (no note yet)")).toBeTruthy();
    expect(within(links).queryByRole("button")).toBeNull();
    expect(screen.queryByText(/example\.com/)).toBeNull();
    expect(screen.queryByText(/etc\/passwd/)).toBeNull();
  });

  it("gives link and backlink cards exact refs beneath their buttons", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) => ({
        ...note(ref, "[[Two]]"),
        resolved_links: [{ target: "Two", state: "resolved", candidates: ["two.md"] }],
      })),
      backlinks: vi.fn(async () => complete({ backlinks: [{ source: "two.md", line: 1 }] })),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    const linkButton = within(screen.getByRole("region", { name: "Links" })).getByRole("button", {
      name: "two",
    });
    const backlinkButton = within(screen.getByRole("region", { name: "Backlinks" })).getByRole(
      "button",
      { name: "two" },
    );
    expect(linkButton.className).toContain("note-card-title");
    expect(backlinkButton.className).toContain("note-card-title");
    const refs = screen.getAllByText("two.md", { selector: ".note-card-ref" });
    expect(refs).toHaveLength(2);
    expect(linkButton.getAttribute("aria-describedby")).toBe(refs[0]?.id);
    expect(backlinkButton.getAttribute("aria-describedby")).toBe(refs[1]?.id);
  });

  it("styles Related card buttons as text, not native button chrome", () => {
    const css = readCss(import.meta.url, "NoteCard.css");
    const base = ruleIn(css, ".note-card-title");
    expect(base).toMatch(/background:\s*none;/);
    expect(base).toMatch(/border:\s*0;/);
    expect(base).toMatch(/text-align:\s*left;/);
    expect(ruleIn(css, ".note-card:has(:focus-visible)")).toMatch(
      /outline:\s*2px solid var\(--or\);/,
    );
  });
});

describe("VaultBrowser wikilinks", () => {
  it("renders a resolved wikilink as a button named by its label that opens the note", async () => {
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "a.md", title: "A" },
          { ref: "b.md", title: "B" },
        ]),
      ),
      note: vi.fn(async (_id, ref) => ({
        ...note(ref, ref === "a.md" ? "See [[B|the other note]].\n" : "b"),
        resolved_links:
          ref === "a.md" ? [{ target: "B", state: "resolved", candidates: ["b.md"] }] : [],
      })),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "A" })));
    const content = screen.getByRole("region", { name: "Note content" });
    const link = within(content).getByRole("button", { name: "the other note" });
    await act(async () => fireEvent.click(link));
    expect(api.note).toHaveBeenCalledWith(W1, "b.md", expect.anything());
  });

  it("renders an unresolved wikilink as plain text with no brackets and no button", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "a.md", title: "A" }])),
      note: vi.fn(async () => note("a.md", "See [[Nope|missing note]].\n")),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "A" })));
    const content = screen.getByRole("region", { name: "Note content" });
    expect(content.textContent).toBe("See missing note.");
    expect(within(content).queryByRole("button")).toBeNull();
  });

  it("gives a resolved wikilink a resting-state cue, not just on hover", () => {
    const css = readCss(import.meta.url, "VaultBrowser.css");
    const base = ruleIn(css, ".vault-note-content .md-note-link");
    expect(base).toMatch(/text-decoration:\s*underline;/);
    expect(base).toMatch(/text-decoration-color:\s*var\(--t3\);/);
    expect(base).toMatch(/text-underline-offset:\s*2px;/);
    expect(ruleIn(css, ".vault-note-content .md-note-link:focus-visible")).toMatch(
      /outline:\s*2px solid var\(--or\);/,
    );
  });
});

describe("VaultBrowser proposals", () => {
  const ITEM = {
    kind: "vault_proposal",
    id: W2,
    origin: { workspace_id: W1, session_id: null },
    revision: "c".repeat(64),
    state: "open",
    attention: true,
    title: "one.md",
    ref: "one.md",
    origin_kind: "note_edit",
    base_revision: "a".repeat(64),
    text_bytes: 7,
    handed_at: null,
    created_at: CREATED_AT,
  } as const;
  const created = (): CreatedProposal => ({ id: W2, item: ITEM, replayed: false });
  type Create = (
    workspaceId: string,
    ref: string,
    text: string,
    baseRevision: string,
  ) => Promise<CreatedProposal>;
  const read = () =>
    vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "original")),
    });

  async function openEditor(props: Record<string, unknown>, api: BrowserApi = read()) {
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} {...props} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
  }
  const propose = async () =>
    act(async () => fireEvent.click(screen.getByRole("button", { name: "Propose" })));
  const draftValue = () =>
    (screen.getByRole("textbox", { name: "Markdown" }) as HTMLTextAreaElement).value;

  it("shows Propose and no Save for a protected vault, whatever the platform", async () => {
    // A protected vault gets no `write` context on any platform; the proposal path needs none.
    await openEditor({ proposalApi: { createProposal: vi.fn<Create>(async () => created()) } });
    expect(screen.getByRole("button", { name: "Propose" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Save note" })).toBeNull();
  });

  it("shows Save and no Propose for an ordinary vault", async () => {
    await openEditor({ write: write(writeApi()) });
    expect(screen.getByRole("button", { name: "Save note" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Propose" })).toBeNull();
  });

  it("offers no editor at all to a vault with neither", async () => {
    render(<VaultBrowser workspaceId={W1} api={read()} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    expect(screen.queryByRole("button", { name: "Edit note" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Propose" })).toBeNull();
  });

  it("proposes the draft against the note's sha and records it on a 201", async () => {
    const createProposal = vi.fn<Create>(async () => created());
    await openEditor({ proposalApi: { createProposal } });
    await propose();
    expect(createProposal).toHaveBeenCalledWith(W1, "one.md", "changed", "a".repeat(64));
    expect(screen.getByRole("status").textContent).toBe(
      "Recorded the proposal. It waits under Needs you in the Chat view's right panel.",
    );
    // The note was not written, so its editor closes with nothing left to send twice.
    expect(screen.queryByRole("textbox", { name: "Markdown" })).toBeNull();
    expect(hasUnsavedWork()).toBe(false);
  });

  it("says so on a replay, which carries no item", async () => {
    const createProposal = vi.fn<Create>(async () => ({ id: W2, item: null, replayed: true }));
    await openEditor({ proposalApi: { createProposal } });
    await propose();
    expect(screen.getByRole("status").textContent).toBe("The same proposal already exists.");
  });

  it("keeps a CRLF note's line endings in the proposed text", async () => {
    const createProposal = vi.fn<Create>(async () => created());
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "a\r\nb\r\n")),
    });
    await openEditor({ proposalApi: { createProposal } }, api);
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "a\nc\n" },
    });
    await propose();
    expect(createProposal.mock.calls[0]?.[2]).toBe("a\r\nc\r\n");
  });

  it.each([
    ["no_change", "This is identical to the current note."],
    ["proposal_conflict", "This note already has another proposal. Withdraw it first."],
    ["proposals_full", "Proposals are full. Withdraw one you no longer need, then try again."],
    ["too_large", "The proposal is too large."],
    ["unavailable", "Can't process the proposal."],
    ["not_protected", "Can't process the proposal."],
  ])("shows the %s line and keeps the draft", async (code, copy) => {
    const createProposal = vi.fn<Create>(async () => {
      throw new DecisionsApiError(code);
    });
    await openEditor({ proposalApi: { createProposal } });
    await propose();
    expect(screen.getByRole("alert").textContent).toBe(copy);
    expect(draftValue()).toBe("changed");
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();
    // Nothing is locked: the same button can try again.
    expect(screen.getByRole("button", { name: "Propose" }).hasAttribute("disabled")).toBe(false);
  });

  it("shows the unavailable line for a failure that is not a typed error", async () => {
    const createProposal = vi.fn<Create>(async () => {
      throw new Error("network");
    });
    await openEditor({ proposalApi: { createProposal } });
    await propose();
    expect(screen.getByRole("alert").textContent).toBe("Can't process the proposal.");
  });

  it("offers a reload on stale_revision that re-reads the note after a confirm", async () => {
    const createProposal = vi.fn<Create>(async () => {
      throw new DecisionsApiError("stale_revision", { current_revision: "b".repeat(64) });
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const api = read();
    await openEditor({ proposalApi: { createProposal } }, api);
    await propose();
    expect(screen.getByRole("alert").textContent).toBe("The note has changed. Reload it.");
    vi.mocked(api.note).mockClear();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Reload" })));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(api.note).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("textbox", { name: "Markdown" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();
    confirm.mockRestore();
  });

  it("keeps the stale draft when the reload confirm is declined", async () => {
    const createProposal = vi.fn<Create>(async () => {
      throw new DecisionsApiError("stale_revision");
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const api = read();
    await openEditor({ proposalApi: { createProposal } }, api);
    await propose();
    vi.mocked(api.note).mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(api.note).not.toHaveBeenCalled();
    expect(draftValue()).toBe("changed");
    confirm.mockRestore();
  });

  it("disables Propose while a proposal is in flight and sends it once", async () => {
    const pending = deferred<CreatedProposal>();
    const createProposal = vi.fn<Create>(() => pending.promise);
    await openEditor({ proposalApi: { createProposal } });
    fireEvent.click(screen.getByRole("button", { name: "Propose" }));
    expect(screen.getByRole("button", { name: "Propose" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Propose" }));
    expect(createProposal).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve(created()));
  });

  it("drops a late answer once the user has left the draft", async () => {
    const pending = deferred<CreatedProposal>();
    const createProposal = vi.fn<Create>(() => pending.promise);
    await openEditor({ proposalApi: { createProposal } });
    fireEvent.click(screen.getByRole("button", { name: "Propose" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await act(async () => pending.resolve(created()));
    expect(
      screen.queryByText(
        "Recorded the proposal. It waits under Needs you in the Chat view's right panel.",
      ),
    ).toBeNull();
  });

  it("hides Edit note for a ref the writer refuses, as for Save", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "CLAUDE.md", title: "One" }])),
      note: vi.fn(async () => note("CLAUDE.md", "text")),
    });
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        proposalApi={{ createProposal: vi.fn<Create>(async () => created()) }}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    expect(screen.queryByRole("button", { name: "Edit note" })).toBeNull();
    expect(screen.getByText("This file is not edited in the app.")).toBeTruthy();
  });
});

describe("VaultBrowser note operations", () => {
  const SHA = "b".repeat(64);
  const opsApi = (): VaultOpsApi => ({
    preview: vi.fn(async () => ({
      steps: [{ n: 1, action: "move" as const, ref: "one.md", to: "two.md" }],
      creates_folders: [],
      recovery_days: 30,
    })),
    apply: vi.fn(async () => ({
      operation_id: W1,
      status: "done" as const,
      replayed: false,
      steps_applied: 1,
      reason: null,
      created_folders: [],
    })),
    recovery: vi.fn(async () => ({ entries: [], stuck: [] })),
    forgetOperation: vi.fn(),
    resolve: vi.fn(),
  });
  const row = (workspace: string, ref: string) => ({
    operation_id: W2,
    workspace_id: workspace,
    ref,
    state: "conflict" as const,
    reason: "conflict" as const,
    resolvable: ["forget" as const],
  });
  const read = () =>
    vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => noteWithHash("one.md", "original", SHA)),
    });

  it("support panels follow the note body", async () => {
    const knowledge = {
      memories: vi.fn(async () => ({ items: [], invalid: [], partial: false })),
    } as unknown as VaultKnowledgeApi;
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read()}
        write={{ ...write(writeApi()), ops: opsApi() }}
        memory={{ api: knowledge, policyRevision: 4, rootPhysicalId: "12:34", protected: true }}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    const notes = screen.getByRole("navigation", { name: "Notes" });
    const inspector = screen.getByRole("article", { name: "Selected note" });
    // The reader sits beside the list's pane (filters, list, pager).
    expect(notes.closest(".vault-browse")?.nextElementSibling).toBe(inspector);
    for (const name of ["Recovery", "Memory"]) {
      const support = screen.getByRole("region", { name, hidden: true });
      expect(inspector.compareDocumentPosition(support) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
    }
  });

  it("does not hide Back to notes in desktop CSS", () => {
    const css = readCss(import.meta.url, "VaultBrowser.css");
    expect(css).toContain("@media (min-width: 800px)");
    expect(css).not.toMatch(/\.vault-back\s*\{[^}]*display:\s*none/);
  });

  async function openNote(
    props: { ops?: VaultOpsApi; save?: VaultWriteApi; api?: BrowserApi } = {},
  ) {
    const ops = props.ops ?? opsApi();
    const save = props.save ?? writeApi();
    const api = props.api ?? read();
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        write={{ ...write(save), ops }}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    return { ops, save, api };
  }
  async function operate(kind: string) {
    fireEvent.click(screen.getByRole("button", { name: "Note actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: kind }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Review" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply" })));
  }

  it("hands the open note's hash to the operation", async () => {
    const { ops } = await openNote();
    await operate("Rename");
    expect(ops.apply).toHaveBeenCalledWith(
      W1,
      expect.objectContaining({
        kind: "rename",
        ref: "one.md",
        expectedSha256: SHA,
        policyRevision: 4,
        rootPhysicalId: "12:34",
      }),
      expect.any(String),
    );
  });

  it.each([["Rename"], ["Delete"]])(
    "closes the note and its open editor once %s is done",
    async (kind) => {
      await openNote();
      fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
      expect(screen.getByRole("textbox", { name: "Markdown" })).toBeTruthy();
      await operate(kind);
      expect(screen.queryByRole("textbox", { name: "Markdown" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Save note" })).toBeNull();
      expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    },
  );

  it("offers no operation while an edit is unsaved", async () => {
    await openNote();
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    expect(screen.getByRole("button", { name: "Note actions" })).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
    expect(screen.queryByRole("button", { name: "Note actions" })).toBeNull();
  });

  it("reads the listing and the recovery rows again after an operation", async () => {
    const save = writeApi({ recovery: vi.fn(async () => []) });
    const { api } = await openNote({ save });
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(save.recovery).toHaveBeenCalledTimes(1);
    await operate("Delete");
    await debounce();
    expect(api.entries).toHaveBeenCalledTimes(2);
    expect(save.recovery).toHaveBeenCalledTimes(2);
  });

  it("reads the recovery lists again after a delete, through the ops client", async () => {
    const { ops } = await openNote();
    expect(ops.recovery).toHaveBeenCalledTimes(1);
    await operate("Delete");
    expect(ops.recovery).toHaveBeenCalledTimes(2);
  });

  it("refreshes the lists but keeps the note open on an outcome that stopped", async () => {
    const ops = opsApi();
    (ops.apply as ReturnType<typeof vi.fn>).mockResolvedValue({
      operation_id: W1,
      status: "recovery_required",
      replayed: false,
      steps_applied: 1,
      reason: null,
      created_folders: [],
    });
    const save = writeApi({ recovery: vi.fn(async () => []) });
    const { api } = await openNote({ ops, save });
    await operate("Delete");
    await debounce();
    expect(ops.recovery).toHaveBeenCalledTimes(2);
    expect(save.recovery).toHaveBeenCalledTimes(2);
    expect(api.entries).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("article", { name: "Selected note" })).toBeTruthy();
  });

  it("moves focus to the view's heading once an operation closes the note", async () => {
    await openNote();
    // A real click focuses the control, so the menu item holds focus when the dialog opens and is
    // gone by then: nothing is left for the dialog to hand focus back to.
    fireEvent.click(screen.getByRole("button", { name: "Note actions" }));
    const item = screen.getByRole("menuitem", { name: "Delete" });
    item.focus();
    fireEvent.click(item);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Review" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply" })));
    await debounce();
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  });

  it("feeds the recovery panel only this workspace's save records", async () => {
    const save = writeApi({
      recovery: vi.fn(async () => [row(W1, "mine.md"), row(W2, "theirs.md")]),
    });
    await openNote({ save });
    expect(screen.getByText("mine.md")).toBeTruthy();
    expect(screen.queryByText("theirs.md")).toBeNull();
  });

  it("shows no operation and no recovery panel without the ops client", async () => {
    const save = writeApi();
    render(<VaultBrowser workspaceId={W1} api={read()} write={write(save)} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "One" }));
    await act(async () => Promise.resolve());
    expect(screen.queryByRole("button", { name: "Note actions" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Recovery" })).toBeNull();
    expect(save.recovery).not.toHaveBeenCalled();
  });

  describe("with a recovery-only context (the folder is not available)", () => {
    const stuck = {
      operation_id: W2,
      kind: "move" as const,
      state: "recovery_required",
      paths: ["a.md", "b.md"],
    };
    function mountStranded(save: VaultWriteApi, ops: VaultOpsApi) {
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read()}
          recoveryOnly={{ api: save, ops }}
          onOpenUrl={vi.fn()}
        />,
      );
    }

    it("mounts the forget-only panel and no write surface, without `write`", async () => {
      const ops = opsApi();
      (ops.recovery as ReturnType<typeof vi.fn>).mockResolvedValue({ entries: [], stuck: [stuck] });
      const save = writeApi({ recovery: vi.fn(async () => [row(W1, "mine.md")]) });
      mountStranded(save, ops);
      await debounce();
      expect(screen.getByText("a.md → b.md")).toBeTruthy();
      expect(screen.getByText("mine.md")).toBeTruthy();
      expect(screen.getAllByRole("button", { name: "Discard record" })).toHaveLength(2);
      expect(screen.queryByText("Deleted notes")).toBeNull();
      expect(screen.queryByRole("button", { name: "New note" })).toBeNull();
      expect(screen.queryByRole("button", { name: "Delete previous version" })).toBeNull();
      expect(screen.queryByRole("region", { name: "Memory" })).toBeNull();
    });

    it("reads both lists again after a forget", async () => {
      const ops = opsApi();
      (ops.recovery as ReturnType<typeof vi.fn>).mockResolvedValue({ entries: [], stuck: [stuck] });
      (ops.forgetOperation as ReturnType<typeof vi.fn>).mockResolvedValue({
        operation_id: W2,
        recovery_ids: [],
        stage_retained: false,
      });
      const save = writeApi({ recovery: vi.fn(async () => []) });
      mountStranded(save, ops);
      await debounce();
      fireEvent.click(screen.getByRole("button", { name: "Discard record" }));
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply" })));
      expect(ops.forgetOperation).toHaveBeenCalledWith(W2);
      expect(save.recovery).toHaveBeenCalledTimes(2);
      expect(ops.recovery).toHaveBeenCalledTimes(3);
    });
  });

  it("mounts the memory panel only when given a memory context", async () => {
    const memories = vi.fn(async () => ({ items: [], invalid: [], partial: false }));
    const knowledge = { memories } as unknown as VaultKnowledgeApi;
    const { rerender } = render(<VaultBrowser workspaceId={W1} api={read()} onOpenUrl={vi.fn()} />);
    expect(screen.queryByRole("region", { name: "Memory" })).toBeNull();
    rerender(
      <VaultBrowser
        workspaceId={W1}
        api={read()}
        onOpenUrl={vi.fn()}
        memory={{ api: knowledge, policyRevision: 4, rootPhysicalId: "12:34", protected: true }}
      />,
    );
    await act(async () => Promise.resolve());
    expect(screen.getByRole("region", { name: "Memory", hidden: true })).toBeTruthy();
    expect(memories).toHaveBeenCalledWith(W1);
  });

  it("mounts the Memory panel closed, with its region present", async () => {
    const memories = vi.fn(async () => ({ items: [], invalid: [], partial: false }));
    const knowledge = { memories } as unknown as VaultKnowledgeApi;
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read()}
        onOpenUrl={vi.fn()}
        memory={{ api: knowledge, policyRevision: 4, rootPhysicalId: "12:34", protected: true }}
      />,
    );
    await debounce();
    await act(async () => Promise.resolve());
    const details = screen.getByText("Memory", { selector: "summary" }).closest("details");
    expect(details?.open).toBe(false);
    expect(
      within(details as HTMLElement).getByRole("region", { name: "Memory", hidden: true }),
    ).toBeTruthy();
  });

  it.each(["save record", "stuck operation"] as const)(
    "shows a recovery notice and opens Recovery for a pending %s",
    async (kind) => {
      const save = writeApi(
        kind === "save record" ? { recovery: vi.fn(async () => [row(W1, "mine.md")]) } : {},
      );
      const ops = opsApi();
      if (kind === "stuck operation")
        (ops.recovery as ReturnType<typeof vi.fn>).mockResolvedValue({
          entries: [],
          stuck: [
            {
              operation_id: W2,
              kind: "move" as const,
              state: "recovery_required",
              paths: ["a.md", "b.md"],
            },
          ],
        });
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read()}
          write={{ ...write(save), ops }}
          onOpenUrl={vi.fn()}
        />,
      );
      await debounce();
      await act(async () => Promise.resolve());
      expect(screen.getByText("A save or note operation didn't finish.")).toBeTruthy();
      const details = screen.getByText("Recovery", { selector: "summary" }).closest("details");
      expect(details?.open).toBe(true);
    },
  );

  it("Review recovery opens Recovery and focuses its summary", async () => {
    const save = writeApi({ recovery: vi.fn(async () => [row(W1, "mine.md")]) });
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read()}
        write={{ ...write(save), ops: opsApi() }}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    await act(async () => Promise.resolve());
    const summary = screen.getByText("Recovery", { selector: "summary" });
    const details = summary.closest("details") as HTMLDetailsElement;
    details.open = false;
    fireEvent.click(screen.getByRole("button", { name: "Review recovery" }));
    expect(details.open).toBe(true);
    expect(document.activeElement).toBe(summary);
  });
});

describe("VaultBrowser supplied page Back", () => {
  it("replaces the page Back in its original position and passes the current callback", async () => {
    const onBack = vi.fn();
    const renderBack = vi.fn((back: () => void) => (
      <button type="button" onClick={back}>
        Back
      </button>
    ));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi()}
        onOpenUrl={vi.fn()}
        onBack={onBack}
        renderBack={renderBack}
      />,
    );
    await debounce();
    const heading = screen.getByRole("heading", { level: 1 });
    const back = screen.getByRole("button", { name: "Back" });
    expect(document.activeElement).toBe(heading);
    expect(heading.closest(".page-head")?.previousElementSibling).toBe(back);
    expect(screen.queryByRole("button", { name: "Back to chat" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Back" })).toHaveLength(1);
    expect(renderBack).toHaveBeenLastCalledWith(onBack);
    fireEvent.click(back);
    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("does not inject a page Back or invoke its renderer without onBack", async () => {
    const renderBack = vi.fn((back: () => void) => (
      <button type="button" onClick={back}>
        Back
      </button>
    ));
    render(
      <VaultBrowser
        workspaceId={W1}
        api={vaultApi()}
        onOpenUrl={vi.fn()}
        renderBack={renderBack}
      />,
    );
    await debounce();
    expect(renderBack).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Back to chat" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  });

  it("retains caller admission and the separate dirty Back to notes", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const leave = vi.fn();
    const onBack = vi.fn(() => {
      if (!hasUnsavedWork() || window.confirm("Leave the vault?")) leave();
    });
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => note("one.md", "original")),
    });
    const writer = writeApi();
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        write={write(writer)}
        onOpenUrl={vi.fn()}
        onBack={onBack}
        renderBack={(back) => (
          <button type="button" onClick={back}>
            Back
          </button>
        )}
      />,
    );
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const editor = screen.getByRole("textbox", { name: "Markdown" });
    fireEvent.change(editor, { target: { value: "local draft" } });
    const pageBack = screen.getByRole("button", { name: "Back" });
    fireEvent.click(pageBack);
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenLastCalledWith("Leave the vault?");
    expect(leave).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    expect(confirm).toHaveBeenLastCalledWith("Discard your unsaved changes to this note?");
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
    expect((editor as HTMLTextAreaElement).value).toBe("local draft");
    expect(screen.getByRole("heading", { name: "one.md" })).toBeTruthy();
    expect(api.note).toHaveBeenCalledTimes(1);
    expect(writer.saveNote).not.toHaveBeenCalled();
    expect(writer.createNote).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    expect(hasUnsavedWork()).toBe(false);
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(leave).not.toHaveBeenCalled();
    fireEvent.click(pageBack);
    expect(onBack).toHaveBeenCalledTimes(2);
    expect(leave).toHaveBeenCalledTimes(1);
    expect(confirm).toHaveBeenCalledTimes(3);
    confirm.mockRestore();
  });
});

describe("VaultBrowser bounded note navigation", () => {
  it("rereads A on Back and restores the list's filters, opener and outer offset", async () => {
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "alpha/a.md", title: "A" },
          { ref: "alpha/b.md", title: "B" },
        ]),
      ),
      bookmarks: vi.fn(async () => ({
        bookmarks: [
          { ref: "alpha/a.md", created_at: CREATED_AT },
          { ref: "alpha/b.md", created_at: CREATED_AT },
        ],
      })),
      note: vi.fn(async (_id, ref) => ({
        ...note(ref, ref === "alpha/a.md" ? "fresh A" : "B"),
        resolved_links:
          ref === "alpha/a.md"
            ? [{ target: "B", state: "resolved", candidates: ["alpha/b.md"] }]
            : [],
      })),
    });
    const { container } = render(
      <div data-region="view">
        <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />
      </div>,
    );
    await debounce();
    fireEvent.change(screen.getByRole("combobox", { name: /Folder/ }), {
      target: { value: "alpha" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: /Sort/ }), {
      target: { value: "recent" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /Bookmarks only/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
      target: { value: "match" },
    });
    await debounce();
    const region = container.firstElementChild as HTMLElement;
    region.scrollTop = 217;
    region.scrollLeft = 13;
    const opener = screen.getByRole("button", { name: "A" });
    await act(async () => fireEvent.click(opener));
    region.scrollTop = 93;
    // Related uses the already-read listing label for this exact ref.
    const link = within(screen.getByRole("region", { name: "Links" })).getByRole("button", {
      name: "B",
    });
    await act(async () => fireEvent.click(link));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
    expect(api.note).toHaveBeenNthCalledWith(3, W1, "alpha/a.md", expect.anything());
    expect(screen.getByText("fresh A")).toBeTruthy();
    expect(document.activeElement).toBe(
      within(screen.getByRole("region", { name: "Links" })).getByRole("button", { name: "B" }),
    );
    expect(region.scrollTop).toBe(93);
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    expect(document.activeElement).toBe(opener);
    expect([region.scrollTop, region.scrollLeft]).toEqual([217, 13]);
    expect(
      (screen.getByRole("textbox", { name: "Search note contents" }) as HTMLInputElement).value,
    ).toBe("match");
    fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
      target: { value: "" },
    });
    await debounce();
    expect((screen.getByRole("combobox", { name: /Folder/ }) as HTMLSelectElement).value).toBe(
      "alpha",
    );
    expect((screen.getByRole("combobox", { name: /Sort/ }) as HTMLSelectElement).value).toBe(
      "recent",
    );
    expect(
      (screen.getByRole("checkbox", { name: /Bookmarks only/ }) as HTMLInputElement).checked,
    ).toBe(true);
    expect(api.note).toHaveBeenCalledTimes(3);
    expect(api.addBookmark).not.toHaveBeenCalled();
    expect(api.removeBookmark).not.toHaveBeenCalled();
  });
});

describe("VaultBrowser note navigation admission", () => {
  const files = ["a.md", "b.md", "c.md"].map((ref) => ({ ref, title: ref }));
  const read = () =>
    vaultApi({
      entries: vi.fn(async () => scan(files)),
      note: vi.fn(async (_id, ref) => ({
        ...note(ref, `original ${ref}`),
        resolved_links: [{ target: "c", state: "resolved", candidates: ["c.md"] }],
      })),
      backlinks: vi.fn(async () => complete({ backlinks: [{ source: "c.md", line: 2 }] })),
    });
  const row = (ref: string) =>
    within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", { name: ref });
  const open = async (ref: string) => act(async () => fireEvent.click(row(ref)));
  it("does not reread, prompt or alter history for the same dirty ref", async () => {
    const api = read();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    await open("a.md");
    await open("b.md");
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const editor = screen.getByRole("textbox", { name: "Markdown" });
    fireEvent.change(editor, { target: { value: "draft B" } });
    await open("b.md");
    expect(api.note).toHaveBeenCalledTimes(2);
    expect(confirm).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
    expect((editor as HTMLTextAreaElement).value).toBe("draft B");
    confirm.mockReturnValue(true);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
    expect(api.note).toHaveBeenLastCalledWith(W1, "a.md", expect.anything());
    expect(api.note).toHaveBeenCalledTimes(3);
    confirm.mockRestore();
  });
  it.each(["row", "link", "backlink"])(
    "keeps the same draft, focus and stack when %s and Back are declined",
    async (kind) => {
      const api = read();
      const writer = writeApi();
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      render(<VaultBrowser workspaceId={W1} api={api} write={write(writer)} onOpenUrl={vi.fn()} />);
      await debounce();
      await open("a.md");
      await open("b.md");
      fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
      const editor = screen.getByRole("textbox", { name: "Markdown" });
      fireEvent.change(editor, { target: { value: "held B" } });
      editor.focus();
      const target =
        kind === "row"
          ? row("c.md")
          : within(
              screen.getByRole("region", { name: kind === "link" ? "Links" : "Backlinks" }),
            ).getByRole("button");
      fireEvent.click(target);
      fireEvent.click(screen.getByRole("button", { name: "Back" }));
      expect(confirm).toHaveBeenCalledTimes(2);
      expect(document.activeElement).toBe(editor);
      expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
      expect((editor as HTMLTextAreaElement).value).toBe("held B");
      expect(api.note).toHaveBeenCalledTimes(2);
      expect(writer.saveNote).not.toHaveBeenCalled();
      expect(writer.createNote).not.toHaveBeenCalled();
      confirm.mockReturnValue(true);
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
      expect(api.note).toHaveBeenLastCalledWith(W1, "a.md", expect.anything());
      expect(api.note).toHaveBeenCalledTimes(3);
      confirm.mockRestore();
    },
  );
  it("refuses a pending save even after Cancel without recording or popping a step", async () => {
    const api = read();
    const pending = deferred<Awaited<ReturnType<VaultWriteApi["saveNote"]>>>();
    const writer = writeApi({ saveNote: vi.fn(() => pending.promise) });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<VaultBrowser workspaceId={W1} api={api} write={write(writer)} onOpenUrl={vi.fn()} />);
    await debounce();
    await open("a.md");
    await open("b.md");
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const editor = screen.getByRole("textbox", { name: "Markdown" });
    fireEvent.change(editor, { target: { value: "held B" } });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await open("c.md");
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
    expect((editor as HTMLTextAreaElement).value).toBe("held B");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    await open("c.md");
    expect(confirm).not.toHaveBeenCalled();
    expect(api.note).toHaveBeenCalledTimes(2);
    await act(async () =>
      pending.resolve({ operation_id: W1, status: "saved", cleanup_pending: false }),
    );
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
    expect(api.note).toHaveBeenLastCalledWith(W1, "a.md", expect.anything());
    expect(writer.saveNote).toHaveBeenCalledTimes(1);
    expect(writer.createNote).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
  it("resolves a removed opener to the heading and clears origins on workspace change", async () => {
    const api = read();
    const view = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await open("a.md");
    fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
      target: { value: "other" },
    });
    vi.mocked(api.entries).mockResolvedValue(scan([{ ref: "b.md", title: "b.md" }]));
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    await debounce();
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
    await open("b.md");
    view.rerender(<VaultBrowser workspaceId={W2} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await open("b.md");
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    expect(vi.mocked(api.note).mock.calls.map(([id, ref]) => [id, ref])).toEqual([
      [W1, "a.md"],
      [W1, "b.md"],
      [W2, "b.md"],
    ]);
  });
  it("keeps only the latest 40 accepted origins", async () => {
    const refs = Array.from({ length: 42 }, (_, index) => `n${index}.md`);
    const api = vaultApi({
      entries: vi.fn(async () => scan(refs.map((ref) => ({ ref, title: ref })))),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    const ordered = [...refs].sort();
    let page = 0;
    for (const ref of refs) {
      const targetPage = Math.floor(ordered.indexOf(ref) / 32);
      if (targetPage !== page) {
        fireEvent.click(
          screen.getByRole("button", {
            name: targetPage > page ? "Next note page" : "Previous note page",
          }),
        );
        page = targetPage;
      }
      await open(ref);
    }
    for (let index = 0; index < 40; index++)
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
    expect(api.note).toHaveBeenLastCalledWith(W1, "n1.md", expect.anything());
    expect(api.note).toHaveBeenCalledTimes(82);
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    expect(api.note).toHaveBeenCalledTimes(82);
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
  }, 30_000);
});

it("a different accepted ref discards delayed Back focus and scroll ownership", async () => {
  const api = vaultApi({
    entries: vi.fn(async () => scan(["a.md", "b.md", "c.md"].map((ref) => ({ ref, title: ref })))),
  });
  const row = (ref: string) =>
    within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", { name: ref });
  const open = async (ref: string) => act(async () => fireEvent.click(row(ref)));
  const held = deferred<Record<string, unknown>>();
  render(
    <div data-region="view">
      <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />
    </div>,
  );
  await debounce();
  await open("a.md");
  const region = row("a.md").closest<HTMLElement>('[data-region="view"]');
  if (!region) throw new Error("Missing current outer region");
  region.scrollTop = 213;
  await open("b.md");
  vi.mocked(api.note).mockImplementationOnce(() => held.promise);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
  const current = row("c.md");
  current.focus();
  region.scrollTop = 47;
  await open("c.md");
  expect(screen.getByRole("heading", { name: "c.md" })).toBeTruthy();
  expect(document.activeElement).toBe(current);
  expect(region.scrollTop).toBe(47);
  await act(async () => held.resolve(note("a.md", "obsolete A")));
  expect(screen.getByRole("heading", { name: "c.md" })).toBeTruthy();
  expect(screen.queryByText("obsolete A")).toBeNull();
  expect(document.activeElement).toBe(current);
  expect(region.scrollTop).toBe(47);
  expect(api.note).toHaveBeenCalledTimes(4);
});

describe("VaultBrowser optional App navigation owner", () => {
  it("publishes synchronous literal snapshots and records only admitted transitions", async () => {
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "a.md", title: "A" },
          { ref: "b.md", title: "B" },
        ]),
      ),
    });
    let mounted: VaultNavigationHandle | null = null;
    const released = vi.fn();
    const ready = vi.fn((handle: VaultNavigationHandle) => {
      mounted = handle;
      return () => {
        mounted = null;
        released();
      };
    });
    const readHandle = (): VaultNavigationHandle => {
      if (!mounted) throw new Error("missing mounted Vault handle");
      return mounted;
    };
    const navigation = vi.fn(() => false);
    const onBack = vi.fn();
    const view = render(
      <div data-region="view">
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          onNavigationReady={ready}
          onNavigation={navigation}
          onBack={onBack}
          renderBack={(back) => (
            <button type="button" onClick={back}>
              Back
            </button>
          )}
        />
      </div>,
    );
    await debounce();
    await act(async () => {
      fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
        target: { value: "literal query" },
      });
      const snapshot: VaultNavigationSnapshot = readHandle().snapshot();
      expect(snapshot).toMatchObject({
        workspaceId: W1,
        ref: null,
        query: "literal query",
      });
    });
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "A" })));
    expect(api.note).not.toHaveBeenCalled();
    expect(navigation).toHaveBeenLastCalledWith(
      expect.objectContaining({
        workspaceId: W1,
        ref: null,
        query: "literal query",
        opener: { kind: "row", ref: "a.md" },
      }),
      expect.objectContaining({ workspaceId: W1, ref: "a.md" }),
      readHandle(),
    );
    navigation.mockReturnValue(true);
    await act(async () => {
      expect(readHandle().open("a.md")).toBe(true);
      expect(readHandle().snapshot().ref).toBe("a.md");
      expect(readHandle().open("b.md")).toBe(true);
      expect(readHandle().snapshot().ref).toBe("b.md");
    });
    expect(api.note).toHaveBeenCalledTimes(2);
    expect(navigation).toHaveBeenLastCalledWith(
      expect.objectContaining({ ref: "a.md" }),
      expect.objectContaining({ ref: "b.md" }),
      readHandle(),
    );
    expect(screen.getAllByRole("button", { name: "Back" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Back to notes" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(api.note).toHaveBeenCalledTimes(2);
    const modal = document.createElement("div");
    modal.setAttribute("aria-modal", "true");
    document.body.append(modal);
    expect(readHandle().canLeave()).toBe(false);
    modal.remove();
    expect(readHandle().canLeave()).toBe(true);
    expect(readHandle().open("../a.md")).toBe(false);
    expect(api.note).toHaveBeenCalledTimes(2);
    const obsolete = readHandle();
    view.unmount();
    expect(obsolete.canLeave()).toBe(false);
    expect(obsolete.open("a.md")).toBe(false);
    expect(api.note).toHaveBeenCalledTimes(2);
    expect(released).toHaveBeenCalledTimes(1);
    expect(mounted).toBeNull();
  });
});

describe("VaultBrowser initial Back restoration", () => {
  it.each([W1, W2])(
    "restores only its qualified %s snapshot once without recording another step",
    async (workspaceId) => {
      const api = vaultApi({
        entries: vi.fn(async () => scan([{ ref: "alpha/a.md", title: "A" }])),
      });
      const navigation = vi.fn(() => true);
      const snapshot: VaultNavigationSnapshot = {
        workspaceId: W1,
        ref: "alpha/a.md",
        query: "query",
        folder: "alpha",
        sort: "recent",
        onlyMarked: false,
        opener: null,
        scroll: { top: 159, left: 4 },
      };
      const view = render(
        <div data-region="view">
          <VaultBrowser
            workspaceId={workspaceId}
            api={api}
            onOpenUrl={vi.fn()}
            initialNavigation={snapshot}
            initialRef="other.md"
            onNavigation={navigation}
          />
        </div>,
      );
      await debounce();
      const region = view.container.firstElementChild as HTMLElement;
      if (workspaceId === W1) {
        expect(api.note).toHaveBeenCalledWith(W1, "alpha/a.md", expect.anything());
        expect(api.note).toHaveBeenCalledTimes(1);
        expect(
          (screen.getByRole("textbox", { name: "Search note contents" }) as HTMLInputElement).value,
        ).toBe("query");
        expect([region.scrollTop, region.scrollLeft]).toEqual([159, 4]);
        expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
      } else {
        expect(api.note).not.toHaveBeenCalled();
        expect(
          (screen.getByRole("textbox", { name: "Search note contents" }) as HTMLInputElement).value,
        ).toBe("");
        expect([region.scrollTop, region.scrollLeft]).toEqual([0, 0]);
      }
      view.rerender(
        <div data-region="view">
          <VaultBrowser
            workspaceId={workspaceId}
            api={api}
            onOpenUrl={vi.fn()}
            initialNavigation={{ ...snapshot, ref: "other.md" }}
            onNavigation={navigation}
          />
        </div>,
      );
      await debounce();
      expect(api.note).toHaveBeenCalledTimes(workspaceId === W1 ? 1 : 0);
      expect(navigation).not.toHaveBeenCalled();
    },
  );
});

it.each(["failed", "pending", "search"])(
  "restored bookmarked browsing waits only for its required %s read",
  async (kind) => {
    const held = deferred<Awaited<ReturnType<BrowserApi["bookmarks"]>>>();
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "alpha/a.md", title: "A" }])),
      bookmarks: vi.fn(() => held.promise),
    });
    const navigation = vi.fn(() => true);
    const { container } = render(
      <div data-region="view">
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          onNavigation={navigation}
          initialNavigation={{
            workspaceId: W1,
            ref: null,
            query: kind === "search" ? "needle" : "",
            folder: "alpha",
            sort: "recent",
            onlyMarked: true,
            opener: { kind: "row", ref: "alpha/a.md" },
            scroll: { top: 203, left: 11 },
          }}
        />
      </div>,
    );
    const region = container.firstElementChild as HTMLElement;
    const input = screen.getByRole("textbox", { name: "Search note contents" });
    input.focus();
    await debounce();
    if (kind === "search") {
      const opener = within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", {
        name: "A",
      });
      expect(document.activeElement).toBe(opener);
      expect([region.scrollTop, region.scrollLeft]).toEqual([203, 11]);
      input.focus();
      region.scrollTop = 77;
      await act(async () => held.resolve({ bookmarks: [] }));
      expect(document.activeElement).toBe(input);
      expect(region.scrollTop).toBe(77);
      expect(api.entries).toHaveBeenCalledWith(W1, "needle", expect.anything());
    } else {
      expect(document.activeElement).toBe(input);
      expect([region.scrollTop, region.scrollLeft]).toEqual([0, 0]);
      await act(async () => {
        if (kind === "failed") held.reject(new Error("synthetic bookmark read failure"));
        else held.resolve({ bookmarks: [{ ref: "alpha/a.md", created_at: CREATED_AT }] });
      });
      const expected =
        kind === "failed"
          ? screen.getByRole("heading", { level: 1 })
          : within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", {
              name: "A",
            });
      expect(document.activeElement).toBe(expected);
      expect([region.scrollTop, region.scrollLeft]).toEqual([203, 11]);
      expect(screen.queryByRole("alert") !== null).toBe(kind === "failed");
    }
    expect(api.bookmarks).toHaveBeenCalledTimes(1);
    expect(api.note).not.toHaveBeenCalled();
    expect(api.addBookmark).not.toHaveBeenCalled();
    expect(api.removeBookmark).not.toHaveBeenCalled();
    expect(navigation).not.toHaveBeenCalled();
  },
);

it.each(["preview", "apply"])(
  "Vault retains its draft and note stack while canceled %s remains pending",
  async (kind) => {
    const heldPreview = deferred<Awaited<ReturnType<VaultOpsApi["preview"]>>>();
    const heldApply = deferred<Awaited<ReturnType<VaultOpsApi["apply"]>>>();
    const plan: Awaited<ReturnType<VaultOpsApi["preview"]>> = {
      steps: [],
      creates_folders: [],
      recovery_days: 30,
    };
    const ops: VaultOpsApi = {
      preview: vi.fn(kind === "preview" ? () => heldPreview.promise : async () => plan),
      apply: vi.fn(() => heldApply.promise),
      recovery: vi.fn(async () => ({ entries: [], stuck: [] })),
      forgetOperation: vi.fn(),
      resolve: vi.fn(),
    };
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan(["a.md", "b.md", "c.md"].map((ref) => ({ ref, title: ref }))),
      ),
    });
    const writer = writeApi();
    let handle: VaultNavigationHandle | undefined;
    const currentHandle = (): VaultNavigationHandle => {
      if (!handle) throw new Error("Missing current Vault handle");
      return handle;
    };
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        write={{ ...write(writer), ops }}
        onOpenUrl={vi.fn()}
        onNavigationReady={(value) => {
          handle = value;
          return () => {
            handle = undefined;
          };
        }}
      />,
    );
    await debounce();
    const row = (ref: string) =>
      within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", { name: ref });
    await act(async () => fireEvent.click(row("a.md")));
    await act(async () => fireEvent.click(row("b.md")));
    fireEvent.click(screen.getByRole("button", { name: "Note actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Review" })));
    if (kind === "apply")
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply" })));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const editor = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
    fireEvent.change(editor, { target: { value: "held B draft" } });
    expect(currentHandle().canLeave()).toBe(false);
    editor.focus();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(row("c.md"));
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
    expect(editor.value).toBe("held B draft");
    expect(document.activeElement).toBe(editor);
    expect(api.note).toHaveBeenCalledTimes(2);
    expect(confirm).not.toHaveBeenCalled();
    await act(async () => {
      if (kind === "preview") heldPreview.resolve(plan);
      else
        heldApply.resolve({
          operation_id: W1,
          status: "done",
          replayed: false,
          steps_applied: 1,
          reason: null,
          created_folders: [],
        });
    });
    expect(currentHandle().canLeave()).toBe(true);
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
    expect(api.note).toHaveBeenLastCalledWith(W1, "a.md", expect.anything());
    expect(api.note).toHaveBeenCalledTimes(3);
    expect(ops.preview).toHaveBeenCalledTimes(1);
    expect(ops.apply).toHaveBeenCalledTimes(kind === "preview" ? 0 : 1);
    expect(writer.saveNote).not.toHaveBeenCalled();
    expect(writer.createNote).not.toHaveBeenCalled();
    confirm.mockRestore();
  },
);

it("Vault keeps a later canceled preview pending when an earlier preview settles", async () => {
  const first = deferred<Awaited<ReturnType<VaultOpsApi["preview"]>>>();
  const second = deferred<Awaited<ReturnType<VaultOpsApi["preview"]>>>();
  const plan = { steps: [], creates_folders: [], recovery_days: 30 };
  const ops: VaultOpsApi = {
    preview: vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise),
    apply: vi.fn(),
    recovery: vi.fn(async () => ({ entries: [], stuck: [] })),
    forgetOperation: vi.fn(),
    resolve: vi.fn(),
  };
  const api = vaultApi({
    entries: vi.fn(async () => scan(["a.md", "b.md"].map((ref) => ({ ref, title: ref })))),
  });
  let handle: VaultNavigationHandle | undefined;
  const currentHandle = (): VaultNavigationHandle => {
    if (!handle) throw new Error("Missing actual Vault owner");
    return handle;
  };
  render(
    <VaultBrowser
      workspaceId={W1}
      api={api}
      write={{ ...write(writeApi()), ops }}
      onOpenUrl={vi.fn()}
      onNavigationReady={(value) => {
        handle = value;
        return () => {
          handle = undefined;
        };
      }}
    />,
  );
  await debounce();
  for (const ref of ["a.md", "b.md"])
    await act(async () => fireEvent.click(screen.getByRole("button", { name: ref })));
  for (let index = 0; index < 2; index++) {
    fireEvent.click(screen.getByRole("button", { name: "Note actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Review" })));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  }
  expect(currentHandle().canLeave()).toBe(false);
  await act(async () => first.resolve(plan));
  expect(currentHandle().canLeave()).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(api.note).toHaveBeenCalledTimes(2);
  await act(async () => second.resolve(plan));
  expect(currentHandle().canLeave()).toBe(true);
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
  expect(api.note).toHaveBeenLastCalledWith(W1, "a.md", expect.anything());
  expect(ops.preview).toHaveBeenCalledTimes(2);
  expect(ops.apply).not.toHaveBeenCalled();
});

it.each(["proposal", "recovery"])(
  "Vault keeps its note history while canceled %s remains pending",
  async (kind) => {
    const heldProposal = deferred<CreatedProposal>();
    const heldRecovery = deferred<Awaited<ReturnType<VaultWriteApi["recovery"]>>>();
    const createProposal = vi.fn(() => heldProposal.promise);
    const recovery = vi.fn<VaultWriteApi["recovery"]>(() => heldRecovery.promise);
    const writer = writeApi({
      recovery,
      saveNote: vi.fn(async () => {
        throw new VaultWriteError("publication_busy");
      }),
    });
    const api = vaultApi({
      entries: vi.fn(async () =>
        scan(["a.md", "b.md", "c.md"].map((ref) => ({ ref, title: ref }))),
      ),
    });
    let handle: VaultNavigationHandle | undefined;
    const currentHandle = (): VaultNavigationHandle => {
      if (!handle) throw new Error("Missing current Vault owner");
      return handle;
    };
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        {...(kind === "recovery" ? { write: write(writer) } : {})}
        {...(kind === "proposal" ? { proposalApi: { createProposal } } : {})}
        onOpenUrl={vi.fn()}
        onNavigationReady={(value) => {
          handle = value;
          return () => {
            handle = undefined;
          };
        }}
      />,
    );
    await debounce();
    const row = (ref: string) =>
      within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", { name: ref });
    await act(async () => fireEvent.click(row("a.md")));
    await act(async () => fireEvent.click(row("b.md")));
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "held B draft" },
    });
    if (kind === "recovery")
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    await act(async () =>
      fireEvent.click(
        screen.getByRole("button", {
          name: kind === "proposal" ? "Propose" : "Check recovery status",
        }),
      ),
    );
    expect(currentHandle().canLeave()).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("textbox", { name: "Markdown" })).toBeNull();
    const focus = document.activeElement;
    const snapshot = currentHandle().snapshot();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    try {
      fireEvent.click(screen.getByRole("button", { name: "Back" }));
      fireEvent.click(row("c.md"));
      expect(currentHandle().canLeave()).toBe(false);
      expect(currentHandle().snapshot()).toEqual(snapshot);
      expect(document.activeElement).toBe(focus);
      expect(api.note).toHaveBeenCalledTimes(2);
      expect(confirm).not.toHaveBeenCalled();
      await act(async () => {
        if (kind === "proposal") heldProposal.resolve({ id: W2, item: null, replayed: true });
        else heldRecovery.resolve([]);
      });
      expect(currentHandle().canLeave()).toBe(true);
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
      expect(api.note).toHaveBeenLastCalledWith(W1, "a.md", expect.anything());
      expect(api.note).toHaveBeenCalledTimes(3);
      expect(confirm).not.toHaveBeenCalled();
      expect(createProposal).toHaveBeenCalledTimes(kind === "proposal" ? 1 : 0);
      if (kind === "proposal")
        expect(createProposal).toHaveBeenCalledWith(W1, "b.md", "held B draft", "a".repeat(64));
      expect(recovery).toHaveBeenCalledTimes(kind === "recovery" ? 1 : 0);
      expect(writer.saveNote).toHaveBeenCalledTimes(kind === "recovery" ? 1 : 0);
      expect(writer.createNote).not.toHaveBeenCalled();
    } finally {
      confirm.mockRestore();
    }
  },
);

it.each(["ready", "missing", "failed"])(
  "selected-note row restoration waits for its current %s listing",
  async (kind) => {
    const held = deferred<Record<string, unknown>>();
    const api = vaultApi({ entries: vi.fn(() => held.promise) });
    const navigation = vi.fn(() => true);
    const snapshot: VaultNavigationSnapshot = {
      workspaceId: W1,
      ref: "alpha/a.md",
      query: "",
      folder: "",
      sort: "name",
      onlyMarked: false,
      opener: { kind: "row", ref: "beta/b.md" },
      scroll: { top: 217, left: 13 },
    };
    const view = render(
      <div data-region="view">
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          initialNavigation={snapshot}
          onNavigation={navigation}
        />
      </div>,
    );
    const region = view.container.firstElementChild as HTMLElement;
    const input = screen.getByRole("textbox", { name: "Search note contents" });
    input.focus();
    await debounce();
    expect(screen.getByRole("heading", { name: "alpha/a.md" })).toBeTruthy();
    expect(document.activeElement).toBe(input);
    expect([region.scrollTop, region.scrollLeft]).toEqual([0, 0]);
    expect(api.entries).toHaveBeenCalledTimes(1);
    await act(async () => {
      if (kind === "failed") held.reject(new Error("synthetic listing read failure"));
      else
        held.resolve(scan(kind === "ready" ? [{ ref: "beta/b.md", title: "Literal opener" }] : []));
    });
    const expected =
      kind === "ready"
        ? within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", {
            name: "Literal opener",
          })
        : screen.getByRole("heading", { level: 1 });
    expect(document.activeElement).toBe(expected);
    expect([region.scrollTop, region.scrollLeft]).toEqual([217, 13]);
    input.focus();
    region.scrollTop = 77;
    view.rerender(
      <div data-region="view">
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          initialNavigation={{ ...snapshot, scroll: { top: 303, left: 9 } }}
          onNavigation={navigation}
        />
      </div>,
    );
    expect(document.activeElement).toBe(input);
    expect([region.scrollTop, region.scrollLeft]).toEqual([77, 13]);
    expect(api.entries).toHaveBeenCalledWith(W1, undefined, expect.anything());
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(api.note).toHaveBeenCalledWith(W1, "alpha/a.md", expect.anything());
    expect(api.note).toHaveBeenCalledTimes(1);
    expect(api.bookmarks).toHaveBeenCalledTimes(1);
    expect(api.addBookmark).not.toHaveBeenCalled();
    expect(api.removeBookmark).not.toHaveBeenCalled();
    expect(navigation).not.toHaveBeenCalled();
  },
);

function displayProjection(overrides: Partial<DisplayMetadata> = {}): DisplayMetadata {
  return {
    state: "ready",
    frontmatter_state: "ready",
    heading_state: "absent",
    display_title: "Authored",
    title_source: "title",
    tags: [],
    type: null,
    status: null,
    ...overrides,
  };
}

function noteProjection(ref: string, metadata: unknown, fields: Record<string, unknown> = {}) {
  const body = note(ref);
  return { ...body, note: { ...(body.note as object), ...fields, display_metadata: metadata } };
}

// An overview containing exactly one bare row for `ref`, so `sectionOf` (not the raw folder path)
// supplies NoteChips's section label: the component only takes that path once the ref is
// present in the overview's own notes list.
function overviewWithRef(ref: string): VaultOverview {
  return {
    notes: [
      {
        ref,
        state: "ready",
        title: null,
        type: null,
        status: null,
        confidence: null,
        tags: null,
        created: null,
        modified: null,
        summary: null,
        links: null,
        complete: true,
      },
    ],
    partial: false,
    partial_reasons: [],
    omitted: 0,
    read_bytes: 0,
  };
}

describe("VaultBrowser article reader", () => {
  it("keeps a dirty draft when a Related card jump is declined", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async (_id, ref) => ({
        ...note(ref, "original"),
        resolved_links:
          ref === "one.md" ? [{ target: "two", state: "resolved", candidates: ["two.md"] }] : [],
      })),
    });
    render(
      <VaultBrowser workspaceId={W1} api={api} write={write(writeApi())} onOpenUrl={vi.fn()} />,
    );
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const editor = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
    fireEvent.change(editor, { target: { value: "unsaved" } });
    const related = within(screen.getByRole("region", { name: "Related" }));
    const card = related.getByRole("button", { name: "two" });
    expect(card.closest(".note-card-compact")).toBeTruthy();
    fireEvent.click(card);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(editor.value).toBe("unsaved");
    expect(api.note).toHaveBeenCalledTimes(1);
    confirm.mockReturnValue(true);
    await act(async () => fireEvent.click(card));
    expect(api.note).toHaveBeenCalledWith(W1, "two.md", expect.anything());
    confirm.mockRestore();
  });

  it("orders backlinks by exact source ref when their answer arrives", async () => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      backlinks: vi.fn(async () =>
        complete({
          backlinks: [
            { source: "z.md", line: 1 },
            { source: "a.md", line: 2 },
          ],
        }),
      ),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    const refs = within(screen.getByRole("region", { name: "Backlinks" })).getAllByText(/\.md$/);
    expect(refs.map((item) => item.textContent)).toEqual(["a.md", "z.md"]);
  });

  it("keeps exact ref as a direct header child and uses the existing bookmark service", async () => {
    const ref = "30-knowledge/one.md";
    let marked = false;
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref, title: "One" }])),
      note: vi.fn(async () => noteProjection(ref, displayProjection({ tags: ["topic"] }))),
      overview: vi.fn(async () => overviewWithRef(ref)),
      bookmarks: vi.fn(async () => ({
        bookmarks: marked ? [{ ref, created_at: CREATED_AT }] : [],
      })),
      addBookmark: vi.fn(async () => {
        marked = true;
        return { bookmark: { ref, created_at: CREATED_AT } };
      }),
    });
    const { container } = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "All notes" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    const head = container.querySelector(".vault-reader-head");
    expect(head?.querySelector(":scope > .vault-ref")?.textContent).toBe(ref);
    expect(head?.querySelector(".chip")).toBeNull();
    const bookmark = within(head as HTMLElement).getByRole("button", {
      name: `Bookmark ${ref}`,
    });
    expect(bookmark.getAttribute("aria-pressed")).toBe("false");
    expect(bookmark.textContent).toContain("Bookmark");
    expect(head?.textContent).toContain("If the note moves, this bookmark will show as missing.");
    await act(async () => fireEvent.click(bookmark));
    expect(api.addBookmark).toHaveBeenCalledWith(W1, ref);
    const removeBookmark = within(head as HTMLElement).getByRole("button", {
      name: `Remove bookmark ${ref}`,
    });
    expect(removeBookmark.getAttribute("aria-pressed")).toBe("true");
    expect(removeBookmark.textContent).toContain("Remove bookmark");
    fireEvent.click(within(head as HTMLElement).getByRole("button", { name: "#topic" }));
    expect(screen.getByText("Tagged #topic")).toBeTruthy();
  });

  it.each(["else/one.md", "30-knowledge/one.md"])(
    "shows tags as text when %s has no exact overview row",
    async (ref) => {
      const api = vaultApi({
        entries: vi.fn(async () => scan([{ ref, title: "One" }])),
        note: vi.fn(async () => noteProjection(ref, displayProjection({ tags: ["topic"] }))),
        overview: vi.fn(async () => overviewWithRef("30-knowledge/other.md")),
      });
      const { container } = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      fireEvent.click(screen.getByRole("button", { name: "All notes" }));
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
      const head = container.querySelector(".vault-reader-head");
      expect(within(head as HTMLElement).queryByRole("button", { name: "#topic" })).toBeNull();
      expect(head?.textContent).toContain("#topic");
    },
  );
});

it("keeps long metadata values in the current wrapping inspector", () => {
  const css = readCss(import.meta.url, "VaultBrowser.css");
  expect(css).toMatch(
    /\.vault-reader-head > h2\s*\{[^}]*min-width:\s*0;[^}]*overflow-wrap:\s*anywhere;/,
  );
  expect(css).not.toMatch(
    /\.vault-reader-head > h2\s*\{[^}]*(?:overflow:\s*(?:auto|scroll)|position:\s*(?:fixed|absolute))/,
  );
});

it("keeps article text at 68ch and 16px with wrapping at narrow widths", () => {
  const css = readCss(import.meta.url, "VaultBrowser.css");
  const body = ruleIn(css, ".vault-note-content > .md");
  expect(body).toMatch(/max-width:\s*68ch;/);
  expect(body).toMatch(/font-size:\s*16px;/);
  expect(body).toMatch(/overflow-wrap:\s*anywhere;/);
  expect(css).toMatch(/\.vault-inspector\s*\{[^}]*min-width:\s*0;/);
  expect(ruleIn(css, ".vault-reader-tag")).toMatch(/max-width:\s*100%;/);
});

it.each([
  ["explicit null", null],
  ["explicit undefined", undefined],
  ["unknown field", { ...displayProjection(), execute: true }],
  ["inconsistent coverage", { ...displayProjection(), state: "absent" }],
])(
  "rejects a present %s projection at the component's raw boundary",
  async (_label, projection) => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () => noteProjection("one.md", projection)),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    expect(screen.getByRole("alert").textContent).toBe("The vault returned an invalid response.");
    expect(screen.queryByRole("heading", { name: "Authored" })).toBeNull();
    expect(api.note).toHaveBeenCalledTimes(1);
  },
);

describe("VaultBrowser projection retains source and read-only authority", () => {
  it.each(["save", "proposal"])(
    "passes the exact source and full SHA to the existing %s route",
    async (kind) => {
      const source =
        "\uFEFF---\r\ntitle: Source title\r\nunknown: preserved\r\n---\r\nOriginal body\r\n";
      const sha = "c".repeat(64);
      const projection = displayProjection({
        display_title: "Display only",
        tags: ["z", "a"],
        type: "skill",
        status: "approved",
      });
      const api = vaultApi({
        entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
        note: vi.fn(async () =>
          noteProjection("one.md", projection, { text: "Original body", source, sha256: sha }),
        ),
      });
      const saveNote = vi.fn<VaultWriteApi["saveNote"]>(async () => ({
        operation_id: W1,
        status: "saved",
        cleanup_pending: false,
      }));
      const writer = writeApi({ saveNote });
      const createProposal = vi.fn(
        async (): Promise<CreatedProposal> => ({ id: W2, item: null, replayed: true }),
      );
      render(
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          {...(kind === "save" ? { write: write(writer) } : { proposalApi: { createProposal } })}
        />,
      );
      await debounce();
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
      expect(screen.getByRole("heading", { name: "Display only" })).toBeTruthy();
      expect(screen.getByText("Original body")).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Note actions" })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
      const editor = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
      expect(editor.value).toBe(
        "\uFEFF---\ntitle: Source title\nunknown: preserved\n---\nOriginal body\n",
      );
      expect(api.note).toHaveBeenCalledTimes(1);
      await act(async () =>
        fireEvent.click(
          screen.getByRole("button", { name: kind === "save" ? "Save note" : "Propose" }),
        ),
      );
      if (kind === "save") {
        expect(saveNote).toHaveBeenCalledExactlyOnceWith(W1, 4, "12:34", "one.md", source, sha);
        expect(createProposal).not.toHaveBeenCalled();
        expect(api.note).toHaveBeenCalledTimes(2);
      } else {
        expect(createProposal).toHaveBeenCalledExactlyOnceWith(W1, "one.md", source, sha);
        expect(saveNote).not.toHaveBeenCalled();
        expect(api.note).toHaveBeenCalledTimes(1);
      }
      expect(writer.createNote).not.toHaveBeenCalled();
      expect(api.addBookmark).not.toHaveBeenCalled();
      expect(api.removeBookmark).not.toHaveBeenCalled();
    },
  );

  it("authored skill and approved labels grant no memory, note action or save controls", async () => {
    const ref = "skills/SKILL.md";
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref, title: "SKILL" }])),
      note: vi.fn(async () =>
        noteProjection(
          ref,
          displayProjection({ type: "skill", status: "approved", tags: ["memory"] }),
        ),
      ),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "skills / SKILL.md" })),
    );
    const article = screen.getByRole("article", { name: "Selected note" });
    expect(within(article).getByRole("list", { name: "About this note" }).textContent).toBe(
      "skillsskillapproved#memory",
    );
    for (const label of ["Edit note", "Save note", "Note actions", "Approve", "Propose"])
      expect(screen.queryByRole("button", { name: label })).toBeNull();
    expect(screen.queryByRole("region", { name: "Memory" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Recovery" })).toBeNull();
    expect(api.note).toHaveBeenCalledTimes(1);
    expect(api.addBookmark).not.toHaveBeenCalled();
    expect(api.removeBookmark).not.toHaveBeenCalled();
  });
});

it("rereads full metadata on local Back while preserving the current filtered opener and outer offset", async () => {
  let readsA = 0;
  const api = vaultApi({
    entries: vi.fn(async () =>
      scan([
        { ref: "alpha/a.md", title: "A" },
        { ref: "alpha/b.md", title: "B" },
      ]),
    ),
    bookmarks: vi.fn(async () => ({
      bookmarks: [
        { ref: "alpha/a.md", created_at: CREATED_AT },
        { ref: "alpha/b.md", created_at: CREATED_AT },
      ],
    })),
    note: vi.fn(async (_id, ref) => ({
      ...noteProjection(
        ref,
        displayProjection({
          display_title: ref === "alpha/a.md" ? `Full A ${++readsA}` : "Full B",
          tags: ref === "alpha/a.md" ? [`A read ${readsA}`] : ["B tag"],
        }),
      ),
      resolved_links:
        ref === "alpha/a.md"
          ? [{ target: "B", state: "resolved", candidates: ["alpha/b.md"] }]
          : [],
    })),
  });
  const { container } = render(
    <div data-region="view">
      <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />
    </div>,
  );
  await debounce();
  fireEvent.change(screen.getByRole("combobox", { name: /Folder/ }), {
    target: { value: "alpha" },
  });
  fireEvent.change(screen.getByRole("combobox", { name: /Sort/ }), {
    target: { value: "recent" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /Bookmarks only/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
    target: { value: "match" },
  });
  await debounce();
  const region = container.firstElementChild as HTMLElement;
  region.scrollTop = 217;
  region.scrollLeft = 13;
  const opener = within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", {
    name: "A",
  });
  await act(async () => fireEvent.click(opener));
  expect(screen.getByRole("heading", { name: "Full A 1" })).toBeTruthy();
  region.scrollTop = 93;
  await act(async () =>
    fireEvent.click(
      // Related uses the already-read listing label for this exact ref.
      within(screen.getByRole("region", { name: "Links" })).getByRole("button", {
        name: "B",
      }),
    ),
  );
  expect(screen.getByText("#B tag")).toBeTruthy();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
  expect(screen.getByRole("heading", { name: "Full A 2" })).toBeTruthy();
  expect(screen.getByText("#A read 2")).toBeTruthy();
  expect(screen.queryByText("#A read 1")).toBeNull();
  expect(screen.queryByText("#B tag")).toBeNull();
  expect(api.note).toHaveBeenNthCalledWith(3, W1, "alpha/a.md", expect.anything());
  expect(api.note).toHaveBeenCalledTimes(3);
  expect(api.backlinks).toHaveBeenCalledTimes(3);
  expect(api.history).toHaveBeenCalledTimes(3);
  expect(document.activeElement).toBe(
    within(screen.getByRole("region", { name: "Links" })).getByRole("button", { name: "B" }),
  );
  expect(region.scrollTop).toBe(93);
  fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
  expect(screen.queryByRole("region", { name: "Note metadata" })).toBeNull();
  expect(document.activeElement).toBe(opener);
  expect([region.scrollTop, region.scrollLeft]).toEqual([217, 13]);
  expect(
    screen.getByRole<HTMLInputElement>("textbox", { name: "Search note contents" }).value,
  ).toBe("match");
  fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
    target: { value: "" },
  });
  await debounce();
  expect(screen.getByRole<HTMLSelectElement>("combobox", { name: /Folder/ }).value).toBe("alpha");
  expect(screen.getByRole<HTMLSelectElement>("combobox", { name: /Sort/ }).value).toBe("recent");
  expect(screen.getByRole<HTMLInputElement>("checkbox", { name: /Bookmarks only/ }).checked).toBe(
    true,
  );
  expect(api.note).toHaveBeenCalledTimes(3);
  expect(api.addBookmark).not.toHaveBeenCalled();
  expect(api.removeBookmark).not.toHaveBeenCalled();
});

it.each(["parent", "sibling", "descendant", "detached"])(
  "an explicit modal note request admits only its connected %s dialog chain",
  async (kind) => {
    const api = vaultApi({ entries: vi.fn(async () => scan([{ ref: "a.md", title: "A" }])) });
    let handle: VaultNavigationHandle | undefined;
    const current = () => {
      if (!handle) throw new Error("Missing current Vault owner");
      return handle;
    };
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        write={write(writeApi())}
        onOpenUrl={vi.fn()}
        onNavigationReady={(value) => {
          handle = value;
          return () => {
            handle = undefined;
          };
        }}
      />,
    );
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "A" })));
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const editor = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
    fireEvent.change(editor, { target: { value: "keep this literal draft" } });
    const outer = document.createElement("div");
    const origin = document.createElement("div");
    const extra = document.createElement("div");
    for (const modal of [outer, origin, extra]) modal.setAttribute("aria-modal", "true");
    outer.append(origin);
    document.body.append(outer);
    if (kind === "sibling") outer.append(extra);
    if (kind === "descendant") origin.append(extra);
    if (kind === "detached") origin.remove();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const before = current().snapshot();
    try {
      expect(current().canLeave()).toBe(false);
      expect(current().open("b.md")).toBe(false);
      if (kind !== "parent") expect(current().open("b.md", origin)).toBe(false);
      expect(confirm).not.toHaveBeenCalled();
      expect(current().snapshot()).toEqual(before);
      expect(editor.value).toBe("keep this literal draft");
      expect(api.note).toHaveBeenCalledTimes(1);
      extra.remove();
      outer.append(origin);
      expect(current().open("../invalid.md", origin)).toBe(false);
      expect(confirm).not.toHaveBeenCalled();
      expect(current().open("b.md", origin)).toBe(false);
      expect(confirm).toHaveBeenCalledTimes(1);
      expect(current().snapshot()).toEqual(before);
      expect(editor.value).toBe("keep this literal draft");
      confirm.mockReturnValue(true);
      await act(async () => expect(current().open("b.md", origin)).toBe(true));
      expect(confirm).toHaveBeenCalledTimes(2);
      expect(api.note).toHaveBeenLastCalledWith(W1, "b.md", expect.anything());
      expect(current().snapshot().ref).toBe("b.md");
      outer.remove();
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
      expect(api.note).toHaveBeenLastCalledWith(W1, "a.md", expect.anything());
      expect(confirm).toHaveBeenCalledTimes(2);
    } finally {
      outer.remove();
      extra.remove();
    }
  },
);
it("an explicitly owned modal cannot bypass a held Save or replay its refused note request", async () => {
  const held = deferred<Awaited<ReturnType<VaultWriteApi["saveNote"]>>>();
  let saved = false;
  const api = vaultApi({
    entries: vi.fn(async () => scan([{ ref: "a.md", title: "A" }])),
    note: vi.fn(async (_id, ref) =>
      saved ? noteWithHash(ref, "saved literal", "b".repeat(64)) : note(ref),
    ),
  });
  const writer = writeApi({ saveNote: vi.fn(() => held.promise) });
  let handle: VaultNavigationHandle | undefined;
  const current = () => {
    if (!handle) throw new Error("Missing current Vault owner");
    return handle;
  };
  render(
    <VaultBrowser
      workspaceId={W1}
      api={api}
      write={write(writer)}
      onOpenUrl={vi.fn()}
      onNavigationReady={(value) => {
        handle = value;
        return () => {
          handle = undefined;
        };
      }}
    />,
  );
  await debounce();
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "A" })));
  fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
    target: { value: "saved literal" },
  });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
  const origin = document.createElement("div");
  origin.setAttribute("aria-modal", "true");
  document.body.append(origin);
  const before = current().snapshot();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  try {
    expect(current().open("b.md", origin)).toBe(false);
    expect(current().snapshot()).toEqual(before);
    expect(screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" }).value).toBe(
      "saved literal",
    );
    expect(api.note).toHaveBeenCalledTimes(1);
    expect(writer.saveNote).toHaveBeenCalledTimes(1);
    expect(confirm).not.toHaveBeenCalled();
    await act(async () => {
      saved = true;
      held.resolve({ operation_id: W1, status: "saved", cleanup_pending: false });
    });
    expect(current().snapshot().ref).toBe("a.md");
    expect(api.note).toHaveBeenCalledTimes(2);
    await act(async () => expect(current().open("b.md", origin)).toBe(true));
    expect(api.note).toHaveBeenCalledTimes(3);
    expect(api.note).toHaveBeenLastCalledWith(W1, "b.md", expect.anything());
    expect(writer.saveNote).toHaveBeenCalledTimes(1);
    expect(writer.createNote).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  } finally {
    origin.remove();
  }
});

function paginationRows(length = 70) {
  return Array.from({ length }, (_, index) => ({
    ref: `alpha/n${String(index).padStart(3, "0")}.md`,
    title: `Row ${String(index).padStart(3, "0")}`,
    modified: index,
  }));
}
function pagedRefs(): string[] {
  return Array.from(
    screen
      .getByRole("navigation", { name: "Notes" })
      .querySelectorAll<HTMLButtonElement>('[data-vault-kind="row"]'),
    (element) => element.dataset.vaultRef ?? "",
  );
}

describe("VaultBrowser returned-list pagination", () => {
  it("renders at most 32 ordered refs with local bounds and zero reader calls on page changes", async () => {
    const rows = paginationRows();
    const api = vaultApi({ entries: vi.fn(async () => scan(rows)), metadata: vi.fn() });
    const navigation = vi.fn(() => true);
    render(
      <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} onNavigation={navigation} />,
    );
    await debounce();
    expect(pagedRefs()).toEqual(rows.slice(0, 32).map((row) => row.ref));
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
    const previous = screen.getByRole<HTMLButtonElement>("button", { name: "Previous note page" });
    const next = screen.getByRole<HTMLButtonElement>("button", { name: "Next note page" });
    expect(previous.disabled).toBe(true);
    expect(next.disabled).toBe(false);
    fireEvent.click(previous);
    expect(pagedRefs()).toEqual(rows.slice(0, 32).map((row) => row.ref));
    fireEvent.click(next);
    expect(pagedRefs()).toEqual(rows.slice(32, 64).map((row) => row.ref));
    expect(screen.getByText("Page 2 of 3")).toBeTruthy();
    for (const element of screen
      .getByRole("navigation", { name: "Notes" })
      .querySelectorAll('[data-vault-kind="row"]')) {
      const id = element.getAttribute("aria-describedby");
      expect(id).toBeTruthy();
      expect(document.getElementById(id ?? "")?.textContent).toBe(
        (element as HTMLButtonElement).dataset.vaultRef,
      );
    }
    fireEvent.click(next);
    expect(pagedRefs()).toEqual(rows.slice(64).map((row) => row.ref));
    expect(screen.getByText("Page 3 of 3")).toBeTruthy();
    expect(next.disabled).toBe(true);
    fireEvent.click(next);
    expect(pagedRefs()).toEqual(rows.slice(64).map((row) => row.ref));
    fireEvent.click(previous);
    expect(pagedRefs()).toEqual(rows.slice(32, 64).map((row) => row.ref));
    fireEvent.click(previous);
    expect(pagedRefs()).toEqual(rows.slice(0, 32).map((row) => row.ref));
    expect(api.entries).toHaveBeenCalledExactlyOnceWith(W1, undefined, expect.anything());
    expect(api.note).not.toHaveBeenCalled();
    expect(api.backlinks).not.toHaveBeenCalled();
    expect(api.history).not.toHaveBeenCalled();
    expect(api.metadata).not.toHaveBeenCalled();
    expect(api.addBookmark).not.toHaveBeenCalled();
    expect(api.removeBookmark).not.toHaveBeenCalled();
    expect(navigation).not.toHaveBeenCalled();
  });
});

describe("VaultBrowser pagination scope changes", () => {
  it.each(["search", "folder", "sort", "bookmarks"])(
    "resets page after deliberate %s change and keeps the current returned order",
    async (kind) => {
      const rows = paginationRows(80).map((row, index) => ({
        ...row,
        ref: index < 40 ? row.ref : row.ref.replace("alpha/", "beta/"),
      }));
      const hits = [...rows].reverse();
      const api = vaultApi({
        entries: vi.fn(async (_id, query) => scan(query ? hits : rows)),
        bookmarks: vi.fn(async () => ({
          bookmarks: rows.map((row) => ({ ref: row.ref, created_at: CREATED_AT })),
        })),
        metadata: vi.fn(),
      });
      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
      expect(screen.getByText("Page 2 of 3")).toBeTruthy();
      if (kind === "search") {
        fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
          target: { value: "needle" },
        });
        await debounce();
      } else if (kind === "folder") {
        fireEvent.change(screen.getByRole("combobox", { name: /Folder/ }), {
          target: { value: "beta" },
        });
      } else if (kind === "sort") {
        fireEvent.change(screen.getByRole("combobox", { name: /Sort/ }), {
          target: { value: "recent" },
        });
      } else fireEvent.click(screen.getByRole("checkbox", { name: /Bookmarks only/ }));
      const expected =
        kind === "folder" ? rows.slice(40) : kind === "search" || kind === "sort" ? hits : rows;
      expect(pagedRefs()).toEqual(expected.slice(0, 32).map((row) => row.ref));
      expect(screen.getByText(`Page 1 of ${Math.ceil(expected.length / 32)}`)).toBeTruthy();
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "Previous note page" }).disabled,
      ).toBe(true);
      expect(api.entries).toHaveBeenCalledTimes(kind === "search" ? 2 : 1);
      if (kind === "search") {
        expect(api.entries).toHaveBeenLastCalledWith(W1, "needle", expect.anything());
        fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
        expect(pagedRefs()).toEqual(hits.slice(32, 64).map((row) => row.ref));
      }
      expect(api.note).not.toHaveBeenCalled();
      expect(api.backlinks).not.toHaveBeenCalled();
      expect(api.history).not.toHaveBeenCalled();
      expect(api.metadata).not.toHaveBeenCalled();
      expect(api.addBookmark).not.toHaveBeenCalled();
      expect(api.removeBookmark).not.toHaveBeenCalled();
    },
  );
  it("reports a filtered returned count separately from incomplete scan coverage", async () => {
    const rows = paginationRows();
    const api = vaultApi({
      entries: vi.fn(async () => ({
        entries: rows,
        partial: true,
        partial_reasons: ["deadline", "entry_limit"],
        omitted: 3,
      })),
      bookmarks: vi.fn(async () => ({
        bookmarks: rows.slice(0, 33).map((row) => ({ ref: row.ref, created_at: CREATED_AT })),
      })),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    const coverage = /Results may be incomplete \(deadline, entry_limit; at least 3 omitted\)/;
    expect(screen.getByText(coverage)).toBeTruthy();
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /Bookmarks only/ }));
    expect(screen.getByText("Page 1 of 2")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
    expect(pagedRefs()).toEqual([rows[32]?.ref]);
    expect(screen.getByText("Page 2 of 2")).toBeTruthy();
    expect(screen.getByText(coverage)).toBeTruthy();
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(api.note).not.toHaveBeenCalled();
  });
});

describe("VaultBrowser pagination result ownership", () => {
  it.each(["api", "workspace"])(
    "discards the prior page on %s replacement before a pending result",
    async (kind) => {
      const rows = paginationRows();
      const held = deferred<Record<string, unknown>>();
      const api = vaultApi({ entries: vi.fn(async () => scan(rows)) });
      const replacement = kind === "api" ? vaultApi({ entries: vi.fn(() => held.promise) }) : api;
      const view = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
      expect(screen.getByText("Page 2 of 3")).toBeTruthy();
      if (kind === "workspace") vi.mocked(api.entries).mockImplementationOnce(() => held.promise);
      view.rerender(
        <VaultBrowser
          workspaceId={kind === "workspace" ? W2 : W1}
          api={replacement}
          onOpenUrl={vi.fn()}
        />,
      );
      await debounce();
      expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
      await act(async () => held.resolve(scan(rows.slice(0, 40))));
      expect(pagedRefs()).toEqual(rows.slice(0, 32).map((row) => row.ref));
      expect(screen.getByText("Page 1 of 2")).toBeTruthy();
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "Previous note page" }).disabled,
      ).toBe(true);
      expect(replacement.entries).toHaveBeenLastCalledWith(
        kind === "workspace" ? W2 : W1,
        undefined,
        expect.anything(),
      );
      expect(api.note).not.toHaveBeenCalled();
      expect(replacement.note).not.toHaveBeenCalled();
    },
  );
  it("clamps page after removing the only bookmark on the last filtered page", async () => {
    const rows = paginationRows();
    const marked = rows.slice(0, 33).map((row) => row.ref);
    const api = vaultApi({
      entries: vi.fn(async () => scan(rows)),
      bookmarks: vi.fn(async () => ({
        bookmarks: marked.map((ref) => ({ ref, created_at: CREATED_AT })),
      })),
      removeBookmark: vi.fn(async (_id, ref) => {
        marked.splice(marked.indexOf(ref), 1);
      }),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("checkbox", { name: /Bookmarks only/ }));
    fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
    expect(pagedRefs()).toEqual(["alpha/n032.md"]);
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Remove bookmark for alpha/n032.md" })),
    );
    expect(pagedRefs()).toEqual(rows.slice(0, 32).map((row) => row.ref));
    expect(screen.queryByRole("group", { name: "Note pages" })).toBeNull();
    expect(api.removeBookmark).toHaveBeenCalledExactlyOnceWith(W1, "alpha/n032.md");
    expect(api.bookmarks).toHaveBeenCalledTimes(2);
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(api.note).not.toHaveBeenCalled();
  });
  it("resets after a real create refresh and uses the shrunken settled listing", async () => {
    const rows = paginationRows();
    const held = deferred<Record<string, unknown>>();
    const api = vaultApi({ entries: vi.fn(async () => scan(rows)) });
    const writer = writeApi();
    render(<VaultBrowser workspaceId={W1} api={api} write={write(writer)} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
    vi.mocked(api.entries).mockImplementationOnce(() => held.promise);
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "alpha/new.md" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "# Exact new" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create note" })));
    await debounce();
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
    await act(async () => held.resolve(scan(rows.slice(0, 4))));
    expect(pagedRefs()).toEqual(rows.slice(0, 4).map((row) => row.ref));
    expect(screen.queryByRole("group", { name: "Note pages" })).toBeNull();
    expect(writer.createNote).toHaveBeenCalledExactlyOnceWith(
      W1,
      4,
      "12:34",
      "alpha/new.md",
      "# Exact new",
    );
    expect(writer.saveNote).not.toHaveBeenCalled();
    expect(api.entries).toHaveBeenCalledTimes(2);
    expect(api.note).toHaveBeenCalledExactlyOnceWith(W1, "alpha/new.md", expect.anything());
    expect(screen.getByRole("article", { name: "Selected note" })).toBeTruthy();
  });
});

describe("VaultBrowser pagination preserves the selected note", () => {
  it("keeps the exact dirty draft, caret, full metadata and ref while paging without admission or IO", async () => {
    const rows = paginationRows();
    const api = vaultApi({
      entries: vi.fn(async () => scan(rows)),
      note: vi.fn(async (_id, ref) =>
        noteProjection(ref, displayProjection({ display_title: "SKILL", tags: ["literal tag"] }), {
          source: "\uFEFF---\r\ntitle: SKILL\r\nunknown: kept\r\n---\r\nBody\r\n",
          sha256: "b".repeat(64),
        }),
      ),
      metadata: vi.fn(),
    });
    const writer = writeApi();
    const navigation = vi.fn(() => true);
    let handle: VaultNavigationHandle | null = null;
    const current = (): VaultNavigationHandle => {
      if (!handle) throw new Error("Missing current Vault handle");
      return handle;
    };
    const ready = (value: VaultNavigationHandle) => {
      handle = value;
      return () => {
        handle = null;
      };
    };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        write={write(writer)}
        onOpenUrl={vi.fn()}
        onNavigation={navigation}
        onNavigationReady={ready}
      />,
    );
    await debounce();
    const next = screen.getByRole("button", { name: "Next note page" });
    fireEvent.click(next);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Row 032" })));
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const editor = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
    const draft = "\uFEFF---\ntitle: SKILL\nunknown: held\n---\nExact dirty body\n";
    fireEvent.change(editor, { target: { value: draft } });
    editor.setSelectionRange(3, 11, "backward");
    next.focus();
    fireEvent.click(next);
    expect(pagedRefs()).toEqual(rows.slice(64).map((row) => row.ref));
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
    expect(editor.value).toBe(draft);
    expect([editor.selectionStart, editor.selectionEnd, editor.selectionDirection]).toEqual([
      3,
      11,
      "backward",
    ]);
    expect(document.activeElement).toBe(next);
    expect(current().snapshot()).toMatchObject({ workspaceId: W1, ref: "alpha/n032.md", page: 2 });
    expect(screen.getByRole("heading", { name: "SKILL" })).toBeTruthy();
    expect(
      within(screen.getByRole("article", { name: "Selected note" })).getByRole("list", {
        name: "About this note",
      }).textContent,
    ).toBe("alpha#literal tag");
    fireEvent.click(screen.getByRole("button", { name: "Previous note page" }));
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(editor);
    expect(current().snapshot()).toMatchObject({ ref: "alpha/n032.md", page: 1 });
    expect(confirm).not.toHaveBeenCalled();
    expect(navigation).toHaveBeenCalledTimes(1);
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(api.note).toHaveBeenCalledExactlyOnceWith(W1, "alpha/n032.md", expect.anything());
    expect(api.backlinks).toHaveBeenCalledTimes(1);
    expect(api.history).toHaveBeenCalledTimes(1);
    expect(api.metadata).not.toHaveBeenCalled();
    expect(writer.saveNote).not.toHaveBeenCalled();
    expect(writer.createNote).not.toHaveBeenCalled();
    expect(api.addBookmark).not.toHaveBeenCalled();
    expect(api.removeBookmark).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
  it("restores the page-two local origin and exact outer offset after two admitted notes", async () => {
    const rows = paginationRows();
    const api = vaultApi({ entries: vi.fn(async () => scan(rows)), metadata: vi.fn() });
    const { container } = render(
      <div data-region="view">
        <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />
      </div>,
    );
    await debounce();
    const region = container.firstElementChild as HTMLElement;
    fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
    region.scrollTop = 217;
    region.scrollLeft = 13;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Row 032" })));
    region.scrollTop = 93;
    fireEvent.click(screen.getByRole("button", { name: "Previous note page" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Row 001" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
    expect(screen.getByRole("heading", { name: "alpha/n032.md" })).toBeTruthy();
    expect(pagedRefs()).toEqual(rows.slice(0, 32).map((row) => row.ref));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Row 001" }));
    expect(region.scrollTop).toBe(93);
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    expect(pagedRefs()).toEqual(rows.slice(32, 64).map((row) => row.ref));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Row 032" }));
    expect([region.scrollTop, region.scrollLeft]).toEqual([217, 13]);
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.note).mock.calls.map(([id, ref]) => [id, ref])).toEqual([
      [W1, "alpha/n032.md"],
      [W1, "alpha/n001.md"],
      [W1, "alpha/n032.md"],
    ]);
    expect(api.backlinks).toHaveBeenCalledTimes(3);
    expect(api.history).toHaveBeenCalledTimes(3);
    expect(api.metadata).not.toHaveBeenCalled();
  });
});

function paginationSnapshot(
  overrides: Partial<VaultNavigationSnapshot> = {},
): VaultNavigationSnapshot {
  return {
    workspaceId: W1,
    ref: null,
    query: "needle",
    folder: "alpha",
    sort: "name",
    onlyMarked: false,
    page: 1,
    opener: { kind: "row", ref: "alpha/n032.md" },
    scroll: { top: 217, left: 13 },
    ...overrides,
  };
}
describe("VaultBrowser opaque page restoration", () => {
  it.each(["ready", "empty", "failed"])(
    "waits for the matching restored list and handles %s coverage once",
    async (kind) => {
      const rows = paginationRows();
      const held = deferred<Record<string, unknown>>();
      const api = vaultApi({ entries: vi.fn(() => held.promise), metadata: vi.fn() });
      const navigation = vi.fn(() => true);
      let handle: VaultNavigationHandle | null = null;
      const current = (): VaultNavigationHandle => {
        if (!handle) throw new Error("Missing current Vault handle");
        return handle;
      };
      const ready = (value: VaultNavigationHandle) => {
        handle = value;
        return () => {
          handle = null;
        };
      };
      const view = render(
        <div data-region="view">
          <VaultBrowser
            workspaceId={W1}
            api={api}
            onOpenUrl={vi.fn()}
            initialNavigation={paginationSnapshot()}
            onNavigation={navigation}
            onNavigationReady={ready}
          />
        </div>,
      );
      const region = view.container.firstElementChild as HTMLElement;
      const input = screen.getByRole("textbox", { name: "Search note contents" });
      input.focus();
      await debounce();
      expect(current().snapshot().page).toBe(1);
      expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
      expect(document.activeElement).toBe(input);
      expect([region.scrollTop, region.scrollLeft]).toEqual([0, 0]);
      await act(async () => {
        if (kind === "failed") held.reject(new Error("synthetic listing failure"));
        else held.resolve(scan(kind === "empty" ? [] : rows));
      });
      if (kind === "ready") {
        expect(pagedRefs()).toEqual(rows.slice(32, 64).map((row) => row.ref));
        expect(screen.getByText("Page 2 of 3")).toBeTruthy();
        expect(document.activeElement).toBe(screen.getByRole("button", { name: "Row 032" }));
        expect(current().snapshot().page).toBe(1);
      } else {
        expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
        expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
        expect(current().snapshot().page).toBe(0);
        if (kind === "empty")
          expect(screen.queryByRole("group", { name: "Note pages" })).toBeNull();
      }
      expect([region.scrollTop, region.scrollLeft]).toEqual([217, 13]);
      input.focus();
      region.scrollTop = 47;
      view.rerender(
        <div data-region="view">
          <VaultBrowser
            workspaceId={W1}
            api={api}
            onOpenUrl={vi.fn()}
            initialNavigation={paginationSnapshot({ page: 2 })}
            onNavigation={navigation}
            onNavigationReady={ready}
          />
        </div>,
      );
      expect(current().snapshot().page).toBe(kind === "ready" ? 1 : 0);
      expect(document.activeElement).toBe(input);
      expect(region.scrollTop).toBe(47);
      expect(api.entries).toHaveBeenCalledExactlyOnceWith(W1, "needle", expect.anything());
      expect(api.note).not.toHaveBeenCalled();
      expect(api.backlinks).not.toHaveBeenCalled();
      expect(api.history).not.toHaveBeenCalled();
      expect(api.metadata).not.toHaveBeenCalled();
      expect(navigation).not.toHaveBeenCalled();
    },
  );
  it("a deliberate new query invalidates pending page and focus ownership", async () => {
    const rows = paginationRows();
    const old = deferred<Record<string, unknown>>();
    const hits = [...rows].reverse();
    const api = vaultApi({
      entries: vi.fn((_id, query) =>
        query === "needle" ? old.promise : Promise.resolve(scan(hits)),
      ),
    });
    const { container } = render(
      <div data-region="view">
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          initialNavigation={paginationSnapshot()}
        />
      </div>,
    );
    await debounce();
    const input = screen.getByRole("textbox", { name: "Search note contents" });
    const region = container.firstElementChild as HTMLElement;
    input.focus();
    region.scrollTop = 39;
    fireEvent.change(input, { target: { value: "new" } });
    await debounce();
    expect(pagedRefs()).toEqual(hits.slice(0, 32).map((row) => row.ref));
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
    await act(async () => old.resolve(scan(rows)));
    expect(pagedRefs()).toEqual(hits.slice(0, 32).map((row) => row.ref));
    expect(document.activeElement).toBe(input);
    expect(region.scrollTop).toBe(39);
    expect(api.entries).toHaveBeenCalledTimes(2);
    expect(api.entries).toHaveBeenLastCalledWith(W1, "new", expect.anything());
    expect(api.note).not.toHaveBeenCalled();
  });
});

describe("VaultBrowser compatible bounded page snapshots", () => {
  it.each(["omitted", "negative", "fraction", "over-cap", "nan", "workspace"])(
    "defaults %s snapshot pages to the first returned page",
    async (kind) => {
      const rows = paginationRows();
      const values: Record<string, number> = {
        negative: -1,
        fraction: 1.5,
        "over-cap": 157,
        nan: Number.NaN,
        workspace: 1,
      };
      const { page: ignored, ...older } = paginationSnapshot();
      void ignored;
      const snapshot = {
        ...older,
        ...(kind === "omitted" ? {} : { page: values[kind] ?? 0 }),
        ...(kind === "workspace" ? { workspaceId: W2 } : {}),
      };
      const api = vaultApi({ entries: vi.fn(async () => scan(rows)), metadata: vi.fn() });
      const navigation = vi.fn(() => true);
      const { container } = render(
        <div data-region="view">
          <VaultBrowser
            workspaceId={W1}
            api={api}
            onOpenUrl={vi.fn()}
            initialNavigation={snapshot}
            onNavigation={navigation}
          />
        </div>,
      );
      await debounce();
      expect(pagedRefs()).toEqual(rows.slice(0, 32).map((row) => row.ref));
      expect(screen.getByText("Page 1 of 3")).toBeTruthy();
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "Previous note page" }).disabled,
      ).toBe(true);
      expect(api.entries).toHaveBeenCalledExactlyOnceWith(
        W1,
        kind === "workspace" ? undefined : "needle",
        expect.anything(),
      );
      expect((container.firstElementChild as HTMLElement).scrollTop).toBe(
        kind === "workspace" ? 0 : 217,
      );
      expect(api.note).not.toHaveBeenCalled();
      expect(api.metadata).not.toHaveBeenCalled();
      expect(navigation).not.toHaveBeenCalled();
    },
  );
  it("clamps a valid but unavailable restored page and uses the missing-opener heading", async () => {
    const rows = paginationRows();
    const api = vaultApi({ entries: vi.fn(async () => scan(rows)) });
    const { container } = render(
      <div data-region="view">
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          initialNavigation={paginationSnapshot({
            page: 156,
            opener: { kind: "row", ref: "missing.md" },
          })}
        />
      </div>,
    );
    await debounce();
    expect(pagedRefs()).toEqual(rows.slice(64).map((row) => row.ref));
    expect(screen.getByText("Page 3 of 3")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
    expect((container.firstElementChild as HTMLElement).scrollTop).toBe(217);
    expect(api.entries).toHaveBeenCalledTimes(1);
    expect(api.note).not.toHaveBeenCalled();
  });
  it("preserves a local page-two origin through the matching Back-triggered reread", async () => {
    const rows = paginationRows();
    const held = deferred<Record<string, unknown>>();
    const api = vaultApi({
      entries: vi.fn(async (_id, query) => scan(query ? [...rows].reverse() : rows)),
      metadata: vi.fn(),
    });
    const { container } = render(
      <div data-region="view">
        <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />
      </div>,
    );
    await debounce();
    const region = container.firstElementChild as HTMLElement;
    fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
    region.scrollTop = 187;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Row 032" })));
    fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
      target: { value: "other" },
    });
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Row 069" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back" })));
    expect(screen.getByRole("heading", { name: "alpha/n032.md" })).toBeTruthy();
    vi.mocked(api.entries).mockImplementationOnce(() => held.promise);
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    const input = screen.getByRole("textbox", { name: "Search note contents" });
    input.focus();
    region.scrollTop = 41;
    await debounce();
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
    expect(document.activeElement).toBe(input);
    expect(region.scrollTop).toBe(41);
    await act(async () => held.resolve(scan(rows)));
    expect(pagedRefs()).toEqual(rows.slice(32, 64).map((row) => row.ref));
    expect(screen.getByText("Page 2 of 3")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Row 032" }));
    expect(region.scrollTop).toBe(187);
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    expect(api.entries).toHaveBeenCalledTimes(3);
    expect(api.entries).toHaveBeenLastCalledWith(W1, undefined, expect.anything());
    expect(vi.mocked(api.note).mock.calls.map(([id, ref]) => [id, ref])).toEqual([
      [W1, "alpha/n032.md"],
      [W1, "alpha/n069.md"],
      [W1, "alpha/n032.md"],
    ]);
    expect(api.backlinks).toHaveBeenCalledTimes(3);
    expect(api.history).toHaveBeenCalledTimes(3);
    expect(api.metadata).not.toHaveBeenCalled();
  });
  it.each(["ready", "failed"])(
    "waits for %s bookmark coverage before settling a restored filtered page",
    async (kind) => {
      const rows = paginationRows();
      const held = deferred<Awaited<ReturnType<BrowserApi["bookmarks"]>>>();
      const api = vaultApi({
        entries: vi.fn(async () => scan(rows)),
        bookmarks: vi.fn(() => held.promise),
        metadata: vi.fn(),
      });
      const navigation = vi.fn(() => true);
      const { container } = render(
        <div data-region="view">
          <VaultBrowser
            workspaceId={W1}
            api={api}
            onOpenUrl={vi.fn()}
            initialNavigation={paginationSnapshot({ query: "", onlyMarked: true })}
            onNavigation={navigation}
          />
        </div>,
      );
      const region = container.firstElementChild as HTMLElement;
      const input = screen.getByRole("textbox", { name: "Search note contents" });
      input.focus();
      await debounce();
      expect(screen.queryByRole("group", { name: "Note pages" })).toBeNull();
      expect(document.activeElement).toBe(input);
      expect(region.scrollTop).toBe(0);
      await act(async () => {
        if (kind === "failed") held.reject(new Error("synthetic bookmark failure"));
        else
          held.resolve({
            bookmarks: rows.map((row) => ({ ref: row.ref, created_at: CREATED_AT })),
          });
      });
      if (kind === "ready") {
        expect(pagedRefs()).toEqual(rows.slice(32, 64).map((row) => row.ref));
        expect(screen.getByText("Page 2 of 3")).toBeTruthy();
        expect(document.activeElement).toBe(screen.getByRole("button", { name: "Row 032" }));
      } else {
        expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
        expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
        expect(screen.getByRole("alert")).toBeTruthy();
      }
      expect([region.scrollTop, region.scrollLeft]).toEqual([217, 13]);
      expect(api.entries).toHaveBeenCalledTimes(1);
      expect(api.bookmarks).toHaveBeenCalledTimes(1);
      expect(api.note).not.toHaveBeenCalled();
      expect(api.metadata).not.toHaveBeenCalled();
      expect(api.addBookmark).not.toHaveBeenCalled();
      expect(api.removeBookmark).not.toHaveBeenCalled();
      expect(navigation).not.toHaveBeenCalled();
    },
  );
});

it("selects duplicate titles by their exact ref", async () => {
  const refs = ["alpha/SKILL.md", "beta/SKILL.md"];
  const api = vaultApi({
    entries: vi.fn(async () => scan(refs.map((ref) => ({ ref, title: "Same" })))),
  });
  render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
  await debounce();
  const rows = screen.getAllByRole("button", { name: "Same" });
  expect(rows).toHaveLength(2);
  for (const [index, ref] of refs.entries()) {
    const description = rows[index]?.getAttribute("aria-describedby");
    expect(description && document.getElementById(description)?.textContent).toBe(ref);
  }
  await act(async () => fireEvent.click(rows[1] as HTMLElement));
  expect(api.note).toHaveBeenCalledExactlyOnceWith(W1, "beta/SKILL.md", expect.any(AbortSignal));
  expect(rows[1]?.getAttribute("aria-current")).toBe("true");
  const selected = within(screen.getByRole("article", { name: "Selected note" }));
  expect(selected.getByText("beta/SKILL.md", { selector: ".vault-ref" })).toBeTruthy();
});

it("has no Load details control and never calls the metadata reader", async () => {
  const api = vaultApi({
    entries: vi.fn(async () => scan(paginationRows(2))),
    metadata: vi.fn(),
  });
  render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
  await debounce();
  expect(screen.queryByRole("button", { name: /Load details/ })).toBeNull();
  expect(api.metadata).not.toHaveBeenCalled();
});

it("hides the pager fieldset for a single returned page", async () => {
  const api = vaultApi({ entries: vi.fn(async () => scan(paginationRows(32))) });
  render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
  await debounce();
  expect(pagedRefs()).toHaveLength(32);
  expect(screen.queryByRole("group", { name: "Note pages" })).toBeNull();
});

it("discards a stale listing answer even when the search scope returns to an identical value", async () => {
  const first = deferred<Record<string, unknown>>();
  const second = deferred<Record<string, unknown>>();
  const third = deferred<Record<string, unknown>>();
  const entries = vi
    .fn<VaultReadApi["entries"]>()
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise)
    .mockImplementationOnce(() => third.promise);
  render(<VaultBrowser workspaceId={W1} api={vaultApi({ entries })} onOpenUrl={vi.fn()} />);
  await debounce();
  const search = screen.getByRole("textbox", { name: "Search note contents" });
  fireEvent.change(search, { target: { value: "needle" } });
  await debounce();
  fireEvent.change(search, { target: { value: "" } });
  await debounce();
  await act(async () => third.resolve(scan([{ ref: "current.md", title: "Current" }])));
  expect(screen.getByRole("button", { name: "Current" })).toBeTruthy();
  await act(async () => first.resolve(scan([{ ref: "stale.md", title: "Stale" }])));
  expect(screen.queryByRole("button", { name: "Stale" })).toBeNull();
  expect(screen.getByRole("button", { name: "Current" })).toBeTruthy();
});

it.each([false, true])(
  "a held Recovery restore refreshes the listing only once it settles, and not on rejection (%s)",
  async (rejection) => {
    const api = vaultApi();
    const ops: VaultOpsApi = {
      preview: vi.fn(),
      apply: vi.fn(() => held.promise),
      recovery: vi.fn(async () => ({
        entries: [
          {
            recovery_id: W2,
            kind: "deleted" as const,
            ref: "alpha/removed.md",
            byte_size: 10,
            created_at: CREATED_AT,
            expires_at: "2026-10-31T00:00:00.000Z",
          },
        ],
        stuck: [],
      })),
      forgetOperation: vi.fn(),
      resolve: vi.fn(),
    };
    const held = deferred<Awaited<ReturnType<VaultOpsApi["apply"]>>>();
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        write={{ ...write(writeApi()), ops }}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Restore", hidden: true })),
    );
    expect(api.entries).toHaveBeenCalledTimes(1);
    await act(async () => {
      if (rejection) held.reject(new Error("restore refused"));
      else
        held.resolve({
          operation_id: W1,
          status: "done",
          replayed: false,
          steps_applied: 1,
          reason: null,
          created_folders: [],
        });
      await held.promise.catch(() => {});
    });
    await debounce();
    expect(api.entries).toHaveBeenCalledTimes(rejection ? 1 : 2);
  },
);

it("a stale memory completion after an api change does not refresh the listing", async () => {
  const first = vaultApi({ entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])) });
  const second = vaultApi({ entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])) });
  const created = deferred<Awaited<ReturnType<VaultKnowledgeApi["createMemory"]>>>();
  const knowledge = {
    memories: vi.fn(async () => ({ items: [], invalid: [], partial: false })),
    createMemory: vi.fn(() => created.promise),
    composeMemory: vi.fn(async () => ({ text: "preview" })),
  } as unknown as VaultKnowledgeApi;
  const props = {
    workspaceId: W1,
    api: first,
    memory: { api: knowledge, policyRevision: 4, rootPhysicalId: "12:34", protected: false },
    onOpenUrl: vi.fn(),
  };
  const view = render(<VaultBrowser {...props} />);
  await debounce();
  fireEvent.change(screen.getByRole("textbox", { name: "Content", hidden: true }), {
    target: { value: "stale claim" },
  });
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: "Review", hidden: true })),
  );
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: "Create", hidden: true })),
  );
  view.rerender(<VaultBrowser {...props} api={second} />);
  await debounce();
  const callsAfterSwap = vi.mocked(second.entries).mock.calls.length;
  await act(async () => {
    created.resolve({
      outcome: "written",
      memory_id: W2,
      ref: "memory/synthetic.md",
      sha256: "a".repeat(64),
      operation_id: W1,
      created_folders: [],
      replayed: false,
    });
    await created.promise;
  });
  // The stale onDone, if not held back by the owner guard, only schedules a debounced refetch;
  // without this tick a mutant that drops the guard would still pass by accident.
  await debounce();
  expect(second.entries).toHaveBeenCalledTimes(callsAfterSwap);
});

it("a stale Recovery restore completion after only the ops client changes does not refresh", async () => {
  const api = vaultApi();
  const restored = deferred<Awaited<ReturnType<VaultOpsApi["apply"]>>>();
  const firstOps: VaultOpsApi = {
    preview: vi.fn(),
    apply: vi.fn(() => restored.promise),
    recovery: vi.fn(async () => ({
      entries: [
        {
          recovery_id: W2,
          kind: "deleted" as const,
          ref: "alpha/removed.md",
          byte_size: 10,
          created_at: CREATED_AT,
          expires_at: "2026-10-31T00:00:00.000Z",
        },
      ],
      stuck: [],
    })),
    forgetOperation: vi.fn(),
    resolve: vi.fn(),
  };
  const nextOps: VaultOpsApi = {
    preview: vi.fn(),
    apply: vi.fn(),
    recovery: vi.fn(async () => ({ entries: [], stuck: [] })),
    forgetOperation: vi.fn(),
    resolve: vi.fn(),
  };
  const writer = writeApi();
  const props = {
    workspaceId: W1,
    api,
    write: { ...write(writer), ops: firstOps },
    onOpenUrl: vi.fn(),
  };
  const view = render(<VaultBrowser {...props} />);
  await debounce();
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: "Restore", hidden: true })),
  );
  view.rerender(<VaultBrowser {...props} write={{ ...props.write, ops: nextOps }} />);
  await act(async () => {});
  const callsAfterSwap = vi.mocked(api.entries).mock.calls.length;
  await act(async () => {
    restored.resolve({
      operation_id: W1,
      status: "done",
      replayed: false,
      steps_applied: 1,
      reason: null,
      created_folders: [],
    });
    await restored.promise;
  });
  // The stale onDone, if not held back by the owner guard, only schedules a debounced refetch;
  // without this tick a mutant that drops the guard would still pass by accident.
  await debounce();
  expect(api.entries).toHaveBeenCalledTimes(callsAfterSwap);
  expect(nextOps.apply).not.toHaveBeenCalled();
});

it.each(["unsupported", "truncated", "frontmatterError"] as const)(
  "shows the quiet line when %s",
  async (kind) => {
    const api = vaultApi({
      entries: vi.fn(async () => scan([{ ref: "one.md", title: "One" }])),
      note: vi.fn(async () =>
        kind === "frontmatterError"
          ? {
              ...note("one.md"),
              note: { ...(note("one.md").note as object), frontmatter_error: "invalid_line" },
            }
          : noteProjection("one.md", displayProjection({ state: kind, heading_state: kind })),
      ),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "One" })));
    expect(screen.getByText("Some details in this note's header could not be read.")).toBeTruthy();
  },
);

type HeldPagePreview = ReturnType<typeof deferred<Awaited<ReturnType<VaultOpsApi["preview"]>>>>;
function pendingPageOps() {
  const held = deferred<Awaited<ReturnType<VaultOpsApi["preview"]>>>();
  const api: VaultOpsApi = {
    preview: vi.fn(() => held.promise),
    apply: vi.fn(),
    recovery: vi.fn(async () => ({ entries: [], stuck: [] })),
    forgetOperation: vi.fn(),
    resolve: vi.fn(),
  };
  return { api, held };
}
async function admitPagePreview() {
  fireEvent.click(screen.getByRole("button", { name: "Note actions" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Review" })));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
}
async function settlePagePreview(held: HeldPagePreview, rejection: boolean) {
  await act(async () => {
    if (rejection) held.reject(new Error("obsolete preview refused"));
    else held.resolve({ steps: [], creates_folders: [], recovery_days: 30 });
    await held.promise.catch(() => {});
  });
}
async function sizedVault(
  overrides: Partial<BrowserApi> = {},
  writer = writeApi(),
  mount = (props: Parameters<typeof VaultBrowser>[0]) => render(<VaultBrowser {...props} />),
) {
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
  const api = vaultApi({
    entries: vi.fn(async () =>
      scan([
        { ref: "a.md", title: "Alpha" },
        { ref: "b.md", title: "Beta" },
      ]),
    ),
    ...overrides,
  });
  const props = { workspaceId: W1, api, write: write(writer), onBack: vi.fn(), onOpenUrl: vi.fn() };
  const view = mount(props);
  await debounce();
  fireEvent.click(screen.getByRole("button", { name: "Alpha" }));
  await act(async () => {});
  const root = screen.getByRole("region", { name: "Vault browser" });
  const pane = screen.getByRole("article", { name: "Selected note" });
  const resize = (width: number, viewport = 1200) => {
    Object.defineProperty(root, "clientWidth", { configurable: true, value: width });
    const current = root.querySelector<HTMLElement>(".vault-inspector");
    if (current)
      Object.defineProperties(current, {
        offsetTop: { configurable: true, value: 100 },
        clientHeight: { configurable: true, value: 300 },
      });
    vi.stubGlobal("innerWidth", viewport);
    act(() => {
      for (const callback of observers) callback([], {} as ResizeObserver);
    });
  };
  return { api, writer, view, props, root, pane, resize };
}
function cleanSizedVault() {
  cleanup();
  vi.unstubAllGlobals();
}
const vaultSplit = () => screen.getByRole("separator", { name: "Resize Vault panes" });
it("Vault inner split keeps the natural ratio and Reset without reading or leaving", async () => {
  const { api, writer, root, props, resize } = await sizedVault();
  try {
    expect(screen.queryByRole("separator")).toBeNull();
    resize(908);
    const handle = vaultSplit();
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("");
    expect(handle.getAttribute("aria-valuenow")).toBe("300");
    expect(handle.getAttribute("aria-controls")).toBe(
      screen.getByRole("navigation", { name: "Notes" }).id,
    );
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("310px");
    fireEvent.click(screen.getByRole("button", { name: "Reset Vault panes" }));
    resize(1808);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("");
    expect(vaultSplit().getAttribute("aria-valuenow")).toBe("600");
    expect(api.entries).toHaveBeenCalledTimes(1);
    for (const read of [api.note, api.backlinks, api.history])
      expect(read).toHaveBeenCalledTimes(1);
    expect(writer.saveNote).not.toHaveBeenCalled();
    expect(writer.createNote).not.toHaveBeenCalled();
    expect(props.onBack).not.toHaveBeenCalled();
  } finally {
    cleanSizedVault();
  }
});

function vaultSplitPointer(handle: HTMLElement, type: string, clientX: number) {
  const event = new MouseEvent(type, { bubbles: true, button: 0, clientX });
  Object.defineProperties(event, { pointerId: { value: 41 }, isPrimary: { value: true } });
  fireEvent(handle, event);
}
function captureVaultSplit(handle: HTMLElement) {
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
it("Vault keyboard bounds preserve an explicit request through shortage and regrowth", async () => {
  const { root, resize } = await sizedVault();
  try {
    resize(908);
    expect(vaultSplit().getAttribute("aria-valuemin")).toBe("240");
    expect(vaultSplit().getAttribute("aria-valuemax")).toBe("660");
    for (const [key, shiftKey, expected] of [
      ["Home", false, 240],
      ["ArrowLeft", true, 240],
      ["ArrowRight", true, 290],
      ["ArrowLeft", false, 280],
      ["End", false, 660],
      ["ArrowRight", true, 660],
    ] as const) {
      fireEvent.keyDown(vaultSplit(), { key, shiftKey });
      expect(root.style.getPropertyValue("--vault-list-width")).toBe(`${expected}px`);
    }
    resize(528);
    expect(vaultSplit().getAttribute("aria-valuemax")).toBe("280");
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("280px");
    resize(487);
    expect(screen.queryByRole("separator")).toBeNull();
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("");
    resize(908);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("660px");
  } finally {
    cleanSizedVault();
  }
});
it("Vault pointer uses the last coordinate and converts font scale once", async () => {
  const scale = document.documentElement.style.getPropertyValue("--font-scale");
  const { root, resize } = await sizedVault();
  try {
    resize(908);
    document.documentElement.style.setProperty("--font-scale", "1.5");
    const handle = vaultSplit();
    const release = captureVaultSplit(handle);
    vaultSplitPointer(handle, "pointerdown", 100);
    vaultSplitPointer(handle, "pointermove", 145);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("330px");
    vaultSplitPointer(handle, "pointerup", 160);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("340px");
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("350px");
    vaultSplitPointer(handle, "pointerdown", 100);
    vaultSplitPointer(handle, "pointermove", 145);
    document.documentElement.style.setProperty("--font-scale", "1");
    vaultSplitPointer(handle, "pointerup", 190);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("350px");
    expect(release).toHaveBeenCalledTimes(2);
    vaultSplitPointer(handle, "pointerdown", 100);
    vaultSplitPointer(handle, "pointerup", -1000);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("240px");
    vaultSplitPointer(handle, "pointerdown", 100);
    vaultSplitPointer(handle, "pointerup", 3000);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("660px");
    expect(release).toHaveBeenCalledTimes(4);
  } finally {
    if (scale) document.documentElement.style.setProperty("--font-scale", scale);
    else document.documentElement.style.removeProperty("--font-scale");
    cleanSizedVault();
  }
});

it.each(["Escape", "blur", "pointercancel", "lostpointercapture"])(
  "Vault %s rollback retains natural and explicit modes without late commits",
  async (kind) => {
    for (const explicit of [false, true]) {
      const { root, resize } = await sizedVault();
      try {
        resize(908);
        const handle = vaultSplit();
        if (explicit) fireEvent.keyDown(handle, { key: "ArrowRight" });
        const before = root.style.getPropertyValue("--vault-list-width");
        const release = captureVaultSplit(handle);
        vaultSplitPointer(handle, "pointerdown", 100);
        vaultSplitPointer(handle, "pointermove", 140);
        if (kind === "Escape") fireEvent.keyDown(window, { key: "Escape" });
        else if (kind === "blur") fireEvent.blur(window);
        else vaultSplitPointer(handle, kind, 140);
        vaultSplitPointer(handle, "pointerup", 180);
        expect(root.style.getPropertyValue("--vault-list-width")).toBe(before);
        expect(release).toHaveBeenCalledTimes(1);
        resize(1808);
        expect(vaultSplit().getAttribute("aria-valuenow")).toBe(explicit ? "310" : "600");
      } finally {
        cleanSizedVault();
      }
    }
  },
);
it.each([false, true])(
  "Vault stationary/return/reset drags preserve natural mode (%s)",
  async (moved) => {
    const { root, resize } = await sizedVault();
    try {
      resize(908);
      const handle = vaultSplit();
      const release = captureVaultSplit(handle);
      vaultSplitPointer(handle, "pointerdown", 100);
      if (moved) vaultSplitPointer(handle, "pointermove", 130);
      vaultSplitPointer(handle, "pointerup", 100);
      expect(root.style.getPropertyValue("--vault-list-width")).toBe("");
      vaultSplitPointer(handle, "pointerdown", 100);
      if (moved) vaultSplitPointer(handle, "pointermove", 130);
      fireEvent.click(screen.getByRole("button", { name: "Reset Vault panes" }));
      // Capture mocks need the native release event explicitly.
      vaultSplitPointer(handle, "lostpointercapture", 130);
      vaultSplitPointer(handle, "pointerup", 180);
      resize(1808);
      expect(root.style.getPropertyValue("--vault-list-width")).toBe("");
      expect(vaultSplit().getAttribute("aria-valuenow")).toBe("600");
      expect(release).toHaveBeenCalledTimes(2);
      vaultSplitPointer(handle, "pointerdown", 100);
      vaultSplitPointer(handle, "pointerup", 120);
      expect(root.style.getPropertyValue("--vault-list-width")).toBe("620px");
    } finally {
      cleanSizedVault();
    }
  },
);
it("Vault changed bounds cancel the capture but retain the original request", async () => {
  const { root, resize } = await sizedVault();
  try {
    resize(908);
    fireEvent.keyDown(vaultSplit(), { key: "End" });
    const handle = vaultSplit();
    const release = captureVaultSplit(handle);
    vaultSplitPointer(handle, "pointerdown", 100);
    vaultSplitPointer(handle, "pointermove", 70);
    resize(528);
    expect(release).toHaveBeenCalledTimes(1);
    vaultSplitPointer(handle, "pointerup", 40);
    resize(908);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("660px");
  } finally {
    cleanSizedVault();
  }
});

it("Vault narrow hiding releases owned focus, retains the draft and preserves other focus", async () => {
  const { root, resize } = await sizedVault();
  try {
    resize(908);
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const area = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
    fireEvent.change(area, { target: { value: "literal edited draft" } });
    area.setSelectionRange(2, 7);
    const handle = vaultSplit();
    const release = captureVaultSplit(handle);
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    vaultSplitPointer(handle, "pointerdown", 100);
    vaultSplitPointer(handle, "pointermove", 140);
    resize(908, 799);
    expect(screen.queryByRole("separator")).toBeNull();
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("");
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
    expect(release).toHaveBeenCalledTimes(1);
    vaultSplitPointer(handle, "pointerup", 180);
    resize(908);
    expect(root.style.getPropertyValue("--vault-list-width")).toBe("310px");
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(area);
    expect(area.value).toBe("literal edited draft");
    expect([area.selectionStart, area.selectionEnd]).toEqual([2, 7]);
    area.focus();
    resize(908, 799);
    expect(document.activeElement).toBe(area);
    expect(screen.getByRole("button", { name: "Back to notes" })).toBeTruthy();
  } finally {
    cleanSizedVault();
  }
});
it("Vault unmount releases capture and actual cancellation listeners without a late callback", async () => {
  const { view, resize } = await sizedVault();
  const add = vi.spyOn(window, "addEventListener");
  const remove = vi.spyOn(window, "removeEventListener");
  try {
    resize(908);
    const handle = vaultSplit();
    const release = captureVaultSplit(handle);
    vaultSplitPointer(handle, "pointerdown", 100);
    const bindings = add.mock.calls.filter(([type]) => type === "keydown" || type === "blur");
    expect(bindings).toHaveLength(2);
    view.unmount();
    for (const [type, listener] of bindings) expect(remove).toHaveBeenCalledWith(type, listener);
    expect(release).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    vaultSplitPointer(handle, "pointerup", 180);
    resize(1808);
    expect(screen.queryByRole("separator")).toBeNull();
  } finally {
    add.mockRestore();
    remove.mockRestore();
    cleanSizedVault();
  }
});

it("Vault resizing preserves the exact dirty editor/caret and existing page admission", async () => {
  const { api, writer, props, root, pane, resize } = await sizedVault();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  try {
    resize(908);
    const list = screen.getByRole("navigation", { name: "Notes" });
    list.scrollTop = 17;
    pane.scrollTop = 29;
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const area = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
    fireEvent.change(area, { target: { value: "literal unchanged draft" } });
    area.setSelectionRange(3, 8);
    const handle = vaultSplit();
    captureVaultSplit(handle);
    vaultSplitPointer(handle, "pointerdown", 100);
    vaultSplitPointer(handle, "pointerup", 160);
    fireEvent.click(screen.getByRole("button", { name: "Reset Vault panes" }));
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(area);
    expect(area.value).toBe("literal unchanged draft");
    expect([area.selectionStart, area.selectionEnd]).toEqual([3, 8]);
    expect(screen.getByRole("navigation", { name: "Notes" })).toBe(list);
    expect(screen.getByRole("article", { name: "Selected note" })).toBe(pane);
    expect([list.scrollTop, pane.scrollTop]).toEqual([17, 29]);
    expect(root.querySelector('[aria-current="true"]')?.getAttribute("data-vault-ref")).toBe(
      "a.md",
    );
    expect(hasUnsavedWork()).toBe(true);
    for (const read of [api.entries, api.note, api.backlinks, api.history])
      expect(read).toHaveBeenCalledTimes(1);
    expect(writer.saveNote).not.toHaveBeenCalled();
    expect(writer.createNote).not.toHaveBeenCalled();
    expect(props.onBack).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(props.onBack).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(area);
    expect(api.note).toHaveBeenCalledTimes(1);
  } finally {
    confirm.mockRestore();
    cleanSizedVault();
  }
});
it("Vault resize leaves an outstanding selected-note read with its original owner", async () => {
  const held = deferred<Record<string, unknown>>();
  const { api, resize, root } = await sizedVault({ note: vi.fn(() => held.promise) });
  try {
    resize(908);
    const signal = vi.mocked(api.note).mock.calls[0]?.[2];
    fireEvent.keyDown(vaultSplit(), { key: "End" });
    fireEvent.click(screen.getByRole("button", { name: "Reset Vault panes" }));
    expect(signal?.aborted).toBe(false);
    expect(api.note).toHaveBeenCalledTimes(1);
    await act(async () => held.resolve(note("a.md", "actual owned result")));
    expect(screen.getByText("actual owned result", { selector: ".md p" })).toBeTruthy();
    expect(root.querySelector('[aria-current="true"]')?.getAttribute("data-vault-ref")).toBe(
      "a.md",
    );
    expect(api.note).toHaveBeenCalledExactlyOnceWith(W1, "a.md", expect.any(AbortSignal));
  } finally {
    cleanSizedVault();
  }
});
it("Vault resize does not replay or release a held Save", async () => {
  const held = deferred<Awaited<ReturnType<VaultWriteApi["saveNote"]>>>();
  const writer = writeApi({ saveNote: vi.fn(() => held.promise) });
  const { api, resize } = await sizedVault({}, writer);
  try {
    resize(908);
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const area = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
    fireEvent.change(area, { target: { value: "held publication" } });
    fireEvent.click(screen.getByRole("button", { name: "Save note" }));
    fireEvent.keyDown(vaultSplit(), { key: "End" });
    fireEvent.click(screen.getByRole("button", { name: "Reset Vault panes" }));
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(area);
    expect(area.value).toBe("held publication");
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Save note" }).disabled).toBe(
      true,
    );
    expect(api.note).toHaveBeenCalledTimes(1);
    expect(writer.saveNote).toHaveBeenCalledTimes(1);
    expect(writer.createNote).not.toHaveBeenCalled();
    await act(async () => held.reject(new Error("synthetic held save refused")));
    expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(area);
    expect(area.value).toBe("held publication");
    expect(writer.saveNote).toHaveBeenCalledTimes(1);
  } finally {
    cleanSizedVault();
  }
});

it("Vault split keeps literal long duplicate refs selectable and current CSS boundaries", async () => {
  const first = `skills/${"alpha".repeat(30)}/SKILL.md`;
  const second = `skills/${"beta".repeat(30)}/SKILL.md`;
  const { api, view, props, resize } = await sizedVault();
  try {
    const next = vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: first, title: "Same" },
          { ref: second, title: "Same" },
        ]),
      ),
    });
    view.rerender(<VaultBrowser {...props} api={next} />);
    await debounce();
    const rows = screen.getAllByRole("button", { name: "Same" });
    expect(rows).toHaveLength(2);
    for (const [index, ref] of [first, second].entries()) {
      const row = rows[index];
      if (!row) throw new Error("Missing exact long-ref row");
      expect(document.getElementById(row.getAttribute("aria-describedby") ?? "")?.textContent).toBe(
        ref,
      );
      fireEvent.click(row);
      await act(async () => {});
      resize(908);
      fireEvent.keyDown(vaultSplit(), { key: "ArrowLeft" });
      expect(next.note).toHaveBeenLastCalledWith(W1, ref, expect.any(AbortSignal));
    }
    expect(next.note).toHaveBeenCalledTimes(2);
    expect(api.note).toHaveBeenCalledTimes(1);
    const css = readCss(import.meta.url, "VaultBrowser.css");
    expect(css).toMatch(/var\(--vault-list-width, minmax\(240px, 1fr\)\) minmax\(0, 2fr\)/);
    expect(css).toMatch(/\.vault-resize-controls\s*\{[^}]*position:\s*absolute/);
    // Reset sits on the divider and shows only while the divider is hovered or focused.
    expect(css).toMatch(/\.vault-split-reset\s*\{[^}]*opacity:\s*0;/);
    expect(css).toMatch(
      /\.vault-resize-controls:focus-within > \.vault-split-reset\s*\{[^}]*opacity:\s*1/,
    );
    expect(css).toMatch(
      /\.vault-list,\s*\.vault-browse,\s*\.vault-inspector,[^{]*\{[^}]*overflow:\s*auto;[^}]*min-height:\s*0/,
    );
    expect(css).toMatch(/\.vault-browser\[data-open\] \.vault-list\s*\{[^}]*display:\s*none/);
    expect(css).toMatch(/\.vault-ref\s*\{[^}]*overflow-wrap:\s*anywhere/);
  } finally {
    cleanSizedVault();
  }
});

it.each([false, true])(
  "Vault sizing retains a canceled pending operation until settlement (%s)",
  async (rejection) => {
    const held = pendingPageOps();
    const { api, writer, props, view, resize } = await sizedVault();
    try {
      view.rerender(<VaultBrowser {...props} write={{ ...props.write, ops: held.api }} />);
      await act(async () => {});
      await admitPagePreview();
      resize(908);
      fireEvent.keyDown(vaultSplit(), { key: "End" });
      fireEvent.click(screen.getByRole("button", { name: "Reset Vault panes" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(held.api.preview).toHaveBeenCalledExactlyOnceWith(W1, {
        policyRevision: 4,
        rootPhysicalId: "12:34",
        kind: "delete",
        ref: "a.md",
        to: undefined,
        expectedSha256: "a".repeat(64),
      });
      expect(held.api.apply).not.toHaveBeenCalled();
      for (const read of [api.entries, api.note, api.backlinks, api.history])
        expect(read).toHaveBeenCalledTimes(1);
      expect(writer.saveNote).not.toHaveBeenCalled();
      expect(writer.createNote).not.toHaveBeenCalled();
      await settlePagePreview(held.held, rejection);
      expect(held.api.preview).toHaveBeenCalledTimes(1);
      expect(held.api.apply).not.toHaveBeenCalled();
      expect(api.note).toHaveBeenCalledTimes(1);
    } finally {
      cleanSizedVault();
    }
  },
);
it.each([false, true])(
  "Vault sizing preserves the held protected proposal draft and exact request (%s)",
  async (rejection) => {
    const held = deferred<CreatedProposal>();
    const createProposal = vi.fn(() => held.promise);
    const { api, writer, resize } = await sizedVault({}, writeApi(), (props) => {
      const { write: omittedWrite, ...readOnly } = props;
      void omittedWrite;
      return render(<VaultBrowser {...readOnly} proposalApi={{ createProposal }} />);
    });
    try {
      fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
      const area = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Markdown" });
      fireEvent.change(area, { target: { value: "held exact protected draft" } });
      area.setSelectionRange(2, 7);
      fireEvent.click(screen.getByRole("button", { name: "Propose" }));
      resize(908);
      fireEvent.keyDown(vaultSplit(), { key: "End" });
      fireEvent.click(screen.getByRole("button", { name: "Reset Vault panes" }));
      expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(area);
      expect(area.value).toBe("held exact protected draft");
      expect([area.selectionStart, area.selectionEnd]).toEqual([2, 7]);
      expect(createProposal).toHaveBeenCalledExactlyOnceWith(
        W1,
        "a.md",
        "held exact protected draft",
        "a".repeat(64),
      );
      for (const read of [api.entries, api.note, api.backlinks, api.history])
        expect(read).toHaveBeenCalledTimes(1);
      expect(writer.saveNote).not.toHaveBeenCalled();
      expect(writer.createNote).not.toHaveBeenCalled();
      await act(async () => {
        if (rejection) held.reject(new Error("synthetic proposal refused"));
        else held.resolve({ id: W2, item: null, replayed: true });
        await held.promise.catch(() => {});
      });
      expect(createProposal).toHaveBeenCalledTimes(1);
      expect(api.note).toHaveBeenCalledTimes(1);
      if (rejection) expect(screen.getByRole("textbox", { name: "Markdown" })).toBe(area);
    } finally {
      cleanSizedVault();
    }
  },
);

describe("VaultBrowser overview", () => {
  const KNOWLEDGE = "30-knowledge/deny-rules.md";
  const SOURCE = "40-sources/paper.md";
  const PLAIN = "notes/plain.md";
  const overviewRow = (ref: string, fields: Partial<OverviewNote> = {}): OverviewNote => ({
    ref,
    state: "ready",
    title: null,
    type: null,
    status: null,
    confidence: null,
    tags: null,
    created: null,
    modified: null,
    summary: null,
    links: null,
    complete: true,
    ...fields,
  });
  const overviewOf = (notes: OverviewNote[]): VaultOverview => ({
    notes,
    partial: false,
    partial_reasons: [],
    omitted: 0,
    read_bytes: 0,
  });
  const reviewed = () =>
    overviewOf([
      overviewRow(KNOWLEDGE, {
        title: "Deny rules hold",
        status: "draft",
        confidence: "high",
        summary: "Deny rules win over allow rules.",
        created: "2026-10-18",
        modified: "2026-10-19",
      }),
      overviewRow(SOURCE, { title: "A paper", status: "processed", created: "2026-10-17" }),
    ]);
  const read = (overrides: Partial<BrowserApi> = {}) =>
    vaultApi({
      entries: vi.fn(async () =>
        scan([
          { ref: "00-inbox/capture.md", title: "capture.md" },
          { ref: KNOWLEDGE, title: "deny-rules.md" },
          { ref: SOURCE, title: "paper.md" },
          { ref: PLAIN, title: "plain.md" },
        ]),
      ),
      overview: vi.fn(async () => reviewed()),
      ...overrides,
    });
  const shown = () => screen.getByRole("region", { name: "Vault overview" });
  const recent = () => within(shown()).getByRole("button", { name: "Deny rules hold" });
  const pressed = (name: string) =>
    within(screen.getByRole("group", { name: "Vault view" }))
      .getByRole("button", { name })
      .getAttribute("aria-pressed");
  const restoring = (fields: Partial<VaultNavigationSnapshot>): VaultNavigationSnapshot => ({
    workspaceId: W1,
    ref: null,
    query: "",
    folder: "",
    sort: "name",
    onlyMarked: false,
    opener: null,
    scroll: { top: 0, left: 0 },
    ...fields,
  });

  it("opens on the overview", async () => {
    const api = read();
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    expect(screen.getByText("Reading your notes…")).toBeTruthy();
    await debounce();
    expect(within(shown()).getByText("Deny rules win over allow rules.")).toBeTruthy();
    expect(within(shown()).getByText("1 note in the inbox")).toBeTruthy();
    expect(screen.getByText("Recent notes, your inbox and how your knowledge grows.")).toBeTruthy();
    expect(pressed("Front page")).toBe("true");
    expect(pressed("All notes")).toBe("false");
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: /Folder/ })).toBeNull();
    expect(api.overview).toHaveBeenCalledExactlyOnceWith(W1, expect.any(AbortSignal));
  });

  it("falls back to All notes with a quiet retry when the overview cannot be read", async () => {
    // "unavailable" does not qualify for the automatic overloaded-retry (vaultOverview.ts), so
    // this still answers error on the very first read, as the manual "Try again" flow below needs.
    const overview = vi
      .fn<NonNullable<BrowserApi["overview"]>>()
      .mockRejectedValueOnce(new Error("unavailable"))
      .mockResolvedValue(reviewed());
    render(<VaultBrowser workspaceId={W1} api={read({ overview })} onOpenUrl={vi.fn()} />);
    await debounce();
    expect(screen.getByRole("navigation", { name: "Notes" })).toBeTruthy();
    expect(screen.getByText("Browse your notes and their context.")).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Vault view" })).toBeNull();
    expect(screen.getByText(/The overview couldn't be read\./)).toBeTruthy();
    const retry = screen.getByRole("button", { name: "Try again" });
    retry.focus();
    fireEvent.click(retry);
    expect(retry.isConnected).toBe(false);
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
    await debounce();
    expect(overview).toHaveBeenCalledTimes(2);
    expect(recent()).toBeTruthy();
    expect(screen.queryByText(/The overview couldn't be read\./)).toBeNull();
  });

  it("stays quietly loading through an automatic overloaded retry, then shows the overview", async () => {
    const overview = vi
      .fn<NonNullable<BrowserApi["overview"]>>()
      .mockRejectedValueOnce(new Error("overloaded"))
      .mockResolvedValue(reviewed());
    render(<VaultBrowser workspaceId={W1} api={read({ overview })} onOpenUrl={vi.fn()} />);
    await debounce();
    expect(overview).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Reading your notes…")).toBeTruthy();
    expect(screen.queryByText(/The overview couldn't be read\./)).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(overview).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "Featured" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Latest" })).toBeTruthy();
  });

  it("shows All notes with no view switch when the overview has no rows", async () => {
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read({ overview: vi.fn(async () => overviewOf([])) })}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    expect(screen.getByRole("navigation", { name: "Notes" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Vault overview" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Vault view" })).toBeNull();
    expect(screen.queryByText(/The overview couldn't be read/)).toBeNull();
  });

  it("switches between the front page and All notes, with filters only in All notes", async () => {
    render(<VaultBrowser workspaceId={W1} api={read()} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "All notes" }));
    expect(pressed("All notes")).toBe("true");
    expect(pressed("Front page")).toBe("false");
    expect(screen.queryByRole("region", { name: "Vault overview" })).toBeNull();
    expect(screen.getByRole("navigation", { name: "Notes" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: /Folder/ })).toBeTruthy();
    expect(screen.getByText("Browse your notes and their context.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Front page" }));
    expect(pressed("Front page")).toBe("true");
    expect(recent()).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "Notes" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: /Folder/ })).toBeNull();
  });

  it("opens a front-page note, and Back restores focus to its exact card", async () => {
    const api = read();
    const { container } = render(
      <div data-region="view">
        <VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />
      </div>,
    );
    await debounce();
    const region = container.firstElementChild as HTMLElement;
    region.scrollTop = 64;
    await act(async () => fireEvent.click(recent()));
    expect(api.note).toHaveBeenCalledExactlyOnceWith(W1, KNOWLEDGE, expect.any(AbortSignal));
    expect(screen.getByRole("article", { name: "Selected note" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "All notes" }));
    region.scrollTop = 5;
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back to notes" })));
    expect(screen.queryByRole("article", { name: "Selected note" })).toBeNull();
    expect(pressed("Front page")).toBe("true");
    expect(document.activeElement).toBe(recent());
    expect(region.scrollTop).toBe(64);
  });

  it("restores a front-page snapshot and focuses its lead card once the overview answers", async () => {
    const held = deferred<VaultOverview>();
    const { container } = render(
      <div data-region="view">
        <VaultBrowser
          workspaceId={W1}
          api={read({ overview: vi.fn(() => held.promise) })}
          onOpenUrl={vi.fn()}
          onNavigation={vi.fn(() => true)}
          initialNavigation={restoring({
            view: "overview",
            opener: { kind: "lead", ref: KNOWLEDGE },
            scroll: { top: 40, left: 0 },
          })}
        />
      </div>,
    );
    const region = container.firstElementChild as HTMLElement;
    await debounce();
    expect(screen.getByText("Reading your notes…")).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
    expect(region.scrollTop).toBe(0);
    await act(async () => held.resolve(reviewed()));
    expect(document.activeElement).toBe(
      container.querySelector(`[data-vault-ref="${KNOWLEDGE}"][data-vault-kind="lead"]`),
    );
    expect(region.scrollTop).toBe(40);
  });

  it("restores a list opener once a slower overview turns out empty", async () => {
    const held = deferred<VaultOverview>();
    const { container } = render(
      <div data-region="view">
        <VaultBrowser
          workspaceId={W1}
          api={read({ overview: vi.fn(() => held.promise) })}
          onOpenUrl={vi.fn()}
          onNavigation={vi.fn(() => true)}
          initialNavigation={restoring({
            opener: { kind: "row", ref: PLAIN },
            scroll: { top: 30, left: 0 },
          })}
        />
      </div>,
    );
    const region = container.firstElementChild as HTMLElement;
    await debounce();
    expect(screen.getByText("Reading your notes…")).toBeTruthy();
    await act(async () => held.resolve(overviewOf([])));
    expect(document.activeElement).toBe(
      within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", {
        name: "plain.md",
      }),
    );
    expect(region.scrollTop).toBe(30);
  });

  it.each([
    ["notes", "notes"],
    ["overview", "overview"],
    ["bogus", "overview"],
    [undefined, "overview"],
  ])("restores a snapshot view of %s as %s", async (view, expected) => {
    let handle: VaultNavigationHandle | undefined;
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read()}
        onOpenUrl={vi.fn()}
        onNavigation={vi.fn(() => true)}
        onNavigationReady={(value) => {
          handle = value;
          return () => {};
        }}
        initialNavigation={restoring(
          view ? { view: view as NonNullable<VaultNavigationSnapshot["view"]> } : {},
        )}
      />,
    );
    await debounce();
    expect(screen.queryByRole("region", { name: "Vault overview" }) !== null).toBe(
      expected === "overview",
    );
    expect(screen.queryByRole("navigation", { name: "Notes" }) !== null).toBe(expected === "notes");
    expect(handle?.snapshot().view).toBe(expected);
  });

  it("opens a covered section on a given initialFolder", async () => {
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read()}
        onOpenUrl={vi.fn()}
        initialFolder="30-knowledge"
      />,
    );
    await debounce();
    expect(screen.getAllByRole("heading", { name: "Knowledge" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("list", { name: "Notes in 30-knowledge" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Deny rules hold" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "plain.md" })).toBeNull();
  });

  it("Browse inbox shows All notes on the inbox folder", async () => {
    let handle: VaultNavigationHandle | undefined;
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read()}
        onOpenUrl={vi.fn()}
        onNavigationReady={(value) => {
          handle = value;
          return () => {};
        }}
      />,
    );
    await debounce();
    const browse = screen.getByRole("button", { name: "Browse inbox" });
    browse.focus();
    fireEvent.click(browse);
    expect(pressed("All notes")).toBe("true");
    expect((screen.getByRole("combobox", { name: /Folder/ }) as HTMLSelectElement).value).toBe(
      "00-inbox",
    );
    const notes = within(screen.getByRole("navigation", { name: "Notes" }));
    expect(notes.getByRole("button", { name: "capture.md" })).toBeTruthy();
    expect(notes.queryByRole("button", { name: "plain.md" })).toBeNull();
    expect(handle?.snapshot()).toMatchObject({ view: "notes", folder: "00-inbox" });
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  });

  it("hands captures and onTriage to the overview's inbox", async () => {
    const captures: CapturesApi = {
      list: vi.fn(async () => ({
        captures: [
          {
            ref: "00-inbox/new.md",
            revision: null,
            modified: "2026-10-19T00:00:00Z",
            captured: "2026-10-19T00:00:00Z",
            preview: "a quick note",
            cut: false,
            unread: null,
            suggestion: null,
          },
        ],
        projects: [],
        partial: false,
        omitted: 0,
        run: null,
      })),
      run: vi.fn(async () => null),
      suggest: vi.fn(),
    };
    const onTriage = vi.fn();
    render(
      <VaultBrowser
        workspaceId={W1}
        api={read()}
        onOpenUrl={vi.fn()}
        captures={captures}
        onTriage={onTriage}
      />,
    );
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "Triage inbox" }));
    expect(onTriage).toHaveBeenCalledTimes(1);
    expect(captures.list).toHaveBeenCalledWith(W1, expect.any(AbortSignal));
  });

  it("labels rows, hits, links, backlinks and reader chips from the overview", async () => {
    const api = read({
      entries: vi.fn(async (_id, q) =>
        q
          ? complete({ hits: [{ ref: KNOWLEDGE, excerpt: "deny wins", line: 3 }] })
          : scan([
              { ref: KNOWLEDGE, title: "deny-rules.md" },
              { ref: PLAIN, title: "plain.md" },
            ]),
      ),
      note: vi.fn(async (_id, ref) => ({
        ...note(ref),
        resolved_links: [{ target: "paper", state: "resolved", candidates: [SOURCE] }],
      })),
      backlinks: vi.fn(async () => complete({ backlinks: [{ source: SOURCE, line: 2 }] })),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "All notes" }));
    const notes = within(screen.getByRole("navigation", { name: "Notes" }));
    const row = notes.getByRole("button", { name: "Deny rules hold" });
    expect(row.parentElement?.querySelector(".vault-row-meta")?.textContent).toBe(
      "Knowledge · draft",
    );
    expect(row.parentElement?.querySelector(".vault-row-summary")?.textContent).toBe(
      "Deny rules win over allow rules.",
    );
    const plain = notes.getByRole("button", { name: "plain.md" });
    expect(plain.parentElement?.querySelector(".vault-row-meta")).toBeNull();
    await act(async () => fireEvent.click(row));
    for (const name of ["Links", "Backlinks"])
      expect(
        within(screen.getByRole("region", { name })).getByRole("button", { name: "A paper" }),
      ).toBeTruthy();
    expect(
      within(screen.getByRole("list", { name: "About this note" }))
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual(["Knowledge", "high confidence"]);
    fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
      target: { value: "deny" },
    });
    await debounce();
    expect(screen.queryByRole("group", { name: "Vault view" })).toBeNull();
    expect(
      within(screen.getByRole("navigation", { name: "Notes" })).getByRole("button", {
        name: "Deny rules hold — line 3: deny wins",
      }),
    ).toBeTruthy();
  });

  it("reads the overview again after a save, a create and a note operation", async () => {
    const api = read();
    const writer = writeApi();
    const ops: VaultOpsApi = {
      preview: vi.fn(async () => ({
        steps: [{ n: 1, action: "move" as const, ref: KNOWLEDGE, to: "30-knowledge/moved.md" }],
        creates_folders: [],
        recovery_days: 30,
      })),
      apply: vi.fn(async () => ({
        operation_id: W1,
        status: "done" as const,
        replayed: false,
        steps_applied: 1,
        reason: null,
        created_folders: [],
      })),
      recovery: vi.fn(async () => ({ entries: [], stuck: [] })),
      forgetOperation: vi.fn(),
      resolve: vi.fn(),
    };
    render(
      <VaultBrowser
        workspaceId={W1}
        api={api}
        write={{ ...write(writer), ops }}
        onOpenUrl={vi.fn()}
      />,
    );
    await debounce();
    expect(api.overview).toHaveBeenCalledTimes(1);
    await act(async () => fireEvent.click(recent()));
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Markdown" }), {
      target: { value: "changed" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save note" })));
    await debounce();
    expect(writer.saveNote).toHaveBeenCalledTimes(1);
    expect(api.overview).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "New note" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Note path" }), {
      target: { value: "30-knowledge/new.md" },
    });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Create note" })));
    await debounce();
    expect(writer.createNote).toHaveBeenCalledTimes(1);
    expect(api.overview).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole("button", { name: "Note actions" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Review" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Apply" })));
    await debounce();
    expect(ops.apply).toHaveBeenCalledTimes(1);
    expect(api.overview).toHaveBeenCalledTimes(4);
  });

  it("splits the reader beside the overview even when the list has no rows", async () => {
    const { root, resize } = await sizedVault({
      entries: vi.fn(async () => scan([])),
      overview: vi.fn(async () =>
        overviewOf([overviewRow("a.md", { title: "Alpha", created: "2026-10-18" })]),
      ),
    });
    try {
      resize(908);
      expect(vaultSplit().getAttribute("aria-controls")).toBe(
        root.querySelector(".vault-overview-host")?.id,
      );
    } finally {
      cleanSizedVault();
    }
  });

  it("counts listed files on the section and All notes tabs, with + for a partial listing", async () => {
    const badge = (name: string) =>
      within(screen.getByRole("group", { name: "Vault view" }))
        .getByRole("button", { name })
        .querySelector(".vault-tab-count")?.textContent ?? null;
    const first = render(<VaultBrowser workspaceId={W1} api={read()} onOpenUrl={vi.fn()} />);
    await debounce();
    expect(badge("Knowledge")).toBe("1");
    expect(badge("Sources")).toBe("1");
    expect(badge("Maps")).toBe("0");
    expect(badge("All notes")).toBe("4");
    expect(badge("Front page")).toBeNull();
    first.unmount();

    const partial = read({
      entries: vi.fn(async () => ({
        entries: [
          { ref: KNOWLEDGE, title: "deny-rules.md" },
          { ref: "30-knowledge/second.md", title: "second.md" },
        ],
        partial: true,
        partial_reasons: ["entry_limit"],
        omitted: 0,
      })),
    });
    render(<VaultBrowser workspaceId={W1} api={partial} onOpenUrl={vi.fn()} />);
    await debounce();
    expect(badge("Knowledge")).toBe("2+");
    expect(badge("Sources")).toBe("0+");
    expect(badge("All notes")).toBe("2+");
  });

  it("gives the list the whole width when the reader side has nothing to show", async () => {
    const api = read({
      entries: vi.fn(async (_id: string, query?: string) =>
        query
          ? complete({ hits: [] })
          : scan([
              { ref: KNOWLEDGE, title: "deny-rules.md" },
              { ref: SOURCE, title: "paper.md" },
            ]),
      ),
    });
    const { container } = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(
      within(screen.getByRole("group", { name: "Vault view" })).getByRole("button", {
        name: "All notes",
      }),
    );
    const panes = () => [...(container.querySelector(".vault-panes")?.children ?? [])];
    expect(panes().map((pane) => pane.className)).toEqual(["vault-browse", "vault-placeholder"]);

    fireEvent.change(screen.getByRole("textbox", { name: "Search note contents" }), {
      target: { value: "nothing matches" },
    });
    await debounce();
    expect(screen.getByText("No notes found.")).toBeTruthy();
    expect(panes().map((pane) => pane.className)).toEqual(["vault-browse"]);
    const css = readCss(import.meta.url, "VaultBrowser.css");
    expect(
      ruleIn(narrowBlock(css, "@media (min-width: 800px)"), ".vault-panes > :only-child"),
    ).toMatch(/grid-column:\s*1\s*\/\s*-1;/);
  });

  it("pins the browse column's container and its full width while no note is open", () => {
    const css = readCss(import.meta.url, "VaultBrowser.css");
    expect(ruleIn(css, ".vault-list")).toMatch(/container:\s*vault-browse\s*\/\s*inline-size/);
    const wide = narrowBlock(css, "@media (min-width: 800px)");
    expect(ruleIn(wide, ".vault-browser:not([data-open]) .vault-list.vault-overview-host")).toMatch(
      /grid-column:\s*1\s*\/\s*-1;/,
    );
    // Each pane scrolls on its own inside a page that fills the view's height.
    expect(ruleIn(wide, ".vault-browser")).toMatch(/height:\s*100%/);
    expect(ruleIn(wide, ".vault-panes")).toMatch(/grid-template-rows:\s*minmax\(0, 1fr\)/);
    expect(css).toMatch(/\.vault-row-summary\s*\{[^}]*-webkit-line-clamp:\s*2/);
    expect(ruleIn(css, ".vault-views")).toMatch(/display:\s*flex/);
  });

  it("a tag chip filters All notes", async () => {
    const api = read({
      overview: vi.fn(async () =>
        overviewOf([
          overviewRow(KNOWLEDGE, { title: "Deny rules hold", tags: ["policy"] }),
          overviewRow(SOURCE, { title: "A paper" }),
        ]),
      ),
    });
    render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
    await debounce();
    fireEvent.click(screen.getByRole("button", { name: "#policy (1)" }));
    expect(pressed("All notes")).toBe("true");
    expect((screen.getByRole("combobox", { name: /Folder/ }) as HTMLSelectElement).value).toBe("");
    expect(screen.getByText("Tagged #policy")).toBeTruthy();
    let notes = within(screen.getByRole("navigation", { name: "Notes" }));
    expect(notes.getByRole("button", { name: "Deny rules hold" })).toBeTruthy();
    expect(notes.queryByRole("button", { name: "A paper" })).toBeNull();
    expect(notes.queryByRole("button", { name: "plain.md" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Clear tag filter" }));
    expect(screen.queryByText("Tagged #policy")).toBeNull();
    notes = within(screen.getByRole("navigation", { name: "Notes" }));
    expect(notes.getByRole("button", { name: "A paper" })).toBeTruthy();
  });

  describe("tag-filtered restore across a remount", () => {
    const TAGGED_REFS = paginationRows(40).map((entry) => entry.ref);
    const taggedOverview = (): VaultOverview =>
      overviewOf(TAGGED_REFS.map((ref) => overviewRow(ref, { tags: ["policy"] })));

    it.each([
      ["answers after the listing", false],
      ["answers with the listing", true],
    ])(
      "restores page 2 and focuses the row opener once the overview %s",
      async (_label, settled) => {
        const held = deferred<VaultOverview>();
        const openerRef = TAGGED_REFS[35] as string;
        const api = read({
          entries: vi.fn(async () => scan(TAGGED_REFS.map((ref) => ({ ref, title: ref })))),
          overview: vi.fn(() => (settled ? Promise.resolve(taggedOverview()) : held.promise)),
        });
        const { container } = render(
          <div data-region="view">
            <VaultBrowser
              workspaceId={W1}
              api={api}
              onOpenUrl={vi.fn()}
              onNavigation={vi.fn(() => true)}
              initialNavigation={restoring({
                view: "notes",
                tag: "policy",
                page: 1,
                opener: { kind: "row", ref: openerRef },
              })}
            />
          </div>,
        );
        await debounce();
        if (!settled) await act(async () => held.resolve(taggedOverview()));
        expect(screen.getByText("Tagged #policy")).toBeTruthy();
        expect(screen.getByText("Page 2 of 2")).toBeTruthy();
        const opener = container.querySelector(
          `[data-vault-ref="${openerRef}"][data-vault-kind="row"]`,
        );
        expect(opener).not.toBeNull();
        expect(document.activeElement).toBe(opener);
      },
    );

    it("hides the no-match line while a restored tag waits for the overview, then shows it once answered", async () => {
      const held = deferred<VaultOverview>();
      const api = read({ overview: vi.fn(() => held.promise) });
      render(
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          initialNavigation={restoring({ view: "notes", tag: "policy" })}
        />,
      );
      await debounce();
      expect(screen.getByText("Tagged #policy")).toBeTruthy();
      expect(screen.queryByText("No notes match these filters.")).toBeNull();
      await act(async () => held.resolve(reviewed()));
      expect(screen.getByText("No notes match these filters.")).toBeTruthy();
    });

    it("restores the tag filter on in-place Back", async () => {
      const api = read({
        overview: vi.fn(async () => overviewOf([overviewRow(KNOWLEDGE, { tags: ["policy"] })])),
      });
      render(
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          initialNavigation={restoring({ view: "notes", tag: "policy" })}
        />,
      );
      await debounce();
      expect(screen.getByText("Tagged #policy")).toBeTruthy();
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "deny-rules.md" })));
      fireEvent.click(screen.getByRole("button", { name: "Back to notes" }));
      expect(screen.getByText("Tagged #policy")).toBeTruthy();
      expect(screen.getByRole("button", { name: "deny-rules.md" })).toBeTruthy();
    });

    it("tells the user a tag filter only covers what a partial overview read", async () => {
      const api = read({
        overview: vi.fn(
          async (): Promise<VaultOverview> => ({
            ...overviewOf([overviewRow(KNOWLEDGE, { tags: ["policy"] })]),
            partial: true,
            partial_reasons: ["deadline"],
            omitted: 1,
          }),
        ),
      });
      render(
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          initialNavigation={restoring({ view: "notes", tag: "policy" })}
        />,
      );
      await debounce();
      expect(
        screen.getByText("In notes read from Knowledge, Sources and Maps · Partial"),
      ).toBeTruthy();
    });

    it("clears the tag filter when Overview is chosen again", async () => {
      const api = read({
        overview: vi.fn(async () => overviewOf([overviewRow(KNOWLEDGE, { tags: ["policy"] })])),
      });
      render(
        <VaultBrowser
          workspaceId={W1}
          api={api}
          onOpenUrl={vi.fn()}
          initialNavigation={restoring({ view: "notes", tag: "policy" })}
        />,
      );
      await debounce();
      expect(screen.getByText("Tagged #policy")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Front page" }));
      fireEvent.click(screen.getByRole("button", { name: "All notes" }));
      expect(screen.queryByText("Tagged #policy")).toBeNull();
    });
  });

  describe("overview skeleton and the quiet refresh", () => {
    it("the skeleton is already in the DOM at 0 ms (aria-busy, sr-only words); an overview landing at 100 ms shows at once", async () => {
      const held = deferred<VaultOverview>();
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read({ overview: vi.fn(() => held.promise) })}
          onOpenUrl={vi.fn()}
        />,
      );
      const words = screen.getByText("Reading your notes…");
      expect(words.closest(".vo-skel")?.getAttribute("aria-busy")).toBe("true");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(100);
        held.resolve(reviewed());
      });
      expect(screen.queryByText("Reading your notes…")).toBeNull();
      expect(recent()).toBeTruthy();
    });

    it("an overview landing at 350 ms: the skeleton holds through 600 ms, the overview replaces it at 700 ms", async () => {
      const held = deferred<VaultOverview>();
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read({ overview: vi.fn(() => held.promise) })}
          onOpenUrl={vi.fn()}
        />,
      );

      await act(async () => {
        await vi.advanceTimersByTimeAsync(350);
        held.resolve(reviewed());
      });
      expect(screen.getByText("Reading your notes…")).toBeTruthy(); // resolved, but still held

      await act(async () => {
        await vi.advanceTimersByTimeAsync(250); // t=600
      });
      // The assertion a naive `overviewPhase === "loading" && overviewData === null` (no
      // useHeld) would already fail here: data landed at 350 ms, so that expression is false
      // and the skeleton would already be gone, well before the 700 ms release.
      expect(screen.getByText("Reading your notes…")).toBeTruthy();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(100); // t=700
      });
      expect(screen.queryByText("Reading your notes…")).toBeNull();
      expect(recent()).toBeTruthy();
    });

    it("a remount shows the remembered overview on its first render and refreshes it with one request", async () => {
      const api = read();
      const first = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      expect(recent()).toBeTruthy();
      expect(api.overview).toHaveBeenCalledTimes(1);
      first.unmount();

      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      // First render, synchronously: the remembered overview is already there, no skeleton.
      expect(recent()).toBeTruthy();
      expect(screen.queryByText("Reading your notes…")).toBeNull();
      expect(screen.getByText("Updating the overview…")).toBeTruthy();

      await debounce(); // the second hook instance's own mount run (setTimeout 0), then it settles
      expect(api.overview).toHaveBeenCalledTimes(2);
      expect(screen.queryByText("Updating the overview…")).toBeNull();
      expect(recent()).toBeTruthy();
    });

    it("a failed refresh keeps the overview's rows, shows the out-of-date line, and Try again re-reads", async () => {
      const overview = vi
        .fn<NonNullable<BrowserApi["overview"]>>()
        .mockResolvedValueOnce({ ...reviewed(), partial: true, omitted: 1 })
        .mockRejectedValueOnce(new Error("unavailable"))
        .mockResolvedValue(reviewed());
      render(<VaultBrowser workspaceId={W1} api={read({ overview })} onOpenUrl={vi.fn()} />);
      await debounce();
      expect(recent()).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Reload front page" }));
      await debounce();
      expect(recent()).toBeTruthy(); // the stale rows stay up through the failed refresh
      expect(
        screen.getByText("This overview may be out of date: it couldn't be read again."),
      ).toBeTruthy();

      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
      await debounce();
      expect(overview).toHaveBeenCalledTimes(3);
      expect(recent()).toBeTruthy();
      expect(
        screen.queryByText("This overview may be out of date: it couldn't be read again."),
      ).toBeNull();
    });

    it("a restored 'recent' opener gets focus while a background refresh runs (remembered overview)", async () => {
      const held = deferred<VaultOverview>();
      // The first call (the first mount below) resolves at once, so the remount has a remembered
      // overview to recall; the second call (the remount's own quiet refresh) stays pending, so
      // the assertions below land while that refresh is still running.
      const overview = vi
        .fn<NonNullable<BrowserApi["overview"]>>()
        .mockResolvedValueOnce(reviewed())
        .mockImplementation(() => held.promise);
      const api = read({ overview });
      const first = render(
        <div data-region="view">
          <VaultBrowser
            workspaceId={W1}
            api={api}
            onOpenUrl={vi.fn()}
            onNavigation={vi.fn(() => true)}
          />
        </div>,
      );
      await debounce();
      expect(recent()).toBeTruthy();
      first.unmount();

      const { container } = render(
        <div data-region="view">
          <VaultBrowser
            workspaceId={W1}
            api={api}
            onOpenUrl={vi.fn()}
            onNavigation={vi.fn(() => true)}
            initialNavigation={restoring({
              view: "overview",
              opener: { kind: "lead", ref: KNOWLEDGE },
              scroll: { top: 40, left: 0 },
            })}
          />
        </div>,
      );
      await debounce(); // settles the (unrelated) list and bookmark reads; the overview stays held
      expect(screen.getByText("Updating the overview…")).toBeTruthy();
      expect(document.activeElement).toBe(
        container.querySelector(`[data-vault-ref="${KNOWLEDGE}"][data-vault-kind="lead"]`),
      );
      const region = container.firstElementChild as HTMLElement;
      expect(region.scrollTop).toBe(40);
    });

    // "Updating the overview…" is itself a held, mount/unmount-in-place indicator (same
    // kit rule as the skeleton), floated out of flow so the remembered overview under it never
    // shifts when the indicator unmounts.
    it("a reopen's refresh answered at 600 ms keeps 'Updating the overview…' until 700 ms, then removes it", async () => {
      const held = deferred<VaultOverview>();
      const overview = vi
        .fn<NonNullable<BrowserApi["overview"]>>()
        .mockResolvedValueOnce(reviewed())
        .mockImplementation(() => held.promise);
      const api = read({ overview });
      const first = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      first.unmount();

      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      expect(screen.getByText("Updating the overview…")).toBeTruthy();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(600);
        held.resolve(reviewed());
      });
      expect(screen.getByText("Updating the overview…")).toBeTruthy(); // resolved, still held

      await act(async () => {
        await vi.advanceTimersByTimeAsync(99); // t=699
      });
      expect(screen.getByText("Updating the overview…")).toBeTruthy();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1); // t=700
      });
      expect(screen.queryByText("Updating the overview…")).toBeNull();
    });

    // A refresh that fails inside the hold window must not show "Updating the
    // overview…" at the same time as the out-of-date line. `overviewRefreshing` alone stays true
    // until t=700 (as above), but the error line appears the instant the rejection lands, so the
    // updating line must drop then too, not wait for t=700.
    it("a reopen's refresh that fails at 360 ms drops 'Updating the overview…' as soon as the out-of-date line shows", async () => {
      const held = deferred<VaultOverview>();
      const overview = vi
        .fn<NonNullable<BrowserApi["overview"]>>()
        .mockResolvedValueOnce(reviewed())
        .mockImplementation(() => held.promise);
      const api = read({ overview });
      const first = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      first.unmount();

      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      expect(screen.getByText("Updating the overview…")).toBeTruthy();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(360); // inside the 300-700 hold window
        held.reject(new Error("unavailable"));
        await held.promise.catch(() => {});
      });
      expect(
        screen.getByText("This overview may be out of date: it couldn't be read again."),
      ).toBeTruthy();
      expect(screen.queryByText("Updating the overview…")).toBeNull();
    });

    it("a refresh answered at 200 ms removes 'Updating the overview…' at once", async () => {
      const held = deferred<VaultOverview>();
      const overview = vi
        .fn<NonNullable<BrowserApi["overview"]>>()
        .mockResolvedValueOnce(reviewed())
        .mockImplementation(() => held.promise);
      const api = read({ overview });
      const first = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      first.unmount();

      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      expect(screen.getByText("Updating the overview…")).toBeTruthy();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(200);
        held.resolve(reviewed());
      });
      expect(screen.queryByText("Updating the overview…")).toBeNull();
    });

    it("routes owned listing rows through a named section without reading notes", async () => {
      const api = read();
      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();

      const nav = screen.getByRole("group", { name: "Vault view" });
      expect(
        within(nav).getByRole("button", { name: "Knowledge" }).getAttribute("aria-current"),
      ).toBeNull();
      fireEvent.click(within(nav).getByRole("button", { name: "Knowledge" }));

      expect(screen.getAllByRole("heading", { name: "Knowledge" }).length).toBeGreaterThan(0);
      expect(
        within(nav).getByRole("button", { name: "Knowledge" }).getAttribute("aria-current"),
      ).toBe("page");
      expect(screen.getByRole("list", { name: "Notes in 30-knowledge" })).toBeTruthy();
      expect(api.note).not.toHaveBeenCalled();
      expect(api.entries).toHaveBeenCalledTimes(1);
      expect(api.overview).toHaveBeenCalledTimes(1);
    });

    it("shows Everything else for known groups in a partial listing", async () => {
      const api = read({
        entries: vi.fn(async () => ({
          entries: [
            { ref: KNOWLEDGE, title: "deny-rules.md" },
            { ref: "garden/ideas.md", title: "ideas.md" },
          ],
          partial: true,
          partial_reasons: ["deadline"],
          omitted: 1,
        })),
      });
      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      fireEvent.click(screen.getByRole("button", { name: "Everything else" }));

      expect(screen.getByRole("heading", { name: "Everything else" })).toBeTruthy();
      expect(screen.getByText("garden/", { selector: ".vsp-ref" })).toBeTruthy();
    });

    it("falls back from an empty saved Everything else route to the front page", async () => {
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read({
            entries: vi.fn(async () => scan([{ ref: KNOWLEDGE, title: "deny-rules.md" }])),
          })}
          onOpenUrl={vi.fn()}
          initialNavigation={restoring({ view: "section", section: "else" })}
        />,
      );
      await debounce();

      expect(screen.getByRole("region", { name: "Vault overview" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Everything else" })).toBeNull();
      expect(screen.getByRole("button", { name: "Front page" }).getAttribute("aria-current")).toBe(
        "page",
      );
    });

    it("keeps the focused Folder select mounted when its initial section becomes available", async () => {
      const held = deferred<VaultOverview>();
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read({ overview: vi.fn(() => held.promise) })}
          onOpenUrl={vi.fn()}
          initialFolder="30-knowledge"
        />,
      );
      await debounce();
      const folderSelect = screen.getByRole("combobox", { name: /Folder/ });
      folderSelect.focus();
      await act(async () => held.resolve(reviewed()));

      expect(folderSelect.isConnected).toBe(true);
      expect(document.activeElement).toBe(folderSelect);
    });

    it("keeps a deliberate Folder choice in All notes after a late overview and blur", async () => {
      const held = deferred<VaultOverview>();
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read({ overview: vi.fn(() => held.promise) })}
          onOpenUrl={vi.fn()}
          initialFolder="30-knowledge"
        />,
      );
      await debounce();
      const folderSelect = screen.getByRole("combobox", { name: /Folder/ });
      folderSelect.focus();
      fireEvent.change(folderSelect, { target: { value: "40-sources" } });
      expect((folderSelect as HTMLSelectElement).value).toBe("40-sources");

      await act(async () => held.resolve(reviewed()));
      expect(folderSelect.isConnected).toBe(true);
      expect(document.activeElement).toBe(folderSelect);
      await act(async () => fireEvent.blur(folderSelect));

      expect(folderSelect.isConnected).toBe(true);
      expect((folderSelect as HTMLSelectElement).value).toBe("40-sources");
      expect(screen.getByRole("button", { name: "All notes" }).getAttribute("aria-pressed")).toBe(
        "true",
      );
      expect(screen.getByRole("navigation", { name: "Notes" })).toBeTruthy();
    });

    it("keeps a deliberate note page in All notes after a late overview", async () => {
      const held = deferred<VaultOverview>();
      const entries = Array.from({ length: 40 }, (_, index) => ({
        ref: `30-knowledge/${String(index).padStart(2, "0")}.md`,
        title: `Note ${index}`,
      }));
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read({
            entries: vi.fn(async () => scan(entries)),
            overview: vi.fn(() => held.promise),
          })}
          onOpenUrl={vi.fn()}
          initialFolder="30-knowledge"
        />,
      );
      await debounce();
      expect(screen.getByRole("button", { name: "Next note page" }).hasAttribute("disabled")).toBe(
        false,
      );

      fireEvent.click(screen.getByRole("button", { name: "Next note page" }));
      expect(screen.getByText("30-knowledge/32.md")).toBeTruthy();
      await act(async () => held.resolve(reviewed()));

      expect(screen.getByRole("button", { name: "All notes" }).getAttribute("aria-pressed")).toBe(
        "true",
      );
      expect(screen.getByRole("navigation", { name: "Notes" })).toBeTruthy();
      expect(screen.getByText("30-knowledge/32.md")).toBeTruthy();
    });

    it("hides front-page cards from the old workspace until its own overview arrives", async () => {
      const first = deferred<VaultOverview>();
      const second = deferred<VaultOverview>();
      const api = read({
        overview: vi.fn((workspaceId: string) =>
          workspaceId === W1 ? first.promise : second.promise,
        ),
        entries: vi.fn(async (workspaceId: string) =>
          scan([
            {
              ref: `${workspaceId === W1 ? "30-knowledge" : "40-sources"}/note.md`,
              title: workspaceId === W1 ? "Old workspace note" : "New workspace note",
            },
          ]),
        ),
      });
      const mounted = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      const oldRef = "30-knowledge/note.md";
      await act(async () =>
        first.resolve(
          overviewOf([
            overviewRow(oldRef, {
              title: "Old workspace card",
              summary: "Old workspace summary.",
              modified: "2026-10-19",
            }),
          ]),
        ),
      );
      expect(screen.getByRole("button", { name: "Old workspace card" })).toBeTruthy();

      mounted.rerender(<VaultBrowser workspaceId={W2} api={api} onOpenUrl={vi.fn()} />);
      expect(screen.queryByRole("button", { name: "Old workspace card" })).toBeNull();
      await debounce();
      await act(async () =>
        second.resolve(
          overviewOf([
            overviewRow("40-sources/note.md", {
              title: "New workspace card",
              summary: "New workspace summary.",
              modified: "2026-10-19",
            }),
          ]),
        ),
      );
      expect(screen.getByRole("button", { name: "New workspace card" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Old workspace card" })).toBeNull();
    });

    it("restores focus to the exact bookmarked opener when the same ref is Latest", async () => {
      const api = read({
        bookmarks: vi.fn(async () => ({
          bookmarks: [{ ref: SOURCE, created_at: CREATED_AT }],
        })),
      });
      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      const latest = document.querySelector(
        `[data-vault-ref="${SOURCE}"][data-vault-kind="recent"]`,
      ) as HTMLButtonElement | null;
      const bookmark = document.querySelector(
        `[data-vault-ref="${SOURCE}"][data-vault-kind="bookmark"]`,
      ) as HTMLButtonElement | null;
      expect(latest).not.toBeNull();
      expect(bookmark).not.toBeNull();
      await act(async () => fireEvent.click(bookmark as HTMLButtonElement));
      await act(async () => fireEvent.click(screen.getByRole("button", { name: "Back to notes" })));

      expect(document.activeElement).toBe(
        document.querySelector(`[data-vault-ref="${SOURCE}"][data-vault-kind="bookmark"]`),
      );
      expect(api.note).toHaveBeenCalledExactlyOnceWith(W1, SOURCE, expect.any(AbortSignal));
    });

    it("'.vault-updating' is taken out of flow, over the overview, the same way the kit floats a held indicator", () => {
      const css = readCss(import.meta.url, "VaultBrowser.css");
      expect(ruleIn(css, ".vault-overview-host")).toMatch(/position:\s*relative/);
      expect(ruleIn(css, ".vault-updating")).toMatch(/position:\s*absolute/);
    });

    // The out-of-date line's own button unmounts (with it) the instant the retried
    // refresh starts, same as the older no-data "Try again" a few hundred lines up; it needs
    // the same focus hand-off or focus falls to the body.
    it("the out-of-date line's Try again moves focus to the Vault heading, not the body", async () => {
      const overview = vi
        .fn<NonNullable<BrowserApi["overview"]>>()
        .mockResolvedValueOnce(reviewed())
        .mockRejectedValueOnce(new Error("unavailable"))
        .mockResolvedValue(reviewed());
      const api = read({ overview });
      const first = render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      expect(recent()).toBeTruthy();
      first.unmount();

      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce(); // the remount's own quiet refresh, this time rejected
      expect(recent()).toBeTruthy(); // the stale rows stay up
      const retry = screen.getByRole("button", { name: "Try again" });
      retry.focus();
      fireEvent.click(retry);
      expect(retry.isConnected).toBe(false);
      expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
    });
  });

  // The listing's rows are empty on every reload and after a failed read. A section page must
  // not read that as an empty folder.
  describe("section pages over a listing that has not been read", () => {
    const sectionPage = () => document.querySelector(".vsp") as HTMLElement;
    const openKnowledge = () =>
      fireEvent.click(
        within(screen.getByRole("group", { name: "Vault view" })).getByRole("button", {
          name: "Knowledge",
        }),
      );
    const expectNoFolderClaim = () => {
      expect(sectionPage().querySelector(".vsp-count")).toBeNull();
      expect(within(sectionPage()).queryByText(/^\d+ notes?$/)).toBeNull();
      expect(sectionPage().querySelector(".vsp-empty")).toBeNull();
      expect(within(sectionPage()).queryByText(/No notes/)).toBeNull();
    };

    it("says the listing could not be read, where the overview was", async () => {
      const api = read({
        entries: vi.fn(async () => {
          throw new Error("overloaded");
        }),
      });
      render(<VaultBrowser workspaceId={W1} api={api} onOpenUrl={vi.fn()} />);
      await debounce();
      expect(recent()).toBeTruthy();
      openKnowledge();

      expect(within(sectionPage()).getByRole("heading", { level: 2 }).textContent).toBe(
        "Knowledge",
      );
      // The overview's count sits in the reader pane beside the list, not in the list.
      expect(screen.getByRole("heading", { name: "1 knowledge notes" })).toBeTruthy();
      expect(within(sectionPage()).getByRole("alert").textContent).toBe(
        "The vault is busy. Wait a moment and try again.",
      );
      expectNoFolderClaim();
    });

    it("waits for the listing on a restored Knowledge page, then counts it", async () => {
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read()}
          onOpenUrl={vi.fn()}
          initialNavigation={restoring({ view: "section", section: "knowledge" })}
        />,
      );
      expect(within(sectionPage()).getByText("Loading…")).toBeTruthy();
      expectNoFolderClaim();

      await debounce();
      expect(within(sectionPage()).queryByText("Loading…")).toBeNull();
      expect(within(sectionPage()).getByText("1 note")).toBeTruthy();
      expect(screen.getByRole("list", { name: "Notes in 30-knowledge" })).toBeTruthy();
    });

    it("waits for the listing on a restored Everything else page", async () => {
      render(
        <VaultBrowser
          workspaceId={W1}
          api={read()}
          onOpenUrl={vi.fn()}
          initialNavigation={restoring({ view: "section", section: "else" })}
        />,
      );
      expect(within(sectionPage()).getByText("Loading…")).toBeTruthy();
      expect(within(sectionPage()).queryByText("No other folders in this listing.")).toBeNull();

      await debounce();
      expect(within(sectionPage()).queryByText("Loading…")).toBeNull();
      expect(within(sectionPage()).getByText("notes/", { selector: ".vsp-ref" })).toBeTruthy();
    });

    it("waits for the listing again after a search is cleared", async () => {
      render(<VaultBrowser workspaceId={W1} api={read()} onOpenUrl={vi.fn()} />);
      await debounce();
      openKnowledge();
      expect(within(sectionPage()).getByText("1 note")).toBeTruthy();
      const search = screen.getByRole("textbox", { name: "Search note contents" });
      fireEvent.change(search, { target: { value: "deny" } });
      await debounce();
      fireEvent.change(search, { target: { value: "" } });

      expect(within(sectionPage()).getByText("Loading…")).toBeTruthy();
      expectNoFolderClaim();
      await debounce();
      expect(within(sectionPage()).getByText("1 note")).toBeTruthy();
    });
  });
});
