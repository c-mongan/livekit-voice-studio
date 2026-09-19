"""Loopback-only Voicebox Studio: static UI, scoped tokens and one owned agent."""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import contextlib
import datetime
import importlib.util
import io
import ipaddress
import json
import logging
import math
import os
import platform
import re
import secrets
import shutil
import signal
import sys
import time
import wave
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from livekit import api
from livekit.plugins.voicebox.errors import VoiceboxError

from examples.backend_lease import BackendLease, runtime_root
from examples.component_endpoints import (
    DEFAULT_LLM_URL,
    ENDPOINT_PROVIDERS,
    check_llm_endpoint,
    is_loopback_url,
)
from examples.fast_qwen import FastQwenTTS
from examples.minimal_agent import check_setup, configured_provider, provider_choices
from examples.nemotron_service import NemotronService
from examples.startup_progress import STARTUP_MESSAGES
from examples.studio_library import GUIDED_TEXT, MAX_RECORDING_BYTES, LibraryError, StudioLibrary
from examples.studio_setup import SetupChecks, SetupUnavailable

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("voicebox.studio")
HEARTBEAT_SECONDS = 45.0
SHUTDOWN_SECONDS = 150.0
MAX_SESSION_SECONDS = 3600.0
STARTUP_SECONDS = 60.0
AUDITION_SECONDS = 90.0
AUDITION_RESULT_SECONDS = 60.0
MAX_AUDITION_PCM = 24000 * 2 * 30


class StudioError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class OwnedSession:
    id: str
    room: str
    participant: str
    agent: str
    key: str
    created: float = field(default_factory=time.monotonic)
    heartbeat: float = field(default_factory=time.monotonic)
    process: asyncio.subprocess.Process | None = None
    monitor: asyncio.Task[None] | None = None
    finished: bool = False
    safe: bool = False
    end_task: asyncio.Task[None] | None = None
    kind: str = "room"
    voice_id: str | None = None
    pcm: bytearray = field(default_factory=bytearray)
    audition_state: str = "generating"
    completed: float | None = None
    audition_message: str | None = None
    stop_ready: asyncio.Event = field(default_factory=asyncio.Event)
    hold_playback: bool = False
    playback_admitted: bool = False


