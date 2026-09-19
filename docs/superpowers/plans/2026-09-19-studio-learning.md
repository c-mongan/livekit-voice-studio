# Studio Learning Implementation Plan

> Use Superpowers test-driven development, bounded implementation tasks, independent review and verification before completion. User authorized implementation of the roadmap; proceed without repeating approval gates.

**Goal:** Make first use approachable and teach LiveKit through a working local voice studio.

**Architecture:** Extend existing setup and conversation boundaries. Keep local model inference single-owner. Learning and experimental features remain opt-in.

**Tech Stack:** Python 3.12, aiohttp, LiveKit Agents 1.8.1, React 19, TypeScript, Vite, Vitest and pytest.

**Spec:** ../specs/2026-09-19-studio-learning.md

## Global constraints

- Preserve the running Hermes installation and private assets.
- No automatic model downloads, microphone capture, cloud deployment or paid resources.
- Keep fixed allowlisted diagnostic text and explicit unknown states.
- Test and build serially under limited disk space.

## Review focus

- Late microphone permission after cancellation must release the stream.
- Read-only setup must neither load models nor leak secret/private-path values.
- Offline/disconnected UI must not show stale data as current.
- Turn detection changes must preserve recognition finalization and cancellation.
- Experiments must never claim listening or live acceptance from mocked tests.

## Task 1: Backend setup and turn handling

Own examples/studio.py, examples/studio_worker.py, new examples/studio_setup.py and examples/turn_handling.py, Python regression tests. Add GET /api/setup returning {version:1, checks:[{id,status,message,action}]} with fixed public text derived from doctor results. Add validated VOICEBOX_TURN_DETECTION=vad|audio-local; default vad, local detector version pinned explicitly without eager initialization on setup reads. Inspect installed API compatibility before integration. Test offline behavior, private-path sanitization, invalid values, and unchanged default cancellation/finalization.

## Task 2: Guided frontend and microphone test

Own web/src/SetupGuide.tsx, MicrophoneCheck.tsx and corresponding tests plus App integration and styles. Consume /api/setup on explicit disclosure opening, render missing/unverified checks with actionable guidance, and keep local audition independent of cloud. Test microphone recording through a bounded browser stream; release on all exits, keep captured preview in memory and revoke object URLs. Tests must exercise cancellation, unavailable devices, permission rejection and cleanup.

## Task 3: Learn LiveKit and diagnostics

Own frontend learning view and tests. Default to conversation; toggle learning details, preserve existing pipeline metrics and expose recognition/end-of-utterance metrics already provided by backend. Show observed SDK connection/agent states and scoped room details only for owning browser. Explain tracks, RPC and dispatch via a small set of runnable exercises. Test missing/stale measurements and mode switching.

## Task 4: Portable example and coherent documentation

Own new examples/cloud_agent.py, focused tests and docs. Inspect public installed SDK APIs; create explicit-dispatch example with stock provider speech and no imports of local private voice libraries. Add a walkthrough with prerequisites, cost boundaries and local/cloud distinctions. Align first-run .env example, quickstart, architecture and README without changing existing user's .env.

## Task 5: Expressive Qwen experiment, last

Create tools/expressive_compare.py and tests using existing FastQwenTTS with explicitly supplied private reference bundles, fixed comparison sentences and private output directory. Require explicit synthesis flag; dry run emits no private paths or audio. Refuse overwriting results. Produce timing report and blind listening worksheet; no automated emotion claim. Research steering integration compatibility and document findings rather than shipping unverified runtime changes.

## Verification and delivery

- Run baseline and focused tests, recording RED/GREEN evidence per task.
- Run pytest -m 'not integration', ruff check/format, mypy, frontend tests/typecheck/build.
- Browser: real backend without credentials/model use for setup; bounded synthetic fixtures for connected learning states. Desktop/mobile overflow, keyboard controls, error console and microphone lifecycle.
- Inspect existing local environment for safe synthesis/live proof; do not interfere with active session or claim unavailable proof.
- Fresh independent review of the full change. Repair findings and rerun affected checks.
- Preserve branch and report exact location, checks and remaining external/human acceptance.
