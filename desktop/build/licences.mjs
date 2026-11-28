/**
 * @module licences
 * Helpers for reviewing VIKI Agent's third-party licences (ADR-055): reading an npm lockfile's
 * production package set, finding a package's own bundled licence files on disk, parsing an
 * SPDX licence expression, synthesising a generic MIT notice for a package that ships no
 * licence file of its own, and loading and validating the reviewed licence table
 * (`desktop/build/licences.json`) and its shared licence texts. `reviewProblems` is
 * the fail-closed gate: it compares what a build actually reports against that table and names
 * every gap, so `dist.mjs` can refuse before electron-builder runs. Used by `notices.mjs`
 * (composing `THIRD-PARTY-NOTICES.txt`) and by the table's own test.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

/** Every `package-lock.json` v3 key but the root (`""`) starts with one of these. */
const NODE_MODULES = "node_modules/";

/** SPDX licence-expression connectives `spdxIds` drops; everything else must be a licence or
 * exception id. */
const SPDX_OPERATORS = new Set(["AND", "OR", "WITH"]);

/** A licence file name `licenceFiles` matches: "licence"/"license", "copying" or "notice", any
 * case, with any suffix (`.md`, `.txt`, `-MIT`, …). */
const LICENCE_FILE_RE = /^(licen[cs]e|copying|notice)/i;

/** The standard MIT permission-and-disclaimer paragraph, copied verbatim from
 * `frontend/public/licenses/libraries-dev.txt` (lines 9-25): that file is itself MIT-licensed,
 * so its body is a byte-for-byte reference copy here rather than a re-typed one. */
const MIT_BODY = `Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.`;

// -- prodPackages ---------------------------------------------------------------------------

/**
 * The packages an npm v3 lockfile actually ships in production: every `packages` entry but the
 * root (`""`), minus anything the lockfile itself marks `dev`, `devOptional` or `link` (a
 * workspace symlink, never a shipped copy). A package installed at more than one path — for
 * example `semver@7.7.4`, both a production dependency's own copy and a dev dependency's — is
 * reported for its production path(s) only; it is dropped only when every path to it is
 * non-production.
 * @param {{packages: Record<string, {version: string, license?: string, dev?: boolean,
 *   devOptional?: boolean, link?: boolean}>}} lock a parsed `package-lock.json` (lockfileVersion 3)
 * @returns {{path: string, name: string, version: string, license?: string}[]} sorted by
 *   `name@version`; `name` keeps any npm scope (the text after the last `node_modules/`)
 */
export function prodPackages(lock) {
  const rows = [];
  for (const [path, pkg] of Object.entries(lock.packages ?? {})) {
    if (path === "" || pkg.dev || pkg.devOptional || pkg.link) continue;
    const at = path.lastIndexOf(NODE_MODULES);
    const name = at === -1 ? path : path.slice(at + NODE_MODULES.length);
    rows.push({ path, name, version: pkg.version, license: pkg.license });
  }
  return rows.sort((a, b) => {
    const ak = `${a.name}@${a.version}`;
    const bk = `${b.name}@${b.version}`;
    return ak < bk ? -1 : ak > bk ? 1 : 0;
  });
}

// -- licenceFiles -----------------------------------------------------------------------------

/**
 * The likely licence files directly inside `dir` (an npm package's own folder): regular files
 * (not subfolders) named "licence"/"license", "copying" or "notice", any case and any suffix.
 * @param {string} dir
 * @returns {string[]} matching file names, sorted; empty when none exist
 */
export function licenceFiles(dir) {
  return readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && LICENCE_FILE_RE.test(entry.name))
    .map((entry) => entry.name)
    .sort();
}

// -- spdxIds / isAllowed ----------------------------------------------------------------------

/**
 * The licence and exception ids named in an SPDX licence expression, dropping the `AND`/`OR`/
 * `WITH` connectives and any parentheses. An exception named after `WITH` (for example
 * `GCC-exception-3.1`) is returned alongside the licence id it qualifies, not paired with it —
 * callers that care about the pairing see both ids and can tell which is which from context.
 * @param {string} expr
 * @returns {string[]} in the expression's own order; never empty
 * @throws {Error} when `expr` names no id at all, or any token is not a bare SPDX-style id
 *   (`[A-Za-z0-9.+-]+`) — for example stray punctuation, or a connective this function does not
 *   know
 */
