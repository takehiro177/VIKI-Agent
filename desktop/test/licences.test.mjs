import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  isAllowed,
  licenceFiles,
  loadTable,
  loadTexts,
  mitText,
  prodPackages,
  reviewProblems,
  rowFor,
  spdxIds,
} from "../build/licences.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DESKTOP_ROOT = dirname(HERE);
const REPO_ROOT = dirname(DESKTOP_ROOT);

// -- prodPackages -------------------------------------------------------------------------------

test("prodPackages keeps desktop's real semver@7.7.4 production/dev pair once, as the production path", () => {
  const lock = JSON.parse(readFileSync(join(DESKTOP_ROOT, "package-lock.json"), "utf8"));
  const rows = prodPackages(lock);
  assert.deepEqual(
    rows.filter((row) => row.name === "semver"),
    [
      {
        path: "node_modules/electron-updater/node_modules/semver",
        name: "semver",
        version: "7.7.4",
        license: "ISC",
      },
    ],
  );
  assert.equal(
    rows.length,
    16,
    "desktop/package-lock.json's production set (app.asar's 16 packages)",
  );
});

test("prodPackages drops dev, devOptional and link entries, keeping a scoped name intact whether nested or not", () => {
  const lock = {
    packages: {
      "": { name: "root" },
      "node_modules/@scope/top": { version: "1.0.0", license: "MIT" },
      "node_modules/host/node_modules/@scope/nested": { version: "2.0.0", license: "ISC" },
      "node_modules/skip-dev": { version: "1.0.0", license: "MIT", dev: true },
      "node_modules/skip-dev-optional": { version: "1.0.0", license: "MIT", devOptional: true },
      "node_modules/skip-link": { version: "1.0.0", license: "MIT", link: true },
    },
  };
  assert.deepEqual(prodPackages(lock), [
    {
      path: "node_modules/host/node_modules/@scope/nested",
      name: "@scope/nested",
      version: "2.0.0",
      license: "ISC",
    },
    { path: "node_modules/@scope/top", name: "@scope/top", version: "1.0.0", license: "MIT" },
  ]);
});

test("prodPackages sorts by name@version regardless of input order", () => {
  const lock = {
    packages: {
      "": {},
      "node_modules/zeta": { version: "1.0.0", license: "MIT" },
      "node_modules/alpha": { version: "2.0.0", license: "MIT" },
      "node_modules/alpha-pair": { version: "1.0.0", license: "MIT" },
    },
  };
  assert.deepEqual(
    prodPackages(lock).map((row) => `${row.name}@${row.version}`),
    ["alpha-pair@1.0.0", "alpha@2.0.0", "zeta@1.0.0"],
  );
});

// -- licenceFiles ---------------------------------------------------------------------------

test("licenceFiles finds licence-like files case-insensitively and sorted, skipping folders", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "licence-files-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "LICENSE"), "a");
  writeFileSync(join(dir, "license.md"), "b");
  writeFileSync(join(dir, "COPYING"), "c");
  writeFileSync(join(dir, "NOTICE.txt"), "d");
  writeFileSync(join(dir, "readme.md"), "e");
  mkdirSync(join(dir, "licenses")); // a folder that also matches the name pattern; must be skipped
  assert.deepEqual(licenceFiles(dir), ["COPYING", "LICENSE", "NOTICE.txt", "license.md"]);
});

// -- spdxIds --------------------------------------------------------------------------------

test("spdxIds extracts ids, dropping AND/OR/WITH and parentheses but keeping a WITH exception", () => {
  assert.deepEqual(spdxIds("MIT"), ["MIT"]);
  assert.deepEqual(spdxIds("(Apache-2.0 OR BSD-3-Clause)"), ["Apache-2.0", "BSD-3-Clause"]);
  assert.deepEqual(spdxIds("MPL-2.0 AND MIT"), ["MPL-2.0", "MIT"]);
  assert.deepEqual(spdxIds("GPL-3.0-or-later WITH GCC-exception-3.1"), [
    "GPL-3.0-or-later",
    "GCC-exception-3.1",
  ]);
});

test("spdxIds throws on empty input and on a token that is not a licence id", () => {
  assert.throws(() => spdxIds(""), /names no licence id/);
  assert.throws(() => spdxIds("AND OR"), /names no licence id/);
  assert.throws(() => spdxIds("MIT; rm -rf /"), /not a licence id/);
});

// -- isAllowed --------------------------------------------------------------------------------

