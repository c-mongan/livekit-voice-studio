"""Bounded asynchronous client for Hermes' native Runs API."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

MAX_JSON_BYTES = 1024 * 1024
MAX_SSE_FRAME_BYTES = 1024 * 1024
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_APPROVAL_CHOICES = frozenset({"once", "session", "always", "deny"})
_REQUIRED_FEATURES = frozenset(
    {"run_submission", "run_status", "run_events_sse", "run_approval_response", "run_stop"}
)
_REQUIRED_ENDPOINTS: dict[str, tuple[str, str]] = {
    "runs": ("POST", "/v1/runs"),
    "run_status": ("GET", "/v1/runs/{run_id}"),
    "run_events": ("GET", "/v1/runs/{run_id}/events"),
    "run_approval": ("POST", "/v1/runs/{run_id}/approval"),
    "run_stop": ("POST", "/v1/runs/{run_id}/stop"),
}


class HermesAPIError(Exception):
    """A sanitized Hermes transport or protocol error."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class HermesConfig:
    base_url: str
    api_key: str
    profile: str | None
    session_id: str
    request_timeout: float = 120.0
    stop_timeout: float = 10.0

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("base_url must be a valid HTTP(S) origin.") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/")
        ):
            raise ValueError("base_url must be an HTTP(S) origin without credentials or path.")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Remote Hermes origins require HTTPS.")
        if not self.api_key.strip():
            raise ValueError("api_key must not be blank.")
        if not self.session_id or len(self.session_id) > 255:
            raise ValueError("session_id must contain 1-255 characters.")
        for value in (self.request_timeout, self.stop_timeout):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts must be finite positive seconds.")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def route(self, path: str) -> str:
        prefix = f"/p/{quote(self.profile, safe='')}" if self.profile else ""
        return f"{self.base_url}{prefix}{path}"


@dataclass(frozen=True)
class RunHandle:
    run_id: str


@dataclass(frozen=True)
class RunEvent:
    type: str
    payload: dict[str, object]


