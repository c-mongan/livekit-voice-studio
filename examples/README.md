# One local Voicebox-backed room

For the local voice-and-text UI, use [Studio](../README.md#run-studio) instead.
Studio owns its agent process; do not also run the standalone worker below.

Prerequisites: existing Voicebox, cached and warmed Qwen TTS 0.6B, an authorized
cloned profile with reference samples, exclusive backend use, and your standard
`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`. The default OpenAI example
also needs `OPENAI_API_KEY`; the Azure configuration below does not.
Keep values in your shell or secret manager, never in source control.

```sh
uv sync --frozen --package livekit-plugins-voicebox --extra dev --extra example --python 3.12
cp .env.example .env
# Edit .env: select an authorized profile, enter credentials locally,
# and confirm exclusive use only after stopping other consumers.
uv run --no-sync python examples/minimal_agent.py check
uv run --no-sync python examples/minimal_agent.py dev --no-reload --log-level info
```

The example loads only this workspace's `.env`, never another project's file.
Existing process variables win. `.env` is ignored by Git. If a local `.env`
already exists, keep it rather than copying the template over it.
`check` is read-only: it lists missing variable **names**, verifies profile and
cached/loaded model status, and rejects tracked work. It never prints secrets,
downloads/loads models, generates speech, or connects to hosted services.
A successful check does not verify credentials with the remote services.

## Using existing Azure resources

```sh
uv sync --frozen --package livekit-plugins-voicebox \
  --extra dev --extra example --extra azure --python 3.12
```

In your local `.env`, set `VOICEBOX_AI_PROVIDER=azure` and fill the nonsecret
`AZURE_*` resource fields from `.env.example`. This example supports an existing
Azure OpenAI deployment and Azure Speech resource in the configured resource group.
Set `AZURE_OPENAI_ENDPOINT` to the resource's actual endpoint, not an assumed hostname.
It defaults to API version `2024-10-21` and the `gpt-4.1-nano` model label;
the deployment name is configured separately.

Sign in with `az login`. At session creation, the example retrieves the two
resource keys through the CLI, captures them in memory, and passes them to the
official LiveKit OpenAI/Azure plugins. Keys are not printed, passed as CLI
arguments, stored in `.env`, or committed. The CLI identity needs permission to
list keys for those resources; subscription read access alone is insufficient.
Failures are explicit and do not silently switch providers.

This key-based path supports existing regional endpoints, which do not support
Entra token authentication. It does not create custom subdomains, change RBAC,
create deployments, or rotate keys. Use workload identity with supported custom
endpoints for a separately designed hosted deployment; do not run this
development CLI credential flow as a production credential service.

Azure Speech receives microphone audio, Azure OpenAI receives conversation text,
Voicebox produces speech locally, and generated audio travels through LiveKit.
The worker's local HTTP health endpoint is bound to loopback only.
Session recording is explicitly disabled, overriding the project's recording
default. Interruption detection uses the local VAD rather than the SDK's default
cloud adaptive detector. Other upstream application logs remain outside this setting.
Do not run desktop generation or another worker while this one uses Voicebox.

Join/dispatch a room using your normal LiveKit client or Agents playground.
The worker admits only one room for its lifetime; restart it for a different
room. Do not start another worker or generate in the Voicebox desktop UI.

To explicitly warm the already cached 0.6B model with one local synthetic line:

```sh
VOICEBOX_INTEGRATION=1 VOICEBOX_TEST_PROFILE="$VOICEBOX_PROFILE" \
  uv run --no-sync pytest tests/integration/test_voicebox_live.py -q -s
```

The pytest smoke test uses process variables, not the example's `.env` loader;
set `VOICEBOX_TEST_PROFILE` explicitly to the authorized name/ID when using it.

This may replace the currently loaded 1.7B model in memory through Voicebox's
normal generation route. It does not change persistent configuration or call
download/load management routes. The example refuses an unloaded model so a
first room turn does not silently become a cold-load benchmark.

By default the example uses OpenAI transcription and an OpenAI LLM, not OpenAI TTS.
With `VOICEBOX_AI_PROVIDER=azure`, it uses Azure Speech STT and Azure OpenAI instead.
Silero VAD loads the ONNX resource bundled with its declared plugin dependency;
there is no runtime model-download command. Voicebox alone performs TTS.
The framework natively adapts this non-streaming provider at sentence boundaries.

Room audio is transmitted through your LiveKit deployment. Hosted STT receives
microphone audio and the LLM receives conversation text. Reference recordings
remain with Voicebox. Disable unwanted SDK tracing/recording and never set
`LK_DUMP_TTS=1` when recordings are not authorized.
