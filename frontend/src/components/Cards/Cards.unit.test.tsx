/**
 * Unit tests pinning behaviours cards.accept.test.tsx leaves implicit: the IME rule on
 * Other's input, the "answer once" guard surviving a second click on an option row, the
 * disabled state of both permission buttons after an answer, the absence of `.card-reason`
 * when there is no reason, and a multi-select Submit button staying disabled with nothing
 * selected.
 *
 * Two more: which local day the rate-limit card names its reset on, and the resume button
 * following ErrorData.recoverable. Both are invisible to errors.accept.test.tsx — it asserts
 * the reset by substring, which a date prefix satisfies either way, and it never builds a
 * crash the server calls unrecoverable — so they are pinned here or nowhere.
 */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ApprovalReceipt } from "../../clients/conversations/durableApi";
import type { PermissionRequestedData, QuestionAskedData } from "../../protocol";
import type { ApprovalView } from "../../state/approvals";
import { PermissionCard, QuestionCard, type QuestionCardProps } from "./Cards";
import { ErrorCard, RateLimitCard } from "./ErrorCard";

afterEach(cleanup);

test("a rejected permission answer becomes answerable again without a second automatic choice", async () => {
  let finish: (status: "applied" | "rejected" | "unknown") => void = () => {};
  const respond = vi.fn(
    () =>
      new Promise<"applied" | "rejected" | "unknown">((resolve) => {
        finish = resolve;
      }),
  );
  render(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      recoveryKey="first"
    />,
  );
  const allow = screen.getByRole("button", { name: "Allow once" }) as HTMLButtonElement;
  fireEvent.click(allow);
  fireEvent.click(allow);
  expect(respond).toHaveBeenCalledTimes(1);
  expect(allow.disabled).toBe(true);
  await act(async () => {
    finish("rejected");
  });
  expect(allow.disabled).toBe(false);
  expect(respond).toHaveBeenCalledTimes(1);
});

test("an unknown answer stays disabled until authoritative request recovery", async () => {
  const respond = vi.fn(async () => "unknown" as const);
  const { rerender } = render(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      recoveryKey="first"
    />,
  );
  const allow = screen.getByRole("button", { name: "Allow once" }) as HTMLButtonElement;
  await act(async () => {
    fireEvent.click(allow);
  });
  expect(allow.disabled).toBe(true);
  expect(screen.getByRole("status").textContent).toContain(
    "The response delivery result is unknown.",
  );
  rerender(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      disabled
      recoveryKey=""
    />,
  );
  expect(allow.disabled).toBe(true);
  rerender(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      recoveryKey="recovered"
    />,
  );
  expect(allow.disabled).toBe(false);
  expect(respond).toHaveBeenCalledTimes(1);
});
afterEach(() => {
  vi.useRealTimers();
});

const permission = (over: Partial<PermissionRequestedData> = {}): PermissionRequestedData => ({
  tool_use_id: "toolu_1",
  kind: "tool",
  tool: "WebFetch",
  summary: "linter-guide.docs.example.com",
  question: "May I read this page?",
  reason: "",
  agent: "",
  plan: "",
  ...over,
});

const asked = (over: Partial<QuestionAskedData["questions"][0]> = {}): QuestionAskedData => ({
  tool_use_id: "toolu_q",
  questions: [
    {
      header: "Destination",
      question: "Where should it go?",
      options: [
        { label: "30-knowledge", description: "evergreen knowledge notes" },
        { label: "00-inbox", description: "" },
      ],
      multi_select: false,
      ...over,
    },
  ],
});

test("names simultaneous approval groups by their question and target", () => {
  const respond = vi.fn();
  const openUrl = vi.fn();
  const answer = vi.fn();
  function cards(recorded = false) {
    return (
      <>
        <PermissionCard
          data={permission({
            question: "Read this note?",
            tool: "Read",
            summary: "notes/first.md",
            reason: "Needed for this answer.",
          })}
          onRespond={respond}
          onOpenUrl={openUrl}
        />
        <PermissionCard
          data={permission({
            question: "Read this note?",
            tool: "Read",
            summary: "notes/second.md",
          })}
          onRespond={respond}
          onOpenUrl={openUrl}
        />
        <PermissionCard
          data={permission({ kind: "plan", plan: "A long plan body stays separate." })}
          onRespond={respond}
          onOpenUrl={openUrl}
          {...(recorded ? { approval: view() } : {})}
        />
        <QuestionCard
          data={asked({
            header: "Destination",
            question: "Where should this note go?",
            options: [{ label: "Inbox", description: "Review later" }],
          })}
          onAnswer={answer}
        />
      </>
    );
  }
  const { container, getByRole, rerender } = render(cards());
  const first = getByRole("group", {
    name: "Read this note?",
    description: "Read notes/first.md Needed for this answer.",
  });
  const second = getByRole("group", {
    name: "Read this note?",
    description: "Read notes/second.md",
  });
  expect(first).not.toBe(second);
  expect(within(first).getByRole("button", { name: "Allow once" })).toBeTruthy();
  expect(within(second).getByRole("button", { name: "Deny" })).toBeTruthy();
  const plan = getByRole("group", {
    name: "Proceed with this plan?",
    description: "Approving exits plan mode and continues.",
  });
  expect(within(plan).getByRole("button", { name: "Approve" })).toBeTruthy();
  const questions = getByRole("group", {
    name: "Destination",
    description: "Where should this note go?",
  });
  const options = within(questions).getByRole("group", { name: "Where should this note go?" });
  expect(within(options).getByRole("button", { name: /^Inbox\s*Review later$/ })).toBeTruthy();

  const ids = [...container.querySelectorAll("[id]")].map((element) => element.id);
  expect(new Set(ids).size).toBe(ids.length);
  rerender(cards(true));
  expect([...container.querySelectorAll("[id]")].map((element) => element.id)).toEqual(ids);
  expect(
    getByRole("group", {
      name: "Proceed with this plan?",
      description: /Recorded choice: Approve.*Sent to Claude Code.*whether it ran/,
    }),
  ).toBe(plan);
  expect(respond).not.toHaveBeenCalled();
  expect(answer).not.toHaveBeenCalled();
});

test("both permission buttons disable once answered", () => {
  render(<PermissionCard data={permission()} onRespond={vi.fn()} onOpenUrl={vi.fn()} />);
  fireEvent.click(screen.getByText("Allow once"));
  expect((screen.getByText("Allow once") as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByText("Deny") as HTMLButtonElement).disabled).toBe(true);
});

test("an empty reason renders no .card-reason at all", () => {
  const { container } = render(
    <PermissionCard data={permission({ reason: "" })} onRespond={vi.fn()} onOpenUrl={vi.fn()} />,
  );
  expect(container.querySelector(".card-reason")).toBeNull();
});

// --- text the request brought: hidden characters, line breaks, a box that scrolls -------------

const at = (...points: number[]): string => String.fromCodePoint(...points);
const HIDDEN_NOTE =
  "Hidden or text-reordering characters are in this text. Each is shown as its code, for example <U+202E>.";

