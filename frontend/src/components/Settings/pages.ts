/**
 * Which settings page a document key belongs on, and how a key's own text is looked up. Plain
 * data and pure functions: the nav, the document pages and the search all read the
 * same table, so a key can never be on two pages or, worse, on none.
 *
 * Two rules the backend can change under us, both answered here rather than at three call sites:
 * - A key present in `keys` and assigned to no page is rendered on General under "Other", so a
 *   backend addition is never silent.
 * - A key or value with no catalog row falls back to the raw token (the caller draws it in
 *   `mono`), never to the catalog-key string itself: `t()` is typed, so a computed key has to be
 *   checked against the merged catalog before it is passed in.
 */

import type { KeySpec } from "../../clients/app/shellApi";
import type { SettingsPage } from "../../commands";
import { en, type Key } from "../../i18n/index";
import { t } from "../../i18n/t";

/** The page order: the nav renders exactly this, and the search groups its hits by it. */
export const SETTINGS_PAGES: readonly SettingsPage[] = [
  "general",
  "appearance",
  "chat",
  "providers",
  "workspaces",
  "persona",
  "privacy",
  "diagnostics",
];

/** The nav tab's label, from the page's `settings.tab.*` row in `i18n/settings.ts`. */
export function tabLabel(page: SettingsPage): string {
  return t(`settings.tab.${page}`);
}

export function pageDescription(page: SettingsPage): string {
  return t(`settings.page.${page}.desc`);
}

export function tabHint(page: SettingsPage): string {
  return t(`settings.page.${page}.hint`);
}

/** The pages `DocumentPage` builds from `keys`. Appearance is built from the document too, but
 * by its own page, which needs Apply/Cancel and a live preview. */
export const DOCUMENT_PAGES: readonly SettingsPage[] = ["general", "chat", "privacy"];

/** Declaration order is row order within a page. */
const PAGE_OF_KEY: Readonly<Record<string, SettingsPage>> = {
  "identity.display_name": "general",
  "identity.assistant_name": "general",
  "identity.user_avatar_id": "general",
  "identity.assistant_avatar_id": "general",
  "workbench.code_font_size": "general",
  "workbench.line_wrap": "general",
  "workbench.worktree_cleanup": "general",
  "appearance.theme": "appearance",
  "appearance.density": "appearance",
  "accessibility.font_scale": "appearance",
  "accessibility.reduce_motion": "appearance",
  "chat.default_mode": "chat",
  "chat.default_effort": "chat",
  "chat.default_model": "chat",
  "chat.other_sign_in": "chat",
  "chat.max_live_sessions": "chat",
  "voice.read_aloud_rate": "chat",
  "voice.read_aloud_voices": "chat",
  "voice.speech_engine": "chat",
  "voice.pack_voices": "chat",
  "voice.voicebox_profile": "chat",
  "voice.voicebox_model_size": "chat",
  "voice.voicebox_url": "chat",
  "voice.speak_while_writing": "chat",
  "voice.dictation_language": "chat",
  "voice.model": "chat",
  "voice.backend": "chat",
  "voice.pause_s": "chat",
  "privacy.hide_previews": "privacy",
  "privacy.diagnostics_include_paths": "privacy",
};

/** `onboarding.completed` is written by "Set up later" and cleared by General's own button; a
 * checkbox for it would offer the user a third, contradictory way to say the same thing. The
 * routines keys belong to the Routines view; the shared notification key is controlled there and
 * by Needs you in the right rail, so no settings page shows them. The
 * read-aloud notice flag is set by the notice's own confirm button.
 * The `decision.*` keys and `layout.launcher`/`layout.launcher_panel` belong to the Decision view
 * (the launcher and its own settings page), so no settings page shows them either. `bots.hues` is
 * the Bot hue assigner's own store (botHues.ts) and Bots' own Bot color field, so no settings
 * page shows it either. `bots.icons` is the Bot icon assigner's own store, so no settings page
 * shows it either. `bots.auto_reflect` and `bots.auto_dream` are switched on a Bot's Learning
 * card, so no settings page shows them either. `updates.check` is switched on Diagnostics and
 * About's Updates card, so no settings page shows it either. */
