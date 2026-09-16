import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from examples import studio_worker
from examples.hermes_llm import ApprovalRequest, ApprovalResolution, HermesLLM, ToolStatus


async def test_approval_request_is_bounded_and_targeted_to_owner() -> None:
    participant = SimpleNamespace(publish_data=AsyncMock())
    room = SimpleNamespace(local_participant=participant)
    request = ApprovalRequest("run-1", "request-1", "x" * 700, ("once", "deny"))

    await studio_worker.publish_approval(room, "owner", request)

    participant.publish_data.assert_awaited_once()
    call = participant.publish_data.await_args
    payload = json.loads(call.args[0])
    assert payload == {
        "runId": "run-1",
        "requestId": "request-1",
        "command": "x" * 500,
        "choices": ["once", "deny"],
    }
    assert len(call.args[0].encode("utf-8")) <= 4096
    assert call.kwargs == {
        "reliable": True,
        "destination_identities": ["owner"],
        "topic": "hermes.approval.request",
    }


async def test_approval_request_rejects_duplicate_choices_before_publish() -> None:
    participant = SimpleNamespace(publish_data=AsyncMock())
    room = SimpleNamespace(local_participant=participant)
    request = ApprovalRequest("run-1", "request-1", "bounded command", ("once", "once"))

    with pytest.raises(ValueError, match="invalid"):
        await studio_worker.publish_approval(room, "owner", request)

    participant.publish_data.assert_not_awaited()


async def test_approval_resolution_is_exact_reliable_and_targeted_to_owner() -> None:
    participant = SimpleNamespace(publish_data=AsyncMock())
    room = SimpleNamespace(local_participant=participant)

    await studio_worker.publish_approval_resolution(
        room, "owner", ApprovalResolution("run-1", "request-1")
    )

    participant.publish_data.assert_awaited_once_with(
        '{"runId":"run-1","requestId":"request-1"}',
        reliable=True,
        destination_identities=["owner"],
        topic="hermes.approval.resolved",
    )


async def test_tool_status_is_bounded_reliable_and_targeted_to_owner() -> None:
    participant = SimpleNamespace(publish_data=AsyncMock())
    room = SimpleNamespace(local_participant=participant)
    status = ToolStatus(
        "run-1", "run-1:2", "completed", "read_file", "fixture result", 0.125, False
    )

    await studio_worker.publish_tool_status(room, "owner", status)

    participant.publish_data.assert_awaited_once_with(
        '{"runId":"run-1","eventId":"run-1:2","phase":"completed","tool":"read_file",'
        '"preview":"fixture result","duration":0.125,"error":false}',
        reliable=True,
        destination_identities=["owner"],
        topic="hermes.tool.status",
    )


@pytest.mark.parametrize(
    "payload",
    [
        '{"runId":"run-1","requestId":"request-1","choice":"once","extra":true}',
        '{"runId":"run-1","requestId":"request-1","choice":"secret"}',
        '{"runId":"run-1","requestId":"request-1","choice":"once"} trailing',
        "{",
        "x" * 4097,
    ],
)
async def test_approval_rpc_rejects_unknown_invalid_or_oversized_payloads(payload: str) -> None:
    model = Mock(spec=HermesLLM)
    model.respond_to_approval = AsyncMock()

    with pytest.raises(studio_worker.rtc.RpcError):
        await studio_worker.respond_to_approval_rpc(
            model, "owner", SimpleNamespace(caller_identity="owner", payload=payload)
        )

    model.respond_to_approval.assert_not_awaited()


async def test_approval_rpc_requires_owner_and_exact_identifiers() -> None:
    model = Mock(spec=HermesLLM)
    model.respond_to_approval = AsyncMock()
    payload = json.dumps({"runId": "run-1", "requestId": "request-1", "choice": "deny"})

    with pytest.raises(studio_worker.rtc.RpcError) as denied:
        await studio_worker.respond_to_approval_rpc(
            model, "owner", SimpleNamespace(caller_identity="intruder", payload=payload)
        )
    assert denied.value.code == 1403
    assert json.loads(
        await studio_worker.respond_to_approval_rpc(
            model, "owner", SimpleNamespace(caller_identity="owner", payload=payload)
        )
    ) == {"accepted": True}
    model.respond_to_approval.assert_awaited_once_with("run-1", "request-1", "deny")


