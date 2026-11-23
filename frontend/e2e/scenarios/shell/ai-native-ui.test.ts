/** Held synthetic App states: no provider, vault, screenshot or timing sleep. */
import type { Browser, Page } from "playwright-core";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, test } from "vitest";
import * as events from "../../../src/accept/m2b/fakes";
import { GENERATION, sessionRow, snapshotData } from "../../../src/accept/m3b/fakes";
import type { ContextReport } from "../../../src/clients/conversations/contextApi";
import type { SearchHit, SearchResult, SessionSnapshot, SnapshotItem } from "../../../src/protocol";
import {
  capabilitiesFixture,
  listingFixture,
  settingsFixture,
  workspaceFixture,
} from "../../../src/testShell";
import { launchEdge, waitForText } from "../../harness";

const ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const START = [events.userMessage("Inspect synthetic examples"), events.turnStarted];
const TOOLS = [
  ...START,
  events.toolStarted("read", "Read", { head: "Read", rest: "notes/example.md" }),
];
const CONTEXT: ContextReport = {
  observed_at: "2026-10-18T01:00:00Z",
  total_tokens: 300,
  max_tokens: 1000,
  raw_max_tokens: 1000,
  percentage: 30,
  auto_compact: { enabled: true, threshold: 800, source: "cli" },
  categories: [
    { name: "Messages", tokens: 200, kind: "used" },
    { name: "System prompt", tokens: 100, kind: "used" },
    { name: "Free space", tokens: 700, kind: "free" },
  ],
  memory_files: [],
  mcp_servers: [],
  skills: { total: 0, included: 0, tokens: 0, items: [] },
  agents: [],
  reasons: {},
  hidden: { categories: 0, memory_files: 0, mcp_servers: 0, skills: 0, agents: 0 },
};
type ContextState = Partial<SessionSnapshot["data"]["state"]>;
let browser: Browser | undefined;
let server: ViteDevServer | undefined;

beforeAll(async () => {
  server = await createServer({
    server: { host: "127.0.0.1", port: 0 },
    logLevel: "silent",
  });
  await server.listen();
  browser = await launchEdge();
});
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

async function open(
  initial: events.Item[] = TOOLS,
  searchHits: SearchHit[] = [],
  routineCount = 0,
) {
  const url = server?.resolvedUrls?.local[0];
  if (!browser || !url) throw new Error("Synthetic App did not start");
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    reducedMotion: "no-preference",
    forcedColors: "none",
  });
  let settings = settingsFixture({ "onboarding.completed": true, "appearance.theme": "dark" });
  const vault = routineCount > 0 ? workspaceFixture({ policy: "existing_readonly" }) : null;
  const capabilities = capabilitiesFixture(
    vault
      ? {
          workspace: {
            active: {
              id: vault.workspace_id,
              kind: "vault",
              name: vault.name,
              policy: vault.policy,
              layout: "unknown",
              source: vault.source,
              aliased: vault.aliased,
              root_available: vault.root_available,
            },
            chat_only: false,
            selected_id: vault.workspace_id,
            selected_by: "registration",
            shared_scratch: false,
          },
        }
      : {},
  );
  let update: (items: events.Item[], state?: ContextState) => void = () => {
    throw new Error("No subscribed synthetic session");
  };
  let disconnect = () => {};
  let contextReads = 0;
  await page.routeWebSocket("**/ws", (socket) => {
    disconnect = () => socket.close({ code: 1000, reason: "Synthetic disconnect" });
    socket.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type !== "session.open") return;
      let seq = 0;
      update = (items, state = {}) => {
        const identity = {
          v: 2,
          session_id: ID,
          generation: GENERATION,
          request_id: message.request_id,
          seq: ++seq,
        };
        socket.send(
          JSON.stringify({
            ...identity,
            type: "session.snapshot",
            data: {
              ...snapshotData(),
              events: items as unknown as SnapshotItem[],
              state: { ...snapshotData().state, live: true, ...state },
            },
          }),
        );
        socket.send(JSON.stringify({ ...identity, type: "session.synced", data: {} }));
      };
      update(initial);
    });
  });
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === `/api/v2/sessions/${ID}/context`) {
      contextReads += 1;
      return route.fulfill({ json: CONTEXT });
    }
    if (path === "/api/search") {
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      const result: SearchResult = {
        hits: searchHits.filter((hit) => hit.title.toLowerCase().includes(query)),
        state: "ready",
        reason: "",
        scanned: searchHits.length,
        total: searchHits.length,
        request_id: url.searchParams.get("request_id") ?? "",
      };
      return route.fulfill({ json: result });
    }
    if (path === "/api/v2/settings" && route.request().method() === "PATCH") {
      const input = route.request().postDataJSON();
      if (input.expected_revision !== settings.revision) {
        return route.fulfill({
          status: 409,
          json: { error: { code: "stale_revision", current: settings } },
        });
      }
      const values = { ...settings.values };
      for (const [key, value] of Object.entries(input.changes)) {
        if (value === null) delete values[key];
        else values[key] = value;
      }
      settings = settingsFixture(values, { revision: settings.revision + 1 });
      return route.fulfill({
        json: { ...settings, command_id: input.command_id, replayed: false },
      });
    }
    const bodies: Record<string, unknown> = {
      "/api/sessions": {
        sessions: [
          sessionRow({ session_id: ID, title: "Synthetic activity" }),
          ...searchHits.map((hit) => sessionRow({ session_id: hit.session_id, title: hit.title })),
        ],
        state: "ready",
        reason: "",
        omitted: 0,
      },
      "/api/rails": {
        // The right rail shows no planned routines (96832932 removed that block); the agent
        // library is the long list that fills the panel.
        agents: Array.from({ length: routineCount }, (_, index) => ({
          name: `Synthetic agent ${index}`,
          description: "",
          tools: [],
          model: "",
          last_run: "",
        })),
        gates: [],
        routines: Array.from({ length: routineCount }, (_, index) => ({
          when: "07:30",
          name: `Synthetic routine ${index}`,
          planned: true,
        })),
        counts: { knowledge: 0, sources: 0, inbox: 0 },
        git: {
          state: "known",
          ref_state: "branch",
          branch: "test",
          head: "1234567",
          committed_at: "",
          dirty: 0,
        },
      },
      "/api/v2/capabilities": capabilities,
      ...(vault
        ? {
            "/api/v2/workspaces": {
              ...listingFixture([vault]),
              active_id: vault.workspace_id,
              selected_id: vault.workspace_id,
              selected_by: "registration",
              chat_only: false,
            },
          }
        : {}),
      "/api/v2/settings": settings,
      "/api/v2/session-metadata": {
        sessions: [
          {
            session_id: ID,
            title: "Synthetic activity",
            revision: 1,
            pinned: false,
            archived: false,
          },
        ],
      },
      "/api/v2/drafts": { drafts: [], next_after: null },
      "/api/v2/routines": {
        routines: [],
        scheduler: { state: "running", attended: true, paused: false },
      },
    };
    const key = /^\/api\/v2\/drafts\/(.+)$/.exec(path)?.[1];
    const body = key ? { key, revision: 0, text: "", saved_at: null } : bodies[path];
    return route.fulfill({ status: body === undefined ? 404 : 200, json: body ?? {} });
  });
  await page.goto(`${url}#token=ai-native-ui-synthetic`);
  await page.locator(`[data-session-id="${ID}"]`).click();
  await page.locator(".reply").waitFor();
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return {
    page,
    update: (items: events.Item[], state?: ContextState) => update(items, state),
    disconnect: () => disconnect(),
    contextReads: () => contextReads,
    settings: () => settings,
  };
}

