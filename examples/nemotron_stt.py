"""LiveKit bridge to an explicitly installed, CPU-only NeMo-Speech.cpp sidecar.

This is NVIDIA's project-specific realtime protocol, not OpenAI Realtime.
No models are downloaded and no processes are started by this module.
Studio must call ``NemotronSTT.commit_utterance()`` at its existing VAD
end-of-speech boundary. The WebSocket ``endpointing_ms`` setting changes only
the silence threshold; it cannot enable the native endpointer, which is off
by default. Explicit VAD commits work without another VAD model or a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import sys
import weakref
from array import array
from types import SimpleNamespace
from typing import Any, Never
from urllib.parse import urlsplit

import aiohttp
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectionError,
    APIConnectOptions,
    APITimeoutError,
    LanguageCode,
    NotGivenOr,
    stt,
    utils,
)
from livekit.agents.utils import AudioBuffer

_PREFIX = "conversation.item.input_audio_transcription."


def _local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "::1")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Nemotron requires a plain HTTP loopback URL without credentials or path")
    return value.rstrip("/")


class _AudioChannel(utils.aio.Chan[rtc.AudioFrame | stt.SpeechStream._FlushSentinel]):
    def __init__(self, byte_limit: int) -> None:
        super().__init__(maxsize=256)
        self.byte_limit = byte_limit
        self.audio_bytes = 0

    def send_nowait(self, value: rtc.AudioFrame | stt.SpeechStream._FlushSentinel) -> None:
        size = len(value.data) * 2 if isinstance(value, rtc.AudioFrame) else 0
        if self.full() or self.audio_bytes + size > self.byte_limit:
            raise BufferError("Nemotron input buffer overloaded; stop and restart the stream")
        super().send_nowait(value)
        self.audio_bytes += size

    def recv_nowait(self) -> rtc.AudioFrame | stt.SpeechStream._FlushSentinel:
        value = super().recv_nowait()
        if isinstance(value, rtc.AudioFrame):
            self.audio_bytes -= len(value.data) * 2
        return value


class NemotronSTT(stt.STT[Never]):
    """Streaming English recognition through a local native sidecar.

    Audio is bounded to ``max_buffer_seconds`` plus one <=200ms in-flight frame.
    ``push_frame`` is synchronous, so overload is an explicit BufferError rather
    than silent audio loss. Stop/recreate the affected stream after overload.
    Retain normal LiveKit VAD turn detection and call ``commit_utterance()`` when
    user state changes from speaking to listening. LiveKit does not flush a
    streaming provider at VAD end-of-speech. Commits wait for authoritative
    native finals without ending input or closing the reusable socket.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8766",
        sample_rate: int = 16000,
        max_buffer_seconds: float = 2.0,
        finalize_timeout: float = 15.0,
        endpointing_ms: int = 800,
    ) -> None:
        self.base_url = _local_url(base_url)
        if sample_rate != 16000:
            raise ValueError("Nemotron model input sample_rate must be 16000")
        if not math.isfinite(max_buffer_seconds) or not 0.02 <= max_buffer_seconds <= 10:
            raise ValueError("max_buffer_seconds must be between 0.02 and 10")
        if not math.isfinite(finalize_timeout) or not 0 < finalize_timeout <= 120:
            raise ValueError("finalize_timeout must be positive and at most 120 seconds")
        if not 80 <= endpointing_ms <= 5000:
            raise ValueError("endpointing_ms must be between 80 and 5000")
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True, interim_results=True, offline_recognize=False
            )
        )
        self.sample_rate = sample_rate
        self.max_buffer_bytes = int(max_buffer_seconds * sample_rate * 2)
        self.finalize_timeout = finalize_timeout
        self.endpointing_ms = endpointing_ms
        self._streams: weakref.WeakSet[NemotronSpeechStream] = weakref.WeakSet()
        self._closed = False

    @property
    def model(self) -> str:
        return "nemotron-speech-streaming-en-0.6b"

    @property
    def provider(self) -> str:
        return "nvidia-nemo-speech-cpp-local-cpu"

    async def is_ready(self, *, timeout: float = 2.0) -> bool:  # noqa: ASYNC109
        """Check loaded transcription capability, without downloading or starting anything."""
        if self._closed:
            return False
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
                session.get(self.base_url + "/ready", allow_redirects=False) as response,
            ):
                if response.status != 200:
                    return False
                try:
                    raw = await response.content.readexactly(65537)
                except asyncio.IncompleteReadError as error:
                    raw = error.partial
                if len(raw) > 65536:
                    return False
                data = json.loads(raw)
                return (
                    isinstance(data, dict)
                    and data.get("ready") is True
                    and "asr" in data.get("capabilities", [])
                    and data.get("device") == "cpu"
                )
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
            return False

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        raise NotImplementedError("NemotronSTT is streaming-only; use stream()")

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> NemotronSpeechStream:
        if self._closed:
            raise RuntimeError("NemotronSTT is closed")
        if utils.is_given(language) and language not in ("en", "en-US", "en-GB"):
            raise ValueError("This Nemotron checkpoint supports English only")
        stream = NemotronSpeechStream(self, conn_options)
        self._streams.add(stream)
        return stream

    def commit_utterance(self) -> None:
        """Queue an explicit VAD turn boundary on this provider's live streams.

        Synchronous: call from the AgentSession ``user_state_changed`` handler
        when ``old_state == "speaking"`` and ``new_state == "listening"``.
        Use the same event loop as audio ingress. Closed, ended, failed and
        overloaded streams are ignored, including callbacks during shutdown.
        This does not stop input or await recognition; finals arrive through
        normal speech events under the stream's bounded finalization deadline.
        Queue overload remains an explicit ``BufferError``, never dropped audio.
        """
        if self._closed:
            return
        for stream in list(self._streams):
            if (
                not stream._input_ch.closed
                and not stream._event_ch.closed
                and not stream._task.done()
                and not stream._overloaded.is_set()
            ):
                stream.flush()

    async def aclose(self) -> None:
        self._closed = True
        await asyncio.gather(*(stream.aclose() for stream in list(self._streams)))


