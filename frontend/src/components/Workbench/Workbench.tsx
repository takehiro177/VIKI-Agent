import {
  type CSSProperties,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { WorkspaceJson } from "../../clients/app/shellApi";
import type { ChecksApi } from "../../clients/code/checksApi";
import {
  type ChangeEntry,
  type CodeApi,
  CodeApiError,
  type CodeDiff,
  type CodeFile,
} from "../../clients/code/codeApi";
import type { PreviewsApi } from "../../clients/code/previewsApi";
import type { ReviewApi } from "../../clients/code/reviewApi";
import type { RootsApi } from "../../clients/code/rootsApi";
import type { TerminalApi } from "../../clients/code/terminalApi";
import { t } from "../../i18n/t";
import { type CodeBufferBase, dirtyRefs, getBuffer, useCodeBuffers } from "../../state/codeBuffers";
import { clampResize } from "../../state/layout";
import { ResizeHandle } from "../Shell/ResizeHandle";
import { ChangesList } from "./ChangesList";
import { Checks } from "./Checks";
import { CodeEditor } from "./CodeEditor";
import { CodeRecovery, type CodeRecoveryApi } from "./CodeRecovery";
import { DiffView } from "./DiffView";
import { FileTree } from "./FileTree";
import { FileView } from "./FileView";
import { Previews } from "./Previews";
import { QuickOpen } from "./QuickOpen";
import { Review } from "./Review";
import { MAX_SESSION_FOLDERS, SessionFolders } from "./SessionFolders";
import { TerminalLauncher } from "./TerminalLauncher";
import { rootRefusal, Worktrees } from "./Worktrees";
import "./Workbench.css";

/** Sessions in this folder and its worktrees. `onStartSession` answers null once the session has
 * started, else the refusal's code, which the view shows beside the button that asked. */
export interface WorkbenchRoots {
  api: RootsApi;
  onStartSession(root: {
    workspace_id: string;
    worktree_id: string | null;
    additional_workspace_ids?: readonly string[];
  }): Promise<string | null>;
  onRaiseLimit?(): void;
}

/** The Review tab: the folder's or a worktree's changes, to commit, integrate or push. */
export interface WorkbenchReview {
  api: ReviewApi;
  onOpenUrl(url: string): void;
}

export interface WorkbenchProps {
  api: CodeApi;
  folders: readonly WorkspaceJson[];
  activeId: string | null;
  onQuote: (text: string) => void;
  onOpenInEditor: (workspaceId: string, path: string, line: number | null) => void;
  onOpenSettings: () => void;
  onBack: () => void;
  renderBack?: (onBack: () => void) => ReactNode;
  fontSize: number;
  lineWrap: boolean;
  recovery?: CodeRecoveryApi;
  roots?: WorkbenchRoots;
  review?: WorkbenchReview;
  checks?: WorkbenchChecks;
  terminal?: WorkbenchTerminal;
  previews?: WorkbenchPreviews;
}

/** The Checks tab: the commands the folder keeps, run in it or in a worktree. */
export interface WorkbenchChecks {
  api: ChecksApi;
  canRun: boolean;
}

/** The Terminal tab: opens a terminal window in the folder or one of its worktrees. */
export interface WorkbenchTerminal {
  api: TerminalApi;
  canOpen: boolean;
}

/** The Previews tab: the dev servers the folder runs, started, listed and opened from here. */
export interface WorkbenchPreviews {
  api: PreviewsApi;
  canRun: boolean;
}

type Tab =
  | "files"
  | "changes"
  | "search"
  | "worktrees"
  | "review"
  | "checks"
  | "terminal"
  | "previews";
type Shown =
  | { phase: "loading"; path: string }
  | { phase: "error"; path: string; code: string }
  | {
      phase: "ready";
      path: string;
      file: CodeFile | null;
      diff: CodeDiff | null;
      diffFailed: boolean;
      editing: boolean;
      /** The file could not be read, so `file` is built from an unsaved buffer's base. */
      readError: string | null;
    };
type FileTab = { ref: string; withDiff: boolean; mode: "diff" | "file"; editing: boolean };

const TABS: readonly Tab[] = [
  "files",
  "changes",
  "search",
  "worktrees",
  "review",
  "checks",
  "terminal",
  "previews",
];
// The save route refuses a longer path, so Edit does not offer one. Counted in code points, as
// the server's `len(ref)` counts them (an astral character is two UTF-16 units).
const MAX_EDIT_PATH = 512;

function sameFolder(left: WorkspaceJson, right: WorkspaceJson): boolean {
  return (
    left.workspace_id === right.workspace_id &&
    left.root === right.root &&
    left.root_physical_id === right.root_physical_id &&
    left.policy_revision === right.policy_revision
  );
}

/** The file as its unsaved buffer last saw it, for a file that can no longer be read. */
function fileFromBase(path: string, base: CodeBufferBase): CodeFile {
  return {
    path,
    size: base.text.length,
    sha256: base.sha256,
    encoding: base.encoding,
    newline: base.newline,
    text: base.text,
    reason: null,
  };
}

const copy = {
  title: "Code",
  back: "Back to chat",
  folder: "Code folder",
  trusted: "Trusted",
  untrusted: "Not trusted",
  none: "No code folders yet. Open one in Settings → Workspaces.",
  settings: "Open Workspaces settings",
  tabs: {
    files: "Files",
    changes: "Changes",
    search: "Search",
    worktrees: "Worktrees",
    review: "Review",
    checks: "Checks",
    terminal: "Terminal",
    previews: "Previews",
  },
  startHere: "New session in this folder",
  empty: "Choose a file to view it.",
  refresh: "Refresh",
  diff: "Diff",
  file: "File",
  diffFailed: "The diff could not be loaded.",
  edit: "Edit",
  editBlocked: {
    trust: "Trust this folder to edit its files.",
    root: "This folder can't be saved to now.",
    mixed: "This file mixes line endings, so it opens read-only. Edit it in VS Code.",
    path: "This file's path is too long to save from VIKI.",
  },
  unsaved: "Unsaved changes",
  unsavedMark: "unsaved",
  closeHint: "Save or revert first",
  errors: {
    private_path: "This file is private and is not shown.",
    not_found: "This file no longer exists.",
    other: "This file could not be opened.",
  } as Record<string, string>,
};

export function Workbench({
  api,
  folders,
  activeId,
  onQuote,
  onOpenInEditor,
  onOpenSettings,
  onBack,
  renderBack,
  fontSize,
  lineWrap,
  recovery,
  roots,
  review,
  checks,
  terminal,
  previews,
}: WorkbenchProps) {
  const workbenchRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const editButtonRef = useRef<HTMLButtonElement>(null);
  const focusAfterClose = useRef(false);
  const focusAfterTabClose = useRef(false);
  const load = useRef<AbortController | null>(null);
  const editHintId = useId();
  const startHintId = useId();
  const [starting, setStarting] = useState(false);
  const startRequest = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const [startRefusal, setStartRefusal] = useState<{ id: string; code: string } | null>(null);
  const [folderSelection, setFolderSelection] = useState<{
    primary: WorkspaceJson | null;
    additional: readonly WorkspaceJson[];
  }>({ primary: null, additional: [] });
  const [selectionChanged, setSelectionChanged] = useState(false);
  const [chosen, setChosen] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("files");
  const [reviewWorktree, setReviewWorktree] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [recoveryKey, setRecoveryKey] = useState(0);
  const [shown, setShown] = useState<Shown | null>(null);
  const [fileTabs, setFileTabs] = useState<{
    folder: WorkspaceJson | null;
    tabs: FileTab[];
    activeRef: string | null;
  }>({ folder: null, tabs: [], activeRef: null });
  useCodeBuffers(); // re-render when a buffer appears or goes: the list and the folder marks

  const ordered = [...folders].sort(
    (a, b) => Number(b.workspace_id === activeId) - Number(a.workspace_id === activeId),
  );
  const folder = ordered.find((f) => f.workspace_id === chosen) ?? ordered[0] ?? null;
  const folderId = folder?.workspace_id ?? null;
  const folderNow = useRef(folder);
  folderNow.current = folder;
  const currentTabs =
    folder && fileTabs.folder && sameFolder(folder, fileTabs.folder) ? fileTabs.tabs : [];
  const activeRef = currentTabs.length > 0 ? fileTabs.activeRef : null;
  const activeFileTab = currentTabs.find((item) => item.ref === activeRef);
  const mode = activeFileTab?.mode ?? "file";
  const trusted = folder?.policy === "ordinary";
  const startBlocked = !trusted
    ? rootRefusal("trust_required")
    : folder?.root_available === false
      ? rootRefusal("root_unavailable")
      : null;
  const additionalFolders =
    folderSelection.primary && folder && sameFolder(folderSelection.primary, folder)
      ? folderSelection.additional.filter((selected) =>
          folders.some(
            (current) =>
              sameFolder(selected, current) &&
              current.kind === "code" &&
              current.policy === "ordinary" &&
              current.persistent &&
              current.root_available,
          ),
        )
      : [];
  useEffect(() => {
    if (folderSelection.additional.length === additionalFolders.length) return;
    setFolderSelection({ primary: folder, additional: additionalFolders });
    setSelectionChanged(true);
  }, [additionalFolders, folder, folderSelection.additional.length]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: primary identity changes invalidate start feedback.
  useEffect(() => {
    setStartRefusal(null);
    return () => startRequest.current?.abort();
  }, [folderId, folder?.root, folder?.root_physical_id, folder?.policy_revision]);
  const wide = tab === "review" || tab === "checks" || tab === "terminal" || tab === "previews";
  const splitReady = folderId !== null && !wide;
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
    const host = workbenchRef.current;
    if (!host) return;
    const pane = host.querySelector<HTMLElement>(".workbench-pane");
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

  const unsavedRefs = folderId === null ? [] : dirtyRefs(folderId);
  // Review reads the checkout when it mounts, so it mounts on the tab's first visit and then stays,
  // hidden, to keep its message and ticks. Another folder or worktree is another visit.
  const reviewKey = folder ? `${folder.workspace_id}:${reviewWorktree ?? ""}` : null;
  const reviewVisited = useRef<string | null>(null);
  if (tab === "review") reviewVisited.current = reviewKey;
  else if (reviewVisited.current !== reviewKey) reviewVisited.current = null;

  useEffect(() => {
    mounted.current = true;
    headingRef.current?.focus();
    return () => {
      mounted.current = false;
      startRequest.current?.abort();
    };
  }, []);

  // Close unmounts the button that had focus; once the re-read settles, focus Edit or the heading.
  useEffect(() => {
    if (!focusAfterClose.current || shown === null || shown.phase === "loading") return;
    focusAfterClose.current = false;
    (editButtonRef.current ?? headingRef.current)?.focus();
  }, [shown]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: fileTabs signals a committed close.
  useLayoutEffect(() => {
    if (!focusAfterTabClose.current) return;
    focusAfterTabClose.current = false;
    (
      workbenchRef.current?.querySelector<HTMLButtonElement>(
        '.workbench-file-select[aria-pressed="true"]',
      ) ?? document.getElementById("workbench-tab-files")
    )?.focus();
  }, [fileTabs]);

  // A folder identity change drops its tabs and open file; unsaved buffers outlive this view.
  // biome-ignore lint/correctness/useExhaustiveDependencies: folder identity is the reset signal.
  useEffect(() => {
    setShown(null);
    setFileTabs({ folder, tabs: [], activeRef: null });
    return () => load.current?.abort();
  }, [folderId, folder?.root, folder?.root_physical_id, folder?.policy_revision]);

  function open(path: string, withDiff: boolean, restoreMode = false, exitEdit = false) {
    if (folder === null) return;
    // The viewer is hidden on a wide tab, so a file opened from the list above it needs Files.
    if (wide) setTab("files");
    if (!exitEdit) focusAfterClose.current = false;
    const previous = currentTabs.find((item) => item.ref === path);
    const requestedDiff = withDiff || (previous?.withDiff ?? false);
    const requestedMode = restoreMode ? (previous?.mode ?? "file") : withDiff ? "diff" : "file";
    const editing = !exitEdit && (previous?.editing ?? false);
    setFileTabs((current) => {
      const tabs = current.folder && sameFolder(current.folder, folder) ? current.tabs : [];
      const existing = tabs.find((item) => item.ref === path);
      const next: FileTab = {
        ref: path,
        withDiff: requestedDiff,
        mode: requestedMode,
        editing,
      };
      return {
        folder,
        tabs: existing ? tabs.map((item) => (item.ref === path ? next : item)) : [...tabs, next],
        activeRef: path,
      };
    });
    load.current?.abort();
    const controller = new AbortController();
    load.current = controller;
    setShown({ phase: "loading", path });
    const { signal } = controller;
    Promise.allSettled([
      api.file(folder.workspace_id, path, signal),
      requestedDiff ? api.diff(folder.workspace_id, path, signal) : Promise.resolve(null),
    ]).then(([file, diff]) => {
      if (
        signal.aborted ||
        load.current !== controller ||
        !folderNow.current ||
        !sameFolder(folderNow.current, folder)
      )
        return;
      const stored = getBuffer(folder.workspace_id, path);
      const diffOk = diff.status === "fulfilled" && diff.value !== null;
      if (file.status === "rejected" && (stored !== undefined || !diffOk)) {
        const code = file.reason instanceof CodeApiError ? file.reason.code : "other";
        if (stored === undefined) {
          setShown({ phase: "error", path, code });
          return;
        }
        // The unsaved edit stays reachable: the editor opens on the base it was made from.
        setFileTabs((current) => ({
          ...current,
          tabs: current.tabs.map((item) =>
            item.ref === path ? { ...item, mode: "file", editing: true } : item,
          ),
        }));
        setShown({
          phase: "ready",
          path,
          file: fileFromBase(path, stored.base),
          diff: null,
          diffFailed: false,
          editing: true,
          readError: code,
        });
        return;
      }
      const diffHasText = diff.status === "fulfilled" && diff.value?.text != null;
      const nextEditing =
        stored !== undefined ||
        (editing &&
          file.status === "fulfilled" &&
          file.value.text !== null &&
          file.value.newline !== "mixed" &&
          trusted &&
          Boolean(folder.root_physical_id) &&
          [...path].length <= MAX_EDIT_PATH);
      const nextMode = diffHasText && !nextEditing && requestedMode === "diff" ? "diff" : "file";
      setFileTabs((current) => ({
        ...current,
        tabs: current.tabs.map((item) =>
          item.ref === path ? { ...item, mode: nextMode, editing: nextEditing } : item,
        ),
      }));
      setShown({
        phase: "ready",
        path,
        file: file.status === "fulfilled" ? file.value : null,
        diff: diff.status === "fulfilled" ? diff.value : null,
        diffFailed: requestedDiff && diff.status === "rejected",
        editing: nextEditing,
        readError: null,
      });
    });
  }

  function setFileMode(next: "diff" | "file") {
    setFileTabs((current) => ({
      ...current,
      tabs: current.tabs.map((item) => (item.ref === activeRef ? { ...item, mode: next } : item)),
    }));
  }

  function closeFileTab(path: string) {
    if (folderId === null || getBuffer(folderId, path)) return;
    const index = currentTabs.findIndex((item) => item.ref === path);
    if (index < 0) return;
    const remaining = currentTabs.filter((item) => item.ref !== path);
    const next = path === activeRef ? (remaining[index] ?? remaining[index - 1] ?? null) : null;
    setFileTabs({ folder, tabs: remaining, activeRef: next?.ref ?? activeRef });
    focusAfterTabClose.current = true;
    if (path !== activeRef) return;
    load.current?.abort();
    load.current = null;
    if (next) open(next.ref, next.withDiff, true);
    else setShown(null);
  }

  function quote(ref: string, start: number, end: number) {
    onQuote(`\`${ref}:${start === end ? start : `${start}-${end}`}\``);
  }

  async function startHere() {
    if (!roots || folderId === null || startBlocked !== null || startRequest.current) return;
    const request = new AbortController();
    startRequest.current = request;
    const additionalIds = additionalFolders.map((additional) => additional.workspace_id);
    setStarting(true);
    setStartRefusal(null);
    try {
      const code = await roots.onStartSession({
        workspace_id: folderId,
        worktree_id: null,
        ...(additionalIds.length > 0 ? { additional_workspace_ids: additionalIds } : {}),
      });
      if (!request.signal.aborted && code !== null) setStartRefusal({ id: folderId, code });
    } catch {
      if (!request.signal.aborted) setStartRefusal({ id: folderId, code: "unavailable" });
    } finally {
      startRequest.current = null;
      if (mounted.current) setStarting(false);
    }
  }

  function viewer() {
    if (!folder || activeRef === null || shown === null || shown.path !== activeRef)
      return <p>{copy.empty}</p>;
    if (shown.phase === "loading") return null;
    if (shown.phase === "error") {
      return <p role="alert">{copy.errors[shown.code] ?? copy.errors.other}</p>;
    }
    const { file, diff } = shown;
    if (shown.editing && file) {
      return (
        <>
          {shown.readError !== null && (
            <p role="alert">{copy.errors[shown.readError] ?? copy.errors.other}</p>
          )}
          <CodeEditor
            key={`${folder.workspace_id}\0${file.path}`}
            api={api}
            workspaceId={folder.workspace_id}
            policyRevision={folder.policy_revision}
            // A buffer outlives the folder's state; with no id the save is refused and the edit kept.
            rootPhysicalId={folder.root_physical_id ?? ""}
            file={file}
            fileVerified={shown.readError === null}
            fontSize={fontSize}
            wrap={lineWrap}
            onBlocked={() => setRecoveryKey((k) => k + 1)}
            onClose={() => {
              focusAfterClose.current = true;
              open(file.path, false, false, true);
            }}
          />
        </>
      );
    }
    const editBlocked = !trusted
      ? copy.editBlocked.trust
      : !folder.root_physical_id
        ? copy.editBlocked.root
        : file?.newline === "mixed"
          ? copy.editBlocked.mixed
          : file && [...file.path].length > MAX_EDIT_PATH
            ? copy.editBlocked.path
            : null;
    return (
      <>
        {shown.diffFailed && <p role="alert">{copy.diffFailed}</p>}
        {file && (diff || file.text !== null) && (
          <div className="workbench-toggle">
            {diff &&
              (["diff", "file"] as const).map((m) => (
                <button
                  type="button"
                  key={m}
                  className="btn txt sm"
                  aria-pressed={mode === m}
                  onClick={() => setFileMode(m)}
                >
                  {copy[m]}
                </button>
              ))}
            {file.text !== null && (
              <>
                <button
                  type="button"
                  ref={editButtonRef}
                  className="btn txt sm"
                  aria-disabled={editBlocked !== null}
                  aria-describedby={editBlocked !== null ? editHintId : undefined}
                  onClick={() => {
                    if (editBlocked !== null) return;
                    setFileMode("file");
                    setFileTabs((current) => ({
                      ...current,
                      tabs: current.tabs.map((item) =>
                        item.ref === file.path ? { ...item, editing: true } : item,
                      ),
                    }));
                    setShown({ ...shown, editing: true });
                  }}
                >
                  {copy.edit}
                </button>
                {editBlocked !== null && (
                  <span id={editHintId} className="code-reason">
                    {editBlocked}
                  </span>
                )}
              </>
            )}
          </div>
        )}
        {diff && (mode === "diff" || !file) ? (
          <DiffView diff={diff} />
        ) : file ? (
          <FileView
            file={file}
            canQuote={folder.workspace_id === activeId}
            onQuote={quote}
            onOpen={(line) => onOpenInEditor(folder.workspace_id, file.path, line)}
          />
        ) : null}
      </>
    );
  }

  return (
    <div
      className="workbench"
      ref={workbenchRef}
      data-split={splitShown || undefined}
      data-open={activeRef && !wide ? "" : undefined}
      data-wide={wide ? "" : undefined}
      data-review={tab === "review" ? "" : undefined}
      data-wrap={lineWrap ? "" : undefined}
      style={
        {
          "--code-font-size": `${fontSize}px`,
          ...(splitShown && (splitPreview !== null || splitWanted !== null)
            ? { "--workbench-nav-width": `${splitValue}px` }
            : {}),
        } as CSSProperties
      }
    >
      {renderBack?.(onBack) ?? (
        <button type="button" className="btn txt sm" onClick={onBack}>
          {copy.back}
        </button>
      )}
      <h1 ref={headingRef} tabIndex={-1}>
        {copy.title}
      </h1>
      {folder === null ? (
        <div className="workbench-none">
          <p>{copy.none}</p>
          <button type="button" className="btn alt sm" onClick={onOpenSettings}>
            {copy.settings}
          </button>
        </div>
      ) : (
        <>
          <div className="workbench-folder">
            <label>
              {copy.folder}{" "}
              <select
                value={folder.workspace_id}
                disabled={starting}
                onChange={(event) => {
                  if (startRequest.current) return;
                  setChosen(event.target.value);
                  setFolderSelection({ primary: null, additional: [] });
                  setSelectionChanged(false);
                  setStartRefusal(null);
                }}
              >
                {ordered.map((f) => (
                  <option key={f.workspace_id} value={f.workspace_id}>
                    {dirtyRefs(f.workspace_id).length > 0
                      ? `${f.name} (${copy.unsavedMark})`
                      : f.name}
                  </option>
                ))}
              </select>
            </label>
            <span className="chip">{trusted ? copy.trusted : copy.untrusted}</span>
            {roots && (
              <>
                <button
                  type="button"
                  className="btn go sm"
                  disabled={starting}
                  aria-disabled={starting || startBlocked !== null}
                  aria-describedby={startBlocked !== null ? startHintId : undefined}
                  onClick={() => void startHere()}
                >
                  {additionalFolders.length > 0
                    ? "New session with selected folders"
                    : copy.startHere}
                </button>
                {startBlocked !== null && (
                  <span id={startHintId} className="code-reason">
                    {startBlocked}
                  </span>
                )}
                {startRefusal?.id === folderId && (
                  <>
                    <span role="alert" className="worktrees-refusal">
                      {rootRefusal(startRefusal.code)}
                    </span>
                    {startRefusal.code === "too_many_sessions" && roots.onRaiseLimit && (
                      <button type="button" className="btn txt sm" onClick={roots.onRaiseLimit}>
                        {t("shell.limit.change")}
                      </button>
                    )}
                  </>
                )}
              </>
            )}
          </div>
          {roots && (
            <SessionFolders
              primary={folder}
              folders={ordered}
              selectedIds={additionalFolders.map((additional) => additional.workspace_id)}
              disabled={starting}
              onChange={(workspaceId, selected) => {
                if (startRequest.current) return;
                const additional = folders.find((item) => item.workspace_id === workspaceId);
                if (
                  !additional ||
                  additional.workspace_id === folderId ||
                  additional.kind !== "code" ||
                  additional.policy !== "ordinary" ||
                  !additional.persistent ||
                  !additional.root_available
                )
                  return;
                const without = additionalFolders.filter(
                  (item) => item.workspace_id !== workspaceId,
                );
                if (selected && without.length >= MAX_SESSION_FOLDERS - 1) return;
                setFolderSelection({
                  primary: folder,
                  additional: selected ? [...without, additional] : without,
                });
                setSelectionChanged(false);
              }}
            />
          )}
          {selectionChanged && (
            <p role="status" className="code-reason">
              Selected folders changed. Review the folders before starting a conversation.
            </p>
          )}
          {unsavedRefs.length > 0 && (
            <fieldset className="workbench-unsaved">
              <legend>{copy.unsaved}</legend>
              <div className="workbench-unsaved-refs">
                {unsavedRefs.map((ref) => (
                  <button
                    type="button"
                    key={ref}
                    className="btn txt sm"
                    onClick={() => open(ref, false)}
                  >
                    {ref}
                  </button>
                ))}
              </div>
            </fieldset>
          )}
          {recovery && (
            <CodeRecovery
              recovery={recovery}
              workspaceId={folder.workspace_id}
              refreshKey={recoveryKey}
            />
          )}
          <div className="workbench-nav" id={`${editHintId}-navigation`}>
            <div role="tablist" className="workbench-tabs">
              {TABS.filter(
                (id) =>
                  (id !== "worktrees" || roots) &&
                  (id !== "review" || review) &&
                  (id !== "checks" || checks) &&
                  (id !== "terminal" || terminal) &&
                  (id !== "previews" || previews),
              ).map((id) => (
                <button
                  type="button"
                  role="tab"
                  key={id}
                  id={`workbench-tab-${id}`}
                  aria-controls={`workbench-panel-${id}`}
                  aria-selected={tab === id}
                  className="btn txt sm"
                  onClick={() => setTab(id)}
                >
                  {copy.tabs[id]}
                </button>
              ))}
            </div>
            <div
              role="tabpanel"
              id="workbench-panel-files"
              aria-labelledby="workbench-tab-files"
              hidden={tab !== "files"}
            >
              <FileTree
                api={api}
                workspaceId={folder.workspace_id}
                selected={activeRef}
                onSelectFile={(ref) => open(ref, false)}
              />
            </div>
            {tab === "changes" && (
              <div
                role="tabpanel"
                id="workbench-panel-changes"
                aria-labelledby="workbench-tab-changes"
              >
                <button
                  type="button"
                  className="btn txt sm"
                  onClick={() => setRefreshKey((k) => k + 1)}
                >
                  {copy.refresh}
                </button>
                <ChangesList
                  api={api}
                  workspaceId={folder.workspace_id}
                  refreshKey={refreshKey}
                  selected={activeRef}
                  onSelect={(change: ChangeEntry) => open(change.path, true)}
                />
              </div>
            )}
            {tab === "search" && (
              <div
                role="tabpanel"
                id="workbench-panel-search"
                aria-labelledby="workbench-tab-search"
              >
                <QuickOpen
                  api={api}
                  workspaceId={folder.workspace_id}
                  enabled={trusted}
                  onSelectFile={(ref) => open(ref, false)}
                />
              </div>
            )}
            {tab === "worktrees" && roots && (
              <div
                role="tabpanel"
                id="workbench-panel-worktrees"
                aria-labelledby="workbench-tab-worktrees"
              >
                <Worktrees
                  key={folder.workspace_id}
                  api={roots.api}
                  folder={folder}
                  onStartSession={roots.onStartSession}
                  {...(roots.onRaiseLimit ? { onRaiseLimit: roots.onRaiseLimit } : {})}
                  {...(review
                    ? {
                        onReview: (worktreeId: string) => {
                          setReviewWorktree(worktreeId);
                          setTab("review");
                        },
                      }
                    : {})}
                />
              </div>
            )}
            {review && reviewVisited.current === reviewKey && (
              <div
                role="tabpanel"
                id="workbench-panel-review"
                aria-labelledby="workbench-tab-review"
                hidden={tab !== "review"}
              >
                {/* Review reads its worktree once at mount, so a new choice remounts it. */}
                <Review
                  key={reviewKey}
                  api={review.api}
                  folder={folder}
                  initialWorktree={reviewWorktree}
                  shown={tab === "review"}
                  onOpenUrl={review.onOpenUrl}
                  {...(checks ? { checks: checks.api } : {})}
                />
              </div>
            )}
            {tab === "checks" && checks && (
              <div
                role="tabpanel"
                id="workbench-panel-checks"
                aria-labelledby="workbench-tab-checks"
              >
                <Checks
                  key={folder.workspace_id}
                  api={checks.api}
                  folder={folder}
                  canRun={checks.canRun}
                />
              </div>
            )}
            {tab === "terminal" && terminal && (
              <div
                role="tabpanel"
                id="workbench-panel-terminal"
                aria-labelledby="workbench-tab-terminal"
              >
                <TerminalLauncher
                  key={folder.workspace_id}
                  api={terminal.api}
                  folder={folder}
                  canOpen={terminal.canOpen}
                />
              </div>
            )}
            {tab === "previews" && previews && (
              <div
                role="tabpanel"
                id="workbench-panel-previews"
                aria-labelledby="workbench-tab-previews"
              >
                <Previews
                  key={folder.workspace_id}
                  api={previews.api}
                  folder={folder}
                  canRun={previews.canRun}
                />
              </div>
            )}
          </div>
          <article className="workbench-pane">
            {currentTabs.length > 0 && (
              <fieldset className="workbench-file-tabs" aria-label="Open files">
                {currentTabs.map((item, index) => {
                  const dirty = folderId !== null && getBuffer(folderId, item.ref) !== undefined;
                  const hintId = `${editHintId}-close-${index}`;
                  return (
                    <div
                      className="workbench-file-tab"
                      key={item.ref}
                      data-unsaved={dirty || undefined}
                    >
                      <button
                        type="button"
                        className="btn txt sm workbench-file-select"
                        aria-pressed={item.ref === activeRef}
                        onClick={() => open(item.ref, item.withDiff, true)}
                      >
                        {item.ref}
                      </button>
                      {dirty && <span className="workbench-file-dirty">{copy.unsavedMark}</span>}
                      <button
                        type="button"
                        className="btn txt sm workbench-file-close"
                        aria-label={`Close ${item.ref}`}
                        aria-disabled={dirty}
                        aria-describedby={dirty ? hintId : undefined}
                        title={dirty ? copy.closeHint : undefined}
                        onClick={() => closeFileTab(item.ref)}
                      >
                        ×
                      </button>
                      {dirty && (
                        <span id={hintId} className="sr-only">
                          {copy.closeHint}
                        </span>
                      )}
                    </div>
                  );
                })}
              </fieldset>
            )}
            {viewer()}
          </article>
        </>
      )}
      {splitShown && (
        <div
          ref={bindSplitHost}
          className="workbench-resize-controls"
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
            label="Resize Code panes"
            controls={`${editHintId}-navigation`}
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
          <button type="button" className="btn txt workbench-split-reset" onClick={resetSplit}>
            Reset Code panes
          </button>
        </div>
      )}
    </div>
  );
}