async function applyAppearance(page: Page, choices: Record<string, string>) {
  await page.keyboard.press("Control+Comma");
  const panel = page.locator('[data-region="settings"]');
  await panel.waitFor();
  await page.getByRole("tab", { name: /Appearance/ }).click();
  for (const [key, value] of Object.entries(choices)) {
    await page.locator(`#appearance-${key}`).selectOption(value);
  }
  const response = page.waitForResponse(
    (result) =>
      new URL(result.url()).pathname === "/api/v2/settings" &&
      result.request().method() === "PATCH",
  );
  await panel.getByRole("button", { name: "Apply", exact: true }).click();
  expect((await response).status()).toBe(200);
  await panel.locator('.settings-page[aria-busy="false"]').waitFor();
  const back = panel.getByRole("button", { name: "Back", exact: true });
  await back.focus();
  await page.keyboard.press("Escape");
  await panel.locator('[data-page="general"][aria-selected="true"]').waitFor();
  await back.focus();
  await page.keyboard.press("Escape");
  await page.locator('[data-region="conversation"]').waitFor();
}

/** Reads actual library paint, without replacing or instrumenting either renderer. */
function motionSnapshot(baseline: { hash: number; pulse: string } | null) {
  const orb = document.querySelector(".composer-wrap .activity-orb");
  const canvas = orb?.querySelector<HTMLCanvasElement>("canvas[data-renderer='viki-tiles']");
  const host = document.querySelector(".composer-beam-host");
  const beam = document.querySelector<HTMLElement>(".composer-beam");
  const ctx = canvas?.getContext("2d");
  if (!canvas || !ctx || !host) throw new Error("Missing actual library renderer host");
  const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let hash = 2166136261;
  let painted = 0;
  let ink = 0;
  let alpha = 0;
  for (const byte of pixels) hash = Math.imul(hash ^ byte, 16777619) >>> 0;
  for (let index = 0; index < pixels.length; index += 4) {
    const opacity = pixels[index + 3] ?? 0;
    if (opacity > 0) painted += 1;
    ink += ((pixels[index] ?? 0) + (pixels[index + 1] ?? 0) + (pixels[index + 2] ?? 0)) * opacity;
    alpha += opacity;
  }
  const pulse = beam
    ? ["beam-w1", "beam-h1", "beam-x1", "beam-y1"]
        .map((field) => getComputedStyle(beam).getPropertyValue(`--${field}`).trim())
        .join("|")
    : "absent";
  if (baseline && (!beam || hash === baseline.hash || pulse === baseline.pulse)) return false;
  return {
    hash,
    pulse,
    beamMounted: beam !== null,
    painted,
    ink: alpha ? ink / alpha / 3 : 0,
    width: canvas.width,
    height: canvas.height,
    state: {
      activity: orb?.getAttribute("data-state"),
      paused: orb?.getAttribute("data-paused"),
      orbVisible: orb?.getAttribute("data-visible"),
      beamVisible: host.getAttribute("data-visible"),
      beamMotion: host.getAttribute("data-motion"),
      animationNames: beam ? getComputedStyle(beam).animationName : null,
      animationPlayStates: beam ? getComputedStyle(beam).animationPlayState : null,
      animationDurations: beam ? getComputedStyle(beam).animationDuration : null,
      animations: (beam?.getAnimations() ?? []).map((animation) => ({
        name: animation instanceof CSSAnimation ? animation.animationName : animation.id,
        playState: animation.playState,
        pending: animation.pending,
        replaceState: animation.replaceState,
        currentTime: String(animation.currentTime),
        startTime: String(animation.startTime),
        playbackRate: animation.playbackRate,
        timelineTime: String(animation.timeline?.currentTime),
        timing: animation.effect
          ? Object.fromEntries(
              Object.entries(animation.effect.getComputedTiming()).map(([key, value]) => [
                key,
                typeof value === "number" && !Number.isFinite(value) ? String(value) : value,
              ]),
            )
          : null,
      })),
      preferences: [
        "(prefers-reduced-motion: reduce)",
        "(forced-colors: active)",
        "(prefers-contrast: more)",
      ].map((query) => ({ query, matches: matchMedia(query).matches })),
      hidden: document.hidden,
    },
  };
}

