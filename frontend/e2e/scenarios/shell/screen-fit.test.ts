/** Actual App geometry with synthetic REST data; no backend, provider or screenshots. */
import type { Browser, Page } from "playwright-core";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, test } from "vitest";
import { GENERATION, snapshotData } from "../../../src/accept/m3b/fakes";
import type { Rails } from "../../../src/protocol";
import { capabilitiesFixture, settingsFixture } from "../../../src/testShell";
import {
  ACCEPTED_MEDIA_TYPES,
  MAX_ATTACHMENTS,
  MAX_FILE_BYTES,
  MAX_TURN_BYTES,
} from "../../../src/turnInput";
import { launchEdge } from "../../harness";
import { focusShellControl, newSidebarSession } from "../../sidebar-navigation";

const PORT = 5411;
const LONG_NAME = "A".repeat(32);
const SESSION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const rails: Rails = {
  agents: [],
  gates: [],
  routines: [],
  counts: { knowledge: 0, sources: 0, inbox: 0 },
  git: {
    state: "known",
    ref_state: "branch",
    branch: "feature/screen-fit-regression",
    head: "1234567",
    committed_at: "",
    dirty: 1,
  },
};
let browser: Browser | undefined;
let server: ViteDevServer | undefined;

beforeAll(async () => {
  server = await createServer({
    server: { host: "127.0.0.1", port: PORT, strictPort: true },
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
  width: number,
  assistantName = "VIKI",
  height = 900,
  attachmentsAvailable = true,
  liveSessionCount = 1,
): Promise<Page> {
  if (!browser) throw new Error("Edge did not launch");
  const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });
  const populated = height < 900 || liveSessionCount > 1;
  const sessionIds = [
    SESSION_ID,
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    "ffffffff-ffff-4fff-8fff-ffffffffffff",
    "77777777-7777-4777-8777-777777777777",
    "88888888-8888-4888-8888-888888888888",
  ].slice(0, liveSessionCount);
  if (populated) {
    await page.addInitScript(
      (id) => sessionStorage.setItem("viki.selected", id),
      sessionIds[sessionIds.length - 1],
    );
  }
  await page.routeWebSocket("**/ws", (socket) =>
    socket.onMessage((raw) => {
      if (!populated) return;
      const message = JSON.parse(String(raw)) as {
        type: string;
        session_id: string;
        request_id: string;
      };
      if (message.type === "auth") {
        socket.send(
          JSON.stringify({
            v: 2,
            type: "vault.sessions",
            session_id: "",
            generation: GENERATION,
            request_id: "",
            seq: 1,
            data: { others: [], live: sessionIds },
          }),
        );
      } else if (message.type === "session.open") {
        const data = snapshotData();
        data.state.max_live_sessions = 8;
        data.state.live = true;
        data.state.info = {
          model: "sonnet",
          mode: "default",
          effort: "medium",
          commands: [],
          agents: [],
          models: [],
          cli_version: "1.0.0",
          resumed: false,
        };
        const identity = {
          v: 2,
          session_id: message.session_id,
          generation: GENERATION,
          request_id: message.request_id,
          seq: data.events.length,
        };
        socket.send(JSON.stringify({ ...identity, type: "session.snapshot", data }));
        socket.send(JSON.stringify({ ...identity, type: "session.synced", data: {} }));
      }
    }),
  );
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const key = /^\/api\/v2\/drafts\/(.+)$/.exec(path)?.[1];
    const bodies: Record<string, unknown> = {
      "/api/rails": rails,
      "/api/sessions": {
        sessions: populated
          ? sessionIds.map((sessionId) => ({
              session_id: sessionId,
              title: "Synthetic screen-fit chat",
              first_prompt: "Synthetic screen-fit chat",
              entrypoint: "viki-agent",
              modified: "2026-10-11T00:00:00Z",
              cli_version: "2.1.259",
              other: false,
            }))
          : [],
        state: "ready",
        reason: "",
        omitted: 0,
      },
      "/api/v2/session-metadata": { sessions: [] },
      "/api/v2/capabilities": capabilitiesFixture(),
      "/api/v2/settings": settingsFixture({
        "identity.assistant_name": assistantName,
        "onboarding.completed": true,
      }),
      "/api/v2/attachment-capabilities": {
        provider: "claude",
        media_types: ACCEPTED_MEDIA_TYPES,
        max_files: MAX_ATTACHMENTS,
        max_file_bytes: MAX_FILE_BYTES,
        max_turn_bytes: MAX_TURN_BYTES,
        max_dimension: 8192,
      },
      "/api/v2/routines": {
        routines: [],
        scheduler: { state: "running", attended: true, paused: false },
      },
      "/api/v2/drafts": { drafts: [], next_after: null },
    };
    const body =
      path === "/api/v2/attachment-capabilities" && !attachmentsAvailable
        ? undefined
        : key
          ? { key, revision: 0, text: "", saved_at: null }
          : bodies[path];
    return route.fulfill({
      status: body === undefined ? 404 : 200,
      contentType: "application/json",
      body: JSON.stringify(body ?? {}),
    });
  });
  // The status bar has no readiness element to poll: register the capabilities
  // response wait before `page.goto` so a reply that lands early is never missed.
  const capabilities = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v2/capabilities",
  );
  await page.goto(`http://127.0.0.1:${PORT}/#token=screen-fit-synthetic`);
  await page.waitForFunction(
    (name) => document.querySelector(".wordmark")?.textContent === name,
    assistantName === "VIKI" ? "VIKI Agent" : assistantName,
  );
  // Wait for the capabilities response itself, then one animation frame so the render it
  // triggers has painted.
  await capabilities;
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
  if (populated) {
    await page.waitForFunction(() =>
      document
        .querySelector('[data-region="statusbar"]')
        ?.textContent?.includes("newer transcript"),
    );
  }
  await page.waitForFunction(
    () =>
      document.documentElement.style.getPropertyValue("--font-scale") === "1" &&
      getComputedStyle(document.documentElement).zoom === "1",
  );
  await page.evaluate(() => document.fonts.ready);
  await settle(page);
  return page;
}

