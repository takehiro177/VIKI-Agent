import { displayThreadTurn } from "./botThreads";
import type { Block, TextBlock, Turn } from "./view";

export const NOTE_CHARS = 280;

export interface ThreadNote {
  id: string;
  text: string;
}

const CUT_KINDS = new Set<Block["kind"]>(["thinking", "tools", "routing"]);

function isText(block: Block): block is TextBlock {
  return block.kind === "text";
}

function isPostBlock(block: Block): boolean {
  return (
    (isText(block) && block.text.trim() !== "") ||
    (block.kind === "notice" && block.tone === "error")
  );
}

/** The first non-blank line, whitespace collapsed to one space and trimmed, cut to
 * `NOTE_CHARS`. "" for a VIKI_RESULT/VIKI_HANDOFF/VIKI_DONE directive line, or none. */
export function noteLine(text: string): string {
  const line = text.split("\n").find((candidate) => candidate.trim() !== "");
  if (line === undefined) return "";
  const collapsed = line.replace(/\s+/g, " ").trim();
  if (
    collapsed === "VIKI_DONE" ||
    collapsed.startsWith("VIKI_RESULT ") ||
    collapsed.startsWith("VIKI_HANDOFF ")
  )
    return "";
  return collapsed.length > NOTE_CHARS ? `${collapsed.slice(0, NOTE_CHARS - 1)}…` : collapsed;
}

/** Splits a turn into its post (what the group log shows) and progress notes (the text at or
 * before the turn's last thinking/tool/subagent block). When nothing is left to post, the
 * last earlier text stands in for it instead. Pure: never mutates `turn`. */
export function threadPost(turn: Turn): { post: Turn; notes: ThreadNote[] } {
  const shown = displayThreadTurn(turn);
  const blocks = shown.blocks;
  const cut = blocks.findLastIndex((block) => CUT_KINDS.has(block.kind));
  const kept = blocks.slice(cut + 1).filter(isPostBlock);
  const before = blocks.slice(0, cut + 1).filter(isText);
  const fallback = kept.some(isText)
    ? undefined
    : before.findLast((block) => block.text.trim() !== "");
  const notes = before
    .filter((block) => block !== fallback)
    .map((block) => ({ id: block.id, text: noteLine(block.text) }))
    .filter((note) => note.text !== "");
  return { post: { ...shown, blocks: fallback ? [fallback, ...kept] : kept }, notes };
}

/** True for a tool or subagent step label; the group log shows "Working" instead of naming it. */
export function isStepLabel(label: string): boolean {
  return label.startsWith("Using ") || label === "Working with an agent";
}

/** The latest completed text block's note line for a working Bot; null until one exists. */
export function latestNote(turn: Turn): string | null {
  const block = turn.blocks
    .filter(isText)
    .findLast((candidate) => candidate.done === true && noteLine(candidate.text) !== "");
  return block ? noteLine(block.text) : null;
}
