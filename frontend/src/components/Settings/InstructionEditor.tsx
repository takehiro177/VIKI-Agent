/**
 * One instruction file (AGENTS.md or CLAUDE.md) as a plain textarea. Save is the only thing that
 * writes: typing, Discard, Undo and unmount send nothing. The file is read once, by the caller,
 * and reaches this component as `source` and `sha256`; a Save sends the draft with that hash, so a
 * file that changed on disk answers `conflict`. Then the disk version is re-read and shown as text,
 * the draft is kept, and Save stays off: only the separate Replace button saves the draft over the
 * disk version, against the disk hash. File and disk text only ever appear as a textarea value or
 * a text node. An optional `proposal` prop can also set the draft from outside, once per object
 * and only while its hash still names the file this editor holds as `base`.
 */
import { type ReactElement, useEffect, useId, useMemo, useRef, useState } from "react";
import type { Key } from "../../i18n/index";
import { t, tf } from "../../i18n/t";
import { platformKind } from "../../platform";
import {
  type InstructionName,
  type InstructionSaveOutcome,
  type VaultApi,
  VaultWriteError,
} from "../../state/vaultApi";
import { InstructionText } from "./InstructionFiles";
import {
  contentFor,
  type EditableText,
  editableText,
  hiddenLines,
  instructionNote,
  unsavableAt,
} from "./instructionText";
import "./InstructionEditor.css";

export interface InstructionEditorProps {
  api: Pick<VaultApi, "note"> & Required<Pick<VaultApi, "saveInstruction">>;
  scope: { workspaceId: string; policyRevision: number; rootPhysicalId: string };
  name: InstructionName;
  /** The file as read: BOM and line breaks included. */
  source: string;
  sha256: string;
  /**
   * The file's bytes as this editor last learned them, and their hash, for the caller's own copy:
   * what a save wrote, or what a conflict found on disk.
   */
  onFile(source: string, sha256: string): void;
  onDirty(dirty: boolean): void;
  /** A refusal that says the vault's registration moved under this page; the draft is kept. */
  onStaleScope?(): void;
  /**
   * A proposed replacement text to load into the draft, once per distinct object, matched by
   * hash against `base`. A hash that no longer matches warns and leaves the draft as it was.
   */
  proposal?: { text: string; sha256: string } | null;
}

const MAX_BYTES = 2 * 1024 * 1024;
// Enough to find each one; a file full of them does not need them all listed twice.
const HIDDEN_LINES_SHOWN = 20;
const PREFIX = "settings.persona.editor.refusal.";

const REFUSAL_KEYS = new Map<string, Key>([
  ["unsafe_target", `${PREFIX}unsafe` as Key],
  ["unsafe_parent", `${PREFIX}unsafe` as Key],
  ["excluded_path", `${PREFIX}unsafe` as Key],
  ["missing_target", `${PREFIX}missing` as Key],
  ["target_busy", `${PREFIX}busy` as Key],
  ["parent_busy", `${PREFIX}busy` as Key],
  ["stage_busy", `${PREFIX}busy` as Key],
  ["unsupported_filesystem", `${PREFIX}filesystem` as Key],
  ["workspace_changed", `${PREFIX}changed` as Key],
  ["ordinary_workspace_required", `${PREFIX}changed` as Key],
  ["stale_root", `${PREFIX}changed` as Key],
  ["root_unavailable", `${PREFIX}changed` as Key],
  ["recovery_required", `${PREFIX}recovery` as Key],
  ["publication_busy", `${PREFIX}recovery` as Key],
  // Another note's unfinished operation blocks the vault; this file's own save is not involved.
  ["vault_recovery_required", `${PREFIX}operation` as Key],
  ["vault_locked", `${PREFIX}locked` as Key],
  ["protected_instructions", `${PREFIX}protected` as Key],
  ["overloaded", `${PREFIX}overloaded` as Key],
  ["publication_limit", `${PREFIX}overloaded` as Key],
  ["publication_too_large", `${PREFIX}too_large` as Key],
  ["too_large", `${PREFIX}too_large` as Key],
]);
const STALE_SCOPE = new Set([
  "workspace_changed",
  "ordinary_workspace_required",
  "stale_root",
  "root_unavailable",
]);

function refusalKey(code: string): Key {
  if (code === "read_only_target") {
    const platform = platformKind() === "windows" ? "windows" : "posix";
    return `${PREFIX}read_only_${platform}` as Key;
  }
  return REFUSAL_KEYS.get(code) ?? (`${PREFIX}other` as Key);
}

