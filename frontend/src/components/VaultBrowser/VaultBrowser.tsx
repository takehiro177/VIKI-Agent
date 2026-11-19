import {
  type CSSProperties,
  memo,
  type ReactElement,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CapturesApi } from "../../clients/vaults/capturesApi";
import { t, tf } from "../../i18n/t";
import { platformKind } from "../../platform";
import { type DecisionsApi, DecisionsApiError } from "../../state/decisionsApi";
import type { ExcerptApi, VaultExcerpt, VaultExcerptPart } from "../../state/excerptApi";
import { clampResize } from "../../state/layout";
import { type DisplayMetadata, parseDisplayMetadata } from "../../state/noteMetadata";
import { useReportUnsavedWork } from "../../state/unsavedWork";
import {
  type VaultBookmark,
  type VaultBookmarkApi,
  type VaultEntry,
  type VaultReadApi,
  type VaultRecoveryStatus,
  type VaultWriteApi,
  VaultWriteError,
  validWriteRef,
} from "../../state/vaultApi";
import type { VaultKnowledgeApi } from "../../state/vaultKnowledgeApi";
import type { VaultOpsApi } from "../../state/vaultOpsApi";
import { useVaultOverview } from "../../state/vaultOverview";
import { otherFolders, sectionOf } from "../../state/vaultOverviewStats";
import { clearBlockedWrite, setBlockedWrite, useBlockedWrites } from "../../state/vaultWriteLocks";
import { MAX_MARKDOWN_BYTES, Markdown } from "../Conversation/Markdown";
import { useModalFocus } from "../Dialog/useModalFocus";
import { BusyLabel, Loading, useHeld } from "../Loading/Loading";
import { ResizeHandle } from "../Shell/ResizeHandle";
import { MemoryPanel } from "./MemoryPanel";
import { NoteActions } from "./NoteActions";
import { statusTone } from "./NoteCard";
import { RecoveryPanel } from "./RecoveryPanel";
import { RelatedNotes } from "./RelatedNotes";
import { readerBody } from "./readerBody";
import { VaultOverview, VaultOverviewSkeleton } from "./VaultOverview";
import { VaultSectionInsight, type VaultSectionKey, VaultSectionPage } from "./VaultSectionPage";
import { linkWikilinks } from "./wikilinks";
import "./VaultBrowser.css";

// A save, a draft edit, or any other state change in the surrounding view re-renders this whole
// component; memoized on the note's own text and the stable onOpenUrl, this skips re-parsing the
// note's markdown when neither changed.
const NoteMarkdown = memo(Markdown);

type Phase = "loading" | "ready" | "partial" | "error";
type Coverage = { phase: Phase; reasons: string[]; omitted: number; error: string };
type Row = { ref: string; title: string; modified?: number; excerpt?: string; line?: number };
type Sort = "name" | "recent";
type View = "overview" | "notes" | "section";
export interface VaultNavigationSnapshot {
  workspaceId: string;
  ref: string | null;
  query: string;
  folder: string;
  sort: Sort;
  onlyMarked: boolean;
  /** Zero-based returned-list page; older snapshots omit it. */
  page?: number;
  /** The chosen browse view; older snapshots omit it. */
  view?: View;
  /** The active tag filter in All notes; older snapshots omit it. */
  tag?: string;
  /** The active section when `view` is `section`; older snapshots omit it. */
  section?: VaultSectionKey;
  opener: {
    kind: "row" | "link" | "backlink" | "lead" | "recent" | "rediscover" | "map" | "bookmark";
    ref: string;
  } | null;
  scroll: { top: number; left: number };
}
type OpenerKind = NonNullable<VaultNavigationSnapshot["opener"]>["kind"];
// A snapshot's section is restored only when it names one of the four section routes.
const SECTIONS = new Set<VaultSectionKey>(["knowledge", "sources", "maps", "else"]);
const sectionKeyOf = (value: unknown): VaultSectionKey | null =>
  typeof value === "string" && SECTIONS.has(value as VaultSectionKey)
    ? (value as VaultSectionKey)
    : null;
const viewOf = (value: unknown): View | null =>
  value === "overview" || value === "notes" || value === "section" ? value : null;
function withSection<T extends { section?: VaultSectionKey }>(
  value: T,
  section: VaultSectionKey | null,
): T {
  const { section: _section, ...rest } = value;
  return (section ? { ...rest, section } : rest) as T;
}
export interface VaultNavigationHandle {
  snapshot(): VaultNavigationSnapshot;
  open(ref: string, origin?: HTMLElement): boolean;
  canLeave(origin?: HTMLElement): boolean;
}
type Marks = {
  api: VaultBookmarkApi;
  workspaceId: string;
  refs: ReadonlySet<string>;
  items: readonly VaultBookmark[];
};
type Link = { target: string; state: string; candidates: string[] };
type Note = {
  ref: string;
  title: string;
  text: string;
  source: string;
  sha256: string;
  frontmatterError: string | null;
  displayMetadata?: DisplayMetadata;
  links: Link[];
};
type Backlink = { source: string; line: number };
type Commit = { short: string; date: string; subject: string };
type History = { phase: "loading" | "ready" | "unknown" | "error"; commits: Commit[] };
type Selection = { ref: string; workspaceId: string; api: VaultReadApi };
type Draft = { mode: "create" | "edit"; ref: string; content: string };
export interface VaultWriteContext {
  api: VaultWriteApi;
  policyRevision: number;
  rootPhysicalId: string;
  /** Given with `write`, so note actions and the recovery panel appear only where Save does. */
  ops?: VaultOpsApi;
}
/** For a vault whose folder is not available: the two clients that list and forget the app's own
 * records, which need no folder. It never enables Save, note actions or the memory panel. */
export interface VaultRecoveryOnlyContext {
  api: Pick<VaultWriteApi, "recovery">;
  ops: VaultOpsApi;
}
/** The memory panel's client and the vault revision it writes against. `protected` leaves only
 * Propose, which writes no vault, so it is given on every platform. */
export interface VaultMemoryContext {
  api: VaultKnowledgeApi;
  policyRevision: number;
  rootPhysicalId: string;
  protected: boolean;
}

const PARTIAL_REASONS = new Set([
  "deadline",
  "entry_limit",
  "item_limit",
  "byte_limit",
  "depth_limit",
  "unsafe_entry",
  "unreadable",
  "changed",
  "hit_limit",
]);
const LINK_STATES = new Set(["resolved", "missing", "ambiguous", "external", "unsafe"]);
const FRONTMATTER_ERRORS = new Set([
  "invalid_line",
  "duplicate_key",
  "too_many_lines",
  "unterminated",
]);
const ERRORS: Record<string, string> = {
  invalid_workspace: "This workspace cannot be read.",
  invalid_query: "Check the search text.",
  cancelled: "The read was cancelled.",
  forbidden: "This workspace is not available to this window.",
  not_found: "The requested note or workspace was not found.",
  unavailable: "The vault is unavailable.",
  invalid_response: "The vault returned an invalid response.",
  response_too_large: "The vault response is too large to display.",
};
// Fixed copy for server error codes that can reach either a read or a write: read
// failures carry the server's `error.code` (vaultApi.ts `get`), and write failures already do
// (`VaultWriteError.code`). Every other code keeps the generic read or write line.
const CODE_COPY: Record<string, string> = {
  note_too_large: "This note is too large to display.",
  target_busy: "Another app has this note open. Close it and try again.",
  invalid_encoding: "This note is not in a format the app can read.",
  overloaded: "The vault is busy. Wait a moment and try again.",
  unsafe_parent: "This note's folder is missing, or is a link or file the app cannot save through.",
  // Recovery only attempts the row at the app's next start; a post-`prepared` conflict, a
  // blocked row, or a lasting OSError still stays after that attempt, so a plain "try again" is
  // not true, and neither is "finishes it".
  // The caller also locks the ref (see `save()`).
  publication_busy:
    "Another save to this note has not finished. Check recovery status; the app tries again at its next start.",
  workspace_changed: "This workspace has changed. Reopen it and try again.",
  invalid_ref: "This note reference is invalid.",
  excluded_path: "This file cannot be edited in the app.",
  unsupported_filesystem: "This vault's drive does not support saving notes here.",
  // The client's pre-send check in `write` (`validWrite`, vaultApi.ts) refuses exactly the
  // server's control-character class and more than 2 MiB before the request is sent.
  invalid_publication: "This note has a control character, or is more than 2 MiB.",
  // Matches the server's own error message for a read-only target
  // (src/viki_agent/vaults/vault_write_api.py `_failure`).
  read_only_target: "This note is read-only. Clear Read-only in its Properties, then save again.",
  // stage_busy and parent_busy (423) are clean and retryable;
  // missing_target (503) means the note was moved or deleted outside the app during the edit.
  stage_busy: "Another app briefly has this note open. Try again.",
  parent_busy: "Another app briefly has this note's folder open. Try again.",
  missing_target: "This note was moved or deleted outside the app.",
  // The save route refuses with this while an unfinished note operation is pending.
  vault_recovery_required:
    "A note operation did not finish, so saving is blocked. Resolve it under Unfinished operations, then save again.",
  // The save waited its bounded time for the vault (a backup holds it, for one) and wrote nothing.
  vault_locked: "Another save or operation is in progress. Try again in a moment.",
};
// POSIX has no Properties dialog with a Read-only checkbox, so off Windows this names the
// remedy without claiming a Windows-only mechanism; `message` and the save-failure branch in
// `save()` choose it over CODE_COPY.read_only_target when platformKind() is not "windows".
const POSIX_READ_ONLY_TARGET =
  "This note is read-only or locked. Make it writable in your file manager, then save again.";
// Copy for the typed codes a bookmark call can fail with (vaultApi.ts `mark`, `get`); the server's
// own message is never shown, so every line here is fixed text. Any other code keeps the last line.
const BOOKMARK_COPY: Record<string, string> = {
  bookmarks_full: "Bookmarks are full. Remove one you no longer need, then try again.",
  invalid_representation: "This action must not include content.",
  overloaded: "The vault is busy. Wait a moment and try again.",
  unavailable: "Can't save the bookmark.",
  not_found: "Vault not found.",
  forbidden: "This window can't do that.",
};
const BOOKMARK_FALLBACK = "Couldn't process the bookmark.";
// Copy for the History section. An `unknown` answer also covers a repository with no commits, so
// its line claims nothing about the vault.
const HISTORY_COPY = {
  heading: "History",
  loading: "Loading the history…",
  unknown: "History isn't available for this note.",
  empty: "No commits found for this note.",
  error: "Couldn't read the history.",
};
// Copy for the Attach excerpt dialog. The typed codes are the ones `api.excerpt` raises
// (excerptApi.ts); the server's own message is never shown, so every line here is fixed text.
const EXCERPT_COPY = {
  open: "Attach excerpt",
  close: "Close",
  start: "First line",
  end: "Last line",
  notice: "This content is attached after your message and sent to Claude.",
  attach: "Attach",
  fallback: "Couldn't load the excerpt.",
};
const EXCERPT_ERRORS: Record<string, string> = {
  invalid_range: "Check the line range.",
  excerpt_too_large: "The selected range is too large.",
  not_active_vault: "This isn't the open vault.",
};
const excerptBackdrop: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 50,
  display: "grid",
  placeItems: "center",
  background: "var(--scrim)",
};
const excerptPanel: CSSProperties = {
  boxSizing: "border-box",
  width: "min(640px, 100%)",
  maxHeight: "90vh",
  overflow: "auto",
  padding: 16,
  border: "1px solid var(--line)",
  borderRadius: 6,
  background: "var(--g1)",
};
const excerptPreview: CSSProperties = { maxHeight: "40vh", overflow: "auto" };
// Copy for Propose, the protected vault's stand-in for Save. The codes are the ones
// `createProposal` raises (decisionsApi.ts `DecisionsApiError`, src/viki_agent/vaults/proposals.py `ProposalError`); the
// server's own message is never shown. Any other code, and any failure that is not a typed error,
// shows the `unavailable` line.
const PROPOSAL_COPY = {
  propose: "Propose",
  recorded: "Recorded the proposal. It waits under Needs you in the Chat view's right panel.",
  replayed: "The same proposal already exists.",
  reload: "Reload",
  unavailable: "Can't process the proposal.",
};
const PROPOSAL_ERRORS: Record<string, string> = {
  stale_revision: "The note has changed. Reload it.",
  no_change: "This is identical to the current note.",
  proposal_conflict: "This note already has another proposal. Withdraw it first.",
  proposals_full: "Proposals are full. Withdraw one you no longer need, then try again.",
  too_large: "The proposal is too large.",
};
const MAX_BOOKMARKS = 200;
const MAX_COMMITS = 20;
const MAX_SHORT_CHARS = 40;
const MAX_SUBJECT_CHARS = 200;
const MAX_ENTRIES = 5_000;
const PAGE_SIZE = 32;
function pageIndex(value: number | undefined): number {
  return value !== undefined &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= Math.floor((MAX_ENTRIES - 1) / PAGE_SIZE)
    ? value
    : 0;
}
const MAX_HITS = 100;
const MAX_NOTE_BYTES = 2 * 1024 * 1024;
const MAX_LINKS = 256;
const MAX_REF_CHARS = 512;
const MAX_EXCERPT_CHARS = 600;

