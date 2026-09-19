"""Explicit local turn detection, independent of cloud inference defaults."""

from __future__ import annotations

import os
from collections.abc import Mapping

from livekit.agents.voice.turn import TurnHandlingOptions


class InvalidTurnDetection(ValueError):
    """A fixed public configuration diagnostic."""


class LocalTurnDetectionUnavailable(RuntimeError):
    """A fixed public native-runtime diagnostic."""


def configured_turn_handling(environ: Mapping[str, str] | None = None) -> TurnHandlingOptions:
    """Called only during conversation startup, never by setup diagnostics.

    Agents 1.8.1's v1-mini uses the bundled livekit-local-inference runtime.
    init_eot initializes its native singleton (about 108 MB); it is not a download.
    """
    env = os.environ if environ is None else environ
    mode = env.get("VOICEBOX_TURN_DETECTION", "vad")
    if mode not in ("vad", "audio-local"):
        raise InvalidTurnDetection("VOICEBOX_TURN_DETECTION must be vad or audio-local.")
    options: TurnHandlingOptions = {
        "turn_detection": "vad",
        "interruption": {"mode": "vad"},
        "preemptive_generation": {"enabled": False},
    }
    if mode == "audio-local":
        try:
            from livekit import local_inference
            from livekit.agents import inference

            local_inference.init_eot()
            options["turn_detection"] = inference.TurnDetector(version="v1-mini")
        except (ImportError, OSError, RuntimeError, ValueError):
            raise LocalTurnDetectionUnavailable(
                "Local audio turn detection is unavailable. Repair the installed LiveKit "
                "local inference runtime or use VOICEBOX_TURN_DETECTION=vad."
            ) from None
    return options
