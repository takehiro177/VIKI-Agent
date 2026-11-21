/**
 * The four Bot usage summary tables (AC-13): Bots, Models and routes, Conversation kinds and
 * Largest conversations. Pure display over an already-parsed `UsageSummary`; number and date
 * formatting, kind labels and the Bot/route name fallbacks come from `usageFormat.ts`, and
 * catalog text from `i18n/botUsage.ts` through `t`/`tf`, the same pattern `UsageColumns.tsx` and
 * `DataParts.tsx` use.
 */
import { useId } from "react";
import type { UsageSummary } from "../../../clients/messaging/botUsageApi";
import { t, tf } from "../../../i18n/t";
import {
  botLabel,
  formatCount,
  KIND_LABELS,
  percentText,
  routeLabel,
  timeLabel,
  tokensOf,
} from "./usageFormat";
import "./UsageTables.css";

export interface UsageTablesProps {
  summary: UsageSummary;
  onTrace: (sessionId: string) => void;
  onOpen: (sessionId: string) => void;
}

function moreBotsNote(count: number): string {
  return tf(count === 1 ? "botUsage.tables.moreBots.one" : "botUsage.tables.moreBots.many", {
    count: formatCount(count),
  });
}

function moreRoutesNote(count: number): string {
  return tf(count === 1 ? "botUsage.tables.moreRoutes.one" : "botUsage.tables.moreRoutes.many", {
    count: formatCount(count),
  });
}

/** Routes have no id of their own on the wire: a connection/model pair is the row's identity. */
function routeKey(connectionId: string | null, model: string): string {
  return `${connectionId ?? "native"}:${model}`;
}

