# Settings

Change how VIKI looks, sounds and behaves, and review what each workspace can do.

## Opening Settings

Select **Settings** from the gear (**App menu**) in the top bar, or press `Ctrl`+`,` (`⌘`+`,` on macOS).

## Settings pages

Settings opens on these pages, in this order:

- **General** — names and avatars, code display and setup.
- **Appearance and Accessibility** — theme, display size, spacing and motion.
- **Chat** — defaults for new chats, voice input and reading replies aloud.
- **Providers** — Claude Code setup, Magpie routing and chat models.
- **Workspaces and Vault** — your notes folder and the code folders chats can work in.
- **Instructions and Persona** — shows this vault's `AGENTS.md` and `CLAUDE.md` in full, with their paths. In an Ordinary vault you can edit either file here: **Save** replaces the file, and **Discard changes** goes back to the saved text. Both files steer every chat in the vault but Bot chats, which skip them; changes apply to chats started or reopened after the save. If the file changed on disk after you opened it, saving stops, your edit stays and the version on disk is shown: select **Replace the version on disk** to save your edit over it, or **Discard changes** to use it. Text-direction marks, zero-width characters and other characters that draw nothing are written as their code, such as `<U+202E>`, with a warning, and the editor lists the lines that hold them; the file itself keeps them. A character the file can't hold, a control character or a broken one, stops **Save** and is selected in the text. A protected vault, or a file with control characters or line endings the editor can't keep (a lone CR, or a mix of kinds), is shown read-only. A missing file can't be edited here. The Persona form drafts a change to the Persona section in a new chat for Claude Code.
- **Privacy and Storage** — instruction previews and folder paths in diagnostics.
- **Diagnostics and About** — the app's version, where your data goes, and a diagnostics report.

## Finding a setting

Use **Search settings** to search labels and descriptions. Results also find pages such as
**Providers**, **Workspaces and Vault** and **Instructions and Persona**. Select **Open this page** to reach
the matching controls.

## Saving changes

Changes on **General**, **Chat** and **Privacy and Storage** save automatically. Text fields
save when you leave the field or press Enter. On **Appearance and Accessibility**, changes
preview immediately; select **Apply** to keep them or **Cancel** to return to the saved values.

## Proactive suggestions and automation

Open **Decision** in the left rail to choose what VIKI watches, how it reaches you and what
work it can start. **Learning** explains how your feedback affects suggestions. Scheduled work
is in **Routines**, and chat bridges are in **Messaging**.

## Defaults for new sessions

On the **Chat** page, **Default mode**, **Default effort** and **Default model** set what a new session starts with. They never change a session that is already open.

**Sessions running at once**: how many sessions can run Claude Code at the same time, 30 by default, any whole number from 1 to 999. Chats, routines and Bots all count. It applies to the next chat you start, with no restart. A lower number stops nothing that is running; new sessions can start once enough have closed. Each running session is its own Claude Code process and uses memory.

## Connecting your own model

On **Providers**, **Chat models** offers your **Claude Code sign-in** and models or routing
groups from Magpie. Magpie manages provider API keys and routing; VIKI chooses the
model or group for each chat. Claude Code handles the chat's tools and permissions for either
route. You can save up to eight models or direct connections in VIKI.

## MCP servers

On **Providers**, **Where to put .mcp.json** names the folder where new chats start, with
**Copy folder path**. Claude Code reads the `.mcp.json` at that folder's top level. A chat in a code
folder starts there, or in its worktree, and reads the `.mcp.json` there. Code folders ticked for
new chats and a Bot's code copy add their files, skills, commands, agents and plugin choices, but
Claude Code does not read their `.mcp.json`. In chat-only mode, chats start in VIKI Agent's own
folder: choose **All my folders** in the setup commands, or set a vault in
**Workspaces and Vault**. A running chat keeps the servers it started with, so start a new chat after you change
`.mcp.json`. The switches in a chat's **MCP servers** dialog are saved by Claude Code in its own
`~/.claude.json`; VIKI Agent writes nothing there. Servers named `viki_…` come from an agent
profile's MCP configuration and reach only that profile's chats in an Ordinary workspace.

Use Magpie for cloud APIs and local model servers. To connect to an endpoint without Magpie,
open **Advanced — Direct connections**. The setup steps for both are below.

