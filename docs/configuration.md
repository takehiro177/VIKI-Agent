# Configuration

VIKI Agent reads supported path overrides when it starts. Set a variable before launching the
app; changes take effect on the next launch. Most people can use the defaults.

| Variable | Purpose | Default |
|---|---|---|
| `VIKI_VAULT` | Use a validated vault folder for this launch. | The workspace selected in the app, or chat-only mode if none is selected. |
| `VIKI_CLAUDE_CLI` | Use a specific Claude Code executable. | In a source checkout, the CLI bundled with the installed SDK when it is present; otherwise your own Claude Code installation. |
| `VIKI_EDGE` | Use a specific Chromium-family browser executable for the browser window. | Windows: Microsoft Edge. macOS/Linux: the first supported Edge, Chrome or Chromium executable found; Linux skips snap and Flatpak launchers. VIKI Agent's own window does not need a browser. |
| `VIKI_SERVER` | Start this VIKI Agent server executable from VIKI Agent's own window instead of the checkout's. An installed app ignores it and always runs its bundled server. | `uv run viki-agent` in the checkout. |
| `VIKI_DATA_DIR` | Choose where VIKI Agent keeps its local app data. | See the platform defaults below. |
| `LOCALAPPDATA` | Set the base used for the default app data folder on Windows. | The Windows local app data folder. |
| `XDG_DATA_HOME` | Set the base used for the default app data folder on Linux. | `~/.local/share`; relative values are ignored. |

Set `VIKI_EDGE` to an explicit executable path to override browser discovery, then restart.
Selecting an executable does not verify its compatibility.

For example, set a custom app data folder in PowerShell before starting:

```powershell
$env:VIKI_DATA_DIR = "D:\viki-agent-data"
uv run viki-agent
```

On macOS or Linux, set the variable in the shell before running `uv run viki-agent`:

```sh
export VIKI_DATA_DIR="$HOME/viki-agent-data"
uv run viki-agent
```

VIKI Agent can't start with `VIKI_DATA_DIR` inside a vault, `~/.claude` or `~/.codex`, so keep the
app data folder outside them. A relative `VIKI_DATA_DIR` is resolved from the launch directory.

## App data location

By default, VIKI Agent keeps its local app data here:

- **Windows:** `%LOCALAPPDATA%\viki-agent` (usually under your user profile).
- **macOS:** `~/Library/Application Support/viki-agent`.
- **Linux:** `$XDG_DATA_HOME/viki-agent`, or `~/.local/share/viki-agent` when `XDG_DATA_HOME` is
  unset or relative.

App data includes local application state and logs. `VIKI_DATA_DIR` moves only this folder;
Claude Code's own login and configuration stay where Claude Code keeps them.

## Optional messaging

Set up Mattermost, Slack and Discord entirely in **Messaging**. Settings apply without restart.

1. Fill in a provider's fields.
2. Select **Save changes**.
3. Turn it on.

- **Bots left on:** a bot you leave on reconnects on its own the next time VIKI Agent starts in
  the workspace you turned it on in. In any other workspace it stays off until you turn it on
  there.
- **Off:** the switch stops a running bot.
- **On:** the switch can renew a stopped connection after cleanup and any cooldown.
- **Remove saved settings:** deletes a provider's saved settings from this computer.

See [Mattermost mobile access](mattermost.md) for trusted-server setup and
[Slack and Discord messaging](messaging.md) for app scopes, the Discord client URL and platform
commands.

## Decision settings

Decision's settings are on the **Decision** page, not in launch variables. A change applies
without a restart. See [What VIKI watches](decision-engine.md#what-viki-watches) for its switches
and [What VIKI may do](decision-engine.md#what-viki-may-do) for each grant's default, daily limit
and vault policy.

## Claude Code environment

VIKI Agent uses the normal Claude Code configuration.

- **`CLAUDE_CONFIG_DIR`:** leave it unset. When it is set, Claude Code cannot start from the app.
- **Claude Code sign-in or your own model:** sign in through Claude Code the same way you do in
  the terminal, with your Claude plan, an API key or a cloud provider, or add a Magpie model or
  routing group under **Chat models** in **Settings › Providers**. Manage provider API keys in
  Magpie.
- **Custom connections:** require Claude Code 2.1.281 or later, and support
  Chat Completions, Responses API and Anthropic Messages. To configure an endpoint without Magpie,
  open **Advanced — Direct connections** in **Chat models**.
- **Conflicts:** resolve any provider or authentication conflict reported in Providers before
  starting a chat.
- **Other sign-in methods:** **Let Claude Code use its other sign-in methods** in **Settings ›
  Chat** is on by default and applies on the next launch. On, chats, routines and Bots that use
  Claude Code's own models start on whatever sign-in your Claude Code is set up with, and that
  account is billed. Off, chats start only on the Claude subscription sign-in, and a chat that
  would bill another account does not start. A chat on a model connection still refuses when
  Claude Code's settings would bill another account, so remove that setting to use connections; the change takes effect after VIKI Agent restarts.
- **Inherited variables:** VIKI Agent starts Claude Code without inherited `ANTHROPIC_*` and
  `CLAUDE_CODE_*` variables, so set an API key or provider in Claude Code's own settings.

### Sessions running at once

**Sessions running at once** in **Settings › Chat** controls how many sessions can run Claude Code
at the same time, 30 by default, any whole number from 1 to 999. Chats, routines and Bots all
count. The setting applies to the next chat you start, with no restart. A lower number stops
nothing that is running; new sessions can start once enough have closed. Each running session is
its own Claude Code process and uses memory. The setting is stored as `chat.max_live_sessions` in
the app settings document. An older VIKI Agent keeps the key but ignores it and runs at its own
limit of 8.

### Where VIKI Agent looks for Claude Code

VIKI Agent does not install or modify Claude Code; it only looks for it. Without
`VIKI_CLAUDE_CLI` set, a source checkout uses the CLI bundled with the installed SDK when it is
present. Otherwise, VIKI Agent looks for your own installation in the order Anthropic's installer
and each package manager place it:

- **Windows:** `%USERPROFILE%\.local\bin\claude.exe`, then
  `%LOCALAPPDATA%\Microsoft\WinGet\Links\claude.exe`, then the WinGet package folder, then npm's
  global install under `%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`.
- **macOS:** `~/.local/bin/claude`, then `/opt/homebrew/bin/claude`, then
  `/usr/local/bin/claude`.
- **Linux:** `~/.local/bin/claude`.
- **Then, on every OS:** PATH.

When no Claude Code is found, Settings › Providers shows the install command for your OS with a
Copy button and a link to Anthropic's setup guide.

1. Install Claude Code.
2. Restart VIKI Agent. It looks again only at the next launch.

Set `VIKI_CLAUDE_CLI` to use a Claude Code executable at another location instead.

### Claude Code updates

VIKI Agent leaves your own installation's update setting as it is. Only the CLI bundled with the
installed SDK, used in a source checkout, has its automatic updates paused.

Whether your own installation updates itself depends on how it was installed:

- **Anthropic's installer and npm installs:** update themselves.
- **WinGet, Homebrew and Linux package-manager installs:** update through their package manager
  (`winget upgrade Anthropic.ClaudeCode`, `brew upgrade claude-code`).

Settings › Providers flags a version older than the tested range.

For setup and the source-checkout commands, see [Getting started](getting-started.md). For the
illustrated overview, see the [visual guide](index.html).
