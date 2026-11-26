import { useEffect, useRef, useState } from "react";
import {
  type BotLearningApi,
  BotLearningApiError,
  type LearningOverview,
  type LearningProposal,
  type LearningRun,
  type ProposalAction,
} from "../../clients/messaging/botLearningApi";
import type { BotRow } from "../../clients/messaging/botsApi";
import type { Key } from "../../i18n";
import { t, tf } from "../../i18n/t";
import { Loading } from "../Loading/Loading";
import "./BotLearningCard.css";

/** The two settings that start learning runs without a click, and their writer. */
export interface LearningAuto {
  reflect: boolean;
  dream: boolean;
  set(key: "bots.auto_reflect" | "bots.auto_dream", value: boolean): Promise<void>;
}

export interface BotLearningCardProps {
  api: BotLearningApi;
  bot: BotRow;
  disabled: boolean;
  auto?: LearningAuto;
  /** How often to look again while a run is going, in milliseconds. */
  pollMs?: number;
}

const OP_KEYS: Record<LearningProposal["op"], Key> = {
  lesson_add: "bots.learning.op.lesson_add",
  fact_add: "bots.learning.op.fact_add",
  profile_add: "bots.learning.op.profile_add",
  record_supersede: "bots.learning.op.record_supersede",
  record_retire: "bots.learning.op.record_retire",
};
const STATE_KEYS: Record<LearningProposal["state"], Key> = {
  open: "bots.learning.state.open",
  applied: "bots.learning.state.applied",
  rejected: "bots.learning.state.rejected",
  stale: "bots.learning.state.stale",
  undone: "bots.learning.state.undone",
};
const MODE_KEYS: Record<LearningRun["mode"], Key> = {
  reflect: "bots.learning.run.reflect",
  dream: "bots.learning.run.dream",
  import: "bots.learning.run.import",
};
const UNDOABLE = new Set(["lesson_add", "fact_add", "profile_add"]);

function message(error: unknown): string {
  if (error instanceof BotLearningApiError) return error.detail || error.code;
  return "unavailable";
}

