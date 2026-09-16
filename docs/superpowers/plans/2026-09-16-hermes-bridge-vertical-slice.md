# Hermes Bridge Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Voicebox Studio run a real Hermes session with local Nemotron STT, local Qwen TTS, streamed replies, interruption, tools, and click-based approvals.

**Architecture:** Add a small HTTP/SSE client for Hermes `/v1/runs`, then wrap it in LiveKit’s `llm.LLM` contract. Keep LiveKit responsible for audio and turn-taking. Keep Hermes responsible for reasoning, tools, memory, approvals, and durable session state.

**Tech Stack:** Python 3.11+, aiohttp, LiveKit Agents, React 19, Vitest, pytest, mypy, ruff, Hermes HTTP Runs API.

**Spec:** `docs/superpowers/specs/2026-09-16-hermes-livekit-voice-companion-design.md`

## Global Constraints

- Do not modify Hermes core for this vertical slice.
- Default speech path remains local Nemotron STT plus local Qwen3-TTS 0.6B.
- Browser never receives Hermes or LiveKit server credentials.
- One room owns at most one Hermes run at a time.
- Every run uses an explicit Hermes `session_id` and unique `Idempotency-Key`.
- Speech interruption stops playback immediately, requests Hermes stop, and never claims an external action was undone.
- Voice alone cannot approve an action.
- No silent provider fallback.
- Keep Studio loopback-only and single-user.
- Expressive cloud TTS is a separate follow-up plan after this slice passes its gates.

## File map

- Create `examples/hermes_api.py` — typed, bounded Hermes Runs HTTP/SSE client.
- Create `examples/hermes_llm.py` — LiveKit `llm.LLM` adapter backed by one durable Hermes session.
- Create `tests/test_hermes_api.py` — HTTP, SSE, auth, idempotency, stop, approval, and disconnect contracts.
- Create `tests/test_hermes_llm.py` — LiveKit stream, stale-event, cancellation, and approval contracts.
- Modify `examples/minimal_agent.py` — select Hermes independently from STT and create the adapter.
- Modify `examples/studio_worker.py` — bind room ownership, targeted approval messages, approval RPC, and interruption state.
- Modify `examples/studio_library.py` — migrate and validate Hermes non-secret settings.
- Modify `examples/studio.py` — expose Hermes readiness without exposing credentials.
- Modify `tests/test_provider_choices.py`, `tests/test_studio_worker.py`, `tests/test_studio_library.py`, `tests/test_studio.py` — server-side integration coverage.
- Modify `web/src/api.ts` — Hermes settings and approval wire types.
- Modify `web/src/App.tsx` — receive pending approvals and distinguish speech stop from action state.
- Modify `web/src/StudioSettings.tsx` — Hermes provider/profile controls and route disclosure.
- Modify `web/src/App.test.tsx`, `web/src/StudioSettings.test.tsx` — browser approval and settings coverage.
- Create `tests/integration/test_hermes_studio_live.py` — opt-in real Hermes/LiveKit vertical slice.
- Modify `docs/architecture.md`, `docs/agent-quickstart.md`, `docs/oss-readiness.md`, `README.md`, `.env.example` — accurate setup, trust boundaries, and status.

---

### Task 1: Bounded Hermes Runs client

**Files:**
- Create: `examples/hermes_api.py`
- Create: `tests/test_hermes_api.py`

**Interfaces:**
- Consumes: Hermes `/v1/capabilities`, `/v1/runs`, `/events`, `/approval`, and `/stop`.
- Produces:
  - `HermesConfig(base_url: str, api_key: str, profile: str | None, session_id: str)`
  - `RunHandle(run_id: str)`
  - `RunEvent(type: str, payload: dict[str, object])`
  - `HermesRunsClient.preflight() -> Awaitable[None]`
  - `HermesRunsClient.start(text: str, *, idempotency_key: str) -> Awaitable[RunHandle]`
  - `HermesRunsClient.events(run_id: str) -> AsyncIterator[RunEvent]`
  - `HermesRunsClient.status(run_id: str) -> Awaitable[dict[str, object]]`
  - `HermesRunsClient.stop(run_id: str) -> Awaitable[dict[str, object]]`
  - `HermesRunsClient.approve(run_id: str, request_id: str, choice: str) -> Awaitable[None]`
  - `HermesRunsClient.aclose() -> Awaitable[None]`

