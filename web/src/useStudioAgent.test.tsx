// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { RoomContext } from '@livekit/components-react';
import { ConnectionState, ParticipantEvent, ParticipantKind, RemoteParticipant, Room, RoomEvent } from 'livekit-client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStudioAgent } from './useStudioAgent';

// These tests use the real installed LiveKit hooks and participant objects.
// Only signaling is absent: no room connection, credentials, network, or media.
let room: Room;
let root: Root;
let host: HTMLDivElement;
let identity: string;
let observed: ReturnType<typeof useStudioAgent>;
function Harness() {
  observed = useStudioAgent(identity, room.state);
  return null;
}
function render() { root.render(<RoomContext.Provider value={room}><Harness /></RoomContext.Provider>); }
function participant(id: string, state?: string) {
  return new RemoteParticipant(
    {} as ConstructorParameters<typeof RemoteParticipant>[0],
    `sid-${id}`, id, 'Test agent', undefined,
    state ? { 'lk.agent.state': state } : {},
    undefined, ParticipantKind.AGENT,
  );
}
async function join(p: RemoteParticipant) {
  await act(async () => {
    room.remoteParticipants.set(p.identity, p);
    room.emit(RoomEvent.ParticipantConnected, p);
  });
}
async function changeState(p: RemoteParticipant, state: string) {
  await act(async () => {
    // Simulate the server updating the public attribute snapshot before its SDK event.
    Object.defineProperty(p, 'attributes', { configurable: true, value: { 'lk.agent.state': state } });
    p.emit(ParticipantEvent.AttributesChanged, { 'lk.agent.state': state });
  });
}
beforeEach(() => {
  vi.useFakeTimers();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  identity = 'owned-agent';
  room = new Room();
  room.state = ConnectionState.Connected;
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
});

describe('identity-bound native LiveKit discovery', () => {
  it('reads an existing listening snapshot when the agent arrives after mount', async () => {
    await act(async () => render());
    expect(observed.state).toBe('connecting');
    const agent = participant(identity, 'listening');
    await join(agent);
    // No AttributesChanged event is emitted: this is the real integration regression.
    expect(observed.state).toBe('listening');
    expect(observed.isConnected).toBe(true);
  });

  it('reads a participant already present when the component mounts', async () => {
    const agent = participant(identity, 'thinking');
    room.remoteParticipants.set(agent.identity, agent);
    await act(async () => render());
    expect(observed.state).toBe('thinking');
    expect(observed.isConnected).toBe(true);
  });

  it('does not select a different ready agent or infer readiness from room connection', async () => {
    await act(async () => render());
    await join(participant('other-agent', 'listening'));
    expect(observed.state).toBe('connecting');
    expect(observed.isConnected).toBe(false);
    await join(participant(identity));
    expect(observed.state).toBe('initializing');
    expect(observed.isConnected).toBe(false);
  });

  it('follows native attribute updates through preparing, speaking, and stopped states', async () => {
    await act(async () => render());
    const agent = participant(identity, 'listening');
    await join(agent);
    for (const state of ['thinking', 'speaking', 'listening']) {
      await changeState(agent, state);
      expect(observed.state).toBe(state);
      expect(observed.isConnected).toBe(true);
    }
  });

  it('does not time out a 23-second reply and keeps later long replies available', async () => {
    await act(async () => render());
    const agent = participant(identity, 'thinking');
    await join(agent);
    await act(async () => { await vi.advanceTimersByTimeAsync(23_000); });
    expect(observed.state).toBe('thinking');
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(observed.isConnected).toBe(true);
  });

  it('fails missing initialization only after 60 seconds and recovers on actual readiness', async () => {
    await act(async () => render());
    const agent = participant(identity, 'initializing');
    await join(agent);
    await act(async () => { await vi.advanceTimersByTimeAsync(59_000); });
    expect(observed.state).toBe('initializing');
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(observed.state).toBe('failed');
    await changeState(agent, 'listening');
    expect(observed.state).toBe('listening');
    expect(observed.isConnected).toBe(true);
  });

  it('drops readiness when the exact participant leaves, despite cached attributes', async () => {
    await act(async () => render());
    const agent = participant(identity, 'listening');
    await join(agent);
    await act(async () => {
      room.remoteParticipants.delete(identity);
      room.emit(RoomEvent.ParticipantDisconnected, agent);
    });
    expect(observed.isConnected).toBe(false);
    expect(observed.state).toBe('connecting');
  });
});
