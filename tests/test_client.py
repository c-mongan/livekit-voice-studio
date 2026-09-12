import asyncio

import aiohttp
import pytest
from aiohttp import web
from conftest import make_wav
from livekit.plugins.voicebox.client import VoiceboxClient
from livekit.plugins.voicebox.errors import (
    AmbiguousProfileError,
    BackendUncertainError,
    ModelNotReadyError,
    ProfileNotFoundError,
    VoiceboxAPIError,
    VoiceboxConnectionError,
    VoiceboxTimeoutError,
)


async def test_discovery_and_payload(server, profile):
    payloads = []

    async def handle(request):
        if request.path == "/health":
            return web.json_response({"status": "healthy", "model_loaded": True, "future": 42})
        if request.path == "/profiles":
            return web.json_response([profile])
        if request.path == "/models/status":
            return web.json_response(
                {
                    "models": [
                        {
                            "model_name": "qwen-tts-0.6B",
                            "downloaded": True,
                            "loaded": False,
                            "downloading": False,
                        }
                    ]
                }
            )
        payloads.append(await request.json())
        return web.Response(body=make_wav(), content_type="audio/wav")

    client = VoiceboxClient(base_url=await server(handle))
    try:
        health = await client.health()
        assert health.ok and health.raw["future"] == 42
        assert not (await client.model_readiness()).loaded
        for selector in ["voice-1", "Test Voice", "test voice"]:
            assert (await client.resolve_profile(selector)).id == "voice-1"
        with pytest.raises(ProfileNotFoundError):
            await client.resolve_profile("missing")
        assert await client.synthesize_wav(profile_id="voice-1", text="Hello")
        assert payloads == [
            {
                "profile_id": "voice-1",
                "text": "Hello",
                "engine": "qwen",
                "model_size": "0.6B",
                "language": "en",
            }
        ]
        await client.synthesize_wav(profile_id="voice-1", text="Hi", instruct="Calm")
        assert payloads[-1]["instruct"] == "Calm"
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "names,selector",
    [
        (["Test", "Test"], "Test"),
        (["Test", "TEST"], "test"),
    ],
)
async def test_ambiguity(server, profile, names, selector):
    async def handle(request):
        return web.json_response([dict(profile, id=str(i), name=n) for i, n in enumerate(names)])

    client = VoiceboxClient(base_url=await server(handle))
    with pytest.raises(AmbiguousProfileError):
        await client.resolve_profile(selector)
    assert (await client.resolve_profile("0")).id == "0"
    await client.aclose()


@pytest.mark.parametrize("status", [400, 404, 422, 500, 503, 307])
async def test_safe_http_errors(server, status):
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        return web.json_response({"detail": "SECRET SYNTHESIS TEXT"}, status=status)

    client = VoiceboxClient(base_url=await server(handle))
    with pytest.raises(VoiceboxAPIError) as error:
        await client.synthesize_wav(profile_id="id", text="Hello")
    assert "SECRET" not in str(error.value)
    assert error.value.status_code == status
    assert calls == 1
    if status in (400, 404, 422):
        assert client.state == "idle"
        await client.aclose()
    else:
        assert client.state == "uncertain"
        with pytest.raises(BackendUncertainError):
            await client.aclose()


async def test_external_session_ownership(server):
    async def handle(request):
        return web.json_response({})

    async with aiohttp.ClientSession() as session:
        client = VoiceboxClient(base_url=await server(handle), session=session)
        await client.health()
        await client.aclose()
        assert not session.closed


async def test_discovery_timeout(server):
    async def handle(request):
        await asyncio.sleep(0.1)
        return web.json_response({})

    client = VoiceboxClient(base_url=await server(handle), request_timeout=0.01)
    with pytest.raises(VoiceboxTimeoutError):
        await client.health()
    assert client.state == "idle"
    await client.aclose()


async def test_connection_failure():
    client = VoiceboxClient(base_url="http://127.0.0.1:1")
    with pytest.raises(VoiceboxConnectionError):
        await client.health()
    await client.aclose()


async def test_missing_model(server):
    async def handle(request):
        return web.json_response({"models": []})

    client = VoiceboxClient(base_url=await server(handle))
    with pytest.raises(ModelNotReadyError):
        await client.model_readiness()
    await client.aclose()


@pytest.mark.parametrize("payload", [None, [], {"model_loaded": "yes"}])
async def test_malformed_health(server, payload):
    async def handle(request):
        return web.json_response(payload)

    client = VoiceboxClient(base_url=await server(handle))
    with pytest.raises(VoiceboxAPIError):
        await client.health()
    await client.aclose()


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"generations": [], "downloads": []}, None),
        ({"generations": [{}], "downloads": []}, "active work"),
        ({"generations": [], "downloads": [{}]}, "active work"),
        ({}, "incompatible schema"),
    ],
)
async def test_idle_preflight(server, payload, expected):
    from livekit.plugins.voicebox.errors import VoiceboxError

    async def handle(request):
        return web.json_response(payload)

    client = VoiceboxClient(base_url=await server(handle))
    if expected is None:
        await client.check_idle()
    else:
        with pytest.raises(VoiceboxError, match=expected):
            await client.check_idle()
    await client.aclose()
