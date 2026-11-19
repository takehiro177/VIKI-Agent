import { SHA256 } from "../../state/vaultApi";

// Parses a card's body (the markdown under its front matter) and writes an owner's answer back
// into it. Byte-for-byte on the way out: `answerCard` edits only the lines SN §D names and keeps
// every other byte (BOM, CRLF, unrelated sections, unknown front matter) exactly as the source had
// it. `parseCardBody` is read-only and normalizes BOM/EOL away since its output is plain strings.

export interface CardOption {
  letter: string;
  label: string;
  recommended: boolean;
  effect: string | null;
}

export interface CardBody {
  what: string | null;
  now: string | null;
  needs: { question: string; options: CardOption[] } | null;
  done: { done: number; total: number } | null;
  verdict: "PASS" | "FAIL" | null;
}

export interface CardRead {
  source: string;
  sha256: string;
  body: CardBody;
}

export type CardAnswer =
  | { kind: "decide"; option: CardOption }
  | { kind: "reply"; text: string }
  | { kind: "approve" }
  | { kind: "send_back"; note: string };

const BOM = "\uFEFF";
const NEEDS_NAMES = ["needs you", "needs decision"];
const OPTION_RE = /^-\s+([A-Z])\.\s+(.*)$/;
// F2: a line that is trying to be an option (bullet + letter) but isn't one OPTION_RE accepts
// (e.g. a bold legacy "- **A. ...**"), used to fall back to free text for the whole section.
const OPTION_LOOKALIKE_RE = /^-\s+\**[A-Z][.)]/;
// F1: no leading `\s*` (the label is trimmed right after this is applied either way), so matching
// a label with no "(recommended)" suffix can't backtrack quadratically over a long run of spaces.
const RECOMMENDED_SUFFIX_RE = /\(recommended\)$/i;
const OWNER_LINE_RE = /^\*\*Owner, \d{4}-\d{2}-\d{2}:\*\*/;
const DONE_ITEM_RE = /^-\s*\[([ xX])\]/;
const VERDICT_RE = /^Verdict: (PASS|FAIL)\b/;

function withoutBom(text: string): string {
  return text.startsWith(BOM) ? text.slice(1) : text;
}

/** Strips `<!-- -->` spans in one linear pass; an unclosed `<!--` stops stripping and keeps the
 * rest verbatim (same output as the old `/<!--[\s\S]*?-->/g` regex, F1: that regex went quadratic
 * on many unclosed openers). */
function withoutComments(text: string): string {
  let out = "";
  let at = 0;
  for (;;) {
    const open = text.indexOf("<!--", at);
    if (open === -1) return out + text.slice(at);
    const close = text.indexOf("-->", open + 4);
    if (close === -1) return out + text.slice(at);
    out += text.slice(at, open);
    at = close + 3;
  }
}

/** 2 for `## `, 1 for `# ` (and nothing deeper), else null. Shared by the reader and the writer. */
function headingLevel(line: string): 1 | 2 | null {
  if (line.startsWith("## ")) return 2;
  if (line.startsWith("# ")) return 1;
  return null;
}

function headingName(line: string): string {
  return line.slice(3).trim();
}

/** A ``` fence delimiter (open or close). Shared by the reader and the writer, F3: a heading-
 * looking line inside a fence (e.g. a yaml `# comment`) must not end a section. */
function isFenceDelimiter(content: string): boolean {
  return content.trimStart().startsWith("```");
}

/** Raw `raw.note.source`/`sha256` from a vault note response, validated and parsed, or null. */
export function readCard(raw: Record<string, unknown>): CardRead | null {
  const note = raw.note;
  if (!note || typeof note !== "object" || Array.isArray(note)) return null;
  const { source, sha256 } = note as Record<string, unknown>;
  if (typeof source !== "string" || typeof sha256 !== "string" || !SHA256.test(sha256)) {
    return null;
  }
  return { source, sha256, body: parseCardBody(source) };
}

function stripFrontMatter(text: string): string {
  const lines = text.split("\n");
  if ((lines[0] ?? "").trim() !== "---") return text;
  const closeAt = lines.slice(1).findIndex((line) => line.trim() === "---");
  return closeAt === -1 ? text : lines.slice(closeAt + 2).join("\n");
}