export function UsageTables({ summary, onTrace, onOpen }: UsageTablesProps) {
  const totalTokens = tokensOf(summary.totals);
  const botsById = new Map(summary.bots.map((bot) => [bot.bot_id, bot]));
  const botsHeadingId = useId();
  const routesHeadingId = useId();
  const largestHeadingId = useId();

  return (
    <div className="bot-usage-tables">
      <section className="bot-usage-table-section">
        <h2 id={botsHeadingId}>{t("botUsage.tables.bots")}</h2>
        {summary.bots.length === 0 ? (
          <p className="bot-usage-table-empty">{t("botUsage.tables.empty")}</p>
        ) : (
          // F13: its own scroll box, so a narrow `.app-view` (which hides horizontal overflow)
          // never cuts off this table's right-hand columns.
          // biome-ignore lint/a11y/useSemanticElements: a scroll region around a table, not a form group.
          <div
            className="bot-usage-scroll"
            role="region"
            aria-labelledby={botsHeadingId}
            // The region is the thing that scrolls, so it needs a tab stop to reach by keyboard.
            // biome-ignore lint/a11y/noNoninteractiveTabindex: see above
            tabIndex={0}
          >
            <table className="bot-usage-table">
              <thead>
                <tr>
                  <th scope="col">{t("botUsage.field.bot")}</th>
                  <th scope="col">{t("botUsage.field.tokens")}</th>
                  <th scope="col">{t("botUsage.field.share")}</th>
                  <th scope="col">{t("botUsage.field.input")}</th>
                  <th scope="col">{t("botUsage.field.output")}</th>
                  <th scope="col">{t("botUsage.field.cacheReads")}</th>
                  <th scope="col">{t("botUsage.field.cacheWrites")}</th>
                  <th scope="col">{t("botUsage.field.calls")}</th>
                  <th scope="col">{t("botUsage.field.conversations")}</th>
                </tr>
              </thead>
              <tbody>
                {summary.bots.map((bot) => (
                  <tr key={bot.bot_id}>
                    <th scope="row">{botLabel(bot.name, bot.current)}</th>
                    <td className="bot-usage-table-num">{formatCount(tokensOf(bot))}</td>
                    <td className="bot-usage-table-num">
                      {percentText(tokensOf(bot), totalTokens)}
                    </td>
                    <td className="bot-usage-table-num">{formatCount(bot.input)}</td>
                    <td className="bot-usage-table-num">{formatCount(bot.output)}</td>
                    <td className="bot-usage-table-num">{formatCount(bot.cache_read)}</td>
                    <td className="bot-usage-table-num">{formatCount(bot.cache_write)}</td>
                    <td className="bot-usage-table-num">{formatCount(bot.calls)}</td>
                    <td className="bot-usage-table-num">{formatCount(bot.sessions)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {summary.bots_omitted > 0 ? (
          <p className="bot-usage-table-note">{moreBotsNote(summary.bots_omitted)}</p>
        ) : null}
      </section>

      <section className="bot-usage-table-section">
        <h2 id={routesHeadingId}>{t("botUsage.tables.modelsAndRoutes")}</h2>
        {summary.routes.length === 0 ? (
          <p className="bot-usage-table-empty">{t("botUsage.tables.empty")}</p>
        ) : (
          // biome-ignore lint/a11y/useSemanticElements: a scroll region around a table, not a form group.
          <div
            className="bot-usage-scroll"
            role="region"
            aria-labelledby={routesHeadingId}
            // biome-ignore lint/a11y/noNoninteractiveTabindex: see above
            tabIndex={0}
          >
            <table className="bot-usage-table">
              <thead>
                <tr>
                  <th scope="col">{t("botUsage.field.route")}</th>
                  <th scope="col">{t("botUsage.field.model")}</th>
                  <th scope="col">{t("botUsage.field.tokens")}</th>
                  <th scope="col">{t("botUsage.field.share")}</th>
                  <th scope="col">{t("botUsage.field.input")}</th>
                  <th scope="col">{t("botUsage.field.output")}</th>
                  <th scope="col">{t("botUsage.field.cacheReads")}</th>
                  <th scope="col">{t("botUsage.field.cacheWrites")}</th>
                  <th scope="col">{t("botUsage.field.calls")}</th>
                </tr>
              </thead>
              <tbody>
                {summary.routes.map((route) => (
                  <tr key={routeKey(route.connection_id, route.model)}>
                    <th scope="row">{routeLabel(route.connection_id, route.connection_name)}</th>
                    <td>{route.model}</td>
                    <td className="bot-usage-table-num">{formatCount(tokensOf(route))}</td>
                    <td className="bot-usage-table-num">
                      {percentText(tokensOf(route), totalTokens)}
                    </td>
                    <td className="bot-usage-table-num">{formatCount(route.input)}</td>
                    <td className="bot-usage-table-num">{formatCount(route.output)}</td>
                    <td className="bot-usage-table-num">{formatCount(route.cache_read)}</td>
                    <td className="bot-usage-table-num">{formatCount(route.cache_write)}</td>
                    <td className="bot-usage-table-num">{formatCount(route.calls)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {summary.routes_omitted > 0 ? (
          <p className="bot-usage-table-note">{moreRoutesNote(summary.routes_omitted)}</p>
        ) : null}
      </section>

      <section className="bot-usage-table-section">
        <h2>{t("botUsage.tables.conversationKinds")}</h2>
        <table className="bot-usage-table">
          <thead>
            <tr>
              <th scope="col">{t("botUsage.field.kind")}</th>
              <th scope="col">{t("botUsage.field.tokens")}</th>
              <th scope="col">{t("botUsage.field.share")}</th>
              <th scope="col">{t("botUsage.field.calls")}</th>
            </tr>
          </thead>
          <tbody>
            {summary.kinds.map((kind) => (
              <tr key={kind.kind}>
                <th scope="row">{KIND_LABELS[kind.kind]}</th>
                <td className="bot-usage-table-num">{formatCount(tokensOf(kind))}</td>
                <td className="bot-usage-table-num">{percentText(tokensOf(kind), totalTokens)}</td>
                <td className="bot-usage-table-num">{formatCount(kind.calls)}</td>
              </tr>
            ))}
            <tr>
              <th scope="row">{t("botUsage.tables.total")}</th>
              <td className="bot-usage-table-num">{formatCount(totalTokens)}</td>
              <td className="bot-usage-table-num">{percentText(totalTokens, totalTokens)}</td>
              <td className="bot-usage-table-num">{formatCount(summary.totals.calls)}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section className="bot-usage-table-section">
        <h2 id={largestHeadingId}>{t("botUsage.tables.largestConversations")}</h2>
        {summary.largest.length === 0 ? (
          <p className="bot-usage-table-empty">{t("botUsage.tables.empty")}</p>
        ) : (
          // biome-ignore lint/a11y/useSemanticElements: a scroll region around a table, not a form group.
          <div
            className="bot-usage-scroll"
            role="region"
            aria-labelledby={largestHeadingId}
            // biome-ignore lint/a11y/noNoninteractiveTabindex: see above
            tabIndex={0}
          >
            <table className="bot-usage-table">
              <thead>
                <tr>
                  <th scope="col">{t("botUsage.field.bot")}</th>
                  <th scope="col">{t("botUsage.field.kind")}</th>
                  <th scope="col">{t("botUsage.field.tokens")}</th>
                  <th scope="col">{t("botUsage.field.calls")}</th>
                  <th scope="col">{t("botUsage.field.lastCall")}</th>
                  <th scope="col">{t("botUsage.tables.traceButton")}</th>
                  <th scope="col">{t("botUsage.tables.openButton")}</th>
                </tr>
              </thead>
              <tbody>
                {summary.largest.map((row) => {
                  const match = botsById.get(row.bot_id);
                  const botName = match
                    ? botLabel(match.name, match.current)
                    : botLabel(null, true);
                  return (
                    <tr key={row.session_id}>
                      <th scope="row">{botName}</th>
                      <td>{KIND_LABELS[row.kind]}</td>
                      <td className="bot-usage-table-num">{formatCount(tokensOf(row))}</td>
                      <td className="bot-usage-table-num">{formatCount(row.calls)}</td>
                      <td>{timeLabel(row.last_at)}</td>
                      <td>
                        <button
                          type="button"
                          className="btn txt sm"
                          aria-label={`${t("botUsage.tables.traceButton")} ${botName}`}
                          onClick={() => onTrace(row.session_id)}
                        >
                          {t("botUsage.tables.traceButton")}
                        </button>
                      </td>
                      <td>
                        {row.transcript ? (
                          <button
                            type="button"
                            className="btn txt sm"
                            aria-label={`${t("botUsage.tables.openButton")} ${botName}`}
                            onClick={() => onOpen(row.session_id)}
                          >
                            {t("botUsage.tables.openButton")}
                          </button>
                        ) : (
                          t("botUsage.tables.transcriptRemoved")
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
