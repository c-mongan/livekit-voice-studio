# Task 6 report — Hermes voice companion verification

## Status

Implemented the opt-in live vertical-slice harness and updated the public documentation. No LiveKit or Hermes service was contacted, no room was created, and no private voice/model asset was loaded. Real live end-to-end verification remains an outstanding release gate.

## Changes

- Added `tests/integration/test_hermes_studio_live.py`.
  - Requires the exact `HERMES_STUDIO_LIVE=1` opt-in.
  - Skips unless all LiveKit credentials, Hermes settings, required provider selections, local model paths, authorized voice bundle, synthetic audio, and expected transcript are explicitly present.
  - Does not load `.env`.
  - Defines one continuous-room check for streamed reply text, same-session continuity, a harmless fixture-file read, an approval-required sandboxed command denied through the owner RPC, Nemotron final transcription, at least 20 Qwen timing samples, interruption, stop-to-silence, stale-delta rejection, action-truth, and full ownership drain.
  - Emits only a bounded timing/status JSON summary; it does not save prompts, transcripts, credentials, voice identifiers, audio, or provider responses.
- Updated `.env.example` with Hermes as the recommended reasoning path and disabled live-test variables.
- Replaced the Azure-first architecture diagram with the Hermes `/v1/runs` path while retaining Copilot, Codex, Azure OpenAI, and OpenAI as legacy alternatives.
- Updated `README.md`, `docs/agent-quickstart.md`, and `docs/oss-readiness.md` to state:
  - local speech is not fully offline;
  - Hermes owns reasoning, tools, memory, sessions, permissions, and action state;
  - approvals are click/tap only;
  - stopping speech does not undo completed actions;
  - the local path is currently Apple Silicon and English-only;
  - Expressive Mode is planned, not shipped, and local Qwen does not implement it;
  - model weights and voice recordings are not bundled;
  - live Hermes E2E remains unverified and release-blocking.
- Broadened the pytest integration marker description to cover explicitly authorized service/model use.

## Verification

All commands ran from the Task 6 worktree at base `1a1dc5532c73232f67f3f19f2366baab2577b0c5`.

- `uv run pytest -m "not integration"` — **549 passed, 6 deselected**.
- `uv run ruff check .` — **passed**.
- `uv run mypy` — **passed; 25 source files checked**.
- `npm --prefix web test` — **179 passed across 13 files**.
- `npm --prefix web run build` — **passed**; Vite retained its existing advisory that the 776.92 kB main chunk exceeds 500 kB.
- `npm --prefix web audit --audit-level=high` — **0 vulnerabilities**.
- `uv run python -m py_compile tests/integration/test_hermes_studio_live.py` — **passed**.
- `git diff --check` — **passed**.
- Added-line scan — **no hardcoded-secret, shell-execution, or eval/exec pattern found**.

### Safe-skip evidence

- With `HERMES_STUDIO_LIVE` absent, the named live test collected and skipped with: `Set HERMES_STUDIO_LIVE=1 only after authorizing real Hermes and LiveKit usage.`
- With `HERMES_STUDIO_LIVE=1` but `LIVEKIT_URL` explicitly blank, it collected and skipped while listing all missing prerequisites. No external connection or model load occurred.

## Outstanding live gate

The command below was deliberately **not run with complete credentials/assets** because separate authorization was not provided:

```sh
uv run pytest tests/integration/test_hermes_studio_live.py -m integration -v -s
```

Therefore these live claims are **not passed**: real Hermes tool execution and denial, real room/session continuity, real Nemotron transcription timing, Qwen warm P95 ≤350 ms, playback stop-to-silence ≤200 ms, absence of stale spoken deltas, Hermes terminal state after stop, and worker/Hermes/Qwen/Nemotron drain under live service conditions.

## Self-review

No hardcoded credential or destructive command was added. The only approval fixture targets a pytest-owned temporary path and is asserted absent after denial. The harness cannot run from a copied example configuration because it requires explicit non-empty credentials and existing asset paths. Independent subagent review was not performed because the binding task ruling prohibited dispatching subagents.

## Fix round 1 — live observability and documentation accuracy

### Status

Resolved all four review findings without starting live services, contacting LiveKit or Hermes, or loading model/voice assets. The live gate remains deliberately opt-in and unexecuted pending separate authorization and complete credentials/assets.

### Changes

