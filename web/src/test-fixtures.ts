import type { SessionGrant, StudioStatus } from './api';

// Isolated test data. These values never reach the application bundle or a real room.
export const readyStatus: StudioStatus = {
  ready: true,
  phase: 'idle',
  problems: [],
  voice: { name: 'Test voice', engine: 'qwen', model: '0.6B', loaded: true, cached: true },
  ai: { provider: 'azure', model: 'test-model' },
  livekit: { configured: true },
  session: null,
  metrics: { ttsFirstFrameSeconds: null, ttsAudioSeconds: null, llmFirstTokenSeconds: null },
  message: null,
};

export const testGrant: SessionGrant = {
  sessionId: 'test-owner',
  serverUrl: 'wss://example.invalid',
  participantToken: 'test-only-token',
  roomName: 'test-room',
  participantName: 'You',
  agentIdentity: 'test-agent',
};

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
