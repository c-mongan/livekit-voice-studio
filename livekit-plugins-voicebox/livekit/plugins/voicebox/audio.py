from __future__ import annotations

import io

import numpy as np
import soundfile as sf
from livekit import rtc
from numpy.typing import NDArray

from .errors import InvalidAudioError
from .models import MAX_WAV_BYTES, OUTPUT_RATES

MAX_AUDIO_SECONDS = 30
MAX_SOURCE_RATE = 96000
MAX_CHANNELS = 2


def decode_pcm(wav: bytes, target_rate: int) -> bytes:
    """Bounded completed-WAV decode, called off the event loop."""
    if target_rate not in OUTPUT_RATES:
        raise InvalidAudioError("Unsupported output sample rate.")
    if not wav or len(wav) > MAX_WAV_BYTES:
        raise InvalidAudioError("WAV body is empty or exceeds 16 MiB.")
    try:
        with sf.SoundFile(io.BytesIO(wav)) as source:
            rate = source.samplerate
            frames = source.frames
            if (
                source.format not in ("WAV", "WAVEX", "RF64")
                or not 8000 <= rate <= MAX_SOURCE_RATE
                or not 1 <= source.channels <= MAX_CHANNELS
                or not 0 < frames <= MAX_AUDIO_SECONDS * rate
            ):
                raise InvalidAudioError(
                    "Expected a nonempty WAV, <=30 seconds, 1-2 channels, 8-96 kHz."
                )
            audio: NDArray[np.float32] = source.read(frames=frames, dtype="float32", always_2d=True)
            if len(audio) != frames or not np.isfinite(audio).all():
                raise InvalidAudioError("WAV contains incomplete or nonfinite audio.")
    except (sf.LibsndfileError, ValueError, OverflowError):
        raise InvalidAudioError("Voicebox returned an invalid WAV container.") from None

    # Float64 accumulation prevents overflow when finite floating WAV samples are extreme.
    mono = np.clip(audio.mean(axis=1, dtype=np.float64), -1.0, 1.0)
    pcm: bytes = (mono * 32767.0).astype("<i2").tobytes()
    if rate == target_rate:
        return pcm

    resampler = rtc.AudioResampler(rate, target_rate, num_channels=1)
    result = bytearray()
    block_bytes = (rate // 50) * 2
    for offset in range(0, len(pcm), block_bytes):
        block = pcm[offset : offset + block_bytes]
        frame = rtc.AudioFrame(block, rate, 1, len(block) // 2)
        for output in resampler.push(frame):
            result.extend(output.data.cast("b"))
    for output in resampler.flush():
        result.extend(output.data.cast("b"))
    if not result or len(result) > (MAX_AUDIO_SECONDS * target_rate + 1) * 2:
        raise InvalidAudioError("Resampled audio exceeds the output duration limit or is empty.")
    return bytes(result)