export function BotLearningCard({ api, bot, disabled, auto, pollMs = 3000 }: BotLearningCardProps) {
  const [overview, setOverview] = useState<LearningOverview | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const botId = bot.bot_id;
  const current = useRef(botId);
  current.current = botId;
  const name = bot.profile?.identity?.name ?? "Bot";

  // biome-ignore lint/correctness/useExhaustiveDependencies: attempt is an explicit reload request
  useEffect(() => {
    const controller = new AbortController();
    setFailed(false);
    void api.overview(botId, controller.signal).then(
      (value) => {
        if (!controller.signal.aborted && current.current === botId) setOverview(value);
      },
      () => {
        if (!controller.signal.aborted && current.current === botId) setFailed(true);
      },
    );
    return () => controller.abort();
  }, [api, botId, attempt]);

  const running = overview?.bot_id === botId ? overview.running : null;
  useEffect(() => {
    if (!running) return;
    const timer = setTimeout(() => setAttempt((value) => value + 1), pollMs);
    return () => clearTimeout(timer);
  }, [running, pollMs]);

  async function act(action: () => Promise<unknown>): Promise<void> {
    if (disabled || busy) return;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(tf("bots.learning.failed", { message: message(failure) }));
    } finally {
      setBusy(false);
      setAttempt((value) => value + 1);
    }
  }

  const ready = overview?.bot_id === botId ? overview : null;
  if (!ready) {
    return (
      <section className="bot-learning-card" aria-label={t("bots.learning")}>
        <h3>{t("bots.learning")}</h3>
        {failed ? (
          <>
            <p role="alert">{t("bots.learning.error")}</p>
            <button type="button" className="btn alt sm" onClick={() => setAttempt((v) => v + 1)}>
              {t("bots.learning.retry")}
            </button>
          </>
        ) : (
          <Loading>{tf("bots.learning.loading", { name })}</Loading>
        )}
      </section>
    );
  }

  const open = ready.proposals.filter((item) => item.state === "open");
  const decided = ready.proposals.filter((item) => item.state !== "open");
  const off = disabled || busy;
  const decide = (item: LearningProposal, action: ProposalAction) =>
    void act(() => api.decide(item.proposal_id, action));

  return (
    <section className="bot-learning-card" aria-label={t("bots.learning")}>
      <header>
        <h3>{t("bots.learning")}</h3>
      </header>
      <p>{tf("bots.learning.intro", { name })}</p>
      <div className="bots-actions">
        <button
          type="button"
          className="btn alt sm"
          disabled={off || running !== null}
          onClick={() => void act(() => api.start(botId, "reflect"))}
        >
          {t("bots.learning.reflect")}
        </button>
        <button
          type="button"
          className="btn alt sm"
          disabled={off || running !== null}
          onClick={() => void act(() => api.start(botId, "dream"))}
        >
          {t("bots.learning.dream")}
        </button>
        {ready.legacy_files > 0 ? (
          <button
            type="button"
            className="btn alt sm"
            disabled={off}
            onClick={() => void act(() => api.importLegacy(botId))}
          >
            {tf("bots.learning.import", { count: ready.legacy_files })}
          </button>
        ) : null}
      </div>
      {running ? <p role="status">{t("bots.learning.running")}</p> : null}
      {ready.episodes.unreflected > 0 ? (
        <p className="bots-note">
          {tf("bots.learning.unreflected", { count: ready.episodes.unreflected })}
        </p>
      ) : null}
      {auto ? (
        <fieldset className="bot-learning-auto" disabled={off}>
          <label>
            <input
              type="checkbox"
              checked={auto.reflect}
              onChange={(event) =>
                void act(() => auto.set("bots.auto_reflect", event.target.checked))
              }
            />
            {t("bots.learning.autoReflect")}
          </label>
          <label>
            <input
              type="checkbox"
              checked={auto.dream}
              onChange={(event) =>
                void act(() => auto.set("bots.auto_dream", event.target.checked))
              }
            />
            {t("bots.learning.autoDream")}
          </label>
        </fieldset>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      <h4>{t("bots.learning.waiting")}</h4>
      {open.length === 0 ? <p className="bots-note">{t("bots.learning.none")}</p> : null}
      <ul className="bot-learning-list" aria-label={t("bots.learning.waiting")}>
        {open.map((item) => (
          <li key={item.proposal_id}>
            <strong>{t(OP_KEYS[item.op])}</strong>
            {item.claim ? <p className="bot-learning-claim">{item.claim}</p> : null}
            {item.target_claim ? (
              <p className="bot-learning-old">
                {tf("bots.learning.replaces", { claim: item.target_claim })}
              </p>
            ) : null}
            {item.why ? <small>{tf("bots.learning.why", { why: item.why })}</small> : null}
            {item.evidence.some((entry) => entry.quote) ? (
              <details>
                <summary>{t("bots.learning.evidence")}</summary>
                <ul>
                  {item.evidence.map((entry, index) =>
                    entry.quote ? (
                      // biome-ignore lint/suspicious/noArrayIndexKey: quotes have no id of their own
                      <li key={index}>
                        <q>{entry.quote}</q>
                      </li>
                    ) : null,
                  )}
                </ul>
              </details>
            ) : null}
            <div className="bots-actions">
              <button
                type="button"
                className="btn go sm"
                disabled={off}
                onClick={() => decide(item, "apply")}
              >
                {t("bots.learning.apply")}
              </button>
              <button
                type="button"
                className="btn alt sm"
                disabled={off}
                onClick={() => decide(item, "reject")}
              >
                {t("bots.learning.reject")}
              </button>
            </div>
          </li>
        ))}
      </ul>
      {decided.length > 0 ? (
        <details>
          <summary>{t("bots.learning.recent")}</summary>
          <ul className="bot-learning-list">
            {decided.map((item) => (
              <li key={item.proposal_id}>
                <span>
                  {t(OP_KEYS[item.op])} · {t(STATE_KEYS[item.state])}
                </span>
                {item.claim ? <p className="bot-learning-claim">{item.claim}</p> : null}
                {item.state === "applied" && UNDOABLE.has(item.op) ? (
                  <button
                    type="button"
                    className="btn alt sm"
                    disabled={off}
                    onClick={() => decide(item, "undo")}
                  >
                    {t("bots.learning.undo")}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <h4>{tf("bots.learning.records", { name })}</h4>
      {!ready.records_readable ? (
        <p className="bots-note">{tf("bots.learning.recordsUnreadable", { name })}</p>
      ) : ready.records.length === 0 ? (
        <p className="bots-note">{tf("bots.learning.noRecords", { name })}</p>
      ) : (
        <ul className="bot-learning-list" aria-label={tf("bots.learning.records", { name })}>
          {ready.records.map((item) => (
            <li key={item.memory_id}>
              <p className="bot-learning-claim">{item.claim}</p>
              <small>
                {tf("bots.learning.uses", {
                  uses: item.uses,
                  helpful: item.helpful,
                  harmful: item.harmful,
                })}
              </small>
            </li>
          ))}
        </ul>
      )}
      {ready.runs.length > 0 ? (
        <details>
          <summary>{t("bots.learning.runs")}</summary>
          <ul className="bot-learning-list">
            {ready.runs.map((run) => (
              <li key={run.run_id}>
                {run.state === "failed"
                  ? tf("bots.learning.run.failed", {
                      mode: t(MODE_KEYS[run.mode]),
                      reason: run.reason,
                    })
                  : tf("bots.learning.run.summary", {
                      mode: t(MODE_KEYS[run.mode]),
                      proposed: run.proposed,
                      rejected: run.rejected,
                    })}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {ready.tainted_sessions.length > 0 ? (
        <details>
          <summary>{t("bots.learning.tainted")}</summary>
          <p className="bots-note">{t("bots.learning.taintedHelp")}</p>
          <ul className="bot-learning-list">
            {ready.tainted_sessions.map((item) => (
              <li key={item.session_id}>
                <code>{item.session_id}</code> <small>{item.sources}</small>
                <button
                  type="button"
                  className="btn alt sm"
                  disabled={off}
                  onClick={() => void act(() => api.untaint(item.session_id))}
                >
                  {t("bots.learning.clear")}
                </button>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
