# First run on an Apple Silicon Mac

This is the supported fast-voice path. You need Python 3.12, Node.js 22.12+, `uv`,
Git and Xcode Command Line Tools. The reference machine has 16 GiB RAM.
Allow roughly 12 GiB free for a fresh setup, including download/build caches;
that is a planning margin, not a runtime memory requirement.

## 1. Install the application

Clone the public source, then install its locked dependencies:

```sh
git clone https://github.com/c-mongan/livekit-voice-studio.git
cd livekit-voice-studio
```

```sh
uv sync --frozen --package livekit-plugins-voicebox \
  --extra dev --extra example --extra agents --extra mlx --python 3.12
npm --prefix web ci
npm --prefix web run build
```

Add `--extra azure` to the same `uv sync` command if you want Azure, or if you
plan to run the full automated test suite (which tests all provider factories). Repeating
sync with fewer extras can remove previously installed optional dependencies.

## 2. Reuse or explicitly download Qwen

If you already have the complete Qwen 0.6B snapshot, reuse it. Do not copy the
weights into this repository. Otherwise, the following command explicitly
downloads the pinned public model, approximately 2.4 GB:

```sh
uv run --no-sync hf download mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16 \
  --revision 1eccf1cb2519b5a4e8a95b5f0544f3303568164f
```

Use the returned snapshot directory for `VOICEBOX_MLX_MODEL_PATH`. Normal
Hugging Face snapshot symlinks are supported. The model declares Apache-2.0;
retain its notices if you redistribute it. Studio itself never downloads weights.

## 3. Install local recognition explicitly

Follow [the Nemotron setup](local-stt.md#2-prerequisites-and-explicit-installation).
It builds the pinned CPU runtime and downloads one approximately 700 MB Q8 model.
The runtime and weights have different licenses, documented in that guide.
Studio starts the installed recognizer when needed; do not also start a manual
sidecar on the same port.

On the reference setup, the Python environment occupied about 830 MB, frontend
dependencies 123 MB, and the native recognizer/source/build/model directory
804 MB. Qwen weights and shared package caches are additional. Sizes vary by
platform and filesystem; these are disk observations, not peak RAM measurements.

## 4. Configure this machine

Copy `.env.example` to `.env` only if you do not already have one. Keep it private.
For a new checkout:

```sh
test -e .env || (umask 077; cp .env.example .env)
```

The example selects standalone MLX. Existing `.env` files are never replaced;
older HTTP installations can keep `VOICEBOX_TTS_BACKEND=voicebox`.
Edit these fields in `.env` (replace paths with the real paths
returned by the preceding setup steps):

| Field | Value for this guide |
| --- | --- |
| `VOICEBOX_TTS_BACKEND` | `mlx` |
| `VOICEBOX_MLX_MODEL_PATH` | Absolute path to the complete Qwen snapshot |
| `VOICEBOX_STT_PROVIDER` | `nemotron` |
| `NEMOTRON_SERVER_BINARY` | Absolute path ending in `build/bin/nemo-speech` |
| `NEMOTRON_MODEL_PATH` | Absolute path to the verified `.gguf` model |
| `VOICEBOX_LLM_PROVIDER` | `copilot` |
| `VOICEBOX_EXCLUSIVE` | `1`, after stopping competing generation jobs |

Leave `VOICEBOX_VOICE_BUNDLE` empty until you record and select a voice in the
app. Selecting it saves the choice in the private local library. No external
Voicebox server is required for this path. Do not run `.env` as a shell script.

For conversations, add a LiveKit project's URL, API key and secret. Install and
sign in to Copilot CLI, then verify your account offers Luna with low reasoning.
The [agent-provider guide](agent-providers.md) covers supported versions and
restrictions. Azure or OpenAI can be configured instead; there is no automatic
fallback. Codex requires separate restricted-agent consent.
The preset was tested with the installed account/runtime combination, not every
subscription. If your account lacks it, choose a supported configured Azure or
OpenAI option rather than assuming the preset is universally available.

Voice recording and local generated auditions do not need cloud credentials.
Conversations do: local speech does not make remote reasoning or LiveKit offline.

Check the local setup without starting services or contacting providers:

```sh
./studio doctor
```

A nonzero exit is expected while setup is incomplete. Missing checks include a
concrete next action. Before recording, a missing voice is expected; do not invent
a profile identifier to make that check pass. Once the other configuration checks
pass, continue to the next step to create the voice. Account access and loaded-model
readiness remain explicitly unverified.
See [doctor's checks and limits](launching.md#read-only-first-run-doctor).

## 5. Record, audition, then talk

```sh
./studio start --open
```

The command returns while macOS keeps the server running. Use `./studio status`
to inspect it and `./studio stop` to request graceful shutdown.
See [service ownership and recovery](launching.md).

Open `http://127.0.0.1:8765`. Open **Voice library** and record 5–30 seconds,
verify the exact transcript and confirm permission. Save the voice, then generate
an audition with different text. The original-recording preview is not the clone.
Listen, choose the voice, and start a conversation.

After selecting a voice, this read-only check should report ready:

```sh
uv run --no-sync python -m examples.studio --check
```

First connection prepares the model and reference before the agent is ready.
Subsequent turns reuse that process. End the session before changing voices or
auditioning another one. Voicebox can stay closed throughout this workflow.

## Browser setup and learning tools

Standalone mode uses this project's own Studio server, voice library and Qwen
adapter. The separate Voicebox app and its HTTP service can stay closed. Older
`VOICEBOX_*` variable names are retained for compatibility; they do not imply an
external service is required when `VOICEBOX_TTS_BACKEND=mlx` and a local voice is selected.

Keep source and virtual environments in a normal local development folder rather
than an offloaded cloud-sync folder. If setup checks report slow or unavailable
files, make them locally available and retry. The server returns this diagnostic
after three seconds while sharing any still-running check across requests.

If another Studio fork has incompatible saved settings, select a separate private
`VOICEBOX_LIBRARY_DIR` in `.env`; do not delete the other installation's library.
An explicitly configured authorized `VOICEBOX_VOICE_BUNDLE` can seed the new library
locally. The original recording and settings remain unchanged.

Open **First conversation guide** for read-only setup checks. A green Found result
confirms local configuration, not account access or model synthesis. Missing
LiveKit credentials do not block a local voice sample. **Check microphone** records
up to eight seconds for local replay; nothing is uploaded or saved.

The default view focuses on conversation. **Learn LiveKit** reveals the pipeline,
room state and measured generation delays. Try the [learning exercises](learn-livekit.md).

To compare turn-taking, set `VOICEBOX_TURN_DETECTION=audio-local` in your private
`.env` and restart Studio after ending your session. This selects LiveKit’s bundled
CPU audio turn detector. Keep `vad` to retain the existing behavior. Judge pauses
and interruptions on your microphone; a configured detector is not quality proof.
