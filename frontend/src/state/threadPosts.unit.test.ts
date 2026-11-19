import { describe, expect, it } from "vitest";
import { isStepLabel, latestNote, NOTE_CHARS, noteLine, threadPost } from "./threadPosts";
import type { Block, NoticeBlock, TextBlock, Turn } from "./view";

function turn(blocks: Block[], extra: Partial<Turn> = {}): Turn {
  return {
    id: "turn-1",
    user: null,
    blocks,
    status: "running",
    usage: null,
    durationMs: 0,
    history: false,
    ...extra,
  };
}

function text(id: string, value: string, extra: Partial<TextBlock> = {}): TextBlock {
  return { kind: "text", id, text: value, done: true, ...extra };
}

function thinking(id = "thinking"): Block {
  return { kind: "thinking", id, text: "", done: true, durationMs: 1 };
}

function tools(id = "tools"): Block {
  return { kind: "tools", id, rows: [] };
}

function routing(id = "routing"): Block {
  return {
    kind: "routing",
    id,
    agent: "helper",
    model: "",
    description: "",
    reason: "",
    status: "completed",
    tools: [],
  };
}

function notice(id: string, tone: NoticeBlock["tone"]): NoticeBlock {
  return { kind: "notice", id, tone, subject: "subject", detail: "detail" };
}

const PACKET =
  'VIKI_RESULT {"to":null,"objective":"o","artifacts":[],"decisions":[],"questions":[],' +
  '"next_gate":"work","criteria":[],"evidence":[],"verdict":null,"progress":"p"}';

describe("threadPost", () => {
  it("1: strips the trailing directive; later text is the post, earlier text is a note", () => {
    const source = turn(
      [
        thinking(),
        text("note-1", "I'll check the spec."),
        tools(),
        text("reply-1", "Shipped X\nVIKI_DONE"),
      ],
      { id: "turn-1", status: "success" },
    );
    const { post, notes } = threadPost(source);
    expect(post.blocks).toEqual([text("reply-1", "Shipped X")]);
    expect(post.id).toBe(source.id);
    expect(post.status).toBe(source.status);
    expect(notes).toEqual([{ id: "note-1", text: "I'll check the spec." }]);
  });

  it("2: a text-only turn keeps every block as the post and has no notes", () => {
    const source = turn([text("a", "Hi"), text("b", "there")], { status: "success" });
    const { post, notes } = threadPost(source);
    expect(post.blocks).toEqual([text("a", "Hi"), text("b", "there")]);
    expect(notes).toEqual([]);
  });

  it("3: a trailing hook notice after the reply is dropped from the post", () => {
    const source = turn([tools(), text("f", "Final"), notice("hook-1", "hook")], {
      status: "success",
    });
    const { post } = threadPost(source);
    expect(post.blocks).toEqual([text("f", "Final")]);
  });

  it("4: fallback - a turn ending on tools posts its last earlier text", () => {
    const source = turn([text("p", "Plan"), tools()], { status: "success" });
    const { post, notes } = threadPost(source);
    expect(post.blocks).toEqual([text("p", "Plan")]);
    expect(notes).toEqual([]);
  });

  it("4b: fallback - a directive-only reply strips to empty, the summary posts instead", () => {
    const source = turn([text("s", "Summary of work"), tools(), text("r", PACKET)], {
      status: "success",
    });
    const { post, notes } = threadPost(source);
    expect(post.blocks).toEqual([text("s", "Summary of work")]);
    expect(notes).toEqual([]);
  });

  it("4c: tools only - nothing to post, no notes", () => {
    const source = turn([tools()], { status: "success" });
    const { post, notes } = threadPost(source);
    expect(post.blocks).toEqual([]);
    expect(notes).toEqual([]);
  });

  it("5: a routing block cuts notes from the post the same as tools", () => {
    const source = turn([text("ask", "Ask"), routing(), text("res", "Result")], {
      status: "success",
    });
    const { post, notes } = threadPost(source);
    expect(post.blocks).toEqual([text("res", "Result")]);
    expect(notes).toEqual([{ id: "ask", text: "Ask" }]);
  });

  it("6: an error notice stays in the post; a blank trailing text block is dropped", () => {
    const source = turn([tools(), text("blank", "   "), notice("err", "error")], {
      status: "success",
    });
    const { post, notes } = threadPost(source);
    expect(post.blocks).toEqual([notice("err", "error")]);
    expect(notes).toEqual([]);
  });

  it("9: never mutates the turn it is given", () => {
    const source = turn(
      [
        thinking(),
        text("note-1", "I'll check the spec."),
        tools(),
        text("reply-1", "Shipped X\nVIKI_DONE"),
      ],
      { id: "turn-9", status: "success" },
    );
    const before = structuredClone(source);
    threadPost(source);
    expect(source).toEqual(before);
  });
});

describe("noteLine", () => {
  it("7a: collapses whitespace runs to one space and trims, from the first non-blank line", () => {
    expect(noteLine("  a   b \nc")).toBe("a b");
  });

  it("7b: truncates a long line to NOTE_CHARS with an ellipsis", () => {
    const result = noteLine("a".repeat(300));
    expect(result).toHaveLength(NOTE_CHARS);
    expect(result.endsWith("…")).toBe(true);
  });

  it("7c: a bare VIKI_DONE line is not a note", () => {
    expect(noteLine("VIKI_DONE")).toBe("");
  });
});

describe("latestNote", () => {
  it("8a: the last done text block's note, while a later block still streams", () => {
    const source = turn(
      [text("a", "Step 1", { done: true }), tools(), text("b", "Wri", { done: false })],
      { status: "running" },
    );
    expect(latestNote(source)).toBe("Step 1");
  });

  it("8b: a done block holding only a VIKI_RESULT packet has no note", () => {
    const source = turn([text("a", PACKET, { done: true })]);
    expect(latestNote(source)).toBeNull();
  });
});

describe("isStepLabel", () => {
  it("10: matches a tool/agent step label, not a presence label", () => {
    expect(isStepLabel("Using Read")).toBe(true);
    expect(isStepLabel("Using a tool")).toBe(true);
    expect(isStepLabel("Working with an agent")).toBe(true);
    expect(isStepLabel("Thinking")).toBe(false);
    expect(isStepLabel("Writing")).toBe(false);
    expect(isStepLabel("Waiting for your answer")).toBe(false);
  });
});
