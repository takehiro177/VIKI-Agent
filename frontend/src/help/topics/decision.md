# Decision

VIKI watches for things that need you and raises them through a floating launcher; it does nothing until you turn it on.

## Turning it on

The page opens with the **Proactive VIKI** card and **How VIKI works**, then groups its controls
under **What VIKI notices**, **How VIKI decides**, **How VIKI tells you** and
**Work VIKI can start**, with **History and learning** last. While settings can't change, one notice
under the page title says so.

Decision does nothing until you turn it on. Open **Decision** in the left rail: while it's off, the
**Proactive VIKI** card says VIKI checks nothing and starts nothing until you turn it on, with one
button, **Turn proactive VIKI on**; VIKI's chat in the launcher still works. Once it's on, the card
shows its switch and the status line the launcher shows, with **Pause for 1 hour**,
**Pause until tomorrow** (**Resume** once paused) and **Focus for 1 hour** (**End focus** once on),
which the launcher's own menu repeats alongside **Open Decision**. Pause stops checks and starts;
Focus keeps VIKI watching but holds nudges and alerts.

**How VIKI works** sits under the card, open while **Proactive VIKI** is off and one line you can
open once it's on. Its steps are **Notices**, **Decides**, **Tells you** and
**Starts work, if you allow**, then Learns and Your data; each opens its settings.

Some of what VIKI does needs the Decision model: badges, nudges and alerts, plans and work chats for
due tasks, and Note links, Superseded notes and Sources to learn proposals. Everything else also
works with simple rules: listed items, looks, second looks, project plans, inbox suggestions, saved
checks, Bot starts and archiving ticked tasks. The page says so in **How VIKI works**, beside the
watch switches, in **How VIKI may reach you** and on the lines of **What VIKI may do**.

Decision uses model answers together with local scoring, timing rules and action grants. Open the
**Decision model** row, select **Get a key** to reach TypeSafe's site, paste the key in and select
**Save**; select **Test** to confirm it answers. The key stays sealed on this computer and is never
shown again. Even once it's on, VIKI calls the configured model sparingly — at most 10 requests a
minute, inside a daily spending cap you set — and never retries a failed call; without a key saved,
VIKI falls back to its own rules. **Spending** holds the **Daily cap**: once today's spend reaches
it, VIKI uses simple rules until tomorrow. **Advanced: version and comparison** holds **Version**
and **Compare with another model version**.

## Decision and Claude Code

Decision is VIKI Agent's proactive loop. It can ask TypeSafe's Decision model for assessments;
Claude Code runs the chats that do the work. VIKI sends the model a bounded state under
**What leaves this computer** and gets probabilities, scores and choices about relevance,
importance, presentation and useful work. VIKI applies its local scoring, timing, limits and
your grants to those assessments. The model does not create cards, edit notes or message Bots.

For example, a task to draft a launch announcement can be assessed against your active
website project. An answer favoring preparation can help VIKI choose a plan; the plan itself
is prepared in a Claude Code chat, within the grant and limits you set.

Bot starts use local Kanban and routine checks under **Start Bots' work**, without requiring
a model answer. A scheduled routine also runs independently of the model. Without a usable model
answer, Decision uses its rules to surface supported items.

## Where goals come from

VIKI reads active project overview titles as goal context. For a website project, use
`10-projects/website/website.md`, with a heading such as `# Launch the website` and front
matter containing `type: project` and `status: active`. The folder and overview basename
must match: `10-projects/<slug>/<slug>.md`. Keep due tasks in that folder's `tasks.md`.

VIKI inspects at most the 16 newest overview candidates and uses up to eight active projects.
Their titles supply goal context, subject to **Never send**; the overview body is not sent
as goal text. A starter project with `status: seed` must become `active` to qualify.
You choose the outcome and can ask Claude Code in a vault chat to help write the note.
Chat and Bots do not require a goal note or a separate goal form.

## Proactive examples

With proactivity enabled and unpaused, an app window open and the relevant grants and limits:

- A Research `todo` card can start its tested, enabled card-waiting routine under
  **Start Bots' work**. Review the run's chat and card; its regular Bot conversation is separate.
- An active project with no note changes for two weeks can get a plan-mode chat proposing
  three next actions under **Plan next steps for my projects**. Review the plan before it proceeds.
- Four inbox captures without destinations, whose newest change is ten minutes old and
  newer than the last suggestion run, can trigger **Suggest destinations for my inbox**.
  **Inbox** must be watched and no sharing exclusion can block the run. Review the suggested
  destinations in triage; the run moves no note.

**Ask first** offers the applicable start action; **Allowed** can begin eligible work.
A daily Ops standup is a separate scheduled routine, configured and tested in **Routines**.
It needs an awake host and an open app window, but does not need the model to choose its start time.

## The launcher

VIKI's launcher is the circle that floats over the window. Drag it anywhere, or move it with `Alt`+arrow keys and reset it with `Home`. Select it, or press `Alt`+`V` from anywhere in the window while no other dialog is open, to open VIKI's panel; `Esc` closes the panel again and returns focus to the launcher, or, when `Alt`+`V` opened it, to wherever you were. Drag the panel's outer edges to resize it; double-click an edge, or press `Enter` on it, to restore that side's default, and the size is kept. Its ring shows what's going on without a sound: a gentle brighten for new information, a number for waiting items, a pulsing ring for something it wants to flag, and a turning arc while it's running a chat for you. It still opens the panel, dimmed, whenever Decision is off or paused.

While plans that VIKI's chats prepared wait for your answer, the panel's top line says so, right after **Watching** — "1 plan waits for you", or "2 plans wait for you" once more than one do.

## Items

