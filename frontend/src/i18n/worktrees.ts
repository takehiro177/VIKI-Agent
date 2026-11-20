/**
 * The Worktrees tab's cleanup sentence, archive chip, restore flow and Preview
 * cleanup's decision words (`Worktrees.tsx`). The refusal codes from the worktree
 * and session-root routes that predate archiving stay in `Worktrees.tsx`'s own
 * `REFUSALS` table; only the three restore refusals below (`no_archive`,
 * `branch_moved`, `branch_in_use`) live here, since `rootRefusal` checks this
 * catalog for them first.
 */

import { defineCatalog } from "./defineCatalog";

export const catalog = defineCatalog({
  en: {
    "worktrees.cleanup.on":
      "Idle worktrees are archived and removed: 24 hours after the last change for auto-named ones, 72 hours for ones you named. Restore an archive here for 30 days.",
    "worktrees.cleanup.off":
      "Automatic cleanup of idle worktrees is off; turn it on in Settings › General. Copies in use by a Bot chat are kept, and copies of deleted chats are still removed, or archived when they hold changes. A copy VIKI Agent can't safely clean up stays here, marked Released.",
    "worktrees.archived_chip": "Archived {date}",
    "worktrees.restore": "Restore",
    "worktrees.restoring": "Restoring…",
    "worktrees.restored": "Restored the worktree with its uncommitted changes.",
    "worktrees.preview": "Preview cleanup",
    "worktrees.checking": "Checking…",
    "worktrees.nothing": "Nothing to clean up.",
    "worktrees.action.remove": "Will be removed",
    "worktrees.action.archive": "Will be archived, then removed",
    "worktrees.action.keep": "Stays",
    "worktrees.reason.unknown": "VIKI couldn't read its state.",
    "worktrees.reason.untrusted": "Its folder isn't trusted now.",
    "worktrees.reason.live": "A session, check or preview is using it.",
    "worktrees.reason.locked": "Git has it locked.",
    "worktrees.reason.missing": "Its folder is already gone.",
    "worktrees.reason.recent": "Changed recently; eligible from {date}.",
    "worktrees.reason.submodule": "It has submodules.",
    "worktrees.reason.embedded_repo": "Another repository is inside it.",
    "worktrees.reason.worktree_refs":
      "It holds Git state only this worktree has, such as a bisect or rebase.",
    "worktrees.reason.hidden_changes": "Some files are marked assume-unchanged or skip-worktree.",
    "worktrees.reason.in_progress": "A merge, rebase, cherry-pick or revert is in progress.",
    "worktrees.reason.branch_changed": "Its checked-out branch isn't the one VIKI made.",
    "worktrees.reason.merged": "No changes, and every commit is on another branch, tag or remote.",
    "worktrees.reason.filtered":
      "Its changes are under a Git filter that VIKI doesn't run in a Bot copy, so it can't archive them.",
    "worktrees.reason.large_untracked": "Its untracked files are too large to archive.",
    "worktrees.reason.dirty": "It has uncommitted changes.",
    "worktrees.reason.unpushed": "It has commits on no other branch or remote.",
    "worktrees.reason.busy": "Its folder was busy; VIKI tries again next time.",
    "worktrees.reason.changed": "It changed while VIKI checked it.",
    "worktrees.reason.in_use": "A program has a file or folder open inside it.",
    "worktrees.reason.git_refused": "Git refused to remove it.",
    "worktrees.refusal.no_archive": "This worktree has no archive to restore.",
    "worktrees.refusal.branch_moved":
      "Its branch has moved since the archive. Restore it with Git by hand.",
    "worktrees.refusal.branch_in_use": "Its branch is checked out in another worktree.",
    "worktrees.bot.copy": "Bot copy: {bot} · {chat}",
    "worktrees.bot.thread_copy": "Bot thread copy: {thread}",
    "worktrees.bot.released": "Released",
    "worktrees.reason.bot_chat": "In use by a Bot chat",
    "worktrees.bot.remove_warning":
      "This chat or Bot thread cannot continue in the copy after you remove it.",
    "worktrees.refusal.git_settings_changed":
      "This folder's Git settings or hooks changed after a Bot copy was made, so VIKI Agent stopped its own Git actions here. Undo the change, or confirm the current settings in the Worktrees tab.",
    "worktrees.git_settings.confirm": "Confirm the current settings",
    "worktrees.git_settings.confirm_body":
      "VIKI Agent will use this folder's current Git settings and hooks as its new baseline. Review them before confirming.",
    "worktrees.git_settings.confirming": "Confirming…",
    "worktrees.git_settings.confirmed": "The current settings are confirmed.",
    "worktrees.archive.title": "Archived changes",
    "worktrees.archive.restore_label": "Restore archived changes from {date}",
    "worktrees.refusal.bot_copy_root":
      "This worktree belongs to a Bot chat. Open that chat instead.",
    "worktrees.refusal.too_many_sessions":
      "The most sessions allowed at once are running. Close one, or raise the limit in Settings › Chat.",
  },
});
