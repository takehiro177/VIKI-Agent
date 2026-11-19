import { type ReactNode, useId, useState } from "react";
import { t, tf } from "../../i18n/t";
import type { OverviewNote, VaultOverview } from "../../state/vaultApi";
import {
  FOLDER_SECTIONS,
  growthOf,
  type ListingNote,
  otherFolders,
  sectionOf,
  todayNumber,
  topTags,
} from "../../state/vaultOverviewStats";
import { NoteCard, type NoteCardKind } from "./NoteCard";
import "./VaultSectionPage.css";

export type VaultSectionKey = "knowledge" | "sources" | "maps" | "else";

export interface VaultSectionListingRow {
  ref: string;
  title: string;
  modified?: number;
}

export interface VaultSectionPageProps {
  section: VaultSectionKey;
  listing: readonly VaultSectionListingRow[];
  overview: VaultOverview | null;
  listingPartial: boolean;
  /** False while the listing is still loading or its read failed. An unread listing is not an
   * empty one, so the page then states no count and no empty folder, and shows `listingStatus`
   * where the notes would be. */
  listingReady?: boolean;
  /** The listing's own loading or failure line, shown only while `listingReady` is false. */
  listingStatus?: ReactNode;
  /** The note open in the reader beside the list, marked as the current row. */
  selected?: string | null;
  onOpen: (ref: string, kind: NoteCardKind, origin: HTMLElement) => void;
  onBrowse: (folder: string) => void;
}

const PAGE_SIZE = 32;

const COVERED_SECTIONS = {
  knowledge: {
    label: "vault.nav.knowledge",
    description: "vault.intro.knowledge",
    growth: "knowledge",
  },
  sources: {
    label: "vault.nav.sources",
    description: "vault.intro.sources",
    growth: "sources",
  },
  maps: {
    label: "vault.nav.maps",
    description: "vault.intro.maps",
    growth: "maps",
  },
} as const;

function fileTime(value: number | undefined): number | null {
  return value !== undefined && Number.isFinite(value) && value > 0 ? value : null;
}

function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function compareRecent(a: VaultSectionListingRow, b: VaultSectionListingRow): number {
  const aTime = fileTime(a.modified);
  const bTime = fileTime(b.modified);
  if (aTime !== null && bTime !== null) return bTime - aTime || compareText(a.ref, b.ref);
  if (aTime !== null) return -1;
  if (bTime !== null) return 1;
  return compareText(a.ref, b.ref);
}

function countLabel(count: number, partial: boolean): string {
  return tf(
    count === 1
      ? partial
        ? "vault.count.at_least_one"
        : "vault.count.note"
      : partial
        ? "vault.count.at_least"
        : "vault.count.notes",
    { count },
  );
}

function latestByFolder(
  listing: readonly VaultSectionListingRow[],
): Map<string, VaultSectionListingRow> {
  const latest = new Map<string, VaultSectionListingRow>();
  for (const row of listing) {
    const slash = row.ref.indexOf("/");
    if (slash < 1) continue;
    const folder = row.ref.slice(0, slash);
    const current = latest.get(folder);
    if (!current || compareRecent(row, current) < 0) latest.set(folder, row);
  }
  return latest;
}

