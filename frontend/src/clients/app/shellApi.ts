/**
 * Authenticated, bounded access to the shell services: capabilities, the
 * diagnostics document and its two non-spawning checks, the settings document, workspaces and
 * vaults, data locations, exports, imports and the typed reset.
 *
 * Rules (the same ones src/clients/conversations/durableApi.ts follows, because the same hard rules apply):
 * - `Authorization: Bearer <token>` on every call and nothing else identifying. The token is a
 *   constructor argument: this module never reads storage, never puts it in a URL or a body,
 *   and never logs anything at all.
 * - Relative paths, so the page's own origin serves them; `content-type: application/json` only
 *   when there is a body.
 * - One AbortController per call on a `setTimeout` the tests' fake clock owns (`AbortSignal
 *   .timeout` runs on a timer vitest cannot advance, as api.ts already records). 15 s by
 *   default; 20 s for a check, because the server bounds a probe at 15 s and needs room to
 *   answer; 30 s for the locations document, because each measured item may spend 2 s.
 * - Every response is shape-checked. A missing or mistyped required member is `invalid_response`
 *   and the body is dropped: a view renders codes from its catalog, never a server string.
 * - Server text never reaches an Error message. `ShellApiError.message` is one fixed English
 *   sentence; the code, the per-field codes, the conflict document and the remaining error
 *   members (read key by key by the view that knows the code) are the whole record.
 * - Nothing is retried, cached or logged here. `command_id` is allocated by the caller, so a
 *   retry of the same attempt replays and a fresh attempt does not.
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** The shape a server error code may take; anything else is reported as `http_error`. */
const CODE = /^[a-z_]{1,64}$/;
const DEFAULT_TIMEOUT_MS = 15_000;
const CHECK_TIMEOUT_MS = 20_000;
const LOCATIONS_TIMEOUT_MS = 30_000;
/** The OS folder dialog waits on the user, same 15-minute ceiling the server itself applies. */
const PICK_TIMEOUT_MS = 15 * 60_000;
const MAX_NAME_CHARS = 256;
const MAX_PATH_CHARS = 4096;
const MAX_PICKER_TITLE_CHARS = 120;
/** The server enforces 256 KiB against the decoded text; this bounds what we are willing to
 * send at all, in the UTF-16 units a textarea actually counts. */
const MAX_IMPORT_CONTENT_UNITS = 262_144;
const MAX_ACKNOWLEDGEMENT_CHARS = 64;
/** `workspace_api._suggestion` refuses a query string longer than this many bytes. */
const MAX_QUERY_BYTES = 1024;

export type JsonObject = Readonly<Record<string, unknown>>;

export type CheckName = "provider_cli" | "git";

export interface CheckResult {
  check: CheckName;
  status: "ok" | "failed";
  result: { version: string };
  checked_at: string;
  duration_ms: number;
}

export interface ProviderReadiness {
  cli: "ready" | "missing" | "unusable" | "config_dir_override";
  version: string;
  login: "unknown" | "signed_in" | "signed_out";
  billing: "clear" | "blocked" | "unknown";
  last_checked_at: string | null;
  can_start: boolean;
  problems: string[];
  source: "installed" | "bundled" | "override" | "";
  version_status: "unknown" | "tested" | "older" | "newer";
  tested: [string, string];
}

export interface ActiveSummary {
  id: string;
  kind: "vault" | "scratch" | "code";
  name: string;
  policy: PolicyKind;
  layout: "legacy_para" | "unknown" | "scratch";
  source: string;
  aliased: boolean;
  root_available: boolean;
}

export interface Capabilities {
  /** `version` is served only when the launcher supplies it; rendered only then. */
  app: { mode: "normal" | "recovery"; version?: string };
  workspace: {
    active: ActiveSummary | null;
    chat_only: boolean;
    selected_id: string | null;
    selected_by: "environment" | "registration" | "builtin";
    restart_required: boolean;
    shared_scratch: boolean;
  };
  provider: { claude: ProviderReadiness };
  git: { state: "ready" | "missing" | "unknown" | "overridden"; version: string };
  storage: {
    state: "ready" | "read_only" | "unavailable";
    reason: string | null;
    private: boolean | null;
  };
  features: Readonly<Record<string, boolean>>;
  launch: { id: string };
}

export type SettingKind = "enum" | "bool" | "string" | "number";
export type Applies = "now" | "next_session" | "next_launch";
export type SettingSource = "default" | "setting" | "provider" | "session";

export interface KeySpec {
  scope: "app";
  applies: Applies;
  kind: SettingKind;
  allowed: readonly unknown[] | null;
  default: unknown;
  personal: boolean;
  /** Present only on a bounded `number` key (`allowed: null`): the inclusive [min, max] a PATCH
   * is validated against. Absent on every other key. */
  bounds?: readonly [number, number];
}

export interface SettingsView {
  version: number;
  revision: number;
  read_only: boolean;
  reason: "newer_version" | "corrupt" | null;
  values: Readonly<Record<string, unknown>> | null;
  effective: Readonly<Record<string, unknown>>;
  sources: Readonly<Record<string, SettingSource>>;
  keys: Readonly<Record<string, KeySpec>>;
}

export interface SettingsPatchResult extends SettingsView {
  command_id: string;
  replayed: boolean;
}

export type PolicyKind = "ordinary" | "existing_readonly" | "protected";

export interface WorkspaceIdentity {
  established: boolean;
  git_root_oids: number;
  inherits_from: string | null;
  probed_at: string;
}

