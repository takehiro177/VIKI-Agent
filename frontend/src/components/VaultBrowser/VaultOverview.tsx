import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { Capture, CapturesApi } from "../../clients/vaults/capturesApi";
import { t } from "../../i18n/t";
import type {
  OverviewNote,
  VaultBookmark,
  VaultEntry,
  VaultOverview as VaultOverviewData,
} from "../../state/vaultApi";
import {
  ageText,
  datedCount,
  type Growth,
  growthOf,
  type ListingNote,
  leadOf,
  mapsOf,
  type RecentGroup,
  recentGroups,
  rediscover,
  type StatsNote,
  type TagCount,
  todayNumber,
  topTags,
} from "../../state/vaultOverviewStats";
import { Loading } from "../Loading/Loading";
import { NoteCard, type NoteCardKind } from "./NoteCard";
import "./VaultOverview.css";

const DAY_MS = 86_400_000;
const RECENT_INITIAL = 10;
const RECENT_STEP = 20;
const CAPTURE_PREVIEW_MAX = 120;
const CAPTURE_PREVIEW_COUNT = 3;
// Keep in sync with .vo-weeks's height in VaultOverview.css.
const WEEK_CHART_HEIGHT = 56;

export interface VaultOverviewProps {
  overview: VaultOverviewData;
  today: Date;
  workspaceId: string;
  captures?: CapturesApi;
  listing?: readonly VaultEntry[];
  listingReady?: boolean;
  listingPartial?: boolean;
  bookmarks?: readonly VaultBookmark[];
  inboxCount: number;
  onOpen: (ref: string, kind: "recent" | "map", origin: HTMLElement) => void;
  onOpenCard?: (ref: string, kind: NoteCardKind, origin: HTMLElement) => void;
  onTag?: (tag: string) => void;
  onBrowse: (folder: string) => void;
  onTriage?: () => void;
  onReload: () => void;
}

/** `text`'s first line, capped at `max` code points (an ellipsis marks real truncation only). */
function firstLine(text: string, max: number): string {
  const line = text.split("\n")[0] ?? "";
  const codePoints = [...line];
  return codePoints.length <= max ? line : `${codePoints.slice(0, max).join("")}…`;
}

/** The Monday `week.start` names, for the bars' sr-only text. Constructed from the raw ms rather
 * than a local y/m/d triple so the result never depends on the host's time zone. */
