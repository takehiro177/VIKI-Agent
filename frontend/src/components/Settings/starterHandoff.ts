/**
 * Composes the one hand-off message that applies a starter update: which `StarterPlan` items a
 * chat may act on, and the exact message text built from them. The chat is the only writer (the
 * app only reads and plans), so this message is both the owner's approval and the whole
 * instruction set.
 */
import type { StarterItem, StarterPlan } from "../../clients/app/shellApi";
import { fenceFor } from "./persona";

/** The items a chat ever acts on: `add`/`replace`/`merge`/`remove` always, plus `editor` (the
 * AGENTS.md rules proposal) only when the chat may edit that file. `manual` and `skip` never
 * appear here; they are never dispatched through the chat. */
export function chatItems(plan: StarterPlan, agentsInChat: boolean): StarterItem[] {
  return plan.items.filter(
    (item) =>
      item.action === "add" ||
      item.action === "replace" ||
      item.action === "merge" ||
      item.action === "remove" ||
      (agentsInChat && item.action === "editor"),
  );
}

/** `diff` when the server sent one; otherwise `text`, fenced as `json` for a `.json` path and
 * `markdown` otherwise. */
function fenceLang(item: StarterItem): "diff" | "json" | "markdown" {
  if (item.diff !== null) return "diff";
  return item.path.endsWith(".json") ? "json" : "markdown";
}

function fenced(content: string, lang: string): string {
  const fence = fenceFor(content);
  return `${fence}${lang}\n${content}\n${fence}`;
}

function renderItem(item: StarterItem): string[] {
  const lines = [`### \`${item.path}\``];
  if (item.action === "merge" && item.diff === null) {
    lines.push(
      "VIKI Agent has no record of the text this file started from. Compare it with the new text below and propose what to change.",
    );
  }
  lines.push(fenced(item.diff ?? item.text ?? "", fenceLang(item)));
  return lines;
}

/** One section, only when `items` is non-empty: a leading blank line, the section's own heading
 * and instruction line, then each item's heading and fenced block. */
function section(heading: string, instruction: string, items: StarterItem[]): string[] {
  if (items.length === 0) return [];
  const lines = ["", heading, instruction];
  for (const item of items) lines.push(...renderItem(item));
  return lines;
}

/** The fenced skeleton for `system/starter-updates.md`, the record note the closing steps ask the
 * chat to create or update. */
function recordBlock(toVersion: number): string {
  const content = [
    "---",
    "type: note",
    "status: evergreen",
    `starter_version: ${toVersion}`,
    "---",
    "# Starter updates",
    "",
    "## Summary",
    "What each VIKI Agent starter update changed in this vault, oldest first. VIKI Agent reads `starter_version` to know which update was last reviewed.",
    "",
    "| Date | Version | File | Outcome |",
    "|---|---|---|---|",
  ].join("\n");
  return fenced(content, "markdown");
}

/** The message the owner sends to a chat to apply a starter update: an explicit approval and
 * commit step, one section per action that has items, and the closing record-keeping steps.
 * `agentsInChat` gates only whether the AGENTS.md rules proposal (`editor`) is included; every
 * other owned action is always offered when the plan carries it. */
export function starterHandoff(plan: StarterPlan, agentsInChat: boolean): string {
  const from = plan.from_version === null ? "unknown" : String(plan.from_version);
  const to = String(plan.to_version);
  const items = chatItems(plan, agentsInChat);
  const add = items.filter((item) => item.action === "add");
  const shipped = items.filter((item) => item.action === "replace" || item.action === "editor");
  const merged = items.filter((item) => item.action === "merge");
  const removed = items.filter((item) => item.action === "remove");

  const lines: string[] = [
    `Please apply VIKI Agent's starter update to this vault: starter version ${from} to ${to}.`,
    "",
    "I approve this update. It may change files under `.claude/` and the rules outside the Persona section of `AGENTS.md`, which this vault's prime directives otherwise leave to me or to /retro; this message is my explicit request for exactly the changes below. Do not edit `CLAUDE.md`, `.claude/settings.json` or anything under `.viki/`: I apply those myself.",
    "",
    "First, if this vault is a Git repository, commit its current state.",
  ];

  lines.push(...section("## New files", "Create each file with exactly the text shown.", add));
  lines.push(
    ...section(
      "## Files still as VIKI Agent shipped them",
      "Apply each diff exactly as shown: the file still holds the text the diff starts from.",
      shipped,
    ),
  );
  lines.push(
    ...section(
      "## Files I changed",
      "Merge each diff (from the text VIKI Agent shipped to its new text) into my version and keep my own edits. Show me each merged file and wait for my OK before you write it. If a change no longer fits, skip it and tell me why.",
      merged,
    ),
  );
  if (removed.length > 0) {
    lines.push(
      "",
      "## Files VIKI Agent no longer ships",
      "Ask me before you move any of these to `90-archive/`.",
      ...removed.map((item) => `- \`${item.path}\``),
    );
  }

  lines.push(
    "",
    "## Last steps",
    `1. Record the update in \`system/starter-updates.md\`: create it from the block below if it is missing, set \`starter_version: ${to}\` in its frontmatter, and add one row per file above with today's date, ${to}, the file and its outcome (applied, adapted or declined).`,
    recordBlock(plan.to_version),
    "2. Append this session to `system/log.md`, as this vault's rules ask.",
  );

  return lines.join("\n");
}
