import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  type BotLearningApi,
  BotLearningApiError,
  type LearningOverview,
  type LearningRun,
} from "../../clients/messaging/botLearningApi";
import type { BotRow } from "../../clients/messaging/botsApi";
import { t, tf } from "../../i18n/t";
import { BotLearningCard } from "./BotLearningCard";

afterEach(cleanup);

const bot = {
  bot_id: "22222222-2222-4222-8222-222222222222",
  profile: { identity: { name: "Research Bot" } },
} as BotRow;
const PROPOSAL = "33333333-3333-4333-8333-333333333333";
const APPLIED = "44444444-4444-4444-8444-444444444444";
const SESSION = "55555555-5555-4555-8555-555555555555";

function overview(over: Partial<LearningOverview> = {}): LearningOverview {
  return {
    bot_id: bot.bot_id,
    running: null,
    records_readable: true,
    runs: [
      {
        run_id: "66666666-6666-4666-8666-666666666666",
        mode: "reflect",
        trigger: "user",
        state: "done",
        reason: "",
        episodes: 3,
        proposed: 1,
        rejected: 2,
        started_at: "2026-01-01T00:00:00Z",
        finished_at: "2026-01-01T00:01:00Z",
      },
    ],
    proposals: [
      {
        proposal_id: PROPOSAL,
        op: "lesson_add",
        state: "open",
        claim: "Trigger: weekly report\nAction: lead with the summary",
        target_claim: "",
        why: "the owner asked twice",
        evidence: [{ session_id: SESSION, quote: "put the summary first please" }],
        result_id: null,
        created_at: "2026-01-01T00:01:00Z",
        decided_at: null,
      },
      {
        proposal_id: APPLIED,
        op: "fact_add",
        state: "applied",
        claim: "The team ships on Fridays.",
        target_claim: "",
        why: "",
        evidence: [],
        result_id: "77777777-7777-4777-8777-777777777777",
        created_at: "2026-01-01T00:01:00Z",
        decided_at: "2026-01-01T00:02:00Z",
      },
    ],
    records: [
      {
        memory_id: "77777777-7777-4777-8777-777777777777",
        ref: "memory/fact.md",
        state: "approved",
        claim: "The team ships on Fridays.",
        uses: 4,
        helpful: 1,
        harmful: 0,
        last_used_at: null,
      },
    ],
    tainted_sessions: [{ session_id: SESSION, sources: "web", tainted_at: "2026-01-01T00:00:00Z" }],
    episodes: { unreflected: 2, last_finished_at: "2026-01-01T00:00:00Z" },
    legacy_files: 3,
    ...over,
  };
}

function learningApi(value: LearningOverview = overview()) {
  const run = value.runs[0] as LearningRun;
  return {
    overview: vi.fn(async () => value),
    start: vi.fn(async () => run),
    importLegacy: vi.fn(async () => run),
    decide: vi.fn(async () => "applied" as const),
    untaint: vi.fn(async () => true),
  } satisfies BotLearningApi;
}

it("lists proposals, records, runs and tainted sessions and sends each decision", async () => {
  const api = learningApi();
  render(<BotLearningCard api={api} bot={bot} disabled={false} />);
  await act(async () => {});

  expect(api.overview).toHaveBeenCalledWith(bot.bot_id, expect.any(AbortSignal));
  expect(screen.getByText(t("bots.learning.op.lesson_add"))).toBeTruthy();
  expect(screen.getByText("put the summary first please")).toBeTruthy();
  expect(screen.getByText(tf("bots.learning.why", { why: "the owner asked twice" }))).toBeTruthy();
  expect(
    screen.getByText(tf("bots.learning.uses", { uses: 4, helpful: 1, harmful: 0 })),
  ).toBeTruthy();
  expect(screen.getByText(tf("bots.learning.unreflected", { count: 2 }))).toBeTruthy();
  expect(
    screen.getByText(
      tf("bots.learning.run.summary", {
        mode: t("bots.learning.run.reflect"),
        proposed: 1,
        rejected: 2,
      }),
    ),
  ).toBeTruthy();

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: t("bots.learning.apply") }));
  });
  expect(api.decide).toHaveBeenCalledWith(PROPOSAL, "apply");
  expect(api.overview).toHaveBeenCalledTimes(2);

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: t("bots.learning.undo") }));
  });
  expect(api.decide).toHaveBeenCalledWith(APPLIED, "undo");

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: t("bots.learning.clear") }));
  });
  expect(api.untaint).toHaveBeenCalledWith(SESSION);

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: tf("bots.learning.import", { count: 3 }) }));
  });
  expect(api.importLegacy).toHaveBeenCalledWith(bot.bot_id);

  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: t("bots.learning.dream") }));
  });
  expect(api.start).toHaveBeenCalledWith(bot.bot_id, "dream");
});

