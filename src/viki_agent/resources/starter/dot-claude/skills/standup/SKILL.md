---
name: standup
description: Kanban standup — read every card in kanban/ and append a Standup entry to today's daily note, changing no card. Use when the owner says "standup", "what's on Kanban", "what needs me", or a daily routine asks for it.
---

# /standup

1. Read every Kanban card in `kanban/`: its status, its newest Handoff line and its Needs you.
2. Open `60-calendar/YYYY/YYYY-MM-DD.md`, or create it from `_templates/daily.md`.
3. Append `- HH:MM — Standup` to its `## Log`, one line per card with its ref, in three groups:
   - **Needs you:** blocked cards with their question, then cards in review.
   - **Stuck:** working cards whose newest handoff is 2 or more days old, or that have none.
   - **Done since yesterday:** done cards whose `modified:` is yesterday or today.

   If Kanban is empty, write "Kanban empty".
4. Change no card: no status, no handoff, no move. Append one line to `system/log.md`.
5. Reply with the entry, Needs you first: at most five lines per group, most urgent first, and past five end the group with "+N more". The daily note keeps every line.
