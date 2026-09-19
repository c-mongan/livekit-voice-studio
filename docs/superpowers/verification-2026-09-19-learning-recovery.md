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

## PR review follow-up

The GitHub direct-script smoke check exposed a real import failure: Python puts
`examples/`, rather than the checkout root, on `sys.path` when executing
`examples/minimal_agent.py`. The direct-script entry now adds its own checkout
root, preserving both script and module entry points. A subprocess regression
runs from an unrelated directory with Python environment overrides ignored; it
failed before the fix and passed afterward.

The two Copilot inline comments at PR4 discussions 4053829671 and 4053829679
suggested that saved cloud `low` effort was rejected by endpoint validation.
Reproduction did not support that claim: the validator receives its default
`none`, while saved cloud effort is checked separately against the provider
preset. Four additional regression cases passed before any validator changes;
they cover cloud save/reload, legacy field migration and doctor's saved environment,
including preserved effort. No validator behavior was changed.

Agent quickstart and preview notes now describe local defaults and optional remote
services consistently; the component guide uses the current Connection & AI label.
Full Python suite: 540 passed, 5 integration tests deselected. Ruff, formatting,
mypy and diff checks passed. Independent review found no actionable issues and
reran 31 targeted tests. Frontend and live inference were unchanged and not rerun.
