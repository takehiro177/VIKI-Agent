import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";
import {
  createSite,
  diagramDigest,
  headingSlug,
  presentationText,
  validateSite,
  writeSite,
} from "./build-docs.mjs";
import {
  BOT_ICONS,
  botIconFiles,
  botIconSvg,
  iconTokens,
  writeBotIcons,
} from "./doc-bot-icons.mjs";

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "viki-html-docs-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const write = (name, text) => {
    const file = path.join(root, name);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, text, "utf8");
  };
  write(
    "docs/index.html",
    '<!doctype html><a id="library" href="manual/docs/README.html">Documentation</a>',
  );
  write(
    "docs/README.md",
    "# Documentation\n\n[Nested](guides/start.md?view=full#context--loadout)\n\n[All guides](guides/)\n",
  );
  write(
    "docs/guides/start.md",
    "# Start\n\n## Context & loadout\n\n![Diagram](../assets/diagram.svg?size=2#view)\n\n[Code](../../src/example.py?raw=1)\n\n[Back](../README.md)\n",
  );
  for (const name of [
    "brand-icon.ico",
    "brand-mark.png",
    "wordmark.svg",
    "guide.css",
    "manual.css",
    "doc-search.js",
    "diagram.svg",
  ]) {
    write(`docs/assets/${name}`, "synthetic");
  }
  write("src/example.py", "# synthetic source\n");
  return { root, write, output: (name) => path.join(root, "docs/manual", name) };
}

function searchDocument(t, site, query = "") {
  const html = site.files.get(path.join(site.output, "search.html"));
  const dom = new JSDOM(html, {
    url: `https://docs.example.test/manual/search.html?q=${encodeURIComponent(query)}`,
    runScripts: "outside-only",
  });
  t.after(() => dom.window.close());
  dom.window.eval(
    fs.readFileSync(new URL("../../docs/assets/doc-search.js", import.meta.url), "utf8"),
  );
  return dom.window.document;
}

test("nested pages, directory indexes, fragments and assets stay in HTML navigation", (t) => {
  const files = fixture(t);
  const site = createSite(files);
  const map = site.files.get(files.output("docs/README.html"));
  const nested = site.files.get(files.output("docs/guides/start.html"));
  const directory = site.files.get(files.output("docs/guides/index.html"));
  assert.match(map, /href="guides\/start.html\?view=full#context--loadout"/);
  assert.match(map, /href="guides\/index.html"/);
  assert.match(nested, /src="\.\.\/\.\.\/\.\.\/assets\/diagram.svg\?size=2#view"/);
  assert.match(nested, /href="\.\.\/\.\.\/\.\.\/\.\.\/src\/example.py\?raw=1"/);
  assert.match(directory, /href="start.html"/);
  assert.ok(validateSite(site).links > 10);
});

test("navigation and section search use the canonical manuals", (t) => {
  const files = fixture(t);
  const manuals = [
    ["getting-started", "Get started"],
    ["using-the-app", "Using the app"],
    ["decision-engine", "Decision"],
    ["configuration", "Configuration"],
    ["sessions-and-data", "Sessions & data"],
    ["security", "Security"],
    ["troubleshooting", "Troubleshooting"],
    ["known-issues", "Known limits"],
  ];
  files.write("docs/concepts.md", "# Concepts\n");
  files.write(
    "docs/README.md",
    `# Documentation\n\n[Concepts](concepts.md)\n\n${manuals
      .map(([slug, label]) => {
        files.write(
          `docs/${slug}.md`,
          `# ${label}\n\n## Detailed controls\n\nNavigation example for ${label}.\n`,
        );
        return `[${label}](${slug}.md)`;
      })
      .join("\n\n")}\n`,
  );
  const site = createSite({ ...files, diagrams: [] });
  for (const [currentSlug] of manuals) {
    const name = `docs/${currentSlug}.html`;
    const dom = new JSDOM(site.files.get(files.output(name)), {
      url: `https://docs.example.test/manual/${name}`,
    });
    t.after(() => dom.window.close());
    const navigation = dom.window.document.querySelector(".manual-nav");
    for (const [slug, label] of manuals) {
      const link = [...navigation.querySelectorAll("a")].find((item) => item.textContent === label);
      assert.equal(new URL(link.href).pathname, `/manual/docs/${slug}.html`);
      assert.equal(link.getAttribute("aria-current"), slug === currentSlug ? "page" : null);
    }
    assert.equal(navigation.querySelectorAll('[aria-current="page"]').length, 1);
    assert.equal(new URL(navigation.querySelector("a").href).pathname, "/index.html");
    assert.equal(
      new URL(navigation.querySelectorAll("a")[1].href).pathname,
      "/manual/docs/concepts.html",
    );
  }
  const search = searchDocument(t, site, "navigation using");
  const results = [...search.querySelectorAll(".search-result:not([hidden])")];
  assert.deepEqual(
    results.map((item) => item.querySelector(".search-result-page").textContent).sort(),
    ["Guide · Using the app"],
  );
  for (const result of results) {
    assert.ok(result.querySelector("a").getAttribute("href").endsWith("#detailed-controls"));
  }
  assert.ok(validateSite(site).links > 20);
});

