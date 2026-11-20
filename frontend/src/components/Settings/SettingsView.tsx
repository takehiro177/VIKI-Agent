/**
 * The settings view: the search field, the eight-page nav and one `.settings-panel`
 * holding either a page built from the document (General, Chat, Privacy) or whatever the
 * caller passes as `children` (the Appearance, Providers, Workspaces, Persona and
 * Diagnostics pages). This file imports none of those pages — the panel is a slot, and
 * `Settings.css`'s class contract is what they render against.
 *
 * What this component owns that the pages cannot:
 * - The review panel. A 409 arrives on one page but the answer is a document, and only the view
 *   may adopt one; the page hands the error and the user's changes up.
 * - The read-only notice, because every page's controls name it through `aria-describedby`.
 * - The `<h1 tabIndex={-1}>` and the "Back to chat" button every non-chat view carries.
 *   The heading takes focus when the view is entered and **not** when the page
 *   changes: the nav's arrow keys move focus to the tab they select, and a heading that grabbed
 *   it back would make the tab strip unusable from the keyboard.
 */

import { type ReactElement, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { ShellApiError } from "../../clients/app/shellApi";
import type { Navigation, SettingsPage } from "../../commands";
import { errorText, t } from "../../i18n/t";
import type { ShellStore } from "../../state/shell";
import { useSnapshot } from "../../state/viki";
import { Loading } from "../Loading/Loading";
import { DocumentPage } from "./DocumentPage";
import { DOCUMENT_PAGES } from "./pages";
import { ReadOnlyNotice } from "./ReadOnlyNotice";
import { ReviewPanel } from "./ReviewPanel";
import { SettingsNav, tabId } from "./SettingsNav";
import { SettingsSearch } from "./SettingsSearch";
import "./Settings.css";

/** The class the pages render inside; exported for their CSS tests. */
export const SETTINGS_PANEL_CLASS = "settings-panel";

export interface SettingsViewProps {
  store: ShellStore;
  page: SettingsPage;
  onPage: (page: SettingsPage) => void;
  /** App's `navigate`: this view uses it only to leave for the chat. */
  onNavigate: (patch: Partial<Navigation>) => void;
  renderBack?: (onBack: () => void) => ReactNode;
  /** The panel's content for the pages this view does not build. */
  children?: ReactNode;
  /** A key to focus once the document page that owns it has actually rendered its control
   * (App's "Change limit"). The heading still takes focus first on entry; this only moves it
   * again once the target exists. Absent or unmatched: nothing beyond the usual entry focus.
   * The caller owns scoping this to the one navigation that set it (App's `limitFocusNav`):
   * this view has no way to tell a key meant for right now from one left over from a page the
   * user has since clicked away from, and would act on either the moment the target appears. */
  focusKey?: string | null;
  /** Called once, the moment `focusKey` is used, so the caller can clear it. */
  onFocusKeyUsed?: () => void;
}

interface Conflict {
  error: ShellApiError;
  changes: Readonly<Record<string, unknown>>;
}

export function SettingsView({
  store,
  page,
  onPage,
  onNavigate,
  renderBack,
  children,
  focusKey,
  onFocusKeyUsed,
}: SettingsViewProps): ReactElement {
  const snapshot = useSnapshot(store);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const base = useId();
  const panelId = `${base}-panel`;
  const noticeId = `${base}-readonly`;
  const pageHeadingId = `${base}-page`;
  const [query, setQuery] = useState("");
  const [conflict, setConflict] = useState<Conflict | null>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  const settings = snapshot.settings;
  const readOnlyNoticeId = settings?.read_only === true ? noticeId : null;

  // Runs after the heading's own mount-focus effect above, so a `focusKey` that already matches
  // on entry wins. Guarded on `settings` and the document pages so it does nothing before the
  // document loads or on a lane's own page — then re-fires once loading finishes, because
  // `settings` joins the dependency list below.
  useEffect(() => {
    if (!focusKey || settings === null || !DOCUMENT_PAGES.includes(page)) return;
    const target = panelRef.current?.querySelector<HTMLInputElement | HTMLSelectElement>(
      `[data-setting-key="${focusKey}"] input, [data-setting-key="${focusKey}"] select`,
    );
    if (!target) return;
    target.focus();
    target.scrollIntoView?.({ block: "center" });
    onFocusKeyUsed?.();
  }, [focusKey, settings, page, onFocusKeyUsed]);

  function reload(): void {
    const current = conflict?.error.current ?? null;
    // `command_conflict` carries no document, so the only honest reload is a fresh GET.
    if (current === null) void store.refreshSettings();
    else store.adoptSettings(current);
    setConflict(null);
  }

  function body(): ReactNode {
    // The other pages read capabilities and workspaces, never the document, so a settings
    // read that failed must not take them down with it — Diagnostics is exactly where the user
    // is sent when the app's own storage is the broken thing.
    if (!DOCUMENT_PAGES.includes(page)) return children;
    if (settings === null)
      return snapshot.settingsStatus === "error" ? (
        <p className="settings-state" role="status">
          {errorText(new ShellApiError(0, snapshot.errors.settings))}
        </p>
      ) : (
        <Loading className="settings-state">{t("common.state.loading")}</Loading>
      );
    return (
      <DocumentPage
        page={page}
        settings={settings}
        headingId={pageHeadingId}
        readOnlyNoticeId={readOnlyNoticeId}
        patch={store.patch}
        onConflict={(error, changes) => setConflict({ error, changes })}
      />
    );
  }

  return (
    <div className="settings-view" data-region="settings">
      <header className="settings-header">
        {renderBack?.(() => onNavigate({ view: "chat" })) ?? (
          <button
            type="button"
            className="btn txt sm settings-back"
            onClick={() => onNavigate({ view: "chat" })}
          >
            {t("shell.nav.back_to_chat")}
          </button>
        )}
        <div className="page-head">
          <h1 className="settings-title page-title" tabIndex={-1} ref={headingRef}>
            {t("settings.title")}
          </h1>
          <p className="page-intro">{t("settings.intro")}</p>
        </div>
        {settings !== null && (
          <SettingsSearch
            keys={settings.keys}
            query={query}
            onQuery={setQuery}
            onOpenPage={(target) => {
              onPage(target);
              setQuery("");
            }}
          />
        )}
      </header>
      <div className="settings-grid">
        <SettingsNav page={page} onPage={onPage} panelId={panelId} />
        <div
          ref={panelRef}
          className={SETTINGS_PANEL_CLASS}
          id={panelId}
          role="tabpanel"
          aria-labelledby={tabId(page)}
          tabIndex={-1}
        >
          {settings !== null && readOnlyNoticeId !== null && (
            <ReadOnlyNotice id={readOnlyNoticeId} reason={settings.reason} />
          )}
          {body()}
        </div>
      </div>
      {conflict !== null && (
        <ReviewPanel
          error={conflict.error}
          changes={conflict.changes}
          onReload={reload}
          onClose={() => setConflict(null)}
        />
      )}
    </div>
  );
}
