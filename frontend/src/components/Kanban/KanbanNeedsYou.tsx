/**
 * The "Needs you" section atop the Kanban page (SN §F): blocked cards waiting on a decision and
 * review cards waiting on an approval, oldest change first. A dumb view only — the caller reads
 * each card's body and owner tags and hands them in as `items`; this component renders them and
 * reports the owner's choice back through `onAnswer`/`onUndo`/`onOpenNote`, or, in message mode,
 * through `message.onSend`/`message.onCancel`. It never saves or sends anything itself.
 */
import { type ReactNode, useEffect, useId, useState } from "react";
import { t, tf } from "../../i18n/t";
import { botName } from "../Bots/team";
import { Icon, type IconName } from "../Icon/Icon";
import "./Kanban.css";
import type { CardAnswer, CardOption, CardRead } from "./kanbanBody";
import type { KanbanCard, KanbanNotice } from "./kanbanCards";
import { CARD_TEXT_CAP } from "./kanbanMessage";

export interface NeedsYouItem {
  card: KanbanCard;
  read: CardRead | null;
  /** The card's owner tag(s), already rendered (BotTag or plain text), exactly as lanes show them. */
  ownerTags: ReactNode;
  /** The Bot the owner's answer will wake, for the reply/review hint; null when none resolves. */
  botName: string | null;
}

/** Message mode's per-item state (present only when `KanbanView` has no `write`): the one open
 * draft, if its ref and read sha match this item, and the refs already sent this visit, keyed by
 * the read sha256 and the Bot name they were sent to. `botName` on each is the target's name
 * fixed at compose/send time, never the item's own current one, which a re-read or a re-seat can
 * move to a different Bot underneath an open draft. */
export interface CardMessageView {
  draft: { ref: string; sha256: string; botName: string; text: string } | null;
  sent: ReadonlyMap<string, { sha256: string; botName: string }>;
  onSend(): void;
  onCancel(): void;
}

export interface KanbanNeedsYouProps {
  items: NeedsYouItem[];
  counts: { blocked: number; review: number } | null;
  /** Card bodies are still being read: an item with no `read` is not yet known to be unreadable. */
  reading: boolean;
  canWrite: boolean;
  saving: boolean;
  /** The one card ref mid its 10 s Undo window; that item's own controls stay disabled. */
  lockedRef: string | null;
  notice: KanbanNotice | null;
  /** Present only off the write path: the open draft to preview and this visit's sent refs.
   * Message mode is `!canWrite && message !== undefined`. */
  message?: CardMessageView;
  onAnswer(item: NeedsYouItem, answer: CardAnswer): void;
  onUndo(): void;
  onOpenNote(ref: string): void;
}

/** Copied from KanbanView (not imported: KanbanView.tsx stays frozen until package P7). */
export function updatedText(ageDays: number): string {
  if (ageDays === 0) return t("kanban.card.updated_today");
  if (ageDays === 1) return t("kanban.card.updated_yesterday");
  return tf("kanban.card.updated_days", { days: ageDays });
}

const LANE_GLYPH: Record<"blocked" | "review", IconName> = {
  blocked: "statusBlocked",
  review: "statusReview",
};

function noticeText(notice: KanbanNotice): string {
  switch (notice.kind) {
    case "saved":
      return tf("kanban.notice.saved", { title: notice.title });
    case "changed":
      return tf("kanban.notice.changed", { title: notice.title });
    case "failed":
      return tf("kanban.notice.failed", { title: notice.title });
    case "undone":
      return tf("kanban.notice.undone", { title: notice.title });
    case "undo_refused":
      return tf("kanban.notice.undo_refused", { title: notice.title });
    case "no_owner":
      return tf("kanban.notice.no_owner", { title: notice.title });
    case "message_refused":
      return tf("kanban.notice.message_refused", {
        title: notice.title,
        cap: CARD_TEXT_CAP,
      });
    case "message_changed":
      return tf("kanban.notice.message_changed", { title: notice.title });
    case "message_unread":
      return tf("kanban.notice.message_unread", { title: notice.title });
    case "woke":
      return tf("kanban.notice.woke", { bot: botName(notice.bot) });
    case "busy":
      return tf("kanban.notice.busy", { bot: botName(notice.bot) });
    case "wake_failed":
      return tf("kanban.notice.wake_failed", { bot: botName(notice.bot) });
    case "message_busy":
      return tf("kanban.notice.message_busy", { bot: botName(notice.bot) });
    case "message_failed":
      return tf("kanban.notice.message_failed", { bot: botName(notice.bot) });
    case "no_bot":
      return tf("kanban.notice.no_bot", { seat: notice.seat });
  }
}

