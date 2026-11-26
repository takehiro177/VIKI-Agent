import { afterEach, expect, test, vi } from "vitest";
import { type AgentChange, AgentChangesApiError, createAgentChangesApi } from "./agentChangesApi";

afterEach(() => {
  vi.useRealTimers();
});

function reply(body: unknown, status = 200): typeof fetch {
  return vi.fn(async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
}
const codeOf = (promise: Promise<unknown>) =>
  promise.then(
    () => null,
    (error: unknown) => (error instanceof AgentChangesApiError ? error.code : "other"),
  );

const SETTING = {
  change_id: "c1",
  session_id: "s1",
  kind: "setting",
  summary: { key: "chat.default_mode", from: "manual", to: "auto", reset: false },
  expires_in_s: 600,
};
const ROUTINE = {
  change_id: "c2",
  session_id: "s2",
  kind: "routine_enable",
  summary: { routine_id: "r1", name: "Morning brief", revision: 3 },
  expires_in_s: 599,
};
const ROUTINE_FULL = {
  change_id: "c6",
  session_id: "s3",
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

test("list GETs with the Bearer token, no body, and parses both kinds", async () => {
  const fetchImpl = reply({ seq: 5, changes: [SETTING, ROUTINE] });
  const result = await createAgentChangesApi("tok", fetchImpl).list();
  const [url, init] = vi.mocked(fetchImpl).mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/api/v2/agent-changes");
  expect(init.method).toBe("GET");
  expect(init.headers).toEqual({ authorization: "Bearer tok" });
  expect(init.body).toBeUndefined();
  expect(result).toEqual({ seq: 5, changes: [SETTING, ROUTINE] });
});

test("an unrecognized or malformed kind is dropped, not fatal to the rest of the list", async () => {
  const unknownKind = { ...SETTING, change_id: "c3", kind: "future_kind" };
  const malformedSetting = { ...SETTING, change_id: "c4", summary: { key: "x" } };
  const fetchImpl = reply({ seq: 1, changes: [SETTING, unknownKind, malformedSetting, ROUTINE] });
  const result = await createAgentChangesApi("tok", fetchImpl).list();
  expect(result.changes.map((change: AgentChange) => change.change_id)).toEqual(["c1", "c2"]);
});

test("a reset carries no `to`, only reset: true, and still parses", async () => {
  const reset = {
    ...SETTING,
    change_id: "c5",
    summary: { key: "chat.default_model", from: "sonnet", to: null, reset: true },
  };
  const fetchImpl = reply({ seq: 1, changes: [reset] });
  const result = await createAgentChangesApi("tok", fetchImpl).list();
  expect(result.changes).toEqual([reset]);
});

test("a setting's summary keeps only its key, both values and reset", async () => {
  const extra = {
    ...SETTING,
    change_id: "c8",
    summary: { ...SETTING.summary, from_truncated: true, to_offset: 12 },
  };
  const fetchImpl = reply({ seq: 1, changes: [extra] });
  const result = await createAgentChangesApi("tok", fetchImpl).list();
  expect(result.changes).toEqual([{ ...extra, summary: SETTING.summary }]);
});

test("a routine_enable's schedule, profile, workspace and prompt parse through", async () => {
  const fetchImpl = reply({ seq: 1, changes: [ROUTINE_FULL] });
  const result = await createAgentChangesApi("tok", fetchImpl).list();
  expect(result.changes).toEqual([ROUTINE_FULL]);
});

test("a routine_enable's malformed schedule drops only the schedule, never the change", async () => {
  const malformed = {
    ...ROUTINE_FULL,
    change_id: "c7",
    summary: { ...ROUTINE_FULL.summary, schedule: { kind: "weekly" } },
  };
  const fetchImpl = reply({ seq: 1, changes: [malformed] });
  const result = await createAgentChangesApi("tok", fetchImpl).list();
  expect(result.changes).toEqual([
    { ...malformed, summary: { ...malformed.summary, schedule: undefined } },
  ]);
});

test("a routine_enable with none of the new fields still parses (an older server)", async () => {
  const fetchImpl = reply({ seq: 1, changes: [ROUTINE] });
  const result = await createAgentChangesApi("tok", fetchImpl).list();
  expect(result.changes).toEqual([ROUTINE]);
});

test("a malformed top-level answer is unavailable", async () => {
  for (const body of [{ seq: 1 }, { changes: [] }, { seq: "1", changes: [] }, []]) {
    expect(await codeOf(createAgentChangesApi("tok", reply(body)).list())).toBe("unavailable");
  }
});

test("a refusal carries its code and message, else unavailable", async () => {
  const refused = reply({ error: { code: "overloaded", message: "too busy" } }, 429);
  await expect(createAgentChangesApi("tok", refused).list()).rejects.toMatchObject({
    code: "overloaded",
    message: "too busy",
  });
  expect(await codeOf(createAgentChangesApi("tok", reply({}, 500)).list())).toBe("unavailable");
});

test("a 404 with no JSON body (the surface itself is gone) defaults to not_found", async () => {
  const gone = vi.fn(async () => new Response("Not Found", { status: 404 })) as typeof fetch;
  expect(await codeOf(createAgentChangesApi("tok", gone).list())).toBe("not_found");
});

test("an answer over 4 MiB is unavailable", async () => {
  const big = vi.fn(
    async () =>
      new Response(
        `${JSON.stringify({ seq: 1, changes: [] })}${" ".repeat(4 * 1024 * 1024 + 1024)}`,
        { status: 200 },
      ),
  ) as typeof fetch;
  expect(await codeOf(createAgentChangesApi("tok", big).list())).toBe("unavailable");
});

test("16 whole prompts at their worst-case escaped size fit under the 4 MiB bound", async () => {
  // A 32,768-byte prompt of control characters escapes to 6 bytes each: about 192 KiB a change.
  const prompt = "\u0001".repeat(32768);
  const changes = Array.from({ length: 16 }, (_, n) => ({
    ...ROUTINE_FULL,
    change_id: `c${n}`,
    summary: { ...ROUTINE_FULL.summary, prompt },
  }));
  const body = JSON.stringify({ seq: 1, changes });
  expect(body.length).toBeGreaterThan(3 * 1024 * 1024);
  const result = await createAgentChangesApi("tok", reply({ seq: 1, changes })).list();
  expect(result.changes.map((change: AgentChange) => change.summary)).toEqual(
    changes.map((change) => change.summary),
  );
});

test("the request aborts at the 15 s timeout, not before", async () => {
  vi.useFakeTimers();
  let signal: AbortSignal | undefined;
  const hung = ((_input: RequestInfo | URL, init?: RequestInit) => {
    signal = init?.signal ?? undefined;
    return new Promise<Response>(() => {});
  }) as typeof fetch;
  void createAgentChangesApi("tok", hung)
    .list()
    .catch(() => undefined);
  await vi.advanceTimersByTimeAsync(14_999);
  expect(signal?.aborted).toBe(false);
  await vi.advanceTimersByTimeAsync(1);
  expect(signal?.aborted).toBe(true);
});

test("an aborted signal never reaches fetch", async () => {
  const fetchImpl = reply({ seq: 1, changes: [] });
  const controller = new AbortController();
  controller.abort();
  await expect(createAgentChangesApi("tok", fetchImpl).list(controller.signal)).rejects.toThrow(
    "cancelled",
  );
  expect(fetchImpl).not.toHaveBeenCalled();
});

test("decide POSTs {decision} to the encoded change id and returns state", async () => {
  const fetchImpl = reply({ state: "applied" });
  const result = await createAgentChangesApi("tok", fetchImpl).decide("c 1", "apply");
  const [url, init] = vi.mocked(fetchImpl).mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/api/v2/agent-changes/c%201");
  expect(init.method).toBe("POST");
  expect(init.headers).toEqual({
    authorization: "Bearer tok",
    "content-type": "application/json",
  });
  expect(init.body).toBe(JSON.stringify({ decision: "apply" }));
  expect(result).toEqual({ state: "applied" });
});

test("decide's 404 and 409 refusals carry the server's own code", async () => {
  const gone = reply({ error: { code: "not_found", message: "gone" } }, 404);
  expect(await codeOf(createAgentChangesApi("tok", gone).decide("c1", "apply"))).toBe("not_found");
  const expired = reply({ error: { code: "expired", message: "expired" } }, 409);
  expect(await codeOf(createAgentChangesApi("tok", expired).decide("c1", "reject"))).toBe(
    "expired",
  );
});

test("decide with no `state` in the answer is unavailable", async () => {
  expect(await codeOf(createAgentChangesApi("tok", reply({})).decide("c1", "apply"))).toBe(
    "unavailable",
  );
});
