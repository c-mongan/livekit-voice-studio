# Approachable voice studio and LiveKit learning

The user approved implementing the previously reviewed roadmap end to end, with expressive Qwen last. The product remains a single-user, local-first Apple Silicon voice studio. Its first success is a generated local voice sample; conversation follows after account setup. A separate learning view explains real state and measurements.

## Outcomes

1. Guided first-run checks expose offline doctor findings safely in the browser. No credentials, absolute private paths, transcripts, or voice bytes enter diagnostics. Existing voice enrollment and auditions remain the source of truth. Microphone testing is explicit, local, bounded, and releases capture on exit.
2. Turn handling becomes an explicit, validated choice: existing VAD remains the compatible default; local audio turn detection is opt-in and unavailable installations fail clearly. Preserve Nemotron finalization, stop ownership, and backend drain semantics. No unannounced model downloads.
3. Default conversation UI is simple. An optional Learn LiveKit view explains rooms, tracks, state, RPC and local/cloud boundaries, showing actual available measurements without fabricated timing totals. Missing data stays unknown.
4. A small cloud-compatible agent example teaches explicit dispatch and managed deployment without uploading local voices or pretending MLX runs in Linux. No deployment or paid resource creation occurs in this task.
5. Expressive Qwen is a final bounded experiment: reproducible local comparison tooling, neutral baseline, explicit reference inputs, latency and listening rubric. Do not present pitch changes or unsupported instruct parameters as emotion support. Do not download alternate models automatically.

## Acceptance

Focused regression tests fail before implementation and pass afterward. Run offline Python tests, frontend tests, type checking, lint and build. Inspect changed UI in an isolated browser at desktop and mobile sizes, including errors and actual interactions. Report synthetic UI checks, local synthesis, LiveKit integration and independent-human acceptance separately. Preserve the running Hermes worktree and all private assets. Maintain source licensing and truthful installation guidance.

## Design

Reuse existing React, CSS tokens, aiohttp endpoints, doctor, recorder and LiveKit APIs. Setup lives in an accessible disclosure before starting; advanced diagnostics appear only when requested. Backend setup output uses fixed allowlisted messages. No automatic repairs, microphone requests, provider calls or model loads during a read-only setup check. The microphone check owns its stream and stops every track on stop, cancel, navigation, error or late permission resolution.
