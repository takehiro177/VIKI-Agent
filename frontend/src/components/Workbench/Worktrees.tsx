import { type FormEvent, useEffect, useRef, useState } from "react";
import type { WorkspaceJson } from "../../clients/app/shellApi";
import {
  type ArchiveJson,
  type BotCopyJson,
  type DecisionJson,
  type RemovalJson,
  type RootsApi,
  RootsApiError,
  type WorktreeJson,
} from "../../clients/code/rootsApi";
import type { Key } from "../../i18n/index";
import { t, tf } from "../../i18n/t";
import { noteWorktree, refreshRoots, useRoots } from "../../state/sessionRoots";
import { formatBytes } from "../Data/formatBytes";
import { ConfirmDialog } from "../Dialog/ConfirmDialog";
import "./Worktrees.css";

const GIT = "Git could not do this. Try again later.";
const CHANGED = "This folder changed. Reopen the Code view and try again.";
const NOT_READY = "This worktree is not ready.";
const TOP_FOLDER = "Worktrees need the repository's top folder.";
const OTHER = "That did not work. Try again.";
// Every code the worktree and session-root routes answer; anything else gets OTHER.
const REFUSALS: Record<string, string> = {
  trust_required: "Sessions start only in a trusted folder.",
  workspace_changed: CHANGED,
  root_unavailable: CHANGED,
  not_repository: TOP_FOLDER,
  not_repository_root: TOP_FOLDER,
  worktree_not_ready: NOT_READY,
  worktree_busy: NOT_READY,
  worktree_in_use: "A session is running in this worktree. Close it first.",
  branch_exists: "A branch with this name exists. Choose another name.",
  invalid_branch: "Use letters, digits and . _ - / (up to 100).",
  no_commits: "This folder has no commit yet.",
  worktree_limit: "VIKI keeps at most 32 worktrees. Remove one first.",
  vault_locked: "The folder is busy. Try again in a moment.",
  git_unavailable: GIT,
  git_failed: GIT,
  git_common_unavailable: GIT,
  unsupported_filesystem: "This folder's drive doesn't support worktrees from VIKI.",
};

/** The refusals with catalog text (`i18n/worktrees.ts`), checked before the older `REFUSALS`
 * table: `restore`'s three, a Bot copy's folder refusal, the Git settings that changed under a
 * Bot copy, and the session limit. */
const CATALOG_REFUSAL_KEYS: Record<string, Key> = {
  no_archive: "worktrees.refusal.no_archive",
  branch_moved: "worktrees.refusal.branch_moved",
  branch_in_use: "worktrees.refusal.branch_in_use",
  bot_copy_root: "worktrees.refusal.bot_copy_root",
  git_settings_changed: "worktrees.refusal.git_settings_changed",
  too_many_sessions: "worktrees.refusal.too_many_sessions",
};

/** The words for a refusal code from the roots routes or from starting a session. Own keys only:
 * a code like "toString" gets OTHER, not an inherited property. */
export function rootRefusal(code: string): string {
  const catalogKey = Object.hasOwn(CATALOG_REFUSAL_KEYS, code)
    ? CATALOG_REFUSAL_KEYS[code]
    : undefined;
  if (catalogKey) return t(catalogKey);
  return (Object.hasOwn(REFUSALS, code) ? REFUSALS[code] : undefined) ?? OTHER;
}

const codeOf = (error: unknown) => (error instanceof RootsApiError ? error.code : "unavailable");

/** Every `DecisionJson["reason"]` to its catalog key; a reason the rules add without a row here
 * is a type error, not a silent blank. */
