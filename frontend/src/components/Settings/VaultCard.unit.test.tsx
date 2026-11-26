import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type {
  Capabilities,
  StarterPlan,
  WorkspaceJson,
  WorkspacesListing,
} from "../../clients/app/shellApi";
import { ShellApiError } from "../../clients/app/shellApi";
import type { CommandArg, CommandId } from "../../commands";
import { t } from "../../i18n/t";
import type { ShellSnapshot, ShellStore } from "../../state/shell";
import {
  capabilitiesFixture,
  FAKE_WORKSPACE_ID,
  fakeShellApi,
  listingFixture,
  workspaceFixture,
} from "../../testShell";
import { FolderPickerProvider } from "../Onboarding/FolderPicker";
import { VaultCard } from "./VaultCard";

afterEach(cleanup);

function snapshotFixture(
  capabilities: Capabilities,
  workspaces: WorkspacesListing | null,
): ShellSnapshot {
  return {
    capabilities,
    capabilitiesStatus: "ready",
    settings: null,
    settingsStatus: "unavailable",
    workspaces,
    workspacesStatus: workspaces === null ? "unavailable" : "ready",
    errors: { capabilities: "", settings: "", workspaces: "" },
    recovery: false,
    checkRunning: null,
  };
}

function fakeStore(): ShellStore {
  return {
    getSnapshot: () => {
      throw new Error("not used by VaultCard");
    },
    subscribe: () => () => {},
    refreshCapabilities: vi.fn(async () => {}),
    refreshSettings: vi.fn(async () => {}),
    refreshWorkspaces: vi.fn(async () => {}),
    patch: vi.fn(async () => {
      throw new Error("not used by VaultCard");
    }),
    adoptSettings: vi.fn(),
    runCheck: vi.fn(async () => {
      throw new Error("not used by VaultCard");
    }),
    afterMutation: vi.fn(async () => {}),
    start: vi.fn(),
    stop: vi.fn(),
  };
}

const onCommand = vi.fn() as (id: CommandId, arg?: CommandArg) => void;

test("shows the active vault's name, policy chip, In use pill and path", () => {
  const workspace = workspaceFixture({
    workspace_id: FAKE_WORKSPACE_ID,
    name: "My notes",
    policy: "ordinary",
  });
  const listing = listingFixture([workspace]);
  const capabilities = capabilitiesFixture({
    workspace: {
      chat_only: false,
      active: {
        id: FAKE_WORKSPACE_ID,
        kind: "vault",
        name: workspace.name,
        policy: workspace.policy,
        layout: "unknown",
        source: workspace.source,
        aliased: false,
        root_available: true,
      },
    },
  });
  render(
    <VaultCard
      api={fakeShellApi()}
      store={fakeStore()}
      snapshot={snapshotFixture(capabilities, listing)}
      onCommand={onCommand}
    />,
  );
  expect(screen.getByRole("heading", { name: "Your vault · chats start here" })).toBeTruthy();
  expect(screen.getByText("My notes")).toBeTruthy();
  expect(screen.getByText("Ordinary")).toBeTruthy();
  expect(screen.getByText("In use")).toBeTruthy();
  expect(screen.getByText(workspace.root)).toBeTruthy();
});

test("falls back to the capabilities name, with no path, when the active workspace is not a registered row", () => {
  const listing = listingFixture([workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID })]);
  const capabilities = capabilitiesFixture({
    workspace: {
      chat_only: false,
      active: {
        id: "99999999-9999-4999-8999-999999999999",
        kind: "vault",
        name: "Override vault",
        policy: "existing_readonly",
        layout: "unknown",
        source: "environment",
        aliased: false,
        root_available: true,
      },
    },
  });
  render(
    <VaultCard
      api={fakeShellApi()}
      store={fakeStore()}
      snapshot={snapshotFixture(capabilities, listing)}
      onCommand={onCommand}
    />,
  );
  expect(screen.getByText("Override vault")).toBeTruthy();
  expect(screen.getByText("Read-only (existing folder)")).toBeTruthy();
  expect(screen.getByText("In use")).toBeTruthy();
  expect(screen.queryByText("My notes")).toBeNull();
  expect(document.querySelector(".workspace-root")).toBeNull();
});

