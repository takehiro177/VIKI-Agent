/**
 * BotUsage: the Bot usage dashboard container (AC-13). A period picker loads one
 * `BotUsageApi.summary()` answer and composes it into a hero/tiles stat row, the daily and
 * per-Bot charts (`UsageColumns`/`DailyTable`), the four summary tables (`UsageTables`), a
 * session trace (`UsageTrace`) and the coverage notes (`UsageNotes`). Catalog text comes from
 * `i18n/botUsage.ts` through `t`, the same pattern every panel it composes already uses; numbers
 * and labels come from `usageFormat.ts`.
 *
 * A switch of period (or Refresh) starts a new `summary()` call without clearing the last one:
 * the previous render stays up, `aria-busy` and dimmed, until that call's own answer lands. Each
 * call carries its own `AbortController`; a call superseded by a newer one before it resolves is
 * aborted, and its handler checks that controller's own `aborted` flag before touching state, so
 * a stale answer that still resolves (a fake that ignores the signal, in a test) is dropped.
 */
import { useEffect, useState } from "react";
import type {
  BotUsageApi,
  UsageDays,
  UsageSummary,
  UsageTotals,
  UsageTrace as UsageTraceData,
} from "../../../clients/messaging/botUsageApi";
import type { Key } from "../../../i18n";
import { t } from "../../../i18n/t";
import { Loading } from "../../Loading/Loading";
import { DailyTable, UsageColumns } from "./UsageColumns";
import { UsageNotes } from "./UsageNotes";
import { UsageTables } from "./UsageTables";
import { UsageTrace } from "./UsageTrace";
import { botLabel, compactCount, tokensOf } from "./usageFormat";
import "./BotUsage.css";

export interface BotUsageProps {
  api: BotUsageApi;
  onOpenSession: (sessionId: string) => void;
}

interface DaySeries {
  tokens: number;
  calls: number;
}

interface Panel {
  key: string;
  title: string;
  series: DaySeries[];
}

interface ChartsData {
  allBots: DaySeries[];
  panels: Panel[];
}

interface TracedState {
  trace: UsageTraceData;
  botName: string;
  opener: HTMLElement | null;
}

const PERIODS: readonly UsageDays[] = [7, 30, 90];
const PERIOD_KEYS: Readonly<Record<UsageDays, Key>> = {
  7: "botUsage.dashboard.last7",
  30: "botUsage.dashboard.last30",
  90: "botUsage.dashboard.last90",
};
const TOP_BOTS = 7;
// The grand total across every Bot: reused as UsageColumns' own caption for the all-Bots chart
// and as the combined DailyTable's first column, the same way "Other Bots" below is reused
// across its own panel and column.
const ALL_BOTS_LABEL = t("botUsage.columns.allBots");

const TILE_FIELDS: ReadonlyArray<{ key: keyof UsageTotals; label: string }> = [
  { key: "input", label: t("botUsage.field.input") },
  { key: "output", label: t("botUsage.field.output") },
  { key: "cache_read", label: t("botUsage.field.cacheReads") },
  { key: "cache_write", label: t("botUsage.field.cacheWrites") },
  { key: "calls", label: t("botUsage.field.calls") },
];

function focusHeading(element: HTMLHeadingElement | null): void {
  element?.focus();
}

function emptySeries(days: number): DaySeries[] {
  return Array.from({ length: days }, () => ({ tokens: 0, calls: 0 }));
}

function addSeries(a: readonly DaySeries[], b: readonly DaySeries[] | undefined): DaySeries[] {
  if (!b) return [...a];
  return a.map((slot, index) => {
    const other = b[index];
    return other ? { tokens: slot.tokens + other.tokens, calls: slot.calls + other.calls } : slot;
  });
}

function peak(series: readonly DaySeries[]): number {
  return series.reduce((max, slot) => Math.max(max, slot.tokens), 0);
}

/** One zero-filled `DaySeries[]` per `bot_id` (a `null` row folds into its own group), summed
 * from `summary.daily`'s non-zero rows and aligned to `summary.dates` by index. */
function dailyByBot(summary: UsageSummary): Map<string | null, DaySeries[]> {
  const dateIndex = new Map(summary.dates.map((date, index) => [date, index]));
  const byBot = new Map<string | null, DaySeries[]>();
  for (const row of summary.daily) {
    const at = dateIndex.get(row.date);
    if (at === undefined) continue;
    let series = byBot.get(row.bot_id);
    if (!series) {
      series = emptySeries(summary.dates.length);
      byBot.set(row.bot_id, series);
    }
    const slot = series[at];
    if (slot) {
      slot.tokens += row.tokens;
      slot.calls += row.calls;
    }
  }
  return byBot;
}

/** The all-Bots total series, plus the "By Bot" grid's panels: the top `TOP_BOTS` Bots by total
 * tokens (`summary.bots` is already ordered that way), then one "Other Bots" panel — every bot
 * ranked beyond it, plus every `null` daily row — once that combined series carries any tokens. */
function chartsData(summary: UsageSummary): ChartsData {
  const byBot = dailyByBot(summary);
  const top = summary.bots.slice(0, TOP_BOTS);
  const panels: Panel[] = top.map((bot) => ({
    key: bot.bot_id,
    title: botLabel(bot.name, bot.current),
    series: byBot.get(bot.bot_id) ?? emptySeries(summary.dates.length),
  }));
  let other = emptySeries(summary.dates.length);
  for (const bot of summary.bots.slice(TOP_BOTS)) other = addSeries(other, byBot.get(bot.bot_id));
  other = addSeries(other, byBot.get(null));
  if (other.some((slot) => slot.tokens > 0)) {
    panels.push({ key: "other-bots", title: t("botUsage.dashboard.otherBots"), series: other });
  }
  let allBots = emptySeries(summary.dates.length);
  for (const series of byBot.values()) allBots = addSeries(allBots, series);
  return { allBots, panels };
}

