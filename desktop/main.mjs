/**
 * @module main
 * The VIKI Agent desktop app: starts the server as its window host (`server.mjs`) and shows the
 * windows the server asks for (`window.mjs`). The server keeps every window decision and ends
 * the app: closing the last window leaves the app to the server's grace, and the server's exit
 * quits it.
 */

import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { app, dialog, session, shell } from "electron";

import { createMagpiePanel } from "./magpie.mjs";
import { APP_ID, APP_ORIGIN, isAppUrl, sandboxOff, spellcheckOn } from "./policy.mjs";
import { serverCommand, startServer } from "./server.mjs";
import { createUpdateChecker } from "./updates.mjs";
import { createWindow, hardenSession, installMenu } from "./window.mjs";

// First, before any page loads (the imports above load none): Electron reports a failed page
// load as a Node warning whose message is the whole URL, launch token included. Print only the
// warning's name (hard rules 7 and 8).
process.removeAllListeners("warning");
process.on("warning", (warning) => console.warn(warning.name));

const REPO = dirname(dirname(fileURLToPath(import.meta.url)));
/** Window icons by platform; packaged macOS and Windows show their own bundled/exe icon. */
const ICONS = app.isPackaged
  ? { linux: join(process.resourcesPath, "icon.png") }
  : {
      win32: join(REPO, "frontend", "public", "favicon.ico"),
      linux: join(REPO, "frontend", "public", "brand", "mark.png"),
    };
const MSG_NOT_RUN = "VIKI Agent can't start: its server could not be run.";
const MSG_CRASHED =
  "VIKI Agent's server stopped unexpectedly. Its log is in the logs folder of VIKI Agent's data folder.";
const MSG_SANDBOX_OFF =
  "VIKI Agent can't start with Chromium's sandbox turned off. On Linux, install VIKI Agent's .deb package, which sets the sandbox up.";

if (sandboxOff((name) => app.commandLine.hasSwitch(name))) {
  dialog.showErrorBox("VIKI Agent", MSG_SANDBOX_OFF);
  app.exit(1);
} else if (!app.requestSingleInstanceLock()) {
  // The running app gets `second-instance` and asks its server for another window.
  app.quit();
} else {
  host();
}

/** Runs the server and shows its windows until it exits. */
function host() {
  /** @type {ReturnType<typeof startServer> | null} */
  let server = null;
  let running = false;
  let quitting = false;
  /** @type {Map<number, import("electron").BrowserWindow>} by the server's window id */
  const windows = new Map();
  /** @type {Map<number, ReturnType<typeof createMagpiePanel>>} */
  const magpiePanels = new Map();

  /** @param {{ code: number | null, signal: NodeJS.Signals | null, error?: Error }} result */
  function serverExited({ code, signal, error }) {
    running = false;
    for (const win of [...windows.values()]) win.destroy();
    if (!quitting) {
      // Exit codes 2 to 9 have shown the server's own box already.
      if (error) dialog.showErrorBox("VIKI Agent", MSG_NOT_RUN);
      else if (signal || code === 1) dialog.showErrorBox("VIKI Agent", MSG_CRASHED);
    }
    app.quit();
  }

  app.enableSandbox();
  if (process.platform === "linux") {
    // Chromium's speech synthesis on Linux, as the browser launch has it (`window_argv`).
    app.commandLine.appendSwitch("enable-speech-dispatcher");
  }
  // The server ends the app, after its own grace.
  app.on("window-all-closed", () => {});
  app.on("second-instance", () => server?.wake());
  app.on("activate", () => {
    if (windows.size === 0) server?.wake();
  });
  app.on("will-quit", (event) => {
    if (!running) return;
    // Quitting stops the server at once: its stdin ends, and its exit quits the app.
    event.preventDefault();
    quitting = true;
    server.stop();
  });

  app.whenReady().then(() => {
    if (process.platform === "win32" && app.isPackaged) {
      // Before any window is created: groups taskbar/notifications under the installers' id.
      app.setAppUserModelId(APP_ID);
    }
    const spellcheck = spellcheckOn(process.platform);
    hardenSession(session.defaultSession, { origin: APP_ORIGIN, spellcheck });
    installMenu({ platform: process.platform, packaged: app.isPackaged });
    try {
      const command = serverCommand({
        env: process.env,
        repoRoot: REPO,
        packaged: app.isPackaged,
        resourcesPath: process.resourcesPath,
        platform: process.platform,
      });
      const checker = createUpdateChecker({
        packaged: app.isPackaged,
        platform: process.platform,
        feedFile: join(process.resourcesPath, "app-update.yml"),
        version: app.getVersion(),
        homepage: createRequire(import.meta.url)("./package.json").homepage,
        openUrl: (url) =>
          shell.openExternal(url).catch(() => console.warn("opening the releases page failed")),
      });
      server = startServer({
        command,
        cwd: app.isPackaged ? dirname(command[0]) : REPO,
        env: process.env,
        onOpen(id, url) {
          if (quitting || !isAppUrl(url, APP_ORIGIN)) {
            server.closed(id);
            return;
          }
          const win = createWindow(url, {
            origin: APP_ORIGIN,
            icon: ICONS[process.platform],
            devTools: !app.isPackaged,
            spellcheck,
          });
          windows.set(id, win);
          win.on("closed", () => {
            magpiePanels.get(id)?.close();
            magpiePanels.delete(id);
            windows.delete(id);
            server.closed(id);
          });
        },
        onClose(id) {
          windows.get(id)?.destroy();
        },
        onUpdateCheck(id, manual) {
          if (quitting) {
            return;
          }
          const win = windows.get(id);
          // A rejected dialog must not become an unhandled rejection in the main process.
          checker
            .check({
              manual,
              ask: ({ message, detail, buttons }) =>
                dialog
                  .showMessageBox(win && !win.isDestroyed() ? win : undefined, {
                    type: "info",
                    title: "VIKI Agent",
                    message,
                    detail,
                    buttons,
                    defaultId: 0,
                    cancelId: buttons.length - 1,
                    noLink: true,
                  })
                  .then((r) => r.response),
            })
            .catch(() => console.warn("update check failed"));
        },
        onMagpie(id, url, view) {
          const win = windows.get(id);
          if (quitting || !win || win.isDestroyed()) return;
          const existing = magpiePanels.get(id);
          if (view && existing?.viewId === view.id) {
            existing.update(view);
            return;
          }
          magpiePanels.get(id)?.close();
          const panel = createMagpiePanel(win, url, {
            view,
            onClose: () => {
              if (magpiePanels.get(id) === panel) magpiePanels.delete(id);
              if (
                panel.viewId !== null &&
                !win.isDestroyed() &&
                !win.webContents.isDestroyed() &&
                isAppUrl(win.webContents.getURL(), APP_ORIGIN)
              ) {
                void win.webContents
                  .executeJavaScript(
                    `window.dispatchEvent(new CustomEvent("viki-magpie-closed", {detail:${JSON.stringify(panel.viewId)}}))`,
                  )
                  .catch(() => {});
              }
            },
            onOpenExternal: (target) => server.openMagpieUrl(id, target),
          });
          magpiePanels.set(id, panel);
        },
        onMagpieView(id, view) {
          magpiePanels.get(id)?.update(view);
        },
        onMagpieClose(id, viewId) {
          const panel = magpiePanels.get(id);
          if (panel && panel.viewId === (viewId ?? null)) panel.close();
        },
        onExit: serverExited,
      });
      running = true;
    } catch (error) {
      // spawn() throws, rather than reporting, for a command it refuses (a .cmd on Windows).
      serverExited({ code: null, signal: null, error });
    }
  });
}
