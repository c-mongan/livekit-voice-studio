// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import { deferred, readyStatus, testGrant } from './test-fixtures';
import { useStudio } from './useStudio';

interface FakeRoom {
  state: string;
  localParticipant: { trackPublications: Map<string, { track: { stop: ReturnType<typeof vi.fn> } }> };
  emit: (name: string, ...args: unknown[]) => void;
}
const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  connect: vi.fn(),
  disconnect: vi.fn(),
  setMicrophoneEnabled: vi.fn(),
  stopTrack: vi.fn(),
  rooms: [] as FakeRoom[],
}));

vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), api: mocks.api }));
vi.mock('livekit-client', () => ({
  ConnectionState: { Connected: 'connected' },
  RoomEvent: { Disconnected: 'disconnected' },
  TokenSource: { literal: vi.fn((value) => value) },
  Room: class {
    state = 'disconnected';
    listeners = new Map<string, Set<(...args: unknown[]) => void>>();
    localParticipant = { trackPublications: new Map([['mic', { track: { stop: mocks.stopTrack } }]]), setMicrophoneEnabled: mocks.setMicrophoneEnabled };
    constructor() { mocks.rooms.push(this); }
    on(name: string, callback: (...args: unknown[]) => void) {
      if (!this.listeners.has(name)) this.listeners.set(name, new Set());
      this.listeners.get(name)!.add(callback);
    }
    off(name: string, callback: (...args: unknown[]) => void) { this.listeners.get(name)?.delete(callback); }
    emit(name: string, ...args: unknown[]) { this.listeners.get(name)?.forEach((callback) => callback(...args)); }
    async connect(url: string, token: string) {
      this.state = 'connecting';
      this.emit('connectionStateChanged');
      await mocks.connect(url, token);
      this.state = 'connected';
      this.emit('connectionStateChanged');
    }
    async disconnect(stop: boolean) {
      await mocks.disconnect(stop);
      this.state = 'disconnected';
      this.emit('connectionStateChanged');
      this.emit('disconnected');
    }
  },
}));
vi.mock('@livekit/components-react', async () => {
  const { useSyncExternalStore } = await import('react');
  return {
    useSession: (_source: unknown, { room }: { room: FakeRoom & { on: Function; off: Function } }) => {
      const state = useSyncExternalStore(
        (callback) => { room.on('connectionStateChanged', callback); return () => room.off('connectionStateChanged', callback); },
        () => room.state,
      );
      return { room, connectionState: state };
    },
  };
});

let root: Root;
let host: HTMLDivElement;
let studio: ReturnType<typeof useStudio>;
function Harness() { studio = useStudio(); return null; }

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  mocks.rooms.length = 0;
  mocks.connect.mockResolvedValue(undefined);
  mocks.disconnect.mockResolvedValue(undefined);
  mocks.setMicrophoneEnabled.mockResolvedValue(undefined);
  mocks.api.mockImplementation(async (path: string) => {
    if (path === 'status') return readyStatus;
    if (path === 'session') return testGrant;
    if (path === 'session/end') return { phase: 'draining' };
    return { ok: true };
  });
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
});
async function mount() { await act(async () => root.render(<Harness />)); }
async function start() { await act(async () => { await studio.start(); }); }
const callsFor = (path: string) => mocks.api.mock.calls.filter(([called]) => called === path);

