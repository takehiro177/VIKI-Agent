import * as Dropdown from "@radix-ui/react-dropdown-menu";
import {
  type DragEvent,
  type KeyboardEvent,
  type ReactElement,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  type SessionMetadata,
  type SessionMetadataApi,
  SessionMetadataError,
  type SessionMetadataPatch,
} from "../../clients/conversations/sessionMetadataApi";
import { isArchiveOnly } from "../../state/sessionOrganization";
import { RootLabel } from "../RootLabel/RootLabel";
import "./Sessions.css";

const LABELS = {
  rename: "Rename",
  pin: "Pin",
  unpin: "Unpin",
  archive: "Archive",
  unarchive: "Unarchive",
  save: "Save",
  reload: "Reload",
  close: "Close",
  pinTab: "Pin",
  error: "The session changed elsewhere. Reload before saving.",
};

export interface SessionMetadataStore {
  sessions: readonly SessionMetadata[];
  loading: boolean;
  pending: ReadonlySet<string>;
  error: string | null;
  reload(): Promise<void>;
  update(sessionId: string, patch: SessionMetadataPatch): Promise<SessionMetadata | null>;
  remove(sessionId: string): Promise<boolean>;
  /** Delete a label only when `isArchiveOnly` holds for its freshest known row, quietly: it
   * never sets `error`. False with no request when there is no api, no known row, the row is
   * not archive-only, or it is already pending; false if the delete throws, keeping the row;
   * true once the row is dropped locally, whether the server deleted it or had already lost it
   * (404). */
  clearArchiveOnly(sessionId: string): Promise<boolean>;
}

export function useSessionMetadata(api: SessionMetadataApi | undefined): SessionMetadataStore {
  const [sessions, setSessions] = useState<readonly SessionMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const readApi = useRef(api);
  const latest = useRef(sessions);
  latest.current = sessions;
  const pendingNow = useRef(new Set<string>());
  const removedIds = useRef(new Map<string, number>());
  const generation = useRef(0);
  const reload = useCallback(async () => {
    if (!api) {
      readApi.current = api;
      setLoading(false);
      return;
    }
    const stamp = ++generation.current;
    setLoading(true);
    try {
      const rows = await api.list();
      if (stamp !== generation.current) return;
      const visible = rows.filter((row) => {
        const removedAt = removedIds.current.get(row.session_id);
        if (removedAt === undefined) return true;
        if (stamp > removedAt) {
          removedIds.current.delete(row.session_id);
          return true;
        }
        return false;
      });
      const merged = visible.map((row) => {
        const newer = latest.current.find((item) => item.session_id === row.session_id);
        return newer && newer.revision > row.revision ? newer : row;
      });
      for (const row of latest.current)
        if (!merged.some((item) => item.session_id === row.session_id)) merged.push(row);
      latest.current = merged;
      setSessions(merged);
      setError(null);
    } catch {
      if (stamp === generation.current) setError("load_failed");
    } finally {
      if (stamp === generation.current) {
        readApi.current = api;
        setLoading(false);
      }
    }
  }, [api]);
  useEffect(() => {
    void reload();
    return () => {
      generation.current += 1;
    };
  }, [reload]);
  const update = useCallback(
    async (sessionId: string, patch: SessionMetadataPatch) => {
      if (!api || pendingNow.current.has(sessionId)) return null;
      const row = latest.current.find((item) => item.session_id === sessionId);
      pendingNow.current.add(sessionId);
      setPending(new Set(pendingNow.current));
      try {
        const next = await api.patch(sessionId, row?.revision ?? 0, patch);
        const updated = latest.current.filter((item) => item.session_id !== sessionId);
        updated.push(next);
        latest.current = updated;
        setSessions(updated);
        setError(null);
        return next;
      } catch (cause) {
        setError(
          cause instanceof SessionMetadataError && cause.status === 409
            ? "conflict"
            : "update_failed",
        );
        return null;
      } finally {
        pendingNow.current.delete(sessionId);
        setPending(new Set(pendingNow.current));
      }
    },
    [api],
  );
  const remove = useCallback(
    async (sessionId: string) => {
      if (!api || pendingNow.current.has(sessionId)) return false;
      const row = latest.current.find((item) => item.session_id === sessionId);
      pendingNow.current.add(sessionId);
      setPending(new Set(pendingNow.current));
      try {
        const removed = await api.remove(sessionId, row?.revision ?? 0);
        removedIds.current.set(sessionId, generation.current);
        const updated = latest.current.filter((item) => item.session_id !== sessionId);
        latest.current = updated;
        setSessions(updated);
        setError(null);
        return removed;
      } catch (cause) {
        setError(
          cause instanceof SessionMetadataError && cause.status === 409
            ? "conflict"
            : "remove_failed",
        );
        return false;
      } finally {
        pendingNow.current.delete(sessionId);
        setPending(new Set(pendingNow.current));
      }
    },
    [api],
  );
  const clearArchiveOnly = useCallback(
    async (sessionId: string) => {
      const row = latest.current.find((item) => item.session_id === sessionId);
      if (!api || !row || !isArchiveOnly(row) || pendingNow.current.has(sessionId)) return false;
      pendingNow.current.add(sessionId);
      setPending(new Set(pendingNow.current));
      try {
        await api.remove(sessionId, row.revision);
        removedIds.current.set(sessionId, generation.current);
        const updated = latest.current.filter((item) => item.session_id !== sessionId);
        latest.current = updated;
        setSessions(updated);
        return true;
      } catch {
        return false;
      } finally {
        pendingNow.current.delete(sessionId);
        setPending(new Set(pendingNow.current));
      }
    },
    [api],
  );
  return {
    sessions,
    loading: loading || readApi.current !== api,
    pending,
    error,
    reload,
    update,
    remove,
    clearArchiveOnly,
  };
}

