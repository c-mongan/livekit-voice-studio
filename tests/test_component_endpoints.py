import json
import os

import pytest
from aiohttp import web

from examples.studio_library import LibraryError, StudioLibrary


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    from examples.component_endpoints import livekit_configuration

    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(livekit_configuration, "original", None)


def test_new_library_is_local_and_legacy_keeps_provider(tmp_path):
    library = StudioLibrary(tmp_path)
    assert library.settings()["livekitMode"] == "local"
    assert library.settings()["llmProvider"] == "ollama"
    old = dict(
        library.settings(), llmProvider="copilot", llmModel="gpt-5.6-luna", reasoningEffort="low"
    )
    del old["livekitMode"]
    del old["llmBaseUrl"]
    library.settings_path.write_text(json.dumps(old))
    assert library.settings()["livekitMode"] == "configured"
    assert library.settings()["llmProvider"] == "copilot"


def test_repeated_environment_application_restores_configured_credentials(tmp_path, monkeypatch):
    for name, value in {
        "LIVEKIT_URL": "wss://example.test",
        "LIVEKIT_API_KEY": "key",
        "LIVEKIT_API_SECRET": "secret",
    }.items():
        monkeypatch.setenv(name, value)
    library = StudioLibrary(tmp_path)
    library.apply_environment()
    assert os.environ["LIVEKIT_URL"] == "ws://127.0.0.1:7880"
    library.apply_environment()
    library = StudioLibrary(tmp_path)
    library.update_settings({"livekitMode": "configured"})
    library.apply_environment()
    assert os.environ["LIVEKIT_URL"] == "wss://example.test"
    assert os.environ["LIVEKIT_API_SECRET"] == "secret"


@pytest.mark.parametrize(
    "url",
    [
        "https://remote.example/v1",
        "http://user:pass@localhost/v1",
        "http://localhost/v1?key=secret",
        "file:///tmp/test",
        "http://localhost/v1#secret",
    ],
)
def test_ollama_rejects_remote_or_sensitive_urls(tmp_path, url):
    with pytest.raises(LibraryError):
        StudioLibrary(tmp_path).update_settings({"llmBaseUrl": url})


def test_custom_endpoint_accepts_explicit_model_and_never_secret(tmp_path):
    library = StudioLibrary(tmp_path)
    settings = library.update_settings(
        {
            "llmProvider": "openai-compatible",
            "llmBaseUrl": "https://models.example/v1",
            "llmModel": "my-model:latest",
            "reasoningEffort": "none",
        }
    )
    assert settings["llmModel"] == "my-model:latest"
    with pytest.raises(LibraryError):
        library.update_settings({"llmModel": "x" * 201})
    with pytest.raises(LibraryError):
        library.update_settings({"llmApiKey": "secret"})


async def test_endpoint_probe_requires_model_and_rejects_redirect(server):
    from examples.component_endpoints import check_llm_endpoint

    async def handler(request):
        return web.json_response({"data": [{"id": "qwen3:1.7b"}]})

    url = await server(handler)
    await check_llm_endpoint("ollama", url + "/v1", "qwen3:1.7b")
    with pytest.raises(RuntimeError, match="model"):
        await check_llm_endpoint("ollama", url + "/v1", "missing")

    async def redirect(request):
        raise web.HTTPFound(url + "/v1/models")

    other = await server(redirect)
    with pytest.raises(RuntimeError):
        await check_llm_endpoint("ollama", other + "/v1", "qwen3:1.7b")


@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
async def test_endpoint_adapter_uses_only_server_side_custom_key(provider, monkeypatch):
    from unittest.mock import Mock

    from examples import minimal_agent

    monkeypatch.setenv("VOICEBOX_STT_PROVIDER", "openai")
    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", provider)
    monkeypatch.setenv("VOICEBOX_LLM_MODEL", "qwen3:1.7b")
    monkeypatch.setenv("VOICEBOX_LLM_BASE_URL", "http://127.0.0.1:12345/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-cloud-key")
    monkeypatch.setenv("VOICEBOX_CUSTOM_LLM_API_KEY", "custom-private-key")
    constructor = Mock()
    from examples import endpoint_llm

    monkeypatch.setattr(endpoint_llm, "EndpointLLM", constructor)
    monkeypatch.setattr(minimal_agent.openai, "STT", Mock())
    await minimal_agent.configured_ai()
    options = constructor.call_args.kwargs
    assert options["api_key"] == ("ollama" if provider == "ollama" else "custom-private-key")
    assert options["base_url"] == "http://127.0.0.1:12345/v1"


