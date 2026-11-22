/**
 * The daily column chart for Bot usage (AC-12): one bar per date, a single token series. Shows
 * the same date range as its caller's table view (`DailyTable`, exported alongside it): one tall
 * chart over all Bots, or several at `height={80}` side by side with one shared `max`. Catalog
 * text comes from `i18n/botUsage.ts` through `t`/`tf`, the same pattern `DataParts.tsx` uses for
 * Decision data; numbers and day strings come from `usageFormat.ts`.
 */
import { useId, useState } from "react";
import { t, tf } from "../../../i18n/t";
import { compactCount, dayLabel, formatCount } from "./usageFormat";
import "./UsageColumns.css";

export interface UsageColumnsProps {
  /** Shown above the plot and folded into its accessible group label. */
  title: string;
  /** One date ("YYYY-MM-DD") per column, left to right in the order given. */
  dates: readonly string[];
  /** One value per date, aligned by index with `dates`. */
  values: readonly { tokens: number; calls: number }[];
  /** The scale's top: shared across a row of charts so their bars stay comparable. */
  max: number;
  height: 160 | 80;
}

export interface DailyTableProps {
  dates: readonly string[];
  series: readonly { label: string; values: readonly number[] }[];
}

const MOVES: Readonly<Record<string, number>> = { ArrowLeft: -1, ArrowRight: 1 };

/** Px reserved above the max gridline for the peak column's floating label (F2): without it, a
 * bar near the chart's own top edge pushes its label out past the chart box and into whatever
 * sits above (the title, or in a grid of small charts, the row above). Ticks and bars scale to
 * `height - PEAK_HEADROOM` instead of the full height, so the peak label always has room to float
 * above its bar and still land inside the chart box. F10: the label's own lift (its CSS, a fixed
 * 4px plus a pinned line-height) is what has to fit this budget, not the label's `bottom` offset
 * itself — `bottom` already lands its bottom edge on the bar's top with no lift of its own. */
const PEAK_HEADROOM = 20;

export type PeakAnchor = "start" | "center" | "end";

/** Where the peak label anchors horizontally (F11): centred in the middle third of the days, but
 * pinned to its own slot's near edge in the first or last third. A centred label is wider than its
 * slot near either end of the row, so centring it there hangs half of it past the row's own left
 * edge, or past the right edge into the tick gutter; anchoring to the slot's own edge instead never
 * reaches past it, whichever slot in that third it is. */
export function peakAnchor(index: number, count: number): PeakAnchor {
  if (count <= 0) return "center";
  if (index < count / 3) return "start";
  if (index >= (count * 2) / 3) return "end";
  return "center";
}

function tokensPhrase(count: number): string {
  return tf(count === 1 ? "botUsage.count.tokens.one" : "botUsage.count.tokens.many", {
    count: formatCount(count),
  });
}

function callsPhrase(count: number): string {
  return tf(count === 1 ? "botUsage.count.calls.one" : "botUsage.count.calls.many", {
    count: formatCount(count),
  });
}

function tooltipText(date: string, value: { tokens: number; calls: number }): string {
  return tf("botUsage.columns.tooltip", {
    day: dayLabel(date),
    tokens: tokensPhrase(value.tokens),
    calls: callsPhrase(value.calls),
  });
}

/** The group's accessible name: the title, then the date range it plots (collapsed to one date
 * when there is only one). */
function groupLabel(title: string, dates: readonly string[]): string {
  const first = dates[0];
  const last = dates[dates.length - 1];
  if (first === undefined || last === undefined) return title;
  const range = first === last ? dayLabel(first) : `${dayLabel(first)}–${dayLabel(last)}`;
  return `${title} · ${range}`;
}

