# Security and privacy

VIKI Agent serves its window from a local loopback address. By default, it starts the installed
Claude Code CLI on the sign-in Claude Code is set up with, such as your Claude plan or an API
key. An explicit custom model connection uses VIKI's local gateway to send inference to the
endpoint you choose.

> [!IMPORTANT]
> Claude Code can send prompts, tool results and other conversation data to its provider as part
> of a chat. Review what you send and which files you let its tools read.

VIKI Agent is independent and is not affiliated with or endorsed by Anthropic.
**Settings › Diagnostics and About** and the **Your data and privacy** topic in **Help** list
where your data goes. The sections below give the details.

## Claude sign-in and plan usage

**Sign in** opens Claude Code's own `claude auth login` in a terminal window. The sign-in
completes in Claude Code's flow. VIKI does not open Claude Code's credential file and keeps no
copy of your sign-in. Its billing check reads Claude Code's settings files and keeps only the
names of settings that would bill another account.

Chats VIKI starts on its own, routines and Bot threads run Claude Code on your sign-in the same
way a chat you start does, so they count against your plan or bill the account your Claude
Code uses. Anthropic's
[legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance) says the
advertised usage limits for Pro, Max and Ultra plans "assume ordinary, individual usage of
Claude Code and the Agent SDK". Anthropic sets how a Claude plan may be used with other
applications and can change it; an API key, a cloud provider or your own model connection is
billed by usage instead of drawing on plan limits.

