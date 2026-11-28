/**
 * @module notices
 * Composes `THIRD-PARTY-NOTICES.txt` (ADR-055) from the compiled server's
 * licence inventory (`scripts/licence_inventory.py`'s JSON), both npm lockfiles' production
 * package sets, and the reviewed licence table (`desktop/build/licences.json`, `licences.mjs`).
 * `buildNotices` is pure over its inputs: given the inventory already parsed, the table already
 * loaded (`loadTable`) and its shared texts already loaded (`loadTexts`), it returns the composed
 * text plus the same kind of fail-closed gaps `reviewProblems` reports, together with gaps only
 * notice-composing can see: an unattributed native file no row's `files` glob covers; a missing
 * `node_modules` package folder, or one whose installed `package.json` disagrees with the lock;
 * an embedded SBOM the inventory could not summarise; and an inventory recorded on a different
 * platform than this build is running on. `dist.mjs` refuses before
 * electron-builder runs when `problems` is non-empty; the text itself is still composed
 * best-effort so a caller can inspect it even on a refused build.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  licenceFiles,
  mitText,
  prodPackages,
  reviewProblems,
  rowFor,
  spdxIds,
} from "./licences.mjs";

/** The sections of the notice file, in the order `buildNotices` renders them. The last title
 * is not a component group: every component's licence texts are collected there, once each,
 * deduplicated by content, and referenced by title from the component's own section. */
export const SECTION_TITLES = [
  "Python packages in the local server",
  "CPython",
  "Nuitka runtime",
  "npm packages in the user interface",
  "npm packages in the desktop window",
  "Other components",
  "Licence texts",
];

/** Which lockfile tree feeds which section. */
const NPM_SECTIONS = [
  { tree: "frontend", section: "npm packages in the user interface" },
  { tree: "desktop", section: "npm packages in the desktop window" },
];

// -- component builders ------------------------------------------------------------------------

/**
 * A Python distribution's licence: its PEP 639 `license_expression` when
 * present, else its legacy `license` field when `spdxIds` can parse it at all (a free-text legacy
 * field such as "UNKNOWN" or a prose blurb is not usable as a licence id and becomes `null`
 * instead of throwing), else `null`.
 * @param {{license_expression?: string|null, license?: string|null}} d
 * @returns {string|null}
 */
function distributionLicence(d) {
  if (typeof d.license_expression === "string" && d.license_expression !== "") {
    return d.license_expression;
  }
  if (typeof d.license === "string") {
    try {
      spdxIds(d.license);
      return d.license;
    } catch {
      return null;
    }
  }
  return null;
}

/** `{title, text}` pairs sorted by title, for deterministic output. */
function sortedTexts(list) {
  return [...list].sort((a, b) => (a.title < b.title ? -1 : a.title > b.title ? 1 : 0));
}

/** One "Bundles:" line for a bundled component an embedded CycloneDX SBOM names. */
function bundleLine(component) {
  const licences = component.licences?.length ? ` (${component.licences.join(", ")})` : "";
  return `Bundles: ${component.name} ${component.version}${licences}`;
}

/** One problem per embedded SBOM `scripts/licence_inventory.py`'s `_embedded_sboms`
 * could not summarise (recorded with `components: null` — a non-CycloneDX document, or one that
 * failed to parse), named by the file the inventory recorded it under. A readable SBOM that
 * simply names no components (`components: []`) is not a problem. */
function embeddedSbomProblems(d) {
  return (d.embedded ?? [])
    .filter((entry) => entry.components === null)
    .map((entry) => `${d.key}: embedded SBOM ${entry.file} unreadable`);
}

/**
 * Assembles one component's two views from its own facts and its matching table row: the
 * uniform shape `reviewProblems` checks (own licence and own texts only), and the text actually
 * rendered (the row's licence when it sets one, and every text the component or its row names,
 * merged for the shared "Licence texts" pool).
 * @param {{section: string, key: string, name: string, version: string,
 *   ownLicence: string|null, ownTexts: {title: string, text: string}[], requiresRow: boolean,
 *   row: object|undefined, repo: string, texts: Map<string, string>, bundles?: string[]}} args
 */
