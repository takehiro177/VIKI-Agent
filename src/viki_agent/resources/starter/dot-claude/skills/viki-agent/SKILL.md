---
name: viki-agent
description: Change VIKI Agent's own settings, routines or Bots through its CLI, from inside a chat opened in the VIKI Agent window. Use when the owner asks to change a setting, add or test a routine, turn a routine on, or create a Bot.
---

# /viki-agent

First check `[ -n "$VIKI_AGENT_CLI" ]`. Empty: only a chat opened in the VIKI Agent window can do this — tell the owner to open or reopen this chat from the window, and stop.

Run it as `"$VIKI_AGENT_CLI" status --json` in Bash, or `& $env:VIKI_AGENT_CLI status --json` in PowerShell. Every command takes `--json` and never prompts.

Look first, before any change — `status` again any time, plus:
- `"$VIKI_AGENT_CLI" settings get <key> --json` — one setting's value and revision; no key lists every key.
- `"$VIKI_AGENT_CLI" bots usage --days 30 --json` — token use of this vault's Bots.
- `"$VIKI_AGENT_CLI" bots trace <session-id> --json` — one conversation's calls.

Change, one command at a time:
- `"$VIKI_AGENT_CLI" settings set appearance.theme dark --json`
- `"$VIKI_AGENT_CLI" routines add --name "Morning review" --prompt "Summarize today." --profile <profile-id> --daily 09:00 --timezone Asia/Tokyo --json`
- `"$VIKI_AGENT_CLI" routines test <routine-id> --json` — starts a test run; the routine is not tested yet.
- `"$VIKI_AGENT_CLI" bots create --profile <profile-id> --json`

Display settings (theme, density, text size, reduced motion, panel widths, the launcher, the code font size and line wrap, Bot colors and icons, read-aloud speed and voices) change at once. Every other setting, and turning a routine on, waits for the owner's own Apply click in the VIKI Agent window, not this chat — exit 3: say so, and don't run the command again.

To turn a routine on: after `routines test`, check `"$VIKI_AGENT_CLI" status --json` until that routine shows `"running": false` — while it stays running, it may be waiting for the owner's own answer on a permission card in the VIKI Agent window, so say that instead of polling in silence. State `tested`: run `"$VIKI_AGENT_CLI" routines enable <routine-id> --json` (itself waiting for Apply). State `draft`: the test failed — tell the owner instead of enabling it.

Exit codes: 0 ok. 1 refused — read the reply's `fix`; it also carries `blockers` (a routine not ready) or `fields` (an invalid setting) when relevant. 2 usage. 3 pending — waiting for the owner's Apply; say so, do not repeat the command. 4 not answering — code `no_reply` means the request may already have gone through, so run `"$VIKI_AGENT_CLI" status --json` and check before repeating anything; any other code means nothing was sent. 5 no connection — ask the owner to reopen this chat in the VIKI Agent window.

Never print `$VIKI_AGENT_KEY` or echo the environment; no command ever needs it typed in.
