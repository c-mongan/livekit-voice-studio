# Troubleshooting Studio

Start with `./studio status`. A running HTTP server and a ready speech model are
different things; the UI reports missing setup before starting a conversation.

| Problem | What to check |
| --- | --- |
| Server unavailable | Run `./studio start`. If it fails, read the private logs described in [launching](launching.md). |
| Model missing | Check `VOICEBOX_MLX_MODEL_PATH` against the complete existing Qwen 0.6B snapshot. Use the explicit [first-run setup](quickstart.md), not a different model. |
| Recognizer unavailable | Check the native executable and model paths with the [Nemotron setup guide](local-stt.md). Don't start a second sidecar on the same port. |
| Local LiveKit unavailable | Start `livekit-server --dev --bind 127.0.0.1`, keep it running, then retry. Studio and LiveKit are separate services. |
| Ollama unavailable | Start `ollama serve` if it is not already running. Check the API address in Settings, including the correct port and `/v1`. |
| Selected chat model missing | Run `ollama list` and select an installed chat model. Install another model explicitly if wanted; Studio does not download or choose a fallback. |
| Model-list route missing (404) | Check the API base address in Settings. Many compatible servers require `/v1`; use that server's documented base URL. |
| Reasoning authentication rejected (401/403) | Check the selected server's credential and model access. A custom endpoint uses `VOICEBOX_CUSTOM_LLM_API_KEY`, not your unrelated OpenAI key. |
| Reasoning service unavailable (5xx) | Repair or restart that service, then retry. Studio has not switched providers. |
| Codex/Copilot unavailable | Sign in to the selected CLI and verify the requested model is available. Codex also needs the separate restricted-agent consent. Installed CLI files alone do not prove model access. |
| Microphone denied | Keep using text, or explicitly grant microphone access in your browser. |
| No sound | Check your output device and system volume; use the browser's audio-start control if shown. |
| Voice sounds wrong | Verify the exact reference transcript and record again without clipping or background speech. Use the [audition checklist](voice-quality.md). |
| Audition expired | Generate another sample; generated audio is temporary rather than a library recording. |
| Another operation owns the backend | End the room or stop narration, then wait for confirmed drain. Don't run another worker. |
| Room cleanup cannot reach LiveKit | Restore connectivity and verify the owned temporary room was removed. A local health response cannot confirm cloud cleanup. |
| Completion unknown | Confirm the inference backend has stopped before using the explicit recovery procedure below. |

## Recovery is not just a restart button

Stop playback and stop inference are different operations. The process can remain
busy after the listener disconnects, so Studio fails closed when completion is
unknown.

For direct local MLX, confirm that the owned Studio worker has exited. For the
original HTTP backend, also stop/restart Voicebox itself: its inference thread
can outlive a disconnected client. Resolve any remaining LiveKit room before
restarting the broker.

Only after confirming the relevant backend stopped:

```sh
./studio stop
./studio status
./studio start --confirm-backend-restarted
```

Never clear the marker just because `/health` responds. Preserve private logs
for diagnosis, but redact them before sharing. Don't upload `.env`, voice bundles,
agent runtime directories or reference recordings in an issue.

## UI development

Use `npm --prefix web run dev` alongside Studio. Vite binds to loopback and
proxies requests to the local API. The production build is served by Python.
Do not start the standalone example worker alongside Studio.

## Session startup is slow

The conversation notice identifies the current startup stage: reasoning-account
validation, Qwen model loading, selected-voice preparation, speech detection, or
LiveKit connection. These are fixed messages; private provider output and model
paths are not exposed. A startup timeout retains its last stage while owned work
finishes draining. Wait for idle before retrying; do not clear an unresolved-work
marker just to dismiss a slow start.

A configuration-ready doctor result does not establish runtime readiness. A
cold model load can behave differently from a warm one. Keep dependency installs
and other heavy work separate from voice measurements, and record failed cold
starts alongside successful samples. Do not increase deadlines or weaken drain
checks merely to make a benchmark pass.

## Retry after a connection or model failure

If startup fails before a worker starts, Studio returns to idle. Fix the service,
address or model choice, then start a new session; you do not need to clear any
backend marker. If a running session fails, end it and wait for cleanup before
retrying. A blocked state requires the verified recovery procedure above.

A dropped model stream is an error, even if some words arrived first. Studio's
endpoint adapter does not automatically resend the prompt or choose another
provider. Explicit retries can create another provider request; partial output is
not proof that the previous answer completed.
