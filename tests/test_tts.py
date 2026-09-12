import asyncio

import numpy as np
import pytest
from aiohttp import web
from conftest import make_wav
from livekit.agents import AgentSession, APIConnectOptions, APIError, APIStatusError, tokenize, tts
from livekit.plugins import voicebox


async def mock_backend(server, profile, *, wav=None, status=200):
    calls = []
    counters = {"profiles": 0}

    async def handle(request):
        if request.path == "/profiles":
            counters["profiles"] += 1
            return web.json_response([profile, dict(profile, id="voice-2", name="Other Voice")])
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
        calls.append(await request.json())
        return web.Response(body=wav if wav is not None else make_wav(), status=status)

    return await server(handle), calls, counters


async def test_provider_frames_metrics_and_session(server, profile):
    url, calls, _ = await mock_backend(server, profile, wav=make_wav(rate=48000, channels=2))
    provider = voicebox.TTS(profile="Test Voice", base_url=url)
    assert provider.provider == "Voicebox"
    assert provider.model == "qwen:0.6B"
    assert not provider.capabilities.streaming
    session = AgentSession(tts=provider)
    assert session.tts is provider
    metrics = []
    provider.on("metrics_collected", metrics.append)
    async with provider.synthesize("Hello") as stream:
        assert isinstance(stream, tts.ChunkedStream)
        frames = [event.frame async for event in stream]
    assert frames
    assert all(f.sample_rate == 24000 and f.num_channels == 1 for f in frames)
    assert all(f.samples_per_channel <= 480 for f in frames)
    assert sum(f.samples_per_channel for f in frames) == 2400
    assert len(metrics) == 1
    assert metrics[0].audio_duration == pytest.approx(0.1)
    assert metrics[0].characters_count == 5
    assert metrics[0].ttfb >= 0
    assert not metrics[0].streamed
    assert metrics[0].metadata.model_provider == "Voicebox"
    assert stream.timings.http_complete is not None
    assert calls[0]["model_size"] == "0.6B"
    await provider.aclose()


async def test_snapshot_cache_update_and_explicit_none(server, profile):
    url, calls, counters = await mock_backend(server, profile)
    provider = voicebox.TTS(profile="Test Voice", base_url=url, instruct="Calm")
    old = provider.synthesize("Old")
    provider.update_options(profile="Other Voice", language="fr", instruct=None)
    async with old:
        await old.collect()
    for text in ["New", "Cached"]:
        async with provider.synthesize(text) as stream:
            await stream.collect()
    assert calls[0]["profile_id"] == "voice-1"
    assert calls[0]["instruct"] == "Calm"
    assert calls[1]["profile_id"] == "voice-2"
    assert calls[1]["language"] == "fr"
    assert "instruct" not in calls[1]
    assert counters["profiles"] == 2
    await provider.resolve_profile(refresh=True)
    assert counters["profiles"] == 3
    await provider.aclose()


async def test_late_old_resolution_cannot_overwrite_current_cache(server, profile):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return web.json_response([profile, dict(profile, id="new", name="New")])

    provider = voicebox.TTS(profile="Test Voice", base_url=await server(handle))
    old = asyncio.create_task(provider.resolve_profile())
    await entered.wait()
    provider.update_options(profile="New")
    assert (await provider.resolve_profile()).id == "new"
    release.set()
    assert (await old).id == "voice-1"
    assert (await provider.resolve_profile()).id == "new"
    assert calls == 2
    await provider.aclose()


@pytest.mark.parametrize(
    "wav,status",
    [(b"corrupt", 200), (b"SECRET TEXT", 400), (b"SECRET", 500), (b"SECRET", 499)],
)
async def test_errors_never_retry(server, profile, wav, status):
    url, calls, _ = await mock_backend(server, profile, wav=wav, status=status)
    provider = voicebox.TTS(profile="voice-1", base_url=url)
    with pytest.raises(APIError) as error:
        async with provider.synthesize(
            "Hello", conn_options=APIConnectOptions(max_retry=5)
        ) as stream:
            await stream.collect()
    assert not error.value.retryable
    assert "SECRET" not in str(error.value)
    if status not in (200, 499):
        assert isinstance(error.value, APIStatusError)
        assert error.value.status_code == status
    assert len(calls) == 1
    if status in (499, 500):
        from livekit.plugins.voicebox.errors import BackendUncertainError

        with pytest.raises(BackendUncertainError):
            await provider.aclose()
    else:
        await provider.aclose()


async def test_native_sentence_adapter(server, profile):
    url, calls, _ = await mock_backend(server, profile)
    provider = voicebox.TTS(profile="voice-1", base_url=url)
    adapter = tts.StreamAdapter(
        tts=provider,
        sentence_tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=1),
    )
    async with adapter.stream() as stream:
        stream.push_text("This is sentence one. This is sentence two. This is sentence three.")
        stream.end_input()
        frames = [event.frame async for event in stream]
    assert len(calls) == 3
    # Agents 1.8.1's native adapter adds one 10 ms final silence marker.
    assert sum(frame.samples_per_channel for frame in frames) == 7440
    samples = np.concatenate([np.frombuffer(frame.data, dtype="<i2") for frame in frames])
    assert np.count_nonzero(samples) == 7200
    await adapter.aclose()
    await provider.aclose()