class Studio:
    def __init__(self, lease: BackendLease) -> None:
        self.lease = lease
        self.current: OwnedSession | None = None
        self.audition: OwnedSession | None = None
        self.phase = "idle"
        self.message: str | None = None
        self.metrics: dict[str, float | None] = {
            "ttsFirstFrameSeconds": None,
            "ttsAudioSeconds": None,
            "llmFirstTokenSeconds": None,
            "endOfUtteranceSeconds": None,
            "transcriptionDelaySeconds": None,
        }
        self.turns: list[dict[str, str | float]] = []
        self.lock = asyncio.Lock()
        self.start_task: asyncio.Task[dict[str, Any]] | None = None
        self.closed = False
        self.fast_loaded = False
        self.library: StudioLibrary | None = None
        self.recognizer_service: NemotronService | None = None
        self.retired_rooms: deque[str] = deque(maxlen=16)
        self._last_status: dict[str, Any] | None = None
        self._last_status_at = 0.0
        self._status_lock = asyncio.Lock()

    async def status(self, *, refresh: bool = False) -> dict[str, Any]:
        backend = os.environ.get("VOICEBOX_TTS_BACKEND", "voicebox")
        local_voice = backend == "mlx" and bool(os.environ.get("VOICEBOX_VOICE_BUNDLE"))
        async with self._status_lock:
            if refresh or self._last_status is None or time.monotonic() - self._last_status_at > 3:
                voice: dict[str, Any] = {
                    "name": "Select a profile in .env",
                    "engine": "qwen",
                    "model": "0.6B",
                    "loaded": False,
                    "cached": False,
                    "backend": backend,
                    "streaming": backend == "mlx",
                }
                try:
                    async with asyncio.timeout(12):
                        problems = await check_setup(
                            require_loaded=backend != "mlx", local_voice=local_voice
                        )
                        if backend not in ("voicebox", "mlx"):
                            problems.append("VOICEBOX_TTS_BACKEND must be voicebox or mlx.")
                        if backend == "mlx":
                            if platform.system() != "Darwin" or platform.machine() != "arm64":
                                problems.append("The MLX fast path requires an Apple Silicon Mac.")
                            if importlib.util.find_spec("mlx_audio") is None:
                                problems.append("Install the optional mlx extra for the fast path.")
                            snapshot = Path(os.environ.get("VOICEBOX_MLX_MODEL_PATH", ""))
                            if not (snapshot / "model.safetensors").is_file():
                                problems.append(
                                    "Set VOICEBOX_MLX_MODEL_PATH to the local Qwen snapshot."
                                )
                        if local_voice or os.environ.get("VOICEBOX_PROFILE"):
                            provider = (
                                FastQwenTTS(
                                    profile=os.environ.get("VOICEBOX_PROFILE", "Local voice"),
                                    model_path=os.environ.get("VOICEBOX_MLX_MODEL_PATH", ""),
                                    voice_bundle=os.environ["VOICEBOX_VOICE_BUNDLE"],
                                )
                                if local_voice
                                else configured_provider()
                            )
                            try:
                                profile = await provider.resolve_profile()
                                readiness = await provider.model_readiness()
                                voice.update(
                                    name=profile.name,
                                    loaded=readiness.loaded
                                    if backend != "mlx"
                                    else self.fast_loaded,
                                    cached=readiness.downloaded,
                                    source="local-bundle" if local_voice else "voicebox",
                                )
                                if backend == "mlx" and profile.sample_count != 1:
                                    problems.append("Fast mode requires a single-reference voice.")
                            finally:
                                await provider.aclose()
                except (TimeoutError, VoiceboxError, ValueError):
                    problems = [
                        "Cannot verify the local voice bundle or model snapshot."
                        if local_voice
                        else "Cannot verify Voicebox readiness. Check the local application."
                    ]
                self._last_status = {"voice": voice, "problems": problems}
                self._last_status_at = time.monotonic()
            info = self._last_status
        message: str | None
        if self.current is not None and self.phase == "starting":
            # A browser can receive a scoped room token before the local agent is ready.
            message = self.message or "Connecting the local agent. This can take a few seconds."
        else:
            message = self.message
        speech_choice, reasoning_choice = provider_choices()
        return {
            **info,
            "ready": not info["problems"] and self.phase == "idle" and not self.closed,
            "phase": self.phase,
            "ai": {
                "provider": reasoning_choice,
                "model": os.environ.get("VOICEBOX_LLM_MODEL", "gpt-5.6-luna")
                if reasoning_choice in ("copilot", "codex", *ENDPOINT_PROVIDERS)
                else os.environ.get("AZURE_OPENAI_MODEL", "gpt-4.1-nano")
                if reasoning_choice == "azure"
                else "gpt-4.1-mini",
                "effort": os.environ.get("VOICEBOX_REASONING_EFFORT", "low")
                if reasoning_choice in ("copilot", "codex")
                else "none",
                "local": reasoning_choice in ENDPOINT_PROVIDERS
                and is_loopback_url(os.environ.get("VOICEBOX_LLM_BASE_URL", DEFAULT_LLM_URL)),
            },
            "stt": {
                "provider": speech_choice,
                "model": "nemotron-speech-streaming-en-0.6b"
                if speech_choice == "nemotron"
                else "Azure Speech"
                if speech_choice == "azure"
                else "gpt-4o-mini-transcribe",
                "local": speech_choice == "nemotron",
            },
            "livekit": {
                "mode": os.environ.get("VOICEBOX_LIVEKIT_MODE", "configured"),
                "local": is_loopback_url(os.environ.get("LIVEKIT_URL", "")),
                "configured": all(
                    os.environ.get(name)
                    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
                ),
            },
            # Session handles are control capabilities; never reveal another tab's handle.
            "session": None,
            "metrics": self.metrics.copy(),
            "turns": [turn.copy() for turn in self.turns],
            "message": message,
        }

    async def create(self) -> dict[str, Any]:
        async with self.lock:
            if self.closed or self.phase != "idle" or self.current is not None:
                raise StudioError("This backend already has a session or unresolved work.")
            self.phase = "starting"
            self.clear_audition()
            self.message = None
            self.metrics = dict.fromkeys(self.metrics)
            self.turns.clear()
            self.fast_loaded = False
            self.start_task = asyncio.create_task(self._create())
            self.start_task.add_done_callback(self._observe_task)
        # The operation survives a disconnected HTTP client; heartbeat expiry reaps it.
        return await asyncio.shield(self.start_task)

    async def _audition_preflight(self, path: Path) -> None:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise StudioError("Generated auditions require the Apple Silicon MLX backend.", 503)
        if importlib.util.find_spec("mlx_audio") is None:
            raise StudioError("Install the optional mlx extra before generating an audition.", 503)
        try:
            provider = FastQwenTTS(
                profile="Local voice",
                model_path=os.environ.get("VOICEBOX_MLX_MODEL_PATH", ""),
                voice_bundle=path,
            )
            try:
                await provider.resolve_profile()
            finally:
                await provider.aclose()
        except (VoiceboxError, ValueError, OSError):
            raise StudioError(
                "Check the saved voice and complete local Qwen 0.6B model snapshot.", 503
            ) from None

    def clear_audition(self) -> None:
        if self.audition is not None:
            self.audition.pcm.clear()
            self.audition = None

    async def create_audition(
        self, voice_id: str, text: str, *, hold_playback: bool = False
    ) -> dict[str, Any]:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300:
            raise StudioError("Audition text must contain 1–300 characters.", 400)
        if not isinstance(voice_id, str):
            raise StudioError("Choose a saved voice.", 400)
        if type(hold_playback) is not bool:
            raise StudioError("holdPlayback must be a boolean.", 400)
        async with self.lock:
            require_idle(self)
            if os.environ.get("VOICEBOX_EXCLUSIVE") != "1":
                raise StudioError("Confirm exclusive local inference with VOICEBOX_EXCLUSIVE=1.")
            if self.library is None:
                raise StudioError("The private voice library is not ready.", 503)
            path = self.library.voice_path(voice_id)
            await self._audition_preflight(path)
            self.clear_audition()
            owned = OwnedSession(
                id=secrets.token_urlsafe(32),
                room="",
                participant="",
                agent="",
                key=secrets.token_urlsafe(32),
                kind="audition",
                voice_id=voice_id,
                hold_playback=hold_playback,
            )
            self.audition = self.current = owned
            self.phase = "starting"
            self.message = "Preparing the selected voice. Generated audio is kept only in memory."
            self.fast_loaded = False
            self.start_task = asyncio.create_task(self._start_audition(owned, path, text.strip()))
            self.start_task.add_done_callback(self._observe_task)
        return await asyncio.shield(self.start_task)

    async def _start_audition(self, owned: OwnedSession, path: Path, text: str) -> dict[str, Any]:
        # No cloud credentials, text arguments, or arbitrary user endpoints in this child.
        env = {
            key: value
            for key, value in os.environ.items()
            if key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV")
        }
        env.update(
            STUDIO_EVENT_KEY=owned.key,
            VOICEBOX_VOICE_BUNDLE=str(path),
            VOICEBOX_MLX_MODEL_PATH=os.environ.get("VOICEBOX_MLX_MODEL_PATH", ""),
            HF_HUB_OFFLINE="1",
            HF_HUB_DISABLE_TELEMETRY="1",
            TRANSFORMERS_OFFLINE="1",
            OTEL_SDK_DISABLED="true",
            LK_DUMP_TTS="0",
            PYTHONUNBUFFERED="1",
        )
        try:
            self.lease.mark_active()
            owned.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "examples.audition_worker",
                cwd=ROOT,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=64 * 1024,
            )
            owned.monitor = asyncio.create_task(self._monitor(owned))
            owned.monitor.add_done_callback(self._observe_task)
            assert owned.process.stdin is not None
            owned.process.stdin.write(json.dumps({"text": text}).encode())
            await owned.process.stdin.drain()
            owned.process.stdin.close()
            return {"auditionId": owned.id, "voiceId": owned.voice_id, "phase": "starting"}
        except (Exception, asyncio.CancelledError) as error:
            owned.audition_state = "failed"
            owned.audition_message = "Local audition could not start. Check the MLX setup."
            if owned.process is None:
                self.lease.mark_safe()
                self.current = None
                self.phase = "idle"
            else:
                await self.end_audition(owned.id)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise StudioError(owned.audition_message, 503) from None

    def require_audition(self, identifier: str) -> OwnedSession:
        owned = self.audition
        if owned is None or not secrets.compare_digest(owned.id, identifier):
            raise StudioError("This tab no longer owns that audition.", 404)
        if (
            owned.completed is not None
            and self.current is not owned
            and time.monotonic() - owned.completed > AUDITION_RESULT_SECONDS
        ):
            self.clear_audition()
            raise StudioError("The audition expired. Generate it again to listen.", 410)
        return owned

    def audition_status(self, identifier: str) -> dict[str, Any]:
        owned = self.require_audition(identifier)
        if owned is self.current and owned.end_task is None:
            owned.heartbeat = time.monotonic()
        return {
            "auditionId": owned.id,
            "voiceId": owned.voice_id,
            "phase": self.phase,
            "state": owned.audition_state,
            "message": owned.audition_message or (self.message if owned is self.current else None),
        }

    def audition_audio(self, identifier: str) -> bytes:
        owned = self.require_audition(identifier)
        if (
            owned.audition_state != "ready"
            or not owned.pcm
            or (self.current is owned and not owned.hold_playback)
        ):
            raise StudioError("Generated audio is not available. Wait for completion or try again.")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(owned.pcm)
        owned.pcm.clear()
        return buffer.getvalue()

    async def end_audition(self, identifier: str) -> None:
        owned = self.require_audition(identifier)
        owned.pcm.clear()
        if owned.audition_state != "failed":
            owned.audition_state = "cancelled"
        if self.current is owned:
            await self.end(identifier)

    @staticmethod
    def _observe_task(task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Studio operation failed (%s).", type(task.exception()).__name__)

    async def _create(self) -> dict[str, Any]:
        owned: OwnedSession | None = None
        room_created = False
        try:
            reasoning = provider_choices()[1]
            if reasoning in ENDPOINT_PROVIDERS:
                try:
                    await check_llm_endpoint(
                        reasoning,
                        os.environ.get("VOICEBOX_LLM_BASE_URL", DEFAULT_LLM_URL),
                        os.environ.get("VOICEBOX_LLM_MODEL", "qwen3:1.7b"),
                        os.environ.get("VOICEBOX_CUSTOM_LLM_API_KEY", "")
                        if reasoning == "openai-compatible"
                        else "",
                    )
                except (RuntimeError, ValueError) as error:
                    raise StudioError(str(error), 503) from None
            if provider_choices()[0] == "nemotron":
                if self.recognizer_service is None:
                    self.recognizer_service = NemotronService()
                try:
                    await self.recognizer_service.start()
                except (RuntimeError, ValueError, OSError):
                    raise StudioError(
                        "Local Nemotron could not start. Check its explicit setup, model and port.",
                        503,
                    ) from None
            info = await self.status(refresh=True)
            if info["problems"]:
                raise StudioError(" ".join(info["problems"]), 503)
            owned = OwnedSession(
                id=secrets.token_urlsafe(32),
                room="voicebox-studio-" + secrets.token_hex(10),
                participant="listener-" + secrets.token_hex(10),
                agent="voicebox-" + secrets.token_hex(10),
                key=secrets.token_urlsafe(32),
            )
            async with api.LiveKitAPI(timeout=aiohttp.ClientTimeout(total=15)) as client:
                # Any live agent in the project could be another client of this local server.
                rooms = await client.room.list_rooms(api.ListRoomsRequest())
                # LiveKit's unfiltered listing can briefly retain an empty room
                # after exact-name deletion checks succeed. Only ignore rooms whose
                # worker this broker already observed exit after a confirmed drain.
                if any(room.name not in self.retired_rooms for room in rooms.rooms):
                    raise StudioError(
                        "A LiveKit room is already active. End other local-agent sessions first."
                    )
                await client.room.create_room(
                    api.CreateRoomRequest(name=owned.room, empty_timeout=60, max_participants=2)
                )
                room_created = True
            token = (
                api.AccessToken()
                .with_identity(owned.participant)
                .with_name("You")
                .with_ttl(datetime.timedelta(minutes=2))
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=owned.room,
                        can_publish=True,
                        can_publish_data=True,
                        can_subscribe=True,
                        can_publish_sources=["microphone"],
                    )
                )
                .to_jwt()
            )
            self.lease.mark_active()
            self.current = owned
            env = dict(
                os.environ,
                STUDIO_ROOM=owned.room,
                STUDIO_PARTICIPANT_IDENTITY=owned.participant,
                STUDIO_AGENT_IDENTITY=owned.agent,
                STUDIO_EVENT_KEY=owned.key,
                OTEL_SDK_DISABLED="true",
                LK_DUMP_TTS="0",
                PYTHONUNBUFFERED="1",
            )
            owned.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "examples.studio_worker",
                cwd=ROOT,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=64 * 1024,
            )
            owned.monitor = asyncio.create_task(self._monitor(owned))
            owned.monitor.add_done_callback(self._observe_task)
            return {
                "sessionId": owned.id,
                "serverUrl": os.environ["LIVEKIT_URL"],
                "roomName": owned.room,
                "participantName": "You",
                "participantToken": token,
                "agentIdentity": owned.agent,
            }
        except (Exception, asyncio.CancelledError) as error:
            if owned is not None and owned.process is not None:
                self.phase = "blocked"
                self.message = (
                    "Agent start completion is unknown. End the session and check the backend."
                )
            else:
                self.lease.mark_safe()
                self.current = None
                self.phase = "idle"
            if owned is not None and room_created:
                await self._delete_room(owned)
            if isinstance(error, (StudioError, asyncio.CancelledError)):
                raise
            raise StudioError(
                "Cannot create the LiveKit session. Check server credentials and connectivity.", 503
            ) from None

    def require_owner(self, session_id: str) -> OwnedSession:
        if self.current is None or not secrets.compare_digest(self.current.id, session_id):
            raise StudioError("This tab no longer owns a Studio session.", 404)
        return self.current

    def heartbeat(self, session_id: str) -> None:
        owned = self.require_owner(session_id)
        if self.phase in ("draining", "blocked"):
            raise StudioError("Session is ending or blocked; wait for backend recovery.")
        owned.heartbeat = time.monotonic()

    async def end(self, session_id: str) -> None:
        owned = self.require_owner(session_id)
        if owned.kind == "audition":
            owned.pcm.clear()
            if owned.audition_state == "generating":
                owned.audition_state = "cancelled"
            if (
                owned.playback_admitted
                and owned.finished
                and owned.safe
                and owned.process is not None
                and owned.process.returncode == 0
            ):
                self.current = None
                self.phase = "idle"
                self.message = None
                return
        if owned.end_task is None:
            if self.phase != "blocked":
                self.phase = "draining"
            owned.end_task = asyncio.create_task(self._end(owned))
            owned.end_task.add_done_callback(self._observe_task)
        # Return promptly; status remains draining until the worker confirms safe completion.

    async def _delete_room(self, owned: OwnedSession) -> bool:
        if owned.kind == "audition":
            return True
        try:
            async with (
                asyncio.timeout(12),
                api.LiveKitAPI(timeout=aiohttp.ClientTimeout(total=10)) as client,
            ):
                await client.room.delete_room(api.DeleteRoomRequest(room=owned.room))
                # Room deletion can become visible after the mutation response. Do not
                # advertise reconnect readiness before this exact owned room is gone.
                for _ in range(25):
                    rooms = await client.room.list_rooms(api.ListRoomsRequest(names=[owned.room]))
                    if not rooms.rooms:
                        return True
                    await asyncio.sleep(0.2)
                self.message = "The previous room is still closing. Verify its removal in LiveKit."
                return False
        except api.TwirpError as error:
            if error.code == "not_found":
                return True
            self.message = "Room cleanup failed. Verify the temporary room in LiveKit."
            logger.warning("LiveKit room cleanup failed (%s).", error.code)
        except (aiohttp.ClientError, TimeoutError):
            self.message = "Room cleanup could not reach LiveKit. Check connectivity."
            logger.warning("LiveKit room cleanup could not reach the service.")
        return False

    async def _end(self, owned: OwnedSession) -> None:
        process = owned.process
        if process is None:
            return
        end_deadline = asyncio.get_running_loop().time() + SHUTDOWN_SECONDS
        if owned.kind == "audition" and not owned.stop_ready.is_set():
            # SIGTERM before Python installs its handler cannot confirm a safe drain.
            started = asyncio.create_task(owned.stop_ready.wait())
            try:
                await asyncio.wait(
                    {started, owned.monitor} if owned.monitor else {started},
                    timeout=min(STARTUP_SECONDS, SHUTDOWN_SECONDS),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                started.cancel()
                await asyncio.gather(started, return_exceptions=True)
            if not owned.stop_ready.is_set() and process.returncode is None:
                self.phase = "blocked"
                self.message = "Audition startup did not acknowledge cancellation readiness."
                process.kill()
                await process.wait()
                return
        if process.returncode is None:
            try:
                process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            async with asyncio.timeout_at(end_deadline):
                if owned.monitor is not None:
                    await asyncio.shield(owned.monitor)
        except TimeoutError:
            self.phase = "blocked"
            self.message = (
                "Agent drain exceeded its bound. Confirm a Voicebox restart before recovery."
            )
            if process.returncode is None:
                process.kill()
                await process.wait()
            # The durable marker deliberately remains after a forced kill.
        except Exception as error:
            self.phase = "blocked"
            self.message = (
                "Worker monitoring failed. Confirm the worker is stopped before backend recovery."
            )
            logger.warning("Owned worker monitoring failed (%s).", type(error).__name__)
            if process.returncode is None:
                process.kill()
                await process.wait()
        finally:
            if owned.kind == "room":
                await self._delete_room(owned)

    def _event(self, owned: OwnedSession, data: dict[str, Any]) -> None:
        if data.get("key") != owned.key or self.current is not owned:
            return
        event = data.get("event")
        if event == "audition_started" and owned.kind == "audition":
            owned.stop_ready.set()
        elif event == "audition_audio" and owned.kind == "audition":
            if owned.audition_state != "generating":
                return
            try:
                encoded = data.get("pcm")
                if not isinstance(encoded, str) or not 1 <= len(encoded) <= 4096:
                    raise ValueError
                pcm = base64.b64decode(encoded, validate=True)
                if len(pcm) % 2 or len(owned.pcm) + len(pcm) > MAX_AUDITION_PCM:
                    raise ValueError
                owned.pcm.extend(pcm)
            except (ValueError, binascii.Error):
                owned.pcm.clear()
                owned.audition_state = "failed"
                owned.audition_message = "The local model returned invalid or oversized audio."
                if owned.end_task is None:
                    owned.end_task = asyncio.create_task(self._end(owned))
                    owned.end_task.add_done_callback(self._observe_task)
                    self.phase = "draining"
        elif event == "startup" and owned.kind == "room" and self.phase == "starting":
            stage = data.get("stage")
            if isinstance(stage, str) and stage in STARTUP_MESSAGES:
                self.message = STARTUP_MESSAGES[stage]
        elif event == "ready" and self.phase == "starting":
            self.phase = "active"
            self.message = None
            if owned.kind == "audition":
                self.message = "Generating the audition with the saved voice."
        elif event == "model_loaded":
            if owned.kind == "room":
                self.fast_loaded = True
                self._last_status_at = 0
        elif event == "draining" and self.phase != "blocked":
            self.phase = "draining"
        elif event == "uncertain":
            self.phase = "blocked"
            self.message = (
                "Inference completion is unknown. End the session and restart the voice backend."
            )
        elif event == "finished":
            owned.finished = True
            owned.safe = data.get("safe") is True
        elif event == "error":
            # Only authenticated child emits static diagnostics; cap even those.
            self.message = str(data.get("message", "Agent failed."))[:240]
            if owned.kind == "audition":
                owned.pcm.clear()
                if owned.audition_state != "failed" or not owned.audition_message:
                    owned.audition_message = self.message
                owned.audition_state = "failed"
        elif event == "metrics":
            values: dict[str, float] = {}
            for key in self.metrics:
                value = data.get(key)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0
                ):
                    self.metrics[key] = float(value)
                    values[key] = float(value)
            identifier = data.get("speechId")
            if (
                owned.kind == "room"
                and values
                and isinstance(identifier, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier)
            ):
                turn = next((item for item in self.turns if item["id"] == identifier), None)
                if turn is None:
                    turn = {"id": identifier}
                    self.turns.append(turn)
                    del self.turns[:-20]
                for key, measurement in values.items():
                    if key == "ttsAudioSeconds":
                        total = float(turn.get(key, 0)) + measurement
                        if math.isfinite(total):
                            turn[key] = total
                    elif key not in turn:
                        # First observed latency for this speech; later TTS chunks
                        # are not the first audible response. Never sum latencies.
                        turn[key] = measurement

    async def _monitor(self, owned: OwnedSession) -> None:
        process = owned.process
        assert process is not None and process.stdout is not None
        try:
            async for line in process.stdout:
                if line.startswith(b"STUDIO_EVENT "):
                    try:
                        data = json.loads(line[len(b"STUDIO_EVENT ") :])
                    except (ValueError, UnicodeDecodeError):
                        logger.warning("Agent returned malformed lifecycle data.")
                        continue
                    if isinstance(data, dict):
                        self._event(owned, data)
            await process.wait()
        finally:
            if self.current is owned:
                if owned.finished and owned.safe and process.returncode == 0:
                    if owned.kind == "audition" or await self._delete_room(owned):
                        self.lease.mark_safe()
                        if owned.kind == "room":
                            self.retired_rooms.append(owned.room)
                        else:
                            owned.completed = time.monotonic()
                            if owned.audition_state == "generating":
                                owned.audition_state = "ready" if any(owned.pcm) else "failed"
                                if owned.audition_state == "failed":
                                    owned.audition_message = (
                                        "The model returned empty or silent generated audio."
                                    )
                            if owned.audition_state != "ready":
                                owned.pcm.clear()
                            self.message = None
                        if owned.hold_playback and owned.audition_state == "ready":
                            owned.playback_admitted = True
                            self.phase = "active"
                            self.message = (
                                "Local read-aloud owns playback. "
                                "Stop it before starting a conversation."
                            )
                        else:
                            self.current = None
                            self.phase = "idle"
                        self.fast_loaded = False
                        self._last_status_at = 0
                    else:
                        self.phase = "blocked"
                else:
                    self.phase = "blocked"
                    if owned.kind == "audition":
                        owned.pcm.clear()
                        owned.audition_state = "failed"
                        owned.audition_message = (
                            "Audition drain is unconfirmed. Confirm the worker is stopped, then "
                            "restart Studio with --confirm-backend-restarted."
                        )
                        self.message = owned.audition_message
                    else:
                        self.message = (
                            "Agent exited without confirmed drain. "
                            "Confirm the voice backend has stopped, then restart "
                            "Studio with --confirm-backend-restarted."
                        )
                    if process.returncode is None and owned.end_task is None:
                        owned.end_task = asyncio.create_task(self._end(owned))
                        owned.end_task.add_done_callback(self._observe_task)

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(5)
            if (
                self.audition is not None
                and self.current is not self.audition
                and self.audition.completed is not None
                and time.monotonic() - self.audition.completed > AUDITION_RESULT_SECONDS
            ):
                self.clear_audition()
            owned = self.current
            if owned is not None and owned.end_task is None:
                now = time.monotonic()
                if (
                    now - owned.heartbeat > HEARTBEAT_SECONDS
                    or now - owned.created
                    > (AUDITION_SECONDS if owned.kind == "audition" else MAX_SESSION_SECONDS)
                    or (self.phase == "starting" and now - owned.created > STARTUP_SECONDS)
                ):
                    if self.phase == "starting" and now - owned.created > STARTUP_SECONDS:
                        self.message = "Startup timed out. " + (
                            self.message or "The local agent did not become ready."
                        )
                    elif now - owned.heartbeat > HEARTBEAT_SECONDS:
                        self.message = "Session ended because its browser stopped responding."
                    else:
                        self.message = "Session ended because its time limit was reached."
                    if owned.kind == "audition":
                        # A watchdog failure is not an explicit user cancellation.
                        # Keep its reason on the result after safe worker drain.
                        owned.audition_state = "failed"
                        if self.phase == "starting" and now - owned.created > STARTUP_SECONDS:
                            owned.audition_message = (
                                "Voice sample startup timed out. "
                                "Check local model readiness, then try again."
                            )
                        elif now - owned.heartbeat > HEARTBEAT_SECONDS:
                            owned.audition_message = (
                                "Voice sample stopped because the browser stopped responding. "
                                "Keep Studio open and try again."
                            )
                        else:
                            owned.audition_message = (
                                "Voice sample generation reached its time limit. "
                                "Try a shorter sample."
                            )
                        self.message = owned.audition_message
                        await self.end_audition(owned.id)
                    else:
                        await self.end(owned.id)

    async def close(self) -> None:
        self.closed = True
        if self.start_task is not None and not self.start_task.done():
            with contextlib.suppress(StudioError):
                await asyncio.shield(self.start_task)
        if self.current is not None:
            owned = self.current
            await self.end(owned.id)
            if owned.end_task is not None:
                await asyncio.shield(owned.end_task)
        if self.recognizer_service is not None:
            await self.recognizer_service.close()
        self.clear_audition()


