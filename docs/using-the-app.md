# Using VIKI Agent

VIKI Agent is a local chat window for Claude Code. By default, it runs the Claude Code CLI on the
sign-in Claude Code is set up with, such as your Claude plan or an API key. It uses the CLI's own instructions, skills, hooks and permissions.

A chat can read or change files through Claude Code tools, according to those settings. See
[Security](security.md) before opening a workspace you do not trust.

## Using your own model

Open **Settings › Providers › Chat models** to choose your Claude Code sign-in or add a model
or routing group from Magpie. Magpie manages provider API keys and routing. VIKI
chooses which model or group a chat uses; Claude Code handles its tools and permissions.

**Claude Code sign-in** uses the sign-in your Claude Code is set up with: your Claude plan, an
API key or a cloud provider. To use a cloud API or local
model server, set it up in Magpie as described below. You can save up to eight models or direct
connections in VIKI.
Choose a model with tool-calling support. Image input and reasoning controls depend on the model.

Adding a model does not change your default. Select **Use for new chats** beside a saved model,
or select it from a new chat's model menu. Start a new chat to change providers.

### Use Qwen or another model in a chat

A custom model supplies the replies and tool requests. Claude Code remains installed and
manages the conversation, tool execution and permission prompts. The model's serving endpoint
must support streaming replies and tool calling.

For example, add a Qwen endpoint in Magpie, add its model to VIKI and select it for a new chat.
Requests travel from Claude Code through VIKI's local gateway, then Magpie, to Qwen.
You can also add the Qwen endpoint under **Advanced — Direct connections**. That route uses
Claude Code and VIKI's gateway and skips Magpie; Magpie's routing groups and failover do not apply.
Use the model ID and API format exposed by your serving endpoint.

### Manage routing with Magpie

Select **Open Magpie** in **Settings › Providers** to manage providers, routing
groups and usage. In VIKI Agent's own window, the dashboard opens in VIKI's main content area and
follows VIKI's colors, typography and font size. VIKI's navigation stays available; select
**Back to chat models** to return to **Chat models**. In a browser window, Magpie opens
in your system browser.

1. Add providers and, optionally, a routing group in Magpie. Enter provider API keys there.
2. Return to **Chat models** and select **Choose a model or group**.
3. Select **List models**, choose a **Magpie model or routing group**, and **Add model to VIKI**.
4. Select **Use for new chats**, or choose that saved model for an individual new chat.

If you enabled authentication for Magpie's gateway, expand **Gateway access key** and enter
that key before selecting **List models**. Otherwise, leave it empty. Provider API keys belong
in Magpie; you do not need to enter them again in VIKI.

Magpie starts when you open its dashboard or use its connection, and VIKI stops it when VIKI begins
to quit. On macOS and Linux, after a crash or a forced kill, a Magpie left running is stopped the
next time VIKI starts, or at a later start or Magpie use if it cannot be identified then; on
Windows it ends with VIKI. If it stops unexpectedly, select **Open
Magpie** to restart it. Installed copies of VIKI Agent include Magpie; a copy run from source
prepares it once with `uv run python scripts/prepare_magpie.py` in the repository folder, then
you open **Settings › Providers** again. Its local ports are 3426 for the
gateway and 3431 for the dashboard; another program using either port prevents startup.
Let VIKI manage this instance's lifetime. Keep Magpie's background-service installation and
self-updates off for the bundled instance; VIKI installers carry its pinned executable.

