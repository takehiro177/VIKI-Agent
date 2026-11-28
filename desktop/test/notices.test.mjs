import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { loadTable, loadTexts, mitText } from "../build/licences.mjs";
import { buildNotices, SECTION_TITLES } from "../build/notices.mjs";

const DESKTOP_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const REPO_ROOT = dirname(DESKTOP_ROOT);
const BUILD_DIR = join(DESKTOP_ROOT, "build");

// -- fixture helpers ------------------------------------------------------------------------

function makeRepo(t) {
  const dir = mkdtempSync(join(tmpdir(), "notices-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

function writeLockfile(repo, tree, packages) {
  mkdirSync(join(repo, tree), { recursive: true });
  writeFileSync(join(repo, tree, "package-lock.json"), JSON.stringify({ packages }));
}

/** A minimal, valid lockfile (root entry only, no production packages) for both trees
 * `buildNotices` always reads, so a test that does not care about npm packages still gets a
 * working repo. */
function writeEmptyLockfiles(repo) {
  writeLockfile(repo, "frontend", { "": {} });
  writeLockfile(repo, "desktop", { "": {} });
}

function writePackageFile(repo, tree, pkgPath, fileName, content) {
  const dir = join(repo, tree, pkgPath);
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, fileName), content);
}

/** A fully clean, minimal `buildNotices` input: empty server distributions, no npm production
 * packages, nothing unattributed, and reviewed rows for the two always-present components
 * (CPython, Nuitka) — so a test that cares about one extra gap starts from zero problems. */
function baseFixture(repo) {
  writeEmptyLockfiles(repo);
  return {
    repo,
    inventory: {
      schema: 1,
      platform: "linux",
      python: { version: "3.14.7", licence: "PSF licence body text" },
      nuitka: {
        version: "4.2.2",
        files: { "LICENSE-RUNTIME.txt": "Nuitka runtime exception text" },
      },
      distributions: [],
      unattributed: [],
    },
    table: {
      allowed: ["MIT", "ISC"],
      texts: {},
      components: {
        "python:cpython@3.14.7": { licence: "PSF-2.0" },
        "pypi:nuitka@4.2.2": { licence: "AGPL-3.0 with the Nuitka Runtime Library Exception 1.0" },
      },
    },
    texts: new Map(),
    platform: "linux",
    version: "1.0.0",
    homepage: "https://example.test",
  };
}

// -- SECTION_TITLES ---------------------------------------------------------------------------

test("SECTION_TITLES lists the sections in the notice file's order", () => {
  assert.deepEqual(SECTION_TITLES, [
    "Python packages in the local server",
    "CPython",
    "Nuitka runtime",
    "npm packages in the user interface",
    "npm packages in the desktop window",
    "Other components",
    "Licence texts",
  ]);
});

// -- buildNotices: the golden fixture ----------------------------------------------------------

test("buildNotices composes the exact golden text for a small fixture, with zero problems", (t) => {
  const repo = makeRepo(t);
  writeLockfile(repo, "frontend", {
    "": {},
    "node_modules/alpha": { version: "1.0.0", license: "MIT" },
  });
  writePackageFile(repo, "frontend", "node_modules/alpha", "LICENSE", "Alpha MIT licence text");
  writePackageFile(
    repo,
    "frontend",
    "node_modules/alpha",
    "package.json",
    JSON.stringify({ version: "1.0.0" }),
  );
  writeLockfile(repo, "desktop", {
    "": {},
    "node_modules/beta": { version: "2.0.0", license: "ISC" },
  });
  writePackageFile(repo, "desktop", "node_modules/beta", "LICENSE", "Beta ISC licence text");
  writePackageFile(
    repo,
    "desktop",
    "node_modules/beta",
    "package.json",
    JSON.stringify({ version: "2.0.0" }),
  );

  const fixture = {
    repo,
    inventory: {
      schema: 1,
      platform: "linux",
      python: { version: "3.14.7", licence: "PSF licence body text" },
      nuitka: {
        version: "4.2.2",
        files: { "LICENSE-RUNTIME.txt": "Nuitka runtime exception text" },
      },
      distributions: [
        {
          key: "pypi:certifi@2026.7.22",
          name: "certifi",
          version: "2026.7.22",
          license_expression: null,
          license: "MPL-2.0",
          classifiers: [],
          files: {},
          embedded: [
            {
              file: "certifi-2026.7.22.dist-info/sboms/certifi.cdx.json",
              components: [{ name: "libexample", version: "1.2.3", licences: ["BSD-3-Clause"] }],
            },
          ],
        },
      ],
      unattributed: ["libfoo.so.1"],
    },
    table: {
      allowed: ["MIT", "ISC"],
      texts: {
        "mpl-2.0": { file: "mpl-2.0.txt", sha256: "deadbeef", source: "https://mozilla.example" },
      },
      components: {
        "pypi:certifi@2026.7.22": { licence: "MPL-2.0", texts: ["mpl-2.0"] },
        "python:cpython@3.14.7": { licence: "PSF-2.0" },
        "pypi:nuitka@4.2.2": { licence: "AGPL-3.0 with the Nuitka Runtime Library Exception 1.0" },
        "extra:widget@9.9.9": {
          licence: "MIT",
          mit: "Copyright (c) 2026 Widget Inc.",
          note: "Ships only the x64 build.",
          files: ["libfoo.so.*"],
        },
      },
    },
    texts: new Map([["mpl-2.0", "Shared MPL body text"]]),
    platform: "linux",
    version: "1.0.0",
    homepage: "https://example.test",
  };

  const { text, problems } = buildNotices(fixture);
  assert.deepEqual(problems, []);

  const expected = `${[
    "VIKI Agent 1.0.0 - Third-party notices (linux)",
    "",
    "This file lists the licences of third-party components bundled with this installer.",
    "Electron's own third-party notices are listed separately, next to the application",
    "executable, in LICENSE.electron.txt and LICENSES.chromium.html (in Contents/Resources",
    "on macOS).",
    "",
    "Source for any component that requires it is available on request at https://example.test.",
    "",
    "Python packages in the local server",
    "=".repeat("Python packages in the local server".length),
    "",
    "==== certifi 2026.7.22 - MPL-2.0",
    "Bundles: libexample 1.2.3 (BSD-3-Clause)",
    "Licence text: mpl-2.0",
    "",
    "CPython",
    "=".repeat("CPython".length),
    "",
    "==== cpython 3.14.7 - PSF-2.0",
    "Licence text: CPython LICENSE.txt",
    "",
    "Nuitka runtime",
    "=".repeat("Nuitka runtime".length),
    "",
    "==== nuitka 4.2.2 - AGPL-3.0 with the Nuitka Runtime Library Exception 1.0",
    "Licence text: LICENSE-RUNTIME.txt",
    "",
    "npm packages in the user interface",
    "=".repeat("npm packages in the user interface".length),
    "",
    "==== alpha 1.0.0 - MIT",
    "Licence text: frontend/node_modules/alpha/LICENSE",
    "",
    "npm packages in the desktop window",
    "=".repeat("npm packages in the desktop window".length),
    "",
    "==== beta 2.0.0 - ISC",
    "Licence text: desktop/node_modules/beta/LICENSE",
    "",
    "Other components",
    "=".repeat("Other components".length),
    "",
    "==== widget 9.9.9 - MIT",
    "Ships only the x64 build.",
    "Licence text: widget 9.9.9 (MIT)",
    "",
    "Licence texts",
    "=".repeat("Licence texts".length),
    "",
    "---- mpl-2.0 ----",
    "Shared MPL body text",
    "",
    "---- CPython LICENSE.txt ----",
    "PSF licence body text",
    "",
    "---- LICENSE-RUNTIME.txt ----",
    "Nuitka runtime exception text",
    "",
    "---- frontend/node_modules/alpha/LICENSE ----",
    "Alpha MIT licence text",
    "",
    "---- desktop/node_modules/beta/LICENSE ----",
    "Beta ISC licence text",
    "",
    "---- widget 9.9.9 (MIT) ----",
    mitText("Copyright (c) 2026 Widget Inc."),
  ].join("\n")}\n`;

  assert.equal(text, expected);
});

// -- buildNotices: free-text licences ------------------------------------------------------------

test("buildNotices prints a row's free-text licence verbatim, never SPDX-parsed", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  const { text, problems } = buildNotices(fixture);
  assert.deepEqual(problems, []);
  assert.match(
    text,
    /^==== nuitka 4\.2\.2 - AGPL-3\.0 with the Nuitka Runtime Library Exception 1\.0$/m,
  );
});