STUDIO = web.AppKey("studio", Studio)
SETUP_CHECKS = web.AppKey("setup_checks", SetupChecks)
ORIGINS = web.AppKey("origins", set[str])


@web.middleware
async def local_only(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    host = request.host
    allowed = request.app[ORIGINS]
    if f"http://{host}" not in allowed:
        return web.json_response(
            {"error": "Only the configured loopback host is allowed."}, status=403
        )
    origin = request.headers.get("Origin")
    if origin is not None and origin not in allowed:
        return web.json_response({"error": "Cross-origin requests are not allowed."}, status=403)
    upload = request.path == "/api/voices" and request.method == "POST"
    if request.method not in ("GET", "HEAD"):
        if request.headers.get("X-Voicebox-Studio") != "1" or request.content_type != (
            "multipart/form-data" if upload else "application/json"
        ):
            return web.json_response({"error": "Expected a Studio JSON request."}, status=403)
    try:
        if request.can_read_body and not upload:
            body = await request.read()
            if len(body) > 4096:
                raise StudioError("JSON request exceeds its allocation limit.", 413)
        response = await handler(request)
    except StudioError as error:
        response = web.json_response({"error": str(error)}, status=error.status)
    except (json.JSONDecodeError, UnicodeDecodeError):
        response = web.json_response({"error": "Invalid JSON body."}, status=400)
    except LibraryError as error:
        response = web.json_response({"error": str(error)}, status=error.status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "microphone=(self), camera=(), display-capture=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' https: wss: "
        "http://127.0.0.1:7880 ws://127.0.0.1:7880; "
        "media-src 'self' blob:; worker-src 'self' blob:; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


async def setup_handler(request: web.Request) -> web.Response:
    try:
        return web.json_response(await request.app[SETUP_CHECKS].get())
    except SetupUnavailable as error:
        return web.json_response({"error": str(error)}, status=503)


async def status_handler(request: web.Request) -> web.Response:
    return web.json_response(await request.app[STUDIO].status())


async def create_handler(request: web.Request) -> web.Response:
    data = await request.json()
    if not isinstance(data, dict) or any(key != "room_config" for key in data):
        raise StudioError("Session options are configured on the server.", 400)
    return web.json_response(await request.app[STUDIO].create())


async def session_id(request: web.Request) -> str:
    data = await request.json()
    if (
        not isinstance(data, dict)
        or set(data) != {"sessionId"}
        or not isinstance(data["sessionId"], str)
        or not 1 <= len(data["sessionId"]) <= 128
    ):
        raise StudioError("Expected a valid sessionId.", 400)
    return data["sessionId"]


async def heartbeat_handler(request: web.Request) -> web.Response:
    request.app[STUDIO].heartbeat(await session_id(request))
    return web.json_response({"ok": True})


async def end_handler(request: web.Request) -> web.Response:
    await request.app[STUDIO].end(await session_id(request))
    return web.json_response({"phase": request.app[STUDIO].phase})


async def audition_handler(request: web.Request) -> web.Response:
    studio = request.app[STUDIO]
    body = await request.json()
    if request.path == "/api/audition":
        if (
            not isinstance(body, dict)
            or not {"voiceId", "text"} <= set(body)
            or set(body) - {"voiceId", "text", "holdPlayback"}
        ):
            raise StudioError("Provide a saved voice and audition text.", 400)
        return web.json_response(
            await studio.create_audition(
                body["voiceId"], body["text"], hold_playback=body.get("holdPlayback", False)
            )
        )
    if (
        not isinstance(body, dict)
        or set(body) != {"auditionId"}
        or not isinstance(body["auditionId"], str)
        or not 1 <= len(body["auditionId"]) <= 128
    ):
        raise StudioError("Expected a valid auditionId.", 400)
    identifier = body["auditionId"]
    if request.path.endswith("/end"):
        await studio.end_audition(identifier)
        return web.json_response({"phase": studio.phase})
    if request.path.endswith("/audio"):
        return web.Response(body=studio.audition_audio(identifier), content_type="audio/wav")
    return web.json_response(studio.audition_status(identifier))


def library_for(request: web.Request) -> StudioLibrary:
    library = request.app[STUDIO].library
    if library is None:
        raise StudioError("The private voice library has not been initialized.", 503)
    return library


def require_idle(studio: Studio) -> None:
    if studio.closed or studio.phase != "idle" or studio.current is not None:
        raise StudioError(
            "End the conversation or audition and wait for drain "
            "before changing voices or providers."
        )


def provider_options() -> dict[str, list[dict[str, Any]]]:
    def option(identifier: str, label: str, available: bool, reason: str) -> dict[str, Any]:
        return {
            "id": identifier,
            "label": label,
            "available": available,
            "reason": None if available else reason,
        }

    azure = shutil.which("az") is not None and bool(os.environ.get("AZURE_SUBSCRIPTION_ID"))
    openai = bool(os.environ.get("OPENAI_API_KEY"))
    native = os.environ.get("NEMOTRON_SERVER_BINARY")
    local_stt = bool(
        native
        and Path(native).is_file()
        and Path(os.environ.get("NEMOTRON_MODEL_PATH", "")).is_file()
    )
    return {
        "stt": [
            option(
                "nemotron",
                "Nemotron · local CPU",
                local_stt,
                "Run the explicit local speech setup.",
            ),
            option(
                "azure",
                "Azure Speech",
                azure,
                "Configure Azure resources and sign in with az login.",
            ),
            option(
                "openai", "OpenAI transcription", openai, "Set OPENAI_API_KEY on the local server."
            ),
        ],
        "llm": [
            option("ollama", "Ollama · local", True, ""),
            option("openai-compatible", "Custom OpenAI-compatible endpoint", True, ""),
            option(
                "copilot",
                "Copilot · Luna low",
                shutil.which("copilot") is not None,
                "Install and sign in to Copilot CLI.",
            ),
            option(
                "codex",
                "Codex · Luna low · restricted agent",
                shutil.which("codex") is not None and platform.system() == "Darwin",
                "Restricted Codex requires a verified macOS runtime and explicit consent.",
            ),
            option(
                "azure",
                "Azure OpenAI",
                azure,
                "Configure Azure resources and sign in with az login.",
            ),
            option("openai", "OpenAI", openai, "Set OPENAI_API_KEY on the local server."),
        ],
    }


async def settings_handler(request: web.Request) -> web.Response:
    studio = request.app[STUDIO]
    library = library_for(request)
    async with studio.lock:
        if request.method == "POST":
            require_idle(studio)
            library.update_settings(await request.json())
            studio.clear_audition()
            library.apply_environment()
            if library.settings()["sttProvider"] != "nemotron" and studio.recognizer_service:
                await studio.recognizer_service.close()
            studio.metrics = dict.fromkeys(studio.metrics)
            studio.turns.clear()
            studio.message = None
            studio._last_status_at = 0
        return web.json_response({**library.settings(), "providers": provider_options()})


async def voices_handler(request: web.Request) -> web.Response:
    library = library_for(request)
    studio = request.app[STUDIO]
    async with studio.lock:
        if request.method == "GET":
            return web.json_response({"voices": library.list_voices(), "guidedText": GUIDED_TEXT})
        require_idle(studio)
        fields: dict[str, bytes] = {}
        total = 0
        async with asyncio.timeout(20):
            reader = await request.multipart()
            async for part in reader:
                if not isinstance(part, aiohttp.BodyPartReader) or not isinstance(part.name, str):
                    raise LibraryError("Nested or unnamed upload parts are not supported.")
                if (
                    part.name not in ("name", "transcript", "authorized", "audio")
                    or part.name in fields
                ):
                    raise LibraryError("Unexpected or duplicated voice enrollment field.")
                value = bytearray()
                while chunk := await part.read_chunk(64 * 1024):
                    total += len(chunk)
                    value.extend(chunk)
                    if total > MAX_RECORDING_BYTES + 8192:
                        raise LibraryError("Voice upload exceeds 4 MiB.", 413)
                fields[part.name] = bytes(value)
        if set(fields) != {"name", "transcript", "authorized", "audio"}:
            raise LibraryError("Provide the recording, name, exact transcript and permission.")
        voice = library.create_voice(
            fields["name"].decode(),
            fields["transcript"].decode(),
            fields["audio"],
            fields["authorized"] == b"true",
        )
        studio.clear_audition()
        studio._last_status_at = 0
        return web.json_response({"voice": voice}, status=201)


async def voice_handler(request: web.Request) -> web.Response:
    library = library_for(request)
    studio = request.app[STUDIO]
    identifier = request.match_info["voice_id"]
    async with studio.lock:
        if request.method == "GET":
            return web.Response(body=library.read_audio(identifier), content_type="audio/wav")
        require_idle(studio)
        body = await request.json()
        if request.method == "PATCH":
            if not isinstance(body, dict) or set(body) != {"name"}:
                raise LibraryError("Only a voice name can be changed.")
            voice = library.rename_voice(identifier, body["name"])
            studio.clear_audition()
            return web.json_response({"voice": voice})
        if not isinstance(body, dict) or set(body) != {"confirm"} or body["confirm"] is not True:
            raise LibraryError("Confirm before deleting this voice.")
        library.delete_voice(identifier)
        studio.clear_audition()
        studio._last_status_at = 0
        return web.json_response({"deleted": True})


def create_app(studio: Studio, *, port: int = 8765, assets: Path | None = None) -> web.Application:
    app = web.Application(middlewares=[local_only], client_max_size=MAX_RECORDING_BYTES + 8192)
    app[STUDIO] = studio
    app[SETUP_CHECKS] = SetupChecks(ROOT)
    app[ORIGINS] = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }
    app.router.add_get("/api/status", status_handler)
    app.router.add_get("/api/setup", setup_handler)
    app.router.add_post("/api/session", create_handler)
    app.router.add_post("/api/session/heartbeat", heartbeat_handler)
    app.router.add_post("/api/session/end", end_handler)
    for route in ("", "/status", "/audio", "/end"):
        app.router.add_post("/api/audition" + route, audition_handler)
    app.router.add_get("/api/settings", settings_handler)
    app.router.add_post("/api/settings", settings_handler)
    app.router.add_get("/api/voices", voices_handler)
    app.router.add_post("/api/voices", voices_handler)
    app.router.add_get("/api/voices/{voice_id}/audio", voice_handler)
    app.router.add_patch("/api/voices/{voice_id}", voice_handler)
    app.router.add_delete("/api/voices/{voice_id}", voice_handler)
    if assets is not None:

        async def index(request: web.Request) -> web.FileResponse:
            return web.FileResponse(assets / "index.html")

        app.router.add_get("/", index)
        app.router.add_static("/assets/", assets / "assets")
    return app


async def serve(args: argparse.Namespace) -> None:
    if not (ROOT / "web/dist/index.html").is_file():
        raise RuntimeError("Build the UI first: npm --prefix web ci && npm --prefix web run build")
    url = os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493")
    parsed = urlsplit(url)
    try:
        loopback = (
            parsed.hostname == "localhost"
            or ipaddress.ip_address(parsed.hostname or "").is_loopback
        )
    except ValueError:
        loopback = False
    if not loopback:
        raise RuntimeError("Local Studio requires a loopback Voicebox URL.")
    lease = BackendLease(runtime_root(), url)
    lease.acquire(confirmed_backend_restart=args.confirm_backend_restarted)
    studio = Studio(lease)
    studio.library = StudioLibrary(
        Path(
            os.environ.get(
                "VOICEBOX_LIBRARY_DIR", str(Path.home() / ".local/share/voicebox-studio/library")
            )
        )
    )
    if os.environ.get("VOICEBOX_VOICE_BUNDLE"):
        studio.library.seed_bundle(Path(os.environ["VOICEBOX_VOICE_BUNDLE"]))
    studio.library.apply_environment()
    runner = web.AppRunner(
        create_app(studio, port=args.port, assets=ROOT / "web/dist"), access_log=None
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    watchdog = asyncio.create_task(studio.watchdog())
    try:
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", args.port).start()
        print(f"Voicebox Studio: http://127.0.0.1:{args.port}", flush=True)
        print(
            "One local session. No conversation recordings. Press Ctrl+C to end and drain.",
            flush=True,
        )
        await stop.wait()
    finally:
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
        await studio.close()
        await runner.cleanup()
        lease.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-file", type=Path, help="Private rotating log for managed launch.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check saved configuration without starting a server or generating audio.",
    )
    parser.add_argument(
        "--confirm-backend-restarted",
        action="store_true",
        help=(
            "Clear unresolved-work marker ONLY after confirming the synthesis backend "
            "and workers stopped/restarted."
        ),
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535.")
    load_dotenv(ROOT / ".env", override=False)
    if args.log_file:
        handler = RotatingFileHandler(args.log_file, maxBytes=256 * 1024, backupCount=1)
        logging.basicConfig(level=logging.WARNING, handlers=[handler])
    else:
        logging.basicConfig(level=logging.WARNING)
    if args.check:
        studio = Studio(
            BackendLease(runtime_root(), os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"))
        )
        library_root = Path(
            os.environ.get(
                "VOICEBOX_LIBRARY_DIR", str(Path.home() / ".local/share/voicebox-studio/library")
            )
        )
        if (library_root / "settings.json").is_file():
            studio.library = StudioLibrary(library_root)
            studio.library.apply_environment()
        state = asyncio.run(studio.status())
        for problem in state["problems"]:
            print(f"NOT READY: {problem}")
        if state["problems"]:
            raise SystemExit(1)
        print("READY: local Studio configuration and selected voice are available.")
        print("Voice source:", state["voice"].get("source", "voicebox"))
        return
    try:
        asyncio.run(serve(args))
    except RuntimeError as error:
        print(f"Cannot start Studio: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
