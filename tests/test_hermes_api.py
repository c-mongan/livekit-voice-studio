from typing import Any

import pytest
from aiohttp import web

from examples.hermes_api import (
    HermesAPIError,
    HermesConfig,
    HermesRunsClient,
    RunEvent,
)


def test_profile_routes_are_encoded_and_loopback_is_allowed() -> None:
    config = HermesConfig(
        base_url="http://127.0.0.1:8642/",
        api_key="test-key",
        profile="voice room/primary",
        session_id="voice-room-1",
    )

    assert config.route("/v1/runs") == "http://127.0.0.1:8642/p/voice%20room%2Fprimary/v1/runs"


def test_http_remote_origin_is_rejected() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HermesConfig("http://example.com", "key", None, "session")


@pytest.mark.parametrize("base_url", ["file:///tmp/hermes", "ftp://example.com", "not-a-url"])
def test_non_http_origins_are_rejected(base_url: str) -> None:
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        HermesConfig(base_url, "key", None, "session")


def test_blank_api_key_and_invalid_session_are_rejected() -> None:
    with pytest.raises(ValueError, match="api_key"):
        HermesConfig("https://example.com", "  ", None, "session")
    with pytest.raises(ValueError, match="session_id"):
        HermesConfig("https://example.com", "key", None, "x" * 256)


def capabilities() -> dict[str, object]:
    return {
        "features": {
            "run_submission": True,
            "run_status": True,
            "run_events_sse": True,
            "run_approval_response": True,
            "run_stop": True,
        },
        "endpoints": {
            "runs": {"method": "POST", "path": "/v1/runs"},
            "run_status": {"method": "GET", "path": "/v1/runs/{run_id}"},
            "run_events": {"method": "GET", "path": "/v1/runs/{run_id}/events"},
            "run_approval": {"method": "POST", "path": "/v1/runs/{run_id}/approval"},
            "run_stop": {"method": "POST", "path": "/v1/runs/{run_id}/stop"},
        },
    }


def make_client(base_url: str, *, profile: str | None = None) -> HermesRunsClient:
    return HermesRunsClient(HermesConfig(base_url, "test-key", profile, "voice-room-1"))


async def test_preflight_uses_configured_key_without_displaying_it(server) -> None:
    seen: dict[str, str] = {}

    async def handle(request: web.Request) -> web.Response:
        seen["path"] = request.path
        seen["authorization"] = request.headers["Authorization"]
        seen["accept"] = request.headers["Accept"]
        return web.json_response(capabilities())

    api_key = "actual-configured-secret"
    config = HermesConfig(await server(handle), api_key, "default", "voice-room-1")
    client = HermesRunsClient(config)
    try:
        await client.preflight()
    finally:
        await client.aclose()

    assert seen == {
        "path": "/p/default/v1/capabilities",
        "authorization": f"Bearer {api_key}",
        "accept": "application/json",
    }
    assert api_key not in repr(config)
    assert api_key not in repr(client)


async def test_preflight_rejects_incomplete_capabilities_without_body_details(server) -> None:
    async def handle(request: web.Request) -> web.Response:
        return web.json_response({"features": {}, "endpoints": {}, "secret": "do-not-expose"})

    client = make_client(await server(handle))
    try:
        with pytest.raises(HermesAPIError, match="capabilities") as caught:
            await client.preflight()
    finally:
        await client.aclose()

    assert "do-not-expose" not in str(caught.value)


async def test_start_sends_explicit_session_and_idempotency(server) -> None:
    seen: dict[str, Any] = {}

    async def response_handler(request: web.Request) -> web.Response:
        seen["body"] = await request.json()
        seen["headers"] = request.headers
        return web.json_response({"run_id": "run_1", "status": "started"}, status=202)

    client = make_client(await server(response_handler))
    try:
        run_handle = await client.start("hello", idempotency_key="room:turn:1")
    finally:
        await client.aclose()

    assert run_handle.run_id == "run_1"
    assert seen["body"] == {"input": "hello", "session_id": "voice-room-1"}
    assert seen["headers"]["Idempotency-Key"] == "room:turn:1"
    assert seen["headers"]["Authorization"] == "Bearer test-key"


async def test_start_rejects_non_202_without_exposing_response_body(server) -> None:
    async def handle(request: web.Request) -> web.Response:
        return web.Response(status=503, text="private provider failure")

    client = make_client(await server(handle))
    try:
        with pytest.raises(HermesAPIError, match="HTTP 503") as caught:
            await client.start("hello", idempotency_key="room:turn:1")
    finally:
        await client.aclose()

    assert "private provider failure" not in str(caught.value)


async def test_start_rejects_malformed_json(server) -> None:
    async def handle(request: web.Request) -> web.Response:
        return web.Response(status=202, text="not-json")

    client = make_client(await server(handle))
    try:
        with pytest.raises(HermesAPIError, match="invalid JSON"):
            await client.start("hello", idempotency_key="room:turn:1")
    finally:
        await client.aclose()


