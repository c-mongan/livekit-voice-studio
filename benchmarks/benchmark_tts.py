"""Sequential, explicitly authorized local benchmark; never writes audio or text."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import statistics
import time
from dataclasses import asdict
from importlib.metadata import version
from typing import Any

from livekit.agents import APIError
from livekit.plugins import voicebox
from livekit.plugins.voicebox.errors import VoiceboxError

PHRASES = {
    "short": "Hello. How can I help?",
    "medium": (
        "That's a good question. Let me explain what is happening and what I would do next."
    ),
    "long": (
        "This is a synthetic voice benchmark, not a recording of a real conversation. "
        "We measure how long a completed local generation takes to reach LiveKit audio frames. "
        "The server finishes its waveform before returning the WAV response, so this is not "
        "incremental model streaming. These results describe this machine and this run only. "
        "Room playback and conversational interruption require separate measurements."
    ),
}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
    for phrase in PHRASES:
        measured = [r for r in rows if r["phrase"] == phrase and not r["warmup"]]
        good = [r for r in measured if r["success"]]
        metrics = {}
        for field in ("elapsed_seconds", "first_frame_seconds", "end_to_end_request_rtf"):
            values = sorted(r[field] for r in good)
            metrics[field] = (
                {
                    "median": statistics.median(values),
                    "p90": values[math.ceil(0.9 * len(values)) - 1],
                    "min": min(values),
                    "max": max(values),
                }
                if values
                else None
            )
        summary[phrase] = {
            "samples": len(measured),
            "failures": len(measured) - len(good),
            **metrics,
        }
    return summary


async def measure(provider: voicebox.TTS, phrase: str, *, warmup: bool) -> dict[str, Any]:
    text = PHRASES[phrase]
    row: dict[str, Any] = {
        "phrase": phrase,
        "characters": len(text),
        "warmup": warmup,
        "success": False,
    }
    stream = None
    started = time.perf_counter()
    first = None
    samples = 0
    try:
        await provider.check_idle()
        readiness = await provider.model_readiness()
        row["loaded_before"] = readiness.loaded
        started = time.perf_counter()
        async with provider.synthesize(text) as stream:
            async for event in stream:
                if first is None:
                    first = time.perf_counter() - started
                samples += event.frame.samples_per_channel
        elapsed = time.perf_counter() - started
        duration = samples / provider.sample_rate
        row.update(
            {
                "success": True,
                "elapsed_seconds": elapsed,
                "first_frame_seconds": first,
                "audio_seconds": duration,
                "end_to_end_request_rtf": elapsed / duration,
            }
        )
    except (APIError, VoiceboxError) as error:
        row.update(
            {
                "elapsed_seconds": time.perf_counter() - started,
                "error_type": type(error).__name__,
                "backend_state": provider.backend_state,
            }
        )
    if stream is not None:
        row["timings"] = asdict(stream.timings)
    return row


async def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    provider = voicebox.TTS(profile=args.profile, base_url=args.base_url)
    rows: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "hardware": args.hardware,
        "os": platform.platform(),
        "python": platform.python_version(),
        "voicebox_version": args.voicebox_version,
        "backend_revision": args.backend_revision,
        "model_revision": args.model_revision,
        "engine": "qwen",
        "model_size": "0.6B",
        "profile_effects": args.profile_effects,
        "power_settings": args.power_settings,
        "concurrent_load": "operator-confirmed exclusive backend",
        "dependencies": {
            name: version(name)
            for name in ("livekit-agents", "livekit", "aiohttp", "numpy", "soundfile")
        },
        "room_metrics": "UNVERIFIED: emitted frames are not audible playback",
        "results": rows,
    }
    try:
        profile = await provider.resolve_profile()
        report["profile_hash"] = hashlib.sha256(profile.id.encode()).hexdigest()[:16]
        health = await provider.health()
        report["backend_type"] = health.raw.get("backend_type", "unknown")
        readiness = await provider.model_readiness()
        report["initial_model_state"] = asdict(readiness)
        if not readiness.downloaded or readiness.downloading:
            raise RuntimeError("Qwen TTS 0.6B must be cached before benchmarking; no downloads.")
        phrases = list(PHRASES) if args.phrase == "all" else [args.phrase]
        stop = False
        for phrase in phrases:
            for index in range(args.iterations + 1):
                row = await measure(provider, phrase, warmup=index == 0)
                rows.append(row)
                if not row["success"]:
                    # A failed warm-up or possible active work is not permission to press on.
                    stop = True
                    break
            if stop:
                break
    finally:
        try:
            await provider.aclose()
        except VoiceboxError as error:
            report["shutdown_error"] = type(error).__name__
    report["summary"] = summarize(rows)
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--profile", default=os.environ.get("VOICEBOX_PROFILE"))
    result.add_argument(
        "--base-url", default=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493")
    )
    result.add_argument("--hardware", required=True, help="Actual machine/chip/RAM label")
    result.add_argument("--voicebox-version", required=True)
    result.add_argument("--backend-revision", default="unknown")
    result.add_argument("--model-revision", default="unknown")
    result.add_argument("--profile-effects", default="unknown (inherits Voicebox profile)")
    result.add_argument("--power-settings", default="unknown")
    result.add_argument(
        "--exclusive-backend", action="store_true", help="Confirm no other consumers"
    )
    result.add_argument("--phrase", choices=["all", *PHRASES], default="all")
    result.add_argument(
        "--iterations", type=int, default=10, help="Measured runs after one warm-up"
    )
    result.add_argument("--json", action="store_true", help="Print structured results to stdout")
    return result


def main() -> None:
    cli = parser()
    args = cli.parse_args()
    if not args.profile or not args.exclusive_backend:
        cli.error("Choose an authorized --profile and confirm --exclusive-backend.")
    if not 1 <= args.iterations <= 1000:
        cli.error("--iterations must be between 1 and 1000.")
    report = asyncio.run(benchmark(args))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("Completed-WAV benchmark: first emitted frame is NOT first audible playback.")
        print(json.dumps(report["summary"], indent=2))
    if report.get("shutdown_error") or any(not row["success"] for row in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
