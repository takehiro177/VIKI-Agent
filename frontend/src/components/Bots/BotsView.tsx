import { useEffect, useId, useRef, useState } from "react";
import type { WorkspaceJson } from "../../clients/app/shellApi";
import type { BotLearningApi } from "../../clients/messaging/botLearningApi";
import type { BotDetail, BotRow, BotsApi, BotsList } from "../../clients/messaging/botsApi";
import type { ProfileApi, ProfileSummary } from "../../clients/profiles/profileApi";
import type {
  PreparedProfilePreview,
  ProfileLaunchApi,
} from "../../clients/profiles/profileLaunchApi";
import { t, tf } from "../../i18n/t";
import { hasUnsavedWork } from "../../state/unsavedWork";
import type { VaultApi } from "../../state/vaultApi";
import { ConfirmDialog } from "../Dialog/ConfirmDialog";
import { Loading } from "../Loading/Loading";
import { problemText } from "../Profiles/profileCopy";
import { BotConversationHelp, BotDeliverySummary, botErrorText } from "./BotChatActivity";
import { BotFace } from "./BotFace";
import type { LearningAuto } from "./BotLearningCard";
import { BotRoster } from "./BotRoster";
import { BotRoutines, type BotRoutinesProps } from "./BotRoutines";
import { BotThreads } from "./BotThreads";
import {
  type BotStatus,
  botStatus,
  CHECKING,
  countText,
  NOT_STARTED,
  STATUS_UNAVAILABLE,
  statusCounts,
  type TeamLive,
} from "./botStatus";
import { DeskHead, ROUTINES_NEED_ORDINARY, SeatDesk } from "./SeatDesk";
import { fillTeam, openSeat, seatErrorText, seatProfile, type TeamFill } from "./seatBots";
import { botName, type SeatId, TEAM, type TeamRoster, type TeamSeat, teamRoster } from "./team";
import { READINESS_TEXT, type TeamReadiness } from "./teamReadiness";
import "./BotsView.css";

const BLOCKED_CHAT_STATES = new Set<BotDetail["chat_status"]["state"]>([
  "archived",
  "transcript_missing",
  "blocked",
  "unavailable",
]);

export interface BotsViewProps {
  api: BotsApi;
  profiles?: ProfileApi;
  launch?: ProfileLaunchApi;
  workspaces: readonly WorkspaceJson[];
  activeWorkspaceId: string;
  selectedId: string | null;
  onSelect(botId: string | null): void;
  onBack(): void;
  onCreateProfile(): void;
  onOpenChat(preview: PreparedProfilePreview, bot: BotRow): void;
  /**
   * Resolves true when the thread opened and a given `request` was placed in its unsent draft.
   * With `placeFirst` the request is the user's own text: it is placed before this page is left,
   * and a refusal stays on this page so the text is not lost with it.
   */
  onOpenThread?(
    preview: PreparedProfilePreview,
    bot: BotRow,
    request?: string,
    placeFirst?: boolean,
  ): Promise<boolean> | boolean;
  onOpenSession(sessionId: string): void;
  onUnarchive?(sessionId: string): Promise<boolean>;
  onOpenNote?(ref: string): void;
  memoryApi?: Pick<VaultApi, "entries" | "note" | "metadata">;
  onOpenUrl?(url: string): void;
  onAskInMainChat?(bot: BotRow): Promise<void>;
  /** Hands one memory file to VS Code; the memory viewer offers it only when given. */
  onOpenInEditor?(ref: string): void;
  /** Reflect and dream runs, their proposals and the auto-learning settings. */
  learningApi?: BotLearningApi;
  learningAuto?: LearningAuto;
  onThreads?(): void;
  routines?: Omit<BotRoutinesProps, "bot">;
  confirm?(message: string): boolean;
  team?: { live: TeamLive; readiness: TeamReadiness };
  initialSeat?: SeatId | null;
  onEditProfile?(profileId: string): void;
  onRecheckTeam?(): void;
}
const DELIVERY_STATUS = {
  off: "Delivery is off.",
  ready: "Mattermost delivery is enabled for this launch.",
  not_connected: "Mattermost is not connected. Check Messaging.",
  workspace_mismatch: "This Bot uses a different workspace from the Messaging bridge.",
  bot_unavailable: "This Bot is unavailable for delivery.",
  app_closing: "VIKI Agent is closing.",
};
const CHAT_STATUS = {
  unprepared: "The conversation will be prepared when you open it.",
  prepared: "The conversation is prepared.",
  reopenable: "The saved conversation can be reopened.",
  archived: "This conversation is archived. Unarchive it or start a new conversation.",
  transcript_missing:
    "The saved conversation source is unavailable. Start a new conversation to continue.",
  blocked: "The conversation is blocked by its current launch requirements.",
  unavailable: "Conversation status is unavailable. Refresh Bots to try again.",
};
function focus(element: HTMLHeadingElement | null) {
  element?.focus();
}
function teamFillText({ added, present, failed }: TeamFill): string {
  const list = new Intl.ListFormat(["en"], { type: "conjunction" });
  const names = (seats: readonly TeamSeat[]) => list.format(seats.map((row) => row.name));
  const parts = [
    ...(added.length ? [tf("bots.team.added", { names: names(added) })] : []),
    ...(present.length && (added.length || failed.length)
      ? [tf("bots.team.present", { names: names(present) })]
      : []),
    ...failed.map(({ seat, error }) =>
      tf("bots.team.failed", { name: seat.name, reason: seatErrorText(error, seat) }),
    ),
  ];
  return parts.length ? parts.join(" ") : t("bots.team.none");
}

