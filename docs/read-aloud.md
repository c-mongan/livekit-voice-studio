# Read an existing reply aloud

This is a small local narrator, **not a voice-controlled coding agent**. It uses
the voice selected in Studio and never requests another LLM answer.

## Try it manually first

Install the `playback` extra alongside your existing extras, then start Studio:

```sh
uv sync --frozen --package livekit-plugins-voicebox \
  --extra dev --extra example --extra agents --extra mlx --extra playback --python 3.12
./studio start
printf '%s' 'This is an existing reply being read locally.' | ./studio speak
```

Keep `--extra azure` in that sync command if you use Azure. No new voice weights
are required. Playback uses your default output device and system volume, not
your microphone.

```sh
./studio stop-speaking
./studio mute
./studio unmute
```

Mute persists for future read-aloud requests and requests an immediate stop of
the current narration. It does not mute normal Studio conversations.
Stop stops the current narration without disabling future ones.

## What is spoken

The narrator removes fenced code blocks, simplifies Markdown and reads at most
300 characters of prose. Long replies end with “The reply continues in your
agent.” This is an **explicit excerpt**, not an LLM-generated summary or a promise
to read an entire code review.

This first version is preview-style playback: it collects the bounded generated
clip before playing it, and each job prepares its own voice worker. It reuses
cached weights but does not keep a model warm between coding notifications.
Expect more startup delay than a turn inside an already-warmed conversation.

Input is bounded to 64 KiB. No shell command, path or tool call from the reply is
executed. URLs are not fetched. Code-only or empty input is an error rather than
an invented spoken response.

## Ownership and privacy

Studio must already be running, with an authorized voice selected. Narration
does not automatically start services, download models or switch providers.
It uses the same local Qwen streaming path as auditions.

The broker holds admission through both generation and playback, so a new room
cannot overlap narration. A private local socket provides Stop; no arbitrary PID
is killed. Concurrent notifications are rejected rather than queued or mixed.
Crashes and lost connections remain bounded by Studio's existing ownership
timeouts. An unexpected client exit can temporarily leave admission held until
its heartbeat expires.

Generated WAV/PCM is kept in memory, played directly and discarded; the narrator
does not create audio files. It does not save reply text. Local speech consumes
compute and electricity, not extra LLM API tokens. The agent that originally
wrote the reply still has its normal usage and billing.

Only enable automatic narration where speaking the content aloud is appropriate.
Private code or secrets can appear in an agent's reply; this is not a content
redaction or speaker-authentication system.

## Opt in for one Codex invocation

The installed Codex CLI **0.153.4** supports a legacy completed-turn notification:

```sh
codex -c 'notify=["/absolute/path/to/voicebox-checkout/studio","codex-notify"]'
```

Run that from your normal coding directory, with a trusted absolute path to
this checkout's launcher. It leaves your existing coding permissions unchanged.
We do not modify global Codex configuration. Do not put this in a project's
`.codex/config.toml`: this version explicitly rejects `notify` in project-local
configuration.

Codex appends a JSON argument containing the already-generated reply. The bridge
reads only `last-assistant-message`; it does not read the supplied working
directory, input messages or agent history. Null replies are ignored. Hashes of
the most recent 64 `(thread-id, turn-id)` pairs prevent duplicate attempts,
including replay after a failed or muted attempt. Receipts contain no reply text.

Notifications run as trusted host commands, **not sandboxed model tool calls**.
Codex passes the full notification in process arguments, where local process
inspection may expose it. That happens before this bridge selects an excerpt.
Use this opt-in only where that boundary and audible output are acceptable.

Codex does not wait for playback and disconnects notification stdout/stderr.
Inspect the last bounded, text-free outcome with:

```sh
./studio read-aloud-status
```

Busy notifications are dropped, not queued. Use `./studio speak` for an explicit
manual retry. Stop/mute remain available through the same private controls.

The configuration and payload were verified against
[Codex v0.153.4 notification code](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/hooks/src/legacy_notify.rs)
and its
[integration test](https://github.com/openai/codex/blob/3d2ee51ca2d5db578f328aa75e20aa22c0197c9a/codex-rs/core/tests/suite/user_notification.rs).
This is a legacy interface; revalidate after upgrading Codex.

The exact notification payload was exercised through real local synthesis and
playback, then replayed to verify duplicate suppression. No extra Codex model
turn was submitted just for this test. Automatic Copilot reply capture and a
general Jarvis-style agent are not implemented; manual piping works for any
already-generated reply.
