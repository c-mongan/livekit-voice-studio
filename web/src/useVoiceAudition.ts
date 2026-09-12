import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, auditionAudio, errorMessage, safeMessage, type AuditionStatus, type StudioStatus } from './api';

type Phase = 'idle' | 'generating' | 'cancelling' | 'ready' | 'error';
type Job = { id: string | null; voiceId: string; cancelled: boolean; endRequested: boolean; keepalive: boolean };
const pause = () => new Promise<void>((resolve) => window.setTimeout(resolve, 1000));

export function useVoiceAudition(onBusyChange: ((busy: boolean) => void) | undefined, refreshStatus: () => void | Promise<void>) {
  const [phase, setPhase] = useState<Phase>('idle');
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  const job = useRef<Job | null>(null);
  const audio = useRef<string | null>(null);
  const busyCallback = useRef(onBusyChange);
  const refreshCallback = useRef(refreshStatus);
  busyCallback.current = onBusyChange;
  refreshCallback.current = refreshStatus;

  const discardAudio = useCallback(() => {
    if (audio.current) URL.revokeObjectURL(audio.current);
    audio.current = null;
    if (alive.current) setUrl(null);
  }, []);
  const cancel = useCallback(() => {
    discardAudio();
    if (job.current) {
      job.current.cancelled = true;
      job.current.endRequested = true;
      if (alive.current) setPhase('cancelling');
    } else if (alive.current) { setPhase('idle'); setError(null); }
  }, [discardAudio]);
  useEffect(() => {
    alive.current = true;
    const leave = () => {
      const current = job.current;
      if (current) {
        current.keepalive = true;
        if (current.id) void api('audition/end', { auditionId: current.id }, true).catch(() => {});
      }
      cancel();
    };
    window.addEventListener('pagehide', leave);
    return () => {
      alive.current = false;
      leave();
      window.removeEventListener('pagehide', leave);
    };
  }, [cancel]);

  function generate(voiceId: string, text: string) {
    if (job.current || !voiceId || !text.trim() || text.length > 300) return;
    discardAudio();
    const current: Job = { id: null, voiceId, cancelled: false, endRequested: false, keepalive: false };
    job.current = current;
    setPhase('generating'); setBusy(true); setError(null); busyCallback.current?.(true);
    void run(current, text.trim());
  }

  async function run(current: Job, text: string) {
    let failure: string | null = null;
    let result: Blob | null = null;
    let completed = false;
    try {
      const started = await api<{ auditionId: string; voiceId: string }>('audition', { voiceId: current.voiceId, text });
      if (!started.auditionId) throw new Error('Invalid audition response');
      current.id = started.auditionId;
      if (started.voiceId !== current.voiceId) {
        failure = 'The server returned a different voice. Choose your voice and try again.';
        current.cancelled = true; current.endRequested = true;
      }
      // This job outlives its view so even a late start is explicitly cancelled.
      while (true) {
        try {
          if (current.endRequested) {
            const ended = await api<{ phase: StudioStatus['phase'] }>('audition/end', { auditionId: current.id }, current.keepalive || !alive.current);
            current.endRequested = false;
            if (ended.phase === 'idle') break;
          }
          const status: AuditionStatus = await api<AuditionStatus>('audition/status', { auditionId: current.id });
          if (status.auditionId !== current.id || status.voiceId !== current.voiceId) {
            failure = 'The sample no longer matches this voice. Choose your voice and try again.';
            current.cancelled = true; current.endRequested = true;
          } else if (status.phase === 'idle') {
            if (!completed && !current.cancelled && status.state === 'ready') {
              try { result = await auditionAudio(current.id); }
              catch (cause) { failure = errorMessage(cause, 'The generated sample could not be retrieved. Generate another sample.'); }
              completed = true;
              current.endRequested = true;
            } else if (!completed && !current.cancelled && status.state !== 'cancelled') {
              failure = safeMessage(status.message, 'The sample could not be generated. Check local Qwen setup and try again.');
              completed = true;
              current.endRequested = true;
            }
            if (!current.endRequested) break;
          } else if (status.state === 'failed' || status.phase === 'blocked') {
            failure = safeMessage(status.message, 'Local generation needs attention. Cancel and check the local worker.');
            if (alive.current) setError(failure);
          }
          if (current.endRequested) continue;
        } catch (cause) {
          if (cause instanceof ApiError && (cause.status === 404 || cause.status === 410)) {
            failure = 'This audition expired or was invalidated. Generate another sample when Studio is idle.';
            current.id = null;
            current.cancelled = true;
            result = null;
            discardAudio();
            break;
          }
          const cleanupError = errorMessage(cause, 'Could not confirm audition cleanup. Keep Studio open and check the local server.');
          if (alive.current) setError(cleanupError);
          if (current.cancelled) current.endRequested = true;
        }
        await pause();
      }
    } catch (cause) {
      failure = errorMessage(cause, 'The audition could not start. Check local Qwen setup and try again.');
    } finally {
      current.id = null;
      if (alive.current && failure) setError(failure);
      // Losing a handle says nothing about a newer room or job. Refresh the
      // public phase (or offline state) before releasing either local busy gate.
      while (alive.current) {
        try { await refreshCallback.current(); break; }
        catch {
          if (alive.current) setError('Could not refresh Studio status. Check the local server; other changes remain paused.');
          await pause();
        }
      }
      if (job.current === current) job.current = null;
      if (alive.current) {
        if (result && !current.cancelled) {
          audio.current = URL.createObjectURL(result); setUrl(audio.current); setPhase('ready'); setError(null);
        } else {
          setPhase(failure ? 'error' : 'idle'); setError(failure);
        }
        setBusy(false); busyCallback.current?.(false);
      }
    }
  }
  return { phase, busy, url, error, generate, cancel };
}
