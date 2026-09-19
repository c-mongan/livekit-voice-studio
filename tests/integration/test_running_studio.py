"""Opt-in local-only recovery checks against the running managed Studio server."""

import asyncio
import json
import os
import time

import aiohttp
import pytest
from livekit import rtc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("STUDIO_RELIABILITY_INTEGRATION") != "1",
        reason="Set STUDIO_RELIABILITY_INTEGRATION=1 for two synthetic local sessions.",
    ),
]

BASE = "http://127.0.0.1:8765"
HEADERS = {"X-Voicebox-Studio": "1"}


async def status(http):
    async with http.get(BASE + "/api/status") as response:
        response.raise_for_status()
        return await response.json()


def require_local(state):
    assert all(state[key].get("local") is True for key in ("ai", "stt", "livekit")), (
        "Select local reasoning, recognition and LiveKit before running this test."
    )


async def post(http, path, body):
    async with http.post(BASE + path, json=body, headers=HEADERS) as response:
        response.raise_for_status()
        return await response.json()


async def exercise(http, iteration):
    initial = await status(http)
    assert initial["phase"] == "idle", "Another session is active; no takeover is allowed."
    require_local(initial)
    grant = await post(http, "/api/session", {})
    room = rtc.Room()
    tasks = []
    errors = []
    samples = 0
    followup_expected = False
    followup_transcribed = asyncio.Event()
    last_audio = 0.0

    async def consume(track):
        nonlocal samples, last_audio
        stream = rtc.AudioStream(track)
        try:
            async for event in stream:
                count = sum(abs(sample) > 100 for sample in event.frame.data)
                if count:
                    samples += count
                    last_audio = time.monotonic()
        finally:
            await stream.aclose()

    def observed(task):
        if not task.cancelled() and task.exception() is not None:
            errors.append(type(task.exception()).__name__)

    @room.on("track_subscribed")
    def subscribed(track, publication, participant):
        if (
            participant.identity == grant["agentIdentity"]
            and track.kind == rtc.TrackKind.KIND_AUDIO
        ):
            task = asyncio.create_task(consume(track))
            task.add_done_callback(observed)
            tasks.append(task)

    async def read_transcript(reader, identity):
        try:
            if identity != grant["agentIdentity"]:
                return
            text = ""
            async for chunk in reader:
                text = (text + chunk)[-4096:]
                if followup_expected and "recovery works" in text.lower():
                    followup_transcribed.set()
        finally:
            reader.close()

    def transcript(reader, identity):
        task = asyncio.create_task(read_transcript(reader, identity))
        task.add_done_callback(observed)
        tasks.append(task)

    room.register_text_stream_handler("lk.transcription", transcript)

    async def heartbeat():
        while True:
            await post(http, "/api/session/heartbeat", {"sessionId": grant["sessionId"]})
            await asyncio.sleep(5)

    heart = asyncio.create_task(heartbeat())
    heart.add_done_callback(observed)

    async def wait_for(predicate):
        async with asyncio.timeout(90):
            while not predicate():
                assert not errors, errors
                await asyncio.sleep(0.05)
        assert not errors, errors

    def agent():
        return next(
            (p for p in room.remote_participants.values() if p.identity == grant["agentIdentity"]),
            None,
        )

    def listening():
        participant = agent()
        return (
            participant is not None
            and participant.attributes.get("lk.agent.state") == "listening"
            and time.monotonic() - last_audio > 1
        )

    async def ask(text):
        before = samples
        await room.local_participant.send_text(text, topic="lk.chat")
        await wait_for(lambda: samples - before >= 2400)
        return samples - before

    try:
        await room.connect(grant["serverUrl"], grant["participantToken"])
        async with asyncio.timeout(150):
            while True:
                state = await status(http)
                require_local(state)
                assert state["phase"] not in ("idle", "blocked"), state.get("message")
                if state["phase"] == "active":
                    break
                await asyncio.sleep(0.5)
        # A second Start must not steal or replace this session.
        async with http.post(BASE + "/api/session", json={}, headers=HEADERS) as response:
            assert response.status == 409
        await ask("Count slowly from one to twenty, saying every number.")
        interrupted_while_speaking = agent().attributes.get("lk.agent.state") == "speaking"
        assert interrupted_while_speaking, "Reply ended before the interruption could be tested."
        result = await room.local_participant.perform_rpc(
            destination_identity=grant["agentIdentity"],
            method="voicebox.interrupt",
            payload="{}",
        )
        assert json.loads(result)["stopped"] is True
        await wait_for(listening)
        followup_expected = True
        fresh_samples = await ask("Reply with only: Recovery works.")
        await wait_for(followup_transcribed.is_set)
        await wait_for(listening)
        state = await status(http)
        assert state.get("message") is None, state.get("message")
        assert not errors, errors
        print(
            json.dumps(
                {
                    "session": iteration + 1,
                    "interrupted_while_speaking": interrupted_while_speaking,
                    "fresh_followup_samples": fresh_samples,
                    "followup_text_matched": followup_transcribed.is_set(),
                    "concurrent_start_rejected": True,
                }
            ),
            flush=True,
        )
    finally:
        heart.cancel()
        await asyncio.gather(heart, return_exceptions=True)
        try:
            await post(http, "/api/session/end", {"sessionId": grant["sessionId"]})
        finally:
            await room.disconnect()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        async with asyncio.timeout(150):
            while True:
                state = await status(http)
                assert state["phase"] != "blocked", state.get("message")
                if state["phase"] == "idle":
                    break
                await asyncio.sleep(0.5)


async def test_two_sessions_interrupt_followup_and_cleanup():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
        state = await status(http)
        assert state["phase"] == "idle", "End your current session before running this test."
        require_local(state)
        for iteration in range(2):
            await exercise(http, iteration)
