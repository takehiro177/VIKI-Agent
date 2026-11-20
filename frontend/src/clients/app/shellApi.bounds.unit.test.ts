/**
 * `KeySpec.bounds` and the `parseKeySpec` rule that admits it: present only on a `number` key
 * with `allowed: null`, and only when it is two safe integers with the minimum no greater than
 * the maximum. `chat.max_live_sessions` is the one key that carries it; it never joins
 * testShell's SPECS (a frozen App test depends on its absence), so each case here takes the
 * settings document the fixture already carries and adds this one key, as the server would
 * send it.
 */
import { afterEach, expect, test, vi } from "vitest";
import { settingsFixture } from "../../testShell";
import { createShellApi, ShellApiError } from "./shellApi";

const TOKEN = "launch-token-9f2ad41c";

function answer(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

async function refusal(promise: Promise<unknown>): Promise<ShellApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ShellApiError) return error;
    throw error;
  }
  throw new Error("expected a ShellApiError");
}

function withKey(spec: unknown): unknown {
  const base = settingsFixture();
  return {
    ...base,
    effective: { ...base.effective, "chat.max_live_sessions": 30 },
    sources: { ...base.sources, "chat.max_live_sessions": "default" },
    keys: { ...base.keys, "chat.max_live_sessions": spec },
  };
}

function apiFor(spec: unknown) {
  return createShellApi(TOKEN, { fetch: vi.fn(async () => answer(withKey(spec))) });
}

function boundedSpec(bounds: unknown, overrides: Record<string, unknown> = {}): unknown {
  return {
    scope: "app",
    applies: "now",
    kind: "number",
    allowed: null,
    default: 30,
    personal: false,
    bounds,
    ...overrides,
  };
}

afterEach(() => vi.restoreAllMocks());

test("settings() accepts a number key whose bounds are two ordered safe integers", async () => {
  const view = await apiFor(boundedSpec([1, 999])).settings();
  expect(view.keys["chat.max_live_sessions"]).toMatchObject({ kind: "number", bounds: [1, 999] });
});

test("settings() accepts a number key with no bounds at all", async () => {
  const view = await apiFor({
    scope: "app",
    applies: "now",
    kind: "number",
    allowed: null,
    default: 30,
    personal: false,
  }).settings();
  expect(view.keys["chat.max_live_sessions"]).not.toHaveProperty("bounds");
});

test("settings() refuses bounds whose minimum is greater than its maximum", async () => {
  expect((await refusal(apiFor(boundedSpec([5, 1])).settings())).code).toBe("invalid_response");
});

test("settings() refuses a bound that is not a safe integer", async () => {
  expect((await refusal(apiFor(boundedSpec(["1", 999])).settings())).code).toBe("invalid_response");
});

test("settings() refuses bounds on an enum key", async () => {
  const spec = boundedSpec([1, 999], { kind: "enum", allowed: ["a", "b"] });
  expect((await refusal(apiFor(spec).settings())).code).toBe("invalid_response");
});