async def test_session_preflight_stops_before_transport_when_model_missing(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from examples import studio as module
    from examples.backend_lease import BackendLease

    monkeypatch.setenv("VOICEBOX_LLM_PROVIDER", "ollama")
    probe = AsyncMock(side_effect=RuntimeError("Selected model unavailable."))
    monkeypatch.setattr(module, "check_llm_endpoint", probe)
    broker = module.Studio(BackendLease(tmp_path, "http://127.0.0.1:17493"))
    broker.lease.acquire()
    status = AsyncMock()
    monkeypatch.setattr(broker, "status", status)
    with pytest.raises(module.StudioError, match="model unavailable"):
        await broker.create()
    status.assert_not_awaited()
    assert broker.current is None
    assert broker.phase == "idle"
    broker.lease.release()


async def test_chat_transport_ignores_proxy_closes_and_disables_thinking(server, monkeypatch):
    from livekit.agents import llm

    from examples.endpoint_llm import EndpointLLM

    received = []

    async def handler(request):
        received.append(await request.json())
        return web.Response(
            text=(
                'data: {"id":"test","choices":[{"index":0,"delta":{"content":"Hello"},'
                '"finish_reason":null}]}\n\ndata: [DONE]\n\n'
            ),
            content_type="text/event-stream",
        )

    url = await server(handler)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    model = EndpointLLM(provider="ollama", model="qwen3:1.7b", base_url=url, api_key="ollama")
    context = llm.ChatContext()
    context.add_message(role="user", content="Hello")
    try:
        async with model.chat(chat_ctx=context) as stream:
            assert stream._conn_options.timeout == 60
            assert stream._conn_options.max_retry == 0
            chunks = [chunk async for chunk in stream]
        assert any(chunk.delta and chunk.delta.content == "Hello" for chunk in chunks)
        assert received[0]["think"] is False
        assert received[0]["reasoning_effort"] == "none"
        assert received[0]["max_completion_tokens"] == 256
    finally:
        await model.aclose()
    assert model.endpoint_client.is_closed()


async def test_chat_redirect_never_forwards_prompt_or_key(server):
    from livekit.agents import APIStatusError, llm

    from examples.endpoint_llm import EndpointLLM

    forwarded = []

    async def target(request):
        forwarded.append(request.path)
        return web.json_response({})

    destination = await server(target)

    async def redirect(request):
        raise web.HTTPTemporaryRedirect(destination + "/chat/completions")

    source = await server(redirect)
    model = EndpointLLM(
        provider="openai-compatible", model="test", base_url=source, api_key="secret"
    )
    context = llm.ChatContext()
    context.add_message(role="user", content="Private prompt")
    try:
        with pytest.raises(APIStatusError):
            async with model.chat(chat_ctx=context) as stream:
                async for _ in stream:
                    pass
    finally:
        await model.aclose()
    assert not forwarded


@pytest.mark.parametrize("provider", ["copilot", "codex", "azure", "openai"])
def test_cloud_presets_save_reload_and_doctor_preserve_effort(tmp_path, provider):
    from examples.studio_library import PRESETS
    from tools.studio_doctor import _saved_environment

    library = StudioLibrary(tmp_path / "library")
    model, effort = PRESETS[provider]
    library.update_settings(
        {
            "llmProvider": provider,
            "llmModel": model,
            "reasoningEffort": effort,
            "codexRestrictedApproved": provider == "codex",
            "livekitMode": "configured",
        }
    )
    settings = library.settings()
    assert settings["reasoningEffort"] == effort
    # Exercise legacy files without the new endpoint and transport fields too.
    del settings["llmBaseUrl"]
    del settings["livekitMode"]
    library.settings_path.write_text(json.dumps(settings))
    assert library.settings()["reasoningEffort"] == effort
    env = {"VOICEBOX_LIBRARY_DIR": str(library.root)}
    assert _saved_environment(tmp_path, env)
    assert env["VOICEBOX_LLM_PROVIDER"] == provider
    assert env["VOICEBOX_LLM_MODEL"] == model
    assert env["VOICEBOX_REASONING_EFFORT"] == effort


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (401, "rejected authentication"),
        (403, "rejected authentication"),
        (404, "/v1"),
        (503, "service is unavailable"),
    ],
)
async def test_endpoint_errors_have_specific_safe_recovery(server, code, expected):
    from examples.component_endpoints import check_llm_endpoint

    async def handler(request):
        return web.Response(status=code, text="private-provider-body-and-secret")

    url = await server(handler)
    with pytest.raises(RuntimeError, match=expected) as error:
        await check_llm_endpoint("openai-compatible", url, "test")
    assert "private-provider" not in str(error.value)


