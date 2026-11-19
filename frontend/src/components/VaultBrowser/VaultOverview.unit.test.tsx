import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Capture, CaptureListing, CapturesApi } from "../../clients/vaults/capturesApi";
import { catalog } from "../../i18n/vault";
import type {
  OverviewNote,
  VaultBookmark,
  VaultEntry,
  VaultOverview as VaultOverviewData,
} from "../../state/vaultApi";
import { todayNumber } from "../../state/vaultOverviewStats";
import { narrowBlock, readCss, ruleIn } from "../../testing/css";
import { VaultOverview, type VaultOverviewProps, VaultOverviewSkeleton } from "./VaultOverview";

afterEach(cleanup);

const W = "00000000-0000-4000-8000-000000000001";
const OTHER_W = "00000000-0000-4000-8000-000000000002";
// A local calendar date fixed via the local-field constructor, so its day number
// (todayNumber reads local fields) is the same regardless of the host's time zone.
const TODAY = new Date(2026, 9, 20, 12, 0, 0);
const TODAY_NUM = todayNumber(TODAY);

function note(overrides: { ref: string } & Partial<OverviewNote>): OverviewNote {
  return {
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
    ...overrides,
  };
}

function overview(
  notes: OverviewNote[],
  overrides: Partial<VaultOverviewData> = {},
): VaultOverviewData {
  return { notes, partial: false, partial_reasons: [], omitted: 0, read_bytes: 0, ...overrides };
}

/** A "YYYY-MM-DD" string for the given UTC day number, built from the raw millisecond count
 * (never a local y/m/d constructor) so it round-trips through the app's own UTC day-number
 * space regardless of the host's time zone. */
function dateFromDay(day: number): string {
  return new Date(day * 86_400_000).toISOString().slice(0, 10);
}

function capture(overrides: { ref: string } & Partial<Capture>): Capture {
  return {
    revision: null,
    modified: "2026-10-18T00:00:00.000Z",
    captured: null,
    preview: null,
    cut: false,
    unread: null,
    suggestion: null,
    ...overrides,
  };
}

function entry(ref: string, modified = 1): VaultEntry {
  return { ref, title: ref, bytes: 1, modified };
}

function bookmark(ref: string, created_at: string): VaultBookmark {
  return { ref, created_at };
}

function capturesReady(listing: Partial<CaptureListing> = {}): CapturesApi {
  const full: CaptureListing = {
    captures: [],
    projects: [],
    partial: false,
    omitted: 0,
    run: null,
    ...listing,
  };
  return {
    list: vi.fn(async () => full),
    run: vi.fn(async () => null),
    suggest: vi.fn(async () => {
      throw new Error("not used in these tests");
    }),
  };
}

function baseProps(overrides: Partial<VaultOverviewProps> = {}): VaultOverviewProps {
  return {
    overview: overview([]),
    today: TODAY,
    workspaceId: W,
    inboxCount: 0,
    onOpen: vi.fn(),
    onBrowse: vi.fn(),
    onReload: vi.fn(),
    ...overrides,
  };
}

function mount(overrides: Partial<VaultOverviewProps> = {}) {
  return render(<VaultOverview {...baseProps(overrides)} />);
}

// --- Root and the partial banner ------------------------------------------------------------

test("the root is a named region; a complete overview shows no partial banner", () => {
  mount({ overview: overview([]) });
  const region = screen.getByRole("region", { name: "Vault overview" });
  expect(region.tagName).toBe("SECTION");
  expect(screen.queryByRole("button", { name: "Reload front page" })).toBeNull();
  const coverage = document.querySelectorAll(".vo-coverage");
  expect(coverage).toHaveLength(4);
  for (const line of coverage) expect(line.textContent).not.toContain("Partial");
});

test("a partial overview prints the read/omitted counts and reload calls onReload", () => {
  const onReload = vi.fn();
  mount({
    overview: overview([note({ ref: "30-knowledge/a.md" })], { partial: true, omitted: 3 }),
    onReload,
  });
  screen.getByText("Read 1 notes; 3 more were not read, so counts are partial.");
  fireEvent.click(screen.getByRole("button", { name: "Reload front page" }));
  expect(onReload).toHaveBeenCalledTimes(1);
});

