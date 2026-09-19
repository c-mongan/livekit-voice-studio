// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { StudioSettings } from './StudioSettings';
import { deferred, readyStatus } from './test-fixtures';
import type { StudioStatus } from './api';

let root: Root;
let host: HTMLDivElement;
let locked = false;
const changed = vi.fn();
const routingChanged = vi.fn();
const settings = {
  sttProvider: 'nemotron', llmProvider: 'copilot', llmModel: 'gpt-5.6-luna', reasoningEffort: 'low', voiceId: 'one',
  providers: {
    stt: [{ id: 'nemotron', label: 'Nemotron', available: true }, { id: 'azure', label: 'Azure', available: false, reason: 'Not configured' }],
    llm: [{ id: 'copilot', label: 'Copilot', available: true }, { id: 'codex', label: 'Codex', available: true }, { id: 'ollama', label: 'Ollama', available: true }, { id: 'openai-compatible', label: 'Custom endpoint', available: true }],
  },
};
const fetchMock = vi.fn();
let status: StudioStatus;
function render() { root.render(<StudioSettings locked={locked} status={status} onChanged={changed} onRoutingChanged={routingChanged} />); }
function button(text: string) {
  const element = [...host.querySelectorAll('button')].find((item) => item.textContent?.trim() === text);
  if (!element) throw new Error(`Missing button ${text}`);
  return element;
}
async function click(text: string) { await act(async () => button(text).click()); }
it('opens the voice library directly without a microphone request or mutation', async () => {
  expect(fetchMock).not.toHaveBeenCalled();
  await click('Voice library');
  expect(host.querySelector('[role=tab][aria-selected=true]')?.textContent).toBe('Voices');
  expect(host.querySelector('#panel-providers')).toBeNull();
  expect(button('Record a voice').disabled).toBe(false);
  expect(button('Record a voice').compareDocumentPosition(host.querySelector('.voice-library')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(fetchMock.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true);
  await click('Close');
  expect(document.activeElement).toBe(button('Voice library'));
});
it('keeps provider settings directly accessible and returns focus to their own trigger', async () => {
  await click('Settings');
  expect(host.querySelector('[role=tab][aria-selected=true]')?.textContent).toBe('Connection & AI');
  const dialog = host.querySelector('dialog')!;
  await act(async () => dialog.dispatchEvent(new Event('cancel', { cancelable: true })));
  expect(dialog.open).toBe(false);
  expect(document.activeElement).toBe(button('Settings'));
  await click('Voice library');
  expect(host.querySelector('[role=tab][aria-selected=true]')?.textContent).toBe('Voices');
});
it('orders voice-first tabs with keyboard navigation and a single tab stop', async () => {
  await click('Voice library');
  const tabs = host.querySelector('[role=tablist]')!;
  expect([...tabs.querySelectorAll('[role=tab]')].map((tab) => tab.textContent)).toEqual(['Voices', 'Connection & AI']);
  for (const [key, id] of [['End', 'providers'], ['Home', 'voices'], ['ArrowRight', 'providers'], ['ArrowLeft', 'voices']]) {
    await act(async () => tabs.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })));
    expect(document.activeElement?.id).toBe(`tab-${id}`);
    expect(tabs.querySelector('[aria-selected=true]')?.id).toBe(`tab-${id}`);
    expect(tabs.querySelectorAll('[tabindex="0"]')).toHaveLength(1);
  }
});
it('returns focus to the guide button after an externally requested tab closes', async () => {
  await click('Voice library'); await click('Close');
  const guideButton = document.createElement('button');
  document.body.append(guideButton); guideButton.focus();
  try {
    await act(async () => root.render(<StudioSettings locked={false} status={status} onChanged={changed} requestedTab="providers" />));
    expect(host.querySelector('[role=tab][aria-selected=true]')?.textContent).toBe('Connection & AI');
    button('Close').focus();
    await click('Close');
    expect(document.activeElement).toBe(guideButton);
  } finally { guideButton.remove(); }
});
beforeEach(async () => {
  locked = false;
  status = { ...readyStatus };
  vi.clearAllMocks();
  fetchMock.mockReset();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockImplementation(async (url: string) => new Response(JSON.stringify(url.endsWith('/settings') ? settings : {
    voices: [{ id: 'one', name: 'My voice', durationSeconds: 12, selected: true, source: 'local' }, { id: 'two', name: 'Second voice', durationSeconds: 9, selected: false, source: 'local' }],
    guidedText: 'Read this passage.',
  })));
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  HTMLDialogElement.prototype.close = function () { this.open = false; };
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  await act(async () => render());
});
it('offers generated audition separately from the original recording with accessible explicit playback', async () => {
  await click('Voice library');
  const original = [...host.querySelectorAll('button')].find((item) => item.textContent === 'Original recording')!;
  await act(async () => original.click());
  const audio = host.querySelector('audio')!;
  expect(audio.getAttribute('aria-label')).toBe('Original recording of My voice');
  expect(audio.autoplay).toBe(false);
  expect(audio.preload).toBe('none');
  const preview = [...host.querySelectorAll('button')].find((item) => item.textContent === 'Audition clone')!;
  await act(async () => preview.click());
  expect(host.querySelector('audio')).toBeNull();
  expect(host.querySelector('textarea')?.maxLength).toBe(300);
  expect(host.textContent).toContain('not a replay of the original recording');
  expect(host.querySelector('textarea')?.closest('label')?.textContent).toContain('Audition text');
  expect(button('Generate sample').disabled).toBe(false);
  expect(fetchMock.mock.calls.some(([url]) => url === '/api/audition')).toBe(false);
});
it('allows local auditions without LiveKit or reasoning credentials', async () => {
  status = { ...status, ready: false, livekit: { configured: false }, problems: ['Configure LiveKit and reasoning.'] };
  await act(async () => render());
  await click('Voice library');
  await act(async () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Audition clone')!.click());
  expect(button('Generate sample').disabled).toBe(false);
  expect(host.textContent).toContain('without those credentials');
});
it.each(['starting', 'active', 'draining', 'blocked'] as const)('does not generate while the broker phase is %s', async (phase) => {
  status = { ...status, phase };
  await act(async () => render());
  await click('Voice library');
  await act(async () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Audition clone')!.click());
  expect(button('Generate sample').disabled).toBe(true);
  expect(host.textContent).toContain('wait for cleanup');
});
it('lets the backend validate local setup and shows its actionable 503 error', async () => {
  await click('Voice library');
  await act(async () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Audition clone')!.click());
  expect(button('Generate sample').disabled).toBe(false);
  fetchMock.mockResolvedValueOnce(new Response('{"message":"Install the local Qwen model first."}', { status: 503 }));
  await click('Generate sample');
  expect(host.querySelector('[role=alert]')?.textContent).toContain('Install the local Qwen model first.');
  expect(host.querySelector('audio')).toBeNull();
  expect(button('Generate sample').disabled).toBe(false);
});
it('revokes generated audio on text edit, voice switch and close without autoplay', async () => {
  URL.createObjectURL = vi.fn(() => 'blob:generated');
  URL.revokeObjectURL = vi.fn();
  await click('Voice library');
  await act(async () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Audition clone')!.click());
  fetchMock.mockImplementation(async (url: string) => url.endsWith('/audio')
    ? new Response(new Blob(['wav']), { headers: { 'Content-Type': 'audio/wav' } })
    : new Response(JSON.stringify(url.endsWith('/status') ? { auditionId: 'job', voiceId: 'one', phase: 'idle', state: 'ready' }
      : { auditionId: 'job', voiceId: 'one', phase: 'starting' })));
  await click('Generate sample');
  const audio = host.querySelector('audio')!;
  expect(audio.getAttribute('aria-label')).toBe('Generated clone of My voice');
  expect(audio.autoplay).toBe(false);
  expect(audio.getAttribute('controlsList')).toBe('nodownload');
  expect(host.querySelector('a[download]')).toBeNull();
  const textarea = host.querySelector('textarea')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, 'Different words.');
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  });
  expect(host.querySelector('audio')).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
  await click('Generate sample');
  await act(async () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Audition clone')!.click());
  expect(host.querySelector('audio')).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
  await click('Close');
  expect(host.querySelector('textarea')).toBeNull();
});
it('locks other mutations immediately and ends a pending audition when the drawer closes', async () => {
  const pending = deferred<Response>();
  await click('Voice library');
  await act(async () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Audition clone')!.click());
  fetchMock.mockImplementationOnce(() => pending.promise);
  await click('Generate sample');
  expect(button('Record a voice').disabled).toBe(true);
  expect(button('Use next session').disabled).toBe(true);
  expect([...host.querySelectorAll('button')].filter((item) => item.textContent === 'Rename').every((item) => item.disabled)).toBe(true);
  await click('Close');
  fetchMock.mockResolvedValue(new Response('{"phase":"idle"}'));
  await act(async () => pending.resolve(new Response('{"auditionId":"late","voiceId":"one","phase":"starting"}')));
  expect(fetchMock.mock.calls.find(([url]) => url.endsWith('/end'))?.[1].body).toBe('{"auditionId":"late"}');
});
it('shows invalidated cleanup on reopening the drawer instead of leaving it busy forever', async () => {
  const start = deferred<Response>();
  const initial = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string) => url === '/api/audition' ? start.promise
    : url.endsWith('/end') ? Promise.resolve(new Response('{"message":"Invalid handle."}', { status: 404 }))
      : initial(url));
  await click('Voice library');
  await act(async () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Audition clone')!.click());
  await click('Generate sample'); await click('Close');
  await act(async () => start.resolve(new Response('{"auditionId":"expired","voiceId":"one","phase":"starting"}')));
  await click('Voice library');
  expect(host.querySelector('[role=alert]')?.textContent).toContain('expired or was invalidated');
  expect(button('Record a voice').disabled).toBe(false);
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/end'))).toHaveLength(1);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
it('loads configuration only on demand, never asks for the microphone on mount', async () => {
  expect(fetchMock).not.toHaveBeenCalled();
  await click('Settings');
  expect(host.textContent).toContain('Remote model');
  expect(host.querySelector('option[value=azure]')?.hasAttribute('disabled')).toBe(true);
  expect(host.textContent).toContain('Not configured');
});
it('saves only allowed configuration fields with a CSRF header', async () => {
  await click('Settings');
  await click('Save settings');
  const call = fetchMock.mock.calls.find(([, init]) => init.method === 'POST')!;
  expect(call[0]).toBe('/api/settings');
  expect(JSON.parse(call[1].body)).toEqual({ sttProvider: 'nemotron', llmProvider: 'copilot', llmModel: 'gpt-5.6-luna', reasoningEffort: 'low', codexRestrictedApproved: false, livekitMode: 'configured', llmBaseUrl: 'http://127.0.0.1:11434/v1' });
  expect(call[1].headers['X-Voicebox-Studio']).toBe('1');
  expect(changed).toHaveBeenCalled();
  expect(routingChanged).toHaveBeenCalledOnce();
});
it('requires explicit restricted Codex consent and resets it when the provider changes', async () => {
  await click('Settings');
  const select = host.querySelectorAll('select')[2];
  await act(async () => { select.value = 'codex'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(host.textContent).toContain('This is not tool-free mode');
  expect(button('Save settings').disabled).toBe(true);
  const checkbox = host.querySelector<HTMLInputElement>('input[type=checkbox]')!;
  await act(async () => checkbox.click());
  expect(button('Save settings').disabled).toBe(false);
  await act(async () => { select.value = 'copilot'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  await act(async () => { select.value = 'codex'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(host.querySelector<HTMLInputElement>('input[type=checkbox]')!.checked).toBe(false);
  expect(button('Save settings').disabled).toBe(true);
});
it('blocks all configuration mutations while a session is active', async () => {
  locked = true;
  await act(async () => render());
  await click('Settings');
  expect(button('Save settings').disabled).toBe(true);
  expect([...host.querySelectorAll('select')].every((select) => select.matches(':disabled'))).toBe(true);
  await click('Save settings');
  expect(fetchMock.mock.calls.some(([, init]) => init.method === 'POST')).toBe(false);
  await click('Voices');
  expect(button('Record a voice').disabled).toBe(true);
  expect(button('Use next session').disabled).toBe(true);
  expect(host.textContent).toContain('End the conversation');
});
it('never deletes automatically and requires explicit confirmation for an unselected voice', async () => {
  await click('Voice library');
  const deletes = [...host.querySelectorAll<HTMLButtonElement>('button')].filter((item) => item.textContent === 'Delete');
  expect(deletes[0].disabled).toBe(true);
  await act(async () => deletes[1].click());
  expect(fetchMock.mock.calls.some(([, init]) => init.method === 'DELETE')).toBe(false);
  await click('Delete voice permanently');
  const call = fetchMock.mock.calls.find(([, init]) => init.method === 'DELETE')!;
  expect(call[0]).toBe('/api/voices/two');
  expect(JSON.parse(call[1].body)).toEqual({ confirm: true });
});
it('keeps failed saves visibly unsuccessful without exposing raw diagnostics', async () => {
  await click('Settings');
  fetchMock.mockResolvedValueOnce(new Response('{"message":"End the active session first."}', { status: 409 }));
  await click('Save settings');
  expect(host.querySelector('[role=alert]')?.textContent).toContain('End the active session first');
  expect(changed).not.toHaveBeenCalled();
  expect(routingChanged).not.toHaveBeenCalled();
});

it('saves local transport independently with an editable Ollama model and endpoint', async () => {
  await click('Settings');
  const selects = host.querySelectorAll('select');
  expect(selects[0].value).toBe('configured');
  await act(async () => { selects[0].value = 'local'; selects[0].dispatchEvent(new Event('change', { bubbles: true })); });
  await act(async () => { selects[2].value = 'ollama'; selects[2].dispatchEvent(new Event('change', { bubbles: true })); });
  const model = host.querySelector<HTMLInputElement>('input[name=llmModel]')!;
  expect(model.value).toBe('qwen3:1.7b');
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(model, 'my-local-model');
    model.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await click('Save settings');
  const body = JSON.parse(fetchMock.mock.calls.find(([, init]) => init.method === 'POST')![1].body);
  expect(body).toMatchObject({ livekitMode: 'local', llmProvider: 'ollama', llmModel: 'my-local-model', reasoningEffort: 'none', sttProvider: 'nemotron', llmBaseUrl: 'http://127.0.0.1:11434/v1' });
});
it('explains custom endpoint privacy and keeps credentials outside the form', async () => {
  await click('Settings');
  const select = host.querySelectorAll('select')[2];
  await act(async () => { select.value = 'openai-compatible'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(host.textContent).toContain('VOICEBOX_CUSTOM_LLM_API_KEY');
  expect(host.querySelector('input[name=llmBaseUrl]')).not.toBeNull();
  expect(host.querySelector('input[type=password]')).toBeNull();
});
it('preserves a custom Ollama port through a cloud provider round trip', async () => {
  const original = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string) => url.endsWith('/settings') ? Promise.resolve(new Response(JSON.stringify({ ...settings, llmProvider: 'ollama', llmModel: 'qwen3:1.7b', llmBaseUrl: 'http://127.0.0.1:11435/v1' }))) : original(url));
  await click('Settings');
  const select = host.querySelectorAll('select')[2];
  for (const provider of ['copilot', 'ollama']) {
    await act(async () => { select.value = provider; select.dispatchEvent(new Event('change', { bubbles: true })); });
  }
  expect(host.querySelector<HTMLInputElement>('input[name=llmBaseUrl]')!.value).toBe('http://127.0.0.1:11435/v1');
  await click('Save settings');
  expect(JSON.parse(fetchMock.mock.calls.find(([, init]) => init.method === 'POST')![1].body).llmBaseUrl).toBe('http://127.0.0.1:11435/v1');
});
it.each(['https://models.example/v1', 'not a URL', 'http://user:secret@localhost:11435/v1', 'http://localhost:11435/v1?token=secret'])('resets a remote or invalid custom endpoint when choosing Ollama: %s', async (endpoint) => {
  const original = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation((url: string) => url.endsWith('/settings') ? Promise.resolve(new Response(JSON.stringify({ ...settings, llmProvider: 'openai-compatible', llmModel: 'custom-model', llmBaseUrl: endpoint }))) : original(url));
  await click('Settings');
  const select = host.querySelectorAll('select')[2];
  await act(async () => { select.value = 'ollama'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(host.querySelector<HTMLInputElement>('input[name=llmBaseUrl]')!.value).toBe('http://127.0.0.1:11434/v1');
});