// -- buildNotices: unattributed native files ------------------------------------------------------

test("buildNotices flags an unattributed native file with no covering row, and clears it once a row's files glob covers it", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  fixture.inventory.unattributed = ["lib1.so.2"];

  assert.deepEqual(buildNotices(fixture).problems, [
    "unattributed: lib1.so.2 matched by no reviewed row",
  ]);

  fixture.table.components["extra:lib1@1.0.0"] = {
    licence: "MIT",
    mit: "Copyright (c) 2026 Lib1",
    files: ["lib1.so.*"],
  };
  assert.deepEqual(buildNotices(fixture).problems, []);
});

// -- buildNotices: missing node_modules folder ----------------------------------------------------

test("buildNotices reports a missing node_modules package folder as a problem naming npm ci in that tree", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  // Overwrite frontend's lockfile to list a package whose node_modules folder is never created.
  writeLockfile(repo, "frontend", {
    "": {},
    "node_modules/gamma": { version: "1.0.0", license: "MIT" },
  });
  fixture.table.components["npm:gamma@1.0.0"] = { licence: "MIT", mit: "Copyright (c) 2026 Gamma" };

  const { problems } = buildNotices(fixture);
  assert.deepEqual(problems, [
    "npm:gamma@1.0.0: no node_modules folder under frontend, run npm ci",
  ]);
});

