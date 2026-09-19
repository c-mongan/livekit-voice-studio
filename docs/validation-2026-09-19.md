# Fresh installation and reliability check — 2026-09-19

Source tested: `aff3442c448c513485450a8317e2c4a2de41eec3` (merged PR #4).

## Clean source installation

A fresh shallow clone of the public repository was installed in a new disposable
directory on an external APFS drive. It did not receive the working checkout's
`.env`, voice library, virtual environment or node_modules. Python 3.12 and Node
were already installed. Package caches could be reused; this is not a clean OS or
independent hardware test. No new speech or reasoning models were downloaded.

The documented frozen uv installation with dev/example/agents/mlx/azure extras
succeeded. `npm --prefix web ci` succeeded and reported zero known vulnerabilities.
The Python environment used about 813 MB and frontend dependencies about 123 MB;
shared caches, native recognition runtime and model weights are additional.

- Python: 540 passed; 5 integration tests deselected.
- Frontend: 216 passed across 18 files.
- TypeScript and production Vite build passed; existing bundle-size warning remains.
- Offline doctor with an explicitly isolated, nonexistent library returned exit 1
  for missing exclusive-use acknowledgement, Qwen, recognition and voice setup.
  Configuration checks passed; account and inference checks remained unverified.
  Doctor created no library and the clone still had no `.env`.

This establishes reproducible dependency installation on the maintainer's Mac.
It does not establish a new user's first successful audition or conversation.
Use the independent tester checklist in [the demo guide](demo.md).

## Spoken-input reliability reproduction

After installation/testing finished, the existing working Studio was idle/ready
and reported local LiveKit, Ollama qwen3:1.7b, Nemotron and Qwen. The opt-in spoken
harness attempted five turns with five-second pauses using the existing short
synthetic fixture. It captured no physical microphone.

The run failed before the first reply, with the server's fixed diagnostic:
“Local speech input buffer filled up. End the session and reduce system load.”
No reply-stage metrics were reported. The harness timed out and performed its
session cleanup. This failed run does not invalidate prior successful runs, but
it confirms that the current setup cannot be described as reliably conversational
under all desktop loads.

The 16 GiB Mac reported about 21 GiB swap in use during diagnosis. This is evidence
of substantial memory pressure, not proof that swapping alone caused the failure.
Other user workloads were not stopped. The recognizer's bounded audio queue can
fill while its sender waits for connection setup or finalization; the current
fixed error does not identify which phase was stalled. The older intermittent
recognition error therefore remains unresolved, rather than being re-labelled as
this exact cause.

No production timeouts or buffer limits were increased and no queued audio was
silently dropped. Next controlled comparison: same fixture and route after the
operator closes unnecessary memory-heavy workloads; compare one typed reply with
one spoken reply, then repeat with pauses. Capture the phase of the recognition
stall before choosing a transport or buffering change. A successful retry alone
is not a fix.

## Typed-input comparison

A separate browser session on the same local route sent only the synthetic prompt
“Reply with just hello.” with the microphone off. The transcript displayed “Hello.”
The server reported 5.757 seconds to the first LLM token, 0.905 seconds to the first
TTS frame and 1.040 seconds of generated audio. These are individual stage metrics,
not end-to-end latency or proof of audible browser playback.

Ending the session returned Studio to idle/ready. This demonstrates that typed
input, local reasoning and speech generation completed while the spoken-input
path failed in the earlier run; it does not identify the recognition stall's
root cause. The screenshots in the demo guide contain only this synthetic exchange
or an empty transcript. The reply screenshot was captured after session cleanup.

## Follow-up CI limitation

PR #5's initial hosted frontend jobs stopped at `npm audit`, before frontend
checks ran. A local reproduction showed HTTP 503 from npm's bulk advisory
endpoint, followed by HTTP 400 from its deprecated quick-audit fallback. The
earlier clean installation reported zero known vulnerabilities; this later audit
did not complete and cannot establish current advisory status. The security gate
remains enabled. Re-run failed CI jobs after the advisory service recovers before
merging. No lockfile was regenerated to work around a service failure.
