"""Update checks relayed through the server: the `updates.check` setting, `WindowHost`'s
`ask_update_check`, the automatic check loop, the window's `updates.check` message and the
`updates` capability, over the os.pipe link of tests/test_window_host.py."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from tests import test_window_host
from tests.test_server import BASE, BEARER, TOKEN, EmptyCatalog, authed, running
from tests.test_window_host import HANG_GUARD_S, OPEN_1, URL, Link, _open
from viki_agent import server
from viki_agent.app.diagnostics import ProviderChecks, Readiness
from viki_agent.app.settings import KEYS, SettingsService
from viki_agent.config import Config
from viki_agent.drivers.claude.driver import DriverConfig
from viki_agent.session import SessionManager
from viki_agent.state import AppState, Prefs
from viki_agent.window_host import UPDATE_EVERY_S, UPDATE_FIRST_S, WindowHost, run_update_checks

link = test_window_host.link  # the WindowHost over two os.pipe pairs

UI = "0" * 32  # URL's ui nonce
OTHER_UI = "1" * 32
ASK_1_MANUAL = b'{"type":"update_check","id":1,"manual":true}\n'
ASK_1_AUTO = b'{"type":"update_check","id":1,"manual":false}\n'
ASK_2_AUTO = b'{"type":"update_check","id":2,"manual":false}\n'


def _app(
    tmp_path: Path,
    host: WindowHost | None,
    *,
    update_check_s: tuple[float, float] = (UPDATE_FIRST_S, UPDATE_EVERY_S),
):
    """create_app with settings and the capabilities route, and `host` as its window host."""
    vault, home, data = tmp_path / "vault", tmp_path / "home", tmp_path / "data"
    vault.mkdir(parents=True)
    home.mkdir()
    config = Config(vault=vault, claude_home=home, cli=Path("fake"), edge=None, data_dir=data)
    state = AppState(data / "state.json", Prefs())
    readiness = Readiness(config=config, launch_id="launch-1", git_available=lambda: False)
    return server.create_app(
        config=config,
        token=TOKEN,
        manager=SessionManager(
            config=DriverConfig(cli=(), vault=vault, env={}),
            state=state,
            claude_home=home,
            make_driver=lambda *_: pytest.fail("no test here starts a provider"),
        ),
        catalog=EmptyCatalog(home, vault),
        static_dir=None,
        settings=SettingsService(
            state.store, provider_defaults=Prefs, on_chat_defaults=lambda _prefs: None
        ),
        readiness=readiness,
        checks=ProviderChecks(readiness=readiness, cli_version=lambda: "", git_version=lambda: ""),
        diagnostics_document=lambda: {},
        window_host=host,
        update_check_s=update_check_s,
    )


def test_the_updates_check_key_defaults_on_and_applies_now(tmp_path: Path) -> None:
    spec = KEYS["updates.check"]
    assert (spec.kind, spec.allowed, spec.default) == ("bool", None, True)
    assert (spec.applies, spec.personal) == ("now", False)
    state = AppState(tmp_path / "state.json", Prefs())
    service = SettingsService(state.store, provider_defaults=Prefs, on_chat_defaults=lambda _: None)
    assert service.flag("updates.check") is True
    assert (UPDATE_FIRST_S, UPDATE_EVERY_S) == (60.0, 21600.0)
    default = inspect.signature(server.create_app).parameters["update_check_s"].default
    assert default == (UPDATE_FIRST_S, UPDATE_EVERY_S)


async def test_ask_update_check_writes_one_line_to_a_live_window_only(link: Link) -> None:
    assert not link.host.ask_update_check(None, manual=False)  # no window yet
    await _open(link.host)
    await _open(link.host, URL.replace(UI, OTHER_UI))
    assert link.line() == OPEN_1
    link.line()
    assert link.host.ask_update_check(None, manual=False)  # the newest window
    assert link.line() == ASK_2_AUTO
    assert link.host.ask_update_check(UI, manual=True)
    assert link.line() == ASK_1_MANUAL
    for ui in ("2" * 32, "", UI[:-1], "A" * 32):
        assert not link.host.ask_update_check(ui, manual=True)
    link.host._closed(2)
    assert not link.host.ask_update_check(OTHER_UI, manual=True)
    assert link.host.ask_update_check(None, manual=False)
    assert link.line() == ASK_1_AUTO  # and none of the refused asks wrote a line before it
    link.sent.close()  # the app reads no more
    assert not link.host.ask_update_check(UI, manual=True)
    assert not link.host.ask_update_check(None, manual=False)


async def test_ask_update_check_is_false_once_the_app_is_gone(link: Link) -> None:
    await _open(link.host)
    assert link.line() == OPEN_1
    link.app.close()
    await asyncio.wait_for(link.ended.wait(), HANG_GUARD_S)
    assert not link.host.ask_update_check(UI, manual=True)
    assert not link.host.ask_update_check(None, manual=False)
    link.host._writer.close()
    assert link.sent.read() == b""


async def test_the_loop_asks_the_newest_window_only_when_the_flag_is_true(
    link: Link, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="viki_agent.window_host")
    await _open(link.host)
    assert link.line() == OPEN_1
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    answers: list[bool | None | Exception] = [True, None, False, RuntimeError(URL), True]

    def allowed() -> bool | None:
        if not answers:
            loop.call_soon_threadsafe(done.set)
            return None
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    task = asyncio.create_task(run_update_checks(link.host, allowed, first_s=0.01, every_s=0.01))
    await asyncio.wait_for(done.wait(), HANG_GUARD_S)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert link.host.ask_update_check(None, manual=True)  # the line after the loop's own
    assert [link.line() for _ in range(3)] == [ASK_1_AUTO, ASK_1_AUTO, ASK_1_MANUAL]
    records = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert records == [(logging.WARNING, "update check unavailable")]
    assert "token" not in caplog.text


def test_the_app_runs_the_loop_with_a_host_and_settings(tmp_path: Path, link: Link) -> None:
    asyncio.run(_open(link.host))
    assert link.line() == OPEN_1
    with TestClient(_app(tmp_path, link.host, update_check_s=(0.0, HANG_GUARD_S)), base_url=BASE):
        assert link.line() == ASK_1_AUTO


def test_a_window_updates_check_reaches_the_host_and_a_bad_ui_is_dropped(
    tmp_path: Path, link: Link, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="viki_agent.server")
    malformed = [
        *({"type": "updates.check", "ui": ui} for ui in (UI[:-1], UI + "0", "A" * 32, "g" * 32)),
        *({"type": "updates.check", "ui": ui} for ui in (5, None, [UI])),
        {"type": "updates.check"},
        {"type": "updates.check", "ui": UI, "manual": False},
    ]
    with TestClient(_app(tmp_path, link.host), base_url=BASE) as client, authed(client) as ws:
        assert client.portal is not None
        client.portal.call(_open, link.host)
        assert link.line() == OPEN_1
        for message in malformed:
            ws.send_json(message)
        ws.send_json({"type": "updates.check", "ui": OTHER_UI})  # well formed, no such window
        ws.send_json({"type": "updates.check", "ui": UI})
        assert link.line() == ASK_1_MANUAL
        # A line any earlier message wrote would come before this one.
        client.portal.call(functools.partial(link.host.ask_update_check, None, manual=False))
        assert link.line() == ASK_1_AUTO
    dropped = [r.getMessage() for r in caplog.records if r.name == "viki_agent.server"]
    assert dropped == ["dropped invalid client message: type=updates.check"] * len(malformed)


def test_updates_check_without_a_host_does_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="viki_agent.server")
    with running(tmp_path) as app, authed(app.client) as ws:
        ws.send_json({"type": "updates.check", "ui": UI})
        ws.send_json({"type": "open.url", "url": "https://good.example"})  # barrier
        assert app.opened.get(timeout=3) == "https://good.example"
    assert "socket read failed" not in caplog.text


def test_features_name_updates_only_with_a_host(tmp_path: Path, link: Link) -> None:
    with TestClient(_app(tmp_path / "hosted", link.host), base_url=BASE) as client:
        hosted = client.get("/api/v2/capabilities", headers=BEARER).json()["features"]
    with TestClient(_app(tmp_path / "bare", None), base_url=BASE) as client:
        bare = client.get("/api/v2/capabilities", headers=BEARER).json()["features"]
    assert (hosted, bare) == ({"updates": True}, {})
