/** External React store for generation-scoped session projections and global live reports.
 * History applies only to its exact pending request. Snapshot reconstruction is separate from
 * retained history and installs current native liveness atomically with the recovered view.
 */
import type { OtherSession, ServerMessage } from "../protocol";
import { applyEnvelope, applyHistoryPage, applySnapshot } from "./sessionReducer";
import type { SocketStatus } from "./socket";
import { emptyView, type SessionView } from "./view";

/**
 * The server's own answer to which of this app's sessions have a claude process — it rebuilds
 * `vault.sessions.live` from its drivers for every snapshot — together with the ids whose
 * `session.started` arrived after that answer was sent. Both halves are needed, because the
 * answer is only as fresh as the five-second vault poll: live.ts uses the list to retire a
 * session whose process died and `startedSince` to stop an older list retiring one that started
 * since. Absent until the first vault.sessions, which the server sends on connect.
 */
export interface LiveReport {
  ids: ReadonlySet<string>;
  startedSince: ReadonlySet<string>;
}

export interface AppSnapshot {
  sessions: Readonly<Record<string, SessionView>>;
  others: readonly OtherSession[];
  status: SocketStatus;
  live?: LiveReport;
  maxLiveSessions?: number;
  kanban?: Readonly<Record<string, number>>;
}

export class AppStore {
  private snapshot: AppSnapshot = { sessions: {}, others: [], status: "connecting" };
  private readonly listeners = new Set<() => void>();
  private globalGeneration: string | null = null;
  private globalSeq = -1;
  private kanbanGeneration: string | null = null;
  private kanbanSeq = -1;

  getSnapshot = (): AppSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  dispatch(message: ServerMessage): void {
    if (message.type === "vault.sessions") {
      if (this.globalGeneration !== null && message.generation !== this.globalGeneration) return;
      if (message.generation === this.globalGeneration && message.seq <= this.globalSeq) return;
      this.globalGeneration = message.generation;
      this.globalSeq = message.seq;
      this.commit({
        ...this.snapshot,
        others: message.data.others,
        live: { ids: new Set(message.data.live), startedSince: new Set() },
      });
      return;
    }
    if (message.type === "kanban.changed") {
      if (this.kanbanGeneration !== null && message.generation !== this.kanbanGeneration) return;
      if (message.generation === this.kanbanGeneration && message.seq <= this.kanbanSeq) return;
      this.kanbanGeneration = message.generation;
      this.kanbanSeq = message.seq;
      const workspaceId = message.data.workspace_id;
      if (typeof workspaceId !== "string") return;
      this.commit({
        ...this.snapshot,
        kanban: {
          ...this.snapshot.kanban,
          [workspaceId]: (this.snapshot.kanban?.[workspaceId] ?? 0) + 1,
        },
      });
      return;
    }
    if (message.type === "control.receipt" || message.type === "session.open.refused") return;
    const before = this.snapshot.sessions[message.session_id];
    if (message.type === "history.page") {
      if (
        before?.historyLoading &&
        before.generation === message.generation &&
        before.historyRequestId === message.request_id
      )
        this.setView(message.session_id, applyHistoryPage(before, message.data));
      return;
    }
    if (message.type === "session.snapshot") {
      const view = applySnapshot(before ?? emptyView(message.session_id), message);
      const maxLiveSessions = message.data.state.max_live_sessions;
      const ids = new Set(this.snapshot.live?.ids);
      const startedSince = new Set(this.snapshot.live?.startedSince);
      if (message.data.state.live) startedSince.add(message.session_id);
      else {
        ids.delete(message.session_id);
        startedSince.delete(message.session_id);
      }
      this.commit({
        ...this.snapshot,
        sessions: { ...this.snapshot.sessions, [message.session_id]: view },
        live: { ids, startedSince },
        maxLiveSessions:
          typeof maxLiveSessions === "number" &&
          Number.isSafeInteger(maxLiveSessions) &&
          maxLiveSessions > 0
            ? maxLiveSessions
            : 3,
      });
      return;
    }
    if (message.type === "session.synced") {
      if (
        !before ||
        before.generation !== message.generation ||
        before.requestId !== message.request_id ||
        before.seq > message.seq
      )
        return;
      this.setView(message.session_id, { ...before, seq: message.seq, synced: true });
      return;
    }
    const view = before ?? emptyView(message.session_id);
    const after = applyEnvelope(view, message);
    if (after === view) return;
    const next: AppSnapshot = {
      ...this.snapshot,
      sessions: { ...this.snapshot.sessions, [message.session_id]: after },
    };
    const report = this.snapshot.live;
    // A session.started that lands after the last vault.sessions is newer than that list, so the
    // list says nothing about this process and live.ts must not read it as saying the process is
    // gone. Before the first list there is nothing to outrank, hence nothing to remember.
    if (message.type === "session.started" && report) {
      const startedSince = new Set(report.startedSince).add(message.session_id);
      next.live = { ids: report.ids, startedSince };
    }
    this.commit(next);
  }

  setStatus(status: SocketStatus): void {
    if (status === "connecting" || status === "reconnecting") {
      this.globalGeneration = null;
      this.globalSeq = -1;
      this.kanbanGeneration = null;
      this.kanbanSeq = -1;
    }
    if (status !== this.snapshot.status) this.commit({ ...this.snapshot, status });
  }

  /** Apply local tracking or history-request state to one session. */
  update(sessionId: string, change: (view: SessionView) => SessionView): void {
    const view = this.snapshot.sessions[sessionId] ?? emptyView(sessionId);
    const next = change(view);
    if (next !== view) this.setView(sessionId, next);
  }

  /** Forget a session's view (the user closed it). */
  remove(sessionId: string): void {
    if (!(sessionId in this.snapshot.sessions)) return;
    const { [sessionId]: _dropped, ...sessions } = this.snapshot.sessions;
    this.commit({ ...this.snapshot, sessions });
  }

  private setView(sessionId: string, view: SessionView): void {
    this.commit({ ...this.snapshot, sessions: { ...this.snapshot.sessions, [sessionId]: view } });
  }

  private commit(snapshot: AppSnapshot): void {
    this.snapshot = snapshot;
    for (const listener of [...this.listeners]) listener();
  }
}
