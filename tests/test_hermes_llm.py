"""Deterministic LiveKit-to-Hermes adapter tests; no network or inference."""

import asyncio
import traceback
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from livekit.agents import APIConnectOptions, APIError, llm

from examples.hermes_api import HermesAPIError, RunEvent, RunHandle
from examples.hermes_llm import (
    ApprovalRequest,
    ApprovalResolution,
    HermesLLM,
    ToolStatus,
    _RunState,
)


class FakeClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(stop_timeout=0.05)
        self.starts: list[dict[str, str]] = []
        self.stopped: list[str] = []
        self.approvals: list[tuple[str, str, str]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block_events = False
        self.runs: list[list[RunEvent]] = [
            [
                RunEvent("message.delta", {"delta": "Hel"}),
                RunEvent("message.delta", {"delta": "lo"}),
                RunEvent("run.completed", {"status": "completed"}),
            ]
        ]
        self.statuses: dict[str, list[dict[str, object]]] = {}
        self.status_calls: list[str] = []
        self.closed = False

    async def start(self, text: str, *, idempotency_key: str) -> RunHandle:
        run_id = f"run_{len(self.starts) + 1}"
        self.starts.append({"text": text, "idempotency_key": idempotency_key, "run_id": run_id})
        self.started.set()
        return RunHandle(run_id)

    async def events(self, run_id: str) -> AsyncIterator[RunEvent]:
        index = int(run_id.removeprefix("run_")) - 1
        for event in self.runs[index]:
            yield event
        if self.block_events:
            await self.release.wait()

    async def status(self, run_id: str) -> dict[str, object]:
        self.status_calls.append(run_id)
        values = self.statuses.setdefault(run_id, [{"status": "completed"}])
        if len(values) > 1:
            return values.pop(0)
        return values[0]

    async def stop(self, run_id: str) -> dict[str, object]:
        self.stopped.append(run_id)
        return {"run_id": run_id, "status": "stopping"}

    async def approve(self, run_id: str, request_id: str, choice: str) -> None:
        self.approvals.append((run_id, request_id, choice))

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


def context(*messages: tuple[str, str]) -> llm.ChatContext:
    ctx = llm.ChatContext()
    for role, text in messages:
        ctx.add_message(role=role, content=text)
    return ctx


async def collect(stream: llm.LLMStream) -> list[llm.ChatChunk]:
    async with stream:
        return [chunk async for chunk in stream]


def options(timeout: float = 1) -> APIConnectOptions:
    return APIConnectOptions(timeout=timeout, max_retry=0)


async def test_message_deltas_become_livekit_chunks(client: FakeClient) -> None:
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("assistant", "old"), ("user", "hello")))
    chunks = await collect(stream)

    assert [chunk.delta.content for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    assert client.starts[0]["text"] == "hello"
    assert len(client.starts[0]["idempotency_key"]) == 32
    assert model.active_run_id is None


async def test_model_close_closes_credential_bearing_client(client: FakeClient) -> None:
    model = HermesLLM(client=client, on_approval=AsyncMock())

    await model.aclose()

    assert client.closed


async def test_model_close_stops_active_run_before_closing_client(client: FakeClient) -> None:
    client.runs = [[]]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "running"}, {"status": "cancelled"}]
    order: list[str] = []
    original_stop = client.stop
    original_close = client.aclose

    async def stop(run_id: str) -> dict[str, object]:
        order.append(f"stop:{run_id}")
        return await original_stop(run_id)

    async def close() -> None:
        order.append("close")
        await original_close()

    client.stop = stop  # type: ignore[method-assign]
    client.aclose = close  # type: ignore[method-assign]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "one")))
    task = asyncio.create_task(collect(stream))
    await client.started.wait()

    await model.aclose()
    await task

    assert order == ["stop:run_1", "close"]
    assert client.stopped == ["run_1"]


async def test_model_close_surfaces_unacknowledged_active_run_after_client_close(
    client: FakeClient,
) -> None:
    client.config.stop_timeout = 0.01
    client.runs = [[]]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "running"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "one")))
    task = asyncio.create_task(collect(stream))
    await client.started.wait()

    with pytest.raises(APIError, match="terminal acknowledgement"):
        await model.aclose()

    assert client.closed
    assert client.stopped == ["run_1"]
    await task


