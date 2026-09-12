import io

import numpy as np
import pytest
import soundfile as sf
from conftest import make_wav
from livekit.plugins.voicebox.audio import decode_pcm
from livekit.plugins.voicebox.errors import InvalidAudioError


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 44100, 48000, 96000])
def test_resample_and_tail(rate):
    pcm = decode_pcm(make_wav(rate=rate, seconds=0.2), 24000)
    assert abs(len(pcm) // 2 - 4800) <= 1
    assert np.frombuffer(pcm, dtype="<i2").max() > 7000


def test_stereo_mean():
    buffer = io.BytesIO()
    sf.write(buffer, np.tile([0.5, -0.5], (2400, 1)), 24000, format="WAV", subtype="FLOAT")
    pcm = decode_pcm(buffer.getvalue(), 24000)
    assert len(pcm) == 4800
    assert not np.frombuffer(pcm, dtype="<i2").any()


@pytest.mark.parametrize("value,expected", [(2.0, 32767), (-2.0, -32767), (0, 0)])
def test_pcm_clipping(value, expected):
    pcm = decode_pcm(make_wav(value=value, subtype="FLOAT"), 24000)
    assert np.all(np.frombuffer(pcm, dtype="<i2") == expected)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite(value):
    with pytest.raises(InvalidAudioError):
        decode_pcm(make_wav(value=value, subtype="FLOAT"), 24000)


@pytest.mark.parametrize(
    "wav",
    [
        b"",
        b"not audio",
        make_wav(seconds=0),
        make_wav(channels=3),
        make_wav(rate=192000),
        make_wav(seconds=30.01),
    ],
)
def test_audio_bounds(wav):
    with pytest.raises(InvalidAudioError):
        decode_pcm(wav, 24000)