test("the published site contains only canonical operator guides and current video transcripts", () => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const site = createSite({ root });
  const pages = [...site.files.keys()].map((file) =>
    path.relative(site.output, file).split(path.sep).join("/"),
  );
  assert.deepEqual(pages.sort(), [
    "docs/README.html",
    "docs/assets/videos/chat-launcher-demo-transcript.html",
    "docs/assets/videos/viki-agent-cinematic-launch-paced-transcript.html",
    "docs/bots.html",
    "docs/concepts.html",
    "docs/configuration.html",
    "docs/decision-engine.html",
    "docs/getting-started.html",
    "docs/known-issues.html",
    "docs/mattermost.html",
    "docs/messaging.html",
    "docs/security.html",
    "docs/sessions-and-data.html",
    "docs/troubleshooting.html",
    "docs/using-the-app.html",
    "search.html",
  ]);
  const search = site.files.get(path.join(site.output, "search.html"));
  assert.doesNotMatch(search, /docs\/(?:current|decisions|design)\/|Legacy source reference/);
  assert.ok(validateSite(site).links > 20);
});

test("inline headings have stable, collision-safe IDs and an accessible table wrapper", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    "# Start\n\n## Context & loadout\n\n## Same\n\n## **Same**\n\n## `Same-1`\n\n| Name | Value |\n| --- | --- |\n| A | B |\n",
  );
  const page = createSite(files).files.get(files.output("docs/guides/start.html"));
  for (const id of ["context--loadout", "same", "same-1", "same-1-1"]) {
    assert.match(page, new RegExp(`id="${id}"`));
    assert.match(page, new RegExp(`href="#${id}"`));
  }
  assert.match(page, /class="table-scroll" tabindex="0" role="region" aria-label="Same-1 table"/);
  assert.equal(headingSlug("日本語 `Code` & context"), "日本語-code--context");
});

test("raw HTML and dangerous URLs cannot become active content or remote images", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    '# Start\n\n## Context & loadout\n\n<script>alert("unsafe")</script>\n\n[Unsafe](javascript:alert%281%29)\n\n![Remote](https://example.test/image.png)\n\n[Reference](https://example.test/docs)\n',
  );
  const page = createSite(files).files.get(files.output("docs/guides/start.html"));
  assert.doesNotMatch(page, /<script|javascript:|src="https:/);
  assert.match(page, /href="https:\/\/example.test\/docs"/);
  assert.match(page, /<span>Remote<\/span>/);
});

test("missing Markdown sources and repository escapes fail before generating output", (t) => {
  const files = fixture(t);
  files.write("docs/guides/start.md", "# Start\n\n[Missing](missing.md)\n");
  assert.throws(() => createSite(files), /Missing documentation target/);
  files.write("docs/guides/start.md", "# Start\n\n[Escape](../../../outside.md)\n");
  assert.throws(() => createSite(files), /leaves the repository/);
  assert.throws(
    () => createSite({ root: files.root, entry: "../outside.md" }),
    /leaves the repository/,
  );
  assert.equal(fs.existsSync(files.output("docs/README.html")), false);
});

