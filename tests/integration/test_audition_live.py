"""Explicit local-only generated audition. Discards received PCM after validation."""

import asyncio
import io
import os

import aiohttp
import numpy as np
import pytest
import soundfile as sf

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("STUDIO_AUDITION_INTEGRATION") != "1",
        reason="Explicitly authorize synthesis with STUDIO_AUDITION_VOICE_ID.",
    ),
]


async def test_real_generated_audition_and_cancelled_retry():
    voice = os.environ.get("STUDIO_AUDITION_VOICE_ID")
    assert voice, "Choose an authorized saved voice ID explicitly."
    base = "http://127.0.0.1:8765"
    headers = {"X-Voicebox-Studio": "1"}
    async with aiohttp.ClientSession() as http:
        async with http.get(base + "/api/status") as response:
            assert (await response.json())["phase"] == "idle", "End the active room first."
        async with http.get(base + "/api/settings") as response:
            original = await response.json()

        async def post(route, body):
            async with http.post(base + route, json=body, headers=headers) as response:
                response.raise_for_status()
                return await response.json()

        async def wait(identifier):
            async with asyncio.timeout(180):
                while True:
                    value = await post("/api/audition/status", {"auditionId": identifier})
                    assert value["phase"] != "blocked", value["message"]
                    if value["phase"] == "idle":
                        return value
                    await asyncio.sleep(0.2)

        for cancel in (False, True, False):
            job = await post(
                "/api/audition", {"voiceId": voice, "text": "Would you like to try a new idea?"}
            )
            handle = {"auditionId": job["auditionId"]}
            try:
                if cancel:
                    await post("/api/audition/end", handle)
                value = await wait(job["auditionId"])
                assert value["voiceId"] == voice
                assert value["state"] == ("cancelled" if cancel else "ready"), value["message"]
                async with http.post(
                    base + "/api/audition/audio", json=handle, headers=headers
                ) as response:
                    if cancel:
                        assert response.status == 409
                    else:
                        assert response.status == 200
                        assert response.headers["Cache-Control"] == "no-store"
                        audio = await response.read()
                        assert len(audio) <= 24000 * 2 * 30 + 44
                        with sf.SoundFile(io.BytesIO(audio)) as wav:
                            assert wav.samplerate == 24000 and wav.channels == 1
                            assert 0 < wav.frames <= 24000 * 30
                            assert np.any(np.abs(wav.read(dtype="float32")) > 0.003)
                async with http.post(
                    base + "/api/audition/audio", json=handle, headers=headers
                ) as response:
                    assert response.status == 409
            finally:
                await post("/api/audition/end", handle)
                await wait(job["auditionId"])
        async with http.get(base + "/api/settings") as response:
            assert await response.json() == original
