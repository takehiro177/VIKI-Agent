import { describe, expect, it } from "vitest";
import type { BotRow } from "../../clients/messaging/botsApi";
import type { DisplayMetadata } from "../../state/noteMetadata";
import type { VaultMetadataItem } from "../../state/vaultApi";
import {
  KANBAN_BODY_CAP,
  type KanbanListing,
  kanbanAttention,
  kanbanBodyRefs,
  kanbanLanes,
  kanbanListing,
  kanbanNeedsYou,
  kanbanReadRefs,
  wakeTarget,
} from "./kanbanCards";

function bot(id: string, profileId: string, name: string | null, workspace = "w1"): BotRow {
  return {
    bot_id: id,
    profile_id: profileId,
    workspace_id: workspace,
    profile: name
      ? {
          revision: 1,
          archived: false,
          identity: { name, purpose: "", avatar_asset_id: null, accent: "slate", shape: "rounded" },
        }
      : null,
  } as BotRow;
}

const meta = (over: Partial<DisplayMetadata> = {}): DisplayMetadata => ({
  state: "ready",
  frontmatter_state: "ready",
  heading_state: "absent",
  display_title: null,
  title_source: null,
  tags: [],
  type: null,
  status: null,
  ...over,
});
const item = (
  ref: string,
  over: Partial<VaultMetadataItem> = {},
  m: Partial<DisplayMetadata> | null = {},
): VaultMetadataItem => ({
  ref,
  state: "ready",
  reason: null,
  read_bytes: 1,
  display_metadata: m === null ? null : meta(m),
  ...over,
});
const NOW = new Date(2026, 10, 9, 12).getTime();
const H = 3_600_000;
const local = (month: number, day: number, hour: number, minute: number) =>
  new Date(2026, month, day, hour, minute).getTime() / 1000;
const listingOf = (refs: string[], over: Partial<KanbanListing> = {}): KanbanListing => ({
  refs,
  modified: new Map(),
  partial: false,
  reasons: [],
  omitted: 0,
  ...over,
});
const groups = (
  entries: VaultMetadataItem[],
  refs = entries.map((e) => e.ref),
  over: Partial<KanbanListing> = {},
) => kanbanLanes(listingOf(refs, over), new Map(entries.map((e) => [e.ref, e])), NOW);
const group = (g: ReturnType<typeof kanbanLanes>, id: string) => g.find((x) => x.id === id);
const envelope = { entries: [], partial: false, partial_reasons: [] };

