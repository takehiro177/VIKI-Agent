"""VIKI's own chats ("missions"): start one for an open item, watch it, stop it.

A mission is a chat VIKI starts for an item and sends one message, the brief (`brief`). Work
`prepare` asks for a plan only: before `host.create` it stores the session's mode as "plan"
(`AppState.remember(session_id, mode="plan")`), which reaches claude only as the existing
`--permission-mode` value `build_argv` writes. Work `run` stores nothing, so the chat takes the
chat defaults. `create` takes the session id alone, so the chat runs in the launch's own folder
with the manager's own root and folder resolvers, as a chat the user opens does. The user's
normal permission prompts govern it: `MissionSession` has no way to answer a card, and nothing
here retries a start or respawns a chat.

A Look mission is an item of a `LOOK_MISSIONS` type (`code_look`), started only for work
`prepare`. Its grant is `grant_look` and its daily limit `limit_look`. Its chat runs in the
trusted code folder named by the `workspace_id` of the item's first `open_code` action: after
every refusal check below and before `remember` and `create`, the injected `roots` port records
the new session's root there (`SessionRoots.prepare`, which checks trust, and `resolve` checks
it again at every spawn). Its brief is fixed text: it names no folder, path, file or diff.

A second look is an item of a `SECOND_LOOKS` type (`long_task_look`, `correction_look`), started
only for work `prepare`. Its grant is `grant_second_look` and its daily limit
`limit_second_look`. It needs no folder or root: like a draft, it stores the plan mode and its
chat runs in the launch's own folder, the vault. Its title is "Second look: " and the chat's
title (the item's title). Its brief is fixed text naming the trigger (the item's type), the
chat's title, the kinds of finding `leave_out` lists, and the notes the item's evidence names
that are vault notes: an exact str that `safe_ref` takes, with a note suffix and no control,
line or paragraph separator, lone surrogate or trailing "…" (`second_looks.admitted`), each
once and in order, at most `MAX_SECOND_LOOK_REFS` and `MAX_SECOND_LOOK_REF_BYTES` UTF-8 bytes in
all, less each one under a Never send folder as `start` reads the settings (a list that cannot
be trusted holds back every one); no other item text. A Never send tag is not judged: the item
holds no note's tags.

A project plan is an item of a `PROJECT_PLANS` type (`stalled_project`, `overdue_tasks`), started
only for work `prepare`. Its grant is `grant_project_plan` and its daily limit
`limit_project_plan`. Like a second look it needs no folder or root: it stores the plan mode and
its chat runs in the launch's own folder, the vault. Its title is "Next steps" or "Overdue tasks",
then ": " and its first evidence label (the project's name) when that is not blank. Its brief is
fixed text for its type around the vault notes its evidence names (`_notes`), as a second look's
are; no other item text.
`owner_chat` says whether a chat is the user's own: no profile binding, session root, routine
attempt, Bot or mission names it.

`start` refuses with a `MissionError` whose code says why: `no_folder` in the chat-only
workspace; `off` while Decision is off or paused, or when the work's grant (a look's: the Look
grant; a second look's or a project plan's: its own grant) does not let this start (VIKI itself
needs "allowed", the user's own start "ask" or "allowed"); `from_mission` for a mission report
or an item naming a mission's chat; `no_work` for an item whose candidate type is not in
`MISSION_TYPES`, unless it is a Look mission, a second look or a project plan asked for
`prepare`, for a project plan whose evidence lists no note (`_notes` is empty, as for a ref over
`MAX_SECOND_LOOK_REF_BYTES` bytes), and for a second look or a project plan once Never send
holds back every note its evidence names; `no_folder` for a look with no canonical folder id or
no `roots` port; `tried`, for
VIKI's own start only, when a stored mission in any state already holds the same scope, item,
work and item state hash (each row keeps the state hash its item had at the start), so VIKI
starts an item's work once per state, across app launches too; when the user stopped a
mission of the same scope and item (stopped, "owner"), whatever its work or state hash, so after
the user's Stop VIKI starts that item no more on its own (the user's own start still may);
and for VIKI's own look, second look or project plan when any stored mission of any scope,
work, state or state hash holds its key; `busy` while `MAX_RUNNING_MISSIONS` are open or the
item has one open, and for VIKI's own start while any mission is open, so VIKI's chats take one
of the places at most and one stays free for the user's start (this also holds a project plan
whose key moved, as its overdue set or stamp does, while the first still waits; the offer is
asked again once that mission ends); `limit` once the
local day's starts of that work (in any state, every scope counted) reach the daily limit, where
looks, second looks, project plans and other missions count apart, by the type of the item each
row's scope and key name in `decision_items` (a row whose item is gone counts as none of the
three); `no_folder` when `roots` answers False or raises, which stores nothing and calls no
host; and `not_sent` when the chat could not be made or did not take the brief, which leaves the
mission failed with reason "not_sent".

An open row is stored only once its chat exists, so no session id is handed out before its
actor; the brief is sent outside the lock, so Stop and Stop all answer while a chat
spawns. Each started chat is watched by a subscriber that notes its first turn end, its last
error code, a close, the cards waiting for the user and whether the user answered one, read
from the same stream events the browser's Approvals and Needs you read. `poll` ends a watched
mission when its turn ends (done, or failed with the error code or turn status), when its chat
closes or its actor is gone (unknown: "closed", or "app_closed" when the app closed it), or once
its deadline, `MISSION_MINUTES` after the start, has passed while no card waits and before the
user has answered one it saw open (stopped: "deadline", interrupting a running turn). While a
card waits the mission waits for the user, and once the user answers a card it saw open
(`request.answered` for that card, not a withdrawal, an exit, or the app's own automatic denial
of a card it never saw) the chat is theirs, so the deadline leaves a prepare mission's plan
card, and the work the user approves, to the user; the first poll that finds that plan
card waiting answers the mission, still running, with reason "ready" (once per mission), so a
report can point the user at it. `stop` and `stop_all` interrupt a running turn, a waiting card
or not, and mark the mission stopped ("owner"), except that a watched one whose turn already
ended or whose chat is gone keeps that ending; the chat stays open. `reconcile`, at startup,
makes every mission left starting or running unknown ("app_closed"). `poll` answers each
mission that ended, by any of these paths, once.

A watched mission that ends quietly frees its chat's process: its turn ended done, or failed
other than by a turn interrupted in the chat, or its brief was not sent, or its deadline passed,
while the user answered no card and none waits. Its ending is written under the lock, and the
next `poll` closes the chat (`host.close`) once the lock is released, so Stop, Stop all and a
start never wait on a close; each close is bounded by `CLOSE_S`, and one that fails or times out
is logged and leaves the chat open. A chat in which a turn runs again by then (someone sent
there after the end) stays open, unless its deadline ended it. The chat stays listed and
reopens from its transcript. A chat the user answered a card in, or stopped, keeps its process,
as the user's own chats do; so do a closed chat and one the app closed.

A second look's or a project plan's outcome is read from its own chat's stream, in memory. Its
first main-thread plan card names the finding's kind (`second_looks.kind_of` on the plan, which
goes nowhere else; "other" for a plan that names none, as a project plan's brief asks for none);
a subagent's plan card counts for nothing. The user's answer is not on the stream, but
its effect is: an approved plan leaves plan mode, so a `status` frame after the plan card reports
another mode, and a kept one does not. When its turn succeeds it ends done with
reason "nothing" if no plan card came, "accepted:<kind>" if a plan card was answered and the mode
left plan, "rejected:<kind>" if one was answered and the mode did not, and as any mission does
if a plan card went unanswered; a turn that does not succeed ends as any mission does. A chat
the app itself closed ("app_closed", after the app denied its waiting cards) ends unknown first,
so an automatic denial never reads as Keep planning.

Keep planning counts as turned down only if that chat then ends without an approval. A look
that ends "rejected:<kind>" while its chat has not closed and is still the host's stays
subscribed, kept in memory. Each `poll` turns a kept look's row into "accepted:<kind>" once a
`status` there reports a mode other than plan, as an approval in a later turn does: one update
of the ended row, in a worker thread, only while it still reads "rejected:<kind>", tried again
at the next poll when the store cannot save it. Once the chat closes or is not the host's any
more before such a status, the look is released and its row stays rejected, as it does after a
restart. None of this is reported. `findings` lists the watched second looks and project plans
whose plan card waits, oldest first; `plans_waiting` counts the watched missions of any kind
whose plan card waits; and `second_look_rows` lists the stored second looks, or with
`types=PROJECT_PLANS` the stored project plans, of a scope for learning, with a kept look's
reason read as "" (neither).

`poll` reports a second look or a project plan when it failed other than by a turn interrupted
in its chat: a turn error, a brief not sent or a chat not made, which uses up the day's start
and holds its key as any start does. It also reports a second look that found nothing ("done",
"nothing"), titled by its chat's title, for one quiet line. Their other ends are quiet, a
project plan's nothing among them, and the ends `reconcile` and `stop` write ("app_closed",
"owner") report no second look and no project plan, watched or not.

Rows live in `decision_missions` across every workspace scope; a row's title and brief are its
`body`, canonical JSON sealed by `protection.protect` under a context naming its scope and
mission id. A body that does not open reads as an empty title and brief. Arguments that are not
valid, `leave_out` naming anything but `FINDING_KINDS` among them, are
`DecisionLoopError("invalid_request")`, an unknown mission `unknown_item`, and a store
or sealing that does not work `unavailable`. Logs hold fixed text and exception type names or
codes only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, get_args

from anyio.to_thread import run_sync

from viki_agent import protection
from viki_agent.api_json import unique_object
from viki_agent.automation.routines import parse_stamp
from viki_agent.conversations.drafts import require_private_store
from viki_agent.decision_loop.agenda import (
    _id,
    _is_key,
    _key,
    _keys,
    _open,
    _plain,
    _require,
    _seal,
    _stamp,
)
from viki_agent.decision_loop.contracts import (
    FINDING_KINDS,
    LOOK_MISSIONS,
    MAX_RUNNING_MISSIONS,
    MAX_SECOND_LOOK_REF_BYTES,
    MAX_SECOND_LOOK_REFS,
    MISSION_MINUTES,
    MISSION_TYPES,
    PROJECT_PLANS,
    SECOND_LOOKS,
    UNTITLED_CHAT,
    DecisionLoopError,
    DecisionSettings,
    Finding,
    ItemRecord,
    LookRow,
    MissionHost,
    MissionRecord,
    MissionSession,
    _canonical_uuid,
)
from viki_agent.decision_loop.second_looks import admitted, kind_of
from viki_agent.decision_loop.state import _rules, _under
from viki_agent.decision_loop.wire import MAX_STARTED, MISSION_WORK
from viki_agent.domain import SCRATCH_WORKSPACE_ID
from viki_agent.events import ErrorCode, TurnStatus
from viki_agent.storage import SessionStore, StateError

log = logging.getLogger(__name__)

PLAN_MODE = "plan"  # the one mode a mission stores, for prepare
MISSION_ERROR_CODES = (
    "limit",
    "busy",
    "off",
    "from_mission",
    "no_work",
    "no_folder",
    "not_sent",
    "tried",
)
OPEN_STATES = ("starting", "running")
BRIEF_EVIDENCE = 10  # the evidence labels a brief lists
BRIEF_CHARS = 300  # the characters a brief keeps of the item's title, "what" and each label
MISSION_BODY_BYTES = 65_536  # the sealed body bound of `decision_missions`
MAX_LOOK_ROWS = 400  # the second-look rows `second_look_rows` reads
CLOSE_S = 15.0  # bounds one chat's close, as `session.CLOSE_TIMEOUT_S` bounds each at app close

_CONTEXT = b"VIKI decision mission v1\x00"
_COLUMNS = (
    "mission_id,scope,session_id,item_key,work,state,reason,created_at,deadline_at,finished_at"
)
_OPEN = "state IN ('starting','running')"
_IN_IDS = "mission_id IN (SELECT value FROM json_each(?))"
_HOW = {
    "prepare": (
        "Plan only. Do not change files; present your plan for approval, opening with what it "
        "will do in one or two plain sentences."
    ),
    "run": "Work in this folder under your normal permission prompts.",
}
_DONE = {"prepare": "a plan for this, ready for approval", "run": "the work done and summarised"}
# A look's brief is fixed: the chat finds the changes itself, so no folder name, path, file list
# or diff leaves the app in it.
_LOOK_GOAL = (
    "Take a second look at this folder's uncommitted changes, untouched for a day or more, and "
    "find what is half-done or risky."
)
_LOOK_HOW = (
    "Look only: read git status, git diff and the files they name, and change nothing. Present "
    "what you found as a plan for approval: first what you found and what you would do about "
    'it, in plain words with at most five numbered steps, then your evidence under "## Details".'
)
_LOOK_DONE = "a short review of these changes, presented as a plan for approval"
# A second look's brief is fixed text around the trigger, the chat's title and the vault notes the
# chat's reply edited: no chat text, plan or diff leaves the app in it.
_SECOND_WORK = {"long_task_look": "a long task", "correction_look": "work the owner corrected"}
_SECOND_HOW = (
    "Look only: read the notes under Evidence, and git status and git diff if this folder is a "
    "Git repository, and change nothing. If you find something worth the owner's time, present "
    'it as a plan for approval. Its first line, before any heading, is exactly "Kind: flaw", '
    '"Kind: better approach" or "Kind: missing piece". Below that line, write for the owner: a '
    "heading that states the finding in plain words, one or two sentences on why it matters, "
    "and what approving does as at most five numbered steps (what changes, what stays, about "
    'how long). Put your evidence, file references and checks last, under "## Details". If '
    "nothing is worth the owner's time, say so in one line and stop without a plan. If the "
    "owner keeps planning, stop without another plan."
)
_SECOND_DONE = "one finding presented as a plan for approval, or one line saying there is none"
_LEAVE_OUT = {"flaw": "flaws", "approach": "better approaches", "missing": "missing pieces"}
# A project plan's brief is fixed text for its type around the one note its evidence names: no
# project name, task text or note text leaves the app in it.
_PLAN_TITLES = {"stalled_project": "Next steps", "overdue_tasks": "Overdue tasks"}
_PLAN_GOAL = {
    "stalled_project": (
        "Propose three next actions for the project whose note is under Evidence; none of its "
        "notes has changed for two weeks or more."
    ),
    "overdue_tasks": (
        "Check the open tasks in the note under Evidence whose due date passed three days ago or "
        "more: which look done, and which need a new date."
    ),
}
_PLAN_HOW = {
    "stalled_project": (
        "Look only: read the note under Evidence, the project's tasks.md if it has one and its "
        "other notes, and change nothing. Present three next actions as a plan for approval: "
        "one sentence on where the project stands, then the three actions, each one a task "
        "line you would add to the project's tasks.md, then your reasons under "
        '"## Details". If the project looks finished or on hold, say so in one line and stop '
        "without a plan. If the owner keeps planning, stop without another plan."
    ),
    "overdue_tasks": (
        "Look only: read the note under Evidence and the project's other notes, and change "
        "nothing. Present one plan for approval that opens with how many of those tasks look "
        "done and how many need a new date, then gives one line per task: tick it if it looks "
        "done, else a new due date. Put what shows each one, and your reasons, under "
        '"## Details". If you cannot tell for any of them, say so in one line and stop without '
        "a plan. If the owner keeps planning, stop without another plan."
    ),
}
_PLAN_DONE = {
    "stalled_project": (
        "three next actions presented as a plan for approval, or one line saying the project "
        "needs none"
    ),
    "overdue_tasks": "one plan for approval, or one line saying you cannot tell",
}
_QUIET = SECOND_LOOKS + PROJECT_PLANS  # the types whose reconcile and Stop ends go unreported
_OWNER = ("stopped", "owner")
_DEADLINE = ("stopped", "deadline")
_NOT_SENT = ("failed", "not_sent")
_STATUSES = frozenset(get_args(TurnStatus))
_ERRORS = frozenset(get_args(ErrorCode))


class MissionError(RuntimeError):
    """A refused start; `code` is one of `MISSION_ERROR_CODES` and its text is the code alone."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ---- the brief ---------------------------------------------------------------------------------


