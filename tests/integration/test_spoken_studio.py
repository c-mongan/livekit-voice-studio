"""Opt-in spoken-input smoke against a running local Studio. No recordings saved."""

import asyncio
import io
import json
import os
import time
from pathlib import Path

import aiohttp
import numpy as np
import pytest
import soundfile as sf
from livekit import rtc

from benchmarks.conversation_stats import summarize

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("STUDIO_SPOKEN_INTEGRATION") != "1",
        reason="Explicitly authorize a spoken-input test and supply STUDIO_TEST_AUDIO.",
    ),
]


def read_fixture(path):
    with Path(path).open("rb") as file:
        return file.read(4 * 1024 * 1024 + 1)


async def test_spoken_input_to_nonzero_reply():
    turns = int(os.environ.get("STUDIO_TEST_TURNS", "1"))
    assert 1 <= turns <= 5, "Use 1–5 explicitly authorized turns."
    path = os.environ.get("STUDIO_TEST_AUDIO")
    assert path, "Set STUDIO_TEST_AUDIO to an authorized short synthetic audio file."
    data = await asyncio.to_thread(read_fixture, path)
    assert len(data) <= 4 * 1024 * 1024
    with sf.SoundFile(io.BytesIO(data)) as source:
        assert 8000 <= source.samplerate <= 48000 and 1 <= source.channels <= 2
        assert 0 < source.frames <= source.samplerate * 15
        wave = source.read(dtype="float32", always_2d=True).mean(axis=1)
        rate = source.samplerate
    pcm = (np.clip(wave, -1, 1) * 32767).astype("<i2").tobytes()
    resampler = rtc.AudioResampler(rate, 16000, num_channels=1)
    frames = resampler.push(rtc.AudioFrame(pcm, rate, 1, len(pcm) // 2)) + resampler.flush()
    audio = b"".join(frame.data.tobytes() for frame in frames)
    base = "http://127.0.0.1:8765"
    headers = {"X-Voicebox-Studio": "1"}
    async with aiohttp.ClientSession() as http:
        async with http.post(base + "/api/session", json={}, headers=headers) as response:
            response.raise_for_status()
            grant = await response.json()
        room = rtc.Room()
        result = asyncio.get_running_loop().create_future()
        tasks = []
        source = None
        last_nonzero = 0.0
        count = 0

        async def consume(track):
            nonlocal last_nonzero, count
            stream = rtc.AudioStream(track)
            try:
                async for event in stream:
                    nonzero = sum(abs(sample) > 100 for sample in event.frame.data)
                    if nonzero:
                        last_nonzero = time.perf_counter()
                    if not result.done():
                        count += nonzero
                        if count >= 2400:
                            result.set_result((time.perf_counter(), count))
            finally:
                await stream.aclose()

        def observe(task):
            if not task.cancelled() and task.exception() is not None and not result.done():
                result.set_exception(task.exception())

        @room.on("track_subscribed")
        def subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                task = asyncio.create_task(consume(track))
                task.add_done_callback(observe)
                tasks.append(task)

        async def heartbeat():
            while True:
                async with http.post(
                    base + "/api/session/heartbeat",
                    json={"sessionId": grant["sessionId"]},
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                await asyncio.sleep(10)

        heart = asyncio.create_task(heartbeat())
        try:
            await room.connect(grant["serverUrl"], grant["participantToken"])
            for _ in range(120):
                async with http.get(base + "/api/status") as response:
                    state = await response.json()
                if state["phase"] == "active":
                    break
                assert state["phase"] not in ("blocked", "idle"), state.get("message")
                await asyncio.sleep(0.5)
            assert state["phase"] == "active"
            source = rtc.AudioSource(16000, 1)
            track = rtc.LocalAudioTrack.create_audio_track("synthetic-smoke-input", source)
            options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(track, options)
            await asyncio.sleep(0.5)
            timings = []
            for turn in range(turns):
                if turn:
                    result = asyncio.get_running_loop().create_future()
                count = 0
                for offset in range(0, len(audio), 640):
                    block = audio[offset : offset + 640]
                    await source.capture_frame(rtc.AudioFrame(block, 16000, 1, len(block) // 2))
                    await asyncio.sleep(0.02)
                await source.wait_for_playout()
                speech_end = time.perf_counter()
                for _ in range(60):
                    await source.capture_frame(rtc.AudioFrame(bytes(640), 16000, 1, 320))
                    await asyncio.sleep(0.02)
                async with asyncio.timeout(45):
                    first, received = await result
                timings.append(first - speech_end)
                print(
                    json.dumps(
                        {"turn": turn + 1, "seconds": timings[-1], "nonzero_samples": received}
                    )
                )
                # Do not count the previous response as the next turn's first audio.
                async with asyncio.timeout(45):
                    while True:
                        agent = next(
                            (
                                p
                                for p in room.remote_participants.values()
                                if p.identity == grant["agentIdentity"]
                            ),
                            None,
                        )
                        if (
                            agent is not None
                            and agent.attributes.get("lk.agent.state") == "listening"
                            and time.perf_counter() - last_nonzero > 1
                        ):
                            break
                        await asyncio.sleep(0.1)
            print(
                json.dumps(
                    {
                        "stt": state["stt"]["provider"],
                        "llm": state["ai"]["provider"],
                        "speech_end_to_reply": summarize(timings),
                        "same_synthetic_phrase_repeated": True,
                        "human_accent_evaluation": False,
                    }
                )
            )
        finally:
            heart.cancel()
            await asyncio.gather(heart, return_exceptions=True)
            async with http.post(
                base + "/api/session/end",
                json={"sessionId": grant["sessionId"]},
                headers=headers,
            ) as response:
                response.raise_for_status()
            await room.disconnect()
            if source is not None:
                await source.aclose()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for _ in range(300):
                async with http.get(base + "/api/status") as response:
                    state = await response.json()
                if state["phase"] in ("idle", "blocked"):
                    break
                await asyncio.sleep(0.5)
            assert state["phase"] == "idle", state.get("message")