async function settle(page: Page): Promise<void> {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

async function scale(page: Page, value: number): Promise<void> {
  await page.evaluate(async (wanted) => {
    document.documentElement.style.setProperty("--font-scale", String(wanted));
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
  }, value);
}

async function geometry(page: Page) {
  return page.evaluate(() => {
    const rect = (el: Element) => {
      const box = el.getBoundingClientRect();
      return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
    };
    const hosts = [
      ".shell",
      ".shell-body",
      ".app-view",
      ".topbar",
      ".topbar-right",
      ".conversation",
      ".conversation-chrome",
      ".conversation-scroll",
      ".composer-slot",
      ".composer",
      ".composer-actions",
      ".right-rail",
      ".right-rail-header",
      ".right-rail-id",
      ".right-rail-name",
    ].flatMap((selector) =>
      [...document.querySelectorAll<HTMLElement>(selector)]
        .filter((el) => el.getClientRects().length > 0)
        .map((el) => ({
          selector,
          client: el.clientWidth,
          scroll: el.scrollWidth,
          scrollLeft: el.scrollLeft,
          ...rect(el),
        })),
    );
    const content = [
      ...document.querySelectorAll<HTMLElement>(
        ".topbar button, .compact-navigation, .wordmark, .composer button, .composer textarea, .right-rail-name .identity-name",
      ),
    ]
      .filter((el) => el.getClientRects().length > 0)
      .map((el) => {
        const host = el.closest(".topbar, .composer, .right-rail-header");
        if (!host) throw new Error("Content host is missing");
        const range = document.createRange();
        range.selectNodeContents(el);
        const clipping = [];
        for (let ancestor = el.parentElement; ancestor; ancestor = ancestor.parentElement) {
          if (/auto|scroll|hidden|clip/.test(getComputedStyle(ancestor).overflowY)) {
            clipping.push({ label: ancestor.className || ancestor.tagName, ...rect(ancestor) });
          }
        }
        return {
          label: el.getAttribute("aria-label") ?? el.textContent?.trim() ?? el.className,
          box: rect(el),
          host: rect(host),
          focused: document.activeElement === el,
          clipping,
          text: [...range.getClientRects()].map((box) => ({
            left: box.left,
            right: box.right,
            top: box.top,
            bottom: box.bottom,
          })),
        };
      });
    return {
      width: innerWidth,
      height: innerHeight,
      hosts,
      content,
      shellLeft: document.querySelector<HTMLElement>(".shell")?.scrollLeft,
      bodyLeft: document.body.scrollLeft,
      frameLeft: document.querySelector<HTMLElement>(".shell-body, .app-view")?.scrollLeft,
      shellTop: document.querySelector<HTMLElement>(".shell")?.scrollTop,
      bodyTop: document.body.scrollTop,
      frameTop: document.querySelector<HTMLElement>(".shell-body, .app-view")?.scrollTop,
    };
  });
}

async function expectFit(page: Page, focusedControl?: "Message" | "Send"): Promise<void> {
  const measured = await geometry(page);
  expect.soft(measured.shellLeft).toBe(0);
  expect.soft(measured.bodyLeft).toBe(0);
  expect.soft(measured.frameLeft).toBe(0);
  expect.soft(measured.shellTop).toBe(0);
  expect.soft(measured.bodyTop).toBe(0);
  expect.soft(measured.frameTop).toBe(0);
  for (const host of measured.hosts) {
    expect.soft(host.scrollLeft, host.selector).toBe(0);
    expect.soft(host.scroll, host.selector).toBeLessThanOrEqual(host.client + 1);
    expect.soft(host.left, host.selector).toBeGreaterThanOrEqual(-1);
    expect.soft(host.right, host.selector).toBeLessThanOrEqual(measured.width + 1);
  }
  if (focusedControl) {
    expect(measured.content.find((item) => item.label === focusedControl)?.focused).toBe(true);
  }
  for (const item of measured.content) {
    for (const box of [item.box, ...item.text]) {
      expect.soft(box.left, item.label).toBeGreaterThanOrEqual(item.host.left - 1);
      expect.soft(box.right, item.label).toBeLessThanOrEqual(item.host.right + 1);
      expect.soft(box.top, item.label).toBeGreaterThanOrEqual(item.host.top - 1);
      expect.soft(box.bottom, item.label).toBeLessThanOrEqual(item.host.bottom + 1);
      // A short viewport may scroll secondary content; the focused core control must be exposed.
      if (focusedControl ? item.label === focusedControl : measured.height >= 900) {
        expect.soft(box.top, item.label).toBeGreaterThanOrEqual(-1);
        expect.soft(box.bottom, item.label).toBeLessThanOrEqual(measured.height + 1);
        for (const ancestor of item.clipping) {
          expect.soft(box.left, ancestor.label).toBeGreaterThanOrEqual(ancestor.left - 1);
          expect.soft(box.right, ancestor.label).toBeLessThanOrEqual(ancestor.right + 1);
          expect.soft(box.top, ancestor.label).toBeGreaterThanOrEqual(ancestor.top - 1);
          expect.soft(box.bottom, ancestor.label).toBeLessThanOrEqual(ancestor.bottom + 1);
        }
      }
    }
  }
}

test("continued resizing, scaling and navigation keeps controls in view", async () => {
  const page = await open(1440);
  try {
    for (const width of [400, 900, 1000, 1280, 400]) {
      await scale(page, 1);
      await page.setViewportSize({ width, height: 900 });
      await scale(page, 1.5);
      await expectFit(page);
      await page.getByLabel("Message").fill("Continued typing stays inside the visible composer.");
      await expectFit(page);
      for (const name of ["Sessions and agents", "VIKI panel"]) {
        const toggle = page.getByRole("button", { name, exact: true });
        if ((await toggle.count()) === 0) continue;
        await toggle.click();
        await page.getByRole("dialog", { name }).waitFor();
        await expectFit(page);
        await page.keyboard.press("Escape");
        await expectFit(page);
      }
      await page.keyboard.press("Control+Comma");
      await page.locator('[data-region="settings"]').waitFor();
      await expectFit(page);
      await page.keyboard.press("Escape");
      await page.locator('[data-region="conversation"]').waitFor();
      await expectFit(page);
    }
  } finally {
    await page.close();
  }
});

test("the admitted assistant name fits its top bar and rail", async () => {
  for (const [width, height, fontScale] of [
    [400, 900, 1.5],
    [900, 900, 1.5],
    [1000, 900, 1.5],
    [1280, 900, 1.5],
    [1440, 900, 1.5],
    [400, 450, 0.85],
    [400, 450, 1],
    [400, 450, 1.15],
    [400, 450, 1.3],
    [400, 450, 1.5],
    [400, 600, 1],
    [800, 600, 1],
  ] as const) {
    for (const attachmentsAvailable of height < 900 ? [true, false] : [true]) {
      const page = await open(width, LONG_NAME, height, attachmentsAvailable);
      try {
        await scale(page, fontScale);
        if (await page.getByRole("button", { name: "VIKI panel", exact: true }).count()) {
          await page.getByRole("button", { name: "VIKI panel", exact: true }).click();
          await page.getByRole("dialog", { name: "VIKI panel" }).waitFor();
        }
        expect(await page.locator(".right-rail-name .identity-name").textContent()).toBe(LONG_NAME);
        await expectFit(page);
        if (height < 900) {
          if (await page.getByRole("dialog", { name: "VIKI panel" }).count()) {
            await page.keyboard.press("Escape");
            await page.getByRole("dialog", { name: "VIKI panel" }).waitFor({ state: "hidden" });
            await expectFit(page);
          }
          const message = page.getByLabel("Message", { exact: true });
          await message.fill(
            Array.from({ length: 20 }, (_, index) => `Draft line ${index + 1}`).join("\n"),
          );
          await settle(page);
          await expectFit(page);
          await page.locator(".topbar button").first().focus();
          await message.focus();
          await settle(page);
          const field = await message.boundingBox();
          const slot = await page.locator(".composer-slot").boundingBox();
          if (!field || !slot) throw new Error("Composer geometry is missing");
          if (field.y < slot.y - 1 || field.y + field.height > slot.y + slot.height + 1) {
            await page.mouse.move(slot.x + 2, slot.y + slot.height / 2);
            await page.mouse.wheel(0, -20);
            await settle(page);
          }
          await expectFit(page, "Message");
          await page.getByRole("button", { name: "Send", exact: true }).focus();
          await settle(page);
          await expectFit(page, "Send");
        }
      } finally {
        await page.close();
      }
    }
  }
});

test("collapsing, pointer focusing and restoring a rail keeps the frame in view", async () => {
  const page = await open(1440);
  try {
    const handle = page.getByRole("separator", { name: "Resize right rail", exact: true });
    await handle.focus();
    await handle.press("Enter");
    expect(await handle.getAttribute("aria-valuenow")).toBe("0");
    await handle.click();
    await expectFit(page);
    await handle.press("Enter");
    expect(Number(await handle.getAttribute("aria-valuenow"))).toBeGreaterThan(0);
    await expectFit(page);
    await scale(page, 1.5);
    await expectFit(page);
  } finally {
    await page.close();
  }
});

test("native pane scrolling keeps the session-limit panel reachable", async () => {
  for (const [width, height, fontScale, name, wideRails, paneHeight] of [
    [400, 450, 1.5, LONG_NAME, false],
    [400, 600, 1.5, LONG_NAME, false],
    [400, 450, 1.5, "W".repeat(32), false],
    [400, 600, 1.5, "W".repeat(32), false],
    [1440, 900, 1, "VIKI", false],
    [1440, 900, 1, "VIKI", false, 361],
    [1440, 900, 1, "VIKI", true],
    [1600, 900, 1.15, "VIKI", true],
  ] as const) {
    const page = await open(width, name, height, true, 8);
    try {
      await scale(page, fontScale);
      if (wideRails) {
        await page.getByRole("separator", { name: "Resize left rail", exact: true }).press("End");
        await page.getByRole("separator", { name: "Resize right rail", exact: true }).press("End");
        await settle(page);
      }
      const pane = page.locator('[data-region="conversation"]');
      if (paneHeight !== undefined) {
        await pane.evaluate((el, height) => {
          el.style.height = `${height}px`;
        }, paneHeight);
        await settle(page);
        expect((await pane.boundingBox())?.height).toBe(paneHeight);
      }
      const message = page.getByLabel("Message", { exact: true });
      const focusMessage = async () => {
        await focusShellControl(page);
        await message.focus();
        await settle(page);
        const field = await message.boundingBox();
        const slot = await page.locator(".composer-slot").boundingBox();
        if (!field || !slot) throw new Error("Composer geometry is missing");
        if (field.y < slot.y - 1 || field.y + field.height > slot.y + slot.height + 1) {
          await page.mouse.move(slot.x + 2, slot.y + slot.height / 2);
          await page.mouse.wheel(0, -20);
          await settle(page);
        }
        await expectFit(page, "Message");
      };
      if (height < 900) {
        await message.fill(
          Array.from({ length: 20 }, (_, index) => `Synthetic draft ${index + 1}`).join("\n"),
        );
        await settle(page);
        await focusShellControl(page);
        await page.getByRole("button", { name: "Session actions", exact: true }).focus();
        await settle(page);
        const before = await pane.evaluate((el) => el.scrollTop);
        const box = await pane.boundingBox();
        const header = await page.locator('[data-region="conversation-header"]').boundingBox();
        if (!box || !header) throw new Error("Conversation header bounds are missing");
        const top = Math.max(box.y, header.y);
        const bottom = Math.min(box.y + box.height, header.y + header.height);
        expect(bottom).toBeGreaterThan(top);
        // The visible header gutter avoids the nested composer and message scrollers.
        await page.mouse.move(box.x + 2, (top + bottom) / 2);
        await page.mouse.wheel(0, before > 0 ? -80 : 80);
        await settle(page);
        expect(await pane.evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto");
        expect(await pane.evaluate((el) => el.scrollTop)).not.toBe(before);
        await focusMessage();
      }

      await newSidebarSession(page);
      const toast = page.locator(".toast");
      await toast.waitFor();
      expect(await toast.locator(".toast-row-close").count()).toBe(8);
      expect(await toast.locator(".toast-title").textContent()).toBe(
        "Up to 8 sessions can run at once",
      );
      expect(await toast.locator(".toast-sub").textContent()).toBe(
        "Close one to start a new session.",
      );

      const expectTargetFit = async (selector: string, focused = false) => {
        const fit = await page.evaluate((selector) => {
          const bounds = (el: Element) => {
            const box = el.getBoundingClientRect();
            return { left: box.left, right: box.right, top: box.top, bottom: box.bottom };
          };
          const panel = document.querySelector<HTMLElement>(".toast");
          const pane = document.querySelector<HTMLElement>('[data-region="conversation"]');
          if (!panel || !pane) throw new Error("Session-limit panel host is missing");
          return {
            window: { left: 0, right: innerWidth, top: 0, bottom: innerHeight },
            panel: bounds(panel),
            pane: bounds(pane),
            scroll: panel.scrollWidth,
            client: panel.clientWidth,
            roots: [
              document.documentElement,
              document.body,
              ...document.querySelectorAll<HTMLElement>(".shell, .shell-body"),
            ].map((el) => ({ left: el.scrollLeft, top: el.scrollTop })),
            targets: [...panel.querySelectorAll<HTMLElement>(selector)].map((el) => {
              const range = document.createRange();
              range.selectNodeContents(el);
              const text = [...range.getClientRects()].map((box) => ({
                left: box.left,
                right: box.right,
                top: box.top,
                bottom: box.bottom,
              }));
              const box = bounds(el);
              const clipping = [];
              for (let ancestor = el.parentElement; ancestor; ancestor = ancestor.parentElement) {
                if (/auto|scroll|hidden|clip/.test(getComputedStyle(ancestor).overflowY)) {
                  clipping.push(bounds(ancestor));
                }
              }
              return {
                label: el.getAttribute("aria-label") ?? el.textContent,
                box,
                text,
                clipping,
                focused: document.activeElement === el,
                hits: (text.length ? text : [box]).map((point) =>
                  el.contains(
                    document.elementFromPoint(
                      (point.left + point.right) / 2,
                      (point.top + point.bottom) / 2,
                    ),
                  ),
                ),
              };
            }),
          };
        }, selector);
        expect(fit.scroll).toBeLessThanOrEqual(fit.client + 1);
        for (const root of fit.roots) {
          expect(root.left).toBe(0);
          expect(root.top).toBe(0);
        }
        for (const host of [fit.pane, fit.window]) {
          expect(fit.panel.left).toBeGreaterThanOrEqual(host.left - 1);
          expect(fit.panel.right).toBeLessThanOrEqual(host.right + 1);
          expect(fit.panel.top).toBeGreaterThanOrEqual(host.top - 1);
          expect(fit.panel.bottom).toBeLessThanOrEqual(host.bottom + 1);
        }
        expect(fit.targets.length).toBeGreaterThan(0);
        for (const target of fit.targets) {
          if (focused) expect(target.focused).toBe(true);
          expect(target.hits.every(Boolean), JSON.stringify({ selector, target })).toBe(true);
          for (const box of [target.box, ...target.text]) {
            for (const host of [target.box, fit.panel, fit.window, ...target.clipping]) {
              expect(box.left).toBeGreaterThanOrEqual(host.left - 1);
              expect(box.right).toBeLessThanOrEqual(host.right + 1);
              expect(box.top).toBeGreaterThanOrEqual(host.top - 1);
              expect(box.bottom).toBeLessThanOrEqual(host.bottom + 1);
            }
          }
        }
      };
      await expectTargetFit(".toast-title, .toast-sub");
      if (width === 1440 && !wideRails && paneHeight === undefined) {
        const box = await toast.boundingBox();
        const host = await pane.boundingBox();
        if (!box || !host) throw new Error("Roomy panel geometry is missing");
        expect(box.width).toBe(380);
        expect(box.height).toBe(424);
        expect(box.x + box.width).toBe(host.x + host.width - 24);
        expect(box.y + box.height).toBe(host.y + host.height - 96);
      }
      if (await toast.evaluate((el) => el.scrollHeight > el.clientHeight)) {
        const before = await toast.evaluate((el) => el.scrollTop);
        const box = await toast.boundingBox();
        if (!box) throw new Error("Session-limit panel bounds are missing");
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.wheel(0, 100);
        await settle(page);
        expect(await toast.evaluate((el) => el.scrollTop)).toBeGreaterThan(before);
      }
      for (const selector of [
        ".toast-row:first-child .toast-row-close",
        ".toast-row:last-child .toast-row-close",
        ".toast-dismiss",
      ]) {
        await focusShellControl(page);
        await toast.locator(selector).focus();
        await settle(page);
        await expectTargetFit(selector, true);
      }
      await page.getByRole("button", { name: "Dismiss", exact: true }).click();
      await toast.waitFor({ state: "hidden" });
      if (height >= 900) await message.fill("Continue after dismissing the panel.");
      await focusMessage();
      await page.getByRole("button", { name: "Send", exact: true }).focus();
      await settle(page);
      await expectFit(page, "Send");
    } finally {
      await page.close();
    }
  }
});
