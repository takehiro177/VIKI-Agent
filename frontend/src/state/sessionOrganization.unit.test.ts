import { expect, test } from "vitest";
import type { SessionMetadata } from "../clients/conversations/sessionMetadataApi";
import type { SessionRow } from "../protocol";
import {
  isArchiveOnly,
  type MissingRecord,
  missingRecords,
  moveTab,
  moveTabTo,
  orderTabs,
} from "./sessionOrganization";

const ROW_A = "11111111-1111-1111-1111-111111111111";
const ROW_B = "22222222-2222-2222-2222-222222222222";
const MISSING_PINNED = "33333333-3333-3333-3333-333333333333";
const MISSING_TITLED = "44444444-4444-4444-4444-444444444444";
const MISSING_ARCHIVED = "55555555-5555-5555-5555-555555555555";
const HELD = "77777777-7777-7777-7777-777777777777";
const BLANK_LABEL = "88888888-8888-8888-8888-888888888888";

function row(sessionId: string): SessionRow {
  return {
    session_id: sessionId,
    title: "on the listing",
    first_prompt: "",
    entrypoint: "cli",
    modified: "2026-01-01T00:00:00Z",
    cli_version: "2.1.0",
    other: false,
  };
}

function label(sessionId: string, patch: Partial<SessionMetadata> = {}): SessionMetadata {
  return {
    session_id: sessionId,
    revision: 1,
    title: "",
    pinned: false,
    archived: false,
    ...patch,
  };
}

test("empty unless the listing is complete", () => {
  const metadata = [label(MISSING_PINNED, { pinned: true })];
  expect(missingRecords({ rows: [], metadata, held: new Set(), complete: false })).toEqual([]);
});

test("a pinned, titled or archived label whose session is off a complete listing becomes a record", () => {
  const metadata = [
    label(MISSING_PINNED, { pinned: true, revision: 3 }),
    label(MISSING_TITLED, { title: "renamed" }),
    label(MISSING_ARCHIVED, { archived: true }),
    label(BLANK_LABEL), // neither pinned, titled nor archived: never a record on its own
  ];
  const records = missingRecords({
    rows: [row(ROW_A)],
    metadata,
    held: new Set(),
    complete: true,
  });
  expect(records.map((item) => item.session_id).sort()).toEqual(
    [MISSING_PINNED, MISSING_TITLED, MISSING_ARCHIVED].sort(),
  );
  const pinned = records.find((item) => item.session_id === MISSING_PINNED) as MissingRecord;
  expect(pinned.revision).toBe(3);
  expect(pinned.pinned).toBe(true);
});

test("listed and held ids are excluded even when their label qualifies", () => {
  const metadata = [
    label(ROW_A, { pinned: true }),
    label(HELD, { pinned: true }),
    label(MISSING_PINNED, { pinned: true }),
  ];
  const records = missingRecords({
    rows: [row(ROW_A), row(ROW_B)],
    metadata,
    held: new Set([HELD]),
    complete: true,
  });
  expect(records.map((item) => item.session_id)).toEqual([MISSING_PINNED]);
});

test("unread-only and color-only missing preferences keep their exact identity and revision", () => {
  const records = missingRecords({
    rows: [],
    metadata: [
      label(ROW_A, { unread: true, revision: 8 }),
      label(ROW_B, { color: "orange", revision: 9 }),
      label(BLANK_LABEL, { unread: false, color: null }),
    ],
    held: new Set(),
    complete: true,
  });
  expect(records).toEqual([
    {
      session_id: ROW_A,
      title: "",
      pinned: false,
      archived: false,
      revision: 8,
      unread: true,
    },
    {
      session_id: ROW_B,
      title: "",
      pinned: false,
      archived: false,
      revision: 9,
      color: "orange",
    },
  ]);
});

test("unread/color preferences are not reported missing while incomplete, listed or held", () => {
  const input = {
    rows: [row(ROW_A)],
    metadata: [
      label(ROW_A, { color: "blue" }),
      label(HELD, { unread: true }),
      label(ROW_B, { unread: true, color: "red" }),
    ],
    held: new Set([HELD]),
    complete: false,
  };
  expect(missingRecords(input)).toEqual([]);
  expect(missingRecords({ ...input, complete: true }).map((record) => record.session_id)).toEqual([
    ROW_B,
  ]);
});

test("pinned records sort first, then every record sorts by id", () => {
  const metadata = [
    label(MISSING_TITLED, { title: "z" }),
    label(MISSING_ARCHIVED, { archived: true, pinned: true }),
    label(MISSING_PINNED, { title: "a" }),
  ];
  const records = missingRecords({
    rows: [],
    metadata,
    held: new Set(),
    complete: true,
  });
  // MISSING_ARCHIVED first: it is pinned, even though its id ("5...") is the highest.
  // The other two are unpinned, so they sort by id: "3..." before "4...".
  expect(records.map((item) => item.session_id)).toEqual([
    MISSING_ARCHIVED,
    MISSING_PINNED,
    MISSING_TITLED,
  ]);
});

test("isArchiveOnly holds for an archive mark with nothing else the user wrote", () => {
  expect(isArchiveOnly(label(ROW_A, { archived: true, color: null }))).toBe(true);
});

test("isArchiveOnly is false once the label also carries a title, pin, unread or color", () => {
  expect(isArchiveOnly(label(ROW_A, { archived: true, title: "named" }))).toBe(false);
  expect(isArchiveOnly(label(ROW_A, { archived: true, pinned: true }))).toBe(false);
  expect(isArchiveOnly(label(ROW_A, { archived: true, unread: true }))).toBe(false);
  expect(isArchiveOnly(label(ROW_A, { archived: true, color: "blue" }))).toBe(false);
});

test("isArchiveOnly is false without the archive mark itself", () => {
  expect(isArchiveOnly(label(ROW_A, { archived: false }))).toBe(false);
});

const A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const C = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const D = "dddddddd-dddd-dddd-dddd-dddddddddddd";

test("orderTabs puts pinned first and keeps each group's order", () => {
  expect(orderTabs([A, B, C, D], [C, A])).toEqual([A, C, B, D]);
});

test("moveTab swaps inside the tab's own group only", () => {
  expect(moveTab([A, B, C, D], C, -1, [A, B])).toEqual([A, B, C, D]);
  expect(moveTab([A, B, C, D], B, -1, [A, B])).toEqual([B, A, C, D]);
  expect(moveTab([A, B, C, D], C, 1, [A, B])).toEqual([A, B, D, C]);
});

test("moveTabTo moves before a tab or to the group's end, never across groups", () => {
  expect(moveTabTo([A, B, C, D], D, C, [A])).toEqual([A, B, D, C]);
  expect(moveTabTo([A, B, C, D], B, null, [A])).toEqual([A, C, D, B]);
  expect(moveTabTo([A, B, C, D], A, null, [A])).toEqual([A, B, C, D]);
  expect(moveTabTo([A, B, C, D], C, A, [A])).toEqual([A, B, C, D]);
});