export function spdxIds(expr) {
  const tokens = String(expr ?? "")
    .replace(/[()]/g, " ")
    .trim()
    .split(/\s+/)
    .filter((token) => token.length > 0);
  const ids = [];
  for (const token of tokens) {
    if (SPDX_OPERATORS.has(token)) continue;
    if (!/^[A-Za-z0-9.+-]+$/.test(token)) {
      throw new Error(`spdxIds: "${expr}" has a token that is not a licence id: "${token}"`);
    }
    ids.push(token);
  }
  if (ids.length === 0) {
    throw new Error(`spdxIds: "${expr}" names no licence id`);
  }
  return ids;
}

/**
 * Whether every licence and exception id `expr` names is in `allowed` (the table's
 * `allowed` list). Propagates `spdxIds`'s error for an expression that does not parse at all —
 * to `reviewProblems`, that is the same gap as a disallowed id, not a different kind of problem.
 * @param {string} expr an SPDX licence expression
 * @param {Iterable<string>} allowed
 * @returns {boolean}
 */
export function isAllowed(expr, allowed) {
  const allowedSet = allowed instanceof Set ? allowed : new Set(allowed);
  return spdxIds(expr).every((id) => allowedSet.has(id));
}

// -- mitText ----------------------------------------------------------------------------------

/**
 * A generic MIT notice for a component that ships no licence file of its own: the table's `mit`
 * field supplies just the copyright line, and this fills in the standard rest.
 * @param {string} copyright a single copyright line, for example "Copyright (c) 2015 Loopline
 *   Systems"
 * @returns {string} "MIT License", blank, `copyright`, blank, the standard MIT body
 */
export function mitText(copyright) {
  return ["MIT License", "", copyright, "", MIT_BODY].join("\n");
}

// -- loadTable --------------------------------------------------------------------------------

/**
 * Whether a row's optional `by_platform` is well formed: an object with exactly one entry per
 * platform in the row's own `platforms` (so a misspelt or missing platform cannot quietly fall
 * back to the row's own texts), each entry an object holding only `texts` (an array of strings)
 * and/or `note` (a string).
 * @param {{platforms?: unknown, by_platform?: unknown}} row
 * @returns {boolean}
 */
function byPlatformOk(row) {
  const map = row.by_platform;
  if (!map || typeof map !== "object" || Array.isArray(map) || !Array.isArray(row.platforms)) {
    return false;
  }
  const keys = Object.keys(map);
  if (keys.length !== row.platforms.length || !row.platforms.every((p) => keys.includes(p))) {
    return false;
  }
  return Object.values(map).every(
    (entry) =>
      entry &&
      typeof entry === "object" &&
      !Array.isArray(entry) &&
      Object.keys(entry).every((field) => field === "texts" || field === "note") &&
      (entry.texts === undefined ||
        (Array.isArray(entry.texts) && entry.texts.every((id) => typeof id === "string"))) &&
      (entry.note === undefined || typeof entry.note === "string"),
  );
}

/**
 * Reads and shape-checks `desktop/build/licences.json`: `allowed` an array, `texts`
 * an object of `{file, sha256, source}` (each a string), `components` an object of objects, and
 * any row's `by_platform` as `byPlatformOk` describes. Does not check `components`' other
 * optional fields (`licence`, `texts`, `mit`, …) — those gaps are `reviewProblems`'s job,
 * reported one by one rather than refusing to load at all.
 * @param {string} file absolute path to the table JSON
 * @returns {{allowed: string[], texts: Record<string, {file: string, sha256: string, source:
 *   string}>, components: Record<string, object>}}
 * @throws {Error} naming the first key whose shape is wrong
 */
