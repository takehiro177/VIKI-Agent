# VIKI Agent: contributor rules

VIKI Agent is a local chat UI for a VIKI vault (`<vault>`). A
Python server (Starlette/uvicorn, 127.0.0.1:8765) spawns Anthropic's **unmodified** `claude`
executable (`claude.exe` on Windows) per chat session over stream-json, maps its frames to a small
event protocol, and serves a React UI in a Chromium app window (Edge `--app` on Windows; a
Chromium-family browser's app window on macOS and Linux). The UI can also run in the Electron
app's own window (`desktop/`, ADR-053), which starts the server itself and needs none of those
browsers installed. It runs on three platforms
— Windows 11, macOS on Apple silicon, and desktop Linux with glibc 2.28 or later — on the user's
Claude subscription login by default: same CLAUDE.md, skills, hooks and permissions as the terminal,
except that a Bot session skips the vault's own instruction files (hard rule 4).
An explicitly selected model connection instead sends Claude Code's inference through the
capability-authenticated loopback gateway (`llm/gateway.py`) to the user's configured endpoint.
The gateway supports Chat Completions, Responses API and Anthropic Messages; Claude Code still
executes tools and handles permissions. The app never writes a protected vault. Apart from
bootstrap of a new or empty vault folder and backup restore into a new folder, it writes notes
only in an admitted ordinary vault through the journalled publication service (hard rule 1).

## Roles

- **You**: whoever runs the session here, a contributor and the coding agent working for them.
- **The requester**: the person who runs the session and asks for the work. Unless a rule names a
  maintainer, a go, word or approval it requires is theirs.
- **A maintainer**: whoever reviews and merges pull requests.
- **The user**: whoever uses VIKI Agent.

## Scope

Local-vault storage is in scope. Describe only what the code does: a planned capability is not an
implemented one.

- Stdlib SQLite is allowed for transactional session, command and draft state kept by VIKI Agent.
  Add a table only with the behavior that uses it. No ORM, transcript database or vector store.
- Vault bootstrap and writes, and any optional private Git synchronization, require the validated
  per-vault services and policy gates. On Windows, macOS and Linux the app bootstraps a new or
  empty vault folder, and in an admitted ordinary vault it saves single notes and renames, moves,
  archives, deletes to recovery and restores them; keep-in-vault makes a create-only copy of a
  chat attachment under `attachments/`; and the Settings instruction editor's Save replaces the
  vault's existing root `AGENTS.md` or `CLAUDE.md` in place (hard rule 1's one exception,
  ADR-082). Off Windows, only a vault on APFS (macOS) or ext4, XFS,
  Btrfs or tmpfs (Linux) accepts these writes; on any other filesystem every such request answers
  `unsupported_filesystem`. Backup restore creates a new folder outside every registered root and
  registers it read-only. The app has no Git synchronization, and no other app write reaches a
  vault. VIKI's vault services never write a protected vault (its Git metadata included) or a
  provider home (`~/.claude` or `~/.codex`). Magpie's separate agent-management features are
  governed by the bundled Magpie scope below.
- Keep the process, authentication, Claude Code permission and privacy invariants below. Nothing
  allows a provider flag, a dependency, a dynamic driver loader or an automatic retry by
  implication. A new integration needs its own compatibility evidence.
