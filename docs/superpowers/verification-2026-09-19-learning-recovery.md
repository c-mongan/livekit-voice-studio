# Learning and onboarding recovery verification

The optional pipeline inspector now contains three real-control troubleshooting
exercises: silent input/output, interruption and recovery, and unavailable local
reasoning. Opening the guide injects no fault. Progress is explicitly self-reported
and kept only in component memory. An interview walkthrough and first-run failure
isolation table accompany it.

Setup report validation now rejects empty/malformed reports and duplicate IDs.
Missing items appear before unverified checks; passed checks are collapsed. Four
new assertions failed against the previous code; the corrected suite passes.

Verified on 2026-09-19:

- Frontend: 216 tests across 18 files; TypeScript and Vite build passed.
- Python: 535 passed, 5 integration tests deselected. Ruff, formatting (74 files),
  and mypy (31 files) passed.
- Real local endpoint preflight against loopback port 1 rejected the unavailable
  service with the expected bounded error. No settings or running services were
  changed. Full live recovery/inference was not rerun for this UI/docs update.
- Browser: exercised selection and per-exercise progress, inspected setup results
  from the real server (12 found checks collapsed, two unverified checks visible).
  Desktop layout inspected; 390px viewport had 390px document width.
- Browser console had no errors. The SDK logs a pre-existing prepareConnection
  warning before Studio issues a session grant. Its mount-time warmup calls the
  token source before explicit session creation; this update does not suppress it
  or create a session automatically to silence it.
- Historical unpublished commit range scanned with Gitleaks: no leaks found.
  Private recordings, settings, credentials and models are not release inputs.

The earlier intermittent recognition/startup failures remain unresolved. No new
claims of offline certification, fresh-user installation, production reliability,
physical microphone quality or Cloud deployment are made. See the component
verification record for prior explicitly scoped live integration evidence.