async def test_events_parse_only_data_frames_and_bound_payload(server) -> None:
    async def handle(request: web.Request) -> web.Response:
        return web.Response(
            text=(
                ": keepalive\n\n"
                "event: ignored-name\n"
                'data: {"event":"message.delta","delta":"Hi"}\n\n'
                "retry: 1000\n"
                'data: {"event":"run.completed","status":"completed"}\n\n'
            ),
            content_type="text/event-stream",
        )

    client = make_client(await server(handle))
    try:
        events = [event async for event in client.events("run_1")]
    finally:
        await client.aclose()

    assert events == [
        RunEvent("message.delta", {"delta": "Hi"}),
        RunEvent("run.completed", {"status": "completed"}),
    ]


async def test_events_reject_malformed_and_non_object_json(server) -> None:
    responses = iter(["data: not-json\n\n", "data: []\n\n"])

    async def handle(request: web.Request) -> web.Response:
        return web.Response(text=next(responses), content_type="text/event-stream")

    client = make_client(await server(handle))
    try:
        for _ in range(2):
            with pytest.raises(HermesAPIError, match="invalid SSE event"):
                async for _event in client.events("run_1"):
                    pass
    finally:
        await client.aclose()


async def test_events_reject_a_data_line_over_one_mib(server) -> None:
    async def handle(request: web.Request) -> web.Response:
        return web.Response(
            body=b"data: " + b"x" * (1024 * 1024 + 1) + b"\n\n",
            content_type="text/event-stream",
        )

    client = make_client(await server(handle))
    try:
        with pytest.raises(HermesAPIError, match="1 MiB"):
            async for _event in client.events("run_1"):
                pass
    finally:
        await client.aclose()


async def test_events_reject_aggregate_comment_frame_over_one_mib(server) -> None:
    max_frame_bytes = 1024 * 1024
    eof_data = b'data: {"event":"run.completed","status":"completed"}'
    first_comment = b": " + b"x" * 400_000 + b"\n"
    second_comment_size = max_frame_bytes - len(eof_data) + 1 - len(first_comment) - 3
    second_comment = b": " + b"y" * second_comment_size + b"\n"
    oversized_frame = first_comment + second_comment + eof_data
    assert len(oversized_frame) == max_frame_bytes + 1

    async def handle(request: web.Request) -> web.Response:
        return web.Response(body=oversized_frame, content_type="text/event-stream")

    client = make_client(await server(handle))
    try:
        with pytest.raises(HermesAPIError, match="frame exceeds 1 MiB"):
            async for _event in client.events("run_1"):
                pass
    finally:
        await client.aclose()


async def test_events_poll_terminal_status_after_sse_eof(server) -> None:
    calls: list[str] = []

    async def handle(request: web.Request) -> web.Response:
        calls.append(request.path)
        if request.path.endswith("/events"):
            return web.Response(text=": stream closed\n\n", content_type="text/event-stream")
        return web.json_response({"run_id": "run_1", "status": "failed", "error": "safe"})

    client = make_client(await server(handle))
    try:
        events = [event async for event in client.events("run_1")]
    finally:
        await client.aclose()

    assert events == [
        RunEvent("run.failed", {"run_id": "run_1", "status": "failed", "error": "safe"})
    ]
    assert calls == ["/v1/runs/run_1/events", "/v1/runs/run_1"]


async def test_status_and_stop_return_bounded_objects(server) -> None:
    async def handle(request: web.Request) -> web.Response:
        if request.path.endswith("/stop"):
            return web.json_response({"status": "stopping"})
        return web.json_response({"run_id": "run_1", "status": "running"})

    client = make_client(await server(handle))
    try:
        assert await client.status("run_1") == {"run_id": "run_1", "status": "running"}
        assert await client.stop("run_1") == {"status": "stopping"}
    finally:
        await client.aclose()


@pytest.mark.parametrize("choice", ["once", "session", "always", "deny"])
async def test_approve_sends_exact_request_id_and_allowed_choice(server, choice: str) -> None:
    seen: dict[str, object] = {}

    async def handle(request: web.Request) -> web.Response:
        seen.update(await request.json())
        return web.json_response({"resolved": 1})

    client = make_client(await server(handle))
    try:
        await client.approve("run_1", "request/exact", choice)
    finally:
        await client.aclose()

    assert seen == {"request_id": "request/exact", "choice": choice}


async def test_approve_rejects_unknown_choice_before_request(server) -> None:
    called = False

    async def handle(request: web.Request) -> web.Response:
        nonlocal called
        called = True
        return web.json_response({})

    client = make_client(await server(handle))
    try:
        with pytest.raises(ValueError, match="choice"):
            await client.approve("run_1", "request_1", "approve")
    finally:
        await client.aclose()

    assert called is False


async def test_close_is_idempotent_and_prevents_more_requests(server) -> None:
    async def handle(request: web.Request) -> web.Response:
        return web.json_response({})

    client = make_client(await server(handle))
    await client.aclose()
    await client.aclose()

    with pytest.raises(HermesAPIError, match="closed"):
        await client.status("run_1")
