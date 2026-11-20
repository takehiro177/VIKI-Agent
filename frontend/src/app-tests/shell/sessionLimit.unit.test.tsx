/**
 * The live-session cap as App actually wires it, not as any one surface renders it in isolation:
 * the settings document (when it carries the key) decides what New session and the new-chat send
 * gate on, ahead of the server's reported snapshot value and the built-in 3; a live PATCH result
 * moves that gate with no remount; and "Change limit" — from the toast, from a too_many_sessions
 * error card, and VIKI's own chat's refusal line — all land on the same Settings › Chat field,
 * already focused. The resolver's own rules live in state/live.unit.test.ts, the toast's two
 * frozen sentences in accept/m3c and the frozen 3-with-nothing-set default in accept/m4b; this
 * file is only the seam between App and the document.
 */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { App } from "../../App";
import { fakeRest, fakeViki, GENERATION } from "../../accept/m3b/fakes";
import type { KeySpec, SettingsView as SettingsDocument } from "../../clients/app/shellApi";
import { createFakeDecisionLoopApi } from "../../clients/decisionLoop/fake";
import { t } from "../../i18n/t";
import type { Envelope, EventMap } from "../../protocol";
import { fakeDurable } from "../../testDurable";
import {
  capabilitiesFixture,
  type DeferredShellApi,
  deferredShellApi,
  type FakeShellApi,
  fakeShellApi,
  listingFixture,
  settingsFixture,
} from "../../testShell";

afterEach(cleanup);

function frame<K extends keyof EventMap>(
  session: string,
  seq: number,
  type: K,
  data: EventMap[K],
): Envelope {
  return {
    v: 2,
    generation: GENERATION,
    request_id: "",
    type,
    session_id: session,
    seq,
    data,
  } as Envelope;
}

// `chat.max_live_sessions` never joins testShell's SPECS (a frozen App test depends on its
// absence), so each case here adds it to the fixture's document itself, as the server would send
// it, with onboarding already complete so App opens straight on the chat view rather than onto
// the create/open cards (shell_only, zero registered workspaces, onboarding.completed !== true).
const BOUNDED_SPEC: KeySpec = {
  scope: "app",
  applies: "now",
  kind: "number",
  allowed: null,
  default: 30,
  personal: false,
  bounds: [1, 999],
};

function documentWithLimit(value: number): SettingsDocument {
  const base = settingsFixture({ "onboarding.completed": true });
  return {
    ...base,
    effective: { ...base.effective, "chat.max_live_sessions": value },
    sources: { ...base.sources, "chat.max_live_sessions": "setting" },
    keys: { ...base.keys, "chat.max_live_sessions": BOUNDED_SPEC },
  };
}

function shellWithLimit(value: number): FakeShellApi {
  return fakeShellApi({ settings: documentWithLimit(value) });
}

/** Resolves every pending deferred call of `method`: `start()`'s own first read and the one its
 * socket-open re-check fires both land in `pending`, and only the generation check decides which
 * of the two answers a late resolve of the other would be ignored for — resolving both is simpler
 * than guessing which one is still live. */
function resolvePending(api: DeferredShellApi, method: string, value: unknown): void {
  for (const call of api.pending) if (call.method === method) call.resolve(value);
}

function mount(
  options: {
    shell?: FakeShellApi;
    decisionApi?: ReturnType<typeof createFakeDecisionLoopApi>;
  } = {},
) {
  const { viki, socket } = fakeViki();
  const durable = fakeDurable();
  const utils = render(
    <App
      viki={viki}
      rest={fakeRest()}
      durable={durable}
      {...(options.shell ? { shell: options.shell } : {})}
      {...(options.decisionApi ? { decisionApi: options.decisionApi } : {})}
    />,
  );
  act(() => socket.open());
  socket.sent.length = 0;
  return { ...utils, socket };
}

async function settled(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 8; i++) await Promise.resolve();
  });
}

const sessionOpens = (socket: ReturnType<typeof mount>["socket"]) =>
  socket.sent.filter((sent) => sent.type === "session.open");

const newSessionButton = () => screen.getByText("New session");

async function click(element: Element): Promise<void> {
  await act(async () => {
    fireEvent.click(element);
  });
}

test("a document value of 5 with no reported snapshot gates New session at 5, not the built-in 3", async () => {
  const { socket } = mount({ shell: shellWithLimit(5) });
  await settled();

  for (let i = 1; i <= 5; i++) {
    await click(newSessionButton());
    expect(sessionOpens(socket)).toHaveLength(i);
  }
  await click(newSessionButton());
  expect(sessionOpens(socket)).toHaveLength(5);
  expect(document.querySelector(".toast")?.textContent).toContain(
    "Up to 5 sessions can run at once",
  );
});

test("a PATCH result the live document adopts gates the very next start, with no remount", async () => {
  const { socket } = mount({ shell: shellWithLimit(5) });
  await settled();
  await click(newSessionButton());
  await click(newSessionButton());
  expect(sessionOpens(socket)).toHaveLength(2);

  await click(screen.getByRole("button", { name: "App menu" }));
  await click(screen.getByRole("menuitem", { name: /^Settings\s*Ctrl/ }));
  await click(screen.getByRole("tab", { name: "Chat" }));
  const field = screen.getByLabelText("Sessions running at once");
  fireEvent.change(field, { target: { value: "2" } });
  await act(async () => {
    fireEvent.blur(field);
    await Promise.resolve();
    await Promise.resolve();
  });

  // New session always opens Chat first when a non-chat view is showing (handleNewSession's own
  // rule), so clicking it here from the Chat settings page proves the live gate with no extra
  // navigation step of this test's own.
  await click(newSessionButton());
  expect(sessionOpens(socket)).toHaveLength(2);
  expect(document.querySelector(".toast")?.textContent).toContain(
    "Up to 2 sessions can run at once",
  );
});

