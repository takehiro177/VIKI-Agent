# Decision

VIKI watches for things that need you and raises them through a floating launcher; it does nothing until you turn it on.

## Turning it on

The page opens with the **Proactive VIKI** card and **How VIKI works**, then groups its controls under **What VIKI notices**, **How VIKI decides**, **How VIKI tells you** and **Work VIKI can start**, with **History and learning** last. While settings can't change, one notice under the page title says so.

Open **Decision** in the left rail and select **Turn proactive VIKI on**; until then VIKI checks nothing and starts nothing, though VIKI's chat in the launcher still works. Once it's on, the card shows its switch and the launcher's status line, with **Pause for 1 hour**, **Pause until tomorrow** (**Resume** once paused) and **Focus for 1 hour** (**End focus** once on), which the launcher's own menu repeats alongside **Open Decision**. Pause stops checks and starts; Focus keeps VIKI watching but holds nudges and alerts.

**How VIKI works** sits under the card. Its steps are **Notices**, **Decides**, **Tells you** and **Starts work, if you allow**, then Learns and Your data; each opens its settings.

Badges, nudges and alerts, plans and work chats for due tasks, and Note links, Superseded notes and Sources to learn proposals need the Decision model. Everything else also works with simple rules, and the page says which on each line. To add the model, open the **Decision model** row, select **Get a key** to reach TypeSafe's site, paste the key in and select **Save**; select **Test** to confirm it answers. The key stays sealed on this computer and is never shown again. VIKI calls the model at most 10 times a minute, inside the **Daily cap** under **Spending**, and never retries a failed call; once today's spend reaches the cap, or without a key, VIKI uses simple rules.

## Decision and Claude Code

Decision is VIKI Agent's proactive loop. VIKI sends the Decision model a bounded state, as **What leaves this computer** sets it, and gets back assessments of relevance, importance, presentation and useful work. VIKI applies its local scoring, timing, limits and your grants to them. The model does not create cards, edit notes or message Bots: the work itself happens in Claude Code chats, within the grants and limits you set. Bot starts under **Start Bots' work** and scheduled routines do not need a model answer.

## Where goals come from

VIKI reads active project overview titles as goal context: notes at `10-projects/<slug>/<slug>.md` with a heading such as `# Launch the website` and front matter containing `type: project` and `status: active`. Keep due tasks in that folder's `tasks.md`. VIKI uses up to eight active projects; their titles supply goal context, subject to **Never send**, and the overview body is not sent as goal text. A starter project with `status: seed` must become `active` to qualify. Chat and Bots do not require a goal note.

## The launcher

VIKI's launcher is the circle that floats over the window. Drag it anywhere, or move it with `Alt`+arrow keys and reset it with `Home`. Select it, or press `Alt`+`V` from anywhere in the window while no other dialog is open, to open VIKI's panel; `Esc` closes the panel again. Drag the panel's outer edges to resize it; double-click an edge, or press `Enter` on it, to restore that side's default. Its ring shows what's going on without a sound: a gentle brighten for new information, a number for waiting items, a pulsing ring for something it wants to flag, and a turning arc while it's running a chat for you. It still opens the panel, dimmed, whenever Decision is off or paused.

While plans that VIKI's chats prepared wait for your answer, the panel's top line says so, right after **Watching**.

## Items

Open the panel to see what VIKI has noticed. Each item carries a kind chip — **For your information**, **Question**, **Suggestion**, **Ready for you** or **Report** — what it's about, a **Why now** line explaining why it surfaced now, evidence chips linking back to the note or chat behind it, and its own actions.

Every item keeps the same feedback controls: **Not now** sets it aside, **Useful** records that it was right, and **Not useful** marks it wrong; its ⋯ menu's **Stop these** mutes that kind of item, with a reason if you give one — **Out of date**, **Already knew**, **Wrong topic** or **Bad timing**. Unmute it from **How VIKI may reach you**. An item that still offers a way to start its work stays open after **Useful**, and its button reads **Marked useful**.

### What VIKI watches

**What VIKI watches** starts with **How often VIKI checks**: **About every minute** (the default), **Every 5 minutes** or **Every 15 minutes**. VIKI also checks soon after you switch chats or notes, open the launcher or come back to the window. Ten switches, each on by default, sit in three groups: **In your vault**, **In the app** and **Improvements**.

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

A switch also stops the work VIKI may start from what it lists, and the text beside it says which.