const emptyCoverage = (phase: Phase): Coverage => ({
  phase,
  reasons: [],
  omitted: 0,
  error: "",
});
const object = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const boundedText = (value: unknown, max: number): value is string =>
  typeof value === "string" && value.isWellFormed() && Array.from(value).length <= max;
const boundedUtf8 = (value: unknown, max: number): value is string =>
  typeof value === "string" &&
  value.isWellFormed() &&
  new TextEncoder().encode(value).byteLength <= max;
const title = (value: unknown): value is string =>
  boundedText(value, MAX_REF_CHARS) && value.length > 0;
// A snapshot's tag is restored only when it is 1..64 code points with no control character.
const tagOf = (value: unknown): string | null =>
  boundedText(value, 64) && value.length > 0 && !/\p{Cc}/u.test(value) ? value : null;
// `tag` is the one snapshot field a normal action (Clear tag filter) can set back to absent;
// exactOptionalPropertyTypes refuses an explicit `tag: undefined`, so this drops the key instead
// of writing one.
function withTag<T extends { tag?: string }>(value: T, tag: string | undefined): T {
  const { tag: _tag, ...rest } = value;
  return (tag === undefined ? rest : { ...rest, tag }) as T;
}
const safeRef = (value: unknown): value is string =>
  boundedText(value, MAX_REF_CHARS) &&
  value.length > 0 &&
  !value.startsWith("/") &&
  !value.includes("\\") &&
  !value.includes(":") &&
  !value.split("/").some((part) => part === "." || part === "..") &&
  !Array.from(value).some((character) => (character.codePointAt(0) ?? 0) < 32);

function coverage(raw: Record<string, unknown>): Coverage {
  if (typeof raw.partial !== "boolean" || !Array.isArray(raw.partial_reasons))
    throw new Error("invalid_response");
  const reasons = raw.partial_reasons.filter(
    (reason): reason is string => typeof reason === "string" && PARTIAL_REASONS.has(reason),
  );
  if (reasons.length !== raw.partial_reasons.length || raw.partial !== reasons.length > 0)
    throw new Error("invalid_response");
  const omitted = raw.omitted === undefined ? 0 : raw.omitted;
  if (!Number.isSafeInteger(omitted) || (omitted as number) < 0)
    throw new Error("invalid_response");
  return {
    phase: raw.partial ? "partial" : "ready",
    reasons,
    omitted: omitted as number,
    error: "",
  };
}

function listResult(raw: Record<string, unknown>): { rows: Row[]; coverage: Coverage } {
  const entries = Array.isArray(raw.entries);
  const hits = Array.isArray(raw.hits);
  if (entries === hits) throw new Error("invalid_response");
  const source = entries ? raw.entries : raw.hits;
  if (!Array.isArray(source) || source.length > (entries ? MAX_ENTRIES : MAX_HITS))
    throw new Error("invalid_response");
  const rows = source.map((value) => {
    const row = object(value);
    if (!row || !safeRef(row.ref)) throw new Error("invalid_response");
    if (entries) {
      if (!title(row.title)) throw new Error("invalid_response");
      // `modified` only orders the Last modified sort; a missing one sorts last, a non-number is
      // refused.
      if (row.modified !== undefined && !Number.isFinite(row.modified))
        throw new Error("invalid_response");
      return {
        ref: row.ref,
        title: row.title,
        modified: (row.modified as number | undefined) ?? 0,
      };
    }
    if (
      !boundedText(row.excerpt, MAX_EXCERPT_CHARS) ||
      !Number.isSafeInteger(row.line) ||
      (row.line as number) < 1
    )
      throw new Error("invalid_response");
    return {
      ref: row.ref,
      title: row.ref,
      excerpt: row.excerpt,
      line: row.line as number,
    };
  });
  if (new Set(rows.map((row) => row.ref)).size !== rows.length) throw new Error("invalid_response");
  return { rows, coverage: coverage(raw) };
}

// The vault view validates what the read client returns raw (vaultApi.ts `bookmarks`).
function bookmarkItems(raw: Record<string, unknown>): VaultBookmark[] {
  if (!Array.isArray(raw.bookmarks) || raw.bookmarks.length > MAX_BOOKMARKS)
    throw new Error("invalid_response");
  const items = raw.bookmarks.map((value) => {
    const mark = object(value);
    if (
      !mark ||
      !safeRef(mark.ref) ||
      typeof mark.created_at !== "string" ||
      mark.created_at.length === 0
    )
      throw new Error("invalid_response");
    return { ref: mark.ref, created_at: mark.created_at };
  });
  return items;
}

// A listing row's folder is its first path component; a note at the vault root has none.
const folderOf = (ref: string): string | null =>
  ref.includes("/") ? ref.slice(0, ref.indexOf("/")) : null;

function displayTitle(note: { ref: string; title: string }): string {
  if (!/^(skill|readme|index)(?:\.(?:md|markdown))?$/i.test(note.title)) return note.title;
  const parts = note.ref.split("/");
  const filename = parts.pop() ?? note.ref;
  const parent = parts.pop();
  return parent ? `${parent} / ${filename}` : filename;
}

function noteResult(
  raw: Record<string, unknown>,
  requestedRef: string,
): { note: Note; coverage: Coverage } {
  const value = object(raw.note);
  if (
    !value ||
    value.ref !== requestedRef ||
    !safeRef(value.ref) ||
    !title(value.title) ||
    !boundedUtf8(value.text, MAX_NOTE_BYTES) ||
    !boundedUtf8(value.source, MAX_NOTE_BYTES) ||
    typeof value.sha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.sha256)
  )
    throw new Error("invalid_response");
  const hasMetadata = Object.hasOwn(value, "display_metadata");
  const displayMetadata = hasMetadata ? parseDisplayMetadata(value.display_metadata) : null;
  if (hasMetadata && displayMetadata === null) throw new Error("invalid_response");
  const frontmatterError = value.frontmatter_error;
  if (frontmatterError !== null && !FRONTMATTER_ERRORS.has(frontmatterError as string))
    throw new Error("invalid_response");
  if (!Array.isArray(raw.resolved_links) || raw.resolved_links.length > MAX_LINKS)
    throw new Error("invalid_response");
  const links = raw.resolved_links.map((item) => {
    const link = object(item);
    if (
      !link ||
      !boundedUtf8(link.target, MAX_NOTE_BYTES) ||
      link.target.length === 0 ||
      !LINK_STATES.has(link.state as string) ||
      !Array.isArray(link.candidates) ||
      link.candidates.length > MAX_ENTRIES
    )
      throw new Error("invalid_response");
    const candidates = link.candidates.filter(safeRef);
    if (
      candidates.length !== link.candidates.length ||
      new Set(candidates).size !== candidates.length ||
      (link.state === "resolved" && candidates.length !== 1) ||
      (link.state === "ambiguous" && candidates.length < 2) ||
      (link.state !== "resolved" && link.state !== "ambiguous" && candidates.length !== 0)
    )
      throw new Error("invalid_response");
    return { target: link.target, state: link.state as string, candidates };
  });
  if (new Set(links.map((link) => link.target)).size !== links.length)
    throw new Error("invalid_response");
  return {
    note: {
      ref: value.ref,
      title: value.title,
      text: value.text,
      source: value.source,
      sha256: value.sha256,
      frontmatterError: frontmatterError as string | null,
      ...(displayMetadata ? { displayMetadata } : {}),
      links,
    },
    coverage: coverage(raw),
  };
}

// A compact summary for the reader header. A covered tag can open the existing All-notes filter;
// tags on other notes are plain text because the overview cannot count them there.
const DEFAULT_TYPE: Record<string, string> = { Knowledge: "note", Source: "source", Map: "moc" };
function NoteChips({
  section,
  type,
  status,
  confidence,
  tags = [],
  onTag,
}: {
  section?: string | null | undefined;
  type?: string | null | undefined;
  status?: string | null | undefined;
  confidence?: string | null | undefined;
  tags?: string[] | undefined;
  onTag?: ((tag: string) => void) | undefined;
}): ReactElement | null {
  const defaultType = section ? DEFAULT_TYPE[section] : undefined;
  const typeAdds = Boolean(
    type &&
      type.toLowerCase() !== (section ?? "").toLowerCase() &&
      type.toLowerCase() !== defaultType,
  );
  if (!section && !typeAdds && !status && !confidence && tags.length === 0) return null;
  return (
    <ul className="vault-chips" aria-label="About this note">
      {section ? <li className="vault-reader-kicker">{section}</li> : null}
      {typeAdds ? <li>{type}</li> : null}
      {status ? (
        <li className="vault-chip-status" data-tone={statusTone(status) ?? "none"}>
          {status}
        </li>
      ) : null}
      {confidence ? <li>{`${confidence} confidence`}</li> : null}
      {tags.slice(0, 6).map((tag) => (
        <li key={tag}>
          {onTag ? (
            <button type="button" className="vault-reader-tag" onClick={() => onTag(tag)}>
              {`#${tag}`}
            </button>
          ) : (
            `#${tag}`
          )}
        </li>
      ))}
      {tags.length > 6 ? <li>{`+${tags.length - 6} more tags`}</li> : null}
    </ul>
  );
}

function backlinkResult(raw: Record<string, unknown>): {
  backlinks: Backlink[];
  coverage: Coverage;
} {
  if (!Array.isArray(raw.backlinks) || raw.backlinks.length > MAX_HITS)
    throw new Error("invalid_response");
  const backlinks = raw.backlinks.map((item) => {
    const row = object(item);
    if (!row || !safeRef(row.source) || !Number.isSafeInteger(row.line) || (row.line as number) < 1)
      throw new Error("invalid_response");
    return { source: row.source, line: row.line as number };
  });
  if (new Set(backlinks.map((link) => link.source)).size !== backlinks.length)
    throw new Error("invalid_response");
  return {
    backlinks: backlinks.sort((a, b) => (a.source < b.source ? -1 : a.source > b.source ? 1 : 0)),
    coverage: coverage(raw),
  };
}

// The route answers `at` as an ISO-8601 string; a number is read as epoch seconds.
function historyResult(raw: Record<string, unknown>): History {
  if (
    (raw.state !== "ready" && raw.state !== "unknown") ||
    !Array.isArray(raw.commits) ||
    raw.commits.length > MAX_COMMITS ||
    (raw.state === "unknown" && raw.commits.length > 0)
  )
    throw new Error("invalid_response");
  const commits = raw.commits.map((item) => {
    const row = object(item);
    const when =
      typeof row?.at === "number"
        ? row.at * 1000
        : typeof row?.at === "string"
          ? Date.parse(row.at)
          : NaN;
    if (
      !row ||
      !boundedText(row.short, MAX_SHORT_CHARS) ||
      row.short.length === 0 ||
      !boundedText(row.subject, MAX_SUBJECT_CHARS) ||
      !Number.isFinite(when)
    )
      throw new Error("invalid_response");
    return { short: row.short, date: new Date(when).toLocaleDateString(), subject: row.subject };
  });
  return { phase: raw.state, commits };
}

// A <textarea> normalizes CRLF to LF on read, so a CRLF note's draft would otherwise
// rewrite every line ending on save. True only when the base note holds CRLF and no bare LF.
function isCrlfOnly(source: string): boolean {
  return source.includes("\r\n") && !source.replace(/\r\n/g, "").includes("\n");
}

const lf = (text: string): string => text.replace(/\r\n?/g, "\n");

