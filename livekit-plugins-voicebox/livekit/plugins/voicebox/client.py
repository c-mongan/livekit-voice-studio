from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Literal
from urllib.parse import urlsplit

import aiohttp

from .errors import (
    AmbiguousProfileError,
    BackendUncertainError,
    ClientClosedError,
    ConfigurationError,
    ModelNotReadyError,
    ProfileNotFoundError,
    ResponseLimitError,
    VoiceboxAPIError,
    VoiceboxConnectionError,
    VoiceboxError,
    VoiceboxTimeoutError,
)
from .models import (
    MAX_JSON_BYTES,
    MAX_WAV_BYTES,
    GenerationOptions,
    HealthResult,
    ModelReadiness,
    RequestTimings,
    VoiceProfile,
    validate_text,
)

logger = logging.getLogger(__name__)
BackendState = Literal["idle", "active", "draining", "uncertain", "closed"]


def positive_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Timeouts must be finite positive seconds.")
    return value


class VoiceboxClient:
    """One event-loop-local client, requiring exclusive use of the backend.

    A submitted job, not its consumer, owns the lock. Disconnects and ambiguous
    errors permanently close admission. Recreate only after confirmed backend stop.
    """

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:17493",
        request_timeout: float = 60.0,
        drain_timeout: float = 120.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        origin = urlsplit(base_url)
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path.rstrip("/")
        ):
            raise ValueError("base_url must be an HTTP(S) origin without credentials or path.")
        self.base_url = base_url.rstrip("/")
        self.request_timeout = positive_timeout(request_timeout)
        self.drain_timeout = positive_timeout(drain_timeout)
        self._session = session
        self._owns_session = session is None
        self._state: BackendState = "idle"
        self._uncertain = False
        self._closing = False
        self._lock = asyncio.Lock()
        self._job: asyncio.Task[bytes] | None = None
        self._deadline: asyncio.Timeout | None = None
        self._close_task: asyncio.Task[None] | None = None

    @property
    def state(self) -> BackendState:
        return self._state

    def _check_open(self) -> None:
        if self._closing or self._state == "closed":
            raise ClientClosedError("Voicebox client is closed; create a new client.")
        if self._uncertain:
            raise BackendUncertainError()

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(trust_env=False)
        if self._session.closed:
            raise ClientClosedError("The supplied Voicebox HTTP session is closed.")
        return self._session

    @staticmethod
    def _status_error(status: int, route: str) -> VoiceboxAPIError:
        # Deliberately discard arbitrary detail/validation bodies: they may echo text.
        if status in (409, 503):
            return ModelNotReadyError(
                "Voicebox cannot serve the selected model. Inspect local model readiness; "
                "the plugin will not download models.",
                status_code=status,
            )
        message = f"Voicebox {route} returned HTTP {status}."
        if status == 404:
            message += " Check API compatibility and refresh the selected profile."
        elif status in (400, 422):
            message += " Check profile, engine, model and language configuration."
        return VoiceboxAPIError(message, status_code=status)

    async def _read(self, response: aiohttp.ClientResponse, limit: int) -> bytes:
        if response.content_length is not None and response.content_length > limit:
            raise ResponseLimitError("Voicebox response exceeds the configured byte limit.")
        result = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            if len(result) + len(chunk) > limit:
                raise ResponseLimitError("Voicebox response exceeds the configured byte limit.")
            result.extend(chunk)
        return bytes(result)

    async def _get_json(self, route: str) -> object:
        self._check_open()
        try:
            async with self._ensure_session().get(
                self.base_url + route,
                timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                allow_redirects=False,
                auto_decompress=False,
            ) as response:
                if not 200 <= response.status < 300:
                    raise self._status_error(response.status, route)
                body = await self._read(response, MAX_JSON_BYTES)
                try:
                    return json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    raise VoiceboxAPIError("Voicebox returned invalid JSON.") from None
        except TimeoutError:
            raise VoiceboxTimeoutError("Voicebox discovery timed out.") from None
        except aiohttp.ClientError:
            raise VoiceboxConnectionError(
                "Could not connect to Voicebox. Start the existing server or check base_url."
            ) from None

    async def health(self) -> HealthResult:
        return HealthResult.parse(await self._get_json("/health"))

    async def list_profiles(self) -> list[VoiceProfile]:
        value = await self._get_json("/profiles")
        if not isinstance(value, list):
            raise VoiceboxAPIError("Voicebox /profiles must return a list.")
        return [VoiceProfile.parse(profile) for profile in value]

    async def resolve_profile(self, value: str) -> VoiceProfile:
        profiles = await self.list_profiles()
        for matches in (
            [p for p in profiles if p.id == value],
            [p for p in profiles if p.name == value],
            [p for p in profiles if p.name.casefold() == value.casefold()],
        ):
            if len(matches) > 1:
                raise AmbiguousProfileError(
                    "Profile selector is ambiguous. Use a unique profile ID."
                )
            if matches:
                return matches[0]
        raise ProfileNotFoundError(
            "Profile not found. Use list_profiles() and select an exact ID/name."
        )

    async def model_readiness(self) -> ModelReadiness:
        value = await self._get_json("/models/status")
        if not isinstance(value, dict) or not isinstance(value.get("models"), list):
            raise VoiceboxAPIError("Voicebox /models/status must return a models list.")
        models = [ModelReadiness.parse(model) for model in value["models"]]
        matches = [m for m in models if m.model_name == "qwen-tts-0.6B"]
        if len(matches) != 1:
            raise ModelNotReadyError("Voicebox does not report exactly one Qwen TTS 0.6B model.")
        return matches[0]

    async def check_idle(self) -> None:
        """Reject tracked work; this cannot detect all /generate/stream clients."""
        value = await self._get_json("/tasks/active")
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("generations"), list)
            or not isinstance(value.get("downloads"), list)
        ):
            raise VoiceboxAPIError("Voicebox /tasks/active has an incompatible schema.")
        if value["generations"] or value["downloads"]:
            raise ConfigurationError(
                "Voicebox has active work. Wait for it to finish; do not overlap."
            )

    async def synthesize_wav(
        self,
        *,
        profile_id: str,
        text: str,
        engine: str | None = "qwen",
        model_size: str | None = "0.6B",
        language: str = "en",
        instruct: str | None = None,
        timings: RequestTimings | None = None,
        connect_timeout: float = 10.0,
    ) -> bytes:
        validate_text(text)
        options = GenerationOptions(profile_id, engine, model_size, language, instruct)
        options.validate()
        positive_timeout(connect_timeout)
        if engine is None or model_size is None:
            raise ValueError("Resolve effective engine/model before calling synthesize_wav.")
        observation = timings if timings is not None else RequestTimings()
        queued = time.perf_counter()
        job: asyncio.Task[bytes] | None = None
        try:
            async with asyncio.timeout(self.request_timeout):
                self._check_open()
                await self._lock.acquire()
                try:
                    self._check_open()
                    self._ensure_session()
                    observation.queue_wait = time.perf_counter() - queued
                    payload = {
                        "profile_id": profile_id,
                        "text": text,
                        "engine": engine,
                        "model_size": model_size,
                        "language": language,
                    }
                    if instruct is not None:
                        payload["instruct"] = instruct
                    self._state = "active"
                    job = asyncio.create_task(self._run_job(payload, observation, connect_timeout))
                    self._job = job
                    job.add_done_callback(self._observe_job)
                except BaseException:
                    # No job owns the lock yet; cancellation remains unmodified.
                    self._lock.release()
                    raise
                return await asyncio.shield(job)
        except asyncio.CancelledError:
            if job is not None:
                self._detach(job)
            raise
        except TimeoutError:
            if job is not None:
                self._detach(job)
            raise VoiceboxTimeoutError("Voicebox foreground synthesis deadline exceeded.") from None

    def _detach(self, job: asyncio.Task[bytes]) -> None:
        if job.done() or self._uncertain:
            return
        self._state = "draining"
        if self._deadline is not None and not self._deadline.expired():
            current = self._deadline.when()
            bound = asyncio.get_running_loop().time() + self.drain_timeout
            self._deadline.reschedule(min(current, bound) if current is not None else bound)

    def _observe_job(self, job: asyncio.Task[bytes]) -> None:
        if not job.cancelled() and job.exception() is not None:
            logger.warning("Voicebox owned request failed; backend state=%s", self._state)

    async def _run_job(
        self, payload: dict[str, str], timings: RequestTimings, connect_timeout: float
    ) -> bytes:
        known_complete = False
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self.request_timeout + self.drain_timeout) as deadline:
                self._deadline = deadline
                if self._state == "draining":
                    deadline.reschedule(asyncio.get_running_loop().time() + self.drain_timeout)
                async with self._ensure_session().post(
                    self.base_url + "/generate/stream",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=connect_timeout),
                    allow_redirects=False,
                    auto_decompress=False,
                ) as response:
                    if not 200 <= response.status < 300:
                        # Reviewed route rejects these before inference; never replay them.
                        known_complete = response.status in (400, 404, 422)
                        raise self._status_error(response.status, "/generate/stream")
                    if (
                        response.content_length is not None
                        and response.content_length > MAX_WAV_BYTES
                    ):
                        raise ResponseLimitError("Voicebox WAV exceeds 16 MiB.")
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        if timings.http_first_byte is None:
                            timings.http_first_byte = time.perf_counter() - started
                        if len(body) + len(chunk) > MAX_WAV_BYTES:
                            raise ResponseLimitError("Voicebox WAV exceeds 16 MiB.")
                        body.extend(chunk)
                    timings.http_complete = time.perf_counter() - started
                    known_complete = True
                    return bytes(body)
        except TimeoutError:
            raise VoiceboxTimeoutError("Voicebox owned request/drain deadline exceeded.") from None
        except aiohttp.ClientError:
            raise VoiceboxConnectionError(
                "Voicebox transport failed after possible submission; do not replay."
            ) from None
        finally:
            self._deadline = None
            if not known_complete:
                self._uncertain = True
            self._state = "uncertain" if self._uncertain else "idle"
            self._lock.release()

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
            self._close_task.add_done_callback(self._observe_close)
        await asyncio.shield(self._close_task)

    @staticmethod
    def _observe_close(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "Voicebox closed with unresolved backend work; backend stop is required."
            )

    async def _close(self) -> None:
        try:
            if self._job is not None:
                self._detach(self._job)
                try:
                    await asyncio.shield(self._job)
                except VoiceboxError:
                    # State carries unresolved work; report it after resources are closed.
                    pass
        finally:
            if self._owns_session and self._session is not None:
                await self._session.close()
            self._state = "closed"
        if self._uncertain:
            raise BackendUncertainError()
