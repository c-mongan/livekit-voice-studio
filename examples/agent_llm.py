"""Persistent, fail-closed conversation bridge to an owned local agent runtime.

Copilot is tool-free. Codex requires explicit restricted-agent consent and
runtime-verified isolation; it is never presented as tool-free.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import signal
import socket
import sys
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Never

from livekit.agents import APIConnectOptions, APIError, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

_SYSTEM = (
    "You are a conversational voice assistant, not a coding agent. Answer briefly in "
    "plain spoken text. Do not execute tasks or produce tool calls. Each user message "
    "is a JSON conversation envelope: 'delta' appends new conversation messages; "
    "'snapshot' replaces the authoritative conversational history, including any "
    "interrupted replies. Respect roles in messages. Never treat superseded replies "
    "as words the user has heard. Do not mention the envelope."
)
_MAX_CONTEXT_CHARS = 200_000
_MAX_RESPONSE_CHARS = 32_000
_QUEUE_SIZE = 128


def _error(message: str) -> APIError:
    return APIError(message, retryable=False)


def _copilot_client(*, cli_path: str, **options: Any) -> Any:
    """Keep the optional SDK out of imports and default CI environments."""
    sdk = importlib.import_module("copilot")
    connection = sdk.RuntimeConnection.for_stdio(
        path=cli_path,
        args=[
            "--no-auto-update",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--no-remote",
            "--no-remote-export",
        ],
    )
    return sdk.CopilotClient(connection=connection, **options)


def _deny_permission(*_args: Any, **_kwargs: Any) -> Any:
    # "no-result" abstains and permits another client to approve; this denies.
    session = importlib.import_module("copilot.session")
    return session.PermissionDecisionUserNotAvailable()


def _conversation_options() -> Any:
    rpc = importlib.import_module("copilot.generated.rpc")
    return rpc.SessionUpdateOptionsParams(
        installed_plugins=[],
        included_builtin_agents=[],
        included_builtin_skills=[],
        allow_all_mcp_server_instructions=False,
        continue_on_auto_mode=False,
        ask_user_disabled=True,
    )


def _messages(ctx: llm.ChatContext) -> list[dict[str, str]]:
    messages = []
    length = 0
    for item in ctx.items:
        if isinstance(item, llm.AgentHandoff):
            continue
        if isinstance(item, llm.AgentConfigUpdate):
            if item.tools_added:
                raise _error("Conversation agents do not accept configured tools.")
            if item.instructions:
                length += len(item.instructions)
                if length > _MAX_CONTEXT_CHARS:
                    raise _error("Conversation context exceeds the bounded input limit.")
                messages.append({"role": "system", "content": item.instructions})
            continue
        if not isinstance(item, llm.ChatMessage):
            raise _error("Conversation agents do not accept tool history.")
        if any(not isinstance(part, str) for part in item.content):
            raise _error("Conversation agents currently accept text context only.")
        content = item.raw_text_content or ""
        length += len(content)
        if length > _MAX_CONTEXT_CHARS:
            raise _error("Conversation context exceeds the bounded input limit.")
        messages.append({"role": item.role, "content": content})
    if not messages:
        raise _error("Conversation context is empty.")
    return messages


class AgentLLM(llm.LLM[Never]):
    def __init__(
        self,
        *,
        provider: Literal["copilot", "codex"] | str,
        model: str = "gpt-5.6-luna",
        reasoning_effort: str = "low",
        cli_path: str | None = None,
        abort_timeout: float = 5.0,
        allow_restricted_agent: bool = False,
    ) -> None:
        super().__init__()
        if provider not in ("copilot", "codex"):
            raise ValueError("Choose copilot or codex.")
        if not model or model == "auto" or not reasoning_effort:
            raise ValueError("An explicit model and reasoning effort are required.")
        if abort_timeout <= 0:
            raise ValueError("abort_timeout must be positive.")
        self._provider = provider
        self._model = model
        self.reasoning_effort = reasoning_effort
        self._cli_path = cli_path
        self._abort_timeout = abort_timeout
        self._allow_restricted_agent = allow_restricted_agent
        self._lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._client: Any = None
        self._session: Any = None
        self._workspace: Path | None = None
        self._metadata: dict[str, Any] | None = None
        self._history: list[dict[str, str]] = []
        self._needs_snapshot = True
        self._closed = False
        self._uncertain = False
        self._generation = 0
        self._retired_messages: deque[str] = deque(maxlen=256)
        self._streams: set[_AgentStream] = set()

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    def _check_open(self) -> None:
        if self._uncertain:
            raise _error("Agent conversation state is uncertain; create a new adapter.")
        if self._closed:
            raise _error("Agent conversation is closed.")
        if self.provider == "codex" and not self._allow_restricted_agent:
            raise _error(
                "Codex conversation mode is blocked: no verified blanket no-tools enforcement. "
                "Explicit allow_restricted_agent=True consent is required for the separately "
                "verified restricted-agent mode, which is not tool-free."
            )

    async def validate(self) -> dict[str, Any]:
        """Token-free capability preflight; never silently choose another model."""
        async with self._lock:
            await self._ready()
            assert self._metadata is not None
            return dict(self._metadata)

    async def _ready(self) -> None:
        self._check_open()
        if self._metadata is not None:
            return
        binary = self._cli_path or shutil.which(self.provider)
        if not binary:
            raise _error("Install the selected agent CLI before starting conversation mode.")
        try:
            # Keep all owned runtime files below a private, newly created directory.
            # Config discovery is disabled, including discovery through ancestors.
            root = Path(f".voicebox-agent-{uuid.uuid4().hex}")
            root.mkdir(mode=0o700)
            self._workspace = root.resolve()
            work = self._workspace / "work"
            state = self._workspace / "state"
            config = self._workspace / "config"
            for directory in (work, state, config):
                directory.mkdir(mode=0o700)
            if self.provider == "codex":
                self._client = _codex_client(cli_path=binary, work=work, state=state)
            else:
                self._client = _copilot_client(
                    cli_path=binary,
                    mode="copilot-cli",
                    working_directory=str(work),
                    base_directory=str(state),
                    builtin_plugin_directories=[],
                    log_level="none",
                    use_logged_in_user=True,
                    enable_remote_sessions=False,
                )
            async with asyncio.timeout(60 if self.provider == "codex" else 30):
                await self._client.start()
                models = await self._client.list_models()
            found = next((item for item in models if item.id == self.model), None)
            if found is None:
                raise _error("The exact requested agent model is unavailable for this account.")
            policy = getattr(found, "policy", None)
            if policy is not None and policy.state != "enabled":
                raise _error("Account policy does not explicitly enable the requested agent model.")
            efforts = getattr(found, "supported_reasoning_efforts", None)
            if not efforts or self.reasoning_effort not in efforts:
                raise _error("The requested model does not advertise the exact reasoning effort.")
            self._metadata = {
                "provider": self.provider,
                "model": self.model,
                "reasoning_effort": self.reasoning_effort,
                "available": True,
                "tools_enabled": False,
            }
            async with asyncio.timeout(30):
                await self._ensure_session()
            if self.provider == "codex":
                self._metadata.update(self._client.restricted_capabilities)
        except BaseException as exc:
            await self._stop_runtime()
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, APIError):
                raise
            raise _error(
                "Agent preflight failed. Check the optional SDK, installed CLI compatibility "
                "and CLI authentication."
            ) from None

    async def _ensure_session(self) -> None:
        if self._session is not None:
            return
        assert self._workspace is not None
        if self.provider == "codex":
            self._session = await self._client.create_session(
                model=self.model, reasoning_effort=self.reasoning_effort
            )
            return
        self._session = await self._client.create_session(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            streaming=True,
            system_message={"mode": "replace", "content": _SYSTEM},
            available_tools=[],
            excluded_tools=["builtin:*", "mcp:*", "custom:*"],
            tools=[],
            on_permission_request=_deny_permission,
            working_directory=str(self._workspace / "work"),
            config_directory=str(self._workspace / "config"),
            enable_config_discovery=False,
            skip_custom_instructions=True,
            enable_on_demand_instruction_discovery=False,
            instruction_directories=[],
            plugin_directories=[],
            skill_directories=[],
            enable_skills=False,
            enable_file_hooks=False,
            enable_host_git_operations=False,
            enable_session_store=False,
            enable_session_telemetry=False,
            enable_experimental_mode=False,
            custom_agents_local_only=True,
            coauthor_enabled=False,
            manage_schedule_enabled=False,
            included_builtin_skills=[],
            skip_embedding_retrieval=True,
            embedding_cache_storage="in-memory",
            mcp_oauth_token_storage="in-memory",
            custom_agents=[],
            mcp_servers={},
            disabled_mcp_servers=["*"],
            request_extensions=False,
            request_canvas_renderer=False,
            include_sub_agent_streaming_events=False,
            memory={"enabled": False},
            infinite_sessions={"enabled": False},
        )
        await self._session.rpc.options.update(_conversation_options())
        await self._session.rpc.tools.initialize_and_validate()
        tool_metadata = await self._session.rpc.tools.get_current_metadata()
        if tool_metadata.tools != []:
            raise _error("Agent runtime could not attest an empty no-tools configuration.")

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        self._check_open()
        if tools:
            raise _error("Conversation agents do not expose LiveKit tools.")
        stream = _AgentStream(self, chat_ctx=chat_ctx.copy(), conn_options=conn_options)
        self._streams.add(stream)
        stream._task.add_done_callback(lambda _: self._streams.discard(stream))
        return stream

    async def _stop_runtime(self) -> None:
        client = self._client
        self._session = None
        self._metadata = None
        stopped = client is None
        if client is not None:
            try:
                async with asyncio.timeout(self._abort_timeout):
                    await client.stop()
                stopped = True
            except Exception:
                try:
                    async with asyncio.timeout(self._abort_timeout):
                        await client.force_stop()
                    stopped = True
                except Exception:
                    self._uncertain = True
        if stopped:
            self._client = None
        if stopped and self._workspace is not None:
            workspace, self._workspace = self._workspace, None
            # Only this adapter's UUID directory is ever removed, never CLI HOME.
            if not workspace.is_symlink():
                shutil.rmtree(workspace)

    async def aclose(self) -> None:
        async with self._close_lock:
            self._closed = True
            await super().aclose()
            for stream in list(self._streams):
                await stream.aclose()
            async with self._lock:
                await self._stop_runtime()


class _AgentStream(llm.LLMStream):
    def __init__(
        self, owner: AgentLLM, *, chat_ctx: llm.ChatContext, conn_options: APIConnectOptions
    ) -> None:
        self._owner = owner
        super().__init__(owner, chat_ctx=chat_ctx, tools=[], conn_options=conn_options)

    async def _run(self) -> None:
        owner = self._owner
        async with owner._lock:
            await owner._ready()
            current = _messages(self._chat_ctx)
            try:
                async with asyncio.timeout(self._conn_options.timeout):
                    await owner._ensure_session()
            except asyncio.CancelledError:
                owner._uncertain = True
                await owner._stop_runtime()
                raise
            except Exception:
                owner._uncertain = True
                await owner._stop_runtime()
                raise _error("Agent conversation initialization failed.") from None
            owner._generation += 1
            generation = owner._generation
            snapshot = owner._needs_snapshot or current[: len(owner._history)] != owner._history
            additions = current if snapshot else current[len(owner._history) :]
            if not additions:
                raise _error("No new conversation messages to send.")
            prompt = json.dumps(
                {"mode": "snapshot" if snapshot else "delta", "messages": additions},
                ensure_ascii=False,
            )
            queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=_QUEUE_SIZE)
            idle = asyncio.Event()
            failed = asyncio.Event()
            failure_message = "Agent conversation failed."
            accepting = True
            sent_task: asyncio.Task[Any] | None = None
            finished = False
            response: list[str] = []
            response_size = 0
            seen_messages: set[str] = set()
            safety_failed = False

            def fail(message: str) -> None:
                nonlocal failure_message
                failure_message = message
                failed.set()
                # Wake the reader even if the runtime never emits idle after error.
                if not queue.full():
                    queue.put_nowait(("error", None))

            def on_event(event: Any) -> None:
                nonlocal safety_failed
                if generation != owner._generation:
                    return
                kind = getattr(event.type, "value", event.type)
                data = event.data
                if kind == "session.idle":
                    idle.set()
                    if accepting and not queue.full():
                        queue.put_nowait(("done", None))
                    return
                if not accepting:
                    return
                if getattr(event, "agent_id", None) or getattr(data, "parent_tool_call_id", None):
                    safety_failed = True
                    fail("Unexpected sub-agent activity in conversation mode.")
                    return
                if (
                    str(kind).startswith("tool.")
                    or kind == "permission.requested"
                    or getattr(data, "tool_requests", None)
                ):
                    safety_failed = True
                    fail("Unexpected tool activity in no-tools conversation mode.")
                    return
                if kind in ("session.error", "session.shutdown"):
                    fail("Agent runtime reported a conversation error.")
                    return
                actual_model = getattr(data, "model", None)
                if actual_model and actual_model != owner.model:
                    safety_failed = True
                    fail("Agent runtime changed the explicitly selected model.")
                    return
                message_id = getattr(data, "message_id", "")
                if message_id and message_id in owner._retired_messages:
                    return
                if kind == "assistant.usage":
                    input_tokens = getattr(data, "input_tokens", None)
                    output_tokens = getattr(data, "output_tokens", None)
                    if (
                        type(input_tokens) is int
                        and type(output_tokens) is int
                        and input_tokens >= 0
                        and output_tokens >= 0
                    ):
                        if queue.full():
                            fail("Agent conversation exceeded the bounded event buffer.")
                        else:
                            queue.put_nowait(
                                (
                                    "usage",
                                    llm.CompletionUsage(
                                        prompt_tokens=input_tokens,
                                        completion_tokens=output_tokens,
                                        total_tokens=input_tokens + output_tokens,
                                    ),
                                )
                            )
                    return
                if kind == "assistant.message_delta":
                    content = getattr(data, "delta_content", "")
                    if not content:
                        return
                    seen_messages.add(message_id)
                elif kind == "assistant.message":
                    if message_id in seen_messages:
                        return
                    seen_messages.add(message_id)
                    content = getattr(data, "content", "")
                    if not content:
                        return
                else:
                    return
                if not isinstance(content, str):
                    fail("Agent runtime returned invalid conversation text.")
                    return
                if queue.full():
                    fail("Agent conversation exceeded the bounded event buffer.")
                else:
                    queue.put_nowait(("text", content))

            unsubscribe: Callable[[], None] = owner._session.on(on_event)
            try:
                sent_task = asyncio.create_task(
                    owner._session.send(prompt, mode="immediate", agent_mode="interactive")
                )
                async with asyncio.timeout(self._conn_options.timeout):
                    await asyncio.shield(sent_task)
                    while True:
                        if failed.is_set():
                            raise _error(failure_message)
                        kind, content = await queue.get()
                        if failed.is_set():
                            raise _error(failure_message)
                        if kind == "done":
                            finished = True
                            break
                        if kind == "usage":
                            self._event_ch.send_nowait(
                                llm.ChatChunk(id=f"agent-{generation}", usage=content)
                            )
                            continue
                        response_size += len(content)
                        if response_size > _MAX_RESPONSE_CHARS:
                            raise _error("Agent conversation exceeded the bounded response limit.")
                        response.append(content)
                        self._event_ch.send_nowait(
                            llm.ChatChunk(
                                id=f"agent-{generation}",
                                delta=llm.ChoiceDelta(role="assistant", content=content),
                            )
                        )
                if not response:
                    raise _error("Agent runtime returned no assistant text.")
                owner._history = current + [{"role": "assistant", "content": "".join(response)}]
                owner._needs_snapshot = False
            except asyncio.CancelledError:
                raise
            except APIError:
                owner._needs_snapshot = True
                raise
            except Exception:
                owner._needs_snapshot = True
                raise _error(
                    "Agent conversation failed; check CLI readiness and authentication."
                ) from None
            finally:
                accepting = False
                if not finished:
                    owner._needs_snapshot = True
                    try:
                        async with asyncio.timeout(owner._abort_timeout):
                            if sent_task is not None:
                                await asyncio.shield(sent_task)
                            await owner._session.abort()
                            await idle.wait()
                    except (Exception, asyncio.CancelledError):
                        owner._uncertain = True
                        if sent_task is not None and not sent_task.done():
                            sent_task.cancel()
                            await asyncio.gather(sent_task, return_exceptions=True)
                        await owner._stop_runtime()
                if safety_failed:
                    owner._uncertain = True
                    await owner._stop_runtime()
                owner._retired_messages.extend(seen_messages)
                owner._generation += 1
                unsubscribe()


def _codex_configuration(
    *, work: Path, profile: str, servers: list[str], plugins: list[str]
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "default_permissions": profile,
        f"permissions.{profile}.filesystem": {
            ":root": "deny",
            ":minimal": "read",
            ":tmpdir": "deny",
            ":slash_tmp": "deny",
            str(Path.home()): "deny",
            str(work): "write",
        },
        f"permissions.{profile}.network.enabled": False,
        "approval_policy": "never",
        "allow_login_shell": False,
        "shell_environment_policy.inherit": "none",
        "shell_environment_policy.set": {
            "PATH": "/usr/bin:/bin",
            "HOME": str(work),
            "TMPDIR": str(work),
        },
        "project_doc_max_bytes": 0,
        "project_doc_fallback_filenames": [],
        "project_root_markers": [],
        "developer_instructions": "",
        "instructions": _SYSTEM,
        "mcp_servers": {name: {"enabled": False} for name in servers},
        "plugins": {name: {"enabled": False} for name in plugins},
        "web_search": "disabled",
        "history.persistence": "none",
        "features.skip_host_skill_discovery": True,
    }
    for flag in (
        "apps",
        "browser_use",
        "browser_use_external",
        "computer_use",
        "in_app_browser",
        "plugins",
        "remote_plugin",
        "hooks",
        "memories",
        "multi_agent",
        "multi_agent_v2",
        "skill_search",
        "skill_mcp_dependency_install",
        "view_image",
        "image_generation",
        "shell_snapshot",
        "code_mode",
        "code_mode_host",
        "request_permissions_tool",
        "sleep_tool",
        "tool_suggest",
        "chronicle",
        "in_app_local_automation",
    ):
        config[f"features.{flag}"] = False
    return config


def _toml_value(value: Any) -> str:
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(json.dumps(key) + "=" + _toml_value(item) for key, item in value.items())
            + "}"
        )
    return json.dumps(value)


def _codex_external_names(response: dict[str, Any], *, require_disabled: bool = False) -> list[str]:
    entries = response.get("data")
    if not isinstance(entries, list) or len(entries) > 128 or response.get("nextCursor"):
        raise _error("Codex external capability enumeration was incomplete.")
    names = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise _error("Codex external capability metadata was invalid.")
        if require_disabled and any(
            entry.get(key) for key in ("tools", "resources", "resourceTemplates")
        ):
            raise _error("Codex still advertises external capabilities after explicit disabling.")
        names.append(entry["name"])
    return names


def _codex_client(*, cli_path: str, work: Path, state: Path) -> _CodexClient:
    return _CodexClient(cli_path=cli_path, work=work, state=state)


class _CodexClient:
    """Bounded, owned stdio app-server transport; no SDK default approval handler."""

    def __init__(self, *, cli_path: str, work: Path, state: Path) -> None:
        self.binary = cli_path
        self.work = work
        self.state = state
        self.profile = "voicebox_" + uuid.uuid4().hex
        self.config = _codex_configuration(work=work, profile=self.profile, servers=[], plugins=[])
        self.process: asyncio.subprocess.Process | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.counter = 0
        self.session: _CodexSession | None = None
        self.closing = False
        self.restricted_capabilities: dict[str, Any] = {
            "mode": "restricted-agent",
            "tools_enabled": True,
            "external_tools": 0,
            "command_network_enabled": False,
            "filesystem_scope": "private-workdir-and-minimal-runtime-paths",
            "inherits_global_instructions": False,
        }

    def _config_args(self) -> list[str]:
        args: list[str] = []
        for key, value in self.config.items():
            args.extend(("-c", key + "=" + _toml_value(value)))
        return args

    async def _launch(self) -> None:
        self.closing = False
        self.process = await asyncio.create_subprocess_exec(
            self.binary,
            "app-server",
            "--listen",
            "stdio://",
            *self._config_args(),
            cwd=self.work,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1_048_576,
            start_new_session=True,
        )
        self.reader_task = asyncio.create_task(self._read())
        await self.request(
            "initialize",
            {
                "clientInfo": {"name": "voicebox_conversation", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self._write({"method": "initialized"})

    async def _write(self, message: dict[str, Any]) -> None:
        if (
            self.process is None
            or self.process.stdin is None
            or self.process.returncode is not None
        ):
            raise _error("Codex runtime is not connected.")
        payload = (json.dumps(message) + "\n").encode()
        if len(payload) > 1_048_576:
            raise _error("Codex request exceeds the bounded transport limit.")
        self.process.stdin.write(payload)
        async with asyncio.timeout(5):
            await self.process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if len(self.pending) >= 16:
            raise _error("Codex exceeded the bounded pending request limit.")
        self.counter += 1
        request_id = self.counter
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            async with asyncio.timeout(30):
                return await future
        finally:
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Invalid protocol envelope")
                if "method" in message and "id" in message:
                    # All server-originated actions are denied, including permissions,
                    # file-change approvals, user input, and dynamic tools.
                    if message["method"] in (
                        "item/commandExecution/requestApproval",
                        "item/fileChange/requestApproval",
                    ):
                        await self._write({"id": message["id"], "result": {"decision": "decline"}})
                    else:
                        await self._write(
                            {
                                "id": message["id"],
                                "error": {"code": -32601, "message": "Denied by conversation host"},
                            }
                        )
                    continue
                if "id" in message:
                    future = self.pending.get(message["id"])
                    if future is not None and not future.done():
                        if "error" in message or not isinstance(message.get("result"), dict):
                            future.set_exception(_error("Codex rejected a conversation request."))
                        else:
                            future.set_result(message["result"])
                elif self.session is not None:
                    self.session.notify(message.get("method", ""), message.get("params", {}))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(_error("Codex conversation transport closed."))
            if not self.closing and self.session is not None:
                self.session.emit("session.error")

    async def _plugins(self, *, require_disabled: bool = False) -> list[str]:
        result = await self.request(
            "plugin/list",
            {"cwds": [str(self.work)], "forceRefetch": False, "marketplaceKinds": ["local"]},
        )
        if result.get("marketplaceLoadErrors") or not isinstance(result.get("marketplaces"), list):
            raise _error("Codex plugin capability enumeration was incomplete.")
        installed: list[str] = []
        for marketplace in result["marketplaces"]:
            for plugin in marketplace.get("plugins", []):
                if plugin.get("installed"):
                    if require_disabled and plugin.get("enabled"):
                        raise _error("Codex still has an enabled installed plugin.")
                    name = plugin.get("id")
                    if not isinstance(name, str) or len(installed) >= 128:
                        raise _error("Codex plugin metadata exceeds the bounded limit.")
                    installed.append(name)
        return installed

    async def start(self) -> None:
        if sys.platform != "darwin":
            raise _error("Restricted Codex is currently verified only with the macOS sandbox.")
        await self._launch()
        servers = _codex_external_names(await self.request("mcpServerStatus/list", {"limit": 128}))
        plugins = await self._plugins()
        await self.stop()
        self.config = _codex_configuration(
            work=self.work, profile=self.profile, servers=servers, plugins=plugins
        )
        await self._launch()
        _codex_external_names(
            await self.request("mcpServerStatus/list", {"limit": 128}), require_disabled=True
        )
        await self._plugins(require_disabled=True)
        await self._verify_sandbox()

    async def _sandbox_code(self, command: list[str]) -> int:
        process = await asyncio.create_subprocess_exec(
            self.binary,
            "sandbox",
            "-P",
            self.profile,
            "-C",
            str(self.work),
            *self._config_args(),
            "--",
            *command,
            cwd=self.work,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(5):
                return await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def _verify_sandbox(self) -> None:
        inside, outside = self.work / "scope-canary", self.state / "scope-canary"
        inside.write_text("synthetic")
        outside.write_text("synthetic")
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(4)
            port = listener.getsockname()[1]
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            checks = [
                (["/bin/cat", str(inside)], 0),
                (["/bin/cat", str(outside)], 1),
                (["/bin/sh", "-c", 'printf synthetic > "$1"', "probe", str(inside)], 0),
                (["/bin/sh", "-c", 'printf synthetic > "$1"', "probe", str(outside)], 1),
                (["/usr/bin/nc", "-z", "-w", "1", "127.0.0.1", str(port)], 1),
            ]
            for command, expected in checks:
                if await self._sandbox_code(command) != expected:
                    raise _error("Codex could not verify scoped filesystem/network enforcement.")
        finally:
            listener.close()
            inside.unlink(missing_ok=True)
            outside.unlink(missing_ok=True)

    async def list_models(self) -> list[Any]:
        result = await self.request("model/list", {"includeHidden": False})
        if result.get("nextCursor"):
            raise _error("Codex model catalog is incomplete.")
        return [
            SimpleNamespace(
                id=model["id"],
                policy=None,
                supported_reasoning_efforts=[
                    effort["reasoningEffort"]
                    for effort in model.get("supportedReasoningEfforts", [])
                ],
            )
            for model in result.get("data", [])
        ]

    async def create_session(self, *, model: str, reasoning_effort: str) -> _CodexSession:
        result = await self.request(
            "thread/start",
            {
                "model": model,
                "cwd": str(self.work),
                "ephemeral": True,
                "approvalPolicy": "never",
                "approvalsReviewer": "user",
                "permissions": self.profile,
                "runtimeWorkspaceRoots": [str(self.work)],
                "environments": [],
                "selectedCapabilityRoots": [],
                "dynamicTools": [],
                "allowProviderModelFallback": False,
                "baseInstructions": _SYSTEM,
                "developerInstructions": "",
                "config": {"model_reasoning_effort": reasoning_effort},
            },
        )
        sandbox = result.get("sandbox", {})
        if (
            result.get("model") != model
            or result.get("reasoningEffort") != reasoning_effort
            or result.get("activePermissionProfile", {}).get("id") != self.profile
            or result.get("approvalPolicy") != "never"
            or sandbox.get("networkAccess") is not False
            or sandbox.get("excludeTmpdirEnvVar") is not True
            or sandbox.get("excludeSlashTmp") is not True
            or sandbox.get("writableRoots")
            or Path(result.get("cwd", "")) != self.work
            or result.get("thread", {}).get("gitInfo")
        ):
            raise _error("Codex did not apply the exact restricted conversation settings.")
        sources = result.get("instructionSources") or []
        config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
        for source in sources:
            path = Path(source).resolve()
            if path.parent != config_home or path.name not in ("AGENTS.md", "AGENTS.override.md"):
                raise _error("Codex inherited instructions outside trusted global configuration.")
        self.restricted_capabilities["inherits_global_instructions"] = bool(sources)
        # Re-attest after thread initialization, which can activate deferred capabilities.
        _codex_external_names(
            await self.request("mcpServerStatus/list", {"limit": 128}), require_disabled=True
        )
        await self._plugins(require_disabled=True)
        self.session = _CodexSession(self, result["thread"]["id"])
        self.session.model, self.session.effort = model, reasoning_effort
        return self.session

    async def stop(self) -> None:
        self.closing = True
        process = self.process
        if process is not None and process.returncode is None:
            # This process group was created by this adapter; never address a
            # pre-existing CLI, daemon, or caller process group.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(2):
                    await process.wait()
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        self.process = None
        if self.reader_task is not None:
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)
            self.reader_task = None

    async def force_stop(self) -> None:
        await self.stop()


class _CodexSession:
    def __init__(self, client: _CodexClient, session_id: str) -> None:
        self.client = client
        self.session_id = session_id
        self.turn_id: str | None = None
        self.model: str | None = None
        self.effort: str | None = None
        self.retired: deque[str] = deque(maxlen=256)
        self.handlers: set[Callable[[Any], None]] = set()

    def on(self, handler: Callable[[Any], None]) -> Callable[[], None]:
        self.handlers.add(handler)
        return lambda: self.handlers.discard(handler)

    def emit(self, kind: str, **data: Any) -> None:
        event = SimpleNamespace(type=kind, data=SimpleNamespace(**data))
        for handler in list(self.handlers):
            handler(event)

    async def send(self, prompt: str, **_kwargs: Any) -> str:
        response = await self.client.request(
            "turn/start",
            {"threadId": self.session_id, "input": [{"type": "text", "text": prompt}]},
        )
        turn_id: str = response["turn"]["id"]
        if turn_id not in self.retired:
            self.turn_id = turn_id
        return turn_id

    async def abort(self) -> None:
        if self.turn_id is not None:
            await self.client.request(
                "turn/interrupt", {"threadId": self.session_id, "turnId": self.turn_id}
            )

    def notify(self, method: str, params: dict[str, Any]) -> None:
        if params.get("threadId") != self.session_id:
            return
        turn = params.get("turn", {})
        turn_id = params.get("turnId") or turn.get("id")
        if turn_id in self.retired:
            return
        if method == "turn/started":
            self.turn_id = turn_id
            return
        if self.turn_id is not None and turn_id is not None and turn_id != self.turn_id:
            return
        if method == "item/agentMessage/delta":
            self.emit(
                "assistant.message_delta",
                message_id=params.get("itemId", ""),
                delta_content=params.get("delta", ""),
            )
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            kind = item.get("type")
            if kind not in (
                "agentMessage",
                "userMessage",
                "reasoning",
                "commandExecution",
                "fileChange",
                "plan",
                "contextCompaction",
            ):
                self.emit("tool.execution_start")
            elif kind == "agentMessage" and method == "item/completed":
                self.emit(
                    "assistant.message", message_id=item.get("id", ""), content=item.get("text", "")
                )
        elif method == "turn/completed":
            if turn.get("status") not in ("completed", "interrupted"):
                self.emit("session.error")
            if isinstance(turn_id, str):
                self.retired.append(turn_id)
            self.turn_id = None
            self.emit("session.idle", aborted=turn.get("status") == "interrupted")
        elif method == "error":
            self.emit("session.error")
        elif method == "thread/settings/updated":
            settings = params.get("threadSettings", {})
            if (
                settings.get("model") != self.model
                or settings.get("effort") != self.effort
                or settings.get("activePermissionProfile", {}).get("id") != self.client.profile
                or settings.get("approvalPolicy") != "never"
            ):
                self.emit("tool.execution_start")
