// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useVoiceAudition } from './useVoiceAudition';
import { deferred } from './test-fixtures';

let root: Root;
let host: HTMLDivElement;
let audition: ReturnType<typeof useVoiceAudition>;
const fetchMock = vi.fn();
const busy = vi.fn();
const refresh = vi.fn();
function Harness() { audition = useVoiceAudition(busy, refresh); return null; }
const json = (data: object, status = 200) => new Response(JSON.stringify(data), { status });
beforeEach(async () => {
  vi.useFakeTimers(); vi.clearAllMocks();
  refresh.mockReset(); refresh.mockResolvedValue(undefined);
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal('fetch', fetchMock);
  URL.createObjectURL = vi.fn(() => 'blob:generated');
  URL.revokeObjectURL = vi.fn();
  fetchMock.mockImplementation(async (url: string) => url.endsWith('/audio')
    ? new Response(new Blob(['wav'], { type: 'audio/wav' }), { headers: { 'Content-Type': 'audio/wav' } })
    : json(url.endsWith('/status') ? { auditionId: 'job', voiceId: 'one', phase: 'idle', state: 'ready', message: null }
      : url.endsWith('/end') ? { phase: 'idle' } : { auditionId: 'job', voiceId: 'one', phase: 'starting' }));
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<Harness />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(1000); }); }
it('generates without changing selected voice and only exposes a memory WAV after drain', async () => {
  await act(async () => audition.generate('one', 'A fresh sentence.'));
  await flush();
  expect(audition.phase).toBe('ready');
  expect(audition.url).toBe('blob:generated');
  const start = fetchMock.mock.calls.find(([url]) => url === '/api/audition')!;
  expect(JSON.parse(start[1].body)).toEqual({ voiceId: 'one', text: 'A fresh sentence.' });
  expect(start[1].headers['X-Voicebox-Studio']).toBe('1');
  expect(fetchMock.mock.calls.some(([url]) => url === '/api/settings')).toBe(false);
  expect(busy).toHaveBeenLastCalledWith(false);
});
it('never downloads before confirmed drain even when state says ready', async () => {
  fetchMock.mockImplementation(async (url: string) => json(url.endsWith('/status')
    ? { auditionId: 'job', voiceId: 'one', phase: 'draining', state: 'ready' }
    : { auditionId: 'job', voiceId: 'one', phase: 'starting' }));
  await act(async () => audition.generate('one', 'Hello.'));
  await flush();
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/audio'))).toBe(false);
  expect(audition.busy).toBe(true);
});
it('cancels a late start response after close or focus change and suppresses stale results', async () => {
  const start = deferred<Response>();
  fetchMock.mockImplementationOnce(() => start.promise);
  await act(async () => audition.generate('one', 'Hello.'));
  await act(async () => audition.cancel());
  await act(async () => start.resolve(json({ auditionId: 'late', voiceId: 'one', phase: 'starting' })));
  await flush();
  expect(fetchMock.mock.calls.find(([url]) => url.endsWith('/end'))?.[1].body).toBe('{"auditionId":"late"}');
  expect(audition.url).toBeNull();
  expect(audition.phase).toBe('idle');
});
it('ends a start that resolves after unmount', async () => {
  const start = deferred<Response>();
  fetchMock.mockImplementationOnce(() => start.promise);
  await act(async () => audition.generate('one', 'Hello.'));
  await act(async () => root.unmount());
  root = createRoot(host);
  await act(async () => start.resolve(json({ auditionId: 'late', voiceId: 'one', phase: 'starting' })));
  expect(fetchMock.mock.calls.find(([url]) => url.endsWith('/end'))?.[1].body).toBe('{"auditionId":"late"}');
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});
it('revokes ready audio on invalidation', async () => {
  await act(async () => audition.generate('one', 'Hello.')); await flush();
  await act(async () => audition.cancel());
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:generated');
  expect(audition.url).toBeNull();
});
it('does not accept a stale voice identity from status', async () => {
  fetchMock.mockImplementation(async (url: string) => json(url.endsWith('/status')
    ? { auditionId: 'job', voiceId: 'other', phase: 'idle', state: 'ready' }
    : url.endsWith('/end') ? { phase: 'idle' } : { auditionId: 'job', voiceId: 'one', phase: 'starting' }));
  await act(async () => audition.generate('one', 'Hello.')); await flush();
  expect(audition.phase).toBe('error');
  expect(audition.url).toBeNull();
});
it('reports audio download failure without fake success', async () => {
  fetchMock.mockImplementation(async (url: string) => url.endsWith('/audio') ? json({ message: 'Audition expired. Generate another sample.' }, 410)
    : json(url.endsWith('/status') ? { auditionId: 'job', voiceId: 'one', phase: 'idle', state: 'ready' }
      : url.endsWith('/end') ? { phase: 'idle' } : { auditionId: 'job', voiceId: 'one', phase: 'starting' }));
  await act(async () => audition.generate('one', 'Hello.')); await flush();
  expect(audition.phase).toBe('error');
  expect(audition.error).toContain('expired');
  expect(audition.url).toBeNull();
});
it('keeps mutations locked until cancellation cleanup drains', async () => {
  let drained = false;
  fetchMock.mockImplementation(async (url: string) => json(url.endsWith('/status')
    ? { auditionId: 'job', voiceId: 'one', phase: drained ? 'idle' : 'draining', state: 'cancelled' }
    : url.endsWith('/end') ? { phase: 'draining' } : { auditionId: 'job', voiceId: 'one', phase: 'starting' }));
  await act(async () => audition.generate('one', 'Hello.'));
  await act(async () => audition.cancel()); await flush();
  expect(audition.busy).toBe(true);
  drained = true; await flush();
  expect(audition.busy).toBe(false);
});
it('rejects empty or overlong text without starting inference', async () => {
  await act(async () => { audition.generate('one', ' '); audition.generate('one', 'x'.repeat(301)); });
  expect(fetchMock).not.toHaveBeenCalled();
});
it('suppresses an audio response that arrives after cancellation', async () => {
  const audio = deferred<Response>();
  fetchMock.mockImplementation(async (url: string) => url.endsWith('/audio') ? audio.promise
    : json(url.endsWith('/status') ? { auditionId: 'job', voiceId: 'one', phase: 'idle', state: 'ready' }
      : url.endsWith('/end') ? { phase: 'idle' } : { auditionId: 'job', voiceId: 'one', phase: 'starting' }));
  await act(async () => audition.generate('one', 'Hello.'));
  await act(async () => audition.cancel());
  await act(async () => audio.resolve(new Response(new Blob(['wav']), { headers: { 'Content-Type': 'audio/wav' } })));
  await flush();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(audition.url).toBeNull();
});
it('cancels pending jobs on pagehide and prevents duplicate start gestures', async () => {
  const start = deferred<Response>();
  fetchMock.mockImplementationOnce(() => start.promise);
  await act(async () => { audition.generate('one', 'Hello.'); audition.generate('one', 'Again.'); });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await act(async () => window.dispatchEvent(new Event('pagehide')));
  await act(async () => start.resolve(json({ auditionId: 'late', voiceId: 'one', phase: 'starting' })));
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/end'))).toBe(true);
  expect(fetchMock.mock.calls.find(([url]) => url.endsWith('/end'))?.[1].keepalive).toBe(true);
  expect(audition.url).toBeNull();
});
it('shows server conflicts as errors without assuming an audition started', async () => {
  fetchMock.mockResolvedValueOnce(json({ message: 'End the active conversation first.' }, 409));
  await act(async () => audition.generate('one', 'Hello.'));
  expect(audition.error).toBe('End the active conversation first.');
  expect(audition.busy).toBe(false);
  expect(audition.url).toBeNull();
});
it('retains the mutation lock through cleanup errors and releases only on confirmed idle', async () => {
  const start = deferred<Response>();
  fetchMock.mockImplementationOnce(() => start.promise);
  await act(async () => audition.generate('one', 'Hello.'));
  await act(async () => audition.cancel());
  fetchMock.mockResolvedValue(json({ message: 'Cleanup unavailable. Check the local worker.' }, 503));
  await act(async () => start.resolve(json({ auditionId: 'job', voiceId: 'one', phase: 'starting' })));
  expect(audition.busy).toBe(true);
  expect(audition.error).toContain('Cleanup unavailable');
  expect(audition.url).toBeNull();
  fetchMock.mockResolvedValue(json({ phase: 'idle' }));
  await flush();
  expect(audition.busy).toBe(false);
  expect(audition.error).toBeNull();
});
it.each([
  ['status', 404], ['status', 410], ['end', 404], ['end', 410],
] as const)('stops obsolete %s polling on %s and refreshes before unlocking', async (endpoint, code) => {
  const start = deferred<Response>();
  const refreshed = deferred<void>();
  refresh.mockReturnValue(refreshed.promise);
  fetchMock.mockImplementation(async (url: string) => {
    if (url === '/api/audition') return start.promise;
    return json({ message: 'Old handle no longer exists.' }, code);
  });
  await act(async () => audition.generate('one', 'Hello.'));
  if (endpoint === 'end') await act(async () => audition.cancel());
  await act(async () => start.resolve(json({ auditionId: 'expired', voiceId: 'one', phase: 'starting' })));
  expect(fetchMock.mock.calls.some(([url]) => url === `/api/audition/${endpoint}`)).toBe(true);
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(audition.busy).toBe(true);
  expect(busy).toHaveBeenLastCalledWith(true);
  expect(audition.url).toBeNull();
  const requests = fetchMock.mock.calls.length;
  await flush(); await flush();
  expect(fetchMock).toHaveBeenCalledTimes(requests);
  await act(async () => refreshed.resolve());
  expect(audition.busy).toBe(false);
  expect(busy).toHaveBeenLastCalledWith(false);
  expect(audition.phase).toBe('error');
  expect(audition.error).toMatch(/expired|invalidated/i);
  await act(async () => root.unmount());
  root = createRoot(host);
  expect(fetchMock).toHaveBeenCalledTimes(requests);
});
it('discards downloaded audio if end reports ownership loss before delivery to the UI', async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url.endsWith('/audio')) return new Response(new Blob(['wav']), { headers: { 'Content-Type': 'audio/wav' } });
    if (url.endsWith('/end')) return json({ message: 'Expired.' }, 410);
    return json(url.endsWith('/status') ? { auditionId: 'job', voiceId: 'one', phase: 'idle', state: 'ready' }
      : { auditionId: 'job', voiceId: 'one', phase: 'starting' });
  });
  await act(async () => audition.generate('one', 'Hello.'));
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(audition.url).toBeNull();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(audition.phase).toBe('error');
});
it('retries failed public refresh without reusing the obsolete handle or releasing the gate', async () => {
  refresh.mockRejectedValueOnce(new Error('Offline')).mockResolvedValue(undefined);
  fetchMock.mockImplementation(async (url: string) => url === '/api/audition'
    ? json({ auditionId: 'expired', voiceId: 'one', phase: 'starting' })
    : json({ message: 'Invalid handle.' }, 404));
  await act(async () => audition.generate('one', 'Hello.'));
  expect(audition.busy).toBe(true);
  expect(busy).toHaveBeenLastCalledWith(true);
  expect(audition.error).toContain('Could not refresh');
  const requests = fetchMock.mock.calls.length;
  await flush();
  expect(refresh).toHaveBeenCalledTimes(2);
  expect(fetchMock).toHaveBeenCalledTimes(requests);
  expect(audition.busy).toBe(false);
  expect(audition.error).toContain('expired or was invalidated');
});
