# Task 3 Report: Hermes settings and readiness

## Status

Complete. Studio can select Hermes as its reasoning provider, persist and validate the non-secret Hermes profile/origin settings, preflight the Hermes Runs API before use, report the Hermes control-plane location accurately, and expose the configuration in the browser without accepting or returning the API key.

## Implementation

### Settings and migration

- Added the fixed Hermes preset: `profile-default` with reasoning effort `none`.
- Added persisted non-secret defaults:
  - `hermesProfile: "default"`
  - `hermesBaseUrl: "http://127.0.0.1:8642"`
- Migrated existing settings with `setdefault` so older valid settings remain usable.
- Validated profile names against `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`.
- Reused `HermesConfig` validation for loopback HTTP and explicit HTTPS origins, including rejection of credentials, paths, queries, fragments, malformed ports, and insecure remote HTTP.
- Applied only profile and base URL to the worker environment. `HERMES_API_SERVER_KEY` remains server-managed and is neither persisted nor overwritten.
- Kept Studio Doctor's static settings contract aligned with runtime settings and Hermes readiness requirements.

### Provider construction and readiness

- Added `hermes` to accepted reasoning providers without coupling it to the selected speech provider; Nemotron remains independently selectable.
- Constructed `HermesRunsClient` from `HERMES_API_SERVER_KEY`, `HERMES_API_BASE_URL`, `HERMES_PROFILE`, and `HERMES_VOICE_SESSION_ID`.
- Required a capability `preflight()` before returning `HermesLLM` and closed the credential-bearing client on construction failure.
- Added a fail-closed default approval callback: approval requests are rejected unless a caller explicitly supplies an approval handler.
- Added `HermesLLM.aclose()` ownership cleanup so closing the LiveKit model also closes its Hermes client.
- Added readiness checks for the API key, profile/origin validation, and capability preflight without starting a run. The readiness client is always closed.
- Propagated the Studio-owned session ID to the worker as `HERMES_VOICE_SESSION_ID`.

### Status and browser UI

- Added Hermes as the first reasoning-provider option.
- Added Hermes status output with fixed model/effort, selected profile, and a `local` value that describes only the Hermes control-plane location.
- Updated pipeline copy to say “Local/Remote Hermes control plane” rather than implying local model inference.
- Added Hermes-only profile and base URL fields and the required explanatory copy.
- Added no API-key field; provider option and settings responses do not expose the server key.

## TDD evidence

The interrupted implementation was preserved and completed with focused regression cycles:

- Existing settings migration, fixed preset, profile/origin validation, provider construction, preflight, status, UI, and credential-boundary tests were exercised.
- A combined focused run exposed cross-test environment leakage in `test_apply_environment_never_persists_or_overwrites_hermes_api_key`; the test was isolated with `monkeypatch`, after which the Task 3 server set passed (`81 passed`).
- Session-ID propagation was first asserted against the spawned worker environment and failed because `HERMES_VOICE_SESSION_ID` was absent; adding the owned session ID made the focused test pass (`1 passed`).
- Studio Doctor contract tests initially failed for the new Hermes settings/readiness path; after aligning its preset, migration, origin validation, environment projection, and readiness checks, the focused set passed (`5 passed`).
- Client lifecycle coverage was added to verify that closing `HermesLLM` closes the credential-bearing Runs client.

## Verification

Fresh verification on the final implementation before this report:

- `uv run pytest -q` — **537 passed, 5 skipped**.
- `npm --prefix web test` — **169 passed** across 12 files.
- `uv run ruff check .` — **passed**.
- `uv run mypy` — **passed**, 25 source files clean.
- `npm --prefix web run build` — **passed**; Vite retained the existing approximately 773 kB chunk-size warning.
- `git diff --check` — **passed**.

A final bounded sanity run on unchanged source also passed:

- `uv run pytest tests/test_studio_library.py tests/test_provider_choices.py tests/test_studio.py tests/test_hermes_llm.py tests/test_studio_doctor.py -q` — **168 passed**.
- `npm --prefix web test -- src/StudioSettings.test.tsx src/App.test.tsx` — **55 passed** across 2 files.
- `git diff --check` — **passed**.

## Self-review

- Scope: all changed production, test, doctor, and browser files directly support Task 3 settings, provider readiness/lifecycle, session correlation, status truthfulness, or API-key isolation.
- Secrets: the API key is read only from the server/worker environment; it is not added to the settings schema, browser form, settings payload, provider metadata, status response, or persisted JSON.
- Fail-closed behavior: unsupported providers remain errors, remote plain HTTP is rejected, preflight must succeed before model use, approvals are rejected unless explicitly wired, and clients are closed on failure and shutdown.
- Compatibility: existing settings migrate in place; non-Hermes provider presets and independently selected STT behavior remain unchanged.
- Residual note: the production frontend build reports the pre-existing large-chunk warning; it does not fail the build and is unrelated to this task.

No blocking correctness, security-boundary, or scope concern remains from the reviewed diff and exercised checks.
