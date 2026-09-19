# Evaluate the configured build

Evaluation has two different jobs: catch functional regressions without models,
and measure an explicitly authorized live path. Neither can judge whether a
cloned voice sounds like its speaker.

## Offline functional checks

Install the full development extras from CONTRIBUTING.md before running the
suite. The tests exercise Azure factories even when your chosen runtime provider
is Copilot, so the Azure extra is required for tests. Keep the MLX extra when
using the Apple Silicon app; uv sync can remove extras omitted from a later command.

```sh
uv sync --frozen --package livekit-plugins-voicebox \
  --extra dev --extra example --extra azure --extra agents --extra mlx --python 3.12
```

Then run:

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

## Interruption recovery and repeated sessions

To test the already-running managed Studio server, end any current conversation,
select an all-local route and an authorized voice, then run:

```sh
STUDIO_RELIABILITY_INTEGRATION=1 uv run --no-sync python -m pytest \
  tests/integration/test_running_studio.py -q -s
```

This opt-in test runs two sequential sessions with synthetic typed prompts. It
requires local LiveKit, recognition and reasoning, and does not capture a
microphone. In each session it verifies that a concurrent Start is rejected,
waits for real nonzero reply audio, interrupts while the agent reports speaking,
waits for listening and quiet, then requires both new audio and a matching
agent transcript for a follow-up. Audio from other participants is ignored. Finally
it ends the owned session and requires cleanup back to idle before starting the
next session. It does not stop the Studio server or clear unresolved-work markers.

An interruption acknowledgement alone is not a pass. The follow-up and subsequent
session must work. The test covers the Stop reply RPC, not natural microphone
barge-in, human voice quality, or long-session endurance. It never downloads a
model or changes the selected provider. Failures stay failures; do not clear a
blocked backend just to rerun this check.

### Spoken interruption after the first reply

On macOS, generate a short synthetic fixture with the built-in system voice:

```sh
say -o /tmp/studio-barge-in.aiff 'Stop talking. Reply with only: Recovery works.'
afconvert /tmp/studio-barge-in.aiff /tmp/studio-barge-in.wav -f WAVE -d LEI16@16000
STUDIO_RELIABILITY_INTEGRATION=1 STUDIO_BARGE_IN_AUDIO=/tmp/studio-barge-in.wav \
  uv run --no-sync python -m pytest tests/integration/test_running_studio.py -q -s
```

On other systems, supply an authorized synthetic PCM16, mono, 16 kHz WAV of that
phrase, at most 15 seconds long. The fixture is never committed. Without
`STUDIO_BARGE_IN_AUDIO`, the additional spoken test skips; the two-session RPC
check still runs with its existing opt-in flag.

The spoken test publishes continuous synthetic microphone audio, including silence
between phrases. It completes a first reply, then speaks over a second reply.
It requires the agent to leave its speaking state within three seconds, produce
the expected follow-up text and nonzero audio, and clean up to idle. It does not
send a Stop RPC or a typed follow-up for the interrupted turn. The reported state
transition time is not a measurement of audible cancellation latency.

**First-reply limitation:** the installed LiveKit Agents SDK defaults to a
three-second echo-cancellation warm-up when the agent first speaks. During that
window it substitutes silence on the recognition path, even though voice activity
can still be detected. Interrupting immediately can therefore lose words. This
test explicitly excludes that initial window; Studio retains the SDK's protection
against speaker echo. Use Stop reply if you need to cancel immediately. This is
not proof of headset, speakerphone, accent, background-noise or browser-microphone
performance. See [the measured results](reliability-2026-09-19.md).