test("falls back to Chat only, with no policy chip, pill or path, when nothing is active", () => {
  render(
    <VaultCard
      api={fakeShellApi()}
      store={fakeStore()}
      snapshot={snapshotFixture(capabilitiesFixture(), listingFixture([]))}
      onCommand={onCommand}
    />,
  );
  expect(screen.getByText("Chat only")).toBeTruthy();
  expect(screen.queryByText("In use")).toBeNull();
  expect(screen.queryByText("Ordinary")).toBeNull();
  expect(document.querySelector(".workspace-root")).toBeNull();
});

test("Change vault… opens then selects", async () => {
  const api = fakeShellApi({
    picker: { available: true, multiple: false, reason: null },
    picked: ["D:\\Vaults\\Picked"],
  });
  render(
    <FolderPickerProvider api={api}>
      <VaultCard
        api={api}
        store={fakeStore()}
        snapshot={snapshotFixture(
          capabilitiesFixture({ workspace: { chat_only: true } }),
          listingFixture([]),
        )}
        onCommand={onCommand}
      />
    </FolderPickerProvider>,
  );
  // Wait for the picker status read to settle so Change vault… takes the picker branch.
  await screen.findByRole("button", { name: "Type a path instead" });
  fireEvent.click(screen.getByRole("button", { name: "Change vault…" }));
  await waitFor(() => expect(api.calls.some((c) => c.method === "selectWorkspace")).toBe(true));
  expect(api.calls.find((c) => c.method === "pickFolders")?.args).toEqual([
    false,
    "Choose your vault folder",
  ]);
  expect(api.calls.find((c) => c.method === "openVault")?.args[1]).toBe("D:\\Vaults\\Picked");
  expect(api.calls.find((c) => c.method === "selectWorkspace")?.args[1]).toBe(FAKE_WORKSPACE_ID);
});

test('Change vault… falls back to onCommand("workspace.open") when no picker is available', () => {
  const api = fakeShellApi();
  const command = vi.fn();
  render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(
        capabilitiesFixture({ workspace: { chat_only: true } }),
        listingFixture([]),
      )}
      onCommand={command}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Change vault…" }));
  expect(command).toHaveBeenCalledWith("workspace.open");
  expect(api.calls.some((c) => c.method === "openVault")).toBe(false);
});

test('Type a path instead only appears with a picker, and calls onCommand("workspace.open")', async () => {
  const api = fakeShellApi({ picker: { available: true, multiple: false, reason: null } });
  const command = vi.fn();
  render(
    <FolderPickerProvider api={api}>
      <VaultCard
        api={api}
        store={fakeStore()}
        snapshot={snapshotFixture(
          capabilitiesFixture({ workspace: { chat_only: true } }),
          listingFixture([]),
        )}
        onCommand={command}
      />
    </FolderPickerProvider>,
  );
  expect(screen.queryByRole("button", { name: "Type a path instead" })).toBeNull();
  const button = await screen.findByRole("button", { name: "Type a path instead" });
  fireEvent.click(button);
  expect(command).toHaveBeenCalledWith("workspace.open");
});

test("Change vault…, New vault… and Details render before Type a path instead (primary actions first)", async () => {
  const api = fakeShellApi({ picker: { available: true, multiple: false, reason: null } });
  render(
    <FolderPickerProvider api={api}>
      <VaultCard
        api={api}
        store={fakeStore()}
        snapshot={snapshotFixture(
          capabilitiesFixture({ workspace: { chat_only: true } }),
          listingFixture([]),
        )}
        onCommand={onCommand}
      />
    </FolderPickerProvider>,
  );
  await screen.findByRole("button", { name: "Type a path instead" });
  const order = screen.getAllByRole("button").map((button) => button.textContent);
  expect(order).toEqual(["Change vault…", "New vault…", "Details", "Type a path instead"]);
});

