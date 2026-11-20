import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import {
  type KeySpec,
  type SettingsView as SettingsDocument,
  ShellApiError,
} from "../../clients/app/shellApi";
import { t } from "../../i18n/t";
import { createShellStore, type ShellStore } from "../../state/shell";
import { AppStore } from "../../state/store";
import { type FakeShellApi, fakeShellApi, settingsFixture } from "../../testShell";
import { SETTINGS_PANEL_CLASS, SettingsView } from "./SettingsView";

afterEach(cleanup);

async function storeWith(
  document?: SettingsDocument,
): Promise<{ store: ShellStore; api: FakeShellApi }> {
  const api = fakeShellApi(document === undefined ? {} : { settings: document });
  const store = createShellStore(api, new AppStore());
  await act(async () => {
    await store.refreshSettings();
  });
  return { store, api };
}

function show(
  store: ShellStore,
  page: Parameters<typeof SettingsView>[0]["page"] = "general",
  focus: { focusKey?: string | null; onFocusKeyUsed?: () => void } = {},
) {
  const onPage = vi.fn();
  const onNavigate = vi.fn();
  render(
    <SettingsView store={store} page={page} onPage={onPage} onNavigate={onNavigate} {...focus}>
      <p data-testid="lane-page">another lane's page</p>
    </SettingsView>,
  );
  return { onPage, onNavigate };
}

// `chat.max_live_sessions` never joins testShell's SPECS (a frozen App test depends on its
// absence), so it is added to the fixture's document here, as the server would send it.
const BOUNDED_SPEC: KeySpec = {
  scope: "app",
  applies: "now",
  kind: "number",
  allowed: null,
  default: 30,
  personal: false,
  bounds: [1, 999],
};

function withLimitKey(document: SettingsDocument = settingsFixture()): SettingsDocument {
  return {
    ...document,
    effective: { ...document.effective, "chat.max_live_sessions": 30 },
    sources: { ...document.sources, "chat.max_live_sessions": "default" },
    keys: { ...document.keys, "chat.max_live_sessions": BOUNDED_SPEC },
  };
}

test("a stale window sees the current values and keeps what the user typed", async () => {
  const { store, api } = await storeWith();
  api.fail(
    "patchSettings",
    new ShellApiError(409, "stale_revision", {
      current: settingsFixture({ "identity.display_name": "Someone else" }, { revision: 9 }),
      currentRevision: 9,
    }),
  );
  show(store);

  const field = screen.getByLabelText("Display name") as HTMLInputElement;
  await act(async () => {
    fireEvent.change(field, { target: { value: "Synth User" } });
    fireEvent.blur(field);
  });

  const panel = screen.getByRole("dialog");
  expect(panel.textContent).toContain("Synth User");
  expect(panel.textContent).toContain("Someone else");

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Load the current values and re-enter" }));
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  // The adopted document is what the store now holds, and the control still carries the user's
  // own text, so one more blur saves it against the new revision.
  expect((screen.getByLabelText("Display name") as HTMLInputElement).value).toBe("Synth User");
  await act(async () => {
    fireEvent.blur(screen.getByLabelText("Display name"));
  });

  const patches = api.calls.filter((call) => call.method === "patchSettings");
  expect(patches).toHaveLength(2);
  expect(patches[0]?.args[1]).toBe(3);
  expect(patches[1]?.args[1]).toBe(9);
  // A retry after the revision was re-read is a new attempt, so it carries a new command id.
  expect(patches[0]?.args[0]).not.toBe(patches[1]?.args[0]);
});

