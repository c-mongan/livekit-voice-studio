// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MicrophoneCheck } from './MicrophoneCheck';
import { deferred } from './test-fixtures';
let root: Root;
let host: HTMLDivElement;
const track = { stop: vi.fn(), onended: null as (() => void) | null };
const stream = { getTracks: () => [track] };
const permission = vi.fn(), close = vi.fn(), createUrl = vi.fn(), revokeUrl = vi.fn();
const resume = vi.fn();
let node: { port: { onmessage: ((event: { data: Float32Array }) => void) | null }; connect: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> };
async function click(label: string) {
  const button = [...host.querySelectorAll('button')].find((item) => item.textContent === label);
  expect(button, label).toBeTruthy(); await act(async () => button!.click());
}
async function audio(seconds = 1, amplitude = .1) {
  await act(async () => node.port.onmessage?.({ data: new Float32Array(24000 * seconds).fill(amplitude) }));
}
beforeEach(async () => {
  vi.useFakeTimers(); vi.clearAllMocks(); Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: permission } });
  permission.mockResolvedValue(stream); close.mockResolvedValue(undefined); resume.mockResolvedValue(undefined); createUrl.mockReturnValue('blob:local-test');
  vi.stubGlobal('AudioContext', class {
    sampleRate = 24000; close = close; resume = resume; destination = {};
    audioWorklet = { addModule: async () => {} };
    createMediaStreamSource() { return { connect: vi.fn(), disconnect: vi.fn() }; }
  });
  vi.stubGlobal('AudioWorkletNode', class {
    port = { onmessage: null }; connect = vi.fn(); disconnect = vi.fn(); constructor() { node = this; }
  });
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL: createUrl, revokeObjectURL: revokeUrl }));
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<MicrophoneCheck />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe('local microphone check', () => {
  it('requires an explicit start and creates a local replay after stopping', async () => {
    expect(permission).not.toHaveBeenCalled(); await click('Test microphone');
    expect(host.textContent).toContain('Microphone on'); await audio(); await click('Stop test');
    expect(track.stop).toHaveBeenCalledOnce(); expect(host.textContent).toContain('Microphone off');
    const player = host.querySelector('audio')!;
    expect(player.src).toBe('blob:local-test'); expect(player.controls).toBe(true);
    expect(createUrl.mock.calls[0][0].size).toBe(48044);
  });
  it('bounds captured audio at eight seconds even for a large chunk', async () => {
    await click('Test microphone'); await audio(9);
    expect(track.stop).toHaveBeenCalledOnce(); expect(createUrl.mock.calls[0][0].size).toBe(384044);
  });
  it('releases capture after eight seconds even without audio', async () => {
    await click('Test microphone'); await act(async () => vi.advanceTimersByTime(8000));
    expect(track.stop).toHaveBeenCalledOnce(); expect(host.querySelector('[role="alert"]')?.textContent).toContain('No audio');
  });
  it('reports quiet audio without an enrollment minimum duration', async () => {
    await click('Test microphone'); await audio(1, 0); await click('Stop test');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('quiet'); expect(host.querySelector('audio')).not.toBeNull();
  });
  it('reports denied permission without raw device errors', async () => {
    permission.mockRejectedValueOnce(new DOMException('private identifier', 'NotAllowedError')); await click('Test microphone');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Allow microphone'); expect(host.textContent).not.toContain('private identifier');
  });
  it.each(['cancel', 'unmount'])('releases late permission after %s', async (action) => {
    const pending = deferred<typeof stream>(); permission.mockReturnValueOnce(pending.promise); await click('Test microphone');
    if (action === 'cancel') await click('Cancel test'); else await act(async () => root.render(null));
    await act(async () => pending.resolve(stream)); expect(track.stop).toHaveBeenCalledOnce(); expect(createUrl).not.toHaveBeenCalled();
  });
  it.each(['cancel', 'unmount', 'disable', 'pagehide'])('releases active capture on %s', async (action) => {
    await click('Test microphone');
    if (action === 'cancel') await click('Cancel test');
    if (action === 'unmount') await act(async () => root.render(null));
    if (action === 'disable') await act(async () => root.render(<MicrophoneCheck disabled />));
    if (action === 'pagehide') await act(async () => window.dispatchEvent(new Event('pagehide')));
    expect(track.stop).toHaveBeenCalledOnce(); expect(close).toHaveBeenCalledOnce(); expect(node.port.onmessage).toBeNull(); expect(createUrl).not.toHaveBeenCalled();
  });
  it('shows capture is on while audio initialization is pending and still permits stopping', async () => {
    const pending = deferred<void>(); resume.mockReturnValueOnce(pending.promise);
    await click('Test microphone'); expect(host.textContent).toContain('Microphone on');
    await click('Stop test'); expect(track.stop).toHaveBeenCalledOnce();
    await act(async () => pending.resolve()); expect(host.querySelector('audio')).toBeNull();
  });
  it('revokes replay URLs on retry and unmount', async () => {
    await click('Test microphone'); await audio(); await click('Stop test'); await click('Test microphone');
    expect(revokeUrl).toHaveBeenCalledWith('blob:local-test'); await audio(); await click('Stop test');
    await act(async () => root.render(null)); expect(revokeUrl).toHaveBeenCalledTimes(2);
  });
});