test("a tool card writes hidden characters out as marked codes in everything the request brought, and says so", () => {
  // Painted raw, the command reads as one quoted string; what would run deletes a folder.
  const { container, getByRole } = render(
    <PermissionCard
      data={permission({
        question: "May I run this command?",
        tool: `Ba${at(0x200d)}sh`,
        summary: `echo "safe${at(0x202e)}" ; rm -rf ~/notes #`,
        reason: `the hook${at(0x200b)} allowed it`,
        agent: `re${at(0x2066)}searcher`,
      })}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  expect(container.querySelector(".card-tool")?.textContent).toBe(
    'Ba<U+200D>sh echo "safe<U+202E>" ; rm -rf ~/notes #',
  );
  expect(container.querySelector(".card-reason")?.textContent).toBe("the hook<U+200B> allowed it");
  expect(container.querySelector(".card-agent")?.textContent).toBe("From re<U+2066>searcher");
  expect(
    [...container.querySelectorAll("mark.hidden-code")].map((mark) => mark.textContent),
  ).toEqual(["<U+2066>", "<U+200D>", "<U+202E>", "<U+200B>"]);
  for (const point of [0x200b, 0x200d, 0x202e, 0x2066]) {
    expect(container.textContent).not.toContain(at(point));
  }

  const notes = container.querySelector(".decision-notes");
  expect(notes?.textContent).toBe(HIDDEN_NOTE);
  const card = getByRole("group", { name: "May I run this command?" });
  expect(card.getAttribute("aria-describedby")?.split(" ")).toContain(notes?.id);
});

test("a card with nothing hidden says nothing and keeps the command as given, line breaks included", () => {
  const summary = "echo ok\n  rm -rf ./build";
  const { container, getByRole } = render(
    <PermissionCard
      data={permission({ question: "May I run this command?", tool: "Bash", summary })}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  // The stylesheet paints these line breaks (Cards.css.unit.test.ts); the text carries them.
  expect(container.querySelector(".card-tool")?.textContent).toBe(`Bash ${summary}`);
  expect(container.querySelector("mark")).toBeNull();
  expect(container.querySelector(".decision-notes")).toBeNull();
  const card = getByRole("group", { name: "May I run this command?" });
  expect(card.getAttribute("aria-describedby")).toBe(container.querySelector(".card-tool")?.id);
});

test("a plan card writes hidden characters out in the plan itself, and says so", () => {
  const { container, getByRole } = render(
    <PermissionCard
      data={permission({
        kind: "plan",
        tool: "ExitPlanMode",
        plan: `### Clean up\n\n- keep notes${at(0x202e)} delete drafts\n`,
      })}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  expect(container.querySelector(".card-plan li")?.textContent).toBe(
    "keep notes<U+202E> delete drafts",
  );
  expect(container.querySelector(".card-plan")?.textContent).not.toContain(at(0x202e));
  const notes = container.querySelector(".decision-notes");
  expect(notes?.textContent).toBe(HIDDEN_NOTE);
  expect(
    getByRole("group", { name: "Proceed with this plan?" })
      .getAttribute("aria-describedby")
      ?.split(" "),
  ).toContain(notes?.id);
});

test("a command or a plan taller than its box says part of it is out of view", () => {
  // Stands for the browser's layout, which jsdom does not have: the box the request is read in
  // holds 600px of text and shows less of it.
  const boxed = (el: Element): boolean =>
    el.classList.contains("card-tool") || el.classList.contains("card-plan");
  const scroll = vi.spyOn(Element.prototype, "scrollHeight", "get").mockImplementation(function (
    this: Element,
  ) {
    return boxed(this) ? 600 : 0;
  });
  const client = vi.spyOn(Element.prototype, "clientHeight", "get").mockImplementation(function (
    this: Element,
  ) {
    return boxed(this) ? 220 : 0;
  });
  try {
    const OUT_OF_VIEW = "Part of this text is out of view. Scroll to read all of it.";
    const tool = render(
      <PermissionCard
        data={permission({ question: "May I run this command?", tool: "Bash", summary: "x" })}
        onRespond={vi.fn()}
        onOpenUrl={vi.fn()}
      />,
    );
    const notes = tool.container.querySelector(".decision-notes");
    expect(notes?.textContent).toBe(OUT_OF_VIEW);
    expect(
      tool
        .getByRole("group", { name: "May I run this command?" })
        .getAttribute("aria-describedby")
        ?.split(" "),
    ).toContain(notes?.id);
    tool.unmount();

    const plan = render(
      <PermissionCard
        data={permission({ kind: "plan", tool: "ExitPlanMode", plan: "a plan" })}
        onRespond={vi.fn()}
        onOpenUrl={vi.fn()}
      />,
    );
    expect(plan.container.querySelector(".decision-notes")?.textContent).toBe(OUT_OF_VIEW);
  } finally {
    scroll.mockRestore();
    client.mockRestore();
  }
});

test("a plan with a Details heading shows the top content and folds the evidence behind a closed disclosure", () => {
  const { container } = render(
    <PermissionCard
      data={permission({
        kind: "plan",
        tool: "ExitPlanMode",
        plan: "### Add a /recall skill\n\nOwner approves this plan.\n\n## Details\n\n- [Steps](https://example.com/x)\n",
      })}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  const top = container.querySelector(".card-plan > .md");
  expect(top?.textContent).toContain("Owner approves this plan.");
  expect(top?.textContent).not.toContain("Steps");
  const details = container.querySelector(".card-plan > details.card-plan-details");
  expect(details?.hasAttribute("open")).toBe(false);
  expect(details?.querySelector("summary")?.textContent).toBe("Details");
  expect(details?.querySelector("a")?.getAttribute("href")).toBe("https://example.com/x");
});

test("a plan with no Details heading renders no disclosure at all", () => {
  const { container } = render(
    <PermissionCard
      data={permission({ kind: "plan", tool: "ExitPlanMode", plan: "Just a plan body." })}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  expect(container.querySelector(".card-plan-details")).toBeNull();
  expect(container.querySelector(".card-plan > .md")?.textContent).toContain("Just a plan body.");
});

test("a question card writes hidden characters out in its header, question, labels and descriptions, and still answers with the label as asked", () => {
  const onAnswer = vi.fn();
  const label = `Inbox${at(0x202e)}`;
  const data = asked({
    header: `Desti${at(0x200b)}nation`,
    question: `Where should${at(0x2066)} this go?`,
    options: [
      { label, description: `Review${at(0xfeff)} later` },
      { label: "Archive", description: "" },
    ],
  });
  const { container } = render(<QuestionCard data={data} onAnswer={onAnswer} />);
  expect(container.querySelector(".chip")?.textContent).toBe("Desti<U+200B>nation");
  expect(container.querySelector(".card-question")?.textContent).toBe(
    "Where should<U+2066> this go?",
  );
  expect(container.querySelector(".card-option-label")?.textContent).toBe("Inbox<U+202E>");
  expect(container.querySelector(".card-option-desc")?.textContent).toBe("Review<U+FEFF> later");
  const notes = container.querySelector(".decision-notes");
  expect(notes?.textContent).toBe(HIDDEN_NOTE);
  expect(container.querySelector(".card")?.getAttribute("aria-describedby")?.split(" ")).toContain(
    notes?.id,
  );

  // The codes are for reading only: the answer names the option exactly as it was asked.
  fireEvent.click(container.querySelectorAll(".card-option")[0] as HTMLElement);
  fireEvent.click(screen.getByText("Submit"));
  expect(JSON.stringify(onAnswer.mock.calls)).toContain(JSON.stringify(label));
  expect(JSON.stringify(onAnswer.mock.calls)).not.toContain("<U+202E>");
});

// --- stacked cards: a click that began on the card above ---------------------------------------

test("the second click of a double-click never answers the card that moved up under the pointer", async () => {
  const respond = vi.fn(async () => "applied" as const);
  const stack = (ids: string[]) => (
    <>
      {ids.map((id) => (
        <PermissionCard
          key={id}
          data={permission({ tool_use_id: id, tool: "Bash", summary: `${id}.sh` })}
          onRespond={respond}
          onOpenUrl={vi.fn()}
        />
      ))}
    </>
  );
  const { rerender } = render(stack(["first", "second"]));
  // `detail` counts the clicks of a pointer: 1, then 2 for the second click of a double-click.
  await act(async () => {
    fireEvent.click(screen.getAllByRole("button", { name: "Allow once" })[0] as HTMLElement, {
      detail: 1,
    });
  });
  expect(respond.mock.calls).toEqual([["first", true]]);

  // request.answered: the first card leaves, and the second sits where it was.
  rerender(stack(["second"]));
  const allow = screen.getByRole("button", { name: "Allow once" });
  // The press is dropped too (its default is what moves focus), then the click.
  expect(fireEvent.mouseDown(allow, { detail: 2 })).toBe(false);
  fireEvent.click(allow, { detail: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Deny" }), { detail: 3 });
  expect(respond).toHaveBeenCalledTimes(1);

  // A click of its own, by pointer or by keyboard (`detail` 0), answers as before.
  expect(fireEvent.mouseDown(allow, { detail: 1 })).toBe(true);
  await act(async () => {
    fireEvent.click(allow, { detail: 1 });
  });
  expect(respond.mock.calls).toEqual([
    ["first", true],
    ["second", true],
  ]);
});

test("a plan card drops the second click of a double-click too, and still takes the keyboard", async () => {
  const respond = vi.fn(async () => "applied" as const);
  render(
    <PermissionCard
      data={permission({ kind: "plan", tool: "ExitPlanMode", plan: "a plan" })}
      onRespond={respond}
      onOpenUrl={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Approve" }), { detail: 2 });
  expect(respond).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Keep planning" }));
  });
  expect(respond.mock.calls).toEqual([["toolu_1", false]]);
});

test("a second click on an option row after answering does not answer twice", () => {
  const onAnswer = vi.fn();
  const { container } = render(<QuestionCard data={asked()} onAnswer={onAnswer} />);
  const options = container.querySelectorAll(".card-option");
  fireEvent.click(options[0] as HTMLElement);
  fireEvent.click(screen.getByText("Submit"));
  fireEvent.click(options[0] as HTMLElement);
  fireEvent.click(options[1] as HTMLElement);
  fireEvent.click(screen.getByText("Submit"));
  expect(onAnswer).toHaveBeenCalledTimes(1);
});

test("Submit stays disabled while nothing is selected", () => {
  render(<QuestionCard data={asked({ multi_select: true })} onAnswer={vi.fn()} />);
  expect((screen.getByText("Submit") as HTMLButtonElement).disabled).toBe(true);
});

test("a multi-select toggled back to empty drops its pick", () => {
  const onAnswer = vi.fn();
  const { container } = render(
    <QuestionCard data={asked({ multi_select: true })} onAnswer={onAnswer} />,
  );
  const option = container.querySelector(".card-option") as HTMLElement;
  fireEvent.click(option);
  expect(screen.getByText("Questions selected: 1 of 1.")).toBeTruthy();
  expect((screen.getByText("Submit") as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(option);
  expect(screen.getByText("Questions selected: 0 of 1.")).toBeTruthy();
  expect((screen.getByText("Submit") as HTMLButtonElement).disabled).toBe(true);
  expect(onAnswer).not.toHaveBeenCalled();
});

test("Submit stays disabled while the answer is pending", async () => {
  let finish!: (result: "applied" | "rejected" | "unknown") => void;
  const onAnswer = vi.fn(
    () =>
      new Promise<"applied" | "rejected" | "unknown">((resolve) => {
        finish = resolve;
      }),
  );
  const { container } = render(<QuestionCard data={asked()} onAnswer={onAnswer} />);
  fireEvent.click(container.querySelector(".card-option") as HTMLElement);
  fireEvent.click(screen.getByText("Submit"));
  expect((screen.getByText("Submit") as HTMLButtonElement).disabled).toBe(true);
  await act(async () => finish("applied"));
});

test("the IME's own Enter never sends Other's text; a plain Enter does, once", () => {
  const onAnswer = vi.fn();
  const { container } = render(<QuestionCard data={asked()} onAnswer={onAnswer} />);
  fireEvent.click(screen.getByText("Other"));
  const input = container.querySelector(".card-other input") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "ななじゅうpeople に置いて" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
  expect(onAnswer).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onAnswer).toHaveBeenCalledTimes(1);
  expect(onAnswer).toHaveBeenCalledWith("toolu_q", {}, "ななじゅうpeople に置いて");
});

/** The tool's schema allows up to four questions in one ask and the CLI's prompt asks the model
 * to batch them, so more than one really does arrive. Rendering only the first meant the card
 * answered for questions the user never saw. */
const twoQuestions = (): QuestionAskedData => ({
  tool_use_id: "toolu_q2",
  questions: [
    {
      header: "Destination",
      question: "Where should it go?",
      options: [
        { label: "30-knowledge", description: "" },
        { label: "00-inbox", description: "" },
      ],
      multi_select: false,
    },
    {
      header: "Tags",
      question: "What about tags?",
      options: [
        { label: "pkm", description: "" },
        { label: "No tags", description: "" },
      ],
      multi_select: false,
    },
  ],
});

test("every question in one ask is shown, and the reply waits for all of them", () => {
  const onAnswer = vi.fn();
  const { container } = render(<QuestionCard data={twoQuestions()} onAnswer={onAnswer} />);
  expect(container.textContent).toContain("Where should it go?");
  expect(container.textContent).toContain("What about tags?");
  expect(container.querySelectorAll(".card-group")).toHaveLength(2);

  const submit = screen.getByText("Submit") as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.click(screen.getByText("30-knowledge"));
  expect(submit.disabled).toBe(true);
  expect(onAnswer).not.toHaveBeenCalled();

  fireEvent.click(screen.getByText("No tags"));
  expect(submit.disabled).toBe(false);
  expect(onAnswer).not.toHaveBeenCalled();
  fireEvent.click(submit);
  expect(onAnswer).toHaveBeenCalledTimes(1);
  expect(onAnswer).toHaveBeenCalledWith(
    "toolu_q2",
    { "Where should it go?": "30-knowledge", "What about tags?": "No tags" },
    "",
  );
});

test.each([
  ["constructor", false],
  ["constructor", true],
  ["toString", false],
  ["toString", true],
  ["__proto__", false],
  ["__proto__", true],
] as const)(
  "%s (multi-select %s) waits for its own explicit answer and keeps its exact key",
  async (question, multi) => {
    const onAnswer = vi.fn<QuestionCardProps["onAnswer"]>(async () => "applied");
    render(
      <QuestionCard
        data={{
          tool_use_id: "toolu_prototype",
          questions: [
            {
              header: "First",
              question: "Choose a destination?",
              multi_select: false,
              options: [{ label: "Inbox", description: "" }],
            },
            {
              header: "Second",
              question,
              multi_select: multi,
              options: [
                { label: "Keep exact key", description: "" },
                { label: "Skip", description: "" },
              ],
            },
          ],
        }}
        onAnswer={onAnswer}
      />,
    );
    const second = within(screen.getByRole("group", { name: question }));
    const options = second
      .getAllByRole<HTMLButtonElement>("button")
      .filter((button) => button.classList.contains("card-option"));
    expect(options.every((button) => !button.disabled)).toBe(true);
    expect(options.map((button) => button.getAttribute("aria-pressed"))).toEqual([
      "false",
      "false",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Inbox" }));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.getByText("Questions selected: 1 of 2.")).toBeTruthy();
    fireEvent.click(second.getByRole("button", { name: "Keep exact key" }));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.getByText("Questions selected: 2 of 2.")).toBeTruthy();
    await act(async () => fireEvent.click(screen.getByText("Submit")));
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith(
      "toolu_prototype",
      Object.fromEntries([
        ["Choose a destination?", "Inbox"],
        [question, "Keep exact key"],
      ]),
      "",
    );
    const submitted = onAnswer.mock.calls[0]?.[1];
    expect(Object.hasOwn(submitted ?? {}, question)).toBe(true);
    expect(Object.keys(submitted ?? {})).toHaveLength(2);
  },
);

test("a picked question stays changeable until Submit", () => {
  const onAnswer = vi.fn();
  const { container } = render(<QuestionCard data={asked()} onAnswer={onAnswer} />);
  const options = container.querySelectorAll<HTMLButtonElement>(".card-option");
  fireEvent.click(options[0] as HTMLElement);
  expect(options[0]?.getAttribute("aria-pressed")).toBe("true");
  expect(options[1]?.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(options[1] as HTMLElement);
  expect(options[0]?.getAttribute("aria-pressed")).toBe("false");
  expect(options[1]?.getAttribute("aria-pressed")).toBe("true");
  expect(onAnswer).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Submit"));
  expect(onAnswer).toHaveBeenCalledWith("toolu_q", { "Where should it go?": "00-inbox" }, "");
});

test("Other answers the whole ask, however many questions it carries", () => {
  const onAnswer = vi.fn();
  const { container } = render(<QuestionCard data={twoQuestions()} onAnswer={onAnswer} />);
  fireEvent.click(screen.getByText("Other"));
  const input = container.querySelector(".card-other input") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "Hold both for now" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onAnswer).toHaveBeenCalledWith("toolu_q2", {}, "Hold both for now");
});

/** The CLI bounds a five-hour and a seven-day window at once — the captured frames carry both,
 * five days apart — and the card names whichever one refused the turn. Rendered at 15:00 on a
 * Saturday, "Usage resets at 09:00." for a reset the following Thursday is read as tomorrow
 * morning, so the user comes back on Sunday and is refused again. */
function rateLimitText(now: Date, resetsAt: Date, status = "rejected"): string {
  vi.useFakeTimers({ toFake: ["Date"] }); // Date only: React's scheduler keeps its real timers
  vi.setSystemTime(now);
  const { container } = render(
    <RateLimitCard data={{ status, kind: "seven_day", resets_at: resetsAt.getTime() / 1000 }} />,
  );
  return container.textContent ?? "";
}

test("a reset on another day is dated, so a five-day wait cannot read as tonight", () => {
  const text = rateLimitText(new Date(2026, 8, 12, 15, 0), new Date(2026, 8, 17, 9, 0));
  expect(text).toContain("Usage limit reached");
  expect(text).toContain("Usage resets at 9/17 09:00.");
});

test("a reset later today stays the bare clock time", () => {
  const text = rateLimitText(new Date(2026, 8, 12, 15, 0), new Date(2026, 8, 12, 21, 0));
  expect(text).toContain("Usage resets at 21:00.");
  expect(text).not.toContain("9/");
});

// The warning branch shares the formatter, and its five-hour window crosses midnight often.
test("the warning dates its reset by the same rule", () => {
  const text = rateLimitText(
    new Date(2026, 8, 12, 23, 40),
    new Date(2026, 8, 13, 3, 0),
    "allowed_warning",
  );
  expect(text).toContain("Approaching the usage limit");
  expect(text).toContain("Usage resets at 9/13 03:00.");
});

/** `recoverable` rides on every error and, until now, nothing read it: the resume button was
 * decided by the code alone. No v1 path emits a crash with recoverable=false, so this is the only
 * place the reader exists at all — drop it and the field goes back to being dead payload. */
test("a crash the server says cannot be resumed offers no button", () => {
  const { container } = render(
    <ErrorCard data={{ code: "crashed", message: "", recoverable: false }} onRetry={vi.fn()} />,
  );
  expect(container.textContent).toContain("Claude Code ended unexpectedly");
  expect(container.querySelector("button")).toBeNull();
});

test("a recoverable crash still offers the one button that resumes it", () => {
  const onRetry = vi.fn();
  render(
    <ErrorCard data={{ code: "crashed", message: "", recoverable: true }} onRetry={onRetry} />,
  );
  fireEvent.click(screen.getByText("Send again"));
  expect(onRetry).toHaveBeenCalledTimes(1);
});

/* The durable ledger's own half of the card: a recorded choice replaces the buttons, an
   unheard result disables them, and a save failure leaves them — because choosing again is
   the only way forward from one. Every line below is the card's copy for that state, word for
   word. */

function approvalReceipt(over: Partial<ApprovalReceipt> = {}): ApprovalReceipt {
  return {
    choice_id: "66666666-6666-4666-8666-666666666666",
    session_id: "11111111-1111-4111-8111-111111111111",
    generation: "22222222-2222-4222-8222-222222222222",
    runtime_id: "33333333-3333-4333-8333-333333333333",
    request_id: "44444444-4444-4444-8444-444444444444",
    turn_id: "55555555-5555-4555-8555-555555555555",
    tool_use_id: "toolu_1",
    state: "delivered_locally",
    reason: "",
    created_at: "2026-09-16T00:00:00.000Z",
    updated_at: "2026-09-16T00:00:00.000Z",
    ...over,
  };
}

function view(over: Partial<ApprovalView> = {}): ApprovalView {
  return {
    phase: "recorded",
    receipt: approvalReceipt(),
    choice: { kind: "permission", allow: true },
    refusal: null,
    ...over,
  };
}

test("a second click while recording is a no-op", async () => {
  let finish: (outcome: "applied" | "rejected" | "unknown") => void = () => {};
  const respond = vi.fn(
    () =>
      new Promise<"applied" | "rejected" | "unknown">((resolve) => {
        finish = resolve;
      }),
  );
  render(<PermissionCard data={permission()} onRespond={respond} onOpenUrl={vi.fn()} />);
  const deny = screen.getByRole("button", { name: "Deny" });
  fireEvent.click(deny);
  fireEvent.click(deny);
  fireEvent.click(screen.getByRole("button", { name: "Allow once" }));
  expect(respond).toHaveBeenCalledTimes(1);
  expect(respond).toHaveBeenCalledWith("toolu_1", false);
  await act(async () => {
    finish("applied");
  });
  expect(respond).toHaveBeenCalledTimes(1);
});

test("a recorded card shows the choice and delivery state without buttons", () => {
  const { container } = render(
    <PermissionCard
      data={permission()}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
      approval={view()}
    />,
  );
  expect(screen.queryByText("Allow once", { selector: "button" })).toBeNull();
  expect(screen.queryByText("Deny", { selector: "button" })).toBeNull();
  expect(container.querySelector(".card-actions")).toBeNull();
  const record = container.querySelector(".card-record") as HTMLElement;
  expect(record.getAttribute("role")).toBe("status");
  expect(record.textContent).toContain("Recorded choice: Allow once");
  expect(record.textContent).toContain(
    "Sent to Claude Code. Check the conversation to see whether it ran.",
  );
  // The question, the tool and the reason still read exactly as they did.
  expect(container.querySelector(".card-tool-name")?.textContent).toBe("WebFetch");
});

test("a recorded Deny and a recorded plan choice use each card's own words", () => {
  const { container, rerender } = render(
    <PermissionCard
      data={permission()}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
      approval={view({ choice: { kind: "permission", allow: false } })}
    />,
  );
  expect(container.querySelector(".card-record")?.textContent).toContain("Recorded choice: Deny");
  rerender(
    <PermissionCard
      data={permission({ kind: "plan", tool: "ExitPlanMode", plan: "a plan" })}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
      approval={view()}
    />,
  );
  expect(container.querySelector(".card-record")?.textContent).toContain(
    "Recorded choice: Approve",
  );
  rerender(
    <PermissionCard
      data={permission({ kind: "plan", tool: "ExitPlanMode", plan: "a plan" })}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
      approval={view({ choice: { kind: "permission", allow: false } })}
    />,
  );
  expect(container.querySelector(".card-record")?.textContent).toContain(
    "Recorded choice: Keep planning",
  );
});

test("a recorded choice stays visible while delivery is unknown", () => {
  const { container } = render(
    <PermissionCard
      data={permission()}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
      approval={view({ receipt: approvalReceipt({ state: "unknown", reason: "interrupted" }) })}
    />,
  );
  const record = container.querySelector(".card-record") as HTMLElement;
  expect(record.textContent).toContain("Recorded choice: Allow once");
  expect(record.textContent).toContain("Delivery is unconfirmed. Check the conversation record.");
  expect(container.querySelector(".card-actions")).toBeNull();
});

test("each not_delivered reason says what it actually proves", () => {
  const lines: [string, string][] = [
    ["stale_request", "The request had already ended, so the choice was not delivered."],
    ["storage", "A storage problem prevented delivery. Stop before continuing."],
    ["owner_changed", "Shutdown prevented delivery."],
  ];
  for (const [reason, copy] of lines) {
    const { container, unmount } = render(
      <PermissionCard
        data={permission()}
        onRespond={vi.fn()}
        onOpenUrl={vi.fn()}
        approval={view({ receipt: approvalReceipt({ state: "not_delivered", reason }) })}
      />,
    );
    expect(container.querySelector(".card-record")?.textContent).toContain(copy);
    unmount();
  }
});

test("a recorded question card shows its recorded answers instead of options", () => {
  const { container } = render(
    <QuestionCard
      data={twoQuestions()}
      onAnswer={vi.fn()}
      approval={view({
        choice: {
          kind: "question",
          answers: { "Where should it go?": "30-knowledge", "What about tags?": "No tags" },
          response: "",
        },
        receipt: approvalReceipt({ tool_use_id: "toolu_q2", state: "delivering" }),
      })}
    />,
  );
  expect(container.querySelectorAll(".card-option")).toHaveLength(0);
  expect(container.querySelector(".card-other-toggle")).toBeNull();
  // The questions themselves stay: the record has to be readable next to what was asked.
  expect(container.textContent).toContain("Where should it go?");
  expect(container.querySelector(".card-record")?.textContent).toContain(
    "Recorded answer: 30-knowledge / No tags",
  );
  expect(container.querySelector(".card-record")?.textContent).toContain("Sending to Claude Code.");
});

test("a recorded Other answer is named a reason for declining, and never echoed back", () => {
  const { container } = render(
    <QuestionCard
      data={asked()}
      onAnswer={vi.fn()}
      approval={view({
        choice: { kind: "question", answers: {}, response: "Put it in 70-people" },
      })}
    />,
  );
  const record = container.querySelector(".card-record") as HTMLElement;
  expect(record.textContent).toContain("Recorded answer: Other response");
  expect(record.textContent).toContain(
    "Other text is not a structured choice; it is sent to Claude Code as the reason for declining.",
  );
  // The words themselves live in the ledger, not in a card rebuilt from browser state.
  expect(container.textContent).not.toContain("Put it in 70-people");
});

test("an unheard result says so and stays answerable, so the next click can inspect it", async () => {
  // The store answers such a click with a GET on the retained ID, never a second POST
  // (state/approvals.unit.test.ts pins that half); the card's job is to leave a way back.
  const respond = vi.fn(async () => "unknown" as const);
  render(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      approval={view({ phase: "ambiguous", receipt: null, choice: null })}
    />,
  );
  const allow = screen.getByText("Allow once") as HTMLButtonElement;
  expect(allow.disabled).toBe(false);
  expect((screen.getByText("Deny") as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByRole("status").textContent).toContain(
    "The response delivery result is unknown. Its status will be checked after reconnecting.",
  );
  await act(async () => {
    fireEvent.click(allow);
  });
  expect(respond).toHaveBeenCalledTimes(1);
  // And still answerable afterwards: the result of the inspection is still unheard.
  expect((screen.getByText("Allow once") as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByRole("status").textContent).toContain(
    "The response delivery result is unknown.",
  );
});

test("a recovered answer drops the uncertainty line instead of contradicting the record", async () => {
  const respond = vi.fn(async () => "unknown" as const);
  // Clicked in the one frame before the store's first publish reaches the card, which is the only
  // way a card keeps an "unknown" state of its own while a record exists for it.
  const { rerender } = render(
    <PermissionCard data={permission()} onRespond={respond} onOpenUrl={vi.fn()} />,
  );
  await act(async () => {
    fireEvent.click(screen.getByText("Allow once"));
  });
  expect(screen.getByRole("status").textContent).toContain(
    "The response delivery result is unknown.",
  );

  // session.synced, a later click or a reload finds the record the lost POST did create.
  rerender(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      approval={view()}
    />,
  );
  const statuses = screen.getAllByRole("status").map((node) => node.textContent ?? "");
  expect(statuses.join(" ")).toContain("Recorded choice: Allow once");
  expect(statuses.join(" ")).not.toContain("The response delivery result is unknown.");
});

test("an unheard question answer clears the picks, so an option row is clickable again", async () => {
  const onAnswer = vi.fn(async () => "unknown" as const);
  const { container } = render(
    <QuestionCard
      data={asked()}
      onAnswer={onAnswer}
      approval={view({ phase: "ambiguous", receipt: null, choice: null })}
    />,
  );
  const options = container.querySelectorAll(".card-option");
  fireEvent.click(options[0] as HTMLElement);
  await act(async () => {
    fireEvent.click(screen.getByText("Submit"));
  });
  expect(onAnswer).toHaveBeenCalledTimes(1);
  expect((options[0] as HTMLButtonElement).disabled).toBe(false);
  expect((screen.getByText("Other") as HTMLButtonElement).disabled).toBe(false);
});

test("a save failure leaves the card answerable and says nothing was saved", () => {
  const respond = vi.fn(async () => "applied" as const);
  render(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      approval={view({ phase: "refused", refusal: "save", receipt: null, choice: null })}
    />,
  );
  const allow = screen.getByText("Allow once") as HTMLButtonElement;
  expect(allow.disabled).toBe(false);
  expect(screen.getByRole("status").textContent).toContain(
    "The choice could not be saved, so it was not sent to Claude Code. Check the storage location, then choose again.",
  );
  fireEvent.click(allow);
  expect(respond).toHaveBeenCalledTimes(1);
});

test("a capacity refusal offers no working buttons and does not ask for a retry", () => {
  const respond = vi.fn(async () => "rejected" as const);
  render(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      approval={view({ phase: "refused", refusal: "capacity", receipt: null, choice: null })}
    />,
  );
  expect((screen.getByText("Allow once") as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("status").textContent).toContain(
    "The choice record storage limit was reached, so the choice was not delivered. Stop, reopen VIKI Agent, then reset Choice records in Data.",
  );
  fireEvent.click(screen.getByText("Allow once"));
  expect(respond).not.toHaveBeenCalled();
});

test("a request that is already over offers no working buttons", () => {
  const respond = vi.fn(async () => "rejected" as const);
  render(
    <PermissionCard
      data={permission()}
      onRespond={respond}
      onOpenUrl={vi.fn()}
      approval={view({ phase: "refused", refusal: "over", receipt: null, choice: null })}
    />,
  );
  expect((screen.getByText("Allow once") as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("status").textContent).toContain("This request has already ended.");
  fireEvent.click(screen.getByText("Allow once"));
  expect(respond).not.toHaveBeenCalled();
});

test("the plan chip reads Plan, matching the frozen contract", () => {
  const { container } = render(
    <PermissionCard
      data={permission({ kind: "plan", tool: "ExitPlanMode", plan: "a plan" })}
      onRespond={vi.fn()}
      onOpenUrl={vi.fn()}
    />,
  );
  expect(container.querySelector(".chip")?.textContent).toBe("Plan");
  expect(container.textContent).toContain("Approving exits plan mode and continues.");
});

test("a tool card's chip is unaffected: still Waiting for approval", () => {
  const { container } = render(
    <PermissionCard data={permission()} onRespond={vi.fn()} onOpenUrl={vi.fn()} />,
  );
  expect(container.querySelector(".chip")?.textContent).toBe("Waiting for approval");
});

function questionGroup(container: HTMLElement, index = 0) {
  const group = container.querySelectorAll<HTMLElement>(".card-group")[index];
  if (!group) throw new Error("Missing question group");
  return within(group);
}

test("single picks expose positional selection and progress before the whole ask is answered", async () => {
  const data = twoQuestions();
  const first = data.questions[0];
  if (!first) throw new Error("Missing first question");
  first.options = [
    { label: "Same", description: "first" },
    { label: "Same", description: "second" },
  ];
  const onAnswer = vi.fn(async () => "applied" as const);
  const { container } = render(<QuestionCard data={data} onAnswer={onAnswer} />);
  const choices = questionGroup(container).getAllByRole<HTMLButtonElement>("button");
  const second = choices[1];
  if (!second) throw new Error("Missing second option");
  fireEvent.click(second);
  expect(second.classList.contains("card-option--selected")).toBe(true);
  expect(choices.map((button) => button.getAttribute("aria-pressed"))).toEqual(["false", "true"]);
  expect(choices.every((button) => !button.disabled)).toBe(true);
  expect(screen.getByText("Questions selected: 1 of 2.")).toBeTruthy();
  expect(onAnswer).not.toHaveBeenCalled();
  fireEvent.click(questionGroup(container, 1).getByRole("button", { name: "No tags" }));
  expect(screen.getByText("Questions selected: 2 of 2.")).toBeTruthy();
  expect(onAnswer).not.toHaveBeenCalled();
  await act(async () => fireEvent.click(screen.getByText("Submit")));
  expect(onAnswer).toHaveBeenCalledExactlyOnceWith(
    "toolu_q2",
    { "Where should it go?": "Same", "What about tags?": "No tags" },
    "",
  );
});

test.each(["rejected", "unknown", "recovery"] as const)(
  "%s clears completed multi-selection and late recovery cannot erase a new draft pick",
  async (boundary) => {
    let finish!: (result: "applied" | "rejected" | "unknown") => void;
    const onAnswer = vi.fn(
      () =>
        new Promise<"applied" | "rejected" | "unknown">((resolve) => {
          finish = resolve;
        }),
    );
    const data = asked({ multi_select: true });
    const props = {
      data,
      onAnswer,
      recoveryKey: "first",
      ...(boundary === "unknown"
        ? { approval: view({ phase: "ambiguous", receipt: null, choice: null }) }
        : {}),
    };
    const { container, rerender } = render(<QuestionCard {...props} />);
    const controls = questionGroup(container);
    const choices = controls
      .getAllByRole<HTMLButtonElement>("button")
      .filter((button) => button.classList.contains("card-option"));
    for (const choice of [...choices].reverse()) fireEvent.click(choice);
    expect(choices.every((button) => button.classList.contains("card-option--selected"))).toBe(
      true,
    );
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith(
      "toolu_q",
      { "Where should it go?": "30-knowledge, 00-inbox" },
      "",
    );
    if (boundary === "recovery") rerender(<QuestionCard {...props} recoveryKey="recovered" />);
    else await act(async () => finish(boundary));
    expect(choices.every((button) => !button.classList.contains("card-option--selected"))).toBe(
      true,
    );
    expect(screen.getByText("Questions selected: 0 of 1.")).toBeTruthy();
    expect(
      choices.every(
        (button) => button.getAttribute("aria-pressed") === "false" && !button.disabled,
      ),
    ).toBe(true);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Submit" }).disabled).toBe(true);
    const choice = choices[1];
    if (!choice) throw new Error("Missing second choice");
    fireEvent.click(choice);
    if (boundary === "recovery") await act(async () => finish("rejected"));
    expect(choice.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText("Questions selected: 1 of 1.")).toBeTruthy();
    expect(onAnswer).toHaveBeenCalledTimes(1);
  },
);

test("a recorded choice replaces local progress with the authoritative record", () => {
  const data = twoQuestions();
  const onAnswer = vi.fn();
  const { container, rerender } = render(<QuestionCard data={data} onAnswer={onAnswer} />);
  fireEvent.click(screen.getByText("30-knowledge"));
  expect(screen.getByText("Questions selected: 1 of 2.")).toBeTruthy();
  rerender(
    <QuestionCard
      data={data}
      onAnswer={onAnswer}
      approval={view({
        choice: { kind: "question", answers: { "Where should it go?": "00-inbox" }, response: "" },
      })}
    />,
  );
  expect(container.querySelector(".card-progress")).toBeNull();
  expect(container.querySelectorAll(".card-option")).toHaveLength(0);
  expect(container.querySelector(".card-record")?.textContent).toContain("00-inbox");
  expect(onAnswer).not.toHaveBeenCalled();
});

test("disabled questions expose no false selection and send nothing", () => {
  const onAnswer = vi.fn();
  const { container } = render(<QuestionCard data={asked()} onAnswer={onAnswer} disabled />);
  for (const button of questionGroup(container).getAllByRole<HTMLButtonElement>("button")) {
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(button);
  }
  expect(screen.getByText("Questions selected: 0 of 1.")).toBeTruthy();
  expect(onAnswer).not.toHaveBeenCalled();
});

function otherTrigger(container: HTMLElement) {
  const trigger = container.querySelector<HTMLButtonElement>(".card-other-toggle");
  if (!trigger) throw new Error("Missing Other trigger");
  return trigger;
}
function otherControls() {
  const input = screen.getByRole<HTMLInputElement>("textbox", {
    name: "Other response (decline reason)",
  });
  const section = input.closest<HTMLElement>(".card-other");
  if (!section) throw new Error("Missing Other controls");
  return { input, controls: within(section) };
}

test("while Other is open the card-level Submit is not shown, and Cancel brings it back", () => {
  const { container } = render(<QuestionCard data={asked()} onAnswer={vi.fn()} />);
  expect(container.querySelector(".card > .card-actions .btn.go")).not.toBeNull();
  fireEvent.click(screen.getByText("Other"));
  expect(container.querySelector(".card > .card-actions")).toBeNull();
  fireEvent.click(screen.getByText("Cancel"));
  expect(container.querySelector(".card > .card-actions .btn.go")).not.toBeNull();
  expect(screen.getByText("Submit")).toBeTruthy();
});

test("Other Cancel keeps local text and prior choices while restoring its trigger", () => {
  const onAnswer = vi.fn();
  const { container } = render(<QuestionCard data={twoQuestions()} onAnswer={onAnswer} />);
  fireEvent.click(screen.getByText("30-knowledge"));
  fireEvent.click(otherTrigger(container));
  const { input, controls } = otherControls();
  expect(document.activeElement).toBe(input);
  expect(controls.getByRole("button", { name: "Submit" })).toBeTruthy();
  fireEvent.change(input, { target: { value: "Hold both for now" } });
  fireEvent.click(controls.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(document.activeElement).toBe(otherTrigger(container));
  expect(screen.getByText("Questions selected: 1 of 2.")).toBeTruthy();
  expect(
    questionGroup(container)
      .getByRole("button", { name: "30-knowledge" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  fireEvent.click(otherTrigger(container));
  expect(otherControls().input.value).toBe("Hold both for now");
  expect(onAnswer).not.toHaveBeenCalled();
});

test("Other Submit trims the existing whole-ask decline payload and pending Cancel cannot reset it", async () => {
  let finish!: (result: "applied" | "rejected" | "unknown") => void;
  const onAnswer = vi.fn(
    () =>
      new Promise<"applied" | "rejected" | "unknown">((resolve) => {
        finish = resolve;
      }),
  );
  const { container } = render(<QuestionCard data={twoQuestions()} onAnswer={onAnswer} />);
  fireEvent.click(screen.getByText("30-knowledge"));
  fireEvent.click(otherTrigger(container));
  const { input, controls } = otherControls();
  const submit = controls.getByRole<HTMLButtonElement>("button", { name: "Submit" });
  const cancel = controls.getByRole<HTMLButtonElement>("button", { name: "Cancel" });
  fireEvent.change(input, { target: { value: "  \n " } });
  fireEvent.click(submit);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(submit.disabled).toBe(true);
  expect(onAnswer).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "  Put it in 70-people  " } });
  act(() => {
    fireEvent.click(submit);
    fireEvent.click(cancel);
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(screen.getByText("No tags"));
  });
  expect(onAnswer).toHaveBeenCalledExactlyOnceWith("toolu_q2", {}, "Put it in 70-people");
  expect(otherControls().input).toBe(input);
  expect([input.disabled, submit.disabled, cancel.disabled]).toEqual([true, true, true]);
  expect(screen.getByText("Questions selected: 0 of 2.")).toBeTruthy();
  await act(async () => finish("rejected"));
  expect([input.disabled, submit.disabled, cancel.disabled]).toEqual([false, false, false]);
  expect(input.value).toBe("  Put it in 70-people  ");
  fireEvent.click(cancel);
  expect(document.activeElement).toBe(otherTrigger(container));
  expect(onAnswer).toHaveBeenCalledTimes(1);
});

test.each(["disabled", "posting", "over", "capacity", "recorded"] as const)(
  "Other controls respect authoritative %s without submitting or echoing text",
  (state) => {
    const data = asked();
    const onAnswer = vi.fn();
    const { container, rerender } = render(<QuestionCard data={data} onAnswer={onAnswer} />);
    fireEvent.click(otherTrigger(container));
    const { input, controls } = otherControls();
    fireEvent.change(input, { target: { value: "Unsent text" } });
    let approval = view({
      choice: { kind: "question", answers: {}, response: "Recorded decline" },
    });
    if (state === "posting") approval = view({ phase: "posting", choice: null, receipt: null });
    else if (state === "over" || state === "capacity")
      approval = view({ phase: "refused", refusal: state, choice: null, receipt: null });
    rerender(
      <QuestionCard
        data={data}
        onAnswer={onAnswer}
        {...(state === "disabled" ? { disabled: true } : { approval })}
      />,
    );
    if (state === "recorded") {
      expect(screen.queryByRole("textbox")).toBeNull();
      expect(container.textContent).not.toContain("Unsent text");
      expect(container.textContent).not.toContain("Recorded decline");
      expect(container.querySelectorAll(".card-option")).toHaveLength(0);
    } else {
      expect(input.disabled).toBe(true);
      for (const button of controls.getAllByRole<HTMLButtonElement>("button")) {
        expect(button.disabled).toBe(true);
        fireEvent.click(button);
      }
      fireEvent.keyDown(input, { key: "Enter" });
      expect(otherControls().input).toBe(input);
    }
    expect(onAnswer).not.toHaveBeenCalled();
  },
);

test("Other Enter preserves IME guards and a recovered old result cannot unlock a new answer", async () => {
  let firstFinish!: (result: "applied" | "rejected" | "unknown") => void;
  let nextFinish!: (result: "applied" | "rejected" | "unknown") => void;
  const onAnswer = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          firstFinish = resolve;
        }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          nextFinish = resolve;
        }),
    );
  const data = asked();
  const { container, rerender } = render(
    <QuestionCard data={data} onAnswer={onAnswer} recoveryKey="first" />,
  );
  fireEvent.click(otherTrigger(container));
  const { input, controls } = otherControls();
  fireEvent.change(input, { target: { value: "  first try  " } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
  expect(onAnswer).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onAnswer).toHaveBeenCalledExactlyOnceWith("toolu_q", {}, "first try");
  rerender(<QuestionCard data={data} onAnswer={onAnswer} recoveryKey="recovered" />);
  expect(input.disabled).toBe(false);
  fireEvent.change(input, { target: { value: "  second try  " } });
  fireEvent.click(controls.getByRole("button", { name: "Submit" }));
  expect(onAnswer).toHaveBeenCalledTimes(2);
  expect(onAnswer).toHaveBeenLastCalledWith("toolu_q", {}, "second try");
  await act(async () => firstFinish("rejected"));
  expect(input.disabled).toBe(true);
  expect(controls.getByRole<HTMLButtonElement>("button", { name: "Cancel" }).disabled).toBe(true);
  expect(onAnswer).toHaveBeenCalledTimes(2);
  await act(async () => nextFinish("applied"));
});

type CardKind = "permission" | "question";

const targetPermission = (tool_use_id = "toolu_permission"): PermissionRequestedData => ({
  tool_use_id,
  kind: "tool",
  tool: "Read",
  summary: "notes/example.md",
  question: "May I read this note?",
  reason: "",
  agent: "",
  plan: "",
});

const targetQuestion = (tool_use_id = "toolu_question"): QuestionAskedData => ({
  tool_use_id,
  questions: [
    {
      header: "Destination",
      question: "Where should it go?",
      options: [{ label: "Inbox", description: "" }],
      multi_select: false,
    },
  ],
});

function targetCard(
  kind: CardKind,
  toolUseId: string,
  target = true,
  targetRequest = 1,
  data?: PermissionRequestedData | QuestionAskedData,
) {
  const targetProps = target ? { target, targetRequest } : {};
  const cardData =
    data ?? (kind === "permission" ? targetPermission(toolUseId) : targetQuestion(toolUseId));
  return kind === "permission" ? (
    <PermissionCard
      data={cardData as PermissionRequestedData}
      onRespond={vi.fn(async () => "applied" as const)}
      onOpenUrl={vi.fn()}
      {...targetProps}
    />
  ) : (
    <QuestionCard
      data={cardData as QuestionAskedData}
      onAnswer={vi.fn(async () => "applied" as const)}
      {...targetProps}
    />
  );
}

let targetScroll = vi.fn();
let priorScrollDescriptor: PropertyDescriptor | undefined;

beforeEach(() => {
  priorScrollDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  targetScroll = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: targetScroll,
  });
});

afterEach(() => {
  delete document.documentElement.dataset.motion;
  if (priorScrollDescriptor) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", priorScrollDescriptor);
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
  }
  vi.unstubAllGlobals();
});

test.each(["permission", "question"] as const)(
  "%s card focuses and scrolls its exact request when targeted",
  (kind) => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false })),
    );
    render(targetCard(kind, "toolu_exact"));

    const target = document.querySelector<HTMLElement>(".card");
    expect(target?.getAttribute("data-tool-use-id")).toBe("toolu_exact");
    expect(target?.tabIndex).toBe(-1);
    expect(document.activeElement).toBe(target);
    expect(targetScroll).toHaveBeenCalledExactlyOnceWith({
      block: "center",
      behavior: "smooth",
    });
  },
);

test.each(["permission", "question"] as const)(
  "%s card focuses again for a new Open request serial on the same card",
  (kind) => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false })),
    );
    const data =
      kind === "permission" ? targetPermission("toolu_same") : targetQuestion("toolu_same");
    const { rerender } = render(targetCard(kind, "toolu_same", true, 1, data));
    const target = document.querySelector<HTMLElement>(".card");
    const elsewhere = document.createElement("button");
    target?.before(elsewhere);
    elsewhere.focus();
    expect(document.activeElement).toBe(elsewhere);

    rerender(targetCard(kind, "toolu_same", true, 2, data));

    expect(document.activeElement).toBe(target);
    expect(targetScroll).toHaveBeenCalledTimes(2);
  },
);

