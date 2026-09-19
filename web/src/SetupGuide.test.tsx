// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { SetupGuide } from './SetupGuide';

let host: HTMLDivElement;
let root: Root;
const fetcher = vi.fn();
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal('fetch', fetcher);
  fetcher.mockReset();
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
const render = async () => { await act(async () => root.render(<SetupGuide onVoices={() => {}} onSettings={() => {}} />)); };
const button = (text: string) => [...host.querySelectorAll('button')].find(item => item.textContent === text)!;
it('checks setup only when asked and keeps local voice practice separate from cloud accounts', async () => {
  fetcher.mockResolvedValue({ ok: true, json: async () => ({ version: 1, checks: [
    { id: 'qwen', status: 'pass', message: 'Local voice model found.', action: '' },
    { id: 'livekit', status: 'missing', message: 'Conversation connection needs setup.', action: 'Configure your LiveKit project.' },
  ] }) });
  await render();
  expect(fetcher).not.toHaveBeenCalled();
  expect(button('First conversation guide')).toBeDefined();
  await act(async () => button('First conversation guide').click());
  expect(host.textContent).toContain('Local voice model found.');
  expect(host.textContent).toContain('Configure your LiveKit project.');
  expect(host.textContent).toContain('without a LiveKit or AI account');
});
it('clears old results when a recheck fails rather than presenting stale passes', async () => {
  fetcher.mockResolvedValueOnce({ ok: true, json: async () => ({ version: 1, checks: [{ id: 'qwen', status: 'pass', message: 'Model verified in old check', action: '' }] }) });
  await render();
  await act(async () => button('First conversation guide').click());
  fetcher.mockRejectedValue(new Error('offline'));
  await act(async () => button('Check again').click());
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).not.toContain('Model verified in old check');
});

it.each([null, {version: 1, checks: []}, {version: 1, checks: [{id: {}, status: 'pass', message: 'Found', action: ''}]}, {version: 1, checks: [{id: 'qwen', status: 'pass', message: {}, action: ''}]}])('rejects malformed setup reports without presenting success: %j', async (report) => {
  fetcher.mockResolvedValue({ ok: true, json: async () => report });
  await render();
  await act(async () => button('First conversation guide').click());
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.querySelector('.setup-checks')).toBeNull();
});
it('puts actionable failures first and keeps found checks in a disclosure', async () => {
  fetcher.mockResolvedValue({ ok: true, json: async () => ({version: 1, checks: [
    { id: 'qwen', status: 'pass', message: 'Model found', action: '' },
    { id: 'voice', status: 'missing', message: 'Select a voice', action: 'Open voice library' },
    { id: 'runtime', status: 'unverified', message: 'Synthesis not tested', action: '' },
  ]}) });
  await render();
  await act(async () => button('First conversation guide').click());
  expect(host.textContent).toContain('1 item needs setup');
  expect(host.querySelector('.setup-checks li')?.textContent).toContain('Select a voice');
  expect(host.querySelector('details')?.textContent).toContain('Model found');
  expect(host.querySelector('details')?.open).toBe(false);
});