test("isAllowed checks every id an expression names, including a WITH exception", () => {
  assert.equal(isAllowed("MIT", ["MIT", "ISC"]), true);
  assert.equal(isAllowed("Apache-2.0 OR BSD-3-Clause", ["MIT"]), false);
  assert.equal(
    isAllowed("GPL-3.0-or-later WITH GCC-exception-3.1", ["GPL-3.0-or-later"]),
    false,
    "the exception id itself must also be allowed, not just the licence it qualifies",
  );
});

// -- mitText ----------------------------------------------------------------------------------

test("mitText's body matches frontend/public/licenses/libraries-dev.txt lines 9-25 verbatim", () => {
  const source = readFileSync(
    join(REPO_ROOT, "frontend", "public", "licenses", "libraries-dev.txt"),
    "utf8",
  );
  const body = source.split(/\r?\n/).slice(8, 25).join("\n");
  const copyright = "Copyright (c) 2026 Example Author";
  assert.equal(mitText(copyright), ["MIT License", "", copyright, "", body].join("\n"));
});

// -- loadTable --------------------------------------------------------------------------------

test("loadTable loads a well-shaped table unchanged", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "licence-table-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, "licences.json");
  const data = {
    allowed: ["MIT"],
    texts: { "mpl-2.0": { file: "notices/mpl-2.0.txt", sha256: "abc", source: "https://e" } },
    components: { "npm:foo@1.0.0": { licence: "MIT" } },
  };
  writeFileSync(file, JSON.stringify(data));
  assert.deepEqual(loadTable(file), data);
});

test("loadTable names the bad key for each shape problem", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "licence-table-bad-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const write = (name, data) => {
    const file = join(dir, name);
    writeFileSync(file, JSON.stringify(data));
    return file;
  };
  assert.throws(() => loadTable(write("not-object.json", "nope")), /not an object/);
  assert.throws(
    () => loadTable(write("bad-allowed.json", { allowed: "MIT", texts: {}, components: {} })),
    /"allowed"/,
  );
  assert.throws(
    () => loadTable(write("bad-texts.json", { allowed: [], texts: [], components: {} })),
    /"texts"/,
  );
  assert.throws(
    () =>
      loadTable(
        write("bad-text-entry.json", {
          allowed: [],
          texts: { "mpl-2.0": { file: "x.txt" } },
          components: {},
        }),
      ),
    /"texts\.mpl-2\.0"/,
  );
  assert.throws(
    () => loadTable(write("bad-components.json", { allowed: [], texts: {}, components: [] })),
    /"components"/,
  );
  assert.throws(
    () =>
      loadTable(
        write("bad-component-row.json", {
          allowed: [],
          texts: {},
          components: { "npm:foo@1.0.0": "nope" },
        }),
      ),
    /"components\.npm:foo@1\.0\.0"/,
  );
});

test("loadTable accepts a by_platform map giving each of a row's platforms its own texts and note", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "licence-table-by-platform-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const file = join(dir, "licences.json");
  const data = {
    allowed: [],
    texts: {},
    components: {
      "python:cpython@1.0.0": {
        platforms: ["win32", "darwin"],
        by_platform: { win32: { texts: ["a"], note: "Windows" }, darwin: { note: "macOS" } },
      },
    },
  };
  writeFileSync(file, JSON.stringify(data));
  assert.deepEqual(loadTable(file), data);
});

test("loadTable refuses a by_platform map that does not give exactly the row's platforms only texts and a note", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "licence-table-by-platform-bad-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const rows = {
    "no platforms": { by_platform: { darwin: { note: "x" } } },
    "not an object": { platforms: ["darwin"], by_platform: [] },
    "a platform the row does not list": {
      platforms: ["win32"],
      by_platform: { win32: { note: "x" }, macos: { note: "y" } },
    },
    "a listed platform left out": {
      platforms: ["win32", "darwin"],
      by_platform: { win32: { note: "x" } },
    },
    "an entry that is not an object": { platforms: ["darwin"], by_platform: { darwin: "x" } },
    "a field other than texts and note": {
      platforms: ["darwin"],
      by_platform: { darwin: { text: ["a"] } },
    },
    "a licence": { platforms: ["darwin"], by_platform: { darwin: { licence: "MIT" } } },
    "texts that are not strings": {
      platforms: ["darwin"],
      by_platform: { darwin: { texts: "a" } },
    },
    "a note that is not a string": { platforms: ["darwin"], by_platform: { darwin: { note: 1 } } },
  };
  for (const [name, row] of Object.entries(rows)) {
    const file = join(dir, `${name}.json`);
    writeFileSync(
      file,
      JSON.stringify({ allowed: [], texts: {}, components: { "python:cpython@1.0.0": row } }),
    );
    assert.throws(() => loadTable(file), /"components\.python:cpython@1\.0\.0\.by_platform"/, name);
  }
});