export interface WorkspaceJson {
  root_physical_id?: string;
  workspace_id: string;
  kind: "vault" | "scratch" | "code";
  root: string;
  root_available: boolean;
  aliased: boolean;
  name: string;
  policy: PolicyKind;
  policy_revision: number;
  revision: number;
  source: string;
  layout: string;
  portable_id: string | null;
  identity: WorkspaceIdentity;
  face_dir: string | null;
  bootstrap: "none" | "pending" | "complete";
  persistent: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkspacesListing {
  workspaces: WorkspaceJson[];
  active_id: string;
  selected_id: string | null;
  selected_by: string;
  chat_only: boolean;
  scratch: { workspace_id: string; kind: "scratch"; shared: true };
}

export interface SelectResult {
  selected_id: string | null;
  active_id: string;
  restart_required: boolean;
}

export interface CancelResult {
  workspace_id: string;
  cancelled: true;
}

export interface RemoveResult {
  workspace_id: string;
  removed: true;
}

export interface VaultSuggestion {
  parent: string;
  folder: string;
  path: string;
}

/** The three reasons `GET /api/v2/folders/pick` gives for an unavailable picker; `null` covers
 * everything else (`pickerReasonText` in FolderPicker.tsx falls back to its own "unknown" row). */
export type PickerReason = "unsupported" | "no_program" | "no_display";

export interface FolderPickerStatus {
  available: boolean;
  multiple: boolean;
  reason: PickerReason | null;
}

export interface CreateVaultInput {
  name: string;
  parent?: string;
  folder?: string;
  portable_metadata?: boolean;
  template?: "blank" | "viki";
}

/** `created` is the HTTP status, not a body member: 201 a new registration, 200 an existing one. */
export interface CreateVaultResult {
  workspace: WorkspaceJson;
  created_files: string[];
  created: boolean;
}

export interface InstructionFile {
  path: string;
  bytes: number;
  sha256: string | null;
  head: string | null;
  too_large: boolean;
}

export interface PolicyInspection {
  workspace_id: string;
  policy: PolicyKind;
  policy_revision: number;
  root_available: boolean;
  aliased: boolean;
  /** claude_md, agents_md, claude_dir, hooks, git, viki_manifest. */
  markers: Readonly<Record<string, boolean>>;
  instructions: InstructionFile[];
  identity: Readonly<Record<string, unknown>>;
  overlaps: string[];
  admissible: { ordinary: boolean; protected: boolean; reason: string | null };
  excluded_from_writes: string[];
  /** instructions_present, hooks_present, git_present. */
  acknowledgements_required: string[];
}

export interface OpenVaultResult {
  workspace: WorkspaceJson;
  inspection: PolicyInspection;
  created: boolean;
}

export interface SetPolicyResult {
  policy: PolicyKind;
  policy_revision: number;
  revision: number;
  note_write: boolean;
}

export interface VaultLocation {
  workspace_id: string;
  name: string;
  path: string;
  policy: PolicyKind;
  available: boolean;
  bytes: number;
  partial: boolean;
}

export interface AppDataLocation {
  kind: "database" | "backups" | "logs" | "edge_profile" | "scratch" | "legacy_state";
  path: string;
  bytes: number;
  schema?: number;
  count?: number;
  partial?: boolean;
  in_use?: boolean;
  shared?: true;
}

export interface ProviderLocation {
  provider: string;
  kind: "home" | "credentials";
  path: string | null;
  read_by_app: boolean;
  contains?: string[];
  bytes?: number;
  partial?: boolean;
}

export type LocationCategory =
  | { id: "vaults"; items: VaultLocation[] }
  | { id: "app_data"; items: AppDataLocation[] }
  | { id: "provider"; items: ProviderLocation[] }
  | { id: "indexes"; items: [] }
  | { id: "assets"; items: { path: string; bytes: number; partial: boolean }[] };

export interface DataLocations {
  categories: LocationCategory[];
}

export type ExportKind = "diagnostics" | "settings";

export interface ExportInclude {
  paths?: boolean;
  identity?: boolean;
  log_tail?: boolean;
  settings?: boolean;
  registrations?: boolean;
}

export interface ExportFile {
  name: string;
  bytes: number;
  redactions: number;
}

export interface ExportPreview {
  preview_id: string;
  kind: ExportKind;
  files: ExportFile[];
  content: Readonly<Record<string, string>>;
  expires_at: string;
}

export interface ExportResult {
  path: string;
  bytes: number;
  sha256: string;
  command_id: string;
  replayed: boolean;
}

export interface ImportChange {
  key: string;
  current: unknown;
  incoming: unknown;
  action: "apply" | "unchanged" | "unknown_key" | "invalid_value" | "conflict";
}

export interface ImportWorkspaceRow {
  name: string;
  display_root: string;
  policy: string;
  action: string;
}

export interface ImportPreview {
  preview_id: string;
  content_hash: string;
  settings_revision: number;
  format_version: number | null;
  supported: boolean;
  /** The service has exactly one refusal to report here (src/viki_agent/app/imports.py:168); an unreadable file
   * never becomes a preview at all, it becomes an `invalid_format` error. */
  unsupported_reason: "newer_format" | null;
  provenance: Readonly<Record<string, unknown>>;
  changes: ImportChange[];
  workspaces: ImportWorkspaceRow[];
  executable_definitions: Readonly<Record<string, unknown>>[];
  duplicate_ids: string[];
}

export type ResetCategory =
  | "settings"
  | "drafts"
  | "commands"
  | "approvals"
  | "scratch"
  | "edge_profile";

export interface ResetOutcome {
  status: "done" | "partial" | "refused" | "skipped";
  reason?: string;
  count: number;
  /** Rows the store refused to delete: a `commands` reset keeps a turn this launch has
   * admitted and not finished, so the count and the status alone would overstate the result. */
  kept?: number;
}

export interface ResetResult {
  scope_of: string;
  results: Readonly<Record<string, ResetOutcome>>;
}

export type StarterAction = "add" | "replace" | "merge" | "manual" | "editor" | "remove" | "skip";

export interface StarterItem {
  path: string;
  action: StarterAction;
  diff: string | null;
  text: string | null;
}

export interface StarterPlan {
  state: "none" | "current" | "available" | "newer";
  from_version: number | null;
  to_version: number;
  notify: boolean;
  items: StarterItem[];
  agents: { proposal: string; sha256: string } | null;
}

export interface ShellApi {
  capabilities(): Promise<Capabilities>;
  /** Opaque by design: the document is a redacted tree the Diagnostics page renders as text. */
  diagnostics(): Promise<JsonObject>;
  runCheck(commandId: string, check: CheckName): Promise<CheckResult>;
  settings(): Promise<SettingsView>;
  patchSettings(
    commandId: string,
    expectedRevision: number,
    changes: Record<string, unknown>,
  ): Promise<SettingsPatchResult>;
  workspaces(): Promise<WorkspacesListing>;
  selectWorkspace(commandId: string, workspaceId: string | null): Promise<SelectResult>;
  vaultSuggestion(name: string): Promise<VaultSuggestion>;
  folderPickerStatus(): Promise<FolderPickerStatus>;
  pickFolders(multiple: boolean, title: string): Promise<string[]>;
  createVault(commandId: string, input: CreateVaultInput): Promise<CreateVaultResult>;
  openVault(commandId: string, path: string): Promise<OpenVaultResult>;
  openCodeFolder(commandId: string, path: string): Promise<OpenVaultResult>;
  cancelBootstrap(commandId: string, workspaceId: string): Promise<CancelResult>;
  removeWorkspace(commandId: string, workspaceId: string): Promise<RemoveResult>;
  policy(workspaceId: string): Promise<PolicyInspection>;
  setPolicy(
    commandId: string,
    workspaceId: string,
    expectedPolicyRevision: number,
    policy: PolicyKind,
    acknowledged: string[],
  ): Promise<SetPolicyResult>;
  dataLocations(): Promise<DataLocations>;
  exportPreview(kind: ExportKind, include: ExportInclude): Promise<ExportPreview>;
  exportWrite(commandId: string, previewId: string, destination: string): Promise<ExportResult>;
  /** `kind` is always "settings"; the server admits no other import. */
  importPreview(content: string): Promise<ImportPreview>;
  importApply(
    previewId: string,
    commandId: string,
    expectedRevision: number,
    keys: string[],
  ): Promise<SettingsPatchResult>;
  reset(commandId: string, categories: ResetCategory[]): Promise<ResetResult>;
  starterPlan?(workspaceId: string): Promise<StarterPlan>;
}

export interface ShellApiOptions {
  fetch?: typeof fetch;
}

export interface ShellApiErrorDetail {
  /** PATCH 400 `invalid_settings`: one code per refused key, rendered on that key's row. */
  fields: Readonly<Record<string, string>>;
  /** 409 `destination_not_empty`: which recovery the server suggests. */
  hint: "open_existing" | "cancel_bootstrap" | null;
  /** 409 `stale_revision`: the document as the store now holds it (the GET shape). */
  current: SettingsView | null;
  currentRevision: number | null;
  /**
   * Every other member of the error body, verbatim. The ones the backend really sends:
   * - `current_bytes` (number) — 413 `settings_too_large` (src/viki_agent/app/settings.py:378, 409)
   * - `required` (string[]) — 409 `acknowledgement_required` (workspaces.py:1130)
   * - `current_policy_revision` (number) — 409 `stale_revision` on PATCH policy
   *   (workspaces.py:1139-1141; the settings `stale_revision` uses `currentRevision` instead)
   * - `workspace_id` (string) — 409 `destination_not_empty` with hint `cancel_bootstrap`
   * - `display` (string, a path) — 409 `case_collision`
   * - `reason` (string) — 422 `unsupported_location`: `mount_point` (workspaces.py:265, 382, 428)
   *   | `cloud_sync` (workspaces.py:350) | `drive_type` (workspaces.py:384) | `no_identity`
   *   (workspaces.py:445) | `network` (workspaces.py:430)
   * - `denied` (string, a category) — 403 `denied_location` (workspaces.py:351)
   *
   * Read KEY BY KEY by the view that knows the code and rendered as a text node; never spread
   * into props or the DOM, because a member can be a path or any other string the backend
   * chose (hard rule 7).
   */
  extra: Readonly<Record<string, unknown>>;
}

/** Safe, fixed client message; server response bodies and transport exceptions stay private. */
export class ShellApiError extends Error {
  /** 0 for a timeout, a transport failure, an unreadable body or refused client input. */
  readonly status: number;
  readonly code: string;
  readonly fields: Readonly<Record<string, string>>;
  readonly hint: "open_existing" | "cancel_bootstrap" | null;
  readonly current: SettingsView | null;
  readonly currentRevision: number | null;
  readonly extra: Readonly<Record<string, unknown>>;

