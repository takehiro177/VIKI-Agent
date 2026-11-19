import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { OverviewNote, VaultEntry } from "../../state/vaultApi";
import { narrowBlock, readCss, ruleIn } from "../../testing/css";
import { NoteCard } from "./NoteCard";

afterEach(cleanup);

const note = (overrides: Partial<OverviewNote> = {}): OverviewNote => ({
  ref: "30-knowledge/alpha/SKILL.md",
  state: "ready",
  title: "Authored title",
  type: null,
  status: "evergreen",
  confidence: "high",
  tags: ["review", "systems", "notes", "extra"],
  created: "2026-10-01",
  modified: "2026-10-02",
  summary: "A short summary.",
  links: 1,
  complete: true,
  ...overrides,
});

const listing: VaultEntry = {
  ref: "30-knowledge/alpha/SKILL.md",
  title: "Legacy title",
  bytes: 123,
  modified: Date.parse("2026-10-03T00:00:00Z") / 1000,
};

it("keeps exact ref visible and describes it from title button", () => {
  const onOpen = vi.fn();
  render(
    <NoteCard
      noteRef={listing.ref}
      listing={listing}
      overview={note()}
      variant="card"
      kind="recent"
      onOpen={onOpen}
    />,
  );

  const title = screen.getByRole("button", { name: "Authored title" });
  const ref = screen.getByText(listing.ref);
  expect(title.getAttribute("data-vault-ref")).toBe(listing.ref);
  expect(title.getAttribute("data-vault-kind")).toBe("recent");
  expect(title.getAttribute("aria-describedby")).toBe(ref.id);
  expect(ref.hidden).toBe(false);
  fireEvent.click(title);
  expect(onOpen).toHaveBeenCalledWith(listing.ref, "recent", title);
});

it("uses listing title before path fallback and preserves an authored generic title", () => {
  const { rerender } = render(
    <NoteCard
      noteRef={listing.ref}
      listing={listing}
      variant="card"
      kind="row"
      onOpen={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "Legacy title" })).toBeTruthy();
  rerender(
    <NoteCard
      noteRef={listing.ref}
      listing={listing}
      overview={note({ title: "SKILL" })}
      variant="card"
      kind="row"
      onOpen={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "SKILL" })).toBeTruthy();
});

it("ignores overview metadata for a different exact ref", () => {
  render(
    <NoteCard
      noteRef={listing.ref}
      listing={listing}
      overview={note({ ref: "30-knowledge/other/SKILL.md", title: "Wrong note" })}
      variant="card"
      kind="row"
      onOpen={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "Legacy title" })).toBeTruthy();
  expect(screen.queryByText("Some details in this note's header could not be read.")).toBeNull();
});

it.each(["absent", "unsupported", "truncated"] as const)(
  "shows distinct coverage for a %s overview row",
  (state) => {
    render(
      <NoteCard
        noteRef={listing.ref}
        overview={note({
          state,
          title: null,
          type: null,
          status: null,
          confidence: null,
          tags: null,
          summary: null,
        })}
        variant="compact"
        kind="row"
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText(listing.ref)).toBeTruthy();
    if (state === "absent") {
      expect(screen.queryByText("Details not read")).toBeNull();
      expect(
        screen.queryByText("Some details in this note's header could not be read."),
      ).toBeNull();
      expect(screen.queryByText("No authored details.")).toBeNull();
    } else {
      expect(
        screen.getByText("Some details in this note's header could not be read."),
      ).toBeTruthy();
      expect(screen.queryByText("Details not read")).toBeNull();
    }
  },
);

it("does not claim a per-ref failure for a missing overview row", () => {
  render(
    <NoteCard
      noteRef={listing.ref}
      listing={listing}
      variant="compact"
      kind="row"
      onOpen={() => {}}
    />,
  );
  expect(screen.getByText("Details not read")).toBeTruthy();
  expect(screen.queryByText("Some details in this note's header could not be read.")).toBeNull();
});

it("uses the folder catalog default type and shows a custom authored type", () => {
  const ref = "50-maps/example.md";
  const { rerender } = render(
    <NoteCard
      noteRef={ref}
      overview={note({ ref, type: "moc" })}
      variant="card"
      kind="map"
      onOpen={() => {}}
    />,
  );
  expect(screen.getByText("Maps")).toBeTruthy();
  expect(screen.queryByText("Maps · moc")).toBeNull();
  rerender(
    <NoteCard
      noteRef={ref}
      overview={note({ ref, type: "plan" })}
      variant="card"
      kind="map"
      onOpen={() => {}}
    />,
  );
  expect(screen.getByText("Maps · plan")).toBeTruthy();
});

it("has no section kicker for a root note", () => {
  const ref = "README.md";
  const { container } = render(
    <NoteCard noteRef={ref} variant="compact" kind="row" onOpen={() => {}} />,
  );
  expect(screen.getByRole("button", { name: "README.md" })).toBeTruthy();
  expect(container.querySelector(".note-card-kicker")).toBeNull();
});

it.each(["research", "constructor", "toString"])(
  "renders an unknown %s folder literally",
  (folder) => {
    const ref = `${folder}/sample.md`;
    const { container } = render(
      <NoteCard noteRef={ref} variant="card" kind="row" onOpen={() => {}} />,
    );
    expect(container.querySelector(".note-card-kicker")?.textContent).toBe(folder);
    expect(screen.getByText(ref)).toBeTruthy();
  },
);

