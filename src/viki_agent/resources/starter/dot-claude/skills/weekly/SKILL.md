---
name: weekly
description: GTD weekly review — project and Kanban sweep, stale-task triage, archive done work, inbox-zero check. Use when the owner says "weekly review", "review the week", or once a week.
---

# /weekly

Write `60-calendar/YYYY/YYYY-Www.md` from `_templates/weekly.md` (ISO week number), by:

1. **Project sweep:** for every `10-projects/<slug>/`, read overview + tasks. Table: status, movement this week (from daily notes, plus git log when the vault is a Git repository), proposed next action. Flag projects with zero movement for 2+ weeks — propose: push, pause (→ status: archived + move to `90-archive/`), or renegotiate scope. Proposals only; the owner decides.
2. **Task triage:** overdue and stale (>2 weeks untouched) checkboxes — propose re-date, delegate to the assistant, or drop, each with a one-line reason.
3. **Archive:** projects/notes the owner confirms done → move whole into `90-archive/`, update links and MOCs. Kanban cards in `kanban/` with `status: done` or `status: cancelled` and a `modified:` date more than 7 days old move whole into `90-archive/kanban/`, adding the card's `modified:` date to its name when that name is already taken there, so no file is replaced; list any lesson a card proposes as a /retro candidate.
4. **Inbox-zero check:** if `00-inbox/` isn't empty, run the /inbox flow first, then continue.
5. **Insights:** patterns from the week's daily notes worth keeping — promote durable ones to `30-knowledge/` or flag them as /retro candidates.
6. Append to `system/log.md`; recommend running `/retro` if the week contained corrections or repeated friction.
7. **Reply** with the decisions first: one batch question for the proposals in steps 1 and 2, recommended option first. Then the week in the note's three-sentence Summary; the tables stay in the note.

Tone: a chief-of-staff weekly sit-down — candid about drift, concrete about next week.
