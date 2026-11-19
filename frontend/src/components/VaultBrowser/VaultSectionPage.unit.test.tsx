import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { OverviewNote, VaultOverview as VaultOverviewData } from "../../state/vaultApi";
import { readCss } from "../../testing/css";
import { VaultSectionInsight, VaultSectionPage } from "./VaultSectionPage";

afterEach(() => {
  cleanup();
});

function overview(notes: OverviewNote[] = [], partial = false): VaultOverviewData {
  return {
    notes,
    partial,
    partial_reasons: partial ? ["entry_limit"] : [],
    omitted: 0,
    read_bytes: 0,
  };
}

function row(ref: string, modified?: number) {
  return modified === undefined
    ? { ref, title: `title:${ref}` }
    : { ref, title: `title:${ref}`, modified };
}

function detail(ref: string, tags: string[] | null = null): OverviewNote {
  return {
    ref,
    state: "ready",
    title: null,
    type: null,
    status: null,
    confidence: null,
    tags,
    created: null,
    modified: null,
    summary: null,
    links: null,
    complete: true,
  };
}

it("uses singular and plural listing counts", () => {
  const { rerender } = render(
    <VaultSectionPage
      section="sources"
      listing={[]}
      overview={overview()}
      listingPartial={false}
      onOpen={() => {}}
      onBrowse={() => {}}
    />,
  );
  expect(screen.getByText("0 notes")).toBeTruthy();

  const renderCount = (listing: ReturnType<typeof row>[], listingPartial: boolean) =>
    rerender(
      <VaultSectionPage
        section="sources"
        listing={listing}
        overview={overview()}
        listingPartial={listingPartial}
        onOpen={() => {}}
        onBrowse={() => {}}
      />,
    );

  renderCount([row("40-sources/one.md")], false);
  expect(screen.getByText("1 note")).toBeTruthy();
  renderCount([row("40-sources/one.md"), row("40-sources/two.md")], false);
  expect(screen.getByText("2 notes")).toBeTruthy();
  renderCount([row("40-sources/one.md")], true);
  expect(screen.getByText("at least 1 note")).toBeTruthy();
  renderCount([row("40-sources/one.md"), row("40-sources/two.md")], true);
  expect(screen.getByText("at least 2 notes")).toBeTruthy();
});

it("shows exact folder ref, partial listing lower-bound count, recent ordering, and 32 rows at a time", () => {
  const rows = Array.from({ length: 34 }, (_, index) =>
    row(`30-knowledge/${String(index).padStart(2, "0")}.md`, 100 + index),
  );
  const onOpen = vi.fn();
  render(
    <VaultSectionPage
      section="knowledge"
      listing={rows}
      overview={null}
      listingPartial
      onOpen={onOpen}
      onBrowse={() => {}}
    />,
  );

  expect(screen.getByText("30-knowledge/", { selector: "code" })).toBeTruthy();
  expect(screen.getByText("at least 34 notes")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: /^title:/ })).toHaveLength(32);
  expect(screen.getByRole("button", { name: "title:30-knowledge/33.md" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "title:30-knowledge/01.md" })).toBeNull();
  expect(screen.getAllByText("Details not read").length).toBeGreaterThan(1);

  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(screen.getAllByRole("button", { name: /^title:/ })).toHaveLength(34);
  const latest = screen.getByRole("button", { name: "title:30-knowledge/33.md" });
  fireEvent.click(latest);
  expect(onOpen).toHaveBeenCalledWith("30-knowledge/33.md", "row", latest);
});

it("sorts finite positive file times first and exact refs break ties", () => {
  render(
    <VaultSectionPage
      section="sources"
      listing={[
        row("40-sources/z.md", 12),
        row("40-sources/b.md", 0),
        row("40-sources/a.md", 12),
        row("40-sources/invalid.md", Number.NaN),
      ]}
      overview={overview([
        { ...detail("40-sources/z.md"), title: "A title", modified: "2026-10-30" },
        { ...detail("40-sources/a.md"), title: "Z title", modified: "2026-10-29" },
      ])}
      listingPartial={false}
      onOpen={() => {}}
      onBrowse={() => {}}
    />,
  );
  const list = screen.getByRole("list", { name: "Notes in 40-sources" });
  expect(
    within(list)
      .getAllByRole("button")
      .map((button) => button.getAttribute("data-vault-ref")),
  ).toEqual(["40-sources/a.md", "40-sources/z.md", "40-sources/b.md", "40-sources/invalid.md"]);

  fireEvent.change(screen.getByLabelText("Sort section"), { target: { value: "name" } });
  expect(
    within(list)
      .getAllByRole("button")
      .map((button) => button.getAttribute("data-vault-ref")),
  ).toEqual(["40-sources/a.md", "40-sources/b.md", "40-sources/invalid.md", "40-sources/z.md"]);
});