export function loadTable(file) {
  const table = JSON.parse(readFileSync(file, "utf8"));
  if (!table || typeof table !== "object" || Array.isArray(table)) {
    throw new Error(`loadTable: ${file}: not an object`);
  }
  if (!Array.isArray(table.allowed)) {
    throw new Error(`loadTable: ${file}: "allowed" must be an array`);
  }
  if (typeof table.texts !== "object" || table.texts === null || Array.isArray(table.texts)) {
    throw new Error(`loadTable: ${file}: "texts" must be an object`);
  }
  for (const [id, entry] of Object.entries(table.texts)) {
    const ok =
      entry &&
      typeof entry === "object" &&
      typeof entry.file === "string" &&
      typeof entry.sha256 === "string" &&
      typeof entry.source === "string";
    if (!ok) {
      throw new Error(`loadTable: ${file}: "texts.${id}" must have file, sha256 and source`);
    }
  }
  if (
    typeof table.components !== "object" ||
    table.components === null ||
    Array.isArray(table.components)
  ) {
    throw new Error(`loadTable: ${file}: "components" must be an object`);
  }
  for (const [key, row] of Object.entries(table.components)) {
    if (!row || typeof row !== "object" || Array.isArray(row)) {
      throw new Error(`loadTable: ${file}: "components.${key}" must be an object`);
    }
    if (row.by_platform !== undefined && !byPlatformOk(row)) {
      throw new Error(
        `loadTable: ${file}: "components.${key}.by_platform" must give each of the row's ` +
          "platforms only its texts and note",
      );
    }
  }
  return table;
}

// -- rowFor -----------------------------------------------------------------------------------

/**
 * A table row as it applies on `platform`: when the row's `by_platform` has an entry for that
 * platform, that entry's `texts` and `note` (each when set) replace the row's own; every other
 * field, `platforms` included, is the row's own. Whether the row applies on `platform` at all
 * is still its `platforms`' call, made by each caller.
 * @template {object|undefined} T
 * @param {T} row a `table.components` row, or `undefined` for a component with none
 * @param {string} platform a `process.platform` value
 * @returns {T} `row` itself when it has no entry for `platform`
 */
export function rowFor(row, platform) {
  const entry = row?.by_platform?.[platform];
  if (!entry) return row;
  const { texts = row.texts, note = row.note } = entry;
  return { ...row, texts, note };
}

// -- loadTexts --------------------------------------------------------------------------------

/**
 * Reads every shared licence text `table.texts` names from `buildDir` (the folder
 * each entry's `file` is relative to — `desktop/build/` in production) and checks each one's
 * SHA-256 against the table's recorded hash, so a hand-edited or re-fetched text is caught
 * before it ships under the wrong licence's name.
 * @param {{texts: Record<string, {file: string, sha256: string, source: string}>}} table
 *   typically `loadTable`'s return value
 * @param {string} buildDir
 * @returns {Map<string, string>} text id to its UTF-8 contents
 * @throws {Error} one error listing every id, file and hash that does not match
 */
export function loadTexts(table, buildDir) {
  const texts = new Map();
  const mismatches = [];
  for (const [id, entry] of Object.entries(table.texts ?? {})) {
    const bytes = readFileSync(join(buildDir, entry.file));
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    if (sha256 !== entry.sha256) {
      mismatches.push(`${id} (${entry.file}): expected ${entry.sha256}, got ${sha256}`);
      continue;
    }
    texts.set(id, bytes.toString("utf8"));
  }
  if (mismatches.length > 0) {
    throw new Error(`loadTexts: sha256 mismatch: ${mismatches.join("; ")}`);
  }
  return texts;
}

// -- reviewProblems ---------------------------------------------------------------------------

