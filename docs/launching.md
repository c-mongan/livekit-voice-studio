# Start and stop Studio

On macOS, use the launcher from your installed checkout:

```sh
./studio start --open
./studio status
./studio stop
```

**These commands finish.** The server is a separate, user-owned macOS service,
not a command that Copilot has to keep running. Closing the chat or its command
details does not stop Studio. Stop it explicitly when finished.

`make studio`, `make studio-status` and `make studio-stop` are equivalent shortcuts.
For debugging, `make studio-foreground` still runs the server in your terminal;
that particular command intentionally stays running.

## What the states mean

| State | Meaning |
| --- | --- |
| Running | HTTP server responds; its activity/ready fields show whether a new session can start |
| Starting | Process exists but startup has not yet produced a usable HTTP response |
| Stopping | Graceful shutdown was requested; inference may still be draining |
| Stopped | No managed server process or listener remains |
| Failed / unresponsive | Inspect the private logs; do not repeatedly restart |
| Unmanaged | Something else owns port 8765; the launcher will not kill or adopt it |

`./studio status --json` returns bounded, nonsecret state for scripts.
Start is idempotent: running it twice does not create two servers.
Stop returns after requesting shutdown; use Status to confirm drain completed.

The launcher does not install Python dependencies, build the UI, download models,
start a conversation or generate a voice. Complete [first-run setup](quickstart.md)
before starting it.

## Read-only first-run doctor

After installing the Python environment, run:

```sh
./studio doctor
./studio doctor --json
```

Both commands finish without starting/stopping services, making network or account
calls, loading models, generating audio, or downloading anything. Doctor does not
initialize a voice library or change settings. It also runs on Linux to explain
configuration requirements; this does not add Linux support for the MLX backend
or managed launcher. A missing `.venv` is reported by the launcher; install the
environment using [first-run setup](quickstart.md) first.

Doctor checks installed package metadata, the selected backend's hardware
requirements, the frontend build entry point, the local Qwen snapshot structure,
native Nemotron executable/layout and model size, LiveKit fields, selected
reasoning configuration, exclusive-use acknowledgement, and the selected voice.
It validates bounded file structure and reference checksums without importing
native audio runtimes. Audio decoding and inference remain explicitly unverified.
It uses the worktree `.env` without executing it or printing values. Existing
process variables override `.env`; valid saved library settings override provider
and voice selections just as in the existing `--check` path. No saved settings
means environment configuration is checked. Invalid or unreadable saved settings
are an explicit failure, never a silent fallback; restore the private settings
from a known-good backup rather than deleting the library.

The JSON contract is `{"version":1,"checks":[...]}`. Each check has `id`, `status`,
`message`, and `action` strings. Status is one of:

| Status | Meaning |
| --- | --- |
| `pass` | The stated local configuration check passed |
| `missing` | A dependency, file, selection, or valid configuration is missing; follow `action` |
| `unverified` | Deliberately not established by this offline check |

Exit **1** means at least one `missing` check. Exit **0** means configuration is
ready for the next explicit step, **not** that credentials work or models are
loaded. An installed CLI is not proof of sign-in, account entitlement, or access
to the exact model/reasoning preset. Native model checksum verification remains
in explicit setup/start; weight integrity, runtime compatibility, available RAM,
LiveKit connectivity, and audible output remain unverified.

A fresh installation normally reports a missing voice. You can still start the
UI to record and select an authorized voice; conversations additionally require
LiveKit and a reasoning provider. Run doctor again afterward. It never substitutes
for an explicitly authorized audition or connection. Unlike the existing
`python -m examples.studio --check`, doctor never probes Voicebox health or model
state. Output excludes credential values, private paths, and voice/profile names.

## Ownership and recovery

The launcher uses your login's `launchd` domain, without `sudo`. Its private
configuration and logs live in:

```text
~/.local/share/voicebox-studio/service/
```

This is deliberately **not** `~/Library/LaunchAgents`: there is no automatic
start at login. The job has no KeepAlive restart policy. A crash must not trigger
blind retries while inference completion is unknown.

One checkout owns the service. Stop it from that checkout before moving to
another installation. Do not delete or archive an installation while its service
is running; its Python environment and source are still needed.

Credentials remain in your private `.env`, not the service plist. Application
logs rotate at 256 KiB with one backup; the small bootstrap log is reset on an
explicit new start. Logs are private and should still be reviewed before sharing.

The existing durable inference marker remains authoritative. If recovery is
required, first confirm that the relevant backend really has stopped or restarted.
Only then use:

```sh
./studio start --confirm-backend-restarted
```

Never use that flag merely because a health endpoint responds.

## Platforms

Managed launch is currently macOS-only, matching the tested fast Qwen setup.
Linux users can run `make studio-foreground` under their own process manager.
There is no Windows launcher or claim of tested cross-platform voice performance.