// -- buildNotices: shared-text dedup -------------------------------------------------------------

test("buildNotices renders a shared licence text once under Licence texts, not once per component (dedup test: fails if a shared text renders inline)", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  fixture.table.texts.shared = { file: "shared.txt", sha256: "x", source: "https://example.com" };
  fixture.texts = new Map([["shared", "SHARED BODY TEXT"]]);
  fixture.table.components["extra:one@1.0.0"] = { licence: "MIT", texts: ["shared"] };
  fixture.table.components["extra:two@1.0.0"] = { licence: "MIT", texts: ["shared"] };

  const { text, problems } = buildNotices(fixture);
  assert.deepEqual(problems, []);
  assert.equal(
    text.split("SHARED BODY TEXT").length - 1,
    1,
    "the shared text must render once under Licence texts, not once per component",
  );
  assert.match(text, /==== one 1\.0\.0 - MIT\nLicence text: shared/);
  assert.match(text, /==== two 1\.0\.0 - MIT\nLicence text: shared/);
});

// -- buildNotices: a note-only extra component has no licence ------------------------------

test("buildNotices renders a note-only extra component as ==== <name> with no licence suffix, never UNKNOWN", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  fixture.table.components["extra:messaging-logos"] = {
    note: "Trademarked logos, not open-source code.",
  };

  const { text, problems } = buildNotices(fixture);
  assert.deepEqual(problems, []);
  assert.match(text, /^==== messaging-logos$/m);
  assert.ok(!text.includes("UNKNOWN"), "a licence-less entry must never print UNKNOWN");
});

// -- buildNotices: an embedded SBOM the inventory could not summarise ---------------------------

test("buildNotices reports an embedded SBOM the inventory recorded as components: null", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  fixture.inventory.distributions = [
    {
      key: "pypi:mystery@1.0.0",
      name: "mystery",
      version: "1.0.0",
      license_expression: null,
      license: "MIT",
      classifiers: [],
      files: {},
      embedded: [{ file: "mystery-1.0.0.dist-info/sboms/mystery.cdx.json", components: null }],
    },
  ];
  fixture.table.components["pypi:mystery@1.0.0"] = {
    licence: "MIT",
    mit: "Copyright (c) 2026 Mystery Inc.",
  };

  assert.deepEqual(buildNotices(fixture).problems, [
    "pypi:mystery@1.0.0: embedded SBOM mystery-1.0.0.dist-info/sboms/mystery.cdx.json unreadable",
  ]);
});

