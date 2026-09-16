"""One real LiveKit room session, supervised by the local Studio server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any

from livekit import api, rtc
from livekit.agents import Agent, AgentSession, MetricsCollectedEvent, room_io
from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics
from livekit.plugins import voicebox

from examples.fast_qwen import FastQwenTTS
from examples.hermes_llm import HermesLLM
from examples.minimal_agent import configured_ai, configured_provider, provider_choices
from examples.startup_progress import STARTUP_MESSAGES


def report(event: str, **values: Any) -> None:
    try:
        print(
            "STUDIO_EVENT "
            + json.dumps({"key": os.environ["STUDIO_EVENT_KEY"], "event": event, **values}),
            flush=True,
        )
    except BrokenPipeError:
        logging.error("Studio supervisor disconnected; cleanup must still drain owned inference.")


def report_startup(stage: str) -> str:
    if stage not in STARTUP_MESSAGES:
        raise ValueError("Unknown startup stage.")
    report("startup", stage=stage)
    return stage


async def run() -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    room = rtc.Room()
    fast = os.environ.get("VOICEBOX_TTS_BACKEND", "voicebox") == "mlx"
    provider: FastQwenTTS | voicebox.TTS | None = None
    session: AgentSession[None] | None = None
    speech = language_model = None
    safe = True
    stage = report_startup("voice provider configuration")
    owner = os.environ["STUDIO_PARTICIPANT_IDENTITY"]
    interrupted_run_id: str | None = None
    interrupt_lock = asyncio.Lock()

    async def watch_parent() -> None:
        parent_pid = os.getppid()
        while not stop.is_set():
            await asyncio.sleep(2)
            if os.getppid() != parent_pid:
                stop.set()

    parent_watch = asyncio.create_task(watch_parent())

    @room.on("participant_disconnected")
    def participant_left(participant: rtc.RemoteParticipant) -> None:
        if participant.identity == owner:
            stop.set()

    @room.on("disconnected")
    def room_left(reason: rtc.DisconnectReason.ValueType) -> None:
        stop.set()

    def provider_error(error: Any) -> None:
        report("error", message="Voice synthesis failed. Check local model readiness.")
        if provider is not None and provider.backend_state == "uncertain":
            report("uncertain")
            stop.set()

    try:
        provider = (
            FastQwenTTS(
                profile=os.environ.get("VOICEBOX_PROFILE", "Local voice"),
                model_path=os.environ["VOICEBOX_MLX_MODEL_PATH"],
                base_url=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"),
                voice_bundle=os.environ.get("VOICEBOX_VOICE_BUNDLE") or None,
            )
            if fast
            else configured_provider()
        )
        provider.on("error", provider_error)
        stage = report_startup("Voicebox readiness")
        await provider.resolve_profile()
        await provider.check_idle()
        readiness = await provider.model_readiness()
        if not readiness.downloaded or (not fast and not readiness.loaded):
            raise RuntimeError("The selected model must already be cached and loaded.")
        stage = report_startup("speech and language configuration")
        speech, language_model = await configured_ai()
        if provider_choices()[1] in ("copilot", "codex"):
            from examples.agent_llm import AgentLLM

            if isinstance(language_model, AgentLLM):
                stage = report_startup("agent model, authentication and permission validation")
                await language_model.validate()
        if stop.is_set():
            return
        if isinstance(provider, FastQwenTTS):
            stage = report_startup("loading the existing local Qwen model")
            await provider.prepare()
            if os.environ.get("VOICEBOX_MLX_PREWARM", "1") == "1" and not stop.is_set():
                stage = report_startup("preparing the selected voice for fast replies")
                async with provider.synthesize("Hello.") as warmup:
                    async for _ in warmup:
                        if stop.is_set():
                            break
            report("model_loaded")
        if stop.is_set():
            return
        from livekit.plugins import silero

        stage = report_startup("local voice activity detector")
        vad = await asyncio.to_thread(silero.VAD.load)
        if stop.is_set():
            return
        stage = report_startup("conversation session setup")
        session = AgentSession(
            vad=vad,
            stt=speech,
            llm=language_model,
            tts=provider,
            turn_handling={
                "turn_detection": "vad",
                "interruption": {"mode": "vad"},
                "preemptive_generation": {"enabled": False},
            },
        )

        @session.on("metrics_collected")
        def metric(event: MetricsCollectedEvent) -> None:
            value = event.metrics
            if isinstance(value, TTSMetrics):
                report(
                    "metrics",
                    ttsFirstFrameSeconds=value.ttfb if value.ttfb >= 0 else None,
                    ttsAudioSeconds=value.audio_duration,
                )
            elif isinstance(value, EOUMetrics):
                report(
                    "metrics",
                    endOfUtteranceSeconds=value.end_of_utterance_delay,
                    transcriptionDelaySeconds=value.transcription_delay,
                )
            elif isinstance(value, LLMMetrics):
                report("metrics", llmFirstTokenSeconds=value.ttft if value.ttft >= 0 else None)

        @session.on("error")
        def session_error(event: Any) -> None:
            if provider_choices()[1] in ("copilot", "codex") and event.source is language_model:
                # AgentLLM's public errors are already sanitized static diagnostics.
                reason = getattr(event.error, "error", None)
                report("error", message=f"Agent response failed: {str(reason)[:180]}")
                return
            component = (
                "Speech recognition"
                if event.source is speech
                else "Voice synthesis"
                if event.source is provider
                else "Conversation"
            )
            report(
                "error",
                message=f"{component} reported an error. Try ending the session.",
            )

        if provider_choices()[0] == "nemotron":
            from examples.nemotron_stt import NemotronSTT

            @session.on("user_state_changed")
            def finalize_local_utterance(event: Any) -> None:
                if (
                    event.old_state == "speaking"
                    and event.new_state == "listening"
                    and isinstance(speech, NemotronSTT)
                ):
                    speech.commit_utterance()

        async def interrupt(data: rtc.RpcInvocationData) -> str:
            nonlocal interrupted_run_id
            if data.caller_identity != owner:
                raise rtc.RpcError(1403, "Only the session owner can stop this reply.")
            if session is None or provider is None:
                raise rtc.RpcError(1503, "The conversation is not ready.")
            await session.interrupt(force=True)
            hermes_stop_requested = False
            if isinstance(language_model, HermesLLM):
                async with interrupt_lock:
                    run_id = language_model.active_run_id
                    if run_id is not None:
                        if interrupted_run_id != run_id:
                            await language_model.stop_active()
                            interrupted_run_id = run_id
                        hermes_stop_requested = True
            return json.dumps(
                {
                    "stoppedPlayback": True,
                    "hermesStopRequested": hermes_stop_requested,
                    "actionUndone": False,
                    "backendState": provider.backend_state,
                }
            )

        token = (
            api.AccessToken()
            .with_identity(os.environ["STUDIO_AGENT_IDENTITY"])
            .with_name("Voicebox")
            .with_kind("agent")
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=os.environ["STUDIO_ROOM"],
                    agent=True,
                    can_update_own_metadata=True,
                )
            )
            .to_jwt()
        )
        stage = report_startup("LiveKit agent connection")
        await room.connect(os.environ["LIVEKIT_URL"], token)
        room.local_participant.register_rpc_method("voicebox.interrupt", interrupt)
        stage = report_startup("conversation startup")
        await session.start(
            room=room,
            record=False,
            room_options=room_io.RoomOptions(
                participant_identity=owner,
                close_on_disconnect=True,
            ),
            agent=Agent(
                instructions=(
                    "Reply for speech: concise plain text unless detail is needed. "
                    "Hermes owns tools, memory, and approvals. Never claim an action was undone "
                    "because playback stopped."
                )
            ),
        )
        report("ready")
        # No automatic spoken greeting: typed-first users can connect without surprise audio.
        await stop.wait()
    except (Exception, asyncio.CancelledError) as error:
        # Process boundary: emit a static diagnostic, not provider bodies or secrets.
        report("error", message=f"Failed during {stage} ({type(error).__name__}).")
        if isinstance(error, asyncio.CancelledError):
            raise
    finally:
        parent_watch.cancel()
        await asyncio.gather(parent_watch, return_exceptions=True)
        report("draining")
        try:
            if session is not None:
                await session.interrupt(force=True)
                await session.aclose()
        finally:
            try:
                if provider is not None:
                    await provider.aclose()
            except Exception as error:
                safe = False
                report("error", message=f"Backend drain is unresolved ({type(error).__name__}).")
            try:
                if language_model is not None:
                    await language_model.aclose()
                if speech is not None:
                    await speech.aclose()
                await room.disconnect()
            finally:
                report("finished", safe=safe)


if __name__ == "__main__":
    if os.environ.get("VOICEBOX_TTS_BACKEND") == "mlx":
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(run())