export function UsageColumns({ title, dates, values, max, height }: UsageColumnsProps) {
  const [active, setActive] = useState<number | null>(null);
  const lastIndex = dates.length - 1;
  const total = values.reduce((sum, value) => sum + value.tokens, 0);

  if (total === 0) {
    return (
      <div className="bot-usage-columns">
        <p className="bot-usage-columns-title">{title}</p>
        <p className="bot-usage-columns-empty">{t("botUsage.columns.empty")}</p>
      </div>
    );
  }

  let peakIndex = 0;
  let peakValue = values[0]?.tokens ?? 0;
  for (let index = 1; index <= lastIndex; index++) {
    const tokens = values[index]?.tokens ?? 0;
    if (tokens > peakValue) {
      peakValue = tokens;
      peakIndex = index;
    }
  }
  const midIndex = Math.floor(lastIndex / 2);
  const plotHeight = height - PEAK_HEADROOM;

  function clamp(index: number): number {
    return Math.min(lastIndex, Math.max(0, index));
  }

  const activeValue = active !== null ? values[active] : undefined;
  const activeDate = active !== null ? dates[active] : undefined;

  return (
    <div className="bot-usage-columns">
      <p className="bot-usage-columns-title">{title}</p>
      {/* A <fieldset> (Biome's suggestion for role="group") carries form semantics this chart
          plot does not want. */}
      {/* biome-ignore lint/a11y/useSemanticElements: see above */}
      <div
        className="bot-usage-columns-plot"
        role="group"
        aria-label={groupLabel(title, dates)}
        // The plot takes keyboard focus and Left/Right/Home/End input on purpose, so it needs a
        // tab stop.
        // biome-ignore lint/a11y/noNoninteractiveTabindex: see above
        tabIndex={0}
        onFocus={() => setActive((current) => current ?? lastIndex)}
        onBlur={() => setActive(null)}
        onMouseLeave={() => setActive(null)}
        onKeyDown={(event) => {
          // A candidate window's arrows belong to the IME, never to this plot.
          if (event.nativeEvent.isComposing || event.keyCode === 229) return;
          const step = MOVES[event.key];
          if (step !== undefined) {
            event.preventDefault();
            setActive((current) => clamp((current ?? lastIndex) + step));
          } else if (event.key === "Home") {
            event.preventDefault();
            setActive(0);
          } else if (event.key === "End") {
            event.preventDefault();
            setActive(lastIndex);
          } else if (event.key === "Escape") {
            setActive(null);
          }
        }}
      >
        <div className="bot-usage-columns-chart" style={{ height }}>
          <div className="bot-usage-columns-grid" aria-hidden="true">
            {[0, plotHeight / 2, plotHeight].map((at) => (
              <span key={at} className="bot-usage-columns-gridline" style={{ bottom: `${at}px` }} />
            ))}
          </div>
          {/* Its own gutter (CSS `padding-right`/`--tick-gutter`), so a tick label never sits over
              a column (F2): the grid and row above stop short of it, by the same amount. */}
          <div className="bot-usage-columns-ticks" aria-hidden="true">
            <span className="bot-usage-columns-tick" style={{ bottom: "0px" }}>
              {compactCount(0)}
            </span>
            <span className="bot-usage-columns-tick" style={{ bottom: `${plotHeight / 2}px` }}>
              {compactCount(max / 2)}
            </span>
            <span className="bot-usage-columns-tick" style={{ bottom: `${plotHeight}px` }}>
              {compactCount(max)}
            </span>
          </div>
          <div className="bot-usage-columns-row">
            {dates.map((date, index) => {
              const value = values[index] ?? { tokens: 0, calls: 0 };
              const barPx = max > 0 ? (value.tokens / max) * plotHeight : 0;
              return (
                // The hover zone is a visual shortcut; Left/Right/Home/End on the group above
                // reach every day without a mouse.
                // biome-ignore lint/a11y/noStaticElementInteractions: see above
                <div
                  key={date}
                  className="bot-usage-columns-slot"
                  style={{ opacity: active === null || active === index ? 1 : 0.45 }}
                  onMouseEnter={() => setActive(index)}
                >
                  {index === peakIndex ? (
                    <span
                      className="bot-usage-columns-peak"
                      data-anchor={peakAnchor(peakIndex, dates.length)}
                      style={{ bottom: `${barPx}px` }}
                    >
                      {compactCount(value.tokens)}
                    </span>
                  ) : null}
                  {value.tokens > 0 ? (
                    <span className="bot-usage-columns-bar" style={{ height: `${barPx}px` }} />
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
        <div className="bot-usage-columns-xlabels">
          <span>{dayLabel(dates[0] ?? "")}</span>
          <span>{dayLabel(dates[midIndex] ?? "")}</span>
          <span>{dayLabel(dates[lastIndex] ?? "")}</span>
        </div>
      </div>
      <p className="bot-usage-columns-tooltip" role="status">
        {activeDate !== undefined && activeValue !== undefined
          ? tooltipText(activeDate, activeValue)
          : ""}
      </p>
    </div>
  );
}

export function DailyTable({ dates, series }: DailyTableProps) {
  const captionId = useId();
  const newestFirst = dates.map((_, index) => index).reverse();
  return (
    // F13: its own scroll box, so a narrow `.app-view` (which hides horizontal overflow) never
    // cuts off this table's right-hand columns; `aria-labelledby` points at its own (sr-only)
    // caption, since nothing else here names this specific table.
    // biome-ignore lint/a11y/useSemanticElements: a scroll region around a table, not a form group.
    <div
      className="bot-usage-scroll"
      role="region"
      aria-labelledby={captionId}
      // The region is the thing that scrolls, so it needs a tab stop to reach by keyboard.
      // biome-ignore lint/a11y/noNoninteractiveTabindex: see above
      tabIndex={0}
    >
      <table className="bot-usage-columns-table">
        <caption id={captionId} className="sr-only">
          {t("botUsage.dashboard.dailyTokens")}
        </caption>
        <thead>
          <tr>
            <th scope="col">{t("botUsage.field.date")}</th>
            {series.map((one) => (
              <th scope="col" key={one.label}>
                {one.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {newestFirst.map((index) => (
            <tr key={dates[index]}>
              <th scope="row">{dayLabel(dates[index] ?? "")}</th>
              {series.map((one) => (
                <td className="bot-usage-columns-num" key={one.label}>
                  {formatCount(one.values[index] ?? 0)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
