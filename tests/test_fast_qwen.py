from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import numpy as np
import pytest
import soundfile as sf
from aiohttp import web
from livekit.agents import APIConnectOptions, APIError, APITimeoutError, tts
from livekit.plugins.voicebox.errors import BackendUncertainError, ConfigurationError

from examples.fast_qwen import FastQwenTTS


def make_wav(
    rate: int = 24000,
    channels: int = 1,
    seconds: float = 0.1,
    value: float = 0.25,
    subtype: str = "PCM_16",
) -> bytes:
    buffer = io.BytesIO()
    sf.write(
        buffer,
        np.full((int(rate * seconds), channels), value, dtype=np.float32),
        rate,
        format="WAV",
        subtype=subtype,
    )
    return buffer.getvalue()


@pytest.fixture
def snapshot() -> Iterator[Path]:
    root = Path.cwd() / (".fast-qwen-test-" + uuid4().hex)
    (root / "speech_tokenizer").mkdir(parents=True)
    # Filenames from the verified cached 1eccf1cb... BF16 snapshot, without real assets.
    for name in (
        ".gitattributes",
        "README.md",
        "config.json",
        "generation_config.json",
        "merges.txt",
        "model.safetensors",
        "model.safetensors.index.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "vocab.json",
        "speech_tokenizer/config.json",
        "speech_tokenizer/configuration.json",
        "speech_tokenizer/model.safetensors",
        "speech_tokenizer/preprocessor_config.json",
    ):
        (root / name).write_bytes(b"synthetic fixture, never loaded")
    (root / "config.json").write_text(
        json.dumps(
            {
                "model_type": "qwen3_tts",
                "tts_model_type": "base",
                "tts_model_size": "0b6",
            }
        )
    )
    try:
        yield root
    finally:
        shutil.rmtree(root)


