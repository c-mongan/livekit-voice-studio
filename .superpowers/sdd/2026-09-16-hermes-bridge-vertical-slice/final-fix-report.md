# Final Fix Report

## Outcome

The final whole-branch review findings for the Hermes voice companion are resolved in the preserved worktree based on `10b1109a7651198fa548dd657892a99db06c9251`.

## Fixes completed

- Forwarded Hermes `tool.started` and `tool.completed` events as a bounded, redacted `ToolStatus` projection and published them reliably only to the room owner.
- Added strict browser parsing for the exact tool-status wire shape and durable, bounded action-status rendering that survives speech interruption.
- Retained approval authority until Hermes emits the matching `approval.responded` event or the run reaches a terminal state; browser RPC acceptance no longer clears the card.
- Disabled typed sending and microphone control while approval remains authoritative, and actively pauses an enabled microphone.
- Bounded the adapter to one active stream plus one pending stream.
- Added one-shot trusted voice context after an acknowledged interruption.
- Drained an active Hermes run to terminal acknowledgement before closing its credential-bearing client.
- Made the worker interrupt response report `hermesTerminalAcknowledged` and made the browser fail closed when a Hermes stop is explicitly unacknowledged or its RPC acknowledgement is unavailable.
- Replaced provider-assuming privacy copy with neutral configured-provider language.
- Strengthened the opt-in live harness so completed-action truth comes from observed `hermes.tool.status` data and stale-retired-text truth comes from events observed after the exact interruption boundary.

## Deterministic verification

All commands ran from the preserved worktree without external services or private model assets.

| Check | Result |
| --- | --- |
| `uv run pytest -m "not integration" -q` | 569 passed, 6 deselected |
| Focused Hermes/worker/provider/evidence tests | 68 passed |
| `uv run ruff check .` | Passed |
| `uv run mypy` | Passed; 25 source files checked |
| `npm test` | 184 passed across 13 files |
| `npm run build` | Passed |
| `npm audit --audit-level=high` | 0 vulnerabilities |
| `git diff --check` | Passed |

The production build reports Vite's non-failing warning that the main minified JavaScript chunk is larger than 500 kB. No new correctness, type, lint, or security-audit failure was observed.

## Live verification boundary

The opt-in integration test was not executed because this fix wave was explicitly constrained not to run external services or private assets. It remains gated by `HERMES_STUDIO_LIVE=1` and the documented LiveKit, Hermes, Nemotron, Qwen, voice-bundle, and authorized-audio prerequisites. Its deterministic evidence helpers are covered by unit tests.

## Targeted final repair cycle

### Outcome

Resolved the four residual safety findings on top of `6679835adc3f40e22c6b341d82bf62790a5ba27d` without contacting external services or loading private assets.

### Repairs

- Tool-status publication failure now latches adapter uncertainty before the active run is drained. A completed action that could not be disclosed therefore prevents any second run on that adapter and requires a fresh room/adapter.
- Browser tool-status dedupe identifiers now retain exactly the same trailing 64-event window as the rendered status history; an evicted identifier can be accepted again instead of growing an unbounded set.
- Hermes interrupt acknowledgements now require the exact five-field worker response, including all four expected boolean fields, and require `hermesTerminalAcknowledged: true` even when admission may still be in flight. Empty, partial, false-terminal, malformed, and RPC-failure responses remain muted and block send, microphone, and approval interactions until End session. Legacy non-Hermes `{}` acknowledgement behavior remains unchanged.
- `HermesLLM.aclose()` now coordinates an explicit admission task. Close waits boundedly for an in-flight run ID, exact-stops any admitted run, waits for terminal acknowledgement, and only then closes the credential-bearing client. A hanging or unacknowledged admission/run latches uncertainty, returns an explicit recovery error, and deliberately leaves the client open so no possibly-live run is orphaned behind a closed client; a later close can finish after terminal recovery.
- A microphone-disable failure while approval is pending now enters the same fail-closed state: speaker output stays muted, typed send, microphone controls, and every approval choice are disabled, and End session remains available.

### TDD evidence

The new focused regressions were observed failing before implementation:

- Python: 3 failures covering tool-status callback reuse and both admitted/hanging close races.
- Frontend: 4 failures covering empty/false Hermes acknowledgements, the >64-event dedupe boundary, and approval-time microphone pause failure.

After implementation:

- `uv run pytest tests/test_hermes_llm.py tests/test_studio_worker.py -q` — **51 passed**.
- `npm test -- --run src/App.test.tsx src/HermesApproval.test.tsx` — **55 passed**.

### Final deterministic verification

- `uv run pytest -m "not integration" -q` — **572 passed, 6 deselected**.
- `uv run ruff check .` — **passed**.
- `uv run mypy` — **passed; 25 source files checked**.
- `npm test` — **189 passed across 13 files**.
- `npm run build` — **passed**; Vite retained the non-failing 780.22 kB main-chunk advisory.
- `npm audit --audit-level=high` — **0 vulnerabilities**.
- `uv run python -m py_compile examples/hermes_llm.py tests/test_hermes_llm.py` — **passed**.
- `git diff --check` — **passed**.
- Added-line safety scan for private-key, credential-assignment, `eval`/`exec`, `os.system`, and subprocess invocation patterns — **passed**.
- Safe skip with `HERMES_STUDIO_LIVE` absent — **1 skipped** with the explicit authorization requirement.
- Safe skip with `HERMES_STUDIO_LIVE=1` and blank `LIVEKIT_URL` — **1 skipped** with the complete missing-prerequisite list.

### Remaining boundary

The live integration test remains deliberately unexecuted. Real LiveKit/Hermes/Nemotron/Qwen behavior and latency gates still require separate authorization plus complete credentials and private assets. No deterministic safety finding remains in this repair scope.
