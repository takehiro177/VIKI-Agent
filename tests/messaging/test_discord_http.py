"""Discord identity, admission and pacing against synthetic fixed transports."""

from __future__ import annotations

import asyncio
import copy
import json
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from viki_agent.messaging.discord_channel import DiscordChannel
from viki_agent.messaging.discord_http import DiscordHttp, DiscordHttpError, normalize_post
from viki_agent.messaging.mattermost_bot import RemoteBot
from viki_agent.messaging.mattermost_http import FixedHttpError
from viki_agent.messaging.messaging_channel import Provider, RemoteBinding
from viki_agent.messaging.messaging_config import DiscordConfig
from viki_agent.messaging.messaging_runtime import MessagingRuntime

BOT, OWNER, CHANNEL, APPLICATION, OTHER = (str(10**17 + value) for value in range(1, 6))
DATE = "Tue, 13 Oct 2026 01:02:03 GMT"
CUTOFF = 1791853324000
POST = str((CUTOFF - 1420070400000) << 22)
SENT = str(int(POST) + 1)
CONFIG = DiscordConfig("synthetic-bot-token", OWNER, "https://client.example.test/support")


class Clock:
    def __init__(self) -> None:
        self.now = 10.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.waits.append(delay)
        self.now += delay


class _HttpBot:
    """A synthetic runtime actor that performs the real client's startup and cleanup."""

    def __init__(self, http: Any) -> None:
        self.http = http
        self.state = "connecting"
        self.ready, self.held = asyncio.Event(), asyncio.Event()

    @property
    def stop_reason(self) -> str:
        # The runtime reads this when the bot's run() task ends, same as RemoteBot.stop_reason.
        return "connection_lost" if self.ready.is_set() else "start_failed"

    async def run(self) -> None:
        await self.http.bind()
        await self.http.fresh_cutoff()
        self.state = "connected"
        self.ready.set()
        await self.held.wait()

    def disarm(self, reason: object = None) -> None:
        self.state = "stopped"

    async def close(self) -> None:
        await self.http.close()


async def runtime_reenable_after_public_deadline(
    provider: Provider,
    make_http: Callable[[], Any],
    clock: Clock,
    establish: Callable[[Any], Awaitable[float]],
) -> None:
    """Drive the real runtime's public callback across closed and fresh HTTP generations."""
    from viki_agent.messaging.messaging_runtime import MessagingError, MessagingRuntime

    actors: list[_HttpBot] = []

    def factory(_on_disarm: Callable[[], None]) -> RemoteBot:
        actor = _HttpBot(make_http())

        def disarm() -> None:
            actor.disarm()
            _on_disarm()

        # Some Gateway-only synthetic clients expose just their Identify deadline.
        register = getattr(actor.http, "set_failure_handler", None)
        if register is not None:
            register(disarm)
        actors.append(actor)
        return cast(RemoteBot, actor)

    runtime = MessagingRuntime(
        mattermost=factory if provider == "mattermost" else None,
        discord=factory if provider == "discord" else None,
        slack=factory if provider == "slack" else None,
        cooldown_until=lambda _provider, bot: cast(_HttpBot, bot).http.cooldown_until,
        clock=clock,
    )
    try:
        await runtime.set_enabled(provider, True)
        await asyncio.wait_for(actors[0].ready.wait(), 0.05)
        first = actors[0].http
        deadline = await establish(first)
        assert first.cooldown_until == deadline and deadline > clock.now
        await runtime.set_enabled(provider, False)
        assert first.cooldown_until == deadline
        with pytest.raises(MessagingError, match="^cooldown$"):
            await runtime.set_enabled(provider, True)
        assert len(actors) == 1
        clock.now = deadline
        await runtime.set_enabled(provider, True)
        await asyncio.wait_for(actors[1].ready.wait(), 0.05)
        assert actors[1].http is not first and actors[1].state == "connected"
    finally:
        await runtime.close()


