"""Opt-in local-only recovery checks against the running managed Studio server."""

import asyncio
import json
import os
import time
import wave

import aiohttp
import pytest
from livekit import rtc

from benchmarks.reply_observation import ReplyObservation

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


def require_local(state, codex=False):
    if codex:
        assert state["ai"]["provider"] == "codex", "Select Codex explicitly before this test."
    components = ("stt", "livekit") if codex else ("ai", "stt", "livekit")
    assert all(state[key].get("local") is True for key in components), (
        "Select local reasoning, recognition and LiveKit before running this test."
    )


async def post(http, path, body):
    async with http.post(BASE + path, json=body, headers=HEADERS) as response:
        response.raise_for_status()
        return await response.json()


async def exercise(http, iteration, spoken_audio=None, codex=False):
    initial = await status(http)
    assert initial["phase"] == "idle", "Another session is active; no takeover is allowed."
    require_local(initial, codex=codex)
    grant = await post(http, "/api/session", {})
    room = rtc.Room()
    tasks = []
    errors = []
    samples = 0
    reply = ReplyObservation()
    last_audio = 0.0
    audio_source = None
    interruption_seconds = None

    async def consume(track):
        nonlocal samples, last_audio
        stream = rtc.AudioStream(track)
        try:
            async for event in stream:
                count = sum(abs(sample) > 100 for sample in event.frame.data)
                if count:
                    samples += count
                    reply.audio(count)
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

    @room.on("participant_attributes_changed")
    def attributes(changed, participant):
        if participant.identity == grant["agentIdentity"] and "lk.agent.state" in changed:
            reply.state_changed(changed["lk.agent.state"])

    async def read_transcript(reader, identity, token):
        try:
            if identity != grant["agentIdentity"]:
                return
            text = ""
            async for chunk in reader:
                text = (text + chunk)[-4096:]
                reply.transcript(token, text)
        finally:
            reader.close()

    def transcript(reader, identity):
        task = asyncio.create_task(read_transcript(reader, identity, reply.token))
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
        checked_at = 0.0
        async with asyncio.timeout(90):
            while not predicate():
                assert not errors, errors
                if time.monotonic() - checked_at > 1:
                    current = await status(http)
                    assert current.get("message") is None, current.get("message")
                    checked_at = time.monotonic()
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
                require_local(state, codex=codex)
                assert state["phase"] not in ("idle", "blocked"), state.get("message")
                if state["phase"] == "active":
                    break
                await asyncio.sleep(0.5)
        # A second Start must not steal or replace this session.
        async with http.post(BASE + "/api/session", json={}, headers=HEADERS) as response:
            assert response.status == 409
        if spoken_audio is not None:
            audio_source = rtc.AudioSource(48000, 1)
            track = rtc.LocalAudioTrack.create_audio_track("synthetic-interruption", audio_source)
            await room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
            queued_audio = asyncio.Queue()

            async def microphone():
                while True:
                    try:
                        block = queued_audio.get_nowait()
                        queued = True
                    except asyncio.QueueEmpty:
                        block = bytes(1920)
                        queued = False
                    await audio_source.capture_frame(rtc.AudioFrame(block, 48000, 1, 960))
                    if queued:
                        queued_audio.task_done()
                    await asyncio.sleep(0.02)

            microphone_task = asyncio.create_task(microphone())
            microphone_task.add_done_callback(observed)
            tasks.append(microphone_task)
            await asyncio.sleep(0.5)
            # The SDK suppresses recognition during its first 3 s of AEC warm-up.
            # Exercise established-conversation barge-in, preserving that echo guard.
            await ask("Reply with only: Ready for the next question.")
            await wait_for(listening)
            await asyncio.sleep(3)
        counting_reply_sequence = (await status(http))["startedReplies"] + 1
        await ask("Count slowly from one to twenty, saying every number.")
        interrupted_while_speaking = agent().attributes.get("lk.agent.state") == "speaking"
        assert interrupted_while_speaking, "Reply ended before the interruption could be tested."
        if spoken_audio is None:
            result = await room.local_participant.perform_rpc(
                destination_identity=grant["agentIdentity"],
                method="voicebox.interrupt",
                payload="{}",
            )
            assert json.loads(result)["stopped"] is True
            await wait_for(listening)
            reply.arm()
            await ask("Reply with only: Recovery works.")
        else:
            # No Stop RPC or typed follow-up: recognition must supply the new request.

            async def speak():
                for offset in range(0, len(spoken_audio), 1920):
                    queued_audio.put_nowait(spoken_audio[offset : offset + 1920].ljust(1920, b"\0"))
                await queued_audio.join()
                await asyncio.sleep(1.2)

            began = time.monotonic()
            speaker = asyncio.create_task(speak())
            speaker.add_done_callback(observed)
            tasks.append(speaker)
            # Bound VAD response time; the SDK interruption flag below separately
            # distinguishes an interruption from natural reply completion.
            async with asyncio.timeout(3):
                await wait_for(lambda: agent().attributes.get("lk.agent.state") != "speaking")
            interruption_seconds = time.monotonic() - began
            reply.arm()
            await wait_for(speaker.done)
            await speaker
        await wait_for(lambda: reply.complete)
        await wait_for(listening)
        state = await status(http)
        assert state.get("message") is None, state.get("message")
        assert counting_reply_sequence in state["interruptedReplySequences"], (
            "SDK did not mark the counting reply interrupted; natural completion is insufficient."
        )
        assert not errors, errors
        print(
            json.dumps(
                {
                    "session": iteration + 1,
                    "reasoning_provider": initial["ai"]["provider"],
                    "interruption_mode": "speech" if spoken_audio is not None else "rpc",
                    "speaking_state_exit_seconds": interruption_seconds,
                    "initial_echo_warmup_excluded": spoken_audio is not None,
                    "interrupted_while_speaking": interrupted_while_speaking,
                    "sdk_interruption_confirmed": True,
                    "fresh_followup_samples": reply.samples,
                    "followup_text_matched": reply.text_matched,
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
            if audio_source is not None:
                await audio_source.aclose()
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


def spoken_fixture():
    path = os.environ.get("STUDIO_BARGE_IN_AUDIO")
    assert path, "Supply STUDIO_BARGE_IN_AUDIO containing the synthetic recovery phrase."
    with wave.open(path, "rb") as fixture:
        assert fixture.getnchannels() == 1 and fixture.getsampwidth() == 2
        assert fixture.getframerate() == 16000
        assert 0 < fixture.getnframes() <= 16000 * 15
        audio = fixture.readframes(fixture.getnframes())
    resampler = rtc.AudioResampler(16000, 48000, num_channels=1)
    frames = resampler.push(rtc.AudioFrame(audio, 16000, 1, len(audio) // 2))
    frames += resampler.flush()
    return b"".join(bytes(frame.data) for frame in frames)


@pytest.mark.skipif(
    os.environ.get("STUDIO_BARGE_IN_AUDIO") is None,
    reason="Supply a 16 kHz mono WAV saying: Stop talking. Reply with only: Recovery works.",
)
async def test_spoken_interruption_and_cleanup():
    audio = spoken_fixture()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
        await exercise(http, 0, spoken_audio=audio)


@pytest.mark.skipif(
    os.environ.get("STUDIO_CODEX_SPEECH_INTEGRATION") != "1"
    or os.environ.get("STUDIO_TEST_RESTRICTED_CODEX") != "1",
    reason="Explicitly authorize Codex cloud reasoning and its restricted-agent mode.",
)
async def test_codex_spoken_interruption_and_cleanup():
    audio = spoken_fixture()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
        async with http.get(BASE + "/api/settings") as response:
            response.raise_for_status()
            settings = await response.json()
        assert settings["llmProvider"] == "codex" and settings["codexRestrictedApproved"] is True
        await exercise(http, 0, spoken_audio=audio, codex=True)
