/** Compose generation-scoped session views with durable, independent editor buffers.
 * Turn submission uses the encrypted draft service and durable command ledger.
 * Claude Code's own permissions, transcript reads and all external actions remain server-owned.
 */
import {
  type ReactElement,
  type ReactNode,
  type RefObject,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { Rest } from "./api";
import type { AgentChangesApi } from "./clients/app/agentChangesApi";
import type { ProviderLoginApi } from "./clients/app/providerLoginApi";
import {
  type CreateVaultResult,
  type SettingsView as SettingsDocument,
  type ShellApi,
  ShellApiError,
} from "./clients/app/shellApi";
import type { RoutineItem, RoutinesApi } from "./clients/automation/routinesApi";
import type { ChecksApi } from "./clients/code/checksApi";
import { type CodeApi, CodeApiError } from "./clients/code/codeApi";
import type { PreviewsApi } from "./clients/code/previewsApi";
import type { ReviewApi } from "./clients/code/reviewApi";
import { type RootsApi, RootsApiError, type SessionRootJson } from "./clients/code/rootsApi";
import { type TerminalApi, TerminalApiError } from "./clients/code/terminalApi";
import type { BetweenChatsApi } from "./clients/conversations/betweenChatsApi";
import type { ChatArchivesApi } from "./clients/conversations/chatArchivesApi";
import type { ContextApi } from "./clients/conversations/contextApi";
import type { ConversationSearchApi } from "./clients/conversations/conversationSearchApi";
import {
  type CommandReceipt,
  type DurableApi,
  DurableApiError,
} from "./clients/conversations/durableApi";
import type { InventoryApi } from "./clients/conversations/inventoryApi";
import type { LimitsApi } from "./clients/conversations/limitsApi";
import type { McpServersApi } from "./clients/conversations/mcpServersApi";
import type { RunsApi } from "./clients/conversations/runsApi";
import type {
  SessionColor,
  SessionMetadata,
  SessionMetadataApi,
  SessionMetadataPatch,
} from "./clients/conversations/sessionMetadataApi";
import type { SessionPrefsApi } from "./clients/conversations/sessionPrefsApi";
import {
  type SessionWindowsApi,
  SessionWindowsError,
} from "./clients/conversations/sessionWindowsApi";
import type { SubagentsApi } from "./clients/conversations/subagentsApi";
import {
  type DecisionLoopApi,
  DecisionLoopApiError,
  type ItemAction,
  type ItemView,
  type PageFacts,
} from "./clients/decisionLoop/types";
import type { AssetsApi } from "./clients/media/assetsApi";
import type { AvatarsApi } from "./clients/media/avatarsApi";
import type { BotRow, BotsApi } from "./clients/messaging/botsApi";
import type { BotUsageApi } from "./clients/messaging/botUsageApi";
import type { MessagingApi } from "./clients/messaging/messagingApi";
import type { NativeAssetsApi } from "./clients/profiles/nativeAssetsApi";
import type { ProfileApi } from "./clients/profiles/profileApi";
import {
  botMemoryFolder,
  type PreparedProfilePreview,
  type ProfileLaunchApi,
  ProfileLaunchApiError,
  type ProfileSessionIdentity,
} from "./clients/profiles/profileLaunchApi";
import type { CapturesApi } from "./clients/vaults/capturesApi";
import { type CommandId, commandFor, type Navigation, type View } from "./commands";
import { createAvatarUrls } from "./components/Avatar/avatarUrls";
import { type Identity, IdentityContext } from "./components/Avatar/identity";
import { BotChatActivity, botErrorText } from "./components/Bots/BotChatActivity";
import { BotChatBar } from "./components/Bots/BotChatBar";
import type { BotRoutinesProps } from "./components/Bots/BotRoutines";
import { BotsView } from "./components/Bots/BotsView";
import { BotThreadWorkspace } from "./components/Bots/BotThreadWorkspace";
import {
  assigningHues,
  BOT_HUES_KEY,
  BOT_ICONS_KEY,
  type BotHueControls,
  BotHuesContext,
  createBotLookQueue,
  hueAssigner,
  parseBotHues,
  parseBotIcons,
} from "./components/Bots/botHues";
import type { TeamLive } from "./components/Bots/botStatus";
import { fillStarterTeam } from "./components/Bots/seatBots";
import { type SeatId, teamRoster } from "./components/Bots/team";
import { useTeamReadiness } from "./components/Bots/teamReadiness";
import { BotUsage } from "./components/Bots/usage/BotUsage";
import { CapabilitiesView } from "./components/Capabilities/CapabilitiesView";
import { CaptureInboxRow } from "./components/Captures/CaptureInboxRow";
import { CaptureTriage } from "./components/Captures/CaptureTriage";
import type { TriageChoice } from "./components/Captures/triage";
import { BlockedCard } from "./components/Cards/BlockedCard";
import {
  CAPACITY_FAILED,
  PermissionCard,
  QuestionCard,
  REQUEST_OVER,
  SAVE_FAILED,
  UNCERTAIN_RESULT,
} from "./components/Cards/Cards";
import { ErrorCard, RateLimitCard } from "./components/Cards/ErrorCard";
import { SignInStrip } from "./components/Cards/SignInStrip";
import type { ComposerActivity } from "./components/Composer/Composer";
import { commandState, DraftRecovery, excerptRefusal } from "./components/Composer/DraftRecovery";
import { withExcerpt } from "./components/Composer/ExcerptChips";
import { McpServersButton } from "./components/Composer/McpServers";
import { MediaComposer, type MediaComposerProps } from "./components/Composer/MediaComposer";
import { useModelChoice } from "./components/Composer/modelChoice";
import { type PrestartChip, usePrestartChip } from "./components/Composer/prestartChip";
import { ContextMeter } from "./components/Context/ContextMeter";
import { ContextView } from "./components/Context/ContextView";
import { DecisionTab, type RequestedCall } from "./components/Context/DecisionTab";
import { ChatFoldersChip } from "./components/Conversation/ChatFoldersChip";
import { Conversation, Reply, type SavedScroll } from "./components/Conversation/Conversation";
import { ConversationHeader } from "./components/Conversation/ConversationHeader";
import { MarkdownParserContext } from "./components/Conversation/markdownParser";
import { cutPromptNotice, RetryReview } from "./components/Conversation/RetryReview";
import { SkillTurnActions } from "./components/Conversation/SkillTurnActions";
import { EmptyState, HistoryNote } from "./components/Conversation/States";
import { ArchivePreview, ArchiveTools } from "./components/ConversationTools/ConversationTools";
import { RevisionSearch } from "./components/ConversationTools/RevisionSearch";
// The component itself fixes the name `DataView`; it shadows the unrelated
// global (a typed-array view constructor this file never uses), as its own module does.
// biome-ignore lint/suspicious/noShadowRestrictedNames: see above
import { DataView } from "./components/Data/DataView";
import { DecisionLoopPage } from "./components/DecisionLoop/DecisionLoopPage";
import { LAUNCHER_SIZE, Launcher } from "./components/DecisionLoop/Launcher";
import { LauncherChat } from "./components/DecisionLoop/LauncherChat";
import {
  type LauncherMenuChoice,
  LauncherPanel,
  type ReviewChoice,
} from "./components/DecisionLoop/LauncherPanel";
import { firstRun, pageFacts } from "./components/DecisionLoop/pageFacts";
import { useLauncherPosition } from "./components/DecisionLoop/useLauncherPosition";
import {
  launcherShortcutHint,
  useLauncherShortcuts,
} from "./components/DecisionLoop/useLauncherShortcuts";
import { type PresenceNote, usePresence } from "./components/DecisionLoop/usePresence";
import { createApprovalExecutor } from "./components/Decisions/approvalExecutor";
import { DecisionInbox } from "./components/Decisions/DecisionInbox";
import { DecisionSection, type ProposalRow } from "./components/Decisions/DecisionSection";
import { decisionLine } from "./components/Decisions/decisionQueue";
import { ProposalInspector } from "./components/Decisions/ProposalInspector";
import { QuestionProposalInspector } from "./components/Decisions/QuestionProposalInspector";
import { SkillProposalInspector } from "./components/Decisions/SkillProposalInspector";
import { HeaderMenu } from "./components/Header/HeaderMenu";
import { HeaderTrail, type ToolsToggle } from "./components/Header/HeaderTrail";
import { VaultBanner } from "./components/Header/VaultBanner";
import { WRAP_UP_PROMPT } from "./components/Header/wrapUp";
import { HelpCenter } from "./components/Help/HelpCenter";
import { KanbanView } from "./components/Kanban/KanbanView";
import type { WakeResult } from "./components/Kanban/useCardAnswer";
import { AgentsSection } from "./components/LeftRail/AgentsSection";
import { ArchiveBar } from "./components/LeftRail/ArchiveBar";
import { LeftRail, type RailDestination } from "./components/LeftRail/LeftRail";
import { elsewhereMap } from "./components/LeftRail/rowState";
import { SessionList } from "./components/LeftRail/SessionList";
import { MessagingView } from "./components/Messaging/MessagingView";
import { FolderPickerProvider } from "./components/Onboarding/FolderPicker";
import { OnboardingView } from "./components/Onboarding/OnboardingView";
import { PathForm } from "./components/Onboarding/PathForm";
import type { DropPayload } from "./components/Panes/paneDrop";
import { type AgentsSectionId, AgentsView } from "./components/Profiles/AgentsView";
import {
  type ConnectionLabel,
  modelLabel,
  useConnectionLabels,
} from "./components/Profiles/modelLabel";
import { NativeAssets } from "./components/Profiles/NativeAssets";
import { ProfileSessionReview } from "./components/Profiles/ProfileSessionReview";
import { ProfilesView } from "./components/Profiles/ProfilesView";
import { type RunningRow, RunningWork } from "./components/Profiles/RunningWork";
import { ToolsInventory } from "./components/Profiles/ToolsInventory";
import { RecoveryView } from "./components/Recovery/RecoveryView";
import { AgentRuns } from "./components/RightRail/AgentRuns";
import { BetweenChats } from "./components/RightRail/BetweenChats";
import { ContextSummary } from "./components/RightRail/ContextSummary";
import { NeedsYou } from "./components/RightRail/NeedsYou";
import { useProposalAttention } from "./components/RightRail/proposalAttention";
import { RailSection } from "./components/RightRail/RailSection";
import { RailFooter, UpNextSection } from "./components/RightRail/RailSections";
import { RequestActions } from "./components/RightRail/RequestActions";
import { RightRail } from "./components/RightRail/RightRail";
import { useRunStops } from "./components/RightRail/RunStop";
import { useRunRequests } from "./components/RightRail/runRequests";
import { SkillActivity } from "./components/RightRail/SkillActivity";
import { SkillsSummary } from "./components/RightRail/SkillsSummary";
import { TeamStrip } from "./components/RightRail/TeamStrip";
import { VaultTiles } from "./components/RightRail/VaultTiles";
import { RoutinesRail } from "./components/Routines/RoutinesRail";
import { RoutinesView } from "./components/Routines/RoutinesView";
import { SearchPalette } from "./components/Search/SearchPalette";
import { MissingSessions } from "./components/Sessions/MissingSessions";
import { SessionRenameDialog } from "./components/Sessions/SessionRenameDialog";
import {
  SessionOrganizer,
  SessionTabs,
  type SessionTabsProps,
  type TabMenuItem,
  useSessionMetadata,
} from "./components/Sessions/Sessions";
import { AppearancePage } from "./components/Settings/AppearancePage";
import {
  type AppearanceValues,
  appearanceFromSettings,
  applyAppearance,
  usePrefersLight,
} from "./components/Settings/appearance";
import { DiagnosticsPage } from "./components/Settings/DiagnosticsPage";
import { PersonaPage } from "./components/Settings/PersonaPage";
import { PolicyPanel } from "./components/Settings/PolicyPanel";
import { ProvidersPage } from "./components/Settings/ProvidersPage";
import { ReviewPanel } from "./components/Settings/ReviewPanel";
import { SettingsView } from "./components/Settings/SettingsView";
import { WorkspacesPage } from "./components/Settings/WorkspacesPage";
import { AgentChanges } from "./components/Shell/AgentChanges";
import { ChatOnlyBanner } from "./components/Shell/ChatOnlyBanner";
import { ReadinessBanner } from "./components/Shell/ReadinessBanner";
import { ReconnectBanner } from "./components/Shell/ReconnectBanner";
import { Shell, type ShellPanes } from "./components/Shell/Shell";
import { StaleWindow } from "./components/Shell/StaleWindow";
import { useLayoutMode } from "./components/Shell/useLayoutMode";
import { ChatSources } from "./components/Sources/ChatSources";
import { usePublishSources } from "./components/Sources/publishSources";
import type { NoteDraft } from "./components/Sources/saveAsNote";
import { useSourcesEnv } from "./components/Sources/sourcesEnv";
import { GitChip } from "./components/StatusBar/GitChip";
import { notReadyNotice } from "./components/StatusBar/notReady";
import { PlanLimitMeter } from "./components/StatusBar/PlanLimitMeter";
import { StatusBar } from "./components/StatusBar/StatusBar";
import { type LiveSessionSummary, SessionLimitToast } from "./components/Toast/SessionLimitToast";
import { AppMenu } from "./components/TopBar/AppMenu";
import { HelpMenu } from "./components/TopBar/HelpMenu";
import { TopBar } from "./components/TopBar/TopBar";
import {
  VaultBrowser,
  type VaultNavigationHandle,
  type VaultNavigationSnapshot,
} from "./components/VaultBrowser/VaultBrowser";
import { Workbench } from "./components/Workbench";
import { CodePreview } from "./components/Workbench/CodePreview";
import {
  type CodeOpenRequest,
  type CodeQuote,
  CodeSidebar,
  codeFolderKey,
} from "./components/Workbench/CodeSidebar";
import { terminalRefusal } from "./components/Workbench/TerminalLauncher";
import type {
  WorkbenchChecks,
  WorkbenchPreviews,
  WorkbenchReview,
  WorkbenchRoots,
  WorkbenchTerminal,
} from "./components/Workbench/Workbench";
import { ArtifactContext } from "./context/artifactContext";
import {
  quoteTurn,
  type ReadOnlyConversationArchive,
  selectedArchive,
} from "./context/conversationTools";
import { t, tf } from "./i18n/t";
import { isMac, platformKind } from "./platform";
import type { SessionHandoff } from "./sessionHandoff";
import { type AnswerOutcome, createApprovalStore } from "./state/approvals";
import { createBotThreadStore, displayThreadTurn } from "./state/botThreads";
import { listingNote } from "./state/catalog";
import { getBuffer, useCodeBuffers } from "./state/codeBuffers";
import { applyHistoryAnchorPage } from "./state/conversationSearch";
import { useDecisionLauncher } from "./state/decisionLoop";
import { collectDecisions, type NativeDecision } from "./state/decisions";
import {
  type DecisionsApi,
  isChangeProposalItem,
  isQuestionProposalItem,
} from "./state/decisionsApi";
import {
  createEditorStore,
  type DispatchRefusalCode,
  draftFits,
  type EditorEntry,
} from "./state/editors";
import type { ExcerptApi, VaultExcerptPart } from "./state/excerptApi";
import { observeGit } from "./state/git";
import { liveSessionIds, maxLiveFrom } from "./state/live";
import {
  commitBack,
  type NavigationContext,
  type NavigationHistory,
  peekBack,
  recordTransition,
} from "./state/navigation";
import { needsYou } from "./state/needsYou";
import {
  clearActive,
  closeTab,
  type Edge,
  FILE_PANE_MIN,
  focusedGroup,
  focusGroup,
  groupOf,
  kindOf,
  MAX_PANES,
  MAX_TABS,
  moveTab,
  openFile,
  openTab,
  PANE_MIN,
  type PaneGroup,
  type PaneLayout,
  groups as paneGroups,
  pinTab,
  resize as resizePane,
  retainTabs,
  splitFile,
  type Zone,
} from "./state/panes";
import { createRestStore } from "./state/rest";
import { browserNotifier, useRoutineNotifications } from "./state/routineNotifications";
import { attentionCount, createRoutinesStore, type RoutinesSnapshot } from "./state/routines";
import {
  groupSessions,
  hereNowLabel,
  isNewerCli,
  menuCommands,
  rosterRunning,
  rowTitle,
  runningAgents,
  timeLabel,
  titleIsPrompt,
} from "./state/rows";
import { groupRuns, runElapsed, runKey } from "./state/runGroups";
import { useCardArrivals, useRunNotifications } from "./state/runNotifications";
import {
  isArchiveOnly,
  missingRecords,
  organizedSessions,
  sessionLabel,
} from "./state/sessionOrganization";
import { codeFolderOf, folderLabel, noteRoot, refreshRoots, useRoots } from "./state/sessionRoots";
import { createShellStore, vaultSurfaces } from "./state/shell";
import type { SkillProposalsApi } from "./state/skillProposalsApi";
import { chatDigest } from "./state/sources";
import { unsavedWork } from "./state/unload";
import { hasUnsavedWork, useHasUnsavedWork } from "./state/unsavedWork";
import { LAYOUT_SETTING, usePanes } from "./state/usePanes";
import type { VaultApi } from "./state/vaultApi";
import type { VaultKnowledgeApi } from "./state/vaultKnowledgeApi";
import type { VaultOpsApi } from "./state/vaultOpsApi";
import {
  type Card,
  presence,
  promptCut,
  type RoutingBlock,
  rejoinPrompt,
  type SessionView,
  type Turn,
} from "./state/view";
import { useSnapshot, type Viki } from "./state/viki";
import type { Attachment } from "./turnInput";
import { DictationContext } from "./voice/dictationContext";
import { HandsFreeNotice, HandsFreeStrip, VoiceControl } from "./voice/HandsFreeControls";
import { HANDS_FREE_OFF } from "./voice/handsFree";
import { useHandsFree } from "./voice/useHandsFree";
import { useReadAloudSettings } from "./voice/useReadAloudSettings";
import { withoutVoiceTag } from "./voice/voiceRecall";
import "./styles/app.css";

/** Caps how many missing-record ids this app ever inspects for a profile draft in one page
 * load: reading every missing record's launch would scale with however many labels this app
 * happens to still hold. */
const MAX_MISSING_INSPECT = 8;

/** Caps how many archive-only missing records (`isArchiveOnly`: nothing but the Archive mark)
 * this app inspects, and deletes on a 404 `not_found`, in one page load. A budget of their own,
 * so they never take a labelled record's inspect; larger, because such a label is cleared
 * rather than listed, and Claude Code removing old transcripts can leave many at once; still
 * bounded, because each one costs an inspect and a DELETE, sent one at a time. One past it stays
 * listed until a later page load. */
const MAX_ARCHIVE_ONLY_CLEARS = 64;

/** Match the encrypted draft service's UTF-8 body limit before starting a target. */
const TOO_LONG_NOTE =
  "Too large to send (up to 1 MiB per message). The text stays in the message box";

/** A new chat's held model/effort pick could not be written before its first start: nothing was
 * sent and the text stays in the box, while the chip drops the pick and reads again. */
const PREFS_NOT_SAVED =
  "Couldn't save this chat's model and effort. Check the chip, then send again.";

/** viki.ts's own storage key, not exported there: mirrored here (not imported) because
 * clearing it is App.tsx's job — a per-launch token the server just refused must not survive
 * to be replayed by the next reload — while writing it is viki.ts's (readToken). */
const TOKEN_STORAGE_KEY = "viki.token";
/** Which session this window was looking at, so a reload comes back to it. */
const SELECTED_STORAGE_KEY = "viki.selected";
/** Which view, settings page and data panel this window was on, so a reload lands there. */
const VIEW_STORAGE_KEY = "viki.view";
const BOT_STORAGE_KEY = "viki.bot";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const CHAT_NAVIGATION: Navigation = {
  view: "chat",
  page: "general",
  panel: "locations",
  onboardingMode: "choose",
};

const SETTINGS_PAGES = [
  "general",
  "appearance",
  "chat",
  "providers",
  "workspaces",
  "persona",
  "privacy",
  "diagnostics",
];
const DATA_PANELS = ["locations", "export", "import", "reset"];

/** What App reads in place of the routines store when it has no routines client. */
const NO_ROUTINES: RoutinesSnapshot = { status: "idle", items: [], scheduler: null, error: null };
const noRoutinesStore = { subscribe: () => () => {}, getSnapshot: () => NO_ROUTINES };

/** The remembered navigation, field by field. A stored `recovery` or `onboarding` reads as the
 * chat: recovery is decided by this launch's capabilities and never by a previous tab, and the
 * onboarding view is entered once per launch by its own rule. Any other view
 * comes back only when it is in `restorable`, the views this window can render. */
function storedNavigation(restorable: readonly View[]): Navigation {
  let raw: unknown;
  try {
    const text = window.sessionStorage.getItem(VIEW_STORAGE_KEY);
    raw = text === null ? null : JSON.parse(text);
  } catch {
    return CHAT_NAVIGATION; // storage refused, or a value another build wrote: start at the chat
  }
  if (raw === null || typeof raw !== "object") return CHAT_NAVIGATION;
  const stored = raw as Record<string, unknown>;
  return {
    view: restorable.includes(stored.view as View) ? (stored.view as View) : "chat",
    page:
      typeof stored.page === "string" && SETTINGS_PAGES.includes(stored.page)
        ? (stored.page as Navigation["page"])
        : CHAT_NAVIGATION.page,
    panel:
      typeof stored.panel === "string" && DATA_PANELS.includes(stored.panel)
        ? (stored.panel as Navigation["panel"])
        : CHAT_NAVIGATION.panel,
    onboardingMode: CHAT_NAVIGATION.onboardingMode,
  };
}

/** A callback ref for a heading App renders itself: being module-level it keeps one identity,
 * so React calls it when the element mounts (entering the view) and not on every render. */
function focusOnMount(element: HTMLElement | null): void {
  element?.focus();
}

/** True for a keydown target that takes typed text: an input, a textarea or a contenteditable
 * element. macOS Option+N is skipped there so its dead key (ñ, ã, õ) still reaches the
 * field instead of opening a session. */
function isTextEntryTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable;
}

function rememberNavigation(nav: Navigation): void {
  try {
    window.sessionStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify(nav));
  } catch {
    // the app is not worth failing over a remembered view
  }
}

function storedSelection(): string | null {
  try {
    return window.sessionStorage.getItem(SELECTED_STORAGE_KEY);
  } catch {
    return null; // storage refused (private mode, quota): nothing to restore, not an error
  }
}

function rememberSelection(sessionId: string | null): void {
  try {
    if (sessionId === null) window.sessionStorage.removeItem(SELECTED_STORAGE_KEY);
    else window.sessionStorage.setItem(SELECTED_STORAGE_KEY, sessionId);
  } catch {
    // the app is not worth failing over a remembered selection
  }
}

function storedBot(): string | null {
  try {
    const value = window.sessionStorage.getItem(BOT_STORAGE_KEY);
    return value !== null && UUID.test(value) ? value : null;
  } catch {
    return null;
  }
}

/** `identity` as a profile session shows it: the profile's name for the assistant, and its chip.
 * `botProfileId`, when given, is a Bot's chat: `profile.botProfileId` then carries the Bot's
 * assigned or drawn hue (botHues.ts) to Face/BotAvatar. */
function boundIdentity(
  identity: Identity,
  binding: ProfileSessionIdentity,
  botProfileId?: string,
): Identity {
  return {
    ...identity,
    assistantName: binding.name,
    profile: {
      name: binding.name,
      avatarId: binding.avatar_asset_id,
      accent: binding.accent,
      shape: binding.shape,
      revision: binding.profile_revision,
      ...(botProfileId !== undefined ? { botProfileId } : {}),
    },
  };
}

export interface AppProps {
  sessionHandoff?: SessionHandoff;
  sessionWindowsApi?: SessionWindowsApi;
  profileLaunchApi?: ProfileLaunchApi;
  /** A chat's model and effort before its process runs: the chip's read, and a new chat's pick
   * written before its first start. Without it the chip shows only once a session has started. */
  sessionPrefsApi?: SessionPrefsApi;
  profileApi?: ProfileApi;
  nativeAssetsApi?: NativeAssetsApi;
  inventoryApi?: InventoryApi;
  /** The MCP servers toggle/reconnect client. Shown in the composer, before the model chip,
   * only together with `inventoryApi` — without either, the button renders nothing. */
  mcpServersApi?: McpServersApi;
  /** A subagent run's own transcript: the run list's Peek and its rows' time and tokens. Without
   * it the rows render with no Peek and no reads. */
  subagentsApi?: SubagentsApi;
  /** Per-chat open tool-permission requests, and stopping a run (src/viki_agent/conversations/runs_api.py). Without it the run
   * list's rows and Peek show no Stop, and a card is attributed to a run only by matching agent
   * type rather than by the request's own run id. */
  runsApi?: RunsApi;
  vaultApi?: VaultApi;
  /** Bot memory the app keeps in its own data folder for a Bot whose vault is not ordinary. */
  appMemoryApi?: Pick<VaultApi, "entries" | "note" | "metadata">;
  /** The note operations and recovery, offered only where Save is. */
  vaultOpsApi?: VaultOpsApi;
  /** Memory, keep-in-vault and backup; Data passes it on to every platform. */
  vaultKnowledgeApi?: VaultKnowledgeApi;
  /** 00-inbox captures: the triage dialog the inbox tile opens, and VIKI's suggested
   * destinations. Without it the tile opens the Vault browser at 00-inbox instead. */
  capturesApi?: CapturesApi;
  assets?: AssetsApi;
  avatars?: AvatarsApi;
  organization?: SessionMetadataApi;
  archives?: ChatArchivesApi;
  conversationSearch?: ConversationSearchApi;
  /** The proposal half of the decision inbox: its section and inspector. Without it the inbox
   * holds native cards only, as it did before. */
  decisions?: DecisionsApi;
  /** Make this a skill/Propose this skill on a finished reply, and the skill proposal inspector
   * in the inbox. Renders only together with `decisions`. */
  skillProposals?: SkillProposalsApi;
  /** Vault excerpts: the chips above the composer, the citations in a sent turn, and the Vault
   * view's Attach excerpt. Without it none of the three renders. */
  excerpts?: ExcerptApi;
  /** Routines: the live tab, its view and the rail section. Without it the rail keeps the
   * `/api/rails` rows the frozen suites render. */
  routinesApi?: RoutinesApi;
  /** The Code view over code folders: the tab, the view and quoting a line into the draft.
   * Without it the Code tab is inert. */
  codeApi?: CodeApi;
  /** Sessions in code folders and their worktrees: the Workbench's start button and the folder
   * labels on the rail, the tabs and the header. Without it neither shows. */
  rootsApi?: RootsApi;
  /** Review of a worktree's changes in the Workbench. Without it the Review tab is absent. */
  reviewApi?: ReviewApi;
  checksApi?: ChecksApi;
  terminalApi?: TerminalApi;
  previewsApi?: PreviewsApi;
  /** Opens Claude Code's sign-in console from a not-signed-in card; without it, or
   * off Windows, the card's own words are all. */
  providerLoginApi?: ProviderLoginApi;
  /** Pending settings/routine changes a chat's agent CLI asked the window to make: the Apply
   * card right after the readiness banner. Without it nothing renders there. */
  agentChangesApi?: AgentChangesApi;
  /** The context report client: the status bar's context meter and the Context & loadout page.
   * Without it neither renders. */
  contextApi?: ContextApi;
  /** The plan-limit reading client: the status bar's plan meter. Without it the meter is
   * absent. */
  limitsApi?: LimitsApi;
  /** The Decision page and its left-rail destination. Without it neither shows. */
  decisionApi?: DecisionLoopApi;
  /** The optional Mattermost bot's launch status and the user's stop request. */
  messagingApi?: MessagingApi;
  /** Named Bots and their existing profile conversations and routines. */
  botsApi?: BotsApi;
  /** The Bot usage dashboard (AC-13/AC-14): token/cost summaries and per-session traces. Without
   * it the Bots page's "Usage" section and nav button are absent. */
  botUsageApi?: BotUsageApi;
  /** The read-only client for what recent chats received from other Claude Code sessions. Without
   * it the right rail's Between chats fold is absent. */
  betweenChatsApi?: BetweenChatsApi;
  viki: Viki;
  rest: Rest;
  durable: DurableApi;
  /** The shell's own client. Optional: without it the app is exactly the base app — no
   * capabilities, no settings, every command the shell always offered still enabled — which is
   * what the frozen `<App viki rest durable />` suites render. */
  shell?: ShellApi;
}

/** Running work's `since`: the first NON-HISTORY user turn's own timestamp, never a clock App
 * reads itself — session.started carries no time, so a session with no turn yet has nothing to
 * show. History turns are skipped: their `at` is the transcript's own old timestamp, copied in
 * by applyHistoryPage (state/sessionReducer.ts), not when this process's session went live —
 * that is exactly the server's own `since` semantics (SessionActor.since, session.py), just not
 * one the protocol carries yet. */
function firstUserAt(view: SessionView): string {
  for (const turn of view.turns) {
    if (turn.history) continue;
    if (turn.user) return turn.user.at;
  }
  return "";
}

/** Running work's `inspect` without a `profileLaunchApi`: answers exactly what a real 404 would,
 * so every row stays an ordinary (non-profile) one instead of RunningWork treating a missing
 * client as some other failure. */
function inspectNever(): Promise<PreparedProfilePreview> {
  return Promise.reject(new ProfileLaunchApiError(404, "not_found"));
}

/** The history note's copy for each error code; any other code (too_large, unavailable) gets
 * the generic sentence. */
function historyErrorCopy(code: NonNullable<SessionView["historyError"]>): string {
  switch (code) {
    case "busy":
      return "Loading past conversations is busy right now. Try again in a moment.";
    case "transcript_missing":
      return "Can't find the conversation record. Claude Code may have deleted it or moved it.";
    case "tail_budget":
      return "Older conversations can't be shown because the record is too large.";
    default:
      return "Couldn't load past conversations.";
  }
}

/** The draft is safe either way, so say that first, then why the send did not happen, then
 * the one thing to do about it. Never names a control by its label. */
function dispatchRefusalCopy(code: DispatchRefusalCode): string {
  switch (code) {
    case "turn_running":
      return "Draft saved. Can't send while a reply is in progress. Stop it or wait for it to finish, then send.";
    case "closed":
      return "Draft saved. Can't send because this conversation is closed.";
    case "stale_generation":
      return "Draft saved. Can't send because the conversation was updated. Send again.";
    case "unsupported_attachment":
      return "Draft saved. Can't send because the attached image doesn't meet the size or format requirements. Remove the attachment, or replace it with an image that meets them.";
    case "attachment_unavailable":
      return "Draft saved. Can't send because the attached image is no longer on this PC. Remove the attachment, or replace the image.";
  }
}

/** MediaComposer's `blockedReason`, checked in this order: an unconfirmed prior send outranks a
 * conflict, which outranks an in-flight one. `submitDisabled` disables the button for the same
 * three conditions as one unordered check, so it is not this precedence: while a send's POST is
 * out, `pendingCommand` and `submitting` are both set, and this order is what decides that the
 * confirm-first sentence shows rather than Sending…, which shows only during the draft
 * save that precedes the POST. */
function composerBlockedReason(editor: EditorEntry | undefined): string | undefined {
  if (editor?.pendingCommand) {
    return "The previous send is being confirmed. Use Check send result to see its state.";
  }
  if (editor?.conflictRevision != null) {
    return "The draft changed in another window. Review the saved draft.";
  }
  if (editor?.submitting) return "Sending…";
  return undefined;
}

/** ArrowUp recalls this window's last prompt, or nothing when this window holds only a cut copy
 * of it. Older transcript prompts stay in history; recovering an accepted submission is an
 * explicit action in DraftRecovery. */
function lastUserText(view: SessionView): string {
  for (let i = view.turns.length - 1; i >= 0; i--) {
    const turn = view.turns[i];
    if (turn?.history) continue;
    if (turn?.user) return promptCut(turn) ? "" : withoutVoiceTag(rejoinPrompt(turn.user));
  }
  return "";
}

type NavigationOpenerIdentity = (
  | { attribute: "id" | "data-page" | "data-region"; value: string }
  | { tag: string; role: string | null; name: string }
) & { scope: "view" | "right-rail-summary" | "code-sidebar" };

const navigationControls =
  "button,input,textarea,select,a[href],summary,[tabindex],[contenteditable=true]";

function navigationControlName(element: HTMLElement): string | null {
  const label = element.getAttribute("aria-label");
  if (label !== null) return label;
  return element.matches("button,a[href],summary,[role=button],[role=tab],[role=menuitem]")
    ? element.textContent
    : null;
}

function navigationOpenerIdentity(element: HTMLElement): NavigationOpenerIdentity | null {
  if (!element.matches(navigationControls)) return null;
  const scope = element.closest(".right-rail-summary")
    ? "right-rail-summary"
    : element.closest(".code-sidebar")
      ? "code-sidebar"
      : "view";
  for (const attribute of ["id", "data-page", "data-region"] as const) {
    const value = element.getAttribute(attribute);
    if (value) return { attribute, value, scope };
  }
  const name = navigationControlName(element);
  return name ? { tag: element.tagName, role: element.getAttribute("role"), name, scope } : null;
}

function eligibleNavigationOpener(element: HTMLElement): boolean {
  if (
    !element.isConnected ||
    !element.matches(navigationControls) ||
    element.closest("[disabled],[aria-disabled=true],[hidden],[inert],[aria-hidden=true]")
  )
    return false;
  for (let current: HTMLElement | null = element; current; current = current.parentElement) {
    const style = window.getComputedStyle(current);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.visibility === "collapse"
    )
      return false;
  }
  return true;
}

/** File tab `id`'s file, or undefined for a chat tab. Only an own key of `files` counts: a drag
 * payload can name any string, `constructor` included. */
function fileOf(
  files: PaneLayout["files"],
  id: string,
): { workspaceId: string; ref: string } | undefined {
  return Object.hasOwn(files, id) ? files[id] : undefined;
}

/** `l` with chat `id` open and active on the chat side, or (null) that side's active tab cleared:
 * in the pane already holding the chat, else in the focused chat pane (`focusedChat`). With only
 * file panes, a chat gets a new pane beside the focused one (a mismatched centre drop splits
 * right), or no tab when none fits. File panes keep their tabs and active files as they are. */
function openChatIn(l: PaneLayout, id: string | null): PaneLayout {
  if (id !== null && groupOf(l, id)) return openTab(l, id);
  const chat = l.focusedChat;
  if (chat === null) {
    if (id === null) return l;
    const placed = moveTab(l, id, { group: l.focused, zone: "center" });
    return placed === l ? l : openTab(placed, id);
  }
  const onChat = chat === l.focused ? l : focusGroup(l, chat);
  return id === null ? clearActive(onChat) : openTab(onChat, id);
}

/** `l` with a new tab for `file` moved into pane `groupId` at `zone` (a centre drop on a chat
 * pane splits right, as `moveTab` does); `l` itself when the tabs are at their cap or the move
 * does not fit. */
function withFileTab(
  l: PaneLayout,
  file: { workspaceId: string; ref: string },
  groupId: string,
  zone: Zone,
): PaneLayout {
  if (paneGroups(l).flatMap((group) => group.tabs).length >= MAX_TABS) return l;
  const id = Array.from({ length: MAX_TABS }, (_, n) => `f${n + 1}`).find(
    (tab) => !fileOf(l.files, tab),
  );
  if (id === undefined) return l;
  const added = { ...l, files: { ...l.files, [id]: file } };
  const next = moveTab(added, id, { group: groupId, zone });
  return next === added ? l : next;
}

/** A file's name: its ref's last segment. */
function fileName(ref: string): string {
  return ref.split(/[\\/]/).pop() ?? ref;
}

/** `SessionTabs` whose file tab rows carry their file's name, path and unsaved-changes dot
 * (`getBuffer`). A file tab names no folder, unless another file tab in this strip has the same
 * name: then its ref's parent directory tells them apart. Chat tabs keep `folderOf`. It
 * re-renders on a code buffer change by itself, so typing in a code editor does not re-render the
 * whole App. */
function TabStrip({
  files,
  sessions,
  folderOf,
  ...props
}: SessionTabsProps & { files: PaneLayout["files"] }) {
  useCodeBuffers();
  const names = sessions.flatMap((row) => {
    const file = fileOf(files, row.session_id);
    return file ? [fileName(file.ref)] : [];
  });
  return (
    <SessionTabs
      {...props}
      folderOf={(id) => {
        const file = fileOf(files, id);
        if (!file) return folderOf?.(id) ?? null;
        const name = fileName(file.ref);
        if (names.filter((other) => other === name).length < 2) return null;
        return file.ref.slice(0, Math.max(0, file.ref.length - name.length - 1));
      }}
      sessions={sessions.map((row) => {
        const file = fileOf(files, row.session_id);
        if (!file) return row;
        const name = fileName(file.ref);
        const dirty = getBuffer(file.workspaceId, file.ref) !== undefined;
        return { ...row, title: name, file: { name, ref: file.ref, dirty } };
      })}
    />
  );
}

/** How long after the last keystroke the launcher still holds a nudge's pulse. */
const TYPING_HOLD_MS = 3_000;
const HOUR_MS = 60 * 60 * 1000;
/** The panel's line when VIKI's chat did not confirm a send; the composer keeps the text. */
const LAUNCHER_SEND_UNCONFIRMED = "VIKI didn't confirm that message. The text stays in the box.";

/** The settings one of the launcher menu's choices writes: a 24-character UTC stamp an hour out or
 * at 08:00 local tomorrow (the Decision page's own Pause until tomorrow), or "" to end a pause or
 * a Focus spell. */
function launcherMenuChanges(
  choice: Exclude<LauncherMenuChoice, "open_decision">,
  now: Date,
): Record<string, string> {
  const inAnHour = new Date(now.getTime() + HOUR_MS).toISOString();
  switch (choice) {
    case "pause_hour":
      return { "decision.paused_until": inAnHour };
    case "pause_tomorrow": {
      const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 8);
      return { "decision.paused_until": tomorrow.toISOString() };
    }
    case "resume":
      return { "decision.paused_until": "" };
    case "focus_hour":
      return { "decision.focus_until": inAnHour };
    case "end_focus":
      return { "decision.focus_until": "" };
  }
}

/** Reduced motion as ActivityOrb/useActivityPresentation.ts reads it: Settings › Appearance's own
 * override or the system's preference. */
function launcherReducedMotion(): boolean {
  return (
    document.documentElement.dataset.motion === "reduce" ||
    (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches)
  );
}

/** The panel's line for a refused start (missions.py's MISSION_ERROR_CODES, api.py's
 * _MISSION_CODES): `busy` (429, two of VIKI's chats already running, or this item already has
 * one) and `limit` (409, today's own start limit, a separate cap) each get their own line, since
 * only `limit` is the one the user can simply wait out till tomorrow; anything else keeps the
 * generic line. */
function startRefusal(error: unknown): string {
  if (!(error instanceof DecisionLoopApiError)) return "VIKI couldn't start that chat.";
  if (error.code === "busy")
    return "VIKI can't start that chat yet: two are already running, or this item has one. Try again soon.";
  if (error.code === "limit") return "VIKI can't start another chat today: today's limit is used.";
  return "VIKI couldn't start that chat.";
}

/** The launcher's own starts that are not chats: a Bot's Start, Run checks and Suggest
 * destinations. */
type WorkStart = "start_seat" | "run_checks" | "suggest_inbox";

/** Each work start's line for a refusal. A Bot's start refused `limit` (today's team limit, a
 * separate cap the user can only wait out) says so, as `startRefusal` does for a chat. */
const WORK_REFUSALS: Record<WorkStart, string> = {
  start_seat: "VIKI couldn't start that Bot.",
  run_checks: "VIKI couldn't run those checks.",
  suggest_inbox: "VIKI couldn't ask for those suggestions.",
};

/** What a `busy` refusal means for each start: something of that kind is already running. */
const WORK_BUSY: Record<WorkStart, string> = {
  start_seat:
    "VIKI can't start that Bot yet: it is already running, or too many Bots are. Try again soon.",
  run_checks:
    "VIKI can't run those checks yet: they are already running, or too many checks are. Try again soon.",
  suggest_inbox:
    "VIKI can't ask for those suggestions yet: a suggestion run is already going. Try again soon.",
};

function workRefusal(start: WorkStart, error: unknown): string {
  const code = error instanceof DecisionLoopApiError ? error.code : null;
  if (start === "start_seat" && code === "limit")
    return "VIKI can't start another Bot today: today's limit is used.";
  if (code === "busy") return WORK_BUSY[start];
  return WORK_REFUSALS[start];
}

/** The launcher button: what the panel takes the focus from and gives it back to. */
function launcherButton(): HTMLElement | null {
  return document.querySelector<HTMLElement>("button.dloop-launcher");
}

/** The open launcher panel, as `navigate`'s modal origin: a Vault view admits leaving from under
 * it (VaultBrowser's `canLeave`). */
function launcherPanelOrigin(): HTMLElement | undefined {
  return document.querySelector<HTMLElement>('.dloop-panel[aria-modal="true"]') ?? undefined;
}

interface DecisionLauncherProps {
  api: DecisionLoopApi;
  /** This App mount's window id, sent with every presence report. */
  windowId: string;
  /** The chat on screen and its title, the vault note on screen, and the page's own facts. */
  chat: string | null;
  chatTitle: string | null;
  note: PresenceNote | null;
  page: PageFacts | null;
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  /** Starts or ends VIKI's voice conversation (useLauncherShortcuts' own "voice"); absent, Alt+T
   * does nothing. */
  onVoice?: () => void;
  /** The open panel's Escape, asked first (LauncherPanel's own `onEscape`): true keeps the panel
   * open, as when it ends VIKI's voice conversation. */
  onPanelEscape: () => boolean;
  /** `layout.launcher` as the settings document holds it. */
  savedPosition: unknown;
  /** `layout.launcher_panel` as the settings document holds it. */
  savedPanelSize: unknown;
  /** Whether a settings write can run now: the menu and the position write only then. */
  writable: boolean;
  patch: (changes: Record<string, unknown>) => Promise<unknown>;
  /** Follows an item's open_* action to its place in the app; the panel is already closing. */
  onNavigate: (action: ItemAction) => void;
  /** Opens Decision at the given row; "proactive" by default (openDecisionFromLauncher). */
  onOpenDecision: (row?: string) => void;
  /** The launcher view's `started`, space-joined ("" for none), for the session list. */
  onStarted: (sessionIds: string) => void;
  onNotice: (text: string | null) => void;
  /** VIKI's chat, drawn at the open panel's foot. */
  renderChat: () => ReactNode;
}

/** VIKI's launcher and its panel. A component of its own so
 * that what changes often (the launcher's reads, a drag, the presence hook's keystrokes)
 * re-renders this rather than the whole App. It reads the launcher view while the page is
 * visible, reports this window's presence, keeps the launcher's position, re-checks as the panel
 * opens, answers the items and starts or stops VIKI's chats itself, and hands every way out (an
 * item's open_* action, Open Decision, the weekly check) back to App. */
function DecisionLauncher({
  api,
  windowId,
  chat,
  chatTitle,
  note,
  page,
  open,
  onOpen,
  onClose,
  onVoice,
  onPanelEscape,
  savedPosition,
  savedPanelSize,
  writable,
  patch,
  onNavigate,
  onOpenDecision,
  onStarted,
  onNotice,
  renderChat,
}: DecisionLauncherProps): ReactElement | null {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  const [focused, setFocused] = useState(() => document.hasFocus());
  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState === "visible");
    const onFocus = () => setFocused(true);
    const onBlur = () => setFocused(false);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onFocus);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("blur", onBlur);
    };
  }, []);
  const launcher = useDecisionLauncher(api, { visible });
  const { view, checking, observe, refresh } = launcher;
  const { lastInputMs } = usePresence(api, {
    window: windowId,
    focused,
    chat,
    chatTitle,
    note,
    launcher: open,
    page,
  });
  const position = useLauncherPosition({
    saved: typeof savedPosition === "string" ? savedPosition : null,
    writable,
    onSave: (value) => void patch({ "layout.launcher": value }).catch(() => {}),
    size: LAUNCHER_SIZE,
  });

  const startedKey = view?.started.join(" ") ?? "";
  useEffect(() => {
    onStarted(startedKey);
  }, [onStarted, startedKey]);
  useEffect(() => () => onStarted(""), [onStarted]);

  // Every item the open panel lists once its re-check has settled; the hook drops a key it has
  // already posted for the view on screen.
  const shownKeys =
    open && !checking && view ? view.items.map((item) => item.item_key).join(" ") : "";
  useEffect(() => {
    if (shownKeys !== "") observe("shown", shownKeys.split(" "));
  }, [observe, shownKeys]);

  // Whether the panel now open was opened by Alt+V/Alt+T rather than a click
  // (useLauncherShortcuts): read once by the panel at mount to send its very first focus into
  // Message VIKI instead of its usual first control (the panel's own `initialFocus`, below).
  const [keyOpened, setKeyOpened] = useState(false);

  function toggle(): void {
    setKeyOpened(false);
    if (open) {
      onClose();
      return;
    }
    // The panel gives the focus back to whatever held it as it opened: the launcher, even when
    // the press moved no focus there.
    launcherButton()?.focus({ preventScroll: true });
    launcher.open();
    onOpen();
  }

  /** Alt+V/Alt+T opening the panel: unlike the click's own toggle() above, the focus stays
   * wherever it already was — useModalFocus then restores it there on close — since the panel's
   * own initialFocus="message" (below) sends the very first focus into Message VIKI instead. */
  function openFromKey(): void {
    setKeyOpened(true);
    launcher.open();
    onOpen();
  }

  /** One work start's request. Its promise is handed back through the panel to ItemCard, which
   * keeps the button disabled until it settles: a second click while the first is in flight
   * would only be refused `busy`, and read as a failure while the first one runs. */
  function startWork(
    start: WorkStart,
    item: ItemView,
    request: () => Promise<unknown>,
  ): Promise<void> {
    return request().then(
      () => {
        observe("acted", [item.item_key]);
        refresh();
      },
      (error: unknown) => onNotice(workRefusal(start, error)),
    );
  }

  function handleAction(action: ItemAction, item: ItemView): Promise<void> | undefined {
    onNotice(null);
    if (action.type === "start_prepare" || action.type === "start_run") {
      void api
        .startMission(item.item_key, action.type === "start_prepare" ? "prepare" : "run")
        .then(
          () => {
            observe("acted", [item.item_key]);
            refresh();
          },
          (error: unknown) => onNotice(startRefusal(error)),
        );
      return;
    }
    // A draft never starts anything by itself: it only fills a composer, so it counts as acted
    // on, the same as a started chat, not merely opened.
    if (action.type === "draft_chat" || action.type === "draft_new") {
      observe("acted", [item.item_key]);
      onClose();
      onNavigate(action);
      return;
    }
    if (action.type === "start_seat") {
      const routineId = action.routine_id;
      if (!routineId) return;
      return startWork("start_seat", item, () => api.startSeat(routineId));
    }
    if (action.type === "run_checks") {
      const workspaceId = action.workspace_id;
      if (!workspaceId) return;
      return startWork("run_checks", item, () => api.runChecks(workspaceId));
    }
    // Opening the triage from this item uses what VIKI prepared, so it counts as acted on.
    if (action.type === "open_triage") {
      observe("acted", [item.item_key]);
      onClose();
      onNavigate(action);
      return;
    }
    if (action.type === "suggest_inbox") {
      return startWork("suggest_inbox", item, () => api.suggestInbox(item.item_key));
    }
    observe("opened", [item.item_key]);
    if (action.type === "stop_seat") {
      if (action.routine_id)
        void api
          .stopSeat(action.routine_id)
          .then(refresh, () => onNotice("VIKI couldn't stop that Bot."));
      return;
    }
    if (action.type === "stop_checks") {
      if (action.workspace_id)
        void api
          .stopChecks(action.workspace_id)
          .then(refresh, () => onNotice("VIKI couldn't stop those checks."));
      return;
    }
    if (action.type === "stop_mission") {
      if (action.mission_id)
        void api
          .stopMission(action.mission_id)
          .then(refresh, () => onNotice("VIKI couldn't stop that chat."));
      return;
    }
    onClose();
    onNavigate(action);
  }

  function choose(choice: LauncherMenuChoice): void {
    if (choice === "open_decision") {
      onClose();
      onOpenDecision();
      return;
    }
    if (!writable) {
      onNotice("Couldn't save that change.");
      return;
    }
    onNotice(null);
    void patch(launcherMenuChanges(choice, new Date())).then(refresh, () =>
      onNotice("Couldn't save that change."),
    );
  }

  /** The weekly check card: Check opens Decision at How VIKI is doing; Skip
   * this week posts review_seen and refreshes the launcher view. */
  function handleReview(choice: ReviewChoice): void {
    if (choice === "check") {
      onClose();
      onOpenDecision("how");
      return;
    }
    onNotice(null);
    void api
      .preference({ name: "review_seen" })
      .then(refresh, () => onNotice("Couldn't save that change."));
  }

  // Alt+V opens or closes the panel from anywhere in the window; Alt+T, while a voice
  // handler is given, opens the panel first when it is closed, then starts or ends VIKI's voice
  // conversation. Called unconditionally, before the early return below, since it is a
  // hook: view's own nullness must never skip it.
  useLauncherShortcuts(view !== null, {
    panel: () => (open ? onClose() : openFromKey()),
    ...(onVoice
      ? {
          voice: () => {
            if (!open) openFromKey();
            onVoice();
          },
        }
      : {}),
  });

  if (view === null) return null;
  return (
    <>
      <Launcher
        view={view}
        open={open}
        onToggle={toggle}
        position={position}
        typingUntilMs={lastInputMs === null ? 0 : lastInputMs + TYPING_HOLD_MS}
        reducedMotion={launcherReducedMotion()}
        shortcut={launcherShortcutHint("panel")}
      />
      {open ? (
        <LauncherPanel
          view={view}
          checking={checking}
          anchor={{ right: position.right, bottom: position.bottom, size: LAUNCHER_SIZE }}
          returnFocus={launcherButton}
          onClose={onClose}
          onEscape={onPanelEscape}
          onAction={handleAction}
          onFeedback={(item, value, reason) =>
            launcher.feedback({ item_key: item.item_key }, value, reason)
          }
          onMenu={choose}
          onReview={handleReview}
          chat={renderChat()}
          nowMs={Date.now()}
          savedSize={typeof savedPanelSize === "string" ? savedPanelSize : null}
          onSaveSize={(value) => {
            if (writable) void patch({ "layout.launcher_panel": value }).catch(() => {});
          }}
          initialFocus={keyOpened ? "message" : "first"}
        />
      ) : null}
    </>
  );
}

/** Unknown completions can be reviewed in this view, but cannot match reconstructed results. */
function runReviewIdentities(sessions: Readonly<Record<string, SessionView>>) {
  const identities = new Map<string, string | RoutingBlock>();
  for (const [sessionId, view] of Object.entries(sessions)) {
    for (const turn of view.turns) {
      for (const block of turn.blocks) {
        if (block.kind !== "routing" || block.status === "running") continue;
        identities.set(
          runKey(sessionId, block.id),
          view.generation !== null && block.completionSeq !== undefined
            ? `${view.generation}:${block.completionSeq}`
            : block,
        );
      }
    }
  }
  return identities;
}

/** Keep each pane's model controls and pending picks with its own mounted editor. */
function SessionMediaComposer({
  sessionId,
  sessionView,
  binding,
  newChat,
  prefsApi,
  modelCatalog,
  refreshKey,
  actions,
  prestarts,
  destinationLabel,
  ...props
}: Omit<MediaComposerProps, "destinationLabel"> & {
  destinationLabel: (connections: readonly ConnectionLabel[]) => string;
  editorKey: string;
  sessionId: string | null;
  sessionView: SessionView | undefined;
  binding: ProfileSessionIdentity | null | "unbound" | undefined;
  newChat: boolean;
  prefsApi: SessionPrefsApi | undefined;
  modelCatalog: Parameters<typeof usePrestartChip>[0]["models"];
  refreshKey: unknown;
  actions: Viki["actions"];
  prestarts: RefObject<Map<string, PrestartChip>>;
}): ReactElement {
  const info = sessionView?.info ?? null;
  const connections = useConnectionLabels(Boolean(info?.model.startsWith("viki-llm/")), refreshKey);
  const prestart = usePrestartChip({
    api: prefsApi,
    models: modelCatalog,
    chatKey: props.editorKey,
    enabled:
      prefsApi !== undefined &&
      info === null &&
      binding === "unbound" &&
      (sessionId !== null || newChat),
    refreshKey,
  });
  useLayoutEffect(() => {
    prestarts.current.set(props.editorKey, prestart);
    return () => {
      prestarts.current.delete(props.editorKey);
    };
  }, [prestarts, props.editorKey, prestart]);
  const choice = useModelChoice({
    sessionId: sessionId ?? "",
    allowCurrentModel: info === null,
    model: info?.model ?? prestart.chip?.model ?? "",
    effort: info?.effort ?? prestart.chip?.effort ?? "",
    error: sessionView?.error ?? null,
    setModel: (id, value) => actions.setModel(id, value).finally(prestart.refresh),
    setEffort: (id, value) => actions.setEffort(id, value).finally(prestart.refresh),
    labelOf: (value) =>
      (info?.models ?? prestart.chip?.models)?.find((m) => m.value === value)?.label ?? value,
  });
  const chip = info ?? prestart.chip;
  const modelReviewRequired = sessionView?.openRefused === "model_review_required";
  const profile = binding !== undefined && binding !== "unbound";
  return (
    <MediaComposer
      {...props}
      destinationLabel={destinationLabel(connections.labels)}
      {...(chip
        ? {
            ...(!profile ? { models: chip.models } : {}),
            model: chip.model,
            effort: chip.effort,
            onModel: sessionId ? choice.pickModel : (value) => prestart.hold("model", value),
            onEffort: sessionId ? choice.pickEffort : (value) => prestart.hold("effort", value),
            modelNote: choice.note,
            modelDisabledReason: modelReviewRequired
              ? t("llm.reopen")
              : sessionId && !sessionView?.synced
                ? t("common.state.loading")
                : undefined,
          }
        : {})}
    />
  );
}

function focusedMessageBox(): HTMLElement | null {
  return (
    document.querySelector<HTMLElement>(
      'section.pane[data-focused] textarea[aria-label="Message"]',
    ) ??
    document.querySelector<HTMLElement>(
      'main.conversation > .composer-slot textarea[aria-label="Message"]',
    )
  );
}

export function App({
  viki,
  rest,
  durable,
  shell,
  assets,
  avatars,
  organization,
  archives,
  conversationSearch,
  vaultApi,
  appMemoryApi,
  vaultOpsApi,
  vaultKnowledgeApi,
  capturesApi,
  profileApi,
  profileLaunchApi,
  sessionPrefsApi,
  nativeAssetsApi,
  inventoryApi,
  mcpServersApi,
  subagentsApi,
  runsApi,
  decisions,
  skillProposals,
  excerpts,
  routinesApi,
  codeApi,
  rootsApi,
  reviewApi,
  checksApi,
  terminalApi,
  previewsApi,
  providerLoginApi,
  agentChangesApi,
  contextApi,
  limitsApi,
  decisionApi,
  messagingApi,
  botsApi: botsClient,
  botUsageApi,
  betweenChatsApi,
  sessionHandoff,
  sessionWindowsApi,
}: AppProps) {
  const [launchHandoff] = useState<SessionHandoff>(() => sessionHandoff ?? { kind: "none" });
  const [handoffActive, setHandoffActive] = useState(launchHandoff.kind !== "none");
  const explicitVisit = useRef<{ id: string; requestId: string | null } | null>(null);
  const restoredSeen = useRef(0);
  const restorationVisits = useRef(new Set<string>());
  const [visitSerial, setVisitSerial] = useState(0);
  const handoffOpened = useRef(false);
  const handoffRecorded = useRef(false);
  const windowCalls = useRef(new Map<string, AbortController>());
  const [windowPending, setWindowPending] = useState<ReadonlySet<string>>(new Set());
  const [windowErrors, setWindowErrors] = useState<ReadonlyMap<string, string>>(new Map());
  const [tabWindowNote, setTabWindowNote] = useState<string | null>(null);
  const terminalCall = useRef<AbortController | null>(null);
  const [sessionActionNote, setSessionActionNote] = useState("");
  const [exportSessionId, setExportSessionId] = useState<string | null>(null);
  const exportTools = useRef<HTMLElement | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const toolsButton = useRef<HTMLButtonElement | null>(null);
  const toolsPanelId = useId();
  // biome-ignore lint/correctness/useExhaustiveDependencies: Abort requests if their API instance is replaced as well as on unmount.
  useEffect(() => {
    setWindowPending(new Set());
    setWindowErrors(new Map());
    setTabWindowNote(null);
    return () => {
      for (const call of windowCalls.current.values()) call.abort();
      windowCalls.current.clear();
    };
  }, [sessionWindowsApi]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: A pending terminal request belongs to this API instance.
  useEffect(() => () => terminalCall.current?.abort(), [terminalApi]);
  const [archiveOpen, setArchiveOpen] = useState<ReadOnlyConversationArchive | null>(null);
  const [historyGap, setHistoryGap] = useState<{
    session: string;
    generation: string | null;
  } | null>(null);
  const [selectedTurns, setSelectedTurns] = useState<ReadonlySet<string>>(new Set());
  const [turnJump, setTurnJump] = useState<{ turnId: string; request: number; blockId?: string }>();
  // The run list's reviewed results, held here rather than in
  // AgentRuns so a mark outlives the right rail, which unmounts whenever another view is open.
  const [reviewedResults, setReviewedResults] = useState<
    ReadonlyMap<string, string | RoutingBlock>
  >(new Map());
  // Stable, and unchanged when every result is already marked: AgentRuns calls this from an
  // effect that lists it as a dependency.
  const markReviewed = useCallback(
    (keys: readonly string[]) => {
      const current = runReviewIdentities(viki.store.getSnapshot().sessions);
      const marks = keys.flatMap((key) => {
        const identity = current.get(key);
        return identity === undefined ? [] : [[key, identity] as const];
      });
      setReviewedResults((prev) =>
        marks.every(([key, identity]) => prev.get(key) === identity)
          ? prev
          : new Map([...prev, ...marks]),
      );
    },
    [viki.store],
  );
  const [showArchived, setShowArchived] = useState(false);
  const [restStore] = useState(() => createRestStore(rest, viki.store));
  const [shellStore] = useState(() => createShellStore(shell ?? null, viki.store));
  const shellSnapshot = useSnapshot(shellStore);
  const activeWorkspaceId = shellSnapshot.capabilities?.workspace.active?.id ?? null;
  const activeWorkspaceIdentity = useRef(activeWorkspaceId);
  const activeWorkspaceGeneration = useRef(0);
  if (activeWorkspaceIdentity.current !== activeWorkspaceId) {
    activeWorkspaceIdentity.current = activeWorkspaceId;
    activeWorkspaceGeneration.current += 1;
  }
  const activeWorkspaceEpoch = activeWorkspaceGeneration.current;
  // The tabs and their panes. The layout is saved to `layout.panes` through the same gate as the
  // rail widths below: a ready, writable settings document that lists the key.
  const panesSettings = shellSnapshot.settingsStatus === "ready" ? shellSnapshot.settings : null;
  const panes = usePanes({
    enabled: Boolean(organization),
    handoff: launchHandoff.kind !== "none",
    settings: panesSettings,
    save:
      typeof shell?.patchSettings === "function" &&
      panesSettings?.keys[LAYOUT_SETTING] &&
      !panesSettings.read_only &&
      panesSettings.values !== null &&
      !shellSnapshot.recovery
        ? (text) => shellStore.patch({ [LAYOUT_SETTING]: text })
        : null,
    refresh: () => shellStore.refreshSettings(),
  });
  // Every layout change goes through `updatePanes`: it applies each op to the newest layout,
  // including a change made earlier in the same event or effect, and returns the result.
  const newestPanes = useRef(panes.layout);
  newestPanes.current = panes.layout;
  const panesUpdate = useRef(panes.update);
  panesUpdate.current = panes.update;
  const updatePanes = useCallback((op: (l: PaneLayout) => PaneLayout): PaneLayout => {
    const next = op(newestPanes.current);
    if (next !== newestPanes.current) {
      newestPanes.current = next;
      panesUpdate.current(() => next);
    }
    return next;
  }, []);
  // Tab pins are the strip's own, apart from the session metadata pin the rail shows.
  const pinnedTabs = useMemo(() => new Set(panes.layout.pinned), [panes.layout.pinned]);
  // Chats this window opened existing-only on its own account (a restored layout, a dropped
  // tab): a refusal drops their tab silently instead of showing the unavailable view.
  const quietOpens = useRef(new Set<string>());
  // Restored file tabs, by tab id with the file each held then. `uncheckedFiles` wait for this
  // window's code folder listing, which drops those outside a registered, available folder;
  // `quietFiles` close without a word when their first read answers not_found or private_path.
  const uncheckedFiles = useRef(new Map<string, { workspaceId: string; ref: string }>());
  const quietFiles = useRef(new Map<string, { workspaceId: string; ref: string }>());
  // Each file tab's preview request, kept until its folder's registration (`codeFolderKey`) or
  // the code client changes, so a pane reads its file again only then (`paneRequest`).
  const fileRequests = useRef(new Map<string, CodeOpenRequest>());
  // A closed file tab's request goes with it, so a tab given its id later starts from its own open.
  useEffect(() => {
    for (const id of fileRequests.current.keys())
      if (!fileOf(panes.layout.files, id)) fileRequests.current.delete(id);
  }, [panes.layout.files]);
  // The file tab the user just opened (`focusOpenedFile`): its preview takes the keyboard focus
  // as it mounts in the commit that shows it. Every commit clears it, so a later mount of that
  // preview (a restore, a layout change, its tab chosen again) leaves the focus alone.
  const freshFileTab = useRef<string | null>(null);
  useEffect(() => {
    freshFileTab.current = null;
  });
  // File panes read through this client: the code client itself, except that a first read
  // finding a quiet tab's file gone or private also closes that tab.
  const fileApi = useMemo((): CodeApi | undefined => {
    if (!codeApi) return undefined;
    const settle = (workspaceId: string, ref: string, error: unknown) => {
      const gone =
        error instanceof CodeApiError &&
        (error.code === "not_found" || error.code === "private_path");
      const closing: string[] = [];
      for (const [id, file] of quietFiles.current) {
        if (file.workspaceId !== workspaceId || file.ref !== ref) continue;
        quietFiles.current.delete(id);
        if (gone) closing.push(id);
      }
      if (closing.length === 0) return;
      updatePanes((l) =>
        closing.reduce((next, id) => {
          const file = fileOf(next.files, id);
          return file?.workspaceId === workspaceId && file.ref === ref ? closeTab(next, id) : next;
        }, l),
      );
    };
    return {
      ...codeApi,
      file: (workspaceId, ref, signal) =>
        codeApi.file(workspaceId, ref, signal).then(
          (file) => {
            settle(workspaceId, ref, null);
            return file;
          },
          (error: unknown) => {
            // An aborted read says nothing about the file.
            if (!signal?.aborted) settle(workspaceId, ref, error);
            throw error;
          },
        ),
    };
  }, [codeApi, updatePanes]);
  const currentFileApi = useRef(fileApi);
  currentFileApi.current = fileApi;
  type RailSide = "left" | "right";
  const railKeys = { left: "layout.left_rail_width", right: "layout.right_rail_width" };
  const [railChanges, setRailChanges] = useState<
    Partial<Record<RailSide, { value: number | null; dirty: boolean }>>
  >({});
  const currentRailChanges = useRef(railChanges);
  const railGate = useRef({ pending: false, reload: false, mounted: true });
  const [railPending, setRailPending] = useState(false);
  const [railProblem, setRailProblem] = useState("");
  const [railReview, setRailReview] = useState<SettingsDocument | null>(null);
  useEffect(() => {
    railGate.current.mounted = true;
    return () => {
      railGate.current.mounted = false;
    };
  }, []);
  function updateRailChanges(changes: typeof railChanges) {
    currentRailChanges.current = changes;
    setRailChanges(changes);
  }
  async function saveRailWidth(side: RailSide) {
    const request = currentRailChanges.current[side];
    if (!request?.dirty || railGate.current.pending || railGate.current.reload) return;
    const snapshot = shellStore.getSnapshot();
    const settings = snapshot.settings;
    if (
      typeof shell?.patchSettings !== "function" ||
      snapshot.settingsStatus !== "ready" ||
      !settings?.keys[railKeys[side]] ||
      settings.read_only ||
      settings.values === null ||
      snapshot.recovery
    ) {
      railGate.current.reload = true;
      setRailProblem(
        settings?.read_only
          ? "Rail widths remain local because settings are read-only."
          : "Rail widths remain local because settings are unavailable.",
      );
      return;
    }
    railGate.current.pending = true;
    setRailPending(true);
    setRailProblem("");
    try {
      // No await precedes patch: its existing store pins this document's revision immediately.
      await shellStore.patch({ [railKeys[side]]: request.value });
      if (!railGate.current.mounted) return;
      // Each accepted local commit owns a new object. An older settlement cannot replace it.
      if (currentRailChanges.current[side] === request)
        updateRailChanges({
          ...currentRailChanges.current,
          [side]: { ...request, dirty: false },
        });
      setRailReview(null);
    } catch (error) {
      if (!railGate.current.mounted) return;
      railGate.current.reload = true;
      setRailReview(error instanceof ShellApiError ? error.current : null);
      setRailProblem(
        error instanceof ShellApiError && error.code === "stale_revision"
          ? "Settings changed in another window. Reload current values before saving rail widths."
          : "Rail widths could not be saved. Reload current values before saving.",
      );
    } finally {
      railGate.current.pending = false;
      if (railGate.current.mounted) setRailPending(false);
    }
  }
  function commitRailWidth(side: RailSide, value: number | null) {
    if (value !== null && (!Number.isFinite(value) || value <= 0)) return;
    updateRailChanges({ ...currentRailChanges.current, [side]: { value, dirty: true } });
    void saveRailWidth(side);
  }
  async function reloadRailWidths() {
    if (railGate.current.pending || typeof shell?.settings !== "function") return;
    railGate.current.pending = true;
    setRailPending(true);
    await shellStore.refreshSettings();
    railGate.current.pending = false;
    if (!railGate.current.mounted) return;
    const snapshot = shellStore.getSnapshot();
    const ready = snapshot.settingsStatus === "ready" && snapshot.settings?.read_only === false;
    railGate.current.reload = !ready;
    setRailReview(snapshot.settings);
    setRailProblem(
      ready ? "" : "Current settings are unavailable or read-only. Widths remain local.",
    );
    setRailPending(false);
  }
  const railWidths = { left: null as number | null, right: null as number | null };
  for (const side of ["left", "right"] as const) {
    const change = railChanges[side];
    const stored =
      typeof shell?.patchSettings === "function" && shellSnapshot.settings?.keys[railKeys[side]]
        ? shellSnapshot.settings.values?.[railKeys[side]]
        : null;
    railWidths[side] = change
      ? change.value
      : typeof stored === "number" && Number.isFinite(stored)
        ? stored
        : null;
  }
  const railWidthReview =
    railPending || railProblem || Object.values(railChanges).some((change) => change?.dirty) ? (
      <section className="readiness-banner" aria-label="Rail width review">
        <div>
          <p role="status">
            {railPending
              ? "Saving or loading rail width settings. Local changes remain available."
              : railProblem ||
                "Rail widths have local changes. Review current values before saving."}
          </p>
          {(["left", "right"] as const).map((side) => {
            const change = railChanges[side];
            if (!change?.dirty) return null;
            const fallback = side === "left" ? 272 : 320;
            const current = (railReview ?? shellSnapshot.settings)?.effective[railKeys[side]];
            return (
              <p key={side}>
                {side} rail: current {String(current ?? fallback)} pixels; requested{" "}
                {change.value ?? fallback} pixels{change.value === null ? " (default)" : ""}.{" "}
                <button
                  type="button"
                  className="btn go sm"
                  disabled={railPending || railGate.current.reload}
                  onClick={() => void saveRailWidth(side)}
                >
                  Save {side} rail width
                </button>
              </p>
            );
          })}
          <button
            type="button"
            className="btn alt sm"
            disabled={railPending || typeof shell?.settings !== "function"}
            onClick={() => void reloadRailWidths()}
          >
            Reload current rail widths
          </button>
        </div>
      </section>
    ) : null;
  // One avatar object-URL cache per App, reached through IdentityContext by every reader;
  // disposed on unmount so no blob: URL outlives the window.
  const [avatarUrls] = useState(() => (avatars ? createAvatarUrls(avatars) : null));
  useEffect(() => {
    return () => avatarUrls?.dispose();
  }, [avatarUrls]);
  // Optional normal-mode clients wait for observed capabilities; an unread mode is unknown.
  // The shell-absent harness keeps its existing startup behavior.
  const normalMode = shell === undefined || shellSnapshot.capabilities?.app.mode === "normal";
  const metadata = useSessionMetadata(normalMode ? organization : undefined);
  const archiveMetadataReady =
    !organization || (normalMode && !metadata.loading && metadata.error === null);
  const archivedIds = useMemo(
    () => new Set(metadata.sessions.filter((row) => row.archived).map((row) => row.session_id)),
    [metadata.sessions],
  );
  const archivingNow = useRef(new Set<string>());
  const [archivingIds, setArchivingIds] = useState<ReadonlySet<string>>(new Set());
  const metadataPending = new Set([...metadata.pending, ...archivingIds]);
  const [routinesStore] = useState(() => (routinesApi ? createRoutinesStore(routinesApi) : null));
  const routinesSnapshot = useSnapshot(routinesStore ?? noRoutinesStore);
  useEffect(() => {
    if (!routinesStore || !normalMode) return;
    routinesStore.start();
    return () => routinesStore.stop();
  }, [routinesStore, normalMode]);
  const [notifier] = useState(browserNotifier);
  const [noticeDismissed, setNoticeDismissed] = useState(false);
  const [noticePending, setNoticePending] = useState(false);
  const [noticeError, setNoticeError] = useState<
    | "shell.needs_you.notice_request_failed"
    | "routines.controls.permission_denied"
    | "routines.controls.permission_default"
    | "routines.controls.permission_unsupported"
    | "routines.controls.save_failed"
    | null
  >(null);
  const noticeInFlight = useRef(false);
  const [selectedRoutine, setSelectedRoutine] = useState<string | null>(null);
  const selectedRoutineRef = useRef<string | null>(null);
  // Which profile a chat session is a Bot's, as far as this window has bound it: set at each of
  // the Bot binds below (and by `botsApi.list` itself, just below) and read by `identityFor`/the
  // focused identity (`profile.botProfileId`) and by `renderRunningWork`'s `botProfileIdOf`.
  // Declared ahead of `botsApi` so its own `list` wrapping can close over `markBotSession`.
  const [botSessions, setBotSessions] = useState<ReadonlyMap<string, string>>(new Map());
  const markBotSession = useCallback((sessionId: string, profileId: string) => {
    setBotSessions((prev) =>
      prev.get(sessionId) === profileId ? prev : new Map(prev).set(sessionId, profileId),
    );
  }, []);
  // Manual Look choices and automatic hue assignment share this settings write queue.
  const botLookQueue = useMemo(
    () =>
      createBotLookQueue({
        read: () => {
          const settings = shellStore.getSnapshot().settings?.effective;
          return {
            hues: parseBotHues(settings?.[BOT_HUES_KEY]),
            icons: parseBotIcons(settings?.[BOT_ICONS_KEY]),
          };
        },
        patch: (changes) => shellStore.patch(changes),
      }),
    [shellStore],
  );
  const assignHue = useMemo(
    () =>
      botsClient &&
      hueAssigner({
        list: async () => (await botsClient.list()).bots,
        assign: botLookQueue.assign,
      }),
    [botsClient, botLookQueue],
  );
  // The one wrapped client every caller reads `botsApi` as: hue-assigning on `create` (above),
  // and here marking every listed Bot's own chat as its session. This is `botSessions`' other
  // writer — beside the explicit Bot binds below — so a live Bot's main chat keeps its icon in
  // Running work and other panes after this window leaves it, and after a fresh mount, once
  // `botsApi.list` has resolved once.
  const botsApi = useMemo(() => {
    const hued = botsClient && assignHue ? assigningHues(botsClient, assignHue) : botsClient;
    if (!hued) return hued;
    return {
      ...hued,
      list: async (signal?: AbortSignal) => {
        const answer = await hued.list(signal);
        for (const row of answer.bots) markBotSession(row.chat_session_id, row.profile_id);
        return answer;
      },
    };
  }, [botsClient, assignHue, markBotSession]);
  const [selectedBot, setSelectedBot] = useState<string | null>(() =>
    botsApi ? storedBot() : null,
  );
  const selectedBotRef = useRef(selectedBot);
  const [botsSection, setBotsSection] = useState<"team" | "profiles" | "usage">("team");
  // Refresh Bots re-reads Kanban; a strip seat and Edit profile hand one selection to the
  // view they open, never stored.
  const [teamCheck, setTeamCheck] = useState(0);
  const [seatRequest, setSeatRequest] = useState<SeatId | null>(null);
  const [profileFocus, setProfileFocus] = useState<string | null>(null);
  useEffect(() => {
    try {
      if (selectedBot === null) window.sessionStorage.removeItem(BOT_STORAGE_KEY);
      else window.sessionStorage.setItem(BOT_STORAGE_KEY, selectedBot);
    } catch {
      // Navigation remains usable when session storage is unavailable.
    }
  }, [selectedBot]);
  // A notification is clicked long after it was shown, so its click reaches the freshest
  // `openRoutine` through this ref, never the one of the render that showed it.
  const openRoutineRef = useRef<(routineId: string) => void>(() => {});
  const onRoutineNotice = useCallback((routineId: string) => openRoutineRef.current(routineId), []);
  // A run notice is clicked long after it was shown, same reasoning as openRoutineRef above.
  const openRunRef = useRef<(sessionId: string, blockId: string | null) => void>(() => {});
  const onRunNotice = useCallback(
    (sessionId: string, blockId: string | null) => openRunRef.current(sessionId, blockId),
    [],
  );
  const [editors] = useState(() => createEditorStore(durable));
  // Card answers go to the durable ledger, never over the socket: one recorded choice
  // per native request, whatever this window, another window or a reload does.
  const [approvals] = useState(() => createApprovalStore(durable));
  const editorSnapshot = useSnapshot(editors);
  const approvalSnapshot = useSnapshot(approvals);
  const appSnapshot = useSnapshot(viki.store);
  const reviewedRuns = useMemo(() => {
    const current = runReviewIdentities(appSnapshot.sessions);
    return new Set(
      [...reviewedResults]
        .filter(([key, identity]) => current.get(key) === identity)
        .map(([key]) => key),
    );
  }, [appSnapshot.sessions, reviewedResults]);
  const requests = useRunRequests(runsApi, appSnapshot.sessions);
  const runGroups = useMemo(
    () => groupRuns(appSnapshot.sessions, reviewedRuns, requests),
    [appSnapshot.sessions, reviewedRuns, requests],
  );
  // How many of a session's own runs are still going, for that chat's header pill.
  const runningBySession = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of [...runGroups.needs, ...runGroups.working])
      counts.set(row.sessionId, (counts.get(row.sessionId) ?? 0) + 1);
    return counts;
  }, [runGroups]);
  // One stop state for a run wherever it lists: AgentRuns and Needs you both read and
  // drive this same `RunStops`, over the run groups' needs and working keys, so a Stop sent from
  // either keeps its phase if the run moves to the other list before it ends.
  const runningRunKeys = useMemo(
    () => new Set([...runGroups.needs, ...runGroups.working].map((row) => row.key)),
    [runGroups],
  );
  const stops = useRunStops(runsApi, runningRunKeys);
  // A routine run is a session of its own, so the live set changing is when a run started or
  // ended; the list is re-read then rather than at the next poll. Skipped at mount, which
  // `start` already reads.
  const liveKey = [...(appSnapshot.live?.ids ?? [])].sort().join(" ");
  const lastLiveKey = useRef(liveKey);
  useEffect(() => {
    if (lastLiveKey.current === liveKey) return;
    lastLiveKey.current = liveKey;
    if (!normalMode) return;
    routinesStore?.refresh();
  }, [liveKey, routinesStore, normalMode]);
  const restSnapshot = useSnapshot(restStore);
  const rails = restSnapshot.rails;

  // Session roots: read at mount, and again each time the sessions listing settles (it goes to
  // `loading` first), so a root recorded by another window reaches the labels. A failed read keeps
  // the last snapshot, so its rejection has nothing to report.
  const roots = useRoots();
  const sessionsSettled = restSnapshot.sessionsStatus !== "loading";
  useEffect(() => {
    if (!rootsApi || !normalMode) return;
    const controller = new AbortController();
    refreshRoots(rootsApi, controller.signal).catch(() => {});
    return () => controller.abort();
  }, [rootsApi, normalMode]);
  useEffect(() => {
    if (!rootsApi || !normalMode || !sessionsSettled) return;
    const controller = new AbortController();
    refreshRoots(rootsApi, controller.signal).catch(() => {});
    return () => controller.abort();
  }, [rootsApi, sessionsSettled, normalMode]);
  // `startSessionIn` reads this render's `liveIds`, `sessionsBlocked` and `view`, so the Workbench's
  // one memoized object reaches it through a ref assigned every render, as `newSessionRef` does.
  const startSessionInRef = useRef<WorkbenchRoots["onStartSession"]>(async () => "unavailable");
  const sessionStart = useRef({ pending: false, mounted: true });
  useEffect(() => {
    sessionStart.current.mounted = true;
    return () => {
      sessionStart.current.mounted = false;
    };
  }, []);
  const workbenchRoots = useMemo<WorkbenchRoots | undefined>(
    () =>
      rootsApi
        ? {
            api: rootsApi,
            onStartSession: (root) => startSessionInRef.current(root),
            onRaiseLimit: () => openLimitSettingRef.current(),
          }
        : undefined,
    [rootsApi],
  );
  const workbenchReview = useMemo<WorkbenchReview | undefined>(
    () =>
      reviewApi
        ? {
            api: reviewApi,
            onOpenUrl: (url: string) => viki.actions.openUrl(url),
          }
        : undefined,
    [reviewApi, viki],
  );
  const workbenchChecks = useMemo<WorkbenchChecks | undefined>(
    () => (checksApi ? { api: checksApi, canRun: true } : undefined),
    [checksApi],
  );
  const workbenchTerminal = useMemo<WorkbenchTerminal | undefined>(
    () => (terminalApi ? { api: terminalApi, canOpen: true } : undefined),
    [terminalApi],
  );
  const workbenchPreviews = useMemo<WorkbenchPreviews | undefined>(
    () => (previewsApi ? { api: previewsApi, canRun: platformKind() === "windows" } : undefined),
    [previewsApi],
  );
  // The not-signed-in card's own Sign in strip, on every OS; the server refuses where it cannot open a console.
  const signIn = providerLoginApi;

  // Sessions with a missing-source Remove in flight: disables that record's own button while
  // its Remove runs, so no click reaches a disabled button's handler twice.
  const [removingIds, setRemovingIds] = useState<ReadonlySet<string>>(new Set());

  const [selectedIdState, setSelectedIdState] = useState<string | null>(() =>
    launchHandoff.kind === "target" ? launchHandoff.sessionId : null,
  );
  const selectionRef = useRef<string | null>(null);
  const selectionGeneration = useRef(0);
  const scrollBySession = useRef(new Map<string, SavedScroll>());
  const [newEditorId, setNewEditorId] = useState<string>(() => crypto.randomUUID());
  // New-chat keys this window minted and has not yet started: seeded with that initial id,
  // joined by each key restoreText mints, and left by handleSend's new-chat branch once it hands
  // the key to viki.actions.newSession (it has started). The pre-start chip's `enabled` offers
  // the held-pick path only for a key still in this set, so a drafts-list pick of some OTHER key
  // (a closed chat this window never minted as new) gets no chip instead of a hold whose commit
  // the server can only refuse (its transcript already exists).
  const mintedRef = useRef<Set<string>>(new Set([newEditorId]));
  const emptyPaneEditors = useRef(new Map<string, string>());
  const prestarts = useRef(new Map<string, PrestartChip>());
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [decisionsOpen, setDecisionsOpen] = useState(false);
  // Bumped when the inbox opens and after every recorded decision, so the section re-reads the
  // list.
  const [decisionsKey, setDecisionsKey] = useState(0);
  // The failure line of a queued decision that failed after Approvals closed, until the user
  // dismisses it or opens Approvals again, which moves it to the panel's own line (showDecisions).
  const [lateFailure, setLateFailure] = useState<string | null>(null);
  // This window's one decision line (decisionQueue.tsx), created once and handed to every
  // DecisionInbox mount, so a reopened Approvals waits behind whatever the last close is still
  // sending instead of starting a line of its own.
  const [approvalLine] = useState(decisionLine);
  // The vault or skill proposal whose inspector is open, or null.
  const [inspecting, setInspecting] = useState<ProposalRow | null>(null);
  // Sessions whose skill hint the user dismissed (Not now) or already acted on (Make this a
  // skill), keyed by session id: SkillTurnActions reads its own session's entry.
  const [skillHintsOff, setSkillHintsOff] = useState<ReadonlySet<string>>(new Set());
  // The inbox's own line about the last hand-off: the draft was opened (`status`), or the editor
  // cap refused it (`alert`). Rendered in the panel, which is where the user is looking.
  const [inboxNote, setInboxNote] = useState<{
    text: string;
    kind: "status" | "alert";
  } | null>(null);
  // Excerpt chips belong to one exact editor; receipts keep that owner after focus changes.
  const [excerptsByEditor, setExcerptsByEditor] = useState<
    Readonly<Record<string, readonly VaultExcerptPart[]>>
  >({});
  function setEditorExcerpts(
    key: string,
    parts:
      | readonly VaultExcerptPart[]
      | ((current: readonly VaultExcerptPart[]) => readonly VaultExcerptPart[]),
  ): void {
    setExcerptsByEditor((current) => ({
      ...current,
      [key]: typeof parts === "function" ? parts(current[key] ?? []) : parts,
    }));
  }
  const sentExcerpts = useRef(
    new Map<string, { editorKey: string; parts: readonly VaultExcerptPart[] }>(),
  );
  const pendingExcerptEditors = useRef(new Set<string>());
  // The failed-or-cancelled turn Retry… is reviewing (Conversation's onRetryTurn); null closes it.
  // `incomplete` is the view's truncated flag when the review opened, so it does not follow a
  // later change to the live view.
  const [retryTurn, setRetryTurn] = useState<{ turn: Turn; incomplete: boolean } | null>(null);
  // The inbox triage dialog (00-inbox capture filing) and the user's destination choice for
  // each capture still open in it; the dialog itself prunes a choice whose capture is gone or
  // has moved on to a new revision.
  const [triageOpen, setTriageOpen] = useState(false);
  const [triageChoices, setTriageChoices] = useState<readonly TriageChoice[]>([]);
  const [vaultFolder, setVaultFolder] = useState("");
  // The note a decision asked the Vault view to open, and how many such requests have landed.
  // `VaultBrowser` consumes `initialRef` once per mount, so each request needs a fresh mount,
  // which the counter gives it through the view's key. Entering the view any other way (the tab,
  // a tile) clears the ref and leaves the counter alone: changing the key there would remount the
  // browser and drop an unsaved note edit without asking.
  const [noteRequest, setNoteRequest] = useState<{
    ref: string;
    mount: number;
    draft?: NoteDraft;
  }>({
    ref: "",
    mount: 0,
  });
  const capabilitiesAvailable = Boolean(nativeAssetsApi || inventoryApi || profileApi);
  const botsAvailable =
    Boolean(botsApi && profileApi && profileLaunchApi) &&
    commandFor("bots.open", shellSnapshot).enabled;
  // Bots keeps its rail slot, held disabled, until the capabilities arrive, so the destinations
  // below it do not move and an early click cannot be silently refused.
  const botsInRail =
    botsAvailable ||
    (Boolean(shell && botsApi && profileApi && profileLaunchApi) &&
      shellSnapshot.capabilities === null);
  // Vault, Agents, Routines, Code and Capabilities come back only with their client: without it the view
  // renders nothing.
  const [nav, setNav] = useState<Navigation>(() =>
    launchHandoff.kind !== "none"
      ? CHAT_NAVIGATION
      : storedNavigation([
          "settings",
          "data",
          ...(vaultApi ? (["vault"] as const) : []),
          ...(profileApi ? (["agents"] as const) : []),

          ...(routinesApi ? (["routines"] as const) : []),
          ...(codeApi ? (["code"] as const) : []),
          ...(contextApi ? (["context"] as const) : []),
          ...(capabilitiesAvailable ? (["capabilities"] as const) : []),
          ...(messagingApi ? (["messaging"] as const) : []),
          ...(botsApi && profileApi && profileLaunchApi
            ? (["bots", "kanban", "bot_threads"] as const)
            : []),
          ...(decisionApi ? (["decision"] as const) : []),
        ]),
  );
  const currentNavigation = useRef(nav);
  const applyNavigation = useCallback((next: Navigation) => {
    currentNavigation.current = next;
    setNav(next);
  }, []);
  const [botChat, setBotChat] = useState<{
    sessionId: string;
    botId: string;
    bot: BotRow;
    main: boolean;
    role: string | null;
    workspaceGeneration: number;
  } | null>(null);
  const backReasonId = useId();
  const navigationHistory = useRef<NavigationHistory>([]);
  const vaultNavigation = useRef<VaultNavigationHandle | null>(null);
  const vaultSnapshots = useRef(new WeakMap<NavigationContext, VaultNavigationSnapshot>());
  const [vaultRestore, setVaultRestore] = useState<VaultNavigationSnapshot | null>(null);
  const registerVaultNavigation = useCallback((handle: VaultNavigationHandle) => {
    vaultNavigation.current = handle;
    return () => {
      if (vaultNavigation.current === handle) vaultNavigation.current = null;
    };
  }, []);
  const pageBack = useRef<{ element: HTMLButtonElement; navigation: Navigation } | null>(null);
  const openerIdentities = useRef(new WeakMap<HTMLElement, NavigationOpenerIdentity>());
  const pendingBack = useRef<{ navigation: Navigation; context: NavigationContext } | null>(null);
  const historyRecovery = useRef(shellSnapshot.recovery);
  useEffect(() => {
    if (historyRecovery.current === shellSnapshot.recovery) return;
    navigationHistory.current = [];
    historyRecovery.current = shellSnapshot.recovery;
  }, [shellSnapshot.recovery]);
  // The Appearance page's live preview, above the committed document. null = nothing to override.
  const [appearancePreview, setAppearancePreview] = useState<AppearanceValues | null>(null);
  // A stale-revision panel the user has already answered. Compared by identity with the error
  // the import panel is still holding, which is the only thing that can close it from here.
  const [dismissedConflict, setDismissedConflict] = useState<ShellApiError | null>(null);
  // Below 800px the left rail is a drawer and below 1280px the right one is. The JS
  // mode is the single source of truth (a closed drawer is unmounted), and every action that
  // takes the user somewhere else closes the drawer first so no two modals ever coexist.
  const layout = useLayoutMode();
  const [drawer, setDrawer] = useState<"left" | "right" | null>(null);
  // Where the header's running-agents pill sends the user: the right rail's own list, scrolled
  // and focused there when it is already docked, else the drawer showRuns also opens.
  const agentsList = useRef<HTMLElement | null>(null);
  const [runsFocus, setRunsFocus] = useState(0);
  // Local refusal or an unconfirmed control result leaves the editor text available for review.
  const [sendRefusals, setSendRefusals] = useState<Readonly<Record<string, string | null>>>({});
  const [rightRailPanel, setRightRailPanel] = useState<"overview" | "code">("overview");
  const [codeRailVisited, setCodeRailVisited] = useState(false);
  const codeRailId = useId();
  const codeRailVisible = Boolean(codeApi && rightRailPanel === "code");
  const [codeRequest, setCodeRequest] = useState<CodeOpenRequest | null>(null);
  const currentCodeRequest = useRef(codeRequest);
  const currentCodeApi = useRef(codeApi);
  currentCodeApi.current = codeApi;
  const codeOpener = useRef<HTMLElement | null>(null);
  const codeCloseFocus = useRef<"opener" | "composer" | null>(null);
  useLayoutEffect(() => {
    if (codeRequest && codeRequest.api !== codeApi && currentCodeRequest.current === codeRequest) {
      currentCodeRequest.current = null;
      setCodeRequest(null);
    }
  }, [codeApi, codeRequest]);
  useLayoutEffect(() => {
    if (codeRequest || !codeCloseFocus.current) return;
    const focus = codeCloseFocus.current;
    codeCloseFocus.current = null;
    const composer = focusedMessageBox();
    const candidates =
      focus === "composer"
        ? [composer]
        : [
            codeOpener.current,
            document.querySelector<HTMLElement>('button[aria-label="VIKI panel"]'),
            document.getElementById(`${codeRailId}-tab-code`),
            composer,
          ];
    candidates
      .find((element) => element && eligibleNavigationOpener(element))
      ?.focus({
        preventScroll: true,
      });
  }, [codeRequest, codeRailId]);
  // Armed by the row menu's Fork or a BlockedCard's Continue in a fork: the session to fork from.
  // Arming creates an independent editor; its first send admits the fork target before
  // saving and submitting the exact text through the durable service.
  const [forkArmedFrom, setForkArmedFrom] = useState<string | null>(null);
  const [forkEditorId, setForkEditorId] = useState<string | null>(null);
  const currentForkEditor = useRef(forkEditorId);
  currentForkEditor.current = forkEditorId;
  // Each session's profile binding, as far as this window knows it: bound (the identity it runs
  // as, or null only when the server's answer omits it), "unbound" (the inspection answered 404
  // `not_found`, or this window minted the id for a plain chat), or no entry while unknown (no
  // answer yet, or a failed read). A recorded session never goes back to unknown, and a later 404
  // never unbinds it: a bound chat keeps what it ran as, even if the profile is later revised. A
  // later successful inspection records bound, keeping a known identity when it names none. Both
  // callbacks are stable (`useCallback`, no deps) because `ProfileSessionReview`'s effect re-runs
  // whenever either changes. The new-chat editor's first id is minted here, so it starts unbound.
  const [bindings, setBindings] = useState<
    ReadonlyMap<string, ProfileSessionIdentity | null | "unbound">
  >(() => new Map([[newEditorId, "unbound"]]));
  const handleProfileSession = useCallback(
    (sessionId: string, sessionIdentity: ProfileSessionIdentity | null) => {
      setBindings((prev) => {
        const prior = prev.get(sessionId);
        const next = new Map(prev);
        next.set(sessionId, sessionIdentity ?? (typeof prior === "object" ? prior : null));
        return next;
      });
    },
    [],
  );
  const handleUnboundSession = useCallback((sessionId: string) => {
    setBindings((prev) => (prev.has(sessionId) ? prev : new Map(prev).set(sessionId, "unbound")));
  }, []);
  const botThreads = useMemo(
    () =>
      createBotThreadStore({
        viki,
        editors,
        durable,
        api: botsApi,
        profiles: profileApi,
        launch: profileLaunchApi,
        canRun: (workspaceId) => {
          const current = shellStore.getSnapshot();
          return (
            current.workspaces?.active_id === workspaceId &&
            commandFor("bots.open", current).enabled &&
            commandFor("session.new", current).enabled
          );
        },
        onPrepared: (preview) => {
          handleProfileSession(preview.session_id, preview.identity ?? null);
          markBotSession(preview.session_id, preview.profile_id);
        },
      }),
    [
      viki,
      editors,
      durable,
      botsApi,
      profileApi,
      profileLaunchApi,
      handleProfileSession,
      markBotSession,
      shellStore,
    ],
  );
  const botThreadSnapshot = useSnapshot(botThreads);
  useEffect(() => () => botThreads.dispose(), [botThreads]);
  // The Agents view's own tab: which of its two sections is on screen. Reset never happens on
  // its own — leaving Agents keeps the last section picked, the same way Settings keeps its page.
  const [agentsSection, setAgentsSection] = useState<AgentsSectionId>("profiles");
  const [skillsVisit, setSkillsVisit] = useState(0);
  const attemptedForks = useRef(new Set<string>());
  const sendingKeys = useRef(new Set<string>());
  // Set on a refused 4th session; cleared by the toast's own dismiss control or by a later
  // "New session" that succeeds because a slot is free again — never by a row's own Close, which
  // can still be refused and so proves nothing on its own about the count.
  const [toastOpen, setToastOpen] = useState(false);
  const [renameSessionId, setRenameSessionId] = useState<string | null>(null);
  // Set by `openLimitSetting` below for `SettingsView` to focus once the field it names
  // actually renders; cleared by that view's own `onFocusKeyUsed` once it does. Scoped to the
  // one navigation that set it by `limitFocusNav` below — without that, a key set while the
  // document is still loading would survive a page change or a trip out of Settings and fire
  // later, focus and scroll jumping on a visit that never asked for either.
  const [limitFocusKey, setLimitFocusKey] = useState<string | null>(null);
  // The `nav` object `limitFocusKey` was set under (every `navigate()` call, including a page
  // change within Settings — `onPage` below — produces a new one). Read only alongside `nav`
  // at render time, never by itself: a stale key under a `nav` no longer current is the same as
  // no key at all.
  const limitFocusNav = useRef<Navigation | null>(null);

  // The sessions THIS window asked the server to start eagerly, which it does for a brand-new
  // id and never for an existing transcript. They hold a slot from the click, not from the
  // session.started frame that confirms it several milliseconds later — see liveSessionIds.
  const startedHereRef = useRef<Set<string>>(new Set());

  // What Alt+N / Ctrl+Shift+O call — see the keydown effect below. A ref because that listener is
  // installed once and this must still call the freshest `handleNewSession`, which closes over
  // `liveIds` and changes identity every render.
  const newSessionRef = useRef<() => void>(() => {});
  // `workbenchRoots` below is memoized by `rootsApi` alone, so its `onRaiseLimit` reaches this
  // render's `navigate` and focus-setting the same way `onStartSession` reaches `startSessionInRef`.
  const openLimitSettingRef = useRef<() => void>(() => {});
  // The same pattern for the two shortcuts: Ctrl+, runs one command, Escape leaves a view and
  // reports whether it did anything (so the key is only swallowed when it was).
  const commandRef = useRef<(id: CommandId) => void>(() => {});
  const escapeRef = useRef<() => boolean>(() => false);
  // Ctrl+F opens the chat's own tools panel and focuses its search field, but only when the
  // chat view owns it; elsewhere the browser's own find runs instead.
  const findRef = useRef<(target: EventTarget | null) => boolean>(() => false);
  // Alt+1…9 select the nth tab of the strip as shown and report whether a tab was there.
  const selectTabAtRef = useRef<(position: number) => boolean>(() => false);
  // Ctrl+\ ("split"), Ctrl+1…4 (that pane) and Alt+W ("close") act on the panes and report
  // whether they did.
  const paneKeyRef = useRef<(key: "split" | "close" | number) => boolean>(() => false);

  useEffect(() => {
    restStore.start();
    return () => restStore.stop();
  }, [restStore]);

  useEffect(() => {
    shellStore.start();
    return () => shellStore.stop();
  }, [shellStore]);

  // Recovery is a launch-long fact, so the flag never clears: a window serving
  // `create_recovery_app` mounts no /ws, /api/sessions, /api/rails, /api/v2/drafts or
  // /api/v2/commands route, and every request that leaves after this point can only collect a
  // 404 and paint an error the user can do nothing about.
  const durableSuspended = useRef(false);
  useEffect(() => {
    if (!shellSnapshot.recovery) return;
    durableSuspended.current = true;
    restStore.stop();
    viki.client.close();
  }, [shellSnapshot.recovery, restStore, viki]);

  // Entered once per launch, the first time all three facts are known together. The ref is set
  // whether or not the view was entered, because leaving the three cards with Escape changes
  // none of the three facts: without it, the next commit of any of the three documents — a
  // reconnect re-reading capabilities, even the `loading` patch a failing read commits first —
  // would throw the user back into onboarding from the chat, or reset an open create/open form
  // to the cards mid-typing. ("Set up later" needs no ref: its PATCH falsifies the third fact.)
  const onboardingDecided = useRef(false);
  useEffect(() => {
    if (onboardingDecided.current) return;
    if (launchHandoff.kind !== "none") {
      onboardingDecided.current = true;
      return;
    }
    const capabilities = shellSnapshot.capabilities;
    const workspaces = shellSnapshot.workspaces;
    const settings = shellSnapshot.settings;
    if (capabilities === null || workspaces === null || settings === null) return;
    onboardingDecided.current = true;
    if (
      capabilities.workspace.chat_only &&
      workspaces.workspaces.length === 0 &&
      settings.effective["onboarding.completed"] !== true
    )
      applyNavigation({
        ...currentNavigation.current,
        view: "onboarding",
        onboardingMode: "choose",
      });
  }, [applyNavigation, shellSnapshot, launchHandoff]);

  useEffect(() => {
    rememberNavigation(nav);
  }, [nav]);

  // The committed appearance, or the Appearance page's preview while that page is open: leaving
  // the page hands back null and this effect re-runs with the document's own values.
  const prefersLight = usePrefersLight();
  useEffect(() => {
    applyAppearance(
      appearancePreview ?? appearanceFromSettings(shellSnapshot.settings),
      prefersLight,
    );
  }, [appearancePreview, shellSnapshot.settings, prefersLight]);

  useEffect(() => {
    // Read once, with the listener: macOS additions below are additive only, so Windows and
    // Linux (mac === false) fall through every `mac && …` term unchanged.
    const mac = isMac();
    function onKeyDown(event: KeyboardEvent): void {
      if (document.querySelector('[aria-modal="true"]')) return;
      const key = event.key.toLowerCase();
      // Shift or CapsLock resolves event.key to "K", not "k" — the modifier held is still
      // Ctrl either way, so the letter is matched case-insensitively.
      if ((event.ctrlKey || (mac && event.metaKey)) && key === "k") {
        event.preventDefault();
        setPaletteOpen(true);
        return;
      }
      // Both new-session chords match the button. The listener installs once, so it reads the
      // current action through a ref; Ctrl+Shift+O can be consumed by Edge, while Alt+N is not.
      // Each chord excludes the OTHER's modifier rather than merely requiring its own. Without
      // that, Ctrl+Alt+Shift+O matches both, and — on the layouts where AltGr reports itself as
      // ctrlKey+altKey — typing an AltGr character containing "n" would open a session.
      const altN = event.altKey && !event.ctrlKey && key === "n";
      // macOS turns Option+N into a dead key, so `event.key` never settles on "n" there; its
      // physical code is matched instead, mac-only so Windows and Linux keep matching the letter.
      // Skipped on a text-entry target: elsewhere Option+N still opens a session, but a
      // focused input, textarea or contenteditable element gets its dead key (ñ, ã, õ) instead.
      const macAltN =
        mac &&
        event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        event.code === "KeyN" &&
        !isTextEntryTarget(event.target);
      const ctrlShiftO =
        (event.ctrlKey || (mac && event.metaKey)) && event.shiftKey && !event.altKey && key === "o";
      if (altN || macAltN || ctrlShiftO) {
        event.preventDefault();
        newSessionRef.current();
        return;
      }
      // A keystroke that is still being composed is not a command.
      if (event.isComposing || event.keyCode === 229) return;
      if (
        (event.ctrlKey || (mac && event.metaKey)) &&
        !event.altKey &&
        !event.shiftKey &&
        key === "f"
      ) {
        if (!event.defaultPrevented && findRef.current(event.target)) event.preventDefault();
        return;
      }
      if ((event.ctrlKey || (mac && event.metaKey)) && !event.altKey && event.key === ",") {
        event.preventDefault();
        commandRef.current("settings.open");
        return;
      }
      // Ctrl+\ is read by `event.key`, so a JIS keyboard's Yen or Ro key that types "\" works too.
      // Ctrl+1…4 and Alt+W go by physical code, like Alt+digit; on macOS, Option+W in a text field
      // types its character instead, and a held Alt+W closes one tab, not one per repeat.
      const primary = (event.ctrlKey || (mac && event.metaKey)) && !event.altKey;
      const paneDigit = /^Digit([1-4])$/.exec(event.code);
      let paneKey: "split" | "close" | number | null = null;
      if (primary && event.key === "\\") paneKey = "split";
      else if (primary && !event.shiftKey && paneDigit) paneKey = Number(paneDigit[1]);
      else if (
        event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        event.code === "KeyW" &&
        !event.repeat &&
        !(mac && isTextEntryTarget(event.target))
      )
        paneKey = "close";
      if (paneKey !== null) {
        if (paneKeyRef.current(paneKey)) event.preventDefault();
        return;
      }
      // Alt+1…8 select that tab and Alt+9 the last. Read by physical code so Option works on macOS,
      // and left alone in a field that takes typed text or a select that takes the key itself.
      const tabDigit = /^Digit([1-9])$/.exec(event.code);
      if (
        tabDigit &&
        event.altKey &&
        !event.ctrlKey &&
        !event.metaKey &&
        !isTextEntryTarget(event.target) &&
        !(event.target instanceof HTMLSelectElement)
      ) {
        if (selectTabAtRef.current(Number(tabDigit[1]))) event.preventDefault();
        return;
      }
      if (event.key === "Escape") {
        if (
          event.defaultPrevented ||
          event.ctrlKey ||
          event.altKey ||
          event.metaKey ||
          event.shiftKey ||
          isTextEntryTarget(event.target) ||
          event.target instanceof HTMLSelectElement
        )
          return;
        // The app menu is a menu, not a dialog, so the modal guard above does not cover it; it
        // closes itself on Escape and the view must not move out from under that.
        if (document.querySelector('[role="menu"]')) return;
        if (escapeRef.current()) event.preventDefault();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const gitObservation = observeGit(rails?.git, restSnapshot.railsStatus);
  const gitDirty = gitObservation.state === "known" && (gitObservation.dirty ?? 0) > 0;
  const viewUnsaved = useHasUnsavedWork();
  const blockUnload =
    viewUnsaved ||
    unsavedWork(appSnapshot) ||
    Object.values(editorSnapshot.editors).some(
      (entry) =>
        entry.submitting ||
        entry.pendingCommand !== null ||
        entry.status === "saving" ||
        ((entry.text !== "" || entry.attachments.length > 0 || entry.revision > 0) &&
          entry.status !== "saved"),
    );

  // A ref, not derived state: "has the socket ever been open" must stay true after a drop, which
  // is exactly when it is read — derived state computed fresh each render would read back false
  // right when it matters.
  const everOpenRef = useRef(false);
  if (appSnapshot.status === "open") everOpenRef.current = true;

  useEffect(() => {
    if (!blockUnload) return;
    function onBeforeUnload(event: BeforeUnloadEvent): void {
      // Verified against real Edge (152.0.4191.66), not assumed: preventDefault() alone
      // prompts there, and the legacy `returnValue` string prompts only when non-empty (an
      // empty string, unlike a merely-falsy one, does nothing) — some older Chromium-family
      // engines still key off it, so both are set, deliberately, not as one covering the other.
      event.preventDefault();
      event.returnValue = "leave without saving?";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [blockUnload]);

  // A per-launch token the server has refused must not be replayed by the next reload.
  useEffect(() => {
    if (appSnapshot.status !== "unauthorized") return;
    try {
      window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch {
      // storage refused (private mode, quota): nothing more to clear
    }
  }, [appSnapshot.status]);

  /** Every deliberate selection goes through here, so the window remembers one thing and one
   * thing only: what the user last chose to look at. */
  function select(sessionId: string | null): void {
    if (sessionId !== null && archivingNow.current.has(sessionId)) return;
    selectionGeneration.current += 1;
    if (sessionId !== null && panes.restored !== restoredSeen.current)
      restorationVisits.current.add(sessionId);
    restoreRef.current = null;
    setHandoffActive(false);
    explicitVisit.current = sessionId
      ? {
          id: sessionId,
          requestId: viki.store.getSnapshot().sessions[sessionId]?.requestId ?? null,
        }
      : null;
    setVisitSerial((value) => value + 1);
    setArchiveOpen(null);
    if (selectionRef.current !== sessionId) {
      setCardTarget(null);
      selectCodeRequest(null);
      setSelectedTurns(new Set());
      setTurnJump(undefined);
      setHistoryGap(null);
      setRefusalNote(null);
    }
    selectionRef.current = sessionId;
    setSelectedIdState(sessionId);
    rememberSelection(sessionId);
    updatePanes((l) => openChatIn(l, sessionId));
  }

  // Read once per page load, then consumed. `undefined` means "not read yet"; null means there is
  // nothing to restore, which is also what it becomes the moment the restore succeeds or the
  // user selects something on their own.
  const restoreRef = useRef<string | null | undefined>(undefined);
  const botRestoreAttempted = useRef(false);
  if (restoreRef.current === undefined)
    restoreRef.current = launchHandoff.kind === "none" ? storedSelection() : null;

  // Derived, not stored: once the selected view leaves the app snapshot (the session closed),
  // this naturally reads back as null on the very next render — no extra effect needed.
  const selectedId =
    selectedIdState !== null && appSnapshot.sessions[selectedIdState] ? selectedIdState : null;
  const selectedView = selectedId ? appSnapshot.sessions[selectedId] : undefined;
  const [cardTarget, setCardTarget] = useState<{
    sessionId: string;
    toolUseId: string;
    request: number;
  } | null>(null);
  const cardTargetSerial = useRef(0);
  useEffect(() => {
    if (
      cardTarget &&
      (nav.view !== "chat" ||
        selectedId !== cardTarget.sessionId ||
        !appSnapshot.sessions[cardTarget.sessionId]?.cards.some(
          (card) => card.data.tool_use_id === cardTarget.toolUseId,
        ))
    )
      setCardTarget(null);
  }, [cardTarget, nav.view, selectedId, appSnapshot.sessions]);
  const botHere =
    botsApi &&
    botsAvailable &&
    selectedId &&
    botChat?.sessionId === selectedId &&
    botChat.workspaceGeneration === activeWorkspaceGeneration.current
      ? botChat
      : null;
  useEffect(() => {
    setBotChat(null);
    if (!botsApi || !botsAvailable || nav.view !== "chat" || !selectedId) return;
    const controller = new AbortController();
    const sessionId = selectedId;
    const workspaceGeneration = activeWorkspaceEpoch;
    botsApi.list(controller.signal).then(
      async (answer) => {
        if (controller.signal.aborted || activeWorkspaceGeneration.current !== workspaceGeneration)
          return;
        const bot = answer.bots.find((row) => row.chat_session_id === sessionId);
        if (bot) {
          const role =
            teamRoster(answer.bots, bot.workspace_id).seats.find(
              (seat) => seat.bot?.bot_id === bot.bot_id,
            )?.seat.role ?? null;
          setBotChat({
            sessionId,
            botId: bot.bot_id,
            bot,
            main: true,
            role,
            workspaceGeneration,
          });
          markBotSession(sessionId, bot.profile_id);
          return;
        }
        if (!profileLaunchApi) return;
        try {
          const binding = await profileLaunchApi.inspect(sessionId, controller.signal);
          if (
            controller.signal.aborted ||
            activeWorkspaceGeneration.current !== workspaceGeneration
          )
            return;
          const owner = answer.bots.find(
            (row) =>
              row.profile_id === binding.profile_id && row.workspace_id === binding.workspace_id,
          );
          if (owner && botsApi.threads) {
            const threads = await botsApi.threads(owner.bot_id, controller.signal);
            // The listing stops at the Bot's newest threads, so past it only the chat's own
            // memory binding can name its Bot. A binding to another Bot, a removed one included,
            // never hands the chat to this one. The listing leaves out the Bot's routine runs,
            // which carry the same binding, so past it the binding must say it is not one.
            const listed =
              threads.threads.some((thread) => thread.session_id === sessionId) ||
              (threads.has_more &&
                binding.bot_memory?.bot_id === owner.bot_id &&
                binding.bot_memory.routine === false);
            if (
              !controller.signal.aborted &&
              activeWorkspaceGeneration.current === workspaceGeneration &&
              listed
            ) {
              const role =
                teamRoster(answer.bots, owner.workspace_id).seats.find(
                  (seat) => seat.bot?.bot_id === owner.bot_id,
                )?.seat.role ?? null;
              setBotChat({
                sessionId,
                botId: owner.bot_id,
                bot: owner,
                main: false,
                role,
                workspaceGeneration,
              });
              markBotSession(sessionId, owner.profile_id);
            }
          }
        } catch {
          /* A plain chat or unavailable binding has no Bot header. */
        }
      },
      () => undefined,
    );
    return () => controller.abort();
  }, [
    botsApi,
    botsAvailable,
    profileLaunchApi,
    selectedId,
    nav.view,
    activeWorkspaceEpoch,
    markBotSession,
  ]);
  const handoffBlocked =
    handoffActive &&
    (launchHandoff.kind === "invalid" ||
      !selectedView?.synced ||
      Boolean(selectedView.openRefused));
  // The launch target is admission-checked by the existing-only subscription. No restored
  // selection or freshly minted editor substitutes for an unavailable target.
  // biome-ignore lint/correctness/useExhaustiveDependencies: Consume this immutable launch target once, using the first render's admission check.
  useEffect(() => {
    if (handoffOpened.current || launchHandoff.kind !== "target") return;
    handoffOpened.current = true;
    if (!allowEditor(launchHandoff.sessionId)) return;
    restorationVisits.current.add(launchHandoff.sessionId);
    viki.actions.openExistingSession(launchHandoff.sessionId);
    explicitVisit.current = {
      id: launchHandoff.sessionId,
      requestId: viki.store.getSnapshot().sessions[launchHandoff.sessionId]?.requestId ?? null,
    };
  }, [launchHandoff, viki]);
  useEffect(() => {
    if (handoffRecorded.current || !handoffActive || launchHandoff.kind !== "target") return;
    const target = appSnapshot.sessions[launchHandoff.sessionId];
    if (selectedIdState !== launchHandoff.sessionId || !target?.synced || target.openRefused)
      return;
    // Record normal navigation only after admission, without opening the target again.
    handoffRecorded.current = true;
    selectionRef.current = launchHandoff.sessionId;
    rememberSelection(launchHandoff.sessionId);
    updatePanes((l) => openChatIn(l, launchHandoff.sessionId));
  }, [appSnapshot, selectedIdState, launchHandoff, handoffActive, updatePanes]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: A deliberate visit to the already selected, synced row must also run this check.
  useEffect(() => {
    const visit = explicitVisit.current;
    if (!visit || visit.id !== selectedIdState) return;
    const current = appSnapshot.sessions[visit.id];
    if (visit.requestId === null && current?.requestId) visit.requestId = current.requestId;
    if (current?.openRefused) {
      explicitVisit.current = null;
      return;
    }
    if (
      !current?.synced ||
      current.requestId !== visit.requestId ||
      metadata.loading ||
      metadata.pending.has(visit.id)
    )
      return;
    explicitVisit.current = null;
    if (metadata.sessions.some((row) => row.session_id === visit.id && row.unread))
      void metadata.update(visit.id, { unread: false });
  }, [
    appSnapshot,
    selectedIdState,
    visitSerial,
    metadata.loading,
    metadata.pending,
    metadata.sessions,
    metadata.update,
  ]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: openTools reads only the stable exportTools ref and setToolsOpen; taking it as a dependency would re-run this effect on every render.
  useEffect(() => {
    if (!exportSessionId || exportSessionId !== selectedId) return;
    openTools("panel");
    setExportSessionId(null);
  }, [exportSessionId, selectedId]);
  selectionRef.current = selectedId;

  // The token this window was launched with is dead and no reconnect will revive it. Everything
  // that could act on the server is taken off the screen rather than left to look usable: the
  // one stale card stands in the main column.
  const staleWindow = appSnapshot.status === "unauthorized";
  // Keep the stored tree while Shell measures whether its panes and both docked rails fit.
  const multiPanes =
    Boolean(organization) &&
    panes.layout.root.kind === "split" &&
    layout.rightDocked &&
    !staleWindow &&
    !archiveOpen;
  const [paneModeVisible, setPaneModeVisible] = useState(true);
  const visibleMultiPanes = multiPanes && paneModeVisible;
  useEffect(() => {
    // A drawer whose rail has just docked has nothing left to show: close it rather than
    // leave a modal over a rail that is now on screen.
    if (drawer === "left" && layout.leftDocked) setDrawer(null);
    if (drawer === "right" && layout.rightDocked) setDrawer(null);
  }, [drawer, layout]);
  // The pill's own Jump, for a right rail already docked: the list it names is already on
  // screen, so it is scrolled to and focused instead of opening a redundant drawer. Skipped at
  // mount (runsFocus starts at 0) so nothing steals focus before the user ever clicks it.
  useEffect(() => {
    if (runsFocus === 0) return;
    agentsList.current?.scrollIntoView?.({ block: "nearest" });
    agentsList.current?.focus({ preventScroll: true });
  }, [runsFocus]);
  // While one pane shows every tab, a few older routes could still leave a
  // hidden file pane focused (Ctrl+1..4 is refused below; "Move to pane N" and a window
  // narrowing while a file pane had the focus are not) — Alt+W and Ctrl+\ would then act on
  // that hidden tab instead of the chat on screen. `focusGroup` only changes `focused` (and an
  // unset `active`), neither of which `structureOf` sees, so fixing it here never saves.
  useEffect(() => {
    if (nav.view !== "chat" || shellSnapshot.recovery || visibleMultiPanes) return;
    const focused = focusedGroup(panes.layout);
    if (kindOf(focused) === "file") updatePanes((l) => focusGroup(l, l.focusedChat ?? l.focused));
  }, [nav.view, shellSnapshot.recovery, visibleMultiPanes, panes.layout, updatePanes]);

  // A restored or adopted layout can name chats this window does not hold: each chat pane's
  // active chat opens existing-only, quietly (`quietOpens`). Its file tabs wait for the folder
  // check below (`uncheckedFiles`) and close quietly when their first read finds the file gone
  // (`quietFiles`). With nothing selected and no remembered selection to wait for, the focused
  // chat pane's chat is selected the way the effect below restores one: not a visit, so its
  // unread mark stays.
  // File admission runs even while session labels are unavailable.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only a restored layout adds file tabs for admission here.
  useEffect(() => {
    for (const [id, file] of Object.entries(panes.layout.files)) {
      uncheckedFiles.current.set(id, file);
      quietFiles.current.set(id, file);
    }
  }, [panes.restored]);
  useEffect(() => {
    if (panes.restored === restoredSeen.current || !archiveMetadataReady) return;
    restoredSeen.current = panes.restored;
    const dropped = paneGroups(panes.layout).flatMap((group) =>
      group.tabs.filter(
        (id) =>
          !fileOf(panes.layout.files, id) &&
          archivedIds.has(id) &&
          !restorationVisits.current.has(id),
      ),
    );
    const newChat =
      selectedIdState === null && visitSerial > 0 && kindOf(focusedGroup(panes.layout)) === "chat";
    const retained = dropped.reduce(closeTab, panes.layout);
    const restoredLayout = newChat ? openChatIn(retained, null) : retained;
    if (dropped.length || newChat)
      updatePanes((l) => {
        const next = dropped.reduce(closeTab, l);
        return newChat ? openChatIn(next, null) : next;
      });
    restorationVisits.current.clear();
    if (restoreRef.current && archivedIds.has(restoreRef.current)) {
      restoreRef.current = null;
      rememberSelection(null);
    }
    const held = viki.store.getSnapshot().sessions;
    for (const group of paneGroups(restoredLayout)) {
      const { active } = group;
      if (
        kindOf(group) === "file" ||
        active === null ||
        held[active] ||
        archivingNow.current.has(active)
      )
        continue;
      quietOpens.current.add(active);
      viki.actions.openExistingSession(active);
    }
    const chatPane = paneGroups(restoredLayout).find(
      (group) => group.id === restoredLayout.focusedChat,
    );
    const focused = chatPane?.active ?? null;
    if (focused === null || selectedIdState !== null || restoreRef.current || visitSerial > 0)
      return;
    currentCodeRequest.current = null;
    setCodeRequest(null);
    setSelectedIdState(focused);
  }, [
    panes.restored,
    panes.layout,
    viki,
    selectedIdState,
    visitSerial,
    archiveMetadataReady,
    archivedIds,
    updatePanes,
  ]);
  // A restored file tab drops once this window's code folder listing is in, unless its workspace
  // is listed there as a code folder with its root available; at once without a code client or a
  // shell, which list no folder. Until then its pane reads nothing (`paneRequest`).
  // biome-ignore lint/correctness/useExhaustiveDependencies: a restore (panes.restored) adds the tabs to check.
  useEffect(() => {
    const unchecked = [...uncheckedFiles.current];
    if (unchecked.length === 0) return;
    const listed = Boolean(codeApi && shell);
    if (listed && shellSnapshot.workspacesStatus !== "ready") return;
    uncheckedFiles.current.clear();
    const admitted = new Set(
      (listed ? (shellSnapshot.workspaces?.workspaces ?? []) : [])
        .filter((folder) => folder.kind === "code" && folder.root_available)
        .map((folder) => folder.workspace_id),
    );
    const files = newestPanes.current.files;
    const dropped = unchecked
      .filter(([id, file]) => {
        const now = fileOf(files, id);
        return (
          now?.workspaceId === file.workspaceId &&
          now.ref === file.ref &&
          !admitted.has(file.workspaceId)
        );
      })
      .map(([id]) => id);
    for (const id of dropped) quietFiles.current.delete(id);
    updatePanes((l) => dropped.reduce(closeTab, l));
  }, [
    panes.restored,
    codeApi,
    shell,
    shellSnapshot.workspaces,
    shellSnapshot.workspacesStatus,
    updatePanes,
  ]);
  // A quiet open the server refuses drops its tab; one that synchronizes is an ordinary chat.
  // biome-ignore lint/correctness/useExhaustiveDependencies: closeChatTab reads the live selection ref; only a view change can carry a refusal.
  useEffect(() => {
    for (const id of quietOpens.current) {
      const view = appSnapshot.sessions[id];
      if (view?.synced) quietOpens.current.delete(id);
      else if (view?.openRefused) {
        quietOpens.current.delete(id);
        closeChatTab(id);
      }
    }
  }, [appSnapshot.sessions]);
  // A tab drops silently once a complete listing shows its chat is gone: not held here, not
  // listed and not live. A partial or failed listing proves nothing, so nothing drops then.
  // Only a drop touches the layout, so a pane left with no active tab keeps none. File tabs are
  // not chats, so this keeps them.
  useEffect(() => {
    if (restSnapshot.sessionsStatus !== "ready") return;
    const keep = new Set([
      ...Object.keys(appSnapshot.sessions),
      ...restSnapshot.sessions.map((row) => row.session_id),
      ...(appSnapshot.live?.ids ?? []),
    ]);
    updatePanes((l) =>
      paneGroups(l).every((group) => group.tabs.every((id) => keep.has(id) || fileOf(l.files, id)))
        ? l
        : retainTabs(l, [...keep, ...Object.keys(l.files)]),
    );
  }, [
    restSnapshot.sessionsStatus,
    restSnapshot.sessions,
    appSnapshot.sessions,
    appSnapshot.live,
    updatePanes,
  ]);

  useEffect(() => {
    // The session the user was looking at only appears once the reconnect has reopened it
    // (vault.sessions -> openSession), several frames after mount, so this waits for it rather
    // than reading the snapshot once. A selection the user makes in the meantime wins outright.
    if (selectedIdState !== null) {
      restoreRef.current = null;
      // The selected session going away (a close, here or elsewhere) is the one case where the
      // remembered id is worse than nothing: a later reload would chase a session that no longer
      // exists and land on the empty state anyway.
      if (!appSnapshot.sessions[selectedIdState]) rememberSelection(null);
      return;
    }
    if (!archiveMetadataReady) return;
    const wanted = restoreRef.current;
    if (wanted && archivedIds.has(wanted)) {
      restoreRef.current = null;
      rememberSelection(null);
      return;
    }
    if (wanted !== null && wanted !== undefined && appSnapshot.sessions[wanted]) {
      restoreRef.current = null;
      currentCodeRequest.current = null;
      setCodeRequest(null);
      setSelectedIdState(wanted);
      updatePanes((l) => openChatIn(l, wanted));
    }
  }, [appSnapshot, selectedIdState, updatePanes, archiveMetadataReady, archivedIds]);

  const now = Date.now();
  const online = appSnapshot.status === "open";
  // TWO sets, because the rail and the cap are asking different questions and sharing one
  // answer was the defect. `heldIds` is every session this window has open, which is what the
  // rail's own "live" chip means — the app is holding
  // this conversation, whether or not a process is attached. `liveIds` is the narrower, honest
  // set: the ones with a claude process. The cap, the toast and Wrap up all mean that one.
  const heldIds = new Set(Object.keys(appSnapshot.sessions));
  const botChatIds = new Set(botSessions.keys());
  for (const row of restSnapshot.sessions) if (row.bot) botChatIds.add(row.session_id);
  for (const thread of botThreadSnapshot.threads) {
    for (const member of thread.members) if (member.sessionId) botChatIds.add(member.sessionId);
    for (const message of thread.messages) if (message.sessionId) botChatIds.add(message.sessionId);
  }
  const sidebarSessions = restSnapshot.sessions.filter((row) => !botChatIds.has(row.session_id));
  // True while the last settled listing read was `ready` (a `loading` commit never writes this
  // ref): every later read — the poll, a turn's end, a vault.sessions frame — flips
  // sessionsStatus to `loading` first, so without this the section and the line below it would
  // blink out and back on every one of them, even though the rows on screen are still the last
  // ready, complete answer. This is what tells a still-unanswered first read apart from a
  // refresh of good data.
  const lastListingReady = useRef(false);
  const listingReady =
    restSnapshot.sessionsStatus === "ready" ||
    (restSnapshot.sessionsStatus === "loading" && lastListingReady.current);
  if (restSnapshot.sessionsStatus !== "loading") {
    lastListingReady.current = restSnapshot.sessionsStatus === "ready";
  }
  // The missing-source rail's own held set: `heldIds` alone misses a session that only this
  // window's own copy of the last `vault.sessions` report still names live. The server already
  // drops the session's driver at the close, before it emits SessionClosed (session.py), and
  // the hub re-sends only when its list actually changes (server.py's `_VaultHub.update`), so
  // that stale copy, a session live again in another window this one lost track of, and a
  // listing that lags can each still need this held set to avoid a spurious record.
  const missingHeld = new Set(heldIds);
  for (const id of appSnapshot.live?.ids ?? []) missingHeld.add(id);
  const missing = missingRecords({
    rows: sidebarSessions,
    metadata: metadata.sessions.filter((row) => !botChatIds.has(row.session_id)),
    held: missingHeld,
    complete: listingReady && !metadata.loading && metadata.error === null,
  });
  const liveIds = liveSessionIds(appSnapshot, startedHereRef.current);
  const maxLiveSessions = maxLiveFrom(shellSnapshot.settings, appSnapshot.maxLiveSessions);
  // Tells a missing record apart from an actually-gone transcript: a record whose session was
  // only ever prepared for a profile launch still answers `inspect` at 200 with `started`
  // false. A started one answers 200 too, but with its transcript gone it can never reopen. A
  // 404 `not_found` says no profile session has the id (server.py's `inspect_profile_session`),
  // so it is neither a draft nor a Bot chat. Two budgets per page load: labelled records get
  // MAX_MISSING_INSPECT inspects; archive-only ones (`isArchiveOnly`) get
  // MAX_ARCHIVE_ONLY_CLEARS, queued after them, and each is deleted quietly only on that 404
  // while the latest render still has its id in `missing` (`missingIdsNow`). Any other answer,
  // a failed delete, or no budget left keeps it (`missingKept`): listed like any other record.
  // Each id is queued at most once in this page load (a failed read is final too), one at a
  // time: the profile routes share a small limiter that answers a burst with 429.
  const missingInspectedIds = useRef<Set<string>>(new Set());
  const missingClearIds = useRef<Set<string>>(new Set());
  const missingKeptIds = useRef<Set<string>>(new Set());
  const missingInspectRef = useRef<{
    signal: AbortSignal;
    queue: string[];
    running: boolean;
  } | null>(null);
  // Written on every render, as `lastListingReady` is: an answer that arrives renders later
  // checks the id against the newest `missing`, not the one its read started from.
  const missingIdsNow = useRef<ReadonlySet<string>>(new Set());
  missingIdsNow.current = new Set(missing.map((record) => record.session_id));
  const [missingDrafts, setMissingDrafts] = useState<ReadonlySet<string>>(new Set());
  const [missingKept, setMissingKept] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    const controller = new AbortController();
    missingInspectRef.current = { signal: controller.signal, queue: [], running: false };
    missingInspectedIds.current = new Set();
    missingClearIds.current = new Set();
    missingKeptIds.current = new Set();
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const run = missingInspectRef.current;
    if (!profileLaunchApi || !run) return;
    for (const record of missing) {
      const id = record.session_id;
      if (isArchiveOnly(record) || missingInspectedIds.current.has(id)) continue;
      if (missingInspectedIds.current.size >= MAX_MISSING_INSPECT) continue;
      missingInspectedIds.current.add(id);
      run.queue.push(id);
    }
    const kept: string[] = [];
    for (const record of missing) {
      const id = record.session_id;
      if (
        !isArchiveOnly(record) ||
        missingClearIds.current.has(id) ||
        missingKeptIds.current.has(id)
      )
        continue;
      // Past its budget, or already read as a labelled record (an Unpin since left only the
      // Archive mark): kept, not read again. missingKeptIds remembers it too, so a kept id is
      // dispatched to setMissingKept once, not on every commit this page load.
      if (
        missingClearIds.current.size >= MAX_ARCHIVE_ONLY_CLEARS ||
        missingInspectedIds.current.has(id)
      ) {
        kept.push(id);
        missingKeptIds.current.add(id);
        continue;
      }
      missingClearIds.current.add(id);
      run.queue.push(id);
    }
    if (kept.length > 0)
      setMissingKept((prev) => {
        const fresh = kept.filter((id) => !prev.has(id));
        return fresh.length === 0 ? prev : new Set([...prev, ...fresh]);
      });
    if (run.running) return;
    run.running = true;
    void (async () => {
      while (!run.signal.aborted) {
        const id = run.queue.shift();
        if (id === undefined) break;
        let notFound = false;
        try {
          const answer = await profileLaunchApi.inspect(id, run.signal);
          if (answer.bot_memory && !run.signal.aborted) markBotSession(id, answer.profile_id);
          if (answer.started === false && !run.signal.aborted)
            setMissingDrafts((prev) => new Set(prev).add(id));
        } catch (cause) {
          // Any failure: not a draft. Only a 404 not_found also says no profile session has it.
          notFound =
            cause instanceof ProfileLaunchApiError &&
            cause.status === 404 &&
            cause.code === "not_found";
        }
        if (!missingClearIds.current.has(id)) continue;
        const cleared =
          notFound &&
          !run.signal.aborted &&
          missingIdsNow.current.has(id) &&
          (await metadata.clearArchiveOnly(id));
        if (!cleared && !run.signal.aborted) setMissingKept((prev) => new Set(prev).add(id));
      }
      run.running = false;
    })();
  }, [missing, profileLaunchApi, markBotSession, metadata.clearArchiveOnly]);
  // The rail's own list: an archive-only record shows only once kept or a confirmed draft, so
  // one still waiting for its answer never flashes up and a cleared one never shows. Without
  // profileLaunchApi nothing is read or cleared, and every record shows.
  const missingListed = profileLaunchApi
    ? missing.filter(
        (record) =>
          !isArchiveOnly(record) ||
          missingDrafts.has(record.session_id) ||
          missingKept.has(record.session_id),
      )
    : missing;
  const agents = rails?.agents ?? [];
  const running = rosterRunning(agents, runningAgents(appSnapshot));
  const agentsCount = hereNowLabel(online, running);
  // The chip count includes only rows the listing actually holds, as "Other (N)" does below.
  const archivedCount = sidebarSessions.filter((row) => archivedIds.has(row.session_id)).length;
  const { groups, other } = organization
    ? organizedSessions(sidebarSessions, metadata.sessions, now, showArchived)
    : groupSessions(sidebarSessions, now);

  const selectedRow = selectedId
    ? restSnapshot.sessions.find((row) => row.session_id === selectedId)
    : undefined;
  const headerTitle = selectedId
    ? sessionLabel(selectedId, restSnapshot.sessions, metadata.sessions)
    : "Untitled session";
  const sessionMetadata = selectedId
    ? (metadata.sessions.find((row) => row.session_id === selectedId) ?? {
        session_id: selectedId,
        revision: 0,
        title: "",
        pinned: false,
        archived: false,
      })
    : null;
  // VIKI's own presence report never gets an untitled chat's first message as its "title": send
  // the user's own rename (sessionMetadata.title, set only by the rename dialog's Save) or a
  // real custom/AI title (selectedRow.title when titleIsPrompt says it is not just the server's
  // first-prompt fallback), else null. The chat header and session list are untouched by this —
  // they still show sessionLabel's effective title either way.
  const presenceChatTitle =
    sessionMetadata?.title ||
    (selectedRow && !titleIsPrompt(selectedRow) ? selectedRow.title : null);
  const renameSession = renameSessionId
    ? (metadata.sessions.find((row) => row.session_id === renameSessionId) ?? {
        session_id: renameSessionId,
        revision: 0,
        title: sessionLabel(renameSessionId, restSnapshot.sessions, metadata.sessions),
        pinned: false,
        archived: false,
      })
    : null;
  // This window's registered code folders: the only folders a file tab reads through.
  const codeFolders = (shellSnapshot.workspaces?.workspaces ?? []).filter(
    (folder) => folder.kind === "code",
  );
  /** A middle-preview request for `file` through its registered code folder with its root
   * available; null without a code client or such a folder. */
  function codeRequestFor(file: { workspaceId: string; ref: string }): CodeOpenRequest | null {
    const folder = codeFolders.find(
      (row) => row.workspace_id === file.workspaceId && row.root_available,
    );
    return codeApi && folder
      ? {
          api: codeApi,
          folder: { ...folder },
          folderKey: codeFolderKey(folder),
          path: file.ref,
          withDiff: false,
        }
      : null;
  }
  const tabRow = (id: string) => ({
    session_id: id,
    revision: 0,
    pinned: false,
    archived: false,
    title: sessionLabel(id, restSnapshot.sessions, metadata.sessions),
  });
  // Every tab in tree order, whether or not this window holds its chat: a tab whose chat it
  // does not hold opens existing-only when chosen.
  const tabs = paneGroups(panes.layout)
    .flatMap((group) => group.tabs)
    .map(tabRow);
  // Running work's rows: every session the vault's last report says has a claude process. A
  // prepared, unstarted session has none yet, so it is absent until a later report lists it.
  const runningRows: RunningRow[] = Array.from(appSnapshot.live?.ids ?? []).map((id) => {
    const runningView = appSnapshot.sessions[id];
    return {
      sessionId: id,
      title: sessionLabel(id, restSnapshot.sessions, metadata.sessions),
      busy: runningView ? presence(runningView) !== "idle" : false,
      since: runningView ? firstUserAt(runningView) : "",
    };
  });
  /** A tab's chat: selected when this window holds it, else opened existing-only. A file tab
   * shows its file instead (`showFile`). */
  function showChat(id: string): void {
    if (fileOf(newestPanes.current.files, id)) showFile(id);
    else if (viki.store.getSnapshot().sessions[id]) select(id);
    else openSession(id, true);
  }
  /** A file tab chosen: active in its pane while the panes sit side by side. Otherwise one pane
   * shows the chat, with every tab in its strip: the file opens in the middle preview and the
   * layout's focus stays on that chat's pane, so Alt+W and Ctrl+\ act on the chat. */
  function showFile(id: string): void {
    if (visibleMultiPanes) {
      updatePanes((l) => openTab(l, id));
      return;
    }
    const file = fileOf(newestPanes.current.files, id);
    const request = file ? codeRequestFor(file) : null;
    if (request) openCodeRequest(request);
  }
  /** Puts chat `id` in pane `groupId` at `zone` (in the centre, before tab `before`) and shows
   * it. The id can come from a drag payload, so only a canonical one this window can give an
   * editor is placed; a chat this window does not hold opens existing-only, quietly. */
  function placeChat(groupId: string, id: string, zone: Zone, before?: string): void {
    if (archivingNow.current.has(id) || !UUID.test(id) || !allowEditor(id)) return;
    updatePanes((l) => moveTab(l, id, { group: groupId, zone, ...(before ? { before } : {}) }));
    if (viki.store.getSnapshot().sessions[id]) select(id);
    else {
      quietOpens.current.add(id);
      openSession(id, true);
    }
  }
  /** A tab dropped on pane `groupId`'s strip or moved by its menu: a chat as `placeChat` places
   * it; one of this window's file tabs moved there (a centre drop on a chat pane splits right, as
   * `moveTab` does). */
  function placeTab(groupId: string, id: string, zone: Zone, before?: string): void {
    if (!fileOf(newestPanes.current.files, id)) {
      placeChat(groupId, id, zone, before);
      return;
    }
    updatePanes((l) => moveTab(l, id, { group: groupId, zone, ...(before ? { before } : {}) }));
  }
  /** A drop on pane `groupId` (Shell's drop zones): a chat as `placeChat` places it; one of this
   * window's file tabs moved there while the panes sit side by side (a centre drop on a chat pane
   * splits right, as `moveTab` does); a file of one of this window's code folders as a new tab
   * there (`withFileTab`), or in the middle preview when no pane would show it. Any other file
   * tab id, and a file of any other folder, is ignored. */
  function dropOnPane(groupId: string, payload: DropPayload, zone: Zone): void {
    if (payload.kind === "chat") {
      placeChat(groupId, payload.id, zone);
      return;
    }
    if (payload.kind === "fileTab") {
      // The id comes from a drag payload: only an own key of this window's file tabs moves. One
      // pane shows the chat alone, where a move would only reshape panes nobody sees.
      if (visibleMultiPanes && fileOf(newestPanes.current.files, payload.id))
        updatePanes((l) => moveTab(l, payload.id, { group: groupId, zone }));
      return;
    }
    const file = { workspaceId: payload.workspaceId, ref: payload.ref };
    const request = codeRequestFor(file);
    if (!request) return;
    const before = newestPanes.current;
    let placed = false;
    updatePanes((l) => {
      const next =
        layout.rightDocked && (l.root.kind === "group" || visibleMultiPanes)
          ? withFileTab(l, file, groupId, zone)
          : l;
      // One pane shows the chat alone, so a layout of one (file) pane would hide the file.
      placed = next !== l && next.root.kind === "split";
      return placed ? next : l;
    });
    focusOpenedFile(before);
    if (!placed) openCodeRequest(request);
  }
  /** The strip's drag and Ctrl+Shift+Arrow: before `before` in the pane holding it when that
   * pane holds the same kind of tab, else in the pane holding `id`, else in the focused chat
   * pane. */
  function moveChatTab(id: string, before: string | null): void {
    const l = panes.layout;
    const target = before === null ? undefined : groupOf(l, before);
    const beside =
      target && (kindOf(target) === "file") === Boolean(fileOf(l.files, id)) ? target : undefined;
    const group = beside ?? groupOf(l, id);
    placeTab(
      group?.id ?? l.focusedChat ?? l.focused,
      id,
      "center",
      beside ? (before ?? undefined) : undefined,
    );
  }
  /** Closing a tab never closes its chat. Closing the selected chat's tab shows the focused chat
   * pane's new active tab instead, or nothing when that pane has none. */
  function closeChatTab(id: string): void {
    const next = updatePanes((l) => closeTab(l, id));
    if (selectionRef.current !== id && selectedIdState !== id) return;
    if (kindOf(focusedGroup(next)) === "file") return;
    const active = paneGroups(next).find((group) => group.id === next.focusedChat)?.active ?? null;
    if (active === null || archivingNow.current.has(active)) select(null);
    else showChat(active);
  }
  const currentCloseChatTab = useRef(closeChatTab);
  currentCloseChatTab.current = closeChatTab;
  /** Empty groups keep independent, app-memory drafts without starting provider sessions. */
  function emptyPaneEditor(groupId: string): string {
    if (
      groupId === (panes.layout.focusedChat ?? panes.layout.focused) &&
      selectedIdState === null
    ) {
      emptyPaneEditors.current.set(groupId, newEditorId);
      return newEditorId;
    }
    let key = emptyPaneEditors.current.get(groupId);
    if (!key) {
      key = crypto.randomUUID();
      emptyPaneEditors.current.set(groupId, key);
      mintedRef.current.add(key);
    }
    return key;
  }
  /** Activate before the original control's click, retaining both its node and exact draft. */
  function activatePane(groupId: string): void {
    if (groupId === newestPanes.current.focused) return;
    const group = paneGroups(newestPanes.current).find((candidate) => candidate.id === groupId);
    if (group?.active && archivingNow.current.has(group.active)) return;
    const emptyKey =
      group && kindOf(group) === "chat" && group.active === null ? emptyPaneEditor(groupId) : null;
    const targetKey =
      emptyKey ?? (group?.active === forkArmedFrom && forkEditorId ? forkEditorId : group?.active);
    if (group && kindOf(group) === "chat" && targetKey && !allowEditor(targetKey)) return;
    updatePanes((l) => {
      const next = focusGroup(l, groupId);
      return emptyKey ? clearActive(next) : next;
    });
    if (emptyKey) {
      setNewEditorId(emptyKey);
      handleUnboundSession(emptyKey);
      setForkArmedFrom(null);
      select(null);
    } else if (group && kindOf(group) === "chat" && group.active !== null) showChat(group.active);
  }
  // A new pane may open while the right rail docks and the layout has room for one more.
  const canSplit =
    layout.rightDocked &&
    (panes.layout.root.kind === "group" || visibleMultiPanes) &&
    paneGroups(panes.layout).length < MAX_PANES;
  /** Whether tab `id` can move into a new pane at `zone` of its own (a file tab: open a second
   * view of its file there, `splitFile`): a new pane may open, the split changes the layout (a
   * pane's only chat tab cannot split off itself), and there is room on that axis for two panes
   * of the tab's kind. While the layout holds one pane the right rail still docks over it, so its
   * box reads narrower than a split would actually leave it; `zone === "right"` then reads
   * Shell's own row budget off the pane's box (`data-row-room`, the width multi mode would give
   * it) instead of the box's own width, optimistic like Shell's own drop zones until that budget
   * is actually measured. `zone === "bottom"`, and `"right"` once there are two or more panes,
   * still read the box itself. Measures the page, so an event or an open tab menu calls it, never
   * a render. */
  function canSplitTab(id: string, zone: "right" | "bottom"): boolean {
    const l = newestPanes.current;
    const own = groupOf(l, id);
    if (!canSplit || !own) return false;
    const file = kindOf(own) === "file";
    const floor = file ? FILE_PANE_MIN : PANE_MIN;
    const box = document.querySelector(`[data-pane="${own.id}"]`);
    const rect = box?.getBoundingClientRect();
    let rowOk: boolean;
    if (paneGroups(l).length === 1) {
      const rowRoom = box?.getAttribute("data-row-room") ?? null;
      rowOk = rowRoom === null || Number(rowRoom) >= 2 * floor.width;
    } else {
      rowOk = (rect?.width ?? 0) >= 2 * floor.width;
    }
    const room = zone === "right" ? rowOk : (rect?.height ?? 0) >= 2 * floor.height;
    const split = file ? splitFile(l, id, zone) : moveTab(l, id, { group: own.id, zone });
    return room && split !== l;
  }
  function splitTab(id: string, zone: "right" | "bottom"): boolean {
    const own = groupOf(newestPanes.current, id);
    if (!own || !canSplitTab(id, zone)) return false;
    if (kindOf(own) === "file") {
      const before = newestPanes.current;
      updatePanes((l) => splitFile(l, id, zone));
      focusOpenedFile(before);
    } else placeChat(own.id, id, zone);
    return true;
  }
  /** Closes every tab in pane `groupId`; their chats keep running. The selected chat's tab
   * goes last, so the focus leaves the pane once, for the chat it lands on. */
  function closePane(groupId: string): void {
    const closing = paneGroups(newestPanes.current).find((group) => group.id === groupId);
    const selected = selectionRef.current;
    for (const tab of closing?.tabs ?? []) if (tab !== selected) closeChatTab(tab);
    if (selected !== null && closing?.tabs.includes(selected)) closeChatTab(selected);
  }
  /** A tab's actions: split it right or down, move it to another pane holding its kind of tab,
   * close its pane, or open a chat in a new window. */
  function tabMenu(id: string): TabMenuItem[] {
    const all = paneGroups(panes.layout);
    const own = all.find((group) => group.tabs.includes(id));
    if (!own) return [];
    const items: TabMenuItem[] = [
      {
        label: "Split right",
        disabled: () => !canSplitTab(id, "right"),
        onSelect: () => splitTab(id, "right"),
      },
      {
        label: "Split down",
        disabled: () => !canSplitTab(id, "bottom"),
        onSelect: () => splitTab(id, "bottom"),
      },
    ];
    all.forEach((group, index) => {
      if (group !== own && kindOf(group) === kindOf(own))
        items.push({
          label: `Move to pane ${index + 1}`,
          onSelect: () => placeTab(group.id, id, "center"),
        });
    });
    if (all.length > 1) items.push({ label: "Close pane", onSelect: () => closePane(own.id) });
    if (kindOf(own) === "chat" && sessionWindowsApi && !recovery && !staleWindow)
      items.push({
        label: windowPending.has(id) ? "Opening window…" : "New window",
        disabled: () => windowPending.has(id),
        onSelect: () => {
          setTabWindowNote(null);
          void openSessionWindow(id).then((message) => {
            if (message) setTabWindowNote(message);
          });
        },
      });
    return items;
  }
  paneKeyRef.current = (key) => {
    // The panes show only in the chat, as the strip does.
    if (!organization || view !== "chat") return false;
    const l = newestPanes.current;
    if (key === "split") {
      // A focused file pane splits its file; otherwise the selected chat splits.
      const focused = focusedGroup(l);
      const tab = kindOf(focused) === "file" ? focused.active : selectionRef.current;
      return tab !== null && splitTab(tab, "right");
    }
    if (key === "close") {
      const active = focusedGroup(l).active;
      if (active === null || l.pinned.includes(active)) return false;
      closeChatTab(active);
      return true;
    }
    // Ctrl+N: pane N in tree order takes the focus and shows its chat, and the keyboard goes
    // to that chat's tab. While one pane shows every tab, a file pane is not a real target: it
    // would only focus a pane nobody can see.
    const target = paneGroups(l)[key - 1];
    if (!target || (!visibleMultiPanes && kindOf(target) === "file")) return false;
    activatePane(target.id);
    const active = focusedGroup(newestPanes.current).active;
    if (active !== null) document.getElementById(`session-tab-${active}`)?.focus();
    return true;
  };
  selectTabAtRef.current = (position) => {
    // The strip shows only in the chat, so Alt+digit has no tab to choose anywhere else.
    if (!organization || view !== "chat") return false;
    // With panes side by side, the digits count the focused pane's own tabs.
    const shown = visibleMultiPanes
      ? focusedGroup(panes.layout).tabs
      : tabs.map((tab) => tab.session_id);
    const id = position === 9 ? shown.at(-1) : shown[position - 1];
    if (!id) return false;
    showChat(id);
    return true;
  };
  function quotedTurn(turn: Turn): string {
    const text = [
      turn.user === null ? undefined : withoutVoiceTag(turn.user.text),
      ...turn.blocks.flatMap((block) => (block.kind === "text" ? [block.text] : [])),
    ]
      .filter(Boolean)
      .join("\n\n");
    return quoteTurn({ sessionId: selectedId ?? "", turnId: turn.id }, text).text;
  }
  function quoteIntoDraft(turn: Turn): void {
    const current = editors.getSnapshot().editors[editorKey];
    const text = `${current?.text ? `${current.text}\n\n` : ""}${quotedTurn(turn)}\n\n`;
    editors.edit(editorKey, text, text.length, text.length);
    focusedMessageBox()?.focus();
  }
  /** `key` defaults to the chat on screen's own editor; a launcher draft_chat action passes the
   * target chat's id instead, since selecting that chat (showLauncherChat) only takes effect
   * next render, too late for this render's own `editorKey` to have moved onto it yet. */
  function appendToDraft(given: string, key: string = editorKey): void {
    const current = editors.getSnapshot().editors[key];
    const text = `${current?.text ? `${current.text}\n\n` : ""}${given}\n\n`;
    editors.edit(key, text, text.length, text.length);
    focusedMessageBox()?.focus();
  }

  const git =
    gitObservation.state === "not_repository"
      ? undefined
      : {
          branch: gitObservation.branch,
          dirty: gitObservation.state === "known" ? gitDirty : null,
          sha: gitObservation.head,
          refState: gitObservation.ref_state,
          checking: restSnapshot.railsStatus === "loading",
        };
  const lastCommit = gitObservation.head
    ? { sha: gitObservation.head, time: timeLabel(gitObservation.committed_at, now) }
    : undefined;
  const contextPercent =
    selectedView && selectedView.contextPercent !== null ? selectedView.contextPercent : undefined;
  // The rail's files section mounts after the selected chat reports context or touches a file;
  // "touches a file" means some group of the chatDigest ChatSources itself renders is non-empty,
  // under the same vault flag, memoised on the turns array and that flag so a render that changes
  // neither does not rescan every turn.
  const sourcesEnv = useSourcesEnv();
  const selectedInVault = Boolean(selectedView && sourcesEnv?.inVault(selectedView.sessionId));
  const selectedViewTurns = selectedView?.turns;
  const hasChatFiles = useMemo(() => {
    if (!selectedViewTurns) return false;
    return Object.values(chatDigest(selectedViewTurns, selectedInVault)).some(
      (group) => group.length > 0,
    );
  }, [selectedViewTurns, selectedInVault]);
  const cliVersion = selectedView?.info ? selectedView.info.cli_version : undefined;
  const notReady = notReadyNotice(shellSnapshot);
  let notice: string | undefined;
  if (appSnapshot.status === "unauthorized") notice = "not signed in · relaunch VIKI Agent";
  // Outside a recovery window, both reads start at mount before capabilities resolve the launch
  // route. Stopping the store later cannot prevent 404s and
  // cannot clear the ones already recorded — `error` would then read 404 for the rest of the
  // launch, pointing the user at their network while the recovery view beside it explains the
  // real fault. (Gating on "capabilities not yet known" instead would also cover the few hundred
  // ms before they arrive, at the price of hiding a genuine offline notice on every launch.)
  else if (restSnapshot.error !== null && !shellSnapshot.recovery) notice = "offline · retrying";
  // Claude Code signed out (while still able to start) or git missing: the parts no banner
  // already states.
  else if (notReady !== null) notice = notReady;
  // The newer-transcript notice. Both halves of the comparison are on the wire: catalog reads
  // each transcript's own `version` onto its row, and session.started carries the CLI that
  // actually answered. A transcript written by a newer Claude Code than the one we run may hold
  // entry shapes this build has never seen, and the decision about that is the user's — so it is
  // said, quietly, and nothing is refused.
  // Ordered last: not being signed in, and being offline, are both more urgent and both make
  // the freshness question moot.
  else if (cliVersion !== undefined && isNewerCli(selectedRow?.cli_version, cliVersion)) {
    notice = `newer transcript · written by ${selectedRow?.cli_version}`;
  }

  // The user's own name for their turns. A corrupt document, or an empty string, falls
  // back to Conversation's built-in "You" rather than showing a blank label.
  const storedName = shellSnapshot.settings?.effective["identity.display_name"];
  const displayName = typeof storedName === "string" ? storedName : "";

  // The app-wide identity every name site reads through IdentityContext: the top bar and the
  // rails always render with this one. A bound chat's own identity, when the session has one
  // pinned, is `chatIdentity` below.
  // This window's one active workspace: the id the inbox stamps on a native decision, the id a
  // proposal's own `origin.workspace_id` is compared against, and the vault every excerpt is read
  // from. Its name, never the id, is what a row shows.
  // The team's vault: undefined until the listing and capabilities are read, null unless the
  // active workspace is a persistent vault.
  const teamVaultId =
    !shellSnapshot.workspaces || !shellSnapshot.capabilities
      ? undefined
      : shellSnapshot.workspaces.chat_only === false &&
          shellSnapshot.workspaces.workspaces.some(
            (row) =>
              row.workspace_id === activeWorkspaceId && row.kind === "vault" && row.persistent,
          )
        ? activeWorkspaceId
        : null;
  const teamReadiness = useTeamReadiness(
    botsAvailable ? nativeAssetsApi : undefined,
    teamVaultId,
    teamCheck,
  );
  useEffect(() => {
    botThreads.setContext(teamVaultId ?? null, botsAvailable && typeof teamVaultId === "string");
    return () => botThreads.setContext(null, false);
  }, [botThreads, teamVaultId, botsAvailable]);
  useEffect(() => {
    botThreads.setVisible(nav.view === "bot_threads");
    return () => botThreads.setVisible(false);
  }, [botThreads, nav.view]);
  const teamLive: TeamLive = {
    online,
    sessions: appSnapshot.sessions,
    others: appSnapshot.others,
    routines: routinesStore ? routinesSnapshot : null,
    checker: !rails
      ? "unknown"
      : running.includes("checker")
        ? "running"
        : agents.some((agent) => agent.name === "checker")
          ? "listed"
          : "absent",
    sessionRows: restSnapshot.sessions,
    nowMs: now,
  };
  // Unstarted profile chats have no native/live listing for the ordinary reload sweep.
  // Restore the remembered, already-bound Bot chat or task thread through source admission.
  const rememberedArchived = Boolean(restoreRef.current && archivedIds.has(restoreRef.current));
  // biome-ignore lint/correctness/useExhaustiveDependencies: one attempt; live refs guard the user's navigation and selection
  useEffect(() => {
    const wanted = restoreRef.current;
    if (
      botRestoreAttempted.current ||
      !botsApi ||
      !botsAvailable ||
      !activeWorkspaceId ||
      shellSnapshot.workspaces?.active_id !== activeWorkspaceId ||
      appSnapshot.status !== "open" ||
      nav.view !== "chat" ||
      selectedIdState !== null ||
      !wanted ||
      !archiveMetadataReady ||
      rememberedArchived ||
      !UUID.test(wanted)
    )
      return;
    botRestoreAttempted.current = true;
    const controller = new AbortController();
    const admitted = () =>
      !controller.signal.aborted &&
      restoreRef.current === wanted &&
      selectionRef.current === null &&
      currentNavigation.current.view === "chat" &&
      shellStore.getSnapshot().workspaces?.active_id === activeWorkspaceId;
    void (async () => {
      try {
        const listing = await botsApi.list(controller.signal);
        if (!admitted()) return;
        const bot = listing.bots.find(
          (row) =>
            row.chat_session_id === wanted &&
            row.workspace_id === activeWorkspaceId &&
            row.chat.profile_revision !== null &&
            !row.chat.archived,
        );
        if (!bot) {
          if (!profileLaunchApi || !botsApi.threads) return;
          const preview = await profileLaunchApi.inspect(wanted, controller.signal);
          if (
            !admitted() ||
            preview.session_id !== wanted ||
            preview.workspace_id !== activeWorkspaceId ||
            !preview.launchable
          )
            return;
          const owner = listing.bots.find(
            (row) =>
              row.profile_id === preview.profile_id && row.workspace_id === activeWorkspaceId,
          );
          if (!owner) return;
          const threads = await botsApi.threads(owner.bot_id, controller.signal);
          if (
            !admitted() ||
            !threads.threads.some(
              (thread) =>
                thread.session_id === wanted &&
                thread.profile_revision === preview.profile_revision &&
                !thread.archived,
            )
          )
            return;
          handleProfileSession(wanted, preview.identity ?? null);
          markBotSession(wanted, owner.profile_id);
          openSession(wanted, true);
          return;
        }
        const answer = await botsApi.chat(bot, controller.signal);
        if (!admitted() || !("session_id" in answer.preview)) return;
        const preview = answer.preview;
        if (
          answer.bot_id !== bot.bot_id ||
          answer.state_revision !== bot.state_revision ||
          preview.session_id !== wanted ||
          preview.profile_id !== bot.profile_id ||
          preview.workspace_id !== activeWorkspaceId ||
          preview.profile_revision !== bot.chat.profile_revision
        )
          return;
        handleProfileSession(wanted, preview.identity ?? null);
        markBotSession(wanted, bot.profile_id);
        openSession(wanted, true);
      } catch {
        // Missing/blocked sources stay explicit; this page load does not retry or replace them.
      }
    })();
    return () => controller.abort();
  }, [
    botsApi,
    botsAvailable,
    profileLaunchApi,
    activeWorkspaceId,
    shellSnapshot.workspaces?.active_id,
    appSnapshot.status,
    nav.view,
    selectedIdState,
    archiveMetadataReady,
    rememberedArchived,
  ]);
  // The proposals behind Needs you and the Approvals count, read in the background: on
  // activation, on a workspace change, on a vault git change, and when Approvals closes
  // (showDecisions). A rails poll that changes nothing keeps the trigger, so it reads nothing.
  const attention = useProposalAttention({
    api: decisions,
    workspaceId: activeWorkspaceId,
    enabled:
      !shellSnapshot.recovery &&
      vaultSurfaces(shellSnapshot) &&
      shellSnapshot.workspaces?.chat_only !== true,
    trigger: [rails?.git?.head, rails?.git?.dirty].join("|"),
  });
  const workspaceName = shellSnapshot.capabilities?.workspace.active?.name ?? null;
  const codeActive = shellSnapshot.capabilities?.workspace.active?.kind === "code";
  // Folder labels include a session's additional folders, worktree or different primary folder.
  // Without a roots client, the rail, tabs and header draw no folder label.
  const folderOf = rootsApi
    ? (sessionId: string) =>
        folderLabel(sessionId, roots, shellSnapshot.workspaces?.workspaces ?? [], activeWorkspaceId)
    : undefined;
  // One object identity for as long as both clients live: `VaultBrowser` keys its listing,
  // bookmark and note reads on `api`, so a fresh object per render would re-read on every render.
  const vaultBrowserApi = useMemo(
    () => (vaultApi && excerpts ? { ...vaultApi, excerpt: excerpts.excerpt } : vaultApi),
    [vaultApi, excerpts],
  );
  const currentVaultReader = useRef(vaultBrowserApi ?? vaultApi);
  currentVaultReader.current = vaultBrowserApi ?? vaultApi;
  // The one Save gate: the vault view's Save, note actions, recovery panel and memory
  // panel, and the composer's keep-in-vault all read it, so no surface offers a write Save would
  // not. A protected vault takes only Propose, which writes no vault, so it has its own scope.
  const activeVault = shellSnapshot.workspaces?.workspaces.find(
    (item) => item.workspace_id === shellSnapshot.workspaces?.active_id,
  );
  const botMemoryReader = activeVault?.policy === "ordinary" ? vaultApi : appMemoryApi;
  const botMemoryApi =
    botMemoryReader &&
    !shellSnapshot.recovery &&
    !codeActive &&
    activeVault?.kind === "vault" &&
    shellSnapshot.capabilities?.workspace.active?.id === activeVault.workspace_id &&
    activeVault.persistent &&
    activeVault.root_available
      ? botMemoryReader
      : undefined;
  // The ref is the one the reader lists: `bot-memory/<bot>/<file>` in a vault, else `<bot>/<file>`.
  const botMemoryEditorOpener = (vault: { workspace_id: string; policy: string }) =>
    vault.policy === "ordinary"
      ? viki.actions.openVault
      : (ref: string) => viki.actions.openBotMemory(vault.workspace_id, ref);
  const botActivityMemory =
    botMemoryApi &&
    botHere &&
    selectedId &&
    botHere.sessionId === selectedId &&
    activeVault &&
    botHere.bot.workspace_id === activeVault.workspace_id &&
    botHere.bot.workspace?.active === true &&
    botHere.bot.workspace.policy === activeVault.policy &&
    (!botHere.main || botHere.bot.chat_session_id === selectedId)
      ? {
          api: botMemoryApi,
          sessionId: selectedId,
          botId: botHere.botId,
          workspaceId: activeVault.workspace_id,
          activeWorkspaceId: activeVault.workspace_id,
          workspacePolicy: activeVault.policy,
          ...(activeVault.policy === "ordinary"
            ? {
                onOpenInVault: (ref: string) => {
                  openVaultNote(ref);
                },
              }
            : {}),
          onOpenUrl: viki.actions.openUrl,
          onOpenInEditor: botMemoryEditorOpener(activeVault),
          // Appends to this chat's unsent draft; refuses when a send or conflict holds the draft.
          onDraftReplacement: async (request: string): Promise<boolean> => {
            const sessionId = selectedId;
            try {
              await editors.ensure(sessionId);
              if (selectionRef.current !== sessionId) return false;
              const draft = editors.getSnapshot().editors[sessionId];
              if ((draft?.status !== "saved" && draft?.status !== "saving") || draft.pendingCommand)
                return false;
              const text = `${draft.text}${draft.text ? "\n\n" : ""}${request}`;
              // The cap holds for the draft the request joins, not the request alone.
              if (!draftFits(text)) return false;
              editors.edit(sessionId, text);
              return true;
            } catch {
              return false;
            }
          },
        }
      : undefined;
  const selectedVaultId = shellSnapshot.workspaces?.selected_id;
  const personaNextLaunch =
    shellSnapshot.capabilities?.workspace.restart_required &&
    selectedVaultId &&
    selectedVaultId !== shellSnapshot.workspaces?.active_id
      ? shellSnapshot.workspaces?.workspaces.find((item) => item.workspace_id === selectedVaultId)
          ?.name
      : undefined;
  // ChatFoldersChip's own lookups: a registered workspace's root resolved to its display name,
  // and this chat's vault row (ADR-050's folders chip).
  const folderNames = new Map(
    (shellSnapshot.workspaces?.workspaces ?? []).map((row) => [row.root, row.name] as const),
  );
  const vaultRow = activeVault ? { name: activeVault.name, root: activeVault.root } : null;
  const writeScope =
    shellSnapshot.workspaces?.chat_only === false &&
    activeVault?.persistent &&
    activeVault.policy === "ordinary" &&
    activeVault.bootstrap === "complete" &&
    activeVault.root_available &&
    activeVault.root_physical_id
      ? {
          workspaceId: activeVault.workspace_id,
          policyRevision: activeVault.policy_revision,
          rootPhysicalId: activeVault.root_physical_id,
        }
      : null;
  // A missing vault folder prevents registration changes, but forgetting the registration needs
  // no folder; only the forget-only recovery panel follows this scope.
  const strandedScope =
    shellSnapshot.workspaces?.chat_only === false &&
    activeVault?.persistent &&
    activeVault.policy === "ordinary" &&
    activeVault.bootstrap === "complete" &&
    !activeVault.root_available;
  const protectedScope =
    activeVault?.policy === "protected" && activeVault.root_physical_id
      ? {
          policyRevision: activeVault.policy_revision,
          rootPhysicalId: activeVault.root_physical_id,
        }
      : null;
  const memoryScope = writeScope ?? protectedScope;

  const effectiveSettings = shellSnapshot.settings?.effective;
  const codeFontSize = effectiveSettings?.["workbench.code_font_size"];
  // One object identity while both clients live, so the Workbench's recovery list does not re-read
  // on every render.
  const codeRecovery = useMemo(
    () =>
      vaultApi && vaultOpsApi
        ? {
            list: (signal?: AbortSignal) => vaultApi.recovery(signal),
            forget: (id: string) => vaultOpsApi.resolve(id, "forget"),
          }
        : undefined,
    [vaultApi, vaultOpsApi],
  );
  const assistantNameValue = effectiveSettings?.["identity.assistant_name"];
  const assistantAvatarValue = effectiveSettings?.["identity.assistant_avatar_id"];
  const userAvatarValue = effectiveSettings?.["identity.user_avatar_id"];
  const quietStart = effectiveSettings?.["notifications.quiet_start"];
  const quietEnd = effectiveSettings?.["notifications.quiet_end"];
  const routineControls = {
    paused: effectiveSettings?.["routines.paused"] === true,
    notificationsEnabled: effectiveSettings?.["notifications.enabled"] === true,
    quietStart: typeof quietStart === "string" ? quietStart : null,
    quietEnd: typeof quietEnd === "string" ? quietEnd : null,
    permission: notifier.permission(),
  };
  useRoutineNotifications({
    snapshot: routinesSnapshot,
    sessions: appSnapshot.sessions,
    enabled: routineControls.notificationsEnabled,
    quietStart: routineControls.quietStart,
    quietEnd: routineControls.quietEnd,
    notifier,
    onOpen: onRoutineNotice,
  });
  // Publish one sources environment for every chip and SourcesStrip read. `recovery` is
  // read as the raw `shellSnapshot.recovery`, not the later `const recovery` of the same name
  // (still in its temporal dead zone this early); `writeScope`/`protectedScope` below are already
  // declared above this call, so reading them directly carries no such risk.
  usePublishSources({
    workspaceId: activeWorkspaceId,
    kind: shellSnapshot.capabilities?.workspace.active?.kind ?? "scratch",
    chatOnly: shellSnapshot.capabilities?.workspace.chat_only ?? true,
    roots,
    rootsTracked: Boolean(rootsApi),
    ...((vaultBrowserApi ?? vaultApi) ? { vault: vaultBrowserApi ?? vaultApi } : {}),
    ...(conversationSearch ? { search: conversationSearch } : {}),
    save: shellSnapshot.recovery ? null : writeScope ? "editor" : protectedScope ? "handoff" : null,
    openNote: openVaultNote,
    openFolder: openVault,
    openUrl: viki.actions.openUrl,
    openDraft: openNoteDraft,
    handoff: restoreText,
  });
  const identity = useMemo<Identity>(
    () => ({
      assistantName:
        typeof assistantNameValue === "string" && assistantNameValue !== ""
          ? assistantNameValue
          : "VIKI",
      assistantAvatarId: typeof assistantAvatarValue === "string" ? assistantAvatarValue : null,
      userAvatarId: typeof userAvatarValue === "string" ? userAvatarValue : null,
      profile: null,
      urls: avatarUrls,
      avatars: avatars ?? null,
    }),
    [assistantNameValue, assistantAvatarValue, userAvatarValue, avatarUrls, avatars],
  );
  // The routines' own active-run sessions: a routine's run notices come from useRoutineNotifications
  // above, so this hook stays quiet about the same session (see state/runNotifications.ts's doc).
  const routineRunSessions = useMemo(
    () =>
      new Set(
        routinesSnapshot.items.flatMap((item) =>
          item.active_run ? [item.active_run.session_id] : [],
        ),
      ),
    [routinesSnapshot.items],
  );
  useRunNotifications({
    groups: runGroups,
    sessions: appSnapshot.sessions,
    exclude: routineRunSessions,
    assistantName: identity.assistantName,
    enabled: routineControls.notificationsEnabled,
    quietStart: routineControls.quietStart,
    quietEnd: routineControls.quietEnd,
    notifier,
    onOpen: onRunNotice,
  });
  const cardArrival = useCardArrivals({
    groups: runGroups,
    sessions: appSnapshot.sessions,
    assistantName: identity.assistantName,
    whereOf: (id) =>
      id === selectedId
        ? tf("shell.needs_you.this_chat", {})
        : sessionLabel(id, restSnapshot.sessions, metadata.sessions),
  });
  const readAloudSettings = useReadAloudSettings(effectiveSettings, shellStore, () =>
    navigate({ view: "settings", page: "chat" }),
  );

  // The chat column follows chatKey: the live selection; with nothing selected, the new-chat
  // editor's key (a drafts-list pick may name a closed chat, whose next send reopens it); null
  // for a closed or not-yet-live selection. An armed fork's editor writes to its own key
  // (`editorKey`) instead; the column still follows the selected source chat.
  const chatKey = selectedId ?? (selectedIdState === null ? newEditorId : null);
  const chatBinding = chatKey === null ? undefined : bindings.get(chatKey);
  // The chat column's own identity. With no profile client, no key, or an unbound key: the same
  // `identity` object, so the nested provider below changes nothing. A bound identity replaces
  // the assistant name and adds the `profile` chip data. Unknown, or bound with no identity:
  // unresolved, so the chat shows no assistant name or face until its binding is known. The
  // user's own name and avatar and the shared cache and client always stay the app's.
  const chatIdentity = useMemo<Identity>(() => {
    if (!profileLaunchApi || chatKey === null || chatBinding === "unbound") return identity;
    if (!chatBinding) return { ...identity, unresolved: true };
    return boundIdentity(
      identity,
      chatBinding,
      botSessions.get(chatKey) ??
        (botChat?.sessionId === chatKey ? botChat.bot.profile_id : undefined),
    );
  }, [identity, profileLaunchApi, chatKey, chatBinding, botSessions, botChat]);
  /** A chat's identity in a pane other than the focused one: its profile once this window knows
   * the chat is bound, else the app's, since a binding is inspected only for the selected chat. */
  function identityFor(id: string): Identity {
    const binding = bindings.get(id);
    return typeof binding === "object" && binding !== null
      ? boundIdentity(
          identity,
          binding,
          botSessions.get(id) ?? (botChat?.sessionId === id ? botChat.bot.profile_id : undefined),
        )
      : identity;
  }

  // The chip before a chat's first session.started: a new chat's defaults, or an opened chat's
  // saved choice, with the provider's last model list. Off without the prefs client, so nothing
  // is fetched; on only for a chat known unbound, so a bound or not-yet-known one gets no chip
  // until its session starts.
  const prestartModels = useMemo(
    () =>
      profileLaunchApi
        ? (signal: AbortSignal) =>
            profileLaunchApi.models("claude", signal).then((view) => view.models)
        : undefined,
    [profileLaunchApi],
  );
  // Keyed on the error object itself, not `seq`: the reducer keeps the same `error` reference
  // across every later event while it stays set (it clears only at session.started/user.message),
  // so a `seq`-keyed trigger re-runs on every one of those unrelated events. A genuinely new
  // start refusal (an error the reducer sets outside a running turn — the shape every profile
  // launch guard failure takes) replaces the object, which is exactly the case that must
  // re-inspect a review opened before the refusal, or never opened.
  const profileReviewRefreshKey = selectedView?.error ?? undefined;
  // presence() reports "waiting" the moment a card opens, even mid-turn — exactly when the Stop
  // pill (and Esc, and the busy-refusal Enter guards) must NOT disappear, so running is read off
  // the turn itself rather than presence. Only the SAME check the reducer used to leave the turn
  // "running" qualifies: a turn that finished (success/cancelled/error) is never "running" again.
  const lastTurn = selectedView?.turns.at(-1);
  const runningComposer = lastTurn?.status === "running";
  function activityFor(selectedView: SessionView | undefined): ComposerActivity {
    const lastTurn = selectedView?.turns.at(-1);
    const runningComposer = lastTurn?.status === "running";
    if (!online)
      return {
        state: "offline",
        label:
          appSnapshot.status === "unauthorized"
            ? "Sign in required"
            : appSnapshot.status === "incompatible"
              ? "Connection incompatible"
              : appSnapshot.status === "connecting"
                ? "Connecting"
                : appSnapshot.status === "reconnecting"
                  ? "Reconnecting"
                  : "Disconnected",
      };
    if (selectedView && !selectedView.synced)
      return { state: "idle", label: "Syncing conversation" };
    if (selectedView?.cards.length)
      return {
        state: "waiting",
        label: selectedView.cards.some((card) => card.kind === "question")
          ? "Waiting for your answer"
          : selectedView.cards.some(
                (card) => card.kind === "permission" && card.data.kind === "plan",
              )
            ? "Waiting for plan approval"
            : "Waiting for approval",
      };
    if (selectedView?.closed) return { state: "idle", label: "Conversation closed" };
    if (selectedView?.error || selectedView?.blocked)
      return { state: "error", label: "Conversation needs attention" };
    if (runningComposer) {
      const lastBlock = lastTurn?.blocks.at(-1);
      const openRow = lastBlock?.kind === "tools" ? lastBlock.rows.at(-1) : undefined;
      const label = selectedView?.compacting
        ? "Compacting context"
        : selectedView && presence(selectedView) === "thinking"
          ? "Thinking"
          : openRow && !openRow.done
            ? openRow.tool.startsWith("mcp__")
              ? "Using a tool"
              : `Using ${openRow.tool}`
            : lastBlock?.kind === "routing" && lastBlock.status === "running"
              ? "Working with an agent"
              : "Writing";
      return {
        state: "active",
        label,
        ...(lastTurn?.user ? { since: Date.parse(lastTurn.user.at) } : {}),
      };
    }
    if (lastTurn?.status === "error") return { state: "error", label: "Reply failed" };
    if (!selectedView?.info && !commandFor("session.new", shellSnapshot, view).enabled)
      return { state: "error", label: "Session start unavailable" };
    return { state: "idle", label: selectedView ? "No reply in progress" : "Start a conversation" };
  }
  // Bumped whenever the turn count or the last turn's status changes, so the context page and
  // the plan-limit meter silently reload once a reply lands, without tracking turn ids themselves.
  const contextKey = `${selectedView?.turns.length ?? 0}:${lastTurn?.status ?? ""}`;
  // The newest finished turn's id, which changes when a reply lands: the git chip's cue to re-read
  // the chat's code folder.
  const lastFinishedTurnId =
    selectedView?.turns.findLast((turn) => turn.status !== "running")?.id ?? "";
  // The independent fork draft stays with its source selection even after that source closes.
  const editorKey =
    forkArmedFrom === selectedIdState && forkEditorId
      ? forkEditorId
      : (selectedIdState ?? newEditorId);
  const editor = editorSnapshot.editors[editorKey];
  const refusalNote = sendRefusals[editorKey] ?? null;
  function setRefusalNote(note: string | null): void {
    setSendRefusals((current) => ({ ...current, [editorKey]: note }));
  }
  const composerOwner = `${
    visibleMultiPanes ? `pane:${panes.layout.focusedChat ?? panes.layout.focused}` : "single"
  }:${editorKey}`;
  const currentComposerTarget = useRef({ key: editorKey, id: selectedId, owner: composerOwner });
  currentComposerTarget.current = { key: editorKey, id: selectedId, owner: composerOwner };
  function composerTargetMatches(key: string, id: string | null): boolean {
    return (
      currentComposerTarget.current.key === key &&
      currentComposerTarget.current.id === id &&
      selectionRef.current === id &&
      editors.getSnapshot().selectedKey === key
    );
  }
  // RetryReview's own fill button stays disabled unless this is true.
  const editorEmpty = !editor?.text && !editor?.attachments?.length;
  // A pane/single-mode transition remounts even the same editor's Composer.
  const dictationApi = useContext(DictationContext);
  const markdownParser = useContext(MarkdownParserContext);
  // Whether VIKI's launcher panel is open. The open panel owns the window's voice (one voice
  // conversation per window): the main loop below leaves and the main boxes' captures are
  // cancelled while it is open, and its own loop (`launcherVoice`) leaves once it closes.
  const [launcherOpen, setLauncherOpen] = useState(false);
  const launcherShown = Boolean(decisionApi) && normalMode && !staleWindow;
  const launcherPanelOpen = launcherOpen && launcherShown;
  const handsFree = useHandsFree({
    dictationApi,
    view: selectedView,
    readAloud: readAloudSettings,
    sessionKey: composerOwner,
    parser: markdownParser,
    // Where the `composer` slot below renders (`view` itself is derived further down), and not
    // while VIKI's panel is open.
    chatShown:
      !shellSnapshot.recovery &&
      nav.view === "chat" &&
      !staleWindow &&
      !archiveOpen &&
      !handoffBlocked &&
      !archivingNow.current.has(editorKey) &&
      !selectedView?.openRefused &&
      !launcherPanelOpen,
  });
  /** What the command record says about the submission the chips rode. `CommandRunner` settles the
   * POST inside `accept()`, when the row is written, so `accepted` says nothing about the excerpts:
   * the dispatch resolves them afterwards. Any later state does say, and a settled excerpt refusal
   * means the quotation never left the app — so it goes back in the composer, where each chip is
   * read again against the note as it is now and a changed one offers its Refresh. */
  function settleExcerpts(receipt: CommandReceipt): void {
    const sent = sentExcerpts.current.get(receipt.command_id);
    if (!sent || receipt.state === "accepted") return;
    sentExcerpts.current.delete(receipt.command_id);
    if (excerptRefusal(receipt))
      setEditorExcerpts(sent.editorKey, (parts) => sent.parts.reduce(withExcerpt, parts));
  }

  // The pending editor's own Check send result and Recheck the same send's receipt land here; the
  // send history's reads, the ones left after acceptance cleared the draft, come through
  // DraftRecovery below.
  // biome-ignore lint/correctness/useExhaustiveDependencies: settleExcerpts reads a ref and this receipt only; taking it as a dependency would re-run the body on every render and undo the user's own Remove.
  useEffect(() => {
    for (const entry of Object.values(editorSnapshot.editors)) {
      if (entry.lastReceipt) settleExcerpts(entry.lastReceipt);
    }
  }, [editorSnapshot.editors]);

  useEffect(() => {
    // A recovery window serves no /api/v2/drafts route; asking anyway would only collect 404s.
    // Declared defence, not a guard with a reachable case today: the one read this window makes
    // is the mount-time one, which leaves before capabilities can report recovery, and the
    // recovery view renders no rail and no composer, so nothing can change `editorKey` after it.
    if (
      durableSuspended.current ||
      handoffBlocked ||
      (selectedView?.openRefused && selectedView.openRefused !== "model_review_required")
    )
      return;
    editors.select(editorKey);
    void editors.ensure(editorKey);
  }, [editors, editorKey, handoffBlocked, selectedView?.openRefused]);

  const visibleEditorKeys =
    visibleMultiPanes &&
    nav.view === "chat" &&
    !shellSnapshot.recovery &&
    !staleWindow &&
    !archiveOpen &&
    !handoffBlocked
      ? paneGroups(panes.layout)
          .flatMap((group) => {
            if (kindOf(group) === "file") return [];
            const view = group.active ? appSnapshot.sessions[group.active] : undefined;
            if (
              group.active &&
              (!view || (view.openRefused && view.openRefused !== "model_review_required"))
            )
              return [];
            return [
              group.active === forkArmedFrom && forkEditorId
                ? forkEditorId
                : (group.active ?? emptyPaneEditor(group.id)),
            ];
          })
          .join(",")
      : "";
  useEffect(() => {
    if (durableSuspended.current) return;
    for (const key of visibleEditorKeys.split(",").filter(Boolean)) {
      if (editors.capacityAvailable(key)) void editors.ensure(key);
    }
  }, [editors, visibleEditorKeys]);

  // Which subscription each session's cards were last checked under. A fresh one (a reconnect,
  // a reopen) is when an answer whose result was never heard is worth asking about again.
  const syncedRef = useRef(new Map<string, string>());
  // Every open card, in every session this window holds — not only the one on screen — is looked
  // up once, so a card answered in another window or before a reload says so rather than
  // offering the opposite choice. An entry is dropped when its card leaves the view.
  useEffect(() => {
    const open = new Set<string>();
    for (const [sessionId, view] of Object.entries(appSnapshot.sessions)) {
      if (view.synced && view.requestId && syncedRef.current.get(sessionId) !== view.requestId) {
        syncedRef.current.set(sessionId, view.requestId);
        approvals.resynced(sessionId);
      }
      if (!view.generation) continue;
      for (const card of view.cards) {
        if (!card.nativeRequest) continue;
        open.add(card.nativeRequest.request_id);
        approvals.observe(card, sessionId, view.generation);
      }
    }
    for (const requestId of Object.keys(approvalSnapshot.approvals))
      if (!open.has(requestId)) approvals.forget(requestId);
  }, [appSnapshot, approvalSnapshot, approvals]);
  // The transcript has been resumed once this window has a turn of its own in it: the note's
  // invitation is spent, while its caveat about the rows above still holds.

  function openSession(sessionId: string, existingOnly = false): void {
    if (archivingNow.current.has(sessionId)) return;
    setDrawer(null);
    if (!allowEditor(sessionId)) return;
    if (existingOnly) viki.actions.openExistingSession(sessionId);
    else viki.actions.openSession(sessionId);
    select(sessionId);
    setForkArmedFrom(null);
  }

  /** The run list's Jump: the run's own chat first when it is not the one on screen, then its
   * routing row (Conversation's block-level `jumpTo`). A chat-level approval row passes no
   * block, so its Jump stops at the chat. Returns whether the jump went ahead, so AgentRuns
   * marks a finished row reviewed only when it actually did. */
  function jumpToRun(sessionId: string, blockId: string | null): boolean {
    setDrawer(null);
    const turn =
      blockId === null
        ? undefined
        : appSnapshot.sessions[sessionId]?.turns.find((candidate) =>
            candidate.blocks.some((block) => block.kind === "routing" && block.id === blockId),
          );
    if (sessionId !== selectedId) {
      openSession(sessionId);
      // A refused selection (the editor cap) keeps the chat on screen: nothing to jump to there.
      if (selectionRef.current !== sessionId) return false;
    }
    // Set after openSession: select() clears turnJump on a selection change, and the later
    // update in this same batch is the one that lands.
    if (turn !== undefined && blockId !== null)
      setTurnJump({ turnId: turn.id, blockId, request: Date.now() });
    return true;
  }

  /** AgentRuns' `onDecide`: a fresh read of `viki.store` — never this render's `appSnapshot`,
   * which a concurrent frame may already have passed — finds the chat's own permission card for
   * `toolUseId`. A missing view or card, an unsynced view, a closed socket, or a card with no
   * `nativeRequest` can none of them be answered, so each rejects with `Error("not_ready")`
   * rather than reaching `approvals.decide` — the same guard `renderCard`'s `disabled` computes
   * for the chat's own cards, applied here to a request named by id instead of one on screen. */
  function decideRequest(
    sessionId: string,
    toolUseId: string,
    allow: boolean,
  ): Promise<AnswerOutcome> {
    const snapshot = viki.store.getSnapshot();
    const view = snapshot.sessions[sessionId];
    const card = view?.cards.find(
      (candidate) => candidate.kind === "permission" && candidate.data.tool_use_id === toolUseId,
    );
    if (!view || !card || !view.synced || snapshot.status !== "open" || !card.nativeRequest) {
      return Promise.reject(new Error("not_ready"));
    }
    return approvals.decide(card, sessionId, view.generation ?? "", { kind: "permission", allow });
  }

  function handleNewSession(modalOrigin?: HTMLElement): void {
    // The table refuses `session.new`: the button keeps its place and its focus, the click does
    // nothing, and the reason is the readiness banner the button already points at. No error
    // card — nothing failed, the app simply knows better than to try.
    if (sessionsBlocked) return;
    // New session always opens Chat: a non-chat view has nowhere to show the new editor, or the
    // toast and banner below. A cancelled unsaved-work prompt leaves the user's view unchanged,
    // exactly as `navigate` already does for every other entry into Chat.
    if (view !== "chat" && !navigate({ view: "chat" }, undefined, modalOrigin)) return;
    setDrawer(null);
    if (liveIds.size >= maxLiveSessions) {
      setToastOpen(true);
      return;
    }
    // Whatever refused earlier no longer holds — a stale toast has nothing left to say.
    setToastOpen(false);
    const id = crypto.randomUUID();
    if (!allowEditor(id)) return;
    // Minted here for a plain chat, so no profile can be bound to it.
    handleUnboundSession(id);
    viki.actions.newSession(id);
    startedHereRef.current.add(id);
    select(id);
  }

  /** The Code view's New session here: answers null once the session has started in Chat, else the
   * refusal's code for the Workbench to word. A cancelled unsaved-work prompt answers null too, with
   * the recorded root left unused. */
  startSessionInRef.current = async (root) => {
    if (!rootsApi || sessionStart.current.pending || !sessionStart.current.mounted)
      return "unavailable";
    if (sessionsBlocked) return "unavailable";
    // The chat column's toast is not on screen in the Code view, so the limit is answered in words
    // by the Workbench rather than by opening it.
    if (liveIds.size >= maxLiveSessions) return "too_many_sessions";
    const id = crypto.randomUUID();
    // `allowEditor` would set a refusal note in the chat column, where the user cannot see it.
    if (!editors.capacityAvailable(id)) return "unavailable";
    const additionalIds = [...(root.additional_workspace_ids ?? [])];
    const origin = currentNavigation.current;
    if (additionalIds.length > 0 && origin.view !== "code") return "unavailable";
    sessionStart.current.pending = true;
    try {
      try {
        noteRoot(
          await rootsApi.record({
            session_id: id,
            workspace_id: root.workspace_id,
            worktree_id: root.worktree_id,
            ...(additionalIds.length > 0 ? { additional_workspace_ids: additionalIds } : {}),
          }),
        );
      } catch (error) {
        return error instanceof RootsApiError ? error.code : "unavailable";
      }
      if (
        !sessionStart.current.mounted ||
        (additionalIds.length > 0 && currentNavigation.current !== origin)
      )
        return null;
      if (!navigate({ view: "chat" })) return null;
      setToastOpen(false);
      handleUnboundSession(id);
      viki.actions.newSession(id);
      startedHereRef.current.add(id);
      select(id);
      return null;
    } finally {
      sessionStart.current.pending = false;
    }
  };

  // The keydown effect above reads this rather than closing over `handleNewSession` directly, so
  // Alt+N and Ctrl+Shift+O always see the current `liveIds` (the session-limit check) even though
  // the listener itself is installed once.
  newSessionRef.current = () => {
    // Not from behind the palette. It covers the screen, so the new session would arrive unseen,
    // and the Esc that dismissed the palette would land them in a conversation they never asked
    // for. The check lives here rather than in the keydown handler because that listener is
    // installed once and would close over the palette state as it was at mount; this assignment
    // runs every render. The New session BUTTON needs no such guard - the palette covers it.
    if (paletteOpen) return;
    handleNewSession();
  };

  // Recovery is derived, never stored: whatever the remembered navigation says, a window whose
  // capabilities report `app.mode: recovery` has exactly one view.
  const recovery = shellSnapshot.recovery;
  const view: View = recovery ? "recovery" : nav.view;
  useEffect(() => {
    if (view !== "bots") setSeatRequest(null);
    if (view !== "agents") setProfileFocus(null);
  }, [view]);
  // The selected chat's own code folder (codeFolderOf), the one folder its git chip reports on and
  // its file rows open in; null without a code client or outside the chat view's selected chat.
  const chatCodeFolder =
    codeApi && !recovery && view === "chat" && selectedId
      ? codeFolderOf(
          selectedId,
          roots,
          Boolean(rootsApi),
          shellSnapshot.workspaces?.workspaces ?? [],
          activeWorkspaceId,
        )
      : null;
  /** Opens a path of the selected chat, in the chat's code folder, as `openCodeFile` does. */
  function openChatCode(path: string, withDiff: boolean): void {
    if (!codeApi || !chatCodeFolder) return;
    openCodeFile({
      api: codeApi,
      folder: { ...chatCodeFolder },
      folderKey: codeFolderKey(chatCodeFolder),
      path,
      withDiff,
    });
  }
  // Asked, not re-derived: the button, both chords (through `handleNewSession`) and the
  // composer's new-session branch are three entry points for one rule, and the rule is the
  // table's (commands.ts). A later gate added there reaches all three by itself.
  const sessionsBlocked = !commandFor("session.new", shellSnapshot, view).enabled;

  /** True once it is fine to drop whatever is unsaved right now: nothing is held, or the user
   * confirmed losing it. Shared by `navigate` and the Agents tab switch, which unmounts the
   * previous section exactly as leaving the view does. */
  function confirmLeaveUnsaved(): boolean {
    return (
      !hasUnsavedWork() || window.confirm("Your unsaved changes will be lost. Leave this view?")
    );
  }

  function vaultSnapshot(): VaultNavigationSnapshot | null {
    const active = shellStore.getSnapshot().workspaces;
    if (currentNavigation.current.view !== "vault" || !active || active.chat_only) return null;
    const snapshot = vaultNavigation.current?.snapshot();
    return snapshot?.workspaceId === active.active_id ? snapshot : null;
  }
  function rememberVault(
    history: NavigationHistory,
    snapshot: VaultNavigationSnapshot | null,
  ): void {
    const context = history.at(-1);
    if (snapshot && context?.navigation.view === "vault")
      vaultSnapshots.current.set(context, snapshot);
  }

  /** Called only after the mounted leaf's own note admission; no second global dirty prompt. */
  function recordVaultNavigation(
    previous: VaultNavigationSnapshot,
    next: VaultNavigationSnapshot,
    owner: VaultNavigationHandle,
  ): boolean {
    const current = vaultSnapshot();
    if (
      owner !== vaultNavigation.current ||
      !current ||
      current.workspaceId !== previous.workspaceId ||
      current.ref !== previous.ref ||
      next.workspaceId !== previous.workspaceId
    )
      return false;
    const context = navigationContext();
    const history = recordTransition(
      navigationHistory.current,
      context,
      {
        ...context,
        note: next.ref === null ? null : { workspace_id: next.workspaceId, ref: next.ref },
      },
      true,
    );
    if (history !== navigationHistory.current) rememberVault(history, previous);
    navigationHistory.current = history;
    pendingBack.current = null;
    applyNavigation({ ...currentNavigation.current });
    return true;
  }

  function navigationContext(): NavigationContext {
    const recoveryNow = shellStore.getSnapshot().recovery;
    if (historyRecovery.current !== recoveryNow) {
      navigationHistory.current = [];
      historyRecovery.current = recoveryNow;
    }
    const region = document.querySelector<HTMLElement>('[data-region="view"]');
    const leaf = vaultSnapshot();
    return {
      navigation: {
        ...currentNavigation.current,
        view: recoveryNow ? "recovery" : currentNavigation.current.view,
      },
      routineId: selectedRoutineRef.current,
      botId: selectedBotRef.current,
      note: leaf?.ref ? { workspace_id: leaf.workspaceId, ref: leaf.ref } : null,
      opener: document.activeElement instanceof HTMLElement ? document.activeElement : null,
      scroll: { top: region?.scrollTop ?? 0, left: region?.scrollLeft ?? 0 },
    };
  }

  /** Compare visible destinations before admission; inactive remembered fields are not steps. */
  function navigate(
    patch: Partial<Navigation>,
    routineId = selectedRoutineRef.current,
    modalOrigin?: HTMLElement,
    botId = selectedBotRef.current,
  ): boolean {
    if (
      (patch.view === "bots" || patch.view === "kanban" || patch.view === "bot_threads") &&
      !botsAvailable
    )
      return false;
    const origin = currentNavigation.current;
    const previous = navigationContext();
    const leaf = vaultSnapshot();
    const next = { ...currentNavigation.current, ...patch };
    const history = recordTransition(
      navigationHistory.current,
      previous,
      {
        ...previous,
        routineId,
        botId,
        navigation: { ...next, view: historyRecovery.current ? "recovery" : next.view },
      },
      true,
    );
    if (
      history !== navigationHistory.current &&
      ((leaf && !vaultNavigation.current?.canLeave(modalOrigin)) || !confirmLeaveUnsaved())
    )
      return false;
    if (
      currentNavigation.current !== origin ||
      selectedRoutineRef.current !== previous.routineId ||
      selectedBotRef.current !== (previous.botId ?? null) ||
      vaultSnapshot()?.ref !== leaf?.ref ||
      vaultSnapshot()?.workspaceId !== leaf?.workspaceId ||
      navigationContext().navigation.view !== previous.navigation.view
    )
      return false;
    if (history !== navigationHistory.current && previous.opener) {
      const identity = navigationOpenerIdentity(previous.opener);
      if (identity) openerIdentities.current.set(previous.opener, identity);
    }
    pendingBack.current = null;
    if (history !== navigationHistory.current) rememberVault(history, leaf);
    setVaultRestore(null);
    navigationHistory.current = history;
    selectedRoutineRef.current = routineId;
    setSelectedRoutine(routineId);
    selectedBotRef.current = botId;
    setSelectedBot(botId);
    setDrawer(null);
    if (next.view !== "chat") setHandoffActive(false);
    selectCodeRequest(null);
    applyNavigation(next);
    return true;
  }

  /** The toast's, the error card's and the Workbench's own "Change limit": Settings › Chat with
   * the live-session field focused. A cancelled unsaved-work prompt leaves the toast and the
   * focus key alone, exactly as `navigate` already does for every other caller. */
  function openLimitSetting(): void {
    if (!navigate({ view: "settings", page: "chat" })) return;
    setToastOpen(false);
    limitFocusNav.current = currentNavigation.current;
    setLimitFocusKey("chat.max_live_sessions");
  }
  openLimitSettingRef.current = openLimitSetting;

  function showBot(botId: string | null): boolean {
    // A profile editor can be replaced without changing the view or selected Bot.
    if (
      currentNavigation.current.view === "bots" &&
      botsSection === "profiles" &&
      selectedBotRef.current === botId &&
      !confirmLeaveUnsaved()
    )
      return false;
    if (!navigate({ view: "bots" }, undefined, undefined, botId)) return false;
    setBotsSection("team");
    return true;
  }

  function goBack(): boolean {
    const current = navigationContext();
    const { context } = peekBack(navigationHistory.current, current.navigation.view);
    if (
      context === null ||
      (vaultSnapshot() && !vaultNavigation.current?.canLeave()) ||
      !confirmLeaveUnsaved()
    )
      return false;
    return restoreBack(context);
  }

  /** Only RoutinesView calls this, after its actual leave wrapper admits the mounted page. */
  function goBackAfterRoutineLeave(): boolean {
    const current = navigationContext();
    if (current.navigation.view !== "routines") return false;
    const { context } = peekBack(navigationHistory.current, current.navigation.view);
    return context === null ? false : restoreBack(context);
  }

  function restoreBack(context: NavigationContext): boolean {
    const leaf = vaultSnapshots.current.get(context);
    if (context.navigation.view === "vault") {
      const active = shellStore.getSnapshot().workspaces;
      const workspaceId = leaf?.workspaceId ?? context.note?.workspace_id;
      if (workspaceId && (!active || active.chat_only || active.active_id !== workspaceId))
        return false;
    }
    const currentView = navigationContext().navigation.view;
    const previous = navigationHistory.current;
    const next = commitBack(previous, currentView, context, true);
    if (next === previous) return false;
    navigationHistory.current = next;
    selectCodeRequest(null);
    setDrawer(null);
    const navigation = { ...context.navigation };
    pendingBack.current = { navigation, context };
    if (navigation.view === "vault") {
      setVaultRestore(leaf ?? null);
      setNoteRequest((request) => ({
        ref: leaf ? "" : (context.note?.ref ?? ""),
        mount: request.mount + 1,
      }));
    }
    selectedRoutineRef.current = context.routineId;
    setSelectedRoutine(context.routineId);
    selectedBotRef.current = context.botId ?? null;
    setSelectedBot(context.botId ?? null);
    applyNavigation(navigation);
    return true;
  }

  const backTarget = peekBack(
    historyRecovery.current === recovery ? navigationHistory.current : [],
    view,
  );
  useEffect(() => {
    const restore = pendingBack.current;
    pendingBack.current = null;
    if (!restore || recovery || nav !== restore.navigation || currentNavigation.current !== nav)
      return;
    if (restore.context.navigation.view === "vault" && vaultSnapshots.current.has(restore.context))
      return;
    const region = document.querySelector<HTMLElement>('[data-region="view"]');
    const scope = region ?? document.querySelector<HTMLElement>('[data-region="conversation"]');
    const opener = restore.context.opener;
    let target = opener && eligibleNavigationOpener(opener) ? opener : null;
    const identity = opener ? openerIdentities.current.get(opener) : undefined;
    const identityScope =
      identity?.scope === "right-rail-summary"
        ? document.querySelector<HTMLElement>('[data-region="right-rail"] .right-rail-summary')
        : identity?.scope === "code-sidebar"
          ? document.querySelector<HTMLElement>('[data-region="right-rail"] .code-sidebar')
          : scope;
    if (!target && identity && identityScope) {
      const candidates = Array.from(
        identityScope.querySelectorAll<HTMLElement>(navigationControls),
      ).filter((element) => {
        const matches =
          "attribute" in identity
            ? element.getAttribute(identity.attribute) === identity.value
            : element.tagName === identity.tag &&
              element.getAttribute("role") === identity.role &&
              navigationControlName(element) === identity.name;
        return matches && eligibleNavigationOpener(element);
      });
      if (candidates.length === 1) target = candidates[0] ?? null;
    }
    for (const selector of ["h1[tabindex]", 'textarea[aria-label="Message"]', "button"]) {
      target ??=
        (nav.view === "chat" && selector.startsWith("textarea")
          ? [focusedMessageBox()].filter((element): element is HTMLElement => element !== null)
          : Array.from(scope?.querySelectorAll<HTMLElement>(selector) ?? [])
        ).find(eligibleNavigationOpener) ?? null;
    }
    target?.focus({ preventScroll: true });
    if (region) {
      region.scrollTop = restore.context.scroll.top;
      region.scrollLeft = restore.context.scroll.left;
    }
  }, [nav, recovery]);
  function renderBack(onBack: () => void): ReactNode {
    const unavailable = backTarget.context === null;
    return (
      <div key="page-back">
        <button
          type="button"
          className="btn txt sm"
          disabled={unavailable}
          aria-describedby={unavailable ? backReasonId : undefined}
          ref={(element) => {
            pageBack.current = element ? { element, navigation: nav } : null;
          }}
          onClick={onBack}
        >
          Back
        </button>
        {unavailable ? (
          <span id={backReasonId}>
            {backTarget.reason === "recovery_root"
              ? "Back is unavailable in Recovery mode."
              : "No earlier page in this window."}
          </span>
        ) : null}
      </div>
    );
  }

  /** The Routine leaf has admitted this selection or completed Save. */
  function selectRoutineAfterLeave(routineId: string): void {
    const previous = navigationContext();
    if (previous.navigation.view !== "routines" || routineId === previous.routineId) return;
    navigationHistory.current = recordTransition(
      navigationHistory.current,
      previous,
      { ...previous, routineId },
      true,
    );
    if (previous.opener) {
      const identity = navigationOpenerIdentity(previous.opener);
      if (identity) openerIdentities.current.set(previous.opener, identity);
    }
    pendingBack.current = null;
    selectedRoutineRef.current = routineId;
    setSelectedRoutine(routineId);
    applyNavigation({ ...currentNavigation.current });
  }

  /** BotsView calls only after its mounted leaf has admitted leaving or completed a mutation. */
  function leaveBotsAfterAdmission(
    nextView: "chat" | "agents" | "bots" | "bot_threads",
    botId = selectedBotRef.current,
  ): boolean {
    const previous = navigationContext();
    if (previous.navigation.view !== "bots" || !botsAvailable) return false;
    const next = { ...previous.navigation, view: nextView };
    navigationHistory.current = recordTransition(
      navigationHistory.current,
      previous,
      { ...previous, navigation: next, botId },
      true,
    );
    if (previous.opener) {
      const identity = navigationOpenerIdentity(previous.opener);
      if (identity) openerIdentities.current.set(previous.opener, identity);
    }
    pendingBack.current = null;
    setVaultRestore(null);
    selectedBotRef.current = botId;
    setSelectedBot(botId);
    setDrawer(null);
    if (nextView !== "chat") setHandoffActive(false);
    applyNavigation(next);
    return true;
  }

  /** A rail row's or a notification's routine, selected in the Routines view. */
  function openRoutine(routineId: string): void {
    navigate({ view: "routines" }, routineId);
  }
  openRoutineRef.current = openRoutine;

  /** A run notice opens the chat, then its routing row, matching
   * AgentRuns' own Jump. */
  function openRun(sessionId: string, blockId: string | null): void {
    if (!navigate({ view: "chat" })) return;
    jumpToRun(sessionId, blockId);
  }
  openRunRef.current = openRun;

  /** A quoted code range goes on the end of the selected draft, on a line of its own. The chat
   * is shown first and the draft written only when the user did not cancel leaving the view. */
  function quoteCode(text: string): boolean {
    if (!navigate({ view: "chat" })) return false;
    const draft = editors.getSnapshot().editors[editorKey]?.text ?? "";
    editors.edit(editorKey, `${draft}${draft && !draft.endsWith("\n") ? "\n" : ""}${text}\n`);
    return true;
  }

  function selectCodeRequest(request: CodeOpenRequest | null): void {
    currentCodeRequest.current = request;
    setCodeRequest(request);
  }

  /** `request` still reads through its folder's current registration, with `api`: the code
   * client, or a file pane's `fileApi`. */
  function admittedCodeRequest(request: CodeOpenRequest, api = currentCodeApi.current): boolean {
    const folder = shellStore
      .getSnapshot()
      .workspaces?.workspaces.find(
        (row) => row.kind === "code" && row.workspace_id === request.folder.workspace_id,
      );
    return (
      api === request.api &&
      folder?.root_available === true &&
      codeFolderKey(folder) === request.folderKey
    );
  }

  function openCodeRequest(request: CodeOpenRequest | null): void {
    if (request && !admittedCodeRequest(request)) return;
    if (request) {
      codeOpener.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setRefusalNote(null);
      if (drawer === "right") setDrawer(null);
    }
    selectCodeRequest(request);
  }

  /** The Code tree's, a chat's file rows' and the Git chip's open: in a pane when one shows the
   * file (`openInPane`), else in the middle preview. */
  function openCodeFile(request: CodeOpenRequest | null): void {
    if (request && !admittedCodeRequest(request)) return;
    if (request && view === "code" && !navigate({ view: "chat" })) return;
    if (request && openInPane(request)) return;
    openCodeRequest(request);
  }

  /** Opens `request`'s file in a pane while the panes can sit side by side (`openFile`): the
   * focused file pane, else the first one, else a new one beside the focused chat pane when
   * `fileRoom` allows. False, changing nothing, when no pane would show it. */
  function openInPane(request: CodeOpenRequest): boolean {
    if (
      !organization ||
      staleWindow ||
      archiveOpen ||
      !layout.rightDocked ||
      (newestPanes.current.root.kind === "split" && !visibleMultiPanes) ||
      !fileApi
    )
      return false;
    const file = { workspaceId: request.folder.workspace_id, ref: request.path };
    const before = newestPanes.current;
    let opened = false;
    const next = updatePanes((l) => {
      const placed = openFile(l, file, fileRoom);
      // One pane shows the chat alone, so a layout of one (file) pane would hide the file.
      if (placed === null || placed.root.kind !== "split") return l;
      opened = true;
      return placed;
    });
    if (!opened) return false;
    // A new tab starts from this request, its diff included; a tab already open keeps its own.
    const tab = focusedGroup(next).active;
    const kept = tab === null ? undefined : fileRequests.current.get(tab);
    if (tab !== null && (kept?.folder.workspace_id !== file.workspaceId || kept.path !== file.ref))
      fileRequests.current.set(tab, { ...request, api: fileApi });
    focusOpenedFile(before);
    if (currentCodeRequest.current) selectCodeRequest(null);
    if (drawer === "right") setDrawer(null);
    return true;
  }

  /** After a change that opened a file tab, active in the focused pane: its preview takes the
   * keyboard focus as it mounts (`freshFileTab`). Nothing when the layout did not change, since
   * no commit would then show the tab. */
  function focusOpenedFile(before: PaneLayout): void {
    const next = newestPanes.current;
    if (next !== before) freshFileTab.current = focusedGroup(next).active;
  }

  /** `openFile`'s room to split pane `groupId` at `edge` for a file pane: the panes sit side by
   * side and the pane measures at least twice the floor of the chat pane being split (the larger
   * floor) on that axis; the one pane by the row its split would share (`data-row-room`).
   * Unmeasured, as in jsdom, is no room. */
  function fileRoom(groupId: string, edge: Edge): boolean {
    const box = document.querySelector(`[data-pane="${groupId}"]`);
    if (
      !layout.rightDocked ||
      (newestPanes.current.root.kind === "split" && !visibleMultiPanes) ||
      !box
    )
      return false;
    if (edge === "bottom") return box.getBoundingClientRect().height >= 2 * PANE_MIN.height;
    const width =
      paneGroups(newestPanes.current).length === 1
        ? Number(box.getAttribute("data-row-room") ?? 0)
        : box.getBoundingClientRect().width;
    return edge === "right" && width >= 2 * PANE_MIN.width;
  }

  function closeCodePreview(): void {
    if (!currentCodeRequest.current) return;
    codeCloseFocus.current = "opener";
    selectCodeRequest(null);
  }

  /** Puts a quoted range of `request`'s file, read with `api`, at the end of the selected draft;
   * refuses, in the refusal note, a folder other than the active one or a draft over 1 MiB. True
   * once the draft holds it. */
  function quoteInDraft(quote: CodeQuote, request: CodeOpenRequest, api?: CodeApi): boolean {
    if (!admittedCodeRequest(request, api)) return false;
    const current = shellStore.getSnapshot();
    const active = current.capabilities?.workspace.active;
    if (
      active?.kind !== "code" ||
      active.id !== quote.workspaceId ||
      quote.workspaceId !== request.folder.workspace_id ||
      !current.workspaces?.workspaces.some(
        (folder) => folder.kind === "code" && folder.workspace_id === quote.workspaceId,
      )
    ) {
      setRefusalNote("Only files in the active code folder can be quoted");
      return false;
    }
    const draft = editors.getSnapshot().editors[editorKey]?.text ?? "";
    const candidate = `${draft}${draft && !draft.endsWith("\n") ? "\n" : ""}${quote.text}\n`;
    if (new TextEncoder().encode(candidate).byteLength > 1 << 20) {
      setRefusalNote(TOO_LONG_NOTE);
      return false;
    }
    setRefusalNote(null);
    return quoteCode(quote.text);
  }

  function quoteSidebar(quote: CodeQuote, request: CodeOpenRequest): void {
    if (currentCodeRequest.current === request && quoteInDraft(quote, request))
      codeCloseFocus.current = "composer";
  }

  /** A file pane's quote: only from the request that pane still shows (`fileRequests`); the
   * Message box takes the focus after it. */
  function quotePane(tab: string, quote: CodeQuote, request: CodeOpenRequest): void {
    if (fileRequests.current.get(tab) !== request) return;
    if (quoteInDraft(quote, request, currentFileApi.current)) focusedMessageBox()?.focus();
  }

  function selectRightRail(panel: "overview" | "code"): void {
    setRightRailPanel(panel);
    if (panel !== "code" || !codeApi) return;
    setCodeRailVisited(true);
    const current = shellStore.getSnapshot();
    if (!current.workspaces && current.workspacesStatus !== "loading")
      void shellStore.refreshWorkspaces();
  }

  /** The header pill's click: the right rail's own Agents list, docked or (below 1280px) in its
   * drawer. */
  function showRuns(): void {
    selectRightRail("overview");
    if (layout.rightDocked) setRunsFocus((n) => n + 1);
    else setDrawer("right");
  }

  function openVault(folder: string, modalOrigin?: HTMLElement): void {
    if (!navigate({ view: "vault" }, undefined, modalOrigin)) return;
    setVaultFolder(folder);
    // A folder entry opens the browser, not whichever note a decision named last; the mount
    // counter is left alone, so this never remounts a browser holding an unsaved edit.
    setNoteRequest((request) =>
      request.ref === "" && !request.draft ? request : { ref: "", mount: request.mount },
    );
  }

  /** A mounted Vault admits note changes itself; requests from another view consume initialRef
   * once through a fresh mount. */
  function openVaultNote(ref: string, origin?: HTMLElement): boolean {
    if (vaultSnapshot() && vaultNavigation.current)
      return vaultNavigation.current.open(ref, origin);
    if (view === "vault" && !confirmLeaveUnsaved()) return false;
    if (!navigate({ view: "vault" })) return false;
    setNoteRequest((request) => ({ ref, mount: request.mount + 1 }));
    return true;
  }

  /** Open a reply's "Save as note" draft on a fresh mount, since `initialDraft` (like
   * `initialRef`) is consumed once per mount and a draft is not just a ref the already-mounted
   * browser's own `open(ref)` could jump to. */
  function openNoteDraft(draft: NoteDraft): void {
    if (!confirmLeaveUnsaved()) return;
    if (!navigate({ view: "vault" })) return;
    setNoteRequest((request) => ({ ref: "", mount: request.mount + 1, draft }));
  }

  // `initialDraft` is consumed once at VaultBrowser's own mount (its seededDraft ref), the same
  // commit where `view` becomes "vault"; App's own copy has then done its job, and clearing it
  // keeps a later remount (a workspace switch, Back) from seeding a stale draft again.
  useEffect(() => {
    if (view !== "vault") return;
    setNoteRequest((request) =>
      request.draft ? { ref: request.ref, mount: request.mount } : request,
    );
  }, [view]);

  /** The inbox tile's other destination, when VIKI can triage captures: refused in a recovery
   * window, closes the native inbox first so only one modal is ever on screen, then opens the
   * triage dialog. */
  function openTriage(): void {
    if (shellStore.getSnapshot().recovery) return;
    showDecisions(false);
    setTriageOpen(true);
  }

  /** The readiness banner's own sentence, so the composer refuses in the same words the button
   * points at. Read from the banner rather than re-derived: the priority order that picks it
   * lives in ReadinessBanner, and a second copy here would be a second thing to keep true. */
  function readinessSentence(): string {
    const banner = document.getElementById("readiness-reason");
    return banner?.querySelector("span")?.textContent || t("reason.provider_not_ready");
  }

  /** Every dispatched command comes through here, and one the table refuses does nothing at all.
   *
   * Not every `CommandId` has a case: the table describes what the app offers and why a row is
   * off, which is a wider list than what anything dispatches. `session.new` is the sidebar button
   * and Alt+N, both straight into `handleNewSession`, which reads the table's own answer through
   * `sessionsBlocked`; `command.run` has no entry point; `diagnostics.check` is
   * ProvidersPage calling `store.runCheck` itself; `settings.search` has
   * no key and no row (Ctrl+, dispatches `settings.open`); `workspace.later` is OnboardingView's
   * own PATCH, and one rule wants one implementation. A case here for any of them would be a
   * second copy of something with nothing to call it.
   *
   * No dispatched row carries an argument either: `diagnostics.check` is the only id whose state
   * depends on one, and the Providers page asks `commandFor` for each of its two checks itself. */
  function onCommand(id: CommandId): void {
    if (!commandFor(id, shellSnapshot, view).enabled) return;
    switch (id) {
      case "search.conversations":
        setDrawer(null);
        setPaletteOpen(true);
        return;
      case "search.vault":
        // Stays disabled (the backend serves no `vault_search` feature yet); the row exists so
        // the user can see the command and read where search already lives, the Vault view.
        return;
      case "workspace.create":
        navigate({ view: "onboarding", onboardingMode: "create" });
        return;
      case "workspace.open":
        navigate({ view: "onboarding", onboardingMode: "open" });
        return;
      case "workspace.select":
        navigate({ view: "settings", page: "workspaces" });
        return;
      case "bots.open":
        if (botsAvailable) navigate({ view: "bots" });
        return;
      case "settings.open":
        navigate({ view: "settings" });
        return;
      case "diagnostics.open":
        navigate({ view: recovery ? "recovery" : "settings", page: "diagnostics" });
        return;
      case "data.open":
        navigate({ view: "data", panel: "locations" });
        return;
      case "data.export_diagnostics":
        navigate({ view: "data", panel: "export" });
        return;
      case "data.import_settings":
        navigate({ view: "data", panel: "import" });
        return;
      case "data.reset":
        navigate({ view: "data", panel: "reset" });
        return;
      case "provider.setup":
        navigate({ view: "settings", page: "providers" });
        return;
    }
  }
  commandRef.current = onCommand;

  escapeRef.current = () => {
    const active = document.activeElement;
    if (
      isTextEntryTarget(active) ||
      active instanceof HTMLSelectElement ||
      active?.closest(".code-editor")
    )
      return false;
    if (currentCodeRequest.current) {
      closeCodePreview();
      return true;
    }
    const current = navigationContext();
    const control = pageBack.current;
    if (
      !control ||
      control.navigation !== currentNavigation.current ||
      current.navigation.view === "recovery" ||
      (current.navigation.view === "onboarding" &&
        current.navigation.onboardingMode !== "choose") ||
      peekBack(navigationHistory.current, current.navigation.view).context === null ||
      !eligibleNavigationOpener(control.element)
    )
      return false;
    // The mounted page supplies admission, including Routine's leave wrapper.
    control.element.click();
    return true;
  };

  findRef.current = (target) => {
    if (
      view !== "chat" ||
      staleWindow ||
      archiveOpen ||
      (codeApi && codeRequest) ||
      !archives ||
      !assets ||
      !conversationSearch ||
      !selectedView ||
      (target instanceof HTMLElement && target.closest(".code-preview, .code-editor"))
    )
      return false;
    openTools("field");
    return true;
  };

  /** Every close door in the app goes through here, and closes at once: the row menu's Close, an
   * open card's, Running work's own Close in the Agents view, and the session-limit toast's.
   * Closing leaves the chat's files on disk and its transcript in the list, so there is nothing
   * to ask (the uncommitted-changes question confused more than it protected). */
  function requestClose(sessionId: string): void {
    setDrawer(null);
    void closeChat(sessionId);
  }

  /** Ends chat `sessionId`. Once the server applies the close, its tab leaves this window's
   * panes as its own × would (`closeChatTab`), so no tab stays behind for a chat that has
   * ended; a close that is not applied leaves the tab where it is. */
  async function closeChat(sessionId: string): Promise<boolean> {
    const outcome = await viki.actions.closeSession(sessionId);
    if (outcome !== "applied") return false;
    currentCloseChatTab.current(sessionId);
    return true;
  }

  async function updateSessionMetadata(
    sessionId: string,
    patch: SessionMetadataPatch,
  ): Promise<SessionMetadata | null> {
    if (archivingNow.current.has(sessionId)) return null;
    if (patch.archived !== true) return metadata.update(sessionId, patch);
    if (metadata.pending.has(sessionId)) return null;
    archivingNow.current.add(sessionId);
    setArchivingIds(new Set(archivingNow.current));
    setSessionActionNote("");
    try {
      if (viki.store.getSnapshot().sessions[sessionId] && !(await closeChat(sessionId))) {
        setSessionActionNote("This session could not be closed. It has not been archived.");
        return null;
      }
      const updated = await metadata.update(sessionId, patch);
      if (updated) currentCloseChatTab.current(sessionId);
      return updated;
    } finally {
      archivingNow.current.delete(sessionId);
      setArchivingIds(new Set(archivingNow.current));
    }
  }

  function handleMissingUnpin(sessionId: string): void {
    void metadata.update(sessionId, { pinned: false });
  }

  /** The missing-source rail's Remove: drops the label. metadata.remove never rejects itself; a
   * failed delete keeps the label and reports through metadata.error, shown by App's own reload
   * notice above. */
  async function handleMissingRemove(sessionId: string): Promise<void> {
    const record = missing.find((item) => item.session_id === sessionId);
    if (!record) return;
    setRemovingIds((current) => new Set(current).add(sessionId));
    try {
      if (record.revision !== null) await metadata.remove(sessionId);
    } finally {
      setRemovingIds((current) => {
        const next = new Set(current);
        next.delete(sessionId);
        return next;
      });
    }
  }

  /** The row menu's Fork and BlockedCard's onFork both land here: select the source (so the
   * user types the fork's opening message into a conversation they can see) and arm the send. */
  function armFork(sourceId: string): void {
    setDrawer(null);
    const forkId = crypto.randomUUID();
    if (!allowEditor(forkId)) return;
    // The server refuses to fork a profile session (profile_locked), so a fork is always plain.
    handleUnboundSession(forkId);
    openSession(sourceId);
    setForkEditorId(forkId);
    setForkArmedFrom(sourceId);
  }

  /** Opens `text` in a NEW editor, keeping the current one. False when this window has no editor
   * left: then nothing was opened, and the caller must not say a draft exists. */
  function restoreText(text: string, attachments: readonly Attachment[] = []): boolean {
    const key = crypto.randomUUID();
    // Clears any earlier refusal (a cut prompt this action itself may have just named) before
    // allowEditor sets its own, so a stale refusal never stays up above the prompt it restores.
    setRefusalNote(null);
    if (!allowEditor(key)) return false;
    // A fresh new-chat editor: its first send starts a plain chat under this id.
    handleUnboundSession(key);
    mintedRef.current.add(key); // minted as new: the pre-start chip may offer a held pick
    editors.edit(key, text);
    if (attachments.length) editors.attach(key, attachments);
    setNewEditorId(key);
    setForkArmedFrom(null);
    select(null);
    return true;
  }

  function allowEditor(key: string): boolean {
    if (editors.capacityAvailable(key)) return true;
    setRefusalNote(t("decisions.editor_full"));
    return false;
  }

  /** Opens `sessionId` in a new window. Resolves to the refusal message on failure (so a caller
   * with nowhere else to show `windowErrors`, like the tab menu, can still surface it), or `null`
   * once it is either showing inline (the rail's own `newWindowErrorOf`) or not needed. */
  async function openSessionWindow(sessionId: string): Promise<string | null> {
    if (!sessionWindowsApi || recovery || staleWindow || windowCalls.current.has(sessionId))
      return null;
    const controller = new AbortController();
    windowCalls.current.set(sessionId, controller);
    setWindowPending(new Set(windowCalls.current.keys()));
    setWindowErrors((prior) => {
      const next = new Map(prior);
      next.delete(sessionId);
      return next;
    });
    let message: string | null = null;
    try {
      await sessionWindowsApi.open(sessionId, controller.signal);
    } catch (error) {
      if (controller.signal.aborted) return null;
      const code = error instanceof SessionWindowsError ? error.code : "unavailable";
      message =
        code === "window_limit"
          ? "Too many session windows are open. Close one and try again."
          : code === "unavailable"
            ? "This conversation is unavailable. Refresh sessions and try again."
            : "The new window could not be opened. Try again.";
      setWindowErrors((prior) => new Map(prior).set(sessionId, message as string));
    } finally {
      if (windowCalls.current.get(sessionId) === controller) {
        windowCalls.current.delete(sessionId);
        if (!controller.signal.aborted) setWindowPending(new Set(windowCalls.current.keys()));
      }
    }
    return message;
  }

  function terminalRoot(sessionId: string): SessionRootJson | null {
    if (!terminalApi || !rootsApi || recovery || staleWindow) return null;
    const root = roots.roots.get(sessionId);
    if (!root) return null;
    const folder = shellSnapshot.workspaces?.workspaces.find(
      (item) => item.workspace_id === root.workspace_id,
    );
    if (
      folder?.kind !== "code" ||
      folder.policy !== "ordinary" ||
      !folder.persistent ||
      !folder.root_available
    )
      return null;
    if (
      root.worktree_id !== null &&
      !roots.worktrees.some(
        (item) =>
          item.worktree_id === root.worktree_id &&
          item.workspace_id === root.workspace_id &&
          item.state === "ready",
      )
    )
      return null;
    return root;
  }

  async function openSessionTerminal(sessionId: string): Promise<void> {
    const root = terminalRoot(sessionId);
    if (!root || !terminalApi || terminalCall.current) return;
    const controller = new AbortController();
    terminalCall.current = controller;
    setSessionActionNote("Opening a terminal in this session's folder…");
    try {
      await terminalApi.open(root.workspace_id, root.worktree_id, controller.signal);
      if (!controller.signal.aborted) setSessionActionNote(t("shell.terminal.opened"));
    } catch (error) {
      if (!controller.signal.aborted)
        setSessionActionNote(
          terminalRefusal(error instanceof TerminalApiError ? error.code : "unavailable"),
        );
    } finally {
      if (terminalCall.current === controller) terminalCall.current = null;
    }
  }

  /** Shows the chat's tools panel at once: its `hidden` DOM property flips synchronously, ahead
   * of the `toolsOpen` render React otherwise defers, so focus can land the same tick — the
   * search field for a Ctrl+F or "Select turns to export", or the panel itself when there is no
   * search field to focus. */
  function openTools(focus: "field" | "panel"): void {
    const panel = exportTools.current;
    if (!panel) return;
    panel.hidden = false;
    setToolsOpen(true);
    const field =
      focus === "field"
        ? panel.querySelector<HTMLInputElement>(".conversation-search input")
        : null;
    (field ?? panel).focus();
  }

  function revealSessionExport(sessionId: string): void {
    if (!archives || !assets || !navigate({ view: "chat" }, undefined, sidebarModalOrigin()))
      return;
    openSession(sessionId, true);
    if (selectionRef.current === sessionId) setExportSessionId(sessionId);
  }

  async function handleSend(text: string, key = editorKey, id = selectedId): Promise<boolean> {
    // Freeze the displayed raw preference before a held effort/model save can await a reply.
    const preview = prestarts.current.get(key)?.preview(key);
    const setSendRefusal = (note: string | null) =>
      setSendRefusals((current) => ({ ...current, [key]: note }));
    if (viki.store.getSnapshot().sessions[key]?.openRefused === "model_review_required")
      return false;
    if (archivingNow.current.has(key)) {
      setSendRefusal("This conversation is being archived. The draft remains.");
      return false;
    }
    if (!composerTargetMatches(key, id)) return false;
    const excerptParts = excerptsByEditor[key] ?? [];
    const attachments = (editors.getSnapshot().editors[key]?.attachments ?? []).map((item) => ({
      ...item,
    }));
    // Composer's own `steer` prop (Boolean(selectedId) && !forkArmed) is this exact gate: it
    // calls onSend while running only when it steers, so a send that reaches here running did
    // so because Composer decided to. A profile chat steers like any chat where its Claude Code
    // version has recorded steer evidence; elsewhere the server answers `turn_running` and the
    // box keeps the draft with its note. Refuse outright only when it did not steer.
    const forkArmed = Boolean(forkArmedFrom) && Boolean(forkEditorId) && key === forkEditorId;
    const steering = Boolean(id) && !forkArmed;
    if (sendingKeys.current.has(key) || (runningComposer && !steering)) return false;
    setSendRefusal(null);
    if (
      excerptParts.length > 0 &&
      sentExcerpts.current.size + pendingExcerptEditors.current.size >= 64
    ) {
      setSendRefusal(
        "Check send history to confirm earlier excerpt sends before sending another excerpt. The text stays in this box.",
      );
      return false;
    }
    if (appSnapshot.status !== "open") {
      setSendRefusal("Can't confirm the connection. The text remains.");
      return false;
    }
    if (new TextEncoder().encode(text).byteLength > 1 << 20) {
      setSendRefusal(TOO_LONG_NOTE);
      return false;
    }
    if (selectedIdState && !selectedId && !forkArmedFrom) {
      setSendRefusal(
        "This conversation is closed. Open the draft in a new message box, then send.",
      );
      return false;
    }
    sendingKeys.current.add(key);
    if (excerptParts.length > 0) pendingExcerptEditors.current.add(key);
    try {
      let target: { session_id: string; generation: string } | null;
      if (forkArmedFrom && forkEditorId && key === forkEditorId) {
        target = null;
        if (attemptedForks.current.has(key)) {
          try {
            const existing = await durable.getTarget(key);
            viki.actions.openSession(key);
            const synced = await viki.actions.prepareTarget(key);
            if (synced?.generation === existing.generation && !existing.closed) target = synced;
          } catch (error) {
            if (!(error instanceof DurableApiError) || error.status !== 404) {
              setSendRefusal("Can't confirm the fork target's state. The text remains.");
              return false;
            }
          }
        }
        if (!target) {
          attemptedForks.current.add(key);
          target = await viki.actions.createFork(forkArmedFrom, key);
        }
        if (target) {
          startedHereRef.current.add(key);
          if (currentForkEditor.current === key && selectionRef.current === forkArmedFrom) {
            setForkArmedFrom(null);
            select(key);
          }
        }
      } else {
        if (!selectedId) {
          // Same gate as the New session button, in the same words: the draft is already saved
          // and stays in the box, and no error card is produced.
          if (sessionsBlocked) {
            setSendRefusal(readinessSentence());
            return false;
          }
          if (liveIds.size >= maxLiveSessions) {
            setToastOpen(true);
            return false;
          }
          // A pick held for this new chat is written before its first session.open, so that
          // start runs it. Nothing held, nothing awaited: the open goes out at once.
          if (
            prestarts.current.get(key)?.holds(key) &&
            !(await prestarts.current.get(key)?.commit(key))
          ) {
            setSendRefusal(PREFS_NOT_SAVED);
            return false;
          }
          if (!composerTargetMatches(key, id)) return false;
          mintedRef.current.delete(key); // started: a later drafts-list pick gets no hold path
          if (preview) viki.actions.newSession(key, preview.model);
          else viki.actions.newSession(key);
          startedHereRef.current.add(key);
          select(key);
        }
        target = await viki.actions.prepareTarget(key);
      }
      if (!target) {
        if (viki.store.getSnapshot().sessions[key]?.openRefused !== "model_review_required")
          setSendRefusal("Can't confirm the destination. The text remains.");
        return false;
      }
      const receipt = await editors.submit(key, target.generation, text, attachments, excerptParts);
      // The chips belong to the message that carried them; the next turn starts without them.
      // Acceptance is not yet evidence that the quotation went out, so this submission keeps them
      // until its record settles: settleExcerpts puts them back on an excerpt refusal.
      if (receipt) {
        // Sending to an archived session also resumes it in the session list.
        if (archivedIds.has(target.session_id))
          void metadata.update(target.session_id, { archived: false });
        if (excerptParts.length > 0)
          sentExcerpts.current.set(receipt.command_id, { editorKey: key, parts: excerptParts });
        setEditorExcerpts(key, (parts) => parts.filter((part) => !excerptParts.includes(part)));
        settleExcerpts(receipt);
      }
      // A dispatch refusal already has its own true sentence in the draft-status region (and,
      // unlike this generic note, there is no send record for the user to go check: the actor
      // guard runs before acceptance, so none was ever committed). Do not say both.
      if (!receipt && !editors.getSnapshot().editors[key]?.dispatchRefusal)
        setSendRefusal(
          "Can't confirm the send result. Check the draft's status and the send history.",
        );
      return receipt !== null;
    } finally {
      sendingKeys.current.delete(key);
      pendingExcerptEditors.current.delete(key);
    }
  }

  /** Wrap up: the convention goes out as an ordinary turn, so the hooks, the log and the commit
   * are claude's doing under the vault's own rules — this app never writes the vault itself. It
   * goes through handleSend rather than around it, so every rule that governs a typed message
   * governs this one: with nothing selected it opens a session and sends into it, and while a
   * fork is armed it becomes the fork's opening message. */
  function handleWrapUp(): void {
    // handleSend refuses while a turn runs, so editing the box first would leave the convention
    // sitting in the composer with nothing sent — and the user reading it as done.
    if (runningComposer) {
      setRefusalNote("A reply is in progress. Wait for it to finish, then wrap up.");
      return;
    }
    if (editors.getSnapshot().editors[editorKey]?.text) {
      setRefusalNote(
        "There's a draft in progress. Send it, or move it to another message box, then wrap up.",
      );
      return;
    }
    editors.edit(editorKey, WRAP_UP_PROMPT);
    void handleSend(WRAP_UP_PROMPT);
  }

  /** This app's OTHER live sessions, titled — what Wrap up has to wait for besides `others`.
   *
   * `others` comes from vault.sessions, which is built by subtracting own_pids(): every process
   * this app runs. So the list Wrap up was given could not, by construction, contain a sibling of
   * the session being wrapped up — while wrapUp.ts's own docstring promised it was disabled for
   * "another window of this app's" too. The prompt runs `git add -A`, so a sibling mid-edit is
   * precisely the case the feature exists to prevent, and it was the one case it could not see.
   */
  function otherLiveTitles(): string[] {
    return [...liveIds]
      .filter((id) => id !== selectedId)
      .map((id) => {
        const row = restSnapshot.sessions.find((r) => r.session_id === id);
        return row ? rowTitle(row) : "";
      });
  }

  /** The toast's own rows: the live sessions in tracking order, named from the REST list (a
   * brand-new session has no row there yet, so SessionLimitToast's own "Untitled session"
   * fallback carries it) and marked writing from the same presence() the rail rows use. */
  function liveSummaries(): LiveSessionSummary[] {
    return [...liveIds].map((id) => {
      const view = appSnapshot.sessions[id];
      const row = restSnapshot.sessions.find((r) => r.session_id === id);
      const p = view ? presence(view) : "idle";
      return {
        sessionId: id,
        title: row ? rowTitle(row) : "",
        writing: p === "writing" || p === "thinking",
      };
    });
  }

  /** One open card of session `sid`, answerable. The card column and the decision inbox both
   * render through here, so a card has one answer path wherever it is shown. */
  function renderCard(card: Card, sid: string, view: SessionView, inColumn = false): ReactNode {
    // The ledger's own view of this request, when this page has one. The answer itself goes
    // through `approvals.decide`, which records it before the server delivers it.
    const approval = card.nativeRequest
      ? approvalSnapshot.approvals[card.nativeRequest.request_id]
      : undefined;
    const generation = view.generation ?? "";
    const targeted =
      inColumn &&
      cardTarget !== null &&
      cardTarget.sessionId === sid &&
      cardTarget.toolUseId === card.data.tool_use_id;
    return card.kind === "permission" ? (
      <PermissionCard
        key={card.nativeRequest?.request_id ?? card.data.tool_use_id}
        data={card.data}
        disabled={!view.synced || appSnapshot.status !== "open" || !card.nativeRequest}
        recoveryKey={view.synced ? (view.requestId ?? "") : ""}
        {...(targeted ? { target: true, targetRequest: cardTarget.request } : {})}
        {...(approval ? { approval } : {})}
        onRespond={(_toolUseId, allow) =>
          approvals.decide(card, sid, generation, { kind: "permission", allow }).then((outcome) => {
            if (targeted) setCardTarget(null);
            return outcome;
          })
        }
        onOpenUrl={viki.actions.openUrl}
      />
    ) : (
      <QuestionCard
        key={card.nativeRequest?.request_id ?? card.data.tool_use_id}
        data={card.data}
        disabled={!view.synced || appSnapshot.status !== "open" || !card.nativeRequest}
        recoveryKey={view.synced ? (view.requestId ?? "") : ""}
        {...(targeted ? { target: true, targetRequest: cardTarget.request } : {})}
        {...(approval ? { approval } : {})}
        onAnswer={(_toolUseId, answers, response) =>
          approvals
            .decide(card, sid, generation, { kind: "question", answers, response })
            .then((outcome) => {
              if (targeted) setCardTarget(null);
              return outcome;
            })
        }
      />
    );
  }

  // The row the Decision page opens on arrival (DecisionLoopPage's `openRow`). Cleared on leaving
  // the view, so a later visit starts from the page's own defaults.
  const [decisionRow, setDecisionRow] = useState<string | null>(null);
  useEffect(() => {
    if (view !== "decision") setDecisionRow(null);
  }, [view]);

  // Context & loadout opens on its Decision tab for each new request (the launcher's "See what
  // was sent", the Decision page's "See the last state sent"). Cleared on leaving the view, so a
  // later visit starts on Context. `decisionRequestedCallId` rides beside it: the specific call an
  // item's own open_context action named (the launcher's "See why"), or null for a request
  // that named none (the Decision page's "See the last state sent" still wants the newest).
  const [decisionRequest, setDecisionRequest] = useState<number | undefined>(undefined);
  const [decisionRequestedCallId, setDecisionRequestedCallId] = useState<string | null>(null);
  useEffect(() => {
    if (view !== "context") {
      setDecisionRequest(undefined);
      setDecisionRequestedCallId(null);
    }
  }, [view]);
  // DecisionTab's own prop: undefined until the first request, exactly like `decisionRequest`
  // itself, so a tab opened by clicking it directly keeps picking the newest call, unchanged.
  const decisionRequestedCall = useMemo<RequestedCall | undefined>(
    () =>
      decisionRequest === undefined
        ? undefined
        : { callId: decisionRequestedCallId, request: decisionRequest },
    [decisionRequest, decisionRequestedCallId],
  );

  // Decision's settings writes (its page, the launcher's menu, position and chat) take the rail
  // widths' gate: a shell that can patch and a ready settings document that is neither read-only
  // nor unreadable (a recovery window shows neither).
  const decisionSettings = shellSnapshot.settingsStatus === "ready" ? shellSnapshot.settings : null;
  const decisionWritable =
    typeof shell?.patchSettings === "function" &&
    decisionSettings !== null &&
    !decisionSettings.read_only &&
    decisionSettings.values !== null;

  // Whether the skill/question-wording hint line under a finished reply has moved into the
  // launcher's own item instead (SkillTurnActions' own prop): Proactive VIKI on, not paused (a
  // `paused_until` that has already passed does not count), and Self-improvement not off.
  const decisionPausedUntil = effectiveSettings?.["decision.paused_until"];
  const decisionPaused =
    typeof decisionPausedUntil === "string" &&
    decisionPausedUntil !== "" &&
    Date.parse(decisionPausedUntil) > now;
  const hintInLauncher =
    effectiveSettings?.["decision.enabled"] === true &&
    !decisionPaused &&
    effectiveSettings?.["decision.watch.improve"] !== false;

  // VIKI's launcher: this mount's own window id for its presence reports, the panel's line
  // after a refused action, and the chats the launcher view says VIKI started (space-joined),
  // which the session list marks. Whether its panel is open is `launcherOpen`, above.
  const [decisionWindowId] = useState(() => crypto.randomUUID());
  const [launcherNotice, setLauncherNotice] = useState<string | null>(null);
  const [vikiStarted, setVikiStarted] = useState("");
  const vikiStartedIds = useMemo(() => new Set(vikiStarted.split(" ")), [vikiStarted]);
  // VIKI's own chat: the saved `decision.launcher_session`, unless this window has started or
  // cleared one since while the settings document still holds the value that change replaced
  // (`over`): its save is still on its way, or was refused.
  const savedLauncherChat = effectiveSettings?.["decision.launcher_session"];
  const [launcherChatOverride, setLauncherChatOverride] = useState<{
    id: string | null;
    over: unknown;
  } | null>(null);
  const launcherChatId =
    launcherChatOverride !== null && launcherChatOverride.over === savedLauncherChat
      ? launcherChatOverride.id
      : typeof savedLauncherChat === "string" && UUID.test(savedLauncherChat)
        ? savedLauncherChat
        : null;
  // VIKI's own voice conversation, bound to VIKI's chat in the open panel; closing the panel ends
  // it (`chatShown`). Its session key changes, ending a running loop, whenever VIKI's chat id
  // changes from a real one: New conversation, or another window's chat arriving with the settings
  // document, whose answers the loop must not read out or send into. A change from null keeps the
  // key, so this window's own first send, which turns a null id into a real one, keeps the loop
  // running. `launcherVoiceChat` is the id the key last followed, adjusted while rendering.
  const [launcherVoiceEpoch, setLauncherVoiceEpoch] = useState(0);
  const [launcherVoiceChat, setLauncherVoiceChat] = useState(launcherChatId);
  if (launcherVoiceChat !== launcherChatId) {
    setLauncherVoiceChat(launcherChatId);
    if (launcherVoiceChat !== null) setLauncherVoiceEpoch((epoch) => epoch + 1);
  }
  const launcherVoice = useHandsFree({
    dictationApi,
    view: launcherChatId === null ? undefined : appSnapshot.sessions[launcherChatId],
    readAloud: readAloudSettings,
    sessionKey: `launcher:${launcherVoiceEpoch}`,
    parser: markdownParser,
    chatShown: launcherPanelOpen,
  });
  // Alt+T with the panel closed: the toggle waits for the render where the panel shows, since the
  // `chatShown` flip voids an entry started before it.
  const pendingLauncherVoice = useRef(false);
  /** Alt+T (useLauncherShortcuts' "voice", after it opened a closed panel): starts or ends VIKI's
   * voice conversation; without dictation it does nothing more. */
  function toggleLauncherVoice(): void {
    if (!dictationApi) return;
    if (!launcherPanelOpen) {
      pendingLauncherVoice.current = true;
      return;
    }
    launcherVoice.toggle();
  }
  // biome-ignore lint/correctness/useExhaustiveDependencies: only the panel opening or closing runs this; the toggle it calls is this same render's.
  useEffect(() => {
    if (!launcherPanelOpen) {
      pendingLauncherVoice.current = false;
      return;
    }
    if (!pendingLauncherVoice.current) return;
    pendingLauncherVoice.current = false;
    launcherVoice.toggle();
  }, [launcherPanelOpen]);
  /** The panel's Escape: ends VIKI's voice conversation and keeps the panel (true); with
   * the loop off, false, so the panel closes. */
  function endLauncherVoice(): boolean {
    if (launcherVoice.state.phase === "off") return false;
    launcherVoice.toggle();
    return true;
  }
  // The panel's replies offer no Read (renderMessagesFor's `panel`): VIKI's voice
  // conversation already speaks its answers, a Read started there would end that conversation
  // (useHandsFree's half-duplex `read` exit), the small chat stays uncluttered, and Open in main
  // window offers Read.
  const panelReadAloud = useMemo(
    () => ({
      ...readAloudSettings,
      controller: { ...readAloudSettings.controller, supported: false },
    }),
    [readAloudSettings],
  );

  function openDecisionTab(origin?: HTMLElement, callId?: string): void {
    if (contextApi && navigate({ view: "context" }, undefined, origin)) {
      setDecisionRequest((request) => (request ?? 0) + 1);
      setDecisionRequestedCallId(callId ?? null);
    }
  }

  /** A chat the launcher names (an item's chat, VIKI's own): in the chat view, selected when this
   * window holds it, else opened existing-only. `origin` is the open panel. */
  function showLauncherChat(sessionId: string, origin?: HTMLElement): void {
    if (view !== "chat" && !navigate({ view: "chat" }, undefined, origin)) return;
    if (viki.store.getSnapshot().sessions[sessionId]) select(sessionId);
    else openSession(sessionId, true);
  }

  /** An item's open_* action, through App's own openers with the open panel as the modal origin.
   * A note or the inbox opens only in the vault on screen, where the item's ref belongs. */
  function openFromLauncher(action: ItemAction): void {
    const origin = launcherPanelOrigin();
    const vaultOpen =
      Boolean(vaultApi) &&
      vaultSurfaces(shellSnapshot) &&
      shellSnapshot.workspaces?.chat_only === false;
    switch (action.type) {
      case "open_chat":
        if (action.session_id) showLauncherChat(action.session_id, origin);
        return;
      case "open_note":
        if (vaultOpen && action.ref && action.workspace_id === shellSnapshot.workspaces?.active_id)
          openVaultNote(action.ref, origin);
        return;
      case "open_approvals":
        showDecisions(true);
        return;
      case "open_inbox":
        if (vaultOpen) openVault("00-inbox", origin);
        return;
      case "open_vault":
        if (vaultOpen && action.workspace_id === shellSnapshot.workspaces?.active_id)
          openVault("", origin);
        return;
      case "open_triage":
        if (vaultOpen && capturesApi) openTriage();
        return;
      case "open_messaging":
        if (messagingApi) navigate({ view: "messaging" }, undefined, origin);
        return;
      case "open_code":
        if (action.workspace_id) viki.actions.openCode(action.workspace_id, "", null);
        return;
      case "open_routine":
        if (routinesStore && action.routine_id)
          navigate({ view: "routines" }, action.routine_id, origin);
        return;
      case "open_runs": {
        const run = firstRun(appSnapshot.sessions, reviewedRuns);
        if (run) {
          if (navigate({ view: "chat" }, undefined, origin)) jumpToRun(run.sessionId, run.blockId);
        } else if (routinesStore) navigate({ view: "routines" }, undefined, origin);
        return;
      }
      case "open_context":
        openDecisionTab(origin, action.call_id);
        return;
      case "draft_chat":
        // Keyed by the target chat's own id, not the (still stale this render) editorKey
        // showLauncherChat's own select() is about to move: see appendToDraft's own note.
        if (action.session_id && action.text) {
          showLauncherChat(action.session_id, origin);
          appendToDraft(action.text, action.session_id);
        }
        return;
      case "draft_new":
        if (action.text && navigate({ view: "chat" }, undefined, origin)) restoreText(action.text);
        return;
    }
  }

  /** The launcher's Open Decision, Turn on in Decision and the weekly check's Check: the
   * Decision page at the given row, "proactive" by default. */
  function openDecisionFromLauncher(row: string = "proactive"): void {
    if (navigate({ view: "decision" }, undefined, launcherPanelOrigin())) setDecisionRow(row);
  }

  function openLauncherPanel(): void {
    setLauncherNotice(null);
    setLauncherOpen(true);
    // VIKI's chat from an earlier launch: opened existing-only, so its transcript shows.
    if (
      launcherChatId !== null &&
      !archivingNow.current.has(launcherChatId) &&
      !viki.store.getSnapshot().sessions[launcherChatId]
    )
      viki.actions.openExistingSession(launcherChatId);
  }

  function closeLauncherPanel(): void {
    setLauncherNotice(null);
    setLauncherOpen(false);
  }

  /** VIKI's chat's send: the first starts a chat of its own, saved as
   * `decision.launcher_session`; later ones go to that chat, opened existing-only when this
   * window does not hold it. False, with the panel's line, when no send was confirmed. */
  async function sendToLauncherChat(text: string): Promise<boolean> {
    setLauncherNotice(null);
    let id = launcherChatId;
    if (
      viki.store.getSnapshot().status !== "open" ||
      (id !== null && archivingNow.current.has(id)) ||
      (id === null && (sessionsBlocked || liveIds.size >= maxLiveSessions))
    ) {
      // The limit alone, with nothing else wrong — the socket open, no id to miss and no other
      // refusal — gets the full sentence (the number and the way out); every other refusal here,
      // a dropped socket included, keeps the generic "didn't confirm" line.
      setLauncherNotice(
        viki.store.getSnapshot().status === "open" &&
          id === null &&
          !sessionsBlocked &&
          liveIds.size >= maxLiveSessions
          ? t("shell.limit.full")
          : LAUNCHER_SEND_UNCONFIRMED,
      );
      return false;
    }
    if (id === null) {
      id = crypto.randomUUID();
      // Minted here for a plain chat, so no profile can be bound to it.
      handleUnboundSession(id);
      viki.actions.newSession(id);
      startedHereRef.current.add(id);
      setLauncherChatOverride({ id, over: savedLauncherChat });
      if (decisionWritable)
        void shellStore.patch({ "decision.launcher_session": id }).catch(() => {});
    } else if (!viki.store.getSnapshot().sessions[id]) viki.actions.openExistingSession(id);
    const sent = await viki.actions.send(id, text);
    if (!sent) setLauncherNotice(LAUNCHER_SEND_UNCONFIRMED);
    return sent;
  }

  /** The open panel's foot: its line after a refused action, then VIKI's own chat. Its mic is the
   * window's DictationContext, as the main box's is; with one, it also offers VIKI's voice
   * conversation (`launcherVoice`, Alt+T). */
  function renderLauncherChat(): ReactNode {
    const chatView = launcherChatId === null ? undefined : appSnapshot.sessions[launcherChatId];
    return (
      <>
        {launcherNotice ? (
          <p className="dloop-error" role="alert">
            {launcherNotice}
          </p>
        ) : null}
        <LauncherChat
          sessionId={launcherChatId}
          messages={
            launcherChatId === null ? null : renderMessagesFor(launcherChatId, chatView, true)
          }
          running={chatView?.turns.at(-1)?.status === "running"}
          onSend={sendToLauncherChat}
          onStop={() => {
            if (launcherChatId !== null) void viki.actions.interrupt(launcherChatId);
          }}
          onNewConversation={() => {
            setLauncherChatOverride({ id: null, over: savedLauncherChat });
            if (decisionWritable)
              void shellStore.patch({ "decision.launcher_session": "" }).catch(() => {});
          }}
          onOpenInMain={() => {
            if (launcherChatId === null) return;
            const origin = launcherPanelOrigin();
            closeLauncherPanel();
            showLauncherChat(launcherChatId, origin);
          }}
          {...(dictationApi
            ? { handsFree: launcherVoice, voiceShortcut: launcherShortcutHint("voice") }
            : {})}
        />
      </>
    );
  }

  /** `messages`: <StaleWindow/> before anything else once the token is dead (nothing below can
   * work either way); else EmptyState with nothing selected or a selection with no turn, card,
   * or blocked/rate-limit/error state at all; else the transcript, its open cards in arrival
   * order, and any of BlockedCard/RateLimitCard/ErrorCard the view is carrying. HistoryNote
   * renders in the composer slot instead (below it belongs beside the pill it describes, not in
   * the scroll box it would otherwise clamp to and scroll away with — see the App docstring).
   *
   * The main column passes the selection; a call for any other chat (another pane's) gets no
   * main-only state: turn selection and its quote, branch and reuse actions, the search jump, the
   * history gap and Retry… (it fills the main composer). With tabs, every chat is its tab's
   * panel; every chat keeps its own saved scroll. `panel` is the launcher panel's VIKI chat:
   * never the main column's state, and no empty state, Read or tab panel. */
  function renderMessagesFor(
    sid: string | null,
    view: SessionView | undefined,
    panel = false,
  ): ReactNode {
    if (staleWindow) return <StaleWindow />;
    const main = !panel && sid === selectedId;
    if (
      main &&
      (handoffBlocked || (view?.openRefused && view.openRefused !== "model_review_required"))
    )
      return (
        <div role="status">
          <h2>
            {handoffActive && launchHandoff.kind === "invalid"
              ? "This conversation link is invalid."
              : view?.openRefused
                ? "This conversation is unavailable."
                : "Opening this conversation…"}
          </h2>
          {view?.openRefused === "storage_unavailable" ? (
            <p>Session storage is unavailable in this launch.</p>
          ) : null}
          <p>Choose another conversation in the sidebar to continue.</p>
          {sid && view?.openRefused ? (
            <button
              type="button"
              className="btn txt sm"
              onClick={() => {
                viki.actions.openExistingSession(sid);
                explicitVisit.current = {
                  id: sid,
                  requestId: viki.store.getSnapshot().sessions[sid]?.requestId ?? null,
                };
              }}
            >
              Try this conversation again
            </button>
          ) : null}
          <button type="button" className="btn txt sm" onClick={() => select(null)}>
            Go to conversations
          </button>
        </div>
      );
    const hasContent =
      view !== undefined &&
      (view.turns.length > 0 ||
        view.cards.length > 0 ||
        view.blocked !== null ||
        view.rateLimit !== null ||
        view.error !== null ||
        // A session opened from the rail or a search hit has none of the above for as long as
        // its first history page is in flight. Without this the greeting "New conversation" paints
        // over a months-old transcript for that moment — the one thing the empty state must
        // never say about a conversation the user is reopening.
        view.historyLoading ||
        view.historyError !== null);
    const landingActions =
      main &&
      capabilitiesAvailable &&
      !recovery &&
      !archiveOpen &&
      !runningComposer &&
      (!selectedIdState || selectedId !== null)
        ? {
            landingSessions: groups
              .flatMap((group) => group.rows)
              .slice(0, 6)
              .map((row) => ({
                sessionId: row.session_id,
                title: sessionLabel(row.session_id, restSnapshot.sessions, metadata.sessions),
                pinned: metadata.sessions.some(
                  (item) => item.session_id === row.session_id && item.pinned,
                ),
                ...(folderOf?.(row.session_id)
                  ? { description: folderOf(row.session_id) ?? "" }
                  : {}),
              })),
            onOpenSession: openSidebarSession,
          }
        : {};
    if (!view || !sid || !hasContent) return panel ? null : <EmptyState {...landingActions} />;
    return (
      <>
        {/* Keyed by session: the scroll box is Shell's and outlives any one conversation, so
            without this React reuses one Conversation across a switch and the incoming session
            inherits the outgoing one's scroll offset and auto-follow state — land mid-transcript
            with follow off and a streaming reply arrives below the fold. BlockedCard's one-shot
            latch leaks the same way, leaving its buttons dead on the next session's card. */}
        {view.historyError ? (
          <div className="history-note jp" role="status">
            {historyErrorCopy(view.historyError)}
            {view.historyError === "busy" ? (
              <button type="button" onClick={() => viki.actions.reloadHistory(sid)}>
                Reload
              </button>
            ) : null}
            {view.historyError !== "transcript_missing" ? (
              <button type="button" onClick={() => viki.actions.openTranscript(sid)}>
                Open conversation record
              </button>
            ) : null}
          </div>
        ) : null}
        {view.truncated ? (
          <div className="history-note jp" role="status">
            This window leaves out some of the older run content.
            <button type="button" onClick={() => viki.actions.openTranscript(sid)}>
              Open conversation record
            </button>
          </div>
        ) : null}
        {/* Skip this note whenever a note above already offers the same button, or says the
            record is missing (historyError === "transcript_missing"): history pages prepend
            (sessionReducer.ts's applyHistoryPage), so a view can hold cut history turns from an
            earlier page and then hit transcript_missing on an older one, and the two notes must
            not contradict each other. */}
        {view.historyError === null &&
        !view.truncated &&
        view.turns.some((turn) => turn.history && promptCut(turn)) ? (
          <div className="history-note jp" role="status">
            This window cuts off part of a long sent message partway through.
            <button type="button" onClick={() => viki.actions.openTranscript(sid)}>
              Open conversation record
            </button>
          </div>
        ) : null}
        <ArtifactContext.Provider
          value={conversationSearch ? { api: conversationSearch, sessionId: sid } : null}
        >
          <Conversation
            key={view.sessionId}
            view={view}
            readAloud={panel ? panelReadAloud : readAloudSettings}
            historyGap={
              main && historyGap?.session === sid && historyGap.generation === view.generation
            }
            jumpTo={main ? turnJump : undefined}
            tabPanel={!panel && Boolean(organization)}
            initialScroll={scrollBySession.current.get(view.sessionId)}
            onRememberScroll={(position: SavedScroll) =>
              scrollBySession.current.set(view.sessionId, position)
            }
            nowMs={now}
            // An empty display name is `undefined` here, so the component's own "You" stands.
            {...(displayName ? { userLabel: displayName } : {})}
            onOpenUrl={viki.actions.openUrl}
            onLoadOlder={() => viki.actions.loadOlder(sid)}
            {...(main
              ? {
                  onRetryTurn: (turn: Turn) =>
                    setRetryTurn({ turn, incomplete: selectedView?.truncated ?? false }),
                }
              : {})}
            {...(excerpts && activeWorkspaceId
              ? {
                  excerptApi: excerpts,
                  workspaceId: activeWorkspaceId,
                  // The citation's own Open note. The browser opens a note, not a line, so the
                  // excerpt's first line is not used here.
                  onOpenVault: (ref: string) => {
                    openVaultNote(ref);
                  },
                }
              : {})}
            {...(archives && main
              ? {
                  selectedTurns,
                  onSelectTurn: (id: string, selected: boolean) =>
                    setSelectedTurns((current) => {
                      const next = new Set(current);
                      if (selected) next.add(id);
                      else next.delete(id);
                      return next;
                    }),
                  onQuoteTurn: quoteIntoDraft,
                  onBranchTurn: (turn: Turn) =>
                    restoreText(`${quotedTurn(turn)}\n\n`, turn.user?.attachments),
                  onReusePrompt: (turn: Turn) => {
                    const user = turn.user;
                    if (!user) return;
                    if (!promptCut(turn)) {
                      restoreText(withoutVoiceTag(rejoinPrompt(user)), user.attachments);
                      return;
                    }
                    // Only this window's copy was cut: opening it would pass part of the prompt
                    // off as the whole. The composer's refusal line says where the full text is.
                    setRefusalNote(
                      cutPromptNotice(turn, false, view.historyError === "transcript_missing"),
                    );
                  },
                }
              : {})}
            {...(main &&
            skillProposals &&
            decisions &&
            shellSnapshot.capabilities?.workspace.active?.kind === "vault"
              ? {
                  renderTurnActions: (turn: Turn) => (
                    <SkillTurnActions
                      turn={turn}
                      view={view}
                      sessions={restSnapshot.sessions}
                      api={skillProposals}
                      workspaceId={activeWorkspaceId ?? ""}
                      hintsOff={skillHintsOff.has(sid)}
                      onHintsOff={() =>
                        setSkillHintsOff((prev) => (prev.has(sid) ? prev : new Set(prev).add(sid)))
                      }
                      onDraft={appendToDraft}
                      onOpenProposal={(item) => {
                        showDecisions(true);
                        if (item) setInspecting(item);
                      }}
                      hintInLauncher={hintInLauncher}
                      {...(decisionApi ? { questions: decisionApi } : {})}
                      onOpenQuestionProposal={(item) => {
                        showDecisions(true);
                        setInspecting(item);
                      }}
                    />
                  ),
                }
              : {})}
          />
        </ArtifactContext.Provider>
        {/* The cards sit inside the reply body, past VIKI's 36px face and
            its 14px gap — it is something VIKI is asking, not something the column is. The
            cards cannot live inside <Conversation/> (they can be open with no turn at all), so
            the offset is theirs to carry. BlockedCard below keeps the full width: it sits at
            column level, because it is about the session, not about a reply. */}
        <div className="card-column">
          {view.cards.map((card) => renderCard(card, sid, view, !panel))}
        </div>
        {view.blocked ? (
          <BlockedCard
            key={view.sessionId}
            data={view.blocked}
            onFork={() => armFork(sid)}
            onClose={() => requestClose(sid)}
          />
        ) : null}
        {view.rateLimit ? <RateLimitCard data={view.rateLimit} /> : null}
        {view.error ? (
          <ErrorCard
            data={view.error}
            // Submitted-text recovery below lets the user inspect uncertain attempts first.
            onRaiseLimit={openLimitSetting}
          />
        ) : null}
        {view.error?.code === "not_logged_in" && signIn ? <SignInStrip api={signIn} /> : null}
      </>
    );
  }

  /** The top bar's toggle for a rail the window cannot dock: opens its drawer, or closes it. */
  function railToggle(side: "left" | "right") {
    return { open: drawer === side, onToggle: () => setDrawer(drawer === side ? null : side) };
  }

  const shellApi = shell ?? null;

  /** The four settings pages this lane does not build; the three document pages are
   * `SettingsView`'s own and arrive as `null` here. */
  function settingsPage(): ReactNode {
    switch (nav.page) {
      case "appearance":
        return <AppearancePage store={shellStore} onPreview={setAppearancePreview} />;
      case "providers":
        return (
          <ProvidersPage
            store={shellStore}
            snapshot={shellSnapshot}
            onOpenUrl={viki.actions.openUrl}
          />
        );
      case "workspaces":
        return (
          <WorkspacesPage
            api={shellApi}
            store={shellStore}
            snapshot={shellSnapshot}
            onCommand={onCommand}
            onOpenInCode={(id) => viki.actions.openCode(id, "", null)}
          />
        );
      case "persona":
        return (
          <PersonaPage
            api={vaultApi ?? null}
            vault={activeVault ?? null}
            assistantName={identity.assistantName}
            displayName={displayName}
            {...(personaNextLaunch !== undefined ? { nextLaunch: personaNextLaunch } : {})}
            onDraft={(text) => restoreText(text)}
            onBack={() => navigate({ view: "chat" })}
            onRefreshVault={() => void shellStore.afterMutation(["capabilities", "workspaces"])}
          />
        );
      case "diagnostics":
        return (
          <DiagnosticsPage
            api={shellApi}
            snapshot={shellSnapshot}
            onNavigate={navigate}
            approvalsRead={{
              status: attention.status,
              readAt: attention.readAt,
              workspaceId: attention.workspaceId,
              attention: attention.items?.filter((item) => item.attention).length ?? null,
            }}
          />
        );
      default:
        return null;
    }
  }

  function openSkills(): void {
    if (shellStore.getSnapshot().recovery || !nativeAssetsApi || !profileApi) return;
    if (!navigate({ view: "capabilities" })) return;
    setSkillsVisit((visit) => visit + 1);
  }

  function openProfiles(): void {
    if (shellStore.getSnapshot().recovery || !profileApi) return;
    if (!botsAvailable) {
      if (view === "agents") {
        if (agentsSection !== "profiles" && !confirmLeaveUnsaved()) return;
      } else if (!navigate({ view: "agents" })) return;
      setAgentsSection("profiles");
      return;
    }
    if (view === "bots") {
      if (botsSection !== "profiles" && !confirmLeaveUnsaved()) return;
    } else if (!navigate({ view: "bots" })) return;
    setBotsSection("profiles");
  }

  function openSidebarSession(sessionId: string): void {
    if (view === "chat" || navigate({ view: "chat" }, undefined, sidebarModalOrigin()))
      openSession(sessionId);
  }

  function sidebarModalOrigin(): HTMLElement | undefined {
    return drawer === "left"
      ? (document.querySelector<HTMLElement>('[data-region="left-drawer"][aria-modal="true"]') ??
          undefined)
      : undefined;
  }

  const botRoutineProps: Omit<BotRoutinesProps, "bot"> | undefined =
    routinesApi && routinesStore
      ? {
          api: routinesApi,
          snapshot: routinesSnapshot,
          sessions: appSnapshot.sessions,
          nowMs: now,
          ...(profileApi ? { profiles: profileApi } : {}),
          workspaces: shellSnapshot.workspaces?.workspaces ?? [],
          controls: routineControls,
          onSettings: async (changes) => {
            await shellStore.patch(changes);
          },
          onRequestPermission: async () => {
            await notifier.request();
          },
          onRefresh: routinesStore.refresh,
          onOpenSession: (sessionId) => {
            if (navigate({ view: "chat" })) openSession(sessionId, true);
          },
          onStop: (sessionId) => viki.actions.interrupt(sessionId),
        }
      : undefined;

  /** Every view but the chat. Each one focuses its own heading on entry — App focuses nothing,
   * because a parent effect runs after its children and would steal the focus the data view
   * just put on the panel the user asked for. The one heading App renders itself, the Vault
   * view without a vault, focuses through its own ref. */
  function renderBotProfiles(): ReactNode {
    if (!profileApi) return null;
    return (
      <ProfilesView
        title={view === "bots" ? t("bots.config") : "Profiles"}
        api={profileApi}
        botSettings={view === "bots"}
        defaultModel={
          typeof shellSnapshot.settings?.effective["chat.default_model"] === "string"
            ? shellSnapshot.settings.effective["chat.default_model"]
            : null
        }
        {...(botsApi && botsAvailable
          ? {
              bots: botsApi,
              onOpenBot: (id: string) => {
                showBot(id);
              },
            }
          : {})}
        {...(profileLaunchApi ? { launchApi: profileLaunchApi } : {})}
        {...(shellSnapshot.workspaces && !shellSnapshot.workspaces.chat_only
          ? { workspaceId: shellSnapshot.workspaces.active_id }
          : {})}
        {...(shellSnapshot.workspaces ? { workspaces: shellSnapshot.workspaces.workspaces } : {})}
        {...(profileFocus ? { initialSelectedId: profileFocus } : {})}
        onPrepared={(sessionId, preparedIdentity) => {
          // Recorded before the chat opens, so it renders bound from its first frame.
          handleProfileSession(sessionId, preparedIdentity);
          openSession(sessionId);
          navigate({ view: "chat" });
        }}
      />
    );
  }

  function renderRunningWork(): ReactNode {
    return (
      <RunningWork
        rows={runningRows}
        maxLiveSessions={maxLiveSessions}
        others={appSnapshot.others}
        inspect={profileLaunchApi?.inspect ?? inspectNever}
        nowMs={now}
        botProfileIdOf={(id) =>
          botSessions.get(id) ?? (botChat?.sessionId === id ? botChat.bot.profile_id : undefined)
        }
        onOpen={(sessionId) => {
          if (navigate({ view: "chat" })) openSession(sessionId);
        }}
        onStop={(sessionId) => viki.actions.interrupt(sessionId)}
        onClose={requestClose}
      />
    );
  }

  /** Kanban's wake (SN §E): one `turn.send` into the seat Bot's own chat, as if the owner typed
   * it. In a vault Kanban saves to, this is the fixed wake, sent once `useCardAnswer`'s undo
   * window runs out and never for Approve; in any other vault, it is the answer message the
   * owner read in full and chose **Send message** for, Approve included. Never selects or shows
   * that chat. Never steers a running reply: idle is checked before and after `prepareTarget`,
   * and the server also refuses a send mid-turn (SessionActor.send's turn_running). */
  async function wakeBot(bot: BotRow, text: string): Promise<WakeResult> {
    if (viki.store.getSnapshot().status !== "open" || bot.chat.archived || !botsApi)
      return "failed";
    const id = bot.chat_session_id;
    const idle = () => {
      const view = viki.store.getSnapshot().sessions[id];
      return !view || presence(view) === "idle";
    };
    if (!idle()) return "busy";
    const tracked = Boolean(viki.store.getSnapshot().sessions[id]);
    if (!tracked && (sessionsBlocked || liveIds.size >= maxLiveSessions)) return "failed";
    let answer: Awaited<ReturnType<BotsApi["chat"]>>;
    try {
      answer = await botsApi.chat(bot);
    } catch {
      return "failed";
    }
    const preview = answer.preview;
    if (
      !("session_id" in preview) ||
      !preview.launchable ||
      preview.session_id !== bot.chat_session_id ||
      answer.bot_id !== bot.bot_id ||
      preview.workspace_id !== activeVault?.workspace_id
    )
      return "failed";
    handleProfileSession(id, preview.identity ?? null);
    markBotSession(id, bot.profile_id);
    if (!tracked) viki.actions.openExistingSession(id);
    if ((await viki.actions.prepareTarget(id)) === null) return "failed";
    if (!idle()) return "busy";
    if (await viki.actions.send(id, text)) return "sent";
    return idle() ? "failed" : "busy";
  }

  function renderView(): ReactNode {
    if (view === "bot_threads" && botsApi && botsAvailable)
      return (
        <>
          {renderBack(goBack)}
          <BotThreadWorkspace
            key={teamVaultId ?? "no-vault"}
            api={botsApi}
            store={botThreads}
            snapshot={botThreadSnapshot}
            workspaceId={teamVaultId ?? null}
            canStart={typeof teamVaultId === "string"}
            canSend={!sessionsBlocked && appSnapshot.status === "open"}
            sendBlockedReason={
              appSnapshot.status === "open" ? readinessSentence() : t("bots.thread.disconnected")
            }
            sessions={appSnapshot.sessions}
            assets={assets}
            activityOf={(id) => activityFor(id ? appSnapshot.sessions[id] : undefined)}
            onOpenSession={(id) => {
              if (navigate({ view: "chat" })) openSession(id, true);
            }}
            renderTurn={(turn, sessionId) => (
              <IdentityContext.Provider value={identityFor(sessionId)}>
                <Reply turn={displayThreadTurn(turn)} onOpenUrl={viki.actions.openUrl} />
              </IdentityContext.Provider>
            )}
            renderCards={(session) =>
              session.cards.map((card) => renderCard(card, session.sessionId, session))
            }
          />
        </>
      );
    if (view === "kanban" && botsAvailable)
      return (
        <>
          {renderBack(goBack)}
          {vaultApi && activeVault?.kind === "vault" && !codeActive ? (
            <KanbanView
              key={activeVault.workspace_id}
              api={vaultApi}
              workspaceId={activeVault.workspace_id}
              onOpenNote={openVaultNote}
              {...(botsApi ? { botsApi } : {})}
              readiness={teamReadiness}
              onOpenSeat={(seat) => {
                if (showBot(null)) setSeatRequest(seat);
              }}
              onRefresh={() => setTeamCheck((check) => check + 1)}
              {...(writeScope ? { write: { api: vaultApi, ...writeScope } } : {})}
              {...(botsApi ? { onWake: wakeBot } : {})}
            />
          ) : (
            <div className="bots-view">
              <h1 ref={focusOnMount} tabIndex={-1}>
                {t("kanban.title")}
              </h1>
              <p>{t("kanban.no_vault")}</p>
            </div>
          )}
        </>
      );
    if (view === "bots")
      return botsApi && botsAvailable ? (
        <>
          {renderBack(goBack)}
          <nav className="bot-thread-tabs" aria-label="Bots sections">
            <button
              type="button"
              className="btn alt"
              aria-current={botsSection === "team" ? "page" : undefined}
              onClick={() => {
                if (botsSection === "team" || confirmLeaveUnsaved()) setBotsSection("team");
              }}
            >
              Team
            </button>
            <button
              type="button"
              className="btn alt"
              aria-current={botsSection === "profiles" ? "page" : undefined}
              onClick={openProfiles}
            >
              {t("bots.config")}
            </button>
            {botUsageApi ? (
              <button
                type="button"
                className="btn alt"
                aria-current={botsSection === "usage" ? "page" : undefined}
                onClick={() => {
                  if (botsSection === "usage" || confirmLeaveUnsaved()) setBotsSection("usage");
                }}
              >
                {t("botUsage.nav.usage")}
              </button>
            ) : null}
          </nav>
          {botsSection === "profiles" ? (
            renderBotProfiles()
          ) : botsSection === "usage" && botUsageApi ? (
            <BotUsage
              api={botUsageApi}
              onOpenSession={(id) => {
                if (navigate({ view: "chat" })) openSession(id, true);
              }}
            />
          ) : (
            <BotsView
              api={botsApi}
              {...(profileApi ? { profiles: profileApi } : {})}
              {...(profileLaunchApi ? { launch: profileLaunchApi } : {})}
              workspaces={shellSnapshot.workspaces?.workspaces ?? []}
              activeWorkspaceId={activeWorkspaceId ?? ""}
              selectedId={selectedBot}
              onSelect={(id) => {
                leaveBotsAfterAdmission("bots", id);
              }}
              onBack={() => {
                leaveBotsAfterAdmission("chat");
              }}
              onCreateProfile={() => {
                if (leaveBotsAfterAdmission("bots")) {
                  setBotsSection("profiles");
                }
              }}
              team={{ live: teamLive, readiness: teamReadiness }}
              initialSeat={seatRequest}
              onEditProfile={(id) => {
                if (leaveBotsAfterAdmission("bots")) {
                  setBotsSection("profiles");
                  setProfileFocus(id);
                }
              }}
              onRecheckTeam={() => setTeamCheck((check) => check + 1)}
              {...(botMemoryApi
                ? {
                    memoryApi: botMemoryApi,
                    onOpenNote: openVaultNote,
                    onOpenUrl: viki.actions.openUrl,
                    ...(activeVault ? { onOpenInEditor: botMemoryEditorOpener(activeVault) } : {}),
                  }
                : {})}
              {...(botMemoryApi && botsApi
                ? {
                    onAskInMainChat: async (bot: BotRow): Promise<void> => {
                      const navigation = currentNavigation.current;
                      const workspaceId = bot.workspace_id;
                      const workspaceGeneration = activeWorkspaceGeneration.current;
                      const isCurrent = (expectedNavigation: Navigation) => {
                        const current = shellStore.getSnapshot();
                        return (
                          currentNavigation.current === expectedNavigation &&
                          selectedBotRef.current === bot.bot_id &&
                          activeWorkspaceGeneration.current === workspaceGeneration &&
                          current.capabilities?.workspace.active?.id === workspaceId &&
                          current.workspaces?.active_id === workspaceId &&
                          current.workspaces.workspaces.some(
                            (row) =>
                              row.workspace_id === workspaceId &&
                              row.kind === "vault" &&
                              row.policy === bot.workspace?.policy &&
                              row.persistent &&
                              row.root_available,
                          )
                        );
                      };
                      if (
                        navigation.view !== "bots" ||
                        !isCurrent(navigation) ||
                        bot.workspace_id !== activeVault?.workspace_id ||
                        bot.workspace?.active !== true ||
                        bot.workspace.policy !== activeVault?.policy
                      )
                        return;
                      let answer: Awaited<ReturnType<BotsApi["chat"]>>;
                      try {
                        answer = await botsApi.chat(bot);
                      } catch (error) {
                        if (!isCurrent(navigation)) return;
                        setSendRefusals((current) => ({
                          ...current,
                          [bot.chat_session_id]: botErrorText(error),
                        }));
                        if (!leaveBotsAfterAdmission("chat", bot.bot_id)) return;
                        handleProfileSession(bot.chat_session_id, null);
                        markBotSession(bot.chat_session_id, bot.profile_id);
                        openSession(bot.chat_session_id, true);
                        return;
                      }
                      if (!isCurrent(navigation)) return;
                      const preview = answer.preview;
                      if (
                        !("session_id" in preview) ||
                        !preview.launchable ||
                        answer.bot_id !== bot.bot_id ||
                        answer.state_revision !== bot.state_revision ||
                        preview.session_id !== bot.chat_session_id ||
                        preview.profile_id !== bot.profile_id ||
                        preview.profile_revision !==
                          (bot.chat.profile_revision ?? bot.profile?.revision) ||
                        preview.workspace_id !== workspaceId ||
                        (preview.bot_memory && preview.bot_memory.bot_id !== bot.bot_id)
                      ) {
                        setSendRefusals((current) => ({
                          ...current,
                          [bot.chat_session_id]: t("bots.memory.draftError"),
                        }));
                        if (!leaveBotsAfterAdmission("chat", bot.bot_id)) return;
                        handleProfileSession(bot.chat_session_id, null);
                        markBotSession(bot.chat_session_id, bot.profile_id);
                        openSession(bot.chat_session_id, true);
                        return;
                      }
                      if (!leaveBotsAfterAdmission("chat", bot.bot_id)) return;
                      const openedNavigation = currentNavigation.current;
                      handleProfileSession(preview.session_id, preview.identity ?? null);
                      markBotSession(preview.session_id, bot.profile_id);
                      openSession(preview.session_id, true);
                      const openedSelection = selectionGeneration.current;
                      if (
                        selectionRef.current !== preview.session_id ||
                        !isCurrent(openedNavigation)
                      )
                        return;
                      if (!preview.bot_memory || preview.bot_memory.bot_id !== bot.bot_id) {
                        setSendRefusals((current) => ({
                          ...current,
                          [preview.session_id]: t("bots.memory.noBinding"),
                        }));
                        return;
                      }
                      try {
                        await editors.ensure(preview.session_id);
                        if (
                          selectionGeneration.current !== openedSelection ||
                          selectionRef.current !== preview.session_id ||
                          !isCurrent(openedNavigation)
                        )
                          return;
                        const draft = editors.getSnapshot().editors[preview.session_id];
                        if (
                          draft?.status !== "saved" ||
                          draft.pendingCommand ||
                          draft.revision !== preview.draft.revision ||
                          draft.text !== preview.draft.text ||
                          JSON.stringify(draft.attachments) !==
                            JSON.stringify(preview.draft.attachments)
                        ) {
                          setSendRefusals((current) => ({
                            ...current,
                            [preview.session_id]: t("bots.memory.draftError"),
                          }));
                          return;
                        }
                        const request = tf("bots.memory.changeRequest", {
                          ref: botMemoryFolder(preview.bot_memory),
                        });
                        editors.edit(
                          preview.session_id,
                          `${draft.text}${draft.text ? "\n\n" : ""}${request}`,
                        );
                      } catch {
                        if (
                          selectionGeneration.current !== openedSelection ||
                          selectionRef.current !== preview.session_id ||
                          !isCurrent(openedNavigation)
                        )
                          return;
                        setSendRefusals((current) => ({
                          ...current,
                          [preview.session_id]: t("bots.memory.draftError"),
                        }));
                      }
                    },
                  }
                : {})}
              onOpenChat={(preview, bot) => {
                if (
                  selectedBotRef.current !== bot.bot_id ||
                  preview.workspace_id !== shellStore.getSnapshot().workspaces?.active_id ||
                  preview.session_id !== bot.chat_session_id ||
                  preview.profile_id !== bot.profile_id
                )
                  return;
                if (!leaveBotsAfterAdmission("chat")) return;
                handleProfileSession(preview.session_id, preview.identity ?? null);
                markBotSession(preview.session_id, bot.profile_id);
                openSession(preview.session_id, true);
              }}
              onOpenThread={async (preview, bot, request, placeFirst) => {
                const stillHere = () =>
                  selectedBotRef.current === bot.bot_id &&
                  preview.workspace_id === shellStore.getSnapshot().workspaces?.active_id &&
                  preview.profile_id === bot.profile_id;
                if (!stillHere()) return false;
                if (request && placeFirst) {
                  // The user's own text lives only in the editor that asked: leaving Bots unmounts
                  // it, so the draft is checked before leaving and a refusal leaves the text there.
                  // The edit itself comes last, so a refused leave places nothing to repeat.
                  const sessionId = preview.session_id;
                  const workspaceGeneration = activeWorkspaceGeneration.current;
                  if (!editors.capacityAvailable(sessionId)) return false;
                  try {
                    await editors.ensure(sessionId);
                  } catch {
                    return false;
                  }
                  if (
                    !stillHere() ||
                    activeWorkspaceGeneration.current !== workspaceGeneration ||
                    currentNavigation.current.view !== "bots"
                  )
                    return false;
                  const draft = editors.getSnapshot().editors[sessionId];
                  const text = `${draft?.text ?? ""}${draft?.text ? "\n\n" : ""}${request}`;
                  if (
                    draft?.status !== "saved" ||
                    draft.pendingCommand ||
                    draft.revision !== preview.draft.revision ||
                    draft.text !== preview.draft.text ||
                    !draftFits(text)
                  )
                    return false;
                  if (!leaveBotsAfterAdmission("chat")) return false;
                  handleProfileSession(sessionId, preview.identity ?? null);
                  markBotSession(sessionId, bot.profile_id);
                  openSession(sessionId, true);
                  editors.edit(sessionId, text);
                  return true;
                }
                if (!leaveBotsAfterAdmission("chat")) return false;
                handleProfileSession(preview.session_id, preview.identity ?? null);
                markBotSession(preview.session_id, bot.profile_id);
                openSession(preview.session_id, true);
                if (!request) return true;
                const refuseRequest = () => {
                  setSendRefusals((current) => ({
                    ...current,
                    [preview.session_id]: t("bots.memory.draftError"),
                  }));
                  return false;
                };
                return editors
                  .ensure(preview.session_id)
                  .then(() => {
                    const draft = editors.getSnapshot().editors[preview.session_id];
                    if (
                      draft?.status !== "saved" ||
                      draft.pendingCommand ||
                      draft.revision !== preview.draft.revision ||
                      draft.text !== preview.draft.text
                    )
                      return refuseRequest();
                    editors.edit(
                      preview.session_id,
                      `${draft.text}${draft.text ? "\n\n" : ""}${request}`,
                    );
                    return true;
                  })
                  .catch(refuseRequest);
              }}
              onOpenSession={(id) => {
                if (navigate({ view: "chat" })) openSession(id, true);
              }}
              {...(organization
                ? {
                    onUnarchive: async (id: string) =>
                      (await updateSessionMetadata(id, { archived: false })) !== null,
                  }
                : {})}
              {...(botRoutineProps ? { routines: botRoutineProps } : {})}
              onThreads={() => leaveBotsAfterAdmission("bot_threads")}
            />
          )}
        </>
      ) : (
        <>
          {renderBack(goBack)}
          <h1 ref={focusOnMount} tabIndex={-1}>
            Bots
          </h1>
          <p>Bots require a ready app store and profile clients.</p>
          <button type="button" className="btn alt" onClick={() => navigate({ view: "chat" })}>
            Back to chat
          </button>
        </>
      );
    if (view === "messaging" && messagingApi)
      return (
        <>
          {renderBack(goBack)}
          <MessagingView
            api={messagingApi}
            onOpenUrl={viki.actions.openUrl}
            {...(capabilitiesAvailable
              ? { onCapabilities: () => navigate({ view: "capabilities" }) }
              : {})}
          />
        </>
      );
    if (view === "decision" && decisionApi) {
      return (
        <>
          {renderBack(goBack)}
          <DecisionLoopPage
            api={decisionApi}
            values={shellSnapshot.settings?.effective ?? {}}
            writable={decisionWritable}
            patch={(changes) => shellStore.patch(changes)}
            onOpenUrl={viki.actions.openUrl}
            onOpenContext={() => openDecisionTab()}
            onOpenChat={(sessionId) => {
              if (navigate({ view: "chat" })) openSession(sessionId, true);
            }}
            {...(decisionRow ? { openRow: decisionRow } : {})}
          />
        </>
      );
    }
    if (view === "capabilities" && capabilitiesAvailable)
      return (
        <>
          {renderBack(goBack)}
          <h1 ref={focusOnMount} tabIndex={-1}>
            Capabilities
          </h1>
          <CapabilitiesView
            chats={runningRows}
            {...(nativeAssetsApi ? { nativeApi: nativeAssetsApi } : {})}
            {...(inventoryApi ? { inventoryApi } : {})}
            {...(profileApi ? { profileApi, onManageProfiles: openProfiles } : {})}
            {...(profileLaunchApi ? { launchApi: profileLaunchApi } : {})}
            {...(shellSnapshot.workspaces && !shellSnapshot.workspaces.chat_only
              ? { workspaceId: shellSnapshot.workspaces.active_id }
              : {})}
            nativeVisit={skillsVisit}
            native={
              <>
                {nativeAssetsApi && profileApi ? (
                  <NativeAssets
                    key={skillsVisit}
                    initialKind={skillsVisit > 0 ? "skill" : "agent"}
                    api={nativeAssetsApi}
                    profileApi={profileApi}
                    onCreated={(id) => {
                      setProfileFocus(id);
                      openProfiles();
                    }}
                  />
                ) : (
                  <p>Native subagent discovery is unavailable in this environment.</p>
                )}
                <section aria-label="Running work">
                  <h2>Running work</h2>
                  {renderRunningWork()}
                </section>
              </>
            }
            {...(nativeAssetsApi && profileApi
              ? {
                  onManageNative: openSkills,
                }
              : {})}
            onPrepared={(sessionId, preparedIdentity) => {
              // Keep identity ahead of selection; an admitted handoff opens no turn.
              handleProfileSession(sessionId, preparedIdentity);
              if (navigate({ view: "chat" })) openSession(sessionId);
            }}
          />
        </>
      );
    if (view === "context" && contextApi)
      return (
        <ContextView
          api={contextApi}
          chat={selectedId ? { sessionId: selectedId, title: headerTitle } : null}
          busy={runningComposer}
          refreshKey={contextKey}
          onBack={goBack}
          renderBack={renderBack}
          {...(decisionApi
            ? {
                decision: (
                  <DecisionTab
                    api={decisionApi}
                    openChatId={selectedId}
                    enabled={effectiveSettings?.["decision.enabled"] === true}
                    {...(decisionRequestedCall ? { requestedCall: decisionRequestedCall } : {})}
                  />
                ),
                decisionRequest,
              }
            : {})}
        />
      );
    if (view === "agents" && profileApi)
      return (
        <AgentsView
          renderBack={renderBack}
          section={agentsSection}
          onSection={(next) => {
            if (next !== agentsSection && !confirmLeaveUnsaved()) return;
            setAgentsSection(next);
          }}
          onBack={goBack}
          {...(botsAvailable ? { onBots: () => navigate({ view: "bots" }) } : {})}
          profiles={renderBotProfiles()}
          running={renderRunningWork()}
          {...(nativeAssetsApi
            ? {
                onSkills: openSkills,
                native: (
                  <NativeAssets
                    key={skillsVisit}
                    initialKind={skillsVisit > 0 ? "skill" : "all"}
                    api={nativeAssetsApi}
                    profileApi={profileApi}
                    onCreated={() => setAgentsSection("profiles")}
                  />
                ),
              }
            : {})}
          {...(inventoryApi
            ? { tools: <ToolsInventory api={inventoryApi} chats={runningRows} /> }
            : {})}
        />
      );
    if (view === "routines" && routinesApi && routinesStore)
      return (
        <RoutinesView
          renderBack={renderBack}
          api={routinesApi}
          snapshot={routinesSnapshot}
          sessions={appSnapshot.sessions}
          nowMs={now}
          selectedId={selectedRoutine}
          onSelect={selectRoutineAfterLeave}
          onRefresh={routinesStore.refresh}
          onBack={goBackAfterRoutineLeave}
          profiles={profileApi}
          workspaces={shellSnapshot.workspaces?.workspaces ?? []}
          activeWorkspaceId={activeWorkspaceId ?? ""}
          controls={routineControls}
          onSettings={async (changes) => {
            await shellStore.patch(changes);
          }}
          onRequestPermission={async () => {
            await notifier.request();
          }}
          onOpenSession={(sessionId) => {
            if (navigate({ view: "chat" })) openSession(sessionId);
          }}
          onStop={(sessionId) => viki.actions.interrupt(sessionId)}
        />
      );
    if (view === "code" && codeApi)
      return (
        <Workbench
          renderBack={renderBack}
          api={codeApi}
          folders={(shellSnapshot.workspaces?.workspaces ?? []).filter(
            (item) => item.kind === "code",
          )}
          activeId={codeActive ? activeWorkspaceId : null}
          onQuote={quoteCode}
          onOpenInEditor={viki.actions.openCode}
          onOpenSettings={() => navigate({ view: "settings", page: "workspaces" })}
          onBack={goBack}
          fontSize={
            typeof codeFontSize === "number" &&
            Number.isInteger(codeFontSize) &&
            codeFontSize >= 11 &&
            codeFontSize <= 20
              ? codeFontSize
              : 13
          }
          lineWrap={effectiveSettings?.["workbench.line_wrap"] === true}
          {...(codeRecovery ? { recovery: codeRecovery } : {})}
          {...(workbenchRoots ? { roots: workbenchRoots } : {})}
          {...(workbenchReview ? { review: workbenchReview } : {})}
          {...(workbenchChecks ? { checks: workbenchChecks } : {})}
          {...(workbenchTerminal ? { terminal: workbenchTerminal } : {})}
          {...(workbenchPreviews ? { previews: workbenchPreviews } : {})}
        />
      );
    if (view === "vault" && vaultApi) {
      const active = shellSnapshot.workspaces;
      return active && !active.chat_only && !codeActive ? (
        <VaultBrowser
          renderBack={renderBack}
          key={`${active.active_id}:${noteRequest.mount}`}
          workspaceId={active.active_id}
          api={vaultBrowserApi ?? vaultApi}
          onOpenUrl={viki.actions.openUrl}
          onBack={goBack}
          initialFolder={vaultFolder}
          onNavigation={recordVaultNavigation}
          onNavigationReady={registerVaultNavigation}
          {...(vaultRestore?.workspaceId === active.active_id
            ? { initialNavigation: vaultRestore }
            : {})}
          {...(noteRequest.ref ? { initialRef: noteRequest.ref } : {})}
          {...(noteRequest.draft && writeScope ? { initialDraft: noteRequest.draft } : {})}
          {...(excerpts
            ? {
                onAttachExcerpt: (part) =>
                  setEditorExcerpts(editorKey, (parts) => withExcerpt(parts, part)),
              }
            : {})}
          // A proposal takes no mutation lease and writes no vault, so a protected vault offers it
          // on every platform, unlike Save. `write` wins when a vault admits both.
          {...(decisions && activeVault?.policy === "protected" ? { proposalApi: decisions } : {})}
          {...(writeScope
            ? {
                write: {
                  api: vaultApi,
                  policyRevision: writeScope.policyRevision,
                  rootPhysicalId: writeScope.rootPhysicalId,
                  ...(vaultOpsApi ? { ops: vaultOpsApi } : {}),
                },
              }
            : strandedScope && vaultOpsApi
              ? { recoveryOnly: { api: vaultApi, ops: vaultOpsApi } }
              : {})}
          {...(vaultKnowledgeApi && memoryScope
            ? {
                memory: {
                  api: vaultKnowledgeApi,
                  policyRevision: memoryScope.policyRevision,
                  rootPhysicalId: memoryScope.rootPhysicalId,
                  protected: writeScope === null,
                },
              }
            : {})}
          {...(capturesApi ? { captures: capturesApi, onTriage: openTriage } : {})}
        />
      ) : (
        <section>
          {renderBack(goBack)}
          <h1 ref={focusOnMount} tabIndex={-1}>
            Vault
          </h1>
          <p>
            {codeActive
              ? "The active workspace is a code folder; it shows no notes."
              : "Open a vault in Settings to browse its notes."}
          </p>
        </section>
      );
    }
    if (view === "recovery")
      return (
        <>
          {renderBack(goBack)}
          <RecoveryView store={shellStore}>
            <ProvidersPage
              store={shellStore}
              snapshot={shellSnapshot}
              onOpenUrl={viki.actions.openUrl}
            />
            <DiagnosticsPage api={shellApi} snapshot={shellSnapshot} onNavigate={navigate} />
          </RecoveryView>
        </>
      );
    if (view === "onboarding") {
      // Unreachable without a shell client: `workspace.create`/`open` need a known-ready store,
      // which only a capabilities answer can report.
      if (shell === undefined) return null;
      return [
        nav.onboardingMode === "choose" ? renderBack(goBack) : null,
        <OnboardingView
          key="onboarding"
          store={shellStore}
          api={shell}
          mode={nav.onboardingMode}
          onMode={(mode) => applyNavigation({ ...currentNavigation.current, onboardingMode: mode })}
          onDone={() => navigate({ view: "chat" })}
          // Starter vault: fill its team's Bot seats once, only when both clients are live.
          {...(botsApi && profileApi
            ? {
                onVaultCreated: (created: CreateVaultResult) =>
                  void fillStarterTeam(botsApi, profileApi, created),
              }
            : {})}
          onNavigate={navigate}
          onOpenUrl={viki.actions.openUrl}
          {...(signIn ? { signIn } : {})}
          renderInspection={(inspection, workspaceId) => (
            <PolicyPanel
              workspaceId={workspaceId}
              api={shellApi}
              inspection={inspection}
              {...(shellSnapshot.workspaces
                ? { workspaces: shellSnapshot.workspaces.workspaces }
                : {})}
              onChanged={() => void shellStore.afterMutation(["capabilities", "workspaces"])}
            />
          )}
        />,
      ];
    }
    if (view === "settings")
      return (
        <SettingsView
          renderBack={renderBack}
          store={shellStore}
          page={nav.page}
          onPage={(page) => navigate({ page })}
          onNavigate={goBack}
          // Stale once `nav` has moved past the navigation that set it — see limitFocusNav.
          focusKey={nav === limitFocusNav.current ? limitFocusKey : null}
          onFocusKeyUsed={() => setLimitFocusKey(null)}
        >
          {settingsPage()}
        </SettingsView>
      );
    return (
      <DataView
        renderBack={renderBack}
        store={shellStore}
        api={shellApi}
        panel={nav.panel}
        onNavigate={goBack}
        onReset={(result) => {
          const commands = result.results.commands;
          if (
            commands &&
            commands.count > 0 &&
            (commands.status === "done" || commands.status === "partial")
          )
            botThreads.invalidate(result.scope_of);
        }}
        knowledgeApi={vaultKnowledgeApi}
        renderPathField={({ id, label, value, onChange, error, onSubmit }) => (
          <PathForm
            id={id}
            label={label}
            value={value}
            onChange={onChange}
            onEnter={onSubmit}
            browse
            {...(error === null ? {} : { error })}
          />
        )}
        renderConflict={(error) =>
          // The import panel holds the refused attempt; this panel is App's, so App is also what
          // closes it — by remembering the one error the user has already answered.
          error === dismissedConflict ? null : (
            <ReviewPanel
              error={error}
              changes={{}}
              onReload={() => {
                if (error.current === null) void shellStore.refreshSettings();
                else shellStore.adoptSettings(error.current);
                setDismissedConflict(error);
              }}
              onClose={() => setDismissedConflict(error)}
            />
          )
        }
      />
    );
  }

  const decisionItems = collectDecisions(appSnapshot.sessions, activeWorkspaceId);
  const needs = needsYou({
    sessions: appSnapshot.sessions,
    workspaceId: activeWorkspaceId,
    // Until the hook's effect drops them, a changed workspace still holds the old scope's items.
    proposals: attention.workspaceId === activeWorkspaceId ? attention.items : null,
    // Lets a card be told apart from a same-named sibling run by id, same as AgentRuns' own run
    // groups.
    requests,
  });

  /** Opening the inbox re-reads the section; closing it drops whatever dialog or line belonged to
   * the last visit, so a reopen starts from the server's answer, and re-reads the proposals the
   * rail shows. A late failure belongs to no visit: opening the inbox moves it onto the panel's
   * own line rather than dropping it. */
  function showDecisions(open: boolean): void {
    setDecisionsOpen(open);
    setInspecting(null);
    setInboxNote(open && lateFailure ? { text: lateFailure, kind: "alert" } : null);
    if (open) {
      setDrawer(null);
      setLateFailure(null);
      setDecisionsKey((key) => key + 1);
    } else attention.refresh();
  }

  /** A hand-off's draft, after the server has recorded the hand-off. The text goes into a NEW
   * editor and nothing is sent; false means this window had no editor left, so no draft exists
   * and the caller says so instead of announcing one. */
  function openHandoffDraft(text: string): boolean {
    if (!restoreText(text)) return false;
    setInboxNote({ text: t("decisions.handoff.drafted"), kind: "status" });
    return true;
  }

  /** Either inspector's hand-off, which the server has already recorded. Either way the inspector
   * closes, so the line below it — the announcement, or the cap refusal — is the one on screen. */
  function inspectorHandoff(text: string): void {
    const drafted = openHandoffDraft(text);
    setInspecting(null);
    if (drafted) {
      setDecisionsKey((key) => key + 1);
    } else setInboxNote({ text: t("decisions.editor_full"), kind: "alert" });
  }

  /** A native row's a or r, once its undo window ends or Approvals closes: the card as the store
   * holds it then, answered through the same durable route as the card's own Allow once and Deny.
   * Nothing is sent, and nothing said, when that card has gone. Nothing is sent either while the
   * connection is not open or the card's view is not synced, and the error's `ownerText` says
   * which; the connection is checked first, because a dropped one unsyncs every view. An answer
   * that is refused or never heard back about fails too: `decide` reports it as its outcome, never
   * by rejecting, and the card's own words (Cards.tsx) say why. */
  async function decideNative(item: NativeDecision, allow: boolean): Promise<void> {
    const snapshot = viki.store.getSnapshot();
    const sid = item.origin.session_id;
    const view = snapshot.sessions[sid];
    const card = view?.cards.find((open) => open.nativeRequest?.request_id === item.revision);
    if (!view || !card) return;
    if (snapshot.status !== "open")
      throw Object.assign(new Error("not_open"), {
        ownerText: "The app is not connected, so nothing was sent. Try again once it reconnects.",
      });
    if (!view.synced)
      throw Object.assign(new Error("not_synced"), {
        ownerText:
          "This conversation is not up to date yet, so nothing was sent. Try again in a moment.",
      });
    const outcome = await approvals.decide(card, sid, view.generation ?? "", {
      kind: "permission",
      allow,
    });
    if (outcome === "applied") return;
    // The store keeps the refusal under the request the card is bound to. One with no reason
    // recorded nothing, which the queue's own line ("Nothing was recorded. Try again.") says.
    const refusal = approvals.getSnapshot().approvals[item.revision]?.refusal;
    // The card's own words (Cards.tsx) for each way an answer can fail to land, so the two never
    // drift apart (merge note).
    const refused = { over: REQUEST_OVER, save: SAVE_FAILED, capacity: CAPACITY_FAILED };
    const ownerText = outcome === "unknown" ? UNCERTAIN_RESULT : refusal && refused[refusal];
    throw Object.assign(new Error(outcome), ownerText ? { ownerText } : {});
  }

  // A recovery window has exactly one view and offers no navigation drawer or inbox.
  const topBar = (
    <TopBar
      navigationInSidebar
      onNewSession={handleNewSession}
      view={view}
      {...(botsAvailable ? { onBots: () => onCommand("bots.open") } : {})}
      appMenu={
        <>
          <HelpMenu
            {...(shell !== undefined && !recovery
              ? {
                  onSetupGuide: () => {
                    if (navigate({ view: "onboarding", onboardingMode: "choose" }))
                      void shellStore.refreshCapabilities();
                  },
                }
              : {})}
          />
          <AppMenu snapshot={shellSnapshot} view={view} onCommand={onCommand} />
        </>
      }
      rails={
        !recovery
          ? {
              ...(layout.leftDocked ? {} : { left: railToggle("left") }),
              ...((view !== "chat" && view !== "code") || layout.rightDocked
                ? {}
                : { right: { ...railToggle("right"), attention: needs.count } }),
            }
          : {}
      }
    />
  );

  const appReadiness =
    shell !== undefined &&
    (shellSnapshot.capabilities === null || shellSnapshot.capabilitiesStatus !== "ready") ? (
      <section
        className="readiness-banner"
        aria-label="App readiness"
        role={shellSnapshot.capabilitiesStatus === "error" ? "alert" : "status"}
      >
        <span>
          {shellSnapshot.capabilitiesStatus === "error"
            ? "App readiness could not be checked."
            : "Checking app readiness…"}
        </span>
        <button
          type="button"
          className="btn alt sm"
          disabled={shellSnapshot.capabilitiesStatus !== "error"}
          onClick={() => void shellStore.refreshCapabilities()}
        >
          Retry app readiness
        </button>
      </section>
    ) : null;

  // The right panel (docked, or open as the drawer) already shows the counts and the context
  // meter in its tiles and footer; the status bar carries them only while that panel is off
  // screen, so neither place duplicates the other.
  const rightPanelShown =
    (view === "chat" || view === "code") && (layout.rightDocked || drawer === "right");

  const statusBar = (
    <StatusBar
      // No git chip in a recovery window, for the same reason as the notice above: `railsStatus`
      // is stuck at "error" from a read that could only 404, and observeGit reports that as
      // "unknown", so the chip would say "Git · status unknown" about a window with no vault
      // that never asked.
      git={
        <GitChip
          key={`${selectedId ?? ""}|${chatCodeFolder ? codeFolderKey(chatCodeFolder) : ""}`}
          {...(git && !recovery && vaultSurfaces(shellSnapshot) ? { vault: git } : {})}
          {...(lastCommit ? { lastCommit } : {})}
          {...(workspaceName ? { vaultName: workspaceName } : {})}
          {...(codeApi && chatCodeFolder
            ? {
                code: {
                  api: codeApi,
                  workspaceId: chatCodeFolder.workspace_id,
                  name: chatCodeFolder.name,
                  onOpen: (path: string) => openChatCode(path, true),
                },
              }
            : {})}
          refreshKey={lastFinishedTurnId}
        />
      }
      {...(rails && vaultSurfaces(shellSnapshot) && !rightPanelShown
        ? { vaultCounts: rails.counts }
        : {})}
      {...(!rightPanelShown && contextApi && selectedId && contextPercent !== undefined
        ? {
            contextMeter: (
              <ContextMeter
                key={selectedId}
                percent={contextPercent}
                sessionId={selectedId}
                api={contextApi}
                onOpenPage={() => navigate({ view: "context" })}
              />
            ),
          }
        : !rightPanelShown && contextPercent !== undefined
          ? { contextPercent }
          : {})}
      {...(notice !== undefined ? { notice } : {})}
      planLimit={
        limitsApi && normalMode ? (
          <PlanLimitMeter api={limitsApi} refreshKey={contextKey} nowMs={now} />
        ) : undefined
      }
    />
  );

  // Ctrl+K opens the palette from every view, so it is built once and rendered beside whichever
  // shell is on screen — it is `position: fixed`, outside `.shell`, and belongs to neither.
  const palette = paletteOpen ? (
    <SearchPalette
      open
      search={(q, id) => rest.search(q, id)}
      onOpen={(sessionId) => {
        if (navigate({ view: "chat" })) openSession(sessionId);
      }}
      onClose={() => setPaletteOpen(false)}
      nowMs={now}
      archived={(sessionId) => archivedIds.has(sessionId)}
    />
  ) : null;

  // Beside the palette for the same reason. Only a tool permission is answered in the panel,
  // through the card column's own renderer; plans and questions lead back to their conversation.
  const inbox =
    decisionsOpen && !recovery ? (
      <DecisionInbox
        items={decisionItems}
        sessionLabel={(id) => sessionLabel(id, restSnapshot.sessions, metadata.sessions)}
        workspaceName={workspaceName}
        renderAnswer={(item) => {
          // Only a native card has an answer to render; a retro row or a vault proposal is
          // answered in its own inspector, below.
          if (item.kind !== "permission" && item.kind !== "question") return null;
          const origin = appSnapshot.sessions[item.origin.session_id];
          return item.inline && origin
            ? renderCard(item.card, item.origin.session_id, origin)
            : null;
        }}
        onOpenConversation={(item, origin) => {
          const now = shellStore.getSnapshot();
          const workspaceId = now.capabilities?.workspace.active?.id ?? null;
          const current = collectDecisions(viki.store.getSnapshot().sessions, workspaceId).find(
            (row) => row.id === item.id && row.revision === item.revision && row.card === item.card,
          );
          const leaf = vaultSnapshot();
          if (
            !decisionsOpen ||
            now.recovery ||
            !origin.isConnected ||
            !origin.matches('.decision-inbox[role="dialog"][aria-modal="true"]') ||
            currentVaultReader.current !== (vaultBrowserApi ?? vaultApi) ||
            !current ||
            current.origin.session_id !== item.origin.session_id ||
            current.origin.workspace_id !== item.origin.workspace_id ||
            (vaultNavigation.current &&
              (!leaf ||
                !now.workspaces ||
                now.workspaces.chat_only ||
                now.workspaces.active_id !== workspaceId ||
                leaf.workspaceId !== workspaceId ||
                !now.workspaces.workspaces.some((row) => row.workspace_id === workspaceId)))
          )
            return;
          if (!editors.capacityAvailable(current.origin.session_id)) {
            setInboxNote({ text: t("decisions.editor_full"), kind: "alert" });
            return;
          }
          if (navigate({ view: "chat" }, selectedRoutineRef.current, origin)) {
            openSession(item.origin.session_id);
            showDecisions(false);
          }
        }}
        onClose={() => showDecisions(false)}
        onDecide={decideNative}
        line={approvalLine}
        // The queue's own failure line, for a decision that failed once this panel had closed.
        onLateFailure={(label, text) => {
          const line = `Could not finish “${label}”. ${text}`;
          setLateFailure((current) => (current ? `${current} ${line}` : line));
        }}
      >
        {inboxNote ? (
          <p className="decisions-notice jp" role={inboxNote.kind}>
            {inboxNote.text}
          </p>
        ) : null}
        {decisions ? (
          <DecisionSection
            api={decisions}
            refreshKey={decisionsKey}
            onInspect={setInspecting}
            activeWorkspaceId={activeWorkspaceId}
            // Runs once the row's undo window ends, or Approvals closes, against the workspace
            // active then: a proposal of a vault this window has since left is refused. Once it
            // has run, the proposals behind the rail are read again too: one flushed at close can
            // land after the read the close itself started (`refresh` outlives the panel).
            onAct={(row, action, note) =>
              createApprovalExecutor({
                api: decisions,
                ...(skillProposals ? { skills: skillProposals } : {}),
                ...(decisionApi ? { questions: decisionApi } : {}),
                activeWorkspaceId:
                  shellStore.getSnapshot().capabilities?.workspace.active?.id ?? null,
                openDraft: openHandoffDraft,
                changed: () => {
                  setDecisionsKey((key) => key + 1);
                  attention.refresh();
                },
              })(row, action, note)
            }
          />
        ) : null}
        {rails && vaultSurfaces(shellSnapshot) && rails.counts.inbox > 0 ? (
          <CaptureInboxRow count={rails.counts.inbox} onOpen={openTriage} />
        ) : null}
        {/* The inspectors sit INSIDE this panel deliberately. useModalFocus inerts a dialog's
            SIBLINGS at every ancestor and re-inerts whatever React mounts later, so a dialog
            mounted beside this panel would be inerted by this panel's own observer. Inside it,
            each one inerts the panel's content and nothing inerts it. Each backdrop is
            position: fixed, so none of them is drawn within the panel's box. */}
        {decisions && inspecting?.kind === "vault_proposal" ? (
          <ProposalInspector
            api={decisions}
            item={inspecting}
            workspaceName={workspaceName}
            activeWorkspaceId={activeWorkspaceId}
            onClose={() => setInspecting(null)}
            onChanged={() => setDecisionsKey((key) => key + 1)}
            onHandoff={inspectorHandoff}
            onOpenEditor={(detail, origin) => {
              const now = shellStore.getSnapshot();
              const workspace = now.workspaces;
              const leaf = vaultSnapshot();
              if (
                now.recovery ||
                !decisionsOpen ||
                !origin.isConnected ||
                inspecting?.kind !== "vault_proposal" ||
                inspecting.id !== detail.id ||
                inspecting.ref !== detail.ref ||
                !workspace ||
                workspace.chat_only ||
                workspace.active_id !== detail.origin.workspace_id ||
                now.capabilities?.workspace.active?.id !== detail.origin.workspace_id ||
                !workspace.workspaces.some(
                  (row) => row.workspace_id === detail.origin.workspace_id,
                ) ||
                (leaf && leaf.workspaceId !== detail.origin.workspace_id)
              )
                return;
              if (openVaultNote(detail.ref, origin)) showDecisions(false);
            }}
          />
        ) : null}
        {decisions && skillProposals && inspecting?.kind === "skill_proposal" ? (
          <SkillProposalInspector
            api={skillProposals}
            item={inspecting}
            activeWorkspaceId={activeWorkspaceId}
            sessionLabel={(id) => sessionLabel(id, restSnapshot.sessions, metadata.sessions)}
            onClose={() => setInspecting(null)}
            onChanged={() => setDecisionsKey((key) => key + 1)}
            onHandoff={inspectorHandoff}
            onOpenChat={(sessionId, origin) => {
              if (!editors.capacityAvailable(sessionId)) {
                setInboxNote({ text: t("decisions.editor_full"), kind: "alert" });
                return;
              }
              if (navigate({ view: "chat" }, selectedRoutineRef.current, origin)) {
                openSession(sessionId);
                showDecisions(false);
              }
            }}
          />
        ) : null}
        {decisions && decisionApi && inspecting?.kind === "question_proposal" ? (
          <QuestionProposalInspector
            api={decisionApi}
            item={inspecting}
            activeWorkspaceId={activeWorkspaceId}
            onClose={() => setInspecting(null)}
            onChanged={() => setDecisionsKey((key) => key + 1)}
          />
        ) : null}
      </DecisionInbox>
    ) : null;

  // A queued decision that failed after Approvals closed (lateFailure), on the window's notice
  // strip in every view, since the panel that would have shown it is gone.
  const lateFailureNotice = lateFailure ? (
    <div className="readiness-banner">
      <span role="alert">{lateFailure}</span>
      <button type="button" className="btn alt sm" onClick={() => setLateFailure(null)}>
        Dismiss
      </button>
    </div>
  ) : null;
  // Beside the inbox: the user's filing pass over 00-inbox/, opened by the tile instead of the
  // Vault browser whenever VIKI can triage captures. Vault-only, like the tile itself.
  const captureTriage =
    triageOpen && capturesApi && activeWorkspaceId && !recovery && vaultSurfaces(shellSnapshot) ? (
      <CaptureTriage
        api={capturesApi}
        workspaceId={activeWorkspaceId}
        choices={triageChoices}
        onChoices={setTriageChoices}
        onFile={(text) => navigate({ view: "chat" }) && restoreText(text)}
        onOpenChat={(sessionId) => {
          if (navigate({ view: "chat" })) {
            openSession(sessionId);
            setTriageOpen(false);
          }
        }}
        onOpenNote={(ref, origin) => {
          if (openVaultNote(ref, origin)) setTriageOpen(false);
        }}
        {...(view !== "vault"
          ? {
              onBrowse: () => {
                setTriageOpen(false);
                openVault("00-inbox");
              },
            }
          : {})}
        onClose={() => setTriageOpen(false)}
      />
    ) : null;

  // Beside the palette and the inbox: Conversation (chat-only) is the sole opener, but the
  // review itself is rendered here, outside `.shell` — like the palette, so it stacks above the
  // shell whatever the layout. Both the chat and non-chat returns below place it in the same
  // fragment slot, so switching between them keeps it mounted rather than losing it.
  const retryReview = retryTurn ? (
    <RetryReview
      turn={retryTurn.turn}
      editorEmpty={editorEmpty}
      incomplete={retryTurn.incomplete}
      onFill={(text, attachments) => {
        editors.edit(editorKey, withoutVoiceTag(text));
        if (attachments.length) editors.attach(editorKey, attachments);
        setRetryTurn(null);
      }}
      onClose={() => setRetryTurn(null)}
    />
  ) : null;
  const renameDialog = renameSession ? (
    <SessionRenameDialog
      key={renameSession.session_id}
      session={renameSession}
      pending={metadata.pending.has(renameSession.session_id)}
      errorCode={metadata.error}
      onSave={(title) => metadata.update(renameSession.session_id, { title })}
      onReload={() => void metadata.reload()}
      onClose={() => setRenameSessionId(null)}
      restoreFocus={() => {
        const row = [...document.querySelectorAll<HTMLElement>(".sess")].find(
          (element) => element.dataset.sessionId === renameSession.session_id,
        );
        return (
          row?.querySelector<HTMLElement>(".sess-menu") ??
          document.querySelector<HTMLElement>('[aria-label="Sessions and agents"]') ??
          focusedMessageBox()
        );
      }}
    />
  ) : null;

  // Mounted after `{retryReview}` in both returns below, the same shared-node pattern as
  // `retryReview` above. `onAsk` opens the chat and drops the excerpt in as an
  // unsent draft; a recovery window has no composer for it to land in, so it is omitted
  // there rather than offering "Ask VIKI" with nowhere to put the answer.
  const helpCenter = (
    <HelpCenter
      onOpenUrl={viki.actions.openUrl}
      // Calls `navigate` directly rather than `onCommand("diagnostics.open")`, which cannot
      // forward the Help dialog as `modalOrigin` — without it `canLeave` sees the dialog still
      // open and refuses to leave the Vault (same reasoning as `onAsk` below).
      onOpenDiagnostics={(origin?: HTMLElement) =>
        navigate(
          { view: recovery ? "recovery" : "settings", page: "diagnostics" },
          undefined,
          origin,
        )
      }
      {...(recovery
        ? {}
        : {
            onAsk: (text: string, origin?: HTMLElement) => {
              if (!restoreText(text)) return false;
              // The Help dialog is still open and `[aria-modal="true"]` here, so it must be
              // passed as `modalOrigin`: otherwise the Vault's own `canLeave` refuses this
              // navigation and the user is left stuck on the Vault view.
              navigate({ view: "chat" }, undefined, origin);
              return true;
            },
          })}
    />
  );

  // VIKI's launcher and its panel, mounted after `{helpCenter}` in both returns below, so moving
  // between views keeps them: only with a Decision client, in a normal-mode window (never
  // recovery) whose token still works.
  const vaultLeaf = vaultSnapshot();
  // The open chat to check for a "make this a skill" hint, for presence's own page facts: a
  // vault workspace's own chat view, the same gate SkillTurnActions' own mount uses, and not
  // once the user has dismissed this session's hint (Not now) — pageFacts' `skillFacts` also
  // reports none without a view, so dismissal is carried by nulling it here, not by `off` below.
  // The second-look trigger takes the same chat view without that dismissal (`lookPageView`).
  const lookPageView =
    view === "chat" &&
    selectedId !== null &&
    selectedView &&
    shellSnapshot.capabilities?.workspace.active?.kind === "vault"
      ? selectedView
      : null;
  const skillPageView = selectedId !== null && !skillHintsOff.has(selectedId) ? lookPageView : null;
  const decisionLauncher =
    launcherShown && decisionApi ? (
      <DecisionLauncher
        api={decisionApi}
        windowId={decisionWindowId}
        chat={view === "chat" ? selectedId : null}
        chatTitle={view === "chat" && selectedId ? presenceChatTitle : null}
        note={vaultLeaf?.ref ? { workspaceId: vaultLeaf.workspaceId, ref: vaultLeaf.ref } : null}
        page={pageFacts({
          sessions: appSnapshot.sessions,
          reviewed: reviewedRuns,
          git: gitObservation,
          railsStatus: restSnapshot.railsStatus,
          routines: routinesSnapshot,
          routinesEnabled: routinesStore !== null,
          skill: {
            view: skillPageView,
            rows: restSnapshot.sessions,
            off: effectiveSettings?.["decision.watch.improve"] === false,
          },
          look: { view: lookPageView, rows: restSnapshot.sessions },
        })}
        open={launcherOpen}
        onOpen={openLauncherPanel}
        onClose={closeLauncherPanel}
        onVoice={toggleLauncherVoice}
        onPanelEscape={endLauncherVoice}
        savedPosition={effectiveSettings?.["layout.launcher"]}
        savedPanelSize={effectiveSettings?.["layout.launcher_panel"]}
        writable={decisionWritable}
        patch={(changes) => shellStore.patch(changes)}
        onNavigate={openFromLauncher}
        onOpenDecision={openDecisionFromLauncher}
        onStarted={setVikiStarted}
        onNotice={setLauncherNotice}
        renderChat={renderLauncherChat}
      />
    ) : null;

  const leftRail = (
    <LeftRail
      {...(!recovery
        ? {
            currentView: view,
            destinations: [
              "chat",
              ...(decisionApi ? ["decision" as const] : []),
              ...(botsInRail ? ["bot_threads" as const, "kanban" as const, "bots" as const] : []),
              ...(capabilitiesAvailable ? ["capabilities" as const] : []),
              ...(messagingApi ? ["messaging" as const] : []),
              ...(vaultApi ? ["vault" as const] : []),
              ...(profileApi && !botsInRail ? ["agents" as const] : []),

              ...(routinesApi ? ["routines" as const] : []),
            ] satisfies RailDestination[],
            ...(botsInRail && !botsAvailable
              ? { waiting: ["bots" as const, "kanban" as const, "bot_threads" as const] }
              : {}),
            onNavigate: (destination: RailDestination, modalOrigin?: HTMLElement) => {
              if (destination === "vault") openVault("", modalOrigin);
              else if (
                destination === "bots" ||
                destination === "kanban" ||
                destination === "bot_threads"
              ) {
                if (commandFor("bots.open", shellSnapshot, view).enabled)
                  navigate({ view: destination }, undefined, modalOrigin);
              } else navigate({ view: destination }, undefined, modalOrigin);
            },
            onNewConversation: handleNewSession,
            newSession: {
              disabled: sessionsBlocked,
              ...(sessionsBlocked && view === "chat" ? { describedBy: "readiness-reason" } : {}),
            },
            routinesAttention: attentionCount(routinesSnapshot.items, appSnapshot.sessions),
            workspace: {
              label: shellSnapshot.capabilities?.workspace.active?.name ?? "Workspace",
              ...(shellSnapshot.capabilities?.workspace.active
                ? { detail: `${shellSnapshot.capabilities.workspace.active.policy} workspace` }
                : {}),
            },
            ...(shell
              ? {
                  onWorkspace: (modalOrigin?: HTMLElement) =>
                    navigate({ view: "settings", page: "workspaces" }, undefined, modalOrigin),
                }
              : {}),
          }
        : {})}
      onSearch={() => {
        setDrawer(null);
        setPaletteOpen(true);
      }}
    >
      {organization ? (
        <div className="session-organization">
          <ArchiveBar
            showArchived={showArchived}
            archivedCount={archivedCount}
            onToggle={() => setShowArchived((value) => !value)}
          />
          {metadata.error ? (
            <p role="status">
              Session settings could not be loaded or saved.{" "}
              <button type="button" onClick={() => void metadata.reload()}>
                Reload
              </button>
            </p>
          ) : null}
          {sessionMetadata ? (
            <details>
              <summary>Organize this session</summary>
              <SessionOrganizer
                key={sessionMetadata.session_id}
                session={sessionMetadata}
                onPatch={(patch) => updateSessionMetadata(sessionMetadata.session_id, patch)}
                onReload={() => void metadata.reload()}
                pending={metadataPending.has(sessionMetadata.session_id)}
                errorCode={metadata.error}
              />
            </details>
          ) : null}
          <MissingSessions
            records={missingListed}
            onUnpin={handleMissingUnpin}
            onRemove={(sessionId) => void handleMissingRemove(sessionId)}
            onOpen={openSidebarSession}
            pending={new Set([...metadata.pending, ...removingIds])}
            drafts={missingDrafts}
          />
        </div>
      ) : null}
      <SessionList
        groups={groups}
        other={other}
        listing={listingNote(restSnapshot)}
        selectedId={selectedId}
        liveIds={heldIds}
        nowMs={now}
        {...(folderOf ? { folderOf } : {})}
        startedByViki={(sessionId) => vikiStartedIds.has(sessionId)}
        onOpen={openSidebarSession}
        dragRows={Boolean(organization)}
        rowContext={{ app: appSnapshot, elsewhere: elsewhereMap(appSnapshot) }}
        menu={{
          onFork: (sessionId) => {
            if (view === "chat" || navigate({ view: "chat" }, undefined, sidebarModalOrigin()))
              armFork(sessionId);
          },
          onOpenTranscript: viki.actions.openTranscript,
          onCloseSession: requestClose,
        }}
        {...(organization
          ? {
              archive: {
                isArchived: (sessionId: string) => archivedIds.has(sessionId),
                onToggle: (sessionId: string) =>
                  void updateSessionMetadata(sessionId, { archived: !archivedIds.has(sessionId) }),
                pending: metadataPending,
              },
              organization: {
                isPinned: (sessionId: string) =>
                  metadata.sessions.some((row) => row.session_id === sessionId && row.pinned),
                unreadOf: (sessionId: string) =>
                  metadata.sessions.some((row) => row.session_id === sessionId && row.unread),
                colorOf: (sessionId: string) =>
                  metadata.sessions.find((row) => row.session_id === sessionId)?.color ?? null,
                pending: metadataPending,
                onUnread: (sessionId: string) => {
                  if (explicitVisit.current?.id === sessionId) explicitVisit.current = null;
                  void metadata.update(sessionId, {
                    unread: !metadata.sessions.some(
                      (row) => row.session_id === sessionId && row.unread,
                    ),
                  });
                },
                onColor: (sessionId: string, color: SessionColor | null) =>
                  void metadata.update(sessionId, { color }),
                ...(sessionWindowsApi && !recovery && !staleWindow
                  ? {
                      onNewWindow: (sessionId: string) => void openSessionWindow(sessionId),
                      newWindowPending: windowPending,
                      newWindowErrorOf: (sessionId: string) => windowErrors.get(sessionId) ?? null,
                    }
                  : {}),
                ...(archives && assets && !recovery ? { onExport: revealSessionExport } : {}),
                ...(terminalApi && rootsApi && !recovery && !staleWindow
                  ? {
                      onOpenTerminal: (sessionId: string) => void openSessionTerminal(sessionId),
                      canOpenTerminal: (sessionId: string) => terminalRoot(sessionId) !== null,
                    }
                  : {}),
                ...(codeApi && rootsApi && !recovery && !staleWindow
                  ? {
                      onNewChatInWorkspace: () => {
                        navigate({ view: "code" }, undefined, sidebarModalOrigin());
                      },
                    }
                  : {}),
                onRename: (sessionId: string) => {
                  setDrawer(null);
                  setRenameSessionId(sessionId);
                },
                onPin: (sessionId: string) =>
                  void metadata.update(sessionId, {
                    pinned: !metadata.sessions.some(
                      (row) => row.session_id === sessionId && row.pinned,
                    ),
                  }),
              },
            }
          : {})}
      />
      {sessionActionNote ? <p role="status">{sessionActionNote}</p> : null}
    </LeftRail>
  );

  const routineRailProps = {
    sessions: appSnapshot.sessions,
    nowMs: now,
    onOpen: openRoutine,
    ...(routinesApi
      ? {
          onRunNow: async (item: RoutineItem) => {
            await routinesApi.run(item.routine_id, crypto.randomUUID(), item.current_revision);
            routinesStore?.refresh();
          },
        }
      : {}),
  };
  const botRoutineItems = botHere
    ? routinesSnapshot.items.filter((item) => botHere.bot.routine_ids.includes(item.routine_id))
    : [];
  const botRoutineRefreshKey = JSON.stringify(
    botRoutineItems.map((item) => [
      item.routine_id,
      item.current_revision,
      item.state_revision,
      item.active_run,
      item.last_attempt,
    ]),
  );
  const visiblePaneChats = new Set(
    visibleMultiPanes && view === "chat"
      ? paneGroups(panes.layout).flatMap((group) =>
          group.active && !fileOf(panes.layout.files, group.active) ? [group.active] : [],
        )
      : [],
  );
  async function requestDesktopNotices(): Promise<void> {
    if (noticeInFlight.current) return;
    noticeInFlight.current = true;
    setNoticePending(true);
    setNoticeError(null);
    try {
      let permission: Awaited<ReturnType<typeof notifier.request>>;
      try {
        permission = await notifier.request();
      } catch {
        setNoticeError("shell.needs_you.notice_request_failed");
        return;
      }
      if (permission !== "granted") {
        setNoticeError(`routines.controls.permission_${permission}`);
        return;
      }
      try {
        await shellStore.patch({ "notifications.enabled": true });
      } catch {
        setNoticeError("routines.controls.save_failed");
      }
    } finally {
      noticeInFlight.current = false;
      setNoticePending(false);
    }
  }
  const pinnedNeeds = (
    <NeedsYou
      needs={needs}
      selectedId={selectedId}
      labelOf={(id) => sessionLabel(id, restSnapshot.sessions, metadata.sessions)}
      {...(recovery
        ? {}
        : {
            onOpenApprovals: () => {
              if (!shellStore.getSnapshot().recovery) showDecisions(true);
            },
          })}
      onOpenNative={(row) => {
        setCardTarget(null);
        if (view !== "chat" && !navigate({ view: "chat" })) return;
        const sessionId = row.item.origin.session_id;
        if (!jumpToRun(sessionId, null)) return;
        setCardTarget({
          sessionId,
          toolUseId: row.item.card.data.tool_use_id,
          request: ++cardTargetSerial.current,
        });
      }}
      onOpenProposal={(item) => {
        showDecisions(true);
        if (!isQuestionProposalItem(item) && !isChangeProposalItem(item)) setInspecting(item);
      }}
      actions={(row, now) => {
        const { card } = row.item;
        if (card.kind !== "permission" || card.data.kind !== "tool") return undefined;
        const sessionId = row.item.origin.session_id;
        if (sessionId === selectedId || visiblePaneChats.has(sessionId)) return undefined;
        const toolUseId = card.data.tool_use_id;
        // The row's own title is one clipped line cut to 200 code points, so RequestActions
        // shows the whole command beside Allow once, not its opening words.
        return (
          <RequestActions
            request={card.data}
            waitingSince={null}
            nowMs={now}
            onDecide={(allow) => decideRequest(sessionId, toolUseId, allow)}
          />
        );
      }}
      waiting={(row, now) => {
        const since = requests
          .get(row.item.origin.session_id)
          ?.get(row.item.card.data.tool_use_id)?.raisedAt;
        return since === undefined ? null : `waiting ${runElapsed(Math.max(0, now - since))}`;
      }}
      {...(runsApi ? { stops } : {})}
      nowMs={now}
      footer={
        needs.count > 0 && !routineControls.notificationsEnabled && !noticeDismissed ? (
          <>
            <p>{tf("shell.needs_you.notice_footer", {})}</p>
            <button
              type="button"
              disabled={noticePending}
              onClick={() => void requestDesktopNotices()}
            >
              {tf("shell.needs_you.notice_on", {})}
            </button>
            <button type="button" onClick={() => setNoticeDismissed(true)}>
              {tf("shell.needs_you.notice_later", {})}
            </button>
            {noticeError ? <p role="alert">{t(noticeError)}</p> : null}
          </>
        ) : null
      }
    />
  );
  const rightRail = (
    <RightRail
      online={online}
      pinned={pinnedNeeds}
      summaryHidden={codeRailVisible}
      footer={
        <ContextSummary
          {...(contextPercent !== undefined ? { percent: contextPercent } : {})}
          {...(contextApi ? { onOpen: () => navigate({ view: "context" }) } : {})}
        />
      }
      summary={
        <RailFooter
          {...(rails && vaultSurfaces(shellSnapshot)
            ? {
                tiles: (
                  <VaultTiles
                    counts={rails.counts}
                    {...(vaultApi && !recovery && shellSnapshot.workspaces?.chat_only === false
                      ? {
                          onOpen: (folder: string) =>
                            folder === "00-inbox" && capturesApi ? openTriage() : openVault(folder),
                        }
                      : {})}
                  />
                ),
              }
            : {})}
          {...(vaultSurfaces(shellSnapshot) && gitObservation.dirty !== null
            ? { uncommitted: gitObservation.dirty }
            : {})}
          skills={
            nativeAssetsApi ? (
              <>
                <SkillsSummary
                  compact
                  api={nativeAssetsApi}
                  contextKey={activeWorkspaceId ?? "none"}
                  {...(profileApi && !recovery ? { onOpen: openSkills } : {})}
                  activity={<SkillActivity view={nav.view === "chat" ? selectedView : undefined} />}
                />
                {!profileApi ? <p>Profile client is not connected.</p> : null}
              </>
            ) : (
              <RailSection title="Skills" className="skills-summary">
                <p>Skills catalog unavailable.</p>
                <SkillActivity view={nav.view === "chat" ? selectedView : undefined} />
              </RailSection>
            )
          }
        />
      }
    >
      <div className="right-rail-workspace">
        {codeApi ? (
          <div role="tablist" aria-label="Right sidebar" className="workbench-tabs">
            {(["overview", "code"] as const).map((panel) => (
              <button
                type="button"
                role="tab"
                key={panel}
                id={`${codeRailId}-tab-${panel}`}
                aria-controls={`${codeRailId}-panel-${panel}`}
                aria-selected={rightRailPanel === panel}
                tabIndex={rightRailPanel === panel ? 0 : -1}
                className="btn txt sm"
                onClick={() => selectRightRail(panel)}
                onKeyDown={(event) => {
                  const next =
                    event.key === "Home"
                      ? "overview"
                      : event.key === "End"
                        ? "code"
                        : event.key === "ArrowLeft" || event.key === "ArrowRight"
                          ? panel === "overview"
                            ? "code"
                            : "overview"
                          : null;
                  if (!next) return;
                  event.preventDefault();
                  selectRightRail(next);
                  document.getElementById(`${codeRailId}-tab-${next}`)?.focus();
                }}
              >
                {panel === "overview" ? "Overview" : "Code"}
              </button>
            ))}
          </div>
        ) : null}
        <section
          className="right-rail-panel"
          role={codeApi ? "tabpanel" : undefined}
          id={`${codeRailId}-panel-overview`}
          aria-labelledby={codeApi ? `${codeRailId}-tab-overview` : undefined}
          hidden={codeRailVisible}
        >
          {botsApi && botHere && selectedId ? (
            <BotChatActivity
              key={`${botHere.botId}:${selectedId}`}
              api={botsApi}
              botId={botHere.botId}
              sessionId={selectedId}
              main={botHere.main}
              {...(botActivityMemory ? { memory: botActivityMemory } : {})}
              onOpenSession={(id) => {
                if (navigate({ view: "chat" })) openSession(id, true);
              }}
              {...(routinesStore
                ? {
                    routines: (
                      <RoutinesRail
                        title={t("bots.activity.routines")}
                        snapshot={{ ...routinesSnapshot, items: botRoutineItems }}
                        {...routineRailProps}
                        onShowAll={() => showBot(botHere.botId)}
                      />
                    ),
                    refreshKey: botRoutineRefreshKey,
                    onRefresh: routinesStore.refresh,
                  }
                : {})}
            />
          ) : null}
          {botsApi && botsAvailable ? (
            <TeamStrip
              api={botsApi}
              activeWorkspaceId={teamVaultId}
              readiness={teamReadiness}
              live={teamLive}
              selectedId={selectedId}
              currentBotId={botHere?.botId ?? null}
              onOpenSession={(id) => {
                if (navigate({ view: "chat" })) openSession(id, true);
              }}
              onOpenPrepared={(preview, bot) => {
                if (
                  preview.workspace_id !== shellStore.getSnapshot().workspaces?.active_id ||
                  preview.session_id !== bot.chat_session_id ||
                  preview.profile_id !== bot.profile_id
                )
                  return;
                handleProfileSession(preview.session_id, preview.identity ?? null);
                markBotSession(preview.session_id, bot.profile_id);
                if (navigate({ view: "chat" })) openSession(preview.session_id, true);
              }}
              onShowBot={(id) => {
                showBot(id);
              }}
              onShowSeat={(seat) => {
                if (showBot(null)) setSeatRequest(seat.seat);
              }}
              onShowAll={() => showBot(null)}
            />
          ) : null}
          <section
            className="rail-agents"
            ref={agentsList}
            tabIndex={-1}
            aria-label={`${identity.assistantName} and subagents`}
          >
            <div className="rail-agents-head">
              <span className="lbl">{`${identity.assistantName} and subagents`}</span>
              {agentsCount ? <span className="rail-agents-count">{agentsCount}</span> : null}
            </div>
            <AgentRuns
              sessions={appSnapshot.sessions}
              selectedId={selectedId}
              labelOf={(id) => sessionLabel(id, restSnapshot.sessions, metadata.sessions)}
              nowMs={now}
              reviewed={reviewedRuns}
              onReviewed={markReviewed}
              onJump={jumpToRun}
              api={subagentsApi}
              hideNeeds
              runs={runsApi}
              stops={stops}
              requests={requests}
              onDecide={decideRequest}
            />
            <details className="agents-library">
              <summary className="lbl">
                {`Library · ${agents.length} subagent${agents.length === 1 ? "" : "s"}`}
              </summary>
              <AgentsSection agents={agents} running={running} vikiOnline={online} nowMs={now} />
            </details>
          </section>
          {betweenChatsApi && !recovery ? (
            <BetweenChats
              api={betweenChatsApi}
              labelOf={(id) => sessionLabel(id, restSnapshot.sessions, metadata.sessions)}
              onOpen={(id) => {
                if (navigate({ view: "chat" })) openSession(id, true);
              }}
              nowMs={now}
            />
          ) : null}
          <UpNextSection
            routines={
              routinesStore ? (
                <RoutinesRail
                  snapshot={{
                    ...routinesSnapshot,
                    items: botHere
                      ? routinesSnapshot.items.filter(
                          (item) => !botHere.bot.routine_ids.includes(item.routine_id),
                        )
                      : routinesSnapshot.items,
                  }}
                  {...routineRailProps}
                  onShowAll={() => navigate({ view: "routines" })}
                />
              ) : null
            }
          />
          {selectedView && (contextPercent !== undefined || hasChatFiles) ? (
            <ChatSources
              view={selectedView}
              {...(chatCodeFolder ? { onOpenCode: openChatCode } : {})}
            />
          ) : null}
        </section>
        {codeApi && codeRailVisited ? (
          <div
            className="right-rail-panel"
            role="tabpanel"
            id={`${codeRailId}-panel-code`}
            aria-labelledby={`${codeRailId}-tab-code`}
            hidden={!codeRailVisible}
          >
            <CodeSidebar
              api={codeApi}
              folders={(shellSnapshot.workspaces?.workspaces ?? []).filter(
                (folder) => folder.kind === "code",
              )}
              activeId={codeActive ? activeWorkspaceId : null}
              selected={codeRequest}
              onOpen={openCodeFile}
              onOpenWorkbench={() => navigate({ view: "code" })}
            />
          </div>
        ) : null}
      </div>
    </RightRail>
  );

  // The settings-backed Bot look: confirmed hues/icons plus the shared serialized writer.
  const botHueControls = useMemo<BotHueControls>(() => {
    const hues = parseBotHues(effectiveSettings?.[BOT_HUES_KEY]);
    const icons = parseBotIcons(effectiveSettings?.[BOT_ICONS_KEY]);
    if (!shellApi) return { hues, icons };
    return {
      hues,
      icons,
      set: botLookQueue.set,
    };
  }, [effectiveSettings, shellApi, botLookQueue]);
  // ADR-050's OS folder picker: every PathForm that opts into Browse… shares one status read.
  // Both of App's returns go through this, so their roots stay the same element type and moving
  // between the chat and another view keeps the shell mounted. Also where every reader of a
  // Bot's hue (BotAvatar/BotIcon/BotHueField) gets BotHuesContext.
  const withFolderPicker = (node: ReactElement): ReactElement => {
    const withHues = (
      <BotHuesContext.Provider value={botHueControls}>{node}</BotHuesContext.Provider>
    );
    return shellApi ? (
      <FolderPickerProvider api={shellApi}>{withHues}</FolderPickerProvider>
    ) : (
      withHues
    );
  };

  const arrivalStatus = (
    <div className="sr-only" role="status" aria-live="polite">
      {cardArrival.text ? <span key={cardArrival.serial}>{cardArrival.text}</span> : null}
    </div>
  );

  if (view !== "chat")
    return withFolderPicker(
      <IdentityContext.Provider value={identity}>
        <Shell
          layout={layout}
          onPaneModeChange={setPaneModeVisible}
          topBar={topBar}
          statusBar={statusBar}
          railWidths={railWidths}
          onRailWidthCommit={commitRailWidth}
          {...(!recovery
            ? {
                viewSidebar: leftRail,
                rails: { drawer, onClose: () => setDrawer(null) },
              }
            : {})}
          view={
            <>
              {appReadiness}
              {railWidthReview}
              {lateFailureNotice}
              {renderView()}
            </>
          }
          // The chat slots exist for the chat; this branch renders none of them, and passing
          // null rather than loosening their types keeps the chat's own contract required.
          leftRail={null}
          header={null}
          messages={null}
          composer={null}
          rightRail={view === "code" ? rightRail : null}
        />
        {palette}
        {inbox}
        {arrivalStatus}
        {captureTriage}
        {retryReview}
        {renameDialog}
        {helpCenter}
        {decisionLauncher}
      </IdentityContext.Provider>,
    );

  // The tab strip takes the header's title row while the selected chat is one of its tabs, and
  // whenever panes sit side by side (one pane shows only when the layout does not fit); the
  // selected tab's label then carries the title. Otherwise it stays in the banner.
  const tabsAsTitle =
    Boolean(organization) &&
    tabs.length > 0 &&
    !archiveOpen &&
    selectedId !== null &&
    tabs.some((tab) => tab.session_id === selectedId);
  const stripInHeader = tabsAsTitle || visibleMultiPanes;
  const sessionTabs = (
    <TabStrip
      files={panes.layout.files}
      sessions={tabs}
      selectedId={selectedId}
      onSelect={showChat}
      onClose={closeChatTab}
      pinned={pinnedTabs}
      onPin={(id, pinned) => updatePanes((l) => pinTab(l, id, pinned))}
      onMove={moveChatTab}
      menuFor={tabMenu}
      {...(folderOf ? { folderOf } : {})}
      {...(stripInHeader ? { asTitle: true } : {})}
    />
  );
  // What acts on the selected chat beside its title: in the one pane's header, or the focused
  // pane's.
  const codeReason = rootsApi &&
    selectedId &&
    (roots.roots.get(selectedId)?.additional_workspace_ids?.length ?? 0) > 0 && (
      <span className="code-reason">
        Selected folders provide conversation context. Claude may ask before accessing them;
        selection does not grant tool permissions.
      </span>
    );
  // The search button: the panel sits above the pane tree, not per pane, so the one-pane
  // header and the focused pane's header pass it to headerTrail below; the archive header gets
  // its own HeaderTrail.
  const toolsToggle: ToolsToggle | undefined =
    archives && assets
      ? {
          open: toolsOpen,
          panelId: toolsPanelId,
          onToggle: () => setToolsOpen((open) => !open),
          buttonRef: toolsButton,
        }
      : undefined;
  /** The header's running-agents pill and mode chip for `id`'s own session (a bound chat locks
   * the chip), plus the ⋯ menu when `id` is the selected chat and the search button when `tools`
   * is given (only the one-pane header passes it). Called for every pane, selected or not, so the
   * pointerdown that activates an unfocused pane never unmounts the pill or chip before its click
   * lands — only the ⋯ menu, which only the selected chat gets, comes and goes. */
  function headerTrail(id: string | null, selected: boolean, tools?: ToolsToggle): ReactNode {
    const binding = id === null ? undefined : bindings.get(id);
    const trailView = id === null ? undefined : appSnapshot.sessions[id];
    return (
      <HeaderTrail
        running={id === null ? 0 : (runningBySession.get(id) ?? 0)}
        onShowRuns={showRuns}
        mode={trailView?.mode ?? ""}
        modeLocked={binding !== undefined && binding !== "unbound"}
        onMode={(mode) => {
          if (id !== null) void viki.actions.setMode(id, mode);
        }}
        {...(tools ? { tools } : {})}
      >
        <ChatFoldersChip
          folders={trailView?.info?.additional_folders ?? []}
          vault={vaultRow}
          names={folderNames}
          codeCopy={trailView?.info?.code_copy}
          onManage={() => navigate({ view: "settings", page: "workspaces" })}
        />
        {selected ? (
          <HeaderMenu
            others={appSnapshot.others}
            ownLive={otherLiveTitles()}
            onWrapUp={handleWrapUp}
          />
        ) : null}
      </HeaderTrail>
    );
  }
  const headerActions = (
    <>
      {codeReason}
      {headerTrail(selectedId, true, toolsToggle)}
    </>
  );
  const chatMessages = (
    <IdentityContext.Provider value={archiveOpen || staleWindow ? identity : chatIdentity}>
      {archiveOpen ? (
        <ArchivePreview archive={archiveOpen} onOpenUrl={viki.actions.openUrl} />
      ) : (
        renderMessagesFor(selectedId, selectedView)
      )}
    </IdentityContext.Provider>
  );
  // The code preview's font size: the setting when it is a whole number from 11 to 20, else 13.
  const codeFontPx =
    typeof codeFontSize === "number" &&
    Number.isInteger(codeFontSize) &&
    codeFontSize >= 11 &&
    codeFontSize <= 20
      ? codeFontSize
      : 13;
  /** File tab `tab`'s preview request through its folder's current registration, kept until that
   * registration (`codeFolderKey`) or the code client changes, so its pane reads the file again
   * only then. Null until the folder listing holds its folder, so the pane reads nothing; a folder
   * gone since keeps the last request, which the preview then does not admit. */
  function paneRequest(tab: string): CodeOpenRequest | null {
    const file = fileOf(panes.layout.files, tab);
    if (!file || !fileApi) return null;
    const kept = fileRequests.current.get(tab);
    const same =
      kept?.folder.workspace_id === file.workspaceId && kept.path === file.ref ? kept : undefined;
    const folder = codeFolders.find((row) => row.workspace_id === file.workspaceId);
    if (!folder) return same ?? null;
    const folderKey = codeFolderKey(folder);
    if (same?.api === fileApi && same.folderKey === folderKey) return same;
    const request = {
      api: fileApi,
      folder: { ...folder },
      folderKey,
      path: file.ref,
      withDiff: same?.withDiff ?? false,
    };
    fileRequests.current.set(tab, request);
    return request;
  }
  /** The pane holding file tab `owner`, by its label, for a refused Edit's sentence. */
  function paneLabelOf(owner: string): string {
    const index = paneGroups(panes.layout).findIndex((group) => group.tabs.includes(owner));
    return index < 0 ? "another pane" : `Pane ${index + 1}`;
  }
  /** A refused Edit's Show editor: the pane holding the file's editor takes the focus, and its
   * tab the keyboard. */
  function showEditor(owner: string): void {
    if (!fileOf(newestPanes.current.files, owner)) return;
    updatePanes((l) => openTab(l, owner));
    document.getElementById(`session-tab-${owner}`)?.focus();
  }
  /** File pane `group`'s slots: its strip of file tabs and its active file's preview, read
   * through that file's registered code folder (`paneRequest`). */
  function filePaneSlots(group: PaneGroup, label: string): ReturnType<ShellPanes["pane"]> {
    const tab = group.active;
    const request = tab === null ? null : paneRequest(tab);
    return {
      label,
      header: (
        <TabStrip
          files={panes.layout.files}
          sessions={group.tabs.map(tabRow)}
          selectedId={group.id === panes.layout.focused ? tab : null}
          onSelect={showChat}
          onClose={closeChatTab}
          pinned={pinnedTabs}
          onPin={(id, pinned) => updatePanes((l) => pinTab(l, id, pinned))}
          onMove={(id, before) => placeTab(group.id, id, "center", before ?? undefined)}
          menuFor={tabMenu}
          paneLabel={label}
          activeId={tab}
        />
      ),
      messages:
        tab === null ? null : request ? (
          <CodePreview
            key={tab}
            api={request.api}
            folders={codeFolders}
            activeId={codeActive ? activeWorkspaceId : null}
            request={request}
            onQuote={(quote) => quotePane(tab, quote, request)}
            onOpenInEditor={(workspaceId, ref, line) => {
              if (
                fileRequests.current.get(tab) === request &&
                admittedCodeRequest(request, currentFileApi.current) &&
                workspaceId === request.folder.workspace_id &&
                ref === request.path
              )
                viki.actions.openCode(workspaceId, ref, line);
            }}
            onClose={() => closeChatTab(tab)}
            fontSize={codeFontPx}
            lineWrap={effectiveSettings?.["workbench.line_wrap"] === true}
            {...(codeRecovery ? { recovery: codeRecovery } : {})}
            viewId={tab}
            ownerLabel={paneLabelOf}
            onShowEditor={showEditor}
            inPane
            autoFocus={freshFileTab.current === tab}
          />
        ) : (
          <p role="status">Loading file…</p>
        ),
      onActivate: () => activatePane(group.id),
    };
  }
  function renderComposer(
    id: string | null,
    key: string,
    paneView: SessionView | undefined,
    focused: boolean,
    region: string,
  ): ReactNode {
    const modelReviewRequired = paneView?.openRefused === "model_review_required";
    if (
      staleWindow ||
      archiveOpen ||
      handoffBlocked ||
      (paneView?.openRefused && !modelReviewRequired)
    )
      return null;
    if (!editors.capacityAvailable(key)) return <p role="alert">{t("decisions.editor_full")}</p>;
    const selectedId = id;
    const selectedView = paneView;
    const editorKey = key;
    const editor = editorSnapshot.editors[key];
    const forkArmed = id === forkArmedFrom && key === forkEditorId;
    // The same bound/unbound/unknown read `headerTrail`'s `modeLocked` uses, and the value
    // SessionMediaComposer's own model-chip gating reads below. A profile chat steers like any
    // chat whose Claude Code version has recorded steer evidence; elsewhere the server answers
    // `turn_running` and the box keeps the draft with its note, so `steer` below no longer reads
    // `binding`.
    const binding = bindings.has(editorKey)
      ? bindings.get(editorKey)
      : mintedRef.current.has(editorKey)
        ? "unbound"
        : undefined;
    const headerTitle = id
      ? sessionLabel(id, restSnapshot.sessions, metadata.sessions)
      : "Untitled session";
    const runningComposer = selectedView?.turns.at(-1)?.status === "running";
    const composerActivity = activityFor(selectedView);
    const lastPrompt = selectedView ? lastUserText(selectedView) : "";
    const commands = selectedView?.info ? menuCommands(selectedView.info.commands) : [];
    const hasHistoryTurn = selectedView?.turns.some((turn) => turn.history) ?? false;
    const resumedHere = selectedView?.turns.some((turn) => !turn.history) ?? false;
    const excerptParts = excerptsByEditor[key] ?? [];
    const paneRefusal = sendRefusals[key] ?? null;
    const owner = `${region}:${key}`;
    // Shell can mount its new slot before its measured-mode report reaches App.
    const captureFocused = focused && composerOwner === owner && !archivingNow.current.has(key);
    const paneHandsFree = {
      ...handsFree,
      ...(!captureFocused
        ? { state: HANDS_FREE_OFF, bridge: { active: false, listening: false, dispatch: () => {} } }
        : {}),
      toggle: () => {
        if (
          !archivingNow.current.has(key) &&
          currentComposerTarget.current.owner === owner &&
          composerTargetMatches(key, id)
        )
          handsFree.toggle();
      },
    };
    return (
      <IdentityContext.Provider value={focused ? chatIdentity : id ? identityFor(id) : identity}>
        {hasHistoryTurn ? <HistoryNote resumed={resumedHere} /> : null}
        {focused && profileLaunchApi && selectedId && !forkArmed ? (
          <ProfileSessionReview
            api={profileLaunchApi}
            sessionId={selectedId}
            {...(profileReviewRefreshKey !== undefined
              ? { refreshKey: profileReviewRefreshKey }
              : {})}
            onProfileSession={handleProfileSession}
            onUnboundSession={handleUnboundSession}
          />
        ) : null}
        {forkArmed ? (
          <div className="fork-note jp">Your next message starts a fork of "{headerTitle}".</div>
        ) : null}
        {focused && selectedIdState && !selectedId && !forkArmedFrom ? (
          <div className="closed-editor jp">
            This conversation is closed.
            <button
              type="button"
              onClick={() => restoreText(editor?.text ?? "", editor?.attachments)}
            >
              Open in a new message box
            </button>
          </div>
        ) : null}
        {/* Nearest the box, because that is where the words it is talking about are. */}
        {paneRefusal ? (
          <div className="composer-refusal jp" role="alert">
            {paneRefusal}
          </div>
        ) : null}
        {modelReviewRequired && (
          <div className="composer-refusal jp" role="alert">
            <p>{t("llm.model_review_required")}</p>
            <button
              type="button"
              className="btn alt sm"
              onClick={() => {
                setSendRefusals((current) => ({ ...current, [key]: null }));
                prestarts.current.get(key)?.refresh();
                viki.actions.newSession(key);
              }}
            >
              {t("llm.reopen")}
            </button>
          </div>
        )}
        {focused ? <HandsFreeNotice key="voice-notice" handsFree={paneHandsFree} /> : null}
        {focused ? <HandsFreeStrip key="voice-strip" handsFree={paneHandsFree} /> : null}
        <SessionMediaComposer
          sessionId={selectedId}
          sessionView={selectedView}
          binding={binding}
          newChat={mintedRef.current.has(editorKey)}
          prefsApi={sessionPrefsApi}
          modelCatalog={prestartModels}
          refreshKey={shellSnapshot.settings?.revision ?? null}
          actions={viki.actions}
          prestarts={prestarts}
          api={assets}
          focused={captureFocused}
          dictationAllowed={!archivingNow.current.has(editorKey) && !launcherPanelOpen}
          handsFree={paneHandsFree.bridge}
          {...(dictationApi
            ? {
                voiceControl: (primary: boolean) => (
                  <VoiceControl
                    handsFree={paneHandsFree}
                    primary={primary}
                    settings={effectiveSettings ?? {}}
                    patch={(c) => shellStore.patch(c)}
                    openSettings={() => navigate({ view: "settings", page: "chat" })}
                  />
                ),
              }
            : {})}
          attachments={editor?.attachments ?? []}
          onAttachments={(attachments) => editors.attach(editorKey, attachments)}
          destinationLabel={(connections) =>
            selectedId
              ? `Claude${selectedView?.info?.model ? ` · ${modelLabel(selectedView.info.model, null, connections, t("profiles.model.connected"))}` : ""} · "${headerTitle}"`
              : "Claude · New conversation"
          }
          blockedReason={composerBlockedReason(editor)}
          key={editorKey}
          running={runningComposer}
          steer={Boolean(selectedId) && !forkArmed}
          activity={composerActivity}
          editorKey={editorKey}
          value={editor?.text ?? ""}
          onEdit={(text, start, end) => editors.edit(editorKey, text, start, end)}
          selection={{ start: editor?.selectionStart ?? 0, end: editor?.selectionEnd ?? 0 }}
          onSelection={(start, end) => editors.setSelection(editorKey, start, end)}
          submitDisabled={Boolean(
            modelReviewRequired ||
              editor?.submitting ||
              editor?.pendingCommand ||
              editor?.conflictRevision != null,
          )}
          onSend={(text) => handleSend(text, editorKey, selectedId)}
          {...(vaultKnowledgeApi && writeScope
            ? { keepInVault: { api: vaultKnowledgeApi, ...writeScope } }
            : {})}
          onStop={() => {
            if (selectedId) viki.actions.interrupt(selectedId);
          }}
          lastPrompt={lastPrompt}
          commands={commands}
          {...(excerpts && activeWorkspaceId
            ? {
                excerpts: excerptParts,
                onExcerptsChange: (parts) => setEditorExcerpts(editorKey, parts),
                excerptApi: excerpts,
                excerptWorkspaceId: activeWorkspaceId,
              }
            : {})}
          {...(inventoryApi && mcpServersApi
            ? {
                mcpControl: (
                  <McpServersButton
                    inventoryApi={inventoryApi}
                    api={mcpServersApi}
                    sessionId={selectedId ?? null}
                    running={runningComposer}
                  />
                ),
              }
            : {})}
        />
        <div className="draft-status jp" role="status">
          {editor?.dispatchRefusal
            ? dispatchRefusalCopy(editor.dispatchRefusal)
            : editor?.status === "saved"
              ? "Draft saved"
              : editor?.status === "saving"
                ? "Saving draft…"
                : editor?.status === "conflict"
                  ? "The draft changed in another window. Review the saved draft."
                  : editor?.status === "unavailable"
                    ? editor.error === "attachment_unavailable"
                      ? "Can't save the draft. The attached image is no longer on this PC. Remove the attachment or replace the image."
                      : "Can't save the draft. The text stays in this window."
                    : "Loading draft…"}
          {editor?.status === "unavailable" &&
          !editor.pendingCommand &&
          editor.conflictRevision === null ? (
            <button type="button" onClick={() => void editors.flush(editorKey)}>
              Retry save
            </button>
          ) : null}
          {editor?.pendingCommand ? (
            <button type="button" onClick={() => void editors.recoverPending(editorKey)}>
              Check send result
            </button>
          ) : null}
          {editor?.pendingCommand && !editor.submitting && !editor.lastReceipt ? (
            <button type="button" onClick={() => void editors.retryPending(editorKey)}>
              Recheck the same send's receipt
            </button>
          ) : null}
          {editor?.conflictRevision != null ? (
            <button type="button" onClick={() => void editors.reviewConflict(editorKey)}>
              Review the saved draft
            </button>
          ) : null}
          {editor?.lastReceipt ? <p>{commandState(editor.lastReceipt)}</p> : null}
          {editor?.acceptedUncleared ? (
            <>
              <p>
                Check the send history, then tidy up the message box. The text also remains in the
                send history.
              </p>
              <button type="button" onClick={() => editors.releaseAccepted(editorKey)}>
                Checked the record · Continue editing
              </button>
            </>
          ) : null}
        </div>
        {editor?.conflictDraft ? (
          <div className="draft-conflict jp">
            <p>Saved draft</p>
            <pre>{editor.conflictDraft.text || "(empty draft)"}</pre>
            <button type="button" onClick={() => void editors.reconcile(editorKey, "keep-local")}>
              Save what you typed
            </button>
            <button
              type="button"
              onClick={() => {
                restoreText(editor.conflictDraft?.text ?? "", editor.conflictDraft?.attachments);
              }}
            >
              Open the saved text in another message box
            </button>
          </div>
        ) : null}
        {focused ? (
          <DraftRecovery
            key="draft-recovery"
            api={durable}
            onRestore={restoreText}
            onCommandState={settleExcerpts}
          />
        ) : null}
        {focused &&
        Object.values(editorSnapshot.editors).some(
          (entry) => entry.key !== editorKey && (entry.text || entry.attachments.length > 0),
        ) ? (
          <details className="local-drafts jp">
            <summary>Other drafts in this window</summary>
            <ul>
              {Object.values(editorSnapshot.editors)
                .filter(
                  (entry) =>
                    entry.key !== editorKey && (entry.text || entry.attachments.length > 0),
                )
                .map((entry) => (
                  <li key={entry.key}>
                    <button
                      type="button"
                      onClick={() => {
                        setForkArmedFrom(null);
                        if (appSnapshot.sessions[entry.key]) select(entry.key);
                        else {
                          setNewEditorId(entry.key);
                          select(null);
                        }
                      }}
                    >
                      Draft {entry.key.slice(0, 8)} ·{" "}
                      {entry.status === "saved" ? "saved" : "unsaved"}
                    </button>
                  </li>
                ))}
            </ul>
          </details>
        ) : null}
      </IdentityContext.Provider>
    );
  }

  /** A chat pane keeps its own header, messages and composer; focus chooses shared rails. */
  function paneSlots(groupId: string): ReturnType<ShellPanes["pane"]> {
    const all = paneGroups(panes.layout);
    const index = all.findIndex((group) => group.id === groupId);
    const label = `Pane ${index + 1}`;
    const group = all[index];
    if (group && kindOf(group) === "file") return filePaneSlots(group, label);
    const focused = groupId === (panes.layout.focusedChat ?? panes.layout.focused);
    const id = all[index]?.active ?? null;
    const paneView = id === null ? undefined : appSnapshot.sessions[id];
    const key =
      id === forkArmedFrom && forkEditorId ? forkEditorId : (id ?? emptyPaneEditor(groupId));
    const paneComposer =
      id === null || paneView || (focused && key === editorKey)
        ? renderComposer(
            focused ? selectedId : id,
            key,
            focused ? selectedView : paneView,
            focused,
            multiPanes ? `pane:${groupId}` : "single",
          )
        : null;
    let messages: ReactNode;
    if (focused) messages = chatMessages;
    else if (id === null) messages = <EmptyState />;
    else if (paneView)
      messages = (
        <IdentityContext.Provider value={identityFor(id)}>
          {renderMessagesFor(id, paneView)}
        </IdentityContext.Provider>
      );
    else
      messages = (
        <p role="status" className="history-note">
          This chat isn't open in this window.{" "}
          <button type="button" className="btn txt sm" onClick={() => openSession(id, true)}>
            Open
          </button>
        </p>
      );
    return {
      label,
      header: (
        <IdentityContext.Provider
          value={focused ? chatIdentity : id === null ? identity : identityFor(id)}
        >
          <ConversationHeader
            title={
              id === null
                ? "Untitled session"
                : sessionLabel(id, restSnapshot.sessions, metadata.sessions)
            }
            profileChips={!(focused && botChat?.sessionId === id)}
            {...(folderOf && id !== null ? { folder: folderOf(id) } : {})}
            tabs={
              <SessionTabs
                sessions={(all[index]?.tabs ?? []).map(tabRow)}
                selectedId={selectedId}
                onSelect={showChat}
                onClose={closeChatTab}
                pinned={pinnedTabs}
                onPin={(tab, pinned) => updatePanes((l) => pinTab(l, tab, pinned))}
                onMove={(tab, before) => placeTab(groupId, tab, "center", before ?? undefined)}
                menuFor={tabMenu}
                {...(folderOf ? { folderOf } : {})}
                asTitle
                paneLabel={label}
                activeId={id}
              />
            }
          />
          {/* A refused or unopened chat without a composer keeps its header Stop. */}
          {id !== null &&
          paneView?.turns.at(-1)?.status === "running" &&
          (!paneComposer || !editors.capacityAvailable(key)) ? (
            <button
              type="button"
              className="btn txt sm pane-stop"
              onClick={() => viki.actions.interrupt(id)}
            >
              Stop
            </button>
          ) : null}
          {focused ? codeReason : null}
          {headerTrail(id, focused, focused ? toolsToggle : undefined)}
          {focused && botChat?.sessionId === id ? (
            <BotChatBar
              bot={botChat.bot}
              main={botChat.main}
              {...(botChat.role ? { role: botChat.role } : {})}
              {...(botActivityMemory?.sessionId === id ? { memory: botActivityMemory } : {})}
              onManage={() => showBot(botChat.botId)}
            />
          ) : null}
        </IdentityContext.Provider>
      ),
      messages,
      composer: paneComposer,
      onActivate: () => activatePane(groupId),
    };
  }

  const app = (
    <IdentityContext.Provider value={identity}>
      <Shell
        layout={layout}
        onPaneModeChange={setPaneModeVisible}
        railWidths={railWidths}
        onRailWidthCommit={commitRailWidth}
        rails={{ drawer, onClose: () => setDrawer(null) }}
        topBar={topBar}
        leftRail={leftRail}
        header={
          <IdentityContext.Provider value={archiveOpen ? identity : chatIdentity}>
            {archiveOpen ? (
              <>
                <ConversationHeader title={archiveOpen.session.title} />
                <button type="button" onClick={() => setArchiveOpen(null)}>
                  Back to chat
                </button>
                <HeaderTrail {...(toolsToggle ? { tools: toolsToggle } : {})} />
              </>
            ) : (
              <>
                <ConversationHeader
                  title={headerTitle}
                  profileChips={botChat?.sessionId !== selectedId}
                  {...(folderOf && selectedId ? { folder: folderOf(selectedId) } : {})}
                  {...(stripInHeader ? { tabs: sessionTabs } : {})}
                />
                {headerActions}
                {botChat?.sessionId === selectedId ? (
                  <BotChatBar
                    bot={botChat.bot}
                    main={botChat.main}
                    {...(botChat.role ? { role: botChat.role } : {})}
                    {...(botActivityMemory?.sessionId === selectedId
                      ? { memory: botActivityMemory }
                      : {})}
                    onManage={() => showBot(botChat.botId)}
                  />
                ) : null}
              </>
            )}
          </IdentityContext.Provider>
        }
        banner={
          <>
            {backTarget.context ? renderBack(goBack) : null}
            {organization && tabs.length > 0 && !stripInHeader ? sessionTabs : null}
            {archives && assets ? (
              <section
                className="conversation-tools"
                id={toolsPanelId}
                ref={exportTools}
                aria-label="Find, select and export"
                tabIndex={-1}
                hidden={!toolsOpen}
                onKeyDown={(event) => {
                  if (
                    event.key !== "Escape" ||
                    event.nativeEvent.isComposing ||
                    event.nativeEvent.keyCode === 229
                  )
                    return;
                  event.preventDefault();
                  setToolsOpen(false);
                  toolsButton.current?.focus();
                }}
              >
                {selectedView && !archiveOpen ? (
                  <>
                    {conversationSearch ? (
                      <RevisionSearch
                        key={selectedView.sessionId}
                        api={conversationSearch}
                        view={selectedView}
                        onNativePage={(page, generation) => {
                          let target: string | null = null;
                          viki.store.update(selectedView.sessionId, (current) => {
                            if (current.generation !== generation) return current;
                            const result = applyHistoryAnchorPage(current, page);
                            if (!result) return current;
                            target = result.turnId;
                            return result.view;
                          });
                          if (!target) return false;
                          setSelectedTurns(new Set());
                          setHistoryGap(
                            page.newer_after_uuid
                              ? { session: selectedView.sessionId, generation }
                              : null,
                          );
                          setTurnJump({ turnId: target, request: Date.now() });
                          return true;
                        }}
                        onJump={(turnId) => setTurnJump({ turnId, request: Date.now() })}
                      />
                    ) : null}
                    {historyGap?.session === selectedView.sessionId &&
                    historyGap.generation === selectedView.generation ? (
                      <p role="status">
                        Newer history is omitted from this window.{" "}
                        <button
                          type="button"
                          onClick={() => {
                            viki.store.update(selectedView.sessionId, (current) => ({
                              ...current,
                              turns: current.turns.filter((turn) => !turn.history),
                              historyCursor: undefined,
                              historyRequestId: null,
                            }));
                            setHistoryGap(null);
                            setSelectedTurns(new Set());
                            setTurnJump(undefined);
                            viki.actions.reloadHistory(selectedView.sessionId);
                          }}
                        >
                          Return to latest
                        </button>
                      </p>
                    ) : null}
                  </>
                ) : null}
                <ArchiveTools
                  api={archives}
                  assets={assets}
                  archive={
                    selectedView && selectedTurns.size
                      ? selectedArchive(selectedView, selectedTurns, headerTitle)
                      : undefined
                  }
                  onImport={setArchiveOpen}
                  recordMissing={selectedView?.historyError === "transcript_missing"}
                />
              </section>
            ) : null}
            {appReadiness}
            {railWidthReview}
            {lateFailureNotice}
            {tabWindowNote ? (
              <div className="readiness-banner">
                <span role="alert">{tabWindowNote}</span>
                <button type="button" className="btn alt sm" onClick={() => setTabWindowNote(null)}>
                  Dismiss
                </button>
              </div>
            ) : null}
            <ReconnectBanner status={appSnapshot.status} everOpen={everOpenRef.current} />
            <VaultBanner others={appSnapshot.others} />
            <ChatOnlyBanner snapshot={shellSnapshot} onCommand={onCommand} />
            <ReadinessBanner snapshot={shellSnapshot} onCommand={onCommand} />
            {agentChangesApi ? (
              <AgentChanges
                api={agentChangesApi}
                titleOf={(sessionId) => {
                  const row = restSnapshot.sessions.find((r) => r.session_id === sessionId);
                  return row ? rowTitle(row) : "";
                }}
                onChanged={() => void shellStore.refreshSettings()}
              />
            ) : null}
          </>
        }
        messages={chatMessages}
        {...(organization && !staleWindow && !archiveOpen
          ? {
              panes: {
                // Narrower than the panes need, one pane: the focused one, with every tab.
                root: multiPanes ? panes.layout.root : focusedGroup(panes.layout),
                // Shell puts the Message box in this pane: the focused chat pane, also while a
                // file pane has the focus.
                focused: panes.layout.focusedChat ?? panes.layout.focused,
                canSplit,
                pane: paneSlots,
                onDrop: dropOnPane,
                onResize: (splitId: string, sizes: readonly number[]) =>
                  updatePanes((l) => resizePane(l, splitId, sizes)),
              },
            }
          : {})}
        composer={renderComposer(selectedId, editorKey, selectedView, true, "single")}
        overlay={
          toastOpen ? (
            <SessionLimitToast
              live={liveSummaries()}
              maxLiveSessions={maxLiveSessions}
              onClose={requestClose}
              onDismiss={() => setToastOpen(false)}
              onRaiseLimit={openLimitSetting}
            />
          ) : null
        }
        rightRail={rightRail}
        center={
          codeApi && codeRequest ? (
            <>
              {refusalNote ? (
                <div className="composer-refusal" role="alert">
                  {refusalNote}
                </div>
              ) : null}
              <CodePreview
                api={codeApi}
                folders={(shellSnapshot.workspaces?.workspaces ?? []).filter(
                  (folder) => folder.kind === "code",
                )}
                activeId={codeActive ? activeWorkspaceId : null}
                request={codeRequest}
                onQuote={(quote) => quoteSidebar(quote, codeRequest)}
                onOpenInEditor={(workspaceId, ref, line) => {
                  if (
                    currentCodeRequest.current === codeRequest &&
                    admittedCodeRequest(codeRequest) &&
                    workspaceId === codeRequest.folder.workspace_id &&
                    ref === codeRequest.path
                  )
                    viki.actions.openCode(workspaceId, ref, line);
                }}
                onClose={closeCodePreview}
                fontSize={
                  typeof codeFontSize === "number" &&
                  Number.isInteger(codeFontSize) &&
                  codeFontSize >= 11 &&
                  codeFontSize <= 20
                    ? codeFontSize
                    : 13
                }
                lineWrap={effectiveSettings?.["workbench.line_wrap"] === true}
                {...(codeRecovery ? { recovery: codeRecovery } : {})}
              />
            </>
          ) : undefined
        }
        statusBar={statusBar}
      />
      {palette}
      {inbox}
      {arrivalStatus}
      {captureTriage}
      {retryReview}
      {renameDialog}
      {helpCenter}
      {decisionLauncher}
    </IdentityContext.Provider>
  );

  return withFolderPicker(app);
}
