import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { createUpdateChecker, loadUpdater, releasesUrl, UPDATE_PLATFORMS } from "../updates.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = dirname(HERE);
const UPDATES_SOURCE = readFileSync(join(DESKTOP_ROOT, "updates.mjs"), "utf8");

// Split so this file never carries the owner's handle as one literal (test_fixtures_clean.py).
const OWNER = "take" + "hiro177";
const HOMEPAGE = `https://github.com/${OWNER}/VIKI-Agent`;
const RELEASES = `${HOMEPAGE}/releases/latest`;

/** @param {Partial<Parameters<typeof createUpdateChecker>[0]>} overrides */
function baseArgs(overrides = {}) {
  return {
    packaged: true,
    platform: "win32",
    feedFile: "/fake/resources/app-update.yml",
    version: "0.2.0",
    homepage: HOMEPAGE,
    openUrl: () => {},
    exists: () => true,
    ...overrides,
  };
}

/** A `load` that fails the test if it is ever called, for branches that must never load. */
function loadMustNotRun() {
  return () => {
    throw new Error("load must not be called here");
  };
}

/** A staging id this checker must never produce: proof that a fake's starting value was replaced. */
const UNREPLACED_STAGING_ID = "unreplaced-staging-id";
/** The nil UUID `rawCheck` must install as `stagingUserIdPromise` before every real check. */
const NIL_UUID = "00000000-0000-0000-0000-000000000000";

/**
 * A fake update library shaped like the real one's default export. `impl` plays
 * `checkForUpdates`'s result or rejection; each call's flag snapshot (as this checker set them
 * just before calling) is recorded in `flagsAtCall`, and the staging id it finds in
 * `stagingUserIdPromise.value` at that same moment is recorded in `stagingIdAtCall`.
 * @param {() => Promise<unknown>} impl
 */
function fakeUpdater(impl) {
  const state = { calls: 0, flagsAtCall: [], stagingIdAtCall: [] };
  state.autoUpdater = {
    autoDownload: true,
    autoInstallOnAppQuit: true,
    logger: console,
    stagingUserIdPromise: { value: Promise.resolve(UNREPLACED_STAGING_ID) },
    async checkForUpdates() {
      state.calls++;
      state.flagsAtCall.push({
        autoDownload: state.autoUpdater.autoDownload,
        autoInstallOnAppQuit: state.autoUpdater.autoInstallOnAppQuit,
        logger: state.autoUpdater.logger,
      });
      state.stagingIdAtCall.push(await state.autoUpdater.stagingUserIdPromise.value);
      return impl();
    },
  };
  return state;
}

/** @param {{ autoUpdater: object }} updater */
function loadReturning(updater) {
  return async () => updater;
}

/**
 * A recording `ask`: pushes each box it is shown and resolves with `response` (or the next
 * item of it, if it is an array), one per call.
 * @param {number | number[]} response
 */
function recordingAsk(response) {
  const calls = [];
  let i = 0;
  const ask = async (box) => {
    calls.push(box);
    const value = Array.isArray(response) ? response[Math.min(i, response.length - 1)] : response;
    i++;
    return value;
  };
  ask.calls = calls;
  return ask;
}

/** An `ask` that fails the test if it is ever called, for branches that must show no box. */
function askMustNotRun() {
  return async () => {
    throw new Error("ask must not be called here");
  };
}