class HermesRunsClient:
    """One event-loop-local, credential-bearing Hermes Runs client."""

    def __init__(self, config: HermesConfig) -> None:
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise HermesAPIError("Hermes Runs client is closed; create a new client.")
        if self._session is None:
            self._session = aiohttp.ClientSession(trust_env=False)
        return self._session

    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _status_error(status: int, route: str) -> HermesAPIError:
        return HermesAPIError(
            f"Hermes {route} returned HTTP {status}.",
            status_code=status,
        )

    @staticmethod
    async def _read_json(response: aiohttp.ClientResponse) -> dict[str, object]:
        if response.content_length is not None and response.content_length > MAX_JSON_BYTES:
            raise HermesAPIError("Hermes JSON response exceeds 1 MiB.")
        body = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            if len(body) + len(chunk) > MAX_JSON_BYTES:
                raise HermesAPIError("Hermes JSON response exceeds 1 MiB.")
            body.extend(chunk)
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            raise HermesAPIError("Hermes returned invalid JSON.") from None
        if not isinstance(value, dict):
            raise HermesAPIError("Hermes returned a non-object JSON response.")
        return value

    async def _request_json(
        self,
        method: str,
        route: str,
        *,
        expected_status: int = 200,
        json_body: Mapping[str, object] | None = None,
        extra_headers: Mapping[str, str] | None = None,
        request_timeout: float | None = None,
    ) -> dict[str, object]:
        try:
            async with self._ensure_session().request(
                method,
                self.config.route(route),
                json=json_body,
                headers=self._headers(extra_headers),
                timeout=aiohttp.ClientTimeout(
                    total=request_timeout or self.config.request_timeout
                ),
                allow_redirects=False,
                auto_decompress=False,
            ) as response:
                if response.status != expected_status:
                    raise self._status_error(response.status, route)
                return await self._read_json(response)
        except TimeoutError:
            raise HermesAPIError(f"Hermes {route} timed out.") from None
        except aiohttp.ClientError:
            raise HermesAPIError(f"Hermes {route} transport failed.") from None

    async def preflight(self) -> None:
        value = await self._request_json("GET", "/v1/capabilities")
        features = value.get("features")
        endpoints = value.get("endpoints")
        if not isinstance(features, dict) or not isinstance(endpoints, dict):
            raise HermesAPIError(
                "Hermes capabilities are incompatible with the required Runs API."
            )
        if not all(features.get(name) is True for name in _REQUIRED_FEATURES):
            raise HermesAPIError(
                "Hermes capabilities are incompatible with the required Runs API."
            )
        for name, (method, path) in _REQUIRED_ENDPOINTS.items():
            endpoint = endpoints.get(name)
            if (
                not isinstance(endpoint, dict)
                or endpoint.get("method") != method
                or endpoint.get("path") != path
            ):
                raise HermesAPIError(
                    "Hermes capabilities are incompatible with the required Runs API."
                )

    async def start(self, text: str, *, idempotency_key: str) -> RunHandle:
        if (
            not idempotency_key
            or len(idempotency_key) > 255
            or any(ord(character) < 33 or ord(character) > 126 for character in idempotency_key)
        ):
            raise ValueError("idempotency_key must contain 1-255 visible ASCII characters.")
        value = await self._request_json(
            "POST",
            "/v1/runs",
            expected_status=202,
            json_body={"input": text, "session_id": self.config.session_id},
            extra_headers={"Idempotency-Key": idempotency_key},
        )
        run_id = value.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise HermesAPIError("Hermes start response did not contain a valid run_id.")
        return RunHandle(run_id)

    async def events(self, run_id: str) -> AsyncIterator[RunEvent]:
        route = f"/v1/runs/{quote(run_id, safe='')}/events"
        terminal_seen = False
        try:
            async with self._ensure_session().get(
                self.config.route(route),
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=self.config.request_timeout),
                allow_redirects=False,
                auto_decompress=False,
            ) as response:
                if response.status != 200:
                    raise self._status_error(response.status, route)
                buffer = bytearray()
                data_lines: list[bytes] = []
                frame_bytes = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    buffer.extend(chunk)
                    if len(buffer) > MAX_SSE_FRAME_BYTES and b"\n" not in buffer:
                        raise HermesAPIError("Hermes SSE event line exceeds 1 MiB.")
                    while (newline := buffer.find(b"\n")) >= 0:
                        line = bytes(buffer[:newline])
                        del buffer[: newline + 1]
                        if line.endswith(b"\r"):
                            line = line[:-1]
                        if len(line) > MAX_SSE_FRAME_BYTES:
                            raise HermesAPIError("Hermes SSE event line exceeds 1 MiB.")
                        if not line:
                            if data_lines:
                                event = self._parse_sse_event(data_lines)
                                terminal_seen = terminal_seen or event.type in {
                                    "run.completed",
                                    "run.failed",
                                    "run.cancelled",
                                    "run.interrupted",
                                }
                                yield event
                            data_lines = []
                            frame_bytes = 0
                            continue
                        if line.startswith(b":") or not line.startswith(b"data:"):
                            continue
                        data = line[5:]
                        if data.startswith(b" "):
                            data = data[1:]
                        frame_bytes += len(data)
                        if frame_bytes > MAX_SSE_FRAME_BYTES:
                            raise HermesAPIError("Hermes SSE event frame exceeds 1 MiB.")
                        data_lines.append(data)
                if buffer:
                    if len(buffer) > MAX_SSE_FRAME_BYTES:
                        raise HermesAPIError("Hermes SSE event line exceeds 1 MiB.")
                    if buffer.startswith(b"data:"):
                        data = bytes(buffer[5:])
                        if data.startswith(b" "):
                            data = data[1:]
                        data_lines.append(data)
                if data_lines:
                    event = self._parse_sse_event(data_lines)
                    terminal_seen = terminal_seen or event.type in {
                        "run.completed",
                        "run.failed",
                        "run.cancelled",
                        "run.interrupted",
                    }
                    yield event
        except TimeoutError:
            raise HermesAPIError(f"Hermes {route} timed out.") from None
        except aiohttp.ClientError:
            raise HermesAPIError(f"Hermes {route} transport failed.") from None

        if terminal_seen:
            return
        status = await self.status(run_id)
        state = status.get("status")
        if isinstance(state, str) and state in _TERMINAL_STATUSES:
            yield RunEvent(f"run.{state}", status)
            return
        raise HermesAPIError("Hermes SSE stream ended before the run reached a terminal status.")

    @staticmethod
    def _parse_sse_event(data_lines: list[bytes]) -> RunEvent:
        try:
            value: Any = json.loads(b"\n".join(data_lines))
        except (UnicodeDecodeError, ValueError):
            raise HermesAPIError("Hermes returned an invalid SSE event.") from None
        if not isinstance(value, dict):
            raise HermesAPIError("Hermes returned an invalid SSE event object.")
        event_type = value.pop("event", None)
        if not isinstance(event_type, str) or not event_type:
            raise HermesAPIError("Hermes returned an invalid SSE event type.")
        return RunEvent(event_type, value)

    async def status(self, run_id: str) -> dict[str, object]:
        route = f"/v1/runs/{quote(run_id, safe='')}"
        return await self._request_json("GET", route)

    async def stop(self, run_id: str) -> dict[str, object]:
        route = f"/v1/runs/{quote(run_id, safe='')}/stop"
        return await self._request_json(
            "POST",
            route,
            json_body={},
            request_timeout=self.config.stop_timeout,
        )

    async def approve(self, run_id: str, request_id: str, choice: str) -> None:
        if choice not in _APPROVAL_CHOICES:
            raise ValueError("choice must be once, session, always, or deny.")
        if not request_id or len(request_id) > 256:
            raise ValueError("request_id must contain 1-256 characters.")
        route = f"/v1/runs/{quote(run_id, safe='')}/approval"
        await self._request_json(
            "POST",
            route,
            json_body={"request_id": request_id, "choice": choice},
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._session is not None:
            await self._session.close()
