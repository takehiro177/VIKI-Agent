/**
 * Checks a packaged build's on-disk layout, fuses, ASAR integrity and bundle id, plus the
 * third-party notices file every installer must carry. Every test is skipped unless
 * VIKI_PACKAGED_APP names a packaged executable that exists, so this suite is silent on an
 * ordinary checkout. This file's top level imports only node: built-ins, ./packaged-paths.mjs
 * and ../build/notices.mjs (which, like ./packaged-paths.mjs, has no node_modules dependency of
 * its own — it and the licences.mjs it re-exports from use only node: built-ins), so it loads
 * with no node_modules installed at all; inside the tests that need them, electron-builder's own
 * copies of @electron/fuses and @electron/asar are loaded through a createRequire anchored at
 * app-builder-lib's package.json, so this checks the exact copies electron-builder itself
 * packages with, not whatever happens to be hoisted.
 */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { SECTION_TITLES } from "../build/notices.mjs";
import { packagedPaths } from "./packaged-paths.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = dirname(HERE);
const APP = process.env.VIKI_PACKAGED_APP;
const PKG = JSON.parse(readFileSync(join(DESKTOP_ROOT, "package.json"), "utf8"));

/**
 * A require() that resolves electron-builder's own @electron/fuses and @electron/asar: the
 * copies it actually packages a build with, reached the same way electron-builder itself would
 * reach them, rather than whatever version happens to be hoisted to desktop/node_modules.
 * @returns {NodeRequire}
 */
function builderRequire() {
  const desktopRequire = createRequire(join(DESKTOP_ROOT, "package.json"));
  return createRequire(desktopRequire.resolve("app-builder-lib/package.json"));
}

/**
 * package.json's `build.electronFuses` keys that are indexed fuse options, mapped to their
 * FuseV1Options index. Excludes `resetAdHocDarwinSignature`, a sibling flag that configures
 * fuse-flipping itself rather than naming a fuse in the wire `getCurrentFuseWire` returns.
 * @param {Record<string, number>} FuseV1Options
 * @returns {Record<string, number>}
 */
function fuseNameToOption(FuseV1Options) {
  return {
    runAsNode: FuseV1Options.RunAsNode,
    enableCookieEncryption: FuseV1Options.EnableCookieEncryption,
    enableNodeOptionsEnvironmentVariable: FuseV1Options.EnableNodeOptionsEnvironmentVariable,
    enableNodeCliInspectArguments: FuseV1Options.EnableNodeCliInspectArguments,
    enableEmbeddedAsarIntegrityValidation: FuseV1Options.EnableEmbeddedAsarIntegrityValidation,
    onlyLoadAppFromAsar: FuseV1Options.OnlyLoadAppFromAsar,
    grantFileProtocolExtraPrivileges: FuseV1Options.GrantFileProtocolExtraPrivileges,
  };
}

/**
 * @param {string[]} entries as `@electron/asar`'s `listPackage` returns them (e.g. `\main.mjs`
 *   on Windows, `/main.mjs` on POSIX)
 * @returns {string[][]} each entry split into its path segments, with empty segments (the
 *   leading separator) dropped
 */
function splitEntries(entries) {
  return entries.map((entry) => entry.split(/[\\/]/).filter((segment) => segment.length > 0));
}

/**
 * Registers `name` as a test, skipped unless VIKI_PACKAGED_APP is set. A set variable naming a
 * missing path fails rather than skips: setting it is deliberate, so a wrong path must not turn
 * every check into a silent pass. `fn` gets the paths computed from it, plus the test context
 * for a platform-only skip.
 * @param {string} name
 * @param {(
 *   paths: ReturnType<typeof packagedPaths>,
 *   t: import("node:test").TestContext,
 * ) => void | Promise<void>} fn
 */
function packagedTest(name, fn) {
  test(name, (t) => {
    if (!APP) {
      t.skip("VIKI_PACKAGED_APP is not set");
      return undefined;
    }
    assert.equal(existsSync(APP), true, `VIKI_PACKAGED_APP names a missing path: ${APP}`);
    return fn(packagedPaths(APP, process.platform), t);
  });
}

// -- fuses (ADR-053) -----------------------------------------------------------------------------

packagedTest("fuses match package.json's build.electronFuses", async (paths) => {
  const require = builderRequire();
  const { FuseV1Options, getCurrentFuseWire } = require("@electron/fuses");
  // FuseState lives in its own submodule: @electron/fuses's main entry re-exports ./config
  // (FuseV1Options, FuseVersion) but not ./constants (FuseState, SENTINEL).
  const { FuseState } = require("@electron/fuses/dist/constants");
  const nameToOption = fuseNameToOption(FuseV1Options);
  const wire = await getCurrentFuseWire(paths.fuseTarget);
  for (const [name, option] of Object.entries(nameToOption)) {
    const expected = PKG.build.electronFuses[name] ? FuseState.ENABLE : FuseState.DISABLE;
    assert.equal(wire[option], expected, `${name} (fuse index ${option})`);
  }
});

