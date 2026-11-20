---
type: moc
status: evergreen
created: {{viki.date}}
modified: {{viki.date}}
up: "[[Home]]"
tags: [pkm, meta]
aliases: []
author: viki
---

# Second Brain MOC

## Summary
How this vault is organized, and how its agents and skills keep it maintained.

## How the vault works
- `AGENTS.md` sets the persona and the rules your chats in this vault follow; Bots take theirs from VIKI Agent and /kanban.
- `_templates/` holds one template per note type — copy from it rather than inventing structure.
- `system/memory/` is learned memory: changed only through /retro.
- `system/retro/` holds the reflection reports /retro writes.
- `kanban/` holds the team's Kanban cards; the /kanban skill sets how seats use them.

## Skills
- /capture — zero-friction capture into `00-inbox/`.
- /inbox — files captured items into the vault.
- /daily — opens or creates today's daily note.
- /weekly — GTD weekly review of projects and tasks.
- /think — a structured thinking partner for hard questions.
- /research — deep-dives a topic into evergreen knowledge notes.
- /learn — ingests an external source with its provenance.
- /connect — finds and adds links across recent notes.
- /lint — checks vault health: dead links, orphans, schema drift.
- /retro — the approval-gated self-improvement loop for memory and skills.
- /kanban — creates and works the team's Kanban cards in `kanban/`.
- /standup — summarizes the Kanban cards in today's daily note; changes no card.
- /viki-agent — changes VIKI Agent's settings, routines and Bots from a chat opened in its window.

## Agents
- librarian — files inbox items into the vault.
- curator — reconciles and refreshes the vault.
- connector — discovers and adds links between notes.
- researcher — fetches and ingests external sources.
- checker — checks a card against its Done-when list; reads only.

## Core notes
- none yet

## Open questions
- none yet
