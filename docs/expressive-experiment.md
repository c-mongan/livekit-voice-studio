# Compare reference delivery, privately

This experiment asks whether Qwen transfers a useful change in delivery from two recordings of the same consenting speaker. It does **not** establish reliable emotion control, and it does not add LiveKit Expressive mode to Qwen.

Use a neutral reference bundle and a second bundle with a deliberate delivery, such as calm or cheerful. Record the same reference passage with the same microphone and room if possible. Keep volume and distance comparable. Each bundle must follow the existing authorized version-1 format: `voice.json` plus checksummed `reference.wav`; see [voice bundles](../examples/voice_bundle.py). Never commit your references or generated voice audio.

## Preview without generating audio

Run from the repository root with the existing MLX installation. Choose a private output location outside the repository. Replace these example paths with your own local paths:

```sh
uv run --no-sync python -m tools.expressive_compare \
  --neutral /path/to/neutral-bundle \
  --expressive /path/to/expressive-bundle \
  --model /path/to/existing-qwen-snapshot \
  --output /path/to/new-private-comparison
```

The default is a dry run. It prints three fixed sentences and the six-sample plan. It does not read references, create output, load models, download files, or contact services. It does not validate the supplied paths.

## Generate the comparison

Stop Studio and other local voice consumers first. Add `--render` to the same command when ready. The command reads the repository `.env` without overriding existing environment values, to use the same `VOICEBOX_URL` and `VOICEBOX_RUNTIME_DIR` ownership settings as Studio. Do not change these settings to bypass an existing owner.

Each reference runs in a separate child process, sequentially. Each child acquires Studio's cross-process backend lease before model construction. A competing Studio process blocks rendering. The process boundary releases model memory before the next reference loads. The adapter's existing bundle validation, inference limits, and close/drain behavior still apply. Offline model flags are forced; the model must already exist locally.

The output directory must be new and receives owner-only permissions. Existing results are never overwritten. Generation is bounded to three short sentences per reference, 30 seconds of PCM per sample and five minutes per child. A timeout or uncertain drain retains the durable unresolved-work marker; follow the existing recovery procedure only after confirming the backend has stopped. Partial outputs remain available after failure. No cloud resources, upload, microphone capture, or automatic playback is involved.

Outputs:

- `A-1.wav` through `B-3.wav`: randomized reference labels, identical sentences.
- `listening.csv`: a worksheet with identity, perceived expression, naturalness, word accuracy, and notes.
- `report.json` and `A-timing.json` / `B-timing.json`: local preparation, first frame, generation and audio durations. These are not browser playback latency or LiveKit end-to-end timings.
- `answer-key.json`: which label used the neutral or expressive reference. Keep it closed until scoring.

Listen in worksheet order, ideally with another listener who has not seen the key. Compare identity and intelligibility before interpreting expression. Repeat with fresh output directories: generation is stochastic, and three sentences are a pilot rather than a benchmark. There is no matched random seed or automatic emotional score. Preparation and warm-cache effects can influence performance, so do not interpret one faster run as a model improvement.

## What this does and does not test

The current adapter accepts reference audio and its transcript. It has no supported emotion instruction or reusable steering-vector API. This harness changes only the authorized reference; it does not alter pitch, temperature, weights, or model internals and call those changes emotion.

LiveKit's [Expressive mode](https://docs.livekit.io/agents/models/tts/expressive/) is a separate provider integration. Experimental Qwen steering implementations would require a separate runtime/compatibility evaluation before entering this adapter. First establish whether reference delivery produces a repeatable benefit while preserving identity. If it does not, preserve that negative result and evaluate a model designed for independent identity and style control separately.

Automated tests cover dry-run behavior, output refusal/privacy, matching prompts, and backend ownership with fake synthesis. They do not demonstrate voice quality, emotional transfer, model compatibility on another machine, or real-time conversation performance. Those remain listening and live acceptance checks.

An independent [Qwen 0.6B emotion recipe](https://github.com/gabriele-mastrapasqua/qwen3-tts/blob/main/docs/emotion-06b-recipe.md) reports steering with embedding directions in its own runtime. This is a research lead, not evidence that the same controls work in this project's Python/MLX adapter. Pitch alone cannot reproduce timing, stress, articulation and voice quality together. Record separate reference deliveries for this small comparison first; a steering port should follow only if its runtime and identity-preservation results justify it.
