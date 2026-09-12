import io
from collections.abc import AsyncIterator

import numpy as np
import pytest
import soundfile as sf
from aiohttp import web
from aiohttp.test_utils import TestServer


def make_wav(rate=24000, channels=1, seconds=0.1, value=0.25, subtype="PCM_16"):
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
async def server() -> AsyncIterator:
    servers = []

    async def create(handler):
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        instance = TestServer(app)
        await instance.start_server()
        servers.append(instance)
        return str(instance.make_url("")).rstrip("/")

    yield create
    for instance in servers:
        await instance.close()


@pytest.fixture
def profile():
    return {
        "id": "voice-1",
        "name": "Test Voice",
        "language": "en",
        "voice_type": "cloned",
        "default_engine": "qwen",
        "preset_engine": None,
        "sample_count": 1,
    }
