# Sessions and data

VIKI Agent shows Claude Code conversations from the CLI's transcript files. The app keeps
its own preferences and recovery records separately. Knowing which copy holds what matters
before an export, reset or move.

## Conversations and local state

Claude Code writes and removes conversation transcripts under `~/.claude/projects/`. VIKI Agent
reads them to build the session list, history, search and Between chats. It does not keep a
second transcript database.

- **Closing or archiving a chat in VIKI Agent:** does not preserve its transcript.
- **Removed transcripts:** if Claude Code removes a transcript, that conversation disappears from
  the list and search and cannot be resumed there. If you had only archived it, VIKI Agent also
  deletes that archive label, unless it is a profile chat. Otherwise it is listed under
  **Conversations whose record is missing**, where **Remove** deletes its labels; a profile chat
  that has not started is listed there with **Open** instead.
- **Archiving:** hides a chat from the active sidebar list. You can archive a listed chat even
  when it shows "This conversation is unavailable."

> [!WARNING]
> Claude Code's `cleanupPeriodDays` setting controls its cleanup; the documented default is 30
> days after last activity. Export conversations you need to keep before that cleanup.

For Between chats, VIKI Agent also reads Claude Code's list of running sessions in
`~/.claude/sessions` to name the chat a message came from. It reads only the `.json` files there.

### The app's own data folder

The app's own data folder holds `app.sqlite3`, images used by drafts and submitted turns, logs and
a browser profile. The **Data locations** view shows the active paths.

The database holds workspace registrations, settings, session labels and bookmarks, sealed drafts
and submitted text, recorded card answers, queues, routines, task records, Bot usage counts and
other app state. It is not a copy of the Claude Code conversations.

Default app-data folders are:

| Platform | App-data folder |
|---|---|
| Windows | `%LOCALAPPDATA%\viki-agent` |
| macOS | `~/Library/Application Support/viki-agent` |
| Linux | `$XDG_DATA_HOME/viki-agent`, or `~/.local/share/viki-agent` when `XDG_DATA_HOME` is unset or relative |

`VIKI_DATA_DIR` can select another app-data folder, outside vaults, `~/.claude` and `~/.codex`.

To make a cold copy:

1. Close VIKI Agent.
2. Copy the entire folder together, including SQLite sidecars and, on macOS and Linux,
   `sealing-key-id`.

Sealed values depend on the Windows account or on the matching macOS/Linux keychain item, so
copying the files alone may not make those values readable under another account or path.

### Bot usage log

The database also keeps a Bot usage log: each Bot chat's calls as token counts and ids, never
message text. A capture runs at the end of each chat turn and before each usage dashboard or CLI
read; closing VIKI Agent stops a running capture before its next write, and what it had not yet
written is read again next time.

