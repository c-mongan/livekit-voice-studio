"""One real LiveKit room session, supervised by the local Studio server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
from typing import Any

from livekit import api, rtc
from livekit.agents import Agent, AgentSession, MetricsCollectedEvent, room_io
from livekit.agents.metrics import EOUMetrics, LLMMetrics, TTSMetrics
from livekit.plugins import voicebox

from examples.fast_qwen import FastQwenTTS
from examples.minimal_agent import configured_ai, configured_provider, provider_choices
from examples.startup_progress import STARTUP_MESSAGES
from examples.turn_handling import (
    InvalidTurnDetection,
    LocalTurnDetectionUnavailable,
    configured_turn_handling,
)


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


def local_speech_error(error: Any) -> str:
    """Only known adapter messages may cross the private-worker boundary."""
    messages = {
        "Nemotron connection timed out": (
            "Local speech connection timed out. End the session and retry."
        ),
        "Nemotron finalization timed out": (
            "Local speech finalization timed out. End the session and retry."
        ),
        "Nemotron input buffer overloaded": (
            "Local speech input buffer filled up. End the session and reduce system load."
        ),
        "Nemotron event buffer overloaded": (
            "Local speech event buffer filled up. End the session and retry."
        ),
        "Nemotron sidecar rejected the stream": (
            "Local speech service rejected the audio stream. End the session and retry."
        ),
        "Nemotron sidecar disconnected": (
            "Local speech connection closed unexpectedly. End the session and retry."
        ),
    }
    for phase in ("connection", "handshake", "streaming", "audio send", "finalization"):
        messages[f"Nemotron input buffer overloaded during {phase}"] = (
            f"Local speech input buffer filled up during {phase}. "
            "End the session and reduce system load."
        )
    reason = getattr(error, "message", None)
    if isinstance(reason, str) and reason in messages:
        return messages[reason]
    return "Speech recognition reported an error. Try ending the session."


def conversation_instructions(language_model: Any) -> str:
    """Describe the actual configured adapter without copying credentials or endpoints."""
    provider = provider_choices()[1]
    if provider not in ("ollama", "openai-compatible", "openai", "azure", "copilot", "codex"):
        provider = "not reported"
    model = getattr(language_model, "model", None)
    if (
        not isinstance(model, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}", model) is None
        or "://" in model
    ):
        model = "not reported"
    identity = json.dumps({"reasoning_provider": provider, "configured_model": model})
    return (
        "You are the assistant in LiveKit Voice Studio. "
        f"Your runtime configuration is {identity}. "
        "When asked what model you are, report this configured provider and model. "
        "This is a configured identifier, not independent verification of the weights. "
        "Do not invent a model version, creator, hosting location or capabilities. "
        "If a field is not reported, say you do not know it. "
        "You are a helpful conversational assistant, not a coding agent. "
        "Use one or two short sentences in plain text, usually under 40 words. "
        "Respond to the latest point instead of repeating greetings "
        "or stock acknowledgements. "
        "For casual chat, offer one relevant thought or gentle follow-up when useful; "
        "do not ask a question every turn. A brief okay or thanks is not necessarily "
        "a goodbye; only close the conversation when the user clearly ends it. "
        "If interrupted, follow the user’s new direction "
        "without finishing the old reply. "
        "Do not use markdown or lists unless asked. Do not claim actions or access "
        "to files or tools you do not have. Your speech is synthetic."
    )


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
        turn_handling = await asyncio.to_thread(configured_turn_handling)
        if stop.is_set():
            return
        session = AgentSession(
            vad=vad,
            stt=speech,
            llm=language_model,
            tts=provider,
            turn_handling=turn_handling,
        )

        reply_sequence = 0

        @session.on("speech_created")
        def speech_created(event: Any) -> None:
            nonlocal reply_sequence
            reply_sequence += 1
            sequence = reply_sequence
            report("reply_started", sequence=sequence)

            def completed(handle: Any) -> None:
                report("reply_completed", sequence=sequence, interrupted=handle.interrupted)

            event.speech_handle.add_done_callback(completed)

        @session.on("metrics_collected")
        def metric(event: MetricsCollectedEvent) -> None:
            value = event.metrics
            if isinstance(value, TTSMetrics):
                report(
                    "metrics",
                    speechId=value.speech_id,
                    ttsFirstFrameSeconds=value.ttfb if value.ttfb >= 0 else None,
                    ttsAudioSeconds=value.audio_duration,
                )
            elif isinstance(value, EOUMetrics):
                report(
                    "metrics",
                    speechId=value.speech_id,
                    endOfUtteranceSeconds=value.end_of_utterance_delay,
                    transcriptionDelaySeconds=value.transcription_delay,
                )
            elif isinstance(value, LLMMetrics):
                report(
                    "metrics",
                    speechId=value.speech_id,
                    llmFirstTokenSeconds=value.ttft if value.ttft >= 0 else None,
                )

        @session.on("error")
        def session_error(event: Any) -> None:
            if provider_choices()[1] in ("copilot", "codex") and event.source is language_model:
                # AgentLLM's public errors are already sanitized static diagnostics.
                reason = getattr(event.error, "error", None)
                report("error", message=f"Agent response failed: {str(reason)[:180]}")
                return
            if provider_choices()[0] == "nemotron" and event.source is speech:
                report("error", message=local_speech_error(getattr(event.error, "error", None)))
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
            if data.caller_identity != owner:
                raise rtc.RpcError(1403, "Only the session owner can stop this reply.")
            if session is None or provider is None:
                raise rtc.RpcError(1503, "The conversation is not ready.")
            await session.interrupt(force=True)
            return json.dumps({"stopped": True, "backendState": provider.backend_state})

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
            agent=Agent(instructions=conversation_instructions(language_model)),
        )
        report("ready")
        # No automatic spoken greeting: typed-first users can connect without surprise audio.
        await stop.wait()
    except (Exception, asyncio.CancelledError) as error:
        # Process boundary: emit a static diagnostic, not provider bodies or secrets.
        message = (
            str(error)
            if isinstance(error, (InvalidTurnDetection, LocalTurnDetectionUnavailable))
            else f"Failed during {stage} ({type(error).__name__})."
        )
        report("error", message=message)
        if isinstance(error, asyncio.CancelledError):
            raise
    finally:
        parent_watch.cancel()
        await asyncio.gather(parent_watch, return_exceptions=True)
        report("draining")
        try:
            if session is not None:
                # aclose already interrupts and drains. A separate interrupt can
                # raise if room disconnect closed the session before SIGTERM.
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