/** @returns {{ promise: Promise<unknown>, resolve: (value: unknown) => void }} */
function deferred() {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

/**
 * Captures `console.warn` calls for the test's duration, restoring the original after.
 * @param {import("node:test").TestContext} t
 * @returns {unknown[][]}
 */
function captureWarnings(t) {
  const warnings = [];
  const original = console.warn;
  console.warn = (...args) => warnings.push(args);
  t.after(() => {
    console.warn = original;
  });
  return warnings;
}

// -- UPDATE_PLATFORMS -----------------------------------------------------------------------

test("UPDATE_PLATFORMS is frozen and exactly win32, darwin and linux", () => {
  assert.deepEqual(UPDATE_PLATFORMS, ["win32", "darwin", "linux"]);
  assert.ok(Object.isFrozen(UPDATE_PLATFORMS));
});

// -- releasesUrl ------------------------------------------------------------------------------

test("releasesUrl accepts a GitHub repository homepage", () => {
  assert.equal(releasesUrl(HOMEPAGE), RELEASES);
  assert.equal(
    releasesUrl("https://github.com/a/b.c-d_e"),
    "https://github.com/a/b.c-d_e/releases/latest",
  );
});

test("releasesUrl rejects anything that is not exactly https://github.com/<owner>/<repo>", () => {
  for (const bad of [
    undefined,
    null,
    42,
    "",
    "http://github.com/o/r",
    "https://github.com/o/r/",
    "https://github.com/o/r/extra",
    "https://github.com/o",
    "https://github.com/",
    "https://not-github.com/o/r",
    "https://github.com//r",
    "https://github.com/o//",
    " https://github.com/o/r",
  ]) {
    assert.equal(releasesUrl(bad), null, JSON.stringify(bad));
  }
});

// -- unavailable: ineligible before any load --------------------------------------------------

const INELIGIBLE = [
  ["not packaged", { packaged: false }],
  ["an uncovered platform", { platform: "freebsd" }],
  ["no feed file", { exists: () => false }],
  ["a non-GitHub homepage", { homepage: `https://example.com/${OWNER}/VIKI-Agent` }],
];

for (const [label, overrides] of INELIGIBLE) {
  test(`unavailable, never loads (${label}): manual shows its box, auto shows nothing`, async () => {
    const checker = createUpdateChecker({ ...baseArgs(overrides), load: loadMustNotRun() });
    assert.equal(await checker.check({ manual: false, ask: askMustNotRun() }), "unavailable");

    const ask = recordingAsk(0);
    assert.equal(await checker.check({ manual: true, ask }), "unavailable");
    assert.deepEqual(ask.calls, [
      { message: "Updates are checked in the installed VIKI Agent.", buttons: ["OK"] },
    ]);
  });
}

test("unavailable when checkForUpdates itself resolves null, though eligible", async () => {
  const updater = fakeUpdater(async () => null);
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  assert.equal(await checker.check({ manual: false, ask: askMustNotRun() }), "unavailable");
  assert.equal(updater.calls, 1);
});

// -- current ------------------------------------------------------------------------------------

test("current: manual names the installed version; auto shows nothing", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: false,
    updateInfo: { version: "9.9.9" },
  }));
  const checker = createUpdateChecker({
    ...baseArgs({ version: "0.2.0" }),
    load: loadReturning(updater),
  });

  assert.equal(await checker.check({ manual: false, ask: askMustNotRun() }), "current");

  const ask = recordingAsk(0);
  assert.equal(await checker.check({ manual: true, ask }), "current");
  assert.deepEqual(ask.calls, [{ message: "VIKI Agent 0.2.0 is up to date.", buttons: ["OK"] }]);
  assert.equal(updater.calls, 2);
});

// -- ready --------------------------------------------------------------------------------------

test("ready shows automatically too, and Download opens the releases URL", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: true,
    updateInfo: { version: "0.3.0" },
  }));
  const opened = [];
  const checker = createUpdateChecker({
    ...baseArgs(),
    load: loadReturning(updater),
    openUrl: (url) => opened.push(url),
  });
  const ask = recordingAsk(0); // Download
  assert.equal(await checker.check({ manual: false, ask }), "ready");
  assert.deepEqual(ask.calls, [
    {
      message: "VIKI Agent 0.3.0 is ready",
      detail: "Quit VIKI Agent, then run the new installer. Your settings and chats are kept.",
      buttons: ["Download", "Later"],
    },
  ]);
  assert.deepEqual(opened, [RELEASES]);
});

test("ready: Later opens nothing", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: true,
    updateInfo: { version: "0.3.0" },
  }));
  const opened = [];
  const checker = createUpdateChecker({
    ...baseArgs(),
    load: loadReturning(updater),
    openUrl: (url) => opened.push(url),
  });
  assert.equal(await checker.check({ manual: true, ask: recordingAsk(1) }), "ready");
  assert.deepEqual(opened, []);
});