function weekLabel(start: number): string {
  return new Date(start * DAY_MS).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

/** Same recency precedence as `ageText` (prefer `captured`, fall back to `modified`), so "the 3
 * newest" means the same thing here as the age text printed next to each one. */
function captureTime(capture: Capture): number {
  const parsed = capture.captured !== null ? Date.parse(capture.captured) : Number.NaN;
  return Number.isNaN(parsed) ? Date.parse(capture.modified) : parsed;
}

// Three tiers, not five shades of one hue: evergreen is the accent, draft is the muted
// text-tone used everywhere else in this file, and the rest share a lighter version of it —
// a run of near-identical opacity steps on var(--or) read as one solid block in both themes.
const STATUS_LEGEND: ReadonlyArray<{
  key: keyof Growth["knowledge"]["status"];
  label: string;
  color: string;
}> = [
  { key: "evergreen", label: "evergreen", color: "var(--vault-settled)" },
  { key: "draft", label: "draft", color: "var(--vault-growing)" },
  { key: "other", label: "other", color: "var(--t3)" },
  { key: "none", label: "no status", color: "var(--t3)" },
  { key: "unreadable", label: "unreadable", color: "var(--t3)" },
];

const SOURCE_LEGEND: ReadonlyArray<{
  key: "processed" | "unread" | "other";
  label: string;
  color: string;
}> = [
  { key: "processed", label: "processed", color: "var(--vault-settled)" },
  { key: "unread", label: "unread", color: "var(--vault-open)" },
  { key: "other", label: "other", color: "var(--t3)" },
];

function coverageLine(partial: boolean) {
  return (
    <p className="vo-quiet vo-coverage">
      {t("vault.coverage.source")}
      {partial ? ` · ${t("vault.coverage.partial")}` : ""}
    </p>
  );
}

function overviewNote(rows: readonly OverviewNote[], ref: string): OverviewNote | undefined {
  return rows.find((row) => row.ref === ref);
}

function cardData(
  ref: string,
  listingByRef: ReadonlyMap<string, VaultEntry>,
  overviewRows: readonly OverviewNote[],
) {
  const listing = listingByRef.get(ref);
  const overview = overviewNote(overviewRows, ref);
  return {
    ...(listing ? { listing } : {}),
    ...(overview ? { overview } : {}),
  };
}

function InboxSection({
  captures,
  workspaceId,
  inboxCount,
  today,
  onBrowse,
  onTriage,
}: {
  captures: CapturesApi | undefined;
  workspaceId: string;
  inboxCount: number;
  today: Date;
  onBrowse: (folder: string) => void;
  onTriage: (() => void) | undefined;
}) {
  const idBase = useId();
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [items, setItems] = useState<readonly Capture[]>([]);
  const [omitted, setOmitted] = useState(0);

  useEffect(() => {
    if (!captures) return;
    let active = true;
    setPhase("loading");
    const controller = new AbortController();
    captures.list(workspaceId, controller.signal).then(
      (listing) => {
        if (!active) return;
        setItems(listing.captures);
        setOmitted(listing.omitted);
        setPhase("ready");
      },
      () => {
        if (!active) return;
        setPhase("error");
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [captures, workspaceId]);

  const newest = useMemo(
    () =>
      [...items].sort((a, b) => captureTime(b) - captureTime(a)).slice(0, CAPTURE_PREVIEW_COUNT),
    [items],
  );

  const triage = captures !== undefined && onTriage !== undefined;
  const actionLabel = triage ? "Triage inbox" : "Browse inbox";
  const action = () => {
    if (triage) onTriage();
    else onBrowse("00-inbox");
  };
  // Nothing to triage or browse on a clear inbox, so no button. While the listing is
  // still loading or failed its count is unknown, so the button stays shown — the user may
  // still want to open triage or browse.
  const showAction = captures
    ? phase !== "ready" || items.length > 0 || omitted > 0
    : inboxCount > 0;

  return (
    <div className="vo-inbox">
      <h2 className="lbl vo-h2">{t("vault.block.inbox")}</h2>
      {captures ? (
        <>
          {phase === "loading" && (
            <Loading announce={false} className="vo-quiet">
              Reading the inbox…
            </Loading>
          )}
          {phase === "error" && <p className="vo-quiet">Couldn't read the inbox.</p>}
          {phase === "ready" && items.length === 0 && <p className="vo-quiet">Inbox is clear.</p>}
          {phase === "ready" && items.length > 0 && (
            <>
              <p>{items.length === 1 ? "1 capture waiting" : `${items.length} captures waiting`}</p>
              <ul className="vo-captures">
                {newest.map((capture, captureIndex) => {
                  const age = ageText(capture.captured, capture.modified, today);
                  const refId = `${idBase}-capture-${captureIndex}`;
                  return (
                    <li key={capture.ref} className="vo-capture" aria-describedby={refId}>
                      {capture.preview !== null && (
                        <p className="vo-summary">
                          {firstLine(capture.preview, CAPTURE_PREVIEW_MAX)}
                        </p>
                      )}
                      {age !== "" && <p className="vo-meta">{age}</p>}
                      <small id={refId} className="vo-ref">
                        {capture.ref}
                      </small>
                    </li>
                  );
                })}
              </ul>
              {omitted > 0 && <p className="vo-quiet">+{omitted} more not listed</p>}
            </>
          )}
        </>
      ) : (
        <p className="vo-quiet">
          {inboxCount === 0
            ? "Inbox is clear."
            : inboxCount === 1
              ? "1 note in the inbox"
              : `${inboxCount} notes in the inbox`}
        </p>
      )}
      {showAction && (
        <button type="button" className="btn alt sm" onClick={action}>
          {actionLabel}
        </button>
      )}
    </div>
  );
}

function RecentSection({
  groups,
  hasMore,
  listingByRef,
  overviewRows,
  partial,
  onShowMore,
  onOpenCard,
}: {
  groups: readonly RecentGroup[];
  hasMore: boolean;
  listingByRef: ReadonlyMap<string, VaultEntry>;
  overviewRows: readonly OverviewNote[];
  partial: boolean;
  onShowMore: () => void;
  onOpenCard: (ref: string, kind: NoteCardKind, origin: HTMLElement) => void;
}) {
  return (
    <div className="vo-recent">
      <h2 className="lbl vo-h2">{t("vault.block.latest")}</h2>
      {coverageLine(partial)}
      {groups.length === 0 ? (
        <p className="vo-quiet">No dated notes yet.</p>
      ) : (
        <div className="vo-groups">
          {groups.map((group) => (
            <div key={group.day}>
              <h3 className="lbl vo-h3">{group.label}</h3>
              <ul className="vo-items">
                {group.items.map((item) => {
                  return (
                    <li key={item.row.ref} className="vo-item">
                      <NoteCard
                        noteRef={item.row.ref}
                        {...cardData(item.row.ref, listingByRef, overviewRows)}
                        variant="card"
                        kind="recent"
                        onOpen={onOpenCard}
                      />
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
      {hasMore && (
        <button type="button" className="btn txt sm" onClick={onShowMore}>
          {t("vault.action.show_more")}
        </button>
      )}
    </div>
  );
}

function GrowthSection({ growth, partial }: { growth: Growth; partial: boolean }) {
  const statusTotal = growth.knowledge.total;
  const nonZeroStatus = STATUS_LEGEND.filter((entry) => growth.knowledge.status[entry.key] > 0);
  const nonZeroSources = SOURCE_LEGEND.filter((entry) => growth.sources[entry.key] > 0);
  const maxWeek = Math.max(1, ...growth.weeks.map((week) => week.count));

  return (
    <div className="vo-growth">
      <h2 className="lbl vo-h2">{t("vault.block.week")}</h2>
      <ul className="vo-cards">
        <li className="vo-card">
          <p>{growth.knowledge.total} knowledge notes</p>
          <div className="vo-meter" aria-hidden="true">
            {nonZeroStatus.map((entry) => (
              <span
                key={entry.key}
                className="vo-meter-segment"
                style={{
                  width: `${statusTotal > 0 ? (growth.knowledge.status[entry.key] / statusTotal) * 100 : 0}%`,
                  background: entry.color,
                }}
              />
            ))}
          </div>
          <p className="vo-meta">
            {nonZeroStatus
              .map((entry) => `${growth.knowledge.status[entry.key]} ${entry.label}`)
              .join(" · ")}
          </p>
          <p className="vo-meta">
            {`Confidence: ${growth.knowledge.confidence.high} high · ${growth.knowledge.confidence.medium} medium · ${growth.knowledge.confidence.low} low · ${growth.knowledge.confidence.other} other · ${growth.knowledge.confidence.none} not rated`}
          </p>
        </li>
        <li className="vo-card">
          <p>{growth.sources.total} sources</p>
          <div className="vo-meter" aria-hidden="true">
            {nonZeroSources.map((entry) => (
              <span
                key={entry.key}
                className="vo-meter-segment"
                style={{
                  width: `${(growth.sources[entry.key] / growth.sources.total) * 100}%`,
                  background: entry.color,
                }}
              />
            ))}
          </div>
          <p className="vo-meta">
            {nonZeroSources
              .map((entry) => `${growth.sources[entry.key]} ${entry.label}`)
              .join(" · ")}
          </p>
        </li>
      </ul>
      <p className="lbl vo-h3">Added per week</p>
      {coverageLine(partial)}
      <div className="vo-weeks-wrap">
        <ol className="vo-weeks">
          {growth.weeks.map((week, weekIndex) => {
            const isCurrent = weekIndex === growth.weeks.length - 1;
            const barClass =
              week.count === 0
                ? "vo-week-bar vo-week-bar-zero"
                : isCurrent
                  ? "vo-week-bar vo-week-bar-current"
                  : "vo-week-bar";
            const height =
              week.count === 0
                ? 2
                : Math.max(3, Math.round((week.count / maxWeek) * WEEK_CHART_HEIGHT));
            return (
              <li key={week.start} className="vo-week">
                <span className={barClass} aria-hidden="true" style={{ height: `${height}px` }} />
                <span className="sr-only">
                  Week of {weekLabel(week.start)}: {week.count} added
                </span>
              </li>
            );
          })}
        </ol>
        <div className="vo-weeks-labels">
          <span className="vo-quiet">{weekLabel(growth.weeks[0]?.start ?? 0)}</span>
          <span className="vo-quiet">This week</span>
        </div>
      </div>
      <p>{growth.lastFourWeeks} added in the last 4 weeks</p>
      <p className="vo-quiet">From knowledge, sources and maps.</p>
    </div>
  );
}

function FeaturedSection({
  note,
  listingByRef,
  partial,
  onOpenCard,
}: {
  note: StatsNote | null;
  listingByRef: ReadonlyMap<string, VaultEntry>;
  partial: boolean;
  onOpenCard: (ref: string, kind: NoteCardKind, origin: HTMLElement) => void;
}) {
  return (
    <div className="vo-featured">
      <h2 className="lbl vo-h2">{t("vault.block.lead")}</h2>
      {coverageLine(partial)}
      {note ? (
        <NoteCard
          noteRef={note.ref}
          {...cardData(note.ref, listingByRef, [note])}
          overview={note}
          variant="lead"
          kind="lead"
          onOpen={onOpenCard}
        />
      ) : (
        <p className="vo-quiet">{t("vault.empty.lead")}</p>
      )}
    </div>
  );
}

function RediscoverSection({
  rows,
  listingByRef,
  overviewRows,
  onOpenCard,
}: {
  rows: readonly ListingNote[];
  listingByRef: ReadonlyMap<string, VaultEntry>;
  overviewRows: readonly OverviewNote[];
  onOpenCard: (ref: string, kind: NoteCardKind, origin: HTMLElement) => void;
}) {
  return (
    <div className="vo-rediscover">
      <h2 className="lbl vo-h2">{t("vault.block.rediscover")}</h2>
      <p className="vo-quiet">{t("vault.rediscover.hint")}</p>
      {rows.length ? (
        <ul className="vo-items">
          {rows.map((row) => (
            <li key={row.ref} className="vo-item">
              <NoteCard
                noteRef={row.ref}
                {...cardData(row.ref, listingByRef, overviewRows)}
                variant="card"
                kind="rediscover"
                onOpen={onOpenCard}
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className="vo-quiet">{t("vault.empty.rediscover")}</p>
      )}
    </div>
  );
}

function BookmarksSection({
  bookmarks,
  listingByRef,
  listingReady,
  listingPartial,
  overviewRows,
  onOpenCard,
}: {
  bookmarks: readonly VaultBookmark[];
  listingByRef: ReadonlyMap<string, VaultEntry>;
  listingReady: boolean;
  listingPartial: boolean;
  overviewRows: readonly OverviewNote[];
  onOpenCard: (ref: string, kind: NoteCardKind, origin: HTMLElement) => void;
}) {
  const newest = bookmarks
    .filter((item) => Number.isFinite(Date.parse(item.created_at)))
    .sort(
      (a, b) =>
        Date.parse(b.created_at) - Date.parse(a.created_at) ||
        (a.ref < b.ref ? -1 : a.ref > b.ref ? 1 : 0),
    )
    .slice(0, 5);
  return (
    <div className="vo-bookmarks">
      <h2 className="lbl vo-h2">{t("vault.block.bookmarks")}</h2>
      {newest.length === 0 ? (
        <p className="vo-quiet">{t("vault.bookmark.empty")}</p>
      ) : (
        <ul className="vo-items">
          {newest.map((item) => {
            const listing = listingByRef.get(item.ref);
            if (listingReady && !listing) {
              return (
                <li key={item.ref} className="vo-item">
                  <span className="vo-ref">{item.ref}</span>
                  <p className="vo-meta">
                    {listingPartial ? t("vault.bookmark.partial") : t("vault.bookmark.missing")}
                  </p>
                </li>
              );
            }
            return (
              <li key={item.ref} className="vo-item">
                <NoteCard
                  noteRef={item.ref}
                  {...cardData(item.ref, listingByRef, overviewRows)}
                  variant="card"
                  kind="bookmark"
                  onOpen={onOpenCard}
                />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function TopicsSection({
  maps,
  tags,
  partial,
  listingByRef,
  overviewRows,
  onOpenCard,
  onTag,
}: {
  maps: readonly StatsNote[];
  tags: readonly TagCount[];
  partial: boolean;
  listingByRef: ReadonlyMap<string, VaultEntry>;
  overviewRows: readonly OverviewNote[];
  onOpenCard: (ref: string, kind: NoteCardKind, origin: HTMLElement) => void;
  onTag: ((tag: string) => void) | undefined;
}) {
  return (
    <div className="vo-topics">
      <h2 className="lbl vo-h2">{t("vault.block.topics")}</h2>
      {coverageLine(partial)}
      <h3 className="lbl vo-h3">Maps · {maps.length}</h3>
      {maps.length === 0 ? (
        <p className="vo-quiet">No maps yet.</p>
      ) : (
        <ul className="vo-cards">
          {maps.map((row) => (
            <li key={row.ref} className="vo-item">
              <NoteCard
                noteRef={row.ref}
                {...cardData(row.ref, listingByRef, overviewRows)}
                variant="card"
                kind="map"
                onOpen={onOpenCard}
              />
              <p className="vo-meta">
                {row.links ?? 0}
                {row.complete ? "" : "+"} notes linked
              </p>
            </li>
          ))}
        </ul>
      )}
      <h3 className="lbl vo-h3">Tags</h3>
      <ul className="vo-tags">
        {tags.map((entry) =>
          onTag ? (
            <li key={entry.tag}>
              <button type="button" className="chip vo-tag" onClick={() => onTag(entry.tag)}>
                #{entry.tag} ({entry.count})
              </button>
            </li>
          ) : (
            <li key={entry.tag}>
              <span className="chip">
                #{entry.tag} ({entry.count})
              </span>
            </li>
          ),
        )}
      </ul>
      {partial && <p className="vo-quiet">Tag counts cover the notes read.</p>}
    </div>
  );
}

export function VaultOverview({
  overview,
  today,
  workspaceId,
  captures,
  listing = [],
  listingReady = false,
  listingPartial = false,
  bookmarks = [],
  inboxCount,
  onOpen,
  onOpenCard,
  onTag,
  onBrowse,
  onTriage,
  onReload,
}: VaultOverviewProps) {
  const todayNum = todayNumber(today);
  const rootRef = useRef<HTMLElement>(null);
  const [shown, setShown] = useState({ workspaceId, overview });
  const [limit, setLimit] = useState({ workspaceId, value: RECENT_INITIAL });
  const sameWorkspace = shown.workspaceId === workspaceId;
  const heldForFocus =
    sameWorkspace &&
    shown.overview !== overview &&
    rootRef.current?.contains(document.activeElement) === true;
  const shownOverview = sameWorkspace && heldForFocus ? shown.overview : overview;

  useEffect(() => {
    if (!heldForFocus && (shown.workspaceId !== workspaceId || shown.overview !== overview))
      setShown({ workspaceId, overview });
  }, [heldForFocus, overview, shown.overview, shown.workspaceId, workspaceId]);

  const listingByRef = useMemo(() => new Map(listing.map((row) => [row.ref, row])), [listing]);
  const lead = useMemo(() => leadOf(shownOverview.notes), [shownOverview]);
  const latestNotes = useMemo(
    () => shownOverview.notes.filter((row) => row.ref !== lead?.ref),
    [lead, shownOverview],
  );
  const latestLimit = limit.workspaceId === workspaceId ? limit.value : RECENT_INITIAL;

  const groups = useMemo(
    () => recentGroups(latestNotes, todayNum, latestLimit),
    [latestNotes, todayNum, latestLimit],
  );
  const totalDated = useMemo(() => datedCount(latestNotes), [latestNotes]);
  const growth = useMemo(() => growthOf(shownOverview.notes, todayNum), [shownOverview, todayNum]);
  const maps = useMemo(() => mapsOf(shownOverview.notes), [shownOverview]);
  const tags = useMemo(() => topTags(shownOverview.notes), [shownOverview]);
  const rediscoverRows = useMemo(() => rediscover(listing, today), [listing, today]);
  const openCard = (ref: string, kind: NoteCardKind, origin: HTMLElement) => {
    if (onOpenCard) onOpenCard(ref, kind, origin);
    else onOpen(ref, kind === "map" ? "map" : "recent", origin);
  };
  const readFailure = shownOverview.partial_reasons.some(
    (reason) => reason === "unreadable" || reason === "changed",
  );

  return (
    <section aria-label="Vault overview" className="vo" ref={rootRef}>
      {heldForFocus && (
        <button
          type="button"
          className="btn txt sm vo-show-latest"
          onClick={() => setShown({ workspaceId, overview })}
        >
          {t("vault.action.show_latest")}
        </button>
      )}
      {shownOverview.partial && (
        <div className="vo-quiet">
          <p>
            Read {shownOverview.notes.length} notes; {shownOverview.omitted} more were not read, so
            counts are partial.
          </p>
          <button type="button" className="btn txt sm" onClick={onReload}>
            {t("vault.action.reload")}
          </button>
        </div>
      )}
      {readFailure && <p className="vo-read-failure">{t("vault.coverage.read_failure")}</p>}
      <div className="vo-grid">
        <FeaturedSection
          note={lead}
          listingByRef={listingByRef}
          partial={shownOverview.partial}
          onOpenCard={openCard}
        />
        <RecentSection
          groups={groups}
          hasMore={latestLimit < totalDated}
          listingByRef={listingByRef}
          overviewRows={shownOverview.notes}
          partial={shownOverview.partial}
          onShowMore={() => setLimit({ workspaceId, value: latestLimit + RECENT_STEP })}
          onOpenCard={openCard}
        />
        {/* One wrapper so the wide layout is a plain two-column, one-row grid: Recent and this
            side column each size to their own content, instead of Recent spanning rows next to
            three separately auto-placed items (which stretched the first row to Recent's full
            height). display: contents below the container query dissolves this box so Inbox,
            Growth and Topics interleave with Recent in source order via the `order` below. */}
        <div className="vo-side">
          <InboxSection
            captures={captures}
            workspaceId={workspaceId}
            inboxCount={inboxCount}
            today={today}
            onBrowse={onBrowse}
            onTriage={onTriage}
          />
          <RediscoverSection
            rows={rediscoverRows}
            listingByRef={listingByRef}
            overviewRows={shownOverview.notes}
            onOpenCard={openCard}
          />
          <BookmarksSection
            bookmarks={bookmarks}
            listingByRef={listingByRef}
            listingReady={listingReady}
            listingPartial={listingPartial}
            overviewRows={shownOverview.notes}
            onOpenCard={openCard}
          />
          <GrowthSection growth={growth} partial={shownOverview.partial} />
          <TopicsSection
            maps={maps}
            tags={tags}
            partial={shownOverview.partial}
            listingByRef={listingByRef}
            overviewRows={shownOverview.notes}
            onOpenCard={openCard}
            onTag={onTag}
          />
        </div>
      </div>
    </section>
  );
}

/**
 * Shown by `VaultBrowser` while `useHeld` holds a first, dataless overview load. Reuses the real
 * layout's own classes (`.vo-grid`, `.vo-featured`, `.vo-recent`, `.vo-side`, `.vo-inbox`,
 * `.vo-rediscover`, `.vo-bookmarks`, `.vo-growth`, `.vo-topics`, `.vo-groups`, `.vo-items`, `.vo-item`) so the container query that switches
 * between the wide and narrow layout (VaultOverview.css) applies to the skeleton exactly as it
 * does to the loaded page; only the filler rectangles (`.skel`, the loading kit) and their sizes
 * (`.vo-skel-*`, VaultOverview.css) are new. `.vo-grid` is `aria-hidden`: the one thing
 * assistive tech needs is the sr-only line that sits above it.
 */
export function VaultOverviewSkeleton() {
  return (
    <div className="vo vo-skel reveal-late" role="status" aria-busy="true">
      <span className="sr-only">Reading your notes…</span>
      <div className="vo-grid" aria-hidden="true">
        <div className="vo-featured">
          <div className="skel vo-skel-heading" />
          <div className="skel vo-skel-title" />
          <div className="skel vo-skel-meta" />
        </div>
        <div className="vo-recent">
          <div className="skel vo-skel-heading" />
          <div className="vo-groups">
            {[0, 1].map((group) => (
              <div key={group}>
                <div className="skel vo-skel-subheading" />
                <ul className="vo-items">
                  {[0, 1, 2].map((item) => (
                    <li key={item} className="vo-item">
                      <div className="skel vo-skel-title" />
                      <div className="skel vo-skel-meta" />
                      <div className="skel vo-skel-summary" />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
        <div className="vo-side">
          <div className="vo-inbox">
            <div className="skel vo-skel-heading" />
            <div className="skel vo-skel-meta" />
          </div>
          <div className="vo-rediscover">
            <div className="skel vo-skel-heading" />
            <div className="skel vo-skel-title" />
          </div>
          <div className="vo-bookmarks">
            <div className="skel vo-skel-heading" />
            <div className="skel vo-skel-title" />
          </div>
          <div className="vo-growth">
            <div className="skel vo-skel-heading" />
            <div className="skel vo-skel-chart" />
          </div>
          <div className="vo-topics">
            <div className="skel vo-skel-heading" />
            <div className="vo-skel-chips">
              {[0, 1, 2, 3].map((chip) => (
                <span key={chip} className="skel vo-skel-chip" />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