To keep chats on your Claude subscription, turn off **Let Claude Code use its other sign-in
methods** in **Settings › Chat**: a chat then does not start when Claude Code is set up to bill
another account. See [Configuration](configuration.md#claude-code-environment).

## Custom model connections

Under **Settings › Providers › Chat models**, add a Magpie model or routing group, or configure
an endpoint under **Advanced — Direct connections**. Magpie holds its providers' API keys.
For a Magpie model, VIKI saves the model choice and any **Gateway access key** you enter;
for a direct connection, VIKI saves the endpoint and its API key.

- **Storage:** saved connection details and keys are encrypted in VIKI's app storage. Settings
  and diagnostics exports omit them.
- **Other apps:** VIKI's connection form uses the key you enter. VIKI does not open other
  apps' credential files; Magpie's own behavior is described below.
- **Removing a connection:** removes its saved details and key. Conversation history remains
  with Claude Code. Removing the default returns new chats to Claude Code's own sign-in.
  Removing a connection also prevents its chats from continuing after their
  process closes.
- **Updating a key:** the connection stays the same connection, so chats and a default model that
  use it keep using it. Open chats keep using their captured key until you close and reopen them.
- **Portable exports and imports:** exports omit keys and a default model that names a saved
  connection. Imports reject such a default, because the connection belongs to this computer.

For a custom connection, Claude Code receives a temporary credential for VIKI's loopback gateway.
Anthropic [does not support](https://code.claude.com/docs/en/llm-gateway) routing Claude Code to
non-Claude models through any gateway.

- **Access:** the gateway accepts only that Claude Code process's credential and refuses
  browser-origin requests. The window's launch token grants no access to it.
- **Endpoint key:** the server keeps it and sends requests to the configured endpoint over HTTPS,
  or HTTP for a server on this computer.
- **TLS and redirects:** the gateway verifies TLS and refuses redirects.
- **Tools:** the gateway translates model requests and replies. Claude Code executes the tools
  under its own permissions and hooks.
- **Connection failures:** unsupported requests and incomplete replies fail at the selected
  connection. A gateway such as Magpie can route or retry requests behind that connection.
- **Local inference:** requires a local model server that you run.
- **After a failure:** a failed or interrupted inference blocks further upstream requests from
  that process, including Claude Code's own retries. Close and reopen the chat after fixing the
  connection to try again.

Listing connections and saving one make no inference request.

- **List models:** queries the endpoint's model list.
- **Test connection:** sends a fixed short prompt, which the provider may charge for. It sends no
  conversation or vault content.
- **Default model:** the chosen default applies to new chats, including chats started by
  automation. Changing the default does not retarget saved chats.
- **Profiles, Bots and inbox suggestions:** these use native Claude models, except that a Bot's
  profile may pin a saved connection for its main chat, task threads and Bot-thread turns. Its
  routine runs use the native default, and a plain Agents › Profiles chat on it is refused.
  The endpoint can receive the whole conversation, including prompts, attachments and earlier
  replies, plus the Bot's profile instructions, memory rule and Bot rules, recalled memory records, the
  instruction files and skills Claude Code loads, tool results, and code or files a chat reads.
- **Existing transcripts:** an existing transcript without a saved VIKI model choice requires an
  explicit choice before using a connection default.
- **Model review:** if the chosen model has changed or cannot be confirmed when a chat opens, a
  notice asks you to review the model. Your draft stays unsent until you select **Reopen chat**,
  review the model and select **Send** again.

Set up models and connections in **Settings › Providers › Chat models**.

## The viki-agent command in chats

A chat you opened in the VIKI Agent window can run its own `viki-agent` command to read and
change settings, routines and Bots.

- **Who gets a key:** VIKI Agent issues a new credential to each ordinary chat's Claude process,
  including on resume, and revokes it when that process closes. A profile chat, a Bot's chat or
  task thread, and a routine run never get one, reopened or not. A chat VIKI starts and runs on
  its own — a Decision mission, an inbox suggestion run, a messaging bridge's conversation — gets
  none of its own either, but reopening its transcript from the window gets it a key like any
  chat you opened there.
- **The three variables:** `VIKI_AGENT_URL`, `VIKI_AGENT_KEY` and `VIKI_AGENT_CLI`, set only in
  that process's own environment. Nothing logs the key, even by accident.
- **Loopback only:** the command reaches only `127.0.0.1`, on this computer.
- **Access:** the surface accepts only that chat process's own key, refuses a request carrying
  an Origin, and accepts no window token.
- **Credentials stay out of reach:** a request to read or change a credential setting is
  refused, and the surface leaves it out of every reply.
- **Starter updates:** `status` also reports each vault's starter update state: its versions,
  whether an update is available and how many files each kind of change touches, never their
  contents.
- **Apply:** changing how the window looks, its layout, the code editor, a Bot's color or icon,
  or read-aloud speed and voice takes effect at once. Every other setting, and turning on a
  routine, waits for your **Apply** in the window; that Apply route accepts only the window's own
  token, never a chat's. **Reject**, or leaving it, changes nothing.
- **What Apply shows:** the whole change — a setting's current and new value (for a reset, the
  value it discards), a routine's schedule, profile, workspace and whole prompt — each long value
  in its own scrolling box, with any hidden or text-reordering character written out as its code,
  for example `<U+202E>`. A setting value longer than `agent_api.py`'s `REVIEW_CHARS` is refused
  rather than held cut short.
- **Routines and Bots:** a routine run, a test run included, gets its profile's MCP servers, its
  pinned permission mode (a test run uses the pin) and, for a Bot's profile, that Bot's identity
  and recall. So a chat adds or tests a routine only for a profile that has no MCP servers, no Bot
  in any vault, and no pinned permission mode that skips asking (**Accept edits** or **Auto**;
  **Default**, **Manual** and **Plan** are allowed, and a profile with no pin runs in the
  **Default mode** setting, as a new chat does); it tests only a routine that same chat added
  since VIKI Agent last started (a resumed chat keeps its own), and creates a Bot only for a
  profile that runs no routine. Anything else is refused with a fix that sends the chat to you
  (`agent_api.py`'s `plain_profile`, `add_routine`, `run_routine` and `create_bot`). The
  Routines and Agents screens themselves are unchanged.
- **If it doesn't answer:** a command that times out exits with code 4, and its JSON error code
  `no_reply` means the request may already have gone through; its own guidance checks `status`
  before running anything again rather than assuming nothing happened.
- **Limit:** these levels, and the Apply route above, guard only this one command's surface.
  Claude Code's own permission rules still decide what every other command in that chat may do.

## Magpie routing and management

**Open Magpie** starts the bundled Magpie process and its dashboard. Magpie manages its own
providers, routing groups, plugins and agent configuration. It can change agent settings and
run plugins and MCP servers. Those actions use Magpie's controls and your account's
system access; VIKI's chat permission cards govern Claude Code's chat tool requests.

VIKI starts its bundled Magpie with Magpie's subscription accounts setting off
(`MAGPIE_NO_ACCOUNTS=1`). [Magpie's documentation](https://github.com/yetone/magpie/blob/8544d5fdb51bea24dece530e7272210de3e0f519/README.md)
says this keeps it from reading agents' sign-ins, such as Claude Code's and Codex's files and
the Keychain, and
[its source](https://github.com/yetone/magpie/blob/8544d5fdb51bea24dece530e7272210de3e0f519/internal/provider/discovery.go)
says it then serves no subscription account. API-key providers and local models work. Magpie uses no subscription plan, so choose an API-key
or local model for the connection. A provider you add that signs in through another tool, such as Google Vertex AI through
gcloud, uses that tool's credentials.

Magpie keeps provider keys, usage records and caches in its own files, and can record history.
Enter those provider keys in Magpie. VIKI's optional **Gateway access key** is only for
authenticating to Magpie's gateway, not to its providers. VIKI does not copy Magpie's
credentials into its saved connections. VIKI settings exports and settings reset do not include
Magpie's data. Review Magpie's storage and privacy settings before adding provider keys or
enabling request recording. VIKI stops its bundled Magpie process when it begins to quit. On macOS
and Linux, after a crash or a forced kill, a Magpie left running is stopped the next time VIKI
starts, or at a later start or Magpie use if it cannot be identified then; on Windows it ends
with VIKI.
Magpie exports use the system Save As dialog and your account's file permissions, separately
from VIKI's note-save controls.

The dashboard has a separate access key. The desktop app shows it in an isolated view with
its own browser session; it receives no VIKI launch token and cannot request VIKI's origin.
Its VIKI appearance uses local fonts and display settings; its credentials and data stay separate.
In a browser window, the dashboard opens in your system browser. Keep its access key private.

Magpie's routing groups may retry requests or switch providers and models before
returning an answer. Changing a group can change where an open chat sends inference.
VIKI's failed-connection notice applies when Magpie returns a failure; it does not describe
every attempt Magpie makes upstream.

The bundled process disables Magpie's user-count telemetry and starts with self-updates off.
Keep its self-updates off and use a VIKI installer to update the bundled version. VIKI checks
the pinned executable before starting it; a modified executable is refused.
Magpie can fetch external model catalogs and icons, and its partner links and services have
their own privacy behavior. Its routing and retention controls are in the
Magpie dashboard. Review the [Magpie documentation](https://github.com/yetone/magpie#readme)
for those features.

## Decide what to trust

Claude Code runs with your account's file and process rights. It loads its usual instructions,
skills, hooks and permission rules.

- **Permission cards:** VIKI Agent shows **Allow once** and **Deny** when the CLI asks it to
  decide a tool call. A call already permitted or denied by the CLI's mode, rules or hooks may
  produce no app card.
- **Permission modes:** an ordinary chat starts in its selected permission mode. The mode chip in
  its header switches the running chat to Manual, Accept edits, Auto or Plan, never to
  `bypassPermissions` or `dontAsk`. A profile chat keeps its profile's mode.
- **Checks and previews:** in a trusted code folder, these run with your account's rights.
  Inspect commands and changes before running them.
- **MCP server switches:** turning a server on or off in the chat bar's **MCP servers** dialog
  asks Claude Code to save that choice in its own settings for the chat's folder. The app itself
  writes nothing there.

> [!WARNING]
> A tool permission is separate from VIKI Agent's own note Save and note actions. Neither is a
> sandbox for a code folder or content returned by a tool.

### Folders chats can also work in

A tick in Settings gives every new ordinary chat that folder too. Only a trusted code folder can
be ticked.

- **Access:** Claude Code reads the folder without asking. It edits it following the chat's own
  permission mode.
- **What it loads:** Claude Code loads that folder's own skills, commands and agents, and its
  plugin choices. A plugin those choices enable may bring its own hooks and MCP servers. The
  folder's `CLAUDE.md` loads only when your own Claude Code setting turns that on, and its
  `AGENTS.md` does not load ([Whose instructions a chat reads](using-the-app.md#code-folders)).
- **Chats left out:** profile chats, routines, Bots, the suggestion run and Workbench chats gain
  no folder this way. A Bot's own code folder works differently
  ([Bot code copies](#bot-code-copies)).
- **Timing:** a tick or untick applies to chats started afterwards. A chat already open keeps the
  folders it started with until its process stops.

The folder picker returns the chosen paths only. Registering one as a vault or a code folder runs
the same checks as a typed path.

### Bot code copies

A Bot you gave a code folder works in a copy of it: a Git worktree of that repository (see
[Code copies for a Bot](bots.md#code-copies-for-a-bot)). A copy is not a sandbox.

- **What a chat gets:** the copy as one added folder, beside its vault. A grant adds no model,
  note-write or permission authority. Claude Code's own permissions decide what a Bot reads,
  edits and runs there.
- **Shared Git data:** a copy shares its repository's refs, config and objects with the folder and
  its other worktrees. A Bot can create, move and delete branches and tags, and what it does
  there can affect other worktrees.
- **Git run by Claude Code:** Git commands Claude Code runs in the copy are tool calls under its
  own permissions, and they run the folder's hooks as in any folder.
- **What the copy loads:** for that chat, and for later turns and shared turns in the same copy,
  Claude Code loads the copy's `.claude` skills, commands and agents and its plugin settings. A
  plugin those settings enable may bring hooks and MCP servers. An added folder's `CLAUDE.md`
  loads only when your own Claude Code setting turns that on. VIKI Agent's Bot rules tell the Bot
  to read the copy's top-level `CLAUDE.md` and `AGENTS.md` with its tools before it reads or
  changes files there, and to follow them for that work: an instruction to the model, not a
  check ([Bot rules](bots.md#bot-memory)). The Bot's settings ask Claude Code to skip the vault's
  own instruction files ([What a Bot reads](bots.md#what-a-bot-reads)).
- **Pushing:** a Bot's instructions can tell it not to push. That is a request to the model.
  VIKI Agent does not enforce it.
- **Hooks off for VIKI Agent's own Git:** its own Git commands in a Bot copy run with an empty
  private hooks folder, Commit and Integrate included, and so does the fast-forward Integrate
  makes of the copy's branch in the main folder. A restored copy is listed as no Bot's copy, and
  its own Git commands still run with hooks off.
- **Programs your Git settings name:** VIKI Agent's own Git commands in a Bot copy, the status
  bar's Git chip there included, also run without the filter drivers your Git config defines,
  start no signing program named by a relative path, and fetch no missing object. A Commit that
  such a program must sign is refused. Git LFS's own commands (`git-lfs clean -- %f`,
  `git-lfs smudge -- %f`, `git-lfs filter-process` and their `--skip` forms) still run, started
  from PATH, and are told to download nothing. Removing a copy and restoring an archived one run
  the same way. In every code folder, VIKI Agent's own Git ignores `log.showSignature`.

**Git settings baseline.** The folder's first Bot copy saves a fingerprint of the repository's
local Git settings: its `config` and per-worktree config, the files they include, the hooks folder
(`.git/hooks` and any `core.hooksPath`), `info/attributes` and `core.attributesFile`. Your global
and system Git config are not part of it. Before VIKI Agent runs its own Git on that folder or one
of its worktrees, it reads the settings again and compares: Commit, Integrate, Push, checkout,
creating, removing, archiving and restoring worktrees, the Code view's changes and status, the
status bar's Git chip, Decision's looks at code and saved checks. A difference stops the action with
"This folder's Git settings or hooks changed after a Bot copy was made". The chip then runs no Git
in that folder and shows status unknown with no branch, commit or change count, while that folder
is the active workspace. The check does not apply to a folder that never had a Bot copy, or to a vault. **Confirm the current settings** in the **Worktrees**
tab, which shows it as soon as it opens, saves what is there now as the new baseline, if the
baseline is still the one you reviewed. Background work, such as the cleanup sweep and a copy's
release, skips the folder until you confirm; Decision's looks and saved-check snapshots then read it
as unknown.

**Git reads in a vault.** In a vault, at any policy, VIKI Agent's own Git reads (the status bar's
Git chip and a note's history), and the identity read when a folder is registered or inspected,
start none of the programs the folder's repository settings name: no fsmonitor hook, no filter
driver (Git LFS's included), no signature program and no network transport. A missing object
fetches nothing and no pack is written. The chip's status does not enter submodules. A folder
holding `HEAD`, `objects/` and `refs/` but no `.git` is not a repository to these reads, and the
trust step lists Git as present for it. Claude Code's own Git, in a chat started in that vault,
still follows the vault's settings.

The saved baseline is deleted only when the folder is removed from the list, so the check
continues after the folder's last copy is gone.

Any change to those settings trips the check, so ordinary commands do it too, whether you or
Claude Code in the copy runs them: `git push -u`, switching to a branch that sets upstream
tracking, adding or changing a remote, or `git config` for any repository setting. That is
expected. The check cannot tell a harmless change from a hostile one, so confirming means you have
looked at the current settings and accept them as the new baseline. Git LFS trips it too the first
time Git runs it in a repository that never used it: it writes `lfs.repositoryformatversion` to
the repository's config.

One exception: registering, refreshing or confirming it is the same folder before a change reads its identity with two Git
commands (the root commit and the common Git directory) without the check. They read neither the
index nor the working tree, and ran none of the repository-configured programs in testing.

VIKI Agent refuses a baseline instead of guessing when it cannot read the settings safely: a
file over its limits (256 files, 4 MiB), a link, a home-relative or drive-relative path, a
condition other than `onbranch` and `hasconfig:remote.*.url` in an `includeIf`, a settings
file that changed while it was read, or a hooks folder that holds a subfolder or a hard-linked
file (the layout of husky 5 to 8). When a Bot's first message hits this, the chat says "VIKI Agent
can't read this folder's Git settings safely." and makes no copy.

Limits:

- Hooks or filters replaced after a check are used by the rest of that operation. One check covers
  every Git command of one review, file view, search or saved-check snapshot, and of the reads
  before a Commit or Push. Commit, Integrate and Push check again once they hold the folder for writing. A Bot copy's
  own `.git` file is read again before each command in it, and a change is checked before that
  command runs.
- VIKI Agent refuses Commit on a copy, and removing it, while a chat is running in it. It does
  not hold back Integrate or its other actions in the main folder meanwhile.
- A repository config with `submodule.recurse=true` can send VIKI Agent's own Git commands other
  than status and diff into nested repositories in a copy. Their config and filters are outside the
  baseline. Every `status` and `diff` VIKI Agent runs in a copy carries `--ignore-submodules`, so
  those start no Git inside a nested repository. The status bar's Git chip does not carry it for a
  copy that is registered as a code folder of its own and is the active workspace. Cleanup keeps a
  copy whose index holds a nested repository's entry, one staged for deletion included, and its
  archive step refuses such an index rather than run `git add -A` on it.
- Where a filter driver other than Git LFS's has a `smudge` or `process` command, cleanup keeps a
  released copy that has changes or commits found nowhere else; with a `clean` command only, it
  keeps one whose changed or new files are under that driver. A new copy in such a folder can list
  unchanged files as changed until a Git command you run in the copy refreshes its index.
- Restoring an archived copy brings a Git LFS file back as its pointer file when the repository's
  own LFS store does not hold its object.
- Push and its remote check, in a copy you registered as a code folder of its own, keep the
  remote's settings. A program those name by a relative path, such as
  `remote.<name>.uploadpack`, is a file in the copy.
- More than 32 switched-off filter drivers and custom-named transports together, or one whose name
  holds `=` or is not UTF-8, makes Git unavailable in that folder's Bot copies.
- A release it could not finish stays pending and is tried again at the next cleanup pass or
  deletion.

## Decision model

Decision can ask TypeSafe's model whether to tell you about something now or hold it. See
[Using VIKI Agent](using-the-app.md#decision) to turn it on and save a key.

### What a Decision request carries

Before a request is sent, VIKI Agent:

- strips every digit from facts, times and counts;
- cuts excerpts to your chosen send level: titles only, or up to 200 or 2,000 characters, each
  ending "…" when cut;
- drops any evidence under a folder or tag on your **Never send** list, counting what it drops.

Text from outside the vault travels only in its own part of the request.

What a request carries about an item is capped at 12,000 bytes. Past that:

1. The least similar past items are dropped first.
2. Then excerpts are cut further.
3. Then the request is blocked rather than sent.

Every request also opens with a fixed guide on how to answer and carries the fixed questions.
Neither holds anything about your items, and together they ride along on top of that cap, about
5.5 KB more.

### How VIKI calls TypeSafe

The app makes only one call to TypeSafe, at `https://api.typesafe.ai`: asking a question.

- **Connection:** the call goes over TLS, with no redirects or system proxy. Any set cookie is
  ignored rather than refused.
- **Your key:** it rides only that one request's Authorization header, and nothing here logs it.
  It is sealed at rest (Windows: current-user DPAPI; macOS: the login Keychain; Linux: the Secret
  Service).
- **Rate:** VIKI keeps at most 2 requests in flight and sends at most 10 a minute.
- **Daily spend:** VIKI stops asking once the day's spend would reach the cap you set.
- **Retries:** a refused or failed request is not retried.
- **Call records:** the app keeps a sealed copy of what it sent and received for your last 20
  calls. **Context & loadout**'s **Decision** tab can show and copy it.

### Version comparisons and proposed wording

With **Compare with another model version** on, a fresh decision's same request state is sent a
second time, to that other version.

- **Limits:** it is sent under the same in-flight, per-minute, cooldown and daily-spend limits as
  the first.
- **Retries and records:** it is not retried, and it keeps no call record of its own.
- **Effect on live requests:** a rejected key or a busy reply to it pauses or cools the live
  requests too, as any request's reply would.

What VIKI learns from your answers is a handful of numbers that shift how loud and when, not your
limits. It lives only in the app's own local database. **Reset what VIKI learned** and
**Delete all decision history** both forget it.

Proposing new wording for one of Decision's questions is tried the same way. From your
**Propose this wording** choice, each fresh decision also asks your proposed wording once.

- **Limits:** it is asked under the same in-flight, per-minute, cooldown and daily-spend limits as
  the comparison above, and it counts toward the same cap.
- **Retries and records:** it is never retried, and it keeps no call record of its own.
- **Effect on live requests:** a rejected key or a busy reply to it pauses or cools the live
  requests too.
- **Storage:** the wording itself is sealed at rest the same way your key is, in the app's own
  local database.
- **What reaches TypeSafe:** the wording, in its trial requests while it is tried and, once you
  approve it, in every request.
- **Approving or rejecting:** doing so in **Approvals** is the only thing that changes which
  wording new decisions ask.

A proposed wording is refused if it contains any of these:

- control characters;
- line or paragraph separators;
- private-use or unassigned characters;
- characters a screen would draw as nothing at all, such as zero-width ones.

An ordinary space is fine.

### Vault upkeep proposals

With Self-improvement also proposing vault upkeep, three more candidate kinds land straight in
**Approvals** rather than a chat draft:

- linking two notes;
- marking an older note as replaced;
- archiving a project's ticked tasks.

What a proposed pair sends follows the rules above:

- **Paths:** each note's path in the vault.
- **Excerpts:** an excerpt, cut to your chosen send level: the line that mentions the other note,
  or the note's opening.
- **Held-back notes:** anything under a **Never send** folder or tag (written in its frontmatter
  or anywhere in the note) is held back. Here, a held-back note on either side blocks the whole
  pair, not just that one note.

A ticked-tasks proposal sends its tasks file's path and project name, never the tasks themselves.

Every request also carries the following, titles only, never a note's own text:

- the titles of notes changed since yesterday;
- the titles of items shown to you lately;
- the items you most recently marked mute or wrong;
- your recent feedback.

**Never send** applies to them the same way.

Approving or rejecting one of these proposals sends nothing about the change itself.

- **Approve:** only marks the row approved and hands you a drafted chat message for that one
  edit. Nothing in your vault changes until you send that yourself.
- **Feedback:** either act still counts as your feedback on that item: useful, or wrong for
  **Reject**. Later requests carry it under the item's fixed title, like any other feedback.

A proposal can never name a path this vault already protects:

- `AGENTS.md`, `CLAUDE.md`, `claude.local.md` and the other instruction files at any depth, case
  or width;
- hidden folders;
- `.git`, `.claude` and the vault's other tool folders;
- `_raw/` at any depth;
- at the vault's root, `system/` (so `system/memory/`), `_templates/` or `00-inbox/`.

This is checked again each time: when VIKI first notices it, when it is filed, and again at every
read-back or approval.

### Skill suggestions

With Self-improvement also able to suggest one of your vault's own skills, VIKI reads that vault's
skills. It matches them to the chat you have open by its title alone, entirely on this computer.

A request about the suggestion carries only fixed wording, its kind and its title, "A skill for
this chat". So does every later request that mentions it once you have seen, used or dismissed it.

- **Skill details:** the skill's own name, its description and the skills the chat already used
  never reach TypeSafe.
- **Chat title:** the chat's title is sent only as the name of the chat you have open.
- **Use this skill:** only puts that skill's `/` command in the chat's own message box. Nothing is
  sent until you do.
- **Untitled chats:** an offer to make a skill from an untitled chat is titled "This chat", so the
  chat's first message is not sent as its title.

### What VIKI starts or offers on its own

#### Look on my behalf

With **Look on my behalf** set to **Allowed**, the default, VIKI starts a plan-mode Claude Code
chat in a trusted code folder on its own, without a click. **Ask first** only offers
**Take a look**.

- **Message:** fixed text that names no folder, path, file or diff.
- **What it sends:** the chat reads the folder itself and sends what it reads to the selected
  model provider through Claude Code, as your chats there do.
- **Folder settings:** the folder's own instructions, hooks and settings, which you acknowledged
  when you trusted it, apply to a look as to any chat there.

> [!WARNING]
> The app checks those acknowledgements when you trust the folder and not again later. So hooks
> or MCP servers a later change adds, such as a pull, run in a look too.

#### Second looks and project plans

With **Take a second look at my work** and **Plan next steps for my projects** set to
**Allowed**, the default, VIKI also starts their plan-mode chats in the vault on its own. Each
waits for your approval before it changes anything.

- **What a second look reads:** only the first line of its own plan, to name the kind of finding.
  It keeps neither the plan nor your chat's text.
- **Your approval:** whether you approved that plan is kept with VIKI's record of that chat in the
  app's own local database, as the chat's mode is.
- **Stalled projects and overdue tasks:** a plan VIKI starts for these is read and kept the same
  way. Its message names one note in your vault and none of its text. VIKI picks no note that
  **Never send** holds back.

#### Message drafts

A message draft VIKI offers is fixed text it writes itself, with no note, web address or chat text
in it. It goes into your message box only when you select it. It leaves this computer only when
you send it, as any message you write.

#### Run my saved checks

With **Run my saved checks** set to **Allowed**, VIKI starts a trusted code folder's
saved checks on its own when the folder moves to a new commit. That includes a commit a pull
brought in. It runs them the same way **Run** in the **Checks** tab does. **Ask first**, the
default, only offers **Run checks**.

- **What runs:** each check is a command you wrote, run with your account's rights.
- **No sandbox:** none is a sandbox, and a check can change files and use the network.
- **Check details:** VIKI reads no check's name, command or output.
- **Which checks:** VIKI runs only checks you have run in that folder before.

#### Suggest destinations for my inbox

With **Suggest destinations for my inbox** set to **Allowed**, the default, VIKI starts the
inbox's suggestion run on its own. **Ask first** only offers **Suggest destinations**.

This is the run **Ask VIKI to suggest** starts:

- **Session:** a new Claude Code session with only Read, Glob and Grep and no MCP server, one
  reply, ended after five minutes.
- **Permissions:** the run denies any permission request it raises, as it does when you start it
  from the triage.
- **What it sends to Claude:** the name, the `captured` value and the first 600 characters of
  each capture in `00-inbox/`, up to 100, and your project names.
- **Vault reads:** it can read files in your vault with those tools.
- **Suggestions:** it keeps each vault's latest run and its suggestions sealed in the app's own
  storage, so they stay after a restart, until you unregister the vault. Nothing in your vault
  changes until you send the filing draft.
- **When none starts:** VIKI starts none while **Never send** holds a tag, a folder over a capture
  or a folder in `10-projects/`.

#### Bot learning runs

On a Bot's Learning card, **Reflect on its own after finished work** and **Dream on its own once
a day when it is quiet** are off by default. Turned on, VIKI starts a Bot's reflect or dream run
without a click, only for Bots in the active workspace, after the Bot has been quiet for 15
minutes, at most six reflect runs and one dream run a day per Bot.

- **Session:** the same kind as a run you start: only Read, Glob and Grep, no MCP server, one
  reply, ended after ten minutes, every permission request denied.
- **What it sends to Claude:** excerpts of that Bot's earlier conversations, never one that read
  web or MCP content, and that Bot's own memory records.
- **Results:** proposals wait sealed in the app's own storage; nothing in your vault changes
  until you apply one.

## Optional messaging bridges

[Mattermost](mattermost.md) and [Slack or Discord](messaging.md) each serve one configured human
owner in a verified private direct message.

- **Conversation:** each bridge has its own conversation in Claude Code's `auto` permission mode
  and stays pinned to the startup workspace.
- **Approvals:** Claude Code's classifier, not the DM, approves most actions. Requests it still
  makes reach the DM as cards.
- **Manual mode:** where `auto` mode is unavailable, Claude Code starts the conversation in Manual
  mode instead, and every action reaches the DM as a card.
- **Mode chip:** if you open a bridge's conversation in the app, its mode chip can switch that
  conversation like any chat. After `!viki new`, the next conversation starts in `auto` again.

A bridge does not sandbox Claude Code or expand Claude Code's own permissions.

### What leaves this computer

Trust the configured Mattermost server, or Slack or Discord, and the owner account before
connecting.

- **All bridges:** prompts, final replies and complete permission or question requests leave this
  computer and follow the service's retention policy.
- **Mattermost progress:** on Mattermost, progress also leaves it while Claude works: the elapsed
  time, the step count, one fixed activity phrase and Claude's latest short note of up to 280
  characters.
- **Activity phrase:** it comes from a fixed table. An MCP tool shows
  as "using a connected tool" and an unknown tool as "working". The phrase names no tool, file,
  path or command.
- **Progress note:** the note is Claude's own text, like a final reply, so it can mention files,
  paths or commands.

> [!WARNING]
> Service operators and anyone controlling the owner account are part of this trust boundary.
> Enable MFA and review retention before sending content. Local Reset and exports do not remove
> remote copies.

Once you send `!viki questions on`, a waiting question's header, text, options and descriptions
from any other desktop chat also leave this computer in the same direct message.

- **Off by default:** this is off by default and only for the current connection, so it must be
  turned on again each time the bridge reconnects.
- **Who can answer:** anyone controlling the owner account can turn it on and answer those
  questions, the same way a desktop choice would, under that chat's own permission mode.
- **Permission cards:** desktop permission cards are not sent this way.
- **Stopping:** this stops when the bridge stops or you send `!viki questions off`.

### Replying from the DM

A request from the bridge's own conversation can be answered three ways: with its command, with a
plain message, or, on Mattermost only, with a reaction. Each request shows its `!viki allow`,
`!viki deny`, `!viki pick` or `!viki answer` command.

Plain messages:

- **One request waiting:** when a request of the bridge's own conversation is the only request
  waiting as your message arrives (a relayed desktop question counts), a plain message answers
  it: `allow` or `deny` for a permission request, or option numbers or your own words for a
  question.
- **Several requests waiting:** while such a request waits beside another request (a relayed
  desktop question counts), nothing is sent to Claude and the reply points to the commands.
- **Other text:** other text on a permission request gets a hint and is not sent.
- **A running turn:** while a turn of the bridge's conversation runs and none of its own requests
  is waiting, a plain message is added to that turn.

Reactions, on Mattermost only:

- **Thumbs-up and thumbs-down:** a thumbs-up (`+1`, `thumbsup`) allows once and a thumbs-down
  (`-1`, `thumbsdown`) denies, from the owner account, in the bot's direct message, on the
  request's detail or instructions post, once the request has been fully posted.
- **Confirmation:** the WebSocket frame is only a candidate. It counts only after VIKI Agent reads
  the post's reactions from Mattermost and finds a matching owner reaction in that channel.
- **Questions:** questions and relayed desktop questions take no reactions.

Allow once still needs the complete request, and each request is answered once. Such a request
has no time limit: it ends when you answer it, send `!viki stop` or `!viki new`, or the bot stops.

Every turn of the bridge's conversation is posted while it is active, including a turn Claude
Code starts by itself, such as a background agent's report. After `!viki stop` or `!viki new`,
such a turn is not posted and its requests are denied until your next message.

### Setting up a bridge

Set up each provider in **Messaging**:

1. Select **Set up** on its card.
2. Fill in its fields.
3. Select **Save changes**.

Saving seals the entered tokens in the app database (Windows: current-user DPAPI; macOS: the
login Keychain; Linux: the Secret Service). It also stops any bridge running with the previous
settings. Saved settings apply without restart.

- **Connecting:** turn a provider's bot on with its switch to connect.
- **Leaving a bot on:** it reconnects on its own the next time VIKI Agent starts in the workspace
  it was turned on in. It stays off in any other workspace until you turn it on there.
- **Reconnecting on its own:** a bot that reconnects this way is reachable again as soon as VIKI
  Agent starts, before you have looked at the desktop. Its ready DM is the visible signal, and
  every tool approval still needs your answer to a current request.

Status and tokens:

- **Refresh status:** reads state without enabling or reconnecting.
- **Status:** shows only fixed state names, fixed stop reasons and on/off flags, without tokens,
  account IDs, server URLs or message text.
- **Settings card:** shows each provider's saved non-secret values (such as the Server URL or a
  member or user ID), and never a token.
- **Launch token:** the local launch token is separate from provider bot and app tokens and is
  not sent to the providers.

### Network connections and limits

These bridges make outbound HTTPS and WSS connections. The local listener remains on loopback.

- **Mattermost:** uses the configured HTTPS base and a closed set of API paths, including reading
  a post's reactions.
- **Slack:** uses its fixed API host and a validated Slack Socket Mode URL.
- **Discord:** uses its fixed v10 API and Gateway. Discord's configured client URL identifies the
  app in its HTTP User-Agent header and is not fetched.
- **All clients:** TLS verification is enabled. The clients refuse redirects, cookies, compressed
  HTTP responses and malformed JSON. They do not use environment proxies or retry a failed post.

On Mattermost, reactions, typing and post edits go through a separate progress client with its
own connection. It allows nothing
before it is bound. Afterwards it allows only:

- the reactions 👀, ✅, ⚠️ and ⏹️ as the bot user;
- removing the bot's own 👀;
- typing and posts in the bound direct message;
- edits to posts that client created.

Its failures stay local. A rate limit starts its own cooldown, and a failed call ends progress for
that turn, without stopping the bridge or answering a request.

Size limits:

| Item | Limit |
| --- | --- |
| HTTP request and raw response bodies | 128 KiB, within a five-second request deadline |
| WebSocket frames | 128 KiB |
| Rendered Mattermost posts | 12 KiB of UTF-8 |
| Slack posts | 4000 characters |
| Discord posts | 2000 UTF-16 units |
| An individual message | Can expand to at most 32 posts |
| Each bridge's queue | At most 32 queued posts and 384 KiB |
| Assistant responses | 64 KiB of source text and four Mattermost-format posts of 12 KiB each |
| Mattermost Bot result notices | 8 KiB after chat formatting |

Assistant responses may be shortened with a direction to check the desktop. An incomplete or
oversized permission request offers Deny only; **Allow once** requires the complete request to
have reached the DM.

How a post looks depends on your server or workspace and your mobile app. VIKI Agent hasn't been
checked against every version.

### Stopping and reconnecting

Each of these stops that bridge from accepting approvals straight away, before its connection
finishes closing:

- turning a bot off;
- saving or removing its settings;
- a connection failure it cannot recover from.

When a bridge stops:

- **Pending requests:** pending requests in that bridge are denied. The other providers and
  desktop conversations remain separate.
- **Stop notice:** a bot that had posted its ready message then posts one fixed line naming the
  reason when the service is reachable. It waits at most 3 seconds for it.
- **Stop reasons:** Messaging shows the reason, such as a rejected token, an unreachable service,
  a safety limit, a conversation error, a changed request or an unexpected error.

> [!WARNING]
> Stopping cannot undo an Allow already delivered locally or retract a post already sent. Check
> the DM and desktop after an uncertain receipt; choices and posts are not automatically retried.

After a stopped connection, turn that provider's bot on again once cleanup and its cooldown allow
it. Slack connection renewal and Discord reconnect requests also require this manual step.

- **Reconnecting:** retains rate limits and waits for fresh messages, without replaying offline
  commands.
- **Restart:** a restart is needed if the startup workspace changed or the bridge could not shut
  down cleanly.
- **Across launches:** saved settings and the on/off choice persist across launches, subject to
  the reconnect rule above.

## Optional Voicebox voice

Read aloud and hands-free can speak through Voicebox, a separate program you install and run on
this computer. See [Using VIKI Agent](using-the-app.md) for setup.

VIKI Agent sends the text of a reply to whatever answers at the configured Voicebox address. That
address must be http on 127.0.0.1, localhost or [::1].

> [!WARNING]
> Voicebox has no authentication. Any program listening at that address receives the text sent
> to it and can present itself as ready and offer its own voices. Set the address only to a
> Voicebox you trust.

- **Calls:** the app server makes only three calls there: a status check, a GPU check and one
  request to synthesize speech.
- **Redirects:** it follows no redirect.
- **Answers:** it caps every answer (256 KiB of JSON, 8 MiB of audio) and accepts only a
  well-formed WAV.
- **The app's own voice requests:** they need the same launch token as its other private
  requests and are marked not to be cached. They answer a failure with a fixed code rather than
  error text, and log only that fixed code, with no text.
- **Hands-free reports:** a hands-free report carries only a fixed event and code, and only those
  two values are logged.

Voicebox is a separate program: once it has the text, how it handles it is its own.

## Optional natural voices

Natural voices are VIKI's own copy of Voicebox 0.5.0's CUDA server and two models, Qwen3-TTS 0.6B
CustomVoice and Kokoro-82M. See [Using VIKI Agent](using-the-app.md) for setup and
[Known issues](known-issues.md) for the hardware it needs.

- **Installing:** they are installed only when you select **Install natural voices** in
  **Settings › Chat** on a supported Windows PC with an NVIDIA graphics card. A pack without
  Kokoro gets it from **Add the fast voice**.
- **Downloads:** VIKI downloads two pinned GitHub release files and 56 pinned Hugging Face files
  at two pinned model revisions (13 for Qwen3-TTS, 43 for Kokoro-82M).
- **Checks:** each file is checked against a pinned size and SHA-256 before use. Downloads go
  over https, with redirects followed only to https.
- **Storage:** VIKI unpacks the archives into a private folder in VIKI's own app storage.
- **Signing:** these binaries are not signed by VIKI or their publisher. Trust rests on the
  pinned hashes matching what is fetched.
- **Removing:** **Remove** in Settings deletes the installed files and what they downloaded.
  Installing again re-downloads them.

Once installed, VIKI starts its own Voicebox server, on a random loopback port, only when a reply
needs a natural voice or you preview one in Settings.

- **Lifetime:** the server ends when VIKI closes and is stopped again after 5 minutes unused.
  While hands-free is on, the server stays ready to answer.
- **Offline:** it runs offline.
- **Previews:** a preview sends only a fixed sample sentence the way a reply does.
- **Audio:** Voicebox 0.5.0 documents its speech endpoint as streaming audio back without saving
  it to disk, and VIKI does not save it either.

> [!WARNING]
> Like your own Voicebox app above, the server has no authentication of its own. Any program on
> this computer that finds its port could ask it to speak.
>
> Voicebox 0.5.0 also checks neither a caller's identity nor its Host header. So a web page open
> in a browser here could reach it too, through DNS rebinding or a plain cross-site form post. A
> browser's own local-network protections may block that.

## Bots and routine results

A Bot is built from an agent profile and has its own reopenable Claude Code conversation.

- **Bot conversation:** it keeps the profile revision and model it was prepared with.
- **Routine runs:** each routine run uses a fresh chat with the current profile.
- **Reading:** reading Bots or opening a Bot's conversation sends no message.

### Bot memory

Bots keep no Claude Code auto memory: every Bot chat, task thread, Bot-thread turn and routine run
starts with `autoMemoryEnabled` off in its private settings file. The same file names a per-Bot
folder in `autoMemoryDirectory` (`bot-memory/<bot UUID>/` in an ordinary vault, otherwise
`<data>/bot-memory/<workspace id>/<bot id>/` in VIKI Agent's data folder, outside the vault), which
holds only notes saved with auto memory, as Bots did in earlier versions of VIKI Agent. Claude Code's managed settings take precedence and could
turn auto memory back on; the Bot memory view shows the requested folder and does not check
Claude Code's effective settings.

- **What a Bot recalls:** only memory records in the vault's `memory/` folder whose exact
  content you approved on this computer: your own records, shared by every Bot in the vault, and
  the records you applied from that Bot's Learning card, marked as its own. VIKI Agent adds them
  after the memory rule and Bot rules in the system prompt it sends in the `initialize` request
  at each start, as `<profile>`, `<facts>` and `<lessons>` blocks headed as data, not
  instructions, each capped at 6,000 characters. A note that says `status: approved` is not
  enough.
- **Learning runs:** a reflect or dream run is a separate Claude Code session with only Read, Glob
  and Grep and no MCP servers. Its permission requests are denied, it writes nothing, and it is
  stopped after ten minutes. Its prompt carries excerpts of the Bot's earlier conversations,
  marked as data, and the Bot's records; it never includes a conversation that read web or MCP
  content. Each proposal must quote that text exactly and can change only that Bot's own records.
  Automatic runs are off unless you turn them on.
- **Approval:** a proposal's text is sealed in VIKI Agent's store like other private records.
  **Apply** saves it as a note the way other note saves work, and stops if the record changed
  meanwhile; nothing is written until you apply it, and **Undo** retires what
  an applied addition wrote. Episodes record when a turn ran, how it ended and which tools it
  used, never its text.
- **Write fence:** each Bot session's Claude Code asks VIKI Agent through a `PreToolUse` hook
  before its `Write`, `Edit`, `MultiEdit` and `NotebookEdit` tools run. VIKI Agent refuses a write
  into `memory/`, `bot-memory/` or the data folder's Bot memory area, and, once the session or its
  Bot thread has used `WebFetch`, `WebSearch`, an MCP tool, or Claude Code's built-in
  `ListMcpResourcesTool` or `ReadMcpResourceTool` (which also read MCP server text, without the
  `mcp__` prefix), also into `system/memory/`, any `.claude` folder or an instruction file. On
  Windows, VIKI Agent also refuses, in every session, a target whose raw or resolved path carries
  a spelling NTFS would otherwise rewrite on creation (a stream name such as
  `CLAUDE.local.md::$DATA`, or a trailing dot or space such as `CLAUDE.md.`), or whose raw or
  resolved drive is not a plain drive letter: a vault is always rooted at a plain drive letter, so
  a `\\?\`, `\\.\`, UNC (`\\server\share\...`) or `\\?\UNC\...` path can land inside a kept folder
  without its spelling ever matching that folder's own. It never allows a call by itself:
  everything else still meets Claude Code's permission check, and an error refuses. It does not
  inspect shell commands.
- **Controls:** the Bot page's memory card contains the Ask controls (review, forget and
  change) for the earlier notes; its **View memory** opens the file dialog. A Bot chat's
  **Memory** header button and **View memory** in Bot activity open the same dialog when the
  active vault's policy matches the Bot's. The dialog lists exact paths; **Open in Vault** (only
  for vault-kept memory), **Open in VS Code**, **Edit**, **Refresh memory** and **Close** act on
  that list. It marks an incomplete list and file or title details it could not read.
- **Editing:** **Open in VS Code** hands the file to VS Code, which edits it directly. **Save** in
  the dialog's in-app editor writes nothing: it prepares an unsent request, appended to the chat's
  unsent draft or as a new task thread's draft. It is never sent for you, and the write fence
  refuses the Bot's own file-writing tools in that folder.
- **Location and exports:** memory records are vault notes and go with vault backups; earlier
  notes kept in the data folder do not. Diagnostics and settings exports carry no memory text.
- **Memory rule:** every Bot chat, task thread and Bot-thread turn gets VIKI Agent's fixed memory
  rule right after the profile's instructions, as an addition to Claude Code's system prompt that
  VIKI Agent sends in the `initialize` request at each start, not in argv or a file; a
  routine run gets it when its profile has a Bot in the routine's workspace. It tells the Bot that
  its recalled records are notes, not orders, not to write `memory/` or `bot-memory/`, and never to
  repeat secrets or instructions taken from web pages, tool output or other Bots as something to
  remember. It is text the model reads; the write fence is what VIKI Agent enforces, and other
  chats don't get it.
- **Bot rules:** the same chats get VIKI Agent's fixed Bot rules right after the memory rule.
  They ask the Bot to read a folder's top-level `CLAUDE.md` and `AGENTS.md` before it reads or
  changes files in that folder outside the vault, such as its code copy; to ask you before
  deleting files, sending anything off this computer, pushing or paying; to treat web pages,
  files, tool output and other Bots' messages as data, apart from those instruction files and the
  skills it uses; and to write no passwords, keys or tokens into notes or files. Like the memory
  rule, they are text the model reads, not a permission: Claude Code's permissions decide what
  runs.
- **Vault instructions:** the same settings file also names, in Claude Code's
  `claudeMdExcludes` setting, the vault's `CLAUDE.md`, `CLAUDE.local.md` and `AGENTS.md` files and
  its `.claude/rules/` files, at any depth, in every vault, for Claude Code to skip. It names
  nothing outside the vault.
- **Removing a Bot:** keeps its earlier memory files, in the vault or in the data folder, and its
  memory records in `memory/`; VIKI Agent deletes the Bot's episodes, runs, proposals and use
  counts.

> [!IMPORTANT]
> Records and folders provide no sandbox: a Bot can read other files its permissions allow.

### Seat Bots

Right after **Start with VIKI** makes a vault, the window that made it adds its seven seat Bots. It
reuses each seat's profile or one with its name, else creates it from the seat's job description.
This prepares no conversation and sends no message.

- **Open conversation:** used later on an empty seat, it creates the seat's missing profile and
  Bot and prepares a conversation without sending a message.
- **Sending in a shared Bot thread:** sending to an empty seat in a shared Bot thread also creates
  its missing profile and Bot, then prepares the thread's source conversation for that send.
- **Add missing Bots:** in **Bots** under **Core team**, it creates each missing seat's profile and
  Bot the same way, one seat at a time, and prepares no conversation and sends no message.
- **Kanban and routines:** these actions need no Kanban skill and create no routine.
- **Removing a Bot:** leaves its seat empty until you use it again.

### Routine permissions and automatic starts

Routine tool permissions and deadlines apply.

- **Requests:** routine requests need desktop review rather than Mattermost permission controls.
- **Scheduled runs:** a scheduled run needs a permission mode that asks first or Claude Code's
  `auto` mode. **Accept edits** cannot be scheduled. Scheduled runs need an awake host and an open
  VIKI Agent window.
- **Stop time:** a request still waiting at the run's stop time is denied.
- **Stop at a draft:** a routine with this setting answers each permission or plan request of a
  run with a deny at once, carrying a fixed message that asks Claude to finish with a draft. It
  changes no permission settings, and a question still waits until the time limit.
- **Only when a card is waiting:** a routine with this setting reads the start of up to 128 notes
  directly in its vault's `kanban/` folder before each scheduled time, to find
  a `todo` card for its seat.

With **Start Bots' work** set to **Allowed**, VIKI starts a seat's routine on its own, without a
click. **Ask first**, the default, only offers **Start**.

- **Which routine:** only that seat's one enabled routine set to **Only when a card is waiting**,
  tested at its current version, in a vault whose policy is **Ordinary**.
- **Checks:** the run passes the checks a scheduled run passes, so its permission mode asks first
  or is Claude Code's `auto` mode.
- **Settings kept:** it keeps its routine's prompt, time limit and confirmation setting.

### Mattermost result notices

Mattermost result delivery is off at each launch; you turn it on for each Bot. It is available
only through the connected bridge's verified private direct message and for the same startup
workspace.

- **Rechecks:** before a notice is sent, the queued notice rechecks the Bot, the workspace, the
  destination, your opt-in and the current attention policy.
- **Cancelled notices:** disabling delivery, removing or resetting the Bot, or closing the last
  VIKI Agent window cancels notices not yet sent. Turning delivery off and on does not revive an
  earlier notice.
- **Sent posts:** a post already sent cannot be retracted. An uncertain receipt is **Unknown**
  and is not retried automatically.
- **Local outcome:** the local routine outcome remains separate.

What a notice carries:

- **Reply text:** only runs from a Bot with delivery on collect their final reply text, and only
  in memory.
- **Successful runs:** a successful run can send up to 8 KiB of chat-formatted text, including
  the name, outcome, run ID, and shortening notice.
- **Failed, blocked, or expired runs:** eligible ones send fixed status text without partial
  replies.
- **Left out:** thinking, tool output, permission cards, and questions are not part of a notice.
- **Replies:** replies to these notices continue the dedicated **Messaging** conversation, rather
  than the Bot conversation or run chat.

How Decision applies:

- **Decision's rules:** they apply to the completed run, using that run's workspace mute and
  **Later** settings. This decision does not start another model run.
- **Held notices:** they remain local. **Later** expiry does not replay them.
- **Notice records:** Bot notice records are kept apart from Decision's own items and totals.

### Bot records and removal

VIKI Agent's records of Bots and notice delivery store links, revisions, hashes, timestamps, and
fixed attention or delivery states.

- **Not held:** they hold no instructions, prompts, reply text, credentials or Mattermost
  settings. They also do not record whether delivery is on, which starts off at each launch.
- **Profile instructions and chat drafts:** these are stored where they are for any profile or
  chat.

**Remove Bot** and **Reset Bots in this workspace** remove Bot links and delivery records and
pause the Bot's enabled routines.

- **Kept locally:** profiles, conversations, routine history, and running attempts stay available
  locally.
- **Not removed:** backups you have already made and copies in Mattermost.

## Vault and app storage

In a vault whose policy is **Ordinary**, VIKI Agent can save notes itself on Windows, macOS and
Linux. This includes the note actions, answering a Kanban card under **Needs you**, and keeping a
chat image in the vault.

- **Conflicts:** a save stops if the note changed elsewhere first, and your draft stays.
- **Refused notes:** a read-only, locked or another user's note is refused.
- **Drives:** saving also needs a supported drive: NTFS on Windows, APFS on macOS, or ext4, XFS,
  Btrfs or tmpfs on Linux. Another drive, such as a network drive, keeps the vault read-only.
- **Replacing a note:** a replace keeps the note's permissions and, where the account allows it,
  its group, else drops group access. It does not keep extended attributes, Finder tags, ACLs or
  the creation date.
- **Instruction files:** in an Ordinary vault, **Save** on the Settings **Instructions and
  Persona** page replaces the vault's own top-level `AGENTS.md` or `CLAUDE.md` with your edit. It
  replaces only an existing file with exactly that name, with the same conflict, permission and
  drive checks as a note save, and keeps its encoding, byte-order mark and line endings. It doesn't
  create, rename, move or delete either file, or save one in a subfolder, a protected vault or a
  code folder, and it doesn't go through Claude Code's permission prompts or hooks. It refuses
  text that a protected vault's file of the same name holds, because saving it would stop VIKI
  Agent from writing to this vault. While the vault is busy, for example during a backup, a save
  waits up to 10 seconds and then stops with nothing written. VIKI Agent keeps no previous
  version. During a save the file is briefly absent. If the save stops there
  (VIKI Agent stops, or the last step fails), the file stays absent until VIKI Agent next starts.
  That start finishes the save, unless something changed in the meantime (for example the vault
  moved, its policy changed, or another program wrote the file): then the save waits under **Saves
  stopped partway through**, with both texts kept in the vault's `.viki-` files. Discarding that
  record does not bring the file back; if another program wrote it, that version stays.
- **Starter updates:** checking for a starter update only reads the vault's `.viki/vault.json`,
  its starter files under `.claude/` and `_templates/`, `AGENTS.md`, `CLAUDE.md` and
  `system/starter-updates.md`, without following links. VIKI Agent writes none of them: the chat you send makes each change
  under your own hooks and permissions, and an AGENTS.md change waits for your Save.
- **Saves at the same moment, off Windows:** off Windows, a file cannot be locked against other
  programs, so the app checks each step before and after it runs. When another program saves the
  note at the same moment, that version is kept and your save stops as a conflict. A new note's
  save can wait in **Recovery** instead.
- **Programs holding the note open:** a program that keeps the note open for writing during a
  save can still lose its own later write. On Windows, the save is refused while another program
  holds the note open for writing. See [Known issues](known-issues.md).
- **Protected places:** VIKI Agent does not save to a protected vault or to Claude Code's own
  folders (`~/.claude`). Changes made by Claude Code tools follow Claude Code's own permissions.
- **Backup restore:** a backup restore makes a new read-only vault folder.

### Sealed app storage

Drafts, sent text, card answers and inbox suggestions kept by VIKI Agent are sealed in local
storage:

- **Windows:** uses current-user DPAPI.
- **macOS and Linux:** use a key from the platform keychain for AES-256-GCM.

Sealing protects those stored values from casual inspection of the database.

> [!IMPORTANT]
> Sealing does not hide a conversation from Claude Code or the provider, and it does not
> anonymize every attachment or transcript. A process running as the same account has broader
> access than the app's browser-facing checks can prevent.

### The local server and links

The local server uses a fresh launch token for private app requests. The token lasts for that
launch.

- **Request checks:** the server checks the request's host and, for relevant requests, origin.
- **Your window and account:** treat an app window and your user account as private.
- **Reply Markdown:** it is displayed without raw HTML.
- **Links:** external links use the operating system's opener for HTTP, HTTPS or mail links.
  Opening a link can contact a remote site.
- **MCP server requests:** turning an MCP server on or off, or reconnecting it, can name only a
  server that chat's Claude Code already lists. The answer is a fixed status or error code rather
  than Claude Code's own error text, and VIKI Agent logs nothing for these requests.

## VIKI Agent's own window

VIKI Agent's own window is the Electron app in `desktop/`. It runs the server and shows its
windows in Electron's own Chromium, with no Edge, Chrome or Chromium needed.

- **Launch token:** the server sends each window's address, launch token included, only over the
  pipe between the two programs. No command line holds it.
- **Request checks:** the same host, origin, content security policy and token checks apply as in
  a browser window.

These windows have context isolation, the renderer sandbox, no Node.js in the page or its
workers, and no embedded web views, except the Magpie dashboard, which opens in its own isolated
view (see [Magpie routing and management](#magpie-routing-and-management)).

- **Navigation:** apart from that dashboard, they load only VIKI Agent's own page, refuse navigation elsewhere, and open no
  new window. Links open in your default browser.
- **Granted only to VIKI Agent's own page:** the microphone (audio only), notifications and
  clipboard writes.
- **Refused:** camera, location, screen capture, clipboard reading, HID, USB and serial devices
  and every other request.
- **System settings:** the operating system's own microphone and notification settings still
  apply.
- **Spellcheck:** uses the macOS system spellchecker. It is off on Windows and Linux, so no
  dictionary is ever downloaded there.
- **Chromium's sandbox:** the window refuses to open at all, showing an error dialog instead,
  when Chromium's sandbox is off (see [Known issues](known-issues.md)).

Quitting and closing:

- **Quitting the app:** tells the server to stop at once. Should the server still be running 15
  seconds later, quitting ends its whole process tree.
- **Closing the last window:** instead lets a running turn finish first, for up to five minutes.

Started from source, this window runs the unmodified Electron binary that `npm` installs in
`desktop/node_modules`.

### Installed builds

An installed build runs only its own bundled server. `VIKI_SERVER` (see
[Configuration](configuration.md)) is a developer override that an installed app ignores.

An installed app:

- cannot be run as plain Node;
- ignores `NODE_OPTIONS` and debugger flags;
- gives pages loaded from local files no extra privileges;
- loads code only from its own packaged files;
- encrypts its cookies.

On Windows and macOS, it also checks at startup that its packaged files have not been altered,
and ends itself if they have. Linux has no such check. An installed build asks GitHub whether a
newer release exists a minute after it starts and every 6 hours while **Settings › Diagnostics
and About**'s **Check for updates automatically** is on, and when you choose that page's **Check
now**. Each check is a request for the release's update file (`latest*.yml`): GitHub gets that
request's IP address and user agent, and counts it as a download of that file. **Download**
opens the release page in your browser; the app downloads and installs nothing itself.

## Sharing diagnostics and backups

**Export diagnostics** and **Export settings** preview a JSON file and redact recognized paths,
account names and token-shaped values.
An export holds its validated destination while saving; replacing that folder's path cannot
redirect the file into another folder.

- **Workspace folder paths:** these are hidden unless you select **Folder paths**.
- **Credentials:** both exports filter recognized credential fields, credential assignments and
  URLs containing login details, including in workspace names.
- **Log tail:** a shortened log tail drops its first captured line so a cut-off credential label
  does not leave its value behind.
- **App logs:** these record warnings and errors. They do not record prompt text or frame bodies.
  An empty log can be normal.

> [!WARNING]
> Redaction is a filter, not a guarantee that all sensitive context has been removed. Review the
> exported file before sharing it.

Backups and portable settings:

- **Vault backups:** a vault backup contains note content and is not encrypted. Put it in a
  location whose access you control.
- **Conversations:** settings and diagnostics exports do not back up conversations. Use a chat's
  own export tools for a conversation.
- **Portable settings:** portable settings export and import exclude Bots, profiles, routines,
  and saved messaging settings.
- **App database backups:** an app database backup includes Bot links, delivery metadata, sealed
  messaging settings, Bot usage counts and Decision data as it was when the backup was made.
  Deleting Decision data later in **Data** does not change the backup; Claude Code owns its
  conversation history separately.

See [Sessions and data](sessions-and-data.md) for retention and Reset consequences.

### Rolling back after an upgrade

Each upgrade step of `app.sqlite3` first makes a verified copy of the database as it was,
`app.sqlite3.v<N>-backup-<id>.sqlite3` beside it, where `<N>` is the version before that step. A
build from before the step refuses the upgraded database. The step that added the Bot usage log,
for example, is version 41, so it leaves `app.sqlite3.v40-backup-<id>.sqlite3`; restoring that
copy drops any usage rows logged since.

For rollback:

1. Close the app.
2. Restore the copy whose version matches the older build, with that build and the same Windows
   account or macOS/Linux keychain item that sealed the values.

In the recipes below, `<data>` is the app-data folder:
`%LOCALAPPDATA%\viki-agent` on Windows, `~/Library/Application Support/viki-agent` on macOS and
`$XDG_DATA_HOME/viki-agent` (`~/.local/share/viki-agent` by default) on Linux. `<backup>` is the
full path of the copy whose `v<N>` is the older build's version; if several match, use the
newest. Each recipe moves the
current database and any `-journal`, `-wal` and `-shm` files into a new folder next to `<data>`
(private on macOS and Linux, where `mkdir -m 700` makes it so), so nothing is deleted, then puts a
copy of the backup in place. It stops if that folder already exists. To roll back again, first
move or rename the earlier hold folder. Write an apostrophe in a path as `''` in PowerShell and as
`'\''` in the shell. Run it with the app closed.

On Windows, in PowerShell:

```powershell
$ErrorActionPreference = 'Stop'
$hold = '<data>-rollback-hold'
if (Test-Path -LiteralPath $hold) { throw "$hold already exists" }
New-Item -ItemType Directory -Path $hold | Out-Null
foreach ($suffix in '', '-journal', '-wal', '-shm') {
    $file = "<data>\app.sqlite3$suffix"
    if (Test-Path -LiteralPath $file) { Move-Item -LiteralPath $file -Destination $hold }
}
Copy-Item -LiteralPath '<backup>' -Destination '<data>\app.sqlite3'
```

On macOS and Linux, in a shell:

```sh
set -eu
data='<data>'
backup='<backup>'
hold="$data-rollback-hold"
mkdir -m 700 -- "$hold"
for suffix in '' -journal -wal -shm; do
    if [ -e "$data/app.sqlite3$suffix" ]; then mv -- "$data/app.sqlite3$suffix" "$hold/"; fi
done
cp -- "$backup" "$data/app.sqlite3"
```

Then start the older build with the same Windows account or keychain item. Copy no credential,
keychain item or `sealing-key-id` record, and do not edit the database's `user_version`. The
backup stays where it is, as does the hold folder.

> [!WARNING]
> After a rollback, everything stored after that upgrade is gone, such as settings saved after
> it. A rollback restores only app state as of the backup, not later changes or Claude Code's
> conversation history.

Changing the database's version in place is not a supported downgrade.
