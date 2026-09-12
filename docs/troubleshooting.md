# Troubleshooting Studio

Start with `./studio status`. A running HTTP server and a ready speech model are
different things; the UI reports missing setup before starting a conversation.

| Problem | What to check |
| --- | --- |
| Server unavailable | Run `./studio start`. If it fails, read the private logs described in [launching](launching.md). |
| Model missing | Check `VOICEBOX_MLX_MODEL_PATH` against the complete existing Qwen 0.6B snapshot. Use the explicit [first-run setup](quickstart.md), not a different model. |
| Recognizer unavailable | Check the native executable and model paths with the [Nemotron setup guide](local-stt.md). Don't start a second sidecar on the same port. |
| Provider unavailable | Sign in to the selected CLI or configure the selected cloud provider. Installed CLI files alone do not prove model access. |
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
