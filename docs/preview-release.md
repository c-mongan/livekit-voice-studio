# Development preview — unreleased

## What works in the tested setup

Standalone Studio on Apple Silicon uses local Nemotron recognition and local Qwen
speech synthesis, with configured LiveKit transport and Copilot reasoning. Users
record an authorized voice, audition generated speech, and choose text or microphone
input. Codex reasoning was separately checked in its documented restricted mode;
this is not a claim that every provider supports the full spoken pipeline equally.

Recent synthetic live checks cover complete replies, a spoken interruption with a
correct follow-up, 20 seconds of idle input, and five turns with pauses. The latest
five-turn run took 3.019–4.226 seconds from speech end to nonzero remote audio,
median 3.288 seconds. These are small samples, not a latency guarantee.

Startup stages and safe diagnostic timing are visible through the local service.
No timeout, interruption threshold, or cleanup ownership check was weakened.

## Release checks

The offline suite has 477 tests. The frontend has 167 tests. Python 3.11–3.13 and
frontend checks passed hosted CI before the latest security update; final CI must
also pass for the release commit. The test runner is updated to pytest 9.0.3 and
pytest-asyncio 1.4.0 to resolve [GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g).
CI now checks known Python and npm dependency advisories. The unpublished project
itself has no PyPI advisory record; code review/tests are still necessary.

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
- Local speech processing still uses remote transport and reasoning. Users provide
  their own entitled accounts; the code license supplies no subscription access.
- Model licenses and dependency notices are separate from project MIT licensing.
  No bundled-model or binary distribution is prepared in this preview.

Report failures using synthetic prompts and redacted diagnostic timings. Do not
attach private voice references, .env files, or unredacted service logs. Follow
SECURITY.md for vulnerability reports.
