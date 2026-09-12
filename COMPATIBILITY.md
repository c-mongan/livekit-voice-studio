# Compatibility and validation

`0.1.0.dev0` is an unreleased, single-backend prototype. The plugin, local Studio,
and cloud services have different validation boundaries.

## Supported configuration

| Layer | Configuration |
| --- | --- |
| Python plugin | Python 3.11–3.13; LiveKit Agents 1.8.1; RTC 1.1.18 |
| Voicebox | API 0.5.0; Qwen TTS 0.6B; cloned profiles with samples |
| Output | Mono PCM16; 24 kHz by default; supported alternate rates validated in code |
| Local Studio server | macOS/Linux; loopback aiohttp; one owned room and process at a time |
| Optional fast voice | Apple Silicon; MLX Audio 0.5.3; existing Qwen 0.6B; one authorized reference |
| Browser UI | Node.js 22+ build; Vite/React; LiveKit React/RTC dependencies locked in `web/` |
| Speech recognition | Local Nemotron CPU; Azure Speech or OpenAI through optional plugins |
| Reasoning | Copilot Luna/low; explicitly restricted Codex Luna/low; Azure OpenAI or OpenAI |

Runtime Python versions are pinned in the package manifest and `uv.lock`.
The UI has its own lockfile. No inference weights or personal recordings are
included in the repository or Python wheel.

## Source and model provenance

| Component | Inspected revision |
| --- | --- |
| Voicebox | `51f49dea198384b4eb6087b72c17057c6eb1c1cd` |
| LiveKit Agents source baseline | `4de62322fa84b7c1736370ff3ebbd66b5d0ffe6f`; installed 1.8.1 public APIs checked |
| Official React starter | `44b8a0ce82039018a1feb2c1bda43b5ada2ab24e` |
| Restored MLX Qwen 0.6B model | `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16` at `1eccf1cb2519b5a4e8a95b5f0544f3303568164f` |

All 14 restored model files passed checksum verification. The installed Voicebox
binary's exact source revision and MLX Audio version are unknown; the reviewed
source must not be substituted for those facts.

## Real observations

The reference machine is an Apple M4 with 16 GiB RAM, macOS 26.6.2,
Voicebox API 0.5.0 and its reported MLX/MPS backend.

| Check | Observed result |
| --- | --- |
| Direct cold TTS | 4.0 s of 24 kHz mono PCM16; first LiveKit frame at 42.00 s |
| Azure LLM connectivity | Synthetic prompt succeeded; three later first-text observations: 2.034, 1.848, 1.857 s |
| Azure STT connectivity | One-second silent input stream completed; not a speech-accuracy test |
| Studio typed reply, session 1 | First non-silent remote audio at 22.72 s; LLM first token 1.72 s; TTS first frame 20.13 s |
| Studio typed reply, session 2 | First non-silent remote audio at 12.05 s; LLM first token 1.83 s; TTS first frame 9.68 s |
| Reconnect | First agent drained and exited before second room admission |
| Stop reply | Owner-only interrupt RPC acknowledged during the second real session |
| Fast Studio reply, session 1 | Non-silent remote audio at 2.23 s; LLM first token 1.48 s; TTS first frame 0.48 s |
| Fast Studio reply, session 2 | Non-silent remote audio at 2.62 s; LLM first token 1.55 s; TTS first frame 0.42 s |
| Fast browser flow | Typed reply, Stop reply acknowledgement, subsequent reply and safe session end passed |
| Standalone fast session 1, Voicebox offline | Non-silent audio at 2.488 s; TTS first frame 0.446 s |
| Standalone fast session 2, Voicebox offline | Non-silent audio at 2.346 s; TTS first frame 0.436 s; stop acknowledged |
| Copilot Luna/low, two direct requests | First text 1.37 / 0.74 s; session memory correct; zero tools attested |
| Codex Luna/low, two restricted requests | First text 3.59 / 2.53 s; session memory correct; scoped sandbox and zero external tools verified |
| Synthetic speech → local Nemotron → Copilot → local Qwen | Non-silent reply 4.07 s after synthetic speech ended |
| Synthetic speech → local Nemotron → restricted Codex → local Qwen | Non-silent reply 3.86 s after synthetic speech ended |
| Local recognition | Expected phrase recognized; 35-second idle pause followed by successful commit |
| Explicit VAD, no speculative LLM; five synthetic turns in one room | 3.440 / 3.110 / 2.878 / 3.177 / 3.628 s after speech ended; median 3.177 s, slowest 3.628 s |
| Local generated audition | Real non-silent WAV, immediate cancel, successful retry and one-shot delivery passed; no settings change |
| Updated browser workflow | Synthetic enrollment and deletion; generated 3.84 s WAV with explicit playback; cancellation and typed reply → Stop → next reply passed |
| Structured spoken evaluation, three turns | 6.412 / 3.137 / 3.605 s; median 3.605 s, slowest 6.412 s; local VAD, Nemotron, Copilot and Qwen |

The room checks counted nonzero PCM samples, not just the presence of an audio
track or silence packets. No listener-side audio recording was written.
The standalone check used a private one-time reference import (about 884 KB),
with the Voicebox desktop application and its port-17493 backend stopped.
It required no new model weights or cloud deployment.
Emitted/received frames are not the same as sound heard by a person.

These are small, exploratory samples. There is no p90 claim, approved release
threshold, production capacity claim, or assertion that a warm model always
responds quickly.

The structured three-turn evaluation repeated one stock synthetic phrase in
the configured room. All requested turns passed; the slower first observation
is retained rather than removed. It is a separate run from the five-turn sample,
not an optimization comparison. The report includes source-modified state and
package versions; no response recording or input fixture was retained.

