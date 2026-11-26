/**
 * The vault's AGENTS.md and CLAUDE.md in full, and its Persona section as a form. In an admitted
 * ordinary vault each file has an `InstructionEditor`, whose Save is the page's only vault write.
 * The Persona form writes nothing: its proposed changes open as a chat draft for the user to
 * review and send. Other vaults show the files as read-only text.
 */
import { type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import type { StarterPlan, WorkspaceJson } from "../../clients/app/shellApi";
import { t, tf } from "../../i18n/t";
import { useReportUnsavedWork } from "../../state/unsavedWork";
import type { InstructionName, VaultApi } from "../../state/vaultApi";
import { LineDiff } from "../Diff/LineDiff";
import { Loading } from "../Loading/Loading";
import { InstructionEditor } from "./InstructionEditor";
import { type InstructionFile, InstructionFiles } from "./InstructionFiles";
import { type InstructionNote, instructionNote, visibleText, withoutBom } from "./instructionText";
import "./Persona.css";
import {
  findPersonaHeading,
  importsAgents,
  itemsFromText,
  PERSONA_CLOSE,
  PERSONA_OPEN,
  type Persona,
  PROACTIVITY,
  personaAddHandoff,
  personaConvertHandoff,
  personaHandoff,
  personaProblems,
  personaRepairHandoff,
  readPersona,
  renderPersona,
  TONES,
} from "./persona";
import { StarterUpdate } from "./StarterUpdate";

export interface PersonaPageProps {
  api: Pick<VaultApi, "note" | "saveInstruction"> | null;
  vault: WorkspaceJson | null;
  onDraft: (text: string) => boolean;
  onBack: () => void;
  /** Reads the vault's registration again, after a save was refused as made against an old one. */
  onRefreshVault?: () => void;
  /** Loads this vault's starter-files update plan; the Starter update section shows only when
   * this is given. */
  starterPlan?: (workspaceId: string) => Promise<StarterPlan>;
  assistantName?: string;
  displayName?: string;
  nextLaunch?: string;
}

type PersonaState =
  | { status: "absent" }
  | { status: "unmarked"; heading: string; section: string }
  | { status: "edited"; body: string | null }
  | { status: "ok"; persona: Persona; body: string };

type FileRead =
  | { status: "loading" }
  | ({ status: "ok" } & InstructionNote)
  | { status: "not_found" }
  | { status: "error"; code: string };

type ReadKey = {
  workspaceId: string;
  root: string;
  api: Pick<VaultApi, "note" | "saveInstruction">;
  reloadToken: number;
};
type ReadState = { key: ReadKey; agents: FileRead; claude: FileRead };

type FormState = {
  name: string;
  address: string;
  tone: string;
  language: string;
  proactivity: string;
  dos: string;
  donts: string;
};

function formFromPersona(persona: Persona): FormState {
  return {
    name: persona.name,
    address: persona.address,
    tone: persona.tone,
    language: persona.language,
    proactivity: persona.proactivity,
    dos: persona.dos.join("\n"),
    donts: persona.donts.join("\n"),
  };
}

function personaFromForm(form: FormState): Persona {
  return {
    name: form.name,
    address: form.address,
    tone: form.tone,
    language: form.language,
    proactivity: form.proactivity,
    dos: itemsFromText(form.dos),
    donts: itemsFromText(form.donts),
  };
}

function filePath(root: string, name: "AGENTS.md" | "CLAUDE.md"): string {
  return root + (/[\\/]$/.test(root) ? "" : root.includes("\\") ? "\\" : "/") + name;
}

function fileError(error: unknown): FileRead {
  const code = error instanceof Error ? error.message : "unknown";
  return code === "not_found" ? { status: "not_found" } : { status: "error", code };
}

function personaState(text: string): PersonaState {
  const result = readPersona(text);
  if (result.state === "ok") return { status: "ok", persona: result.persona, body: result.body };
  if (result.state === "edited") return { status: "edited", body: result.body };
  const heading = findPersonaHeading(text);
  return heading === null ? { status: "absent" } : { status: "unmarked", ...heading };
}

function instructionFile(
  name: InstructionName,
  root: string,
  read: FileRead,
  persona: PersonaState | null,
  editor: ReactNode,
): InstructionFile {
  const path = filePath(root, name);
  if (read.status === "not_found") {
    return {
      name,
      path,
      text: null,
      pill: t("settings.persona.files.pill.missing"),
      note: t(
        name === "AGENTS.md"
          ? "settings.persona.files.agents.missing"
          : "settings.persona.files.claude.missing",
      ),
    };
  }
  if (read.status !== "ok") {
    return {
      name,
      path,
      text: null,
      pill: t("settings.persona.files.pill.unreadable"),
      note: tf(
        name === "AGENTS.md"
          ? "settings.persona.files.agents.error"
          : "settings.persona.files.claude.error",
        { code: read.status === "error" ? read.code : "unknown" },
      ),
      tone: "error",
    };
  }
  if (name === "CLAUDE.md") {
    const linked = importsAgents(read.text);
    return {
      name,
      path,
      text: read.text,
      pill: t(
        linked ? "settings.persona.files.pill.linked" : "settings.persona.files.pill.unlinked",
      ),
      note: t(
        linked ? "settings.persona.files.claude.linked" : "settings.persona.files.claude.unlinked",
      ),
      ...(editor !== null && { editor }),
    };
  }
  const kind =
    persona?.status === "edited" && persona.body === null
      ? "marker_problem"
      : persona?.status === "ok" || persona?.status === "edited"
        ? "marked"
        : persona?.status === "unmarked"
          ? "unmarked"
          : "absent";
  return {
    name,
    path,
    text: read.text,
    pill: t(`settings.persona.files.pill.${kind}`),
    note: t(`settings.persona.files.agents.${kind}`),
    tone: kind === "marker_problem" ? "error" : "ready",
    ...(editor !== null && { editor }),
  };
}

/** The shared row shell every field uses (Settings.css's `.settings-row` contract): a label (or,
 * with no single control to point `htmlFor` at, a plain `<span>`), an optional hint, and the
 * control itself. */
function FieldRow({
  id,
  label,
  hint,
  children,
}: {
  id?: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="settings-row">
      <div className="settings-row-label">
        {id !== undefined ? <label htmlFor={id}>{label}</label> : <span>{label}</span>}
        {hint !== undefined && <p className="settings-row-desc">{hint}</p>}
      </div>
      <div className="settings-row-control">{children}</div>
    </div>
  );
}

export function PersonaPage({
  api,
  vault,
  onDraft,
  onBack,
  onRefreshVault,
  starterPlan,
  assistantName,
  displayName,
  nextLaunch,
}: PersonaPageProps) {
  const titleId = useId();
  const base = useId();
  const protectedNoticeId = `${base}-protected`;
  const noVault = vault === null || !vault.persistent;
  const workspaceId = vault?.workspace_id;
  const root = vault?.root;

  const [read, setRead] = useState<ReadState | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  // `body` is the Persona section the form was seeded from (null for the add form), so a save of
  // AGENTS.md can tell whether that section changed under the form.
  const [form, setForm] = useState<{
    source: FileRead;
    body: string | null;
    value: FormState;
  } | null>(null);
  const [draftResult, setDraftResult] = useState<{
    source: FileRead;
    value: "sent" | "no_editor";
  } | null>(null);
  const [dirtyFiles, setDirtyFiles] = useState({ agents: false, claude: false });
  // The AGENTS.md proposal the owner chose to review, for the AGENTS.md editor's `proposal` prop;
  // cleared on a fresh read (a new vault, or Read again) and once that editor saves successfully.
  const [agentsProposal, setAgentsProposal] = useState<{ text: string; sha256: string } | null>(
    null,
  );
  const anyDirty = dirtyFiles.agents || dirtyFiles.claude;
  useReportUnsavedWork("instructions", anyDirty);

  const current =
    read !== null &&
    api !== null &&
    vault !== null &&
    read.key.api === api &&
    read.key.workspaceId === vault.workspace_id &&
    read.key.root === vault.root &&
    read.key.reloadToken === reloadToken
      ? read
      : null;
  const agents = current?.agents ?? { status: "loading" as const };
  const claude = current?.claude ?? { status: "loading" as const };
  const persona = useMemo(
    () => (agents.status === "ok" ? personaState(agents.text) : null),
    [agents],
  );
  const activeForm = form?.source === agents ? form.value : null;
  const activeDraftResult = draftResult?.source === agents ? draftResult.value : null;

  useEffect(() => {
    // A new read, of another vault or by Read again, starts the form over from its file.
    setForm(null);
    setAgentsProposal(null);
    if (noVault || api === null || workspaceId === undefined || root === undefined) {
      setRead(null);
      return;
    }
    const readApi = api;
    let cancelled = false;
    const key = { workspaceId, root, api, reloadToken };
    setRead({ key, agents: { status: "loading" }, claude: { status: "loading" } });
    async function readFiles(): Promise<void> {
      let agentsRead: FileRead;
      try {
        const body = await readApi.note(key.workspaceId, "AGENTS.md");
        const ok = instructionNote(body, "AGENTS.md");
        agentsRead =
          ok === null ? { status: "error", code: "invalid_response" } : { status: "ok", ...ok };
      } catch (error) {
        agentsRead = fileError(error);
      }
      if (cancelled) return;
      setRead({ key, agents: agentsRead, claude: { status: "loading" } });
      let claudeRead: FileRead;
      try {
        const body = await readApi.note(key.workspaceId, "CLAUDE.md");
        const ok = instructionNote(body, "CLAUDE.md");
        claudeRead =
          ok === null ? { status: "error", code: "invalid_response" } : { status: "ok", ...ok };
      } catch (error) {
        claudeRead = fileError(error);
      }
      if (!cancelled) setRead({ key, agents: agentsRead, claude: claudeRead });
    }
    void readFiles();
    return () => {
      cancelled = true;
    };
  }, [api, workspaceId, root, noVault, reloadToken]);

  useEffect(() => {
    if (persona?.status === "absent") {
      // The add form has no section to compare: a save that leaves the file without one keeps it.
      setForm((prev) => (prev !== null && prev.body === null ? { ...prev, source: agents } : prev));
      return;
    }
    if (persona?.status !== "ok") return;
    const { body } = persona;
    // A save of AGENTS.md swaps in a new `agents`: keep unsent edits unless it changed the section.
    setForm((prev) =>
      prev !== null && prev.body === body
        ? { ...prev, source: agents }
        : { source: agents, body, value: formFromPersona(persona.persona) },
    );
  }, [agents, persona]);

  // The starter-update section's own key: forces its reload after an AGENTS.md save changes the
  // file's hash, on top of a vault switch or Read again (both already bump `reloadToken`).
  const starterRefreshKey = `${reloadToken}:${agents.status === "ok" ? (agents.sha256 ?? "") : ""}`;
  const protectedVault = vault !== null && vault.policy === "protected";
  const currentPersona = activeForm !== null ? personaFromForm(activeForm) : null;
  const currentBody = currentPersona !== null ? renderPersona(currentPersona) : null;
  const problems = currentPersona !== null ? personaProblems(currentPersona) : [];
  const changed = persona?.status === "ok" && currentBody !== null && currentBody !== persona.body;
  const canDraft = persona?.status === "ok" && !protectedVault && problems.length === 0 && changed;

  function updateForm(patch: Partial<FormState>): void {
    if (protectedVault) return;
    setForm((current) =>
      current === null || current.source !== agents
        ? current
        : { ...current, value: { ...current.value, ...patch } },
    );
  }

  function handleDraft(): void {
    if (!canDraft || currentBody === null) return;
    const sent = onDraft(personaHandoff(currentBody));
    setDraftResult({ source: agents, value: sent ? "sent" : "no_editor" });
  }

  function draft(text: string): void {
    if (protectedVault) return;
    setDraftResult({ source: agents, value: onDraft(text) ? "sent" : "no_editor" });
  }

  function openAdd(): void {
    if (protectedVault || persona?.status !== "absent") return;
    setForm({
      source: agents,
      body: null,
      value: {
        name: assistantName || "VIKI",
        address: displayName || "you",
        tone: "calm",
        language: "English",
        proactivity: "coach",
        dos: "",
        donts: "",
      },
    });
  }

  /** Stores the starter plan's AGENTS.md proposal for the AGENTS.md editor to pick up. */
  function reviewAgentsProposal(proposal: { proposal: string; sha256: string }): void {
    setAgentsProposal({ text: proposal.proposal, sha256: proposal.sha256 });
  }

  const describedBy = protectedVault ? protectedNoticeId : undefined;

  // The server checks all of this again; here it only decides whether to offer an editor.
  const saveInstruction = api?.saveInstruction;
  const rootPhysicalId = vault?.root_physical_id;
  const eligible =
    vault?.persistent &&
    vault.policy === "ordinary" &&
    vault.bootstrap === "complete" &&
    vault.root_available &&
    rootPhysicalId
      ? {
          workspaceId: vault.workspace_id,
          policyRevision: vault.policy_revision,
          rootPhysicalId,
        }
      : null;
  // A fresh listing can find the vault no longer takes saves. An editor that holds typed text
  // stays up with the scope it had: the server refuses its save and the text can still be copied.
  const scopeRef = useRef(eligible);
  if (eligible !== null || !anyDirty || scopeRef.current?.workspaceId !== vault?.workspace_id)
    scopeRef.current = eligible;
  const scope = scopeRef.current;
  const editorApi =
    api !== null && saveInstruction !== undefined && scope !== null
      ? { note: api.note, saveInstruction }
      : null;

  function editorFor(name: InstructionName, file: FileRead): ReactNode {
    if (editorApi === null || vault === null || scope === null) return null;
    if (file.status !== "ok" || file.source === undefined || file.sha256 === undefined) return null;
    const field = name === "AGENTS.md" ? "agents" : "claude";
    return (
      <InstructionEditor
        key={`${vault.workspace_id}:${vault.root}:${name}:${reloadToken}`}
        api={editorApi}
        scope={scope}
        name={name}
        source={file.source}
        sha256={file.sha256}
        onFile={(source, sha256) => {
          setRead((prev) =>
            prev === null || prev.key.reloadToken !== reloadToken
              ? prev
              : {
                  ...prev,
                  [field]: { status: "ok", text: withoutBom(source), source, sha256 },
                },
          );
          if (field === "agents") setAgentsProposal(null);
        }}
        onDirty={(dirty) => setDirtyFiles((prev) => ({ ...prev, [field]: dirty }))}
        {...(onRefreshVault !== undefined ? { onStaleScope: onRefreshVault } : {})}
        {...(field === "agents" ? { proposal: agentsProposal } : {})}
      />
    );
  }

  function readAgain(): void {
    if (anyDirty && !window.confirm(t("settings.persona.editor.reread_confirm"))) return;
    setReloadToken((token) => token + 1);
  }

  return (
    <section className="settings-page" aria-labelledby={titleId}>
      <h2 className="settings-page-title" id={titleId}>
        {t("settings.tab.persona")}
      </h2>

      {noVault || vault === null ? (
        <p>Open a vault to see its persona. It lives in the vault's AGENTS.md.</p>
      ) : (
        <>
          {starterPlan && (
            <StarterUpdate
              workspaceId={vault.workspace_id}
              load={starterPlan}
              refreshKey={starterRefreshKey}
              // Every vault whose editor cannot save (not just "protected") has no dedicated
              // AGENTS.md editor to route the rules change through, so it goes via the chat instead.
              protectedVault={vault.policy !== "ordinary"}
              onDraft={onDraft}
              {...(vault.policy === "ordinary" ? { onReviewAgents: reviewAgentsProposal } : {})}
            />
          )}
          <p className="settings-page-intro">
            {tf("settings.persona.intro", { vault: vault.name })}
          </p>
          {nextLaunch && (
            <>
              <p>{tf("workspaces.vault.next_launch", { name: nextLaunch })}</p>
              <p>{tf("settings.persona.running_vault", { name: vault.name })}</p>
            </>
          )}

          <div className="settings-actions">
            <button type="button" className="btn alt sm" onClick={readAgain}>
              Read again
            </button>
          </div>

          {api === null ? (
            <p>{t("settings.persona.state.unavailable")}</p>
          ) : (
            <>
              {(agents.status === "loading" || claude.status === "loading") && (
                <Loading>{t("settings.persona.state.loading")}</Loading>
              )}

              {agents.status !== "loading" && claude.status !== "loading" && (
                <>
                  {editorApi !== null && (
                    <p className="settings-note">{t("settings.persona.editor.warning")}</p>
                  )}
                  <InstructionFiles
                    files={[
                      instructionFile(
                        "AGENTS.md",
                        vault.root,
                        agents,
                        persona,
                        editorFor("AGENTS.md", agents),
                      ),
                      instructionFile(
                        "CLAUDE.md",
                        vault.root,
                        claude,
                        persona,
                        editorFor("CLAUDE.md", claude),
                      ),
                    ]}
                  />
                </>
              )}

              {agents.status === "not_found" && <p>{t("settings.persona.state.not_found")}</p>}

              {persona?.status === "absent" && (
                <>
                  <p>{t("settings.persona.state.absent")}</p>
                  {!protectedVault && activeForm === null && (
                    <div className="settings-actions">
                      <button type="button" className="btn go sm" onClick={openAdd}>
                        {t("settings.persona.draft.add")}
                      </button>
                    </div>
                  )}
                </>
              )}

              {persona?.status === "unmarked" && (
                <>
                  <p>{t("settings.persona.state.unmarked")}</p>
                  <pre className="persona-edited-body">{visibleText(persona.section).text}</pre>
                  {!protectedVault && (
                    <div className="settings-actions">
                      <button
                        type="button"
                        className="btn go sm"
                        onClick={() =>
                          draft(personaConvertHandoff(persona.heading, persona.section))
                        }
                      >
                        {t("settings.persona.draft.convert")}
                      </button>
                    </div>
                  )}
                </>
              )}

              {persona?.status === "edited" &&
                (persona.body === null ? (
                  <p>{t("settings.persona.state.marker_problem")}</p>
                ) : (
                  <>
                    <p>{t("settings.persona.state.edited")}</p>
                    <pre className="persona-edited-body">{visibleText(persona.body).text}</pre>
                  </>
                ))}
              {persona?.status === "edited" && !protectedVault && (
                <div className="settings-actions">
                  <button
                    type="button"
                    className="btn go sm"
                    onClick={() => draft(personaRepairHandoff(persona.body))}
                  >
                    {t("settings.persona.draft.repair")}
                  </button>
                </div>
              )}

              {agents.status === "error" && (
                <p className="settings-error">
                  {agents.code === "invalid_encoding" ||
                  agents.code === "unsafe_path" ||
                  agents.code === "note_too_large"
                    ? t(`settings.persona.state.${agents.code}`)
                    : tf("settings.persona.files.agents.error", { code: agents.code })}
                </p>
              )}

              {protectedVault && agents.status !== "loading" && (
                <div className="settings-readonly" id={protectedNoticeId}>
                  <p>
                    This vault is protected: the app proposes no change here. Edit AGENTS.md
                    yourself.
                  </p>
                </div>
              )}

              {(persona?.status === "ok" ||
                (persona?.status === "absent" && activeForm !== null)) &&
                activeForm !== null && (
                  <>
                    <div className="settings-card">
                      <FieldRow
                        id={`${base}-name`}
                        label="Assistant name in this vault"
                        hint="The name in AGENTS.md. The app's own label for the assistant is in Settings → General."
                      >
                        <input
                          id={`${base}-name`}
                          type="text"
                          className="settings-text"
                          value={activeForm.name}
                          aria-disabled={protectedVault}
                          aria-describedby={describedBy}
                          readOnly={protectedVault}
                          onChange={(event) => updateForm({ name: event.target.value })}
                        />
                      </FieldRow>

                      <FieldRow id={`${base}-address`} label="Address me as">
                        <input
                          id={`${base}-address`}
                          type="text"
                          className="settings-text"
                          value={activeForm.address}
                          aria-disabled={protectedVault}
                          aria-describedby={describedBy}
                          readOnly={protectedVault}
                          onChange={(event) => updateForm({ address: event.target.value })}
                        />
                      </FieldRow>

                      <FieldRow id={`${base}-tone`} label="Tone">
                        <select
                          id={`${base}-tone`}
                          className="settings-select"
                          value={activeForm.tone}
                          aria-disabled={protectedVault}
                          aria-describedby={describedBy}
                          onChange={(event) => updateForm({ tone: event.target.value })}
                        >
                          {TONES.map((tone) => (
                            <option key={tone} value={tone}>
                              {tone}
                            </option>
                          ))}
                        </select>
                      </FieldRow>

                      <FieldRow id={`${base}-language`} label="Reply language">
                        <input
                          id={`${base}-language`}
                          type="text"
                          className="settings-text"
                          value={activeForm.language}
                          aria-disabled={protectedVault}
                          aria-describedby={describedBy}
                          readOnly={protectedVault}
                          onChange={(event) => updateForm({ language: event.target.value })}
                        />
                      </FieldRow>

                      <FieldRow
                        label="Guidance in chats"
                        hint="Choose whether to offer guidance during a conversation. Proactive suggestions are set on the Decision page."
                      >
                        <div
                          className="persona-radios"
                          role="radiogroup"
                          aria-label="Guidance in chats"
                        >
                          {PROACTIVITY.map((option) => (
                            <label key={option}>
                              <input
                                type="radio"
                                name={`${base}-proactivity`}
                                checked={activeForm.proactivity === option}
                                aria-disabled={protectedVault}
                                aria-describedby={describedBy}
                                onChange={() => updateForm({ proactivity: option })}
                              />
                              {option === "coach" ? "Coach me" : "Stay quiet"}
                            </label>
                          ))}
                        </div>
                      </FieldRow>

                      <FieldRow id={`${base}-do`} label="Do (one per line)">
                        <textarea
                          id={`${base}-do`}
                          className="persona-textarea"
                          value={activeForm.dos}
                          aria-disabled={protectedVault}
                          aria-describedby={describedBy}
                          readOnly={protectedVault}
                          onChange={(event) => updateForm({ dos: event.target.value })}
                        />
                      </FieldRow>

                      <FieldRow id={`${base}-dont`} label="Don't (one per line)">
                        <textarea
                          id={`${base}-dont`}
                          className="persona-textarea"
                          value={activeForm.donts}
                          aria-disabled={protectedVault}
                          aria-describedby={describedBy}
                          readOnly={protectedVault}
                          onChange={(event) => updateForm({ donts: event.target.value })}
                        />
                      </FieldRow>
                    </div>

                    <div className="settings-card">
                      <div className="settings-card-head">
                        <h3 className="settings-card-title">
                          {persona?.status === "absent"
                            ? t("settings.persona.draft.open_add")
                            : "Review your changes"}
                        </h3>
                      </div>
                      {persona?.status === "ok" && currentBody !== null && changed ? (
                        <LineDiff before={persona.body} after={currentBody} />
                      ) : persona?.status === "absent" && currentBody !== null ? (
                        <div className="line-diff">
                          {currentBody.split("\n").map((line, index) => (
                            // biome-ignore lint/suspicious/noArrayIndexKey: preview lines can repeat, have no stable ID, and carry no component state.
                            <div className="line-diff-add" key={`${index}-${line}`}>
                              + {line}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <p className="settings-note">No change yet.</p>
                      )}

                      {problems.length > 0 && (
                        <ul className="settings-error" role="alert">
                          {problems.map((problem) => (
                            <li key={problem}>{problem}</li>
                          ))}
                        </ul>
                      )}

                      {!protectedVault && (
                        <div className="settings-actions">
                          <button
                            type="button"
                            className="btn go sm"
                            aria-disabled={
                              persona?.status === "ok" ? !canDraft : problems.length > 0
                            }
                            onClick={
                              persona?.status === "ok"
                                ? handleDraft
                                : () => {
                                    if (
                                      problems.length === 0 &&
                                      currentBody !== null &&
                                      readPersona(
                                        `${PERSONA_OPEN}\n${currentBody}\n${PERSONA_CLOSE}`,
                                      ).state === "ok"
                                    )
                                      draft(personaAddHandoff(currentBody));
                                  }
                            }
                          >
                            {persona?.status === "ok"
                              ? "Draft the change in a new chat"
                              : t("settings.persona.draft.add_send")}
                          </button>
                        </div>
                      )}
                    </div>
                  </>
                )}
              {activeDraftResult === "sent" && (
                <div role="status">
                  <p>{t("settings.persona.draft.sent")}</p>
                  <button type="button" className="btn alt sm" onClick={onBack}>
                    Go to the chat
                  </button>
                </div>
              )}
              {activeDraftResult === "no_editor" && (
                <p className="settings-error" role="alert">
                  No editor is free for the draft. Close one and try again.
                </p>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
