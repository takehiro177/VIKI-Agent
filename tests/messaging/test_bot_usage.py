"""Bot usage capture on synthetic app state: chat attribution, the background-agent fixture's
calls recorded once, transcript path lookups, a removed Bot's saved conversations, budgets, write
batches, pruning and a busy store."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.messaging.test_bots import (
    _attempt,
    _bind_chat,
    _bot,
    _group_member,
    _id,
    _owned_chat,
    _routine,
)
from tests.messaging.test_bots import service as service
from tests.messaging.test_usage_reader import (
    STREAM_FIXTURE,
    TRANSCRIPT_FIXTURE,
    _stream_model_usage,
)
from viki_agent import catalog
from viki_agent.automation.routines import stamp
from viki_agent.catalog import Catalog, project_dir
from viki_agent.code import session_roots
from viki_agent.messaging import bot_usage
from viki_agent.messaging.bot_usage import Attribution, BotUsage, BotUsageError, Capture
from viki_agent.messaging.bot_usage_api import UsageViews
from viki_agent.messaging.bot_usage_report import Names
from viki_agent.messaging.bots import BotService
from viki_agent.storage import StateError

_NOW = datetime(2026, 10, 14, tzinfo=UTC)
# An hour before _NOW, past the settle window: a full read stores it as the session's read_ns.
_MTIME_NS = (int(_NOW.timestamp()) - 3600) * 1_000_000_000
_GAP = bot_usage.WRITE_GAP_S


def _usage(
    service: BotService,
    sleep: Callable[[float], None] = lambda _seconds: None,
    now: Callable[[], datetime] = lambda: _NOW,
) -> tuple[BotUsage, Path]:
    """A capture service over a catalog in the test's folder, and that catalog's transcripts."""
    root = service.store.path.parent.parent
    catalog = Catalog(root / "claude", root / "vault")
    usage = BotUsage(service.store, catalog, now=now, sleep=sleep)
    return usage, project_dir(root / "claude", root / "vault")


def _line(number: int, *, at: datetime = _NOW - timedelta(days=1), **message: Any) -> str:
    """An assistant line with usage, its message id, counts and time set by `number`; `message`
    replaces keys of its message."""
    usage = {
        "input_tokens": number + 1,
        "output_tokens": 2,
        "cache_read_input_tokens": 3,
        "cache_creation_input_tokens": 4,
    }
    body = {"id": f"msg_{number:06d}", "model": "claude-sonnet-5", "usage": usage, **message}
    return json.dumps(
        {
            "type": "assistant",
            "isSidechain": False,
            "timestamp": stamp(at + timedelta(seconds=number)),
            "message": body,
        }
    )


def _fixture() -> list[str]:
    return TRANSCRIPT_FIXTURE.read_text(encoding="utf-8").splitlines()


def _write(
    path: Path, lines: list[str], *, append: bool = False, mtime_ns: int = _MTIME_NS
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(line + "\n" for line in lines)
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def _calls(service: BotService) -> list[tuple[Any, ...]]:
    with service.store._connection() as connection:
        return connection.execute(
            "SELECT * FROM bot_usage_calls ORDER BY session_id,message_id"
        ).fetchall()


class _Roots:
    """A `catalog.RootLookup`, bound as server.py binds `SessionRoots`, that counts its reads.
    Its snapshot holds `listed`; `root_of` and `roots_for` also know `unlisted`, as they know
    the roots past the snapshot's `session_roots.MAX_ROOT_ROWS`. While `fail` is set, the
    snapshot raises, and so does `roots_for` while `fail_bulk` is set."""

    def __init__(self, listed: dict[str, Path], unlisted: dict[str, Path] | None = None) -> None:
        self.listed, self.unlisted = listed, unlisted or {}
        self.fail = self.fail_bulk = False
        self.snapshots = 0
        self.alone: list[str] = []
        self.bulk: list[list[str]] = []

    def root_of(self, session_id: str) -> Path | None:
        self.alone.append(session_id)
        return self.listed.get(session_id, self.unlisted.get(session_id))

    def roots_for(self, session_ids: list[str]) -> dict[str, Path]:
        self.bulk.append(sorted(session_ids))
        if self.fail_bulk:
            raise StateError("unavailable")
        known = {**self.unlisted, **self.listed}
        return {sid: known[sid] for sid in session_ids if sid in known}

    def transcript_roots(self) -> dict[str, Path]:
        self.snapshots += 1
        if self.fail:
            raise StateError("unavailable")
        return dict(self.listed)


def _bound(service: BotService, roots: _Roots) -> tuple[BotUsage, Path]:
    """`_usage`, its catalog bound to `roots`."""
    usage, projects = _usage(service)
    assert isinstance(usage.catalog, Catalog)
    usage.catalog.bind_roots(roots)
    return usage, projects


def _elsewhere(service: BotService, root: Path) -> Path:
    """The folder `_usage`'s catalog reads the transcripts of sessions recorded in `root` from."""
    return project_dir(service.store.path.parent.parent / "claude", root)