async function pausedCanvasProbe(page: Page) {
  return page.evaluate(async () => {
    const orb = document.querySelector(".composer-wrap .activity-orb");
    const canvas = orb?.querySelector<HTMLCanvasElement>("canvas");
    const host = document.querySelector(".composer-beam-host");
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !host) throw new Error("Missing canvas probe target");
    const clearRect = ctx.clearRect;
    const descriptor = Object.getOwnPropertyDescriptor(ctx, "clearRect");
    const draws: Array<{ at: number; theme: string | undefined; paused: string | null }> = [];
    let resizes = 0;
    const resize = () => {
      resizes += 1;
    };
    ctx.clearRect = function (...args: Parameters<CanvasRenderingContext2D["clearRect"]>) {
      draws.push({
        at: performance.now(),
        theme: document.documentElement.dataset.theme,
        paused: orb?.getAttribute("data-paused") ?? null,
      });
      return clearRect.apply(this, args);
    };
    window.addEventListener("resize", resize);
    const read = () => {
      const beam = host.querySelector<HTMLElement>(".composer-beam");
      const bytes = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let hash = 2166136261;
      let painted = 0;
      let ink = 0;
      let alpha = 0;
      for (const byte of bytes) hash = Math.imul(hash ^ byte, 16777619) >>> 0;
      for (let index = 0; index < bytes.length; index += 4) {
        const opacity = bytes[index + 3] ?? 0;
        if (opacity > 0) painted += 1;
        ink += ((bytes[index] ?? 0) + (bytes[index + 1] ?? 0) + (bytes[index + 2] ?? 0)) * opacity;
        alpha += opacity;
      }
      return {
        bytes,
        paint: {
          hash,
          painted,
          ink: alpha ? ink / alpha / 3 : 0,
          width: canvas.width,
          height: canvas.height,
          beamMounted: beam !== null,
          pulse: beam
            ? ["beam-w1", "beam-h1", "beam-x1", "beam-y1"]
                .map((field) => getComputedStyle(beam).getPropertyValue(`--${field}`).trim())
                .join("|")
            : "absent",
        },
      };
    };
    const difference = (a: Uint8ClampedArray, b: Uint8ClampedArray) => {
      let channels = 0;
      let alphaChannels = 0;
      let maxDelta = 0;
      for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
        const delta = Math.abs((a[index] ?? 0) - (b[index] ?? 0));
        if (delta) {
          channels += 1;
          if (index % 4 === 3) alphaChannels += 1;
          maxDelta = Math.max(maxDelta, delta);
        }
      }
      return { channels, alphaChannels, maxDelta };
    };
    try {
      const before = read();
      const beforeTwin = read();
      await new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      );
      const after = read();
      const afterTwin = read();
      return {
        before: before.paint,
        after: after.paint,
        diagnostics: {
          draws,
          resizes,
          beforeTwin: {
            paint: beforeTwin.paint,
            difference: difference(before.bytes, beforeTwin.bytes),
          },
          afterTwin: {
            paint: afterTwin.paint,
            difference: difference(after.bytes, afterTwin.bytes),
          },
          acrossFrames: difference(before.bytes, after.bytes),
        },
      };
    } finally {
      if (descriptor) Object.defineProperty(ctx, "clearRect", descriptor);
      else Reflect.deleteProperty(ctx, "clearRect");
      window.removeEventListener("resize", resize);
    }
  });
}