  constructor(status: number, code: string, detail: Partial<ShellApiErrorDetail> = {}) {
    super("The shell operation could not be completed.");
    this.name = "ShellApiError";
    this.status = status;
    this.code = code;
    this.fields = Object.freeze({ ...detail.fields });
    this.hint = detail.hint ?? null;
    this.current = detail.current ?? null;
    this.currentRevision = detail.currentRevision ?? null;
    this.extra = Object.freeze({ ...detail.extra });
  }
}

function invalidInput(): never {
  throw new ShellApiError(0, "invalid_input");
}

function invalidResponse(): never {
  throw new ShellApiError(0, "invalid_response");
}

function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalidResponse();
  return value as Record<string, unknown>;
}

function isPlain(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): value is string {
  return typeof value === "string";
}

function flag(value: unknown): value is boolean {
  return typeof value === "boolean";
}

/** A non-negative safe integer: every count, size, revision and duration the server sends. */
function count(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value);
}

function nullableText(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function textArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(text);
}

function mapOf(value: unknown, check: (item: unknown) => boolean): boolean {
  return isPlain(value) && Object.values(value).every(check);
}

function optional(value: unknown, check: (item: unknown) => boolean): boolean {
  return value === undefined || check(value);
}

function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) invalidResponse();
  return value;
}

/** Refuses a response object that carries an unexpected or missing member. */
function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return (
    Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key))
  );
}

