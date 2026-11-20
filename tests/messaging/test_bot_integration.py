"""Synthetic server graph: Bot chats, scheduled runs, policy and the shared DM writer."""

from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.automation.test_routines import _document_raw, _manual_schedule
from tests.automation.test_scheduler import FakeClock
from tests.messaging.test_mattermost_bot import BINDING, CONFIG, Http, take
from tests.profiles.test_profile_document import document
from tests.profiles.test_profile_launch_api import (
    AUTH,
    BASE,
    HANG_GUARD_S,
    TOKEN,
    WRITE,
    FakeDriver,
    Rig,
)
from tests.test_catalog import _assistant, _user
from viki_agent import server
from viki_agent.automation.routines import stamp
from viki_agent.catalog import Catalog, project_dir
from viki_agent.conversations.drafts import DraftService, draft_scope
from viki_agent.conversations.session_metadata import SessionMetadataService
from viki_agent.drivers.claude.control import Launch
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.events import (
    Event,
    PermissionRequested,
    Question,
    QuestionAsked,
    QuestionOption,
    RateLimit,
    RequestAnswered,
    SessionRow,
    SessionsResult,
    TextDone,
    TurnDone,
    Usage,
)
from viki_agent.messaging import mattermost_bot
from viki_agent.messaging.bot_memory import instruction_excludes
from viki_agent.messaging.bot_memory_rule import bot_system_prompt
from viki_agent.messaging.mattermost_bot import MattermostBot
from viki_agent.messaging.messaging_store import MessagingConfigStore
from viki_agent.profiles.profile_launch import ProfileLaunchService
from viki_agent.profiles.service import ProfileService
from viki_agent.session import SessionManager
from viki_agent.state import Prefs

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def ident() -> str:
    return str(uuid.uuid4())


async def nothing() -> None:
    pass


