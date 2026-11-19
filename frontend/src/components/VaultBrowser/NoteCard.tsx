import { useId } from "react";
import { t, tf } from "../../i18n/t";
import type { OverviewNote, VaultEntry } from "../../state/vaultApi";
import { FOLDER_SECTIONS, fallbackTitle } from "../../state/vaultOverviewStats";
import "./NoteCard.css";

export type NoteCardKind =
  | "lead"
  | "recent"
  | "rediscover"
  | "map"
  | "bookmark"
  | "row"
  | "link"
  | "backlink";

export interface NoteCardProps {
  noteRef: string;
  listing?: Pick<VaultEntry, "title" | "modified">;
  overview?: OverviewNote;
  variant: "lead" | "card" | "compact";
  kind: NoteCardKind;
  onOpen: (noteRef: string, kind: NoteCardKind, origin: HTMLElement) => void;
  marked?: boolean;
  onToggleMark?: (noteRef: string) => void;
  disabled?: boolean;
  /** The note open in the reader beside this list. */
  current?: boolean;
  /** Set where the list already names the section, so each card says only a non-default type. */
  hideSection?: boolean;
}

/** One data colour per settled, growing or open state; the tone names the colour token. */
export function statusTone(
  status: string | null | undefined,
): "settled" | "growing" | "open" | null {
  switch (status?.toLowerCase()) {
    case "evergreen":
    case "processed":
    case "done":
      return "settled";
    case "draft":
    case "seed":
      return "growing";
    case "unread":
    case "active":
      return "open";
    default:
      return null;
  }
}

/** The path repeats the title when the note sits directly in its folder under its own file name. */
function refRepeatsTitle(noteRef: string, title: string): boolean {
  const parts = noteRef.split("/");
  const file = (parts.at(-1) ?? "").replace(/\.(?:md|markdown)$/i, "");
  return parts.length === 2 && file.toLowerCase() === title.toLowerCase();
}

function genericLabel(value: string): boolean {
  return /^(?:skill|readme|index)(?:\.(?:md|markdown))?$/i.test(value);
}

function titleOf(
  noteRef: string,
  listing: NoteCardProps["listing"],
  overview: OverviewNote | undefined,
): string {
  if (overview?.title && /\P{White_Space}/u.test(overview.title)) return overview.title;
  if (listing?.title && /\P{White_Space}/u.test(listing.title) && !genericLabel(listing.title))
    return listing.title;
  return fallbackTitle(noteRef);
}

function sectionLabel(folder: string | null): string | null {
  if (!folder) return null;
  if (!Object.hasOwn(FOLDER_SECTIONS, folder)) return folder;
  const entry = FOLDER_SECTIONS[folder as keyof typeof FOLDER_SECTIONS];
  if (entry.key === "knowledge") return t("vault.nav.knowledge");
  if (entry.key === "sources") return t("vault.nav.sources");
  if (entry.key === "maps") return t("vault.nav.maps");
  return t(`vault.folder.${entry.key}`);
}

function changedAt(modified: number | undefined): string | null {
  if (modified === undefined || !Number.isFinite(modified) || modified <= 0) return null;
  const date = new Date(modified * 1000);
  return Number.isNaN(date.getTime()) ? null : date.toISOString().slice(0, 10);
}

function NoteDate({
  listing,
  overview,
}: {
  listing: NoteCardProps["listing"] | undefined;
  overview: OverviewNote | undefined;
}) {
  const created = overview?.created ?? null;
  const modified = overview?.modified ?? null;
  if (created && modified) {
    const added = created >= modified;
    const date = added ? created : modified;
    const label = tf(added ? "vault.time.added" : "vault.time.updated", { date });
    return (
      <time className="note-card-time" dateTime={date}>
        {label}
      </time>
    );
  }
  if (created)
    return (
      <time className="note-card-time" dateTime={created}>
        {tf("vault.time.added", { date: created })}
      </time>
    );
  if (modified)
    return (
      <time className="note-card-time" dateTime={modified}>
        {tf("vault.time.updated", { date: modified })}
      </time>
    );
  const changed = changedAt(listing?.modified);
  return changed ? (
    <time className="note-card-time" dateTime={changed}>
      {tf("vault.time.changed", { date: changed })}
    </time>
  ) : null;
}

export function NoteCard({
  noteRef,
  listing,
  overview,
  variant,
  kind,
  onOpen,
  marked = false,
  onToggleMark,
  disabled = false,
  current = false,
  hideSection = false,
}: NoteCardProps) {
  const id = useId();
  const metadata = overview?.ref === noteRef ? overview : undefined;
  const title = titleOf(noteRef, listing, metadata);
  const folder = noteRef.includes("/") ? noteRef.slice(0, noteRef.indexOf("/")) : null;
  const entry =
    folder && Object.hasOwn(FOLDER_SECTIONS, folder)
      ? FOLDER_SECTIONS[folder as keyof typeof FOLDER_SECTIONS]
      : undefined;
  const section = hideSection ? null : sectionLabel(folder);
  const defaultType = entry && "defaultType" in entry ? entry.defaultType : null;
  const type = metadata?.type;
  const kicker = [section, type && type.toLowerCase() !== defaultType ? type : null]
    .filter((part): part is string => Boolean(part))
    .join(" · ");
  const unavailable = metadata?.state === "unsupported" || metadata?.state === "truncated";
  const tags = metadata?.tags?.slice(0, 3) ?? [];
  const tone = statusTone(metadata?.status);

  return (
    <article className={`note-card note-card-${variant}`} {...(tone ? { "data-tone": tone } : {})}>
      {kicker ? <p className="note-card-kicker">{kicker}</p> : null}
      <button
        type="button"
        className="note-card-title"
        data-vault-ref={noteRef}
        data-vault-kind={kind}
        aria-describedby={`${id}-ref`}
        aria-current={current || undefined}
        onClick={(event) => onOpen(noteRef, kind, event.currentTarget)}
      >
        {title}
      </button>
      {metadata?.summary ? <p className="note-card-summary">{metadata.summary}</p> : null}
      <div className="note-card-facts">
        {metadata?.status || metadata?.confidence ? (
          <p className="note-card-meta">
            {[metadata.status, metadata.confidence ? `${metadata.confidence} confidence` : null]
              .filter((part): part is string => Boolean(part))
              .join(" · ")}
          </p>
        ) : null}
        <NoteDate listing={listing} overview={metadata} />
        {tags.length > 0 ? (
          <ul className="note-card-tags">
            {tags.map((tag) => (
              <li key={tag}>{tag}</li>
            ))}
          </ul>
        ) : null}
      </div>
      <small
        id={`${id}-ref`}
        className="note-card-ref"
        hidden={refRepeatsTitle(noteRef, title) || undefined}
      >
        {noteRef}
      </small>
      {unavailable ? <p className="note-card-quiet">{t("vault.card.header")}</p> : null}
      {!metadata ? <p className="note-card-quiet">{t("vault.card.unread")}</p> : null}
      {onToggleMark ? (
        <button
          type="button"
          className="note-card-bookmark"
          aria-label={`${t(marked ? "vault.reader.remove_bookmark" : "vault.reader.bookmark")} ${noteRef}`}
          aria-pressed={marked}
          disabled={disabled}
          onClick={() => onToggleMark(noteRef)}
        >
          {marked ? "★" : "☆"}
        </button>
      ) : null}
    </article>
  );
}
