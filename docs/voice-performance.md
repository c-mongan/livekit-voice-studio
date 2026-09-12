# Making the local voice conversational

## What actually improved on this machine

The slow part was not the browser or Azure. Voicebox's HTTP route waits for a
complete WAV, and its installed inference path repeatedly does work that a
newer direct MLX path can retain between turns.

We ran MLX Audio **0.5.3** directly with the **same existing Qwen 0.6B BF16
weights and the same authorized reference voice**. No different voice model was
downloaded. The test used `stream=True` and measured actual non-silent PCM chunks,
not HTTP headers or a fake waveform.

| Measurement | Observed |
| --- | --- |
| Model load | 1.95 s |
| First request after process start | 4.81 s to first PCM, excluding model load |
| Warm-up request | 0.42 s to first PCM |
| Three warm requests, 0.32 s chunks | **0.63 / 0.36 / 0.38 s to first non-silent PCM** |
| Same requests: elapsed generation / audio duration | **0.89 / 0.75 / 0.73** |
| Longest gap between those chunks | About 0.22 s |
| Three warm requests, 0.64 s chunks | 0.57 / 0.57 / 0.59 s to first PCM |
| Complete-waveform control | 5.07 s before its first/only waveform |

A ratio below 1 means generation finishes faster than playback consumes the
audio. This matters as much as getting the first chunk early: early chunks alone
would not help if the rest arrived too slowly.

These are six warm samples and one batch control on an Apple M4, not a release
benchmark, a speaker-identity assessment or a guarantee for every PC. The
direct experiment's process RSS peaked around 1.73 GiB; this does not independently
measure all GPU/unified-memory allocations. The separate experiment environment
used about 426 MiB of disk in addition to the already-cached weights.

The path was then integrated into Studio and tested through actual LiveKit rooms:

| Full-path observation | Session 1 | Session 2 |
| --- | --- | --- |
| Typed input → non-silent remote audio | 2.23 s | 2.62 s |
| LLM first token | 1.48 s | 1.55 s |
| TTS first LiveKit frame | 0.48 s | 0.42 s |

Both sessions closed safely and a new room could start. Stop reply was
acknowledged in the second room. The real browser also completed a typed reply,
an interrupted streamed reply and a subsequent reply, with no console errors.
This verifies the implemented flow; it does not establish human-audible
interruption latency or a statistically meaningful performance guarantee.

## Why this path is different

- MLX Audio's real parameter is **`stream=True`**, not `streaming=True`.
- The Qwen implementation decodes audio during its autoregressive loop.
  A 0.32-second interval corresponds to four codec frames, not a promised
  0.32-second wall-clock delay.
- Version 0.5.3 retains reference audio codes and reference-text IDs on the model
  instance. Keeping that model and its voice conditioning alive matters.
- The speech decoder's streaming path also differs from complete-waveform
  decoding. This experiment changes the execution path and library version,
  so the entire improvement cannot be attributed to one cache alone.
- The first turn still pays model/conditioning costs. Subsequent turns reuse
  the instance. Starting a new process for every sentence throws that away.

Studio's optional fast mode moves that preparation to **Start session**: it
loads the existing model and generates a short private warm-up that is neither
played nor saved. It does not announce Ready until preparation completes.
Set `VOICEBOX_MLX_PREWARM=0` to skip the warm-up when measuring cold requests.

The Studio fast path is optional and experimental. It must retain the same
single-worker, bounded-buffer and cancellation ownership guarantees as the HTTP
provider. Closing a consumer must not pretend its inference worker has stopped.

## Which model should we use?

**On this Apple Silicon Mac: keep the existing Qwen 0.6B and use the measured
streaming/cached path first.** It preserves the requested cloning workflow and
does not need new weights. The public Voicebox HTTP adapter remains available.

**For a portable CPU comparison: Pocket TTS is the strongest next candidate.**
Its official README claims around 200 ms to first audio and six times real-time
generation on an M4 CPU using two cores. Those are upstream claims, not our
measurements. The current cloning weights require access approval and declare
CC-BY-4.0; some configurations can fall back to non-cloning weights. Such a
fallback would not satisfy “my own voice” and must never be silently accepted.

Chatterbox Turbo/Nano can clone voices, but the inspected official generation
API returns a completed waveform and has a larger dependency/weight footprint.
Kokoro is a useful fast preset-voice baseline, **not a replacement for cloning
your voice**.

