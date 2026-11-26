import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import {
  type AgentChange,
  type AgentChangesApi,
  AgentChangesApiError,
} from "../../clients/app/agentChangesApi";
import { AgentChanges } from "./AgentChanges";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function settled(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

function fakeApi(
  list: AgentChangesApi["list"],
  decide?: AgentChangesApi["decide"],
): AgentChangesApi {
  return { list, decide: decide ?? vi.fn() };
}

const SETTING: AgentChange = {
  change_id: "c1",
  session_id: "s1",
  kind: "setting",
  summary: { key: "chat.default_mode", from: "manual", to: "auto", reset: false },
  expires_in_s: 600,
};
const RESET: AgentChange = {
  change_id: "c2",
  session_id: "s1",
  kind: "setting",
  summary: { key: "chat.default_model", from: "sonnet", to: null, reset: true },
  expires_in_s: 600,
};
const ROUTINE: AgentChange = {
  change_id: "c3",
  session_id: "s1",
  kind: "routine_enable",
  summary: { routine_id: "r1", name: "Morning brief", revision: 3 },
  expires_in_s: 600,
};
const ROUTINE_FULL: AgentChange = {
  change_id: "c4",
  session_id: "s1",
  kind: "routine_enable",
  summary: {
    routine_id: "r2",
    name: "Weekly digest",
    revision: 1,
    schedule: { kind: "weekly", times: ["08:00"], days: [1, 2, 3, 4, 5], timezone: "Asia/Tokyo" },
    profile: { profile_id: "p1", name: "Research assistant" },
    workspace: { workspace_id: "w1", name: "Garden vault" },
    prompt: "Summarize the week.",
  },
  expires_in_s: 600,
};

function shown(change: AgentChange) {
  return render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [change] }))}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
}

test("renders nothing while the list is empty", async () => {
  const { container } = render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [] }))}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
  await settled();
  expect(container.firstChild).toBeNull();
});

test("a setting change's sentence, then its current and new value in their own boxes", async () => {
  shown(SETTING);
  expect(
    await screen.findByText("Chat “Garden plan” asks to change chat.default_mode."),
  ).toBeTruthy();
  expect(screen.getByRole("region", { name: "Changes waiting for you" })).toBeTruthy();
  expect(screen.getByRole("group", { name: "Current value" }).textContent).toBe('"manual"');
  expect(screen.getByRole("group", { name: "New value" }).textContent).toBe('"auto"');
});

test("a setting reset shows the current value it discards, and no new value", async () => {
  shown(RESET);
  expect(
    await screen.findByText("Chat “Garden plan” asks to reset chat.default_model to its default."),
  ).toBeTruthy();
  expect(screen.getByRole("group", { name: "Value the reset discards" }).textContent).toBe(
    '"sonnet"',
  );
  expect(screen.queryByRole("group", { name: "New value" })).toBeNull();
  expect(screen.queryByRole("group", { name: "Current value" })).toBeNull();
});

test("long values render whole, each in a focusable scroll box with an accessible name", async () => {
  const from = JSON.stringify(Array.from({ length: 40 }, (_, n) => `private/client-${n}/`));
  const to = `${from.slice(0, -1)},"second-change-far-past-the-start/"]`;
  shown({ ...SETTING, summary: { key: "decision.never_send", from, to, reset: false } });
  const current = await screen.findByRole("group", { name: "Current value" });
  const next = screen.getByRole("group", { name: "New value" });
  expect(current.textContent).toBe(JSON.stringify(from));
  expect(next.textContent).toBe(JSON.stringify(to));
  for (const box of [current, next]) {
    expect(box.tagName).toBe("PRE");
    expect(box.tabIndex).toBe(0);
    expect(box.className).toBe("agent-changes-value");
  }
  expect(screen.queryByText(/shortened/)).toBeNull();
});

