import asyncio
import base64
import io
import json
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf
from aiohttp.test_utils import TestClient, TestServer

from examples import studio as module
from examples.backend_lease import BackendLease
from examples.studio import Studio, StudioError
from examples.studio_library import StudioLibrary
from tests.test_studio import Process


@pytest.fixture
async def audition(tmp_path, monkeypatch):
    monkeypatch.setattr(module.os, "environ", dict(module.os.environ))
    lease = BackendLease(tmp_path / "lock", "http://127.0.0.1:17493")
    lease.acquire()
    broker = Studio(lease)
    broker.library = StudioLibrary(tmp_path / "library")
    wave = io.BytesIO()
    sf.write(wave, np.full(24000 * 5, 0.05), 24000, format="WAV", subtype="PCM_16")
    voice = broker.library.create_voice("Test voice", "Synthetic fixture.", wave.getvalue(), True)
    monkeypatch.setenv("VOICEBOX_EXCLUSIVE", "1")
    monkeypatch.setattr(broker, "_audition_preflight", AsyncMock())
    process = Process()
    process.stdin = AsyncMock()
    process.stdin.write = lambda value: None
    process.stdin.close = lambda: None

    async def spawn(*args, **kwargs):
        process.event(broker.current, "audition_started")
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", AsyncMock(side_effect=spawn))
    yield broker, voice, process
    if broker.audition and broker.audition.end_task:
        await broker.audition.end_task
    lease.release()


async def settle(broker, process, *, safe=True):
    owner = broker.current
    process.event(owner, "finished", safe=safe)
    process.finish()
    await owner.monitor


async def test_audition_snapshots_voice_does_not_select_or_create_room(audition, monkeypatch):
    broker, voice, process = audition
    monkeypatch.setattr(broker, "_delete_room", AsyncMock())
    created = await broker.create_audition(voice["id"], "A new sentence.")
    assert broker.library.settings()["voiceId"] is None
    assert created["voiceId"] == voice["id"]
    assert broker.lease.marker.exists()
    assert broker.current.kind == "audition"
    with pytest.raises(StudioError):
        await broker.create()
    with pytest.raises(StudioError):
        await broker.create_audition(voice["id"], "Overlap.")
    await settle(broker, process)
    broker._delete_room.assert_not_called()


async def test_audio_is_private_bounded_one_shot_and_only_after_drain(audition):
    broker, voice, process = audition
    result = await broker.create_audition(voice["id"], "New words.")
    owner = broker.current
    process.event(owner, "audition_audio", pcm=base64.b64encode(b"\x01\x00" * 480).decode())
    await asyncio.sleep(0)
    with pytest.raises(StudioError):
        broker.audition_audio(result["auditionId"])
    with pytest.raises(StudioError):
        broker.audition_status("wrong-tab")
    await settle(broker, process)
    assert broker.phase == "idle"
    assert not broker.lease.marker.exists()
    assert broker.audition_status(result["auditionId"])["state"] == "ready"
    wav = broker.audition_audio(result["auditionId"])
    assert wav[:4] == b"RIFF"
    with sf.SoundFile(io.BytesIO(wav)) as audio:
        assert (audio.samplerate, audio.channels, audio.frames) == (24000, 1, 480)
    with pytest.raises(StudioError):
        broker.audition_audio(result["auditionId"])
    assert broker.audition.pcm == bytearray()


async def test_cancel_discards_audio_and_retains_lease_until_drain(audition):
    broker, voice, process = audition
    result = await broker.create_audition(voice["id"], "Cancel me.")
    owner = broker.current
    await broker.end_audition(result["auditionId"])
    await asyncio.sleep(0)
    assert broker.phase == "draining"
    assert broker.lease.marker.exists()
    process.event(owner, "audition_audio", pcm=base64.b64encode(b"\x01\x00" * 480).decode())
    await settle(broker, process)
    await owner.end_task
    assert broker.audition_status(result["auditionId"])["state"] == "cancelled"
    assert not broker.audition.pcm
    with pytest.raises(StudioError):
        broker.audition_audio(result["auditionId"])


