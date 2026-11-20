"""voice_worker: the isolated process, its guard, its start checks and the engine adapter.

In-process tests drive `main` with a fake `load` and a fake `gpu`; process tests run the real
file. No test loads a real model.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import io
import json
import math
import os
import struct
import sys
import types
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

from viki_agent import winproc
from viki_agent.voice import voice_gpu, voice_models, voice_worker
from viki_agent.voice.voice_gpu import DEVICE_GPU, Device
from viki_agent.voice.voice_stream import FRAME_SAMPLES, Recognizer

HANG_GUARD_S = 10
CAPTURE = "123e4567-e89b-42d3-a456-426614174000"
WORKER = Path(voice_worker.__file__)
SRC = WORKER.parents[2]
PIN_SIZE = 8
BINDING = voice_models.BACKENDS["cuda"].binding
THREADS = min(8, os.cpu_count() or 1)
FREE = 10_000
FRAME_BYTES = FRAME_SAMPLES * 2


# ---- fixtures and fakes ----------------------------------------------------------------------


def _small(pin: voice_models.PinnedFile) -> voice_models.PinnedFile:
    return dataclasses.replace(pin, size=PIN_SIZE)


@pytest.fixture
def voice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `<data>/voice` cwd holding every pinned file, with the pins shrunk to PIN_SIZE bytes."""
    monkeypatch.setattr(voice_models, "VAD", _small(voice_models.VAD))
    for model_id, model in list(voice_models.MODELS.items()):
        small = dataclasses.replace(model, weights=_small(model.weights))
        monkeypatch.setitem(voice_models.MODELS, model_id, small)
    cuda = voice_models.BACKENDS["cuda"]
    small_cuda = dataclasses.replace(cuda, files=tuple(_small(pin) for pin in cuda.files))
    monkeypatch.setitem(voice_models.BACKENDS, "cuda", small_cuda)
    folders = [(tmp_path / "models" / m, voice_models.model_files(m)) for m in voice_models.MODELS]
    folders.append((tmp_path / "backends" / "cuda", small_cuda.files))
    for folder, pins in folders:
        folder.mkdir(parents=True)
        for pin in pins:
            (folder / pin.name).write_bytes(bytes(PIN_SIZE))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def on_platform(monkeypatch: pytest.MonkeyPatch, name: str, machine: str) -> None:
    monkeypatch.setattr(sys, "platform", name)
    monkeypatch.setattr(voice_worker.platform, "machine", lambda: machine)


