# Local Nemotron speech recognition

**Local STT is opt-in and never falls back to Azure.** `examples.nemotron_stt.NemotronSTT`
connects to a separately installed NVIDIA native CPU sidecar. It does not download
models, start processes, read Copilot caches, or change Studio configuration.
The parent Studio launcher owns starting/stopping the sidecar and selecting this provider.
Qwen remains an independent TTS choice; this setup does not load or modify Qwen.

Studio starts the already-installed sidecar when a Nemotron conversation is
requested, checks CPU/ASR readiness, and stops only the process it owns when
Studio exits. It refuses an occupied port rather than taking over another server.
For a manually running sidecar, stop it before using managed Studio startup.

## 1. What is installed

This is **NVIDIA's official NeMo-Speech.cpp runtime**, not the archived Microsoft
Foundry Local ONNX variant shown as `nemotron-speech-streaming-en-0.6b-generic-cpu`
in some applications. The architecture/model family matches; the artifact and
quantization are different. No private app cache is extracted.

| Component | Exact source |
| --- | --- |
| Native runtime | [NVIDIA/NeMo-Speech.cpp v0.1.0](https://github.com/NVIDIA/NeMo-Speech.cpp/tree/4f9676226f667d14608487df744f375db87127f8), commit `4f9676226f667d14608487df744f375db87127f8` |
| English weights | [Official NVIDIA checkpoint](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b/tree/ebe59e5a817142986528bbbee5dba8db7b38ed50), revision `ebe59e5a817142986528bbbee5dba8db7b38ed50` |
| Only downloaded model | `nemotron-speech-streaming-en-0.6b.q8_0.gguf`, **699,872,960 bytes** |
| SHA-256 | `d9a01898d2a611c8764e23a1c2f45e70bbd5a425dc4de93692ac951dd603812d` |
| Native dependencies | Pinned SentencePiece plus the runtime's pinned ggml and cpp-httplib submodules; revisions in `experiments/nemotron/provenance.json` |
| Build tools | Isolated CMake 3.31.6 and Ninja 1.11.1.4, with their own manifest/lock |

The CPU-only `cpu-asr` preset enables HTTP/WebSocket and disables microphone
capture, diarization, TTS, translation, CUDA, Metal, Vulkan and OpenMP. It builds
with **two compiler jobs**, without Homebrew, sudo, global PATH edits, or Python
ONNX dependencies. HTTP TLS is disabled because the listener is loopback only.
The root environment only needs its existing LiveKit Agents/RTC and aiohttp.

## 2. Prerequisites and explicit installation

Supported/verified target: macOS Apple Silicon with installed Xcode Command Line
Tools (C++17 compiler), Git, Python 3.11+, and `uv`. The same CMake approach is
intended for Linux with an existing C++17 compiler; Linux is not validated here.
Windows is not supported by this setup script.

Check disk first. Approximately 12 GiB free is a comfortable starting budget.
Setup refuses to proceed below **3 GiB free**; source/build/model should fit in
roughly 1 GiB, with additional space needed for tool environments and caches.
CPU inference still shares RAM, power and memory bandwidth with GPU-based Qwen.
There is no claim that the 700 MB weight file is the runtime's peak RAM usage.

From the repository root, run each explicit phase:

```bash
df -h .
uv sync --project experiments/nemotron
python tools/setup_nemotron.py --build
python tools/setup_nemotron.py --download
python tools/setup_nemotron.py --check
```

Defaults: `~/.local/share/voicebox-studio/nemotron/` for source, build, notices and
the one verified model. Use `--runtime-dir PATH` on **every** command to choose a
different private directory, and `--model-path PATH` to supply/reuse the approved
GGUF. Paths are not taken from private application caches. Existing mismatched
files are rejected, never silently replaced. Download is opt-in, size-bounded,
SHA-256 verified, and written through a private `.part` file.

For a checkout-local experiment, substitute
`--runtime-dir experiments/nemotron/.native` on every command. That directory is
gitignored. Setup steps are serialized by `.setup.lock`; do not run build and
download together in the same runtime directory. A crashed installer can leave
the lock; check for an active setup process before explicitly removing it.

## 3. Launch and readiness

The **foreground** command below never installs or downloads:

```bash
python tools/setup_nemotron.py --serve --port 8766
```

It checks the model, refuses a busy port, strips inherited `NEMO_SPEECH_*` model
overrides, and replaces itself with the native process. A Studio launcher can
own/terminate that process directly. Ctrl-C stops a manually launched sidecar.

Its native command is equivalent to:

```bash
<runtime-dir>/build/bin/nemo-speech serve \
  --host 127.0.0.1 --port 8766 --device cpu \
  --asr-model <absolute-approved-model.gguf> \
  --no-ui --threads 4 --max-upload-mb 64 \
  --read-timeout 30 --write-timeout 10 \
  --asr.batching.enabled=false \
  --asr.batching.state_arena_slots 1 \
  --asr.streaming.rnnt_right_context 1
```

`--threads 4` bounds HTTP workers, **not** every native inference thread.
Batching is disabled for this single-room use case. The upload limit bounds
audio accepted between explicit commits (about 35 minutes at 16 kHz mono PCM16);
long-running sessions should reconnect/commit before reaching it. No VAD,
punctuation, translation or second speech model is downloaded.

Before using a microphone, verify the service and synthetic protocol smoke:

```bash
curl --fail http://127.0.0.1:8766/ready
curl --fail http://127.0.0.1:8766/v1/models
.venv/bin/python -m experiments.nemotron.smoke_test
```

`/ready` must report `ready: true`, `device: "cpu"` and capability `"asr"`.
The smoke sends **one second of generated silence**, waits for the commit
acknowledgement and prints only event types/usage/timing. It does not record
audio, measure recognition accuracy, or claim room latency.

## 4. Studio integration contract

```python
from examples.nemotron_stt import NemotronSTT

local_stt = NemotronSTT(base_url="http://127.0.0.1:8766", sample_rate=16000)
if not await local_stt.is_ready():
    raise RuntimeError("Local Nemotron is not ready; install/start the sidecar explicitly")
# Pass local_stt as AgentSession(stt=local_stt, ...) with standard LiveKit VAD.
```

The model is English-only. `stream()` accepts mono or stereo PCM16 frames
between 8 and 96 kHz, downmixes stereo once and uses LiveKit RTC's high-quality
resampler once to produce 16 kHz mono. Individual input frames must be at most
200 ms. LiveKit's normal VAD/turn detector should remain enabled for
interruption and turn timing; its speaking-to-listening event commits the utterance.
Do not treat the first partial as a final or use a fixed sleep to end a turn.

**Wire protocol:** NVIDIA's [pinned realtime API](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/4f9676226f667d14608487df744f375db87127f8/docs/api.md)
is project-specific, not OpenAI Realtime. The bridge waits for `session.created`,
sends `session.update`, waits for `session.updated`, then sends binary
little-endian PCM16. Native `delta` pieces accumulate into interim transcripts;
`completed.transcript` supplies the authoritative final. Native revisions are
not separately identified in this protocol, so a non-prefix partial revision
can look append-only until the authoritative final replaces it.

First nonempty text emits `START_OF_SPEECH`; native completion emits
`FINAL_TRANSCRIPT` then `END_OF_SPEECH`. Empty finals do not invent speech.
`flush()` sends `input_audio_buffer.commit` and waits for the native
`input_audio_buffer.committed` acknowledgement. Another turn can follow on the
same socket. `end_input()` finishes the last commit; finalization has a
configurable deadline (15 seconds by default), not an artificial delay.
Usage events account for actually sent audio at explicit commits.

**Conversational finalization:** this pinned native build has automatic
endpointing disabled by default. Setting WebSocket `endpointing_ms` alone does
not enable it. Studio therefore calls `commit_utterance()` when LiveKit's actual
VAD reports the user changing from speaking to listening. This preserves native
partial results while producing a real final transcript at the turn boundary.
No extra VAD model or fixed-delay final transcript is fabricated.

The socket sends a heartbeat so a paused microphone can resume without silently
losing the session. A 35-second idle pause followed by a successful commit was
verified against the native CPU server.

Each stream owns its socket. Closing it sends a best-effort bounded `clear`
and cancels/closes that socket only; it never stops the shared sidecar.
Closing the provider closes its own streams.

**Limits and failure behavior:**

- Input queue defaults to two seconds of audio plus one in-flight <=200 ms frame,
  with a separate 256-item cap. Synchronous `push_frame()` raises `BufferError`
  on overload and fails that stream; stop/recreate it instead of dropping audio.
- Native incoming messages are capped at 64 KiB; accumulated interim text at
  32 KiB; queued consumer events at 128. Slow consumption is an explicit failure.
- Connection establishment can use LiveKit retries **only before audio is sent**.
  After audio begins, disconnect/rejection/timeout is non-retryable to avoid
  silently losing/replaying a user's utterance.
- URLs must use literal loopback IPs with no credentials, query or path.
  Redirects are refused. Native error payloads and transcripts are not logged
  in adapter exceptions. Never expose this unauthenticated sidecar to a network.
- Missing/failed local STT is reported explicitly. Azure requires a separate
  explicit provider choice; there is no cloud fallback.

## 5. Provenance, licensing, and validation

The GGUF remains outside source control. NVIDIA's model is governed by the
[NVIDIA Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/),
including its use conditions and Trustworthy AI terms. If redistributing the
model, include a copy of the agreement and the required Notice:
**“Licensed by NVIDIA Corporation under the NVIDIA Open Model License”**.
Do not assume the repository's license covers the weights.

The native code is Apache-2.0 with its own NOTICE and third-party licenses.
Setup preserves the runtime and SentencePiece notices in `<runtime-dir>/licenses`;
upstream sources retain the submodules' notices. Redistribution must include
all applicable notices and agreements, not just the Python bridge's license.

The model's 80/160/560/1120 ms native chunk settings describe audio context,
**not measured wall-clock response latency**. The selected RNNT right-context
setting is the upstream low-latency preset, not a performance guarantee.

Focused checks, without model downloads or audio recording:

```bash
.venv/bin/python -m pytest tests/test_nemotron_stt.py -q
.venv/bin/ruff check examples/nemotron_stt.py tests/test_nemotron_stt.py tools/setup_nemotron.py
.venv/bin/mypy examples/nemotron_stt.py tools/setup_nemotron.py
```

Tests were written before the adapter/setup implementation. They exercise the
actual native JSON shapes through a mock aiohttp WebSocket server: partials,
finals, PCM conversion, delayed commit, bounded timeout/queues, cancellation,
readiness and private error handling. Native synthetic smoke results are
separate from those mocked tests; neither substitutes for an explicitly
authorized real-room test owned by the Studio launcher.

On 2026-09-11, the pinned build and model checksum passed on macOS Apple Silicon.
The native sidecar reported CPU/ASR readiness and acknowledged one second of
synthetic silence in 0.181 seconds of test wall time, emitting only usage (no
invented transcript). The sidecar was stopped afterward. The 32 focused tests,
Ruff and mypy passed; compact evidence is in `experiments/nemotron/validation.json`.

Later integration checks used an installed stock synthetic voice, not a captured
person: a 3.69-second phrase produced a correct final containing the expected
words, with first partial around 0.95 seconds. A full LiveKit audio-input test
then traversed **local Nemotron → Copilot Luna/low → local Qwen**, receiving
non-silent response audio about **4.07 seconds after the synthetic speech ended**.
This includes turn finalization, cloud reasoning and speech generation. It is
not a real-human accent evaluation or a latency distribution.
