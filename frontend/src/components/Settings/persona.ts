/** Parses the fixed-grammar Persona section in a starter vault's AGENTS.md. It fails closed on
 * edited or malformed content: the app does not edit a vault's AGENTS.md once it exists (its note
 * saves refuse that name), so the user edits the source file directly. */

export const PERSONA_OPEN = "<!-- viki:persona -->";
export const PERSONA_CLOSE = "<!-- /viki:persona -->";

export const TONES = ["calm", "precise", "playful"] as const;
export const PROACTIVITY = ["coach", "quiet"] as const;

/** The bounds: every value is one trimmed line with no control characters, within these lengths,
 * and Do/Don't hold at most this many items. */
export const NAME_MIN_LENGTH = 1;
export const NAME_MAX_LENGTH = 32;
export const ADDRESS_MIN_LENGTH = 1;
export const ADDRESS_MAX_LENGTH = 64;
export const LANGUAGE_MIN_LENGTH = 1;
export const LANGUAGE_MAX_LENGTH = 40;
export const ITEM_MIN_LENGTH = 1;
export const ITEM_MAX_LENGTH = 200;
export const MAX_ITEMS = 10;

export type Persona = {
  name: string;
  address: string;
  tone: string;
  language: string;
  proactivity: string;
  dos: string[];
  donts: string[];
};

type PersonaRead =
  | { state: "ok"; persona: Persona; body: string }
  | { state: "absent" }
  | { state: "edited"; body: string | null };

const HEADER = "## Persona";
const NAME_PREFIX = "- Name: ";
const ADDRESS_PREFIX = "- Address the owner as: ";
const TONE_PREFIX = "- Tone: ";
const LANGUAGE_PREFIX = "- Reply language: ";
const PROACTIVITY_PREFIX = "- Proactivity: ";
const DO_HEADER = "### Do";
const DONT_HEADER = "### Don't";

/**
 * Parses the text of a whole `AGENTS.md`-like file and extracts the persona block.
 *
 * - CRLF is normalised to LF before anything else, so a file edited on Windows parses the same as
 *   one the starter wrote (LF only).
 * - A marker is matched as a whole line, compared after `trimEnd` (trailing whitespace on the
 *   marker line is tolerated; a marker not flush with the start of the line is not a marker).
 * - No marker line anywhere means "absent". Anything other than exactly one open marker strictly
 *   before exactly one close marker is "edited" with no recoverable body (`body: null`) — there is
 *   no single span of text that is unambiguously "the persona section".
 * - Otherwise the body is the text strictly between the two marker lines. It reads "ok" only when
 *   the body parses against the Persona grammar, every parsed value satisfies
 *   `personaProblems`, and `renderPersona` of the parsed result reproduces the body
 *   byte-for-byte; otherwise "edited".
 */
export function readPersona(text: string): PersonaRead {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const opens: number[] = [];
  const closes: number[] = [];
  lines.forEach((line, index) => {
    const trimmed = line.trimEnd();
    if (trimmed === PERSONA_OPEN) opens.push(index);
    if (trimmed === PERSONA_CLOSE) closes.push(index);
  });

  if (opens.length === 0 && closes.length === 0) return { state: "absent" };

  if (opens.length === 1 && closes.length === 1) {
    const openIndex = opens[0];
    const closeIndex = closes[0];
    if (openIndex !== undefined && closeIndex !== undefined && openIndex < closeIndex) {
      const body = lines.slice(openIndex + 1, closeIndex).join("\n");
      const persona = parseBody(body);
      if (
        persona !== null &&
        personaProblems(persona).length === 0 &&
        renderPersona(persona) === body
      ) {
        return { state: "ok", persona, body };
      }
      return { state: "edited", body };
    }
  }

  return { state: "edited", body: null };
}

type ScannedLine = { raw: string; start: number; visible: string };

function splitSourceLines(text: string): { raw: string; start: number }[] {
  const lines: { raw: string; start: number }[] = [];
  let start = 0;
  while (start <= text.length) {
    const newline = text.indexOf("\n", start);
    const end = newline < 0 ? text.length : newline;
    lines.push({ raw: text.slice(start, end), start });
    if (newline < 0) break;
    start = newline + 1;
  }
  return lines;
}

