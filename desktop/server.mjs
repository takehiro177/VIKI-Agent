/**
 * Runs the VIKI Agent server as a child process and speaks the window-host
 * protocol with it over its stdio: one JSON object per line each way.
 *
 * Server to app: open/close for a VIKI window, magpie/magpie_close for its dashboard view,
 * update_check to ask it to check for an update in a window.
 * App to server: closed/wake for windows, magpie_open_url for a dashboard's browser link.
 * End of stdin tells the server the app is gone.
 */
import { spawn } from "node:child_process";
import { join } from "node:path";
import { createInterface } from "node:readline";

import { allowsMagpieOpenUrl, magpieOrigin, parseMagpieView } from "./policy.mjs";

/**
 * @param {{
 *   env: NodeJS.ProcessEnv,
 *   repoRoot: string,
 *   packaged?: boolean,
 *   resourcesPath?: string,
 *   platform?: string,
 * }} args
 * @returns {string[]} the server executable and its arguments, before
 *   `--window-host=<pid>` is appended by {@link startServer}
 */
export function serverCommand({
  env,
  repoRoot,
  packaged = false,
  resourcesPath = "",
  platform = process.platform,
}) {
  if (packaged) {
    // The installed app carries its own compiled server; VIKI_SERVER is a dev-mode override only.
    const exe = platform === "win32" ? "viki-agent.exe" : "viki-agent";
    return [join(resourcesPath, "viki-agent", exe)];
  }
  if (env.VIKI_SERVER) {
    return [env.VIKI_SERVER];
  }
  return ["uv", "run", "--directory", repoRoot, "viki-agent"];
}

/**
 * @param {string} line one line of the server's stdout, without its newline
 * @returns {{ type: "open" | "magpie", id: number, url: string, view?: import("./policy.mjs").MagpieView } |
 *   { type: "magpie_view", id: number, view: import("./policy.mjs").MagpieView } |
 *   { type: "close" | "magpie_close", id: number, view_id?: string } |
 *   { type: "update_check", id: number, manual: boolean } | null}
 */
export function parseServerLine(line) {
  let message;
  try {
    message = JSON.parse(line);
  } catch {
    return null;
  }
  if (message === null || typeof message !== "object") {
    return null;
  }
  const { type, id, url, manual } = message;
  if (!Number.isSafeInteger(id) || id < 1) {
    return null;
  }
  if (type === "open" && typeof url === "string") {
    return { type: "open", id, url };
  }
  if (type === "close") {
    return { type: "close", id };
  }
  if (type === "update_check" && typeof manual === "boolean" && Object.keys(message).length === 3) {
    return { type: "update_check", id, manual };
  }
  if (type === "magpie" && magpieOrigin(url) !== null) {
    if (Object.keys(message).length === 3) return { type: "magpie", id, url };
    if (Object.keys(message).length === 4 && Object.hasOwn(message, "view")) {
      const view = parseMagpieView(message.view);
      if (view !== null) return { type: "magpie", id, url, view };
    }
  }
  if (type === "magpie_view" && Object.keys(message).length === 3) {
    const view = parseMagpieView(message.view);
    if (view !== null) return { type, id, view };
  }
  if (type === "magpie_close") {
    if (Object.keys(message).length === 2) return { type: "magpie_close", id };
    if (
      Object.keys(message).length === 3 &&
      typeof message.view_id === "string" &&
      /^[0-9a-f]{32}$/.test(message.view_id)
    )
      return { type, id, view_id: message.view_id };
  }
  return null;
}

/**
 * @param {{
 *   command: string[],
 *   cwd: string,
 *   env: NodeJS.ProcessEnv,
 *   onOpen: (id: number, url: string) => void,
 *   onClose: (id: number) => void,
 *   onUpdateCheck?: (id: number, manual: boolean) => void,
 *   onMagpie?: (id: number, url: string, view?: import("./policy.mjs").MagpieView) => void,
 *   onMagpieView?: (id: number, view: import("./policy.mjs").MagpieView) => void,
 *   onMagpieClose?: (id: number, viewId?: string) => void,
 *   onExit: (result: { code: number | null, signal: NodeJS.Signals | null, error?: Error }) => void,
 *   stopGraceMs?: number,
 * }} args
 * @returns {{ closed(id: number): void, wake(): void, stop(): void,
 *   openMagpieUrl(id: number, url: string): void }}
 */
