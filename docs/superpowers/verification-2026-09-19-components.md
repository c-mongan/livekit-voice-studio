# Local/cloud component verification

This extends the standalone Studio work with independently selected LiveKit
transport and reasoning. Existing saved settings migrate to the configured
LiveKit route; new libraries use local LiveKit and Ollama. Speech providers remain
independent. Voice generation remains local Qwen.

## Machine and runtime observations

The implementation machine is an Apple M4 MacBook Air with 10 CPU cores
(4 performance, 6 efficiency), 8 GPU cores and 16 GiB unified memory. These are
hardware inventory values, not measured maximum inference throughput. Initial
swap usage was about 5.7 GiB before local chat generation and rose above 9 GiB
during concurrent development/testing. Other applications were running. This is
not a clean-machine memory benchmark or a recommended operating target.

Ollama 0.22.1 reported approximately 1.9 GB for `qwen3:1.7b`, 100% GPU and a
4096-token context. Its model download was approximately 1.4 GB. A direct cold
chat request returned the expected eight-token sentence in 6.14 seconds. This
excludes recognition, voice generation and LiveKit transport.

The local LiveKit server is version 1.13.7, bound to loopback. A separate local
Ollama instance avoids disturbing the machine's pre-existing embedding service.
Its model files use the checked removable model volume. No reference recordings
or credentials were copied into Git or uploaded for this work.

## Failures found and repaired

The first combined local voice test did not receive audio. Ollama cold loading
was cancelled under the inherited short inference timeout. The endpoint adapter
now has a 60-second HTTP inactivity timeout and zero retries/fallbacks. This is
not a total-response deadline.

Independent review found that the provider options API omitted the new choices,
although mocked UI tests passed. An actual settings response regression now
covers those options. Review also found that the upstream default HTTP client
followed redirects and inherited proxy settings. The custom endpoint transport
now rejects redirects, ignores environment proxies and owns/cleans its client.

## Scope limits

Same-machine local transport is distinct from a public self-hosted deployment.
No public ports, cloud deployments or paid resources were provisioned. No model
quality comparison, physical microphone listening or sustained thermal benchmark
is implied. Local route verification does not itself prove network-disconnected
operation or certify the behavior of an operator-supplied proxy endpoint.

## Completed component matrix

Each row received nonzero audio and acknowledged interruption, then completed
worker/room cleanup. Prompts were generic synthetic text. These are individual
observations with different cache states on a busy machine, not comparable
benchmarks or typical-latency promises.

| LiveKit | Reasoning | Text to first nonzero audio |
| --- | --- | --- |
| Local | ollama | 45.51 s |
| Local | copilot | 12.18 s |
| Cloud | ollama | 25.47 s |
| Cloud | copilot | 7.39 s |

A native recognizer startup failed between cases. Its public diagnostic did not
identify the exact cause; the remaining cases passed when retried without
concurrent offline test work. Resource pressure is a plausible contributor, not
a proven root cause.

Browser verification exercised real provider options, saved the all-local route,
switched Ollama to Copilot and back, and confirmed the nondefault local endpoint
survived. The mobile drawer fit 390 px and the privacy text correctly described
local transport, local recognition and local reasoning. No page errors were
reported in that flow.

## Spoken input and remaining reliability limit

An all-local two-turn spoken-input test used a 2.13-second synthetic system-voice
fixture, without capturing a physical microphone. The final run passed in
39.36 seconds: nonzero reply audio arrived 7.73 and 5.60 seconds after speech
ended. The second turn's first model token took 0.53 seconds. This verifies local
Nemotron → Ollama → Qwen over local LiveKit, including a follow-up and cleanup.

An earlier run answered the first spoken turn but reported a speech-recognition
error and timed out on the second. The subsequent successful run does not explain
that failure. The native WebSocket has a 300-second read timeout, aiohttp removes
its HTTP read timeout after upgrade, and an isolated fake-sidecar test survived
16 seconds of silence before producing a second transcript. A CPU stall delaying
native heartbeat handling remains a hypothesis, not a diagnosed cause. No STT
transport timeout was increased. The worker now exposes only allowlisted static
speech failure reasons; arbitrary exception text stays private.

The browser's session-creation request now allows 60 seconds instead of 15, to
accommodate its existing sequential endpoint, recognizer and room preparation.
Other API calls remain bounded to 15 seconds. The error explains startup/cleanup
rather than incorrectly reporting that the entire Studio server is unavailable.

## Final browser and regression checks

A real browser start exposed a missing local transport permission: the page's
Content Security Policy allowed secure cloud connections but blocked
`ws://127.0.0.1:7880`. A captured `securitypolicyviolation` identified `connect-src`.
The policy now allows only the exact local development HTTP/WebSocket origin in
addition to its existing secure sources. A regression failed before this change
and passed afterward; unrestricted HTTP/WS origins remain disallowed.

The final browser conversation connected, sent a typed synthetic prompt and
received the expected local-model reply. End session returned the service to
idle/ready. No page errors were reported. This browser test verifies connection,
transcript and cleanup; the separate RTC tests above verify nonzero audio.

Final offline checks: 535 Python tests passed, 5 integration tests deselected;
198 frontend tests passed across 14 files; mypy passed 31 source files; Ruff and
format checks passed for 74 files. The production frontend build passed, with the
existing bundle-size warning. After the final recovery-message wording change,
the 45 Studio tests passed again. Private credentials, references, model files
and machine-specific service definitions remain outside the commit.