class Stream(httpx2.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.data

    async def aclose(self) -> None:
        self.closed = True


def post(**changes: Any) -> dict[str, Any]:
    return {
        "id": POST,
        "channel_id": CHANNEL,
        "author": {"id": OWNER},
        "content": "literal synthetic text",
        "timestamp": "2026-10-13T01:02:04.000000+00:00",
        "edited_timestamp": None,
        "type": 0,
        "tts": False,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": [],
        "embeds": [],
        "components": [],
        "pinned": False,
        "flags": 0,
        **changes,
    }


class Server:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.streams: list[Stream] = []
        self.headers = {"date": DATE}
        channel = {"id": CHANNEL, "type": 1, "recipients": [{"id": OWNER}]}
        self.bodies: dict[str, Any] = {
            "/users/@me": {"id": BOT, "bot": True},
            "/users/" + OWNER: {"id": OWNER},
            "/applications/@me": {"id": APPLICATION, "bot": {"id": BOT, "bot": True}},
            "/users/@me/channels": copy.deepcopy(channel),
            "/channels/" + CHANNEL: copy.deepcopy(channel),
            f"/channels/{CHANNEL}/messages/{POST}": post(),
            "/gateway/bot": {
                "url": "wss://gateway.discord.gg/",
                "shards": 1,
                "session_start_limit": {
                    "total": 1000,
                    "remaining": 999,
                    "reset_after": 14400000,
                    "max_concurrency": 1,
                },
            },
        }
        self.statuses: dict[str, int] = {}
        self.override: httpx2.Response | None = None
        self.send_changes: dict[str, Any] = {}

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == "Bot " + CONFIG.bot_token
        assert (
            request.headers["user-agent"]
            == f"DiscordBot (https://client.example.test/support, {metadata.version('viki-agent')})"
        )
        assert request.headers["accept-encoding"] == "identity"
        assert "cookie" not in request.headers
        assert request.url.scheme == "https" and request.url.host == "discord.com"
        assert request.url.port is None and request.url.query == b""
        assert request.url.path.startswith("/api/v10/")
        if self.override is not None:
            return self.override
        path = request.url.path.removeprefix("/api/v10")
        if path in self.statuses:
            stream = Stream(b"{}")
            self.streams.append(stream)
            return httpx2.Response(self.statuses[path], headers=self.headers, stream=stream)
        if path == f"/channels/{CHANNEL}/messages" and request.method == "POST":
            body = post(
                **{
                    "id": SENT,
                    "author": {"id": BOT, "bot": True},
                    "flags": 4,
                    "content": json.loads(request.content)["content"],
                    **self.send_changes,
                }
            )
        else:
            body = self.bodies[path]
        stream = Stream(json.dumps(body).encode("utf-8"))
        self.streams.append(stream)
        return httpx2.Response(200, headers=self.headers, stream=stream)


@pytest.fixture
def server() -> Server:
    return Server()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def http(server: Server, clock: Clock) -> AsyncIterator[DiscordHttp]:
    instance = DiscordHttp(
        CONFIG, transport=httpx2.MockTransport(server), clock=clock, sleep=clock.sleep
    )
    try:
        yield instance
    finally:
        await instance.close()


async def test_exact_bot_application_human_owner_and_type_one_dm(
    http: DiscordHttp, server: Server
) -> None:
    binding = await http.bind()
    assert binding == RemoteBinding("discord", (APPLICATION,), BOT, OWNER, CHANNEL)
    assert http.binding == binding and APPLICATION != BOT
    assert [
        (request.method, request.url.path.removeprefix("/api/v10")) for request in server.requests
    ] == [
        ("GET", "/users/@me"),
        ("GET", "/applications/@me"),
        ("GET", "/users/" + OWNER),
        ("POST", "/users/@me/channels"),
        ("GET", "/channels/" + CHANNEL),
    ]
    assert json.loads(server.requests[3].content) == {"recipient_id": OWNER}
    assert all(stream.closed for stream in server.streams)
    assert http.ssl_context.verify_mode == ssl.CERT_REQUIRED and http.ssl_context.check_hostname


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/users/@me", {"id": BOT}),
        ("/users/@me", {"id": BOT, "bot": False}),
        ("/users/@me", {"id": BOT, "bot": 1}),
        ("/users/@me", {"id": BOT, "bot": True, "system": True}),
        ("/users/@me", {"id": OWNER, "bot": True}),
        ("/users/@me", {"id": "0" + BOT, "bot": True}),
        ("/users/" + OWNER, {"id": OTHER}),
        ("/users/" + OWNER, {"id": OWNER, "bot": True}),
        ("/users/" + OWNER, {"id": OWNER, "bot": 0}),
        ("/users/" + OWNER, {"id": OWNER, "system": True}),
        ("/users/@me/channels", {"id": CHANNEL, "type": 3, "recipients": [{"id": OWNER}]}),
        ("/users/@me/channels", {"id": CHANNEL, "type": 0, "recipients": [{"id": OWNER}]}),
        ("/users/@me/channels", {"id": CHANNEL, "type": True, "recipients": [{"id": OWNER}]}),
        (
            "/users/@me/channels",
            {"id": CHANNEL, "type": 1, "guild_id": OTHER, "recipients": [{"id": OWNER}]},
        ),
        ("/channels/" + CHANNEL, {"id": OTHER, "type": 1, "recipients": [{"id": OWNER}]}),
        (
            "/channels/" + CHANNEL,
            {"id": CHANNEL, "type": 1, "recipients": [{"id": OWNER}, {"id": OTHER}]},
        ),
        (
            "/channels/" + CHANNEL,
            {"id": CHANNEL, "type": 1, "recipients": [{"id": OWNER, "bot": True}]},
        ),
        ("/channels/" + CHANNEL, {"id": CHANNEL, "type": 1, "recipients": []}),
    ],
)
async def test_group_guild_and_human_tokens_never_bind(
    http: DiscordHttp, server: Server, path: str, body: Any
) -> None:
    server.bodies[path] = body
    with pytest.raises(DiscordHttpError, match="^discord_binding_failed$"):
        await http.bind()
    assert http.binding is None


