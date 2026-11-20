import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type Capture,
  type CaptureListing,
  type CaptureSuggestion,
  type CapturesApi,
  CapturesApiError,
  type SuggestionRun,
} from "../../clients/vaults/capturesApi";
import { CaptureTriage, type CaptureTriageProps } from "./CaptureTriage";
import type { TriageChoice } from "./triage";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function capture(overrides: Partial<Capture> = {}): Capture {
  return {
    ref: "00-inbox/a.md",
    revision: "a".repeat(64),
    modified: "2026-10-09T12:00:00.000Z",
    captured: null,
    preview: "A loose idea.",
    cut: false,
    unread: null,
    suggestion: null,
    ...overrides,
  };
}

function suggestion(overrides: Partial<CaptureSuggestion> = {}): CaptureSuggestion {
  return {
    to: "knowledge",
    project: null,
    why: "Reads like an evergreen claim.",
    at: "2026-10-09T12:05:00.000Z",
    ...overrides,
  };
}

function run(overrides: Partial<SuggestionRun> = {}): SuggestionRun {
  return {
    run_id: "r1",
    session_id: "11111111-1111-1111-1111-111111111111",
    state: "running",
    started_at: "2026-10-09T00:00:00.000Z",
    finished_at: null,
    reason: null,
    suggested: 0,
    rejected: 0,
    ...overrides,
  };
}

function listing(overrides: Partial<CaptureListing> = {}): CaptureListing {
  return { captures: [], projects: [], partial: false, omitted: 0, run: null, ...overrides };
}

function fakeApi(overrides: Partial<CapturesApi> = {}): CapturesApi {
  return {
    list: vi.fn(async () => listing()),
    run: vi.fn(async () => null),
    suggest: vi.fn(async () => run()),
    ...overrides,
  };
}

function baseProps(overrides: Partial<CaptureTriageProps> = {}): CaptureTriageProps {
  return {
    api: fakeApi(),
    workspaceId: "w1",
    choices: [],
    onChoices: vi.fn(),
    onFile: vi.fn((_text: string) => true),
    onClose: vi.fn(),
    ...overrides,
  };
}

describe("CaptureTriage: dialog chrome", () => {
  it("renders a modal dialog labelled 'Inbox triage'", () => {
    render(<CaptureTriage {...baseProps()} />);
    expect(screen.getByRole("dialog", { name: "Inbox triage" })).toBeTruthy();
  });

  it("focuses Close initially", () => {
    render(<CaptureTriage {...baseProps()} />);
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));
  });

  it("Close calls onClose", () => {
    const onClose = vi.fn();
    render(<CaptureTriage {...baseProps({ onClose })} />);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Escape calls onClose", () => {
    const onClose = vi.fn();
    render(<CaptureTriage {...baseProps({ onClose })} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Browse in Vault is absent without onBrowse", () => {
    render(<CaptureTriage {...baseProps()} />);
    expect(screen.queryByRole("button", { name: "Browse in Vault" })).toBeNull();
  });

  it("Browse in Vault calls onBrowse when given", () => {
    const onBrowse = vi.fn();
    render(<CaptureTriage {...baseProps({ onBrowse })} />);
    fireEvent.click(screen.getByRole("button", { name: "Browse in Vault" }));
    expect(onBrowse).toHaveBeenCalledOnce();
  });
});

