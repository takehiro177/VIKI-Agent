/**
 * Fact-checks the reviewed licence table itself (`desktop/build/licences.json`): that it loads
 * and its shared texts hash-check, and that it leaves `reviewProblems` with nothing to flag over
 * both real lockfiles' production packages — on every build platform, and (when both
 * `node_modules` folders are installed) over their real bundled licence files too.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  licenceFiles,
  loadTable,
  loadTexts,
  prodPackages,
  reviewProblems,
} from "../build/licences.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = dirname(HERE);
const REPO_ROOT = dirname(DESKTOP_ROOT);
const FRONTEND_ROOT = join(REPO_ROOT, "frontend");
const BUILD_DIR = join(DESKTOP_ROOT, "build");
const TABLE_FILE = join(BUILD_DIR, "licences.json");

const PLATFORMS = ["win32", "linux", "darwin"];
const LOCKS = [
  { root: DESKTOP_ROOT, file: join(DESKTOP_ROOT, "package-lock.json") },
  { root: FRONTEND_ROOT, file: join(FRONTEND_ROOT, "package-lock.json") },
];

/** Both real lockfiles' production packages, each tagged with the root its `path` is relative to. */
function prodRows() {
  return LOCKS.flatMap(({ root, file }) => {
    const lock = JSON.parse(readFileSync(file, "utf8"));
    return prodPackages(lock).map((row) => ({ ...row, root }));
  });
}

// -- loadTable / loadTexts ---------------------------------------------------------------------

test("the real table loads with loadTable and loadTexts", () => {
  const table = loadTable(TABLE_FILE);
  assert.ok(Array.isArray(table.allowed) && table.allowed.length > 0);
  assert.ok(Object.keys(table.components).length > 0);

  const texts = loadTexts(table, BUILD_DIR);
  assert.deepEqual([...texts.keys()].sort(), [
    "bzip2-1.0.8",
    "cpython-3.14.4-incorporated",
    "cpython-3.14.7-incorporated",
    "gcc-runtime-exception-3.1",
    "gpl-3.0",
    "lgpl-2.1",
    "libedit-20240808-3.1",
    "libuuid-1.0.3",
    "mpl-2.0",
    "ncurses-6.5",
    "zstd-1.5.7",
  ]);
  for (const [id, text] of texts) {
    assert.ok(text.length > 0, `${id} text is non-empty`);
  }
});

// -- the two per-platform CPython rows and the Linux CPython texts -------------------------------

test("the two CPython rows are scoped to their own platforms", () => {
  const table = loadTable(TABLE_FILE);
  assert.deepEqual(table.components["python:cpython@3.14.7"].platforms, ["win32", "darwin"]);
  assert.deepEqual(table.components["python:cpython@3.14.4"].platforms, ["linux"]);
});

test("the six Linux CPython licence texts pin their exact sha256", () => {
  const table = loadTable(TABLE_FILE);
  const expected = {
    "cpython-3.14.4-incorporated":
      "10786818efccbce61d0540cc253fa9b256f18cda42e173c8439750545570bd8b",
    "bzip2-1.0.8": "1f38bbc7caacafd65169276d759c0d88c991b753b643ce35d0e45ea1971dd441",
    "libedit-20240808-3.1": "29cea33c32bbc9785142386377915612a2fa786482c46843383384aded2e09b1",
    "ncurses-6.5": "87a4c4442337b8968ef956031c406b74f9cb7149b7ba87311bdaba534816201c",
    "libuuid-1.0.3": "122ee1f7e258f2c3c0e538a75c037684f420454bf3850ddc74ce750bbf5fe86b",
    "zstd-1.5.7": "7055266497633c9025b777c78eb7235af13922117480ed5c674677adc381c9d8",
  };
  for (const [id, sha256] of Object.entries(expected)) {
    assert.equal(table.texts[id].sha256, sha256, id);
  }
});

// -- reviewProblems, licences only --------------------------------------------------------------

test("reviewProblems over both real lockfiles' prodPackages has no licence problems, on win32, linux and darwin", () => {
  const table = loadTable(TABLE_FILE);
  const rows = prodRows();
  assert.ok(rows.length > 150, "sanity: both lockfiles' production sets were read");

  // A placeholder, always-non-empty `texts` isolates the licence-approval rule from the
  // "no licence text" rule: that one is checked for real below, against real node_modules.
  const components = rows.map((row) => ({
    key: `npm:${row.name}@${row.version}`,
    name: row.name,
    version: row.version,
    licence: row.license,
    texts: [{ title: "placeholder", text: "placeholder" }],
  }));

  for (const platform of PLATFORMS) {
    assert.deepEqual(reviewProblems(components, table, platform), [], `platform ${platform}`);
  }
});

// -- reviewProblems, real bundled licence files -------------------------------------------------

const nodeModulesInstalled =
  existsSync(join(DESKTOP_ROOT, "node_modules")) && existsSync(join(FRONTEND_ROOT, "node_modules"));

test("with both node_modules installed, reading each production package's own licence files leaves exactly lazy-val and react-remove-scroll-bar relying on the table's mit field", {
  skip: nodeModulesInstalled
    ? false
    : "node_modules is not installed in both frontend/ and desktop/",
}, () => {
  const table = loadTable(TABLE_FILE);
  const components = prodRows().map((row) => {
    const dir = join(row.root, row.path);
    let texts = [];
    try {
      texts = licenceFiles(dir).map((name) => ({
        title: name,
        text: readFileSync(join(dir, name), "utf8"),
      }));
    } catch {
      texts = [];
    }
    return {
      key: `npm:${row.name}@${row.version}`,
      name: row.name,
      version: row.version,
      licence: row.license,
      texts,
    };
  });

  const noOwnTexts = components.filter((c) => c.texts.length === 0).map((c) => c.key);
  assert.deepEqual(noOwnTexts.sort(), ["npm:lazy-val@1.0.5", "npm:react-remove-scroll-bar@2.3.8"]);

  for (const platform of PLATFORMS) {
    assert.deepEqual(reviewProblems(components, table, platform), [], `platform ${platform}`);
  }
});
