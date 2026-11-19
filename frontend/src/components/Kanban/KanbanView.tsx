import { type ReactNode, useEffect, useId, useState } from "react";
import type { BotRow, BotsApi } from "../../clients/messaging/botsApi";
import type { Key } from "../../i18n";
import { t, tf } from "../../i18n/t";
import type { VaultApi, VaultMetadataItem } from "../../state/vaultApi";
import { BotTag, type ThreadBot } from "../Bots/BotThreadParts";
import { botName, type SeatId, seatFace, teamRoster } from "../Bots/team";
import { READINESS_TEXT, type TeamReadiness } from "../Bots/teamReadiness";
import { Icon, type IconName } from "../Icon/Icon";
import { Loading } from "../Loading/Loading";
import "./Kanban.css";
import { KanbanNeedsYou, type NeedsYouItem, updatedText } from "./KanbanNeedsYou";
import { type CardRead, readCard } from "./kanbanBody";
import {
  KANBAN_BATCH,
  KANBAN_READ_CAP,
  type KanbanCard,
  type KanbanLaneId,
  type KanbanListing,
  kanbanAttention,
  kanbanBodyRefs,
  kanbanLanes,
  kanbanListing,
  kanbanNeedsYou,
  kanbanReadRefs,
  type UnreadCode,
  wakeTarget,
} from "./kanbanCards";
import { type KanbanWrite, useCardAnswer, type WakeResult } from "./useCardAnswer";

export interface KanbanViewProps {
  api: Pick<VaultApi, "entries" | "metadata"> & Partial<Pick<VaultApi, "note">>;
  workspaceId: string;
  onOpenNote(ref: string): void;
  botsApi?: Pick<BotsApi, "list">;
  readiness: TeamReadiness;
  onOpenSeat(seat: SeatId): void;
  onRefresh(): void;
  write?: KanbanWrite;
  onWake?: (bot: BotRow, text: string) => Promise<WakeResult>;
  nowMs?: number;
}

type State =
  | { phase: "loading" }
  | { phase: "error" }
  | {
      phase: "ready";
      workspaceId: string;
      listing: KanbanListing;
      items: ReadonlyMap<string, VaultMetadataItem>;
      refreshing: boolean;
      bodies: ReadonlyMap<string, CardRead>;
      reading: boolean;
    };

const LANE_NAME: Record<KanbanLaneId, Key> = {
  todo: "kanban.lane.todo",
  working: "kanban.lane.working",
  blocked: "kanban.lane.blocked",
  review: "kanban.lane.review",
  done: "kanban.lane.done",
  cancelled: "kanban.lane.cancelled",
  other: "kanban.lane.other",
  unread: "kanban.lane.unread",
};
const GLYPH: Record<KanbanLaneId, IconName> = {
  todo: "statusTodo",
  working: "statusWorking",
  blocked: "statusBlocked",
  review: "statusReview",
  done: "statusDone",
  cancelled: "close",
  other: "statusOther",
  unread: "statusUnread",
};
const UNREAD_TEXT: Record<UnreadCode, Key> = {
  unsupported: "kanban.unread.unsupported",
  truncated: "kanban.unread.truncated",
  changed: "kanban.unread.changed",
  unreadable: "kanban.unread.unreadable",
  stopped: "kanban.unread.stopped",
  retry: "kanban.unread.retry",
  name: "kanban.unread.name",
  cap: "kanban.unread.cap",
};

function coverageText({ reasons, omitted }: KanbanListing): string {
  const detail = [
    reasons.join(", "),
    omitted > 0 ? tf("kanban.coverage.omitted", { count: omitted }) : "",
  ]
    .filter(Boolean)
    .join("; ");
  return detail ? tf("kanban.coverage.detail", { detail }) : t("kanban.coverage");
}

function cardNote(card: KanbanCard, laneId: KanbanLaneId): string | null {
  if (card.unread)
    return tf(UNREAD_TEXT[card.unread.code], {
      reason: card.unread.reason ?? "",
      cap: KANBAN_READ_CAP,
    });
  return laneId === "other" && card.status !== null
    ? tf("kanban.card.status", { status: card.status })
    : null;
}

/** A slug naming a seat exactly is that seat's Bot, else the seat's face; any other slug is text. */
function ownerBot(slug: string, bots: readonly BotRow[], workspaceId: string): ThreadBot | null {
  const entry = teamRoster(bots, workspaceId).seats.find(({ seat }) => seat.seat === slug);
  if (!entry) return null;
  const { bot, seat } = entry;
  if (bot)
    return {
      key: bot.bot_id,
      name: botName(bot),
      profileId: bot.profile_id,
      identity: bot.profile?.identity ?? null,
    };
  return { key: `seat:${seat.seat}`, name: seat.name, ...seatFace(seat.seat) };
}

/** A card's owner markup: "No owner", or a label plus each owner's Bot tag or plain slug.
 * Shared by a lane card and its Needs-you item so the two read the same. */
