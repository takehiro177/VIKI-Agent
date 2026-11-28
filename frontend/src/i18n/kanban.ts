/** `kanban.*`: the Kanban page's strings (KanbanView, KanbanNeedsYou, kanbanMessage, CardReader and App's Kanban fallback); its missing-skill line is Bots' `READINESS_TEXT.missing`. */

import { defineCatalog } from "./defineCatalog";

export const catalog = defineCatalog({
  en: {
    "kanban.empty":
      "No cards yet. A card is a note directly in this vault's kanban/ folder with type: card, status: todo, working, blocked, review, done or cancelled, and an owner/<seat> tag.",
    "kanban.title": "Kanban",
    "kanban.intro":
      "Cards from this vault's kanban/ folder, by status. Answer what needs you on top; Bots move cards with /kanban. Select a card to open its note.",
    "kanban.loading": "Loading Kanban…",
    "kanban.error": "Kanban couldn't read the cards. It tries again on its own.",
    "kanban.coverage": "The vault listing may be incomplete, so some cards may be missing.",
    "kanban.coverage.detail":
      "The vault listing may be incomplete ({detail}), so some cards may be missing.",
    "kanban.coverage.omitted": "at least {count} omitted",
    "kanban.empty.ask": "Ask the Chief to make the first card.",
    "kanban.open_chief": "Open Chief",
    "kanban.lane.todo": "To do",
    "kanban.lane.working": "Working",
    "kanban.lane.blocked": "Blocked",
    "kanban.lane.review": "Ready for review",
    "kanban.lane.done": "Done",
    "kanban.lane.cancelled": "Cancelled",
    "kanban.lane.other": "Other",
    "kanban.lane.unread": "Couldn't read",
    "kanban.lane.heading": "{lane} ({count})",
    "kanban.lane.empty": "No cards.",
    "kanban.card.owner": "Owner:",
    "kanban.card.owners": "Owners:",
    "kanban.card.no_owner": "No owner",
    "kanban.card.status": "Status: {status}",
    "kanban.card.updated_today": "Updated today",
    "kanban.card.updated_yesterday": "Updated yesterday",
    "kanban.card.updated_days": "Updated {days} days ago",
    "kanban.card.stale": "Stopped moving",
    "kanban.card.needs_you": "Needs you",
    "kanban.card.done_of": "{done} of {total} done",
    "kanban.card.checker_pass": "Checker passed",
    "kanban.card.checker_fail": "Checker failed",
    "kanban.unread.unsupported": "The front matter uses a form Kanban cannot read.",
    "kanban.unread.truncated": "The front matter is longer than Kanban reads.",
    "kanban.unread.changed": "The note changed while it was read.",
    "kanban.unread.unreadable": "The note could not be read ({reason}).",
    "kanban.unread.stopped": "The read stopped early ({reason}).",
    "kanban.unread.retry": "Not read yet. Kanban tries again on its own.",
    "kanban.unread.name": "Not read: Kanban cannot read this name.",
    "kanban.unread.cap": "Not read: Kanban reads at most {cap} cards.",
    "kanban.no_vault": "Open a vault to view Kanban.",
    "kanban.needs.title": "Needs you",
    "kanban.needs.title_count": "Needs you · {count}",
    "kanban.needs.summary": "{blocked} blocked and {review} to review, oldest first",
    "kanban.needs.no_write":
      "This vault doesn't take saves from VIKI Agent, so answer in the card's note.",
    "kanban.needs.unread": "Kanban couldn't read this card's text. Open the card to answer.",
    "kanban.needs.open": "Open card",
    "kanban.needs.recommended": "Recommended.",
    "kanban.needs.reply_open": "Reply in your own words…",
    "kanban.needs.reply_label": "Your answer",
    "kanban.needs.reply_send": "Send answer",
    "kanban.needs.cancel": "Cancel",
    "kanban.needs.reply_hint": "Your answer goes on the card word for word, and {bot} picks it up.",
    "kanban.needs.reply_hint_plain": "Your answer goes on the card word for word.",
    "kanban.needs.review_question": "Accept it and close the card?",
    "kanban.needs.approve": "Approve",
    "kanban.needs.send_back_open": "Send back…",
    "kanban.needs.send_back_label": "What to change",
    "kanban.needs.send_back": "Send back",
    "kanban.needs.review_hint":
      "Approve moves it to Done. Send back returns it to {bot} with your note.",
    "kanban.needs.review_hint_plain":
      "Approve moves it to Done. Send back returns it to To do with your note.",
    "kanban.notice.saved": "Saved your answer on {title}.",
    "kanban.notice.undo": "Undo",
    "kanban.notice.changed":
      "{title} changed since Kanban read it, so nothing was saved. Kanban is reading it again.",
    "kanban.notice.failed":
      "Kanban couldn't save {title}, so nothing changed. Open the card to answer there.",
    "kanban.notice.undone": "Undone: {title} is back as it was.",
    "kanban.notice.undo_refused": "Undo did nothing: {title} changed again or couldn't be saved.",
    "kanban.notice.woke": "Sent your answer to {bot}.",
    "kanban.notice.busy": "{bot} is busy; the answer waits on the card.",
    "kanban.notice.no_bot": "No Bot works owner/{seat}; the answer waits on the card.",
    "kanban.notice.no_owner": "This card names no owner; the answer waits on the card.",
    "kanban.notice.wake_failed": "Couldn't reach {bot}; the answer waits on the card.",
    "kanban.wake.message":
      "Kanban: I answered one of your cards. Load /kanban, then continue the card whose newest Handoff line is mine.",
    "kanban.needs.message_mode":
      "This vault doesn't take saves from VIKI Agent, so Kanban sends your answer to the card's Bot and shows you the message first.",
    "kanban.needs.message_hint": "{bot} gets your answer in a message you see before it goes.",
    "kanban.needs.no_bot_card": "No Bot can take this answer here, so answer in the card's note.",
    "kanban.needs.preview_label": "This goes to {bot} as your message, exactly as shown:",
    "kanban.needs.preview_send": "Send message",
    "kanban.needs.preview_sending": "Sending…",
    "kanban.needs.sent": "Sent to {bot}. The card stays here until {bot} records your answer.",
    "kanban.notice.message_busy":
      "{bot} is busy, so your answer wasn't sent. Send it again once {bot} is done.",
    "kanban.notice.message_failed":
      "Couldn't reach {bot}, so your answer wasn't sent. Try again, or answer in the card's note.",
    "kanban.notice.message_refused":
      "Kanban won't send an answer for {title}: its name or the option is over {cap} characters, or the answer has characters it can't show. Answer in the card's note.",
    "kanban.notice.message_changed":
      "{title} changed since Kanban read it, so your answer wasn't sent. Kanban is reading it again.",
    "kanban.notice.message_unread":
      "Couldn't read {title} again, so your answer wasn't sent. Kanban is reading it again.",
    "kanban.message.continue":
      "Kanban: I answered one of your cards. Load /kanban, find the card named on the next line, write the answer on the line after it under that card's question, word for word, set status: todo, then continue the card. Both lines are JSON strings.\n{card}\n{line}",
    "kanban.message.done":
      "Kanban: I approved one of your cards. Load /kanban, find the card named on the next line, write the answer on the line after it under that card's question, word for word, and set status: done. Both lines are JSON strings.\n{card}\n{line}",
    "kanban.reader.fold_done_when": "Done when",
    "kanban.reader.fold_done_when_count": "Done when · {done} of {total} done",
    "kanban.reader.fold_handoff": "Handoff",
    "kanban.reader.fold_handoff_count_one": "Handoff · 1 entry",
    "kanban.reader.fold_handoff_count": "Handoff · {count} entries",
    "kanban.reader.fold_check": "Check",
    "kanban.reader.fold_check_pass": "Check · PASS",
    "kanban.reader.fold_check_fail": "Check · FAIL",
  },
});