Open the panel to see what VIKI has noticed. Each item carries a kind chip — **For your information**, **Question**, **Suggestion**, **Ready for you** or **Report** — what it's about, a **Why now** line explaining why it surfaced now, evidence chips linking back to the note or chat behind it, and its own actions.

Every item also keeps the same feedback controls: **Not now** sets it aside, **Useful** records that it was right, and **Not useful** marks it wrong; its ⋯ menu's **Stop these** mutes that kind of item until you unmute it, with a reason if you give one — **Out of date**, **Already knew**, **Wrong topic** or **Bad timing**. Unmute it again from **How VIKI may reach you**. **Useful** closes the item, as **Not useful** does, unless the item still offers a way to start its work — its own start action, or one VIKI is about to start under **Allowed** — in which case it stays open so you or VIKI can still start it, and its button then reads **Marked useful**.

### What VIKI watches

**What VIKI watches** on the **Decision** page starts with **How often VIKI checks**:
**About every minute** (the default), **Every 5 minutes** or **Every 15 minutes**. Whichever you
pick, VIKI also checks soon after you switch chats or notes, open the launcher or come back to the
window. Under it, ten switches, each on by default, sit in three groups: **In your vault**,
**In the app** and **Improvements**. They also work with simple rules. Each one turns a group of
items on or off:

| Switch | What VIKI lists while it is on |
|---|---|
| **Tasks due** | Tasks that are due; with **Plan next steps for my projects**, a project's overdue tasks; with **Self-improvement**, proposals in **Approvals** to archive a project's ticked tasks |
| **Inbox** | Captures waiting in `00-inbox/`; with **Suggest destinations for my inbox**, inbox suggestions |
| **Vault changes** | Uncommitted changes in a vault kept in Git; with **Self-improvement**, proposals in **Approvals** to link two notes or mark an older note as replaced |
| **Approvals** | Proposals in **Approvals** and permission or question cards waiting in your chats, and a chat whose oldest card has waited five minutes or more |
| **Chats and runs** | Agent runs at work or ready for your review, and messages and MCP servers that need a look |
| **Routines** | Your routines, and your Bots' routine runs waiting for your OK or your answer; the Bot runs VIKI starts or offers follow **Start Bots' work** instead |
| **Self-improvement** | Offers to improve VIKI and your vault, below |
| **Kanban** | Kanban cards that need you or stopped moving |
| **Vault health** | Stopped saves, deleted notes about to expire, a nearly full store for deleted notes and memories to review |
| **Messaging** | Messaging bridges that stopped |

A switch also gates the work VIKI may start from what it lists, and the text beside it says so.
**Tasks due** off also stops plans and work chats for due tasks, plans for overdue tasks and
proposals to archive ticked tasks; plans and work chats for due tasks need the Decision model.
**Inbox** off also stops inbox suggestions. **Vault changes** off also stops Note links and
Superseded notes proposals, which need the Decision model. **Self-improvement** off stops skill
suggestions, Sources to learn, feedback reviews and vault upkeep proposals; Sources to learn, Note
links and Superseded notes need the Decision model. **Look on my behalf** has no watch switch.

The items for code folders, second looks, project plans, message drafts and saved checks come with their grants in **What VIKI may do**, as the sections below describe.

### Kanban cards

While **Kanban** is on in **What VIKI watches** (on by default), VIKI lists the cards directly in your vault's `kanban/` folder that need you: `status: blocked` as a **Question** and `status: review` as **Ready for you**. It reports, as a **Report**, a card left `working` for a day or `todo` for three days without a change. Each card item shows the card's title and Open card, which opens its note in the vault view. VIKI lists at most six cards that need you and three that stopped moving; a card already listed keeps its place, and a new one waits for a free place. A card that needs you stays in the list, with no badge or nudge, until 30 minutes after its last change; one that already shows a badge or nudge keeps it when the card is edited. VIKI reads the front matter of up to 128 cards each time it checks; a card it could not read keeps its item as it was.

### Bot runs

While **Routines** is on in **What VIKI watches**, VIKI also lists the routine runs of your Bots in the open vault. A run that waits for your OK or your answer shows at once as a **Question**, such as "Research needs your OK to continue.", with **Open session**, and closes once you answer. While **Start Bots' work** is not **Off**, a run VIKI started shows as **For your information** with **Open session** and **Stop**, a seat VIKI could start shows as a **Suggestion** with **Start**, and a **Report** says when a run VIKI started stopped, reached its time limit, could not start or moved none of its cards, or why VIKI held a start back: today's limit, the **Pause automatic starts at Claude plan usage** setting, quiet hours, a routine changed since its test, or two routines for one seat. A report held back by quiet hours or by the plan-usage setting also offers **Start**, so you can start that seat yourself. These items stay in the list with no badge or nudge; only the Decision model can make a waiting run louder.

### Vault, chats and bridges

VIKI also lists what it notices elsewhere in the app, each with the place that answers it. While **Vault health** is on in **What VIKI watches** (on by default), in a vault whose policy is **Ordinary** it reports, as a **Report**, saves stopped because a note operation didn't finish, a save left partway through for two minutes, a deleted note that expires within three days, and a nearly full store for deleted notes, which every vault shares, and it asks, as a **Question**, about a memory you approved on this computer that cites a note changed since. Each offers **Open vault**, where **Recovery** and **Memory** hold the answer; **Recovery** opens by itself while a save is stopped. While **Approvals** is on, a chat whose oldest card has waited five minutes or more shows as a **Question**, oldest first and at most six, except a Bot's routine run or a chat VIKI started, which have their own items. While **Chats and runs** is on, VIKI reports a message from before VIKI Agent last started, up to a day old when VIKI first checks, whose arrival at Claude Code is unknown, and a chat whose last context reading shows one MCP server's loaded tools taking a tenth or more of its context; both offer **Open chat**. While **Messaging** is on, it reports a messaging bridge that stopped, with **Open Messaging**. These items stay in the list with no badge or nudge, start no work, and close once what they report clears.

