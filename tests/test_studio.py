import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from livekit import api

from examples import studio as module
from examples.backend_lease import BackendLease
from examples.studio import OwnedSession, Studio, StudioError


@pytest.fixture
def lease(tmp_path):
    value = BackendLease(tmp_path, "http://127.0.0.1:17493")
    value.acquire()
    yield value
    value.release()


@pytest.fixture
def broker(lease):
    return Studio(lease)


def owned_session():
    return OwnedSession(
        "private-session-handle", "test-room", "test-user", "test-agent", "ipc-secret"
    )


class Process:
    def __init__(self):
        self.stdout = asyncio.StreamReader()
        self.returncode = None
        self.exited = asyncio.Event()
        self.signals = []

    def send_signal(self, value):
        self.signals.append(value)

    def kill(self):
        self.finish(-9)

    def finish(self, code=0):
        self.returncode = code
        self.stdout.feed_eof()
        self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def event(self, owner, event, **kwargs):
        self.stdout.feed_data(
            b"STUDIO_EVENT "
            + json.dumps({"key": owner.key, "event": event, **kwargs}).encode()
            + b"\n"
        )


def test_cross_process_lease_and_durable_uncertainty(tmp_path):
    first = BackendLease(tmp_path, "http://127.0.0.1:17493")
    second = BackendLease(tmp_path, "http://localhost:17493/")
    first.acquire()
    with pytest.raises(RuntimeError, match="Another local"):
        second.acquire()
    first.mark_active()
    first.release()
    with pytest.raises(RuntimeError, match="unresolved"):
        second.acquire()
    second.acquire(confirmed_backend_restart=True)
    second.mark_active()
    second.mark_safe()
    second.release()
    first.acquire()
    first.release()


async def test_end_retains_ownership_until_confirmed_worker_exit(broker, monkeypatch):
    owner = owned_session()
    process = Process()
    owner.process = process
    broker.current = owner
    broker.phase = "active"
    broker.lease.mark_active()
    monkeypatch.setattr(broker, "_delete_room", AsyncMock(return_value=True))
    owner.monitor = asyncio.create_task(broker._monitor(owner))
    await broker.end(owner.id)
    await asyncio.sleep(0)
    assert broker.phase == "draining"
    assert broker.lease.marker.exists()
    assert process.signals
    with pytest.raises(StudioError):
        await broker.create()
    process.event(owner, "finished", safe=True)
    process.finish()
    await owner.end_task
    assert broker.phase == "idle"
    assert broker.current is None
    assert not broker.lease.marker.exists()
    assert owner.room in broker.retired_rooms


@pytest.mark.parametrize(
    "finished,safe,code", [(False, False, 0), (True, False, 0), (True, True, 1)]
)
async def test_unconfirmed_exit_blocks_new_room(broker, monkeypatch, finished, safe, code):
    owner = owned_session()
    process = Process()
    owner.process = process
    broker.current = owner
    broker.lease.mark_active()
    monkeypatch.setattr(broker, "_delete_room", AsyncMock())
    task = asyncio.create_task(broker._monitor(owner))
    if finished:
        process.event(owner, "finished", safe=safe)
    process.finish(code)
    await task
    assert broker.phase == "blocked"
    assert broker.lease.marker.exists()
    with pytest.raises(StudioError):
        await broker.create()


async def test_forced_shutdown_keeps_uncertain_marker(broker, monkeypatch):
    owner = owned_session()
    process = Process()
    owner.process = process
    broker.current = owner
    broker.lease.mark_active()
    monkeypatch.setattr(module, "SHUTDOWN_SECONDS", 0.01)
    monkeypatch.setattr(broker, "_delete_room", AsyncMock())
    owner.monitor = asyncio.create_task(broker._monitor(owner))
    await broker.end(owner.id)
    await owner.end_task
    await owner.monitor
    assert process.returncode == -9
    assert broker.phase == "blocked"
    assert broker.lease.marker.exists()


