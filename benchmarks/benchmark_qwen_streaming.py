"""Opt-in direct MLX benchmark: existing weights, authorized voice, no audio files."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import platform
import resource
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

from examples.backend_lease import BackendLease, runtime_root

PHRASES = (
    "Hello. How can I help?",
    "That makes sense. Tell me a little more.",
    "LiveKit connects your browser to the assistant. Voicebox gives the assistant its local voice.",
)


def read_local(base_url: str, path: str, limit: int) -> bytes:
    with urlopen(base_url + path, timeout=10) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError(
            "Local reference metadata/audio exceeds the experiment's allocation bound."
        )
    return content


def reference(base_url: str, selector: str) -> tuple[bytes, str]:
    profiles = json.loads(read_local(base_url, "/profiles", 1024 * 1024))
    matches = [p for p in profiles if p.get("id") == selector]
    if not matches:
        matches = [p for p in profiles if p.get("name") == selector]
    if len(matches) != 1 or matches[0].get("voice_type") != "cloned":
        raise ValueError("Select one exact authorized cloned profile name or ID.")
    samples = json.loads(
        read_local(
            base_url, "/profiles/" + quote(matches[0]["id"], safe="") + "/samples", 1024 * 1024
        )
    )
    if len(samples) != 1:
        raise ValueError(
            "This controlled experiment requires a single-reference profile; "
            "it will not silently select one sample from a multi-sample voice."
        )
    sample = samples[0]
    transcript = sample.get("reference_text")
    if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 4000:
        raise ValueError("The selected reference needs its existing, bounded transcript.")
    audio = read_local(base_url, "/samples/" + quote(sample["id"], safe=""), 16 * 1024 * 1024)
    return audio, transcript


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:17493")
    parser.add_argument("--exclusive-backend", action="store_true")
    parser.add_argument(
        "--output", type=Path, required=True, help="Timing JSON only, never audio/text"
    )
    args = parser.parse_args()
    origin = urlsplit(args.base_url)
    if origin.scheme != "http" or origin.hostname not in ("127.0.0.1", "localhost"):
        parser.error("This experiment only reads the existing loopback Voicebox server.")
    if origin.username or origin.password or origin.query or origin.fragment:
        parser.error("The loopback origin must not contain credentials or query fields.")
    if not args.exclusive_backend:
        parser.error("Stop Studio/other consumers and confirm --exclusive-backend.")
    model_path = args.model_path.resolve()
    if not model_path.is_dir() or not (model_path / "model.safetensors").is_file():
        parser.error("--model-path must point to the complete existing local Qwen snapshot.")

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    lease = BackendLease(runtime_root(), args.base_url)
    lease.acquire()
    try:
        import mlx.core as mx
        import numpy as np
        import soundfile as sf
        from mlx_audio.tts.utils import load_model
        from scipy.signal import resample_poly

        tasks = json.loads(read_local(args.base_url, "/tasks/active", 1024 * 1024))
        if tasks.get("generations") or tasks.get("downloads"):
            raise RuntimeError("Voicebox has tracked work; experiment not started.")
        wav, ref_text = reference(args.base_url, args.profile)
        with sf.SoundFile(io.BytesIO(wav)) as source:
            if (
                not 8000 <= source.samplerate <= 96000
                or not 0 < source.frames <= source.samplerate * 30
                or not 1 <= source.channels <= 2
            ):
                raise ValueError("Reference must be nonempty, <=30 seconds and mono/stereo.")
            wave = source.read(dtype="float32", always_2d=True).mean(axis=1)
            reference_rate = source.samplerate
        if not np.isfinite(wave).all():
            raise ValueError("Reference contains nonfinite samples.")
        if reference_rate != 24000:
            divisor = math.gcd(reference_rate, 24000)
            wave = resample_poly(wave, 24000 // divisor, reference_rate // divisor).astype(
                "float32"
            )
        loaded_at = time.perf_counter()
        model = load_model(str(model_path))
        model_load = time.perf_counter() - loaded_at
        rows = []
        # First invocation includes conditioning; one additional warm-up, then matched samples.
        runs = [
            ("process-cold", PHRASES[0], True, 0.32),
            ("warmup", PHRASES[0], True, 0.32),
            ("batch-control", PHRASES[0], False, 0.32),
            *[("measured", text, True, interval) for interval in (0.32, 0.64) for text in PHRASES],
        ]
        for label, text, streaming, interval in runs:
            mx.random.seed(42)
            started = previous = time.perf_counter()
            first = nonzero = None
            gaps = []
            samples = chunks = 0
            rate = 24000
            for result in model.generate(
                text=text,
                ref_audio=mx.array(wave),
                ref_text=ref_text,
                lang_code="english",
                stream=streaming,
                streaming_interval=interval,
                verbose=False,
                max_tokens=750,
            ):
                audio = np.asarray(result.audio)
                now = time.perf_counter()
                rate = result.sample_rate
                if audio.ndim != 1 or not np.isfinite(audio).all():
                    raise ValueError("Model produced nonfinite audio.")
                if first is None and audio.size:
                    first = now - started
                if nonzero is None and np.any(np.abs(audio) > 0.003):
                    nonzero = now - started
                if chunks:
                    gaps.append(now - previous)
                previous = now
                samples += audio.size
                chunks += 1
                if samples > rate * 60:
                    raise RuntimeError(
                        "Generation exceeded the controlled experiment's audio bound."
                    )
            elapsed = time.perf_counter() - started
            if not samples:
                raise RuntimeError("No audio was generated.")
            row = {
                "kind": label,
                "characters": len(text),
                "stream": streaming,
                "interval_seconds": interval,
                "first_pcm_seconds": first,
                "first_nonzero_pcm_seconds": nonzero,
                "elapsed_seconds": elapsed,
                "audio_seconds": samples / rate,
                "elapsed_rtf": elapsed / (samples / rate),
                "chunks": chunks,
                "max_interchunk_seconds": max(gaps, default=0),
            }
            rows.append(row)
            print(json.dumps(row), flush=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(
                    {
                        "engine": "Qwen 0.6B BF16 direct MLX Audio 0.5.3",
                        "model_revision": model_path.name,
                        "platform": platform.platform(),
                        "model_load_seconds": model_load,
                        "reference_seconds": len(wave) / 24000,
                        "peak_rss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        "audio_saved": False,
                        "speaker_identity": "UNVERIFIED",
                        "results": rows,
                    },
                    indent=2,
                )
                + "\n"
            )
    finally:
        lease.release()


if __name__ == "__main__":
    main()
