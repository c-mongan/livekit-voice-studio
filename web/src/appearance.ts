export type Theme = 'system' | 'light' | 'dark';
export const APPEARANCE_KEY = 'studio-appearance';
export interface Appearance { theme: Theme; compact: boolean }
export function readAppearance(): Appearance {
  try {
    const value = JSON.parse(localStorage.getItem(APPEARANCE_KEY) || '{}');
    return { theme: ['system','light','dark'].includes(value?.theme) ? value.theme : 'system', compact: value?.compact === true };
  } catch { return { theme: 'system', compact: false }; }
}
export function applyAppearance(value: Appearance, dark = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false) {
  document.documentElement.dataset.theme = value.theme === 'system' ? dark ? 'dark' : 'light' : value.theme;
  document.documentElement.dataset.density = value.compact ? 'compact' : 'comfortable';
}
export function saveAppearance(value: Appearance): boolean {
  applyAppearance(value);
  try { localStorage.setItem(APPEARANCE_KEY, JSON.stringify(value)); return true; } catch { return false; }
}
export function initializeAppearance() {
  applyAppearance(readAppearance());
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const update = () => applyAppearance(readAppearance());
  const storage = (event: StorageEvent) => { if (event.key === APPEARANCE_KEY || event.key === null) update(); };
  media.addEventListener('change', update); window.addEventListener('storage', storage);
  return () => { media.removeEventListener('change', update); window.removeEventListener('storage', storage); };
}