async def test_uncertain_audition_fails_closed_and_hides_partial_audio(audition):
    broker, voice, process = audition
    result = await broker.create_audition(voice["id"], "Failure.")
    await settle(broker, process, safe=False)
    assert broker.phase == "blocked"
    assert broker.lease.marker.exists()
    assert broker.audition_status(result["auditionId"])["state"] == "failed"
    with pytest.raises(StudioError):
        broker.audition_audio(result["auditionId"])


@pytest.mark.parametrize("text", ["", " " * 3, "x" * 301, None, ["test"]])
async def test_bad_audition_text_never_submits(audition, text):
    broker, voice, process = audition
    with pytest.raises(StudioError):
        await broker.create_audition(voice["id"], text)
    assert broker.current is None
    assert not broker.lease.marker.exists()


async def test_missing_backend_and_exclusivity_never_submit(audition, monkeypatch):
    broker, voice, process = audition
    monkeypatch.setenv("VOICEBOX_EXCLUSIVE", "0")
    with pytest.raises(StudioError, match="exclusive"):
        await broker.create_audition(voice["id"], "Hello.")
    assert broker.phase == "idle"
    assert not broker.lease.marker.exists()


async def test_result_expires_and_new_voice_change_invalidates(audition, monkeypatch):
    broker, voice, process = audition
    result = await broker.create_audition(voice["id"], "New words.")
    process.event(
        broker.current, "audition_audio", pcm=base64.b64encode(b"\x01\x00" * 480).decode()
    )
    await settle(broker, process)
    owner = broker.audition
    monkeypatch.setattr(module.time, "monotonic", lambda: owner.created + 400)
    with pytest.raises(StudioError):
        broker.audition_audio(result["auditionId"])
    assert not owner.pcm


async def test_malformed_child_audio_cannot_be_success(audition):
    broker, voice, process = audition
    result = await broker.create_audition(voice["id"], "Words.")
    process.event(broker.current, "audition_audio", pcm="not valid base64!")
    await settle(broker, process)
    assert broker.audition_status(result["auditionId"])["state"] == "failed"
    assert not broker.audition.pcm


async def test_audition_http_guards_private_results_and_idle_mutations(audition):
    broker, voice, process = audition
    async with TestClient(TestServer(module.create_app(broker))) as client:
        headers = {"Host": "127.0.0.1:8765", "X-Voicebox-Studio": "1"}
        response = await client.post(
            "/api/audition", headers=headers, json={"voiceId": voice["id"], "text": "Hello."}
        )
        assert response.status == 200
        result = await response.json()
        response = await client.post(
            "/api/settings", headers=headers, json={"voiceId": voice["id"]}
        )
        assert response.status == 409
        response = await client.post(
            "/api/audition/status", headers=headers, json={"auditionId": "wrong-tab"}
        )
        assert response.status == 404
        response = await client.post(
            "/api/audition/audio",
            headers={**headers, "Origin": "https://untrusted.example"},
            json={"auditionId": result["auditionId"]},
        )
        assert response.status == 403
        await settle(broker, process)
        response = await client.post(
            "/api/audition/status", headers=headers, json={"auditionId": result["auditionId"]}
        )
        data = await response.json()
        assert "Hello." not in json.dumps(data)
        assert response.headers["Cache-Control"] == "no-store"


async def test_disconnected_start_consumer_does_not_abandon_child(audition, monkeypatch):
    broker, voice, process = audition
    entered, release = asyncio.Event(), asyncio.Event()

    async def spawn(*args, **kwargs):
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "LIVEKIT_API_SECRET" not in kwargs["env"]
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert "Private audition words" not in str(args) + str(kwargs["env"])
        entered.set()
        await release.wait()
        return process

    monkeypatch.setenv("OPENAI_API_KEY", "private-fixture")
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    consumer = asyncio.create_task(broker.create_audition(voice["id"], "Private audition words"))
    await entered.wait()
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert broker.lease.marker.exists()
    assert not broker.start_task.cancelled()
    release.set()
    await broker.start_task
    await settle(broker, process)