def _line(text: str) -> str:
    """`text` on one line: control characters and runs of white space become one space; at most
    `BRIEF_CHARS` characters."""
    kept = "".join(" " if unicodedata.category(ch) in ("Cc", "Cs") else ch for ch in text)
    return " ".join(kept.split())[:BRIEF_CHARS]


def _notes(item: ItemRecord, held: Sequence[tuple[str, ...]] = ()) -> list[str]:
    """The vault notes a second look's or a project plan's evidence names, each once and in
    order, stopping at `MAX_SECOND_LOOK_REFS` or before `MAX_SECOND_LOOK_REF_BYTES` UTF-8 bytes
    in all, less those under a Never send folder of `held` (`state._rules` segments)."""
    notes: list[str] = []
    size = 0
    for ref in (entry.ref for entry in item.body.evidence):
        if not admitted(ref) or ref in notes or _under(ref, held):
            continue
        size += len(ref.encode("utf-8"))
        if len(notes) == MAX_SECOND_LOOK_REFS or size > MAX_SECOND_LOOK_REF_BYTES:
            break
        notes.append(ref)
    return notes


def _chat_title(item: ItemRecord) -> str:
    """A second look's chat title: the item's, on one line, or `UNTITLED_CHAT`."""
    return _line(item.body.title) or UNTITLED_CHAT