// -- buildNotices: an installed npm package's package.json disagrees with the lock --------------

test("buildNotices reports an installed npm package whose package.json version differs from the lock, naming npm ci", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  writeLockfile(repo, "frontend", {
    "": {},
    "node_modules/gamma": { version: "1.0.0", license: "MIT" },
  });
  fixture.table.components["npm:gamma@1.0.0"] = {
    licence: "MIT",
    mit: "Copyright (c) 2026 Gamma",
  };
  writePackageFile(
    repo,
    "frontend",
    "node_modules/gamma",
    "package.json",
    JSON.stringify({ version: "1.0.1" }),
  );

  assert.deepEqual(buildNotices(fixture).problems, [
    "npm:gamma@1.0.0: installed 1.0.1, run npm ci",
  ]);
});

test("buildNotices reports an unreadable or version-less installed package.json the same way", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  writeLockfile(repo, "frontend", {
    "": {},
    "node_modules/gamma": { version: "1.0.0", license: "MIT" },
  });
  fixture.table.components["npm:gamma@1.0.0"] = {
    licence: "MIT",
    mit: "Copyright (c) 2026 Gamma",
  };

  for (const packageJson of ["{}", "not json"]) {
    writePackageFile(repo, "frontend", "node_modules/gamma", "package.json", packageJson);
    assert.deepEqual(
      buildNotices(fixture).problems,
      ["npm:gamma@1.0.0: installed package.json under frontend is unreadable, run npm ci"],
      packageJson,
    );
  }
});

// -- buildNotices: the inventory's platform must match the build's -------------------------------

test("buildNotices reports a problem when the inventory's platform differs from the build platform", (t) => {
  const repo = makeRepo(t);
  const fixture = baseFixture(repo);
  fixture.inventory.platform = "darwin";

  assert.deepEqual(buildNotices(fixture).problems, [
    "inventory platform darwin differs from build platform linux",
  ]);
});

// -- buildNotices: a row's per-platform texts and note ----------------------------------------

test("buildNotices renders a row's by_platform texts and note on that platform, and the row's own elsewhere", (t) => {
  const rendered = {};
  for (const platform of ["linux", "darwin"]) {
    const fixture = baseFixture(makeRepo(t));
    fixture.platform = platform;
    fixture.inventory.platform = platform;
    fixture.table.texts = {
      a: { file: "a.txt", sha256: "x", source: "https://e" },
      b: { file: "b.txt", sha256: "x", source: "https://e" },
    };
    fixture.texts = new Map([
      ["a", "Text A"],
      ["b", "Text B"],
    ]);
    fixture.table.components["python:cpython@3.14.7"] = {
      licence: "PSF-2.0",
      platforms: ["linux", "darwin"],
      texts: ["a"],
      note: "Linux build.",
      by_platform: { darwin: { texts: ["b"], note: "macOS build." } },
    };
    const { text, problems } = buildNotices(fixture);
    assert.deepEqual(problems, [], platform);
    rendered[platform] = text;
  }
  const cpython = (note, id) =>
    `==== cpython 3.14.7 - PSF-2.0\n${note}\nLicence text: CPython LICENSE.txt\nLicence text: ${id}\n`;
  assert.ok(rendered.linux.includes(cpython("Linux build.", "a")));
  assert.ok(rendered.linux.includes("---- a ----\nText A\n"));
  assert.ok(!rendered.linux.includes("Text B"));
  assert.ok(rendered.darwin.includes(cpython("macOS build.", "b")));
  assert.ok(rendered.darwin.includes("---- b ----\nText B\n"));
  assert.ok(!rendered.darwin.includes("Text A") && !rendered.darwin.includes("Linux build."));
});

// -- buildNotices over the real table: CPython 3.14.7 on win32 and darwin ---------------------