@pytest.mark.parametrize("drain_succeeds", [True, False])
@pytest.mark.parametrize("terminal_acknowledged", [True, False])
async def test_rpc_registration_follows_connection_and_shutdown_drains(
    monkeypatch, drain_succeeds, terminal_acknowledged
):
    reports = []
    order = []
    handlers = {}

    class Room:
        connected = False

        def on(self, name):
            def register(callback):
                handlers[name] = callback
                return callback

            return register

        async def connect(self, url, token):
            from livekit import api

            claims = api.TokenVerifier("unit-key", "unit-secret-more-than-32-characters").verify(
                token
            )
            assert claims.video.can_update_own_metadata
            self.connected = True
            order.append("connect")

        @property
        def local_participant(self):
            assert self.connected, "RPC cannot be registered before the local participant exists."

            def register(name, callback):
                order.append(f"rpc:{name}")
                handlers[name] = callback

            async def publish_data(payload, **kwargs):
                order.append("approval-published")
                handlers["published"] = (payload, kwargs)

            return SimpleNamespace(register_rpc_method=register, publish_data=publish_data)

        async def disconnect(self):
            order.append("disconnect")

    playback_started = asyncio.Event()
    finish_playback = asyncio.Event()

    async def start(**kwargs):
        assert kwargs["record"] is False
        assert approval_callback is not None
        assert tool_status_callback is not None
        await tool_status_callback(
            ToolStatus("run-tool", "run-tool:1", "completed", "read_file", "fixture", 0.1, False)
        )
        tool_status_payload, tool_status_options = handlers["published"]
        assert json.loads(tool_status_payload)["phase"] == "completed"
        assert tool_status_options["topic"] == "hermes.tool.status"
        await approval_callback(
            ApprovalRequest("run-approval", "request-approval", "redacted", ("once", "deny"))
        )
        published, publish_options = handlers["published"]
        assert json.loads(published)["command"] == "redacted"
        assert publish_options["destination_identities"] == ["user"]
        assert resolution_callback is not None
        await resolution_callback(ApprovalResolution("run-approval", "request-approval"))
        resolved, resolution_options = handlers["published"]
        assert json.loads(resolved) == {
            "runId": "run-approval",
            "requestId": "request-approval",
        }
        assert resolution_options == {
            "reliable": True,
            "destination_identities": ["user"],
            "topic": "hermes.approval.resolved",
        }
        approval_result = await handlers["hermes.approval.respond"](
            SimpleNamespace(
                caller_identity="user",
                payload=json.dumps(
                    {
                        "runId": "run-approval",
                        "requestId": "request-approval",
                        "choice": "deny",
                    }
                ),
            )
        )
        assert json.loads(approval_result) == {"accepted": True}
        with pytest.raises(studio_worker.rtc.RpcError) as error:
            await handlers["voicebox.interrupt"](
                SimpleNamespace(caller_identity="another-participant")
            )
        assert error.value.code == 1403
        first = asyncio.create_task(
            handlers["voicebox.interrupt"](SimpleNamespace(caller_identity="user"))
        )
        repeated_call = asyncio.create_task(
            handlers["voicebox.interrupt"](SimpleNamespace(caller_identity="user"))
        )
        await playback_started.wait()
        await asyncio.sleep(0)
        finish_playback.set()
        result, repeated = [
            json.loads(value) for value in await asyncio.gather(first, repeated_call)
        ]
        assert (
            result
            == repeated
            == {
                "stoppedPlayback": True,
                "hermesStopRequested": True,
                "hermesTerminalAcknowledged": terminal_acknowledged,
                "actionUndone": False,
                "backendState": "ready",
            }
        )
        handlers["disconnected"](1)
        order.append("session-start")

    async def drain():
        order.append("provider-drained")
        if not drain_succeeds:
            raise RuntimeError("Unresolved backend")

    async def stop_hermes():
        order.append(f"wrong-hermes-stop:{model.active_run_id}")
        # The RPC reports that a stop was requested, not that an external action was undone.
        return False

    exact_stops = []

    async def stop_exact(run):
        exact_stops.append(run.run_id)
        order.append(f"hermes-stop:{run.run_id}")
        return terminal_acknowledged

    interrupt_calls = 0

    async def interrupt_playback(**_kwargs):
        nonlocal interrupt_calls
        interrupt_calls += 1
        order.append("playback-stopped")
        if interrupt_calls <= 2:
            model.active_run_id = "run-2"
            playback_started.set()
            await finish_playback.wait()

    provider = SimpleNamespace(
        on=Mock(),
        resolve_profile=AsyncMock(),
        check_idle=AsyncMock(),
        model_readiness=AsyncMock(return_value=SimpleNamespace(downloaded=True, loaded=True)),
        aclose=AsyncMock(side_effect=drain),
        backend_state="ready",
    )
    session = SimpleNamespace(
        on=lambda name: lambda callback: callback,
        start=AsyncMock(side_effect=start),
        aclose=AsyncMock(),
        interrupt=AsyncMock(side_effect=interrupt_playback),
    )
    speech = SimpleNamespace(aclose=AsyncMock())
    model = Mock(spec=HermesLLM)
    model.active_run_id = "run-1"
    model.capture_active_run = Mock(
        side_effect=lambda: SimpleNamespace(run_id=model.active_run_id)
        if model.active_run_id is not None
        else None
    )
    model.stop_run = AsyncMock(side_effect=stop_exact)
    model.stop_active = AsyncMock(side_effect=stop_hermes)
    model.respond_to_approval = AsyncMock()
    model.aclose = AsyncMock()
    approval_callback = None
    resolution_callback = None
    tool_status_callback = None

    async def configure_ai(*, on_approval=None, on_approval_resolved=None, on_tool_status=None):
        nonlocal approval_callback, resolution_callback, tool_status_callback
        approval_callback = on_approval
        resolution_callback = on_approval_resolved
        tool_status_callback = on_tool_status
        return speech, model

    monkeypatch.setattr(studio_worker.rtc, "Room", Room)
    monkeypatch.setattr(studio_worker, "configured_provider", lambda: provider)
    monkeypatch.setattr(studio_worker, "configured_ai", configure_ai)
    monkeypatch.setattr(studio_worker, "AgentSession", Mock(return_value=session))
    monkeypatch.setattr(
        studio_worker, "report", lambda event, **data: reports.append((event, data))
    )
    from livekit.plugins import silero

    monkeypatch.setattr(silero.VAD, "load", Mock(return_value=object()))
    for key, value in {
        "STUDIO_PARTICIPANT_IDENTITY": "user",
        "STUDIO_AGENT_IDENTITY": "agent",
        "STUDIO_ROOM": "room",
        "LIVEKIT_URL": "wss://unit.invalid",
        "LIVEKIT_API_KEY": "unit-key",
        "LIVEKIT_API_SECRET": "unit-secret-more-than-32-characters",
    }.items():
        monkeypatch.setenv(key, value)
    await studio_worker.run()
    assert studio_worker.AgentSession.call_args.kwargs["turn_handling"] == {
        "turn_detection": "vad",
        "interruption": {"mode": "vad"},
        "preemptive_generation": {"enabled": False},
    }
    assert order.index("connect") < order.index("rpc:voicebox.interrupt")
    assert order.index("connect") < order.index("rpc:hermes.approval.respond")
    assert order.index("rpc:hermes.approval.respond") < order.index("approval-published")
    assert order.index("playback-stopped") < order.index("hermes-stop:run-1")
    assert order.index("provider-drained") < order.index("disconnect")
    assert reports[-1] == ("finished", {"safe": drain_succeeds})
    assert not any(
        event == "error" and data["message"].startswith("Failed during conversation startup")
        for event, data in reports
    )
    assert session.interrupt.await_count == 3
    session.interrupt.assert_awaited_with(force=True)
    assert exact_stops == ["run-1"]
    model.stop_active.assert_not_awaited()
    model.respond_to_approval.assert_awaited_once_with("run-approval", "request-approval", "deny")
    assert studio_worker.AgentSession.call_args.kwargs["llm"] is model
    assert session.start.await_args.kwargs["agent"].instructions == (
        "Reply for speech: concise plain text unless detail is needed. "
        "Hermes owns tools, memory, and approvals. Never claim an action was undone "
        "because playback stopped."
    )


