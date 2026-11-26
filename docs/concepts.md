# How VIKI Agent works

VIKI Agent is a local workspace for proactive coding with Claude Code and the knowledge you
keep in Markdown. It brings the next action, the agent's work and the evidence for your review
into one place.

You can move from a note or a due task to a conversation, work in a code folder, inspect the
result and keep a useful finding for later.

Start with [Getting started](getting-started.md) for installation and a first conversation.
See [Known issues](known-issues.md) for limits you may run into.

## The parts and the value they provide

VIKI Agent's parts form a workflow rather than one automatic authority.

| Part | What it does | Why it helps |
|---|---|---|
| Decision | Evaluates supported signals with a model, current context, history and local gates; falls back to rules | Can raise a suggestion or start eligible work within your grants |
| Claude Code, run by VIKI Agent | Runs Anthropic's unmodified `claude` executable for each chat and presents its conversation, tools, questions, permissions and status | Keeps execution visible alongside the work it concerns |
| Code workspace | Connects a trusted folder to chat, file inspection and the supported review tools | Lets you inspect a change in its project context |
| Markdown vault | Holds notes you can read, link, search and attach as explicit excerpts | Keeps decisions and source material in files you can use outside the app |
| Review and recovery controls | Bind supported actions to the version you reviewed and preserve recoverable state | Help you notice changed inputs and recover interrupted work |

The workflow runs like this:

![VIKI Agent workflow: notice opportunities, decide when to help, work within your grants and learn from feedback, with permission cards, recovery and review controls underneath](assets/brand-workflow.svg)

```text
Notice what needs attention
          |
          v
Choose a goal and the relevant sources
          |
          v
Claude Code works in the chosen context
          |
          v
Review changes, results and outstanding questions
          |
          v
Keep a finding or decide the next action
```

Decision chooses attention, timing and eligible work. Claude Code does the work you start or
allow through a grant. A permission, a draft proposal and a completed file change each have their
own meaning.

## How profiles, Bots and routines fit together

A profile, a Bot and a routine describe different ways to use the agent runtime, which Claude
Code supplies. VIKI Agent supplies the interface and the surrounding controls: workspace
selection, chat state, routines, proactive checks and review.

| Part | What it holds or decides | Example |
|---|---|---|
| Profile | Saved role instructions and launch configuration | Research's instructions, model and tools |
| Bot | A profile and persistent vault pair, its reopenable conversation and the routines using that pair | Research in your product vault |
| Chat/session | One Claude Code conversation, whether you opened it directly, through a Bot or for a run | A follow-up about the research brief |
| Routine | A prompt, profile, workspace, trigger and run settings; each attempt gets a fresh chat | Pick up Research's oldest `todo` card |
| Native subagent | A helper called from a chat for one task | The Kanban pack's Checker checks the card's Done when list |
| Vault | Markdown project overviews, tasks, cards, sources and results | `10-projects/website/website.md` and `kanban/backup-comparison.md` |
| Decision | Observes supported signals, evaluates attention and checks which work may start | Offer **Start** for Research or begin its eligible routine |
| JEV | TypeSafe's optional assessment model within Decision; the app shows it as the **Decision model** | Rate whether a due task advances a listed goal and whether preparation would help |

Where things are kept:

- **VIKI's local app data:** profiles, Bot conversation links, routines and Decision records.
  JEV's assessments are Decision records, not new vault notes.
- **Claude Code:** owns the original chat transcripts.
- **The vault:** goals, cards and saved results, as Markdown files. A reply becomes a note when
  you or a chat writes it there.