- [ ] **Step 1: Write failing URL and validation tests**

```python
from examples.hermes_api import HermesConfig


def test_profile_routes_are_encoded_and_loopback_is_allowed():
    config = HermesConfig(
        base_url="http://127.0.0.1:8642",
        api_key="test-key",
        profile="default",
        session_id="voice-room-1",
    )
    assert config.route("/v1/runs") == "http://127.0.0.1:8642/p/default/v1/runs"


def test_http_remote_origin_is_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        HermesConfig("http://example.com", "key", None, "session")
```

- [ ] **Step 2: Run the tests and confirm missing-module failure**

Run: `uv run pytest tests/test_hermes_api.py -v`

Expected: collection fails because `examples.hermes_api` does not exist.

- [ ] **Step 3: Implement immutable configuration and bounded wire types**

Use frozen dataclasses. Normalize only a trailing slash. Permit plain HTTP only for `127.0.0.1`, `localhost`, and `::1`. Percent-encode the profile with `urllib.parse.quote(profile, safe="")`. Reject blank API keys, session IDs over 255 characters, and non-HTTP(S) URLs.

```python
@dataclass(frozen=True)
class HermesConfig:
    base_url: str
    api_key: str
    profile: str | None
    session_id: str
    request_timeout: float = 120.0
    stop_timeout: float = 10.0

    def route(self, path: str) -> str:
        prefix = f"/p/{quote(self.profile, safe='')}" if self.profile else ""
        return f"{self.base_url.rstrip('/')}{prefix}{path}"
```

- [ ] **Step 4: Write failing preflight, start, and SSE tests with an aiohttp test server**

Cover these exact behaviors:

```python
async def test_start_sends_explicit_session_and_idempotency(aiohttp_server):
    # Server records headers/body and returns 202.
    handle = await client.start("hello", idempotency_key="room:turn:1")
    assert handle.run_id == "run_1"
    assert seen["body"] == {"input": "hello", "session_id": "voice-room-1"}
    assert seen["headers"]["Idempotency-Key"] == "room:turn:1"


async def test_events_parse_only_data_frames_and_bound_payload(aiohttp_server):
    events = [event async for event in client.events("run_1")]
    assert events == [RunEvent("message.delta", {"delta": "Hi"}),
                      RunEvent("run.completed", {"status": "completed"})]
```

Also test: bearer auth, non-202 start failure, malformed JSON, an event line over 1 MiB, terminal status polling after SSE EOF, exact approval request ID, allowed choices (`once`, `session`, `always`, `deny`), and idempotent close.

- [ ] **Step 5: Implement the client**

Use one owned `aiohttp.ClientSession`. Set `Authorization: Bearer …`, `Accept: application/json`, and per-call timeouts. For SSE, accept comment keepalives, parse only `data:` fields, reject malformed/non-object JSON, and cap each frame at 1 MiB. Never log response bodies or the API key.

`preflight()` must require capability flags/endpoints for runs, events, approval, and stop. Report one sanitized `HermesAPIError` on mismatch.

- [ ] **Step 6: Run focused verification**

Run:

```bash
uv run pytest tests/test_hermes_api.py -v
uv run ruff check examples/hermes_api.py tests/test_hermes_api.py
uv run mypy examples/hermes_api.py
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add examples/hermes_api.py tests/test_hermes_api.py pyproject.toml
git commit -m "feat: add bounded Hermes runs client"
```

---

### Task 2: LiveKit `HermesLLM` adapter

**Files:**
- Create: `examples/hermes_llm.py`
- Create: `tests/test_hermes_llm.py`

