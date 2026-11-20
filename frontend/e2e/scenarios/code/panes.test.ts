/**
 * e2e: tabs and panes, in real Edge against the real server and the fake CLI.
 *
 * Two sessions answer in the one pane, then one tab is dragged onto the right edge: two
 * conversations side by side between both docked rails, each with its own scroll. A third session
 * lands beside it in the focused pane, Alt+1 and Alt+2 choose that pane's tabs, a reload keeps
 * `viki.panes`, below 1280 px the panes fold into one strip until the window widens, and the tab
 * menu's Split down and Move to pane 1 open and then fold a third pane. After those layout
 * checks, the live-session limit is set to 8, then eight live sessions fill the slots and a
 * ninth shows the limit toast.
 *
 * The facts only a browser can show are the layout (panes sit side by side, sized by real boxes),
 * the scroll hosts (two scrollers, neither moving the other), a real HTML5 drag onto a pane edge,
 * and the keyboard (Alt+digit read by `event.code`). Everything else — the layout model, the tab
 * menu's items, which pane a drop or a move lands in — is pinned in jsdom by
 * src/app-tests/shell/panes.unit.test.tsx and src/app-tests/shell/panesKeys.unit.test.tsx; this
 * file covers only the browser-only cases.
 *
 * Both fake sessions have no catalog row, so their tabs carry their own session id as the title;
 * tabs are therefore found and named by session id, never by title.
 *
 * Binds 8765, so it is part of the serialized end-to-end run, never of a parallel one:
 *   npx vitest run --config vitest.e2e.config.ts e2e/scenarios/code/panes.test.ts
 */
import type { Browser, Locator, Page } from "playwright-core";
import { afterAll, beforeAll, expect, test } from "vitest";
import type { SettingsView } from "../../../src/clients/app/shellApi";
import { appUrl, launchEdge, send, TEST_TOKEN, waitForCount, waitForText } from "../../harness";
import { ORIGIN, PORT, type Server, startServer } from "../../server";
import { newSidebarSession } from "../../sidebar-navigation";

const FIXTURE = "tests/fixtures/claude/text-two-turns.jsonl";
/** The start of the fixture's reply. */
const REPLY = "Prime directive";
const LIMIT_TOAST = "Up to 8 sessions can run at once";

let server: Server | undefined;
let browser: Browser | undefined;

beforeAll(async () => {
  server = await startServer({ fixture: FIXTURE });
  await persistSessionLimit();
  browser = await launchEdge();
}, 60_000);

afterAll(async () => {
  await browser?.close();
  await server?.stop();
});

/** Sets chat.max_live_sessions to 8 the way bot-settings.shots.ts's persistAppearanceSettings
 * sets appearance settings — GET the revision, then PATCH with it — so step 8 below gates at 8
 * instead of filling toward the real server's new default of 30. */
async function persistSessionLimit(): Promise<void> {
  const headers = {
    authorization: `Bearer ${TEST_TOKEN}`,
    origin: ORIGIN,
    "content-type": "application/json",
  };
  const currentResponse = await fetch(`${ORIGIN}/api/v2/settings`, { headers });
  if (!currentResponse.ok)
    throw new Error(`Synthetic settings read answered ${currentResponse.status}.`);
  const current = (await currentResponse.json()) as SettingsView;
  const savedResponse = await fetch(`${ORIGIN}/api/v2/settings`, {
    method: "PATCH",
    headers,
    body: JSON.stringify({
      command_id: crypto.randomUUID(),
      expected_revision: current.revision,
      changes: { "chat.max_live_sessions": 8 },
    }),
  });
  if (!savedResponse.ok)
    throw new Error(`Synthetic settings patch answered ${savedResponse.status}.`);
  const saved = (await savedResponse.json()) as SettingsView;
  expect(saved.effective["chat.max_live_sessions"]).toBe(8);
}