const CLI_STATES = ["ready", "missing", "unusable", "config_dir_override"] as const;
const LOGIN_STATES = ["unknown", "signed_in", "signed_out"] as const;
const BILLING_STATES = ["clear", "blocked", "unknown"] as const;
const SOURCE_STATES = ["installed", "bundled", "override", ""] as const;
const VERSION_STATUS_STATES = ["unknown", "tested", "older", "newer"] as const;
const GIT_STATES = ["ready", "missing", "unknown", "overridden"] as const;
const STORAGE_STATES = ["ready", "read_only", "unavailable"] as const;
const SELECTED_BY = ["environment", "registration", "builtin"] as const;
const WORKSPACE_KINDS = ["vault", "scratch", "code"] as const;
const LAYOUTS = ["legacy_para", "unknown", "scratch"] as const;
const POLICIES: readonly PolicyKind[] = ["ordinary", "existing_readonly", "protected"];
const BOOTSTRAPS = ["none", "pending", "complete"] as const;
const SETTING_KINDS = ["enum", "bool", "string", "number"] as const;
const APPLIES = ["now", "next_session", "next_launch"] as const;
const SOURCES = ["default", "setting", "provider", "session"] as const;
const CHECK_NAMES: readonly CheckName[] = ["provider_cli", "git"];
const EXPORT_KINDS: readonly ExportKind[] = ["diagnostics", "settings"];
const PICKER_REASONS: readonly PickerReason[] = ["unsupported", "no_program", "no_display"];
const INCLUDE_KEYS = ["paths", "identity", "log_tail", "settings", "registrations"] as const;
const RESET_CATEGORIES: readonly ResetCategory[] = [
  "settings",
  "drafts",
  "commands",
  "approvals",
  "scratch",
  "edge_profile",
];
const RESET_STATUSES = ["done", "partial", "refused", "skipped"] as const;
const IMPORT_ACTIONS = ["apply", "unchanged", "unknown_key", "invalid_value", "conflict"] as const;
const APP_DATA_KINDS = [
  "database",
  "backups",
  "logs",
  "edge_profile",
  "scratch",
  "legacy_state",
] as const;
const STARTER_STATES = ["none", "current", "available", "newer"] as const;
const STARTER_ACTIONS: readonly StarterAction[] = [
  "add",
  "replace",
  "merge",
  "manual",
  "editor",
  "remove",
  "skip",
];
const STARTER_ITEM_KEYS = ["path", "action", "diff", "text"] as const;
const STARTER_PLAN_KEYS = [
  "state",
  "from_version",
  "to_version",
  "notify",
  "items",
  "agents",
] as const;
const STARTER_AGENTS_KEYS = ["proposal", "sha256"] as const;
const STARTER_SHA = /^[0-9a-f]{64}$/;

function parseProvider(value: unknown): void {
  const row = object(value);
  if (
    !oneOf(row.cli, CLI_STATES) ||
    !text(row.version) ||
    !oneOf(row.login, LOGIN_STATES) ||
    !oneOf(row.billing, BILLING_STATES) ||
    !nullableText(row.last_checked_at) ||
    !flag(row.can_start) ||
    !textArray(row.problems) ||
    !oneOf(row.source, SOURCE_STATES) ||
    !oneOf(row.version_status, VERSION_STATUS_STATES) ||
    !(textArray(row.tested) && row.tested.length === 2)
  )
    invalidResponse();
}

function parseActive(value: unknown): void {
  if (value === null) return;
  const row = object(value);
  if (
    !text(row.id) ||
    !oneOf(row.kind, WORKSPACE_KINDS) ||
    !text(row.name) ||
    !oneOf(row.policy, POLICIES) ||
    !oneOf(row.layout, LAYOUTS) ||
    !text(row.source) ||
    !flag(row.aliased) ||
    !flag(row.root_available)
  )
    invalidResponse();
}

function parseCapabilities(value: unknown): Capabilities {
  const row = object(value);
  const app = object(row.app);
  const workspace = object(row.workspace);
  const git = object(row.git);
  const storage = object(row.storage);
  const launch = object(row.launch);
  parseProvider(object(row.provider).claude);
  parseActive(workspace.active);
  if (
    !oneOf(app.mode, ["normal", "recovery"] as const) ||
    !optional(app.version, text) ||
    !flag(workspace.chat_only) ||
    !nullableText(workspace.selected_id) ||
    !oneOf(workspace.selected_by, SELECTED_BY) ||
    !flag(workspace.restart_required) ||
    !flag(workspace.shared_scratch) ||
    !oneOf(git.state, GIT_STATES) ||
    !text(git.version) ||
    !oneOf(storage.state, STORAGE_STATES) ||
    !nullableText(storage.reason) ||
    !(storage.private === null || flag(storage.private)) ||
    !mapOf(row.features, flag) ||
    !text(launch.id)
  )
    invalidResponse();
  return row as unknown as Capabilities;
}

function parseCheckResult(value: unknown, check: CheckName): CheckResult {
  const row = object(value);
  const result = object(row.result);
  if (
    row.check !== check ||
    !oneOf(row.status, ["ok", "failed"] as const) ||
    !text(result.version) ||
    !text(row.checked_at) ||
    !count(row.duration_ms)
  )
    invalidResponse();
  return row as unknown as CheckResult;
}

/** `KeySpec.bounds`: two safe integers with the minimum no greater than the maximum. */
function validBounds(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    Number.isSafeInteger(value[0]) &&
    Number.isSafeInteger(value[1]) &&
    (value[0] as number) <= (value[1] as number)
  );
}

function parseKeySpec(value: unknown): boolean {
  const row = object(value);
  return (
    row.scope === "app" &&
    oneOf(row.applies, APPLIES) &&
    oneOf(row.kind, SETTING_KINDS) &&
    (row.allowed === null || Array.isArray(row.allowed)) &&
    "default" in row &&
    flag(row.personal) &&
    (row.bounds === undefined ||
      (row.kind === "number" && row.allowed === null && validBounds(row.bounds)))
  );
}