// --- Inbox: without a captures API -----------------------------------------------------------

test("without captures: inboxCount drives clear/singular/plural copy; Browse inbox appears once something is waiting", () => {
  const onBrowse = vi.fn();
  const { rerender } = render(<VaultOverview {...baseProps({ inboxCount: 0, onBrowse })} />);
  screen.getByText("Inbox is clear.");
  expect(screen.queryByRole("button", { name: "Browse inbox" })).toBeNull();

  rerender(<VaultOverview {...baseProps({ inboxCount: 1, onBrowse })} />);
  screen.getByText("1 note in the inbox");
  fireEvent.click(screen.getByRole("button", { name: "Browse inbox" }));
  expect(onBrowse).toHaveBeenCalledWith("00-inbox");

  rerender(<VaultOverview {...baseProps({ inboxCount: 5, onBrowse })} />);
  screen.getByText("5 notes in the inbox");
  screen.getByRole("button", { name: "Browse inbox" });
});

test("without captures: a clear inbox shows neither button", () => {
  mount({ inboxCount: 0 });
  expect(screen.queryByRole("button", { name: "Browse inbox" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Triage inbox" })).toBeNull();
});

test("without captures: Browse inbox shows even when onTriage happens to be passed", () => {
  mount({ inboxCount: 1, onTriage: vi.fn() });
  screen.getByRole("button", { name: "Browse inbox" });
  expect(screen.queryByRole("button", { name: "Triage inbox" })).toBeNull();
});

// --- Inbox: with a captures API --------------------------------------------------------------

test("with captures: loading copy first, then Inbox is clear. once the empty list resolves; the button stays while loading but disappears once clear", async () => {
  mount({ captures: capturesReady({ captures: [] }) });
  screen.getByText("Reading the inbox…");
  screen.getByRole("button", { name: "Browse inbox" });
  await screen.findByText("Inbox is clear.");
  expect(screen.queryByRole("button", { name: "Browse inbox" })).toBeNull();
});

test("with captures: a clear inbox shows neither button, even with onTriage", async () => {
  mount({ captures: capturesReady({ captures: [] }), onTriage: vi.fn() });
  await screen.findByText("Inbox is clear.");
  expect(screen.queryByRole("button", { name: "Triage inbox" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Browse inbox" })).toBeNull();
});

test("with captures: a rejected list() shows the error copy and keeps the button", async () => {
  const api: CapturesApi = {
    list: vi.fn(async () => {
      throw new Error("network");
    }),
    run: vi.fn(async () => null),
    suggest: vi.fn(async () => {
      throw new Error("not used");
    }),
  };
  mount({ captures: api });
  await screen.findByText("Couldn't read the inbox.");
  screen.getByRole("button", { name: "Browse inbox" });
});

test("with captures: singular count, up to 3 newest previews, and +omitted not listed", async () => {
  const oldCapture = capture({
    ref: "00-inbox/old.md",
    captured: "2026-10-01",
    preview: "old thought",
  });
  const newCapture = capture({
    ref: "00-inbox/new.md",
    captured: "2026-10-19",
    preview: "new thought\nsecond line is dropped",
  });
  mount({ captures: capturesReady({ captures: [oldCapture, newCapture], omitted: 2 }) });
  await screen.findByText("2 captures waiting");
  const rows = document.querySelectorAll(".vo-capture");
  expect(rows).toHaveLength(2);
  const refs = Array.from(rows).map((row) => row.querySelector(".vo-ref")?.textContent);
  expect(refs).toEqual(["00-inbox/new.md", "00-inbox/old.md"]);
  screen.getByText("new thought");
  expect(screen.queryByText(/second line/)).toBeNull();
  screen.getByText("+2 more not listed");
});

test("with captures: a single capture uses the singular copy and hides +more when omitted is 0", async () => {
  const only = capture({ ref: "00-inbox/only.md", captured: "2026-10-19" });
  mount({ captures: capturesReady({ captures: [only], omitted: 0 }) });
  await screen.findByText("1 capture waiting");
  expect(screen.queryByText(/more not listed/)).toBeNull();
});

test("a capture preview shows only its first line, clamped to 120 code points", async () => {
  const longLine = "x".repeat(130);
  const multiLine = capture({
    ref: "00-inbox/m.md",
    captured: "2026-10-19",
    preview: `${longLine}\nsecond line`,
  });
  mount({ captures: capturesReady({ captures: [multiLine] }) });
  await screen.findByText("1 capture waiting");
  screen.getByText(`${"x".repeat(120)}…`);
  expect(screen.queryByText(/second line/)).toBeNull();
});

test("with captures: one capture still shows Triage inbox; Browse inbox is used when onTriage is absent", async () => {
  const onTriage = vi.fn();
  mount({
    captures: capturesReady({ captures: [capture({ ref: "00-inbox/a.md" })] }),
    onTriage,
  });
  await screen.findByText("1 capture waiting");
  fireEvent.click(screen.getByRole("button", { name: "Triage inbox" }));
  expect(onTriage).toHaveBeenCalledTimes(1);

  cleanup();
  const onBrowse = vi.fn();
  mount({
    captures: capturesReady({ captures: [capture({ ref: "00-inbox/a.md" })] }),
    onBrowse,
  });
  await screen.findByText("1 capture waiting");
  fireEvent.click(screen.getByRole("button", { name: "Browse inbox" }));
  expect(onBrowse).toHaveBeenCalledWith("00-inbox");
});

test("the inbox fetch runs once per [captures, workspaceId] and is aborted on cleanup", () => {
  const calls: { id: string; signal: AbortSignal }[] = [];
  const api: CapturesApi = {
    list: vi.fn((id: string, signal?: AbortSignal) => {
      calls.push({ id, signal: signal as AbortSignal });
      return new Promise<CaptureListing>(() => {
        // Never resolves: this test only inspects call arguments and abort signals.
      });
    }),
    run: vi.fn(async () => null),
    suggest: vi.fn(async () => {
      throw new Error("not used");
    }),
  };
  const { rerender, unmount } = render(
    <VaultOverview {...baseProps({ captures: api, workspaceId: W })} />,
  );
  expect(calls).toHaveLength(1);
  expect(calls[0]?.id).toBe(W);
  expect(calls[0]?.signal.aborted).toBe(false);

  rerender(<VaultOverview {...baseProps({ captures: api, workspaceId: OTHER_W })} />);
  expect(calls).toHaveLength(2);
  expect(calls[0]?.signal.aborted).toBe(true);
  expect(calls[1]?.id).toBe(OTHER_W);
  expect(calls[1]?.signal.aborted).toBe(false);

  unmount();
  expect(calls[1]?.signal.aborted).toBe(true);
});

// --- What's new --------------------------------------------------------------------------------

test('What\'s new shows "No dated notes yet." when nothing has a created/modified date', () => {
  mount({ overview: overview([note({ ref: "30-knowledge/a.md" })]) });
  screen.getByText("No dated notes yet.");
});

test("the lead card calls onOpen with its ref and keeps summary and note details as text", () => {
  const onOpen = vi.fn();
  const full = note({
    ref: "30-knowledge/alpha.md",
    title: "Alpha",
    status: "evergreen",
    confidence: "high",
    created: dateFromDay(TODAY_NUM),
    summary: "<b>bold</b> plan",
  });
  const bare = note({
    ref: "10-projects/x/tasks.md",
    title: "Tasks",
    modified: dateFromDay(TODAY_NUM),
  });
  mount({ overview: overview([full, bare]), onOpen });

  screen.getByText("Today");
  screen.getByText("Knowledge");
  screen.getByText("evergreen · high confidence");
  screen.getByText("Added 2026-10-20");
  screen.getByText("Updated 2026-10-20");

  const button = screen.getByRole("button", { name: "Alpha" });
  fireEvent.click(button);
  expect(onOpen).toHaveBeenCalledWith("30-knowledge/alpha.md", "recent", button);
  expect(button.getAttribute("data-vault-ref")).toBe("30-knowledge/alpha.md");
  expect(button.getAttribute("data-vault-kind")).toBe("lead");
  expect(
    document.querySelector('[data-vault-kind="recent"][data-vault-ref="30-knowledge/alpha.md"]'),
  ).toBeNull();
  expect(button.parentElement?.querySelector(".note-card-ref")?.textContent).toBe(
    "30-knowledge/alpha.md",
  );

  // A literal HTML-looking summary renders as text, never as a real element.
  screen.getByText("<b>bold</b> plan");
  expect(document.querySelector("b")).toBeNull();
});

test("Latest NoteCards keep authored statuses visible as text", () => {
  const sourceProcessed = note({
    ref: "40-sources/p.md",
    title: "Processed source",
    status: "processed",
    created: dateFromDay(TODAY_NUM),
  });
  const sourceUnread = note({
    ref: "40-sources/u.md",
    title: "Unread source",
    status: "unread",
    created: dateFromDay(TODAY_NUM),
  });
  const knowledgeProcessed = note({
    ref: "30-knowledge/p.md",
    title: "Oddly processed knowledge",
    status: "processed",
    created: dateFromDay(TODAY_NUM),
  });
  mount({ overview: overview([sourceProcessed, sourceUnread, knowledgeProcessed]) });

  expect(screen.getAllByText("processed")).toHaveLength(2);
  screen.getByText("unread");
});

test('What\'s new shows 10 dated rows by default; "Show more" reveals 20 more (30 total) and then disappears', () => {
  const notes = Array.from({ length: 30 }, (_, i) =>
    note({
      ref: `30-knowledge/note-${i}.md`,
      title: `Note ${i}`,
      created: dateFromDay(TODAY_NUM - i),
    }),
  );
  const { container } = mount({ overview: overview(notes) });
  expect(container.querySelectorAll('[data-vault-kind="recent"]')).toHaveLength(10);
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(container.querySelectorAll('[data-vault-kind="recent"]')).toHaveLength(30);
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
});

// --- Growth --------------------------------------------------------------------------------

test("Growth keeps the knowledge and sources cards (no standalone maps card); legend only non-zero buckets, and the full confidence line", () => {
  const notes = [
    note({ ref: "30-knowledge/a.md", status: "evergreen", confidence: "high" }),
    note({ ref: "30-knowledge/b.md", status: "evergreen", confidence: "high" }),
    note({ ref: "30-knowledge/c.md", status: "draft", confidence: null }),
    note({ ref: "40-sources/d.md", status: "processed" }),
    note({ ref: "40-sources/e.md", status: "unread" }),
    note({ ref: "50-maps/f.md" }),
  ];
  mount({ overview: overview(notes) });

  screen.getByText("3 knowledge notes");
  screen.getByText("2 evergreen · 1 draft");
  screen.getByText("Confidence: 2 high · 0 medium · 0 low · 0 other · 1 not rated");
  screen.getByText("2 sources");
  screen.getByText("1 processed · 1 unread");
  // The maps count moved to the Topics "Maps" heading (a separate test); no bare count here.
  expect(screen.queryByText("1 maps")).toBeNull();

  const meter = document.querySelector(".vo-meter");
  expect(meter?.getAttribute("aria-hidden")).toBe("true");
  const segments = Array.from(meter?.querySelectorAll<HTMLElement>(".vo-meter-segment") ?? []);
  expect(segments).toHaveLength(2);
  // Evergreen and draft take the Vault's settled and growing state colours: they must not share
  // one, or the split disappears, as it did when every bucket was an opacity step of one hue.
  expect(segments[0]?.style.background).toContain("--vault-settled");
  expect(segments[1]?.style.background).toContain("--vault-growing");
  expect(segments[0]?.style.background).not.toBe(segments[1]?.style.background);
});

test("Added per week renders a muted past bar, the accent current bar, zero-track bars, and the start/this-week labels", () => {
  const notes = [
    note({ ref: "30-knowledge/a.md", created: dateFromDay(TODAY_NUM) }), // this week
    note({ ref: "30-knowledge/b.md", created: dateFromDay(TODAY_NUM - 14) }), // 2 weeks back
  ];
  mount({ overview: overview(notes) });

  expect(document.querySelectorAll(".vo-week")).toHaveLength(12);
  const bars = Array.from(document.querySelectorAll<HTMLElement>(".vo-week-bar"));
  expect(bars).toHaveLength(12);
  for (const bar of bars) expect(bar.getAttribute("aria-hidden")).toBe("true");

  const current = bars.filter((bar) => bar.classList.contains("vo-week-bar-current"));
  const zero = bars.filter((bar) => bar.classList.contains("vo-week-bar-zero"));
  const pastActive = bars.filter(
    (bar) =>
      !bar.classList.contains("vo-week-bar-current") && !bar.classList.contains("vo-week-bar-zero"),
  );
  expect(current).toHaveLength(1);
  // weeksOf ends on the current week, so its bar is always the 12th (last) one.
  expect(bars[11]?.classList.contains("vo-week-bar-current")).toBe(true);
  expect(pastActive).toHaveLength(1);
  expect(zero).toHaveLength(10);

  const labels = Array.from(document.querySelectorAll(".vo-weeks .sr-only")).map(
    (el) => el.textContent,
  );
  expect(labels.some((text) => text?.endsWith(": 1 added"))).toBe(true);

  const footerLabels = document.querySelectorAll(".vo-weeks-labels .vo-quiet");
  expect(footerLabels).toHaveLength(2);
  expect(footerLabels[0]?.textContent).not.toBe("");
  expect(footerLabels[1]?.textContent).toBe("This week");

  // The bar row and the label row share one shrink-to-fit wrapper, so space-between
  // lands "This week" under the last bar instead of at the card's far edge.
  const wrap = document.querySelector(".vo-weeks-wrap");
  expect(wrap?.querySelector(".vo-weeks")).not.toBeNull();
  expect(wrap?.querySelector(".vo-weeks-labels")).not.toBeNull();

  screen.getByText("2 added in the last 4 weeks");
  screen.getByText("From knowledge, sources and maps.");
});

// --- Topics --------------------------------------------------------------------------------

test('Topics shows "Maps · 0" and "No maps yet." with no map notes', () => {
  mount({ overview: overview([note({ ref: "30-knowledge/a.md" })]) });
  screen.getByText("Maps · 0");
  screen.getByText("No maps yet.");
});

test("a Maps card marks an incomplete link count with +, opens with kind map, shows its ref, and shows its summary", () => {
  const onOpen = vi.fn();
  mount({
    overview: overview([
      note({ ref: "50-maps/moc.md", title: "MOC", links: 4, complete: false, summary: "hub note" }),
    ]),
    onOpen,
  });
  screen.getByText("Maps · 1");
  screen.getByText("4+ notes linked");
  screen.getByText("hub note");
  const button = screen.getByRole("button", { name: "MOC" });
  fireEvent.click(button);
  expect(onOpen).toHaveBeenCalledWith("50-maps/moc.md", "map", button);
  expect(button.parentElement?.querySelector(".note-card-ref")?.textContent).toBe("50-maps/moc.md");
});

test("a Maps card with no link count and complete=true shows a bare count with no plus", () => {
  mount({
    overview: overview([note({ ref: "50-maps/x.md", title: "X", links: null, complete: true })]),
  });
  screen.getByText("0 notes linked");
});

test("tags become clickable #tag (count) buttons calling onTag, when onTag is provided", () => {
  const onTag = vi.fn();
  mount({ overview: overview([note({ ref: "30-knowledge/a.md", tags: ["pkm", "ai"] })]), onTag });
  const button = screen.getByRole("button", { name: "#pkm (1)" });
  fireEvent.click(button);
  expect(onTag).toHaveBeenCalledWith("pkm");
});

test("tags render as plain, non-interactive chips when onTag is absent", () => {
  mount({ overview: overview([note({ ref: "30-knowledge/a.md", tags: ["pkm"] })]) });
  expect(screen.queryByRole("button", { name: /#pkm/ })).toBeNull();
  screen.getByText("#pkm (1)");
});

test('a partial overview adds "Tag counts cover the notes read." under Tags', () => {
  mount({
    overview: overview([note({ ref: "30-knowledge/a.md", tags: ["pkm"] })], {
      partial: true,
      omitted: 1,
    }),
  });
  screen.getByText("Tag counts cover the notes read.");
});

test("front page shows listing refs, source coverage, and only one global read failure", () => {
  const outside = note({
    ref: "misc/new-note.md",
    title: "Outside folder",
    created: dateFromDay(TODAY_NUM),
    summary: "A newer note outside the three covered folders.",
  });
  mount({
    overview: overview([outside], {
      partial: true,
      partial_reasons: ["unreadable", "changed"],
      omitted: 2,
    }),
    listing: [entry(outside.ref, TODAY.getTime() / 1000)],
    listingReady: true,
    listingPartial: true,
  });

  screen.getByText("Featured");
  screen.getByText("Latest");
  screen.getByText("misc/new-note.md");
  const coverage = document.querySelectorAll(".vo-coverage");
  expect(coverage).toHaveLength(4);
  for (const line of coverage)
    expect(line.textContent).toContain("In notes read from Knowledge, Sources and Maps");
  for (const line of coverage) expect(line.textContent).toContain("Partial");
  expect(screen.getAllByText("Some note details could not be read.")).toHaveLength(1);
  expect(screen.queryByText(/misc\/new-note\.md.*not in/i)).toBeNull();
});

test("front page uses the catalog's labels, hints, and actions", () => {
  const rows = Array.from({ length: 11 }, (_, index) =>
    note({
      ref: `30-knowledge/item-${index}.md`,
      title: `Item ${index}`,
      created: dateFromDay(TODAY_NUM - index),
    }),
  );
  const { rerender } = render(
    <VaultOverview {...baseProps({ overview: overview(rows, { partial: true, omitted: 1 }) })} />,
  );
  for (const key of [
    "vault.block.lead",
    "vault.block.latest",
    "vault.block.inbox",
    "vault.block.rediscover",
    "vault.block.topics",
    "vault.block.bookmarks",
    "vault.block.week",
  ] as const)
    screen.getByRole("heading", { name: catalog.en[key] });
  screen.getByText(catalog.en["vault.rediscover.hint"]);
  screen.getByText(catalog.en["vault.empty.lead"]);
  screen.getByText(catalog.en["vault.empty.rediscover"]);
  screen.getByText(catalog.en["vault.bookmark.empty"]);
  screen.getByRole("button", { name: catalog.en["vault.action.reload"] });
  screen.getByRole("button", { name: catalog.en["vault.action.show_more"] });
  const first = screen.getByRole("button", { name: "Item 0" });
  first.focus();
  rerender(
    <VaultOverview
      {...baseProps({
        overview: overview(
          [note({ ref: "30-knowledge/new.md", title: "New", created: "2026-10-21" }), ...rows],
          { partial: true, omitted: 1 },
        ),
      })}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: catalog.en["vault.action.show_latest"] }));
});

test("bookmarks keep exact refs, open with bookmark kind, and defer missing verdict until listing settles", () => {
  const onOpenCard = vi.fn();
  const marks = [
    bookmark("misc/new-note.md", "2026-10-19T00:00:00Z"),
    bookmark("gone/old.md", "2026-10-18T00:00:00Z"),
  ];
  const props: Partial<VaultOverviewProps> = {
    bookmarks: marks,
    overview: overview([
      note({ ref: "misc/new-note.md", title: "Outside folder", created: "2026-10-19" }),
    ]),
    listing: [{ ...entry("misc/new-note.md"), title: "Outside folder" }],
    listingReady: false,
    onOpenCard,
  };
  const { rerender } = render(<VaultOverview {...baseProps(props)} />);
  const present = document.querySelector<HTMLButtonElement>(
    '[data-vault-kind="bookmark"][data-vault-ref="misc/new-note.md"]',
  );
  expect(present).not.toBeNull();
  fireEvent.click(present as HTMLButtonElement);
  expect(onOpenCard).toHaveBeenCalledWith(
    "misc/new-note.md",
    "bookmark",
    present as HTMLButtonElement,
  );
  expect(screen.getAllByText("misc/new-note.md")).toHaveLength(2);
  expect(screen.queryByText("Not in the vault listing")).toBeNull();
  expect(screen.queryByText("Not in this partial listing")).toBeNull();

  rerender(
    <VaultOverview
      {...baseProps({
        ...props,
        listingReady: true,
        listingPartial: true,
      })}
    />,
  );
  screen.getByText("Not in this partial listing");
});

test("five newest valid bookmarks sort by time then exact ref; complete listing missing row is explicit", () => {
  const bookmarks = [
    bookmark("z.md", "2026-10-15T00:00:00Z"),
    bookmark("b.md", "2026-10-18T00:00:00Z"),
    bookmark("a.md", "2026-10-18T00:00:00Z"),
    bookmark("c.md", "2026-10-17T00:00:00Z"),
    bookmark("d.md", "2026-10-16T00:00:00Z"),
    bookmark("e.md", "2026-10-14T00:00:00Z"),
    bookmark("invalid.md", "invalid"),
  ];
  const { container } = mount({ bookmarks, listing: [], listingReady: true });
  const refs = Array.from(container.querySelectorAll(".vo-bookmarks .vo-ref"));
  expect(refs.map((ref) => ref.textContent)).toEqual(["a.md", "b.md", "c.md", "d.md", "z.md"]);
  expect(screen.getAllByText("Not in the vault listing")).toHaveLength(5);
  expect(screen.queryByText("invalid.md")).toBeNull();
});

test("fresh answer keeps focused card order until Show latest; workspace switch replaces cards immediately", () => {
  const first = note({ ref: "30-knowledge/first.md", title: "First", created: "2026-10-19" });
  const newer = note({ ref: "30-knowledge/newer.md", title: "Newer", created: "2026-10-20" });
  const { rerender } = render(<VaultOverview {...baseProps({ overview: overview([first]) })} />);
  const firstButton = screen.getByRole("button", { name: "First" });
  firstButton.focus();
  rerender(<VaultOverview {...baseProps({ overview: overview([newer, first]) })} />);
  expect(screen.getByRole("button", { name: "First" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Newer" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show latest" }));
  screen.getByRole("button", { name: "Newer" });

  rerender(
    <VaultOverview
      {...baseProps({
        workspaceId: OTHER_W,
        overview: overview([
          note({ ref: "20-areas/other.md", title: "Other", created: "2026-10-20" }),
        ]),
      })}
    />,
  );
  expect(screen.queryByRole("button", { name: "Newer" })).toBeNull();
  screen.getByRole("button", { name: "Other" });
});

// --- VaultOverviewSkeleton ---------------------------------------------------------------------

test("VaultOverviewSkeleton: a busy status region with matching Featured, Latest and six-block layout", () => {
  render(<VaultOverviewSkeleton />);
  const status = screen.getByRole("status");
  expect(status.className).toBe("vo vo-skel reveal-late");
  expect(status.getAttribute("aria-busy")).toBe("true");
  const srOnly = screen.getByText("Reading your notes…");
  expect(srOnly.tagName).toBe("SPAN");
  expect(srOnly.className).toContain("sr-only");

  const grid = status.querySelector(".vo-grid");
  expect(grid?.getAttribute("aria-hidden")).toBe("true");
  expect(grid?.querySelector(".vo-featured")).not.toBeNull();
  // 2 day groups x 3 items each, same as the real RecentSection's default page.
  expect(grid?.querySelectorAll(".vo-recent .vo-groups > div")).toHaveLength(2);
  expect(grid?.querySelectorAll(".vo-recent .vo-item")).toHaveLength(6);
  for (const item of grid?.querySelectorAll(".vo-recent .vo-item") ?? [])
    expect(item.querySelectorAll(".skel")).toHaveLength(3); // title, meta, 2-line summary
  expect(grid?.querySelector(".vo-inbox")).not.toBeNull();
  expect(grid?.querySelector(".vo-rediscover")).not.toBeNull();
  expect(grid?.querySelector(".vo-bookmarks")).not.toBeNull();
  expect(grid?.querySelector(".vo-growth .vo-skel-chart")).not.toBeNull();
  expect(grid?.querySelectorAll(".vo-topics .vo-skel-chip")).toHaveLength(4);
});

// --- CSS pins --------------------------------------------------------------------------------

const css = readCss(import.meta.url, "VaultOverview.css");

test("single column by default; wide layout places Featured and Latest over the side column", () => {
  expect(ruleIn(css, ".vo-grid")).toMatch(/display:\s*flex/);
  expect(ruleIn(css, ".vo-side")).toMatch(/display:\s*contents/);
  const wide = narrowBlock(css, "@container vault-browse (width >= 880px)");
  expect(ruleIn(wide, ".vo-grid")).toMatch(
    /grid-template-columns:\s*minmax\(0, 3fr\) minmax\(260px, 2fr\)/,
  );
  expect(ruleIn(wide, ".vo-recent")).toMatch(/grid-column:\s*1/);
  expect(ruleIn(wide, ".vo-featured")).toMatch(/grid-column:\s*1/);
  expect(ruleIn(wide, ".vo-side")).toMatch(/display:\s*flex/);
  expect(ruleIn(wide, ".vo-side")).toMatch(/grid-column:\s*2/);
  expect(ruleIn(wide, ".vo-featured")).toMatch(/grid-row:\s*1\s*;/);
  expect(ruleIn(wide, ".vo-recent")).toMatch(/grid-row:\s*2\s*;/);
  expect(ruleIn(wide, ".vo-side")).toMatch(/grid-row:\s*1\s*\/\s*span 2/);
});

test("the bar/label wrap shrinks to the chart's own width, not the full card", () => {
  expect(ruleIn(css, ".vo-weeks-wrap")).toMatch(/width:\s*max-content/);
});

test("past bars are muted, the current bar is the accent, zero weeks are the track colour, and bars/meter go solid CanvasText under forced colours", () => {
  expect(ruleIn(css, ".vo-week-bar")).toMatch(/background:\s*var\(--t2\)/);
  expect(ruleIn(css, ".vo-week-bar-current")).toMatch(/background:\s*var\(--or\)/);
  expect(ruleIn(css, ".vo-week-bar-zero")).toMatch(/background:\s*var\(--track\)/);
  expect(ruleIn(css, ".vo-meter")).toMatch(/background:\s*var\(--track\)/);
  const forced = narrowBlock(css, "@media (forced-colors: active)");
  expect(ruleIn(forced, ".vo-week-bar")).toMatch(/forced-color-adjust:\s*none/);
  expect(ruleIn(forced, ".vo-week-bar")).toMatch(/background:\s*CanvasText/);
  expect(ruleIn(forced, ".vo-meter-segment")).toMatch(/forced-color-adjust:\s*none/);
  expect(ruleIn(forced, ".vo-meter-segment")).toMatch(/background:\s*CanvasText/);
});

test("summaries clamp to 2 lines, NoteCard titles wrap anywhere, and buttons get a 2px accent outline", () => {
  expect(ruleIn(css, ".vo-summary")).toMatch(/-webkit-line-clamp:\s*2/);
  expect(readCss(import.meta.url, "NoteCard.css")).toMatch(/overflow-wrap:\s*anywhere/);
  expect(ruleIn(css, ".vo button:focus-visible")).toMatch(/outline:\s*2px solid var\(--or\)/);
});

test("the skeleton's growth chart block matches the real week chart's height (56px)", () => {
  expect(ruleIn(css, ".vo-skel .vo-skel-chart")).toMatch(/height:\s*56px/);
});

test("the real overview fades in on its own (.vo, not .vo-skel), none under reduced motion", () => {
  expect(ruleIn(css, ".vo:not(.vo-skel)")).toMatch(
    /animation:\s*vo-reveal var\(--motion-content\) var\(--motion-ease-out\)/,
  );
  const reduced = narrowBlock(css, "@media (prefers-reduced-motion: reduce)");
  expect(ruleIn(reduced, ".vo:not(.vo-skel)")).toMatch(/animation:\s*none/);
  expect(ruleIn(css, 'html[data-motion="reduce"] .vo:not(.vo-skel)')).toMatch(/animation:\s*none/);
});
