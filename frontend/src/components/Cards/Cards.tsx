/** Permission and question cards wait for an authoritative request event or explicit rejection.
 * Unknown delivery remains visible. Approval views show the durable choice and hand-off state;
 * provider text is rendered as text or sanitized Markdown, with any character that hides or
 * reorders text written out as its code (DecisionText.tsx) and a line saying so. */
import {
  type KeyboardEvent,
  type MouseEvent,
  type ReactElement,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { ApprovalChoice, ApprovalReceipt } from "../../clients/conversations/durableApi";
import type { PermissionRequestedData, QuestionAskedData } from "../../protocol";
import type { ApprovalView } from "../../state/approvals";
import { Markdown } from "../Conversation/Markdown";
import { AgentFace } from "../Face/AgentFace";
import { DecisionNotes, DecisionText, useOverflowing } from "./DecisionText";
import { splitPlanDetails } from "./planDetails";
import { visibleText } from "./visibleText";
import "./Cards.css";

/** The one thing this app can say about an answer it did not hear back about. Exported: App.tsx's
 * decideNative hands the same native request through its own durable route and copies this card's
 * words for the same failures, rather than drifting its own (merge note). */
export const UNCERTAIN_RESULT =
  "The response delivery result is unknown. Its status will be checked after reconnecting.";
export const SAVE_FAILED =
  "The choice could not be saved, so it was not sent to Claude Code. Check the storage location, then choose again.";
export const REQUEST_OVER = "This request has already ended.";
// The reclaim only frees a previous launch's terminal rows, so a scope this launch
// filled stays full until it exits; unlike SAVE_FAILED this is not a transient write failure, so
// the copy does not invite a retry that can only fail again.
export const CAPACITY_FAILED =
  "The choice record storage limit was reached, so the choice was not delivered. Stop, reopen VIKI Agent, then reset Choice records in Data.";

/** What the ledger's own state proves, in the user's words. `delivered_locally` means the
 * request's future resolved here — never that the tool ran. */
function deliveryLine(receipt: ApprovalReceipt | null): string {
  if (!receipt) return "";
  switch (receipt.state) {
    case "recorded":
    case "delivering":
      return "Sending to Claude Code.";
    case "delivered_locally":
      return "Sent to Claude Code. Check the conversation to see whether it ran.";
    case "unknown":
      return "Delivery is unconfirmed. Check the conversation record.";
    default:
      if (receipt.reason === "stale_request" || receipt.reason === "runtime_changed")
        return "The request had already ended, so the choice was not delivered.";
      if (receipt.reason === "storage")
        return "A storage problem prevented delivery. Stop before continuing.";
      return "Shutdown prevented delivery.";
  }
}

/** The note under the actions: a definite refusal first, then an unheard result. "" renders none.
 * A recorded choice answers the question the uncertainty line asks, so that line goes with it —
 * otherwise a recovered ambiguous answer would contradict the record printed right above it. */
function noteFor(approval: ApprovalView | undefined, answerState: string): string {
  if (approval?.phase === "recorded") return "";
  if (approval?.refusal === "capacity") return CAPACITY_FAILED;
  if (approval?.refusal === "save") return SAVE_FAILED;
  if (approval?.refusal === "over") return REQUEST_OVER;
  if (answerState === "unknown" || approval?.phase === "ambiguous") return UNCERTAIN_RESULT;
  return "";
}

/** An unheard POST is recoverable by another click — the store inspects the retained ID with a
 * GET and never posts again — so a card that has a ledger view goes back to answerable instead of
 * latching shut. Without the prop (a card with no ledger view) an unknown result still latches,
 * which is all such a card can honestly do. */
function recoverable(approval: ApprovalView | undefined, outcome: AnswerOutcome): boolean {
  return outcome === "unknown" && approval !== undefined;
}

/** The choice, as recorded. A card in this state offers no buttons, so the opposite answer is
 * not reachable from here either. */
function recordedLines(choice: ApprovalChoice | null, isPlan: boolean): string[] {
  if (!choice) return [];
  if (choice.kind === "permission") {
    const label = isPlan
      ? choice.allow
        ? "Approve"
        : "Keep planning"
      : choice.allow
        ? "Allow once"
        : "Deny";
    return [`Recorded choice: ${label}`];
  }
  // Other is never an accepted answer: the CLI receives it as the reason for declining.
  if (choice.response !== "")
    return [
      "Recorded answer: Other response",
      "Other text is not a structured choice; it is sent to Claude Code as the reason for declining.",
    ];
  const labels = Object.values(choice.answers);
  return labels.length > 0 ? [`Recorded answer: ${labels.join(" / ")}`] : [];
}

function RecordRegion({
  approval,
  isPlan = false,
  id,
}: {
  approval: ApprovalView;
  isPlan?: boolean;
  id?: string;
}): ReactElement {
  const lines = [...recordedLines(approval.choice, isPlan), deliveryLine(approval.receipt)];
  return (
    <div id={id} className="card-record jp" role="status">
      {lines
        .filter((line) => line !== "")
        .map((line) => (
          <div key={line}>{line}</div>
        ))}
    </div>
  );
}

/** Only reached when `question` is empty: an older frame, or a plan card. The mapper has its own
 * generic line for a tool it does not know, so this is a second line of defence, not the usual
 * path. */
const TOOL_QUESTION = "May I perform this operation?";

/** Enter can also close an IME candidate window; do not submit while composition is active. */
function isComposingEvent(event: KeyboardEvent<HTMLInputElement>): boolean {
  return event.nativeEvent.isComposing || event.keyCode === 229;
}

/** For a permission card's action row. Cards stack: when the one above is answered and leaves,
 * the next card's Allow once comes to rest where the pointer already is, and the second click of
 * a double-click would answer a request the user has not read. `detail` counts the clicks of a
 * multi-click (0 for a keyboard activation), so a click past the first continues one that began
 * earlier, possibly on the card that was here before: its press and its click are dropped.
 * ConfirmDialog.tsx drops the same clicks for the same reason; the few lines are written out
 * again here, as `isComposingEvent` above is, so the card tree stays independent of the dialogs. */
function dropMultiClick(event: MouseEvent): void {
  if (event.detail <= 1) return;
  event.preventDefault();
  event.stopPropagation();
}

function useCardTarget(
  cardRef: { current: HTMLDivElement | null },
  target: boolean,
  toolUseId: string,
  targetRequest: number | undefined,
): void {
  // Request identity and Open serial trigger refocus, though the effect reads only the DOM node.
  // biome-ignore lint/correctness/useExhaustiveDependencies: Both values are required effect triggers.
  useLayoutEffect(() => {
    const card = cardRef.current;
    if (!target || !card) return;
    const reduceMotion =
      document.documentElement.dataset.motion === "reduce" ||
      (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches);
    card.scrollIntoView?.({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
    card.focus({ preventScroll: true });
  }, [cardRef, target, toolUseId, targetRequest]);
}

export interface PermissionCardProps {
  data: PermissionRequestedData;
  onRespond: (toolUseId: string, allow: boolean) => Promise<AnswerOutcome>;
  onOpenUrl: (url: string) => void;
  disabled?: boolean;
  /** Changes only after an authoritative synchronization of the still-open request. */
  recoveryKey?: string;
  /** What the durable ledger holds for this request. Absent: the card behaves as it always has. */
  approval?: ApprovalView;
  target?: boolean;
  targetRequest?: number;
}

export type AnswerOutcome = "applied" | "rejected" | "unknown";

export function PermissionCard({
  data,
  onRespond,
  onOpenUrl,
  disabled = false,
  recoveryKey,
  approval,
  target = false,
  targetRequest,
}: PermissionCardProps): ReactElement {
  const cardId = useId();
  const cardRef = useRef<HTMLDivElement>(null);
  useCardTarget(cardRef, target, data.tool_use_id, targetRequest);
  const [answerState, setAnswerState] = useState<"idle" | "pending" | "unknown">("idle");
  const answerLatch = useRef(false);
  const answerEpoch = useRef(0);
  useEffect(() => {
    if (!recoveryKey) return;
    answerEpoch.current += 1;
    answerLatch.current = false;
    setAnswerState("idle");
  }, [recoveryKey]);
  const recorded = approval?.phase === "recorded";
  // A recorded choice, a request that is already over, and a full quota all take the buttons out
  // of play — the quota cannot be worked around by choosing again either. A save failure and an
  // unheard result leave them, because the next click is the only way forward — for the save
  // failure a fresh attempt, for the unheard one an inspection of the retained ID.
  const answered =
    disabled ||
    answerState !== "idle" ||
    approval?.phase === "posting" ||
    approval?.refusal === "over" ||
    approval?.refusal === "capacity";
  const isPlan = data.kind === "plan";
  const note = noteFor(approval, answerState);
  // The box the request is read in, a command or a plan: both scroll past a set height.
  const boxRef = useRef<HTMLDivElement>(null);
  const overflowing = useOverflowing(boxRef);
  const plan = visibleText(data.plan);
  const planParts = splitPlanDetails(plan.text);
  const hidden =
    plan.hidden ||
    [data.tool, data.summary, data.reason, data.agent].some((text) => visibleText(text).hidden);
  const described = [
    isPlan ? `${cardId}-outcome` : `${cardId}-target`,
    !isPlan && data.reason ? `${cardId}-reason` : "",
    hidden || overflowing ? `${cardId}-notes` : "",
  ];

  async function respond(allow: boolean): Promise<void> {
    if (answered || answerLatch.current) return;
    answerLatch.current = true;
    const epoch = answerEpoch.current;
    setAnswerState("pending");
    let outcome: AnswerOutcome;
    try {
      outcome = await onRespond(data.tool_use_id, allow);
    } catch {
      outcome = "unknown";
    }
    if (answerEpoch.current !== epoch) return;
    const answerable = outcome === "rejected" || recoverable(approval, outcome);
    if (answerable) answerLatch.current = false;
    setAnswerState(answerable ? "idle" : outcome === "unknown" ? "unknown" : "pending");
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: This group retains the request and receipt after its controls are removed.
    <div
      ref={cardRef}
      className={`card${target ? " card-target" : ""}`}
      data-tool-use-id={target ? data.tool_use_id : undefined}
      tabIndex={target ? -1 : undefined}
      role="group"
      aria-labelledby={`${cardId}-question`}
      aria-describedby={described.filter(Boolean).join(" ")}
    >
      <div className="card-head">
        <span className="chip">{isPlan ? "Plan" : "Waiting for approval"}</span>
        {data.agent ? (
          <span className="card-agent">
            <AgentFace name={data.agent} size={16} />
            {"From "}
            <DecisionText text={data.agent} />
          </span>
        ) : null}
      </div>
      <div id={`${cardId}-question`} className="card-question jp">
        {isPlan ? "Proceed with this plan?" : data.question || TOOL_QUESTION}
      </div>
      {isPlan ? (
        <div className="card-plan" ref={boxRef}>
          <Markdown text={planParts.top} onOpenUrl={onOpenUrl} />
          {planParts.details !== null ? (
            <details className="card-plan-details">
              <summary>Details</summary>
              <Markdown text={planParts.details} onOpenUrl={onOpenUrl} />
            </details>
          ) : null}
        </div>
      ) : (
        <>
          <div id={`${cardId}-target`} className="card-tool mono" ref={boxRef}>
            <span className="card-tool-name">
              <DecisionText text={data.tool} />
            </span>{" "}
            <DecisionText text={data.summary} />
          </div>
          {data.reason ? (
            <div id={`${cardId}-reason`} className="card-reason">
              <DecisionText text={data.reason} />
            </div>
          ) : null}
        </>
      )}
      <DecisionNotes id={`${cardId}-notes`} hidden={hidden} overflowing={overflowing} />
      {recorded && approval ? (
        <RecordRegion id={`${cardId}-outcome`} approval={approval} isPlan={isPlan} />
      ) : (
        <div
          className="card-actions jp"
          onMouseDownCapture={dropMultiClick}
          onClickCapture={dropMultiClick}
        >
          <button
            type="button"
            className="btn go"
            disabled={answered}
            onClick={() => void respond(true)}
          >
            {isPlan ? "Approve" : "Allow once"}
          </button>
          <button
            type="button"
            className="btn alt"
            disabled={answered}
            onClick={() => void respond(false)}
          >
            {isPlan ? "Keep planning" : "Deny"}
          </button>
          <span id={`${cardId}-outcome`} className="card-note">
            {isPlan
              ? "Approving exits plan mode and continues."
              : "Permission applies to this request only"}
          </span>
        </div>
      )}
      {note ? (
        <div className="card-note jp" role="status">
          {note}
        </div>
      ) : null}
    </div>
  );
}

export interface QuestionCardProps {
  data: QuestionAskedData;
  onAnswer: (
    toolUseId: string,
    answers: Record<string, string>,
    response: string,
  ) => Promise<AnswerOutcome>;
  disabled?: boolean;
  recoveryKey?: string;
  /** What the durable ledger holds for this request. Absent: the card behaves as it always has. */
  approval?: ApprovalView;
  target?: boolean;
  targetRequest?: number;
}

/** A controlled question: the card owns every pick and hands this group only its own selection
 * and a callback to change it. */
function QuestionGroup({
  question,
  questionId,
  chip,
  disabled,
  recorded = false,
  selection,
  onSelect,
}: {
  question: QuestionAskedData["questions"][number];
  questionId: string;
  /** Shown only for the second and later questions; the first one's chip is the card's own. */
  chip: boolean;
  disabled: boolean;
  /** The ledger holds a choice for this request: the question stays, its options go. */
  recorded?: boolean;
  selection: ReadonlySet<number>;
  onSelect: (index: number) => void;
}): ReactElement {
  return (
    // biome-ignore lint/a11y/useSemanticElements: The question remains a named group after recorded answers remove its controls.
    <div className="card-group" role="group" aria-labelledby={questionId}>
      {chip ? (
        <div className="card-head">
          <span className="chip">
            <DecisionText text={question.header} />
          </span>
        </div>
      ) : null}
      <div id={questionId} className="card-question jp">
        <DecisionText text={question.question} />
      </div>
      {recorded ? null : (
        <div className="card-options">
          {question.options.map((option, index) => (
            // Keyed by position, not by label: two options can share a label (Cards.tsx docstring
            // above), which would otherwise collide; the options array is fixed for the life of a
            // card, so index is a stable identity here.
            <button
              // biome-ignore lint/suspicious/noArrayIndexKey: see above
              key={index}
              type="button"
              className={`card-option${selection.has(index) ? " card-option--selected" : ""}`}
              disabled={disabled}
              aria-pressed={selection.has(index)}
              onClick={() => onSelect(index)}
            >
              <span className="card-option-label jp">
                <DecisionText text={option.label} />
                {selection.has(index) ? (
                  <span className="card-option-check" aria-hidden="true">
                    ✓
                  </span>
                ) : null}
              </span>
              {option.description ? (
                <span className="card-option-desc jp">
                  <DecisionText text={option.description} />
                </span>
              ) : null}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const NO_SELECTION: ReadonlySet<number> = new Set();

export function QuestionCard({
  data,
  onAnswer,
  disabled = false,
  recoveryKey,
  approval,
  target = false,
  targetRequest,
}: QuestionCardProps): ReactElement {
  const cardId = useId();
  const cardRef = useRef<HTMLDivElement>(null);
  useCardTarget(cardRef, target, data.tool_use_id, targetRequest);
  const questions = data.questions;
  const first = questions[0];
  const [answerState, setAnswerState] = useState<"idle" | "pending" | "unknown">("idle");
  const answerLatch = useRef(false);
  const answerEpoch = useRef(0);
  const [picked, setPicked] = useState<ReadonlyMap<string, ReadonlySet<number>>>(new Map());
  const [showOther, setShowOther] = useState(false);
  const [otherText, setOtherText] = useState("");
  const otherTrigger = useRef<HTMLButtonElement>(null);
  const otherInput = useRef<HTMLInputElement>(null);
  const restoreOtherFocus = useRef(false);
  useEffect(() => {
    if (showOther) otherInput.current?.focus();
    else if (restoreOtherFocus.current) {
      restoreOtherFocus.current = false;
      otherTrigger.current?.focus({ preventScroll: true });
    }
  }, [showOther]);
  useEffect(() => {
    if (!recoveryKey) return;
    answerEpoch.current += 1;
    answerLatch.current = false;
    setAnswerState("idle");
    setPicked(new Map());
  }, [recoveryKey]);
  const recorded = approval?.phase === "recorded";
  // Same rule as the permission card: a save failure and an unheard result leave the options
  // answerable, and the picks are cleared with them so there is a row left to click; a full quota
  // does not.
  const answered =
    disabled ||
    answerState !== "idle" ||
    approval?.phase === "posting" ||
    approval?.refusal === "over" ||
    approval?.refusal === "capacity";
  const note = noteFor(approval, answerState);
  const allPicked = questions.every((question) => (picked.get(question.question)?.size ?? 0) > 0);
  // Display only: the answer still carries each question and label exactly as it was asked.
  const hidden = questions.some((question) =>
    [
      question.header,
      question.question,
      ...question.options.flatMap((option) => [option.label, option.description]),
    ].some((text) => visibleText(text).hidden),
  );
  const described = questions.map((_, index) => `${cardId}-question-${index}`);
  if (hidden) described.push(`${cardId}-notes`);

  if (!first)
    return (
      <div
        ref={cardRef}
        className={`card${target ? " card-target" : ""}`}
        data-tool-use-id={target ? data.tool_use_id : undefined}
        tabIndex={target ? -1 : undefined}
      />
    );

  async function answerOnce(answers: Record<string, string>, response: string): Promise<void> {
    if (answered || answerLatch.current) return;
    answerLatch.current = true;
    if (response) {
      setPicked(new Map());
    }
    const epoch = answerEpoch.current;
    setAnswerState("pending");
    let outcome: AnswerOutcome;
    try {
      outcome = await onAnswer(data.tool_use_id, answers, response);
    } catch {
      outcome = "unknown";
    }
    if (answerEpoch.current !== epoch) return;
    const answerable = outcome === "rejected" || recoverable(approval, outcome);
    if (answerable) {
      answerLatch.current = false;
      setPicked(new Map());
    }
    setAnswerState(answerable ? "idle" : outcome === "unknown" ? "unknown" : "pending");
  }

  /** A click only ever changes this question's own selection — single-select replaces it,
   * multi-select toggles one option and an emptied set drops the question's entry — and never
   * sends, so the user can keep changing their mind on any question until they choose Submit. */
  function selectOption(questionText: string, multi: boolean, index: number): void {
    if (answered || answerLatch.current) return;
    setPicked((current) => {
      const next = new Map(current);
      if (!multi) {
        next.set(questionText, new Set([index]));
        return next;
      }
      const updated = new Set(next.get(questionText));
      if (updated.has(index)) updated.delete(index);
      else updated.add(index);
      if (updated.size === 0) next.delete(questionText);
      else next.set(questionText, updated);
      return next;
    });
  }

  /** Sent only once every question has a pick: the wire carries one response for the whole ask,
   * and a partial map would tell the model the user settled things they were never shown. */
  function submit(): void {
    if (answered || answerLatch.current || !allPicked) return;
    const answers = Object.fromEntries(
      questions.map((question) => {
        const selection = picked.get(question.question) ?? NO_SELECTION;
        const labels = question.options
          .filter((_, index) => selection.has(index))
          .map((option) => option.label)
          .join(", ");
        return [question.question, labels];
      }),
    );
    void answerOnce(answers, "");
  }

  function sendOther(): void {
    const text = otherText.trim();
    if (!text) return;
    void answerOnce({}, text);
  }

  return (
    // biome-ignore lint/a11y/useSemanticElements: This group retains the questions and receipt after its controls are removed.
    <div
      ref={cardRef}
      className={`card${target ? " card-target" : ""}`}
      data-tool-use-id={target ? data.tool_use_id : undefined}
      tabIndex={target ? -1 : undefined}
      role="group"
      aria-labelledby={`${cardId}-header`}
      aria-describedby={described.join(" ")}
    >
      <div className="card-head">
        <span id={`${cardId}-header`} className="chip">
          <DecisionText text={first.header} />
        </span>
      </div>
      {!recorded && (
        <p className="card-progress" aria-live="polite">
          Questions selected: {picked.size} of {questions.length}.
        </p>
      )}
      {questions.map((question, index) => (
        <QuestionGroup
          // Two questions in one ask can repeat a header but not a text (the CLI keys its own
          // result by the text), so the text is the stable identity here.
          key={question.question}
          question={question}
          questionId={`${cardId}-question-${index}`}
          chip={index > 0}
          disabled={answered}
          recorded={recorded}
          selection={picked.get(question.question) ?? NO_SELECTION}
          onSelect={(optionIndex) =>
            selectOption(question.question, question.multi_select, optionIndex)
          }
        />
      ))}
      <DecisionNotes id={`${cardId}-notes`} hidden={hidden} overflowing={false} />
      {recorded && approval ? <RecordRegion approval={approval} /> : null}
      {recorded ? null : showOther ? (
        <div className="card-other">
          <label>
            Other response (decline reason)
            <input
              ref={otherInput}
              type="text"
              className="jp"
              value={otherText}
              disabled={answered}
              placeholder="Enter a response…"
              onChange={(event) => setOtherText(event.target.value)}
              onKeyDown={(event) => {
                if (isComposingEvent(event)) return; // the IME's own Enter closes its candidate window
                if (event.key === "Enter") sendOther();
              }}
            />
          </label>
          <div className="card-actions">
            <button
              type="button"
              className="btn go"
              disabled={answered || !otherText.trim()}
              onClick={sendOther}
            >
              Submit
            </button>
            <button
              type="button"
              className="btn alt"
              disabled={answered}
              onClick={() => {
                if (answered || answerLatch.current) return;
                restoreOtherFocus.current = true;
                setShowOther(false);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="card-actions">
          <button
            type="button"
            className="btn go"
            disabled={answered || !allPicked}
            onClick={submit}
          >
            Submit
          </button>
          <button
            type="button"
            className="card-other-toggle jp"
            ref={otherTrigger}
            disabled={answered}
            onClick={() => setShowOther(true)}
          >
            Other
          </button>
        </div>
      )}
      {note ? (
        <div className="card-note jp" role="status">
          {note}
        </div>
      ) : null}
    </div>
  );
}
