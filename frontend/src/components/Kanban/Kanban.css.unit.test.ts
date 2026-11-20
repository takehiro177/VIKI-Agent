import { expect, test } from "vitest";
import { narrowBlock, readCss, ruleIn } from "../../testing/css";

// jsdom applies no stylesheet, so the source is pinned.
const css = readCss(import.meta.url, "Kanban.css");

test("the lanes are a wrapping grid with no horizontal scroll", () => {
  const lanes = ruleIn(css, ".kanban-lanes");
  expect(lanes).toMatch(/grid-template-columns:\s*repeat\(auto-fit, minmax\(200px, 1fr\)\)/);
  expect(lanes).not.toMatch(/overflow-x|grid-auto-flow/);
});

test("a lane has a 3px top rule in its own colour, neutral by default", () => {
  const lane = ruleIn(css, ".kanban-lane");
  expect(lane).toMatch(/--kanban-lane:\s*var\(--t3\)/);
  expect(lane).toMatch(/border-top:\s*3px solid var\(--kanban-lane\)/);
});

test.each(["todo", "working", "blocked", "review", "done"])(
  "the %s lane takes its status colour",
  (stage) => {
    expect(ruleIn(css, `.kanban-lane[data-stage="${stage}"]`)).toMatch(
      new RegExp(`--kanban-lane:\\s*var\\(--kanban-${stage}\\)`),
    );
  },
);

test("the glyph and the count chip use the lane colour", () => {
  expect(ruleIn(css, ".kanban-lane-glyph")).toMatch(/color:\s*var\(--kanban-lane\)/);
  const chip = ruleIn(css, ".kanban-count");
  expect(chip).toMatch(/border:\s*1px solid var\(--kanban-lane\)/);
  expect(chip).toMatch(/background:\s*color-mix\(in srgb, var\(--kanban-lane\) 7%, transparent\)/);
  expect(chip).toMatch(/color:\s*var\(--t1\)/);
});

test("narrow windows stack the lanes in one column", () => {
  const narrow = narrowBlock(css);
  expect(ruleIn(narrow, ".kanban-lanes")).toMatch(/grid-template-columns:\s*1fr/);
  expect(ruleIn(narrow, ".kanban-lane")).toMatch(/min-height:\s*0/);
});

test("forced colours use system colours for the rule, glyph, chip and focus", () => {
  const forced = narrowBlock(css, "@media (forced-colors: active) {");
  expect(ruleIn(forced, ".kanban-lane")).toMatch(/border-top-color:\s*CanvasText/);
  expect(ruleIn(forced, ".kanban-lane-glyph")).toMatch(/color:\s*CanvasText/);
  expect(ruleIn(forced, ".kanban-count")).toMatch(/border-color:\s*CanvasText/);
  expect(ruleIn(forced, ".kanban-card:focus-within")).toMatch(/outline:\s*2px solid Highlight/);
});

test("the sheet has no animation or transition", () => {
  expect(css).not.toMatch(/animation|transition/);
});

test("owner names on cards stay neutral, not the Bot's hue", () => {
  expect(ruleIn(css, ".kanban-card .bot-tag-name")).toMatch(/color:\s*var\(--t1\)/);
});

test("the empty state keeps its buttons at their own width", () => {
  expect(ruleIn(css, ".kanban-empty")).toMatch(/justify-items:\s*start/);
});

test("the needs-you options are a wrapping grid with a 210px minimum", () => {
  const options = ruleIn(css, ".kanban-needs-options");
  expect(options).toMatch(/grid-template-columns:\s*repeat\(auto-fit, minmax\(210px, 1fr\)\)/);
});

test("a recommended option gets a 2px border in the owner-recommended colour", () => {
  expect(ruleIn(css, ".kanban-needs-option[data-recommended]")).toMatch(
    /border:\s*2px solid var\(--or\)/,
  );
});

test.each(["blocked", "review"])("the %s needs-you chip takes its lane colour", (stage) => {
  expect(ruleIn(css, `.kanban-chip[data-stage="${stage}"]`)).toMatch(
    new RegExp(`--kanban-lane:\\s*var\\(--kanban-${stage}\\)`),
  );
});

test("the chip is neutral by default and its glyph follows the chip's colour", () => {
  const chip = ruleIn(css, ".kanban-chip");
  expect(chip).toMatch(/--kanban-lane:\s*var\(--t3\)/);
  expect(ruleIn(css, ".kanban-chip .ico")).toMatch(/color:\s*var\(--kanban-lane\)/);
});

test("the needs-you badge uses the owner-recommended colours", () => {
  const badge = ruleIn(css, ".kanban-badge");
  expect(badge).toMatch(/background:\s*var\(--or\)/);
  expect(badge).toMatch(/color:\s*var\(--on-or\)/);
});

test("forced colours cover the chip, its glyph, the badge and a recommended tile", () => {
  const forced = narrowBlock(css, "@media (forced-colors: active) {");
  expect(ruleIn(forced, ".kanban-chip")).toMatch(/border-color:\s*CanvasText/);
  expect(ruleIn(forced, ".kanban-chip .ico")).toMatch(/color:\s*CanvasText/);
  expect(ruleIn(forced, ".kanban-badge")).toMatch(/background:\s*Highlight/);
  expect(ruleIn(forced, ".kanban-needs-option[data-recommended]")).toMatch(
    /border-color:\s*CanvasText/,
  );
});

test("the old attention line is gone, replaced by the section's own heading", () => {
  expect(css).not.toMatch(/\.kanban-attention/);
});

test("a reader fold's summary has spacing above it and a pointer cursor", () => {
  const summary = ruleIn(css, ".kanban-reader-fold > summary");
  expect(summary).toMatch(/margin-top:\s*8px/);
  expect(summary).toMatch(/cursor:\s*pointer/);
});

test("an opened reader fold keeps the reader's 68ch, 16px article text", () => {
  const body = ruleIn(css, ".vault-note-content > .kanban-reader-fold > .md");
  expect(body).toMatch(/max-width:\s*68ch;/);
  expect(body).toMatch(/font-size:\s*16px;/);
  expect(body).toMatch(/overflow-wrap:\s*anywhere;/);
});

test("the message preview's pre wraps and shows the whole message, with no height cap", () => {
  const pre = ruleIn(css, ".kanban-needs-preview pre");
  expect(pre).toMatch(/white-space:\s*pre-wrap/);
  expect(pre).toMatch(/overflow-wrap:\s*anywhere/);
  expect(pre).toMatch(/overflow:\s*auto/);
  expect(pre).not.toMatch(/max-height/);
});
