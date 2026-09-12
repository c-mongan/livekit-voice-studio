import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSession } from '@livekit/components-react';
import { ConnectionState, Room, RoomEvent, TokenSource } from 'livekit-client';
import { api, ApiError, errorMessage, type SessionGrant, type StudioStatus } from './api';

export function useStudio() {
  const [room] = useState(() => new Room({ adaptiveStream: true, dynacast: true }));
  const grantRef = useRef<SessionGrant | null>(null);
  const tokenSource = useMemo(() => TokenSource.literal(() => {
    const grant = grantRef.current;
    if (!grant) throw new Error('Start a Studio session first.');
    return { serverUrl: grant.serverUrl, participantToken: grant.participantToken, roomName: grant.roomName, participantName: grant.participantName };
  }), []);
  const session = useSession(tokenSource, { room, agentConnectTimeoutMilliseconds: 60_000 });
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [online, setOnline] = useState(false);
  const [grant, setGrant] = useState<SessionGrant | null>(null);
  const [starting, setStarting] = useState(false);
  const [ending, setEnding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [heartbeatError, setHeartbeatError] = useState<string | null>(null);
  const busy = useRef(false);
  const closing = useRef(false);
  const releaseRequested = useRef(false);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const next = await api<StudioStatus>('status');
      if (alive.current) {
        setStatus(next);
        setOnline(true);
      }
    } catch {
      if (alive.current) setOnline(false);
    }
  }, []);

  const disconnect = useCallback(async () => {
    // Stop capture immediately, even when signaling or broker cleanup is unavailable.
    for (const publication of room.localParticipant.trackPublications.values()) publication.track?.stop();
    await room.disconnect(true);
  }, [room]);

  const end = useCallback(async () => {
    if (closing.current) return;
    closing.current = true;
    releaseRequested.current = true;
    setEnding(true);
    const owned = grantRef.current;
    try {
      const local = disconnect();
      const remote = owned ? api<{ phase: 'draining' | 'idle' | 'blocked' }>('session/end', { sessionId: owned.sessionId }) : Promise.resolve();
      const results = await Promise.allSettled([local, remote]);
      if (results[1].status === 'rejected') throw results[1].reason;
      const response = results[1].value;
      if (response) setStatus((previous) => previous ? { ...previous, phase: response.phase } : previous);
      grantRef.current = null;
      setGrant(null);
      setHeartbeatError(null);
      if (results[0].status === 'rejected') setError('The browser could not finish disconnecting. Microphone capture has stopped; reload before starting again.');
    } catch (cause) {
      setError(errorMessage(cause, 'Session cleanup could not be confirmed. Retry End session; the server will also expire an abandoned session.'));
    } finally {
      busy.current = false;
      setStarting(false);
      closing.current = false;
      setEnding(false);
      await refresh();
    }
  }, [disconnect, refresh]);

  const start = useCallback(async (): Promise<boolean> => {
    if (busy.current || grantRef.current || !online || !status?.ready || status.phase !== 'idle') return false;
    busy.current = true;
    releaseRequested.current = false;
    setStarting(true);
    setError(null);
    try {
      const next = await api<SessionGrant>('session', {});
      grantRef.current = next;
      setGrant(next);
      // Connecting directly avoids useSession.start's optional dispatch wait and microphone default.
      // The broker already owns agent dispatch; the SDK hooks observe its actual arrival.
      // Also reset any publication left by an interrupted previous connection.
      await room.localParticipant.setMicrophoneEnabled(false);
      await room.connect(next.serverUrl, next.participantToken);
      if (!grantRef.current || releaseRequested.current) {
        await disconnect();
        return false;
      }
      return true;
    } catch (cause) {
      setError(errorMessage(cause, 'Could not connect to LiveKit. Check the server connection and start a fresh session; expired tokens are not reused.'));
      await end();
      return false;
    } finally {
      busy.current = false;
      setStarting(false);
      await refresh();
    }
  }, [disconnect, end, online, refresh, room, status]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4_000);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [refresh]);

  useEffect(() => {
    if (!grant || ending) return;
    let stopped = false;
    let pending = false;
    const heartbeat = async () => {
      if (stopped || pending || releaseRequested.current) return;
      pending = true;
      try {
        await api('session/heartbeat', { sessionId: grant.sessionId });
        if (!stopped) setHeartbeatError(null);
      } catch (cause) {
        if (stopped) return;
        if (cause instanceof ApiError && [403, 404, 409, 410].includes(cause.status)) {
          stopped = true;
          grantRef.current = null;
          setGrant(null);
          setError('This session is no longer owned by this browser. Audio has stopped. Wait for the server to become ready before starting again.');
          try { await disconnect(); } catch { setError('Session ownership was lost. Microphone capture has stopped; reload this page before continuing.'); }
          void refresh();
        } else {
          setHeartbeatError('The session heartbeat could not reach the local server. If it stays unavailable, the server will end this session.');
        }
      } finally {
        pending = false;
      }
    };
    const timer = window.setInterval(() => void heartbeat(), 10_000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [disconnect, ending, grant, refresh]);

  useEffect(() => {
    const disconnected = () => {
      if (grantRef.current && !closing.current && !busy.current) {
        setError('The LiveKit connection ended. The local session is being released; start a new session when it is ready.');
        void end();
      }
    };
    const pagehide = () => {
      const owned = grantRef.current;
      if (!owned) return;
      releaseRequested.current = true;
      for (const publication of room.localParticipant.trackPublications.values()) publication.track?.stop();
      grantRef.current = null;
      setGrant(null);
      void room.disconnect(true).catch(() => {
        // Capture was already stopped synchronously; the page is leaving.
      });
      void api('session/end', { sessionId: owned.sessionId }, true).catch(() => {
        // A page leaving cannot render feedback. Broker ownership expires without heartbeats.
      });
    };
    room.on(RoomEvent.Disconnected, disconnected);
    window.addEventListener('pagehide', pagehide);
    return () => {
      room.off(RoomEvent.Disconnected, disconnected);
      window.removeEventListener('pagehide', pagehide);
    };
  }, [end, room]);

  return {
    session, status, online, grant, starting, ending, error, heartbeatError,
    start, end, refresh, setError,
    connected: session.connectionState === ConnectionState.Connected,
  };
}