test("New vault… calls onCommand", () => {
  const command = vi.fn();
  render(
    <VaultCard
      api={fakeShellApi()}
      store={fakeStore()}
      snapshot={snapshotFixture(
        capabilitiesFixture({ storage: { state: "ready" } }),
        listingFixture([]),
      )}
      onCommand={command}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "New vault…" }));
  expect(command).toHaveBeenCalledWith("workspace.create");
});

test("Change vault… and New vault… are refused with a reason when storage is not ready", () => {
  const command = vi.fn();
  render(
    <VaultCard
      api={fakeShellApi()}
      store={fakeStore()}
      snapshot={snapshotFixture(
        capabilitiesFixture({ storage: { state: "read_only" } }),
        listingFixture([]),
      )}
      onCommand={command}
    />,
  );
  const change = screen.getByRole("button", { name: "Change vault…" });
  const create = screen.getByRole("button", { name: "New vault…" });
  expect(change.getAttribute("aria-disabled")).toBe("true");
  expect(create.getAttribute("aria-disabled")).toBe("true");
  expect(change.getAttribute("title")).toBe("The app store is not ready.");
  fireEvent.click(change);
  fireEvent.click(create);
  expect(command).not.toHaveBeenCalled();
});

test("Details follows the displayed vault while a different vault waits for restart", async () => {
  const workspace = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID, policy: "ordinary" });
  const selected = workspaceFixture({ workspace_id: "next-vault", name: "Next vault" });
  const listing = { ...listingFixture([workspace, selected]), selected_id: selected.workspace_id };
  const capabilities = capabilitiesFixture({
    workspace: {
      chat_only: false,
      restart_required: true,
      active: {
        id: FAKE_WORKSPACE_ID,
        kind: "vault",
        name: workspace.name,
        policy: workspace.policy,
        layout: "unknown",
        source: workspace.source,
        aliased: false,
        root_available: true,
      },
    },
  });
  const api = fakeShellApi();
  render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(capabilities, listing)}
      onCommand={onCommand}
    />,
  );
  const details = screen.getByRole("button", { name: "Details" });
  expect(details.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(details);
  expect(details.getAttribute("aria-expanded")).toBe("true");
  await waitFor(() =>
    expect(api.calls.some((c) => c.method === "policy" && c.args[0] === FAKE_WORKSPACE_ID)).toBe(
      true,
    ),
  );
  await waitFor(() => expect(screen.getByText("Change the policy")).toBeTruthy());
});

test("applying a policy change from Details calls onChanged, which refreshes workspaces", async () => {
  const workspace = workspaceFixture({
    workspace_id: FAKE_WORKSPACE_ID,
    policy: "existing_readonly",
  });
  const listing = listingFixture([workspace]);
  const capabilities = capabilitiesFixture({
    workspace: {
      chat_only: false,
      active: {
        id: FAKE_WORKSPACE_ID,
        kind: "vault",
        name: workspace.name,
        policy: workspace.policy,
        layout: "unknown",
        source: workspace.source,
        aliased: false,
        root_available: true,
      },
    },
  });
  const store = fakeStore();
  render(
    <VaultCard
      api={fakeShellApi()}
      store={store}
      snapshot={snapshotFixture(capabilities, listing)}
      onCommand={onCommand}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Ordinary" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Ordinary" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(store.refreshWorkspaces).toHaveBeenCalled());
});

test("shows vault.next_launch with the selected row's name when a restart is pending", () => {
  const selected = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID, name: "Next vault" });
  const listing = { ...listingFixture([selected]), selected_id: FAKE_WORKSPACE_ID };
  const capabilities = capabilitiesFixture({
    workspace: { chat_only: true, restart_required: true },
  });
  render(
    <VaultCard
      api={fakeShellApi()}
      store={fakeStore()}
      snapshot={snapshotFixture(capabilities, listing)}
      onCommand={onCommand}
    />,
  );
  expect(
    screen.getByText("Selected for next launch: Next vault. Restart VIKI Agent to switch."),
  ).toBeTruthy();
});

