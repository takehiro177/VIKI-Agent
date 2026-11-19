import { tf } from "../../i18n/t";
import { visibleText } from "../Cards/visibleText";
import { type CardAnswer, ownerLineText } from "./kanbanBody";

/** Ref and decide-option-label cap, in code points (/kanban asks for ~60-character titles). */
export const CARD_TEXT_CAP = 200;

/**
 * The text Kanban previews and, only on **Send message**, sends into the card's Bot's own chat.
 *
 * F11 found that a ref — a Bot-writable file name — must never reach a message sent in the
 * owner's voice undetected; here a decide option's label is Bot-written too, and this is the one
 * place either is allowed to reach such a message at all. The message is always exactly three
 * lines: a fixed instruction line with no Bot-written text on it at all, then the ref as its own
 * JSON string line, then the owner's line as its own JSON string line — so Bot-written text never
 * shares a line with template text. That separation matters because `JSON.stringify` escapes a
 * real quote but not a character that merely looks like one (e.g. U+02BA, U+2033, U+201D): on a
 * line that mixes template prose with a quoted ref, such a look-alike can paint as if the quote
 * had closed early and the rest of the line were new instructions, but a line that is nothing but
 * the ref has no template prose for it to blend into. Either part over `CARD_TEXT_CAP` code
 * points refuses outright (null), and so does any hidden character — a bidi override, a
 * zero-width space, a tag character, a raw line/paragraph separator — anywhere in the composed
 * text, since `JSON.stringify` escapes none of those either. The owner reads this exact string in
 * full before choosing to send it, so refusing rather than returning a text that wouldn't paint as
 * what it says keeps that read-then-Send step a true check: the preview and the sent text are
 * always the same string.
 */
export function cardMessage(ref: string, answer: CardAnswer, date: string): string | null {
  if ([...ref].length > CARD_TEXT_CAP) return null;
  if (answer.kind === "decide" && [...answer.option.label].length > CARD_TEXT_CAP) return null;
  const text = tf(answer.kind === "approve" ? "kanban.message.done" : "kanban.message.continue", {
    card: JSON.stringify(ref),
    line: JSON.stringify(ownerLineText(answer, date)),
  });
  return visibleText(text).hidden ? null : text;
}