def _bind_chats(service: BotService, bot: dict[str, Any], count: int) -> list[str]:
    """`count` task threads of `bot` in one transaction: the binding `_bind_chat` writes for
    each, without its `sessions` row."""
    session_ids, at = [_id() for _ in range(count)], stamp(_NOW)
    with service.store._connection(write=True) as connection:
        connection.executemany(
            "INSERT INTO session_profile_bindings (session_id,command_id,request_hash,profile_id,"
            "profile_revision,workspace_id,workspace_revision,policy,policy_revision,"
            "settings_revision,runtime,capability_key,launch_spec_hash,native_started,created_at)"
            " VALUES (?,?,?,?,1,?,1,'ordinary',1,0,'claude',?,?,1,?)",
            [
                (sid, sid, "a" * 64, bot["profile_id"], bot["workspace_id"], "b" * 64, "c" * 64, at)
                for sid in session_ids
            ],
        )
    return session_ids


def _settle(service: BotService, bot: dict[str, Any], session_ids: list[str]) -> None:
    """Log `session_ids` as `bot`'s task threads read to their end at _MTIME_NS, one call each."""
    at = stamp(_NOW - timedelta(days=1))
    with service.store._connection(write=True) as connection:
        connection.executemany(
            "INSERT INTO bot_usage_sessions VALUES (?,?,?,?,'task',NULL,?)",
            [
                (sid, bot["workspace_id"], bot["bot_id"], bot["profile_id"], _MTIME_NS)
                for sid in session_ids
            ],
        )
        connection.executemany(
            "INSERT INTO bot_usage_calls VALUES (?,'m1',NULL,?,'claude-sonnet-5',0,1,2,3,4)",
            [(sid, at) for sid in session_ids],
        )


def test_sessions_attribute_exactly_the_bot_chats_of_one_vault(service: BotService) -> None:
    bot = _bot(service)
    workspace_id = bot["workspace_id"]
    other = _bot(service, workspace_id=workspace_id)
    elsewhere = _bot(service)
    _bind_chat(service, bot)
    thread = _owned_chat(service, bot)
    pair = _owned_chat(service, bot, memory=False)
    member = _owned_chat(service, bot)
    _group_member(service, bot, member)
    run = _owned_chat(service, bot)
    _group_member(service, bot, run)  # a routine run in a Bot thread is still a routine run
    attempt = _attempt(service, _routine(service, bot))
    _bind_chat(service, {**bot, "chat_session_id": attempt.session_id})
    foreign = _owned_chat(service, other)
    _group_member(service, other, other["chat_session_id"])  # bound to no launch yet
    _bind_chat(service, elsewhere)
    connection_id = _id()
    order = [bot["chat_session_id"], thread, pair, member, run, attempt.session_id, foreign]
    with service.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE session_profile_bindings SET launch_origin='routine' WHERE session_id=?",
            (run,),
        )
        connection.execute(
            "UPDATE sessions SET model=? WHERE session_id=?",
            (f"viki-llm/{connection_id}/synthetic-model", bot["chat_session_id"]),
        )
        for minutes, session_id in enumerate(order):
            connection.execute(
                "UPDATE session_profile_bindings SET created_at=? WHERE session_id=?",
                (stamp(_NOW + timedelta(minutes=minutes)), session_id),
            )
    usage, _projects = _usage(service)

    def seen(owner: dict[str, Any], session_id: str, kind: str, **changes: Any) -> Attribution:
        values = {"connection_id": None, "started": True, **changes}
        return Attribution(
            session_id, workspace_id, owner["bot_id"], owner["profile_id"], kind, **values
        )

    found = usage.sessions(workspace_id)
    assert {row.session_id for row in found} == service.chat_session_ids() - {
        elsewhere["chat_session_id"]
    }
    assert found == [
        seen(other, foreign, "task"),
        seen(bot, attempt.session_id, "routine"),
        seen(bot, run, "routine"),
        seen(bot, member, "bot_thread"),
        seen(bot, pair, "task"),
        seen(bot, thread, "task"),
        seen(bot, bot["chat_session_id"], "main", connection_id=connection_id),
        seen(other, other["chat_session_id"], "bot_thread", started=False),
    ]
    assert [row.session_id for row in usage.sessions(elsewhere["workspace_id"])] == [
        elsewhere["chat_session_id"]
    ]
    with service.store._connection(write=True) as connection:
        connection.execute(
            "UPDATE sessions SET model='viki-llm/not-a-connection/x' WHERE session_id=?", (pair,)
        )
    assert pair not in {row.session_id for row in usage.sessions(workspace_id)}


