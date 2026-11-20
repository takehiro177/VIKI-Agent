/**
 * Buttons, generic states, and the settings source/applies chips: copy reused by two or more
 * screens, which read the same wherever they appear. Screen-specific copy belongs in that
 * screen's own module, not here.
 */

import { defineCatalog } from "./defineCatalog";

export const catalog = defineCatalog({
  en: {
    "common.source.default": "Default",
    "common.source.setting": "Set here",
    "common.source.provider": "Claude Code setting",
    "common.source.session": "Session",

    "common.applies.now": "Applied now",
    "common.applies.next_session": "From the next conversation",
    "common.applies.next_launch": "On next launch",

    "common.button.refresh": "Refresh",
    "common.button.copy": "Copy",
    "common.button.apply": "Apply",
    "common.button.reject": "Reject",
    "common.button.cancel": "Cancel",
    "common.button.close": "Close",
    "common.button.retry": "Retry",
    "common.button.cancel_bootstrap": "Cancel",

    "common.state.loading": "Loading…",
    "common.state.saving": "Saving…",
    "common.state.saved": "Saved",

    "common.chip.not_found": "Not found",
    "common.workspace.restart_required": "Switches on the next launch.",

    "common.confirm.irreversible": "This can't be undone.",

    "common.decision.hidden_characters":
      "Hidden or text-reordering characters are in this text. Each is shown as its code, for example <U+202E>.",
    "common.decision.out_of_view": "Part of this text is out of view. Scroll to read all of it.",
  },
});
