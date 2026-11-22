/**
 * Pure formatting helpers for Bot usage (no React): number and date display, the kind labels and
 * the model-route/Bot name fallbacks every usage view needs. Catalog text comes from
 * `i18n/botUsage.ts` through `t`, the same pattern `DataParts.tsx` uses for Decision data.
 */
import type { UsageKind } from "../../../clients/messaging/botUsageApi";
import { t } from "../../../i18n/t";

const countFormatter = new Intl.NumberFormat("en-US");
const compactFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});
const dayFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});
const localDayFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
});
const timeFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** "12,345": the grouped, full-precision count for a tooltip or table cell. */
export function formatCount(value: number): string {
  return countFormatter.format(value);
}

/** "1.2M": a compact count for an axis tick or a tile, at most one fraction digit. */
export function compactCount(value: number): string {
  return compactFormatter.format(value);
}

/** "-" when `whole` is 0; else a percentage, one decimal under 10% and whole above it
 * ("4.2%", "37%"). */
export function percentText(part: number, whole: number): string {
  if (whole === 0) return t("botUsage.format.percentEmpty");
  const ratio = part / whole;
  const digits = ratio < 0.1 ? 1 : 0;
  return new Intl.NumberFormat("en-US", {
    style: "percent",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(ratio);
}

/** The total tokens a usage row carries: input, output and both cache types. */
export function tokensOf(row: {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}): number {
  return row.input + row.output + row.cache_read + row.cache_write;
}

/** The label of each conversation kind a usage row can carry, keyed by its wire code, in the
 * summary's display order (main chat, task threads, thread turns, routine runs, subagents). */
export const KIND_LABELS: Readonly<Record<UsageKind, string>> = {
  main: t("botUsage.kind.mainChat"),
  task: t("botUsage.kind.taskThreads"),
  bot_thread: t("botUsage.kind.threadTurns"),
  routine: t("botUsage.kind.routineRuns"),
  subagent: t("botUsage.kind.subagents"),
};

/** "Claude Code" for the native route (`id` null); else the connection's `name`, or "Removed
 * connection" once that connection no longer exists. */
export function routeLabel(id: string | null, name: string | null | undefined): string {
  if (id === null) return t("botUsage.route.claudeCode");
  return name || t("botUsage.route.removed");
}

/** `name`, or "Unknown Bot" when it is missing; with "(removed)" appended when the Bot is no
 * longer current. */
export function botLabel(name: string | null | undefined, current: boolean): string {
  const base = name || t("botUsage.bot.unknown");
  return current ? base : `${base} ${t("botUsage.bot.removedSuffix")}`;
}

/** "Nov 21": a day string ("YYYY-MM-DD") read as a calendar date, pinned to UTC so it never
 * shifts a day under a viewer's local offset. */
export function dayLabel(day: string): string {
  const iso = /^\d{4}-\d{2}-\d{2}$/.test(day) ? `${day}T00:00:00Z` : day;
  return dayFormatter.format(new Date(iso));
}

/** "Nov 21, 14:03:07": a call timestamp in the viewer's local time. */
export function timeLabel(timestamp: string): string {
  return timeFormatter.format(new Date(timestamp));
}

/** "Nov 21": a full timestamp's calendar day in the viewer's local time, no `timeZone` override
 * (same as `timeLabel`) — unlike `dayLabel`, which reads a plain "YYYY-MM-DD" day string as UTC.
 * Near a local midnight, the two disagree: use this one for a real timestamp (e.g. a log's
 * `oldest_at`), `dayLabel` only for a date-only wire string. */
export function localDayLabel(timestamp: string): string {
  return localDayFormatter.format(new Date(timestamp));
}
