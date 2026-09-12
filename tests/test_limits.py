import pytest
from aiohttp import web
from livekit.plugins.voicebox import client as client_module
from livekit.plugins.voicebox.client import VoiceboxClient
from livekit.plugins.voicebox.errors import (
    BackendUncertainError,
    ResponseLimitError,
    VoiceboxAPIError,
)


@pytest.mark.parametrize("declared", [True, False])
async def test_wav_response_allocation_bound(server, monkeypatch, declared):
    monkeypatch.setattr(client_module, "MAX_WAV_BYTES", 16)

    async def handle(request):
        if declared:
            return web.Response(body=b"x" * 17)
        response = web.StreamResponse()
        await response.prepare(request)
        await response.write(b"x" * 17)
        await response.write_eof()
        return response

    client = VoiceboxClient(base_url=await server(handle))
    with pytest.raises(ResponseLimitError):
        await client.synthesize_wav(profile_id="id", text="Hello")
    assert client.state == "uncertain"
    with pytest.raises(BackendUncertainError):
        await client.aclose()


async def test_json_allocation_bound(server, monkeypatch):
    monkeypatch.setattr(client_module, "MAX_JSON_BYTES", 16)

    async def handle(request):
        return web.Response(body=b"x" * 17)

    client = VoiceboxClient(base_url=await server(handle))
    with pytest.raises(ResponseLimitError):
        await client.health()
    assert client.state == "idle"
    await client.aclose()


@pytest.mark.parametrize("body", [b"not json", b"\xff", b"{}"])
async def test_invalid_profile_response(server, body):
    async def handle(request):
        return web.Response(body=body)

    client = VoiceboxClient(base_url=await server(handle))
    with pytest.raises(VoiceboxAPIError):
        await client.list_profiles()
    await client.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/x",
        "http://user:password@localhost",
        "http://localhost/path",
        "http://localhost?token=secret",
        "http://localhost#fragment",
        "",
    ],
)
def test_invalid_origins(url):
    with pytest.raises(ValueError):
        VoiceboxClient(base_url=url)