- Model connections are capped at 8 and sealed with `protection` in the existing app settings
  document (`chat.llm_connections`). Only keys entered by the user in Providers are unsealed;
  no provider credential store is read or copied. Settings and diagnostics exports omit this
  bundle and its qualified default. Portable imports reject qualified defaults. SQLite format
  38 fences these preference semantics from subscription-only readers (ADR-073).
  A connection-qualified model preference pins a chat to that connection; missing,
  unreadable or removed connections refuse rather than falling back to the subscription.
  Runtime capabilities are per Claude process and revoked when it closes. Upstream requests
  require HTTPS except on loopback, verify TLS, and follow no redirects or automatic retries.
  Only the model gateway may translate inference; it executes no tools and persists no history.
  Key renewal preserves connection identity; an active process keeps its key snapshot. Custom
  launches require checked Claude Code 2.1.281 or later and the host-managed provider environment.
  A failed or unfinished inference latches its runtime route until explicit close/reopen.
  Existing history without stored preferences needs an explicit model choice before a connection
  default may route it. Agents › Profiles chats, routine runs and inbox suggestions use native
  models, and so does a Bot whose profile pins no model connection. A Bot-bound session that is
  not a routine run (the Bot's main chat, its task threads and its turns in Bot threads) uses
  the model connection its profile pins as `runtime.model`; a routine run of that profile uses
  the native default model instead, an Agents › Profiles chat on that profile refuses to start,
  and the app's default model connection routes none of them (ADR-080).
- **Bundled Magpie:** `llm/magpie.py` supervises the pinned stock Magpie CLI and its dashboard.
  Magpie manages providers, routing groups, retries and failover behind the saved
  connection; pinning that connection does not pin its ultimate upstream provider or model.
  VIKI starts it with `MAGPIE_NO_ACCOUNTS=1`, so Magpie reads no agent's sign-in or credential
  file and serves no subscription account: no subscription bridge, failover between subscription
  accounts or window warm-up. Magpie may change agent configuration, execute its own plugins and
  MCP servers, and keep its own credentials, usage, history, caches and logs. Its dashboard
  exports use the system Save As dialog. Magpie-managed operations and exports use the account's
  filesystem rights, separate from VIKI's note-save publication gates. These are Magpie
  capabilities, separate from VIKI's sealed connection store, Claude session permission cards and
  publication service. VIKI code does not copy Magpie's credentials into its settings or add
  provider flags. Magpie's own files live under `<data>/magpie` through its XDG directories, with
  stock platform log paths where applicable.
  Launch only the checksum-verified bundled executable through `winproc`, on loopback, when
  explicitly opened or when its saved connection is used. A crash requires an explicit open;
  do not respawn it automatically. The dashboard has its own key and isolated desktop session,
  never VIKI's launch token. Build-time preparation pins source, binaries, notices and SBOM;
  VIKI's runtime downloads no Magpie executable. The bundled process disables its user-count
  telemetry and starts with self-updates off; stock dashboard settings and external
  catalog/icon requests otherwise follow Magpie's behavior. This scope is the exception to the
  provider-home, dependency, loader, storage, retry, remote-asset and process restrictions for
  Magpie's internals only. It does not expand VIKI's direct vault writes or Claude launch flags.
- Acceptance tests (`tests/acceptance/**`, `*.accept.test.*`) are frozen. If one looks wrong, say
  so in the pull request: a maintainer reviews an assertion change before the implementation it
  affects, and never weakens one merely to make a failing check pass.
- Claude Code is the user's own installation (`config._cli`: `VIKI_CLAUDE_CLI`, then in a source
  checkout the Agent SDK's copy, then `config.installed_cli`), the app does not install or modify
  it, and only the SDK's copy runs with `DISABLE_AUTOUPDATER=1` (`winproc.child_env`).

Preserve unrelated changes already in the tree; do not get a clean tree by deleting or reverting
them.

## Vault names and metadata: design constraints

How the app names and shows vault notes follows these constraints. Claim a projection, metadata
route or control only once it is implemented and tested. The read and wire contract is the one
`VaultService.metadata` (`POST /api/v2/vaults/{workspace_id}/metadata`) and
`vaults/note_metadata.py` implement and their tests pin; change it only within these constraints.

- Markdown source and canonical relative refs are authoritative. Use `(workspace_id, ref)`
  for note identity, selection, links, bookmarks and write requests; use the native catalog's
  (`profiles/native_assets.py`) `(source_id, relative_id)` for native assets. Display labels must
  not normalize, merge, rename or retarget refs or change link resolution. Every note row visibly
  exposes its exact ref, including duplicate-title, search and bookmarked rows, with accessible
  ref context.
- After supported metadata has actually been read, prefer a non-empty authored `title`, then
  `name`, then the first supported H1, then the path fallback. `name` supplies a label only.
  Preserve an authored title even when it equals `SKILL`, `README` or `index`. Before projection,
  preserve a non-generic legacy wire label. For the path fallback, ordinary files use their stem;
  generic basenames `SKILL`, `README` and `index` (case-insensitive) use `parent / filename`, or
  the filename at the root. Full refs distinguish duplicate parents and display titles.
- Display heterogeneous Markdown tolerantly. Do not impose the VIKI vault's authoring schema
  on other files, require metadata, insert titles or IDs, rename generic files, normalize tags
  or repair source for presentation. Preserve source bytes, BOM, line endings and unknown fields.
  Keep existing wire `title`, `text`, `source`, full-note hash, frontmatter/error, canonical refs
  and search/link offsets compatible; use an additive display projection, not new meanings.
- Preserve the directory scan's zero-content-read contract and current caps. First project the
  selected note from the bytes already read. List enrichment uses a separate bounded, on-demand
  prefix read with explicit metadata coverage, under the read and wire contract above. Preserve
  path/root/alias checks, private exclusions, file admission, actual byte/line budgets and handle
  lifetimes. A prefix cannot supply a full-file hash. Do not fetch every note, repeatedly call
  full-note reads to enrich rows, expand reader allowlists, add caches/indexes or dependencies.
- A display grammar is a supported subset, not general YAML validation or edit serialization.
  Unsupported, unread, truncated and failed metadata differ from absent metadata. Partial reads
  cannot establish vault-wide tag counts or filters. Render labels and metadata as text nodes;
  metadata arrival must not reorder focused rows or update another workspace, list or selection.
- Authored `tags`, `type` and `status` grant no write capability, Claude Code permission or
  memory authority. Skills use the existing native catalog's kind/source/relative ID and coverage;
  a vault `SKILL.md` is not native admission or launch evidence. Memory uses the existing service's
  effective state and exact-content approval; `status: approved` does not activate a memory.
  Keep native and memory parser contracts, allowlists, private exclusions, platform/policy gates
  and provider permissions intact. Note, native-catalog, memory and tile counts have different
  populations; label their source and coverage rather than treating them as interchangeable.
- For organization kept by VIKI Agent, reuse the existing exact-ref bookmarks. Missing or moved
  refs do not silently relink. There are no personal SQLite labels: add no table or feature for
  them before a maintainer explicitly approves a consuming API or control and a schema and
  lifecycle covering revisions, caps, forget/backup/export/reset, older binaries and moved or
  missing refs. Store no extracted metadata or Markdown content under the guise of personal labels.
- Authoring guidance applies only to writes these rules already allow. It does not expand the
  journalled publication service, ordinary-vault admission, protected-root/provider-home rules,
  platform restrictions or excluded instruction names. An instruction-file edit the user
  authorizes directly lets the app's publication service write only what hard rule 1's one
  exception allows: the existing root `AGENTS.md` or `CLAUDE.md`, on the Settings instruction
  editor's Save. Showing names and metadata allows no automatic classification, migration, source rewriting,
  metadata sidecar, storage expansion or new dependency.

## Hard rules (each one is tested once its code exists; breaking one is a review blocker)

1. **Vault writes only through bootstrap or the publication service; never protected roots
   or `~/.claude`.**
   Apart from bootstrap, which writes a welcome note and an optional `.viki/vault.json`
   (with the starter's fixed file set and its manifest) into a new or empty
   folder, and backup restore, which materialises a verified backup into a
   new folder the app creates outside every registered root and then registers it read-only, the
   app writes a vault only to an admitted ordinary vault (persistent, bootstrap complete, matching
   policy revision and root, no protected overlap), only through the journalled publication service
   (`vaults/vault_publication.py`) under the mutation lease (`vaults/vault_mutation.py`). A save,
   and every step of a rename, move, archive, delete-to-recovery
   or restore, runs only against the file's expected SHA-256, or its absence for a new name, so a
   changed file is a conflict, no step replaces an existing name, and nothing is overwritten.
   A delete copies the note's bytes into the app's private recovery store and
   verifies them before it removes the name. Besides notes, the only files it creates are the
   folders a target ref needs and, under `attachments/`, a copy of a chat attachment written
   create-only against that asset's SHA-256. Writes through it work on Windows, macOS and Linux.
   Off Windows, only a vault on APFS (macOS) or ext4, XFS, Btrfs or tmpfs (Linux) accepts them;
   elsewhere the service refuses every write with `unsupported_filesystem` before any vault
   write. A read-only, locked or another user's note refuses the write too. A replace keeps the
   note's permissions and, where the account allows it, its group, else drops group access; it
   does not keep extended attributes, Finder tags, ACLs or the creation date.
   It never writes a protected or read-only root or a provider home, and no step targets
   an excluded name at any depth (the reader's private set; `AGENTS.md`, `AGENTS.override.md`,
   `CLAUDE.md`, `CLAUDE.local.md` and `GEMINI.md` in any letter case; the stage and backup
   prefixes, reserved for the service's own temporary files) or an alias of one, but for one
   exception (ADR-082): on the user's Save in the Settings instruction editor,
   `VaultPublicationService.save_instruction` replaces in place an existing regular file named
   exactly `AGENTS.md` or `CLAUDE.md` at the root of an admitted ordinary vault, against its
   expected SHA-256 and under the same lease, journal, conflict and filesystem gates as a note
   save. It never creates either file, no rename, move, archive or delete operation targets one,
   and it never writes this way another excluded name, a deeper or differently spelled one, or a
   file in a code folder. Only `PUT /api/v2/vaults/{workspace_id}/instructions/{name}` calls it,
   and only the editor's Save calls that route; the replace passes no Claude Code permission
   prompt or hook. A new save or operation step never targets a ref whose first segment is
   `bot-memory`, in any letter case: Claude Code alone writes Bot memory (hard rule 4's fifth
   exception), and recovery still completes or rolls back a step already journalled there. Every other vault change through a
   VIKI chat happens inside `claude`, where the user's hooks and log apply.
   Magpie-managed features and exports follow the separate bundled Magpie scope. Vault handles are held
   only for one save, operation step, recovery step or bounded vault read; every other vault or
   provider-home read is open-read-close, and a text read names its `encoding=`.
   An MCP server switch the user flips asks claude.exe (`mcp_toggle`) to save that server's
   on/off for the chat's folder in its own `~/.claude.json`; the app itself writes nothing there.
2. **Every process starts in `winproc`** (`spawn`, `run`, `git`, `open_external`). ruff TID251
   bans `subprocess`, `asyncio.create_subprocess_*`, `os.system`, `os.popen` and `os.startfile`
   everywhere else in VIKI runtime code. The bundled Magpie executable owns its internal
   program launches under the Magpie scope above.
3. **git** only through `winproc.git()` (`--no-optional-locks -c core.quotepath=off`).
4. **claude argv** only from `drivers/claude/control.build_argv`. Never add a flag. The first
   exception: a single `--mcp-config=<absolute path>` element, in equals form,
   naming a file the app generated under `<data>/mcp/`, passed only (a) when a profile session's
   pinned revision lists app MCP servers and its workspace policy is `ordinary`, or (b) by
   `scripts/capture.py`'s compatibility scenarios on the requester's recorded go. The second:
   `control.login_argv`, `<cli> auth login --claudeai`, started only by `POST
   /api/v2/provider/login` on the user's Sign in click, in a console window of its own
   (`winproc.open_console`): Windows PowerShell on Windows, Terminal through a private `.command`
   file on macOS, and `xdg-terminal-exec`, else `x-terminal-emulator -e`, on Linux; never
   `--console`, `--sso` or `--email`. The third:
   `--tools=Read,Glob,Grep` and `--strict-mcp-config`, with no
   `--mcp-config`, passed only for the inbox suggestion run's session (`Launch.read_only_tools`).
   Never a JSON-string value, a relative path, a second `--mcp-config`, or `--strict-mcp-config`
   outside that session. The fourth: one `--add-dir=<absolute path>` element per entry of
   `Launch.add_dirs`, in equals form, at most 7, each a trusted (`ordinary`), persistent
   code-folder registration at its registered identity that the user ticked in Settings ›
   Workspaces and Vault (`chat.workspace_ids`), passed only to an ordinary chat that runs in the
   launch's own folder; never beside `--mcp-config` or `--tools`, and never for a profile chat, a
   bot, the inbox suggestion run or a chat with a session root (ADR-050). The one other
   `--add-dir` use is a Bot code copy: one `--add-dir=<absolute path>` element, in equals form,
   naming `Launch.code_worktree`, a worktree the app made with repository hooks off in its
   private worktree area (`code/worktrees.py` `worktree_area`) from the one trusted
   (`ordinary`), persistent code folder, at its registered identity, that the user granted that
   Bot in Bot settings (`bot_code_folders`), and bound to that Bot's chat session or to a Bot
   thread it takes part in (`bot_code_copies`). It is passed only to a Bot-bound session prepared
   in an ordinary vault that runs in the launch's own folder, beside that session's `--settings`
   element and, when its profile lists app MCP servers, its `--mcp-config`; never beside
   `Launch.add_dirs` or `--tools`, and never for a routine run, a fork, the inbox suggestion run
   or a chat with a session root. Every spawn, resume included, rechecks the grant, the folder's
   trust and registered identity and the copy's `ready` state, and refuses the launch rather
   than pass another path. `scripts/capture.py` may pass one in its synthetic scratch scenario on
   the requester's recorded go (ADR-081).
   The fifth: one `--settings=<absolute path>` element naming a private, generated hash-named
   JSON file under `<data>/bot-memory-settings/`, holding only `autoMemoryDirectory` and
   `claudeMdExcludes`. Every spawn of a Bot-bound session uses it, in an ordinary, read-only or
   protected vault; resume keeps that binding. Resolve the current registered root and Bot
   identity before every spawn. The target is `<vault>/bot-memory/<bot UUID>/` for a session
   prepared in an ordinary vault, else `<data>/bot-memory/<workspace id>/<bot UUID>/`, outside
   the vault (ADR-083); a link, junction or non-folder at any of its levels refuses the launch.
   Only Claude Code creates and writes either folder and its files, under its native
   permissions. `claudeMdExcludes` holds globs naming only that vault's own instruction files
   (`CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` and `.claude/rules/`, at its top level and in its
   subfolders), whatever the policy, so Claude Code skips them (ADR-085). No inline JSON,
   other settings keys, non-Bot chat, fork or suggestion run, and no `--add-dir` beside it except
   that session's own Bot code copy (the fourth exception). `scripts/capture.py` may exercise it
   in its synthetic scratch scenarios on the requester's recorded go: alone (ADR-074), through
   the app's model gateway with an app `--mcp-config` (ADR-080), with a Bot code copy and an
   app `--mcp-config` (ADR-081), with the memory folder outside the session's working
   folder (ADR-083), and with and without `claudeMdExcludes` in throwaway vault folders
   (ADR-085).
   Explicit model connections add no flags: the existing `--model` carries the native model ID.
   Only the gateway's process-scoped capability and loopback URL enter that Claude process's
   environment; the upstream API key remains in the server. Sessions without an explicitly
   resolved gateway route start on whatever sign-in the user's Claude Code is set up with while
   **Let Claude Code use its other sign-in methods** (`chat.other_sign_in`, read once at
   launch) is on, as it is by default: an app that runs Claude Code may not restrict its
   built-in sign-in methods. The subscription billing gate applies to them only while the user
   has turned it off or the setting cannot be read, and a gateway route still refuses on a
   billing preflight conflict. Gateway starts
   do not establish subscription-login or native-model-catalog evidence.
   Programs the bundled Magpie starts follow the bundled Magpie scope; this rule governs VIKI's
   directly managed Claude sessions.
5. **Control frames only reach the SDK.** `ClaudeTransport` yields `control_*` frames to the
   SDK's Query and sends everything else to the session queue. Never call
   `receive_messages()`; never import `claude_agent_sdk._internal`. Outbound, the transport adds
   to the SDK's `initialize` request only `perTaskStopAffordance` and, for a Bot-bound session,
   `appendSystemPrompt`: that Bot's pinned profile instructions, the memory rule and the Bot
   rules (`messaging/bot_memory_rule.py`), resolved before every spawn, resume included
   (`ProfileLaunchService.bot_system_prompt`, ADR-084, ADR-085). No other session gets a system
   prompt, and no argv element or file carries one.
6. **Permissions.** Cards offer only Allow once and Deny. The only permission
   update ever sent is `setMode`, in reply to ExitPlanMode. Never send `bypassPermissions` or
   `dontAsk`; never forward `suggestions`. The user's mode pick in a chat header is not a
   permission update: it is the SDK's `set_permission_mode` control request
   (`ClaudeControl.set_permission_mode`), only to a mode in `PERMISSION_MODES`, never for a
   profile chat.
7. **Security.** Per-launch token, kept in `sessionStorage`, never logged: on Windows it rides the
   `--app` URL's fragment; on macOS and Linux the browser's argv names only a private
   redirect page (a fresh 0600 file in the 0700 `<data>/run` folder) whose meta refresh carries the
   fragment, so on macOS and Linux no argv holds the token. The WebSocket authenticates with its
   first message within 5 s; REST (the sessions, search and rails routes) uses `Bearer`. The
   separate `/api/llm/runtime/` gateway accepts only its process-scoped credential, refuses
   requests carrying an Origin, and accepts no window token; only
   the static bundle is open. TrustedHost `127.0.0.1:8765`/`localhost:8765`;
   Origin checked before `accept()`; strict CSP. Markdown renders without raw HTML; links only
   http/https/mailto, opened through `open.url`; tool output, hook text, reasons, snippets and
   titles are always text nodes. With `--window-host=<pid>` (`__main__._window_host_pid`) the
   server instead writes each window's URL, its fragment included, only to its stdout pipe to
   that app (`window_host.WindowHost`), so on every OS no argv holds the token; that app's
   windows run with context isolation, the renderer sandbox and no Node.js, load only
   `http://127.0.0.1:8765` (`desktop/policy.mjs` `APP_ORIGIN`), open no other window, and grant
   only microphone audio, notifications and clipboard writes, to that origin only. The desktop
   window refuses to start at all, checked before the single-instance lock, when Chromium's
   sandbox is off (`sandboxOff`, `desktop/policy.mjs`): an error dialog shows instead of any
   window, telling a Linux user to install VIKI Agent's `.deb` package instead (ADR-055).
   The bundled Magpie dashboard is the separate-origin exception: its `WebContentsView` uses
   its own nonpersistent session, sandbox and context isolation, no preload or Node, and no
   access to VIKI's origin or launch token. Its trusted return header is separate from Magpie.
8. **Logging.** WARNING and above, to `<data>/logs`: `%LOCALAPPDATA%\viki-agent\logs` on
   Windows, `~/Library/Application Support/viki-agent/logs` on macOS, and
   `$XDG_DATA_HOME/viki-agent/logs` (`~/.local/share/viki-agent/logs` by default) on Linux.
   Never the token, prompt text or frame bodies.
   These logging restrictions govern VIKI-owned logs; Magpie owns its stock logs and usage
   records under the bundled Magpie scope.
9. **Fixtures** are scripted sessions or synthetic, scrubbed with
   `scripts/scrub_fixture.py`. Never copy anyone's real transcripts or vault text into the repo.

## Do-NOT list

- **Libraries:** no Tailwind, streamdown, assistant-ui, cmdk or shadcn. No state, query,
  router or virtualization libraries. No shiki, prism, mermaid, katex or rehype-raw. No FastAPI
  and no pydantic models of our own.
- **Storage:** only the stdlib SQLite store kept by VIKI Agent, as Scope allows it; no ORM, cache
  file, persisted transcript index or vector store. The one `sealing-key-id` record kept beside
  the store on macOS and Linux (the sealing key's id, never key material) is not a cache
  file, and neither are the Bot memory files Claude Code writes under `<data>/bot-memory/`
  (hard rule 4's fifth exception): VIKI Agent creates and writes none of them and reads them only
  through the Bot memory view's bounded, read-only route.
- **Dependencies:** add none and never edit `uv.lock` or `package-lock.json`; to propose one, ask
  a maintainer, who adds and locks each approved dependency. The approved exceptions:
  the stock Magpie CLI pinned by `desktop/build/magpie-release.json`, prepared only by
  `scripts/prepare_magpie.py` and distributed with its notices and Go-module SBOM;
  `pillow==13.0.0`, for isolated image workers only; importing Pillow in the server process is
  not allowed. `cryptography==50.0.1`, imported only by `protection.py`, for AES-256-GCM sealing
  on macOS and Linux. `electron` 44.9.3, pinned exactly in `desktop/package.json`, locked in
  `desktop/package-lock.json` and used only by `desktop/`. `nuitka==4.2.2` in the
  `build` dependency group, run by `scripts/compile.py` to compile the app and by
  `scripts/compiled_smoke_app.py` on Windows for its test stand-ins (ADR-054); it is never a
  runtime import. `electron-builder` 26.17.0, pinned exactly in `desktop/package.json` as a
  build-only devDependency, run only by `desktop/build/dist.mjs` to build the installers
  (ADR-055); that file's `overrides` give it minimatch 11.0.1, the first release fixing the
  advisory open against minimatch 7 to 11.0.0. `electron-updater` 6.8.10, pinned exactly in
  `desktop/package.json`, imported only by `desktop/updates.mjs`; it runs no update check
  without a configured feed file, and `build.publish` stays null until ADR-055's GO switch.
- **CLI flags:** only those in `build_argv`, plus `login_argv`. No `--append-system-prompt`,
  `--allowedTools`, `--disallowedTools`, `--strict-mcp-config`, `--max-turns`, `--bare`,
  `--setting-sources`, `--system-prompt`, `--agent`, `--agents`, and no permission-bypass
  flags. `--mcp-config` only as hard rule 4 allows; `--strict-mcp-config` only under hard rule 4's
  third exception, for the suggestion run's session only. `--add-dir` only as hard rule 4's fourth
  exception allows. `--settings` only as hard rule 4's fifth exception allows.
- **Files:** no vault writes except through the journalled publication service (and
  bootstrap or restore of a new folder), no instruction-file write but hard rule 1's one
  exception, none under `~/.claude` or a provider
  home, no reading provider credential stores (the app may unseal its own user-entered model
  keys as Scope allows), no remote assets (no Google Fonts link).
- **Product scope:** VIKI adds no telemetry or tray; Magpie's external services follow its
  bundled scope above. Electron is only the desktop app in `desktop/`
  (ADR-053); its installers are built only as `desktop/`'s electron-builder targets, and an update
  check runs only through `desktop/updates.mjs`, gated on a configured feed file that no build
  ships until GO (ADR-055). No automatic respawn of `claude` and no automatic turn retries (the
  browser WebSocket's reconnect backoff is the one exception). No driver registry or plugin
  loader. A control that is not implemented must not claim a completed operation. Voice is
  optional.

## Repository rules

- Never touch a real vault, `~/.claude` or another provider home; never read secrets or
  credentials.
- Never `git stash`, `checkout` or `reset`; `git restore` only to unstage or to undo a stray edit
  to a file that was clean when the work started. One commit per change, pushed only when the
  requester approves. A maintainer merges pull requests by hand: never `gh pr merge`.
- `.agents/`, `.claude/`, `docs/decisions/` (the ADRs) and the documentation media sources
  (`docs/assets/motion/`, the video manifest and provenance) are git-ignored and stay on the
  maintainer's disk; never force-add them. An `ADR-n` citation names one of those decision
  records; the rule beside it is complete without it.
- No real-data run, no screenshot shots (`frontend/e2e/*.shots.ts`) and no live or paid provider
  run without the requester's recorded go.
- Repo files never hold a home path, your username or a PR URL. Write `<repo>`, `<vault>`,
  `%LOCALAPPDATA%`, "PR #n". The owner's handle is allowed only where `tests/test_fixtures_clean.py`
  exempts it: the exact public repository URL in the root README, `docs/index.html` and the
  showcase bundle, the exact GitHub profile URL in the root README, the exact GitHub Sponsors URL
  in the root README, `docs/index.html` and the showcase bundle, the GitHub Pages site's front page
  and showcase URLs (each with an optional anchor) in the root README, the exact URLs of the FOSSA
  license and security badges and the FOSSA project page they link to in the root README, and the
  single rule line of `.github/CODEOWNERS` and of `.github/FUNDING.yml`. That test scans tracked
  and untracked files.
  Words that identify you beyond your account name, such as a family name or an employer, go in
  the git-ignored `.private-words.json` (`scripts/scrub_fixture.py` `PRIVATE_FILE`), never in a
  repo file.
- There is no GitHub CI (ADR-056). A pull request's evidence is the full check on Windows plus
  `uv run --directory <repo> python scripts/linux_check.py`, which runs the Linux suites in
  Docker on the working tree and, for a suite where a test still fails when rerun alone, on its
  merge base with `origin/main`; only failures main does not share count (exit 1; 2 when it cannot
  run). Add `--suite package` or `--suite compile` for an installer or compile change. No suite
  runs on macOS. For a code change under `desktop/`, a maintainer also runs
  `npm --prefix desktop run e2e` on Windows by hand, with VIKI Agent closed, before merging.
  Never push a `*-ci` branch.

## Where things are

`src/viki_agent/` (the Python server), `frontend/` (Vite + React 19 + TS strict, plain CSS),
`tests/` (`replay.py` and `fake_claude.py` are the one replay engine), `scripts/`, `desktop/`
(the Electron window host: `main.mjs`, `window.mjs`, `policy.mjs`, `server.mjs`, `updates.mjs`,
`build/dist.mjs`). Find modules by symbol under `src/viki_agent/` and `frontend/src/`.
`src/viki_agent_main.py` is the compiled app's entry point (ADR-054).

## Commands (`<repo>` is the checkout the session runs in; run from anywhere)

```powershell
uv run --directory <repo> python scripts/check.py           # everything
uv run --directory <repo> pytest -q tests/test_transport.py
npm --prefix <repo>\frontend run test    # lint | typecheck | build | e2e
```

On macOS and Linux, the same commands run repo-relative from the repository root:

```bash
uv run python scripts/check.py
uv run pytest -q tests/test_transport.py
npm --prefix frontend run test
```

The desktop window (`desktop/`, ADR-053) adds `npm --prefix <repo>\desktop run test` (also
`lint`, `smoke` and `e2e`; `e2e` starts the real app and server, so it usually runs on Linux in
Docker under xvfb) and `npm --prefix <repo>\desktop start`,
with the same POSIX forms as above. `scripts/check.py`'s frontend lane also runs desktop `lint`
and `test`.

`scripts/check.py` runs pyright three times: `--pythonplatform Windows`, then
`-p pyright-posix.json --pythonplatform Linux`, then `-p pyright-posix.json --pythonplatform
Darwin`, so every `sys.platform` branch is type-checked from any one host. No CI service runs this gate
(ADR-056): run it on Windows locally and on Linux in Docker.
Run e2e only after a fresh build, with port 8765 free. The local e2e can hit a native vitest crash
(exit 3221226505, Node 24.15.0); an unchanged rerun settles it.

`scripts/compile.py` builds a standalone folder app (ADR-054); it refuses to run against an
editable install (exit 2) and prints the non-editable command to use instead.
`scripts/compiled_smoke.py` and `scripts/compiled_smoke_app.py`, each with a `--app <binary>`
option, smoke-test the compiled workers and the whole app; the app smoke refuses to start while
`127.0.0.1:8765` already answers.

`npm --prefix <repo>\desktop run dist` (`desktop/build/dist.mjs`, ADR-055) builds VIKI
Agent's installers once `scripts/compile.py` has produced `dist/compiled/`; it refuses with no
compiled backend there or on any argument outside its build-flag allowlist (`ALLOWED_ARGS`),
always appends its own `--publish never`, signs only from a complete set of `VIKI_*` secrets in
its own environment, and strips every other signing or GitHub-token variable (any letter case)
from electron-builder's environment. Output: `dist/desktop/`.

In a Bash tool on Windows, `$'\r'` can expand to nothing, so `grep -c $'\r$'` counts every line;
check line endings with `git ls-files --eol`.

## How work runs here

For every change:
- Never edit acceptance tests (`tests/acceptance/**`, `*.accept.test.*`); add your own beside
  them. Never use `--no-verify`.
- Only the complexity the task needs: no speculative options, layers or helpers used once.
  Small functions, names that say what things are, comments that say why.
- Tests inject timers and use ≤50 ms timeouts; they never sleep, except to poll real OS state
  in process tests. A seconds-long guard on an event that normally fires in milliseconds is the
  one allowed exception (`HANG_GUARD_S`, as in `tests/profiles/test_profile_launch_api.py`); a bound on
  real work is not.
- A behaviour change updates the one doc page that owns the fact (grep the symbol) in the same
  pull request.

### Docs: lookup, not reading

- Do not read `docs/` pages or ADRs for orientation. Grep by symbol and read only that section. A
  plan or brief that names a page is the exception.
- Cite file and symbol, never a line number: `tests/test_doc_policy.py` fails on a `file.py:<line>`
  cite (a number after the colon) in the shipped docs.
- State only what a test or a measurement shows. No "never", "always" or "no longer" unless a test
  pins it.
- Release notes go in the GitHub release, one line per user-visible change, with no mechanisms
  and no test counts; the in-app **What's new** (`frontend/src/help/whats-new.md`) summarizes the
  release for users. `docs/known-issues.md`: only what a user could hit. An ADR: only a decision
  that constrains future work (a hard rule, a storage format, a protocol), in about 80 lines.
- Process records (plans, designs, progress logs, review and gate reports) are not committed. A
  plan lives in the git-ignored `docs/work/`; the PR description records the change and each
  remaining open item in one line.

## Style

Python 3.14, ruff (line length 100), pyright standard, `from __future__ import annotations`,
dataclasses rather than pydantic. TypeScript strict with Biome; React function components;
plain CSS using the tokens in `frontend/src/styles/tokens.css`. User-facing strings are English
only, with no translations: each catalog in `frontend/src/i18n/` holds one English table
(`defineCatalog`). Text in another script appears only as data the app matches or speaks, each
with its reason in `DATA` of `frontend/src/englishOnly.unit.test.ts` or
`tests/test_english_only.py`. The product, package, commands and folders are VIKI Agent
(`viki-agent`, `viki_agent` in Python); `VIKI_*` names stay, and stored values use `viki-agent`
too (the keychain service name, id namespaces, the transcript entrypoint).
`tests/test_legacy_names.py` fails on the app's former name in any tracked or untracked path or
line, with no exception. Kanban is the only name of the card folder, its skill and its settings
key; the same test fails on the former name in any tracked or untracked path or line, with no
exception.

The desktop window (`desktop/`) is plain ES modules with JSDoc, checked by the frontend's Biome
and Node's built-in test runner.
