"""The memory service (MEMORY-01/02): list, approve, retire, correct, narrow scope and promote.

A memory record is an ordinary note, `memory/<id>.md`, in `memory_record`'s grammar, so every vault
write here is one journalled save through `VaultPublicationService.save` against the record's
current SHA-256, or its absence for a new record (hard rule 1, `AGENTS.md`): there is no writer of
its own. Authority is local: a record is `active` only while this device's `memory_reviews` row
holds its exact hash, so a file gains nothing from saying `approved`. A protected vault takes no
memory write; there `promote` seals the composed record as a proposal instead.

A correction is two saves in a fixed order: the new record, whose `supersedes` names the old one,
then the old one retired with `retired_by`. A cut between them leaves both records live and the new
one naming what it replaces, which `list` reports as the old record's `superseded_by`, so the user
can finish it with `retire(..., retired_by=...)`.

`list` blocks on vault reads, so a caller on the event loop runs it in a thread; the write methods
are coroutines that do their own blocking work in threads.
"""

from __future__ import annotations

import dataclasses
import hashlib
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from anyio.to_thread import run_sync

from viki_agent.automation.routines import stamp
from viki_agent.storage import SessionStore
from viki_agent.vaults import memory_record
from viki_agent.vaults.memory_record import NOT_A_RECORD, Fields, Invalid, Record, Source, State
from viki_agent.vaults.proposals import ProposalError, ProposalService
from viki_agent.vaults.vault import MAX_SCAN_BYTES, VaultError, VaultService
from viki_agent.vaults.vault_mutation import MutationError
from viki_agent.vaults.vault_publication import (
    FolderOutcome,
    PublicationError,
    VaultPublicationService,
    _canonical_uuid,
    _valid_hash,
)
from viki_agent.workspaces import WorkspaceError, WorkspaceService

FOLDER = "memory"


class MemoryServiceError(Exception):
    """A typed refusal. `extra` carries only ids, revisions and grammar reasons, never a claim."""

    def __init__(self, code: str, **extra: Any) -> None:
        self.code = code
        self.extra = extra
        super().__init__(code)


@dataclass(frozen=True)
class MemoryItem:
    """A record that parses. `sha256` is the file's revision, the one a write must name.
    `superseded_by` names a record, active on this device, whose `supersedes` is this one while
    this one is not retired: a correction cut between its two saves, finished by retiring this
    one with `retired_by`. A record with no authority here never marks another."""

    ref: str
    sha256: str
    state: State
    fields: Fields
    claim: str
    superseded_by: str | None


@dataclass(frozen=True)
class InvalidMemory:
    """A file under `memory/` with the marker that breaks the grammar (its `Invalid` reason and raw
    text), or one the reader refused (the reader's code and no text). Never usable."""

    ref: str
    reason: str
    text: str | None
    state: Literal["invalid"] = "invalid"


@dataclass(frozen=True)
class MemoryList:
    items: tuple[MemoryItem, ...]
    invalid: tuple[InvalidMemory, ...]
    partial: bool  # the vault scan was partial, or the read budget ran out


@dataclass(frozen=True)
class Reviewed:
    """This device recorded the record's hash; no vault byte was written."""

    memory_id: str
    sha256: str
    outcome: Literal["reviewed"] = "reviewed"


@dataclass(frozen=True)
class Written:
    """One journalled save landed; `sha256` is the record file's new revision.
    `created_folders` names the folders a new record's create made, e.g. `("memory",)`.
    A replayed create is the record as it is now and has no `operation_id`: the save that
    made it is not looked up."""

    memory_id: str
    ref: str
    sha256: str
    operation_id: str | None
    created_folders: tuple[str, ...] = ()
    outcome: Literal["written"] = "written"


@dataclass(frozen=True)
class Corrected:
    record: Written  # the new record, saved first
    retired: Written  # the old record, retired second
    outcome: Literal["corrected"] = "corrected"


@dataclass(frozen=True)
class Proposed:
    """A protected vault's promotion: the record sealed as a proposal, nothing written."""

    memory_id: str
    ref: str
    proposal_id: str
    proposed_sha256: str
    outcome: Literal["proposed"] = "proposed"