export const HIDDEN_KEYS: ReadonlySet<string> = new Set([
  "chat.llm_connections",
  "bots.hues",
  "bots.icons",
  "bots.auto_reflect",
  "bots.auto_dream",
  "updates.check",
  "layout.left_rail_width",
  "layout.right_rail_width",
  "layout.panes",
  "layout.launcher",
  "layout.launcher_panel",
  "onboarding.completed",
  "routines.paused",
  "notifications.enabled",
  "notifications.quiet_start",
  "notifications.quiet_end",
  "voice.read_aloud_notice_seen",
  "chat.workspace_ids",
  "decision.enabled",
  "decision.paused_until",
  "decision.focus_until",
  "decision.send_level",
  "decision.never_send",
  "decision.watch.tasks",
  "decision.watch.inbox",
  "decision.watch.vault_changes",
  "decision.watch.approvals",
  "decision.watch.chats",
  "decision.watch.routines",
  "decision.watch.improve",
  "decision.watch.kanban",
  "decision.watch.vault_health",
  "decision.watch.messaging",
  "decision.cadence",
  "decision.daily_cap_usd",
  "decision.nudges_per_hour",
  "decision.alerts",
  "decision.in_context",
  "decision.model_version",
  "decision.grant.prepare",
  "decision.grant.run",
  "decision.grant.team",
  "decision.grant.look",
  "decision.grant.second_look",
  "decision.grant.project_plan",
  "decision.grant.check",
  "decision.grant.inbox_triage",
  "decision.limit.drafts",
  "decision.limit.chats",
  "decision.limit.changes",
  "decision.plan_brake",
  "decision.limit.team",
  "decision.team.handoffs",
  "decision.limit.look",
  "decision.limit.second_look",
  "decision.limit.project_plan",
  "decision.limit.message_draft",
  "decision.limit.check",
  "decision.limit.inbox_triage",
  "decision.learning",
  "decision.shadow",
  "decision.launcher_session",
]);

/** An unassigned key lands on General, under its "Other" heading. */
export function pageOfKey(key: string): SettingsPage {
  return PAGE_OF_KEY[key] ?? "general";
}

export function isOtherKey(key: string): boolean {
  return !HIDDEN_KEYS.has(key) && PAGE_OF_KEY[key] === undefined;
}

/** The keys a page renders, in the table's order, with anything the backend added appended. */
export function keysOnPage(
  page: SettingsPage,
  keys: Readonly<Record<string, KeySpec>>,
): { known: string[]; other: string[] } {
  const known = Object.keys(PAGE_OF_KEY).filter(
    (key) => !HIDDEN_KEYS.has(key) && PAGE_OF_KEY[key] === page && key in keys,
  );
  const other =
    page === "general" ? Object.keys(keys).filter((key) => isOtherKey(key)) : ([] as string[]);
  return { known, other };
}

/** A page's first titled cards. Each value is a card heading, a plain literal
 * (the interface is English; these name no catalog row). */
export const SECTION_STARTS: Readonly<Record<string, string>> = {
  "identity.display_name": "Names and avatars",
  "workbench.code_font_size": "Code view",
  "chat.default_mode": "New chats",
  "chat.max_live_sessions": "Running sessions",
  "voice.read_aloud_rate": "Read aloud",
  "voice.voicebox_profile": "Voicebox",
  "voice.dictation_language": "Voice input",
};

/** Carves a page's `known` keys (already in `PAGE_OF_KEY` order) into the cards `DocumentPage`
 * renders: a new, titled run starts at each `SECTION_STARTS` key, and any keys before a page's
 * first start — the whole page, when it has none, as Privacy does — share one untitled run. */
export function sectionsOf(known: readonly string[]): { title: string | null; keys: string[] }[] {
  const sections: { title: string | null; keys: string[] }[] = [];
  let current: { title: string | null; keys: string[] } | null = null;
  for (const key of known) {
    const start = SECTION_STARTS[key];
    if (start !== undefined || current === null) {
      current = { title: start ?? null, keys: [] };
      sections.push(current);
    }
    current.keys.push(key);
  }
  return sections;
}

/** `identity.display_name` → `identity_display_name`, the spelling `i18n/settings.ts` and
 * `i18n/appearance.ts` both write their rows with. */
function slug(key: string): string {
  return key.replace(/\./g, "_");
}

function row(candidate: string): Key | null {
  return candidate in en ? (candidate as Key) : null;
}

/** The label, or the raw key when the catalog has no row for it. */
export function keyLabel(key: string): string {
  const found = row(`settings.key.${slug(key)}.label`);
  return found === null ? key : t(found);
}

/** The description, or "" — a key with no description renders no description line. */
export function keyDesc(key: string): string {
  const found = row(`settings.key.${slug(key)}.desc`);
  return found === null ? "" : t(found);
}

/** The label for one allowed value, or null when the caller must show the raw token in `mono`. */
export function valueLabel(key: string, value: unknown): string | null {
  const found = row(`settings.value.${slug(key)}.${String(value)}`);
  if (found !== null) return t(found);
  // The language list comes from the model's measured set, so its names come from the platform.
  if (key === "voice.dictation_language" && typeof value === "string") {
    return new Intl.DisplayNames(["en"], { type: "language" }).of(value) ?? null;
  }
  return null;
}

/** Search includes the setting's words as well as its visible label and description. */
export function searchText(key: string): string {
  return `${key.replace(/[._]/g, " ")} ${keyLabel(key)} ${keyDesc(key)}`;
}