test("freshness checks detect source changes, edited output and obsolete pages", (t) => {
  const files = fixture(t);
  const site = createSite(files);
  writeSite(site);
  writeSite(site, { check: true });
  files.write("docs/guides/start.md", "# Changed\n\n## Context & loadout\n");
  assert.throws(() => writeSite(createSite(files), { check: true }), /stale/);
  fs.appendFileSync(files.output("docs/README.html"), "changed");
  assert.throws(() => writeSite(site, { check: true }), /stale/);
  fs.writeFileSync(files.output("unrecognized.txt"), "user file");
  assert.throws(() => writeSite(site), /unrecognized generated file/);
  fs.unlinkSync(files.output("unrecognized.txt"));
  fs.writeFileSync(files.output("docs/README.html"), "user file");
  assert.throws(() => writeSite(site), /overwrite an unrecognized generated file/);
});

test("validation rejects raw Markdown navigation and broken fragments", (t) => {
  const files = fixture(t);
  files.write("docs/index.html", '<a href="README.md">Raw source</a>');
  assert.throws(() => validateSite(createSite(files)), /points to Markdown/);
  files.write("docs/index.html", '<a href="manual/docs/README.html#missing">Broken fragment</a>');
  assert.throws(() => validateSite(createSite(files)), /Missing HTML fragment/);
});

test("presentation placeholders remove private path and provider token markers", () => {
  const home = "C:" + "\\" + "Users" + "\\sample-account\\project";
  const source = `${home} sample-account ${"." + "credentials"}.json ${"sk-" + "ant-"}`;
  const rendered = presentationText(source);
  assert.equal(rendered, "%HOME% owner [provider credential filename].json [provider key prefix]");
});

test("private account links inherited from another source keep their label without a destination", (t) => {
  const files = fixture(t);
  files.write(
    "AGENTS.md",
    `# Rules\n\n\`${"C:" + "\\" + "Users" + "\\sample-account\\project"}\`\n`,
  );
  files.write(
    "docs/README.md",
    "# Documentation\n\n[Rules](../AGENTS.md)\n\n[VIKI](https://github.com/sample-account177/VIKI)\n\n[Library](https://github.com/sample-account-tools/library)\n",
  );
  const site = createSite(files);
  const page = site.files.get(files.output("docs/README.html"));
  assert.match(page, /<span>VIKI<\/span>/);
  assert.doesNotMatch(page, /sample-account177|github.com\/owner/);
  assert.match(page, /href="https:\/\/github.com\/sample-account-tools\/library"/);
  assert.doesNotMatch(
    site.files.get(files.output("search.html")),
    /sample-account177|github.com\/owner/,
  );
});

test("generated copies redact the root README repository account without a home path", (t) => {
  const files = fixture(t);
  const original =
    "# Project\n\n```sh\ngit clone https://github.com/sample-account177/project\n```\n\n[Manuals](docs/README.md)\n";
  files.write("README.md", original);
  files.write("docs/README.md", "# Documentation\n\n[Project](../README.md)\n");
  const site = createSite(files);
  for (const name of ["README.html", "search.html"]) {
    assert.doesNotMatch(site.files.get(files.output(name)), /sample-account177/);
    assert.doesNotMatch(site.files.get(files.output(name)), /github.com\/owner\//);
  }
  const document = new JSDOM(site.files.get(files.output("README.html"))).window.document;
  assert.match(document.querySelector("pre code").textContent, /git clone <repository-url>/);
  assert.match(document.querySelector("pre code").textContent, /root README.md/);
  assert.equal(fs.readFileSync(path.join(files.root, "README.md"), "utf8"), original);
});

test("manual sections preserve source order and IDs with the jump grid after the introduction", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    "# Working **guide**\n\nOpening [context](../README.md).\n\nAnother intro.\n\n## First `step`\n\nOne fact.\n\n### Details\n\nA detail.\n\n## Second step\n\nTwo facts.\n",
  );
  const page = createSite(files).files.get(files.output("docs/guides/start.html"));
  const document = new JSDOM(page).window.document;
  const article = document.querySelector("article");
  assert.equal(document.querySelector("main#main").getAttribute("tabindex"), "-1");
  const hero = article.querySelector(".manual-hero");
  assert.equal(hero.querySelector("h1").id, "working-guide");
  assert.equal(hero.querySelectorAll("p").length, 2);
  assert.equal(hero.nextElementSibling.className, "manual-toc");
  assert.deepEqual(
    [...article.querySelectorAll(".manual-section")].map((section) =>
      section.getAttribute("aria-labelledby"),
    ),
    ["first-step", "second-step"],
  );
  assert.deepEqual(
    [...article.querySelectorAll("h2 > .section-number")].map((span) => [
      span.textContent,
      span.getAttribute("aria-hidden"),
    ]),
    [
      ["01", "true"],
      ["02", "true"],
    ],
  );
  assert.deepEqual(
    [...article.querySelectorAll(".manual-toc a")].map((link) => [
      link.getAttribute("href"),
      link.querySelector(".toc-label").textContent,
    ]),
    [
      ["#first-step", "First step"],
      ["#second-step", "Second step"],
    ],
  );
  article.querySelector(".manual-toc").remove();
  for (const number of article.querySelectorAll(".section-number")) number.remove();
  assert.equal(
    article.textContent.replace(/\s+/g, " ").trim(),
    "Working guide Opening context. Another intro. First step One fact. Details A detail. Second step Two facts.",
  );
  assert.equal(article.querySelector("h3").id, "details");
  assert.equal(article.querySelector("p a").getAttribute("href"), "../README.html");
});

