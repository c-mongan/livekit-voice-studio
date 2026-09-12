// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVoiceRecorder } from './useVoiceRecorder';
import { deferred } from './test-fixtures';

let root: Root;
let host: HTMLDivElement;
let recorder: ReturnType<typeof useVoiceRecorder>;
const track = { stop: vi.fn(), onended: null as (() => void) | null };
const stream = { getTracks: () => [track] };
const permission = vi.fn();
const close = vi.fn();
const resume = vi.fn();
const addModule = vi.fn();
let node: { port: { onmessage: ((event: { data: Float32Array }) => void) | null }; connect: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> };
function Probe() { recorder = useVoiceRecorder(); return null; }
beforeEach(async () => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: permission } });
  permission.mockResolvedValue(stream); close.mockResolvedValue(undefined); resume.mockResolvedValue(undefined); addModule.mockResolvedValue(undefined);
  vi.stubGlobal('AudioContext', class {
    sampleRate = 24000;
    close = close; resume = resume; destination = {};
    audioWorklet = { addModule };
    createMediaStreamSource() { return { connect: vi.fn(), disconnect: vi.fn() }; }
  });
  vi.stubGlobal('AudioWorkletNode', class {
    port = { onmessage: null }; connect = vi.fn(); disconnect = vi.fn();
    constructor() { node = this; }
  });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<Probe />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
describe('recording microphone lifecycle', () => {
  it('never captures on mount and stops all tracks immediately on cancel', async () => {
    expect(permission).not.toHaveBeenCalled();
    await act(async () => recorder.start());
    expect(recorder.phase).toBe('recording');
    await act(async () => recorder.cancel());
    expect(track.stop).toHaveBeenCalledOnce();
    expect(close).toHaveBeenCalledOnce();
    expect(node.port.onmessage).toBeNull();
    expect(recorder.recording).toBeNull();
  });
  it('stops a late microphone grant after permission is cancelled', async () => {
    const pending = deferred<typeof stream>();
    permission.mockReturnValueOnce(pending.promise);
    let start: Promise<void>;
    await act(async () => { start = recorder.start(); });
    await act(async () => recorder.cancel());
    await act(async () => { pending.resolve(stream); await start; });
    expect(track.stop).toHaveBeenCalledOnce();
    expect(addModule).not.toHaveBeenCalled();
    expect(recorder.phase).toBe('idle');
  });
  it('stops on unmount and discards temporary samples', async () => {
    await act(async () => recorder.start());
    await act(async () => root.render(null));
    expect(track.stop).toHaveBeenCalledOnce();
    expect(node.port.onmessage).toBeNull();
  });
  it('ends at exactly 30 seconds, not a delayed wall-clock timeout', async () => {
    await act(async () => recorder.start());
    await act(async () => node.port.onmessage?.({ data: new Float32Array(24000 * 31).fill(.1) }));
    expect(recorder.duration).toBe(30);
    expect(recorder.recording?.blob.size).toBe(44 + 30 * 24000 * 2);
    expect(recorder.recording?.problem).toBeNull();
    expect(track.stop).toHaveBeenCalledOnce();
  });
  it('retains a silent sample for review but marks it invalid', async () => {
    await act(async () => recorder.start());
    await act(async () => { node.port.onmessage?.({ data: new Float32Array(24000 * 6) }); recorder.stop(); });
    expect(recorder.recording?.problem).toContain('quiet');
  });
  it('handles permission denial without exposing device diagnostics', async () => {
    permission.mockRejectedValueOnce(new Error('private device ID'));
    await act(async () => recorder.start());
    expect(recorder.error).toContain('Allow microphone access');
    expect(recorder.error).not.toContain('private device ID');
    expect(recorder.phase).toBe('idle');
  });
  it('releases capture if the worklet cannot load', async () => {
    addModule.mockRejectedValueOnce(new Error('CSP'));
    await act(async () => recorder.start());
    expect(track.stop).toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
    expect(recorder.error).toContain('Web Audio');
  });
  it('stops and clears the recording on pagehide', async () => {
    await act(async () => recorder.start());
    await act(async () => window.dispatchEvent(new Event('pagehide')));
    expect(track.stop).toHaveBeenCalledOnce();
    expect(recorder.phase).toBe('idle');
    expect(recorder.recording).toBeNull();
  });
});
