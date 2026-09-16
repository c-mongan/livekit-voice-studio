# Task 4 Report: Worker wiring and action-safe interruption

## Status

Complete. Studio now gives each Hermes-backed room a scoped `voice:<room-id>` session, wires the reviewed `HermesLLM` into the existing LiveKit speech pipeline, and separates immediate playback interruption from Hermes run stopping without claiming that completed actions were undone.

## Implementation

- Scoped worker Hermes configuration to exactly the selected profile, selected base URL, generated room session ID, and server-side API key; unrelated ambient `HERMES_*` values are removed.
- Kept Hermes credentials and profile/control-plane details out of the browser session response and worker event logs.
- Preserved the existing `configured_ai()` path, including the Task 3 fail-closed approval callback until Task 5 supplies the UI/RPC approval flow.
- Preserved Nemotron STT, explicit final-utterance commit, FastQwenTTS lifecycle/drain ordering, Silero VAD, disabled preemptive generation, one-room ownership, and legacy provider selection.
- Added owner-checked `voicebox.interrupt` behavior that stops LiveKit playback first, requests stop for the exact active Hermes run once, and returns `stoppedPlayback`, `hermesStopRequested`, `actionUndone: false`, and backend state.
- Kept repeated interruption idempotent per Hermes run and retained the adapter rule that discards deltas after a stop request.
- Replaced LiveKit-side personality/tool denial with the bounded speech-delivery instruction, leaving Hermes profile/SOUL, tools, memory, and approvals authoritative.
- Updated the browser to mute playback until the RPC returns or times out and announce: “Speech stopped. Any completed Hermes action remains completed.”

## TDD evidence

- Worker and broker tests first failed because the old RPC did not stop Hermes, returned the old payload, used the public session handle instead of `voice:<room-id>`, inherited unrelated Hermes variables, and retained old instructions.
- Browser tests first failed because playback remained muted pending a later agent-state transition and the old “Reply stopped” acknowledgement remained.
- Minimal implementation changes made each focused regression pass; the final tests additionally verify owner rejection, playback-before-Hermes ordering, one stop request across repeated interrupts, Qwen drain before room disconnect, scoped secrets, and suppression of late stopped-run deltas.

## Verification

- `uv run pytest tests/test_studio_worker.py tests/test_provider_choices.py tests/test_studio.py tests/test_hermes_llm.py -q` — **78 passed**.
- `npm --prefix web test -- src/App.test.tsx` — **35 passed**.
- `uv run pytest -q` — **537 passed, 5 skipped**.
- `npm --prefix web test` — **169 passed** across 12 files.
- `uv run ruff check .` — **passed**.
- `uv run mypy` — **passed**, 25 source files clean.
- `npm --prefix web run build` — **passed**; Vite retained the existing approximately 773 kB chunk-size warning.
- `git diff --check` and added-line security pattern scan — **passed**.

## Self-review

- The browser session-creation response contains none of the worker's Hermes API key, selected profile, base URL, or `voice:<room-id>` session value.
- Wrong-owner RPC calls fail before playback or Hermes state is touched.
- Playback interruption precedes the Hermes stop request; repeat calls do not issue duplicate stop requests for the same run.
- `actionUndone` is always false, and the UI does not use cancellation language for external actions.
- No production approval path was added; Task 3's default approval callback remains fail-closed.
- No blocking correctness or security concern remains. The only observed warning is the pre-existing Vite chunk-size advisory.

## Round 1 exact-run interruption race fix

- Root cause: the worker awaited LiveKit playback interruption before reading `active_run_id`, allowing the interrupted run to retire or be replaced before the parameterless Hermes stop selected its target.
- Added an opaque `HermesRunHandle` captured synchronously from the active adapter state and `stop_run(handle)`, which retains that exact state and rejects handles from another adapter. `stop_active()` keeps its prior idempotent and timeout/uncertain behavior by delegating to the exact-run operation.
- The worker now captures the Hermes run before any await, starts playback interruption immediately, and starts one deduplicated exact-run stop concurrently. Wrong-owner rejection and `actionUndone: false` remain unchanged; no Task 5 approval behavior was added.
- Regression coverage makes two concurrent owner interrupts begin against `run-1`, has LiveKit interruption replace the visible active run with `run-2` before completing, and proves `run-1` is stopped exactly once while the parameterless stop path never touches `run-2`.
- TDD RED: the focused regression run failed in all 3 cases because `capture_active_run` did not exist and the worker never called the exact-run stop (`3 failed`).
- GREEN verification: `uv run pytest tests/test_hermes_llm.py tests/test_studio_worker.py -q` — **28 passed**; `uv run ruff check .` — **passed**; `uv run mypy` — **passed**, 25 source files clean; `git diff --check` — **passed**.
