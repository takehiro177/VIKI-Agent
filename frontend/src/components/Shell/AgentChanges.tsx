/**
 * The Apply card: pending settings or routine changes a chat's agent CLI asked the window to
 * make, awaiting Apply or Reject. Lists on mount and polls every `pollMs` while the document is
 * visible; a `not_found` from the surface (it is gone) stops polling for good. Renders nothing
 * while there is nothing pending -- including right after a poll failure, since there is then
 * nothing of substance to show.
 */
import { Fragment, type ReactElement, useCallback, useEffect, useRef, useState } from "react";
import {
  type AgentChange,
  type AgentChangesApi,
  AgentChangesApiError,
  type RoutineEnableSummary,
  type ScheduleSummary,
} from "../../clients/app/agentChangesApi";
import { t, tf } from "../../i18n/t";
import "./Banners.css";

export interface AgentChangesProps {
  api: AgentChangesApi;
  /** The asking chat's display title; "" when the chat is not, or no longer, known. */
  titleOf: (sessionId: string) => string;
  /** Called when a poll's `seq` moves past the previous read, and right after a successful Apply. */
  onChanged: () => void;
  pollMs?: number;
}

const WEEKDAY_NAMES = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

/** The weekday names a `kind: "weekly"` schedule's `days` names (ISO, 1 = Monday), collapsed to
 * "Weekdays" for the common Monday-Friday set. */
function dayList(days: readonly number[]): string {
  const sorted = [...new Set(days)]
    .filter((day) => Number.isInteger(day) && day >= 1 && day <= 7)
    .sort((a, b) => a - b);
  if (sorted.length === 5 && sorted.every((day, i) => day === i + 1)) return "Weekdays";
  return sorted.map((day) => WEEKDAY_NAMES[day - 1]).join(", ");
}

/** "Weekdays at 08:00, Asia/Tokyo" / "Daily at 07:30, UTC" / "Manual only". */
function scheduleText(schedule: ScheduleSummary): string {
  if (schedule.kind === "manual") return "Manual only";
  const at = schedule.times.length > 0 ? ` at ${schedule.times.join(", ")}` : "";
  const timezone = schedule.timezone ? `, ${schedule.timezone}` : "";
  const label = schedule.kind === "daily" ? "Daily" : dayList(schedule.days) || "Weekly";
  return `${label}${at}${timezone}`;
}

/** The JSON-rendered value: a leading "…" when `offset` says the shown text starts after the
 * value's own beginning, then a catalog-worded "(shortened)" note when the server also cut its
 * end. Either, both or neither -- a summary without them renders exactly as before. */
function valueText(
  value: unknown,
  truncated: boolean | undefined,
  offset: number | undefined,
): string {
  const text = `${offset !== undefined && offset > 0 ? "…" : ""}${JSON.stringify(value)}`;
  return truncated === true ? tf("shell.agent_changes.shortened", { value: text }) : text;
}

/** The one sentence for a change, worded by its kind and, for a setting, whether it resets. */
function changeText(change: AgentChange, titleOf: (sessionId: string) => string): string {
  const title = titleOf(change.session_id) || "Untitled chat";
  if (change.kind === "routine_enable")
    return tf("shell.agent_changes.routine", { title, name: change.summary.name });
  if (change.summary.reset)
    return tf("shell.agent_changes.reset", { title, key: change.summary.key });
  return tf("shell.agent_changes.change", {
    title,
    key: change.summary.key,
    from: valueText(change.summary.from, change.summary.from_truncated, change.summary.from_offset),
    to: valueText(change.summary.to, change.summary.to_truncated, change.summary.to_offset),
  });
}

/** R8: the schedule, profile, workspace and (bounded, scrollable) prompt below a routine_enable
 * card's sentence, each shown only when the server's summary carries it -- an older server, or a
 * malformed field (`agentChangesApi.ts`'s `parseChange` already dropped it), falls back to no
 * extra line rather than throwing. Null when none of the four parsed. */
