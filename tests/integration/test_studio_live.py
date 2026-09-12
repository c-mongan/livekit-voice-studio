"""Opt-in real-room checks. No microphone capture, recording, or personal text."""

import asyncio
import json
import os
import socket
import time

import pytest
from dotenv import load_dotenv
from livekit import rtc

from examples.backend_lease import BackendLease, runtime_root
from examples.studio import ROOT, Studio

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("STUDIO_INTEGRATION") != "1",
        reason="Set STUDIO_INTEGRATION=1 to authorize two real local Studio sessions.",
    ),
]


async def wait_phase(studio, phases):
    for _ in range(1500):
        if studio.phase in phases:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"Phase remained {studio.phase}: {studio.message}")


async def exercise_room(studio, iteration):
    details = await studio.create()
    room = rtc.Room()
    audible = asyncio.Event()
    tasks = []
    errors = []
    nonzero_samples = 0
    elapsed = None
    started = time.perf_counter()

    def observed(task):
        if not task.cancelled() and task.exception() is not None:
            errors.append(type(task.exception()).__name__)
            audible.set()

    async def consume(track):
        nonlocal nonzero_samples, elapsed
        stream = rtc.AudioStream(track)
        try:
            async for event in stream:
                nonzero_samples += sum(abs(sample) > 100 for sample in event.frame.data)
                if nonzero_samples >= 2400:
                    elapsed = time.perf_counter() - started
                    audible.set()
                    return
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def track_subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            task = asyncio.create_task(consume(track))
            task.add_done_callback(observed)
            tasks.append(task)

    async def heartbeat():
        while True:
            studio.heartbeat(details["sessionId"])
            await asyncio.sleep(10)

    heart = asyncio.create_task(heartbeat())
    try:
        await room.connect(details["serverUrl"], details["participantToken"])
        await wait_phase(studio, {"active", "blocked", "idle"})
        assert studio.phase == "active", studio.message
        started = time.perf_counter()
        await room.local_participant.send_text(
            "Please say only: Hello from the local voice studio.", topic="lk.chat"
        )
        async with asyncio.timeout(90):
            await audible.wait()
        assert not errors, errors
        assert nonzero_samples >= 2400
        if iteration == 1:
            response = await room.local_participant.perform_rpc(
                destination_identity=details["agentIdentity"],
                method="voicebox.interrupt",
                payload="{}",
            )
            assert json.loads(response)["stopped"] is True
        else:
            for _ in range(300):
                if studio.metrics["ttsAudioSeconds"] is not None:
                    break
                await asyncio.sleep(0.1)
            assert studio.metrics["ttsAudioSeconds"] is not None
        return {
            "session": iteration + 1,
            "nonzero_remote_samples": nonzero_samples,
            "text_to_nonzero_audio_seconds": elapsed,
            "metrics": studio.metrics.copy(),
            "interrupt_acknowledged": iteration == 1,
        }
    finally:
        heart.cancel()
        await asyncio.gather(heart, return_exceptions=True)
        if studio.current is not None:
            await studio.end(details["sessionId"])
        await room.disconnect()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await wait_phase(studio, {"idle", "blocked"})
        assert studio.phase == "idle", studio.message
        assert not studio.lease.marker.exists()


async def test_two_real_sessions_text_audio_interrupt_and_drain():
    load_dotenv(ROOT / ".env", override=False)
    if os.environ.get("STUDIO_REQUIRE_VOICEBOX_OFFLINE") == "1":
        assert os.environ.get("VOICEBOX_VOICE_BUNDLE"), "Configure the standalone reference bundle."
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", 17493)) != 0, "Voicebox must be offline."
    lease = BackendLease(runtime_root(), os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"))
    lease.acquire()
    studio = Studio(lease)
    watchdog = asyncio.create_task(studio.watchdog())
    observations = []
    try:
        for iteration in range(2):
            observations.append(await exercise_room(studio, iteration))
        if os.environ.get("STUDIO_REQUIRE_VOICEBOX_OFFLINE") == "1":
            with socket.socket() as probe:
                probe.settimeout(1)
                assert probe.connect_ex(("127.0.0.1", 17493)) != 0, "Voicebox was restarted."
        print(json.dumps({"studio_real_observations": observations}, sort_keys=True))
    finally:
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
        await studio.close()
        lease.release()