function focusOnMount(node: HTMLElement | null) {
  node?.focus();
}

/** The option tile's secondary line: "Recommended." (and/or the effect), or "" for neither. */
function optionEffect(option: CardOption): string {
  return [option.recommended ? t("kanban.needs.recommended") : null, option.effect]
    .filter((part): part is string => Boolean(part))
    .join(" ");
}

/** How long after the preview mounts Send stays disabled (F11): the preview renders where the
 * control that opened it was, within a frame, so without a delay the second click of a double
 * click on that control would land on Send and send a message the owner never read. */
export const SEND_ARM_MS = 600;

/** The message-mode preview: text, Send (armed `SEND_ARM_MS` after mount, ignoring a double
 * click's second click) and Cancel (never delayed). The caller keys this by the draft's ref,
 * sha256 and text, so a new draft remounts it and re-arms. */
function MessagePreview({
  bot,
  text,
  disabled,
  onSend,
  onCancel,
}: {
  bot: string;
  text: string;
  disabled: boolean;
  onSend(): void;
  onCancel(): void;
}) {
  const labelId = useId();
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setArmed(true), SEND_ARM_MS);
    return () => clearTimeout(timer);
  }, []);
  return (
    // biome-ignore lint/a11y/useSemanticElements: a labelled group, not a form control (F11 spec).
    <div className="kanban-needs-preview" role="group" aria-labelledby={labelId}>
      <p id={labelId}>{tf("kanban.needs.preview_label", { bot })}</p>
      <pre tabIndex={-1} ref={focusOnMount}>
        {text}
      </pre>
      <div>
        <button
          type="button"
          className="btn go sm"
          disabled={disabled || !armed}
          onClick={(event) => {
            // F11: ignore the second click of a double click; with a long system double-click
            // time it can still land here after Send arms.
            if (event.detail > 1) return;
            onSend();
          }}
        >
          {disabled ? t("kanban.needs.preview_sending") : t("kanban.needs.preview_send")}
        </button>
        <button type="button" className="btn alt sm" disabled={disabled} onClick={onCancel}>
          {t("kanban.needs.cancel")}
        </button>
      </div>
    </div>
  );
}

