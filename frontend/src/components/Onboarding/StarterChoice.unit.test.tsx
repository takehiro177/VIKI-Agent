/**
 * StarterChoice: the "Start from" radios and the viki preview, whose assistant name and
 * address mirror `starter_fill`'s own normalisation, and whose persona replies in English.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createShellStore, type ShellStore } from "../../state/shell";
import { AppStore } from "../../state/store";
import { settingsFixture } from "../../testShell";
import { StarterChoice } from "./StarterChoice";

afterEach(cleanup);

function makeStore(): ShellStore {
  return createShellStore(null, new AppStore());
}

describe("the two radios", () => {
  it("checks Start with VIKI when template is viki, and Blank folder when it is blank", () => {
    const { rerender } = render(
      <StarterChoice store={makeStore()} template="viki" onChange={vi.fn()} />,
    );
    expect(
      (screen.getByLabelText("Start with VIKI (recommended)") as HTMLInputElement).checked,
    ).toBe(true);
    expect((screen.getByLabelText("Blank folder") as HTMLInputElement).checked).toBe(false);

    rerender(<StarterChoice store={makeStore()} template="blank" onChange={vi.fn()} />);
    expect(
      (screen.getByLabelText("Start with VIKI (recommended)") as HTMLInputElement).checked,
    ).toBe(false);
    expect((screen.getByLabelText("Blank folder") as HTMLInputElement).checked).toBe(true);
  });

  it("clicking the unselected radio reports the new choice through onChange", () => {
    const toBlank = vi.fn();
    render(<StarterChoice store={makeStore()} template="viki" onChange={toBlank} />);
    fireEvent.click(screen.getByLabelText("Blank folder"));
    expect(toBlank).toHaveBeenCalledWith("blank");
    cleanup();

    const toViki = vi.fn();
    render(<StarterChoice store={makeStore()} template="blank" onChange={toViki} />);
    fireEvent.click(screen.getByLabelText("Start with VIKI (recommended)"));
    expect(toViki).toHaveBeenCalledWith("viki");
  });

  it("disables both radios when disabled", () => {
    render(<StarterChoice store={makeStore()} template="viki" onChange={vi.fn()} disabled />);
    expect(
      (screen.getByLabelText("Start with VIKI (recommended)") as HTMLInputElement).disabled,
    ).toBe(true);
    expect((screen.getByLabelText("Blank folder") as HTMLInputElement).disabled).toBe(true);
  });
});

describe("the Start with VIKI description", () => {
  it("describes the v4 starter set", () => {
    render(<StarterChoice store={makeStore()} template="viki" onChange={vi.fn()} />);
    screen.getByText(
      "A ready second brain: folders, note templates, empty memory, Kanban, five agents and thirteen skills, with a persona in AGENTS.md you can change at any time. No hooks. Your team's Bots come with it: Chief, Ops, Research, Writer, Engineer, Desk and Checker.",
    ); // throws if absent
  });
});

describe("describedBy", () => {
  it("sets aria-describedby on both radios only when the prop is given", () => {
    const { rerender } = render(
      <StarterChoice store={makeStore()} template="viki" onChange={vi.fn()} />,
    );
    expect(
      screen.getByLabelText("Start with VIKI (recommended)").getAttribute("aria-describedby"),
    ).toBe(null);
    expect(screen.getByLabelText("Blank folder").getAttribute("aria-describedby")).toBe(null);

    rerender(
      <StarterChoice
        store={makeStore()}
        template="viki"
        onChange={vi.fn()}
        describedBy="create-retry-lock-reason"
      />,
    );
    expect(
      screen.getByLabelText("Start with VIKI (recommended)").getAttribute("aria-describedby"),
    ).toBe("create-retry-lock-reason");
    expect(screen.getByLabelText("Blank folder").getAttribute("aria-describedby")).toBe(
      "create-retry-lock-reason",
    );
  });
});

describe("the viki preview", () => {
  it("falls back to VIKI/you/English before settings have ever loaded", () => {
    render(<StarterChoice store={makeStore()} template="viki" onChange={vi.fn()} />);
    screen.getByText(
      "AGENTS.md and CLAUDE.md: the persona and the vault's rules. The assistant is called VIKI and replies in English.",
    ); // throws if absent — no address clause when the address is "you"
  });

  it("reads the assistant name and address from settings, and includes the address clause", () => {
    const store = makeStore();
    store.adoptSettings(
      settingsFixture({ "identity.assistant_name": "Ada", "identity.display_name": "Sam Lee" }),
    );
    render(<StarterChoice store={store} template="viki" onChange={vi.fn()} />);
    screen.getByText(
      "AGENTS.md and CLAUDE.md: the persona and the vault's rules. The assistant is called Ada, and addresses you as Sam Lee and replies in English.",
    ); // throws if absent
  });

  it("collapses whitespace the same way starter_fill does", () => {
    const store = makeStore();
    store.adoptSettings(
      settingsFixture({
        "identity.assistant_name": " Ada  Lovelace ",
        "identity.display_name": "  Sam   Lee  ",
      }),
    );
    render(<StarterChoice store={store} template="viki" onChange={vi.fn()} />);
    screen.getByText(
      "AGENTS.md and CLAUDE.md: the persona and the vault's rules. The assistant is called Ada Lovelace, and addresses you as Sam Lee and replies in English.",
    ); // throws if absent
  });

  it("falls back to VIKI when the stored name is outside 1-32 characters", () => {
    const store = makeStore();
    store.adoptSettings(
      settingsFixture({ "identity.assistant_name": "x".repeat(33), "identity.display_name": "" }),
    );
    render(<StarterChoice store={store} template="viki" onChange={vi.fn()} />);
    screen.getByText(
      "AGENTS.md and CLAUDE.md: the persona and the vault's rules. The assistant is called VIKI and replies in English.",
    ); // throws if absent
  });

  it("falls back to you when the stored address is outside 1-64 characters", () => {
    const store = makeStore();
    store.adoptSettings(
      settingsFixture({
        "identity.assistant_name": "",
        // 40 astral (surrogate-pair) code points: 80 UTF-16 units, over the 64-unit bound, even
        // though a naive code-point count would read 40 and look fine.
        "identity.display_name": "\u{1F642}".repeat(40),
      }),
    );
    render(<StarterChoice store={store} template="viki" onChange={vi.fn()} />);
    screen.getByText(
      "AGENTS.md and CLAUDE.md: the persona and the vault's rules. The assistant is called VIKI and replies in English.",
    ); // throws if absent — no address clause when the address falls back to "you"
  });

  it("lists the rest of what the starter set writes, and where the names come from", () => {
    render(<StarterChoice store={makeStore()} template="viki" onChange={vi.fn()} />);
    screen.getByText("What Start with VIKI writes"); // throws if absent
    screen.getByText("Home.md, 50-maps/, an empty kanban/ and the folders 00-inbox to 90-archive");
    screen.getByText("_templates/: one template per note type");
    screen.getByText("system/: empty memory files, a retro folder and the log");
    screen.getByText(
      ".claude/: the librarian, curator, connector, researcher and checker agents, thirteen skills, and settings with deny rules only",
    );
    screen.getByText(
      ".viki/vault.json: the vault's id, the starter version and a fingerprint of each file",
    );
    screen.getByText("Names come from Settings → General."); // throws if absent
  });
});
