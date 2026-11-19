import {
  type ReactNode,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { AssetsApi } from "../../clients/media/assetsApi";
import type { BotsApi } from "../../clients/messaging/botsApi";
import { t, tf } from "../../i18n/t";
import {
  type BotThreadStore,
  isSteerWaiting,
  MAX_THREAD_MEMBERS,
  type TeamThread,
  type ThreadMember,
  type ThreadSnapshot,
  threadDirective,
  threadDisplayOrder,
  threadMembers,
  threadOptions,
  threadReply,
  threadTurn,
} from "../../state/botThreads";
import { readHandoff } from "../../state/threadHandoff";
import { isStepLabel, latestNote, threadPost } from "../../state/threadPosts";
import type { SessionView, Turn } from "../../state/view";
import { IdentityContext, useIdentity } from "../Avatar/identity";
import type { ComposerActivity } from "../Composer/Composer";
import { MediaComposer } from "../Composer/MediaComposer";
import { ConfirmDialog } from "../Dialog/ConfirmDialog";
import { Icon } from "../Icon/Icon";
import { Loading } from "../Loading/Loading";
import { BotFace } from "./BotFace";
import { BotRecipient, BotTag, BotThreadListFaces, BotThreadStrip } from "./BotThreadParts";
import { BotHuesContext, botHue } from "./botHues";
import "./BotThreadWorkspace.css";

function focusHeading(node: HTMLHeadingElement | null) {
  node?.focus();
}

function latestMessageAt(thread: TeamThread): string | undefined {
  let latestAt: string | undefined;
  let latestTime = Number.NEGATIVE_INFINITY;
  for (const message of thread.messages) {
    const time = Date.parse(message.at);
    if (Number.isFinite(time) && time > latestTime) {
      latestAt = message.at;
      latestTime = time;
    }
  }
  return latestAt;
}

export function BotThreadWorkspace({
  api,
  store,
  snapshot,
  workspaceId,
  canStart,
  canSend,
  sendBlockedReason,
  sessions,
  onOpenSession,
  renderTurn,
  renderCards,
  assets,
  activityOf,
}: {
  api: BotsApi;
  store: BotThreadStore;
  snapshot: ThreadSnapshot;
  workspaceId: string | null;
  canStart: boolean;
  canSend: boolean;
  sendBlockedReason: string;
  sessions: Readonly<Record<string, SessionView>>;
  onOpenSession(sessionId: string): void;
  renderTurn(turn: Turn, sessionId: string): ReactNode;
  renderCards(view: SessionView): ReactNode;
  assets?: AssetsApi | undefined;
  activityOf(sessionId: string | null): ComposerActivity;
}) {
  const id = useId();
  const { hues } = useContext(BotHuesContext);
  const identity = useIdentity();
  const [options, setOptions] = useState<ThreadMember[] | null>(null);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [creating, setCreating] = useState(false);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set(["chief", "checker"]));
  const [first, setFirst] = useState("");
  const [showMembers, setShowMembers] = useState(true);
  const [pendingRemove, setPendingRemove] = useState<{
    id: string;
    title: string;
    hasDraft: boolean;
  } | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removedTitle, setRemovedTitle] = useState<string | null>(null);
  const removing = useRef(false);
  const focusAfterRemove = useRef<string | null | undefined>(undefined);
  const log = useRef<HTMLOListElement | null>(null);
  const following = useRef(true);
  const [behind, setBehind] = useState(false);
  const selectedRow = useRef<HTMLButtonElement | null>(null);
  const newButton = useRef<HTMLButtonElement | null>(null);
  const threads = threadDisplayOrder(snapshot.threads, workspaceId);
  const thread = threads.find((row) => row.id === snapshot.selectedId) ?? threads[0];
  const participants = options
    ? threadOptions(creating ? undefined : thread, options)
    : creating
      ? []
      : (thread?.members ?? []);
  const enabled = thread?.members.filter((member) => member.enabled) ?? [];
  const running = snapshot.busy;
  const text = thread?.draft ?? "";
  const sourceReady = !thread?.sourceStatus || thread.sourceStatus === "ready";
  const firstKey = enabled.find((member) => member.key === first)?.key ?? enabled[0]?.key ?? "";
  // biome-ignore lint/correctness/useExhaustiveDependencies: a different thread starts at its latest message
  useLayoutEffect(() => {
    following.current = true;
    setBehind(false);
  }, [thread?.id, creating]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: the selected row ref changes with the thread ID
  useLayoutEffect(() => {
    if (!creating) selectedRow.current?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [thread?.id, creating]);
  useLayoutEffect(() => {
    const removedId = focusAfterRemove.current;
    if (
      removedId === undefined ||
      pendingRemove ||
      snapshot.threads.some((row) => row.id === removedId)
    )
      return;
    if (threads.length && !creating) selectedRow.current?.focus();
    else newButton.current?.focus();
    focusAfterRemove.current = undefined;
  }, [creating, pendingRemove, snapshot.threads, threads.length]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: session deltas and new messages can change the log's height
  useLayoutEffect(() => {
    if (following.current && log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [sessions, thread?.messages.length, thread?.id, creating]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: refresh explicitly reloads participants
  useEffect(() => {
    setOptions(null);
    setError(false);
    if (!workspaceId || !canStart) return;
    const controller = new AbortController();
    api.list(controller.signal).then(
      (answer) => {
        if (!controller.signal.aborted) setOptions(threadMembers(answer.bots, workspaceId));
      },
      () => {
        if (!controller.signal.aborted) setError(true);
      },
    );
    return () => controller.abort();
  }, [api, workspaceId, canStart, refresh]);

  function choose(option: ThreadMember) {
    if (!creating && thread) {
      store.toggle(thread.id, option);
      return;
    }
    setChosen((previous) => {
      const next = new Set(previous);
      if (next.has(option.key)) next.delete(option.key);
      else if (next.size < MAX_THREAD_MEMBERS) next.add(option.key);
      return next;
    });
  }

  /** A member's assigned hue, for the elements this component colours directly (BotThreadParts'
   * own pieces resolve their hue themselves). Undefined, the `data-bot-hue` attribute is omitted. */
  function hueOf(bot: Pick<ThreadMember, "profileId" | "identity" | "name"> | undefined) {
    return bot ? botHue(bot.profileId, bot.identity?.name ?? bot.name, hues) : undefined;
  }
  const threadRunning = Boolean(thread && thread.phase === "running" && running);
  const workingMember = thread
    ? thread.members.find((member) => member.key === thread.activeMember)
    : undefined;
  const to = enabled.find((member) => member.key === firstKey) ?? enabled[0];
  const speaker = (threadRunning ? workingMember : undefined) ?? to;
  const activity: ComposerActivity = threadRunning
    ? workingMember?.sessionId
      ? activityOf(workingMember.sessionId)
      : { state: "active", label: t("bots.thread.preparing") }
    : activityOf(null);
  const needsYouKeys = new Set(
    (thread?.members ?? [])
      .filter((member) => member.sessionId && sessions[member.sessionId]?.cards.length)
      .map((member) => member.key),
  );
  const newDisabledReason = !canStart
    ? `${id}-new-no-vault`
    : snapshot.loading
      ? `${id}-new-loading`
      : snapshot.error
        ? `${id}-new-error`
        : snapshot.threads.length >= 20
          ? `${id}-new-capacity`
          : undefined;
  /** The Send button's old disabled list, `running` (another thread busy) included: this is only
   * read while `!threadRunning`, where a global `running` can only mean another thread. */
  function sendDisabled(current: TeamThread): boolean {
    return (
      running ||
      snapshot.loading ||
      !!snapshot.error ||
      !canStart ||
      !canSend ||
      !sourceReady ||
      !text.trim() ||
      !enabled.length ||
      current.messages.length >= 200
    );
  }
  /** Why Send is blocked, for the cases the UI does not already explain with its own status line. */
  function sendBlocked(current: TeamThread): string | undefined {
    if (!canSend) return sendBlockedReason;
    if (!enabled.length) return t("bots.thread.noBots");
    if (running) return t("bots.thread.otherRunning");
    if (current.messages.length >= 200) return t("bots.thread.capacity");
    if (!text.trim()) return t("bots.thread.needsText");
    return undefined;
  }
  /** Why Steer is blocked; `submitDisabled` while running is simply whether this has an answer. */
  function steerBlocked(current: TeamThread): string | undefined {
    if (!store.steerable(current.id)) return t("bots.thread.steerWait");
    if ((current.attachments ?? []).length) return t("bots.thread.steerFiles");
    if (current.messages.length > 198) return t("bots.thread.capacity");
    return undefined;
  }

  /** A finished turn's no-post or failed-reply line: face, name, then ` · ${message}`. */
  function pendingLine(member: ThreadMember | undefined, message: string): ReactNode {
    return (
      <p className="bot-thread-pending" data-bot-hue={hueOf(member)}>
        {member ? (
          <BotFace
            profileId={member.profileId}
            identity={member.identity}
            name={member.name}
            size={36}
            decorative
          />
        ) : null}
        <b className="bot-thread-pending-name">{member?.name ?? "Bot"}</b>
        {` · ${message}`}
      </p>
    );
  }

  /** The Bot side of a log entry once its turn exists: a running line with no `renderTurn` call,
   * or the finished reply, its failed/no-post fallback, and progress notes. */
  function botTurnBody(member: ThreadMember | undefined, turn: Turn, sessionId: string): ReactNode {
    if (turn.status === "running") {
      const label = activityOf(sessionId).label;
      const note = latestNote(turn);
      return (
        <p className="bot-thread-pending bot-thread-working" data-bot-hue={hueOf(member)}>
          {member ? (
            <BotFace
              profileId={member.profileId}
              identity={member.identity}
              name={member.name}
              size={36}
              decorative
              state="working"
            />
          ) : null}
          <b className="bot-thread-pending-name">{member?.name ?? "Bot"}</b>
          {` · ${isStepLabel(label) ? t("bots.thread.working") : label}`}
          {note ? <span className="bot-thread-note">{note}</span> : null}
        </p>
      );
    }
    const { post, notes } = threadPost(turn);
    const result = readHandoff(threadReply(turn));
    return (
      <>
        {post.blocks.length > 0 || turn.status === "cancelled" ? (
          <div className="bot-thread-reply" data-bot-hue={hueOf(member)}>
            {renderTurn(post, sessionId)}
          </div>
        ) : turn.status === "error" ? (
          pendingLine(member, t("bots.thread.replyFailed"))
        ) : result === null ? (
          pendingLine(member, t("bots.thread.noPost"))
        ) : null}
        {notes.length > 0 ? (
          <details className="bot-thread-notes">
            <summary>{tf("bots.thread.notes", { count: notes.length })}</summary>
            <ul>
              {notes.map((note) => (
                <li key={note.id}>{note.text}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </>
    );
  }

  return (
    <section className="bot-thread-workspace" aria-labelledby={`${id}-title`}>
      <header className="page-head">
        <h1 className="page-title" id={`${id}-title`} ref={focusHeading} tabIndex={-1}>
          {t("bots.thread.page")}
        </h1>
        <p className="page-intro">{t("bots.thread.intro")}</p>
        <div className="page-actions">
          <button
            type="button"
            className="btn txt sm"
            disabled={running || snapshot.loading}
            onClick={() => store.reload()}
          >
            {t("bots.thread.reload")}
          </button>
        </div>
      </header>
      {!canStart ? (
        <p id={`${id}-new-no-vault`} role="status">
          {t("bots.thread.noVault")}
        </p>
      ) : null}
      {snapshot.loading ? (
        <Loading id={`${id}-new-loading`}>{t("bots.thread.loading")}</Loading>
      ) : null}
      {snapshot.error ? (
        <p id={`${id}-new-error`} role="alert">
          {t(snapshot.error)}
        </p>
      ) : null}
      <nav className="bot-thread-tabs" aria-label={t("bots.thread.list")}>
        <button
          type="button"
          className="btn go sm"
          ref={newButton}
          aria-disabled={newDisabledReason ? true : undefined}
          aria-describedby={newDisabledReason}
          onClick={() => {
            if (newDisabledReason) return;
            setCreating(true);
            setShowMembers(true);
          }}
        >
          <Icon name="plus" />
          {t("bots.thread.new")}
        </button>
        {threads.map((row) => {
          const enabledMembers = row.members.filter((member) => member.enabled);
          const needsYou = row.members.some(
            (member) => member.sessionId && sessions[member.sessionId]?.cards.length,
          );
          const latestAt = latestMessageAt(row);
          const latestTime = latestAt
            ? new Date(latestAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
            : undefined;
          return (
            <div className="bot-thread-workspace-row" key={row.id}>
              <button
                type="button"
                className="btn alt sm"
                aria-current={row.id === thread?.id && !creating ? "page" : undefined}
                ref={row.id === thread?.id && !creating ? selectedRow : undefined}
                onClick={() => {
                  store.select(row.id);
                  setCreating(false);
                }}
              >
                <BotThreadListFaces bots={enabledMembers} />
                <span className="bot-thread-row-title">
                  {row.title || t("bots.thread.untitled")}
                </span>
                <span className="bot-thread-row-meta">
                  <span className="bot-thread-phase">{t(`bots.thread.${row.phase}`)}</span>
                  {latestAt && latestTime ? (
                    <time className="bot-thread-row-time" dateTime={latestAt}>
                      {tf("bots.thread.lastActivity", { time: latestTime })}
                    </time>
                  ) : null}
                </span>
              </button>
              {needsYou ? (
                <span className="bot-thread-row-needs">{t("bots.thread.needsYou")}</span>
              ) : null}
            </div>
          );
        })}
      </nav>
      <div className={`bot-thread-layout${showMembers ? "" : " bot-thread-layout-folded"}`}>
        <div className="bot-thread-main">
          <div className="bot-thread-toolbar">
            <h2>
              {creating || !thread
                ? t("bots.thread.choose")
                : thread.title || t("bots.thread.untitled")}
            </h2>
            <button
              type="button"
              className="btn txt sm"
              aria-expanded={showMembers}
              aria-controls={`${id}-members`}
              onClick={() => setShowMembers((value) => !value)}
            >
              <Icon name="people" />
              {t(showMembers ? "bots.thread.hide" : "bots.thread.show")}
            </button>
            {!creating && thread ? (
              <>
                <span className="bot-thread-toolbar-separator" aria-hidden="true" />
                <button
                  type="button"
                  className="btn txt sm"
                  aria-disabled={running ? true : undefined}
                  aria-describedby={running ? `${id}-remove-blocked` : undefined}
                  onClick={() => {
                    if (running || removing.current) return;
                    setRemovedTitle(null);
                    setPendingRemove({
                      id: thread.id,
                      title: thread.title || t("bots.thread.untitled"),
                      hasDraft: thread.draft.length > 0 || (thread.attachments ?? []).length > 0,
                    });
                  }}
                >
                  <Icon name="trash" />
                  {t("bots.thread.removeAsk")}
                </button>
              </>
            ) : null}
          </div>
          {pendingRemove ? (
            <div className="bot-thread-remove-confirm">
              <ConfirmDialog
                className="bot-thread-remove-dialog"
                label={tf("bots.thread.removeTitle", { title: pendingRemove.title })}
                body={`${tf("bots.thread.removeTitle", { title: pendingRemove.title })}\n${t("bots.thread.removeHelp")}${pendingRemove.hasDraft ? `\n${t("bots.thread.removeDraft")}` : ""}`}
                confirmLabel={t("bots.thread.remove")}
                destructive
                busy={removeBusy}
                onCancel={() => {
                  if (!removing.current) setPendingRemove(null);
                }}
                onConfirm={() => {
                  if (removing.current || snapshot.busy) return;
                  removing.current = true;
                  setRemoveBusy(true);
                  const { id: targetId, title } = pendingRemove;
                  void store.remove(targetId).then((removed) => {
                    if (removed) {
                      focusAfterRemove.current = targetId;
                      setRemovedTitle(title);
                    }
                    setPendingRemove(null);
                    setRemoveBusy(false);
                    removing.current = false;
                  });
                }}
              />
            </div>
          ) : null}
          {running && !creating && thread ? (
            <p id={`${id}-remove-blocked`} className="bot-thread-remove-blocked" role="status">
              {t("bots.thread.removeBlocked")}
            </p>
          ) : null}
          {creating || !thread ? (
            <div className="bot-thread-empty">
              <p>{t("bots.thread.empty")}</p>
              <button
                type="button"
                className="btn go"
                disabled={
                  !canStart ||
                  snapshot.loading ||
                  !!snapshot.error ||
                  !options?.some((row) => chosen.has(row.key)) ||
                  snapshot.threads.length >= 20
                }
                onClick={() => {
                  if (options && store.create(options.filter((row) => chosen.has(row.key))))
                    setCreating(false);
                }}
              >
                {t("bots.thread.create")}
              </button>
            </div>
          ) : (
            <>
              <ol
                className="bot-thread-log"
                aria-label={t("bots.thread.log")}
                ref={log}
                onScroll={(event) => {
                  const node = event.currentTarget;
                  following.current = node.scrollHeight - node.clientHeight - node.scrollTop < 48;
                  setBehind(!following.current);
                }}
              >
                {thread.messages.map((message) => {
                  const member = thread.members.find((row) => row.key === message.memberKey);
                  const turn = threadTurn(message, sessions);
                  if (message.continuation && !turn) return null;
                  const result = readHandoff(threadReply(turn));
                  const target =
                    member?.key !== "checker" && result?.next_gate === "review"
                      ? "checker"
                      : threadDirective(threadReply(turn)).target;
                  const targetMember = thread.members.find((row) => row.key === target);
                  return (
                    <li
                      key={message.id}
                      className={member ? "bot-thread-entry" : "bot-thread-entry bot-thread-user"}
                    >
                      <div className="bot-thread-entry-meta">
                        <time dateTime={message.at}>
                          {new Date(message.at).toLocaleTimeString([], {
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </time>
                        {message.sessionId ? (
                          <button
                            type="button"
                            className="btn txt sm"
                            onClick={() => onOpenSession(message.sessionId as string)}
                          >
                            {t("bots.thread.openChat")}
                          </button>
                        ) : null}
                      </div>
                      {message.memberKey !== null && message.sessionId ? (
                        turn ? (
                          botTurnBody(member, turn, message.sessionId)
                        ) : (
                          <p className="bot-thread-pending" data-bot-hue={hueOf(member)}>
                            {member ? (
                              <BotFace
                                profileId={member.profileId}
                                identity={member.identity}
                                name={member.name}
                                size={36}
                                decorative
                              />
                            ) : null}
                            <b className="bot-thread-pending-name">{member?.name ?? "Bot"}</b>
                            {` · ${t(thread.phase === "running" && thread.activeMember === member?.key ? "bots.thread.preparing" : "bots.thread.noReply")}`}
                          </p>
                        )
                      ) : (
                        <p className="bot-thread-user-text">{message.text}</p>
                      )}
                      {message.steer && isSteerWaiting(message, sessions) ? (
                        <p className="bot-thread-steer-wait">
                          {tf("bots.thread.steerWaiting", { name: workingMember?.name ?? "Bot" })}
                        </p>
                      ) : null}
                      {turn?.status === "success" && target ? (
                        <p className="bot-thread-handoff">
                          {t("bots.thread.handoff")}
                          {" · "}
                          {member ? <BotTag bot={member} /> : "Bot"}
                          {" → "}
                          {targetMember ? <BotTag bot={targetMember} /> : target}
                        </p>
                      ) : null}
                      {turn?.status === "success" && result ? (
                        <details className="bot-thread-result">
                          <summary>{`${result.verdict ? `${result.verdict} · ` : ""}${result.progress}`}</summary>
                          <dl>
                            <dt>{t("bots.thread.objective")}</dt>
                            <dd>{result.objective}</dd>
                            {(
                              [
                                "artifacts",
                                "decisions",
                                "questions",
                                "criteria",
                                "evidence",
                              ] as const
                            ).map((field) =>
                              result[field].length ? (
                                <div key={field}>
                                  <dt>{t(`bots.thread.${field}`)}</dt>
                                  <dd>
                                    <ul>
                                      {[...new Set(result[field])].map((item) => (
                                        <li key={item}>{item}</li>
                                      ))}
                                    </ul>
                                  </dd>
                                </div>
                              ) : null,
                            )}
                          </dl>
                        </details>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
              {behind ? (
                <button
                  type="button"
                  className="btn alt sm"
                  onClick={() => {
                    following.current = true;
                    setBehind(false);
                    if (log.current) log.current.scrollTop = log.current.scrollHeight;
                  }}
                >
                  {t("bots.thread.latest")}
                </button>
              ) : null}
              {thread.members.map((member) => {
                const view = member.sessionId ? sessions[member.sessionId] : undefined;
                return view?.cards.length ? (
                  <section
                    key={member.key}
                    className="bot-thread-questions"
                    aria-label={`${member.name} · ${t("bots.thread.needsYou")}`}
                  >
                    <h3>
                      <BotTag bot={member} /> · {t("bots.thread.needsYou")}
                    </h3>
                    {renderCards(view)}
                  </section>
                ) : null;
              })}
              {thread.note ? (
                <p className="bot-thread-notice" role="status">
                  {t(thread.note)}
                </p>
              ) : null}
              {thread.sourceStatus === "loading" ? (
                <Loading>{t("bots.thread.loadingSources")}</Loading>
              ) : thread.sourceStatus === "unavailable" &&
                thread.note !== "bots.thread.sourceError" ? (
                <p role="status">{t("bots.thread.sourceError")}</p>
              ) : null}
              {/* Keyed like App's chat composer: dictation and other in-box state stay with one
                  thread's draft. */}
              <div key={thread.id} className="bot-thread-composer" data-bot-hue={hueOf(speaker)}>
                <IdentityContext.Provider
                  value={{ ...identity, assistantName: speaker?.name ?? "", unresolved: false }}
                >
                  <MediaComposer
                    api={assets}
                    attachments={thread.attachments ?? []}
                    onAttachments={(attachments) => store.attach(thread.id, attachments)}
                    destinationLabel={to?.name ?? ""}
                    editorKey={thread.id}
                    value={thread.draft}
                    onEdit={(value) => store.edit(thread.id, value)}
                    label={t("bots.thread.prompt")}
                    sendLabel={t("bots.thread.send")}
                    placeholder={tf("bots.thread.talkTo", { name: speaker?.name ?? "" })}
                    maxLength={8000}
                    running={threadRunning}
                    activity={activity}
                    activityLead={
                      <BotThreadStrip
                        bots={enabled}
                        lit={threadRunning ? thread.activeMember : null}
                        needsYou={needsYouKeys}
                      />
                    }
                    recipient={
                      <BotRecipient
                        label={t("bots.thread.to")}
                        bots={enabled}
                        // While this thread runs, Enter steers the working Bot: name it here too.
                        value={(threadRunning ? workingMember?.key : undefined) ?? firstKey}
                        disabled={running}
                        onChange={setFirst}
                      />
                    }
                    submitDisabled={
                      threadRunning ? steerBlocked(thread) !== undefined : sendDisabled(thread)
                    }
                    blockedReason={threadRunning ? steerBlocked(thread) : sendBlocked(thread)}
                    onSend={(value) => {
                      if (threadRunning) void store.steer(thread.id, value);
                      else void store.send(thread.id, value, to?.key, thread.attachments ?? []);
                      return Promise.resolve(true);
                    }}
                    onStop={() => store.stop()}
                    steer
                  />
                </IdentityContext.Provider>
              </div>
              {!enabled.length ? <p role="status">{t("bots.thread.noBots")}</p> : null}
              {!canSend ? <p role="status">{sendBlockedReason}</p> : null}
            </>
          )}
          {removedTitle ? (
            <p role="status">{tf("bots.thread.removed", { title: removedTitle })}</p>
          ) : null}
          <p className="bot-thread-window-note">{t("bots.thread.window")}</p>
          {snapshot.threads.length >= 20 || (thread?.messages.length ?? 0) >= 200 ? (
            <p id={snapshot.threads.length >= 20 ? `${id}-new-capacity` : undefined} role="status">
              {t("bots.thread.capacity")}
            </p>
          ) : null}
        </div>
        <aside
          id={`${id}-members`}
          className="bot-thread-members"
          aria-label={t("bots.thread.participants")}
          hidden={!showMembers}
        >
          <h2>{t("bots.thread.participants")}</h2>
          <p>{t("bots.thread.membersHelp")}</p>
          {error ? (
            <p role="alert">{t("bots.thread.loadError")}</p>
          ) : !options && canStart ? (
            <Loading>{t("bots.thread.loadingBots")}</Loading>
          ) : null}
          <div className="bot-thread-member-list">
            {participants.map((option) => {
              const member = creating
                ? undefined
                : thread?.members.find((row) => row.key === option.key);
              const checked =
                creating || !thread ? chosen.has(option.key) : member?.enabled === true;
              const view = member?.sessionId ? sessions[member.sessionId] : undefined;
              const working = thread?.phase === "running" && thread.activeMember === option.key;
              const status = view?.cards.length
                ? "bots.thread.needsYou"
                : working
                  ? "bots.thread.running"
                  : checked
                    ? "bots.thread.ready"
                    : "bots.thread.off";
              return (
                <label key={option.key} className="bot-thread-member" data-enabled={checked}>
                  <BotFace
                    profileId={option.profileId}
                    identity={option.identity}
                    name={option.name}
                    size={52}
                    decorative
                    {...(working ? { state: "working" } : {})}
                  />
                  <span>
                    <strong>{option.name}</strong>
                    <small>{t(status)}</small>
                  </span>
                  <input
                    type="checkbox"
                    checked={checked}
                    aria-label={option.name}
                    disabled={
                      !canStart ||
                      (!checked &&
                        (creating || !thread ? chosen.size : enabled.length) >= MAX_THREAD_MEMBERS)
                    }
                    onChange={() => choose(option)}
                  />
                </label>
              );
            })}
          </div>
          <button
            type="button"
            className="btn txt sm"
            disabled={!canStart}
            onClick={() => setRefresh((value) => value + 1)}
          >
            {t("bots.thread.refresh")}
          </button>
        </aside>
      </div>
    </section>
  );
}
