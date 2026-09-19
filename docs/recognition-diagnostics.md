# Recognition overload diagnostics

Studio reports a fixed, privacy-safe phase when local recognition's bounded input
queue fills: connection, handshake, streaming, audio send or finalization. The
first overload phase is retained even if a pending operation later completes.
Unknown error payloads still receive a generic message; transcripts, endpoints
and native error text are not forwarded to the UI.

This adds diagnosis, not capacity: the two-second default audio buffer, deadlines,
explicit overload failure and session cleanup remain unchanged.

## Reproduction on 2026-09-19

A single synthetic spoken turn on local LiveKit, Ollama, Nemotron and Qwen reported:
“Local speech input buffer filled up during finalization.” The microphone was not
used. The adapter was submitting the native commit or waiting for its acknowledgement
while new input accumulated. The diagnostic does not distinguish these two steps. The Mac was using about 19 GiB of swap during this run.

This identifies the adapter phase, not the cause of slow native finalization.
The next controlled experiment is the same fixture after reducing unrelated
memory-heavy workloads, measuring commit-to-ack duration. A passing retry alone
would not prove a fix. Do not increase queue sizes or remove commit ordering
without checking the native protocol's ordering and memory bounds.

The spoken harness timed out before a reply and successfully returned Studio to
idle/ready. Focused adapter/worker tests passed (48); the full offline Python suite
passed (543, with 5 integration tests deselected). Lint, formatting and mypy passed.
These checks validate the diagnostic change, not spoken-conversation reliability.