class NemotronSpeechStream(stt.SpeechStream):
    def __init__(self, provider: NemotronSTT, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=provider, conn_options=conn_options, sample_rate=16000)
        self._provider = provider
        self._audio = _AudioChannel(provider.max_buffer_bytes)
        self._input_ch = self._audio
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._started = False
        self._partial = ""
        self._sent_audio = False
        self._committed = asyncio.Event()
        self._commit_pending = False
        self._output_pending = 0
        self._overloaded = asyncio.Event()

    @property
    def buffered_audio_bytes(self) -> int:
        return self._audio.audio_bytes

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        if frame.sample_rate < 8000 or frame.sample_rate > 96000:
            raise ValueError("Input sample rate must be between 8000 and 96000")
        if frame.num_channels not in (1, 2):
            raise ValueError("Nemotron expects mono or stereo PCM16 audio")
        if frame.samples_per_channel > frame.sample_rate // 5:
            raise ValueError("Audio frames must be at most 200 ms")
        if len(frame.data) != frame.samples_per_channel * frame.num_channels:
            raise ValueError("PCM16 frame size does not match its declared sample count")
        if frame.num_channels == 2:
            source = frame.data
            mono = array("h", ((source[i] + source[i + 1]) // 2 for i in range(0, len(source), 2)))
            frame = rtc.AudioFrame(mono.tobytes(), frame.sample_rate, 1, frame.samples_per_channel)
        try:
            super().push_frame(frame)
        except BufferError:
            self._overloaded.set()
            raise

    def flush(self) -> None:
        try:
            super().flush()
        except BufferError:
            self._overloaded.set()
            raise

    def _emit(self, event: stt.SpeechEvent) -> None:
        if self._output_pending >= 128:
            raise APIConnectionError("Nemotron event buffer overloaded", retryable=False)
        self._output_pending += 1
        self._event_ch.send_nowait(event)

    async def __anext__(self) -> stt.SpeechEvent:
        event = await super().__anext__()
        self._output_pending -= 1
        return event

    async def _message(self, ws: aiohttp.ClientWebSocketResponse) -> dict[str, Any]:
        message = await ws.receive()
        if message.type != aiohttp.WSMsgType.TEXT:
            raise APIConnectionError(
                "Nemotron sidecar disconnected", retryable=not self._sent_audio
            )
        try:
            data = message.json()
        except (ValueError, TypeError):
            raise APIConnectionError("Invalid Nemotron protocol message", retryable=False) from None
        if not isinstance(data, dict) or not isinstance(data.get("type"), str):
            raise APIConnectionError("Invalid Nemotron protocol message", retryable=False)
        if data["type"] == "error":
            # Native errors may contain input text. Never include their payload in exceptions/logs.
            raise APIConnectionError("Nemotron sidecar rejected the stream", retryable=False)
        return data

    async def _run(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        trace = aiohttp.TraceConfig()

        async def deny_redirect(
            session: aiohttp.ClientSession,
            context: SimpleNamespace,
            params: aiohttp.TraceRequestRedirectParams,
        ) -> None:
            raise APIConnectionError("Nemotron websocket redirect refused", retryable=False)

        trace.on_request_redirect.append(deny_redirect)
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(
                        total=None,
                        connect=self._conn_options.timeout,
                        sock_read=self._conn_options.timeout,
                    ),
                    trace_configs=[trace],
                ) as session,
                session.ws_connect(
                    self._provider.base_url + "/v1/realtime",
                    max_msg_size=65536,
                    timeout=aiohttp.ClientWSTimeout(ws_close=0.5),
                    heartbeat=10,
                    headers={"Origin": self._provider.base_url},
                ) as ws,
            ):
                self._ws = ws
                async with asyncio.timeout(self._conn_options.timeout):
                    created = await self._message(ws)
                    if created["type"] != "session.created":
                        raise APIConnectionError(
                            "Invalid Nemotron session handshake", retryable=False
                        )
                    await ws.send_json(
                        {
                            "type": "session.update",
                            "session": {
                                "sample_rate": 16000,
                                "language": "en",
                                "automatic_punctuation": False,
                                "verbatim": True,
                                "endpointing_ms": self._provider.endpointing_ms,
                            },
                        }
                    )
                    updated = await self._message(ws)
                    if updated["type"] != "session.updated":
                        raise APIConnectionError(
                            "Invalid Nemotron session handshake", retryable=False
                        )
                sender = asyncio.create_task(self._send(ws))
                receiver = asyncio.create_task(self._receive(ws))
                overload = asyncio.create_task(self._wait_overload())
                tasks = [sender, receiver, overload]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        except TimeoutError:
            raise APITimeoutError(
                "Nemotron connection timed out", retryable=not self._sent_audio
            ) from None
        except (aiohttp.ClientError, OSError):
            raise APIConnectionError(
                "Nemotron sidecar unavailable; run the explicit local setup and launcher",
                retryable=not self._sent_audio,
            ) from None
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._ws = None

    async def _wait_overload(self) -> None:
        await self._overloaded.wait()
        raise APIConnectionError("Nemotron input buffer overloaded", retryable=False)

    async def _send(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        audio_bytes = 0
        async for item in self._input_ch:
            if self._overloaded.is_set():
                raise APIConnectionError("Nemotron input buffer overloaded", retryable=False)
            if isinstance(item, rtc.AudioFrame):
                self._sent_audio = True
                pcm = bytes(item.data)
                if sys.byteorder != "little":
                    samples = array("h", pcm)
                    samples.byteswap()
                    pcm = samples.tobytes()
                async with asyncio.timeout(self._conn_options.timeout):
                    await ws.send_bytes(pcm)
                audio_bytes += len(pcm)
            elif audio_bytes:
                self._committed.clear()
                self._commit_pending = True
                try:
                    async with asyncio.timeout(self._provider.finalize_timeout):
                        await ws.send_json({"type": "input_audio_buffer.commit"})
                        await self._committed.wait()
                except TimeoutError:
                    raise APITimeoutError(
                        "Nemotron finalization timed out", retryable=False
                    ) from None
                self._emit(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.RECOGNITION_USAGE,
                        recognition_usage=stt.RecognitionUsage(audio_duration=audio_bytes / 32000),
                    )
                )
                audio_bytes = 0

    async def _receive(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            data = await self._message(ws)
            kind = data["type"]
            if kind in (_PREFIX + "delta", _PREFIX + "completed"):
                final = kind.endswith(".completed")
                text = data.get("transcript" if final else "delta")
                if not isinstance(text, str):
                    raise APIConnectionError("Invalid Nemotron transcript event", retryable=False)
                if final:
                    self._partial = ""
                else:
                    if not text:
                        continue
                    self._partial += text
                    if len(self._partial) > 32768:
                        raise APIConnectionError(
                            "Nemotron transcript buffer overloaded", retryable=False
                        )
                    text = self._partial
                if text:
                    if not self._started:
                        self._started = True
                        self._emit(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
                    self._emit(
                        stt.SpeechEvent(
                            type=(
                                stt.SpeechEventType.FINAL_TRANSCRIPT
                                if final
                                else stt.SpeechEventType.INTERIM_TRANSCRIPT
                            ),
                            alternatives=[stt.SpeechData(language=LanguageCode("en"), text=text)],
                        )
                    )
                if final and self._started:
                    self._started = False
                    self._emit(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
            elif kind == "input_audio_buffer.committed":
                if not self._commit_pending:
                    raise APIConnectionError("Unexpected Nemotron commit", retryable=False)
                self._commit_pending = False
                self._committed.set()
            elif kind != "input_audio_buffer.cleared":
                raise APIConnectionError("Unexpected Nemotron protocol event", retryable=False)

    async def aclose(self) -> None:
        if self._ws is not None and not self._ws.closed and not self._task.done():
            with contextlib.suppress(aiohttp.ClientError, TimeoutError, OSError):
                async with asyncio.timeout(0.5):
                    await self._ws.send_json({"type": "input_audio_buffer.clear"})
        await super().aclose()
