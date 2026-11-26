/** The vault card: the user's one vault — its name, policy and path when a registered
 * row backs it — plus Change vault…/New vault…/Details. Name falls back from the registered row
 * matching the active id, to the capabilities summary when that id is active but not (yet) a row
 * (an unregistered override), to "Chat only" when the active summary is itself the shared
 * scratch (`kind === "scratch"`, `OtherVaults.tsx`'s own test for the same fact). Change vault…
 * prefers the OS folder picker (ADR-050) when one is available, reusing the same openVault +
 * select-after-action flow CreateVaultForm/OpenVaultForm already run; otherwise, and for "Type a
 * path instead", it hands off to the page's own `workspace.open` command. Details toggles
 * PolicyPanel for the displayed active vault; with no active vault, it shows the selection
 * for the next launch. */
import { type ReactElement, useEffect, useState } from "react";
import type { ActiveSummary, ShellApi, WorkspaceJson } from "../../clients/app/shellApi";
import { ShellApiError } from "../../clients/app/shellApi";
import type { CommandArg, CommandId } from "../../commands";
import { commandFor } from "../../commands";
import { errorText, t, tf } from "../../i18n/t";
import type { ShellSnapshot, ShellStore } from "../../state/shell";
import { useFolderPicker } from "../Onboarding/FolderPicker";
import { useSelectAfterVaultAction } from "../Onboarding/useVaultAction";
import "./Pages.css";
import { PolicyPanel } from "./PolicyPanel";
import { describedBy, ReasonHint, reasonTitle } from "./ReasonHint";
import "./VaultCard.css";

export interface VaultCardProps {
  api: ShellApi | null;
  store: ShellStore;
  snapshot: ShellSnapshot;
  onCommand: (id: CommandId, arg?: CommandArg) => void;
}

type VaultSource = WorkspaceJson | ActiveSummary;

/** `WorkspacesPage.tsx`'s own `policyChip`, generalised to the one shape it actually needs
 * (`WorkspaceJson` and `ActiveSummary` both carry `kind`/`policy`), since that function is not
 * exported and this card can show either a full row or just the capabilities summary. */
function policyChip(source: Pick<VaultSource, "kind" | "policy">): string {
  const { policy } = source;
  if (source.kind === "code" && policy !== "protected") {
    return t(
      policy === "ordinary" ? "workspaces.code.chip.trusted" : "workspaces.code.chip.untrusted",
    );
  }
  return t(`workspaces.policy.${policy}`);
}

