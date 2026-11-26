/**
 * `starterHandoff.ts`: which `StarterPlan` items a chat may act on (`chatItems`) and the exact
 * hand-off message built from them (`starterHandoff`).
 */
import { describe, expect, test } from "vitest";
import type { StarterItem, StarterPlan } from "../../clients/app/shellApi";
import { chatItems, starterHandoff } from "./starterHandoff";

const ADD: StarterItem = {
  path: ".claude/skills/example/SKILL.md",
  action: "add",
  diff: null,
  text: "# Example\nA new skill.",
};
const REPLACE: StarterItem = {
  path: "system/log.md",
  action: "replace",
  diff: "--- a/system/log.md\n+++ b/system/log.md\n@@\n-old\n+new",
  text: null,
};
const MERGE: StarterItem = {
  path: "30-knowledge/some-note.md",
  action: "merge",
  diff: "--- a/30-knowledge/some-note.md\n+++ b/30-knowledge/some-note.md\n@@\n-old\n+new",
  text: null,
};
const MERGE_NO_BASE: StarterItem = {
  path: "30-knowledge/no-base.md",
  action: "merge",
  diff: null,
  text: "A brand new body VIKI Agent has no shipped base for.",
};
const REMOVE: StarterItem = {
  path: "_templates/old-template.md",
  action: "remove",
  diff: null,
  text: null,
};
const EDITOR: StarterItem = {
  path: "AGENTS.md",
  action: "editor",
  diff: "--- a/AGENTS.md\n+++ b/AGENTS.md\n@@\n-old\n+new",
  text: null,
};
const MANUAL: StarterItem = {
  path: "CLAUDE.md",
  action: "manual",
  diff: "--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@\n-old\n+new",
  text: null,
};
const SKIP: StarterItem = {
  path: "system/unreadable.md",
  action: "skip",
  diff: null,
  text: null,
};

function planWith(items: StarterItem[], overrides: Partial<StarterPlan> = {}): StarterPlan {
  return {
    state: "available",
    from_version: 5,
    to_version: 7,
    notify: true,
    items,
    agents: null,
    ...overrides,
  };
}

const ALL_ACTIONS = [ADD, REPLACE, MERGE, MERGE_NO_BASE, REMOVE, EDITOR, MANUAL, SKIP];

describe("chatItems", () => {
  test("keeps add, replace, merge and remove; drops manual and skip; drops editor", () => {
    expect(chatItems(planWith(ALL_ACTIONS), false).map((item) => item.action)).toEqual([
      "add",
      "replace",
      "merge",
      "merge",
      "remove",
    ]);
  });

  test("keeps editor too, in the plan's own order, only when agentsInChat is true", () => {
    expect(chatItems(planWith(ALL_ACTIONS), true).map((item) => item.action)).toEqual([
      "add",
      "replace",
      "merge",
      "merge",
      "remove",
      "editor",
    ]);
  });
});

describe("starterHandoff: opening", () => {
  test("states the version range and the explicit approval", () => {
    const handoff = starterHandoff(planWith([]), false);
    expect(handoff).toContain(
      "Please apply VIKI Agent's starter update to this vault: starter version 5 to 7.",
    );
    expect(handoff).toContain(
      "I approve this update. It may change files under `.claude/` and the rules outside the Persona section of `AGENTS.md`, which this vault's prime directives otherwise leave to me or to /retro; this message is my explicit request for exactly the changes below. Do not edit `CLAUDE.md`, `.claude/settings.json` or anything under `.viki/`: I apply those myself.",
    );
    expect(handoff).toContain(
      "First, if this vault is a Git repository, commit its current state.",
    );
  });

  test("writes 'unknown' when from_version is null", () => {
    const handoff = starterHandoff(planWith([], { from_version: null }), false);
    expect(handoff).toContain(
      "Please apply VIKI Agent's starter update to this vault: starter version unknown to 7.",
    );
  });

  test("an empty plan has no sections and still ends with the Last steps", () => {
    const handoff = starterHandoff(planWith([]), false);
    expect(handoff).not.toContain("## New files");
    expect(handoff).not.toContain("## Files still as VIKI Agent shipped them");
    expect(handoff).not.toContain("## Files I changed");
    expect(handoff).not.toContain("## Files VIKI Agent no longer ships");
    expect(handoff).toContain("## Last steps");
  });
});

