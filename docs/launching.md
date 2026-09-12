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
