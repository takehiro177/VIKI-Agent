import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { t, tf } from "../../i18n/t";
import {
  KanbanNeedsYou,
  type KanbanNeedsYouProps,
  type NeedsYouItem,
  updatedText,
} from "./KanbanNeedsYou";
import type { CardBody, CardOption, CardRead } from "./kanbanBody";
import type { KanbanCard, KanbanNotice } from "./kanbanCards";

afterEach(cleanup);

const body = (over: Partial<CardBody> = {}): CardBody => ({
  what: null,
  now: null,
  needs: null,
  done: null,
  verdict: null,
  ...over,
});
const card = (over: Partial<KanbanCard> = {}): KanbanCard => ({
  ref: "kanban/a.md",
  title: "A card",
  owners: [],
  status: "blocked",
  ageDays: null,
  stale: false,
  unread: null,
  ...over,
});
const read = (over: Partial<CardBody> = {}): CardRead => ({
  source: "",
  sha256: "a".repeat(64),
  body: body(over),
});
const item = (over: Partial<NeedsYouItem> = {}): NeedsYouItem => ({
  card: card(),
  read: read(),
  ownerTags: null,
  botName: null,
  ...over,
});
const option = (over: Partial<CardOption> = {}): CardOption => ({
  letter: "A",
  label: "One",
  recommended: false,
  effect: null,
  ...over,
});

function setup(over: Partial<KanbanNeedsYouProps> = {}) {
  const onAnswer = vi.fn();
  const onUndo = vi.fn();
  const onOpenNote = vi.fn();
  const props: KanbanNeedsYouProps = {
    items: [],
    counts: null,
    reading: false,
    canWrite: true,
    saving: false,
    lockedRef: null,
    notice: null,
    onAnswer,
    onUndo,
    onOpenNote,
    ...over,
  };
  const view = render(<KanbanNeedsYou {...props} />);
  return { ...view, onAnswer, onUndo, onOpenNote, props };
}

describe("updatedText", () => {
  it("words today, yesterday and N days ago", () => {
    expect(updatedText(0)).toBe(t("kanban.card.updated_today"));
    expect(updatedText(1)).toBe(t("kanban.card.updated_yesterday"));
    expect(updatedText(4)).toBe(tf("kanban.card.updated_days", { days: 4 }));
  });
});

