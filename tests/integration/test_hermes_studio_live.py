"""Authorized Hermes/LiveKit/local-speech vertical-slice verification.

This module is inert unless every prerequisite below is explicitly supplied in the
process environment. It never loads ``.env``. The live test uses paid/external
services, a local synthetic speech fixture, and an authorized local voice bundle.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from livekit import rtc

from examples.backend_lease import BackendLease, runtime_root
from examples.studio import Studio

_OPT_IN = "HERMES_STUDIO_LIVE"
_REQUIRED_VALUES = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "HERMES_API_BASE_URL",
    "HERMES_API_SERVER_KEY",
    "HERMES_PROFILE",
    "VOICEBOX_LLM_PROVIDER",
    "VOICEBOX_STT_PROVIDER",
    "VOICEBOX_TTS_BACKEND",
    "VOICEBOX_MLX_MODEL_PATH",
    "VOICEBOX_VOICE_BUNDLE",
    "NEMOTRON_SERVER_BINARY",
    "NEMOTRON_MODEL_PATH",
    "HERMES_STUDIO_TEST_AUDIO",
    "HERMES_STUDIO_AUDIO_EXPECT",
)
_REQUIRED_FILES = (
    "NEMOTRON_SERVER_BINARY",
    "NEMOTRON_MODEL_PATH",
    "HERMES_STUDIO_TEST_AUDIO",
)
_REQUIRED_DIRECTORIES = ("VOICEBOX_MLX_MODEL_PATH", "VOICEBOX_VOICE_BUNDLE")


@dataclass(frozen=True)
class _ObservedTranscription:
    observed_at: float
    participant_identity: str
    text: str
    final: bool


@dataclass(frozen=True)
class _ObservationBoundary:
    observed_at: float
    transcript_count: int
    audio_count: int


@dataclass
class _ObservationLog:
    transcriptions: list[_ObservedTranscription] = field(default_factory=list)
    nonzero_audio_at: list[float] = field(default_factory=list)
    tool_statuses: list[dict[str, Any]] = field(default_factory=list)

    @property
    def transcript_count(self) -> int:
        return len(self.transcriptions)

    def record_transcription(
        self, observed_at: float, participant_identity: str, text: str, *, final: bool
    ) -> None:
        self.transcriptions.append(
            _ObservedTranscription(observed_at, participant_identity, text, final)
        )

    def record_nonzero_audio(self, observed_at: float) -> None:
        self.nonzero_audio_at.append(observed_at)

    def record_tool_status(self, status: dict[str, Any]) -> None:
        self.tool_statuses.append(status.copy())

    def boundary(self, observed_at: float) -> _ObservationBoundary:
        return _ObservationBoundary(
            observed_at,
            transcript_count=len(self.transcriptions),
            audio_count=len(self.nonzero_audio_at),
        )


def _assistant_text(observations: _ObservationLog, since: int, identity: str) -> str:
    return "".join(
        event.text
        for event in observations.transcriptions[since:]
        if event.participant_identity == identity
    )


def _streamed_assistant_text_observed(
    observations: _ObservationLog, since: int, identity: str, expected: str
) -> bool:
    non_final_text = "".join(
        event.text
        for event in observations.transcriptions[since:]
        if event.participant_identity == identity and not event.final
    )
    return expected.casefold() in non_final_text.casefold()


def _completed_tool_status_observed(observations: _ObservationLog, marker: str) -> bool:
    return any(
        status.get("phase") == "completed"
        and status.get("error") is False
        and isinstance(status.get("preview"), str)
        and marker.casefold() in status["preview"].casefold()
        for status in observations.tool_statuses
    )


def _retired_text_absent_after(
    observations: _ObservationLog,
    boundary: _ObservationBoundary,
    agent_identity: str,
    marker: str,
) -> bool:
    return not any(
        event.participant_identity == agent_identity and marker.casefold() in event.text.casefold()
        for event in observations.transcriptions[boundary.transcript_count :]
    )


def _assert_interruption_evidence(
    observations: _ObservationLog,
    retired_start: _ObservationBoundary,
    boundary: _ObservationBoundary,
    *,
    audio_end: _ObservationBoundary | None = None,
    agent_identity: str,
    marker: str,
    silence_limit_seconds: float,
) -> float:
    before = observations.transcriptions[retired_start.transcript_count : boundary.transcript_count]
    assert any(
        event.participant_identity == agent_identity
        and not event.final
        and marker.casefold() in event.text.casefold()
        for event in before
    ), "The retired-run marker in non-final output was not observed before interruption."
    assert boundary.audio_count > retired_start.audio_count, (
        "No retired-run audio was observed before interruption."
    )

    assert _retired_text_absent_after(observations, boundary, agent_identity, marker), (
        "Observed retired Hermes text after the stop boundary."
    )

    audio_end_count = audio_end.audio_count if audio_end is not None else None
    post_stop_audio = observations.nonzero_audio_at[boundary.audio_count : audio_end_count]
    stop_to_silence = (
        max(0.0, max(post_stop_audio) - boundary.observed_at) if post_stop_audio else 0.0
    )
    assert stop_to_silence <= silence_limit_seconds, (
        "Observed retired reply audio after the stop-to-silence gate."
    )
    return stop_to_silence


def _live_skip_reason() -> str | None:
    if os.environ.get(_OPT_IN) != "1":
        return f"Set {_OPT_IN}=1 only after authorizing real Hermes and LiveKit usage."
    missing = [name for name in _REQUIRED_VALUES if not os.environ.get(name, "").strip()]
    if missing:
        return "Missing explicit live-test settings: " + ", ".join(missing)
    expected_modes = {
        "VOICEBOX_LLM_PROVIDER": "hermes",
        "VOICEBOX_STT_PROVIDER": "nemotron",
        "VOICEBOX_TTS_BACKEND": "mlx",
    }
    wrong_modes = [name for name, value in expected_modes.items() if os.environ[name] != value]
    if wrong_modes:
        return "Live-test provider settings are not the required local Hermes slice: " + ", ".join(
            wrong_modes
        )
    wrong_files = [name for name in _REQUIRED_FILES if not Path(os.environ[name]).is_file()]
    wrong_directories = [
        name for name in _REQUIRED_DIRECTORIES if not Path(os.environ[name]).is_dir()
    ]
    if wrong_files or wrong_directories:
        return "Live-test model/audio paths do not exist: " + ", ".join(
            wrong_files + wrong_directories
        )
    return None


_SKIP_REASON = _live_skip_reason()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "live test disabled"),
]


def _read_authorized_audio(path: str) -> tuple[bytes, int]:
    data = Path(path).read_bytes()
    if len(data) > 4 * 1024 * 1024:
        raise AssertionError("The synthetic input fixture must be no larger than 4 MiB.")
    with sf.SoundFile(io.BytesIO(data)) as source:
        if not (8_000 <= source.samplerate <= 48_000 and 1 <= source.channels <= 2):
            raise AssertionError("The synthetic input must be 8–48 kHz mono or stereo.")
        if not (0 < source.frames <= source.samplerate * 15):
            raise AssertionError("The synthetic input must be at most 15 seconds.")
        wave = source.read(dtype="float32", always_2d=True).mean(axis=1)
        rate = source.samplerate
    pcm = (np.clip(wave, -1, 1) * 32767).astype("<i2").tobytes()
    resampler = rtc.AudioResampler(rate, 16_000, num_channels=1)
    frames = resampler.push(rtc.AudioFrame(pcm, rate, 1, len(pcm) // 2)) + resampler.flush()
    return b"".join(frame.data.tobytes() for frame in frames), 16_000


def _p95(values: list[float]) -> float:
    if len(values) < 20:
        raise AssertionError("The Qwen warm P95 gate requires at least 20 measured turns.")
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


async def _wait_until(predicate: Any, *, seconds: float, message: str) -> None:
    async with asyncio.timeout(seconds):
        while not predicate():
            tick = asyncio.Event()
            handle = asyncio.get_running_loop().call_later(0.05, tick.set)
            try:
                await tick.wait()
            finally:
                handle.cancel()
    assert predicate(), message


async def test_continuous_hermes_room_tools_approval_interrupt_continuity_and_drain(
    tmp_path: Path,
) -> None:
    """Exercise the release slice; run only with separate live-service authorization."""
    assert os.environ["VOICEBOX_LLM_PROVIDER"] == "hermes"
    assert os.environ["VOICEBOX_STT_PROVIDER"] == "nemotron"
    assert os.environ["VOICEBOX_TTS_BACKEND"] == "mlx"
    measured_turns = int(os.environ.get("HERMES_STUDIO_MEASURED_TURNS", "20"))
    assert 20 <= measured_turns <= 30

    fixture_marker = "HERMES-LIVE-FIXTURE-7F31"
    memory_word = "violet"
    fixture = tmp_path / "harmless-read-fixture.txt"
    fixture.write_text(fixture_marker + "\n", encoding="utf-8")
    denied_target = tmp_path / "denied-command-must-not-run"
    synthetic_pcm, sample_rate = _read_authorized_audio(os.environ["HERMES_STUDIO_TEST_AUDIO"])

    lease = BackendLease(runtime_root(), os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"))
    lease.acquire()
    studio = Studio(lease)
    watchdog = asyncio.create_task(studio.watchdog())
    details: dict[str, Any] | None = None
    room = rtc.Room()
    agent_identity = ""
    owner_identity = ""
    audio_tasks: list[asyncio.Task[None]] = []
    observations = _ObservationLog()
    approvals: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    resolutions: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    nonzero_audio = asyncio.Event()
    last_nonzero_audio = 0.0
    source: rtc.AudioSource | None = None

    async def consume_audio(track: rtc.Track) -> None:
        nonlocal last_nonzero_audio
        stream = rtc.AudioStream(track)
        try:
            async for event in stream:
                if any(abs(sample) > 100 for sample in event.frame.data):
                    last_nonzero_audio = time.perf_counter()
                    observations.record_nonzero_audio(last_nonzero_audio)
                    nonzero_audio.set()
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def track_subscribed(
        track: rtc.Track,
        _publication: rtc.RemoteTrackPublication,
        _participant: rtc.RemoteParticipant,
    ) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            audio_tasks.append(asyncio.create_task(consume_audio(track)))

    @room.on("transcription_received")
    def transcription_received(transcription: rtc.Transcription) -> None:
        now = time.perf_counter()
        for segment in transcription.segments:
            observations.record_transcription(
                now,
                transcription.participant_identity,
                segment.text,
                final=segment.final,
            )

    @room.on("data_received")
    def data_received(packet: rtc.DataPacket) -> None:
        if packet.participant is None or details is None:
            return
        if packet.participant.identity != details["agentIdentity"]:
            return
        if packet.topic not in {
            "hermes.approval.request",
            "hermes.approval.resolved",
            "hermes.tool.status",
        }:
            return
        try:
            payload = json.loads(packet.data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            if packet.topic == "hermes.tool.status":
                observations.record_tool_status(payload)
                return
            queue = approvals if packet.topic == "hermes.approval.request" else resolutions
            queue.put_nowait(payload)

    def assistant_text(since: int = 0) -> str:
        return _assistant_text(observations, since, agent_identity)

    async def send_and_wait(prompt: str, expected: str, *, seconds: float = 60) -> float:
        start_index = observations.transcript_count
        started = time.perf_counter()
        await room.local_participant.send_text(prompt, topic="lk.chat")
        await _wait_until(
            lambda: _streamed_assistant_text_observed(
                observations, start_index, agent_identity, expected
            ),
            seconds=seconds,
            message=(
                f"Assistant speech text did not stream a non-final segment containing {expected!r}."
            ),
        )
        return started

    async def wait_listening() -> None:
        await _wait_until(
            lambda: any(
                participant.identity == details["agentIdentity"]
                and participant.attributes.get("lk.agent.state") == "listening"
                for participant in room.remote_participants.values()
            ),
            seconds=60,
            message="Agent did not return to listening.",
        )

    async def heartbeat() -> None:
        assert details is not None
        while True:
            studio.heartbeat(details["sessionId"])
            await asyncio.sleep(10)

    heartbeat_task: asyncio.Task[None] | None = None
    report: dict[str, Any] = {}
    try:
        details = await studio.create()
        agent_identity = details["agentIdentity"]
        assert studio.current is not None
        owner_identity = studio.current.participant
        heartbeat_task = asyncio.create_task(heartbeat())
        await room.connect(details["serverUrl"], details["participantToken"])
        await _wait_until(
            lambda: studio.phase in {"active", "blocked", "idle"},
            seconds=150,
            message="Studio room did not finish startup.",
        )
        assert studio.phase == "active", studio.message

        # Deterministic text and session-memory seed.
        await send_and_wait(
            f"Remember the word {memory_word}. Reply only with READY-{memory_word.upper()}.",
            f"READY-{memory_word.upper()}",
        )
        await wait_listening()

        # A harmless real Hermes tool read. The expected marker is not present in the prompt.
        await send_and_wait(
            f"Use your file-reading tool to read {fixture}. Reply only with the file contents.",
            fixture_marker,
        )
        await wait_listening()
        await _wait_until(
            lambda: _completed_tool_status_observed(observations, fixture_marker),
            seconds=15,
            message="No successful completed Hermes tool status reported the fixture result.",
        )

        # The selected test profile must require approval for this sandboxed fixture command.
        await room.local_participant.send_text(
            f"Use your command tool to run exactly: /usr/bin/touch {denied_target}", topic="lk.chat"
        )
        async with asyncio.timeout(30):
            approval = await approvals.get()
        assert "deny" in approval.get("choices", [])
        response = await room.local_participant.perform_rpc(
            destination_identity=details["agentIdentity"],
            method="hermes.approval.respond",
            payload=json.dumps(
                {
                    "runId": approval["runId"],
                    "requestId": approval["requestId"],
                    "choice": "deny",
                }
            ),
        )
        assert json.loads(response) == {"accepted": True}
        async with asyncio.timeout(30):
            resolution = await resolutions.get()
        assert resolution == {
            "runId": approval["runId"],
            "requestId": approval["requestId"],
        }
        await wait_listening()
        assert not denied_target.exists()

        # Exercise Nemotron using an explicitly authorized synthetic recording.
        source = rtc.AudioSource(sample_rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track("authorized-synthetic-input", source)
        await room.local_participant.publish_track(
            track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        )
        spoken_start = observations.transcript_count
        for offset in range(0, len(synthetic_pcm), 640):
            block = synthetic_pcm[offset : offset + 640]
            await source.capture_frame(rtc.AudioFrame(block, sample_rate, 1, len(block) // 2))
            await asyncio.sleep(0.02)
        await source.wait_for_playout()
        await _wait_until(
            lambda: os.environ["HERMES_STUDIO_AUDIO_EXPECT"].casefold()
            in "".join(
                event.text
                for event in observations.transcriptions[spoken_start:]
                if event.participant_identity == owner_identity and event.final
            ).casefold(),
            seconds=30,
            message="Nemotron final transcript did not contain the expected synthetic phrase.",
        )
        await wait_listening()
        assert studio.metrics["endOfUtteranceSeconds"] is not None
        assert studio.metrics["transcriptionDelaySeconds"] is not None

        # Warm Qwen timings: the design gate is meaningful only after 20 measured turns.
        warm_ttfb: list[float] = []
        for turn in range(measured_turns):
            studio.metrics["ttsFirstFrameSeconds"] = None
            marker = f"WARM-{turn:02d}"
            await send_and_wait(f"Reply only with {marker}.", marker)
            await wait_listening()
            await _wait_until(
                lambda: studio.metrics["ttsFirstFrameSeconds"] is not None,
                seconds=15,
                message="Qwen did not report first-frame latency.",
            )
            first_frame = studio.metrics["ttsFirstFrameSeconds"]
            assert first_frame is not None
            warm_ttfb.append(first_frame)
        warm_p95 = _p95(warm_ttfb)
        assert warm_p95 <= 0.350

        # Interrupt a distinctive long reply and prove playback stop is not action undo.
        await _wait_until(
            lambda: time.perf_counter() - last_nonzero_audio > 0.5,
            seconds=5,
            message="Previous reply audio did not become quiet before interruption probe.",
        )
        nonzero_audio.clear()
        retired_start = observations.boundary(time.perf_counter())
        before_long = observations.transcript_count
        await room.local_participant.send_text(
            "Give a long response containing the token RETIRE-ME in every sentence.",
            topic="lk.chat",
        )
        await _wait_until(
            lambda: nonzero_audio.is_set()
            and _streamed_assistant_text_observed(
                observations, before_long, agent_identity, "RETIRE-ME"
            ),
            seconds=45,
            message="The long reply did not stream RETIRE-ME with audible output before Stop.",
        )
        stop_boundary = observations.boundary(time.perf_counter())
        interrupt = json.loads(
            await room.local_participant.perform_rpc(
                destination_identity=details["agentIdentity"],
                method="voicebox.interrupt",
                payload="{}",
            )
        )
        assert interrupt["stoppedPlayback"] is True
        assert interrupt["hermesStopRequested"] is True
        assert interrupt["hermesTerminalAcknowledged"] is True
        assert interrupt["actionUndone"] is False
        await asyncio.sleep(0.25)

        # Same Hermes session must retain memory while stale text remains monitored.
        continuity_start = observations.boundary(time.perf_counter())
        await send_and_wait(
            "What word did I ask you to remember? Reply only with that word.", memory_word
        )
        await wait_listening()
        continuity_text = assistant_text(continuity_start.transcript_count)
        assert memory_word.casefold() in continuity_text.casefold()
        stop_to_silence = _assert_interruption_evidence(
            observations,
            retired_start,
            stop_boundary,
            audio_end=continuity_start,
            agent_identity=agent_identity,
            marker="RETIRE-ME",
            silence_limit_seconds=0.2,
        )
        action_truth_observed = _completed_tool_status_observed(observations, fixture_marker)
        no_retired_deltas = _retired_text_absent_after(
            observations, stop_boundary, agent_identity, "RETIRE-ME"
        )
        assert action_truth_observed
        assert no_retired_deltas

        assert studio.metrics["llmFirstTokenSeconds"] is not None
        assert studio.metrics["ttsFirstFrameSeconds"] is not None
        report = {
            "hermes_live_vertical_slice": {
                "approval_denied": True,
                "completed_action_reported": action_truth_observed,
                "continuity_verified": True,
                "end_of_utterance_seconds": studio.metrics["endOfUtteranceSeconds"],
                "hermes_terminal_after_stop": interrupt["hermesTerminalAcknowledged"],
                "llm_first_text_delta_seconds": studio.metrics["llmFirstTokenSeconds"],
                "measured_qwen_turns": len(warm_ttfb),
                "nemotron_final_transcription_seconds": studio.metrics["transcriptionDelaySeconds"],
                "no_retired_deltas": no_retired_deltas,
                "qwen_first_audio_p95_seconds": round(warm_p95, 6),
                "stop_to_silence_seconds": round(stop_to_silence, 6),
            }
        }
        print(json.dumps(report, sort_keys=True))
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if details is not None and studio.current is not None:
            await studio.end(details["sessionId"])
        await room.disconnect()
        if source is not None:
            await source.aclose()
        for task in audio_tasks:
            task.cancel()
        await asyncio.gather(*audio_tasks, return_exceptions=True)
        await _wait_until(
            lambda: studio.phase in {"idle", "blocked"},
            seconds=160,
            message="Studio did not finish draining.",
        )
        watchdog.cancel()
        await asyncio.gather(watchdog, return_exceptions=True)
        await studio.close()
        lease.release()

    assert studio.phase == "idle", studio.message
    assert studio.current is None
    assert not studio.lease.marker.exists()
    assert studio.recognizer_service is None or studio.recognizer_service.process is None
    assert report
