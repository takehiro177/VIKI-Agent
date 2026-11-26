"""Bot learning: what joins episodes, taint, the write fence, recall and approved proposals.

`BotLearning` is the one service the server binds (ADR-087, ADR-088, ADR-089):

- `fence(session_id)`, before each spawn of a Bot-bound session: starts watching its turns
  (`EpisodeRecorder`) and answers the session's write fence (`bot_fence.WriteFence`), which also
  stores its taint;
- `recall_text(...)`, from `ProfileLaunchService.bot_system_prompt`: the approved memory the
  session starts with (`bot_recall`), remembering which lessons it recalled;
- `observe(...)`, from the manager's observer: every session envelope, for the recorder;
- `apply`, `reject` and `undo`: the owner's decision on one sealed proposal. Only an approval
  writes the vault, through `MemoryService` (a journalled save), and only in an ordinary vault;
- `import_legacy`: code-only proposals made from the Bot's earlier auto-memory files, one per
  file, which the owner approves like any other.
"""

from __future__ import annotations

import asyncio
import json
import logging
import stat
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from anyio.to_thread import run_sync

from viki_agent.automation.routines import stamp
from viki_agent.decision_loop.improve import match_tokens
from viki_agent.domain import CanonicalRoot
from viki_agent.messaging.bot_episodes import EpisodeRecorder, EpisodeStore, SessionInfo
from viki_agent.messaging.bot_fence import FenceRoots, WriteFence
from viki_agent.messaging.bot_proposals import (
    ProposalStore,
    Run,
    Stored,
    summary,
)
from viki_agent.messaging.bot_recall import compose, creator_for
from viki_agent.messaging.bots import BotError, BotService
from viki_agent.messaging.dream_ops import DUPLICATE_FACT, Proposal
from viki_agent.storage import SessionStore, StateError
from viki_agent.vaults import lesson_body, memory_record
from viki_agent.vaults.memory import MemoryItem, MemoryService, MemoryServiceError
from viki_agent.vaults.memory_record import NoteSource, SessionSource, Source
from viki_agent.vaults.skill_proposals import SkillProposalError, SkillProposalService
from viki_agent.vaults.vault import BOT_MEMORY_FOLDER, VaultError, VaultService, _is_reparse
from viki_agent.workspaces import WorkspaceError, WorkspaceService, canonical_root

log = logging.getLogger(__name__)

MAX_IMPORT_FILES = 40
MAX_IMPORT_READS = 400  # earlier memory files one import call reads looking for new proposals
# A lesson used this often and found helpful more often than harmful is offered as a skill.
SKILL_USES = 10
MAX_SKILL_SESSIONS = 10
_RESULT_NAMESPACE = uuid.UUID("6f1f2b7e-5d43-4c4e-9a59-0b1c3e8f6a21")
ADD_OPS = frozenset({"lesson_add", "fact_add", "profile_add"})
# Memory refusals that mean the proposal's target moved on since it was checked.
STALE_CODES = frozenset(
    {"conflict", "stale_revision", "invalid_transition", "invalid_record", "missing_note"}
)


class LearningError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Admission:
    workspace_id: str
    policy_revision: int
    root_physical_id: str
    vault: Path


def result_id(proposal_id: str) -> str:
    """The memory id an approved add writes: fixed per proposal, so a retry meets its own
    first write rather than adding a second record."""
    return str(uuid.uuid5(_RESULT_NAMESPACE, proposal_id))