class Driver(FakeDriver):
    def __init__(self, stack: Integration, launch: Launch, emit: Callable[[Event], None]) -> None:
        super().__init__(launch, emit, stack.rig.state, stack.rig.catalog)
        self.stack = stack
        self.cards: list[str] = []
        stack.launches.append(launch)

    async def open(self) -> Any:
        await self.stack.before_open()
        return await super().open()

    async def send(self, text: str) -> None:
        await self.stack.before_send()
        await super().send(text)
        folder = project_dir(self.stack.rig.home, self.stack.rig.vault)
        folder.mkdir(parents=True, exist_ok=True)
        user = ident()
        rows = [_user(user, None, text, 1), _assistant(ident(), user, "Saved native reply", 2)]
        (folder / f"{self.launch.session_id}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        self.stack.texts.append(text)
        for event in self.stack.script:
            if isinstance(event, PermissionRequested | QuestionAsked):
                self.cards.append(event.tool_use_id)
            self.emit(event)
        self.stack.sent.set()

    def pending(self) -> list[str]:
        return list(self.cards)

    def respond(self, tool_use_id: str, answer: Any) -> bool:
        self.cards.remove(tool_use_id)
        self.stack.answers.append(answer)
        self.emit(RequestAnswered(tool_use_id=tool_use_id))
        return True


class Integration:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        windows: Any = None,
    ) -> None:
        # A short folder: the driver's transcript path repeats the vault's path, which must stay
        # under Windows MAX_PATH even beneath pytest's default basetemp.
        self.rig = Rig(Path(tempfile.mkdtemp(dir=tmp_path.parent, prefix="c")))
        self.clock = FakeClock(NOW)
        self.script: tuple[Event, ...] = (
            TextDone(block_id="final", text="Useful proactive result"),
            TurnDone(status="success", usage=Usage()),
        )
        self.before_open: Callable[[], Awaitable[object]] = nothing
        self.before_send: Callable[[], Awaitable[object]] = nothing
        self.texts: list[str] = []
        self.launches: list[Launch] = []
        self.answers: list[object] = []
        self.sent = asyncio.Event()
        self.http = Http()
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.bridges: list[MattermostBot] = []
        monkeypatch.setattr(
            self.rig.catalog, "exists", lambda sid: Catalog.exists(self.rig.catalog, sid)
        )
        monkeypatch.setattr(server, "MattermostHttp", lambda _: self.http)
        monkeypatch.setattr(mattermost_bot, "posted", self.posted)

        def bridge(*args: Any, **kwargs: Any) -> MattermostBot:
            made = MattermostBot(*args, **kwargs)
            self.bridges.append(made)
            return made

        monkeypatch.setattr(server, "MattermostBot", bridge)
        self.manager = SessionManager(
            config=DriverConfig(cli=("synthetic",), vault=self.rig.vault, env={}),
            state=self.rig.state,
            claude_home=self.rig.home,
            make_driver=self.driver,
        )
        MessagingConfigStore(self.rig.state.store).save("mattermost", CONFIG)
        self.app = server.create_app(
            config=self.rig.config,
            token=TOKEN,
            manager=self.manager,
            catalog=self.rig.catalog,
            static_dir=None,
            settings=self.rig.settings,
            workspaces=self.rig.workspaces,
            readiness=self.rig.readiness,
            routine_clock=self.clock,
            vault_poll_s=60,
            windows=windows,
        )
        self.bridge: MattermostBot
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=BASE)

    async def connect(self) -> None:
        response = await self.client.post("/api/v2/messaging/mattermost/enable", headers=WRITE)
        assert response.status_code == 200, response.text
        self.bridge = self.bridges[-1]

    def driver(self, launch: Launch, _: Prefs, emit: Callable[[Event], None]) -> Driver:
        driver = Driver(self, launch, emit)
        self.rig.drivers.append(driver)
        return driver

    async def posted(self, _config: Any, binding: Any, **kwargs: Any) -> AsyncIterator[Any]:
        assert binding == BINDING
        assert await kwargs["fresh_cutoff"]() == 1000
        while True:
            yield await self.incoming.get()

    async def create(
        self, *, profile_id: str | None = None, workspace_id: str | None = None
    ) -> dict[str, Any]:
        response = await self.client.post(
            "/api/v2/bots",
            headers=WRITE,
            json={
                "bot_id": ident(),
                "profile_id": profile_id or self.rig.profile_id,
                "workspace_id": workspace_id or self.rig.workspace_id,
                "expected_profile_revision": 1,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["bot"]

    async def action(self, bot: dict[str, Any], action: str, **fields: Any) -> httpx.Response:
        return await self.client.post(
            f"/api/v2/bots/{bot['bot_id']}/{action}",
            headers=WRITE,
            json={"expected_state_revision": bot["state_revision"], **fields},
        )

    async def enable(self, bot: dict[str, Any]) -> dict[str, Any]:
        response = await self.action(bot, "delivery", enabled=True)
        assert response.status_code == 200, response.text
        return response.json()["bot"]

    def routine(self, **changes: Any) -> str:
        raw = _document_raw(
            **{
                "profile_id": self.rig.profile_id,
                "workspace_id": self.rig.workspace_id,
                "schedule": _manual_schedule(),
                **changes,
            }
        )
        return self.app.state.routines.create(ident(), raw)[0]["routine_id"]

    async def run(self, routine_id: str) -> dict[str, Any]:
        runner = self.app.state.routine_tasks
        attempt, _ = await runner.run_now(routine_id, ident(), 1)
        await asyncio.wait_for(asyncio.gather(*list(runner._runs)), HANG_GUARD_S)
        return runner.store.runs(routine_id, 1, None)[0]["attempt"]

    def preference(self, name: str, value: str, *, scope: str | None = None) -> None:
        with self.rig.state.store._connection(write=True) as connection:
            connection.execute(
                "INSERT INTO decision_preferences VALUES (?,?,?,1,?)",
                (scope or self.rig.workspace_id, name, value, stamp(self.clock.wall())),
            )


@pytest.fixture
async def integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Integration]:
    stack = Integration(tmp_path, monkeypatch)
    async with stack.app.router.lifespan_context(stack.app), stack.client:
        await stack.connect()
        assert "ready" in (await take(stack.http.sent))[0].lower()
        yield stack


async def test_canonical_chat_is_reserved_and_open_never_starts_provider(
    integration: Integration,
) -> None:
    stack = integration
    bot = await stack.create()
    assert stack.manager.get(bot["chat_session_id"]) is None and not stack.rig.drivers
    detail = await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)
    assert detail.json()["chat_status"] == {"state": "unprepared", "blockers": []}
    first = await stack.action(bot, "chat")
    assert first.status_code == 200, first.text
    preview = first.json()["preview"]
    assert preview["session_id"] == bot["chat_session_id"] and preview["generation"]
    again = await stack.action(bot, "chat")
    assert again.json()["preview"]["generation"] == preview["generation"]
    assert not stack.rig.drivers


