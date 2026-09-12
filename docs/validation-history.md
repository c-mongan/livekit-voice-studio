# Compatibility and evidence

This is `0.1.0.dev0`, an unreleased single-engine technical prototype, not a
production-serving or public-package compatibility guarantee.

## Reproducible adapter environment

| Component | Selected version |
| --- | --- |
| Python | Local checks on 3.11.15, 3.12.12 and 3.13.9 |
| LiveKit Agents | 1.8.1 |
| LiveKit RTC | 1.1.18 (direct pin and `uv.lock`) |
| aiohttp | 3.14.3 |
| NumPy | 2.3.5 (Python 3.11-compatible rather than newest Python-limited release) |
| SoundFile | 0.14.0 |
| Example OpenAI / Silero plugins | 1.8.1 |

All direct runtime dependencies are pinned; `uv.lock` freezes transitive
dependencies and development/example tools. The namespace is
`from livekit.plugins import voicebox`, using Hatch's official namespace layout
and a `py.typed` marker. An importable wheel is not a real-room certification.

## Upstream source review, not installed revision certification

| Source | Reviewed immutable baseline |
| --- | --- |
| Voicebox | `51f49dea198384b4eb6087b72c17057c6eb1c1cd` (also main when inspected) |
| LiveKit Agents | `4de62322fa84b7c1736370ff3ebbd66b5d0ffe6f`; installed public 1.8.1 interfaces re-inspected |
| MLX Audio 0.4.1 | `3163f1eff4625a545c2abf98759df129fa6e1b56` from reviewed plan |

Routes inspected: `/health`, `/profiles`, `/models/status`, `/tasks/active`,
`POST /generate/stream`. Source paths: Voicebox `backend/models.py`,
`backend/routes/generations.py`, `backend/routes/models.py`,
`backend/services/profiles.py`, `backend/backends/mlx_backend.py`; LiveKit
`tts/tts.py`, `tts/stream_adapter.py`, OpenAI and Deepgram TTS plugins.

The installed Voicebox binary reports API version 0.5.0. Its precise source
commit and installed MLX/MLX Audio dependency versions are **unknown**; the source
baseline above must not be substituted for them.

## Deliberate reviewed-plan deviations

1. This validation milestone covered Qwen 0.6B cloned profiles and the minimal
   and Azure examples, not Copilot, UI, other engines or upstream edits.
   Model downloads are never automatic; the validated artifact is recorded below.
2. Cancellation-safe owned HTTP jobs shipped with the typed client, before the
   atomic TTS/audio/cache provider commit; no unsafe semaphore-only generation layer.
3. Added read-only selected-model readiness and activity checks. Health alone
   cannot distinguish loaded 1.7B from the requested 0.6B.
4. `model_size=None` resolves locally to 0.6B. Other sizes/engines fail explicitly.
5. Native `ChunkedStream` finalization flushes audio; no redundant provider flush.
   Agents 1.8.1 native sentence adaptation adds a final 10 ms silence marker.
6. Error responses are discarded rather than echoing arbitrary backend details;
   actionable diagnostics contain only static route/status/configuration guidance.
7. Numeric resource/deadline bounds are explicit prototype policy, not measured
   latency guarantees. See README for defaults and recovery behavior.

## Validation environment and readiness method

The reference environment was Apple M4, 16 GiB RAM, macOS 26.6.2,
Voicebox API 0.5.0, and its reported MLX/MPS backend.

Before synthesis, check selected-model cache visibility and tracked activity.
A loaded model alone does not prove its cached files are available; inconsistent
status requires investigation before proceeding. Missing-model preflight must
fail before any generation POST. Keep profile selections and credentials in
local configuration, and confirm exclusive backend use before room tests.
Do not repair caches, download models, or restart another process implicitly.

Cloned speaker identity requires listening and verification because the upstream
MLX implementation can fall back to an unconditioned voice. BUILD.md section 36
thresholds remain proposals, not approved or measured release limits.

## Validated model artifact

The validated snapshot was
`mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16` at revision
`1eccf1cb2519b5a4e8a95b5f0544f3303568164f`.
All 14 snapshot files passed Hugging Face CLI (`huggingface-hub==1.31.0`)
checksum verification with missing files treated as errors. Voicebox reported
a cached size of 2399.58 MiB. Artifact verification is not an inference test;
the plugin never downloads models automatically.

## Real synthesis observation

One synthetic test line was generated through the actual plugin and LiveKit
`ChunkedStream`, with the selected 0.6B cache visible and no tracked jobs active.