### Code folders

While **Look on my behalf** is not **Off** and a vault whose policy is **Ordinary** or **Protected** is open, VIKI also checks your trusted code folders, one at each check and each at most every half hour. A folder whose uncommitted changes to tracked files have not changed for a day or more shows as a **Suggestion**, most recently changed first and at most three, with **Open in Code** and, while the grant is **Ask first**, **Take a look**. New files not yet added to Git and deleted files don't count. The item closes once VIKI has started a chat for it, or once those changes are committed or change again.

### Second looks

While **Take a second look at my work** is not **Off** and a vault whose policy is **Ordinary** or **Protected** is open, VIKI watches the chat you have open. When a reply there finishes after a long task, or after you corrected VIKI in that chat, and the reply edited notes in the vault, it shows as a **Suggestion** with **Open chat** and, while the grant is **Ask first**, **Take a second look**; it closes once VIKI has started the second look, or once that chat gets a new reply — even while another chat is the one you have open. While a second look's plan waits for you, a **Suggestion** names what it found, a possible flaw, a better approach or a missing piece, with Open plan; it closes once you answer the plan. A second look that finds nothing now ends with one quiet **Report**, such as "Second look at your chat: nothing worth your time.", with **Open chat**; one that could not start or failed still shows a **Report**.

### Project plans

While **Plan next steps for my projects** is not **Off** and a vault whose policy is **Ordinary** or **Protected** is open, VIKI also watches your active projects: the overview notes `10-projects/<slug>/<slug>.md` whose front matter reads `type: project` and `status: active`, up to the eight whose overview changed most recently. A project none of whose notes has changed for two weeks or more shows as a **Suggestion**, most recently changed first and at most three, and while **Tasks due** is on in **What VIKI watches**, so does each project's `tasks.md` holding an open task three days or more past its due date, among the tasks VIKI lists. Each offers **Open note** and, while the grant is **Ask first**, Plan next steps or Check these tasks; it closes once VIKI has started its chat, or once the project changes or those tasks are ticked or given another date. A project or `tasks.md` that **Never send** holds back gets no item, and so does a project with a **Never send** folder inside it or a `tasks.md` with a listed task **Never send** holds back. While such a chat's plan waits for you, a **Suggestion** says so, with Open plan; it closes once you answer the plan. A project plan that could not start or failed shows a **Report**.

### Message drafts

While **Prepare drafts** is not **Off** and a vault whose policy is **Ordinary** or **Protected** is open, VIKI also offers messages for the chat you have open, when it is your own: not VIKI's chat, a chat VIKI started, a routine run, a Bot's or an agent's chat, or a chat in a code folder. When Claude Code reports that chat's context 70% full or more at the end of a reply, a **Suggestion** offers Draft a handoff, which puts a message in that chat's message box asking for the decisions, open questions and next steps; send it, then choose **New chat with this turn** on the reply to carry on in a fresh chat. When your last reply there fetched web pages, a **Suggestion** offers Draft literature notes, a message asking Claude Code to write a literature note for each page and to show you each note and wait for your go-ahead first; if that reply read no note in your vault, the message also asks it to search your notes on the same subject first. VIKI puts a draft in the message box only when you select it, and sends nothing. The handoff closes once that chat is no longer the one you have open or reports less than 70%, and the literature notes once that reply is no longer the last one in the chat you have open.

### Saved checks

While **Run my saved checks** is not **Off** and a vault whose policy is **Ordinary** is open, VIKI also looks at your trusted code folders that have saved checks, one folder each time it checks and each folder at most every five minutes. When such a folder has stayed for ten minutes at a commit that a saved check you have run in that folder before has not run on, a **Suggestion** says so, for at most three folders, with **Open in Code** and, while the grant is **Ask first**, **Run checks**. While those checks run, the item is **For your information** with **Stop**, and once you stop them, or press **Stop all** after VIKI began starting them, VIKI starts no further check on that commit by itself while VIKI Agent stays open; once they end, a **Report** says whether they passed, one failed, or they did not finish. A check VIKI could not start makes that report say they did not finish, unless one failed, and VIKI does not try it again on that commit while VIKI Agent stays open; the **Checks** tab still runs it. The item names the folder, not its checks; the Code workbench's **Checks** tab shows each check and its output. The item closes once the folder moves to another commit.

### Inbox suggestions

While **Suggest destinations for my inbox** is not **Off**, **Inbox** is on in **What VIKI watches** and a vault whose policy is **Ordinary** or **Protected** is open, VIKI also looks at the captures in `00-inbox/`. When the inbox has more than three captures or one older than three days, some captures have no suggested destination, no suggestion run has started since the newest capture changed, and that capture has not changed for ten minutes, a **Suggestion** says so, with **Triage inbox** and, while the grant is **Ask first**, **Suggest destinations**, which starts the same run as **Ask VIKI to suggest**. While that run goes, the item is **For your information**; once suggestions wait, it is **Ready for you**; when the run could not suggest, a **Report** says so. **Triage inbox** opens the triage, where you choose each capture's destination and send the filing draft; nothing in your vault changes until you do. A run that finds no destination it can use ends its item. VIKI starts one run for each state of the inbox, whatever its result, while VIKI Agent stays open; a capture added, changed or filed makes a new state, and the item closes.

## VIKI's chat

