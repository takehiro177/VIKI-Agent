# Bots and messaging

Chat with VIKI from your phone through Mattermost, Slack or Discord, or give an agent profile its own Bot conversation.

## Messaging

Open **Messaging** in the left rail. It lists one row for each provider: Mattermost, Slack and Discord.

Each row shows the provider's state, **Not configured**, **Disabled**, **Connecting**, **Connected** or **Stopped**, and an On/Off switch. Select the provider's name to open its settings; an open row's source pill shows **Needs setup**, **Saved settings** or **Settings unreadable**.

1. Select **Set up** on the provider's row, or its name, and fill in the fields.
2. Select **Save changes**.
3. Turn the bot on with the switch beside its status (turn it on again if it stopped earlier).

Select **Refresh status** to read the current state without connecting. Select **Remove saved settings** to delete a provider's saved values from this computer.

## How a bridge works

Each bridge serves one configured owner, through a private direct message. It keeps its own conversation, running in auto mode, pinned to the workspace VIKI started in.

Claude Code's classifier reviews most actions automatically. Whatever still needs your say arrives as a card in that same direct message.

Pick an option by number with `!viki pick`, or answer a question in your own words with `!viki answer`. Send `!viki questions on` before you leave to also get questions from your desktop chats here, and `!viki questions off` to stop; it starts off again on every reconnect.

When a request from this conversation is the only request waiting, you can answer it in plain text: `allow` or `deny` for a permission, option numbers or your own words for a question. On Mattermost, a thumbs-up or thumbs-down reaction on a permission request allows it once or denies it. Requests wait until you answer them. A message you send while Claude is working is added to the running turn.

On Mattermost, the bot marks your message 👀, shows typing and keeps one status reply updated; `!viki status` shows the same line on every provider. When a bot stops, it says why in the chat when it can, and Messaging shows the reason.

Keep VIKI Agent running and your computer awake, or a connected bridge stops working. A fresh connection never replays messages that arrived while it was offline.

Slack and Discord only take plain new top-level text: no files, and no thread replies.

## Bots

A Bot gives one agent profile its own reopenable conversation, separate from your regular chats.
Bot chats stay out of the left sidebar's regular chat history. Open them from the right panel
or **Bots**. Starting the app lists your Bots without preparing conversations.

1. Open **Bots**. **Core team** lists Chief, Ops, Research, Writer, Engineer, Desk and Checker in every registered vault, including existing folders. They also appear in the right panel at startup. **Not started** means a conversation has no message yet.
2. Select a seat or its Bot, then **Open conversation**. On a seat with no Bot yet, this first makes the seat's profile and Bot. The conversation opens without sending anything. A vault made with **Start with VIKI** comes with those seven Bots already added. **Add missing Bots** under **Core team** adds every missing one in one step and sends nothing.
3. For any other profile, create it in **Bots** → **Bot settings**, then select **New Bot** in **Bots**, choose the profile and a workspace, and select **Create Bot**.

Each routine the Bot runs opens in its own separate chat. Select **Remove Bot** once you no longer need it; if it filled a seat, the seat shows **Not started** again. Opening that seat or sending to it in a shared thread creates its missing Bot.

### Chat or Chief?

Use a regular chat for questions, discussion or work you want to guide directly. Use Chief
when you want a task routed to a team seat. Open **Bots**, select Chief under **Core team**,
then choose **Open conversation**. Type your request and send it; Chief's instructions go to Claude Code as part of its system
prompt, and opening it alone sends nothing.

For example: “Have Research compare three backup tools, with sources and a recommendation.”
With the Kanban pack, Chief creates a card with an `owner/research` tag and a Done when
checklist. Chief routes the task; the Research routine does the specialist work. You can
also create cards yourself. Regular chats need no card.

### Does a card start a Bot?

Creating a card alone starts no specialist. Create, test and enable a routine for that Bot's
profile and vault, and choose **Only when a card is waiting** for its seat. Its schedule can
then check for work at the saved times. For pickup between those times, turn on
**Proactive VIKI** and choose **Start Bots' work** in **Decision**: **Ask first** offers
**Start** in the launcher; **Allowed** can start an eligible routine under its limits and holds.

