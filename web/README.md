# Voicebox Studio frontend

Local browser workspace for the existing LiveKit/Azure/Voicebox agent.
The Python Studio broker owns credentials, room provisioning, agent lifecycle,
voice/model settings, heartbeats, and drain safety.

## Development

Requires Node 22.12+ (tested with 22.23) and npm.

```sh
cd web
npm ci
npm run dev
```

Vite binds to `127.0.0.1` and proxies `/api` to the local broker on port `8765`.
No API keys or `.env` values belong in this frontend.

```sh
npm run typecheck
npm test
npm run build
```

Tests include API/state units, DOM interactions (typed-first queueing, permission
denial, interrupt recovery, keyboard/IME, privacy and transcript safety), and
session-hook lifecycle tests (duplicate start, failed connect/end, ownership
loss, terminal disconnect and pagehide). LiveKit and HTTP are explicitly mocked
in the lifecycle/UI tests. Discovery regressions additionally exercise the
actual installed LiveKit hooks and participant objects without signaling or
media; no test connects to a real service. Stylesheet-contract tests
guard mobile stacking, wrapping, target sizing and reduced motion, but do not
replace browser geometry/390px checks or real microphone/playback E2E.

The broker serves the generated `web/dist` directory in production. A normal
Vite preview server alone does not provide the Studio API.

The UI follows the agent's actual state while a reply is generated. Local WAV
synthesis can take tens of seconds; this is not treated as a failed reply.
The 60-second readiness timeout applies to the agent joining/initializing, not a
20-second response limit. Stop reply and End session remain available.
Optional `voice.backend` and `voice.streaming` status fields select the matching
pipeline explanation. Missing fields preserve the Voicebox complete-WAV path.
MLX PCM streaming is shown only when reported, and a cold model says “Prepares
at start” until the worker reports `loaded: true`. Audio streaming does not
imply token-by-token text synthesis.

## SDK integration

The licensed starter adapter wraps `SessionProvider` and `RoomAudioRenderer`.
`useSession` supplies shared LiveKit session context; an in-memory
`TokenSource.literal` maps the broker grant without any extra API fetch.
The UI explicitly calls `Room.connect` because the broker already dispatches
the agent, and `useSession.start` otherwise defaults to microphone capture and
may wait for token-based agent dispatch. No second room is created.
Before every new connection it explicitly calls the SDK's
`setMicrophoneEnabled(false)` to reset any leftover publication. No call enables
capture until the user presses Turn mic on. Native-SDK tests spy on
`getUserMedia` during first and repeated typed-first starts.

- `useStudioAgent`: binds to the exact broker-returned identity using native
  `useRemoteParticipant`, `useParticipantAttributes`, and `useParticipantTracks`.
  Readiness requires that participant's actual `lk.agent.state` to be listening,
  thinking, or speaking. It never infers readiness from the HTTP status.
  This avoids an initial-attribute snapshot issue in `useAgent` 2.9.20 when the
  agent arrives with its listening attribute already set.
- `useLocalParticipant`: microphone enable/disable and observed state.
- `useSessionMessages`: standard `lk.chat` messages and `lk.transcription`
  text streams. Its `send` method returns the canonical sent message; no
  additional optimistic duplicate is inserted by the app.
- `useMultibandTrackVolume`: real input/output audio activity, no invented wave.
- `StartAudio`: browser autoplay unlock.
- `performRpc`: `voicebox.interrupt` with `{}` payload and the granted agent
  identity. The audio renderer stays muted until the request is acknowledged
  and the SDK reports that the interrupted reply is no longer speaking. A
  failed request restores playback and offers End session as the fallback.

All requests use same-origin credentials. Mutations use `X-Voicebox-Studio: 1`;
JSON is used except for voice uploads, whose multipart boundary is set by the
browser. `/api/session` is called only from an explicit start
action (including Start & send), with a synchronous duplicate-action guard.
Heartbeats run every 10 seconds while the browser owns the session. Capture
stops before end cleanup; failed cleanup can be retried without keeping the
lease alive. The server's reported drain phase blocks another start.

On a terminal disconnect the UI releases the broker session rather than
reusing an expired token. On unload it makes a best-effort keepalive end
request; the broker must expire abandoned ownership independently.

## Privacy

