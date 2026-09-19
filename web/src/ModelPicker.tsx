import { useEffect, useState } from 'react';
import { api, errorMessage } from './api';

type Model = { id: string; efforts: string[] };
export function ModelPicker({ provider, endpoint, value, effort, onChange, disabled, codexApproved = false }: {
  provider: 'ollama' | 'copilot' | 'codex'; endpoint: string; value: string; effort: string;
  onChange: (model: string, effort: string) => void; disabled: boolean; codexApproved?: boolean;
}) {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [manual, setManual] = useState(false);
  const consentNeeded = provider === 'codex' && !codexApproved;
  useEffect(() => {
    let current = true;
    setModels([]); setError(null); setLoading(!consentNeeded);
    if (consentNeeded || disabled) return;
    const timer = setTimeout(() => {
      void api<{ models: Model[] }>('models', { provider, ...(provider === 'ollama' ? { endpoint } : {}), ...(provider === 'codex' ? { codexRestrictedApproved: codexApproved } : {}) })
        .then(result => {
          if (!Array.isArray(result.models) || result.models.some(model => typeof model.id !== 'string' || !Array.isArray(model.efforts))) throw new Error('Invalid catalog');
          if (current) setModels(result.models);
        })
        .catch(cause => { if (current) setError(errorMessage(cause, 'Could not load models. Check the service and refresh.')); })
        .finally(() => { if (current) setLoading(false); });
    }, 250);
    return () => { current = false; clearTimeout(timer); };
  }, [provider, endpoint, refresh, consentNeeded, codexApproved, disabled]);
  const selected = models.find(model => model.id === value);
  const efforts = selected?.efforts ?? [effort];
  return <div>
    {manual && provider === 'ollama' ? <label className="field">Model name<input name="llmModel" value={value} disabled={disabled} onChange={event => onChange(event.target.value, 'none')} /></label> :
      <label className="field">{provider === 'ollama' ? 'Installed model' : 'AI model'}<select name="llmModel" value={value} disabled={disabled || loading || consentNeeded} onChange={event => {
        const model = models.find(item => item.id === event.target.value);
        if (model) onChange(model.id, model.efforts.includes(effort) ? effort : model.efforts.includes('low') ? 'low' : model.efforts[0] ?? 'none');
      }}>
        {!selected && <option value={value}>{value || 'Choose a model'}{!loading && !consentNeeded && value ? ' · not listed' : ''}</option>}
        {models.map(model => <option key={model.id} value={model.id}>{model.id}</option>)}
      </select></label>}
    {provider !== 'ollama' && <label className="field">Reasoning level<select name="reasoningEffort" disabled={disabled || !selected} value={effort} onChange={event => onChange(value, event.target.value)}>
      {!efforts.includes(effort) && <option value={effort}>{effort} · not available</option>}
      {efforts.map(level => <option key={level} value={level}>{level}</option>)}
    </select></label>}
    {loading && !disabled && <p className="field-help" role="status">{provider === 'ollama' ? 'Loading installed models…' : 'Loading models available to your account…'}</p>}
    {consentNeeded && <p className="field-help">Confirm the Codex boundary below to load its model list.</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {!loading && !error && !consentNeeded && !models.length && <p className="field-help">{provider === 'ollama' ? 'No installed models found. Install a chat model with Ollama, then refresh.' : 'No compatible models found. Check account access, then refresh.'}</p>}
    <div className="voice-row-actions">
      <button type="button" className="button secondary" disabled={disabled || loading || consentNeeded} onClick={() => setRefresh(value => value + 1)}>Refresh models</button>
      {provider === 'ollama' && <button type="button" className="button quiet" disabled={disabled} onClick={() => setManual(value => !value)}>{manual ? 'Choose installed model' : 'Enter name manually'}</button>}
    </div>
    <p className="field-help">{provider === 'ollama' ? 'Lists models on this server; never downloads one.' : 'Lists compatible account models; does not send a chat message.'} Your choice applies after Save settings.</p>
  </div>;
}
