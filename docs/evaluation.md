# Evaluate the configured build

Evaluation has two different jobs: catch functional regressions without models,
and measure an explicitly authorized live path. Neither can judge whether a
cloned voice sounds like its speaker.

## Offline functional checks

After installing the development dependencies:

```sh
uv run --no-sync python -m tools.evaluate
```

This runs the Python suite with live integration tests deselected and model
downloads disabled. It prints a versioned JSON report and exits nonzero on a
failure or missing report. The report contains source/package provenance,
test counts and pass/fail results. **Latency is null**, not a synthetic claim
about model speed.

Run the UI checks separately:

```sh
npm --prefix web run typecheck
npm --prefix web test
npm --prefix web run build
```

## Spoken latency diagnostic

Start Studio, select the authorized voice/providers, and end any existing room.
Supply a short synthetic audio fixture you have permission to use:

```sh
uv run --no-sync python -m tools.evaluate \
  --mode spoken --allow-live --turns 3 \
  --audio /private/path/synthetic-input.wav
```

This explicitly uses your configured services and their normal account usage.
It does not create accounts, download models, start Studio, choose a voice or
silently switch providers.

The test repeats the same fixture for one to five turns in a single room. Each
turn waits for the previous response to finish. It measures from input speech
end to nonzero audio received by the test client—not just first HTTP headers.
The report includes every timing, sample count, provider configuration, median,
slowest and fastest observed samples. Incomplete runs remain failures; they
are not discarded to improve the numbers.

Five samples are not a population p90. Keep cold model preparation separate from
conversation latency, and do not compare different prompts/providers/hardware
as if they were a controlled experiment.

No response audio is recorded. Temporary test reports are removed; JSON output
has no input path, voice name, credential, transcript or raw provider response.
You can save the report explicitly using normal shell redirection, but review
it before sharing as it identifies runtime versions and source revisions.

## Listening is still a human check

Use the [voice-quality checklist](voice-quality.md) for names, numbers, pacing
and question intonation. Test a real microphone, your accent, room noise and
audible interruption separately. Browser tests and nonzero PCM prove signal
flow, not naturalness, identity or safety for sensitive documents.
