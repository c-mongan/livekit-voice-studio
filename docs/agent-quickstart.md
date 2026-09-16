# Agent quickstart

Use this guide when the user asks you to install Voicebox Studio. Read the
repository's applicable instructions first. This guide does not itself authorize
spending, downloads, account creation, permissions or changes outside the app.

## 1. Inspect before changing anything

Supported fast path: macOS Apple Silicon, Python 3.12, Node.js 22.12+, uv, Git,
and Xcode Command Line Tools. Reference hardware has 16 GB RAM. Check available
disk space; plan roughly 12 GiB for a new setup. On unsupported hardware, explain
the limitation rather than promising the local MLX path will work.

Locate an existing checkout before cloning. Inspect its branch, status and local
instructions. Preserve dirty work, .env files, saved settings and voices. Do not
print credentials, reference transcripts or private voice identifiers. Reuse
verified model installations; never copy them into source control.

## 2. Install the source and dependencies

If no checkout exists, clone https://github.com/c-mongan/livekit-voice-studio into
a user-approved project location. From that source root, follow docs/quickstart.md.
The Python wheel alone is not the Studio application.

```sh
uv sync --frozen --package livekit-plugins-voicebox \
  --extra dev --extra example --extra azure --extra agents --extra mlx --python 3.12
npm --prefix web ci
npm --prefix web run build
```

The Azure extra is included for the full test suite; it does not configure Azure
or create resources. Preserve required extras on later syncs. If npm reports a
broken cache, retry using a fresh task-local cache with `--cache PATH`; do not
change global settings or disable lifecycle scripts to hide the failure.

## 3. Configure models and accounts

Follow docs/quickstart.md and docs/local-stt.md exactly for pinned model revisions,
checksums and native build steps. Ask before downloading absent models; state
sizes and destinations. Do not download a larger model or silently substitute one.
Do not start another recognizer on an already occupied port.

Create .env from .env.example only if absent, with private permissions. Set the
standalone backend to mlx and use actual absolute model/runtime paths. Set
VOICEBOX_EXCLUSIVE=1 only after confirming competing inference is stopped.
Do not overwrite an existing .env or source it as a shell script.

Use the user's configured LiveKit project. Hermes is the recommended reasoning
path; Copilot, Codex, Azure OpenAI, and OpenAI remain legacy alternatives. For
Hermes, configure the loopback API base URL (or an explicitly approved HTTPS
origin), server key, and profile only in the private environment. Before launch,
verify that `GET /v1/capabilities` advertises run submission, SSE events, run
status, approval response, and stop for `POST /v1/runs` and its child routes.
Hermes remains responsible for model routing, tools, memory, sessions, permissions,
and action state.

Guide the user through official sign-in and private credential entry; never ask
them to paste secrets into chat. Do not create paid resources automatically.
Read docs/agent-providers.md: available model/effort combinations depend on the
account. Codex's restricted-agent approval must be explicit; do not set its
consent flag on the user's behalf. Never silently switch providers.

“Local speech” is not fully offline: Nemotron recognition and Qwen synthesis run
on the Mac, LiveKit transports room audio, and Hermes sends text to its configured
model. The local path currently supports Apple Silicon and English only. No model
weights or recordings are bundled. Expressive Mode is planned, not shipped, and
will require separate cloud-TTS consent; do not describe local Qwen as expressive.

## 4. Check, launch, and let the user choose a voice

```sh
./studio doctor
./studio start --open
./studio status
```

Doctor is read-only and returns a nonzero status while setup is incomplete.
Resolve its missing configuration checks. A missing voice is expected before
recording; do not invent a profile ID to make it pass. If another checkout owns
the managed service, preserve it and explain the conflict before moving ownership.

In Voice library, let the user record or choose a voice they are authorized to
use, verify its transcript, audition generated speech and select it. Do not grant
microphone access or attest to voice consent for them. Keep the service private
and loopback-bound; do not publish a tunnel as an installation shortcut.
Hermes approval cards require an owner click/tap. Never approve from spoken input,
and never enter a password, verification code, payment value, or secret through
the room.

## 5. Verify and hand back a usable app

Run the offline checks in docs/evaluation.md. With authorization for configured
service usage, test a short text reply, Stop reply, another reply and End session.
Confirm the rendered UI and return to Ready; a server process alone is not proof.
Synthetic speech is optional and must use an authorized fixture and the documented
live-test opt-in. Do not record personal conversations for debugging.

The Hermes vertical-slice test is intentionally skipped unless
`HERMES_STUDIO_LIVE=1` and every LiveKit credential, Hermes setting, local model
path, authorized voice bundle, and synthetic audio fixture is supplied explicitly.
Do not enable or run it without separate authorization for real LiveKit and Hermes
usage. When authorized, run only the named test and retain its sanitized JSON
stdout; it covers one room, a harmless fixture read, denial through the session
owner-only approval RPC, non-final assistant transcription as direct streaming
evidence, interruption, continuity, timing gates, and ownership drain:

```sh
uv run pytest tests/integration/test_hermes_studio_live.py -m integration -v -s
```

Stopping a reply proves that speech stopped and requests Hermes cancellation. It
does not undo an action that already completed; report those outcomes separately.

Report separately: dependencies installed, configuration checked, account access
verified, live audio tested, and human listening still needed. Include exact
remaining blockers. Do not claim a successful install when only doctor passed.
Leave the app ready and the user's unrelated work intact. Do not publish a package,
change repository visibility, install host hooks or edit global agent settings.