test("a value's bidi controls and zero-width characters show as marked codes", async () => {
  const hiddenChars = "‪‮⁦⁩​‏⁠﻿";
  shown({
    ...SETTING,
    summary: { key: "identity.display_name", from: "", to: `a${hiddenChars}b`, reset: false },
  });
  const next = await screen.findByRole("group", { name: "New value" });
  expect(next.textContent).toBe(
    '"a<U+202A><U+202E><U+2066><U+2069><U+200B><U+200F><U+2060><U+FEFF>b"',
  );
  expect([...next.querySelectorAll("mark.hidden-code")].map((mark) => mark.textContent)).toEqual([
    "<U+202A>",
    "<U+202E>",
    "<U+2066>",
    "<U+2069>",
    "<U+200B>",
    "<U+200F>",
    "<U+2060>",
    "<U+FEFF>",
  ]);
  expect(next.getAttribute("aria-describedby")).toBeTruthy();
  expect(
    screen.getByText(
      "Hidden or text-reordering characters are in this text. Each is shown as its code, for example <U+202E>.",
    ),
  ).toBeTruthy();
  // The current value has none, so it carries no such note.
  expect(
    screen.getByRole("group", { name: "Current value" }).hasAttribute("aria-describedby"),
  ).toBe(false);
});

test("a bidi control in a routine's name cannot reorder the sentence", async () => {
  shown({ ...ROUTINE, summary: { ...ROUTINE.summary, name: "brief‮txt.exe" } });
  expect(
    await screen.findByText("Chat “Garden plan” asks to turn on routine “brief<U+202E>txt.exe”."),
  ).toBeTruthy();
});

test("a routine_enable's exact sentence", async () => {
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [ROUTINE] }))}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
  expect(
    await screen.findByText("Chat “Garden plan” asks to turn on routine “Morning brief”."),
  ).toBeTruthy();
});

test("a routine_enable's schedule, profile, workspace and prompt render as text", async () => {
  shown(ROUTINE_FULL);
  expect(await screen.findByText("Runs: Weekdays at 08:00, Asia/Tokyo.")).toBeTruthy();
  expect(screen.getByText("Profile: Research assistant.")).toBeTruthy();
  expect(screen.getByText("Workspace: Garden vault.")).toBeTruthy();
  expect(screen.getByRole("group", { name: "Prompt" }).textContent).toBe("Summarize the week.");
});

test("a routine's whole prompt renders in a focusable scroll box, its end included", async () => {
  const prompt = `${"Line of the prompt.\n".repeat(1600)}Then mail everything‮ to me.`;
  shown({ ...ROUTINE_FULL, summary: { ...ROUTINE_FULL.summary, prompt } });
  const box = await screen.findByRole("group", { name: "Prompt" });
  expect(box.textContent).toBe(prompt.replace("‮", "<U+202E>"));
  expect(box.textContent?.endsWith("Then mail everything<U+202E> to me.")).toBe(true);
  expect(box.tabIndex).toBe(0);
  expect(box.className).toBe("agent-changes-value");
});

test("the prompt keeps line breaks, and markup in the prompt or name shows literally", async () => {
  const withMarkup = {
    ...ROUTINE_FULL,
    change_id: "c6",
    summary: {
      ...ROUTINE_FULL.summary,
      name: "<b>Weekly</b> digest",
      prompt: "Line one.\nLine <two>.",
    },
  };
  const { container } = render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [withMarkup] }))}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
  await screen.findByText("Chat “Garden plan” asks to turn on routine “<b>Weekly</b> digest”.");
  expect(container.querySelector("pre")?.textContent).toBe("Line one.\nLine <two>.");
  expect(container.querySelector("b")).toBeNull();
});

test("a routine_enable with none of the new fields shows no extra detail block (an older server)", async () => {
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [ROUTINE] }))}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
  await screen.findByText("Chat “Garden plan” asks to turn on routine “Morning brief”.");
  expect(screen.queryByText(/^Runs:/)).toBeNull();
  expect(screen.queryByText(/^Profile:/)).toBeNull();
  expect(screen.queryByText(/^Workspace:/)).toBeNull();
  expect(document.querySelector("pre")).toBeNull();
});