// -- announce-once -------------------------------------------------------------------------------

test("announce-once: an automatic check shows ready only the first time for a version", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: true,
    updateInfo: { version: "0.3.0" },
  }));
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  const ask = recordingAsk(1);

  assert.equal(await checker.check({ manual: false, ask }), "ready");
  assert.equal(await checker.check({ manual: false, ask }), "ready");
  assert.equal(ask.calls.length, 1, "the second automatic check for the same version shows no box");
  assert.equal(updater.calls, 2, "each call still asks the server");
});

test("announce-once does not gate a manual check: it always shows the box", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: true,
    updateInfo: { version: "0.3.0" },
  }));
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  const ask = recordingAsk(1);

  assert.equal(await checker.check({ manual: false, ask }), "ready");
  assert.equal(await checker.check({ manual: true, ask }), "ready");
  assert.equal(ask.calls.length, 2);
});

test("announce-once tracks per version: a later version announces again automatically", async () => {
  let version = "0.3.0";
  const updater = fakeUpdater(async () => ({ isUpdateAvailable: true, updateInfo: { version } }));
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  const ask = recordingAsk(1);

  assert.equal(await checker.check({ manual: false, ask }), "ready");
  version = "0.4.0";
  assert.equal(await checker.check({ manual: false, ask }), "ready");
  assert.equal(ask.calls.length, 2);
  assert.equal(ask.calls[1].message, "VIKI Agent 0.4.0 is ready");
});

// -- join-in-flight: one display per flight, even when several calls join it -------------------

test("join-in-flight: three manual Check now clicks show exactly one box and run one check", async () => {
  const gate = deferred();
  const updater = fakeUpdater(() => gate.promise);
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  const starterAsk = recordingAsk(0); // Download, if asked

  const calls = [
    checker.check({ manual: true, ask: starterAsk }), // starts the flight
    checker.check({ manual: true, ask: askMustNotRun() }), // joins; must show nothing itself
    checker.check({ manual: true, ask: askMustNotRun() }), // joins; must show nothing itself
  ];
  gate.resolve({ isUpdateAvailable: true, updateInfo: { version: "0.3.0" } });
  const states = await Promise.all(calls);

  assert.deepEqual(states, ["ready", "ready", "ready"], "every joiner resolves to the same state");
  assert.equal(updater.calls, 1, "only one real check ran");
  assert.equal(starterAsk.calls.length, 1, "exactly one box for the whole flight");
});

test("join-in-flight: a manual join upgrades the flight, so the starter's own box uses manual rules", async () => {
  const gate = deferred();
  const updater = fakeUpdater(() => gate.promise);
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  const starterAsk = recordingAsk(0); // the automatic starter's own ask; it is the only one used

  const auto = checker.check({ manual: false, ask: starterAsk }); // starts the flight
  const manual = checker.check({ manual: true, ask: askMustNotRun() }); // joins; shows nothing itself

  gate.resolve({ isUpdateAvailable: false, updateInfo: { version: "0.2.0" } });
  const [autoState, manualState] = await Promise.all([auto, manual]);

  assert.equal(updater.calls, 1, "only one real check ran");
  assert.equal(autoState, "current");
  assert.equal(manualState, "current");
  assert.deepEqual(
    starterAsk.calls,
    [{ message: "VIKI Agent 0.2.0 is up to date.", buttons: ["OK"] }],
    "the join upgrades the flight to manual rules: 'current' now shows the up-to-date box",
  );
});

test("join-in-flight: once settled, a later call starts a fresh check and shows its own box", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: false,
    updateInfo: { version: "0.2.0" },
  }));
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  const ask = recordingAsk(0);
  await checker.check({ manual: true, ask });
  await checker.check({ manual: true, ask });
  assert.equal(updater.calls, 2);
  assert.equal(ask.calls.length, 2, "each settled flight shows its own box, not a joined one");
});

// -- bad version: untrusted feed text never reaches a box --------------------------------------