// -- asar layout ---------------------------------------------------------------------------

packagedTest(
  "the asar contains the desktop modules, fonts, package.json and runtime dependencies",
  (paths) => {
    const { listPackage } = builderRequire()("@electron/asar");
    const entries = listPackage(paths.asar, { isPack: false });
    const topLevelNames = splitEntries(entries)
      .filter((segments) => segments.length === 1)
      .map((segments) => segments[0]);
    assert.deepEqual([...new Set(topLevelNames)].sort(), [
      "fonts",
      "magpie-theme.mjs",
      "magpie.mjs",
      "main.mjs",
      "node_modules",
      "package.json",
      "policy.mjs",
      "server.mjs",
      "updates.mjs",
      "window.mjs",
    ]);
    const fontFiles = splitEntries(entries)
      .filter((segments) => segments[0] === "fonts" && segments.length === 2)
      .map((segments) => segments[1]);
    assert.deepEqual(fontFiles.sort(), [
      "ibm-plex-mono-latin-400-normal.woff2",
      "ibm-plex-mono-latin-500-normal.woff2",
      "ibm-plex-sans-latin-400-normal.woff2",
      "ibm-plex-sans-latin-500-normal.woff2",
      "ibm-plex-sans-latin-600-normal.woff2",
    ]);
  },
);

packagedTest("node_modules/electron-updater/package.json is inside the asar", (paths) => {
  const { listPackage } = builderRequire()("@electron/asar");
  const entries = listPackage(paths.asar, { isPack: false });
  const joined = splitEntries(entries).map((segments) => segments.join("/"));
  assert.ok(joined.includes("node_modules/electron-updater/package.json"));
});

packagedTest(
  "no resources/app folder ships, and app-update.yml names the public release",
  (paths) => {
    assert.equal(existsSync(join(paths.resources, "app")), false, "resources/app must not exist");
    // The update check's feed (desktop/updates.mjs): build.publish, written by electron-builder.
    const feed = readFileSync(join(paths.resources, "app-update.yml"), "utf8").split(/\r?\n/);
    for (const line of [
      "provider: github",
      `owner: ${PKG.build.publish.owner}`,
      "repo: VIKI-Agent",
      "releaseType: draft",
    ]) {
      assert.ok(feed.includes(line), `app-update.yml must hold "${line}"`);
    }
  },
);

// -- backend binary -------------------------------------------------------------------

packagedTest("the backend exists and, on POSIX, is owner-executable", (paths) => {
  assert.equal(existsSync(paths.backend), true, paths.backend);
  if (process.platform !== "win32") {
    const mode = statSync(paths.backend).mode;
    assert.equal((mode & 0o100) !== 0, true, "owner-executable bit (mode & 0o100)");
  }
});

// -- Electron's own licence files -----------------------------------------------------------

packagedTest("Electron's licence files ship beside the exe, or in Resources on darwin", (paths) => {
  const licenceDir = process.platform === "darwin" ? paths.resources : dirname(APP);
  for (const name of ["LICENSE.electron.txt", "LICENSES.chromium.html"]) {
    assert.equal(existsSync(join(licenceDir, name)), true, name);
  }
});

// -- third-party notices ---------------------------------------------------------------------

packagedTest("the notices ship in resources with every section", (paths) => {
  const noticesPath = join(paths.resources, "THIRD-PARTY-NOTICES.txt");
  assert.equal(existsSync(noticesPath), true, noticesPath);
  const text = readFileSync(noticesPath, "utf8");
  for (const title of SECTION_TITLES) {
    assert.ok(text.includes(title), title);
  }
});

// -- macOS Info.plist -----------------------------------------------------------------------

packagedTest(
  "darwin: Info.plist carries the bundle id, extendInfo and asar integrity",
  (paths, t) => {
    if (process.platform !== "darwin") {
      t.skip("darwin only");
      return;
    }
    const json = execFileSync("plutil", ["-convert", "json", "-o", "-", paths.plist], {
      encoding: "utf8",
    });
    const plist = JSON.parse(json);
    assert.equal(plist.CFBundleIdentifier, PKG.build.appId);
    for (const [key, value] of Object.entries(PKG.build.mac.extendInfo)) {
      assert.equal(plist[key], value, key);
    }
    assert.ok(
      plist.ElectronAsarIntegrity && "Resources/app.asar" in plist.ElectronAsarIntegrity,
      "ElectronAsarIntegrity must cover Resources/app.asar",
    );
  },
);