test("only the explicitly named review loop receives the workflow presentation", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    "# Context\n\n## Keep context current\n\n### A review loop for the next turn\n\n1. **Check the source.** Read its date.\n2. **Choose context.** Keep the current constraint.\n3. **Work.** Review the result.\n4. **Keep learning.** Save the useful finding.\n\n### Another sequence\n\n1. Ordinary first item.\n2. Ordinary second item.\n",
  );
  const page = createSite(files).files.get(files.output("docs/guides/start.html"));
  const document = new JSDOM(page).window.document;
  const loop = document.querySelector("ol.context-review-loop");
  assert.equal(loop.children.length, 4);
  assert.equal(loop.previousElementSibling.id, "a-review-loop-for-the-next-turn");
  assert.equal(loop.querySelector("strong").textContent, "Check the source.");
  assert.equal(document.querySelectorAll("ol.context-review-loop").length, 1);
  assert.equal(document.querySelector("#another-sequence").nextElementSibling.className, "");
});

test("GitHub alerts become titled callouts while plain quotes stay quotes", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    "# Start\n\n## Context & loadout\n\n> [!WARNING]\n> Deleting a vault removes its notes.\n\n> [!TIP]\n>\n> Keep a backup.\n\n> A plain quote.\n",
  );
  const document = new JSDOM(createSite(files).files.get(files.output("docs/guides/start.html")))
    .window.document;
  const alerts = [...document.querySelectorAll(".manual-alert")];
  assert.deepEqual(
    alerts.map((alert) => [alert.className, alert.getAttribute("role")]),
    [
      ["manual-alert alert-warning", "note"],
      ["manual-alert alert-tip", "note"],
    ],
  );
  assert.equal(alerts[0].querySelector(".alert-title").textContent, "Warning");
  assert.equal(
    alerts[0].textContent.replace(/\s+/g, " ").trim(),
    "Warning Deleting a vault removes its notes.",
  );
  assert.equal(alerts[1].querySelectorAll("p").length, 2);
  assert.doesNotMatch(document.querySelector("article").textContent, /\[!/);
  assert.equal(document.querySelectorAll("blockquote").length, 1);
  assert.equal(document.querySelector("blockquote").textContent.trim(), "A plain quote.");
});

test("bullet lists that open every item with a bold label become label rows", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    [
      "# Start",
      "## Context & loadout",
      "- **Storage:** keys are `sealed`.\n- **Updating a key** — keeps its identity.\n- **Removing.** Drops the key.",
      "### Sentences",
      "- **Allow once** and **Deny** are the choices.\n- **Stop** ends work.",
      "### Mixed",
      "- **Storage:** keys are sealed.\n- Plain item.",
    ].join("\n\n"),
  );
  const document = new JSDOM(createSite(files).files.get(files.output("docs/guides/start.html")))
    .window.document;
  const lists = [...document.querySelectorAll("article ul")];
  assert.deepEqual(
    lists.map((list) => list.className),
    ["label-list", "", ""],
  );
  assert.deepEqual(
    [...lists[0].querySelectorAll(":scope > li")].map((item) => [
      item.querySelector(".label-term").textContent,
      item.querySelector(".label-body").textContent,
    ]),
    [
      ["Storage", "keys are sealed."],
      ["Updating a key", "keeps its identity."],
      ["Removing.", "Drops the key."],
    ],
  );
  assert.equal(lists[0].querySelector(".label-body code").textContent, "sealed");
});