const REASON_KEY: Record<DecisionJson["reason"], Key> = {
  unknown: "worktrees.reason.unknown",
  untrusted: "worktrees.reason.untrusted",
  live: "worktrees.reason.live",
  bot_chat: "worktrees.reason.bot_chat",
  locked: "worktrees.reason.locked",
  missing: "worktrees.reason.missing",
  recent: "worktrees.reason.recent",
  submodule: "worktrees.reason.submodule",
  embedded_repo: "worktrees.reason.embedded_repo",
  worktree_refs: "worktrees.reason.worktree_refs",
  hidden_changes: "worktrees.reason.hidden_changes",
  in_progress: "worktrees.reason.in_progress",
  branch_changed: "worktrees.reason.branch_changed",
  merged: "worktrees.reason.merged",
  filtered: "worktrees.reason.filtered",
  large_untracked: "worktrees.reason.large_untracked",
  dirty: "worktrees.reason.dirty",
  unpushed: "worktrees.reason.unpushed",
  busy: "worktrees.reason.busy",
  changed: "worktrees.reason.changed",
  in_use: "worktrees.reason.in_use",
  git_refused: "worktrees.reason.git_refused",
};

/** Every `DecisionJson["action"]` to its catalog key, the same way. */
const ACTION_KEY: Record<DecisionJson["action"], Key> = {
  remove: "worktrees.action.remove",
  archive: "worktrees.action.archive",
  keep: "worktrees.action.keep",
};

/** A decision row's reason, in words; `recent` substitutes `{date}` when the server sent one.
 * Dates read like the Code view's other timestamps (`clients/code/checkWords.ts#runLine`): the
 * browser's own default formatting, no new library. */
function reasonWords(decision: DecisionJson): string {
  const key = REASON_KEY[decision.reason];
  return decision.eligible_at !== null
    ? tf(key, { date: new Date(decision.eligible_at).toLocaleString() })
    : t(key);
}

const SETTINGS_CHANGED = "git_settings_changed";

/** Which Bot or chat uses a copy, in the catalog's words; null when a name it needs is unknown,
 * since an id is no label. A task copy has no chat title, so it names only its Bot. */
function botCopyLabel(bot: BotCopyJson): string | null {
  if (bot.kind === "thread")
    return bot.title === null ? null : tf("worktrees.bot.thread_copy", { thread: bot.title });
  if (bot.bot_name === null) return null;
  const label = tf("worktrees.bot.copy", { bot: bot.bot_name, chat: bot.title ?? "" });
  return bot.title === null ? label.replace(/\s*·\s*$/, "") : label;
}

const archivedAt = (archive: ArchiveJson) => new Date(archive.archived_at).toLocaleString();

const copy = {
  note: "A worktree is a separate working folder made from this folder's last commit. It is not a sandbox.",
  branch: "Branch name",
  create: "Create worktree",
  creating: "Creating…",
  noRoot: "This folder can't be changed now.",
  states: {
    ready: "Ready",
    creating: "Creating",
    removing: "Removing",
    kept: "Kept: removal did not finish",
    removed: "Removed",
  } as Record<WorktreeJson["state"], string>,
  base: "Base commit",
  start: "New session here",
  review: "Review",
  remove: "Remove",
  confirm: (branch: string) =>
    `This deletes the worktree's folder, including ignored files such as build output. The branch ${branch} and its commits stay.`,
  removed: (branch: string) => `Removed the worktree. The branch ${branch} stays.`,
  dirty: (count: number) =>
    `Not removed: ${count} changed or new files. Commit or discard them first.`,
  gitRefused: "Git did not remove the worktree. Check its folder, then try again.",
};