test("shows the already-active message when Change vault… re-selects the active vault", async () => {
  // selectWorkspace's restart computation reads the fake's own `data.capabilities`, not the
  // snapshot prop handed to the component, so both must agree on which workspace is active.
  const capabilities = capabilitiesFixture({
    workspace: {
      chat_only: false,
      active: {
        id: FAKE_WORKSPACE_ID,
        kind: "vault",
        name: "My notes",
        policy: "ordinary",
        layout: "unknown",
        source: "created",
        aliased: false,
        root_available: true,
      },
    },
  });
  const api = fakeShellApi({
    capabilities,
    picker: { available: true, multiple: false, reason: null },
    picked: ["D:\\Vaults\\Picked"],
  });
  render(
    <FolderPickerProvider api={api}>
      <VaultCard
        api={api}
        store={fakeStore()}
        snapshot={snapshotFixture(
          capabilities,
          listingFixture([workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID })]),
        )}
        onCommand={onCommand}
      />
    </FolderPickerProvider>,
  );
  await screen.findByRole("button", { name: "Type a path instead" });
  fireEvent.click(screen.getByRole("button", { name: "Change vault…" }));
  await waitFor(() => expect(screen.getByText("This workspace is already in use.")).toBeTruthy());
});

test("a failed openVault during Change vault… shows the refusal via errorText", async () => {
  const api = fakeShellApi({
    picker: { available: true, multiple: false, reason: null },
    picked: ["D:\\Vaults\\Picked"],
  });
  api.fail("openVault", new ShellApiError(503, "unavailable"));
  render(
    <FolderPickerProvider api={api}>
      <VaultCard
        api={api}
        store={fakeStore()}
        snapshot={snapshotFixture(
          capabilitiesFixture({ workspace: { chat_only: true } }),
          listingFixture([]),
        )}
        onCommand={onCommand}
      />
    </FolderPickerProvider>,
  );
  await screen.findByRole("button", { name: "Type a path instead" });
  fireEvent.click(screen.getByRole("button", { name: "Change vault…" }));
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toBe("The operation could not be completed."),
  );
});

// The starter-update badge: fetched only for a registered, non-pending-bootstrap, non-protected
// vault row, and only when the api exposes `starterPlan` at all.

const BADGE_TEXT = t("settings.starter.badge");

function activeVaultCapabilities(workspace: WorkspaceJson): Capabilities {
  return capabilitiesFixture({
    workspace: {
      chat_only: false,
      active: {
        id: workspace.workspace_id,
        kind: "vault",
        name: workspace.name,
        policy: workspace.policy,
        layout: "unknown",
        source: workspace.source,
        aliased: false,
        root_available: true,
      },
    },
  });
}

function starterPlanFixture(overrides: Partial<StarterPlan> = {}): StarterPlan {
  return {
    state: "available",
    from_version: 1,
    to_version: 2,
    notify: false,
    items: [],
    agents: null,
    ...overrides,
  };
}

function deferredPlan() {
  let resolve!: (value: StarterPlan) => void;
  const promise = new Promise<StarterPlan>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

test("fetches the starter plan for the active vault and shows the badge when notify is true", async () => {
  const workspace = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID });
  const starterPlan = vi.fn(async () => starterPlanFixture({ notify: true }));
  const api = { ...fakeShellApi(), starterPlan };
  render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspace), listingFixture([workspace]))}
      onCommand={onCommand}
    />,
  );
  expect(await screen.findByText(BADGE_TEXT)).toBeTruthy();
  expect(starterPlan).toHaveBeenCalledWith(FAKE_WORKSPACE_ID);
});

test("fetches the starter plan and shows the badge for a none-bootstrap vault (added with Open)", async () => {
  const workspace = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID, bootstrap: "none" });
  const starterPlan = vi.fn(async () => starterPlanFixture({ notify: true }));
  const api = { ...fakeShellApi(), starterPlan };
  render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspace), listingFixture([workspace]))}
      onCommand={onCommand}
    />,
  );
  expect(await screen.findByText(BADGE_TEXT)).toBeTruthy();
  expect(starterPlan).toHaveBeenCalledWith(FAKE_WORKSPACE_ID);
});

