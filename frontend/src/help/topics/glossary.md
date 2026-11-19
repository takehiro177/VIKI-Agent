# Glossary

Plain definitions for the words VIKI Agent uses throughout the app.

## Terms

### Approvals
Where permission requests and questions from a running chat wait for you. Open it from
**Open Approvals** in **Needs you**, then choose **Allow once** or **Deny**.

### Archive
Hides a chat from the list without keeping its own transcript, so Claude Code's 30-day
default still decides how long the original is kept.

### Between chats

The right panel's read-only list of messages your chats received from other Claude Code sessions.

### Kanban
The notes directly in a vault's `kanban/` folder, each one a card. **Kanban** groups them by
status and lists blocked and review cards under **Needs you**, and VIKI's launcher lists the
cards that need you while **Kanban** is on.

### Bot
A saved profile wired to its own Claude Code conversation, so a routine can run it
and optionally deliver results to **Messaging**. **Bots** lists Chief, Ops, Research, Writer, Engineer, Desk and Checker under **Core team** in every registered vault, before you create any Bot. Their conversations work without the optional Kanban skills.

### Card
A note directly in `kanban/`. Its lowercase `status` (`todo`, `working`, `blocked`, `review`,
`done` or `cancelled`) says where it stands, and an `owner/<seat>` tag names the seat that should
pick it up; the card template also sets `type: card`.

### Chat
What you see as a tab in VIKI Agent's window. Opening an old session's chat resumes it; closing the tab leaves its record untouched.

### Context window
How much of a session's Claude Code context budget is used, shown as a percentage next to **Context window** at the foot of the right panel. Select it to open **Context & loadout** for the full report.

### Decision
VIKI Agent's proactive loop: it observes supported signals, evaluates attention and checks
what work may start under your grants. Its optional model supplies assessments;
local rules and settings determine the result.

### Decision model
TypeSafe's optional assessment model used by Decision. It returns probabilities, scores and
choices; Claude Code runs the resulting chats under its own permissions.

### Export
**Export Markdown**, or a JSON archive with attachments, from a chat's own tools; an imported archive is read-only.

### Goal
An outcome you choose. Decision reads active project overview titles from
`10-projects/<slug>/<slug>.md` as goal context when their front matter has `type: project`
and `status: active`. Chat and Bots can be used without a goal note.

### MCP server
A tool server Claude Code can call during a chat. Turn one on or off from the chat bar's **MCP servers** dialog.

### Permission mode
Controls whether Claude Code asks before acting — Manual, Auto, Accept edits or Plan.
Set the default under **Default mode** in **Settings** › **Chat**, or in a profile.

### Profile
A saved set of launch settings — provider, model, tools and MCP servers — you can start a session from.

### Proposal
A suggested change to a note in a protected vault, or to a skill, shown as a
diff. You decide whether it is applied.

### Routine
A job VIKI runs on a schedule while the app is open. Open **Routines** in the left
rail; a count shows when one needs you.

### Seat
A Bot's role on Kanban, such as `research`, named in its profile's instructions and in its
cards' `owner/<seat>` tag. A routine set to **Only when a card is waiting** skips a scheduled
time when no `todo` card waits for its seat. **Not started** on a seat means its conversation has no message yet.

### Session
A single conversation with Claude Code, the CLI — its turns, tool use and running
process all track to one session, whether or not a chat tab is open for it.

### Skill
Packaged instructions Claude Code can load for a task. See what is installed under **Browse Skills** in the right panel.

### Subagent
An agent a chat calls for one task, such as the checker in your vault's `.claude/agents/`. The right panel lists them under VIKI and subagents (your **Assistant name** in place of VIKI, if you changed it), and the header counts the ones running.

### Vault
Your plain-markdown second brain. An ordinary vault allows direct note saves; a vault with the
**Protected** policy only changes through a proposal you approve.

### Workspace
The vault, scratch folder, or code project VIKI Agent is currently pointed at. Switch to another one from the **App menu**.
