// Theme choices and density adapted from the owner's Document Processor UserPreferences.
import { useState } from 'react';
import { readAppearance, saveAppearance, type Appearance } from '../appearance';
export function AppearanceSettings() {
  const [value, setValue] = useState(readAppearance);
  const [notice, setNotice] = useState('');
  function update(next: Appearance) { setValue(next); setNotice(saveAppearance(next) ? 'Appearance saved on this browser.' : 'Applied for now. This browser could not save your preference.'); }
  return <section role="tabpanel" id="panel-appearance" aria-labelledby="tab-appearance">
    <h3 className="drawer-section-title">Make yourself comfortable</h3><p className="field-help">Changes apply immediately. Only appearance preferences are saved in this browser.</p>
    <fieldset className="theme-options"><legend>Colour theme</legend>{(['system','light','dark'] as const).map(theme => <label key={theme} className={`theme-option theme-${theme}`}><input type="radio" name="theme" checked={value.theme === theme} onChange={() => update({...value,theme})} /><span className="theme-preview" aria-hidden="true"><i /><i /><i /></span><strong>{theme[0].toUpperCase()+theme.slice(1)}</strong><small>{theme === 'system' ? 'Follow your device' : theme === 'dark' ? 'Navy & cyan' : 'Bright & clear'}</small></label>)}</fieldset>
    <label className="appearance-toggle"><span><strong>Compact spacing</strong><small>Fit more controls and messages on screen.</small></span><input type="checkbox" checked={value.compact} onChange={e => update({...value,compact:e.target.checked})} /></label>
    <p className="field-help">Motion follows your device’s reduced-motion preference.</p><p role="status" className="save-notice">{notice}</p>
  </section>;
}