it("resets the visible page when sort or section changes", () => {
  const sourceRows = Array.from({ length: 33 }, (_, index) =>
    row(`40-sources/${String(index).padStart(2, "0")}.md`, index + 1),
  );
  const knowledgeRows = Array.from({ length: 33 }, (_, index) =>
    row(`30-knowledge/${String(index).padStart(2, "0")}.md`, index + 1),
  );
  const props = {
    section: "sources" as const,
    listing: sourceRows,
    overview: overview(),
    listingPartial: false,
    onOpen: () => {},
    onBrowse: () => {},
  };
  const { rerender } = render(<VaultSectionPage {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(screen.getAllByRole("button", { name: /^title:/ })).toHaveLength(33);

  fireEvent.change(screen.getByLabelText("Sort section"), { target: { value: "name" } });
  expect(screen.getAllByRole("button", { name: /^title:/ })).toHaveLength(32);
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(screen.getAllByRole("button", { name: /^title:/ })).toHaveLength(33);

  rerender(<VaultSectionPage {...props} section="knowledge" listing={knowledgeRows} />);
  expect(screen.getAllByRole("button", { name: /^title:/ })).toHaveLength(32);
});

it("names the exact folder in its empty state", () => {
  render(
    <VaultSectionPage
      section="sources"
      listing={[]}
      overview={overview()}
      listingPartial={false}
      onOpen={() => {}}
      onBrowse={() => {}}
    />,
  );
  expect(screen.getByText("No notes in this folder. (40-sources/)")).toBeTruthy();
});

it("routes tags with three-folder coverage label on complete overview", () => {
  const onTag = vi.fn();
  render(
    <VaultSectionInsight
      section="maps"
      overview={overview([
        detail("30-knowledge/a.md", ["review"]),
        detail("40-sources/b.md", ["review"]),
        detail("50-maps/Home.md", ["review"]),
      ])}
      onOpen={() => {}}
      onTag={onTag}
    />,
  );
  expect(screen.getAllByText("In notes read from Knowledge, Sources and Maps")).toHaveLength(1);
  const tag = screen.getByRole("button", { name: "#review (3)" });
  fireEvent.click(tag);
  expect(onTag).toHaveBeenCalledWith("review");
  expect(tag.className).not.toContain("chip");
});

it("shows 32 eligible Everything else folders, one newest card each, then the next 32", () => {
  const listing = [
    row("root.md", 100),
    row("bot-memory/local.md", 100),
    row(".private/local.md", 100),
    row("archive/local.md", 100),
    row("90-archive/local.md", 100),
    ...Array.from({ length: 34 }, (_, index) => {
      const folder = `Folder-${String(index).padStart(2, "0")}`;
      return [row(`${folder}/older.md`, 10), row(`${folder}/newest.md`, 20)];
    }).flat(),
  ];
  const onBrowse = vi.fn();
  render(
    <VaultSectionPage
      section="else"
      listing={listing}
      overview={null}
      listingPartial
      onOpen={() => {}}
      onBrowse={onBrowse}
    />,
  );

  expect(screen.getAllByRole("button", { name: /^title:/ })).toHaveLength(32);
  expect(screen.getByRole("button", { name: "title:Folder-00/newest.md" })).toBeTruthy();
  expect(screen.getAllByText("at least 2 notes").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "title:Folder-00/older.md" })).toBeNull();
  expect(screen.queryByText("root.md")).toBeNull();
  for (const excluded of ["bot-memory", ".private", "archive", "90-archive"]) {
    expect(screen.queryByText(`${excluded}/`, { selector: "code" })).toBeNull();
  }

  const showAll = screen.getAllByRole("button", { name: "Show all" })[0];
  if (!showAll) throw new Error("missing Show all button");
  fireEvent.click(showAll);
  expect(onBrowse).toHaveBeenCalledWith("Folder-00");
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(screen.getAllByRole("button", { name: /^title:/ })).toHaveLength(34);
});