/** A page whose settings read has landed, which is what the app's own DOM writes wait for. */
async function openApp(): Promise<Page> {
  if (!browser) throw new Error("Edge did not launch");
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: "ja-JP" });
  const settings = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v2/settings" &&
      response.request().method() === "GET",
  );
  await page.goto(appUrl(PORT));
  await page.locator('[data-region="shell"]').waitFor({ timeout: 15_000 });
  await settings;
  return page;
}

/** Send one message, then wait for its reply to show and the turn to end. */
async function sendAndAnswered(page: Page): Promise<void> {
  await send(page, "hello");
  await waitForText(page, ".reply .md", REPLY, 20_000);
  await waitForCount(page, ".composer-stop", 0, 20_000);
}

/** The session ids of every tab in the window, strip by strip, in tree order. */
function tabIds(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll(".session-tabs [role=tab]")].map((tab) =>
      tab.id.replace("session-tab-", ""),
    ),
  );
}

/** The session ids of one pane's own strip. */
function paneTabIds(pane: Locator): Promise<string[]> {
  return pane.evaluate((node) =>
    [...node.querySelectorAll(".session-tabs [role=tab]")].map((tab) =>
      tab.id.replace("session-tab-", ""),
    ),
  );
}

function selectedTab(page: Page): Promise<string | undefined> {
  return page.evaluate(() =>
    document
      .querySelector('.session-tabs [role=tab][aria-selected="true"]')
      ?.id.replace("session-tab-", ""),
  );
}

async function waitSelected(page: Page, id: string): Promise<void> {
  await page.waitForFunction(
    (want) =>
      document
        .querySelector('.session-tabs [role=tab][aria-selected="true"]')
        ?.id.endsWith(want) === true,
    id,
  );
}

function tab(page: Page, id: string): Locator {
  return page.locator(`.session-tab:has(#session-tab-${id})`);
}

/** The scroll position of a scroller, from the page. */
function scrollTopOf(scroller: Locator): Promise<number> {
  return scroller.evaluate((el) => el.scrollTop);
}