Open **Kanban** in the left sidebar for a Kanban view of the notes directly in `kanban/`. Each
status has its own lane, color and glyph: **To do**, **Working**, **Blocked**, **Ready for review**
and **Done**, plus **Cancelled** once a card is cancelled. A card shows its owner's face and name,
when it last changed, and **Stopped moving** after a day in Working or three days in To do. A
blocked or review card also shows under **Needs you**, atop the page: answer it, or **Approve** or
send it back, with one click. The click saves at once and offers **Undo** for 10 seconds; after
that, it tries to send the card's Bot one message to continue, except after **Approve**, which
sends nothing. Select a card to open its note. Open a routine's
result with **Open session**; it is separate from the Bot's regular conversation.

Kanban is optional. New seat profiles include instructions for working without it:
Chief drafts a task brief, Ops summarizes the notes you name, and specialists handle your requests
in their own conversation and, in a shared Bot thread, hand finished work to Checker. Saved profiles
keep their instructions; use **Edit profile**
if they expect Kanban skills your vault does not have. Checker has its own Bot profile. The optional native checker subagent is available only
when installed in the vault.

## Shared Bot threads

Use **Bot threads** in the left sidebar for a shared group; **Task threads** lists one Bot's own
chats. The page puts **New Bot thread** first, followed by saved groups in newest-first order.
Each row shows the group title and phase, up to three enabled Bot faces and `+N` for additional
enabled participants. It shows the latest-message time when the group has a message.
**Needs your answer** appears when one of the group's Bot chats has a pending approval or question.
Select a row to open it.

Select **New Bot thread** to start a group. Choose up to 64 participants, then **Create thread**.
Chief and Checker are selected by default. Pick the first Bot with **To**, write the outcome and
press Enter or choose **Send to team**. Files you attach go to that Bot.
Bots hand off concise task details, criteria, artifact references and evidence. The log shows
their replies, tools, permissions and questions. Each Checker review starts in a fresh chat;
completion requires its PASS with criteria and proof. Turning Checker off leaves review pending.
Finished turns release their live slots. Each handoff starts a fresh chat with the task and
result packet; earlier source chats remain available.

While a Bot works, press Enter or choose **Steer** to send it a message right away: it reads it
after its current step, and its reply continues under your message. This works only on Claude Code
2.1.281, since the steer goes to that Bot's own chat; on any other version, newer ones
included, it is refused and your text goes back in the box. Steer takes text only: while
files are in the box it is off, and the files go with your next message after the run.

**Stop** in the message box, or Esc, stops the current Bot and further handoffs; a message still
waiting then runs next in that Bot's chat. Turning a participant off also stops
the handoff. A chain pauses after 24 turns, two handoffs without changed progress, a failed or
uncertain send, or a connection or workspace change. **Hide participants** folds away the panel.

Groups are saved and reopen paused. Their history loads when you open **Bot threads**.
Unsent text and files clear on refresh. Replies come from the existing source chats,
searching up to 16 older pages for a saved reply. If a source chat contains
an empty reply, its native history cannot establish whether that reply is complete.
Choose **Delete thread group…** beside the selected group's title to review a confirmation. It
deletes only the grouping: the Bot chats and their Claude Code transcripts stay. Unsent text and
files in that group are discarded. If you delete the selected group, the next group is selected,
or the previous group if there is no next one; with no groups left, use **New Bot thread**.
Forgetting a source chat or resetting **Send history** in **Data** also removes the affected grouping. Save
conflicts pause the thread; **Reload saved threads** waits for pending saves, then reloads the saved grouping and
discards unsent thread text and files. After a **Send history** reset, an idle group reloads automatically;
if it has unsent text or files, reload it explicitly to continue.
VIKI Agent saves 20 groups, each with up to 200 messages.

## Add Kanban to a vault you already have

To use the Kanban workflow, take these from a vault made with **Start with VIKI** and copy them into your vault without replacing existing files:

- `_templates/card.md`
- `_templates/daily.md` (including its `## Log` section)
- `.claude/agents/checker.md`
- `.claude/skills/kanban/`
- `.claude/skills/standup/`
- for your own chats, merge the Team section, `card` in the note schema and the `kanban/` row of its `AGENTS.md` into your vault's instructions; Bots skip them and take the card rules from `/kanban`

Create `kanban/`, `60-calendar/` and `system/` folders if missing. Create `system/log.md` with a
`# Operation log` heading if it does not exist; keep an existing log. Have Claude Code load the
merged instructions, for example with an `@AGENTS.md` import in `CLAUDE.md`. You can do this
by hand, or ask VIKI in a chat in that vault. Afterward, choose **Refresh Bots** in **Bots**.
Use `/kanban` for cards.

## Privacy

Everything sent through a bridge leaves this computer and passes through the service you chose. Review that service's own retention policy before you treat it as private.