def test_capture_logs_the_fixture_sonnet_totals_of_the_sessions_with_a_transcript(
    service: BotService,
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    _owned_chat(service, bot)  # no transcript: not present, not pending
    chat = bot["chat_session_id"]
    sleeps: list[float] = []
    usage, projects = _usage(service, sleeps.append)
    _write(projects / f"{chat}.jsonl", _fixture())

    result = usage.capture(bot["workspace_id"])

    calls = _calls(service)
    assert calls
    assert result == Capture(frozenset({chat}), 0, len(calls))
    with service.store._connection() as connection:
        totals = connection.execute(
            "SELECT sum(input_tokens),sum(output_tokens),sum(cache_read_tokens),"
            "sum(cache_write_tokens) FROM bot_usage_calls WHERE model='claude-sonnet-5'"
        ).fetchone()
        sessions = connection.execute("SELECT * FROM bot_usage_sessions").fetchall()
    usage_totals = _stream_model_usage(STREAM_FIXTURE, "claude-sonnet-5")
    assert totals == (
        usage_totals["inputTokens"],
        usage_totals["outputTokens"],
        usage_totals["cacheReadInputTokens"],
        usage_totals["cacheCreationInputTokens"],
    )
    assert sessions == [
        (chat, bot["workspace_id"], bot["bot_id"], bot["profile_id"], "main", None, _MTIME_NS)
    ]
    assert sleeps == []  # one write transaction waits nothing


def test_unchanged_transcripts_are_not_opened_and_repeats_add_nothing(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    usage, projects = _usage(service)
    path = _write(projects / f"{chat}.jsonl", _fixture())
    assert usage.capture(workspace_id).added > 0
    logged = _calls(service)
    opened: list[Path] = []
    read_calls = bot_usage.read_calls

    def spy(path: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(path)
        return read_calls(path, *args, **kwargs)

    monkeypatch.setattr(bot_usage, "read_calls", spy)
    unchanged = Capture(frozenset({chat}), 0, 0)
    assert usage.capture(workspace_id) == unchanged
    assert _usage(service)[0].capture(workspace_id) == unchanged
    assert opened == []
    with service.store._connection(write=True) as connection:
        connection.execute("UPDATE bot_usage_sessions SET read_ns=NULL")
    # Read again from the start, every line merges into the row it already has.
    assert _usage(service)[0].capture(workspace_id) == unchanged
    assert opened == [path]
    assert _calls(service) == logged
    # Modified a second before now, inside the settle window: the next capture reads it again.
    now_ns = int(_NOW.timestamp()) * 1_000_000_000
    _write(path, [_line(1)], append=True, mtime_ns=now_ns - 1_000_000_000)
    assert usage.capture(workspace_id) == Capture(frozenset({chat}), 0, 1)
    assert opened == [path, path]
    with service.store._connection() as connection:
        assert connection.execute("SELECT read_ns FROM bot_usage_sessions").fetchall() == [
            (now_ns - bot_usage.SETTLE_NS,)
        ]
    assert _usage(service)[0].capture(workspace_id) == unchanged
    assert opened == [path, path, path]


def test_a_shrunk_transcript_is_read_again_from_its_start(service: BotService) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    usage, projects = _usage(service)
    path = _write(projects / f"{bot['chat_session_id']}.jsonl", [_line(n) for n in range(3)])
    assert usage.capture(bot["workspace_id"]).added == 3
    _write(path, [_line(9)], mtime_ns=_MTIME_NS + 1_000_000_000)
    assert usage.capture(bot["workspace_id"]).added == 1
    assert [row[1] for row in _calls(service)][-1] == "msg_000009"


def test_repeated_message_ids_merge_into_one_row(service: BotService) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    usage, projects = _usage(service)
    at, tick = _NOW - timedelta(days=1), 1_000_000_000

    def prompt(prompt_id: str) -> str:
        return json.dumps({"type": "user", "promptId": prompt_id})

    def counts(input_tokens: int, output_tokens: int) -> dict[str, int]:
        return {"input_tokens": input_tokens, "output_tokens": output_tokens}

    main = _write(
        projects / f"{chat}.jsonl",
        [_line(7, at=at + timedelta(seconds=9), model=None, usage=counts(1, 5))],
    )
    usage.capture(workspace_id)
    _write(
        main,
        [prompt("p-1"), _line(7, at=at, usage=counts(9, 2))],
        append=True,
        mtime_ns=_MTIME_NS + tick,
    )
    usage.capture(workspace_id)
    later = _line(7, at=at + timedelta(seconds=20), model="other-model", usage=counts(1, 1))
    _write(main, [prompt("p-2"), later], append=True, mtime_ns=_MTIME_NS + 2 * tick)
    usage.capture(workspace_id)
    _write(projects / chat / "subagents" / "agent-a1.jsonl", [later])
    os.utime(main, ns=(_MTIME_NS + 3 * tick, _MTIME_NS + 3 * tick))
    usage.capture(workspace_id)

    assert _calls(service) == [
        (
            chat,
            "msg_000007",
            "p-1",
            stamp(at + timedelta(seconds=7)),
            "claude-sonnet-5",
            1,
            9,
            5,
            0,
            0,
        )
    ]


def test_capture_over_two_bots_tolerates_a_lone_surrogate_model(service: BotService) -> None:
    """F6: a bad line beside good ones logs every call of both Bots, the bad one with model "",
    and never stops the other Bot's calls from being recorded."""
    first = _bot(service)
    _bind_chat(service, first)
    workspace_id = first["workspace_id"]
    second = _bot(service, workspace_id=workspace_id)  # bound after first: the newest Bot
    _bind_chat(service, second)
    usage, projects = _usage(service)
    _write(projects / f"{first['chat_session_id']}.jsonl", [_line(0), _line(1)])
    _write(
        projects / f"{second['chat_session_id']}.jsonl",
        [_line(2), _line(3, model="\ud800")],
    )

    result = usage.capture(workspace_id)

    models = {row[1]: row[4] for row in _calls(service)}
    assert models == {
        "msg_000000": "claude-sonnet-5",
        "msg_000001": "claude-sonnet-5",
        "msg_000002": "claude-sonnet-5",
        "msg_000003": "",
    }
    assert result.added == 4
    assert usage.capture(workspace_id).added == 0


def test_subagent_transcripts_log_agent_rows_within_the_file_budget(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bot_usage, "MAX_CAPTURE_FILES", 1)
    bot = _bot(service)
    _bind_chat(service, bot)
    chat = bot["chat_session_id"]
    usage, projects = _usage(service)
    _write(projects / f"{chat}.jsonl", [_line(0)])
    folder = projects / chat / "subagents"
    _write(folder / "agent-a1.jsonl", [_line(1), _line(2)])
    _write(folder / "agent-a1.meta.json", ["{}"])
    _write(folder / "notes.jsonl", [_line(3)])

    assert usage.capture(bot["workspace_id"]) == Capture(frozenset({chat}), 1, 1)
    assert usage.capture(bot["workspace_id"]) == Capture(frozenset({chat}), 0, 2)

    assert [(row[1], row[5]) for row in _calls(service)] == [
        ("msg_000000", 0),
        ("msg_000001", 1),
        ("msg_000002", 1),
    ]


@pytest.mark.parametrize("entry", ["folder", "symlink outside"])
def test_a_subagent_entry_that_is_a_folder_or_resolves_outside_is_never_read(
    service: BotService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """F8: `_agents` filters out a `subagents` entry that is not a regular file inside that
    folder, so a folder or a symlink pointing outside it is never opened, while a real sibling
    transcript still is."""
    bot = _bot(service)
    _bind_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    usage, projects = _usage(service)
    main = _write(projects / f"{chat}.jsonl", [_line(0)])
    folder = main.parent.resolve() / chat / "subagents"
    z = _write(folder / "agent-z.jsonl", [_line(1)])
    if entry == "folder":
        (folder / "agent-x.jsonl").mkdir()
    else:
        outside = _write(tmp_path / "outside.jsonl", [_line(2)])
        try:
            (folder / "agent-y.jsonl").symlink_to(outside)
        except OSError:
            pytest.skip("this platform refuses to create a symlink")
    opened: list[Path] = []
    read_calls = bot_usage.read_calls

    def spy(path: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(path)
        return read_calls(path, *args, **kwargs)

    monkeypatch.setattr(bot_usage, "read_calls", spy)

    # The candidate list itself excludes the entry, by name: a Windows folder's stat size is 0,
    # so the spy alone could not tell a skipped folder from an empty read.
    assert [path.name for path in bot_usage._agents(main, chat)] == ["agent-z.jsonl"]
    assert usage.capture(workspace_id) == Capture(frozenset({chat}), 0, 2)
    assert opened == [main, z]
    assert {row[1] for row in _calls(service)} == {"msg_000000", "msg_000001"}


def test_a_small_budget_leaves_sessions_pending_and_later_captures_reach_the_same_rows(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    task = _owned_chat(service, bot)
    workspace_id = bot["workspace_id"]
    usage, projects = _usage(service)
    _write(projects / f"{bot['chat_session_id']}.jsonl", _fixture())
    # A first line longer than the budget spends it before the subagent transcript.
    padding = json.dumps({"type": "user", "text": "x" * 2048})
    _write(projects / f"{task}.jsonl", [padding, *(_line(number) for number in range(6))])
    _write(
        projects / task / "subagents" / "agent-a1.jsonl",
        [_line(number) for number in range(10, 14)],
    )
    budgets: list[int] = []
    read_calls = bot_usage.read_calls

    def spy(path: Path, offset: int, budget: int, **kwargs: Any) -> Any:
        budgets.append(budget)
        return read_calls(path, offset, budget, **kwargs)

    monkeypatch.setattr(bot_usage, "read_calls", spy)

    results = [usage.capture(workspace_id, budget=1024)]
    while results[-1].pending and len(results) < 100:
        results.append(usage.capture(workspace_id, budget=1024))

    assert results[0].pending == 2
    assert results[-1].pending == 0
    assert min(budgets) > 0 and max(budgets) == 1024
    small = _calls(service)
    assert sum(result.added for result in results) == len(small)
    with service.store._connection(write=True) as connection:
        connection.execute("DELETE FROM bot_usage_calls")
        connection.execute("DELETE FROM bot_usage_sessions")
    assert _usage(service)[0].capture(workspace_id).pending == 0
    assert _calls(service) == small


def test_250_calls_commit_in_transactions_of_at_most_100_rows(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    sleeps: list[float] = []
    usage, projects = _usage(service, sleeps.append)
    _write(projects / f"{bot['chat_session_id']}.jsonl", [_line(number) for number in range(250)])
    statements: list[str] = []
    connect = sqlite3.connect

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)
    assert usage.capture(bot["workspace_id"]).added == 250

    sizes: list[int] = []
    rows = 0
    for sql in statements:
        if sql == "BEGIN IMMEDIATE":
            rows = 0
        elif sql.startswith("INSERT INTO bot_usage_calls"):
            rows += 1
        elif sql == "COMMIT":
            sizes.append(rows)
    assert sizes == [100, 100, 50]
    assert sleeps == [_GAP, _GAP]


def test_a_held_write_lock_stops_the_capture_and_keeps_committed_rows(
    service: BotService,
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    chat = bot["chat_session_id"]
    held: list[sqlite3.Connection] = []

    def hold(_seconds: float) -> None:
        """Take the store's write lock in the first gap between two write transactions."""
        if not held:
            held.append(sqlite3.connect(service.store.path, isolation_level=None))
            held[0].execute("BEGIN IMMEDIATE")

    usage, projects = _usage(service, hold)
    _write(projects / f"{chat}.jsonl", [_line(number) for number in range(250)])
    try:
        assert usage.capture(bot["workspace_id"]) == Capture(frozenset({chat}), 1, 100)
        assert len(_calls(service)) == 100
    finally:
        held[0].execute("ROLLBACK")
        held[0].close()
    assert usage.capture(bot["workspace_id"]) == Capture(frozenset({chat}), 0, 150)
    assert len(_calls(service)) == 250


def test_stop_during_the_first_write_gap_ends_the_capture_after_one_transaction(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F9: stop() checked before each write transaction ends the capture with exactly what
    already committed; its cursors stay at that point, so a later capture reaches the full
    totals, and pruning never runs after stop."""
    bot = _bot(service)
    _bind_chat(service, bot)
    chat = bot["chat_session_id"]
    usage_holder: list[BotUsage] = []
    sleeps: list[float] = []

    def stop_during_gap(seconds: float) -> None:
        sleeps.append(seconds)
        usage_holder[0].stop()

    usage, projects = _usage(service, stop_during_gap)
    usage_holder.append(usage)
    _write(projects / f"{chat}.jsonl", [_line(number) for number in range(250)])
    prune_calls: list[int] = []
    monkeypatch.setattr(usage, "_prune", lambda: prune_calls.append(1))
    statements: list[str] = []
    connect = sqlite3.connect

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)

    assert usage.capture(bot["workspace_id"]) == Capture(frozenset({chat}), 1, 100)

    assert sleeps == [_GAP]  # stopped in the first gap; no second gap was ever awaited
    assert prune_calls == []
    sizes: list[int] = []
    rows = 0
    for sql in statements:
        if sql == "BEGIN IMMEDIATE":
            rows = 0
        elif sql.startswith("INSERT INTO bot_usage_calls"):
            rows += 1
        elif sql == "COMMIT":
            sizes.append(rows)
    assert sizes == [100]
    assert len(_calls(service)) == 100

    # A fresh BotUsage has no stored cursors for the uncommitted rows: it rereads from the
    # start and the idempotent upsert still reaches the full 250 without duplicates.
    assert _usage(service)[0].capture(bot["workspace_id"]) == Capture(frozenset({chat}), 0, 150)
    assert len(_calls(service)) == 250


def test_capture_after_stop_reads_and_writes_nothing(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    usage, projects = _usage(service)
    _write(projects / f"{chat}.jsonl", [_line(0)])
    opened: list[Path] = []
    read_calls = bot_usage.read_calls

    def spy(path: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(path)
        return read_calls(path, *args, **kwargs)

    monkeypatch.setattr(bot_usage, "read_calls", spy)
    usage.stop()

    assert usage.capture(workspace_id) == Capture(frozenset(), 0, 0)
    assert opened == []
    with service.store._connection() as connection:
        assert connection.execute(
            "SELECT (SELECT count(*) FROM bot_usage_calls),"
            "(SELECT count(*) FROM bot_usage_sessions)"
        ).fetchone() == (0, 0)


def test_stop_during_the_reads_ends_the_capture_with_nothing_more_read_or_written(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F25: stop() is checked before each session's read, so a stop during one ends the capture
    there: no other transcript is opened and nothing is written."""
    bot = _bot(service)
    _bind_chat(service, bot)
    task = _owned_chat(service, bot)
    usage, projects = _usage(service)
    for session_id in (bot["chat_session_id"], task):
        _write(projects / f"{session_id}.jsonl", [_line(0)])
    opened: list[Path] = []
    read_calls = bot_usage.read_calls

    def stop_while_reading(path: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(path)
        usage.stop()
        return read_calls(path, *args, **kwargs)

    monkeypatch.setattr(bot_usage, "read_calls", stop_while_reading)

    assert usage.capture(bot["workspace_id"]) == Capture(frozenset(), 0, 0)
    assert len(opened) == 1
    with service.store._connection() as connection:
        assert connection.execute(
            "SELECT (SELECT count(*) FROM bot_usage_calls),"
            "(SELECT count(*) FROM bot_usage_sessions)"
        ).fetchone() == (0, 0)


def test_a_first_capture_reads_the_roots_once_and_looks_no_session_up_alone(
    service: BotService,
) -> None:
    """F25, F33: with the production root lookup bound, a first capture takes every transcript
    path from one snapshot of the recorded roots: 1,001 settled sessions and 1,000 with no
    transcript, as Claude Code's cleanup leaves them. The snapshot is under MAX_ROOT_ROWS, so
    complete: a session it lacks is the vault's, and none is looked up alone, then or later."""
    bot = _bot(service)
    workspace_id = bot["workspace_id"]
    chats = [bot["chat_session_id"], *_bind_chats(service, bot, 999)]
    _bind_chats(service, bot, 1000)  # no transcripts
    [listed] = _bind_chats(service, bot, 1)
    code = service.store.path.parent.parent / "code"
    roots = _Roots({listed: code})
    usage, projects = _bound(service, roots)
    for number, session_id in enumerate(chats):
        _write(projects / f"{session_id}.jsonl", [_line(number)])
    _write(_elsewhere(service, code) / f"{listed}.jsonl", [_line(0)])
    _settle(service, bot, [*chats, listed])

    assert usage.capture(workspace_id) == Capture(frozenset([*chats, listed]), 0, 0)
    assert (roots.snapshots, roots.alone, roots.bulk) == (1, [], [])
    assert usage.capture(workspace_id).present == frozenset([*chats, listed])
    assert (roots.snapshots, roots.alone, roots.bulk) == (2, [], [])  # again, for the 1,000


def test_a_found_path_is_remembered_and_a_missing_one_looked_up_at_each_capture(
    service: BotService,
) -> None:
    """F32: a session whose transcript a capture did not find is looked up again by the next,
    so a root recorded in between is picked up and its calls read. A path whose file was found
    is not looked up again until that file is gone."""
    bot = _bot(service)
    _bind_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    task = _owned_chat(service, bot)
    code = service.store.path.parent.parent / "code"
    roots = _Roots({})
    usage, projects = _bound(service, roots)
    main = _write(projects / f"{chat}.jsonl", [_line(0)])

    assert usage.capture(workspace_id) == Capture(frozenset({chat}), 0, 1)
    roots.listed[task] = code  # as when the task runs in a Bot code copy
    _write(_elsewhere(service, code) / f"{task}.jsonl", [_line(1)])
    assert usage.capture(workspace_id) == Capture(frozenset({chat, task}), 0, 1)
    assert roots.snapshots == 2
    assert usage.capture(workspace_id).added == 0
    assert roots.snapshots == 2  # both files found: nothing to look up
    main.unlink()
    assert usage.capture(workspace_id).present == {task}
    assert roots.snapshots == 2
    assert usage.capture(workspace_id).present == {task}
    assert (roots.snapshots, roots.alone, roots.bulk) == (3, [], [])


def test_a_snapshot_at_the_cap_looks_up_the_sessions_it_lacks_in_one_bulk_lookup(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F33: a snapshot of exactly MAX_ROOT_ROWS roots may have stopped short, so the sessions it
    lacks whose vault file is absent are looked up together, in one bulk lookup; one with a
    vault file is not, and none is looked up alone."""
    monkeypatch.setattr(catalog, "MAX_ROOT_ROWS", 2)
    bot = _bot(service)
    _bind_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    listed = _owned_chat(service, bot)
    lacked = [_owned_chat(service, bot) for _ in range(3)]
    gone = _owned_chat(service, bot)  # Claude Code deleted its transcript
    code = service.store.path.parent.parent / "code"
    roots = _Roots({listed: code, _id(): code}, dict.fromkeys(lacked, code))
    usage, projects = _bound(service, roots)
    _write(projects / f"{chat}.jsonl", [_line(0)])
    for number, session_id in enumerate((listed, *lacked), 1):
        _write(_elsewhere(service, code) / f"{session_id}.jsonl", [_line(number)])

    assert usage.capture(workspace_id).present == {chat, listed, *lacked}
    assert (roots.snapshots, roots.alone, roots.bulk) == (1, [], [sorted([*lacked, gone])])
    assert usage.capture(workspace_id).added == 0
    assert (roots.snapshots, roots.alone, roots.bulk[1:]) == (2, [], [[gone]])


def test_a_failed_bulk_lookup_reads_those_sessions_from_the_vault(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed bulk lookup leaves the sessions it named on their vault path, as a failed
    `root_of` does, and fails the capture of no other session."""
    monkeypatch.setattr(catalog, "MAX_ROOT_ROWS", 1)
    bot = _bot(service)
    _bind_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    lacked = _owned_chat(service, bot)
    code = service.store.path.parent.parent / "code"
    roots = _Roots({_id(): code}, {lacked: code})
    roots.fail_bulk = True
    usage, projects = _bound(service, roots)
    _write(projects / f"{chat}.jsonl", [_line(0)])
    _write(_elsewhere(service, code) / f"{lacked}.jsonl", [_line(1)])

    assert usage.capture(workspace_id) == Capture(frozenset({chat}), 0, 1)
    assert (roots.alone, roots.bulk) == ([], [[lacked]])
    roots.fail_bulk = False
    assert usage.capture(workspace_id) == Capture(frozenset({chat, lacked}), 0, 1)


def test_the_catalogs_root_cap_mirrors_session_roots() -> None:
    assert catalog.MAX_ROOT_ROWS == session_roots.MAX_ROOT_ROWS


def test_a_failed_roots_snapshot_looks_every_session_up_in_one_bulk_lookup(
    service: BotService,
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    task = _owned_chat(service, bot)
    code = service.store.path.parent.parent / "code"
    roots = _Roots({}, {task: code})
    roots.fail = True
    usage, projects = _bound(service, roots)
    _write(projects / f"{chat}.jsonl", [_line(0)])
    _write(_elsewhere(service, code) / f"{task}.jsonl", [_line(1)])

    assert usage.capture(workspace_id).present == {chat, task}
    assert (roots.snapshots, roots.alone, roots.bulk) == (1, [], [sorted([chat, task])])


def test_a_removed_bots_saved_conversation_keeps_its_transcript_in_summary_and_trace(
    service: BotService,
) -> None:
    """F26: Remove Bot keeps the Bot's usage rows. While its conversation is still saved, the
    summary does not count it as log-only and neither the summary nor the trace calls its
    transcript removed; once the file is gone, both do."""
    bot = _bot(service)
    _bind_chat(service, bot)
    workspace_id, session_id = bot["workspace_id"], bot["chat_session_id"]
    usage, projects = _usage(service)
    transcript = _write(projects / f"{session_id}.jsonl", [_line(1), _line(2)])
    views = UsageViews(
        usage,
        service.store,
        active=lambda: workspace_id,
        names=lambda: Names(profiles={}, bots=frozenset(), connections={}),
        now=lambda: _NOW,
    )
    assert views.summary(30)["coverage"]["log_only_sessions"] == 0
    service.remove(bot["bot_id"], service.get(bot["bot_id"])["state_revision"])
    assert usage.sessions(workspace_id) == []

    summary = views.summary(30)
    assert (summary["coverage"]["log_only_sessions"], summary["largest"][0]["transcript"]) == (
        0,
        True,
    )
    assert views.trace(session_id)["transcript"] is True
    assert usage.capture(workspace_id) == Capture(frozenset({session_id}), 0, 0)
    assert usage.capture(workspace_id, [_id()]) == Capture(frozenset(), 0, 0)

    transcript.unlink()
    summary = views.summary(30)
    assert (summary["coverage"]["log_only_sessions"], summary["largest"][0]["transcript"]) == (
        1,
        False,
    )
    assert views.trace(session_id)["transcript"] is False


def test_patched_caps_prune_the_oldest_rows_old_rows_and_empty_sessions(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bot_usage, "MAX_CALLS", 6)
    monkeypatch.setattr(bot_usage, "PRUNE_CHUNK", 2)
    bot = _bot(service)
    _bind_chat(service, bot)
    task = _owned_chat(service, bot)
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    clock = [_NOW]
    sleeps: list[float] = []
    usage, projects = _usage(service, sleeps.append, lambda: clock[0])
    main = _write(projects / f"{chat}.jsonl", [_line(number) for number in range(10)])

    assert usage.capture(workspace_id).added == 10
    assert [row[1] for row in _calls(service)] == [f"msg_{n:06d}" for n in range(4, 10)]
    assert sleeps == [_GAP, _GAP]  # the calls, then two chunks of the four oldest

    monkeypatch.setattr(bot_usage, "MAX_CALLS", 100)
    _write(projects / f"{task}.jsonl", [_line(20, at=_NOW - timedelta(days=100))])
    assert usage.capture(workspace_id).added == 1
    assert task in {row[0] for row in _calls(service)}  # the age pruning is not due yet

    # F27: the age pruning runs once due though a capture adds nothing, and not before.
    clock[0] = _NOW + timedelta(seconds=bot_usage.AGE_PRUNE_EVERY_S - 1)
    assert usage.capture(workspace_id).added == 0
    assert task in {row[0] for row in _calls(service)}
    clock[0] = _NOW + timedelta(seconds=bot_usage.AGE_PRUNE_EVERY_S)
    assert usage.capture(workspace_id).added == 0
    assert {row[0] for row in _calls(service)} == {chat}
    with service.store._connection() as connection:
        assert connection.execute("SELECT session_id FROM bot_usage_sessions").fetchall() == [
            (chat,)
        ]
    # A call 90 days old is within MAX_AGE_DAYS + 1 days: the next due pruning keeps it.
    kept = _line(30, at=_NOW - timedelta(days=90))
    _write(main, [kept], append=True, mtime_ns=_MTIME_NS + 1_000_000_000)
    clock[0] = _NOW + timedelta(seconds=2 * bot_usage.AGE_PRUNE_EVERY_S)
    assert usage.capture(workspace_id).added == 1
    calls = _calls(service)
    assert {row[0] for row in calls} == {chat}
    assert [row[1] for row in calls][-1] == "msg_000030"


def test_a_capture_past_the_lock_timeout_names_present_sessions_and_counts_all_pending(
    service: BotService, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot(service)
    _bind_chat(service, bot)
    _owned_chat(service, bot)  # no transcript
    chat, workspace_id = bot["chat_session_id"], bot["workspace_id"]
    monkeypatch.setattr(bot_usage, "LOCK_TIMEOUT_S", 0.01)
    roots = _Roots({})
    usage, projects = _bound(service, roots)
    _write(projects / f"{chat}.jsonl", [_line(0)])
    parked, release = threading.Event(), threading.Event()
    opened: list[Path] = []
    read_calls = bot_usage.read_calls

    def park(path: Path, *args: Any, **kwargs: Any) -> Any:
        """Hold the first capture, and with it the capture lock, inside its transcript read."""
        opened.append(path)
        parked.set()
        assert release.wait(10)
        return read_calls(path, *args, **kwargs)

    monkeypatch.setattr(bot_usage, "read_calls", park)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(usage.capture, workspace_id)
        assert parked.wait(10)
        try:
            assert usage.capture(workspace_id) == Capture(frozenset({chat}), 2, 0)
            assert opened == [projects / f"{chat}.jsonl"]  # the waiting capture read nothing
            # Each capture took its paths from one snapshot of the roots.
            assert (roots.snapshots, roots.alone) == (2, [])
            with service.store._connection() as connection:
                assert connection.execute(
                    "SELECT (SELECT count(*) FROM bot_usage_calls),"
                    "(SELECT count(*) FROM bot_usage_sessions)"
                ).fetchone() == (0, 0)
        finally:
            release.set()
        assert first.result(10) == Capture(frozenset({chat}), 0, 1)


@pytest.mark.parametrize(
    ("code", "expected"),
    [("corrupt", "corrupt"), ("busy", "unavailable"), ("incompatible", "unavailable")],
)
def test_store_faults_raise_bot_usage_errors(
    service: BotService, monkeypatch: pytest.MonkeyPatch, code: str, expected: str
) -> None:
    usage, _projects = _usage(service)

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise StateError(code)

    monkeypatch.setattr(service.store, "_connection", fail)
    with pytest.raises(BotUsageError) as failure:
        usage.capture(_id())
    assert failure.value.code == expected
