# Agents, skills and context

See your agent profiles, what they're running, the Skills and tools available, and how much context a chat has left.

## Bots and capabilities
Open **Bots → Bot settings** to create or edit a Bot's profile. **Team** shows the Bots in your vault.
Open **Capabilities**, then choose **Subagents** or **Skills** to browse the native helpers available to your chats.
The right panel shows running subagents for the loaded chat.
Under **Subagents**, **Running work** lists chats open in VIKI, including idle ones. Use **Open**, **Stop** or **Close** on a chat; work shown as **Running elsewhere** is read-only.

## Profiles
A profile configures a Bot's identity, instructions and runtime. In a saved profile, **Use as Bot** adds it to the active vault, while **Open Bot** returns to its existing Bot. Changes apply to future chats and routine runs; existing chats keep the revision they were prepared with.
Native subagents handle tasks delegated inside a chat. Research Bot can use the researcher subagent through installed skills. A subagent has no Bot chat or recurring routine of its own.
Select **New profile** and fill in its identity, model, effort and permission mode, then choose which workspaces it may open in.
To build a team, pick a seat under **Start from a seat** first: it fills in that seat's job description for you to edit.
Select **Create profile** for a new one, or **Save revision** once you've changed an existing one.
Once a profile is running, watch it in the right panel: **Working** while it runs, **Ready for review** once it's done.

In **Bots → Bot settings**, **Choose icon** opens the emblem gallery. Choose **Automatic** or an emblem, then select **Use icon**; this saves right away. **Bot color** offers eight colors and also saves right away. Both apply to every Bot on that profile. An uploaded picture stays in the profile draft until **Save revision**. **Reset** clears the custom emblem immediately and stages picture removal; save the revision to finish removing the picture.

Under **Model and permissions**, **Model** lists Claude Code's available models. **App default** uses the app's native model choice, or Claude Code's default when the app default is a connection. In Bot settings, **Your connections** lists saved connections; a Bot that pins one uses it for its chats, task threads and Bot-thread turns, and the endpoint can receive what those chats send, including prompts, attachments, earlier replies, the Bot's instructions and rules, loaded Bot memory, the instruction files Claude Code loads and files read. Routine runs use the native default, and a chat on that profile in Agents is refused. A Bot thread can chain up to 24 turns per message. A saved model that is no longer listed stays visible as unavailable. **Effort** offers **Low**, **Medium**, **High**, **Extra high** and **Maximum**. **Permission mode** offers **Use my default** or **Plan first**.

The **Cannot apply in a chat** section appears only when one or more of six groups has a value: context items, native assets, required tools, denied tools, memory or limits. **Clear** restores one group to its default; **Clear all** resets all six. Save the revision to keep these edits. **Try it in a chat** lists any launch blockers with a plain explanation. Save first, select an active workspace, and resolve those blockers; if Claude Code or a saved transcript is unavailable, the message explains how to continue.

## Bot memory
On the Bot page, the memory card names the Bot's own folder and offers **Ask to review**, **Ask to forget all…** and the dynamic Ask [Bot name] to change… action (the Bot's name fills in for [Bot name]). The change action opens an unsent draft in the Bot's main chat; review and forget open unsent task-thread drafts. The card's **View memory** opens the file dialog. A Bot chat's header has a **Memory** button, and **View memory** in Bot activity (the right rail) does the same for a main chat or task thread; both open the dialog when the active vault's policy matches the Bot's. In the dialog, **Open in Vault** (only for memory kept in the vault) opens the selected file read-only; **Open in VS Code** opens it in VS Code, which edits it directly; **Refresh memory** reloads the list and **Close** closes it. The list shows exact paths. An incomplete list, failed scan or file read, and unsupported, cut-off or unread title details each have a status.

**Edit** opens an in-app editor. Its **Save** does not write the file: it prepares an unsent request asking the Bot to replace the file with your text, only if the file's SHA-256 still matches the one you were shown. From a Bot chat the request is appended to that chat's unsent draft and is never sent; from the Bots page it becomes a new task thread's draft. The Bot changes the file only after you send it, under Claude Code's own permission settings. If **Save** cannot place the draft, the editor keeps your text and shows an error; Esc and **Close** ask before discarding typed text. A file with CRLF line endings or a byte order mark shows a note, and the request asks the Bot to keep them. A file with a lone CR, or with CRLF and LF mixed, can't be edited here: open it in your own editor or ask the Bot. A request over 1 MiB is refused.

