"""Opt-in, private comparison of two authorized Qwen reference deliveries."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import secrets
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

SENTENCES = (
    "Oh, really? I had no idea you were coming today.",
    "We finally finished it. Thank you for staying with me.",
    "Take your time. We can work through this together.",
)
MAX_PCM_BYTES = 24000 * 2 * 30
ROOT = Path(__file__).resolve().parents[1]


def private_write(path: Path, value: str) -> None:
    with path.open("x") as handle:
        path.chmod(0o600)
        handle.write(value)


async def render_reference(
    bundle: Path, model: Path, output: Path, label: str
) -> list[dict[str, Any]]:
    """One subprocess owns one model; exit frees it before the next reference."""
    from examples.backend_lease import BackendLease, runtime_root

    lease = BackendLease(runtime_root(), os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"))
    lease.acquire()
    provider = None
    try:
        from examples.fast_qwen import FastQwenTTS

        lease.mark_active()
        provider = FastQwenTTS(profile="Local comparison", model_path=model, voice_bundle=bundle)
        start = time.perf_counter()
        await provider.prepare()
        preparation = time.perf_counter() - start
        samples = []
        for index, text in enumerate(SENTENCES, 1):
            pcm = bytearray()
            started = time.perf_counter()
            first_frame = None
            async with provider.synthesize(text) as stream:
                async for event in stream:
                    if event.frame.sample_rate != 24000 or event.frame.num_channels != 1:
                        raise ValueError("Unexpected audio format.")
                    if first_frame is None:
                        first_frame = time.perf_counter() - started
                    pcm.extend(event.frame.data)
                    if len(pcm) > MAX_PCM_BYTES:
                        raise ValueError("Comparison sample exceeded 30 seconds.")
            elapsed = time.perf_counter() - started
            if not pcm:
                raise ValueError("Comparison returned no audio.")
            sample = f"{label}-{index}"
            with (output / f"{sample}.wav").open("xb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                with wave.open(handle, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(24000)
                    audio.writeframes(bytes(pcm))
            samples.append(
                {
                    "sample": sample,
                    "text": text,
                    "preparationSeconds": preparation,
                    "generationSeconds": elapsed,
                    "firstFrameSeconds": first_frame,
                    "audioSeconds": len(pcm) / 48000,
                }
            )
        return samples
    finally:
        try:
            if provider is not None:
                await provider.aclose()
            lease.mark_safe()
        finally:
            lease.release()


def run_worker(bundle: Path, model: Path, output: Path, label: str) -> list[dict[str, Any]]:
    env = dict(os.environ)
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.expressive_compare",
            "--render-one",
            label,
            "--neutral",
            str(bundle),
            "--model",
            str(model),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=300,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Comparison failed. Check local model, bundle and backend ownership.")
    value: list[dict[str, Any]] = json.loads((output / f"{label}-timing.json").read_text())
    return value


def compare(
    neutral: Path, expressive: Path, model: Path, output: Path, *, render: bool = False
) -> dict[str, Any]:
    summary = {
        "rendered": False,
        "samples": 6,
        "sentences": list(SENTENCES),
        "claim": "Reference delivery experiment; expression requires human listening.",
    }
    if not render:
        return summary
    if output.exists() or output.is_symlink():
        raise ValueError("Choose a new output directory; existing results are never overwritten.")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output.chmod(0o700)
    references = [("neutral", neutral), ("expressive", expressive)]
    secrets.SystemRandom().shuffle(references)
    samples = []
    key = {}
    for label, (style, bundle) in zip(("A", "B"), references, strict=True):
        key[label] = style
        samples.extend(
            run_worker(
                bundle.expanduser().resolve(), model.expanduser().resolve(), output.resolve(), label
            )
        )
    private_write(output / "report.json", json.dumps({"version": 1, "samples": samples}, indent=2))
    private_write(output / "answer-key.json", json.dumps(key))
    worksheet = io.StringIO()
    writer = csv.writer(worksheet)
    writer.writerow(
        [
            "sample",
            "identity_1_to_5",
            "expression_heard",
            "naturalness_1_to_5",
            "words_correct",
            "notes",
        ]
    )
    for index in range(1, 4):
        labels = ["A", "B"]
        secrets.SystemRandom().shuffle(labels)
        for label in labels:
            writer.writerow([f"{label}-{index}", "", "", "", "", ""])
    private_write(output / "listening.csv", worksheet.getvalue())
    return {**summary, "rendered": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--neutral", type=Path, required=True)
    parser.add_argument("--expressive", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--render", action="store_true", help="Explicitly synthesize six local samples."
    )
    parser.add_argument("--render-one", choices=("A", "B"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.render_one:
            # Internal child always acquires the same lease as Studio.
            os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            samples = asyncio.run(
                render_reference(args.neutral, args.model, args.output, args.render_one)
            )
            private_write(args.output / f"{args.render_one}-timing.json", json.dumps(samples))
        else:
            if args.expressive is None:
                parser.error("--expressive is required")
            print(
                json.dumps(
                    compare(
                        args.neutral, args.expressive, args.model, args.output, render=args.render
                    )
                )
            )
        return 0
    except (Exception, KeyboardInterrupt):
        print(
            "Comparison could not complete. Check local prerequisites and backend ownership. "
            "Partial private results are preserved; choose a new output directory to retry.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
