# Development preview — unreleased

Latest bounded follow-up: [quality-pass results and remaining acceptance work](preview-quality-2026-09-19.md).

Additional evidence: [repeated conversations and interruption recovery](reliability-2026-09-19.md).

## What works in the tested setup

Standalone Studio supports independent local or configured LiveKit transport and
local Ollama or selected remote reasoning, with local Nemotron recognition and
Qwen synthesis on the tested Mac. Users can record an authorized voice, audition
new speech and choose text or microphone input.

Prior synthetic tests exercised local/cloud combinations, interruption and
follow-ups. A newer all-local run reported a full input buffer under high
memory pressure before its first reply. This remains a developer preview with
an unresolved reliability issue; no single successful run is a latency guarantee.
See the [current validation record](validation-2026-09-19.md) and
[earlier component evidence](superpowers/verification-2026-09-19-components.md).

## Release checks

The merged PR #4 revision passed hosted Python 3.11–3.13 and frontend CI. A fresh
clone on the maintainer's Mac passed 540 offline Python tests, 216 frontend tests
and the production build. CI checks known Python and npm dependency advisories.
An independent user's complete installation and human listening remain unverified.

Use docs/releasing.md for source/package boundaries. The Studio source archive
includes no credentials, voices, model weights, or installed dependencies. The
Python wheel is only the LiveKit provider library, not a Studio installer.

## Known limitations

- An earlier provider failure and a separate cold-model timeout remain unexplained.
  Subsequent successful runs do not prove these intermittent issues are fixed.
- Human microphone recognition, naturalness, speaker similarity, and exact audible
  stop latency are not established by synthetic tests.
- A clean checkout on the maintainer's Mac is not independent hardware/account
  validation. Existing package caches may be reused during installation.
- Apple Silicon is the supported local fast-voice path. Linux CI validates Python
  logic, not MLX audio. Windows and broad device compatibility are not claimed.
- New libraries default to local LiveKit and Ollama with local recognition and
  synthesis; existing settings are preserved. Remote transport and reasoning are
  independent opt-in choices requiring the user’s own services/accounts. The code
  license supplies no subscription access. Local routing is not offline certification.
- Model licenses and dependency notices are separate from project MIT licensing.
  No bundled-model or binary distribution is prepared in this preview.

Report failures using synthetic prompts and redacted diagnostic timings. Do not
attach private voice references, .env files, or unredacted service logs. Follow
SECURITY.md for vulnerability reports.