test("Bot icon lists become rosters and icon-only paragraphs become strips", (t) => {
  const files = fixture(t);
  for (const name of ["chief", "ops"]) files.write(`docs/assets/bots/${name}.svg`, "synthetic");
  const chief = "![Chief icon](../assets/bots/chief.svg)";
  const ops = "![Ops icon](../assets/bots/ops.svg)";
  files.write(
    "docs/guides/start.md",
    [
      "# Start",
      "## Context & loadout",
      `- ${chief} **Chief:** The front door.\n- ${ops} **Ops:** The manager.`,
      `${chief}\n${ops}`,
      "### Mixed",
      `- ${chief} **Chief:** The front door.\n- **Ops:** The manager.`,
      `${chief} and Ops`,
    ].join("\n\n"),
  );
  const document = new JSDOM(createSite(files).files.get(files.output("docs/guides/start.html")))
    .window.document;
  const lists = [...document.querySelectorAll("article ul")];
  assert.deepEqual(
    lists.map((list) => list.className),
    ["bot-roster", ""],
  );
  assert.deepEqual(
    [...lists[0].children].map((item) => [
      item.className,
      item.firstElementChild.className,
      item.firstElementChild.getAttribute("alt"),
      item.querySelector(".label-term").textContent,
      item.querySelector(".label-body").textContent,
    ]),
    [
      ["seat-chief", "bot-roster-face", "Chief icon", "Chief", "The front door."],
      ["seat-ops", "bot-roster-face", "Ops icon", "Ops", "The manager."],
    ],
  );
  assert.deepEqual(
    [...document.querySelectorAll("article p")]
      .filter((paragraph) => paragraph.querySelector("img"))
      .map((paragraph) => paragraph.className),
    ["bot-strip", ""],
  );
});

test("Bot icons are the app's own designs, in its dark colors, and match the app", () => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const tokens = iconTokens(
    fs.readFileSync(path.join(root, "frontend/src/styles/tokens.css"), "utf8"),
  );
  const chief = botIconSvg(
    BOT_ICONS.find((icon) => icon.name === "Chief"),
    tokens,
  );
  assert.doesNotMatch(chief, /var\(/);
  assert.match(chief, /role="img" aria-labelledby="title desc"/);
  assert.match(chief, /<title id="title">Chief<\/title>/);
  assert.match(chief, /ring eyes and a door emblem, lit orange\./);
  assert.match(chief, new RegExp(`<g fill="${tokens.get("--label-orange")}">`));
  const pink = botIconSvg(
    BOT_ICONS.find((icon) => icon.hue === "pink"),
    tokens,
  );
  assert.match(pink, new RegExp(`<g fill="${tokens.get("--bot-pink")}">`));
  assert.throws(() => botIconSvg(BOT_ICONS[0], new Map()), /no dark value/);
  assert.throws(() => iconTokens(":root { --g0: #000; }"), /lacks a block/);
  writeBotIcons(root, botIconFiles(root), { check: true });
});

test("Bot icon writes detect stale and obsolete icons and never touch other files", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "viki-bot-icons-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const directory = path.join(root, "docs/assets/bots");
  const tokens = iconTokens(
    fs.readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8"),
  );
  const generated = botIconSvg(BOT_ICONS[0], tokens);
  const files = new Map([[path.join(directory, "chief.svg"), generated]]);
  assert.throws(() => writeBotIcons(root, files, { check: true }), /1 changed, 0 obsolete/);
  writeBotIcons(root, files);
  writeBotIcons(root, files, { check: true });
  fs.writeFileSync(path.join(directory, "old.svg"), generated);
  assert.throws(() => writeBotIcons(root, files, { check: true }), /0 changed, 1 obsolete/);
  writeBotIcons(root, files);
  assert.deepEqual(fs.readdirSync(directory), ["chief.svg"]);
  fs.writeFileSync(path.join(directory, "mine.svg"), "<svg/>");
  assert.throws(() => writeBotIcons(root, files), /remove an unrecognized/);
  fs.rmSync(path.join(directory, "mine.svg"));
  fs.writeFileSync(path.join(directory, "chief.svg"), "<svg/>");
  assert.throws(() => writeBotIcons(root, files), /overwrite an unrecognized/);
});