export function VaultCard({ api, store, snapshot, onCommand }: VaultCardProps): ReactElement {
  const picker = useFolderPicker();
  // The hook requires a non-null client; `changeVault` below never calls `select.run` while
  // `api` is null, so the cast is safe even though this card (like WorkspacesPage) can render
  // before the shell API exists.
  const select = useSelectAfterVaultAction(api as ShellApi, store);
  const [picking, setPicking] = useState(false);
  const [openError, setOpenError] = useState<ShellApiError | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [starterNotify, setStarterNotify] = useState(false);

  const capabilities = snapshot.capabilities;
  const listing = snapshot.workspaces;
  const workspaces = listing?.workspaces ?? [];
  const activeSummary = capabilities?.workspace.active ?? null;
  const activeId = activeSummary?.id ?? null;
  const activeRow =
    activeId !== null
      ? workspaces.find((workspace) => workspace.workspace_id === activeId)
      : undefined;
  // The scratch placeholder is never a presentable "name" (OtherVaults.tsx's own `showChatOnly`
  // reads the same `kind !== "scratch"` fact for the same reason).
  const realActive =
    activeSummary !== null && activeSummary.kind !== "scratch" ? activeSummary : null;
  const source: VaultSource | null = activeRow ?? realActive;
  const name = source !== null ? source.name : t("workspaces.other.chat_only");

  // biome-ignore lint/correctness/useExhaustiveDependencies: fetch only on an api or active-id change (mirrors PolicyPanel's own policy(workspaceId) effect); a workspaces refresh that leaves the active id the same must not re-fetch.
  useEffect(() => {
    setStarterNotify(false);
    const starter = api?.starterPlan;
    if (
      starter === undefined ||
      activeRow === undefined ||
      activeRow.kind !== "vault" ||
      activeRow.bootstrap === "pending" ||
      activeRow.policy === "protected"
    )
      return;
    let cancelled = false;
    starter(activeRow.workspace_id).then(
      (plan) => {
        if (!cancelled) setStarterNotify(plan.notify);
      },
      () => {
        // Errors render nothing: the badge just stays hidden.
      },
    );
    return () => {
      cancelled = true;
    };
  }, [api, activeId]);

  const selectedId = listing?.selected_id ?? null;
  const selectedRow =
    selectedId !== null
      ? workspaces.find((workspace) => workspace.workspace_id === selectedId)
      : undefined;
  const inspectedId = source === null ? selectedId : activeId;

  const openState = commandFor("workspace.open", snapshot);
  const createState = commandFor("workspace.create", snapshot);
  const showPicker = picker?.status.available === true;
  const busy = picking || select.outcome?.kind === "pending";

  async function changeVault(): Promise<void> {
    if (api === null) return;
    if (picker === null || picker.status.available !== true) {
      onCommand("workspace.open");
      return;
    }
    setOpenError(null);
    setPicking(true);
    try {
      const paths = await picker.pick(t("workspaces.vault.pick_title"), false);
      const path = paths[0];
      if (path === undefined) return; // Cancelled: leave whatever vault is active alone.
      const result = await api.openVault(crypto.randomUUID(), path);
      select.run(result.workspace.workspace_id);
    } catch (error) {
      setOpenError(error instanceof ShellApiError ? error : new ShellApiError(0, "unavailable"));
    } finally {
      setPicking(false);
    }
  }

  return (
    <div className="ws-vault">
      <h3 className="lbl">{t("workspaces.vault.heading")}</h3>
      <div className="settings-card">
        <div className="settings-card-head">
          <svg
            className="ico ws-vault-icon"
            width="16"
            height="16"
            viewBox="0 0 16 16"
            aria-hidden="true"
          >
            <path d="M2 5.3C2 4.6 2.6 4 3.3 4h2.8c.4 0 .7.1 1 .4l.7.7h4.9c.7 0 1.3.6 1.3 1.3v4.9c0 .7-.6 1.3-1.3 1.3H3.3C2.6 12.6 2 12 2 11.3V5.3Z" />
          </svg>
          <span className="settings-card-title">{name}</span>
          {source !== null ? <span className="chip dim">{policyChip(source)}</span> : null}
          {source !== null ? (
            <span className="settings-pill" data-tone="ready">
              {t("workspaces.active")}
            </span>
          ) : null}
        </div>
        {starterNotify ? <p className="settings-note">{t("settings.starter.badge")}</p> : null}
        {activeRow !== undefined ? (
          <div className="mono workspace-root">{activeRow.root}</div>
        ) : null}

        <div className="settings-actions">
          <button
            type="button"
            className="btn alt sm"
            aria-disabled={!openState.enabled || busy}
            aria-describedby={describedBy("vault-change-reason", openState)}
            title={reasonTitle(openState)}
            onClick={() => {
              if (openState.enabled && !busy) void changeVault();
            }}
          >
            {t("workspaces.vault.change")}
          </button>
          <ReasonHint id="vault-change-reason" state={openState} />
          <button
            type="button"
            className="btn alt sm"
            aria-disabled={!createState.enabled}
            aria-describedby={describedBy("vault-new-reason", createState)}
            title={reasonTitle(createState)}
            onClick={() => {
              if (createState.enabled) onCommand("workspace.create");
            }}
          >
            {t("workspaces.vault.new")}
          </button>
          <ReasonHint id="vault-new-reason" state={createState} />
          <button
            type="button"
            className="btn alt sm"
            aria-expanded={detailsOpen}
            onClick={() => setDetailsOpen((open) => !open)}
          >
            {t("workspaces.vault.details")}
          </button>
          {showPicker ? (
            <>
              <button
                type="button"
                className="btn txt sm"
                aria-disabled={!openState.enabled}
                aria-describedby={describedBy("vault-type-path-reason", openState)}
                title={reasonTitle(openState)}
                onClick={() => {
                  if (openState.enabled) onCommand("workspace.open");
                }}
              >
                {t("workspaces.type_path")}
              </button>
              <ReasonHint id="vault-type-path-reason" state={openState} />
            </>
          ) : null}
        </div>

        {capabilities?.workspace.restart_required === true && selectedRow !== undefined ? (
          <p className="settings-note" role="status">
            {tf("workspaces.vault.next_launch", { name: selectedRow.name })}
          </p>
        ) : null}
        {select.outcome?.kind === "pending" ? (
          <p role="status">{t("onboarding.select.selecting")}</p>
        ) : null}
        {select.outcome?.kind === "done" && !select.outcome.restartRequired ? (
          <p role="status">{t("workspaces.select.already_active")}</p>
        ) : null}
        {select.outcome?.kind === "failed" ? (
          <p className="settings-error" role="alert">
            {errorText(select.outcome.error)}
          </p>
        ) : null}
        {openError !== null ? (
          <p className="settings-error" role="alert">
            {errorText(openError)}
          </p>
        ) : null}

        {detailsOpen && inspectedId !== null ? (
          <PolicyPanel
            workspaceId={inspectedId}
            api={api}
            workspaces={workspaces}
            onChanged={() => void store.refreshWorkspaces()}
          />
        ) : null}
      </div>
    </div>
  );
}
