"""Opt-in native protocol smoke test using generated silence, never a microphone."""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from livekit import rtc
from livekit.agents import APIConnectOptions, stt

from examples.nemotron_stt import NemotronSTT


async def smoke(base_url: str) -> dict[str, object]:
    provider = NemotronSTT(base_url=base_url, finalize_timeout=30)
    async with provider:
        if not await provider.is_ready():
            raise RuntimeError("CPU ASR sidecar is not ready; launch it explicitly first")
        stream = provider.stream(conn_options=APIConnectOptions(max_retry=0, timeout=10))
        started = time.monotonic()
        for _ in range(50):
            stream.push_frame(rtc.AudioFrame(bytes(640), 16000, 1, 320))
        stream.end_input()
        try:
            async with asyncio.timeout(40):
                events = [event async for event in stream]
        finally:
            await stream.aclose()
        usage = [
            event.recognition_usage.audio_duration
            for event in events
            if event.type == stt.SpeechEventType.RECOGNITION_USAGE
            and event.recognition_usage is not None
        ]
        if abs(sum(usage) - 1.0) > 0.001:
            raise RuntimeError("Native commit acknowledgement did not account for synthetic audio")
        return {
            "ready": True,
            "input": "generated PCM16 mono silence; no recording",
            "input_seconds": 1.0,
            "events": [event.type.value for event in events],
            "wall_seconds": round(time.monotonic() - started, 3),
            "usage_seconds": sum(usage),
            "note": "Protocol smoke only; not a speech accuracy or perceived latency benchmark.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(smoke(args.base_url)), indent=2))


if __name__ == "__main__":
    main()