**Still requiring human validation:** microphone speech recognition, speaker
identity, audible interruption-to-silence, absence of stale playback in a real
conversation, next-turn recovery, and multi-turn latency.

## Tests

The integrated Python suite passes **471 tests** covering the provider, broker,
private voice library, local STT, agent adapters and fast mode on Python 3.12.
The 22 broker ownership tests also passed on Python 3.11 and 3.13; the original
provider previously passed its 103-test suite on all three versions.
Frontend type checking, production build and **167 tests** passed. These include
typed-first startup, microphone denial, ownership loss, stop acknowledgements,
late connection cancellation and transcript safety.

A fresh sanitized clone installed the locked Python/frontend dependencies and
passed offline tests, type checks and builds without a `.env`, reference bundle,
or model download. This was a second checkout on the same Mac, not independent
validation on somebody else's hardware. The read-only doctor also passed
isolated-home, no-network/no-write tests: fresh installs report missing setup
without creating a voice library or claiming account authentication.

The quality pass adds generated-audition lifecycle tests: exclusive admission,
voice snapshots, owner-only access, one-time audio delivery, expiry, cancellation
before worker initialization, bounded frames, silent-output rejection and
fail-closed cleanup after a broken worker monitor. The worker uses the same
streaming Qwen provider as conversations, without cloud credentials.
Independent review found and corrected an expired-handle retry loop. A real
browser test interrupted audition polling/cleanup for 75 seconds; after the
completed result expired, restoring transport displayed the expiry message,
discarded audio and released controls only after checking public backend status.

The managed-launch/read-aloud pass adds ownership and service-control tests,
deterministic prose extraction, mute, private Stop IPC and playback-error cleanup.
On macOS, two Start commands reused one PID, graceful Stop produced exit code 0,
and restart produced a new ready PID. A real managed LiveKit/Nemotron/Copilot/Qwen
turn received nonzero audio in 4.546 seconds after synthetic speech ended; this
single observation is not a latency comparison. Local narration played through
the output device, rejected room admission during playback, stopped on request,
and made no synthesis request while muted.
The Codex 0.153.4 notification contract was checked against its exact upstream
release. An exact-schema notification fixture exercised real local speech, and
repeating its turn ID produced no second synthesis. This validates the bridge
and payload handling; no additional Codex coding turn was requested solely to
trigger the hook.

New Studio sessions explicitly use local VAD turn detection and disable LiveKit
speculative reasoning. Earlier one-off room timings above used inherited
turn-detection defaults; they are not measurements of this changed configuration.
The repeatable spoken test reports median and slowest observed latency for up to
five turns in one room, rather than presenting a best sample as typical.
The five-turn run above used one stock synthetic phrase repeatedly, did not
capture a person's microphone and saved no response audio. Browser playback was
checked in a muted, isolated test browser: its media clock advanced and real RTC
inbound audio energy was nonzero. That is not a human listening assessment.

Actual browser checks verified a typed reply from the configured services,
zero microphone requests before explicit activation, no transcript keys in local
storage, successful Stop reply acknowledgement, and End session returning to
ready only after drain. A simulated microphone denial displayed an actionable
message and left typed sending enabled. The direct agent token now explicitly
permits publishing its own state attributes; otherwise the React SDK correctly
remains in Connecting despite the room transport being connected.

All checks use actual SDK surfaces rather than substituting a fake connected UI.
Simulated permission-denial and component tests are not evidence of microphone
speech accuracy on another device.

Desktop 1200×830 and mobile 390×844 checks found no horizontal overflow and kept
the message composer inside the viewport. Forty rendered text elements had a
minimum measured contrast of 6.1:1. Keyboard focus remained visible and reduced
motion kept content visible. Source tests describe CSS contracts separately
from those actual browser observations.

Opt-in tests are separate from ordinary CI and never silently skip after being
explicitly enabled:

```sh
# One direct Voicebox synthesis. Select an authorized profile explicitly.
VOICEBOX_INTEGRATION=1 VOICEBOX_TEST_PROFILE="$VOICEBOX_PROFILE" \
  uv run --no-sync pytest tests/integration/test_voicebox_live.py -q -s

# Stop Studio and other workers first. Two real rooms, replies, stop and reconnect.
STUDIO_INTEGRATION=1 \
  uv run --no-sync pytest tests/integration/test_studio_live.py -q -s

# Against a running Studio, with an explicitly chosen short synthetic audio input:
STUDIO_SPOKEN_INTEGRATION=1 STUDIO_TEST_AUDIO=/private/path/test.wav \
  uv run --no-sync pytest tests/integration/test_spoken_studio.py -q -s

# Local generated audio, cancellation and retry; Studio must already be idle.
STUDIO_AUDITION_INTEGRATION=1 STUDIO_AUDITION_VOICE_ID='AUTHORIZED_SAVED_VOICE_ID' \
  uv run --no-sync pytest tests/integration/test_audition_live.py -q -s
```

## License boundaries

The original adapter is MIT licensed. Reused starter source keeps LiveKit's MIT
notice. LiveKit SDKs are Apache-2.0; NumPy and SoundFile have BSD licenses, and
SoundFile's native libsndfile has LGPL terms. The Qwen MLX model card declares
Apache-2.0. Verify all artifact notices before redistributing binaries or weights.
The local Nemotron runtime is Apache-2.0 with third-party notices; the NVIDIA
weights use the NVIDIA Open Model License. The roughly 700 MB model is
downloaded only through explicit setup and is not included in Git or the wheel.
Optional local narration uses SoundDevice 0.5.6 and PortAudio; preserve their MIT
license notices when redistributing an application bundle.

[Earlier validation evidence](docs/validation-history.md) records readiness
methods, measured results, artifact provenance and licensing boundaries.
Those validation milestones are not the current setup requirements.