type Base = EditableText & { sha256: string };
type Notice = { role: "status" | "alert"; text: string };

export function InstructionEditor({
  api,
  scope,
  name,
  source,
  sha256,
  onFile,
  onDirty,
  onStaleScope,
  proposal,
}: InstructionEditorProps): ReactElement {
  const [base, setBase] = useState<Base | null>(() => {
    const first = editableText(source);
    return "reason" in first ? null : { ...first, sha256 };
  });
  const [draft, setDraft] = useState(base?.text ?? "");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  // The version on disk after a conflict; `editable` is false when this editor cannot take it.
  const [disk, setDisk] = useState<{ source: string; editable: boolean } | null>(null);
  // The text from before the last save, offered back as an unsaved draft.
  const [previous, setPrevious] = useState<string | null>(null);
  const mounted = useRef(true);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const report = useRef(onDirty);
  report.current = onDirty;
  const hiddenId = useId();
  const hidden = useMemo(() => hiddenLines(draft, HIDDEN_LINES_SHOWN), [draft]);

  const dirty = base !== null && draft !== base.text;
  // While a disk version is on screen, Save is off: writing over it takes the Replace button, so
  // a second click on Save cannot replace a file the user has not looked at yet.
  const canSave = dirty && !busy && disk === null;
  const canReplace = dirty && !busy && disk?.editable === true;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    report.current(dirty);
    return () => report.current(false);
  }, [dirty]);

  // `base` and `busy` as of the moment `proposal` last changed, and `warn` itself: read through
  // refs inside the effect below, not listed as its dependencies, so a save finishing, the base
  // changing or a re-render that redefines `warn` does not re-fire it.
  const atProposal = useRef({ base, busy });
  atProposal.current = { base, busy };
  const warnRef = useRef(warn);
  warnRef.current = warn;
  const handledProposal = useRef<InstructionEditorProps["proposal"]>(undefined);
  useEffect(() => {
    if (!proposal || proposal === handledProposal.current) return;
    handledProposal.current = proposal;
    const { base: currentBase, busy: currentBusy } = atProposal.current;
    if (currentBase === null || currentBusy) return;
    if (proposal.sha256 !== currentBase.sha256) {
      warnRef.current("settings.persona.editor.proposal_stale");
      return;
    }
    setDraft(proposal.text);
  }, [proposal]);

  if (base === null) {
    const reason = editableText(source);
    const key = `settings.persona.editor.readonly.${"reason" in reason ? reason.reason : "control"}`;
    return (
      <div className="instruction-editor">
        <p className="settings-note">{tf(key as Key, { name })}</p>
        <InstructionText
          source={source}
          label={tf("settings.persona.files.full_text_label", { name })}
        />
      </div>
    );
  }

  function warn(key: Key, vars: Record<string, unknown> = {}) {
    setNotice({ role: "alert", text: tf(key, { name, ...vars }) });
  }

  function fail(code: string) {
    warn(refusalKey(code));
    if (STALE_SCOPE.has(code)) onStaleScope?.();
  }

  /** Re-read the file after a conflict: the disk version becomes the base when it is editable. */
  async function takeDiskVersion() {
    let found: ReturnType<typeof instructionNote>;
    try {
      found = instructionNote(await api.note(scope.workspaceId, name), name);
    } catch {
      found = null;
    }
    if (!mounted.current) return;
    if (found?.source === undefined || found.sha256 === undefined) {
      // The save was refused for a change on disk; say so even though that version is unreadable.
      warn("settings.persona.editor.conflict_unread");
      return;
    }
    onFile(found.source, found.sha256);
    const text = editableText(found.source);
    if ("reason" in text) {
      setDisk({ source: found.source, editable: false });
      warn("settings.persona.editor.cannot_edit");
      return;
    }
    setBase({ ...text, sha256: found.sha256 });
    setDisk({ source: found.source, editable: true });
    // The text from before an earlier save is no longer one step back from this base.
    setPrevious(null);
    warn("settings.persona.editor.conflict");
  }

  async function save(current: Base, replacing: boolean) {
    if (replacing ? !canReplace : !canSave) return;
    const sent = draft;
    const unsavable = unsavableAt(sent);
    if (unsavable !== -1) {
      warn(`${PREFIX}character` as Key, { line: sent.slice(0, unsavable).split("\n").length });
      // Such a character draws nothing, so selecting it is the only way to point at it.
      textarea.current?.focus();
      textarea.current?.setSelectionRange(unsavable, unsavable + 1);
      return;
    }
    const content = contentFor(current, sent);
    if (new TextEncoder().encode(content).length > MAX_BYTES) {
      fail("too_large");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const outcome: InstructionSaveOutcome = await api.saveInstruction(
        scope.workspaceId,
        scope.policyRevision,
        scope.rootPhysicalId,
        name,
        content,
        current.sha256,
      );
      if (!mounted.current) return;
      if (outcome.status === "conflict") {
        // A conflict that left a journal row is for recovery; any other holds nothing to wait for.
        if (outcome.cleanup_pending) fail("publication_busy");
        else await takeDiskVersion();
        return;
      }
      if (outcome.sha256 === null) throw new Error("invalid_response");
      setPrevious(outcome.status === "saved" ? current.text : null);
      setBase({ ...current, text: sent, sha256: outcome.sha256 });
      setDisk(null);
      const done = outcome.status !== "saved" ? "no_change" : replacing ? "replaced" : "saved";
      setNotice({ role: "status", text: t(`settings.persona.editor.${done}`) });
      onFile(content, outcome.sha256);
      // The Replace button unmounts with the disk version; keep the keyboard where the text is.
      if (replacing) textarea.current?.focus();
    } catch (error) {
      if (mounted.current) fail(error instanceof VaultWriteError ? error.code : "other");
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  function discard(current: Base) {
    if (!dirty || busy || disk?.editable === false) return;
    if (!window.confirm(tf("settings.persona.editor.discard_confirm", { name }))) return;
    setDraft(current.text);
    setDisk(null);
    setNotice(null);
    setPrevious(null);
  }

  function putBack() {
    if (previous === null || dirty || busy) return;
    setDraft(previous);
    setPrevious(null);
    setNotice(null);
    // The Undo button unmounts with this change; keep the keyboard where the text is.
    textarea.current?.focus();
  }

  const status =
    notice?.role === "status" ? notice.text : dirty ? t("settings.persona.editor.dirty") : "";
  return (
    <div className="instruction-editor">
      <textarea
        ref={textarea}
        className="instruction-editor-box"
        aria-label={tf("settings.persona.editor.label", { name })}
        aria-describedby={hidden.length > 0 ? hiddenId : undefined}
        spellCheck={false}
        readOnly={busy}
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          if (notice?.role === "status") setNotice(null);
        }}
      />
      <div className="instruction-editor-actions">
        <button
          type="button"
          className="btn go sm"
          aria-disabled={!canSave}
          onClick={() => {
            void save(base, false);
          }}
        >
          {tf("settings.persona.editor.save", { name })}
        </button>
        <button
          type="button"
          className="btn alt sm"
          aria-disabled={!dirty || busy || disk?.editable === false}
          onClick={() => discard(base)}
        >
          {t("settings.persona.editor.discard")}
        </button>
        {previous !== null && !dirty ? (
          <button type="button" className="btn alt sm" onClick={putBack}>
            {t("settings.persona.editor.undo")}
          </button>
        ) : null}
      </div>
      <p className="settings-note" role="status">
        {status}
      </p>
      {notice?.role === "alert" ? (
        <p className="settings-error" role="alert">
          {notice.text}
        </p>
      ) : null}
      {hidden.length > 0 ? (
        <div className="instruction-editor-hidden">
          <p className="settings-error" id={hiddenId}>
            {tf("settings.persona.editor.hidden", { count: HIDDEN_LINES_SHOWN })}
          </p>
          <InstructionText
            source={hidden.map((row) => `${row.line}: ${row.text}`).join("\n")}
            label={t("settings.persona.editor.hidden_lines")}
          />
        </div>
      ) : null}
      {disk ? (
        <div className="instruction-editor-disk">
          <p className="settings-note">{t("settings.persona.editor.disk_version")}</p>
          <InstructionText source={disk.source} label={t("settings.persona.editor.disk_version")} />
          {disk.editable ? (
            <div className="instruction-editor-actions">
              <button
                type="button"
                className="btn alt sm"
                aria-disabled={!canReplace}
                onClick={() => {
                  void save(base, true);
                }}
              >
                {t("settings.persona.editor.replace")}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