test("table cells that only state a status render as badges", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    "# Start\n\n## Context & loadout\n\n| Action | Windows | Linux |\n| --- | --- | --- |\n| Save | Yes | Partly |\n| Sync | Not available | Yes, on ext4 |\n",
  );
  const document = new JSDOM(createSite(files).files.get(files.output("docs/guides/start.html")))
    .window.document;
  assert.deepEqual(
    [...document.querySelectorAll("td .badge")].map((badge) => [
      badge.className,
      badge.textContent,
    ]),
    [
      ["badge yes", "Yes"],
      ["badge part", "Partly"],
      ["badge no", "Not available"],
    ],
  );
  assert.equal(document.querySelectorAll("th .badge").length, 0);
});

test("manual pages carry an icon, reading facts outside the article and previous/next links", (t) => {
  const files = fixture(t);
  for (const [slug, title] of [
    ["concepts", "Concepts"],
    ["getting-started", "Get started"],
    ["security", "Security"],
  ]) {
    files.write(`docs/${slug}.md`, `# ${title}\n\n${"Word ".repeat(460)}\n\n## One\n\nText.\n`);
  }
  files.write(
    "docs/README.md",
    "# Documentation\n\n[Concepts](concepts.md) [Get started](getting-started.md) [Security](security.md)\n",
  );
  const site = createSite({ ...files, diagrams: [] });
  const page = (name) =>
    new JSDOM(site.files.get(files.output(name)), {
      url: `https://docs.example.test/manual/${name}`,
    }).window.document;
  const started = page("docs/getting-started.html");
  assert.ok(started.querySelector(".manual-hero > .manual-icon .icon-terminal"));
  assert.equal(started.querySelector(".manual-facts").textContent, "2 min read · 1 section");
  assert.equal(started.querySelector("article .manual-facts"), null);
  const link = (document, rel) => {
    const anchor = document.querySelector(`.manual-pager a[rel="${rel}"]`);
    return (
      anchor && [new URL(anchor.href).pathname, anchor.querySelector(".pager-title").textContent]
    );
  };
  assert.deepEqual(link(started, "prev"), ["/manual/docs/concepts.html", "Concepts"]);
  assert.deepEqual(link(started, "next"), ["/manual/docs/security.html", "Security"]);
  assert.deepEqual(link(page("docs/concepts.html"), "prev"), ["/index.html", "Field guide"]);
  assert.equal(link(page("docs/security.html"), "next"), null);
  assert.equal(page("docs/README.html").querySelector(".manual-pager"), null);
  assert.ok(page("docs/README.html").querySelector(".manual-hero .icon-book"));
  assert.deepEqual(
    [...started.querySelectorAll(".manual-nav .nav-label")].map((label) => label.textContent),
    ["Start here", "Reference"],
  );
});

test("registered text diagrams become SVG figures while program samples remain code", (t) => {
  const files = fixture(t);
  const code = "Start\n  |\n  v\nReview";
  files.write(
    "docs/assets/diagrams/synthetic-loop.svg",
    '<svg xmlns="http://www.w3.org/2000/svg"/>',
  );
  files.write(
    "docs/guides/start.md",
    `# Start\n\n## Context & loadout\n\n\`\`\`text\n${code}\n\`\`\`\n\n\`\`\`python\nprint("Start -> Review")\n\`\`\`\n`,
  );
  const diagrams = [
    {
      sourcepath: "docs/guides/start.md",
      sha256: diagramDigest(code),
      asset_slug: "synthetic-loop",
      caption: "Review follows the starting point.",
    },
  ];
  const site = createSite({ ...files, diagrams });
  const document = new JSDOM(site.files.get(files.output("docs/guides/start.html"))).window
    .document;
  const figure = document.querySelector("figure.manual-diagram");
  assert.match(
    figure.querySelector("img").getAttribute("src"),
    /assets\/diagrams\/synthetic-loop.svg$/,
  );
  assert.equal(figure.querySelector(".diagram-canvas").getAttribute("tabindex"), "0");
  const fullSize = figure.querySelector(".diagram-tools a");
  assert.equal(fullSize.getAttribute("href"), figure.querySelector("img").getAttribute("src"));
  assert.equal(
    fullSize.getAttribute("aria-label"),
    `Open full-size diagram: ${diagrams[0].caption}`,
  );
  assert.equal(fullSize.textContent, "Open full-size diagram ↗");
  assert.equal(figure.querySelector("figcaption").textContent, diagrams[0].caption);
  assert.deepEqual(
    [...figure.querySelectorAll(".diagram-description li")].map((item) => item.textContent),
    ["Start", "Review"],
  );
  assert.equal(document.querySelectorAll("pre").length, 1);
  assert.equal(document.querySelector("pre").textContent.trim(), 'print("Start -> Review")');
  assert.ok(validateSite(site).links > 10);
});

