# BUILD.md — `livekit-plugins-voicebox`

> Agent-executable implementation plan for a local-first Voicebox TTS provider for LiveKit Agents.

**Status:** Ready for a constrained technical spike; v0.1 release remains gated
**Primary deliverable:** `livekit-plugins-voicebox` Python package

**Import path:** `from livekit.plugins import voicebox`

**Reference repositories:** `jamiepine/voicebox`, `livekit/agents`

**Initial scope:** Voicebox TTS integration + working LiveKit voice-agent demos

**Secondary scope:** Azure OpenAI demo, GitHub Copilot SDK demo/adapter
**Deferred scope:** upstream true incremental Voicebox audio streaming

**Review revision:** September 10, 2026. This edited copy preserves the original
plan separately as `BUILD.original.md`. It is an implementation specification,
not evidence that the package exists or that performance gates have passed.

## Authoritative release boundary

Where later examples or milestones imply broader scope, this boundary wins:

| Stage | Required scope | Exit gate |
| --- | --- | --- |
| Technical spike | Qwen 0.6B, one named backend/hardware configuration, one exclusive local agent, real room audio | Measured conversation latency and interruption recovery meet explicitly approved limits |
| v0.1 | Core provider, profile switching, lifecycle safety, minimal and Azure examples, compatibility documentation | All release gates below pass; advertise only measured engine/backend combinations |
| v0.2 | Browser demo, additional engine support, optional Copilot demo | Separate acceptance criteria; none blocks v0.1 |
| Later upstream work | Incremental generation, backend-wide serialization and cooperative cancellation | Early audio plus bounded resource use and proven worker-lifecycle behavior |

Do not describe the MVP as production-ready multi-client serving. Its temporary
exclusive-backend requirement includes avoiding Voicebox desktop generation and
other workers against the same server while the agent runs.

---

## 0. Mission

Build the smallest production-quality bridge that lets a LiveKit Agent speak through a locally running Voicebox voice profile.

The target developer experience is:

```python
from livekit.agents import AgentSession
from livekit.plugins import voicebox

session = AgentSession(
    stt=...,
    llm=...,
    tts=voicebox.TTS(
        profile="Authorized Voice",
        engine="qwen",
        model_size="0.6B",
    ),
)
```

The integration must preserve the ownership boundary:

```text
LiveKit
  = realtime transport, conversation orchestration, interruption, metrics

LLM provider
  = reasoning / response generation

Voicebox
  = local voice profiles, cloning, TTS inference

livekit-plugins-voicebox
  = adapter between LiveKit's TTS contract and Voicebox's HTTP API
```

Do not rebuild any of these systems.

---

# 1. Non-negotiable engineering rules

The coding agent MUST follow these rules.

1. **Do not fork LiveKit or Voicebox for v0.1.**
2. **Do not modify `jamiepine/voicebox` during the initial adapter implementation.**
3. **Do not build a custom WebRTC stack.**
4. **Do not build STT.**
5. **Do not build an LLM.**
6. **Do not copy large amounts of provider code from LiveKit. Follow its interface and patterns.**
7. **Do not claim Voicebox currently performs true incremental HTTP TTS streaming. It does not.**
8. **Do not claim cancellation stops underlying Voicebox inference in v0.1. Suppress stale audio and retain the backend-use lease until known completion; disconnects/timeouts can leave completion unknown.**
9. **Do not hard-code a user's voice profile ID.**
10. **Do not upload reference voice samples to LiveKit or an LLM provider.**
11. **Default Voicebox URL must remain loopback-only: `http://127.0.0.1:17493`.**
12. **Do not automatically expose Voicebox to the Internet.**
13. **Never log reference audio, generated audio, or full synthesized text at INFO level.**
14. **Tests must not require a GPU unless explicitly marked integration/benchmark.**
15. **Every commit described below must leave the repository in a passing state.**
16. **Use voices only with the speaker's authorization. Demo assets must be synthetic/publicly licensed or recorded by the developer.**

---

# 2. Verified upstream facts

Before implementation, inspect upstream and record the exact revisions used.
Default branches are discovery sources, not reproducible compatibility targets.

Source-review baseline (not an executed compatibility certification):

* Voicebox: `51f49dea198384b4eb6087b72c17057c6eb1c1cd`.
* LiveKit Agents: `4de62322fa84b7c1736370ff3ebbd66b5d0ffe6f`.
* MLX Audio 0.4.1: `3163f1eff4625a545c2abf98759df129fa6e1b56`.

Reconcile changed contracts explicitly rather than silently mixing revisions.

## Voicebox

Current architecture:

```text
React/Tauri UI
      ↓ HTTP
FastAPI backend
      ↓
voice profile system
      ↓
TTS engine registry
      ↓
Qwen / Chatterbox / LuxTTS / TADA / Kokoro / ...
```

Default local API:

```text
http://127.0.0.1:17493
```

Relevant routes:

```text
GET  /health
GET  /profiles
GET  /profiles/{profile_id}
POST /generate/stream
```

Relevant source paths:

```text
backend/routes/generations.py
backend/routes/profiles.py
backend/models.py
backend/backends/__init__.py
backend/backends/mlx_backend.py
backend/utils/chunked_tts.py
backend/services/tts.py
backend/services/task_queue.py
```

Current `POST /generate/stream` behavior:

```text
request
  ↓
generate_chunked(...)
  ↓
complete NumPy audio array
  ↓
optional effects
  ↓
normalization
  ↓
encode complete WAV
  ↓
yield WAV in HTTP chunks
```

This is **HTTP response chunking after generation**, not true generation streaming.

Important Qwen/MLX finding:

```python
for result in self.model.generate(...):
    audio_chunks.append(np.array(result.audio))
```

Yielding `result.audio` is not proof of incremental audio within an utterance.
At the reviewed baseline, Voicebox omits `stream=True`; MLX Audio defaults to
non-streaming and may yield completed segments. Its genuine streaming mode is a
promising future seam, but must be explicitly enabled and integrated through the
backend, post-processing, transport, and cancellation layers.

References:

* [Voicebox MLX call and accumulation](https://github.com/jamiepine/voicebox/blob/51f49dea198384b4eb6087b72c17057c6eb1c1cd/backend/backends/mlx_backend.py#L224-L264)
* [MLX Audio generation parameters](https://github.com/Blaizzy/mlx-audio/blob/3163f1eff4625a545c2abf98759df129fa6e1b56/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py#L862-L909)
* [Voicebox completed-WAV response](https://github.com/jamiepine/voicebox/blob/51f49dea198384b4eb6087b72c17057c6eb1c1cd/backend/routes/generations.py#L318-L414)

Voicebox uses `soundfile` to write the returned WAV.

## LiveKit Agents

Relevant source paths:

```text
livekit-agents/livekit/agents/tts/tts.py
livekit-agents/livekit/agents/tts/stream_adapter.py
livekit-agents/livekit/agents/voice/agent.py
livekit-plugins/*/livekit/plugins/*/tts.py
```

A provider implements:

```python
class TTS(tts.TTS):
    ...
    def synthesize(...) -> tts.ChunkedStream:
        ...
```

and a chunked stream implements:

```python
class ChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        ...
```

A non-streaming TTS declares:

```python
tts.TTSCapabilities(streaming=False)
```

LiveKit can wrap a non-streaming TTS in `tts.StreamAdapter`, allowing incoming LLM output to be segmented and synthesized incrementally at sentence boundaries.

This is the correct v0.1 path.

---

# 3. Definition of v0.1

v0.1 is complete when a developer can:

1. Run Voicebox locally.
2. Have at least one cloned Voicebox profile.
3. Install this package.
4. Instantiate `voicebox.TTS(profile="...")`.
5. Use that object directly in `AgentSession`.
6. Speak to a LiveKit agent.
7. Hear its response using the selected local Voicebox voice.
8. Interrupt agent playback without stale audio later leaking into the room.
9. Switch profile between turns.
10. Observe correct LiveKit TTS metrics.
11. Receive useful errors when Voicebox is offline, a profile is missing, or generation fails.
12. Run unit tests without Voicebox.
13. Run optional integration tests against a real Voicebox instance.

v0.1 does **not** require true incremental Voicebox generation.

---

# 4. Repository layout

Create exactly this initial structure:

```text
livekit-plugins-voicebox/
├── livekit-plugins-voicebox/
│   ├── livekit/
│   │   └── plugins/
│   │       └── voicebox/
│   │           ├── __init__.py
│   │           ├── client.py
│   │           ├── errors.py
│   │           ├── models.py
│   │           ├── tts.py
│   │           └── version.py
│   └── pyproject.toml
│
├── examples/
│   ├── minimal_agent.py
│   ├── azure_agent.py
│   └── README.md
│
├── tests/
│   ├── conftest.py
│   ├── test_client.py
│   ├── test_models.py
│   ├── test_tts.py
│   ├── test_errors.py
│   ├── test_cancellation.py
│   └── integration/
│       └── test_voicebox_live.py
│
├── benchmarks/
│   ├── benchmark_tts.py
│   └── README.md
│
├── .github/
│   └── workflows/
│       └── ci.yml
│
├── .gitignore
├── LICENSE
├── README.md
├── BUILD.md
└── CONTRIBUTING.md
```

If upstream LiveKit's current plugin packaging layout has materially changed, adopt the current official convention while retaining the public import:

```python
from livekit.plugins import voicebox
```

Do not invent a second public namespace.

---

# 5. Package dependencies

Keep runtime dependencies minimal.

Required:

```text
livekit-agents
aiohttp
numpy
soundfile
```

Use LiveKit's existing RTC audio utilities for audio frames/resampling where practical rather than adding SciPy solely for resampling.

Development dependencies:

```text
pytest
pytest-asyncio
pytest-cov
ruff
mypy
```

Optional example dependencies must be extras, not core requirements. Create the
Copilot extra and `examples/copilot_agent.py` only in the optional v0.2 phase.

Example:

```toml
[project.optional-dependencies]
azure = [...]
copilot = ["github-copilot-sdk"]
dev = [...]
```

Do not guess the exact current package name for Copilot SDK. Resolve it from GitHub's current official Python installation instructions at implementation time and pin/constraint it appropriately.

---

# 6. Public API contract

## `voicebox.TTS`

Implement:

```python
class TTS(tts.TTS):
    def __init__(
        self,
        *,
        profile: str,
        base_url: str = "http://127.0.0.1:17493",
        engine: str | None = "qwen",
        model_size: str | None = "0.6B",
        language: str = "en",
        instruct: str | None = None,
        sample_rate: int = 24000,
        request_timeout: float = 60.0,
        http_session: aiohttp.ClientSession | None = None,
        max_concurrent_requests: int = 1,
    ) -> None:
        ...
```

### Semantics

`profile`
: Voicebox profile ID or exact profile name.

`base_url`
: Voicebox API origin. Strip trailing `/`.

`engine`
: Optional Voicebox engine key. Resolve `None` locally from the selected profile
  and validated compatibility rules. Omitting the field currently selects the
  API schema default `qwen`, not necessarily the profile's default engine.

`model_size`
: Optional model size. The plugin deliberately defaults to Qwen `0.6B`, while
  the reviewed Voicebox API defaults to `1.7B`. Resolve and validate against the
  effective engine; never silently reinterpret an incompatible size. Callers
  selecting a single-model engine must use `model_size=None`.

`language`
: Generation language sent to Voicebox.

`instruct`
: Optional engine-specific natural-language delivery instruction.

`sample_rate`
: Stable per-instance output rate emitted to LiveKit. Default `24000`, mono
  PCM16. Validate supported positive rates at construction and never change this
  contract through `update_options`.

`request_timeout`
: Foreground synthesis deadline, including local queue wait and response
  handling. Expiry after possible submission does not prove inference stopped:
  suppress audio and follow the drain/uncertain-state policy in sections 13-15.

`http_session`
: Optional externally managed session.

`max_concurrent_requests`
: Must equal `1` for v0.1; reject other values as unsupported. This limit applies
  only to this client's owned jobs, not to the Voicebox server as a whole.

### Effective configuration and readiness

Validate engine/model/language/profile combinations against the pinned upstream
contract, not just the request schema's union of allowed values. Preset profiles
are engine-specific; cloned profiles require usable reference samples.

`health()` reports connectivity and observed server fields, not selected-engine
readiness. At the reviewed revision, health primarily checks default Qwen state.
Before an interactive session, explicitly resolve the profile and establish
selected-model readiness/warm-up using supported upstream interfaces. Do not
download models automatically or call a cached-but-unloaded model "warm".

### Required properties

```python
@property
def model(self) -> str:
    ...

@property
def provider(self) -> str:
    return "Voicebox"
```

Suggested `model` value:

```text
qwen:0.6B
chatterbox_turbo
...
```

Do not include profile name in `model`.

### Capabilities

v0.1 MUST declare:

```python
capabilities=tts.TTSCapabilities(
    streaming=False,
)
```

Do not set `streaming=True` merely because the HTTP response uses `StreamingResponse`.

### Required methods

```python
def synthesize(
    self,
    text: str,
    *,
    conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
) -> ChunkedStream:
    ...

async def list_profiles(self) -> list[VoiceProfile]:
    ...

async def health(self) -> HealthResult:
    ...

async def resolve_profile(self, *, refresh: bool = False) -> VoiceProfile:
    ...

def update_options(
    self,
    *,
    profile: NotGivenOr[str] = NOT_GIVEN,
    engine: NotGivenOr[str | None] = NOT_GIVEN,
    model_size: NotGivenOr[str | None] = NOT_GIVEN,
    language: NotGivenOr[str] = NOT_GIVEN,
    instruct: NotGivenOr[str | None] = NOT_GIVEN,
) -> None:
    ...

async def aclose(self) -> None:
    ...
```

If current LiveKit public helper types differ (`NotGivenOr`, etc.), use the current supported API rather than importing private internals.

---

# 7. Internal data models

Create `models.py`.

Minimum:

```python
@dataclass(frozen=True)
class VoiceProfile:
    id: str
    name: str
    language: str
    voice_type: str
    default_engine: str | None
    preset_engine: str | None
    sample_count: int | None = None
```

```python
@dataclass(frozen=True)
class HealthResult:
    ok: bool
    status: str | None
    model_loaded: bool | None
    raw: Mapping[str, Any]
```

Do not mirror every field in Voicebox's response. Parse only fields required by this package and preserve unknown health payload data in `raw`.

---

# 8. `VoiceboxClient`

Implement all HTTP details in `client.py`.

No route construction belongs in `tts.py` except through the client.

## Constructor

```python
class VoiceboxClient:
    def __init__(
        self,
        *,
        base_url: str,
        request_timeout: float,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        ...
```

Track whether the session is internally owned.

## Methods

```python
async def health(self) -> HealthResult:
    ...
```

Request:

```text
GET /health
```

Any 2xx response is HTTP connectivity success. Validate the JSON payload before
constructing `HealthResult`; `ok` must not be presented as synthesis readiness.

---

```python
async def list_profiles(self) -> list[VoiceProfile]:
    ...
```

Request:

```text
GET /profiles
```

Validate that the top-level response is a list.

---

```python
async def resolve_profile(self, value: str) -> VoiceProfile:
    ...
```

Algorithm:

```text
1. list profiles
2. exact ID match
3. otherwise unique exact case-sensitive name match; fail if duplicated
4. otherwise exact case-insensitive name match IF uniquely resolvable
5. if none -> ProfileNotFoundError
6. if >1 match at the applicable name tier -> AmbiguousProfileError
```

Never silently pick the first fuzzy match.

---

```python
async def synthesize_wav(
    self,
    *,
    profile_id: str,
    text: str,
    engine: str | None,
    model_size: str | None,
    language: str,
    instruct: str | None,
) -> bytes:
    ...
```

Request:

```text
POST /generate/stream
Content-Type: application/json
```

Payload should contain only meaningful Voicebox `GenerationRequest` fields.
Resolve effective engine/model defaults before serialization; omitted fields
must have verified server semantics, not assumed profile-default behavior.

Minimum:

```json
{
  "profile_id": "...",
  "text": "...",
  "language": "en"
}
```

Conditionally add:

```json
{
  "engine": "qwen",
  "model_size": "0.6B",
  "instruct": "..."
}
```

Do not send:

```text
personality=true
effects_chain
long-form chunk settings
```

by default in an interactive voice agent.

Omitting effects/chunk settings does not disable Voicebox defaults or inherited
profile effects. Document the effective server behavior, benchmark it, and use
an explicit effects override only when its semantics are verified. Do not claim
an unprocessed fast path merely because those fields are absent.

Bound accepted text length, response bytes, decoded duration, channel count, and
sample rate. Establish documented limits from the sentence-oriented scope;
reject excess explicitly. A timeout is not a memory-allocation limit.

---

# 9. Error model

Create `errors.py`.

Internal exception hierarchy:

```python
class VoiceboxError(Exception):
    pass

class VoiceboxConnectionError(VoiceboxError):
    pass

class VoiceboxTimeoutError(VoiceboxError):
    pass

class VoiceboxAPIError(VoiceboxError):
    status_code: int | None

class ProfileNotFoundError(VoiceboxError):
    pass

class AmbiguousProfileError(VoiceboxError):
    pass

class InvalidAudioError(VoiceboxError):
    pass
```

`VoiceboxClient` raises these.

The LiveKit `ChunkedStream` layer maps them to the appropriate current LiveKit error types.

Desired mappings:

```text
VoiceboxConnectionError -> APIConnectionError
VoiceboxTimeoutError    -> APITimeoutError
HTTP 4xx/5xx            -> APIStatusError
```

Preserve useful Voicebox response detail only after sanitizing it, and cap its
length. Validation responses can echo submitted text; truncation alone does not
make them safe to log. Use an allowlist of safe diagnostic fields.

Mark request/configuration failures non-retryable. A 404 may indicate an absent
route rather than a missing profile; distinguish endpoint and profile failures.
Model readiness errors need their own actionable classification, not automatic
retry based solely on status code.

Do not log user text when formatting errors.

---

# 10. Audio pipeline

## Input

Voicebox currently returns a completed WAV body.

Decode using `soundfile` from memory:

```python
audio, source_rate = sf.read(
    io.BytesIO(wav_bytes),
    dtype="float32",
    always_2d=True,
)
```

### Channel handling

If multiple channels are returned:

```text
downmix to mono
```

using a mean across channels.

### Safety

Before conversion:

```text
reject NaN / inf
reject zero-frame output
clip float waveform to [-1.0, 1.0]
```

### PCM conversion

Convert to signed little-endian PCM16:

```python
pcm16 = (audio * 32767.0).astype("<i2")
```

Avoid integer overflow around `-1.0`; use a well-tested helper.

### Resampling

Prefer LiveKit's current `rtc.AudioResampler` API.

If source sample rate equals target rate:

```text
no resampling
```

If it differs:

```text
source AudioFrame
      ↓
rtc.AudioResampler
      ↓
target-rate frames
```

Avoid a SciPy dependency solely for resampling.

### Output framing

Push audio through LiveKit's `tts.AudioEmitter`.

Initialize it with:

```text
request_id = shortuuid/current supported helper
sample_rate = target sample rate
num_channels = 1
mime_type = "audio/pcm"
```

Then push PCM bytes and flush. Flush the resampler first and emit its tail;
maintain sample alignment and derive duration from actual emitted sample counts.
Decode using the WAV header rather than engine-name sample-rate assumptions.

Use an output chunk/frame size appropriate to current LiveKit plugin examples. Do not emit one massive multi-second frame if current LiveKit utilities expect frame-sized chunks.

---

# 11. `ChunkedStream` implementation

In `tts.py`:

```python
class ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: TTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        ...
```

Core `_run` sequence:

```text
START
  ↓
snapshot profile selector and all generation options when stream is created
  ↓
resolve that snapshot's profile (cache keyed by selector/configuration revision)
  ↓
acquire exclusive client job lease; reject if backend state is uncertain
  ↓
submit independently owned HTTP job; foreground waits without cancelling it
  ↓
receive completed WAV
  ↓
release backend-use lease only on known job completion
  ↓
decode WAV
  ↓
downmix
  ↓
resample if required
  ↓
initialize AudioEmitter
  ↓
push PCM frames
  ↓
flush
  ↓
END
```

Do not implement this as `async with semaphore: await http_request()` inside the
cancelled foreground stream: its context exit releases the semaphore while
Voicebox may still be computing.

The client owns and tracks the submitted job independently. A cancelled consumer
propagates cancellation promptly, detaches from output, and leaves a bounded
drain task owning the lease. Observe all task failures explicitly. Use cooperative
cancellation checkpoints during bounded frame emission; a synchronous push loop
must not enqueue the entire response before cancellation can be processed.

Use the pinned SDK's public stream-lifecycle and metrics hooks. Do not rely on
private emitters or a second custom metrics implementation.

---

# 12. Profile cache behavior

Profile discovery is cheap compared with TTS but should not occur on every sentence.

`TTS` stores cache entries keyed by the snapshotted profile selector and a
configuration revision, rather than one unqualified mutable result:

```text
configured_profile_value
configuration_revision
resolved_profiles_by_snapshot
```

On first synthesis:

```text
resolve and cache
```

On:

```python
update_options(profile="Other Voice")
```

invalidate the current configuration's cached resolution. A late resolution
from a prior revision must not overwrite the current cache. In-flight streams
continue resolving their own saved selector, never the new global selector.

Also expose:

```python
await tts.resolve_profile(refresh=True)
```

Do not implement a complex TTL cache in v0.1 unless required by tests.

---

# 13. Cancellation semantics

This section is critical.

LiveKit may cancel a TTS task when the user interrupts.

v0.1 MUST guarantee:

> Once the LiveKit synthesis task is cancelled, that turn must not emit any later audio frames.

Implementation rules:

1. Propagate `asyncio.CancelledError`.
2. Never catch cancellation and convert it to a retry.
3. If cancellation occurs before `AudioEmitter` receives audio, emit nothing.
4. If cancellation occurs during output, stop pushing immediately.
5. Before submission, cancel freely. After possible submission, detach the
   consumer but keep an independently owned HTTP job draining without emitting.
6. Do not retry a cancelled request.

### Known upstream limitation

Voicebox inference may continue on its own worker/thread even if the HTTP client disconnects.

Therefore do **not** claim:

```text
"interrupting cancels local GPU generation"
```

in v0.1.

Correct claim:

```text
"interrupting stops agent playback immediately; underlying Voicebox generation
cancellation depends on Voicebox's current backend behavior."
```

This limitation becomes part of the true-streaming upstream phase.

### Backend-use lifecycle

Use explicit states: idle, active, draining, uncertain, and closed.

* Cancel while queued: no POST and no backend state change.
* Cancel after submission: stop the consumer immediately; retain the lease while
  the owned job drains. Only a confirmed completed job can restore idle state.
* Transport loss, drain deadline, or ambiguous post-submission failure: close
  local resources and enter uncertain state. Reject further synthesis instead
  of releasing a success-shaped permit and starting overlapping work.
* Recovery: require explicit operator confirmation that the old inference has
  stopped (for example, a completed backend restart) and recreate the client.
  `/health` success and a fixed sleep do not establish inference completion.
* Shutdown: stop admissions, cancel unsent work, drain submitted jobs within a
  documented bound, then close owned resources. Surface unresolved backend work.
  Never close a caller-owned HTTP session.

The implementation must define a finite drain bound separately from the
foreground deadline. This policy favors safe failure over hidden GPU contention
and may worsen post-interruption latency; include that cost in the spike gate.
Do not put the foreground deadline directly on the independently owned HTTP
request if that would abort its drain. Configure separate bounded transport/drain
limits, and document the maximum total lifetime of a submitted job.

---

# 14. Retry policy

Do not invent aggressive retries.

For TTS:

```text
connection failure proven to occur before POST submission:
    a bounded retry may respect LiveKit conn_options

HTTP configuration/profile/model-readiness failure:
    do not retry

HTTP 5xx, disconnect, or timeout after possible submission:
    no automatic replay; resolve job completion or enter uncertain state

any failure after audio emission:
    never replay the utterance automatically

cancel:
    never retry
```

Default generation replay to disabled using the pinned LiveKit public error and
connection-option interfaces. `conn_options` cannot override cancellation or
unknown-completion safety. Do not infer "not submitted" from a generic connection
exception. Voicebox has no established idempotency contract for this route.

---

# 15. Concurrency policy

Voicebox's normal generation workflow has a serial queue to avoid GPU contention, while `/generate/stream` currently calls inference directly.

v0.1 MUST therefore protect itself.

Default:

```python
max_concurrent_requests=1
```

An async semaphore may implement lease admission, but ownership belongs to the
actual submitted job, not the foreground synthesis task. Hold it through drain
completion; unknown backend state closes admission rather than restoring idle.

Document:

> v0.1 requires one TTS client and exclusive use of its Voicebox server. Do not
> concurrently generate from another instance, worker process, or desktop UI.
> The plugin cannot enforce server-wide serialization. Interruptions stop audio,
> but the next turn may wait for abandoned inference to finish.

Do not advertise production GPU multiplexing.

Backend-wide serialization must hold until the inference worker really exits,
including during cancellation, before this exclusive-use restriction can lift.

---

# 16. LiveKit sentence streaming

The plugin itself declares:

```python
streaming=False
```

Do not implement a fake `stream()` method.

Let LiveKit's current `StreamAdapter` convert incoming streamed LLM text into sentence-sized calls to `synthesize()`.

Verify with a real `AgentSession` that the current LiveKit voice pipeline automatically wraps a non-streaming TTS implementation as expected.

Test conversational behavior with responses containing at least three sentences.

Expected:

```text
LLM produces sentence 1
      ↓
Voicebox generates sentence 1
      ↓
play sentence 1

while later text continues through the agent pipeline
```

This is **sentence-level streaming**, not true TTS model streaming. Use that exact terminology in docs.

---

# 17. `update_options`

Support changing voices without reconstructing the session.

Example:

```python
tts.update_options(profile="Morgan")
```

Implementation requirements:

```text
update profile string
invalidate resolved-profile cache
do not close HTTP client
do not reload anything inside Voicebox directly
```

Engine/model/language/instruct changes update future requests only.

Do not mutate an in-flight request's options. `ChunkedStream` should snapshot options on creation using a dataclass copy.

---

# 18. Unit test fixtures

Mock Voicebox with an in-process aiohttp test server or appropriate async HTTP mocking library.

Fixtures:

```text
healthy server
server offline
profile list
duplicate profile names
valid 24k mono WAV
valid 48k mono WAV
stereo WAV
empty WAV
corrupt WAV
slow generation response
400 response
404 response
500 response
```

Generate WAV fixtures programmatically in tests. Do not commit voice recordings.

---

# 19. Unit test requirements

## `test_client.py`

Must verify:

```text
health success
health connection failure
profiles parsed
profile ID resolution
profile exact-name resolution
profile unique case-insensitive resolution
profile not found
ambiguous case-insensitive match
duplicate exact-name rejection
generation payload
optional payload fields omitted correctly
effective profile/engine/model/language compatibility
health connectivity distinguished from selected-engine readiness
400 error mapping
500 error mapping
timeout
session ownership / close behavior
```

## `test_models.py`

Verify defensive parsing and defaults.

## `test_tts.py`

Verify:

```text
provider == "Voicebox"
model label stable
streaming capability == False
synthesize returns ChunkedStream
correct request options snapshotted
profile cache works
profile update invalidates cache
WAV -> PCM output succeeds
48k input -> configured output rate
stereo downmix
zero audio rejected
corrupt audio rejected
```

## `test_cancellation.py`

Test at least:

### Cancel before response

```text
start synthesis
server blocks
cancel task
assert CancelledError
assert no AudioEmitter audio emitted
```

### Cancel during emission

Use sufficiently long synthetic output and an instrumented emitter.

```text
begin output
cancel
assert no additional frames after cancellation observed
```

### No stale response reuse

```text
cancel turn A
run turn B
assert turn A audio cannot appear in B
```

### Cancellation does not release inference ownership

Use a fake server whose inference continues after consumer cancellation:

```text
start A and confirm server work began
cancel A's consumer
request B
assert B is not submitted while A remains active
finish A and its drain
assert B can now submit and only B's audio reaches its consumer
```

Also cover cancellation while waiting for admission, transport loss with
unknown completion, drain deadline expiry, bounded shutdown, external-session
ownership, and stale profile-resolution completions after `update_options`.
Assert no retry after partial output. Test actual room playback interruption
separately; emitter-level assertions alone do not cover already queued audio.

---

# 20. Integration test

Mark:

```python
@pytest.mark.integration
```

Skip unless explicitly enabled:

```text
VOICEBOX_INTEGRATION=1
```

When explicitly enabled, missing configuration or an unreachable/unready
Voicebox server must fail with an actionable error, not silently skip.

Required environment variable:

```text
VOICEBOX_TEST_PROFILE
```

Test:

```text
resolve profile
synthesize "This is a Voicebox LiveKit integration test."
assert >0 audio frames
assert output duration >0
```

Never infer a random local voice profile for CI.

---

# 21. CI

GitHub Actions matrix:

```text
Python 3.11
Python 3.12
Python 3.13
```

Only include versions supported simultaneously by the current LiveKit dependency.

Run:

```text
ruff check
ruff format --check
mypy
pytest -m "not integration" --cov
```

Fail CI if tests fail.

Do not attempt GPU Voicebox integration in standard public CI.

---

# 22. Commit plan

The coding agent must work in this order.

Milestones 3-6 below form one atomic core-provider commit: audio conversion,
profile resolution, option snapshots, and cancellation ownership are prerequisites
for a usable `TTS`. Do not publish or commit an intentionally broken provider
between those milestones. Earlier and later commits remain independently passing.

---

## Commit 1 — `chore: scaffold LiveKit Voicebox plugin`

Create:

```text
package layout
pyproject.toml
namespace package
README skeleton
LICENSE
CI
lint/type/test config
```

Acceptance:

```text
python -c "from livekit.plugins import voicebox"
```

works after editable install.

`pytest` runs with at least one smoke test.

**Do not continue if packaging/import path is broken.**

---

## Commit 2 — `feat: add typed Voicebox HTTP client`

Implement:

```text
models.py
errors.py
client.py
```

Routes:

```text
GET /health
GET /profiles
POST /generate/stream
```

Acceptance:

```text
all client tests green
no LiveKit TTS class yet
HTTP session ownership tested
profile resolution deterministic
```

---

## Milestone 3 — core-provider commit: LiveKit interface

Implement:

```text
TTS
ChunkedStream
model/provider properties
capabilities
option snapshotting
error mapping
```

Use current LiveKit plugin patterns as reference.

Acceptance:

```text
AgentSession accepts voicebox.TTS instance
unit tests produce LiveKit audio frames
streaming capability is explicitly false
```

---

## Milestone 4 — core-provider commit: audio normalization

Add robust:

```text
WAV decode
mono conversion
float validation
PCM16 conversion
resampling
frame emission
```

Acceptance:

```text
24k mono fixture passes
48k mono fixture passes
stereo fixture passes
corrupt/empty fixtures fail cleanly
no SciPy dependency solely for resampling
```

---

## Milestone 5 — core-provider commit: profiles and option snapshots

Add:

```text
TTS.list_profiles()
TTS.resolve_profile()
TTS.update_options()
profile cache invalidation
```

Acceptance:

```text
name and ID both work
ambiguous names fail
changing profile affects next synthesis
in-flight synthesis keeps its snapshot
```

---

## Milestone 6 — core-provider commit: cancellation-safe lifecycle

Implement/test cancellation behavior.

Acceptance:

```text
cancelled synthesis emits no later audio
CancelledError propagates
cancel never retries
submitted job drains while retaining its lease
unknown completion blocks further submission explicitly
next turn works after known completion
bounded shutdown closes owned resources
```

Record the upstream Voicebox inference-cancellation limitation in docs. Commit
milestones 3-6 together only after their combined acceptance criteria pass.

---

## Commit 7 — `feat: add minimal LiveKit voice-agent example`

Create:

```text
examples/minimal_agent.py
```

Requirements:

```text
Voicebox profile selected via env/CLI
normal LiveKit-supported STT
normal LiveKit-supported LLM
voicebox.TTS
real conversational AgentSession
```

Do not make the minimal demo dependent on Azure or Copilot.

Acceptance:

```text
developer can follow README from fresh checkout
agent joins a room
agent speaks through selected Voicebox profile
```

---

## Commit 8 — `feat: add Azure OpenAI example`

Create:

```text
examples/azure_agent.py
```

Use LiveKit's current official Azure OpenAI integration.

The Voicebox package must contain **zero Azure-specific code**.

Configuration must come from environment variables expected by the official LiveKit/Azure plugin.

Acceptance:

```text
Azure-backed LLM response reaches Voicebox TTS
README clearly separates Azure setup from Voicebox setup
```

---

## Commit 9 — `feat: add benchmarks and LiveKit TTS metrics validation`

Create:

```text
benchmarks/benchmark_tts.py
benchmarks/README.md
```

Capture:

```text
hardware label
OS
Voicebox version
engine
model size
profile ID hashed/anonymized
character count
request latency
time to first emitted LiveKit audio
audio duration
realtime factor
success/failure
```

Important:

With current Voicebox batch generation:

```text
TTFB ~= server generation completion + WAV decode
```

Label results honestly.

Acceptance:

```text
benchmark runs without modifying package
JSON and human-readable output supported
no raw text/audio saved unless explicit flag
```

---

## Commit 10 — `docs: publish architecture, limitations, and troubleshooting`

README must contain:

```text
what it does
what it does not do
architecture diagram
install
5-minute example
profile selection
engine configuration
Azure example
sentence-level streaming explanation
cancellation limitation
security
responsible voice-cloning guidance
troubleshooting
benchmarks
roadmap
```

Acceptance:

A developer unfamiliar with the implementation can understand the boundary:

```text
LiveKit = realtime conversation
Voicebox = local voice
LLM = brain
```

within the first screenful.

---

# 23. README hero

Use a concise opening similar to:

````markdown
# LiveKit Voicebox

Use locally cloned Voicebox voices with LiveKit Agents.

```python
from livekit.plugins import voicebox

tts = voicebox.TTS(profile="My Voice")
```

Voice profiles and TTS inference stay on your Voicebox machine.
Use any LLM supported by LiveKit Agents.
````

Then show:

```text
User → LiveKit → STT → LLM → Voicebox → LiveKit → User
```

Do not lead with internal implementation details.

---

# 24. Demo configuration

Support env-driven demo configuration.

Example:

```text
VOICEBOX_URL=http://127.0.0.1:17493
VOICEBOX_PROFILE="Authorized Voice"
VOICEBOX_ENGINE=qwen
VOICEBOX_MODEL_SIZE=0.6B
```

LiveKit credentials remain standard LiveKit environment variables.

Do not define duplicate LiveKit credential handling.

---

# 25. Azure example

The Azure example should prove that the TTS layer is LLM-independent.

Architecture:

```text
mic
 ↓
LiveKit
 ↓
STT
 ↓
Azure OpenAI
 ↓
text
 ↓
Voicebox TTS
 ↓
LiveKit audio
```

The Azure example is an example only.

No Azure types should leak into:

```text
client.py
tts.py
models.py
errors.py
```

---

# 26. GitHub Copilot phase

Do this **after v0.1 TTS is stable**.

Do not block the first release on Copilot.

Current GitHub Copilot SDK supports Python sessions and streaming session events.

At implementation time, verify the current official SDK interface.

Known current shape:

```python
from copilot import CopilotClient

client = CopilotClient()
await client.start()

session = await client.create_session(
    model="auto",
    streaming=True,
    ...
)
```

The current SDK emits incremental assistant events when streaming is enabled.

## v0.2 objective

Allow:

```text
LiveKit STT
   ↓
Copilot SDK model/session
   ↓
streamed response text
   ↓
LiveKit's TTS pipeline
   ↓
Voicebox
```

### Scope choice

First implement:

```text
examples/copilot_agent.py
```

with the smallest adapter needed for the demo.

Only promote it into a reusable package/API after the demo works and the LiveKit `llm.LLM` abstraction has been implemented correctly.

Do not tightly couple `voicebox.TTS` to Copilot.

### Security

Do not hard-code tokens.

For local personal development, use GitHub's documented signed-in Copilot CLI/SDK auth path.

For redistributed/server applications, follow GitHub's current documented application authentication model.

---

# 27. Copilot adapter design gate

Before coding the reusable adapter, inspect current:

```text
livekit-agents/livekit/agents/llm/
```

and at least two current streaming LLM plugins.

Write a short design note documenting:

```text
LiveKit LLM base class
LLMStream base class
ChatContext mapping
tool-call requirements
stream chunk representation
cancellation semantics
usage metrics
```

Then decide whether the first public Copilot integration supports:

```text
A. text chat only
or
B. full LiveKit tool calling
```

Default to **A** for the initial demo unless B is nearly free.

Do not fake tool-call compatibility.

---

# 28. Product demo

After v0.1 package correctness, build a small polished demo.

Recommended starter:

```text
LiveKit's current React agent starter
```

Do not build frontend media primitives manually.

UI:

```text
┌─────────────────────────────────────┐
│ Voicebox × LiveKit                  │
│                                     │
│ LiveKit     ● Connected             │
│ Voicebox    ● Connected             │
│                                     │
│ Voice       [ Authorized Voice   ▼] │
│ Engine      [ Qwen3 0.6B         ▼] │
│ Brain       [ Azure / Copilot    ▼] │
│                                     │
│                ◉                    │
│              Talk                   │
│                                     │
│ TTS first audio       824 ms        │
│ Response latency      1.42 s        │
└─────────────────────────────────────┘
```

All metrics must be real.

Do not mock latency values.

---

# 29. Demo API boundary

The browser must **not** call Voicebox directly.

Correct:

```text
Browser
 ↓ WebRTC
LiveKit
 ↓
Agent worker
 ↓ localhost
Voicebox
```

Incorrect:

```text
Browser → public Voicebox port
```

The local Agent worker is the trust boundary.

---

# 30. True streaming upstream phase

Only begin this after:

```text
v0.1 works
real benchmarks exist
adapter is useful
```

Create a separate branch/fork of Voicebox.

Do not mix this work into the adapter package's first PR.

## Goal

Change Voicebox from:

```text
model yields chunks
 ↓
Voicebox buffers
 ↓
Voicebox returns completed WAV
```

to:

```text
model yields audio
 ↓
Voicebox emits audio immediately
 ↓
LiveKit emits audio immediately
```

## Proposed Voicebox abstraction

Do not break existing `TTSBackend.generate()` implementations.

Add an optional streaming protocol/capability.

Conceptual API:

```python
class StreamingTTSBackend(Protocol):
    async def stream_generate(
        self,
        text: str,
        voice_prompt: dict,
        language: str = "en",
        seed: int | None = None,
        instruct: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        ...
```

Where:

```python
@dataclass
class AudioChunk:
    samples: np.ndarray
    sample_rate: int
```

Do not force every engine to implement this.

Capability detection can initially be:

```python
hasattr(backend, "stream_generate")
```

or a clearly documented registry capability, depending on upstream maintainer preference.

---

# 31. Qwen MLX streaming implementation

The current MLX Qwen backend internally loops in default non-streaming mode:

```python
for result in self.model.generate(...):
    ...
```

The true-streaming implementation should move that generator across an async-safe boundary.

First enable and establish genuine within-utterance generation with the pinned
dependency's streaming flag and chunk-interval controls. Merely forwarding its
default completed results does not satisfy this phase.

Because model generation is synchronous/blocking, do **not** run it directly on the event loop.

One valid design:

```text
inference thread
    │
    ├─ result.audio
    ↓
thread-safe / loop-safe async queue
    ↓
async generator
    ↓
HTTP StreamingResponse
```

Required bridge contract:

```text
bounded queue and bounded pending cross-thread submissions
producer waits for capacity and checks a cooperative stop signal
consumer disconnect requests stop and stops forwarding audio
terminal success/error is delivered without deadlocking a full queue
backend-use lease remains held until the actual worker exits
```

Do not use an unbounded `asyncio.Queue` or enqueue unlimited
`call_soon_threadsafe(queue.put_nowait, ...)` callbacks. Merely adding `maxsize`
to that pattern causes queue-full errors rather than producer backpressure.
Cancelling `asyncio.to_thread()` does not terminate its running worker.

---

# 32. True streaming wire format

For the realtime endpoint, prefer raw PCM over WAV.

Proposed route:

```text
POST /generate/realtime
```

Response:

```text
Content-Type: audio/pcm
X-Audio-Format: s16le
X-Audio-Sample-Rate: 24000
X-Audio-Channels: 1
```

This proposed application contract means signed 16-bit little-endian PCM, mono,
24 kHz. `audio/pcm` alone is not sufficient: validate all format metadata and
convert explicitly at the LiveKit boundary.

Do not treat `audio/L16` as an interchangeable MIME label: it specifies
big-endian samples. If maintainers select L16, specify `rate`/`channels` and
perform the corresponding endian conversion. See
[RFC 2586 section 3](https://www.rfc-editor.org/rfc/rfc2586.html#section-3).

HTTP chunk boundaries need not align with samples or playback frames. Carry
incomplete samples across reads and reject an incomplete sample at end-of-stream.
Do not manually require HTTP/1.1 chunked transfer on HTTP/2 connections.

Avoid requiring the client to wait for a final WAV header or seekable container.

Each yielded audio chunk:

```text
apply fixed-gain or stateful streaming-safe processing; clip as needed
convert to PCM16
yield bytes
```

Do not apply global post-processing effects that require the entire waveform in true realtime mode.
Do not independently peak-normalize each chunk; that can produce audible
loudness pumping. Preserve resampler/filter state across chunks and flush tails.

Document effects incompatibilities.

Define failure after headers/audio have been sent. HTTP 200 alone is not proof
of complete synthesis. Require a supported completion/error signal or treat an
aborted transport as failure; do not silently return a successful shortened
utterance or retry speech that has already played. Resolve this transport
decision with upstream before freezing the endpoint.

---

# 33. True streaming cancellation

This is the hard part.

Acceptance for Voicebox true streaming is not merely "bytes arrive early."

It must satisfy:

```text
client disconnect
   ↓
server detects cancellation
   ↓
stream stops accepting generated chunks
   ↓
backend is asked to cancel/stop where technically supported
   ↓
resources are released
```

If a backend cannot actually stop autoregressive generation, document that
explicitly, discard subsequent chunks, bound memory, and hold server-wide
inference ownership until the worker actually exits. Request-task completion
must not admit the next inference while the old worker is still running.

Do not market interruption latency as solved until measured.

---

# 34. True streaming LiveKit upgrade

Once Voicebox exposes real incremental generation:

```python
tts.TTSCapabilities(streaming=True)
```

may become appropriate only if the LiveKit implementation supports incremental **text input** through a persistent synthesis stream.

Distinguish:

### Streaming audio from one submitted text segment

```text
synthesize("sentence")
 → PCM chunk
 → PCM chunk
 → ...
```

from:

### Full duplex/incremental TTS input

```text
push_text("hello")
push_text(" there")
flush()
 → audio while input is still arriving
```

LiveKit's `streaming=True` capability usually implies the latter provider behavior.

Therefore do **not** flip the capability flag merely because `/generate/realtime` streams output audio.

If Voicebox only streams output for a completed input string, retain the correct LiveKit abstraction and still benefit from lower `ChunkedStream` TTFB.

This distinction must be validated against the current LiveKit API when implementing the upstream streaming phase.

---

# 35. Benchmark methodology

Before optimization, establish baseline.

Test phrases:

```text
SHORT:
"Hello. How can I help?"

MEDIUM:
"That's a good question. Let me explain what is happening and what I would do next."

LONG:
~400 characters across 4-5 sentences
```

For each configuration run:

```text
1 warm-up
10 measured iterations
```

This is exploratory benchmarking, not a robust tail-latency release sample.
For release decisions use at least 30 measured conversational turns per
reference configuration, publish the sample count, and repeat interruption and
recovery cases. Record backend implementation, dependency/model revisions,
power settings, selected profile effects, and concurrent load.

Record:

```text
median
p90
min/max
failures
```

Metrics:

```text
HTTP request start -> first received response byte
HTTP request start -> complete body
decode duration
first LiveKit audio emission
generated audio duration
RTF = synthesis wall time / audio duration
```

If backend inference timestamps are unavailable, label client elapsed time
divided by audio duration as end-to-end request RTF, not pure model RTF.
Record queue wait and profile resolution separately.

Add room-level end-of-user-speech to first audible agent response,
inter-sentence silence, interruption-to-silence, and next-turn recovery.
Use monotonic clocks within each process; do not subtract unsynchronized
browser/worker timestamps. First emitted audio is not first audible playback.

For v0.1, be explicit that HTTP TTFB may equal nearly the entire synthesis duration because Voicebox buffers generation.

For true streaming, measure:

```text
model start -> first model audio
request start -> first HTTP audio
request start -> first LiveKit frame
```

---

# 36. Performance gates

Do not equate valid audio with usable conversation, or select thresholds after
seeing favorable results. Record one reference engine/backend/hardware setup
and approve numeric thresholds before the spike's pass/fail evaluation.

Proposed initial targets below are reviewer recommendations, not measured
results or already approved product commitments:

| Warm conversational metric | Proposed gate |
| --- | --- |
| End of user speech to first audible response | p90 <= 2.5 seconds |
| Gap between consecutive response sentences | p90 <= 300 milliseconds |
| Detected interruption to audible silence | p90 <= 200 milliseconds |
| End of next user turn to first audible response after interruption | p90 <= 3 seconds |
| End-to-end request RTF for the reference sentence set | p90 < 1.0 |

Define turn-end/interrupt detection and audio measurement points, and report
detection latency separately. Short utterances alone must not mask a sustained
throughput failure. A gate failure can justify a narrower product claim or
upstream work, but cannot be relabeled "feels usable".

Mandatory correctness gates remain no corrupt output, no stale playback, no
overlapping owned inference after cancellation, and accurate metrics.
Publish outcomes including failures and cold-load behavior.

True-streaming target:

```text
warm first LiveKit audio < 750 ms on explicitly documented reference hardware
```

This is a target, not a pre-existing capability claim.

---

# 37. Failure UX

Make these errors useful.

## Voicebox unavailable

```text
Could not connect to Voicebox at http://127.0.0.1:17493.
Start Voicebox or set VOICEBOX_URL to a reachable Voicebox server.
```

## Profile missing

```text
Voicebox profile 'Authorized Voice' was not found.
Available profiles: Morgan, Narrator, Test Voice.
```

Cap profile list to a reasonable count in the error.

## Ambiguous

```text
Voicebox profile name 'test' matches multiple profiles.
Use the profile ID instead.
```

## Engine mismatch

Surface Voicebox's engine/profile validation message.

## Model missing

Surface Voicebox's returned readiness/download message if available.

Do not tell the plugin to download Voicebox models itself in v0.1.

---

# 38. Security

README must state:

```text
Voicebox is local-only by default and relies on localhost trust.
Keep it bound to loopback unless you intentionally configure and secure remote access.
```

Plugin defaults:

```text
127.0.0.1
not 0.0.0.0
```

Never:

```text
open firewall ports
create tunnels
disable CORS/security automatically
```

Voice reference recordings remain inside Voicebox.

The plugin sends synthesis text, a profile identifier, and supported generation
options to the configured Voicebox server. It does not send reference samples.

Local inference is not end-to-end offline operation. Synthesized cloned speech
is transmitted through the configured LiveKit deployment; LiveKit Cloud sees
that media path. Hosted STT/LLM providers have their own audio/text data flows.
Document these boundaries separately from reference-recording locality.

---

# 39. Responsible voice cloning

Include a short, non-preachy section:

> Only clone or synthesize a person's voice when you have authorization to do so. Do not use cloned speech to impersonate people, bypass voice authentication, or misrepresent generated audio as an authentic recording.

Do not bundle celebrity/public-figure demo voices.

---

# 40. Logging

INFO:

```text
Voicebox connected
profile resolved (ID may be shortened/hashed)
engine/model
synthesis duration
audio duration
errors without text body
```

DEBUG may include:

```text
request IDs
HTTP statuses
sample rates
frame counts
```

Do not log:

```text
full input text
reference transcript
audio bytes
voice sample paths
```

by default.

---

# 41. Version compatibility

Expose:

```python
__version__
```

Document tested versions:

```text
livekit-agents: tested range
Voicebox: tested release/commit
Python: tested versions
inference backend dependencies: tested versions
model artifacts: repository IDs and tested revisions
```

Do not claim compatibility with every future Voicebox version.

At package startup, do not hard-fail solely because Voicebox lacks a specific version header unless an actual API incompatibility is known.

### License and redistribution boundary

Check adapter code, copied upstream code, inference dependencies, and model
weights separately. Voicebox's application license does not license all weights.
The reviewed TADA artifacts use the Llama 3.2 Community License, with terms beyond
MIT/Apache-style licensing; review before advertising or redistributing support.
Record artifact-specific licenses in the compatibility matrix, and do not bundle
weights or imply unrestricted commercial use from the package license.

[TADA 1B license](https://huggingface.co/HumeAI/tada-1b/blob/39a987c813d70bc70587757c3ed82c6e9853077d/LICENSE#L33-L67)

---

# 42. API compatibility tests

Store representative JSON fixtures based on public Voicebox schemas, not personally generated data.

Test tolerance for additional unknown fields.

Fail when required fields disappear.

Do not depend on object key order.

---

# 43. What not to overengineer

Do not add in v0.1:

```text
Redis
Postgres
Celery
Kafka
custom RPC protocol
WebSockets
Docker orchestration platform
Kubernetes
GPU scheduler
database
user accounts
auth server
voice asset storage
observability backend
```

The package is a local HTTP adapter.

---

# 44. Local developer setup

README development flow should be approximately:

```bash
git clone <repo>
cd livekit-plugins-voicebox

python -m venv .venv
source .venv/bin/activate

pip install -e "./livekit-plugins-voicebox[dev]"
pytest
```

Then:

```text
1. Install/start Voicebox.
2. Create a voice profile you are authorized to use.
3. Export VOICEBOX_PROFILE.
4. Configure normal LiveKit credentials.
5. Run examples/minimal_agent.py.
```

Use the project's chosen package manager consistently if adopting `uv`.

---

# 45. Manual v0.1 test script

The coding agent MUST perform this before declaring v0.1 ready.

### Test A — basic synthesis

```text
Voicebox running
profile loaded
one short phrase
audio heard
```

### Test B — multi-sentence agent response

Prompt agent to answer with three short sentences.

Verify:

```text
sentence-level output
measured inter-sentence gaps meet the approved reference gate
```

### Test C — interruption

While agent is speaking:

```text
speak over it
```

Verify:

```text
playback stops
new response begins correctly
old response never resumes
no second owned POST while abandoned inference is still active
post-interruption recovery meets its approved reference gate
```

### Test D — voice switch

Switch profile.

Verify next turn uses visibly/audibly different configured profile.

### Test E — Voicebox restart

Stop Voicebox.

Verify actionable error.

Restart Voicebox.

For a proven pre-submission connection failure, verify subsequent synthesis
recovers. For uncertain in-flight work, require confirmed backend shutdown/restart
and recreate the TTS client; health alone must not automatically clear the block.

### Test F — engine switch

Switch between configurations claimed in the release compatibility matrix.
Cross-engine switching is required only when more than one engine is claimed.

Verify profile validation and output.

---

# 46. Demo acceptance

The project should be understandable from a 20-second screen recording:

```text
1. Show Voicebox profile.
2. Start LiveKit demo.
3. Ask one question.
4. Agent replies in that cloned voice.
5. Interrupt it.
6. Ask another question.
7. For the later v0.2 demo only, show Azure/Copilot selection.
```

Do not require viewers to understand the code first.

---

# 47. Release checklist

Before `0.1.0`:

```text
[ ] import namespace follows LiveKit convention
[ ] unit tests green
[ ] lint green
[ ] type checks green
[ ] integration test manually passed
[ ] Azure example manually passed
[ ] cancellation manually tested
[ ] benchmark numbers published
[ ] numeric reference performance gates approved and passed
[ ] exclusive-backend limitation documented
[ ] cancel/drain/uncertain-state recovery covered
[ ] dependency/model revisions and per-artifact licenses recorded
[ ] Voicebox offline error tested
[ ] profile-not-found error tested
[ ] README has architecture diagram
[ ] README clearly says sentence-level vs true streaming
[ ] localhost security warning included
[ ] responsible cloning section included
[ ] no secrets committed
[ ] no personal voice audio committed
[ ] license confirmed
```

---

# 48. Upstream strategy

Do not start by asking LiveKit to merge an unproven plugin.

Order:

```text
independent package
   ↓
working demo
   ↓
tests
   ↓
benchmarks
   ↓
initial external users / feedback
   ↓
LiveKit plugin proposal
```

In parallel, after validation:

```text
Voicebox true-streaming prototype
   ↓
benchmark improvement
   ↓
small upstream Voicebox PR
```

Do not send one cross-repository mega-PR.

---

# 49. Issues to open after v0.1

Create only after baseline works:

```text
#1 True incremental Qwen MLX output
#2 Voicebox server-side cancellation on client disconnect
#3 Output-streaming adapter optimization
#4 Chatterbox Turbo expressive markup mapping
#5 OpenAI-compatible Voicebox endpoint compatibility
#6 Copilot SDK reusable LiveKit LLM adapter
#7 Multi-session GPU scheduling
```

Prioritize by measured impact, not novelty.

---

# 50. Potential expressive speech phase

Voicebox Chatterbox Turbo supports bracketed non-verbal tags.

LiveKit has an expressive/markup abstraction.

Do not implement mapping in v0.1.

Later investigate mapping LiveKit standardized expressive markers into Voicebox-native tags such as:

```text
[laugh]
[sigh]
[gasp]
...
```

Only advertise tags actually supported by the selected Voicebox engine.

Do not send Chatterbox-specific tags to Qwen or engines that will read them literally.

---

# 51. Potential direct OpenAI-compatible path

Voicebox has community demand for OpenAI-compatible TTS endpoints.

If Voicebox later exposes a sufficiently compatible:

```text
/v1/audio/speech
```

developers may be able to use LiveKit's OpenAI-compatible TTS implementation directly.

This does **not** automatically obsolete this package.

Durable Voicebox-specific value:

```text
profile-name resolution
profile discovery
engine selection
model selection
health/readiness
Voicebox-specific error handling
runtime profile switching
capability detection
Voicebox streaming negotiation
safe local defaults
```

Keep `VoiceboxClient` isolated so the wire endpoint can change later without breaking the public `TTS` class.

---

# 52. Stop conditions

The coding agent should stop and report rather than papering over these cases.

## Stop condition A

Current LiveKit TTS API has changed enough that the design above does not compile.

Action:

```text
inspect current official provider implementations
adapt to current public API
document deviation
```

Do not pin an ancient LiveKit version just to preserve this document.

## Stop condition B

Current Voicebox removed or materially changed `/generate/stream`.

Action:

```text
inspect current API/routes
identify replacement
update client contract
```

## Stop condition C

Voicebox output cannot be decoded deterministically.

Action:

```text
capture sanitized response headers + a synthetic/nonpersonal minimal fixture
identify actual audio subtype
fix decoder
```

Do not parse audio by guessing byte offsets.

Do not save a user's generated cloned speech as a diagnostic fixture without
explicit opt-in, even if it would make debugging convenient.

## Stop condition D

A LiveKit interruption causes stale audio to leak.

Action:

```text
do not release
fix lifecycle/cancellation semantics first
```

## Stop condition E

The adapter requires publicly exposing Voicebox.

Action:

```text
reject architecture
keep agent worker colocated/reachable through secure private networking
```

---

# 53. Coding quality expectations

Use:

```text
small typed dataclasses
explicit error boundaries
dependency injection for HTTP session
no global mutable application state
no unnecessary inheritance beyond LiveKit contracts
async cancellation-safe code
structured tests
```

Avoid:

```text
catch Exception everywhere
silent fallbacks to another profile
silent fallbacks to another engine
sync HTTP in async path
blocking audio decode on huge payloads without consideration
private LiveKit APIs when public equivalents exist
```

For bounded short-sentence audio, in-memory decode is acceptable. Enforce input
and decoded-output limits before large allocations where the decoder permits.
Move material blocking decode/resampling off the event loop when needed to meet
cancellation responsiveness. Do not defer payload limits as an optimization.

---

# 54. Definition of done per layer

## HTTP client done

```text
profile discovery works
generation works
errors typed
timeouts work
session closes correctly
```

## Audio layer done

```text
valid WAV -> correct mono PCM
sample rate stable
bad audio rejected
```

## LiveKit layer done

```text
AgentSession accepts TTS
audio reaches room
metrics emitted
cancellation correct
```

## Developer experience done

```text
one obvious install command
one obvious constructor
errors tell developer what to fix
```

## OSS release done

```text
tests
CI
examples
benchmarks
docs
license
security/responsible-use notes
```

---

# 55. Agent execution loop

For each commit:

```text
1. Inspect relevant upstream implementation.
2. Implement only current commit scope.
3. Add/update tests.
4. Run formatter.
5. Run linter.
6. Run type checker.
7. Run unit tests.
8. Review diff for accidental scope expansion.
9. Commit.
10. Continue only if green.
```

Do not batch ten commits worth of changes and debug everything at the end.

---

# 56. First coding task

Start here.

### Goal

Create Commit 1 and Commit 2 only.

### Required actions

```text
1. Initialize repository.
2. Confirm current LiveKit external plugin namespace convention.
3. Create package/import layout.
4. Configure pyproject.
5. Add CI/lint/test tooling.
6. Implement VoiceboxClient.
7. Implement typed models/errors.
8. Add mocked HTTP tests.
9. Make all tests green.
```

### Do not yet

```text
implement TTS
build frontend
implement Azure
implement Copilot
modify Voicebox
optimize streaming
```

### Output after task

Report:

```text
files created
public API
test results
lint/type results
any upstream API differences found
next commit to implement
```

---

# 57. Second coding task

Only after Task 1 is green:

```text
1. Inspect current LiveKit tts.TTS and at least two provider implementations.
2. Implement TTS + ChunkedStream.
3. Implement WAV decode/resample/emission.
4. Implement profile snapshots/cache isolation and cancellation-safe job ownership.
5. Add LiveKit-facing tests, including drain and uncertain-state behavior.
6. Verify non-streaming capability and correct metrics integration.
```

Then stop and manually test basic audio before adding Azure/Copilot.

---

# 58. Third coding task

After basic audio works:

```text
1. Exercise runtime profile switching with a real backend.
2. Exercise cancellation, drain, and recovery with the real backend.
3. Build minimal real LiveKit agent example.
4. Perform manual interruption test.
5. Run 50 short sequential synthesis requests.
6. Check process/client resource stability.
```

Do not release if turn cancellation is unreliable.

---

# 59. Fourth coding task

After core package is stable:

```text
1. Add Azure OpenAI example using official LiveKit integration.
2. Add benchmark harness.
3. Record actual hardware/results.
4. Finish README.
5. Tag 0.1.0 candidate.
```

---

# 60. Fifth coding task — optional Copilot v0.2

After `0.1.x`:

```text
1. Verify current GitHub Copilot SDK Python API.
2. Verify local signed-in authentication path.
3. Inspect current LiveKit LLM streaming abstraction.
4. Build text-only Copilot demo adapter.
5. Stream Copilot assistant deltas into LiveKit.
6. Route output to existing Voicebox TTS unchanged.
7. Add clean shutdown of Copilot client/session.
8. Measure LLM TTFT separately from TTS TTFB.
```

Do not grant Copilot tool execution permissions merely to answer conversational prompts. Use the narrowest safe permission behavior supported by the SDK.

---

# 61. Success state

When the project is complete, this should work:

```python
from livekit.agents import Agent, AgentSession
from livekit.plugins import voicebox

tts = voicebox.TTS(
    profile="My Voice",
    engine="qwen",
    model_size="0.6B",
)

session = AgentSession(
    stt=...,
    llm=...,
    tts=tts,
)

await session.start(
    room=ctx.room,
    agent=Agent(
        instructions="Be concise and conversational."
    ),
)
```

And the user experience should be:

```text
talk
 ↓
agent understands
 ↓
chosen LLM responds
 ↓
Voicebox generates locally
 ↓
LiveKit plays the cloned voice
 ↓
user can interrupt and continue
```

No proprietary TTS provider is required.

---

# 62. Final product statement

Do not describe the repository as an AI clone product.

The repository is:

> **A local-first Voicebox TTS provider for LiveKit Agents.**

The flagship demo is:

> **Clone a voice locally in Voicebox, choose any LiveKit-compatible LLM, and have a realtime conversation with an agent speaking in that voice.**

The engineering story is:

```text
useful integration
+
clean provider abstraction
+
measured realtime behavior
+
real cancellation semantics
+
path to upstream true streaming
```

That is the project.
