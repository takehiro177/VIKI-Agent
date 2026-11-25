// @vitest-environment node
import { parseAst } from "vite";
import { describe, expect, it } from "vitest";
import { catalog as botsCatalog } from "./i18n/bots";
import { catalog as settingsCatalog } from "./i18n/settings";
import { catalog as vaultCatalog } from "./i18n/vault";
import { catalog as workspacesCatalog } from "./i18n/workspaces";

const JAPANESE = /[　-〿぀-ヿ㐀-䶿一-鿿＀-￯]/;

/** String `Literal` values (never regex literals), `TemplateElement` cooked parts and trimmed
 * `JSXText` in `code` that contain a Japanese character. Comments never reach the AST, so they
 * are skipped for free. */
function japaneseStrings(code: string, filePath: string): string[] {
  const lang = filePath.endsWith(".tsx") ? "tsx" : "ts";
  const ast = parseAst(code, { lang });
  const found: string[] = [];

  const visit = (node: unknown): void => {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (const item of node) visit(item);
      return;
    }
    const record = node as Record<string, unknown>;
    if (record.type === "Literal" && typeof record.value === "string") {
      if (JAPANESE.test(record.value)) found.push(record.value);
    } else if (record.type === "TemplateElement") {
      const quasiValue = record.value as { cooked?: string | null } | undefined;
      const cooked = quasiValue?.cooked;
      if (typeof cooked === "string" && JAPANESE.test(cooked)) found.push(cooked);
    } else if (record.type === "JSXText" && typeof record.value === "string") {
      const trimmed = record.value.trim();
      if (JAPANESE.test(trimmed)) found.push(trimmed);
    }
    for (const key of Object.keys(record)) {
      if (key === "type") continue;
      const child = record[key];
      if (child && typeof child === "object") visit(child);
    }
  };

  visit(ast);
  return found;
}

const SOURCES = import.meta.glob<string>(
  [
    "./**/*.{ts,tsx}",
    "!./**/*.test.{ts,tsx}",
    "!./**/*.d.ts",
    "!./accept/**",
    "!./testing/**",
    "!./test*.ts",
  ],
  { query: "?raw", import: "default", eager: true },
);

// file -> exact string in another script -> why the app needs it. VIKI Agent's screens are
// English only, so each is text the app matches in what the user types or keeps, a sample a
// voice speaks in its own language, or a glyph; never a translation of UI text.
const DATA: Record<string, Record<string, string>> = {
  "./components/Settings/persona.ts": {
    ペルソナ: "a Persona heading the parser recognizes in a vault's AGENTS.md written in Japanese",
  },
  "./components/Composer/Composer.tsx": {
    "／": "a command prefix a Japanese keyboard types where the user meant /",
    "・": "a command prefix a Japanese keyboard types where the user meant /",
  },
  "./components/Attachments/Attachments.tsx": {
    "＋": "the add button's glyph, a full-width plus; its accessible name is English",
  },
  "./state/skillTriggers.ts": {
    "\u9055\u3046": "an owner correction the skill hint detector matches; never shown",
    "\u9055\u3044\u307e\u3059": "an owner correction the skill hint detector matches; never shown",
    "\u3061\u304c\u3046": "an owner correction the skill hint detector matches; never shown",
    "\u305d\u3046\u3058\u3083\u306a":
      "an owner correction the skill hint detector matches; never shown",
    "\u305d\u3046\u3067\u306f\u306a":
      "an owner correction the skill hint detector matches; never shown",
    "\u3044\u3084\u3001": "an owner correction the skill hint detector matches; never shown",
  },
  "./voice/packVoices.ts": {
    "\u4f60\u597d\uff0c\u8fd9\u662f\u6211\u6717\u8bfb\u56de\u590d\u65f6\u7684\u58f0\u97f3\u3002":
      "the sample a Chinese natural voice speaks for Preview; never shown",
    "\u3053\u3093\u306b\u3061\u306f\u3002\u8fd4\u4e8b\u306f\u3053\u306e\u58f0\u3067\u8aad\u307f\u4e0a\u3052\u307e\u3059\u3002":
      "the sample a Japanese natural voice speaks for Preview; never shown",
  },
};

describe("frontend English-only guard", () => {
  it("finds a planted literal, template part and JSX text; skips a comment and a regex literal", () => {
    const sample = [
      "// 日本語コメント",
      "const regexLiteral = /日本語正規表現/;",
      'const literal = "日本語リテラル";',
      // biome-ignore lint/suspicious/noTemplateCurlyInString: this is source text for the parser, not an interpolation
      "const template = `${value}日本語テンプレート`;",
      "const element = <div> 日本語ジェイエスエックス </div>;",
    ].join("\n");
    expect(japaneseStrings(sample, "./synthetic.tsx")).toEqual([
      "日本語リテラル",
      "日本語テンプレート",
      "日本語ジェイエスエックス",
    ]);
  });

  const files = Object.keys(SOURCES);

  it("scans at least 250 files, including App.tsx, main.tsx and the catalogs", () => {
    expect(files.length).toBeGreaterThanOrEqual(250);
    expect(files).toContain("./App.tsx");
    expect(files).toContain("./main.tsx");
    expect(files).toContain("./i18n/settings.ts");
  });

  it("finds no Japanese string outside DATA", () => {
    const failures: string[] = [];
    for (const file of files) {
      const allowed = DATA[file] ?? {};
      const found = japaneseStrings(SOURCES[file] ?? "", file).filter(
        (value) => !(value in allowed),
      );
      if (found.length > 0) {
        failures.push(`${file}: ${found.length} — ${found.slice(0, 3).join(", ")}`);
      }
    }
    expect(failures).toEqual([]);
  });

  it("keeps every DATA string present in its file", () => {
    for (const [file, entries] of Object.entries(DATA)) {
      const found = japaneseStrings(SOURCES[file] ?? "", file);
      for (const [value, reason] of Object.entries(entries)) {
        expect(reason.length > 0, `${file} ${value} needs a reason`).toBe(true);
        expect(found, `${file} is missing DATA string ${value}`).toContain(value);
      }
    }
  });
});