function fenceLine(line: string): { char: string; length: number } | null {
  const match = /^( {0,3})(`{3,}|~{3,})(.*)$/.exec(line);
  if (!match) return null;
  const marker = match[2] ?? "";
  if (marker[0] === "`" && (match[3] ?? "").includes("`")) return null;
  return { char: marker[0] ?? "", length: marker.length };
}

function visibleMarkdownLines(text: string, skipFrontmatter: boolean): ScannedLine[] {
  const lines = splitSourceLines(text);
  let first = 0;
  const last = lines.length;
  const initial = lines[0]?.raw.replace(/\r$/, "").replace(/^\uFEFF/, "");
  if (skipFrontmatter && initial === "---") {
    let closing = 1;
    while (
      closing < lines.length &&
      lines[closing]?.raw.replace(/\r$/, "") !== "---" &&
      lines[closing]?.raw.replace(/\r$/, "") !== "..."
    ) {
      closing++;
    }
    if (closing === lines.length) return [];
    first = closing + 1;
  }

  const result: ScannedLine[] = [];
  let fence: { char: string; length: number } | null = null;
  let inComment = false;
  for (let index = first; index < last; index++) {
    const line = lines[index];
    if (!line) continue;
    let content = line.raw.endsWith("\r") ? line.raw.slice(0, -1) : line.raw;
    if (index === 0) content = content.replace(/^\uFEFF/, "");
    const marker = inComment ? null : fenceLine(content);
    if (fence) {
      const close = new RegExp("^ {0,3}" + fence.char + "{" + fence.length + ",}\\s*$").test(
        content,
      );
      if (close) fence = null;
      result.push({ ...line, visible: "" });
      continue;
    }
    if (marker) {
      fence = marker;
      result.push({ ...line, visible: "" });
      continue;
    }

    let visible = "";
    for (let cursor = 0; cursor < content.length; cursor++) {
      if (inComment) {
        const close = content.indexOf("-->", cursor);
        if (close < 0) break;
        inComment = false;
        cursor = close + 2;
      } else if (content.startsWith("<!--", cursor)) {
        inComment = true;
        cursor += 3;
      } else {
        visible += content[cursor];
      }
    }
    result.push({ ...line, visible });
  }
  return result;
}

const isBlank = (code: number): boolean => code === 0x20 || code === 0x09;

/**
 * A heading's text without its closing `#` run (blanks, `#`s, then optional blanks at the end).
 * Scanned once from the end: the equivalent regex retries from every blank in a long run of
 * them, which costs seconds on a line of spaces.
 */
export function withoutClosingHashes(text: string): string {
  let end = text.length;
  while (end > 0 && isBlank(text.charCodeAt(end - 1))) end--;
  let hashes = end;
  while (hashes > 0 && text.charCodeAt(hashes - 1) === 0x23) hashes--;
  let start = hashes;
  while (start > 0 && isBlank(text.charCodeAt(start - 1))) start--;
  return hashes < end && start < hashes ? text.slice(0, start) : text;
}

function personaHeadingLevel(line: string): { level: number; text: string } | null {
  const match = /^( {0,3})(#{1,6})[ \t]+(.*)$/.exec(line);
  if (!match) return null;
  let heading = withoutClosingHashes(match[3] ?? "")
    .trim()
    .normalize("NFKC");
  while (heading.length >= 2 && "*_".includes(heading[0] ?? "") && heading.at(-1) === heading[0]) {
    heading = heading.slice(1, -1).trim();
  }
  if (heading.endsWith(":")) heading = heading.slice(0, -1).trimEnd();
  return { level: (match[2] ?? "").length, text: heading.toLowerCase() };
}

function isPersonaHeading(text: string): boolean {
  const token = text.startsWith("personas")
    ? "personas"
    : text.startsWith("persona")
      ? "persona"
      : text.startsWith("\u30da\u30eb\u30bd\u30ca")
        ? "\u30da\u30eb\u30bd\u30ca"
        : "";
  if (!token) return false;
  const next = text[token.length] ?? "";
  return !/[a-z]/.test(next);
}

/** Finds the first visible Persona ATX heading and its original source section. */
export function findPersonaHeading(text: string): { heading: string; section: string } | null {
  const lines = visibleMarkdownLines(text, true);
  let selected: { index: number; level: number } | null = null;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (!line) continue;
    let sourceLine = line.raw.replace(/\r$/, "");
    if (index === 0) sourceLine = sourceLine.replace(/^\uFEFF/, "");
    if (!/^ {0,3}#{1,6}[ \t]+/.test(sourceLine)) continue;
    const parsed = personaHeadingLevel(line.visible);
    if (!parsed) continue;
    if (selected) {
      if (parsed.level <= selected.level) {
        const start = lines[selected.index]?.start;
        return start === undefined
          ? null
          : {
              heading: lines[selected.index]?.raw.replace(/\r$/, "") ?? "",
              section: text.slice(start, line.start),
            };
      }
    } else if (isPersonaHeading(parsed.text)) {
      selected = { index, level: parsed.level };
    }
  }
  if (!selected) return null;
  const line = lines[selected.index];
  if (!line) return null;
  return {
    heading: line.raw.replace(/\r$/, ""),
    section: text.slice(line.start),
  };
}

/** Checks standalone textual @AGENTS.md imports outside comments, code spans and fences. */
export function importsAgents(text: string): boolean {
  const lines = visibleMarkdownLines(text, false);
  let codeTicks = 0;
  for (const line of lines) {
    let visible = "";
    for (let cursor = 0; cursor < line.visible.length; cursor++) {
      const char = line.visible[cursor];
      if (char !== "`") {
        if (codeTicks === 0) visible += char;
        continue;
      }
      let end = cursor + 1;
      while (line.visible[end] === "`") end++;
      const length = end - cursor;
      if (codeTicks === 0) codeTicks = length;
      else if (codeTicks === length) codeTicks = 0;
      cursor = end - 1;
    }
    if (
      codeTicks === 0 &&
      line.raw.trim() === visible.trim() &&
      (visible.trim() === "@AGENTS.md" || visible.trim() === "@./AGENTS.md")
    ) {
      return true;
    }
  }
  return false;
}

/** The exact inverse of the body grammar `readPersona` parses: always the same blank-line
 * skeleton, regardless of how many Do/Don't items there are. */
export function renderPersona(persona: Persona): string {
  return [
    HEADER,
    "",
    `${NAME_PREFIX}${persona.name}`,
    `${ADDRESS_PREFIX}${persona.address}`,
    `${TONE_PREFIX}${persona.tone}`,
    `${LANGUAGE_PREFIX}${persona.language}`,
    `${PROACTIVITY_PREFIX}${persona.proactivity}`,
    "",
    DO_HEADER,
    ...persona.dos.map((item) => `- ${item}`),
    "",
    DONT_HEADER,
    ...persona.donts.map((item) => `- ${item}`),
  ].join("\n");
}

/** Structural parse only: the five fields in fixed order, then 0-10 "- " items under each of
 * `### Do` and `### Don't`, separated by exactly the blank lines the template uses. Bounds, enum
 * membership and the "no control characters" rule are `personaProblems`'s job, not this one's —
 * so a merely out-of-bounds (but structurally intact) edit still yields a `Persona`
 * `personaProblems` can explain, instead of a bare parse failure. */
function parseBody(body: string): Persona | null {
  const lines = body.split("\n");
  let i = 0;
  const take = (): string | undefined => lines[i++];
  const field = (prefix: string): string | null => {
    const line = take();
    if (line === undefined || !line.startsWith(prefix)) return null;
    return line.slice(prefix.length).trim();
  };

  if (take() !== HEADER) return null;
  if (take() !== "") return null;

  const name = field(NAME_PREFIX);
  const address = field(ADDRESS_PREFIX);
  const tone = field(TONE_PREFIX);
  const language = field(LANGUAGE_PREFIX);
  const proactivity = field(PROACTIVITY_PREFIX);
  if (
    name === null ||
    address === null ||
    tone === null ||
    language === null ||
    proactivity === null
  ) {
    return null;
  }

  if (take() !== "") return null;
  if (take() !== DO_HEADER) return null;

  const dos: string[] = [];
  for (;;) {
    const line = take();
    if (line === undefined) return null;
    if (line === "") break;
    if (!line.startsWith("- ")) return null;
    dos.push(line.slice(2).trim());
  }

  if (take() !== DONT_HEADER) return null;

  const donts: string[] = [];
  for (;;) {
    const line = take();
    if (line === undefined) break;
    if (!line.startsWith("- ")) return null;
    donts.push(line.slice(2).trim());
  }

  return { name, address, tone, language, proactivity, dos, donts };
}

function hasControlChar(value: string): boolean {
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

function valueProblems(
  label: string,
  value: string,
  bounds?: { min: number; max: number },
): string[] {
  const problems: string[] = [];
  if (value.includes("\n") || value.includes("\r")) {
    problems.push(`${label} must be a single line.`);
  } else if (value !== value.trim()) {
    problems.push(`${label} must not have leading or trailing whitespace.`);
  }
  if (hasControlChar(value)) problems.push(`${label} must not contain control characters.`);
  if (bounds && (value.length < bounds.min || value.length > bounds.max)) {
    problems.push(`${label} must be ${bounds.min}-${bounds.max} characters.`);
  }
  return problems;
}

/** English validation messages for a `Persona`, empty when every rule holds. */
export function personaProblems(persona: Persona): string[] {
  const problems: string[] = [
    ...valueProblems("Name", persona.name, { min: NAME_MIN_LENGTH, max: NAME_MAX_LENGTH }),
    ...valueProblems("Address", persona.address, {
      min: ADDRESS_MIN_LENGTH,
      max: ADDRESS_MAX_LENGTH,
    }),
    ...valueProblems("Tone", persona.tone),
    ...valueProblems("Reply language", persona.language, {
      min: LANGUAGE_MIN_LENGTH,
      max: LANGUAGE_MAX_LENGTH,
    }),
    ...valueProblems("Proactivity", persona.proactivity),
  ];
  if (!(TONES as readonly string[]).includes(persona.tone)) {
    problems.push(`Tone must be one of ${TONES.join(", ")}.`);
  }
  if (!(PROACTIVITY as readonly string[]).includes(persona.proactivity)) {
    problems.push(`Proactivity must be one of ${PROACTIVITY.join(", ")}.`);
  }
  if (persona.dos.length > MAX_ITEMS) problems.push(`Do must have at most ${MAX_ITEMS} items.`);
  if (persona.donts.length > MAX_ITEMS)
    problems.push(`Don't must have at most ${MAX_ITEMS} items.`);
  persona.dos.forEach((item, index) => {
    problems.push(
      ...valueProblems(`Do item ${index + 1}`, item, {
        min: ITEM_MIN_LENGTH,
        max: ITEM_MAX_LENGTH,
      }),
    );
  });
  persona.donts.forEach((item, index) => {
    problems.push(
      ...valueProblems(`Don't item ${index + 1}`, item, {
        min: ITEM_MIN_LENGTH,
        max: ITEM_MAX_LENGTH,
      }),
    );
  });
  return problems;
}

/** Splits free text into items the way the Persona page's Do/Don't textareas do: one item per
 * line, trimmed, blank lines dropped, a leading "- " stripped if present. */
export function itemsFromText(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => (line.startsWith("- ") ? line.slice(2) : line));
}

type DiffEntry = { kind: "same" | "add" | "del"; text: string };

function zeros(length: number): number[] {
  return new Array(length).fill(0);
}

/** LCS table of `a`'s and `b`'s suffixes, built bottom-up so every row is a plain, fully written
 * array rather than one `personaDiff` would need to prove in-bounds to the compiler. */
function lcsLengths(a: readonly string[], b: readonly string[]): readonly (readonly number[])[] {
  const n = a.length;
  const m = b.length;
  const lengths: number[][] = Array.from({ length: n + 1 }, () => zeros(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    const below = lengths[i + 1] ?? [];
    const row = zeros(m + 1);
    for (let j = m - 1; j >= 0; j--) {
      row[j] = a[i] === b[j] ? (below[j + 1] ?? 0) + 1 : Math.max(below[j] ?? 0, row[j + 1] ?? 0);
    }
    lengths[i] = row;
  }
  return lengths;
}

/** A line-level LCS diff from `before` to `after`, the shape the Persona page draws its live
 * preview from: an unchanged line carries through as "same", a line only in `before` is "del", a
 * line only in `after` is "add". */
export function personaDiff(before: string, after: string): DiffEntry[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const lengths = lcsLengths(a, b);
  const result: DiffEntry[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const ai = a[i];
    const bj = b[j];
    if (ai !== undefined && ai === bj) {
      result.push({ kind: "same", text: ai });
      i++;
      j++;
      continue;
    }
    const keepDel = (lengths[i + 1]?.[j] ?? 0) >= (lengths[i]?.[j + 1] ?? 0);
    if (keepDel) {
      if (ai !== undefined) result.push({ kind: "del", text: ai });
      i++;
    } else {
      if (bj !== undefined) result.push({ kind: "add", text: bj });
      j++;
    }
  }
  while (i < a.length) {
    const ai = a[i];
    if (ai !== undefined) result.push({ kind: "del", text: ai });
    i++;
  }
  while (j < b.length) {
    const bj = b[j];
    if (bj !== undefined) result.push({ kind: "add", text: bj });
    j++;
  }
  return result;
}

/** The longest run of consecutive backticks in `text`, plus one, floored at 3: a fence this long
 * can never be closed early by anything already inside the text. */
export function fenceFor(text: string): string {
  let longest = 0;
  let current = 0;
  for (const char of text) {
    if (char === "`") {
      current++;
      if (current > longest) longest = current;
    } else {
      current = 0;
    }
  }
  return "`".repeat(Math.max(3, longest + 1));
}

/** The chat message the Persona page's "Draft the change in a new chat" button hands off:
 * plain instructions plus the exact replacement block, fenced so no backtick run already in
 * `body` can break out of it. */
export function personaHandoff(body: string): string {
  const fence = fenceFor(body);
  return [
    "Please update the persona in this vault's AGENTS.md.",
    "",
    "Replace the lines between `<!-- viki:persona -->` and `<!-- /viki:persona -->` with exactly the block below. Keep both marker lines and change nothing else in the file.",
    "",
    `${fence}markdown`,
    body,
    fence,
  ].join("\n");
}

function quoteSource(text: string): string {
  const fence = fenceFor(text);
  return `${fence}markdown\n${text}\n${fence}`;
}

function handoffLimits(): string {
  return "Use the fixed Persona fields in order, with blank lines between groups. Keep Do and Don't at no more than 10 items each; keep every item within 200 characters.";
}

/** Drafts conversion of an existing unmarked section; caller hands this text to a new chat. */
export function personaConvertHandoff(heading: string, section: string): string {
  return [
    "Please convert the unmarked Persona section in this vault's AGENTS.md.",
    "",
    `Add exactly one pair of markers: ${PERSONA_OPEN} and ${PERSONA_CLOSE} around the quoted existing section. Convert its content to the fixed Persona field and blank-line layout. Preserve all other file text.`,
    handoffLimits(),
    "",
    "Existing heading:",
    quoteSource(heading),
    "",
    "Existing section:",
    quoteSource(section),
    "",
    "Use this layout:",
    quoteSource(
      "## Persona\n\n- Name: ...\n- Address the owner as: ...\n- Tone: calm\n- Reply language: English\n- Proactivity: coach\n\n### Do\n- ...\n\n### Don't\n- ...",
    ),
  ].join("\n");
}

/** Drafts repair of the existing marker pair without adding another pair. */
export function personaRepairHandoff(body: string | null): string {
  const message = [
    "Please repair the existing Persona section in this vault's AGENTS.md.",
    "",
    `Repair the existing marker structure and body using ${PERSONA_OPEN} and ${PERSONA_CLOSE} so the file has exactly one correctly ordered pair. Do not add a second pair. Use the fixed field and blank-line layout. Preserve all unrelated file text.`,
    handoffLimits(),
  ];
  if (body === null) {
    message.push(
      "The marker structure has no recoverable body. Do not guess or quote any source span.",
    );
  } else {
    message.push("", "Current recoverable body:", quoteSource(body));
  }
  return message.join("\n");
}

/** Drafts insertion of a new marked Persona block into an existing AGENTS.md. */
export function personaAddHandoff(body: string): string {
  const block = `${PERSONA_OPEN}\n${body}\n${PERSONA_CLOSE}`;
  return [
    "Please add a Persona section to this vault's AGENTS.md.",
    "",
    "Insert this complete new block in the existing file. Do not add a second Persona section. Preserve all other text.",
    handoffLimits(),
    "",
    "New block:",
    quoteSource(block),
  ].join("\n");
}