async function expectMotion(page: Page, moving: boolean) {
  try {
    await page.waitForFunction((moving) => {
      const orb = document.querySelector(".composer-wrap .activity-orb");
      const host = document.querySelector(".composer-beam-host");
      const beam = document.querySelector(".composer-beam");
      if (!orb || !host || orb.getAttribute("data-paused") !== String(!moving)) return false;
      if (!moving) {
        return beam === null;
      }
      if (!beam) return false;
      return (
        !document.hidden &&
        orb.getAttribute("data-visible") === "true" &&
        host.getAttribute("data-visible") === "true" &&
        host.getAttribute("data-motion") === "allow" &&
        ["beam-w1", "beam-h1", "beam-x1", "beam-y1"].every(
          (field) => getComputedStyle(beam).getPropertyValue(`--${field}`).trim() !== "",
        )
      );
    }, moving);
  } catch (cause) {
    const paint = await page.evaluate(motionSnapshot, null);
    throw new Error(`Library motion readiness (${moving}): ${JSON.stringify(paint)}`, { cause });
  }
  // Observe real animation-frame opportunities after the public pause/visibility state settles.
  const frames = () =>
    page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
        }),
    );
  await frames();
  const before = await page.evaluate(motionSnapshot, null);
  if (!before) throw new Error("Missing initial library paint");
  expect(before.painted, JSON.stringify(before.state)).toBeGreaterThan(0);
  expect(before.beamMounted, JSON.stringify(before.state)).toBe(moving);
  if (moving)
    expect(before.pulse.split("|").every(Boolean), JSON.stringify(before.state)).toBe(true);
  else expect(before.pulse).toBe("absent");
  expect(before.width).toBeLessThanOrEqual(128);
  expect(before.height).toBeLessThanOrEqual(128);
  if (moving) {
    const changed = await page.waitForFunction(motionSnapshot, before);
    const after = await changed.jsonValue();
    if (!after) throw new Error("No observed library motion");
    expect(after.hash).not.toBe(before.hash);
    expect(after.pulse).not.toBe(before.pulse);
  } else {
    const probe = await pausedCanvasProbe(page);
    expect(probe.before.beamMounted).toBe(false);
    expect(probe.after.beamMounted).toBe(false);
    expect(probe.after, JSON.stringify({ state: before.state, ...probe.diagnostics })).toEqual(
      probe.before,
    );
  }
}

async function beamPaint(page: Page) {
  return page.locator(".composer-beam").evaluate((node) => {
    const style = getComputedStyle(node, "::after");
    return {
      gradient: style.backgroundImage,
      mask: style.maskImage,
      pointerEvents: style.pointerEvents,
      visibility: style.visibility,
      filter: style.filter,
    };
  });
}