function parseSettingsView(value: unknown): SettingsView {
  const row = object(value);
  if (
    !count(row.version) ||
    !count(row.revision) ||
    !flag(row.read_only) ||
    !(row.reason === null || oneOf(row.reason, ["newer_version", "corrupt"] as const)) ||
    !(row.values === null || isPlain(row.values)) ||
    !isPlain(row.effective) ||
    !mapOf(row.sources, (item) => oneOf(item, SOURCES)) ||
    !mapOf(row.keys, parseKeySpec)
  )
    invalidResponse();
  return row as unknown as SettingsView;
}

function parsePatchResult(value: unknown, commandId: string): SettingsPatchResult {
  const view = parseSettingsView(value) as unknown as Record<string, unknown>;
  if (view.command_id !== commandId || !flag(view.replayed)) invalidResponse();
  return view as unknown as SettingsPatchResult;
}

function parseWorkspace(value: unknown): WorkspaceJson {
  const row = object(value);
  const identity = object(row.identity);
  if (
    !text(row.workspace_id) ||
    !oneOf(row.kind, WORKSPACE_KINDS) ||
    !text(row.root) ||
    !flag(row.root_available) ||
    (row.root_physical_id !== undefined &&
      (typeof row.root_physical_id !== "string" ||
        !/^\d+:\d+$/.test(row.root_physical_id) ||
        row.root_physical_id.length > 80)) ||
    !flag(row.aliased) ||
    !text(row.name) ||
    !oneOf(row.policy, POLICIES) ||
    !count(row.policy_revision) ||
    !count(row.revision) ||
    !text(row.source) ||
    !text(row.layout) ||
    !nullableText(row.portable_id) ||
    !flag(identity.established) ||
    !count(identity.git_root_oids) ||
    !nullableText(identity.inherits_from) ||
    !text(identity.probed_at) ||
    !nullableText(row.face_dir) ||
    !oneOf(row.bootstrap, BOOTSTRAPS) ||
    !flag(row.persistent) ||
    !text(row.created_at) ||
    !text(row.updated_at)
  )
    invalidResponse();
  return row as unknown as WorkspaceJson;
}

function parseListing(value: unknown): WorkspacesListing {
  const row = object(value);
  const scratch = object(row.scratch);
  array(row.workspaces).forEach(parseWorkspace);
  if (
    !text(row.active_id) ||
    !nullableText(row.selected_id) ||
    !text(row.selected_by) ||
    !flag(row.chat_only) ||
    !text(scratch.workspace_id) ||
    scratch.kind !== "scratch" ||
    scratch.shared !== true
  )
    invalidResponse();
  return row as unknown as WorkspacesListing;
}

function parseInstruction(value: unknown): void {
  const row = object(value);
  if (
    !text(row.path) ||
    !count(row.bytes) ||
    !nullableText(row.sha256) ||
    !nullableText(row.head) ||
    !flag(row.too_large)
  )
    invalidResponse();
}

function parseInspection(value: unknown): PolicyInspection {
  const row = object(value);
  const admissible = object(row.admissible);
  array(row.instructions).forEach(parseInstruction);
  if (
    !text(row.workspace_id) ||
    !oneOf(row.policy, POLICIES) ||
    !count(row.policy_revision) ||
    !flag(row.root_available) ||
    !flag(row.aliased) ||
    !mapOf(row.markers, flag) ||
    !isPlain(row.identity) ||
    !textArray(row.overlaps) ||
    !flag(admissible.ordinary) ||
    !flag(admissible.protected) ||
    !nullableText(admissible.reason) ||
    !textArray(row.excluded_from_writes) ||
    !textArray(row.acknowledgements_required)
  )
    invalidResponse();
  return row as unknown as PolicyInspection;
}

function parseVaultLocation(value: unknown): void {
  const row = object(value);
  if (
    !text(row.workspace_id) ||
    !text(row.name) ||
    !text(row.path) ||
    !oneOf(row.policy, POLICIES) ||
    !flag(row.available) ||
    !count(row.bytes) ||
    !flag(row.partial)
  )
    invalidResponse();
}

function parseAppDataLocation(value: unknown): void {
  const row = object(value);
  if (
    !oneOf(row.kind, APP_DATA_KINDS) ||
    !text(row.path) ||
    !count(row.bytes) ||
    !optional(row.schema, count) ||
    !optional(row.count, count) ||
    !optional(row.partial, flag) ||
    !optional(row.in_use, flag) ||
    !optional(row.shared, (item) => item === true)
  )
    invalidResponse();
}

function parseProviderLocation(value: unknown): void {
  const row = object(value);
  if (
    !text(row.provider) ||
    !oneOf(row.kind, ["home", "credentials"] as const) ||
    !nullableText(row.path) ||
    !flag(row.read_by_app) ||
    !optional(row.contains, textArray) ||
    !optional(row.bytes, count) ||
    !optional(row.partial, flag)
  )
    invalidResponse();
}

function parseLocations(value: unknown): DataLocations {
  const row = object(value);
  for (const raw of array(row.categories)) {
    const category = object(raw);
    const items = array(category.items);
    if (category.id === "vaults") items.forEach(parseVaultLocation);
    else if (category.id === "app_data") items.forEach(parseAppDataLocation);
    else if (category.id === "provider") items.forEach(parseProviderLocation);
    else if (category.id === "assets") {
      for (const item of items) {
        const asset = object(item);
        if (!text(asset.path) || !count(asset.bytes) || !flag(asset.partial)) invalidResponse();
      }
    } else if (category.id === "indexes") {
      if (items.length > 0) invalidResponse();
    } else invalidResponse();
  }
  return row as unknown as DataLocations;
}

function parseExportPreview(value: unknown, kind: ExportKind): ExportPreview {
  const row = object(value);
  for (const raw of array(row.files)) {
    const file = object(raw);
    if (!text(file.name) || !count(file.bytes) || !count(file.redactions)) invalidResponse();
  }
  if (
    !text(row.preview_id) ||
    row.kind !== kind ||
    !mapOf(row.content, text) ||
    !text(row.expires_at)
  )
    invalidResponse();
  return row as unknown as ExportPreview;
}

