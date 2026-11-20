---
name: inbox
description: Librarian pass — GTD-clarify every item in 00-inbox/ and file it into the vault. Use when the owner says "process the inbox", "file my notes", or asks what's in the inbox.
---

# /inbox

Empty `00-inbox/` by clarifying and filing every item. Delegate the mechanical work to the `librarian` subagent when there are more than ~5 items; do small batches yourself.

For each item, in capture order (oldest first):

1. **Clarify (GTD):** What is it? Is it actionable?
   - Actionable, <2 min of agent work (e.g. "add X to project tasks") → just do it now.
   - Actionable, larger → add a `- [ ]` next action to the right `10-projects/<slug>/tasks.md` (create the project from `_templates/project.md` if genuinely new — confirm with the owner before creating a project).
   - Reference/knowledge → file per PARA: evergreen idea → `30-knowledge/` (claim-shaped title, `_templates/note.md`); external material → `40-sources/` (fetch only if the owner asked); person fact → `70-people/`; area fact → `20-areas/`.
   - A fleeting thought with no durable value → append one line to today's daily note Log, citing it.
2. **Enrich:** proper frontmatter per AGENTS.md schema, `up:` to the right MOC, tags, wikilinks to related notes (grep first — prefer linking/updating an existing note over creating a near-duplicate).
3. **Preserve provenance:** the owner's verbatim words survive — quote them in the filed note; never silently rewrite them.
4. **Delete the inbox file** only after its content demonstrably lives elsewhere (the filed note links make this checkable). This deletion is pre-approved by prime directive 5 in `AGENTS.md`. Note the librarian subagent has no shell and cannot delete — when it did the filing, it reports which files are safe to delete and the calling session removes them.

Finish with a filing report: first anything you were unsure about (flag it instead of guessing) and how many items you filed, then item → destination, one line each, grouped by destination when there are more than about five. Append a line to `system/log.md`.