At the bottom of the panel is VIKI's own chat, a **Message VIKI** box kept separate from your regular chats. Send it a message to start a conversation; **New conversation** clears it so your next message starts fresh, and **Open in main window** continues that same chat there. With dictation available, the box also offers the microphone and the round **Hands-free** button (`Alt`+`T`), and your first words start the chat. While talking to VIKI, the first `Esc` ends the conversation and keeps the panel open; the next `Esc` closes the panel. Closing the panel, however it closes, ends a voice conversation still running, and opening it ends one running in your main chat instead. VIKI's replies here have no **Read aloud** button; use **Open in main window** to hear them.

## What leaves this computer

The **What leaves this computer** row controls what Decision may send. **Send level** picks how much: **Titles only**, **Titles and short excerpts (up to 200 characters)** — the default — or **Titles and sections (up to 2,000 characters)**. Add a folder ending in `/` or a tag starting with `#` (one written inline counts too) to **Never send**, and VIKI leaves anything under it out before anything is sent; it also tells you from how many of today's requests it held something back. Select **See the last state sent** to read the exact text, in **Context & loadout**'s **Decision** tab.

### By kind of item

These kinds of item follow their own rules for what reaches the Decision model. A chat VIKI starts is a Claude Code chat like your own, which sends what it reads to Anthropic as your chats do.

- Kanban cards: with any tag on **Never send**, or `kanban/` as a folder there, card items stay out of every request and VIKI's own rules list them.
- Bot runs: a Bot's item sends only its kind to the Decision model; the seat, the routine and its cards stay on this computer.
- Vault, chats and bridges: the items VIKI notices elsewhere in the app send only their kind to the Decision model, and for a waiting chat the kind of card and roughly how long it has waited; the notes, memories, chats, messages, servers and bridges stay on this computer.
- Code folders: a code folder's item sends only its kind to the Decision model, never the folder or its changes. A chat VIKI starts in the folder sends what it reads to Anthropic, as your chats do.
- Second looks: a second look's items send only their kind and your chat's title to the Decision model, never the notes or the chat. The second look's own message names your chat's title and the notes the reply edited, never the chat's text, and leaves out a note under a **Never send** folder; with every such note held back, no second look starts. A **Never send** tag is not judged for these notes, and in a vault kept in Git the chat reads `git status` and `git diff`, which can show changes under a held folder.
- Project plans: a project plan's items send only their kind to the Decision model, never the project, its notes or its tasks. The plan's own message names one note in your vault, the project's note or its `tasks.md`, never the note's text.
- Message drafts: a message draft's item sends only its kind to the Decision model, never the chat, its pages or its notes. The draft is fixed text with no note, web address or chat text in it; it stays on this computer until you send it, and then it goes to the selected model provider through Claude Code like any message you write.
- Saved checks: a saved-check item sends only its kind to the Decision model, never the folder, its commit or its checks. VIKI reads no check's name, command or output; a check it runs is your own command, run on this computer as **Run** in the **Checks** tab runs it.
- Inbox suggestions: an inbox suggestion item sends only its kind to the Decision model, never your captures, their names or how many there are. A suggestion run VIKI starts is the one **Ask VIKI to suggest** starts: it sends the name, the `captured` value and the first 600 characters of each capture in `00-inbox/`, up to 100 of them, and your project names to Claude, and it can read files in your vault with its read-only tools. With **Never send** holding any tag, a folder over a capture in `00-inbox/`, or a folder in `10-projects/` (the run also sends your project names), VIKI starts no such run; your own **Ask VIKI to suggest** still does. When the inbox changes after VIKI last listed it, a run started from VIKI's launcher stops before it sends anything, and the next check applies **Never send** to the new inbox.

## How VIKI may reach you

Open **How VIKI may reach you** to set how loud VIKI may be. A legend explains how an item can reach
you: **Listed** adds it to the launcher's list and the launcher brightens briefly, **Badge** puts a
number on the launcher, **Nudge** pulses the ring once, and **Alert** keeps the ring lit until you
open the panel. Badges, nudges and alerts need the Decision model; while VIKI uses simple rules, the
row says so and VIKI only lists items. **Nudges per hour** is the most nudges and alerts in any
hour, and 0 means none. **Alerts** can be on or off, and an alert also needs no quiet hours and a
task linked to an active project that is due within 2 hours or overdue.
**Tell me when I open the related chat or note** lets VIKI hold an item until you open the chat or
note it is about. **Quiet hours** hold nudges and alerts, and VIKI also holds its own looks, second
looks, project plans, saved checks and inbox runs, and Bots start only routines that stop at a
draft; the setting is shared with Routines. **Topics you stopped** lists the kinds you muted with
**Stop these**, each with a way to unmute it. Focus on the **Proactive VIKI** card holds nudges and
alerts for an hour, as quiet hours do.

## What VIKI may do

Open **What VIKI may do** to set its grants. One legend explains the three choices for every grant:
**Off** means VIKI doesn't start it, **Ask first** offers it as an action on its item in VIKI's
launcher, and **Allowed** lets VIKI start it at its next check, within the daily limit. Message
drafts are the exception: they wait for you to select them under **Ask first** and **Allowed**.

Each kind of work has its own line with its daily limit and today's count; the longer explanation
sits under **More about this**. **Running chats** comes first, only while something runs.
**Prepared for your review** holds **Prepare drafts**, **Look on my behalf**,
**Take a second look at my work**, **Plan next steps for my projects**,
**Suggest destinations for my inbox** and **Vault upkeep proposals**, with
**Daily limit: vault changes**; nothing changes until you approve. **Work that can make changes**
holds **Start chats to do work**, **Start Bots' work** (with **Let seats hand work to each other**
under it) and **Run my saved checks**. **Safety** holds
**Pause automatic starts at Claude plan usage**. A line also says when a grant cannot start work:
plans and work chats for due tasks, and Note links and Superseded notes proposals, need the Decision
model, and a line names the watch switch that stops it when that switch is off.