See [Conversations and local state](sessions-and-data.md#conversations-and-local-state) for
storage and recovery details.

```text
VIKI Agent runs on your computer; JEV is a separate model service
Vault signals --> Decision loop <--> JEV (optional assessments)
Decision --> Local start checks --> Routine --> Fresh run chat --> Claude Code
Profile --> Bot (profile + vault) --> Regular Bot conversation --> Claude Code
Bot --> Routines using the same profile and vault
Regular chat --> Claude Code --> Review reply and resulting files
```

### Chief routes requests

Chief is a Bot conversation for routing requests. With the Kanban pack, it records a request
as a card for a specialist.

- **VIKI's local Kanban and routine checks:** can then start that specialist through
  **Start Bots' work**. JEV is not required for that start.
- **A fixed-time routine:** uses the scheduler instead.

Both paths produce a new run chat, separate from the Bot's regular conversation. See
[Chat, Chief or a specialist](bots.md#choose-chat-chief-or-a-specialist) for the entry points
and a worked example.

### JEV's part in Decision

- **JEV:** receives the state VIKI permits it to see and returns probabilities, scores and
  choices. It neither edits the vault nor sends a message to Chief.
- **VIKI:** converts those assessments into local attention and work decisions.
- **Claude Code:** performs any resulting chat work under its own permissions.

The [Decision guide](decision-engine.md#what-jev-contributes) explains JEV's inputs, outputs and
rules fallback, and [where goals come from](decision-engine.md#where-goals-come-from).

## Explore the workflow

The workflow is covered in several places:

- **[HTML guide](index.html):** explains
  [how Decision, Claude Code and the vault work together](index.html#system) and
  [keeping context current](index.html#freshness).
- **This page:** the sections below explain the workflow's operating boundaries.
- **[Manual](using-the-app.md):** contains the complete feature workflows.
- **[Getting started](getting-started.md):** gives the installation commands.

The optional [demonstration](index.html#chat-launcher-demo) follows
**Notice → Decide → Work → Learn**, in this order:

- a skill suggestion;
- VIKI starting a chat for a due task;
- Claude Code asking your permission to edit a file and run local checks;
- **Not useful** feedback and the **Learning** screen.

It shows the real app working through a scripted example, so the **Learning** numbers on screen
are sample data, not anyone's real results. A generated voice narrates it, and captions are
optional.

The [launch film](index.html#cinematic-launch) is a shorter introduction.

## The proactive loop

Decision connects opportunities, attention, delegated work and feedback. This makes proactivity
useful between prompts: a supported next step can reach you, and an eligible task can start
within the authority you have chosen.

| Stage | What it does | Your control |
| --- | --- | --- |
| Notice | Gathers supported signals such as due tasks, approvals, runs, inbox activity, Kanban cards, vault health, code folders, repeated workflows and knowledge-upkeep opportunities | Choose the workspace, what VIKI watches, sharing settings and exclusions; enable or pause proactivity |
| Decide | Weighs relevance, novelty, actionability, current context, timing, attention cost and authority | Review why an item appeared, defer it, mark it Not useful or stop that topic |
| Propose | Offers the proposals listed below | Inspect the source and choose whether to send the prepared draft |
| Work | Starts the kinds of work listed below | Ask first, Allowed or Off govern each kind of start; **Pause automatic starts at Claude plan usage** and the vault's policy can hold them; Claude Code's own permissions still apply |
| Learn | Uses engagement and feedback to adapt local Decision scoring and attention estimates | Inspect Learning, compare Start / Now / Based on, disable learning or reset it |

The Propose stage offers:

- reusable skills;
- use of one of your own skills;
- a captured source worth a literature note;
- retrospectives;
- question tuning and supported note upkeep;
- a handoff for a nearly full chat;
- literature notes for fetched pages.

The Work stage starts these, with the required grant and activity limits:

- a chat for an eligible due task;
- a Bot's tested routine when one of your cards waits for its seat;
- plan-mode looks at trusted code folders' day-old changes;
- plan-mode second looks at the chat you have open;
- plan-mode plans for stalled projects and overdue tasks;
- the inbox's read-only suggestion run when the inbox piles up;
- a trusted code folder's saved checks on a new commit.

The loop requires:

- proactivity to be enabled and unpaused;
- the app to be running;
- a window to be open.

Proactivity starts disabled. Once it is on, the defaults are:

- **Ask first:** **Start chats to do work**, **Start Bots' work** and **Run my saved checks**.
- **Allowed:** **Prepare drafts**, the plan-mode looks, second looks and project plans, and the
  inbox's read-only suggestion run. VIKI can begin those without a click.

Each plan-mode chat waits for your approval before it changes anything. Improvement offers remain
reviewed drafts even when **Start chats to do work** is **Allowed**. Only the kinds of work listed
above are proposed or started.

The [Decision guide](decision-engine.md#what-viki-may-do) lists every grant with its default and
daily limit.

### Learning from earlier responses

- **Opening or acting on an item:** provides positive engagement.
- **Not useful:** provides negative feedback.
- **Not now:** defers an item without supplying a positive or negative fitting label.

Recent feedback also supplies bounded context for later Decision requests.

With Learning on, VIKI updates its local numbers every five minutes. Later decisions use the
learned numbers while learning is enabled.

- **Scoring weights and topic offsets:** need at least 20 eligible labelled model decisions,
  including three positive and three negative labels.
- **Attention reach and interruption-cost estimates:** can adapt before that fitting threshold.

This is local preference calibration. It does not train Claude or the optional Decision
service's underlying model, and it does not guarantee better outcomes on every decision.

In the demonstration, the **Learning** screen shows sample numbers: across 20 sample decisions,
the goal-link weight moves from 0.600 to 1.266. They are sample data, not anyone's real results.
A single **Not useful** click does not refit the weights at once.

Learning can be turned off or reset. A reset:

- removes the learned snapshot and starts its learning window again;
- preserves recorded decisions and labels.

Changes to reusable skills, knowledge and question wording follow review workflows rather than
automatic self-modification.

Decision and Claude Code use separate model connections:

- **Decision:** uses a separate model service and sharing controls, with a rules fallback. You
  can pick the model version; you can't switch to another provider.
- **Claude Code:** uses its own provider connection.

The [demonstration in the guide](index.html#chat-launcher-demo) shows both at work.

## Proactive attention: Decision

Open **Decision** in the left rail to set up the proactive loop:

- **Proactive VIKI:** enables the loop.
- **Decision model:** saves its key and selects a version.
- **What leaves this computer:** controls titles, short excerpts or sections and the folders or
  tags to exclude.

### What Decision observes

Supported observations include:

- due tasks, approvals, agent runs, inbox activity, routines and Kanban cards;
- bounded vault changes, stopped saves and deleted notes about to expire;
- memories whose cited notes changed;
- chats waiting for you and messages that may not have reached Claude Code;
- MCP servers filling a chat's context and stopped messaging bridges;
- code folders with changes left uncommitted for a day;
- code folders at a new commit their saved checks have not run on;
- inbox captures with no suggested destination;
- replies in the chat you have open that finish a long task or corrected work;
- active projects with no change for two weeks and tasks three days or more overdue;
- the context share of the chat you have open and the web pages its last reply fetched.

The model receives the permitted context, goals and recent feedback. Its answers inform whether
to stay quiet, add an item to the list, show a badge, nudge or alert, and when to surface it.

Local checks also apply:

- **Local gates:** apply quiet hours, focus, typing, muted topics, activity limits and your
  grants.
- **Without a usable model answer:** the local rules still surface supported items.
- **Bot routine eligibility and scheduled starts:** have their own local checks, as described
  above.

### Grants for drafts and starts

**What VIKI may do** separates **Prepare drafts** from **Start chats to do work**, each with
**Off**, **Ask first** and **Allowed**. The settings work like this:

- **Start chats to do work:** an eligible due task with **Allowed** authority can start a chat
  without another prompt.
- **Look on my behalf:** lets VIKI start a plan-mode chat in a trusted code folder whose
  uncommitted changes have sat for a day or more.
- **Take a second look at my work:** lets VIKI start a plan-mode chat that reads the notes a long
  or corrected reply edited and proposes one finding as a plan.
- **Plan next steps for my projects:** lets VIKI start a plan-mode chat that proposes three next
  actions for a stalled project, or ticks and new dates for overdue tasks.
- **Prepare drafts:** also lets VIKI offer a message for the chat you have open, a handoff when
  its context is nearly full or literature notes for pages its last reply fetched. It waits in
  the message box for you to send.
- **Run my saved checks:** lets VIKI run the saved checks you have run in a trusted
  code folder when it moves to a new commit.
- **Suggest destinations for my inbox:** lets VIKI run the inbox's read-only suggestion run on
  its own, so suggestions wait in the triage.
- **Pause automatic starts at Claude plan usage:** holds the chats, Bot runs and inbox
  suggestion runs VIKI would start on its own while your plan use reaches the share you pick or
  your plan limit is reached. It does not hold saved checks, message drafts or your own starts.

These rules also apply:

- **Vault policy:** in a protected vault VIKI starts only plans, and in a read-only vault none.
- **Chats at once:** at most two chats that Decision starts run at once, within separate daily
  limits.
- **Tool use:** Claude Code's own permissions still govern tool use.
- **Improvement offers and vault upkeep:** retain their draft and approval paths.

### Where the loop reaches you

The loop reaches you inside the app.

- It does not choose among your devices or escalate to a messaging bridge after silence.
- It starts a Bot only through that Bot's tested routine set to
  **Only when a card is waiting**, under **Start Bots' work**.
- Messaging connections and Bot routine notices have separate controls.

See the [Decision guide](decision-engine.md).

### Decision and Approvals have different jobs

- The **Decision** page concerns what VIKI chooses to show, hold back or begin.
- The **Approvals** view concerns proposals and choices you review: tool requests, plans and
  questions, note proposals for a protected vault, skill proposals, vault-upkeep proposals and
  proposed question wording.

What each review records:

- **Edit to a protected vault:** VIKI Agent keeps the proposal itself. Handing it to a chat does
  not establish that it has been applied.
- **Skill approval:** also records your intent rather than installing a skill.

Re-read the resulting source before treating a proposed change as completed. See
[Needs you and Approvals](using-the-app.md#needs-you-and-approvals) for the review surfaces and
[Skills and memory](using-the-app.md#skills-and-memory) for creating skill proposals.

## Claude Code: execution you can inspect

VIKI Agent runs Anthropic's unmodified `claude` executable for each chat session. That Claude
Code process uses your instructions, skills, hooks and permission configuration. The app
presents its messages, tool activity, questions, hook notices and agent runs in the desktop
interface.

Where Claude Code's model requests go depends on the connection:

- **Claude Code sign-in:** the sign-in your Claude Code is set up with: your Claude plan, an API key
  or a cloud provider. To keep chats on your Claude subscription only, turn off **Let Claude Code
  use its other sign-in methods** in **Settings › Chat**.
- **Your own model:** set up in **Settings → Providers**, it instead sends Claude Code's model
  requests through VIKI's local gateway to the endpoint you choose. Claude Code still runs the
  tools.

Offline inference requires a local model server. Optional integrations and the commands you run
have their own network behavior.

Permission modes keep their Claude Code meaning. When Claude Code asks for a tool permission,
the card offers **Allow once** or **Deny**. Some configured modes and existing rules allow tools
without a card.

> [!WARNING]
> A code folder, a worktree or a preview is not a sandbox: commands run with your rights.

See [Needs you and Approvals](using-the-app.md#needs-you-and-approvals) and
[Security](security.md#decide-what-to-trust).

## Integrity has separate layers

Integrity depends on the path a change takes. Keep these layers distinct when setting up a vault
or reviewing an agent's work.

| Layer | What protects it | What you still review |
|---|---|---|
| Claude Code execution | Your instructions, hooks, permissions and the app's visible controls | Whether the configured rules cover the operation and whether the result is correct |
| Saving a note in the app | Saves go only to an ordinary vault, and a save stops if the note changed elsewhere first | The intended content, the destination and any conflict or **Recovery** notice |
| Governed proposals and memory | A choice or approval bound to the content reviewed; protected changes remain proposals | Whether the source still supports the claim and whether the change was actually made |

### Hooks belong to your Claude Code configuration

A hook can check an operation at a point supported by Claude Code.

- **VIKI Agent:** preserves that configuration and displays blocked or failing hook notices it
  receives. It does not supply a universal guard around every command or tool.
- **The starter vault:** includes deny rules, but ships no guard hook.

Adding an integrity hook requires choosing its checks and understanding which operations it
covers.

VIKI Agent's own note saves have their own checks, separate from your hooks:

- Do not assume that a Claude Code hook validates a save made by the app.
- Conversely, a save only stops when the note changed elsewhere first. It does not inspect the
  meaning of a change Claude Code makes.

[Security](security.md) explains these boundaries.

### Vault policy determines what the app can save

An ordinary vault is one whose policy is **Ordinary**; it is the only kind VIKI Agent saves notes
into. There, VIKI Agent can save a single note and run the documented rename, move, archive,
delete-to-recovery and restore actions:

- on Windows;
- on APFS on macOS;
- on ext4, XFS, Btrfs or tmpfs on Linux.

If the note changed elsewhere first, a save stops and your draft stays. The app also supports a
create-only copy, under `attachments/`, of a chat attachment you choose to keep.

The other cases:

- **Protected vault:** VIKI Agent does not save into a protected vault. An edit there becomes a
  proposal for you to hand to Claude Code.
- **Read-only vault:** does not become writable because a note declares a title, tag or status.
- **Other filesystems:** a vault stays read-only for VIKI Agent's own note saves and actions.
- **New vaults and backups:** creating a new vault and restoring a backup into a new, read-only
  folder are separate supported paths.

See [The Vault view](using-the-app.md#the-vault-view) and [Sessions and data](sessions-and-data.md).

## Keep context from aging

Old context can fail in different ways:

- a file can change after you attach it;
- an approval can refer to earlier text;
- a long conversation can lose detail;
- a source can become inaccurate even while its bytes remain unchanged.

VIKI Agent provides controls for the first kinds of staleness. Source accuracy still needs your
judgment.

### What VIKI Agent checks

| Situation | Safeguard | Your next step |
|---|---|---|
| Selected note text changes after you attach an excerpt | The selected lines are read again before sending; changed excerpt text is refused | Open the current note and attach the relevant passage again |
| A proposal changes after review | Choices name the reviewed revision; changed content can make them stale | Read the updated item before deciding again |
| An approved memory record changes | Its local approval applies to the exact bytes; changed bytes need review again | Inspect the claim and its sources before approving it |
| A memory is superseded or wrong | Supported memory actions can correct or retire it, within the platform and vault policy | Keep the replacement precise and review the old record's retirement |
| A conversation grows | Context & loadout shows the last reported usage and compaction information from Claude Code; an earlier report can remain visible after a later read fails | Revisit the goal, inspect the current files and choose the context needed for the next step |
| A source is old but unchanged | A content hash does not detect a factual change in the world | Check the source, record a correction and review dependent decisions |

Memory review is explicit:

- **Approval:** a record that says `status: approved` does not authorize itself. The device must
  have approved that exact content. An inference does not become active solely because the file
  calls it approved.
- **Another device:** moving records to another device does not transfer local approval.
- **Active confirms approval of this version.** It does not confirm that its cited sources are
  still current: an unchanged memory can remain Active after a source changes.
- **Excerpts:** excerpt freshness checks cover the selected passage. Retiring a memory can leave
  a claim-only excerpt unchanged.

Review the record's current status before reusing its claim.

> [!NOTE]
> The memory service does not automatically retrieve records into each prompt, expire facts by
> age or guarantee that remembered claims remain true.

The starter's `system/memory/` folders are instruction and skill scaffolding. They are separate
from the app's reviewed `memory/<id>.md` records. A folder name or a skill's presence does not
prove that any particular memory was loaded into the running session.

Use [Context window and loadout](using-the-app.md#context-window-and-loadout) to inspect what Claude Code reported, and attach the
source you want the agent to use. See [Keep and recover work](index.html#keep) for the guide's
overview of the available copies.

### A review loop for the next turn

1. **Inspect the source.** Open the current files and state the goal for the next turn.
2. **Choose the context.** Attach relevant passages and inspect the last reported context.
3. **Review the result.** Resolve changed excerpts and review the outcome before relying on it.
4. **Update the knowledge.** Correct or retire an outdated memory through the available review
   controls. Restate current requirements or start a new conversation when needed.

These are deliberate review steps. The app's freshness checks do not establish whether an
unchanged source is factually correct.

### Agent aging: research and coverage

In [*Your Agents Are Aging Too: Agent Lifespan Engineering for Deployed Systems*](https://arxiv.org/pdf/2605.26302),
Zhu et al. (2026) study reliability as an agent's memory and operating state evolve. Their AgingBench
framework distinguishes four failure mechanisms:

![Four agent-aging mechanisms: compression loses details, interference confuses similar memories, revision leaves outdated state, and maintenance causes regressions.](assets/diagrams/agent-aging.svg)

| Aging mechanism | Related controls | What these controls do not establish |
|---|---|---|
| Compression: useful details are lost during summarization | Last-reported context and compaction visibility; explicit source selection | That a summary retained every detail a later task will need |
| Interference: similar memories obstruct the intended fact | Exact workspace/ref identity and deliberately selected passages | Reliable memory retrieval as similar records accumulate |
| Revision: changes do not propagate correctly | Passage/revision checks and explicit memory correction and retirement | Current factual truth or automatic updates to dependent claims |
| Maintenance: lifecycle changes introduce regressions | Recovery controls and the option to start a fresh conversation | Preserved knowledge after every compaction or other lifecycle event |

This table maps VIKI Agent's controls to related risks. It is not a benchmark result, and these
controls do not establish prevention of agent aging.

## A coding workflow you can repeat

The steps below show how you might work on a coding goal in your own project. For example,
suppose you want to fix a settings panel that loses a draft when the user changes tabs.

1. **Open and inspect the project.** Register the code folder, review the trust information and
   select it as the session's folder. Its instructions and commands deserve the same review as
   a project opened directly in Claude Code.
2. **Define a result.** Ask the agent to preserve a draft across tab changes, explain the cause
   and run the project's existing checks. Name the files or behavior that should stay outside
   the task.
3. **Provide the evidence.** Add relevant note excerpts or images and inspect the selected folder
   context. Reattach an excerpt if its source changes.
4. **Follow the work.** Read the plan and tool activity, answer questions and review requested
   permissions in context. Inspect agent-run results before using them as the basis for a change.
5. **Review the result.** Read the changed files and diff. The Code view can commit selected
   files, review a worktree and run saved checks. A displayed check
   result does not make other checks unnecessary or establish that the whole change is correct.
6. **Keep what will help later.** Record the cause, the decision and the evidence in a note, or
   prepare a proposal for a protected vault. Use the save or proposal flow that fits the vault's
   policy.

To let VIKI start a chat for an eligible due task, review **What VIKI may do** in
[Decision](using-the-app.md#decision):

- **Starts:** its grants, activity limits, **Pause automatic starts at Claude plan usage** and
  the vault's policy govern those starts.
- **Tool actions:** Claude Code's own permissions still govern them.

Review the chat's conversation and affected files before keeping its result. On every platform,
use the controls that platform supports; see
[Workspaces, notes and code](using-the-app.md#workspaces-notes-and-code).

## Make repeated work deliberate

A routine saves a prompt, profile, workspace and schedule so that an agreed job can be repeated.
A useful first routine might inspect a project's open tasks and draft a weekly review without
changing files. Work through it in this order:

1. Preview its rehearsal.
2. Run a test.
3. Inspect the resulting session.
4. Enable the schedule if the result fits your intent.

> [!IMPORTANT]
> Asking for a read-only review in the prompt does not itself restrict the available tools.
> Choose Claude Code permissions that fit the job.

New scheduled routine runs need VIKI Agent running with an open app window and an awake host.

- **Missed schedule times:** are recorded rather than replayed as a burst of work.
- **Failures:** a failure does not trigger an automatic second attempt. Review the run's result,
  resolve outstanding permissions or errors and choose Run now when another attempt is
  appropriate.
- **Decision:** can surface the next routine; it does not create or enable that routine for you.

See [Create and run routines](bots.md#create-and-run-routines) for rehearsal, scheduling, pause,
history and notification behavior.

A routine can also wait for work and stop short of acting:

- **Only when a card is waiting:** with this setting, a daily or weekly routine skips a
  scheduled time when no `todo` card in its vault's `kanban/` folder is tagged for its seat. The
  time is recorded as **Skipped** and no chat starts.
- **Stop at a draft:** with this setting, a run's permission and plan requests are denied at
  once, so Claude finishes with a draft. A run that then completes is recorded as
  **Stopped for your decision**.

With **Start Bots' work** in **What VIKI may do**, VIKI can also start such a routine when one of
your cards waits for its seat. See [A team using Kanban](bots.md#a-team-using-kanban).

### A Bot gives repeated work a place to return to

A Bot links an agent profile and a persistent vault workspace to one reopenable conversation
and the routines owned by that pair. It provides a place to check:

- the current conversation;
- routine outcomes;
- optional Mattermost delivery state.

Each routine run still has its own conversation. A delivered notice and a successful local run
are separate outcomes.

Continuity also needs review:

- Saved history can include material that has left the model's active context.
- A Bot's profile instructions are part of Claude Code's system prompt, which compaction does
  not summarize. An Agents › Profiles chat's instructions are first-turn text, which compaction
  can drop.

Check Context & loadout, restate current requirements and choose New conversation when a fresh
context is appropriate. A new conversation preserves the previous conversation's history; it
does not certify that old assumptions are still true.

See [Bots](bots.md) for setup and controls.

## For contributors: trace the workflow

The user-facing concepts above map to these entry points in the source checkout:

| Follow this behavior | File and symbol |
|---|---|
| Chief's role, specialist roles and the initial instructions | `frontend/src/components/Profiles/seatStarters.ts`, `SEAT_STARTERS` |
| Active project notes becoming goal titles | `src/viki_agent/automation/brief.py`, `read_goals`; `src/viki_agent/decision_loop/state.py`, `build_state` |
| JEV request and structured response validation | `src/viki_agent/decision_loop/model.py`, `DecisionModel.ask`; the guide and questions in `playbook.py` |
| Assessments becoming attention, timing and work choices | `src/viki_agent/decision_loop/arbiter.py`, `decide`; item text in `templates.py`, `render` |
| A Kanban card becoming a Bot run | `src/viki_agent/decision_loop/engine.py`, `DecisionLoopEngine._plan_seats` and `_start_seats`; `seats.py`, `plan`; `src/viki_agent/automation/tasks.py`, `TaskRunner.run_decision` |

The model-assessment path and the seat-start path are distinct in the engine. When changing
proactive behavior, trace the applicable start path as well as its launcher item. Changing a
rating does not by itself change routine eligibility, workspace policy or tool permission.

## Continue with the right reference

These guides continue from this page:

- **[Getting started](getting-started.md):** requirements, installation, startup and a first
  conversation.
- **[Using the app](using-the-app.md):** exact controls and complete feature workflows.
- **[Security](security.md):** data boundaries, permissions and accepted limits.
- **[Sessions and data](sessions-and-data.md):** Claude Code's transcripts, data kept by VIKI
  Agent, exports and recovery.
- **[Troubleshooting](troubleshooting.md):** symptoms and recovery steps.
- **[All guides](README.md):** setup, feature workflows, configuration and recovery.
