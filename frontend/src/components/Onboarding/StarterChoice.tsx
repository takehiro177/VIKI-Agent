/** Lets the user choose the VIKI starter set or a blank folder and previews the starter files. The displayed identity follows the same renderer used by vault bootstrap. */
import type { ReactElement } from "react";
import type { ShellStore } from "../../state/shell";
import { useSnapshot } from "../../state/viki";

export type StarterTemplate = "blank" | "viki";

export interface StarterChoiceProps {
  store: ShellStore;
  template: StarterTemplate;
  onChange: (template: StarterTemplate) => void;
  disabled?: boolean;
  /** The id of an element explaining why the radios are disabled (for example a lock-reason
   * line); set as `aria-describedby` on both radios only when given. */
  describedBy?: string;
}

const WRITES_BULLETS: readonly string[] = [
  "Home.md, 50-maps/, an empty kanban/ and the folders 00-inbox to 90-archive",
  "_templates/: one template per note type",
  "system/: empty memory files, a retro folder and the log",
  ".claude/: the librarian, curator, connector, researcher and checker agents, thirteen skills, and settings with deny rules only",
  ".viki/vault.json: the vault's id, the starter version and a fingerprint of each file",
];

/** `" ".join(value.split())` — trims and folds every internal run of whitespace to one space,
 * the same as Python's whitespace-only `str.split()`/`" ".join`. */
function collapseWhitespace(value: string): string {
  return value
    .split(/\s+/)
    .filter((part) => part !== "")
    .join(" ");
}

/** Mirrors `starter_fill`'s `name` rule: collapsed, else "VIKI" outside 1-32 characters. */
function previewAssistantName(raw: unknown): string {
  const name = collapseWhitespace(typeof raw === "string" ? raw : "");
  return name.length >= 1 && name.length <= 32 ? name : "VIKI";
}

/** Mirrors `starter_fill`'s `address` rule: collapsed, else "you" outside 1-64 characters. */
function previewAddress(raw: unknown): string {
  const address = collapseWhitespace(typeof raw === "string" ? raw : "");
  return address.length >= 1 && address.length <= 64 ? address : "you";
}

/** `starter_fill`'s persona always replies in English. */
function writesSummary(name: string, address: string): string {
  const addressClause = address === "you" ? "" : `, and addresses you as ${address}`;
  return `AGENTS.md and CLAUDE.md: the persona and the vault's rules. The assistant is called ${name}${addressClause} and replies in English.`;
}

export function StarterChoice({
  store,
  template,
  onChange,
  disabled = false,
  describedBy,
}: StarterChoiceProps): ReactElement {
  const effective = useSnapshot(store).settings?.effective;
  const name = previewAssistantName(effective?.["identity.assistant_name"]);
  const address = previewAddress(effective?.["identity.display_name"]);

  return (
    <fieldset className="starter-choice">
      <legend>Start from</legend>
      <div className="starter-choice-option">
        <label>
          <input
            type="radio"
            name="starter-choice-template"
            checked={template === "viki"}
            disabled={disabled}
            {...(describedBy !== undefined ? { "aria-describedby": describedBy } : {})}
            onChange={() => onChange("viki")}
          />
          Start with VIKI (recommended)
        </label>
        <p className="starter-choice-desc">
          A ready second brain: folders, note templates, empty memory, Kanban, five agents and
          thirteen skills, with a persona in AGENTS.md you can change at any time. No hooks. Your
          team's Bots come with it: Chief, Ops, Research, Writer, Engineer, Desk and Checker.
        </p>
      </div>
      <div className="starter-choice-option">
        <label>
          <input
            type="radio"
            name="starter-choice-template"
            checked={template === "blank"}
            disabled={disabled}
            {...(describedBy !== undefined ? { "aria-describedby": describedBy } : {})}
            onChange={() => onChange("blank")}
          />
          Blank folder
        </label>
        <p className="starter-choice-desc">
          Only a welcome note. Claude Code works here with no persona, skills or agents.
        </p>
      </div>

      <div className="starter-choice-preview">
        <h3>What Start with VIKI writes</h3>
        <ul>
          <li>{writesSummary(name, address)}</li>
          {WRITES_BULLETS.map((bullet) => (
            <li key={bullet}>{bullet}</li>
          ))}
        </ul>
        <p className="starter-choice-preview-note">Names come from Settings → General.</p>
      </div>
    </fieldset>
  );
}
