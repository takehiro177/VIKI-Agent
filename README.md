<div align="center">

<a href="https://takehiro177.github.io/VIKI-Agent/">
  <img src="docs/assets/brand-mark.png" alt="VIKI Agent logo" width="120" height="120" />
</a>

# VIKI Agent

**Proactive coding. Decisions you control.**

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-1f1f1f?style=flat-square)](LICENSE)
[![Python 3.14 or newer](https://img.shields.io/badge/python-3.14%2B-1f1f1f?style=flat-square&logo=python&logoColor=white)](docs/getting-started.md#what-you-need)
[![Platforms: Windows, macOS and Linux](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-1f1f1f?style=flat-square)](docs/getting-started.md#what-you-need)
[![Documentation: visual guide](https://img.shields.io/badge/docs-visual%20guide-1f1f1f?style=flat-square)](https://takehiro177.github.io/VIKI-Agent/)
[![Pull requests welcome](https://img.shields.io/badge/PRs-welcome-1f1f1f?style=flat-square)](CONTRIBUTING.md)
[![Sponsor VIKI Agent on GitHub](https://img.shields.io/badge/sponsor-VIKI%20Agent-1f1f1f?style=flat-square&logo=githubsponsors&logoColor=ea4aaa)](https://github.com/sponsors/takehiro177)
[![FOSSA license scan](https://app.fossa.com/api/projects/git%2Bgithub.com%2Ftakehiro177%2FVIKI-Agent.svg?type=shield&issueType=license)](https://app.fossa.com/projects/git%2Bgithub.com%2Ftakehiro177%2FVIKI-Agent?ref=badge_shield&issueType=license)
[![FOSSA security scan](https://app.fossa.com/api/projects/git%2Bgithub.com%2Ftakehiro177%2FVIKI-Agent.svg?type=shield&issueType=security)](https://app.fossa.com/projects/git%2Bgithub.com%2Ftakehiro177%2FVIKI-Agent?ref=badge_shield&issueType=security)

[Visual guide](https://takehiro177.github.io/VIKI-Agent/) · [Showcase](https://takehiro177.github.io/VIKI-Agent/showcase/) ·
[Get started](docs/getting-started.md) · [Manual](docs/using-the-app.md) ·
[Security model](docs/security.md) · [Sponsor](https://github.com/sponsors/takehiro177)

</div>

VIKI Agent is a local workspace for proactive coding. It connects three things: **Decision**,
which suggests work and can start it within limits you set; **Claude Code**, which VIKI Agent runs
for you on the model you bring, from your Claude plan or API key to a cloud API or a model on your
own computer; and a **Markdown knowledge vault**. See work that needs attention, bring the relevant
context into a session, review actions and results, and keep useful knowledge for the next task.
It can create a ready-made second-brain vault (**Start with VIKI**), or open your own notes or a
code folder. In the app, your assistant is called VIKI, pronounced Vicky (VIK-ee), and you can
rename it.

[![Watch the VIKI Agent launch film: Vault, Decision, Bots and Kanban](docs/assets/videos/viki-agent-cinematic-launch.png)](https://takehiro177.github.io/VIKI-Agent/#cinematic-launch)

**[Watch the launch film](https://takehiro177.github.io/VIKI-Agent/#cinematic-launch)** · 1 min 33 sec, with captions

See Vault context, Decision and the Bot team turn a request into reviewed work, with Kanban
tracking each task. [Read the transcript](docs/assets/videos/viki-agent-cinematic-launch-paced-transcript.md).

**[Open the showcase page](https://takehiro177.github.io/VIKI-Agent/showcase/)**: VIKI Agent on
one page, opening with white tiles that assemble the VIKI Agent mark.

## Features

Each clip shows the app working through a scripted example with sample data.

<table>
<tr>
<td width="50%" valign="middle">

### Decision

VIKI notices work worth doing, such as a task due today, and shows you why now. Draft a plan with
one click. VIKI starts work on its own only within the limits you set.

[Decision guide →](docs/decision-engine.md)

</td>
<td width="50%">
  <a href="docs/decision-engine.md"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/decision.poster.jpg"><source srcset="docs/assets/readme-features/decision.webp" type="image/webp"><img src="docs/assets/readme-features/decision.poster.jpg" alt="VIKI's launcher opens a suggestion to review checkout validation, due today and linked to the release goal; Draft a plan puts the request in the message box" width="100%" /></picture></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Chat with Claude Code

Work through a task in a chat. When Claude Code asks to change a file or run a command, you answer
on a card in the conversation.

[Chat and approve work →](docs/using-the-app.md#chat-and-approve-work)

</td>
<td width="50%">
  <a href="docs/using-the-app.md#chat-and-approve-work"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/chat.poster.jpg"><source srcset="docs/assets/readme-features/chat.webp" type="image/webp"><img src="docs/assets/readme-features/chat.poster.jpg" alt="A chat asks Claude Code to reject zero and negative quantities at checkout; the user allows a file edit and a test run on two permission cards, and the reply reports three passing tests" width="100%" /></picture></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Tabs and panes

Keep chats open as tabs and show up to four side by side: drag a tab onto a pane's edge to split
it. Each pane keeps its own draft and Stop, and a file from the Code panel opens beside the chat.

[Tabs and panes →](docs/using-the-app.md#tabs-and-panes)

</td>
<td width="50%">
  <a href="docs/using-the-app.md#tabs-and-panes"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/panes.poster.jpg"><source srcset="docs/assets/readme-features/panes.webp" type="image/webp"><img src="docs/assets/readme-features/panes.poster.jpg" alt="Three chats open as tabs; the user asks one to add the guard to the release notes, drags that tab into a second pane where the reply keeps streaming, and opens checkout.py from the Code panel below it" width="100%" /></picture></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Vault knowledge

Browse your Markdown vault's knowledge notes, sources and maps, and read any note. In a chat,
Claude Code reads the notes it needs, and the reply lists them as its sources.

[The Vault view →](docs/using-the-app.md#the-vault-view)

</td>
<td width="50%">
  <a href="docs/using-the-app.md#the-vault-view"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/vault.poster.jpg"><source srcset="docs/assets/readme-features/vault.webp" type="image/webp"><img src="docs/assets/readme-features/vault.poster.jpg" alt="The Vault front page with seven knowledge notes; the user opens the note A zero-quantity cart must be rejected before payment, then asks a chat what the notes say about zero-quantity carts, and the reply reads that note and lists it as its source" width="100%" /></picture></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Code view

Open a code folder in the Code workbench: browse files, see what changed and read each diff.

[The Code workbench →](docs/using-the-app.md#the-code-workbench)

</td>
<td width="50%">
  <a href="docs/using-the-app.md#the-code-workbench"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/code.poster.jpg"><source srcset="docs/assets/readme-features/code.webp" type="image/webp"><img src="docs/assets/readme-features/code.poster.jpg" alt="The Code workbench's Changes tab lists src/shop/checkout.py; opening it shows a diff that adds a guard rejecting quantities below 1" width="100%" /></picture></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Bot thread with Chief

Give Chief one outcome in a shared Bot thread. Bots hand the work to one another until Checker
reports PASS with evidence, or the thread needs you.

[Shared Bot threads →](docs/bots.md#shared-bot-threads)

</td>
<td width="50%">
  <a href="docs/bots.md#shared-bot-threads"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/botthread.poster.jpg"><source srcset="docs/assets/readme-features/botthread.webp" type="image/webp"><img src="docs/assets/readme-features/botthread.poster.jpg" alt="A Bot thread with Chief, Engineer and Checker: the user asks Chief to ship the checkout quantity fix, Chief hands the guard to Engineer, Engineer hands the result to Checker, and Checker's PASS finishes the thread" width="100%" /></picture></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Bots and Kanban

Bots work the Kanban cards tagged for them, and Checker checks finished work against its Done when
items. Approve the result or send it back.

[Bots guide →](docs/bots.md)

</td>
<td width="50%">
  <a href="docs/bots.md"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/kanban.poster.jpg"><source srcset="docs/assets/readme-features/kanban.webp" type="image/webp"><img src="docs/assets/readme-features/kanban.poster.jpg" alt="The Kanban view; after a refresh, the card Validate checkout quantities is ready for review with Checker passed and 4 of 4 done, and the user approves it" width="100%" /></picture></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Routines

Save a prompt as a routine with its workspace and profile. Test it, then enable its schedule.
Routines run while VIKI Agent is open.

[Routines →](docs/using-the-app.md#routines)

</td>
<td width="50%">
  <a href="docs/using-the-app.md#routines"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/routines.poster.jpg"><source srcset="docs/assets/readme-features/routines.webp" type="image/webp"><img src="docs/assets/readme-features/routines.poster.jpg" alt="The routine Checkout check shows its profile, workspace, first message and next runs; Test run comes back Tested with a Done run in its history, and Enable puts it on the schedule" width="100%" /></picture></a>
</td>
</tr>
<tr>
<td width="50%" valign="middle">

### Choose the model

Use your Claude plan, or pick another tool-calling model for a chat: one routed through Magpie, or
a local or cloud endpoint you connect directly.

[Using your own model →](docs/using-the-app.md#using-your-own-model)

</td>
<td width="50%">
  <a href="docs/using-the-app.md#using-your-own-model"><picture><source media="(prefers-reduced-motion: reduce)" srcset="docs/assets/readme-features/models.poster.jpg"><source srcset="docs/assets/readme-features/models.webp" type="image/webp"><img src="docs/assets/readme-features/models.poster.jpg" alt="The message box's model menu lists a Magpie model, a local Llama server and a cloud API; after choosing the local server, the reply says the chat runs on llama3.1-70b-instruct" width="100%" /></picture></a>
</td>
</tr>
</table>

**Bring your own model.** VIKI Agent runs Claude Code, Anthropic's unmodified `claude`
executable, with the instructions, skills, hooks and permissions Claude Code applies in that
folder. Choose the model behind it:

- **Claude Code sign-in:** your Claude plan, or an API key or cloud provider you set up in
  Claude Code. VIKI Agent does not open Claude Code's credential file and keeps no copy of your
  sign-in.
- **Your own model:** open **Settings › Providers › Chat models**. Pick a model or routing group
  from Magpie, a third-party model router that VIKI Agent runs for you and that keeps your
  provider API keys, or connect an endpoint directly under **Advanced — Direct connections** (Chat
  Completions, Responses API or Anthropic Messages). The model must support tool calling. With a
  local model server, inference stays on your computer.

Either way, Claude Code runs the tools and asks for your permission, and your model usage is
billed by your own provider, not by VIKI Agent.

The window runs locally, in a Chromium browser's app window or the optional Electron window, on
Windows, macOS and Linux. A local window does not make chats offline: Claude Code sends each
request to the model provider you use.

<details>
<summary>About the VIKI name</summary>

<img width="100%" alt="VIKI — Virtual Interactive Kinetic Intelligence" src="docs/assets/original-banner.png" />

</details>

## What you can do

Decision considers supported project signals, current context, timing and feedback. With
**Proactive VIKI** turned on and not paused, and an app window open, it can stay quiet, raise a
suggestion or start eligible work within your grants and activity limits:

- a chat for a due task;
- a plan-mode look at a code folder, your latest work, a stalled project or overdue tasks;
- a Bot's routine set to **Only when a card is waiting**;
- the inbox's read-only suggestion run;
- your saved checks.

Decision can also ask JEV, TypeSafe's optional assessment model, which the app calls the
**Decision model**: a separate service with its own key. Open **Decision** to save that key, choose **What leaves this computer**, inspect decisions
and control learning. Without a usable key, Decision falls back to local rules.

Proactivity starts off, but several grants start as **Allowed**, so review **What VIKI may do**
before you turn it on. A start VIKI holds back, for example in your quiet hours or near your plan
limit, is offered to you with the reason.

Claude Code does the work under its own permissions and hooks. VIKI can propose reusable skills,
literature notes for captured sources, retrospectives and improvements to Decision questions;
each one starts as a draft for you to review. Local learning adapts bounded decision parameters
from your feedback; it does not retrain Claude or JEV, and you can inspect, turn off or reset it.
Messaging bridges are a separate, optional feature. Decision sends nothing to your phone apart
from the notice of a Bot whose delivery you turned on.
[Product concepts](docs/concepts.md) explains the workflow and its boundaries.

| Your goal | Where to start | What you can review |
|---|---|---|
| Work through an idea | Start a chat, or resume a past conversation | Replies, tool activity and requests for your approval |
| Turn notes into a next step | Browse the Vault view and quote a note into chat | The source note, its exact ref and a proposed draft |
| Reuse a working setup | Create an agent profile and test a routine | The profile, workspace, first message and schedule before enabling it |
| Investigate a code change | Open a code folder in the Code view | Files, changes and the conversation beside them |
| Hand an outcome to your Bot team | Open Chief from **Bots**, or start a **Bot thread** | The card or task brief, each Bot's replies and handoffs, and Checker's verdict |
| Pick up earlier work | Search sessions with `Ctrl`+`K` (`⌘`+`K` on macOS) | Matching conversation text and its original session |

[Chat and approve work](docs/using-the-app.md#chat-and-approve-work) walks you through your first
message and Claude Code's permission requests. The [data guide](docs/sessions-and-data.md)
explains which records belong to VIKI Agent and which remain under Claude Code's retention rules.

### Meet the Bot team

![Chief](docs/assets/bots/chief.svg)
![Ops](docs/assets/bots/ops.svg)
![Research](docs/assets/bots/research.svg)
![Writer](docs/assets/bots/writer.svg)
![Engineer](docs/assets/bots/engineer.svg)
![Desk](docs/assets/bots/desk.svg)
![Checker](docs/assets/bots/checker.svg)

Chief turns your requests into Kanban cards for the right seat, and Ops runs the standup and the
weekly review from a routine you set up. Research, Writer, Engineer and Desk work the cards tagged
for them, and Checker checks finished work against its Done when items.
[Meet the core team](https://takehiro177.github.io/VIKI-Agent/#core-team).

## See VIKI Agent in motion

Start with the [visual guide](https://takehiro177.github.io/VIKI-Agent/) for the system and
platform limits. Its
[narrated demonstration](https://takehiro177.github.io/VIKI-Agent/#chat-launcher-demo) follows
**Notice → Decide → Work → Learn**.

### Proactive coding, with decisions that adapt

[![VIKI Agent narrated demonstration: notice opportunities, propose work, execute with permission and adapt its decisions](docs/assets/videos/chat-launcher-demo.png)](https://takehiro177.github.io/VIKI-Agent/#chat-launcher-demo)

[Watch the demonstration in the visual guide](https://takehiro177.github.io/VIKI-Agent/#chat-launcher-demo).

The video opens with Decision and your vault's context. A repeated coding workflow becomes a
**Make this a skill** suggestion, then a separate due task in the vault starts a chat on its own
under **Allowed** authority. In that example, Claude Code asks permission for an Edit and for a
Bash command, changes a disposable file and runs local checks; the test output appears in the
conversation. The last part shows **Not useful** feedback, then **Learning › Show all numbers**
with **Start / Now / Based on** and **Each question**.

The video runs about 2 minutes 45 seconds and is narrated by a generated voice. It shows the
real app working through a scripted example, so the **Learning** numbers on screen are sample
data, not anyone's real results.

Continue in the guide with
[how Decision, Claude Code and your vault work together](https://takehiro177.github.io/VIKI-Agent/#system)
or [keeping context current](https://takehiro177.github.io/VIKI-Agent/#freshness), or read
[the capability boundaries](docs/concepts.md#the-proactive-loop). The manuals
contain the installation steps, complete controls and platform limits.

For team workflows, see
[when to use Chat or Chief](https://takehiro177.github.io/VIKI-Agent/#team-workflow),
[where goals belong](docs/decision-engine.md#where-goals-come-from) and
[examples of proactive work](https://takehiro177.github.io/VIKI-Agent/#proactive-examples).

![VIKI Agent workflow: consider context, decide within grants, execute through Claude Code, and review evidence for reuse](docs/assets/brand-workflow.svg)

## Download

| Platform | Download |
|---|---|
| Windows 11 x64 | [viki-agent-windows-x64-setup.exe](https://github.com/takehiro177/VIKI-Agent/releases/latest/download/viki-agent-windows-x64-setup.exe) |
| macOS, Apple silicon | [viki-agent-macos-arm64.dmg](https://github.com/takehiro177/VIKI-Agent/releases/latest/download/viki-agent-macos-arm64.dmg) |
| Linux x64 (.deb), glibc 2.28+ | [viki-agent-linux-amd64.deb](https://github.com/takehiro177/VIKI-Agent/releases/latest/download/viki-agent-linux-amd64.deb) |

- **Windows:** run the downloaded setup. It adds Start menu and desktop shortcuts.
- **macOS:** drag VIKI Agent to Applications.
- **Linux:** `sudo apt install ./viki-agent-linux-amd64.deb`, then open VIKI Agent from the app
  menu.
- **Linux on aarch64:** no installer yet; [build it from source](#set-up-viki-agent).

These builds are unsigned. See
[Known issues](docs/known-issues.md#viki-agents-own-window-and-installers) for the steps Windows
and macOS ask for on first open.

**Updates:** VIKI Agent shows a notice when a newer release is ready. Choose **Download**, quit
VIKI Agent, then run the new installer.

**Support:** open an issue on the [Issues page](https://github.com/takehiro177/VIKI-Agent/issues).

## Set up VIKI Agent

Clone the repository and run these commands from its root:

```sh
git clone https://github.com/takehiro177/VIKI-Agent
cd VIKI-Agent
uv sync
npm --prefix frontend ci
npm --prefix frontend run build
uv run viki-agent
```

A source checkout runs the copy of Claude Code that `uv sync` installs with the Claude Agent SDK.
To use Magpie, run `uv run python scripts/prepare_magpie.py` once.

The supported desktops are Windows 11, Apple-silicon macOS and Linux x86_64/aarch64
with glibc 2.28 or later. Source setup needs Python 3.14 or newer, uv, Node.js 24 with npm and a
Chromium-family browser. The optional Electron window removes the browser requirement:
after building the interface, run `npm --prefix desktop ci` and
`npm --prefix desktop start`. On Linux, first give Electron's sandbox helper the permissions in
[Known issues](docs/known-issues.md#viki-agents-own-window-and-installers). See
[full setup](docs/getting-started.md).

Saving a note and the note actions work in an ordinary vault on NTFS on Windows, on APFS on
macOS, and on ext4, XFS, Btrfs or tmpfs on Linux; on any other filesystem the vault stays
read-only. Code-folder editing, worktrees and commit, integrate and push work on the same
filesystems. Checks and the terminal work on all three OSes; previews work on Windows.
VIKI Agent's own saves do not reach a protected vault (one whose policy is **Protected**) or
Claude Code's own folders (`~/.claude`).

VIKI cleans up the worktrees it made: after 24 hours idle (72 hours for one you named) it removes
a clean one, and archives one with uncommitted or unpushed work first. Restore an archive from the
**Worktrees** tab for 30 days, or turn cleanup off in **Settings › General**.

## Documentation

Start with the [visual guide](https://takehiro177.github.io/VIKI-Agent/) or browse the
[manuals](docs/README.md).

| Guide | What it covers |
|---|---|
| [Getting started](docs/getting-started.md) | Requirements, installation, first launch and shutdown |
| [Using the app](docs/using-the-app.md) | Chats, permissions, notes, code, worktrees and Decision's controls |
| [Product concepts](docs/concepts.md) | Profiles, Bots, routines, Decision, JEV, Claude Code and the vault |
| [Decision](docs/decision-engine.md) | JEV's role, goals, proactive examples, grants, sharing and learning |
| [Bots](docs/bots.md) | The core team, Chat or Chief, Bot threads, card pickup, routines and results |
| [Configuration](docs/configuration.md) | Settings, environment, limits and data locations |
| [Sessions and data](docs/sessions-and-data.md) | Conversation records, archives, exports and recovery |
| [Security](docs/security.md) | Trust boundaries, permissions and protection limits |
| [Troubleshooting](docs/troubleshooting.md) | Symptoms, repairs and diagnostic sharing |
| [Known issues](docs/known-issues.md) | Limits you may run into |
| [Messaging](docs/messaging.md) | Optional Slack and Discord bridges and their boundaries |
| [Mattermost](docs/mattermost.md) | Connection setup and routine delivery |

## Sponsor VIKI Agent

VIKI Agent is free, open source and developed independently. If it saves you time, please
consider [sponsoring it on GitHub](https://github.com/sponsors/takehiro177), monthly or once, at a
suggested amount or one you choose. Donations fund new features, fixes and testing on Windows,
macOS and Linux. A sponsorship is a donation: it comes with no rewards, goods or services, and
VIKI Agent is the same for everyone. Companies that rely on VIKI Agent are welcome to sponsor too.

Thank you to everyone who sponsors VIKI Agent. When you sponsor, you choose whether GitHub shows
your sponsorship publicly; public sponsors appear on the
[GitHub Sponsors page](https://github.com/sponsors/takehiro177).

## License

VIKI Agent is free software: you can redistribute it and modify it under the terms of the
[GNU Affero General Public License](LICENSE) as published by the Free Software Foundation, either
version 3 of the License or, at your option, any later version. You may use it for any purpose,
at work included. If you distribute a modified version, or let other people use one over a
network, you must offer them its source code under the same license.

VIKI Agent comes without any warranty, as the license's sections 15 and 16 state: no copyright
holder is liable for damages arising from its use, as far as the law allows.

Copyright (C) 2026 VIKI Agent contributors.

### Third-party software and trademarks

Third-party components keep their own licenses and notices. Claude Code is Anthropic's software,
not part of VIKI Agent, and Anthropic's terms govern its use: a source checkout runs the copy the
Claude Agent SDK installs, and other builds run the Claude Code you install. Your model
providers' terms govern your model usage, including Anthropic's terms for using a Claude plan
with other applications, and the providers can change those terms. VIKI Agent is an independent
project and is not affiliated with, endorsed by or sponsored by Anthropic. Claude and
Claude Code are trademarks of Anthropic, PBC.

## Contributing

Contributions are welcome. External contributions require acceptance of the
[Contributor License Agreement](CLA.md) before merge. Contributors keep ownership and grant the
Project Owner copyright and patent rights, including commercial relicensing rights. Using the app
does not require a CLA. See [Contributing](CONTRIBUTING.md) for the acceptance process.

For the CLA, the Project Owner is the holder of the owner account for the
[canonical repository](https://github.com/takehiro177/VIKI-Agent), as identified in the
README revision accepted by the contributor.
