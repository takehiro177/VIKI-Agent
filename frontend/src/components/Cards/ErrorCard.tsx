/** Session errors use fixed copy for known codes and the server message for unknown codes.
 * Rate-limit cards distinguish rejected turns from warnings and format reset times locally.
 * Server-provided messages render as text. */
import type { ReactElement } from "react";
import { t } from "../../i18n/t";
import type { ErrorCode, ErrorData, RateLimitData } from "../../protocol";
import "./Cards.css"; // .card-head, .card-actions, reused rather than a second copy
import "./Errors.css";

interface ErrorCopy {
  title: string;
  line: string;
}

/** Fixed copy for known codes; other codes use the generic title and server message. */
const ERROR_COPY: Partial<Record<ErrorCode, ErrorCopy>> = {
  crashed: {
    title: "Claude Code ended unexpectedly",
    line: "If the conversation record is still available, send again to resume from here. Any work already done remains in place.",
  },
  forced_stop: {
    title: "Claude Code was terminated because it could not be stopped",
    line: "Any work already done remains in place. Check the conversation record and changes before sending your next message.",
  },
  provider_disconnected: {
    title: "Delivery to Claude Code is unconfirmed",
    line: "Check the send record before deciding what to send next. It will not be sent again automatically.",
  },
  not_logged_in: {
    title: "Not signed in to Claude Code",
    line: "Open claude in a terminal, run /login, then send again.",
  },
  cli_missing: {
    title: "Claude Code was not found",
    line: "Check in a terminal that claude is installed.",
  },
  billing: {
    title: "This session could not run on your subscription",
    line: "Claude Code is signed in to, or set up to bill, an account other than your Claude subscription. Sign in with your Claude subscription, remove the setting that Settings › Providers lists, or turn on Let Claude Code use its other sign-in methods in Settings › Chat. Then restart VIKI Agent. A setting listed there also blocks chats on a model connection.",
  },
};

export interface ErrorCardProps {
  data: ErrorData;
  /** Optional retry action, shown only for a recoverable crash. */
  onRetry?: () => void;
  /** Opens Settings › Chat with the live-session-limit field focused. Absent: the
   * too_many_sessions card offers no button. Never retries or closes anything itself. */
  onRaiseLimit?: () => void;
}

export function ErrorCard({ data, onRetry, onRaiseLimit }: ErrorCardProps): ReactElement {
  const copy = ERROR_COPY[data.code];
  // too_many_sessions is deliberately not in ERROR_COPY: its line is already the server's own
  // words (the number lives there), so only the title needs fixed copy.
  const title =
    copy?.title ??
    (data.code === "too_many_sessions" ? t("shell.limit.error_title") : "Reply failed");
  const line = copy?.line ?? data.message;
  return (
    <div className="error-card">
      <div className="card-head">
        <span className="chip dim">Error</span>
      </div>
      <div className="error-title jp">{title}</div>
      <div className="error-line jp">{line}</div>
      {/* Trust the server's recoverable decision so retries are offered only when resuming is
          possible. */}
      {data.code === "crashed" && data.recoverable && onRetry ? (
        <div className="card-actions jp">
          <button type="button" className="btn alt sm" onClick={onRetry}>
            Send again
          </button>
        </div>
      ) : null}
      {data.code === "too_many_sessions" && onRaiseLimit ? (
        <div className="card-actions jp">
          <button type="button" className="btn alt sm" onClick={onRaiseLimit}>
            {t("shell.limit.change")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

export interface RateLimitCardProps {
  data: RateLimitData;
}

/** "HH:MM" in the viewer's own local timezone — the same reading a person glancing at their
 * system clock would get, which is the point of naming a reset time at all — and "M/D HH:MM"
 * once the reset is on another local day, because the weekly window resets days away and a clock
 * time alone is then read as tonight. Same rule as the session rail's `timeLabel` (state/rows.ts:
 * same local day → the clock, otherwise a date), used in a reset sentence instead of a chip.
 *
 * The day is compared at render. A refused user may send nothing for hours — the reducer drops
 * `view.rateLimit` only when the next turn starts — so a card drawn at 23:50 can still be on
 * screen after midnight wearing the label it was given then. That only ages into an
 * over-statement, a bare clock time whose reset has already passed, and never back into the
 * failure fixed here, because a reset on another day always carries its date. */
function formatResetTime(epochMs: number, nowMs: number): string {
  const at = new Date(epochMs);
  const now = new Date(nowMs);
  const pad = (n: number) => String(n).padStart(2, "0");
  const clock = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  const sameDay =
    at.getFullYear() === now.getFullYear() &&
    at.getMonth() === now.getMonth() &&
    at.getDate() === now.getDate();
  return sameDay ? clock : `${at.getMonth() + 1}/${at.getDate()} ${clock}`;
}

export function RateLimitCard({ data }: RateLimitCardProps): ReactElement {
  const refused = data.status === "rejected";
  const resets = data.resets_at
    ? `Usage resets at ${formatResetTime(data.resets_at * 1000, Date.now())}.`
    : "";
  const line =
    resets ||
    (refused ? "Wait a while, then send again." : "You can continue, but the limit is near.");
  return (
    <div className={refused ? "error-card" : "error-card notice"}>
      <div className="card-head">
        <span className="chip dim">{refused ? "Error" : "Notice"}</span>
      </div>
      <div className="error-title jp">
        {refused ? "Usage limit reached" : "Approaching the usage limit"}
      </div>
      <div className="error-line jp">{line}</div>
    </div>
  );
}