it("keeps controls keyboard reachable and section CSS scoped", () => {
  render(
    <VaultSectionPage
      section="else"
      listing={[row("research/guide.md", 1)]}
      overview={null}
      listingPartial={false}
      onOpen={() => {}}
      onBrowse={() => {}}
    />,
  );
  const showAll = screen.getByRole("button", { name: "Show all" });
  expect(showAll.tabIndex).toBe(0);
  expect(screen.getByText("research/", { selector: "code" })).toBeTruthy();
  const css = readCss(import.meta.url, "./VaultSectionPage.css");
  expect(css).not.toMatch(/\.chip\b/);
});

it.each([
  ["sources", "No notes in this folder. (40-sources/)"],
  ["else", "No other folders in this listing."],
] as const)(
  "an unread %s listing shows its status and claims no count or empty folder",
  (section, empty) => {
    const page = (ready: boolean) => (
      <VaultSectionPage
        section={section}
        listing={[]}
        overview={overview()}
        listingPartial={false}
        listingReady={ready}
        listingStatus={<p role="alert">The vault is unavailable.</p>}
        onOpen={() => {}}
        onBrowse={() => {}}
      />
    );
    const { rerender } = render(page(false));
    expect(screen.getByRole("alert").textContent).toBe("The vault is unavailable.");
    expect(screen.queryByText("0 notes")).toBeNull();
    expect(screen.queryByText(empty)).toBeNull();

    // Once it has been read, the same empty listing is a fact, and the status line is not shown.
    rerender(page(true));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(empty)).toBeTruthy();
    if (section === "sources") expect(screen.getByText("0 notes")).toBeTruthy();
  },
);

it("marks the open note's row as current", () => {
  render(
    <VaultSectionPage
      section="knowledge"
      listing={[row("30-knowledge/a.md", 2), row("30-knowledge/b.md", 1)]}
      overview={overview()}
      listingPartial={false}
      selected="30-knowledge/b.md"
      onOpen={() => {}}
      onBrowse={() => {}}
    />,
  );
  expect(
    screen.getByRole("button", { name: "title:30-knowledge/b.md" }).getAttribute("aria-current"),
  ).toBe("true");
  expect(
    screen.getByRole("button", { name: "title:30-knowledge/a.md" }).getAttribute("aria-current"),
  ).toBeNull();
});

it("shows a section's status mix as one bar with a counted legend", () => {
  render(
    <VaultSectionInsight
      section="knowledge"
      overview={overview([
        { ...detail("30-knowledge/a.md"), status: "evergreen" },
        { ...detail("30-knowledge/b.md"), status: "evergreen" },
        { ...detail("30-knowledge/c.md"), status: "draft" },
        { ...detail("40-sources/d.md"), status: "unread" },
      ])}
      onOpen={() => {}}
      onTag={() => {}}
    />,
  );
  expect(screen.getByRole("heading", { name: "3 knowledge notes" })).toBeTruthy();
  const bar = screen.getByRole("img", {
    name: "Knowledge notes by status: 2 evergreen, 1 draft",
  });
  expect([...bar.children].map((part) => part.getAttribute("data-tone"))).toEqual([
    "settled",
    "growing",
  ]);
  expect(screen.getByText("evergreen").textContent).toBe("2 evergreen");
  expect(screen.getByText("draft").textContent).toBe("1 draft");
});

it("ranks maps by linked notes and opens one as a map", () => {
  const onOpen = vi.fn();
  render(
    <VaultSectionInsight
      section="maps"
      overview={overview([
        { ...detail("50-maps/small.md"), title: "Small", links: 1 },
        { ...detail("50-maps/big.md"), title: "Big", links: 9 },
        { ...detail("50-maps/empty.md"), title: "Empty", links: 0 },
      ])}
      onOpen={onOpen}
      onTag={() => {}}
    />,
  );
  const maps = within(screen.getByRole("list", { name: "Maps by linked notes" })).getAllByRole(
    "button",
  );
  expect(maps.map((map) => map.textContent)).toEqual(["Big9 linked", "Small1 linked"]);
  fireEvent.click(maps[0] as HTMLElement);
  expect(onOpen).toHaveBeenCalledWith("50-maps/big.md", "map", maps[0]);
});
