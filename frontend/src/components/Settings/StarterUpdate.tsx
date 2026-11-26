/**
 * Shows this vault's VIKI Agent starter update, if any: the plan the backend computed, rendered
 * as grouped file changes plus the one or two actions that apply it (a chat hand-off, and — only
 * when `AGENTS.md`'s own rules changed and the vault takes edits — the Persona page's editor).
 * This component writes nothing itself; `onDraft` and `onReviewAgents` are both owned elsewhere.
 */
import { type ReactElement, useEffect, useState } from "react";
import type { StarterAction, StarterItem, StarterPlan } from "../../clients/app/shellApi";
import type { Key } from "../../i18n/index";
import { t, tf } from "../../i18n/t";
import { chatItems, starterHandoff } from "./starterHandoff";

export interface StarterUpdateProps {
  workspaceId: string;
  load: (id: string) => Promise<StarterPlan>;
  refreshKey: string;
  protectedVault: boolean;
  onDraft: (text: string) => boolean;
  onReviewAgents?: (proposal: { proposal: string; sha256: string }) => void;
}

type State = { status: "loading" } | { status: "error" } | { status: "ok"; plan: StarterPlan };

/** Display order and label for every action, independent of which ones a chat ever dispatches
 * (`chatItems` in starterHandoff.ts): every group with at least one item is shown. */
const GROUPS: readonly { action: StarterAction; label: Key }[] = [
  { action: "add", label: "settings.starter.group.add" },
  { action: "replace", label: "settings.starter.group.replace" },
  { action: "editor", label: "settings.starter.group.editor" },
  { action: "merge", label: "settings.starter.group.merge" },
  { action: "manual", label: "settings.starter.group.manual" },
  { action: "remove", label: "settings.starter.group.remove" },
  { action: "skip", label: "settings.starter.group.skip" },
];

function itemRow(item: StarterItem): ReactElement {
  return (
    <details className="settings-details" key={item.path}>
      <summary>{item.path}</summary>
      <pre>{item.diff ?? item.text}</pre>
    </details>
  );
}

export function StarterUpdate({
  workspaceId,
  load,
  refreshKey,
  protectedVault,
  onDraft,
  onReviewAgents,
}: StarterUpdateProps): ReactElement | null {
  const [state, setState] = useState<State>({ status: "loading" });
  const [draftResult, setDraftResult] = useState<"sent" | "no_editor" | null>(null);
  const [agentsOpened, setAgentsOpened] = useState(false);

  // biome-ignore lint/correctness/useExhaustiveDependencies: refreshKey has no use in the body; it exists only so the caller can force a reload with no workspace change.
  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    setDraftResult(null);
    setAgentsOpened(false);
    load(workspaceId).then(
      (plan) => {
        if (!cancelled) setState({ status: "ok", plan });
      },
      () => {
        if (!cancelled) setState({ status: "error" });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [workspaceId, refreshKey, load]);

  if (state.status === "loading") return null;
  if (state.status === "error") {
    return <p className="settings-error">{t("settings.starter.error")}</p>;
  }

  const { plan } = state;
  if (plan.state === "none") return null;
  if (plan.state === "newer") {
    return <p className="settings-note">{t("settings.starter.newer")}</p>;
  }

  const showAgents = plan.agents !== null && !protectedVault && onReviewAgents !== undefined;

  function handleReviewAgents(): void {
    if (plan.agents === null || onReviewAgents === undefined) return;
    onReviewAgents(plan.agents);
    setAgentsOpened(true);
  }

  if (plan.state === "current") {
    // Files the owner kept (every item but `skip`, which was never a decision): shown collapsed
    // below the up-to-date line, with no "Update in a chat" button — a declined change is not
    // offered again, so this state has nothing left to dispatch through a chat.
    const kept = plan.items.filter((item) => item.action !== "skip");
    return (
      <>
        <p className="settings-note">{tf("settings.starter.current", { to: plan.to_version })}</p>
        {kept.length > 0 && (
          <details className="settings-details">
            <summary>{tf("settings.starter.kept", { count: kept.length })}</summary>
            {kept.map(itemRow)}
          </details>
        )}
        {showAgents && (
          <div className="settings-actions">
            <button type="button" className="btn alt sm" onClick={handleReviewAgents}>
              {t("settings.starter.open_agents")}
            </button>
          </div>
        )}
        {agentsOpened && (
          <p className="settings-note" role="status">
            {t("settings.starter.agents_status")}
          </p>
        )}
      </>
    );
  }

  const from = plan.from_version === null ? "unknown" : String(plan.from_version);
  const showChat = chatItems(plan, protectedVault).length > 0 || plan.notify;

  function handleDraft(): void {
    const sent = onDraft(starterHandoff(plan, protectedVault));
    setDraftResult(sent ? "sent" : "no_editor");
  }

  return (
    <section className="settings-card">
      <div className="settings-card-head">
        <h3 className="settings-card-title">{t("settings.starter.heading")}</h3>
      </div>
      <p>{tf("settings.starter.intro", { to: plan.to_version, from })}</p>

      {GROUPS.map(({ action, label }) => {
        const items = plan.items.filter((item) => item.action === action);
        if (items.length === 0) return null;
        return (
          <div key={action}>
            <h4>{t(label)}</h4>
            {items.map(itemRow)}
          </div>
        );
      })}

      {(showChat || showAgents) && (
        <div className="settings-actions">
          {showChat && (
            <button type="button" className="btn go sm" onClick={handleDraft}>
              {t("settings.starter.update_in_chat")}
            </button>
          )}
          {showAgents && (
            <button type="button" className="btn alt sm" onClick={handleReviewAgents}>
              {t("settings.starter.open_agents")}
            </button>
          )}
        </div>
      )}

      {draftResult === "sent" && (
        <p className="settings-note" role="status">
          {t("settings.starter.drafted")}
        </p>
      )}
      {draftResult === "no_editor" && (
        <p className="settings-error" role="alert">
          {t("settings.starter.no_room")}
        </p>
      )}
      {agentsOpened && (
        <p className="settings-note" role="status">
          {t("settings.starter.agents_status")}
        </p>
      )}
    </section>
  );
}
