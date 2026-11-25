import { useEffect, useRef, useState } from "react";
import type { BotLearningApi } from "../../clients/messaging/botLearningApi";
import type { BotRow, BotsApi, BotThreads as Threads } from "../../clients/messaging/botsApi";
import {
  botMemoryFolder,
  type PreparedProfilePreview,
  type ProfileLaunchApi,
} from "../../clients/profiles/profileLaunchApi";
import { t, tf } from "../../i18n/t";
import type { VaultApi } from "../../state/vaultApi";
import { Loading } from "../Loading/Loading";
import { problemText } from "../Profiles/profileCopy";
import { BotLearningCard, type LearningAuto } from "./BotLearningCard";
import { BotMemoryCard } from "./BotMemoryCard";

export function BotThreads({
  api,
  launch,
  bot,
  vaultActive = false,
  disabled,
  onPrepared,
  onOpen,
  onUnarchive,
  onOpenNote,
  memoryApi,
  onOpenUrl,
  onAskInMainChat,
  onOpenInEditor,
  runMemoryAction,
  memoryPending = false,
  learningApi,
  learningAuto,
}: {
  api: BotsApi;
  launch: ProfileLaunchApi;
  bot: BotRow;
  /** The Bot's vault is the active workspace, so a withheld memory API is not "open the vault". */
  vaultActive?: boolean;
  disabled: boolean;
  /**
   * Resolves true when the draft carrying `request` was placed in the opened thread. `placeFirst`
   * marks the user's own text, placed before the page is left (`BotsView`'s `onOpenThread`).
   */
  onPrepared(
    preview: PreparedProfilePreview,
    request?: string,
    placeFirst?: boolean,
  ): Promise<boolean> | boolean;
  onOpen(sessionId: string): void;
  onUnarchive?(sessionId: string): Promise<boolean>;
  onOpenNote?(ref: string): void;
  memoryApi?: Pick<VaultApi, "entries" | "note" | "metadata">;
  onOpenUrl?(url: string): void;
  onAskInMainChat?(bot: BotRow): Promise<void>;
  onOpenInEditor?(ref: string): void;
  runMemoryAction?(action: () => Promise<void>): Promise<void>;
  /** A memory request of this page is still running, possibly from a card since unmounted. */
  memoryPending?: boolean;
  /** Reflect and dream runs and the proposals they leave; only an ordinary vault learns. */
  learningApi?: BotLearningApi;
  learningAuto?: LearningAuto;
}) {
  const [listing, setListing] = useState<Threads | null>(null);
  const [readError, setReadError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [archived, setArchived] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const request = useRef<{ key: string; command: string; session: string } | null>(null);
  const action = useRef<AbortController | null>(null);
  const key = `${bot.bot_id}:${bot.profile_id}:${bot.workspace_id}:${bot.profile?.revision}`;
  const current = useRef(key);
  current.current = key;
  const memoryEligible = Boolean(
    memoryApi &&
      !disabled &&
      bot.workspace?.active === true &&
      bot.profile &&
      !bot.profile.archived,
  );
  const currentMemoryEligible = useRef(memoryEligible);
  currentMemoryEligible.current = memoryEligible;
  const withMemoryLock = runMemoryAction ?? ((action: () => Promise<void>) => action());

  // biome-ignore lint/correctness/useExhaustiveDependencies: refresh is an explicit read request
  useEffect(() => {
    const controller = new AbortController();
    setReadError(false);
    setListing(null);
    api.threads?.(bot.bot_id, controller.signal).then(
      (value) => {
        if (!controller.signal.aborted && current.current === key) setListing(value);
      },
      () => {
        if (!controller.signal.aborted && current.current === key) setReadError(true);
      },
    );
    return () => controller.abort();
  }, [api, bot.bot_id, key, refresh]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: identity or memory eligibility changes cancel pending preparation
  useEffect(() => {
    setBusy(false);
    setError(null);
    return () => {
      action.current?.abort();
      action.current = null;
    };
  }, [
    key,
    launch,
    memoryApi,
    disabled,
    bot.workspace?.active,
    bot.workspace?.policy,
    bot.profile?.archived,
  ]);

  async function create() {
    if (disabled || action.current || !bot.profile || bot.profile.archived) return;
    const requestKey = `${key}:task`;
    if (request.current?.key !== requestKey)
      request.current = {
        key: requestKey,
        command: crypto.randomUUID(),
        session: crypto.randomUUID(),
      };
    const captured = request.current;
    const controller = new AbortController();
    action.current = controller;
    setBusy(true);
    setError(null);
    try {
      const answer = await launch.prepare(
        bot.profile_id,
        captured.command,
        captured.session,
        bot.profile.revision,
        bot.workspace_id,
        controller.signal,
        bot.bot_id,
      );
      if (controller.signal.aborted || current.current !== key) return;
      if (!("session_id" in answer) || !answer.launchable) {
        const permissionMode = "prefs" in answer ? answer.prefs.mode : null;
        setError(
          answer.blockers.length
            ? `${problemText("launch_blocked")} ${answer.blockers
                .map((code) => {
                  const field = answer.fields.find((item) => item.field === code);
                  return problemText(code, {
                    ...(field?.reason ? { reason: field.reason } : {}),
                    permissionMode,
                  });
                })
                .join(" ")}`
            : problemText("launch_blocked"),
        );
        return;
      }
      if (
        answer.session_id !== captured.session ||
        answer.profile_id !== bot.profile_id ||
        answer.profile_revision !== bot.profile.revision ||
        answer.workspace_id !== bot.workspace_id
      )
        throw new Error("invalid_response");
      request.current = null;
      void onPrepared(answer);
      setRefresh((value) => value + 1);
    } catch {
      if (!controller.signal.aborted && current.current === key)
        setError(t("bots.threads.prepareError"));
    } finally {
      if (action.current === controller) {
        action.current = null;
        setBusy(false);
      }
    }
  }

  /**
   * Prepares a Bot-bound task thread whose unsent draft is `compose(the Bot's memory folder)`.
   * Resolves true when the thread was prepared and `onPrepared` placed the draft, false when
   * nothing was drafted. `kind` keys the retry, so a repeat of the same request reuses its command
   * and session, also after a draft that was not placed.
   */
  async function prepareMemory(
    kind: "review" | "forget" | "replace",
    compose: (ref: string) => string,
  ): Promise<boolean> {
    if (!memoryEligible || action.current || !bot.profile || bot.profile.archived) return false;
    const requestKey = `${key}:${kind}`;
    if (request.current?.key !== requestKey)
      request.current = {
        key: requestKey,
        command: crypto.randomUUID(),
        session: crypto.randomUUID(),
      };
    const captured = request.current;
    const controller = new AbortController();
    action.current = controller;
    setBusy(true);
    setError(null);
    try {
      const answer = await launch.prepare(
        bot.profile_id,
        captured.command,
        captured.session,
        bot.profile.revision,
        bot.workspace_id,
        controller.signal,
        bot.bot_id,
      );
      if (controller.signal.aborted || current.current !== key || !currentMemoryEligible.current)
        return false;
      if (!("session_id" in answer) || !answer.launchable) {
        const permissionMode = "prefs" in answer ? answer.prefs.mode : null;
        setError(
          answer.blockers.length
            ? `${problemText("launch_blocked")} ${answer.blockers
                .map((code) => {
                  const field = answer.fields.find((item) => item.field === code);
                  return problemText(code, {
                    ...(field?.reason ? { reason: field.reason } : {}),
                    permissionMode,
                  });
                })
                .join(" ")}`
            : problemText("launch_blocked"),
        );
        return false;
      }
      if (
        answer.session_id !== captured.session ||
        answer.profile_id !== bot.profile_id ||
        answer.profile_revision !== bot.profile.revision ||
        answer.workspace_id !== bot.workspace_id
      )
        throw new Error("invalid_response");
      if (!answer.bot_memory || answer.bot_memory.bot_id !== bot.bot_id) {
        setError(t("bots.memory.draftError"));
        return false;
      }
      const placed = await onPrepared(
        answer,
        compose(botMemoryFolder(answer.bot_memory)),
        kind === "replace",
      );
      if (placed) request.current = null;
      setRefresh((value) => value + 1);
      return placed;
    } catch {
      if (!controller.signal.aborted && current.current === key)
        setError(t("bots.memory.draftError"));
      return false;
    } finally {
      if (action.current === controller) {
        action.current = null;
        setBusy(false);
      }
    }
  }

  async function unarchive(sessionId: string) {
    if (disabled || action.current || !onUnarchive) return;
    const captured = key;
    const controller = new AbortController();
    action.current = controller;
    setBusy(true);
    setError(null);
    try {
      const restored = await onUnarchive(sessionId);
      if (controller.signal.aborted || current.current !== captured) return;
      if (restored) setRefresh((value) => value + 1);
      else setError(t("bots.threads.unarchiveError"));
    } catch {
      if (!controller.signal.aborted && current.current === captured)
        setError(t("bots.threads.unarchiveError"));
    } finally {
      if (action.current === controller) {
        action.current = null;
        setBusy(false);
      }
    }
  }

  const shown = listing?.threads.filter((thread) => archived || !thread.archived) ?? [];
  return (
    <section className="bot-threads" aria-label={t("bots.threads")}>
      <BotMemoryCard
        {...(memoryApi ? { api: memoryApi } : {})}
        workspaceId={bot.workspace_id}
        bot={bot}
        vaultActive={vaultActive}
        kept={bot.workspace?.policy === "ordinary" ? "vault" : "app"}
        disabled={disabled || busy || memoryPending || !bot.profile || bot.profile.archived}
        onOpenNote={onOpenNote ?? (() => {})}
        onOpenUrl={onOpenUrl ?? (() => {})}
        {...(memoryApi
          ? {
              onReview: () =>
                withMemoryLock(async () => {
                  await prepareMemory("review", (ref) => tf("bots.memory.reviewRequest", { ref }));
                }),
              onForget: () =>
                withMemoryLock(async () => {
                  await prepareMemory("forget", (ref) => tf("bots.memory.forgetRequest", { ref }));
                }),
              onDraftReplacement: async (text: string) => {
                let drafted = false;
                await withMemoryLock(async () => {
                  drafted = await prepareMemory("replace", () => text);
                });
                return drafted;
              },
              ...(onOpenInEditor ? { onOpenInEditor } : {}),
              ...(onAskInMainChat
                ? {
                    onAskInMainChat: (currentBot: BotRow) =>
                      withMemoryLock(() => onAskInMainChat(currentBot)),
                  }
                : {}),
            }
          : {})}
      />
      {learningApi && bot.workspace?.policy === "ordinary" ? (
        <BotLearningCard
          api={learningApi}
          bot={bot}
          disabled={disabled || !bot.profile || bot.profile.archived}
          {...(learningAuto ? { auto: learningAuto } : {})}
        />
      ) : null}
      <div className="bots-actions">
        <h3>{t("bots.threads")}</h3>
        <button
          type="button"
          className="btn alt"
          disabled={disabled || busy || !bot.profile || bot.profile.archived}
          onClick={() => void create()}
        >
          {t(busy ? "bots.threads.preparing" : "bots.threads.new")}
        </button>
      </div>
      <p>{t("bots.threads.help")}</p>
      <p className="bots-note">{t("bots.threads.context")}</p>
      {error ? <p role="alert">{error}</p> : null}
      {api.threads ? (
        <>
          <label className="bot-threads-filter">
            <input
              type="checkbox"
              checked={archived}
              onChange={(event) => setArchived(event.target.checked)}
            />{" "}
            {t("bots.threads.showArchived")}
          </label>
          {readError ? (
            <p role="alert">{t("bots.threads.error")}</p>
          ) : !listing ? (
            <Loading>{t("bots.threads.loading")}</Loading>
          ) : (
            <>
              {shown.length === 0 ? (
                <p>{t("bots.threads.empty")}</p>
              ) : (
                <ul className="bot-thread-list">
                  {shown.map((thread) => (
                    <li key={thread.session_id}>
                      <button
                        type="button"
                        className="bot-thread-row"
                        disabled={disabled || busy || thread.archived}
                        aria-label={tf("bots.threads.open", {
                          title: thread.title || thread.session_id,
                        })}
                        onClick={() => onOpen(thread.session_id)}
                      >
                        <span>{thread.title || thread.session_id}</span>
                        <span className="bots-note">
                          <time dateTime={thread.created_at}>
                            {new Date(thread.created_at).toLocaleDateString()}
                          </time>
                          {` · ${thread.session_id.slice(0, 8)}`}
                          {thread.archived ? ` · ${t("bots.threads.archived")}` : ""}
                        </span>
                      </button>
                      {thread.archived && onUnarchive ? (
                        <button
                          type="button"
                          className="btn txt sm"
                          disabled={disabled || busy}
                          onClick={() => void unarchive(thread.session_id)}
                        >
                          {t("bots.threads.unarchive")}
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              <p className="bots-note">{t("bots.threads.shared")}</p>
              {listing.has_more ? <p className="bots-note">{t("bots.threads.more")}</p> : null}
            </>
          )}
          <button
            type="button"
            className="btn txt sm"
            disabled={busy || disabled}
            onClick={() => setRefresh((value) => value + 1)}
          >
            {t("bots.threads.refresh")}
          </button>
        </>
      ) : null}
    </section>
  );
}
