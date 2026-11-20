import type { SessionColor, SessionMetadata } from "../clients/conversations/sessionMetadataApi";
import type { SessionRow } from "../protocol";
import { groupSessions, rowTitle } from "./rows";

/** Pinned tabs first, then the rest; each group keeps its order in `ids`. */
export function orderTabs(ids: readonly string[], pinned: Iterable<string>): string[] {
  const pins = new Set(pinned);
  return [...ids.filter((id) => pins.has(id)), ...ids.filter((id) => !pins.has(id))];
}

/** Swap a tab with its neighbour inside its own group (pinned or not). */
export function moveTab(
  ids: readonly string[],
  id: string,
  delta: -1 | 1,
  pinned: Iterable<string>,
): string[] {
  const pins = new Set(pinned);
  const group = pins.has(id);
  const slots = ids.flatMap((item, index) => (pins.has(item) === group ? [index] : []));
  const at = slots.findIndex((index) => ids[index] === id);
  const from = slots[at];
  const to = slots[at + delta];
  const next = [...ids];
  if (at < 0 || from === undefined || to === undefined) return next;
  next[from] = ids[to] as string;
  next[to] = id;
  return next;
}

/** Move a tab before `beforeId` (null: to the end of its group), within its own group only. */
export function moveTabTo(
  ids: readonly string[],
  id: string,
  beforeId: string | null,
  pinned: Iterable<string>,
): string[] {
  const pins = new Set(pinned);
  const group = pins.has(id);
  const rest = ids.filter((item) => item !== id);
  if (rest.length === ids.length) return [...ids];
  let at: number;
  if (beforeId === null) {
    const last = rest.findLastIndex((item) => pins.has(item) === group);
    at = last >= 0 ? last + 1 : group ? 0 : rest.length;
  } else {
    at = rest.indexOf(beforeId);
    if (at < 0 || pins.has(beforeId) !== group) return [...ids];
  }
  rest.splice(at, 0, id);
  return rest;
}

export function sessionLabel(
  id: string,
  rows: readonly SessionRow[],
  metadata: readonly SessionMetadata[],
): string {
  const title = metadata.find((item) => item.session_id === id)?.title;
  const row = rows.find((item) => item.session_id === id);
  return title || (row ? rowTitle(row) : "Untitled session");
}

/** A label this app still holds for a session the current listing no longer has. */
export interface MissingRecord {
  session_id: string;
  title: string;
  pinned: boolean;
  archived: boolean;
  revision: number | null;
  unread?: boolean;
  color?: SessionColor | null;
}

/** Labels (pinned, titled, archived, unread or colored) absent from a *complete* listing and
 * not held open. Empty until the listing and the labels are both known, so a session merely not
 * loaded yet is never reported missing. */
export function missingRecords(input: {
  rows: readonly SessionRow[];
  metadata: readonly SessionMetadata[];
  held: ReadonlySet<string>;
  complete: boolean;
}): MissingRecord[] {
  if (!input.complete) return [];
  const rowIds = new Set(input.rows.map((row) => row.session_id));
  const byId = new Map(input.metadata.map((item) => [item.session_id, item]));
  const candidates = new Set<string>();
  for (const item of input.metadata)
    if (item.pinned || item.title || item.archived || item.unread || item.color)
      candidates.add(item.session_id);
  const records: MissingRecord[] = [];
  for (const sessionId of candidates) {
    if (rowIds.has(sessionId) || input.held.has(sessionId)) continue;
    const label = byId.get(sessionId);
    records.push({
      session_id: sessionId,
      title: label?.title ?? "",
      pinned: label?.pinned ?? false,
      archived: label?.archived ?? false,
      revision: label?.revision ?? null,
      ...(label?.unread !== undefined ? { unread: label.unread } : {}),
      ...(label?.color !== undefined ? { color: label.color } : {}),
    });
  }
  records.sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return a.session_id < b.session_id ? -1 : a.session_id > b.session_id ? 1 : 0;
  });
  return records;
}

/** A label that holds nothing the user wrote: just the Archive mark, with no title, pin, color
 * or unread flag of its own. Safe for the app to delete quietly once its transcript is gone. */
export function isArchiveOnly(
  label: Pick<SessionMetadata, "archived" | "title" | "pinned" | "unread" | "color">,
): boolean {
  return label.archived && !label.title && !label.pinned && !label.unread && !label.color;
}

export function organizedSessions(
  rows: readonly SessionRow[],
  metadata: readonly SessionMetadata[],
  now: number,
  archived: boolean,
) {
  const byId = new Map(metadata.map((item) => [item.session_id, item]));
  const shown = rows
    .filter((row) => Boolean(byId.get(row.session_id)?.archived) === archived)
    .map((row) => ({ ...row, title: byId.get(row.session_id)?.title || row.title }));
  const pinned = shown.filter((row) => byId.get(row.session_id)?.pinned);
  const grouped = groupSessions(
    shown.filter((row) => !byId.get(row.session_id)?.pinned),
    now,
  );
  if (pinned.length) grouped.groups.unshift({ label: "Pinned", rows: pinned });
  return grouped;
}
