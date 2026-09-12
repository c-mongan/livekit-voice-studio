"""Experimental, immutable, direct local Qwen provider; not the Voicebox default.

The worker must set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 before importing
MLX/Transformers. This module never changes process-wide environment settings.
Only an explicitly supplied complete local snapshot is accepted. ``prepare()``
loads the model/reference without generating speech; conditioning occurs on the
first synthesis. Keep one instance alive to retain MLX Audio's conditioning cache.
Exclusive process/backend ownership remains the Studio supervisor's responsibility.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import io
import json
import math
import os
import threading
import weakref
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Never
from urllib.parse import quote

import aiohttp
import numpy as np
import soundfile as sf
from livekit.agents import APIConnectOptions, APIError, APITimeoutError, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import shortuuid
from livekit.plugins.voicebox.client import BackendState, VoiceboxClient, positive_timeout
from livekit.plugins.voicebox.errors import (
    BackendUncertainError,
    ClientClosedError,
    ConfigurationError,
)
from livekit.plugins.voicebox.models import (
    MAX_JSON_BYTES,
    MAX_WAV_BYTES,
    GenerationOptions,
    HealthResult,
    ModelReadiness,
    VoiceProfile,
    validate_text,
)
from numpy.typing import NDArray

_RATE = 24000
_MAX_SAMPLES = _RATE * 30


@dataclass(eq=False)
class _Job:
    profile: str
    text: str | None
    deadline: float
    queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=4))
    stop: threading.Event = field(default_factory=threading.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: APIError | None = None
    task: asyncio.Task[None] | None = None


class FastQwenTTS(tts.TTS[Never]):
    """One cached model, one actual thread job at a time, no automatic retries."""

    def __init__(
        self,
        *,
        profile: str,
        model_path: Path | str,
        base_url: str = "http://127.0.0.1:17493",
        request_timeout: float = 60.0,
        drain_timeout: float = 120.0,
        voice_bundle: Path | str | None = None,
    ) -> None:
        GenerationOptions(profile).validate()
        self._model_path = Path(model_path).expanduser().resolve()
        self._validate_snapshot()
        self._request_timeout = positive_timeout(request_timeout)
        self._drain_timeout = positive_timeout(drain_timeout)
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False), sample_rate=_RATE, num_channels=1
        )
        self._selector = profile
        self._bundle_path = voice_bundle
        self._bundle: Any = None
        self._client = VoiceboxClient(
            base_url=base_url, request_timeout=request_timeout, drain_timeout=drain_timeout
        )
        self._session: aiohttp.ClientSession | None = None
        self._profile: VoiceProfile | None = None
        self._model: Any = None
        self._reference: Any = None
        self._reference_text: str | None = None
        self._lock = asyncio.Lock()
        self._job: _Job | None = None
        self._state: BackendState = "idle"
        self._uncertain = False
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self._streams: weakref.WeakSet[ChunkedStream] = weakref.WeakSet()

    def _validate_snapshot(self) -> None:
        required = (
            "config.json",
            "model.safetensors",
            "tokenizer_config.json",
            "speech_tokenizer/config.json",
            "speech_tokenizer/model.safetensors",
        )
        # Qwen's verified BF16 snapshot uses vocab/merges, not tokenizer.json.
        tokenizer_present = (self._model_path / "tokenizer.json").is_file() or all(
            (self._model_path / name).is_file() for name in ("vocab.json", "merges.txt")
        )
        if (
            not tokenizer_present
            or not self._model_path.is_dir()
            or any(not (self._model_path / name).is_file() for name in required)
        ):
            raise ConfigurationError("A complete existing local Qwen snapshot is required.")
        try:
            with (self._model_path / "config.json").open("rb") as source:
                raw = source.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise ValueError
            config = json.loads(raw)
            if not isinstance(config, dict) or any(
                config.get(key) != value
                for key, value in (
                    ("model_type", "qwen3_tts"),
                    ("tts_model_type", "base"),
                    ("tts_model_size", "0b6"),
                )
            ):
                raise ValueError
        except (OSError, ValueError, RecursionError):
            raise ConfigurationError(
                "The local snapshot must identify a Qwen3 TTS 0.6B base model."
            ) from None

    @property
    def model(self) -> str:
        return "qwen:0.6B:mlx"

    @property
    def provider(self) -> str:
        return "Local Qwen (MLX)"

    @property
    def backend_state(self) -> BackendState:
        return self._state

    @property
    def model_loaded(self) -> bool:
        """Local model/reference are resident; this does not imply warm conditioning.

        ``model_readiness()`` describes the separate Voicebox server. Studio should
        use actual synthesis metrics, not this flag, to report a warmed voice.
        """
        return self._model is not None

    def _check_open(self) -> None:
        if self._uncertain:
            raise BackendUncertainError()
        if self._closing:
            raise ClientClosedError("Local Qwen TTS is closed; create a new instance.")

    async def health(self) -> HealthResult:
        self._check_open()
        if self._bundle_path is not None:
            await self._resolve(self._selector)
            return HealthResult(
                True, "local-reference-ready", self.model_loaded, {"source": "local"}
            )
        return await self._client.health()

    async def check_idle(self) -> None:
        self._check_open()
        if self._bundle_path is not None:
            # Studio's cross-process lease owns local inference; no HTTP backend is used.
            return
        await self._client.check_idle()

    async def model_readiness(self) -> ModelReadiness:
        self._check_open()
        if self._bundle_path is not None:
            self._validate_snapshot()
            return ModelReadiness("qwen-tts-0.6B", True, self.model_loaded, False)
        return await self._client.model_readiness()

    async def resolve_profile(self) -> VoiceProfile:
        self._check_open()
        return await self._resolve(self._selector)

    async def _resolve(self, selector: str) -> VoiceProfile:
        if self._profile is None:
            if self._bundle_path is not None:
                from examples.voice_bundle import load_bundle

                self._bundle = await asyncio.to_thread(load_bundle, self._bundle_path)
                self._profile = self._bundle.profile
                return self._profile
            profile = await self._client.resolve_profile(selector)
            if selector not in (profile.id, profile.name):
                raise ConfigurationError("Select an exact authorized profile ID or name.")
            if (
                profile.voice_type != "cloned"
                or profile.preset_engine is not None
                or profile.sample_count != 1
            ):
                raise ConfigurationError("An authorized single-reference cloned voice is required.")
            self._profile = profile
        return self._profile

    async def _read(self, route: str, limit: int) -> bytes:
        if self._session is None:
            self._session = aiohttp.ClientSession(trust_env=False)
        async with self._session.get(
            self._client.base_url + route,
            timeout=aiohttp.ClientTimeout(total=self._request_timeout),
            allow_redirects=False,
            auto_decompress=False,
        ) as response:
            if not 200 <= response.status < 300:
                raise APIError("Local reference request failed.", retryable=False)
            if response.content_length is not None and response.content_length > limit:
                raise APIError("Local reference exceeds its byte limit.", retryable=False)
            body = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                if len(body) + len(chunk) > limit:
                    raise APIError("Local reference exceeds its byte limit.", retryable=False)
                body.extend(chunk)
            return bytes(body)

    async def _fetch_reference(self, selector: str) -> tuple[bytes, str]:
        profile = await self._resolve(selector)
        if self._bundle_path is not None:
            return self._bundle.audio, self._bundle.transcript
        samples = json.loads(
            await self._read("/profiles/" + quote(profile.id, safe="") + "/samples", MAX_JSON_BYTES)
        )
        if not isinstance(samples, list) or len(samples) != 1 or not isinstance(samples[0], dict):
            raise ConfigurationError("Exactly one reference sample is required.")
        sample = samples[0]
        identifier, transcript = sample.get("id"), sample.get("reference_text")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 500:
            raise ConfigurationError("Invalid reference sample metadata.")
        if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 4000:
            raise ConfigurationError("A bounded existing reference transcript is required.")
        wav = await self._read("/samples/" + quote(identifier, safe=""), MAX_WAV_BYTES)
        return wav, transcript

    @staticmethod
    def _decode_reference(wav: bytes) -> NDArray[np.float32]:
        if len(wav) > MAX_WAV_BYTES or wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
            raise ValueError("Invalid reference WAV.")
        with sf.SoundFile(io.BytesIO(wav)) as source:
            rate = source.samplerate
            if (
                not 8000 <= rate <= 96000
                or not 1 <= source.channels <= 2
                or not 0 < source.frames <= rate * 30
            ):
                raise ValueError("Reference WAV exceeds supported bounds.")
            wave = source.read(dtype="float32", always_2d=True).mean(axis=1)
        if not np.isfinite(wave).all():
            raise ValueError("Invalid reference samples.")
        wave = np.clip(wave, -1.0, 1.0)
        if rate != _RATE:
            resample_poly = importlib.import_module("scipy.signal").resample_poly
            divisor = math.gcd(rate, _RATE)
            wave = resample_poly(wave, _RATE // divisor, rate // divisor)
        return np.asarray(np.clip(wave, -1.0, 1.0), dtype=np.float32)

    def _initialize(self, reference: tuple[bytes, str]) -> None:
        # Executed under the actual job's lease, including decode/load/conditioning.
        self._validate_snapshot()
        if any(os.environ.get(key) != "1" for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")):
            raise ConfigurationError("The experimental worker requires offline model loading.")
        wave = self._decode_reference(reference[0])
        mx = importlib.import_module("mlx.core")
        load_model = importlib.import_module("mlx_audio.tts.utils").load_model
        model = load_model(str(self._model_path))
        ref_audio = mx.array(wave)
        self._model, self._reference, self._reference_text = model, ref_audio, reference[1]

    def _work(
        self, job: _Job, loop: asyncio.AbstractEventLoop, reference: tuple[bytes, str] | None
    ) -> None:
        if job.stop.is_set():
            return
        if self._model is None:
            assert reference is not None
            self._initialize(reference)
        if job.stop.is_set() or job.text is None:
            return
        generator = self._model.generate(
            text=job.text,
            ref_audio=self._reference,
            ref_text=self._reference_text,
            lang_code="english",
            stream=True,
            streaming_interval=0.32,
            max_tokens=750,
            verbose=False,
        )
        total = 0
        try:
            for result in generator:
                if job.stop.is_set():
                    return
                audio = np.asarray(result.audio)
                if (
                    result.sample_rate != _RATE
                    or audio.ndim != 1
                    or audio.size + total > _MAX_SAMPLES
                    or not np.isfinite(audio).all()
                ):
                    raise ValueError("Unsupported model audio.")
                total += audio.size
                if not audio.size:
                    continue
                pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                pending = asyncio.run_coroutine_threadsafe(job.queue.put(pcm), loop)
                try:
                    while not job.stop.is_set():
                        try:
                            pending.result(timeout=0.02)
                            break
                        except concurrent.futures.TimeoutError:
                            continue
                    else:
                        return
                finally:
                    if not pending.done():
                        pending.cancel()
            if total == 0 and not job.stop.is_set():
                raise ValueError("No model audio.")
        finally:
            generator.close()

    def _stop(self, job: _Job) -> None:
        job.stop.set()
        if not job.done.is_set() and not self._uncertain:
            self._state = "draining"

    def _mark_uncertain(self) -> None:
        self._uncertain = True
        self._state = "uncertain"

    async def _run_job(self, job: _Job) -> None:
        try:
            async with asyncio.timeout_at(job.deadline):
                reference = (
                    await self._fetch_reference(job.profile) if self._model is None else None
                )
            if job.stop.is_set():
                return
            worker = asyncio.create_task(
                asyncio.to_thread(self._work, job, asyncio.get_running_loop(), reference)
            )
            remaining = max(0.0, job.deadline - asyncio.get_running_loop().time())
            completed, _ = await asyncio.wait({worker}, timeout=remaining)
            if not completed:
                self._stop(job)
                self._mark_uncertain()
            # Never cancel this task or its thread, even after the ownership deadline.
            await asyncio.shield(worker)
        except Exception:
            job.error = APIError("Local Qwen preparation or generation failed.", retryable=False)
        finally:
            job.done.set()
            self._job = None
            if not self._uncertain:
                self._state = "draining" if self._closing else "idle"
            self._lock.release()

    async def _start(self, selector: str, text: str | None, deadline: float) -> _Job:
        await self._lock.acquire()
        try:
            self._check_open()
            job = _Job(selector, text, deadline + self._drain_timeout)
            self._job = job
            self._state = "active"
            job.task = asyncio.create_task(self._run_job(job))
            return job
        except BaseException:
            self._lock.release()
            raise

    async def prepare(self) -> None:
        """Cache model and authorized reference only; do not synthesize warm-up audio."""
        self._check_open()
        deadline = asyncio.get_running_loop().time() + self._request_timeout
        job = None
        try:
            async with asyncio.timeout_at(deadline):
                job = await self._start(self._selector, None, deadline)
                await job.done.wait()
                if job.error is not None:
                    raise job.error
        except TimeoutError:
            raise APITimeoutError("Local Qwen preparation timed out.", retryable=False) from None
        finally:
            if job is not None and not job.done.is_set():
                self._stop(job)

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        self._check_open()
        validate_text(text)
        positive_timeout(conn_options.timeout)
        stream = ChunkedStream(owner=self, input_text=text, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
            self._close_task.add_done_callback(self._observe_close)
        await asyncio.shield(self._close_task)

    @staticmethod
    def _observe_close(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def _close(self) -> None:
        try:
            async with asyncio.timeout(self._drain_timeout):
                if self._job is not None:
                    self._stop(self._job)
                for stream in list(self._streams):
                    await stream.aclose()
                if self._job is not None:
                    await self._job.done.wait()
        except TimeoutError:
            self._mark_uncertain()
        finally:
            if self._session is not None:
                await self._session.close()
            await self._client.aclose()
        if self._uncertain:
            # Retain model/reference and the real job until process-supervised shutdown.
            raise BackendUncertainError()
        self._state = "closed"


class ChunkedStream(tts.ChunkedStream):
    def __init__(
        self, *, owner: FastQwenTTS, input_text: str, conn_options: APIConnectOptions
    ) -> None:
        self._owner = owner
        self._snapshot = owner._selector
        self._deadline = asyncio.get_running_loop().time() + owner._request_timeout
        self._consumer_closed = False
        self._job: _Job | None = None
        super().__init__(
            tts=owner, input_text=input_text, conn_options=replace(conn_options, max_retry=0)
        )

    async def __anext__(self) -> tts.SynthesizedAudio:
        if self._consumer_closed:
            raise StopAsyncIteration
        try:
            result = await super().__anext__()
            if self._consumer_closed:
                raise StopAsyncIteration
            return result
        except asyncio.CancelledError:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        self._consumer_closed = True
        if self._job is not None:
            self._owner._stop(self._job)
        await super().aclose()

    async def _consume(self, job: _Job, emitter: tts.AudioEmitter) -> None:
        completed = asyncio.create_task(job.done.wait())
        pending: asyncio.Task[bytes] | None = None
        try:
            while not job.done.is_set() or not job.queue.empty():
                pending = asyncio.create_task(job.queue.get())
                await asyncio.wait({pending, completed}, return_when=asyncio.FIRST_COMPLETED)
                if not pending.done():
                    if job.queue.empty():
                        break
                    await pending
                pcm = pending.result()
                pending = None
                for offset in range(0, len(pcm), 960):
                    await asyncio.sleep(0)
                    if self._consumer_closed:
                        raise asyncio.CancelledError
                    emitter.push(pcm[offset : offset + 960])
            if job.error is not None:
                raise job.error
        finally:
            tasks = [completed] + ([pending] if pending is not None else [])
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        try:
            async with asyncio.timeout_at(self._deadline):
                self._job = await self._owner._start(
                    self._snapshot, self.input_text, self._deadline
                )
                output_emitter.initialize(
                    request_id=shortuuid(),
                    sample_rate=_RATE,
                    num_channels=1,
                    mime_type="audio/pcm",
                    frame_size_ms=20,
                )
                await self._consume(self._job, output_emitter)
                # SDK 1.8.1 owns finalization; explicit flush adds a silence marker.
        except TimeoutError:
            raise APITimeoutError(
                "Local Qwen foreground timed out; its job may still be draining.", retryable=False
            ) from None
        except APIError:
            raise
        except Exception:
            raise APIError("Local Qwen synthesis failed.", retryable=False) from None
        finally:
            if self._job is not None and not self._job.done.is_set():
                self._owner._stop(self._job)