def test_worker_events_require_private_key_and_valid_metrics(broker):
    owner = owned_session()
    broker.current = owner
    broker.phase = "starting"
    broker._event(owner, {"key": "wrong", "event": "ready"})
    assert broker.phase == "starting"
    broker._event(owner, {"key": owner.key, "event": "ready"})
    assert broker.phase == "active"
    for bad in (True, -1, float("nan"), float("inf"), "2"):
        broker._event(owner, {"key": owner.key, "event": "metrics", "ttsAudioSeconds": bad})
        assert broker.metrics["ttsAudioSeconds"] is None
    broker._event(owner, {"key": owner.key, "event": "metrics", "ttsAudioSeconds": 2.5})
    assert broker.metrics["ttsAudioSeconds"] == 2.5


def test_heartbeat_requires_owner_and_rejects_draining(broker):
    owner = owned_session()
    broker.current = owner
    owner.heartbeat = 0
    broker.heartbeat(owner.id)
    assert owner.heartbeat > 0
    with pytest.raises(StudioError):
        broker.heartbeat("other-tab")
    broker.phase = "draining"
    with pytest.raises(StudioError):
        broker.heartbeat(owner.id)


async def test_failed_preflight_never_spawns_or_holds_lease(broker, monkeypatch):
    monkeypatch.setattr(broker, "status", AsyncMock(return_value={"problems": ["Missing model."]}))
    spawn = AsyncMock()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(StudioError, match="Missing model"):
        await broker.create()
    assert broker.phase == "idle"
    assert not broker.lease.marker.exists()
    spawn.assert_not_awaited()


async def fake_backend(broker, monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://unit-test.invalid")
    monkeypatch.setenv("LIVEKIT_API_KEY", "unit-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "unit-secret-with-32-characters-long")
    fake_api = SimpleNamespace(
        room=SimpleNamespace(
            list_rooms=AsyncMock(return_value=SimpleNamespace(rooms=[])),
            create_room=AsyncMock(),
            delete_room=AsyncMock(),
        )
    )
    context = AsyncMock()
    context.__aenter__.return_value = fake_api
    monkeypatch.setattr(api, "LiveKitAPI", lambda **_: context)
    monkeypatch.setattr(broker, "status", AsyncMock(return_value={"problems": []}))
    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    return process, fake_api


async def test_scoped_tokens_and_one_session_at_a_time(broker, monkeypatch):
    process, fake_api = await fake_backend(broker, monkeypatch)
    data = await broker.create()
    claims = api.TokenVerifier("unit-key", "unit-secret-with-32-characters-long").verify(
        data["participantToken"]
    )
    assert claims.video.room == data["roomName"]
    assert claims.video.room_join
    assert not claims.video.room_admin
    assert claims.video.can_publish_sources == ["microphone"]
    assert data["sessionId"] not in data["participantToken"]
    assert "unit-secret" not in json.dumps(data)
    with pytest.raises(StudioError):
        await broker.create()
    fake_api.room.create_room.assert_awaited_once()
    owner = broker.current
    process.event(owner, "finished", safe=True)
    process.finish()
    await owner.monitor
    assert broker.phase == "idle"


async def test_cancelled_http_start_remains_owned(broker, monkeypatch):
    process, _ = await fake_backend(broker, monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_status(**kwargs):
        entered.set()
        await release.wait()
        return {"problems": []}

    monkeypatch.setattr(broker, "status", slow_status)
    caller = asyncio.create_task(broker.create())
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert broker.phase == "starting"
    release.set()
    await broker.start_task
    owner = broker.current
    assert owner is not None
    process.event(owner, "finished", safe=True)
    process.finish()
    await owner.monitor


@pytest.fixture
async def client(broker):
    app = module.create_app(broker)
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example:8765", "X-Voicebox-Studio": "1"},
        {"Host": "127.0.0.1:8765", "Origin": "https://evil.example", "X-Voicebox-Studio": "1"},
        {"Host": "127.0.0.1:8765"},
    ],
)
async def test_mutation_host_origin_and_csrf_guards(client, broker, headers, monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(broker, "create", create)
    response = await client.post("/api/session", json={}, headers=headers)
    assert response.status == 403
    create.assert_not_awaited()


@pytest.mark.parametrize("data", [{"profile": "unapproved"}, [], {"serverUrl": "https://evil"}])
async def test_cannot_override_server_options(client, data):
    response = await client.post(
        "/api/session",
        json=data,
        headers={"Host": "127.0.0.1:8765", "X-Voicebox-Studio": "1"},
    )
    assert response.status == 400


async def test_status_no_tokens_or_control_handle(client, broker, monkeypatch):
    broker.current = owned_session()
    broker._last_status = {"voice": {"name": "Synthetic"}, "problems": []}
    broker._last_status_at = module.time.monotonic()
    response = await client.get("/api/status", headers={"Host": "127.0.0.1:8765"})
    assert response.status == 200
    data = await response.json()
    assert data["session"] is None
    assert "private-session-handle" not in json.dumps(data)
    assert "ipc-secret" not in json.dumps(data)
    assert response.headers["Cache-Control"] == "no-store"


async def test_end_and_heartbeat_reject_other_tabs(client, broker):
    broker.current = owned_session()
    for route in ("end", "heartbeat"):
        response = await client.post(
            f"/api/session/{route}",
            json={"sessionId": "another-tab"},
            headers={"Host": "127.0.0.1:8765", "X-Voicebox-Studio": "1"},
        )
        assert response.status == 404


async def test_livekit_failure_is_sanitized(broker, monkeypatch):
    _, fake_api = await fake_backend(broker, monkeypatch)
    fake_api.room.create_room.side_effect = RuntimeError("secret-key-and-provider-detail")
    with pytest.raises(StudioError) as error:
        await broker.create()
    assert "secret" not in str(error.value)
    assert broker.phase == "idle"


async def test_worker_start_failure_cleans_created_room(broker, monkeypatch):
    _, fake_api = await fake_backend(broker, monkeypatch)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError()))
    with pytest.raises(StudioError):
        await broker.create()
    fake_api.room.delete_room.assert_awaited_once()
    assert not broker.lease.marker.exists()
    assert broker.current is None