it("holds the run buttons while a run is going and looks again until it ends", async () => {
  vi.useFakeTimers();
  try {
    const api = learningApi(overview({ running: "66666666-6666-4666-8666-666666666666" }));
    render(<BotLearningCard api={api} bot={bot} disabled={false} pollMs={50} />);
    await act(async () => {});

    expect(screen.getByRole("status").textContent).toBe(t("bots.learning.running"));
    const reflect = screen.getByRole("button", { name: t("bots.learning.reflect") });
    expect((reflect as HTMLButtonElement).disabled).toBe(true);

    api.overview.mockResolvedValue(overview());
    await act(async () => {
      vi.advanceTimersByTime(60);
    });
    await act(async () => {});
    expect(api.overview).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("status")).toBeNull();
    expect(
      (screen.getByRole("button", { name: t("bots.learning.reflect") }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  } finally {
    vi.useRealTimers();
  }
});

it("shows a refusal and keeps every action off while the card is disabled", async () => {
  const api = learningApi(overview({ legacy_files: 0 }));
  api.start.mockRejectedValue(
    new BotLearningApiError(409, "busy", "A learning run is already going."),
  );
  const view = render(<BotLearningCard api={api} bot={bot} disabled={false} />);
  await act(async () => {});

  expect(
    screen.queryByRole("button", { name: tf("bots.learning.import", { count: 0 }) }),
  ).toBeNull();
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: t("bots.learning.reflect") }));
  });
  expect(screen.getByRole("alert").textContent).toBe(
    tf("bots.learning.failed", { message: "A learning run is already going." }),
  );

  view.rerender(<BotLearningCard api={api} bot={bot} disabled />);
  await act(async () => {});
  for (const name of [t("bots.learning.reflect"), t("bots.learning.apply")]) {
    expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  }
});

it("offers a retry when the overview cannot be read and writes the auto settings", async () => {
  const api = learningApi();
  api.overview.mockRejectedValueOnce(new BotLearningApiError(503, "unavailable", ""));
  const set = vi.fn(async () => {});
  render(
    <BotLearningCard
      api={api}
      bot={bot}
      disabled={false}
      auto={{ reflect: false, dream: true, set }}
    />,
  );
  await act(async () => {});

  expect(screen.getByRole("alert").textContent).toBe(t("bots.learning.error"));
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: t("bots.learning.retry") }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("checkbox", { name: t("bots.learning.autoReflect") }));
  });
  expect(set).toHaveBeenCalledWith("bots.auto_reflect", true);
  expect(
    (screen.getByRole("checkbox", { name: t("bots.learning.autoDream") }) as HTMLInputElement)
      .checked,
  ).toBe(true);
});

it("shows the empty-records note when records_readable is true and there are no records", async () => {
  const api = learningApi(overview({ records_readable: true, records: [] }));
  render(<BotLearningCard api={api} bot={bot} disabled={false} />);
  await act(async () => {});

  expect(screen.getByText(tf("bots.learning.noRecords", { name: "Research Bot" }))).toBeTruthy();
  expect(
    screen.queryByText(tf("bots.learning.recordsUnreadable", { name: "Research Bot" })),
  ).toBeNull();
});

it("shows that the approved records could not be read, instead of the empty-records note (F1)", async () => {
  const api = learningApi(overview({ records_readable: false, records: [] }));
  render(<BotLearningCard api={api} bot={bot} disabled={false} />);
  await act(async () => {});

  expect(
    screen.getByText(tf("bots.learning.recordsUnreadable", { name: "Research Bot" })),
  ).toBeTruthy();
  expect(screen.queryByText(tf("bots.learning.noRecords", { name: "Research Bot" }))).toBeNull();
});
