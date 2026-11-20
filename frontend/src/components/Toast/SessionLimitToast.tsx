/** Lists sessions that can be closed after a new session is refused. Closing and dismissal are separate actions; the parent removes the toast after an actual close. Transcript titles render as text. */
import { type ReactElement, useEffect } from "react";
import { t } from "../../i18n/t";
import "./SessionLimitToast.css";

export interface LiveSessionSummary {
  sessionId: string;
  title: string;
  /** True while this session is actively streaming (a filled dot); ringed when idle
   * or unset. Optional so a caller that has not computed presence yet still type-checks. */
  writing?: boolean;
}

export interface SessionLimitToastProps {
  live: readonly LiveSessionSummary[];
  maxLiveSessions?: number;
  onClose: (sessionId: string) => void;
  onDismiss: () => void;
  /** Opens Settings › Chat with the live-session-limit field focused. Absent: no raise-limit
   * line or button renders. Never closes a session or dismisses the toast itself. */
  onRaiseLimit?: () => void;
}

export function SessionLimitToast({
  live,
  maxLiveSessions = 3,
  onClose,
  onDismiss,
  onRaiseLimit,
}: SessionLimitToastProps): ReactElement {
  // Escape belongs to the window, not the toast, so it dismisses even when focus never left
  // the composer; the IME guard matches SearchPalette's — a candidate-window Escape is the
  // IME's, not ours. There is no other timer here: see the module doc on why nothing auto-hides.
  useEffect(() => {
    function onWindowKeyDown(event: globalThis.KeyboardEvent): void {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === "Escape") onDismiss();
    }
    window.addEventListener("keydown", onWindowKeyDown);
    return () => window.removeEventListener("keydown", onWindowKeyDown);
  }, [onDismiss]);

  return (
    <div className="toast" role="alert">
      <button type="button" className="toast-dismiss" aria-label="Dismiss" onClick={onDismiss}>
        <svg className="ico" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M3 3l10 10M13 3 3 13" />
        </svg>
      </button>
      <div className="toast-title jp">Up to {maxLiveSessions} sessions can run at once</div>
      <div className="toast-sub jp">Close one to start a new session.</div>
      {onRaiseLimit ? (
        <div className="toast-raise">
          <span className="jp">{t("shell.limit.raise")}</span>
          <button type="button" className="btn alt sm jp toast-raise-button" onClick={onRaiseLimit}>
            {t("shell.limit.change")}
          </button>
        </div>
      ) : null}
      <div className="toast-rows">
        {live.map((session) => (
          <div className="toast-row" key={session.sessionId}>
            <span
              className={`toast-row-dot${session.writing ? " toast-row-dot-writing" : ""}`}
              aria-hidden="true"
            />
            <span className="toast-row-title jp">{session.title || "Untitled session"}</span>
            <button
              type="button"
              className="btn alt sm jp toast-row-close"
              onClick={() => onClose(session.sessionId)}
            >
              Close
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