- Replaced tuple-based transcript tracking with timestamped, sequence-bounded observation bookkeeping for assistant transcriptions and non-zero audio frames.
- The interruption probe now records its run start, proves a non-final `RETIRE-ME` transcript and audible output before Stop, captures the retirement boundary immediately before the interrupt RPC, inspects all post-boundary events, rejects retired marker text, and rejects audio beyond the 200 ms silence gate before starting the continuity turn.
- `send_and_wait` now requires a non-empty, non-final assistant transcription event from the current turn in addition to the expected reply text. This is the direct LiveKit streaming signal used by the harness; a final-only transcript no longer passes.
- `voicebox.interrupt` now returns `hermesTerminalAcknowledged` from the actual `HermesLLM.stop_run()` boolean. Concurrent duplicate interrupts share the same stop task and therefore report the same observed result. `actionUndone` remains independently and explicitly false.
- The live test asserts `hermesTerminalAcknowledged is True` and emits that observed value in its JSON report instead of a literal success value.
- Corrected `docs/agent-quickstart.md`: the harness denial is performed through the session owner-only approval RPC, not by a browser click. The document also names non-final assistant transcription as the streaming evidence.
- Added deterministic helper tests covering final-only rejection, pre-stop marker/activity provenance, late retired text rejection, post-stop audio timing, and accepted bounded audio drain.

### Verification

- `uv run pytest tests/test_hermes_live_evidence.py tests/test_studio_worker.py tests/test_hermes_llm.py -q` — **46 passed**.
- `uv run pytest -m "not integration"` — **556 passed, 6 deselected**.
- `uv run ruff check .` — **passed**.
- `uv run mypy` — **passed; 25 source files checked**.
- `npm --prefix web test` — **179 passed across 13 files**.
- `npm --prefix web run build` — **passed**; Vite retained the existing 776.92 kB chunk-size advisory.
- `npm --prefix web audit --audit-level=high` — **0 vulnerabilities**.
- `uv run python -m py_compile examples/studio_worker.py tests/integration/test_hermes_studio_live.py tests/test_hermes_live_evidence.py tests/test_studio_worker.py` — **passed**.
- `git diff --check` — **passed**.
- Safe-skip check with `HERMES_STUDIO_LIVE` absent — **1 skipped** with the explicit authorization reason.
- Safe-skip check with `HERMES_STUDIO_LIVE=1` and blank `LIVEKIT_URL` — **1 skipped** with the complete missing-prerequisite list.

### Remaining gate

The authorized live command was not run. Real service behavior—including whether the configured LiveKit version emits the required non-final assistant transcription, Hermes returns terminal acknowledgement within its stop timeout, and the measured Qwen/Nemotron timing gates pass—remains unverified until the separately authorized live test is executed.

## Fix round 2 — non-final proof and continuity-wide stale monitoring

### Status

Resolved the three remaining deterministic evidence gaps without changing the terminal-status fix or public documentation wording. No live external service, local model, or voice asset was started or contacted.

### Changes

- `_streamed_assistant_text_observed()` now searches only accumulated non-final assistant transcription chunks for the expected text. A final `READY` plus an unrelated non-final `REA` is rejected; genuine non-final `REA` + `DY` accumulation is accepted.
- `_assert_interruption_evidence()` now requires the pre-stop `RETIRE-ME` marker itself to occur in a non-final assistant transcription event.
- The stop boundary remains captured immediately before the interrupt RPC. The silence observation window is bounded at continuity initiation so continuity audio is excluded from the retired-audio calculation, while stale assistant transcript inspection is deferred until continuity completes and covers every event after the stop boundary.
- Added deterministic regressions for final-only expected text, genuine incremental text, final-only pre-stop marker evidence, continuity audio separation, and a retired marker arriving during continuity.

### Verification

- `uv run pytest tests/test_hermes_live_evidence.py tests/test_studio_worker.py tests/test_hermes_llm.py -q` — **50 passed**.
- `uv run pytest -m "not integration"` — **560 passed, 6 deselected**.
- `uv run ruff check .` — **passed**.
- `uv run mypy` — **passed; 25 source files checked**.
- `npm --prefix web test` — **179 passed across 13 files**.
- `npm --prefix web run build` — **passed**; Vite retained the existing 776.92 kB chunk-size advisory.
- `npm --prefix web audit --audit-level=high` — **0 vulnerabilities**.
- `uv run python -m py_compile tests/integration/test_hermes_studio_live.py tests/test_hermes_live_evidence.py` — **passed**.
- Both safe-skip checks passed: absent opt-in skipped with the authorization reason; explicit opt-in with blank `LIVEKIT_URL` skipped with the missing-prerequisite list.
- `git diff --check` — **passed**.

### Remaining gate

The authorized live command remains deliberately unexecuted. Real LiveKit/Hermes continuity, stale-output behavior, and latency gates remain release-blocking until separately authorized with complete credentials and assets.