export interface SessionOrganizerProps {
  session: SessionMetadata;
  onPatch: (patch: SessionMetadataPatch) => Promise<SessionMetadata | null>;
  onReload?: () => void;
  pending?: boolean;
  errorCode?: string | null;
}
export function SessionOrganizer({
  session,
  onPatch,
  onReload,
  pending = false,
  errorCode,
}: SessionOrganizerProps): ReactElement {
  const text = LABELS;
  const [title, setTitle] = useState(session.title);
  const [error, setError] = useState(false);
  useEffect(() => {
    setTitle(session.title);
  }, [session.title]);
  async function patch(patch: SessionMetadataPatch): Promise<void> {
    const result = await onPatch(patch);
    setError(result === null);
  }
  return (
    <section className="session-organizer" aria-label={text.rename}>
      <input
        value={title}
        aria-label={text.rename}
        maxLength={256}
        onChange={(event) => setTitle(event.target.value)}
      />
      <div className="session-organizer-actions">
        <button
          type="button"
          disabled={pending}
          onClick={() => void patch({ title: title.trim() })}
        >
          {text.save}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => void patch({ pinned: !session.pinned })}
        >
          {session.pinned ? text.unpin : text.pin}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => void patch({ archived: !session.archived })}
        >
          {session.archived ? text.unarchive : text.archive}
        </button>
      </div>
      {error ? (
        <p className="session-organizer-error" role="alert">
          {errorCode === "conflict"
            ? text.error
            : "Could not save. Reload and check the saved state."}{" "}
          <button type="button" onClick={onReload}>
            {text.reload}
          </button>
        </p>
      ) : null}
    </section>
  );
}

export interface TabMenuItem {
  label: string;
  /** A function runs only while the menu is open, so a check that measures the page runs then. */
  disabled?: boolean | (() => boolean);
  onSelect(): void;
}

/** A file tab's label data: `name` is its real filename (any case, extension included); `ref` is
 * the full path shown as the tab's tooltip; `dirty` shows the unsaved-changes dot. */
export interface SessionTabFile {
  name: string;
  dirty: boolean;
  ref: string;
}

export interface SessionTabsProps {
  /** A row carrying `file` renders as a file tab: its name splits into a shrinking stem and a
   * never-cut extension, shown in its real case. */
  sessions: readonly (SessionMetadata & { file?: SessionTabFile })[];
  selectedId: string | null;
  onSelect: (sessionId: string) => void;
  onClose: (sessionId: string) => void;
  /** The folder a session runs in, shown inside its tab after the title. */
  folderOf?: (sessionId: string) => string | null;
  /** Pinned tabs show no close button. */
  pinned?: ReadonlySet<string>;
  onPin?: (sessionId: string, pinned: boolean) => void;
  /** Move a tab before `beforeId`, or to the end of its group when null. `onMove` also gets ids
   * from other strips and the rail. */
  onMove?: (sessionId: string, beforeId: string | null) => void;
  /** A non-empty list adds a "Tab actions" menu button after the pin button. */
  menuFor?: (sessionId: string) => readonly TabMenuItem[];
  /** When true, the selected tab's label also carries `.conversation-title` (ConversationHeader's `tabs` mode). */
  asTitle?: boolean;
  /** Names the tablist `${paneLabel} tabs`; otherwise the existing language default. */
  paneLabel?: string;
  /** The pane's own active tab. Unless it is also `selectedId`, it gets `data-active` and a
   * dimmed selected look (a pane without the focus); `aria-selected` stays with `selectedId`. */
  activeId?: string | null;
}
const TAB_DRAG_TYPE = "text/x-viki-tab";

