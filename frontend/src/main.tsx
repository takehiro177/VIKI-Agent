import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Tokens and utility classes must land before any component CSS so that a component rule of
// equal specificity (say, a colour on an element that also carries .chip) wins the cascade.
import "./styles/base.css";
import { App } from "./App";
import { createRest } from "./api";
import { createAgentChangesApi } from "./clients/app/agentChangesApi";
import {
  createModelConnectionsApi,
  ModelConnectionsContext,
} from "./clients/app/modelConnectionsApi";
import { createProviderLoginApi } from "./clients/app/providerLoginApi";
import { createShellApi } from "./clients/app/shellApi";
import { createRoutinesApi } from "./clients/automation/routinesApi";
import { createChecksApi } from "./clients/code/checksApi";
import { createCodeApi } from "./clients/code/codeApi";
import { createPreviewsApi } from "./clients/code/previewsApi";
import { createReviewApi } from "./clients/code/reviewApi";
import { createRootsApi } from "./clients/code/rootsApi";
import { createTerminalApi } from "./clients/code/terminalApi";
import { createBetweenChatsApi } from "./clients/conversations/betweenChatsApi";
import { createChatArchivesApi } from "./clients/conversations/chatArchivesApi";
import { createContextApi } from "./clients/conversations/contextApi";
import { createConversationSearchApi } from "./clients/conversations/conversationSearchApi";
import { createDurableApi } from "./clients/conversations/durableApi";
import { createInventoryApi } from "./clients/conversations/inventoryApi";
import { createLimitsApi } from "./clients/conversations/limitsApi";
import { createMcpServersApi } from "./clients/conversations/mcpServersApi";
import { createRunsApi } from "./clients/conversations/runsApi";
import { createSessionMetadataApi } from "./clients/conversations/sessionMetadataApi";
import { createSessionPrefsApi } from "./clients/conversations/sessionPrefsApi";
import { createSessionWindowsApi } from "./clients/conversations/sessionWindowsApi";
import { createSubagentsApi } from "./clients/conversations/subagentsApi";
import { createDecisionLoopApi } from "./clients/decisionLoop/decisionLoopApi";
import { createAssetsApi } from "./clients/media/assetsApi";
import { createAvatarsApi } from "./clients/media/avatarsApi";
import { createBotsApi } from "./clients/messaging/botsApi";
import { createBotUsageApi } from "./clients/messaging/botUsageApi";
import { createMessagingApi } from "./clients/messaging/messagingApi";
import { createNativeAssetsApi } from "./clients/profiles/nativeAssetsApi";
import { createProfileApi } from "./clients/profiles/profileApi";
import { createProfileLaunchApi } from "./clients/profiles/profileLaunchApi";
import { createCapturesApi } from "./clients/vaults/capturesApi";
import {
  createWorkerParser,
  MarkdownParserContext,
} from "./components/Conversation/markdownParser";
import { MediaContext } from "./context/mediaContext";
import { readSessionHandoff } from "./sessionHandoff";
import { createDecisionsApi } from "./state/decisionsApi";
import { createExcerptApi } from "./state/excerptApi";
import { createSkillProposalsApi } from "./state/skillProposalsApi";
import { createVaultApi } from "./state/vaultApi";
import { createVaultKnowledgeApi } from "./state/vaultKnowledgeApi";
import { createVaultOpsApi } from "./state/vaultOpsApi";
import { createViki, readToken } from "./state/viki";
// Vite builds the worklet into one standalone module that `audioWorklet.addModule` loads.
import captureWorkletUrl from "./voice/captureWorklet.ts?worker&url";
import { createDictationApi } from "./voice/dictationApi";
import { microphones, startCapture } from "./voice/dictationCapture";
import { DictationContext } from "./voice/dictationContext";
import { createVoiceboxApi, VoiceboxContext } from "./voice/voiceboxApi";
import { createVoicePackApi, VoicePackContext } from "./voice/voicePackApi";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");

