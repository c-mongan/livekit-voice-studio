// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { LearningLab } from './LearningLab';

it('keeps exercise progress separate and never performs a fault or network call itself', async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<LearningLab />));
    expect(host.querySelector('details')?.open).toBe(false);
    const checkbox = () => host.querySelector<HTMLInputElement>('input')!;
    await act(async () => checkbox().click());
    expect(host.textContent).toContain('1 of 3 self-reported');
    const select = host.querySelector('select')!;
    await act(async () => { select.value = '2'; select.dispatchEvent(new Event('change', {bubbles: true})); });
    expect(checkbox().checked).toBe(false);
    expect(host.textContent).toContain('Restore the exact previous URL');
    expect(host.textContent).toContain('disposable local setup');
    await act(async () => checkbox().click());
    expect(host.textContent).toContain('2 of 3 self-reported');
    await act(async () => { select.value = '0'; select.dispatchEvent(new Event('change', {bubbles: true})); });
    expect(checkbox().checked).toBe(true);
    await act(async () => checkbox().click());
    expect(host.textContent).toContain('1 of 3 self-reported');
    expect(fetcher).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); }
});