### What else VIKI lists

- Kanban cards: a `blocked` card as a **Question**, a `review` card as **Ready for you**, and a card left `working` for a day or `todo` for three days as a **Report**.
- Bot runs: a routine run that waits for your OK or your answer, as a **Question** with **Open session**. While **Start Bots' work** is not **Off**, also the runs VIKI started (with **Stop**), the seats it could start (with **Start**) and why it held a start back.
- Vault, chats and bridges: stopped saves, deleted notes about to expire, a memory that cites a changed note, a chat whose card has waited five minutes or more, and a stopped messaging bridge, each with the place that answers it, such as **Open vault** or **Open chat**.
- Code folders: a trusted code folder whose uncommitted changes have sat for a day, with **Open in Code** and **Take a look**.
- Second looks: a reply that finished a long or corrected task and edited notes, with **Take a second look**.
- Project plans: a project with no changes for two weeks, or tasks three days overdue, with **Open note** and Plan next steps or Check these tasks.
- Message drafts: Draft a handoff when your chat's context is 70% full or more, and Draft literature notes after a reply fetched web pages. VIKI puts a draft in the message box only when you select it, and sends nothing.
- Saved checks: a trusted code folder at a new commit your saved checks have not run on, with **Run checks**.
- Inbox suggestions: captures waiting for a destination, with **Triage inbox** and **Suggest destinations**. Nothing in your vault changes until you send the filing draft.

The start action, such as **Take a look**, shows while the grant is **Ask first**; under **Allowed** VIKI starts the work itself. Most items close once what they report clears or their work has started.

## VIKI's chat

At the bottom of the panel is VIKI's own chat, a **Message VIKI** box kept separate from your regular chats. **New conversation** clears it, and **Open in main window** continues that same chat there. With dictation available, the box also offers the microphone and the round **Hands-free** button (`Alt`+`T`). While talking to VIKI, the first `Esc` ends the conversation and keeps the panel open; the next `Esc` closes the panel.

## What leaves this computer

The **What leaves this computer** row controls what Decision may send. **Send level** picks how much: **Titles only**, **Titles and short excerpts (up to 200 characters)** — the default — or **Titles and sections (up to 2,000 characters)**. Add a folder ending in `/` or a tag starting with `#` to **Never send**, and VIKI leaves anything under it out before anything is sent. Select **See the last state sent** to read the exact text, in **Context & loadout**'s **Decision** tab.

Each request also carries the titles of notes changed since yesterday, of items Decision showed you in the last day and of the items you most recently muted or marked wrong, with your recent feedback: titles only, never a note's text. Items for Bot runs, code folders, project plans, message drafts, saved checks, inbox suggestions and what VIKI notices elsewhere in the app send only their kind; a waiting chat adds its kind of card and roughly how long it has waited, and a second look adds your chat's title. Card items stay out of every request while **Never send** holds any tag or `kanban/`.

A chat VIKI starts is a Claude Code chat like your own, which sends what it reads to Anthropic as your chats do. An inbox suggestion run sends the name, the `captured` value and the first 600 characters of up to 100 captures in `00-inbox/`, and your project names, to Claude; VIKI starts none on its own while **Never send** holds any tag, a folder over a capture or a folder in `10-projects/`.

## How VIKI may reach you

Open **How VIKI may reach you** to set how loud VIKI may be. **Listed** adds an item to the launcher's list, **Badge** puts a number on the launcher, **Nudge** pulses the ring once, and **Alert** keeps the ring lit until you open the panel. Badges, nudges and alerts need the Decision model. **Nudges per hour** is the most nudges and alerts in any hour, and 0 means none. **Alerts** can be on or off; an alert also needs no quiet hours and a task linked to an active project that is due within 2 hours or overdue. **Tell me when I open the related chat or note** holds an item until you open what it is about. **Quiet hours**, shared with Routines, hold nudges and alerts and VIKI's own looks, second looks, project plans, saved checks and inbox runs; in them, Bots start only routines that stop at a draft. **Topics you stopped** lists the kinds you muted with **Stop these**, each with a way to unmute it.

## What VIKI may do

Open **What VIKI may do** to set its grants. **Off** means VIKI doesn't start it, **Ask first** offers it as an action on its item in VIKI's launcher, and **Allowed** lets VIKI start it at its next check, within the daily limit. Message drafts always wait for you to select them. Each line shows its daily limit and today's count, and **More about this** holds its full rules.

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