test("bad version (not x.y.z) fails the check; an automatic call shows no box and warns nothing", async (t) => {
  const warnings = captureWarnings(t);
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: true,
    updateInfo: { version: "not-a-version" },
  }));
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  assert.equal(await checker.check({ manual: false, ask: askMustNotRun() }), "failed");
  assert.deepEqual(warnings, []);
});

test("bad version still gets the generic failed box when the call is manual", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: true,
    updateInfo: { version: "v1" },
  }));
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });
  const ask = recordingAsk(0);
  assert.equal(await checker.check({ manual: true, ask }), "failed");
  assert.deepEqual(ask.calls, [
    {
      message: "VIKI Agent couldn't check for updates.",
      detail: "Check your internet connection, then choose Check now again.",
      buttons: ["OK"],
    },
  ]);
});

// A real "latest" release can be tagged with a valid semver electron-updater accepts (a
// pre-release or build suffix) or carry incidental whitespace; none of these contain an x.y.z
// substring the un-anchored half of SEMVER_LIKE would also reject, so each one pins the anchors.
const BAD_VERSION_ANCHORS = ["0.3.0-rc.1", "0.3.0+x", " 0.3.0", "0.3.0 "];

for (const bad of BAD_VERSION_ANCHORS) {
  test(`bad version anchor ${JSON.stringify(bad)} fails the check: no automatic box, no openUrl`, async () => {
    const updater = fakeUpdater(async () => ({
      isUpdateAvailable: true,
      updateInfo: { version: bad },
    }));
    const opened = [];
    const checker = createUpdateChecker({
      ...baseArgs(),
      load: loadReturning(updater),
      openUrl: (url) => opened.push(url),
    });
    assert.equal(await checker.check({ manual: false, ask: askMustNotRun() }), "failed");
    assert.deepEqual(opened, []);
  });
}

// -- failed: network/feed errors, fixed warning text, manual box -------------------------------

test("a rejecting checkForUpdates fails the check and warns only the fixed text", async (t) => {
  const warnings = captureWarnings(t);
  const updater = fakeUpdater(async () => {
    throw new Error("network is down, 10.0.0.1");
  });
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });

  assert.equal(await checker.check({ manual: false, ask: askMustNotRun() }), "failed");
  assert.deepEqual(warnings, [["update check failed"]]);

  const ask = recordingAsk(0);
  assert.equal(await checker.check({ manual: true, ask }), "failed");
  assert.deepEqual(ask.calls, [
    {
      message: "VIKI Agent couldn't check for updates.",
      detail: "Check your internet connection, then choose Check now again.",
      buttons: ["OK"],
    },
  ]);
});

test("a throwing load fails the check and warns only the fixed text", async (t) => {
  const warnings = captureWarnings(t);
  const checker = createUpdateChecker({
    ...baseArgs(),
    load: async () => {
      throw new Error("could not load the update library, https://example.invalid/secret");
    },
  });
  assert.equal(await checker.check({ manual: false, ask: askMustNotRun() }), "failed");
  assert.deepEqual(warnings, [["update check failed"]]);
});

// -- load runs at most once; the update flags are reset before every real check ----------------

test("load runs at most once across repeated checks, including a manual one", async () => {
  let loads = 0;
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: false,
    updateInfo: { version: "0.2.0" },
  }));
  const checker = createUpdateChecker({
    ...baseArgs(),
    load: async () => {
      loads++;
      return updater;
    },
  });
  await checker.check({ manual: false, ask: askMustNotRun() });
  await checker.check({ manual: false, ask: askMustNotRun() });
  await checker.check({ manual: true, ask: recordingAsk(0) });
  assert.equal(loads, 1);
  assert.equal(updater.calls, 3);
});

test("autoDownload, autoInstallOnAppQuit and logger are reset before every checkForUpdates call", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: false,
    updateInfo: { version: "0.2.0" },
  }));
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });

  await checker.check({ manual: false, ask: askMustNotRun() });
  // Simulate drift between calls: if the reset only ran once at load time, this would stick.
  updater.autoUpdater.autoDownload = true;
  updater.autoUpdater.autoInstallOnAppQuit = true;
  updater.autoUpdater.logger = console;
  await checker.check({ manual: false, ask: askMustNotRun() });

  assert.deepEqual(updater.flagsAtCall, [
    { autoDownload: false, autoInstallOnAppQuit: false, logger: null },
    { autoDownload: false, autoInstallOnAppQuit: false, logger: null },
  ]);
});