describe("starterHandoff: section routing", () => {
  const handoff = starterHandoff(planWith(ALL_ACTIONS), true);

  test("add items go under New files, as exact text", () => {
    expect(handoff).toContain(
      [
        "## New files",
        "Create each file with exactly the text shown.",
        "### `.claude/skills/example/SKILL.md`",
        "```markdown\n# Example\nA new skill.\n```",
      ].join("\n"),
    );
  });

  test("replace and editor items go under Files still as VIKI Agent shipped them, in plan order", () => {
    const shipped = [
      "## Files still as VIKI Agent shipped them",
      "Apply each diff exactly as shown: the file still holds the text the diff starts from.",
      "### `system/log.md`",
      `\`\`\`diff\n${REPLACE.diff}\n\`\`\``,
      "### `AGENTS.md`",
      `\`\`\`diff\n${EDITOR.diff}\n\`\`\``,
    ].join("\n");
    expect(handoff).toContain(shipped);
  });

  test("merge items go under Files I changed; a null diff gets the no-base sentence and the text block", () => {
    expect(handoff).toContain(
      [
        "## Files I changed",
        "Merge each diff (from the text VIKI Agent shipped to its new text) into my version and keep my own edits. Show me each merged file and wait for my OK before you write it. If a change no longer fits, skip it and tell me why.",
        "### `30-knowledge/some-note.md`",
        `\`\`\`diff\n${MERGE.diff}\n\`\`\``,
        "### `30-knowledge/no-base.md`",
        "VIKI Agent has no record of the text this file started from. Compare it with the new text below and propose what to change.",
        `\`\`\`markdown\n${MERGE_NO_BASE.text}\n\`\`\``,
      ].join("\n"),
    );
  });

  test("remove items go under Files VIKI Agent no longer ships, as a plain bullet list with no fenced block", () => {
    expect(handoff).toContain(
      [
        "## Files VIKI Agent no longer ships",
        "Ask me before you move any of these to `90-archive/`.",
        "- `_templates/old-template.md`",
      ].join("\n"),
    );
  });

  test("manual and skip items never appear", () => {
    // "CLAUDE.md" itself is named in the opening paragraph (the owner applies it by hand), so
    // the refusal checks for the MANUAL item's own heading and diff, not the bare filename.
    expect(handoff).not.toContain("### `CLAUDE.md`");
    expect(handoff).not.toContain(MANUAL.diff);
    expect(handoff).not.toContain("system/unreadable.md");
  });

  test("editor is absent when agentsInChat is false, and its own section drops too if nothing else fills it", () => {
    const withoutAgents = starterHandoff(planWith([REPLACE, EDITOR]), false);
    expect(withoutAgents).toContain("### `system/log.md`");
    expect(withoutAgents).not.toContain("### `AGENTS.md`");
    const editorOnly = starterHandoff(planWith([EDITOR]), false);
    expect(editorOnly).not.toContain("## Files still as VIKI Agent shipped them");
  });
});

test("a .json path with text fences as json", () => {
  const item: StarterItem = {
    path: ".viki/config.json",
    action: "add",
    diff: null,
    text: '{"a": 1}',
  };
  const handoff = starterHandoff(planWith([item]), false);
  expect(handoff).toContain('```json\n{"a": 1}\n```');
});

test("a block holding a triple-backtick run gets a four-backtick fence", () => {
  const text = "Example:\n```\nfenced\n```\nDone.";
  const item: StarterItem = { path: "30-knowledge/with-fence.md", action: "add", diff: null, text };
  const handoff = starterHandoff(planWith([item]), false);
  expect(handoff).toContain(`\`\`\`\`markdown\n${text}\n\`\`\`\``);
});

test("a diff holding a triple-backtick run gets a four-backtick diff fence", () => {
  const diff = "--- a/x\n+++ b/x\n@@\n-```\n+kept";
  const item: StarterItem = { path: "x", action: "replace", diff, text: null };
  const handoff = starterHandoff(planWith([item]), false);
  expect(handoff).toContain(`\`\`\`\`diff\n${diff}\n\`\`\`\``);
});

describe("starterHandoff: closing", () => {
  test("ends with the Last steps and the exact record block for to_version", () => {
    const handoff = starterHandoff(planWith([], { to_version: 9 }), false);
    const record = [
      "---",
      "type: note",
      "status: evergreen",
      "starter_version: 9",
      "---",
      "# Starter updates",
      "",
      "## Summary",
      "What each VIKI Agent starter update changed in this vault, oldest first. VIKI Agent reads `starter_version` to know which update was last reviewed.",
      "",
      "| Date | Version | File | Outcome |",
      "|---|---|---|---|",
    ].join("\n");
    const expected = [
      "## Last steps",
      "1. Record the update in `system/starter-updates.md`: create it from the block below if it is missing, set `starter_version: 9` in its frontmatter, and add one row per file above with today's date, 9, the file and its outcome (applied, adapted or declined).",
      `\`\`\`markdown\n${record}\n\`\`\``,
      "2. Append this session to `system/log.md`, as this vault's rules ask.",
    ].join("\n");
    expect(handoff).toContain(expected);
    expect(handoff.endsWith(expected)).toBe(true);
  });
});
