import asyncio

import aiohttp
import pytest
from aiohttp import web
from conftest import make_wav
from livekit.plugins.voicebox.client import VoiceboxClient
from livekit.plugins.voicebox.errors import (
    BackendUncertainError,
    ClientClosedError,
    VoiceboxConnectionError,
    VoiceboxTimeoutError,
)


async def test_cancel_retains_lease_and_does_not_reuse_stale_audio(server):
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []
    wav_a, wav_b = make_wav(value=0.1), make_wav(value=0.7)

    async def handle(request):
        payload = await request.json()
        calls.append(payload["text"])
        if payload["text"] == "A":
            started.set()
            await finish.wait()
            return web.Response(body=wav_a)
        return web.Response(body=wav_b)

    client = VoiceboxClient(base_url=await server(handle))
    a = asyncio.create_task(client.synthesize_wav(profile_id="id", text="A"))
    await started.wait()
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    assert client.state == "draining"
    b = asyncio.create_task(client.synthesize_wav(profile_id="id", text="B"))
    await asyncio.sleep(0.02)
    assert calls == ["A"]
    finish.set()
    assert await b == wav_b
    assert calls == ["A", "B"]
    assert client.state == "idle"
    await client.aclose()


async def test_cancel_queued_work_never_submits(server):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def handle(request):
        calls.append((await request.json())["text"])
        started.set()
        await finish.wait()
        return web.Response(body=make_wav())

    client = VoiceboxClient(base_url=await server(handle))
    a = asyncio.create_task(client.synthesize_wav(profile_id="id", text="A"))
    await started.wait()
    b = asyncio.create_task(client.synthesize_wav(profile_id="id", text="B"))
    await asyncio.sleep(0)
    b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b
    assert client.state == "active"
    finish.set()
    await a
    assert calls == ["A"]
    await client.aclose()


async def test_foreground_timeout_drains_before_next_request(server):
    started, finish = asyncio.Event(), asyncio.Event()

    async def handle(request):
        started.set()
        await finish.wait()
        return web.Response(body=make_wav())

    client = VoiceboxClient(base_url=await server(handle), request_timeout=0.02, drain_timeout=0.2)
    with pytest.raises(VoiceboxTimeoutError):
        await client.synthesize_wav(profile_id="id", text="Hello")
    assert started.is_set()
    assert client.state == "draining"
    finish.set()
    await client.aclose()
    assert client.state == "closed"


async def test_drain_timeout_blocks_admission_and_bounds_shutdown(server):
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def handle(request):
        calls.append(await request.json())
        started.set()
        await finish.wait()
        return web.Response(body=make_wav())

    async with aiohttp.ClientSession() as session:
        client = VoiceboxClient(
            base_url=await server(handle), request_timeout=1, drain_timeout=0.02, session=session
        )
        task = asyncio.create_task(client.synthesize_wav(profile_id="id", text="Hello"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.04)
        assert client.state == "uncertain"
        with pytest.raises(BackendUncertainError):
            await client.synthesize_wav(profile_id="id", text="No replay")
        async with asyncio.timeout(0.1):
            with pytest.raises(BackendUncertainError):
                await client.aclose()
        assert not session.closed
        assert len(calls) == 1
        finish.set()


async def test_transport_loss_is_uncertain(server):
    async def handle(request):
        await request.json()
        request.transport.close()
        return web.Response()

    client = VoiceboxClient(base_url=await server(handle))
    with pytest.raises(VoiceboxConnectionError):
        await client.synthesize_wav(profile_id="id", text="Hello")
    assert client.state == "uncertain"
    with pytest.raises(BackendUncertainError):
        await client.synthesize_wav(profile_id="id", text="No retry")
    with pytest.raises(BackendUncertainError):
        await client.aclose()


async def test_shutdown_rejects_queued_jobs_and_survives_caller_cancellation(server):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def handle(request):
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        return web.Response(body=make_wav())

    client = VoiceboxClient(base_url=await server(handle), drain_timeout=0.2)
    a = asyncio.create_task(client.synthesize_wav(profile_id="id", text="A"))
    await started.wait()
    b = asyncio.create_task(client.synthesize_wav(profile_id="id", text="B"))
    closing = asyncio.create_task(client.aclose())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    finish.set()
    await a
    with pytest.raises(ClientClosedError):
        await b
    await client.aclose()
    assert calls == 1
    assert client.state == "closed"