describe("kanbanListing", () => {
  it("keeps only notes directly in kanban/", () => {
    const body = {
      ...envelope,
      entries: ["kanban/a.md", "kanban/sub/b.md", "Kanban/c.md", "cards/d.md", "kanbanx/f.md"].map(
        (ref) => ({ ref }),
      ),
    };
    expect(kanbanListing(body)).toEqual({
      refs: ["kanban/a.md"],
      modified: new Map(),
      partial: false,
      reasons: [],
      omitted: 0,
    });
  });

  it("keeps a file time, skips a missing one and refuses a bad one", () => {
    const body = {
      ...envelope,
      entries: [
        { ref: "kanban/a.md", modified: 1700000000.5 },
        { ref: "kanban/b.md" },
        { ref: "kanban/c.md", modified: undefined },
        { ref: "cards/x.md", modified: "soon" },
      ],
    };
    const listing = kanbanListing(body);
    expect(listing.refs).toEqual(["kanban/a.md", "kanban/b.md", "kanban/c.md"]);
    expect([...listing.modified]).toEqual([["kanban/a.md", 1700000000.5]]);
    for (const modified of ["1", null, Number.NaN, Number.POSITIVE_INFINITY])
      expect(() =>
        kanbanListing({ ...envelope, entries: [{ ref: "kanban/a.md", modified }] }),
      ).toThrow("invalid_response");
  });

  it("accepts selected rows without bytes or modified and ignores off-folder modified", () => {
    expect(
      kanbanListing({
        ...envelope,
        entries: [
          { ref: "kanban/no-bytes.md", modified: 1700000000 },
          { ref: "kanban/no-time.md", bytes: 12 },
          { ref: "other/file.md", modified: "not a time" },
        ],
      }),
    ).toEqual({
      refs: ["kanban/no-bytes.md", "kanban/no-time.md"],
      modified: new Map([["kanban/no-bytes.md", 1700000000]]),
      partial: false,
      reasons: [],
      omitted: 0,
    });
  });

  it("rejects a nonobject row anywhere in the scan", () => {
    expect(() => kanbanListing({ ...envelope, entries: [{ ref: "other/file.md" }, null] })).toThrow(
      "invalid_response",
    );
  });

  it("preserves the legacy tolerance for bytes and dot-named direct refs", () => {
    expect(
      kanbanListing({
        ...envelope,
        entries: [{ ref: "kanban/a.md", bytes: "bad" }, { ref: "kanban/." }, { ref: "kanban/.." }],
      }).refs,
    ).toEqual(["kanban/a.md", "kanban/.", "kanban/.."]);
  });

  it("refuses a malformed envelope", () => {
    const bad = [
      { entries: {} },
      { partial: "no" },
      { partial_reasons: [1] },
      { partial_reasons: "x" },
      { omitted: -1 },
      { omitted: 1.5 },
      { omitted: "1" },
      { entries: [{ ref: "kanban/a.md" }, { ref: "kanban/a.md" }] },
    ];
    for (const over of bad)
      expect(() => kanbanListing({ ...envelope, ...over })).toThrow("invalid_response");
  });
});

describe("kanbanReadRefs", () => {
  it("caps at 128 and skips refused names", () => {
    const refs = Array.from({ length: 130 }, (_, i) => `kanban/n${i}.md`);
    refs.splice(3, 0, "kanban/bad\u0085.md");
    expect(refs).toHaveLength(131);
    const read = kanbanReadRefs(refs);
    expect(read).toHaveLength(128);
    expect(read).not.toContain("kanban/bad\u0085.md");
    expect(read[127]).toBe("kanban/n127.md");
  });
});