// -- rowFor -----------------------------------------------------------------------------------

test("rowFor takes texts and note from the platform's by_platform entry and keeps the rest of the row", () => {
  const row = {
    licence: "PSF-2.0",
    platforms: ["win32", "darwin"],
    files: ["lib*.dylib"],
    texts: ["base"],
    note: "Base note.",
    by_platform: { win32: { texts: ["win"] }, darwin: { texts: ["mac", "zstd"], note: "macOS." } },
  };
  assert.deepEqual(rowFor(row, "darwin"), {
    ...row,
    texts: ["mac", "zstd"],
    note: "macOS.",
  });
  assert.deepEqual(rowFor(row, "win32"), { ...row, texts: ["win"], note: "Base note." });
  assert.equal(rowFor(row, "linux"), row);
  assert.equal(rowFor({ licence: "MIT" }, "darwin").licence, "MIT");
  assert.equal(rowFor(undefined, "darwin"), undefined);
});

// -- loadTexts --------------------------------------------------------------------------------

test("loadTexts reads every shared text once its SHA-256 checks out", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "licence-texts-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "good.txt"), "hello\n");
  const sha256 = createHash("sha256").update("hello\n").digest("hex");
  const table = { texts: { good: { file: "good.txt", sha256, source: "https://e" } } };
  assert.deepEqual([...loadTexts(table, dir)], [["good", "hello\n"]]);
});

