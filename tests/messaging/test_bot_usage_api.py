"""Bot usage routes (auth, origin, headers, days/id validation, capacity, error mapping,
answer size and no-store) against fake views, and the turn-end capture scheduler."""

from __future__ import annotations

import asyncio
import itertools
import queue
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from viki_agent.automation.routines import stamp
from viki_agent.messaging import bot_usage_api
from viki_agent.messaging.bot_usage import Attribution, BotUsageError, Capture
from viki_agent.messaging.bot_usage_api import UsageViews, bot_usage_routes, turn_end_capture
from viki_agent.messaging.bot_usage_report import Names
from viki_agent.state import AppState, Prefs
from viki_agent.storage import SessionStore

ORIGIN = "http://127.0.0.1:8765"
BEARER = {"Authorization": "Bearer usage-test-token"}
READ = {**BEARER, "Origin": ORIGIN}
BASE = "/api/v2/bot-usage"
HANG_GUARD_S = 5  # an event hang guard, not a timer bound on the operation under test
SID = "aaaaaaaa-1111-4111-8111-111111111111"
_EMPTY_NAMES = Names(profiles={}, bots=frozenset(), connections={})


def _id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012x}"


class FakeViews:
    """A stand-in for UsageViews: canned or scripted summary/trace/capture_ids, each able to
    block on `gate` (announcing entry through `entered`) for capacity/concurrency tests."""

    def __init__(self) -> None:
        self.summary_calls: list[int] = []
        self.trace_calls: list[str] = []
        self.capture_ids_calls: list[list[str]] = []
        self.summary_result: dict[str, Any] | BaseException = {"ok": "summary"}
        self.trace_result: dict[str, Any] | BaseException = {"ok": "trace"}
        self.capture_ids_error: BaseException | None = None
        self.gate: threading.Event | None = None
        self.entered: queue.Queue[Any] = queue.Queue()

    def _wait(self, token: Any) -> None:
        if self.gate is not None:
            self.entered.put(token)
            assert self.gate.wait(HANG_GUARD_S)

    def summary(self, days: int) -> dict[str, Any]:
        self.summary_calls.append(days)
        self._wait(("summary", days))
        if isinstance(self.summary_result, BaseException):
            raise self.summary_result
        return self.summary_result

    def trace(self, session_id: str) -> dict[str, Any]:
        self.trace_calls.append(session_id)
        self._wait(("trace", session_id))
        if isinstance(self.trace_result, BaseException):
            raise self.trace_result
        return self.trace_result

    def capture_ids(self, ids: Iterable[str]) -> None:
        batch = list(ids)
        self.capture_ids_calls.append(batch)
        self._wait(("capture_ids", batch))
        if self.capture_ids_error is not None:
            raise self.capture_ids_error


class FakeUsage:
    """A stand-in for BotUsage: canned `Capture` results, in order, the last one repeating."""

    def __init__(self, results: list[Capture]) -> None:
        self.results = results
        self.calls: list[tuple[str, frozenset[str] | None]] = []
        self.rosters: dict[str, list[Attribution]] = {}

    def capture(
        self, workspace_id: str, session_ids: Iterable[str] | None = None, *, budget: int = 0
    ) -> Capture:
        ids = None if session_ids is None else frozenset(session_ids)
        self.calls.append((workspace_id, ids))
        return self.results[min(len(self.calls), len(self.results)) - 1]

    def sessions(self, workspace_id: str) -> list[Attribution]:
        return self.rosters.get(workspace_id, [])


def _app(views: FakeViews, workers: set[asyncio.Task[Any]]) -> Starlette:
    return Starlette(
        routes=bot_usage_routes(
            views,
            authorized=lambda request: (
                request.headers.get("authorization") == BEARER["Authorization"]
            ),
            origins=frozenset({ORIGIN}),
            workers=workers,
        )
    )


def _store(tmp_path: Path) -> SessionStore:
    return AppState(tmp_path / "state.json", Prefs()).store


