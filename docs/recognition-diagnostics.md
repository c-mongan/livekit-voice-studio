# Recognition overload diagnostics

Studio reports a fixed, privacy-safe phase when local recognition's bounded input
queue fills: connection, handshake, streaming, audio send or finalization. The
first overload phase is retained even if a pending operation later completes.
Unknown error payloads still receive a generic message; transcripts, endpoints
and native error text are not forwarded to the UI.

Normal input retains its two-second default buffer. While finalizing an utterance,
a reserve allows up to the existing finalization deadline's worth of new audio to
queue without mixing utterances. With default settings, the total queued PCM
budget is 17 seconds (544,000 bytes), plus at most one 200 ms in-flight frame.
A separate 2048-item queue cap also applies. The normal byte limit resumes after
the reserve drains. Timeouts, explicit overload failure and cleanup are unchanged.

## Reproduction on 2026-09-19

A single synthetic spoken turn on local LiveKit, Ollama, Nemotron and Qwen reported:
“Local speech input buffer filled up during finalization.” The microphone was not
used. The adapter was submitting the native commit or waiting for its acknowledgement
while new input accumulated. The diagnostic does not distinguish these two steps.
The Mac was using about 19 GiB of swap during this run.

An isolated native probe then returned a final transcript about 2.007 seconds
after committing and completed about 2.010 seconds after committing. The native
server handles commit by finishing recognition, resetting the utterance and then
acknowledging it. Our adapter correctly waits for that acknowledgement before
sending the next utterance, but its two-second input budget could expire during
that legitimate wait. A deterministic regression reproduced this mismatch.

The deadline-sized reserve fixes that capacity mismatch while retaining ordering
and finite memory use. It does not make a slow recognizer faster or prevent the
existing finalization timeout. Heavy system load can still cause failures.

The spoken harness timed out before a reply and successfully returned Studio to
idle/ready. Focused adapter/worker tests passed (48); the full offline Python suite
passed (543, with 5 integration tests deselected). Lint, formatting and mypy passed.
These checks validate the diagnostic change, not spoken-conversation reliability.

## Reserve fix validation

The regression that continued input during a held commit failed before the fix
and passes with it. Additional tests cover the item cap, backlog drain back to
the normal byte limit, bounded overflow, and retry phase reporting. All 546 offline
Python tests passed (5 integration tests deselected), along with lint, formatting
and mypy.

Against the real native recognizer, the synthetic fixture followed by three
seconds of continuing silence completed without overflow. The final transcript
arrived about 2.084 seconds after commit; the complete two-commit stream ended
about 4.010 seconds after the first commit. No transcript text was retained in
this evidence record.

The full LiveKit/Qwen conversation retest still timed out before receiving reply
audio; its final UI diagnostic reported that the recognition connection closed.
Session cleanup returned Studio to idle/ready. The reserve fix therefore has unit
and isolated native proof, but does not yet establish full-conversation reliability.

## Heartbeat and finalization deadlines

The native server processes recognition synchronously between WebSocket reads,
so it cannot answer a ping while finishing an utterance. A regression reproduces
the old heartbeat closing this socket before valid finalization completes.
The heartbeat interval is now `max(10, 2 * finalize_timeout)` seconds, giving its
pong wait at least the configured finalization deadline. The operation's own
deadline is unchanged.

The tradeoff is slower detection of an unresponsive idle connection: approximately
45 seconds with defaults, and up to 360 seconds with the largest allowed
finalization setting. Active finalization still fails after its configured deadline.
Tests cover completion before the new ping and after the ping but before its pong
wait expires. This prevents a reproduced premature-disconnection mechanism; it
does not prove every earlier live connection failure had that cause.
