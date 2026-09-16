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