function changedDate(modified: number | null): string | null {
  if (modified === null) return null;
  const date = new Date(modified * 1000);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

function folderOfSection(section: "knowledge" | "sources" | "maps"): string {
  return (
    Object.keys(FOLDER_SECTIONS).find(
      (candidate) => FOLDER_SECTIONS[candidate as keyof typeof FOLDER_SECTIONS].key === section,
    ) ?? ""
  );
}

function sectionNotes(
  overview: VaultOverview | null,
  section: "knowledge" | "sources" | "maps",
): OverviewNote[] {
  const name = section === "knowledge" ? "Knowledge" : section === "sources" ? "Source" : "Map";
  return (overview?.notes ?? []).filter((note) => sectionOf(note.ref) === name);
}

interface Part {
  key: string;
  label: string;
  count: number;
  tone: "settled" | "growing" | "open" | "none";
}

/** One stacked bar and its legend: the share of each state in the section, by colour. */
function StateBar({ parts, label }: { parts: readonly Part[]; label: string }) {
  const shown = parts.filter((part) => part.count > 0);
  const total = shown.reduce((sum, part) => sum + part.count, 0);
  if (total === 0) return null;
  return (
    <figure className="vsp-bar-figure">
      <div className="vsp-bar" role="img" aria-label={label}>
        {shown.map((part) => (
          <span
            key={part.key}
            className="vsp-bar-part"
            data-tone={part.tone}
            style={{ flexGrow: part.count }}
          />
        ))}
      </div>
      <figcaption>
        <ul className="vsp-legend">
          {shown.map((part) => (
            <li key={part.key} data-tone={part.tone}>
              <strong>{part.count}</strong> {part.label}
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}

export interface VaultSectionInsightProps {
  section: "knowledge" | "sources" | "maps";
  overview: VaultOverview | null;
  onOpen: (ref: string, kind: NoteCardKind, origin: HTMLElement) => void;
  onTag: (tag: string) => void;
}

/**
 * The reader side of a section while no note is open: how the section is made up and which topics
 * it covers, read from the overview, so the list beside it starts with its notes.
 */
export function VaultSectionInsight({
  section,
  overview,
  onOpen,
  onTag,
}: VaultSectionInsightProps) {
  const headingId = useId();
  const config = COVERED_SECTIONS[section];
  const notes = sectionNotes(overview, section);
  const growth = growthOf(notes, todayNumber(new Date()));
  const topicRows = (overview?.notes ?? []).filter((note) => sectionOf(note.ref) !== null);
  const tags = overview ? topTags(topicRows) : [];
  const coverage = overview
    ? `${t("vault.coverage.source")}${overview.partial ? ` · ${t("vault.coverage.partial")}` : ""}`
    : t("vault.card.unread");
  const total =
    section === "knowledge"
      ? `${growth.knowledge.total} knowledge notes`
      : section === "sources"
        ? `${growth.sources.total} sources`
        : `${growth.maps.total} maps`;
  const maps = notes
    .filter((note) => (note.links ?? 0) > 0)
    .sort((a, b) => (b.links ?? 0) - (a.links ?? 0) || compareText(a.ref, b.ref))
    .slice(0, 8);
  const mostLinks = Math.max(1, ...maps.map((note) => note.links ?? 0));

  return (
    <section className="vsp-insight" aria-labelledby={headingId}>
      <p className="vsp-kicker">{t(config.label)}</p>
      <h2 id={headingId} className="vsp-total">
        {overview ? total : t(config.label)}
      </h2>
      <p className="vsp-meta">{t(config.description)}</p>
      {overview && section === "knowledge" ? (
        <StateBar
          label={`Knowledge notes by status: ${growth.knowledge.status.evergreen} evergreen, ${growth.knowledge.status.draft} draft`}
          parts={[
            {
              key: "evergreen",
              label: "evergreen",
              count: growth.knowledge.status.evergreen,
              tone: "settled",
            },
            { key: "draft", label: "draft", count: growth.knowledge.status.draft, tone: "growing" },
            { key: "other", label: "other", count: growth.knowledge.status.other, tone: "none" },
            { key: "none", label: "not set", count: growth.knowledge.status.none, tone: "none" },
            {
              key: "unreadable",
              label: "unreadable",
              count: growth.knowledge.status.unreadable,
              tone: "none",
            },
          ]}
        />
      ) : null}
      {overview && section === "sources" ? (
        <StateBar
          label={`Sources by status: ${growth.sources.processed} processed, ${growth.sources.unread} unread`}
          parts={[
            {
              key: "processed",
              label: "processed",
              count: growth.sources.processed,
              tone: "settled",
            },
            { key: "unread", label: "unread", count: growth.sources.unread, tone: "open" },
            { key: "other", label: "other", count: growth.sources.other, tone: "none" },
          ]}
        />
      ) : null}
      {section === "maps" && maps.length > 0 ? (
        <ul className="vsp-maps" aria-label="Maps by linked notes">
          {maps.map((note) => (
            <li key={note.ref}>
              <button
                type="button"
                className="vsp-map"
                data-vault-ref={note.ref}
                data-vault-kind="map"
                onClick={(event) => onOpen(note.ref, "map", event.currentTarget)}
              >
                <span className="vsp-map-name">{note.title ?? note.ref}</span>
                <span className="vsp-map-count">{`${note.links} linked`}</span>
                <span
                  className="vsp-map-bar"
                  aria-hidden="true"
                  style={{ inlineSize: `${((note.links ?? 0) / mostLinks) * 100}%` }}
                />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="vsp-meta vsp-coverage">{coverage}</p>
      <section className="vsp-topics" aria-labelledby={`${headingId}-topics`}>
        <h3 id={`${headingId}-topics`}>{t("vault.block.topics")}</h3>
        {tags.length > 0 ? (
          <ul className="vsp-tags">
            {tags.map((tag) => (
              <li key={tag.tag}>
                <button type="button" className="vsp-tag" onClick={() => onTag(tag.tag)}>
                  #{tag.tag} ({tag.count})
                </button>
              </li>
            ))}
          </ul>
        ) : overview ? (
          <p className="vsp-meta">No topics read.</p>
        ) : null}
      </section>
      <p className="vsp-hint">Pick a note in the list to read it here.</p>
    </section>
  );
}

export function VaultSectionPage({
  section,
  listing,
  overview,
  listingPartial,
  listingReady = true,
  listingStatus,
  selected = null,
  onOpen,
  onBrowse,
}: VaultSectionPageProps) {
  const [sort, setSort] = useState<"recent" | "name">("recent");
  const [page, setPage] = useState({ section, sort, count: PAGE_SIZE });
  const shown = page.section === section && page.sort === sort ? page.count : PAGE_SIZE;

  if (section === "else") {
    const listingNotes: ListingNote[] = listing.map((row) => ({
      ref: row.ref,
      title: row.title,
      bytes: 0,
      modified: row.modified,
    }));
    const groups = otherFolders(listingNotes, listingPartial);
    const latest = latestByFolder(listing);
    return (
      <div className="vsp">
        <header className="vsp-head">
          <div className="vsp-title">
            <h2>{t("vault.nav.else")}</h2>
            <p className="vsp-meta">{t("vault.intro.else")}</p>
          </div>
        </header>
        {!listingReady ? (
          listingStatus
        ) : groups.length === 0 ? (
          <p className="vsp-empty">{t("vault.empty.else")}</p>
        ) : (
          <div className="vsp-groups">
            {groups.slice(0, shown).map((group) => {
              const newest = changedDate(group.newest);
              const latestRow = latest.get(group.folder);
              return (
                <section className="vsp-group" key={group.folder}>
                  <h3>{group.folder}</h3>
                  <p className="vsp-meta">
                    <code className="vsp-ref">{group.folder}/</code>
                    {" · "}
                    <span>{countLabel(group.count, group.partial)}</span>
                  </p>
                  {newest ? (
                    <p className="vsp-meta">{tf("vault.time.changed", { date: newest })}</p>
                  ) : null}
                  <p className="vsp-meta">{t("vault.listing.unread")}</p>
                  {latestRow ? (
                    <NoteCard
                      noteRef={latestRow.ref}
                      listing={{ title: latestRow.title, modified: latestRow.modified ?? 0 }}
                      variant="compact"
                      kind="row"
                      current={selected === latestRow.ref}
                      hideSection
                      onOpen={onOpen}
                    />
                  ) : null}
                  <button
                    type="button"
                    className="vsp-action"
                    onClick={() => onBrowse(group.folder)}
                  >
                    {t("vault.action.show_all")}
                  </button>
                </section>
              );
            })}
          </div>
        )}
        {groups.length > shown ? (
          <button
            type="button"
            className="vsp-action"
            onClick={() => setPage({ section, sort, count: shown + PAGE_SIZE })}
          >
            {t("vault.action.show_more")}
          </button>
        ) : null}
      </div>
    );
  }

  const config = COVERED_SECTIONS[section];
  const folderPrefix = `${folderOfSection(section)}/`;
  const rows = listing.filter((row) => row.ref.startsWith(folderPrefix));
  const sorted = [...rows].sort(
    sort === "recent" ? compareRecent : (a, b) => compareText(a.ref, b.ref),
  );
  const details = new Map((overview?.notes ?? []).map((note) => [note.ref, note]));

  return (
    <div className="vsp">
      <header className="vsp-head">
        <div className="vsp-title">
          <h2>{t(config.label)}</h2>
          <p className="vsp-meta">
            {listingReady ? (
              <span className="vsp-count">{countLabel(rows.length, listingPartial)}</span>
            ) : null}
            {listingReady ? " · " : null}
            <code className="vsp-ref">{folderPrefix}</code>
          </p>
        </div>
        <label className="vsp-sort">
          {t("vault.sort.label")}
          <select
            value={sort}
            onChange={(event) => {
              const next = event.currentTarget.value === "name" ? "name" : "recent";
              setSort(next);
              setPage({ section, sort: next, count: PAGE_SIZE });
            }}
          >
            <option value="recent">{t("vault.sort.recent")}</option>
            <option value="name">{t("vault.sort.name")}</option>
          </select>
        </label>
      </header>
      {!listingReady ? (
        listingStatus
      ) : rows.length === 0 ? (
        <p className="vsp-empty">
          {listingPartial ? t("vault.empty.partial") : t("vault.empty.section")} ({folderPrefix})
        </p>
      ) : (
        <ul className="vsp-note-list" aria-label={`Notes in ${folderPrefix.slice(0, -1)}`}>
          {sorted.slice(0, shown).map((row) => {
            const metadata = details.get(row.ref);
            return (
              <li key={row.ref}>
                <NoteCard
                  {...(metadata ? { overview: metadata } : {})}
                  noteRef={row.ref}
                  listing={{ title: row.title, modified: row.modified ?? 0 }}
                  variant="compact"
                  kind="row"
                  current={selected === row.ref}
                  hideSection
                  onOpen={onOpen}
                />
              </li>
            );
          })}
        </ul>
      )}
      {sorted.length > shown ? (
        <button
          type="button"
          className="vsp-action"
          onClick={() => setPage({ section, sort, count: shown + PAGE_SIZE })}
        >
          {t("vault.action.show_more")}
        </button>
      ) : null}
    </div>
  );
}
