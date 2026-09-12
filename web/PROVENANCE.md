# Frontend provenance

`src/components/agent-session-provider.tsx` is adapted from
[`components/agents-ui/agent-session-provider.tsx`](https://github.com/livekit-examples/agent-starter-react/blob/44b8a0ce82039018a1feb2c1bda43b5ada2ab24e/components/agents-ui/agent-session-provider.tsx)
in the official LiveKit React Agent Starter, commit
`44b8a0ce82039018a1feb2c1bda43b5ada2ab24e`.

The original implementation wrapping `SessionProvider` and `RoomAudioRenderer`
is retained. Adaptation removes redundant intersection types, redundant room/audio
property declarations already covered by `RoomAudioRendererProps`, and extensive
example comments. It adds a local attribution pointer and an explicit ReactNode
type import. No Next.js dependency is required by this component.

The full original MIT notice is in `THIRD_PARTY_LICENSES`. No other starter source
files or assets are copied. The workspace layout, application controls, API
adapter, state logic, and styles are new code.

Media transport, capture, playback, audio analysis, text streams, transcript
merging, participant observation, and reconnection use the maintained LiveKit
packages, not a custom WebRTC implementation. Direct runtime versions are pinned
to `@livekit/components-react` 2.9.20 and `livekit-client` 2.17.2, and the complete
dependency graph is locked in `package-lock.json`.