**Interfaces:**
- Consumes: `HermesRunsClient` from Task 1 and LiveKit `llm.LLM`.
- Produces:
  - `ApprovalRequest(run_id: str, request_id: str, command: str, choices: tuple[str, ...])`
  - `HermesLLM(client: HermesRunsClient, on_approval: Callable[[ApprovalRequest], Awaitable[None]], max_response_chars: int = 32_000)`
  - `HermesLLM.active_run_id: str | None`
  - `HermesLLM.respond_to_approval(request_id: str, choice: str) -> Awaitable[None]`
  - `HermesLLM.stop_active() -> Awaitable[bool]`

- [ ] **Step 1: Write failing stream tests**

```python
async def test_message_deltas_become_livekit_chunks(client):
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "hello")))
    chunks = [chunk async for chunk in stream]
    assert [chunk.delta.content for chunk in chunks if chunk.delta] == ["Hel", "lo"]
    assert client.starts[0]["text"] == "hello"


async def test_cancellation_stops_exact_active_run(client):
    model = HermesLLM(client=client, on_approval=AsyncMock())
    stream = model.chat(chat_ctx=context(("user", "start")))
    task = asyncio.create_task(collect(stream))
    await client.started.wait()
    await stream.aclose()
    await task
    assert client.stopped == ["run_1"]
```

Also test: one active stream at a time, empty user turn rejection, response size bound, stale run event suppression, SSE EOF followed by terminal status poll, terminal failure sanitization, exact approval forwarding, wrong/stale approval rejection, and `aclose()` stopping only the owned run.

- [ ] **Step 2: Run tests and confirm missing-module failure**

Run: `uv run pytest tests/test_hermes_llm.py -v`

Expected: collection fails because `examples.hermes_llm` does not exist.

- [ ] **Step 3: Implement one-turn extraction**

Read the latest non-empty user `llm.ChatMessage` from `chat_ctx`. Do not serialize LiveKit’s assistant history into each request; Hermes owns durable history through `session_id`. Reject LiveKit tools passed into `chat()` so no tool schema can bypass Hermes.

```python
def chat(self, *, chat_ctx, tools=None, conn_options=DEFAULT_API_CONNECT_OPTIONS,
         parallel_tool_calls=NOT_GIVEN, tool_choice=NOT_GIVEN,
         extra_kwargs=NOT_GIVEN) -> llm.LLMStream:
    if tools:
        raise _error("Hermes owns tools; LiveKit tools are not accepted.")
    return _HermesStream(self, chat_ctx=chat_ctx.copy(), conn_options=conn_options)
```

- [ ] **Step 4: Implement streamed run lifecycle**

Inside `_HermesStream._run()`:

1. Acquire the model lock.
2. Increment a generation counter.
3. Start the run with `uuid.uuid4().hex` as idempotency key.
4. Store exact active `run_id` plus generation.
5. Convert only `message.delta.payload["delta"]` strings into `llm.ChatChunk` values.
6. Forward `approval.request` through `on_approval` without speaking it.
7. On SSE EOF before a terminal event, call `status()` once.
8. Require terminal `completed`; map `cancelled` to `asyncio.CancelledError` only when local cancellation requested, otherwise sanitized `APIError`.
9. Clear active state only if both run ID and generation still match.

- [ ] **Step 5: Implement cancellation acknowledgement**

On stream cancellation, call `stop(active_run_id)` once and poll status until terminal or `stop_timeout`. If the timeout expires, set adapter state to uncertain and reject future `chat()` calls. Never claim the tool/action was reversed.

- [ ] **Step 6: Run focused verification**

```bash
uv run pytest tests/test_hermes_llm.py -v
uv run ruff check examples/hermes_llm.py tests/test_hermes_llm.py
uv run mypy examples/hermes_llm.py
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add examples/hermes_llm.py tests/test_hermes_llm.py
git commit -m "feat: stream Hermes runs through LiveKit"
```

---

### Task 3: Hermes settings and readiness

**Files:**
- Modify: `examples/studio_library.py:26-169`
- Modify: `examples/minimal_agent.py:73-210`
- Modify: `examples/studio.py:113-220`
- Modify: `tests/test_studio_library.py`
- Modify: `tests/test_provider_choices.py`
- Modify: `tests/test_studio.py`
- Modify: `web/src/api.ts`
- Modify: `web/src/StudioSettings.tsx:7-121`
- Modify: `web/src/StudioSettings.test.tsx`