export function BotUsage({ api, onOpenSession }: BotUsageProps) {
  const [days, setDays] = useState<UsageDays>(30);
  const [reloadKey, setReloadKey] = useState(0);
  const [shown, setShown] = useState<{ days: UsageDays; summary: UsageSummary } | null>(null);
  const [busy, setBusy] = useState(true);
  const [failed, setFailed] = useState(false);
  const [tableShown, setTableShown] = useState(false);
  const [traced, setTraced] = useState<TracedState | null>(null);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadKey forces a reload
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    api.summary(days, controller.signal).then(
      (summary) => {
        if (controller.signal.aborted) return;
        setShown({ days, summary });
        setFailed(false);
        setBusy(false);
      },
      () => {
        if (controller.signal.aborted) return;
        // Discard any earlier good answer too (F22): a reload that fails is not "still showing
        // the old numbers", it is a failed attempt, exactly like a failed first load — the
        // alert branch below only fires once `summary` is null.
        setShown(null);
        setFailed(true);
        setBusy(false);
      },
    );
    return () => controller.abort();
  }, [api, days, reloadKey]);

  function handleTrace(sessionId: string): void {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    api.trace(sessionId).then(
      (trace) => {
        const match = shown?.summary.bots.find((bot) => bot.bot_id === trace.bot_id);
        const botName = match ? botLabel(match.name, match.current) : botLabel(null, true);
        setTraced({ trace, botName, opener });
      },
      () => {
        // Leave the dashboard as is: a failed trace fetch has no Spec'd UI of its own, and
        // nothing was showing yet for this click to replace.
      },
    );
  }

  function closeTrace(): void {
    traced?.opener?.focus();
    setTraced(null);
  }

  const summary = shown?.summary ?? null;
  const charts = summary ? chartsData(summary) : null;
  const byBotMax = charts ? Math.max(0, ...charts.panels.map((panel) => peak(panel.series))) : 0;

  return (
    <section className="bot-usage">
      <header className="page-head">
        <h1 className="page-title" ref={focusHeading} tabIndex={-1}>
          {t("botUsage.dashboard.title")}
        </h1>
        {/* A labelled group of view buttons, not form fields — fieldset/legend (biome's
            suggestion for role="group") is for form controls; a div is right here. */}
        {/* biome-ignore lint/a11y/useSemanticElements: see above */}
        <div
          className="page-actions bot-usage-period"
          role="group"
          aria-label={t("botUsage.dashboard.period")}
        >
          {PERIODS.map((period) => (
            <button
              key={period}
              type="button"
              className="btn txt sm"
              aria-pressed={days === period}
              onClick={() => setDays(period)}
            >
              {t(PERIOD_KEYS[period])}
            </button>
          ))}
          <button type="button" className="btn txt sm" onClick={() => setReloadKey((k) => k + 1)}>
            {t("botUsage.dashboard.refresh")}
          </button>
        </div>
      </header>

      {summary === null && busy ? <Loading>{t("botUsage.dashboard.loading")}</Loading> : null}
      {summary === null && !busy && failed ? (
        <p role="alert">{t("botUsage.dashboard.error")}</p>
      ) : null}

      {summary && charts ? (
        <div className="bot-usage-body" aria-busy={busy}>
          <div className="bot-usage-stats">
            <div className="bot-usage-hero">
              <p className="lbl bot-usage-stat-label">{t("botUsage.dashboard.totalTokens")}</p>
              <p className="bot-usage-hero-value">{compactCount(tokensOf(summary.totals))}</p>
            </div>
            <div className="bot-usage-tiles">
              {TILE_FIELDS.map((tile) => (
                <div className="bot-usage-tile" key={tile.key}>
                  <p className="lbl bot-usage-stat-label">{tile.label}</p>
                  <p className="bot-usage-tile-value">{compactCount(summary.totals[tile.key])}</p>
                </div>
              ))}
            </div>
          </div>

          <section className="bot-usage-section">
            <h2>{t("botUsage.dashboard.dailyTokens")}</h2>
            <UsageColumns
              title={ALL_BOTS_LABEL}
              dates={summary.dates}
              values={charts.allBots}
              max={peak(charts.allBots)}
              height={160}
            />
          </section>

          <section className="bot-usage-section">
            <h2>{t("botUsage.dashboard.byBot")}</h2>
            <div className="bot-usage-bybot-grid">
              {charts.panels.map((panel) => (
                <UsageColumns
                  key={panel.key}
                  title={panel.title}
                  dates={summary.dates}
                  values={panel.series}
                  max={byBotMax}
                  height={80}
                />
              ))}
            </div>
          </section>

          <button
            type="button"
            className="btn txt sm"
            aria-expanded={tableShown}
            onClick={() => setTableShown((current) => !current)}
          >
            {t("botUsage.dashboard.showTable")}
          </button>
          {tableShown ? (
            <DailyTable
              dates={summary.dates}
              series={[
                { label: ALL_BOTS_LABEL, values: charts.allBots.map((slot) => slot.tokens) },
                ...charts.panels.map((panel) => ({
                  label: panel.title,
                  values: panel.series.map((slot) => slot.tokens),
                })),
              ]}
            />
          ) : null}

          <UsageTables summary={summary} onTrace={handleTrace} onOpen={onOpenSession} />
          {traced ? (
            <UsageTrace trace={traced.trace} botName={traced.botName} onClose={closeTrace} />
          ) : null}
          <UsageNotes summary={summary} />
        </div>
      ) : null}
    </section>
  );
}
