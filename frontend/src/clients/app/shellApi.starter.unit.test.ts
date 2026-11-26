/**
 * `ShellApi.starterPlan`: GET request shape, uuid refusal, and the strict parse (exact keys,
 * known enums, integer versions, item path length, agents sha256 shape) that AC-10 asks for.
 */
import { expect, test } from "vitest";
import { FAKE_WORKSPACE_ID } from "../../testShell";
import { createShellApi, type ShellApi, ShellApiError, type StarterPlan } from "./shellApi";

const TOKEN = "launch-token-9f2ad41c";
const SHA = "a".repeat(64);

interface Recorded {
  path: string;
  method: string;
  headers: Record<string, string>;
}

function answer(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function recorder(respond: (path: string) => Response | Promise<Response>): {
  calls: Recorded[];
  api: ShellApi;
} {
  const calls: Recorded[] = [];
  const fetchImpl = (async (input: unknown, init: RequestInit = {}) => {
    const path = String(input);
    calls.push({
      path,
      method: String(init.method),
      headers: { ...(init.headers as Record<string, string>) },
    });
    return respond(path);
  }) as unknown as typeof fetch;
  return { calls, api: createShellApi(TOKEN, { fetch: fetchImpl }) };
}

async function refusal(promise: Promise<unknown> | undefined): Promise<ShellApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ShellApiError) return error;
    throw error;
  }
  throw new Error("expected a ShellApiError");
}

function planFixture(): StarterPlan {
  return {
    state: "available",
    from_version: 5,
    to_version: 7,
    notify: true,
    items: [
      { path: ".claude/skills/example/SKILL.md", action: "add", diff: null, text: "new skill" },
      { path: "system/log.md", action: "replace", diff: "--- a\n+++ b", text: null },
    ],
    agents: { proposal: "# AGENTS.md", sha256: SHA },
  };
}

test("starterPlan() GETs the vault's starter route and parses a strict plan", async () => {
  const plan = planFixture();
  const { calls, api } = recorder(() => answer(plan));
  const result = await api.starterPlan?.(FAKE_WORKSPACE_ID);
  expect(calls).toHaveLength(1);
  const sent = calls[0];
  if (sent === undefined) throw new Error("no request recorded");
  expect(sent.path).toBe(`/api/v2/vaults/${FAKE_WORKSPACE_ID}/starter`);
  expect(sent.method).toBe("GET");
  expect(sent.headers.authorization).toBe(`Bearer ${TOKEN}`);
  expect(result).toEqual(plan);
});

test("starterPlan() accepts a null from_version, an empty item list and null agents", async () => {
  const plan: StarterPlan = { ...planFixture(), from_version: null, items: [], agents: null };
  const { api } = recorder(() => answer(plan));
  expect(await api.starterPlan?.(FAKE_WORKSPACE_ID)).toEqual(plan);
});

test("starterPlan() refuses a non-uuid workspace id without making a request", async () => {
  const { calls, api } = recorder(() => answer(planFixture()));
  const error = await refusal(api.starterPlan?.("not-a-uuid"));
  expect(error.code).toBe("invalid_input");
  expect(calls).toHaveLength(0);
});

test("starterPlan() refuses an unknown state", async () => {
  const bad = { ...planFixture(), state: "pending" };
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});

test("starterPlan() refuses a non-integer to_version", async () => {
  const bad = { ...planFixture(), to_version: 7.5 };
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});

test("starterPlan() refuses a plan missing a required key", async () => {
  const { state, from_version, to_version, items, agents } = planFixture();
  const bad = { state, from_version, to_version, items, agents }; // notify dropped
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});

test("starterPlan() refuses a plan carrying an extra top-level key", async () => {
  const bad = { ...planFixture(), extra: true };
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});

test("starterPlan() refuses an item with an unknown action", async () => {
  const bad = {
    ...planFixture(),
    items: [{ path: "a.md", action: "delete", diff: null, text: null }],
  };
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});

test.each([0, 513])("starterPlan() refuses an item path of %i characters", async (length) => {
  const bad = {
    ...planFixture(),
    items: [{ path: "a".repeat(length), action: "skip", diff: null, text: null }],
  };
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});

test("starterPlan() accepts an item path at the 512 character ceiling", async () => {
  const plan: StarterPlan = {
    ...planFixture(),
    items: [{ path: "a".repeat(512), action: "skip", diff: null, text: null }],
  };
  const { api } = recorder(() => answer(plan));
  expect(await api.starterPlan?.(FAKE_WORKSPACE_ID)).toEqual(plan);
});

test("starterPlan() refuses an item carrying an extra key", async () => {
  const bad = {
    ...planFixture(),
    items: [{ path: "a.md", action: "skip", diff: null, text: null, note: "x" }],
  };
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});

test("starterPlan() refuses agents with a malformed sha256", async () => {
  const bad = { ...planFixture(), agents: { proposal: "x", sha256: "not-hex" } };
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});

test("starterPlan() refuses agents carrying an extra key", async () => {
  const bad = { ...planFixture(), agents: { proposal: "x", sha256: SHA, extra: 1 } };
  const { api } = recorder(() => answer(bad));
  expect((await refusal(api.starterPlan?.(FAKE_WORKSPACE_ID))).code).toBe("invalid_response");
});
