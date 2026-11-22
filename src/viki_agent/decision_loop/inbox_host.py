"""Decision's one port into the inbox suggestion run.

`SuggestionHost` implements `contracts.InboxHost` over the app's one `CaptureSuggester`, the run
**Ask VIKI to suggest** starts. `read` answers from the suggester's memory only: its latest run for
the vault (`run_for`), the modified stamp of each suggestion it keeps (`suggestions`) and the item
keys this host started. It does no I/O and takes no lock, so it runs on the event loop.

`start` reaches the suggester through one call, `CaptureSuggester.start`, at most once per item key.
The start is bound to the item's own inbox state (`capture_suggest.bound_listing`): when the
suggester's fresh listing is another state (`inbox_triage.state_key`), as when a capture landed
since the cycle checked Never send against the listing, it refuses with `inbox_changed` and sends
nothing. A key still starting answers `busy`; a key started before answers `not_applicable`. The
suggester's `suggestion_running` (another run goes) answers `busy` and keeps nothing, so the key can
start once that run ends. Any other refusal, `inbox_changed` among them, is kept as "" and answers
`not_applicable` (a `CaptureError`) or `unavailable` (a `StateError`); it is not asked again. The
start runs in a task of its own, kept until done and awaited through `asyncio.shield`: a caller
cancelled while the suggester lists the captures leaves the start running, and its done callback
records the run id under the key. The host keeps the newest `MAX_STARTED` keys of each vault, so
another vault's starts never evict a vault's current state, whose key is that vault's newest; `read`
answers the vault's own keys.

With a `SuggestionMemory` the kept keys outlive a restart: they are loaded as the host is built and
each vault's keys are saved, in a task of their own, whenever one is recorded. `persistent` says
whether they were loaded, so the engine knows a key started before a restart is still known.

`close` is the shutdown step: it marks the host closed, cancels every start still running and awaits
them, so none creates a session after it returns, then awaits the saves still writing. A `start`
after `close` answers `unavailable` before it reserves a key.

It calls nothing else of the suggester. The run's cards stay the suggester's own: this module
answers none and changes no session's mode. It logs nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from functools import partial
from typing import Protocol

from viki_agent.decision_loop.contracts import DecisionLoopError, InboxLook, InboxRun
from viki_agent.decision_loop.inbox_triage import state_key
from viki_agent.storage import StateError
from viki_agent.vaults.capture_reply import Suggestion
from viki_agent.vaults.capture_suggest import SuggestionRun, bound_listing
from viki_agent.vaults.captures import CaptureError, CaptureListing
from viki_agent.vaults.suggestion_memory import SuggestionMemory

MAX_STARTED = 64


class _Suggester(Protocol):
    """What the port reaches of `CaptureSuggester`."""

    def run_for(self, workspace_id: str) -> SuggestionRun | None: ...

    def suggestions(self, workspace_id: str) -> Mapping[str, Suggestion]: ...

    async def start(self, workspace_id: str) -> SuggestionRun: ...


class SuggestionHost:
    def __init__(
        self,
        suggester: _Suggester,
        *,
        memory: SuggestionMemory | None = None,
        max_started: int = MAX_STARTED,
    ) -> None:
        self._suggester = suggester
        self._memory = memory
        self._max_started = max_started
        self._pending: set[str] = set()
        # Vault to its item keys, each to the run id it started ("" for a refused start), oldest
        # first.
        self._started: dict[str, dict[str, str]] = {}
        loaded = None if memory is None else memory.load("started")
        self.persistent = loaded is not None
        for workspace_id, value in (loaded or {}).items():
            started = _decode(value)
            if started:
                self._started[workspace_id] = dict(list(started.items())[-max_started:])
        # Strong references, so a start whose caller was cancelled still runs to its end.
        self._tasks: set[asyncio.Future[SuggestionRun]] = set()
        self._saves: set[asyncio.Task[None]] = set()
        self._closed = False

    def read(self, workspace_id: str) -> InboxLook:
        found = self._suggester.run_for(workspace_id)
        run = (
            None
            if found is None
            else InboxRun(found.run_id, found.state, found.reason, found.started_at)
        )
        answered = {
            ref: item.modified for ref, item in self._suggester.suggestions(workspace_id).items()
        }
        return InboxLook(run, answered, dict(self._started.get(workspace_id, {})))

    async def close(self) -> None:
        """Stop every start in flight; the shield keeps a cancelled caller from doing it."""
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*list(self._saves), return_exceptions=True)

    async def start(self, workspace_id: str, item_key: str) -> str:
        if self._closed:
            raise DecisionLoopError("unavailable")
        if item_key in self._pending:
            raise DecisionLoopError("busy")
        if item_key in self._started.get(workspace_id, {}):
            raise DecisionLoopError("not_applicable")
        self._pending.add(item_key)  # no await before: a second start for the key sees it
        # The task copies the context as it is created, so the bound reaches the suggester's
        # listing whatever the caller does next.
        token = bound_listing.set(partial(_lists_state, workspace_id, item_key))
        try:
            task = asyncio.ensure_future(self._suggester.start(workspace_id))
        finally:
            bound_listing.reset(token)
        self._tasks.add(task)
        task.add_done_callback(partial(self._settle, workspace_id, item_key))
        try:
            run = await asyncio.shield(task)
        except CaptureError as refusal:
            busy = refusal.code == "suggestion_running"
            raise DecisionLoopError("busy" if busy else "not_applicable") from None
        except StateError:
            raise DecisionLoopError("unavailable") from None
        return run.run_id

    def _settle(
        self, workspace_id: str, item_key: str, task: asyncio.Future[SuggestionRun]
    ) -> None:
        """Record the start's outcome, whether or not its caller still waits."""
        self._pending.discard(item_key)
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()  # retrieved here, so a cancelled caller leaves none unseen
        if isinstance(exc, CaptureError) and exc.code == "suggestion_running":
            return  # another run goes: asked again once it ends
        started = self._started.setdefault(workspace_id, {})
        started[item_key] = "" if exc is not None else task.result().run_id
        while len(started) > self._max_started:
            del started[next(iter(started))]
        if self._memory is not None:
            # The value is read as the save writes, so the newest keys land last.
            value = partial(_encode, self._started, workspace_id)
            save = asyncio.ensure_future(self._memory.save(workspace_id, "started", value))
            self._saves.add(save)
            save.add_done_callback(self._saves.discard)


def _lists_state(workspace_id: str, item_key: str, listing: CaptureListing) -> bool:
    """Whether `listing` is the inbox state whose item is `item_key`."""
    listed = [(capture.ref, capture.modified) for capture in listing.captures]
    return state_key(workspace_id, listed) == item_key


def _encode(started: Mapping[str, Mapping[str, str]], workspace_id: str) -> object:
    return [[key, run_id] for key, run_id in started.get(workspace_id, {}).items()]


def _decode(value: object) -> dict[str, str] | None:
    """The saved keys of one vault, oldest first; None when not the shape `_encode` writes."""
    if not isinstance(value, list):
        return None
    started: dict[str, str] = {}
    for pair in value:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(part, str) for part in pair)
        ):
            return None
        started[pair[0]] = pair[1]
    return started