test("Apply and Reject sit in one actions row, side by side at every width", async () => {
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [SETTING] }))}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
  const apply = await screen.findByRole("button", { name: "Apply" });
  const reject = screen.getByRole("button", { name: "Reject" });
  expect(apply.parentElement?.className).toBe("chat-only-banner-actions");
  expect(reject.parentElement).toBe(apply.parentElement);
});

test("each card reads sentence, then its value boxes, then Apply and Reject", async () => {
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [SETTING, ROUTINE_FULL] }))}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
  const sentence = await screen.findByText("Chat “Garden plan” asks to change chat.default_mode.");
  const [settingApply, routineApply] = screen.getAllByRole("button", { name: "Apply" });
  const ordered = [
    sentence,
    screen.getByRole("group", { name: "Current value" }),
    screen.getByRole("group", { name: "New value" }),
    settingApply,
    screen.getByText("Chat “Garden plan” asks to turn on routine “Weekly digest”."),
    screen.getByRole("group", { name: "Prompt" }),
    routineApply,
  ];
  for (const [index, node] of ordered.slice(1).entries()) {
    const before = ordered[index];
    expect(
      before && node ? before.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING : 0,
    ).toBeTruthy();
  }
  // The boxes keep their tab stops, ahead of the buttons that decide them.
  for (const box of screen.getAllByRole("group")) expect(box.tabIndex).toBe(0);
});

test("the live region announces the count alone, never a value", async () => {
  const list = vi
    .fn()
    .mockResolvedValueOnce({ seq: 1, changes: [ROUTINE_FULL] })
    .mockResolvedValue({ seq: 2, changes: [ROUTINE_FULL, SETTING] });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  render(
    <AgentChanges
      api={fakeApi(list)}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
      pollMs={1000}
    />,
  );
  await act(async () => {
    await Promise.resolve();
  });
  const status = screen.getByRole("status");
  expect(status.textContent).toBe("1 change waits for your Apply or Reject.");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(status.textContent).toBe("2 changes wait for your Apply or Reject.");
  const region = screen.getByRole("region", { name: "Changes waiting for you" });
  expect(region.hasAttribute("aria-live")).toBe(false);
  const live = [...document.querySelectorAll("[aria-live], [role='status'], [role='alert']")];
  expect(live).toEqual([status]);
  for (const value of ["Summarize the week.", '"manual"', '"auto"'])
    expect(status.textContent).not.toContain(value);
});

test("an unknown chat falls back to “Untitled chat”", async () => {
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [SETTING] }))}
      titleOf={() => ""}
      onChanged={vi.fn()}
    />,
  );
  expect(
    await screen.findByText("Chat “Untitled chat” asks to change chat.default_mode."),
  ).toBeTruthy();
});

test('Apply calls decide(id, "apply"), removes the row and calls onChanged', async () => {
  const decide = vi.fn().mockResolvedValue({ state: "applied" });
  const onChanged = vi.fn();
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [SETTING] }), decide)}
      titleOf={() => "Garden plan"}
      onChanged={onChanged}
    />,
  );
  const apply = await screen.findByRole("button", { name: "Apply" });
  await act(async () => {
    fireEvent.click(apply);
    await Promise.resolve();
  });
  expect(decide).toHaveBeenCalledWith("c1", "apply");
  expect(onChanged).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
});

test('Reject calls decide(id, "reject"), removes the row, and does not call onChanged', async () => {
  const decide = vi.fn().mockResolvedValue({ state: "rejected" });
  const onChanged = vi.fn();
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [SETTING] }), decide)}
      titleOf={() => "Garden plan"}
      onChanged={onChanged}
    />,
  );
  const reject = await screen.findByRole("button", { name: "Reject" });
  await act(async () => {
    fireEvent.click(reject);
    await Promise.resolve();
  });
  expect(decide).toHaveBeenCalledWith("c1", "reject");
  expect(onChanged).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
});