/** A file tab's name split for display: the extension is from the last "." onward, unless that
 * "." is the name's first character (a dotfile has no extension). */
function splitFileName(name: string): { stem: string; ext: string } {
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? { stem: name, ext: "" } : { stem: name.slice(0, dot), ext: name.slice(dot) };
}

// These buttons are siblings of the tab button, so nothing but App's window listener sees their
// keys. Every key but Escape goes on to it (the shortcuts); Escape stays with the button, as it
// did before the tab strip had a pin and a tab-actions menu button.
function stopEscape(event: KeyboardEvent<HTMLElement>): void {
  if (event.key === "Escape") event.stopPropagation();
}

/** The tab's "Tab actions" menu: the same Radix open handling as OrganizedSessionMenu (a
 * controlled `open` plus a pointerdown/click guard so Radix's own pointerdown toggle and the
 * trailing click never double-fire) — except only Escape stays local, like the tab's other
 * buttons. */
function TabMenu({
  title,
  selected,
  items,
}: {
  title: string;
  selected: boolean;
  items: readonly TabMenuItem[];
}): ReactElement {
  const [open, setOpen] = useState(false);
  const pointerClick = useRef(false);
  return (
    <Dropdown.Root open={open} onOpenChange={setOpen} modal={false}>
      <Dropdown.Trigger
        type="button"
        className="session-tab-menu"
        aria-label={`Tab actions: ${title}`}
        tabIndex={selected ? 0 : -1}
        onKeyDown={stopEscape}
        onPointerDown={(event) => {
          pointerClick.current = true;
          event.stopPropagation();
        }}
        onClick={(event) => {
          event.stopPropagation();
          // Radix already toggled on pointerdown; its trailing click must not reopen it.
          if (!pointerClick.current && !open) setOpen(true);
          pointerClick.current = false;
        }}
      >
        ⋯
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content className="menu-panel">
          {/* Built only while open: a `disabled` function runs then, not on every render. */}
          {open
            ? items.map((item) => (
                <Dropdown.Item
                  key={item.label}
                  className="mi"
                  disabled={
                    typeof item.disabled === "function" ? item.disabled() : (item.disabled ?? false)
                  }
                  onSelect={item.onSelect}
                >
                  {item.label}
                </Dropdown.Item>
              ))
            : null}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}

export function SessionTabs({
  sessions,
  selectedId,
  onSelect,
  onClose,
  folderOf,
  pinned,
  onPin,
  onMove,
  menuFor,
  asTitle,
  paneLabel,
  activeId,
}: SessionTabsProps): ReactElement {
  const text = LABELS;
  const groupOf = (id: string) => pinned?.has(id) ?? false;
  const dropOn = (event: DragEvent<HTMLElement>, beforeId: string | null) => {
    const id = event.dataTransfer.getData(TAB_DRAG_TYPE);
    if (!onMove || !id) return;
    event.preventDefault();
    event.stopPropagation();
    if (id === beforeId) return;
    const from = sessions.findIndex((item) => item.session_id === id);
    const to = sessions.findIndex((item) => item.session_id === beforeId);
    if (beforeId !== null && from >= 0 && from < to && groupOf(id) === groupOf(beforeId)) {
      // A tab dragged right goes after its target: before the next tab of the group, else last.
      const next = sessions[to + 1]?.session_id;
      onMove(id, next !== undefined && groupOf(next) === groupOf(id) ? next : null);
      return;
    }
    onMove(id, beforeId);
  };
  return (
    <div
      className="session-tabs"
      role="tablist"
      aria-label={paneLabel ? `${paneLabel} tabs` : "Sessions"}
      onDragOver={onMove ? (event) => event.preventDefault() : undefined}
      onDrop={onMove ? (event) => dropOn(event, null) : undefined}
    >
      {sessions.map((session) => {
        const folder = folderOf?.(session.session_id) ?? null;
        const isPinned = pinned?.has(session.session_id) ?? false;
        const title = session.title || session.session_id;
        const menuItems = menuFor?.(session.session_id) ?? [];
        const file = session.file;
        const fileName = file ? splitFileName(file.name) : null;
        return (
          // biome-ignore lint/a11y/noStaticElementInteractions: a drag source and drop target around real buttons; the keyboard move is Ctrl+Shift+Arrow
          <div
            className="session-tab"
            role="presentation"
            key={session.session_id}
            data-selected={session.session_id === selectedId}
            data-active={
              (session.session_id === activeId && session.session_id !== selectedId) || undefined
            }
            draggable={onMove ? true : undefined}
            onDragStart={
              onMove
                ? (event) => event.dataTransfer.setData(TAB_DRAG_TYPE, session.session_id)
                : undefined
            }
            onDrop={onMove ? (event) => dropOn(event, session.session_id) : undefined}
          >
            <button
              type="button"
              className="session-tab-select"
              role="tab"
              id={`session-tab-${session.session_id}`}
              aria-controls={
                session.session_id === selectedId
                  ? `conversation-panel-${session.session_id}`
                  : undefined
              }
              aria-selected={session.session_id === selectedId}
              tabIndex={session.session_id === selectedId ? 0 : -1}
              onClick={() => onSelect(session.session_id)}
              onKeyDown={(event) => {
                if (event.target !== event.currentTarget || event.nativeEvent.isComposing) return;
                const index = sessions.findIndex((item) => item.session_id === session.session_id);
                if (
                  onMove &&
                  event.ctrlKey &&
                  event.shiftKey &&
                  (event.key === "ArrowLeft" || event.key === "ArrowRight")
                ) {
                  event.preventDefault();
                  const left = event.key === "ArrowLeft";
                  const neighbour = sessions[left ? index - 1 : index + 1];
                  if (neighbour)
                    onMove(
                      session.session_id,
                      left ? neighbour.session_id : (sessions[index + 2]?.session_id ?? null),
                    );
                  return;
                }
                const nextIndex =
                  event.key === "ArrowRight"
                    ? (index + 1) % sessions.length
                    : event.key === "ArrowLeft"
                      ? (index + sessions.length - 1) % sessions.length
                      : event.key === "Home"
                        ? 0
                        : event.key === "End"
                          ? sessions.length - 1
                          : null;
                if (nextIndex !== null) {
                  event.preventDefault();
                  const next = sessions[nextIndex];
                  if (next) onSelect(next.session_id);
                  const nodes = event.currentTarget
                    .closest('[role="tablist"]')
                    ?.querySelectorAll<HTMLElement>('[role="tab"]');
                  nodes?.[nextIndex]?.focus();
                  return;
                }
                if (event.key === "Delete") {
                  event.preventDefault();
                  if (!isPinned) onClose(session.session_id);
                  return;
                }
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(session.session_id);
                }
              }}
            >
              <span
                className={
                  asTitle && session.session_id === selectedId
                    ? "session-tab-label conversation-title"
                    : "session-tab-label"
                }
                title={file?.ref}
              >
                {file && fileName ? (
                  <>
                    <span className="session-tab-stem">{fileName.stem}</span>
                    <span className="session-tab-ext">{fileName.ext}</span>
                    {file.dirty ? (
                      <span className="session-tab-dirty" role="img" aria-label="Unsaved changes">
                        ●
                      </span>
                    ) : null}
                  </>
                ) : (
                  session.title || session.session_id
                )}
              </span>
              {folder ? (
                <>
                  {" "}
                  <RootLabel text={folder} />
                </>
              ) : null}
            </button>
            {onPin ? (
              <button
                type="button"
                className="session-tab-pin"
                tabIndex={session.session_id === selectedId ? 0 : -1}
                aria-pressed={isPinned}
                aria-label={`${text.pinTab}: ${title}`}
                data-pin-for={session.session_id}
                onKeyDown={stopEscape}
                onClick={() => onPin(session.session_id, !isPinned)}
              >
                {isPinned ? "●" : "○"}
              </button>
            ) : null}
            {menuItems.length > 0 ? (
              <TabMenu
                title={title}
                selected={session.session_id === selectedId}
                items={menuItems}
              />
            ) : null}
            {isPinned ? null : (
              <button
                type="button"
                className="session-tab-close"
                tabIndex={session.session_id === selectedId ? 0 : -1}
                aria-label={`${text.close}: ${title}`}
                onKeyDown={stopEscape}
                onClick={(event) => {
                  event.stopPropagation();
                  onClose(session.session_id);
                }}
              >
                ×
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