test.each(["permission", "question"] as const)(
  "%s card targets a changed tool-use ID when request serial stays the same",
  (kind) => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false })),
    );
    const { rerender } = render(targetCard(kind, "toolu_old", true, 1));
    const target = document.querySelector<HTMLElement>(".card");
    const elsewhere = document.createElement("button");
    target?.before(elsewhere);
    elsewhere.focus();

    rerender(targetCard(kind, "toolu_new", true, 1));

    const newTarget = document.querySelector<HTMLElement>(".card");
    expect(newTarget?.getAttribute("data-tool-use-id")).toBe("toolu_new");
    expect(document.activeElement).toBe(newTarget);
    expect(targetScroll).toHaveBeenCalledTimes(2);
  },
);

test.each(["permission", "question"] as const)(
  "%s card stays unfocused without a target",
  (kind) => {
    render(
      <>
        <button type="button">Keep focus</button>
        {targetCard(kind, "toolu_idle", false)}
      </>,
    );
    const button = screen.getByRole("button", { name: "Keep focus" });
    act(() => button.focus());

    expect(document.activeElement).toBe(button);
    expect(document.querySelector(".card")?.hasAttribute("data-tool-use-id")).toBe(false);
    expect(targetScroll).not.toHaveBeenCalled();
  },
);

test("OS and Appearance reduced-motion settings use automatic scroll", () => {
  const matchMedia = vi.fn(() => ({ matches: true }));
  vi.stubGlobal("matchMedia", matchMedia);
  const { rerender } = render(targetCard("permission", "toolu_os"));
  expect(matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
  expect(targetScroll).toHaveBeenLastCalledWith({ block: "center", behavior: "auto" });

  targetScroll.mockClear();
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: false })),
  );
  document.documentElement.dataset.motion = "reduce";
  rerender(targetCard("permission", "toolu_setting", true, 2));

  expect(targetScroll).toHaveBeenCalledExactlyOnceWith({ block: "center", behavior: "auto" });
});

test("targeting tolerates jsdom without scrollIntoView and still focuses the card", () => {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: undefined,
  });
  expect(() => render(targetCard("question", "toolu_no_scroll"))).not.toThrow();
  expect(document.activeElement).toBe(document.querySelector(".card"));
});