**Prepare drafts** lets VIKI put together a plan in a chat that waits for your approval before it
does anything, and **Start chats to do work** lets it open a chat under your normal permission
prompts to actually do something. The other grants below work the same way for their own kind of
work.

| Grant | What VIKI may start or offer | Default | Daily limit (default, from 0 to 10) | Vault policy |
|---|---|---|---|---|
| **Prepare drafts** | A plan for a due task, in a chat that waits for your approval; message drafts for the chat you have open | **Allowed** | **Daily limit: drafts** (5); **Daily limit: message drafts** (3) | Ordinary or Protected |
| **Start chats to do work** | A chat that does a due task under your normal permission prompts | **Ask first** | **Daily limit: chats** (3) | Ordinary |
| **Start Bots' work** | A seat's tested routine set to **Only when a card is waiting** | **Ask first** | **Daily limit: Bot runs VIKI starts** (4) | Ordinary |
| **Look on my behalf** | A plan-mode chat in a trusted code folder whose changes have sat for a day | **Allowed** | **Daily limit: looks VIKI starts** (3) | Ordinary or Protected |
| **Take a second look at my work** | A plan-mode chat after a long or corrected reply that edited notes | **Allowed** | **Daily limit: second looks VIKI starts** (3) | Ordinary or Protected |
| **Plan next steps for my projects** | A plan-mode chat for a stalled project or overdue tasks | **Allowed** | **Daily limit: project plans VIKI starts** (3) | Ordinary or Protected |
| **Suggest destinations for my inbox** | The inbox's read-only suggestion run | **Allowed** | **Daily limit: inbox suggestion items** (3) | Ordinary or Protected |
| **Run my saved checks** | The saved checks you have run in a trusted code folder, on its new commit | **Ask first** | **Daily limit: new commits VIKI checks** (3) | Ordinary |

Vault policy is the policy of the vault you have open, as Vault policy below explains. **Daily limit: vault changes** caps the vault-upkeep proposals in Self-improvement below, which wait in **Approvals** instead.

At most two chats that Decision starts run at once, each against its own daily limit, and VIKI starts none of its own while one is open, so one stays free for yours; a chat VIKI started shows a **Started by VIKI** chip in your session list, and can be stopped on its own with **Stop**, or all together with **Stop all**. **Stop all** also keeps VIKI from starting chats or Bot runs of its own for one check interval after you press it, as **How often VIKI checks** sets it. A chat VIKI started that finishes, fails or reaches its time limit while you have answered none of its cards closes, even while you are looking at it, and reopens from its transcript when you select it in your session list; one you answered a card in or stopped stays open. Once you stop a chat VIKI started, VIKI does not start work for that item again on its own, even after the item changes. Just before each chat, Bot run, saved check or inbox suggestion run it starts on its own, VIKI reads your settings, quiet hours and plan use again, and starts nothing while proactivity is off or paused or no app window is open, so a change you save while it checks holds that start. **VIKI starts a Bot only through its own tested routine, under Start Bots' work.** Apart from the notice of a Bot whose delivery you turned on, it does not message other chats or people or send to your phone.

While VIKI holds back its own start of an item under **Allowed** — another chat VIKI started is open, **Pause automatic starts at Claude plan usage** holds it, your quiet hours, or VIKI is waiting for a better moment — that item offers its start as under **Ask first** instead, and its **Why now** line names what is holding it and says you can start it yourself. At today's daily limit for that kind of work, which counts the ones you start yourself too, the item offers no start at all, and its **Why now** line says the limit is reached and that you can raise it here, in **What VIKI may do**.

### Pause automatic starts at Claude plan usage

**Pause automatic starts at Claude plan usage** picks **Off**, **50%**, **80%** (the default) or **95%**. While a plan window Claude Code reported, whose reset is still ahead, is at that share of your plan or more, VIKI starts no chat on its own; while your plan limit is reached it holds them until that limit resets, whatever you pick here. A chat it holds back is not stored and does not count against a daily limit, so VIKI starts it at its first check after the hold ends. While it holds them, **What VIKI may do** reads **Holding** and says until when. Until Claude Code first reports your plan use after VIKI Agent starts, VIKI holds every start this setting holds, whatever you pick here, **Off** included. Your own **Prepare a plan** and **Start a chat to do it** are not held.

### Start Bots' work

**Start Bots' work** picks **Off**, **Ask first** (the default) or **Allowed**. When one of your `todo` cards directly in the vault's `kanban/` folder waits for a seat (its `owner/<seat>` tag) and that seat has exactly one enabled routine set to **Only when a card is waiting**, tested at its current version, **Allowed** starts that routine at VIKI's next check and **Ask first** offers **Start** in the launcher. A card counts as yours when it last changed outside every routine run and every chat VIKI started; a card written during one gets **Start** instead, unless **Let seats hand work to each other** is on (off by default). After a run, VIKI starts the seat again only when one of its cards that is not `todo` changed during that run, or when you add or change one of its `todo` cards. After you **Stop** such a run, or press **Stop all**, **Allowed** starts that seat again only once you add or change one of its `todo` cards; **Ask first** keeps offering **Start**. A `todo` card whose file time is ahead of this computer's clock counts only once that time comes.

