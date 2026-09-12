import asyncio
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from livekit.plugins.voicebox.errors import ConfigurationError


@pytest.fixture(autouse=True)
def default_ai_provider(monkeypatch):
    monkeypatch.delenv("VOICEBOX_AI_PROVIDER", raising=False)


async def test_example_admits_only_one_room_per_worker():
    example = runpy.run_path("examples/minimal_agent.py")
    first = SimpleNamespace(accept=AsyncMock(), reject=AsyncMock())
    second = SimpleNamespace(accept=AsyncMock(), reject=AsyncMock())
    await example["accept_one_room"](first)
    await example["accept_one_room"](second)
    first.accept.assert_awaited_once()
    first.reject.assert_not_awaited()
    second.accept.assert_not_awaited()
    second.reject.assert_awaited_once()


@pytest.mark.parametrize("loaded,downloaded", [(True, True), (False, True), (False, False)])
async def test_readiness_is_read_only_and_reports_model_state(monkeypatch, loaded, downloaded):
    example = runpy.run_path("examples/minimal_agent.py")
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "secret-that-must-not-appear")
    monkeypatch.setenv("VOICEBOX_PROFILE", "Test Voice")
    monkeypatch.setenv("VOICEBOX_EXCLUSIVE", "1")
    provider = AsyncMock()
    provider.__aenter__.return_value = provider
    provider.model_readiness.return_value = SimpleNamespace(
        loaded=loaded, downloaded=downloaded, downloading=False
    )
    check = example["check_setup"]
    monkeypatch.setitem(check.__globals__, "configured_provider", lambda: provider)
    problems = await check()
    assert bool(problems) == (not loaded or not downloaded)
    assert "secret-that-must-not-appear" not in str(problems)
    provider.health.assert_awaited_once()
    provider.resolve_profile.assert_awaited_once()
    provider.check_idle.assert_awaited_once()
    provider.synthesize.assert_not_called()
    provider.__aexit__.assert_awaited_once()


async def test_missing_environment_reports_names_without_http(monkeypatch):
    example = runpy.run_path("examples/minimal_agent.py")
    for name in (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "OPENAI_API_KEY",
        "VOICEBOX_PROFILE",
        "VOICEBOX_EXCLUSIVE",
    ):
        monkeypatch.delenv(name, raising=False)
    problems = await example["check_setup"]()
    assert len(problems) == 6
    assert any("LIVEKIT_API_SECRET" in message for message in problems)


async def test_preflight_rejects_active_backend(monkeypatch):
    example = runpy.run_path("examples/minimal_agent.py")
    monkeypatch.setenv("VOICEBOX_PROFILE", "Test Voice")
    provider = AsyncMock()
    provider.__aenter__.return_value = provider
    provider.check_idle.side_effect = ConfigurationError("Voicebox has active work.")
    check = example["check_setup"]
    monkeypatch.setitem(check.__globals__, "configured_provider", lambda: provider)
    problems = await check()
    assert any("active work" in problem for problem in problems)
    provider.synthesize.assert_not_called()
    provider.__aexit__.assert_awaited_once()


async def test_azure_config_does_not_require_openai_key(monkeypatch):
    example = runpy.run_path("examples/minimal_agent.py")
    monkeypatch.setenv("VOICEBOX_AI_PROVIDER", "azure")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("VOICEBOX_PROFILE", "Test Voice")
    monkeypatch.setenv("VOICEBOX_EXCLUSIVE", "1")
    for name in (
        *example["AZURE_REQUIRED"],
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ):
        monkeypatch.setenv(name, "configured")
    provider = AsyncMock()
    provider.__aenter__.return_value = provider
    provider.model_readiness.return_value = SimpleNamespace(
        loaded=True, downloaded=True, downloading=False
    )
    check = example["check_setup"]
    monkeypatch.setitem(check.__globals__, "configured_provider", lambda: provider)
    monkeypatch.setattr(example["shutil"], "which", lambda _: "/mock/az")
    assert await check() == []


async def test_azure_key_stays_in_memory(monkeypatch, capsys):
    example = runpy.run_path("examples/minimal_agent.py")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "test-group")
    monkeypatch.setattr(example["shutil"], "which", lambda _: "/mock/az")
    process = SimpleNamespace(
        communicate=AsyncMock(return_value=(b"private-test-key\n", b"")),
        returncode=0,
    )
    execute = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", execute)
    assert await example["azure_resource_key"]("test-account") == "private-test-key"
    assert "private-test-key" not in str(execute.call_args)
    assert "private-test-key" not in capsys.readouterr().out


async def test_azure_cli_error_is_sanitized(monkeypatch):
    example = runpy.run_path("examples/minimal_agent.py")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "test-group")
    monkeypatch.setattr(example["shutil"], "which", lambda _: "/mock/az")
    process = SimpleNamespace(
        communicate=AsyncMock(return_value=(b"", b"private-token-in-error")),
        returncode=1,
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(RuntimeError) as error:
        await example["azure_resource_key"]("test-account")
    assert "private-token" not in str(error.value)


async def test_azure_cli_cancel_stops_subprocess(monkeypatch):
    example = runpy.run_path("examples/minimal_agent.py")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "test-group")
    monkeypatch.setattr(example["shutil"], "which", lambda _: "/mock/az")
    process = SimpleNamespace(
        communicate=AsyncMock(side_effect=asyncio.CancelledError()),
        returncode=None,
        kill=Mock(),
        wait=AsyncMock(),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(asyncio.CancelledError):
        await example["azure_resource_key"]("test-account")
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


async def test_azure_provider_factory(monkeypatch):
    from livekit.plugins import azure, openai

    example = runpy.run_path("examples/minimal_agent.py")
    monkeypatch.setenv("VOICEBOX_AI_PROVIDER", "azure")
    for name in example["AZURE_REQUIRED"]:
        monkeypatch.setenv(name, "configured")
    factory = example["configured_ai"]
    key = AsyncMock(side_effect=["llm-private", "speech-private"])
    monkeypatch.setitem(factory.__globals__, "azure_resource_key", key)
    speech = Mock()
    model = Mock()
    monkeypatch.setattr(azure, "STT", speech)
    monkeypatch.setattr(openai.LLM, "with_azure", model)
    await factory()
    assert speech.call_args.kwargs["speech_key"] == "speech-private"
    assert model.call_args.kwargs["api_key"] == "llm-private"
    assert key.await_count == 2


async def test_room_start_disables_recording_and_uses_local_vad(monkeypatch):
    example = runpy.run_path("examples/minimal_agent.py")
    monkeypatch.setenv("VOICEBOX_EXCLUSIVE", "1")
    entry = example["entrypoint"]
    provider = AsyncMock()
    provider.model_readiness.return_value = SimpleNamespace(downloaded=True, loaded=True)
    speech, model = AsyncMock(), AsyncMock()
    session = SimpleNamespace(start=AsyncMock(), say=AsyncMock())
    construct = Mock(return_value=session)
    monkeypatch.setitem(entry.__globals__, "configured_provider", lambda: provider)
    monkeypatch.setitem(entry.__globals__, "configured_ai", AsyncMock(return_value=(speech, model)))
    monkeypatch.setitem(entry.__globals__, "AgentSession", construct)
    monkeypatch.setattr(example["silero"].VAD, "load", Mock(return_value=object()))
    context = SimpleNamespace(room=object(), add_shutdown_callback=Mock())
    await entry(context)
    assert session.start.call_args.kwargs["record"] is False
    assert construct.call_args.kwargs["turn_handling"]["interruption"]["mode"] == "vad"
    session.say.assert_awaited_once()
