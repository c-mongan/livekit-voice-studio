import { useCallback, useEffect, useRef, useState } from 'react';
import { encodeWav, inspectAudio } from './recording';

type Capture = { stream: MediaStream; context: AudioContext; source?: MediaStreamAudioSourceNode; node?: AudioWorkletNode; timer?: ReturnType<typeof setTimeout> };

/** A disposable microphone test. Audio never leaves browser memory. */
export function MicrophoneCheck({ disabled = false }: { disabled?: boolean }) {
  const [phase, setPhase] = useState<'idle' | 'requesting' | 'recording'>('idle');
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [duration, setDuration] = useState(0);
  const capture = useRef<Capture | null>(null);
  const generation = useRef(0);
  const busy = useRef(false);
  const url = useRef<string | null>(null);
  const chunks = useRef<Float32Array[]>([]);
  const count = useRef(0);
  const dispose = useCallback(() => {
    const owned = capture.current; capture.current = null;
    if (!owned) return;
    clearTimeout(owned.timer);
    owned.stream.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    if (owned.node) { owned.node.port.onmessage = null; owned.node.disconnect(); }
    owned.source?.disconnect();
    void owned.context.close().catch(() => {});
  }, []);
  const discard = useCallback(() => {
    generation.current++; busy.current = false; dispose(); chunks.current = []; count.current = 0;
    if (url.current) URL.revokeObjectURL(url.current);
    url.current = null;
  }, [dispose]);
  const cancel = useCallback(() => {
    discard(); setPreview(null); setPhase('idle'); setDuration(0); setLevel(0); setError(null);
  }, [discard]);
  const stop = useCallback(() => {
    const owned = capture.current;
    if (!owned) return;
    const sampleRate = owned.context.sampleRate;
    generation.current++; busy.current = false; dispose(); setPhase('idle'); setLevel(0);
    const samples = new Float32Array(count.current);
    let offset = 0;
    for (const chunk of chunks.current) { samples.set(chunk, offset); offset += chunk.length; }
    chunks.current = []; count.current = 0;
    if (!samples.length) { setError('No audio arrived. Check your system microphone input and try again.'); return; }
    try {
      url.current = URL.createObjectURL(encodeWav(samples, sampleRate)); setPreview(url.current);
      const measurements = inspectAudio(samples, sampleRate);
      setDuration(measurements.duration);
      if (measurements.rms < .005) setError('Your microphone sounds quiet. Check the selected input in your browser or system settings, then move closer and try again.');
      else if (measurements.clipped > .01) setError('Your microphone is too loud. Move farther away or lower the input volume and try again.');
    } catch { setError('This browser could not prepare a replay. Try another microphone or browser.'); }
  }, [dispose]);
  const start = async () => {
    if (disabled || busy.current) return;
    cancel(); busy.current = true;
    const attempt = generation.current;
    setPhase('requesting');
    let stream: MediaStream | undefined;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
      if (attempt !== generation.current) { stream.getTracks().forEach((track) => track.stop()); return; }
      const context = new AudioContext({ sampleRate: 24000 });
      capture.current = { stream, context };
      setPhase('recording');
      if (context.sampleRate < 8000 || context.sampleRate > 96000) throw new Error('Unsupported sample rate');
      capture.current.timer = setTimeout(stop, 8000);
      await context.resume();
      if (attempt !== generation.current) return;
      await context.audioWorklet.addModule(new URL('./pcm-worklet.js', import.meta.url).href);
      if (attempt !== generation.current) return;
      const source = context.createMediaStreamSource(stream);
      capture.current!.source = source;
      const node = new AudioWorkletNode(context, 'voicebox-capture');
      capture.current!.node = node;
      node.port.onmessage = (event: MessageEvent<Float32Array>) => {
        if (attempt !== generation.current) return;
        const chunk = event.data.slice(0, sampleLimit() - count.current);
        chunks.current.push(chunk); count.current += chunk.length;
        setDuration(count.current / context.sampleRate);
        setLevel(Math.min(1, inspectAudio(chunk, context.sampleRate).rms * 5));
        if (count.current >= sampleLimit()) stop();
      };
      function sampleLimit() { return context.sampleRate * 8; }
      source.connect(node); node.connect(context.destination);
      stream.getTracks().forEach((track) => { track.onended = stop; });
      setPhase('recording');
    } catch {
      if (attempt !== generation.current) return;
      if (!capture.current) stream?.getTracks().forEach((track) => track.stop());
      discard(); setPhase('idle');
      setError('Allow microphone access in your browser and check your system input device. This test needs a browser with Web Audio support.');
    }
  };
  useEffect(() => {
    window.addEventListener('pagehide', cancel);
    return () => { window.removeEventListener('pagehide', cancel); discard(); };
  }, [cancel, discard]);
  useEffect(() => { if (disabled) cancel(); }, [disabled, cancel]);
  return <section className="microphone-check" aria-label="Microphone check">
    <h3>Check your microphone</h3>
    <p className="field-help">Say a few words, then play them back. Uses your browser’s default microphone. Change the input in browser or system settings if needed.</p>
    <p className="field-help" role="status">{phase === 'recording' ? 'Microphone on. Stops automatically within 8 seconds.' : phase === 'requesting' ? 'Waiting for microphone permission. You can cancel this test.' : 'Microphone off.'}</p>
    {phase === 'recording' && <div className="recording-strip"><meter aria-label="Microphone level" min={0} max={1} value={level} /><output aria-label="Test duration">{duration.toFixed(1)} / 8 s</output></div>}
    <div className="recording-actions">
      {phase === 'recording' ? <button className="button secondary" type="button" onClick={stop}>Stop test</button> : <button className="button secondary" type="button" disabled={disabled || phase === 'requesting'} onClick={() => void start()}>Test microphone</button>}
      {(phase !== 'idle' || preview) && <button className="button quiet" type="button" onClick={cancel}>{preview ? 'Discard test' : 'Cancel test'}</button>}
    </div>
    {error && <p className="notice error" role="alert">{error}</p>}
    {preview && <audio className="voice-preview" aria-label="Microphone test replay" controls controlsList="nodownload" preload="none" src={preview} onError={() => setError('Playback failed. Try the microphone test again.')} />}
    <p className="privacy-footnote">This temporary test stays in browser memory. Nothing is uploaded or saved. Discard it or leave this page to clear it.</p>
  </section>;
}
