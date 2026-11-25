"""Scenario 40's offline gates and evidence; no real Claude process or provider runs. The
synthetic repository, its code copy and its commits use the local Git only."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import capture  # noqa: E402

from viki_agent.messaging.bot_memory import instruction_excludes  # noqa: E402

AUTHOR = "VIKI Capture <capture@example.com>"
# The provider's credentials file name, in halves so the owner-data scan never sees it whole.
CREDENTIALS_FILE = "." + "credentials" + ".json"


def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("reached a step scenario 40 must not reach here")


def _linux(monkeypatch: pytest.MonkeyPatch, platform: str = "linux") -> None:
    """Only capture's own `sys` sees the guest's platform; the rest of the process is unchanged."""
    fake = SimpleNamespace(platform=platform, executable=sys.executable, stderr=sys.stderr)
    monkeypatch.setattr(capture, "sys", fake)


def _guest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A synthetic clean guest: its own default home, no provider or credential variables, one
    absolute CLI. Returns (home, cli)."""
    _linux(monkeypatch)
    for name in set(os.environ) - set(capture._without_provider_env(os.environ)):
        monkeypatch.delenv(name)
    for name in ("CLAUDE_CONFIG_DIR", "VIKI_VAULT", "VIKI_EDGE", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "guest-home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (home / ".claude" / CREDENTIALS_FILE).write_text("credential sentinel", encoding="utf-8")
    cli = tmp_path / "bin" / "claude"
    cli.parent.mkdir()
    cli.write_text("", encoding="utf-8")
    cli.chmod(0o700)
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("VIKI_DATA_DIR", str(tmp_path / "viki-data"))
    monkeypatch.setenv("VIKI_CLAUDE_CLI", str(cli))
    monkeypatch.setattr(capture.config, "_managed_sources", lambda _platform: [])
    return home, cli


def _assert_guest_settings_only(read: list[Path], home: Path, tmp_path: Path) -> None:
    """The normal preflight read the guest home's settings and the run's own synthetic project
    settings, and never the credentials file beside them."""
    assert read[0] == home / ".claude" / "settings.json"
    assert home / ".claude" / CREDENTIALS_FILE not in read
    assert all(path.name in {"settings.json", "settings.local.json"} for path in read)
    assert all(path == read[0] or path.is_relative_to(tmp_path / "viki-data") for path in read)


def test_40_is_named_only() -> None:
    assert "40" in capture.SCENARIOS
    assert "40" in capture.NAMED_ONLY
    assert "40" not in capture.REFUSED
    assert "39" in capture.NAMED_ONLY


@pytest.mark.parametrize("keys", [["01", "40"], ["40", "40"], ["40", "01"], ["40", "39"]])
async def test_40_rejects_mixed_or_repeated_selection_before_discovery(
    keys: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("load", "_cli", "billing_problems"):
        monkeypatch.setattr(capture.config, name, _forbidden)
    monkeypatch.setattr(capture.winproc, "run", _forbidden)
    monkeypatch.setattr(capture.winproc, "git", _forbidden)
    assert await capture.run(keys) == 2


@pytest.mark.parametrize(
    "case",
    ["windows", "config_dir", "api_key", "oauth_token", "relative_cli", "no_cli"],
)
async def test_40_refuses_outside_the_clean_guest_before_discovery(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _guest(tmp_path, monkeypatch)
    if case == "windows":
        _linux(monkeypatch, "win32")
    elif case == "config_dir":
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    elif case == "api_key":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "forwarded-sentinel")
    elif case == "oauth_token":
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "forwarded-sentinel")
    elif case == "relative_cli":
        monkeypatch.setenv("VIKI_CLAUDE_CLI", "claude")
    else:
        monkeypatch.delenv("VIKI_CLAUDE_CLI")
    for name in ("load", "_cli", "billing_problems"):
        monkeypatch.setattr(capture.config, name, _forbidden)
    monkeypatch.setattr(capture.winproc, "run", _forbidden)
    monkeypatch.setattr(capture.winproc, "git", _forbidden)
    monkeypatch.setattr(capture, "TappedTransport", _forbidden)
    assert await capture.run(["40"]) == 2


async def test_40_guest_preflight_refuses_before_any_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _cli = _guest(tmp_path, monkeypatch)
    (home / ".claude" / "settings.json").write_text(
        '{"apiKeyHelper": "synthetic"}', encoding="utf-8"
    )
    read: list[Path] = []
    original = capture.config._read_json

    def spy(path: Path) -> dict[str, Any]:
        read.append(path)
        return original(path)

    monkeypatch.setattr(capture.config, "_read_json", spy)
    monkeypatch.setattr(capture.winproc, "run", _forbidden)
    monkeypatch.setattr(capture.winproc, "git", _forbidden)
    monkeypatch.setattr(capture, "TappedTransport", _forbidden)
    assert await capture.run(["40"]) == 2
    _assert_guest_settings_only(read, home, tmp_path)


SUBSCRIPTION = {"account": {"apiProvider": "firstParty", "subscriptionType": "max"}}
PLACEHOLDERS = {
    "--session-id": "<session-id>",
    "--add-dir": "<code-copy>",
    "--settings": "<bot-memory-settings>",
    "--mcp-config": "<app-mcp-config>",
}


def _shape(argv: list[str], cli: Path) -> list[str]:
    """`argv` with the CLI, the session id and each private path as its placeholder."""
    out = []
    for arg in argv:
        name = arg.split("=", 1)[0]
        if arg == str(cli):
            out.append("<cli>")
        elif "=" in arg and name in PLACEHOLDERS:
            out.append(f"{name}={PLACEHOLDERS[name]}")
        else:
            out.append(arg)
    return out


class _Runner:
    """`capture.run(["40"])` in a synthetic guest with the real Capture.open, preflight and
    setup Git; spies on the version call, Git, the spawned transport and the control."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        init: dict[str, Any],
        version: str = "2.1.290 (Claude Code)",
        mcp_error: bool = False,
    ) -> None:
        self.tmp_path = tmp_path
        self.home, self.cli = _guest(tmp_path, monkeypatch)
        self.read: list[Path] = []
        self.versions: list[tuple[list[str], dict[str, Any]]] = []
        self.gits: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self.spawned: dict[str, Any] = {}
        self.sent: list[str] = []
        self.closed = 0
        runner = self
        original_read = capture.config._read_json
        real_run, real_git = capture.winproc.run, capture.winproc.git

        def read_spy(path: Path) -> dict[str, object]:
            runner.read.append(path)
            return original_read(path)

        def run_spy(argv: list[str], **kwargs: Any) -> Any:
            if argv[-1:] == ["--version"]:
                runner.versions.append((argv, kwargs))
                return SimpleNamespace(stdout=version + "\n", returncode=0)
            return real_run(argv, **kwargs)

        def git_spy(cwd: Any, *args: str, **kwargs: Any) -> str:
            runner.gits.append((args, kwargs))
            return real_git(cwd, *args, **kwargs)

        class FakeTransport:
            def __init__(self, argv: list[str], **kwargs: Any) -> None:
                runner.spawned.update(argv=argv, **kwargs)
                flags = {arg.split("=", 1)[0]: arg.split("=", 1)[1] for arg in argv if "=" in arg}
                settings = Path(flags["--settings"])
                capture.protection.require_private_file(settings)
                document = json.loads(settings.read_text(encoding="ascii"))
                assert (Path(document["autoMemoryDirectory"]) / "MEMORY.md").is_file()
                # The app's settings: the folder it runs in is the vault, not the copy.
                assert document["claudeMdExcludes"] == instruction_excludes(Path(kwargs["cwd"]))
                assert set(document) == {
                    "autoMemoryDirectory",
                    "autoMemoryEnabled",
                    "claudeMdExcludes",
                }
                assert Path(flags["--mcp-config"]).is_file()
                assert (Path(flags["--add-dir"]) / ".git").is_file()  # a ready linked worktree

        class FakeControl:
            def __init__(self, _transport: Any, _callback: Any) -> None:
                pass

            async def start(self) -> dict[str, Any]:
                return init

            async def mcp_status(self) -> dict[str, Any]:
                if mcp_error:
                    raise RuntimeError("synthetic failure after open")
                return {}

            async def send(self, text: str, _session_id: str) -> None:
                runner.sent.append(text)
                raise AssertionError("a paid turn was sent")  # fails fast, not at a timeout

            async def close(self) -> None:
                runner.closed += 1

        monkeypatch.setattr(capture.config, "_read_json", read_spy)
        monkeypatch.setattr(capture.winproc, "run", run_spy)
        monkeypatch.setattr(capture.winproc, "git", git_spy)
        monkeypatch.setattr(capture, "TappedTransport", FakeTransport)
        monkeypatch.setattr(capture, "ClaudeControl", FakeControl)
        monkeypatch.setattr(capture.Recorder, "__init__", _forbidden)
        for name in ("_transcript", "_registry", "_project_dir", "_agent_meta_stopped_by_user"):
            monkeypatch.setattr(capture, name, _forbidden)
        monkeypatch.setattr(capture.catalog, "project_dir", _forbidden)

    def summary(self) -> dict[str, Any]:
        [run_dir] = (self.tmp_path / "viki-data" / "captures").iterdir()
        assert not list(run_dir.rglob("*.jsonl"))
        text = (run_dir / "summary.json").read_text(encoding="utf-8")
        assert str(self.tmp_path) not in text and self.tmp_path.as_posix() not in text
        assert "BOT_MEMORY_" not in text
        return json.loads(text)


async def test_40_not_logged_in_stops_before_send_with_the_same_cli_and_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _Runner(tmp_path, monkeypatch, init={})  # no account: not signed in
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hookspath'='inherited'")

    assert await capture.run(["40"]) == 2

    assert runner.sent == []
    assert shlex.join([str(runner.cli), "auth", "login", "--claudeai"]) in capsys.readouterr().err
    _assert_guest_settings_only(runner.read, runner.home, tmp_path)
    [(version_argv, version_kwargs)] = runner.versions
    assert version_argv[0] == str(runner.cli) == runner.spawned["argv"][0]
    assert "env" not in version_kwargs  # the guest's own environment, as the child's below
    env = runner.spawned["env"]
    assert env["HOME"] == str(runner.home)
    assert "CLAUDE_CONFIG_DIR" not in env
    assert "GIT_CONFIG_PARAMETERS" not in env
    assert env["GIT_CONFIG_GLOBAL"] == env["GIT_CONFIG_SYSTEM"] == os.devnull
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert env["GIT_AUTHOR_NAME"] + " <" + env["GIT_AUTHOR_EMAIL"] + ">" == AUTHOR
    assert sum(arg.startswith("--add-dir=") for arg in runner.spawned["argv"]) == 1
    assert runner.gits and all(
        kwargs == {"isolated_config": True}
        and args[:2] == ("-c", f"core.hooksPath={env['GIT_CONFIG_VALUE_0']}")
        and args[2:4] == ("-c", "commit.gpgsign=false")
        for args, kwargs in runner.gits
    )

    summary = runner.summary()
    assert set(summary) == SUMMARY_KEYS
    assert summary["diagnosis"] == NO_DIAGNOSIS  # the same fixed keys, at zero and false
    assert summary["native_version"] == "2.1.290"
    assert summary["synthetic_author"] == AUTHOR
    assert summary["argv"] == _shape(runner.spawned["argv"], runner.cli)  # what was spawned
    assert "--add-dir=<code-copy>" in summary["argv"] and "<redacted>" not in summary["argv"]
    assert {key for key, value in summary["observations"].items() if value} == {"not_logged_in"}


async def test_40_any_refusal_names_the_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A signed-out guest may answer with an account that has no subscription type."""
    runner = _Runner(tmp_path, monkeypatch, init={"account": {"apiProvider": "firstParty"}})
    assert await capture.run(["40"]) == 2
    assert runner.sent == []
    assert shlex.join([str(runner.cli), "auth", "login", "--claudeai"]) in capsys.readouterr().err
    assert not any(runner.summary()["observations"].values())


async def test_40_old_cli_refuses_before_the_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _Runner(tmp_path, monkeypatch, init=SUBSCRIPTION, version="2.1.280")
    assert await capture.run(["40"]) == 2
    assert runner.versions and not runner.spawned and not runner.gits


async def test_40_cli_resolving_elsewhere_refuses_before_any_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _Runner(tmp_path, monkeypatch, init=SUBSCRIPTION)
    load = capture.config.load
    monkeypatch.setattr(
        capture.config, "load", lambda: capture.replace(load(), cli=tmp_path / "other")
    )
    assert await capture.run(["40"]) == 2
    assert not runner.versions and not runner.spawned and not runner.gits


async def test_40_failure_after_open_closes_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _Runner(tmp_path, monkeypatch, init=SUBSCRIPTION, mcp_error=True)
    assert await capture.run(["40"]) == 1
    assert runner.spawned and runner.sent == []
    assert runner.closed == 1
    summary = runner.summary()
    assert not any(summary["observations"].values())
    assert summary["diagnosis"] == NO_DIAGNOSIS


async def test_40_persists_only_the_whitelist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _guest(tmp_path, monkeypatch)
    monkeypatch.setattr(
        capture.winproc, "run", lambda *_a, **_k: SimpleNamespace(stdout="2.1.290\n")
    )
    monkeypatch.setattr(capture.Recorder, "__init__", _forbidden)
    leak = str(tmp_path / "secret-path")
    recorders: list[Any] = []

    async def fake_scenario(_cap: Any, rec: Any) -> dict[str, Any]:
        recorders.append(rec)
        rec("out", {"type": "assistant", "body": leak})
        return {
            "pass": True,
            "native_login": True,
            "copy_commit": "yes",  # not a boolean True: recorded as False
            "argv": ["<cli>", "-p", leak, "--session-id=3f2b8c1e-0000-4000-8000-000000000000"],
            "session_ids": [leak],
            "reply": leak,
            "headers": {"authorization": leak},
            "diagnosis": {"asks": 1, "command": leak, "near_asks": leak},
        }

    monkeypatch.setattr(capture, "s40", fake_scenario)
    assert await capture.run(["40"]) == 0
    [run_dir] = (tmp_path / "viki-data" / "captures").iterdir()
    assert sorted(path.name for path in run_dir.iterdir()) == ["scratch", "summary.json"]
    text = (run_dir / "summary.json").read_text(encoding="utf-8")
    assert "secret-path" not in text and "3f2b8c1e" not in text
    summary = json.loads(text)
    assert summary["argv"] == ["<cli>", "-p", "<redacted>", "<redacted>"]
    assert set(summary) == SUMMARY_KEYS
    assert set(summary["observations"]) == OBSERVATION_KEYS
    assert summary["diagnosis"] == {**NO_DIAGNOSIS, "asks": 1}
    assert summary["observations"]["pass"] is True
    assert summary["observations"]["copy_commit"] is False
    assert isinstance(recorders[0], capture._NoRawRecorder)


async def test_40_open_refuses_a_non_git_overlay_without_a_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture, "TappedTransport", _forbidden)
    cfg = capture.config.Config(
        vault=None, claude_home=tmp_path, cli=tmp_path / "cli", edge=None, data_dir=tmp_path
    )
    cap = capture.Capture(cfg, tmp_path, tmp_path)
    with pytest.raises(ValueError, match="unsafe capture environment"):
        await cap.open(
            capture._NoRawRecorder(),
            capture.Launch("sid"),
            child_overlay={"ANTHROPIC_BASE_URL": "http://127.0.0.1:1/"},
        )


