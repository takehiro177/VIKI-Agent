/**
 * The "Needs you" section atop the Kanban page (SN §F): blocked cards waiting on a decision and
 * review cards waiting on an approval, oldest change first. A dumb view only — the caller reads
 * each card's body and owner tags and hands them in as `items`; this component renders them and
 * reports the owner's choice back through `onAnswer`/`onUndo`/`onOpenNote`. It never saves
 * anything itself.
 */
import { type ReactNode, useId, useState } from "react";
import { t, tf } from "../../i18n/t";
import { botName } from "../Bots/team";
import { Icon, type IconName } from "../Icon/Icon";
import "./Kanban.css";
import type { CardAnswer, CardOption, CardRead } from "./kanbanBody";
import type { KanbanCard, KanbanNotice } from "./kanbanCards";

export interface NeedsYouItem {
  card: KanbanCard;
  read: CardRead | null;
  /** The card's owner tag(s), already rendered (BotTag or plain text), exactly as lanes show them. */
  ownerTags: ReactNode;
  /** The Bot the owner's answer will wake, for the reply/review hint; null when none resolves. */
  botName: string | null;
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
    case "woke":
      return tf("kanban.notice.woke", { bot: botName(notice.bot) });
    case "busy":
      return tf("kanban.notice.busy", { bot: botName(notice.bot) });
    case "wake_failed":
      return tf("kanban.notice.wake_failed", { bot: botName(notice.bot) });
    case "no_bot":
      return tf("kanban.notice.no_bot", { seat: notice.seat });
  }
}

function focusOnMount(node: HTMLButtonElement | null) {
  node?.focus();
}

/** The option tile's secondary line: "Recommended." (and/or the effect), or "" for neither. */
function optionEffect(option: CardOption): string {
  return [option.recommended ? t("kanban.needs.recommended") : null, option.effect]
    .filter((part): part is string => Boolean(part))
    .join(" ");
}

function NeedsYouArticle({
  item,
  disabled,
  canWrite,
  reading,
  onAnswer,
  onOpenNote,
}: {
  item: NeedsYouItem;
  disabled: boolean;
  canWrite: boolean;
  reading: boolean;
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
          {canWrite ? (
            isReview ? (
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
                <span className="kanban-needs-hint">{hint}</span>
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
                <span className="kanban-needs-hint">{hint}</span>
              </div>
            )
          ) : null}
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
      {canWrite ? null : <p className="kanban-needs-no-write">{t("kanban.needs.no_write")}</p>}
      {items.map((item) => (
        <NeedsYouArticle
          key={item.card.ref}
          item={item}
          disabled={saving || item.card.ref === lockedRef}
          canWrite={canWrite}
          reading={reading}
          onAnswer={onAnswer}
          onOpenNote={onOpenNote}
        />
      ))}
    </section>
  );
}