function firstPrefixed(text: string, prefix: string): string | null {
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line.startsWith(prefix)) return line.slice(prefix.length).trim();
  }
  return null;
}

/** F2: true when an option-lookalike line fails OPTION_RE, or a real option is followed by an
 * indented, non-blank continuation line (a sub-item). Either shape means the section's options
 * don't cleanly fit the grammar, so SN §C's fallback applies: all of it is shown as text. */
function hasMalformedOptions(rawLines: readonly string[]): boolean {
  for (let i = 0; i < rawLines.length; i++) {
    const line = (rawLines[i] ?? "").trim();
    if (line === "") continue;
    const isOption = OPTION_RE.test(line);
    if (!isOption && OPTION_LOOKALIKE_RE.test(line)) return true;
    const next = rawLines[i + 1] ?? "";
    if (isOption && /^[ \t]/.test(next) && next.trim() !== "") return true;
  }
  return false;
}

function parseNeeds(text: string): { question: string; options: CardOption[] } {
  const rawLines = text.split("\n");
  const legacy = hasMalformedOptions(rawLines);
  const options: CardOption[] = [];
  const questionLines: string[] = [];
  for (const raw of rawLines) {
    const line = raw.trim();
    if (line === "") continue;
    const m = legacy ? null : OPTION_RE.exec(line);
    if (m) {
      const letter = m[1] ?? "";
      const rest = m[2] ?? "";
      const colonAt = rest.indexOf(": ");
      const labelRaw = colonAt === -1 ? rest : rest.slice(0, colonAt);
      const effect = colonAt === -1 ? null : rest.slice(colonAt + 2).trim();
      const recommended = RECOMMENDED_SUFFIX_RE.test(labelRaw);
      const label = (recommended ? labelRaw.replace(RECOMMENDED_SUFFIX_RE, "") : labelRaw).trim();
      options.push({ letter, label, recommended, effect });
      continue;
    }
    if (OWNER_LINE_RE.test(line)) continue;
    questionLines.push(line);
  }
  return { question: questionLines.join("\n"), options };
}

function parseDone(text: string): { done: number; total: number } | null {
  let done = 0;
  let total = 0;
  // Not trimmed: a leading space means the item is nested, not top-level, and must not count.
  for (const line of text.split("\n")) {
    const m = DONE_ITEM_RE.exec(line);
    if (!m) continue;
    total += 1;
    if (m[1] !== " ") done += 1;
  }
  return total === 0 ? null : { done, total };
}

function parseVerdict(text: string): "PASS" | "FAIL" | null {
  let verdict: "PASS" | "FAIL" | null = null;
  for (const raw of text.split("\n")) {
    const m = VERDICT_RE.exec(raw.trim());
    if (m) verdict = m[1] === "FAIL" ? "FAIL" : "PASS";
  }
  return verdict;
}

/** A named section's own text, comments stripped within it only (F3: never across a heading). */
function sectionText(
  lines: readonly string[],
  section: { start: number; end: number } | null,
): string {
  return section === null
    ? ""
    : withoutComments(lines.slice(section.start + 1, section.end).join("\n"));
}

/** The card's body: BOM and front matter skipped, `<!-- -->` ignored per section (SN §C). Never
 * throws. */
export function parseCardBody(source: string): CardBody {
  const normalized = withoutBom(source).replace(/\r\n|\r/g, "\n");
  const lines = stripFrontMatter(normalized).split("\n");
  const summary = sectionText(lines, findSection(lines, "summary", 0));
  const needsSection = findNeedsSection(lines, 0);
  return {
    what: firstPrefixed(summary, "What:"),
    now: firstPrefixed(summary, "Now:"),
    needs: needsSection === null ? null : parseNeeds(sectionText(lines, needsSection)),
    done: parseDone(sectionText(lines, findSection(lines, "done when", 0))),
    verdict: parseVerdict(sectionText(lines, findSection(lines, "check", 0))),
  };
}

