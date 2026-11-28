import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

import { parseServerLine, serverCommand, startServer } from "../server.mjs";
import { magpieView } from "./magpie-view-fixture.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const FAKE_SERVER = join(HERE, "fake-server.mjs");
const FAKE_PARENT = join(HERE, "fake-parent.mjs");
const HANG_GUARD_MS = 10_000;

/**
 * Polls `predicate` until it is true, failing the test if `timeoutMs` passes
 * first — short enough that a server.mjs regression (a missing kill timer, a
 * callback that never fires) fails this test instead of hanging it.
 * @param {() => boolean} predicate
 * @param {number} [timeoutMs]
 */
async function waitFor(predicate, timeoutMs = HANG_GUARD_MS) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, "timed out waiting");
    await sleep(10);
  }
}

/**
 * Reads the fake server's JSON-lines log, tolerating two harmless races with
 * the child process still writing it: the file not existing yet, and its
 * last line being mid-write (no trailing newline yet, so not valid JSON).
 * Both heal on the next poll, so this returns what is cleanly readable now.
 * @param {string} logPath
 */
function readLog(logPath) {
  let content;
  try {
    content = readFileSync(logPath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") {
      return [];
    }
    throw error;
  }
  const entries = [];
  for (const line of content.split("\n")) {
    if (!line) {
      continue;
    }
    try {
      entries.push(JSON.parse(line));
    } catch {
      break; // a partially written last line; complete on the next poll
    }
  }
  return entries;
}

function collectors() {
  const opens = [];
  const closes = [];
  const exits = [];
  return {
    opens,
    closes,
    exits,
    onOpen: (id, url) => opens.push({ id, url }),
    onClose: (id) => closes.push(id),
    onExit: (result) => exits.push(result),
  };
}

/**
 * @param {import("node:test").TestContext} t
 * @param {string} mode FAKE_MODE for desktop/test/fake-server.mjs
 */
