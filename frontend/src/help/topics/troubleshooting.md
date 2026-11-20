# Troubleshooting

What common messages in VIKI Agent mean, and what to do about each one.

## Claude Code is not ready

When Claude Code cannot start, **New session** turns off: **Claude Code is not ready to start a session.**
A banner next to it names the exact cause, such as a missing CLI, blocked billing or an app
store that is not ready, beside a **Set up provider** button.

With **Let Claude Code use its other sign-in methods** turned off, blocked billing means Claude Code
is signed in to, or set up to bill, an account other than your Claude subscription. Sign in with
your Claude subscription, remove the setting that **Settings › Providers** lists, or turn that
switch back on in **Settings › Chat**. After any of the three, restart VIKI Agent. To sign in, run
`claude auth login` in a terminal; this card has no **Sign in** button. A setting listed there
also blocks chats on a model connection; a sign-in to another account alone does not.

## Signed out of Claude Code

Being signed out does not turn off **New session**. Instead, the message you send fails, and
the chat offers **Sign in** on Windows, macOS and Linux: VIKI Agent opens Claude Code's own
sign-in in a PowerShell window on Windows, a Terminal window on macOS, or your desktop's
terminal on Linux, and your browser shows the Claude sign-in page. Finish there, then send
your message again. On Linux without a terminal emulator VIKI Agent can use, or if the window
does not open on any OS, run `claude auth login` yourself in a terminal.

## A chat will not start or send

VIKI Agent runs at most 30 Claude Code sessions at once unless you change **Sessions running at once** in Settings › Chat. At the limit, starting or sending shows "You can run at most 30 sessions at the same time. Close one, or raise the limit in Settings › Chat, then send again.", with your limit in place of 30. Close a running chat, or select **Change limit** and raise the number, then try again.

## Connection and windows

If the connection drops, VIKI Agent shows **Disconnected. Reconnecting…** and retries by
itself; just wait. **After reconnecting, the rest of the reply will appear.**

An old window left open after an update or restart shows **This window belongs to a previous launch.**
Open VIKI Agent again; its chats stay recorded.

## A reply seems stuck

Select **Stop**, or press `Esc`: **Stops the reply. Agents running in the background keep going.**

## Notes, routines and voice

Saving a reply as a note, and editing notes in the **Vault** view, need a supported drive: NTFS on Windows, APFS on macOS, ext4, XFS, Btrfs or tmpfs on Linux.

If a routine's scheduled time comes while VIKI Agent is closed, it does not run; its
history then shows **Missed**.

A routine set to **Only when a card is waiting** shows **Skipped** when no `todo` card for its
seat waited in `kanban/`. Check the card's spelling: `status: todo` in lowercase, the tag
exactly `owner/<seat>` with no `#` and no capitals, and the note directly in `kanban/`.
Kanban, routines and VIKI read only notes directly in `kanban/` (lowercase); move cards there or ask
a chat in that vault to, then update your **Never send** folder rules to name `kanban/`.
A routine set to **Only when a note changed** shows **Skipped** when no note in its folder changed
after its last completed run started. Check the folder's spelling, and edit a note you moved or
synced in, since it can keep an older file time.
A routine set to **Stop at a draft** shows **Stopped for your decision**: open its session
for the draft and the report.

If dictation or read-aloud has no voice, check **Settings** › **Chat**: dictation needs
a working microphone, and read-aloud needs a voice installed on your computer.

## What VIKI may do reads Holding

While **What VIKI may do** in **Decision** reads **Holding**, **Pause automatic starts at Claude plan usage**
is holding the chats, Bot runs and inbox suggestion runs VIKI would start on its own: your
plan use is at the share you picked, your plan limit is reached, or Claude Code has not
reported your plan use since VIKI Agent started. The row says until when, and your own starts still work.

## When the app cannot open normally

A read-only recovery window headed **VIKI is running in recovery mode.** means the
app store is damaged, locked, or newer than this build.

Do not select **Reset** to clear it. Close VIKI Agent, copy the entire app-data folder
somewhere safe, then look at the reason the window gives.

## Getting help

For a report you can share, use **Export diagnostics** and look at the file before
sending it.
