import sys
from types import ModuleType, SimpleNamespace
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


async def test_hermes_reasoning_keeps_nemotron_independent_and_preflights(monkeypatch):
    nemotron = ModuleType("examples.nemotron_stt")
    speech = Mock()
    nemotron.NemotronSTT = Mock(return_value=speech)
    monkeypatch.setitem(sys.modules, "examples.nemotron_stt", nemotron)
    monkeypatch.setenv("VOICEBOX_STT_PROVIDER", "nemotron")
    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", "hermes")
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "server-secret")
    monkeypatch.setenv("HERMES_API_BASE_URL", "https://hermes.example")
    monkeypatch.setenv("HERMES_PROFILE", "voice-profile")
    monkeypatch.setenv("HERMES_VOICE_SESSION_ID", "voice-session")
    client = SimpleNamespace(preflight=AsyncMock(), aclose=AsyncMock())
    client_constructor = Mock(return_value=client)
    model = Mock()
    model.aclose = AsyncMock()
    model_constructor = Mock(return_value=model)
    monkeypatch.setattr(module, "HermesRunsClient", client_constructor, raising=False)
    monkeypatch.setattr(module, "HermesLLM", model_constructor, raising=False)
    approval = AsyncMock()

    selected_speech, selected_model = await module.configured_ai(on_approval=approval)

    assert selected_speech is speech
    assert selected_model is model
    config = client_constructor.call_args.args[0]
    assert config.base_url == "https://hermes.example"
    assert config.api_key == "server-secret"
    assert config.profile == "voice-profile"
    assert config.session_id == "voice-session"
    client.preflight.assert_awaited_once_with()
    assert model_constructor.call_args.kwargs == {"client": client, "on_approval": approval}


async def test_hermes_default_approval_callback_fails_closed(monkeypatch):
    monkeypatch.setenv("VOICEBOX_STT_PROVIDER", "openai")
    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", "hermes")
    monkeypatch.setenv("OPENAI_API_KEY", "speech-secret")
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "server-secret")
    monkeypatch.setenv("HERMES_API_BASE_URL", "http://127.0.0.1:8642")
    monkeypatch.setenv("HERMES_PROFILE", "default")
    monkeypatch.setenv("HERMES_VOICE_SESSION_ID", "voice-session")
    client = SimpleNamespace(preflight=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(module, "HermesRunsClient", Mock(return_value=client), raising=False)
    constructor = Mock(return_value=Mock(aclose=AsyncMock()))
    monkeypatch.setattr(module, "HermesLLM", constructor, raising=False)

    await module.configured_ai()

    callback = constructor.call_args.kwargs["on_approval"]
    with pytest.raises(RuntimeError, match="approval"):
        await callback(Mock())


async def test_hermes_readiness_preflights_capabilities_without_voice_session(monkeypatch):
    monkeypatch.setenv("VOICEBOX_STT_PROVIDER", "openai")
    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", "hermes")
    monkeypatch.setenv("OPENAI_API_KEY", "speech-secret")
    monkeypatch.setenv("HERMES_API_SERVER_KEY", "server-secret")
    monkeypatch.setenv("HERMES_API_BASE_URL", "https://hermes.example")
    monkeypatch.setenv("HERMES_PROFILE", "voice-profile")
    monkeypatch.delenv("HERMES_VOICE_SESSION_ID", raising=False)
    client = SimpleNamespace(preflight=AsyncMock(), aclose=AsyncMock())
    constructor = Mock(return_value=client)
    monkeypatch.setattr(module, "HermesRunsClient", constructor, raising=False)

    problems = await module.check_setup(local_voice=True)

    assert problems == []
    config = constructor.call_args.args[0]
    assert config.profile == "voice-profile"
    assert config.session_id == "studio-readiness"
    client.preflight.assert_awaited_once_with()
    client.aclose.assert_awaited_once_with()


async def test_hermes_readiness_requires_server_key_and_secure_remote_url(monkeypatch):
    monkeypatch.setenv("VOICEBOX_STT_PROVIDER", "openai")
    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", "hermes")
    monkeypatch.setenv("OPENAI_API_KEY", "speech-secret")
    monkeypatch.delenv("HERMES_API_SERVER_KEY", raising=False)
    monkeypatch.setenv("HERMES_API_BASE_URL", "http://hermes.example")
    monkeypatch.setenv("HERMES_PROFILE", "default")
    constructor = Mock()
    monkeypatch.setattr(module, "HermesRunsClient", constructor, raising=False)

    problems = await module.check_setup(local_voice=True)

    assert any("HERMES_API_SERVER_KEY" in problem for problem in problems)
    assert any("HTTPS" in problem for problem in problems)
    constructor.assert_not_called()


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