function message(error: unknown): string {
  const code = error instanceof Error ? error.message : "";
  if (code === "read_only_target" && platformKind() !== "windows") return POSIX_READ_ONLY_TARGET;
  return ERRORS[code] ?? CODE_COPY[code] ?? "The vault is unavailable.";
}

function bookmarkMessage(error: unknown): string {
  const code = error instanceof Error ? error.message : "";
  return BOOKMARK_COPY[code] ?? BOOKMARK_FALLBACK;
}

function isBotMemoryRef(ref: string): boolean {
  return ref.split("/", 1)[0]?.toLowerCase() === "bot-memory";
}

// Names the rule a disabled Create note is refusing. While the path is still being
// typed or its leaf has no ".md", that is the commonest cause, so say so; only a complete
// ".md" leaf the writer still refuses (CLAUDE.md, a reserved device name, ...) keeps the
// generic line.
function createDisabledReason(ref: string): string {
  const leaf = ref.split("/").pop() ?? "";
  return /\.md$/i.test(leaf) ? "This file is not edited in the app." : "Note paths end in .md.";
}

// Shared by both places a recovery lock is shown: a `recovery_required` answer, and a
// `conflict` outcome whose `cleanup_pending` flag keeps its row locked the same way.
function lockedMessage(operationId: string | null): string {
  return `Recovery is required before saving this note again${
    operationId ? ` (operation ${operationId})` : ""
  }. Your draft is still here.`;
}

// The server counts a note's last line terminator as the end of a line, not a line of its own
// (src/viki_agent/vaults/vault.py excerpt), so the bound the inputs show is the same count.
function lineCount(text: string): number {
  const lines = text.split("\n");
  return lines[lines.length - 1] === "" ? lines.length - 1 : lines.length;
}

// Previews the chosen lines through `api.excerpt` and hands the route's own `part` up untouched:
// the part carries the server's sha256 of the excerpt, which the later freshness check compares.
// The preview text comes from a vault note, so it is only ever a text node.
function ExcerptDialog({
  api,
  workspaceId,
  noteRef,
  lines,
  onAttach,
  onClose,
}: {
  api: ExcerptApi;
  workspaceId: string;
  noteRef: string;
  lines: number;
  onAttach: (part: VaultExcerptPart) => void;
  onClose: () => void;
}): ReactElement {
  const [start, setStart] = useState("1");
  const [end, setEnd] = useState("1");
  const [preview, setPreview] = useState<VaultExcerpt | null>(null);
  const [error, setError] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useModalFocus({ active: true, dialogRef, initialFocusRef: closeRef, onDismiss: onClose });

  useEffect(() => {
    setPreview(null);
    setError("");
    if (!/^\d+$/.test(start) || !/^\d+$/.test(end)) return;
    let live = true;
    const controller = new AbortController();
    api.excerpt(workspaceId, noteRef, Number(start), Number(end), controller.signal).then(
      (result) => {
        if (live) setPreview(result);
      },
      (failure) => {
        const code = failure instanceof Error ? failure.message : "";
        if (live) setError(EXCERPT_ERRORS[code] ?? EXCERPT_COPY.fallback);
      },
    );
    return () => {
      live = false;
      controller.abort();
    };
  }, [api, workspaceId, noteRef, start, end]);

  return (
    <div style={excerptBackdrop}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={EXCERPT_COPY.open}
        style={excerptPanel}
      >
        <button type="button" ref={closeRef} className="btn txt sm" onClick={onClose}>
          {EXCERPT_COPY.close}
        </button>
        <h2>{EXCERPT_COPY.open}</h2>
        <label>
          {EXCERPT_COPY.start}{" "}
          <input
            type="number"
            min={1}
            max={lines}
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </label>{" "}
        <label>
          {EXCERPT_COPY.end}{" "}
          <input
            type="number"
            min={1}
            max={lines}
            value={end}
            onChange={(event) => setEnd(event.target.value)}
          />
        </label>
        {error ? <p role="alert">{error}</p> : null}
        {preview ? <pre style={excerptPreview}>{preview.text}</pre> : null}
        <p>{EXCERPT_COPY.notice}</p>
        <button
          type="button"
          disabled={!preview}
          onClick={() => {
            if (!preview) return;
            onAttach(preview.part);
            onClose();
          }}
        >
          {EXCERPT_COPY.attach}
        </button>
      </div>
    </div>
  );
}

function State({ value }: { value: Coverage }): ReactElement | null {
  if (value.phase === "loading") return <Loading>Loading…</Loading>;
  if (value.phase === "error") return <p role="alert">{value.error}</p>;
  if (value.phase !== "partial") return null;
  return (
    <p role="status">
      Results may be incomplete ({value.reasons.join(", ")}
      {value.omitted > 0 ? `; at least ${value.omitted} omitted` : ""}).
    </p>
  );
}