async def test_spawn_failure_without_submission_releases_marker(audition, monkeypatch):
    broker, voice, process = audition
    monkeypatch.setattr(
        module.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("private detail"))
    )
    with pytest.raises(StudioError, match="Local audition could not start"):
        await broker.create_audition(voice["id"], "Words.")
    assert broker.phase == "idle"
    assert not broker.lease.marker.exists()


async def test_result_invalidated_by_successful_voice_selection(audition):
    broker, voice, process = audition
    result = await broker.create_audition(voice["id"], "Words.")
    process.event(broker.current, "audition_audio", pcm=base64.b64encode(b"\x01\x00").decode())
    await settle(broker, process)
    previous = broker.audition
    async with TestClient(TestServer(module.create_app(broker))) as client:
        response = await client.post(
            "/api/settings",
            headers={"Host": "127.0.0.1:8765", "X-Voicebox-Studio": "1"},
            json={"voiceId": voice["id"]},
        )
        assert response.status == 200
    assert not previous.pcm
    with pytest.raises(StudioError):
        broker.audition_audio(result["auditionId"])


async def test_oversized_audio_is_stopped_and_never_delivered(audition, monkeypatch):
    broker, voice, process = audition
    monkeypatch.setattr(module, "MAX_AUDITION_PCM", 20)
    result = await broker.create_audition(voice["id"], "Words.")
    owner = broker.current
    process.event(owner, "audition_audio", pcm=base64.b64encode(b"\x01\x00" * 11).decode())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert process.signals
    await settle(broker, process)
    assert broker.audition_status(result["auditionId"])["state"] == "failed"
    assert not owner.pcm


async def test_cannot_audition_over_active_room(audition):
    broker, voice, process = audition
    broker.phase = "active"
    with pytest.raises(StudioError, match="End the conversation"):
        await broker.create_audition(voice["id"], "Words.")
    assert not broker.lease.marker.exists()


async def test_preflight_failure_never_starts_worker(audition, monkeypatch):
    broker, voice, process = audition
    monkeypatch.setattr(
        broker,
        "_audition_preflight",
        AsyncMock(side_effect=StudioError("The local backend is unavailable.", 503)),
    )
    with pytest.raises(StudioError, match="unavailable"):
        await broker.create_audition(voice["id"], "Words.")
    assert broker.current is None
    assert not broker.lease.marker.exists()


async def test_failed_monitor_stops_owned_child_but_keeps_uncertainty(audition):
    broker, voice, process = audition
    await broker.create_audition(voice["id"], "Words.")
    owner = broker.current
    process.stdout.set_exception(ValueError("broken child protocol"))
    with pytest.raises(ValueError):
        await owner.monitor
    assert owner.end_task is not None
    await owner.end_task
    assert process.returncode == -9
    assert broker.phase == "blocked"
    assert broker.lease.marker.exists()
    assert not owner.pcm


async def test_cancel_before_worker_boot_waits_for_signal_handler(audition, monkeypatch):
    broker, voice, process = audition
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    result = await broker.create_audition(voice["id"], "Words.")
    owner = broker.current
    await broker.end_audition(result["auditionId"])
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not process.signals
    process.event(owner, "audition_started")
    for _ in range(10):
        await asyncio.sleep(0)
        if process.signals:
            break
    assert process.signals
    await settle(broker, process)
    await owner.end_task
    assert broker.phase == "idle"
    assert broker.audition_status(result["auditionId"])["state"] == "cancelled"


async def test_silent_generated_audio_is_not_a_successful_audition(audition):
    broker, voice, process = audition
    result = await broker.create_audition(voice["id"], "Words.")
    process.event(broker.current, "audition_audio", pcm=base64.b64encode(bytes(960)).decode())
    await settle(broker, process)
    assert broker.audition_status(result["auditionId"])["state"] == "failed"
    assert not broker.audition.pcm