class BotLearning:
    def __init__(
        self,
        store: SessionStore,
        workspaces: WorkspaceService,
        bots: BotService,
        memories: MemoryService,
        vault: VaultService,
        data_dir: Path,
        *,
        tasks: set[asyncio.Task[Any]],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.workspaces = workspaces
        self.bots = bots
        self.memories = memories
        self.vault = vault
        self.data_dir = data_dir
        self.now = now
        self.episodes = EpisodeStore(store)
        self.proposals = ProposalStore(store)
        self.recorder = EpisodeRecorder(self.episodes, tasks=tasks, now=now)
        self.skills: SkillProposalService | None = None
        self._recalled: dict[str, tuple[str, ...]] = {}
        self._lock = threading.Lock()
        self._deciding: set[str] = set()

    def _stamp(self) -> str:
        return stamp(self.now())

    # --- sessions

    def fence(self, session_id: str) -> WriteFence | None:
        """Blocks. None for a session no Bot owns."""
        info = self.episodes.session(session_id)
        if info is None:
            return None
        try:
            workspace = self.workspaces.get(info.workspace_id)
        except WorkspaceError:
            raise LearningError("workspace_missing") from None
        vault = Path(workspace.root.display)
        with self._lock:
            lessons = self._recalled.get(session_id, ())
        self.recorder.watch(session_id, info, lessons)
        return WriteFence(
            FenceRoots(cwd=vault, vault=vault, app_memory=self.data_dir / BOT_MEMORY_FOLDER),
            tainted=lambda: self.episodes.tainted(session_id),
            mark=lambda source: self._mark(session_id, info, source),
        )

    def _mark(self, session_id: str, info: SessionInfo, source: str) -> None:
        self.episodes.mark_tainted(session_id, info, source, self._stamp())

    def observe(self, session_id: str, envelope: dict[str, Any]) -> None:
        self.recorder.observe(session_id, envelope)

    def recall_text(self, workspace_id: str, bot_id: str, session_id: str) -> str:
        """Blocks. The recall blocks for this spawn, "" when the vault's records can't be read."""
        try:
            listing = self.memories.list(workspace_id)
            stats = self.proposals.stats(workspace_id, bot_id)
        except (MemoryServiceError, StateError) as error:
            log.warning("Bot recall skipped: %s", type(error).__name__)
            return ""
        recall = compose(listing.items, bot_id, stats, self.now())
        with self._lock:
            self._recalled[session_id] = recall.lesson_ids
        return recall.text

    def untaint(self, session_id: str) -> bool:
        return self.episodes.untaint(session_id)

    # --- the owner's view

    def bot(self, bot_id: str) -> dict[str, Any]:
        try:
            return self.bots.get(bot_id)
        except BotError as error:
            raise LearningError(error.code) from None

    def admission(self, workspace_id: str) -> Admission:
        """The vault an approval writes to: registered, persistent and ordinary."""
        try:
            workspace = self.workspaces.get(workspace_id)
        except WorkspaceError:
            raise LearningError("workspace_missing") from None
        if not workspace.persistent or workspace.policy != "ordinary":
            raise LearningError("vault_not_ordinary")
        return Admission(
            workspace_id,
            workspace.policy_revision,
            workspace.root.physical_id,
            Path(workspace.root.display),
        )

    def overview(self, bot_id: str) -> dict[str, Any]:
        """Blocks. The Bot's runs, proposals, lessons and tainted sessions."""
        bot = self.bot(bot_id)
        workspace_id = bot["workspace_id"]
        stats = self.proposals.stats(workspace_id, bot_id)
        lessons: list[dict[str, Any]] = []
        records_readable = True
        try:
            items = self.memories.list(workspace_id).items
        except MemoryServiceError:
            items, records_readable = (), False
        mine = creator_for(bot_id)
        for item in items:
            if item.fields.creator != mine or item.state == "retired":
                continue
            stat = stats.get(item.fields.id)
            lessons.append(
                {
                    "memory_id": item.fields.id,
                    "ref": item.ref,
                    "state": item.state,
                    "claim": item.claim,
                    "uses": stat.uses if stat else 0,
                    "helpful": stat.helpful if stat else 0,
                    "harmful": stat.harmful if stat else 0,
                    "last_used_at": stat.last_used_at if stat else None,
                }
            )
        return {
            "bot_id": bot_id,
            "workspace_id": workspace_id,
            "records_readable": records_readable,
            "runs": [run.__dict__ for run in self.proposals.runs(bot_id)],
            "proposals": [summary(stored) for stored in self.proposals.listing(bot_id)],
            "records": lessons,
            "tainted_sessions": self.episodes.tainted_sessions(bot_id),
            "episodes": {
                "unreflected": self.episodes.count(bot_id, after=None, reflected=False),
                "last_finished_at": self.episodes.last_finished(bot_id),
            },
            "legacy_files": self._legacy_count(workspace_id, bot_id),
        }

    # --- decisions

    async def _claim(self, proposal_id: str) -> Stored:
        with self._lock:
            if proposal_id in self._deciding:
                raise LearningError("conflict")
            self._deciding.add(proposal_id)
        try:
            return await run_sync(self.proposals.get, proposal_id)
        except BaseException:
            self._release(proposal_id)
            raise

    def _release(self, proposal_id: str) -> None:
        with self._lock:
            self._deciding.discard(proposal_id)

    async def reject(self, proposal_id: str) -> None:
        stored = await self._claim(proposal_id)
        try:
            if stored.state != "open":
                raise LearningError("conflict")
            await run_sync(
                lambda: self.proposals.decide(
                    proposal_id, expected="open", state="rejected", now=self._stamp()
                )
            )
        finally:
            self._release(proposal_id)

    async def apply(self, proposal_id: str) -> str:
        """Apply one open proposal; answers its new state (`applied` or `stale`)."""
        stored = await self._claim(proposal_id)
        try:
            if stored.state != "open":
                raise LearningError("conflict")
            admission = await run_sync(self.admission, stored.workspace_id)
            try:
                result = await self._write(stored, admission)
            except MemoryServiceError as error:
                if error.code not in STALE_CODES:
                    raise LearningError(error.code) from None
                await run_sync(
                    lambda: self.proposals.decide(
                        proposal_id, expected="open", state="stale", now=self._stamp()
                    )
                )
                return "stale"
            await run_sync(
                lambda: self.proposals.decide(
                    proposal_id,
                    expected="open",
                    state="applied",
                    now=self._stamp(),
                    result_id=result,
                )
            )
            return "applied"
        finally:
            self._release(proposal_id)

    async def _write(self, stored: Stored, admission: Admission) -> str | None:
        body = stored.body
        args = (admission.workspace_id, admission.policy_revision, admission.root_physical_id)
        op = stored.op
        if op in ADD_OPS:
            written = await self.memories.write_approved(
                *args,
                memory_id=result_id(stored.proposal_id),
                kind="owner" if op == "profile_add" else "assistant",
                claim=body["claim"],
                sources=_sources(body),
                creator=None if op == "profile_add" else creator_for(stored.bot_id),
            )
            return written.memory_id
        if stored.target_id is None or stored.target_sha256 is None:
            raise LearningError("corrupt")
        if op == "record_retire":
            await self.memories.retire(
                admission.workspace_id,
                stored.target_id,
                stored.target_sha256,
                admission.policy_revision,
                admission.root_physical_id,
            )
            return None
        corrected = await self.memories.correct(
            admission.workspace_id,
            stored.target_id,
            stored.target_sha256,
            body["claim"],
            admission.policy_revision,
            admission.root_physical_id,
            sources=_sources(body),
        )
        return corrected.record.memory_id

    async def undo(self, proposal_id: str) -> None:
        """Retire the record an applied add wrote; other ops have no undo."""
        stored = await self._claim(proposal_id)
        try:
            if stored.state != "applied" or stored.op not in ADD_OPS or not stored.result_id:
                raise LearningError("not_undoable")
            admission = await run_sync(self.admission, stored.workspace_id)
            listing = await run_sync(self.memories.list, admission.workspace_id)
            item = next((i for i in listing.items if i.fields.id == stored.result_id), None)
            if item is not None and item.state != "retired":
                try:
                    await self.memories.retire(
                        admission.workspace_id,
                        item.fields.id,
                        item.sha256,
                        admission.policy_revision,
                        admission.root_physical_id,
                    )
                except MemoryServiceError as error:
                    raise LearningError(error.code) from None
            await run_sync(
                lambda: self.proposals.decide(
                    proposal_id, expected="applied", state="undone", now=self._stamp()
                )
            )
        finally:
            self._release(proposal_id)

    # --- proven lessons

    def offer_skills(self, workspace_id: str, bot_id: str, bot_name: str) -> int:
        """Blocks. Offer each of the Bot's active lessons used at least SKILL_USES times, and
        found helpful more often than harmful, as a skill proposal (`SkillProposalService`, its
        `repeated_request` trigger) naming the sessions it was learned from. A name already held
        by a proposal or a skill is left alone. Answers how many were offered."""
        if self.skills is None:
            return 0
        stats = self.proposals.stats(workspace_id, bot_id)
        try:
            items = self.memories.list(workspace_id).items
        except MemoryServiceError:
            return 0
        offered = 0
        for item in items:
            stat = stats.get(item.fields.id)
            lesson = lesson_body.parse(item.claim)
            if (
                item.state != "active"
                or item.fields.creator != creator_for(bot_id)
                or lesson is None
                or stat is None
                or stat.uses < SKILL_USES
                or stat.helpful <= stat.harmful
            ):
                continue
            sessions = [
                source.session
                for source in item.fields.sources
                if isinstance(source, SessionSource)
            ][:MAX_SKILL_SESSIONS]
            if not sessions:
                continue
            try:
                self.skills.create(
                    workspace_id,
                    skill_text(item.fields.id, lesson, bot_name),
                    "repeated_request",
                    sessions,
                )
            except SkillProposalError:
                continue
            offered += 1
        return offered

    # --- earlier auto memory

    def _legacy_refs(self, workspace_id: str, bot_id: str) -> list[str]:
        """This Bot's earlier memory file refs, sorted, within its own `bot-memory/<bot_id>/`
        folder - never a whole-vault scan, which could stop (`VaultService.MAX_ENTRIES`,
        `MAX_VISITED_ITEMS`) before reaching it in a large vault."""
        area = _LegacyArea(self.workspaces, bot_id)
        try:
            scan = area.scan(workspace_id)
        except VaultError:
            return []
        prefix = f"{BOT_MEMORY_FOLDER}/{bot_id}/"
        names = sorted(
            entry.ref
            for entry in scan.entries
            if entry.ref.casefold().endswith(".md")
            and PurePosixPath(entry.ref).name.casefold() != "memory.md"
        )
        return [f"{prefix}{name}" for name in names]

    def _legacy_count(self, workspace_id: str, bot_id: str) -> int:
        """How many of this Bot's earlier memory files exist in its folder: what the "Import
        earlier memory ({count} files)" label names. Cheap, since the overview reads this often
        - never decodes a proposal's sealed body, only lists the folder `_legacy_refs` already
        lists cheaply. A later import that finds nothing new among them still finishes as
        `nothing_new`, so this is a plain file count, not a prediction of what a click offers."""
        try:
            self.admission(workspace_id)
        except LearningError:
            return 0
        return len(self._legacy_refs(workspace_id, bot_id))

    def import_legacy(self, bot_id: str, trigger: str = "user") -> Run:
        """Blocks. Up to MAX_IMPORT_FILES new `fact_add` proposals, one per earlier memory file
        of this Bot (its body, without frontmatter), reading at most MAX_IMPORT_READS of them in
        sorted order so a Bot with many earlier files is imported over several calls. A ref
        already behind an open, rejected or applied proposal (`ProposalStore.fact_add_notes`,
        every one, not the owner-facing window `listing` keeps) is skipped before it can spend
        any of that read budget - an approved file must not keep paying to be re-discovered as a
        duplicate of the kept record it became, which would starve the files behind it. Among
        the rest, skips a file whose claim repeats one already kept (and not retired), open, or
        rejected."""
        bot = self.bot(bot_id)
        workspace_id = bot["workspace_id"]
        self.admission(workspace_id)
        refs = self._legacy_refs(workspace_id, bot_id)
        if not refs:
            raise LearningError("nothing_to_import")
        try:
            items: tuple[MemoryItem, ...] = self.memories.list(workspace_id).items
        except MemoryServiceError as error:
            raise LearningError(error.code) from None
        seen = [match_tokens(item.claim) for item in items if item.state != "retired"]
        noted: set[str] = set()
        for stored in self.proposals.fact_add_notes(bot_id):
            claim = stored.body.get("claim", "")
            if claim:
                seen.append(match_tokens(claim))
            for note in stored.body.get("notes", []):
                if isinstance(note, dict) and isinstance(note.get("note"), str):
                    noted.add(note["note"])
        proposals: list[Proposal] = []
        read = 0
        for ref in refs:
            if len(proposals) >= MAX_IMPORT_FILES:
                break
            if ref in noted:
                continue  # already proposed: skip for free, never spending the read budget
            if read >= MAX_IMPORT_READS:
                break
            read += 1
            try:
                note = self.vault.read(workspace_id, ref)
            except VaultError:
                continue
            claim = _body(note.source)
            if claim is None:
                continue
            tokens = match_tokens(claim)
            if any(_overlap(tokens, other) >= DUPLICATE_FACT for other in seen):
                continue
            seen.append(tokens)
            proposals.append(
                Proposal(
                    "fact_add",
                    claim,
                    (),
                    f"From this Bot's earlier memory file {ref}.",
                    notes=((ref, note.sha256),),
                )
            )
        run = self.proposals.start_run(workspace_id, bot_id, "import", "user", None, self._stamp())
        stored_count = self.proposals.add(run, proposals, self._stamp())
        self.proposals.finish_run(
            run.run_id,
            reason="" if stored_count else "nothing_new",
            episodes=0,
            proposed=stored_count,
            rejected=read - stored_count,
            now=self._stamp(),
        )
        return run


class _LegacyArea(VaultService):
    """`VaultService` rooted at one Bot's earlier memory folder inside the vault
    (`bot-memory/<bot_id>/`), so listing it touches only that folder - never the whole vault.
    The chain down to the vault root is the normal registered one (`VaultService._root`); these
    last two levels get the reparse-point and directory checks `BotMemoryArea` applies to its
    own (app-data) folder, then `canonical_root` re-derives the physical identity the scan's
    budgets and checks (`VaultService.scan`) run against, exactly as for any other root."""

    def __init__(self, workspaces: WorkspaceService, bot_id: str) -> None:
        super().__init__(workspaces)
        self._bot_id = bot_id

    def _root(self, workspace_id: str) -> CanonicalRoot:
        root = super()._root(workspace_id)
        area = Path(root.display, BOT_MEMORY_FOLDER, self._bot_id)
        for level in (area.parent, area):
            try:
                info = level.lstat()
            except FileNotFoundError:
                raise VaultError("missing_folder") from None
            except OSError as exc:
                raise VaultError("root_unavailable") from exc
            if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise VaultError("unsafe_path")
        try:
            return canonical_root(str(area), deny=())
        except WorkspaceError as exc:
            raise VaultError("root_unavailable") from exc


def skill_text(memory_id: str, lesson: lesson_body.Lesson, bot_name: str) -> str:
    """The `SKILL.md` a proven lesson is offered as: named by its record, described by its
    trigger, its action and first example as the body."""
    trigger = " ".join(lesson.trigger.split()).replace('"', "'")
    description = f"{trigger} (a lesson {' '.join(bot_name.split())} learned)"[:200]
    lines = [
        "---",
        f"name: lesson-{memory_id[:8]}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        "---",
        "",
        f"# {trigger}",
        "",
        lesson.action,
    ]
    if lesson.examples:
        lines += ["", f'Example from earlier work: "{lesson.examples[0]}"']
    return "\n".join(lines) + "\n"


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


def _body(source: str) -> str | None:
    """A memory file's text as one record claim: frontmatter dropped, blank lines and trailing
    spaces dropped; None when nothing is left or it is too large for a record."""
    text = source.lstrip("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            lines = lines[end + 1 :]
    claim = "\n".join(line.rstrip() for line in lines if line.strip())
    if not claim or "\r" in claim:
        return None
    if len(claim.encode("utf-8")) > memory_record.MAX_CLAIM_BYTES:
        return None
    return claim


def _sources(body: dict[str, Any]) -> list[Source]:
    sources: list[Source] = []
    seen: set[str] = set()
    for item in body.get("evidence", []):
        session = item.get("session_id")
        if isinstance(session, str) and session not in seen:
            seen.add(session)
            sources.append(SessionSource(session))
    for item in body.get("notes", []):
        if isinstance(item, dict) and isinstance(item.get("note"), str):
            sources.append(NoteSource(item["note"], item["sha256"]))
    return sources[: memory_record.MAX_SOURCES]
