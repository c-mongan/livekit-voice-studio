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

## Two-turn reasoning check

To verify the account adapter and conversation context without voice models or
LiveKit, run this explicitly opted-in check. It sends two short synthetic prompts,
checks a remembered word, and reports first-text timings without saving replies.
It uses normal subscription/account inference. It does not change Studio settings.

```sh
STUDIO_AGENT_INTEGRATION=1 STUDIO_TEST_PROVIDER=copilot \
  uv run --no-sync pytest tests/integration/test_agent_live.py -q -s
```

Restricted Codex has a separate explicit opt-in. Its preflight must still verify
zero external tools and command networking disabled; it is not tool-free mode.
Read [the provider boundary](agent-providers.md#restricted-agent-mode) first.

```sh
STUDIO_AGENT_INTEGRATION=1 STUDIO_TEST_PROVIDER=codex \
  STUDIO_TEST_RESTRICTED_CODEX=1 \
  uv run --no-sync pytest tests/integration/test_agent_live.py -q -s
```

Neither check runs in ordinary CI. Passing it establishes this tiny context test,
not general reasoning quality, full speech compatibility or every subscription.

## Conversation-flow check

Let a short spoken answer finish. Then request a longer answer and interrupt it
with a different question while audio is playing. Confirm the old reply stops,
the new answer is correct, and End session returns to Ready. A shortened
transcript alone does not prove a fault: it may reflect an intentional interruption.
Repeat casual small talk to check greeting repetition and premature goodbyes.
Synthetic audio validates signal flow; it does not replace microphone and listening
evaluation. Record failures alongside successful timings.

## Diagnose stage timing and pauses

The spoken test also prints `diagnostic_turn` records containing the latest
worker timing measurements: final-transcript delay, end-of-turn delay, first
reasoning token, and first generated audio frame. These are diagnostic snapshots,
not disjoint parts of a sum: end-of-turn and transcription delays overlap,
and playback/transport add delay beyond model timings. Keep the separately
measured speech-end-to-remote-audio latency as the end-to-end result.

To check a pause with no microphone frames between two turns, run the live test
directly with `STUDIO_TEST_TURNS=2` and `STUDIO_TEST_PAUSE_SECONDS=20`, alongside
`STUDIO_SPOKEN_INTEGRATION=1` and the existing `STUDIO_TEST_AUDIO` fixture.
The pause is bounded to 0–30 seconds and defaults to zero. It tests an idle input
stream, not recognition under continuous room noise.