/**
 * Flags gaps between `table` (`desktop/build/licences.json`) and the licence facts
 * `components` actually reports, for `platform` (a `process.platform`/`sys.platform` value).
 * Below, "a matching row" means a `table.components` row at the component's exact key whose own
 * `platforms` (when it has one) includes `platform`, taken as it applies on `platform`
 * (`rowFor`: its `by_platform` entry's `texts` and `note` replace its own) — a row scoped to
 * another platform does not review the component at all, which then faces exactly the rules
 * below as if no row existed, same as a component whose key simply has no row. Five independent
 * rules, any of which a single component can fail at once:
 *   - a matching row's own non-empty string `licence` IS the review and is accepted
 *     verbatim — free text, never SPDX-parsed, never checked against
 *     `table.allowed` — which is how a curated row ships Nuitka's AGPL-with-exception text or an
 *     MPL-2.0/LGPL component even while `allowed` stays permissive-only; a row that sets
 *     `licence` to anything else (present but not a non-empty string) is itself the problem.
 *     With no row licence at all, the component's own licence must parse as SPDX and every id in
 *     it must be in `table.allowed` instead. Exception: for a component whose key starts
 *     with `extra:` (no package of its own), a matching row's non-empty string `note` clears this
 *     rule outright, with no licence id at all — how electron, microsoft-vc-runtime and
 *     messaging-logos are reviewed without one;
 *   - it must have some licence text: its own `texts`, a matching row's `texts`, non-empty
 *     `repo_texts` or `mit` — or, again only for an `extra:` component, that same row `note`;
 *   - a matching row's `texts` ids must all exist in `table.texts`;
 *   - a component flagged `requiresRow` (CPython, Nuitka) must have a matching row at all;
 *   - (checked once, over the table, not per component) a `pypi:`/`npm:`/`python:` row that
 *     applies to `platform` is stale when no given component has its exact key — but only when
 *     at least one given component shares that row's key prefix, so a call made with only npm
 *     components never flags an absent `pypi:` row as stale.
 * `extra:` rows (components with no package) are never checked for staleness —
 * nothing in `components` could ever match one. The `note` exemption above is also `extra:`-only:
 * a `pypi:`/`npm:`/`python:` row's `note` never substitutes for a licence or licence text.
 * @param {{key: string, name: string, version: string, licence?: string|null,
 *   texts: {title: string, text: string}[], requiresRow?: boolean}[]} components
 * @param {{allowed: string[], texts: Record<string, {file: string, sha256: string, source:
 *   string}>, components: Record<string, {licence?: string, texts?: string[], mit?: string,
 *   note?: string, platforms?: string[], files?: string[], repo_texts?: string[],
 *   by_platform?: Record<string, {texts?: string[], note?: string}>}>}} table
 * @param {string} platform a `process.platform` value
 * @returns {string[]} sorted, human-readable problem descriptions, one per gap; empty when the
 *   table covers everything `components` reports
 */
export function reviewProblems(components, table, platform) {
  const problems = [];
  const allowedSet = new Set(table.allowed ?? []);
  const textIds = new Set(Object.keys(table.texts ?? {}));
  const rows = table.components ?? {};
  const presentPrefixes = new Set(components.map((c) => c.key.slice(0, c.key.indexOf(":") + 1)));

  for (const component of components) {
    const matchedRow = rows[component.key];
    // A row whose `platforms` excludes the build platform does not review this component at
    // all — it falls through to exactly the checks below for a component with no row. (The stale
    // check further down, and `notices.mjs`'s extras and file-glob checks, filter rows by
    // `platforms` on their own.)
    const row =
      matchedRow?.platforms && !matchedRow.platforms.includes(platform)
        ? undefined
        : rowFor(matchedRow, platform);
    // An `extra:` row (no package of its own) can be reviewed through a plain-English
    // `note` instead of a licence id or licence text; every other prefix follows the rules
    // below.
    const reviewedByNote =
      component.key.startsWith("extra:") && typeof row?.note === "string" && row.note !== "";

    if (!reviewedByNote) {
      if (row && row.licence !== undefined) {
        // A row's own licence is the review: accepted as given, free text, with no SPDX parse
        // and no `allowed` check.
        if (typeof row.licence !== "string" || row.licence === "") {
          problems.push(`${component.key}: no licence recorded`);
        }
      } else if (typeof component.licence !== "string") {
        problems.push(`${component.key}: no licence recorded`);
      } else {
        let allowed = false;
        try {
          allowed = isAllowed(component.licence, allowedSet);
        } catch {
          allowed = false;
        }
        if (!allowed) {
          problems.push(`${component.key}: licence "${component.licence}" is not allowed`);
        }
      }
    }

    const hasOwnTexts = component.texts.length > 0;
    const hasRowTexts = Boolean(row?.texts?.length);
    const hasRepoTexts = Boolean(row?.repo_texts?.length);
    const hasMit = row?.mit !== undefined;
    if (!hasOwnTexts && !hasRowTexts && !hasRepoTexts && !hasMit && !reviewedByNote) {
      problems.push(`${component.key}: no licence text`);
    }

    if (row?.texts) {
      for (const id of row.texts) {
        if (!textIds.has(id)) {
          problems.push(`${component.key}: row names unknown text "${id}"`);
        }
      }
    }

    if (component.requiresRow && !row) {
      problems.push(`${component.key}: requires a reviewed row`);
    }
  }

  for (const [key, row] of Object.entries(rows)) {
    const prefix = key.slice(0, key.indexOf(":") + 1);
    if (prefix === "extra:" || !presentPrefixes.has(prefix)) continue;
    if (row.platforms && !row.platforms.includes(platform)) continue;
    if (!components.some((c) => c.key === key)) {
      problems.push(`${key}: stale row, no component reports it`);
    }
  }

  return problems.sort();
}
