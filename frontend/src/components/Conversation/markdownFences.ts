/**
 * A small CommonMark fenced-code-block tracker, shared by the line scanners that must treat a
 * fence as opaque: splitPlanDetails (Cards/planDetails.ts) and splitCardParts
 * (Kanban/CardReader.tsx). Both scan a note's lines for their own heading shape and must skip
 * anything inside a fenced code block — including a heading-shaped line quoted there as an
 * example — while never mistaking a mismatched delimiter for a real closer.
 *
 * CommonMark's rule: an opening fence is a line with at most 3 leading spaces, then a run of 3
 * or more backticks or tildes (the opener's character and run length); a backtick opener's info
 * string — the rest of the line — must hold no backtick (a tilde opener's info string has no
 * such limit). A closing fence is a line with at most 3 leading spaces, then a run of the
 * opener's own character at least as long as the opener's, then only spaces or tabs. Anything
 * else inside an open fence is content, and an unclosed fence runs to the end of the text.
 *
 * frontend/src/state/skillTriggers.ts has a private closing-line check of the same shape
 * (`isCloseLine`) for its own fenced-block reader; this tracker matches its semantics (same
 * character, same minimum length, trailing whitespace only) and adds the opener side both
 * callers here also need. That file is not touched by this tracker.
 */

/** An opening fence candidate: at most 3 leading spaces, then a run of 3+ backticks or tildes,
 * then the rest of the line (its info string, untrimmed — trimming cannot hide a backtick). */
const OPEN_FENCE_RE = /^ {0,3}(`{3,}|~{3,})(.*)$/;

export interface FenceScanner {
  /** Feeds the next line, in order. True when the line is itself a fence delimiter (an opener
   * or a matching closer) or sits inside an open fence, meaning a caller's own line-shape check
   * (a heading, say) must not run on it. False for ordinary content outside any fence, which a
   * caller is free to test as it likes. */
  step(line: string): boolean;
}

/** Whether `line` closes a fence opened with `fenceChar` repeated `minLen` times: the same
 * character, at least that many of them, then nothing but spaces or tabs. */
function isCloser(line: string, fenceChar: string, minLen: number): boolean {
  const re = fenceChar === "`" ? /^ {0,3}(`+)[ \t]*$/ : /^ {0,3}(~+)[ \t]*$/;
  const match = re.exec(line);
  return match !== null && (match[1]?.length ?? 0) >= minLen;
}

/**
 * A new tracker for one text, used top-to-bottom: construct it once per scan and call `step`
 * with each line in order, exactly once each.
 */
export function fenceScanner(): FenceScanner {
  let fenceChar: string | null = null;
  let fenceLen = 0;
  return {
    step(line: string): boolean {
      if (fenceChar !== null) {
        if (isCloser(line, fenceChar, fenceLen)) {
          fenceChar = null;
          fenceLen = 0;
        }
        return true;
      }
      const open = OPEN_FENCE_RE.exec(line);
      if (!open || open[1] === undefined) return false;
      const run = open[1];
      const char = run[0] ?? "`";
      if (char === "`" && (open[2] ?? "").includes("`")) return false;
      fenceChar = char;
      fenceLen = run.length;
      return true;
    },
  };
}
