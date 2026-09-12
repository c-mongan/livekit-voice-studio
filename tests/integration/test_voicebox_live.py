"""Opt-in single request: does not establish room playback or speaker identity."""

import json
import os
import time
from dataclasses import asdict

import pytest
from livekit.plugins import voicebox

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VOICEBOX_INTEGRATION") != "1",
        reason="Set VOICEBOX_INTEGRATION=1 to authorize one local synthesis request.",
    ),
]


async def test_local_voicebox_single_request():
    profile = os.environ.get("VOICEBOX_TEST_PROFILE")
    assert profile, "Set VOICEBOX_TEST_PROFILE to an explicitly authorized ID or exact name."
    provider = voicebox.TTS(
        profile=profile,
        base_url=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"),
    )
    try:
        await provider.resolve_profile()
        before = await provider.model_readiness()
        assert before.downloaded and not before.downloading, (
            "Qwen TTS 0.6B must already be cached; this test never downloads models."
        )
        await provider.check_idle()
        started = time.perf_counter()
        first_audio = None
        count = samples = 0
        async with provider.synthesize(
            "This is a synthetic voice test for the local LiveKit integration."
        ) as stream:
            async for event in stream:
                first_audio = first_audio or time.perf_counter() - started
                assert event.frame.sample_rate == 24000
                assert event.frame.num_channels == 1
                assert event.frame.data.itemsize == 2
                count += 1
                samples += event.frame.samples_per_channel
        elapsed = time.perf_counter() - started
        assert count > 0 and samples > 0
        after = await provider.model_readiness()
        print(
            json.dumps(
                {
                    "frames": count,
                    "samples": samples,
                    "sample_rate": 24000,
                    "channels": 1,
                    "format": "PCM16",
                    "audio_seconds": samples / 24000,
                    "elapsed_seconds": elapsed,
                    "first_livekit_frame_seconds": first_audio,
                    "loaded_before": before.loaded,
                    "loaded_after": after.loaded,
                    "timings": asdict(stream.timings),
                    "room_playback": "UNVERIFIED",
                    "speaker_identity": "UNVERIFIED",
                },
                sort_keys=True,
            )
        )
    finally:
        await provider.aclose()