A routing group can retry requests or switch upstream providers and models. Changes to a
group in Magpie can affect an open chat using that group. Claude Code handles VIKI chat tool
permissions. Magpie's agent configuration and plugins are managed in Magpie.
See [Magpie security and privacy](security.md#magpie-routing-and-management) before using
those features.

Use VIKI's saved connection to route its chats. Changing Claude Code's provider on Magpie's
Agents page can conflict with VIKI's provider checks. Disconnect that Claude Code
configuration in Magpie and run **Check Claude Code** again if VIKI reports a conflict.

### Connect directly without Magpie

Open **Advanced — Direct connections** in **Chat models** and fill in the **Add connection**
form. Enter a name, API base URL, model ID and API key if the server requires one. Choose the
server's API format: Chat Completions, Responses API or Anthropic Messages. Start your local
model server before connecting it. Direct connection keys are encrypted on this computer.

- **List models:** queries available IDs without generating a reply.
- **Test connection:** sends a short fixed prompt. Your provider may charge for it.

Select **Save connection**, then **Use for new chats**, or choose the saved connection from a
new chat's model menu. Saved direct connections appear in the model menu alongside Magpie
models.

### Compact a custom-model chat

`/compact` is Claude Code's built-in command for summarizing a conversation so you can
continue with less context. When Claude Code offers it in the chat's command menu, VIKI
forwards it to Claude Code. Compaction support depends on the selected model and serving API,
even when ordinary chat replies work.

Automatic compaction follows Claude Code's context management. VIKI does not discover your
custom endpoint's configured context limit, and token counting for context checks is an
estimate. A server with a smaller context limit may reject a long conversation before
automatic compaction starts. Request compaction before that limit is reached.

## Chat and approve work

This section covers sending messages, reading replies, voice, approvals and MCP servers.

### Send a message

Select **New session** or press `Alt`+`N`. In a chat:

- Type a message and press `Enter`. Use `Shift`+`Enter` for a new line.
- Type `/` to choose a command or skill.
- Press `↑` in an empty message box to recall your last message in that chat.
- Select **Stop** or press `Esc` to interrupt a running reply.

The message box stays available while Claude Code replies. For all keyboard shortcuts, press `?` outside a
text field.

### Permission mode and running agents

The chat header shows the chat's permission mode as Claude Code reports it.

- **Switching modes:** choose **Manual**, **Accept edits**, **Auto** or **Plan** from the header to
  switch the running chat, also while it replies. The header changes once Claude Code confirms the
  switch.
- **Profile chats:** a profile chat keeps the mode its profile sets.
- **Running agents:** while a chat's agents run, its header shows how many. Select that to see their
  runs in the VIKI panel.

### Send while a reply runs

If you try to send while a reply is running, VIKI Agent steers the message. It waits until Claude
Code reads it after the current step, then shows as yours.

- Up to 8 messages can wait this way.
- If one is still waiting when you choose **Stop**, it runs next.

These messages are not steered. Each stays in the box, with a note to send it when the reply ends:

- a message that starts with a known slash command
- any message in **Message VIKI**
- any message after you choose **Fork**, until the fork's first message is sent

A profile chat steers too, only on Claude Code 2.1.281. On
any other version, newer ones included, its message stays in the box with a note.

> [!IMPORTANT]
> After an error, stop, disconnect, window close or restart, check **Drafts and send history**
> before sending again. VIKI Agent does not automatically retry an uncertain send or restart a
> failed Claude Code process.

### Activity and colors

The tile orb above the message box shows the chat's observed activity, with a status caption.

- **During a reply:** a monochrome glow pulses inside the box's border.
- **A waiting approval, plan or question:** keeps the orb and border still.
- **Idle, connection and error states:** also have their own captions.
- **Reduced motion:** keeps the indicators still when your computer's setting asks for it, or when
  **Reduce** is chosen in **Settings › Appearance and Accessibility › Animations**.

Collapsed tool groups show their tool and error counts. Expand them to read individual results.

In the chat log:

- **Your messages:** a blue edge and a tinted background.
- **VIKI's name:** teal.
- **Thought headings:** purple.
- **Tool activity:** amber. Tool errors include a red **Error** label.
- **Links in replies:** blue and underlined.

### Drafts, attachments and turn actions

Paste or drop an image onto the message box to attach it. A draft's save status tells you whether
its text and attachment reference reached local app storage.

> [!WARNING]
> If saving fails, keep the window open and copy important text elsewhere before closing it.

- **Dictation:** when available, puts recognized words into the box for review. It does not send
  them.
- **Read aloud:** a finished reply can offer it when the browser has a local speech voice.
- **More actions (⋯):** under each finished turn, holds **Quote into draft**,
  **New chat with this turn**, **Edit in a new chat** and **Select for export**. A selected turn
  shows Selected for export.
- **Resizing the box:** drag the line above the message box to resize it. Double-click the line, or
  press Enter on it, to reset its height.

### Read replies aloud

In **Settings › Chat**, **Read replies aloud with** chooses how a finished reply is read aloud:

- **System voices:** this computer's own.
- **Natural voices:** a voice pack VIKI installs, on Windows PCs with an NVIDIA graphics card.
- **My own Voicebox app (advanced):** the separate
  [Voicebox](https://github.com/jamiepine/voicebox/releases) app on this computer.

#### Set up natural voices

Choosing **Natural voices** shows an install card below it. Before natural voices are installed,
the card shows **Install natural voices** and names:

- **Download size and free space:** around 6.0 GB to download, around 12.4 GB free while
  installing, and around 8.2 GB once installed.
- **Licences:** for the Voicebox server, NVIDIA CUDA libraries, Qwen3-TTS model and Kokoro-82M
  model.

If Kokoro is missing from a natural voices install, VIKI offers **Add the fast voice**: Kokoro, a 0.35 GB natural
voice that reads much faster on your PC.

The card's controls:

- **Cancel:** while downloading, pauses the download.
- **Resume:** continues a paused, network-interrupted or disk-full download where it stopped.
- **Try again:** restarts a download that did not match VIKI's pinned copy.
- **Repair:** once installed, rebuilds a natural voice that stops working.
- **Remove:** once installed, deletes a natural voice after you confirm. Installing again downloads
  everything again.

A natural voice starts the first time a reply is read or a voice is previewed. Its first reading
waits longer while it starts. It stops after 5 minutes unused.

Without a supported Windows PC and NVIDIA graphics card, the card explains why. Where it can help,
it offers **Add a Windows voice…**, which opens Windows' own speech settings, or **Check again**, to
recheck the graphics card.

#### Natural voice for each language

In **Settings › Chat**, **Natural voice** sets which voice reads each language. Each choice is
listed with its gender:

- a Kokoro voice (English, Chinese, French, Portuguese, Spanish, Italian)
- a Qwen3 voice
- the system voice

**Preview** plays a short fixed sample, and **Stop** ends it. Settings also:

- shows the speed measured on this PC
- warns when a voice is too slow for hands-free, or its files are missing
- names the system voice that reads a language, or says none is installed
- lists recent hands-free fallbacks

While hands-free is on, natural voices stay ready. A reply the system voice read instead offers
**Voice settings…**.

#### Your own Voicebox app

Choosing **My own Voicebox app (advanced)** reads replies with the separate
[Voicebox](https://github.com/jamiepine/voicebox/releases) app instead, in languages your system
voices may lack, such as Japanese, Chinese and Korean. To set it up:

1. Install Voicebox.
2. Download a model.
3. Create a voice profile for each language you want, such as a Japanese profile for Japanese.
4. Set **Voicebox voice**: select **Check Voicebox** to list its profiles, then choose one.

Qwen voices in Voicebox take model size 0.6B or 1.7B; 0.6B suits a GPU with 8 GB.

- **Get Voicebox:** links to where to download it.
- **Read-aloud speed:** changes only a system voice's pace. A natural or Voicebox voice speaks at
  its own pace.
- **Voicebox address:** change it only to point at a Voicebox you run elsewhere on this computer.
  Its own `python -m backend.main` listens on port 8000, not the installer's 17493.

#### Fallback to a system voice

**Read aloud** on a reply speaks through the chosen engine for a language it covers. It falls back
to a system voice:

- when one is installed for a language the engine does not cover
- when natural voices or Voicebox cannot answer

A note explains why, such as natural voices not installed yet or Voicebox not running. It can offer
**Install natural voices…** or **Open Windows voice settings**.

### Hands-free voice conversation

With the message box empty, the round **Hands-free** button takes **Send**'s place. Its tooltip
reads **Start voice conversation**, plus what it will talk to: your system voice, a natural voice,
or a named Voicebox voice.

- **Once you start typing:** the button moves beside **Send** or **Stop**.
- **While a conversation runs:** its tooltip reads **End voice conversation (Esc)** instead.

The chevron beside it opens **Voice options**:

- Choose **System voice**, **Natural voice** once installed, or a Voicebox voice.
- **Natural voices…** opens Settings to install or manage them.
- **Get Voicebox** appears if your own Voicebox app is not answering.
- Select **Check Voicebox** when Voicebox is not answering.
- Turn on **Speak while VIKI writes** to have hands-free start speaking the answer before the reply
  finishes. It needs a natural or Voicebox voice chosen first.
- Open **Voice settings…**.

Below 800 px wide, the chevron disappears, and while you type the whole control does too. Use
**Settings › Chat** there instead.

Dictation and voice status:

- **While dictation listens:** its mic shows a square.
- **While it turns your words into text:** the mic shows a spinner.
- **While a voice is prepared after the reply ends:** the strip says **Getting the voice ready**.
  Pressing the microphone there listens again.
- **Whenever an answer is not read aloud, or the system voice reads it instead:** the strip says
  why in one line.

If you edit the message box while voice input is finishing, the edited draft stays unsent. Review
it and send it yourself.

### Needs you and Approvals

In the Chat and Code views, **Needs you** stays at the top of the right panel's **Overview** and **Code** tabs and shows the number of waiting items.

A tool permission from a chat that's off screen can be answered in the list with **Allow once** or **Deny**. The row shows the whole command beside the buttons, on the lines it was written on. A long command scrolls, and a line under it says when part of it is out of view. For half a second after the buttons appear or move, a mouse click on **Allow once** or **Deny** is ignored and the buttons look dimmed; the keyboard still works. An agent run opened from the VIKI panel lists its waiting tool requests the same way, with the same buttons. Other tool permissions, plans and questions open their chat; selecting a plan or question focuses its exact card. New requests are announced once per batch: a single request gets a short description, while several get a count.

When the window is too narrow to dock the right panel, the **VIKI panel** toggle shows a dot when something needs you. Its accessible description and tooltip give the waiting count.

The **Turn on desktop notices** button in **Needs you** enables the same notification setting used for routine notices. VIKI Agent offers desktop notices only while it is in the background. Browser notification permission and quiet hours still apply.

The list shows:

- tool requests, plans and questions from your chats
- the note and skill changes VIKI proposes

From that list:

- **Open Approvals:** in the list's heading, opens **Approvals**, the review screen. There, a tool
  request lets you allow once or deny.
- **A waiting proposal:** selecting it opens Approvals on it.
- **Plans and questions:** open their conversation for an answer.

**Conversation requests** lists waiting tool permissions, plans and questions. Read a tool request's
action and target before choosing **Allow once** or **Deny**. Allowing lets Claude Code attempt the
action; check the conversation for the result.

A request's text is shown on the lines it was written on. A character that hides or reorders text,
such as a text-direction control or a zero-width space, is shown as its code, such as `<U+202E>`,
and the card says so; a card whose text runs past its box says that part of it is out of view. For
a tool VIKI Agent has no summary for, such as an MCP tool, the card lists every argument, and says so
first when they were shortened to fit.

These cards appear only when Claude Code asks. Its mode, rules and hooks can settle other tool calls
without an app card.

#### Proposal cards

Proposal cards show their kind, status, source and what each choice does.

- **Review details** opens the item's source context and review controls.
- **Source** and **Status** filter the returned proposals.
- **Needs review** and **Draft handoffs** appear before collapsed **History**.

Approving a note or skill proposal creates a chat draft asking VIKI to apply the change.

Retros run in a chat: ask VIKI for `/retro` and answer its proposals there.

### MCP servers

The chat bar's **MCP** button opens the **MCP servers** dialog for the current chat. The dialog
opens above the chat bar; press **Esc** or click outside it to close it.

- **On or off:** switch a server on or off there. A server shown as **Failed** offers **Retry**.
- **Where a choice applies:** Claude Code saves each choice for this chat's folder. New chats
  there, and `/mcp` in a terminal there, follow it. Other open chats on that folder keep their
  connection until they reconnect.
- **Before Claude Code starts:** if this chat's Claude Code has not started yet, the dialog says so
  and does not start it.

#### Add an MCP server

Select **Add an MCP server** in that dialog, or open **Settings → Providers**, for setup commands
and JSON examples. VIKI Agent uses your own Claude Code's MCP configuration and authentication.

- **Project configuration:** named `.mcp.json`, with the leading dot, at the top level of the
  folder where the chat starts. Its top-level object is `mcpServers`. **Where to put .mcp.json**
  in **Settings → Providers** shows that folder for the active workspace, with **Copy folder
  path**. With no active workspace path, it points to the folder under **Your vault** in
  **Settings → Workspaces and Vault**. In chat-only mode, chats start in VIKI Agent's own folder:
  choose **All my folders** below, or set a vault.
- **Code folders:** a chat in a code folder starts in that folder, or in its worktree, and reads
  the `.mcp.json` there. Code folders ticked for new chats, and a Bot's code copy, add their
  files, skills, commands, agents and plugin choices; Claude Code does not read their
  `.mcp.json`.
- **New chats:** a running chat keeps the servers it started with. After you add or change
  `.mcp.json`, start a new chat.
- **Profile servers:** servers named `viki_…` come from an agent profile's MCP configuration in
  **Bots → Bot settings**. VIKI Agent adds them only to that profile's chats in an Ordinary
  workspace.
- **VS Code's `.vscode/mcp.json`:** uses a different format, and is not Claude Code's project
  configuration.

For Atlassian's Jira and Confluence server, run this in a terminal in the chat's folder, using the
same Claude Code installation as VIKI Agent:

```text
claude mcp add --transport http --scope local atlassian https://mcp.atlassian.com/v2/mcp
```

The setup command changes with your selection:

- **This folder, for me:** configures the server for that folder.
- **All my folders:** uses Claude Code's user scope.
- **This project, shared through .mcp.json:** uses its project scope.

Then:

1. Run `claude` in that folder.
2. Open `/mcp`, and follow the server's approval and sign-in steps.
3. Start a new VIKI chat in the same folder and send a message.
4. Open **MCP** to check its reported connection.

**Refresh** checks the running chat; it does not load newly added configuration. See
[Claude Code's MCP guide](https://code.claude.com/docs/en/mcp) and
[Atlassian's setup guide](https://atlassian.github.io/atlassian-mcp-server/) for authentication
options and server requirements.

#### MCP servers for one agent profile

To attach servers to one agent profile:

1. Open **Bots → Bot settings** and select the profile.
2. Expand **Import MCP JSON** under **MCP configuration**.
3. Paste a Claude `mcpServers` object or a VS Code `servers` object.
4. Select **Add from JSON**. Import adds checked entries to the draft without starting or
   connecting to them.
5. Review the fields and save the profile.

Import details:

- **Imports that add nothing:** an import with an unsupported entry, a duplicate name or more than
  eight servers adds nothing. Existing entries stay in the draft.
- **Size:** the JSON is limited to 64 KiB.
- **Entries:** profile entries accept stdio commands and arguments or HTTP URLs, with the
  profile's existing value limits.
- **Live names:** start with `viki_`.
- **Workspace:** app-profile servers require an **Ordinary** workspace.

For OAuth, environment variables, headers or other configuration fields, configure the server in
Claude Code instead. Profiles inherit those servers too.

> [!IMPORTANT]
> Keep credentials out of profile definitions.

### Ask a chat to change settings

A chat you opened in the VIKI Agent window can read and change your settings, add and test
routines, and create Bots, if you ask it to, by running its own command. It answers four kinds of
request: **status**, **settings**, **routines** and **bots**.

- **New vaults:** **Start with VIKI** ships the skill that teaches a chat to use this. A Start
  with VIKI vault made earlier gets it through a starter update. **status** includes each
  vault's starter update state.
- **Permission:** running it is an ordinary tool call, so it shows the chat's usual permission
  card in every mode except **Auto**.
- **What changes right away:** how the window looks (theme, density, font scale, reduced
  motion), the rails' and launcher's layout, the code editor's font and wrap, a Bot's color and
  icon, and read-aloud speed and voice.
- **Everything else waits for your Apply:** every other setting, and turning a routine on, adds
  a banner naming the chat and the exact change — a setting's whole current and new value (for a
  reset, the value it discards), or a routine's schedule, profile, workspace and whole prompt —
  with **Apply** and **Reject**. A long value scrolls in its own box, and a hidden or
  text-reordering character shows as its code, for example `<U+202E>`. **Reject**, or leaving it
  for 10 minutes, changes nothing.
- **Testing a routine:** add it, then test it once before turning it on, and wait for that run to
  finish before enabling it. Still a draft once the run has ended means the test failed.
- **What a chat can't do here:** test a routine it didn't add itself since VIKI Agent last
  started; add or test a routine for a profile that has MCP servers, a Bot, or a pinned
  permission mode other than **Plan**; or create a Bot for a profile that already runs routines.
  Do those from **Routines** or **Agents** yourself.
- **Which chats can do this:** a chat you opened in the window. A chat VIKI starts and runs on
  its own — a Decision mission, an inbox suggestion run, a messaging bridge's conversation — gets
  none of its own while VIKI runs it, but behaves like any other chat you opened once you reopen
  it from the window. A profile chat, a Bot's chat or task thread, and a routine run never do,
  reopened or not.

## Read and organize conversations

The left rail lists sessions; opening an old session resumes its conversation.

### Tabs and panes

With chats open in tabs, the tabs sit in the conversation header, and the selected tab is the
chat's title.

Show up to four chats side by side:

- **Split:** drag a tab or session onto a pane's edge to split it.
- **Move:** drag a tab or session onto the middle of a pane to move it there.
- **Tab menu:** the tab menu's (⋯) actions do the same.

A chat tab's menu (⋯) also offers **New window**, which opens that chat in its own window. If the
new window cannot open, a message above the chat explains why; dismiss it or retry.

In each pane:

- The message box keeps its chat's draft and attachments.
- **Stop** interrupts only that chat.
- The header shows its own chat's mode and running agents.
- Clicking the pane selects the chat shown in the sidebars.

On narrow windows:

- **Below 1280 px:** the right sidebar becomes a drawer, and one pane shows every tab.
- **Below 800 px:** the left sidebar becomes a drawer too.

Use Tab or Shift+Tab to move between a drawer’s controls, including the headings of collapsed sections. The controls inside a collapsed section join the sequence when you expand it.

If the saved pane layout cannot fit its available width or height, the center shows a single pane.
The layout returns when there is room.

On restart:

- Chat tabs wait for session labels to load.
- Archived chats stay closed until you open them from the archived list.
- Other saved tabs return with the pane layout.

#### Close a tab or a chat

- **Closing a tab:** removes it from the strip and leaves the chat itself alone.
- **Closing the chat itself:** also closes its tab in this window. That includes **Close session**
  in its row menu, or any other **Close** for that chat.
- **A tab whose chat this window no longer holds:** stays in its pane and opens the chat again on
  click.

### Session labels

Archive, pin, color and unread are labels kept by VIKI Agent. Archive closes an open session,
including an idle chat, then hides it from the main list.

> [!IMPORTANT]
> Archive does not preserve a session's transcript. A missing transcript cannot be resumed from
> those labels.

### Search and chat tools

Press `Ctrl`+`K` (⌘+`K` on macOS) to search session titles and the text you and Claude Code wrote.

- Search excludes tool output and sessions in **Other**.
- It can report partial results when a scan reaches its limit. Try a narrower query or open the
  transcript.

The search button in the chat header, or `Ctrl`+`F` (⌘+`F` on macOS), opens the chat's own tools
under the header. `Esc` closes them. With them you can:

- search this conversation
- select turns to export
- import a read-only copy

In the code editor, `Ctrl`+`F` searches the file instead.

Use the arrow keys to move through search results and `Enter` to open the selected result.

Bookmarks in the Vault view keep a note's exact reference. If you move or delete the note, the
bookmark does not follow it.

### Between chats

**Between chats**, in the right panel, lists the newest messages your recent chats received from
other Claude Code sessions. Each shows who sent it, which chat it went to, when, and its first
line.

- Select one to open that chat.
- A sender that is not in your session list, or has closed, shows its Claude Code session name.
- The list only reads; it sends nothing.

### Sources and files

A finished reply shows the notes or other sources Claude Code read as chips. Open a chip to
inspect its target.

- **No vault sources:** means it read no vault note for that reply. It does not account for every
  other source of an answer.
- **+N more:** expands the remaining sources.
- **Show fewer:** collapses them.

**Files in this chat**, in the right panel, groups these for this chat:

- the files Claude Code changed and read
- the pages it fetched
- the searches it ran

Each file appears once with a count. Files only agents touched sit under **By agents**.

**Save as note** offers an editor for an ordinary vault on a supported drive, or a chat draft for a
protected vault. Both have a review step before anything is sent or created.

### Tool results

Select a finished tool call in a chat to open its result. **Technical details** under the result
lists its type and tool and, when available, its hash and size.

Read the result's status and path before relying on it. Receiving a result does not verify its
contents.

## Workspaces, notes and code

This section covers your vault and its notes, code folders and the Code view.

### Choose or create a vault

In **Settings → Workspaces and Vault**:

- **Change vault…:** opens your system's folder picker on a different vault.
- **New vault…:** creates one there instead.

Switching takes effect on the next launch.

A new vault starts from one of two choices:

- **Start with VIKI (recommended):** a ready second brain with folders, note templates, empty
  memory, Kanban, agents and skills. The form lists what **Start with VIKI** writes before you
  create it.
- **Blank folder:** holds only a welcome note, plus `.viki/vault.json` if you tick
  **Write portable metadata**.

**Starter updates:** when VIKI Agent ships newer Start with VIKI files, Settings › Workspaces and
Vault says so, and Settings › Instructions and Persona lists each change. Files you never edited
take the new text, files you changed get a merge you approve, files you deleted stay deleted, and
your notes, memory, log, Home and maps are left out. **Update in a chat** drafts one message for a
new chat; sending it applies the update. **Open AGENTS.md with the update** puts the new rules,
with your Persona kept, in the AGENTS.md editor for you to Save.

If interrupted starter setup cannot resume because its files or setup details changed, choose
**Cancel** and start a new setup.

**Other vaults** lists every vault you have registered besides the active one. It also lists
**Chat only**, unless that is already active. Each row's **Use this vault** switches to it.

### The Vault view

Tabs under the search box switch between the **Front page**, **Knowledge**, **Sources**, **Maps**,
**Everything else** and **All notes**; the number beside a tab counts the listed files in that
folder, or in the whole list for **All notes**. In a window 800 pixels wide or more, the list sits
on the left and the open note on the right, and each side scrolls on its own. Until a note is open,
the right side of **Knowledge**, **Sources** and **Maps** shows the section at a glance: its notes
by status, its most linked maps, and its topics. Drag the divider to resize the two sides; **Reset**
on the divider restores the default split. In a narrower window the note replaces the list, and
**Back to notes** returns to it.

When a vault has notes in `30-knowledge/`, `40-sources/` or `50-maps/`, the **Front page** brings
together **Featured**, **Latest**, **Topics**, **This week**, **Rediscover** and **Bookmarks**.
**Latest** groups recently added or updated notes by day. **Rediscover** lists Knowledge files whose
modification time is at least 30 days old. A newer note in another folder does not appear in **Latest**.

**Featured**, **Latest**, **Topics** and **This week** cover notes read from Knowledge, Sources and
Maps; when that read is partial, their counts are lower bounds. The rail's Knowledge, Sources and
Inbox tiles count listed files, so their counts can differ from front-page note counts. **Topics**
lists maps and their linked-note counts, plus tags found in the covered notes; tag counts do not
cover the whole vault. **Rediscover** uses Knowledge file modification times. **Bookmarks** use
saved exact paths and can point to notes outside those folders. **Inbox** shows captures waiting in
`00-inbox/`; **Triage inbox** opens inbox triage.

The section navigation includes **Knowledge**, **Sources** and **Maps**. **Everything else** appears
when the list has another folder to show. A partial list may omit folders. **All notes**
browses readable notes, and **Search note contents** searches their text. Each row shows its exact
relative path, including duplicate-title and search results. A card on the **Front page** or a
section page leaves out a path that only repeats its title (a note directly in its folder under its
own file name); the reader always shows the path. A title does not change the file name or link target.

Dates, statuses and summaries come from the opening text of notes in the three covered folders. A
card can show its file-change date when no note date is available. A listing-only row says its title
comes from the file name and its details have not been read. An unsupported or truncated header
shows a message that some header details could not be read. A card can say **Details not read**
without saying why. The front page may show a
global notice that some note details could not be read. When newer note details arrive while a card has focus, the
cards keep their order until you select **Show latest**.

**Bookmarks** shows the five newest saved exact paths. A moved or renamed note can appear missing,
and the bookmark does not follow it. **Related** shows resolved or ambiguous links and backlinks
with exact paths; missing links remain text and are not matched to another note.

### Edit notes

In an ordinary vault you can create, edit, save, rename, move, archive and delete notes.
**New note** and **Create note** make a new one; a note's actions cover rename, move, archive and
delete.
The note text and the path for a new note are read-only while the app creates, saves or proposes
the note.

- **Creating:** review the path and content before creating.
- **Saving:** if the note changed elsewhere first, saving stops and your draft stays.
- **Archive:** moves a note under `archive/`. Use Move to put it back.
- **Delete:** creates a recovery item when capacity permits. You can restore that deleted note for
  30 days while the item is available.

Off Windows, this works on APFS (macOS) and on ext4, XFS, Btrfs or tmpfs (Linux). On another
drive, such as a network drive, Save says the drive does not support saving notes, and the vault
stays read-only.

In a protected vault, ask Claude Code to make a change and review its permission prompt or
proposal.

### Code folders

Add a code folder, and manage the ones you have, from **Settings → Workspaces and Vault**.

- **Add folders…:** opens your system's folder picker. There, `Ctrl` (⌘ on macOS) or `Shift`
  selects several at once.
- **Type a path instead:** types one in by hand.
- **Browse…:** opens the same picker beside a folder field, such as a new vault's parent folder or
  an export, backup or restore destination.

Each folder shows **Trusted** or **Not trusted**. Ticking a trusted folder lets each new chat from
**New session** read and edit it too, up to 7 at a time, with no restart. A chat already open
keeps the folders it started with.

Chats started from the Code workbench, Bot conversations and routine runs don't get ticked
folders. A chat with extra folders names them in a chip on its header. A Bot can instead work in a
copy of one folder you give it (see [Code copies for a Bot](#code-copies-for-a-bot)).

**Whose instructions a chat reads.** Claude Code reads the instruction files of the folder a chat
starts in: its `CLAUDE.md`, or its `AGENTS.md` when it has no `CLAUDE.md` (Claude Code 2.1.277 or
later).

- **A chat from the Code workbench**, and a **Look on my behalf** chat, starts in the code folder or
  its worktree. It reads that folder's instructions, not your vault's.
- **A chat from New session**, or from the **Message VIKI** box in VIKI's launcher, starts in
  your vault and reads the vault's instructions. The folders you ticked join it, but Claude Code
  loads no `AGENTS.md` from an added folder, and its `CLAUDE.md` only when your own Claude Code
  setting turns that on. With no vault set, chats start in VIKI Agent's own folder instead.
- **A Bot**, in its chats, task threads, Bot-thread turns and routine runs, starts in your vault
  but does not read the vault's instructions: VIKI Agent asks Claude Code to skip them. It gets
  its profile's instructions, VIKI Agent's memory rule and its Bot rules instead
  ([What a Bot reads](bots.md#what-a-bot-reads)). For a Bot working in a code copy, its Bot rules
  tell it to read the copy's `CLAUDE.md` and `AGENTS.md` before it reads or changes files there.
- **A vault made with Start with VIKI** tells your own chats, in its `AGENTS.md`, to read a
  folder's `CLAUDE.md` and `AGENTS.md` before working there. In another vault, add that rule to
  your instructions, or ask Claude to read them.

Each row's menu offers:

- **Details and trust**
- **Open in Code**
- **Remove from list…**, except on a protected folder or the folder chats start in. It asks you to
  confirm **Remove** and leaves nothing on disk changed.

### The Code workbench

Open the full **Code** view from the right sidebar's **Code → Open workbench** control for command
tools.

Trusted folders use worktrees and offer review, integration, push, checks and terminal controls
on Windows, macOS and Linux. Previews run on Windows. Checks use cmd.exe on Windows and /bin/sh
on macOS and Linux; write commands for the computer where they run. On Linux, opening a terminal
needs `xdg-terminal-exec` or `x-terminal-emulator`.

> [!WARNING]
> Trust a folder only if you are willing to run its project commands with your account's rights.
> Checks and previews are not sandboxes.

Review the branch, changed files and command before using these controls. The app does not require
checks to pass before integration or push.

In the full **Code** workbench, the folder tree stays available while files are open: above the
file on narrow windows, and beside it on desktop.

- **Tabs:** open files appear in tabs labeled with their full paths. Select a tab to switch files,
  or use its × button to close it.
- **Unsaved edits:** switching keeps them available. A tab with unsaved edits stays open until you
  Save or Revert.

### Worktree cleanup

VIKI cleans up the worktrees it made in the `viki-agent-worktrees` folder beside VIKI Agent's data folder. A sweep runs at launch, after recovery,
and every 6 hours.

- **Removed:** a clean worktree, idle 24 hours since its last change (72 hours for one you named)
  and with every commit on another branch, tag or remote.
- **Archived, then removed:** one with uncommitted changes or commits nowhere else.
- **The worktree's branch:** deleted unless it is on a remote or checked out elsewhere.
- **Ignored files:** such as build output, `node_modules` and `.venv`, are not archived. They go
  with the tree.

**Restore** an archived worktree from the **Worktrees** tab for 30 days. **Preview cleanup** there
shows what the next sweep would do for each worktree and why, without changing anything.

A chat whose worktree was archived says so when you send to it, and points to the **Worktrees**
tab. **Settings › General › Clean up idle worktrees** turns this off. A worktree folder VIKI Agent
could not delete after removing the worktree is deleted at the start of the next sweep, also with
**Clean up idle worktrees** off.

A copy of a code folder that a Bot chat or Bot thread still uses is kept, and the **Worktrees** tab
labels it **In use by a Bot chat**. A copy whose chat, thread or Bot was deleted is removed when
its work is already merged, otherwise archived with its branch kept, even with cleanup off.
Archived changes list under **Archived changes** with **Restore**, and a restored copy is no longer
labeled as a Bot's.

Cleanup leaves such a released copy in place, marked **Released**, when it cannot handle it safely:
the repository has a submodule, the copy's branch was switched or its HEAD detached, a merge,
rebase, cherry-pick or revert is in progress, a bisect is in progress or it holds per-worktree or
rewritten refs, it contains another repository, it has files hidden
from Git status (assume-unchanged or skip-worktree), it has large untracked files (over 50 MB in
one file or 200 MB in total), or a change it holds is under a Git filter other than Git LFS's,
which VIKI Agent's own Git does not run in a Bot copy. **Preview cleanup** shows the reason. The copy stays on disk and
counts toward the limit of at most 32 worktrees until you remove it. **Remove** in the
**Worktrees** tab works when Git status shows no changes in the copy, for a switched branch or an
uninitialised submodule, for example. With changes, conflicts, untracked files or an initialised
submodule the app refuses: clean the copy up in a terminal first (finish or abort the operation,
commit or delete the files), or run `git worktree remove --force <copy path>` in the code folder,
then select **Remove** to clear the row. Removing a copy discards whatever is only in it,
including changes hidden from Git status.

### Code preview from a chat

To inspect code from a chat:

1. Select **Code** in the right sidebar's **Overview / Code** tabs.
2. Choose a registered **Code folder**.
3. Open a file from **Files**, or a change from **Changes**.

The read-only preview opens in a pane beside the chat when there is room, else in the middle pane.
Changes have **File** and **Diff** views. One file can be open in two panes, with one editor at a
time.

- **Your chat and draft:** are kept in place. **Close code preview** or **Back to chat** returns
  to them.
- **A file opened in a pane beside the chat:** closes with **Close file**, or its tab's ×,
  instead.
- **On a narrow window:** open **VIKI panel**. Selecting a file closes the drawer and leaves the
  preview open.

Browsing does not change the active workspace.

Python, JavaScript and TypeScript (including JSX and TSX), and JSON use syntax colors in the
preview and editor. Other file types and large or costly previews can stay plain text.

### Edit code

Use the pencil **Edit** button to edit a supported text file in a trusted, ordinary code folder.
If **Edit** is unavailable, check the file or folder's message.

- **Save** and **Revert** work in the middle pane.
- A save conflict keeps your edit for comparison.
- While VIKI Agent is busy in that folder, for example cleaning up worktrees, a save waits up to
  10 seconds, then says saving is busy and keeps your edit.
- Save or revert dirty changes before choosing the editor's **Close** button to return to the file
  preview.

Off Windows, Save and these controls need APFS on macOS, or ext4, XFS, Btrfs or tmpfs on Linux. On
another drive, such as a network drive, Save says the drive does not support saving.

#### Unsaved code edits

Unsaved code edits stay in the current window when you browse other files, return to chat or
navigate elsewhere.

- **Finding them:** the **Code folder** menu marks folders with `(unsaved)`. The
  **Unsaved changes** list shows their file references.
- **Resuming:** reopen a file, then choose **Edit**.
- **A retained file that cannot be opened here:** use **Open workbench** to recover its edit.
- **A save that needs recovery:** points to **Stopped saves**.
- **While a save is pending or its result is uncertain:** edits and undo history remain available.
  Check the save state before retrying.
  If you reopen a file during a save, changing it back to its earlier text after that save finishes
  counts as an unsaved edit.

> [!WARNING]
> Save your edits before closing or reloading the window.

### Code quotes and vault excerpts

Select lines and use **Quote in chat** to append their exact file reference to the draft.

- Only the active code folder can supply a quote.
- Quoting does not send the message.
- The sidebar browses registered folders. It does not automatically follow a chat's session
  worktree. Register a folder in **Settings → Workspaces and Vault** if it is not listed.

A vault excerpt in the message box shows its exact note reference, line range, character count and
destination.
You can add or remove excerpts while a refresh is running; the refresh keeps your latest list.

- **Details:** expand it for the excerpt's modified time and full content hash.
- **A changed or unreadable note:** needs attention before sending. Its warning remains beside the
  excerpt when Details is closed.
- **The total below the excerpts:** shows the app-added content against the 12,000-character
  limit.

## Bots

**Bots** lists your team first: Chief, Ops, Research, Writer, Engineer, Desk and Checker.

- **A Start with VIKI vault:** already has the seven Bots.
- **Another persistent vault:** **Open conversation** on a seat makes its profile and Bot if
  missing, and opens its chat without sending anything. **Add missing Bots** under **Core team**
  makes every missing seat's profile and Bot in one step and sends nothing.
- **Any other profile:** create it in **Bots → Bot settings**, then choose **Team → New Bot**. This
  gives it a dedicated conversation and access to its routines.

**Open conversation** returns to that Bot's chat; routine runs have separate chats.

A new Bot chat or task thread opens with the Bot's icon above the wordmark. Its header shows the
Bot's name, its seat's role, **Main Bot chat** or **Task thread** with the revision the chat was
prepared with, and **Chats & routines**, which returns to that Bot. The right panel shows
**Needs you** first, then **Bot activity**: **Refresh activity**, **This Bot's routines** with
**Run now**, and the latest run with **Open latest run chat**; **Show all** opens the Bot, and
**Context & loadout** stays in the panel's footer. The **Bots** strip rings the Bot whose chat or
task thread is open, and **All Bots** opens the roster on **Team**.

Each row says what its Bot is doing. The right panel's **Bots** section lists them too, and shows
the same status when you point at one. See [Bots](bots.md) for profile revisions, context limits,
routine controls and removal.

### Regular chat or Chief

- **A regular chat:** use it for discussion or work you want to guide directly.
- **Chief's Open conversation:** use it when you want a request routed to a specialist. With
  Kanban pack, Chief creates a card with an owner and a Done when checklist. Without it, a new seat
  profile prepares a task brief in chat.

Opening Chief sends nothing, and its message box starts empty: Chief's instructions go to Claude Code as part of its system prompt. Type your request and send. See
[Choose Chat, Chief or a specialist](bots.md#choose-chat-chief-or-a-specialist).

### Bot threads

Open **Bot threads** in the left sidebar to choose participants and follow a shared conversation.
Bots hand work to one another until Checker reports PASS with evidence or the thread needs you.

- The thread has its own participant panel and Stop control.
- Saved groups reopen paused.
- Unsent thread text clears on refresh.

See [Shared Bot threads](bots.md#shared-bot-threads) for its limits.

### Code copies for a Bot

In **Bots → Bot settings**, **Code folder** gives a Bot one trusted code folder; the default is
**None**. Its main chat and each task thread then work in their own copy of that folder, made
when you send the chat's first message, and a Bot thread's Bots share one copy. Routine runs get
none.

- **Before the first send:** the chat's review says **Gets a copy of** the folder **when you send**.
  Afterwards it says **Works in a copy of** the folder with its branch, and the chat's folders chip
  reads **Code copy of** the folder with the branch.
- **In the Code view:** the **Worktrees** tab labels each copy **Bot copy** with its Bot and chat,
  or **Bot thread copy**, and **Released** once its owner is gone.
- **If the folder's Git settings change:** once a folder has had a Bot copy, any change to its Git
  settings, included files, worktree settings, hooks or attributes makes VIKI Agent's own Git
  actions on the folder and its worktrees refuse until you undo the change or select **Confirm the
  current settings** in the **Worktrees** tab. This is expected after ordinary commands, whether you
  or Claude Code in the copy runs them: publishing a branch with upstream tracking (`git push -u`,
  an editor's publish action), switching to a branch that sets upstream tracking, adding or changing
  a remote, or `git config` for your name or e-mail. The Code view's Git actions and a new Bot chat
  refuse, the status bar's Git chip shows an unknown status with no branch, commit or change count
  while that folder is the active workspace, the **Worktrees** tab shows the confirm control as soon
  as it opens, and **Code folder** in Bot settings shows a sentence that names the same way out when
  saving a code folder is refused. The check continues after the folder's last copy is gone, until
  you remove the folder from the list (**Remove from list…** in Settings). Confirming means you have looked at the
  current settings and accept them as the new baseline.

See [Code copies for a Bot](bots.md#code-copies-for-a-bot) for the waits, the 32-worktree limit and
what happens when a chat is deleted, and [Security](security.md#bot-code-copies) for what a Bot can
do in a copy.

### Kanban

The sidebar's **Kanban** is a Kanban view of notes directly in `kanban/`. Select a card to open its
note. Edit the note or ask a Bot to change it.

Automatic pickup requires a separately tested and enabled routine and the appropriate start
settings. Creating a card alone starts no specialist. See
[From a card to automatic work](bots.md#from-a-card-to-automatic-work).

## Routines, skills and context

This section covers routines, subagents, skills and memory, the status bar and the context report.

### Routines

In **Routines**:

1. Create a prompt.
2. Choose its workspace and profile. New routines start with the active vault when it is
   available, or the first listed vault. Review the workspace before saving.
3. Test it.
4. Once tested, enable its schedule.

Routines run while VIKI Agent is open.

- **Missed times:** a due time missed while VIKI Agent is closed can appear as **Missed** at the
  next launch. It does not trigger a catch-up run.
- **Waiting confirmations:** a confirmation waiting when the last window closes is denied.
- **Uncertain outcomes:** inspect a routine's run history before choosing to run it again.

Run options:

- **Only when a card is waiting:** a daily or weekly routine can be set to run only when a card is
  waiting for its seat on the vault's Kanban. Its run history reads **Skipped** for a time with no
  card waiting.
- **Stop at a draft:** ends a run with a draft instead of waiting for your OK. Its run history
  reads **Stopped for your decision** for a run that ended at a draft.

See [Create and run routines](bots.md#create-and-run-routines) and
[A team using Kanban](bots.md#a-team-using-kanban).

### Subagents and running work

**Capabilities → Subagents** and **Skills** show the available native helpers. Running subagents
appear in the activity panel.

Under **Capabilities**, choose **Subagents** for **Running work**. It lists chats open in VIKI,
including idle ones, with **Open**, **Stop** and **Close** controls. Work shown as running
elsewhere is read-only.

When Claude Code reports that a stopped agent is running again:

- its run returns to **Working** in the right panel
- it appears as running under **VIKI and subagents** (your **Assistant name** in place of VIKI, if
  you changed it)
- its next completion is available for review
- **Stop** becomes available again for that run

### Skills and memory

A vault `SKILL.md` note is just a note; it installs nothing on its own.

- **Make this a skill:** drafts a request in chat.
- **Propose this skill:** opens its exact text for review in Approvals. Approving it creates a
  chat draft asking VIKI to apply the skill, rather than writing it silently.

A memory takes effect only once you approve its exact content. A note that says
`status: approved` is not enough.

The right-rail footer previews up to three returned native Skills.

- **Browse Skills:** opens a searchable list with each Skill's exact ref and source, plus the
  catalog's coverage.
- **Recent Skill calls · loaded chat:** below it, shows up to three recent observed calls for the
  selected chat, separately from the catalog.

Calls show **Calling**, **Invoked**, **Failed** or **Result unknown**. A slash expansion may not
produce a recorded Skill tool call. These call rows do not identify a native source or ref.

### Status bar

The status bar starts with the Git chip: the branch and state of the vault, or of the code folder
the open chat runs in.

- **Clicking the chip:** shows the repository, branch, HEAD and last commit. For a code folder, it
  lists up to 10 changed files, each opening in the middle pane.
- **After the chip:** a plan-limit meter and short notices follow, including a warning when Claude
  Code is signed out or Git is missing.

App readiness and the Claude Code version are in **Settings › Diagnostics**.

### Context window and loadout

The right panel's footer keeps **Context window** visible below its scrolling sections. It shows
the selected chat's last reported percentage, or **Not reported** until a measurement arrives.
**Context & loadout** opens the full report.

The right panel is hidden in a window narrower than 1280 px with the VIKI panel closed, or on a
page without the panel. Then the status bar shows the note counts and a context meter with a
breakdown instead.

**Context & loadout** includes skills and memory when the CLI reports them.

- **Breakdown:** each row's small bar shows its share of the reported window.
- **Hatched space:** is reserved for auto-compact.
- **Deferred tools and free space:** keep their own notes rather than appearing as used context.

The report updates after a reply ends, so it may lag during a running turn or after a process
restart. A plan-limit meter likewise uses the latest reported reading, when present.

## Decision

This section covers VIKI's launcher and panel, and the **Decision** settings for proactive work.

### VIKI's launcher and panel

VIKI's launcher, the circle that floats over the window, shows what is going on without a sound:

- **A brighten:** new information.
- **A number:** waiting items.
- **A pulsing ring:** something it wants to flag.
- **A turning arc:** while it is running a chat for you.

Select the launcher to open VIKI's panel; it still opens, dimmed, even while Decision is off or
paused.

- `Alt`+`V` opens or closes the panel from anywhere in the window while no other dialog is open.
- `Alt`+`T` starts or ends a voice conversation with VIKI, opening the panel first if it is closed.
- Drag the panel's outer edges to resize it. The size is kept after you close it and after a
  restart.

#### Panel menu and items

The panel's **More** (⋯) menu offers:

- **Pause for 1 hour**
- **Pause until tomorrow** (**Resume** once paused)
- **Focus for 1 hour** (**End focus** once on)
- **Open Decision**. While Decision is off, **Turn on in Decision** opens it instead.

Each item carries a kind chip, what it is about, a **Why now** line, evidence chips linking back to
the note or chat behind it, and its own actions.

- **Not now:** sets an item aside.
- **Useful:** records that it was right, and closes the item like **Not useful** does. If the item
  still offers a way to start its work, it stays open instead, and its button reads
  **Marked useful**.
- **Not useful:** marks it wrong.
- **Stop these:** in an item's own **More** (⋯) menu, mutes that kind of item until you unmute it,
  with a reason if you give one. Unmute it again from **How VIKI may reach you**.

#### Message VIKI

At the panel's foot sits VIKI's own chat, a **Message VIKI** box kept separate from your regular
chats.

- **New conversation:** clears it.
- **Open in main window:** continues the same chat there.
- **With dictation available:** it also offers the microphone and the round **Hands-free** button.
  Opening the panel ends a voice conversation running in your main chat.

### Decision settings

Open **Decision** in the left rail for the full settings. Decision checks for proactive work only
while all of these hold:

- **Proactive VIKI** is enabled. The **Proactive VIKI** card at the top of the page says VIKI
  checks nothing until you turn it on; **How VIKI works** under it says what VIKI watches and
  what would leave this computer.
- Proactivity is unpaused.
- An app window is open.

Once it's on, the card also holds **Pause for 1 hour**, **Pause until tomorrow** and
**Focus for 1 hour**. Pause stops checks and starts. Focus holds nudges and alerts.
**How VIKI works** under the card explains the page in steps, and each step opens its settings.

More settings:

- **Decision model:** saves a TypeSafe key. Without a working key, or once it is capped for the day
  or cannot be reached, VIKI falls back to its own rules, which list items without badges, nudges or
  alerts. **Spending** holds the **Daily cap**. **Advanced: version and comparison** picks
  **Version** (**Pinned**, **Latest stable** or **Preview**) and turns on
  **Compare with another model version**.
- **What leaves this computer:** sets how much of an item's text may be sent, and the folders or
  tags it should never send.
- **What VIKI watches:** starts with **How often VIKI checks** and chooses what Decision considers.
- **How VIKI may reach you:** sets nudges, alerts, quiet hours and the topics you stopped.

Within **What VIKI watches**:

- **Kanban:** lists Kanban cards that need you and the cards that stopped moving.
- **Vault health** and **Messaging** list stopped saves, deleted notes about to expire, memories
  whose cited notes changed, and messaging bridges that stopped.

### What VIKI may do

**What VIKI may do** grants preparing drafts or starting chats to do work. Each is **Off**,
**Ask first** or **Allowed**, under one legend. Each kind of work has its own line with its daily
limit, in **Prepared for your review** or **Work that can make changes**, and the longer
explanation sits under **More about this**.

- **Default daily limits:** five prepared drafts and three work chats, counted across workspaces.
- **Chats at once:** at most two chats VIKI starts run at once.
- **Pause automatic starts at Claude plan usage:** 80% by default. It holds the chats VIKI would
  start on its own while your plan use is that high or your plan limit is reached.
- **Vault policy:** in a protected vault VIKI starts only plans, and in a read-only vault none.
- **Started by VIKI:** a chat VIKI starts shows this in your session list. It can be stopped from
  here.

The other settings there let VIKI start or offer more kinds of work:

- **Start Bots' work:** lets VIKI start a Bot's tested routine set to
  **Only when a card is waiting** when one of your `todo` cards waits for its seat. **Ask first**
  by default. Capped by **Daily limit: Bot runs VIKI starts** (4 by default).
- **Look on my behalf:** lets VIKI start a chat in plan mode in a trusted code folder whose
  uncommitted changes have not changed for a day or more. Its aim is to look for what is half-done
  or risky. **Allowed** by default. Capped by **Daily limit: looks VIKI starts** (3 by default).
- **Take a second look at my work:** lets VIKI start a chat in plan mode after a long task or work
  you corrected in the chat you have open. Its aim is to look for one flaw, better approach or
  missing piece in the notes the reply edited. **Allowed** by default. Capped by
  **Daily limit: second looks VIKI starts** (3 by default).
- **Plan next steps for my projects:** lets VIKI start a chat in plan mode for an active project
  none of whose notes changed for two weeks, to propose three next actions. It also lets VIKI start
  one for a project's tasks three days or more overdue, to propose ticking the done ones and new
  dates for the rest. **Allowed** by default. Capped by
  **Daily limit: project plans VIKI starts** (3 by default).
- **Prepare drafts:** also offers message drafts for the chat you have open in a vault. These are
  a handoff when its context is 70% full or more, and literature notes for the web pages your last
  reply fetched. Each is put in that chat's message box only when you select it. Capped by
  **Daily limit: message drafts** (3 by default).
- **Run my saved checks:** lets VIKI run the saved checks you have run in a trusted
  code folder when that folder moves to a new commit they have not run on. VIKI then reports
  whether they passed. **Ask first** by default. Capped by
  **Daily limit: new commits VIKI checks** (3 by default).
- **Suggest destinations for my inbox:** lets VIKI run the inbox's read-only suggestion run when
  your inbox piles up, so suggestions wait in **Triage inbox**. **Allowed** by default. Capped by
  **Daily limit: inbox suggestion items** (3 by default).

Apart from the notice of a Bot whose delivery you turned on, VIKI does not message other chats or
people or send to your phone. [What VIKI may do](decision-engine.md#what-viki-may-do) in the
Decision guide lists every grant with its default, daily limit and vault policy.

### Review, learning and data

- **How VIKI is doing:** shows the last 30 days and the weekly check of what it held back, with
  feedback buttons.
- **Learning:** tunes a few numbers to your answers without moving your quiet hours, alerts or
  other limits. It can reset back to its starting numbers.
- **Data:** shows what VIKI keeps, with charts, and deletes chosen decisions, suggestions and model
  calls or all decision history.
- **Context & loadout**'s **Decision** tab lists your last 20 calls to the configured model, with
  the exact state sent, its answers, and its cost and timing.

> [!IMPORTANT]
> Once **Compare with another model version** (under **Decision model** › **Advanced: version and
> comparison**) names a version, each new decision is also asked of that version, for comparison
> only. Its answers never change what VIKI decides, but each is a second
> paid call within the same daily cap and per-minute limit.

### Self-improvement

With **Self-improvement** on (one of **What VIKI watches**), Decision may also notice work worth
doing on itself:

- a skill worth drafting from a chat
- one of your own skills that fits the chat you're in
- a captured source worth a literature note
- a retro worth running once enough items were marked Not useful
- one of its own questions worth rewording
- three kinds of vault upkeep: linking two notes, marking an older note as replaced by a newer
  one, and archiving a project's ticked tasks

#### Suggestions that fill a message box

The first five each show like any other item. Their one action is a ready-to-send draft that only
fills a message box: in that chat for a skill, a new one otherwise. They send nothing until you
press Send yourself.

A skill that fits has one action, Use this skill, which puts its `/` command there instead. It is
offered at most once a chat, and never for one already used there. **Stop these** on it mutes only
**Skill suggestions**.

#### Vault upkeep proposals

The three vault-upkeep kinds skip that step and land straight in **Approvals** instead, as a
**Link proposal** or **Upkeep proposal** row.

- Only once you select **Approve** there does VIKI make a draft of that one edit.
- Nothing in the vault changes until you send it.
- The three kinds come only from a vault whose policy is **Ordinary**.
- **Daily limit: vault changes**, beside the drafts and chats limits in **What VIKI may do**, caps
  the three kinds together: 0 to 10 a day, 3 by default, and 0 turns vault-change proposals off.

#### New wording for Decision's questions

A reply that offers new wording for one of Decision's own questions shows **Propose this wording**
underneath it. Choosing it tries the new wording alongside your usual decisions, in the shadow,
until you act on it.

In **Approvals**, it shows both wordings and how the trial compared. From there:

- **Approve** it. Approving is what makes new decisions ask the new wording.
- **Reject** it.
- **Undo** an approved one.

## Messaging

Open **Messaging** from the sidebar for separate Mattermost, Slack and Discord cards, with their
official logos beside the provider names. **Open Capabilities** leads to skills, tools, connectors
and observed plugins; it does not install plugins.

### Save provider settings

An open card's source pill shows **Needs setup**, **Saved settings** or **Settings unreadable**.
Fill in its required fields and select **Save changes**.

- **Secret fields:** a saved secret field stays empty and is never shown again. Leaving it empty on
  a later save keeps the token already saved for the same server.
- **Mattermost Server URL:** changing it needs the bot token again.
- **Remove saved settings:** deletes a provider's settings from this computer.

Saving or removing a provider's settings turns its bot off.

### Turn a bridge on or off

Each card also shows **Not configured**, **Disabled**, **Connecting**, **Connected** or
**Stopped**. Cards start closed: select a provider's name, or **Set up** when nothing is saved, to
open its settings.

- **Turning on:** turn a provider on with the switch beside its status when you want to connect
  it.
- **Turning off:** stops that bridge and closes its dedicated conversation. You can turn it on
  again in the same launch.
- **After a stop:** turn it on again once cleanup and any cooldown allow it.
- **Next launch:** a bot you leave on reconnects on its own the next time VIKI Agent starts in the
  workspace you turned it on in. In any other workspace, it stays off until you turn it on there.
- **A restart-required message:** needs a new launch.
- **Refresh status:** reads state without connecting.

One provider's controls do not stop the others or a desktop chat.

### Bridge owner and limits

Each bridge serves one configured human owner in a private DM, and stays with the startup
workspace.

- Keep the desktop host awake and VIKI Agent running.
- Review retention before sending content.
- Slack and Discord accept only plain new top-level text, with no files or thread replies.
- A new connection does not replay offline messages.
- Named Bot routine notices are Mattermost-only.

See [Mattermost mobile access](mattermost.md) and [Slack and Discord messaging](messaging.md) for
setup, commands, privacy and limits.

## Save and recover data

This section covers where your data lives, exports and imports, vault backups and recovery mode.

### Data locations and exports

From the app menu, open **Data locations** to find app storage and Claude Code's conversation
location.

- **Export diagnostics** and **Export settings** show a preview before writing a JSON file.
- In that preview, **Identity** includes personal settings such as Bot icon and color choices.
  Leave it unchecked to omit those values. Model connections, their keys and a default model set to one of those
  connections are excluded from settings and diagnostics exports.
- **Import settings** previews validated setting changes. If settings change after an import
  preview, make a new preview and select the values again before applying them.

These exports are not a conversation backup and do not include Bots, profiles or routines. A
chat's own tools offer **Export Markdown** or a JSON archive with attachments; an imported archive
opens read-only.

### Back up and restore a vault

The Data view can back up a registered vault's notes to a folder outside registered vaults, the
app data folder, `~/.claude` and `~/.codex`. Review its count and skipped files before running
it.

> [!WARNING]
> The backup is unencrypted.

**Restore into a new folder** verifies the backup and registers the newly created vault read-only.
It does not replace the source folder.

See [Sessions and data](sessions-and-data.md) for what each backup or export contains. See
[Troubleshooting](troubleshooting.md) before switching builds or using Reset.

### Readiness and recovery mode

If app readiness cannot be read, the window explains the failure and offers
**Retry app readiness**.

A launch whose app store could not open shows a recovery window, with the reason and
**Diagnostics**. The window is headed **VIKI is running in recovery mode.**

The recovery window's **Before you retry** steps explain how to preserve the complete app-data
folder, including SQLite journals and `state.json`, if present. See
[Troubleshooting](troubleshooting.md#when-the-app-cannot-open-normally) before changing the data.