function parseExportResult(value: unknown, commandId: string): ExportResult {
  const row = object(value);
  if (
    !text(row.path) ||
    !count(row.bytes) ||
    !text(row.sha256) ||
    row.command_id !== commandId ||
    !flag(row.replayed)
  )
    invalidResponse();
  return row as unknown as ExportResult;
}

function parseImportPreview(value: unknown): ImportPreview {
  const row = object(value);
  for (const raw of array(row.changes)) {
    const change = object(raw);
    if (!text(change.key) || !oneOf(change.action, IMPORT_ACTIONS)) invalidResponse();
    if (!("current" in change) || !("incoming" in change)) invalidResponse();
  }
  for (const raw of array(row.workspaces)) {
    const workspace = object(raw);
    if (
      !text(workspace.name) ||
      !text(workspace.display_root) ||
      !text(workspace.policy) ||
      !text(workspace.action)
    )
      invalidResponse();
  }
  array(row.executable_definitions).forEach(object);
  if (
    !text(row.preview_id) ||
    !text(row.content_hash) ||
    !count(row.settings_revision) ||
    !(row.format_version === null || count(row.format_version)) ||
    !flag(row.supported) ||
    !(row.unsupported_reason === null || row.unsupported_reason === "newer_format") ||
    !isPlain(row.provenance) ||
    !textArray(row.duplicate_ids)
  )
    invalidResponse();
  return row as unknown as ImportPreview;
}

function parseResetResult(value: unknown): ResetResult {
  const row = object(value);
  const results = object(row.results);
  for (const raw of Object.values(results)) {
    const outcome = object(raw);
    if (!oneOf(outcome.status, RESET_STATUSES) || !count(outcome.count)) invalidResponse();
    if (!optional(outcome.reason, text) || !optional(outcome.kept, count)) invalidResponse();
  }
  if (!text(row.scope_of)) invalidResponse();
  return row as unknown as ResetResult;
}

function parseStarterItem(value: unknown): void {
  const row = object(value);
  if (
    !exactKeys(row, STARTER_ITEM_KEYS) ||
    !boundedText(row.path, 1, 512) ||
    !oneOf(row.action, STARTER_ACTIONS) ||
    !nullableText(row.diff) ||
    !nullableText(row.text)
  )
    invalidResponse();
}

function parseStarterAgents(value: unknown): void {
  if (value === null) return;
  const row = object(value);
  if (
    !exactKeys(row, STARTER_AGENTS_KEYS) ||
    !text(row.proposal) ||
    !text(row.sha256) ||
    !STARTER_SHA.test(row.sha256)
  )
    invalidResponse();
}

function parseStarterPlan(value: unknown): StarterPlan {
  const row = object(value);
  if (!exactKeys(row, STARTER_PLAN_KEYS)) invalidResponse();
  array(row.items).forEach(parseStarterItem);
  parseStarterAgents(row.agents);
  if (
    !oneOf(row.state, STARTER_STATES) ||
    !(row.from_version === null || count(row.from_version)) ||
    !count(row.to_version) ||
    !flag(row.notify)
  )
    invalidResponse();
  return row as unknown as StarterPlan;
}

/** Members the typed fields above already carry; everything else becomes `extra`. */
const NAMED_ERROR_MEMBERS = new Set([
  "code",
  "message",
  "fields",
  "hint",
  "current",
  "current_revision",
]);

async function responseError(response: Response): Promise<ShellApiError> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return new ShellApiError(response.status, "http_error");
  }
  const error = isPlain(body) ? body.error : null;
  if (!isPlain(error)) return new ShellApiError(response.status, "http_error");
  const code = text(error.code) && CODE.test(error.code) ? error.code : "http_error";
  const extra: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(error))
    if (!NAMED_ERROR_MEMBERS.has(key)) extra[key] = value;
  let current: SettingsView | null = null;
  if (response.status === 409 && code === "stale_revision") {
    try {
      current = parseSettingsView(error.current);
    } catch {
      // A conflict whose document we cannot read is still a conflict: the caller re-reads.
      current = null;
    }
  }
  return new ShellApiError(response.status, code, {
    fields: mapOf(error.fields, text) ? (error.fields as Record<string, string>) : {},
    hint: oneOf(error.hint, ["open_existing", "cancel_bootstrap"] as const) ? error.hint : null,
    current,
    currentRevision: count(error.current_revision) ? error.current_revision : null,
    extra,
  });
}

function uuid(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}

function boundedText(value: unknown, minimum: number, maximum: number): value is string {
  return typeof value === "string" && value.length >= minimum && value.length <= maximum;
}