it("renders authored metadata as text nodes", () => {
  const markup = "<img src=x onerror=alert(1)>";
  const { container } = render(
    <NoteCard
      noteRef={listing.ref}
      overview={note({ title: markup, type: markup, summary: markup, tags: [markup] })}
      variant="card"
      kind="row"
      onOpen={() => {}}
    />,
  );
  expect(screen.getAllByText(markup).length).toBeGreaterThan(0);
  expect(container.querySelector("img")).toBeNull();
});

it("renders metadata as text, caps tags, and labels date provenance", () => {
  render(
    <NoteCard
      noteRef={listing.ref}
      overview={note()}
      variant="lead"
      kind="lead"
      onOpen={() => {}}
    />,
  );
  expect(screen.getByText("Knowledge")).toBeTruthy();
  expect(screen.getByText("evergreen · high confidence")).toBeTruthy();
  expect(screen.getByText("Updated 2026-10-02")).toBeTruthy();
  expect(screen.getByText("review")).toBeTruthy();
  expect(screen.getByText("systems")).toBeTruthy();
  expect(screen.getByText("notes")).toBeTruthy();
  expect(screen.queryByText("extra")).toBeNull();
  expect(screen.getByText("A short summary.")).toBeTruthy();
});

it("toggles exact-ref bookmark and honors disabled", () => {
  const onToggleMark = vi.fn();
  const { rerender } = render(
    <NoteCard
      noteRef={listing.ref}
      variant="card"
      kind="bookmark"
      marked
      onToggleMark={onToggleMark}
      onOpen={() => {}}
    />,
  );
  const mark = screen.getByRole("button", { name: `Remove bookmark ${listing.ref}` });
  fireEvent.click(mark);
  expect(onToggleMark).toHaveBeenCalledWith(listing.ref);
  rerender(
    <NoteCard
      noteRef={listing.ref}
      variant="card"
      kind="bookmark"
      marked
      disabled
      onToggleMark={onToggleMark}
      onOpen={() => {}}
    />,
  );
  expect(
    (screen.getByRole("button", { name: `Remove bookmark ${listing.ref}` }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it("uses a forced-colors focus outline and disables hover lift in both reduced-motion modes", () => {
  const css = readCss(import.meta.url, "./NoteCard.css");
  expect(ruleIn(css, ".note-card-summary")).toMatch(/-webkit-line-clamp:\s*2/);
  expect(ruleIn(css, ".note-card:hover")).toMatch(/transform:\s*translateY\(-1px\)/);
  const forced = narrowBlock(css, "@media (forced-colors: active) {");
  expect(ruleIn(forced, ".note-card:has(:focus-visible)")).toMatch(
    /outline:\s*2px solid Highlight/,
  );
  for (const reduced of [
    narrowBlock(css, "@media (prefers-reduced-motion: reduce) {"),
    narrowBlock(css, 'html[data-motion="reduce"]'),
  ]) {
    expect(reduced).toMatch(/\.note-card:hover\s*\{[^}]*transform:\s*none/);
  }
});

it.each([
  ["evergreen", "settled"],
  ["Processed", "settled"],
  ["done", "settled"],
  ["draft", "growing"],
  ["seed", "growing"],
  ["unread", "open"],
  ["active", "open"],
  ["archived", null],
  [null, null],
] as const)("gives status %s the %s tone", (status, tone) => {
  const ref = "30-knowledge/a.md";
  const { container } = render(
    <NoteCard
      noteRef={ref}
      overview={note({ ref, status })}
      variant="compact"
      kind="row"
      onOpen={() => {}}
    />,
  );
  expect(container.querySelector(".note-card")?.getAttribute("data-tone")).toBe(tone);
});

it.each([
  ["30-knowledge/Alpha.md", "alpha", true],
  ["30-knowledge/sub/Alpha.md", "Alpha", false],
  ["30-knowledge/Beta.md", "Alpha", false],
  ["Alpha.md", "Alpha", false],
] as const)(
  "hides the path %s from sight only when it repeats the title %s",
  (ref, title, hidden) => {
    render(
      <NoteCard
        noteRef={ref}
        overview={note({ ref, title })}
        variant="card"
        kind="recent"
        onOpen={() => {}}
      />,
    );
    const path = screen.getByText(ref);
    expect(path.hidden).toBe(hidden);
    expect(screen.getByRole("button", { name: title }).getAttribute("aria-describedby")).toBe(
      path.id,
    );
  },
);

it("leaves out the section a list already names and keeps a non-default type", () => {
  const ref = "30-knowledge/a.md";
  const card = (type: string | null, hideSection: boolean) => (
    <NoteCard
      noteRef={ref}
      overview={note({ ref, type })}
      variant="compact"
      kind="row"
      hideSection={hideSection}
      onOpen={() => {}}
    />
  );
  const { container, rerender } = render(card(null, true));
  expect(container.querySelector(".note-card-kicker")).toBeNull();
  rerender(card("thinking", true));
  expect(container.querySelector(".note-card-kicker")?.textContent).toBe("thinking");
  rerender(card("thinking", false));
  expect(container.querySelector(".note-card-kicker")?.textContent).toBe("Knowledge · thinking");
});