**Daily limit: Bot runs VIKI starts**, 4 by default and from 0 to 10, counts across workspaces, includes the runs you start with **Start** and leaves out a run that could not start. VIKI starts a Bot on its own only while no routine run is going, so a scheduled routine finds a free place; your own **Start** can run beside one other run. Nothing starts while two routine runs are going, while **Pause all** holds your routines, or outside a vault whose policy is **Ordinary**. **Pause automatic starts at Claude plan usage** holds these starts too, and in your quiet hours VIKI starts only routines set to **Stop at a draft**; a question in such a run still waits until its time limit. Neither holds your own **Start**. A run VIKI starts keeps its routine's prompt, time limit, confirmation setting and Bot notice, passes the checks a scheduled run passes, and reads **Started by VIKI** in its run history and your session list; **Stop** on its item or **Stop all** here stops it.

### Look on my behalf

**Look on my behalf** picks **Off**, **Ask first** or **Allowed** (the default). When one of your trusted code folders has uncommitted changes to tracked files that have not changed for a day or more, **Allowed** starts a chat in that folder at VIKI's next check, and **Ask first** offers **Take a look** in the launcher. VIKI starts the chat in plan mode and asks it to look the changes over and present what is half-done or risky as a plan; it waits for your approval before it changes anything. VIKI starts one such chat for each state of a folder's changes. **Daily limit: looks VIKI starts**, 3 by default and from 0 to 10, counts across workspaces and includes the chats you start with **Take a look**. VIKI checks code folders only while a vault whose policy is **Ordinary** or **Protected** is open. **Pause automatic starts at Claude plan usage** holds these chats too, and VIKI starts none in your quiet hours; neither holds your own **Take a look**.

### Take a second look at my work

**Take a second look at my work** picks **Off**, **Ask first** or **Allowed** (the default). When a reply in the chat you have open finishes a long task, or work you corrected in that chat, and edited notes in the vault, **Allowed** starts a chat in plan mode at VIKI's next check and **Ask first** offers **Take a second look**. VIKI asks that chat to read those notes and present at most one flaw, better approach or missing piece as a plan; it waits for your approval before it changes anything. VIKI asks the plan to lead with a heading naming the finding, why it matters and, in at most five numbered steps, what approving does, with its evidence under a closing **Details** heading that the card folds shut until you open it. **Approve** on that plan makes the change; **Keep planning** turns it down only if that chat then ends without your approval, so approving it later in the same chat still counts as approved. VIKI takes at most one second look at each reply, and none at a chat VIKI started, a routine run, a Bot's or an agent's chat, or a chat in a code folder. **Daily limit: second looks VIKI starts**, 3 by default and from 0 to 10, counts across workspaces and includes the chats you start with **Take a second look**. **Pause automatic starts at Claude plan usage** holds these chats too, and VIKI starts none in your quiet hours; neither holds your own **Take a second look**.

### Plan next steps for my projects

**Plan next steps for my projects** picks **Off**, **Ask first** or **Allowed** (the default). When nothing in one of your active projects has changed for two weeks, or a project's tasks are three days or more overdue, **Allowed** starts a chat in plan mode in the vault at VIKI's next check and **Ask first** offers Plan next steps or Check these tasks. VIKI asks that chat to read the project's note or its `tasks.md` and to present, as a plan, three next actions for the project, or ticks for the overdue tasks that look done and new dates for the rest; it waits for your approval before it changes anything. **Approve** on that plan makes the change; **Keep planning** turns it down only if that chat then ends without your approval, so approving it later in the same chat still counts as approved. VIKI starts one such chat each time a project stalls and for each set of overdue tasks, and while one is open VIKI starts no other chat of its own, in any vault, until you answer or stop it. **Daily limit: project plans VIKI starts**, 3 by default and from 0 to 10, counts across workspaces and includes the chats you start from these items. **Pause automatic starts at Claude plan usage** holds these chats too, and VIKI starts none in your quiet hours; neither holds your own start.

### Prepare drafts and message drafts

**Prepare drafts** also lets VIKI offer message drafts for the chat you have open, as Message drafts under Items above describes: under **Ask first** and **Allowed** alike a draft waits in VIKI's launcher until you select it, and **Off** offers none. **Daily limit: message drafts**, 3 by default and from 0 to 10, counts each new draft VIKI raises in a day, across workspaces, including one it decides not to show you, and never drops a draft already raised; a kind you turned off with **Stop these** is not raised at all. Message drafts start no chat, so **Pause automatic starts at Claude plan usage** does not apply to them.

### Run my saved checks

**Run my saved checks** picks **Off**, **Ask first** (the default) or **Allowed**. When a trusted code folder stays for ten minutes at a commit that some of its saved checks have not run on, including a commit a pull brought in, **Allowed** runs, the next time VIKI checks, each of those checks you have run in that folder before, as **Run** in the **Checks** tab does, and **Ask first** offers **Run checks** in the launcher. Each check is your own command, run with your account's rights in the folder itself: it can change files and use the network. VIKI runs a check at most once on each commit, whatever its result, does not run a check you have not run in that folder before, and runs none while a merge, rebase, cherry-pick or revert is under way.

**Daily limit: new commits VIKI checks**, 3 by default and from 0 to 10, counts each folder commit VIKI raises in a day, across workspaces, including one it decides not to show you and the ones you run with **Run checks**; a kind you turned off with **Stop these** is not raised at all. VIKI runs or offers these checks only while a vault whose policy is **Ordinary** is open. VIKI runs none in your quiet hours; **Pause automatic starts at Claude plan usage** does not apply, since checks use no plan, and neither holds your own **Run checks**. **Stop** on the item, or **Stop all** here, cancels the check runs started from VIKI while VIKI Agent stays open, and VIKI starts no more checks of its own on that commit, as Saved checks under Items above describes.

### Suggest destinations for my inbox

