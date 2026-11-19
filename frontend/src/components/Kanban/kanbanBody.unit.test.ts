import { describe, expect, it } from "vitest";
import { answerCard, type CardAnswer, cardDate, parseCardBody, readCard } from "./kanbanBody";

const SHA = "a".repeat(64);
const toCrlf = (text: string) => `\uFEFF${text.replace(/\n/g, "\r\n")}`;

// The literal starter/_templates/card.md template (S/spec-card.md), byte for byte.
const BLANK_CARD = `---
type: card
status: todo
created: {{date}}
modified: {{date}}
up: "[[<project overview>]]"
tags: [owner/<seat>]
author: viki
---

# <Outcome in plain words, about 60 characters>
<!-- This heading is the card's title: add no title: line above. Keep tags: on one line. -->

## Summary
What: <what this card is for, in one plain sentence>
Now: <where it stands; rewrite it each time a seat stops>

## Needs you
<!-- Only while the card is blocked or in review. One question, then options such as:
- A. <label> (recommended): <what happens>
- B. <label>: <what happens>
No options when the owner must give an input instead. The answer goes under the question: **Owner, YYYY-MM-DD:** <their words>. -->

## Done when
- [ ] <checkable item>

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->

## Check
<!-- The checker's verdict, pasted whole. -->
`;

// A realistic filled-in blocked card: options (recommended + plain + effectless), a nested
// done-when item that must not count, and two Verdict lines (last one must win).
const FILLED_CARD = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-17
up: "[[kanban-needs-you]]"
tags: [owner/engineer]
author: viki
---

# Ship the beta behind a flag

## Summary
What: Decide whether the beta ships today.
Now: Tests are green; waiting on the call.

## Needs you
Do we ship the beta now?
- A. Ship it (recommended): goes live today
- B. Wait: one more review cycle
- C. Cancel this release

## Done when
- [x] Tests green
- [ ] Owner decides
  - [x] nested sub-item should not count
- [X] Changelog drafted

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->
- 2026-11-17 · engineer · blocked · this card · waits on the owner · none

## Check
Verdict: FAIL
Ran again after a fix.
Verdict: PASS
`;

describe("parseCardBody", () => {
  it("parses the blank template: comment-only Needs you is empty, not missing", () => {
    const body = parseCardBody(BLANK_CARD);
    expect(body.what).toBe("<what this card is for, in one plain sentence>");
    expect(body.now).toBe("<where it stands; rewrite it each time a seat stops>");
    expect(body.needs).toEqual({ question: "", options: [] });
    expect(body.done).toEqual({ done: 0, total: 1 });
    expect(body.verdict).toBeNull();
  });

  it("parses a filled card: question, options, recommended, effectless option", () => {
    const body = parseCardBody(FILLED_CARD);
    expect(body.what).toBe("Decide whether the beta ships today.");
    expect(body.now).toBe("Tests are green; waiting on the call.");
    expect(body.needs?.question).toBe("Do we ship the beta now?");
    expect(body.needs?.options).toEqual([
      { letter: "A", label: "Ship it", recommended: true, effect: "goes live today" },
      { letter: "B", label: "Wait", recommended: false, effect: "one more review cycle" },
      { letter: "C", label: "Cancel this release", recommended: false, effect: null },
    ]);
  });

  it("counts only top-level done-when items (N of M), excluding a nested one", () => {
    expect(parseCardBody(FILLED_CARD).done).toEqual({ done: 2, total: 3 });
  });

  it("takes the last Verdict line, not the first", () => {
    expect(parseCardBody(FILLED_CARD).verdict).toBe("PASS");
  });

  it("matches (recommended) case-insensitively and strips it from the label", () => {
    const card = `## Needs you
Pick one.
- A. First (RECOMMENDED): details here
`;
    expect(parseCardBody(card).needs?.options).toEqual([
      { letter: "A", label: "First", recommended: true, effect: "details here" },
    ]);
  });

  it("reads legacy free text (no option lines) as a question with no options", () => {
    const card = `## Needs decision
Should we rename the widget before launch? Marketing wants "Pulse", eng wants to keep "Beacon".
`;
    expect(parseCardBody(card).needs).toEqual({
      question:
        'Should we rename the widget before launch? Marketing wants "Pulse", eng wants to keep "Beacon".',
      options: [],
    });
  });

  it("returns null, not empty, for sections missing entirely", () => {
    const card = `## Summary