function NeedsYouArticle({
  item,
  disabled,
  canWrite,
  reading,
  message,
  onAnswer,
  onOpenNote,
}: {
  item: NeedsYouItem;
  disabled: boolean;
  canWrite: boolean;
  reading: boolean;
  message: CardMessageView | undefined;
  onAnswer(item: NeedsYouItem, answer: CardAnswer): void;
  onOpenNote(ref: string): void;
}) {
  const [replyOpen, setReplyOpen] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [sendBackOpen, setSendBackOpen] = useState(false);
  const [noteText, setNoteText] = useState("");
  const { card, read, ownerTags, botName: ownerBotName } = item;
  const isReview = card.status === "review";
  const needs = read?.body.needs ?? null;
  const hasOptions = !isReview && needs !== null && needs.options.length > 0;
  const replyShown = !hasOptions || replyOpen;
  const question = needs?.question || (isReview ? t("kanban.needs.review_question") : "");
  const hint = isReview
    ? ownerBotName
      ? tf("kanban.needs.review_hint", { bot: ownerBotName })
      : t("kanban.needs.review_hint_plain")
    : ownerBotName
      ? tf("kanban.needs.reply_hint", { bot: ownerBotName })
      : t("kanban.needs.reply_hint_plain");
  const done = read?.body.done ?? null;

  /** The write-mode (and message-mode fallback) actions: unchanged markup, a swappable hint. */
  function actionsBlock(hintText: string): ReactNode {
    return isReview ? (
      <div className="kanban-needs-actions">
        <button
          type="button"
          className="btn go sm"
          disabled={disabled}
          onClick={() => onAnswer(item, { kind: "approve" })}
        >
          {t("kanban.needs.approve")}
        </button>
        {sendBackOpen ? (
          <div className="kanban-needs-reply">
            <label>
              {t("kanban.needs.send_back_label")}
              <textarea
                value={noteText}
                disabled={disabled}
                onChange={(event) => setNoteText(event.target.value)}
              />
            </label>
            <div>
              <button
                type="button"
                className="btn alt sm"
                disabled={disabled || !/\S/.test(noteText)}
                onClick={() => onAnswer(item, { kind: "send_back", note: noteText })}
              >
                {t("kanban.needs.send_back")}
              </button>
              <button
                type="button"
                className="btn alt sm"
                disabled={disabled}
                onClick={() => {
                  setSendBackOpen(false);
                  setNoteText("");
                }}
              >
                {t("kanban.needs.cancel")}
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="btn alt sm"
            disabled={disabled}
            onClick={() => setSendBackOpen(true)}
          >
            {t("kanban.needs.send_back_open")}
          </button>
        )}
        <span className="kanban-needs-hint">{hintText}</span>
      </div>
    ) : (
      <div className="kanban-needs-actions">
        {hasOptions ? (
          <div className="kanban-needs-options">
            {(needs?.options ?? []).map((option) => {
              const effect = optionEffect(option);
              return (
                <button
                  key={option.letter}
                  type="button"
                  className="kanban-needs-option"
                  data-recommended={option.recommended || undefined}
                  disabled={disabled}
                  onClick={() => onAnswer(item, { kind: "decide", option })}
                >
                  <b>{`${option.letter} · ${option.label}`}</b>
                  {effect ? <span>{effect}</span> : null}
                </button>
              );
            })}
          </div>
        ) : null}
        {replyShown ? (
          <div className="kanban-needs-reply">
            <label>
              {t("kanban.needs.reply_label")}
              <textarea
                value={replyText}
                disabled={disabled}
                onChange={(event) => setReplyText(event.target.value)}
              />
            </label>
            <div>
              <button
                type="button"
                className="btn alt sm"
                disabled={disabled || !/\S/.test(replyText)}
                onClick={() => onAnswer(item, { kind: "reply", text: replyText })}
              >
                {t("kanban.needs.reply_send")}
              </button>
              {hasOptions ? (
                <button
                  type="button"
                  className="btn alt sm"
                  disabled={disabled}
                  onClick={() => {
                    setReplyOpen(false);
                    setReplyText("");
                  }}
                >
                  {t("kanban.needs.cancel")}
                </button>
              ) : null}
            </div>
          </div>
        ) : (
          <button
            type="button"
            className="btn alt sm"
            disabled={disabled}
            onClick={() => setReplyOpen(true)}
          >
            {t("kanban.needs.reply_open")}
          </button>
        )}
        <span className="kanban-needs-hint">{hintText}</span>
      </div>
    );
  }

  /** Message mode's per-item ladder, first match wins: no Bot, already sent on this read, an open
   * draft for this read, else the write-mode actions with the message hint. The preview's label
   * and the sent line name the draft's/sent entry's own Bot (fixed when it was composed or sent),
   * never `ownerBotName` (the item's current one, which a re-read or a re-seat can move). */
  function messageBody(msg: CardMessageView, cardRead: CardRead): ReactNode {
    if (ownerBotName === null) {
      return <p className="kanban-needs-unread">{t("kanban.needs.no_bot_card")}</p>;
    }
    const sentEntry = msg.sent.get(card.ref);
    if (sentEntry !== undefined && sentEntry.sha256 === cardRead.sha256) {
      return (
        <p className="kanban-needs-unread">{tf("kanban.needs.sent", { bot: sentEntry.botName })}</p>
      );
    }
    const { draft } = msg;
    if (draft !== null && draft.ref === card.ref && draft.sha256 === cardRead.sha256) {
      return (
        <MessagePreview
          key={`${draft.ref}\u0000${draft.sha256}\u0000${draft.text}`}
          bot={draft.botName}
          text={draft.text}
          disabled={disabled}
          onSend={msg.onSend}
          onCancel={msg.onCancel}
        />
      );
    }
    return actionsBlock(tf("kanban.needs.message_hint", { bot: ownerBotName }));
  }

  return (
    <article className="kanban-needs-item">
      <div className="kanban-needs-meta">
        <span className="kanban-chip" data-stage={card.status ?? undefined}>
          <Icon name={LANE_GLYPH[isReview ? "review" : "blocked"]} size={14} />
          {t(isReview ? "kanban.lane.review" : "kanban.lane.blocked")}
        </span>
        {ownerTags}
        {card.ageDays === null ? null : <span>{updatedText(card.ageDays)}</span>}
        {done ? (
          <span>{tf("kanban.card.done_of", { done: done.done, total: done.total })}</span>
        ) : null}
        {read?.body.verdict === "PASS" ? <span>{t("kanban.card.checker_pass")}</span> : null}
        {read?.body.verdict === "FAIL" ? <span>{t("kanban.card.checker_fail")}</span> : null}
        <button type="button" className="btn txt sm" onClick={() => onOpenNote(card.ref)}>
          {t("kanban.needs.open")}
        </button>
      </div>
      <h3>{card.title}</h3>
      {read ? (
        <>
          {read.body.what ? <p>{read.body.what}</p> : null}
          {read.body.now ? <p>{read.body.now}</p> : null}
          {question ? <p className="kanban-needs-question">{question}</p> : null}
          {canWrite
            ? actionsBlock(hint)
            : message !== undefined
              ? messageBody(message, read)
              : null}
        </>
      ) : !reading ? (
        <p className="kanban-needs-unread">{t("kanban.needs.unread")}</p>
      ) : null}
    </article>
  );
}