test("composer activity persists with the actual libraries and respects motion preferences", async () => {
  const { page, update, disconnect, settings } = await open();
  try {
    // The running label names the open tool; TOOLS has an open Read row (275bf06b).
    await waitForText(page, '.composer-activity[role="status"]', "Using Read");
    const orb = page.locator(".composer-wrap .activity-orb");
    expect(await orb.count()).toBe(1);
    expect(await page.locator(".reply .activity-orb").count()).toBe(0);
    const orbSize = await orb.evaluate((node) => {
      const box = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return {
        width: box.width,
        height: box.height,
        color: style.color,
        hidden: node.getAttribute("aria-hidden"),
        focusable: node.getAttribute("focusable"),
        role: node.getAttribute("role"),
      };
    });
    expect(orbSize.width).toBe(64);
    expect(orbSize.height).toBe(64);
    expect(orbSize).toMatchObject({
      hidden: "true",
      focusable: null,
      role: null,
    });
    expect(await orb.locator('canvas[data-renderer="viki-tiles"]').count()).toBe(1);
    expect(await orb.locator(".activity-orb-state").getAttribute("focusable")).toBe("false");
    expect(await orb.locator(".activity-orb-state").getAttribute("aria-hidden")).toBe("true");
    expect(await page.getByRole("status", { name: "Chat activity" }).count()).toBe(1);
    expect(
      await page
        .locator("html")
        .evaluate((node) => getComputedStyle(node).getPropertyValue("--or").trim()),
    ).toBe("#f8f8ff");
    await page.locator('.activity-orb[data-visible="true"]').waitFor();
    await page.locator('.composer-beam-host[data-visible="true"]').waitFor();
    await expectMotion(page, true);
    const decoration = page.locator(".composer-beam");
    expect(await decoration.getAttribute("aria-hidden")).toBe("true");
    expect(await decoration.evaluate((node) => getComputedStyle(node).pointerEvents)).toBe("none");
    const darkPaint = await beamPaint(page);
    expect(darkPaint.gradient).toContain("radial-gradient");
    expect(darkPaint.mask).not.toBe("none");
    expect(darkPaint.pointerEvents).toBe("none");
    const colors = [...darkPaint.gradient.matchAll(/rgba?\((\d+),\s*(\d+),\s*(\d+)/g)];
    expect(colors.length).toBeGreaterThan(0);
    for (const color of colors) {
      expect(color[1]).toBe(color[2]);
      expect(color[2]).toBe(color[3]);
    }
    const darkCanvas = await page.evaluate(motionSnapshot, null);
    if (!darkCanvas) throw new Error("Missing dark library paint");
    const transform = await page.locator(".composer-wrap").evaluate((node) => {
      const previous = node.style.transform;
      node.style.transform = "translateY(-10000px)";
      return previous;
    });
    await page.locator('.activity-orb[data-visible="false"]').waitFor({ state: "attached" });
    await page.locator('.composer-beam-host[data-visible="false"]').waitFor({ state: "attached" });
    await expectMotion(page, false);
    await page.locator(".composer-wrap").evaluate((node, previous) => {
      node.style.transform = previous;
    }, transform);
    await page.locator('.activity-orb[data-visible="true"]').waitFor();
    await page.locator('.composer-beam-host[data-visible="true"]').waitFor();
    await expectMotion(page, true);

    update([
      ...TOOLS,
      {
        type: "permission.requested",
        data: {
          tool_use_id: "read",
          kind: "tool",
          tool: "Read",
          question: "Read this synthetic note?",
          summary: "notes/example.md",
          reason: "",
          agent: "",
          plan: "",
        },
      },
    ]);
    await waitForText(page, ".composer-activity", "Waiting for approval");
    expect(await orb.getAttribute("data-state")).toBe("waiting");
    await expectMotion(page, false);

    update([...START, events.subagentStarted("agent", "researcher", "sonnet")]);
    // A running routing block reads "Working with an agent" (275bf06b).
    await waitForText(page, ".composer-activity", "Working with an agent");
    expect(await orb.count()).toBe(1);
    await expectMotion(page, true);
    update(TOOLS);
    // Back to TOOLS's own open Read row (275bf06b).
    await waitForText(page, ".composer-activity", "Using Read");

    await applyAppearance(page, { theme: "light", reduce_motion: "reduce" });
    expect(settings().values).toMatchObject({
      "appearance.theme": "light",
      "accessibility.reduce_motion": "reduce",
    });
    await expectMotion(page, false);
    const lightCanvas = await page.evaluate(motionSnapshot, null);
    if (!lightCanvas) throw new Error("Missing light library paint");
    expect(lightCanvas.ink).toBeLessThan(darkCanvas.ink);
    expect(
      await page
        .locator("html")
        .evaluate((node) => getComputedStyle(node).getPropertyValue("--or").trim()),
    ).not.toBe("#f8f8ff");
    await applyAppearance(page, { reduce_motion: "allow" });
    await expectMotion(page, true);
    const lightPaint = await beamPaint(page);
    expect(lightPaint.gradient).toContain("radial-gradient");
    const lightColors = [
      ...lightPaint.gradient.matchAll(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/g),
    ];
    expect(lightColors.length).toBeGreaterThan(0);
    for (const color of lightColors) {
      expect(color[1]).toBe(color[2]);
      expect(color[2]).toBe(color[3]);
    }
    expect(lightPaint.filter).not.toBe(darkPaint.filter);
    expect(lightPaint.mask).not.toBe("none");
    expect(lightPaint.visibility).toBe("visible");
    await applyAppearance(page, { theme: "dark", reduce_motion: "allow" });
    expect(settings().values).toMatchObject({
      "appearance.theme": "dark",
      "accessibility.reduce_motion": "allow",
    });
    expect(await orb.evaluate((node) => getComputedStyle(node).color)).toBe(orbSize.color);
    await expectMotion(page, true);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await expectMotion(page, false);
    await page.emulateMedia({ reducedMotion: "no-preference", forcedColors: "active" });
    await expectMotion(page, false);
    expect(await page.locator(".composer-beam").count()).toBe(0);
    expect(await orb.locator("canvas").evaluate((node) => getComputedStyle(node).visibility)).toBe(
      "hidden",
    );
    expect(await orb.evaluate((node) => getComputedStyle(node).outlineStyle)).toBe("solid");
    expect(
      await orb
        .locator(".activity-orb-state")
        .evaluate((node) => getComputedStyle(node).stroke === getComputedStyle(node).color),
    ).toBe(true);
    await page.emulateMedia({ forcedColors: "none" });
    await page.locator('.activity-orb[data-visible="true"]').waitFor();
    await page.locator('.composer-beam-host[data-visible="true"]').waitFor();
    await page.waitForFunction(() => {
      const beam = document.querySelector(".composer-beam");
      return (
        [
          "(prefers-reduced-motion: reduce)",
          "(forced-colors: active)",
          "(prefers-contrast: more)",
        ].every((query) => !matchMedia(query).matches) &&
        beam !== null &&
        getComputedStyle(beam).display !== "none"
      );
    });
    await expectMotion(page, true);

    update([...TOOLS, events.toolDone("read"), events.turnDone()]);
    await waitForText(page, ".composer-activity", "No reply in progress");
    expect(await orb.getAttribute("data-state")).toBe("idle");
    await expectMotion(page, false);
    expect(await page.locator(".reply .activity-orb").count()).toBe(0);
    for (const viewport of [
      { width: 1440, height: 900 },
      { width: 400, height: 450 },
    ]) {
      await page.setViewportSize(viewport);
      await page.waitForFunction(
        ({ width, height, scale }) => {
          const root = document.documentElement;
          const appliedScale = Number(root.style.getPropertyValue("--font-scale")) || 1;
          return (
            innerWidth === width &&
            innerHeight === height &&
            appliedScale === scale &&
            Number.parseFloat(getComputedStyle(root).zoom) === scale &&
            [
              ["left", 800],
              ["right", 1280],
            ].every(
              ([side, threshold]) =>
                Boolean(document.querySelector(`.shell-rail-host[data-resize-rail="${side}"]`)) ===
                matchMedia(`(min-width: ${Number(threshold) * scale}px)`).matches,
            )
          );
        },
        { ...viewport, scale: Number(settings().values?.["accessibility.font_scale"] ?? 1) },
      );
      const { diagnostics, ...fits } = await page
        .locator(".composer-wrap")
        .evaluate(async (node) => {
          await new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          );
          const dock = node.querySelector(".composer-activity")?.getBoundingClientRect();
          const input = node.querySelector("textarea")?.getBoundingClientRect();
          const buttons = [...node.querySelectorAll("button")].filter(
            (button) => button.getBoundingClientRect().width > 0,
          );
          const overlaps = buttons.some((button) => {
            const box = button.getBoundingClientRect();
            return (
              dock &&
              box.left < dock.right &&
              box.right > dock.left &&
              box.top < dock.bottom &&
              box.bottom > dock.top
            );
          });
          const result = {
            fits: node.scrollWidth <= node.clientWidth + 1,
            separate: Boolean(dock && input && dock.bottom <= input.top),
            overlaps,
          };
          const measure = (element: Element | null) => {
            if (!element) return null;
            const style = getComputedStyle(element);
            return {
              tag: element.tagName,
              className: element.getAttribute("class"),
              rect: element.getBoundingClientRect().toJSON(),
              clientWidth: element.clientWidth,
              scrollWidth: element.scrollWidth,
              clientHeight: element.clientHeight,
              scrollHeight: element.scrollHeight,
              width: style.width,
              minWidth: style.minWidth,
              maxWidth: style.maxWidth,
              boxSizing: style.boxSizing,
              padding: style.padding,
              margin: style.margin,
              overflow: style.overflow,
              display: style.display,
            };
          };
          const geometry = () => ({
            viewport: { width: innerWidth, height: innerHeight },
            document: {
              width: document.documentElement.clientWidth,
              scrollWidth: document.documentElement.scrollWidth,
              zoom: getComputedStyle(document.documentElement).zoom,
            },
            wrap: measure(node),
            slot: measure(node.closest(".composer-slot")),
            beam: measure(node.querySelector(".composer-beam")),
            fieldset: measure(node.querySelector("fieldset.composer")),
            input: measure(node.querySelector("textarea")),
            actions: measure(node.querySelector(".composer-actions")),
            controls: buttons.map(measure),
            rails: [...document.querySelectorAll(".shell-rail-host")].map(measure),
          });
          const before = geometry();
          return { ...result, diagnostics: { before } };
        });
      expect(fits, JSON.stringify(diagnostics)).toEqual({
        fits: true,
        separate: true,
        overlaps: false,
      });
    }
    disconnect();
    await page.locator('.composer-wrap[data-activity="offline"]').waitFor();
    expect(await orb.getAttribute("data-state")).toBe("offline");
    await expectMotion(page, false);
  } finally {
    await page.close();
  }
});

