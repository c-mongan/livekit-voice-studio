import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from examples import studio_worker
from examples.hermes_llm import HermesLLM


@pytest.mark.parametrize("drain_succeeds", [True, False])
async def test_rpc_registration_follows_connection_and_shutdown_drains(monkeypatch, drain_succeeds):
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
                order.append("rpc")
                handlers["rpc"] = callback

            return SimpleNamespace(register_rpc_method=register)

        async def disconnect(self):
            order.append("disconnect")

    async def start(**kwargs):
        assert kwargs["record"] is False
        with pytest.raises(studio_worker.rtc.RpcError) as error:
            await handlers["rpc"](SimpleNamespace(caller_identity="another-participant"))
        assert error.value.code == 1403
        result = json.loads(await handlers["rpc"](SimpleNamespace(caller_identity="user")))
        repeated = json.loads(await handlers["rpc"](SimpleNamespace(caller_identity="user")))
        assert result == repeated == {
            "stoppedPlayback": True,
            "hermesStopRequested": True,
            "actionUndone": False,
            "backendState": "ready",
        }
        handlers["disconnected"](1)
        order.append("session-start")

    async def drain():
        order.append("provider-drained")
        if not drain_succeeds:
            raise RuntimeError("Unresolved backend")

    async def stop_hermes():
        order.append("hermes-stop")
        # The RPC reports that a stop was requested, not that an external action was undone.
        return False

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
        interrupt=AsyncMock(side_effect=lambda **_: order.append("playback-stopped")),
    )
    speech = SimpleNamespace(aclose=AsyncMock())
    model = Mock(spec=HermesLLM)
    model.active_run_id = "run-1"
    model.stop_active = AsyncMock(side_effect=stop_hermes)
    model.aclose = AsyncMock()
    monkeypatch.setattr(studio_worker.rtc, "Room", Room)
    monkeypatch.setattr(studio_worker, "configured_provider", lambda: provider)
    monkeypatch.setattr(studio_worker, "configured_ai", AsyncMock(return_value=(speech, model)))
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
    assert order.index("connect") < order.index("rpc")
    assert order.index("playback-stopped") < order.index("hermes-stop")
    assert order.index("provider-drained") < order.index("disconnect")
    assert reports[-1] == ("finished", {"safe": drain_succeeds})
    assert session.interrupt.await_count == 3
    session.interrupt.assert_awaited_with(force=True)
    model.stop_active.assert_awaited_once_with()
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
