"""LiveKit LLM adapter for Hermes' authoritative streamed Runs API."""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Never

from livekit.agents import APIConnectOptions, APIError, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr

from examples.hermes_api import HermesRunsClient, RunEvent

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_INTERRUPTION_CONTEXT = "Voice context: previous spoken reply was interrupted."


class _AdapterAPIError(APIError):
    """An API error whose message is safe to expose outside this adapter."""


def _error(message: str) -> _AdapterAPIError:
    return _AdapterAPIError(message, retryable=False)


@dataclass(frozen=True)
class ApprovalRequest:
    run_id: str
    request_id: str
    command: str
    choices: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalResolution:
    run_id: str
    request_id: str


@dataclass(frozen=True)
class ToolStatus:
    run_id: str
    event_id: str
    phase: Literal["started", "completed"]
    tool: str
    preview: str | None = None
    duration: float | None = None
    error: bool | None = None


@dataclass
class _RunState:
    run_id: str
    generation: int
    stop_requested: bool = False
    stop_sent: bool = False
    terminal: bool = False
    status_sequence: int = 0
    approval_ids: set[str] = field(default_factory=set)
    stop_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class _PendingApproval:
    state: _RunState
    choices: tuple[str, ...]
    in_flight: bool = False


@dataclass(frozen=True)
class HermesRunHandle:
    """Opaque identity for one exact run owned by a HermesLLM instance."""

    run_id: str
    _state: _RunState = field(repr=False, compare=False)
    _owner: object = field(repr=False, compare=False)


