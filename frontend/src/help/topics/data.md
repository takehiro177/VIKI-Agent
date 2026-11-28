# Your data and privacy

See where VIKI keeps your data, export or back up your vault, and reset the app without losing a chat or a vault.

## Where your data goes

**Settings** › **Diagnostics and About** shows the same list.

- Chats, routines and Bots: What you send, attach or let a tool read goes to your own Claude Code, which sends it to the selected model provider under your account. Its tools, such as web fetch and MCP servers, can reach other sites under your permission rules.
- Model connections: For a saved model connection, VIKI Agent forwards Claude Code's model requests to the endpoint you chose, using the key you entered. Test connection sends a short prompt. List models requests model IDs.
- VIKI Agent itself: No VIKI account, payment or usage reporting. Nothing goes to VIKI Agent's maker.
- Update check: The installed app asks GitHub whether a newer VIKI Agent release exists, a minute after it starts and every 6 hours while Check for updates automatically is on, and when you choose Check now. GitHub counts each check as a download of the release's update file. Download opens the release page in your web browser; the app downloads and installs nothing itself.
- Speech: Dictation, read aloud with system voices, and your own Voicebox app run on this computer. The browser's online voices are not used.
- Natural voices: Off until you install them. Installing downloads pinned files from GitHub and Hugging Face; speech then runs on this computer.
- Messaging: Off until you set it up. Slack, Discord or Mattermost then carries your bridge messages, VIKI's final replies, its permission and question requests, and questions from your other chats once you send !viki questions on.
- Decision: Off until you turn on Proactive VIKI and save a TypeSafe key. Then TypeSafe's Decision model, at api.typesafe.ai, gets titles, facts with digits removed and excerpts up to your send level, and any question wording you propose, while it is tried and in every request once you approve it, under your key. Anything under a folder or tag on your Never send list stays out.
- Code folders: Push sends a reviewed commit to the folder's own Git remote when you select it. Checks and previews run the commands you type.
- Links: Links and pull request pages you open go to your web browser.
- Stored locally: VIKI Agent's own data and logs, and Claude Code's transcripts, stay on this computer; Claude Code's own settings decide how long it keeps them. Vaults stay in the folders you chose.

Claude Code keeps each conversation's transcript under `~/.claude/projects/`.

The app keeps its own data folder for each platform. Open **Data locations** in the App menu to see the exact paths on your computer.

## Export a chat

In a chat, open its **Find, select and export** tools. Select **Export Markdown** for a readable file, or export a JSON archive to keep its attachments too.

An imported archive opens read-only, so you can look back at it without starting a new chat.

## Back up your vault

From the gear's **App menu**, select **Data locations** to open the Data view, then scroll to its **Backup** panel. Select **Back up** to copy a vault's notes to a folder outside every vault. This copy is not encrypted, so pick a destination only you can read.

Select **Restore into a new folder** to bring a backup back. It registers the copy read-only, in a new folder, and never replaces an existing vault.

## Diagnostics and settings files

In the same Data view, open **Export** and choose **Diagnostics** or **Settings**. Then pick what to include, such as **Folder paths**, **Identity**, or **Recent log lines**.

Inspect the file yourself before you share it with anyone. **Import settings** only previews the incoming values; you still choose what to apply.

## Reset

The Data view's **Reset** clears only its own listed categories, and nothing else. Type RESET to confirm, since this cannot be undone. Your chat transcripts and every vault stay untouched.
