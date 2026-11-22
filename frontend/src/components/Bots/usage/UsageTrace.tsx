/**
 * UsageTrace: the conversation trace panel for one Bot session (AC-13), opened from the Largest
 * conversations table (UsageTables.tsx). Calls are grouped by `prompt_id` in first-appearance
 * order: a non-null id names a turn ("Turn {n}") unless every one of its calls is a subagent
 * call ("Subagent run"), and a null id collects every call with no turn id of its own ("Calls
 * without a turn id"). Pure display over an already-parsed `UsageTrace`; number and date
 * formatting, the kind label and the route fallback come from `usageFormat.ts`, and catalog text
 * from `i18n/botUsage.ts` through `t`/`tf`, the same pattern `UsageTables.tsx` and
 * `UsageNotes.tsx` use.
 */
import { useId } from "react";
import type {
  UsageCallRow,
  UsageTrace as UsageTraceData,
} from "../../../clients/messaging/botUsageApi";
import { t, tf } from "../../../i18n/t";
import { formatCount, KIND_LABELS, routeLabel, timeLabel, tokensOf } from "./usageFormat";
import "./UsageTrace.css";

export interface UsageTraceProps {
  trace: UsageTraceData;
  botName: string;
  onClose: () => void;
}

interface CallGroup {
  key: string | null;
  label: string;
  calls: UsageCallRow[];
}

function focusOnMount(element: HTMLElement | null): void {
  element?.focus();
}

function callsLabel(count: number): string {
  return tf(count === 1 ? "botUsage.count.calls.one" : "botUsage.count.calls.many", {
    count: formatCount(count),
  });
}

function tokensLabel(count: number): string {
  return tf(count === 1 ? "botUsage.count.tokens.one" : "botUsage.count.tokens.many", {
    count: formatCount(count),
  });
}

/** Groups calls by `prompt_id` in first-appearance order. Every call with no turn id (`null`)
 * collects into one group, positioned where the first of them appears. A group is a subagent run
 * only when every one of its calls is a subagent call; the rest are numbered turns, the number
 * counting only turn groups so an interleaved subagent run or no-turn-id group never opens a gap
 * in the sequence. */
function groupCalls(calls: readonly UsageCallRow[]): CallGroup[] {
  // A Map keeps first-appearance order (its iteration order is insertion order), so grouping
  // needs no separate index bookkeeping.
  const groups = new Map<string | null, UsageCallRow[]>();
  for (const call of calls) {
    const existing = groups.get(call.prompt_id);
    if (existing) existing.push(call);
    else groups.set(call.prompt_id, [call]);
  }
  let turnNumber = 0;
  return Array.from(groups, ([key, groupedCalls]) => {
    let label: string;
    if (key === null) {
      label = t("botUsage.trace.noTurnId");
    } else if (groupedCalls.every((call) => call.agent)) {
      label = t("botUsage.trace.subagentRun");
    } else {
      turnNumber += 1;
      label = tf("botUsage.trace.turnLabel", { n: turnNumber });
    }
    return { key, label, calls: groupedCalls };
  });
}

export function UsageTrace({ trace, botName, onClose }: UsageTraceProps) {
  const groups = groupCalls(trace.calls);
  const groupsBaseId = useId();

  return (
    <div className="bot-usage-trace">
      <h2 ref={focusOnMount} tabIndex={-1}>
        {t("botUsage.trace.heading")}
      </h2>
      <p className="bot-usage-trace-line">
        {tf("botUsage.trace.line", {
          bot: botName,
          kind: KIND_LABELS[trace.kind],
          route: routeLabel(trace.connection_id, trace.connection_name),
          calls: callsLabel(trace.calls.length),
        })}
      </p>
      {trace.omitted > 0 ? (
        <p className="bot-usage-trace-omitted">
          {tf("botUsage.trace.omitted", { count: formatCount(trace.omitted) })}
        </p>
      ) : null}
      <div className="bot-usage-trace-groups">
        {groups.map((group, index) => {
          const tokens = group.calls.reduce((sum, call) => sum + tokensOf(call), 0);
          const summaryId = `${groupsBaseId}-${index}`;
          return (
            <details
              key={group.key ?? "no-turn-id"}
              open={index === groups.length - 1}
              className="bot-usage-trace-group"
            >
              <summary id={summaryId}>
                {tf("botUsage.trace.turnSummary", {
                  label: group.label,
                  calls: callsLabel(group.calls.length),
                  tokens: tokensLabel(tokens),
                })}
              </summary>
              {/* F13: its own scroll box, so a narrow `.app-view` (which hides horizontal
                  overflow) never cuts off this table's right-hand columns; named by this group's
                  own summary line above. */}
              {/* biome-ignore lint/a11y/useSemanticElements: a scroll region around a table, not a form group. */}
              <div
                className="bot-usage-scroll"
                role="region"
                aria-labelledby={summaryId}
                // The region is the thing that scrolls, so it needs a tab stop to reach by keyboard.
                // biome-ignore lint/a11y/noNoninteractiveTabindex: see above
                tabIndex={0}
              >
                <table className="bot-usage-trace-table">
                  <thead>
                    <tr>
                      <th scope="col">{t("botUsage.field.time")}</th>
                      <th scope="col">{t("botUsage.field.model")}</th>
                      <th scope="col">{t("botUsage.field.thread")}</th>
                      <th scope="col">{t("botUsage.field.input")}</th>
                      <th scope="col">{t("botUsage.field.output")}</th>
                      <th scope="col">{t("botUsage.field.cacheReads")}</th>
                      <th scope="col">{t("botUsage.field.cacheWrites")}</th>
                      <th scope="col">{t("botUsage.field.context")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.calls.map((call) => (
                      <tr key={call.at}>
                        <td>{timeLabel(call.at)}</td>
                        <td>{call.model}</td>
                        <td>
                          {call.agent
                            ? t("botUsage.trace.threadSubagent")
                            : t("botUsage.trace.threadMain")}
                        </td>
                        <td className="bot-usage-trace-num">{formatCount(call.input)}</td>
                        <td className="bot-usage-trace-num">{formatCount(call.output)}</td>
                        <td className="bot-usage-trace-num">{formatCount(call.cache_read)}</td>
                        <td className="bot-usage-trace-num">{formatCount(call.cache_write)}</td>
                        <td className="bot-usage-trace-num">
                          {formatCount(call.input + call.cache_read + call.cache_write)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          );
        })}
      </div>
      <button type="button" className="btn txt sm" onClick={onClose}>
        {t("botUsage.trace.close")}
      </button>
    </div>
  );
}