function makeEntry({
  section,
  key,
  name,
  version,
  ownLicence,
  ownTexts,
  requiresRow,
  row,
  repo,
  texts,
  bundles = [],
}) {
  const reviewComponent = { key, name, version, licence: ownLicence, texts: ownTexts, requiresRow };

  const licence =
    row && typeof row.licence === "string" && row.licence !== "" ? row.licence : ownLicence;

  const textRefs = [...ownTexts];
  for (const id of row?.texts ?? []) {
    if (texts.has(id)) textRefs.push({ title: id, text: texts.get(id) });
  }
  if (row?.mit !== undefined) {
    textRefs.push({ title: `${name} ${version} (MIT)`, text: mitText(row.mit) });
  }
  for (const path of row?.repo_texts ?? []) {
    textRefs.push({ title: path, text: readFileSync(join(repo, path), "utf8") });
  }

  return {
    reviewComponent,
    renderEntry: { section, name, version, licence, note: row?.note, bundles, textRefs },
  };
}

/** Section 1: a server-bundled Python distribution from the licence inventory. */
function buildDistributionEntry(d, row, repo, texts) {
  const ownTexts = sortedTexts(
    Object.entries(d.files ?? {}).map(([path, text]) => ({ title: path, text })),
  );
  const bundles = (d.embedded ?? []).flatMap((entry) => entry.components ?? []).map(bundleLine);
  return makeEntry({
    section: "Python packages in the local server",
    key: d.key,
    name: d.name,
    version: d.version,
    ownLicence: distributionLicence(d),
    ownTexts,
    requiresRow: false,
    row,
    repo,
    texts,
    bundles,
  });
}

/** Section 2: the CPython runtime the server was compiled with; always needs a reviewed row. */
function buildCpythonEntry(python, rows, repo, texts) {
  const key = `python:cpython@${python.version}`;
  return makeEntry({
    section: "CPython",
    key,
    name: "cpython",
    version: python.version,
    ownLicence: "PSF-2.0",
    ownTexts: [{ title: "CPython LICENSE.txt", text: python.licence }],
    requiresRow: true,
    row: rows[key],
    repo,
    texts,
  });
}

/** Section 3: the Nuitka runtime the server was compiled with; always needs a reviewed row
 * (its own licence is AGPL-3.0, only shippable through the row's free-text exception note). */
function buildNuitkaEntry(nuitka, rows, repo, texts) {
  const key = `pypi:nuitka@${nuitka.version}`;
  const ownTexts = sortedTexts(
    Object.entries(nuitka.files ?? {}).map(([title, text]) => ({ title, text })),
  );
  return makeEntry({
    section: "Nuitka runtime",
    key,
    name: "nuitka",
    version: nuitka.version,
    ownLicence: null,
    ownTexts,
    requiresRow: true,
    row: rows[key],
    repo,
    texts,
  });
}

/**
 * The installed copy's own `package.json` must report the version the lockfile expects.
 * An unreadable or version-less `package.json` is the same gap either way — this check cannot
 * confirm anything about what is actually installed — so both are reported with one message; a
 * real version that differs from the lock names what is actually installed. Only meaningful once
 * the package's folder is known to exist: `buildNpmSection` calls this only when its own
 * `licenceFiles` read did not already report that folder missing, so the two problems never fire
 * for the same gap.
 */
function installedVersionProblem(key, dir, tree, lockVersion) {
  let installed;
  try {
    installed = JSON.parse(readFileSync(join(dir, "package.json"), "utf8")).version;
  } catch {
    installed = undefined;
  }
  if (typeof installed !== "string" || installed === "") {
    return `${key}: installed package.json under ${tree} is unreadable, run npm ci`;
  }
  return installed === lockVersion ? null : `${key}: installed ${installed}, run npm ci`;
}

/**
 * Sections 4 and 5: one lockfile tree's production npm packages. A package's own licence files
 * live at `<repo>/<tree>/<pkg.path>/`; a missing folder (no `npm ci` run in that tree) is its own
 * reported problem rather than a silent empty texts list, naming the tree so the fix is obvious.
 * When the folder does exist, its `package.json` must also agree with the lock's version
 * (`installedVersionProblem`).
 */
