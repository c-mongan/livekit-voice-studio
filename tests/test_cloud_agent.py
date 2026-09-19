"""Offline contracts for the separate stock-voice learning agent."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from examples import cloud_agent


def test_stage_tool_explains_real_pipeline_roles():
    assert "words" in cloud_agent.explain_stage("stt").lower()
    assert "audio" in cloud_agent.explain_stage("tts").lower()
    assert "permission" in cloud_agent.explain_stage("token").lower()
    assert "room" in cloud_agent.explain_stage("dispatch").lower()


@pytest.mark.parametrize("stage", ["", "STT", "shell", "/private/voice.wav"])
def test_stage_tool_rejects_unknown_requests_without_echoing_input(stage):
    with pytest.raises(ValueError, match="Choose a stage:") as error:
        cloud_agent.explain_stage(stage)
    assert "/private/" not in str(error.value)


def test_factory_passes_explicit_model_overrides_and_does_not_enable_recording(monkeypatch):
    calls = {}

    def factory(name):
        def create(**kwargs):
            calls[name] = kwargs
            return name

        return create

    for name in ("STT", "LLM", "TTS"):
        monkeypatch.setattr(cloud_agent.inference, name, factory(name))
    monkeypatch.setattr(cloud_agent.silero.VAD, "load", lambda: "vad")
    monkeypatch.setattr(cloud_agent, "AgentSession", factory("session"))
    assert (
        cloud_agent.build_session(
            {
                "LEARNING_STT_MODEL": "example/stt",
                "LEARNING_LLM_MODEL": "example/llm",
                "LEARNING_TTS_MODEL": "example/tts",
                "LEARNING_TTS_VOICE": "Example",
            }
        )
        == "session"
    )
    assert calls["STT"] == {"model": "example/stt", "language": "en"}
    assert calls["LLM"] == {"model": "example/llm"}
    assert calls["TTS"] == {"model": "example/tts", "voice": "Example"}
    assert calls["session"]["vad"] == "vad"
    assert calls["session"]["turn_handling"]["turn_detection"] == "vad"


async def test_dispatched_entrypoint_starts_room_with_recording_off(monkeypatch):
    session = SimpleNamespace(start=AsyncMock(), generate_reply=AsyncMock())
    monkeypatch.setattr(cloud_agent, "build_session", lambda: session)
    room = object()
    await cloud_agent.entrypoint(SimpleNamespace(room=room))
    args = session.start.call_args.kwargs
    assert args["room"] is room
    assert args["record"] is False
    assert isinstance(args["agent"], cloud_agent.LearningAgent)
    session.generate_reply.assert_not_called()


async def test_agent_registers_the_validated_tool():
    agent = cloud_agent.LearningAgent()
    assert len(agent.tools) == 1
    assert await agent.tools[0]("room") == cloud_agent.explain_stage("room")
