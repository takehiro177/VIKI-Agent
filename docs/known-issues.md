# Known issues and limits

These are limits you may run into. For an error with a
recovery step, see [Troubleshooting](troubleshooting.md).

## Claude Code and chats

These rows cover setup, sending and stopping, history and search, notices and figures, and custom
model connections.

> [!WARNING]
> Archive, pin and bookmarks do not preserve a transcript that Claude Code cleans up. Export
> important conversations before the CLI's retention period.

| Situation | What to expect |
|---|---|
| Signing in on Linux when neither `xdg-terminal-exec` nor `x-terminal-emulator` is installed | VIKI Agent can't open a sign-in console. Run `claude auth login` in a terminal instead. |
| VIKI Agent opened from Finder, the Dock or a desktop menu (macOS or Linux) | It adds `~/.local/bin`, `/opt/homebrew/bin`, `/opt/homebrew/sbin` and `/usr/local/bin` to its PATH when they exist and are not on it. It does not run your shell, so a folder that only your shell's startup files add, such as a version manager's, is missing for saved checks and for Claude Code's MCP servers and hooks. Start VIKI Agent from a terminal to keep that PATH. |
| Claude Code is installed, or moved, while VIKI Agent is open | VIKI Agent keeps using what it found at launch. Restart it to find the new installation. |
| macOS and Linux: VIKI Agent is killed or crashes while Magpie is running | Magpie can keep running until VIKI Agent starts again, which stops it. If **Open Magpie** reports its ports in use before that, quit the `magpie` process that holds 3426 and 3431. |
| Installing Claude Code plugins through **Open Capabilities** | This view lists observed plugins only. Magpie manages its own plugins in its dashboard. |
| A brand-new chat is stopped before Claude Code records its first turn | A later send can report that the conversation record was not found. Start a new session and recover any unsent text from **Drafts and send history**. |
| A background agent's **Stop** is pressed | An older Claude Code build may stop other runs in that chat as well. A still-open permission card can remain until answered or the process ends. |
| A request mixes right-to-left letters, such as Arabic or Hebrew, with digits or punctuation | Permission cards show hidden characters as codes, but right-to-left letters still follow the usual display rules, so such a command can be drawn in a different order from the one it runs in. The zero-width joiners that Persian and Indic text use are also shown as codes. |
| Two single clicks land on **Allow once** or **Deny** in the chat or **Approvals** as cards move up | The second click of a double-click is ignored, but two separate clicks each answer the card under the pointer. Read the card before the second click. |
| A waiting **Steer** message | It can't be taken back. **Stop** runs it next. Send another message to change course. |
| Closing the chat while a **Steer** message is still waiting | The message shows as not sent, but Claude Code may still start it. Let the waiting rows clear before you close the chat. |
| The window changes size while text is in the message box | The message box can keep its old height until the next keystroke. |
| A chat or search says history is partial | Large transcripts and bounded searches can omit older content. Open the original transcript or narrow the search before concluding content is absent. |
| **Search sessions** doesn't find a message Claude read mid-reply | It shows in the conversation. |
| Several windows are open | Each window can raise its own agent or routine desktop notification. So a notice may appear more than once. |
| Context or plan-limit figures are absent or stale | Figures appear only when Claude Code reports them. Context updates after a reply ends. A new process can show the previous trigger until its own first reply completes. |
| A custom model connection fails or is interrupted | The chat sends no more requests until you close and reopen it. Fix the connection, then close and reopen the chat to retry. |
| A custom connection encounters a native tool-search result | Chat Completions and Responses connections cannot translate native tool-reference blocks. Start a chat with an Anthropic Messages connection for those results. |
| Signing in through Magpie inside the desktop app | Some sign-in flows show a popup-blocked notice even after the sign-in page opens in your system browser. Continue in that browser and return to Magpie; it keeps checking for completion. |

## Vault notes and app data