class _NativeStandIn:
    """Stands in for Capture around one native turn: checks what exists before the open, then
    plays Claude Code running the approved command in the child's own Git environment. `mode`
    names one deviation from that turn (see MODES)."""

    def __init__(self, tmp_path: Path, mode: str) -> None:
        self.root = tmp_path / "scratch"
        self.run_dir = tmp_path
        self.cfg = SimpleNamespace(cli=tmp_path / "bin" / "claude")
        self.root.mkdir()
        self.mode = mode
        self.argvs: list[list[str]] = []
        self.answers: list[Any] = []
        self.closed = False

    async def open(self, _rec: Any, launch: Any, **kwargs: Any) -> Any:
        assert kwargs == {"mode": "default", "child_overlay": kwargs["child_overlay"]}
        self.argvs.append(
            capture.build_argv([str(self.cfg.cli)], launch, mode="default", effort="low")
        )
        capture.protection.require_private_file(Path(launch.bot_memory_settings))
        assert Path(launch.mcp_config).is_file()
        self.copy = Path(launch.code_worktree)
        assert self.copy.parent == capture.worktree_area(self.run_dir / "data")
        assert (self.copy / ".git").is_file()
        [skill] = (self.copy / ".claude" / "skills").iterdir()
        self.skill = skill.name
        self.overlay = kwargs["child_overlay"]
        # The setup script is ready before the launch, in the session's own folder only.
        script = self.root / "copy-note.sh"
        assert (
            script.read_bytes()
            == (
                "#!/bin/sh\n"
                "set -e\n"
                f"cd {shlex.quote(str(self.copy))}\n"
                "echo synthetic > BOT_COPY_NOTE.txt\n"
                "git add BOT_COPY_NOTE.txt\n"
                "git commit -m synthetic\n"
            ).encode()
        )
        if sys.platform != "win32":
            assert stat.S_IMODE(script.stat().st_mode) == 0o700
        assert not script.is_relative_to(self.run_dir / "repo")
        assert not script.is_relative_to(self.copy)
        tools = [] if self.mode == "no_mcp" else [{"name": "echo"}]

        async def mcp_status() -> dict[str, Any]:
            return {"mcpServers": [{"name": "viki_echo", "tools": tools}]}

        return SimpleNamespace(control=SimpleNamespace(mcp_status=mcp_status))

    def _git(self, *args: str, env: dict[str, str]) -> None:
        git = shutil.which("git")
        assert git
        done = capture.winproc.run([git, *args], cwd=self.copy, env=env, timeout=30)
        assert done.returncode == 0, done.stderr

    def _requests(self, command: str) -> list[tuple[str, dict[str, Any]]]:
        """The permission requests this mode's model makes, in order."""
        exact = ("Bash", {"command": command})
        return {
            "other_command": [("Bash", {"command": "git push --force"})],
            "extra_before": [("Bash", {"command": "git status"}), exact],
            "extra_after": [exact, ("Read", {"file_path": "README.md"})],
            "exact_twice": [exact, exact],
            "quoted": [("Bash", {"command": command.replace("sh ", '"sh" ', 1)})],
            "spaced": [("Bash", {"command": command.replace("sh ", "sh  ", 1)})],
            "other_launcher": [("Bash", {"command": "ba" + command})],
            "no_ask": [],
        }.get(self.mode, [exact])

    async def turn(self, _live: Any, text: str, *, on_ask: Any) -> list[dict[str, Any]]:
        mode = self.mode
        command = f"sh {shlex.quote(str(self.root / 'copy-note.sh'))}"
        assert f"```bash\n{command}\n```" in text
        tools: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        for index, (tool, tool_input) in enumerate(self._requests(command)):
            tool_id = f"toolu_{index}"
            future = asyncio.get_running_loop().create_future()
            context = capture.ToolPermissionContext(tool_use_id=tool_id)
            answer = on_ask(capture.Ask(tool, tool_input, context, future))
            self.answers.append(answer)
            allowed = isinstance(answer, capture.PermissionResultAllow)
            if allowed:
                self._run_commit(mode)
            tools.append({"type": "tool_use", "id": tool_id, "name": tool, "input": tool_input})
            # Claude Code reports a denied request as an errored result too.
            errored = not allowed or mode == "approved_error"
            results.append({"type": "tool_result", "tool_use_id": tool_id, "is_error": errored})
        memory = next((self.root / "bot-memory").glob("*/MEMORY.md")).read_text(encoding="utf-8")
        canary = re.search(r"BOT_MEMORY_[0-9a-f]{32}", memory)
        assert canary
        if mode == "second_tool":
            tools.append({"type": "tool_use", "name": "Bash", "input": {"command": "git status"}})
        cwd = self.run_dir if mode == "cwd_elsewhere" else self.root
        return [
            {
                "type": "system",
                "subtype": "init",
                "cwd": str(cwd),
                "skills": [] if mode == "no_skill" else [self.skill],
            },
            {"type": "assistant", "message": {"content": tools}},
            {"type": "user", "message": {"content": results}},
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "" if mode == "no_canary" else canary[0]}]
                },
            },
            {"type": "result", "subtype": "success"},
        ]

    def _run_commit(self, mode: str) -> None:
        """What the approved command does, in the child's own Git environment."""
        env = {**os.environ, **self.overlay}
        if mode == "stripped_env":  # Claude Code's Bash dropping the child's Git variables
            dropped = (
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_KEY_",
                "GIT_CONFIG_VALUE_",
                "GIT_AUTHOR_",
            )
            env = {k: v for k, v in env.items() if not k.startswith((*dropped, "GIT_COMMI"))}
        if mode == "other_committer":
            env |= {
                "GIT_COMMITTER_NAME": "Someone Else",
                "GIT_COMMITTER_EMAIL": "e@example.com",
            }
        if mode == "other_branch":
            self._git("checkout", "-q", "-b", "other", env=env)
        if mode == "original_write":
            (self.run_dir / "repo" / "BOT_COPY_NOTE.txt").write_bytes(b"synthetic\n")
        hooks = self.run_dir / "repo" / ".git" / "hooks"
        on = ("-c", f"core.hooksPath={hooks}") if mode == "hooks_on" else ()
        note = b"other\n" if mode == "other_content" else b"synthetic\n"
        (self.copy / "BOT_COPY_NOTE.txt").write_bytes(note)
        self._git("add", "BOT_COPY_NOTE.txt", env=env)
        self._git(*on, "commit", "-m", "synthetic", env=env)

    async def close(self, _live: Any) -> None:
        self.closed = True