// The page's one connection and its REST client, built from the launch URL's token.
const sessionHandoff = readSessionHandoff();
const token = readToken(window.location, window.sessionStorage, window.history) ?? "";
const scheme = window.location.protocol === "https:" ? "wss" : "ws";
const viki = createViki({ url: `${scheme}://${window.location.host}/ws`, token });
viki.client.connect();
const rest = createRest(token);
const durable = createDurableApi(token);
const assets = createAssetsApi(token);
const avatars = createAvatarsApi(token);
const organization = createSessionMetadataApi(token);
const sessionWindowsApi = createSessionWindowsApi(token);
const archives = createChatArchivesApi(token);
const conversationSearch = createConversationSearchApi(token);
const vaultApi = createVaultApi(token);
const appMemoryApi = createVaultApi(token, fetch, "/api/v2/bot-memory");
const vaultOpsApi = createVaultOpsApi(token);
const vaultKnowledgeApi = createVaultKnowledgeApi(token);
const capturesApi = createCapturesApi(token);
const profileApi = createProfileApi(token);
const profileLaunchApi = createProfileLaunchApi(token);
const sessionPrefsApi = createSessionPrefsApi(token);
const nativeAssetsApi = createNativeAssetsApi(token);
const inventoryApi = createInventoryApi(token);
const mcpServersApi = createMcpServersApi(token);
const contextApi = createContextApi(token);
const limitsApi = createLimitsApi(token);
const subagentsApi = createSubagentsApi(token);
const runsApi = createRunsApi(token);
const decisions = createDecisionsApi(token);
const skillProposals = createSkillProposalsApi(token);
const excerpts = createExcerptApi(token);
const routinesApi = createRoutinesApi(token);
const codeApi = createCodeApi(token);
const rootsApi = createRootsApi(token);
const reviewApi = createReviewApi(token);
const checksApi = createChecksApi(token);
const terminalApi = createTerminalApi(token);
const previewsApi = createPreviewsApi(token);
const providerLoginApi = createProviderLoginApi(token);
const agentChangesApi = createAgentChangesApi(token);
const modelConnectionsApi = createModelConnectionsApi(token);
const decisionApi = createDecisionLoopApi(token);
const messagingApi = createMessagingApi(token);
const botsApi = createBotsApi(token);
const botUsageApi = createBotUsageApi(token);
const betweenChatsApi = createBetweenChatsApi(token);
const dictationApi = createDictationApi(token, {
  workletUrl: captureWorkletUrl,
  startCapture,
  microphones,
});
const voiceboxApi = createVoiceboxApi(token);
const voicePackApi = createVoicePackApi(token);
// The shell services' own REST client. The token reaches it the same way it reaches the other two
// clients — as a Bearer header they build themselves — and nowhere else.
const shell = createShellApi(token);
const markdownParser = createWorkerParser({
  spawn: () =>
    new Worker(new URL("./components/Conversation/markdown.worker.ts", import.meta.url), {
      type: "module",
    }),
  setTimer: (callback, ms) => window.setTimeout(callback, ms),
  clearTimer: (handle) => window.clearTimeout(handle),
});

createRoot(root).render(
  <StrictMode>
    <ModelConnectionsContext.Provider value={modelConnectionsApi}>
      <MediaContext.Provider value={assets}>
        <MarkdownParserContext.Provider value={markdownParser}>
          <DictationContext.Provider value={dictationApi}>
            <VoiceboxContext.Provider value={voiceboxApi}>
              <VoicePackContext.Provider
                value={{ api: voicePackApi, openUrl: viki.actions.openUrl }}
              >
                <App
                  viki={viki}
                  rest={rest}
                  durable={durable}
                  shell={shell}
                  assets={assets}
                  avatars={avatars}
                  organization={organization}
                  sessionHandoff={sessionHandoff}
                  sessionWindowsApi={sessionWindowsApi}
                  archives={archives}
                  conversationSearch={conversationSearch}
                  vaultApi={vaultApi}
                  appMemoryApi={appMemoryApi}
                  vaultOpsApi={vaultOpsApi}
                  vaultKnowledgeApi={vaultKnowledgeApi}
                  capturesApi={capturesApi}
                  profileApi={profileApi}
                  profileLaunchApi={profileLaunchApi}
                  sessionPrefsApi={sessionPrefsApi}
                  nativeAssetsApi={nativeAssetsApi}
                  inventoryApi={inventoryApi}
                  mcpServersApi={mcpServersApi}
                  contextApi={contextApi}
                  limitsApi={limitsApi}
                  subagentsApi={subagentsApi}
                  runsApi={runsApi}
                  decisions={decisions}
                  skillProposals={skillProposals}
                  excerpts={excerpts}
                  routinesApi={routinesApi}
                  codeApi={codeApi}
                  rootsApi={rootsApi}
                  reviewApi={reviewApi}
                  checksApi={checksApi}
                  terminalApi={terminalApi}
                  previewsApi={previewsApi}
                  providerLoginApi={providerLoginApi}
                  agentChangesApi={agentChangesApi}
                  decisionApi={decisionApi}
                  messagingApi={messagingApi}
                  botsApi={botsApi}
                  botUsageApi={botUsageApi}
                  betweenChatsApi={betweenChatsApi}
                />
              </VoicePackContext.Provider>
            </VoiceboxContext.Provider>
          </DictationContext.Provider>
        </MarkdownParserContext.Provider>
      </MediaContext.Provider>
    </ModelConnectionsContext.Provider>
  </StrictMode>,
);