@pytest.mark.parametrize(
    "body",
    [
        {"id": "bad"},
        {"id": APPLICATION, "bot": {"id": OTHER}},
        {"id": APPLICATION, "bot": {"id": BOT, "bot": False}},
    ],
)
async def test_application_bot_mismatch_does_not_bind(
    http: DiscordHttp, server: Server, body: Any
) -> None:
    server.bodies["/applications/@me"] = body
    with pytest.raises(DiscordHttpError, match="^discord_binding_failed$"):
        await http.bind()
    assert len(server.requests) == 2


async def test_partial_application_bot_user_is_checked_by_authenticated_id(
    http: DiscordHttp, server: Server
) -> None:
    server.bodies["/applications/@me"]["bot"] = {"id": BOT}
    assert (await http.bind()).scope == (APPLICATION,)


async def test_fresh_cutoff_uses_monotonic_canonical_server_date(
    http: DiscordHttp, server: Server
) -> None:
    await http.bind()
    assert await http.fresh_cutoff() == CUTOFF
    server.headers["date"] = "Tue, 13 Oct 2026 01:02:04 GMT"
    assert await http.fresh_cutoff() == CUTOFF + 1000
    server.headers["date"] = DATE
    with pytest.raises(DiscordHttpError, match="^discord_freshness_failed$"):
        await http.fresh_cutoff()


@pytest.mark.parametrize(
    "headers", [{}, {"date": "bad"}, {"date": "Mon, 13 Oct 2026 01:02:03 GMT"}]
)
async def test_no_bad_server_date_establishes_freshness(
    http: DiscordHttp, server: Server, headers: dict[str, str]
) -> None:
    server.headers = headers
    with pytest.raises(DiscordHttpError, match="^discord_freshness_failed$"):
        await http.bind()