test("context usage stays in the rail footer and opens a color-matched reported breakdown", async () => {
  const { page, update, contextReads } = await open(TOOLS, [], 60);
  try {
    const footer = page.locator(".right-rail-footer");
    await footer.getByRole("button", { name: "Context & loadout: Not reported" }).waitFor();
    expect(contextReads()).toBe(0);
    expect(await footer.getByRole("meter", { name: "Context window used" }).count()).toBe(0);
    update([...TOOLS, events.toolDone("read"), events.turnDone()], { context_percent: 30 });
    await footer.getByRole("button", { name: "Context & loadout: 30% used" }).waitFor();
    expect(
      await footer
        .getByRole("meter", { name: "Context window used" })
        .getAttribute("aria-valuenow"),
    ).toBe("30");
    expect(contextReads()).toBe(0);
    // The opened agent library is the long list: the planned routines are gone (96832932);
    // the count is VIKI's own row plus the 60 synthetic agents.
    await page.locator(".right-rail-panel .agents-library > summary").click();
    await page.locator(".right-rail-panel .agent", { hasText: "Synthetic agent 59" }).waitFor();
    expect(await page.locator(".right-rail-panel .agent").count()).toBe(61);
    const before = await footer.boundingBox();
    // AgentsSection mounts inside .rail-agents (App.tsx), which caps itself at 40%
    // height with its own overflow-y: auto (AgentsSection.css) — the 61 rows scroll there, not
    // in .right-rail-panel, which never grows past its own bounds.
    const scroll = await page
      .locator(".right-rail-panel .rail-agents")
      .first()
      .evaluate((node) => {
        node.scrollTop = node.scrollHeight;
        return node.scrollTop;
      });
    expect(scroll).toBeGreaterThan(0);
    expect(await footer.boundingBox()).toEqual(before);
    expect(await page.locator(".right-rail-summary .skills-summary").count()).toBe(1);
    await page.setViewportSize({ width: 400, height: 500 });
    await page.getByRole("button", { name: "VIKI panel", exact: true }).click();
    const drawerFooter = page.locator(".rail-drawer .right-rail-footer");
    await drawerFooter.waitFor();
    // At mobile width the whole panel scrolls, so its footer stays reachable below the rail.
    const drawerMotion = await page.locator(".rail-drawer").evaluate(async (drawer) => {
      const geometry = () => ({
        transform: getComputedStyle(drawer).transform,
        footer: drawer.querySelector(".right-rail-footer")?.getBoundingClientRect().toJSON(),
      });
      const before = geometry();
      const entrance = drawer
        .getAnimations()
        .filter(
          (animation) =>
            animation instanceof CSSAnimation && animation.animationName === "rail-drawer-in",
        );
      await Promise.all(entrance.map((animation) => animation.finished));
      return { before, settled: geometry() };
    });
    const drawerScroll = await page.locator(".rail-drawer").evaluate((drawer) => {
      const before = drawer.scrollTop;
      const hasOverflow = drawer.scrollHeight > drawer.clientHeight;
      drawer.scrollTop = drawer.scrollHeight;
      return { before, after: drawer.scrollTop, hasOverflow };
    });
    expect(drawerScroll.hasOverflow).toBe(true);
    expect(drawerScroll.after).toBeGreaterThan(drawerScroll.before);
    const drawerViewport = await page.locator(".rail-drawer").evaluate((drawer) => {
      const bounds = drawer.getBoundingClientRect();
      return {
        left: bounds.left + drawer.clientLeft,
        top: bounds.top + drawer.clientTop,
        right: bounds.left + drawer.clientLeft + drawer.clientWidth,
        bottom: bounds.top + drawer.clientTop + drawer.clientHeight,
      };
    });
    const box = await drawerFooter.boundingBox();
    const geometry = JSON.stringify({ drawerMotion, drawerScroll, drawerViewport });
    expect(box, geometry).not.toBeNull();
    expect(box?.x, geometry).toBeGreaterThanOrEqual(drawerViewport.left);
    expect(box?.y, geometry).toBeGreaterThanOrEqual(drawerViewport.top);
    expect((box?.x ?? 0) + (box?.width ?? 0), geometry).toBeLessThanOrEqual(drawerViewport.right);
    expect((box?.y ?? 0) + (box?.height ?? 0), geometry).toBeLessThanOrEqual(drawerViewport.bottom);
    await drawerFooter.getByRole("button", { name: "Context & loadout: 30% used" }).click();
    await page.getByRole("heading", { name: "Context & loadout", exact: true }).waitFor();
    await page.locator(".context-bar-segment").first().waitFor();
    // This Vite development page mounts ContextView under main.tsx's StrictMode effect replay.
    expect(contextReads()).toBe(2);
    const colors = await page.locator(".context-view-report").evaluate((node) => {
      const segments = [...node.querySelectorAll<HTMLElement>(".context-bar-segment")];
      const used = [...node.querySelectorAll<HTMLElement>('.context-items tr[data-kind="used"]')];
      const bar = node.querySelector(".context-bar")?.getBoundingClientRect();
      return used.map((row, index) => {
        const swatch = row.querySelector<HTMLElement>(".context-category-swatch");
        const fill = row.querySelector<HTMLElement>(".context-category-fill");
        const track = row.querySelector(".context-category-track")?.getBoundingClientRect();
        const segment = segments[index];
        if (!swatch || !fill || !segment || !bar || !track)
          throw new Error("Missing category visual");
        return {
          swatch: getComputedStyle(swatch).backgroundColor,
          fill: getComputedStyle(fill).backgroundColor,
          segment: getComputedStyle(segment).backgroundColor,
          share: fill.getBoundingClientRect().width / track.width,
          barShare: segment.getBoundingClientRect().width / bar.width,
        };
      });
    });
    expect(colors).toHaveLength(2);
    expect(colors[0]?.swatch).not.toBe(colors[1]?.swatch);
    for (const [index, row] of colors.entries()) {
      expect(row.swatch).toBe(row.segment);
      expect(row.fill).toBe(row.segment);
      expect(row.share).toBeCloseTo(index === 0 ? 0.2 : 0.1, 2);
      expect(row.barShare).toBeCloseTo(row.share, 2);
    }
    expect(
      await page.locator('.context-items tr[data-kind="free"] .context-category-fill').count(),
    ).toBe(0);
  } finally {
    await page.close();
  }
});