def _subject(item: ItemRecord) -> str:
    """A project plan's project: its first evidence label on one line, or ""."""
    return _line(item.body.evidence[0].label) if item.body.evidence else ""


def _second_goal(item: ItemRecord) -> str:
    title = _chat_title(item)
    where = "another chat" if title == UNTITLED_CHAT else f'the chat "{title}"'
    return (
        f"Take a second look at {_SECOND_WORK[item.candidate]} that just finished in {where}, "
        "and find at most one flaw, better approach or missing piece worth the owner's time."
    )


def _left_out(leave_out: Sequence[str]) -> str:
    """The sentence naming the kinds in `leave_out`, in `FINDING_KINDS` order, or ""."""
    words = [_LEAVE_OUT[kind] for kind in FINDING_KINDS if kind in leave_out]
    if not words:
        return ""
    listed = words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"
    return f" Leave out {listed}."


def brief(
    item: ItemRecord,
    work: str,
    deadline: datetime | None = None,
    leave_out: Sequence[str] = (),
    *,
    held: Sequence[tuple[str, ...]] = (),
) -> str:
    """The one message a mission's chat gets: the goal is the item's "what", the evidence its
    first `BRIEF_EVIDENCE` labels, each on its own line, and the deadline `deadline` in local
    time, or "none". A Look mission's goal, how and done lines are fixed and it lists no
    evidence, so none of its item's text reaches the brief. A second look's are fixed around its
    trigger and chat title, its evidence is its vault notes (`_notes`) less those under a Never
    send folder of `held`, and its how line names the kinds in `leave_out`. A project plan's are
    fixed for its type and its evidence is its vault notes, held ones left out too."""
    if item.candidate in PROJECT_PLANS:
        goal, labels = _PLAN_GOAL[item.candidate], _notes(item, held)
        how, done = _PLAN_HOW[item.candidate], _PLAN_DONE[item.candidate]
    elif item.candidate in SECOND_LOOKS:
        goal, labels = _second_goal(item), _notes(item, held)
        how, done = _SECOND_HOW + _left_out(leave_out), _SECOND_DONE
    elif item.candidate in LOOK_MISSIONS:
        goal, labels, how, done = _LOOK_GOAL, [], _LOOK_HOW, _LOOK_DONE
    else:
        goal, how, done = _line(item.body.what), _HOW[work], _DONE[work]
        labels = [label for label in map(_line, (e.label for e in item.body.evidence)) if label]
    evidence = "".join(f"\n- {label}" for label in labels[:BRIEF_EVIDENCE]) or " none"
    when = "none" if deadline is None else deadline.astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        "VIKI started this chat (Decision).\n"
        f"Goal: {goal}\n"
        f"Evidence:{evidence}\n"
        f"How: {how}\n"
        f"Budget: this turn, at most {MISSION_MINUTES} minutes.\n"
        f"Done when: {done}\n"
        f"Deadline: {when}\n"
        "Report: lead with the result in one or two plain sentences; details after.\n"
        "Do not start other chats, change VIKI Agent settings, or edit AGENTS.md or memory files."
    )


def _granted(
    settings: DecisionSettings,
    work: str,
    by_owner: bool,
    now: datetime,
    *,
    look: bool,
    second: bool,
    plan: bool,
) -> bool:
    """Decision is on and not paused at `now`, and the work's grant, a look's Look grant or a
    second look's or a project plan's own grant, lets this start."""
    if look:
        grant = settings.grant_look
    elif second:
        grant = settings.grant_second_look
    elif plan:
        grant = settings.grant_project_plan
    else:
        grant = settings.grant_prepare if work == "prepare" else settings.grant_run
    paused = settings.paused_until is not None and settings.paused_until > now
    allowed = ("ask", "allowed") if by_owner else ("allowed",)
    return settings.enabled and not paused and grant in allowed


def _sessions(item: ItemRecord) -> list[str]:
    """The chats the item names in its evidence and actions."""
    entries = (*item.body.evidence, *item.body.actions)
    return sorted({entry.session_id for entry in entries if entry.session_id})


def _folder(item: ItemRecord) -> str:
    """A look's code folder: the canonical id in its first `open_code` action, or ""."""
    for action in item.body.actions:
        if action.type == "open_code":
            return _canonical_uuid(action.workspace_id)
    return ""


# ---- rows --------------------------------------------------------------------------------------


