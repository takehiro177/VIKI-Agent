# Getting started

Install VIKI Agent from a source checkout, connect a model, and start a chat. The
[visual guide](index.html) gives an illustrated overview; these manuals have the full steps.

## What you need

Check these requirements before you install.

- **Operating system:** Windows 11, macOS on Apple silicon, or desktop Linux on x86_64 or aarch64
  with glibc 2.28 or later. macOS also needs `git`, which Xcode Command Line Tools provide
  (`xcode-select --install`). Linux also needs X11 or Wayland, `xdg-open`, `git`, and a Secret
  Service available through `secret-tool` (package `libsecret-tools`). The `.deb` package
  declares `libsecret-tools`, `git` and `zenity | kdialog`, the folder picker's dialog, as
  dependencies.
- **Vault folder:** keep a vault in a folder that is not cloud-synced. On Windows, VIKI Agent
  refuses the OneDrive folders that the `OneDrive`, `OneDriveConsumer` and `OneDriveCommercial`
  environment variables name. On macOS, it refuses `~/Library/Mobile Documents` (iCloud Drive)
  and `~/Library/CloudStorage` (the cloud providers' folders) as cloud-synced, and the rest of
  `~/Library` as a system folder. On Linux, it refuses no folder for being cloud-synced.
- **Python:** Python 3.14 or newer and `uv`.
- **Node.js:** Node.js 24 with `npm` to build the interface.
- **Browser window:** Microsoft Edge on Windows by default. On macOS, discovery checks Edge,
  Chrome and Chromium. On Linux, install a non-confined Edge, Chrome or Chromium executable;
  snap and Flatpak launchers are skipped. See [Configuration](configuration.md) to select an
  explicit executable.
- **VIKI Agent's own window:** needs no installed browser.
- **Claude Code:** a source checkout runs the copy of Claude Code that `uv sync` installs with
  the Claude Agent SDK. The compiled app and installer builds run your own Claude Code, installed
  with Anthropic's installer. On Windows: `irm https://claude.ai/install.ps1 | iex`. On macOS or
  Linux: `curl -fsSL https://claude.ai/install.sh | bash`. See
  [Anthropic's setup guide](https://code.claude.com/docs/en/setup).

The app uses the existing Claude Code CLI without installing or modifying it. Connect a model
through either route:

- **Claude Code sign-in:** your Claude plan, or an API key or cloud provider you set up in
  Claude Code. A chat that is not signed in offers **Sign in** for your Claude plan on Windows,
  macOS and Linux.
- **Your own model:** add a cloud API or local model server in **Settings → Providers**.
  Custom connections need a tool-calling model and support Chat Completions, Responses API and
  Anthropic Messages.

## Install the released app

Skip the source build below and install a prebuilt release instead. The repository's README
links each installer under **Download**:

- **Windows:** run the downloaded setup. It adds Start menu and desktop shortcuts.
- **macOS:** drag VIKI Agent to Applications.
- **Linux:** `sudo apt install ./viki-agent-linux-amd64.deb`, then open VIKI Agent from the app
  menu.

These builds are unsigned. See
[Known issues](known-issues.md#viki-agents-own-window-and-installers) for the steps Windows and
macOS ask for on first open.

Check a downloaded installer against the checksums published beside it on the same release, with
`sha256sum -c SHA256SUMS-linux.txt` or `shasum -a 256 -c SHA256SUMS-macos.txt`. On Windows, use
PowerShell's `Get-FileHash`.

### Update the released app

The installed app asks GitHub whether a newer release exists a minute after it starts and every
6 hours, while **Settings → Diagnostics and About**'s **Check for updates automatically** is on,
and whenever you choose that page's **Check now**. A ready update shows a notice: choose
**Download** to open the releases page in your browser, quit VIKI Agent, then run the new
installer. Your settings and chats are kept: on Windows, the installer's uninstall step does not
delete the app data folder (`deleteAppDataOnUninstall` is off).

## Install and start

Clone VIKI Agent's GitHub repository (the [visual guide](index.html#start) links to it), then run
these commands from its root on Windows, macOS, or Linux:

```sh
uv sync
npm --prefix frontend ci
npm --prefix frontend run build
uv run viki-agent
```

### First launch

The first launch opens the setup guide on **Welcome to VIKI**. It lists the steps with their state and an action for each:

1. **Install Claude Code** (needed). VIKI Agent runs every chat through Claude Code and doesn't install it. Anthropic's setup guide and the install commands are in **Settings → Providers**. Restart VIKI Agent after installing.
2. **Sign in to Claude, or connect a model** (needed). **Sign in** opens Claude Code's own sign-in, and VIKI Agent checks it when a chat starts. Or save a model connection and select **Use for new chats**.
3. **Create or open a vault** (needed for notes, Bots, Kanban and the persona). Choose **Create a vault**, **Open a folder** or **Set up later**. **Open a folder** registers an existing folder read-only: you can browse its notes and chat about them, and the app's own note saving stays off there. Without a vault, chats still run in a shared temporary folder. A vault you create or open starts being used after the next restart.

The guide also lists optional features: Bots and Kanban, model connections, code folders, routines, voice and messaging. To open the guide again, use **Help → Setup guide**.

A new vault can start from either of these:

- **Start with VIKI (recommended):** adds folders, note templates, empty memory, Kanban, agents
  and skills.
- **Blank folder:** holds only a welcome note, and `.viki/vault.json` too if you tick
  **Write portable metadata**.

Send a message in a new conversation to start chatting.

- **Claude Code sign-in:** complete sign-in in Claude Code if it is missing.
- **Your own model:** save its endpoint, model ID and optional key in **Settings → Providers**,
  then select **Use for new chats**. See
  [Using your own model](using-the-app.md#using-your-own-model).

### Stop and update

Closing the last app window gives running chats up to 300 seconds to finish. Waiting permission
requests are denied while no window is open.

Installed from a release instead of a source checkout? See
[Update the released app](#update-the-released-app) above; the steps below are for a source
checkout.

To update:

1. Stop running work first.
2. Wait for the app process to exit before you update the checkout or run `uv sync` or other
   upgrades.
   - **Windows:** confirm in Task Manager that the VIKI Agent process you launched has exited.
   - **macOS:** quit the browser if it remains open.
   - **Linux:** wait until the command you started VIKI Agent with, such as
     `uv run viki-agent`, returns to the prompt.
3. Back up VIKI Agent's [app data folder](configuration.md#app-data-location): an older version
   refuses to open app data that a newer version has updated.
4. Update the checkout, for example with `git pull`.
5. Repeat the install and start commands.

## VIKI Agent's own window

VIKI Agent can also open in its own window, on any desktop, with no Edge, Chrome or Chromium
installed. After the install steps above (`uv sync` and the built frontend are still needed),
run:

```sh
npm --prefix desktop ci
npm --prefix desktop start
```

- **First start:** downloads Electron from GitHub and checks it against the SHA-256 sums its npm
  package lists.
- **Closing the last window:** follows the usual rules above.
- **Quitting the app:** stops running chats at once.

## Notes and voice

Note saving, the note actions, and keeping a chat image in the vault are supported on Windows,
macOS and Linux. This needs a supported drive: NTFS on Windows, APFS on macOS, or ext4, XFS, Btrfs
or tmpfs on Linux. On another drive, such as a network drive, the vault stays read-only.

### Voice

Voice is optional. To inspect available offline speech models and backends, run:

```sh
uv run python scripts/voice_setup.py --list
```

- **Install:** run `uv run python scripts/voice_setup.py` to install the recommended model and
  backend.
- **Check:** then run `uv run python scripts/voice_setup.py --check` to check existing
  installation folders for missing, resized or irregular files.

Setup downloads the selected files; voice processing runs locally. GPU voice setup may require a
compatible NVIDIA driver and the Microsoft Visual C++ runtime on Windows.

## Optional local wheel

After building the frontend, create a local wheel with:

```sh
uv run python scripts/build_product.py
```

The wheel includes the built interface, app icon and bundled third-party license notices. The
frontend configuration skips local `.env` files and custom client environment variables; app
settings come from the local server.

> [!IMPORTANT]
> Keep personal settings, credentials and vault copies out of product resource folders.

The build scripts check resources and filenames:

- **Extra resources:** rejected outside the bundled interface, welcome note and starter set.
- **Private filenames:** wheel members and compiled backends are checked for private filenames
  such as `.env`, database files, private keys, logs and source maps.
- **Refusals:** a refusal names the file without showing its contents.
- **Runtime files:** the bundled certifi CA certificates and contained framework links are
  checked separately so those runtime files can be included.

## Optional compiled app

After building the frontend, build a standalone folder app with Nuitka. The script refuses an
editable install and prints the command to use instead, so run exactly:

```sh
UV_PROJECT_ENVIRONMENT=<folder outside the repo> uv run --no-editable --no-dev --group build python scripts/compile.py
```

In PowerShell, set the variable first:
`$env:UV_PROJECT_ENVIRONMENT = "<folder outside the repo>"`, then run the same `uv run` command.

This needs:

- **Windows:** MSVC (Visual Studio 2022 or later with the C++ tools).
- **macOS:** Xcode.
- **Linux:** gcc and patchelf.

The result is the folder `dist/compiled/viki-agent.dist/`. Start `viki-agent` (`viki-agent.exe`
on Windows) in that folder. It runs without Python, uv or Node, and uses the Claude Code you
install.

Dictation setup (`scripts/voice_setup.py` above) needs a checkout. The compiled app's own folder
is not one, and VIKI Agent refuses it as a vault or code folder.

## Optional installers

After building the compiled app above (`scripts/compile.py`, which also writes
`dist/compiled/viki-agent-licences.json`), build installers for Windows, macOS or Linux with
electron-builder. `npm run dist` needs `npm ci` in both `frontend/` (the install steps above) and
`desktop/`.

Build on the OS and architecture you intend to install on. The installer command refuses
cross-platform and cross-architecture targets; Linux builds use the host's x64 or arm64
architecture (`viki-agent-linux-amd64.deb` or `viki-agent-linux-arm64.deb`), and releases carry
the x64 one:

```sh
npm --prefix desktop ci
npm --prefix desktop run dist
```

The result lands in `dist/desktop/`:

- the installers
- `latest*.yml`, the update feed for that OS (`latest.yml` on Windows, `latest-mac.yml` on
  macOS, `latest-linux.yml` on Linux)
- `SHA256SUMS-<os>.txt`
- four software-bill-of-materials files: `sbom-python.cdx.json`, `sbom-frontend.cdx.json`,
  `sbom-desktop.cdx.json` and `sbom-magpie.cdx.json`
- `THIRD-PARTY-NOTICES-<os>.txt`

These builds are unsigned. [Known issues](known-issues.md) lists what Windows and macOS ask when
you open one.

Check an installer against its checksums with `sha256sum -c SHA256SUMS-linux.txt` or
`shasum -a 256 -c SHA256SUMS-macos.txt`. On Windows, use PowerShell's `Get-FileHash`.
