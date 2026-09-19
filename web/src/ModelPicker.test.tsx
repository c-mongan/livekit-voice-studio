// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ModelPicker } from './ModelPicker';
import { deferred } from './test-fixtures';

let host: HTMLDivElement;
let root: Root;
const fetchMock = vi.fn();
const change = vi.fn();
const endpoint = 'http://127.0.0.1:11435/v1';
async function render(url = endpoint) {
  await act(async () => root.render(<ModelPicker provider="ollama" effort="none" endpoint={url} value="qwen3:1.7b" onChange={change} disabled={false} />));
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
}
function button(text: string) { return [...host.querySelectorAll('button')].find((b) => b.textContent === text)!; }
beforeEach(() => {
  vi.useFakeTimers(); fetchMock.mockReset(); change.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement('div'); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });
it('lists installed models without changing the saved choice and selects an alternative', async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ models: [{ id: 'qwen3:1.7b', efforts: ['none'] }, { id: 'small:latest', efforts: ['none'] }] })));
  await render();
  const select = host.querySelector('select')!;
  expect([...select.options].map(o => o.value)).toEqual(['qwen3:1.7b', 'small:latest']);
  expect(select.value).toBe('qwen3:1.7b'); expect(change).not.toHaveBeenCalled();
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ provider: 'ollama', endpoint });
  await act(async () => { select.value = 'small:latest'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(change).toHaveBeenCalledWith('small:latest', 'none');
});
it('keeps missing selections and explains an empty library', async () => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ models: [] })));
  await render();
  expect(host.textContent).toContain('No installed models');
  expect(host.querySelector('select')!.value).toBe('qwen3:1.7b');
  expect(change).not.toHaveBeenCalled();
});
it('shows a recoverable error and refreshes without changing settings', async () => {
  fetchMock.mockResolvedValueOnce(new Response('{"error":"Start Ollama and retry."}', { status: 503 }));
  await render(); expect(host.querySelector('[role=alert]')!.textContent).toContain('Start Ollama');
  fetchMock.mockResolvedValueOnce(new Response('{"models":[{"id":"small:latest","efforts":["none"]}]}'));
  await act(async () => button('Refresh models').click());
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  expect(host.querySelector('[role=alert]')).toBeNull();
  expect(host.textContent).toContain('small:latest');
  expect(change).not.toHaveBeenCalled();
});
it('ignores stale results when the endpoint changes', async () => {
  const old = deferred<Response>();
  fetchMock.mockReturnValueOnce(old.promise).mockResolvedValueOnce(new Response('{"models":[{"id":"new:model","efforts":["none"]}]}'));
  await render(); expect(host.textContent).toContain('Loading installed models');
  await render('http://127.0.0.1:11436/v1');
  await act(async () => old.resolve(new Response('{"models":[{"id":"old:model","efforts":["none"]}]}')));
  expect(host.textContent).toContain('new:model'); expect(host.textContent).not.toContain('old:model');
});
it('offers account models with their supported reasoning levels', async () => {
  fetchMock.mockResolvedValue(new Response('{"models":[{"id":"other-model","efforts":["medium","high"]}]}'));
  await act(async () => root.render(<ModelPicker provider="copilot" endpoint="" value="saved" effort="low" disabled={false} onChange={change} />));
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  const select = host.querySelector<HTMLSelectElement>('select[name=llmModel]')!;
  await act(async () => { select.value = 'other-model'; select.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(change).toHaveBeenCalledWith('other-model', 'medium');
  await act(async () => root.render(<ModelPicker provider="copilot" endpoint="" value="other-model" effort="medium" disabled={false} onChange={change} />));
  const levels = host.querySelector<HTMLSelectElement>('select[name=reasoningEffort]')!;
  expect([...levels.options].map(option => option.value)).toEqual(['medium', 'high']);
  await act(async () => { levels.value = 'high'; levels.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(change).toHaveBeenLastCalledWith('other-model', 'high');
});
it('waits for Codex consent before starting discovery', async () => {
  await act(async () => root.render(<ModelPicker provider="codex" endpoint="" value="saved" effort="low" disabled={false} onChange={change} />));
  await act(async () => { await vi.advanceTimersByTimeAsync(300); });
  expect(fetchMock).not.toHaveBeenCalled();
  expect(button('Refresh models').disabled).toBe(true);
});