export function createShellApi(token: string, options: ShellApiOptions = {}): ShellApi {
  const fetchImpl = options.fetch ?? fetch;

  /** Every call's answer: the status decides `created`/`replayed`, the body is shape-checked. */
  async function call(
    path: string,
    method: "GET" | "POST" | "PATCH",
    timeoutMs: number,
    body?: string,
  ): Promise<{ status: number; body: unknown }> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new ShellApiError(0, "timeout"));
      }, timeoutMs);
    });
    const operation = (async () => {
      let response: Response;
      try {
        response = await fetchImpl(path, {
          method,
          headers: {
            authorization: `Bearer ${token}`,
            ...(body === undefined ? {} : { "content-type": "application/json" }),
          },
          ...(body === undefined ? {} : { body }),
          signal: controller.signal,
        });
      } catch {
        throw new ShellApiError(0, "network_error");
      }
      if (!response.ok) throw await responseError(response);
      if (response.status !== 200 && response.status !== 201) invalidResponse();
      try {
        return { status: response.status, body: (await response.json()) as unknown };
      } catch {
        return invalidResponse();
      }
    })();
    try {
      return await Promise.race([operation, timeout]);
    } finally {
      clearTimeout(timer);
    }
  }

  function commandId(value: string): string {
    if (!uuid(value)) invalidInput();
    return value;
  }

  function expected(value: number, minimum: number): number {
    if (!Number.isSafeInteger(value) || value < minimum) invalidInput();
    return value;
  }

  return {
    async capabilities() {
      const { body } = await call("/api/v2/capabilities", "GET", DEFAULT_TIMEOUT_MS);
      return parseCapabilities(body);
    },
    async diagnostics() {
      const { body } = await call("/api/v2/diagnostics", "GET", DEFAULT_TIMEOUT_MS);
      return object(body) as JsonObject;
    },
    async runCheck(id, check) {
      if (!oneOf(check, CHECK_NAMES)) invalidInput();
      const payload = JSON.stringify({ command_id: commandId(id), check });
      const { body } = await call("/api/v2/diagnostics/checks", "POST", CHECK_TIMEOUT_MS, payload);
      return parseCheckResult(body, check);
    },
    async settings() {
      const { body } = await call("/api/v2/settings", "GET", DEFAULT_TIMEOUT_MS);
      return parseSettingsView(body);
    },
    async patchSettings(id, expectedRevision, changes) {
      if (!isPlain(changes)) invalidInput();
      const payload = JSON.stringify({
        command_id: commandId(id),
        expected_revision: expected(expectedRevision, 0),
        changes,
      });
      const { body } = await call("/api/v2/settings", "PATCH", DEFAULT_TIMEOUT_MS, payload);
      return parsePatchResult(body, id);
    },
    async workspaces() {
      const { body } = await call("/api/v2/workspaces", "GET", DEFAULT_TIMEOUT_MS);
      return parseListing(body);
    },
    async selectWorkspace(id, workspaceId) {
      if (workspaceId !== null && !uuid(workspaceId)) invalidInput();
      const payload = JSON.stringify({
        command_id: commandId(id),
        workspace_id: workspaceId,
      });
      const { body } = await call("/api/v2/workspaces/select", "POST", DEFAULT_TIMEOUT_MS, payload);
      const row = object(body);
      if (!nullableText(row.selected_id) || !text(row.active_id) || !flag(row.restart_required))
        invalidResponse();
      return row as unknown as SelectResult;
    },
    async vaultSuggestion(name) {
      // The server accepts an empty name (it suggests the default folder), so only the ceiling
      // is a client rule; a name past it is ours to refuse before it reaches a query string.
      if (!boundedText(name, 0, MAX_NAME_CHARS)) invalidInput();
      // The server bounds the query STRING in bytes (src/viki_agent/app/workspace_api.py:288-289), not the name in
      // characters, and percent-encoding turns one CJK character into nine ASCII ones. Bounding
      // the encoded form here keeps a name that `createVault` accepts (256 characters) from
      // coming back as the server's `invalid_name`, which the form would pin to the name field.
      const encoded = encodeURIComponent(name);
      if (encoded.length + "name=".length > MAX_QUERY_BYTES) invalidInput();
      const { body } = await call(
        `/api/v2/vaults/suggestion?name=${encoded}`,
        "GET",
        DEFAULT_TIMEOUT_MS,
      );
      const row = object(body);
      if (!text(row.parent) || !text(row.folder) || !text(row.path)) invalidResponse();
      return row as unknown as VaultSuggestion;
    },
    async folderPickerStatus() {
      const { body } = await call("/api/v2/folders/pick", "GET", DEFAULT_TIMEOUT_MS);
      const row = object(body);
      if (
        !flag(row.available) ||
        !flag(row.multiple) ||
        !(row.reason === null || oneOf(row.reason, PICKER_REASONS))
      )
        invalidResponse();
      return row as unknown as FolderPickerStatus;
    },
    async pickFolders(multiple, title) {
      if (!flag(multiple) || !boundedText(title, 1, MAX_PICKER_TITLE_CHARS)) invalidInput();
      const payload = JSON.stringify({ multiple, title });
      const { body } = await call("/api/v2/folders/pick", "POST", PICK_TIMEOUT_MS, payload);
      const row = object(body);
      // An empty array is a cancelled picker, not a refusal: the caller tells the two apart by
      // length, same as a cancelled native file-save prompt elsewhere in this app.
      if (
        !Array.isArray(row.paths) ||
        !row.paths.every((item) => boundedText(item, 1, MAX_PATH_CHARS))
      )
        invalidResponse();
      return row.paths as string[];
    },
    async createVault(id, input) {
      if (!boundedText(input.name, 1, MAX_NAME_CHARS)) invalidInput();
      if (input.parent !== undefined && !boundedText(input.parent, 1, MAX_PATH_CHARS))
        invalidInput();
      if (input.folder !== undefined && !boundedText(input.folder, 1, MAX_PATH_CHARS))
        invalidInput();
      if (input.portable_metadata !== undefined && !flag(input.portable_metadata)) invalidInput();
      if (input.template !== undefined && !oneOf(input.template, ["blank", "viki"] as const))
        invalidInput();
      const payload = JSON.stringify({
        command_id: commandId(id),
        name: input.name,
        ...(input.parent === undefined ? {} : { parent: input.parent }),
        ...(input.folder === undefined ? {} : { folder: input.folder }),
        ...(input.portable_metadata === undefined
          ? {}
          : { portable_metadata: input.portable_metadata }),
        ...(input.template === undefined ? {} : { template: input.template }),
      });
      const { status, body } = await call(
        "/api/v2/vaults/create",
        "POST",
        DEFAULT_TIMEOUT_MS,
        payload,
      );
      const row = object(body);
      if (!textArray(row.created_files)) invalidResponse();
      return {
        workspace: parseWorkspace(row.workspace),
        created_files: row.created_files,
        created: status === 201,
      };
    },
    async openVault(id, path) {
      if (!boundedText(path, 1, MAX_PATH_CHARS)) invalidInput();
      const payload = JSON.stringify({ command_id: commandId(id), path });
      const { status, body } = await call(
        "/api/v2/vaults/open",
        "POST",
        DEFAULT_TIMEOUT_MS,
        payload,
      );
      const row = object(body);
      return {
        workspace: parseWorkspace(row.workspace),
        inspection: parseInspection(row.inspection),
        created: status === 201,
      };
    },
    async openCodeFolder(id, path) {
      if (!boundedText(path, 1, MAX_PATH_CHARS)) invalidInput();
      const payload = JSON.stringify({ command_id: commandId(id), path });
      const { status, body } = await call(
        "/api/v2/workspaces/code",
        "POST",
        DEFAULT_TIMEOUT_MS,
        payload,
      );
      const row = object(body);
      return {
        workspace: parseWorkspace(row.workspace),
        inspection: parseInspection(row.inspection),
        created: status === 201,
      };
    },
    async cancelBootstrap(id, workspaceId) {
      if (!uuid(workspaceId)) invalidInput();
      const payload = JSON.stringify({ command_id: commandId(id) });
      const { body } = await call(
        `/api/v2/vaults/${workspaceId}/cancel-bootstrap`,
        "POST",
        DEFAULT_TIMEOUT_MS,
        payload,
      );
      const row = object(body);
      if (row.workspace_id !== workspaceId || row.cancelled !== true) invalidResponse();
      return row as unknown as CancelResult;
    },
    async removeWorkspace(id, workspaceId) {
      if (!uuid(workspaceId)) invalidInput();
      const payload = JSON.stringify({ command_id: commandId(id) });
      const { body } = await call(
        `/api/v2/workspaces/${workspaceId}/remove`,
        "POST",
        DEFAULT_TIMEOUT_MS,
        payload,
      );
      const row = object(body);
      if (row.workspace_id !== workspaceId || row.removed !== true) invalidResponse();
      return row as unknown as RemoveResult;
    },
    async policy(workspaceId) {
      if (!uuid(workspaceId)) invalidInput();
      const { body } = await call(
        `/api/v2/vaults/${workspaceId}/policy`,
        "GET",
        DEFAULT_TIMEOUT_MS,
      );
      const inspection = parseInspection(body);
      if (inspection.workspace_id !== workspaceId) invalidResponse();
      return inspection;
    },
    async setPolicy(id, workspaceId, expectedPolicyRevision, policy, acknowledged) {
      if (!uuid(workspaceId) || !oneOf(policy, POLICIES)) invalidInput();
      if (
        !Array.isArray(acknowledged) ||
        !acknowledged.every((item) => boundedText(item, 1, MAX_ACKNOWLEDGEMENT_CHARS))
      )
        invalidInput();
      const payload = JSON.stringify({
        command_id: commandId(id),
        expected_policy_revision: expected(expectedPolicyRevision, 1),
        policy,
        acknowledged,
      });
      const { body } = await call(
        `/api/v2/vaults/${workspaceId}/policy`,
        "PATCH",
        DEFAULT_TIMEOUT_MS,
        payload,
      );
      const row = object(body);
      if (
        !oneOf(row.policy, POLICIES) ||
        !count(row.policy_revision) ||
        !count(row.revision) ||
        !flag(row.note_write)
      )
        invalidResponse();
      return row as unknown as SetPolicyResult;
    },
    async dataLocations() {
      const { body } = await call("/api/v2/data/locations", "GET", LOCATIONS_TIMEOUT_MS);
      return parseLocations(body);
    },
    async exportPreview(kind, include) {
      if (!oneOf(kind, EXPORT_KINDS) || !isPlain(include)) invalidInput();
      if (Object.keys(include).some((key) => !(INCLUDE_KEYS as readonly string[]).includes(key)))
        invalidInput();
      if (Object.values(include).some((value) => !flag(value))) invalidInput();
      const payload = JSON.stringify({ kind, include });
      const { body } = await call("/api/v2/exports/preview", "POST", DEFAULT_TIMEOUT_MS, payload);
      return parseExportPreview(body, kind);
    },
    async exportWrite(id, previewId, destination) {
      if (!uuid(previewId) || !boundedText(destination, 1, MAX_PATH_CHARS)) invalidInput();
      const payload = JSON.stringify({
        command_id: commandId(id),
        preview_id: previewId,
        destination,
      });
      const { body } = await call("/api/v2/exports", "POST", DEFAULT_TIMEOUT_MS, payload);
      return parseExportResult(body, id);
    },
    async importPreview(content) {
      if (!boundedText(content, 0, MAX_IMPORT_CONTENT_UNITS)) invalidInput();
      const payload = JSON.stringify({ kind: "settings", content });
      const { body } = await call("/api/v2/imports/preview", "POST", DEFAULT_TIMEOUT_MS, payload);
      return parseImportPreview(body);
    },
    async importApply(previewId, id, expectedRevision, keys) {
      if (!uuid(previewId) || !textArray(keys)) invalidInput();
      const payload = JSON.stringify({
        command_id: commandId(id),
        expected_revision: expected(expectedRevision, 0),
        keys,
      });
      const { body } = await call(
        `/api/v2/imports/${previewId}/apply`,
        "POST",
        DEFAULT_TIMEOUT_MS,
        payload,
      );
      return parsePatchResult(body, id);
    },
    async reset(id, categories) {
      if (
        !Array.isArray(categories) ||
        categories.length === 0 ||
        new Set(categories).size !== categories.length ||
        !categories.every((category) => oneOf(category, RESET_CATEGORIES))
      )
        invalidInput();
      const payload = JSON.stringify({
        command_id: commandId(id),
        categories,
        confirmation: "RESET",
      });
      const { body } = await call("/api/v2/data/reset", "POST", DEFAULT_TIMEOUT_MS, payload);
      return parseResetResult(body);
    },
    async starterPlan(workspaceId) {
      if (!uuid(workspaceId)) invalidInput();
      const { body } = await call(
        `/api/v2/vaults/${workspaceId}/starter`,
        "GET",
        DEFAULT_TIMEOUT_MS,
      );
      return parseStarterPlan(body);
    },
  };
}
