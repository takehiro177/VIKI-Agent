import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { KeySpec, SettingSource } from "../../clients/app/shellApi";
import { settingsFixture } from "../../testShell";
import { SettingRow } from "./SettingRow";

afterEach(cleanup);

const DOCUMENT = settingsFixture();

function renderRow(
  key: string,
  options: {
    value?: unknown;
    source?: SettingSource;
    readOnly?: boolean;
    spec?: Partial<KeySpec>;
  } = {},
): { onChange: ReturnType<typeof vi.fn> } {
  const base = DOCUMENT.keys[key] as KeySpec;
  const spec = { ...base, ...options.spec };
  const onChange = vi.fn();
  render(
    <SettingRow
      settingKey={key}
      spec={spec}
      value={"value" in options ? options.value : DOCUMENT.effective[key]}
      source={options.source ?? "default"}
      readOnlyNoticeId={options.readOnly === true ? "notice" : null}
      status={null}
      error={null}
      onChange={onChange}
    />,
  );
  return { onChange };
}

test("each kind renders its control, and the row meta follows source and applies", () => {
  renderRow("appearance.theme", { value: "dark", source: "setting" });
  const select = screen.getByLabelText("Theme") as HTMLSelectElement;
  expect(select.tagName).toBe("SELECT");
  expect([...select.options].map((option) => option.value)).toEqual(["dark", "light", "system"]);
  // The option labels come from the catalog, not from the wire token.
  expect([...select.options].map((option) => option.textContent)).toEqual([
    "Dark",
    "Light",
    "Follow computer settings",
  ]);
  expect(select.value).toBe("dark");
  // `appearance.theme` is set here and applies now: the meta names only the source, no chips.
  expect(document.querySelector(".settings-row-meta")?.textContent).toBe("Set here");
  cleanup();

  renderRow("privacy.hide_previews", { value: true });
  const toggle = screen.getByRole("switch") as HTMLInputElement;
  expect(toggle.type).toBe("checkbox");
  expect(toggle.checked).toBe(true);
  // A default row that applies now has nothing worth naming, so it renders no meta at all.
  expect(document.querySelector(".settings-row-meta")).toBeNull();
  cleanup();

  renderRow("identity.display_name", { value: "Synth User" });
  const text = screen.getByLabelText("Display name") as HTMLInputElement;
  expect(text.type).toBe("text");
  expect(text.value).toBe("Synth User");
  cleanup();

  // From the provider and not applied until the next conversation: both join the meta.
  renderRow("chat.default_model", { value: "claude-opus", source: "provider" });
  expect(document.querySelector(".settings-row-meta")?.textContent).toBe(
    "Claude Code setting · From the next conversation",
  );
  cleanup();

  // A number key is a closed list too, so it is a <select> over `allowed`; the option's value
  // is the token, and what comes back out stays a number rather than the "1.3" the DOM holds.
  const { onChange } = renderRow("accessibility.font_scale", { value: 1 });
  const scale = screen.getByLabelText("Display size") as HTMLSelectElement;
  expect([...scale.options].map((option) => option.value)).toEqual([
    "0.85",
    "1",
    "1.15",
    "1.3",
    "1.5",
  ]);
  fireEvent.change(scale, { target: { value: "1.3" } });
  expect(onChange).toHaveBeenCalledWith("accessibility.font_scale", 1.3);
});

test("the reset button is refused, not removed, unless the key is set here", () => {
  const inherited = renderRow("identity.display_name", { source: "default" });
  const button = screen.getByRole("button", { name: "Reset" });
  expect(button.getAttribute("aria-disabled")).toBe("true");
  // A refused control names its reason; the read-only notice is not the reason here.
  const reasonId = button.getAttribute("aria-describedby") as string;
  expect(document.getElementById(reasonId)?.textContent).toBe(
    "No custom value is saved here, so there is nothing to reset.",
  );
  // Focusable and clickable: the click does nothing, as a refused control should.
  fireEvent.click(button);
  expect(inherited.onChange).not.toHaveBeenCalled();
  cleanup();

  const set = renderRow("identity.display_name", { source: "setting", value: "Synth User" });
  const enabled = screen.getByRole("button", { name: "Reset" });
  expect(enabled.hasAttribute("aria-disabled")).toBe(false);
  fireEvent.click(enabled);
  expect(set.onChange).toHaveBeenCalledWith("identity.display_name", null);
});