Plan-mode chats wait for your approval before they change anything; **Approve** on the plan makes the change. A saved check is your own command, run with your account's rights in its folder, so it can change files and use the network; VIKI runs only checks you have run there before, at most once per commit. Under **Allowed**, **Start Bots' work** starts a seat's routine for a `todo` card that waits for it; a card written during a run or a chat VIKI started gets **Start** instead, unless **Let seats hand work to each other** is on.

At most two chats that Decision starts run at once, each against its own daily limit, and VIKI starts none of its own while one is open, so one stays free for yours. A chat VIKI started shows a **Started by VIKI** chip in your session list; **Stop** ends one and **Stop all** ends them all. Once you stop a chat VIKI started, VIKI does not start work for that item again on its own. **VIKI starts a Bot only through its own tested routine, under Start Bots' work.** Apart from the notice of a Bot whose delivery you turned on, it does not message other chats or people or send to your phone.

While VIKI holds back a start under **Allowed**, the item offers it as under **Ask first**, and its **Why now** line says why. At today's daily limit, which counts the ones you start yourself too, the item offers no start.

**Pause automatic starts at Claude plan usage** picks **Off**, **50%**, **80%** (the default) or **95%**: at that share of your Claude plan or more, VIKI starts no chat on its own, and **What VIKI may do** reads **Holding**. Until Claude Code first reports your plan use after VIKI Agent starts, VIKI holds those starts whatever you pick, **Off** included. Your own **Prepare a plan** and **Start a chat to do it** are not held.

### Vault policy

In a vault whose policy is **Protected**, VIKI only prepares plans and starts no work chat. In a vault whose policy is **Read-only (existing folder)**, it starts and offers no plan and no work chat. VIKI reads the vault's policy at each check.

## Learning

Open **Learning**, between **How VIKI is doing** and **Data**, to see what VIKI has learned from your answers and turn it on or off. It changes only when and how loudly VIKI speaks up, never your quiet hours, nudges per hour, alerts, muted topics or what VIKI may do. Once it has 20 labels, including at least three positive and three negative, it also learns which questions and topics matter to you. **Show all numbers**, **How well it matches you** and **Each question** show the details.

**Learning** also shows how the work VIKI starts or offers on its own went over the last 30 days. After 10 outcomes of one kind, VIKI stops that kind while fewer than 1 in 4 was approved or used, says so, and offers **Try again**; it also stops raising a kind of second-look finding you turned down 5 times in a row, and offers **Show these again**. None of this changes your grants, daily limits or quiet hours.

**Compare with another model version**, under **Advanced: version and comparison**, asks every new decision of a second version for comparison only, as a second paid call within the same daily cap. **Reset what VIKI learned** starts its numbers over; your decisions and labels stay.

## Weekly check

While some of last week's held-back items are still unlabelled, a **Weekly check** card appears in the launcher panel. Select **Check** to answer up to 10 of them with **Right to hold** or **Should have told me**, or **Skip this week**. **How VIKI is doing** counts what VIKI showed you and how you answered over the **Last 30 days**, and **Report a missing suggestion** records a miss at any time.

## Self-improvement

VIKI also offers to turn a chat into a skill, one of your own skills that fits the chat you're in, a captured source worth a literature note, a retro once several items are marked Not useful, and better wording for one of Decision's own questions. These prepare a draft in a chat (a skill that fits puts its `/` command there instead), and nothing is sent until you press Send. Three kinds of vault upkeep — linking two notes, marking an older note as replaced, and archiving a project's ticked tasks — land in **Approvals** as a **Link proposal** or **Upkeep proposal**, only from a vault whose policy is **Ordinary**; nothing changes until you approve and send the edit. **Daily limit: vault changes** caps them, and 0 turns them off. A reply with new wording offers **Propose this wording**, which sends it to Approvals; until you decide, each new decision also tries it as a second paid call within the same daily cap. **Approve** switches to it and **Undo** goes back.

## Data

Open **Data** to see **What VIKI keeps** for this workspace: **Decision history**, **Suggestions**, **Model calls**, **Chats VIKI started**, **Proposals**, **Learned preferences** and the **Decision model key**, each with its count and limit. **Browse and delete** lists decisions, suggestions or model calls; select up to 50 rows with **Delete selected**. **Delete all decision history** removes decisions and reports, every suggestion, every model call and what VIKI learned; its confirmation lists what else changes and what stays.
