# Listen before choosing a voice

A valid recording is not necessarily a good clone. Studio can check format,
duration and signal levels; it cannot decide whether the result sounds like you.

## Reference versus generated speech

**Original recording** plays what you recorded. **Generated audition** asks the
local Qwen model to say new words using that saved voice. Hearing the original
recording is not evidence that cloning works.

End the conversation first. In Voices, choose a saved voice, edit the audition
text and generate it. Wait for preparation and generation, then press Play.
This does not change the voice selected for your next conversation.

Auditions use the same Qwen provider and exclusive process lease as conversations.
There is no second inference engine running alongside a room. Cancelling stops
delivery immediately, but the next job must wait for confirmed model drain.
No generated audio is saved to disk: the broker holds one bounded result in
memory for at most 60 seconds after completion, delivers it once, and the browser
revokes its temporary audio URL when you leave or change the audition.

Audition limits are 300 characters, 30 seconds of output, a 90-second overall
generation bound and a separate bounded drain. An uncertain exit blocks reuse;
a successful health probe cannot clear that state.

## A repeatable listening check

Use the same short phrases when comparing your own authorized references:

| Check | Example text | Listen for |
| --- | --- | --- |
| Names | “Maya and Daniel will meet on Monday.” | Clear names without skipped syllables |
| Numbers | “The total is forty-two pounds and fifty pence.” | Complete numbers and natural grouping |
| Question | “Would you like to try a different approach?” | Question intonation without exaggeration |
| Pacing | “Take your time. We can work through it together.” | Comfortable pauses and stable loudness |

For each, note **likeness**, **word accuracy**, **pacing** and **audio artifacts**
as good, usable or needs another recording. A listener who knows the speaker is
more useful than a synthetic test for likeness. Repeat a surprising result:
generation can vary even with the same reference and words.

If the clone sounds wrong, first record again in a quiet room at a comfortable
level. Speak naturally, avoid clipping, and verify that the saved transcript
matches the recording exactly. Do not use punctuation to disguise an incorrect
transcript. Do not add other people's recordings without permission.

## Controls that actually reach this backend

The current audition collects **the same streaming chunks as conversation TTS**,
then wraps them in a playable WAV. It does not use a different, batch-only
decoder to make the preview sound better than the conversation.

Installed MLX Audio 0.5.3 source was checked against its upstream release.
These are the current choices, not additional UI sliders:

| Control | Current behavior |
| --- | --- |
| Reference audio + exact text | Both condition the base model; this is why transcript accuracy matters |
| Language | `lang_code="english"` |
| Streaming | `stream=True`, `streaming_interval=0.32`; the interval is generated audio span, not a latency promise |
| Output bound | `max_tokens=750`, with a separate 30-second decoded-audio cap |
| Sampling | Installed defaults: temperature 0.9, top-k 50, top-p 1.0 |
| Repetition | The installed reference-conditioned route clamps its penalty to at least 1.5 |

Speed, emotion, preset-voice and streaming-context sliders would not control this
base-model route as their names suggest. They are deliberately absent. Greedy
sampling or smaller chunks are not automatically better; a change needs an
audible comparison and a timing distribution before becoming a default.

Implementation references:
[MLX generation and reference routing](https://github.com/Blaizzy/mlx-audio/blob/0d2b681ec1f168397a871cfdf4c88378e68117ac/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py#L1126-L1256),
[streaming decode](https://github.com/Blaizzy/mlx-audio/blob/0d2b681ec1f168397a871cfdf4c88378e68117ac/mlx_audio/tts/models/qwen3_tts/qwen3_tts.py#L2268-L2370),
and the [official Qwen reference API](https://github.com/QwenLM/Qwen3-TTS/blob/022e286b98fbec7e1e916cb940cdf532cd9f488e/qwen_tts/inference/qwen3_tts_model.py#L355-L390).
The official Qwen API and MLX implementation are not interchangeable parameter
surfaces.

The reviewed [Voicebox HTTP route](https://github.com/jamiepine/voicebox/blob/51f49dea198384b4eb6087b72c17057c6eb1c1cd/backend/routes/generations.py#L366-L413)
finishes generation, effects and WAV encoding before sending the body in blocks.
That is not equivalent to model-incremental streaming. Direct MLX auditions do
not inherit Voicebox's effects, normalization or engine-picker settings. This
comparison is pinned to the reviewed Voicebox commit, not a claim about every
future Voicebox release.

## Conversation timing

Studio explicitly uses local Silero VAD for turn detection and interruption.
It disables LiveKit's speculative reasoning, so the SDK waits for a finalized
turn before requesting an answer. The installed SDK otherwise enables
preemptive generation by default, which can start requests that are later
cancelled or replaced. Cancellation is not a billing refund.

This is a deliberate local-first, predictable-usage choice, not a free speedup:
removing speculation can increase response latency. The older one-off timings
in the compatibility history used inherited turn-detection defaults. New
measurements must identify the explicit-VAD configuration. Ordinary provider
retries and provider-internal behavior are separate from SDK speculation.
See the pinned [LiveKit turn-handling implementation](https://github.com/livekit/agents/blob/f9b53c3c22e15a265d454c1e54dbc07ed3a50396/livekit-agents/livekit/agents/voice/turn.py#L201-L315).

Time from the end of a spoken turn to the first response audio includes speech
recognition, turn-end detection, remote reasoning, local speech generation and
transport. TTS first-frame time alone is not conversational latency.

The opt-in spoken test can repeat the same explicitly supplied synthetic fixture
for up to five turns in one room:

```sh
STUDIO_SPOKEN_INTEGRATION=1 \
STUDIO_TEST_AUDIO=/private/path/synthetic-input.wav \
STUDIO_TEST_TURNS=5 \
uv run --no-sync pytest tests/integration/test_spoken_studio.py -q -s
```

Start Studio first and end other rooms. The test uses the configured providers
and therefore consumes their normal reasoning usage. It waits for the previous
response to finish before the next turn, reports every observation plus median
and slowest observed time, and saves no response recording. Five repeated inputs
are a small diagnostic sample, not a population p90 or an accent benchmark.

Try interruption separately: start a response, interrupt it, then ask something
different. Check by listening that old words do not resume. Automated RPC
acknowledgements and nonzero PCM checks are useful, but do not establish audible
interruption quality on your speakers.

## Costs and a future read-aloud adapter

Local Nemotron recognition and Qwen synthesis do not incur LLM API token charges.
They use your CPU/GPU, memory and electricity. Copilot, Codex, Azure or OpenAI
reasoning still uses the selected account's allowance or billing; conversations
include context as well as the newest message. LiveKit has its own plan limits.

Reading an already-generated agent reply aloud locally needs no second reasoning
request. A future read-aloud extension can reuse the voice provider and ownership
rules without giving the voice UI access to coding tools. That extension is not
implemented here, and a prompt skill alone is not a persistent playback service.