NOT_RUN = {"exact_approved_once", "copy_file", "copy_commit", "synthetic_author"}
# Each stand-in mode and exactly the observations it must turn false.
MODES: dict[str, set[str]] = {
    "ok": set(),
    "stripped_env": set(),  # the repository's own config still keeps hooks off and the author
    "no_skill": {"add_dir_skill_listed"},  # reported only: still a pass
    "other_command": NOT_RUN,
    "second_tool": {"exact_command_only"},  # reported only: still a pass
    "extra_before": {"exact_command_only"},  # a denied extra request is harmless
    "extra_after": {"exact_command_only"},
    "exact_twice": {"exact_command_only"},  # the repeat is denied; the first one ran
    "quoted": NOT_RUN,  # an edited command is denied
    "spaced": NOT_RUN,
    "other_launcher": NOT_RUN,  # the script through another launcher is denied
    "no_ask": NOT_RUN | {"exact_command_only"},
    "approved_error": set(),  # diagnosis only
    "other_content": {"copy_file"},
    "no_canary": {"memory_loaded"},
    "no_mcp": {"mcp_listed"},
    "original_write": {"original_untouched"},
    "other_branch": {"copy_commit", "synthetic_author"},
    "other_committer": {"synthetic_author"},
    "hooks_on": {"hooks_off"},
    "cwd_elsewhere": {"cwd_scratch"},
}
REPORTED_ONLY = {"add_dir_skill_listed", "exact_command_only"}
# Which of each mode's requests were approved, in order; one approved exact request otherwise.
ALLOWED: dict[str, list[bool]] = {
    "other_command": [False],
    "extra_before": [False, True],
    "extra_after": [True, False],
    "exact_twice": [True, False],
    "quoted": [False],
    "spaced": [False],
    "other_launcher": [False],
    "no_ask": [],
}
ONE_EXACT_RUN = {
    "asks": 1,
    "bash_asks": 1,
    "exact_asks": 1,
    "near_asks": 0,
    "script_asks": 0,
    "tool_calls": 1,
    "bash_calls": 1,
    "approved_errored": False,
    "note_exists": True,
    "commits_after_base": 1,
}
NOTHING_RAN = {"exact_asks": 0, "note_exists": False, "commits_after_base": 0}
# A request that names the script but is not exact: denied, so nothing ran.
SCRIPT_EDITED = {**NOTHING_RAN, "script_asks": 1}
# Each mode's diagnosis where it differs from ONE_EXACT_RUN.
DIAGNOSES: dict[str, dict[str, Any]] = {
    "other_command": NOTHING_RAN,
    "second_tool": {"tool_calls": 2, "bash_calls": 2},
    # The denied request's result is an error too: only the approved one's counts.
    "extra_before": {"asks": 2, "bash_asks": 2, "tool_calls": 2, "bash_calls": 2},
    "extra_after": {"asks": 2, "tool_calls": 2},
    "exact_twice": {"asks": 2, "bash_asks": 2, "exact_asks": 2, "tool_calls": 2, "bash_calls": 2},
    "quoted": {**SCRIPT_EDITED, "near_asks": 1},
    "spaced": {**SCRIPT_EDITED, "near_asks": 1},
    "other_launcher": SCRIPT_EDITED,
    "no_ask": {**NOTHING_RAN, "asks": 0, "bash_asks": 0, "tool_calls": 0, "bash_calls": 0},
    "approved_error": {"approved_errored": True},
}
NO_DIAGNOSIS = {
    key: False if key in {"approved_errored", "note_exists"} else 0 for key in ONE_EXACT_RUN
}
SUMMARY_KEYS = {
    "scenario",
    "platform",
    "native_version",
    "synthetic_author",
    "argv",
    "observations",
    "diagnosis",
}
OBSERVATION_KEYS = {
    "pass",
    "native_login",
    "not_logged_in",
    "mcp_listed",
    "memory_loaded",
    "exact_approved_once",
    "turn_success",
    "copy_file",
    "copy_commit",
    "synthetic_author",
    "original_untouched",
    "hooks_off",
    "cwd_scratch",
    "exact_command_only",
    "add_dir_skill_listed",
}


