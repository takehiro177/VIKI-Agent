import { describe, expect, it } from "vitest";
import {
  askText,
  HELP_GROUPS,
  HELP_TOPICS,
  loadWhatsNew,
  searchHelp,
  TOUR_REGIONS,
  tourSteps,
} from "./helpTopics";

describe("HELP_TOPICS", () => {
  it("has the 17 topics in order, titled and grouped", () => {
    expect(HELP_TOPICS.map((t) => t.id)).toEqual([
      "welcome",
      "getting-started",
      "chats",
      "approvals",
      "sources",
      "vault",
      "code",
      "agents",
      "decision",
      "routines",
      "messaging",
      "voice",
      "settings",
      "data",
      "keyboard-shortcuts",
      "troubleshooting",
      "glossary",
    ]);
    expect(HELP_TOPICS.map((t) => t.title)).toEqual([
      "Welcome to VIKI Agent",
      "The window at a glance",
      "Chats and tabs",
      "Approvals and permissions",
      "What VIKI read",
      "Vault",
      "Code",
      "Agents, skills and context",
      "Decision",
      "Routines",
      "Bots and messaging",
      "Voice",
      "Settings",
      "Your data and privacy",
      "Keyboard shortcuts",
      "Troubleshooting",
      "Glossary",
    ]);
    expect(HELP_TOPICS.map((t) => t.group)).toEqual([
      "Start here",
      "Start here",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Using VIKI Agent",
      "Reference",
      "Reference",
      "Reference",
    ]);
  });

  it("orders HELP_GROUPS Start here, Using VIKI Agent, Reference", () => {
    expect(HELP_GROUPS).toEqual(["Start here", "Using VIKI Agent", "Reference"]);
  });

  it("marks the tour and diagnostics actions, and no others", () => {
    const actions = Object.fromEntries(HELP_TOPICS.map((t) => [t.id, t.action]));
    expect(actions.welcome).toBe("tour");
    expect(actions["getting-started"]).toBe("tour");
    expect(actions.troubleshooting).toBe("diagnostics");
    for (const id of [
      "chats",
      "approvals",
      "sources",
      "vault",
      "code",
      "agents",
      "decision",
      "routines",
      "messaging",
      "voice",
      "settings",
      "data",
      "keyboard-shortcuts",
      "glossary",
    ]) {
      expect(actions[id]).toBeUndefined();
    }
  });

  it("gives every topic a plain, non-empty lead of at most 160 characters", () => {
    for (const topic of HELP_TOPICS) {
      expect(topic.lead.length, topic.id).toBeGreaterThan(0);
      expect(topic.lead.length, topic.id).toBeLessThanOrEqual(160);
      expect(topic.lead, topic.id).not.toMatch(/[*`[]/);
      expect(topic.lead.startsWith("#"), topic.id).toBe(false);
    }
  });

  it("gives every topic 2-4 related ids, each real, never itself, never repeated", () => {
    const ids = new Set(HELP_TOPICS.map((t) => t.id));
    for (const topic of HELP_TOPICS) {
      expect(topic.related.length, topic.id).toBeGreaterThanOrEqual(2);
      expect(topic.related.length, topic.id).toBeLessThanOrEqual(4);
      expect(topic.related, topic.id).not.toContain(topic.id);
      expect(new Set(topic.related).size, topic.id).toBe(topic.related.length);
      for (const relatedId of topic.related) {
        expect(ids.has(relatedId), `${topic.id} -> ${relatedId}`).toBe(true);
      }
    }
  });

  it("carries each page's full body", () => {
    for (const topic of HELP_TOPICS) {
      expect(topic.body.length).toBeGreaterThan(0);
      expect(topic.body).toContain(topic.title);
    }
  });
});

describe("searchHelp", () => {
  it("trims and lowercases the query", () => {
    expect(searchHelp("  VAULT  ").some((h) => h.id === "vault")).toBe(true);
  });

  it("matches a topic's title before its body", () => {
    const hit = searchHelp("routines").find((h) => h.id === "routines");
    expect(hit?.snippet).toBe("Routines");
  });

  it("matches inside the body when the title does not match", () => {
    // Built from HELP_TOPICS at test time rather than a hardcoded phrase, so this stays true
    // whatever the topics say: any topic with a long enough body has a middle slice that is
    // not part of its own (short) title.
    const topic = HELP_TOPICS.find((t) => t.body.length > 300);
    if (!topic) throw new Error("expected a topic with a body longer than 300 characters");
    const mid = Math.floor(topic.body.length / 2);
    const phrase = topic.body.slice(mid, mid + 30);
    expect(topic.title.toLowerCase()).not.toContain(phrase.toLowerCase());
    expect(searchHelp(phrase).some((h) => h.id === topic.id)).toBe(true);
  });

  it("returns a snippet of up to 60 characters either side, with an ellipsis where cut", () => {
    const topic = HELP_TOPICS.find((t) => t.body.length > 300);
    if (!topic) throw new Error("expected a topic with a body longer than 300 characters");
    const mid = Math.floor(topic.body.length / 2);
    const phrase = topic.body.slice(mid, mid + 30);
    const hit = searchHelp(phrase).find((h) => h.id === topic.id);
    expect(hit).toBeDefined();
    expect(hit?.snippet.startsWith("…")).toBe(true);
    expect(hit?.snippet.endsWith("…")).toBe(true);
    expect(hit?.snippet).toContain(phrase);
  });

  it("returns no hits for an empty or blank query", () => {
    expect(searchHelp("")).toEqual([]);
    expect(searchHelp("   ")).toEqual([]);
  });

  it("returns no hits when nothing matches", () => {
    expect(searchHelp("zzz_not_in_any_topic")).toEqual([]);
  });

  it("caps hits at 20", () => {
    expect(searchHelp("a").length).toBeLessThanOrEqual(20);
  });
});

describe("loadWhatsNew", () => {
  it("loads current public changes with their limits and review steps", async () => {
    const { default: notes } = await loadWhatsNew();
    expect(notes).toContain("\n## VIKI Agent 0.2.0\n");
    expect(notes.indexOf("## VIKI Agent 0.2.0")).toBeLessThan(notes.indexOf("## VIKI Agent 0.1.0"));
    for (const feature of [
      "Bots learn only what you approve",
      "Kanban answers",
      "More chats at once",
      "Bot usage",
      "Starter updates",
    ]) {
      expect(notes).toContain(`**${feature}.**`);
    }
    expect(notes).toContain("\n## VIKI Agent 0.1.0\n");
    for (const feature of [
      "Your own Claude Code",
      "Bring your own model",
      "Vault",
      "Decision",
      "Bots and Kanban",
      "Setup",
    ]) {
      expect(notes).toContain(`**${feature}.**`);
    }
    for (const fact of [
      "asks for your permission with **Allow once** and **Deny**",
      "add a connection in **Settings › Providers**",
      "In an ordinary vault on a supported drive",
      "Several grants start as **Allowed**, so review them first",
      "Each Bot keeps its own memory",
    ]) {
      expect(notes).toContain(fact);
    }
  });

  it("does not load maintainer milestones, test counts or review records", async () => {
    const { default: notes } = await loadWhatsNew();
    expect(notes).toMatch(/^# What's new\n/);
    expect(notes).not.toMatch(/\bF\d+\b|S5-a|B0\/B1|Milestone|replay script|study/i);
    expect(notes).not.toMatch(/Verified green|\d[\d,]* (?:Python|frontend) tests|Ten-lens/i);
    expect(notes).not.toContain("file-disjoint implementers");
  });
});

describe("TOUR_REGIONS", () => {
  it("lists the seven tour regions", () => {
    expect(TOUR_REGIONS).toEqual([
      "left-rail",
      "new-session",
      "composer",
      "conversation",
      "right-rail",
      "topbar",
      "statusbar",
    ]);
  });
});

describe("tourSteps", () => {
  it("splits `## ` sections into title/body pairs, ignoring any preamble", () => {
    const md = "# Intro\npreamble\n\n## One\nbody one\nmore one\n\n## Two\nbody two\n";
    expect(tourSteps(md)).toEqual([
      { title: "One", body: "body one\nmore one" },
      { title: "Two", body: "body two" },
    ]);
  });

  it(`gives the getting-started page exactly ${TOUR_REGIONS.length} steps`, () => {
    const gettingStarted = HELP_TOPICS.find((t) => t.id === "getting-started");
    expect(gettingStarted).toBeDefined();
    expect(tourSteps(gettingStarted?.body ?? "")).toHaveLength(TOUR_REGIONS.length);
  });
});

describe("askText", () => {
  it("builds the ask-VIKI draft in order", () => {
    const text = askText({
      title: "Vault",
      excerpt: "Some excerpt.",
      question: "How do I restore a note?",
    });
    expect(text).toBe(
      [
        "I have a question about VIKI Agent.",
        "Help page: Vault",
        "```\nSome excerpt.\n```",
        "My question: How do I restore a note?",
      ].join("\n\n"),
    );
  });

  it("cuts the excerpt to 4,000 code points", () => {
    const long = "x".repeat(5000);
    const text = askText({ title: "T", excerpt: long, question: "Q?" });
    const fenced = /```\n([\s\S]*)\n```/.exec(text);
    expect(fenced).not.toBeNull();
    expect(Array.from(fenced?.[1] ?? "").length).toBe(4000);
  });

  it("widens the fence past a backtick run already in the excerpt", () => {
    const text = askText({ title: "T", excerpt: "```js\ncode\n```", question: "Q?" });
    expect(text).toContain("````\n```js\ncode\n```\n````");
  });
});
