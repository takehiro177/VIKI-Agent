# Welcome

This vault was set up from the VIKI starter v5. The assistant `{{viki.assistant_name}}` reads `AGENTS.md` and `.claude/` when a chat starts here.

## What is here
- `AGENTS.md` — the persona between its markers, plus the rules. Change the persona in Settings › Instructions and Persona, by hand, or by asking `{{viki.assistant_name}}`.
- `CLAUDE.md`, `Home.md` and the numbered folders — the vault's map; retrieval starts at `Home.md`.
- `_templates/` — one template per note type; copy from it rather than inventing structure.
- `kanban/` — the team's Kanban cards: one card per job; /kanban works a card and /standup summarizes the cards (see Team in `AGENTS.md`).
- `system/` — empty memory changed only through /retro, the `retro/` folder, and the log.
- `.claude/agents` and `.claude/skills` — the shipped agents (librarian, curator, connector, researcher, checker) and skills (capture, inbox, daily, weekly, think, research, learn, connect, lint, retro, kanban, standup).
- `.claude/settings.json` — deny rules only, no hooks.
- `.viki/vault.json` — the vault id, the starter version, and the file fingerprints.

## Getting started
- Capture anything — a thought, a link, a task — with zero friction; it lands in `00-inbox/`.
- Run /inbox to file what has landed.
- Run /daily to open today's note.
- Run /weekly for a weekly review.

## What the app does here
The app wrote these files once and does not rewrite them on its own. A note changes when you save it in the Vault view, when you save this vault's instructions in Settings, or when a chat you start here changes it under the permissions you grant.

You can delete this note at any time.