async def test_literal_original_message_and_rest_confirmation(
    http: DiscordHttp, server: Server
) -> None:
    binding = await http.bind()
    cutoff = await http.fresh_cutoff()
    candidate = http.validate(post(), cutoff)
    assert candidate is not None
    assert candidate.id == POST and candidate.message == "literal synthetic text"
    assert candidate.created_ms == CUTOFF and candidate.root_id == ""
    assert len(candidate.fingerprint) == 64 and candidate.size_bytes < 12 * 1024
    assert await http.confirm(candidate) == candidate
    assert (
        normalize_post(post(unknown={"private_metadata": "not retained"}), binding, cutoff)
        == candidate
    )
    server.bodies[f"/channels/{CHANNEL}/messages/{POST}"]["content"] = "changed before approval"
    assert await http.confirm(candidate) is None


@pytest.mark.parametrize(
    "change",
    [
        {"id": "0" + POST},
        {"id": 123},
        {"id": str(2**64)},
        {"id": "9" * 10000},
        {"channel_id": OTHER},
        {"author": {"id": OTHER}},
        {"author": {"id": OWNER, "bot": True}},
        {"author": {"id": OWNER, "bot": 0}},
        {"author": {"id": OWNER, "system": True}},
        {"type": 19},
        {"type": 1},
        {"type": False},
        {"guild_id": OTHER},
        {"member": {}},
        {"webhook_id": OTHER},
        {"edited_timestamp": "2026-10-13T01:02:05+00:00"},
        {"timestamp": "2026-10-13T01:02:03+00:00"},
        {"timestamp": "2026-10-13T01:02:05+00:00"},
        {"timestamp": "not a timestamp"},
        {"content": ""},
        {"content": "\ud800"},
        {"content": "x" * (12 * 1024)},
        {"tts": True},
        {"mention_everyone": True},
        {"attachments": [{"id": OTHER}]},
        {"embeds": [{}]},
        {"components": [{}]},
        {"poll": {}},
        {"message_reference": {}},
        {"referenced_message": None},
        {"message_snapshots": [{}]},
        {"sticker_items": [{}]},
        {"interaction_metadata": {}},
        {"application_id": APPLICATION},
        {"activity": {}},
        {"thread": {}},
        {"flags": 1},
        {"flags": False},
        {"mentions": [{"id": OTHER}]},
        {"pinned": True},
    ],
)
async def test_original_fresh_plain_owner_message_only(
    http: DiscordHttp, change: dict[str, Any]
) -> None:
    await http.bind()
    assert http.validate(post(**change), CUTOFF) is None


async def test_timestamp_normalization_keeps_submillisecond_identity(http: DiscordHttp) -> None:
    binding = await http.bind()
    value = normalize_post(post(timestamp="2026-10-13T01:02:04.000001Z"), binding, CUTOFF)
    same = normalize_post(post(timestamp="2026-10-13T01:02:04.000001+00:00"), binding, CUTOFF)
    different = normalize_post(post(timestamp="2026-10-13T01:02:04.000002Z"), binding, CUTOFF)
    assert value is not None and value == same and value != different
    assert normalize_post(post(), binding, CUTOFF + 1) is None


async def test_forged_confirmation_is_refused_before_io(http: DiscordHttp, server: Server) -> None:
    await http.bind()
    candidate = http.validate(post(), CUTOFF)
    assert candidate is not None
    count = len(server.requests)
    assert await http.confirm(replace(candidate, id="../other")) is None
    assert await http.confirm(replace(candidate, root_id=OTHER)) is None
    assert len(server.requests) == count


