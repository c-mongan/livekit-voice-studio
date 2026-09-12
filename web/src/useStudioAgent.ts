import { useEffect, useState } from 'react';
import { useParticipantAttributes, useParticipantTracks, useRemoteParticipant } from '@livekit/components-react';
import { Track } from 'livekit-client';

type AgentState = 'disconnected' | 'connecting' | 'initializing' | 'idle' | 'listening' | 'thinking' | 'speaking' | 'failed';
const readyStates = new Set(['listening', 'thinking', 'speaking']);
const reportedStates = new Set(['initializing', 'idle', ...readyStates]);

export function useStudioAgent(agentIdentity: string | undefined, connectionState: string) {
  const participant = useRemoteParticipant(agentIdentity ?? '');
  // This native subscription seeds its snapshot when a participant arrives, unlike
  // useAgent 2.9.20's initial-only attribute state. Read the exact participant below.
  useParticipantAttributes({ participant });
  const tracks = useParticipantTracks([Track.Source.Microphone], { participantIdentity: agentIdentity });
  const matches = !!agentIdentity && participant?.identity === agentIdentity;
  const reported = matches ? participant.attributes['lk.agent.state'] : undefined;
  const isConnected = connectionState === 'connected' && matches && !!reported && readyStates.has(reported);
  const waiting = connectionState === 'connected' && !!agentIdentity && !isConnected;
  const [timedOutIdentity, setTimedOutIdentity] = useState<string | null>(null);

  useEffect(() => {
    setTimedOutIdentity(null);
    if (!waiting || !agentIdentity) return;
    const timer = window.setTimeout(() => setTimedOutIdentity(agentIdentity), 60_000);
    return () => window.clearTimeout(timer);
  }, [agentIdentity, waiting]);

  let state: AgentState = 'disconnected';
  if (connectionState !== 'disconnected' && agentIdentity) {
    state = !isConnected && timedOutIdentity === agentIdentity ? 'failed'
      : matches && reported && reportedStates.has(reported) ? reported as AgentState
      : matches ? 'initializing' : 'connecting';
  }
  return {
    state,
    isConnected,
    microphoneTrack: matches ? tracks.find((track) => track.participant.identity === agentIdentity) : undefined,
  };
}
