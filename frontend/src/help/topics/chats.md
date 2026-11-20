# Chats and tabs

Chats are Claude Code conversations you start, answer and organize from the left rail.

## Start a chat

Select **New session** at the top of the left rail, or press `Alt`+`N` (also `Ctrl`+`Shift`+`O`, or `⌘`+`Shift`+`O` on macOS). Type your message and press `Enter`; press `Shift`+`Enter` for a new line. Type `/` to pick a command or skill. Press `↑` in an empty message box to recall your last message. You can also paste or drop an image onto the chat bar. VIKI Agent runs up to 30 sessions at once by default; chats, routines and Bots all count. To change that, set **Sessions running at once** in Settings › Chat to any whole number from 1 to 999. At the limit, starting another chat lists the running ones so you can close one, and **Change limit** opens that setting.

## Send, steer and stop

VIKI's reply streams into the conversation as it is written; **Stop** or `Esc` interrupts it. While a reply runs, **Steer** or `Enter` sends your next message right away: it waits under the conversation until Claude reads it after its current step, then shows as your message. A message still waiting when you stop runs next. A slash command isn't steered: it stays in the box for you to send when the reply ends, and so does any message in **Message VIKI**. A profile chat steers on Claude Code 2.1.281; on another version its message stays in the box with a note. After an error, a stop, or a restart, check **Drafts and send history** before sending again. The strip above the message box says what VIKI is doing. With chats side by side, a pane without the message box shows its own **Stop** while its chat runs.

## Reply actions

**Copy** and **Read aloud** sit on each reply. Under a finished turn, **More actions** (⋯) offers **Quote into draft**, **New chat with this turn**, **Edit in a new chat** and **Select for export**.

## Tabs and panes

Tabs sit in the conversation header. Its menu (⋯) offers **Split right**, **Split down**, **Move to pane**, **Close pane**, and **New window**. Drag a tab or session onto a pane's edge to split it, or onto its middle to move it. VIKI Agent supports up to four panes side by side. Below 1280 px wide, one pane shows every tab; your layout returns after a restart.

## Find a past chat

Press **Search sessions** (`Ctrl`+`K`, or `⌘`+`K` on macOS) to search session titles, your messages and finished replies — not tool output, and not **Other**. Use the arrow keys to move through results, and `Enter` to open one. Opening an old session resumes its conversation.

## Tidy the list

A session's row menu offers **Rename…**, **Pin**, **Color label** and **Mark as unread**. It also offers **Fork**, **Select turns to export**, **Archive** and **Close session**. **Archive** hides a chat from the list but keeps no transcript. **Close session** also closes its tab. The left rail's **Archived** count shows how many chats you have archived.

## Between chats

The right panel's **Between chats** shows the messages your recent chats received from other Claude Code sessions, newest first. Select one to open the chat it went to. It only reads; it sends nothing.

## Good to know

When you close the last window, a turn that is still running may finish first, for up to five minutes. The chat bar's **MCP** button opens **MCP servers** for the current chat's connections.
