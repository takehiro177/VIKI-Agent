/**
 * Product-framing lines and the data-flow table, in the exact English the UI renders them.
 * Repeated word for word in Help (frontend/src/help/topics/welcome.md, data.md, voice.md and
 * settings.md) and pinned against
 * the real network surface by tests/test_product_statements.py. Change the wording only here.
 */

/** Settings > Diagnostics > About, first line: what the product is. */
export const PRODUCT_LINE =
  "VIKI Agent is a productivity app: an AI assistant for your notes, code and tasks. Its replies come from the model you select, through your own Claude Code, and can be wrong.";

/** Settings > Diagnostics > About, second line: who makes it. */
export const NOT_AFFILIATED =
  "VIKI Agent is independent and is not affiliated with or endorsed by Anthropic.";

/** One row of the "Where your data goes" card: a named flow and the fact that describes it. */
export interface DataFlow {
  label: string;
  fact: string;
}

/** Settings > Diagnostics > About, the "Where your data goes" card, in display order. */
export const DATA_FLOWS: readonly DataFlow[] = [
  {
    label: "Chats, routines and Bots",
    fact: "What you send, attach or let a tool read goes to your own Claude Code, which sends it to the selected model provider under your account. Its tools, such as web fetch and MCP servers, can reach other sites under your permission rules.",
  },
  {
    label: "Model connections",
    fact: "For a saved model connection, VIKI Agent forwards Claude Code's model requests to the endpoint you chose, using the key you entered. Test connection sends a short prompt. List models requests model IDs.",
  },
  {
    label: "VIKI Agent itself",
    fact: "No VIKI account, payment or usage reporting. Nothing goes to VIKI Agent's maker.",
  },
  {
    label: "Update check",
    fact: "The installed app asks GitHub whether a newer VIKI Agent release exists, a minute after it starts and every 6 hours while Check for updates automatically is on, and when you choose Check now. GitHub counts each check as a download of the release's update file. Download opens the release page in your web browser; the app downloads and installs nothing itself.",
  },
  {
    label: "Speech",
    fact: "Dictation, read aloud with system voices, and your own Voicebox app run on this computer. The browser's online voices are not used.",
  },
  {
    label: "Natural voices",
    fact: "Off until you install them. Installing downloads pinned files from GitHub and Hugging Face; speech then runs on this computer.",
  },
  {
    label: "Messaging",
    fact: "Off until you set it up. Slack, Discord or Mattermost then carries your bridge messages, VIKI's final replies, its permission and question requests, and questions from your other chats once you send !viki questions on.",
  },
  {
    label: "Decision",
    fact: "Off until you turn on Proactive VIKI and save a TypeSafe key. Then TypeSafe's Decision model, at api.typesafe.ai, gets titles, facts with digits removed and excerpts up to your send level, and any question wording you propose, while it is tried and in every request once you approve it, under your key. Anything under a folder or tag on your Never send list stays out.",
  },
  {
    label: "Code folders",
    fact: "Push sends a reviewed commit to the folder's own Git remote when you select it. Checks and previews run the commands you type.",
  },
  {
    label: "Links",
    fact: "Links and pull request pages you open go to your web browser.",
  },
  {
    label: "Stored locally",
    fact: "VIKI Agent's own data and logs, and Claude Code's transcripts, stay on this computer; Claude Code's own settings decide how long it keeps them. Vaults stay in the folders you chose.",
  },
];