describe("kanbanLanes", () => {
  it("maps the five statuses, blocked before review", () => {
    const g = groups([
      item("kanban/r.md", {}, { status: "review", tags: ["owner/ann"] }),
      item("kanban/b.md", {}, { status: "blocked" }),
      item("kanban/t.md", {}, { status: "todo" }),
      item("kanban/w.md", {}, { status: "working" }),
      item("kanban/d.md", {}, { status: "done" }),
    ]);
    expect(group(g, "blocked")?.cards.map((c) => [c.ref, c.owners])).toEqual([["kanban/b.md", []]]);
    expect(group(g, "review")?.cards.map((c) => [c.ref, c.owners])).toEqual([
      ["kanban/r.md", ["ann"]],
    ]);
    expect(group(g, "todo")?.cards[0]?.owners).toEqual([]);
    expect(group(g, "working")?.cards).toHaveLength(1);
    expect(group(g, "done")?.cards).toHaveLength(1);
  });

  it("Other", () => {
    const g = groups([
      item("kanban/a.md", {}, { frontmatter_state: "absent", state: "absent" }),
      item("kanban/b.md", {}, { status: null }),
      item("kanban/c.md", {}, { status: "Todo" }),
      item("kanban/d.md", {}, { status: "in-progress" }),
    ]);
    expect(group(g, "other")?.cards.map((c) => c.status)).toEqual([
      null,
      null,
      "Todo",
      "in-progress",
    ]);
    expect(group(g, "unread")).toBeUndefined();
  });

  it("Couldn't read", () => {
    const g = groups(
      [
        item(
          "kanban/u.md",
          { state: "unsupported" },
          {
            frontmatter_state: "unsupported",
            heading_state: "unsupported",
            state: "unsupported",
            tags: null,
          },
        ),
        item(
          "kanban/t.md",
          { state: "truncated" },
          {
            frontmatter_state: "truncated",
            heading_state: "truncated",
            state: "truncated",
            tags: null,
          },
        ),
        item("kanban/c.md", { state: "changed", reason: "changed" }, null),
        item("kanban/x.md", { state: "unreadable", reason: "read_failed" }, null),
        item("kanban/k.md", { state: "unknown", reason: "deadline" }, null),
        item("kanban/o.md", { state: "ready" }, null),
      ],
      [
        "kanban/u.md",
        "kanban/t.md",
        "kanban/c.md",
        "kanban/x.md",
        "kanban/k.md",
        "kanban/o.md",
        "kanban/m.md",
        "kanban/bad\u0085.md",
      ],
    );
    expect(group(g, "unread")?.cards.map((c) => c.unread)).toEqual([
      { code: "unsupported", reason: null },
      { code: "truncated", reason: null },
      { code: "changed", reason: null },
      { code: "unreadable", reason: "read_failed" },
      { code: "stopped", reason: "deadline" },
      { code: "retry", reason: null },
      { code: "retry", reason: null },
      { code: "name", reason: null },
    ]);
  });

  it("reports cards past the read cap", () => {
    const refs = Array.from({ length: 129 }, (_, i) => `kanban/n${i}.md`);
    const g = kanbanLanes(listingOf(refs), new Map(), NOW);
    expect(group(g, "unread")?.cards[128]?.unread).toEqual({ code: "cap", reason: null });
    expect(group(g, "unread")?.cards[0]?.unread).toEqual({ code: "retry", reason: null });
  });

  it("heading truncation keeps a known status", () => {
    const g = groups([
      item(
        "kanban/a.md",
        { state: "truncated" },
        { heading_state: "truncated", state: "truncated", status: "done" },
      ),
    ]);
    expect(group(g, "done")?.cards).toHaveLength(1);
    expect(group(g, "unread")).toBeUndefined();
  });

  it("reads owners in tag order", () => {
    const g = groups([
      item(
        "kanban/a.md",
        {},
        { tags: ["owner/research", "project/x", "owner/", "Owner/ops"], status: "todo" },
      ),
      item("kanban/b.md", {}, { tags: ["owner/a", "owner/b", "owner/a"], status: "todo" }),
    ]);
    expect(group(g, "todo")?.cards.map((c) => c.owners)).toEqual([["research"], ["a", "b", "a"]]);
  });

  it("titles", () => {
    const g = groups([
      item("kanban/a.md", {}, { display_title: "Nice", title_source: "title", status: "todo" }),
      item("kanban/stem.md", {}, { status: "todo" }),
      item("kanban/README.md", {}, { status: "todo" }),
      item("kanban/sub/INDEX.markdown", {}, { status: "todo" }),
    ]);
    expect(group(g, "todo")?.cards.map((c) => c.title)).toEqual([
      "Nice",
      "stem",
      "kanban / README.md",
      "sub / INDEX.markdown",
    ]);
  });

  it("lane presence", () => {
    expect(kanbanLanes(listingOf([]), new Map(), NOW).map((g) => g.id)).toEqual([
      "todo",
      "working",
      "blocked",
      "review",
      "done",
    ]);
    const g = groups([
      item("kanban/a.md", {}, { status: "x" }),
      item("kanban/b.md", { state: "changed" }, null),
    ]);
    expect(g.map((x) => x.id)).toEqual([
      "todo",
      "working",
      "blocked",
      "review",
      "done",
      "other",
      "unread",
    ]);
  });

  it("adds a Cancelled lane after Done, before Other, only while it holds a card", () => {
    const g = groups([
      item("kanban/c.md", {}, { status: "cancelled" }),
      item("kanban/d.md", {}, { status: "done" }),
      item("kanban/o.md", {}, { status: "weird" }),
    ]);
    expect(g.map((x) => x.id)).toEqual([
      "todo",
      "working",
      "blocked",
      "review",
      "done",
      "cancelled",
      "other",
    ]);
    expect(group(g, "cancelled")?.cards.map((c) => c.ref)).toEqual(["kanban/c.md"]);
  });
});