- **Outlives transcript cleanup:** a logged call stays even after Claude Code's own cleanup
  (30 days by default, above) removes the transcript it came from. See
  [Bots](bots.md#token-usage) for how long VIKI Agent itself keeps a call.
- **Not a Reset category:** **Reset** does not clear it; see [Reset](#reset).
- **Not exported:** **Export settings** and **Export diagnostics** omit it.

### Bot learning

For each Bot, the database also keeps what [Learning](bots.md#learning) needs, never message text:

- **Episodes:** each finished Bot turn's time, outcome, kind of chat and tool names, the newest
  2,000 per Bot.
- **Web and MCP marks:** which chats read web or MCP content, until you clear one.
- **Runs and proposals:** the latest 200 runs per Bot and their proposals, each proposal's text
  sealed like drafts; a run with a proposal still waiting is kept. At most 100 proposals wait per
  Bot.
- **Use counts:** how often each memory record was recalled and judged helpful or harmful.

The memory records themselves are notes in the vault's `memory/` folder. Removing a Bot, or
unregistering its vault, deletes these rows; **Reset** does not, and **Export settings** and
**Export diagnostics** omit them.

### Magpie data

The bundled Magpie process keeps its configuration, provider keys, usage records, history and
caches under the app-data folder's `magpie/` directory. The installed app also extracts its
verified Magpie executable into that directory's `runtime/` folder. Magpie owns its data files;
its provider keys do not use VIKI Agent's encrypted connection store. Its own logs and any
agent configuration it manages can be outside that directory.

VIKI Agent settings exports and settings reset do not include Magpie data. Use Magpie's own
controls to manage its accounts, history and backups. To remove its local data, close VIKI Agent
and delete only the app-data folder's `magpie/` directory. This does not undo agent settings
Magpie changed or remove accounts from other applications.

### Drafts and images

Drafts and submitted turns use local sealed storage.

- **Claude Code's image copy:** when an upload is sent, Claude Code also keeps its own image copy.
  Clearing a draft or send history in VIKI Agent does not clear that copy.
- **Unused images:** VIKI Agent may delete an image once no draft or sent turn uses it, so save
  any attachment you need elsewhere before a reset.

### VIKI Agent's own window profile

VIKI Agent's own window keeps a separate Chromium profile outside this app-data folder, in
Electron's own per-OS location:

| Platform | Profile folder |
|---|---|
| Windows | `%APPDATA%\VIKI Agent` |
| macOS | `~/Library/Application Support/VIKI Agent` |
| Linux | `~/.config/VIKI Agent`, or `$XDG_CONFIG_HOME/VIKI Agent` when that is set |

It holds that window's own caches and its saved microphone choice. **Reset** does not clear it;
delete it yourself while VIKI Agent is closed.

## Finding and exporting a conversation

Use `Ctrl`+`K` (⌘+`K` on macOS) to search the listed transcripts.

- **Search covers:** session titles, your messages and finished replies.
- **Search omits:** tool input, tool output and thinking. It also excludes the **Other** session
  group.
- **Partial results:** large or unreadable transcripts can produce partial results.
- **Transcripts larger than 256 MiB:** read in bounded windows. Old content may be unavailable in
  the app's history or search even while the file exists.

Open a chat's own tools to **Export Markdown** or export a JSON archive with attachments.

- **Cut text:** if the window says a long prompt's displayed text was cut, inspect the prompt
  before relying on an archive. Use the full submission or transcript view first.
- **Importing an archive:** opens a read-only copy for reference.
- **Settings export and diagnostics export:** contain no conversation transcript.

## Backups, import and Reset

The Data view's vault backup copies a vault's notes, not every file in it.

- **Preview:** review the preview's note count, skipped items and instruction files.
- **Destination:** must be outside registered vaults, the app data folder, `~/.claude` and
  `~/.codex`.
- **Protection:** the backup is not encrypted, and a folder on the same disk does not protect
  against losing that disk.
- **Restore:** verifies a backup into a new folder and registers it read-only. It does not
  overwrite an existing vault.

### Settings and diagnostics files

**Export settings** and **Export diagnostics** each produce a JSON file after a preview.

- **Diagnostics:** can include selected local facts such as paths, settings, workspace
  registrations or a log tail.
- **Redaction:** VIKI Agent applies a redaction filter, but the output may still describe your
  machine or work. Inspect the file before sharing it.
- **Import settings:** previews and validates settings values. It does not import transcripts,
  vault notes or routines.

An import preview applies only to the settings as they were when you made it. If settings change
before Apply, make a new preview and select the values again. Reloading settings alone does not
renew the earlier import preview.

### Reset

**Reset** requires typing `RESET` and acts only on selected app categories: settings, drafts,
send history, approval records, chat-only scratch files and the browser profile.

- **Refusals:** the running browser profile is refused, and a scratch folder in use is refused.
- **Retained items:** an in-flight submission or an answer recorded during this launch can be
  retained, so the result may say **partial**.
- **Other app data:** other data kept by VIKI Agent, including routines, queues, task records and
  bookmarks, is outside these Reset categories. Use **Delete all decision history** or delete
  chosen rows under **Data** on the **Decision** page to clear Decision data.

Reset does not erase Claude Code transcripts or registered vaults. Reset does not undo changes
already made to files in a vault.

> [!CAUTION]
> Reset is irreversible for the rows it removes. Export any conversation you need and make a cold
> copy of the app-data folder before resetting.

- **Clearing drafts:** can orphan an attachment held only by a draft.
- **Clearing send history:** can remove the app's recoverable copy of sent text and an image's
  original name; its local image copy can then expire.

See [Using VIKI Agent](using-the-app.md) for the controls and
[Troubleshooting](troubleshooting.md) for safe recovery when storage cannot open.