async def test_only_confirmed_retired_rooms_are_ignored_on_reconnect(broker, monkeypatch):
    process, fake_api = await fake_backend(broker, monkeypatch)
    fake_api.room.list_rooms.return_value = SimpleNamespace(
        rooms=[SimpleNamespace(name="previous-owned-room")]
    )
    with pytest.raises(StudioError, match="already active"):
        await broker.create()
    broker.retired_rooms.append("previous-owned-room")
    await broker.create()
    owner = broker.current
    fake_api.room.list_rooms.return_value = SimpleNamespace(rooms=[])
    process.event(owner, "finished", safe=True)
    process.finish()
    await owner.monitor
    assert broker.phase == "idle"


async def test_status_observes_readiness_and_caches_discovery(broker, monkeypatch):
    check = AsyncMock(return_value=[])
    provider = SimpleNamespace(
        resolve_profile=AsyncMock(return_value=SimpleNamespace(name="Synthetic Voice")),
        model_readiness=AsyncMock(return_value=SimpleNamespace(loaded=True, downloaded=True)),
        aclose=AsyncMock(),
    )
    monkeypatch.setenv("VOICEBOX_PROFILE", "configured")
    monkeypatch.setattr(module, "check_setup", check)
    monkeypatch.setattr(module, "configured_provider", lambda: provider)
    first = await broker.status()
    second = await broker.status()
    assert first["ready"] and second["voice"]["cached"]
    assert second["voice"]["name"] == "Synthetic Voice"
    check.assert_awaited_once()
    provider.aclose.assert_awaited_once()
    broker.current = owned_session()
    broker.phase = "starting"
    state = await broker.status()
    assert not state["ready"]
    assert "Connecting" in state["message"]


async def test_status_timeout_is_an_explicit_problem(broker, monkeypatch):
    monkeypatch.setattr(module, "check_setup", AsyncMock(side_effect=TimeoutError()))
    state = await broker.status()
    assert not state["ready"]
    assert "Cannot verify" in state["problems"][0]


@pytest.mark.parametrize("reason", ["heartbeat", "startup", "duration"])
async def test_watchdog_ends_expired_owned_session(broker, monkeypatch, reason):
    owner = owned_session()
    broker.current = owner
    broker.phase = "active"
    if reason == "heartbeat":
        owner.heartbeat -= 50
    elif reason == "startup":
        broker.phase = "starting"
        owner.created -= 65
    else:
        owner.created -= 3605
    end = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(broker, "end", end)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await broker.watchdog()
    end.assert_awaited_once_with(owner.id)