function buildNpmSection({ repo, tree, section, rows, texts }) {
  const lock = JSON.parse(readFileSync(join(repo, tree, "package-lock.json"), "utf8"));
  const entries = [];
  const problems = [];
  for (const pkg of prodPackages(lock)) {
    const key = `npm:${pkg.name}@${pkg.version}`;
    const dir = join(repo, tree, pkg.path);
    let ownTexts = [];
    let folderMissing = false;
    try {
      ownTexts = licenceFiles(dir).map((name) => ({
        title: `${tree}/${pkg.path}/${name}`,
        text: readFileSync(join(dir, name), "utf8"),
      }));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
      folderMissing = true;
      problems.push(`${key}: no node_modules folder under ${tree}, run npm ci`);
    }
    if (!folderMissing) {
      const problem = installedVersionProblem(key, dir, tree, pkg.version);
      if (problem) problems.push(problem);
    }
    entries.push(
      makeEntry({
        section,
        key,
        name: pkg.name,
        version: pkg.version,
        ownLicence: pkg.license ?? null,
        ownTexts,
        requiresRow: false,
        row: rows[key],
        repo,
        texts,
      }),
    );
  }
  return { entries, problems };
}

/** `extra:<name>@<version>` (or just `extra:<name>` with no version) into its display parts. */
function splitExtraKey(key) {
  const rest = key.slice("extra:".length);
  const at = rest.lastIndexOf("@");
  return at === -1
    ? { name: rest, version: "" }
    : { name: rest.slice(0, at), version: rest.slice(at + 1) };
}

/** Section 6: table rows with no package of their own ("Other components"), applicable to
 * `platform`. These exist only through their row, so their whole licence and texts come from it. */
function buildExtraEntries(rows, platform, repo, texts) {
  const entries = [];
  for (const [key, row] of Object.entries(rows)) {
    if (!key.startsWith("extra:")) continue;
    if (row.platforms && !row.platforms.includes(platform)) continue;
    const { name, version } = splitExtraKey(key);
    entries.push(
      makeEntry({
        section: "Other components",
        key,
        name,
        version,
        ownLicence: null,
        ownTexts: [],
        requiresRow: false,
        row,
        repo,
        texts,
      }),
    );
  }
  return entries;
}

// -- unattributed files -------------------------------------------------------------------------

/** Whether `glob` (`*` a run of non-`/` characters) matches `path` in full. */
function matchesGlob(glob, path) {
  const escapeRe = (segment) => segment.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = glob.split("*").map(escapeRe).join("[^/]*");
  return new RegExp(`^${pattern}$`).test(path);
}

/** Whether some table row applicable to `platform` has a `files` glob matching `path`. */
function isCovered(path, rows, platform) {
  for (const row of Object.values(rows)) {
    if (row.platforms && !row.platforms.includes(platform)) continue;
    if (!row.files) continue;
    if (row.files.some((glob) => matchesGlob(glob, path))) return true;
  }
  return false;
}

// -- text rendering -----------------------------------------------------------------------------

/** The file's opening paragraphs: version, platform, what the file is, where Electron's own two
 * notice files live, and where to ask for source. No timestamp. */
function headerLines({ version, platform, homepage }) {
  return [
    `VIKI Agent ${version} - Third-party notices (${platform})`,
    "",
    "This file lists the licences of third-party components bundled with this installer.",
    "Electron's own third-party notices are listed separately, next to the application",
    "executable, in LICENSE.electron.txt and LICENSES.chromium.html (in Contents/Resources",
    "on macOS).",
    "",
    `Source for any component that requires it is available on request at ${homepage}.`,
  ];
}

/**
 * Renders every section in `SECTION_TITLES` order, then "Licence texts" once at the end: every
 * text any entry names, deduplicated by content (so two components naming the same shared text,
 * for example two rows both citing the table's "mpl-2.0" id, print it once, not once per
 * component), each preceded by `---- <title> ----`, titled after its first occurrence. LF only,
 * exactly one trailing newline, no timestamp. An entry with no licence (`null`) prints
 * `==== <name>[ <version>]` with no trailing ` - <licence>` at all, never ` - UNKNOWN`: for a
 * package component that is already a `reviewProblems` gap that refuses the build before this
 * text ships (`dist.mjs`); for an `extra:` row reviewed through its `note` instead of a licence
 * id (electron, microsoft-vc-runtime, messaging-logos), that is the expected, reviewed shape.
 * @param {{section: string, name: string, version: string, licence: string|null,
 *   note?: string, bundles: string[], textRefs: {title: string, text: string}[]}[]} entries
 */