function routineDetail(summary: RoutineEnableSummary): ReactElement | null {
  const when = summary.schedule ? scheduleText(summary.schedule) : null;
  const { profile, workspace, prompt, prompt_truncated: truncated } = summary;
  if (when === null && !profile && !workspace && prompt === undefined) return null;
  return (
    <div className="agent-changes-detail">
      {when !== null ? <p>{tf("shell.agent_changes.schedule", { when })}</p> : null}
      {profile ? <p>{tf("shell.agent_changes.profile", { name: profile.name })}</p> : null}
      {workspace ? <p>{tf("shell.agent_changes.workspace", { name: workspace.name })}</p> : null}
      {prompt !== undefined ? <pre>{prompt}</pre> : null}
      {prompt !== undefined && truncated === true ? (
        <p>{t("shell.agent_changes.prompt_truncated")}</p>
      ) : null}
    </div>
  );
}

export function AgentChanges({
  api,
  titleOf,
  onChanged,
  pollMs = 2000,
}: AgentChangesProps): ReactElement | null {
  const [changes, setChanges] = useState<readonly AgentChange[]>([]);
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const [visible, setVisible] = useState(() => !document.hidden);
  // The seq from the previous successful read; only a change from THAT (not the absence of one,
  // the first read) is worth telling the caller about.
  const lastSeq = useRef<number | null>(null);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  // One AbortController for this component's whole lifetime, shared by the mount read and every
  // poll tick below, so a request still in flight when the component unmounts resolves into
  // nothing rather than setting state after that.
  const abortRef = useRef<AbortController | null>(null);

  const list = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const result = await api.list(signal);
        if (signal?.aborted) return;
        setChanges(result.changes);
        setError(null);
        if (lastSeq.current !== null && lastSeq.current !== result.seq) onChangedRef.current();
        lastSeq.current = result.seq;
      } catch (err) {
        if (signal?.aborted) return;
        if (err instanceof AgentChangesApiError && err.code === "not_found") {
          setStopped(true);
          setChanges([]);
          return;
        }
        setError(err instanceof Error ? err.message : "unavailable");
      }
    },
    [api],
  );

  // Mount: one read regardless of visibility, on the controller every later poll tick reuses.
  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    void list(controller.signal);
    return () => {
      controller.abort();
      abortRef.current = null;
    };
  }, [list]);

  useEffect(() => {
    const onVisibility = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // The poll: only while visible, and never again once a not_found has stopped it for good.
  useEffect(() => {
    if (!visible || stopped) return;
    const id = setInterval(() => void list(abortRef.current?.signal), pollMs);
    return () => clearInterval(id);
  }, [visible, stopped, pollMs, list]);

  async function decide(change: AgentChange, decision: "apply" | "reject"): Promise<void> {
    setPending((current) => new Set(current).add(change.change_id));
    try {
      await api.decide(change.change_id, decision);
      setChanges((current) => current.filter((item) => item.change_id !== change.change_id));
      setError(null);
      if (decision === "apply") onChangedRef.current();
    } catch (err) {
      setError(err instanceof Error ? err.message : "unavailable");
    } finally {
      setPending((current) => {
        const next = new Set(current);
        next.delete(change.change_id);
        return next;
      });
    }
  }

  if (changes.length === 0) return null;

  return (
    <section aria-label={t("shell.agent_changes.region")} aria-live="polite">
      {changes.map((change) => {
        const busy = pending.has(change.change_id);
        return (
          <Fragment key={change.change_id}>
            <div className="readiness-banner">
              <span>{changeText(change, titleOf)}</span>
              <div className="chat-only-banner-actions">
                <button
                  type="button"
                  className="btn go sm"
                  disabled={busy}
                  onClick={() => void decide(change, "apply")}
                >
                  {t("common.button.apply")}
                </button>
                <button
                  type="button"
                  className="btn alt sm"
                  disabled={busy}
                  onClick={() => void decide(change, "reject")}
                >
                  {t("common.button.reject")}
                </button>
              </div>
            </div>
            {change.kind === "routine_enable" ? routineDetail(change.summary) : null}
          </Fragment>
        );
      })}
      {error !== null ? <p role="alert">{error}</p> : null}
    </section>
  );
}