async def test_room_cleanup_error_does_not_reopen_admission(broker, monkeypatch):
    owner = owned_session()
    process = Process()
    owner.process = process
    broker.current = owner
    broker.lease.mark_active()
    monkeypatch.setattr(broker, "_delete_room", AsyncMock(return_value=False))
    monitor = asyncio.create_task(broker._monitor(owner))
    process.event(owner, "finished", safe=True)
    process.finish()
    await monitor
    assert broker.phase == "blocked"
    assert broker.lease.marker.exists()


async def test_room_deletion_waits_for_exact_visibility(broker, monkeypatch):
    _, fake_api = await fake_backend(broker, monkeypatch)
    fake_api.room.list_rooms.side_effect = [
        SimpleNamespace(rooms=[SimpleNamespace(name="test-room")]),
        SimpleNamespace(rooms=[]),
    ]
    assert await broker._delete_room(owned_session())
    assert fake_api.room.list_rooms.await_count == 2


async def test_http_end_returns_draining_without_waiting_for_inference(client, broker, monkeypatch):
    broker.current = owned_session()
    broker.phase = "active"
    ended = asyncio.Event()

    async def end(owner):
        await ended.wait()

    monkeypatch.setattr(broker, "_end", end)
    response = await client.post(
        "/api/session/end",
        json={"sessionId": broker.current.id},
        headers={"Host": "127.0.0.1:8765", "X-Voicebox-Studio": "1"},
    )
    assert response.status == 200
    assert (await response.json())["phase"] == "draining"
    task = broker.current.end_task
    ended.set()
    await task


async def test_invalid_json_and_oversized_request_are_rejected(client):
    headers = {
        "Host": "127.0.0.1:8765",
        "X-Voicebox-Studio": "1",
        "Content-Type": "application/json",
    }
    response = await client.post("/api/session", data="{", headers=headers)
    assert response.status == 400
    response = await client.post("/api/session", data=" " * 5000, headers=headers)
    assert response.status == 413


async def test_ending_an_already_blocked_session_does_not_hide_block(broker, monkeypatch):
    owner = owned_session()
    broker.current = owner
    broker.phase = "blocked"
    monkeypatch.setattr(broker, "_end", AsyncMock())
    await broker.end(owner.id)
    await owner.end_task
    assert broker.phase == "blocked"


