/**
 * The settings view's chrome (nav tabs, review panel, read-only notice) and the general/chat/
 * privacy pages' own key labels and descriptions. The appearance page's four keys live in
 * `appearance.ts`; the source and applies chips live in `common.ts`.
 *
 * The `settings.value.<key>.<value>` rows below come from `src/viki_agent/app/settings.py`'s own allowed lists
 * (`KEYS`, via `PERMISSION_MODES` and `EFFORTS`), not from `testShell.ts`'s fixture, which
 * carries a narrower set: a value the backend can send with no row here would otherwise render
 * as a raw token. `<key>` is the setting key with its dots turned into underscores, the spelling
 * `settings.key.*` and `i18n/appearance.ts` already use.
 */

import { defineCatalog } from "./defineCatalog";

export const catalog = defineCatalog({
  en: {
    "settings.title": "Settings",
    "settings.intro":
      "Names, appearance, chat defaults, providers, workspaces, Persona, privacy and diagnostics.",
    "settings.nav.label": "Settings sections",

    "settings.page.general.hint": "Names, avatars, code view",
    "settings.page.appearance.hint": "Theme, size, motion",
    "settings.page.chat.hint": "Chat defaults and voice",
    "settings.page.providers.hint": "Claude Code and model connections",
    "settings.page.workspaces.hint": "Vault and code folders",
    "settings.page.persona.hint": "AGENTS.md, CLAUDE.md and Persona",
    "settings.page.privacy.hint": "Previews and paths in diagnostics",
    "settings.page.diagnostics.hint": "App details and troubleshooting",
    "settings.page.general.desc":
      "Choose the names and pictures shown in chats, and how code appears.",
    "settings.page.appearance.desc":
      "Preview changes as you make them. Select Apply to keep them or Cancel to go back. Restore defaults saves immediately.",
    "settings.page.chat.desc":
      "Set defaults for new chats and choose how you speak to VIKI and hear replies.",
    "settings.page.providers.desc":
      "Check Claude Code, which runs your chats. You can also connect a local or hosted model.",
    "settings.page.workspaces.desc":
      "Your vault holds your notes. Add code folders to let chats work with your projects too.",
    "settings.page.persona.desc":
      "Read and edit your vault's AGENTS.md and CLAUDE.md, and choose how the assistant responds in its Persona section.",
    "settings.page.privacy.desc":
      "Choose which instruction previews appear and whether diagnostics include folder paths.",
    "settings.page.diagnostics.desc":
      "Check the app's version, see where data goes, and inspect details to help troubleshoot a problem.",
    "settings.save.automatic":
      "Changes save automatically. Text fields save when you leave the field or press Enter.",

    "settings.tab.general": "General",
    "settings.tab.appearance": "Appearance and Accessibility",
    "settings.tab.chat": "Chat",
    "settings.tab.providers": "Providers",
    "settings.tab.workspaces": "Workspaces and Vault",
    "settings.tab.persona": "Instructions and Persona",
    "settings.tab.privacy": "Privacy and Storage",
    "settings.tab.diagnostics": "Diagnostics and About",
    "settings.page.other": "Other",

    "settings.value.inherit": "Use {source}",
    "settings.value.empty": "(not set)",
    "settings.row.reset_to_inherited": "Reset",
    "settings.row.reset_unavailable":
      "No custom value is saved here, so there is nothing to reset.",
    "settings.row.personal_excluded": "Excluded from exports by default.",
    "settings.general.show_onboarding_again": "Show first-run setup on next launch",
    "settings.general.onboarding.label": "First-run setup",
    "settings.general.onboarding.desc": "Shows the first-run setup again on the next launch.",

    "settings.search.label": "Search settings",
    "settings.search.placeholder": "Search settings",
    "settings.search.results": "{count} settings match.",
    "settings.search.empty": "No settings match.",
    "settings.search.open_page": "Open this page",

    "settings.review.title": "Changed in another window",
    "settings.review.you_entered": "You entered",
    "settings.review.current": "Current value",
    "settings.review.reload_and_reenter": "Load the current values and re-enter",

    "settings.read_only.newer_version":
      "A newer version of VIKI is saving settings. This version is read-only.",
    "settings.read_only.corrupt": "The settings file could not be read.",
    "settings.read_only.guidance":
      "Close VIKI Agent and copy the whole app data folder somewhere safe. For settings saved by a newer version, use that version. For damaged settings, restore a complete backup made while VIKI Agent was closed.",

    "settings.key.identity_display_name.label": "Display name",
    "settings.key.identity_display_name.desc":
      "The name shown next to your messages in the conversation.",
    "settings.key.identity_assistant_name.label": "Assistant name",
    "settings.key.identity_assistant_name.desc":
      "The name used to refer to the assistant. It does not change the window title.",
    "settings.key.identity_user_avatar_id.label": "Your avatar",
    "settings.key.identity_user_avatar_id.desc":
      "Shown next to your messages. Leave it unset to show no picture.",
    "settings.key.identity_assistant_avatar_id.label": "Assistant avatar",
    "settings.key.identity_assistant_avatar_id.desc":
      "Shown beside the assistant's replies and at the top of the right panel in ordinary chats. Bot and profile replies use their own pictures.",
    "settings.key.workbench_code_font_size.label": "Code view font size",
    "settings.key.workbench_code_font_size.desc":
      "Text size of the Code view's files and editor, in pixels.",
    "settings.key.workbench_line_wrap.label": "Wrap long lines in the Code view",
    "settings.key.workbench_line_wrap.desc": "Long lines wrap instead of scrolling sideways.",
    "settings.key.workbench_worktree_cleanup.label": "Clean up idle worktrees",
    "settings.key.workbench_worktree_cleanup.desc":
      "Archive and remove the worktrees VIKI made once they sit idle: 24 hours for auto-named ones, 72 hours for ones you named. Restore archives from the Worktrees tab for 30 days. Copies in use by a Bot chat are kept, and copies of deleted chats are removed, or archived when they hold changes, even with this off. A copy VIKI Agent can't safely clean up stays in the Worktrees tab, marked Released.",
    "settings.key.layout_left_rail_width.label": "Left sidebar width",
    "settings.key.layout_left_rail_width.desc":
      "Preferred width in pixels, from 220 to 480. Reset uses 272.",
    "settings.key.layout_right_rail_width.label": "Right sidebar width",
    "settings.key.layout_right_rail_width.desc":
      "Preferred width in pixels, from 240 to 560. Reset uses 320.",

    "settings.key.chat_default_mode.label": "Default mode",
    "settings.key.chat_default_mode.desc":
      "Choose when Claude Code asks for your approval in new chats.",
    "settings.key.chat_default_effort.label": "Default effort",
    "settings.key.chat_default_effort.desc":
      "The model's reasoning effort in new chats: how much it thinks before answering.",
    "settings.key.chat_default_model.label": "Default model",
    "settings.key.chat_default_model.desc": "The model a new session starts with.",
    "settings.key.chat_other_sign_in.label": "Let Claude Code use its other sign-in methods",
    "settings.key.chat_other_sign_in.desc":
      "On by default: chats, routines and Bots use the sign-in your Claude Code is set up with, such as your Claude plan, an API key, an Anthropic Console login or a cloud provider, and that account is billed. When off, chats start only on your Claude subscription sign-in, and a chat that would bill another account does not start. Set your sign-in in Claude Code's own settings: VIKI Agent does not pass ANTHROPIC_ or CLAUDE_CODE_ variables from your shell, such as ANTHROPIC_API_KEY, to Claude Code. Model connections are not affected.",
    "settings.key.chat_max_live_sessions.label": "Sessions running at once",
    "settings.key.chat_max_live_sessions.desc":
      "How many sessions can run Claude Code at the same time, from 1 to 999. Each running session is its own Claude Code process and uses memory. Chats, routines and Bots all count. A lower number stops nothing that is running; new sessions can start once enough have closed.",
    "settings.number.range": "Enter a whole number from {min} to {max}.",

    "settings.value.chat_default_mode.acceptEdits": "Accept edits",
    "settings.value.chat_default_mode.auto": "Auto",
    "settings.value.chat_default_mode.default": "Default",
    "settings.value.chat_default_mode.manual": "Manual",
    "settings.value.chat_default_mode.plan": "Plan",
    "settings.mode.manual.desc":
      "Asks before edits and commands your Claude Code settings don't allow",
    "settings.mode.accept_edits.desc":
      "Edits files, and runs rm, mv, cp and sed, in this chat's folders without asking",

    "settings.value.chat_default_effort.low": "Low",
    "settings.value.chat_default_effort.medium": "Medium",
    "settings.value.chat_default_effort.high": "High",
    "settings.value.chat_default_effort.xhigh": "Very high",
    "settings.value.chat_default_effort.max": "Maximum",

    "settings.key.voice_dictation_language.label": "Dictation language",
    "settings.key.voice_dictation_language.desc":
      "Auto detects the language. The list holds the languages the current speech model handles well.",
    "settings.key.voice_model.label": "Dictation model",
    "settings.key.voice_model.desc":
      "Installed models only. Auto uses the best one installed for the CPU or GPU selected below.",
    "settings.key.voice_backend.label": "Dictation runs on",
    "settings.key.voice_backend.desc":
      "Auto uses the GPU when its files are installed, otherwise the CPU.",
    "settings.key.voice_pause_s.label": "Pause before voice input ends",
    "settings.key.voice_pause_s.desc":
      "A silence this long ends voice input in dictation and hands-free. Hands-free then " +
      "sends your message. Applies the next time you speak.",

    "settings.value.voice_dictation_language.auto": "Auto",
    "settings.value.voice_model.auto": "Auto",
    "settings.value.voice_backend.auto": "Auto",
    "settings.value.voice_backend.cpu": "CPU",
    "settings.value.voice_backend.cuda": "NVIDIA GPU (CUDA)",
    "settings.value.voice_backend.metal": "Apple GPU (Metal)",
    "settings.value.voice_pause_s.1.2": "1.2 s",
    "settings.value.voice_pause_s.2": "2 s",
    "settings.value.voice_pause_s.3": "3 s",
    "settings.value.voice_pause_s.4": "4 s",
    "settings.value.voice_pause_s.5": "5 s",
    "settings.value.unavailable": "{value} (not available here)",

    "settings.key.voice_speech_engine.label": "Read replies aloud with",
    "settings.key.voice_speech_engine.desc":
      "How VIKI reads replies aloud: this computer's system voices, VIKI's natural voices (a voice pack VIKI installs on Windows PCs with an NVIDIA graphics card), or your own Voicebox app.",
    "settings.value.voice_speech_engine.system": "System voices",
    "settings.value.voice_speech_engine.pack": "Natural voices",
    "settings.value.voice_speech_engine.voicebox": "My own Voicebox app (advanced)",

    "settings.key.voice_pack_voices.label": "Natural voice",
    "settings.key.voice_pack_voices.desc":
      "Which voice reads each language: Kokoro (fast), Qwen3 (more expressive, needs a strong GPU) or this computer's system voice.",

    "settings.key.voice_voicebox_profile.label": "Voicebox voice",
    "settings.key.voice_voicebox_profile.desc":
      "Read replies aloud with a voice from the Voicebox app on this computer, in the languages " +
      "Voicebox speaks. VIKI Agent sends the text to read to Voicebox. Off uses this computer's " +
      "own voices.",
    "settings.key.voice_voicebox_model_size.label": "Voicebox model size",
    "settings.key.voice_voicebox_model_size.desc":
      "The model size Voicebox uses: Qwen voices take 0.6B or 1.7B, TADA voices 1B or 3B, other " +
      "voices ignore it. 0.6B suits a GPU with 8 GB.",
    "settings.key.voice_voicebox_url.label": "Voicebox address",
    "settings.key.voice_voicebox_url.desc":
      "Where Voicebox answers on this computer: http with 127.0.0.1, localhost or [::1] and a " +
      "port. The Voicebox app uses http://127.0.0.1:17493.",
    "settings.key.voice_speak_while_writing.label": "Speak while VIKI writes",
    "settings.key.voice_speak_while_writing.desc":
      "In hands-free, start playing the spoken answer while VIKI is still writing the reply. " +
      "Needs a Voicebox voice.",

    "settings.key.privacy_hide_previews.label": "Hide instruction previews",
    "settings.key.privacy_hide_previews.desc":
      "Hide the previews of CLAUDE.md and AGENTS.md in a workspace's Details.",
    "settings.key.privacy_diagnostics_include_paths.label": "Include folder paths in diagnostics",
    "settings.key.privacy_diagnostics_include_paths.desc":
      "Include folder paths in the diagnostics document and its exports.",

    "settings.persona.intro":
      "Persona is one section of AGENTS.md, between its two viki:persona marker lines. It sets the assistant's name, how it addresses you, its tone, reply language, guidance in chats and Do and Don't lists. {vault}'s instruction files are shown in full below.",
    "settings.persona.editor.warning":
      "AGENTS.md and CLAUDE.md steer every chat in this vault but Bot chats, which skip them. Changes apply to chats started or reopened after the save. Saving here doesn't go through Claude Code's permission prompts or hooks.",
    "settings.persona.editor.label": "Edit {name}",
    "settings.persona.editor.save": "Save {name}",
    "settings.persona.editor.discard": "Discard changes",
    "settings.persona.editor.discard_confirm": "Discard your unsaved changes to {name}?",
    "settings.persona.editor.reread_confirm":
      "Discard your unsaved changes and read the files again?",
    "settings.persona.editor.dirty": "Unsaved changes",
    "settings.persona.editor.saved": "Saved.",
    "settings.persona.editor.no_change": "Nothing changed on disk.",
    "settings.persona.editor.conflict":
      "{name} changed on disk after you opened it. Your edit is kept and nothing was saved. The version on disk is below: choose “Replace the version on disk” to save your edit over it, or Discard changes to use it.",
    "settings.persona.editor.conflict_unread":
      "{name} changed on disk after you opened it, and the new version couldn't be read here. Your edit is kept and nothing was saved. Copy your edit, then check {name} in your own editor.",
    "settings.persona.editor.disk_version": "Version on disk",
    "settings.persona.editor.replace": "Replace the version on disk",
    "settings.persona.editor.replaced": "Saved. Your edit replaced the version on disk.",
    "settings.persona.editor.hidden":
      "Your text has invisible characters: text-direction marks, zero-width characters or control characters. They can make a line read differently on screen from what Claude Code receives. The lines that have them are below (the first {count} at most), with each one written as its code, such as <U+202E>.",
    "settings.persona.editor.hidden_lines": "Lines with invisible characters",
    "settings.persona.editor.cannot_edit":
      "The version on disk can't be edited here. Read the files again, or edit {name} in your own editor.",
    "settings.persona.editor.undo": "Put back the text from before your save",
    "settings.persona.editor.readonly.line_endings":
      "{name} uses line endings this editor can't keep (a lone CR, or a mix of kinds), so it is shown read-only. Edit it in your own editor.",
    "settings.persona.editor.readonly.control":
      "{name} contains control characters, so it is shown read-only. Edit it in your own editor.",
    "settings.persona.editor.refusal.read_only_windows":
      "{name} is read-only. Clear Read-only in its Properties, then save again.",
    "settings.persona.editor.refusal.read_only_posix":
      "{name} is read-only or locked. Make it writable, then save again.",
    "settings.persona.editor.refusal.unsafe":
      "VIKI Agent can't save {name} safely: it is a link, has other names, or its name on disk differs in letter case. Edit it in your own editor.",
    "settings.persona.editor.refusal.missing": "{name} was moved or deleted. Your edit is kept.",
    "settings.persona.editor.refusal.busy":
      "Another app is using {name}. Close it, then save again.",
    "settings.persona.editor.refusal.filesystem":
      "This vault's drive doesn't support saving from VIKI Agent.",
    "settings.persona.editor.refusal.changed":
      "This vault changed after this page read it, or it no longer accepts saves from VIKI Agent. Your edit is kept. Save again; if this message comes back, copy your edit before you leave this page.",
    "settings.persona.editor.refusal.character":
      "Line {line} has a character that can't be saved here: an invisible control character or a broken character. It is selected in the text. Delete it, then save again.",
    "settings.persona.editor.refusal.operation":
      "A note operation in this vault did not finish, so saving is blocked. Resolve it under “Unfinished operations” in the Vault view, then save again. Leaving this page drops your edit, so copy it first.",
    "settings.persona.editor.refusal.locked":
      "This vault is busy right now, for example with a backup. Your edit is kept. Try again in a moment.",
    "settings.persona.editor.refusal.protected":
      "This text matches the instructions of a protected vault, and saving it would lock this vault. Nothing was saved and your edit is kept. Change the text, then save again.",
    "settings.persona.editor.refusal.recovery":
      "A previous save of {name} needs recovery, and {name} may be missing until VIKI Agent restarts. Restarting VIKI Agent finishes that save, unless something changed in the meantime (for example the vault moved, its policy changed, or another program wrote {name}). Discarding its record under “Saves stopped partway through” in the Vault view does not bring {name} back; if another program wrote it, that version stays.",
    "settings.persona.editor.refusal.overloaded": "Saving is busy. Try again in a moment.",
    "settings.persona.editor.refusal.too_large":
      "{name} would be over 2 MiB, so it can't be saved here.",
    "settings.persona.editor.refusal.other": "{name} couldn't be saved. Your edit is kept.",
    "settings.persona.running_vault":
      "These are the instruction files in the running vault, {name}.",
    "settings.persona.files.title": "Instruction files in this vault",
    "settings.persona.files.path": "Path",
    "settings.persona.files.full_text_label": "Full text of {name}",
    "settings.persona.files.hidden":
      "This text has invisible characters (text-direction marks, zero-width characters or control characters), which can make a line read differently on screen from what Claude Code receives. Each one is written here as its code, such as <U+202E>.",
    "settings.persona.files.copy_path_label": "Copy {name} path",
    "settings.persona.files.line_one": "{count} line",
    "settings.persona.files.line_other": "{count} lines",
    "settings.persona.files.pill.marked": "Marked",
    "settings.persona.files.pill.marker_problem": "Invalid",
    "settings.persona.files.pill.unmarked": "Unmarked",
    "settings.persona.files.pill.absent": "Absent",
    "settings.persona.files.pill.unreadable": "Unreadable",
    "settings.persona.files.pill.linked": "Linked",
    "settings.persona.files.pill.unlinked": "Unlinked",
    "settings.persona.files.pill.missing": "Missing",
    "settings.persona.files.agents.marked": "Found a Persona section between the marker lines.",
    "settings.persona.files.agents.marker_problem":
      "Persona marker lines are missing, repeated, or out of order.",
    "settings.persona.files.agents.unmarked": "Found a Persona heading without marker lines.",
    "settings.persona.files.agents.absent": "Found no Persona section or Persona heading.",
    "settings.persona.files.agents.missing": "No AGENTS.md in this vault.",
    "settings.persona.files.agents.error": "Couldn't read AGENTS.md ({code}).",
    "settings.persona.files.claude.linked": "Found a standalone @AGENTS.md or @./AGENTS.md line.",
    "settings.persona.files.claude.unlinked":
      "Found no standalone @AGENTS.md or @./AGENTS.md line.",
    "settings.persona.files.claude.missing": "No CLAUDE.md in this vault.",
    "settings.persona.files.claude.error": "Couldn't read CLAUDE.md ({code}).",
    "settings.persona.state.unmarked":
      "This Persona heading has no marker lines. You can draft a conversion.",
    "settings.persona.state.absent": "No Persona section was found in AGENTS.md.",
    "settings.persona.state.edited":
      "This Persona section does not match the form. Review its text and draft a repair.",
    "settings.persona.state.marker_problem":
      "The Persona marker lines are missing, repeated, or out of order. Draft a repair in a chat.",
    "settings.persona.state.not_found": "This vault has no AGENTS.md.",
    "settings.persona.state.loading": "Reading instruction files…",
    "settings.persona.state.unavailable": "Instruction files are unavailable right now.",
    "settings.persona.state.invalid_encoding": "AGENTS.md could not be decoded as text.",
    "settings.persona.state.unsafe_path": "AGENTS.md could not be read safely.",
    "settings.persona.state.note_too_large": "AGENTS.md is too large to show here.",
    "settings.persona.draft.convert": "Draft the conversion in a new chat",
    "settings.persona.draft.repair": "Draft the repair in a new chat",
    "settings.persona.draft.add": "Add a Persona section…",
    "settings.persona.draft.open_add": "Add a Persona section",
    "settings.persona.draft.add_send": "Draft adding it in a new chat",
    "settings.persona.draft.sent":
      "Drafted in a new chat editor. Send it there to ask Claude Code to edit AGENTS.md under its own permissions.",
    "settings.persona.editor.proposal_stale":
      "AGENTS.md changed since the update was checked. Read it again first.",

    "settings.starter.badge": "Starter update available: see Settings > Instructions and Persona.",
    "settings.starter.error": "Couldn't check this vault's starter files.",
    "settings.starter.newer": "This vault's starter files are newer than this VIKI Agent's.",
    "settings.starter.current": "Starter files are up to date (version {to}).",
    "settings.starter.kept": "Kept as they were ({count})",
    "settings.starter.heading": "Starter update",
    "settings.starter.intro":
      "VIKI Agent ships starter version {to}; this vault was last updated to version {from}.",
    "settings.starter.group.add": "New files",
    "settings.starter.group.replace": "Unchanged since VIKI Agent added them",
    "settings.starter.group.editor": "AGENTS.md rules (your Persona stays)",
    "settings.starter.group.merge": "Files you changed: merged in the chat, with your OK",
    "settings.starter.group.manual": "Apply by hand (chats may not edit these)",
    "settings.starter.group.remove": "No longer shipped",
    "settings.starter.group.skip": "Couldn't read: left as is",
    "settings.starter.update_in_chat": "Update in a chat",
    "settings.starter.drafted": "Drafted in a new chat. Send it to apply the update.",
    "settings.starter.no_room": "No room for another chat draft. Close one and try again.",
    "settings.starter.open_agents": "Open AGENTS.md with the update",
    "settings.starter.agents_status": "AGENTS.md below now shows the update. Check it, then Save.",
  },
});