async def test_send_has_exact_payload_no_mentions_or_reference_and_exact_receipt(
    http: DiscordHttp, server: Server
) -> None:
    await http.bind()
    await http.send("literal @everyone text", POST)
    assert json.loads(server.requests[-1].content) == {
        "content": "literal @everyone text",
        "allowed_mentions": {"parse": [], "replied_user": False},
        "flags": 4,
        "tts": False,
    }
    assert server.requests[-1].url.path == f"/api/v10/channels/{CHANNEL}/messages"


@pytest.mark.parametrize(
    "change",
    [
        {"id": "bad"},
        {"channel_id": OTHER},
        {"author": {"id": OWNER}},
        {"content": "altered"},
        {"type": 19},
        {"webhook_id": OTHER},
        {"embeds": [{}]},
        {"mention_everyone": True},
    ],
)
async def test_send_rejects_unverified_receipts(
    http: DiscordHttp, server: Server, change: dict[str, Any]
) -> None:
    await http.bind()
    server.send_changes = change
    with pytest.raises(DiscordHttpError, match="^discord_post_invalid$"):
        await http.send("literal")


@pytest.mark.parametrize(
    ("content", "reply"),
    [("", ""), ("a" * 2001, ""), ("\U0001f600" * 1001, ""), ("\ud800", ""), ("text", "../channel")],
)
async def test_send_utf16_limits_and_reply_ids_precede_io(
    http: DiscordHttp, server: Server, content: str, reply: str
) -> None:
    await http.bind()
    count = len(server.requests)
    with pytest.raises(DiscordHttpError, match="^discord_post_invalid$"):
        await http.send(content, reply)
    assert len(server.requests) == count


async def test_two_thousand_utf16_units_are_admitted(http: DiscordHttp) -> None:
    await http.bind()
    await http.send("\U0001f600" * 1000)


async def test_gateway_returns_only_fixed_url_single_shard_and_bounded_quota(
    http: DiscordHttp,
) -> None:
    await http.bind()
    info = await http.gateway()
    assert (
        info.url,
        info.shards,
        info.total,
        info.remaining,
        info.reset_after,
        info.max_concurrency,
    ) == ("wss://gateway.discord.gg/", 1, 1000, 999, 14400000, 1)


@pytest.mark.parametrize(
    "change",
    [
        {"url": "wss://evil.example.test/"},
        {"url": "wss://gateway.discord.gg/?token=bad"},
        {"shards": 2},
        {"shards": True},
        {
            "session_start_limit": {
                "total": 1000,
                "remaining": 1001,
                "reset_after": 1,
                "max_concurrency": 1,
            }
        },
        {
            "session_start_limit": {
                "total": 1000,
                "remaining": 1,
                "reset_after": 86400001,
                "max_concurrency": 1,
            }
        },
        {
            "session_start_limit": {
                "total": 1000,
                "remaining": True,
                "reset_after": 1,
                "max_concurrency": 1,
            }
        },
    ],
)
async def test_gateway_never_delegates_authority(
    http: DiscordHttp, server: Server, change: dict[str, Any]
) -> None:
    await http.bind()
    server.bodies["/gateway/bot"].update(change)
    with pytest.raises(DiscordHttpError, match="^discord_gateway_failed$"):
        await http.gateway()


async def test_empty_identify_quota_retains_cooldown_and_requires_later_manual_bind(
    http: DiscordHttp, server: Server, clock: Clock
) -> None:
    await http.bind()
    server.bodies["/gateway/bot"]["session_start_limit"]["remaining"] = 0
    with pytest.raises(DiscordHttpError, match="^discord_cooldown$"):
        await http.gateway()
    assert http.cooldown_until == clock.now + 14400
    count = len(server.requests)
    with pytest.raises(DiscordHttpError, match="^discord_cooldown$"):
        await http.bind()
    assert len(server.requests) == count
    clock.now = http.cooldown_until
    assert await http.bind()


async def test_valid_rate_headers_delay_the_next_new_request(
    http: DiscordHttp, server: Server, clock: Clock
) -> None:
    server.headers.update(
        {
            "x-ratelimit-limit": "5",
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset-after": "0.125",
            "x-ratelimit-bucket": "fixed-bucket",
        }
    )
    await http.bind()
    assert clock.waits == [0.125] * 4
    await http.fresh_cutoff()
    assert clock.waits[-1] == 0.125


