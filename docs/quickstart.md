# First run on an Apple Silicon Mac

This is the supported fast-voice path. You need Python 3.12, Node.js 22+, `uv`,
Git and Xcode Command Line Tools. The reference machine has 16 GiB RAM.
Allow roughly 12 GiB free for a fresh setup, including download/build caches;
that is a planning margin, not a runtime memory requirement.

## 1. Install the application

From the repository root:

```sh
uv sync --frozen --package livekit-plugins-voicebox \
  --extra dev --extra example --extra agents --extra mlx --python 3.12
npm --prefix web ci
npm --prefix web run build
```

Add `--extra azure` to the same `uv sync` command if you want Azure. Repeating
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
Set `VOICEBOX_TTS_BACKEND=mlx`, the Qwen snapshot path and the Nemotron executable
and model paths from its guide. Stop competing generation jobs, then explicitly
set `VOICEBOX_EXCLUSIVE=1`.

For conversations, add a LiveKit project's URL, API key and secret. Install and
sign in to Copilot CLI, then verify your account offers Luna with low reasoning.
The [agent-provider guide](agent-providers.md) covers supported versions and
restrictions. Azure or OpenAI can be configured instead; there is no automatic
fallback. Codex requires separate restricted-agent consent.

Voice recording and local generated auditions do not need cloud credentials.
Conversations do: local speech does not make remote reasoning or LiveKit offline.

## 5. Record, audition, then talk

```sh
uv run --no-sync python -m examples.studio
```

Open `http://127.0.0.1:8765`. In **Settings & voices**, record 5–30 seconds,
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