async def test_session_listing_marks_bot_history_without_opening_it(
    integration: Integration, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = integration
    bot = await stack.create()
    rows = [
        SessionRow(
            session_id=session_id,
            title=title,
            first_prompt=title,
            entrypoint="viki-agent",
            modified=stamp(NOW),
            cli_version="2.1.281",
            other=False,
        )
        for session_id, title in [
            (bot["chat_session_id"], "Bot history"),
            (ident(), "Regular chat"),
        ]
    ]
    monkeypatch.setattr(
        stack.rig.catalog,
        "list_sessions",
        lambda: SessionsResult(sessions=rows, state="partial", reason="read_failed", omitted=1),
    )
    response = await stack.client.get("/api/sessions", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert [row["bot"] for row in body["sessions"]] == [True, False]
    assert [row["title"] for row in body["sessions"]] == ["Bot history", "Regular chat"]
    assert (body["state"], body["reason"], body["omitted"]) == ("partial", "read_failed", 1)
    assert stack.manager.get(bot["chat_session_id"]) is None
    assert stack.rig.state.store.get(bot["chat_session_id"]) is None
    assert not stack.rig.drivers


async def test_run_delivers_final_output_and_keeps_canonical_chat(integration: Integration) -> None:
    stack = integration
    bot = await stack.enable(await stack.create())
    opened = await stack.action(bot, "chat")
    canonical = opened.json()["preview"]["session_id"]
    routine_id = stack.routine()
    run = await stack.run(routine_id)
    assert run["outcome"] == "succeeded"
    assert run["session_id"] != canonical and stack.manager.get(run["session_id"]) is None
    assert stack.manager.get(canonical) is not None
    message, root = await take(stack.http.sent)
    assert "Useful proactive result" in message and "Routine succeeded" in message and not root
    detail = (await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)).json()
    assert detail["bot"]["latest_delivery"]["state"] == "sent"
    assert detail["bot"]["latest_delivery"]["session_id"] == run["session_id"]


async def test_bot_chat_and_routine_run_get_instructions_and_memory_rule_as_system_prompt(
    integration: Integration,
) -> None:
    stack = integration
    bot = await stack.enable(await stack.create())
    instructions = document()["instructions"]
    preview = (await stack.action(bot, "chat")).json()["preview"]
    draft = DraftService(stack.rig.state, stack.rig.vault).get(preview["session_id"])
    assert (draft.text, draft.revision) == ("", 0)
    prompt = _document_raw()["prompt"]
    run = await stack.run(stack.routine())
    assert run["outcome"] == "succeeded"
    assert stack.texts == [prompt]
    [launch] = [item for item in stack.launches if item.session_id == run["session_id"]]
    assert launch.append_system_prompt == bot_system_prompt(instructions)
    assert launch.bot_memory_settings is not None
    # The run gets the Bot rules, so it skips the vault's instruction files too.
    settings = json.loads(Path(launch.bot_memory_settings).read_text(encoding="ascii"))
    assert settings["claudeMdExcludes"] == instruction_excludes(stack.rig.vault)


async def test_routine_run_without_a_bot_sends_no_memory_rule(integration: Integration) -> None:
    stack = integration
    prompt = _document_raw()["prompt"]
    assert (await stack.run(stack.routine()))["outcome"] == "succeeded"
    assert stack.texts == [f"{document()['instructions']}\n\n{prompt}"]
    assert [launch.append_system_prompt for launch in stack.launches] == [None]


async def test_schedule_uses_bot_hooks_without_opening_its_chat(integration: Integration) -> None:
    stack = integration
    workspace = stack.rig.workspaces.get(stack.rig.workspace_id)
    required = stack.rig.workspaces.inspect_policy(workspace.workspace_id).acknowledgements_required
    stack.rig.workspaces.set_policy(
        ident(), workspace.workspace_id, workspace.policy_revision, "ordinary", required
    )
    bot = await stack.create()
    routine_id = stack.routine(
        schedule={
            "kind": "daily",
            "times": ["00:01"],
            "days": [],
            "timezone": "UTC",
            "start_date": None,
            "end_date": None,
            "catch_up": "skip",
        }
    )
    assert (await stack.run(routine_id))["outcome"] == "succeeded"
    row = stack.app.state.routines.get(routine_id)
    stack.app.state.routines.enable(routine_id, ident(), row["state_revision"])
    bot = await stack.enable(bot)
    entered = asyncio.Event()

    async def opened() -> None:
        entered.set()

    stack.before_open = opened
    stack.app.state.scheduler.wake()
    stack.clock.advance(60)
    stack.clock.tick()
    await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
    runner = stack.app.state.routine_tasks
    await asyncio.wait_for(asyncio.gather(*list(runner._runs)), HANG_GUARD_S)
    [row] = runner.store.runs(routine_id, 1, None)
    assert row["kind"] == "schedule" and row["attempt"]["outcome"] == "succeeded"
    assert "Useful proactive result" in (await take(stack.http.sent))[0]
    assert stack.manager.get(bot["chat_session_id"]) is None


async def test_started_canonical_reopens_native_history_and_keeps_pinned_revision(
    integration: Integration,
) -> None:
    stack = integration
    bot = await stack.create()
    preview = (await stack.action(bot, "chat")).json()["preview"]
    sid = preview["session_id"]
    actor = stack.manager.get(sid)
    assert actor is not None and await actor.send("Synthetic owner message")
    assert stack.rig.catalog.exists(sid)
    before = stack.rig.catalog.history(sid)
    assert any(
        isinstance(event, TextDone) and event.text == "Saved native reply"
        for event in before.events
    )
    await stack.manager.close(sid)
    profiles = ProfileService(stack.rig.state.store)
    current = profiles.get(stack.rig.profile_id)
    changed = copy.deepcopy(current["document"])
    changed["identity"]["name"] = "Current identity"
    changed["instructions"] = "New instructions for future conversations."
    profiles.revise(stack.rig.profile_id, ident(), 1, changed)
    detail = (await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)).json()
    assert detail["bot"]["profile"]["revision"] == 2
    assert detail["bot"]["chat"]["profile_revision"] == 1
    assert detail["chat_status"]["state"] == "reopenable" and stack.manager.get(sid) is None
    reopened = await stack.action(bot, "chat")
    assert reopened.status_code == 200, reopened.text
    pinned = reopened.json()["preview"]
    assert (
        pinned["profile_revision"] == 1
        and pinned["identity"]["name"] == preview["identity"]["name"]
    )
    assert pinned["generation"] != preview["generation"]
    assert len(stack.rig.drivers) == 1  # inspection and opening attach; they do not spawn
    assert stack.rig.catalog.history(sid) == before