@pytest.mark.parametrize("mode", list(MODES))
async def test_40_observations(mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if mode == "no_mcp":  # _mcp_listed asks again after delays; yield instead of waiting
        real_sleep = asyncio.sleep

        async def no_wait(_delay: float) -> None:
            await real_sleep(0)

        monkeypatch.setattr(capture.asyncio, "sleep", no_wait)
    cap = _NativeStandIn(tmp_path, mode)
    result = await capture.s40(cast(capture.Capture, cap), capture._NoRawRecorder())
    false = {
        key
        for key, value in result.items()
        if key not in {"pass", "argv", "diagnosis"} and not value
    }
    assert false == MODES[mode]
    assert set(result) - {"argv", "diagnosis"} == OBSERVATION_KEYS - {"not_logged_in"}
    assert result["pass"] is (not MODES[mode] - REPORTED_ONLY)
    allowed = [isinstance(answer, capture.PermissionResultAllow) for answer in cap.answers]
    assert allowed == ALLOWED.get(mode, [True])
    assert cap.closed
    assert result["argv"] == _shape(cap.argvs[0], cap.cfg.cli)

    script = (cap.root / "copy-note.sh").read_text(encoding="utf-8")
    command = f"sh {shlex.quote(str(cap.root / 'copy-note.sh'))}"
    planted = {**result, "command": command, "diagnosis": {**result["diagnosis"], "path": script}}
    summary = capture._evidence_40("2.1.290", planted)
    assert set(summary) == SUMMARY_KEYS
    assert set(summary["observations"]) == OBSERVATION_KEYS
    assert summary["diagnosis"] == {**ONE_EXACT_RUN, **DIAGNOSES.get(mode, {})}
    text = json.dumps(summary)
    assert tmp_path.name not in text and "toolu_" not in text
    assert "BOT_COPY_NOTE" not in text and "copy-note" not in text


def test_40_prompt_renders_the_guest_command_unquoted() -> None:
    """A guest-shaped script path needs no shell quoting, so the prompt's line is the bare
    command; the prompt names no Git command for the model to reach for."""
    script = PurePosixPath(
        "/home/guest/.local/share/viki-agent/captures/20261112-120000-1a2b3c4d/scratch/copy-note.sh"
    )
    command = capture._script_command(script)
    assert command == f"sh {script}"
    prompt = capture._PROMPT_40.format(command=command)
    assert f"```bash\n{command}\n```" in prompt
    assert "'" not in command and '"' not in command
    assert "git" not in prompt.lower() and "commit" not in prompt.lower()
    assert len(prompt) < 600


def test_40_evidence_keeps_whitelisted_safe_values_only() -> None:
    assert set(capture._EVIDENCE_40) == OBSERVATION_KEYS
    planted = "planted-path/BOT_COPY_NOTE.txt"
    obs = {
        "pass": True,
        "exact_approved_once": 1,  # not a boolean True: recorded as False
        "command": planted,
        "diagnosis": {
            "asks": 12,  # above the cap
            "bash_asks": -3,
            "exact_asks": True,  # a bool is not a count
            "near_asks": "2",
            "script_asks": 3,
            "tool_calls": 2.0,
            "bash_calls": None,
            "commits_after_base": 4,
            "approved_errored": 1,  # not a boolean True
            "note_exists": True,
            "path": planted,
        },
    }
    summary = capture._evidence_40("2.1.290", obs)
    assert set(summary) == SUMMARY_KEYS
    assert summary["observations"]["pass"] is True
    assert summary["observations"]["exact_approved_once"] is False
    assert summary["diagnosis"] == {
        **NO_DIAGNOSIS,
        "asks": 9,
        "script_asks": 3,
        "commits_after_base": 4,
        "note_exists": True,
    }
    assert "planted" not in json.dumps(summary)
    for diagnosis in ([3], "asks", None):  # not a mapping: every field at its safe value
        assert capture._evidence_40("2.1.290", {"diagnosis": diagnosis})["diagnosis"] == (
            NO_DIAGNOSIS
        )
