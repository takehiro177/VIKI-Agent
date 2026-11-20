# Troubleshooting

Start with the message shown in VIKI Agent. Then:

1. Open **Diagnostics** for provider and storage readiness.
2. Open **Data locations** if you need the active app-data path.

If the window cannot open, a startup message may name the log. The log is `logs/viki-agent.log`
under the app-data folder. It records warnings and errors; an empty file is possible after a
healthy launch.

| What you see | What to do |
|---|---|
| **Claude Code is not ready to start a session** | Open **Settings → Providers**. A missing CLI shows the install command for your OS; install it, then restart VIKI Agent so it is found. Still signed out? Send a message and use the chat's **Sign in** button, or run `claude` and use its login flow yourself. For Claude Code's own diagnostics, run `claude doctor` in a terminal. With **Let Claude Code use its other sign-in methods** turned off, a billing-related readiness warning means Claude Code is signed in to, or set up to bill, an account other than your Claude subscription: sign in with your Claude subscription, remove the setting that Settings → Providers lists, or turn that switch back on in **Settings › Chat**. After any of the three, restart VIKI Agent. To sign in, run `claude auth login` in a terminal; the billing message has no **Sign in** button. A listed setting also blocks chats on a model connection; a sign-in to another account alone does not. |
| **claude: update needed** | Claude Code is older than the range VIKI Agent has tested. Run `claude update`, or upgrade it with WinGet, Homebrew or your Linux package manager, then select **Check Claude Code** in Settings → Providers. |
| **Profile launch refused** (…_unconfirmed) in a profile or Bot chat | Claude Code did not confirm the profile's plan mode, model or app MCP servers when it started, so nothing was sent. Edit the profile in **Agents** and start again. |
| **You can run at most 30 sessions at the same time. Close one, or raise the limit in Settings › Chat, then send again.** | Your limit shows in place of 30. Close a chat that is running Claude Code, or raise **Sessions running at once** in Settings › Chat (1 to 999). Closing its tab does not erase its transcript. |
| **Disconnected. Reconnecting…** | Wait for the window to reconnect. Check **Drafts and send history** before resending a turn whose delivery is uncertain. |
| **This window belongs to a previous launch** | Close that window and open VIKI Agent again. The conversation records remain with Claude Code. |
| A reply is still running | Select **Stop** or press `Esc`. A hook, tool or connector can hold a turn open without a turn deadline. If Claude Code's process must be ended, inspect the resulting status before retrying. |
| A note save reports a conflict | Reopen the current note and compare it with your draft. Save only after reviewing the newer version; the app did not replace it. |
| Save says the drive does not support saving notes | The drive is not NTFS (Windows), APFS (macOS) or ext4, XFS, Btrfs or tmpfs (Linux), such as a network or exFAT drive. The vault stays read-only there; browse and search still work, or ask Claude Code to make a change under its own permissions. |
| A routine says **Missed**, **unknown** or **blocked** | Open that routine's run history and its chat, when present. A missed schedule is not run later automatically; an unknown result needs inspection before a manual retry. |
| A routine says **Skipped** (No card was waiting on Kanban) | The routine is set to **Only when a card is waiting**. It found no note directly in its vault's `kanban/` folder with `status: todo` in lowercase and an `owner/<seat>` tag naming its **Seat**. Check the card's spelling: `status: todo` in lowercase, and the tag exactly `owner/<seat>` with no `#` and no capitals. Also check that the note is directly in `kanban/`, not in a subfolder or a `Kanban/` folder. A card VIKI Agent cannot read does not cause **Skipped**: the time then runs as usual. **Test run** and **Run now** run without checking. See [Bots](bots.md#kanban). |
| A routine says **Skipped** (No note changed in its folder) | The routine is set to **Only when a note changed**. It found no note at any depth in its folder with a file time after the start of its last run at this version that succeeded or stopped for your decision. Check the folder's spelling: a folder with no notes, or a misspelled one, never counts as changed. A note moved or synced in can keep an older file time; edit it or use **Run now**. When VIKI Agent cannot list the whole vault, the time runs as usual. See [Bots](bots.md#create-and-run-routines). |
| A routine says **Stopped for your decision** | The routine is set to **Stop at a draft**, and Claude asked for a permission or a plan. Open the run's session for the draft and its report, and ask it to continue there once you decide. For later runs to wait for your OK instead, set **When a confirmation is needed** to **Wait until the time limit**. |
| Kanban shows no cards | Kanban, routines and VIKI read only notes directly in `kanban/` (lowercase). Move the cards there, or ask a chat in that vault to, then update your **Never send** folder rules to name `kanban/`. Bookmarks to moved cards show as missing. |
| A Kanban card shows under **Couldn't read** | Kanban could not read the card, and the card names the reason. When the reason is the front matter, keep it as simple as `/kanban` and the card template write it: the title in the card's `# ` heading with no `title:` line, and `tags:` on one line. When the card says to try again, select **Refresh Kanban**. See [Bots](bots.md#kanban). |
| VIKI does not start a Bot for a `todo` card | A **Report** in VIKI's launcher names why it held a start back: today's limit, the **Pause automatic starts at Claude plan usage** setting, quiet hours, a routine changed since its test, or two routines for one seat. VIKI starts a Bot itself only while **Start Bots' work** is **Allowed**; **Ask first**, the default, offers **Start** instead. The seat needs exactly one enabled routine set to **Only when a card is waiting**, tested at its current version. The vault's policy must be **Ordinary**. VIKI also starts none, without a **Report**, while **Proactive VIKI** is off or paused, while another routine run is going, or while **Pause all** holds your routines. A card written during a routine run or a chat VIKI started gets **Start** instead, unless **Let seats hand work to each other** is on. See [Start Bots' work](decision-engine.md#start-bots-work). |
| **What VIKI may do** reads **Holding** | **Pause automatic starts at Claude plan usage** is holding the chats, Bot runs and inbox suggestion runs VIKI starts on its own, and the row says until when. Your own starts still work. VIKI also holds them until Claude Code first reports your plan use after VIKI Agent starts, and while your plan limit is reached, at every setting. See [Pause automatic starts at Claude plan usage](decision-engine.md#pause-automatic-starts-at-claude-plan-usage). |
| Dictation or read-aloud has no voice | Check **Settings → Chat** and the operating system's microphone or installed voices. Dictation depends on its local model and engine; read-aloud depends on a browser-reported local voice. |

## When the app cannot open normally

A missing browser, occupied port 8765, unavailable data folder or missing built UI can stop
startup. Follow the startup message.

- **A browser left open after an earlier crash (macOS and Linux):** it can block a new launch.
  Quit that browser, then start VIKI Agent again.
- **A second launch merely opens another window:** the first instance is already running.

### Read-only recovery window

A damaged, locked or newer app store opens a read-only recovery window with Diagnostics and the
refusal reason, without starting a chat.

> [!WARNING]
> Do not use Reset or delete `app.sqlite3` to clear that screen: the database can contain drafts,
> submissions and preferences that exports do not recreate.

Instead:

1. Close VIKI Agent.
2. Copy the entire app-data folder to a separate safe location, including database sidecars and
   retained backups.
3. Inspect the reported cause.

An older build may not open data upgraded by a newer one.

- **Rolling back:** if you need to roll back, return to the matching build or a complete cold
  backup. Changes made after that backup will be absent.
- **Upgrade backups:** each upgrade step also leaves a verified copy of the database as it was,
  `app.sqlite3.v<N>-backup-<id>.sqlite3`, in the app-data folder. See
  [Sharing diagnostics and backups](security.md#sharing-diagnostics-and-backups) for restoring
  one.

### Updating a source checkout

Closing the last window alone can leave a turn running. Before updating a source checkout:

1. Stop running chats.
2. Close VIKI Agent, and wait for its process to exit.
   - **Windows:** confirm in Task Manager that the VIKI Agent process you launched has exited.
   - **macOS:** quit the browser VIKI Agent opened if it remains open.
   - **Linux:** wait until the command you started VIKI Agent with, such as
     `uv run viki-agent`, returns to the prompt.
3. Keep a complete cold copy of the app-data folder and any vault backup you depend on.
4. Follow the install and build steps in [Getting started](getting-started.md).
5. Reopen the app and check Diagnostics.

A newer build can upgrade app state, so keep the matching build with a backup intended for
rollback.

### Sealed values on macOS and Linux

On macOS and Linux, sealed local values depend on the platform keychain and the `sealing-key-id`
record in the app-data folder.

- **Keychain locked or unavailable:** unlock or restore it and restart the app.
- **Data folder moved:** restore its original path before retrying.
- **Key-id file:** keep it with the folder. Deleting it while the keychain is merely locked can
  strand existing drafts and other sealed values.

### Unfinished saves and note operations

When the Vault view's **Recovery** panel still lists a save or note operation that didn't finish
after you restart VIKI Agent:

1. Copy the vault and the app-data folder before changing either one.
2. Compare the note at each path involved and any entry in **Recovery**. An interrupted operation
   or another program may have changed the note.

Do not delete a hidden temporary file or a backup of the previous version in the vault, and do
not overwrite a note to force the operation through. VIKI Agent stops an operation it cannot be
sure of so you can decide which version to keep.

### Sharing a diagnostics report

For a report you choose to share, use **Export diagnostics** and inspect the JSON before sending
it. Redaction may miss context you consider private.

See [Known issues](known-issues.md) for limits you may run into and
[Sessions and data](sessions-and-data.md) before Reset.
