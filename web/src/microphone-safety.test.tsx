// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { ConnectionState, RoomEvent } from 'livekit-client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readyStatus, testGrant } from './test-fixtures';
import { useStudio } from './useStudio';

const request = vi.hoisted(() => vi.fn());
vi.mock('./api', async (original) => ({ ...await original<typeof import('./api')>(), api: request }));

let root: Root;
let host: HTMLDivElement;
let studio: ReturnType<typeof useStudio>;
let getUserMedia: ReturnType<typeof vi.fn>;
function Harness() { studio = useStudio(); return null; }
async function mount() {
  await act(async () => root.render(<Harness />));
  const room = studio.session.room;
  // Only the network boundary is replaced. useSession, Room construction, local
  // participant state, and setMicrophoneEnabled(false) run the real installed SDK.
  vi.spyOn(room, 'connect').mockImplementation(async () => {
    room.state = ConnectionState.Connected;
    room.emit(RoomEvent.ConnectionStateChanged, room.state);
  });
}

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  getUserMedia = vi.fn().mockRejectedValue(new Error('Unexpected microphone access in a typed-first test'));
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia, addEventListener: vi.fn(), removeEventListener: vi.fn() },
  });
  request.mockImplementation(async (path) => {
    if (path === 'status') return readyStatus;
    if (path === 'session') return testGrant;
    return { phase: 'idle' };
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
});

describe('native SDK typed-first microphone safety', () => {
  it('mounts and starts through Room.connect without any getUserMedia call', async () => {
    await mount();
    const participant = studio.session.room.localParticipant;
    const microphone = vi.spyOn(participant, 'setMicrophoneEnabled');
    expect(getUserMedia).not.toHaveBeenCalled();
    await act(async () => { await studio.start(); });
    expect(microphone).toHaveBeenCalledExactlyOnceWith(false);
    expect(participant.isMicrophoneEnabled).toBe(false);
    expect(studio.session.room.connect).toHaveBeenCalledExactlyOnceWith(testGrant.serverUrl, testGrant.participantToken);
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it('resets the native microphone path on a second explicit session start', async () => {
    await mount();
    const room = studio.session.room;
    const microphone = vi.spyOn(room.localParticipant, 'setMicrophoneEnabled');
    // Disconnect is an instance method. Replace only signaling teardown for this
    // disconnected test room; no fake "mic enabled" flags determine the result.
    vi.spyOn(room, 'disconnect').mockImplementation(async () => {
      room.state = ConnectionState.Disconnected;
      room.emit(RoomEvent.ConnectionStateChanged, room.state);
      room.emit(RoomEvent.Disconnected);
    });
    await act(async () => { await studio.start(); });
    await act(async () => { await studio.end(); });
    await act(async () => { await studio.start(); });
    expect(microphone.mock.calls).toEqual([[false], [false]]);
    expect(room.connect).toHaveBeenCalledTimes(2);
    expect(room.localParticipant.isMicrophoneEnabled).toBe(false);
    expect(getUserMedia).not.toHaveBeenCalled();
  });
});