export function startServer({
  command,
  cwd,
  env,
  onOpen,
  onClose,
  onUpdateCheck = () => {},
  onMagpie = () => {},
  onMagpieView = () => {},
  onMagpieClose = () => {},
  onExit,
  stopGraceMs = 15000,
}) {
  const child = spawn(command[0], [...command.slice(1), `--window-host=${process.pid}`], {
    cwd,
    env,
    stdio: ["pipe", "pipe", "inherit"],
    windowsHide: true,
    // On POSIX this child leads its own process group, so killTree() below can reach it and
    // every process it starts (uv, then the real server) with one signal. Windows has no such
    // group; killTree() walks its process tree by pid instead, so this flag is POSIX-only.
    detached: process.platform !== "win32",
  });
  child.stdin.on("error", () => {
    // The app writes `closed`/`wake` best-effort; a gone or broken pipe is not our problem.
  });

  let stopping = false;
  let exited = false;
  /** @type {NodeJS.Timeout | null} */
  let graceTimer = null;

  /** @param {{ code: number | null, signal: NodeJS.Signals | null, error?: Error }} result */
  function finish(result) {
    if (exited) {
      return;
    }
    exited = true;
    if (graceTimer !== null) {
      clearTimeout(graceTimer);
      graceTimer = null;
    }
    onExit(result);
  }

  child.on("exit", (code, signal) => finish({ code, signal }));
  child.on("error", (error) => finish({ code: null, signal: null, error }));

  createInterface({ input: child.stdout }).on("line", (line) => {
    const message = parseServerLine(line);
    if (message === null) {
      console.warn("ignored a message the server sent");
    } else if (message.type === "open") {
      onOpen(message.id, message.url);
    } else if (message.type === "magpie") {
      onMagpie(message.id, message.url, message.view);
    } else if (message.type === "magpie_view") {
      onMagpieView(message.id, message.view);
    } else if (message.type === "magpie_close") {
      onMagpieClose(message.id, message.view_id);
    } else if (message.type === "update_check") {
      onUpdateCheck(message.id, message.manual);
    } else {
      onClose(message.id);
    }
  });

  /** @param {string} line */
  function send(line) {
    if (exited || stopping) {
      return;
    }
    try {
      child.stdin.write(line + "\n");
    } catch {
      // Same best-effort handling as the stdin "error" event above.
    }
  }

  return {
    closed(id) {
      send(JSON.stringify({ type: "closed", id }));
    },
    wake() {
      send(JSON.stringify({ type: "wake" }));
    },
    openMagpieUrl(id, url) {
      if (Number.isSafeInteger(id) && id >= 1 && allowsMagpieOpenUrl(url)) {
        send(JSON.stringify({ type: "magpie_open_url", id, url }));
      }
    },
    stop() {
      if (stopping) {
        return;
      }
      stopping = true;
      try {
        child.stdin.end();
      } catch {
        // Same best-effort handling as the stdin "error" event above.
      }
      graceTimer = setTimeout(() => {
        graceTimer = null;
        if (!exited) {
          killTree(child.pid);
        }
      }, stopGraceMs);
      graceTimer.unref();
    },
  };
}

/**
 * Ends the server's whole process tree, not just this direct child: uv (or another wrapper)
 * stands between the app and the real server, and killing only the wrapper would orphan it.
 * @param {number} pid the direct child's pid
 */
function killTree(pid) {
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    }).on("error", () => {
      // Best effort: nothing more to do if taskkill itself could not even run.
    });
    return;
  }
  try {
    process.kill(-pid, "SIGKILL");
  } catch (error) {
    if (error.code !== "ESRCH") {
      throw error;
    }
  }
}
