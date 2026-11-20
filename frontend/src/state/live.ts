/**
 * Which of this app's sessions actually have a claude process behind them.
 *
 * This exists because the app had no honest answer to that question, and two different features
 * were quietly asking it. Both read `Object.keys(appSnapshot.sessions)` — every session the
 * browser has a VIEW of — which is a different set entirely:
 *
 * - Opening a row to READ it seeds a view here, synchronously, before any request is answered
 *   (viki.ts's openSession → requestHistory → store.update → emptyView). The server does the
 *   opposite: `SessionManager.open()` builds an actor and starts nothing, because "its process
 *   starts on the first send" (session.py). So reading three old conversations filled the
 *   three-session cap, and the toast then named those three as the live sessions the user had
 *   to close — in an app running zero copies of claude. The server would have admitted a fourth
 *   happily; only the browser refused.
 * - Wrap up has the same question with higher stakes, from the other side: its prompt runs
 *   `git add -A`, so any sibling session mid-edit has to finish first.
 *
 * The honest test is `info`, which arrives with `session.started` and is therefore a real
 * process reporting itself. `startedHere` covers the only gap that leaves: a brand-new session
 * IS started eagerly by the server on `session.open`, so from the click until that frame lands
 * it holds a slot it has not yet confirmed. Without it a fourth new session could slip through
 * in the round trip. A resumed session needs no such entry — it genuinely has no process until
 * the user sends, which is exactly when the server starts one and reports it.
 *
 * A closed view never counts: `session.closed` removes it outright, and the flag covers the
 * frame or two before that lands.
 *
 * Neither signal can say a process DIED, and both of them leak a slot when one does:
 *
 * - A crash. Nothing on the wire announces it. The server drops its own driver on an error that
 *   arrives while the process is gone (session.py), but the codes it forwards then are the same
 *   ones a perfectly live driver emits (`internal`, `frame_error`), so the browser cannot tell
 *   process death from a bad turn by reading frames. The server can, and says so: it rebuilds
 *   `vault.sessions.live` from its own drivers for every snapshot. So that list REMOVES ids here
 *   and never adds them. Never adds, because it is up to five seconds behind (VAULT_POLL_S) and a
 *   resumed session's first send starts a process it has not seen yet — three of those inside one
 *   poll interval would read as fewer than three, let a fourth in, and hand the user the silent
 *   `too_many_sessions` on send that this module exists to prevent. `startedSince` (store.ts)
 *   settles the ordering: a `session.started` that arrived after the last list is newer than it,
 *   so that list gets no vote on it. What survives this: a process that starts and dies inside one
 *   poll window with nothing else in the vault changing produces no new list to prune it, because
 *   the hub only broadcasts when its answer changes. That slot is held until the next change and
 *   is cleared by Close. Holding a slot too long refuses a session; releasing one too early loses
 *   a typed message to the server's own refusal, so the bias is deliberate.
 * - A start that never produced a process: a new session whose eager start raised StartError (a
 *   broken CLI install, billing, or the server's own cap) sits in `startedHere` with nothing
 *   behind it. Here the frames are enough: a successful start emits `session.started` and nothing
 *   else before it, so an error reaching a session that never started IS that failure.
 *
 * `startedHere` is still only ever added to. It is consulted only for sessions that match here,
 * so an id whose session has closed or whose start failed simply stops counting; pruning the set
 * itself would buy nothing and would need a second place to remember to do it.
 */
import type { SettingsView } from "../clients/app/shellApi";
import type { AppSnapshot } from "./store";

/**
 * The live-session cap App gates new chats on. The settings document is authoritative once it
 * actually carries `chat.max_live_sessions` — a document from before that key existed does not,
 * and it never joins `testShell`'s fixed fifteen keys, so a document missing it reads the same as
 * no document at all. Short of that, the last limit the server reported in a `session.snapshot`
 * carries the app through before the document loads. Short of that too — no shell, or neither
 * answer has arrived yet — 3 is what this app always refused a fourth session at before either
 * existed (m3c, m4b's frozen tests). A value that is not a safe integer of at least 1, at either
 * step, is treated as absent rather than as a refusal to start anything.
 */
export function maxLiveFrom(settings: SettingsView | null, reported: number | undefined): number {
  const effective =
    settings !== null && "chat.max_live_sessions" in settings.keys
      ? settings.effective["chat.max_live_sessions"]
      : undefined;
  if (typeof effective === "number" && Number.isSafeInteger(effective) && effective >= 1)
    return effective;
  if (typeof reported === "number" && Number.isSafeInteger(reported) && reported >= 1)
    return reported;
  return 3;
}

export function liveSessionIds(
  snapshot: AppSnapshot,
  startedHere: ReadonlySet<string>,
): Set<string> {
  const live = new Set<string>();
  const report = snapshot.live;
  for (const [id, view] of Object.entries(snapshot.sessions)) {
    if (view.closed) continue;
    if (view.info !== null) {
      // A process reported itself, and only the server can tell us it has since gone. Its list
      // outranks `startedHere` here: the round trip that entry covers ended when `info` arrived.
      if (report && !report.ids.has(id) && !report.startedSince.has(id)) continue;
      live.add(id);
      continue;
    }
    if (startedHere.has(id) && view.error === null) live.add(id);
  }
  return live;
}
