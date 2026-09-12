# Local completed-WAV benchmark

Select an authorized profile and stop all other Voicebox consumers, including
desktop generation. This intentionally generates local speech. It does not save
text, WAVs, or reference samples; JSON contains only anonymized metadata/timings.

```sh
uv run --no-sync python benchmarks/benchmark_tts.py \
  --profile "$VOICEBOX_PROFILE" --exclusive-backend \
  --hardware "actual chip and RAM" --voicebox-version "actual installed version" \
  --phrase all --iterations 10 --json > benchmark-results.json
```

One warm-up **per phrase**, then ten measured requests by default. Every failure
is retained; a failure stops further submissions. `loaded_before=false` indicates
a cold model load, even when its weights were cached. Model warmness does not
certify warmed profile conditioning. No automatic downloads.

Reported measurements use one monotonic clock: profile resolution, queue wait,
HTTP first body byte, complete body, decode/resample, first LiveKit frame, total
consumer elapsed time and actual emitted duration. Readiness GETs are included
in total consumer elapsed time but not attributed to queue or HTTP generation.
RTF is **end-to-end request elapsed / emitted duration**, not pure model RTF.
Voicebox completes generation before sending its WAV, so first HTTP audio often
arrives near generation completion. `p90` uses nearest rank.

Record actual hardware/RAM, power settings, backend dependency and model revisions,
profile effects, and concurrent load with the CLI metadata options. `unknown`
means unknown, not the source-review baseline. Profile effects and default
normalization/crossfade are not disabled by this adapter.

Ten samples are exploratory, not release evidence. Use at least 30 measured turns
per reference setup for proposed release gates, and approve numeric limits before
evaluation. The proposed BUILD.md thresholds are not approved or achieved claims.

## Separate real-room gates

Using `examples/minimal_agent.py`, measure end-of-user-speech to audible response,
gaps in a three-sentence reply, interruption-to-silence, and next-turn recovery.
Record detection latency separately. Do not subtract clocks from different
machines. Repeat voice switching using another explicitly authorized profile.
Emitter-level cancellation tests cannot establish that already queued room
playback has stopped. Never perform interruption stress runs without exclusive
backend ownership; ambiguous completion requires backend stop and client recreation.