describe("card age and stale", () => {
  const card = (status: string | null, modified?: number) => {
    const g = groups([item("kanban/a.md", {}, { status })], ["kanban/a.md"], {
      modified: new Map(modified === undefined ? [] : [["kanban/a.md", modified]]),
    });
    return g.flatMap((l) => l.cards)[0];
  };
  const ago = (ms: number) => (NOW - ms) / 1000;

  it("reads a file time on the next local day as 0 days", () => {
    expect(card("todo", new Date(2026, 10, 10, 9).getTime() / 1000)?.ageDays).toBe(0);
  });

  it("counts local calendar days", () => {
    expect(card("todo", local(10, 9, 0, 30))?.ageDays).toBe(0);
    expect(card("todo", local(10, 8, 23, 0))?.ageDays).toBe(1);
    expect(card("todo", local(10, 8, 0, 30))?.ageDays).toBe(1);
    expect(card("todo", new Date(2026, 10, 6, 12).getTime() / 1000)?.ageDays).toBe(3);
    expect(card("todo", ago(-H))?.ageDays).toBe(0);
    expect(card("todo")?.ageDays).toBeNull();
  });

  it("marks working after 24 h and todo after 72 h of elapsed time", () => {
    expect(card("working", ago(24 * H))?.stale).toBe(true);
    expect(card("working", ago(24 * H - 60_000))?.stale).toBe(false);
    expect(card("todo", ago(72 * H))?.stale).toBe(true);
    expect(card("todo", ago(72 * H - 60_000))?.stale).toBe(false);
  });

  it("does not mark by calendar day, other statuses or a missing time", () => {
    const c = card("working", local(10, 8, 23, 0));
    expect(c?.ageDays).toBe(1);
    expect(c?.stale).toBe(false);
    expect(card("done", ago(30 * 24 * H))?.stale).toBe(false);
    expect(card("cancelled", ago(30 * 24 * H))?.stale).toBe(false);
    expect(card("Working", ago(30 * 24 * H))?.stale).toBe(false);
    expect(card("working")?.stale).toBe(false);
  });

  it("ages unread cards too", () => {
    const g = kanbanLanes(
      listingOf(["kanban/a.md"], { modified: new Map([["kanban/a.md", ago(2 * 24 * H)]]) }),
      new Map(),
      NOW,
    );
    expect(g.find((l) => l.id === "unread")?.cards[0]?.ageDays).toBe(2);
  });
});

describe("kanbanAttention", () => {
  const entries = [
    item("kanban/r.md", {}, { status: "review" }),
    item("kanban/b1.md", {}, { status: "blocked" }),
    item("kanban/b2.md", {}, { status: "blocked" }),
  ];
  const attention = (over: Partial<KanbanListing> = {}, more: VaultMetadataItem[] = []) => {
    const all = [...entries, ...more];
    const listing = listingOf(
      all.map((e) => e.ref),
      over,
    );
    const lanes = kanbanLanes(listing, new Map(all.map((e) => [e.ref, e])), NOW);
    return kanbanAttention(listing, lanes);
  };

  it("counts review and blocked on a complete read", () => {
    expect(attention()).toEqual({ review: 1, blocked: 2 });
  });

  it("is null on a partial, omitted, unread or oversized read", () => {
    expect(attention({ partial: true })).toBeNull();
    expect(attention({ omitted: 1 })).toBeNull();
    expect(attention({}, [item("kanban/c.md", { state: "changed" }, null)])).toBeNull();
    const refs = Array.from({ length: 129 }, (_, i) => `kanban/n${i}.md`);
    const listing = listingOf(refs);
    const items = new Map(refs.map((r) => [r, item(r, {}, { status: "todo" })]));
    expect(kanbanAttention(listing, kanbanLanes(listing, items, NOW))).toBeNull();
  });
});