describe("avatar and code-folder trust copy", () => {
  it("pins the approved catalog wording", () => {
    expect(settingsCatalog.en).toMatchObject({
      "settings.key.identity_user_avatar_id.desc":
        "Shown next to your messages. Leave it unset to show no picture.",
      "settings.key.identity_assistant_avatar_id.desc":
        "Shown beside the assistant's replies and at the top of the right panel in ordinary chats. Bot and profile replies use their own pictures.",
    });
    expect(workspacesCatalog.en).toMatchObject({
      "workspaces.code.trusted_note":
        "Chats can start here, and a tick adds this folder to new chats.",
      "workspaces.code.trust_writes":
        "Trust covers VIKI Agent's own actions here: Code view saves, commits, Integrate, Push, worktree creation, archiving and removal, and the looks and saved checks your Decision settings allow, Bot code copies included.",
      "workspaces.code.trust_loads":
        "Chats that start here load its CLAUDE.md and skills, run its hooks and connect its MCP servers without asking.",
      "workspaces.code.trust_sandbox":
        "Trust is not a sandbox: Claude Code's permissions decide what a chat reads, edits or runs.",
    });
  });
});

describe("Bot memory copy", () => {
  it("keeps every Bot-memory label and read state", () => {
    const keys = [
      "source",
      "remaining",
      "omitted",
      "how",
      "view",
      "allFiles",
      "openInVault",
      "refresh",
      "loading",
      "empty",
      "partial",
      "error",
      "fileError",
      "metadata.absent",
      "metadata.unsupported",
      "metadata.truncated",
      "metadata.changed",
      "metadata.unreadable",
      "metadata.unknown",
      "metadata.notRead",
      "askChange",
      "askChangeHint",
      "changeRequest",
      "openVault",
      "unavailable",
      "noBinding",
    ] as const;
    for (const key of keys) {
      const name = `bots.memory.${key}` as keyof typeof botsCatalog.en;
      expect(botsCatalog.en[name], `missing ${name}`).toEqual(expect.any(String));
    }
    expect(botsCatalog.en["bots.memory.source"]).toBe("Source {ref}/");
    expect(botsCatalog.en["bots.memory.remaining"]).toBe("{count} more");
    expect(botsCatalog.en["bots.memory.view"]).toBe("View memory");
    expect(botsCatalog.en["bots.memory.allFiles"]).toBe("All files");
    expect(botsCatalog.en["bots.memory.openInVault"]).toBe("Open in Vault");
    expect(botsCatalog.en["bots.memory.refresh"]).toBe("Refresh memory");
    expect(botsCatalog.en["bots.memory.how"]).toBe("How Bot memory works");
  });

  it("states that native recall is off, main-chat drafting, and exact review or forget targets", () => {
    const catalog = botsCatalog.en;
    expect(catalog["bots.memory.help"]).toContain("autoMemoryEnabled off");
    expect(catalog["bots.memory.help"]).toContain("autoMemoryDirectory");
    expect(catalog["bots.memory.askChangeHint"]).toMatch(/unsent/);
    expect(catalog["bots.memory.reviewRequest"]).toContain("{ref}/");
    expect(catalog["bots.memory.forgetRequest"]).toContain("{ref}/");
    expect(catalog["bots.memory.reviewRequest"]).toMatch(/exact source path/);
    expect(catalog["bots.memory.forgetRequest"]).toMatch(/exact source path/);
    expect(catalog["bots.memory.reviewRequest"]).not.toMatch(/approval card|permission card/i);
    expect(catalog["bots.memory.forgetRequest"]).not.toMatch(/approval card|permission card/i);
    expect(botsCatalog.en["bots.memory.forgetRequest"]).toContain(
      "confirm the listed files before changing or deleting anything.",
    );
    expect(botsCatalog.en["bots.memory.forgetRequest"]).toContain(
      "Delete only the confirmed files, within this folder.",
    );
    expect(botsCatalog.en["bots.memory.changeRequest"].endsWith("Requested change: ")).toBe(true);
    expect(botsCatalog.en["bots.memory.openVault"]).toBe(
      "Open this Bot's vault to view its memory.",
    );
    expect(botsCatalog.en["bots.memory.noBinding"]).toContain("started before Bot memory");
    expect(botsCatalog.en["bots.memory.help"]).toContain(
      "Bots don't keep Claude Code's auto memory",
    );
    expect(botsCatalog.en["bots.memory.help"]).toContain(
      "memory records you approved, under Learning",
    );
    expect(botsCatalog.en["bots.memory.help"]).toContain("Import them on the Learning card");
    expect(botsCatalog.en["bots.memory.askChange"]).toBe("Ask {name} to change…");
    expect(botsCatalog.en["bots.memory.askChangeHint"]).toBe(
      "This opens an unsent draft in the Bot’s main chat.",
    );
    expect(botsCatalog.en["bots.memory.review"]).toBe("Ask to review");
    expect(botsCatalog.en["bots.memory.forget"]).toBe("Ask to forget all…");
    expect(vaultCatalog.en["vault.reader.bot_memory_read_only"]).toBe(
      "Bot memory is read-only in Vault. Ask the Bot to change it from its page.",
    );
  });
});