async def test_bundle_status_skips_voicebox_provider(broker, monkeypatch, tmp_path):
    monkeypatch.setenv("VOICEBOX_TTS_BACKEND", "mlx")
    monkeypatch.setenv("VOICEBOX_VOICE_BUNDLE", str(tmp_path / "voice"))
    monkeypatch.setenv("VOICEBOX_MLX_MODEL_PATH", str(tmp_path))
    (tmp_path / "model.safetensors").write_bytes(b"mock")
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(module.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _: object())
    check = AsyncMock(return_value=[])
    monkeypatch.setattr(module, "check_setup", check)
    provider = SimpleNamespace(
        resolve_profile=AsyncMock(return_value=SimpleNamespace(name="Imported", sample_count=1)),
        model_readiness=AsyncMock(return_value=SimpleNamespace(downloaded=True, loaded=False)),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(module, "FastQwenTTS", lambda **_: provider)
    monkeypatch.setattr(
        module, "configured_provider", lambda: pytest.fail("Voicebox HTTP requested")
    )
    state = await broker.status()
    assert state["ready"]
    assert state["voice"]["source"] == "local-bundle"
    check.assert_awaited_once_with(require_loaded=False, local_voice=True)


def test_startup_progress_is_owned_fixed_text_and_cleared_when_ready(broker):
    owner = owned_session()
    broker.current = owner
    broker.phase = "starting"
    event = {"key": owner.key, "event": "startup", "stage": "loading the existing local Qwen model"}
    broker._event(owner, {**event, "key": "wrong"})
    assert broker.message is None
    broker._event(owner, event)
    assert broker.message == "Loading the local Qwen speech model…"
    for invalid in ("private provider body", [], None, 1):
        broker._event(owner, {**event, "stage": invalid})
        assert broker.message == "Loading the local Qwen speech model…"
    broker._event(owner, {"key": owner.key, "event": "ready"})
    assert broker.phase == "active"
    assert broker.message is None
    broker._event(owner, event)
    assert broker.message is None


def test_late_startup_progress_cannot_hide_a_drain_failure(broker):
    owner = owned_session()
    broker.current = owner
    broker.phase = "draining"
    broker.message = "Waiting for safe cleanup."
    broker._event(owner, {"key": owner.key, "event": "startup", "stage": "conversation startup"})
    assert broker.message == "Waiting for safe cleanup."


async def test_startup_timeout_keeps_last_stage_and_requests_owned_cleanup(broker, monkeypatch):
    owner = owned_session()
    broker.current = owner
    broker.phase = "starting"
    owner.created = module.time.monotonic() - module.STARTUP_SECONDS - 1
    broker.message = "Loading the local Qwen speech model…"
    sleeps = 0

    async def sleep(_):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    monkeypatch.setattr(broker, "end", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await broker.watchdog()
    broker.end.assert_awaited_once_with(owner.id)
    assert broker.message == "Startup timed out. Loading the local Qwen speech model…"


@pytest.mark.parametrize("key", ["endOfUtteranceSeconds", "transcriptionDelaySeconds"])
def test_turn_timing_metrics_require_owned_finite_values(broker, key):
    owner = owned_session()
    broker.current = owner
    broker._event(owner, {"key": "wrong", "event": "metrics", key: 1.25})
    assert broker.metrics[key] is None
    for invalid in (True, -1, float("nan"), float("inf"), "secret"):
        broker._event(owner, {"key": owner.key, "event": "metrics", key: invalid})
        assert broker.metrics[key] is None
    broker._event(owner, {"key": owner.key, "event": "metrics", key: 1.25})
    assert broker.metrics[key] == 1.25


def test_metric_history_correlates_speech_only_and_keeps_first_frame(broker):
    owner = owned_session()
    broker.current = owner
    broker._event(
        owner, {"key": "wrong", "event": "metrics", "speechId": "speech-1", "ttsAudioSeconds": 10}
    )
    broker._event(owner, {"key": owner.key, "event": "metrics", "llmFirstTokenSeconds": 9})
    assert broker.turns == []
    for data in (
        {"speechId": "speech-1", "llmFirstTokenSeconds": 0.4},
        {"speechId": "speech-2", "llmFirstTokenSeconds": 0.8},
        {"speechId": "speech-1", "ttsFirstFrameSeconds": 0.3, "ttsAudioSeconds": 1.0},
        {"speechId": "speech-1", "ttsFirstFrameSeconds": 0.6, "ttsAudioSeconds": 2.0},
        {"speechId": "speech-1", "transcriptionDelaySeconds": float("nan")},
    ):
        broker._event(owner, {"key": owner.key, "event": "metrics", **data})
    assert broker.turns == [
        {
            "id": "speech-1",
            "llmFirstTokenSeconds": 0.4,
            "ttsFirstFrameSeconds": 0.3,
            "ttsAudioSeconds": 3.0,
        },
        {"id": "speech-2", "llmFirstTokenSeconds": 0.8},
    ]
    assert broker.metrics["ttsFirstFrameSeconds"] == 0.6


def test_metric_history_is_bounded_and_rejects_untrusted_identifiers(broker):
    owner = owned_session()
    broker.current = owner
    for identifier in (None, "", "/private/path", "x" * 129):
        broker._event(
            owner,
            {"key": owner.key, "event": "metrics", "speechId": identifier, "ttsAudioSeconds": 1},
        )
    assert broker.turns == []
    for number in range(22):
        broker._event(
            owner,
            {
                "key": owner.key,
                "event": "metrics",
                "speechId": f"speech-{number}",
                "ttsAudioSeconds": 1,
            },
        )
    assert len(broker.turns) == 20
    assert broker.turns[0]["id"] == "speech-2"


async def test_new_session_resets_metric_history(broker, monkeypatch):
    broker.turns = [{"id": "previous", "ttsAudioSeconds": 1}]
    monkeypatch.setattr(broker, "_create", AsyncMock(return_value={}))
    await broker.create()
    assert broker.turns == []


@pytest.mark.parametrize("reason", ["startup", "heartbeat", "duration", "user"])
async def test_audition_deadline_reason_survives_safe_drain(broker, monkeypatch, reason):
    owner = owned_session()
    owner.kind = "audition"
    owner.pcm.extend(b"\x01\x02")
    process = Process()
    owner.process = process
    broker.current = broker.audition = owner
    broker.phase = "active"
    broker.lease.mark_active()
    monkeypatch.setattr(broker, "_end", AsyncMock())
    if reason == "user":
        await broker.end_audition(owner.id)
    else:
        if reason == "startup":
            broker.phase = "starting"
            owner.created -= module.STARTUP_SECONDS + 1
        elif reason == "heartbeat":
            owner.heartbeat -= module.HEARTBEAT_SECONDS + 1
        else:
            owner.created -= module.AUDITION_SECONDS + 1
        sleeps = 0

        async def tick(_):
            nonlocal sleeps
            sleeps += 1
            if sleeps > 1:
                raise asyncio.CancelledError

        monkeypatch.setattr(module.asyncio, "sleep", tick)
        with pytest.raises(asyncio.CancelledError):
            await broker.watchdog()
    await owner.end_task
    if reason != "user":
        broker._event(owner, {"key": owner.key, "event": "error", "message": "Worker stopped."})
    broker._event(owner, {"key": owner.key, "event": "finished", "safe": True})
    process.finish()
    await broker._monitor(owner)
    result = broker.audition_status(owner.id)
    assert broker.phase == "idle"
    assert not broker.lease.marker.exists()
    assert not owner.pcm
    if reason == "user":
        assert result["state"] == "cancelled"
        assert result["message"] is None
    else:
        assert result["state"] == "failed"
        expected = {
            "startup": (
                "Voice sample startup timed out. Check local model readiness, then try again."
            ),
            "heartbeat": (
                "Voice sample stopped because the browser stopped responding. "
                "Keep Studio open and try again."
            ),
            "duration": "Voice sample generation reached its time limit. Try a shorter sample.",
        }
        assert result["message"] == expected[reason]


@pytest.mark.parametrize(
    "mode, expected", [("local", "livekit-server --dev"), ("configured", "credentials")]
)
async def test_livekit_connection_failure_can_retry_without_restart(
    broker, monkeypatch, mode, expected
):
    process, fake_api = await fake_backend(broker, monkeypatch)
    monkeypatch.setenv("VOICEBOX_LIVEKIT_MODE", mode)
    fake_api.room.list_rooms.side_effect = ConnectionError("private-details")
    with pytest.raises(StudioError, match=expected) as error:
        await broker.create()
    assert "private-details" not in str(error.value)
    assert broker.current is None and broker.phase == "idle"
    assert not broker.lease.marker.exists()
    fake_api.room.create_room.assert_not_awaited()
    fake_api.room.list_rooms.side_effect = None
    await broker.create()
    owner = broker.current
    process.event(owner, "ready")
    process.event(owner, "finished", safe=True)
    process.finish()
    await owner.monitor
    assert broker.phase == "idle"
    assert not broker.lease.marker.exists()


def test_reply_sequences_distinguish_interrupted_followup_from_normal_first_reply(broker):
    owner = owned_session()
    broker.current = owner
    started = {"key": owner.key, "event": "reply_started", "sequence": 1}
    completed = {"key": owner.key, "event": "reply_completed", "sequence": 1, "interrupted": True}
    broker._event(owner, {**started, "key": "wrong"})
    broker._event(owner, {**started, "sequence": True})
    assert owner.started_replies == 0
    broker._event(owner, started)
    broker._event(owner, {**started, "sequence": 2})
    broker._event(owner, {**completed, "sequence": 2})
    broker._event(owner, {**completed, "interrupted": False})
    assert owner.started_replies == 2
    assert 1 not in owner.interrupted_reply_sequences
    assert owner.interrupted_reply_sequences == [2]
    # The first reply can be interrupted even if it has no chat item.
    broker._event(owner, completed)
    broker._event(owner, completed)
    assert owner.interrupted_reply_sequences == [2, 1]
    replacement = owned_session()
    broker.current = replacement
    broker._event(owner, {**started, "sequence": 3})
    assert owner.started_replies == 2
    assert replacement.started_replies == 0
    replacement.kind = "audition"
    broker._event(replacement, started)
    assert replacement.started_replies == 0
