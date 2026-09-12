# Development

Work in an isolated checkout. Use Python 3.11-3.13 and the committed `uv.lock`.

```sh
uv sync --frozen --package livekit-plugins-voicebox --extra dev --extra example --extra azure --extra agents --python 3.12
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
uv run --no-sync pytest -m "not integration" --cov=livekit.plugins.voicebox
uv build --package livekit-plugins-voicebox
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web test
npm --prefix web run build
```

Tests create WAVs and loopback aiohttp servers in memory. They do not require
Voicebox, a GPU, microphone, cloud credentials, or model downloads.
For a machine-readable functional report, run
`uv run --no-sync python -m tools.evaluate`.
See [evaluation](docs/evaluation.md) before enabling any live measurements.

Keep changes atomic: profile snapshots, decode limits, cancellation/drain
ownership, error mapping, and audio emission form one provider contract.
Never release an inference lease just because its consumer was cancelled.
Never retry an ambiguously submitted generation or replay partial speech.

Only run the explicitly opted-in integration test with an authorized voice.
If enabled, missing configuration or readiness is a failure, not a skipped test.
Do not run live cancellation or load tests on a shared Voicebox backend.
Use synthetic fixtures; never commit recordings, profile IDs, secrets, or
benchmark results containing user text.

Before dependency upgrades, inspect released SDK types and Voicebox routes.
Update compatibility evidence and test native metrics, final markers, public
stream cleanup, and the three-sentence adapter path. Do not infer compatibility
from a successful import alone.

## Studio

`web/` owns the React interface. `examples/studio.py` owns HTTP, room-scoped
tokens, admission and the supervised process. `examples/studio_worker.py` owns
one LiveKit conversation. The installable provider remains independent of both.

Use `make studio` after building the UI. Do not run the standalone example worker
at the same time. The optional Vite dev server proxies to the local Studio API;
the built UI is served directly by Python.
On macOS this starts the user-managed service and returns. `make studio-stop`
requests graceful shutdown. Use `make studio-foreground` when debugging in a
terminal. Add the `playback` extra when exercising local read-aloud hardware.

Before changing session lifecycle code, cover abandoned starts, concurrent tabs,
lost heartbeats, safe drain, crashed workers and reconnects. A killed agent
process is not evidence that a Voicebox inference thread stopped.

Keep browser controls explicit: microphone off before user consent, no automatic
spoken greeting, no transcript persistence, visible failed/blocked states and a
keyboard-accessible stop/end path. Check 390-pixel and desktop layouts, keyboard
focus, reduced motion and a real text-to-audio room. Browser/SDK mocks are useful
but do not establish real microphone or audible interruption behavior.

The project should remain small. Reuse supported LiveKit components rather than
building media primitives. Preserve upstream notices for adapted code, pin the
source revision, and explain necessary deviations in `docs/provenance.md`.