async def test_missing_native_source_is_explicit_and_never_recreated(
    integration: Integration,
) -> None:
    stack = integration
    bot = await stack.create()
    sid = (await stack.action(bot, "chat")).json()["preview"]["session_id"]
    actor = stack.manager.get(sid)
    assert actor is not None and await actor.send("Synthetic owner message")
    await stack.manager.close(sid)
    path = stack.rig.catalog.transcript_path(sid)
    assert path is not None
    path.unlink()
    detail = (await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)).json()
    assert detail["chat_status"] == {
        "state": "transcript_missing",
        "blockers": ["transcript_missing"],
    }
    response = await stack.action(bot, "chat")
    assert response.status_code == 404 and response.json()["error"]["code"] == "transcript_missing"
    assert stack.manager.get(sid) is None and len(stack.rig.drivers) == 1


async def test_only_legitimate_held_profile_actor_can_cover_first_write(
    integration: Integration,
) -> None:
    stack = integration
    bot = await stack.create()
    preview = (await stack.action(bot, "chat")).json()["preview"]
    sid = preview["session_id"]
    actor = stack.manager.get(sid)
    assert actor is not None
    entered, release = asyncio.Event(), asyncio.Event()

    async def first_write() -> None:
        entered.set()
        await release.wait()

    stack.before_send = first_write
    sending = asyncio.create_task(actor.send("Synthetic owner message"))
    try:
        await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        assert actor.live and not stack.rig.catalog.exists(sid)
        response = await stack.action(bot, "chat")
        assert (
            response.status_code == 200
            and response.json()["preview"]["generation"] == actor.generation
        )
    finally:
        release.set()
        await asyncio.wait_for(sending, HANG_GUARD_S)
    await stack.manager.close(sid)
    path = stack.rig.catalog.transcript_path(sid)
    assert path is not None
    path.unlink()
    stack.manager.create(sid)  # a foreign held actor cannot authorize same-ID replacement
    response = await stack.action(bot, "chat")
    assert response.status_code == 404 and len(stack.rig.drivers) == 1