describe("CaptureTriage: status lines", () => {
  it("shows the reading line before the listing resolves", () => {
    render(<CaptureTriage {...baseProps()} />);
    expect(screen.getByText("Reading 00-inbox/…")).toBeTruthy();
  });

  it("always shows the no-changes note, even while reading", () => {
    render(<CaptureTriage {...baseProps()} />);
    expect(
      screen.getByText("Nothing changes in your vault until you send VIKI the filing draft."),
    ).toBeTruthy();
  });

  it("shows the empty line when 00-inbox/ has no captures", async () => {
    render(<CaptureTriage {...baseProps()} />);
    expect(await screen.findByText("00-inbox/ is empty.")).toBeTruthy();
  });

  it("keeps incomplete empty scans explicit and retries only the read", async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce(listing({ partial: true }))
      .mockResolvedValueOnce(listing());
    const suggest = vi.fn();
    render(<CaptureTriage {...baseProps({ api: fakeApi({ list, suggest }) })} />);
    expect(
      await screen.findByText("No notes listed in 00-inbox/ · some files were not read"),
    ).toBeTruthy();
    expect(screen.queryByText("00-inbox/ is empty.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("00-inbox/ is empty.")).toBeTruthy();
    expect(list).toHaveBeenCalledTimes(2);
    expect(suggest).not.toHaveBeenCalled();
  });

  it("shows the count line with both addenda when they apply", async () => {
    const theListing = listing({
      captures: [capture(), capture({ ref: "00-inbox/b.md" })],
      omitted: 3,
      partial: true,
    });
    render(
      <CaptureTriage {...baseProps({ api: fakeApi({ list: vi.fn(async () => theListing) }) })} />,
    );
    expect(
      await screen.findByText(
        "2 notes in 00-inbox/ · 3 more not listed · some files were not read",
      ),
    ).toBeTruthy();
  });

  it("shows the error line and Try again on a failed load; Try again reloads", async () => {
    const list = vi
      .fn()
      .mockRejectedValueOnce(new CapturesApiError("unavailable"))
      .mockResolvedValueOnce(listing({ captures: [capture()] }));
    render(<CaptureTriage {...baseProps({ api: fakeApi({ list }) })} />);
    expect(await screen.findByText("Could not read 00-inbox/.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("1 notes in 00-inbox/")).toBeTruthy();
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe("CaptureTriage: suggestion bar", () => {
  it("disables Ask VIKI to suggest with no captures", async () => {
    render(<CaptureTriage {...baseProps()} />);
    await screen.findByText("00-inbox/ is empty.");
    const button = screen.getByRole("button", { name: "Ask VIKI to suggest" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("enables the button once captures exist, and always shows the hint", async () => {
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })) }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    const button = screen.getByRole("button", { name: "Ask VIKI to suggest" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(
      screen.getByText(
        "Runs one VIKI turn in a background chat. The app denies any permission it asks for.",
      ),
    ).toBeTruthy();
  });

  it("states that suggestions are kept until the app closes", async () => {
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })) }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    expect(screen.getByText("Suggestions are kept until VIKI Agent closes.")).toBeTruthy();
  });

  it("calls suggest(workspaceId) and disables the button while starting", async () => {
    let resolveSuggest!: (value: SuggestionRun) => void;
    const suggestPromise = new Promise<SuggestionRun>((resolve) => {
      resolveSuggest = resolve;
    });
    const suggest = vi.fn(() => suggestPromise);
    render(
      <CaptureTriage
        {...baseProps({
          workspaceId: "w9",
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })), suggest }),
        })}
      />,
    );
    const button = await screen.findByRole("button", { name: "Ask VIKI to suggest" });
    fireEvent.click(button);
    expect(suggest).toHaveBeenCalledWith("w9");
    expect((button as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      resolveSuggest(run({ state: "running" }));
      await suggestPromise;
    });
    expect(screen.getByText("VIKI is suggesting…")).toBeTruthy();
  });

  it("shows 'VIKI is suggesting…' with Open chat while running, calling onOpenChat(session_id)", async () => {
    const theRun = run({ state: "running", session_id: "22222222-2222-2222-2222-222222222222" });
    const onOpenChat = vi.fn();
    render(
      <CaptureTriage
        {...baseProps({
          onOpenChat,
          api: fakeApi({
            list: vi.fn(async () => listing({ captures: [capture()], run: theRun })),
          }),
        })}
      />,
    );
    expect(await screen.findByText("VIKI is suggesting…")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    expect(onOpenChat).toHaveBeenCalledWith("22222222-2222-2222-2222-222222222222");
  });

  it("omits Open chat while running when onOpenChat is not given", async () => {
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({
            list: vi.fn(async () =>
              listing({ captures: [capture()], run: run({ state: "running" }) }),
            ),
          }),
        })}
      />,
    );
    await screen.findByText("VIKI is suggesting…");
    expect(screen.queryByRole("button", { name: "Open chat" })).toBeNull();
  });

  it("shows the done sentence without the rejected addendum when nothing was rejected", async () => {
    const theRun = run({
      state: "done",
      finished_at: "2026-10-09T01:00:00.000Z",
      suggested: 2,
      rejected: 0,
    });
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({
            list: vi.fn(async () =>
              listing({ captures: [capture(), capture({ ref: "00-inbox/b.md" })], run: theRun }),
            ),
          }),
        })}
      />,
    );
    expect(
      await screen.findByText("Suggestions from 2026-10-09T01:00:00.000Z: 2 of 2 captures."),
    ).toBeTruthy();
  });

  it("adds the rejected addendum when some suggestions were rejected", async () => {
    const theRun = run({
      state: "done",
      finished_at: "2026-10-09T01:00:00.000Z",
      suggested: 1,
      rejected: 2,
    });
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({
            list: vi.fn(async () =>
              listing({
                captures: [
                  capture(),
                  capture({ ref: "00-inbox/b.md" }),
                  capture({ ref: "00-inbox/c.md" }),
                ],
                run: theRun,
              }),
            ),
          }),
        })}
      />,
    );
    expect(
      await screen.findByText(
        "Suggestions from 2026-10-09T01:00:00.000Z: 1 of 3 captures. 2 could not be used.",
      ),
    ).toBeTruthy();
  });

  it("Accept all sets via: suggestion only on suggested captures with no existing choice", async () => {
    const already: TriageChoice = {
      ref: "00-inbox/has-choice.md",
      revision: "r-fixed",
      modified: "2026-10-09T12:00:00.000Z",
      destination: { to: "discard", project: null },
      via: "owner",
    };
    const captures = [
      capture({
        ref: "00-inbox/has-choice.md",
        revision: "r-fixed",
        suggestion: suggestion({ to: "area" }),
      }),
      capture({
        ref: "00-inbox/suggested-1.md",
        revision: "r1",
        suggestion: suggestion({ to: "knowledge" }),
      }),
      capture({ ref: "00-inbox/no-suggestion.md", revision: "r2", suggestion: null }),
    ];
    const onChoices = vi.fn();
    render(
      <CaptureTriage
        {...baseProps({
          choices: [already],
          onChoices,
          api: fakeApi({
            list: vi.fn(async () =>
              listing({ captures, run: run({ state: "done", suggested: 1 }) }),
            ),
          }),
        })}
      />,
    );
    const acceptAll = (await screen.findByRole("button", {
      name: "Accept all 1 suggestions",
    })) as HTMLButtonElement;
    expect(acceptAll.disabled).toBe(false);
    fireEvent.click(acceptAll);
    expect(onChoices).toHaveBeenCalledWith([
      already,
      {
        ref: "00-inbox/suggested-1.md",
        revision: "r1",
        modified: "2026-10-09T12:00:00.000Z",
        destination: { to: "knowledge", project: null },
        via: "suggestion",
      },
    ]);
  });

  it("disables Accept all when there is nothing left to accept", async () => {
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({
            list: vi.fn(async () =>
              listing({ captures: [capture({ suggestion: null })], run: run({ state: "done" }) }),
            ),
          }),
        })}
      />,
    );
    const acceptAll = (await screen.findByRole("button", {
      name: "Accept all 0 suggestions",
    })) as HTMLButtonElement;
    expect(acceptAll.disabled).toBe(true);
  });

  it.each([
    ["no_captures", "There is nothing to suggest for."],
    ["not_active_vault", "This vault is not the one open in this window."],
    ["some_future_code", "Could not ask VIKI."],
  ])("maps a suggest() start failure %s to %s", async (code, sentence) => {
    const suggest = vi.fn().mockRejectedValue(new CapturesApiError(code));
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })), suggest }),
        })}
      />,
    );
    const button = await screen.findByRole("button", { name: "Ask VIKI to suggest" });
    fireEvent.click(button);
    expect(await screen.findByText(sentence)).toBeTruthy();
  });
});