@pytest.mark.parametrize("wait", ["bucket", "retry_after", "identify"])
async def test_successful_rate_and_identify_wait_survive_runtime_fresh_objects(
    server: Server, clock: Clock, wait: str
) -> None:
    def make_http() -> DiscordHttp:
        return DiscordHttp(
            CONFIG, transport=httpx2.MockTransport(server), clock=clock, sleep=clock.sleep
        )

    async def establish(client: DiscordHttp) -> float:
        if wait == "identify":
            deadline = clock.now + 5
            client.reserve_identify()
            with pytest.raises(DiscordHttpError, match="^discord_cooldown$"):
                client.reserve_identify()
            # Reserving a Gateway Identify does not prevent that connection's cutoff REST read.
            assert await client.fresh_cutoff() == CUTOFF
            with pytest.raises(DiscordHttpError, match="^discord_cooldown$"):
                client.reserve_identify()
            return deadline
        server.headers.update(
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset-after": "0.125"}
            if wait == "bucket"
            else {"retry-after": "0.125"}
        )
        await client.send("one successful message")
        return clock.now + 0.125

    await runtime_reenable_after_public_deadline("discord", make_http, clock, establish)


@pytest.mark.parametrize("retry", ["1.25", "86400", "NaN", "1e999", "9" * 100, "-1", "86401"])
async def test_429_stops_without_retry_and_cooldown_is_bounded(
    http: DiscordHttp, server: Server, clock: Clock, retry: str
) -> None:
    await http.bind()
    server.override = httpx2.Response(
        429,
        headers={"date": DATE, "retry-after": retry},
        stream=Stream(b'{"message":"private response"}'),
    )
    with pytest.raises(DiscordHttpError, match="^discord_cooldown$"):
        await http.send("one send")
    expected = float(retry) if retry in ("1.25", "86400") else 86400
    assert http.cooldown_until == clock.now + expected
    count = len(server.requests)
    clock.now = http.cooldown_until
    with pytest.raises(DiscordHttpError, match="^discord_cooldown$"):
        await http.send("no automatic retry")
    assert len(server.requests) == count
    server.override = None
    assert await http.bind()


@pytest.mark.parametrize(
    "rate",
    [
        {"x-ratelimit-remaining": "0"},
        {"x-ratelimit-remaining": "9" * 100},
        {"x-ratelimit-remaining": "0", "x-ratelimit-reset-after": "1e999"},
        {"x-ratelimit-limit": "2", "x-ratelimit-remaining": "3", "x-ratelimit-reset-after": "1"},
    ],
)
async def test_malformed_numeric_rate_headers_fail_closed(
    http: DiscordHttp, server: Server, rate: dict[str, str]
) -> None:
    server.headers.update(rate)
    with pytest.raises(DiscordHttpError, match="^discord_http_failed$"):
        await http.bind()
    assert len(server.requests) == 1


async def test_rate_wait_is_cancelable_before_a_new_request(
    http: DiscordHttp, server: Server, clock: Clock
) -> None:
    await http.bind()
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(_: float) -> None:
        started.set()
        await release.wait()

    http._sleep = blocked
    server.headers.update({"x-ratelimit-remaining": "0", "x-ratelimit-reset-after": "0.125"})
    clock.now += 1
    await http.send("establish the next wait")
    count = len(server.requests)
    task = asyncio.create_task(http.send("cancel before io"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(server.requests) == count


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "https://evil.example.test"),
        ("GET", "//evil.example.test/users/@me"),
        ("GET", "/users/@me?token=bad"),
        ("GET", "/users/" + OTHER),
        ("GET", f"/channels/{OTHER}/messages/{POST}"),
        ("POST", f"/channels/{OTHER}/messages"),
        ("PATCH", f"/channels/{CHANNEL}/messages/{POST}"),
    ],
)
async def test_closed_endpoint_allowlist_precedes_io(
    http: DiscordHttp, server: Server, method: str, path: str
) -> None:
    await http.bind()
    count = len(server.requests)
    with pytest.raises(FixedHttpError):
        await http._http.request(method, path)
    assert len(server.requests) == count