async def test_roster_reads_no_native_sources_and_detail_reads_selected_only(
    integration: Integration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = integration
    bot = await stack.create()
    other_profile = ident()
    ProfileService(stack.rig.state.store).create(other_profile, ident(), document())
    other = await stack.create(profile_id=other_profile)
    calls: list[str] = []
    original = stack.rig.catalog.exists

    def exists(sid: str) -> bool:
        calls.append(sid)
        return original(sid)

    monkeypatch.setattr(stack.rig.catalog, "exists", exists)
    response = await stack.client.get("/api/v2/bots", headers=AUTH)
    assert response.status_code == 200 and len(response.json()["bots"]) == 2 and not calls
    await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)
    assert calls == [bot["chat_session_id"]] and other["chat_session_id"] not in calls


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "blocked"])
async def test_off_delivery_keeps_local_execution_outcome(
    integration: Integration,
    outcome: str,
) -> None:
    stack = integration
    bot = await stack.create()
    if outcome == "failed":
        stack.script = (TurnDone(status="error", usage=Usage()),)
    elif outcome == "blocked":
        stack.script = (RateLimit(status="rejected"), TurnDone(status="success", usage=Usage()))
    run = await stack.run(stack.routine())
    assert run["outcome"] == outcome and stack.http.sent.empty()
    latest = stack.app.state.bots.get(bot["bot_id"])["latest_delivery"]
    assert latest["outcome"] == outcome and latest["state"] == "off"


@pytest.mark.parametrize("outcome", ["failed", "blocked"])
async def test_failure_notice_is_generic_and_preserves_local_outcome(
    integration: Integration,
    outcome: str,
) -> None:
    stack = integration
    bot = await stack.enable(await stack.create())
    stack.script = (
        TextDone(block_id="sensitive", text="Synthetic private failure details"),
        *((RateLimit(status="rejected"),) if outcome == "blocked" else ()),
        TurnDone(status="success" if outcome == "blocked" else "error", usage=Usage()),
    )
    run = await stack.run(stack.routine())
    assert run["outcome"] == outcome
    message = (await take(stack.http.sent))[0]
    assert f"Routine {outcome}" in message and "Synthetic private failure details" not in message
    assert stack.app.state.bots.get(bot["bot_id"])["latest_delivery"]["state"] == "sent"


async def test_rules_use_exact_workspace_and_never_change_execution(
    integration: Integration,
) -> None:
    stack = integration
    bot = await stack.enable(await stack.create())
    stack.preference("mute:agent_runs", "1", scope=ident())
    run = await stack.run(stack.routine())
    assert (
        run["outcome"] == "succeeded"
        and "Useful proactive result" in (await take(stack.http.sent))[0]
    )
    stack.preference("mute:agent_runs", "1")
    stack.clock.advance(1)
    run = await stack.run(stack.routine())
    assert run["outcome"] == "succeeded" and stack.http.sent.empty()
    latest = stack.app.state.bots.get(bot["bot_id"])["latest_delivery"]
    assert latest["state"] == "held" and latest["attention"] == "silent"


async def test_moved_routine_keeps_frozen_delivery_owner_and_prepared_identity(
    integration: Integration,
) -> None:
    stack = integration
    first = await stack.enable(await stack.create())
    profiles = ProfileService(stack.rig.state.store)
    second_profile = ident()
    profiles.create(second_profile, ident(), document())
    second = await stack.enable(await stack.create(profile_id=second_profile))
    routine_id = stack.routine()
    entered, release = asyncio.Event(), asyncio.Event()

    async def opening() -> None:
        entered.set()
        await release.wait()

    stack.before_open = opening
    runner = stack.app.state.routine_tasks
    attempt, _ = await runner.run_now(routine_id, ident(), 1)
    runs = list(runner._runs)
    try:
        await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        changed = copy.deepcopy(profiles.get(stack.rig.profile_id)["document"])
        pinned_name = changed["identity"]["name"]
        changed["identity"]["name"] = "Identity saved after preparation"
        profiles.revise(stack.rig.profile_id, ident(), 1, changed)
        stack.app.state.routines.revise(
            routine_id,
            ident(),
            1,
            _document_raw(
                profile_id=second_profile,
                workspace_id=stack.rig.workspace_id,
                schedule=_manual_schedule(),
            ),
        )
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*runs), HANG_GUARD_S)
    message = (await take(stack.http.sent))[0]
    assert pinned_name in message and "Identity saved after preparation" not in message
    assert routine_id not in stack.app.state.bots.get(first["bot_id"])["routine_ids"]
    assert routine_id in stack.app.state.bots.get(second["bot_id"])["routine_ids"]
    assert stack.app.state.bots.delivery(attempt.attempt_id)["bot_id"] == first["bot_id"]
    assert stack.app.state.bots.get(second["bot_id"])["latest_delivery"] is None


