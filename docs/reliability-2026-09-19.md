# Repeated-conversation and recovery checks — 19 September 2026

These are bounded local synthetic tests, not an independent installation review
or a claim of long-session reliability. No physical microphone, private text or
recording is used. Model and voice assets remain outside the repository.

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
was clean. The teardown warning remains unexplained and is not claimed fixed.