async def test_empty_user_turn_and_livekit_tools_are_rejected(client: FakeClient) -> None:
    model = HermesLLM(client=client, on_approval=AsyncMock())

    with pytest.raises(APIError, match="non-empty user"):
        await collect(model.chat(chat_ctx=context(("assistant", "hello"), ("user", "  "))))
    with pytest.raises(APIError, match="Hermes owns tools"):
        model.chat(chat_ctx=context(("user", "hello")), tools=[object()])  # type: ignore[list-item]

    assert client.starts == []


async def test_only_one_stream_starts_and_waiting_close_does_not_stop_owner(
    client: FakeClient,
) -> None:
    client.runs = [[], []]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    first = model.chat(chat_ctx=context(("user", "one")))
    await client.started.wait()
    second = model.chat(chat_ctx=context(("user", "two")))
    await asyncio.sleep(0)

    assert [item["text"] for item in client.starts] == ["one"]
    await second.aclose()
    assert client.stopped == []
    await first.aclose()
    assert client.stopped == ["run_1"]


async def test_third_stream_is_rejected_while_one_is_active_and_one_waits(
    client: FakeClient,
) -> None:
    client.runs = [[], []]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    first = model.chat(chat_ctx=context(("user", "one")))
    await client.started.wait()
    second = model.chat(chat_ctx=context(("user", "two")))

    with pytest.raises(APIError, match="one pending"):
        model.chat(chat_ctx=context(("user", "three")))

    await second.aclose()
    await first.aclose()


async def test_acknowledged_interruption_adds_trusted_context_to_next_run_once(
    client: FakeClient,
) -> None:
    client.runs = [
        [],
        [RunEvent("run.completed", {"status": "completed"})],
        [RunEvent("run.completed", {"status": "completed"})],
    ]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    first = model.chat(chat_ctx=context(("user", "start")))
    first_task = asyncio.create_task(collect(first))
    await client.started.wait()
    assert await model.stop_active() is True
    await first.aclose()
    await first_task
    client.block_events = False

    await collect(model.chat(chat_ctx=context(("user", "user text"))))
    await collect(model.chat(chat_ctx=context(("user", "later"))))

    assert client.starts[1]["text"] == (
        "Voice context: previous spoken reply was interrupted.\n\nUser: user text"
    )
    assert client.starts[2]["text"] == "later"


async def test_cancellation_stops_exact_active_run(client: FakeClient) -> None:
    client.runs = [[]]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "running"}, {"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "start")))
    task = asyncio.create_task(collect(stream))
    await client.started.wait()

    await stream.aclose()
    await task

    assert client.stopped == ["run_1"]
    assert client.status_calls == ["run_1", "run_1"]


async def test_aclose_stops_only_the_run_owned_by_that_stream(client: FakeClient) -> None:
    client.runs = [[], []]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    first = model.chat(chat_ctx=context(("user", "one")))
    await client.started.wait()
    waiting = model.chat(chat_ctx=context(("user", "two")))

    await waiting.aclose()
    assert client.stopped == []
    await first.aclose()
    assert client.stopped == ["run_1"]


async def test_stop_timeout_marks_adapter_uncertain(client: FakeClient) -> None:
    client.runs = [[]]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "running"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "one")))
    await client.started.wait()

    await stream.aclose()

    with pytest.raises(APIError, match="uncertain"):
        model.chat(chat_ctx=context(("user", "two")))
    assert client.stopped == ["run_1"]


async def test_stop_timeout_includes_hanging_stop_request(client: FakeClient) -> None:
    client.config.stop_timeout = 0.02
    client.runs = [[]]
    client.block_events = True
    stop_started = asyncio.Event()

    async def hanging_stop(run_id: str) -> dict[str, object]:
        client.stopped.append(run_id)
        stop_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    client.stop = hanging_stop  # type: ignore[method-assign]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "one")))
    task = asyncio.create_task(collect(stream))
    await client.started.wait()

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    assert await asyncio.wait_for(model.stop_active(), timeout=0.2) is False
    assert loop.time() - started_at < 0.15
    assert stop_started.is_set()
    assert client.stopped == ["run_1"]
    with pytest.raises(APIError, match="uncertain"):
        model.chat(chat_ctx=context(("user", "two")))

    await stream.aclose()
    await task


