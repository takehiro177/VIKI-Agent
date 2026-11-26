import { describe, expect, it, vi } from "vitest";
import { BotLearningApiError, createBotLearningApi, parseOverview } from "./botLearningApi";

const BOT = "00000000-0000-4000-8000-00000000000b";
const RUN = "00000000-0000-4000-8000-00000000000c";
const PROPOSAL = "00000000-0000-4000-8000-00000000000d";
const SESSION = "00000000-0000-4000-8000-00000000000e";
const STAMP = "2026-11-21T11:30:00.123Z";

const RUN_ROW = {
  run_id: RUN,
  workspace_id: "00000000-0000-4000-8000-00000000000a",
  bot_id: BOT,
  mode: "reflect",
  trigger: "user",
  session_id: null,
  state: "running",
  reason: "",
  episodes: 3,
  proposed: 0,
  rejected: 0,
  started_at: STAMP,
  finished_at: null,
};
const OVERVIEW = {
  bot_id: BOT,
  workspace_id: "00000000-0000-4000-8000-00000000000a",
  running: RUN,
  records_readable: true,
  runs: [RUN_ROW],
  proposals: [
    {
      proposal_id: PROPOSAL,
      run_id: RUN,
      op: "lesson_add",
      state: "open",
      claim: "Trigger: a weekly report\nAction: lead with the summary",
      target_id: null,
      target_claim: "",
      why: "asked twice",
      evidence: [{ session_id: SESSION, quote: "summary first please" }],
      result_id: null,
      created_at: STAMP,
      decided_at: null,
    },
  ],
  records: [],
  tainted_sessions: [{ session_id: SESSION, sources: "web,mcp", tainted_at: STAMP }],
  episodes: { unreflected: 2, last_finished_at: STAMP },
  legacy_files: 1,
};

function respond(status: number, body: unknown) {
  return vi.fn<typeof fetch>(async () => new Response(JSON.stringify(body), { status }));
}

describe("botLearningApi", () => {
  it("reads an overview with the bearer token and keeps only the fields the page shows", async () => {
    const fetchImpl = respond(200, OVERVIEW);
    const value = await createBotLearningApi("tok", fetchImpl).overview(BOT);

    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe(`/api/v2/bot-learning/${BOT}`);
    expect(init?.method).toBe("GET");
    expect(init?.headers).toEqual({ authorization: "Bearer tok" });
    expect(value.running).toBe(RUN);
    expect(value.proposals[0]?.evidence).toEqual([
      { session_id: SESSION, quote: "summary first please" },
    ]);
    expect(value.runs[0]).not.toHaveProperty("workspace_id");
    expect(value.episodes.unreflected).toBe(2);
  });

  it("posts exact bodies for runs, imports, decisions and clearing a session", async () => {
    const calls: [string, unknown][] = [];
    const replies: Record<string, unknown> = {
      [`/api/v2/bot-learning/${BOT}/runs`]: { run: RUN_ROW },
      [`/api/v2/bot-learning/${BOT}/import`]: {
        run: { ...RUN_ROW, mode: "import", state: "done" },
      },
      [`/api/v2/bot-learning/proposals/${PROPOSAL}`]: { proposal_id: PROPOSAL, state: "applied" },
      [`/api/v2/bot-learning/sessions/${SESSION}/untaint`]: { session_id: SESSION, cleared: true },
    };
    const fetchImpl = vi.fn<typeof fetch>(async (url, init) => {
      calls.push([String(url), JSON.parse(String(init?.body))]);
      return new Response(JSON.stringify(replies[String(url)]), { status: 200 });
    });
    const api = createBotLearningApi("tok", fetchImpl);

    expect((await api.start(BOT, "dream")).run_id).toBe(RUN);
    expect((await api.importLegacy(BOT)).mode).toBe("import");
    expect(await api.decide(PROPOSAL, "apply")).toBe("applied");
    expect(await api.untaint(SESSION)).toBe(true);
    expect(calls.map(([, body]) => body)).toEqual([{ mode: "dream" }, {}, { action: "apply" }, {}]);
  });

  it("turns a refusal into its code and message, and refuses ids that are not UUIDs", async () => {
    const api = createBotLearningApi(
      "tok",
      respond(409, { error: { code: "run_running", message: "A run is already going." } }),
    );
    await expect(api.start(BOT, "reflect")).rejects.toMatchObject({
      status: 409,
      code: "run_running",
      detail: "A run is already going.",
    });
    const fetchImpl = respond(200, {});
    await expect(createBotLearningApi("tok", fetchImpl).overview("../x")).rejects.toMatchObject({
      code: "invalid_input",
    });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("rejects a malformed overview", () => {
    expect(() =>
      parseOverview({ ...OVERVIEW, proposals: [{ ...OVERVIEW.proposals[0], op: "delete_all" }] }),
    ).toThrow(BotLearningApiError);
    expect(() => parseOverview({ ...OVERVIEW, legacy_files: -1 })).toThrow(BotLearningApiError);
    expect(() => parseOverview({ ...OVERVIEW, episodes: null })).toThrow(BotLearningApiError);
  });
});
