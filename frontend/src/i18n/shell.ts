/**
 * Banners, the readiness line, the app menu's own chrome, and the shared "back to chat" header
 * pattern every non-chat view uses. Banner and menu button *labels* that are also commands
 * (workspace.create, workspace.open, workspace.select, provider.setup) reuse `cmd.<id>` from
 * `i18n/commands.ts` rather than duplicating text here. `shell.limit.*` is the session-limit
 * wording shared by the toast (SessionLimitToast), the error card and the full-limit send
 * refusal.
 */

import { defineCatalog } from "./defineCatalog";

export const catalog = defineCatalog({
  en: {
    "shell.chat_only.text":
      "No vault is connected. This conversation runs in a shared temporary working folder.",
    "shell.chat_only.registered":
      "A vault is registered and becomes active on the next launch. This conversation continues in the shared temporary folder.",
    "shell.chat_only.registered_code":
      "A code folder is selected and becomes active on the next launch. This conversation continues in the shared temporary folder.",

    "shell.readiness.config_dir":
      "CLAUDE_CONFIG_DIR is set; sessions cannot start until it is removed.",
    "shell.readiness.batch_shim":
      "The configured Claude Code path is a batch wrapper, not claude.exe; sessions cannot start.",
    "shell.readiness.cli_missing": "Claude Code was not found; sessions cannot start.",
    "shell.readiness.billing": "Billing is blocked for this account; sessions cannot start.",
    "shell.readiness.signed_out": "Not signed in to Claude Code; sessions cannot start.",
    "shell.readiness.storage": "The app store is not ready; sessions cannot start.",

    "shell.agent_changes.region": "Changes waiting for you",
    "shell.agent_changes.change": "Chat “{title}” asks to change {key} from {from} to {to}.",
    "shell.agent_changes.reset": "Chat “{title}” asks to reset {key}.",
    "shell.agent_changes.routine": "Chat “{title}” asks to turn on routine “{name}”.",
    "shell.agent_changes.shortened": "{value} (shortened)",
    "shell.agent_changes.schedule": "Runs: {when}.",
    "shell.agent_changes.profile": "Profile: {name}.",
    "shell.agent_changes.workspace": "Workspace: {name}.",
    "shell.agent_changes.prompt_truncated":
      "Showing the first 2,000 characters. The full prompt is in Routines.",

    "shell.status.claude_signed_out": "claude: signed out",
    "shell.status.claude_outdated": "claude: update needed",
    "shell.status.git_missing": "git: missing",

    "shell.menu.label": "App menu",
    "shell.nav.back_to_chat": "Back to chat",
    "shell.terminal.opened": "Opened a terminal in this session's folder.",

    "shell.signin.line":
      "Sign in to Claude Code to keep chatting: VIKI Agent opens Claude Code's own sign-in in a PowerShell window, and your browser shows the Claude sign-in page.",
    "shell.signin.line_mac":
      "Sign in to Claude Code to keep chatting: VIKI Agent opens Claude Code's own sign-in in a Terminal window, and your browser shows the Claude sign-in page.",
    "shell.signin.line_linux":
      "Sign in to Claude Code to keep chatting: VIKI Agent opens Claude Code's own sign-in in your desktop's terminal, and your browser shows the Claude sign-in page.",
    "shell.signin.button": "Sign in",
    "shell.signin.opening": "Opening…",
    "shell.signin.opened": "Finish signing in in your browser, then send your message again.",
    "shell.signin.provider":
      "VIKI Agent can't start Claude Code right now. See Settings → Providers.",
    "shell.signin.failed":
      "The sign-in window could not open. In a terminal, run claude auth login.",

    "shell.needs_you.region": "Needs you, {n} waiting",
    "shell.needs_you.proposals": "Proposals",
    "shell.needs_you.arrival.one": "{body}, in {where}",
    "shell.needs_you.arrival.many": "{n} new requests need you",
    "shell.needs_you.arrival.tool": "{name} needs your approval",
    "shell.needs_you.arrival.plan": "{name} has a plan for you to approve",
    "shell.needs_you.arrival.question": "{name} has a question for you",
    "shell.needs_you.arrival.agent": "An agent needs your approval",
    "shell.needs_you.this_chat": "this chat",
    "shell.needs_you.notice_footer":
      "Get a desktop notice, while VIKI Agent is in the background, when a chat, an agent run or a routine needs you, or an agent run or routine finishes.",
    "shell.needs_you.notice_on": "Turn on desktop notices",
    "shell.needs_you.notice_later": "Not now",
    "shell.needs_you.notice_request_failed":
      "Could not request notification permission. Try again.",
    "shell.rail.waiting": "{n} waiting for you",

    "shell.chat_folders.one": "+1 folder",
    "shell.chat_folders.many": "+{n} folders",
    "shell.chat_folders.title": "This chat can work in",
    "shell.chat_folders.label": "Folders this chat can work in",
    "shell.chat_folders.starts_here": "· starts here",
    "shell.chat_folders.manage": "Manage workspaces in Settings",
    "shell.chat_folders.code_copy": "Code copy of {folder} · {branch}",

    "shell.limit.raise": "Or raise the limit in Settings.",
    "shell.limit.change": "Change limit",
    "shell.limit.error_title": "Session limit reached",
    "shell.limit.full":
      "The most sessions allowed at once are running. Close one, or raise the limit in Settings › Chat.",
  },
});
