# Choose where each component runs

Studio lets you choose the conversation transport, speech recognition and reasoning
provider independently. Cloned voice generation stays on your Apple Silicon Mac.
The **How it works** view explains the selected route; it is optional.

| Component | Local choice | Remote choice |
| --- | --- | --- |
| LiveKit connection | LiveKit development server on this computer | LiveKit Cloud or your own configured server |
| Speech recognition | Nemotron | Configured Azure or OpenAI speech |
| Reasoning | Ollama, or a local OpenAI-compatible endpoint | Copilot, restricted Codex, Azure, OpenAI, or a custom endpoint |
| Cloned voice | Qwen MLX and your private reference | Not moved by these settings |

New voice libraries default to local LiveKit, Nemotron, Ollama and Qwen. Existing
libraries keep their previous reasoning choice and configured LiveKit connection.
Settings changes take effect on the next session, and are locked while a session
or voice sample is running. Missing services produce errors; Studio never silently
switches to a cloud provider or downloads a model.

## Local LiveKit

On macOS, explicitly install the open-source server, then run it in a terminal:

```sh
brew install livekit
livekit-server --dev --bind 127.0.0.1
```

In **Settings → Providers**, choose the local LiveKit option. Studio uses
`ws://127.0.0.1:7880` and LiveKit's public development credentials (`devkey` /
`secret`). They are for same-machine development only. Keep this terminal running;
Ctrl-C stops the server. The Studio server and LiveKit server are separate processes.

For LiveKit Cloud or a separately hosted server, put its credentials in the private
`.env` and restart Studio to load them:

```dotenv
LIVEKIT_URL=wss://your-server.example
LIVEKIT_API_KEY=replace-locally
LIVEKIT_API_SECRET=replace-locally
```

Choose the configured-server option in Settings. Switching to local mode does not
rewrite those credentials, and switching back restores the configured connection.
Cloud and self-hosted endpoints use the same option because their connection
configuration is the same. Secrets are never entered into browser settings.

See [LiveKit's local guide](https://docs.livekit.io/transport/self-hosting/local/).
Public self-hosting is a separate deployment task involving TLS, firewall rules,
network reachability and often TURN relay configuration. Development credentials
and this loopback setup are not a public deployment recipe. See the
[production deployment guide](https://docs.livekit.io/transport/self-hosting/deployment/).

## Local reasoning with Ollama

Install [Ollama](https://ollama.com/download), start its local service, and explicitly
download a small chat model:

```sh
ollama pull qwen3:1.7b
```

The default model is a starting point for a 16 GB Apple Silicon Mac, not a claim
that it matches a larger cloud model. Its download is about 1.4 GB; runtime memory
also includes the context cache and all other speech models.

Choose **Ollama** in Settings. Enter the installed model name and the local API
address, normally `http://127.0.0.1:11434/v1`. Ollama connections in this option must
be loopback addresses. Use a different port if you deliberately run another local
instance. Studio checks the selected model at session startup; an embedding model
cannot replace a chat model.

Ollama is supported through LiveKit's existing
[OpenAI plugin](https://docs.livekit.io/agents/models/llm/ollama/). No separate model
runner is embedded in Studio. End the Studio session before stopping Ollama.

## Bring another compatible model server

Choose **OpenAI-compatible**, enter its model identifier and chat API base URL
(including `/v1` if the server expects it). A local server can use HTTP; use HTTPS
for a remote service. URLs cannot contain credentials, query strings or fragments.
If authentication is required, set `VOICEBOX_CUSTOM_LLM_API_KEY` in the private
`.env` and restart Studio. This key is separate from `OPENAI_API_KEY`; Studio does
not forward your OpenAI credential to arbitrary endpoints.

Compatibility means the endpoint exposes the selected identifier from `/models`
and supports streaming OpenAI-style chat completions. Requests do not follow
redirects or inherit environment proxy settings.
Different model servers may handle tools, errors and streaming differently. The
custom choice does not certify every server as compatible. Locality describes the
configured address, not a guarantee that a proxy or model runner never forwards
requests elsewhere.

## Useful combinations

- **All local:** local LiveKit + Nemotron + Ollama + Qwen.
- **Cloud reasoning:** local LiveKit + Nemotron + Copilot/OpenAI/Azure + Qwen.
- **Managed transport:** configured LiveKit Cloud + Nemotron + Ollama + Qwen.
- **Custom:** select your own LiveKit server and compatible model endpoint.

The separate [cloud learning agent](cloud-learning.md) is still a different example;
changing Studio's LiveKit connection does not deploy the Qwen worker to Cloud.
Models and services must be prepared before attempting disconnected operation.
A successful local-route test is not proof that all network access was disabled.

## Memory and compute

LLM weights, its context cache, Qwen voice generation, the recognizer and your other
applications share system memory. Start with a small quantized chat model and a
short conversation. Compare time to first reply and interruption behavior before
trying a larger model. A large model that fits on disk may still cause swapping
or unacceptable voice latency.

On macOS you can inspect your machine without uploading its details:

```sh
system_profiler SPHardwareDataType SPDisplaysDataType
memory_pressure
sysctl vm.swapusage
ollama ps
```

Hardware summaries can contain serial numbers; do not paste the complete output
into public issues. RSS process measurements do not fully represent shared GPU
memory, and a brief test is not a sustained thermal or throughput benchmark.