def _context(scope: str, mission_id: str) -> bytes:
    return _CONTEXT + scope.encode("utf-8") + mission_id.encode("utf-8")


def _texts(plain: bytes | None) -> tuple[str, str]:
    """(title, brief) of an opened body; two empty strings when it is not one."""
    if plain is None:
        return "", ""
    try:
        value = json.loads(plain, object_pairs_hook=unique_object)
    except ValueError, RecursionError:
        return "", ""
    if (
        not isinstance(value, dict)
        or value.keys() != {"title", "brief"}
        or not all(isinstance(text, str) for text in value.values())
    ):
        return "", ""
    return value["title"], value["brief"]


def _record(row: Sequence[Any]) -> MissionRecord:
    """The record of a row read as `_COLUMNS` and its body, which opens here."""
    mission_id, scope, session_id, key, work, state, reason, created, deadline, finished, body = row
    title, text = _texts(_open(_context(scope, mission_id), body))
    return MissionRecord(
        mission_id=mission_id,
        scope=scope,
        session_id=session_id,
        item_key=key,
        work=work,
        state=state,
        reason=reason,
        title=title,
        brief=text,
        created_at=created,
        deadline_at=deadline,
        finished_at=finished,
    )


def _day(day: object) -> tuple[str, str]:
    """The stamps that bound the local day `day` ("YYYY-MM-DD") on this computer's clock."""
    try:
        _require(isinstance(day, str) and date.fromisoformat(day).isoformat() == day)
        start = datetime.fromisoformat(str(day))
        return _stamp(start.astimezone()), _stamp((start + timedelta(days=1)).astimezone())
    except ValueError, OverflowError, OSError:
        raise DecisionLoopError("invalid_request") from None


def _started_on(
    connection: sqlite3.Connection,
    work: str,
    bounds: tuple[str, str],
    *,
    types: tuple[str, ...] | None,
) -> int:
    """The missions of `work` started within `bounds` whose item (same scope and key) is of one
    of `types`, or for None is in no family (`LOOK_MISSIONS`, `SECOND_LOOKS`, `PROJECT_PLANS`)
    or has no row."""
    return connection.execute(
        "SELECT count(*) FROM decision_missions AS m "
        "WHERE m.work=? AND m.created_at>=? AND m.created_at<? AND "
        f"{'NOT ' if types is None else ''}EXISTS (SELECT 1 FROM decision_items AS i "
        "WHERE i.scope=m.scope AND i.item_key=m.item_key "
        "AND i.candidate IN (SELECT value FROM json_each(?)))",
        (
            work,
            *bounds,
            json.dumps(LOOK_MISSIONS + SECOND_LOOKS + PROJECT_PLANS if types is None else types),
        ),
    ).fetchone()[0]


# ---- watching ----------------------------------------------------------------------------------


@dataclass(eq=False)  # hashable by identity: the actor keeps its subscribers in a set
class _Recorder:
    """A mission chat's subscriber: it notes the first turn end, the last error code, a close,
    the cards waiting for the user and whether the user answered one, and calls nothing.

    `cards` follows the chat's stream as the browser's Approvals and Needs you do: a
    `permission.requested` or `question.asked` opens a card under its tool-use id (True for a
    plan card); `request.answered`, `request.cancelled` and the `tool.done` of the same id close
    it; a turn end or a close ends them all, the mission with them. `engaged` is set by the
    first `request.answered` for a card this recorder saw open and has not since seen closed: the
    user has taken the chat over. The app also answers its own automatic denials this way (an
    oversize card refused, or `deny_pending` when the last window closes) for requests this
    recorder may never have seen open, so those must not set it. A withdrawal or a process exit
    closes a card with `request.cancelled` and leaves it unset.

    For a second look's or a project plan's outcome it also keeps, from main-thread plan cards
    only (`kind == "plan"` with no `agent`): `finding`, the first one's
    `second_looks.kind_of(plan)`; `plan_answered`, set when the user answers a plan card it saw
    open; and `left_plan`, set by a `status` that reports a mode other than plan after that first
    card, which is how an approved plan shows."""

    status: str | None = None
    error: str | None = None
    closed: str | None = None
    cards: dict[str, bool] = field(default_factory=dict)
    engaged: bool = False
    finding: str | None = None
    plan_answered: bool = False
    left_plan: bool = False

    @property
    def waiting(self) -> bool:
        """A card waits for the user."""
        return bool(self.cards)

    @property
    def plan_waiting(self) -> bool:
        """A plan card waits for the user's approval."""
        return any(self.cards.values())

    def __call__(self, envelope: dict[str, Any]) -> None:
        kind, data = envelope.get("type"), envelope.get("data")
        if not isinstance(data, dict):
            return
        if kind in ("permission.requested", "question.asked"):
            card = data.get("tool_use_id")
            if isinstance(card, str):
                plan = data.get("kind") == "plan" and not data.get("agent")
                self.cards[card] = plan
                if plan and self.finding is None:
                    self.finding = kind_of(data.get("plan"))
        elif kind in ("request.answered", "request.cancelled", "tool.done"):
            card = data.get("block_id" if kind == "tool.done" else "tool_use_id")
            shown = self.cards.pop(card, None) if isinstance(card, str) else None
            if kind == "request.answered" and shown is not None:
                self.engaged = True
                self.plan_answered = self.plan_answered or shown
        elif kind == "status":
            mode = data.get("mode")
            if self.finding is not None and isinstance(mode, str) and mode not in ("", PLAN_MODE):
                self.left_plan = True
        elif kind == "turn.done":
            self.cards.clear()
            if self.status is None:
                status = data.get("status")
                self.status = status if status in _STATUSES else "error"
        elif kind == "error":
            code = data.get("code")
            self.error = code if code in _ERRORS else "error"
        elif kind == "session.closed":
            self.cards.clear()
            if self.closed is None:
                self.closed = "app_closed" if data.get("reason") == "browser_gone" else "closed"


@dataclass(eq=False)
class _Watch:
    """A started mission watched until its row says it ended, or a look the user turned down
    until an approval in its chat or the chat's close (`MissionService._kept`); `ending` is the
    (state, reason) it ends with, kept while the row cannot be written, and `ready` says `poll`
    has answered its waiting plan. A second look's or a project plan's `look` is its type and
    `subject` its chat's title or its project."""

    mission: MissionRecord
    session: MissionSession
    recorder: _Recorder
    unsubscribe: Callable[[], None]
    deadline: datetime
    ending: tuple[str, str] | None = None
    ready: bool = False
    look: str = ""
    subject: str = ""


def _outcome(recorder: _Recorder) -> tuple[str, str] | None:
    """A second look's or project plan's ending after a successful turn, or None to end as any
    mission does: no plan card read "nothing"; a plan card the user answered read "accepted"
    when the mode left plan, else "rejected", each with the finding's kind."""
    if recorder.status != "success":
        return None
    if recorder.finding is None:
        return "done", "nothing"
    if not recorder.plan_answered:
        return None
    return "done", f"{'accepted' if recorder.left_plan else 'rejected'}:{recorder.finding}"


def _failed(watch: _Watch) -> bool:
    """The watch ended failed, but not by a turn interrupted in its chat, which is the user's
    doing, as Stop is."""
    ending = watch.ending
    return ending is not None and ending[0] == "failed" and watch.recorder.status != "cancelled"


