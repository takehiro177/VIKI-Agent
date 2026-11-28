/**
 * The desktop update checker: looks for a newer VIKI Agent release and tells the user with a
 * native dialog box. It only checks — it never downloads or installs anything itself; a
 * "Download" click opens the public releases page in the user's browser, and the user runs the
 * new installer by hand. `main.mjs` calls `check` both for the periodic automatic check and for
 * the Settings "Check now" button.
 */
import { existsSync } from "node:fs";

/** Platforms the update check covers. */
export const UPDATE_PLATFORMS = Object.freeze(["win32", "darwin", "linux"]);

/** A fetched release version must look like this before it is trusted into a dialog's text. */
const SEMVER_LIKE = /^\d+\.\d+\.\d+$/;

/** Only this exact shape of homepage names a releases page worth opening. */
const GITHUB_HOMEPAGE = /^https:\/\/github\.com\/[A-Za-z0-9-]+\/[A-Za-z0-9._-]+$/;

/**
 * @param {unknown} homepage
 * @returns {string | null} the public release page a "Download" click opens, or null when
 *   `homepage` is not a `https://github.com/<owner>/<repo>` URL
 */
export function releasesUrl(homepage) {
  return typeof homepage === "string" && GITHUB_HOMEPAGE.test(homepage)
    ? `${homepage}/releases/latest`
    : null;
}

/** @typedef {"ready" | "current" | "unavailable" | "failed"} UpdateState */
/** @typedef {{ message: string, detail?: string, buttons: string[] }} UpdateBox */

/**
 * @param {{
 *   packaged: boolean,
 *   platform: string,
 *   feedFile: string,
 *   version: string,
 *   homepage: unknown,
 *   openUrl: (url: string) => void,
 *   exists?: (path: string) => boolean,
 *   load?: () => Promise<{ autoUpdater: {
 *     autoDownload: boolean,
 *     autoInstallOnAppQuit: boolean,
 *     logger: unknown,
 *     stagingUserIdPromise: unknown,
 *     checkForUpdates(): Promise<{ isUpdateAvailable: boolean, updateInfo: { version: string } } | null>,
 *   } }>,
 * }} args
 * @returns {{ check(args: { manual: boolean, ask: (box: UpdateBox) => Promise<number> }): Promise<UpdateState> }}
 */
export function createUpdateChecker({
  packaged,
  platform,
  feedFile,
  version,
  homepage,
  openUrl,
  exists = existsSync,
  load = loadUpdater,
}) {
  const releases = releasesUrl(homepage);
  const eligible =
    packaged && UPDATE_PLATFORMS.includes(platform) && exists(feedFile) && releases !== null;
  /** @type {{ autoUpdater: { autoDownload: boolean, autoInstallOnAppQuit: boolean, logger: unknown, stagingUserIdPromise: unknown, checkForUpdates(): Promise<unknown> } } | null} loaded at most once, for this checker's whole life */
  let loaded = null;
  /** @type {{ promise: Promise<{ state: UpdateState, version?: string }>, manual: boolean } | null} the one real check in flight, and whether to show it under manual rules; a call while this is set joins it instead of starting another */
  let inFlight = null;
  /** The version last shown in a "ready" box, so an automatic check announces each version once. */
  let announced = null;

  /** Runs the real check. Never throws or rejects: a load or network failure becomes "failed". */
  async function rawCheck() {
    if (!eligible) {
      return { state: "unavailable" };
    }
    try {
      if (loaded === null) {
        loaded = await load();
      }
      const updater = loaded.autoUpdater;
      updater.autoDownload = false;
      updater.autoInstallOnAppQuit = false;
      updater.logger = null;
      // No install id ever leaves this machine: the pinned update library (6.8.10) would
      // otherwise create a persistent `<userData>/.updaterId` and send it as `x-user-staging-id`.
      updater.stagingUserIdPromise = {
        value: Promise.resolve("00000000-0000-0000-0000-000000000000"),
      };
      const result = await updater.checkForUpdates();
      if (result === null) {
        return { state: "unavailable" };
      }
      const latest = result.updateInfo?.version;
      if (typeof latest !== "string" || !SEMVER_LIKE.test(latest)) {
        // An untrusted version string from the feed must never reach a dialog's text.
        return { state: "failed" };
      }
      return result.isUpdateAvailable ? { state: "ready", version: latest } : { state: "current" };
    } catch {
      console.warn("update check failed");
      return { state: "failed" };
    }
  }

  /**
   * @param {{ state: UpdateState, version?: string }} result
   * @param {boolean} manual
   * @param {(box: UpdateBox) => Promise<number>} ask
   */
  async function showBox(result, manual, ask) {
    if (result.state === "ready") {
      if (!manual && announced === result.version) {
        return;
      }
      announced = result.version;
      const response = await ask({
        message: `VIKI Agent ${result.version} is ready`,
        detail: "Quit VIKI Agent, then run the new installer. Your settings and chats are kept.",
        buttons: ["Download", "Later"],
      });
      if (response === 0) {
        openUrl(releases);
      }
      return;
    }
    if (!manual) {
      return;
    }
    if (result.state === "current") {
      await ask({ message: `VIKI Agent ${version} is up to date.`, buttons: ["OK"] });
    } else if (result.state === "unavailable") {
      await ask({ message: "Updates are checked in the installed VIKI Agent.", buttons: ["OK"] });
    } else {
      await ask({
        message: "VIKI Agent couldn't check for updates.",
        detail: "Check your internet connection, then choose Check now again.",
        buttons: ["OK"],
      });
    }
  }

  return {
    async check({ manual, ask }) {
      // Only the call that starts a flight shows a box for it: three "Check now" clicks during
      // one network round trip must show one box, not three. A manual joiner still upgrades the
      // flight to manual rules (e.g. a "current" result then shows the up-to-date box).
      const starting = inFlight === null;
      if (starting) {
        const promise = rawCheck();
        inFlight = { promise, manual };
        promise.finally(() => {
          inFlight = null;
        });
      } else if (manual) {
        inFlight.manual = true;
      }
      const flight = inFlight;
      const result = await flight.promise;
      if (starting) {
        await showBox(result, flight.manual, ask);
      }
      return result.state;
    },
  };
}

/**
 * Loads the real update library. Its own function, called only from inside
 * {@link createUpdateChecker}'s `rawCheck`, and never at this module's top level, so importing
 * this file never loads it.
 * @returns {Promise<{ autoUpdater: object }>}
 */
export async function loadUpdater() {
  return (await import("electron-updater")).default;
}
