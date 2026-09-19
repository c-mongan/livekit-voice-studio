> Latest evidence: [2026-09-19 clean installation and reliability check](validation-2026-09-19.md). Older counts and timings below are historical.

# Standalone voice validation — 14 September 2026

## Decision

Continue this repository as the standalone voice application. Keep cooking and
other experiments secondary. This pass starts from GitHub main `2b80490`, not
the later local cooking/consolidation worktree.

The working local installation is a close relative of this source, with private
phone-access changes. Its configuration and saved voices were reused in process
for tests; no credentials, reference audio or private settings were copied into
the checkout. The original managed installation now includes the reviewed startup diagnostics
and conversation instructions. Existing private phone-access edits are preserved.

## Observed live results

Hardware: Apple M4, 16 GiB unified memory. Speech: local Nemotron and Qwen 0.6B
through MLX Audio 0.5.3. Transport: configured LiveKit service. Reasoning:
Copilot Luna/low for the full spoken pipeline.

| Check | Result |
| --- | --- |
| First installed-app spoken attempt | Failed during Qwen preparation; no latency sample. Safe cleanup returned idle. |
| Isolated offline model load | 24.5 seconds including 3.43 seconds importing MLX Audio. No inference. |
| Installed-app spoken recheck | Passed; first nonzero reply audio 4.168 seconds after speech end. |
| Updated GitHub checkout, three spoken turns | Passed: 3.451, 2.859, 3.820 seconds; median 3.451 seconds. |
| Browser controls | Start and send, stop reply, successful next reply, end, and return to idle passed; no page errors observed. |
| Copilot synthetic two-turn memory | Correct; first text 1.895 and 1.494 seconds. |
| Restricted Codex synthetic two-turn memory | Correct; first text 4.068 and 1.004 seconds. Preflight confirmed zero external tools and disabled command networking. |

Spoken input was a short synthetic operating-system voice fixture. Response
recording was disabled. Nonzero audio and correct synthetic-word recall do not
establish human microphone performance, naturalness or broad reasoning quality.
These few samples are not a latency guarantee or a controlled provider ranking.
Codex was checked as a reasoning adapter, not as a complete spoken pipeline.

The first startup failure remains part of the evidence. Isolated loading and
later runs succeeded, but the exact cause of the original timeout was not proven.
No timeout or drain boundary was weakened to make the checks pass.

## Changes

- Report fixed, authenticated startup stages to the conversation notice.
- Preserve the current stage in startup timeout messages; distinguish startup
  timeout, lost browser heartbeat and session time limits.
- Reject unknown progress text and prevent late progress from hiding cleanup.
- Clear startup progress when the worker becomes ready.
- Add an opt-in real two-turn reasoning check and document provider consent.

## Installation and automated checks

A fresh Python environment installed the frozen dependencies from cached
packages. A fresh frontend install succeeded using a task-local npm cache;
the host's default cache path was broken. No global cache/configuration was
changed, and no speech models were downloaded.

The final modified source passed all 477 offline Python tests, with five live
tests deselected. The installed copy also passed all 48 focused broker, worker,
and startup tests, including its existing private-access tests. Ruff and mypy
passed after the conversation changes. A frozen dependency dry run checked 109
packages and required no changes.

All 167 frontend tests, TypeScript/build, Ruff, formatting, mypy, and Python
wheel/source builds passed. Existing GitHub main's CI succeeded; hosted CI has
not run for this local change.

The MIT license and existing upstream notices are present. Gitleaks found no
secrets in the available four-commit Git history. This is not a complete
third-party/model-license audit or proof that future releases contain no secrets.

## Simulated conversation follow-up

A further baseline spoken run failed before reply audio, with a generic provider
error; the failing provider was not identified. Do not omit that failure.
After installing the diagnostics and conversation instructions, a real spoken
run completed the full answer "Two plus two equals four." (7.879 seconds from
speech end). A synthetic spoken interruption during the next reply changed the
question; the old reply ended at "One through thirty are the counting" and the
agent answered "Six." Nonzero remote audio accompanied the follow-up. This
establishes functional spoken interruption and recovery, not acoustic stop latency.