describe("CaptureTriage: failed runs", () => {
  it.each([
    [
      "too_many_sessions",
      "No free session slot. Close a session or raise the limit in Settings › Chat, then try again.",
      false,
    ],
    ["deadline", "VIKI did not answer within 5 minutes.", true],
    [
      "no_suggestions",
      "VIKI's reply had no suggestions the app could read. Open the chat to see it.",
      true,
    ],
    ["cli_missing", "VIKI could not start. Check Settings → Diagnostics.", false],
    ["not_logged_in", "VIKI could not start. Check Settings → Diagnostics.", false],
    ["billing", "VIKI could not start. Check Settings → Diagnostics.", false],
    ["app_closed", "The app closed before VIKI answered.", false],
    ["cancelled", "VIKI could not suggest this time.", true],
    ["error", "VIKI could not suggest this time.", true],
    ["crashed", "VIKI could not suggest this time.", true],
    ["unlisted_future_code", "VIKI could not suggest this time.", false],
  ] as const)(
    "reason %s reads %s, offers Open chat: %s",
    async (reason, sentence, offersOpenChat) => {
      const onOpenChat = vi.fn();
      const theRun = run({ state: "failed", reason });
      render(
        <CaptureTriage
          {...baseProps({
            onOpenChat,
            api: fakeApi({
              list: vi.fn(async () => listing({ captures: [capture()], run: theRun })),
            }),
          })}
        />,
      );
      expect(await screen.findByText(sentence)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Open chat" }) !== null).toBe(offersOpenChat);
    },
  );

  it("omits Open chat for an eligible reason when onOpenChat is not given", async () => {
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({
            list: vi.fn(async () =>
              listing({ captures: [capture()], run: run({ state: "failed", reason: "deadline" }) }),
            ),
          }),
        })}
      />,
    );
    await screen.findByText("VIKI did not answer within 5 minutes.");
    expect(screen.queryByRole("button", { name: "Open chat" })).toBeNull();
  });
});

