import { useEffect, useRef, useState } from 'react';
import { api, errorMessage, studioRequest, type LocalVoice, type StudioConfig, type StudioStatus, type VoiceLibrary } from './api';
import { VoiceEnrollment } from './VoiceEnrollment';
import { useVoiceAudition } from './useVoiceAudition';
import { VoiceAudition } from './VoiceAudition';

const defaultEndpoint = 'http://127.0.0.1:11434/v1';
function ollamaEndpoint(current: string | undefined): string {
  if (!current || current.length > 2048 || /[\s\u0000-\u001f\u007f?#]/.test(current)) return defaultEndpoint;
  try {
    const url = new URL(current);
    const loopback = url.hostname === 'localhost' || url.hostname === '[::1]' || /^127(?:\.\d{1,3}){3}$/.test(url.hostname);
    return loopback && ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && url.port !== '0'
      ? current : defaultEndpoint;
  } catch { return defaultEndpoint; }
}
const presets: Record<string, { model: string; effort: string }> = {
  ollama: { model: 'qwen3:1.7b', effort: 'none' }, 'openai-compatible': { model: '', effort: 'none' },
  copilot: { model: 'gpt-5.6-luna', effort: 'low' }, codex: { model: 'gpt-5.6-luna', effort: 'low' },
  azure: { model: 'gpt-4.1-nano', effort: '' }, openai: { model: 'gpt-4.1-mini', effort: '' },
};

export function StudioSettings({ locked, onChanged, status, onAuditionBusy, requestedTab, onRequestHandled, onOpen, onRoutingChanged }: {
  locked: boolean; onChanged: () => void | Promise<void>; status?: StudioStatus | null;
  onAuditionBusy?: (busy: boolean) => void;
  requestedTab?: 'providers' | 'voices' | null;
  onRequestHandled?: () => void;
  onOpen?: () => void;
  onRoutingChanged?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'providers' | 'voices'>('voices');
  const [config, setConfig] = useState<StudioConfig | null>(null);
  const [library, setLibrary] = useState<VoiceLibrary | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [enrolling, setEnrolling] = useState(false);
  const [deleting, setDeleting] = useState<LocalVoice | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [preview, setPreview] = useState<string | null>(null);
  const [focusedVoice, setFocusedVoice] = useState<string | null>(null);
  const audition = useVoiceAudition(onAuditionBusy, onChanged);
  const request = useRef(0);
  const inFlight = useRef(false);
  const disabled = locked || busy || loading || audition.busy;
  const unavailable = locked || (!audition.busy && status?.phase !== 'idle')
    ? 'End the conversation and wait for cleanup before generating a sample.' : null;
  async function load() {
    const id = ++request.current;
    setLoading(true); setError(null);
    try {
      const [settings, voices] = await Promise.all([api<StudioConfig>('settings'), api<VoiceLibrary>('voices')]);
      if (request.current !== id) return;
      setConfig({ ...settings, livekitMode: settings.livekitMode ?? 'configured', llmBaseUrl: settings.llmBaseUrl ?? defaultEndpoint }); setLibrary(voices);
    } catch (cause) { if (request.current === id) setError(errorMessage(cause, 'Configuration could not be loaded. Check the local server and retry.')); }
    finally { if (request.current === id) setLoading(false); }
  }
  useEffect(() => {
    if (open) { dialog.current?.showModal(); void load(); }
    return () => { request.current++; };
  }, [open]);
  useEffect(() => {
    if (requestedTab && !locked) {
      if (!open) trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      onOpen?.(); setTab(requestedTab); setOpen(true); onRequestHandled?.();
    }
  }, [requestedTab, locked, onRequestHandled]);
  useEffect(() => {
    if (locked) {
      if (audition.busy || audition.url) audition.cancel();
      setEnrolling(false); setDeleting(null); setRenaming(null); setPreview(null);
    }
  }, [locked, audition.cancel, audition.busy, audition.url]);
  function close() {
    if (inFlight.current) return;
    audition.cancel();
    setOpen(false); setEnrolling(false); setPreview(null); setFocusedVoice(null); setDeleting(null); setRenaming(null); setNotice('');
    dialog.current?.close(); trigger.current?.focus();
  }
  async function mutate(action: () => Promise<unknown>, success: string, routingChanged = false) {
    if (disabled || inFlight.current) return;
    audition.cancel();
    inFlight.current = true; setBusy(true); setError(null); setNotice('');
    try { await action(); if (routingChanged) onRoutingChanged?.(); setNotice(success); setDeleting(null); setRenaming(null); await load(); await onChanged(); }
    catch (cause) { setError(errorMessage(cause, 'The change could not be saved. Check the local server and retry.')); }
    finally { inFlight.current = false; setBusy(false); }
  }
  const switchTab = (next: 'providers' | 'voices') => { if (!enrolling) { audition.cancel(); setFocusedVoice(null); setTab(next); setPreview(null); setDeleting(null); } };
  function openDrawer(next: 'providers' | 'voices', source: HTMLButtonElement) {
    onOpen?.();
    trigger.current = source;
    setTab(next);
    setOpen(true);
  }
  return <>
    <div className="setup-entry">
      <button className="button secondary settings-trigger" aria-haspopup="dialog" onClick={(event) => openDrawer('voices', event.currentTarget)}>Voice library</button>
      <button className="button quiet settings-trigger" aria-haspopup="dialog" onClick={(event) => openDrawer('providers', event.currentTarget)}>Settings</button>
      {!locked && <span className="field-help">{audition.busy ? 'Local audition running · other changes paused' : 'Record → audition → choose → chat'}</span>}
    </div>
    <dialog className="settings-drawer" ref={dialog} aria-labelledby="settings-title" onCancel={(event) => { event.preventDefault(); close(); }}>
      {open && <>
        <header className="drawer-header"><div><h2 id="settings-title">Make it yours</h2><p>Choose where your conversation runs</p></div><button className="button quiet" disabled={busy} onClick={close} aria-label="Close settings">Close</button></header>
        <div className="drawer-tabs" role="tablist" aria-label="Studio configuration" onKeyDown={(event) => {
          if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) && !enrolling) {
            event.preventDefault();
            const next = event.key === 'Home' ? 'voices' : event.key === 'End' ? 'providers' : tab === 'providers' ? 'voices' : 'providers';
            switchTab(next); document.getElementById(`tab-${next}`)?.focus();
          }
        }}>
          {(['voices', 'providers'] as const).map((value) => <button key={value} className="button quiet" id={`tab-${value}`} role="tab" aria-selected={tab === value} aria-controls={`panel-${value}`} tabIndex={tab === value ? 0 : -1} disabled={enrolling} onClick={() => switchTab(value)}>{value === 'providers' ? 'Connection & AI' : 'Voices'}</button>)}
        </div>
        <div className="drawer-body">
          {locked && <p className="notice warning">End the conversation and wait for cleanup before changing settings or recording a voice.</p>}
          {audition.busy && !focusedVoice && <p className="notice warning" role="status">Finishing the audition before other changes can continue. {audition.error || 'Waiting for local worker cleanup.'}</p>}
          {audition.error && !audition.busy && !focusedVoice && <p className="notice error" role="alert">{audition.error}</p>}
          {error && <div className="notice error" role="alert"><p>{error}</p><button className="button quiet" disabled={busy} onClick={() => void load()}>Retry</button></div>}
          {notice && <p className="save-notice" role="status">{notice}</p>}
          {loading && <p className="loading-line" role="status">Loading local configuration...</p>}
          {tab === 'providers' && config && <section role="tabpanel" id="panel-providers" aria-labelledby="tab-providers">
            <h3 className="drawer-section-title">Your conversation setup</h3>
            <p className="field-help">Start local. Change individual components when you need cloud services. Changes apply to the next session.</p>
            <fieldset disabled={disabled} className="provider-fields">
              <label className="field">Connection<select value={config.livekitMode} onChange={(event) => setConfig({ ...config, livekitMode: event.target.value as 'local' | 'configured' })}><option value="local">Local LiveKit · default</option><option value="configured">Configured cloud or self-hosted server</option></select></label>
              <p className="route-note">{config.livekitMode === 'local' ? 'Conversation audio and text travel through LiveKit on this computer. Start the local LiveKit server before connecting.' : 'Conversation audio and text travel through your configured LiveKit server. Its address and credentials stay in the server’s .env file.'}</p>
              <label className="field">Speech recognition<select value={config.sttProvider} onChange={(event) => setConfig({ ...config, sttProvider: event.target.value })}>{config.providers.stt.map((provider) => <option key={provider.id} value={provider.id} disabled={!provider.available}>{provider.label}{!provider.available ? ' - unavailable' : ''}</option>)}</select></label>
              <p className="route-note">{config.sttProvider === 'nemotron' ? 'Local recognition. Microphone audio is transcribed on this machine.' : 'Cloud recognition. Microphone audio is sent to your selected speech provider.'}</p>
              {config.providers.stt.filter((provider) => !provider.available).map((provider) => <p className="field-help" key={provider.id}>{provider.label}: {provider.reason || 'Not available on this server.'}</p>)}
              <label className="field">AI provider<select value={config.llmProvider} onChange={(event) => {
                const provider = event.target.value; const preset = presets[provider];
                if (preset) setConfig({ ...config, llmProvider: provider, llmModel: preset.model, reasoningEffort: preset.effort, codexRestrictedApproved: false, llmBaseUrl: provider === 'ollama' ? ollamaEndpoint(config.llmBaseUrl) : config.llmBaseUrl });
              }}>{config.providers.llm.map((provider) => <option key={provider.id} value={provider.id} disabled={!provider.available || !presets[provider.id]}>{provider.label}{!provider.available ? ' - unavailable' : ''}</option>)}</select></label>
              {['ollama', 'openai-compatible'].includes(config.llmProvider) ? <>
                <label className="field">Model name<input name="llmModel" value={config.llmModel} onChange={(event) => setConfig({ ...config, llmModel: event.target.value })} placeholder={config.llmProvider === 'ollama' ? 'qwen3:1.7b' : 'Model available at your endpoint'} /></label>
                <details className="endpoint-settings" key={config.llmProvider} open={config.llmProvider === 'openai-compatible' ? true : undefined}><summary>Advanced connection</summary><label className="field">API endpoint<input name="llmBaseUrl" type="url" value={config.llmBaseUrl} onChange={(event) => setConfig({ ...config, llmBaseUrl: event.target.value })} /></label></details>
                <p className="route-note">{config.llmProvider === 'ollama' ? 'Local reasoning. Start Ollama and prepare the named model first. Use a loopback endpoint on this computer; models are never downloaded automatically. The first reply can take longer while the model loads. On a 16 GB Mac, start with a small model.' : 'Conversation text is sent to this endpoint. Its location determines whether reasoning is local or remote. If it needs an API key, set VOICEBOX_CUSTOM_LLM_API_KEY in the server’s .env file.'}</p>
              </> : <><div className="model-summary"><span>Remote model</span><strong>{config.llmModel}</strong>{config.reasoningEffort && config.reasoningEffort !== 'none' && <span>Reasoning effort: {config.reasoningEffort}</span>}</div>
              <p className="route-note">Your conversation text goes to the selected remote AI provider. Your connection and speech recognition choices stay as selected above.</p></>}
              {config.llmProvider === 'codex' && <label className="consent"><input type="checkbox" checked={config.codexRestrictedApproved === true} onChange={(event) => setConfig({ ...config, codexRestrictedApproved: event.target.checked })} /><span>I accept restricted Codex: commands are limited to a private workspace and minimal runtime files, command networking and external tools are disabled, and my global Codex instructions are trusted. This is not tool-free mode.</span></label>}
              {config.providers.llm.filter((provider) => !provider.available).map((provider) => <p className="field-help" key={provider.id}>{provider.label}: {provider.reason || 'Not available on this server.'}</p>)}
              <div className="local-synthesis"><strong>Speech synthesis stays local</strong><p>Your selected voice is used by Qwen on this machine. The reference recording is not sent to LiveKit or your reasoning provider.</p></div>
              <button className="button primary" disabled={disabled || !config.llmModel.trim() || (['ollama', 'openai-compatible'].includes(config.llmProvider) && !config.llmBaseUrl?.trim()) || (config.llmProvider === 'codex' && !config.codexRestrictedApproved) || !config.providers.stt.find((p) => p.id === config.sttProvider)?.available || !config.providers.llm.find((p) => p.id === config.llmProvider)?.available} onClick={() => void mutate(() => api('settings', { livekitMode: config.livekitMode, llmBaseUrl: config.llmBaseUrl, sttProvider: config.sttProvider, llmProvider: config.llmProvider, llmModel: config.llmModel, reasoningEffort: config.reasoningEffort, codexRestrictedApproved: config.codexRestrictedApproved === true }), 'Settings saved for the next session.', true)}>{busy ? 'Saving...' : 'Save settings'}</button>
            </fieldset>
            <p className="privacy-footnote">Connection, speech recognition and reasoning are independent choices. Review the conversation privacy notice after saving; local recognition alone does not make the entire conversation local.</p>
          </section>}
          {tab === 'voices' && library && <section role="tabpanel" id="panel-voices" aria-labelledby="tab-voices">
            {enrolling ? <VoiceEnrollment guidedText={library.guidedText} locked={locked} onCancel={() => setEnrolling(false)} onSaved={async (voice) => { setEnrolling(false); setFocusedVoice(voice.id); setNotice('Private voice saved. Generate a sample below to hear the clone, then choose it for chat.'); await load(); await onChanged(); }} /> : <>
              <div className="section-title"><h3 className="drawer-section-title">Your voice library</h3><span className="local-badge">On this machine</span></div>
              <p className="field-help">Record a reference → audition generated speech → choose your voice for chat.</p>
              <div className="library-record-action"><button className="button primary" disabled={disabled} onClick={() => { audition.cancel(); setFocusedVoice(null); setPreview(null); setEnrolling(true); setNotice(''); }}>Record a voice</button></div>
              <p className="privacy-footnote">Only record yourself or someone who has given permission. New recordings stay in memory until saved to your local server.</p>
              <details className="voice-readiness"><summary>What you need for an audition</summary><p className="field-help">{unavailable || 'A saved voice and a local Qwen model. Generate sample checks model and hardware availability on this machine; setup issues appear here with next steps.'}</p><p className="field-help">{status?.ready ? 'Conversation setup is ready.' : 'Chat has separate requirements: LiveKit plus your selected speech and reasoning providers. You can audition locally without those credentials.'}</p></details>
              <ul className="voice-library">{library.voices.map((voice) => <li key={voice.id}>
                <div className="voice-row-heading"><strong>{voice.name}</strong>{voice.selected && <span className="selected-badge">Next session</span>}</div>
                <p className="field-help">{voice.durationSeconds.toFixed(1)} s reference · Private local WAV</p>
                <div className="voice-row-actions">
                  <button className="button secondary" disabled={disabled || voice.selected} onClick={() => void mutate(() => api('settings', { voiceId: voice.id }), `${voice.name} selected for the next session.`)}>{voice.selected ? 'Selected' : 'Use next session'}</button>
                  <button className="button secondary" aria-label={`${focusedVoice === voice.id ? 'Hide audition' : 'Audition clone'} of ${voice.name}`} aria-expanded={focusedVoice === voice.id} disabled={busy || locked} onClick={() => { audition.cancel(); setPreview(null); setFocusedVoice(focusedVoice === voice.id ? null : voice.id); }}>{focusedVoice === voice.id ? 'Hide audition' : 'Audition clone'}</button>
                  <button className="button quiet" aria-label={`${preview === voice.id ? 'Hide original recording' : 'Original recording'} of ${voice.name}`} aria-expanded={preview === voice.id} disabled={busy || locked || audition.busy} onClick={() => { audition.cancel(); setFocusedVoice(null); setPreview(preview === voice.id ? null : voice.id); }}>{preview === voice.id ? 'Hide original' : 'Original recording'}</button>
                  <button className="button quiet" disabled={disabled} onClick={() => { setRenaming(voice.id); setName(voice.name); }}>Rename</button>
                  <button className="button quiet danger-text" disabled={disabled || voice.selected} title={voice.selected ? 'Select another voice before deleting this one.' : undefined} onClick={() => setDeleting(voice)}>Delete</button>
                </div>
                {preview === voice.id && <div className="original-preview"><p className="field-help">Original recording · the saved reference, not generated speech.</p><audio className="voice-preview" aria-label={`Original recording of ${voice.name}`} src={`/api/voices/${encodeURIComponent(voice.id)}/audio`} controls controlsList="nodownload" preload="none" onError={() => setError('This reference could not be played. Check the local server or try another voice.')} /></div>}
                {focusedVoice === voice.id && <VoiceAudition key={voice.id} voice={voice} audition={audition} unavailable={unavailable} choosing={disabled} onClose={() => { audition.cancel(); setFocusedVoice(null); }} onChoose={() => void mutate(() => api('settings', { voiceId: voice.id }), `${voice.name} selected. Close settings, then start a session to chat.`)} />}
                {renaming === voice.id && <form className="rename-form" onSubmit={(event) => { event.preventDefault(); if (name.trim()) void mutate(() => studioRequest(`voices/${encodeURIComponent(voice.id)}`, { method: 'PATCH', body: { name: name.trim() } }), 'Voice renamed.'); }}>
                  <label className="field">New voice name<input autoFocus required maxLength={80} value={name} disabled={disabled} onChange={(event) => setName(event.target.value)} /></label>
                  <button className="button secondary" disabled={disabled || !name.trim()}>Save name</button><button className="button quiet" type="button" onClick={() => setRenaming(null)}>Cancel rename</button>
                </form>}
              </li>)}</ul>
              {!library.voices.length && <p className="empty-library">No private voices yet. Record a short passage to create your first voice.</p>}
              {deleting && <div className="delete-confirmation" role="group" aria-label="Confirm voice deletion"><strong>Delete {deleting.name}?</strong><p>This removes its private reference from this machine. This cannot be undone.</p><button className="button secondary danger-text" disabled={disabled} onClick={() => void mutate(() => studioRequest(`voices/${encodeURIComponent(deleting.id)}`, { method: 'DELETE', body: { confirm: true } }), 'Voice deleted.')}>Delete voice permanently</button><button className="button quiet" onClick={() => setDeleting(null)}>Keep voice</button></div>}
            </>}
          </section>}
        </div>
      </>}
    </dialog>
  </>;
}