A six-turn Copilot replay of the casual-chat prompts avoided repeated greetings
and premature goodbye. It remains a small subjective sample, not a quality
benchmark. Brief acknowledgements still receive short acknowledgements.

Runtime errors now name recognition or synthesis where the provider is known,
using fixed text rather than exposing raw provider bodies. Interruption thresholds
were not changed because the deliberate-interruption check worked.


A second fresh session after the changes passed two spoken turns at 6.376 and
4.313 seconds (median 5.345), then returned to idle. Across this follow-up,
one baseline run failed and two updated sessions passed. This is encouraging
but does not resolve the unidentified failure or establish stable cold-start
reliability. The first full reply in the interruption session took 7.879 seconds;
responsiveness remains a priority.

## Stage-timing follow-up

Three additional spoken turns passed at 4.923, 3.357, and 3.084 seconds.
The corresponding reasoning first-token times were 2.893, 1.250, and 1.552
seconds; local first-audio-frame times were 0.587, 0.475, and 0.463 seconds.
Final transcript delay stayed between 0.652 and 0.695 seconds. These metrics
overlap and must not be added as if they are an exact end-to-end breakdown.
Reasoning was the largest measured component in this sample. No model, voice,
interruption threshold, or timeout was changed to produce these results.
The earlier unidentified error was not reproduced in this three-turn run.

A separate two-turn session with 20 seconds of idle input between turns passed
at 3.504 and 2.888 seconds, then returned to Ready. Five additional turns across
two sessions passed without reproducing the unidentified error. This is not proof
that the intermittent failure is fixed. All 477 offline tests and 48 installed
focused tests passed; the seven evaluator tests also passed after ensuring the
new diagnostic records cannot be mistaken for latency samples.

## First-time configuration audit

The real offline checker was exercised against three isolated configurations:
no environment file, the untouched example, and the documented standalone MLX
selection with built frontend assets. No maintainer credentials, saved voices, or
model paths were inherited. All three returned the expected incomplete status;
no private library was created or modified. Missing model, recognizer, CLI,
LiveKit configuration, and voice checks included next actions.

All 61 doctor tests passed, including the real shell launcher with an empty
configuration and the cold-process checks that forbid network, writes, and child
processes. The guide now uses the current Voice library label, explicitly lists
the standalone configuration fields, explains the expected missing-voice check,
and invokes native setup through the installed uv Python environment.

This verifies first-time configuration diagnostics on the maintainer's machine,
not a new person's account entitlement or a clean operating-system installation.

## Current developer-preview status

The review branch was committed and passed GitHub CI before this final security
pass. A dependency audit identified GHSA-6w46-j5rx-g56g in the old development
pytest pin; pytest 9.0.3 and compatible pytest-asyncio 1.4.0 resolve it. The
updated installed Python dependency audit reports no known vulnerabilities,
except that the unpublished local project cannot be matched to a PyPI advisory.
The npm audit reports zero known vulnerabilities. CI now repeats both checks.

A fresh source directory received a new Python environment and frontend install,
without a .env or saved voice library. A broken/reused npm cache caused an initial
install stall and a missing esbuild installer; a new cache installed successfully.
The frontend's 167 tests, type checks, and build passed. The initial Python run
without the optional Azure extra failed three Azure factory tests; the testing
instructions now explicitly require that extra even for Copilot users. With the
full documented developer extras installed, all 477 tests passed in the fresh
environment. The read-only checker returned the expected missing-configuration
status without creating a library.

A five-turn synthetic spoken run with five-second pauses passed at 4.226, 3.288,
3.019, 3.286 and 3.422 seconds, median 3.288 seconds, then returned idle. This
extends the prior interruption and twenty-second-pause checks; it does not prove
that the earlier intermittent failure is fixed.

License inventory found metadata or notice files for all 109 installed Python
distributions and license metadata for all 208 npm lockfile entries. This is
traceability, not an exhaustive legal clearance. No model weights are shipped.

## Remaining acceptance boundaries

Human microphone/listening and independent hardware/account validation cannot be
substituted by agent-run tests. Preserve these limitations and the unresolved
intermittent error in docs/preview-release.md. This is a developer preview, not a
stable release. Final hosted CI must pass the exact security-update commit.
Repository visibility changes and a public release remain separate publication
actions; source preparation does not perform them.
