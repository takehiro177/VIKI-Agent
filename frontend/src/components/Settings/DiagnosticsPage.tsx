/**
 * Diagnostics and About: `capabilities.app`, the redacted document from
 * `api.diagnostics()` rendered read-only through `JsonTree`, and the version facts.
 * About offers bundled license downloads and the existing What's new help page.
 */
import { useEffect, useState } from "react";
import type { JsonObject, ShellApi } from "../../clients/app/shellApi";
import { ShellApiError } from "../../clients/app/shellApi";
import type { CommandState, Navigation } from "../../commands";
import { commandFor } from "../../commands";
import { errorText, t } from "../../i18n/t";
import { DATA_FLOWS, NOT_AFFILIATED, PRODUCT_LINE } from "../../productFacts";
import type { ShellSnapshot } from "../../state/shell";
import { Loading } from "../Loading/Loading";
import "./Settings.css";
import { openHelp, useHelpAvailable } from "../Help/helpStore";
import { JsonTree } from "./JsonTree";
import { UpdatesCard, type UpdatesCardProps } from "./UpdatesCard";

/** No `onCommand`: this page issues no command a dispatcher needs to see. `onNavigate` stays —
 * the paths note's "Open Privacy settings" button uses it directly, never `settings.open`'s own
 * dispatcher case (which drops the target `page`). `commandFor` is read only as a table lookup,
 * to gate that one button: this page is also `RecoveryView`'s body, where navigation can never
 * take effect, and a refused command is shown disabled with its reason, never hidden. */
export interface DiagnosticsPageProps {
  api: ShellApi | null;
  snapshot: ShellSnapshot;
  onNavigate: (nav: Partial<Navigation>) => void;
  approvalsRead?:
    | {
        status: "off" | "reading" | "ready" | "failed";
        readAt: string | null;
        workspaceId: string | null;
        attention: number | null;
      }
    | undefined;
  /** Present only when the window hosts an update check (`capabilities.features.updates`);
   * absent, the Updates card does not render at all. */
  updates?: UpdatesCardProps | undefined;
}

/** The reason a disabled control is off (the same pattern as `ProvidersPage` and
 * `WorkspacesPage` — deliberately re-declared per page rather than shared). */
function ReasonHint({ id, state }: { id: string; state: CommandState }) {
  if (state.enabled || state.reason === null) return null;
  return (
    <span id={id} className="sr-only">
      {t(state.reason)}
    </span>
  );
}

function describedBy(id: string, state: CommandState): string | undefined {
  return state.enabled || state.reason === null ? undefined : id;
}

function reasonTitle(state: CommandState): string | undefined {
  return state.enabled || state.reason === null ? undefined : t(state.reason);
}

/** `en-US`, medium date / short time (e.g. "Oct 9, 2026, 12:00 PM"); the raw string stands in for
 * a value `Date` cannot parse. Re-declared per page rather than shared, same as `ReasonHint`. */
function localTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

type ApprovalsStatus = NonNullable<DiagnosticsPageProps["approvalsRead"]>["status"];

/** The Approvals-read card-head pill: word and `data-tone` per status. `tone` absent renders no
 * `data-tone` attribute, which `.settings-pill` reads as neutral. */
const APPROVALS_PILL: Record<
  ApprovalsStatus,
  { word: string; tone?: "attention" | "ready" | "error" }
> = {
  off: { word: "Off" },
  reading: { word: "Reading", tone: "attention" },
  ready: { word: "OK", tone: "ready" },
  failed: { word: "Failed", tone: "error" },
};