def _insert_session(store: SessionStore, **row: Any) -> None:
    values = {
        "session_id": _id(1),
        "workspace_id": _id(900),
        "bot_id": _id(101),
        "profile_id": _id(201),
        "kind": "main",
        "connection_id": None,
        **row,
    }
    with store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO bot_usage_sessions VALUES (?,?,?,?,?,?,NULL)", tuple(values.values())
        )


def _insert_call(store: SessionStore, **row: Any) -> None:
    values = {
        "session_id": _id(1),
        "message_id": "m1",
        "prompt_id": None,
        "at": stamp(datetime(2026, 11, 21, 12, tzinfo=UTC)),
        "model": "claude-sonnet-5",
        "agent": 0,
        "input_tokens": 1,
        "output_tokens": 2,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        **row,
    }
    with store._connection(write=True) as connection:
        connection.execute(
            "INSERT INTO bot_usage_calls VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(values.values())
        )


# ---------------------------------------------------------------------------
# Routes: refusal order, success, capacity, cancellation.
# ---------------------------------------------------------------------------


async def test_refusal_order_success_and_shielded_capacity() -> None:
    views = FakeViews()
    workers: set[asyncio.Task[Any]] = set()
    foreign = {**READ, "Origin": "https://foreign.test"}
    refused = [
        (f"{BASE}/summary/7", {}, 401, "unauthorized"),
        (f"{BASE}/summary/7", {"Authorization": "Bearer wrong"}, 401, "unauthorized"),
        (f"{BASE}/sessions/{SID}", {}, 401, "unauthorized"),
        (f"{BASE}/summary/7", foreign, 403, "forbidden_origin"),
        (f"{BASE}/sessions/{SID}", foreign, 403, "forbidden_origin"),
        (f"{BASE}/summary/7?x=1", READ, 400, "invalid_input"),  # query
        (f"{BASE}/summary/14", READ, 400, "invalid_input"),  # days
        (f"{BASE}/sessions/not-a-uuid", READ, 400, "invalid_input"),  # id
        (f"{BASE}/sessions/{SID.upper()}", READ, 400, "invalid_input"),  # id, wrong case
    ]
    transport = httpx.ASGITransport(app=_app(views, workers))
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        for url, headers, status, code in refused:
            response = await client.get(url, headers=headers)
            assert (response.status_code, response.json()["error"]["code"]) == (status, code), url
            assert response.headers["cache-control"] == "no-store"
        assert views.summary_calls == [] and views.trace_calls == []

        for headers in (READ, BEARER):  # the app's own Origin, or none at all
            response = await client.get(f"{BASE}/summary/30", headers=headers)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert response.json() == {"ok": "summary"}
        trace = await client.get(f"{BASE}/sessions/{SID}", headers=READ)
        assert trace.status_code == 200 and trace.json() == {"ok": "trace"}
        assert views.trace_calls == [SID]

        views.gate = threading.Event()
        calls = [
            asyncio.create_task(client.get(f"{BASE}/summary/30", headers=READ))
            for _ in range(bot_usage_api.MAX_REQUESTS)
        ]
        for _ in range(bot_usage_api.MAX_REQUESTS):
            await asyncio.to_thread(views.entered.get, timeout=HANG_GUARD_S)
        busy = await client.get(f"{BASE}/summary/30", headers=READ)
        assert (busy.status_code, busy.json()["error"]["code"]) == (429, "overloaded")
        assert busy.headers["cache-control"] == "no-store"
        # A cancelled caller keeps its permit: the shielded read runs on, tracked in workers.
        calls[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await calls[0]
        assert len(workers) == bot_usage_api.MAX_REQUESTS
        assert (await client.get(f"{BASE}/summary/30", headers=READ)).status_code == 429
        views.gate.set()
        async with asyncio.timeout(HANG_GUARD_S):
            answers = await asyncio.gather(*calls[1:])
            await asyncio.gather(*list(workers))
        assert [answer.status_code for answer in answers] == [200] * (
            bot_usage_api.MAX_REQUESTS - 1
        )
        await asyncio.sleep(0)  # the done callbacks run on the next loop pass
        assert not workers


def test_oversize_answer_is_503_unavailable_and_small() -> None:
    views = FakeViews()
    views.summary_result = {"blob": "x" * (2 * 1024 * 1024)}
    with TestClient(_app(views, set())) as client:
        response = client.get(f"{BASE}/summary/7", headers=READ)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    assert len(response.content) < 256
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (BotUsageError("not_found"), 404, "not_found"),
        (BotUsageError("unavailable"), 503, "unavailable"),
        (BotUsageError("corrupt"), 503, "corrupt"),
        (RuntimeError("synthetic-secret-value"), 503, "unavailable"),
    ],
)
def test_error_codes_map_and_only_unmapped_ones_are_logged(
    error: BaseException, status: int, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    views = FakeViews()
    views.summary_result = error
    with TestClient(_app(views, set())) as client:
        response = client.get(f"{BASE}/summary/7", headers=READ)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.headers["cache-control"] == "no-store"
    assert "synthetic-secret-value" not in response.text + caplog.text
    if isinstance(error, BotUsageError):
        assert not caplog.records
    else:
        assert "RuntimeError" in caplog.text


# ---------------------------------------------------------------------------
# UsageViews: the capture-repeat loop, unlogged, trace, capture_ids, names fallback.
# ---------------------------------------------------------------------------


def test_summary_stops_after_two_calls_when_a_capture_makes_no_progress(tmp_path: Path) -> None:
    """Every call returns the same pending with added 0: nothing moved, so the second call's
    result is compared against the first, finds no progress, and stops there rather than
    spinning for the whole deadline."""
    usage = FakeUsage([Capture(frozenset(), 5, 0)])  # FakeUsage repeats its last result
    views = UsageViews(
        usage,
        _store(tmp_path),
        active=lambda: _id(900),
        names=lambda: _EMPTY_NAMES,
        now=lambda: datetime(2026, 11, 21, 12, tzinfo=UTC),
    )
    result = views.summary(7)
    assert len(usage.calls) == 2
    assert result["coverage"]["pending"] == 5


def test_summary_with_falling_pending_stops_once_settled(tmp_path: Path) -> None:
    usage = FakeUsage(
        [Capture(frozenset(), 2, 5), Capture(frozenset(), 1, 3), Capture(frozenset(), 0, 1)]
    )
    views = UsageViews(
        usage,
        _store(tmp_path),
        active=lambda: _id(900),
        names=lambda: _EMPTY_NAMES,
        now=lambda: datetime(2026, 11, 21, 12, tzinfo=UTC),
        # The default real monotonic clock: three fake calls finish nowhere near the deadline.
    )
    result = views.summary(7)
    assert len(usage.calls) == 3  # kept going while pending fell, stopped once it hit 0
    assert result["coverage"]["pending"] == 0


def test_summary_with_falling_pending_still_stops_at_the_monotonic_deadline(tmp_path: Path) -> None:
    """Pending keeps falling and added is never 0, so the no-progress check never fires; only
    the deadline, timed on the injected monotonic clock, can stop it."""
    usage = FakeUsage([Capture(frozenset(), n, 1) for n in range(10, 0, -1)])
    clock = itertools.count()
    views = UsageViews(
        usage,
        _store(tmp_path),
        active=lambda: _id(900),
        names=lambda: _EMPTY_NAMES,
        now=lambda: datetime(2026, 11, 21, 12, tzinfo=UTC),
        monotonic=lambda: next(clock) * 2.0,  # 0, 2, 4 s: past the 3 s deadline on the second check
    )
    result = views.summary(7)
    assert len(usage.calls) == 2  # the first retry; the next deadline check (4 s) stopped it
    assert result["coverage"]["pending"] == 9


def test_summary_deadline_ignores_a_frozen_or_backwards_wall_clock(tmp_path: Path) -> None:
    """A wall clock that never advances (as a backwards step could look, moment to moment) must
    not stretch the loop: only the injected monotonic clock may end it."""
    usage = FakeUsage([Capture(frozenset(), 1, 1)])  # added never 0: no-progress check never fires
    clock = itertools.count()
    views = UsageViews(
        usage,
        _store(tmp_path),
        active=lambda: _id(900),
        names=lambda: _EMPTY_NAMES,
        now=lambda: datetime(2026, 11, 21, 12, tzinfo=UTC),  # frozen: a wall-clock step backwards
        monotonic=lambda: next(clock) * 2.0,  # 0, 2, 4 s: past the 3 s deadline on the second check
    )
    result = views.summary(7)
    assert len(usage.calls) == 2  # the monotonic deadline stopped it; a frozen wall clock could not
    assert result["coverage"]["pending"] == 1


def test_unlogged_is_started_attributions_with_no_transcript_and_no_session_row(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    workspace_id = _id(900)
    _insert_session(store, session_id=_id(2), workspace_id=workspace_id, kind="task")
    _insert_call(store, session_id=_id(2))
    usage = FakeUsage([Capture(frozenset({_id(1)}), 0, 0)])
    # has_transcript: present. logged_already: no transcript, but a session row. never_logged:
    # neither, so it counts as unlogged. not_started: never started, so it never counts.
    has_transcript, logged_already, never_logged, not_started = _id(1), _id(2), _id(3), _id(4)
    usage.rosters[workspace_id] = [
        Attribution(has_transcript, workspace_id, _id(101), _id(201), "main", None, True),
        Attribution(logged_already, workspace_id, _id(101), _id(201), "task", None, True),
        Attribution(never_logged, workspace_id, _id(101), _id(201), "task", None, True),
        Attribution(not_started, workspace_id, _id(101), _id(201), "bot_thread", None, False),
    ]
    views = UsageViews(
        usage,
        store,
        active=lambda: workspace_id,
        names=lambda: _EMPTY_NAMES,
        now=lambda: datetime(2026, 11, 21, 12, tzinfo=UTC),
    )
    result = views.summary(7)
    assert result["coverage"]["unlogged"] == 1


def test_trace_raises_not_found_then_forwards_capture_and_names(tmp_path: Path) -> None:
    store = _store(tmp_path)
    workspace_id = _id(900)
    usage = FakeUsage([Capture(frozenset({_id(1)}), 0, 0)])
    views = UsageViews(
        usage,
        store,
        active=lambda: workspace_id,
        names=lambda: _EMPTY_NAMES,
        now=lambda: datetime(2026, 11, 21, 12, tzinfo=UTC),
    )
    with pytest.raises(BotUsageError) as failure:
        views.trace(_id(1))
    assert failure.value.code == "not_found"
    assert usage.calls == [(workspace_id, frozenset({_id(1)}))]

    _insert_session(store, session_id=_id(1), workspace_id=workspace_id)
    _insert_call(store, session_id=_id(1))
    result = views.trace(_id(1))
    assert result["transcript"] is True  # _id(1) is in the fake capture's present set


def test_capture_ids_forwards_to_usage_capture_for_the_active_workspace(tmp_path: Path) -> None:
    workspace_id = _id(900)
    usage = FakeUsage([Capture(frozenset(), 0, 0)])
    views = UsageViews(
        usage,
        _store(tmp_path),
        active=lambda: workspace_id,
        names=lambda: _EMPTY_NAMES,
        now=lambda: datetime(2026, 11, 21, 12, tzinfo=UTC),
    )
    views.capture_ids([_id(1), _id(2)])
    assert usage.calls == [(workspace_id, frozenset({_id(1), _id(2)}))]


def test_failing_names_gives_empty_names_to_the_report(tmp_path: Path) -> None:
    store = _store(tmp_path)
    workspace_id = _id(900)
    _insert_session(store, workspace_id=workspace_id)
    _insert_call(store)
    usage = FakeUsage([Capture(frozenset({_id(1)}), 0, 0)])

    def boom() -> Names:
        raise RuntimeError("names unavailable")

    views = UsageViews(
        usage,
        store,
        active=lambda: workspace_id,
        names=boom,
        now=lambda: datetime(2026, 11, 21, 12, tzinfo=UTC),
    )
    result = views.summary(7)
    assert result["bots"][0]["name"] is None


# ---------------------------------------------------------------------------
# turn_end_capture: coalescing, the 64 cap, and swallowed exceptions.
# ---------------------------------------------------------------------------


async def test_three_schedules_for_one_id_run_one_capture() -> None:
    views = FakeViews()
    workers: set[asyncio.Task[Any]] = set()
    schedule = turn_end_capture(views, workers)
    schedule(_id(1))
    schedule(_id(1))
    schedule(_id(1))
    assert len(workers) == 1  # one drain task, however many times it was scheduled
    async with asyncio.timeout(HANG_GUARD_S):
        await asyncio.gather(*list(workers))
    assert views.capture_ids_calls == [[_id(1)]]


async def test_a_schedule_after_the_drain_finishes_starts_a_new_one() -> None:
    """Pins the `finally: task = None` re-arm (F16). Mutant D replaces that line with `pass`:
    `task` would keep pointing at the finished drain, so a later `schedule` would add its id to
    `pending` but find `task is None` false and never start a new drain — the id would then sit
    uncaptured until the next dashboard read, no matter how many later turns end."""
    views = FakeViews()
    workers: set[asyncio.Task[Any]] = set()
    schedule = turn_end_capture(views, workers)

    schedule(_id(1))
    assert len(workers) == 1
    async with asyncio.timeout(HANG_GUARD_S):
        await asyncio.gather(*list(workers))
    await asyncio.sleep(0)  # the done callback discards the finished task on the next loop pass
    assert not workers

    schedule(_id(2))
    assert len(workers) == 1  # re-armed: a later schedule starts a new drain task
    async with asyncio.timeout(HANG_GUARD_S):
        await asyncio.gather(*list(workers))

    assert views.capture_ids_calls == [[_id(1)], [_id(2)]]


async def test_pending_set_caps_at_64_and_a_dropped_id_is_never_captured() -> None:
    views = FakeViews()
    views.gate = threading.Event()
    workers: set[asyncio.Task[Any]] = set()
    schedule = turn_end_capture(views, workers)
    schedule("first")
    # The drain task has taken "first" into capture_ids and is blocked there: pending is empty.
    await asyncio.to_thread(views.entered.get, timeout=HANG_GUARD_S)
    for n in range(64):
        schedule(_id(n))
    schedule("dropped")  # the 65th distinct id while 64 are already pending
    schedule(_id(0))  # already pending: re-adding it is a harmless no-op
    views.gate.set()
    async with asyncio.timeout(HANG_GUARD_S):
        await asyncio.gather(*list(workers))
    batches = [set(batch) for batch in views.capture_ids_calls]
    assert batches == [{"first"}, {_id(n) for n in range(64)}]
    assert "dropped" not in {session_id for batch in batches for session_id in batch}


async def test_a_raising_capture_is_logged_by_type_only_and_never_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    views = FakeViews()
    views.capture_ids_error = RuntimeError("synthetic-secret-value")
    workers: set[asyncio.Task[Any]] = set()
    schedule = turn_end_capture(views, workers)
    schedule(_id(1))
    assert len(workers) == 1
    async with asyncio.timeout(HANG_GUARD_S):
        await asyncio.gather(*list(workers))  # drain() swallows the error: gather does not raise
    assert views.capture_ids_calls == [[_id(1)]]
    assert "RuntimeError" in caplog.text
    assert "synthetic-secret-value" not in caplog.text
    await asyncio.sleep(0)  # the done callback runs on the next loop pass
    assert not workers
