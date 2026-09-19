# Provider and recovery validation — 19 September 2026

These checks use synthetic text/audio. They do not establish human microphone
quality, long-session endurance or offline/network-isolated operation.

## Follow-up evidence

The managed integration test now counts nonzero audio only after a new agent
speaking-state transition following interruption. A transcript stream receives a
generation token when it opens; a previous stream's late chunks cannot match the
new reply. Three offline regressions exercise stale transcripts, draining audio,
rearming and audio arriving before a longer input fixture finishes.

Interruption evidence is tied to a sequence assigned when each SDK speech handle
is created. Its completion callback reports that same sequence and interrupted
flag, without text or an SDK identifier. The test requires the counting reply's
sequence, so interruption of a later reply cannot count. Regressions cover
out-of-order completion and replies omitted from chat history.

The final local run passed both tests in 63.84 seconds: two Stop-RPC sessions
and one spoken interruption session. All three confirmed the specific SDK speech
handle was interrupted, matched follow-up text and received new-interval audio.
The spoken check received 40,683 nonzero samples and returned to idle. Its
speaking-state transition was 0.659 seconds, not an audible cancellation
measurement.

## Codex: text and speech

The fresh restricted-agent text check passed two turns with correct synthetic
memory. First-text timings were 2.741 and 0.997 seconds. Preflight required zero
external tools and disabled command networking.

The speech integration used local LiveKit, Nemotron and Qwen with **cloud Codex
Luna/low reasoning**. After initial echo warm-up, synthetic speech interrupted a
reply and produced the expected follow-up transcript and 40,336 nonzero samples
during the new speaking interval. The specific SDK speech handle confirmed
interruption. It passed in 43.23 seconds and returned to idle.
The state transition was 0.816 seconds. Original local settings were restored and
read back. No private conversation or voice reference was sent to Codex.

## Failure injection

Controlled local HTTP tests cover a model-list connection drop and recovery, a
missing model becoming available, authentication rejection, wrong API route, and
server unavailability. A broken streaming reply raises an error without an
adapter retry; a subsequent explicit request succeeds. No fallback provider or
model download is invoked.

Broker tests simulate a LiveKit connection failure, verify idle/no worker/no
active inference marker, then allow the next start to succeed and clean up.
These are deterministic fault-injection tests, not a live WAN reconnect test.

The initial focused recovery/boundary suite passed 75 tests. Browser validation also
selected an intentionally missing local model, displayed the actionable error
(`ollama list` and Settings), restored the original model, started a new session successfully, and ended it
back at Ready to start. The test tab reported no console errors.

## Failures retained in the record

Before restarting managed Studio, the first updated spoken run failed with an
HTTP cleanup timeout. A focused rerun reported Nemotron finalization timeout.
Studio eventually returned to idle; neither failure was converted to a pass.
After an ordinary graceful stop/start of Studio and its owned recognizer, the
Codex and local spoken runs above succeeded. This does not establish that the
intermittent recognizer failure is fixed. Test waits now surface server-reported
errors instead of waiting only for expected reply text.

The offline suite initially printed 571 passes but exited with SIGABRT (134).
The macOS crash report mapped the failing `Microsoft::Applications::Events`
telemetry callback to `onnxruntime_pybind11_state.so`. Installed ONNX Runtime
1.30.0 contains the documented `ORT_DISABLE_TELEMETRY` startup switch.
Studio's examples package now sets it before loading the native speech modules.
This avoids starting the unnecessary uploader, rather than hiding its stderr or
ignoring an exit code. Two successive full-suite runs then passed with exit 0
(571 tests each). After the speech-handle regression was added, the final offline
suite passed 572 tests with exit 0; eight opt-in live tests were deselected.

An intermediate RPC test did not receive an interrupted chat item when stopped
immediately. SDK source confirms that an empty played transcript can omit the
chat item. The final test uses speech-handle completion instead, so it does not
rely on a delay or on a transcript being saved.

The upstream [ONNX Runtime privacy documentation](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md#disabling-telemetry)
distinguishes this pre-initialization switch from the later API opt-out. This
change is process-scoped to the examples; it does not certify that every dependency
or configured endpoint is network-silent. It cannot undo an ONNX initialization
that happened before another application's import of this package.