**Interfaces:**
- Consumes: `HermesConfig`, `HermesRunsClient`, and `HermesLLM`.
- Produces persisted non-secret settings:
  - `llmProvider: "hermes"`
  - `llmModel: "profile-default"`
  - `reasoningEffort: "none"`
  - `hermesProfile: string`
  - `hermesBaseUrl: string`

- [ ] **Step 1: Write failing settings migration tests**

```python
def test_existing_settings_gain_hermes_defaults(tmp_path):
    library = StudioLibrary(tmp_path)
    private_json(library.settings_path, old_settings())
    settings = library.settings()
    assert settings["hermesProfile"] == "default"
    assert settings["hermesBaseUrl"] == "http://127.0.0.1:8642"


def test_hermes_preset_is_fixed(tmp_path):
    settings = default_settings(llmProvider="hermes")
    settings.update(llmModel="gpt-made-up", reasoningEffort="low")
    with pytest.raises(LibraryError, match="Hermes profile"):
        StudioLibrary(tmp_path)._validate_settings(settings)
```

- [ ] **Step 2: Add schema migration and validation**

Add `"hermes": ("profile-default", "none")` to `PRESETS`. Add the two fields to defaults and migrate them with `setdefault`. Validate profile as `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`. Reuse `HermesConfig` URL validation. Update the error copy to include Hermes.

- [ ] **Step 3: Write failing provider construction tests**

Assert `configured_ai()` creates `HermesLLM` when `VOICEBOX_LLM_PROVIDER=hermes`, while Nemotron remains independently selectable. Assert it reads `HERMES_API_SERVER_KEY`, `HERMES_API_BASE_URL`, `HERMES_PROFILE`, and `HERMES_VOICE_SESSION_ID` from the worker environment and calls `preflight()` before returning.

- [ ] **Step 4: Implement provider selection and broker readiness**

In `minimal_agent.py`, add `hermes` to accepted reasoning providers and construct the adapter. In `check_setup()`, require the API key, validate loopback/HTTPS URL, and run capability preflight without starting a run. In `Studio.status()`, report:

```json
{
  "provider": "hermes",
  "model": "profile-default",
  "effort": "none",
  "local": true,
  "profile": "default"
}
```

Here `local` means local Hermes control-plane location, not local model inference. Rename the browser label to avoid implying local reasoning.

- [ ] **Step 5: Update settings UI**

Add Hermes as the first reasoning option. Show profile and base URL fields only for Hermes. Copy: “Hermes keeps its tools, memory, model, and approval rules. Studio sends transcripts and receives streamed reply text.” Do not add an API-key input.

- [ ] **Step 6: Run server and frontend tests**

```bash
uv run pytest tests/test_studio_library.py tests/test_provider_choices.py tests/test_studio.py -v
npm --prefix web test -- src/StudioSettings.test.tsx
npm --prefix web run build
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add examples/minimal_agent.py examples/studio.py examples/studio_library.py \
  tests/test_provider_choices.py tests/test_studio.py tests/test_studio_library.py \
  web/src/api.ts web/src/StudioSettings.tsx web/src/StudioSettings.test.tsx
git commit -m "feat: configure Hermes as Studio reasoning provider"
```

---

### Task 4: Worker wiring and action-safe interruption

**Files:**
- Modify: `examples/studio.py` in `_create()` worker environment construction
- Modify: `examples/studio_worker.py:40-266`
- Modify: `tests/test_studio_worker.py`
- Modify: `web/src/App.tsx:74-236`
- Modify: `web/src/App.test.tsx`

**Interfaces:**
- Consumes: `HermesLLM.active_run_id`, `stop_active()`.
- Produces owner-checked RPC `voicebox.interrupt` response:
  - `stoppedPlayback: bool`
  - `hermesStopRequested: bool`
  - `actionUndone: false`

- [ ] **Step 1: Write failing worker tests**