class Echo:
    def __init__(self, text: str = "hello", error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    def transcribe(self, pcm: bytes, language: str, *, partial: bool) -> str:
        if self.error is not None:
            raise self.error
        return self.text


@dataclasses.dataclass
class FakeLoad:
    recognizer: Recognizer = dataclasses.field(default_factory=Echo)
    error: Exception | None = None
    calls: list[tuple[Path, Path, int, bool, bool]] = dataclasses.field(default_factory=list)
    order: list[str] | None = None

    def __call__(
        self,
        weights: Path,
        vad: Path,
        threads: int,
        use_gpu: bool,
        proportional_partials: bool,
        check_binding: Callable[[], None],
    ) -> Recognizer:
        self.calls.append((weights, vad, threads, use_gpu, proportional_partials))
        check_binding()
        if self.order is not None:
            self.order.append("load")
        if self.error is not None:
            raise self.error
        return self.recognizer


WHEEL_SPEC = importlib.util.find_spec("_pywhispercpp")
assert WHEEL_SPEC is not None and WHEEL_SPEC.origin is not None
WHEEL_DIR = Path(WHEEL_SPEC.origin).parent


@dataclasses.dataclass
class FakeGpu:
    """The cuda calls, answered from fields; `frees` holds each devices() call's free memory."""

    voice: Path
    preload_fails: bool = False
    name: str = "CUDA0"
    frees: list[int] = dataclasses.field(default_factory=lambda: [FREE, FREE - PIN_SIZE // 2])
    bound_elsewhere: bool = False
    wheel_loaded: bool = False
    order: list[str] = dataclasses.field(default_factory=list)

    def preload(self, path: Path) -> None:
        assert path == self.voice / "backends" / "cuda" / BINDING
        self.order.append("preload")
        if self.preload_fails:
            raise OSError("fake")

    def devices(self, ggml: Path, ggml_base: Path) -> list[Device]:
        backend = self.voice / "backends" / "cuda"
        assert (ggml, ggml_base) == (backend / "ggml.dll", backend / "ggml-base.dll")
        self.order.append("devices")
        cpu = Device(0, 0, "CPU", "Fake CPU", 0, 0)
        return [cpu, Device(1, DEVICE_GPU, self.name, "Fake GPU", self.frees.pop(0), 2 * FREE)]

    def first_gpu(self, devices: Iterable[Device], prefix: str) -> Device | None:
        return voice_gpu.first_gpu(devices, prefix)

    def module_file(self, name: str) -> str | None:
        folder = WHEEL_DIR if self.bound_elsewhere else self.voice / "backends" / "cuda"
        return str(folder / name)

    def is_loaded(self, path: Path) -> bool:
        return self.wheel_loaded and path == WHEEL_DIR / BINDING


def frame(kind: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">cI", kind, len(payload)) + payload


def begin(language: str = "en", pause_frames: int | None = None) -> bytes:
    body: dict[str, object] = {"capture": CAPTURE, "language": language}
    if pause_frames is not None:
        body["pause_frames"] = pause_frames
    return frame(b"B", json.dumps(body).encode("ascii"))


def audio(pcm: bytes) -> bytes:
    return b"".join(frame(b"A", pcm[i : i + 32_000]) for i in range(0, len(pcm), 32_000))


def tone(duration_s: float) -> bytes:
    peak = 32768 * 10 ** (-20 / 20) * math.sqrt(2)  # RMS at -20 dBFS
    count = round(duration_s * 16_000)
    return struct.pack(
        f"<{count}h",
        *(round(peak * math.sin(2 * math.pi * 300 * i / 16_000)) for i in range(count)),
    )


def run_main(
    argv: list[str], frames: bytes = b"", **kwargs: Any
) -> tuple[int, list[dict[str, Any]]]:
    protocol = io.BytesIO()
    kwargs.setdefault("load", FakeLoad())
    code = voice_worker.main(argv, stdin=io.BytesIO(frames), protocol=protocol, **kwargs)
    return code, [json.loads(line) for line in protocol.getvalue().decode("ascii").splitlines()]


def ready(model: str, backend: str, device: str | None) -> dict[str, Any]:
    return {
        "type": "ready",
        "engine": "pywhispercpp",
        "engine_version": voice_models.ENGINE_VERSION,
        "model": model,
        "backend": backend,
        "device": device,
    }


def error(code: str, detail: str | None = None) -> dict[str, Any]:
    return {"type": "error", "code": code} | ({} if detail is None else {"detail": detail})


# ---- main in process ---------------------------------------------------------------------------


def test_ready_first_then_partial_final_and_done(voice: Path) -> None:
    load = FakeLoad()
    pcm = bytes(12_800) + tone(2.0)

    code, lines = run_main(["small-q8_0", "cpu"], begin() + audio(pcm) + frame(b"E"), load=load)

    assert code == 0
    assert lines == [
        ready("small-q8_0", "cpu", None),
        {"type": "partial", "capture": CAPTURE, "segment": 0, "revision": 1, "text": "hello"},
        {"type": "final", "capture": CAPTURE, "segment": 0, "text": "hello"},
        {"type": "done", "capture": CAPTURE},
    ]
    folder = voice / "models" / "small-q8_0"
    vad = voice_models.VAD.name
    assert load.calls == [(folder / "ggml-small-q8_0.bin", folder / vad, THREADS, False, True)]


def test_cancel_sends_cancelled(voice: Path) -> None:
    code, lines = run_main(["large-v3-turbo-q8_0", "cpu"], begin("ja") + frame(b"X"))

    assert code == 0
    assert lines[1:] == [{"type": "cancelled", "capture": CAPTURE}]


def test_eof_mid_capture_exits_0_with_no_further_line(voice: Path) -> None:
    code, lines = run_main(["small-q8_0", "cpu"], begin() + audio(bytes(32_000)))

    assert code == 0
    assert lines == [ready("small-q8_0", "cpu", None)]


def test_a_begin_with_pause_frames_reaches_the_session(voice: Path) -> None:
    speech = tone(1.0)
    short_pause = bytes(90 * FRAME_BYTES)  # over the 60-frame default, short of pause_frames=100
    long_pause = bytes(105 * FRAME_BYTES)  # clears pause_frames=100

    short_code, short_lines = run_main(
        ["small-q8_0", "cpu"], begin(pause_frames=100) + audio(speech + short_pause)
    )
    long_code, long_lines = run_main(
        ["small-q8_0", "cpu"], begin(pause_frames=100) + audio(speech + long_pause)
    )

    assert short_code == 0
    assert long_code == 0
    assert "final" not in [line["type"] for line in short_lines]
    assert "final" in [line["type"] for line in long_lines]


@pytest.mark.parametrize(
    "frames",
    [
        frame(b"Z"),
        audio(bytes(640)),  # audio with no capture open
        begin() + begin(),
        begin("xx"),  # a language the model does not list
        frame(b"A", bytes(3)),
        b"B\x00",  # a short header
        begin() + audio(bytes(3_872_000)),  # past MAX_CAPTURE_S
    ],
    ids=["kind", "no-capture", "two-begins", "language", "odd-audio", "short", "capture-cap"],
)
def test_a_bad_frame_is_invalid_input(voice: Path, frames: bytes) -> None:
    code, lines = run_main(["small-q8_0", "cpu"], frames)

    assert code == 2
    assert lines[0]["type"] == "ready"
    assert lines[-1] == error("invalid_input")


@pytest.mark.parametrize(
    ("raised", "code", "exit_code"),
    [
        (ImportError("no engine"), "engine_unavailable", 3),
        (RuntimeError("bad model"), "model_unavailable", 4),
    ],
)
def test_a_load_failure_ends_before_ready(
    voice: Path, raised: Exception, code: str, exit_code: int
) -> None:
    result = run_main(["small-q8_0", "cpu"], begin(), load=FakeLoad(error=raised))

    assert result == (exit_code, [error(code)])


def test_a_recognizer_failure_is_decode_failed(voice: Path) -> None:
    load = FakeLoad(recognizer=Echo(error=RuntimeError("decode")))

    code, lines = run_main(["small-q8_0", "cpu"], begin() + audio(tone(1.0)), load=load)

    assert code == 1
    assert lines[-1] == error("decode_failed")


@pytest.mark.parametrize(
    "argv",
    [
        ["no-such-model", "cpu"],
        ["small-q8_0", "vulkan"],
        ["small-q8_0"],
        ["small-q8_0", "cpu", "x"],
    ],
)
def test_bad_argv_is_invalid_input(voice: Path, argv: list[str]) -> None:
    load = FakeLoad()

    assert run_main(argv, load=load) == (2, [error("invalid_input")])
    assert load.calls == []


@pytest.mark.parametrize("backend", ["cuda", "metal"])
def test_a_backend_not_offered_here_is_invalid_input(
    voice: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    on_platform(monkeypatch, "linux", "x86_64")
    gpu = FakeGpu(voice)

    assert run_main(["small-q8_0", backend], gpu=gpu) == (2, [error("invalid_input")])
    assert gpu.order == []


@pytest.mark.parametrize(
    ("backend", "missing"),
    [
        ("cpu", "models/small-q8_0/ggml-small-q8_0.bin"),
        ("cpu", f"models/small-q8_0/{voice_models.VAD.name}"),
        ("cuda", "backends/cuda/cublasLt64_12.dll"),
    ],
)
def test_a_missing_file_is_invalid_input(
    voice: Path, monkeypatch: pytest.MonkeyPatch, backend: str, missing: str
) -> None:
    on_platform(monkeypatch, "win32", "AMD64")
    (voice / missing).unlink()
    gpu = FakeGpu(voice)

    assert run_main(["small-q8_0", backend], gpu=gpu) == (2, [error("invalid_input")])
    assert gpu.order == []


def test_metal_loads_on_the_gpu_with_no_device_named(
    voice: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, "darwin", "arm64")
    load = FakeLoad()

    code, lines = run_main(["large-v3-turbo-q5_0", "metal"], load=load)

    assert (code, lines) == (0, [ready("large-v3-turbo-q5_0", "metal", None)])
    assert load.calls[0][3:] == (True, False)


def test_cuda_checks_the_device_then_names_it(voice: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    on_platform(monkeypatch, "win32", "AMD64")
    gpu = FakeGpu(voice)
    load = FakeLoad(order=gpu.order)

    code, lines = run_main(["small-q8_0", "cuda"], load=load, gpu=gpu)

    assert (code, lines) == (0, [ready("small-q8_0", "cuda", "Fake GPU")])
    assert gpu.order == ["preload", "devices", "load", "devices"]
    assert load.calls[0][3] is True


class Tracer(io.BytesIO):
    """A protocol channel that notes each write in `order`, beside the recognizer's calls."""

    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self.order = order

    def write(self, data: Any, /) -> int:
        self.order.append("write")
        return super().write(data)


class Warm:
    """A recognizer that notes its calls in `order` and may fail them."""

    def __init__(self, order: list[str], error: Exception | None = None) -> None:
        self.order = order
        self.error = error
        self.calls: list[tuple[bytes, str, bool]] = []

    def transcribe(self, pcm: bytes, language: str, *, partial: bool) -> str:
        self.order.append("transcribe")
        self.calls.append((pcm, language, partial))
        if self.error is not None:
            raise self.error
        return "discarded"


def run_warm(
    argv: list[str], recognizer: Warm, order: list[str], **kwargs: Any
) -> tuple[int, list[dict[str, Any]]]:
    protocol = Tracer(order)
    code = voice_worker.main(
        argv,
        stdin=io.BytesIO(),
        protocol=protocol,
        load=FakeLoad(recognizer=recognizer),
        **kwargs,
    )
    return code, [json.loads(line) for line in protocol.getvalue().decode("ascii").splitlines()]


@pytest.mark.parametrize(
    ("backend", "platform_", "machine", "model"),
    [
        ("cuda", "win32", "AMD64", "small-q8_0"),
        ("metal", "darwin", "arm64", "large-v3-turbo-q5_0"),
    ],
)
def test_a_gpu_worker_warms_up_with_one_partial_decode_before_ready(
    voice: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    platform_: str,
    machine: str,
    model: str,
) -> None:
    on_platform(monkeypatch, platform_, machine)
    order: list[str] = []
    recognizer = Warm(order)
    gpu = FakeGpu(voice, order=order) if backend == "cuda" else voice_gpu

    code, lines = run_warm([model, backend], recognizer, order, gpu=gpu)

    assert code == 0
    assert [line["type"] for line in lines] == ["ready"]
    # 1.1 s of silence as a partial: no VAD runs, and the first kernel compile lands here.
    assert recognizer.calls == [(bytes(35_200), "en", True)]
    assert order[-2:] == ["transcribe", "write"]


def test_a_cpu_worker_does_not_warm_up(voice: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    on_platform(monkeypatch, "win32", "AMD64")
    order: list[str] = []
    recognizer = Warm(order)

    code, lines = run_warm(["small-q8_0", "cpu"], recognizer, order)

    assert (code, [line["type"] for line in lines]) == (0, ["ready"])
    assert recognizer.calls == []
    assert order == ["write"]


def test_a_cuda_warm_up_failure_is_model_unavailable_with_no_ready(
    voice: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_platform(monkeypatch, "win32", "AMD64")
    order: list[str] = []
    recognizer = Warm(order, error=RuntimeError("kernel compile"))

    result = run_warm(["small-q8_0", "cuda"], recognizer, order, gpu=FakeGpu(voice, order=order))

    assert result == (4, [error("model_unavailable")])
    assert len(recognizer.calls) == 1


@pytest.mark.parametrize(
    ("change", "detail", "loads"),
    [
        ({"preload_fails": True}, "load_failed", False),
        ({"name": "Vulkan0"}, "no_device", False),
        ({"frees": [FREE, FREE - PIN_SIZE // 2 + 1]}, "not_on_device", True),
    ],
)
def test_a_cuda_start_check_failure_is_backend_unavailable(
    voice: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any], detail: str, loads: bool
) -> None:
    on_platform(monkeypatch, "win32", "AMD64")
    load = FakeLoad()

    result = run_main(["small-q8_0", "cuda"], load=load, gpu=FakeGpu(voice, **change))

    assert result == (5, [error("backend_unavailable", detail)])
    assert bool(load.calls) is loads


@pytest.mark.parametrize("change", [{"bound_elsewhere": True}, {"wheel_loaded": True}])
def test_binding_elsewhere_is_refused(
    voice: Path, monkeypatch: pytest.MonkeyPatch, change: dict[str, Any]
) -> None:
    on_platform(monkeypatch, "win32", "AMD64")

    result = run_main(["small-q8_0", "cuda"], gpu=FakeGpu(voice, **change))

    assert result == (5, [error("backend_unavailable", "binding_mismatch")])


# ---- the engine adapter ----------------------------------------------------------------------


@dataclasses.dataclass
class EngineRecord:
    order: list[str] = dataclasses.field(default_factory=list)
    constructed: list[tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=list)
    calls: list[dict[str, Any]] = dataclasses.field(default_factory=list)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> EngineRecord:
    """A fake `pywhispercpp.model` that records construction and every transcribe call."""
    record = EngineRecord()

    class FakeModel:
        def __init__(self, model: str, **kwargs: Any) -> None:
            record.order.append("Model")
            record.constructed.append((model, kwargs))

        def transcribe(self, media: Any, **params: Any) -> list[types.SimpleNamespace]:
            sample = {"dtype": media.dtype.name, "size": media.size, "first": float(media[0])}
            record.calls.append(sample | {"last": float(media[-1])} | params)
            return [types.SimpleNamespace(text=" hel"), types.SimpleNamespace(text="lo ")]

    package = types.ModuleType("pywhispercpp")
    module = types.ModuleType("pywhispercpp.model")
    module.Model = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pywhispercpp", package)
    monkeypatch.setitem(sys.modules, "pywhispercpp.model", module)
    return record


def test_load_recognizer_constructs_the_measured_settings(engine: EngineRecord) -> None:
    def check() -> None:
        engine.order.append("check_binding")

    voice_worker._load_recognizer(Path("w.bin"), Path("vad.bin"), 6, True, False, check)

    assert engine.order == ["check_binding", "Model"]
    assert engine.constructed == [
        (
            "w.bin",
            {
                "redirect_whispercpp_logs_to": False,
                "context_params": {"use_gpu": True},
                "n_threads": 6,
                "print_progress": False,
                "print_realtime": False,
                "print_timestamps": False,
                "no_context": True,
                "suppress_nst": True,
                "no_timestamps": True,
                "vad": True,
                "vad_model_path": "vad.bin",
            },
        )
    ]


def test_a_binding_mismatch_stops_before_the_model(engine: EngineRecord) -> None:
    def check() -> None:
        raise voice_worker._Failure("backend_unavailable", 5, "binding_mismatch")

    with pytest.raises(voice_worker._Failure):
        voice_worker._load_recognizer(Path("w"), Path("v"), 1, True, False, check)

    assert engine.order == []


@pytest.mark.parametrize(
    ("proportional", "partial", "samples", "size", "audio_ctx", "max_tokens"),
    [
        (True, True, 8_000, 17_600, 55 + 64, 24),  # padded to 1.1 s
        (True, False, 8_000, 17_600, 0, 24),
        (True, True, 32_000, 32_000, 100 + 64, 34),
        (True, False, 32_000, 32_000, 0, 34),
        (False, True, 32_000, 32_000, 0, 34),
        (False, False, 32_000, 32_000, 0, 34),
        (True, True, 320_000, 320_000, 1000 + 64, 250),  # 20 s, the segment cap
        (True, True, 480_000, 480_000, 1500, 370),  # past the full context
        (True, True, 16_000, 16_000, 50 + 64, 22),  # exactly 1.0 s: no pad
    ],
)
def test_the_adapter_sets_the_decode_parameters_on_every_call(
    engine: EngineRecord,
    proportional: bool,
    partial: bool,
    samples: int,
    size: int,
    audio_ctx: int,
    max_tokens: int,
) -> None:
    recognizer = voice_worker._load_recognizer(
        Path("w"), Path("v"), 1, False, proportional, lambda: None
    )
    pcm = struct.pack("<h", 16_384) + bytes(2 * samples - 2)

    texts = [recognizer.transcribe(pcm, language, partial=partial) for language in ("ja", "auto")]

    assert texts == ["hello", "hello"]
    expected = {"dtype": "float32", "size": size, "first": 0.5, "last": 0.0}
    # Partials decode without the VAD, which empties the first short windows; finals keep it.
    params = {"audio_ctx": audio_ctx, "max_tokens": max_tokens, "vad": not partial}
    assert engine.calls == [expected | params | {"language": lang} for lang in ("ja", "auto")]


# ---- the real process --------------------------------------------------------------------------


GUARDED = f"""
import os, socket, subprocess, sys
sys.path.insert(0, {str(SRC)!r})
from viki_agent.voice import voice_worker
voice_worker._install_guard()
def attempt(name, action):
    try:
        action()
    except PermissionError:
        print(name, "refused")
    else:
        print(name, "allowed")
"""


def run_guarded(cwd: Path, body: str) -> list[str]:
    result = winproc.run(
        [sys.executable, "-I", "-B", "-c", GUARDED + body], cwd=cwd, timeout=HANG_GUARD_S
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def test_guard_refuses_sockets(tmp_path: Path) -> None:
    body = """
attempt("socket", socket.socket)
attempt("getaddrinfo", lambda: socket.getaddrinfo("127.0.0.1", 0))
"""
    assert run_guarded(tmp_path, body) == ["socket refused", "getaddrinfo refused"]


def test_guard_refuses_writes_and_processes(tmp_path: Path) -> None:
    (tmp_path / "r").write_bytes(b"data")
    body = """
attempt("open-w", lambda: open("x", "w"))
attempt("open-a", lambda: open("x", "ab"))
attempt("open-plus", lambda: open("r", "r+b"))
attempt("os-open", lambda: os.open("x", os.O_WRONLY | os.O_CREAT))
attempt("system", lambda: os.system("echo"))
attempt("popen", lambda: subprocess.run([sys.executable, "-c", "0"]))
attempt("remove", lambda: os.remove("r"))
attempt("rename", lambda: os.rename("r", "y"))
attempt("mkdir", lambda: os.mkdir("d"))
"""
    lines = run_guarded(tmp_path, body)

    names = ["open-w", "open-a", "open-plus", "os-open", "system", "popen", "remove", "rename"]
    assert lines == [f"{name} refused" for name in [*names, "mkdir"]]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["r"]
    assert (tmp_path / "r").read_bytes() == b"data"


def test_guard_refuses_links_attributes_and_sqlite(tmp_path: Path) -> None:
    (tmp_path / "r").write_bytes(b"data")
    body = """
import sqlite3
attempt("link", lambda: os.link("r", "l"))
attempt("symlink", lambda: os.symlink("r", "s"))
attempt("chmod", lambda: os.chmod("r", 0o600))
attempt("utime", lambda: os.utime("r", (0, 0)))
attempt("sqlite", lambda: sqlite3.connect("x.db"))
"""
    lines = run_guarded(tmp_path, body)

    names = ["link", "symlink", "chmod", "utime", "sqlite"]
    assert lines == [f"{name} refused" for name in names]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["r"]


@pytest.mark.skipif(sys.platform != "win32", reason="_winapi exists on Windows only")
def test_guard_refuses_winapi_process_creation(tmp_path: Path) -> None:
    body = """
import _winapi
attempt(
    "create-process",
    lambda: _winapi.CreateProcess(None, "cmd /c exit", None, None, False, 0, None, None, None),
)
"""
    assert run_guarded(tmp_path, body) == ["create-process refused"]


@pytest.mark.parametrize(
    "event",
    [
        "_winapi.CreateFile",
        "os.chown",
        "_winapi.CreateJunction",
        "os.chflags",
        "os.setxattr",
        "os.removexattr",
        "winreg.CreateKey",
        "winreg.SetValue",
        "winreg.DeleteKey",
        "winreg.DeleteValue",
        "winreg.SaveKey",
        "winreg.LoadKey",
    ],
)
def test_audit_refuses_an_event_of_the_refused_set(event: str) -> None:
    with pytest.raises(PermissionError):
        voice_worker._audit(event, ())


def test_audit_refuses_a_truncating_open_and_passes_a_read() -> None:
    with pytest.raises(PermissionError):
        voice_worker._audit("open", ("p", None, os.O_RDONLY | os.O_TRUNC))

    voice_worker._audit("open", ("p", "rb", 0))


@pytest.mark.skipif(importlib.util.find_spec("pywhispercpp") is None, reason="no engine installed")
def test_the_engine_imports_under_the_guard(tmp_path: Path) -> None:
    body = """
import numpy
from pywhispercpp.model import Model
print("imported")
"""
    assert run_guarded(tmp_path, body) == ["imported"]


def test_main_installs_the_guard(tmp_path: Path) -> None:
    snippet = f"""
import socket, sys
sys.path.insert(0, {str(SRC)!r})
from viki_agent.voice import voice_models, voice_worker
voice_models.present = lambda directory, files: True
def stub(*args):
    for action in (socket.socket, lambda: open("marker.txt", "w")):
        try:
            action()
        except PermissionError:
            print("refused", file=sys.stderr)
        else:
            print("allowed", file=sys.stderr)
    raise RuntimeError
sys.exit(voice_worker.main(["small-q8_0", "cpu"], load=stub))
"""
    result = winproc.run(
        [sys.executable, "-I", "-B", "-c", snippet], cwd=tmp_path, timeout=HANG_GUARD_S
    )

    assert result.returncode == 4, result.stderr
    assert result.stdout == '{"type":"error","code":"model_unavailable"}\n'
    assert result.stderr.count("refused") == 2
    assert "allowed" not in result.stderr
    assert not (tmp_path / "marker.txt").exists()


def test_guard_allows_reads(tmp_path: Path) -> None:
    (tmp_path / "r").write_bytes(b"data")
    body = """
with open("r", "rb") as handle:
    print(handle.read() == b"data")
os.close(os.open("r", os.O_RDONLY))
print("os.open read")
"""
    assert run_guarded(tmp_path, body) == ["True", "os.open read"]


def test_the_channel_holds_only_protocol_lines(tmp_path: Path) -> None:
    snippet = f"""
import os, sys
sys.path.insert(0, {str(SRC)!r})
from viki_agent.voice import voice_worker
protocol = voice_worker._open_channel()
voice_worker._install_guard()
os.write(1, b"noise\\n")
print("stray print")
voice_worker._sender(protocol)({{"type": "ready"}})
"""
    result = winproc.run(
        [sys.executable, "-I", "-B", "-c", snippet], cwd=tmp_path, timeout=HANG_GUARD_S
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"type":"ready"}\n'
    assert "noise" in result.stderr and "stray print" in result.stderr
    assert "PermissionError" not in result.stderr


def test_a_real_launch_with_an_unknown_model_is_invalid_input(tmp_path: Path) -> None:
    result = winproc.run(
        [sys.executable, "-I", "-B", str(WORKER), "no-such-model", "cpu"],
        cwd=tmp_path,
        timeout=HANG_GUARD_S,
    )

    assert result.returncode == 2, result.stderr
    assert result.stdout == '{"type":"error","code":"invalid_input"}\n'
    assert result.stderr == ""


# ---- import boundaries -------------------------------------------------------------------------

ENGINE_MODULES = {"pywhispercpp", "numpy"}


def imported_parts(tree: ast.AST) -> list[set[str]]:
    """Each import statement's dotted name parts (module and imported names) under `tree`."""
    found: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [set(alias.name.split(".")) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = set((node.module or "").split("."))
            found += [module | {alias.name} for alias in node.names]
    return found


def test_only_the_worker_imports_the_engine_and_voice_gpu() -> None:
    for path in (SRC / "viki_agent").rglob("*.py"):
        if path.name == "voice_worker.py":
            continue
        for parts in imported_parts(ast.parse(path.read_text(encoding="utf-8"))):
            assert not parts & ENGINE_MODULES, path.name
            assert "voice_gpu" not in parts, path.name


def test_no_module_imports_a_network_client() -> None:
    # ruff bans all of these but httpx, which is a dev-only test dependency. Its exceptions, as
    # in ruff: voicebox_http.py's loopback-only calls to the Voicebox app (ADR-047),
    # voice_pack_fetch.py's pinned download for the setup child (ADR-048), and agent_cli.py's
    # loopback-only calls to VIKI Agent's own server (ADR-086).
    clients = ("httpx", "urllib3", "requests", "urllib.request", "http.client")
    allowed = {
        ("voicebox_http.py", "http.client"),
        ("voice_pack_fetch.py", "urllib.request"),
        ("voice_pack_fetch.py", "http.client"),
        ("agent_cli.py", "http.client"),
    }
    for path in (SRC / "viki_agent").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                # `from urllib import request` imports the module urllib.request.
                names = [node.module or "", *(f"{node.module}.{a.name}" for a in node.names)]
            else:
                continue
            for name in names:
                if (path.name, name) in allowed:
                    continue
                assert not any(name == c or name.startswith(f"{c}.") for c in clients), path.name


def test_the_worker_imports_the_engine_only_inside_load_recognizer() -> None:
    tree = ast.parse(WORKER.read_text(encoding="utf-8"))
    loader = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_load_recognizer"
    )

    def engine_imports(root: ast.AST) -> list[set[str]]:
        return [parts for parts in imported_parts(root) if parts & ENGINE_MODULES]

    assert len(engine_imports(loader)) == 2
    assert engine_imports(tree) == engine_imports(loader)