describe("KanbanNeedsYou", () => {
  it("renders nothing with no items and no notice", () => {
    const { container } = setup();
    expect(container.firstChild).toBeNull();
  });

  it("titles the section with the count and summary when counts are known", () => {
    setup({ items: [item()], counts: { blocked: 1, review: 2 } });
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe(
      `${tf("kanban.needs.title_count", { count: 3 })} ${tf("kanban.needs.summary", { blocked: 1, review: 2 })}`,
    );
  });

  it("falls back to the plain title with no summary when counts are unknown", () => {
    setup({ items: [item()], counts: null });
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe(t("kanban.needs.title"));
  });

  it("opens the card's note", () => {
    const { onOpenNote } = setup({ items: [item({ card: card({ ref: "kanban/x.md" }) })] });
    fireEvent.click(screen.getByRole("button", { name: t("kanban.needs.open") }));
    expect(onOpenNote).toHaveBeenCalledWith("kanban/x.md");
  });

  it("shows the lane chip, owner tags, Updated, the done count and the checker verdict", () => {
    const { container } = setup({
      items: [
        item({
          card: card({ status: "review", ageDays: 1 }),
          read: read({ done: { done: 3, total: 5 }, verdict: "FAIL" }),
          ownerTags: <span data-testid="owner">Engineer</span>,
        }),
      ],
    });
    const meta = container.querySelector(".kanban-needs-meta") as HTMLElement;
    expect(meta.querySelector('.kanban-chip[data-stage="review"]')).toBeTruthy();
    expect(within(meta).getByText(t("kanban.card.updated_yesterday"))).toBeTruthy();
    expect(within(meta).getByText(tf("kanban.card.done_of", { done: 3, total: 5 }))).toBeTruthy();
    expect(within(meta).getByText(t("kanban.card.checker_fail"))).toBeTruthy();
    expect(within(meta).getByTestId("owner")).toBeTruthy();
  });

  it("picking an option tile answers with that decide option", () => {
    const picked = option({ recommended: true, effect: "All tests pass." });
    const target = item({ read: read({ needs: { question: "Commit it?", options: [picked] } }) });
    const { onAnswer } = setup({ items: [target] });
    expect(screen.getByText("Commit it?")).toBeTruthy();
    const tile = screen.getByRole("button", { name: /A · One/ });
    expect(tile.textContent).toContain("Recommended.");
    expect(tile.textContent).toContain("All tests pass.");
    expect(tile.getAttribute("data-recommended")).toBe("true");
    fireEvent.click(tile);
    expect(onAnswer).toHaveBeenCalledWith(target, { kind: "decide", option: picked });
  });

  it("sends a free-text reply exactly as typed, blank until a non-space character", () => {
    // Blocked, no options: the reply box is open from the start, no toggle needed.
    const target = item({ read: read({ needs: null }) });
    const { onAnswer } = setup({ items: [target] });
    const send = screen.getByRole("button", {
      name: t("kanban.needs.reply_send"),
    }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    const box = screen.getByLabelText(t("kanban.needs.reply_label"));
    fireEvent.change(box, { target: { value: "   " } });
    expect(send.disabled).toBe(true);
    fireEvent.change(box, { target: { value: "  yes, go ahead  \n" } });
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    expect(onAnswer).toHaveBeenCalledWith(target, { kind: "reply", text: "  yes, go ahead  \n" });
  });

  it("opens the reply box from a blocked card that has options, beside its tiles", () => {
    const target = item({ read: read({ needs: { question: "Q", options: [option()] } }) });
    setup({ items: [target] });
    expect(screen.queryByLabelText(t("kanban.needs.reply_label"))).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: t("kanban.needs.reply_open") }));
    expect(screen.getByLabelText(t("kanban.needs.reply_label"))).toBeTruthy();
    expect(screen.getByRole("button", { name: /A · One/ })).toBeTruthy();
  });

  it("approves a review card", () => {
    const target = item({ card: card({ status: "review" }), read: read({ needs: null }) });
    const { onAnswer } = setup({ items: [target] });
    fireEvent.click(screen.getByRole("button", { name: t("kanban.needs.approve") }));
    expect(onAnswer).toHaveBeenCalledWith(target, { kind: "approve" });
  });

  it("defaults a review card's question when its card names none", () => {
    setup({ items: [item({ card: card({ status: "review" }), read: read({ needs: null }) })] });
    expect(screen.getByText(t("kanban.needs.review_question"))).toBeTruthy();
  });

  it("sends a send-back note exactly as typed, blank until a non-space character", () => {
    const target = item({ card: card({ status: "review" }) });
    const { onAnswer } = setup({ items: [target] });
    fireEvent.click(screen.getByRole("button", { name: t("kanban.needs.send_back_open") }));
    const send = screen.getByRole("button", {
      name: t("kanban.needs.send_back"),
    }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    const box = screen.getByLabelText(t("kanban.needs.send_back_label"));
    fireEvent.change(box, { target: { value: " " } });
    expect(send.disabled).toBe(true);
    fireEvent.change(box, { target: { value: "please redo the title" } });
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    expect(onAnswer).toHaveBeenCalledWith(target, {
      kind: "send_back",
      note: "please redo the title",
    });
  });

  it("hints which Bot will see the answer, or stays generic with none", () => {
    const withBot = setup({ items: [item({ botName: "Engineer" })] });
    expect(screen.getByText(tf("kanban.needs.reply_hint", { bot: "Engineer" }))).toBeTruthy();
    withBot.unmount();
    setup({ items: [item({ botName: null })] });
    expect(screen.getByText(t("kanban.needs.reply_hint_plain"))).toBeTruthy();
  });

  it("shows no action and the no_write line when the vault refuses saves", () => {
    const target = item({ read: read({ needs: { question: "Q", options: [option()] } }) });
    setup({ items: [target], canWrite: false });
    expect(screen.getByText(t("kanban.needs.no_write"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: /A · One/ })).toBeNull();
    expect(screen.queryByRole("button", { name: t("kanban.needs.reply_open") })).toBeNull();
  });

  it("shows no action while still reading, then the unread line once reading settles", () => {
    const target = item({ read: null });
    const view = setup({ items: [target], reading: true });
    expect(screen.queryByText(t("kanban.needs.unread"))).toBeNull();
    expect(screen.queryByRole("button", { name: t("kanban.needs.reply_open") })).toBeNull();
    view.rerender(<KanbanNeedsYou {...view.props} reading={false} />);
    expect(screen.getByText(t("kanban.needs.unread"))).toBeTruthy();
  });

  it("shows the notice and focuses Undo after a save", () => {
    const notice: KanbanNotice = { kind: "saved", title: "A card", undo: true };
    const { onUndo } = setup({ notice });
    const status = screen.getByRole("status");
    expect(status.textContent).toContain(tf("kanban.notice.saved", { title: "A card" }));
    const undo = screen.getByRole("button", { name: t("kanban.notice.undo") });
    expect(document.activeElement).toBe(undo);
    fireEvent.click(undo);
    expect(onUndo).toHaveBeenCalled();
  });

  it("shows a saved notice with no Undo when the save gave none", () => {
    setup({ notice: { kind: "saved", title: "A card", undo: false } });
    expect(screen.queryByRole("button", { name: t("kanban.notice.undo") })).toBeNull();
  });

  it("renders with no items once a notice is the only thing left to show", () => {
    const { container } = setup({ notice: { kind: "undone", title: "A card" } });
    expect(container.querySelectorAll("article")).toHaveLength(0);
    expect(screen.getByText(tf("kanban.notice.undone", { title: "A card" }))).toBeTruthy();
  });

  it("disables controls while saving and for the locked ref, leaving other items free", () => {
    const locked = item({
      card: card({ ref: "kanban/locked.md" }),
      read: read({ needs: { question: "Q", options: [option()] } }),
    });
    const free = item({
      card: card({ ref: "kanban/free.md" }),
      read: read({ needs: { question: "Q2", options: [option()] } }),
    });
    setup({ items: [locked, free], lockedRef: "kanban/locked.md" });
    const tiles = screen.getAllByRole("button", { name: /A · One/ }) as HTMLButtonElement[];
    expect(tiles[0]?.disabled).toBe(true);
    expect(tiles[1]?.disabled).toBe(false);
  });

  it("disables every item's controls while a save is in flight", () => {
    setup({
      items: [item({ read: read({ needs: { question: "Q", options: [option()] } }) })],
      saving: true,
    });
    const tile = screen.getByRole("button", { name: /A · One/ }) as HTMLButtonElement;
    expect(tile.disabled).toBe(true);
  });
});