test("the two identity keys carry the bound the client can reproduce and the model key does not", () => {
  renderRow("identity.display_name");
  expect((screen.getByLabelText("Display name") as HTMLInputElement).maxLength).toBe(64);
  cleanup();
  renderRow("identity.assistant_name", { value: "VIKI" });
  expect((screen.getByLabelText("Assistant name") as HTMLInputElement).maxLength).toBe(32);
  cleanup();
  renderRow("chat.default_model", { value: "" });
  // -1 is "no maxLength attribute": the 1,024-byte bound is the server's, not the client's.
  expect((screen.getByLabelText("Default model") as HTMLInputElement).maxLength).toBe(-1);
});

test("clearing a non-empty key inherits, and clearing the display name saves an empty string", () => {
  const assistant = renderRow("identity.assistant_name", { value: "VIKI", source: "setting" });
  const name = screen.getByLabelText("Assistant name");
  fireEvent.change(name, { target: { value: "" } });
  fireEvent.blur(name);
  expect(assistant.onChange).toHaveBeenCalledWith("identity.assistant_name", null);
  cleanup();

  const display = renderRow("identity.display_name", { value: "Synth User", source: "setting" });
  const field = screen.getByLabelText("Display name");
  fireEvent.change(field, { target: { value: "" } });
  fireEvent.blur(field);
  expect(display.onChange).toHaveBeenCalledWith("identity.display_name", "");
});

test("Enter saves the text field, and the IME's Enter does not", () => {
  const { onChange } = renderRow("identity.display_name", { value: "" });
  const field = screen.getByLabelText("Display name");
  fireEvent.change(field, { target: { value: "Synth User" } });
  fireEvent.keyDown(field, { key: "Enter", isComposing: true });
  fireEvent.keyDown(field, { key: "Enter", keyCode: 229 });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.keyDown(field, { key: "Enter" });
  expect(onChange).toHaveBeenCalledWith("identity.display_name", "Synth User");
});

test("a key that inherits offers the inherit option, and choosing it patches null", () => {
  const { onChange } = renderRow("chat.default_mode", { value: null, source: "provider" });
  const select = screen.getByLabelText("Default mode") as HTMLSelectElement;
  expect(select.options[0]?.value).toBe("");
  expect(select.options[0]?.textContent).toBe("Use Claude Code setting");
  expect(select.value).toBe("");
  fireEvent.change(select, { target: { value: "plan" } });
  expect(onChange).toHaveBeenCalledWith("chat.default_mode", "plan");
  fireEvent.change(select, { target: { value: "" } });
  expect(onChange).toHaveBeenLastCalledWith("chat.default_mode", null);
});

test("a read-only document refuses every control without taking it out of the tab order", () => {
  const { onChange } = renderRow("appearance.theme", {
    value: "dark",
    source: "setting",
    readOnly: true,
  });
  const select = screen.getByLabelText("Theme") as HTMLSelectElement;
  expect(select.getAttribute("aria-disabled")).toBe("true");
  expect(select.hasAttribute("disabled")).toBe(false);
  expect(select.getAttribute("aria-describedby")?.split(" ")).toContain("notice");
  fireEvent.change(select, { target: { value: "light" } });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  expect(onChange).not.toHaveBeenCalled();
});

test("the text field follows the document unless the user typed something of their own", () => {
  const spec = DOCUMENT.keys["identity.display_name"] as KeySpec;
  const props = {
    settingKey: "identity.display_name",
    spec,
    source: "setting" as SettingSource,
    readOnlyNoticeId: null,
    status: null,
    error: null,
    onChange: vi.fn(),
  };
  const view = render(<SettingRow {...props} value="Synth User" />);
  // Nothing typed: a reset arriving from the store empties the field.
  view.rerender(<SettingRow {...props} value="" />);
  expect((screen.getByLabelText("Display name") as HTMLInputElement).value).toBe("");

  // Typed but not yet committed: a document arriving from elsewhere does not erase it.
  fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Mine" } });
  view.rerender(<SettingRow {...props} value="Someone else" />);
  expect((screen.getByLabelText("Display name") as HTMLInputElement).value).toBe("Mine");
});

test("a value the backend allows but no lane has named renders as its raw token", () => {
  renderRow("chat.default_mode", {
    value: null,
    spec: { allowed: ["default", "brand_new_mode"] },
  });
  const select = screen.getByLabelText("Default mode") as HTMLSelectElement;
  expect([...select.options].map((option) => option.textContent)).toEqual([
    "Use Default",
    "Default",
    "brand_new_mode",
  ]);
  // Record 1.9: the unnamed one is drawn as the wire token, so it does not read as written copy.
  expect([...select.options].map((option) => option.className)).toEqual(["", "", "mono"]);
});

