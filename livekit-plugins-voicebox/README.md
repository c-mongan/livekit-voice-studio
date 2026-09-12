# LiveKit Voicebox

Use local Voicebox Qwen 0.6B cloned voices with LiveKit Agents.

```python
from livekit.plugins import voicebox

tts = voicebox.TTS(profile="your authorized voice", engine="qwen", model_size="0.6B")
```

This is an **unreleased technical prototype**, not production-ready. It requires
exclusive backend use: one client, one agent, no concurrent desktop generation.
Voicebox returns a completed WAV; LiveKit provides sentence-level streaming with
`ChunkedStream` and `streaming=False`. Audio becomes bounded mono PCM16 with RTC
resampling. The default local origin is `http://127.0.0.1:17493`.

Qwen 0.6B must already be cached. No automatic model downloads. A normal authorized
first generation may load the cached model, replacing another in memory.
`model_readiness()` distinguishes cached from loaded; `health()` is connectivity
only. `check_idle()` detects tracked work but is not a global inference lock.

Cancellation suppresses stale output but cannot stop Voicebox's inference thread.
Submitted jobs keep their lease through a bounded drain. Unknown completion blocks
admission; confirm backend stop and recreate the client. Foreground timeout is
60 seconds, drain timeout 120 seconds, and submitted jobs last at most 180 seconds
by default. Externally supplied aiohttp sessions are not closed.

Inputs are limited to 800 characters, WAV responses to 16 MiB, decoded audio to
30 seconds and 1-2 channels. Only Qwen 0.6B cloned profiles are supported.
Profile/options updates affect future streams and preserve in-flight snapshots.

Reference recordings stay in Voicebox. Generated speech travels through LiveKit,
and hosted STT/LLM providers have their own data flows. The plugin sanitizes server
errors, but Voicebox and LiveKit tracing may log text. Only use authorized voices.
Correct audio does not verify speaker identity, room playback or conversation
latency. See the checkout's README, COMPATIBILITY.md, examples, benchmarks and
reviewed BUILD.md for usage and evidence.