describe("kanbanNeedsYou", () => {
  it("combines blocked and review, oldest change first, undated last and stable", () => {
    const entries = [
      item("kanban/late.md", {}, { status: "blocked" }),
      item("kanban/early.md", {}, { status: "review" }),
      item("kanban/mid.md", {}, { status: "blocked" }),
      item("kanban/no-time-a.md", {}, { status: "review" }),
      item("kanban/no-time-b.md", {}, { status: "blocked" }),
      item("kanban/t.md", {}, { status: "todo" }),
      item("kanban/d.md", {}, { status: "done" }),
    ];
    const listing = listingOf(
      entries.map((e) => e.ref),
      {
        modified: new Map([
          ["kanban/late.md", local(10, 9, 9, 0)],
          ["kanban/early.md", local(10, 1, 9, 0)],
          ["kanban/mid.md", local(10, 5, 9, 0)],
        ]),
      },
    );
    const lanes = kanbanLanes(listing, new Map(entries.map((e) => [e.ref, e])), NOW);
    expect(kanbanNeedsYou(lanes, listing).map((c) => c.ref)).toEqual([
      "kanban/early.md",
      "kanban/mid.md",
      "kanban/late.md",
      "kanban/no-time-b.md",
      "kanban/no-time-a.md",
    ]);
  });
});

describe("kanbanBodyRefs", () => {
  it("orders needs-you first, then working, then todo", () => {
    const entries = [
      item("kanban/b.md", {}, { status: "blocked" }),
      item("kanban/r.md", {}, { status: "review" }),
      item("kanban/w1.md", {}, { status: "working" }),
      item("kanban/w2.md", {}, { status: "working" }),
      item("kanban/t1.md", {}, { status: "todo" }),
      item("kanban/t2.md", {}, { status: "todo" }),
      item("kanban/d.md", {}, { status: "done" }),
      item("kanban/c.md", {}, { status: "cancelled" }),
      item("kanban/o.md", {}, { status: "weird" }),
    ];
    const listing = listingOf(entries.map((e) => e.ref));
    const lanes = kanbanLanes(listing, new Map(entries.map((e) => [e.ref, e])), NOW);
    expect(kanbanBodyRefs(lanes, listing)).toEqual([
      "kanban/b.md",
      "kanban/r.md",
      "kanban/w1.md",
      "kanban/w2.md",
      "kanban/t1.md",
      "kanban/t2.md",
    ]);
  });

  it(`caps at ${KANBAN_BODY_CAP}, keeping priority order`, () => {
    const todoRefs = Array.from({ length: 50 }, (_, i) => `kanban/t${i}.md`);
    const entries = todoRefs.map((ref) => item(ref, {}, { status: "todo" }));
    const listing = listingOf(todoRefs);
    const lanes = kanbanLanes(listing, new Map(entries.map((e) => [e.ref, e])), NOW);
    const refs = kanbanBodyRefs(lanes, listing);
    expect(refs).toHaveLength(KANBAN_BODY_CAP);
    expect(refs).toEqual(todoRefs.slice(0, KANBAN_BODY_CAP));
  });
});

describe("wakeTarget", () => {
  it("wakes the first owner whose seat has a Bot, skipping seats with none", () => {
    const writer = bot("w", "p-writer", "Writer");
    expect(wakeTarget(["research", "writer"], [writer], "w1")).toEqual({
      bot: writer,
      name: "Writer",
    });
  });

  it("falls back to the card's first owner's seat when no owner has a Bot", () => {
    expect(wakeTarget(["research", "writer"], [], "w1")).toEqual({ seat: "research" });
  });

  it("falls back to the first owner even when it names no known seat", () => {
    expect(wakeTarget(["editor", "research"], [], "w1")).toEqual({ seat: "editor" });
  });

  it("ignores a Bot claiming that seat in another workspace", () => {
    const away = bot("a", "p-research", "Research", "w2");
    expect(wakeTarget(["research"], [away], "w1")).toEqual({ seat: "research" });
  });

  it("is null for a card with no owners", () => {
    expect(wakeTarget([], [bot("w", "p", "Writer")], "w1")).toBeNull();
  });
});