class Model:
    def __init__(self, generate: Callable[[], Iterator[Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = 0
        self.generate_audio = generate or (lambda: iter([chunk(2400)]))

    def generate(self, **kwargs: Any) -> Iterator[Any]:
        self.calls.append(kwargs)
        try:
            yield from self.generate_audio()
        finally:
            self.closed += 1


def chunk(size: int = 7680, *, value: float = 0.25, rate: int = 24000) -> SimpleNamespace:
    return SimpleNamespace(audio=np.full(size, value, dtype=np.float32), sample_rate=rate)


async def eventually(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 — observes synchronous worker-thread state.
            await asyncio.sleep(0.005)


@pytest.fixture
async def harness(
    server: Any, profile: dict[str, Any], snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Any]:
    calls: list[str] = []
    models: list[Model] = []
    providers: list[FastQwenTTS] = []
    initialized: list[bytes] = []
    samples: list[dict[str, str]] = [{"id": "sample-1", "reference_text": "private transcript"}]
    payload = {"wav": make_wav()}

    async def handle(request: web.Request) -> web.Response:
        calls.append(request.path)
        if request.path == "/profiles":
            return web.json_response([profile])
        if request.path.endswith("/samples"):
            return web.json_response(samples)
        if request.path == "/samples/sample-1":
            return web.Response(body=payload["wav"])
        if request.path == "/health":
            return web.json_response({"status": "ok"})
        if request.path == "/tasks/active":
            return web.json_response({"generations": [], "downloads": []})
        if request.path == "/models/status":
            return web.json_response(
                {
                    "models": [
                        {
                            "model_name": "qwen-tts-0.6B",
                            "downloaded": True,
                            "loaded": True,
                            "downloading": False,
                        }
                    ]
                }
            )
        return web.Response(status=404)

    url = await server(handle)

    def initialize(owner: FastQwenTTS, reference: tuple[bytes, str]) -> None:
        initialized.append(reference[0])
        owner._reference = owner._decode_reference(reference[0])
        owner._reference_text = reference[1]
        owner._model = models[providers.index(owner)]

    monkeypatch.setattr(FastQwenTTS, "_initialize", initialize)

    def create(model: Model | None = None, **kwargs: Any) -> FastQwenTTS:
        owner = FastQwenTTS(profile="Test Voice", model_path=snapshot, base_url=url, **kwargs)
        models.append(model or Model())
        providers.append(owner)
        return owner

    yield SimpleNamespace(
        create=create,
        models=models,
        calls=calls,
        initialized=initialized,
        samples=samples,
        payload=payload,
        profile=profile,
    )
    for provider in providers:
        try:
            await provider.aclose()
        except BackendUncertainError:
            pass


async def test_native_frames_cache_and_discovery(harness: Any) -> None:
    owner = harness.create()
    assert owner.provider == "Local Qwen (MLX)"
    assert owner.model == "qwen:0.6B:mlx"
    assert owner.sample_rate == 24000 and owner.num_channels == 1
    assert not owner.capabilities.streaming
    assert not owner.model_loaded
    assert (await owner.resolve_profile()).id == "voice-1"
    assert (await owner.health()).ok
    assert (await owner.model_readiness()).downloaded
    await owner.check_idle()
    await owner.prepare()
    assert owner.model_loaded
    assert harness.models[0].calls == []
    for _ in range(2):
        async with owner.synthesize("Hello") as stream:
            assert isinstance(stream, tts.ChunkedStream)
            frames = [event.frame async for event in stream]
        assert sum(f.samples_per_channel for f in frames) == 2400
        assert all(f.sample_rate == 24000 and f.num_channels == 1 for f in frames)
        assert all(0 < f.samples_per_channel <= 480 for f in frames)
    assert len(harness.initialized) == 1
    assert harness.calls.count("/samples/sample-1") == 1
    assert harness.calls.count("/profiles") == 1
    arguments = harness.models[0].calls
    assert len(arguments) == 2
    assert arguments[0]["ref_audio"] is arguments[1]["ref_audio"]
    assert arguments[0]["stream"] is True
    assert "streaming" not in arguments[0]
    assert arguments[0]["streaming_interval"] == 0.32
    assert arguments[0]["max_tokens"] == 750
    assert arguments[0]["lang_code"] == "english"
    await owner.aclose()
    assert owner.backend_state == "closed"
    assert owner._session.closed


async def test_first_frame_precedes_completion_and_cancel_holds_lease(harness: Any) -> None:
    blocked, release = threading.Event(), threading.Event()

    def generate() -> Iterator[Any]:
        yield chunk()
        blocked.set()
        release.wait(3)
        yield chunk()

    model = Model(generate)
    owner = harness.create(model)
    stream = owner.synthesize("First")
    try:
        first = await anext(stream)
        assert 0 < first.frame.samples_per_channel <= 480
        await eventually(blocked.is_set)
        assert model.closed == 0
        queued = owner.synthesize("Must not generate")
        await queued.aclose()
        await stream.aclose()
        assert owner.backend_state == "draining"
        assert owner._lock.locked()
        assert len(model.calls) == 1
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    finally:
        release.set()
        await stream.aclose()
    await eventually(lambda: owner.backend_state == "idle")
    assert model.closed == 1
    async with owner.synthesize("Second") as following:
        await following.collect()
    assert len(model.calls) == 2


async def test_consumer_task_cancellation_is_prompt(harness: Any) -> None:
    entered, release = threading.Event(), threading.Event()

    def generate() -> Iterator[Any]:
        entered.set()
        release.wait(3)
        yield chunk()

    owner = harness.create(Model(generate))
    stream = owner.synthesize("Hello")
    consumer = asyncio.create_task(anext(stream))
    try:
        await eventually(entered.is_set)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(consumer, 0.2)
        assert owner.backend_state == "draining"
        assert owner._lock.locked()
    finally:
        release.set()
        await stream.aclose()
    await eventually(lambda: owner.backend_state == "idle")


async def test_uncooperative_job_deadline_permanently_blocks_admission(harness: Any) -> None:
    entered, release = threading.Event(), threading.Event()

    def generate() -> Iterator[Any]:
        entered.set()
        release.wait(3)
        yield chunk()

    model = Model(generate)
    owner = harness.create(model, drain_timeout=0.06)
    # This test targets stalled generation, not cold HTTP reference retrieval or
    # initialization competing for its deliberately short foreground deadline.
    await owner.prepare()
    assert not model.calls
    owner._request_timeout = 0.08
    stream = owner.synthesize("Hello")
    try:
        await eventually(entered.is_set)
        with pytest.raises(APITimeoutError) as error:
            await stream.collect()
        assert not error.value.retryable
        await eventually(lambda: owner.backend_state == "uncertain")
        assert owner._lock.locked()
        with pytest.raises(BackendUncertainError):
            owner.synthesize("Blocked")
        with pytest.raises(BackendUncertainError):
            await asyncio.wait_for(owner.aclose(), 0.2)
    finally:
        release.set()
        await stream.aclose()
    await eventually(lambda: not owner._lock.locked())
    assert owner.backend_state == "uncertain"
    with pytest.raises(BackendUncertainError):
        owner.synthesize("Still blocked")


async def test_close_during_initialize_and_cancelled_prepare(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    original = FastQwenTTS._initialize

    def initialize(owner: FastQwenTTS, reference: tuple[bytes, str]) -> None:
        entered.set()
        release.wait(3)
        original(owner, reference)

    monkeypatch.setattr(FastQwenTTS, "_initialize", initialize)
    owner = harness.create(drain_timeout=0.05)
    preparing = asyncio.create_task(owner.prepare())
    try:
        await eventually(entered.is_set)
        preparing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(preparing, 0.2)
        with pytest.raises(BackendUncertainError):
            await asyncio.wait_for(owner.aclose(), 0.2)
        assert owner._lock.locked()
        assert harness.models[0].calls == []
    finally:
        release.set()
    await eventually(lambda: not owner._lock.locked())
    assert owner.backend_state == "uncertain"
    assert harness.models[0].calls == []


async def test_bridge_backpressure_and_stop_unblocks_pending_put(harness: Any) -> None:
    produced: list[int] = []

    def generate() -> Iterator[Any]:
        for index in range(30):
            produced.append(index)
            yield chunk()

    owner = harness.create(Model(generate))
    deadline = asyncio.get_running_loop().time() + 2
    job = await owner._start(owner._selector, "Hello", deadline)
    await eventually(lambda: job.queue.qsize() == 4 and len(produced) == 5)
    await asyncio.sleep(0.06)
    assert len(produced) == 5
    assert job.queue.maxsize == 4
    owner._stop(job)
    await asyncio.wait_for(job.done.wait(), 0.3)
    assert harness.models[0].closed == 1
    assert not owner._lock.locked()
    assert len(produced) == 5


@pytest.mark.parametrize(
    "kind", ["nan", "infinite", "stereo", "oversize", "rate", "empty", "raise"]
)
async def test_invalid_model_audio_sanitized_never_replayed(harness: Any, kind: str) -> None:
    def generate() -> Iterator[Any]:
        yield chunk()
        if kind == "raise":
            raise RuntimeError("private transcript and Hello")
        result = chunk()
        if kind == "nan":
            result.audio[0] = np.nan
        elif kind == "infinite":
            result.audio[0] = np.inf
        elif kind == "stereo":
            result.audio = np.zeros((12, 2))
        elif kind == "oversize":
            result = chunk(720001)
        elif kind == "rate":
            result.sample_rate = 48000
        yield result

    model = Model((lambda: iter([])) if kind == "empty" else generate)
    owner = harness.create(model)
    with pytest.raises(APIError) as error:
        async with owner.synthesize("Hello", conn_options=APIConnectOptions(max_retry=9)) as stream:
            await stream.collect()
    assert not error.value.retryable
    assert "private" not in str(error.value) and "Hello" not in str(error.value)
    assert len(model.calls) == 1
    assert model.closed == 1
    assert owner.backend_state == "idle"


@pytest.mark.parametrize(
    "kind", ["corrupt", "nan", "long", "channels", "rate", "multiple", "transcript", "bytes"]
)
async def test_reference_validation_prevents_model_generation(harness: Any, kind: str) -> None:
    if kind == "corrupt":
        harness.payload["wav"] = b"private invalid WAV"
    elif kind == "nan":
        harness.payload["wav"] = make_wav(value=np.nan, subtype="FLOAT")
    elif kind == "long":
        harness.payload["wav"] = make_wav(seconds=30.01)
    elif kind == "channels":
        harness.payload["wav"] = make_wav(channels=3)
    elif kind == "rate":
        harness.payload["wav"] = make_wav(rate=192000)
    elif kind == "multiple":
        harness.samples.append(harness.samples[0])
    elif kind == "transcript":
        harness.samples[0]["reference_text"] = "x" * 4001
    elif kind == "bytes":
        harness.payload["wav"] = b"x" * (16 * 1024 * 1024 + 1)
    owner = harness.create()
    with pytest.raises(APIError) as error:
        await owner.prepare()
    assert not error.value.retryable
    assert "private" not in str(error.value)
    assert harness.models[0].calls == []


async def test_redirect_and_json_bounds(
    server: Any, snapshot: Path, profile: dict[str, Any]
) -> None:
    hits: list[str] = []
    mode = "redirect"

    async def handle(request: web.Request) -> web.Response:
        hits.append(request.path)
        if request.path == "/profiles":
            return web.json_response([profile])
        if mode == "redirect":
            return web.Response(status=302, headers={"Location": "/must-not-follow"})
        return web.Response(body=b"x" * (1024 * 1024 + 1))

    url = await server(handle)
    for case in ("redirect", "oversize"):
        mode = case
        owner = FastQwenTTS(profile="Test Voice", model_path=snapshot, base_url=url)
        try:
            with pytest.raises(APIError):
                await owner.prepare()
        finally:
            await owner.aclose()
    assert "/must-not-follow" not in hits


def test_missing_snapshot_and_ordinary_import(snapshot: Path) -> None:
    (snapshot / "speech_tokenizer/model.safetensors").unlink()
    with pytest.raises(ConfigurationError, match="complete existing local"):
        FastQwenTTS(profile="Test Voice", model_path=snapshot)


async def test_verified_snapshot_layout_without_tokenizer_json(snapshot: Path) -> None:
    assert len([path for path in snapshot.rglob("*") if path.is_file()]) == 14
    assert not (snapshot / "tokenizer.json").exists()
    owner = FastQwenTTS(profile="Test Voice", model_path=snapshot)
    try:
        assert owner.backend_state == "idle"
        assert not owner.model_loaded
        assert owner._session is None
    finally:
        await owner.aclose()


async def test_single_file_tokenizer_layout_supported(snapshot: Path) -> None:
    (snapshot / "vocab.json").unlink()
    (snapshot / "merges.txt").unlink()
    (snapshot / "tokenizer.json").write_bytes(b"synthetic tokenizer, never loaded")
    owner = FastQwenTTS(profile="Test Voice", model_path=snapshot)
    assert not owner.model_loaded
    await owner.aclose()


async def test_huggingface_snapshot_symlinks_to_external_blobs(snapshot: Path) -> None:
    files = [path for path in snapshot.rglob("*") if path.is_file()]
    cache = snapshot / "synthetic-hf-cache"
    cached_snapshot = cache / "snapshots" / "revision"
    blobs = cache / "blobs"
    blobs.mkdir(parents=True)
    weight_blob: Path | None = None
    for index, source in enumerate(files):
        relative = source.relative_to(snapshot)
        blob = blobs / str(index)
        source.rename(blob)
        link = cached_snapshot / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(blob, link.parent))
        if str(relative) == "model.safetensors":
            weight_blob = blob
    owner = FastQwenTTS(profile="Test Voice", model_path=cached_snapshot)
    assert not owner.model_loaded
    assert (cached_snapshot / "model.safetensors").is_symlink()
    await owner.aclose()
    assert weight_blob is not None
    weight_blob.unlink()
    with pytest.raises(ConfigurationError, match="complete existing local"):
        FastQwenTTS(profile="Test Voice", model_path=cached_snapshot)


@pytest.mark.parametrize(
    "missing",
    ["vocab.json", "merges.txt", "model.safetensors", "speech_tokenizer/model.safetensors"],
)
def test_incomplete_tokenizer_or_weights_rejected(snapshot: Path, missing: str) -> None:
    (snapshot / missing).unlink()
    with pytest.raises(ConfigurationError, match="complete existing local"):
        FastQwenTTS(profile="Test Voice", model_path=snapshot)


@pytest.mark.parametrize("field", ["model_type", "tts_model_type", "tts_model_size"])
@pytest.mark.parametrize("value", ["private-wrong-checkpoint", None, 123])
def test_wrong_model_identity_rejected(snapshot: Path, field: str, value: Any) -> None:
    path = snapshot / "config.json"
    config = json.loads(path.read_text())
    config[field] = value
    path.write_text(json.dumps(config))
    with pytest.raises(ConfigurationError, match="Qwen3 TTS 0.6B base") as error:
        FastQwenTTS(profile="Test Voice", model_path=snapshot)
    assert "private-wrong-checkpoint" not in str(error.value)


@pytest.mark.parametrize(
    "raw", [b"private malformed JSON", b"\xff", b"[]", b"{}", b"x" * (1024 * 1024 + 1)]
)
def test_invalid_model_config_rejected(snapshot: Path, raw: bytes) -> None:
    (snapshot / "config.json").write_bytes(raw)
    with pytest.raises(ConfigurationError, match="Qwen3 TTS 0.6B base") as error:
        FastQwenTTS(profile="Test Voice", model_path=snapshot)
    assert "private" not in str(error.value)


async def test_queued_timeout_does_not_generate(harness: Any) -> None:
    entered, release = threading.Event(), threading.Event()

    def generate() -> Iterator[Any]:
        entered.set()
        release.wait(3)
        yield chunk()

    owner = harness.create(Model(generate), request_timeout=0.1)
    first = owner.synthesize("First")
    try:
        await eventually(entered.is_set)
        queued = owner.synthesize("Queued")
        with pytest.raises(APITimeoutError):
            await queued.collect()
        await queued.aclose()
        assert len(harness.models[0].calls) == 1
        assert owner._lock.locked()
    finally:
        release.set()
        await first.aclose()
    await eventually(lambda: owner.backend_state == "idle")


async def test_lazy_local_loader_mlx_reference_and_resampling(
    server: Any, profile: dict[str, Any], snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded: list[str] = []
    converted: list[Any] = []
    reference = object()
    model = Model()

    def load_model(path: str) -> Model:
        assert threading.current_thread() is not threading.main_thread()
        loaded.append(path)
        return model

    def array(wave: Any) -> object:
        converted.append(wave)
        return reference

    def resample_poly(wave: Any, up: int, down: int) -> Any:
        assert up == 1 and down == 2
        assert wave.shape == (4800,)
        assert np.max(np.abs(wave)) <= 1
        return wave[::2]

    monkeypatch.setitem(sys.modules, "mlx.core", SimpleNamespace(array=array))
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", SimpleNamespace(load_model=load_model))
    monkeypatch.setitem(sys.modules, "scipy.signal", SimpleNamespace(resample_poly=resample_poly))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    async def handle(request: web.Request) -> web.Response:
        if request.path == "/profiles":
            return web.json_response([profile])
        if request.path.endswith("/samples"):
            return web.json_response([{"id": "one", "reference_text": "private"}])
        return web.Response(body=make_wav(rate=48000, channels=2, value=2, subtype="FLOAT"))

    owner = FastQwenTTS(profile="Test Voice", model_path=snapshot, base_url=await server(handle))
    try:
        await owner.prepare()
        assert loaded == [str(snapshot.resolve())]
        assert len(converted) == 1
        assert converted[0].shape == (2400,)
        assert converted[0].dtype == np.float32
        assert np.isfinite(converted[0]).all()
        assert np.max(np.abs(converted[0])) <= 1
        assert model.calls == []
        async with owner.synthesize("Hello") as stream:
            await stream.collect()
        assert model.calls[0]["ref_audio"] is reference
        assert len(loaded) == 1
    finally:
        await owner.aclose()


def test_offline_flags_required_before_optional_imports(
    snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    owner = FastQwenTTS(profile="Test Voice", model_path=snapshot)
    with pytest.raises(ConfigurationError, match="offline"):
        owner._initialize((make_wav(), "private"))


async def test_uncertain_close_remains_owned_when_caller_cancelled(harness: Any) -> None:
    entered, release = threading.Event(), threading.Event()

    def generate() -> Iterator[Any]:
        entered.set()
        release.wait(3)
        yield chunk()

    owner = harness.create(Model(generate), drain_timeout=0.08)
    stream = owner.synthesize("Hello")
    closing: asyncio.Task[None] | None = None
    try:
        await eventually(entered.is_set)
        closing = asyncio.create_task(owner.aclose())
        await asyncio.sleep(0)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        with pytest.raises(BackendUncertainError):
            await owner.aclose()
        assert owner._lock.locked()
    finally:
        release.set()
        await stream.aclose()
    await eventually(lambda: not owner._lock.locked())


def test_modules_import_without_optional_mlx_or_scipy() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.abc
import runpy
import sys

class WithoutExtras(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'mlx', 'mlx_audio', 'scipy'}:
            raise ModuleNotFoundError('Optional dependency is deliberately unavailable')
        return None

sys.meta_path.insert(0, WithoutExtras())
import examples.fast_qwen
runpy.run_path('tests/test_fast_qwen.py')
""",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
