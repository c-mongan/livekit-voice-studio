import { useCallback, useEffect, useRef, useState } from 'react';
import { encodeWav, inspectAudio, recordingProblem, type AudioMeasurements } from './recording';

export interface VoiceRecording { blob: Blob; measurements: AudioMeasurements; problem: string | null }
export function useVoiceRecorder() {
  const [phase, setPhase] = useState<'idle' | 'requesting' | 'recording' | 'review'>('idle');
  const [recording, setRecording] = useState<VoiceRecording | null>(null);
  const [duration, setDuration] = useState(0);
  const [levels, setLevels] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const resources = useRef<{ stream: MediaStream; context: AudioContext; node?: AudioWorkletNode; source?: MediaStreamAudioSourceNode } | null>(null);
  const generation = useRef(0);
  const busy = useRef(false);
  const chunks = useRef<Float32Array[]>([]);
  const count = useRef(0);
  const dispose = useCallback(() => {
    const owned = resources.current;
    resources.current = null;
    if (!owned) return;
    owned.stream.getTracks().forEach((track) => track.stop());
    if (owned.node) { owned.node.port.onmessage = null; owned.node.disconnect(); }
    owned.source?.disconnect();
    void owned.context.close().catch(() => setError('Microphone capture stopped, but the audio device could not close cleanly. Reload before recording again.'));
  }, []);
  const cancel = useCallback(() => {
    generation.current++;
    busy.current = false;
    dispose();
    chunks.current = []; count.current = 0;
    setRecording(null); setDuration(0); setLevels([]); setPhase('idle');
  }, [dispose]);
  const stop = useCallback(() => {
    const owned = resources.current;
    if (!owned?.node) return;
    const sampleRate = owned.context.sampleRate;
    dispose();
    busy.current = false;
    const samples = new Float32Array(count.current);
    let offset = 0;
    for (const chunk of chunks.current) { samples.set(chunk, offset); offset += chunk.length; }
    chunks.current = []; count.current = 0;
    const measurements = inspectAudio(samples, sampleRate);
    const blob = encodeWav(samples, sampleRate);
    setRecording({ blob, measurements, problem: blob.size > 4 * 1024 * 1024 ? 'This sample exceeds 4 MiB. Use a lower sample rate or a shorter recording.' : recordingProblem(measurements) });
    setDuration(measurements.duration); setPhase('review');
  }, [dispose]);
  const start = useCallback(async () => {
    if (busy.current) return;
    cancel();
    busy.current = true;
    const attempt = generation.current;
    setError(null); setPhase('requesting');
    let stream: MediaStream | undefined;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      if (attempt !== generation.current) { stream.getTracks().forEach((track) => track.stop()); return; }
      const context = new AudioContext({ sampleRate: 24000 });
      resources.current = { stream, context };
      if (context.sampleRate < 8000 || context.sampleRate > 96000) throw new Error('rate');
      await context.resume();
      await context.audioWorklet.addModule(new URL('./pcm-worklet.js', import.meta.url).href);
      if (attempt !== generation.current) return;
      const source = context.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(context, 'voicebox-capture');
      resources.current = { stream, context, source, node };
      node.port.onmessage = (event: MessageEvent<Float32Array>) => {
        if (attempt !== generation.current || !resources.current) return;
        const remaining = context.sampleRate * 30 - count.current;
        const chunk = event.data.slice(0, remaining);
        chunks.current.push(chunk); count.current += chunk.length;
        // Only refresh the UI roughly ten times a second; capture remains sample-accurate.
        if (Math.floor(count.current / (context.sampleRate / 10)) !== Math.floor((count.current - chunk.length) / (context.sampleRate / 10))) {
          setDuration(count.current / context.sampleRate);
          const level = inspectAudio(chunk, context.sampleRate).rms;
          setLevels((previous) => [...previous.slice(-47), level]);
        }
        if (count.current >= context.sampleRate * 30) stop();
      };
      source.connect(node); node.connect(context.destination);
      stream.getTracks().forEach((track) => { track.onended = () => { setError('The microphone disconnected. Review this sample or record again.'); stop(); }; });
      setPhase('recording');
    } catch {
      stream?.getTracks().forEach((track) => track.stop());
      if (attempt !== generation.current) return;
      dispose(); busy.current = false; setPhase('idle');
      setError('Recording could not start. Allow microphone access and use a browser with Web Audio support and an 8-96 kHz audio device. You can still use an existing voice.');
    }
  }, [cancel, dispose, stop]);
  useEffect(() => {
    const leave = () => cancel();
    window.addEventListener('pagehide', leave);
    return () => { generation.current++; dispose(); chunks.current = []; window.removeEventListener('pagehide', leave); };
  }, [cancel, dispose]);
  return { phase, recording, duration, levels, error, start, stop, cancel };
}
