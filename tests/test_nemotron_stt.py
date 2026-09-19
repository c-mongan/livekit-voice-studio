from __future__ import annotations

import asyncio
import hashlib
import io
import json
import shutil
from array import array
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from aiohttp import WSMsgType, web
from livekit import rtc
from livekit.agents import APIConnectionError, APIConnectOptions, APITimeoutError, stt

from examples.nemotron_stt import NemotronSTT, _AudioChannel

PREFIX = "conversation.item.input_audio_transcription."


def frame(rate: int = 16000, channels: int = 1, milliseconds: int = 20) -> rtc.AudioFrame:
    samples = rate * milliseconds // 1000
    values = array("h", [1000, -1000] if channels == 2 else [1000])
    return rtc.AudioFrame((values * samples).tobytes(), rate, channels, samples)


class NativeServer:
    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.audio: list[bytes] = []
        self.sockets: list[web.WebSocketResponse] = []
        self.closed: list[web.WebSocketResponse] = []
        self.audio_received = asyncio.Event()
        self.commit_received = asyncio.Event()
        self.release_commit = asyncio.Event()
        self.release_commit.set()
        self.ready = True
        self.capabilities = ["asr"]
        self.failure = ""
        self.partial = True
        self.final = "Hello world."
        self.empty_deltas = False
        self.automatic_endpoint = False

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if request.path == "/ready":
            return web.json_response(
                {"ready": self.ready, "capabilities": self.capabilities, "device": "cpu"}
            )
        assert request.path == "/v1/realtime"
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.append(ws)
        await ws.send_json({"type": "session.created", "session": {"sample_rate": 16000}})
        try:
            async for message in ws:
                if message.type == WSMsgType.BINARY:
                    self.audio.append(message.data)
                    self.audio_received.set()
                    if self.failure == "close":
                        await ws.close()
                    elif self.failure == "error":
                        await ws.send_json(
                            {"type": "error", "error": {"message": "PRIVATE TRANSCRIPT"}}
                        )
                    elif self.failure == "malformed":
                        await ws.send_str("PRIVATE TRANSCRIPT")
                    elif self.partial:
                        await ws.send_json({"type": PREFIX + "delta", "delta": "Hello"})
                        await ws.send_json({"type": PREFIX + "delta", "delta": " world"})
                        if self.empty_deltas:
                            for _ in range(5):
                                await ws.send_json({"type": PREFIX + "delta", "delta": ""})
                    if self.automatic_endpoint:
                        await ws.send_json({"type": PREFIX + "completed", "transcript": self.final})
                elif message.type == WSMsgType.TEXT:
                    data = json.loads(message.data)
                    self.messages.append(data)
                    if data["type"] == "session.update":
                        await ws.send_json({"type": "session.updated", "session": data["session"]})
                    elif data["type"] == "input_audio_buffer.commit":
                        self.commit_received.set()
                        await self.release_commit.wait()
                        await ws.send_json({"type": PREFIX + "completed", "transcript": self.final})
                        await ws.send_json({"type": "input_audio_buffer.committed"})
                    elif data["type"] == "input_audio_buffer.clear":
                        await ws.send_json({"type": "input_audio_buffer.cleared"})
        finally:
            self.closed.append(ws)
        return ws


@pytest.fixture
async def native(server: Any) -> tuple[NativeServer, str]:
    runtime = NativeServer()
    return runtime, await server(runtime.handle)


async def collect(stream: stt.SpeechStream) -> list[stt.SpeechEvent]:
    async with asyncio.timeout(3):
        return [event async for event in stream]