test("folded errors and keyboard disclosures fit a narrow short scaled conversation", async () => {
  const summary = "A synthetic routing result with exact references and readable detail. ".repeat(
    24,
  );
  const rows = Array.from({ length: 5 }, (_, index) => [
    events.toolStarted(`read-${index}`, "Read", {
      head: "Read",
      rest: `notes/example-${index}.md`,
    }),
    events.toolDone(`read-${index}`, {
      isError: index === 0,
      preview: `Synthetic result ${index}`,
    }),
  ]).flat();
  const { page, settings } = await open([
    ...START,
    ...rows,
    events.subagentStarted("agent", "researcher", "sonnet"),
    {
      type: "subagent.done",
      data: {
        block_id: "agent",
        status: "completed",
        summary,
        tool_uses: 0,
        total_tokens: 0,
        duration_ms: 100,
      },
    },
    events.turnDone(),
  ]);
  try {
    await applyAppearance(page, { font_scale: "1.5" });
    expect(settings().values?.["accessibility.font_scale"]).toBe(1.5);
    await page.setViewportSize({ width: 400, height: 450 });
    expect(await page.locator(".tool-error-count").textContent()).toBe("1 error");
    expect(await page.locator(".tool-row").count()).toBe(0);
    const more = page.getByRole("button", { name: "Show 5 more tool calls", exact: true });
    await more.focus();
    await page.keyboard.press("Enter");
    await more.waitFor({ state: "detached" });
    expect(await page.locator(".tool-row").count()).toBe(5);
    const error = page.locator(".tool-row--error");
    await error.focus();
    await page.keyboard.press("Enter");
    expect(await error.getAttribute("aria-expanded")).toBe("true");
    expect(await error.evaluate((node) => node === document.activeElement)).toBe(true);
    const previewId = await error.getAttribute("aria-controls");
    expect(await page.locator(".tool-preview").getAttribute("id")).toBe(previewId);
    expect(await page.locator(".tool-preview pre").textContent()).toBe("ok\nSynthetic result 0");

    const toggle = page.locator(".routing-summary-toggle");
    await toggle.focus();
    await page.keyboard.press("Enter");
    expect(await toggle.getAttribute("aria-expanded")).toBe("true");
    expect(await toggle.evaluate((node) => node === document.activeElement)).toBe(true);
    expect(await toggle.evaluate((node) => getComputedStyle(node).outlineStyle)).toBe("solid");
    const expanded = page.locator(".routing-summary");
    expect(await expanded.textContent()).toBe(summary);
    expect(await expanded.getAttribute("id")).toBe(await toggle.getAttribute("aria-controls"));
    expect(
      await expanded.evaluate((node) => ({
        display: getComputedStyle(node).display,
        clamp: getComputedStyle(node).getPropertyValue("-webkit-line-clamp"),
        fullHeight: node.scrollHeight <= node.clientHeight + 1,
      })),
    ).toEqual({ display: "block", clamp: "none", fullHeight: true });
    expect(
      await page
        .locator(".conversation")
        .evaluate((node) => node.scrollWidth <= node.clientWidth + 1),
    ).toBe(true);
  } finally {
    await page.close();
  }
});

