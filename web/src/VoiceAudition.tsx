import { useEffect, useId, useState } from 'react';
import type { LocalVoice } from './api';
import type { useVoiceAudition } from './useVoiceAudition';

export const DEFAULT_AUDITION_TEXT = 'Hello. This is my generated voice. Does it sound natural, clear, and like me?';

export function VoiceAudition({ voice, audition, unavailable, onChoose, onClose, choosing }: {
  voice: LocalVoice;
  audition: ReturnType<typeof useVoiceAudition>;
  unavailable: string | null;
  onChoose: () => void;
  onClose: () => void;
  choosing: boolean;
}) {
  const [text, setText] = useState(DEFAULT_AUDITION_TEXT);
  const [playbackError, setPlaybackError] = useState(false);
  const hint = useId();
  useEffect(() => { setPlaybackError(false); }, [audition.url]);
  return <section className="audition-panel" aria-label={`Generated audition for ${voice.name}`}>
    <div className="section-title"><h4>Hear the generated clone</h4><button className="button quiet" type="button" onClick={onClose}>Close audition</button></div>
    <p className="field-help">New speech from {voice.name}, not a replay of the original recording. Listen for likeness, pronunciation, and a natural rhythm.</p>
    <label className="field">Audition text<textarea rows={3} maxLength={300} value={text} aria-describedby={hint} onChange={(event) => { audition.cancel(); setText(event.target.value); }} /></label>
    <div className="audition-meta" id={hint}><span>Try the same sentence across voices.</span><span>{text.length}/300</span></div>
    {unavailable && <p className="notice warning">{unavailable}</p>}
    {audition.error && <p className="notice error" role="alert">{audition.error}</p>}
    {playbackError && <p className="notice error" role="alert">This generated sample could not be played. Generate another sample and try again.</p>}
    <div className="audition-actions">
      <button className="button primary" type="button" disabled={!!unavailable || audition.busy || !text.trim() || choosing} onClick={() => audition.generate(voice.id, text)}>{audition.phase === 'generating' ? 'Generating sample…' : audition.phase === 'cancelling' ? 'Finishing cleanup…' : audition.url ? 'Generate again' : 'Generate sample'}</button>
      {(audition.busy || audition.url) && <button className="button quiet" type="button" onClick={audition.cancel}>{audition.busy ? 'Cancel audition' : 'Discard sample'}</button>}
    </div>
    {audition.busy && <p className="field-help audition-progress" role="status">{audition.phase === 'cancelling' ? 'Cancelling and waiting for the local worker to stop. Other changes stay locked until cleanup finishes.' : 'Preparing the local model and generating speech. This can take tens of seconds. Playback waits until generation has fully stopped.'}</p>}
    {audition.url && <div className="generated-result">
      <strong>Generated clone · ready to play</strong>
      <audio className="voice-preview" aria-label={`Generated clone of ${voice.name}`} controls controlsList="nodownload" preload="none" src={audition.url} onError={() => setPlaybackError(true)} />
      <p className="field-help">Press Play to listen. Nothing plays automatically.</p>
    </div>}
    <div className="audition-next"><button className="button secondary" type="button" disabled={audition.busy || choosing || voice.selected} onClick={onChoose}>{voice.selected ? 'Selected for chat' : 'Choose this voice'}</button><span className="field-help">{voice.selected ? 'Close settings, then start a session to chat.' : 'Choosing changes the next conversation, not this audition.'}</span></div>
    <p className="privacy-footnote">Generated locally without LiveKit or cloud AI. Sample audio stays in this page’s memory and is discarded when you edit, switch voices, or close. No download or browser storage.</p>
  </section>;
}