**Suggest destinations for my inbox** picks **Off**, **Ask first** or **Allowed** (the default). When an inbox item is due, as Inbox suggestions under Items above describes, **Allowed** starts the inbox's suggestion run at VIKI's next check and **Ask first** offers **Suggest destinations** in the launcher. The run is the one **Ask VIKI to suggest** starts: one reply from Claude Code with only Read, Glob and Grep and no MCP server, ended after five minutes, and that run denies any permission it asks for; it changes nothing, and its suggestions are kept until VIKI Agent closes.

**Daily limit: inbox suggestion items**, 3 by default and from 0 to 10, counts each inbox state VIKI raises in a day, across workspaces, including one it decides not to show you and the ones you start with **Suggest destinations**; a kind you turned off with **Stop these** is not raised at all. VIKI starts none in your quiet hours, and **Pause automatic starts at Claude plan usage** holds these runs too; neither holds your own **Suggest destinations** or **Ask VIKI to suggest**. VIKI does not start the run again for the same inbox, even when it failed, and after VIKI Agent restarts it starts none for an inbox it raised before, whose item then offers **Suggest destinations** under **Allowed** too; **Ask VIKI to suggest** in the triage runs it again as well. A capture moved or synced into `00-inbox/` with a file time older than the last run waits for the next newer change. A run VIKI starts shows no **Started by VIKI** chip, and **Stop** and **Stop all** do not reach it; it ends by itself within five minutes.

### Vault policy

In a vault whose policy is **Protected**, VIKI only prepares plans: it starts and offers no work chat, whatever **Start chats to do work** says. In a vault whose policy is **Read-only (existing folder)**, it starts and offers no plan and no work chat. Self-improvement drafts that wait for you to send them still come in every vault, and message drafts in a vault whose policy is **Ordinary** or **Protected**. VIKI reads the vault's policy at each check, so a change applies from the next one, and your own **Prepare a plan** or **Start a chat to do it** reads it when you select it.

## Learning

Open **Learning details and statistics** for the numeric breakdowns. The main section explains how
to give feedback with **Useful**, **Not useful** and **Stop these**. The optional comparison with
another model version sits under **Decision model** › **Advanced: version and comparison**. Saving
the Learning switch or comparison choice also refreshes the status and comparison results.

Open **Learning**, between **How VIKI is doing** and **Data**, to see what VIKI has learned from your answers — Useful, Not useful, Stop these, opening or acting on an item, and the weekly check below — and turn it on or off with its switch. Either way, this only changes how loud and when VIKI speaks up, never your limits: quiet hours, nudges per hour, alerts, muted topics and what VIKI may do stay as you set them. From the first decisions, Learning tunes only when and how loudly VIKI speaks up; once it has 20 labels, including at least three positive and three negative, it also learns which questions and topics matter to you. Select **Show all numbers** for a table of every number next to where it started, **How well it matches you** for how VIKI's confidence compares with your own feedback, and **Each question** for that same comparison broken down by what VIKI asked.

### How VIKI's own work went

**Learning** also shows how the work VIKI starts or offers on its own went, each over the last 30 days and since your last **Reset**:

- **Second looks** shows, for a long task and for a correction, how many second looks VIKI started, how many you approved or turned down, the reasons you gave with **Not useful** on their items, and how many found nothing. A second look that finds nothing no longer counts toward pausing that kind, though it still shows here as found nothing. Once 10 of one kind of second look were approved or turned down, VIKI stops taking that kind while fewer than 1 in 4 of those was approved, says so, and offers **Try again**. VIKI also stops raising a kind of finding you turned down 5 times in a row, and offers **Show these again**.
- **Project plans** shows, for a stalled project and for overdue tasks, how many plans VIKI started, how many you approved or turned down, the reasons you gave with **Not useful** on their items, and how many found nothing to propose. Once 10 of one kind were answered or found nothing, VIKI stops that kind while fewer than 1 in 4 is approved, says so, and offers **Try again**.
- **Message drafts** shows, for a nearly full chat and for fetched pages, how many drafts VIKI's launcher showed you, how many you used or turned down, the reasons you gave with **Not useful**, and how many closed before you used or answered them. Selecting a draft's button counts as using it. Once 10 of one kind were used, turned down or closed, VIKI stops offering that kind while fewer than 1 in 4 is used, says so, and offers **Try again**.
- **Saved checks** shows how many new-commit items VIKI's launcher showed you, how many you used, by selecting **Run checks** or **Useful**, or turned down, and the reasons you gave with **Not useful**. Once 10 were used or turned down, VIKI stops raising them while fewer than 1 in 4 is used, says so, and offers **Try again**; an item you leave unanswered counts toward neither.
- **Inbox suggestions** shows how many inbox suggestion items VIKI's launcher showed you, how many you used, by selecting **Triage inbox** or **Suggest destinations** on the item or **Useful**, or turned down, the reasons you gave with **Not useful**, and how many runs found no destination VIKI could use. Once 10 were used, turned down or found nothing, VIKI stops raising them while fewer than 1 in 4 is used, says so, and offers **Try again**; an item you leave unanswered counts toward neither.

None of these pauses changes your grant, daily limit or quiet hours, and **Delete all decision history** forgets these counts. Deleting decisions or suggestions recounts them.

### Comparison and reset

**Compare with another model version**, under **Decision model** ›
**Advanced: version and comparison**, is off until you turn it on; set to **Pinned**,
**Latest stable** or **Preview**, every new decision is also asked of that version, for comparison
only: its answers never change what VIKI decides. Each one is a second paid call within the same
daily cap and 10-a-minute limit, and if TypeSafe rejects your key or says it is busy, VIKI pauses
all its requests, live ones included. Select **Reset what VIKI learned** to start its numbers over
from scratch; your decisions and labels stay.

## Weekly check