@pytest.mark.parametrize("question", [False, True])
async def test_foreign_routine_permission_has_no_mattermost_nonce(
    integration: Integration,
    question: bool,
) -> None:
    stack = integration
    bot = await stack.enable(await stack.create())
    stack.script = (
        QuestionAsked(
            tool_use_id="synthetic-permission",
            questions=[
                Question(
                    header="Synthetic",
                    question="Synthetic private argument",
                    options=[QuestionOption(label="Choice", description="Synthetic")],
                    multi_select=False,
                )
            ],
        )
        if question
        else PermissionRequested(
            tool_use_id="synthetic-permission",
            kind="tool",
            tool="Edit",
            summary="Synthetic private argument",
        ),
    )
    runner = stack.app.state.routine_tasks
    routine_id = stack.routine()
    attempt, _ = await runner.run_now(routine_id, ident(), 1)
    runs = list(runner._runs)
    await asyncio.wait_for(stack.sent.wait(), HANG_GUARD_S)
    assert stack.rig.drivers[-1].pending() == ["synthetic-permission"]
    assert not stack.bridge._cards and stack.bridge._actor is None and stack.http.sent.empty()
    stack.clock.advance(30 * 60)
    stack.clock.tick()
    await asyncio.wait_for(asyncio.gather(*runs), HANG_GUARD_S)
    message = (await take(stack.http.sent))[0]
    assert "Routine expired" in message and "Synthetic private argument" not in message
    assert "!viki allow" not in message and not stack.bridge._cards
    assert runner.store.runs(routine_id, 1, None)[0]["attempt"]["outcome"] == "expired"
    assert stack.app.state.bots.delivery(attempt.attempt_id)["bot_id"] == bot["bot_id"]


async def test_archived_chat_and_new_chat_preserve_old_native_source(
    integration: Integration,
) -> None:
    stack = integration
    bot = await stack.create()
    sid = (await stack.action(bot, "chat")).json()["preview"]["session_id"]
    actor = stack.manager.get(sid)
    assert actor is not None and await actor.send("Synthetic owner message")
    history = stack.rig.catalog.history(sid)
    metadata = SessionMetadataService(stack.rig.state.store, draft_scope(stack.rig.vault))
    current = next(row for row in metadata.list() if row["session_id"] == sid)
    metadata.patch(sid, current["revision"], {"archived": True})
    detail = (await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)).json()
    assert detail["chat_status"]["state"] == "archived"
    refused = await stack.action(bot, "chat")
    assert refused.status_code == 422 and len(stack.rig.drivers) == 1
    response = await stack.action(bot, "new-chat")
    assert response.status_code == 200, response.text
    new = response.json()["bot"]
    assert new["chat_session_id"] != sid and new["chat"]["profile_revision"] is None
    assert stack.rig.catalog.history(sid) == history and stack.manager.get(sid) is actor


async def test_new_chat_refuses_busy_current_actor(integration: Integration) -> None:
    stack = integration
    bot = await stack.create()
    await stack.action(bot, "chat")
    actor = stack.manager.get(bot["chat_session_id"])
    assert actor is not None and await actor.send("Synthetic owner message")
    stack.rig.drivers[-1].busy = True
    response = await stack.action(bot, "new-chat")
    assert response.status_code == 409 and response.json()["error"]["code"] == "busy"
    assert stack.app.state.bots.get(bot["bot_id"])["chat_session_id"] == actor.session_id
    stack.rig.drivers[-1].busy = False


async def test_other_workspace_is_unavailable_for_startup_bridge(integration: Integration) -> None:
    stack = integration
    root = stack.rig.vault.parent / "another-vault"
    root.mkdir()
    workspace, _ = stack.rig.workspaces.register_existing(ident(), str(root))
    bot = await stack.create(workspace_id=workspace.workspace_id)
    assert bot["delivery"] == {
        "enabled": False,
        "available": False,
        "reason": "workspace_mismatch",
    }
    response = await stack.action(bot, "delivery", enabled=True)
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "delivery_unavailable"
    )
    detail = (await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)).json()
    assert detail["chat_status"]["blockers"] == ["workspace_restart_required"]


