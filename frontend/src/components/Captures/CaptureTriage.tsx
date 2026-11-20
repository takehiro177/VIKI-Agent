/**
 * The inbox triage dialog: every capture in 00-inbox/, one destination choice each (plain or
 * VIKI-suggested), and a single handoff draft the user sends to file them all at once. It never
 * touches the vault itself — onFile only hands the user's own /inbox flow a drafted message
 * (triage.ts's captureHandoffText); nothing changes until the user sends that draft.
 */
import { type ReactElement, useEffect, useRef, useState } from "react";
import type {
  Capture,
  CaptureListing,
  CaptureSuggestion,
  CapturesApi,
  SuggestionRun,
} from "../../clients/vaults/capturesApi";
import { useModalFocus } from "../Dialog/useModalFocus";
import { Loading } from "../Loading/Loading";
import { CaptureCard } from "./CaptureCard";
import { captureHandoffText, pruneChoices, type TriageChoice } from "./triage";
import { useCaptureListing } from "./useCaptureListing";
import "./CaptureTriage.css";

/** Reasons that still have something worth opening a chat for — the run at least started, or
 * VIKI replied with nothing the app could use. Infra-level reasons (no slot, no login, the app
 * closing mid-run) have no chat worth opening. */
const OPEN_CHAT_REASONS = new Set(["deadline", "no_suggestions", "cancelled", "error", "crashed"]);

/** The sentence for a failed run's `reason`. The server normalizes any reason outside this set to
 * "error" before it ever reaches here, so the default case is the one sentence that code (and any
 * future one) needs. */
function failureText(reason: string | null): string {
  switch (reason) {
    case "too_many_sessions":
      return "No free session slot. Close a session or raise the limit in Settings › Chat, then try again.";
    case "deadline":
      return "VIKI did not answer within 5 minutes.";
    case "no_suggestions":
      return "VIKI's reply had no suggestions the app could read. Open the chat to see it.";
    case "cli_missing":
    case "not_logged_in":
    case "billing":
      return "VIKI could not start. Check Settings → Diagnostics.";
    case "app_closed":
      return "The app closed before VIKI answered.";
    default:
      return "VIKI could not suggest this time.";
  }
}

/** The sentence for a `suggest()` call that failed to even start a run. */
function startErrorText(code: string): string {
  switch (code) {
    case "no_captures":
      return "There is nothing to suggest for.";
    case "not_active_vault":
      return "This vault is not the one open in this window.";
    default:
      return "Could not ask VIKI.";
  }
}

/** The listing's own status line: empty, or a count plus whichever addenda apply. No trailing
 * period on the count form. */
function listingStatusText(listing: CaptureListing): string {
  const n = listing.captures.length;
  if (n === 0 && !listing.partial && listing.omitted === 0) return "00-inbox/ is empty.";
  const omitted = listing.omitted > 0 ? ` · ${listing.omitted} more not listed` : "";
  const partial = listing.partial ? " · some files were not read" : "";
  const count = n === 0 ? "No notes listed in 00-inbox/" : `${n} notes in 00-inbox/`;
  return `${count}${omitted}${partial}`;
}

/** The done sentence, with the "could not be used" addendum only when some suggestions were
 * rejected. */
function doneText(run: SuggestionRun, totalCaptures: number): string {
  const rejected = run.rejected > 0 ? ` ${run.rejected} could not be used.` : "";
  return `Suggestions from ${run.finished_at}: ${run.suggested} of ${totalCaptures} captures.${rejected}`;
}

/** Captures VIKI suggested a destination for that the user has not already chosen one for —
 * exactly what "Accept all" is allowed to touch. */
function acceptableCaptures(
  captures: readonly Capture[],
  choices: readonly TriageChoice[],
): (Capture & { suggestion: CaptureSuggestion })[] {
  return captures.filter(
    (capture): capture is Capture & { suggestion: CaptureSuggestion } =>
      capture.suggestion !== null && !choices.some((choice) => choice.ref === capture.ref),
  );
}

export interface CaptureTriageProps {
  api: CapturesApi;
  workspaceId: string;
  choices: readonly TriageChoice[];
  onChoices: (choices: readonly TriageChoice[]) => void;
  /** Opens the filing draft; returns whether it opened. */
  onFile: (text: string) => boolean;
  onOpenChat?: (sessionId: string) => void;
  onOpenNote?: (ref: string, origin?: HTMLElement) => void;
  onBrowse?: () => void;
  onClose: () => void;
  pollMs?: number;
}

