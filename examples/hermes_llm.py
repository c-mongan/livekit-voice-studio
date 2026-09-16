"""LiveKit LLM adapter for Hermes' authoritative streamed Runs API."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Never

from livekit.agents import APIConnectOptions, APIError, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

from examples.hermes_api import HermesRunsClient, RunEvent

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})


def _error(message: str) -> APIError:
    return APIError(message, retryable=False)


@dataclass(frozen=True)
class ApprovalRequest:
    run_id: str
    request_id: str
    command: str
    choices: tuple[str, ...]


@dataclass
class _RunState:
    run_id: str
    generation: int
    stop_requested: bool = False
    stop_sent: bool = False
    terminal: bool = False
    approval_ids: set[str] = field(default_factory=set)
    stop_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class HermesLLM(llm.LLM[Never]):
    """Expose one Hermes run at a time through LiveKit's streaming LLM contract."""

    def __init__(
        self,
        *,
        client: HermesRunsClient,
        on_approval: Callable[[ApprovalRequest], Awaitable[None]],
        max_response_chars: int = 32_000,
    ) -> None:
        super().__init__()
        if max_response_chars <= 0:
            raise ValueError("max_response_chars must be positive.")
        self._client = client
        self._on_approval = on_approval
        self._max_response_chars = max_response_chars
        self._run_lock = asyncio.Lock()
        self._generation = 0
        self._active: _RunState | None = None
        self._pending_approvals: dict[str, tuple[_RunState, tuple[str, ...]]] = {}
        self._uncertain = False

    @property
    def provider(self) -> str:
        return "hermes"

    @property
    def model(self) -> str:
        return "hermes-runs"

    @property
    def active_run_id(self) -> str | None:
        state = self._active
        return state.run_id if state is not None and not state.terminal else None

    def _check_ready(self) -> None:
        if self._uncertain:
            raise _error("Hermes conversation state is uncertain; create a new adapter.")

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
        del parallel_tool_calls, tool_choice, extra_kwargs
        self._check_ready()
        if tools:
            raise _error("Hermes owns tools; LiveKit tools are not accepted.")
        return _HermesStream(self, chat_ctx=chat_ctx.copy(), conn_options=conn_options)

    async def respond_to_approval(self, request_id: str, choice: str) -> None:
        self._check_ready()
        pending = self._pending_approvals.get(request_id)
        if pending is None:
            raise _error("The Hermes approval request is not current.")
        state, choices = pending
        if self._active is not state or state.terminal or state.stop_requested:
            raise _error("The Hermes approval request is not current.")
        if choice not in choices:
            raise _error("The Hermes approval choice is not valid for this request.")
        self._pending_approvals.pop(request_id, None)
        try:
            await self._client.approve(state.run_id, request_id, choice)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _error("Hermes approval response failed.") from None

    async def stop_active(self) -> bool:
        state = self._active
        if state is None:
            return False
        state.stop_requested = True
        return await self._stop_and_wait(state)

    async def _stop_and_wait(self, state: _RunState) -> bool:
        async with state.stop_lock:
            if state.terminal:
                return True
            try:
                if not state.stop_sent:
                    state.stop_sent = True
                    await self._client.stop(state.run_id)
                timeout = self._client.config.stop_timeout
                async with asyncio.timeout(timeout):
                    while True:
                        status = await self._client.status(state.run_id)
                        value = status.get("status")
                        if isinstance(value, str) and value in _TERMINAL_STATES:
                            state.terminal = True
                            self._clear_approvals(state)
                            return True
                        await asyncio.sleep(min(0.01, timeout / 10))
            except asyncio.CancelledError:
                self._uncertain = True
                raise
            except Exception:
                self._uncertain = True
                return False

    def _clear_approvals(self, state: _RunState) -> None:
        for request_id, (owner, _choices) in list(self._pending_approvals.items()):
            if owner is state:
                self._pending_approvals.pop(request_id, None)