async def test_cancellation_during_emission_stops_pushes(server, profile, monkeypatch):
    url, _, _ = await mock_backend(server, profile, wav=make_wav(seconds=20))
    provider = voicebox.TTS(profile="voice-1", base_url=url)
    pushed = []
    first_push = asyncio.Event()
    original = tts.AudioEmitter.push

    def record(self, data):
        pushed.append(data)
        first_push.set()
        return original(self, data)

    monkeypatch.setattr(tts.AudioEmitter, "push", record)
    stream = provider.synthesize("Long synthetic output")
    await first_push.wait()
    await stream.aclose()
    count = len(pushed)
    await asyncio.sleep(0.01)
    assert 0 < count < 1000
    assert len(pushed) == count
    assert [event async for event in stream] == []
    await provider.aclose()


async def test_cancel_consumer_before_response(server, profile):
    entered, release = asyncio.Event(), asyncio.Event()
    url, _, _ = await mock_backend(server, profile)
    provider = voicebox.TTS(profile="voice-1", base_url=url)
    original = provider._client.synthesize_wav

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return await original(**kwargs)

    provider._client.synthesize_wav = delayed
    stream = provider.synthesize("Hello")
    consumer = asyncio.create_task(stream.collect())
    await entered.wait()
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    release.set()
    assert stream.done
    assert provider.backend_state == "idle"
    await provider.aclose()


async def test_cancel_submitted_stream_suppresses_audio_and_holds_next_turn(server, profile):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handle(request):
        if request.path == "/profiles":
            return web.json_response([profile])
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
        text = (await request.json())["text"]
        calls.append(text)
        if text == "A":
            entered.set()
            await release.wait()
        return web.Response(body=make_wav(value=0.1 if text == "A" else 0.7))

    provider = voicebox.TTS(profile="voice-1", base_url=await server(handle))
    stream_a = provider.synthesize("A")
    a = asyncio.create_task(stream_a.collect())
    await entered.wait()
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    assert provider.backend_state == "draining"
    stream_b = provider.synthesize("B")
    b = asyncio.create_task(stream_b.collect())
    await asyncio.sleep(0.01)
    assert calls == ["A"]
    release.set()
    audio = await b
    assert np.frombuffer(audio.data, dtype="<i2").min() > 20000
    assert [event async for event in stream_a] == []
    assert calls == ["A", "B"]
    await stream_b.aclose()
    await provider.aclose()


async def test_missing_model_never_posts(server, profile):
    posts = 0

    async def handle(request):
        nonlocal posts
        if request.path == "/profiles":
            return web.json_response([profile])
        if request.path == "/models/status":
            return web.json_response(
                {
                    "models": [
                        {
                            "model_name": "qwen-tts-0.6B",
                            "downloaded": False,
                            "loaded": False,
                            "downloading": False,
                        }
                    ]
                }
            )
        posts += 1
        return web.Response(body=make_wav())

    provider = voicebox.TTS(profile="voice-1", base_url=await server(handle))
    with pytest.raises(APIStatusError, match="already be downloaded"):
        async with provider.synthesize("Hello") as stream:
            await stream.collect()
    assert posts == 0
    await provider.aclose()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_concurrent_requests": 2},
        {"sample_rate": 0},
        {"request_timeout": float("inf")},
        {"engine": "kokoro"},
        {"model_size": "1.7B"},
    ],
)
def test_bad_constructor(kwargs):
    from livekit.plugins.voicebox.errors import ConfigurationError

    with pytest.raises((ValueError, ConfigurationError)):
        voicebox.TTS(profile="Test", **kwargs)


async def test_pcm_matches_source(server, profile):
    url, _, _ = await mock_backend(server, profile, wav=make_wav(value=0.5))
    provider = voicebox.TTS(profile="voice-1", base_url=url)
    async with provider.synthesize("Hello") as stream:
        audio = await stream.collect()
    samples = np.frombuffer(audio.data, dtype="<i2")
    assert np.all(samples == 16383)
    await provider.aclose()


async def test_fifty_sequential_synthetic_requests_and_closed_lifecycle(server, profile):
    from livekit.plugins.voicebox.errors import ClientClosedError

    url, calls, counters = await mock_backend(server, profile)
    provider = voicebox.TTS(profile="voice-1", base_url=url)
    for _ in range(50):
        async with provider.synthesize("Synthetic stability request") as stream:
            await stream.collect()
        assert provider.backend_state == "idle"
    assert len(calls) == 50
    assert counters["profiles"] == 1
    assert len(await provider.list_profiles()) == 2
    await provider.aclose()
    assert provider.backend_state == "closed"
    with pytest.raises(ClientClosedError):
        provider.synthesize("Closed")


async def test_foreground_resolution_deadline(server, profile):
    from livekit.agents import APITimeoutError

    async def handle(request):
        await asyncio.sleep(0.05)
        return web.json_response([profile])

    provider = voicebox.TTS(profile="voice-1", base_url=await server(handle), request_timeout=0.01)
    with pytest.raises(APITimeoutError) as error:
        async with provider.synthesize("Hello") as stream:
            await stream.collect()
    assert not error.value.retryable
    assert provider.backend_state == "idle"
    await provider.aclose()
