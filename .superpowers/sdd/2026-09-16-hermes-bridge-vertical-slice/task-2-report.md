# Task 2 Report: LiveKit `HermesLLM` adapter

## Status

DONE

## Implemented

- Added `HermesLLM`, `ApprovalRequest`, and a LiveKit `llm.LLMStream` implementation over the reviewed `HermesRunsClient`.
- Sends only the latest non-empty user turn to Hermes with a fresh UUID idempotency key; assistant history and LiveKit tools are not forwarded.
- Serializes runs with one adapter-local lock and exposes only the exact current `active_run_id`.
- Converts only string `message.delta` payloads into assistant `ChatChunk` values and bounds aggregate response characters.
- Suppresses mismatched/retired run events and never emits tool, reasoning, approval, or raw error content as speech.
- Reconciles SSE EOF with one status read and requires a successful terminal completion unless a locally requested cancellation is acknowledged.
- Forwards validated approvals exactly, binds responses to the active run/request/advertised choice, rejects stale or duplicate requests, and prevents duplicate response submission.
- Stops only the stream-owned run on cancellation, sends stop once, polls to terminal acknowledgement, and permanently marks the adapter uncertain when acknowledgement cannot be established.
- Sanitizes start, stream, approval, timeout, and terminal failures as non-retryable LiveKit `APIError` values.

## Files

- Created `examples/hermes_llm.py`
- Created `tests/test_hermes_llm.py`
- Created `.superpowers/sdd/2026-09-16-hermes-bridge-vertical-slice/task-2-report.md`

## TDD evidence

- Initial focused run failed during collection with `ModuleNotFoundError: No module named 'examples.hermes_llm'`.
- Implemented the minimum adapter and observed the initial 17 contract tests pass.
- Added a local-cancel terminal mapping regression; it failed because the stream task completed normally, then passed after mapping acknowledged `run.cancelled` to `asyncio.CancelledError`.
- Added a duplicate approval-request regression; it failed because the repeated request was forwarded, then passed after run-scoped request-ID replay rejection.
- Added repeat approval-response rejection; it failed because the response was submitted twice, then passed after consuming the pending request before transport submission.

## Verification

- `uv run pytest tests/test_hermes_llm.py -v` — 19 passed.
- `uv run ruff check examples/hermes_llm.py tests/test_hermes_llm.py` — passed.
- `uv run mypy examples/hermes_llm.py` — passed with no issues.
- `uv run pytest -q` — 519 passed, 5 skipped.
- `uv run python -m compileall -q examples/hermes_llm.py` — passed.
- `git diff --check` — passed.

The `uv run` interpreter was verified as the worktree `.venv` Python 3.12 environment with user-site packages disabled; LiveKit resolved from that environment.

## Self-review

- The diff is scoped to the requested adapter, tests, and this report.
- Approval authority stays in Hermes; the adapter only forwards an exact active request and one advertised response.
- Cancellation never claims action rollback and blocks adapter reuse if terminal acknowledgement is uncertain.
- No credentials, raw provider diagnostics, tool payloads, reasoning payloads, or approval commands enter spoken chunks or exception strings.
- No subagents were dispatched.

## Concerns

- No blocker found in the requested unit-tested adapter scope. A live Hermes/LiveKit interruption test is still required by the broader design before release to establish how partial assistant text persists in durable Hermes history.