With a custom model such as Qwen, that model supplies the replies and tool requests.
Claude Code remains installed and manages the conversation, tool execution and permission
prompts. The serving endpoint must support streaming replies and tool calling.
Add the endpoint in Magpie, add its model to VIKI and select it for a new chat. Requests pass
through VIKI's local gateway and Magpie to the model. A direct connection uses Claude Code
and VIKI's gateway and skips Magpie; Magpie's routing groups and failover do not apply.
Anthropic does not support using Claude Code with non-Claude models.

Claude Code 2.1.281 or later must be installed. Use **Check Claude Code** on **Providers** if
its version is unknown. Resolve any provider or authentication conflict reported there before
starting a chat.

Adding a model does not change your default. Select **Use for new chats** beside a saved model,
or choose it under **Default model** on **Chat**. New chats use that default, including chats
started by automation.
Before starting a chat, its model menu also offers saved connections for that chat alone.
Inbox suggestions and Agents › Profiles chats use Claude Code's native models. In
**Bots → Bot settings**, a Bot can pin a saved connection for its chats, task threads and
Bot-thread turns; its routine runs use the native default. The endpoint can receive the prompts, attachments, earlier
replies, Bot memory, project instructions, skills, tool results and files a chat reads. Changing
the default does not retarget saved chats or a Bot that pins a connection. When an existing terminal conversation has no saved VIKI model choice,
select a model explicitly before sending it through a connection. A running chat keeps its
connection; close and reopen it or start a new chat before choosing a different one.
**Use Claude Code sign-in for new chats** returns new chats to Claude Code's own sign-in.

If a model-review notice appears, your draft stays in the editor. Select **Reopen chat**,
review the model, then select **Send**. Reopening does not send the draft.

**Remove connection** removes its saved key and settings. If it was the default, new chats
return to Claude Code's own sign-in. Its conversation history remains,
but a chat using that connection cannot continue after its process closes. A failed or removed
connection does not fall back to a different provider. Choose a model with tool-calling support;
image input and reasoning controls also depend on the endpoint and model.
A failed or interrupted connection blocks further requests until you close and reopen the chat.
If a prior subscription billing error is shown, you can open a new session and choose a custom
connection; provider or authentication settings conflicts must be repaired first.

## Other Claude Code sign-in methods

By default, chats, routines and Bots that use Claude Code's own models run on the sign-in your
Claude Code is set up with, such as your Claude plan, an API key, an Anthropic Console login or
a cloud provider, and that account is billed. To start chats only on your Claude subscription
sign-in, turn off **Let Claude Code use its other sign-in methods** on **Settings › Chat**; it
takes effect on the next launch, and a chat that would bill another account then does not
start. A chat on a model connection still does not start while Claude Code's
settings would bill another account; remove that setting, and the change takes effect after
VIKI Agent restarts. Set your sign-in in Claude Code's own settings: VIKI Agent does not
pass `ANTHROPIC_*` or `CLAUDE_CODE_*` variables from your shell, such as `ANTHROPIC_API_KEY`,
to Claude Code.

## Exporting personal settings

In the **Data** export preview, select **Identity** to include personal setting values such as
Bot icon and color choices. Leave it unchecked to omit those values. The export still omits saved
model connections, their keys and a default model set to one of those connections.

## Magpie routing and providers

In **Chat models**, select **Open Magpie** to manage providers, routing groups and
usage. In VIKI Agent's own window, Magpie opens in the main content area with VIKI's colors,
typography and font size. VIKI's navigation stays available; select Back to chat models
to return to **Chat models**. In a browser window, Magpie opens in your system browser.

1. Add providers and, optionally, a routing group in Magpie. Enter provider API keys there.
2. Return to **Chat models** and select **Choose a model or group**.
3. Select **List models**, choose a **Magpie model or routing group**, and **Add model to VIKI**.
4. Select **Use for new chats** to make it the default, or choose it in a new chat's model menu.

**List models** fetches model IDs without generating a reply. If you enabled gateway
authentication in Magpie, expand **Gateway access key** and enter Magpie's gateway key before
selecting **List models**.
Otherwise, leave it empty. You do not need to enter Magpie's provider API keys in VIKI.

To replace a gateway key, select **Update gateway key** beside the saved model, enter the new
key and select **Save gateway key**. Close and reopen chats using it so they pick up the new
key. VIKI encrypts the saved model choice and gateway key on this computer. Settings and
diagnostics exports omit them and their default-model reference; portable settings imports
cannot restore those references.