What: Just a note.
`;
    const body = parseCardBody(card);
    expect(body.what).toBe("Just a note.");
    expect(body.now).toBeNull();
    expect(body.needs).toBeNull();
    expect(body.done).toBeNull();
    expect(body.verdict).toBeNull();
  });

  it("returns done: null when the only item is inside a comment (0 -> null)", () => {
    const card = `## Done when
<!-- none yet -->
`;
    expect(parseCardBody(card).done).toBeNull();
  });

  it("parses the same whether the source is LF or BOM+CRLF", () => {
    expect(parseCardBody(toCrlf(FILLED_CARD))).toEqual(parseCardBody(FILLED_CARD));
  });

  it("keeps text after an unterminated comment opener instead of losing it (F1)", () => {
    const card = `## Summary
What: a stray <!-- opener with no close
Now: still here
`;
    const body = parseCardBody(card);
    expect(body.what).toBe("a stray <!-- opener with no close");
    expect(body.now).toBe("still here");
  });

  it("does not misread a long run of spaces before the colon as recommended (F1)", () => {
    const spaces = " ".repeat(2000);
    const card = `## Needs you
Q?
- A. x${spaces}y: z
`;
    expect(parseCardBody(card).needs?.options).toEqual([
      { letter: "A", label: `x${spaces}y`, recommended: false, effect: "z" },
    ]);
  });

  it("treats a bold legacy option line as free text: no options dropped (F2a)", () => {
    const card = `## Needs decision
Should the change be committed to \`main\`?

Options:
- **A. Approve: commit all of it as one commit (recommended).** The checker passed.
- B. Approve, but as two commits: the test fix first, then the feature.
- C. Revise first. These known limits are open:
  1. Limit one.
  2. Limit two.
- D. Do not commit.

The push stays a separate later step.
`;
    const body = parseCardBody(card);
    expect(body.needs?.options).toEqual([]);
    expect(body.needs?.question).toContain(
      "- **A. Approve: commit all of it as one commit (recommended).** The checker passed.",
    );
    expect(body.needs?.question).toContain(
      "- B. Approve, but as two commits: the test fix first, then the feature.",
    );
    expect(body.needs?.question).toContain("1. Limit one.");
    expect(body.needs?.question).toContain("- D. Do not commit.");
  });

  it("treats a plain option with an indented sub-item as free text (F2b)", () => {
    const card = `## Needs you
Pick one.
- A. First: ok
- B. Second, with limits:
  1. Limit one.
- C. Third: ok
`;
    expect(parseCardBody(card).needs?.options).toEqual([]);
  });

  it("a stray backtick-quoted <!-- in Summary does not eat into Needs you (F3c)", () => {
    const card = `## Summary
What: Remove the stray \`<!--\` marker from the export.
Now: Two ways to do it.

## Needs you
<!-- Only while the card is blocked or in review. One question, then options such as:
- A. <label> (recommended): what happens
No options when the owner must give an input instead. -->
Which way?
- A. Regex (recommended): fast
- B. Parser: safe

## Done when
- [ ] one
`;
    const body = parseCardBody(card);
    expect(body.what).toBe("Remove the stray `<!--` marker from the export.");
    expect(body.now).toBe("Two ways to do it.");
    expect(body.needs?.question).toBe("Which way?");
    expect(body.needs?.options).toEqual([
      { letter: "A", label: "Regex", recommended: true, effect: "fast" },
      { letter: "B", label: "Parser", recommended: false, effect: "safe" },
    ]);
  });
});