test("a changed or removed registered diagram fails instead of falling back to text art", (t) => {
  const files = fixture(t);
  const code = "A\n  |\nB";
  const diagrams = [
    {
      sourcepath: "docs/guides/start.md",
      sha256: diagramDigest(code),
      asset_slug: "synthetic-loop",
      caption: "A and B.",
    },
  ];
  files.write("docs/guides/start.md", `# Start\n\n\`\`\`text\n${code} changed\n\`\`\`\n`);
  assert.throws(() => createSite({ ...files, diagrams }), /Diagram source changed/);
  files.write("docs/guides/start.md", "# Start\n\nNo diagram.\n");
  assert.throws(() => createSite({ ...files, diagrams }), /Diagram source changed/);
  assert.equal(diagramDigest("A\r\nB"), diagramDigest("A\nB"));
});

test("folder cards preserve names, hierarchy and wrapped descriptions without a text tree", (t) => {
  const files = fixture(t);
  const code =
    "viki-agent\\\n  logs\\\n    viki-agent.log        rotating, WARNING and above, UTF-8\n  app.sqlite3               preferences, encrypted editor drafts,\n                             workspace registrations\n  voice\\\n    models\\<model_id>\\       one model's weights";
  files.write("docs/guides/start.md", `# Start\n\n\`\`\`text\n${code}\n\`\`\`\n`);
  const diagrams = [
    {
      sourcepath: "docs/guides/start.md",
      sha256: diagramDigest(code),
      asset_slug: "app-data-folders",
      caption: "Files and their purposes.",
    },
  ];
  const site = createSite({ ...files, diagrams });
  const document = new JSDOM(site.files.get(files.output("docs/guides/start.html"))).window
    .document;
  const figure = document.querySelector(".manual-folder-map");
  assert.deepEqual(
    [...figure.querySelectorAll(".folder-name")].map((item) => item.textContent),
    ["viki-agent\\", "logs\\", "viki-agent.log", "app.sqlite3", "voice\\", "models\\<model_id>\\"],
  );
  assert.deepEqual(
    [...figure.querySelectorAll(".folder-description")].map((item) => item.textContent),
    [
      "rotating, WARNING and above, UTF-8",
      "preferences, encrypted editor drafts, workspace registrations",
      "one model's weights",
    ],
  );
  assert.equal(
    figure.querySelector(".folder-cards .folder-cards .folder-cards .folder-name").textContent,
    "viki-agent.log",
  );
  assert.equal(document.querySelectorAll("pre, .manual-folder-map img").length, 0);
});

test("provider documentation uses its HTML endpoint while GitHub Markdown pages keep their URL", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    "# Start\n\n[Errors](https://code.claude.com/docs/en/errors.md?view=full#authentication)\n\n[Skill](https://github.com/example/project/blob/main/SKILL.md#setup)\n",
  );
  const page = createSite(files).files.get(files.output("docs/guides/start.html"));
  const document = new JSDOM(page).window.document;
  const links = [...document.querySelectorAll("article a")];
  assert.equal(
    links.find((link) => link.textContent === "Errors").getAttribute("href"),
    "https://code.claude.com/docs/en/errors?view=full#authentication",
  );
  assert.equal(
    links.find((link) => link.textContent === "Skill").getAttribute("href"),
    "https://github.com/example/project/blob/main/SKILL.md#setup",
  );
  assert.equal(
    document.querySelector(".header-link").getAttribute("href"),
    "../../../index.html#library",
  );
  assert.equal(document.querySelector(".eyebrow").textContent, "VIKI Agent / Guide");
  assert.equal(document.querySelector('meta[name="color-scheme"]').content, "dark");
  assert.equal(document.querySelector('meta[name="theme-color"]').content, "#0b0b0b");
  assert.doesNotMatch(page, /Reference library|Documentation map/);
});