async def test_stop_active_returns_acknowledgement_and_sends_stop_once(client: FakeClient) -> None:
    client.runs = [[]]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "one")))
    await client.started.wait()

    assert await model.stop_active() is True
    assert await model.stop_active() is True
    assert client.stopped == ["run_1"]
    await stream.aclose()


async def test_captured_run_stop_does_not_follow_replaced_active_state(client: FakeClient) -> None:
    client.runs = [[]]
    client.block_events = True
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "one")))
    await client.started.wait()
    captured = model.capture_active_run()
    assert captured is not None

    replacement = _RunState("run_2", 2)
    model._active = replacement
    assert await model.stop_run(captured) is True

    assert client.stopped == ["run_1"]
    assert replacement.stop_requested is False
    model._active = None
    await stream.aclose()


async def test_stop_active_discards_late_deltas_and_maps_cancelled_run_to_cancellation(
    client: FakeClient,
) -> None:
    client.runs = [[
        RunEvent("message.delta", {"run_id": "run_1", "delta": "must not be spoken"}),
        RunEvent("run.cancelled", {"run_id": "run_1", "status": "cancelled"}),
    ]]
    client.statuses["run_1"] = [{"status": "cancelled"}]
    gate = asyncio.Event()
    original_events = client.events

    async def gated_events(run_id: str) -> AsyncIterator[RunEvent]:
        await gate.wait()
        async for event in original_events(run_id):
            yield event

    client.events = gated_events  # type: ignore[method-assign]
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "one")))
    task = asyncio.create_task(collect(stream))
    await client.started.wait()

    assert await model.stop_active() is True
    gate.set()
    assert await task == []
    assert stream._task.cancelled()


async def test_response_size_is_bounded_and_run_is_stopped(client: FakeClient) -> None:
    client.runs = [
        [
            RunEvent("message.delta", {"delta": "abc"}),
            RunEvent("message.delta", {"delta": "def"}),
        ]
    ]
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock(), max_response_chars=5)

    with pytest.raises(APIError, match="response limit"):
        await collect(model.chat(chat_ctx=context(("user", "one")), conn_options=options()))

    assert client.stopped == ["run_1"]


async def test_stale_run_events_and_non_text_payloads_are_never_spoken(client: FakeClient) -> None:
    client.runs = [
        [
            RunEvent("message.delta", {"run_id": "retired", "delta": "LEAK"}),
            RunEvent("message.delta", {"run_id": "run_1", "delta": {"reasoning": "SECRET"}}),
            RunEvent("tool.output", {"run_id": "run_1", "text": "SECRET"}),
            RunEvent("message.delta", {"run_id": "run_1", "delta": "safe"}),
            RunEvent("run.completed", {"run_id": "run_1", "status": "completed"}),
        ]
    ]
    model = HermesLLM(client=client, on_approval=AsyncMock())

    chunks = await collect(model.chat(chat_ctx=context(("user", "one"))))

    assert [chunk.delta.content for chunk in chunks if chunk.delta] == ["safe"]


async def test_only_bounded_authoritative_tool_status_is_forwarded(client: FakeClient) -> None:
    on_tool_status = AsyncMock()
    client.runs = [[
        RunEvent(
            "tool.started",
            {"run_id": "run_1", "tool": "read_file", "preview": "fixture.txt", "args": "SECRET"},
        ),
        RunEvent(
            "tool.completed",
            {
                "run_id": "run_1",
                "tool": "read_file",
                "preview": "HERMES-LIVE-FIXTURE-7F31",
                "duration": 0.125,
                "error": False,
                "result": "SECRET",
            },
        ),
        RunEvent("tool.failed", {"run_id": "run_1", "result": "SECRET"}),
        RunEvent("run.completed", {"run_id": "run_1", "status": "completed"}),
    ]]
    model = HermesLLM(
        client=client,
        on_approval=AsyncMock(),
        on_tool_status=on_tool_status,
    )

    await collect(model.chat(chat_ctx=context(("user", "one"))))

    assert on_tool_status.await_args_list == [
        ((ToolStatus("run_1", "run_1:1", "started", "read_file", "fixture.txt"),), {}),
        (
            (
                ToolStatus(
                    "run_1",
                    "run_1:2",
                    "completed",
                    "read_file",
                    "HERMES-LIVE-FIXTURE-7F31",
                    0.125,
                    False,
                ),
            ),
            {},
        ),
    ]