test("shows nothing when the plan's notify is false", async () => {
  const workspace = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID });
  const starterPlan = vi.fn(async () => starterPlanFixture({ notify: false }));
  const api = { ...fakeShellApi(), starterPlan };
  render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspace), listingFixture([workspace]))}
      onCommand={onCommand}
    />,
  );
  await waitFor(() => expect(starterPlan).toHaveBeenCalled());
  expect(screen.queryByText(BADGE_TEXT)).toBeNull();
});

test("shows nothing when the starter-plan fetch rejects", async () => {
  const workspace = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID });
  const starterPlan = vi.fn(async () => {
    throw new ShellApiError(500, "unavailable");
  });
  const api = { ...fakeShellApi(), starterPlan };
  render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspace), listingFixture([workspace]))}
      onCommand={onCommand}
    />,
  );
  await waitFor(() => expect(starterPlan).toHaveBeenCalled());
  expect(screen.queryByText(BADGE_TEXT)).toBeNull();
});

test("never fetches the starter plan for a protected vault", () => {
  const workspace = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID, policy: "protected" });
  const starterPlan = vi.fn(async () => starterPlanFixture({ notify: true }));
  const api = { ...fakeShellApi(), starterPlan };
  render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspace), listingFixture([workspace]))}
      onCommand={onCommand}
    />,
  );
  expect(starterPlan).not.toHaveBeenCalled();
  expect(screen.queryByText(BADGE_TEXT)).toBeNull();
});

test("never fetches the starter plan while bootstrap has not completed", () => {
  const workspace = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID, bootstrap: "pending" });
  const starterPlan = vi.fn(async () => starterPlanFixture({ notify: true }));
  const api = { ...fakeShellApi(), starterPlan };
  render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspace), listingFixture([workspace]))}
      onCommand={onCommand}
    />,
  );
  expect(starterPlan).not.toHaveBeenCalled();
  expect(screen.queryByText(BADGE_TEXT)).toBeNull();
});

test("shows nothing when the api has no starterPlan", () => {
  const workspace = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID });
  render(
    <VaultCard
      api={fakeShellApi()}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspace), listingFixture([workspace]))}
      onCommand={onCommand}
    />,
  );
  expect(screen.queryByText(BADGE_TEXT)).toBeNull();
});

test("drops a stale starter-plan answer from a previous active id", async () => {
  const SECOND_WORKSPACE_ID = "77777777-7777-4777-8777-777777777777";
  const workspaceA = workspaceFixture({ workspace_id: FAKE_WORKSPACE_ID });
  const workspaceB = workspaceFixture({
    workspace_id: SECOND_WORKSPACE_ID,
    name: "Second vault",
  });
  const listing = listingFixture([workspaceA, workspaceB]);
  const pending = new Map<string, ReturnType<typeof deferredPlan>>();
  const starterPlan = vi.fn((id: string) => {
    const entry = deferredPlan();
    pending.set(id, entry);
    return entry.promise;
  });
  const api = { ...fakeShellApi(), starterPlan };

  const { rerender } = render(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspaceA), listing)}
      onCommand={onCommand}
    />,
  );
  await waitFor(() => expect(starterPlan).toHaveBeenCalledWith(FAKE_WORKSPACE_ID));

  // Switch the active vault before the first answer arrives.
  rerender(
    <VaultCard
      api={api}
      store={fakeStore()}
      snapshot={snapshotFixture(activeVaultCapabilities(workspaceB), listing)}
      onCommand={onCommand}
    />,
  );
  await waitFor(() => expect(starterPlan).toHaveBeenCalledWith(SECOND_WORKSPACE_ID));

  // The new vault's own answer lands first, with no update due.
  await act(async () => {
    pending.get(SECOND_WORKSPACE_ID)?.resolve(starterPlanFixture({ notify: false }));
    await pending.get(SECOND_WORKSPACE_ID)?.promise;
  });
  expect(screen.queryByText(BADGE_TEXT)).toBeNull();

  // The old vault's answer arrives last, saying notify: true — it must be dropped, not shown.
  await act(async () => {
    pending.get(FAKE_WORKSPACE_ID)?.resolve(starterPlanFixture({ notify: true }));
    await pending.get(FAKE_WORKSPACE_ID)?.promise;
  });
  expect(screen.queryByText(BADGE_TEXT)).toBeNull();
});