describe('session ownership and lifecycle', () => {
  it('never creates a session on mount, including effect remounts', async () => {
    await mount();
    await act(async () => root.render(<Harness />));
    expect(callsFor('session')).toHaveLength(0);
    expect(studio.grant).toBeNull();
    expect(studio.status?.session).toBeNull();
  });

  it('does not start while the broker reports drain or is unavailable', async () => {
    mocks.api.mockResolvedValue({ ...readyStatus, phase: 'draining' });
    await mount();
    await start();
    expect(callsFor('session')).toHaveLength(0);
    mocks.api.mockRejectedValue(new ApiError(0, 'Offline'));
    await act(async () => { await studio.refresh(); });
    await start();
    expect(studio.online).toBe(false);
    expect(callsFor('session')).toHaveLength(0);
  });

  it('deduplicates simultaneous start actions and consumes only the POST grant', async () => {
    await mount();
    const pending = deferred<typeof testGrant>();
    mocks.api.mockImplementation(async (path) => path === 'session' ? pending.promise : readyStatus);
    let first!: Promise<boolean>;
    let second!: Promise<boolean>;
    await act(async () => { first = studio.start(); second = studio.start(); });
    expect(callsFor('session')).toHaveLength(1);
    await expect(second).resolves.toBe(false);
    await act(async () => { pending.resolve(testGrant); await first; });
    expect(studio.grant?.sessionId).toBe(testGrant.sessionId);
    expect(mocks.connect).toHaveBeenCalledWith(testGrant.serverUrl, testGrant.participantToken);
    expect(mocks.setMicrophoneEnabled).toHaveBeenCalledExactlyOnceWith(false);
    expect(mocks.setMicrophoneEnabled.mock.invocationCallOrder[0]).toBeLessThan(mocks.connect.mock.invocationCallOrder[0]);
  });

  it('does not steal a foreign session after a 409', async () => {
    await mount();
    mocks.api.mockImplementation(async (path) => {
      if (path === 'session') throw new ApiError(409, 'Another browser owns the session.');
      return { ...readyStatus, phase: 'active', session: null };
    });
    await start();
    expect(studio.grant).toBeNull();
    expect(studio.error).toContain('Another browser');
    expect(callsFor('session')).toHaveLength(1);
    expect(callsFor('session/end')).toHaveLength(0);
    expect(mocks.connect).not.toHaveBeenCalled();
  });

  it('releases the owned broker session when room connection fails', async () => {
    await mount();
    mocks.connect.mockRejectedValueOnce(new Error('private connection diagnostics'));
    await start();
    expect(mocks.stopTrack).toHaveBeenCalled();
    expect(callsFor('session/end')).toEqual([['session/end', { sessionId: testGrant.sessionId }]]);
    expect(studio.grant).toBeNull();
    expect(studio.error).toContain('Could not connect to LiveKit');
    expect(studio.error).not.toContain('private');
  });

  it('stops capture synchronously before waiting for remote cleanup', async () => {
    await mount();
    await start();
    const remote = deferred<{ phase: string }>();
    mocks.api.mockImplementation(async (path) => path === 'session/end' ? remote.promise : readyStatus);
    let end!: Promise<void>;
    await act(async () => { end = studio.end(); });
    expect(mocks.stopTrack).toHaveBeenCalled();
    expect(studio.ending).toBe(true);
    await act(async () => { remote.resolve({ phase: 'draining' }); await end; });
    expect(studio.grant).toBeNull();
    expect(studio.ending).toBe(false);
  });

  it('ending during connection does not allow a late connection to reopen capture', async () => {
    await mount();
    const connection = deferred<void>();
    mocks.connect.mockReturnValueOnce(connection.promise);
    let starting!: Promise<boolean>;
    await act(async () => { starting = studio.start(); });
    expect(studio.grant?.sessionId).toBe(testGrant.sessionId);
    await act(async () => { await studio.end(); });
    await act(async () => { connection.resolve(); await starting; });
    expect(mocks.disconnect).toHaveBeenCalledTimes(2);
    expect(studio.grant).toBeNull();
    expect(mocks.rooms[0].state).toBe('disconnected');
  });

  it('retains the end response drain state even if the following status refresh fails', async () => {
    await mount();
    await start();
    mocks.api.mockImplementation(async (path) => {
      if (path === 'session/end') return { phase: 'draining' };
      throw new ApiError(0, 'Offline');
    });
    await act(async () => { await studio.end(); });
    expect(studio.status?.phase).toBe('draining');
    expect(studio.online).toBe(false);
    await start();
    expect(callsFor('session')).toHaveLength(1);
  });

  it('leaves failed cleanup retryable without extending the abandoned lease', async () => {
    await mount();
    await start();
    mocks.api.mockImplementation(async (path) => {
      if (path === 'session/end') throw new ApiError(0, 'The local server is offline.');
      return readyStatus;
    });
    await act(async () => { await studio.end(); });
    expect(studio.grant?.sessionId).toBe(testGrant.sessionId);
    expect(studio.error).toContain('offline');
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(callsFor('session/heartbeat')).toHaveLength(0);
    mocks.api.mockImplementation(async (path) => path === 'session/end' ? { phase: 'idle' } : readyStatus);
    await act(async () => { await studio.end(); });
    expect(callsFor('session/end')).toHaveLength(2);
    expect(studio.grant).toBeNull();
  });

  it('sends only owner-scoped heartbeats and stops after ownership is lost', async () => {
    await mount();
    await start();
    mocks.api.mockImplementation(async (path) => {
      if (path === 'session/heartbeat') throw new ApiError(409, 'Ownership lost');
      return readyStatus;
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(callsFor('session/heartbeat')).toEqual([['session/heartbeat', { sessionId: testGrant.sessionId }]]);
    expect(studio.grant).toBeNull();
    expect(mocks.stopTrack).toHaveBeenCalled();
    expect(studio.error).toContain('no longer owned');
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(callsFor('session/heartbeat')).toHaveLength(1);
    expect(callsFor('session/end')).toHaveLength(0);
  });

  it('surfaces transient heartbeat failure and clears it after recovery', async () => {
    await mount();
    await start();
    let failed = false;
    mocks.api.mockImplementation(async (path) => {
      if (path === 'session/heartbeat' && !failed) { failed = true; throw new ApiError(0, 'Offline'); }
      return readyStatus;
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(studio.heartbeatError).toContain('heartbeat');
    expect(studio.grant).not.toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(studio.heartbeatError).toBeNull();
  });

  it('terminal disconnect releases the room rather than reusing an expired token', async () => {
    await mount();
    await start();
    await act(async () => mocks.rooms[0].emit('disconnected'));
    expect(callsFor('session/end')).toHaveLength(1);
    expect(studio.grant).toBeNull();
    expect(mocks.connect).toHaveBeenCalledTimes(1);
  });

  it('pagehide stops capture and sends best-effort keepalive cleanup without URL tokens', async () => {
    await mount();
    await start();
    await act(async () => window.dispatchEvent(new Event('pagehide')));
    expect(mocks.stopTrack).toHaveBeenCalled();
    expect(callsFor('session/end')).toEqual([['session/end', { sessionId: testGrant.sessionId }, true]]);
    expect(studio.grant).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(callsFor('session/heartbeat')).toHaveLength(0);
  });
});