def _frees(watch: _Watch) -> bool:
    """The watch's ending frees its chat's process: its turn ended done or failed (`_failed`), or
    its deadline passed, while the user answered no card and none waits. A chat the user took
    over, stopped, closed or interrupted, or the app closed, keeps whatever it has."""
    if watch.recorder.engaged or watch.recorder.waiting or watch.ending is None:
        return False
    return watch.ending == _DEADLINE or watch.ending[0] == "done" or _failed(watch)


async def _interrupt(session: MissionSession) -> None:
    """Interrupt the chat's running turn, if it has one."""
    if not session.busy:
        return
    try:
        await session.interrupt()
    except Exception as exc:
        log.warning("decision mission interrupt failed: %s", type(exc).__name__)


# ---- the service -------------------------------------------------------------------------------


class MissionService:
    """VIKI's chats across every workspace scope, at most `MAX_RUNNING_MISSIONS` open at once, of
    which VIKI's own starts take one at most.

    `start`, `poll`, `stop`, `stop_all` and `reconcile` run on the event loop and reach the store
    in worker threads; `running`, `started_since`, `count_today`, `count_looks`,
    `count_second_looks`, `count_project_plans`, `taken`, `is_mission` and `owner_chat` read the
    store on the caller's thread. `findings` and `plans_waiting` read the watches on the event
    loop; `second_look_rows` reads the store on the caller's thread and the kept looks' ids,
    rebound whole, from any thread. `roots(session_id, workspace_id)` records a new chat's root
    in a trusted code folder and answers True once it is recorded; without it no look starts."""

    def __init__(
        self,
        store: SessionStore,
        host: MissionHost,
        read_settings: Callable[[], DecisionSettings],
        *,
        wall: Callable[[], datetime],
        roots: Callable[[str, str], Awaitable[bool]] | None = None,
    ) -> None:
        self.store = store
        self._host = host
        self._read_settings = read_settings
        self._wall = wall
        self._roots = roots
        self._lock = asyncio.Lock()
        self._watched: dict[str, _Watch] = {}
        # Looks the user turned down (Keep planning) whose chats are still open, watched for an
        # approval there; `_pending` holds their ids, rebound whole: other threads read it.
        self._kept: dict[str, _Watch] = {}
        self._pending: frozenset[str] = frozenset()
        self._reports: list[MissionRecord] = []
        self._closing: list[_Watch] = []  # ended quietly; `poll` closes their chats, unlocked

    @contextlib.contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        try:
            require_private_store(self.store)
            with self.store._connection(write=write) as connection:
                yield connection
        except StateError, protection.ProtectionError:
            raise DecisionLoopError("unavailable") from None

    # ---- reads ---------------------------------------------------------------------------------

    def running(self) -> list[MissionRecord]:
        """The open missions (starting or running) of every scope, oldest first."""
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS},body FROM decision_missions WHERE {_OPEN} "
                "ORDER BY created_at,mission_id"
            ).fetchall()
        return [_record(row) for row in rows]

    def started_since(self, since: datetime) -> list[str]:
        """The chats of the missions started at or after `since`, newest first, at most
        `MAX_STARTED`."""
        stamped = _stamp(since)
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                "SELECT session_id FROM decision_missions WHERE created_at>=? "
                "ORDER BY created_at DESC,mission_id DESC LIMIT ?",
                (stamped, MAX_STARTED),
            ).fetchall()
        return [session_id for (session_id,) in rows]

    def count_today(self, work: str, day: str) -> int:
        """How many missions of `work` that are not looks, second looks or project plans started
        on the local day `day` ("YYYY-MM-DD"), in any state and any scope."""
        _require(work in MISSION_WORK)
        bounds = _day(day)
        with self._transaction(write=False) as connection:
            return _started_on(connection, work, bounds, types=None)

    def count_looks(self, day: str) -> int:
        """How many Look missions started on the local day `day`, in any state and any scope."""
        bounds = _day(day)
        with self._transaction(write=False) as connection:
            return _started_on(connection, "prepare", bounds, types=LOOK_MISSIONS)

    def count_second_looks(self, day: str) -> int:
        """How many second looks started on the local day `day`, in any state and any scope."""
        bounds = _day(day)
        with self._transaction(write=False) as connection:
            return _started_on(connection, "prepare", bounds, types=SECOND_LOOKS)

    def count_project_plans(self, day: str) -> int:
        """How many project plans started on the local day `day`, in any state and any scope."""
        bounds = _day(day)
        with self._transaction(write=False) as connection:
            return _started_on(connection, "prepare", bounds, types=PROJECT_PLANS)

    def taken(self, item_keys: Iterable[str]) -> frozenset[str]:
        """The keys among `item_keys` that a stored mission of any scope, work or state holds."""
        keys = _keys(item_keys)
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                "SELECT DISTINCT item_key FROM decision_missions "
                "WHERE item_key IN (SELECT value FROM json_each(?))",
                (json.dumps(keys),),
            ).fetchall()
        return frozenset(key for (key,) in rows)

    def is_mission(self, session_id: str) -> bool:
        """Whether the chat is one VIKI started; False for anything that is not an id."""
        try:
            session_id = _id(session_id)
        except DecisionLoopError:
            return False
        with self._transaction(write=False) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM decision_missions WHERE session_id=?", (session_id,)
                ).fetchone()
                is not None
            )

    def owner_chat(self, session_id: str) -> bool:
        """Whether the chat is the user's own: no profile binding, session root, routine
        attempt, Bot or mission names it. One read; False for anything that is not an id."""
        try:
            session_id = _id(session_id)
        except DecisionLoopError:
            return False
        with self._transaction(write=False) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM session_profile_bindings WHERE session_id=?1 "
                    "UNION ALL SELECT 1 FROM session_roots WHERE session_id=?1 "
                    "UNION ALL SELECT 1 FROM task_attempts WHERE session_id=?1 "
                    "UNION ALL SELECT 1 FROM bots WHERE chat_session_id=?1 "
                    "UNION ALL SELECT 1 FROM decision_missions WHERE session_id=?1 LIMIT 1",
                    (session_id,),
                ).fetchone()
                is None
            )

    def findings(self) -> list[Finding]:
        """The watched second looks and project plans whose plan card waits for the user, oldest
        first. Called on the event loop, where the watches change."""
        waiting = [
            watch
            for watch in self._watched.values()
            if watch.look and watch.ending is None and watch.recorder.plan_waiting
        ]
        waiting.sort(key=lambda watch: (watch.mission.created_at, watch.mission.mission_id))
        return [
            Finding(
                watch.mission.mission_id,
                watch.mission.scope,
                watch.mission.session_id,
                watch.look,
                watch.recorder.finding or "other",
                watch.subject,
            )
            for watch in waiting
        ]

    def plans_waiting(self) -> int:
        """How many watched missions, of any kind, have not ended and have a plan card waiting
        for the user's approval; a kept look's chat is the user's and counts for none. Called on
        the event loop, where the watches change."""
        return sum(
            1
            for watch in self._watched.values()
            if watch.ending is None and watch.recorder.plan_waiting
        )

    def second_look_rows(
        self, scope: str, since: datetime, *, types: tuple[str, ...] = SECOND_LOOKS
    ) -> list[LookRow]:
        """The second looks (`types` `SECOND_LOOKS`) or the project plans (`PROJECT_PLANS`)
        started in `scope` at or after `since`, in any state, newest first, at most
        `MAX_LOOK_ROWS`. A kept look's row (Keep planning, its chat still open) reads with
        reason "", neither accepted nor turned down. The kept ids are read before the rows and
        again after, so a read that overlaps a keep or a release still reads the row as kept: a
        look is kept before its row reads rejected and released after its row reads accepted."""
        _require(types in (SECOND_LOOKS, PROJECT_PLANS))
        scope, stamped = _id(scope), _stamp(since)
        pending = self._pending
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                "SELECT m.mission_id,i.candidate,m.state,m.reason,m.created_at "
                "FROM decision_missions AS m "
                "JOIN decision_items AS i ON i.scope=m.scope AND i.item_key=m.item_key "
                "WHERE m.scope=? AND m.created_at>=? "
                "AND i.candidate IN (SELECT value FROM json_each(?)) "
                "ORDER BY m.created_at DESC,m.mission_id DESC LIMIT ?",
                (scope, stamped, json.dumps(types), MAX_LOOK_ROWS),
            ).fetchall()
        pending |= self._pending
        return [
            LookRow(look, state, "" if mission_id in pending else reason, parse_stamp(at))
            for mission_id, look, state, reason, at in rows
        ]

    def _without_quiet(self, missions: Sequence[MissionRecord]) -> list[MissionRecord]:
        """`missions` less the second looks and project plans, told by their items' types in one
        read: the ends `reconcile` and Stop write report none of them."""
        if not missions:
            return []
        with self._transaction(write=False) as connection:
            quiet = {
                mission_id
                for (mission_id,) in connection.execute(
                    "SELECT m.mission_id FROM decision_missions AS m "
                    f"WHERE m.{_IN_IDS} AND EXISTS (SELECT 1 FROM decision_items AS i "
                    "WHERE i.scope=m.scope AND i.item_key=m.item_key "
                    "AND i.candidate IN (SELECT value FROM json_each(?)))",
                    (
                        json.dumps([mission.mission_id for mission in missions]),
                        json.dumps(_QUIET),
                    ),
                )
            }
        return [mission for mission in missions if mission.mission_id not in quiet]

    def _read(self, mission_ids: Sequence[str]) -> list[MissionRecord]:
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS},body FROM decision_missions WHERE {_IN_IDS} "
                "ORDER BY created_at,mission_id",
                (json.dumps(list(mission_ids)),),
            ).fetchall()
        return [_record(row) for row in rows]

    def _refusal(
        self,
        scope: str,
        key: str,
        work: str,
        state_hash: str | None,
        by_owner: bool,
        sessions: list[str],
        now: datetime,
        limit: int,
        family: tuple[str, ...] | None,
    ) -> str | None:
        """What the stored missions refuse a new start with: `from_mission`, `tried` (VIKI's own
        start only: a mission of the item at the same work and state hash, or one the user
        stopped, whatever its work and state; for a look, a second look or a project plan,
        `family` set, any mission holding its key), `busy` (too many open, or the item has one
        open; VIKI's own start while any mission is open), `limit` (counted within `family`, as
        `_started_on` counts), or None."""
        bounds = _day(now.astimezone().date().isoformat())
        with self._transaction(write=False) as connection:
            named = connection.execute(
                "SELECT 1 FROM decision_missions "
                "WHERE session_id IN (SELECT value FROM json_each(?)) LIMIT 1",
                (json.dumps(sessions),),
            ).fetchone()
            if named is not None:
                return "from_mission"
            if not by_owner:
                # One look per folder state, one second look per chat turn, one project plan
                # per stall or overdue set: the key names them, whatever the scope.
                if family is not None:
                    tried = connection.execute(
                        "SELECT 1 FROM decision_missions WHERE item_key=? LIMIT 1", (key,)
                    ).fetchone()
                else:
                    # Once per state and work; once the user stopped VIKI's chat for the item,
                    # not again on VIKI's own, whatever the state hash moves to next.
                    tried = connection.execute(
                        "SELECT 1 FROM decision_missions WHERE scope=? AND item_key=? AND "
                        "((work=? AND state_hash IS ?) OR (state='stopped' AND reason='owner')) "
                        "LIMIT 1",
                        (scope, key, work, state_hash),
                    ).fetchone()
                if tried is not None:
                    return "tried"
            running = connection.execute(
                f"SELECT count(*) FROM decision_missions WHERE {_OPEN}"
            ).fetchone()[0]
            same = connection.execute(
                f"SELECT 1 FROM decision_missions WHERE {_OPEN} AND scope=? AND item_key=?",
                (scope, key),
            ).fetchone()
            # VIKI's own chats take one of the places at most, so one stays free for the user's.
            # That also holds a project plan whose key moved (its overdue set or stamp changed)
            # while the first still waits, with no read of its item, which a prune or Delete all
            # can remove.
            if running >= (MAX_RUNNING_MISSIONS if by_owner else 1) or same is not None:
                return "busy"
            if _started_on(connection, work, bounds, types=family) >= limit:
                return "limit"
        return None

    # ---- writes --------------------------------------------------------------------------------

    def _insert(
        self, mission: MissionRecord, state_hash: str | None, body: bytes, now: datetime
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                f"INSERT INTO decision_missions ({_COLUMNS},state_hash,updated_at,body) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mission.mission_id,
                    mission.scope,
                    mission.session_id,
                    mission.item_key,
                    mission.work,
                    mission.state,
                    mission.reason,
                    mission.created_at,
                    mission.deadline_at,
                    mission.finished_at,
                    state_hash,
                    _stamp(now),
                    body,
                ),
            )

    def _mark_running(self, mission_id: str, now: datetime) -> list[MissionRecord]:
        """Mark a starting mission running; answer its row as it stands after."""
        with self._transaction() as connection:
            connection.execute(
                "UPDATE decision_missions SET state='running',updated_at=? "
                "WHERE mission_id=? AND state='starting'",
                (_stamp(now), mission_id),
            )
        return self._read([mission_id])

    def _finish(
        self, mission_ids: Sequence[str] | None, ending: tuple[str, str], now: datetime
    ) -> list[MissionRecord]:
        """End the listed missions, or every one when None, that are still open; answer them as
        ended, oldest first."""
        state, reason = ending
        stamped = _stamp(now)
        where = _OPEN if mission_ids is None else f"{_OPEN} AND {_IN_IDS}"
        listed = () if mission_ids is None else (json.dumps(list(mission_ids)),)
        with self._transaction() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS},body FROM decision_missions WHERE {where} "
                "ORDER BY created_at,mission_id",
                listed,
            ).fetchall()
            connection.execute(
                f"UPDATE decision_missions SET state=?,reason=?,finished_at=?,updated_at=? "
                f"WHERE {where}",
                (state, reason, stamped, stamped, *listed),
            )
        return [
            replace(_record(row), state=state, reason=reason, finished_at=stamped) for row in rows
        ]

    def _accept(self, mission_id: str, kind: str, now: datetime) -> None:
        """Turn a kept look's ended row into "accepted:<kind>", only while it still reads
        "rejected:<kind>"."""
        with self._transaction() as connection:
            connection.execute(
                "UPDATE decision_missions SET reason=?,updated_at=? "
                "WHERE mission_id=? AND reason=?",
                (f"accepted:{kind}", _stamp(now), mission_id, f"rejected:{kind}"),
            )

    def _lives(self, watch: _Watch) -> bool:
        """The watch's chat has not closed and is still the host's."""
        return (
            watch.recorder.closed is None
            and self._host.get(watch.mission.session_id) is watch.session
        )

    async def _settle(self, watch: _Watch, now: datetime) -> None:
        """Write the watch's ending and stop watching, and set its chat aside for `poll` to
        close when the ending frees it (`_frees`); a store that does not work keeps the watch,
        so the next poll writes it. A second look or a project plan is reported only when it
        failed (`_failed`), and a second look also when it found nothing, titled by its chat's
        title. A look the user turned down whose chat lives on (`_lives`) stays subscribed in
        `_kept` for `poll` (`_settle_kept`), kept before its row reads rejected."""
        if watch.ending is None:
            return
        mission_id = watch.mission.mission_id
        kept = (
            bool(watch.look)
            and watch.ending == ("done", f"rejected:{watch.recorder.finding}")
            and self._lives(watch)
        )
        if kept:
            self._kept[mission_id] = watch
            self._pending = frozenset(self._kept)
        try:
            ended = await run_sync(self._finish, [mission_id], watch.ending, now)
        except DecisionLoopError as exc:
            log.warning("decision mission not saved: %s", exc.code)
            if kept:
                del self._kept[mission_id]
                self._pending = frozenset(self._kept)
            return
        if not kept:
            watch.unsubscribe()
        self._watched.pop(mission_id, None)
        if not watch.look or _failed(watch):
            self._reports.extend(ended)
        elif watch.look in SECOND_LOOKS and watch.ending == ("done", "nothing"):
            self._reports.extend(replace(mission, title=watch.subject) for mission in ended)
        if _frees(watch):
            self._closing.append(watch)

    async def _settle_kept(self, now: datetime) -> None:
        """Settle the looks kept after Keep planning; call it under the lock. Once a kept look's
        chat has left plan mode (the user approved a plan there after all) its row turns
        "accepted:<kind>" (`_accept`, in a worker thread) and the look is released; once its
        chat closes or is not the host's any more it is released and its row stays rejected.
        An approval the store cannot save stays kept for the next poll. Nothing is reported."""
        for watch in list(self._kept.values()):
            if watch.recorder.left_plan:
                kind = watch.recorder.finding or "other"
                try:
                    await run_sync(self._accept, watch.mission.mission_id, kind, now)
                except DecisionLoopError as exc:
                    log.warning("decision mission outcome not saved: %s", exc.code)
                    continue
            elif self._lives(watch):
                continue
            watch.unsubscribe()
            del self._kept[watch.mission.mission_id]
            self._pending = frozenset(self._kept)

    async def _close_ended(self) -> None:
        """Close the chats `_settle` set aside, each within `CLOSE_S`; call it without the lock,
        as a close waits for the process to end. A chat that is no longer that watch's, or in
        which a turn runs again (someone sent there after the end), stays open, unless the
        deadline ended it, whose turn the close interrupts. A failed close leaves the chat
        open."""
        closing, self._closing = self._closing, []
        for watch in closing:
            session_id = watch.mission.session_id
            if self._host.get(session_id) is not watch.session:
                continue
            if watch.session.busy and watch.ending != _DEADLINE:
                continue
            try:
                await asyncio.wait_for(self._host.close(session_id), CLOSE_S)
            except Exception as exc:
                log.warning("decision mission chat not closed: %s", type(exc).__name__)

    def _ending(self, watch: _Watch, now: datetime) -> tuple[str, str] | None:
        """How the watched mission has ended at `now`, or None while it goes on. The deadline
        ends it only while no card waits and before the user has answered one: a waiting card
        is the user's to answer, and after an answer the chat is the user's. A second look's
        successful turn ends with its outcome (`_outcome`), as a project plan's does, unless the
        app closed the chat."""
        recorder = watch.recorder
        if watch.look and recorder.closed == "app_closed":
            return "unknown", "app_closed"
        if recorder.status is not None:
            if watch.look and (outcome := _outcome(recorder)) is not None:
                return outcome
            if recorder.status == "success":
                return "done", ""
            return "failed", recorder.error or recorder.status
        if (
            recorder.closed is not None
            or self._host.get(watch.mission.session_id) is not watch.session
        ):
            return "unknown", recorder.closed or "closed"
        if now < watch.deadline or recorder.waiting or recorder.engaged:
            return None
        return _DEADLINE

    async def _rooted(self, session_id: str, folder: str) -> bool:
        """Whether `roots` recorded the new chat's root in `folder`; a raise reads False."""
        if self._roots is None:
            return False
        try:
            return await self._roots(session_id, folder) is True
        except Exception as exc:
            log.warning("decision mission root not recorded: %s", type(exc).__name__)
            return False

    # ---- lifecycle -----------------------------------------------------------------------------

    async def start(
        self,
        scope: str,
        item: ItemRecord,
        work: str,
        *,
        by_owner: bool,
        deadline: datetime | None = None,
        leave_out: Sequence[str] = (),
    ) -> MissionRecord:
        """Start a chat for `item` of `scope` and send it the brief; answer the mission,
        running, or as `stop` left it when Stop came while the brief was being sent.

        `work` is "prepare" or "run"; `by_owner` says the user asked for it; `deadline` is the
        item's own deadline, for the brief; `leave_out`, some of `FINDING_KINDS`, names the
        kinds a second look's brief leaves out. The row keeps the item's state hash, for
        `tried`. Refusals are in the module docstring.
        """
        scope = _id(scope)
        _require(
            isinstance(item, ItemRecord)
            and work in MISSION_WORK
            and isinstance(by_owner, bool)
            and (item.state_hash is None or _is_key(item.state_hash))
            and (
                deadline is None
                or (isinstance(deadline, datetime) and deadline.utcoffset() is not None)
            )
            and isinstance(leave_out, Sequence)
            and not isinstance(leave_out, str)
            and all(kind in FINDING_KINDS for kind in leave_out)
        )
        key, state_hash = _key(item.item_key), item.state_hash
        look = item.candidate in LOOK_MISSIONS
        second = item.candidate in SECOND_LOOKS
        plan = item.candidate in PROJECT_PLANS
        if scope == SCRATCH_WORKSPACE_ID:
            raise MissionError("no_folder")
        settings = await run_sync(self._read_settings)
        rules = _rules(settings.never_send, settings.never_send_invalid)
        # A list that cannot be trusted holds every note back, as a Never send folder "/" would.
        # A tag rule is not judged here: the item carries no note's tags.
        held = ((),) if rules is None else rules.folders
        text = brief(item, work, deadline, leave_out, held=held)
        # A look's title is the type words, the same for every folder: its "what" names the folder.
        if second:
            title = f"Second look: {_chat_title(item)}"
        elif plan:
            subject = _subject(item)
            title = _PLAN_TITLES[item.candidate] + (f": {subject}" if subject else "")
        elif look:
            title = _line(item.body.what) or _line(item.body.title)
        else:
            title = _line(item.body.title) or _line(item.body.what)
        async with self._lock:
            now = self._wall()
            if not _granted(settings, work, by_owner, now, look=look, second=second, plan=plan):
                raise MissionError("off")
            if item.candidate == "mission_report":
                raise MissionError("from_mission")
            # The authority guard: only a look, a second look or a project plan, and only to
            # plan, joins `MISSION_TYPES`.
            if item.candidate not in MISSION_TYPES and not (
                (look or second or plan) and work == "prepare"
            ):
                raise MissionError("no_work")
            folder = _folder(item) if look else ""
            if look and (not folder or self._roots is None):
                raise MissionError("no_folder")
            if look:
                family, limit = LOOK_MISSIONS, settings.limit_look
            elif second:
                family, limit = SECOND_LOOKS, settings.limit_second_look
            elif plan:
                family, limit = PROJECT_PLANS, settings.limit_project_plan
            else:
                family = None
                limit = settings.limit_drafts if work == "prepare" else settings.limit_chats
            sessions = _sessions(item)
            refusal = await run_sync(
                self._refusal,
                scope,
                key,
                work,
                state_hash,
                by_owner,
                sessions,
                now,
                limit,
                family,
            )
            if refusal is not None:
                raise MissionError(refusal)
            # A plan's goal points at "Evidence: none" without a note, so it fails closed; so does
            # a second look once Never send holds back every note it named.
            notes = _notes(item, held)
            if not notes and (plan or (second and _notes(item))):
                raise MissionError("no_work")
            mission = MissionRecord(
                mission_id=str(uuid.uuid4()),
                scope=scope,
                session_id=str(uuid.uuid4()),
                item_key=key,
                work=work,
                state="starting",
                reason="",
                title=title,
                brief=text,
                created_at=_stamp(now),
                deadline_at=_stamp(now + timedelta(minutes=MISSION_MINUTES)),
                finished_at=None,
            )
            body = _plain({"title": title, "brief": text})
            sealed = _seal(_context(scope, mission.mission_id), body, MISSION_BODY_BYTES)
            # The root comes after every refusal and before the mode and the chat: `create`
            # resolves the folder from it, and a refused root leaves nothing to clean up.
            if look and not await self._rooted(mission.session_id, folder):
                raise MissionError("no_folder")
            recorder = _Recorder()
            try:
                if work == "prepare":
                    state = self._host.app_state
                    await run_sync(
                        lambda: state.remember(mission.session_id, new_session=True, mode=PLAN_MODE)
                    )
                session = self._host.create(mission.session_id)
                unsubscribe = session.subscribe(recorder)
            except Exception as exc:
                log.warning("decision mission chat not created: %s", type(exc).__name__)
                failed = replace(
                    mission, state="failed", reason="not_sent", finished_at=mission.created_at
                )
                await run_sync(self._insert, failed, state_hash, sealed, now)
                self._reports.append(failed)
                raise MissionError("not_sent") from None
            try:
                await run_sync(self._insert, mission, state_hash, sealed, now)
            except BaseException:
                unsubscribe()
                raise
        try:
            sent = bool(await session.send(text))
        except Exception as exc:
            log.warning("decision mission brief not sent: %s", type(exc).__name__)
            sent = False
        async with self._lock:
            watch = _Watch(
                mission, session, recorder, unsubscribe, parse_stamp(mission.deadline_at)
            )
            if second:
                watch.look, watch.subject = item.candidate, _chat_title(item)
            elif plan:
                watch.look, watch.subject = item.candidate, _subject(item)
            self._watched[mission.mission_id] = watch
            if not sent:
                watch.ending = _NOT_SENT
                await self._settle(watch, self._wall())
                raise MissionError("not_sent")
            (stored,) = await run_sync(self._mark_running, mission.mission_id, self._wall())
            if stored.state == "running":
                watch.mission = stored
                return stored
            # Stopped while the brief was on its way, before its turn could be interrupted.
            self._watched.pop(mission.mission_id, None)
            unsubscribe()
            await _interrupt(session)
            return stored

    async def poll(self, now: datetime) -> list[MissionRecord]:
        """End each watched mission whose turn ended, whose chat is gone, or whose deadline
        passed with no card waiting and none answered by the user (interrupting its running
        turn), then, with the lock released, close the chats of those that ended quietly, by
        this path or another (`_close_ended`). Answer, once each, every mission that ended since
        the last poll and each prepare mission this poll first finds with its plan card waiting,
        running with reason "ready". Under the lock it also settles the looks kept after Keep
        planning (`_settle_kept`), which answers none."""
        _stamp(now)
        await self._settle_watched(now)
        await self._close_ended()
        # Taken after the closes, so a close cancelled at shutdown leaves them for the next poll.
        reports, self._reports = self._reports, []
        return reports

    async def _settle_watched(self, now: datetime) -> None:
        async with self._lock:
            for watch in list(self._watched.values()):
                if watch.ending is None:
                    watch.ending = self._ending(watch, now)
                    if watch.ending is None:
                        prepared = watch.mission.work == "prepare" and not watch.look
                        if prepared and watch.recorder.plan_waiting and not watch.ready:
                            watch.ready = True
                            self._reports.append(replace(watch.mission, reason="ready"))
                        continue
                    if watch.ending == _DEADLINE:
                        await _interrupt(watch.session)
                await self._settle(watch, now)
            await self._settle_kept(now)

    async def stop(self, mission_id: str) -> MissionRecord:
        """Stop one open mission as the module docstring says and answer it as stored after;
        one that already ended is answered as it is, an unknown one is `unknown_item`."""
        mission_id = _id(mission_id)
        async with self._lock:
            found = await run_sync(self._read, [mission_id])
            if not found:
                raise DecisionLoopError("unknown_item")
            if found[0].state not in OPEN_STATES:
                return found[0]
            (stopped,) = await self._stop(found)
            return stopped

    async def stop_all(self) -> list[MissionRecord]:
        """Stop every open mission as `stop` does; answer them as stored after, oldest first."""
        async with self._lock:
            return await self._stop(await run_sync(self.running))

    async def _stop(self, missions: Sequence[MissionRecord]) -> list[MissionRecord]:
        """Interrupt each mission's running turn and mark it stopped ("owner"); a watched one
        whose turn already ended or whose chat is gone keeps that ending instead. The chats it
        stops stay open; one whose turn had already ended quietly closes at the next `poll`."""
        now = self._wall()
        loose: list[str] = []  # open rows nobody watches yet: their briefs are on their way
        for mission in missions:
            watch = self._watched.get(mission.mission_id)
            if watch is None:
                loose.append(mission.mission_id)
                session = self._host.get(mission.session_id)
                if session is not None:
                    await _interrupt(session)
                continue
            if watch.ending is None:
                ending = self._ending(watch, now)
                watch.ending = _OWNER if ending in (None, _DEADLINE) else ending
                if watch.ending == _OWNER:
                    await _interrupt(watch.session)
        if loose:
            ended = await run_sync(self._finish, loose, _OWNER, now)
            self._reports.extend(await run_sync(self._without_quiet, ended))
        for mission in missions:
            watch = self._watched.get(mission.mission_id)
            if watch is not None:
                await self._settle(watch, now)
        return await run_sync(self._read, [mission.mission_id for mission in missions])

    async def reconcile(self) -> int:
        """At startup, before any start: make every mission an earlier launch left starting or
        running unknown ("app_closed"); `poll` answers each once. Answer how many."""
        async with self._lock:
            ended = await run_sync(self._finish, None, ("unknown", "app_closed"), self._wall())
            self._reports.extend(await run_sync(self._without_quiet, ended))
        return len(ended)
