"""Episodes, taint, recall, sealed proposals and the owner's decisions on them."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from tests.messaging.test_bots import _bind_chat, _bot, _group_member, _owned_chat
from tests.messaging.test_bots import service as service
from tests.vaults.test_memory import CREATED, NOON, Harness, _harness
from viki_agent.automation.routines import stamp
from viki_agent.messaging.bot_episodes import EpisodeRecorder, EpisodeStore
from viki_agent.messaging.bot_learning import BotLearning, LearningError, result_id, skill_text
from viki_agent.messaging.bot_proposals import ProposalError, ProposalStore
from viki_agent.messaging.bot_recall import HEADER, LessonStat, compose, creator_for
from viki_agent.messaging.bots import BotService
from viki_agent.messaging.dream_ops import Evidence, Proposal, Score
from viki_agent.profiles.native_assets import parse_skill_text
from viki_agent.vaults import lesson_body
from viki_agent.vaults.memory import MemoryItem
from viki_agent.vaults.memory_record import Fields, SessionSource
from viki_agent.vaults.memory_record import compose as compose_record

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
BOT = "b0c7ec1e-85a0-4c20-97f3-e79509370f20"
SESSION = "8d1e0c52-3b7a-4f0e-9c1d-2a6b5e4f7c90"


def _envelope(kind: str, **data: Any) -> dict[str, Any]:
    return {"type": kind, "data": data}


# --- episodes and taint


async def test_a_watched_turn_becomes_one_episode_and_web_tools_taint_it(
    service: BotService,
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    with service.store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO session_bot_memory VALUES (?,?)", (bot["chat_session_id"], bot["bot_id"])
        )
    task = _owned_chat(service, bot)
    store = EpisodeStore(service.store)
    tasks: set[asyncio.Task[Any]] = set()
    clock = iter(NOW + timedelta(seconds=n) for n in range(100))
    recorder = EpisodeRecorder(store, tasks=tasks, now=lambda: next(clock))
    main = store.session(bot["chat_session_id"])
    assert main is not None and main.kind == "main" and main.bot_id == bot["bot_id"]
    info = store.session(task)
    assert info is not None and info.kind == "task"
    assert store.session(str(uuid.uuid4())) is None

    recorder.watch(task, info, ("33333333-3333-4333-8333-000000000001",))
    recorder.observe(str(uuid.uuid4()), _envelope("turn.started"))  # not watched: ignored
    for envelope in (
        _envelope("turn.started"),
        _envelope("tool.started", tool="Read"),
        _envelope("tool.started", tool="WebSearch"),
        _envelope("turn.done", status="success"),
        _envelope("turn.started"),
        _envelope("turn.done", status="strange"),
    ):
        recorder.observe(task, envelope)
    await asyncio.gather(*tasks)

    episodes = store.episodes(bot["bot_id"], limit=10, include_tainted=True)
    assert [(e.status, e.tools, e.tainted) for e in episodes] == [
        ("error", 0, True),  # the session is tainted from the first turn on
        ("success", 2, True),
    ]
    assert store.episodes(bot["bot_id"], limit=10) == []
    assert store.tainted(task) and not store.tainted(bot["chat_session_id"])
    assert store.tainted_sessions(bot["bot_id"])[0]["sources"] == "web"
    with service.store._connection() as connection:
        uses = connection.execute("SELECT uses FROM bot_lesson_stats").fetchall()
    assert uses == [(2,)]

    assert store.untaint(task)
    assert not store.tainted(task)
    assert len(store.episodes(bot["bot_id"], limit=10)) == 2


def test_taint_reaches_every_member_of_a_bot_thread(service: BotService) -> None:
    bot = _bot(service)
    first, second, alone = (_owned_chat(service, bot) for _ in range(3))
    _group_member(service, bot, first)
    with service.store._connection(write=True) as connection:
        thread = connection.execute(
            "SELECT thread_id FROM bot_thread_sessions WHERE session_id=?", (first,)
        ).fetchone()[0]
        connection.execute("INSERT INTO bot_thread_sessions VALUES (?,?)", (thread, second))
    store = EpisodeStore(service.store)
    info = store.session(first)
    assert info is not None and info.kind == "bot_thread"
    store.mark_tainted(first, info, "mcp", stamp(NOW))
    store.mark_tainted(first, info, "web", stamp(NOW))
    assert store.tainted(second) and not store.tainted(alone)
    assert store.tainted_sessions(bot["bot_id"]) == [
        {"session_id": first, "sources": "web,mcp", "tainted_at": stamp(NOW)}
    ]
    # Clearing a peer's own row changes nothing while the thread still carries taint.
    assert not store.untaint(second) and store.tainted(second)


def test_removing_a_bot_drops_what_it_learned_from(service: BotService) -> None:
    bot = _bot(service)
    session = _owned_chat(service, bot)
    store = EpisodeStore(service.store)
    info = store.session(session)
    assert info is not None
    store.mark_tainted(session, info, "web", stamp(NOW))
    service.remove(bot["bot_id"], bot["state_revision"])
    with service.store._connection() as connection:
        assert connection.execute("SELECT count(*) FROM bot_session_taint").fetchone() == (0,)


# --- recall


def _item(
    claim: str,
    *,
    creator: str | None,
    state: str = "active",
    created: str = "2026-11-20T00:00:00.000Z",
) -> MemoryItem:
    memory_id = str(uuid.uuid4())
    fields = Fields(memory_id, "assistant", "vault", "approved", created, creator=creator)
    return MemoryItem(f"memory/{memory_id}.md", "d" * 64, cast(Any, state), fields, claim, None)


def _lesson(trigger: str) -> str:
    return lesson_body.compose(lesson_body.Lesson(trigger, "Do the thing", ("an example",)))


def test_recall_lists_profile_facts_and_the_best_lessons_as_data() -> None:
    mine = creator_for(BOT)
    profile = _item("Lives in <Tokyo>\nWorks late", creator=None)
    pending = _item("Not approved here", creator=None, state="pending")
    other = _item("Another Bot's fact", creator=creator_for(str(uuid.uuid4())))
    fact = _item("The lab uses arXiv ids", creator=mine)
    lessons = [_item(_lesson(f"Case {n}"), creator=mine) for n in range(7)]
    harmful = _item(_lesson("Harmful case"), creator=mine)
    stale = _item(_lesson("Old case"), creator=mine, created="2026-01-01T00:00:00.000Z")
    stats = {
        lessons[6].fields.id: LessonStat(3, 3, 0, stamp(NOW)),
        harmful.fields.id: LessonStat(5, 1, 2, stamp(NOW)),
    }
    recall = compose([profile, pending, other, fact, *lessons, harmful, stale], BOT, stats, NOW)
    assert recall.text.startswith(HEADER)
    assert "- Lives in ‹Tokyo› / Works late" in recall.text
    assert "Not approved here" not in recall.text and "Another Bot" not in recall.text
    assert "<facts>\n- The lab uses arXiv ids\n</facts>" in recall.text
    assert len(recall.lesson_ids) == 5 and recall.lesson_ids[0] == lessons[6].fields.id
    assert harmful.fields.id not in recall.lesson_ids and stale.fields.id not in recall.lesson_ids
    assert compose([pending], BOT, {}, NOW).text == ""


# --- proposals


def _proposal(claim: str = "The lab uses arXiv ids", op: str = "fact_add") -> Proposal:
    return Proposal(
        cast(Any, op), claim, (Evidence(str(uuid.uuid4()), SESSION, "copied quote"),), "why"
    )


def test_proposals_are_sealed_bound_to_their_row_and_decided_once(service: BotService) -> None:
    store = ProposalStore(service.store)
    workspace = str(uuid.uuid4())
    run = store.start_run(workspace, BOT, "reflect", "user", None, stamp(NOW))
    assert store.add(run, [_proposal(), _proposal("Second")], stamp(NOW)) == 2
    store.finish_run(run.run_id, reason="", episodes=2, proposed=2, rejected=1, now=stamp(NOW))
    (first, second) = store.listing(BOT)
    assert first.body["claim"] == "The lab uses arXiv ids" and first.state == "open"
    with service.store._connection() as connection:
        body = connection.execute(
            "SELECT body FROM bot_proposals WHERE proposal_id=?", (first.proposal_id,)
        ).fetchone()[0]
    assert b"arXiv" not in body  # sealed at rest
    with service.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE bot_proposals SET body=? WHERE proposal_id=?", (body, second.proposal_id)
        )
    with pytest.raises(ProposalError):  # another row's seal does not open here
        store.get(second.proposal_id)
    store.decide(first.proposal_id, expected="open", state="rejected", now=stamp(NOW))
    with pytest.raises(ProposalError, match="conflict"):
        store.decide(first.proposal_id, expected="open", state="applied", now=stamp(NOW))
    (done,) = store.runs(BOT)
    assert (done.state, done.proposed, done.rejected) == ("done", 2, 1)


def test_scores_count_beside_uses(service: BotService) -> None:
    store = ProposalStore(service.store)
    workspace, lesson = str(uuid.uuid4()), str(uuid.uuid4())
    store.score(
        workspace,
        BOT,
        [Score(lesson, "helpful"), Score(lesson, "harmful"), Score(lesson, "helpful")],
    )
    assert store.stats(workspace, BOT) == {lesson: LessonStat(0, 2, 1, None)}


# --- applying


class _Bots:
    def __init__(self, workspace_id: str) -> None:
        self.workspace_id = workspace_id

    def get(self, bot_id: str) -> dict[str, Any]:
        return {"bot_id": bot_id, "workspace_id": self.workspace_id, "profile": None}


def _learning(harness: Harness, tmp_path: Path) -> BotLearning:
    harness.spaces.spaces[harness.wid].persistent = True
    return BotLearning(
        harness.store,
        cast(Any, harness.spaces),
        cast(Any, _Bots(harness.wid)),
        harness.service,
        harness.vault,
        tmp_path / "data",
        tasks=set(),
        now=lambda: datetime.fromtimestamp(NOON / 1e9, UTC),  # the memory service's clock
    )


def _open(learning: BotLearning, harness: Harness, proposal: Proposal) -> str:
    run = learning.proposals.start_run(harness.wid, BOT, "dream", "user", None, stamp(NOW))
    learning.proposals.add(run, [proposal], stamp(NOW))
    return next(s.proposal_id for s in learning.proposals.listing(BOT) if s.state == "open")


async def test_an_approved_lesson_is_written_approved_and_can_be_undone(tmp_path: Path) -> None:
    harness = _harness(tmp_path, "ordinary")
    learning = _learning(harness, tmp_path)
    claim = _lesson("Quoting a paper's number")
    proposal_id = _open(learning, harness, _proposal(claim, "lesson_add"))

    assert await learning.apply(proposal_id) == "applied"
    memory_id = result_id(proposal_id)
    record = harness.parsed(memory_id)
    assert record.fields.creator == creator_for(BOT) and record.fields.kind == "assistant"
    assert record.fields.sources == (SessionSource(SESSION),)
    assert harness.states() == {memory_id: "active"}
    assert "Quoting a paper" in learning.recall_text(harness.wid, BOT, SESSION)
    with pytest.raises(LearningError, match="conflict"):
        await learning.apply(proposal_id)

    await learning.undo(proposal_id)
    assert harness.states() == {memory_id: "retired"}
    assert learning.proposals.get(proposal_id).state == "undone"


async def test_a_changed_target_makes_a_proposal_stale(tmp_path: Path) -> None:
    harness = _harness(tmp_path, "ordinary")
    learning = _learning(harness, tmp_path)
    target, revision = harness.record(kind="assistant")
    retire = Proposal("record_retire", "", (), "dormant", target, revision, "old")
    proposal_id = _open(learning, harness, retire)
    path = harness.path(target)
    path.write_bytes(path.read_bytes().replace(b"tea", b"Tea"))

    assert await learning.apply(proposal_id) == "stale"
    assert learning.proposals.get(proposal_id).state == "stale"


async def test_a_replacement_keeps_its_owner_and_names_the_old_record(tmp_path: Path) -> None:
    harness = _harness(tmp_path, "ordinary")
    learning = _learning(harness, tmp_path)
    target = str(uuid.uuid4())
    fields = Fields(target, "assistant", "vault", "approved", CREATED, creator=creator_for(BOT))
    revision = harness.write(f"memory/{target}.md", compose_record(fields, "Prefers tea").encode())
    supersede = Proposal(
        "record_supersede",
        "Prefers green tea",
        (Evidence("e", SESSION, "q"),),
        "w",
        target,
        revision,
        "old",
    )
    proposal_id = _open(learning, harness, supersede)

    assert await learning.apply(proposal_id) == "applied"
    stored = learning.proposals.get(proposal_id)
    assert stored.result_id is not None
    new = harness.parsed(stored.result_id)
    assert new.fields.supersedes == target and new.fields.creator == creator_for(BOT)
    assert harness.parsed(target).fields.status == "retired"


async def test_a_protected_vault_takes_no_approval(tmp_path: Path) -> None:
    harness = _harness(tmp_path, "protected")
    learning = _learning(harness, tmp_path)
    proposal_id = _open(learning, harness, _proposal())
    with pytest.raises(LearningError, match="vault_not_ordinary"):
        await learning.apply(proposal_id)
    assert learning.proposals.get(proposal_id).state == "open"


def test_earlier_memory_files_become_open_fact_proposals(tmp_path: Path) -> None:
    harness = _harness(tmp_path, "ordinary")
    learning = _learning(harness, tmp_path)
    folder = f"bot-memory/{BOT}"
    harness.write(f"{folder}/MEMORY.md", b"- [Tea](tea.md)\n")
    digest = harness.write(f"{folder}/tea.md", b"---\nname: tea\n---\nPrefers tea\r\n\r\n")
    harness.write(f"{folder}/empty.md", b"---\nname: x\n---\n")

    run = learning.import_legacy(BOT)
    (proposal,) = learning.proposals.listing(BOT)
    assert proposal.body["claim"] == "Prefers tea"
    assert proposal.body["notes"] == [{"note": f"{folder}/tea.md", "sha256": digest}]
    assert learning.proposals.runs(BOT)[0].run_id == run.run_id
    with pytest.raises(LearningError, match="nothing_to_import"):
        learning.import_legacy(str(uuid.uuid4()))


def test_a_proven_lesson_is_offered_as_an_installable_skill(tmp_path: Path) -> None:
    harness = _harness(tmp_path, "ordinary")
    learning = _learning(harness, tmp_path)
    lesson = lesson_body.Lesson('Citing a "number"', "Check the newest version", ("it was v3",))
    memory_id = str(uuid.uuid4())
    fields = Fields(
        memory_id,
        "assistant",
        "vault",
        "approved",
        CREATED,
        creator=creator_for(BOT),
        sources=(SessionSource(SESSION),),
    )
    text = skill_text(memory_id, lesson, "Research")
    head = parse_skill_text(text)
    assert head.name == f"lesson-{memory_id[:8]}" and "Research" in head.description
    revision = harness.write(
        f"memory/{memory_id}.md", compose_record(fields, lesson_body.compose(lesson)).encode()
    )
    harness.service._review(harness.wid, memory_id, revision)
    offered: list[tuple[str, str, str, list[str]]] = []

    class _Skills:
        def create(self, workspace_id: str, text: str, trigger: str, sessions: list[str]) -> None:
            offered.append((workspace_id, text, trigger, sessions))

    learning.skills = cast(Any, _Skills())
    assert learning.offer_skills(harness.wid, BOT, "Research") == 0  # not used yet
    with harness.store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO bot_lesson_stats VALUES (?,?,?,10,4,1,NULL)", (harness.wid, memory_id, BOT)
        )
    assert learning.offer_skills(harness.wid, BOT, "Research") == 1
    assert offered == [(harness.wid, text, "repeated_request", [SESSION])]