def test_disconnected_supervisor_does_not_prevent_cleanup(monkeypatch, caplog):
    monkeypatch.setenv("STUDIO_EVENT_KEY", "unit-private-ipc")
    monkeypatch.setattr("builtins.print", Mock(side_effect=BrokenPipeError))
    studio_worker.report("draining")
    assert "cleanup must still drain" in caplog.text
    assert "unit-private-ipc" not in caplog.text


async def test_provider_construction_failure_reports_safe_exit(monkeypatch):
    reports = []
    monkeypatch.setenv("STUDIO_PARTICIPANT_IDENTITY", "user")
    monkeypatch.setenv("VOICEBOX_TTS_BACKEND", "voicebox")
    monkeypatch.setattr(
        studio_worker, "configured_provider", Mock(side_effect=ValueError("config"))
    )
    monkeypatch.setattr(
        studio_worker, "report", lambda event, **data: reports.append((event, data))
    )
    await studio_worker.run()
    assert reports[-1] == ("finished", {"safe": True})
    assert reports[0] == ("startup", {"stage": "voice provider configuration"})
    errors = [data for event, data in reports if event == "error"]
    assert len(errors) == 1
    assert "voice provider configuration" in errors[0]["message"]


def test_local_recognizer_finalize_is_owned_and_explicit():
    from examples.nemotron_stt import NemotronSTT

    provider = NemotronSTT()
    stream = Mock()
    stream._input_ch.closed = False
    stream._event_ch.closed = False
    stream._task.done.return_value = False
    stream._overloaded.is_set.return_value = False
    provider._streams.add(stream)
    provider.commit_utterance()
    stream.flush.assert_called_once()