async def test_sse_eof_is_reconciled_with_one_status_poll(client: FakeClient) -> None:
    client.runs = [[]]
    client.statuses["run_1"] = [{"status": "completed"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())

    assert await collect(model.chat(chat_ctx=context(("user", "one")))) == []
    assert client.status_calls == ["run_1"]


@pytest.mark.parametrize("terminal", ["failed", "cancelled", "interrupted"])
async def test_nonlocal_terminal_failures_are_sanitized(client: FakeClient, terminal: str) -> None:
    client.runs = [
        [RunEvent(f"run.{terminal}", {"status": terminal, "error": "SECRET provider trace"})]
    ]
    model = HermesLLM(client=client, on_approval=AsyncMock())

    with pytest.raises(APIError) as caught:
        await collect(model.chat(chat_ctx=context(("user", "one"))))

    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.retryable is False


async def test_approval_is_forwarded_exactly_without_becoming_speech(client: FakeClient) -> None:
    on_approval = AsyncMock()
    on_resolved = AsyncMock()
    client.runs = [
        [
            RunEvent(
                "approval.request",
                {
                    "run_id": "run_1",
                    "request_id": "req_1",
                    "command": "rm redacted-file",
                    "choices": ["once", "deny"],
                },
            ),
            RunEvent("run.completed", {"run_id": "run_1", "status": "completed"}),
        ]
    ]
    model = HermesLLM(
        client=client, on_approval=on_approval, on_approval_resolved=on_resolved
    )

    assert await collect(model.chat(chat_ctx=context(("user", "one")))) == []
    on_approval.assert_awaited_once_with(
        ApprovalRequest("run_1", "req_1", "rm redacted-file", ("once", "deny"))
    )
    on_resolved.assert_awaited_once_with(ApprovalResolution("run_1", "req_1"))


async def test_callback_api_error_is_fully_sanitized(
    client: FakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    client.runs = [
        [
            RunEvent(
                "approval.request",
                {
                    "run_id": "run_1",
                    "request_id": "req_1",
                    "command": "redacted command",
                    "choices": ["once", "deny"],
                },
            )
        ]
    ]
    client.statuses["run_1"] = [{"status": "cancelled"}]

    async def unsafe_callback(_request: ApprovalRequest) -> None:
        try:
            raise RuntimeError("SECRET callback context")
        except RuntimeError as cause:
            raise APIError("SECRET callback detail", retryable=True) from cause

    model = HermesLLM(client=client, on_approval=unsafe_callback)

    with pytest.raises(APIError) as caught:
        await collect(model.chat(chat_ctx=context(("user", "one"))))

    formatted = "".join(traceback.format_exception(caught.value))
    assert str(caught.value) == "Hermes run failed."
    assert "SECRET" not in formatted
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__
    assert caught.value.retryable is False
    assert "SECRET" not in caplog.text
    assert len(client.starts) == 1
    assert client.stopped == ["run_1"]


async def test_approval_response_requires_current_exact_request_and_choice(
    client: FakeClient,
) -> None:
    approval_seen = asyncio.Event()
    continue_events = asyncio.Event()

    async def events(_run_id: str) -> AsyncIterator[RunEvent]:
        yield RunEvent(
            "approval.request",
            {
                "run_id": "run_1",
                "request_id": "req_1",
                "command": "safe summary",
                "choices": ["once", "deny"],
            },
        )
        approval_seen.set()
        await continue_events.wait()
        yield RunEvent(
            "approval.responded", {"run_id": "run_1", "request_id": "req_1", "choice": "once"}
        )
        yield RunEvent("run.completed", {"run_id": "run_1", "status": "completed"})

    client.events = events  # type: ignore[method-assign]
    on_resolved = AsyncMock()
    model = HermesLLM(
        client=client, on_approval=AsyncMock(), on_approval_resolved=on_resolved
    )
    stream = model.chat(chat_ctx=context(("user", "one")))
    task = asyncio.create_task(collect(stream))
    await approval_seen.wait()

    with pytest.raises(APIError, match="approval request"):
        await model.respond_to_approval("run_1", "wrong", "once")
    with pytest.raises(APIError, match="approval request"):
        await model.respond_to_approval("wrong-run", "req_1", "once")
    with pytest.raises(APIError, match="approval choice"):
        await model.respond_to_approval("run_1", "req_1", "always")
    await model.respond_to_approval("run_1", "req_1", "once")
    with pytest.raises(APIError, match="already being processed"):
        await model.respond_to_approval("run_1", "req_1", "once")
    assert client.approvals == [("run_1", "req_1", "once")]
    on_resolved.assert_not_awaited()
    continue_events.set()
    await task
    on_resolved.assert_awaited_once_with(ApprovalResolution("run_1", "req_1"))
    with pytest.raises(APIError, match="approval request"):
        await model.respond_to_approval("run_1", "req_1", "once")


async def test_failed_approval_response_can_be_retried(client: FakeClient) -> None:
    approval_seen = asyncio.Event()
    continue_events = asyncio.Event()

    async def events(_run_id: str) -> AsyncIterator[RunEvent]:
        yield RunEvent(
            "approval.request",
            {
                "run_id": "run_1",
                "request_id": "req_1",
                "command": "safe summary",
                "choices": ["once", "deny"],
            },
        )
        approval_seen.set()
        await continue_events.wait()
        yield RunEvent("run.completed", {"run_id": "run_1", "status": "completed"})

    attempts: list[tuple[str, str, str]] = []

    async def approve(run_id: str, request_id: str, choice: str) -> None:
        attempts.append((run_id, request_id, choice))
        if len(attempts) == 1:
            raise RuntimeError("temporary upstream failure")

    client.events = events  # type: ignore[method-assign]
    client.approve = approve  # type: ignore[method-assign]
    on_resolved = AsyncMock()
    model = HermesLLM(
        client=client, on_approval=AsyncMock(), on_approval_resolved=on_resolved
    )
    task = asyncio.create_task(collect(model.chat(chat_ctx=context(("user", "one")))))
    await approval_seen.wait()

    with pytest.raises(APIError, match="approval response failed"):
        await model.respond_to_approval("run_1", "req_1", "once")
    on_resolved.assert_not_awaited()

    await model.respond_to_approval("run_1", "req_1", "once")
    assert attempts == [("run_1", "req_1", "once"), ("run_1", "req_1", "once")]
    on_resolved.assert_not_awaited()

    continue_events.set()
    await task
    on_resolved.assert_awaited_once()


async def test_terminal_cleanup_resolves_failed_approval_once(client: FakeClient) -> None:
    approval_seen = asyncio.Event()
    send_terminal = asyncio.Event()

    async def events(_run_id: str) -> AsyncIterator[RunEvent]:
        yield RunEvent(
            "approval.request",
            {
                "run_id": "run_1",
                "request_id": "req_1",
                "command": "safe summary",
                "choices": ["once", "deny"],
            },
        )
        approval_seen.set()
        await send_terminal.wait()
        yield RunEvent("run.completed", {"run_id": "run_1", "status": "completed"})

    async def failing_approve(_run_id: str, _request_id: str, _choice: str) -> None:
        raise RuntimeError("temporary upstream failure")

    client.events = events  # type: ignore[method-assign]
    client.approve = failing_approve  # type: ignore[method-assign]
    on_resolved = AsyncMock()
    model = HermesLLM(
        client=client, on_approval=AsyncMock(), on_approval_resolved=on_resolved
    )
    task = asyncio.create_task(collect(model.chat(chat_ctx=context(("user", "one")))))
    await approval_seen.wait()

    with pytest.raises(APIError, match="approval response failed"):
        await model.respond_to_approval("run_1", "req_1", "once")
    on_resolved.assert_not_awaited()

    send_terminal.set()
    await task
    on_resolved.assert_awaited_once_with(ApprovalResolution("run_1", "req_1"))
    with pytest.raises(APIError, match="approval request"):
        await model.respond_to_approval("run_1", "req_1", "once")


async def test_simultaneous_approval_responses_reach_hermes_once(client: FakeClient) -> None:
    approval_seen = asyncio.Event()
    continue_events = asyncio.Event()
    approve_started = asyncio.Event()
    finish_approve = asyncio.Event()
    attempts: list[tuple[str, str, str]] = []

    async def events(_run_id: str) -> AsyncIterator[RunEvent]:
        yield RunEvent(
            "approval.request",
            {
                "run_id": "run_1",
                "request_id": "req_1",
                "command": "safe summary",
                "choices": ["once", "deny"],
            },
        )
        approval_seen.set()
        await continue_events.wait()
        yield RunEvent("run.completed", {"run_id": "run_1", "status": "completed"})

    async def blocking_approve(run_id: str, request_id: str, choice: str) -> None:
        attempts.append((run_id, request_id, choice))
        approve_started.set()
        await finish_approve.wait()

    client.events = events  # type: ignore[method-assign]
    client.approve = blocking_approve  # type: ignore[method-assign]
    on_resolved = AsyncMock()
    model = HermesLLM(
        client=client, on_approval=AsyncMock(), on_approval_resolved=on_resolved
    )
    stream_task = asyncio.create_task(collect(model.chat(chat_ctx=context(("user", "one")))))
    await approval_seen.wait()

    first_response = asyncio.create_task(
        model.respond_to_approval("run_1", "req_1", "once")
    )
    await approve_started.wait()
    with pytest.raises(APIError, match="already being processed"):
        await model.respond_to_approval("run_1", "req_1", "once")
    assert attempts == [("run_1", "req_1", "once")]

    finish_approve.set()
    await first_response
    on_resolved.assert_not_awaited()
    continue_events.set()
    await stream_task
    on_resolved.assert_awaited_once_with(ApprovalResolution("run_1", "req_1"))


async def test_failed_resolution_publication_retains_request_for_terminal_retry(
    client: FakeClient,
) -> None:
    approval_seen = asyncio.Event()
    emit_responded = asyncio.Event()

    async def events(_run_id: str) -> AsyncIterator[RunEvent]:
        yield RunEvent(
            "approval.request",
            {
                "run_id": "run_1",
                "request_id": "req_1",
                "command": "safe",
                "choices": ["deny"],
            },
        )
        approval_seen.set()
        await emit_responded.wait()
        yield RunEvent("approval.responded", {"run_id": "run_1", "request_id": "req_1"})

    resolutions = AsyncMock(side_effect=[RuntimeError("publish failed"), None])
    client.events = events  # type: ignore[method-assign]
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(
        client=client,
        on_approval=AsyncMock(),
        on_approval_resolved=resolutions,
    )
    task = asyncio.create_task(collect(model.chat(chat_ctx=context(("user", "one")))))
    await approval_seen.wait()
    await model.respond_to_approval("run_1", "req_1", "deny")
    emit_responded.set()

    with pytest.raises(APIError, match="resolution"):
        await task

    assert resolutions.await_count == 2


async def test_duplicate_approval_request_id_fails_closed(client: FakeClient) -> None:
    request = RunEvent(
        "approval.request",
        {
            "run_id": "run_1",
            "request_id": "req_1",
            "command": "redacted command",
            "choices": ["once", "deny"],
        },
    )
    client.runs = [[request, request]]
    client.statuses["run_1"] = [{"status": "cancelled"}]
    on_approval = AsyncMock()
    model = HermesLLM(client=client, on_approval=on_approval)

    with pytest.raises(APIError, match="reused"):
        await collect(model.chat(chat_ctx=context(("user", "one"))))

    on_approval.assert_awaited_once()
    assert client.stopped == ["run_1"]


async def test_malformed_approval_is_sanitized_and_stops_run(client: FakeClient) -> None:
    client.runs = [
        [RunEvent("approval.request", {"request_id": "req_1", "command": "SECRET", "choices": []})]
    ]
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())

    with pytest.raises(APIError) as caught:
        await collect(model.chat(chat_ctx=context(("user", "one"))))

    assert "SECRET" not in str(caught.value)
    assert client.stopped == ["run_1"]


async def test_transport_errors_are_sanitized(client: FakeClient) -> None:
    async def broken(_run_id: str) -> AsyncIterator[RunEvent]:
        if False:
            yield RunEvent("unused", {})
        raise HermesAPIError("SECRET upstream response")

    client.events = broken  # type: ignore[method-assign]
    client.statuses["run_1"] = [{"status": "cancelled"}]
    model = HermesLLM(client=client, on_approval=AsyncMock())

    with pytest.raises(APIError) as caught:
        await collect(model.chat(chat_ctx=context(("user", "one"))))

    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None