No single model can be promised to run quickly on every PC. Apple Silicon,
Windows CPUs and dedicated GPUs need separate measurements. Voice similarity,
pronunciation, cold start, continuous playback and interruption behavior must
be tested together.

## Enable the experimental Studio fast mode

This mode requires an Apple Silicon Mac and a complete existing local Qwen
0.6B snapshot. It reads either a selected Voicebox profile or an explicitly imported
private voice bundle, then performs inference directly in the local Studio agent.
The normal HTTP plugin is unchanged.

```sh
uv sync --frozen --package livekit-plugins-voicebox \
  --extra dev --extra example --extra azure --extra agents --extra mlx --python 3.12
```

Set these nonsecret values in your private `.env`:

```dotenv
VOICEBOX_TTS_BACKEND=mlx
VOICEBOX_MLX_MODEL_PATH=/absolute/path/to/existing/qwen/snapshot
VOICEBOX_MLX_PREWARM=1
```

To remove the runtime dependency on Voicebox, record and select a voice in
Studio, or run `python -m examples.voice_bundle`
once with an authorized single-reference profile, then configure
`VOICEBOX_VOICE_BUNDLE=/private/path/to/imported/voice`. The bundle contains a
versioned JSON manifest with the transcript and SHA-256 checksum plus one WAV.
Import creates owner-only files and refuses overwrites. Treat the bundle as
sensitive voice data; keep it outside your repository and ordinary shared storage.

Local-bundle mode never calls Voicebox discovery, health, model-status, or sample
endpoints. The existing model snapshot is validated locally and no weights are
copied. Voicebox can be completely stopped.

Two actual room checks with its port confirmed offline measured
**2.49/2.35 seconds from typed input to non-silent remote audio**, and
**0.446/0.436 seconds to the first TTS frame**. Stop and reconnect also passed.
Those timings do not include microphone transcription or detecting a spoken turn end.

Use the normal Studio start command. A session retains its model and voice state
for repeated turns. New sessions prepare their own state again. This first
version requires an authorized **single-reference** profile; it will not silently
choose one sample from a multi-sample voice.
It bypasses Voicebox's profile effects and completed-WAV normalization. Check
the resulting loudness and likeness yourself before relying on it.

To verify standalone operation again, stop Studio first and close Voicebox:

```sh
STUDIO_REQUIRE_VOICEBOX_OFFLINE=1 STUDIO_INTEGRATION=1 \
  uv run --no-sync pytest tests/integration/test_studio_live.py -q -s
```

The test checks that the Voicebox port is closed before and after two actual
rooms, receives non-silent audio, acknowledges Stop, and confirms safe drain.
It does not capture the microphone or save generated recordings.

To return to the original Voicebox HTTP path, end the current session, stop
Studio, set `VOICEBOX_TTS_BACKEND=voicebox`, and start it again. Do not run both
paths concurrently. Both use the same local admission lock.

## Reproduce the direct experiment

Stop Studio and other Voicebox consumers first. This command reads the selected
single-reference profile over loopback and keeps the reference in memory. It
does not save audio or reference text.

```sh
uv sync --project experiments/qwen-streaming --python 3.12
uv run --project experiments/qwen-streaming --no-sync \
  python -m benchmarks.benchmark_qwen_streaming \
  --model-path /absolute/path/to/existing/qwen/snapshot \
  --profile "Your authorized single-reference profile" \
  --exclusive-backend \
  --output /private/path/qwen-timings.json
```

The runner forces Hugging Face/Transformers offline for model loading. Missing
assets are an error, not permission to download another model. The output is
timing metadata only. Stop and investigate any loss of voice likeness, truncated
ending or choppy playback; low numbers alone do not make a good voice.

## Sources

- [MLX Audio 0.5.3 Qwen implementation](https://github.com/Blaizzy/mlx-audio/blob/0d2b681ec1f168397a871cfdf4c88378e68117ac/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py)
- [MLX Audio 0.4.1 streaming implementation](https://github.com/Blaizzy/mlx-audio/blob/3163f1eff4625a545c2abf98759df129fa6e1b56/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py)
- [Pocket TTS source and performance claims](https://github.com/kyutai-labs/pocket-tts/tree/0c2db3bdea7c991c568989cc11b503f14483fabc)
- [Pocket TTS weight access and license](https://huggingface.co/kyutai/pocket-tts)
- [Chatterbox source](https://github.com/resemble-ai/chatterbox/tree/5de7a54aa4e5e2baadb0182dde554908b48b85c2)
