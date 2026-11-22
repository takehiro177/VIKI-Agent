/**
 * UsageNotes: the measured facts and coverage notes under the Bot usage dashboard (AC-13).
 * "Measured facts" turns the summary's totals into a few derived sentences, each shown only when
 * its own denominator is non-zero. "About these numbers" explains what the log counts, plus its
 * coverage gaps (log-only sessions, unlogged starts, still-reading conversations), each gap line
 * shown only when its own coverage count is non-zero. Catalog text comes from `i18n/botUsage.ts`
 * through `t`/`tf`, the same pattern `UsageTables.tsx` and `DataParts.tsx` use; numbers and dates
 * come from `usageFormat.ts`.
 */
import type { UsageKindRow, UsageSummary } from "../../../clients/messaging/botUsageApi";
import { t, tf } from "../../../i18n/t";
import { formatCount, localDayLabel, percentText, tokensOf } from "./usageFormat";
import "./UsageNotes.css";

export interface UsageNotesProps {
  summary: UsageSummary;
}

function kindTokens(summary: UsageSummary, kind: UsageKindRow["kind"]): number {
  const row = summary.kinds.find((one) => one.kind === kind);
  return row ? tokensOf(row) : 0;
}

function measuredFacts(summary: UsageSummary): string[] {
  const { totals } = summary;
  const inputSide = totals.input + totals.cache_read + totals.cache_write;
  const allTokens = tokensOf(totals);
  const facts: string[] = [];

  if (inputSide > 0) {
    facts.push(
      tf("botUsage.notes.cacheReadShare", { share: percentText(totals.cache_read, inputSide) }),
    );
  }
  if (totals.calls > 0) {
    facts.push(
      tf("botUsage.notes.avgContext", {
        count: formatCount(Math.round(inputSide / totals.calls)),
      }),
    );
  }
  if (allTokens > 0) {
    facts.push(
      tf("botUsage.notes.routineShare", {
        share: percentText(kindTokens(summary, "routine"), allTokens),
      }),
    );
    facts.push(
      tf("botUsage.notes.subagentShare", {
        share: percentText(kindTokens(summary, "subagent"), allTokens),
      }),
    );
    const largest = summary.largest[0];
    if (largest) {
      facts.push(
        tf("botUsage.notes.largestShare", { share: percentText(tokensOf(largest), allTokens) }),
      );
    }
  }
  return facts;
}

/** "About these numbers", in display order: the fixed intro, each coverage-gap line that has a
 * non-zero count, the log's row count and oldest date (once the log holds any row), then the two
 * fixed closing notes. */
function aboutNumbersLines(summary: UsageSummary): string[] {
  const { coverage } = summary;
  const lines: string[] = [
    tf("botUsage.notes.coverage", {
      days: formatCount(coverage.max_age_days),
      rows: formatCount(coverage.max_rows),
    }),
  ];

  if (coverage.log_only_sessions > 0) {
    lines.push(
      tf(
        coverage.log_only_sessions === 1
          ? "botUsage.notes.removedTranscripts.one"
          : "botUsage.notes.removedTranscripts.many",
        {
          count: formatCount(coverage.log_only_sessions),
          calls: formatCount(coverage.log_only_calls),
        },
      ),
    );
  }
  if (coverage.unlogged > 0) {
    lines.push(
      tf(
        coverage.unlogged === 1
          ? "botUsage.notes.noTranscript.one"
          : "botUsage.notes.noTranscript.many",
        { count: formatCount(coverage.unlogged) },
      ),
    );
  }
  if (coverage.pending > 0) {
    lines.push(
      tf(
        coverage.pending === 1
          ? "botUsage.notes.stillReading.one"
          : "botUsage.notes.stillReading.many",
        { count: formatCount(coverage.pending) },
      ),
    );
  }
  if (coverage.oldest_at !== null) {
    lines.push(
      tf("botUsage.notes.logHolds", {
        rows: formatCount(coverage.rows),
        date: localDayLabel(coverage.oldest_at),
      }),
    );
  }
  lines.push(t("botUsage.notes.cleanupPeriod"), t("botUsage.notes.gatewayLimited"));
  return lines;
}

export function UsageNotes({ summary }: UsageNotesProps) {
  const facts = measuredFacts(summary);
  const aboutLines = aboutNumbersLines(summary);

  return (
    <div className="bot-usage-notes">
      <section className="bot-usage-notes-section">
        <h2>{t("botUsage.notes.measuredFacts")}</h2>
        <ul className="bot-usage-notes-list">
          {facts.map((fact) => (
            <li key={fact}>{fact}</li>
          ))}
        </ul>
      </section>
      <section className="bot-usage-notes-section">
        <h2>{t("botUsage.notes.aboutNumbers")}</h2>
        <ul className="bot-usage-notes-list">
          {aboutLines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
