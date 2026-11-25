"""What a learning run's reply may change, checked against the snapshot its prompt showed: pure.

`check` turns the ops `dream_reply.parse_reply` kept into proposals and lesson scores, refusing
each op that fails a rule:

- every quote is copied exactly from the excerpt of the episode it names, MIN_QUOTE to
  dream_reply.MAX_QUOTE characters, and a `profile_add` quote from the owner's own words in it;
- a target is one of this Bot's records the prompt listed (R or L), never the owner's (P);
- a `record_supersede` needs evidence from an episode that ended after its target was made;
- a new lesson or fact must not repeat one already kept or proposed in the same reply
  (`improve.match_tokens` overlap of at least DUPLICATE_LESSON or DUPLICATE_FACT);
- a `lesson_score` names a lesson its episode recalled.

The run's limits refuse the whole reply: more than the mode's proposals (`reflect` MAX_REFLECT,
`dream` MAX_DREAM_OPS), or retirements and replacements beyond MAX_REMOVED_SHARE of the Bot's
records (at least one). Nothing here writes: an accepted proposal is sealed for the owner
(`bot_proposals`), and only the owner's approval applies it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from viki_agent.decision_loop.improve import match_tokens
from viki_agent.messaging.bot_excerpts import Excerpt
from viki_agent.messaging.bot_recall import LessonStat, dormant
from viki_agent.vaults import lesson_body
from viki_agent.vaults.memory import MemoryItem

Mode = Literal["reflect", "dream"]
ProposalOp = Literal["lesson_add", "fact_add", "profile_add", "record_supersede", "record_retire"]

MIN_QUOTE = 8
MAX_REFLECT = 3
MAX_DREAM_OPS = 40
MAX_REMOVED_SHARE = 0.10
DUPLICATE_LESSON = 0.8
DUPLICATE_FACT = 0.9
MAX_DORMANT = 10


@dataclass(frozen=True)
class RecordView:
    """One record the prompt listed: R (a Bot fact), L (a Bot lesson) or P (the owner's)."""

    label: str
    memory_id: str
    sha256: str
    created: str
    claim: str
    lesson: lesson_body.Lesson | None

    @property
    def owned(self) -> bool:
        return self.label[0] in "RL"


@dataclass(frozen=True)
class Evidence:
    episode_id: str
    session_id: str
    quote: str


@dataclass(frozen=True)
class Proposal:
    op: ProposalOp
    claim: str  # the record text to write; "" for a retirement
    evidence: tuple[Evidence, ...]
    why: str
    target_id: str | None = None
    target_sha256: str | None = None
    target_claim: str = ""
    notes: tuple[tuple[str, str], ...] = ()  # (vault ref, SHA-256) sources of an import

    def body(self) -> dict[str, Any]:
        """What is sealed: everything the owner reads and the applier needs."""
        return {
            "op": self.op,
            "claim": self.claim,
            "evidence": [
                {"episode_id": e.episode_id, "session_id": e.session_id, "quote": e.quote}
                for e in self.evidence
            ],
            "why": self.why,
            "target_id": self.target_id,
            "target_sha256": self.target_sha256,
            "target_claim": self.target_claim,
            "notes": [{"note": ref, "sha256": digest} for ref, digest in self.notes],
        }


@dataclass(frozen=True)
class Score:
    memory_id: str
    verdict: Literal["helpful", "harmful"]


@dataclass(frozen=True)
class Checked:
    proposals: tuple[Proposal, ...] = ()
    scores: tuple[Score, ...] = ()
    rejected: int = 0
    reason: str | None = None  # "over_limit" when the whole reply was refused


@dataclass
class _Seen:
    lessons: list[frozenset[str]] = field(default_factory=list)
    facts: list[frozenset[str]] = field(default_factory=list)


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _duplicate(tokens: frozenset[str], seen: Iterable[frozenset[str]], limit: float) -> bool:
    return any(_overlap(tokens, other) >= limit for other in seen)


def views(items: Iterable[MemoryItem], bot_id: str, *, with_profile: bool) -> list[RecordView]:
    """The active records a prompt lists, labelled: the Bot's facts R1.., its lessons L1.., and,
    `with_profile`, the owner's records P1.."""
    mine = f"bot:{bot_id}"
    facts: list[RecordView] = []
    lessons: list[RecordView] = []
    profile: list[RecordView] = []
    for item in items:
        if item.state != "active":
            continue
        lesson = lesson_body.parse(item.claim)
        if item.fields.creator == mine:
            target = lessons if lesson is not None else facts
            prefix = "L" if lesson is not None else "R"
            target.append(
                RecordView(
                    f"{prefix}{len(target) + 1}",
                    item.fields.id,
                    item.sha256,
                    item.fields.created,
                    item.claim,
                    lesson,
                )
            )
        elif item.fields.creator is None and with_profile:
            profile.append(
                RecordView(
                    f"P{len(profile) + 1}",
                    item.fields.id,
                    item.sha256,
                    item.fields.created,
                    item.claim,
                    None,
                )
            )
    return [*facts, *lessons, *profile]


def _evidence(
    raw: Sequence[Mapping[str, str]], excerpts: Mapping[str, Excerpt], *, owner: bool
) -> tuple[Evidence, ...] | None:
    kept: list[Evidence] = []
    for item in raw:
        excerpt = excerpts.get(item["episode"])
        quote = item["quote"].strip()
        if excerpt is None or len(quote) < MIN_QUOTE:
            return None
        source = excerpt.owner_text if owner else excerpt.text
        if quote not in source:
            return None
        kept.append(Evidence(excerpt.episode.episode_id, excerpt.episode.session_id, quote))
    return tuple(kept)


def _lesson_claim(trigger: str, action: str, evidence: Sequence[Evidence]) -> str | None:
    examples = []
    for item in evidence[: lesson_body.MAX_EXAMPLES]:
        quote = lesson_body.normalize_quote(item.quote)
        if quote:
            examples.append(quote)
    try:
        return lesson_body.compose(
            lesson_body.Lesson(" ".join(trigger.split()), " ".join(action.split()), tuple(examples))
        )
    except ValueError:
        return None


def _flat_claim(text: str) -> str | None:
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    claim = "\n".join(lines)
    if not claim or lesson_body.parse(claim) is not None or "\r" in claim:
        return None
    return claim


def check(
    ops: Sequence[Mapping[str, Any]],
    *,
    mode: Mode,
    excerpts: Sequence[Excerpt],
    records: Sequence[RecordView],
) -> Checked:
    by_episode = {excerpt.label: excerpt for excerpt in excerpts}
    by_label = {record.label: record for record in records}
    owned = [record for record in records if record.owned]
    seen = _Seen(
        lessons=[match_tokens(f"{r.lesson.trigger} {r.lesson.action}") for r in owned if r.lesson],
        facts=[match_tokens(r.claim) for r in records if r.lesson is None],
    )
    proposals: list[Proposal] = []
    scores: list[Score] = []
    rejected = 0
    targeted: set[str] = set()
    for op in ops:
        result = _one(op, by_episode, by_label, seen, targeted)
        if result is None:
            rejected += 1
        elif isinstance(result, Score):
            scores.append(result)
        else:
            proposals.append(result)
    limit = MAX_REFLECT if mode == "reflect" else MAX_DREAM_OPS
    removals = sum(p.op in ("record_supersede", "record_retire") for p in proposals)
    if len(proposals) > limit or removals > max(1, int(len(owned) * MAX_REMOVED_SHARE)):
        return Checked(rejected=len(ops), reason="over_limit")
    return Checked(tuple(proposals), tuple(scores), rejected)


def _one(
    op: Mapping[str, Any],
    excerpts: Mapping[str, Excerpt],
    records: Mapping[str, RecordView],
    seen: _Seen,
    targeted: set[str],
) -> Proposal | Score | None:
    kind = op["op"]
    why = " ".join(str(op.get("why", "")).split())
    if kind == "lesson_score":
        target, excerpt = records.get(op["target"]), excerpts.get(op["episode"])
        if target is None or target.lesson is None or not target.owned or excerpt is None:
            return None
        if target.memory_id not in excerpt.episode.lessons:
            return None
        return Score(target.memory_id, op["verdict"])
    if kind == "record_retire":
        target = records.get(op["target"])
        if target is None or not target.owned or target.memory_id in targeted:
            return None
        targeted.add(target.memory_id)
        return Proposal("record_retire", "", (), why, target.memory_id, target.sha256, target.claim)
    evidence = _evidence(op["evidence"], excerpts, owner=kind == "profile_add")
    if evidence is None:
        return None
    if kind == "lesson_add":
        claim = _lesson_claim(op["trigger"], op["action"], evidence)
        tokens = match_tokens(f"{op['trigger']} {op['action']}")
        if claim is None or _duplicate(tokens, seen.lessons, DUPLICATE_LESSON):
            return None
        seen.lessons.append(tokens)
        return Proposal("lesson_add", claim, evidence, why)
    if kind in ("fact_add", "profile_add"):
        claim = _flat_claim(op["claim"])
        if claim is None:
            return None
        tokens = match_tokens(claim)
        if _duplicate(tokens, seen.facts, DUPLICATE_FACT):
            return None
        seen.facts.append(tokens)
        return Proposal(kind, claim, evidence, why)
    # record_supersede
    target = records.get(op["target"])
    if target is None or not target.owned or target.memory_id in targeted:
        return None
    newest = max(
        (excerpts[item["episode"]].episode.finished_at for item in op["evidence"]), default=""
    )
    if newest <= target.created:
        return None
    if target.lesson is not None:
        if "trigger" not in op:
            return None
        claim = _lesson_claim(op["trigger"], op["action"], evidence)
    else:
        claim = _flat_claim(op["claim"]) if "claim" in op else None
    if claim is None or claim == target.claim:
        return None
    targeted.add(target.memory_id)
    return Proposal(
        "record_supersede", claim, evidence, why, target.memory_id, target.sha256, target.claim
    )


def dormant_retirements(
    records: Sequence[RecordView],
    items: Mapping[str, MemoryItem],
    stats: Mapping[str, LessonStat],
    now: datetime,
    *,
    open_targets: Iterable[str],
) -> list[Proposal]:
    """Code's own retirement proposals for this Bot's dormant lessons (`bot_recall.dormant`),
    leaving out a lesson that already has an open proposal; at most MAX_DORMANT."""
    taken = set(open_targets)
    found: list[Proposal] = []
    for record in records:
        item = items.get(record.memory_id)
        if record.lesson is None or not record.owned or item is None:
            continue
        if record.memory_id in taken or not dormant(item, stats.get(record.memory_id), now):
            continue
        stat = stats.get(record.memory_id)
        why = (
            f"Dormant: used {stat.uses} times, helpful {stat.helpful}, harmful {stat.harmful}."
            if stat is not None and stat.uses
            else "Dormant: not used in the last 60 days."
        )
        found.append(
            Proposal("record_retire", "", (), why, record.memory_id, record.sha256, record.claim)
        )
        if len(found) == MAX_DORMANT:
            break
    return found