```python
async def test_owner_interrupt_stops_playback_and_active_hermes_run(worker):
    result = json.loads(await handlers["rpc"](SimpleNamespace(caller_identity="user")))
    session.interrupt.assert_awaited_once_with(force=True)
    hermes.stop_active.assert_awaited_once()
    assert result == {
        "stoppedPlayback": True,
        "hermesStopRequested": True,
        "actionUndone": False,
        "backendState": "ready",
    }
```

Also assert wrong owner returns 1403, repeated interrupt is idempotent, local Qwen drain still precedes room disconnect, and late deltas from the stopped run are not spoken.

- [ ] **Step 2: Pass scoped Hermes values to the worker**

Broker supplies only the selected profile, base URL, generated session ID, and server-side API key. Generate session ID as `voice:<room-id>`. Never include these values in the browser session response or logs.

- [ ] **Step 3: Wire `HermesLLM` into `AgentSession`**

Keep `NemotronSTT`, `FastQwenTTS`, Silero VAD, explicit Nemotron commit, and `preemptive_generation.enabled=False`. Replace static tool-denying instructions with voice-style instructions only:

```python
Agent(instructions=(
    "Reply for speech: concise plain text unless detail is needed. "
    "Hermes owns tools, memory, and approvals. Never claim an action was undone "
    "because playback stopped."
))
```

Do not duplicate Hermes profile/SOUL instructions in LiveKit.

- [ ] **Step 4: Make interruption explicit in UI**

Change the acknowledgement to “Speech stopped. Any completed Hermes action remains completed.” Keep microphone muted until the RPC returns or times out. Do not use “cancelled” for external actions.

- [ ] **Step 5: Run focused tests**

```bash
uv run pytest tests/test_studio_worker.py tests/test_provider_choices.py -v
npm --prefix web test -- src/App.test.tsx
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add examples/studio.py examples/studio_worker.py tests/test_studio_worker.py \
  web/src/App.tsx web/src/App.test.tsx
git commit -m "feat: preserve Hermes action state during voice interruption"
```

---

### Task 5: Click-based Hermes approvals over LiveKit

**Files:**
- Modify: `examples/hermes_llm.py`
- Modify: `examples/studio_worker.py`
- Modify: `tests/test_hermes_llm.py`
- Modify: `tests/test_studio_worker.py`
- Modify: `web/src/api.ts`
- Create: `web/src/HermesApproval.tsx`
- Create: `web/src/HermesApproval.test.tsx`
- Modify: `web/src/App.tsx`

**Interfaces:**
- Worker to browser targeted data topic: `hermes.approval.request`.
- Browser to worker owner-checked RPC: `hermes.approval.respond`.
- Payload:

```ts
interface HermesApprovalRequest {
  runId: string;
  requestId: string;
  command: string;
  choices: Array<'once' | 'session' | 'always' | 'deny'>;
}
```

- [ ] **Step 1: Write failing approval state tests**

Test that only the room owner receives the request, command text is bounded to 500 characters, unknown fields are rejected, only advertised choices render, stale request IDs fail, and a terminal run clears the card.

- [ ] **Step 2: Publish targeted approval requests**

In `studio_worker.py`, pass an async `on_approval` callback to `HermesLLM`. Publish reliable data only to `owner`, using topic `hermes.approval.request`. Reject payloads over 4 KiB before publication.

- [ ] **Step 3: Register approval RPC**

Register after room connection, beside `voicebox.interrupt`. Require caller identity to equal `owner`. Parse bounded JSON. Require exact `runId`, `requestId`, and choice. Call `HermesLLM.respond_to_approval()` and return `{"accepted": true}`. Never support secret or free-text values.

- [ ] **Step 4: Add accessible browser card**

`HermesApproval.tsx` renders the redacted command and one button per advertised choice. Default focus goes to Deny. Disable all buttons after one click until acknowledgement. Announce success/failure through `role="status"`/`role="alert"`.

- [ ] **Step 5: Run approval tests**

```bash
uv run pytest tests/test_hermes_api.py tests/test_hermes_llm.py tests/test_studio_worker.py -v
npm --prefix web test -- src/HermesApproval.test.tsx src/App.test.tsx
npm --prefix web run build
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```bash
git add examples/hermes_llm.py examples/studio_worker.py \
  tests/test_hermes_llm.py tests/test_studio_worker.py \
  web/src/api.ts web/src/HermesApproval.tsx web/src/HermesApproval.test.tsx \
  web/src/App.tsx web/src/App.test.tsx