These rows cover where a vault folder may live, direct note Save and note actions on macOS or
Linux, a deny rule in some **Start with VIKI** vaults, and opening the app data with an older
build.

| Situation | What to expect |
|---|---|
| A vault folder inside a cloud-synced folder | Windows refuses the OneDrive folders only. macOS refuses iCloud Drive (`~/Library/Mobile Documents`) and `~/Library/CloudStorage`, and the rest of `~/Library` as a system folder. Linux refuses no folder by name for being cloud-synced. Keep the vault outside any synced folder. |
| Direct note Save or note actions in a vault on another drive, such as a network drive (macOS or Linux) | The vault stays read-only. |
| Direct note Save or note actions in a vault on a drive that isn't NTFS, such as an exFAT USB drive (Windows) | The vault stays read-only, and the app says the vault's drive doesn't support saving notes. |
| Direct note Save or a note action that replaces a note (macOS or Linux) | The replace does not keep extended attributes or Finder tags. |
| A note save that collides with another program's save (macOS or Linux) | With no file lock from the OS, the save keeps that program's version and says the note changed. A new note's save can wait in recovery instead. |
| Another program keeps the note open for writing during a save | On macOS or Linux, with no file lock from the OS, that program can lose its own later write. On Windows, the save is refused while another program holds the note open for writing. |
| A vault that uses Git LFS or another Git filter | The status bar's Git chip counts a filtered file as changed when its timestamps changed but its contents did not, for example after the vault was copied. Running `git status` in the vault yourself clears it. |
| An Ordinary vault whose `AGENTS.md` or `CLAUDE.md` comes to hold the same text as a protected vault's file of that name | Saving that text in **Settings › Instructions and Persona** is refused. Another program's edit, a protected vault's file that changed since VIKI Agent last read it, or a vault made protected later can still bring the files into line; VIKI Agent then refuses every save to this vault. Change the file outside VIKI Agent so the two differ. |
| A **Start with VIKI** vault whose `.claude/settings.json` denies `Edit(_raw/**)` | Claude Code applies that rule to new files too, so /learn and the researcher can't save originals into `_raw/`. Delete that line from the vault's `.claude/settings.json`. `_raw/` stays create-only through the first prime directive in the vault's `AGENTS.md`. |
| Updating VIKI Agent | Each update step to `app.sqlite3` leaves a verified copy of the store beside it. VIKI Agent deletes none of them. So an update that crosses several store versions leaves one such copy for each step in the app's data folder. |
| Opening the app data with an older build | The older build refuses it. Rolling back to the copy made before the update loses what the app stored after it, such as saved settings. See [Rolling back after an upgrade](security.md#rolling-back-after-an-upgrade). |

## Code folders

These rows cover saving and Git in code folders, managing folders and previews, and worktrees.

### Saving and Git

| Situation | What to expect |
|---|---|
| A code folder on another drive, such as a network drive (macOS or Linux) | It is not supported for Save, commit, integrate, push and worktrees. |
| A code folder on a drive that isn't NTFS (Windows) | It is not supported for Save, commit, integrate, push and worktrees. |
| A save that collides with another program's write (macOS or Linux) | With no file lock from the OS, the save keeps that program's version and says the file changed elsewhere. |
| Another program keeps the file open for writing during a save | On macOS or Linux, with no file lock from the OS, that program can lose its own later write. On Windows, the save is refused with "Another app is using this file." instead. |
| A save that replaces a file (macOS or Linux) | The replace keeps the mode bits, the executable bit included. It keeps the group where the account may, else it drops group access. It drops xattrs, Finder tags, ACLs and the creation date. |
| A file with no owner write, another user's file, or a file with a macOS `uchg` or `uappnd` flag (macOS or Linux) | The save says the file is read-only or locked. |
| A file name that breaks the Win32 name rules (every OS) | The Win32 name rules apply on every OS. So a Linux file named `a?b.txt` shows in the folder tree but cannot be saved. |
| A code file name part over 255 UTF-8 bytes (macOS or Linux) | It is refused. |
| Hooks (macOS or Linux) | Hooks run as Git runs them. |

### Folders, previews and the picker

| Situation | What to expect |
|---|---|
| Removing a code folder that still has a worktree | VIKI Agent refuses until every worktree under it is removed in the Code view first. |
| Removing a code folder while its preview is running | The preview keeps running, and the Code view can no longer list or stop it. It stops when VIKI Agent closes. Stop a folder's preview before you remove the folder. |
| Unticking a folder in Settings while a chat is using it | The open chat keeps its folders until its process stops. The new tick set applies once it restarts or a new chat opens. |
| The folder picker (**Browse…**) is left open | On Windows it has no timeout, so closing VIKI Agent waits until the picker closes. Off Windows, the picker program is stopped after 600 seconds. |

### Worktrees and cleanup

| Situation | What to expect |
|---|---|
| Inspecting a chat's worktree in the Code sidebar | The sidebar browses the selected registered code folder. It does not automatically follow the chat's session worktree. The worktree's files can differ from the registered folder's files. |
| Restoring an archived worktree | A restored file comes back with the line endings a checkout writes. So a file whose endings differed from that comes back converted. |
| `git push --mirror` on a trusted code folder | It also sends `refs/viki/archive/*`, worktree cleanup's archive refs, to the remote. |
| An editor or dev server has an idle worktree's folder open | Watching the folder for changes does not keep it from cleanup. Only a process whose working folder or an open file is inside the worktree does. |
| Worktree cleanup can't tell whether a program is using an idle worktree | It keeps the worktree, and the **Worktrees** tab says "A program has a file or folder open inside it." |
| A worktree whose open-program check cannot finish (macOS) | VIKI keeps it when its folder or the system's open-file query cannot be read. |
| A Bot chat that started before its Bot had a code folder, and a Bot's routine run | It gets no copy. |
| A Bot code copy's first send | It takes longer while the copy is made. It waits up to 10 seconds for VIKI Agent's other work in that folder. VIKI Agent keeps at most 32 worktrees, so a 33rd copy is refused until you remove one in the **Worktrees** tab. |
| Git settings of a folder that has had a Bot copy change | Any change does it, including an ordinary `git push -u`, a branch that sets upstream tracking, a changed remote or `git config` for your name or e-mail, run by you or by Claude Code in the copy. VIKI Agent's own Git actions on that folder and its worktrees refuse, and the status bar's Git chip shows an unknown status, until you undo the change or select **Confirm the current settings** in the **Worktrees** tab. The check goes on after the last copy is removed, until you remove the folder from the list. See [Bot code copies](security.md#bot-code-copies) for the limits that remain. |
| A Bot whose code folder's hooks folder holds a subfolder or a hard-linked file (the layout of husky 5 to 8) | Every first message answers "VIKI Agent can't read this folder's Git settings safely." and no copy is made. Change the hooks layout, or clear the Bot's code folder in **Bot settings**. |
| A released Bot copy cleanup can't handle safely | It stays on disk, marked **Released** in the **Worktrees** tab, and counts toward the 32-worktree limit until you remove it. This happens, for example, with a submodule, a switched branch, a merge in progress, or a changed file under a Git filter other than Git LFS. See [Worktree cleanup](using-the-app.md#worktree-cleanup) to remove one. |
| A Bot copy in a folder whose Git filter rewrites files on checkout (not Git LFS) | The Code view can list unchanged files as changed, and **Remove** then reports changes. Running `git status` in the copy yourself clears it. |
| Restoring an archived Bot copy that holds Git LFS files | A file whose object is not in the repository's own LFS store comes back as its pointer file. Nothing is downloaded. |
| Commit in a Bot copy when commits are signed by a program named by a relative path | VIKI Agent does not start that program there, so Commit is refused. |
| A worktree not made by VIKI Agent | Cleanup sweeps, archives and offers **Restore** only for the worktrees VIKI made in its own area. |
| A worktree recorded from before VIKI Agent's data folder last moved | Restoring it from the **Worktrees** tab is refused, since its folder no longer sits in the current data area. Restore it with Git by hand. |

## VIKI's launcher and Decision

These rows cover the chats, runs, drafts and items VIKI starts or offers on its own. The
[Decision guide](decision-engine.md) explains each setting these rows name.

### What VIKI starts on its own

| Situation | What to expect |
|---|---|
| Letting VIKI prepare drafts, start chats or start Bots | VIKI starts a Bot only through its own tested routine, under **Start Bots' work**. Apart from the notice of a Bot whose delivery you turned on, it does not message other chats or people or send to your phone. |
| Right after VIKI Agent starts | VIKI starts no chat, Bot run or inbox suggestion run of its own until Claude Code first reports your plan use. This applies whatever **Pause automatic starts at Claude plan usage** says. Your own starts and saved checks are not held. |
| After **Stop all** | VIKI starts no chat or Bot run of its own for one check interval. What still waits starts at the check after that. |
| A chat VIKI started that finished without your answer | VIKI closes it once it finishes, fails or reaches its time limit while you have answered none of its cards. It closes even while you are looking at it. Select it in your session list to reopen it from its transcript. |
| Stopping a chat VIKI started | VIKI does not start work for that item again on its own, even after the item changes. |
| **Delete all decision history** after marking items **Not useful** | Those items can come back, and VIKI may start their work again. A kind you turned off with **Stop these** stays off. |
| A second look or a project plan still open when you use **Delete all decision history** | If VIKI Agent closes before it ends, the next start lists it as a chat VIKI started that has finished. |
| Deleting a suggestion you marked **Not useful** | It can come back as a new suggestion, and VIKI may start its work again. |
| Deleting suggestions during the day | They leave today's message draft, saved check and inbox suggestion counts, so more may come that day. |
| Chats VIKI started | **Data** cannot delete them. Their transcripts stay in Claude Code. |

After **Delete all decision history** during a day, what VIKI already started or raised that day counts
like this:

| Started or raised that day | What changes |
|---|---|
| Chats for **Look on my behalf** | They count toward **Daily limit: drafts** instead of **Daily limit: looks VIKI starts**, until the next day. |
| Chats for **Take a second look at my work** | They count toward **Daily limit: drafts** instead of **Daily limit: second looks VIKI starts**, until the next day. |
| Chats for **Plan next steps for my projects** | They count toward **Daily limit: drafts** instead of **Daily limit: project plans VIKI starts**, until the next day. |
| Message drafts | Their counts are freed from **Daily limit: message drafts**. |
| New commits for **Run my saved checks** | Their counts are freed from **Daily limit: new commits VIKI checks**. |
| Inbox states | Their counts are freed from **Daily limit: inbox suggestion items**. |

### Bots and Kanban

| Situation | What to expect |
|---|---|
| A saved Bot thread reopens with no reply | Native history can show the same records after an empty reply or after reply records were removed. Open its Bot chat to inspect the available history before continuing. |
| A Bot is removed or Bots are reset while the app window is open | The Bot's chats show in the ordinary chat list again after the window reloads. |
| A Bot task thread beyond the newest 50 | It opens with its Bot's header when the chat's saved Bot identity names that Bot. In a vault whose policy is not **Ordinary** it still opens as a profile chat, and for a Bot with more than 50 threads a routine run's chat opens with the header reading **Task thread**. |
| A Bot pins a model connection and the connection is removed or unreadable | The Bot's chats refuse to start; there is no fallback to your Claude Code sign-in. Routine runs use the native default. |
| Cards in a vault synced from another computer | VIKI tells your cards from a Bot's by the card file's change time, which a sync can move. So it may offer **Start** where it would have started a Bot, or start one for a card a Bot wrote. |
| A `todo` card synced in after a Bot's run with an older file time | It gets no start or **Start** offer until it changes again. |
| **Stop** on a Bot run VIKI just started | While Claude Code is still starting, **Stop** can miss the run. Press it again once the run's session shows. |
| Stopping a Bot run VIKI started | With **Start Bots' work** set to **Allowed**, VIKI starts that seat again on its own only once you add or change one of its `todo` cards. **Run now** and the routine's own times still run it. |
| A team with thousands of Bot runs and chats VIKI started | A `todo` card older than the newest 5,000 of them counts as possibly a Bot's. A card a Bot wrote during its run counts the same way. |
| A card whose `title:` text has a comma, colon or quote, or whose tags are listed on the lines below `tags:` | VIKI reads only simple front matter, so such a card can be left unread. VIKI does not count an unread card, and a routine set to **Only when a card is waiting** runs its time as usual. Put the title in the card's `# ` heading and keep `tags:` on one line, as `/kanban` and the card template do. |
| Cards kept in a folder other than `kanban/` | They don't show or count until you move them into `kanban/`, or ask a chat in that vault to. |
| **Core team** says "This vault has no Kanban skill" | A vault can lack `.claude/skills/kanban/`, or hold its card skill under another name. Copy `.claude/skills/kanban/` from a new Start with VIKI vault, as the Help topic's "Add Kanban to a vault you already have" describes, then choose **Refresh Bots**. |
| **Never send** folder rules and cards in `kanban/` | A folder rule covers a card only if it names `kanban/`. |
| A bookmark to a card you moved | It shows as missing. |
| A note moved or synced into a folder a routine waits on | A routine set to **Only when a note changed** goes by each note's file time. A move or a sync can keep that time older than the routine's last run, so the routine's next time can be skipped. Edit the note, or use **Run now**. |
| A Bot that writes a new finished card each run, without moving the `todo` card it was started for | VIKI counts any of a Bot's cards that is not `todo` and changed during its run as progress. So the Bot can be started again, up to **Daily limit: Bot runs VIKI starts**. |

### Vault, chats and bridges

| Situation | What to expect |
|---|---|
| A memory whose cited note was only rewritten | VIKI compares a cited note's change time with when you approved the memory. So a sync or tool that rewrites the file unchanged lists the memory too. |
| A Bot's memory kept in VIKI Agent's data folder | It is not part of vault backups. After a vault's policy changes, the Bot's earlier memory folder is not used. |
| A message VIKI lists as possibly not arrived | The item stays after you check the chat. Mark it **Not useful** to clear it. |
| An MCP server item after you turned the server off | VIKI reads the chat's last context reading, which updates when that chat finishes a reply. So the item stays until the chat's next reply. |

### Looks at code folders

| Situation | What to expect |
|---|---|
| A code folder whose uncommitted changes you keep on purpose | VIKI looks at each state of those changes once. Set **Look on my behalf** to **Ask first** or **Off** to stop these chats. |
| A code folder with only new, untracked files | VIKI checks changes to files Git already tracks, so such a folder gets no look. |
| A launch whose active workspace is a code folder | VIKI checks code folders for **Look on my behalf** only while a vault is the active workspace. |
| A code folder's look item that closes and comes back | VIKI lists a code folder only from a check in the last half hour that found its changes idle for a day. It checks one folder at a time. So an item can close until that folder's next check. This can happen after VIKI Agent starts, after a failed check, or with more trusted folders than checks in half an hour. |
| A code folder that uses Git LFS or another Git filter | VIKI's check runs `git status` there, as the Code tab does. So the folder's own Git filters run and may update their cache under `.git`. In a Bot copy, only Git LFS's own commands run. |
| A trusted code folder whose `.claude` settings or `.mcp.json` change after you trusted it | Chats there, VIKI's looks included, run the new hooks and servers without asking again. Set **Look on my behalf** to **Ask first** to start every look yourself. |

### Second looks and project plans

| Situation | What to expect |
|---|---|
| A long task in a chat you are not looking at | VIKI takes a second look only at the chat open in front of you, when its last reply finishes. |
| A reply that changed files that are not notes, or changed notes with shell commands | A second look starts only when the reply edited notes in the vault with Claude Code's edit tools. |
| A chat in a code folder, an agent's chat or a Bot's run | These get no second look. |
| A long or corrected chat | Each later reply that edits notes can get its own second look, up to **Daily limit: second looks VIKI starts**. Set **Take a second look at my work** to **Ask first** or **Off** to stop them. |
| A second look with **Never send** folders | Its message leaves out the notes under a held folder. No second look starts when every note it would name is held, though its item can still show. |
| A second look in a vault kept in Git | The chat reads `git status` and `git diff`, which can show changes under a held folder. |
| A second look with a **Never send** tag | The tag is not judged there. |
| A second look or a project plan you leave unanswered | Its chat stays open until you answer the plan or select **Stop**. While it is open, VIKI starts no other chat of its own. Your own start can still use the second place. |
| A second look or a project plan that keeps failing | Each failed start shows a **Report** that notifies you, up to that kind's daily limit. Set its grant to **Ask first** or **Off** to stop them. |
| Stopping second looks or project plans | **Learning** counts a stopped one as started only, not as turned down. So stopping them does not pause that kind. Set its grant to **Ask first** or **Off**. |
| Changing the mode of a second look or a project plan yourself | VIKI counts a plan as approved when its chat leaves plan mode after you answer it. So a mode you pick by hand there counts too. |
| Closing VIKI Agent's window while a second look or a project plan waits | VIKI Agent answers that plan with **Keep planning** as the chat closes. **Learning** can count that second look or project plan as turned down. |
| An overdue task while **Prepare drafts** and **Plan next steps for my projects** are both on | One overdue task can get a chat that plans its work and another that checks whether it is done. |
| A vault VIKI cannot list in full, such as one with more than 5,000 notes | VIKI cannot tell when a project last changed, so it starts no plan for a stalled project there. |
| More than eight active projects | VIKI reads the eight active projects whose overview note changed most recently. So an older stalled project may get no plan. |
| Overdue tasks in more than three projects | VIKI looks at the three most overdue tasks it finds, so it checks at most three projects' tasks at a time. The others come up once those tasks are ticked or given new dates. |
| A note or task line in a project that **Never send** holds back by a tag | VIKI can still start a plan for that project. Its chat can read that note, as your own chat can. **Never send** holds the plan back only for a held folder inside the project or a held task VIKI lists. |
| Several stalled projects or sets of overdue tasks at once | VIKI starts one chat of its own at a time, in any vault. The next comes at a later check, once you answer or stop the first. |

### Message drafts

| Situation | What to expect |
|---|---|
| A chat that is not open in front of you, or not your own | VIKI offers message drafts only for the chat you have open, while a vault whose policy is **Ordinary** or **Protected** is active. It offers none for VIKI's chat, a chat VIKI started, a routine run, a Bot's or an agent's chat, or a chat in a code folder. |
| **Prepare drafts** set to **Off** | VIKI then offers no message drafts either. |
| A message draft you select but do not send | **Learning** counts a message draft as used once you select its button, sent or not. |
| A message draft VIKI decides not to show you | It still counts toward **Daily limit: message drafts** that day. |
| A chat Claude Code compacts on its own | VIKI offers a handoff once Claude Code reports the chat 70% full or more at the end of a reply. A chat compacted before that gets none. |
| A literature-note draft you send | It asks Claude Code to write a note for each page in the vault you have open, a protected vault included, under your own permission prompts. It also asks Claude Code to show you each note and wait for your go-ahead first, but VIKI itself holds no write back. |
| A literature-note draft, under a permission mode that accepts edits | No prompt stops a write. |

### Saved checks

> [!WARNING]
> With **Run my saved checks** set to **Allowed**, VIKI runs every saved check you have run in a
> trusted folder when the folder moves to a new commit. If a saved check changes anything outside
> its folder, such as a deploy, keep that command out of saved checks, or keep the grant on
> **Ask first**.

| Situation | What to expect |
|---|---|
| Preview commands on macOS and Linux | Previews run on Windows only. Checks and the terminal are available on all three OSes. |
| Saved checks in a worktree | **Run my saved checks** looks only at a trusted folder's own checkout, not its worktrees. |
| A new commit that changes a folder's own scripts, such as after a pull | With **Run my saved checks** set to **Allowed**, a saved check runs those changed scripts without asking. Keep the grant on **Ask first** to look first. |
| A check that makes a commit or moves the branch | Each commit it makes is a new commit for **Run my saved checks**. So VIKI can run the checks again, up to **Daily limit: new commits VIKI checks** a day. |
| A check run VIKI started, in the **Checks** tab | It looks like a run you started. Only its item in VIKI's launcher tells them apart. **Stop** there or **Stop all** cancels it while VIKI Agent stays open. |
| A saved-check item after **Stop** or **Stop all**, under **Allowed** | The item can keep saying VIKI can run the checks, though VIKI starts none on that commit while VIKI Agent stays open. Run them from the **Checks** tab, or set **Run my saved checks** to **Ask first** for **Run checks**. |
| A commit VIKI had not begun to check before **Stop** or **Stop all**, under **Allowed** | It can still start later. |
| **Run checks** says the checks are already running | It says so whenever none of the checks could start, also when one cannot start at all. The **Checks** tab shows why. |
| A saved-check item that still says its checks are running | VIKI looks at one folder each time it checks, and at each folder at most every five minutes. So the item can trail the **Checks** tab by five minutes or more. With many folders, or when VIKI checks less often, it can trail by about one check's interval for each folder. |
| A saved check you run many more times, such as in a worktree | VIKI judges a commit by each check's most recent kept runs. So after many later runs of the same check, it can offer or run that check again on a commit it already checked. |

### Inbox suggestions

| Situation | What to expect |
|---|---|
| An inbox suggestion run VIKI started | It is the run **Ask VIKI to suggest** starts. It sends your captures' names and opening text to Claude without a click, and denies any permission it asks for. Set **Suggest destinations for my inbox** to **Ask first** to start it yourself, or to **Off**. |
| A suggestion run VIKI starts | It shows no **Started by VIKI** chip. **Stop** and **Stop all** do not reach it. It reads only and ends by itself within five minutes. |
| A suggestion run going when VIKI Agent closes | After the restart it shows as failed, and VIKI does not run the suggestions again for that inbox until a capture is added or changed. **Ask VIKI to suggest** in the triage runs them again. |
| **Suggest destinations** just after the inbox changed | When a capture was added, changed or filed since VIKI last checked, the run sends nothing. The launcher says VIKI couldn't ask for those suggestions. The next check raises the new inbox, or use **Ask VIKI to suggest** in the triage. |
| A capture moved or synced into `00-inbox/` | When its file time is older than VIKI's last suggestion run, VIKI raises no inbox suggestion item for it until a capture gets a newer change. **Ask VIKI to suggest** in the triage runs it. |
| A capture added while VIKI suggests | The item for the earlier inbox closes. VIKI raises the new inbox ten minutes after its newest capture changed. It comes as a new state that counts toward **Daily limit: inbox suggestion items**. |
| A capture a suggestion run left without a destination | VIKI does not run the suggestions again for it until a capture in `00-inbox/` is added or changed. **Ask VIKI to suggest** in the triage runs them again. |
| An inbox suggestion item that still says VIKI is suggesting | VIKI sees that the run ended at its next check. So the item can trail **Triage inbox** by one check. |
| Any tag, a folder over a capture in `00-inbox/`, or a folder in `10-projects/` on **Never send** | VIKI raises no inbox suggestion item and starts no run. **Ask VIKI to suggest** in the triage still runs it. |
| Triaging from the inbox tile or the Vault overview | **Learning** counts an inbox suggestion item as used only when you select its own **Triage inbox** or **Suggest destinations**, or **Useful**. An item you leave unanswered counts toward neither. |

## Messaging

Messaging supports optional Mattermost, Slack and Discord bridges.

| Situation | What to expect |
|---|---|
| Choosing a messaging provider | Other messaging providers are unavailable. Other server or workspace versions, mobile apps, Mattermost reactions, typing and the edited status reply, and installing the Discord app are untested. |
| A messaging connection stops or requests renewal | The bot says why in its chat when it can. After cleanup and any cooldown, turn it on again with the switch beside its status in Messaging. If the card says to restart VIKI Agent, restart it before connecting again. The bridge does not reconnect automatically or replay offline messages. |
| Editing or deleting a Mattermost message right after sending it | Before the bot has read the message back from Mattermost, this can stop the bot. The bot then says why in the chat. Send a new message instead of editing. |
| Bot routine notices through Slack or Discord | Named Bot routine notices are Mattermost-only, with their separate consent for each launch. |

## Voice

Voice is optional, and it is less tested outside Windows. Test your microphone and installed voice
before depending on it.

| Situation | What to expect |
|---|---|
| Optional voice on a new machine | Dictation may take longer on first start, especially when a GPU driver builds its cache. |
| Choosing Natural voices as the speech engine | Natural voices need Windows x64, an NVIDIA graphics card with 4 GB of memory and driver 570.65 or later. |
| Natural voices for Japanese, Korean, German or Russian | Kokoro does not read them. Qwen3 or the system voice does. |
| Natural voices through Qwen3 on a laptop graphics card | Speech can take several times longer than it plays. **Settings › Chat** shows the speed measured on this PC. It warns when hands-free answers will come from the system voice. |
| Reading aloud or hands-free speaks through a Voicebox voice | Speech starts only once Voicebox has made the first sentence. The first reading after Voicebox itself starts also waits for it to load its model. Choose a smaller model size in **Settings › Chat** if it starts too late. |
| Reading aloud or hands-free speaks through a natural voice | The first reading after natural voices start waits for the voice server to start and load its model. |
| Hands-free speaks through a natural or Voicebox voice | When no audio arrives 15 seconds after the reply ends and a system voice speaks the reply's language, the system voice reads that answer. The hands-free strip says so. |
| The window is covered or minimized during hands-free | The answer is still read, then hands-free ends instead of reopening the microphone. Covering the window while hands-free is listening ends it at once. |
| The audio device changes while hands-free speaks | The answer keeps playing on the new default device. A reply's own Read pauses instead. |
| A card opens while **Speak while VIKI writes** is reading the final answer | The card stops the early speech. When the turn ends, the answer is read again from its start, so its first sentences can repeat. |

## VIKI Agent's own window and installers

These rows cover VIKI Agent's own window and installing an unsigned build.

| Situation | What to expect |
|---|---|
| Opening a code preview from VIKI Agent's own window | Code previews open in Edge, Chrome or Chromium. Without one of those installed, Preview reports that the browser is missing. |
| Spellcheck in VIKI Agent's own window | Spellcheck works only on macOS, using the system checker. Windows and Linux show no corrections there. |
| Opening VIKI Agent's own window on desktop Linux | Electron's sandbox needs its helper binary owned by root at mode 4755. After `npm --prefix desktop ci`, run once: `sudo chown root:root desktop/node_modules/electron/dist/chrome-sandbox` and `sudo chmod 4755 desktop/node_modules/electron/dist/chrome-sandbox`. |
| Installing an unsigned build on Windows | SmartScreen shows **More info**, then **Run anyway**. |
| Installing an unsigned build on macOS | Open System Settings › Privacy & Security and choose **Open Anyway**. macOS asks for keychain access again after each new unsigned build. |

See [Using VIKI Agent](using-the-app.md) for the affected controls and
[Security](security.md) for the task and permission boundaries.
