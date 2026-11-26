# VIKI Agent documentation

These guides describe VIKI Agent. VIKI watches for things that need you and raises them in a
floating launcher; it starts work only within the grants you set.

VIKI can ask TypeSafe's Decision model whether to tell you about something now or hold it. Saving
a model key does not grant permission to act.

1. Start with [Getting started](getting-started.md).
2. Then open the [Decision guide](decision-engine.md) to set what VIKI watches, what it may share,
   and what work it may start. In the app, **Decision** is the page in the left rail.

For the relationship between profiles, Bots, routines and Claude Code, start with
[How VIKI Agent works](concepts.md#how-profiles-bots-and-routines-fit-together). For common
workflow questions, see:

- [Chat or Chief](bots.md#choose-chat-chief-or-a-specialist)
- [Where goals come from](decision-engine.md#where-goals-come-from)
- [Proactive examples](decision-engine.md#examples-of-proactive-work)

## Set up and use the app

| Guide | What it covers |
|---|---|
| [Getting started](getting-started.md) | Source installation, the browser window, VIKI Agent’s own desktop window, voice and optional local builds |
| [Using VIKI Agent](using-the-app.md) | Chats, permissions, workspaces, notes, code, Bots, routines and Decision |
| [Decision](decision-engine.md) | JEV's inputs and outputs, goals, proactive examples, the launcher, grants, sharing and learning |
| [Configuration](configuration.md) | Launch settings, app data, saved messaging setup and your Claude Code installation |

## Keep control of work and data

| Guide | What it covers |
|---|---|
| [Sessions and data](sessions-and-data.md) | Conversation ownership, history, exports, backups and Reset |
| [Security and privacy](security.md) | Workspace trust, model sharing, Claude Code's own permissions and storage boundaries |
| [Known issues and limits](known-issues.md) | Limits you may run into |
| [Troubleshooting](troubleshooting.md) | Setup failures, recovery and diagnostics |

## Bots and messaging

| Guide | What it covers |
|---|---|
| [Bots](bots.md) | Choosing Chat or Chief, profile-backed conversations, card pickup, routines, Kanban and results |
| [Mattermost mobile access](mattermost.md) | Chat with VIKI from the Mattermost mobile app, plus optional Bot result notices |
| [Slack and Discord messaging](messaging.md) | Setup, text chat and approvals for Slack or Discord direct messages |

Messaging is configured separately. VIKI starts a Bot only through its tested routine, under
**Start Bots' work**. Apart from the notice of a Bot whose delivery you turned on, it does not send
to your phone or message other chats or people.

Keep the desktop host awake and the app running when you use a messaging bridge.

## Watch and explore

- [Visual guide](index.html)
- [How VIKI Agent works](concepts.md)
- [Launch film](index.html#cinematic-launch)
- [Proactive workflow guide transcript](assets/videos/chat-launcher-demo-transcript.md)
