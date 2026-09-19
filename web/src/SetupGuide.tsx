import { useEffect, useRef, useState } from 'react';
import { api, errorMessage, safeMessage } from './api';

interface Check { id: string; status: 'pass' | 'missing' | 'unverified'; message: string; action: string }
interface SetupReport { version: number; checks: Check[] }
const labels = { pass: 'Found', missing: 'Needs setup', unverified: 'Not tested' };

export function SetupGuide({ disabled = false, onVoices, onSettings }: { disabled?: boolean; onVoices: () => void; onSettings: () => void }) {
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<SetupReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  useEffect(() => () => { generation.current++; }, []);
  async function check() {
    const id = ++generation.current;
    setLoading(true); setError(null); setReport(null);
    try {
      const result = await api<SetupReport>('setup');
      if (result.version !== 1 || !Array.isArray(result.checks) || result.checks.some(item => !item || !['pass', 'missing', 'unverified'].includes(item.status))) throw new Error('Invalid report');
      if (generation.current === id) setReport(result);
    } catch (cause) {
      if (generation.current === id) setError(errorMessage(cause, 'Setup checks could not be read. Check the local server and try again.'));
    } finally { if (generation.current === id) setLoading(false); }
  }
  function toggle() {
    if (open) { generation.current++; setLoading(false); setOpen(false); }
    else { setOpen(true); void check(); }
  }
  return <section className="setup-guide" aria-label="Getting started">
    <button className="button quiet" aria-expanded={open} aria-controls="first-conversation-guide" onClick={toggle}>First conversation guide</button>
    {open && <div id="first-conversation-guide" className="guide-body">
      <h2>Your first conversation, one step at a time</h2>
      <p className="field-help">Standalone mode uses this app’s own voice library and runs Qwen on your Mac. You do not need the separate Voicebox app.</p>
      <ol className="guide-steps">
        <li><h3>Check this computer</h3><p>These checks inspect configuration only. They do not download models, contact providers, or turn on your microphone.</p>
          <button className="button secondary" disabled={loading} onClick={() => void check()}>{loading ? 'Checking…' : 'Check again'}</button>
          {loading && <p role="status">Checking local setup…</p>}
          {error && <p className="notice error" role="alert">{error}</p>}
          {report && <ul className="setup-checks">{report.checks.map(item => <li key={item.id}>
            <span className={`check-state check-${item.status}`}>{labels[item.status]}</span>
            <div><p>{safeMessage(item.message, 'Check local configuration.')}</p>{item.action && <p className="field-help">{safeMessage(item.action, 'See the first-run guide.')}</p>}</div>
          </li>)}</ul>}
          <a href="https://github.com/c-mongan/livekit-voice-studio/blob/main/docs/quickstart.md" target="_blank" rel="noreferrer">Open the installation guide ↗</a>
        </li>
        <li><h3>Record and hear your voice</h3><p>Record a short passage, check its words, then generate a new sample. With the local speech model installed, you can do this without a LiveKit or AI account.</p>
          <button className="button secondary" disabled={disabled} onClick={onVoices}>Open voice library</button>
        </li>
        <li><h3>Connect your conversation</h3><p>Choose speech recognition and an AI service in Settings. LiveKit carries the conversation audio; your reference recording stays on this computer. Account access is checked when you start.</p>
          <button className="button secondary" disabled={disabled} onClick={onSettings}>Choose conversation services</button>
        </li>
        <li><h3>Start with a message</h3><p>Close this guide, acknowledge where your conversation goes, then type a message. Turn on your microphone only when you want to speak.</p></li>
      </ol>
      {disabled && <p className="field-help">Finish the current session or sample before changing your setup.</p>}
    </div>}
  </section>;
}