While some of last week's held-back items are still unlabelled, a **Weekly check** card appears in the launcher panel, naming how many things VIKI held back and asking whether that was right. Select **Check** to open it inside **How VIKI is doing**, or **Skip this week** to dismiss the card until next week.

There, **Weekly check** lists up to 10 of last week's held-back items, picked at random, or says there's nothing to check when none were held back. Each gets the same two choices: **Right to hold** confirms VIKI was right to stay quiet, and **Should have told me** marks it as a miss.

**How VIKI is doing** is mostly a record to read: **Last 30 days** counts what VIKI showed you and how you answered. **See every decision in Data** opens the full decision list, and **See how you answered, day by day** opens your answer history. Only two parts need you. The **Weekly check** asks once a week; the row's status shows how many items are left to check, or **Nothing to do now**. **Report a missing suggestion**, under **Something VIKI missed**, is optional at any time: it counts as one **Should have told me** in the 30-day numbers and does not record what was missed. While **Learning** is on, it uses your weekly check answers to tune when and how loudly VIKI speaks up.

## Self-improvement

VIKI also watches for ways to improve itself, and raises what it finds through the same launcher as everything else: an offer to turn a chat into a skill, one of your own skills that fits the chat you're in, a captured source that may be worth a literature note, a suggestion to run a retro once several items are marked Not useful, better wording for one of Decision's own questions, and three kinds of vault upkeep — linking two notes, marking an older note as replaced by a newer one, and archiving a project's ticked tasks. The first five only prepare a draft in a chat, and nothing is sent until you press Send yourself; a skill that fits has one action, Use this skill, putting its `/` command there instead, offered at most once a chat and never for one already used there — **Stop these** on it mutes only **Skill suggestions**. The three vault-upkeep kinds skip that step and land straight in **Approvals** instead, as a **Link proposal** or **Upkeep proposal** row — only once you select **Approve** there does VIKI make a draft of that one edit, and nothing in the vault changes until you send it. The three vault-upkeep kinds come only from a vault whose policy is **Ordinary**; a vault whose policy is **Protected** or **Read-only (existing folder)** gets no new ones, and one filed while the vault was **Ordinary** stays in **Approvals**. The **Self-improvement** switch, in **What VIKI watches**, turns every kind off together and is on by default; **Stop these** on any one item mutes just that kind, the same as for any other item; the vault-upkeep kinds have no launcher item, so **Daily limit: vault changes** at 0 stops them. A skill offer's reason shows as that item's **Why now** line in the launcher, not under the chat reply.

**Daily limit: vault changes**, a setting beside the drafts and chats limits in **What VIKI may do**, caps the three vault-upkeep kinds together: 0 to 10 a day, 3 by default, and 0 turns vault-change proposals off. It's one count across every workspace, not per workspace. Whatever these settings say, each request to the Decision model also carries the titles of notes changed since yesterday and of items Decision showed you in the last day, plus the items you most recently marked mute or wrong and your recent feedback — titles only, never a note's own text, and **Never send** applies to them as to everything else.

## Better questions

Decision's own questions can be reworded too. The better-wording item's draft asks, in a chat, for new wording for one question; once a reply gives it in the shape asked for, that reply offers **Propose this wording**, sending it to Approvals. Once proposed, every fresh decision also asks the proposed wording once, in the shadow, as a second paid call within the same daily cap and 10-a-minute limit — its answers never change what VIKI decides, and only one wording is tried at a time. From Approvals, **Approve** switches new decisions to it and asks every item still open one more time under the new wording; once a wording is in use, the same row offers **Undo** to go back to the one before it. **Delete all decision history** forgets the trial numbers behind this comparison, not the wording itself.

## Data

Open **Data** to see **What VIKI keeps** for this workspace. Each card shows its count, oldest and newest date, and limit: **Decision history** has decisions and reports, newest 10,000 from up to 365 days; **Suggestions** has up to 50 open and keeps closed ones for 30 days; **Model calls** shows the newest 20; **Chats VIKI started** has no limit and stays so VIKI does not start the same work twice (you cannot delete them here, and transcripts stay in Claude Code); **Proposals** has up to 50 wordings and 50 vault changes, acted on in **Approvals**; **Learned preferences** reset together with **Reset what VIKI learned**, and topics can be unmuted in **Topics you stopped**; **Decision model key** is one key for every workspace, changed in **Decision model**.

**Activity** covers **Last 30 days**, **Last 90 days** or **Last 365 days**. Its chart has a column per day, or a week for 365 days; select **Show as table** to see the values. **How you answered** covers the same days. **Topics** includes every stored decision.

**Browse and delete** lists decisions, suggestions or model calls. Filter decisions and suggestions, then show 50 rows at a time with **Show 50 more**; model calls appear all at once. Open **Details** for a row. Model call details show its model version and answers, and **Open in Context** shows what VIKI sent in each call.

Select up to 50 rows with **Delete selected**, or delete every matching decision or suggestion. The count is fixed when **Data** loads. Rows still in use stay and explain why: a decision whose suggestion is open, or a suggestion that is open or held by a chat VIKI started. Each confirmation lists what else changes and what stays. Deleting decisions waits for VIKI's current check to stop and resets what VIKI learned. **Delete all decision history** removes decisions and reports, every suggestion, every model call and what VIKI learned.

That confirmation also explains that paused kinds are counted again; today's message drafts, saved checks and inbox suggestions get their counts back; today's looks, second looks and project plans count toward **Daily limit: drafts** until tomorrow; comparisons lose their numbers; an approved or rejected vault change teaches VIKI nothing; and self-improvement suggestions are worked out again. Settings, grants, **Topics you stopped**, chats VIKI started and their transcripts, proposals, wording in use, today's call count and spending stay. A deleted item marked **Not useful** can return, and VIKI may start its work again.
