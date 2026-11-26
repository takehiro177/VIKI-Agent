import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { askText, HELP_GROUPS, HELP_TOPICS, searchHelp, TOUR_REGIONS } from "../../help/helpTopics";
import { HelpCenter } from "./HelpCenter";
import {
  type HelpTopicId,
  openHelp,
  resetHelp,
  useHelpAvailable,
  useHelpTarget,
} from "./helpStore";

afterEach(cleanup);
beforeEach(resetHelp);

function topic(id: HelpTopicId) {
  const found = HELP_TOPICS.find((t) => t.id === id);
  if (!found) throw new Error(`fixture missing topic ${id}`);
  return found;
}

function renderCenter(overrides: Partial<Parameters<typeof HelpCenter>[0]> = {}) {
  const onOpenUrl = vi.fn();
  const onOpenDiagnostics = vi.fn();
  const utils = render(
    <HelpCenter onOpenUrl={onOpenUrl} onOpenDiagnostics={onOpenDiagnostics} {...overrides} />,
  );
  return { ...utils, onOpenUrl, onOpenDiagnostics };
}

describe("closed", () => {
  test("renders nothing, but useHelpHost/useHelpShortcut still run", () => {
    const { container } = renderCenter();
    expect(container.firstChild).toBeNull();

    const { result: available } = renderHook(() => useHelpAvailable());
    expect(available.current).toBe(true);

    const { result: target } = renderHook(() => useHelpTarget());
    fireEvent.keyDown(window, { key: "?" });
    expect(target.current).toEqual({ kind: "topic", id: "keyboard-shortcuts" });
    // The store update re-renders the same mounted instance into the dialog.
    screen.getByRole("dialog");
  });
});

