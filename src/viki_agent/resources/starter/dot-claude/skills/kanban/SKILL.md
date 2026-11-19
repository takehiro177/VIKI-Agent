---
name: kanban
description: Work the team's Kanban cards in kanban/ — create a card, pick up one card, ask the checker, hand off, or block with one question for the owner. Use when the owner invokes /kanban, asks to make or pick up a card, when a seat or routine works Kanban cards, or before a Bot hands work to another Bot or asks the owner to decide.
---

# /kanban

The team's seats coordinate through cards in `kanban/`, not by messaging each other:

- **Chief** is the front door: it turns requests into cards and does none of the work.
- **Ops** runs the standup and the weekly sweep and does none of the work.
- **Specialists** each own one domain and work their own cards.
- **The `checker` subagent** checks work in its own context and edits nothing.

A Bot, a routine or a chat takes the seat its instructions name. Card work starts from a card. Work that goes to another seat, or that needs the owner's decision, goes on a card, even when the owner asked in a chat or thread; a quick answer you give at once needs none. Any other request without a card goes to Chief first.

A card is one note directly in `kanban/`, made from `_templates/card.md`: `type: card`, `status: todo | working | blocked | review | done | cancelled`, `up:` the project, and `tags: [owner/<seat>]`. The owner reads its top; seats read all of it:

- `# <title>`: the outcome in plain words, about 60 characters at most.
- `## Summary`: two lines for the owner: `What:` what the card is for, and `Now:` where it stands.
- `## Needs you`: only while the card is `blocked` or in `review`: one question, then the options as `- A. <label>: <what happens>`, with `(recommended)` after the label you recommend. Give no options when the owner must give an input instead, such as a file or an ID.
- `## Done when`: short checkable items, each ticked when met, with one line of evidence under it.
- `## Handoff`: one line each time a seat stops, newest first.
- `## Check`: the checker's verdict.

1. **Create a card.** Grep `kanban/` first, and update a matching open card if there is one. Otherwise copy `_templates/card.md` to `kanban/<one-line outcome>.md`: no subfolder, one card per outcome, and a name that neither `kanban/` nor `90-archive/kanban/` already uses. Set `status: todo`, `up:` the project overview as a quoted wikilink, and `tags: [owner/<seat>]`, where the seat is a slug of lowercase letters, digits and hyphens. Keep `tags:` on that one line, and put the outcome only in the `# ` heading, which is the card's title: add no `title:` line. VIKI reads only simple front matter, so a `title:` with a comma, colon or quote, or tags written as a list, leaves the card unread. Fill in `## Summary`, and write `## Done when` as `- [ ]` items. Ask one question rather than guess an owner or an item. Reply with the card's path and owner.
2. **Pick up one card.** Take the oldest by `created:` with `status: todo` and your tag. If there is none, reply "No card" and stop. Set `status: working` and `modified:` first. If the owner answered under `## Needs you`, act on that answer, then empty the section: the handoff keeps the answer. Work one card at a time.
3. **Ask the checker.** Before `review`, run the `checker` subagent on the card and paste its whole verdict under `## Check`. On `Verdict: FAIL`, fix and ask again, or block.
4. **Hand off.** Whenever you stop, rewrite `Now:` and add `- YYYY-MM-DD · <seat> · <status> · <notes touched> · <next step> · <blockers or none>` at the top of `## Handoff`, in about 200 characters: details belong in the notes it names. The next seat reads the card, not your chat. Set `review` only after a PASS, and say under `## Needs you` what the owner should check. `review` waits for the owner, who sets `done` or returns the card to `todo` with a note. /weekly moves done and cancelled cards whole into `90-archive/kanban/`.
5. **Block with one question.** Finish every step that can be undone, and stop at the first that cannot, such as sending, deleting, pushing, paying, or anything outside this vault. Set `status: blocked`, write the question and its options under `## Needs you`, and add a handoff line. The owner answers on Kanban, in a chat or in a thread. Whoever records the answer writes it under the question as `**Owner, YYYY-MM-DD:** <their words>`, word for word, and sets the card back to `todo`. When the owner drops the work, set `cancelled`.

Rules for every seat:

- A card's status coordinates seats and grants no permission.
- Fetch any fact that could change an action; do not trust memory for it.
- Write general rules, not stories: propose lessons for /retro, and do not edit `system/memory/` yourself.
- When a session changes the vault, append one line to `system/log.md`: the date, your seat, the action and the files.