// -- no install id: stagingUserIdPromise is replaced before every real check -------------------

test("stagingUserIdPromise resolves to the nil UUID at the moment checkForUpdates is called, on the first and a second check", async () => {
  const updater = fakeUpdater(async () => ({
    isUpdateAvailable: false,
    updateInfo: { version: "0.2.0" },
  }));
  const checker = createUpdateChecker({ ...baseArgs(), load: loadReturning(updater) });

  await checker.check({ manual: false, ask: askMustNotRun() });
  await checker.check({ manual: false, ask: askMustNotRun() });

  assert.deepEqual(updater.stagingIdAtCall, [NIL_UUID, NIL_UUID]);
});

// -- static scans: no banned names, and the flags are set before the call, textually -----------

test("updates.mjs never names checkForUpdatesAndNotify, downloadUpdate or quitAndInstall", () => {
  for (const banned of ["checkForUpdatesAndNotify", "downloadUpdate", "quitAndInstall"]) {
    assert.ok(!UPDATES_SOURCE.includes(banned), `must not name ${banned}`);
  }
});

test("autoDownload is set to false before the checkForUpdates() call, textually", () => {
  const flagIndex = UPDATES_SOURCE.indexOf("autoDownload = false");
  // The JSDoc type for `load` also mentions "checkForUpdates(" (as a method signature), earlier
  // in the file than the real call: search for the actual call site, `updater.checkForUpdates(`.
  const callIndex = UPDATES_SOURCE.indexOf("updater.checkForUpdates(");
  assert.ok(flagIndex !== -1, "autoDownload = false must appear");
  assert.ok(callIndex !== -1, "updater.checkForUpdates( must appear");
  assert.ok(flagIndex < callIndex, "autoDownload must be set false before checkForUpdates() runs");
});

test("stagingUserIdPromise is set to the nil UUID before the checkForUpdates() call, textually", () => {
  assert.ok(
    UPDATES_SOURCE.includes(`"${NIL_UUID}"`),
    "the nil UUID literal must appear, so no real staging id is ever generated",
  );
  const stagingIndex = UPDATES_SOURCE.indexOf("stagingUserIdPromise = {");
  const callIndex = UPDATES_SOURCE.indexOf("updater.checkForUpdates(");
  assert.ok(stagingIndex !== -1, "stagingUserIdPromise = { must appear");
  assert.ok(callIndex !== -1, "updater.checkForUpdates( must appear");
  assert.ok(
    stagingIndex < callIndex,
    "stagingUserIdPromise must be replaced before checkForUpdates() runs",
  );
});

// -- the module itself: no real update library at the top level --------------------------------

test("updates.mjs's only static import is node:fs", () => {
  const importLines = UPDATES_SOURCE.split("\n").filter((line) => /^\s*import\b/.test(line));
  assert.equal(importLines.length, 1, "exactly one static import line");
  assert.match(importLines[0], /from\s+"node:fs"/);
});

test("updates.mjs names electron-updater exactly once, in the dynamic import inside loadUpdater", () => {
  const occurrences = UPDATES_SOURCE.split("electron-updater").length - 1;
  assert.equal(occurrences, 1, "electron-updater must be named exactly once in this file");

  const lines = UPDATES_SOURCE.split("\n");
  const loadUpdaterLine = lines.findIndex((line) => /function\s+loadUpdater\s*\(/.test(line));
  const importLine = lines.findIndex((line) => line.includes("electron-updater"));
  assert.ok(loadUpdaterLine !== -1, "loadUpdater must be declared");
  assert.ok(
    importLine > loadUpdaterLine,
    "the only mention of electron-updater must come after loadUpdater's declaration",
  );
  assert.match(lines[importLine], /await import\("electron-updater"\)/);
});

test("loadUpdater is exported for use as createUpdateChecker's default load", () => {
  assert.equal(typeof loadUpdater, "function");
});
