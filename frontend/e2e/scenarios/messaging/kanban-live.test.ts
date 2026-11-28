/**
 * A card file written straight into the vault's kanban/ folder, with no click once Kanban is
 * open: the live `kanban.changed` push (the server's 1 s watch on kanban/) is what moves it
 * through To do, Ready for review and Needs you, never a manual refresh.
 *
 * Run from frontend/ (binds 8765; check it is free first):
 *   npm run e2e -- e2e/scenarios/messaging/kanban-live.test.ts
 */
// The harness owns `e2e/node.d.ts`, which declares only what `server.ts` needs; these modules are
// this file's own business, so they are suppressed here rather than added to it
// (`e2e/kanban.shots.ts` and `e2e/scenarios/vaults/browser.test.ts` do the same).
// @ts-expect-error see above
import { mkdirSync, writeFileSync } from "node:fs";
// @ts-expect-error see above
import { homedir, tmpdir } from "node:os";
// @ts-expect-error see above
import { join } from "node:path";
import type { Browser } from "playwright-core";
import { afterAll, beforeAll, expect, test } from "vitest";
import { appUrl, launchEdge } from "../../harness";
import { type Server, startServer } from "../../server";
import { navigateSidebar } from "../../sidebar-navigation";

/** `scripts/e2e_server.py`'s own `--bots-team` SCRATCH root, mirrored as `e2e/kanban.shots.ts`. */
const SCRATCH =
  process.platform === "win32" ? join(tmpdir(), "viki-e2e") : join(homedir(), ".cache", "viki-e2e");
const VAULT = join(SCRATCH, "workspaces", "vault");
const TEST_TIMEOUT_MS = 90_000;

let server: Server | undefined;
let browser: Browser | undefined;

beforeAll(async () => {
  server = await startServer({
    bots: true,
    team: true,
    fixture: "tests/fixtures/claude/text-two-turns.jsonl",
  });
  browser = await launchEdge();
}, 120_000);

afterAll(async () => {
  await browser?.close();
  await server?.stop();
});

/** Writes (or rewrites) the one card this scenario watches, direct fs the way `kanban.shots.ts`'s
 * own `writeCard` does, so the live watch sees it exactly as a human editing the note would. */
function writeCard(status: string): void {
  mkdirSync(join(VAULT, "kanban"), { recursive: true });
  writeFileSync(
    join(VAULT, "kanban", "live-card.md"),
    `---\ntype: card\nstatus: ${status}\ntags: [owner/research]\n---\n\n# Live card\n`,
    "utf-8",
  );
}

test(
  "a card written on disk reaches To do, then Ready for review and Needs you, with no click",
  async () => {
    if (!browser) throw new Error("Browser did not launch");
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    try {
      await page.goto(appUrl());
      await navigateSidebar(page, "Kanban");
      await page.getByRole("heading", { name: "To do (0)" }).waitFor({ timeout: 15_000 });
      const kanban = page.locator(".kanban-page");
      expect(await kanban.getByRole("button", { name: /refresh/i }).count()).toBe(0);

      writeCard("todo");
      await page.getByRole("heading", { name: "To do (1)" }).waitFor({ timeout: 15_000 });
      const liveCard = page.getByRole("button", { name: "Live card", exact: true });
      await liveCard.waitFor({ timeout: 15_000 });

      writeCard("review");
      const reviewHeading = page.getByRole("heading", { name: "Ready for review (1)" });
      await reviewHeading.waitFor({ timeout: 15_000 });
      await page.getByRole("region", { name: /^Needs you/ }).waitFor({ timeout: 15_000 });
    } finally {
      await page.close();
    }
  },
  TEST_TIMEOUT_MS,
);