test("the toast's Change limit opens Settings › Chat with the field focused, and the toast is gone", async () => {
  const { socket } = mount({ shell: shellWithLimit(1) });
  await settled();
  await click(newSessionButton()); // fills the one slot
  await click(newSessionButton()); // refused: the toast
  expect(document.querySelector(".toast")).not.toBeNull();

  await click(screen.getByRole("button", { name: t("shell.limit.change") }));

  expect(document.querySelector(".toast")).toBeNull();
  expect(document.activeElement).toBe(screen.getByLabelText("Sessions running at once"));
  expect(sessionOpens(socket)).toHaveLength(1);
});

// F2: a focus key set while the document was still loading must not survive a page change made
// before it ever got the chance to fire — otherwise it fires later, on a Chat visit the user
// never asked to be focused into, once the document finally loads.
test("a focus key set while the document is still loading does not survive a page change away from it", async () => {
  const api = deferredShellApi();
  const { viki, socket } = fakeViki();
  render(<App viki={viki} rest={fakeRest()} durable={fakeDurable()} shell={api} />);
  act(() => socket.open());
  await settled();

  // capabilities and workspaces settle normally; settings (the document) stays pending — "still
  // loading" is this test's premise, until the resolve below.
  act(() => resolvePending(api, "capabilities", capabilitiesFixture()));
  await settled();
  act(() => resolvePending(api, "workspaces", listingFixture()));
  await settled();
  expect(api.pending.some((call) => call.method === "settings")).toBe(true);

  // Hits the built-in 3 (no document, no snapshot yet) to open the toast, same as the frozen
  // m4b default, then "Change limit" sets the focus key for chat.max_live_sessions.
  for (let i = 0; i < 3; i++) await click(newSessionButton());
  await click(newSessionButton());
  await click(screen.getByRole("button", { name: t("shell.limit.change") }));
  expect(screen.getByRole("tab", { name: "Chat", selected: true })).toBeTruthy();

  // Switches away before the document, and so the field, ever arrives.
  await click(screen.getByRole("tab", { name: "General" }));

  // The document loads.
  act(() => resolvePending(api, "settings", documentWithLimit(5)));
  await settled();

  // Back to Chat: the field exists now, but this later visit never asked to be focused into it.
  await click(screen.getByRole("tab", { name: "Chat" }));
  expect(document.activeElement).not.toBe(screen.getByLabelText("Sessions running at once"));
});

test("a too_many_sessions error card's Change limit opens the same field, focused", async () => {
  const { socket } = mount({ shell: shellWithLimit(5) });
  await settled();
  await click(newSessionButton());
  const id = sessionOpens(socket)[0]?.session_id as string;

  await act(async () => {
    socket.receive(
      frame(id, 1, "error", {
        code: "too_many_sessions",
        message: "stub refusal text",
        recoverable: true,
      }),
    );
  });
  expect(screen.getByText(t("shell.limit.error_title"))).toBeTruthy();

  await click(screen.getByRole("button", { name: t("shell.limit.change") }));

  expect(document.activeElement).toBe(screen.getByLabelText("Sessions running at once"));
});

test("Message VIKI at the limit shows the full-limit line, not the generic unconfirmed one", async () => {
  const { socket } = mount({ shell: shellWithLimit(1), decisionApi: createFakeDecisionLoopApi() });
  await settled();
  await click(newSessionButton()); // fills the one slot; no VIKI chat exists yet
  const opensBefore = sessionOpens(socket).length;

  const launcherButton = document.querySelector<HTMLButtonElement>("button.dloop-launcher");
  if (!launcherButton) throw new Error("no launcher button");
  await click(launcherButton);
  await settled();
  const dialog = within(screen.getByRole("dialog", { name: "VIKI" }));
  const box = dialog.getByRole("textbox", { name: "Message VIKI" });
  fireEvent.change(box, { target: { value: "What is due today?" } });
  await act(async () => {
    fireEvent.keyDown(box, { key: "Enter" });
  });
  await settled();

  expect(dialog.getByRole("alert").textContent).toBe(t("shell.limit.full"));
  expect(sessionOpens(socket)).toHaveLength(opensBefore);
});

// F1: the outer guard above sendToLauncherChat's notice choice also refuses while the socket is
// not open; the inner choice must agree, or a send that failed for the connection, not the cap,
// blames the limit instead.
test("Message VIKI at the limit with the socket not open shows the generic line, not the limit sentence", async () => {
  const { socket } = mount({ shell: shellWithLimit(1), decisionApi: createFakeDecisionLoopApi() });
  await settled();
  await click(newSessionButton()); // fills the one slot
  const opensBefore = sessionOpens(socket).length;

  const launcherButton = document.querySelector<HTMLButtonElement>("button.dloop-launcher");
  if (!launcherButton) throw new Error("no launcher button");
  await click(launcherButton);
  await settled();

  act(() => socket.onclose?.({ code: 1006 })); // mid-reconnect: status moves off "open"
  await settled();

  const dialog = within(screen.getByRole("dialog", { name: "VIKI" }));
  const box = dialog.getByRole("textbox", { name: "Message VIKI" });
  fireEvent.change(box, { target: { value: "What is due today?" } });
  await act(async () => {
    fireEvent.keyDown(box, { key: "Enter" });
  });
  await settled();

  expect(dialog.getByRole("alert").textContent).toBe(
    "VIKI didn't confirm that message. The text stays in the box.",
  );
  expect(sessionOpens(socket)).toHaveLength(opensBefore);
});