/** `viki/<yyyymmdd>-<4 hex>` from the local date and a random draw. */
function defaultBranch(): string {
  const now = new Date();
  const day = [now.getFullYear(), now.getMonth() + 1, now.getDate()]
    .map((part, i) => String(part).padStart(i === 0 ? 4 : 2, "0"))
    .join("");
  const hex = [...crypto.getRandomValues(new Uint8Array(2))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
  return `viki/${day}-${hex}`;
}

const byNewest = (a: WorktreeJson, b: WorktreeJson) =>
  Number(a.state === "removed") - Number(b.state === "removed") ||
  (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0);

export function Worktrees({
  api,
  folder,
  onStartSession,
  onReview,
  onRaiseLimit,
}: {
  api: RootsApi;
  folder: WorkspaceJson;
  onStartSession(root: {
    workspace_id: string;
    worktree_id: string | null;
  }): Promise<string | null>;
  onReview?(worktreeId: string): void;
  onRaiseLimit?: () => void;
}) {
  const alive = useRef<AbortController | null>(null);
  const [branch, setBranch] = useState(defaultBranch);
  const [creating, setCreating] = useState(false);
  const [createRefusal, setCreateRefusal] = useState<string | null>(null);
  /** The row a remove, start or restore is running for; one at a time. */
  const [acting, setActing] = useState<string | null>(null);
  const [startRefusal, setStartRefusal] = useState<{ id: string; code: string } | null>(null);
  const [restoreRefusal, setRestoreRefusal] = useState<{ id: string; code: string } | null>(null);
  const [message, setMessage] = useState("");
  const [archiveData, setArchiveData] = useState<{
    enabled: boolean;
    archives: ArchiveJson[];
  } | null>(null);
  const [decisions, setDecisions] = useState<DecisionJson[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [confirming, setConfirming] = useState<WorktreeJson | null>(null);
  /** Set by any action the folder's changed Git settings refused; shown once, with the way out. */
  const [settingsRefused, setSettingsRefused] = useState(false);
  const [settingsPending, setSettingsPending] = useState<"reading" | "confirming" | null>(null);
  const [settingsFailure, setSettingsFailure] = useState<string | null>(null);
  /** What the open dialog confirms: the API, folder identity and saved hash as they were read. */
  const [settingsReview, setSettingsReview] = useState<{
    api: RootsApi;
    workspaceId: string;
    policyRevision: number;
    rootId: string;
    fingerprint: string;
  } | null>(null);
  const latest = useRef({ api, workspaceId: folder.workspace_id });
  latest.current = { api, workspaceId: folder.workspace_id };
  const archiveRead = useRef(0);
  const rows = useRoots()
    .worktrees.filter((row) => row.workspace_id === folder.workspace_id)
    .sort(byNewest);
  const rootId = folder.root_physical_id;
  // An archive a listed row already shows (chip and Restore) is not shown twice.
  const archiveOnly = (archiveData?.archives ?? [])
    .filter((item) => !rows.some((row) => row.worktree_id === item.worktree_id))
    .sort((a, b) => (a.archived_at < b.archived_at ? 1 : a.archived_at > b.archived_at ? -1 : 0));

  // A failed read keeps the rows already held; the store says nothing more.
  const refresh = () => refreshRoots(api, alive.current?.signal).catch(() => {});
  /** The settings refusal belongs to the folder, so it shows once, beside its way out. */
  const settingsChanged = (code: string) => {
    if (code !== SETTINGS_CHANGED) return false;
    setSettingsRefused(true);
    setSettingsFailure(null);
    return true;
  };
  // A failed read leaves the archive state as it was: no chip or sentence beats a wrong one, and
  // only the latest read publishes. Only the settings refusal shows, so the way out is there on
  // open; a read that was aborted says nothing.
  const loadArchives = () => {
    const read = ++archiveRead.current;
    const signal = alive.current?.signal;
    return api.archives(folder.workspace_id, signal).then(
      (data) => {
        if (read === archiveRead.current) setArchiveData(data);
      },
      (error: unknown) => {
        if (!signal?.aborted) settingsChanged(codeOf(error));
      },
    );
  };
  // biome-ignore lint/correctness/useExhaustiveDependencies: one read per mount.
  useEffect(() => {
    const controller = new AbortController();
    alive.current = controller;
    void refresh();
    void loadArchives();
    return () => controller.abort();
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (creating || !rootId) return;
    if (branch === "") {
      setCreateRefusal("invalid_branch");
      return;
    }
    setCreating(true);
    setCreateRefusal(null);
    setSettingsRefused(false);
    try {
      noteWorktree(
        await api.create(folder.workspace_id, {
          policy_revision: folder.policy_revision,
          root_physical_id: rootId,
          branch,
        }),
      );
      setBranch(defaultBranch());
    } catch (error) {
      const code = codeOf(error);
      if (!settingsChanged(code)) setCreateRefusal(code);
    }
    setCreating(false);
    void refresh();
  }

  function outcomeWords(row: WorktreeJson, removal: RemovalJson): string {
    if (removal.outcome === "removed") return copy.removed(row.branch);
    return removal.reason === "dirty" && removal.changes !== null
      ? copy.dirty(removal.changes)
      : copy.gitRefused;
  }

  async function remove(row: WorktreeJson) {
    if (!rootId) return;
    setActing(row.worktree_id);
    setMessage("");
    setSettingsRefused(false);
    try {
      const removal = await api.remove(row.worktree_id, {
        policy_revision: folder.policy_revision,
        root_physical_id: rootId,
      });
      noteWorktree(removal.worktree);
      setMessage(outcomeWords(row, removal));
    } catch (error) {
      const code = codeOf(error);
      if (!settingsChanged(code)) setMessage(rootRefusal(code));
    }
    setActing(null);
    void refresh();
  }

  async function start(row: WorktreeJson) {
    setActing(row.worktree_id);
    setStartRefusal(null);
    setSettingsRefused(false);
    const code = await onStartSession({
      workspace_id: row.workspace_id,
      worktree_id: row.worktree_id,
    });
    setActing(null);
    if (code !== null && !settingsChanged(code)) setStartRefusal({ id: row.worktree_id, code });
  }

  /** Restores by the archive's opaque worktree id, so it needs no listed row. The restored row
   * comes from the answer, then the archives and the roots are read again. */
  async function restore(worktreeId: string) {
    if (!rootId) return;
    setActing(worktreeId);
    setRestoreRefusal(null);
    setMessage("");
    setSettingsRefused(false);
    try {
      const restored = await api.restore(worktreeId, {
        policy_revision: folder.policy_revision,
        root_physical_id: rootId,
      });
      noteWorktree(restored);
      setMessage(t("worktrees.restored"));
    } catch (error) {
      const code = codeOf(error);
      if (!settingsChanged(code)) setRestoreRefusal({ id: worktreeId, code });
    }
    setActing(null);
    void refresh();
    void loadArchives();
  }

  async function preview() {
    setChecking(true);
    setSettingsRefused(false);
    try {
      setDecisions(await api.cleanup(folder.workspace_id, alive.current?.signal));
    } catch (error) {
      // Keeps whatever the last preview showed; the button is there to try again.
      settingsChanged(codeOf(error));
    }
    setChecking(false);
  }

  /** Whether an answer still belongs to what is on screen: same API, same folder, mounted. */
  const stillShown = (shown: { api: RootsApi; workspaceId: string }, signal?: AbortSignal) =>
    !signal?.aborted &&
    latest.current.api === shown.api &&
    latest.current.workspaceId === shown.workspaceId;

  /** Reads the saved hash and opens the dialog for exactly that hash; nothing is sent yet. */
  async function reviewSettings() {
    if (!api.gitSettings || !rootId || settingsPending !== null) return;
    const shown = { api, workspaceId: folder.workspace_id };
    const identity = { policyRevision: folder.policy_revision, rootId };
    const signal = alive.current?.signal;
    setSettingsPending("reading");
    setSettingsFailure(null);
    try {
      const fingerprint = await api.gitSettings(shown.workspaceId, signal);
      if (stillShown(shown, signal)) {
        // With no saved baseline nothing is confirmable, and the server would refuse it too.
        if (fingerprint === null) setSettingsFailure("baseline_changed");
        else setSettingsReview({ ...shown, ...identity, fingerprint });
      }
    } catch (error) {
      if (stillShown(shown, signal)) setSettingsFailure(codeOf(error));
    }
    setSettingsPending(null);
  }

  /** One attempt, never repeated: a failure closes the dialog and the next try reads the hash again. */
  async function confirmSettings(review: NonNullable<typeof settingsReview>) {
    if (!review.api.confirmGitSettings || settingsPending !== null) return;
    const signal = alive.current?.signal;
    setSettingsPending("confirming");
    try {
      await review.api.confirmGitSettings(
        review.workspaceId,
        {
          policy_revision: review.policyRevision,
          root_physical_id: review.rootId,
          expected_fingerprint: review.fingerprint,
        },
        signal,
      );
      if (stillShown(review, signal)) {
        setSettingsRefused(false);
        setSettingsFailure(null);
        setMessage(t("worktrees.git_settings.confirmed"));
        void refresh();
        void loadArchives();
      }
    } catch (error) {
      if (stillShown(review, signal)) setSettingsFailure(codeOf(error));
    }
    setSettingsReview(null);
    setSettingsPending(null);
  }

  return (
    <div className="worktrees">
      <p>{copy.note}</p>
      <form className="worktrees-form" onSubmit={(event) => void create(event)}>
        <label>
          {copy.branch}
          <input
            value={branch}
            maxLength={100}
            disabled={!rootId}
            spellCheck={false}
            onChange={(event) => setBranch(event.target.value)}
          />
        </label>
        <button type="submit" className="btn go sm" disabled={creating || !rootId}>
          {creating ? copy.creating : copy.create}
        </button>
        {!rootId && <p className="code-reason">{copy.noRoot}</p>}
        {createRefusal !== null && (
          <p role="alert" className="worktrees-refusal">
            {rootRefusal(createRefusal)}
          </p>
        )}
      </form>
      <p role="status" aria-live="polite">
        {message}
      </p>
      {settingsRefused && (
        <div className="worktrees-cleanup">
          <p role="alert" className="worktrees-refusal">
            {rootRefusal(SETTINGS_CHANGED)}
          </p>
          {api.gitSettings && api.confirmGitSettings && (
            <div className="worktree-actions">
              <button
                type="button"
                className="btn alt sm"
                disabled={settingsPending !== null || !rootId}
                onClick={() => void reviewSettings()}
              >
                {settingsPending === "reading"
                  ? t("worktrees.git_settings.confirming")
                  : t("worktrees.git_settings.confirm")}
              </button>
            </div>
          )}
          {settingsFailure !== null && (
            <p role="alert" className="worktrees-refusal">
              {rootRefusal(settingsFailure)}
            </p>
          )}
        </div>
      )}
      {settingsReview && (
        <ConfirmDialog
          label={t("worktrees.git_settings.confirm")}
          confirmLabel={
            settingsPending === "confirming"
              ? t("worktrees.git_settings.confirming")
              : t("worktrees.git_settings.confirm")
          }
          body={t("worktrees.git_settings.confirm_body")}
          busy={settingsPending === "confirming"}
          onConfirm={() => void confirmSettings(settingsReview)}
          onCancel={() => {
            if (settingsPending !== "confirming") setSettingsReview(null);
          }}
        />
      )}
      <div className="worktrees-cleanup">
        {archiveData && (
          <p>{archiveData.enabled ? t("worktrees.cleanup.on") : t("worktrees.cleanup.off")}</p>
        )}
        <button
          type="button"
          className="btn txt sm"
          disabled={checking}
          onClick={() => void preview()}
        >
          {checking ? t("worktrees.checking") : t("worktrees.preview")}
        </button>
        {decisions !== null &&
          (decisions.length === 0 ? (
            <p className="code-reason">{t("worktrees.nothing")}</p>
          ) : (
            <ul className="worktrees-decisions">
              {decisions.map((decision) => (
                <li key={decision.worktree_id}>
                  <strong>
                    {rows.find((row) => row.worktree_id === decision.worktree_id)?.branch ??
                      decision.worktree_id}
                  </strong>
                  <span className="code-reason">
                    {t(ACTION_KEY[decision.action])} · {reasonWords(decision)}
                    {decision.bytes !== null ? ` · ${formatBytes(decision.bytes)}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ))}
      </div>
      <ul>
        {rows.map((row) => {
          const nameId = `worktree-${row.worktree_id}`;
          const archive =
            row.state === "removed"
              ? archiveData?.archives.find((item) => item.worktree_id === row.worktree_id)
              : undefined;
          const bot = row.bot_copy;
          const botLabel = bot ? botCopyLabel(bot) : null;
          return (
            <li key={row.worktree_id} className="worktree-row" data-state={row.state}>
              <div className="worktree-head">
                <strong id={nameId}>{row.branch}</strong>
                <span className="chip">{copy.states[row.state]}</span>
                {bot?.released && <span className="chip">{t("worktrees.bot.released")}</span>}
                {archive && (
                  <span className="chip">
                    {tf("worktrees.archived_chip", { date: archivedAt(archive) })}
                  </span>
                )}
              </div>
              {botLabel !== null && <span className="code-reason">{botLabel}</span>}
              <span className="worktree-path">{row.path}</span>
              <span className="code-reason">
                {copy.base} <code>{row.base_oid.slice(0, 12)}</code>
              </span>
              {(row.state === "ready" || row.state === "kept") && (
                <div className="worktree-actions">
                  {row.state === "ready" && !bot && (
                    <button
                      type="button"
                      className="btn alt sm"
                      aria-describedby={nameId}
                      disabled={acting !== null}
                      onClick={() => void start(row)}
                    >
                      {copy.start}
                    </button>
                  )}
                  {row.state === "ready" && onReview && (
                    <button
                      type="button"
                      className="btn txt sm"
                      aria-describedby={nameId}
                      onClick={() => onReview(row.worktree_id)}
                    >
                      {copy.review}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn txt sm"
                    aria-describedby={nameId}
                    disabled={acting !== null || !rootId}
                    onClick={() => setConfirming(row)}
                  >
                    {copy.remove}
                  </button>
                </div>
              )}
              {confirming?.worktree_id === row.worktree_id && (
                <ConfirmDialog
                  label={copy.remove}
                  confirmLabel={copy.remove}
                  body={
                    bot
                      ? `${copy.confirm(row.branch)} ${t("worktrees.bot.remove_warning")}`
                      : copy.confirm(row.branch)
                  }
                  destructive
                  irreversible
                  onConfirm={() => {
                    setConfirming(null);
                    void remove(row);
                  }}
                  onCancel={() => setConfirming(null)}
                />
              )}
              {archive && (
                <div className="worktree-actions">
                  <button
                    type="button"
                    className="btn txt sm"
                    aria-describedby={nameId}
                    disabled={acting !== null || !rootId}
                    onClick={() => void restore(row.worktree_id)}
                  >
                    {acting === row.worktree_id ? t("worktrees.restoring") : t("worktrees.restore")}
                  </button>
                </div>
              )}
              {startRefusal?.id === row.worktree_id && (
                <>
                  <p role="alert" className="worktrees-refusal">
                    {rootRefusal(startRefusal.code)}
                  </p>
                  {startRefusal.code === "too_many_sessions" && onRaiseLimit && (
                    <button type="button" className="btn txt sm" onClick={onRaiseLimit}>
                      {t("shell.limit.change")}
                    </button>
                  )}
                </>
              )}
              {restoreRefusal?.id === row.worktree_id && (
                <p role="alert" className="worktrees-refusal">
                  {rootRefusal(restoreRefusal.code)}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {archiveOnly.length > 0 && (
        <section aria-label={t("worktrees.archive.title")}>
          <p>
            <strong>{t("worktrees.archive.title")}</strong>
          </p>
          <ul>
            {archiveOnly.map((item) => (
              <li key={item.worktree_id} className="worktree-row">
                <div className="worktree-head">
                  <span className="chip">
                    {tf("worktrees.archived_chip", { date: archivedAt(item) })}
                  </span>
                  <button
                    type="button"
                    className="btn txt sm"
                    aria-label={tf("worktrees.archive.restore_label", {
                      date: archivedAt(item),
                    })}
                    disabled={acting !== null || !rootId}
                    onClick={() => void restore(item.worktree_id)}
                  >
                    {acting === item.worktree_id
                      ? t("worktrees.restoring")
                      : t("worktrees.restore")}
                  </button>
                </div>
                {restoreRefusal?.id === item.worktree_id && (
                  <p role="alert" className="worktrees-refusal">
                    {rootRefusal(restoreRefusal.code)}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