@pytest.mark.parametrize(
    ("status", "headers", "data"),
    [
        (302, {"location": "https://evil.example.test/"}, b"{}"),
        (200, {"content-encoding": "gzip"}, b"{}"),
        (200, {"set-cookie": "secret=x"}, b"{}"),
        (200, {}, b"x" * (128 * 1024 + 1)),
        (200, {}, b'{"id":"one","id":"two"}'),
    ],
    ids=("redirect", "compressed", "cookie", "oversize", "duplicate-key"),
)
async def test_fixed_transport_caps_redirects_compression_cookies_and_json(
    http: DiscordHttp, server: Server, status: int, headers: dict[str, str], data: bytes
) -> None:
    stream = Stream(data)
    server.override = httpx2.Response(status, headers={"date": DATE, **headers}, stream=stream)
    with pytest.raises(DiscordHttpError, match="^discord_http_failed$"):
        await http.bind()
    assert len(server.requests) == 1 and stream.closed


@pytest.mark.parametrize(
    "config",
    [
        DiscordConfig("bad\r\nheader", OWNER, CONFIG.client_url),
        DiscordConfig(CONFIG.bot_token, "../owner", CONFIG.client_url),
        DiscordConfig(CONFIG.bot_token, OWNER, "https://user:secret" + "@" + "evil.example.test/"),
        DiscordConfig(CONFIG.bot_token, OWNER, "https://client.example.test/\r\nBad"),
    ],
)
def test_direct_config_values_cannot_escape_headers(config: DiscordConfig) -> None:
    with pytest.raises(DiscordHttpError, match="^discord_binding_failed$"):
        DiscordHttp(config)