Transcripts, drafts, room tokens and ownership handles exist only in memory.
The frontend does not record conversations, use browser persistence, send
analytics, or log transcripts. Explicit voice enrollment captures a temporary
reference for review, then saves it only to the loopback Studio server after
transcript verification and authorization. LiveKit transports media/text; cloud
speech (when selected) and language providers process their respective inputs. Local voice generation is not
an assertion that the entire conversation stays on the machine.

## Provider settings and voice enrollment

Open **Settings & voices** for two compact tabs. Settings load from
`GET /api/settings`; updates send allowlisted fields to `POST /api/settings`.
Speech recognition supports Nemotron, Azure and OpenAI as advertised by the
server. Reasoning presets are Copilot/Codex `gpt-5.6-luna` with `low` effort,
Azure `gpt-4.1-nano`, and OpenAI `gpt-4.1-mini` (no effort for the latter two).
Unavailable providers remain disabled with the server's reason. No keys,
service URLs, model downloads or agent tools are configured by this page.

The Voices tab reads `GET /api/voices`, including the guided passage. Record
5-30 seconds, preview the sample, correct its transcript and confirm permission.
`AudioWorklet` captures PCM; a small encoder creates mono 16-bit WAV at 24 kHz.
The worklet is emitted as a same-origin asset rather than an inline data URL.
The broker's CSP must allow scripts from `self` and media from `self blob:`.
There is no WebM/FFmpeg dependency or cloud reference transcription.

`POST /api/voices` sends multipart `name`, `transcript`, `authorized=true` and
`audio` (WAV, at most 4 MiB). The reference can be explicitly previewed through
`GET /api/voices/{id}/audio`, renamed with `PATCH /api/voices/{id}`, or deleted
with `DELETE /api/voices/{id}` and `{confirm:true}`. Select another voice before
deleting the selected one. Selecting a voice changes only the next room.
Session activation cancels enrollment; start, active and draining states disable
all configuration changes. Cancel/close/unmount/pagehide stop capture, discard
temporary audio, and revoke preview URLs. No audio is captured on mount.

See `PROVENANCE.md` and `THIRD_PARTY_LICENSES` for starter reuse.

## Generated voice auditions

The Voices tab distinguishes **Original recording** (the saved reference) from
**Audition clone** (newly generated speech). Saving a reference opens its audition;
edit the sample sentence, generate, explicitly press Play, then choose the voice
for the next chat. Auditioning never changes the selected voice. Reference quality
guidance focuses on quiet single-speaker audio and an exact transcript, not
unimplemented quality sliders.

`GET /api/status` supplies the global phase. Auditions require idle/no active
conversation, not `status.ready`: that flag includes LiveKit and cloud-provider
credentials which local auditions do not need. The audition endpoint validates
the saved voice, local model and hardware when generation is requested. Missing
setup returns an actionable error rather than a fabricated readiness indication.

The generated-audio protocol uses protected same-origin JSON POST requests:

- `/api/audition`: `{voiceId, text}` (1–300 characters); returns `{auditionId, voiceId, phase}`.
- `/api/audition/status`: `{auditionId}`; polls the owned job, never a public session handle.
- `/api/audition/audio`: `{auditionId}`; accepts only nonempty `audio/wav`, only
  after an owned status reports `state: "ready", phase: "idle"`. Delivery is one-time;
  expired or failed downloads require generating another sample.
- `/api/audition/end`: `{auditionId}`; removes the queued result and cancels work.

Starting immediately locks settings, enrollment, selection, and both conversation
start paths. Cancel, focus/text changes, tab/drawer close, pagehide, and unmount
invalidate playback. Cleanup continues polling the owned job until drain is
confirmed; even a start response arriving after close/unmount is ended. Transient
errors keep the worker gate locked and retry. A 404/410 from audition status/end
means the handle expired or was invalidated: stop polling that handle, discard
pending audio, and refresh public Studio status before releasing the local gate.
Ownership loss never implies that the backend is idle; its refreshed phase or
offline state continues to protect other rooms and jobs. Ready WAVs use revocable in-memory blob URLs,
never browser storage or download links. Native playback is explicit, not autoplay.

Tests mock HTTP and WAV bytes; they cover stale identity, late start/audio,
cleanup drain/failures, invalid WAVs, active-room gating, accessible controls, and
blob revocation. Live model/audio-quality and responsive browser verification
remain separate integration checks; the build and DOM suite do not establish them.
