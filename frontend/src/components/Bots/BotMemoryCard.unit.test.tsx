import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { BotRow } from "../../clients/messaging/botsApi";
import { t, tf } from "../../i18n/t";
import type { VaultApi } from "../../state/vaultApi";
import { BotMemoryCard } from "./BotMemoryCard";

afterEach(cleanup);

const workspaceId = "11111111-1111-4111-8111-111111111111";
const bot = {
  bot_id: "22222222-2222-4222-8222-222222222222",
  profile: { identity: { name: "Research Bot" } },
} as BotRow;

function memoryApi() {
  return {
    entries: vi.fn(async () => ({
      entries: [
        { ref: `bot-memory/${bot.bot_id}/MEMORY.md`, title: "MEMORY.md", modified: 1 },
        ...Array.from({ length: 5 }, (_, index) => ({
          ref: `bot-memory/${bot.bot_id}/topic-${index}.md`,
          title: `Topic ${index}`,
          modified: 10 - index,
        })),
      ],
      partial: false,
      partial_reasons: [],
      omitted: 0,
    })),
    note: vi.fn(async () => ({ text: "memory text" })),
  } as unknown as Pick<VaultApi, "entries" | "note" | "metadata">;
}

it("shows the exact source, five memory refs, remaining count, and full memory actions", async () => {
  const api = memoryApi();
  const view = render(
    <BotMemoryCard
      api={api}
      workspaceId={workspaceId}
      bot={bot}
      disabled={false}
      onOpenNote={vi.fn()}
      onOpenUrl={vi.fn()}
      onReview={vi.fn()}
      onForget={vi.fn()}
      onAskInMainChat={vi.fn(async () => {})}
    />,
  );

  await act(async () => {});
  expect(api.entries).toHaveBeenCalledWith(workspaceId, undefined, expect.any(AbortSignal));
  expect(
    screen.getByText(tf("bots.memory.source", { ref: `bot-memory/${bot.bot_id}` })),
  ).toBeTruthy();
  expect(screen.getByText(`bot-memory/${bot.bot_id}/MEMORY.md`)).toBeTruthy();
  expect(
    screen.getByText("bot-memory/22222222-2222-4222-8222-222222222222/topic-0.md"),
  ).toBeTruthy();
  expect(
    screen.queryByText("bot-memory/22222222-2222-4222-8222-222222222222/topic-4.md"),
  ).toBeNull();
  expect(screen.getByText(tf("bots.memory.remaining", { count: 1 }))).toBeTruthy();
  expect(screen.getByRole("button", { name: "View memory" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "How Bot memory works" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Ask Research Bot to change…" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Ask to review" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Ask to forget all…" })).toBeTruthy();
  view.unmount();
});

it("keeps the shared request lock until the main-chat callback finishes", async () => {
  let finish!: () => void;
  const onAskInMainChat = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
  render(
    <BotMemoryCard
      api={memoryApi()}
      workspaceId={workspaceId}
      bot={bot}
      disabled={false}
      onOpenNote={vi.fn()}
      onOpenUrl={vi.fn()}
      onReview={vi.fn(async () => {})}
      onForget={vi.fn(async () => {})}
      onAskInMainChat={onAskInMainChat}
    />,
  );
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Ask Research Bot to change…" }));
  expect(
    (screen.getByRole("button", { name: "Ask to review" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Ask to review" }));
  expect(onAskInMainChat).toHaveBeenCalledTimes(1);
  expect(onAskInMainChat).toHaveBeenCalledWith(bot);
  await act(async () => finish());
  expect(
    (screen.getByRole("button", { name: "Ask to review" }) as HTMLButtonElement).disabled,
  ).toBe(false);
});

it("asks to open the Bot's vault, without reading or offering requests, while the memory API is withheld", () => {
  const api = memoryApi();
  render(
    <BotMemoryCard
      workspaceId={workspaceId}
      bot={bot}
      disabled={false}
      onOpenNote={vi.fn()}
      onOpenUrl={vi.fn()}
      onReview={vi.fn()}
      onForget={vi.fn()}
    />,
  );
  expect(screen.getByText("Open this Bot's vault to view its memory.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Ask to review" })).toBeNull();
  expect(api.entries).not.toHaveBeenCalled();
});

it.each([
  { active: false, shown: "Open this Bot's vault to view its memory.", hidden: "can't be shown" },
  { active: true, shown: "Bot memory can't be shown right now.", hidden: "Open this Bot's vault" },
])("explains a withheld memory API by whether the Bot's vault is active (%o)", (c) => {
  render(
    <BotMemoryCard
      workspaceId={workspaceId}
      bot={bot}
      vaultActive={c.active}
      disabled={false}
      onOpenNote={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  expect(screen.getByText(c.shown)).toBeTruthy();
  expect(screen.queryByText(new RegExp(c.hidden))).toBeNull();
});

it("hides the main-chat request when the App does not provide its callback", async () => {
  render(
    <BotMemoryCard
      api={memoryApi()}
      workspaceId={workspaceId}
      bot={bot}
      disabled={false}
      onOpenNote={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  await act(async () => {});
  expect(screen.queryByRole("button", { name: "Ask Research Bot to change…" })).toBeNull();
});

it("closes the memory dialog when its API is removed and does not reopen on return", async () => {
  const api = memoryApi();
  const props = {
    workspaceId,
    bot,
    disabled: false,
    onOpenNote: vi.fn(),
    onOpenUrl: vi.fn(),
  };
  const view = render(<BotMemoryCard {...props} api={api} />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "View memory" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
  const readsBeforeReturn = vi.mocked(api.entries).mock.calls.length;
  view.rerender(<BotMemoryCard {...props} />);
  expect(screen.queryByRole("dialog")).toBeNull();
  view.rerender(<BotMemoryCard {...props} api={api} />);
  await act(async () => {});
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.entries).toHaveBeenCalledTimes(readsBeforeReturn + 1);
});

it.each([
  { partial: true, omitted: 1 },
  { partial: false, omitted: 1 },
] as const)("does not claim an incomplete zero-row scan is empty (%o)", async (scan) => {
  const api = {
    entries: vi.fn().mockResolvedValue({
      entries: [],
      partial_reasons: [],
      ...scan,
    }),
  } as unknown as Pick<VaultApi, "entries" | "note" | "metadata">;
  render(
    <BotMemoryCard
      api={api}
      workspaceId={workspaceId}
      bot={bot}
      disabled={false}
      onOpenNote={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  await act(async () => {});
  expect(screen.queryByText(EMPTY)).toBeNull();
  expect(screen.getByRole("status")).toBeTruthy();
});

const EMPTY = "Research Bot has no auto memory notes.";
const OWN = "Notes Claude Code saved for Research Bot with auto memory. Bot chats don't read them.";
const SOURCE = `Source bot-memory/${bot.bot_id}/`;
const props = { workspaceId, bot, disabled: false, onOpenNote: vi.fn(), onOpenUrl: vi.fn() };

it("names the Bot's own folder in every state", async () => {
  let finish!: (value: Record<string, unknown>) => void;
  const api = memoryApi();
  vi.mocked(api.entries).mockImplementationOnce(() => new Promise((resolve) => (finish = resolve)));
  const view = render(<BotMemoryCard {...props} api={api} />);
  const shown = () => {
    expect(screen.getByRole("heading", { name: "Bot memory" })).toBeTruthy();
    expect(screen.getByText(OWN)).toBeTruthy();
    expect(screen.getByText(SOURCE)).toBeTruthy();
  };
  shown(); // loading
  await act(async () => finish({ entries: [], partial: false, partial_reasons: [], omitted: 0 }));
  expect(screen.getByText(EMPTY)).toBeTruthy(); // empty
  shown();
  view.rerender(<BotMemoryCard {...props} />); // vault not open
  expect(screen.getByText("Open this Bot's vault to view its memory.")).toBeTruthy();
  shown();
  view.rerender(<BotMemoryCard {...props} api={rejecting()} />); // read failed
  await act(async () => {});
  expect(screen.getByRole("alert").textContent).toBe("Could not read Bot memory.");
  shown();
});

function rejecting() {
  return {
    entries: vi.fn().mockRejectedValue(new Error("offline")),
    note: vi.fn(),
  } as unknown as Pick<VaultApi, "entries" | "note" | "metadata">;
}

it("retries a failed memory read", async () => {
  const api = memoryApi();
  vi.mocked(api.entries).mockRejectedValueOnce(new Error("offline"));
  render(<BotMemoryCard {...props} api={api} />);
  await act(async () => {});
  expect(screen.getByRole("alert").textContent).toBe("Could not read Bot memory.");
  expect(screen.queryByRole("button", { name: "View memory" })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await act(async () => {});
  expect(api.entries).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText(`bot-memory/${bot.bot_id}/MEMORY.md`)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
});

it("explains an empty folder with the Bot's name", async () => {
  const api = {
    entries: vi.fn(async () => ({ entries: [], partial: false, partial_reasons: [], omitted: 0 })),
  } as unknown as Pick<VaultApi, "entries" | "note" | "metadata">;
  render(<BotMemoryCard {...props} api={api} />);
  await act(async () => {});
  expect(screen.getByText(EMPTY)).toBeTruthy();
});

it("offers no Ask to set up memory… button", async () => {
  render(
    <BotMemoryCard
      {...props}
      api={memoryApi()}
      onReview={vi.fn(async () => {})}
      onForget={vi.fn(async () => {})}
      onAskInMainChat={vi.fn()}
    />,
  );
  await act(async () => {});
  expect(screen.queryByRole("button", { name: /set up memory/i })).toBeNull();
});

it("gives the viewer its VS Code opener and its drafted-request editor when the owner provides them", async () => {
  const api = memoryApi();
  const ref = `bot-memory/${bot.bot_id}/MEMORY.md`;
  vi.mocked(api.note).mockResolvedValue({
    note: {
      ref,
      title: "MEMORY.md",
      text: "- tea\n",
      source: "- tea\n",
      byte_size: 6,
      sha256: "b".repeat(64),
      frontmatter: {},
      frontmatter_error: null,
    },
    resolved_links: [],
  } as never);
  const onOpenInEditor = vi.fn();
  render(
    <BotMemoryCard
      {...props}
      api={api}
      onOpenInEditor={onOpenInEditor}
      onDraftReplacement={vi.fn(async () => true)}
    />,
  );
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "View memory" }));
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Open in VS Code" }));
  expect(onOpenInEditor).toHaveBeenCalledExactlyOnceWith(ref);
  expect(screen.getByRole("button", { name: "Edit" })).toBeTruthy();
});

it("explains that Ask opens an unsent draft in the main chat", async () => {
  render(
    <BotMemoryCard
      api={memoryApi()}
      workspaceId={workspaceId}
      bot={bot}
      disabled={false}
      onOpenNote={vi.fn()}
      onOpenUrl={vi.fn()}
      onAskInMainChat={vi.fn(async () => {})}
    />,
  );
  await act(async () => {});
  const ask = screen.getByRole("button", { name: "Ask Research Bot to change…" });
  const hint = screen.getByText("This opens an unsent draft in the Bot’s main chat.");
  expect(hint.id).toBe(ask.getAttribute("aria-describedby"));
});

it.each([
  ["unsupported", "unsupported_metadata", "Some title metadata could not be read."],
  ["changed", "changed", "This file changed while its title was being read."],
] as const)("shows the %s detail text on a non-ready row", async (state, reason, text) => {
  const ref = `bot-memory/${bot.bot_id}/topic.md`;
  const api = {
    entries: vi.fn(async () => ({
      entries: [{ ref, title: "Topic", modified: 1 }],
      partial: false,
      partial_reasons: [],
      omitted: 0,
    })),
    metadata: vi.fn(async () => ({
      items: [
        {
          ref,
          state,
          reason,
          read_bytes: 0,
          display_metadata:
            state === "changed"
              ? null
              : {
                  state,
                  frontmatter_state: state,
                  heading_state: state,
                  display_title: null,
                  title_source: null,
                  tags: null,
                  type: null,
                  status: null,
                },
        },
      ],
      partial: true,
      partial_reasons: [state],
      read_bytes: 0,
      omitted: 0,
    })),
  } as unknown as Pick<VaultApi, "entries" | "note" | "metadata">;
  render(
    <BotMemoryCard
      api={api}
      workspaceId={workspaceId}
      bot={bot}
      disabled={false}
      onOpenNote={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  await act(async () => {});
  const row = screen.getByText(ref).closest("li") as HTMLElement;
  expect(row.querySelector("small")?.textContent).toBe(text);
});

it("shows app-kept memory: kept line, no vault source, no Open in Vault, folder-based Edit", async () => {
  const folder = "/data/bot-memory/11111111-1111-4111-8111-111111111111";
  const ref = `${bot.bot_id}/MEMORY.md`;
  const api = {
    entries: vi.fn(async () => ({
      entries: [{ ref, title: "MEMORY.md", modified: 1 }],
      partial: false,
      partial_reasons: [],
      omitted: 0,
      folder,
    })),
    note: vi.fn(async () => ({
      note: {
        ref,
        title: "MEMORY.md",
        text: "- tea\n",
        source: "- tea\n",
        byte_size: 6,
        sha256: "b".repeat(64),
        frontmatter: {},
        frontmatter_error: null,
      },
      resolved_links: [],
    })),
  } as unknown as Pick<VaultApi, "entries" | "note" | "metadata">;
  const onDraftReplacement = vi.fn(async (_request: string) => true);
  render(<BotMemoryCard {...props} api={api} kept="app" onDraftReplacement={onDraftReplacement} />);
  await act(async () => {});
  expect(screen.getByText(t("bots.memory.keptApp"))).toBeTruthy();
  expect(screen.queryByText(SOURCE)).toBeNull();
  expect(screen.getByText(ref)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "View memory" }));
  await act(async () => {});
  expect(screen.getByText(tf("bots.memory.sourceApp", { folder }))).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open in Vault" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByRole("textbox", { name: "New text of this file" }), {
    target: { value: "- coffee\n" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await act(async () => {});
  expect(onDraftReplacement.mock.calls[0]?.[0]).toContain(`${folder}/${ref}`);
});