async def test_missing_model_can_be_prepared_and_retried_without_download(server):
    from examples.component_endpoints import check_llm_endpoint

    available = False
    requests = []

    async def handler(request):
        requests.append((request.method, request.path))
        return web.json_response({"data": [{"id": "test"}] if available else []})

    url = await server(handler)
    with pytest.raises(RuntimeError, match="ollama list"):
        await check_llm_endpoint("ollama", url, "test")
    available = True
    await check_llm_endpoint("ollama", url, "test")
    assert requests == [("GET", "/models"), ("GET", "/models")]


async def test_broken_reply_stream_fails_without_retry_then_new_turn_recovers(server):
    from livekit.agents import APIConnectionError, llm

    from examples.endpoint_llm import EndpointLLM

    requests = []

    async def handler(request):
        requests.append(await request.json())
        if len(requests) == 1:
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(
                b'data: {"id":"one","choices":[{"index":0,'
                b'"delta":{"content":"Partial"},"finish_reason":null}]}\n\n'
            )
            request.transport.abort()
            return response
        return web.Response(
            text='data: {"id":"two","choices":[{"index":0,'
            '"delta":{"content":"Recovered"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n",
            content_type="text/event-stream",
        )

    url = await server(handler)
    model = EndpointLLM(provider="ollama", model="test", base_url=url, api_key="ollama")
    context = llm.ChatContext()
    context.add_message(role="user", content="Synthetic recovery check")
    try:
        with pytest.raises(APIConnectionError):
            async with model.chat(chat_ctx=context) as stream:
                async for _ in stream:
                    pass
        assert len(requests) == 1
        async with model.chat(chat_ctx=context) as stream:
            text = "".join([chunk.delta.content or "" async for chunk in stream if chunk.delta])
        assert text == "Recovered"
        assert len(requests) == 2
    finally:
        await model.aclose()
    assert model.endpoint_client.is_closed()


async def test_unreachable_local_model_service_can_recover(server):
    from examples.component_endpoints import check_llm_endpoint

    connected = False

    async def handler(request):
        if not connected:
            request.transport.abort()
            return web.Response()
        return web.json_response({"data": [{"id": "test"}]})

    url = await server(handler)
    with pytest.raises(RuntimeError, match="ollama serve"):
        await check_llm_endpoint("ollama", url, "test")
    connected = True
    await check_llm_endpoint("ollama", url, "test")


async def test_model_listing_is_bounded_read_only_and_deduplicated(server):
    from examples.component_endpoints import list_llm_models

    requests = []

    async def handler(request):
        requests.append((request.method, request.path, request.headers.get("Authorization")))
        return web.json_response(
            {
                "data": [
                    {"id": "z:latest"},
                    {"id": "a:small"},
                    {"id": "z:latest"},
                    {"id": "bad\nname"},
                    {"id": 12},
                ]
            }
        )

    url = await server(handler)
    assert await list_llm_models("ollama", url) == ["a:small", "z:latest"]
    assert requests == [("GET", "/models", None)]


async def test_empty_model_listing_is_valid(server):
    from examples.component_endpoints import list_llm_models

    async def handler(request):
        return web.json_response({"data": []})

    assert await list_llm_models("ollama", await server(handler)) == []


async def test_model_listing_rejects_remote_ollama_before_request():
    from examples.component_endpoints import list_llm_models

    with pytest.raises(ValueError, match="loopback"):
        await list_llm_models("ollama", "https://remote.invalid/v1")