export function VaultBrowser({
  workspaceId,
  api,
  write,
  recoveryOnly,
  memory,
  proposalApi,
  onOpenUrl,
  onBack,
  renderBack,
  initialFolder = "",
  initialRef,
  initialDraft,
  initialNavigation,
  onNavigation,
  onNavigationReady,
  onAttachExcerpt,
  captures,
  onTriage,
}: {
  workspaceId: string;
  /** `excerpt` is what the Attach excerpt control reads through; without it the control is absent. */
  api: VaultReadApi & VaultBookmarkApi & Partial<ExcerptApi>;
  write?: VaultWriteContext;
  /** Given instead of `write` when the folder is not available: only the forget-only panel. */
  recoveryOnly?: VaultRecoveryOnlyContext;
  memory?: VaultMemoryContext;
  /**
   * Given only for a `protected` vault, on every platform: the editor offers Propose instead of
   * Save. It takes no write lock and writes no vault, so the Save gate does not apply. `write`
   * wins if both are given.
   */
  proposalApi?: Pick<DecisionsApi, "createProposal">;
  onOpenUrl: (url: string) => void;
  /** "Back to chat", the way out every non-chat view offers. */
  onBack?: () => void;
  renderBack?: (onBack: () => void) => ReactNode;
  /** The folder the select starts on; it applies once the listing has that folder. */
  initialFolder?: string;
  /**
   * The note to open; it applies once, to the first listing, and only if that listing holds it.
   * It then moves the folder select to the note's own folder, so it wins over `initialFolder`.
   */
  initialRef?: string;
  /** Seeds the New note editor once, as `{mode: "create", ...initialDraft}`; applies only when
   * `write` is given. */
  initialDraft?: { ref: string; content: string };
  /** One window's admitted Back snapshot; it restores through the current reader without recording a step. */
  initialNavigation?: VaultNavigationSnapshot;
  /** The host owns accepted ref transitions when supplied; standalone keeps its own trail. */
  onNavigation?: (
    previous: VaultNavigationSnapshot,
    next: VaultNavigationSnapshot,
    owner: VaultNavigationHandle,
  ) => boolean;
  /** Registers this mounted leaf only; cleanup must release this exact handle. */
  onNavigationReady?: (handle: VaultNavigationHandle) => () => void;
  /** Receives the route's own excerpt part for the chosen lines; absent hides the control. */
  onAttachExcerpt?: (part: VaultExcerptPart) => void;
  /** The overview's Inbox lists these captures; with `onTriage` it offers Triage inbox. */
  captures?: CapturesApi;
  onTriage?: () => void;
}) {
  const initialRestore = useRef(
    initialNavigation?.workspaceId === workspaceId &&
      (initialNavigation.ref === null || safeRef(initialNavigation.ref))
      ? initialNavigation
      : null,
  );
  const initialSectionPending = useRef(
    initialRestore.current === null &&
      initialRef === undefined &&
      ["30-knowledge", "40-sources", "50-maps"].includes(initialFolder),
  );
  const startSection =
    sectionKeyOf(initialRestore.current?.section) ??
    (initialRestore.current === null
      ? initialFolder === "30-knowledge"
        ? "knowledge"
        : initialFolder === "40-sources"
          ? "sources"
          : initialFolder === "50-maps"
            ? "maps"
            : null
      : null);
  const restoredView = viewOf(initialRestore.current?.view);
  const startView =
    (restoredView === "section" && !sectionKeyOf(initialRestore.current?.section)
      ? null
      : restoredView) ?? (initialFolder !== "" ? "notes" : "overview");
  const startTag = tagOf(initialRestore.current?.tag) ?? undefined;
  const location = useRef<VaultNavigationSnapshot>(
    initialRestore.current
      ? withTag(withSection({ ...initialRestore.current, view: startView }, startSection), startTag)
      : {
          workspaceId,
          ref: null,
          query: "",
          folder: initialFolder,
          sort: "name",
          onlyMarked: false,
          view: startView,
          ...(startSection ? { section: startSection } : {}),
          opener: null,
          scroll: { top: 0, left: 0 },
        },
  );
  const noteTrail = useRef<VaultNavigationSnapshot[]>([]);
  const restoreFocus = useRef<VaultNavigationSnapshot | null>(null);
  const browserRef = useRef<HTMLElement>(null);
  const pendingWrites = useRef(new Set<symbol>());
  const mounted = useRef(true);
  const [query, setQuery] = useState(location.current.query);
  const [rows, setRows] = useState<Row[]>([]);
  const [listing, setListing] = useState<Coverage>(() => emptyCoverage("loading"));
  const [listOwner, setListOwner] = useState<{ api: VaultReadApi; key: string } | null>(null);
  const [listRefresh, setListRefresh] = useState(0);
  const [view, setView] = useState<View>(startView);
  const [section, setSection] = useState<VaultSectionKey | null>(startSection);
  const [folderControlFocused, setFolderControlFocused] = useState(false);
  const [overviewRefresh, setOverviewRefresh] = useState(0);
  const overviewState = useVaultOverview(api, workspaceId, overviewRefresh);
  const [folder, setFolder] = useState(location.current.folder);
  const [sort, setSort] = useState<Sort>(location.current.sort);
  const [onlyMarked, setOnlyMarked] = useState(location.current.onlyMarked);
  const [tag, setTag] = useState<string | null>(startTag ?? null);
  const [page, setPage] = useState(pageIndex(location.current.page));
  const [marks, setMarks] = useState<Marks | null>(null);
  const [markRefresh, setMarkRefresh] = useState(0);
  const [marking, setMarking] = useState(false);
  const [markError, setMarkError] = useState("");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [noteState, setNoteState] = useState<Coverage>(() => emptyCoverage("ready"));
  const [backlinks, setBacklinks] = useState<Backlink[]>([]);
  const [backlinkState, setBacklinkState] = useState<Coverage>(() => emptyCoverage("ready"));
  const [history, setHistory] = useState<History>({ phase: "loading", commits: [] });
  // Seeds `draft` through the useState initializer below only; the identity-reset effect never
  // reads this ref, so a StrictMode re-invocation of that effect cannot null the seed out before
  // it ever paints.
  const seededDraft = useRef<Draft | null>(
    write && initialDraft ? { mode: "create", ...initialDraft } : null,
  );
  const [draft, setDraft] = useState<Draft | null>(seededDraft.current);
  const [excerptRef, setExcerptRef] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "checking" | "saved" | "recovery" | "error" | "stale"
  >("idle");
  const [saveMessage, setSaveMessage] = useState("");
  const blockedWrites = useBlockedWrites(workspaceId);
  const listGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const detailController = useRef<AbortController | null>(null);
  const saveGeneration = useRef(0);
  const currentScope = useRef({ api, workspaceId });
  const writeApi = write?.api;
  const writePolicyRevision = write?.policyRevision;
  const writeRootPhysicalId = write?.rootPhysicalId;
  const opsApi = write?.ops;
  const memoryApi = memory?.api;
  const recoveryListApi = write?.api ?? recoveryOnly?.api;
  const recoveryOpsApi = opsApi ?? recoveryOnly?.ops;
  const [publications, setPublications] = useState<VaultRecoveryStatus[]>([]);
  const [recoveryRefresh, setRecoveryRefresh] = useState(0);
  const [recoveryCount, setRecoveryCount] = useState(0);
  const recoveryDetailsRef = useRef<HTMLDetailsElement>(null);
  const recoverySummaryRef = useRef<HTMLElement>(null);
  function openRecovery(): void {
    if (recoveryDetailsRef.current) recoveryDetailsRef.current.open = true;
    recoverySummaryRef.current?.focus();
  }
  const listKey = `${workspaceId}\0${query}`;
  const restorePageFor = useRef(initialRestore.current ? { api, key: listKey } : null);
  const currentListKey = useRef(listKey);
  currentListKey.current = listKey;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const noteListId = useId();
  const [focusHeading, setFocusHeading] = useState(false);

  // Entry focus is the view's own, as in every non-chat view.
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  // After an operation closes the note, the dialog that held focus is gone and so is its opener.
  // This runs once that dialog has unmounted and released its inert background.
  useEffect(() => {
    if (!focusHeading) return;
    headingRef.current?.focus();
    setFocusHeading(false);
  }, [focusHeading]);

  useEffect(() => {
    void listRefresh;
    currentScope.current = { api, workspaceId };
    const restored = restorePageFor.current;
    restorePageFor.current = null;
    if (!restored || restored.api !== api || restored.key !== listKey) {
      location.current = { ...location.current, page: 0 };
      setPage(0);
    }
    const mine = ++listGeneration.current;
    const key = listKey;
    const controller = new AbortController();
    setRows([]);
    setListing(emptyCoverage("loading"));
    setListOwner({ api, key });
    const timer = window.setTimeout(() => {
      api.entries(workspaceId, query.trim() || undefined, controller.signal).then(
        (raw) => {
          if (mine !== listGeneration.current || key !== currentListKey.current) return;
          try {
            const result = listResult(raw);
            setRows(result.rows);
            setListing(result.coverage);
          } catch (error) {
            setListing({ ...emptyCoverage("error"), error: message(error) });
          }
        },
        (error) => {
          if (
            !controller.signal.aborted &&
            mine === listGeneration.current &&
            key === currentListKey.current
          )
            setListing({ ...emptyCoverage("error"), error: message(error) });
        },
      );
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (listGeneration.current === mine) ++listGeneration.current;
    };
  }, [api, listKey, query, workspaceId, listRefresh]);

  // The recovery panel's save records: read on mount and after each operation. A failed read keeps
  // the last list, since the panel itself says nothing it could not know.
  useEffect(() => {
    void recoveryRefresh;
    if (!recoveryListApi || !recoveryOpsApi) return;
    const controller = new AbortController();
    recoveryListApi.recovery(controller.signal).then(
      (list) => {
        if (!controller.signal.aborted)
          setPublications(list.filter((row) => row.workspace_id === workspaceId));
      },
      () => {},
    );
    return () => controller.abort();
  }, [recoveryListApi, recoveryOpsApi, workspaceId, recoveryRefresh]);

  // Bookmarks are read once per workspace, and again after each change (`markRefresh`).
  useEffect(() => {
    void markRefresh;
    let live = true;
    const controller = new AbortController();
    api.bookmarks(workspaceId, controller.signal).then(
      (raw) => {
        if (!live) return;
        try {
          const items = bookmarkItems(raw);
          setMarks({ api, workspaceId, refs: new Set(items.map((item) => item.ref)), items });
          setMarkError("");
        } catch (error) {
          setMarkError(bookmarkMessage(error));
        }
      },
      (error) => {
        if (live) setMarkError(bookmarkMessage(error));
      },
    );
    return () => {
      live = false;
      controller.abort();
    };
  }, [api, workspaceId, markRefresh]);

  async function toggleMark(ref: string, marked: boolean): Promise<void> {
    const current = () =>
      api === currentScope.current.api && workspaceId === currentScope.current.workspaceId;
    setMarking(true);
    try {
      if (marked) await api.removeBookmark(workspaceId, ref);
      else await api.addBookmark(workspaceId, ref);
      if (current()) setMarkRefresh((tick) => tick + 1);
    } catch (error) {
      if (current()) setMarkError(bookmarkMessage(error));
    } finally {
      setMarking(false);
    }
  }

  // The previous run's own identity, so this effect can tell a real change (clear the draft, as
  // before) from this effect's first run and a StrictMode re-invocation of it (same deps both
  // times) — neither of which must touch the draft the useState initializer above already seeded.
  const previousIdentity = useRef<{
    api: typeof api;
    workspaceId: string;
    writeApi: typeof writeApi;
    writePolicyRevision: typeof writePolicyRevision;
    writeRootPhysicalId: typeof writeRootPhysicalId;
  } | null>(null);
  useEffect(() => {
    const cleanup = () => {
      ++detailGeneration.current;
      detailController.current?.abort();
    };
    const identity = { api, workspaceId, writeApi, writePolicyRevision, writeRootPhysicalId };
    const previous = previousIdentity.current;
    previousIdentity.current = identity;
    const changed =
      previous !== null &&
      (previous.api !== api ||
        previous.workspaceId !== workspaceId ||
        previous.writeApi !== writeApi ||
        previous.writePolicyRevision !== writePolicyRevision ||
        previous.writeRootPhysicalId !== writeRootPhysicalId);
    if (previous === null || !changed) return cleanup;
    currentScope.current = { api, workspaceId };
    ++detailGeneration.current;
    detailController.current?.abort();
    location.current = { ...location.current, workspaceId, ref: null };
    noteTrail.current = [];
    restoreFocus.current = null;
    pendingWrites.current.clear();
    setSelection(null);
    setNote(null);
    setBacklinks([]);
    setNoteState(emptyCoverage("ready"));
    setBacklinkState(emptyCoverage("ready"));
    setHistory({ phase: "loading", commits: [] });
    ++saveGeneration.current;
    setDraft(null);
    setExcerptRef(null);
    setSaveState("idle");
    setSaveMessage("");
    setRecoveryCount(0);
    return cleanup;
  }, [api, workspaceId, writeApi, writePolicyRevision, writeRootPhysicalId]);

  function open(ref: string): void {
    location.current = { ...location.current, ref };
    const mine = ++detailGeneration.current;
    const workspace = workspaceId;
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    setSelection({ ref, workspaceId: workspace, api });
    setNote(null);
    setBacklinks([]);
    setNoteState(emptyCoverage("loading"));
    setBacklinkState(emptyCoverage("loading"));
    setHistory({ phase: "loading", commits: [] });
    ++saveGeneration.current;
    setDraft(null);
    setExcerptRef(null);
    setSaveState("idle");
    setSaveMessage("");
    const current = () =>
      mine === detailGeneration.current &&
      workspace === currentScope.current.workspaceId &&
      api === currentScope.current.api;
    api.note(workspace, ref, controller.signal).then(
      (raw) => {
        if (!current()) return;
        try {
          const result = noteResult(raw, ref);
          setNote(result.note);
          setNoteState(result.coverage);
        } catch (error) {
          setNoteState({ ...emptyCoverage("error"), error: message(error) });
        }
      },
      (error) => {
        if (!controller.signal.aborted && current())
          setNoteState({ ...emptyCoverage("error"), error: message(error) });
      },
    );
    api.backlinks(workspace, ref, controller.signal).then(
      (raw) => {
        if (!current()) return;
        try {
          const result = backlinkResult(raw);
          setBacklinks(result.backlinks);
          setBacklinkState(result.coverage);
        } catch (error) {
          setBacklinkState({ ...emptyCoverage("error"), error: message(error) });
        }
      },
      (error) => {
        if (!controller.signal.aborted && current())
          setBacklinkState({ ...emptyCoverage("error"), error: message(error) });
      },
    );
    api.history(workspace, ref, controller.signal).then(
      (raw) => {
        if (!current()) return;
        try {
          setHistory(historyResult(raw));
        } catch {
          setHistory({ phase: "error", commits: [] });
        }
      },
      () => {
        if (!controller.signal.aborted && current()) setHistory({ phase: "error", commits: [] });
      },
    );
  }

  function closeNote(): void {
    location.current = { ...location.current, ref: null };
    ++detailGeneration.current;
    detailController.current?.abort();
    ++saveGeneration.current;
    setSelection(null);
    setNote(null);
    setBacklinks([]);
    setDraft(null);
    setSaveState("idle");
    setSaveMessage("");
  }

  function beginPending(): () => void {
    const current = () => mounted.current && currentMutationOwner.current === mutationOwner;
    if (!current()) return () => {};
    const pending = Symbol();
    pendingWrites.current.add(pending);
    return () => {
      pendingWrites.current.delete(pending);
    };
  }
  function refreshAfterOperation(): void {
    if (!mounted.current || currentMutationOwner.current !== mutationOwner) return;
    setListRefresh((tick) => tick + 1);
    setRecoveryRefresh((tick) => tick + 1);
    setOverviewRefresh((tick) => tick + 1);
  }
  const reloadOverview = () => setOverviewRefresh((tick) => tick + 1);

  // A rename, move, archive or delete leaves nothing at the open ref, so the note closes with its
  // editor: no draft can save to the old ref.
  function noteChanged(): void {
    if (!mounted.current || currentMutationOwner.current !== mutationOwner) return;
    noteTrail.current = [];
    refreshAfterOperation();
    closeNote();
    setFocusHeading(true);
  }

  async function save(): Promise<void> {
    const blocked = draft ? blockedWrites[draft.ref.toLowerCase()] : undefined;
    if (
      !write ||
      !draft ||
      blocked ||
      saveState === "saving" ||
      saveState === "checking" ||
      saveState === "recovery"
    )
      return;
    const editing = draft.mode === "edit" ? note : null;
    if (draft.mode === "edit" && (!editing || editing.ref !== draft.ref)) return;
    const release = beginPending();
    const mine = ++saveGeneration.current;
    setSaveState("saving");
    setSaveMessage("");
    const content =
      editing && isCrlfOnly(editing.source)
        ? draft.content.replace(/\r?\n/g, "\r\n")
        : draft.content;
    try {
      const outcome =
        draft.mode === "create"
          ? await write.api.createNote(
              workspaceId,
              write.policyRevision,
              write.rootPhysicalId,
              draft.ref,
              content,
            )
          : await write.api.saveNote(
              workspaceId,
              write.policyRevision,
              write.rootPhysicalId,
              draft.ref,
              content,
              editing?.sha256 ?? "",
            );
      // The lock is recorded even for a generation the user has already moved past (Cancel,
      // or another note), so an answer that arrives late still blocks the ref it names, the
      // same as the catch branch below does for recovery_required.
      if (outcome.status === "conflict" && outcome.cleanup_pending)
        setBlockedWrite(workspaceId, draft.ref, {
          operationId: outcome.operation_id,
          state: "recovery",
        });
      if (mine !== saveGeneration.current) return;
      if (outcome.status === "conflict") {
        // A collision after `prepared` (step 6) keeps its row and answers with
        // cleanup_pending: true; the ref locks exactly as for recovery_required, and the view
        // never re-reads a note the server still holds.
        if (outcome.cleanup_pending) {
          setSaveState("recovery");
          setSaveMessage(lockedMessage(outcome.operation_id));
          return;
        }
        // The server discards the stage and journal row on every other conflict, so
        // there is nothing pending to lock. Keep the draft; for an edit, re-read the note so a
        // deliberate retry sends the current hash.
        if (draft.mode === "create") {
          setSaveState("error");
          setSaveMessage("A note already exists at this path.");
          return;
        }
        setSaveState("checking");
        setSaveMessage("This note changed elsewhere. Your draft is still here.");
        try {
          const raw = await api.note(workspaceId, draft.ref);
          if (mine !== saveGeneration.current) return;
          const result = noteResult(raw, draft.ref);
          setNote(result.note);
          setNoteState(result.coverage);
        } catch {
          // Keep the draft and the conflict message; a retry still sends the stale hash
          // until a read succeeds.
        }
        if (mine === saveGeneration.current) setSaveState("error");
        return;
      }
      const savedRef = draft.ref;
      const wasCreate = draft.mode === "create";
      setDraft(null);
      open(savedRef);
      if (wasCreate) setListRefresh((tick) => tick + 1);
      setOverviewRefresh((tick) => tick + 1);
      setSaveState("saved");
      setSaveMessage(
        // A `saved` with cleanup_pending leaves no lock here. The row the user's own
        // save just made only blocks a later save to the *same* ref, which
        // will get a `publication_busy` answer and lock there; adding a second lock path for
        // one already-open draft is not worth the extra state to track.
        outcome.cleanup_pending
          ? "Saved. Recovery will finish cleaning up the previous version."
          : "Saved.",
      );
    } catch (error) {
      // The lock is recorded even for a generation the user has already moved past (Cancel,
      // or another note), so a slow answer still blocks the ref it names. A
      // publication_busy answer holds a row until the app's next start, so it locks
      // the same way, with no operation id to poll for.
      if (
        error instanceof VaultWriteError &&
        (error.code === "recovery_required" || error.code === "publication_busy")
      )
        setBlockedWrite(workspaceId, draft.ref, {
          operationId: error.code === "recovery_required" ? error.operationId : null,
          state: "recovery",
        });
      if (mine !== saveGeneration.current) return;
      setSaveState("error");
      if (error instanceof VaultWriteError && error.code === "recovery_required") {
        setSaveState("recovery");
        setSaveMessage(lockedMessage(error.operationId));
      } else if (error instanceof VaultWriteError && CODE_COPY[error.code]) {
        const text =
          error.code === "read_only_target" && platformKind() !== "windows"
            ? POSIX_READ_ONLY_TARGET
            : CODE_COPY[error.code];
        setSaveMessage(`${text} Your draft is still here.`);
      } else {
        setSaveMessage("The note could not be saved. Your draft is still here.");
      }
    } finally {
      release();
    }
  }

  // The note is never written here, so the editor closes on an answer and nothing is locked; the
  // draft stays on a refusal. `stale` is the one refusal with a way forward (Reload).
  async function propose(): Promise<void> {
    const editing = draft?.mode === "edit" && note?.ref === draft.ref ? note : null;
    if (!proposalApi || !draft || !editing || saveState === "saving") return;
    const release = beginPending();
    const mine = ++saveGeneration.current;
    setSaveState("saving");
    setSaveMessage("");
    const content = isCrlfOnly(editing.source)
      ? draft.content.replace(/\r?\n/g, "\r\n")
      : draft.content;
    try {
      const outcome = await proposalApi.createProposal(
        workspaceId,
        draft.ref,
        content,
        editing.sha256,
      );
      if (mine !== saveGeneration.current) return;
      setDraft(null);
      setSaveState("saved");
      setSaveMessage(outcome.replayed ? PROPOSAL_COPY.replayed : PROPOSAL_COPY.recorded);
    } catch (error) {
      if (mine !== saveGeneration.current) return;
      const code = error instanceof DecisionsApiError ? error.code : "";
      setSaveState(code === "stale_revision" ? "stale" : "error");
      setSaveMessage(PROPOSAL_ERRORS[code] ?? PROPOSAL_COPY.unavailable);
    } finally {
      release();
    }
  }

  async function refreshRecovery(): Promise<void> {
    if (!write || !draft) return;
    const key = draft.ref.toLowerCase();
    const blocked = blockedWrites[key];
    if (!blocked) return;
    const release = beginPending();
    const mine = saveGeneration.current;
    setSaveState("checking");
    try {
      const rows = await write.api.recovery();
      if (mine !== saveGeneration.current) return;
      const pending = rows.some(
        (row) => row.workspace_id === workspaceId && row.ref.toLowerCase() === key,
      );
      if (pending) {
        setSaveState(blocked.state);
        setSaveMessage("Recovery is still pending. Your draft is still here.");
        return;
      }
      clearBlockedWrite(workspaceId, draft.ref);
      setSaveState("idle");
      setSaveMessage("Recovery is clear. Review the current note before saving again.");
    } catch {
      if (mine !== saveGeneration.current) return;
      setSaveState("recovery");
      setSaveMessage("Recovery status is unavailable. Your draft is still here.");
    } finally {
      release();
    }
  }

  // Whether the open draft differs from its base; App asks on the same signal. Line
  // endings are compared normalized: the textarea hands back LF for a CRLF note.
  const dirty =
    draft !== null &&
    (draft.mode === "create"
      ? draft.ref !== "" || draft.content !== ""
      : lf(draft.content) !== lf(note?.source ?? draft.content));
  useReportUnsavedWork("vault", dirty);
  function canLeave(origin?: HTMLElement): boolean {
    if (origin && (!origin.isConnected || origin.getAttribute("aria-modal") !== "true"))
      return false;
    return (
      mounted.current &&
      workspaceId === currentScope.current.workspaceId &&
      api === currentScope.current.api &&
      pendingWrites.current.size === 0 &&
      saveState !== "checking" &&
      !Array.from(document.querySelectorAll('[aria-modal="true"]')).some(
        (modal) => !origin || (modal !== origin && !modal.contains(origin)),
      ) &&
      !browserRef.current?.querySelector('[role="menu"]')
    );
  }
  const confirmDiscard = (origin?: HTMLElement): boolean =>
    canLeave(origin) && (!dirty || window.confirm("Discard your unsaved changes to this note?"));

  function snapshot(): VaultNavigationSnapshot {
    const region = browserRef.current?.closest<HTMLElement>('[data-region="view"]');
    return {
      ...location.current,
      page: pageIndex(location.current.page),
      scroll: { top: region?.scrollTop ?? 0, left: region?.scrollLeft ?? 0 },
    };
  }

  function requestOpen(ref: string, kind: OpenerKind = "row", origin?: HTMLElement): boolean {
    if (!mounted.current || !safeRef(ref)) return false;
    if (location.current.ref === ref) return true;
    if (!confirmDiscard(origin)) return false;
    const previous = { ...snapshot(), opener: { kind, ref } };
    if (onNavigation) {
      if (!onNavigation(previous, { ...previous, ref }, navigationHandle.current)) return false;
    } else noteTrail.current = [...noteTrail.current.slice(-39), previous];
    restoreFocus.current = null;
    open(ref);
    return true;
  }

  // Note content's wikilinks (below) call this directly; its identity has to stay stable because
  // `NoteMarkdown` is `memo(Markdown)` and this view re-renders on every unrelated keystroke (the
  // search box) while a note stays open beside it in the split layout. Depending on `note` would
  // defeat that memo the moment a note opens, so the handler lives behind a ref, reassigned every
  // render, and the callback handed to `Markdown` only ever forwards through it.
  const openWikilink = useRef((_target: string) => {});
  openWikilink.current = (target: string) => {
    const link = note?.links.find(
      (candidate) =>
        candidate.target === target &&
        (candidate.state === "resolved" || candidate.state === "ambiguous") &&
        candidate.candidates.length > 0,
    );
    const first = link?.candidates[0];
    if (first) requestOpen(first, "link");
  };
  const onOpenNoteLink = useCallback((target: string) => openWikilink.current(target), []);

  function backNote(): void {
    if (onNavigation) {
      onBack?.();
      return;
    }
    if (!confirmDiscard()) return;
    const target = noteTrail.current.at(-1);
    if (!target) {
      closeNote();
      setFocusHeading(true);
      return;
    }
    noteTrail.current = noteTrail.current.slice(0, -1);
    if (target.query !== query)
      restorePageFor.current = { api, key: `${workspaceId}\0${target.query}` };
    const targetSection = sectionKeyOf(target.section);
    const targetView =
      viewOf(target.view) === "section" && !targetSection ? "notes" : (viewOf(target.view) ?? view);
    const targetTag = tagOf(target.tag) ?? undefined;
    location.current = withTag(
      withSection({ ...target, view: targetView }, targetSection),
      targetTag,
    );
    setPage(pageIndex(target.page));
    setQuery(target.query);
    setFolder(target.folder);
    setSort(target.sort);
    setOnlyMarked(target.onlyMarked);
    setView(targetView);
    setSection(targetSection);
    setTag(targetTag ?? null);
    restoreFocus.current = target;
    if (target.ref === null) closeNote();
    else open(target.ref);
  }

  const openFromApp = (ref: string, origin?: HTMLElement) => requestOpen(ref, "row", origin);
  const navigationHandle = useRef<VaultNavigationHandle>({ snapshot, open: openFromApp, canLeave });
  Object.assign(navigationHandle.current, { snapshot, open: openFromApp, canLeave });
  useLayoutEffect(() => {
    void api;
    void workspaceId;
    mounted.current = true;
    const release = onNavigationReady?.(navigationHandle.current);
    return () => {
      mounted.current = false;
      release?.();
    };
  }, [onNavigationReady, api, workspaceId]);
  useEffect(() => {
    const target = initialRestore.current;
    initialRestore.current = null;
    if (!target || target.workspaceId !== workspaceId) return;
    location.current = withTag({ ...target, view }, tag ?? undefined);
    setPage(pageIndex(target.page));
    restoreFocus.current = target;
    if (target.ref !== null) open(target.ref);
  });

  const ownedList = listOwner?.api === api && listOwner.key === listKey;
  const visibleRows = ownedList ? rows : [];
  const visibleListing = ownedList ? listing : emptyCoverage("loading");
  // The folder, sort and bookmark controls work over the plain listing only; a search keeps its
  // own hit order. `visibleListing` (the partial-state line) is never derived from `shownRows`.
  const searching = query.trim() !== "";
  const markedRefs = marks?.api === api && marks.workspaceId === workspaceId ? marks.refs : null;
  // The overview counts only for the api and workspace it was read for; until its effect has run
  // for these, it is still loading (or absent for an api without the route).
  const overviewOwned = overviewState.api === api && overviewState.workspaceId === workspaceId;
  const overviewPhase = overviewOwned ? overviewState.phase : api.overview ? "loading" : "none";
  const overviewData = overviewOwned ? overviewState.data : null;
  // Held so a dataless load that reaches 300 ms doesn't flash the skeleton back off before it
  // has shown for at least MIN_VISIBLE_MS; a load answered before 300 ms, or already past the
  // hold window, drops at once (Loading.tsx `useHeld`).
  const overviewWaiting = useHeld(overviewPhase === "loading" && overviewData === null);
  // Held the same way: a refresh of an already-shown overview that answers past 300 ms must
  // not drop "Updating the overview…" before 700 ms (that is the very flash the kit exists to
  // prevent), and one answered before 300 ms must not show it at all.
  const overviewRefreshing = useHeld(overviewPhase === "loading" && overviewData !== null);
  const overviewCount = overviewData?.notes.length ?? 0;
  const overviewRows = useMemo(
    () => new Map((overviewData?.notes ?? []).map((row) => [row.ref, row])),
    [overviewData],
  );
  const sectionListing = useMemo<VaultEntry[]>(
    () =>
      visibleRows.map((row) => ({
        ref: row.ref,
        title: row.title,
        bytes: 0,
        modified: row.modified ?? 0,
      })),
    [visibleRows],
  );
  const otherGroups = useMemo(
    () => otherFolders(sectionListing, visibleListing.phase === "partial"),
    [sectionListing, visibleListing.phase],
  );
  const listingReady = visibleListing.phase === "ready" || visibleListing.phase === "partial";
  // The tab badges count listed files per section folder, as the section pages do.
  const sectionCounts = useMemo(() => {
    const counts = { knowledge: 0, sources: 0, maps: 0 };
    for (const row of visibleRows) {
      if (row.ref.startsWith("30-knowledge/")) counts.knowledge += 1;
      else if (row.ref.startsWith("40-sources/")) counts.sources += 1;
      else if (row.ref.startsWith("50-maps/")) counts.maps += 1;
    }
    return counts;
  }, [visibleRows]);
  const unavailableOverview =
    overviewPhase === "none" ||
    (overviewPhase === "error" && overviewData === null) ||
    (overviewPhase !== "loading" && overviewCount === 0);
  let effectiveView: View = searching ? "notes" : view;
  if (effectiveView === "section" && section === null) effectiveView = "notes";
  if ((effectiveView === "overview" || effectiveView === "section") && unavailableOverview)
    effectiveView = "notes";
  if (
    effectiveView === "section" &&
    section === "else" &&
    visibleListing.phase !== "loading" &&
    otherGroups.length === 0
  )
    effectiveView = overviewCount > 0 ? "overview" : "notes";
  // Keep the focused filter mounted until the user leaves it.
  if (folderControlFocused && view !== "notes") effectiveView = "notes";
  const folders = useMemo(
    () => [...new Set(visibleRows.flatMap((row) => folderOf(row.ref) ?? []))].sort(),
    [visibleRows],
  );
  const activeFolder = folders.includes(folder) ? folder : "";
  const shownRows = useMemo(() => {
    if (searching) return visibleRows;
    const kept = visibleRows.filter(
      (row) =>
        (activeFolder === "" || folderOf(row.ref) === activeFolder) &&
        (!onlyMarked || markedRefs?.has(row.ref)) &&
        (!tag || overviewRows.get(row.ref)?.tags?.includes(tag)),
    );
    return sort === "recent"
      ? kept.sort((a, b) => (b.modified ?? 0) - (a.modified ?? 0))
      : kept.sort((a, b) => {
          const [left, right] = [a.ref.toLowerCase(), b.ref.toLowerCase()];
          return left < right ? -1 : left > right ? 1 : 0;
        });
  }, [searching, visibleRows, activeFolder, onlyMarked, markedRefs, sort, tag, overviewRows]);
  // A restored tag depends on the overview's per-note tags; while that first read is still in
  // flight every row looks untagged, so paging waits rather than clamping to an empty, filtered
  // page before the overview has had a chance to say otherwise.
  const tagWaits =
    tag !== null && !searching && overviewPhase === "loading" && overviewData === null;
  const pagingReady =
    ownedList &&
    !tagWaits &&
    visibleListing.phase !== "loading" &&
    (searching || !onlyMarked || markedRefs !== null || markError !== "");
  const lastPage = Math.max(0, Math.ceil(shownRows.length / PAGE_SIZE) - 1);
  const currentPage = pagingReady ? Math.min(page, lastPage) : page;
  const pageStart = currentPage * PAGE_SIZE;
  const pagedRows = shownRows.slice(pageStart, pageStart + PAGE_SIZE);
  const mutationOwner = useMemo(
    () => ({
      api,
      workspaceId,
      writeApi,
      writePolicyRevision,
      writeRootPhysicalId,
      opsApi,
      recoveryOpsApi,
      proposalApi,
      memoryApi,
    }),
    [
      api,
      workspaceId,
      writeApi,
      writePolicyRevision,
      writeRootPhysicalId,
      opsApi,
      recoveryOpsApi,
      proposalApi,
      memoryApi,
    ],
  );
  const currentMutationOwner = useRef(mutationOwner);
  currentMutationOwner.current = mutationOwner;
  useEffect(() => {
    if (!pagingReady || page === currentPage) return;
    location.current = { ...location.current, page: currentPage };
    setPage(currentPage);
  });
  function changePage(next: number): void {
    cancelInitialSection();
    restoreFocus.current = null;
    restorePageFor.current = null;
    location.current = { ...location.current, page: next };
    setPage(next);
  }
  function cancelInitialSection(): void {
    initialSectionPending.current = false;
  }
  function changeView(next: View): void {
    cancelInitialSection();
    restoreFocus.current = null;
    location.current = {
      ...location.current,
      view: next,
      ...(next === "section" && section ? { section } : {}),
    };
    setView(next);
  }
  function changeSection(next: VaultSectionKey): void {
    cancelInitialSection();
    restoreFocus.current = null;
    location.current = { ...location.current, view: "section", section: next };
    setSection(next);
    setView("section");
  }
  // The overview's Browse inbox: All notes on that folder. The overview, and the button with it,
  // unmounts, so focus goes to the heading.
  function browseFolder(next: string): void {
    location.current = { ...location.current, folder: next };
    changePage(0);
    changeView("notes");
    setFolder(next);
    setFocusHeading(true);
  }
  // The Topics section's tag chips: the same jump into a filtered All notes, scoped to that tag
  // instead of a folder.
  function filterByTag(next: string): void {
    location.current = { ...location.current, tag: next, folder: "" };
    changePage(0);
    changeView("notes");
    setFolder("");
    setTag(next);
    setFocusHeading(true);
  }
  function clearTag(): void {
    cancelInitialSection();
    location.current = withTag(location.current, undefined);
    changePage(0);
    setTag(null);
  }
  useEffect(() => {
    if (!initialSectionPending.current || searching || !listingReady || overviewCount === 0) return;
    initialSectionPending.current = false;
    const target: VaultSectionKey =
      initialFolder === "30-knowledge"
        ? "knowledge"
        : initialFolder === "40-sources"
          ? "sources"
          : "maps";
    location.current = { ...location.current, view: "section", section: target };
    setSection(target);
    setView("section");
  }, [initialFolder, listingReady, overviewCount, searching]);
  const selected =
    selection?.api === api && selection.workspaceId === workspaceId ? selection.ref : null;

  useEffect(() => {
    const target = restoreFocus.current;
    if (!target || target.workspaceId !== workspaceId || !ownedList) return;
    if (target.onlyMarked && !searching && !markedRefs && !markError) return;
    if (
      (target.opener?.kind === "row" ||
        target.opener?.kind === "lead" ||
        target.opener?.kind === "recent" ||
        target.opener?.kind === "rediscover" ||
        target.opener?.kind === "map" ||
        target.opener?.kind === "bookmark") &&
      (visibleListing.phase === "loading" || tagWaits)
    )
      return;
    if (target.opener?.kind === "backlink" && backlinkState.phase === "loading") return;
    // Overview openers wait only for a dataless load (no remembered overview to show yet); a
    // row opener waits too while that still decides whether the list is shown at all. A load
    // that already has data to show (remembered, or a refresh) never blocks focus restore here.
    if (
      overviewPhase === "loading" &&
      overviewData === null &&
      (target.opener?.kind === "lead" ||
        target.opener?.kind === "recent" ||
        target.opener?.kind === "rediscover" ||
        target.opener?.kind === "map" ||
        target.opener?.kind === "bookmark" ||
        (target.opener?.kind === "row" && effectiveView !== "notes"))
    )
      return;
    if (target.ref === null ? visibleListing.phase === "loading" : noteState.phase === "loading")
      return;
    restoreFocus.current = null;
    const opener = target.opener;
    const control = Array.from(
      browserRef.current?.querySelectorAll<HTMLButtonElement>("[data-vault-ref]") ?? [],
    ).find(
      (element) =>
        element.dataset.vaultRef === opener?.ref && element.dataset.vaultKind === opener?.kind,
    );
    (control ?? headingRef.current)?.focus({ preventScroll: true });
    const region = browserRef.current?.closest<HTMLElement>('[data-region="view"]');
    if (region) {
      region.scrollTop = target.scroll.top;
      region.scrollLeft = target.scroll.left;
    }
  });

  // Consumed by the first finished listing, found or not, so a later listing (a search, a refresh
  // after a create) never opens the note behind the user's back.
  const refPending = useRef(!initialRestore.current && initialRef !== undefined);
  useEffect(() => {
    if (!refPending.current || !ownedList) return;
    if (visibleListing.phase !== "ready" && visibleListing.phase !== "partial") return;
    refPending.current = false;
    if (!initialRef || !visibleRows.some((row) => row.ref === initialRef)) return;
    location.current = { ...location.current, folder: folderOf(initialRef) ?? "" };
    setFolder(folderOf(initialRef) ?? "");
    open(initialRef);
  });

  const splitReady = selected !== null && (effectiveView === "overview" || shownRows.length > 0);
  const [splitBox, setSplitBox] = useState({ width: 0, top: 0, height: 0, wide: false });
  const [splitWanted, setSplitWanted] = useState<number | null>(null);
  const [splitPreview, setSplitPreview] = useState<number | null>(null);
  const splitDrag = useRef<{ target: HTMLElement; id: number } | null>(null);
  const splitHost = useRef<HTMLDivElement | null>(null);
  const bindSplitHost = useCallback((node: HTMLDivElement | null) => {
    if (!node && splitHost.current?.contains(document.activeElement)) headingRef.current?.focus();
    splitHost.current = node;
  }, []);
  useLayoutEffect(() => {
    void splitReady;
    const host = browserRef.current;
    if (!host) return;
    const pane = host.querySelector<HTMLElement>(".vault-inspector");
    let active = true;
    const measure = () => {
      if (!active) return;
      const next = {
        width: host.clientWidth,
        top: pane?.offsetTop ?? 0,
        height: pane?.clientHeight ?? 0,
        wide: window.innerWidth >= 800,
      };
      setSplitBox((old) =>
        old.width === next.width &&
        old.top === next.top &&
        old.height === next.height &&
        old.wide === next.wide
          ? old
          : next,
      );
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(host);
    if (pane) observer?.observe(pane);
    window.addEventListener("resize", measure);
    return () => {
      active = false;
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [splitReady]);
  const splitShown = splitReady && splitBox.wide && splitBox.width >= 488 && splitBox.height > 0;
  const splitMaximum = Math.max(240, Math.floor(splitBox.width - 248));
  const splitNatural = clampResize((splitBox.width - 8) / 3, 240, splitMaximum);
  const splitValue = clampResize(splitPreview ?? splitWanted ?? splitNatural, 240, splitMaximum);
  useLayoutEffect(() => {
    if (!splitShown) {
      splitDrag.current = null;
      setSplitPreview(null);
    }
  }, [splitShown]);
  function clearSplitPreview() {
    splitDrag.current = null;
    setSplitPreview(null);
  }
  function resetSplit() {
    const drag = splitDrag.current;
    if (drag?.target.hasPointerCapture(drag.id)) drag.target.releasePointerCapture(drag.id);
    setSplitPreview(null);
    setSplitWanted(null);
  }

  // Shared by the h2 and `readerBody`: the body's own first line drops only when it duplicates
  // the title already shown above it.
  const readerTitle = note ? (note.displayMetadata?.display_title ?? displayTitle(note)) : "";
  const readerOverview = note ? overviewRows.get(note.ref) : undefined;
  const readerListing = note ? visibleRows.find((row) => row.ref === note.ref) : undefined;
  const addedDate =
    readerOverview?.created &&
    (!readerOverview.modified || readerOverview.created >= readerOverview.modified);
  const authoredDate = addedDate ? readerOverview?.created : readerOverview?.modified;
  const changedTime = readerListing?.modified;
  const changedValue = changedTime !== undefined ? new Date(changedTime * 1000) : null;
  const changedDate =
    changedTime !== undefined &&
    changedTime > 0 &&
    changedValue &&
    !Number.isNaN(changedValue.getTime())
      ? changedValue.toISOString().slice(0, 10)
      : null;
  const date = authoredDate ?? changedDate;
  const dateLabel = authoredDate
    ? tf(addedDate ? "vault.time.added" : "vault.time.updated", { date: authoredDate })
    : changedDate
      ? tf("vault.time.changed", { date: changedDate })
      : null;
  const relatedListing = useMemo(
    () => new Map(sectionListing.map((row) => [row.ref, row])),
    [sectionListing],
  );
  // Recomputed only when the note's own text or links change, or the title the stripped body
  // depends on — never on an unrelated re-render (every search keystroke), since `linkWikilinks`
  // walks the whole body. Gated on the rewrite's own size, not the body's: the rewrite grows
  // the byte count, so a body just under Markdown's render cap can still cross it. Above
  // that cap the note shows as plain text anyway, so the stripped source is shown as written,
  // rather than its rewritten form (`[T](viki-note:T)`, `\_`) with nothing left to parse it.
  // biome-ignore lint/correctness/useExhaustiveDependencies: text/links are the deliberately narrower deps; note itself would also fire this on a same-content refetch.
  const readerText = useMemo(() => {
    if (!note) return "";
    const body = readerBody(note.text, readerTitle);
    const linked = linkWikilinks(body, (target) =>
      note.links.some(
        (link) =>
          link.target === target &&
          (link.state === "resolved" || link.state === "ambiguous") &&
          link.candidates.length > 0,
      ),
    );
    return new TextEncoder().encode(linked).byteLength > MAX_MARKDOWN_BYTES ? body : linked;
  }, [note?.text, note?.links, readerTitle]);
  const editingThisNote = draft?.mode === "edit" && note !== null && draft.ref === note.ref;
  // Gates the Attach excerpt action: a route to preview lines, a place to send the result, and at
  // least one line to excerpt.
  const excerptReady = Boolean(note && onAttachExcerpt && api.excerpt && lineCount(note.text) > 0);
  return (
    <section
      ref={browserRef}
      aria-label="Vault browser"
      className="vault-browser"
      data-split={splitShown || undefined}
      style={
        splitShown && (splitPreview !== null || splitWanted !== null)
          ? ({ "--vault-list-width": `${splitValue}px` } as CSSProperties)
          : undefined
      }
      data-open={selected ? "" : undefined}
      data-view={effectiveView}
    >
      {onBack
        ? (renderBack?.(onBack) ?? (
            <button type="button" className="btn txt sm" onClick={onBack}>
              {t("shell.nav.back_to_chat")}
            </button>
          ))
        : null}
      <header className="page-head vault-head">
        <div className="vault-head-title">
          <h1 ref={headingRef} tabIndex={-1} className="page-title">
            Vault
          </h1>
          <p className="page-intro">
            {effectiveView === "overview"
              ? "Recent notes, your inbox and how your knowledge grows."
              : "Browse your notes and their context."}
          </p>
        </div>
        <label className="vault-search">
          <svg
            className="ico"
            width="16"
            height="16"
            viewBox="0 0 24 24"
            aria-hidden="true"
            focusable="false"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            aria-label="Search note contents"
            maxLength={256}
            placeholder="Search note contents"
            value={query}
            onChange={(event) => {
              cancelInitialSection();
              location.current = { ...location.current, query: event.target.value.slice(0, 256) };
              changePage(0);
              setQuery(location.current.query);
            }}
          />
        </label>
      </header>
      {(!searching && overviewCount > 0) || write ? (
        <div className="vault-toolbar">
          {!searching && overviewCount > 0 ? (
            <fieldset aria-label="Vault view" className="vault-views">
              <button
                type="button"
                className="vault-tab"
                aria-current={effectiveView === "overview" ? "page" : undefined}
                aria-pressed={effectiveView === "overview"}
                onClick={() => {
                  clearTag();
                  changeView("overview");
                }}
              >
                {t("vault.nav.front")}
              </button>
              {(["knowledge", "sources", "maps"] as const).map((key) => (
                <button
                  key={key}
                  type="button"
                  className="vault-tab"
                  aria-current={effectiveView === "section" && section === key ? "page" : undefined}
                  aria-pressed={effectiveView === "section" && section === key}
                  onClick={() => changeSection(key)}
                >
                  {t(`vault.nav.${key}`)}
                  {listingReady ? (
                    <span className="vault-tab-count" aria-hidden="true">
                      {`${sectionCounts[key]}${visibleListing.phase === "partial" ? "+" : ""}`}
                    </span>
                  ) : null}
                </button>
              ))}
              {otherGroups.length > 0 ? (
                <button
                  type="button"
                  className="vault-tab"
                  aria-current={
                    effectiveView === "section" && section === "else" ? "page" : undefined
                  }
                  aria-pressed={effectiveView === "section" && section === "else"}
                  onClick={() => changeSection("else")}
                >
                  {t("vault.nav.else")}
                </button>
              ) : null}
              <button
                type="button"
                className="vault-tab"
                aria-current={effectiveView === "notes" ? "page" : undefined}
                aria-pressed={effectiveView === "notes"}
                onClick={() => changeView("notes")}
              >
                {t("vault.nav.all")}
                {listingReady ? (
                  <span className="vault-tab-count" aria-hidden="true">
                    {`${visibleRows.length}${visibleListing.phase === "partial" ? "+" : ""}`}
                  </span>
                ) : null}
              </button>
            </fieldset>
          ) : null}
          {write ? (
            <button
              type="button"
              className="btn alt sm vault-new-note"
              onClick={() => {
                if (!confirmDiscard()) return;
                ++saveGeneration.current;
                setDraft({ mode: "create", ref: "", content: "" });
                setSaveState("idle");
                setSaveMessage("");
              }}
            >
              New note
            </button>
          ) : null}
        </div>
      ) : null}
      {recoveryCount > 0 ? (
        <p role="status" className="vault-notice">
          A save or note operation didn't finish.{" "}
          <button type="button" onClick={openRecovery}>
            Review recovery
          </button>
        </p>
      ) : null}
      {overviewPhase === "error" && overviewData === null ? (
        <p className="vault-quiet">
          The overview couldn't be read.{" "}
          <button
            type="button"
            onClick={() => {
              reloadOverview();
              // The line, and this button with it, leaves while the overview is read again.
              setFocusHeading(true);
            }}
          >
            Try again
          </button>
        </p>
      ) : null}
      {markError ? <p role="alert">{markError}</p> : null}
      {draft?.mode === "create" ? (
        <section aria-label="New note editor">
          <label>
            Note path
            <input
              value={draft.ref}
              readOnly={saveState === "saving"}
              maxLength={MAX_REF_CHARS}
              onChange={(event) => setDraft({ ...draft, ref: event.target.value })}
            />
          </label>
          <label>
            Markdown
            <textarea
              value={draft.content}
              readOnly={saveState === "saving"}
              onChange={(event) => setDraft({ ...draft, content: event.target.value })}
            />
          </label>
          {draft.ref !== "" && !validWriteRef(draft.ref) ? (
            <p>{createDisabledReason(draft.ref)}</p>
          ) : null}
          <button
            type="button"
            disabled={
              saveState === "saving" ||
              saveState === "checking" ||
              saveState === "recovery" ||
              !validWriteRef(draft.ref) ||
              Boolean(blockedWrites[draft.ref.toLowerCase()])
            }
            onClick={() => void save()}
          >
            <BusyLabel
              busy={saveState === "saving" || saveState === "checking"}
              failed={saveState === "error" || saveState === "stale" || saveState === "recovery"}
            >
              Create note
            </BusyLabel>
          </button>
          <button type="button" onClick={() => setDraft(null)}>
            Cancel
          </button>
        </section>
      ) : null}
      {saveMessage ? <p role={saveState === "saved" ? "status" : "alert"}>{saveMessage}</p> : null}
      {draft && blockedWrites[draft.ref.toLowerCase()] ? (
        <button type="button" onClick={() => void refreshRecovery()}>
          Check recovery status
        </button>
      ) : null}
      {saveState === "stale" && draft ? (
        <button
          type="button"
          onClick={() => {
            if (confirmDiscard()) open(draft.ref);
          }}
        >
          {PROPOSAL_COPY.reload}
        </button>
      ) : null}
      <div className="vault-panes">
        {effectiveView === "overview" ? (
          <div className="vault-list vault-overview-host" id={`${noteListId}-navigation`}>
            {overviewWaiting ? (
              <VaultOverviewSkeleton />
            ) : overviewData && overviewCount > 0 ? (
              <>
                {overviewRefreshing && overviewPhase !== "error" ? (
                  <Loading className="vault-quiet vault-updating">Updating the overview…</Loading>
                ) : null}
                {overviewPhase === "error" ? (
                  <p className="vault-quiet">
                    This overview may be out of date: it couldn't be read again.{" "}
                    <button
                      type="button"
                      onClick={() => {
                        reloadOverview();
                        // The line, and this button with it, leaves while the overview is read again.
                        setFocusHeading(true);
                      }}
                    >
                      Try again
                    </button>
                  </p>
                ) : null}
                <VaultOverview
                  overview={overviewData}
                  today={new Date()}
                  workspaceId={workspaceId}
                  listing={sectionListing}
                  listingReady={listingReady}
                  listingPartial={visibleListing.phase === "partial"}
                  bookmarks={
                    marks?.api === api && marks.workspaceId === workspaceId ? marks.items : []
                  }
                  inboxCount={visibleRows.filter((row) => row.ref.startsWith("00-inbox/")).length}
                  // The title button is no modal origin: `requestOpen` would refuse it (`canLeave`).
                  onOpen={(ref, kind) => {
                    requestOpen(ref, kind);
                  }}
                  onOpenCard={(ref, kind) => requestOpen(ref, kind)}
                  onTag={filterByTag}
                  onBrowse={browseFolder}
                  onReload={reloadOverview}
                  {...(captures ? { captures } : {})}
                  {...(onTriage ? { onTriage } : {})}
                />
              </>
            ) : (
              <p role="status">Reading your notes…</p>
            )}
          </div>
        ) : effectiveView === "section" && section ? (
          <div className="vault-list vault-section-host" id={`${noteListId}-navigation`}>
            <VaultSectionPage
              selected={selected}
              section={section}
              listing={visibleRows}
              overview={overviewData}
              listingPartial={visibleListing.phase === "partial"}
              // The rows are empty on every reload and after a failed read, which is not "no notes".
              listingReady={listingReady}
              listingStatus={<State value={visibleListing} />}
              onOpen={(ref, kind) => requestOpen(ref, kind)}
              onBrowse={browseFolder}
            />
          </div>
        ) : (
          <div className="vault-browse">
            {searching ? (
              <p aria-live="polite" className="vault-quiet">
                Searching all readable note contents. Saved browsing filters: folder{" "}
                {folder ? `"${folder}"` : "all folders"};{" "}
                {onlyMarked ? "bookmarks only" : "all notes"};{" "}
                {sort === "recent" ? "most recent first" : "name order"}
                {tag ? `; tag "#${tag}"` : ""}.
              </p>
            ) : null}
            {!searching && visibleRows.length > 0 ? (
              <fieldset className="vault-filters">
                <legend>Filter and sort</legend>
                <label>
                  Folder{" "}
                  <select
                    value={activeFolder}
                    onFocus={() => setFolderControlFocused(true)}
                    onBlur={() => setFolderControlFocused(false)}
                    onChange={(event) => {
                      cancelInitialSection();
                      location.current = { ...location.current, folder: event.target.value };
                      changePage(0);
                      setFolder(event.target.value);
                    }}
                  >
                    <option value="">All</option>
                    {folders.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Sort{" "}
                  <select
                    value={sort}
                    onChange={(event) => {
                      cancelInitialSection();
                      location.current = { ...location.current, sort: event.target.value as Sort };
                      changePage(0);
                      setSort(location.current.sort);
                    }}
                  >
                    <option value="name">Name</option>
                    <option value="recent">Last modified</option>
                  </select>
                </label>
                <label className="vault-bookmark-filter">
                  <input
                    type="checkbox"
                    checked={onlyMarked}
                    onChange={(event) => {
                      cancelInitialSection();
                      location.current = { ...location.current, onlyMarked: event.target.checked };
                      changePage(0);
                      setOnlyMarked(event.target.checked);
                    }}
                  />{" "}
                  Bookmarks only
                </label>
                {tag ? (
                  <p className="vault-tag-filter">
                    {`Tagged #${tag}`}{" "}
                    <button type="button" aria-label="Clear tag filter" onClick={clearTag}>
                      ×
                    </button>
                  </p>
                ) : null}
                {tag ? (
                  <p className="vault-quiet">
                    {t("vault.coverage.source")}
                    {overviewData?.partial ? ` · ${t("vault.coverage.partial")}` : ""}
                  </p>
                ) : null}
              </fieldset>
            ) : null}
            <State value={visibleListing} />
            {!searching &&
            folder !== "" &&
            activeFolder === "" &&
            (visibleListing.phase === "ready" || visibleListing.phase === "partial") ? (
              <p>
                Folder "{folder}" was not found in this{" "}
                {visibleListing.phase === "partial" ? "partial listing" : "listing"}. Showing all
                folders.
              </p>
            ) : null}
            {visibleListing.phase !== "loading" &&
            visibleListing.phase !== "error" &&
            visibleRows.length === 0 ? (
              <p>
                {visibleListing.phase === "partial"
                  ? "No notes found in the scanned results."
                  : "No notes found."}
              </p>
            ) : null}
            {visibleRows.length > 0 && shownRows.length === 0 && !tagWaits ? (
              <p>No notes match these filters.</p>
            ) : null}
            {shownRows.length > 0 ? (
              <nav aria-label="Notes" className="vault-list" id={`${noteListId}-navigation`}>
                {pagedRows.map((row, localIndex) => {
                  const index = pageStart + localIndex;
                  const marked = markedRefs?.has(row.ref) ?? false;
                  const known = overviewRows.get(row.ref);
                  const label = known?.title ?? displayTitle(row);
                  return (
                    <div key={row.ref} className="vault-row">
                      <svg
                        className="ico vault-note-icon"
                        width="20"
                        height="20"
                        viewBox="0 0 24 24"
                        aria-hidden="true"
                        focusable="false"
                      >
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                        <path d="M14 2v6h6M8 12h8M8 16h6" />
                      </svg>
                      <div className="vault-row-label">
                        <button
                          type="button"
                          data-vault-ref={row.ref}
                          data-vault-kind="row"
                          aria-current={selected === row.ref || undefined}
                          aria-describedby={`${noteListId}-${index}`}
                          onClick={() => {
                            requestOpen(row.ref);
                          }}
                        >
                          {label}
                          {row.excerpt !== undefined ? ` — line ${row.line}: ${row.excerpt}` : ""}
                        </button>
                        {known ? (
                          <p className="vault-row-meta">
                            {[sectionOf(row.ref), known.status].filter(Boolean).join(" · ")}
                          </p>
                        ) : null}
                        {known?.summary ? (
                          <p className="vault-row-summary">{known.summary}</p>
                        ) : null}
                        <small id={`${noteListId}-${index}`} className="vault-ref">
                          {row.ref}
                        </small>
                      </div>
                      {searching ? null : (
                        <button
                          type="button"
                          className="vault-bookmark"
                          aria-pressed={marked}
                          aria-label={`${marked ? "Remove bookmark for" : "Add bookmark for"} ${row.ref}`}
                          disabled={marking}
                          onClick={() => void toggleMark(row.ref, marked)}
                        >
                          {marked ? "★" : "☆"}
                        </button>
                      )}
                    </div>
                  );
                })}
              </nav>
            ) : null}
            {pagingReady &&
            lastPage > 0 &&
            (visibleListing.phase === "ready" || visibleListing.phase === "partial") ? (
              <fieldset aria-label="Note pages" className="vault-pages">
                <p aria-live="polite">
                  Page {currentPage + 1} of {lastPage + 1}
                </p>
                <button
                  type="button"
                  aria-label="Previous note page"
                  disabled={currentPage === 0}
                  onClick={() => changePage(currentPage - 1)}
                >
                  Previous
                </button>
                <button
                  type="button"
                  aria-label="Next note page"
                  disabled={currentPage >= lastPage}
                  onClick={() => changePage(currentPage + 1)}
                >
                  Next
                </button>
              </fieldset>
            ) : null}
          </div>
        )}
        {selected ? (
          <article aria-label="Selected note" className="vault-inspector">
            {!onNavigation && (
              <button type="button" className="btn txt sm vault-back" onClick={backNote}>
                {noteTrail.current.at(-1)?.ref ? "Back" : "Back to notes"}
              </button>
            )}
            <State value={noteState} />
            {note ? (
              <>
                <header className="vault-reader-head">
                  <NoteChips
                    section={overviewRows.has(note.ref) ? sectionOf(note.ref) : folderOf(note.ref)}
                    type={note.displayMetadata?.type}
                    status={note.displayMetadata?.status}
                    confidence={overviewRows.get(note.ref)?.confidence}
                    tags={note.displayMetadata?.tags ?? undefined}
                    {...(sectionOf(note.ref) !== null && overviewRows.has(note.ref)
                      ? { onTag: filterByTag }
                      : {})}
                  />
                  <h2>{readerTitle}</h2>
                  {date && dateLabel ? (
                    <p className="vault-reader-date">
                      <time dateTime={date}>{dateLabel}</time>
                    </p>
                  ) : null}
                  <p className="vault-ref">{note.ref}</p>
                  {isBotMemoryRef(note.ref) ? (
                    <p className="vault-quiet">{t("vault.reader.bot_memory_read_only")}</p>
                  ) : null}
                  {note.displayMetadata?.state === "unsupported" ||
                  note.displayMetadata?.state === "truncated" ||
                  (!note.displayMetadata && note.frontmatterError) ? (
                    <p className="vault-quiet">
                      Some details in this note's header could not be read.
                    </p>
                  ) : null}
                  <div className="vault-reader-actions">
                    <button
                      type="button"
                      className="vault-reader-bookmark"
                      aria-label={`${t(markedRefs?.has(note.ref) ? "vault.reader.remove_bookmark" : "vault.reader.bookmark")} ${note.ref}`}
                      aria-pressed={markedRefs?.has(note.ref) ?? false}
                      aria-describedby={`${noteListId}-bookmark-hint`}
                      disabled={marking || markedRefs === null}
                      onClick={() => void toggleMark(note.ref, markedRefs?.has(note.ref) ?? false)}
                    >
                      {markedRefs?.has(note.ref) ? "★" : "☆"}{" "}
                      {t(
                        markedRefs?.has(note.ref)
                          ? "vault.reader.remove_bookmark"
                          : "vault.reader.bookmark",
                      )}
                    </button>
                    {editingThisNote ? null : write || proposalApi ? (
                      validWriteRef(note.ref) ? (
                        <button
                          type="button"
                          onClick={() => {
                            setDraft({ mode: "edit", ref: note.ref, content: note.source });
                            setSaveState("idle");
                            setSaveMessage("");
                            // Only Save takes a lock; a proposal has nothing to wait for.
                            const blocked = write
                              ? blockedWrites[note.ref.toLowerCase()]
                              : undefined;
                            if (blocked) {
                              setSaveState(blocked.state);
                              setSaveMessage(
                                "Recovery must clear before this note can be saved again.",
                              );
                            }
                          }}
                        >
                          Edit note
                        </button>
                      ) : isBotMemoryRef(note.ref) ? null : (
                        <p>This file is not edited in the app.</p>
                      )
                    ) : null}
                    {!dirty && (write?.ops || excerptReady) ? (
                      <NoteActions
                        workspaceId={workspaceId}
                        noteRef={note.ref}
                        noteSha256={note.sha256}
                        revision={
                          write
                            ? {
                                policyRevision: write.policyRevision,
                                rootPhysicalId: write.rootPhysicalId,
                              }
                            : { policyRevision: 0, rootPhysicalId: "" }
                        }
                        api={validWriteRef(note.ref) ? write?.ops : undefined}
                        {...(excerptReady
                          ? {
                              extra: [
                                {
                                  label: EXCERPT_COPY.open,
                                  onSelect: () => setExcerptRef(note.ref),
                                },
                              ],
                            }
                          : {})}
                        onPending={beginPending}
                        onDone={noteChanged}
                        onRefresh={refreshAfterOperation}
                      />
                    ) : null}
                  </div>
                  <p id={`${noteListId}-bookmark-hint`} className="vault-reader-hint" hidden>
                    {t("vault.reader.bookmark_hint")}
                  </p>
                </header>
                {editingThisNote ? (
                  <section aria-label="Note editor">
                    <label>
                      Markdown
                      <textarea
                        value={draft.content}
                        readOnly={saveState === "saving"}
                        onChange={(event) => setDraft({ ...draft, content: event.target.value })}
                      />
                    </label>
                    {write ? (
                      <button
                        type="button"
                        disabled={
                          saveState === "saving" ||
                          saveState === "checking" ||
                          saveState === "recovery" ||
                          Boolean(blockedWrites[draft.ref.toLowerCase()])
                        }
                        onClick={() => void save()}
                      >
                        <BusyLabel
                          busy={saveState === "saving" || saveState === "checking"}
                          failed={
                            saveState === "error" ||
                            saveState === "stale" ||
                            saveState === "recovery"
                          }
                        >
                          Save note
                        </BusyLabel>
                      </button>
                    ) : proposalApi ? (
                      <button
                        type="button"
                        disabled={saveState === "saving"}
                        onClick={() => void propose()}
                      >
                        <BusyLabel
                          busy={saveState === "saving" || saveState === "checking"}
                          failed={
                            saveState === "error" ||
                            saveState === "stale" ||
                            saveState === "recovery"
                          }
                        >
                          {PROPOSAL_COPY.propose}
                        </BusyLabel>
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => {
                        ++saveGeneration.current;
                        setDraft(null);
                        setSaveState("idle");
                        setSaveMessage("");
                      }}
                    >
                      Cancel
                    </button>
                  </section>
                ) : (
                  <section aria-label="Note content" className="vault-note-content">
                    <NoteMarkdown
                      text={readerText}
                      onOpenUrl={onOpenUrl}
                      onOpenNoteLink={onOpenNoteLink}
                    />
                  </section>
                )}
                {onAttachExcerpt && api.excerpt && excerptRef === note.ref ? (
                  <ExcerptDialog
                    api={api as ExcerptApi}
                    workspaceId={workspaceId}
                    noteRef={note.ref}
                    lines={lineCount(note.text)}
                    onAttach={onAttachExcerpt}
                    onClose={() => setExcerptRef(null)}
                  />
                ) : null}
              </>
            ) : null}
            <RelatedNotes
              links={note?.links ?? []}
              backlinks={backlinks}
              backlinkPhase={backlinkState.phase}
              backlinkStatus={<State value={backlinkState} />}
              listing={relatedListing}
              overview={overviewRows}
              onOpen={(ref, kind) => {
                requestOpen(ref, kind);
              }}
            />
            <section aria-label={HISTORY_COPY.heading} className="vault-history">
              <details>
                <summary>{HISTORY_COPY.heading}</summary>
                {history.phase === "loading" ? <Loading>{HISTORY_COPY.loading}</Loading> : null}
                {history.phase === "error" ? <p role="alert">{HISTORY_COPY.error}</p> : null}
                {history.phase === "unknown" ? <p>{HISTORY_COPY.unknown}</p> : null}
                {history.phase === "ready" && history.commits.length === 0 ? (
                  <p>{HISTORY_COPY.empty}</p>
                ) : null}
                {history.commits.length > 0 ? (
                  <ul>
                    {history.commits.map((commit) => (
                      <li key={commit.short} className="vault-commit">
                        <code>{commit.short}</code> <time>{commit.date}</time> {commit.subject}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </details>
            </section>
          </article>
        ) : effectiveView === "section" && section && section !== "else" ? (
          <VaultSectionInsight
            section={section}
            overview={overviewData}
            onOpen={(ref, kind) => requestOpen(ref, kind)}
            onTag={filterByTag}
          />
        ) : (effectiveView === "notes" && shownRows.length > 0) ||
          (effectiveView === "section" && section === "else") ? (
          <p className="vault-placeholder">Pick a note in the list to read it here.</p>
        ) : null}
      </div>
      {splitShown && (
        <div
          ref={bindSplitHost}
          className="vault-resize-controls"
          style={{ left: splitValue + 4, top: splitBox.top, height: splitBox.height }}
          onPointerDownCapture={(event) => {
            if (event.button !== 0 || !event.isPrimary || !(event.target instanceof HTMLElement))
              return;
            if (event.target.getAttribute("role") !== "separator") return;
            const drag = splitDrag.current;
            if (!drag?.target.hasPointerCapture(drag.id))
              splitDrag.current = { target: event.target, id: event.pointerId };
          }}
          onPointerUp={(event) => {
            const drag = splitDrag.current;
            if (
              drag &&
              event.isPrimary &&
              event.pointerId === drag.id &&
              !drag.target.hasPointerCapture(drag.id)
            )
              clearSplitPreview();
          }}
        >
          <ResizeHandle
            label="Resize Vault panes"
            controls={`${noteListId}-navigation`}
            orientation="vertical"
            growDirection={1}
            value={splitValue}
            min={240}
            max={splitMaximum}
            defaultValue={splitNatural}
            onPreview={(next) => {
              const drag = splitDrag.current;
              if (drag && !drag.target.hasPointerCapture(drag.id)) clearSplitPreview();
              else setSplitPreview(next);
            }}
            onCommit={(next, intent) => {
              clearSplitPreview();
              if (intent === "reset") resetSplit();
              else setSplitWanted(next);
            }}
          />
          <button
            type="button"
            className="vault-split-reset"
            aria-label="Reset Vault panes"
            onClick={resetSplit}
          >
            Reset
          </button>
        </div>
      )}
      {write?.ops ? (
        <details
          ref={recoveryDetailsRef}
          className="vault-more"
          open={recoveryCount > 0 || undefined}
        >
          <summary ref={recoverySummaryRef}>Recovery</summary>
          <RecoveryPanel
            api={write.ops}
            workspaceId={workspaceId}
            policyRevision={write.policyRevision}
            rootPhysicalId={write.rootPhysicalId}
            publications={publications}
            refreshKey={recoveryRefresh}
            onDone={refreshAfterOperation}
            onPending={beginPending}
            onAttention={setRecoveryCount}
          />
        </details>
      ) : recoveryOnly ? (
        <details
          ref={recoveryDetailsRef}
          className="vault-more"
          open={recoveryCount > 0 || undefined}
        >
          <summary ref={recoverySummaryRef}>Recovery</summary>
          <RecoveryPanel
            forgetOnly
            api={recoveryOnly.ops}
            workspaceId={workspaceId}
            publications={publications}
            refreshKey={recoveryRefresh}
            onDone={refreshAfterOperation}
            onPending={beginPending}
            onAttention={setRecoveryCount}
          />
        </details>
      ) : null}
      {memory ? (
        <details className="vault-more">
          <summary>Memory</summary>
          <MemoryPanel
            api={memory.api}
            workspaceId={workspaceId}
            revision={{
              policyRevision: memory.policyRevision,
              rootPhysicalId: memory.rootPhysicalId,
            }}
            protected={memory.protected}
            onDone={refreshAfterOperation}
            onPending={beginPending}
          />
        </details>
      ) : null}
    </section>
  );
}
