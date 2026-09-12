// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { VoiceEnrollment } from './VoiceEnrollment';
const mocks = vi.hoisted(() => ({ recorder: {} as Record<string, unknown> }));
vi.mock('./useVoiceRecorder', () => ({ useVoiceRecorder: () => mocks.recorder }));
let root: Root;
let host: HTMLDivElement;
const saved = vi.fn().mockResolvedValue(undefined);
const cancel = vi.fn();
const fetchMock = vi.fn();
beforeEach(async () => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mocks.recorder = { phase: 'review', recording: { blob: new Blob(['wav'], { type: 'audio/wav' }), measurements: { duration: 8 }, problem: null }, cancel, duration: 8, levels: [], error: null };
  vi.stubGlobal('fetch', fetchMock.mockResolvedValue(new Response('{}')));
  URL.createObjectURL = vi.fn().mockReturnValue('blob:private-preview');
  URL.revokeObjectURL = vi.fn();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<VoiceEnrollment guidedText="Read this exactly." locked={false} onSaved={saved} onCancel={vi.fn()} />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function prepare() {
  await act(async () => {
    const input = host.querySelector('input:not([type=checkbox])')!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'My voice');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => host.querySelectorAll<HTMLInputElement>('input[type=checkbox]').forEach((input) => input.click()));
}
it('does not send audio before explicit authorization and transcript verification', async () => {
  expect(host.querySelector<HTMLButtonElement>('button[type=submit]')!.disabled).toBe(true);
  expect(fetchMock).not.toHaveBeenCalled();
  await prepare();
  await act(async () => host.querySelector<HTMLButtonElement>('button[type=submit]')!.click());
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe('/api/voices');
  expect(init.body).toBeInstanceOf(FormData);
  expect(init.body.get('name')).toBe('My voice');
  expect(init.body.get('transcript')).toBe('Read this exactly.');
  expect(init.body.get('authorized')).toBe('true');
  expect(init.body.get('audio').type).toBe('audio/wav');
  expect(init.headers).toEqual({ 'X-Voicebox-Studio': '1' });
  expect(saved).toHaveBeenCalledOnce();
});
it('deduplicates simultaneous save events', async () => {
  await prepare();
  fetchMock.mockReturnValue(new Promise(() => {}));
  await act(async () => {
    host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    host.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
  expect(fetchMock).toHaveBeenCalledOnce();
});
it('revokes the temporary preview on unmount', async () => {
  await act(async () => root.render(null));
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:private-preview');
});