function renderOptions(value: unknown, options: readonly unknown[]): HTMLSelectElement {
  render(
    <SettingRow
      settingKey="appearance.theme"
      spec={DOCUMENT.keys["appearance.theme"] as KeySpec}
      value={value}
      source="setting"
      readOnlyNoticeId={null}
      status={null}
      error={null}
      onChange={vi.fn()}
      options={options}
    />,
  );
  return screen.getByLabelText("Theme") as HTMLSelectElement;
}

test("options replace the allowed list and a stored outsider is listed disabled", () => {
  const listed = renderOptions("dark", ["dark"]);
  expect([...listed.options].map((option) => option.value)).toEqual(["dark"]);
  cleanup();

  const outsider = renderOptions("light", ["dark"]);
  const extra = [...outsider.options].find((option) => option.value === "light");
  expect(extra?.disabled).toBe(true);
  expect(extra?.textContent).toBe("Light (not available here)");
  expect(outsider.value).toBe("light");
  expect([...outsider.options].map((option) => option.value)).toEqual(["light", "dark"]);
});

// `chat.max_live_sessions` never joins testShell's SPECS (a frozen App test depends on its
// absence there), so its KeySpec is built here, the same way the brief for this key says to.
const BOUNDED_SPEC: KeySpec = {
  scope: "app",
  applies: "now",
  kind: "number",
  allowed: null,
  default: 30,
  personal: false,
  bounds: [1, 999],
};

function renderBoundedNumber(
  options: { value?: unknown; readOnly?: boolean; error?: string | null } = {},
): { onChange: ReturnType<typeof vi.fn> } {
  const onChange = vi.fn();
  render(
    <SettingRow
      settingKey="chat.max_live_sessions"
      spec={BOUNDED_SPEC}
      value={"value" in options ? options.value : 30}
      source="default"
      readOnlyNoticeId={options.readOnly === true ? "notice" : null}
      status={null}
      error={options.error ?? null}
      onChange={onChange}
    />,
  );
  return { onChange };
}

test("a bounded number key renders a plain number input, never a select", () => {
  renderBoundedNumber();
  const input = screen.getByLabelText("Sessions running at once") as HTMLInputElement;
  expect(input.tagName).toBe("INPUT");
  expect(input.type).toBe("number");
  expect(input.min).toBe("1");
  expect(input.max).toBe("999");
  expect(input.value).toBe("30");
  expect(document.querySelector("select")).toBeNull();
  expect(document.querySelector("option")).toBeNull();
});

test("Enter saves a valid number once, and the IME's Enter does not", () => {
  const { onChange } = renderBoundedNumber();
  const input = screen.getByLabelText("Sessions running at once");
  fireEvent.change(input, { target: { value: "45" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(onChange).toHaveBeenCalledWith("chat.max_live_sessions", 45);
});

test("blur saves a valid number", () => {
  const { onChange } = renderBoundedNumber();
  const input = screen.getByLabelText("Sessions running at once");
  fireEvent.change(input, { target: { value: "45" } });
  fireEvent.blur(input);
  expect(onChange).toHaveBeenCalledWith("chat.max_live_sessions", 45);
});

test("saving the already-committed value sends nothing", () => {
  const { onChange } = renderBoundedNumber({ value: 30 });
  fireEvent.blur(screen.getByLabelText("Sessions running at once"));
  expect(onChange).not.toHaveBeenCalled();
});

test("an invalid or out-of-range entry is refused inline, with no call, for every bad input", () => {
  for (const bad of ["0", "1000", "2.5", "abc", ""]) {
    const { onChange } = renderBoundedNumber();
    const input = screen.getByLabelText("Sessions running at once");
    fireEvent.change(input, { target: { value: bad } });
    fireEvent.blur(input);
    expect(onChange).not.toHaveBeenCalled();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe("Enter a whole number from 1 to 999.");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(alert.id);
    cleanup();
  }
});

test("typing after a refusal clears it without sending anything", () => {
  const { onChange } = renderBoundedNumber();
  const input = screen.getByLabelText("Sessions running at once");
  fireEvent.change(input, { target: { value: "abc" } });
  fireEvent.blur(input);
  expect(screen.getByRole("alert").textContent).toBe("Enter a whole number from 1 to 999.");
  fireEvent.change(input, { target: { value: "5" } });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});

test("a read-only bounded number field is readOnly and sends nothing", () => {
  const { onChange } = renderBoundedNumber({ readOnly: true });
  const input = screen.getByLabelText("Sessions running at once") as HTMLInputElement;
  expect(input.readOnly).toBe(true);
  fireEvent.change(input, { target: { value: "45" } });
  fireEvent.blur(input);
  expect(onChange).not.toHaveBeenCalled();
});