export function CaptureTriage({
  api,
  workspaceId,
  choices,
  onChoices,
  onFile,
  onOpenChat,
  onOpenNote,
  onBrowse,
  onClose,
  pollMs,
}: CaptureTriageProps): ReactElement {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useModalFocus({ active: true, dialogRef, initialFocusRef: closeRef, onDismiss: onClose });
  const [fileRefused, setFileRefused] = useState(false);

  const { listing, loading, error, starting, startError, reload, suggest } = useCaptureListing(
    api,
    workspaceId,
    pollMs === undefined ? {} : { pollMs },
  );

  // Whenever a fresh listing lands, drop choices whose capture is gone or has moved on to a new
  // revision (pruneChoices keeps the same array when nothing changed). Keyed on `listing` alone:
  // the caller's `choices`/`onChoices` identity is not a reason to re-run this.
  // biome-ignore lint/correctness/useExhaustiveDependencies: see comment above.
  useEffect(() => {
    if (!listing) return;
    const pruned = pruneChoices(choices, listing.captures);
    if (pruned !== choices) onChoices(pruned);
  }, [listing]);

  function setChoice(ref: string, next: TriageChoice | null): void {
    const rest = choices.filter((choice) => choice.ref !== ref);
    onChoices(next ? [...rest, next] : rest);
  }

  function acceptAll(suggestable: readonly (Capture & { suggestion: CaptureSuggestion })[]): void {
    const additions: TriageChoice[] = suggestable.map((capture) => ({
      ref: capture.ref,
      revision: capture.revision,
      modified: capture.modified,
      destination: { to: capture.suggestion.to, project: capture.suggestion.project },
      via: "suggestion",
    }));
    onChoices([...choices, ...additions]);
  }

  function handleFile(chosen: readonly TriageChoice[]): void {
    if (onFile(captureHandoffText(chosen))) {
      onChoices([]);
      onClose();
    } else {
      setFileRefused(true);
    }
  }

  const run = listing?.run ?? null;
  const suggestable = listing ? acceptableCaptures(listing.captures, choices) : [];
  const chosenInOrder = listing
    ? listing.captures
        .map((capture) => choices.find((choice) => choice.ref === capture.ref))
        .filter((choice): choice is TriageChoice => choice !== undefined)
    : [];
  const noCaptures = !listing || listing.captures.length === 0;

  return (
    <div className="capture-triage-backdrop">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Inbox triage"
        className="capture-triage"
      >
        <div className="capture-triage-head">
          {onBrowse ? (
            <button type="button" className="btn alt sm" onClick={onBrowse}>
              Browse in Vault
            </button>
          ) : null}
          <button type="button" ref={closeRef} className="btn txt sm" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="capture-triage-details">
          <div className="capture-triage-status">
            {error ? (
              <div className="capture-triage-line">
                <p role="alert">Could not read 00-inbox/.</p>
                <button type="button" className="btn alt sm" onClick={reload}>
                  Try again
                </button>
              </div>
            ) : loading || !listing ? (
              <Loading>Reading 00-inbox/…</Loading>
            ) : (
              <div className="capture-triage-line">
                <p role="status">{listingStatusText(listing)}</p>
                {listing.partial || listing.omitted > 0 ? (
                  <button type="button" className="btn alt sm" onClick={reload}>
                    Try again
                  </button>
                ) : null}
              </div>
            )}
            <p className="capture-triage-note">
              Nothing changes in your vault until you send VIKI the filing draft.
            </p>
          </div>
          {listing ? (
            <div className="capture-triage-suggest">
              <button
                type="button"
                className="btn alt sm"
                disabled={starting || run?.state === "running" || noCaptures}
                onClick={suggest}
              >
                Ask VIKI to suggest
              </button>
              <p className="capture-triage-hint">
                Runs one VIKI turn in a background chat. The app denies any permission it asks for.
              </p>
              <p className="capture-triage-hint">Suggestions are kept until VIKI Agent closes.</p>
              {startError ? <p role="alert">{startErrorText(startError)}</p> : null}
              {run && run.state === "running" ? (
                <div className="capture-triage-line">
                  <Loading>VIKI is suggesting…</Loading>
                  {onOpenChat ? (
                    <button
                      type="button"
                      className="btn alt sm"
                      onClick={() => onOpenChat(run.session_id)}
                    >
                      Open chat
                    </button>
                  ) : null}
                </div>
              ) : null}
              {run && run.state === "done" ? (
                <div className="capture-triage-line">
                  <p role="status">{doneText(run, listing.captures.length)}</p>
                  <button
                    type="button"
                    className="btn alt sm"
                    disabled={suggestable.length === 0}
                    onClick={() => acceptAll(suggestable)}
                  >
                    {`Accept all ${suggestable.length} suggestions`}
                  </button>
                </div>
              ) : null}
              {run && run.state === "failed" ? (
                <div className="capture-triage-line">
                  <p role="alert">{failureText(run.reason)}</p>
                  {onOpenChat && OPEN_CHAT_REASONS.has(run.reason ?? "") ? (
                    <button
                      type="button"
                      className="btn alt sm"
                      onClick={() => onOpenChat(run.session_id)}
                    >
                      Open chat
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}
          {listing ? (
            <ul className="capture-triage-cards">
              {listing.captures.map((capture) => (
                <CaptureCard
                  key={capture.ref}
                  capture={capture}
                  choice={
                    choices.find(
                      (choice) =>
                        choice.ref === capture.ref && choice.revision === capture.revision,
                    ) ?? null
                  }
                  projects={listing.projects}
                  onChoose={(choice) => setChoice(capture.ref, choice)}
                  {...(onOpenNote
                    ? { onOpenNote: () => onOpenNote(capture.ref, dialogRef.current ?? undefined) }
                    : {})}
                />
              ))}
            </ul>
          ) : null}
        </div>
        {listing ? (
          <div className="capture-triage-foot">
            <div className="capture-triage-line">
              <p>{`${chosenInOrder.length} chosen`}</p>
              <button
                type="button"
                className="btn alt sm"
                disabled={chosenInOrder.length === 0}
                onClick={() => handleFile(chosenInOrder)}
              >
                {`File ${chosenInOrder.length} with VIKI`}
              </button>
            </div>
            {fileRefused ? <p role="alert">The draft did not open. Nothing was sent.</p> : null}
            <p className="capture-triage-note">
              Your choices stay until you file them or reload this window.
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