test("search and its last keyboard result fit a short window at 150% font scale", async () => {
  const hits: SearchHit[] = Array.from({ length: 30 }, (_, index) => ({
    session_id: `bbbbbbbb-bbbb-4bbb-8bbb-${String(index + 1).padStart(12, "0")}`,
    title: `Synthetic result ${index + 1}`,
    role: "user",
    snippet: `Synthetic preview for result ${index + 1}`,
    at: "",
  }));
  const { page, settings } = await open(TOOLS, hits);
  try {
    await applyAppearance(page, { font_scale: "1.5" });
    expect(settings().values?.["accessibility.font_scale"]).toBe(1.5);
    expect(await page.locator("html").evaluate((node) => getComputedStyle(node).zoom)).toBe("1.5");
    await page.setViewportSize({ width: 400, height: 450 });
    await page.keyboard.press("Control+k");
    const input = page.getByRole("textbox", { name: "Search sessions", exact: true });
    await input.fill("synthetic");
    const last = page.locator(".hit").last();
    await last.waitFor();
    expect(await page.locator(".hit").count()).toBe(30);
    for (const box of await page
      .locator(".palette, .palette-input, .palette-hits")
      .evaluateAll((nodes) =>
        nodes.map((node) => {
          const { top, left, bottom, right } = node.getBoundingClientRect();
          return { top, left, bottom, right };
        }),
      )) {
      expect(box.top).toBeGreaterThanOrEqual(0);
      expect(box.left).toBeGreaterThanOrEqual(0);
      expect(box.bottom).toBeLessThanOrEqual(450);
      expect(box.right).toBeLessThanOrEqual(400);
    }

    await page.keyboard.press("ArrowUp");
    await waitForText(page, ".palette [role='status']", "Result 30 of 30: Synthetic result 30");
    expect(await last.getAttribute("aria-current")).toBe("true");
    expect(await input.evaluate((node) => node === document.activeElement)).toBe(true);
    expect(await page.locator(".palette-hits").evaluate((node) => node.scrollTop)).toBeGreaterThan(
      0,
    );
    const box = await last.boundingBox();
    expect(box).not.toBeNull();
    expect(box?.y).toBeGreaterThanOrEqual(0);
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(450);
  } finally {
    await page.close();
  }
});