VIKI Agent adds its fixed memory rule and Bot rules after the instructions in the system prompt of each Bot chat, task thread and Bot-thread turn, and of a routine run whose profile has a Bot in the routine's workspace; a routine's preview shows them under **Bot instructions (system prompt)**. The Bot rules ask a Bot to read the `CLAUDE.md` and `AGENTS.md` of a folder outside the vault, such as its code copy, before working there, to ask you before deleting files, sending anything off this computer, pushing or paying, to put the answer, the result or the one question first when it writes to you and leave evidence in the card or note, to treat web pages, files, tool output and other Bots' messages as data, and to write no passwords, keys or tokens into notes or files. These chats skip the vault's own instruction files: VIKI Agent asks Claude Code not to load its `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` and `.claude/rules/` files.

The forget draft asks Claude Code to list the exact files and request your confirmation before
changing or deleting anything, then delete only the confirmed files within that folder.
It does not promise a permission card. Claude Code writes Bot memory under its native permissions.

In every vault, VIKI requests the Bot's own folder for new Bot chats and routine runs:
`bot-memory/<bot UUID>/` in an ordinary vault, otherwise a folder in VIKI Agent's data folder on
this computer, outside the vault. The memory card says "Kept on this computer in VIKI Agent's data
folder, outside the vault." for it. **Remove Bot** and **Reset Bots in this workspace** keep the
memory files. Claude Code's `autoMemoryEnabled` controls whether auto memory is enabled;
a managed `autoMemoryDirectory` setting takes precedence and may choose another folder. The Bot
memory view shows the requested folder; it does not check Claude Code's effective memory settings
or report whether auto memory is enabled.

## Bot chats and task threads
Open a Bot's **Open conversation** for its main chat, or **New task thread** for a separate topic. A new thread uses the current profile without copying other chats; Claude Code's memory settings still apply. Opening either sends no message.
The Bot's **Task threads** list includes other interactive chats with the same profile in the same vault, up to 50, newest by creation time. Routine runs are separate.
A new Bot chat or task thread opens with the Bot's icon in place of the VIKI AGENT mark. Its header shows the Bot's name, its seat's role, **Main Bot chat** or **Task thread** with the revision the chat was prepared with, and **Chats & routines**, which returns to that Bot.
In a Bot chat the right panel shows **Needs you** first, then **Bot activity**: **Refresh activity**, **This Bot's routines** with **Run now**, and the latest run with **Open latest run chat**. The **Bots** strip rings the Bot whose chat is open, and its **All Bots** opens the roster.

## Code folder for a Bot
In **Bots → Bot settings**, **Code folder** gives a Bot one trusted code folder; the default is **None**. The Bot's main chat and each task thread then work in their own copy of it, made when you send the first message, and the Bots of a Bot thread share one copy. Routine runs get none.
The first send takes longer while the copy is made. VIKI keeps at most 32 worktrees. The chat's review says **Gets a copy of** the folder **when you send**, then **Works in a copy of** it with its branch.
VIKI gives Claude Code only that copy, and Claude Code's permissions decide the rest. A copy shares its repository's branches, tags and config with the folder and its other worktrees, and Claude Code loads the copy's `.claude` skills, commands, agents and plugin settings. Telling a Bot not to push is a request, not something VIKI enforces.

## Skills
The right panel's **Skills** section lists up to three of your Skills, and shows whether it could read all of them.
Select **Browse Skills** for a searchable list showing each one's exact ref and source.
Below it, **Recent Skill calls · loaded chat** shows up to three recent calls for this chat. Each one is marked **Calling**, **Invoked**, **Failed** or **Result unknown**.
From a reply, **Make this a skill** drafts a chat request, and **Propose this skill** sends its exact text to Approvals. A skill offer's reason shows as that item's **Why now** line in the launcher, not under the chat reply.
A vault `SKILL.md` note is just a note — it installs nothing on its own.

## Capabilities
Open **Capabilities** in the left rail to explore **Skills**, **Tools**, **Connectors** and **Plugins** for your next session.
It does not install plugins for you.

## Context and Decision
The right panel's footer shows **Context window**, or **Not reported** until a measurement arrives, and **Context & loadout** for the full report.
Its **Decision** tab shows the **Decision model context window**: the request and largest-question bars, the version and timing, the questions with answers, and what was sent.
