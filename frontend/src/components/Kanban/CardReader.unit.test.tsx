import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CardReader, isKanbanCard, splitCardParts } from "./CardReader";

afterEach(cleanup);

const CARD = `## Summary
Summary text.

## Needs you
Needs you text.

## Done when
- [x] one
- [ ] two

## Handoff
- 2026-11-20 · engineer · blocked · this card · waits · none

## Check
Verdict: PASS
`;

describe("splitCardParts", () => {
  it("renders one part for a note with no foldable section", () => {
    const text = "## Summary\nA note.\n\n## Needs you\nReady?\n";
    const parts = splitCardParts(text);
    expect(parts).toHaveLength(1);
    expect(parts[0]?.text).toBe(text);
  });

  it("keeps a non-fold heading after a fold heading as its own part, in file order", () => {
    const text = "## Done when\n- [ ] one\n\n## Report to owner\nAll set.\n";
    const parts = splitCardParts(text);
    expect(parts.map((part) => [part.heading, part.fold])).toEqual([
      ["Done when", true],
      ["Report to owner", false],
    ]);
  });

  it("does not split a ## Check line inside a fenced code block", () => {
    const text = "## Summary\n```\n## Check\n```\nmore text\n";
    const parts = splitCardParts(text);
    expect(parts).toHaveLength(1);
    expect(parts[0]?.heading).toBe("Summary");
    expect(parts[0]?.text).toContain("## Check");
  });

  it("matches Done when, Handoff and Check case-insensitively, trimmed", () => {
    const text = "##   done WHEN  \n- [ ] one\n";
    expect(splitCardParts(text)[0]).toMatchObject({ heading: "done WHEN", fold: true });
  });

  it("F5: a stray ~~~ opens a tilde fence a later ``` cannot close, so Handoff stays swallowed in Check (matches the app's own Markdown renderer, which shows no Handoff heading for this text — see markdownFences.unit.test.ts)", () => {
    const text =
      "## Check\n``` `oops`\nVerdict: PASS\n~~~\n```python\nassert False\n```\n\n## Handoff\n- 2026-11-20 · engineer · blocked · this card · waits · none\n";
    const parts = splitCardParts(text);
    expect(parts).toHaveLength(1);
    expect(parts[0]).toMatchObject({ heading: "Check", fold: true });
    expect(parts[0]?.text).toContain("## Handoff");
  });
});

describe("isKanbanCard", () => {
  const CARD_SOURCE = "---\ntype: card\nstatus: todo\n---\n\n# x\n\n## Summary\nhi\n";

  it("is true only for a note directly in kanban/ whose source front matter has type: card", () => {
    expect(isKanbanCard("kanban/x.md", CARD_SOURCE)).toBe(true);
  });

  it("is false for a card nested in a kanban/ subfolder", () => {
    expect(isKanbanCard("kanban/sub/x.md", CARD_SOURCE)).toBe(false);
  });

  it("is false outside the kanban/ folder", () => {
    expect(isKanbanCard("other/x.md", CARD_SOURCE)).toBe(false);
  });

  it("is false when front matter has no type: card line", () => {
    expect(isKanbanCard("kanban/x.md", CARD_SOURCE.replace("type: card", "type: note"))).toBe(
      false,
    );
    expect(isKanbanCard("kanban/x.md", "# x\n\nNo front matter here.\n")).toBe(false);
  });
});

describe("CardReader", () => {
  it("folds Done when, Handoff and Check closed with their labels", () => {
    render(<CardReader text={CARD} onOpenUrl={vi.fn()} />);
    const doneWhen = screen.getByText("Done when · 1 of 2 done", { selector: "summary" });
    const handoff = screen.getByText("Handoff · 1 entry", { selector: "summary" });
    const check = screen.getByText("Check · PASS", { selector: "summary" });
    for (const summary of [doneWhen, handoff, check]) {
      const details = summary.closest("details");
      expect(details?.open).toBe(false);
    }
    // The content is still there, just folded, and the heading itself is not repeated inside.
    expect(within(doneWhen.closest("details") as HTMLElement).getByText("one")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Done when" })).toBeNull();
  });

  it("keeps Summary and Needs you outside any details", () => {
    render(<CardReader text={CARD} onOpenUrl={vi.fn()} />);
    expect(screen.getByText("Summary text.").closest("details")).toBeNull();
    expect(screen.getByText("Needs you text.").closest("details")).toBeNull();
  });

  it("keeps an old-format card's Report to owner after Done when visible, not folded", () => {
    const text = "## Done when\n<!-- none yet -->\n\n## Report to owner\nAll set.\n";
    render(<CardReader text={text} onOpenUrl={vi.fn()} />);
    expect(screen.getByText("All set.").closest("details")).toBeNull();
    const details = screen.getByText("Done when", { selector: "summary" }).closest("details");
    expect(details?.open).toBe(false);
  });

  it("renders a note with no foldable section as a single block, with no details at all", () => {
    const text = "## Summary\nA note.\n\n## Needs you\nReady?\n";
    const { container } = render(<CardReader text={text} onOpenUrl={vi.fn()} />);
    expect(container.querySelectorAll("details")).toHaveLength(0);
    expect(screen.getByText("A note.")).toBeTruthy();
    expect(screen.getByText("Ready?")).toBeTruthy();
  });

  it("shows the bare label when a fold section has no countable content", () => {
    const text = "## Done when\n\n## Handoff\n\n## Check\n";
    render(<CardReader text={text} onOpenUrl={vi.fn()} />);
    expect(screen.getByText("Done when", { selector: "summary" })).toBeTruthy();
    expect(screen.getByText("Handoff", { selector: "summary" })).toBeTruthy();
    expect(screen.getByText("Check", { selector: "summary" })).toBeTruthy();
  });

  it("singularizes a one-entry Handoff fold", () => {
    const text = "## Handoff\n- 2026-11-20 · engineer · blocked · this card · waits · none\n";
    render(<CardReader text={text} onOpenUrl={vi.fn()} />);
    expect(screen.getByText("Handoff · 1 entry", { selector: "summary" })).toBeTruthy();
  });

  it("shows Check · FAIL from the last Verdict line", () => {
    const text = "## Check\nVerdict: PASS\nretried\nVerdict: FAIL\n";
    render(<CardReader text={text} onOpenUrl={vi.fn()} />);
    expect(screen.getByText("Check · FAIL", { selector: "summary" })).toBeTruthy();
  });
});
