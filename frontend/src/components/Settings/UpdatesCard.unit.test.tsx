import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ShellApiError } from "../../clients/app/shellApi";
import type { ShellSnapshot } from "../../state/shell";
import { capabilitiesFixture, fakeShellApi, settingsFixture } from "../../testShell";
import { DiagnosticsPage } from "./DiagnosticsPage";
import { UpdatesCard } from "./UpdatesCard";

afterEach(cleanup);

function snapshotFixture(overrides: Partial<ShellSnapshot> = {}): ShellSnapshot {
  return {
    capabilities: capabilitiesFixture(),
    capabilitiesStatus: "ready",
    settings: settingsFixture(),
    settingsStatus: "ready",
    workspaces: null,
    workspacesStatus: "unavailable",
    errors: { capabilities: "", settings: "", workspaces: "" },
    recovery: false,
    checkRunning: null,
    ...overrides,
  };
}

test("DiagnosticsPage renders no Updates card without the prop, and one with it", () => {
  const api = fakeShellApi();
  const { rerender } = render(
    <DiagnosticsPage api={api} snapshot={snapshotFixture()} onNavigate={vi.fn()} />,
  );
  expect(screen.queryByRole("heading", { name: "Updates" })).toBeNull();

  rerender(
    <DiagnosticsPage
      api={api}
      snapshot={snapshotFixture()}
      onNavigate={vi.fn()}
      updates={{ auto: true, onAutoChange: vi.fn(async () => {}), onCheckNow: vi.fn(() => true) }}
    />,
  );
  expect(screen.getByRole("heading", { name: "Updates" })).toBeTruthy();
});

test("the checkbox reflects auto and writes the new value through onAutoChange", async () => {
  const onAutoChange = vi.fn(async () => {});
  render(<UpdatesCard auto={false} onAutoChange={onAutoChange} onCheckNow={vi.fn(() => true)} />);
  const checkbox = screen.getByRole("checkbox", {
    name: "Check for updates automatically",
  }) as HTMLInputElement;
  expect(checkbox.checked).toBe(false);

  await act(async () => {
    fireEvent.click(checkbox);
  });
  expect(onAutoChange).toHaveBeenCalledWith(true);
});

test("a patch failure shows the resolved error text next to the checkbox", async () => {
  const onAutoChange = vi.fn(async () => {
    throw new ShellApiError(0, "network_error");
  });
  render(<UpdatesCard auto={false} onAutoChange={onAutoChange} onCheckNow={vi.fn(() => true)} />);

  await act(async () => {
    fireEvent.click(screen.getByRole("checkbox", { name: "Check for updates automatically" }));
  });
  expect(screen.getByRole("alert").textContent).toBe("The server could not be reached.");
});

test("Check now shows the checking status line when the check was sent", () => {
  const onCheckNow = vi.fn(() => true);
  render(<UpdatesCard auto={true} onAutoChange={vi.fn(async () => {})} onCheckNow={onCheckNow} />);
  expect(screen.queryByRole("status")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Check now" }));
  expect(onCheckNow).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("status").textContent).toBe(
    "Checking. VIKI Agent shows the result in a dialog.",
  );
});

test("Check now shows the reopen-window status line when the check was not sent", () => {
  const onCheckNow = vi.fn(() => false);
  render(<UpdatesCard auto={true} onAutoChange={vi.fn(async () => {})} onCheckNow={onCheckNow} />);

  fireEvent.click(screen.getByRole("button", { name: "Check now" }));
  expect(screen.getByRole("status").textContent).toBe(
    "Couldn't ask VIKI Agent to check. Reopen this window and try again.",
  );
});
