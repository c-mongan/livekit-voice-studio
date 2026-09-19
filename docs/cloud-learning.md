# A separate LiveKit Cloud learning agent

Use [cloud_agent.py](../examples/cloud_agent.py) to learn named dispatch and managed
agent deployment with a stock voice. It imports no Studio worker, Qwen, Nemotron,
voice bundle, or private reference. It runs on ordinary Python, including Linux.
The main Studio continues to use its own local worker; starting this example does
not replace Studio's agent or connect it to the Studio UI.

This exercise sends conversation audio/text to LiveKit and inference providers.
`record=False` disables this agent's session audio recording request. It does not
disable all Cloud telemetry, transcripts, or provider retention. Inspect project
observability settings before discussing anything private.

A Cloud account and project are required. Check current
[allowances](https://docs.livekit.io/deploy/admin/quotas-and-limits/) and
[pricing](https://livekit.com/pricing) before starting: free project access is not
unlimited model inference. Local `dev` still uses Cloud transport and inference;
managed deployment also uses Cloud agent resources. No cloud account creation,
dispatch, inference, or deployment is part of the repository's offline tests.

## 1. Prepare dependencies and configuration

In an already installed Studio checkout, the `example` extra includes Silero.
For a fresh, cloud-example-only checkout, install without the local MLX extras:

```sh
uv sync --frozen --package livekit-plugins-voicebox --extra example --python 3.12
```

Do not run that reduced-extra sync over a working Studio environment unless you
intend to remove its additional extras. Use a separate checkout for this lesson.

Create a private `.env.cloud` file in that checkout using your project's URL,
API key and API secret. `.env*` files are ignored by this repository. Never put a
secret in browser code, issue reports, terminal screenshots, or committed files.

```dotenv
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=replace-locally
LIVEKIT_API_SECRET=replace-locally
LEARNING_STT_MODEL=deepgram/nova-3
LEARNING_LLM_MODEL=google/gemma-4-31b-it
LEARNING_TTS_MODEL=inworld/inworld-tts-2
LEARNING_TTS_VOICE=Ashley
```

These stock-model defaults follow the current
[LiveKit quickstart](https://docs.livekit.io/agents/start/voice-ai/). Overrides are
read when a job starts; use a supported model and a compatible voice together.
This example uses Silero VAD for a deliberately simple turn-taking baseline.

## 2. Run locally, with explicit dispatch

The first command explicitly prepares plugin assets; the second starts the local
agent server and registers its dispatch name with your Cloud project:

```sh
uv run --no-sync --env-file .env.cloud python -m examples.cloud_agent download-files
uv run --no-sync --env-file .env.cloud python -m examples.cloud_agent dev
```

Install the [LiveKit CLI](https://docs.livekit.io/agents/start/voice-ai/#livekit-cli), authenticate with
`lk cloud auth`, and use `lk project list` to verify the selected project. In a
second terminal, request one session:

```sh
lk dispatch create --agent-name studio-learning --room studio-learning-room
```

Use the Agent Console's named-agent session flow, or your own test frontend, to
join the same project and room. If Console creates and dispatches its own room,
use that flow instead of the manual command: do not create duplicate dispatches.
The dispatch command creates the agent job; it does not open a human microphone.
The agent waits for your first message rather than generating an automatic greeting.

Ask “What does STT do?” and “Explain dispatch.” The `explain_stage` tool accepts
only `room`, `token`, `track`, `stt`, `llm`, `tts`, and `dispatch`. It returns fixed
educational facts, with no file access, subprocesses or external writes. A tool's
local implementation is distinct from the remote model deciding to call it.
See [explicit dispatch](https://docs.livekit.io/agents/server/agent-dispatch/).

Evidence to capture privately: the selected project, named worker registration,
room participants, one answer, and one observed tool call. A worker startup log
alone does not prove speech, playback, or tools worked. Leave the room and stop
the dev process after the exercise. Avoid running a local and deployed worker
with the same name while comparing them: dispatch could reach either.

## 3. Optional managed deployment

This is an external deployment, not a local test. Review the selected Cloud
project and spending limits before executing it. Use a new, empty build directory
outside Studio; copy only the portable example. Do not upload the Studio checkout
or its models, voices, credentials, runtime directory, or generated recordings.

From the repository root, choose a new destination that does not already exist:

```sh
mkdir ../studio-learning-cloud
cp examples/cloud_agent.py ../studio-learning-cloud/agent.py
cd ../studio-learning-cloud
uv init --bare --python 3.12
uv add 'livekit-agents==1.8.1' 'livekit-plugins-silero==1.8.1'
```

Create this `Dockerfile` in that new directory:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir livekit-agents==1.8.1 livekit-plugins-silero==1.8.1
COPY agent.py ./agent.py
RUN python agent.py download-files
CMD ["python", "agent.py", "start"]
```

The build copies one Python file. Also create a `.dockerignore` containing:

```text
*
!agent.py
!Dockerfile
!.dockerignore
```

Before deployment, confirm this directory contains only the intended example and
build metadata. Authenticate and explicitly select the intended project, then:

```sh
lk project list
lk project set-default "YOUR-LEARNING-PROJECT"
lk agent create
lk agent status
lk agent logs
```

`lk agent create` uploads code and creates the managed agent. Cloud supplies its
LiveKit connection credentials; do not bake `.env.cloud` into the image. Set any
optional model overrides through the deployment's environment configuration.
The dispatch name remains `studio-learning`. Follow the
[deployment guide](https://docs.livekit.io/deploy/agents/quickstart/) for current
CLI behavior. Local tests do not prove this image has built or deployed.

Repeat the named-agent exercise, checking which deployed worker receives the job.
At the end, stop test sessions and use the Cloud dashboard to remove the learning
deployment if you do not want to retain it. Verify its status and usage afterward.

## Troubleshooting

| Symptom | Check first |
| --- | --- |
| No agent joins | Same project and exact `studio-learning` dispatch name; worker registration; no duplicate job |
| Connected but silent | Start by speaking or sending text; this example has no greeting; then check microphone publication and audio playback |
| Inference rejected | Project inference allowance, model availability, compatible voice, and server-side credentials |
| Tool not called | Ask about one supported stage and inspect tool events; ordinary text answers do not prove a call |
| Local works, deployment fails | Build logs, pinned dependencies, plugin asset download, and the `start` entrypoint |
| Multiple agents answer | End duplicate sessions; run one worker environment for the comparison |

## Offline verification

```sh
.venv/bin/python -m pytest tests/test_cloud_agent.py -q
```

The tests exercise stage validation, the real tool registration, provider factory
configuration, and `record=False` at the session boundary. They substitute model
providers and room startup; they do not test network authentication, inference
quality, microphone capture, Docker, or Cloud deployment.