async def test_native_event_mapping_and_commit(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    async with NemotronSTT(base_url=url) as provider:
        assert provider.capabilities.streaming and provider.capabilities.interim_results
        stream = provider.stream()
        stream.push_frame(frame())
        stream.end_input()
        events = await collect(stream)
        await stream.aclose()
    types = [event.type for event in events]
    assert types == [
        stt.SpeechEventType.START_OF_SPEECH,
        stt.SpeechEventType.INTERIM_TRANSCRIPT,
        stt.SpeechEventType.INTERIM_TRANSCRIPT,
        stt.SpeechEventType.FINAL_TRANSCRIPT,
        stt.SpeechEventType.END_OF_SPEECH,
        stt.SpeechEventType.RECOGNITION_USAGE,
    ]
    assert [e.alternatives[0].text for e in events if e.alternatives] == [
        "Hello",
        "Hello world",
        "Hello world.",
    ]
    assert events[-1].recognition_usage.audio_duration == pytest.approx(0.02)
    assert runtime.audio == [bytes(frame().data)]
    assert runtime.messages[0]["session"]["sample_rate"] == 16000
    assert runtime.messages[-1]["type"] == "input_audio_buffer.commit"


async def test_resamples_and_downmixes_once(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    runtime.partial = False
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        for _ in range(5):
            stream.push_frame(frame(48000, 2))
        stream.end_input()
        await collect(stream)
        await stream.aclose()
    pcm = b"".join(runtime.audio)
    assert len(pcm) == 3200
    # RTC's high-quality resampler dithers silence by at most one PCM16 unit.
    assert max(abs(value) for value in array("h", pcm)) <= 1


async def test_final_waits_for_native_commit_ack(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    runtime.release_commit.clear()
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        stream.push_frame(frame())
        stream.end_input()
        collected = asyncio.create_task(collect(stream))
        await asyncio.wait_for(runtime.commit_received.wait(), 1)
        assert not collected.done()
        runtime.release_commit.set()
        events = await collected
        assert any(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events)
        await stream.aclose()


async def test_commit_timeout_is_bounded(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    runtime.release_commit.clear()
    async with NemotronSTT(base_url=url, finalize_timeout=0.05) as provider:
        stream = provider.stream(conn_options=APIConnectOptions(max_retry=2))
        stream.push_frame(frame())
        stream.end_input()
        try:
            with pytest.raises(APITimeoutError, match="finalization") as error:
                await collect(stream)
            assert not error.value.retryable
        finally:
            runtime.release_commit.set()
            await stream.aclose()
    assert len(runtime.sockets) == 1  # audio is never silently retried/replayed


@pytest.mark.parametrize("failure", ["close", "error", "malformed"])
async def test_stream_errors_are_private_and_not_replayed(
    native: tuple[NativeServer, str], failure: str
) -> None:
    runtime, url = native
    runtime.failure = failure
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream(conn_options=APIConnectOptions(max_retry=2))
        stream.push_frame(frame())
        stream.end_input()
        with pytest.raises(APIConnectionError) as error:
            await collect(stream)
        assert "PRIVATE" not in str(error.value)
        assert not error.value.retryable
        await stream.aclose()
    assert len(runtime.sockets) == 1


async def test_flush_allows_multiple_turns(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    runtime.partial = False
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        stream.push_frame(frame())
        stream.flush()
        stream.push_frame(frame())
        stream.end_input()
        events = await collect(stream)
        await stream.aclose()
    assert sum(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events) == 2
    assert len(runtime.sockets) == 1


async def test_empty_input_does_not_invent_speech(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        stream.end_input()
        assert await collect(stream) == []
        await stream.aclose()
    assert not runtime.commit_received.is_set()


async def test_silent_final_emits_usage_not_speech(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    runtime.partial = False
    runtime.final = ""
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        stream.push_frame(frame())
        stream.end_input()
        events = await collect(stream)
        await stream.aclose()
    assert [e.type for e in events] == [stt.SpeechEventType.RECOGNITION_USAGE]


async def test_close_only_cancels_owned_socket(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    async with NemotronSTT(base_url=url) as provider:
        first, second = provider.stream(), provider.stream()
        first.push_frame(frame())
        await asyncio.wait_for(runtime.audio_received.wait(), 1)
        await first.aclose()
        second.push_frame(frame())
        second.end_input()
        assert any(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in await collect(second))
        await second.aclose()
    assert {"type": "input_audio_buffer.clear"} in runtime.messages
    assert len(runtime.sockets) == 2


async def test_input_queue_is_bounded_before_connect(native: tuple[NativeServer, str]) -> None:
    _, url = native
    async with NemotronSTT(base_url=url, max_buffer_seconds=0.04) as provider:
        stream = provider.stream()
        stream.push_frame(frame())
        stream.push_frame(frame())
        with pytest.raises(BufferError, match="during connection"):
            stream.push_frame(frame())
        assert stream.buffered_audio_bytes <= 1280
        await stream.aclose()


async def test_oversized_frame_is_rejected(native: tuple[NativeServer, str]) -> None:
    _, url = native
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        with pytest.raises(ValueError, match="200"):
            stream.push_frame(frame(milliseconds=201))
        await stream.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://0.0.0.0:8766",
        "http://user:secret@127.0.0.1:8766",
        "http://127.0.0.1:8766/path",
        "http://127.0.0.1:8766?secret=value",
    ],
)
def test_only_plain_loopback_urls(url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        NemotronSTT(base_url=url)


@pytest.mark.parametrize("sample_rate", [8000, 24000, 48000])
def test_model_requires_16khz(sample_rate: int) -> None:
    with pytest.raises(ValueError, match="16000"):
        NemotronSTT(sample_rate=sample_rate)


async def test_readiness_requires_transcription(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    async with NemotronSTT(base_url=url) as provider:
        assert await provider.is_ready()
        runtime.capabilities = ["speech"]
        assert not await provider.is_ready()
        runtime.capabilities = ["asr"]
        runtime.ready = False
        assert not await provider.is_ready()


async def test_unavailable_runtime_reports_connection_error(server: Any) -> None:
    async def denied(_: web.Request) -> web.Response:
        return web.Response(status=503, text="PRIVATE TRANSCRIPT")

    url = await server(denied)
    async with NemotronSTT(base_url=url) as provider:
        assert not await provider.is_ready()
        stream = provider.stream(conn_options=APIConnectOptions(max_retry=0))
        stream.end_input()
        with pytest.raises(APIConnectionError) as error:
            await collect(stream)
        assert "PRIVATE" not in str(error.value)
        await stream.aclose()


async def test_websocket_redirect_never_receives_audio(server: Any) -> None:
    contacted = False

    async def target(_: web.Request) -> web.Response:
        nonlocal contacted
        contacted = True
        return web.Response()

    target_url = await server(target)

    async def redirect(_: web.Request) -> web.Response:
        raise web.HTTPFound(target_url)

    async with NemotronSTT(base_url=await server(redirect)) as provider:
        stream = provider.stream(conn_options=APIConnectOptions(max_retry=0))
        stream.push_frame(frame())
        stream.end_input()
        with pytest.raises(APIConnectionError, match="redirect"):
            await collect(stream)
        await stream.aclose()
    assert not contacted


def test_setup_pins_only_approved_model() -> None:
    from tools import setup_nemotron

    assert setup_nemotron.MODEL_SIZE == 699872960
    assert setup_nemotron.MODEL_SHA256 == (
        "d9a01898d2a611c8764e23a1c2f45e70bbd5a425dc4de93692ac951dd603812d"
    )
    assert "ebe59e5a817142986528bbbee5dba8db7b38ed50" in setup_nemotron.MODEL_URL


def test_setup_sidecar_uses_explicit_cpu_path() -> None:
    from pathlib import Path

    from tools.setup_nemotron import sidecar_command

    command = sidecar_command(Path("runtime"), Path("approved.gguf"), 8766)
    assert command[:2] == ["runtime/build/bin/nemo-speech", "serve"]
    assert command[command.index("--device") + 1] == "cpu"
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "8766"
    assert command[command.index("--asr-model") + 1].endswith("/approved.gguf")
    assert "--no-ui" in command
    assert "--asr.batching.enabled=false" in command


@pytest.fixture
def setup_directory() -> Iterator[Path]:
    root = Path("experiments/nemotron/.native") / ("test-" + uuid4().hex)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def test_setup_checksum_rejects_corruption(
    setup_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import setup_nemotron

    data = b"synthetic model fixture; never loaded"
    model = setup_directory / "fixture.gguf"
    model.write_bytes(data)
    monkeypatch.setattr(setup_nemotron, "MODEL_SIZE", len(data))
    monkeypatch.setattr(setup_nemotron, "MODEL_SHA256", hashlib.sha256(data).hexdigest())
    setup_nemotron.verify_model(model)
    model.write_bytes(b"x" * len(data))
    with pytest.raises(ValueError, match="SHA-256"):
        setup_nemotron.verify_model(model)


def test_setup_download_is_size_bounded_and_removes_partial(
    setup_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import setup_nemotron

    monkeypatch.setattr(setup_nemotron, "MODEL_SIZE", 3)
    monkeypatch.setattr(setup_nemotron, "urlopen", lambda *a, **kw: io.BytesIO(b"oversized"))
    monkeypatch.setattr(setup_nemotron, "check_disk", lambda _: None)
    destination = setup_directory / "fixture.gguf"
    with pytest.raises(ValueError, match="byte limit"):
        setup_nemotron.download_model(destination)
    assert not destination.exists()
    assert not destination.with_suffix(".gguf.part").exists()


def test_setup_download_never_overwrites_existing_model(
    setup_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import setup_nemotron

    destination = setup_directory / "fixture.gguf"
    destination.write_bytes(b"existing")
    with pytest.raises(ValueError, match="approved"):
        setup_nemotron.download_model(destination)
    assert destination.read_bytes() == b"existing"


async def test_http_handshake_timeout_is_bounded(server: Any) -> None:
    release = asyncio.Event()

    async def stalled(_: web.Request) -> web.Response:
        await release.wait()
        return web.Response()

    async with NemotronSTT(base_url=await server(stalled)) as provider:
        stream = provider.stream(conn_options=APIConnectOptions(max_retry=0, timeout=0.05))
        stream.end_input()
        try:
            with pytest.raises(APITimeoutError):
                await collect(stream)
        finally:
            release.set()
            await stream.aclose()


async def test_oversized_declared_frame_is_rejected(
    native: tuple[NativeServer, str],
) -> None:
    _, url = native
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        with pytest.raises(ValueError, match="sample count"):
            stream.push_frame(rtc.AudioFrame(bytes(6400), 16000, 1, 320))
        await stream.aclose()


async def test_non_consuming_client_has_bounded_event_queue(
    native: tuple[NativeServer, str],
) -> None:
    _, url = native
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        for _ in range(128):
            stream._emit(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
        with pytest.raises(APIConnectionError, match="event buffer"):
            stream._emit(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
        await stream.aclose()


async def test_continuous_stream_delivers_two_native_finals_without_flush(
    native: tuple[NativeServer, str],
) -> None:
    runtime, url = native
    runtime.automatic_endpoint = True
    turns: list[list[stt.SpeechEvent]] = []
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        try:
            for _ in range(2):
                stream.push_frame(frame())
                turn: list[stt.SpeechEvent] = []
                async with asyncio.timeout(1):
                    async for event in stream:
                        turn.append(event)
                        if event.type == stt.SpeechEventType.END_OF_SPEECH:
                            break
                turns.append(turn)
                assert not stream._input_ch.closed
                assert not runtime.commit_received.is_set()
        finally:
            await stream.aclose()
    assert len(runtime.sockets) == 1
    for turn in turns:
        assert [event.type for event in turn] == [
            stt.SpeechEventType.START_OF_SPEECH,
            stt.SpeechEventType.INTERIM_TRANSCRIPT,
            stt.SpeechEventType.INTERIM_TRANSCRIPT,
            stt.SpeechEventType.FINAL_TRANSCRIPT,
            stt.SpeechEventType.END_OF_SPEECH,
        ]
        assert turn[-2].alternatives[0].text == "Hello world."
        assert turn[1].alternatives[0].text == "Hello"


async def test_empty_native_deltas_do_not_repeat_interim_during_silence(
    native: tuple[NativeServer, str],
) -> None:
    runtime, url = native
    runtime.empty_deltas = True
    async with NemotronSTT(base_url=url) as provider:
        stream = provider.stream()
        stream.push_frame(frame())
        stream.end_input()
        events = await collect(stream)
        await stream.aclose()
    partials = [
        event.alternatives[0].text
        for event in events
        if event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT
    ]
    assert partials == ["Hello", "Hello world"]


async def test_public_vad_commit_finalizes_two_turns_without_ending_input(
    native: tuple[NativeServer, str],
) -> None:
    runtime, url = native
    provider = NemotronSTT(base_url=url)
    async with provider:
        stream = provider.stream()
        for _ in range(2):
            stream.push_frame(frame())
            provider.commit_utterance()
            # A duplicate callback with no intervening audio must not create another final.
            provider.commit_utterance()
            events: list[stt.SpeechEvent] = []
            async with asyncio.timeout(1):
                async for event in stream:
                    events.append(event)
                    if event.type == stt.SpeechEventType.RECOGNITION_USAGE:
                        break
            assert any(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events)
            assert any(e.type == stt.SpeechEventType.END_OF_SPEECH for e in events)
            assert not stream._input_ch.closed
        await stream.aclose()
    commits = [m for m in runtime.messages if m["type"] == "input_audio_buffer.commit"]
    assert len(commits) == 2
    assert len(runtime.sockets) == 1


async def test_public_vad_commit_ignores_closed_ended_and_failed_streams(
    native: tuple[NativeServer, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, url = native
    provider = NemotronSTT(base_url=url)
    async with provider:
        closed = provider.stream()
        ended = provider.stream()
        failed = provider.stream(conn_options=APIConnectOptions(max_retry=0))
        await closed.aclose()
        ended.end_input()
        runtime.failure = "close"
        failed.push_frame(frame())
        with pytest.raises(APIConnectionError):
            await collect(failed)
        runtime.failure = ""

        def forbidden() -> None:
            raise AssertionError("inactive stream was flushed")

        for inactive in (closed, ended, failed):
            monkeypatch.setattr(inactive, "flush", forbidden)
        provider.commit_utterance()
        await ended.aclose()
        await failed.aclose()
    provider.commit_utterance()  # Late VAD callback during provider shutdown is harmless.


async def test_public_vad_commit_only_flushes_provider_owned_streams(
    native: tuple[NativeServer, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, url = native
    first, second = NemotronSTT(base_url=url), NemotronSTT(base_url=url)
    calls: list[str] = []
    async with first, second:
        first_streams = [first.stream(), first.stream()]
        second_stream = second.stream()
        for stream in first_streams:
            monkeypatch.setattr(stream, "flush", lambda: calls.append("first"))
        monkeypatch.setattr(second_stream, "flush", lambda: calls.append("second"))
        first.commit_utterance()
        assert calls == ["first", "first"]
        calls.clear()
        second.commit_utterance()
        assert calls == ["second"]


async def test_overload_reports_finalization_phase(native: tuple[NativeServer, str]) -> None:
    runtime, url = native
    runtime.release_commit.clear()
    async with NemotronSTT(base_url=url, max_buffer_seconds=0.04, finalize_timeout=0.2) as provider:
        stream = provider.stream()
        try:
            stream.push_frame(frame())
            await asyncio.wait_for(runtime.audio_received.wait(), 1)
            stream.flush()
            await asyncio.wait_for(runtime.commit_received.wait(), 1)
            for _ in range(12):
                stream.push_frame(frame())
            with pytest.raises(BufferError, match="during finalization"):
                stream.push_frame(frame())
            with pytest.raises(APIConnectionError, match="during finalization"):
                await collect(stream)
            assert stream.buffered_audio_bytes <= 7680
        finally:
            runtime.release_commit.set()
            await stream.aclose()


async def test_retry_resets_overload_phase_to_connection(server: Any) -> None:
    attempts = 0
    reconnecting = asyncio.Event()
    release = asyncio.Event()

    async def handle(request: web.Request) -> web.StreamResponse:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            # Never send session.created: the first attempt times out in handshake.
            await release.wait()
            return ws
        reconnecting.set()
        await release.wait()
        return web.Response(status=503)

    url = await server(handle)
    async with NemotronSTT(base_url=url, max_buffer_seconds=0.04) as provider:
        stream = provider.stream(
            conn_options=APIConnectOptions(max_retry=1, timeout=0.1, retry_interval=0)
        )
        try:
            await asyncio.wait_for(reconnecting.wait(), 2)
            stream.push_frame(frame())
            stream.push_frame(frame())
            with pytest.raises(BufferError, match="during connection"):
                stream.push_frame(frame())
        finally:
            release.set()
            await stream.aclose()


async def test_finalization_retains_next_audio_within_deadline_budget(
    native: tuple[NativeServer, str],
) -> None:
    runtime, url = native
    runtime.release_commit.clear()
    async with NemotronSTT(base_url=url, max_buffer_seconds=0.04, finalize_timeout=0.2) as provider:
        stream = provider.stream()
        try:
            stream.push_frame(frame())
            await asyncio.wait_for(runtime.audio_received.wait(), 1)
            stream.flush()
            await asyncio.wait_for(runtime.commit_received.wait(), 1)
            # Audio continues during commit processing; it must not be lost or
            # sent into the preceding utterance before its acknowledgement.
            for _ in range(5):
                stream.push_frame(frame())
            assert len(runtime.audio) == 1
            stream.end_input()
            runtime.release_commit.set()
            events = await collect(stream)
            assert len(runtime.audio) == 6
            assert sum(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events) == 2
            assert stream._audio.byte_limit == provider.max_buffer_bytes
        finally:
            runtime.release_commit.set()
            await stream.aclose()


async def test_finalization_reserve_shrinks_only_after_backlog_drains() -> None:
    channel = _AudioChannel(1280)
    channel.reserve_finalization(6400)
    for _ in range(4):
        channel.send_nowait(frame())
    channel.release_finalization()
    assert channel.byte_limit == 7680
    channel.recv_nowait()
    assert channel.audio_bytes == 1920 and channel.byte_limit == 7680
    channel.recv_nowait()
    assert channel.audio_bytes == 1280 and channel.byte_limit == 1280
    with pytest.raises(BufferError):
        channel.send_nowait(frame())
    channel.recv_nowait()
    channel.send_nowait(frame())
    assert channel.audio_bytes == 1280


async def test_small_frames_still_obey_item_bound() -> None:
    channel = _AudioChannel(100000)
    for _ in range(2048):
        channel.send_nowait(frame(milliseconds=1))
    assert channel.audio_bytes < channel.byte_limit
    with pytest.raises(BufferError):
        channel.send_nowait(frame(milliseconds=1))


@pytest.mark.parametrize("finish_delay", [0.22, 0.36])
async def test_heartbeat_does_not_preempt_valid_native_finalization(
    native: tuple[NativeServer, str], monkeypatch: pytest.MonkeyPatch, finish_delay: float
) -> None:
    import aiohttp

    runtime, url = native
    runtime.release_commit.clear()
    original_connect = aiohttp.ClientSession.ws_connect

    def scaled_connect(self: Any, *args: Any, **kwargs: Any) -> Any:
        # Compress heartbeat time so this exercises the real ping/pong failure
        # path without a 15-second CI wait. Native processing blocks its reader.
        kwargs["heartbeat"] /= 100
        return original_connect(self, *args, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", scaled_connect)
    async with NemotronSTT(base_url=url, finalize_timeout=15) as provider:
        stream = provider.stream(conn_options=APIConnectOptions(max_retry=0))
        try:
            stream.push_frame(frame())
            await asyncio.wait_for(runtime.audio_received.wait(), 1)
            stream.end_input()
            await asyncio.wait_for(runtime.commit_received.wait(), 1)
            await asyncio.sleep(finish_delay)
            runtime.release_commit.set()
            events = await collect(stream)
            assert any(e.type == stt.SpeechEventType.FINAL_TRANSCRIPT for e in events)
        finally:
            runtime.release_commit.set()
            await stream.aclose()