export function BotsView({
  api,
  profiles,
  launch,
  workspaces,
  activeWorkspaceId,
  selectedId,
  onSelect,
  onBack,
  onCreateProfile,
  onOpenChat,
  onOpenThread,
  onOpenSession,
  onUnarchive,
  onOpenNote,
  memoryApi,
  onOpenUrl,
  onAskInMainChat,
  onOpenInEditor,
  learningApi,
  learningAuto,
  onThreads,
  routines,
  confirm = (message) => window.confirm(message),
  team,
  initialSeat = null,
  onEditProfile,
  onRecheckTeam,
}: BotsViewProps) {
  const id = useId();
  const openNote = onOpenNote;
  const [listing, setListing] = useState<BotsList | null>(null);
  const [detail, setDetail] = useState<BotDetail | null>(null);
  const [profileRows, setProfileRows] = useState<ProfileSummary[] | null>(profiles ? null : []);
  const [profileError, setProfileError] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<{ scope: string; text: string } | null>(null);
  const [verifiedScope, setVerifiedScope] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [creating, setCreating] = useState(false);
  const [profileId, setProfileId] = useState("");
  const [workspaceId, setWorkspaceId] = useState(activeWorkspaceId);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<
    | { kind: "newChat"; bot: BotRow }
    | { kind: "remove"; bot: BotRow; body: string }
    | { kind: "reset"; body: string }
    | null
  >(null);
  const action = useRef<AbortController | null>(null);
  const memoryRequest = useRef(false);
  const [memoryPending, setMemoryPending] = useState(false);
  const createCommand = useRef<{ key: string; id: string } | null>(null);
  const [adding, setAdding] = useState(false);
  const [teamNote, setTeamNote] = useState<string | null>(null);
  const addController = useRef<AbortController | null>(null);
  // True from a finished run's reload request until that reload's list settles.
  const addReloading = useRef(false);
  const [seat, setSeat] = useState<SeatId | null>(initialSeat);
  const shownSeat = selectedId ? null : seat;
  // Set by a roster click (or a seat chosen in the right panel) so only the desk that click shows
  // takes focus.
  const deskFocus = useRef(initialSeat !== null);
  useEffect(() => {
    if (selectedId) setSeat(null);
  }, [selectedId]);
  const scope = `${selectedId ?? (shownSeat ? `seat:${shownSeat}` : "")}:${activeWorkspaceId}`;
  const current = useRef(scope);
  current.current = scope;
  const currentBot = useRef<{
    scope: string;
    bot: BotRow;
    chatStatus: BotDetail["chat_status"];
  } | null>(null);
  if (currentBot.current?.scope !== scope) currentBot.current = null;
  const routineKey = JSON.stringify(
    routines?.snapshot.items.map((item) => [
      item.routine_id,
      item.current_revision,
      item.state_revision,
      item.active_run,
      item.last_attempt,
    ]) ?? [],
  );
  // biome-ignore lint/correctness/useExhaustiveDependencies: refresh is an explicit read request
  useEffect(() => {
    const controller = new AbortController();
    setReadError(null);
    const settled = () => {
      if (!addReloading.current) return;
      addReloading.current = false;
      setAdding(false);
    };
    api.list(controller.signal).then(
      (result) => {
        if (controller.signal.aborted) return;
        setListing(result);
        settled();
      },
      () => {
        if (controller.signal.aborted) return;
        setReadError("Bots could not be refreshed. Showing the last loaded list, if available.");
        settled();
      },
    );
    return () => controller.abort();
  }, [api, refresh]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: refresh also reloads available profiles
  useEffect(() => {
    if (!profiles) {
      setProfileRows([]);
      return;
    }
    const controller = new AbortController();
    setProfileError(false);
    profiles.list(false, controller.signal).then(
      (rows) => {
        if (!controller.signal.aborted) setProfileRows(rows.filter((row) => !row.archived));
      },
      () => {
        if (!controller.signal.aborted) setProfileError(true);
      },
    );
    return () => controller.abort();
  }, [profiles, refresh]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: refresh and routineKey reload ownership and runtime delivery
  useEffect(() => {
    const controller = new AbortController();
    setDetailError(null);
    if (selectedId)
      api.get(selectedId, controller.signal).then(
        (answer) => {
          if (controller.signal.aborted || current.current !== scope) return;
          if (answer.bot.bot_id !== selectedId) {
            invalidateAdmission();
            setDetailError({
              scope,
              text: "Bot details are unavailable. Refresh Bots to try again.",
            });
            return;
          }
          if (applyBot(answer.bot, answer.chat_status)) setVerifiedScope(scope);
        },
        (reason) => {
          if (!controller.signal.aborted && current.current === scope) {
            invalidateAdmission();
            setDetailError({ scope, text: botErrorText(reason) });
          }
        },
      );
    return () => controller.abort();
  }, [api, selectedId, scope, refresh, routineKey]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: identity or client changes cancel pending actions
  useEffect(() => {
    setCreating(false);
    setActionError(null);
    setBusy(false);
    // A confirmation names the Bot or vault it was opened for. Left pending, it would come back
    // on its own with that Bot's desk, and Reset would run against the vault now active.
    setPending(null);
    return () => {
      action.current?.abort();
      action.current = null;
    };
  }, [scope, api]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: a vault or client change stops Add missing Bots; a selection change does not
  useEffect(() => {
    setAdding(false);
    addReloading.current = false;
    setTeamNote(null);
    return () => {
      addController.current?.abort();
      addController.current = null;
    };
  }, [activeWorkspaceId, api, profiles]);
  const selected = detail?.bot.bot_id === selectedId ? detail : null;
  const offered = workspaces.filter(
    (workspace) => workspace.kind === "vault" && workspace.persistent,
  );
  const chosenProfile =
    profileRows?.find((row) => row.profile_id === profileId) ?? profileRows?.[0];
  const chosenWorkspace =
    offered.find((workspace) => workspace.workspace_id === workspaceId) ?? offered[0];
  const bots = listing?.bots ?? [];
  const existingBot = bots.find(
    (bot) =>
      bot.profile_id === chosenProfile?.profile_id &&
      bot.workspace_id === chosenWorkspace?.workspace_id,
  );
  const here = bots.filter((bot) => bot.workspace_id === activeWorkspaceId);
  const claim = teamRoster(bots, activeWorkspaceId);
  // App passes "" until shell data loads; every Bot would read as away, so hold them back.
  const vaultLoading = activeWorkspaceId === "" && team?.readiness === "loading";
  const roster: TeamRoster = !team
    ? { seats: [], others: here, away: claim.away }
    : vaultLoading
      ? { ...claim, away: [] }
      : claim;
  const seatFallback: BotStatus =
    vaultLoading || (!listing && !readError)
      ? CHECKING
      : !listing
        ? STATUS_UNAVAILABLE
        : NOT_STARTED;
  const statusOf = (bot: BotRow, live: TeamLive) =>
    vaultLoading ? CHECKING : botStatus(bot, live, activeWorkspaceId);
  const summary = team ? countText(statusCounts(here.map((bot) => statusOf(bot, team.live)))) : "";
  const activeVault = workspaces.find((workspace) => workspace.workspace_id === activeWorkspaceId);
  const selectedMemoryApi =
    selected &&
    currentBot.current?.scope === scope &&
    currentBot.current.bot.bot_id === selectedId &&
    selected.bot.bot_id === selectedId &&
    selected.bot.workspace_id === activeWorkspaceId &&
    selected.bot.workspace?.active === true &&
    activeVault?.kind === "vault" &&
    activeVault.persistent &&
    activeVault.root_available &&
    selected.bot.workspace.policy === activeVault.policy
      ? memoryApi
      : undefined;
  // One memory request at a time for the page, even across a Bot switch that remounts the card;
  // memoryPending keeps the remounted card's controls disabled instead of swallowing clicks.
  async function runMemoryAction(actionToRun: () => Promise<void>): Promise<void> {
    if (memoryRequest.current) return;
    memoryRequest.current = true;
    setMemoryPending(true);
    try {
      await actionToRun();
    } finally {
      memoryRequest.current = false;
      setMemoryPending(false);
    }
  }
  const canAddMissing =
    Boolean(profiles && team) &&
    !vaultLoading &&
    listing !== null &&
    !profileError &&
    activeVault?.kind === "vault" &&
    activeVault.persistent &&
    team?.readiness !== "no_vault" &&
    (adding || claim.seats.some((row) => !row.bot));
  // The button unmounts when the last seat fills; keep focus on the page instead of the body.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only the button's disappearance moves focus
  useEffect(() => {
    if (!canAddMissing && document.activeElement === document.body)
      document.getElementById(`${id}-title`)?.focus();
  }, [canAddMissing]);
  const seatFor = (bot: BotRow) => claim.seats.find((row) => row.bot?.bot_id === bot.bot_id)?.seat;

  function mayLeave(): boolean {
    return !hasUnsavedWork() || confirm("Discard the unsaved routine draft?");
  }
  function leave(run: () => void) {
    if (mayLeave()) run();
  }
  function reload() {
    routines?.onRefresh();
    onRecheckTeam?.();
    setRefresh((n) => n + 1);
  }
  function deskHeading(element: HTMLHeadingElement | null) {
    if (!element || !deskFocus.current) return;
    deskFocus.current = false;
    element.focus();
  }
  function applyBot(bot: BotRow, chatStatus?: BotDetail["chat_status"]) {
    const latest = currentBot.current?.bot;
    if (latest?.bot_id === bot.bot_id && latest.state_revision > bot.state_revision) return false;
    setListing((value) =>
      value
        ? {
            ...value,
            bots: value.bots.map((row) =>
              row.bot_id === bot.bot_id && row.state_revision <= bot.state_revision ? bot : row,
            ),
          }
        : value,
    );
    if (bot.bot_id !== selectedId) return false;
    const status = chatStatus ??
      currentBot.current?.chatStatus ?? { state: "unavailable", blockers: [] };
    currentBot.current = { scope, bot, chatStatus: status };
    setDetail({
      bot,
      chat_status: status,
    });
    return true;
  }
  function invalidateAdmission() {
    setVerifiedScope(null);
    const known = currentBot.current;
    if (known?.scope === scope) applyBot(known.bot, { state: "unavailable", blockers: [] });
  }
  async function run(operation: (signal: AbortSignal) => Promise<void>) {
    if (action.current) return;
    const controller = new AbortController();
    const captured = scope;
    action.current = controller;
    setBusy(true);
    setActionError(null);
    try {
      await operation(controller.signal);
    } catch (error) {
      if (!controller.signal.aborted && current.current === captured)
        setActionError(botErrorText(error));
    } finally {
      if (action.current === controller) {
        action.current = null;
        setBusy(false);
      }
    }
  }
  function valid(signal: AbortSignal, captured: string, bot?: BotRow, needsOpen = false) {
    if (signal.aborted || current.current !== captured) return false;
    if (!bot) return true;
    const latest = currentBot.current?.bot;
    if (
      latest?.bot_id === bot.bot_id &&
      latest.profile_id === bot.profile_id &&
      latest.workspace_id === bot.workspace_id &&
      latest.chat_session_id === bot.chat_session_id &&
      latest.state_revision === bot.state_revision &&
      (!needsOpen ||
        !BLOCKED_CHAT_STATES.has(currentBot.current?.chatStatus.state ?? "unavailable"))
    )
      return true;
    setActionError("This Bot changed. Refresh Bots before trying again.");
    return false;
  }
  async function create() {
    if (!chosenProfile || !chosenWorkspace) return;
    if (existingBot) {
      setCreating(false);
      onSelect(existingBot.bot_id);
      return;
    }
    const captured = scope;
    const payloadKey = `${chosenProfile.profile_id}:${chosenProfile.current_revision}:${chosenWorkspace.workspace_id}`;
    if (createCommand.current?.key !== payloadKey)
      createCommand.current = { key: payloadKey, id: crypto.randomUUID() };
    const commandId = createCommand.current.id;
    await run(async (signal) => {
      const answer = await api.create(
        commandId,
        chosenProfile.profile_id,
        chosenProfile.current_revision,
        chosenWorkspace.workspace_id,
        signal,
      );
      if (!valid(signal, captured)) return;
      createCommand.current = null;
      setCreating(false);
      setDetail({ bot: answer.bot, chat_status: { state: "unprepared", blockers: [] } });
      onSelect(answer.bot.bot_id);
      reload();
    });
  }
  function open() {
    if (!selected || selected.bot.workspace_id !== activeWorkspaceId) return;
    const bot = selected.bot;
    const captured = scope;
    void run(async (signal) => {
      const answer = await api.chat(bot, signal);
      if (!valid(signal, captured, bot, true)) return;
      if ("session_id" in answer.preview) {
        if (
          answer.bot_id !== bot.bot_id ||
          answer.state_revision !== bot.state_revision ||
          answer.preview.session_id !== bot.chat_session_id ||
          answer.preview.profile_id !== bot.profile_id ||
          answer.preview.workspace_id !== bot.workspace_id
        )
          throw new Error("invalid_response");
        leave(() => onOpenChat(answer.preview as PreparedProfilePreview, bot));
      } else
        setActionError(
          answer.preview.blockers.length
            ? `${problemText("launch_blocked")} ${answer.preview.blockers
                .map((code) => {
                  const field = answer.preview.fields.find((item) => item.field === code);
                  const permissionMode =
                    "prefs" in answer.preview ? answer.preview.prefs.mode : null;
                  return problemText(code, {
                    ...(field?.reason ? { reason: field.reason } : {}),
                    permissionMode,
                  });
                })
                .join(" ")}`
            : problemText("launch_blocked"),
        );
    });
  }
  function unarchive() {
    if (!selected || !onUnarchive || selected.bot.workspace_id !== activeWorkspaceId) return;
    const bot = selected.bot;
    const captured = scope;
    void run(async (signal) => {
      if (!valid(signal, captured, bot)) return;
      const restored = await onUnarchive(bot.chat_session_id);
      if (!valid(signal, captured, bot)) return;
      if (restored) reload();
      else setActionError(t("bots.threads.unarchiveError"));
    });
  }
  function openSeatChat(target: TeamSeat, client: ProfileApi) {
    const captured = scope;
    void run(async (signal) => {
      let opened: Awaited<ReturnType<typeof openSeat>>;
      try {
        opened = await openSeat(api, client, target, activeWorkspaceId, signal);
      } catch (error) {
        if (valid(signal, captured)) setActionError(seatErrorText(error, target));
        return;
      }
      if (!valid(signal, captured)) return;
      const { bot, chat } = opened;
      setSeat(null);
      onSelect(bot.bot_id);
      // A blocked chat leaves the Bot selected so its desk shows the blockers.
      if (!("session_id" in chat.preview)) reload();
      else leave(() => onOpenChat(chat.preview as PreparedProfilePreview, bot));
    });
  }
  async function addMissing() {
    if (!profiles || addController.current) return;
    const controller = new AbortController();
    addController.current = controller;
    setAdding(true);
    setTeamNote(t("bots.team.adding"));
    try {
      const result = await fillTeam(api, profiles, activeWorkspaceId, controller.signal);
      if (controller.signal.aborted) return;
      setTeamNote(teamFillText(result));
      addReloading.current = true;
      reload();
    } catch (error) {
      // fillTeam rethrows the original error when aborted, so the signal says whether it still counts.
      if (!controller.signal.aborted) setTeamNote(botErrorText(error));
    } finally {
      if (addController.current === controller) {
        addController.current = null;
        // After a run the reload's list clears it, so the button stays off until the list is fresh.
        if (!addReloading.current) setAdding(false);
      }
    }
  }
  function newChat() {
    if (!selected) return;
    const bot = selected.bot;
    leave(() => setPending({ kind: "newChat", bot }));
  }
  function confirmNewChat(bot: BotRow) {
    const captured = scope;
    void run(async (signal) => {
      const answer = await api.newChat(bot.bot_id, bot.state_revision, signal);
      if (!valid(signal, captured, bot)) return;
      applyBot(answer.bot, { state: "unprepared", blockers: [] });
    });
  }
  function delivery(enabled: boolean) {
    if (!selected) return;
    const bot = selected.bot;
    const captured = scope;
    void run(async (signal) => {
      const answer = await api.setDelivery(bot.bot_id, bot.state_revision, enabled, signal);
      if (valid(signal, captured, bot)) applyBot(answer.bot);
    });
  }
  function remove() {
    if (!selected) return;
    const bot = selected.bot;
    const filled = seatFor(bot);
    leave(() =>
      setPending({
        kind: "remove",
        bot,
        body: `Remove ${botName(bot)}? This pauses its owned routines and disables Mattermost delivery. Profiles, conversations and routine history remain available. Running attempts can finish locally. ${t(bot.workspace?.policy === "ordinary" ? "bots.memory.removeKept" : "bots.memory.removeKeptApp")}${filled ? ` ${filled.name} stays in your team as Not started; opening it again starts a new conversation with the same profile.` : ""}${bot.code_folder ? ` ${t("bots.code_folder.remove_copies")}` : ""}`,
      }),
    );
  }
  function confirmRemove(bot: BotRow) {
    const captured = scope;
    void run(async (signal) => {
      await api.remove(bot.bot_id, bot.state_revision, signal);
      if (!valid(signal, captured, bot)) return;
      setDetail(null);
      onSelect(null);
      reload();
    });
  }
  function reset() {
    leave(() => {
      const workspaceName =
        workspaces.find((workspace) => workspace.workspace_id === activeWorkspaceId)?.name ??
        activeWorkspaceId;
      setPending({
        kind: "reset",
        body: `Reset Bots in ${workspaceName}? This removes all Bots in this workspace, pauses their owned routines and disables delivery. It keeps profiles, conversations and routine history.${here.length ? ` ${t(here.some((row) => row.workspace?.policy === "ordinary") ? "bots.memory.removeKept" : "bots.memory.removeKeptApp")}` : ""}${claim.seats.some((row) => row.bot) ? " Your team's seats stay listed as Not started." : ""}${here.some((row) => row.code_folder) ? ` ${t("bots.code_folder.remove_copies")}` : ""}`,
      });
    });
  }
  function confirmReset() {
    const captured = scope;
    void run(async (signal) => {
      await api.reset(activeWorkspaceId, signal);
      if (!valid(signal, captured, selected?.bot)) return;
      if (selected?.bot.workspace_id === activeWorkspaceId) {
        setDetail(null);
        onSelect(null);
      }
      reload();
    });
  }
  function confirmPending() {
    if (!pending) return;
    setPending(null);
    if (pending.kind === "newChat") confirmNewChat(pending.bot);
    else if (pending.kind === "remove") confirmRemove(pending.bot);
    else confirmReset();
  }

  function seatDesk(target: TeamSeat, current: { live: TeamLive; readiness: TeamReadiness }) {
    const client = profiles;
    const vault = workspaces.find((workspace) => workspace.workspace_id === activeWorkspaceId);
    const hasVault =
      vault?.kind === "vault" && vault.persistent && current.readiness !== "no_vault";
    return (
      <SeatDesk
        key={target.seat}
        seat={target}
        status={seatFallback}
        live={current.live}
        vaultName={vault?.name ?? activeWorkspaceId}
        policy={vault?.policy ?? "ordinary"}
        profile={
          client && !profileError && profileRows ? seatProfile(profileRows, target) : undefined
        }
        canOpen={Boolean(client) && !profileError && listing !== null && hasVault}
        blockedReason={
          !client || profileError
            ? "Profiles could not be loaded. Refresh Bots to try again."
            : !listing
              ? (readError ?? "Bots are still loading.")
              : !hasVault
                ? READINESS_TEXT.no_vault
                : null
        }
        busy={busy}
        onOpen={() => {
          if (client) openSeatChat(target, client);
        }}
        headingRef={deskHeading}
      />
    );
  }

  // Mounted next to the control that opened it: Reset under the page head, New conversation and
  // Remove Bot under the desk's last action row. The dialog makes the rest of the window inert,
  // so one mounted a screen away from its opener would move the page off the desk to show itself.
  const confirmation = pending ? (
    <ConfirmDialog
      label={
        pending.kind === "newChat"
          ? t("bots.confirm.newChat")
          : pending.kind === "remove"
            ? tf("bots.confirm.remove", { name: botName(pending.bot) })
            : t("bots.confirm.reset")
      }
      body={
        pending.kind === "newChat"
          ? `Start a new conversation for ${botName(pending.bot)}? This keeps the previous conversation and its history. The new conversation uses the current profile revision.`
          : pending.body
      }
      confirmLabel={
        pending.kind === "newChat"
          ? t("bots.confirm.newChat")
          : pending.kind === "remove"
            ? t("bots.confirm.removeAction")
            : t("bots.confirm.reset")
      }
      destructive={pending.kind !== "newChat"}
      onConfirm={confirmPending}
      onCancel={() => setPending(null)}
      className={pending.kind === "reset" ? undefined : "confirm-dialog-under-opener"}
    />
  ) : null;

  return (
    <section className="bots-view" aria-labelledby={`${id}-title`}>
      <button type="button" className="btn txt" onClick={() => leave(onBack)}>
        Back to chat
      </button>
      <header className="page-head">
        <h1 className="page-title" id={`${id}-title`} ref={focus} tabIndex={-1}>
          Bots
        </h1>
        <p className="page-intro">Manage your Bots, profiles, routines and delivery.</p>
        {summary ? <p className="bots-summary">{summary}</p> : null}
        <div className="page-actions">
          <button type="button" className="btn go" onClick={() => leave(() => setCreating(true))}>
            New Bot
          </button>
          <button type="button" className="btn alt" disabled={busy} onClick={reload}>
            Refresh Bots
          </button>
          <button
            type="button"
            className="btn txt"
            disabled={busy || adding || !activeWorkspaceId}
            onClick={reset}
          >
            Reset Bots in this workspace
          </button>
        </div>
      </header>
      {pending?.kind === "reset" ? confirmation : null}
      {readError ? <p role="alert">{readError}</p> : null}
      {actionError ? <p role="alert">{actionError}</p> : null}
      {detailError?.scope === scope ? <p role="alert">{detailError.text}</p> : null}
      {teamNote ? <p role="status">{teamNote}</p> : null}
      {!listing && !readError ? <Loading>Loading Bots…</Loading> : null}
      {!team && listing?.bots.length === 0 ? (
        <p>No Bots yet. Create a profile, then add it as a Bot.</p>
      ) : null}
      <div className="bots-body">
        <BotRoster
          roster={roster}
          team={team ?? null}
          seatFallback={seatFallback}
          activeWorkspaceId={activeWorkspaceId}
          selectedId={selectedId}
          selectedSeat={shownSeat}
          {...(canAddMissing
            ? { onAddMissing: () => void addMissing(), adding: adding || busy }
            : {})}
          onSelectBot={(botId) =>
            leave(() => {
              deskFocus.current = true;
              setCreating(false);
              setSeat(null);
              onSelect(botId);
            })
          }
          onSelectSeat={(next) =>
            leave(() => {
              deskFocus.current = true;
              setCreating(false);
              setSeat(next);
              onSelect(null);
            })
          }
        />
        <div className="bots-pane">
          {creating ? (
            <section className="bots-create" aria-label="New Bot">
              <h2>New Bot</h2>
              <p>A Bot uses an existing profile's name, role, avatar and configuration.</p>
              <button type="button" className="btn txt" onClick={() => leave(onCreateProfile)}>
                {t("bots.profile.new")}
              </button>
              {profileError ? (
                <p role="alert">Profiles could not be loaded. Refresh Bots to try again.</p>
              ) : profileRows === null ? (
                <Loading>Loading profiles…</Loading>
              ) : profileRows.length === 0 ? (
                <>
                  <p>Create a profile in Bot settings, then return to Team.</p>
                  <button type="button" className="btn go" onClick={() => leave(onCreateProfile)}>
                    Create profile
                  </button>
                </>
              ) : (
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void create();
                  }}
                >
                  <label>
                    Profile
                    <select
                      value={chosenProfile?.profile_id ?? ""}
                      disabled={busy}
                      onChange={(event) => setProfileId(event.target.value)}
                    >
                      {profileRows.map((row) => (
                        <option key={row.profile_id} value={row.profile_id}>
                          {row.identity.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Workspace
                    <select
                      value={chosenWorkspace?.workspace_id ?? ""}
                      disabled={busy}
                      onChange={(event) => setWorkspaceId(event.target.value)}
                    >
                      {offered.map((row) => (
                        <option key={row.workspace_id} value={row.workspace_id}>
                          {row.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {offered.length === 0 ? (
                    <p>No persistent vault is available for this Bot.</p>
                  ) : null}
                  {existingBot ? <p>{t("bots.profile.existing")}</p> : null}
                  <button
                    type="submit"
                    className="btn go"
                    disabled={busy || !chosenProfile || !chosenWorkspace}
                  >
                    {existingBot ? t("bots.profile.open") : "Create Bot"}
                  </button>
                </form>
              )}
              <button
                type="button"
                className="btn alt"
                disabled={busy}
                onClick={() => setCreating(false)}
              >
                Cancel
              </button>
            </section>
          ) : selected ? (
            <section key={selected.bot.bot_id} className="bots-detail" aria-label="Selected Bot">
              <DeskHead
                face={
                  <BotFace
                    profileId={selected.bot.profile_id}
                    identity={selected.bot.profile?.identity ?? null}
                    name={botName(selected.bot)}
                    size={64}
                    {...(team ? { state: statusOf(selected.bot, team.live).state } : {})}
                    frontDoor={seatFor(selected.bot)?.seat === "chief"}
                    decorative
                  />
                }
                name={botName(selected.bot)}
                role={seatFor(selected.bot)?.role ?? ""}
                status={team ? statusOf(selected.bot, team.live).text : ""}
                headingRef={deskHeading}
              />
              <p>{`Current profile revision ${selected.bot.profile?.revision ?? "unavailable"} · Conversation revision ${selected.bot.chat.profile_revision ?? "not prepared"}`}</p>
              <p>
                {selected.chat_status.state === "unavailable"
                  ? t("profiles.chat.unavailable")
                  : BLOCKED_CHAT_STATES.has(selected.chat_status.state)
                    ? problemText(
                        selected.chat_status.state === "blocked"
                          ? "launch_blocked"
                          : selected.chat_status.state === "archived"
                            ? "profile_blocked"
                            : selected.chat_status.state,
                      )
                    : CHAT_STATUS[selected.chat_status.state]}
              </p>
              {selected.chat_status.blockers.length > 0 ? (
                <ul>
                  {selected.chat_status.blockers.map((code) => (
                    <li key={code}>{problemText(code)}</li>
                  ))}
                </ul>
              ) : null}
              {selected.bot.workspace_id !== activeWorkspaceId ? (
                <p>Restart VIKI Agent in this Bot's workspace to open its conversation.</p>
              ) : null}
              {selected.bot.workspace && selected.bot.workspace.policy !== "ordinary" ? (
                <p className="bots-note">{ROUTINES_NEED_ORDINARY}</p>
              ) : null}
              <div className="bots-actions bot-chat-actions">
                <button
                  type="button"
                  className="btn go"
                  disabled={
                    busy ||
                    selected.bot.workspace_id !== activeWorkspaceId ||
                    BLOCKED_CHAT_STATES.has(selected.chat_status.state)
                  }
                  onClick={open}
                >
                  Open conversation
                </button>
                <span className="bots-note">{t("bots.chat.main")}</span>
              </div>
              {onThreads ? (
                <button type="button" className="btn alt" onClick={() => leave(onThreads)}>
                  Bot threads
                </button>
              ) : null}
              {launch && onOpenThread ? (
                <BotThreads
                  key={selected.bot.bot_id}
                  api={api}
                  launch={launch}
                  bot={selected.bot}
                  vaultActive={selected.bot.workspace_id === activeWorkspaceId}
                  disabled={busy || selected.bot.workspace_id !== activeWorkspaceId}
                  onPrepared={(preview, request, placeFirst) =>
                    mayLeave() ? onOpenThread(preview, selected.bot, request, placeFirst) : false
                  }
                  onOpen={(sessionId) => leave(() => onOpenSession(sessionId))}
                  {...(onUnarchive ? { onUnarchive } : {})}
                  {...(openNote ? { onOpenNote: openNote } : {})}
                  {...(selectedMemoryApi ? { memoryApi: selectedMemoryApi } : {})}
                  {...(selectedMemoryApi ? { runMemoryAction, memoryPending } : {})}
                  {...(selectedMemoryApi && onOpenInEditor ? { onOpenInEditor } : {})}
                  {...(onOpenUrl ? { onOpenUrl } : {})}
                  {...(learningApi ? { learningApi } : {})}
                  {...(learningAuto ? { learningAuto } : {})}
                  {...(onAskInMainChat
                    ? {
                        // The main chat replaces this page, so ask before the request starts.
                        onAskInMainChat: (target: BotRow) =>
                          mayLeave() ? onAskInMainChat(target) : Promise.resolve(),
                      }
                    : {})}
                />
              ) : null}
              {routines ? (
                <fieldset
                  className="bots-routine-interactions"
                  aria-label="Bot routine controls"
                  disabled={busy}
                >
                  {busy ? (
                    <Loading>
                      Applying Bot change… Routine editing is paused until this request finishes.
                    </Loading>
                  ) : null}
                  <BotRoutines
                    key={selected.bot.bot_id}
                    {...routines}
                    bot={selected.bot}
                    onRefresh={reload}
                    {...(verifiedScope !== scope
                      ? { blocked: "Bot details are unavailable. Refresh Bots to try again." }
                      : {})}
                  />
                </fieldset>
              ) : (
                <p>Routine controls are unavailable.</p>
              )}
              <div className="bots-actions">
                {selected.bot.chat.archived && onUnarchive ? (
                  <button
                    type="button"
                    className="btn alt"
                    disabled={busy || selected.bot.workspace_id !== activeWorkspaceId}
                    onClick={unarchive}
                  >
                    {t("bots.threads.unarchive")}
                  </button>
                ) : null}
                <button type="button" className="btn alt" disabled={busy} onClick={newChat}>
                  New conversation
                </button>
                {onEditProfile && selected.bot.profile ? (
                  <button
                    type="button"
                    className="btn alt"
                    disabled={busy}
                    onClick={() => leave(() => onEditProfile(selected.bot.profile_id))}
                  >
                    Edit profile
                  </button>
                ) : null}
                <button type="button" className="btn txt" disabled={busy} onClick={remove}>
                  Remove Bot
                </button>
              </div>
              {pending && pending.kind !== "reset" && pending.bot.bot_id === selected.bot.bot_id
                ? confirmation
                : null}
              <BotConversationHelp />
              <section className="bots-delivery" aria-label="Mattermost delivery">
                <h3>Mattermost delivery</h3>
                <p>
                  Delivery is off at each launch. Enable it explicitly for this launch to send
                  routine results to your configured Mattermost direct message.
                </p>
                <p>
                  Messages leave this computer and follow your Mattermost server's retention policy.
                  Replies there continue the dedicated Messaging conversation; open the named Bot or
                  run in VIKI Agent to continue that conversation.
                </p>
                <p>{DELIVERY_STATUS[selected.bot.delivery.reason]}</p>
                <button
                  type="button"
                  className="btn alt"
                  disabled={
                    busy || (!selected.bot.delivery.enabled && !selected.bot.delivery.available)
                  }
                  onClick={() => delivery(!selected.bot.delivery.enabled)}
                >
                  {selected.bot.delivery.enabled
                    ? "Disable Mattermost delivery"
                    : "Enable Mattermost delivery for this launch"}
                </button>
              </section>
              <BotDeliverySummary bot={selected.bot} onOpenSession={onOpenSession} />
            </section>
          ) : shownSeat && team ? (
            seatDesk(TEAM.find((row) => row.seat === shownSeat) as TeamSeat, team)
          ) : (
            <p>
              {selectedId && detailError?.scope !== scope ? (
                <Loading inline announce={false}>
                  Loading Bot details…
                </Loading>
              ) : selectedId ? (
                "Bot details are unavailable."
              ) : (
                <span className="bots-home">
                  <strong>{t("bots.home.title")}</strong>
                  <span>{t("bots.home.body")}</span>
                  <span className="bots-note">{t("bots.home.context")}</span>
                </span>
              )}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