| Observed metric | Single cold-model request |
| --- | --- |
| Emitted audio | 4.0 seconds, 96,000 samples |
| Format | 24,000 Hz, mono PCM16 |
| LiveKit frames | 201 |
| First LiveKit frame | 42.001421 seconds |
| Total consumer elapsed | 42.086643 seconds |
| HTTP first body byte | 41.717408 seconds |
| HTTP complete body | 41.722027 seconds |
| Decode/resample | 0.249550 seconds |
| Model loaded before / after | false / true |

This is one **cold-model** observation, not a warm latency distribution, audible
playback measurement, or release-performance claim. The integration test passed;
no audio recording was saved or played, and no LiveKit room connection occurred.
After synthesis, health reported model_loaded=true and model_size=0.6B.

**Validation boundary:** this check establishes local audio generation and
LiveKit frame conversion, not speaker identity, audible room playback,
interruptions, next-turn recovery or warm conversational latency. Room tests
additionally require standard credentials and confirmed exclusive backend use.

## Local validation achieved

### Azure-backed example follow-up

Validate room connectivity with a read-only LiveKit room-list request without
printing room identities or credential values. The optional example uses
`livekit-plugins-azure==1.8.1` with locked Speech SDK 1.51.2, plus the existing
OpenAI plugin. The provider package remains Azure-independent.

The Azure example uses the signed-in CLI to retrieve resource keys into process
memory only. Authentication support depends on the configured endpoint; this
method is not a general claim about Entra token support. Do not log keys or
write them to configuration. Connectivity validation does not require changing
endpoints or permissions or creating resources.
A short synthetic LLM prompt succeeded against the selected `gpt-4.1-nano`
deployment. A one-second silent stream completed through Azure STT. These
establish endpoint/authentication connectivity, not microphone transcription
accuracy or end-to-end conversational latency.

The worker registered successfully with LiveKit and a temporary validation room
received 24,000 PCM samples at 48 kHz mono (half a second of remote audio) by
5.102 seconds after starting the test, including room and worker startup.
This is not first-frame latency or a conversational benchmark. The temporary
room was removed and no listener-side audio file was written.
Listeners must use the supported `rtc.AudioStream.aclose()` lifecycle rather
than treating the stream as an async context manager.

The example explicitly passes `record=False` instead of inheriting project
recording defaults, and uses local VAD interruption detection. The successful
room check used those explicit settings. Speaker identity, human-audible
playback, real speech recognition accuracy, and interruption/next-turn latency
still require an interactive session.

### Original synthetic suite

103 synthetic tests passed on each of Python 3.11.15, 3.12.12 and 3.13.9.
The primary 3.12 run measured 98% package statement coverage. Ruff lint/format,
strict mypy (provider, example and benchmark), sdist/wheel builds, and a fresh
isolated wheel import with its typing marker passed. CLI help for both example
and benchmark runs without credentials or generation.

Coverage includes 50 sequential synthetic requests, actual LiveKit PCM frames
and native metrics, the native three-sentence adapter, downmix/clip/resampling
tails, snapshots and late profile resolutions, pre-response/output cancellation,
owned drain retention, transport ambiguity, resource limits, bounded shutdown,
external-session ownership, benchmark warm-up/failure accounting and one-room
example admission. These are synthetic observations, not hardware performance
or cloned-audio evidence.

## Minimal existing-cache recovery

```sh
curl --fail --max-time 5 http://127.0.0.1:17493/models/status
```

In the existing Voicebox application, check its configured model cache location
and access to the already downloaded weights. Do not reinstall or download as
part of this adapter's setup. Proceed only when `qwen-tts-0.6B` is reported
`downloaded=true`, `downloading=false`, and no other consumer is generating.
Then select an authorized profile and rerun:

```sh
VOICEBOX_INTEGRATION=1 VOICEBOX_TEST_PROFILE="$VOICEBOX_PROFILE" \
  LK_DUMP_TTS=0 OTEL_SDK_DISABLED=true \
  uv run --no-sync pytest tests/integration/test_voicebox_live.py -q -s
```

This test intentionally fails rather than skipping when enabled but unready.
Room tests additionally require the standard credentials and exclusive use.

## Licenses and redistribution

Adapter code is original MIT-licensed code; no provider implementation was copied.
LiveKit Agents/RTC are Apache-2.0; aiohttp Apache-2.0; NumPy BSD-3-Clause;
SoundFile BSD-3-Clause (its native libsndfile has LGPL terms). Consult installed
artifact notices when redistributing binaries; the adapter license does not
replace dependency licenses.

The reviewed Voicebox application uses MIT, MLX Audio uses MIT, and the model
card for `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16` declares Apache-2.0,
converted from `Qwen/Qwen3-TTS-12Hz-0.6B-Base`. The original cached artifact revision
was unknown; the subsequently validated 0.6B artifact revision is recorded above.
No weights are redistributed by this project.
Record actual model/backend revisions before publishing performance results.
Do not infer licenses or support for TADA or other deferred engines from this
single-model scope.
