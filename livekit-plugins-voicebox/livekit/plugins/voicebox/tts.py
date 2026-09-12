from __future__ import annotations

import asyncio
import logging
import time
import weakref
from dataclasses import replace
from typing import Never

import aiohttp
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIError,
    APIStatusError,
    APITimeoutError,
    tts,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given, shortuuid

from .audio import decode_pcm
from .client import BackendState, VoiceboxClient, positive_timeout
from .errors import (
    ClientClosedError,
    ModelNotReadyError,
    VoiceboxAPIError,
    VoiceboxConnectionError,
    VoiceboxError,
    VoiceboxTimeoutError,
)
from .models import (
    OUTPUT_RATES,
    GenerationOptions,
    HealthResult,
    ModelReadiness,
    RequestTimings,
    VoiceProfile,
    validate_text,
)

logger = logging.getLogger(__name__)


class TTS(tts.TTS[Never]):
    """Completed-WAV Qwen 0.6B provider; native LiveKit sentence adaptation.

    Requires one client and exclusive backend use (including no desktop inference).
    Cancellation stops output, not the Voicebox inference worker.
    """

    def __init__(
        self,
        *,
        profile: str,
        base_url: str = "http://127.0.0.1:17493",
        engine: str | None = "qwen",
        model_size: str | None = "0.6B",
        language: str = "en",
        instruct: str | None = None,
        sample_rate: int = 24000,
        request_timeout: float = 60.0,
        drain_timeout: float = 120.0,
        http_session: aiohttp.ClientSession | None = None,
        max_concurrent_requests: int = 1,
    ) -> None:
        if type(max_concurrent_requests) is not int or max_concurrent_requests != 1:
            raise ValueError(
                "Only max_concurrent_requests=1 with exclusive backend use is supported."
            )
        if type(sample_rate) is not int or sample_rate not in OUTPUT_RATES:
            raise ValueError("sample_rate must be one of 8000/16000/22050/24000/32000/44100/48000.")
        self._options = GenerationOptions(profile, engine, model_size, language, instruct)
        self._options.validate()
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._client = VoiceboxClient(
            base_url=base_url,
            request_timeout=request_timeout,
            drain_timeout=drain_timeout,
            session=http_session,
        )
        self._revision = 0
        self._cache: tuple[int, str, VoiceProfile] | None = None
        self._streams: weakref.WeakSet[ChunkedStream] = weakref.WeakSet()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def model(self) -> str:
        return "qwen:0.6B"

    @property
    def provider(self) -> str:
        return "Voicebox"

    @property
    def backend_state(self) -> BackendState:
        return self._client.state

    def _check_open(self) -> None:
        if self._closed:
            raise ClientClosedError("Voicebox TTS is closed; create a new instance.")

    async def health(self) -> HealthResult:
        self._check_open()
        return await self._client.health()

    async def list_profiles(self) -> list[VoiceProfile]:
        self._check_open()
        return await self._client.list_profiles()

    async def model_readiness(self) -> ModelReadiness:
        """Read selected-model status without loading or downloading anything."""
        self._check_open()
        return await self._client.model_readiness()

    async def check_idle(self) -> None:
        """Read tracked activity; exclusive backend use is still an operator requirement."""
        self._check_open()
        await self._client.check_idle()

    async def resolve_profile(self, *, refresh: bool = False) -> VoiceProfile:
        self._check_open()
        if refresh:
            self._revision += 1
            self._cache = None
        return await self._resolve(self._options, self._revision)

    async def _resolve(self, options: GenerationOptions, revision: int) -> VoiceProfile:
        if self._cache is not None and self._cache[:2] == (revision, options.profile):
            return self._cache[2]
        profile = await self._client.resolve_profile(options.profile)
        options.effective(profile)
        if revision == self._revision:
            self._cache = (revision, options.profile, profile)
        return profile

    def update_options(
        self,
        *,
        profile: NotGivenOr[str] = NOT_GIVEN,
        engine: NotGivenOr[str | None] = NOT_GIVEN,
        model_size: NotGivenOr[str | None] = NOT_GIVEN,
        language: NotGivenOr[str] = NOT_GIVEN,
        instruct: NotGivenOr[str | None] = NOT_GIVEN,
    ) -> None:
        self._check_open()
        updated = GenerationOptions(
            profile=profile if is_given(profile) else self._options.profile,
            engine=engine if is_given(engine) else self._options.engine,
            model_size=model_size if is_given(model_size) else self._options.model_size,
            language=language if is_given(language) else self._options.language,
            instruct=instruct if is_given(instruct) else self._options.instruct,
        )
        updated.validate()
        self._options = updated
        self._revision += 1
        self._cache = None

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
            self._closed = True
            self._close_task = asyncio.create_task(self._close())
            self._close_task.add_done_callback(self._observe_close)
        await asyncio.shield(self._close_task)

    @staticmethod
    def _observe_close(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Voicebox TTS shutdown requires confirmed backend stop.")

    async def _close(self) -> None:
        try:
            for stream in list(self._streams):
                await stream.aclose()
        finally:
            await self._client.aclose()


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, owner: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        self._owner = owner
        self._snapshot = owner._options
        self._revision = owner._revision
        self._foreground_deadline = (
            asyncio.get_running_loop().time() + owner._client.request_timeout
        )
        self._consumer_closed = False
        self.timings = RequestTimings()
        # Generation has no idempotency contract. SDK retry settings cannot enable replay.
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
        await super().aclose()

    async def __aenter__(self) -> ChunkedStream:
        return self

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        try:
            async with asyncio.timeout_at(self._foreground_deadline):
                started = time.perf_counter()
                profile = await self._owner._resolve(self._snapshot, self._revision)
                options = self._snapshot.effective(profile)
                self.timings.profile_resolution = time.perf_counter() - started
                # Cached != warm. Checking downloads here prevents accidental model acquisition.
                readiness = await self._owner.model_readiness()
                if not readiness.downloaded or readiness.downloading:
                    raise ModelNotReadyError(
                        "Qwen TTS 0.6B must already be downloaded in Voicebox. "
                        "No model download was requested."
                    )
                wav = await self._owner._client.synthesize_wav(
                    profile_id=profile.id,
                    text=self.input_text,
                    engine=options.engine,
                    model_size=options.model_size,
                    language=options.language,
                    instruct=options.instruct,
                    timings=self.timings,
                    connect_timeout=self._conn_options.timeout,
                )
                started = time.perf_counter()
                pcm = await asyncio.to_thread(decode_pcm, wav, self._owner.sample_rate)
                self.timings.decode = time.perf_counter() - started
                output_emitter.initialize(
                    request_id=shortuuid(),
                    sample_rate=self._owner.sample_rate,
                    num_channels=1,
                    mime_type="audio/pcm",
                    frame_size_ms=20,
                )
                frame_bytes = (self._owner.sample_rate // 50) * 2
                for offset in range(0, len(pcm), frame_bytes):
                    await asyncio.sleep(0)
                    if self._consumer_closed:
                        raise asyncio.CancelledError
                    output_emitter.push(pcm[offset : offset + frame_bytes])
                # ChunkedStream ends input and flushes the tail. Explicit flush here
                # makes Agents 1.8.1 append an extra 10 ms final silence marker.
        except (TimeoutError, VoiceboxTimeoutError):
            raise APITimeoutError(
                "Voicebox foreground deadline exceeded; submitted work may still be draining.",
                retryable=False,
            ) from None
        except VoiceboxConnectionError as error:
            raise APIConnectionError(str(error), retryable=False) from None
        except VoiceboxAPIError as error:
            if error.status_code == 499:
                # LiveKit reserves 499 for graceful close; a remote HTTP failure isn't one.
                raise APIError(str(error), retryable=False) from None
            raise APIStatusError(
                str(error), status_code=error.status_code or -1, retryable=False
            ) from None
        except VoiceboxError as error:
            raise APIError(str(error), retryable=False) from None