export function DiagnosticsPage({
  api,
  snapshot,
  onNavigate,
  approvalsRead,
  updates,
}: DiagnosticsPageProps) {
  const [read, setRead] = useState<{
    api: ShellApi | null;
    document: JsonObject | null;
    error: ShellApiError | null;
    pending: boolean;
  }>({ api, document: null, error: null, pending: api !== null });
  const [reloadToken, setReloadToken] = useState(0);
  const helpAvailable = useHelpAvailable();

  // biome-ignore lint/correctness/useExhaustiveDependencies: reloadToken is a manual refresh trigger (the Reload button), never read inside the effect body.
  useEffect(() => {
    if (api === null) {
      setRead({ api, document: null, error: null, pending: false });
      return;
    }
    let cancelled = false;
    setRead((previous) => ({
      api,
      document: previous.api === api ? previous.document : null,
      error: null,
      pending: true,
    }));
    api.diagnostics().then(
      (value) => {
        if (!cancelled) setRead({ api, document: value, error: null, pending: false });
      },
      (loadError: unknown) => {
        if (!cancelled)
          setRead((previous) => ({
            api,
            document: previous.api === api ? previous.document : null,
            error:
              loadError instanceof ShellApiError ? loadError : new ShellApiError(0, "unavailable"),
            pending: false,
          }));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [api, reloadToken]);

  const {
    document: document_,
    error,
    pending,
  } = read.api === api ? read : { document: null, error: null, pending: api !== null };
  const app = snapshot.capabilities?.app ?? null;
  const includesPaths = snapshot.settings?.effective["privacy.diagnostics_include_paths"] === true;
  const privacyState = commandFor("settings.open", snapshot);
  const pill = APPROVALS_PILL[approvalsRead?.status ?? "off"];

  return (
    <section className="settings-page" aria-labelledby="diagnostics-page-title">
      <h2 className="settings-page-title" id="diagnostics-page-title">
        {t("settings.tab.diagnostics")}
      </h2>
      <p className="settings-page-intro">{t("settings.page.diagnostics.desc")}</p>

      <section aria-labelledby="about-title" className="settings-card">
        <div className="settings-card-head">
          <h3 id="about-title" className="settings-card-title">
            {t("workspaces.about.title")}
          </h3>
        </div>
        <p>{PRODUCT_LINE}</p>
        <p>{NOT_AFFILIATED}</p>
        {app !== null ? (
          <dl className="settings-facts">
            {app.version !== undefined ? (
              <div>
                <dt>{t("workspaces.diagnostics.version")}</dt>
                <dd className="mono">{app.version}</dd>
              </div>
            ) : null}
            <div>
              <dt>{t("workspaces.diagnostics.mode")}</dt>
              <dd>{app.mode}</dd>
            </div>
            <div>
              <dt>{t("workspaces.diagnostics.launch_id")}</dt>
              <dd className="mono">{snapshot.capabilities?.launch.id}</dd>
            </div>
          </dl>
        ) : null}
        <p>
          <a href="/LICENSE.txt" download="VIKI-Agent-LICENSE.txt">
            {t("workspaces.about.license")}
          </a>
        </p>
        <p>
          <a href="/THIRD_PARTY_NOTICES.txt" download="THIRD_PARTY_NOTICES.txt">
            {t("workspaces.about.third_party_notices")}
          </a>
        </p>
        <button
          type="button"
          className="btn txt sm"
          disabled={!helpAvailable}
          title={helpAvailable ? undefined : t("workspaces.about.help_unavailable")}
          onClick={() => {
            if (helpAvailable) openHelp({ kind: "whats-new" });
          }}
        >
          {t("workspaces.about.whats_new")}
        </button>
      </section>

      {updates !== undefined ? <UpdatesCard {...updates} /> : null}

      <section aria-labelledby="data-flows-title" className="settings-card">
        <div className="settings-card-head">
          <h3 id="data-flows-title" className="settings-card-title">
            Where your data goes
          </h3>
        </div>
        <dl className="settings-facts">
          {DATA_FLOWS.map(({ label, fact }) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{fact}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-labelledby="approvals-read-title" className="settings-card">
        <div className="settings-card-head">
          <h3 id="approvals-read-title" className="settings-card-title">
            Last approvals check
          </h3>
          <span className="settings-pill" data-tone={pill.tone}>
            {pill.word}
          </span>
        </div>
        {approvalsRead === undefined || approvalsRead.status === "off" ? (
          <p>
            Approvals have not been checked in this window. A vault must be open and its approvals
            available.
          </p>
        ) : approvalsRead.status === "reading" ? (
          <Loading announce={false}>Reading…</Loading>
        ) : approvalsRead.status === "failed" && approvalsRead.readAt === null ? (
          <p>Could not check approvals. No successful check yet.</p>
        ) : (
          <>
            <p>
              Last read{" "}
              {approvalsRead.readAt ? (
                <time dateTime={approvalsRead.readAt}>{localTime(approvalsRead.readAt)}</time>
              ) : null}
              {approvalsRead.attention !== null ? (
                <> · {approvalsRead.attention} need attention</>
              ) : null}
              {approvalsRead.workspaceId ? (
                <>
                  {" "}
                  · workspace <code>{approvalsRead.workspaceId}</code>
                </>
              ) : null}
            </p>
            {approvalsRead.status === "failed" ? (
              <p>The latest read failed; this is the last successful read.</p>
            ) : null}
          </>
        )}
      </section>

      <div className="settings-card">
        <div className="settings-card-head">
          <h3 className="settings-card-title">Diagnostics report</h3>
          <div className="settings-actions">
            <button
              type="button"
              className="btn alt sm"
              disabled={api === null || pending}
              onClick={() => {
                if (api !== null && !pending) setReloadToken((token) => token + 1);
              }}
            >
              {t("workspaces.diagnostics.reload")}
            </button>
          </div>
        </div>
        {!includesPaths ? (
          <div className="settings-actions">
            <p className="settings-note">{t("workspaces.diagnostics.paths_note")}</p>
            <button
              type="button"
              className="btn alt sm"
              aria-disabled={!privacyState.enabled}
              aria-describedby={describedBy("diagnostics-privacy-reason", privacyState)}
              title={reasonTitle(privacyState)}
              onClick={() => {
                if (privacyState.enabled) onNavigate({ view: "settings", page: "privacy" });
              }}
            >
              {t("workspaces.diagnostics.open_privacy")}
            </button>
            <ReasonHint id="diagnostics-privacy-reason" state={privacyState} />
          </div>
        ) : null}
        {error !== null ? (
          <p className="settings-error" role="alert">
            {errorText(error)}
          </p>
        ) : null}
        {api === null ? (
          <p role="status">Diagnostics are unavailable in this window.</p>
        ) : pending ? (
          <Loading>
            {document_ === null ? "Loading diagnostics…" : "Reloading diagnostics…"}
          </Loading>
        ) : null}
        {error !== null && document_ !== null ? (
          <p>The latest diagnostics read failed. Showing the last successful read.</p>
        ) : null}
        {document_ !== null ? (
          <details className="settings-details">
            <summary>View report details</summary>
            <JsonTree value={document_} />
          </details>
        ) : null}
      </div>
    </section>
  );
}