export function KanbanNeedsYou({
  items,
  counts,
  reading,
  canWrite,
  saving,
  lockedRef,
  notice,
  message,
  onAnswer,
  onUndo,
  onOpenNote,
}: KanbanNeedsYouProps) {
  const id = useId();
  if (items.length === 0 && notice === null) return null;
  const headingId = `${id}-needs-title`;

  return (
    <section className="kanban-needs" aria-labelledby={headingId}>
      <h2 id={headingId}>
        {counts
          ? tf("kanban.needs.title_count", { count: counts.blocked + counts.review })
          : t("kanban.needs.title")}
        {counts ? (
          <span className="kanban-needs-summary"> {tf("kanban.needs.summary", counts)}</span>
        ) : null}
      </h2>
      {notice ? (
        <p className="kanban-needs-notice" role="status">
          {noticeText(notice)}
          {notice.kind === "saved" && notice.undo ? (
            <button
              type="button"
              className="btn txt sm"
              ref={focusOnMount}
              disabled={saving}
              onClick={onUndo}
            >
              {t("kanban.notice.undo")}
            </button>
          ) : null}
        </p>
      ) : null}
      {canWrite ? null : (
        <p className="kanban-needs-no-write">
          {message ? t("kanban.needs.message_mode") : t("kanban.needs.no_write")}
        </p>
      )}
      {items.map((item) => (
        <NeedsYouArticle
          key={item.card.ref}
          item={item}
          disabled={saving || item.card.ref === lockedRef}
          canWrite={canWrite}
          reading={reading}
          message={message}
          onAnswer={onAnswer}
          onOpenNote={onOpenNote}
        />
      ))}
    </section>
  );
}