function ownerTags(card: KanbanCard, bots: readonly BotRow[], workspaceId: string): ReactNode {
  if (card.owners.length === 0) return t("kanban.card.no_owner");
  return (
    <>
      <span>{t(card.owners.length === 1 ? "kanban.card.owner" : "kanban.card.owners")}</span>
      {card.owners.map((slug, slot) => {
        const bot = ownerBot(slug, bots, workspaceId);
        // A card can list a slug twice, so the slug alone is no key.
        const key = `${slug}-${slot}`;
        return bot ? <BotTag key={key} bot={bot} /> : <span key={key}>{slug}</span>;
      })}
    </>
  );
}

function focusHeading(node: HTMLHeadingElement | null) {
  node?.focus();
}

export function KanbanView({
  api,
  workspaceId,
  onOpenNote,
  botsApi,
  readiness,
  onOpenSeat,
  onRefresh,
  write,
  onWake,
  nowMs,
}: KanbanViewProps) {
  const id = useId();
  const [requested, setRequested] = useState(0);
  const [state, setState] = useState<State>({ phase: "loading" });
  const [bots, setBots] = useState<BotRow[]>([]);
  const { notice, saving, lockedRef, answer, undo } = useCardAnswer({
    write: write ?? null,
    ...(onWake === undefined
      ? {}
      : {
          // F11: the ref (a Bot-writable file name) never enters the message sent in the
          // owner's voice — the fixed text says only that an answer is waiting.
          wake: (bot: BotRow) => onWake(bot, t("kanban.wake.message")),
        }),
    onSettled: () => setRequested((n) => n + 1),
    ...(nowMs === undefined ? {} : { nowMs }),
  });
  // biome-ignore lint/correctness/useExhaustiveDependencies: requested is an explicit read request
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    // A re-read of the same workspace keeps its lanes; another workspace starts empty.
    setState((now) =>
      now.phase === "ready" && now.workspaceId === workspaceId
        ? { ...now, refreshing: true }
        : { phase: "loading" },
    );
    (async () => {
      let listing: KanbanListing;
      try {
        listing = kanbanListing(await api.entries(workspaceId, undefined, signal));
      } catch {
        if (!signal.aborted) setState({ phase: "error" });
        return;
      }
      const items = new Map<string, VaultMetadataItem>();
      const refs = kanbanReadRefs(listing.refs);
      try {
        for (
          let from = 0;
          api.metadata && !signal.aborted && from < refs.length;
          from += KANBAN_BATCH
        ) {
          const batch = await api.metadata(
            workspaceId,
            refs.slice(from, from + KANBAN_BATCH),
            signal,
          );
          for (const item of batch.items) items.set(item.ref, item);
        }
      } catch {
        // A failed batch stops the reads; kanbanLanes labels the unread cards.
      }
      if (signal.aborted) return;
      const lanes = kanbanLanes(listing, items, nowMs ?? Date.now());
      setState((now) => ({
        phase: "ready",
        workspaceId,
        listing,
        items,
        refreshing: false,
        // A same-workspace refresh keeps what was already read; a new workspace starts empty.
        bodies: now.phase === "ready" && now.workspaceId === workspaceId ? now.bodies : new Map(),
        reading: api.note !== undefined,
      }));
      const note = api.note;
      if (!note) return;
      // One body at a time, needs-you first (kanbanBodyRefs' order and KANBAN_BODY_CAP): a
      // throw or an unparseable body stops the rest, so a stuck or bad read never spins forever.
      for (const ref of kanbanBodyRefs(lanes, listing)) {
        if (signal.aborted) return;
        let raw: Record<string, unknown>;
        try {
          raw = await note(workspaceId, ref, signal);
        } catch {
          break;
        }
        const read = readCard(raw);
        if (read === null) break;
        if (signal.aborted) return;
        setState((now) =>
          now.phase === "ready" ? { ...now, bodies: new Map(now.bodies).set(ref, read) } : now,
        );
      }
      if (!signal.aborted)
        setState((now) => (now.phase === "ready" ? { ...now, reading: false } : now));
    })();
    return () => controller.abort();
  }, [api, workspaceId, requested]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: requested is an explicit read request
  useEffect(() => {
    if (!botsApi) return;
    const controller = new AbortController();
    const { signal } = controller;
    botsApi.list(signal).then(
      (list) => {
        if (!signal.aborted) setBots(list.bots);
      },
      () => {
        if (!signal.aborted) setBots([]);
      },
    );
    return () => controller.abort();
  }, [botsApi, requested]);

  const ready = state.phase === "ready" && state.workspaceId === workspaceId ? state : null;
  const lanes = ready ? kanbanLanes(ready.listing, ready.items, nowMs ?? Date.now()) : [];
  const counts = ready ? kanbanAttention(ready.listing, lanes) : null;
  const needsYouItems: NeedsYouItem[] = ready
    ? kanbanNeedsYou(lanes, ready.listing).map((card) => {
        const target = wakeTarget(card.owners, bots, workspaceId);
        return {
          card,
          read: ready.bodies.get(card.ref) ?? null,
          ownerTags: ownerTags(card, bots, workspaceId),
          botName: target && "bot" in target ? target.name : null,
        };
      })
    : [];

  return (
    <section className="kanban kanban-page" aria-labelledby={`${id}-title`}>
      <header className="page-head">
        <h1 className="page-title" id={`${id}-title`} tabIndex={-1} ref={focusHeading}>
          {t("kanban.title")}
        </h1>
        <p className="page-intro">{t("kanban.intro")}</p>
        <div className="page-actions">
          <button
            type="button"
            className="btn alt"
            onClick={() => {
              onRefresh();
              setRequested((value) => value + 1);
            }}
          >
            {t("kanban.refresh")}
          </button>
        </div>
      </header>
      {state.phase === "loading" || (state.phase === "ready" && !ready) ? (
        <Loading>{t("kanban.loading")}</Loading>
      ) : null}
      {state.phase === "error" ? <p role="alert">{t("kanban.error")}</p> : null}
      {ready ? (
        <>
          {ready.listing.partial ? <p role="status">{coverageText(ready.listing)}</p> : null}
          <div inert={ready.refreshing}>
            <KanbanNeedsYou
              items={needsYouItems}
              counts={counts}
              reading={ready.reading}
              canWrite={write !== undefined}
              saving={saving}
              lockedRef={lockedRef}
              notice={notice}
              onAnswer={(item, value) => {
                if (!item.read) return;
                void answer(
                  item.card,
                  item.read,
                  wakeTarget(item.card.owners, bots, workspaceId),
                  value,
                );
              }}
              onUndo={() => {
                void undo();
              }}
              onOpenNote={onOpenNote}
            />
          </div>
          {ready.listing.refs.length === 0 ? (
            <div className="kanban-empty">
              <p>{t("kanban.empty")}</p>
              {readiness === "missing" ? <p>{READINESS_TEXT.missing}</p> : null}
              {readiness === "ready" ? <p>{t("kanban.empty.ask")}</p> : null}
              {readiness === "ready" ? (
                <button type="button" className="btn alt sm" onClick={() => onOpenSeat("chief")}>
                  {t("kanban.open_chief")}
                </button>
              ) : null}
            </div>
          ) : null}
          <div className="kanban-lanes" inert={ready.refreshing} aria-busy={ready.refreshing}>
            {lanes.map((lane) => {
              const name = t(LANE_NAME[lane.id]);
              return (
                <section
                  key={lane.id}
                  className="kanban-lane"
                  data-stage={lane.id}
                  aria-labelledby={`${id}-${lane.id}`}
                >
                  <h3
                    id={`${id}-${lane.id}`}
                    aria-label={tf("kanban.lane.heading", { lane: name, count: lane.cards.length })}
                  >
                    <span className="kanban-lane-glyph">
                      <Icon name={GLYPH[lane.id]} size={14} />
                    </span>
                    {name}
                    <span className="kanban-count">{lane.cards.length}</span>
                  </h3>
                  {lane.cards.length === 0 ? (
                    <p className="kanban-lane-empty">{t("kanban.lane.empty")}</p>
                  ) : (
                    <ul>
                      {lane.cards.map((card, index) => {
                        const refId = `${id}-${lane.id}-${index}`;
                        const note = cardNote(card, lane.id);
                        const body = ready.bodies.get(card.ref) ?? null;
                        const done = body?.body.done ?? null;
                        return (
                          <li key={card.ref} className="kanban-card">
                            <button
                              type="button"
                              className="btn txt"
                              aria-describedby={refId}
                              onClick={() => onOpenNote(card.ref)}
                            >
                              {card.title}
                            </button>
                            {lane.id === "blocked" || lane.id === "review" ? (
                              <span className="kanban-badge">{t("kanban.card.needs_you")}</span>
                            ) : null}
                            {note ? <p className="kanban-card-note">{note}</p> : null}
                            {body?.body.now ? (
                              <p className="kanban-card-note">{body.body.now}</p>
                            ) : null}
                            {card.unread ? null : (
                              <p className="kanban-card-owners">
                                {ownerTags(card, bots, workspaceId)}
                              </p>
                            )}
                            {card.ageDays === null && !done ? null : (
                              <p className="kanban-card-meta">
                                {card.ageDays === null ? null : (
                                  <span>{updatedText(card.ageDays)}</span>
                                )}
                                {card.stale ? (
                                  <span className="kanban-stale">{t("kanban.card.stale")}</span>
                                ) : null}
                                {done ? (
                                  <span>
                                    {tf("kanban.card.done_of", {
                                      done: done.done,
                                      total: done.total,
                                    })}
                                  </span>
                                ) : null}
                              </p>
                            )}
                            <small id={refId} className="kanban-card-ref">
                              {card.ref}
                            </small>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              );
            })}
          </div>
        </>
      ) : null}
    </section>
  );
}
