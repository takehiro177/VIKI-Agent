import type { ReactElement } from "react";
import { t, tf } from "../../i18n/t";
import { Markdown, type MarkdownProps } from "../Conversation/Markdown";
import { fenceScanner } from "../Conversation/markdownFences";
import { parseDone, parseVerdict } from "./kanbanBody";
import { KANBAN_FOLDER } from "./kanbanCards";

/**
 * Folds a Kanban card's team-only sections when the Vault reader (VaultBrowser.tsx) shows one, so
 * the owner's part (the title, Summary, Needs you, any other section) stays at equal weight while
 * Done when / Handoff / Check — which can run to 100+ lines — sit behind a closed `<details>`
 * with a one-line label. Everything else renders exactly as `NoteMarkdown` renders it today.
 */

/** Mirrors exactly the props VaultBrowser.tsx's reader branch passes `NoteMarkdown`, so this
 * component is a drop-in alternative for a kanban card's note. */
export type CardReaderProps = Pick<MarkdownProps, "text" | "onOpenUrl" | "onOpenNoteLink">;

const FOLD_HEADINGS = new Set(["done when", "handoff", "check"]);

export interface CardPart {
  /** The trimmed `## ` heading text for this part, or null for a leading part with none. */
  heading: string | null;
  /** This part's full text exactly as it appears in the source, its own heading line included
   * when `heading` is not null. */
  text: string;
  /** Whether this part's heading is Done when, Handoff or Check (case-insensitive). */
  fold: boolean;
}

/**
 * Splits `text` into parts at level-2 (`## `) headings outside fenced code. A run of consecutive
 * non-fold parts is kept merged into one, so a card with none of the three fold headings renders
 * as a single part, byte-identical to the source; each fold heading stays its own part so it can
 * render inside its own closed `<details>`, and a non-fold heading that follows one (an
 * old-format card's `## Report to owner` after `## Done when`) starts a new part rather than
 * being swallowed into the fold before or after it.
 */
export function splitCardParts(text: string): CardPart[] {
  const lines = text.split("\n");
  const raw: { heading: string | null; text: string }[] = [];
  let start = 0;
  let heading: string | null = null;
  const fence = fenceScanner();
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    if (fence.step(line) || !line.startsWith("## ")) continue;
    raw.push({ heading, text: lines.slice(start, i).join("\n") });
    start = i;
    heading = line.slice(3).trim();
  }
  raw.push({ heading, text: lines.slice(start).join("\n") });

  const parts: CardPart[] = [];
  for (const chunk of raw) {
    if (chunk.heading === null && chunk.text.trim() === "") continue;
    const fold = chunk.heading !== null && FOLD_HEADINGS.has(chunk.heading.toLowerCase());
    const last = parts[parts.length - 1];
    if (!fold && last && !last.fold) {
      last.text += `\n${chunk.text}`;
    } else {
      parts.push({ heading: chunk.heading, text: chunk.text, fold });
    }
  }
  return parts.length > 0 ? parts : [{ heading: null, text, fold: false }];
}

/** The text after a fold part's own heading line, which is never repeated inside its `<details>`
 * (the `<summary>` label stands in for it). */
function foldBody(partText: string): string {
  const at = partText.indexOf("\n");
  return at === -1 ? "" : partText.slice(at + 1);
}

function doneWhenLabel(body: string): string {
  const counts = parseDone(body);
  return counts
    ? tf("kanban.reader.fold_done_when_count", counts)
    : t("kanban.reader.fold_done_when");
}

/** Top-level `- ` lines, the same shape a Handoff entry always starts with (kanbanBody.ts's
 * template: "- YYYY-MM-DD · seat · status · notes touched · next step · blockers"). An indented
 * continuation line is not top-level and must not count. */
function handoffEntryCount(body: string): number {
  return body.split("\n").filter((line) => line.startsWith("- ")).length;
}

function handoffLabel(body: string): string {
  const count = handoffEntryCount(body);
  if (count === 0) return t("kanban.reader.fold_handoff");
  return count === 1
    ? t("kanban.reader.fold_handoff_count_one")
    : tf("kanban.reader.fold_handoff_count", { count });
}

function checkLabel(body: string): string {
  const verdict = parseVerdict(body);
  if (verdict === "PASS") return t("kanban.reader.fold_check_pass");
  if (verdict === "FAIL") return t("kanban.reader.fold_check_fail");
  return t("kanban.reader.fold_check");
}

/** `heading` is one of FOLD_HEADINGS (case-insensitive); the caller only calls this for a fold
 * part. */
function foldLabel(heading: string, body: string): string {
  const name = heading.toLowerCase();
  if (name === "done when") return doneWhenLabel(body);
  if (name === "handoff") return handoffLabel(body);
  return checkLabel(body);
}

/**
 * A Kanban card: a note whose ref sits directly in `kanban/`, with no subfolder, and whose raw
 * source front matter has the exact line `type: card`. Reads `source`, never `text` — the
 * reader's displayed text has already had its front matter stripped (VaultBrowser.tsx's
 * `readerBody`), so only the raw source still carries it.
 */
export function isKanbanCard(ref: string, source: string): boolean {
  const parts = ref.split("/");
  if (parts.length !== 2 || parts[0]?.toLowerCase() !== KANBAN_FOLDER || !parts[1]) return false;
  const normalized = source.replace(/^\uFEFF/, "").replace(/\r\n|\r/g, "\n");
  const lines = normalized.split("\n");
  if ((lines[0] ?? "").trim() !== "---") return false;
  const end = lines.slice(1).findIndex((line) => line.trim() === "---");
  if (end === -1) return false;
  return lines.slice(1, end + 1).some((line) => line.trim() === "type: card");
}

export function CardReader({ text, onOpenUrl, onOpenNoteLink }: CardReaderProps): ReactElement {
  const parts = splitCardParts(text);
  return (
    <>
      {parts.map((part, index) =>
        part.fold && part.heading !== null ? (
          // biome-ignore lint/suspicious/noArrayIndexKey: parts are recomputed whole from the text on every render; two can share a heading, so index is the only stable key.
          <details key={index} className="kanban-reader-fold">
            <summary>{foldLabel(part.heading, foldBody(part.text))}</summary>
            <Markdown
              text={foldBody(part.text)}
              onOpenUrl={onOpenUrl}
              {...(onOpenNoteLink ? { onOpenNoteLink } : {})}
            />
          </details>
        ) : (
          <Markdown
            // biome-ignore lint/suspicious/noArrayIndexKey: see above
            key={index}
            text={part.text}
            onOpenUrl={onOpenUrl}
            {...(onOpenNoteLink ? { onOpenNoteLink } : {})}
          />
        ),
      )}
    </>
  );
}