class _HermesStream(llm.LLMStream):
    def __init__(
        self, owner: HermesLLM, *, chat_ctx: llm.ChatContext, conn_options: APIConnectOptions
    ) -> None:
        self._owner = owner
        self._state: _RunState | None = None
        super().__init__(owner, chat_ctx=chat_ctx, tools=[], conn_options=conn_options)

    @staticmethod
    def _latest_user_text(chat_ctx: llm.ChatContext) -> str:
        for item in reversed(chat_ctx.items):
            if not isinstance(item, llm.ChatMessage) or item.role != "user":
                continue
            if any(not isinstance(part, str) for part in item.content):
                raise _error("Hermes voice turns accept text only.")
            text = item.raw_text_content or ""
            if text.strip():
                return text
        raise _error("Hermes requires a non-empty user turn.")

    @staticmethod
    def _belongs_to(event: RunEvent, state: _RunState) -> bool:
        event_run_id = event.payload.get("run_id")
        return event_run_id is None or event_run_id == state.run_id

    @staticmethod
    def _terminal_state(event: RunEvent) -> str | None:
        if event.type.startswith("run."):
            state = event.type.removeprefix("run.")
            if state in _TERMINAL_STATES:
                return state
        return None

    async def _forward_approval(self, event: RunEvent, state: _RunState) -> None:
        payload = event.payload
        request_id = payload.get("request_id")
        command = payload.get("command")
        choices = payload.get("choices")
        if (
            payload.get("run_id") != state.run_id
            or not isinstance(request_id, str)
            or not request_id
            or not isinstance(command, str)
            or not isinstance(choices, list)
            or not choices
            or any(not isinstance(choice, str) or not choice for choice in choices)
        ):
            raise _error("Hermes returned an invalid approval request.")
        exact_choices = tuple(choices)
        owner = self._owner
        if request_id in state.approval_ids:
            raise _error("Hermes reused an approval request identifier.")
        state.approval_ids.add(request_id)
        owner._pending_approvals[request_id] = (state, exact_choices)
        await owner._on_approval(
            ApprovalRequest(state.run_id, request_id, command, exact_choices)
        )

    async def _consume(self, state: _RunState) -> None:
        owner = self._owner
        response_size = 0
        terminal: str | None = None
        async for event in owner._client.events(state.run_id):
            if owner._active is not state or not self._belongs_to(event, state):
                continue
            event_terminal = self._terminal_state(event)
            if event_terminal is not None:
                terminal = event_terminal
                state.terminal = True
                owner._clear_approvals(state)
                break
            if state.stop_requested:
                continue
            if event.type == "message.delta":
                content = event.payload.get("delta")
                if not isinstance(content, str) or not content:
                    continue
                response_size += len(content)
                if response_size > owner._max_response_chars:
                    raise _error("Hermes response exceeded the bounded response limit.")
                self._event_ch.send_nowait(
                    llm.ChatChunk(
                        id=state.run_id,
                        delta=llm.ChoiceDelta(role="assistant", content=content),
                    )
                )
            elif event.type == "approval.request":
                await self._forward_approval(event, state)
            elif event.type == "approval.responded":
                request_id = event.payload.get("request_id")
                if isinstance(request_id, str):
                    pending = owner._pending_approvals.get(request_id)
                    if pending is not None and pending[0] is state:
                        owner._pending_approvals.pop(request_id, None)

        if terminal is None:
            status = await owner._client.status(state.run_id)
            value = status.get("status")
            if isinstance(value, str) and value in _TERMINAL_STATES:
                terminal = value
                state.terminal = True
                owner._clear_approvals(state)
            else:
                raise _error("Hermes event stream ended before terminal acknowledgement.")
        if terminal == "completed":
            return
        if terminal == "cancelled" and state.stop_requested:
            raise asyncio.CancelledError
        raise _error("Hermes run did not complete successfully.")

    async def _run(self) -> None:
        owner = self._owner
        async with owner._run_lock:
            owner._check_ready()
            text = self._latest_user_text(self._chat_ctx)
            owner._generation += 1
            generation = owner._generation
            try:
                handle = await owner._client.start(text, idempotency_key=uuid.uuid4().hex)
            except asyncio.CancelledError:
                owner._uncertain = True
                raise
            except Exception:
                raise _error("Hermes run could not be started.") from None

            state = _RunState(handle.run_id, generation)
            self._state = state
            owner._active = state
            try:
                async with asyncio.timeout(self._conn_options.timeout):
                    await self._consume(state)
            except asyncio.CancelledError:
                state.stop_requested = True
                await owner._stop_and_wait(state)
                raise
            except TimeoutError:
                state.stop_requested = True
                await owner._stop_and_wait(state)
                raise _error("Hermes run timed out.") from None
            except APIError:
                if not state.terminal:
                    state.stop_requested = True
                    await owner._stop_and_wait(state)
                raise
            except Exception:
                if not state.terminal:
                    state.stop_requested = True
                    await owner._stop_and_wait(state)
                raise _error("Hermes run failed.") from None
            finally:
                owner._clear_approvals(state)
                if owner._active is state and owner._generation == generation:
                    owner._active = None
