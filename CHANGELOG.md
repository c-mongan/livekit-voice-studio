# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project is a
development preview and does not yet follow semantic versioning.

## [Unreleased]

### Added

- Code of conduct, changelog, and pull request template.

### Changed

- Pinned GitHub Actions to commit SHAs.
- Restructured the README so the quickstart comes first.
- Moved the provider recovery validation record to `docs/history/`.

### Removed

- Internal planning documents (`BUILD.md` and `docs/superpowers/`).

## 2026-10-23 - Project video overview

### Added

- Narrated 22-second video overview, published as the
  `portfolio-overview-2026-10-23` GitHub release with captions and transcript
  (#12).

## Development preview (September 2026)

### Added

- Model dropdowns for installed Ollama models and Copilot and Codex account
  models (#11).
- Agent-assisted setup and clarified Copilot and Codex support (#2).
- Local-first Studio with guided LiveKit learning and recovery (#4).
- Standalone Voicebox developer preview (#1).

### Changed

- Improved provider recovery, verified Codex speech, and clarified local setup
  (#10).
- Improved preview reliability, identity, and first-run guidance (#7).
- Documented validated source installation and preview reliability (#5).

### Fixed

- Session cleanup is now safe after a room disconnect (#8).
- Bounded incoming audio is retained during recognition finalization (#6).

### Tests

- Verified spoken interruption after echo warm-up (#9).

## Earlier history

### Added

- Managed launch and local reply narration; read-only preflight and structured
  evaluation; onboarding polish; initial Voicebox Studio snapshot.

[Unreleased]: https://github.com/c-mongan/livekit-voice-studio/compare/portfolio-overview-2026-10-23...HEAD