test("a command conflict with no document in it re-reads instead of adopting one", async () => {
  const { store, api } = await storeWith();
  api.fail("patchSettings", new ShellApiError(409, "command_conflict"));
  show(store);
  const field = screen.getByLabelText("Display name");
  await act(async () => {
    fireEvent.change(field, { target: { value: "Synth User" } });
    fireEvent.blur(field);
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Load the current values and re-enter" }));
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(api.calls.filter((call) => call.method === "settings")).toHaveLength(2);
});

test("a newer or corrupt document is read-only everywhere", async () => {
  const { store } = await storeWith(
    settingsFixture({}, { read_only: true, reason: "newer_version", values: null }),
  );
  show(store);
  const notice = document.querySelector(".settings-readonly") as HTMLElement;
  expect(notice.getAttribute("role")).toBe("status");
  expect(notice.textContent).toContain(
    "A newer version of VIKI is saving settings. This version is read-only.",
  );
  expect(notice.textContent).toContain(
    "Close VIKI Agent and copy the whole app data folder somewhere safe. For settings saved by a newer version, use that version. For damaged settings, restore a complete backup made while VIKI Agent was closed.",
  );

  const controls = [
    ...document.querySelectorAll(".settings-row-control input, .settings-row-control select"),
  ];
  expect(controls).not.toHaveLength(0);
  for (const control of controls) {
    expect(control.getAttribute("aria-disabled")).toBe("true");
    expect(control.getAttribute("aria-describedby")?.split(" ")).toContain(notice.id);
  }
  // Nothing on a read-only page may claim the user set it here.
  const rowStatusText = [...document.querySelectorAll(".settings-row-status")].map(
    (status) => status.textContent,
  );
  expect(rowStatusText.join(" ")).not.toContain("Set here");
  cleanup();

  const corrupt = await storeWith(
    settingsFixture({}, { read_only: true, reason: "corrupt", values: null }),
  );
  show(corrupt.store);
  expect(document.querySelector(".settings-readonly")?.textContent).toContain(
    "The settings file could not be read.",
  );
});

test("the panel holds the lane's own page for a page this lane does not build", async () => {
  const { store } = await storeWith();
  show(store, "providers");
  const panel = document.querySelector(`.${SETTINGS_PANEL_CLASS}`);
  expect(panel?.getAttribute("role")).toBe("tabpanel");
  expect(panel?.getAttribute("aria-labelledby")).toBe("settings-tab-providers");
  expect(screen.getByTestId("lane-page").parentElement).toBe(panel);
  // No document row is drawn for a page this view does not build.
  expect(document.querySelector(".settings-row")).toBeNull();
});

test("the heading takes focus on entry and the header offers the way back to the chat", async () => {
  const { store } = await storeWith();
  const { onNavigate } = show(store);
  const heading = screen.getByRole("heading", { level: 1 });
  expect(heading.textContent).toBe("Settings");
  expect(document.activeElement).toBe(heading);
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(onNavigate).toHaveBeenCalledWith({ view: "chat" });
});

test("a document that has not arrived says so, and a failed read says why", async () => {
  const store = createShellStore(null, new AppStore());
  show(store);
  expect(screen.getByRole("status").textContent).toBe("Loading…");
  expect(screen.queryByLabelText("Search settings")).toBeNull();
  cleanup();

  const api = fakeShellApi();
  api.fail("settings", new ShellApiError(0, "timeout"));
  const failing = createShellStore(api, new AppStore());
  await act(async () => {
    await failing.refreshSettings();
  });
  show(failing);
  expect(screen.getByRole("status").textContent).toBe("No answer in time.");
});

test("opening a page from the search result clears the query and navigates", async () => {
  const { store } = await storeWith();
  const { onPage } = show(store);
  const input = screen.getByLabelText("Search settings") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "density" } });
  expect(document.querySelector(".settings-search-results")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open this page" }));
  expect(onPage).toHaveBeenCalledWith("appearance");
  expect(input.value).toBe("");
});

test("a settings read that failed does not take the other lanes' pages down with it", async () => {
  const api = fakeShellApi();
  api.fail("settings", new ShellApiError(503, "unavailable"));
  const store = createShellStore(api, new AppStore());
  await act(async () => {
    await store.refreshSettings();
  });
  // Diagnostics reads capabilities, not the document — and it is where the user is sent when
  // the app's own storage is the broken thing.
  show(store, "diagnostics");
  expect(screen.getByTestId("lane-page").textContent).toBe("another lane's page");
  expect(document.querySelector(".settings-state")).toBeNull();
  cleanup();

  // A document page still says what went wrong, because it has nothing else to draw.
  show(store, "chat");
  expect(screen.getByRole("status").textContent).toBe("The operation could not be completed.");
});

test("the settings header holds the title and intro in a page head", async () => {
  const { store } = await storeWith();
  render(
    <SettingsView store={store} page="providers" onPage={vi.fn()} onNavigate={vi.fn()}>
      <p>Providers content</p>
    </SettingsView>,
  );
  const heading = screen.getByRole("heading", { name: "Settings", level: 1 });
  expect(heading.classList.contains("page-title")).toBe(true);
  const head = heading.closest(".page-head") as HTMLElement;
  expect(head).not.toBeNull();
  expect(head.closest("header")).not.toBeNull();
  expect(head.querySelector("p.page-intro")?.textContent).toBe(t("settings.intro"));
});

test("a supplied Back replaces the header button and uses its current navigation wrapper", async () => {
  const { store } = await storeWith();
  const onNavigate = vi.fn();
  const onPage = vi.fn();
  render(
    <SettingsView
      store={store}
      page="providers"
      onPage={onPage}
      onNavigate={onNavigate}
      renderBack={(onBack) => (
        <button type="button" onClick={onBack}>
          Back
        </button>
      )}
    >
      <p>Providers content</p>
    </SettingsView>,
  );
  const heading = screen.getByRole("heading", { name: "Settings", level: 1 });
  const header = heading.closest("header");
  expect(header).not.toBeNull();
  expect(within(header as HTMLElement).getAllByRole("button")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Back to chat" })).toBeNull();
  expect(document.activeElement).toBe(heading);
  expect(screen.getByRole("tabpanel").textContent).toBe("Providers content");
  expect(onNavigate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(onNavigate).toHaveBeenCalledTimes(1);
  expect(onNavigate).toHaveBeenCalledWith({ view: "chat" });
  expect(onPage).not.toHaveBeenCalled();
});

test("focusKey moves focus to that key's control once the document page holds it, and reports back once", async () => {
  const { store } = await storeWith(withLimitKey());
  const onFocusKeyUsed = vi.fn();
  show(store, "chat", { focusKey: "chat.max_live_sessions", onFocusKeyUsed });

  const input = screen.getByLabelText("Sessions running at once");
  expect(document.activeElement).toBe(input);
  expect(onFocusKeyUsed).toHaveBeenCalledTimes(1);
});

test("with no focusKey the heading keeps the focus it took on entry", async () => {
  const { store } = await storeWith(withLimitKey());
  show(store, "chat");
  expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
});
