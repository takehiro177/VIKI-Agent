/**
 * Decision's plan briefs put the owner's part first and their evidence last, under a "## Details"
 * heading, so the plan card (Cards.tsx) can show the finding and the proposal and fold the
 * evidence behind a disclosure. Splitting the plan text here, as plain lines, keeps Cards.tsx
 * from parsing Markdown itself or asking the Markdown renderer to fold its own output.
 */
import { fenceScanner } from "../Conversation/markdownFences";

/** A heading line reading "Details" alone, any level 1-6, optionally closed by trailing `#`s —
 * the one shape a plan's evidence heading is told to use. */
const DETAILS_HEADING = /^ {0,3}#{1,6}[ \t]+details[ \t]*#*[ \t]*$/i;

function dropTrailingBlankLines(lines: string[]): string[] {
  let end = lines.length;
  while (end > 0 && (lines[end - 1] ?? "").trim() === "") end--;
  return lines.slice(0, end);
}

/**
 * `text` split at its first "## Details" heading outside a fenced code block: `top` is
 * everything before it with trailing blank lines dropped, and `details` is everything after it
 * (the heading line itself is dropped). No such heading, or nothing left before it, leaves the
 * plan whole: `{ top: text, details: null }`.
 */
export function splitPlanDetails(text: string): { top: string; details: string | null } {
  const lines = text.split("\n");
  const fence = fenceScanner();
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    if (fence.step(line)) continue;
    if (DETAILS_HEADING.test(line)) {
      const top = dropTrailingBlankLines(lines.slice(0, i)).join("\n");
      if (top.trim() === "") return { top: text, details: null };
      return { top, details: lines.slice(i + 1).join("\n") };
    }
  }
  return { top: text, details: null };
}
