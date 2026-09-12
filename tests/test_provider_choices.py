import sys
from types import ModuleType
from unittest.mock import AsyncMock, Mock

import pytest

from examples import minimal_agent as module


@pytest.fixture(autouse=True)
def configuration(monkeypatch):
    for name in module.AZURE_REQUIRED:
        monkeypatch.setenv(name, "configured")
    monkeypatch.setenv("VOICEBOX_AI_PROVIDER", "azure")
    monkeypatch.setenv("VOICEBOX_PROFILE", "Selected")
    monkeypatch.setenv("VOICEBOX_EXCLUSIVE", "1")
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setenv(name, "private")
    monkeypatch.setattr(module, "azure_resource_key", AsyncMock(return_value="private-azure"))


async def test_copilot_reasoning_keeps_azure_speech_independent(monkeypatch):
    from livekit.plugins import azure

    agent_module = ModuleType("examples.agent_llm")
    instance = Mock()
    instance.aclose = AsyncMock()
    constructor = Mock(return_value=instance)
    agent_module.AgentLLM = constructor
    monkeypatch.setitem(sys.modules, "examples.agent_llm", agent_module)
    monkeypatch.setenv("VOICEBOX_STT_PROVIDER", "azure")
    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", "copilot")
    monkeypatch.setenv("VOICEBOX_LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("VOICEBOX_REASONING_EFFORT", "low")
    stt = Mock()
    monkeypatch.setattr(azure, "STT", stt)
    speech, model = await module.configured_ai()
    assert model is instance and speech is stt.return_value
    assert constructor.call_args.kwargs["provider"] == "copilot"
    assert constructor.call_args.kwargs["model"] == "gpt-5.6-luna"
    assert constructor.call_args.kwargs["reasoning_effort"] == "low"
    stt.assert_called_once()
    module.azure_resource_key.assert_awaited_once()


async def test_nemotron_recognition_keeps_azure_reasoning(monkeypatch):
    nemotron = ModuleType("examples.nemotron_stt")
    constructor = Mock()
    nemotron.NemotronSTT = constructor
    monkeypatch.setitem(sys.modules, "examples.nemotron_stt", nemotron)
    monkeypatch.setenv("VOICEBOX_STT_PROVIDER", "nemotron")
    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", "azure")
    monkeypatch.setattr(module.openai.LLM, "with_azure", Mock())
    speech, _ = await module.configured_ai()
    assert speech is constructor.return_value
    assert constructor.call_args.kwargs["base_url"] == "http://127.0.0.1:8766"
    module.azure_resource_key.assert_awaited_once()


async def test_unknown_provider_never_falls_back(monkeypatch):
    monkeypatch.setenv("VOICEBOX_STT_PROVIDER", "missing")
    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", "azure")
    with pytest.raises(RuntimeError, match="speech"):
        await module.configured_ai()
    module.azure_resource_key.assert_not_awaited()


def test_native_agent_handoff_metadata_is_not_tool_history():
    from livekit.agents import llm

    from examples.agent_llm import _messages

    context = llm.ChatContext()
    context.items.append(llm.AgentHandoff(new_agent_id="voice-assistant"))
    context.add_message(role="user", content="Hello")
    assert _messages(context) == [{"role": "user", "content": "Hello"}]


def test_native_agent_configuration_preserves_instructions_without_tools():
    from livekit.agents import llm

    from examples.agent_llm import _messages

    context = llm.ChatContext()
    context.items.append(llm.AgentConfigUpdate(instructions="Reply briefly.", tools_added=[]))
    context.add_message(role="user", content="Hello")
    assert _messages(context) == [
        {"role": "system", "content": "Reply briefly."},
        {"role": "user", "content": "Hello"},
    ]
