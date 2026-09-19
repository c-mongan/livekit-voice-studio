# Learn LiveKit through a conversation

Start with the [quickstart](quickstart.md). Hear a local generated sample before
connecting a conversation, then open **How it works** in Studio. The view explains
the running application; it is not a simulated claim that every component works.
Missing measurements mean “not reported,” not zero.

Use ordinary, non-private test sentences. Local voice synthesis does not make a
LiveKit conversation fully offline: room media passes through the configured
LiveKit server, and the selected recognition/reasoning providers determine which
speech or text leaves your machine. Studio's microphone check stays in browser
memory; it does not publish a track to a room.

Work through one exercise at a time. Keep a short private record with: what you
tried, expected behavior, observed behavior, relevant measurement, and unresolved
question. These are exercises to perform, not claims that they have already passed.

## 1. Understand the room and its permissions

A room connects participants. Studio joins as a browser participant; the worker
joins as the agent. A server creates a signed token that grants room access.
The browser receives the token, never the signing secret. Keep tokens out of
screenshots and logs because they grant access until they expire.

**Exercise:** start one session and inspect its room/participant state. Explain
which participant produces your microphone audio and which produces the reply.
End the session and confirm the UI disconnects. If the browser joins but no agent
becomes ready, investigate worker startup separately from browser authentication.