class HermesLLM(llm.LLM[Never]):
    """Expose one Hermes run at a time through LiveKit's streaming LLM contract."""

    def __init__(
        self,
        *,
        client: HermesRunsClient,
        on_approval: Callable[[ApprovalRequest], Awaitable[None]],
        on_approval_resolved: Callable[[ApprovalResolution], Awaitable[None]] | None = None,
        on_tool_status: Callable[[ToolStatus], Awaitable[None]] | None = None,
        max_response_chars: int = 32_000,
    ) -> None:
        super().__init__()
        if max_response_chars <= 0:
            raise ValueError("max_response_chars must be positive.")
        self._client = client
        self._on_approval = on_approval
        self._on_approval_resolved = on_approval_resolved
        self._on_tool_status = on_tool_status
        self._max_response_chars = max_response_chars
        self._run_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._generation = 0
        self._active: _RunState | None = None
        self._admission_task: asyncio.Task[_RunState] | None = None
        self._admission_stop_requested = False
        self._closing = False
        self._run_handle_owner = object()
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._uncertain = False
        self._stream_slots = 0
        self._streams: set[_HermesStream] = set()
        self._interruption_context_pending = False

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

    def capture_active_run(self) -> HermesRunHandle | None:
        state = self._active
        if state is None or state.terminal:
            return None
        return HermesRunHandle(state.run_id, state, self._run_handle_owner)

    def _check_ready(self) -> None:
        if self._uncertain:
            raise _error("Hermes conversation state is uncertain; create a new adapter.")
        if self._closing:
            raise _error("Hermes adapter is closing; create a new adapter.")

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
        if self._stream_slots >= 2:
            raise _error("Hermes allows one active and one pending voice turn.")
        self._stream_slots += 1
        try:
            stream = _HermesStream(self, chat_ctx=chat_ctx.copy(), conn_options=conn_options)
            self._streams.add(stream)
            return stream
        except BaseException:
            self._stream_slots -= 1
            raise

    async def respond_to_approval(self, run_id: str, request_id: str, choice: str) -> None:
        self._check_ready()
        pending = self._pending_approvals.get(request_id)
        if pending is None:
            raise _error("The Hermes approval request is not current.")
        state = pending.state
        if (
            state.run_id != run_id
            or self._active is not state
            or state.terminal
            or state.stop_requested
        ):
            raise _error("The Hermes approval request is not current.")
        if choice not in pending.choices:
            raise _error("The Hermes approval choice is not valid for this request.")
        if pending.in_flight:
            raise _error("The Hermes approval request is already being processed.")
        pending.in_flight = True
        try:
            await self._client.approve(state.run_id, request_id, choice)
        except asyncio.CancelledError:
            if self._pending_approvals.get(request_id) is pending:
                pending.in_flight = False
            raise
        except Exception:
            if self._pending_approvals.get(request_id) is pending:
                pending.in_flight = False
            raise _error("Hermes approval response failed.") from None

    async def stop_active(self) -> bool:
        state = self._active
        if state is None:
            return False
        run = HermesRunHandle(state.run_id, state, self._run_handle_owner)
        return await self.stop_run(run)

    async def stop_run(self, run: HermesRunHandle) -> bool:
        """Stop only the captured run, even if another run becomes active."""
        if run._owner is not self._run_handle_owner:
            return False
        state = run._state
        if state.run_id != run.run_id:
            return False
        state.stop_requested = True
        acknowledged = await self._stop_and_wait(state)
        if acknowledged:
            self._interruption_context_pending = True
        return acknowledged

    async def _stop_and_wait(self, state: _RunState) -> bool:
        async with state.stop_lock:
            if state.terminal:
                return True
            timeout = self._client.config.stop_timeout
            deadline = asyncio.get_running_loop().time() + timeout
            try:
                async with asyncio.timeout_at(deadline):
                    if not state.stop_sent:
                        state.stop_sent = True
                        await self._client.stop(state.run_id)
                    while True:
                        status = await self._client.status(state.run_id)
                        value = status.get("status")
                        if isinstance(value, str) and value in _TERMINAL_STATES:
                            state.terminal = True
                            await self._clear_approvals(state)
                            return True
                        await asyncio.sleep(min(0.01, timeout / 10))
            except asyncio.CancelledError:
                self._uncertain = True
                raise
            except Exception:
                self._uncertain = True
                return False

    async def _notify_resolution(self, run_id: str, request_id: str) -> None:
        if self._on_approval_resolved is None:
            return
        try:
            await self._on_approval_resolved(ApprovalResolution(run_id, request_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _error("Hermes approval resolution could not be published.") from None

    async def _clear_approvals(self, state: _RunState) -> None:
        request_ids = [
            request_id
            for request_id, pending in self._pending_approvals.items()
            if pending.state is state
        ]
        failed = False
        for request_id in request_ids:
            try:
                await self._notify_resolution(state.run_id, request_id)
            except _AdapterAPIError:
                failed = True
            else:
                self._pending_approvals.pop(request_id, None)
        if failed:
            raise _error("Hermes approval resolutions could not be published.")

    async def _admit(self, request_text: str, generation: int) -> _RunState:
        try:
            handle = await self._client.start(request_text, idempotency_key=uuid.uuid4().hex)
        except asyncio.CancelledError:
            self._uncertain = True
            raise
        except Exception:
            raise _error("Hermes run could not be started.") from None

        self._interruption_context_pending = False
        state = _RunState(handle.run_id, generation)
        self._active = state
        if self._closing or self._admission_stop_requested:
            state.stop_requested = True
            if not await self._stop_and_wait(state):
                self._uncertain = True
        return state

    async def aclose(self) -> None:
        async with self._close_lock:
            self._closing = True
            admission = self._admission_task
            if admission is not None and not admission.done():
                self._admission_stop_requested = True
                try:
                    async with asyncio.timeout(self._client.config.stop_timeout):
                        await asyncio.shield(admission)
                except TimeoutError:
                    self._uncertain = True
                    raise _error(
                        "Hermes run admission is uncertain; the client remains open for recovery."
                    ) from None
                except asyncio.CancelledError:
                    self._uncertain = True
                    raise
                except Exception:
                    # A definitive admission failure leaves no run to drain.
                    pass

            state = self._active
            if state is not None and not state.terminal:
                state.stop_requested = True
                if not await self._stop_and_wait(state):
                    self._uncertain = True
                    raise _error(
                        "Hermes run closed without terminal acknowledgement; "
                        "the client remains open for recovery."
                    )
            await asyncio.gather(
                *(stream.aclose() for stream in tuple(self._streams)),
                return_exceptions=True,
            )
            await super().aclose()
            await self._client.aclose()


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
        owner._pending_approvals[request_id] = _PendingApproval(state, exact_choices)
        await owner._on_approval(ApprovalRequest(state.run_id, request_id, command, exact_choices))

    async def _forward_tool_status(self, event: RunEvent, state: _RunState) -> None:
        callback = self._owner._on_tool_status
        if callback is None:
            return
        payload = event.payload
        tool = payload.get("tool")
        preview = payload.get("preview")
        if (
            not isinstance(tool, str)
            or not tool
            or len(tool) > 100
            or (preview is not None and (not isinstance(preview, str) or len(preview) > 500))
        ):
            raise _error("Hermes returned an invalid tool status.")
        phase: Literal["started", "completed"]
        duration: float | None = None
        error: bool | None = None
        if event.type == "tool.started":
            phase = "started"
        elif event.type == "tool.completed":
            raw_duration = payload.get("duration")
            raw_error = payload.get("error")
            if (
                isinstance(raw_duration, bool)
                or not isinstance(raw_duration, (int, float))
                or not math.isfinite(raw_duration)
                or raw_duration < 0
                or raw_duration > 86_400
                or not isinstance(raw_error, bool)
            ):
                raise _error("Hermes returned an invalid tool status.")
            phase = "completed"
            duration = float(raw_duration)
            error = raw_error
        else:
            return
        state.status_sequence += 1
        status = ToolStatus(
            state.run_id,
            f"{state.run_id}:{state.status_sequence}",
            phase,
            tool,
            preview,
            duration,
            error,
        )
        try:
            await callback(status)
        except asyncio.CancelledError:
            self._owner._uncertain = True
            raise
        except Exception:
            self._owner._uncertain = True
            raise _error("Hermes tool status could not be published.") from None

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
                await owner._clear_approvals(state)
                break
            if event.type == "message.delta":
                if state.stop_requested:
                    continue
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
                if not state.stop_requested:
                    await self._forward_approval(event, state)
            elif event.type == "approval.responded":
                request_id = event.payload.get("request_id")
                if isinstance(request_id, str):
                    pending = owner._pending_approvals.get(request_id)
                    if pending is not None and pending.state is state:
                        await owner._notify_resolution(state.run_id, request_id)
                        owner._pending_approvals.pop(request_id, None)
            elif event.type in {"tool.started", "tool.completed"}:
                await self._forward_tool_status(event, state)

        if terminal is None:
            status = await owner._client.status(state.run_id)
            value = status.get("status")
            if isinstance(value, str) and value in _TERMINAL_STATES:
                terminal = value
                state.terminal = True
                await owner._clear_approvals(state)
            else:
                raise _error("Hermes event stream ended before terminal acknowledgement.")
        if terminal == "completed":
            return
        if terminal == "cancelled" and state.stop_requested:
            raise asyncio.CancelledError
        raise _error("Hermes run did not complete successfully.")

    async def _run(self) -> None:
        try:
            await self._run_reserved()
        finally:
            self._owner._stream_slots -= 1
            self._owner._streams.discard(self)

    async def _run_reserved(self) -> None:
        owner = self._owner
        async with owner._run_lock:
            owner._check_ready()
            text = self._latest_user_text(self._chat_ctx)
            request_text = (
                f"{_INTERRUPTION_CONTEXT}\n\nUser: {text}"
                if owner._interruption_context_pending
                else text
            )
            owner._generation += 1
            generation = owner._generation
            owner._admission_stop_requested = False
            admission = asyncio.create_task(owner._admit(request_text, generation))
            owner._admission_task = admission
            try:
                state = await asyncio.shield(admission)
            except asyncio.CancelledError:
                owner._admission_stop_requested = True
                if admission.done() and not admission.cancelled():
                    try:
                        admitted = admission.result()
                    except Exception:
                        pass
                    else:
                        self._state = admitted
                        admitted.stop_requested = True
                        if not await owner._stop_and_wait(admitted):
                            owner._uncertain = True
                else:
                    owner._uncertain = True
                raise
            finally:
                if admission.done() and owner._admission_task is admission:
                    owner._admission_task = None
            self._state = state
            if state.stop_requested and state.terminal:
                raise asyncio.CancelledError
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
            except _AdapterAPIError:
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
                await owner._clear_approvals(state)
                if owner._active is state and owner._generation == generation:
                    owner._active = None
