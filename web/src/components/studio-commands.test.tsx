// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { StudioCommands } from './studio-commands';
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const run = vi.fn();
beforeEach(async () => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  HTMLDialogElement.prototype.showModal = function() { this.setAttribute('open',''); };
  HTMLDialogElement.prototype.close = function() { this.removeAttribute('open'); };
  run.mockReset(); host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<StudioCommands commands={[
    {id:'voice',label:'Choose voice',description:'Private library',action:run},
    {id:'locked',label:'Settings',description:'Local or cloud',disabled:true,action:run},
  ]} />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
it('opens by shortcut, runs Enter selection, and restores focus', async () => {
  const trigger = host.querySelector('button')!; trigger.focus();
  await act(async () => window.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,cancelable:true})));
  expect(host.querySelector('dialog')?.open).toBe(true);
  await act(async () => host.querySelector('input')!.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})));
  expect(run).toHaveBeenCalledOnce(); expect(host.querySelector('dialog')?.open).toBe(false);
  expect(document.activeElement).toBe(trigger);
});
it('keyboard selection cannot run a locked action', async () => {
  await act(async () => host.querySelector('button')!.click());
  const input = host.querySelector('input')!;
  await act(async () => input.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true})));
  await act(async () => input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})));
  expect(run).not.toHaveBeenCalled(); expect(host.querySelector('dialog')?.open).toBe(true);
});
it('does not open over another modal', async () => {
  const other = document.createElement('dialog'); other.setAttribute('open',''); document.body.append(other);
  try { await act(async () => window.dispatchEvent(new KeyboardEvent('keydown',{key:'k',metaKey:true}))); expect(host.querySelector('dialog')?.open).toBe(false); }
  finally { other.remove(); }
});