function fakeServerArgs(t, mode) {
  const dir = mkdtempSync(join(tmpdir(), "server-test-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const logPath = join(dir, "log.jsonl");
  return {
    logPath,
    command: [process.execPath, FAKE_SERVER],
    cwd: HERE,
    env: { ...process.env, FAKE_MODE: mode, FAKE_LOG: logPath },
  };
}

/**
 * @param {import("node:test").TestContext} t
 */
function fakeParentArgs(t) {
  const dir = mkdtempSync(join(tmpdir(), "server-test-parent-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const pidFile = join(dir, "pids.json");
  return {
    pidFile,
    command: [process.execPath, FAKE_PARENT],
    cwd: HERE,
    env: {
      ...process.env,
      FAKE_MODE: "stubborn",
      FAKE_LOG: join(dir, "leaf-log.jsonl"),
      FAKE_PARENT_PID_FILE: pidFile,
    },
  };
}

/**
 * Waits for fake-parent.mjs to have written its pid file, then reads it.
 * @param {string} pidFile
 * @returns {Promise<{ parent: number, leaf: number }>}
 */
async function readPids(pidFile) {
  await waitFor(() => existsSync(pidFile));
  return JSON.parse(readFileSync(pidFile, "utf8"));
}

/**
 * @param {number} pid
 * @returns {boolean} true iff a process with this pid exists (signal 0 sends nothing)
 */
function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    assert.equal(error.code, "ESRCH", `unexpected error checking pid ${pid}`);
    return false;
  }
}

test("serverCommand uses VIKI_SERVER alone when it is set", () => {
  assert.deepEqual(serverCommand({ env: { VIKI_SERVER: "/opt/viki-agent" }, repoRoot: "/repo" }), [
    "/opt/viki-agent",
  ]);
});

test("serverCommand falls back to uv run --directory <repoRoot> viki-agent", () => {
  assert.deepEqual(serverCommand({ env: {}, repoRoot: "/repo" }), [
    "uv",
    "run",
    "--directory",
    "/repo",
    "viki-agent",
  ]);
  assert.deepEqual(serverCommand({ env: { VIKI_SERVER: "" }, repoRoot: "/repo" }), [
    "uv",
    "run",
    "--directory",
    "/repo",
    "viki-agent",
  ]);
});

test("serverCommand returns the packaged binary and ignores VIKI_SERVER, win32", () => {
  assert.deepEqual(
    serverCommand({
      env: { VIKI_SERVER: "/opt/viki-agent" },
      repoRoot: "/repo",
      packaged: true,
      resourcesPath: "/res",
      platform: "win32",
    }),
    [join("/res", "viki-agent", "viki-agent.exe")],
  );
});

test("serverCommand returns the packaged binary with no .exe on darwin and linux", () => {
  for (const platform of ["darwin", "linux"]) {
    assert.deepEqual(
      serverCommand({
        env: {},
        repoRoot: "/repo",
        packaged: true,
        resourcesPath: "/res",
        platform,
      }),
      [join("/res", "viki-agent", "viki-agent")],
    );
  }
});

test("parseServerLine reads open and close, and rejects everything else", () => {
  assert.deepEqual(parseServerLine('{"type":"open","id":1,"url":"http://x"}'), {
    type: "open",
    id: 1,
    url: "http://x",
  });
  assert.deepEqual(parseServerLine('{"type":"close","id":2}'), { type: "close", id: 2 });
  assert.equal(parseServerLine("not json"), null);
  assert.equal(parseServerLine("[]"), null);
  assert.equal(parseServerLine('{"type":"open","id":0,"url":"http://x"}'), null, "id must be >= 1");
  assert.equal(
    parseServerLine('{"type":"open","id":true,"url":"http://x"}'),
    null,
    "a boolean is not a safe integer",
  );
  assert.equal(parseServerLine('{"type":"open","id":1.5,"url":"http://x"}'), null);
  assert.equal(parseServerLine('{"type":"open","id":1}'), null, "open needs a url");
  assert.equal(parseServerLine('{"type":"wat","id":1}'), null);
});

test("parseServerLine reads update_check and rejects anything but its exact shape", () => {
  assert.deepEqual(parseServerLine('{"type":"update_check","id":3,"manual":true}'), {
    type: "update_check",
    id: 3,
    manual: true,
  });
  assert.deepEqual(parseServerLine('{"type":"update_check","id":3,"manual":false}'), {
    type: "update_check",
    id: 3,
    manual: false,
  });
  assert.equal(
    parseServerLine('{"type":"update_check","id":3,"manual":true,"extra":1}'),
    null,
    "an extra key must be rejected",
  );
  assert.equal(
    parseServerLine('{"type":"update_check","id":3,"manual":"true"}'),
    null,
    "manual must be a boolean, not a string",
  );
  assert.equal(parseServerLine('{"type":"update_check","id":3}'), null, "manual is required");
  assert.equal(
    parseServerLine('{"type":"update_check","id":0,"manual":true}'),
    null,
    "id must be >= 1",
  );
});

test("the spawned child's argv is the command plus --window-host=<pid>, with no token", async (t) => {
  const { closes, onOpen, onClose, onExit, ...args } = {
    ...fakeServerArgs(t, "talk"),
    ...collectors(),
  };
  const server = startServer({ ...args, onOpen, onClose, onExit });
  t.after(() => server.stop());
  // Wait for the close that ends the talk sequence, not just the log file appearing: this
  // drains the child's whole stdout (including its one malformed line) before the test ends,
  // so its `console.warn` does not fire later during another test's console.warn capture.
  await waitFor(() => closes.length > 0);
  const [{ argv }] = readLog(args.logPath);
  assert.deepEqual(argv, [`--window-host=${process.pid}`]);
  assert.ok(
    argv.every((part) => !part.includes("token")),
    `argv must not carry the token: ${JSON.stringify(argv)}`,
  );
});

test("talk: open and close reach their callbacks; a malformed line only warns", async (t) => {
  const { opens, closes, onOpen, onClose, onExit, ...args } = {
    ...fakeServerArgs(t, "talk"),
    ...collectors(),
  };
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...warning) => warnings.push(warning);
  t.after(() => {
    console.warn = originalWarn;
  });

  const server = startServer({ ...args, onOpen, onClose, onExit });
  t.after(() => server.stop());
  await waitFor(() => closes.length > 0);

  assert.deepEqual(
    opens.map((o) => o.id),
    [1],
  );
  assert.match(opens[0].url, /#token=/);
  assert.deepEqual(closes, [1]);
  assert.deepEqual(warnings, [["ignored a message the server sent"]]);
});

test("talk: window and Magpie commands write JSON lines, and stop() ends it cleanly", async (t) => {
  const { exits, onOpen, onClose, onExit, ...args } = {
    ...fakeServerArgs(t, "talk"),
    ...collectors(),
  };
  const server = startServer({ ...args, onOpen, onClose, onExit });
  t.after(() => server.stop());
  await waitFor(() => readLog(args.logPath).length >= 1); // the child has started

  server.closed(7);
  server.wake();
  server.openMagpieUrl(7, "https://example.test/oauth?state=synthetic");
  server.openMagpieUrl(7, "file:///synthetic");
  server.openMagpieUrl(7, "http://127.0.0.1:8765/");
  await waitFor(() => readLog(args.logPath).filter((e) => "stdin" in e).length >= 3);
  const stdinLines = readLog(args.logPath)
    .filter((e) => "stdin" in e)
    .map((e) => JSON.parse(e.stdin));
  assert.ok(stdinLines.some((m) => m.type === "closed" && m.id === 7));
  assert.ok(stdinLines.some((m) => m.type === "wake"));
  assert.deepEqual(
    stdinLines.filter((m) => m.type === "magpie_open_url"),
    [{ type: "magpie_open_url", id: 7, url: "https://example.test/oauth?state=synthetic" }],
  );

  server.stop();
  await waitFor(() => exits.length > 0);
  assert.deepEqual(exits, [{ code: 0, signal: null }]);
});

test("a server line of type update_check reaches onUpdateCheck with its id and manual flag", async (t) => {
  const events = [];
  const { onOpen, onClose, onExit } = collectors();
  const script =
    'process.stdout.write(JSON.stringify({type:"update_check",id:7,manual:true})+"\\n");' +
    'process.stdout.write(JSON.stringify({type:"update_check",id:9,manual:false})+"\\n");' +
    "process.stdin.on('end', () => process.exit(0));";
  const server = startServer({
    // "--" keeps node from parsing the appended --window-host=<pid> as its own CLI flag.
    command: [process.execPath, "-e", script, "--"],
    cwd: HERE,
    env: process.env,
    onOpen,
    onClose,
    onExit,
    onUpdateCheck: (id, manual) => events.push({ id, manual }),
  });
  t.after(() => server.stop());
  await waitFor(() => events.length === 2);
  assert.deepEqual(events, [
    { id: 7, manual: true },
    { id: 9, manual: false },
  ]);
});

test("Magpie show and close reach only their dedicated callbacks", async (t) => {
  const events = [];
  const collected = collectors();
  const server = startServer({
    ...fakeServerArgs(t, "magpie"),
    ...collected,
    onMagpie: (id, url) => events.push({ type: "show", id, url }),
    onMagpieClose: (id) => events.push({ type: "hide", id }),
  });
  t.after(() => server.stop());
  await waitFor(() => events.length === 2);
  assert.deepEqual(events, [
    { type: "show", id: 1, url: `http://127.0.0.1:3430/?k=${"a".repeat(64)}` },
    { type: "hide", id: 1 },
  ]);
  assert.deepEqual(collected.opens, []);
  assert.deepEqual(collected.closes, []);
});

test("embedded Magpie show, presentation sync and ownership close reach separate callbacks", async (t) => {
  const events = [];
  const server = startServer({
    ...fakeServerArgs(t, "magpie-view"),
    ...collectors(),
    onMagpie: (id, _url, view) => events.push({ type: "show", id, view }),
    onMagpieView: (id, view) => events.push({ type: "sync", id, view }),
    onMagpieClose: (id, viewId) => events.push({ type: "hide", id, viewId }),
  });
  t.after(() => server.stop());
  await waitFor(() => events.length === 3);
  const view = magpieView();
  assert.deepEqual(events, [
    { type: "show", id: 1, view },
    { type: "sync", id: 1, view: { ...view, visible: false } },
    { type: "hide", id: 1, viewId: view.id },
  ]);
});

test("a server that exits right away reports its code once", async (t) => {
  const { exits, onOpen, onClose, onExit, ...args } = {
    ...fakeServerArgs(t, "crash"),
    ...collectors(),
  };
  const server = startServer({ ...args, onOpen, onClose, onExit });
  t.after(() => server.stop());
  await waitFor(() => exits.length > 0);
  assert.deepEqual(exits, [{ code: 1, signal: null }]);
});

test("stop() kills a server that ignores stdin ending, after its grace period, and reports exit once", async (t) => {
  const { exits, onOpen, onClose, onExit, ...args } = {
    ...fakeServerArgs(t, "stubborn"),
    ...collectors(),
  };
  const server = startServer({ ...args, onOpen, onClose, onExit, stopGraceMs: 50 });
  t.after(() => server.stop());
  await waitFor(() => existsSync(args.logPath)); // the child has actually started

  const stoppedAt = Date.now();
  server.stop();
  await waitFor(() => exits.length > 0, 5_000); // fails fast if the kill timer is gone
  assert.ok(Date.now() - stoppedAt >= 50, "must wait out the grace period before killing");
  assert.equal(exits.length, 1);
  assert.notDeepEqual(exits[0], { code: 0, signal: null }, "a clean exit means nothing was killed");
});

test("stop() ends the whole process tree behind the direct child, not just it", async (t) => {
  const { pidFile, onOpen, onClose, onExit, exits, ...args } = {
    ...fakeParentArgs(t),
    ...collectors(),
  };
  const server = startServer({ ...args, onOpen, onClose, onExit, stopGraceMs: 200 });
  t.after(() => server.stop());
  const { parent, leaf } = await readPids(pidFile);
  t.after(() => {
    // A broken kill must fail this test, not hang the run on two stubborn processes.
    for (const pid of [parent, leaf]) {
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        // Already gone.
      }
    }
  });
  await waitFor(() => isAlive(parent) && isAlive(leaf)); // both actually running

  const stoppedAt = Date.now();
  server.stop();
  await waitFor(() => exits.length > 0, 5_000);
  await waitFor(() => !isAlive(parent) && !isAlive(leaf), 5_000);
  assert.ok(Date.now() - stoppedAt >= 200, "must wait out the grace period before killing");
  assert.ok(!isAlive(parent), "the direct child (the wrapper, e.g. uv) must be gone");
  assert.ok(!isAlive(leaf), "the leaf behind it (the real server) must be gone too");
});

test("a missing executable reports the spawn error once; later calls do not throw", async (t) => {
  const { exits, onOpen, onClose, onExit } = collectors();
  const server = startServer({
    command: [`this-executable-does-not-exist-${process.pid}`],
    cwd: HERE,
    env: process.env,
    onOpen,
    onClose,
    onExit,
  });
  t.after(() => server.stop());
  await waitFor(() => exits.length > 0);
  assert.equal(exits.length, 1);
  assert.ok(exits[0].error instanceof Error);

  assert.doesNotThrow(() => {
    server.closed(1);
    server.wake();
    server.stop();
    server.stop();
  });
  await sleep(100); // give any stray async callback a chance to (wrongly) fire again
  assert.equal(exits.length, 1);
});