test("loadTexts throws one error listing every SHA-256 mismatch", (t) => {
  const dir = mkdtempSync(join(tmpdir(), "licence-texts-bad-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  writeFileSync(join(dir, "a.txt"), "aaa");
  writeFileSync(join(dir, "b.txt"), "bbb");
  const table = {
    texts: {
      a: { file: "a.txt", sha256: "deadbeef", source: "https://example.com/a" },
      b: { file: "b.txt", sha256: "deadbeef", source: "https://example.com/b" },
    },
  };
  assert.throws(
    () => loadTexts(table, dir),
    (error) => {
      assert.match(error.message, /a \(a\.txt\)/);
      assert.match(error.message, /b \(b\.txt\)/);
      return true;
    },
  );
});

// -- reviewProblems ---------------------------------------------------------------------------

test("reviewProblems flags a licence that is not allowed (the licence-rule test: fails if isAllowed returns true)", () => {
  const table = { allowed: ["MIT"], texts: {}, components: {} };
  const components = [
    {
      key: "npm:foo@1.0.0",
      name: "foo",
      version: "1.0.0",
      licence: "GPL-3.0-only",
      texts: [{ title: "LICENSE", text: "..." }],
    },
  ];
  assert.deepEqual(reviewProblems(components, table, "linux"), [
    'npm:foo@1.0.0: licence "GPL-3.0-only" is not allowed',
  ]);
});

test("reviewProblems accepts a row's own licence as the review even when it is not in allowed, but flags the component without that row", () => {
  const components = [
    {
      key: "pypi:certifi@2026.7.22",
      name: "certifi",
      version: "2026.7.22",
      licence: "MPL-2.0",
      texts: [{ title: "MPL Exhibit A", text: "..." }],
    },
  ];
  const permissiveAllowed = ["MIT"]; // MPL-2.0 is deliberately absent, as in the real table

  assert.deepEqual(
    reviewProblems(
      components,
      {
        allowed: permissiveAllowed,
        texts: {},
        components: { "pypi:certifi@2026.7.22": { licence: "MPL-2.0" } },
      },
      "linux",
    ),
    [],
    "a row's own licence is the review and is accepted as given, not checked against allowed",
  );

  assert.deepEqual(
    reviewProblems(components, { allowed: permissiveAllowed, texts: {}, components: {} }, "linux"),
    ['pypi:certifi@2026.7.22: licence "MPL-2.0" is not allowed'],
    "with no row, the component's own licence still needs to be in allowed",
  );
});

test("reviewProblems accepts a free-text row licence as the review, never SPDX-parsed", () => {
  const components = [
    {
      key: "pypi:nuitka@4.2.2",
      name: "nuitka",
      version: "4.2.2",
      licence: null,
      texts: [{ title: "LICENSE-RUNTIME.txt", text: "..." }],
      requiresRow: true,
    },
  ];
  const table = {
    allowed: ["MIT"],
    texts: {},
    components: {
      "pypi:nuitka@4.2.2": { licence: "AGPL-3.0 with the Nuitka Runtime Library Exception 1.0" },
    },
  };
  assert.deepEqual(reviewProblems(components, table, "linux"), []);
});

test("reviewProblems flags a present row licence that is not a non-empty string", () => {
  const components = [
    {
      key: "npm:odd@1.0.0",
      name: "odd",
      version: "1.0.0",
      licence: "MIT",
      texts: [{ title: "LICENSE", text: "x" }],
    },
  ];
  for (const badLicence of ["", null, 0]) {
    assert.deepEqual(
      reviewProblems(
        components,
        { allowed: ["MIT"], texts: {}, components: { "npm:odd@1.0.0": { licence: badLicence } } },
        "linux",
      ),
      ["npm:odd@1.0.0: no licence recorded"],
      JSON.stringify(badLicence),
    );
  }
});

test("reviewProblems flags a component with no licence text anywhere, but accepts a row's mit field instead", () => {
  const components = [
    { key: "npm:bar@2.0.0", name: "bar", version: "2.0.0", licence: "MIT", texts: [] },
  ];
  assert.deepEqual(
    reviewProblems(components, { allowed: ["MIT"], texts: {}, components: {} }, "linux"),
    ["npm:bar@2.0.0: no licence text"],
  );
  assert.deepEqual(
    reviewProblems(
      components,
      {
        allowed: ["MIT"],
        texts: {},
        components: { "npm:bar@2.0.0": { mit: "Copyright (c) 2020 Bar" } },
      },
      "linux",
    ),
    [],
  );
});

test("reviewProblems flags a row that names an unknown shared text id", () => {
  const table = {
    allowed: [], // deliberately empty: the row's own licence (below) is the review, not allowed
    texts: { "mpl-2.0": { file: "x", sha256: "y", source: "z" } },
    components: {
      "pypi:certifi@2026.7.22": { licence: "MPL-2.0", texts: ["not-a-real-id"] },
    },
  };
  const components = [
    {
      key: "pypi:certifi@2026.7.22",
      name: "certifi",
      version: "2026.7.22",
      licence: "MPL-2.0",
      texts: [],
    },
  ];
  assert.deepEqual(reviewProblems(components, table, "linux"), [
    'pypi:certifi@2026.7.22: row names unknown text "not-a-real-id"',
  ]);
});

test("reviewProblems flags a requiresRow component (CPython, Nuitka) with no row at all", () => {
  const table = { allowed: ["PSF-2.0"], texts: {}, components: {} };
  const components = [
    {
      key: "python:cpython@3.14.7",
      name: "cpython",
      version: "3.14.7",
      licence: "PSF-2.0",
      texts: [{ title: "LICENSE.txt", text: "..." }],
      requiresRow: true,
    },
  ];
  assert.deepEqual(reviewProblems(components, table, "win32"), [
    "python:cpython@3.14.7: requires a reviewed row",
  ]);
});

test("reviewProblems lets an extra: row's non-empty note satisfy both the licence and text rules", () => {
  const components = [
    {
      key: "extra:messaging-logos",
      name: "messaging-logos",
      version: "",
      licence: null,
      texts: [],
    },
  ];
  const table = {
    allowed: ["MIT"],
    texts: {},
    components: {
      "extra:messaging-logos": { note: "Trademarked logos, not open-source code." },
    },
  };
  assert.deepEqual(reviewProblems(components, table, "linux"), []);
});

test("reviewProblems flags an extra: row with neither licence nor note for both the licence and text rules", () => {
  const components = [
    { key: "extra:mystery@1.0.0", name: "mystery", version: "1.0.0", licence: null, texts: [] },
  ];
  const table = { allowed: ["MIT"], texts: {}, components: { "extra:mystery@1.0.0": {} } };
  assert.deepEqual(reviewProblems(components, table, "linux"), [
    "extra:mystery@1.0.0: no licence recorded",
    "extra:mystery@1.0.0: no licence text",
  ]);
});

test("reviewProblems keeps the strict text rule for a pypi: row with only a note (the note exemption is extra: only)", () => {
  const components = [
    { key: "pypi:example@1.0.0", name: "example", version: "1.0.0", licence: "MIT", texts: [] },
  ];
  const table = {
    allowed: ["MIT"],
    texts: {},
    components: {
      "pypi:example@1.0.0": { note: "Reviewed, but no licence text shipped." },
    },
  };
  assert.deepEqual(reviewProblems(components, table, "linux"), [
    "pypi:example@1.0.0: no licence text",
  ]);
});

test("reviewProblems counts a row's non-empty repo_texts as licence text, for any component prefix", () => {
  const components = [
    { key: "npm:foo@1.0.0", name: "foo", version: "1.0.0", licence: "MIT", texts: [] },
  ];
  const table = {
    allowed: ["MIT"],
    texts: {},
    components: {
      "npm:foo@1.0.0": { licence: "MIT", repo_texts: ["frontend/public/licenses/foo.txt"] },
    },
  };
  assert.deepEqual(reviewProblems(components, table, "linux"), []);
});

test("reviewProblems flags a stale row once its ecosystem is represented, ignores extra: rows and other ecosystems", () => {
  const table = {
    allowed: ["MIT"],
    texts: {},
    components: {
      "npm:gone@1.0.0": {},
      "npm:here@1.0.0": {},
      "extra:electron@44.7.0": { mit: "Copyright (c) 2026" },
    },
  };
  const components = [
    {
      key: "npm:here@1.0.0",
      name: "here",
      version: "1.0.0",
      licence: "MIT",
      texts: [{ title: "LICENSE", text: "x" }],
    },
  ];
  assert.deepEqual(reviewProblems(components, table, "linux"), [
    "npm:gone@1.0.0: stale row, no component reports it",
  ]);
});

test("reviewProblems never flags a stale row for an ecosystem absent from the given components", () => {
  const table = { allowed: ["MIT"], texts: {}, components: { "pypi:gone@1.0.0": {} } };
  const components = [
    {
      key: "npm:here@1.0.0",
      name: "here",
      version: "1.0.0",
      licence: "MIT",
      texts: [{ title: "x", text: "y" }],
    },
  ];
  assert.deepEqual(reviewProblems(components, table, "linux"), []);
});

test("reviewProblems ignores a row whose platforms exclude the build platform, treating the component as if no row existed", () => {
  const table = {
    allowed: ["MIT"],
    texts: {},
    components: {
      "pypi:pywin32@312": {
        licence: "PSF-2.0",
        mit: "Copyright (c) Example",
        platforms: ["win32"],
      },
    },
  };
  const components = [
    { key: "pypi:pywin32@312", name: "pywin32", version: "312", licence: null, texts: [] },
  ];
  assert.deepEqual(reviewProblems(components, table, "linux"), [
    "pypi:pywin32@312: no licence recorded",
    "pypi:pywin32@312: no licence text",
  ]);
});

test("reviewProblems never flags a stale row whose platforms exclude the current one", () => {
  const table = {
    allowed: ["PSF-2.0"],
    texts: {},
    components: { "pypi:pywin32@312": { platforms: ["win32"] } },
  };
  const components = [
    {
      key: "pypi:somethingelse@1.0.0",
      name: "somethingelse",
      version: "1.0.0",
      licence: "PSF-2.0",
      texts: [{ title: "x", text: "y" }],
    },
  ];
  assert.deepEqual(reviewProblems(components, table, "linux"), []);
});

/** A CPython component as `notices.mjs`'s `buildCpythonEntry` reports it. */
function cpythonComponent(version) {
  return {
    key: `python:cpython@${version}`,
    name: "cpython",
    version,
    licence: "PSF-2.0",
    texts: [{ title: "CPython LICENSE.txt", text: "CPython licence" }],
    requiresRow: true,
  };
}

test("reviewProblems checks the text ids of the build platform's by_platform entry only", () => {
  const table = {
    allowed: ["PSF-2.0"],
    texts: { known: { file: "k.txt", sha256: "x", source: "https://e" } },
    components: {
      "python:cpython@3.14.7": {
        platforms: ["win32", "darwin"],
        by_platform: { win32: { texts: ["known"] }, darwin: { texts: ["known", "missing"] } },
      },
    },
  };
  const components = [cpythonComponent("3.14.7")];
  assert.deepEqual(reviewProblems(components, table, "win32"), []);
  assert.deepEqual(reviewProblems(components, table, "darwin"), [
    'python:cpython@3.14.7: row names unknown text "missing"',
  ]);
});

test("the real table reviews CPython 3.14.7 on darwin and win32 and 3.14.4 on linux, with no problems", () => {
  const table = loadTable(join(DESKTOP_ROOT, "build", "licences.json"));
  assert.deepEqual(reviewProblems([cpythonComponent("3.14.7")], table, "darwin"), []);
  assert.deepEqual(reviewProblems([cpythonComponent("3.14.7")], table, "win32"), []);
  assert.deepEqual(reviewProblems([cpythonComponent("3.14.4")], table, "linux"), []);
  // The release dry run's macOS failure: a 3.14.4 build has no darwin row.
  assert.deepEqual(reviewProblems([cpythonComponent("3.14.4")], table, "darwin"), [
    "python:cpython@3.14.4: requires a reviewed row",
    "python:cpython@3.14.7: stale row, no component reports it",
  ]);
});