async def test_failure_handler_saves_429_before_self_cancel_and_runtime_replacement(
    server: Server, clock: Clock
) -> None:
    from tests.messaging.test_messaging_http import HeldCloseStream

    def make_http() -> DiscordHttp:
        return DiscordHttp(
            CONFIG, transport=httpx2.MockTransport(server), clock=clock, sleep=clock.sleep
        )

    async def establish(client: DiscordHttp) -> float:
        stream = HeldCloseStream([b"private rate response"])
        server.override = httpx2.Response(
            429, headers={"retry-after": "7", "date": DATE}, stream=stream
        )
        previous = client._failure_handler
        seen: list[float] = []

        def stop() -> None:
            seen.append(client.cooldown_until)
            assert client.cooldown_until == clock.now + 7
            assert previous is not None
            previous()
            task = asyncio.current_task()
            assert task is not None
            task.cancel()

        client.set_failure_handler(stop)
        count = len(server.requests)
        sending = asyncio.create_task(client.send("one send"))
        try:
            await asyncio.wait_for(stream.closing.wait(), 0.05)
            with pytest.raises(asyncio.CancelledError):
                await sending
            assert len(seen) == 1 and len(server.requests) == count + 1
            return seen[0]
        finally:
            stream.release.set()
            await asyncio.gather(sending, return_exceptions=True)
            server.override = None

    await runtime_reenable_after_public_deadline("discord", make_http, clock, establish)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("me-401", "token_rejected"),
        ("me-403", None),
        ("me-500", None),
        ("owner-404", "account_check_failed"),
        ("dm-403", "account_check_failed"),
        ("owner-429", None),
        ("owner-is-bot", "account_check_failed"),
        ("unanswered", "server_unreachable"),
        ("gateway-401", "token_rejected"),
        ("send-403", "account_check_failed"),
        ("gateway-403", None),
        ("applications-403", None),
    ],
)
async def test_discord_names_why_it_failed(
    http: DiscordHttp, server: Server, clock: Clock, setup: str, reason: str | None
) -> None:
    paths = {
        "me": "/users/@me",
        "owner": "/users/" + OWNER,
        "dm": "/users/@me/channels",
        "gateway": "/gateway/bot",
        "send": f"/channels/{CHANNEL}/messages",
        "applications": "/applications/@me",
    }
    client = http
    if setup == "unanswered":

        def unreachable(_: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("synthetic private unreachable")

        client = DiscordHttp(
            CONFIG, transport=httpx2.MockTransport(unreachable), clock=clock, sleep=clock.sleep
        )
    seen: list[str | None] = []
    client.set_failure_handler(lambda: seen.append(client.stop_reason))
    try:
        if setup in ("gateway-401", "gateway-403", "send-403"):
            await client.bind()
        if setup == "owner-is-bot":
            server.bodies["/users/" + OWNER]["bot"] = True
        elif setup != "unanswered":
            key, status = setup.rsplit("-", 1)
            server.statuses[paths[key]] = int(status)
        with pytest.raises(DiscordHttpError):
            if setup in ("gateway-401", "gateway-403"):
                await client.gateway()
            elif setup == "send-403":
                await client.send("probe message")
            else:
                await client.bind()
        assert client.stop_reason == reason
        if setup == "me-401":
            assert len(server.requests) == 1
        if setup != "owner-is-bot":
            assert seen and seen[0] == reason
    finally:
        if client is not http:
            await client.close()


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("me-401", "token_rejected"),
        ("owner-404", "account_check_failed"),
        ("dm-403", "account_check_failed"),
        ("gateway-401", "token_rejected"),
        ("unanswered", "server_unreachable"),
        ("gateway-unanswered", "server_unreachable"),
    ],
)
async def test_failed_discord_start_reads_its_reason(
    tmp_path: Path, server: Server, clock: Clock, setup: str, reason: str
) -> None:
    from tests.messaging.test_mattermost_bot import HANG_GUARD_S, Rig

    transport: Any
    failed: list[httpx2.Request] = []
    if setup in ("unanswered", "gateway-unanswered"):

        def unreachable(request: httpx2.Request) -> httpx2.Response:
            if setup == "unanswered" or request.url.path.endswith("/gateway/bot"):
                failed.append(request)
                raise httpx2.ConnectError("synthetic private unreachable", request=request)
            return server(request)

        transport = httpx2.MockTransport(unreachable)
    else:
        path, status = {
            "me-401": ("/users/@me", 401),
            "owner-404": ("/users/" + OWNER, 404),
            "dm-403": ("/users/@me/channels", 403),
            "gateway-401": ("/gateway/bot", 401),
        }[setup]
        server.statuses[path] = status
        transport = httpx2.MockTransport(server)

    rig = Rig(tmp_path)
    http = DiscordHttp(CONFIG, transport=transport, clock=clock, sleep=clock.sleep)

    def factory(on_disarm: Callable[[], None]) -> RemoteBot:
        return RemoteBot(
            DiscordChannel(CONFIG, http),
            rig.manager,
            rig.approvals,
            create_actor=rig.create,
            on_disarm=on_disarm,
        )

    runtime = MessagingRuntime(discord=factory)
    try:
        enabled = await runtime.set_enabled("discord", True)
        assert enabled["state"] == "connecting"
        run_task = runtime._entries["discord"].run
        assert run_task is not None
        async with asyncio.timeout(HANG_GUARD_S):
            await run_task
        assert runtime.status()["discord"] == {
            "configured": True,
            "state": "stopped",
            "restart_required": False,
            "reason": reason,
        }
        assert http.stop_reason == reason
        assert len(failed) == (1 if setup in ("unanswered", "gateway-unanswered") else 0)
    finally:
        await runtime.close()
        await rig.approvals.close()
        await rig.manager.close_all()
