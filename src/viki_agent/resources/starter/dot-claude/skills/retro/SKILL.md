---
name: retro
description: The assistant's self-improvement loop — extract evidence-cited learnings from recent sessions, propose diffs to memory/skills, apply only what the owner approves. Use when the owner says "retro", "reflect", "improve yourself", or weekly.
---

# /retro

The only flow through which the assistant's own memory and skills change. Never run mid-task; never apply anything without approval.

1. **Gather evidence:** `git log` since the last retro (see `system/retro/` for the last date) when the vault is a Git repository, otherwise the files modified since then + the current session's conversation + recent `system/log.md` entries.
2. **Extract candidates** — a learning qualifies ONLY if backed by one of:
   - an **explicit owner correction** ("no, do it this way", a rejected proposal, a reverted commit)
   - an **explicit owner approval** ("yes, always do that")
   - a **pattern that succeeded twice or more** (same approach, repeated, unremarked = weak; repeated and praised = strong)
   Every candidate must quote its evidence. No evidence, no candidate — plausible-sounding "improvements" without a citation are exactly the drift this procedure exists to prevent.
3. **Classify** each candidate: target (`system/memory/user.md` / `preferences.md` / `lessons.md` / a specific `.claude/skills/*/SKILL.md`) × confidence (high/medium/low). Changes to `CLAUDE.md`, `settings.json`, hooks, and `AGENTS.md` outside its Persona section are **proposal-only** — write the suggested diff in the report for the owner to apply by hand.
4. **Prune:** also propose removals — stale memory entries, lessons that stopped being true, bloat. Memory files stay bounded.
5. **Write the report** to `system/retro/YYYY-MM-DD.md`: candidates grouped by confidence, each with evidence quote + exact proposed diff. It opens with one line: what waits on the owner and what was applied.
6. **Approval gate:** put the candidates to the owner as questions, at most four at a time, recommended option first. Apply approved diffs **one commit each when the vault is a Git repository** (`viki(retro): <what>`) so any bad learning is a single `git revert`. Rejected candidates are recorded in the report as rejected — rejection is itself evidence for the next retro.
7. **Promotion ladder:** an observation becomes a `lessons.md` entry only when a second retro confirms it; a lesson becomes a skill/rule change only after it recurs again. Note in the report which prior observations just got confirmed.
