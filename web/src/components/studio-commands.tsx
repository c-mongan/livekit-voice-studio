// Adapted with owner permission from mongo-ai/Intelligent-Document-Processor
// components/CommandPalette.tsx: searchable actions with keyboard selection.
import { useEffect, useRef, useState } from 'react';
export interface StudioCommand { id: string; label: string; description: string; disabled?: boolean; action: () => void }
export function StudioCommands({ commands }: { commands: StudioCommand[] }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const previous = useRef<HTMLElement | null>(null);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(0);
  const filtered = commands.filter(c => `${c.label} ${c.description}`.toLowerCase().includes(search.toLowerCase()));
  function open() {
    if (document.querySelector('dialog[open]')) return;
    previous.current = document.activeElement as HTMLElement;
    setSearch(''); setSelected(0); dialog.current?.showModal(); input.current?.focus();
  }
  function close() { dialog.current?.close(); previous.current?.focus(); }
  function run(command: StudioCommand | undefined) { if (!command || command.disabled) return; close(); command.action(); }
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); open(); }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, []);
  return <><button className="button quiet command-trigger" onClick={open}>Quick actions <kbd>⌘ / Ctrl K</kbd></button>
    <dialog ref={dialog} className="command-dialog" aria-labelledby="commands-title" onCancel={e => { e.preventDefault(); close(); }}>
      <header><h2 id="commands-title">Quick actions</h2><button className="button quiet" onClick={close}>Close actions</button></header>
      <label className="field">Find an action<input ref={input} value={search} onChange={e => { setSearch(e.target.value); setSelected(0); }} onKeyDown={e => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); setSelected(i => filtered.length ? (i + (e.key === 'ArrowDown' ? 1 : filtered.length - 1)) % filtered.length : 0); }
        if (e.key === 'Enter') { e.preventDefault(); run(filtered[selected]); }
      }} aria-describedby="command-hint" /></label>
      <p id="command-hint">Search, then use arrow keys and Enter. Escape closes.</p>
      <span className="sr-only" aria-live="polite">{filtered[selected]?.label}{filtered[selected]?.disabled ? ' unavailable during a conversation' : ''}</span>
      <div className="command-results">{filtered.map((c,i) => <button key={c.id} className={`command-item${i === selected ? ' selected' : ''}`} disabled={c.disabled} onFocus={() => setSelected(i)} onClick={() => run(c)}><strong>{c.label}</strong><span>{c.description}{c.disabled ? ' · Available after the conversation ends' : ''}</span></button>)}</div>
      {!filtered.length && <p>No matching actions. Try “voice” or “settings”.</p>}
    </dialog></>;
}