/** Local `YYYY-MM-DD` for a timestamp, matching the card date SN §D writes. */
export function cardDate(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// --- answerCard: a byte-keeping writer -------------------------------------------------------

/** One physical source line, terminator (if any) still attached, so `.join("")` is lossless. */
function splitKeepEnds(text: string): string[] {
  return text.length === 0 ? [] : text.split(/(?<=\n)/);
}

function lineParts(line: string): { content: string; eol: string } {
  const m = /\r?\n$/.exec(line);
  return m ? { content: line.slice(0, m.index), eol: m[0] } : { content: line, eol: "" };
}

function frontMatterEnd(lines: readonly string[]): number | null {
  if (lines.length === 0 || withoutBom(lineParts(lines[0] ?? "").content) !== "---") return null;
  for (let i = 1; i < lines.length; i++) {
    if (lineParts(lines[i] ?? "").content === "---") return i;
  }
  return null;
}

/** F2-F2a: the writer's lines still include front matter (unlike the reader's, which
 * `stripFrontMatter` already removed); a line in there that looks like a fence or a heading must
 * never be mistaken for a real one, so every writer section search starts right after it. */
function bodyStart(lines: readonly string[]): number {
  const end = frontMatterEnd(lines);
  return end === null ? 0 : end + 1;
}

/** Rewrites `status:`/`modified:` in place; inserts `modified:` after `status:` when missing. */
function rewriteFrontMatter(lines: string[], status: string, date: string, eol: string): void {
  const end = frontMatterEnd(lines);
  if (end === null) throw new Error("missing_status");
  let statusAt = -1;
  let modifiedAt = -1;
  for (let i = 1; i < end; i++) {
    const content = lineParts(lines[i] ?? "").content;
    if (content.startsWith("status:")) statusAt = i;
    else if (content.startsWith("modified:")) modifiedAt = i;
  }
  if (statusAt === -1) throw new Error("missing_status");
  lines[statusAt] = `status: ${status}${lineParts(lines[statusAt] ?? "").eol}`;
  if (modifiedAt === -1) {
    lines.splice(statusAt + 1, 0, `modified: ${date}${eol}`);
  } else {
    lines[modifiedAt] = `modified: ${date}${lineParts(lines[modifiedAt] ?? "").eol}`;
  }
}

/** True for each line that starts already inside an unterminated `<!-- -->` span. */
function commentMask(content: readonly string[]): boolean[] {
  let inside = false;
  return content.map((line) => {
    const wasInside = inside;
    let rest = lineParts(line).content;
    for (;;) {
      if (inside) {
        const closeAt = rest.indexOf("-->");
        if (closeAt === -1) break;
        inside = false;
        rest = rest.slice(closeAt + 3);
      } else {
        const openAt = rest.indexOf("<!--");
        if (openAt === -1) break;
        inside = true;
        rest = rest.slice(openAt + 4);
      }
    }
    return wasInside;
  });
}

function asNewLines(text: string, eol: string): string[] {
  return text.split("\n").map((line) => line + eol);
}

function answerText(answer: CardAnswer): string {
  switch (answer.kind) {
    case "decide":
      return `${answer.option.letter}. ${answer.option.label}`;
    case "reply":
      return answer.text;
    case "approve":
      return "Approved.";
    case "send_back":
      return `Sent back: ${answer.note}`;
  }
}

// ponytail: the owner's words are written verbatim (SN §G), so a reply or note whose first line
// starts with "#" becomes a markdown heading once saved and can misread as a new section on the
// next parse. Upgrade path: warn before save if the draft's first line starts with "#".
export function ownerLineText(answer: CardAnswer, date: string): string {
  return `**Owner, ${date}:** ${answerText(answer)}`;
}

function handoffLineText(answer: CardAnswer, date: string, seat: string | null): string {
  if (answer.kind === "approve") {
    return `- ${date} · owner · done · this card · approved on Kanban · none`;
  }
  const continues = seat === null ? "" : `; ${seat} continues`;
  const raw = answer.kind === "send_back" ? answer.note : answerText(answer);
  const flat = raw.replace(/\r?\n/g, " ");
  const verb = answer.kind === "send_back" ? "sent back" : "answered";
  return `- ${date} · owner · todo · this card · ${verb} on Kanban: ${flat}${continues} · none`;
}

/** First `## <name>` heading (any case) at or after `from`, to the next `#`/`##` line or EOF
 * (SN §C). Fence-aware (F3d): a heading-looking line inside a ``` fence neither starts nor ends a
 * section. Shared, so the reader and the writer always locate the same section (F3); the writer
 * passes `bodyStart(lines)` so a line inside front matter is never mistaken for one (F2-F2a). */
function findSection(
  lines: readonly string[],
  name: string,
  from: number,
): { start: number; end: number } | null {
  let inFence = false;
  for (let i = from; i < lines.length; i++) {
    const content = lineParts(lines[i] ?? "").content;
    if (isFenceDelimiter(content)) {
      inFence = !inFence;
      continue;
    }
    if (inFence || headingLevel(content) !== 2 || headingName(content).toLowerCase() !== name) {
      continue;
    }
    let end = lines.length;
    let endFence = false;
    for (let j = i + 1; j < lines.length; j++) {
      const inner = lineParts(lines[j] ?? "").content;
      if (isFenceDelimiter(inner)) {
        endFence = !endFence;
        continue;
      }
      if (!endFence && headingLevel(inner) !== null) {
        end = j;
        break;
      }
    }
    return { start: i, end };
  }
  return null;
}

/** The Needs section to read or to answer (F3a/b): the first `## Needs you`, else the first
 * `## Needs decision`. One shared rule, so the reader shows the exact section the writer answers. */
function findNeedsSection(
  lines: readonly string[],
  from: number,
): { start: number; end: number } | null {
  for (const name of NEEDS_NAMES) {
    const section = findSection(lines, name, from);
    if (section) return section;
  }
  return null;
}

/** F2: true only when this section's own (comment-stripped) text parses with real options, same
 * rule the reader uses. A legacy/malformed section parses to `options: []`, and then the writer
 * must not drop any of its option-lookalike lines either (SN §C: it shows as text + a reply box). */
function sectionHasOptions(content: readonly string[]): boolean {
  const text = withoutComments(content.map((line) => lineParts(line).content).join("\n"));
  return parseNeeds(text).options.length > 0;
}

/** Decide/Reply: drop option lines outside comments (only when the section parses with options,
 * F2), insert after the last non-blank line. */
function appendOwnerLineAfterQuestion(
  content: readonly string[],
  ownerLines: readonly string[],
): string[] {
  const dropOptions = sectionHasOptions(content);
  const mask = commentMask(content);
  const kept = content.filter(
    (line, i) => !dropOptions || mask[i] || !OPTION_RE.test(lineParts(line).content.trim()),
  );
  let insertAt = 0; // front of `kept`: "after the heading" when nothing non-blank remains
  for (let i = 0; i < kept.length; i++) {
    if (lineParts(kept[i] ?? "").content.trim() !== "") insertAt = i + 1;
  }
  if (insertAt > 0) {
    // F4: line `insertAt - 1` may be the source's last line with no EOL; give it one before
    // joining the owner line on, or the two join onto one line with nothing between them.
    const last = lineParts(kept[insertAt - 1] ?? "");
    if (last.eol === "") {
      kept[insertAt - 1] = last.content + (lineParts(ownerLines[0] ?? "").eol || "\n");
    }
  }
  return [...kept.slice(0, insertAt), ...ownerLines, ...kept.slice(insertAt)];
}

/** Approve/Send back: the section body becomes just that one line. */
function replaceSectionBody(
  ownerLines: readonly string[],
  eol: string,
  hasFollowingHeading: boolean,
): string[] {
  return hasFollowingHeading ? [...ownerLines, eol] : [...ownerLines];
}

function insertMissingNeedsSection(
  lines: string[],
  ownerLines: readonly string[],
  eol: string,
): void {
  const done = findSection(lines, "done when", bodyStart(lines));
  if (done) {
    lines.splice(done.start, 0, `## Needs you${eol}`, ...ownerLines, eol);
    return;
  }
  const last = lineParts(lines[lines.length - 1] ?? "");
  // F2d: a trailing whitespace-only line with no EOL is blank (trim() === "") but still needs a
  // separator pushed, or the pushed heading glues directly onto it with no newline between.
  if (lines.length > 0 && (last.content.trim() !== "" || last.eol === "")) {
    lines.push(eol);
  }
  lines.push(`## Needs you${eol}`, ...ownerLines);
}

/** `lines.splice(start, deleteCount, ...replacement)` without spreading `replacement` into the
 * call (F2b: a 100k+-line section's replacement array blew the engine's argument-count limit).
 * Array-literal spread has no such limit, so slice/concat first, then copy back into `lines` in
 * place (callers keep using the same array reference). */
function spliceReplace(
  lines: string[],
  start: number,
  deleteCount: number,
  replacement: readonly string[],
): void {
  const merged = [...lines.slice(0, start), ...replacement, ...lines.slice(start + deleteCount)];
  lines.length = 0;
  for (const line of merged) lines.push(line);
}

function applyNeedsSection(
  lines: string[],
  answer: CardAnswer,
  ownerLines: readonly string[],
  eol: string,
): void {
  const section = findNeedsSection(lines, bodyStart(lines));
  if (!section) {
    insertMissingNeedsSection(lines, ownerLines, eol);
    return;
  }
  const { start, end } = section;
  const replacement =
    answer.kind === "approve" || answer.kind === "send_back"
      ? replaceSectionBody(ownerLines, eol, end < lines.length)
      : appendOwnerLineAfterQuestion(lines.slice(start + 1, end), ownerLines);
  spliceReplace(lines, start + 1, end - (start + 1), replacement);
}

/** The first line at or after `from` (and before `end`) past a comment block right there. */
function skipLeadingComment(lines: readonly string[], from: number, end: number): number {
  if (
    from >= end ||
    !lineParts(lines[from] ?? "")
      .content.trimStart()
      .startsWith("<!--")
  ) {
    return from;
  }
  for (let i = from; i < end; i++) {
    if (lineParts(lines[i] ?? "").content.includes("-->")) return i + 1;
  }
  return end;
}

function applyHandoffSection(lines: string[], handoffLine: string): void {
  const section = findSection(lines, "handoff", bodyStart(lines));
  if (!section) {
    const eol = lineParts(handoffLine).eol || "\n";
    const last = lineParts(lines[lines.length - 1] ?? "");
    // F2d: a trailing whitespace-only line with no EOL is blank (trim() === "") but still needs a
    // separator pushed, or the pushed heading glues directly onto it with no newline between.
    if (lines.length > 0 && (last.content.trim() !== "" || last.eol === "")) {
      lines.push(eol);
    }
    lines.push(`## Handoff${eol}`, handoffLine);
    return;
  }
  const insertAt = skipLeadingComment(lines, section.start + 1, section.end);
  if (insertAt > 0) {
    // F4: the line right before the insertion point may be the source's last line with no EOL
    // (e.g. a one-line comment right at EOF) - give it one first so it doesn't join onto handoffLine.
    const prev = lineParts(lines[insertAt - 1] ?? "");
    if (prev.eol === "") lines[insertAt - 1] = prev.content + (lineParts(handoffLine).eol || "\n");
  }
  lines.splice(insertAt, 0, handoffLine);
}

/**
 * SN §D exactly: rewrites `status:`/`modified:`, the Needs you body, and the top of Handoff.
 * Every other byte (BOM, CRLF, other sections, unknown front matter) is kept; new lines take the
 * source's own EOL. Throws when the front matter has no `status:` line.
 */
export function answerCard(
  source: string,
  answer: CardAnswer,
  date: string,
  seat: string | null,
): string {
  const eol = source.includes("\r\n") ? "\r\n" : "\n";
  const lines = splitKeepEnds(source);
  rewriteFrontMatter(lines, answer.kind === "approve" ? "done" : "todo", date, eol);
  const ownerLines = asNewLines(ownerLineText(answer, date), eol);
  applyNeedsSection(lines, answer, ownerLines, eol);
  applyHandoffSection(lines, handoffLineText(answer, date, seat) + eol);
  return lines.join("");
}
