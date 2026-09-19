# Repeated-conversation and recovery checks — 19 September 2026

These are bounded local synthetic tests, not an independent installation review
or a claim of long-session reliability. No physical microphone, private text or
private conversation recording is used. Model and voice assets remain outside the repository.

## Five spoken turns

The existing spoken harness passed five sequential turns in one room, with ten
seconds between replies. Each reply finished before the next input. The local
route used Nemotron recognition, Ollama reasoning, Qwen speech and local LiveKit.
Speech-end-to-first-nonzero-audio timings were 5.473, 2.694, 2.553, 2.326 and 2.924
seconds (median 2.694). Session cleanup returned to idle/ready. The same short
system-voice fixture was repeated, so this does not evaluate varied questions,
accents or natural microphone interruption.

## Shutdown race regression

An initial two-session interruption run reached its follow-up audio checks but
failed cleanup with “Agent exited without confirmed drain.” Source inspection and
a regression reproduced a worker shutdown exception: the worker called
`session.interrupt()` after a room disconnect could already have closed the SDK
session. That call raises when the session has no running activity.

The worker now calls the SDK's idempotent `aclose()` directly. In the installed
SDK, it already interrupts and drains active speech. The separate synthesis
provider drain is retained, including its failure path and durable unresolved-work
marker. No condition that admits uncertain inference has been weakened.

## Stronger live recovery check

The new opt-in managed-server test requires an idle all-local configuration before
each of two sessions. In each session it rejects a second concurrent Start,
interrupts an actually speaking agent, waits for quiet/listening, and requests a
follow-up. Only the granted agent's audio contributes to the result, and its
transcription must contain the synthetic follow-up phrase. This prevents delayed
audio from the interrupted reply from passing as successful recovery.

See [the evaluation commands](evaluation.md#interruption-recovery-and-repeated-sessions).
The test exercises the Stop reply RPC rather than microphone barge-in. It does
not measure audible cancellation delay or claim semantic accuracy generally.

The tightened live rerun passed both sessions in 53.47 seconds, including matching
follow-up text, nonzero audio and cleanup to idle after each session. Studio's
status was read back as ready afterward. This supports the fix on the tested
machine; it does not identify the original process exception with certainty,
because private worker stderr is intentionally not retained.

## Offline checks

A direct pytest process completed with exit code 0: 559 passed and 6 live tests
were deselected. Lint, formatting and mypy passed. An earlier grouped check
printed a native `recursive_mutex lock failed` teardown error after the passing
pytest summary; that run did not isolate pytest's exit code. The direct rerun
was clean. The warning was unexplained in that run. A later crash report identified the
ONNX Runtime telemetry path; see the [follow-up investigation](provider-recovery-2026-09-19.md).

## Spoken interruption and the initial echo guard

An additional synthetic test now exercises spoken interruption after the first
reply. It uses a local microphone track, no physical microphone, and requires the
spoken instruction to produce a matching agent transcript and nonzero audio.
The first successful run took 27.42 seconds. The agent left its speaking state
0.810 seconds after synthetic input began; cleanup returned Studio to idle/ready.
This is a state-transition observation, not audible cancellation latency.

The initial immediate-interruption probes failed to deliver the full instruction.
The same fixture transcribed correctly when sent directly to Nemotron, including
with the agent's resampling and gain processing. A temporary synthetic-input trace
showed only the tail reaching recognition. Source inspection identified the
installed SDK's default three-second AEC warm-up: `AgentActivity.push_audio`
substitutes silence for recognition during that window. Continuous input and a
48 kHz publishing rate did not fix it. Completing the initial reply and allowing
the warm-up to expire did. The application guard is unchanged, and immediate
first-reply spoken interruption remains a documented limitation.

Temporary audio tracing was removed before delivery. The committed test prints
only timings and result flags; it does not save audio or transcript text. The
fixture is generated locally and remains outside the repository.

A silence-only negative control failed at the three-second speaking-state deadline
as expected (23.80 seconds including setup and cleanup). It returned Studio to
idle. Silence therefore did not pass as successful interruption.

The final combined run passed both tests in 66.76 seconds: two RPC sessions and
one spoken-interruption session. The spoken state transition was 0.654 seconds;
all three sessions matched their follow-up text and cleaned up. Studio reported
idle/ready afterward. Ruff and formatting checks passed, and both live tests
skipped when their opt-in flag was absent.
