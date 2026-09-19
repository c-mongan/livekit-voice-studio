"""Stock-voice LiveKit learning agent, independent of Studio and private voice files.

Run with ``python -m examples.cloud_agent dev`` after configuring LiveKit Cloud.
Importing this module does not load models, read voice files, or connect to Cloud.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    cli,
    function_tool,
    inference,
)
from livekit.plugins import silero

STAGES = {
    "room": "A room connects participants so they can exchange media tracks and data.",
    "token": "A token gives permission to join a room and publish or subscribe to tracks.",
    "track": "A track carries one stream of media, such as microphone audio or the agent's speech.",
    "stt": "Speech recognition turns microphone audio into words for the language model.",
    "llm": "The language model uses conversation context to generate an answer or request a tool.",
    "tts": "Speech synthesis turns answer text into audio that the agent publishes to the room.",
    "dispatch": "Dispatch asks an available named agent server to handle a session in a room.",
}


def explain_stage(stage: str) -> str:
    """Look up a fixed fact; never execute commands, read files, or call providers."""
    if stage not in STAGES:
        raise ValueError("Choose a stage: room, token, track, stt, llm, tts, or dispatch.")
    return STAGES[stage]


@function_tool(name="explain_stage")
async def explain_stage_tool(stage: str) -> str:
    """Explain one stage: room, token, track, stt, llm, tts, or dispatch."""
    return explain_stage(stage)


class LearningAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You help a beginner understand LiveKit. Keep spoken answers short and concrete. "
                "Use explain_stage when explaining room, token, track, stt, llm, tts, or dispatch. "
                "You use a stock synthetic voice. You cannot access private voices, files, "
                "account settings or live usage. Do not claim to have changed anything."
            ),
            tools=[explain_stage_tool],
        )


def build_session(environ: Mapping[str, str] | None = None) -> AgentSession[None]:
    """Build providers only when a session is dispatched, with explicit model overrides."""
    env = os.environ if environ is None else environ
    return AgentSession(
        stt=inference.STT(model=env.get("LEARNING_STT_MODEL", "deepgram/nova-3"), language="en"),
        llm=inference.LLM(model=env.get("LEARNING_LLM_MODEL", "google/gemma-4-31b-it")),
        tts=inference.TTS(
            model=env.get("LEARNING_TTS_MODEL", "inworld/inworld-tts-2"),
            voice=env.get("LEARNING_TTS_VOICE", "Ashley"),
        ),
        vad=silero.VAD.load(),
        turn_handling={
            "turn_detection": "vad",
            "interruption": {"mode": "vad"},
            "preemptive_generation": {"enabled": False},
        },
    )


server = AgentServer()


@server.rtc_session(agent_name="studio-learning")
async def entrypoint(ctx: JobContext) -> None:
    session = build_session()
    # Do not request session audio recording. Cloud telemetry is a separate account setting.
    await session.start(room=ctx.room, agent=LearningAgent(), record=False)
    # Wait for the learner; no automatic greeting/inference on joining an empty room.


if __name__ == "__main__":
    cli.run_app(server)