def _ref(memory_id: str) -> str:
    return f"{FOLDER}/{memory_id}.md"


class MemoryService:
    def __init__(
        self,
        workspaces: WorkspaceService,
        store: SessionStore,
        vault: VaultService,
        publications: VaultPublicationService,
        proposals: ProposalService,
        *,
        lease_wait_s: float,
        wall: Callable[[], int] = time.time_ns,
    ) -> None:
        self._workspaces = workspaces
        self._store = store
        self._vault = vault
        self._publications = publications
        self._proposals = proposals
        self._lease_wait_s = lease_wait_s
        self._wall = wall

    def list(self, workspace_id: str) -> MemoryList:
        """Every `memory/<name>.md` directly under the root, read now; nothing is cached."""
        try:
            scan = self._vault.scan(workspace_id)
        except VaultError as exc:
            raise MemoryServiceError(exc.code) from None
        approved = self._approved(workspace_id)
        partial = scan.partial
        budget = MAX_SCAN_BYTES
        records: list[tuple[str, str, Record]] = []
        invalid: list[InvalidMemory] = []
        for entry in scan.entries:
            parts = entry.ref.split("/")
            if len(parts) != 2 or parts[0] != FOLDER or not parts[1].casefold().endswith(".md"):
                continue
            if budget == 0:
                partial = True
                break
            try:
                note = self._vault.read(workspace_id, entry.ref)
            except VaultError as exc:
                read_bytes = exc.extra.get("read_bytes", 0)
                if read_bytes > budget:
                    partial = True
                    break
                budget -= read_bytes
                if exc.code != "missing_note":  # one removed since the scan is simply gone
                    invalid.append(InvalidMemory(entry.ref, exc.code, None))
                continue
            if note.byte_size > budget:
                partial = True
                break
            budget -= note.byte_size
            # `Note.source` keeps a BOM, so the record's hash is the file's; never route a
            # record through `Note.frontmatter`, a lenient reader.
            parsed = memory_record.parse(note.source)
            if isinstance(parsed, Invalid):
                if parsed.reason != NOT_A_RECORD:  # no marker: an ordinary note
                    invalid.append(InvalidMemory(entry.ref, parsed.reason, note.source))
            elif parsed.fields.id != PurePosixPath(entry.ref).stem:
                invalid.append(InvalidMemory(entry.ref, "bad_id", note.source))
            else:
                records.append((entry.ref, note.sha256, parsed))
        live = {record.fields.id for _, _, record in records if record.fields.status != "retired"}
        # Only a replacement active on this device counts, as `correct` saves its own: a candidate
        # or pending record another tool wrote must not offer to retire the record it names.
        superseded = {
            record.fields.supersedes: record.fields.id
            for _, _, record in records
            if record.fields.supersedes in live
            and memory_record.effective(record, approved.get(record.fields.id)) == "active"
        }
        items = tuple(
            MemoryItem(
                ref,
                revision,
                memory_record.effective(record, approved.get(record.fields.id)),
                record.fields,
                record.claim,
                superseded.get(record.fields.id),
            )
            for ref, revision, record in records
        )
        return MemoryList(items, tuple(invalid), partial)

    async def approve(
        self,
        workspace_id: str,
        memory_id: str,
        sha256: str,
        policy_revision: int,
        root_physical_id: str,
    ) -> Reviewed | Written:
        """A file already `approved` (and not an inference) only has its hash recorded, in every
        policy, so devices sharing a vault never rewrite each other's approvals. A candidate or an
        inference is written `approved`, an inference as `assistant`, and that hash recorded."""
        policy, record = await run_sync(lambda: self._load(workspace_id, memory_id, sha256))
        fields = record.fields
        if fields.status == "retired":
            raise MemoryServiceError("invalid_transition")
        if fields.status == "approved" and fields.kind != "inference":
            await run_sync(lambda: self._review(workspace_id, memory_id, sha256))
            return Reviewed(memory_id, sha256)
        _require_writable(policy)
        kind = "assistant" if fields.kind == "inference" else fields.kind
        approved = dataclasses.replace(fields, kind=kind, status="approved", reviewed=self._stamp())
        return await self._save(
            workspace_id, policy_revision, root_physical_id, approved, record.claim, sha256, True
        )

    async def retire(
        self,
        workspace_id: str,
        memory_id: str,
        sha256: str,
        policy_revision: int,
        root_physical_id: str,
        *,
        retired_by: str | None = None,
    ) -> Written:
        policy, record = await run_sync(lambda: self._load(workspace_id, memory_id, sha256))
        _require_writable(policy)
        if record.fields.status == "retired":
            raise MemoryServiceError("invalid_transition")
        retired = dataclasses.replace(
            record.fields, status="retired", reviewed=self._stamp(), retired_by=retired_by
        )
        return await self._save(
            workspace_id, policy_revision, root_physical_id, retired, record.claim, sha256, False
        )

    async def correct(
        self,
        workspace_id: str,
        memory_id: str,
        sha256: str,
        claim: str,
        policy_revision: int,
        root_physical_id: str,
        *,
        sources: Sequence[Source] | None = None,
    ) -> Corrected:
        """Save an approved replacement naming the old record, then retire the old one: the order
        whose interruption loses nothing. A refusal of the second save carries the new
        record's id as `replacement_id`; `list` then offers to finish. The replacement keeps the
        old record's kind (an inference becomes `assistant`), scope and creator, and its sources
        unless `sources` names new ones."""
        policy, record = await run_sync(lambda: self._load(workspace_id, memory_id, sha256))
        _require_writable(policy)
        old = record.fields
        if old.status == "retired":
            raise MemoryServiceError("invalid_transition")
        now = self._stamp()
        replacement = Fields(
            str(uuid.uuid4()),
            "assistant" if old.kind == "inference" else old.kind,
            old.scope,
            "approved",
            now,
            reviewed=now,
            creator=old.creator,
            sources=old.sources if sources is None else tuple(sources),
            supersedes=old.id,
        )
        retired = dataclasses.replace(
            old, status="retired", reviewed=now, retired_by=replacement.id
        )
        _compose(retired, record.claim)  # refuse before the first save, not between the two
        new = await self._save(
            workspace_id, policy_revision, root_physical_id, replacement, claim, None, True
        )
        try:
            gone = await self._save(
                workspace_id,
                policy_revision,
                root_physical_id,
                retired,
                record.claim,
                sha256,
                False,
            )
        except MemoryServiceError as exc:
            raise MemoryServiceError(exc.code, **exc.extra, replacement_id=new.memory_id) from None
        return Corrected(new, gone)

    async def write_approved(
        self,
        workspace_id: str,
        policy_revision: int,
        root_physical_id: str,
        *,
        memory_id: str,
        kind: str,
        claim: str,
        sources: Sequence[Source],
        creator: str | None,
    ) -> Written:
        """A new record written `approved` in one journalled save, and its hash recorded: the
        owner approved this exact text already, in a Bot learning proposal (ADR-088). Only an
        ordinary vault takes it. A retry with the same `memory_id` meets the record its own first
        try wrote and answers it, as `promote` does."""
        if memory_id != str(uuid.UUID(memory_id)):
            raise MemoryServiceError("invalid_request")
        policy = await run_sync(lambda: self._policy(workspace_id))
        _require_writable(policy)
        if policy != "ordinary":
            raise MemoryServiceError("read_only")
        now = self._stamp()
        fields = Fields(
            memory_id,
            kind,
            "vault",
            "approved",
            now,
            reviewed=now,
            creator=creator,
            sources=tuple(sources),
        )
        try:
            return await self._save(
                workspace_id, policy_revision, root_physical_id, fields, claim, None, True
            )
        except MemoryServiceError as exc:
            if exc.code != "conflict":
                raise
            replay = await run_sync(lambda: self._written_replay(workspace_id, memory_id))
            if replay is None:
                raise
            # Only this command's first try writes a fresh id: approve what it saved.
            await run_sync(lambda: self._review(workspace_id, memory_id, replay.sha256))
            return replay

    async def narrow_scope(
        self,
        workspace_id: str,
        memory_id: str,
        sha256: str,
        policy_revision: int,
        root_physical_id: str,
    ) -> Written:
        """`personal` becomes `vault`; this device's approval moves to the new bytes only if it
        held the old ones."""
        policy, record = await run_sync(lambda: self._load(workspace_id, memory_id, sha256))
        _require_writable(policy)
        if record.fields.status == "retired" or record.fields.scope != "personal":
            raise MemoryServiceError("invalid_transition")
        approved = await run_sync(lambda: self._approved(workspace_id).get(memory_id))
        active = memory_record.effective(record, approved) == "active"
        narrowed = dataclasses.replace(record.fields, scope="vault")
        return await self._save(
            workspace_id, policy_revision, root_physical_id, narrowed, record.claim, sha256, active
        )

    async def promote(
        self,
        workspace_id: str,
        policy_revision: int,
        root_physical_id: str,
        *,
        command_id: str,
        kind: str,
        scope: str,
        claim: str,
        sources: Sequence[Source] = (),
    ) -> tuple[Written | Proposed, bool]:
        """A new `candidate` record, `command_id` its id: written as a new note in an ordinary
        vault, or in a protected one sealed as a `memory_promotion` proposal that applies nothing.
        The answer's flag is a replay: a retry meets the record (or proposal) its own first try
        made, since only that command can have minted the id, and gets it back. Whatever
        else sits at the ref, a record naming another id or no record at all, stays a conflict."""
        if not _canonical_uuid(command_id):
            raise MemoryServiceError("invalid_request")
        policy = await run_sync(lambda: self._policy(workspace_id))
        fields = Fields(command_id, kind, scope, "candidate", self._stamp(), sources=tuple(sources))
        if policy != "protected":
            try:
                return (
                    await self._save(
                        workspace_id, policy_revision, root_physical_id, fields, claim, None, False
                    ),
                    False,
                )
            except MemoryServiceError as exc:
                replay = None
                if exc.code == "conflict":
                    replay = await run_sync(lambda: self._written_replay(workspace_id, command_id))
                if replay is None:
                    raise
                return replay, True
        ref = _ref(command_id)
        text = _compose(fields, claim)
        try:
            item = await run_sync(
                lambda: self._proposals.create(
                    workspace_id, ref, text, None, origin="memory_promotion"
                )
            )
        except ProposalError as exc:
            # A retry's text carries a new `created` stamp, so its own proposal is a conflict and
            # only a same-instant one is `proposal_exists`: either way the row at the ref decides.
            replay = (
                await run_sync(lambda: self._proposed_replay(workspace_id, command_id))
                if exc.code in {"proposal_exists", "proposal_conflict"}
                else None
            )
            if replay is None:
                raise MemoryServiceError(exc.code, **exc.extra) from None
            return replay, True
        return Proposed(command_id, ref, str(item["id"]), str(item["revision"])), False

    # --- internals ------------------------------------------------------------------------

    def _policy(self, workspace_id: str) -> str:
        try:
            return self._workspaces.get(workspace_id).policy
        except WorkspaceError:
            raise MemoryServiceError("unknown_workspace") from None

    def _load(self, workspace_id: str, memory_id: str, sha256: str) -> tuple[str, Record]:
        """The workspace's policy and the record as read now, which must still be `sha256`."""
        if not (_canonical_uuid(memory_id) and isinstance(sha256, str) and _valid_hash(sha256)):
            raise MemoryServiceError("invalid_request")
        policy = self._policy(workspace_id)
        try:
            note = self._vault.read(workspace_id, _ref(memory_id))
        except VaultError as exc:
            raise MemoryServiceError(exc.code) from None
        if note.sha256 != sha256:
            raise MemoryServiceError("stale_revision", current_revision=note.sha256)
        parsed = memory_record.parse(note.source)
        if isinstance(parsed, Invalid):
            raise MemoryServiceError("invalid_record", reason=parsed.reason)
        if parsed.fields.id != memory_id:
            raise MemoryServiceError("invalid_record", reason="bad_id")
        return policy, parsed

    def _written_replay(self, workspace_id: str, memory_id: str) -> Written | None:
        """The record already at the ref when it names `memory_id`; its original operation is not
        known, so `operation_id` is None."""
        try:
            note = self._vault.read(workspace_id, _ref(memory_id))
        except VaultError:
            return None
        parsed = memory_record.parse(note.source)
        if isinstance(parsed, Invalid) or parsed.fields.id != memory_id:
            return None
        return Written(memory_id, _ref(memory_id), note.sha256, None)

    def _proposed_replay(self, workspace_id: str, memory_id: str) -> Proposed | None:
        """The `memory_promotion` proposal already held at the ref; only the command that minted
        `memory_id` makes one there."""
        ref = _ref(memory_id)
        for row in self._store.list_vault_proposals(workspace_id):
            if row["ref"] == ref and row["origin"] == "memory_promotion":
                return Proposed(memory_id, ref, row["proposal_id"], row["proposed_sha256"])
        return None

    async def _save(
        self,
        workspace_id: str,
        policy_revision: int,
        root_physical_id: str,
        fields: Fields,
        claim: str,
        expected_sha256: str | None,
        review: bool,
    ) -> Written:
        """One journalled save of the composed record; with `review`, then record its hash."""
        content = _compose(fields, claim).encode("utf-8")
        ref = _ref(fields.id)
        try:
            outcome = await self._publications.save(
                workspace_id,
                policy_revision,
                root_physical_id,
                ref,
                content,
                expected_sha256=expected_sha256,
                # Only a new record's create may make `memory/`; a rewrite against an
                # expected hash needs its folder to exist already.
                create_folders=expected_sha256 is None,
                wait_s=self._lease_wait_s,
            )
        except PublicationError as exc:
            raise MemoryServiceError(exc.code, operation_id=exc.operation_id) from None
        except MutationError as exc:
            raise MemoryServiceError(exc.code) from None
        if outcome.status == "conflict":
            raise MemoryServiceError("conflict", operation_id=outcome.operation_id)
        digest = hashlib.sha256(content).hexdigest()
        if review:
            await run_sync(lambda: self._review(workspace_id, fields.id, digest))
        created = outcome.created_folders if isinstance(outcome, FolderOutcome) else ()
        return Written(fields.id, ref, digest, outcome.operation_id, created)

    def _approved(self, workspace_id: str) -> dict[str, str]:
        """This device's approved hash per memory id; the table has no accessor, so this
        reaches it the way `vault_operations` reaches its own tables."""
        with self._store._connection() as connection:
            rows = connection.execute(
                "SELECT memory_id,sha256 FROM memory_reviews WHERE workspace_id=?",
                (workspace_id,),
            ).fetchall()
        return {memory_id: digest for memory_id, digest in rows}

    def _review(self, workspace_id: str, memory_id: str, sha256: str) -> None:
        with self._store._connection(write=True) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO memory_reviews (workspace_id,memory_id,sha256,reviewed_at) "
                "VALUES (?,?,?,?)",
                (workspace_id, memory_id, sha256, self._stamp()),
            )

    def _stamp(self) -> str:
        return stamp(datetime.fromtimestamp(self._wall() / 1_000_000_000, UTC))


def reviews(store: SessionStore, workspace_id: str) -> dict[str, tuple[str, str]]:
    """Each memory id to the hash this device approved and when; reads only."""
    with store._connection() as connection:
        rows = connection.execute(
            "SELECT memory_id,sha256,reviewed_at FROM memory_reviews WHERE workspace_id=?",
            (workspace_id,),
        ).fetchall()
    return {memory_id: (digest, at) for memory_id, digest, at in rows}


def _require_writable(policy: str) -> None:
    if policy == "protected":
        raise MemoryServiceError("vault_protected")


def _compose(fields: Fields, claim: str) -> str:
    try:
        return memory_record.compose(fields, claim)
    except ValueError as exc:
        raise MemoryServiceError("invalid_input", reason=str(exc)) from None
