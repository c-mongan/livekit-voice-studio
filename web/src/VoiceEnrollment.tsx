import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { errorMessage, studioRequest, type LocalVoice } from './api';
import { useVoiceRecorder } from './useVoiceRecorder';

export function VoiceEnrollment({ guidedText, locked, onSaved, onCancel }: {
  guidedText: string; locked: boolean; onSaved: (voice: LocalVoice) => Promise<void>; onCancel: () => void;
}) {
  const recorder = useVoiceRecorder();
  const { cancel } = recorder;
  const [name, setName] = useState('');
  const [transcript, setTranscript] = useState(guidedText);
  const [authorized, setAuthorized] = useState(false);
  const [verified, setVerified] = useState(false);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const capturing = recorder.phase === 'recording' || recorder.phase === 'requesting';
  useEffect(() => { if (locked) cancel(); }, [cancel, locked]);
  useEffect(() => {
    if (!recorder.recording) { setPreview(null); return; }
    const url = URL.createObjectURL(recorder.recording.blob);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [recorder.recording]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (locked || savingRef.current || !authorized || !verified || !name.trim() || !transcript.trim() || !recorder.recording || recorder.recording.problem) return;
    if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) {
      setError('Open Studio on localhost to save a private voice. Reference audio is never sent to a remote server.'); return;
    }
    savingRef.current = true; setSaving(true); setError(null);
    const form = new FormData();
    form.set('name', name.trim()); form.set('transcript', transcript.trim()); form.set('authorized', 'true');
    form.set('audio', recorder.recording.blob, 'reference.wav');
    try {
      const result = await studioRequest<{ voice: LocalVoice }>('voices', { method: 'POST', body: form });
      cancel();
      await onSaved(result.voice);
    } catch (cause) { setError(errorMessage(cause, 'Your voice could not be saved. The sample stays here until you retry or cancel.')); }
    finally { savingRef.current = false; setSaving(false); }
  }
  return <form className="enrollment" onSubmit={(event) => void save(event)}>
    <div className="section-title"><h3>Record your voice</h3><button className="button quiet" type="button" disabled={saving} onClick={() => { cancel(); onCancel(); }}>Cancel recording</button></div>
    <p className="field-help">Read in your natural voice for 5-30 seconds. Find a quiet spot and keep a little distance from the microphone.</p>
    <blockquote className="guided-passage">{guidedText}</blockquote>
    <div className="recording-strip">
      <div className="recording-wave" aria-hidden="true">{Array.from({ length: 48 }, (_, i) => <span key={i} style={{ '--level': Math.max(.04, Math.min(1, (recorder.levels[i] || 0) * 5)) } as CSSProperties} />)}</div>
      <output aria-label="Recording duration">{recorder.duration.toFixed(1)} <span>/ 30 s</span></output>
    </div>
    <div className="recording-actions">
      {recorder.phase === 'recording' ? <button className="button secondary" type="button" onClick={recorder.stop}>Stop recording</button> :
        <button className="button secondary" type="button" disabled={locked || saving || recorder.phase === 'requesting'} onClick={() => { setVerified(false); void recorder.start(); }}>{recorder.phase === 'requesting' ? 'Waiting for microphone...' : recorder.recording ? 'Record again' : 'Start recording'}</button>}
      <span className="field-help" role="status">{recorder.phase === 'recording' ? 'Microphone on. Stops automatically at 30 seconds.' : recorder.phase === 'review' ? 'Microphone off. Listen before saving.' : 'Microphone off until you choose Start recording.'}</span>
    </div>
    {(recorder.error || recorder.recording?.problem || error) && <p className="notice error" role="alert">{error || recorder.error || recorder.recording?.problem}</p>}
    {preview && <div className="original-preview"><p className="field-help">Original recording · check this reference before saving. This is not the generated clone.</p><audio className="voice-preview" aria-label="Recorded voice preview" controls controlsList="nodownload" preload="none" src={preview} onError={() => setError('This recording could not be played. Record again before saving.')} /></div>}
    <label className="field">Voice name<input maxLength={80} value={name} disabled={saving} onChange={(event) => setName(event.target.value)} placeholder="e.g. My natural voice" required /></label>
    <label className="field">Words you recorded<textarea rows={4} maxLength={2000} value={transcript} disabled={saving || capturing} onChange={(event) => { setTranscript(event.target.value); setVerified(false); }} required /></label>
    <p className="field-help">Edit this to match your recording exactly. No cloud transcription is used to check the passage.</p>
    <p className="field-help">For a clearer clone: use one speaker, no music, an even speaking volume, and the exact words you said. After saving, generate a new sentence to hear the result.</p>
    <label className="check-field"><input type="checkbox" checked={verified} disabled={!preview || capturing || saving} onChange={(event) => setVerified(event.target.checked)} />I listened to the sample and these words match.</label>
    <label className="check-field"><input type="checkbox" checked={authorized} disabled={saving} onChange={(event) => setAuthorized(event.target.checked)} />This is my voice, or I have permission to use it.</label>
    <p className="privacy-footnote">Save sends the WAV and transcript only to this machine's local Studio server. Nothing is kept in browser storage or sent to cloud speech recognition. Cancel discards the unsaved sample.</p>
    <button className="button primary" type="submit" disabled={locked || saving || capturing || !recorder.recording || !!recorder.recording.problem || !authorized || !verified || !name.trim() || !transcript.trim()}>{saving ? 'Saving voice...' : 'Save private voice'}</button>
  </form>;
}