test("tabs and the panes", async () => {
  const page = await openApp();

  // 1. Two sessions, each with its reply, both tabs of the one pane.
  await sendAndAnswered(page);
  await newSidebarSession(page);
  await waitForCount(page, ".session-tabs [role=tab]", 2, 15_000);
  await sendAndAnswered(page);
  const [a, b] = await tabIds(page);
  if (a === undefined || b === undefined) throw new Error("two tabs were expected");
  expect(await selectedTab(page)).toBe(b);
  expect(await page.locator("section.conversation.pane").count()).toBe(0);

  // 2. A's tab dragged onto the right edge of the one conversation: a real HTML5 drag opens a
  // second pane with it, focused, between the docked rails.
  const main = page.locator('[data-region="conversation"]');
  const mainBox = await main.boundingBox();
  if (!mainBox) throw new Error("the conversation was not rendered");
  await tab(page, a).dragTo(main, {
    targetPosition: { x: mainBox.width - 16, y: mainBox.height / 2 },
  });
  await waitForCount(page, "section.conversation.pane", 2, 15_000);
  const panes = page.locator("section.conversation.pane");
  expect(await paneTabIds(panes.nth(0))).toEqual([b]);
  expect(await paneTabIds(panes.nth(1))).toEqual([a]);
  expect(await panes.nth(1).evaluate((el) => el.hasAttribute("data-focused"))).toBe(true);
  expect(await selectedTab(page)).toBe(a);

  // Both panes have the real composer before the inactive pane receives focus. Keep the
  // original inputs so the focus roundtrip also catches a replaced field swallowing the action.
  await waitForCount(page, '.pane > .composer-slot [data-region="composer"]', 2, 15_000);
  for (const index of [0, 1]) {
    const pane = panes.nth(index);
    expect(await pane.getByRole("group", { name: "Chat composer", exact: true }).count()).toBe(1);
    expect(await pane.getByRole("status", { name: "Chat activity", exact: true }).count()).toBe(1);
    expect(await pane.getByRole("status", { name: "Chat activity", exact: true }).isVisible()).toBe(
      true,
    );
    expect(await pane.getByRole("textbox", { name: "Message", exact: true }).count()).toBe(1);
    expect(await pane.getByRole("textbox", { name: "Message", exact: true }).isVisible()).toBe(
      true,
    );
  }
  const leftInput = await panes
    .nth(0)
    .getByRole("textbox", { name: "Message", exact: true })
    .elementHandle();
  const rightInput = await panes
    .nth(1)
    .getByRole("textbox", { name: "Message", exact: true })
    .elementHandle();
  if (!leftInput || !rightInput) throw new Error("both original composer inputs were expected");
  await leftInput.fill("Draft kept in the left chat");
  await waitSelected(page, b);
  expect(
    await leftInput.evaluate((input) => input.isConnected && document.activeElement === input),
  ).toBe(true);
  await rightInput.click();
  await waitSelected(page, a);
  expect(
    await rightInput.evaluate((input) => input.isConnected && document.activeElement === input),
  ).toBe(true);
  expect(await leftInput.evaluate((input) => input.isConnected)).toBe(true);
  expect(await leftInput.inputValue()).toBe("Draft kept in the left chat");

  const leftBox = await panes.nth(0).boundingBox();
  const rightBox = await panes.nth(1).boundingBox();
  if (!leftBox || !rightBox) throw new Error("both panes were expected to render");
  expect(rightBox.x).toBeGreaterThanOrEqual(leftBox.x + leftBox.width - 1);
  expect(rightBox.y).toBe(leftBox.y);
  expect(leftBox.width).toBeGreaterThanOrEqual(420);
  expect(rightBox.width).toBeGreaterThanOrEqual(420);
  const leftRail = await page.locator('[data-region="left-rail"]').boundingBox();
  const rightRail = await page.locator('[data-region="right-rail"]').boundingBox();
  if (!leftRail || !rightRail) throw new Error("both docked rails were expected to render");
  expect(leftBox.x).toBeGreaterThanOrEqual(leftRail.x + leftRail.width - 1);
  expect(rightBox.x + rightBox.width).toBeLessThanOrEqual(rightRail.x + 1);
  expect(rightRail.x + rightRail.width).toBeLessThanOrEqual(1440);
  expect(await page.getByRole("button", { name: "VIKI panel", exact: true }).count()).toBe(0);
  expect(await page.locator('[data-region="left-rail"]').isVisible()).toBe(true);
  expect(await page.locator('[data-region="right-rail"]').isVisible()).toBe(true);

  // 3. One short reply fits its pane, so a spacer after the messages makes each scroller longer
  // than it is tall. Neither pane's scroller may move the other's.
  for (const index of [0, 1]) {
    const scroller = panes.nth(index).locator(".conversation-scroll");
    const room = await scroller.evaluate((el) => {
      const spacer = document.createElement("div");
      spacer.style.height = "1500px";
      // Keep this artificial empty spacer tall inside the primary column flex scroller.
      spacer.style.flexShrink = "0";
      el.append(spacer);
      el.scrollTo(0, 0);
      return { scroll: el.scrollHeight, client: el.clientHeight };
    });
    expect(room.scroll).toBeGreaterThan(room.client + 40);
  }
  const leftScroll = panes.nth(0).locator(".conversation-scroll");
  const rightScroll = panes.nth(1).locator(".conversation-scroll");
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await leftScroll.evaluate((el) => el.scrollTo(0, 0));
  await rightScroll.evaluate((el) => el.scrollTo(0, 0));
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await leftScroll.evaluate((el) => el.scrollTo(0, 40));
  expect(await scrollTopOf(leftScroll)).toBe(40);
  expect(await scrollTopOf(rightScroll)).toBe(0);
  await rightScroll.evaluate((el) => el.scrollTo(0, 30));
  expect(await scrollTopOf(rightScroll)).toBe(30);
  expect(await scrollTopOf(leftScroll)).toBe(40);

  // 4. A third session lands beside A in the focused pane. Alt+1 and Alt+2, read by `event.code`,
  // choose between that pane's own two tabs, never B's.
  await newSidebarSession(page);
  await waitForCount(page, ".session-tabs [role=tab]", 3, 15_000);
  const c = (await paneTabIds(panes.nth(1))).find((id) => id !== a);
  if (c === undefined) throw new Error("a third tab was expected beside a");
  expect(await paneTabIds(panes.nth(1))).toEqual([a, c]);
  expect(await selectedTab(page)).toBe(c);

  await tab(page, c).getByRole("tab").focus();
  await page.keyboard.press("Alt+Digit1");
  await waitSelected(page, a);
  await tab(page, a).getByRole("tab").focus();
  await page.keyboard.press("Alt+Digit2");
  await waitSelected(page, c);

  // 5. A reload keeps the panes: the same two groups, the same tabs, the same selection.
  const settings = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v2/settings" &&
      response.request().method() === "GET",
  );
  await page.reload();
  await settings;
  await waitForCount(page, "section.conversation.pane", 2, 20_000);
  expect(await paneTabIds(panes.nth(0))).toEqual([b]);
  expect(await paneTabIds(panes.nth(1))).toEqual([a, c]);
  await waitSelected(page, c);
  expect(await selectedTab(page)).toBe(c);
  await waitForText(page, ".conversation-scroll", REPLY, 20_000);

  // 6. Below 1280 px the panes fold into the one strip; widening restores them.
  await page.setViewportSize({ width: 1279, height: 900 });
  await waitForCount(page, "section.conversation.pane", 0, 15_000);
  expect(await page.locator('[role="tablist"].session-tabs').count()).toBe(1);
  expect(await page.locator(".session-tabs [role=tab]").count()).toBe(3);
  await page.setViewportSize({ width: 1440, height: 900 });
  await waitForCount(page, "section.conversation.pane", 2, 15_000);

  // 7. The tab menu's Split down opens a third pane for C, focused; Move to pane 1 folds it back
  // into A and B's first pane.
  const cMenu = tab(page, c).getByRole("button", { name: /^Tab actions:/ });
  await cMenu.click();
  await page.getByRole("menuitem", { name: "Split down", exact: true }).click();
  await waitForCount(page, "section.conversation.pane", 3, 15_000);
  expect(await page.locator('.pane-split[data-dir="column"]').count()).toBe(1);
  const threePanes = page.locator("section.conversation.pane");
  expect(await paneTabIds(threePanes.nth(2))).toEqual([c]);
  expect(await threePanes.nth(2).evaluate((el) => el.hasAttribute("data-focused"))).toBe(true);

  await cMenu.click();
  await page.getByRole("menuitem", { name: "Move to pane 1", exact: true }).click();
  await waitForCount(page, "section.conversation.pane", 2, 15_000);
  const finalPanes = page.locator("section.conversation.pane");
  expect(await paneTabIds(finalPanes.nth(0))).toEqual([b, c]);
  expect(await finalPanes.nth(0).evaluate((el) => el.hasAttribute("data-focused"))).toBe(true);
  expect(await selectedTab(page)).toBe(c);

  // 8. Fill eight live sessions, then refuse a ninth with eight Close rows and no ninth tab.
  expect(await page.locator(".toast").count()).toBe(0);
  await sendAndAnswered(page);
  for (let count = 4; count <= 8; count++) {
    await newSidebarSession(page);
    await waitForCount(page, ".session-tabs [role=tab]", count, 15_000);
    await sendAndAnswered(page);
  }
  await newSidebarSession(page);
  await waitForText(page, ".toast", LIMIT_TOAST, 15_000);
  expect(await page.locator(".session-tabs [role=tab]").count()).toBe(8);
  expect(await page.locator(".toast-row-close").count()).toBe(8);
  expect(
    await page.locator(".toast").getByRole("button", { name: "Change limit", exact: true }).count(),
  ).toBe(1);
  await page.keyboard.press("Escape");
  await waitForCount(page, ".toast", 0, 15_000);

  await page.close();
}, 120_000);
