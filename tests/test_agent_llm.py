"""Deterministic conversation-runtime tests; never authenticate or run inference."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from livekit.agents import APIConnectOptions, APIError, llm

from examples.agent_llm import AgentLLM


def event(kind, **data):
    return SimpleNamespace(type=SimpleNamespace(value=kind), data=SimpleNamespace(**data))


class Session:
    session_id = "owned-session"

    def __init__(self):
        self.handlers = []
        self.old_handlers = []
        self.prompts = []
        self.abort = AsyncMock(side_effect=self.abort_turn)
        self.disconnect = AsyncMock()
        self.started = asyncio.Event()
        self.send_gate = None
        self.auto_reply = True
        self.abort_idle = True
        self.rpc = SimpleNamespace(
            options=SimpleNamespace(update=AsyncMock()),
            tools=SimpleNamespace(
                initialize_and_validate=AsyncMock(),
                get_current_metadata=AsyncMock(return_value=SimpleNamespace(tools=[])),
            ),
        )

    def on(self, handler):
        self.handlers.append(handler)
        self.old_handlers.append(handler)
        return lambda: self.handlers.remove(handler)

    def emit(self, kind, **data):
        for handler in list(self.handlers):
            handler(event(kind, **data))

    async def send(self, prompt, **kwargs):
        self.prompts.append(json.loads(prompt))
        self.started.set()
        if self.send_gate:
            await self.send_gate.wait()
        if self.auto_reply:
            message_id = f"message-{len(self.prompts)}"
            self.emit("assistant.message_delta", message_id=message_id, delta_content="")
            self.emit("assistant.message_delta", message_id=message_id, delta_content="Hello")
            self.emit("assistant.message", message_id=message_id, content="Hello")
            self.emit("session.idle")
        return f"user-{len(self.prompts)}"

    async def abort_turn(self):
        if self.abort_idle:
            self.emit("session.idle", aborted=True)


@pytest.fixture
def runtime(monkeypatch):
    session = Session()
    model = SimpleNamespace(
        id="gpt-5.6-luna",
        supported_reasoning_efforts=["low", "medium"],
        policy=SimpleNamespace(state="enabled"),
    )
    client = SimpleNamespace(
        start=AsyncMock(),
        list_models=AsyncMock(return_value=[model]),
        create_session=AsyncMock(return_value=session),
        delete_session=AsyncMock(),
        stop=AsyncMock(),
        force_stop=AsyncMock(),
    )
    options = {}

    def factory(**kwargs):
        options.update(kwargs)
        return client

    import examples.agent_llm as module

    monkeypatch.setattr(module, "_copilot_client", factory)
    monkeypatch.setattr(module, "_conversation_options", lambda: {"installed_plugins": []})
    monkeypatch.setattr(module.shutil, "which", lambda name: "/installed/copilot")
    return SimpleNamespace(session=session, client=client, options=options, model=model)


def context(*messages):
    ctx = llm.ChatContext()
    for role, text in messages:
        ctx.add_message(role=role, content=text)
    return ctx


async def text(adapter, ctx):
    stream = adapter.chat(chat_ctx=ctx, conn_options=APIConnectOptions(timeout=1, max_retry=0))
    async with stream:
        return "".join([chunk.delta.content async for chunk in stream if chunk.delta])


async def test_persistent_model_and_no_tools_configuration(runtime):
    async with AgentLLM(provider="copilot") as adapter:
        metadata = await adapter.validate()
        assert metadata["model"] == adapter.model == "gpt-5.6-luna"
        assert metadata["reasoning_effort"] == "low"
        assert adapter.provider == "copilot"
        assert await text(adapter, context(("user", "Hi"))) == "Hello"
        assert (
            await text(adapter, context(("user", "Hi"), ("assistant", "Hello"), ("user", "Again")))
            == "Hello"
        )
        assert runtime.client.create_session.await_count == 1
        assert runtime.session.prompts[1]["messages"] == [{"role": "user", "content": "Again"}]
        config = runtime.client.create_session.call_args.kwargs
        assert config["available_tools"] == []
        assert set(config["excluded_tools"]) == {"builtin:*", "mcp:*", "custom:*"}
        assert config["enable_config_discovery"] is False
        assert config["plugin_directories"] == []
        assert config["enable_skills"] is False
        assert config["enable_file_hooks"] is False
        assert config["mcp_servers"] == {}
        assert config["request_extensions"] is False
        assert runtime.options["mode"] == "copilot-cli"
        assert "env" not in runtime.options
        assert runtime.options["use_logged_in_user"] is True
        runtime.session.rpc.options.update.assert_awaited_once_with({"installed_plugins": []})
        assert config["custom_agents_local_only"] is True
        assert config["included_builtin_skills"] == []
        assert config["enable_experimental_mode"] is False
        workspace = Path(config["working_directory"])
        assert workspace != Path.cwd()
        assert workspace.is_dir()
    assert not workspace.exists()
    runtime.client.stop.assert_awaited_once()


@pytest.mark.parametrize("bad", ["missing", "effort", "disabled", "unknown-policy"])
async def test_metadata_rejected_without_inference(runtime, bad):
    if bad == "missing":
        runtime.client.list_models.return_value = []
    elif bad == "effort":
        runtime.model.supported_reasoning_efforts = ["high"]
    else:
        runtime.model.policy.state = "disabled" if bad == "disabled" else "surprise"
    async with AgentLLM(provider="copilot") as adapter:
        with pytest.raises(APIError) as exc:
            await adapter.validate()
        assert not exc.value.retryable
    runtime.client.create_session.assert_not_awaited()
    assert runtime.session.prompts == []


async def test_codex_is_fail_closed_without_spawning(monkeypatch):
    import examples.agent_llm as module

    spawn = AsyncMock()
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    async with AgentLLM(provider="codex") as adapter:
        with pytest.raises(APIError, match="no-tools"):
            await adapter.validate()
    spawn.assert_not_awaited()


async def test_cancel_before_acquiring_turn_does_not_abort_other_turn(runtime):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        first = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        second = adapter.chat(chat_ctx=context(("user", "two")))
        await asyncio.sleep(0)
        await second.aclose()
        runtime.session.abort.assert_not_awaited()
        await first.aclose()
        runtime.session.abort.assert_awaited_once()
        assert len(runtime.session.prompts) == 1


async def test_abort_ack_and_stale_callback_suppression(runtime):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        runtime.session.emit("assistant.message_delta", message_id="old", delta_content="Partial")
        assert (await anext(stream)).delta.content == "Partial"
        old_handler = runtime.session.old_handlers[-1]
        await stream.aclose()
        runtime.session.abort.assert_awaited_once()
        runtime.session.auto_reply = True
        old_handler(event("assistant.message_delta", message_id="old", delta_content="LEAK"))
        result = await text(
            adapter, context(("user", "one"), ("assistant", "Part"), ("user", "two"))
        )
        assert result == "Hello"
        assert runtime.session.prompts[1]["mode"] == "snapshot"
        assert runtime.session.prompts[1]["messages"][1]["content"] == "Part"


async def test_cancel_during_send_waits_ack_then_aborts(runtime):
    runtime.session.send_gate = asyncio.Event()
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        closing = asyncio.create_task(stream.aclose())
        await asyncio.sleep(0)
        assert not closing.done()
        runtime.session.send_gate.set()
        await closing
        runtime.session.abort.assert_awaited_once()


async def test_unknown_abort_closes_and_blocks_reuse(runtime):
    runtime.session.auto_reply = False
    runtime.session.abort_idle = False
    async with AgentLLM(provider="copilot", abort_timeout=0.02) as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        await stream.aclose()
        with pytest.raises(APIError, match="uncertain"):
            await text(adapter, context(("user", "two")))
        runtime.client.stop.assert_awaited_once()
        assert len(runtime.session.prompts) == 1


@pytest.mark.parametrize("kind", ["tool.execution_start", "session.error", "assistant-tool"])
async def test_error_never_speaks_tools_or_raw_diagnostics(runtime, kind):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        if kind == "assistant-tool":
            runtime.session.emit(
                "assistant.message", message_id="bad", content="SECRET", tool_requests=[{}]
            )
        else:
            runtime.session.emit(kind, message="SECRET token=abc", output="SECRET")
        async with stream:
            with pytest.raises(APIError) as exc:
                _ = [chunk async for chunk in stream]
        assert "SECRET" not in str(exc.value)
        assert not exc.value.retryable


async def test_sdk_error_is_sanitized(runtime):
    runtime.client.start.side_effect = RuntimeError("secret-token raw stderr")
    async with AgentLLM(provider="copilot") as adapter:
        with pytest.raises(APIError) as exc:
            await adapter.validate()
    assert "secret-token" not in str(exc.value)
    assert exc.value.__cause__ is None
    runtime.client.stop.assert_awaited_once()


async def test_bounded_callback_queue(runtime):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        for _ in range(300):
            runtime.session.emit("assistant.message_delta", message_id="id", delta_content="x")
        async with stream:
            with pytest.raises(APIError, match="buffer"):
                _ = [chunk async for chunk in stream]


async def test_close_cancels_only_owned_turn_and_is_idempotent(runtime):
    runtime.session.auto_reply = False
    adapter = AgentLLM(provider="copilot")
    stream = adapter.chat(chat_ctx=context(("user", "one")))
    await runtime.session.started.wait()
    await adapter.aclose()
    await adapter.aclose()
    await stream.aclose()
    runtime.client.stop.assert_awaited_once()
    runtime.session.abort.assert_awaited_once()


@pytest.mark.parametrize("tools", [None, ["bash"]])
async def test_runtime_tools_must_be_verified_empty(runtime, tools):
    runtime.session.rpc.tools.get_current_metadata.return_value.tools = tools
    async with AgentLLM(provider="copilot") as adapter:
        with pytest.raises(APIError, match="no-tools"):
            await adapter.validate()
    assert runtime.session.prompts == []


async def test_late_known_message_forwarded_to_new_subscription_is_ignored(runtime):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        runtime.session.emit("assistant.message_delta", message_id="old", delta_content="one")
        await anext(stream)
        await stream.aclose()
        runtime.session.started.clear()
        second = adapter.chat(chat_ctx=context(("user", "two")))
        await runtime.session.started.wait()
        runtime.session.emit("assistant.message_delta", message_id="old", delta_content="LEAK")
        runtime.session.emit("assistant.message_delta", message_id="new", delta_content="new")
        runtime.session.emit("session.idle")
        async with second:
            assert "".join([chunk.delta.content async for chunk in second]) == "new"


async def test_final_only_reply_and_verified_usage(runtime):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        runtime.session.emit(
            "assistant.usage", model="gpt-5.6-luna", input_tokens=10, output_tokens=2
        )
        runtime.session.emit("assistant.message", message_id="final", content="Hi")
        runtime.session.emit("assistant.message", message_id="final", content="Hi")
        runtime.session.emit("session.idle")
        async with stream:
            chunks = [chunk async for chunk in stream]
        assert chunks[0].usage.total_tokens == 12
        assert [chunk.delta.content for chunk in chunks if chunk.delta] == ["Hi"]


async def test_runtime_cannot_silently_switch_model(runtime):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        runtime.session.emit("assistant.turn_start", model="auto-fallback")
        async with stream:
            with pytest.raises(APIError, match="selected model"):
                _ = [chunk async for chunk in stream]
        with pytest.raises(APIError, match="uncertain"):
            adapter.chat(chat_ctx=context(("user", "two")))


async def test_empty_deltas_do_not_yield_empty_chunks(runtime):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        runtime.session.emit("assistant.message_delta", message_id="empty", delta_content="")
        runtime.session.emit("assistant.message", message_id="empty", content="")
        runtime.session.emit("session.idle")
        async with stream:
            with pytest.raises(APIError, match="no assistant text"):
                _ = [chunk async for chunk in stream]


async def test_missing_send_ack_closes_instead_of_reusing(runtime):
    runtime.session.send_gate = asyncio.Event()
    async with AgentLLM(provider="copilot", abort_timeout=0.02) as adapter:
        stream = adapter.chat(chat_ctx=context(("user", "one")))
        await runtime.session.started.wait()
        await stream.aclose()
        runtime.client.stop.assert_awaited_once()
        runtime.session.abort.assert_not_awaited()
        with pytest.raises(APIError, match="uncertain"):
            await adapter.validate()


async def test_timeout_aborts_without_automatic_retry(runtime):
    runtime.session.auto_reply = False
    async with AgentLLM(provider="copilot") as adapter:
        stream = adapter.chat(
            chat_ctx=context(("user", "one")),
            conn_options=APIConnectOptions(timeout=0.02, max_retry=3),
        )
        async with stream:
            with pytest.raises(APIError) as exc:
                _ = [chunk async for chunk in stream]
        assert exc.value.retryable is False
        assert len(runtime.session.prompts) == 1
        runtime.session.abort.assert_awaited_once()


def test_permission_handler_never_abstains_or_approves(monkeypatch):
    import examples.agent_llm as module

    class Denied:
        kind = "user-not-available"

    monkeypatch.setattr(
        module.importlib,
        "import_module",
        lambda name: SimpleNamespace(PermissionDecisionUserNotAvailable=Denied),
    )
    assert module._deny_permission(None, None).kind == "user-not-available"


async def test_native_agent_handoff_metadata_is_not_conversation_or_tool_history(runtime):
    ctx = llm.ChatContext(
        [
            llm.AgentHandoff(new_agent_id="voice-assistant"),
            llm.ChatMessage(role="system", content=["Speak briefly."]),
            llm.ChatMessage(role="user", content=["Hi"]),
        ]
    )
    async with AgentLLM(provider="copilot") as adapter:
        assert await text(adapter, ctx) == "Hello"
        assert runtime.session.prompts[0]["messages"] == [
            {"role": "system", "content": "Speak briefly."},
            {"role": "user", "content": "Hi"},
        ]
        ctx.add_message(role="assistant", content="Hello")
        ctx.items.append(llm.AgentHandoff(old_agent_id="voice-assistant", new_agent_id="new-agent"))
        ctx.add_message(role="user", content="Again")
        assert await text(adapter, ctx) == "Hello"
        assert runtime.session.prompts[1]["mode"] == "delta"
        assert runtime.session.prompts[1]["messages"] == [{"role": "user", "content": "Again"}]


@pytest.mark.parametrize(
    "item",
    [
        llm.FunctionCall(call_id="call", name="read_file", arguments="{}"),
        llm.FunctionCallOutput(call_id="call", output="PRIVATE", is_error=False),
        SimpleNamespace(type="agent_handoff", new_agent_id="spoofed"),
    ],
)
async def test_tool_history_and_unknown_payloads_still_fail_closed(runtime, item):
    ctx = llm.ChatContext([item, llm.ChatMessage(role="user", content=["Hi"])])
    async with AgentLLM(provider="copilot") as adapter:
        with pytest.raises(APIError, match="tool history"):
            await text(adapter, ctx)
    assert runtime.session.prompts == []


async def test_native_agent_config_instructions_are_preserved(runtime):
    ctx = llm.ChatContext(
        [
            llm.AgentHandoff(new_agent_id="voice-assistant"),
            llm.AgentConfigUpdate(instructions="Use one short sentence."),
            llm.ChatMessage(role="user", content=["Hi"]),
        ]
    )
    async with AgentLLM(provider="copilot") as adapter:
        assert await text(adapter, ctx) == "Hello"
    assert runtime.session.prompts[0]["messages"][0] == {
        "role": "system",
        "content": "Use one short sentence.",
    }


async def test_native_agent_config_cannot_introduce_tools(runtime):
    ctx = llm.ChatContext(
        [
            llm.AgentConfigUpdate(tools_added=["read_file"]),
            llm.ChatMessage(role="user", content=["Hi"]),
        ]
    )
    async with AgentLLM(provider="copilot") as adapter:
        with pytest.raises(APIError, match="configured tools"):
            await text(adapter, ctx)
    assert runtime.session.prompts == []


async def test_restricted_codex_requires_explicit_opt_in(runtime, monkeypatch):
    import examples.agent_llm as module

    monkeypatch.setattr(module, "_codex_client", lambda **kwargs: runtime.client)
    runtime.client.restricted_capabilities = {
        "mode": "restricted-agent",
        "tools_enabled": True,
        "external_tools": 0,
        "command_network_enabled": False,
        "inherits_global_instructions": True,
    }
    async with AgentLLM(provider="codex", allow_restricted_agent=True) as adapter:
        metadata = await adapter.validate()
        assert metadata["mode"] == "restricted-agent"
        assert metadata["tools_enabled"] is True
        assert metadata["external_tools"] == 0
        assert metadata["inherits_global_instructions"] is True
        assert await text(adapter, context(("user", "Hi"))) == "Hello"
        assert (
            await text(adapter, context(("user", "Hi"), ("assistant", "Hello"), ("user", "Again")))
            == "Hello"
        )
    assert runtime.client.create_session.await_count == 1
    runtime.client.stop.assert_awaited_once()


def test_codex_profile_and_per_server_disabling_are_explicit():
    from examples.agent_llm import _codex_configuration

    work = Path.cwd() / ".owned-test-work"
    config = _codex_configuration(
        work=work, profile="voicebox_test", servers=["alpha", "with.dot"], plugins=["a@market"]
    )
    assert config["mcp_servers"] == {
        "alpha": {"enabled": False},
        "with.dot": {"enabled": False},
    }
    assert config["plugins"] == {"a@market": {"enabled": False}}
    assert config["permissions.voicebox_test.filesystem"][":root"] == "deny"
    assert config["permissions.voicebox_test.filesystem"][str(work)] == "write"
    assert config["permissions.voicebox_test.network.enabled"] is False
    assert config["shell_environment_policy.inherit"] == "none"
    assert config["allow_login_shell"] is False
    assert config["web_search"] == "disabled"
    assert config["features.hooks"] is False
    assert config["features.view_image"] is False


@pytest.mark.parametrize("key", ["tools", "resources", "resourceTemplates"])
def test_codex_external_capability_attestation_rejects_any_remaining_access(key):
    from examples.agent_llm import _codex_external_names

    response = {"data": [{"name": "server", key: ["still available"]}]}
    with pytest.raises(APIError, match="external"):
        _codex_external_names(response, require_disabled=True)


async def test_codex_session_maps_only_text_and_interrupt_completion():
    from examples.agent_llm import _CodexSession

    client = SimpleNamespace(
        request=AsyncMock(return_value={"turn": {"id": "turn-1"}}),
        session=None,
    )
    session = _CodexSession(client, "thread-1")
    received = []
    unsubscribe = session.on(received.append)
    await session.send("synthetic")
    session.notify(
        "item/agentMessage/delta",
        {"threadId": "thread-1", "turnId": "turn-1", "itemId": "message", "delta": "Hello"},
    )
    session.notify(
        "item/completed",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"type": "commandExecution", "aggregatedOutput": "DO NOT SPEAK"},
        },
    )
    assert [e.data.delta_content for e in received] == ["Hello"]
    await session.abort()
    assert client.request.call_args.args == (
        "turn/interrupt",
        {"threadId": "thread-1", "turnId": "turn-1"},
    )
    session.notify(
        "turn/completed",
        {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "interrupted"}},
    )
    assert received[-1].type == "session.idle"
    assert received[-1].data.aborted is True
    count = len(received)
    session.notify(
        "item/agentMessage/delta",
        {"threadId": "thread-1", "turnId": "turn-1", "itemId": "late", "delta": "STALE"},
    )
    assert len(received) == count
    unsubscribe()


async def test_codex_session_rejects_non_sandboxed_tool_activity():
    from examples.agent_llm import _CodexSession

    client = SimpleNamespace(
        request=AsyncMock(return_value={"turn": {"id": "turn-1"}}),
        session=None,
    )
    session = _CodexSession(client, "thread-1")
    received = []
    session.on(received.append)
    await session.send("synthetic")
    session.notify(
        "item/started",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"type": "mcpToolCall", "result": "SECRET"},
        },
    )
    assert received[-1].type == "tool.execution_start"
    assert "SECRET" not in str(received[-1].data)


@pytest.mark.parametrize(
    "method",
    [
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "item/tool/call",
    ],
)
async def test_codex_transport_denies_all_server_requests(method):
    from examples.agent_llm import _CodexClient

    reader = asyncio.StreamReader()
    reader.feed_data(
        (
            json.dumps({"id": "server-id", "method": method, "params": {"token": "SECRET"}}) + "\n"
        ).encode()
    )
    reader.feed_eof()
    client = _CodexClient(cli_path="unused", work=Path.cwd(), state=Path.cwd())
    client.process = SimpleNamespace(stdout=reader)
    client._write = AsyncMock()
    await client._read()
    reply = client._write.call_args.args[0]
    assert reply["id"] == "server-id"
    if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
        assert reply["result"] == {"decision": "decline"}
    else:
        assert "error" in reply
        assert "result" not in reply
    assert "SECRET" not in json.dumps(reply)


async def test_codex_stop_signals_only_owned_process_group(monkeypatch):
    import examples.agent_llm as module

    client = module._CodexClient(cli_path="unused", work=Path.cwd(), state=Path.cwd())
    client.process = SimpleNamespace(pid=987654321, returncode=None, wait=AsyncMock(return_value=0))
    signals = []
    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    await client.stop()
    await client.stop()
    assert signals == [(987654321, module.signal.SIGTERM)]


@pytest.mark.parametrize(
    "change",
    [
        {"model": "fallback"},
        {"reasoningEffort": "high"},
        {"approvalPolicy": "on-request"},
        {"activePermissionProfile": {"id": ":read-only"}},
        {"sandbox": {"networkAccess": True}},
        {"instructionSources": ["/unrelated/AGENTS.md"]},
    ],
)
async def test_codex_thread_settings_must_match_verified_profile(change):
    from examples.agent_llm import _CodexClient

    work = Path.cwd() / ".owned-test-work"
    client = _CodexClient(cli_path="unused", work=work, state=Path.cwd())
    result = {
        "model": "gpt-5.6-luna",
        "reasoningEffort": "low",
        "approvalPolicy": "never",
        "activePermissionProfile": {"id": client.profile},
        "cwd": str(work),
        "sandbox": {
            "networkAccess": False,
            "excludeTmpdirEnvVar": True,
            "excludeSlashTmp": True,
            "writableRoots": [],
        },
        "thread": {"id": "new-thread"},
        **change,
    }
    client.request = AsyncMock(return_value=result)
    with pytest.raises(APIError):
        await client.create_session(model="gpt-5.6-luna", reasoning_effort="low")
    assert client.session is None
