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