git commit -m "feat: route Hermes approvals through Studio"
```

---

### Task 6: Real vertical-slice verification and documentation

**Files:**
- Create: `tests/integration/test_hermes_studio_live.py`
- Modify: `docs/architecture.md`
- Modify: `docs/agent-quickstart.md`
- Modify: `docs/oss-readiness.md`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: running Hermes API server, LiveKit project, local Nemotron, local Qwen voice.
- Produces: opt-in evidence for one continuous Hermes voice session.

- [ ] **Step 1: Add an opt-in live test**

Mark it `integration`. Skip unless all explicit test credentials and local model paths exist. The test must:

1. Start one Studio room with `llmProvider=hermes`.
2. Send a deterministic text turn and receive streamed speech text.
3. Run a harmless Hermes tool such as reading a fixture file.
4. Trigger an approval-required fixture command and deny it.
5. Start a long reply, interrupt it, and assert no retired deltas are emitted.
6. Send a second turn on the same Hermes session and verify continuity.
7. End the room and confirm worker, Hermes run, Qwen, and Nemotron ownership drain.

Never run destructive tools or use a real secret in the test.

- [ ] **Step 2: Add latency and truth assertions**

Record:

- end-of-utterance delay;
- Nemotron final transcription delay;
- Hermes first text delta;
- Qwen first audio frame;
- barge-in to silent playback;
- Hermes terminal status after stop.

Assert only the design gates: Qwen warm P95 ≤350 ms after at least 20 measured turns, stop-to-silence ≤200 ms, and no stale speech. Keep environment-sensitive measurements in the opt-in report, not normal unit CI.

- [ ] **Step 3: Update docs with verified boundaries**

Document:

- Hermes setup and required `/v1/runs` capabilities;
- local speech versus LiveKit transport versus Hermes text routing;
- click-only approval behavior;
- “speech stopped” versus “action undone” distinction;
- current Apple Silicon and English-only limits;
- Expressive Mode as planned, not shipped;
- no bundled model weights or recordings.

Replace the stale Azure-first architecture diagram with Hermes as the recommended reasoning path while preserving Azure/OpenAI/Copilot/Codex as legacy alternatives until a later deprecation decision.

- [ ] **Step 4: Run full deterministic verification**

```bash
uv run pytest -m "not integration"
uv run ruff check .
uv run mypy
npm --prefix web test
npm --prefix web run build
npm --prefix web audit --audit-level=high
```

Expected baseline: at least the previously audited 477 Python tests and 167 frontend tests pass, plus all new tests. Build, ruff, mypy, and audit exit 0.

- [ ] **Step 5: Run explicitly authorized live smoke test**

```bash
uv run pytest tests/integration/test_hermes_studio_live.py -m integration -v -s
```

Expected: all seven lifecycle stages pass. Save the sanitized timing report under the existing evaluation output location. If approval, stop, or action-truth checks fail, mark Hermes mode experimental and do not publish it as ready.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_hermes_studio_live.py docs README.md .env.example
git commit -m "docs: verify Hermes voice companion vertical slice"
```

---

## Follow-up plans after this slice passes

1. **Cloud expressive TTS A/B:** Add a TTS selector for Cartesia Sonic and Fish Audio `s2.1-pro`, explicit cloud consent, LiveKit Expressive Mode, and latency/cost comparisons. Do not adapt Qwen to the LiveKit expressive flag.
2. **Package extraction:** Move production modules from `examples/` into `voicebox_studio/` without behavior changes, then publish a pinned installable package.
3. **Wake-word/Desktop integration:** Decide whether to adopt Hermes TUI JSON-RPC for wake control and reconnect replay. Preserve the single microphone lease.
4. **Independent release validation:** Clean install on a second Apple Silicon Mac, human listening tests, voice-consent review, license notices, and release manifest.

Do not start these follow-up plans before Task 6 records the bridge, approval, interruption, and action-truth results.