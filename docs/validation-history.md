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

1. Only Qwen 0.6B cloned profiles are implemented. Azure was initially deferred
   and later explicitly requested for the example; Copilot, UI, other engines,
   publication and upstream edits remain out of scope. Model downloads are
   never automatic and the one operator-approved restoration is recorded below.
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

## Actual local observations

Read-only probes on 2026-09-11 (local time) identified Apple M4, 16 GiB RAM,
macOS 26.6.2, Voicebox API 0.5.0, MLX with MPS. Initial model status reported
Qwen TTS 0.6B cached but not loaded, and 1.7B cached and loaded.

At the explicitly authorized smoke attempt, the same API reported **0.6B
downloaded=false and loaded=false**. A follow-up read confirmed that, with 1.7B
still loaded but also reported downloaded=false, and health.model_downloaded
null. This establishes inconsistent/changing cache visibility, not proven loss
of weights. No attempt was made to alter cache configuration or scan personal
model directories.

The smoke test failed in preflight, **before any generation POST**. Active tracked
generation/download counts were zero. No speech was generated or retained; no
models were downloaded, explicitly loaded, stopped, or reconfigured. Therefore
there are no measured real first-frame/request/audio-duration results to report.
The user-authorized profile selection remains local and is not committed.
The final read-only check at `2026-09-10T23:20:39.766776+00:00` again reported
0.6B downloaded=false, loaded=false, downloading=false, with no tracked activity.

A subsequent check of the API-reported cache confirmed both MLX Qwen
directories were absent and the 1.7B alias was dangling. The resident 1.7B
process was preserved, and no new model download was authorized at that stage.
The earlier cache-visibility uncertainty above describes the evidence available
at the original smoke attempt, not the final setup diagnosis.

The four example variables `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
`LIVEKIT_API_SECRET`, and `OPENAI_API_KEY` were absent from this process's
environment. No credential files were searched and no secret values were read.
The setup follow-up created a Git-ignored workspace `.env` with empty credential
fields, the locally authorized profile, and exclusive use left unconfirmed.
Example dependencies and the bundled Silero VAD loaded successfully.

**Remaining gates at the original handoff:** restore visibility of the already
existing 0.6B cache in Voicebox, rerun the authorized single-request smoke, then
supply standard room credentials and confirm exclusive backend use before
conversational/interruption measurements. See the later setup update below for
the restored cache. Cloned speaker identity still needs listening/verification because
the upstream MLX implementation can fall back to an unconditioned voice.
BUILD.md section 36 thresholds remain proposals, not approved/measured results.

## Approved model restoration update

On 2026-09-11 local time, the user explicitly authorized downloading **only**
Qwen TTS 0.6B.
The existing Voicebox downloader failed because its frozen-runtime certificate
bundle was missing. Restoring trusted CA data without disabling TLS exposed a
second runtime failure (`Broken pipe`). No backend restart was performed without
confirmation, and no further runtime repairs were attempted.

An isolated Hugging Face CLI (`huggingface-hub==1.31.0`) successfully downloaded
`mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16` at revision
`1eccf1cb2519b5a4e8a95b5f0544f3303568164f` into Voicebox's existing model cache.
All 14 snapshot files passed the CLI's checksum verification with missing files
treated as errors. Twelve empty `.incomplete` markers from the failed backend
attempt were moved aside after verification, and the errored task was dismissed.
No other model was downloaded.

At `2026-09-10T23:52:28Z`, Voicebox reported the model cached, not downloading,
and not loaded (2399.58 MiB). This replaces
the earlier missing-cache blocker; it is not a completed inference test.
The failed normal loader had already unloaded the previously resident 1.7B model.

**Blockers at restoration:** restart the damaged Voicebox application cleanly, then run
one authorized smoke synthesis with no other consumers. Enter the standard
LiveKit/OpenAI credentials locally and confirm exclusive backend use before a
room session. Real audio, speaker identity, interruption recovery and latency
remain **UNVERIFIED**. The plugin still never downloads models automatically;
this was an explicit one-time operator setup action.

## Successful real synthesis after UI restart

After explicit user approval, Voicebox was quit and reopened using native macOS
computer-use controls. Its backend process was replaced, the restored 0.6B cache
was visible, and no tracked jobs were active. One authorized synthetic test line
was generated through the actual plugin and LiveKit `ChunkedStream`.

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
At `2026-09-11T00:00:30Z`, health reported model_loaded=true and model_size=0.6B.
The authorized profile name remains in local configuration/evidence, not this document.

**State at this validation milestone:** real local audio generation and LiveKit
frame conversion work.
Speaker identity, audible room playback, interruptions, next-turn recovery and
warm conversational latency remain **UNVERIFIED**. The standard four LiveKit/OpenAI
credential fields are still empty, and exclusive backend use must be confirmed
before starting the room example. The setup `check` command now reports only
these configuration requirements, not missing or unloaded model weights.

## Local validation achieved

### Azure-backed example follow-up

LiveKit credentials were supplied locally for the Azure-backed validation.
A read-only LiveKit room-list request authenticated successfully without
printing room identities or credential values. The optional example uses
`livekit-plugins-azure==1.8.1` with locked Speech SDK 1.51.2, plus the existing
OpenAI plugin. The provider package remains Azure-independent.

The Azure example was validated using the signed-in CLI to retrieve resource
keys into process memory only. Authentication support depends on the endpoint;
this observation is not a general claim about Entra token support.
No Azure keys are logged or written to configuration, no endpoints or
permissions are changed, and no resources are created.
A short synthetic LLM prompt succeeded against the selected `gpt-4.1-nano`
deployment. A one-second silent stream completed through Azure STT. These
establish endpoint/authentication connectivity, not microphone transcription
accuracy or end-to-end conversational latency.

The worker registered successfully with LiveKit and a temporary validation room
received 24,000 PCM samples at 48 kHz mono (half a second of remote audio) by
5.102 seconds after starting the test, including room and worker startup.
This is not first-frame latency or a conversational benchmark. The temporary
room was removed and no listener-side audio file was written.
Two earlier listener attempts timed out because the scratch harness incorrectly
used `rtc.AudioStream` as an async context manager; using its supported
`aclose()` lifecycle fixed the observation without changing TTS.

An initial room validation may have inherited cloud recording from job defaults.
Only synthetic greeting audio was involved, not microphone input. The example now
explicitly passes `record=False` and uses local VAD interruption detection.
The successful room retry used those explicit settings. Speaker identity,
human-audible playback, real speech recognition accuracy, and interruption/
next-turn latency still require an interactive session.

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
converted from `Qwen/Qwen3-TTS-12Hz-0.6B-Base`. The original cached artifact revision was unknown; the explicitly restored
0.6B artifact revision is recorded above. No weights are redistributed by this project.
Record actual model/backend revisions before publishing performance results.
Do not infer licenses or support for TADA or other deferred engines from this
single-model scope.