describe("CaptureTriage: cards", () => {
  it("renders one CaptureCard per capture, in listing order", async () => {
    const captures = [
      capture({ ref: "00-inbox/first.md" }),
      capture({ ref: "00-inbox/second.md" }),
      capture({ ref: "00-inbox/third.md" }),
    ];
    render(
      <CaptureTriage
        {...baseProps({ api: fakeApi({ list: vi.fn(async () => listing({ captures })) }) })}
      />,
    );
    await screen.findByText("3 notes in 00-inbox/");
    const refs = screen.getAllByText(/^00-inbox\//).map((el) => el.textContent);
    expect(refs).toEqual(["00-inbox/first.md", "00-inbox/second.md", "00-inbox/third.md"]);
  });

  it("passes the listing's projects through to each card's Change form", async () => {
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({
            list: vi.fn(async () => listing({ captures: [capture()], projects: ["garden"] })),
          }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    fireEvent.change(screen.getByLabelText("Destination"), { target: { value: "task" } });
    expect(screen.getByLabelText("Project")).toBeTruthy();
    expect(screen.getByText("garden")).toBeTruthy();
  });

  it("shows no choice chip when a stale choice's revision no longer matches the capture", async () => {
    const stale: TriageChoice = {
      ref: "00-inbox/a.md",
      revision: "old-revision",
      modified: "2026-10-09T12:00:00.000Z",
      destination: { to: "discard", project: null },
      via: "owner",
    };
    render(
      <CaptureTriage
        {...baseProps({
          choices: [stale],
          api: fakeApi({
            list: vi.fn(async () => listing({ captures: [capture({ revision: "new-revision" })] })),
          }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    expect(screen.getByRole("button", { name: "Accept" })).toBeTruthy();
    expect(screen.queryByText(/^Chosen:/)).toBeNull();
  });

  it("wires a card's onChoose into onChoices", async () => {
    const onChoices = vi.fn();
    render(
      <CaptureTriage
        {...baseProps({
          onChoices,
          api: fakeApi({
            list: vi.fn(async () =>
              listing({ captures: [capture({ ref: "00-inbox/a.md", revision: "r1" })] }),
            ),
          }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(onChoices).toHaveBeenCalledWith([
      {
        ref: "00-inbox/a.md",
        revision: "r1",
        modified: "2026-10-09T12:00:00.000Z",
        destination: { to: "discard", project: null },
        via: "owner",
      },
    ]);
  });

  it("Open note is absent without onOpenNote, and calls onOpenNote(ref, origin) when given", async () => {
    const onOpenNote = vi.fn();
    render(
      <CaptureTriage
        {...baseProps({
          onOpenNote,
          api: fakeApi({
            list: vi.fn(async () => listing({ captures: [capture({ ref: "00-inbox/note.md" })] })),
          }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    fireEvent.click(screen.getByRole("button", { name: "Open note" }));
    expect(onOpenNote).toHaveBeenCalledWith("00-inbox/note.md", expect.any(HTMLElement));
  });
});

// CaptureTriage is controlled (choices/onChoices are props); this stands in for the owning
// window's App state so a real choice, once made, round-trips back through the pruning effect
// exactly as it does in the app.
function ControlledTriage(props: CaptureTriageProps) {
  const [choices, setChoices] = useState<readonly TriageChoice[]>(props.choices);
  return <CaptureTriage {...props} choices={choices} onChoices={setChoices} />;
}

describe("CaptureTriage: pruning stale choices", () => {
  it("prunes a choice whose capture is gone once the listing lands", async () => {
    const stale: TriageChoice = {
      ref: "00-inbox/gone.md",
      revision: "r0",
      modified: "2026-10-09T12:00:00.000Z",
      destination: { to: "discard", project: null },
      via: "owner",
    };
    const kept: TriageChoice = {
      ref: "00-inbox/a.md",
      revision: "a".repeat(64),
      modified: "2026-10-09T12:00:00.000Z",
      destination: { to: "knowledge", project: null },
      via: "owner",
    };
    const onChoices = vi.fn();
    render(
      <CaptureTriage
        {...baseProps({
          choices: [stale, kept],
          onChoices,
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })) }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    // The prune runs once the new listing lands, which can be a render after its text shows.
    await waitFor(() => expect(onChoices).toHaveBeenCalledWith([kept]));
  });

  it("does not call onChoices when nothing needed pruning", async () => {
    const kept: TriageChoice = {
      ref: "00-inbox/a.md",
      revision: "a".repeat(64),
      modified: "2026-10-09T12:00:00.000Z",
      destination: { to: "knowledge", project: null },
      via: "owner",
    };
    const onChoices = vi.fn();
    render(
      <CaptureTriage
        {...baseProps({
          choices: [kept],
          onChoices,
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })) }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    expect(onChoices).not.toHaveBeenCalled();
  });

  it("drops a choice made on a too-large capture once a later scan shows a new modified stamp", async () => {
    const captureAt = (modified: string) =>
      capture({ ref: "00-inbox/big.md", revision: null, unread: "too_large", modified });

    const list1 = vi.fn(async () => listing({ captures: [captureAt("2026-10-09T12:00:00.000Z")] }));
    const { rerender } = render(
      <ControlledTriage {...baseProps({ api: fakeApi({ list: list1 }) })} />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    expect(await screen.findByText(/^Chosen:/)).toBeTruthy();

    const list2 = vi.fn(async () => listing({ captures: [captureAt("2026-10-10T08:00:00.000Z")] }));
    rerender(<ControlledTriage {...baseProps({ api: fakeApi({ list: list2 }) })} />);
    await screen.findByText("Modified 2026-10-10T08:00:00.000Z");
    // The prune runs once the new listing lands, which can be a render after its text shows.
    await waitFor(() => expect(screen.queryByText(/^Chosen:/)).toBeNull());
  });
});

describe("CaptureTriage: footer", () => {
  it("shows the chosen count and disables File at zero", async () => {
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })) }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    expect(screen.getByText("0 chosen")).toBeTruthy();
    const file = screen.getByRole("button", { name: "File 0 with VIKI" }) as HTMLButtonElement;
    expect(file.disabled).toBe(true);
  });

  it("always shows the choices-persist note", async () => {
    render(
      <CaptureTriage
        {...baseProps({
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })) }),
        })}
      />,
    );
    await screen.findByText("1 notes in 00-inbox/");
    expect(
      screen.getByText("Your choices stay until you file them or reload this window."),
    ).toBeTruthy();
  });

  it("orders the handoff text by listing order, not by the order choices were made", async () => {
    const captures = [
      capture({ ref: "00-inbox/first.md", revision: "r1" }),
      capture({ ref: "00-inbox/second.md", revision: "r2" }),
    ];
    const choiceForSecond: TriageChoice = {
      ref: "00-inbox/second.md",
      revision: "r2",
      modified: "2026-10-09T12:00:00.000Z",
      destination: { to: "daily", project: null },
      via: "owner",
    };
    const choiceForFirst: TriageChoice = {
      ref: "00-inbox/first.md",
      revision: "r1",
      modified: "2026-10-09T12:00:00.000Z",
      destination: { to: "area", project: null },
      via: "owner",
    };
    const onFile = vi.fn((_text: string) => true);
    render(
      <CaptureTriage
        {...baseProps({
          choices: [choiceForSecond, choiceForFirst],
          onFile,
          api: fakeApi({ list: vi.fn(async () => listing({ captures })) }),
        })}
      />,
    );
    const fileButton = await screen.findByRole("button", { name: "File 2 with VIKI" });
    fireEvent.click(fileButton);
    const text = onFile.mock.calls[0]?.[0] as string;
    expect(text.indexOf("00-inbox/first.md")).toBeLessThan(text.indexOf("00-inbox/second.md"));
  });

  it("onFile true clears the choices and closes", async () => {
    const onFile = vi.fn((_text: string) => true);
    const onChoices = vi.fn();
    const onClose = vi.fn();
    render(
      <CaptureTriage
        {...baseProps({
          choices: [
            {
              ref: "00-inbox/a.md",
              revision: "a".repeat(64),
              modified: "2026-10-09T12:00:00.000Z",
              destination: { to: "knowledge", project: null },
              via: "owner",
            },
          ],
          onFile,
          onChoices,
          onClose,
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })) }),
        })}
      />,
    );
    const fileButton = await screen.findByRole("button", { name: "File 1 with VIKI" });
    fireEvent.click(fileButton);
    expect(onChoices).toHaveBeenCalledWith([]);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("onFile false says so in the dialog and keeps the choices", async () => {
    const onFile = vi.fn((_text: string) => false);
    const onChoices = vi.fn();
    const onClose = vi.fn();
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    render(
      <CaptureTriage
        {...baseProps({
          choices: [
            {
              ref: "00-inbox/a.md",
              revision: "a".repeat(64),
              modified: "2026-10-09T12:00:00.000Z",
              destination: { to: "knowledge", project: null },
              via: "owner",
            },
          ],
          onFile,
          onChoices,
          onClose,
          api: fakeApi({ list: vi.fn(async () => listing({ captures: [capture()] })) }),
        })}
      />,
    );
    const fileButton = await screen.findByRole("button", { name: "File 1 with VIKI" });
    fireEvent.click(fileButton);
    const notice = screen.getByText("The draft did not open. Nothing was sent.");
    expect(notice.tagName).toBe("P");
    expect(notice.getAttribute("role")).toBe("alert");
    expect(alertSpy).not.toHaveBeenCalled();
    expect(onChoices).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