test("search includes section text and local video pages, with working forms and section links", (t) => {
  const files = fixture(t);
  files.write(
    "docs/index.html",
    '<!doctype html><title>Field guide</title><main><h1 id="guide">Guide</h1><a id="library" href="manual/docs/README.html">Manuals</a><a href="assets/videos/launch.html">Launch film</a><script>unsearchableScriptMarker</script></main>',
  );
  files.write(
    "docs/assets/videos/launch.html",
    '<!doctype html><title>Cinematic launch</title><main><video aria-label="VIKI Agent launch film"></video><h1>Take a look</h1><p>Work with your project.</p></main>',
  );
  files.write(
    "docs/guides/start.md",
    `# Start\n\n## Restore a copy\n\n${"Overview ".repeat(50)}A copper otter restores the safe copy.\n\n## Context & loadout\n\nChoose a source.\n`,
  );
  const site = createSite(files);
  const document = searchDocument(t, site, "  COPPER   otter  ");
  const visible = [...document.querySelectorAll(".search-result:not([hidden])")];
  assert.equal(visible.length, 1);
  assert.equal(
    visible[0].querySelector("a").getAttribute("href"),
    "docs/guides/start.html#restore-a-copy",
  );
  assert.match(visible[0].querySelector(".search-result-excerpt").textContent, /copper otter/);
  assert.equal(document.querySelector('[role="status"]').textContent, "1 result.");
  const video = searchDocument(t, site, "product launch video");
  assert.equal(
    video.querySelector(".search-result:not([hidden]) a").getAttribute("href"),
    "../assets/videos/launch.html",
  );
  assert.doesNotMatch(site.files.get(files.output("search.html")), /unsearchableScriptMarker/);
  for (const [file, html] of site.files) {
    const page = new JSDOM(html).window.document;
    const form = page.querySelector('form[role="search"]');
    const target = new URL(
      form.getAttribute("action"),
      `https://docs.example.test/${path.relative(site.output, file).split(path.sep).join("/")}`,
    );
    assert.ok(target.pathname.endsWith("/search.html"));
    assert.equal(form.querySelector('input[type="search"]').name, "q");
    assert.ok(page.querySelector('label[for="docs-query"]'));
  }
  const policy = document.querySelector('meta[http-equiv="Content-Security-Policy"]').content;
  assert.match(policy, /script-src 'self'/);
  assert.doesNotMatch(policy, /unsafe-inline|unsafe-eval/);
  assert.ok(validateSite(site).links > 20);
});

test("search ranks heading matches before body matches and handles clear, no matches and literal input", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    "# Start\n\n## Background\n\nThe copper otter appears in this paragraph.\n\n## Copper otter\n\nThe matching heading.\n",
  );
  const document = searchDocument(t, createSite(files), "copper otter");
  const links = [...document.querySelectorAll(".search-result:not([hidden]) a")];
  assert.deepEqual(
    links.map((link) => link.textContent),
    ["Copper otter", "Background"],
  );
  const input = document.querySelector('input[type="search"]');
  for (const query of ["absent-topic", '<img src=x onerror="alert(1)">', ""]) {
    input.value = query;
    input.dispatchEvent(new document.defaultView.Event("input", { bubbles: true }));
    assert.equal(document.querySelectorAll(".search-result:not([hidden])").length, 0);
    assert.match(
      document.querySelector('[role="status"]').textContent,
      query ? /No results/ : /Enter a topic/,
    );
    assert.equal(document.querySelector('img[src="x"]'), null);
  }
});

test("broad search bounds visible results and reports the full match count", (t) => {
  const files = fixture(t);
  files.write(
    "docs/guides/start.md",
    `# Start\n\n${Array.from({ length: 25 }, (_, index) => `## Common topic ${index}\n\nA shared example.\n`).join("\n")}`,
  );
  const document = searchDocument(t, createSite(files), "common");
  assert.equal(document.querySelectorAll(".search-result:not([hidden])").length, 20);
  assert.match(document.querySelector('[role="status"]').textContent, /Showing 20 of 25 results/);
  const input = document.querySelector('input[type="search"]');
  input.value = "common 24";
  input.dispatchEvent(new document.defaultView.Event("input", { bubbles: true }));
  assert.equal(document.querySelectorAll(".search-result:not([hidden])").length, 1);
  assert.equal(
    document.querySelector(".search-result:not([hidden]) a").textContent,
    "Common topic 24",
  );
});