/** The win32-only `python:cpython@3.14.7` row as it stood before darwin joined it. */
const WIN32_CPYTHON_ROW_BEFORE_DARWIN = {
  platforms: ["win32"],
  texts: ["cpython-3.14.7-incorporated"],
  note: "CPython 3.14.7 (PSF-2.0, uv-managed build 20260807). LICENSE.txt covers PSF history, bzip2, zstd, Tcl/Tk and the Microsoft Distributable Code conditions (redistributors must bind end users to terms protecting it). Doc/license.rst's 'Licenses and Acknowledgements for Incorporated Software' (OpenSSL, expat, libffi, zlib, libmpdec, mimalloc and others) is not installed, so it is supplied above. SQLite (public domain) and xz (0BSD) need no notice. Source: https://raw.githubusercontent.com/python/cpython/v3.14.7/Doc/license.rst",
};

/** `buildNotices` input over the real table and texts, for a CPython 3.14.7 build on `platform`
 * with no server distributions or npm packages; the rows' `repo_texts` are copied in from the
 * real repo. */
function realTableFixture(repo, platform) {
  writeEmptyLockfiles(repo);
  const table = loadTable(join(BUILD_DIR, "licences.json"));
  for (const path of Object.values(table.components).flatMap((row) => row.repo_texts ?? [])) {
    mkdirSync(dirname(join(repo, path)), { recursive: true });
    copyFileSync(join(REPO_ROOT, path), join(repo, path));
  }
  return {
    repo,
    inventory: {
      schema: 1,
      platform,
      python: { version: "3.14.7", licence: "CPython LICENSE.txt body" },
      nuitka: { version: "4.2.2", files: { "LICENSE-RUNTIME.txt": "Nuitka runtime exception" } },
      distributions: [],
      unattributed: [],
    },
    table,
    texts: loadTexts(table, BUILD_DIR),
    platform,
    version: "1.0.0",
    homepage: "https://example.test",
  };
}

test("with the real table, Windows notices are byte-identical to the win32 row's before darwin joined it", (t) => {
  const now = realTableFixture(makeRepo(t), "win32");
  const before = realTableFixture(makeRepo(t), "win32");
  before.table.components["python:cpython@3.14.7"] = WIN32_CPYTHON_ROW_BEFORE_DARWIN;
  const nowNotices = buildNotices(now);
  const beforeNotices = buildNotices(before);
  assert.equal(nowNotices.text, beforeNotices.text);
  assert.deepEqual(nowNotices.problems, beforeNotices.problems);
  assert.ok(
    nowNotices.text.includes(
      `==== cpython 3.14.7 - PSF-2.0\n${WIN32_CPYTHON_ROW_BEFORE_DARWIN.note}\nLicence text: CPython LICENSE.txt\nLicence text: cpython-3.14.7-incorporated\n`,
    ),
  );
  assert.deepEqual(
    nowNotices.problems.filter((problem) => problem.startsWith("python:")),
    [],
  );
});

test("with the real table, darwin notices carry the macOS CPython note and the texts of what libpython statically links", (t) => {
  const fixture = realTableFixture(makeRepo(t), "darwin");
  const { text, problems } = buildNotices(fixture);
  assert.deepEqual(
    problems.filter((problem) => problem.startsWith("python:")),
    [],
  );
  const darwin = fixture.table.components["python:cpython@3.14.7"].by_platform.darwin;
  assert.deepEqual(darwin.texts, [
    "cpython-3.14.7-incorporated",
    "bzip2-1.0.8",
    "libuuid-1.0.3",
    "zstd-1.5.7",
  ]);
  const lines = darwin.texts.map((id) => `Licence text: ${id}`).join("\n");
  assert.ok(
    text.includes(
      `==== cpython 3.14.7 - PSF-2.0\n${darwin.note}\nLicence text: CPython LICENSE.txt\n${lines}\n`,
    ),
  );
  for (const id of darwin.texts) {
    assert.ok(text.includes(`---- ${id} ----\n${fixture.texts.get(id)}`), id);
  }
  // macOS's own libedit and ncurses are linked, not shipped; the Windows note is not macOS's.
  assert.ok(!text.includes(fixture.texts.get("libedit-20240808-3.1")));
  assert.ok(!text.includes(fixture.texts.get("ncurses-6.5")));
  assert.ok(!text.includes(WIN32_CPYTHON_ROW_BEFORE_DARWIN.note));
});
