# Studio learning verification

Implemented on `cmongan/studio-learning-experience`, based on published commit
`fcee15c0671f6968eb6e80c28b520255e23605f9`. Existing running installation, private
references, credentials and settings were preserved. Nothing was published or
deployed.

## Delivered

- Lazy, offline setup report and a four-step first-conversation guide.
- Browser-only microphone test with bounded capture, replay and cleanup.
- Conversation-first layout with optional LiveKit room/track explanations.
- Bounded, speech-ID-correlated reply timings; missing/offline data stays unknown.
- Opt-in local audio turn detector; unchanged VAD default.
- Portable stock-voice agent, explicit dispatch and deployment learning guide.
- Last, an opt-in private Qwen reference-delivery comparison with blinded listening.

## Automated evidence

- Python: **506 passed, 5 integration tests deselected**, 26.41 seconds.
- Frontend: **187 passed across 14 files**.
- Configured strict mypy: **29 source files**, no issues.
- Ruff check: passed. Ruff format check: **67 files already formatted**.
- TypeScript check and Vite production build: passed. Existing large-bundle warning
  remains (about 784 kB minified JavaScript); not a runtime failure.
- `git diff --check`: passed.
- Actual local `v1-mini` turn-detector initialization succeeded using installed
  LiveKit Agents 1.8.1 and local inference 0.2.7. This is initialization proof,
  not conversational quality evidence.
- Expressive CLI dry run succeeded with nonexistent placeholder paths; no reference
  reads, model load or output creation. Five focused tests use fake synthesis.

Regression tests first reproduced incorrect settings focus restoration and invented
offline provider labels; both repairs passed. Independent review covered setup
privacy, microphone lifecycle, SDK turn handling, metrics, cloud wiring and the
expressive harness. No unresolved actionable finding remained in that review.

## Browser evidence

An isolated local server on port 8775 used an empty temporary voice library and no
provider credentials. Desktop (1280 px) and mobile (390 px) had no horizontal
overflow. The setup report rendered real configuration results; its provider CTA
opened the Providers tab. After an earlier direct drawer opening, closing a
guide-opened drawer restored focus to its originating guide button.

Learning disclosure rendered actual disconnected SDK states. A synthetic permission
denial produced actionable microphone guidance. A generated browser tone then ran
through the real AudioWorklet capture path, stopped automatically, produced a replay
element and ended its media track. Discard removed the preview. No physical
microphone was used. Fixtures were removed by reloading the page.

No page runtime errors were observed. Console retains the existing nonfatal SDK
`Room.prepareConnection failed: Start a Studio session first` warning before a
session exists. No connected-room state was fabricated as live acceptance.

## Remaining acceptance

- Live synthesis and a real LiveKit conversation with the new branch. A read-only
  ownership probe confirmed the existing running app holds the voice backend lease;
  the test did not bypass it or stop that app.
- Physical microphone/device playback and human accessibility/usability testing.
- Listening comparison with two authorized same-speaker reference deliveries;
  emotion transfer is not established by the harness or its fake tests.
- Stock cloud inference, named dispatch and managed deployment; no account changes,
  deployment or paid resources were created.
- An independent fresh installation following only the public quickstart.

The workspace's iCloud offloading intermittently stalled dependency reads. Final
Python verification used the same locked dependencies in a temporary environment
outside iCloud. One stalled type-check attempt was terminated; only the completed
29-file check above counts as passing. Prefer a normal local development directory
for model work and virtual environments. No user storage settings were changed.

## Local save status

Changes remain uncommitted on the isolated feature branch. Git staging stalled
on filesystem reads; the owned attempt was terminated and its empty index lock
removed. No commit, push, merge or publication is claimed. Source files and the
completed verification evidence above remain preserved.

## Standalone runtime follow-up

The original worktree was preserved. Continued work now lives on
`cmongan/studio-standalone-runtime` in a normal local development directory,
with its own locked Python environment and frontend dependencies. The local
service now runs this checkout on `http://127.0.0.1:8765`, rather than the
former temporary preview on port 8775. Private configuration and the selected
consented reference were kept outside Git in a separate standalone library;
existing fork settings and voice files were preserved.

Standalone MLX mode requires no running Voicebox application. The legacy HTTP
adapter remains available. Setup checks share one pending background inspection
and return an actionable error after three seconds if local files stall. Audition
watchdog failures now retain their reason instead of appearing as cancellations.

Fresh verification:

- Offline Python: **512 passed, 5 deselected**, 6.59 seconds. A rendering unit test
  was isolated from the developer's real `.env` after it contaminated later tests.
- Frontend: **187 passed across 14 files**. Production build passed.
- Strict mypy: **29 source files**, no issues. Ruff check and format passed
  (**71 files**); `git diff --check` passed.
- Direct local Qwen generation produced 3.6 seconds of nonzero audio from the
  existing model and private reference, with the Voicebox HTTP service offline.
- Real LiveKit integration: **1 passed**, 85.77 seconds, covering two sessions,
  received nonzero generated audio, interruption acknowledgement and cleanup.
  First received audio was approximately 4.2–4.4 seconds after sending text in
  these two runs. This is observation, not a latency benchmark.
- Real local audition integration: **1 passed**, 33.33 seconds, covering generate,
  cancel and retry, valid nonzero WAV output, one-shot retrieval and unchanged
  saved settings. An earlier cold attempt failed after approximately 66 seconds;
  the exact cause was not established because the previous watchdog discarded
  its failure reason. The subsequent retry passed after that diagnostic repair.
- Browser setup guide found all 12 configuration checks. Its two runtime checks
  correctly remain labelled unverified because the endpoint only inspects files.

These results resolve the earlier lack of live synthesis/conversation proof.
Physical microphone and human listening, expressive reference comparison, cloud
agent deployment and an independent fresh installation remain unverified.

The browser also completed a real typed conversation, displayed the agent reply
and streamed-audio state, and ended the session. The Learn LiveKit view reflected
the actual connected pipeline. Desktop (1280 px) and mobile (390 px) had no
horizontal overflow; no browser page errors were reported. A remaining generic
transcript speaker label was changed from Voicebox to Agent. Physical playback
was not listened to as part of this automated check.
