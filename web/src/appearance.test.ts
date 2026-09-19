// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { APPEARANCE_KEY, applyAppearance, initializeAppearance, readAppearance, saveAppearance } from './appearance';
beforeEach(() => localStorage.clear());
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); delete document.documentElement.dataset.theme; delete document.documentElement.dataset.density; });
it('handles corrupt or unsupported stored preferences', () => {
  localStorage.setItem(APPEARANCE_KEY, 'broken'); expect(readAppearance()).toEqual({theme:'system',compact:false});
  localStorage.setItem(APPEARANCE_KEY, JSON.stringify({theme:'neon',compact:'yes'})); expect(readAppearance()).toEqual({theme:'system',compact:false});
});
it('applies light and dark without changing conversation data', () => {
  localStorage.setItem('unrelated','keep');
  expect(saveAppearance({theme:'dark',compact:true})).toBe(true);
  expect(document.documentElement.dataset).toMatchObject({theme:'dark',density:'compact'});
  expect(readAppearance()).toEqual({theme:'dark',compact:true});
  expect(localStorage.getItem('unrelated')).toBe('keep');
  applyAppearance({theme:'light',compact:false},true); expect(document.documentElement.dataset.theme).toBe('light');
});
it('follows system changes only when system is selected and cleans up listeners', () => {
  let change: (()=>void) | undefined;
  const media = {matches:false,addEventListener:vi.fn((_type:string, fn:()=>void)=>{change=fn;}),removeEventListener:vi.fn()};
  vi.stubGlobal('matchMedia',vi.fn(()=>media));
  const cleanup=initializeAppearance(); expect(document.documentElement.dataset.theme).toBe('light');
  media.matches=true; change!(); expect(document.documentElement.dataset.theme).toBe('dark');
  saveAppearance({theme:'light',compact:false}); change!(); expect(document.documentElement.dataset.theme).toBe('light');
  cleanup(); expect(media.removeEventListener).toHaveBeenCalled();
});
it('still applies a theme if storage is unavailable', () => {
  vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new Error('blocked');});
  expect(saveAppearance({theme:'dark',compact:false})).toBe(false);
  expect(document.documentElement.dataset.theme).toBe('dark');
});
