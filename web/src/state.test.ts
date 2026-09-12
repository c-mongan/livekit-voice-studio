import { describe, expect, it } from 'vitest';
import { MAX_MESSAGE_LENGTH, mergeTranscript, validMessage, workspaceState } from './state';
import type { StudioStatus } from './api';

const status: StudioStatus = {
  ready: true, phase: 'idle', problems: [],
  voice: { name: 'Test voice', engine: 'qwen', model: '0.6B', loaded: true, cached: true },
  ai: { provider: 'azure', model: 'test-model' }, livekit: { configured: true },
  session: null, metrics: { ttsFirstFrameSeconds: null, ttsAudioSeconds: null, llmFirstTokenSeconds: null }, message: null,
};
const base = { status, online: true, owned: false, starting: false, ending: false, connection: 'disconnected', agent: 'disconnected', mic: false };

describe('truthful workspace state', () => {
  it('is ready only when the broker is ready and idle', () => expect(workspaceState(base)).toBe('ready'));
  it('marks an unavailable local server offline', () => expect(workspaceState({ ...base, online: false })).toBe('offline'));
  it.each(['active', 'starting', 'blocked'] as const)('blocks foreign or unavailable broker phase %s', (phase) => expect(workspaceState({ ...base, status: { ...status, phase } })).toBe('blocked'));
  it('never shows listening when the mic is off', () => expect(workspaceState({ ...base, owned: true, connection: 'connected', agent: 'listening' })).toBe('ready'));
  it('shows actual listening with an enabled mic', () => expect(workspaceState({ ...base, owned: true, connection: 'connected', agent: 'listening', mic: true })).toBe('listening'));
  it.each(['thinking', 'speaking'] as const)('preserves actual agent state %s', (agent) => expect(workspaceState({ ...base, owned: true, connection: 'connected', agent })).toBe(agent));
  it.each(['reconnecting', 'signalReconnecting'])('prioritizes %s over old agent state', (connection) => expect(workspaceState({ ...base, owned: true, connection, agent: 'speaking' })).toBe('reconnecting'));
  it('shows connecting while the agent initializes', () => expect(workspaceState({ ...base, owned: true, connection: 'connected', agent: 'initializing' })).toBe('connecting'));
  it('does not promise readiness for an idle agent that cannot receive input yet', () => expect(workspaceState({ ...base, owned: true, connection: 'connected', agent: 'idle' })).toBe('connecting'));
  it('prioritizes drain over old speaking state', () => expect(workspaceState({ ...base, owned: true, ending: true, connection: 'connected', agent: 'speaking' })).toBe('draining'));
  it('keeps a broker drain visible after the browser disconnects', () => expect(workspaceState({ ...base, status: { ...status, phase: 'draining' } })).toBe('draining'));
  it('does not label failed agents ready', () => expect(workspaceState({ ...base, owned: true, connection: 'connected', agent: 'failed' })).toBe('blocked'));
});

describe('in-memory transcript', () => {
  const first = { id: 'a', message: 'Hello', role: 'you' as const, timestamp: 1 };
  it('updates a streaming segment by ID rather than duplicating it', () => {
    expect(mergeTranscript([first], [{ ...first, message: 'Hello again' }], new Set())).toEqual([{ ...first, message: 'Hello again' }]);
  });
  it('does not duplicate SDK canonical typed messages', () => expect(mergeTranscript([first], [first], new Set())).toEqual([first]));
  it('does not restore cleared IDs on subsequent stream updates', () => expect(mergeTranscript([], [{ ...first, message: 'Updated' }], new Set(['a']))).toEqual([]));
  it('retains history when SDK chat resets on disconnect', () => expect(mergeTranscript([first], [], new Set())).toEqual([first]));
  it('preserves intentional identical messages with different IDs', () => expect(mergeTranscript([first], [{ ...first, id: 'b' }], new Set())).toHaveLength(2));
  it('ignores blank streamed segments', () => expect(mergeTranscript([], [{ ...first, message: ' ' }], new Set())).toEqual([]));
});

describe('composer limits', () => {
  it.each(['', ' ', '\n'])('rejects empty content %j', (text) => expect(validMessage(text)).toBe(false));
  it('accepts exactly 800 characters', () => expect(validMessage('x'.repeat(MAX_MESSAGE_LENGTH))).toBe(true));
  it('rejects more than 800 characters', () => expect(validMessage('x'.repeat(MAX_MESSAGE_LENGTH + 1))).toBe(false));
});