function renderText(entries, { version, platform, homepage }) {
  const textPool = new Map(); // text content -> the title it first appeared under
  const sharedTexts = [];
  const titleFor = (title, text) => {
    if (textPool.has(text)) return textPool.get(text);
    textPool.set(text, title);
    sharedTexts.push({ title, text });
    return title;
  };

  const bySection = new Map(SECTION_TITLES.map((title) => [title, []]));
  for (const entry of entries) {
    const titles = entry.textRefs.map(({ title, text }) => titleFor(title, text));
    bySection.get(entry.section).push({ ...entry, titles });
  }

  const lines = [...headerLines({ version, platform, homepage })];
  for (const title of SECTION_TITLES) {
    if (title === "Licence texts") continue;
    lines.push("", title, "=".repeat(title.length));
    for (const entry of bySection.get(title)) {
      const versionPart = entry.version ? ` ${entry.version}` : "";
      const licencePart =
        typeof entry.licence === "string" && entry.licence !== "" ? ` - ${entry.licence}` : "";
      lines.push("", `==== ${entry.name}${versionPart}${licencePart}`);
      if (entry.note) lines.push(entry.note);
      lines.push(...entry.bundles);
      lines.push(...entry.titles.map((t) => `Licence text: ${t}`));
    }
  }

  lines.push("", "Licence texts", "=".repeat("Licence texts".length));
  for (const { title, text } of sharedTexts) {
    lines.push("", `---- ${title} ----`, text);
  }

  return `${lines.join("\n")}\n`;
}

// -- buildNotices ---------------------------------------------------------------------------

/**
 * Composes `THIRD-PARTY-NOTICES.txt` and the fail-closed gaps that should refuse a build before
 * it ships.
 * @param {{repo: string, inventory: object, table: {allowed: string[], texts: object,
 *   components: object}, texts: Map<string, string>, platform: string, version: string,
 *   homepage: string}} args `inventory` is `scripts/licence_inventory.py`'s JSON, already
 *   parsed; `table` is `licences.mjs`'s `loadTable` result; `texts` is its `loadTexts` result;
 *   `platform` is a `process.platform` value; `version` and `homepage` come from
 *   `desktop/package.json`. Each row renders as it applies on `platform` (`rowFor`).
 * @returns {{text: string, problems: string[]}} `problems` is sorted and empty when the table
 *   covers everything the inventory and both lockfiles report.
 */
export function buildNotices({ repo, inventory, table, texts, platform, version, homepage }) {
  const rows = Object.fromEntries(
    Object.entries(table.components ?? {}).map(([key, row]) => [key, rowFor(row, platform)]),
  );
  const built = [];
  const problems = [];

  if (inventory.platform !== platform) {
    problems.push(
      `inventory platform ${inventory.platform} differs from build platform ${platform}`,
    );
  }

  for (const d of inventory.distributions ?? []) {
    built.push(buildDistributionEntry(d, rows[d.key], repo, texts));
    problems.push(...embeddedSbomProblems(d));
  }
  built.push(buildCpythonEntry(inventory.python, rows, repo, texts));
  built.push(buildNuitkaEntry(inventory.nuitka, rows, repo, texts));

  for (const { tree, section } of NPM_SECTIONS) {
    const npmSection = buildNpmSection({ repo, tree, section, rows, texts });
    built.push(...npmSection.entries);
    problems.push(...npmSection.problems);
  }

  built.push(...buildExtraEntries(rows, platform, repo, texts));

  problems.push(
    ...reviewProblems(
      built.map((entry) => entry.reviewComponent),
      table,
      platform,
    ),
  );
  for (const path of inventory.unattributed ?? []) {
    if (!isCovered(path, rows, platform)) {
      problems.push(`unattributed: ${path} matched by no reviewed row`);
    }
  }
  problems.sort();

  const text = renderText(
    built.map((entry) => entry.renderEntry),
    { version, platform, homepage },
  );
  return { text, problems };
}