describe("the backdrop dialog", () => {
  test("is labelled by an h2 reading Help, with a Close help control", () => {
    act(() => openHelp({ kind: "topic", id: "glossary" }));
    renderCenter();
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    const labelledBy = dialog.getAttribute("aria-labelledby");
    expect(labelledBy).not.toBeNull();
    const heading = document.getElementById(labelledBy as string);
    expect(heading?.tagName).toBe("H2");
    expect(heading?.textContent).toBe("Help");
    screen.getByRole("button", { name: "Close help" });
  });

  test(".help-head wraps both the Help heading and Close help", () => {
    act(() => openHelp({ kind: "topic", id: "glossary" }));
    const { container } = renderCenter();
    const head = container.querySelector(".help-head");
    if (!head) throw new Error(".help-head not rendered");
    within(head as HTMLElement).getByRole("heading", { name: "Help" });
    within(head as HTMLElement).getByRole("button", { name: "Close help" });
  });

  test(".help-nav-top wraps .help-head and the search input, so both stay visible while the topic list scrolls", () => {
    act(() => openHelp({ kind: "topic", id: "glossary" }));
    const { container } = renderCenter();
    const navTop = container.querySelector(".help-nav-top");
    if (!navTop) throw new Error(".help-nav-top not rendered");
    within(navTop as HTMLElement).getByRole("heading", { name: "Help" });
    within(navTop as HTMLElement).getByRole("button", { name: "Close help" });
    within(navTop as HTMLElement).getByLabelText("Search help");
  });

  test("Close help closes and returns focus to the opener", () => {
    document.body.innerHTML = '<button id="opener">Open help</button>';
    const opener = document.getElementById("opener") as HTMLButtonElement;
    opener.focus();
    act(() => openHelp({ kind: "topic", id: "glossary" }));
    const { result: target } = renderHook(() => useHelpTarget());
    renderCenter();

    fireEvent.click(screen.getByRole("button", { name: "Close help" }));
    expect(target.current).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  test("Escape closes the dialog", () => {
    act(() => openHelp({ kind: "topic", id: "glossary" }));
    const { result: target } = renderHook(() => useHelpTarget());
    renderCenter();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(target.current).toBeNull();
  });

  test("a mousedown on the backdrop closes; a mousedown inside the panel does not", () => {
    act(() => openHelp({ kind: "topic", id: "glossary" }));
    const { result: target } = renderHook(() => useHelpTarget());
    const { container } = renderCenter();

    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(target.current).toEqual({ kind: "topic", id: "glossary" });

    const backdrop = container.querySelector(".help-backdrop") as HTMLElement;
    fireEvent.mouseDown(backdrop);
    expect(target.current).toBeNull();
  });
});

describe("the nav", () => {
  test("renders HELP_GROUPS in order, each a labelled group with its own topics inside", () => {
    act(() => openHelp({ kind: "topic", id: "vault" }));
    const { container } = renderCenter();
    const groups = container.querySelectorAll(".help-group");
    expect(groups).toHaveLength(HELP_GROUPS.length);

    HELP_GROUPS.forEach((groupName, index) => {
      const group = groups[index] as HTMLElement;
      expect(group.getAttribute("role")).toBe("group");
      const labelledBy = group.getAttribute("aria-labelledby");
      expect(labelledBy).not.toBeNull();
      expect(document.getElementById(labelledBy as string)?.textContent).toBe(groupName);
      within(group).getByRole("heading", { name: groupName, level: 3 });

      for (const t of HELP_TOPICS.filter((candidate) => candidate.group === groupName)) {
        within(group).getByRole("button", { name: t.title });
      }

      if (groupName === "Reference") within(group).getByRole("button", { name: "What's new" });
      else expect(within(group).queryByRole("button", { name: "What's new" })).toBeNull();
    });
  });

  test("every topic's title resolves to exactly one nav button", () => {
    act(() => openHelp({ kind: "topic", id: "vault" }));
    renderCenter();
    for (const t of HELP_TOPICS) {
      screen.getByRole("button", { name: t.title }); // throws if not exactly one match
    }
  });

  test("the search input carries the Search help placeholder, capped at 100", () => {
    act(() => openHelp({ kind: "topic", id: "vault" }));
    renderCenter();
    const input = screen.getByLabelText("Search help") as HTMLInputElement;
    expect(input.placeholder).toBe("Search help");
    expect(input.maxLength).toBe(100);
  });

  test("aria-current marks only the open topic, moving when another is clicked", () => {
    act(() => openHelp({ kind: "topic", id: "vault" }));
    const { result: target } = renderHook(() => useHelpTarget());
    renderCenter();
    expect(screen.getByRole("button", { name: "Vault" }).getAttribute("aria-current")).toBe("page");
    expect(
      screen.getByRole("button", { name: "Glossary" }).getAttribute("aria-current"),
    ).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Glossary" }));
    expect(target.current).toEqual({ kind: "topic", id: "glossary" });
    expect(screen.getByRole("button", { name: "Glossary" }).getAttribute("aria-current")).toBe(
      "page",
    );
    expect(screen.getByRole("button", { name: "Vault" }).getAttribute("aria-current")).toBeNull();
  });

  test("What's new also gets aria-current once open", () => {
    act(() => openHelp({ kind: "whats-new" }));
    renderCenter({ loadWhatsNew: () => Promise.resolve({ default: "" }) });
    expect(screen.getByRole("button", { name: "What's new" }).getAttribute("aria-current")).toBe(
      "page",
    );
  });
});

describe("a topic page", () => {
  test("renders the topic's Markdown body", () => {
    act(() => openHelp({ kind: "topic", id: "glossary" }));
    renderCenter();
    // The nav's own "Glossary" button also carries that text; the page's `# Glossary` heading
    // is what proves the body actually rendered.
    screen.getByRole("heading", { name: topic("glossary").title });
  });

  test("Related topics cards match the topic's related ids, titled and led, and open their topic", () => {
    act(() => openHelp({ kind: "topic", id: "vault" }));
    const { result: target } = renderHook(() => useHelpTarget());
    const { container } = renderCenter();
    const related = container.querySelector(".help-related");
    if (!related) throw new Error(".help-related not rendered");
    within(related as HTMLElement).getByRole("heading", { name: "Related topics", level: 3 });

    const vaultTopic = topic("vault");
    const cards = within(related as HTMLElement).getAllByRole("button");
    expect(cards).toHaveLength(vaultTopic.related.length);
    vaultTopic.related.forEach((id, index) => {
      const relatedTopic = topic(id);
      expect(cards[index]?.textContent).toBe(`${relatedTopic.title}${relatedTopic.lead}`);
    });

    fireEvent.click(cards[0] as HTMLElement);
    expect(target.current).toEqual({ kind: "topic", id: vaultTopic.related[0] });
  });

  test("troubleshooting's action opens Settings diagnostics and closes", () => {
    act(() => openHelp({ kind: "topic", id: "troubleshooting" }));
    const { result: target } = renderHook(() => useHelpTarget());
    const { onOpenDiagnostics } = renderCenter();
    fireEvent.click(screen.getByRole("button", { name: "Open Settings → Diagnostics" }));
    expect(onOpenDiagnostics).toHaveBeenCalledTimes(1);
    expect(target.current).toBeNull();
  });

  test("getting-started's action starts the tour in place, without closing", () => {
    act(() => openHelp({ kind: "topic", id: "getting-started" }));
    const { result: target } = renderHook(() => useHelpTarget());
    renderCenter();
    fireEvent.click(screen.getByRole("button", { name: "Start the tour" }));
    expect(target.current).toEqual({ kind: "tour" });
    // The dialog hands off to Tour; a getting-started page has TOUR_REGIONS.length `## ` steps
    // (helpTopics tests pin this), so the docked tour card, not the backdrop dialog, is what's
    // on screen now.
    screen.getByText(`Step 1 of ${TOUR_REGIONS.length}`);
    expect(screen.queryByRole("dialog", { name: "Help" })).toBeNull();
  });

  test("welcome's action starts the tour in place too, without closing", () => {
    act(() => openHelp({ kind: "topic", id: "welcome" }));
    const { result: target } = renderHook(() => useHelpTarget());
    renderCenter();
    fireEvent.click(screen.getByRole("button", { name: "Start the tour" }));
    expect(target.current).toEqual({ kind: "tour" });
  });

  test("a topic with no action (glossary) shows no action row without onAsk", () => {
    act(() => openHelp({ kind: "topic", id: "glossary" }));
    renderCenter();
    expect(screen.queryByRole("button", { name: "Open Settings → Diagnostics" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start the tour" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask VIKI" })).toBeNull();
  });
});

describe("search", () => {
  test("typing shows title + snippet hits; clicking one opens that topic", () => {
    act(() => openHelp({ kind: "search", query: "" }));
    const { result: target } = renderHook(() => useHelpTarget());
    const { container } = renderCenter();
    const input = screen.getByLabelText("Search help");
    fireEvent.change(input, { target: { value: "routines" } });
    expect(target.current).toEqual({ kind: "search", query: "routines" });

    // Built from the real searchHelp rather than assumed: a query can match a topic other than
    // its own name first (e.g. another page's body mentions "routines" in passing), same as
    // helpTopics.unit.test.ts itself only ever asserts membership, never hits[0].
    const expected = searchHelp("routines");
    expect(expected.length).toBeGreaterThan(0);
    const firstExpected = expected[0];
    if (!firstExpected) throw new Error("expected at least one hit");

    // Scoped to the page: the nav's own topic buttons repeat these same titles.
    const page = container.querySelector(".help-page");
    if (!page) throw new Error("page not rendered");
    const hitButtons = within(page as HTMLElement).getAllByRole("button");
    expect(hitButtons).toHaveLength(expected.length);
    const firstButton = hitButtons[0];
    if (!firstButton) throw new Error("expected a hit button");
    expect(firstButton.textContent).toBe(`${firstExpected.title}${firstExpected.snippet}`);

    fireEvent.click(firstButton);
    expect(target.current).toEqual({ kind: "topic", id: firstExpected.id });
  });

  test("a blank query shows no hits, hinting to type a word above", () => {
    act(() => openHelp({ kind: "search", query: "" }));
    renderCenter();
    expect(screen.queryByText(/No help page matches/)).toBeNull();
    screen.getByText("Type a word above to search every help page.");
  });

  test("a blank query with onAsk extends the hint to mention Ask VIKI", () => {
    act(() => openHelp({ kind: "search", query: "" }));
    renderCenter({ onAsk: vi.fn(() => true) });
    screen.getByText(
      "Type a word above to search every help page, or select Ask VIKI to start a chat draft you can finish and send.",
    );
  });

  test("no match teaches the user to try another word, or ask VIKI", () => {
    act(() => openHelp({ kind: "search", query: "zzz_not_in_any_topic" }));
    renderCenter();
    screen.getByText("No help page matches “zzz_not_in_any_topic”. Try another word, or ask VIKI.");
  });
});

describe("Ask VIKI", () => {
  test("does not render without an onAsk prop, in search or on a topic", () => {
    act(() => openHelp({ kind: "search", query: "vault" }));
    const { unmount } = renderCenter();
    expect(screen.queryByRole("button", { name: "Ask VIKI" })).toBeNull();
    unmount();

    act(() => openHelp({ kind: "topic", id: "vault" }));
    renderCenter();
    expect(screen.queryByRole("button", { name: "Ask VIKI" })).toBeNull();
  });

  test("from a topic page: the topic itself is the excerpt, with a blank question", () => {
    act(() => openHelp({ kind: "topic", id: "vault" }));
    const { result: target } = renderHook(() => useHelpTarget());
    const onAsk = vi.fn(() => true);
    renderCenter({ onAsk });
    fireEvent.click(screen.getByRole("button", { name: "Ask VIKI" }));
    expect(onAsk).toHaveBeenCalledWith(
      askText({ title: topic("vault").title, excerpt: topic("vault").body, question: "" }),
      expect.anything(),
    );
    expect(target.current).toBeNull(); // true closes
  });

  test("from search with a hit: the first hit's topic is the excerpt, the query is the question", () => {
    act(() => openHelp({ kind: "search", query: "routines" }));
    const onAsk = vi.fn(() => true);
    renderCenter({ onAsk });

    const firstHit = searchHelp("routines")[0];
    if (!firstHit) throw new Error("expected at least one hit");
    const firstTopic = topic(firstHit.id);

    fireEvent.click(screen.getByRole("button", { name: "Ask VIKI" }));
    expect(onAsk).toHaveBeenCalledWith(
      askText({ title: firstTopic.title, excerpt: firstTopic.body, question: "routines" }),
      expect.anything(),
    );
  });

  test("from search with no hit: a blank excerpt, the query as the question", () => {
    act(() => openHelp({ kind: "search", query: "zzz_not_in_any_topic" }));
    const onAsk = vi.fn(() => true);
    renderCenter({ onAsk });
    fireEvent.click(screen.getByRole("button", { name: "Ask VIKI" }));
    expect(onAsk).toHaveBeenCalledWith(
      askText({ title: "", excerpt: "", question: "zzz_not_in_any_topic" }),
      expect.anything(),
    );
  });

  test("false leaves the dialog open and explains there is no room for another draft", () => {
    act(() => openHelp({ kind: "topic", id: "vault" }));
    const { result: target } = renderHook(() => useHelpTarget());
    const onAsk = vi.fn(() => false);
    renderCenter({ onAsk });
    fireEvent.click(screen.getByRole("button", { name: "Ask VIKI" }));
    expect(target.current).toEqual({ kind: "topic", id: "vault" });
    screen.getByText("This window has no room for another draft. Close one, then try again.");
  });
});

describe("What's new", () => {
  test("renders public release notes through Markdown", async () => {
    const notes = "# What's new\n\nExample release note.\n";
    act(() => openHelp({ kind: "whats-new" }));
    renderCenter({ loadWhatsNew: () => Promise.resolve({ default: notes }) });
    await screen.findByRole("heading", { name: "What's new" });
    screen.getByText("Example release note.");
  });

  test("a rejected load says What's new isn't available in this build", async () => {
    act(() => openHelp({ kind: "whats-new" }));
    renderCenter({ loadWhatsNew: () => Promise.reject(new Error("offline")) });
    await screen.findByText("What's new isn't available in this build.");
  });

  test("empty release notes say the same thing", async () => {
    act(() => openHelp({ kind: "whats-new" }));
    renderCenter({
      loadWhatsNew: () => Promise.resolve({ default: " \n\t" }),
    });
    await screen.findByText("What's new isn't available in this build.");
  });

  test("the real import renders product facts without maintainer content", async () => {
    act(() => openHelp({ kind: "whats-new" }));
    const { container } = renderCenter();
    await screen.findByRole("heading", { name: "What's new" });
    screen.getByRole("heading", { name: "VIKI Agent 0.2.0" });
    screen.getByText("Bots learn only what you approve.");
    screen.getByRole("heading", { name: "VIKI Agent 0.1.0" });
    screen.getByText("Your own Claude Code.");
    screen.getByText("Bring your own model.");
    screen.getByText("Bots and Kanban.");
    const page = container.querySelector(".help-page");
    expect(page?.textContent).toContain("The first public release of VIKI Agent.");
    expect(page?.textContent).not.toMatch(/\bF\d+\b|S5-a|Milestone|replay script|Verified green/i);
  });
});

describe("initial focus", () => {
  test("a search target focuses the search input", () => {
    act(() => openHelp({ kind: "search", query: "" }));
    renderCenter();
    expect(document.activeElement).toBe(screen.getByLabelText("Search help"));
  });

  test("a topic target focuses Close help", () => {
    act(() => openHelp({ kind: "topic", id: "vault" }));
    renderCenter();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close help" }));
  });
});

describe("the tour target", () => {
  test("renders Tour instead of the backdrop dialog, with the getting-started steps", () => {
    act(() => openHelp({ kind: "tour" }));
    renderCenter();
    expect(screen.queryByRole("dialog", { name: "Help" })).toBeNull();
    screen.getByText(`Step 1 of ${TOUR_REGIONS.length}`);
  });
});