describe("parseCardBody and answerCard agree on which Needs section is live (F3)", () => {
  it("answers the Needs you section even with a legacy Needs decision above it (F3a)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs decision
Old question, kept as a record.
- A. Old kept: stays

## Needs you
New question?
- A. New: goes
`;
    const body = parseCardBody(card);
    expect(body.needs?.question).toBe("New question?");
    const option = body.needs?.options[0];
    if (!option) throw new Error("expected option A");
    const result = answerCard(card, { kind: "decide", option }, "2026-11-18", null);
    expect(result).toContain(
      "## Needs decision\nOld question, kept as a record.\n- A. Old kept: stays\n",
    );
    expect(result).toContain("## Needs you\nNew question?\n**Owner, 2026-11-18:** A. New\n");
  });

  it("answers the first of two Needs you sections, leaving the second as written (F3b)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
Old question?
- A. Old: one

## Done when
- [ ] one

## Needs you
New question?
- A. New: two
- B. Other: three
`;
    const body = parseCardBody(card);
    expect(body.needs?.question).toBe("Old question?");
    const option = body.needs?.options[0];
    if (!option) throw new Error("expected option A");
    const result = answerCard(card, { kind: "decide", option }, "2026-11-18", null);
    expect(result).toContain("## Needs you\nOld question?\n**Owner, 2026-11-18:** A. Old\n");
    expect(result).toContain("## Needs you\nNew question?\n- A. New: two\n- B. Other: three\n");
  });

  it("a '#' comment line inside a fenced code block does not end the section (F3d)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
Which default goes in the config?
\`\`\`yaml
# default
retries: 3
\`\`\`
- A. Keep 3 (recommended): no change
- B. Use 5: slower failures

## Done when
- [ ] one
`;
    const body = parseCardBody(card);
    expect(body.needs?.options).toEqual([
      { letter: "A", label: "Keep 3", recommended: true, effect: "no change" },
      { letter: "B", label: "Use 5", recommended: false, effect: "slower failures" },
    ]);
    const result = answerCard(card, { kind: "reply", text: "Keep 3." }, "2026-11-18", null);
    expect(result).toContain("```yaml\n# default\nretries: 3\n```\n");
    expect(result).toContain("```\n**Owner, 2026-11-18:** Keep 3.\n");
    expect(result).not.toContain("- A. Keep 3");
  });

  it("a ``` line inside front matter does not fool the writer into missing the real sections (F2-F2a)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
note: |
  \`\`\`not actually code
---

## Needs you
Ship?
- A. Ship: now

## Done when
- [ ] one

## Handoff
- 2026-11-18 · engineer · blocked · this card · waits · none
`;
    const body = parseCardBody(card);
    expect(body.needs?.question).toBe("Ship?");
    const option = body.needs?.options[0];
    if (!option) throw new Error("expected option A");
    const result = answerCard(card, { kind: "decide", option }, "2026-11-19", "engineer");
    expect(result).toContain("## Needs you\nShip?\n**Owner, 2026-11-19:** A. Ship\n");
    expect(result.match(/## Needs you/g)).toHaveLength(1);
    expect(result.match(/## Handoff/g)).toHaveLength(1);
  });
});

describe("readCard", () => {
  it("reads a valid note into source/sha256/body", () => {
    const result = readCard({
      note: { ref: "kanban/x.md", text: FILLED_CARD, source: FILLED_CARD, sha256: SHA },
    });
    expect(result?.source).toBe(FILLED_CARD);
    expect(result?.sha256).toBe(SHA);
    expect(result?.body.what).toBe("Decide whether the beta ships today.");
  });

  it("returns null when source is missing", () => {
    expect(readCard({ note: { sha256: SHA } })).toBeNull();
  });

  it("returns null when sha256 is not 64 lowercase hex", () => {
    expect(readCard({ note: { source: "x", sha256: "A".repeat(64) } })).toBeNull();
    expect(readCard({ note: { source: "x", sha256: "a".repeat(63) } })).toBeNull();
  });

  it("returns null when note is missing or not an object", () => {
    expect(readCard({})).toBeNull();
    expect(readCard({ note: "x" })).toBeNull();
  });
});

describe("cardDate", () => {
  it("formats a local timestamp as YYYY-MM-DD, zero-padded", () => {
    expect(cardDate(new Date(2026, 10, 18, 23, 59).getTime())).toBe("2026-11-18");
    expect(cardDate(new Date(2026, 0, 5, 0, 0).getTime())).toBe("2026-01-05");
  });
});

describe("answerCard", () => {
  const BLOCKED = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
up: "[[kanban-needs-you]]"
tags: [owner/engineer]
author: viki
---

# Ship the beta behind a flag

## Summary
What: Decide whether the beta ships today.
Now: Tests are green; waiting on the call.

## Needs you
Do we ship the beta now?
- A. Ship it (recommended): goes live today
- B. Wait: one more review cycle

## Done when
- [x] Tests green
- [ ] Owner decides

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->
- 2026-11-15 · engineer · blocked · this card · waits on the owner · none

## Check
Verdict: PASS
`;

  const REVIEW = `---
type: card
status: review
created: 2026-11-10
modified: 2026-11-16
up: "[[kanban-needs-you]]"
tags: [owner/engineer]
author: viki
---

# Merge the parser PR

## Summary
What: Ready to merge PR #42?
Now: Checks are green.

## Needs you
Ready to merge PR #42?

## Done when
- [x] Tests green
- [x] Review requested

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->
- 2026-11-16 · engineer · review · this card · ready for review · none

## Check
Verdict: PASS
`;

  function golden(source: string, answer: CardAnswer, seat: string | null, expected: string) {
    expect(answerCard(source, answer, "2026-11-18", seat)).toBe(expected);
    expect(answerCard(toCrlf(source), answer, "2026-11-18", seat)).toBe(toCrlf(expected));
  }

  it("decide: drops both options, keeps the question, appends the owner line", () => {
    golden(
      BLOCKED,
      {
        kind: "decide",
        option: { letter: "A", label: "Ship it", recommended: true, effect: "goes live today" },
      },
      "engineer",
      `---
type: card
status: todo
created: 2026-11-10
modified: 2026-11-18
up: "[[kanban-needs-you]]"
tags: [owner/engineer]
author: viki
---

# Ship the beta behind a flag

## Summary
What: Decide whether the beta ships today.
Now: Tests are green; waiting on the call.

## Needs you
Do we ship the beta now?
**Owner, 2026-11-18:** A. Ship it

## Done when
- [x] Tests green
- [ ] Owner decides

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->
- 2026-11-18 · owner · todo · this card · answered on Kanban: A. Ship it; engineer continues · none
- 2026-11-15 · engineer · blocked · this card · waits on the owner · none

## Check
Verdict: PASS
`,
    );
  });

  it("reply: keeps a multi-line owner line, flattens it to spaces in Handoff, no seat omits continues", () => {
    golden(
      BLOCKED,
      { kind: "reply", text: "Let's wait.\nNeed one more review." },
      null,
      `---
type: card
status: todo
created: 2026-11-10
modified: 2026-11-18
up: "[[kanban-needs-you]]"
tags: [owner/engineer]
author: viki
---

# Ship the beta behind a flag

## Summary
What: Decide whether the beta ships today.
Now: Tests are green; waiting on the call.

## Needs you
Do we ship the beta now?
**Owner, 2026-11-18:** Let's wait.
Need one more review.

## Done when
- [x] Tests green
- [ ] Owner decides

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->
- 2026-11-18 · owner · todo · this card · answered on Kanban: Let's wait. Need one more review. · none
- 2026-11-15 · engineer · blocked · this card · waits on the owner · none

## Check
Verdict: PASS
`,
    );
  });

  it("approve: Needs you body becomes exactly one line, status -> done, seat ignored", () => {
    golden(
      REVIEW,
      { kind: "approve" },
      "engineer",
      `---
type: card
status: done
created: 2026-11-10
modified: 2026-11-18
up: "[[kanban-needs-you]]"
tags: [owner/engineer]
author: viki
---

# Merge the parser PR

## Summary
What: Ready to merge PR #42?
Now: Checks are green.

## Needs you
**Owner, 2026-11-18:** Approved.

## Done when
- [x] Tests green
- [x] Review requested

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->
- 2026-11-18 · owner · done · this card · approved on Kanban · none
- 2026-11-16 · engineer · review · this card · ready for review · none

## Check
Verdict: PASS
`,
    );
  });

  it("send back: Needs you body is the note, status stays todo, seat adds continues", () => {
    golden(
      REVIEW,
      { kind: "send_back", note: "Please add a test for the empty-input case." },
      "engineer",
      `---
type: card
status: todo
created: 2026-11-10
modified: 2026-11-18
up: "[[kanban-needs-you]]"
tags: [owner/engineer]
author: viki
---

# Merge the parser PR

## Summary
What: Ready to merge PR #42?
Now: Checks are green.

## Needs you
**Owner, 2026-11-18:** Sent back: Please add a test for the empty-input case.

## Done when
- [x] Tests green
- [x] Review requested

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->
- 2026-11-18 · owner · todo · this card · sent back on Kanban: Please add a test for the empty-input case.; engineer continues · none
- 2026-11-16 · engineer · review · this card · ready for review · none

## Check
Verdict: PASS
`,
    );
  });

  it("throws when front matter has no status: line", () => {
    const noStatus = `---
type: card
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
Ready?

## Done when
- [ ] thing
`;
    expect(() => answerCard(noStatus, { kind: "approve" }, "2026-11-18", null)).toThrow();
  });

  it("throws when there is no front matter at all", () => {
    expect(() =>
      answerCard("## Needs you\nHi\n", { kind: "approve" }, "2026-11-18", null),
    ).toThrow();
  });

  it("inserts a missing Needs you section right before Done when", () => {
    const noNeeds = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Done when
- [ ] thing

## Handoff
- 2026-11-15 · engineer · blocked · this card · note · none
`;
    const result = answerCard(
      noNeeds,
      { kind: "reply", text: "Go ahead." },
      "2026-11-18",
      "engineer",
    );
    expect(result).toBe(`---
type: card
status: todo
created: 2026-11-10
modified: 2026-11-18
---

## Needs you
**Owner, 2026-11-18:** Go ahead.

## Done when
- [ ] thing

## Handoff
- 2026-11-18 · owner · todo · this card · answered on Kanban: Go ahead.; engineer continues · none
- 2026-11-15 · engineer · blocked · this card · note · none
`);
  });

  it("decide/reply on a truly empty Needs you section inserts right after the heading", () => {
    const empty = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
## Done when
- [ ] thing
`;
    const result = answerCard(empty, { kind: "reply", text: "Go." }, "2026-11-18", null);
    expect(result).toBe(`---
type: card
status: todo
created: 2026-11-10
modified: 2026-11-18
---

## Needs you
**Owner, 2026-11-18:** Go.
## Done when
- [ ] thing

## Handoff
- 2026-11-18 · owner · todo · this card · answered on Kanban: Go. · none
`);
  });

  it("approve with no following heading adds no forced blank line, and still appends Handoff", () => {
    const lastSection = `---
type: card
status: review
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
Ready?
`;
    const result = answerCard(lastSection, { kind: "approve" }, "2026-11-18", null);
    expect(result).toBe(`---
type: card
status: done
created: 2026-11-10
modified: 2026-11-18
---

## Needs you
**Owner, 2026-11-18:** Approved.

## Handoff
- 2026-11-18 · owner · done · this card · approved on Kanban · none
`);
  });

  it("appends a missing Needs you section at the end when Done when is also missing", () => {
    const neither = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Check
Verdict: PASS
`;
    const option = { letter: "A", label: "Go", recommended: false, effect: null };
    const result = answerCard(neither, { kind: "decide", option }, "2026-11-18", null);
    expect(result).toBe(`---
type: card
status: todo
created: 2026-11-10
modified: 2026-11-18
---

## Check
Verdict: PASS

## Needs you
**Owner, 2026-11-18:** A. Go

## Handoff
- 2026-11-18 · owner · todo · this card · answered on Kanban: A. Go · none
`);
  });

  it("appends a missing Handoff section at the end", () => {
    const noHandoff = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
Ready?

## Done when
- [ ] thing
`;
    const result = answerCard(noHandoff, { kind: "approve" }, "2026-11-18", null);
    expect(result).toBe(`---
type: card
status: done
created: 2026-11-10
modified: 2026-11-18
---

## Needs you
**Owner, 2026-11-18:** Approved.

## Done when
- [ ] thing

## Handoff
- 2026-11-18 · owner · done · this card · approved on Kanban · none
`);
  });

  it("Reply on a legacy bold-option section drops nothing (F2 writer)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs decision
Should the change be committed to \`main\`?
- **A. Approve (recommended).** Notes.
- B. Wait: notes.

## Done when
- [ ] thing
`;
    const result = answerCard(card, { kind: "reply", text: "Go with B." }, "2026-11-18", null);
    expect(result).toContain("- **A. Approve (recommended).** Notes.");
    expect(result).toContain("- B. Wait: notes.");
    expect(result).toContain("**Owner, 2026-11-18:** Go with B.");
  });

  it("adds a newline before the owner line when the question had none (F4)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs decision
Ship it now?`;
    const result = answerCard(card, { kind: "reply", text: "Yes." }, "2026-11-19", null);
    expect(result).toContain("Ship it now?\n**Owner, 2026-11-19:** Yes.\n");
    expect(result).not.toContain("Ship it now?**Owner");
  });

  it("adds a newline before the handoff line when the EOF comment had none (F4)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
Ready?

## Handoff
<!-- Newest first. -->`;
    const result = answerCard(card, { kind: "approve" }, "2026-11-19", null);
    expect(result).toContain(
      "<!-- Newest first. -->\n- 2026-11-19 · owner · done · this card · approved on Kanban · none\n",
    );
    expect(result).not.toContain("-->- 2026-11-19");
  });

  it("Decide keeps the template comment's example options untouched (F5, kills M1)", () => {
    const template = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-17
up: "[[<project overview>]]"
tags: [owner/<seat>]
author: viki
---

# Ship the beta behind a flag
<!-- This heading is the card's title: add no title: line above. Keep tags: on one line. -->

## Summary
What: Decide whether the beta ships today.
Now: Tests are green; waiting on the call.

## Needs you
<!-- Only while the card is blocked or in review. One question, then options such as:
- A. <label> (recommended): <what happens>
- B. <label>: <what happens>
No options when the owner must give an input instead. The answer goes under the question: **Owner, YYYY-MM-DD:** <their words>. -->
Do we ship the beta now?
- A. Ship it (recommended): goes live today
- B. Wait: one more review cycle

## Done when
- [ ] <checkable item>

## Handoff
<!-- Newest first, about 200 characters each time a seat stops: YYYY-MM-DD · seat · status · notes touched · next step · blockers. -->

## Check
<!-- The checker's verdict, pasted whole. -->
`;
    const option = { letter: "A", label: "Ship it", recommended: true, effect: "goes live today" };
    const result = answerCard(template, { kind: "decide", option }, "2026-11-18", "engineer");
    expect(result).toContain("- A. <label> (recommended): <what happens>");
    expect(result).toContain("- B. <label>: <what happens>");
    expect(result).not.toContain("- A. Ship it (recommended): goes live today");
    expect(result).not.toContain("- B. Wait: one more review cycle");
    expect(result).toContain("**Owner, 2026-11-18:** A. Ship it");
  });

  it("answers an existing legacy Needs decision instead of adding a new Needs you (F5, kills M2)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Done when
- [ ] one

## Handoff
- 2026-11-17 · engineer · blocked · this card · waits on the owner · none

## Needs decision
Ship it now?
- A. Ship: today

## Check
Verdict: PASS
`;
    const option = { letter: "A", label: "Ship", recommended: false, effect: "today" };
    const result = answerCard(card, { kind: "decide", option }, "2026-11-18", "engineer");
    expect(result).toContain("## Needs decision\nShip it now?\n**Owner, 2026-11-18:** A. Ship\n");
    expect(result).not.toContain("## Needs you");
  });

  it("inserts a missing modified: line after status: (F5, kills M3)", () => {
    const noModified = `---
type: card
status: blocked
created: 2026-11-10
---

## Needs you
Ready?

## Done when
- [ ] thing
`;
    const result = answerCard(noModified, { kind: "approve" }, "2026-11-18", null);
    expect(result).toContain("status: done\nmodified: 2026-11-18\n");
  });

  it("does not crash on a Needs section with 100k+ lines (F2b)", () => {
    const filler = "line\n".repeat(150_000);
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
Q?
${filler}
## Done when
- [ ] one
`;
    let result = "";
    expect(() => {
      result = answerCard(card, { kind: "reply", text: "ok" }, "2026-11-19", null);
    }).not.toThrow();
    expect(result).toContain("**Owner, 2026-11-19:** ok");
    expect(result).toContain("## Done when\n- [ ] one\n");
  });

  it("does not glue a new Handoff heading onto a trailing whitespace-only line with no EOL (F2d)", () => {
    const card = `---
type: card
status: blocked
created: 2026-11-10
modified: 2026-11-15
---

## Needs you
Ready?

## Done when
- [ ] one
   `;
    const result = answerCard(card, { kind: "approve" }, "2026-11-19", null);
    expect(result).not.toContain("   ## Handoff");
    expect(result).toContain(
      "## Handoff\n- 2026-11-19 · owner · done · this card · approved on Kanban · none\n",
    );
  });
});