**Evidence:** a connected browser and an identified ready agent. A connected
browser alone is insufficient. Read [authentication](https://docs.livekit.io/frontends/build/authentication/)
and [rooms and participants](https://docs.livekit.io/transport/).

## 2. Distinguish audio tracks from text

A microphone publishes an audio track; another participant subscribes to it.
Typed messages travel as text, bypassing microphone capture and speech recognition.
A typed message can still produce a spoken answer. A transcript is text associated
with a conversation; it is not the audio itself.

**Exercise:** send a typed message before enabling the microphone. Confirm the
browser has not requested microphone permission. Then deliberately enable the
microphone, say one short sentence, and compare the transcript with your words.
Mute it and observe the change in track state. Do not infer that an empty visual
meter proves the microphone is off: use the explicit microphone/track state.

**Evidence:** text-only input, a separately enabled microphone, and the agent's
subscribed output track. If speech is generated but inaudible, check browser
playback permission and the output device before blaming the TTS model.
See [text and transcriptions](https://docs.livekit.io/agents/multimodality/text/).

## 3. Try an interruption and an explicit stop

Automatic interruption begins with user speech. **Stop reply** is different: the
browser calls the agent's `voicebox.interrupt` RPC and waits for a response. The
worker checks that the caller is the session owner, then interrupts the session.
This is a small request/response operation, not a new chat message.

**Exercise:** request a few sentences, interrupt naturally, and ask a short follow-up.
Repeat using Stop reply. Check that speech stops, controls recover, and the next
message works. With local synthesis, cancellation must also let the active model
work drain safely before another generation starts.

**Evidence:** interruption/stop response and a successful subsequent turn. A stopped
animation alone does not prove that audio or the backend stopped. Do not probe
another person's room to test ownership; use the offline ownership tests.
See [RPC](https://docs.livekit.io/transport/data/rpc/).

## 4. Compare turn detection

Voice activity detection (VAD) detects speech and silence. A turn detector estimates
whether the speaker has finished their thought. Neither is speech recognition.
Studio defaults to `VOICEBOX_TURN_DETECTION=vad`; the optional `audio-local` mode
uses the installed LiveKit local inference runtime. Unsupported installations fail
with a repair/fallback message instead of silently switching to cloud inference.

**Exercise:** use the same sentences in both modes. End the conversation, change
`VOICEBOX_TURN_DETECTION` in `.env`, and restart the Studio server or service before
starting a fresh conversation. Starting another conversation alone does not reload
`.env`. An explicit environment variable takes precedence over `.env`; update that
value too if your launcher sets it. Include “I was thinking … maybe tomorrow,” a short
“yes,” and a correction while the agent speaks. Keep the pauses roughly comparable.
Record premature replies, long waits, missed words, and successful interruptions.
Use several attempts; a single pleasing exchange is weak evidence.

**Evidence:** your small comparison table and a working follow-up after each mode.
For Nemotron, check that final words still arrive: finalizing a recognition utterance
and deciding when to answer are related but separate operations.
See [turn detection](https://docs.livekit.io/agents/logic/turns/turn-detector/).

## 5. Read timings without inventing totals

Studio reports measurements from distinct stages. They can overlap, and not every
provider reports every metric. Associate events by speech/turn ID where available;
otherwise a “latest” number may belong to a different reply.

| Measurement | What it helps explain | What it does not establish |
| --- | --- | --- |
| End-of-utterance delay | Waiting around the end of a spoken turn | Entire user-to-speaker response latency |
| Transcription delay | Recognition finalization after speech | Reasoning or speech synthesis speed |
| LLM first token | Time until the reasoning provider starts output | Time until the complete answer is ready |
| TTS first audio | Time until synthesis yields initial audio | When a browser speaker audibly plays it |
| Synthesis duration | Work spent generating speech | End-to-end duration when stages overlap |

**Exercise:** compare a typed question with a spoken version. Then compare the
first reply after model startup with a subsequent reply. Explain which measurements
are missing, which changed, and what remains uncertain. Never add all displayed
numbers into a fabricated response-time total. Browser playback requires its own
observation; an audio track's existence is not a precise playback timestamp.

**Evidence:** a couple of labeled turns and a cautious diagnosis. If provider timing
is low but playback seems slow, investigate buffering, transport, subscription,
and autoplay. See [agent metrics and events](https://docs.livekit.io/reference/agents/events/)
and [observability](https://docs.livekit.io/deploy/observability/).

## 6. Understand dispatch and deployment separately

Studio launches a dedicated local worker for its private session. The separate
[Cloud learning example](cloud-learning.md) uses a named `AgentServer` and explicit
dispatch, with stock cloud inference. Follow that lesson to compare a local dev
worker with a managed deployment without uploading private voice assets.

**Evidence:** explain how a request finds the named agent, which machine runs it,
what happens when no worker is available, and which services consume allowance.
An offline factory test proves wiring; a successful Cloud session proves more.
Keep those claims separate in a portfolio or interview.

## A useful Developer Success exercise

Give a friend only the quickstart and ask them to attempt a first conversation.
Observe where they hesitate without taking over. Convert one failure into a small
issue containing sanitized steps, expected versus actual behavior, environment
versions, and the first relevant error. Fix it and have them retry.

That demonstrates a complete support loop: reproduce, isolate, explain, repair,
and verify. Do not include credentials, tokens, voice recordings, or personal
transcripts in a public issue. Independent installation and listening acceptance
remain open until a person actually performs them.

## A repeatable break-and-fix practice

In **How it works → Practice troubleshooting**, choose one of three exercises.
The guide operates no services itself. Its checkboxes are your observations, not
proof generated by Studio. Progress is kept only while that view remains mounted.

For the endpoint failure exercise, use a disposable local setup and first note
your working Ollama URL and model. End the session, keep local LiveKit, select
Ollama, and save `http://127.0.0.1:1/v1`. Close Settings and try Start session once.
Saving validates format; this deliberately unavailable loopback endpoint should
fail the session model preflight. Restore the exact working URL
and model, save, and prove recovery with a fresh typed reply. Avoid stopping
shared services, altering credentials or forcing a backend recovery acknowledgement.

This teaches failure isolation: an unavailable reasoning endpoint is not evidence
that the LiveKit room transport failed. Configuration validation and successful
provider authentication/inference are different checks. Local LiveKit is a real
LiveKit server; its development mode is described in the official
[local server guide](https://docs.livekit.io/transport/self-hosting/local/).

## An interview walkthrough

Aim for a five-minute walkthrough; rehearse and adjust to your actual timing.

1. **User problem:** show how someone records an authorized voice, auditions it,
   and starts a conversation. Explain why the first typed message isolates speech
   recognition from the rest of the pipeline.
2. **Architecture:** point to the room, browser and agent. Explain where tokens
   are signed, how tracks carry audio, and where STT, LLM and TTS run. Show the
   selected route before making any privacy claim.
3. **Failure:** demonstrate one rehearsed exercise with synthetic/non-private text.
   State a hypothesis, collect evidence, restore the configuration, then prove a
   successful follow-up. Explain what you would ask a customer next.
4. **Tradeoff:** compare local control/hardware requirements with managed service
   operations. Do not label Cloud inference unlimited or free. Distinguish this
   local worker launcher from the named-dispatch Cloud example.
5. **Evidence and limits:** show tests and measured turns. Describe the unresolved
   intermittent recognition failure honestly. Explain what additional repeated
   runs, device testing and fresh-user installation would establish.

Keep a small private worksheet:

| Hypothesis | Action | Expected | Observed | Recovery verified? |
| --- | --- | --- | --- | --- |
| Input path causes silence | Compare typed and spoken input | Typed works if only input path is affected | Fill after running | Send a follow-up |
| Cancellation drains safely | Stop a reply | Audio stops; next turn works | Fill after running | Hear the next reply |
| Model endpoint unavailable | Use unavailable local port | Session preflight fails | Fill after running | Restore and send text |

A useful Developer Success story is a clear reproduction, a careful diagnosis,
a small fix and a verified recovery. Do not claim production scale, offline
certification or deployment experience from these local exercises.