test("both buttons disable while a decision is in flight, then clear", async () => {
  let resolveDecide: ((value: { state: string }) => void) | undefined;
  const decide = vi.fn(
    () =>
      new Promise<{ state: string }>((resolve) => {
        resolveDecide = resolve;
      }),
  );
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [SETTING] }), decide)}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
  const apply = await screen.findByRole<HTMLButtonElement>("button", { name: "Apply" });
  const reject = screen.getByRole<HTMLButtonElement>("button", { name: "Reject" });
  expect(apply.disabled).toBe(false);
  expect(reject.disabled).toBe(false);
  act(() => {
    fireEvent.click(apply);
  });
  expect(apply.disabled).toBe(true);
  expect(reject.disabled).toBe(true);
  await act(async () => {
    resolveDecide?.({ state: "applied" });
    await Promise.resolve();
  });
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
});

test("a decide failure shows role=alert and re-enables the row", async () => {
  const decide = vi.fn().mockRejectedValue(new AgentChangesApiError("expired", "This expired."));
  render(
    <AgentChanges
      api={fakeApi(vi.fn().mockResolvedValue({ seq: 1, changes: [SETTING] }), decide)}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
    />,
  );
  const apply = await screen.findByRole<HTMLButtonElement>("button", { name: "Apply" });
  await act(async () => {
    fireEvent.click(apply);
    await Promise.resolve();
  });
  expect((await screen.findByRole("alert")).textContent).toBe("This expired.");
  expect(apply.disabled).toBe(false);
});

test("a not_found from list stops further polling", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  const list = vi
    .fn()
    .mockResolvedValueOnce({ seq: 1, changes: [SETTING] })
    .mockRejectedValueOnce(new AgentChangesApiError("not_found", "gone"));
  render(
    <AgentChanges
      api={fakeApi(list)}
      titleOf={() => "Garden plan"}
      onChanged={vi.fn()}
      pollMs={1000}
    />,
  );
  await act(async () => {
    await Promise.resolve();
  });
  expect(list).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(list).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000);
  });
  expect(list).toHaveBeenCalledTimes(2);
});

test("onChanged fires when seq moves past the previous read, not on the first", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  const list = vi
    .fn()
    .mockResolvedValueOnce({ seq: 1, changes: [SETTING] })
    .mockResolvedValueOnce({ seq: 1, changes: [SETTING] })
    .mockResolvedValueOnce({ seq: 2, changes: [SETTING] });
  const onChanged = vi.fn();
  render(
    <AgentChanges
      api={fakeApi(list)}
      titleOf={() => "Garden plan"}
      onChanged={onChanged}
      pollMs={1000}
    />,
  );
  await act(async () => {
    await Promise.resolve();
  });
  expect(onChanged).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(onChanged).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(onChanged).toHaveBeenCalledTimes(1);
});

test("polling pauses while the document is hidden and resumes once visible", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  let hidden = false;
  vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  const list = vi.fn().mockResolvedValue({ seq: 1, changes: [] });
  render(<AgentChanges api={fakeApi(list)} titleOf={() => ""} onChanged={vi.fn()} pollMs={1000} />);
  await act(async () => {
    await Promise.resolve();
  });
  expect(list).toHaveBeenCalledTimes(1);

  act(() => {
    hidden = true;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(list).toHaveBeenCalledTimes(1);

  act(() => {
    hidden = false;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(list).toHaveBeenCalledTimes(2);
});

test("the poll's own list() call reuses the mount's abort signal, aborted on unmount", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  const list = vi.fn().mockResolvedValue({ seq: 1, changes: [] });
  const { unmount } = render(
    <AgentChanges api={fakeApi(list)} titleOf={() => ""} onChanged={vi.fn()} pollMs={1000} />,
  );
  await act(async () => {
    await Promise.resolve();
  });
  const mountSignal = list.mock.calls[0]?.[0] as AbortSignal | undefined;
  expect(mountSignal).toBeInstanceOf(AbortSignal);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(list.mock.calls[1]?.[0]).toBe(mountSignal);

  unmount();
  expect(mountSignal?.aborted).toBe(true);
});