Magpie starts when you open it or use its saved model, and VIKI stops it when VIKI begins to
quit. On macOS and Linux, after a crash or a forced kill, a Magpie left running is stopped the
next time VIKI starts; on Windows it ends with VIKI. If Magpie stops unexpectedly, select
**Open Magpie** to restart it. Installed copies of VIKI Agent include Magpie; a copy run from
source prepares it once with `uv run python scripts/prepare_magpie.py` in the repository folder,
then you open **Settings › Providers** again.

Magpie groups can retry requests and switch models or providers according to their routing
settings. Editing a group in Magpie can change the provider used by an open VIKI chat.
Claude Code handles the chat's tool permissions; Magpie's plugins and agent-management
features have their own behavior. VIKI starts Magpie with its subscription accounts turned
off, so it doesn't read the sign-ins of Claude Code, Codex or other agents, or use subscription
plans; it can still change agent settings. Its provider keys and usage data are separate from VIKI's encrypted
connection settings and are not included in VIKI settings exports or settings reset.

Configure VIKI through its saved Magpie connection. Changing Claude Code's provider on
Magpie's Agents page can cause VIKI's provider check to refuse startup. Disconnect that
Claude Code configuration in Magpie and run **Check Claude Code** again if this happens.

## Direct connections without Magpie

In **Chat models**, open **Advanced — Direct connections** and fill in the **Add connection**
form. Enter a name, API base URL, API format, model ID and an API key if the server requires one.
Choose Chat Completions, Responses API or Anthropic Messages to match the server. Local
servers can use HTTP on this computer; remote servers require HTTPS. Include the server's
API prefix, such as `/v1`, in the base URL. Start and configure your local model server
before connecting it.

**List models** asks the endpoint for model IDs without generating a reply. **Test connection**
sends a short fixed prompt and checks for a complete streamed reply. Your provider may charge
for the test. Available model IDs appear as suggestions. Select **Save connection**, then
**Use for new chats**, or choose the connection in a new chat's model menu.

Direct connections and their API keys are encrypted on this computer. Settings and diagnostics
exports omit them and their default-model references; portable settings imports cannot restore
those references. To replace an expired key, select **Update API key** beside the connection,
enter the new key and select **Save API key**. Close and reopen chats using it so they pick up
the new key.

## Compacting a custom-model chat

`/compact` is Claude Code's built-in command for summarizing a conversation so you can
continue with less context. When Claude Code offers it in the chat's command menu, VIKI
forwards it to Claude Code. Compaction support depends on the selected model and serving API,
even when ordinary chat replies work.

Automatic compaction follows Claude Code's context management. VIKI does not discover your
custom endpoint's configured context limit, and token counting for context checks is an
estimate. A server with a smaller context limit may reject a long conversation before
automatic compaction starts. Request compaction before that limit is reached.

## Switching workspaces

On the **Workspaces and Vault** page, **Change vault…** opens your system's folder picker on a different vault, and **New vault…** creates one; switching applies at the next launch, not immediately. **Other vaults** lists every vault you have registered besides the active one, each with **Use this vault** to switch to it.

## Names and avatars

On **General**, **Names and avatars** sets your name, the assistant name and their
avatars. **Your avatar** appears beside your messages. **Assistant avatar** appears
beside replies and at the top of the right panel in ordinary chats. Bots and profiles
use their own pictures.

## Workspace details

The vault card's **Details** shows the vault named on the card, including while another vault
is selected for the next launch.

**Details** on your vault, or **Details and trust** on a workspace's menu, shows what VIKI Agent found in the folder, with a preview of its `CLAUDE.md` and `AGENTS.md` unless **Hide instruction previews** is on under **Privacy and Storage**. **Git identity** and **Git root commits found** show whether VIKI Agent could read the folder's Git history. With the instruction files' contents, that is how it recognises a copy of a protected vault, and **Inherits from** names the protected vault a copy came from. **Overlapping workspaces** lists other workspaces that sit inside or around the folder, and **Paths note saves never write** lists `CLAUDE.md`, `.claude/` and `.git/`.

For code folders, **Files the Code editor won't save** lists protected paths such as
`.git/`, `.claude/`, `.codex/`, `.viki/`, `.env` and credential or key files. Trust
controls which code folders chats can use, and which folder you can give a Bot in
**Bots → Bot settings**. Claude Code still applies its own permissions to actions in a trusted
folder, Bot code copies included.

## Diagnostics and About

This page shows the app's version and where your data goes. Open **View report details** in
the diagnostics report to inspect its individual fields.

The installed app keeps `THIRD-PARTY-NOTICES.txt` in its resources folder (`Contents/Resources` on macOS).