@pytest.mark.parametrize("missing", [False, True])
async def test_missing_or_archived_profile_is_explicit_without_attachment(
    integration: Integration,
    missing: bool,
) -> None:
    stack = integration
    bot = await stack.create()
    if missing:
        with stack.rig.state.store._connection(write=True) as connection:
            connection.execute("DELETE FROM profiles WHERE profile_id=?", (bot["profile_id"],))
        code, state = "profile_missing", "unavailable"
    else:
        profiles = ProfileService(stack.rig.state.store)
        row = profiles.get(bot["profile_id"])
        profiles.archive(bot["profile_id"], ident(), row["state_revision"], True)
        code, state = "profile_archived", "blocked"
    detail = (await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)).json()
    assert detail["chat_status"] == {"state": state, "blockers": [code]}
    response = await stack.action(bot, "chat")
    assert response.status_code == 422 and response.json()["error"]["code"] == code
    assert stack.manager.get(bot["chat_session_id"]) is None and not stack.rig.drivers


async def test_opt_out_revokes_queued_notice_before_actual_http(
    integration: Integration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = integration
    bot = await stack.enable(await stack.create())
    stack.http.gate.clear()
    assert stack.bridge._enqueue("Synthetic writer occupant", "")
    assert (await take(stack.http.sent))[0] == "Synthetic writer occupant"
    entered = asyncio.Event()
    original = stack.bridge.send_notice

    async def queued(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        return await original(*args, **kwargs)

    monkeypatch.setattr(stack.bridge, "send_notice", queued)
    runner = stack.app.state.routine_tasks
    routine_id = stack.routine()
    attempt, _ = await runner.run_now(routine_id, ident(), 1)
    runs = list(runner._runs)
    try:
        await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
        assert stack.manager.get(attempt.session_id) is None  # native slot freed before DM wait
        response = await stack.action(bot, "delivery", enabled=False)
        assert response.status_code == 200 and not response.json()["bot"]["delivery"]["enabled"]
    finally:
        stack.http.gate.set()
        await asyncio.wait_for(asyncio.gather(*runs), HANG_GUARD_S)
    assert stack.http.sent.empty()
    assert stack.app.state.bots.delivery(attempt.attempt_id)["state"] == "off"
    assert runner.store.runs(routine_id, 1, None)[0]["attempt"]["outcome"] == "succeeded"


async def test_messaging_disable_revokes_consent_and_settles_delivery(
    integration: Integration,
) -> None:
    stack = integration
    bot = await stack.enable(await stack.create())
    stack.http.gate.clear()
    runner = stack.app.state.routine_tasks
    routine_id = stack.routine()
    attempt, _ = await runner.run_now(routine_id, ident(), 1)
    runs = list(runner._runs)
    assert "Routine succeeded" in (await take(stack.http.sent))[0]
    response = await stack.client.post("/api/v2/messaging/mattermost/disable", headers=WRITE)
    assert response.status_code == 200, response.text
    await asyncio.wait_for(asyncio.gather(*runs), HANG_GUARD_S)
    detail = (await stack.client.get(f"/api/v2/bots/{bot['bot_id']}", headers=AUTH)).json()
    assert not detail["bot"]["delivery"]["enabled"] and not stack.bridge._armed
    assert stack.app.state.bots.delivery(attempt.attempt_id)["state"] == "unknown"
    assert not stack.app.state.bot_runtime._contexts


async def test_shutdown_disarms_before_task_receipt_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    context = stack.app.router.lifespan_context(stack.app)
    await context.__aenter__()
    await stack.connect()
    await take(stack.http.sent)
    bot = await stack.enable(await stack.create())
    stack.http.gate.clear()
    runner = stack.app.state.routine_tasks
    attempt, _ = await runner.run_now(stack.routine(), ident(), 1)
    assert "Routine succeeded" in (await take(stack.http.sent))[0]
    original = runner.close
    checked = False

    async def close() -> None:
        nonlocal checked
        assert not stack.bridge._armed
        assert stack.app.state.bot_runtime._closing
        assert not stack.app.state.bot_runtime._consents
        checked = True
        await original()

    monkeypatch.setattr(runner, "close", close)
    try:
        await asyncio.wait_for(context.__aexit__(None, None, None), HANG_GUARD_S)
        assert checked and not runner._runs and not stack.app.state.bot_runtime._contexts
        assert stack.app.state.bots.delivery(attempt.attempt_id)["state"] == "unknown"
        assert stack.app.state.bots.delivery(attempt.attempt_id)["bot_id"] == bot["bot_id"]
    finally:
        stack.http.gate.set()
        await stack.client.aclose()


@pytest.mark.parametrize("held_at", ["prepare", "source"])
@pytest.mark.parametrize("cancel_http", [False, True])
async def test_shutdown_drains_cancelled_open_worker_without_late_actor_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    held_at: str,
    cancel_http: bool,
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    context = stack.app.router.lifespan_context(stack.app)
    await context.__aenter__()
    await stack.connect()
    await take(stack.http.sent)
    bot = await stack.create()
    if held_at == "source":
        assert (await stack.action(bot, "chat")).status_code == 200
        await stack.manager.close(bot["chat_session_id"])
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original = ProfileLaunchService.prepare

    def prepare(self: ProfileLaunchService, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(self, *args, **kwargs)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(HANG_GUARD_S)
        return result

    if held_at == "prepare":
        monkeypatch.setattr(ProfileLaunchService, "prepare", prepare)
    else:
        original_source = server.existing_session_source

        def source(*args: Any, **kwargs: Any) -> Any:
            result = original_source(*args, **kwargs)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(HANG_GUARD_S)
            return result

        monkeypatch.setattr(server, "existing_session_source", source)
    opening = asyncio.create_task(stack.action(bot, "chat"))
    await asyncio.wait_for(entered.wait(), HANG_GUARD_S)
    if cancel_http:
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
    closed = asyncio.Event()
    original_close = stack.manager.close_all

    async def close_all() -> None:
        await original_close()
        closed.set()

    monkeypatch.setattr(stack.manager, "close_all", close_all)
    exiting = asyncio.create_task(context.__aexit__(None, None, None))
    try:
        await asyncio.wait_for(closed.wait(), HANG_GUARD_S)
        assert stack.app.state.bot_runtime._closing
        release.set()
        if not cancel_http:
            response = await asyncio.wait_for(opening, HANG_GUARD_S)
            assert response.status_code == 503 and response.json()["error"]["code"] == "app_closing"
        await asyncio.wait_for(exiting, HANG_GUARD_S)
        assert stack.manager.get(bot["chat_session_id"]) is None and not stack.rig.drivers
        assert stack.app.state.bots.get(bot["bot_id"])["chat"]["profile_revision"] == 1
    finally:
        release.set()
        await asyncio.gather(exiting, return_exceptions=True)
        await stack.client.aclose()


async def test_startup_marks_sending_unknown_without_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = Integration(tmp_path, monkeypatch)
    bot = await stack.create()
    routine_id = stack.routine()
    runner = stack.app.state.routine_tasks
    attempt, _ = runner.store.claim_manual(routine_id, ident(), 1, stamp(NOW))
    assert runner.store.finish(attempt.attempt_id, "succeeded", "", stamp(NOW))
    assert stack.app.state.bots.record_delivery(
        attempt.attempt_id,
        bot["bot_id"],
        attention="notify",
        reason="runs_to_review",
        state="sending",
        delivery_reason="none",
    )
    async with stack.app.router.lifespan_context(stack.app):
        await stack.connect()
        await take(stack.http.sent)
        receipt = stack.app.state.bots.delivery(attempt.attempt_id)
        assert receipt["state"] == "unknown" and receipt["delivery_reason"] == "send_unknown"
        assert stack.http.sent.empty() and not stack.rig.drivers
    await stack.client.aclose()


async def test_last_window_reconnect_preserves_local_chat_but_revokes_remote_consent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Windows:
        count = 1

        def __init__(self) -> None:
            self.admission_lock = asyncio.Lock()

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    windows = Windows()
    stack = Integration(tmp_path, monkeypatch, windows=windows)
    async with stack.app.router.lifespan_context(stack.app), stack.client:
        await stack.connect()
        await take(stack.http.sent)
        bot = await stack.enable(await stack.create())
        await stack.action(bot, "chat")
        actor = stack.manager.get(bot["chat_session_id"])
        assert actor is not None and await actor.send("Synthetic owner message")
        stack.rig.drivers[-1].busy = True

        async def reconnected(_timeout: float) -> bool:
            windows.count = 1
            return True

        monkeypatch.setattr(actor, "wait_idle", reconnected)
        windows.count = 0
        await stack.manager.settle(0.05, until=lambda: windows.count == 0)
        assert windows.count == 1 and not stack.app.state.bot_runtime._closing
        assert not stack.bridge._armed and not stack.app.state.bot_runtime._consents
        response = await stack.action(bot, "chat")
        assert (
            response.status_code == 200
            and response.json()["preview"]["generation"] == actor.generation
        )
        assert not stack.app.state.bot_runtime.delivery_status(bot)["enabled"]
        stack.rig.drivers[-1].busy = False
