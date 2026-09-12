import type { StudioStatus } from './api';

export type WorkspaceState = 'offline' | 'ready' | 'connecting' | 'listening' | 'thinking' | 'speaking' | 'reconnecting' | 'draining' | 'blocked';

export function workspaceState(input: {
  status: StudioStatus | null;
  online: boolean;
  starting: boolean;
  ending: boolean;
  owned: boolean;
  connection: string;
  agent: string;
  mic: boolean;
}): WorkspaceState {
  if (input.ending || input.status?.phase === 'draining') return 'draining';
  if (input.connection === 'reconnecting' || input.connection === 'signalReconnecting') return 'reconnecting';
  if (input.starting || (input.owned && ['connecting', 'initializing', 'idle', 'pre-connect-buffering'].includes(input.agent))) return 'connecting';
  if (input.owned && input.connection === 'connected') {
    if (input.agent === 'failed' || input.agent === 'disconnected') return 'blocked';
    if (input.agent === 'speaking' || input.agent === 'thinking') return input.agent;
    if (input.agent === 'listening' && input.mic) return 'listening';
    return 'ready';
  }
  if (!input.online || !input.status) return 'offline';
  if (!input.status.ready || input.status.phase !== 'idle') return 'blocked';
  return 'ready';
}

export interface TranscriptMessage {
  id: string;
  message: string;
  role: 'you' | 'agent';
  timestamp: number;
}

export function mergeTranscript(
  previous: TranscriptMessage[],
  incoming: TranscriptMessage[],
  cleared: ReadonlySet<string>,
): TranscriptMessage[] {
  const result = new Map(previous.map((entry) => [entry.id, entry]));
  for (const entry of incoming) {
    if (!cleared.has(entry.id) && entry.message.trim()) result.set(entry.id, entry);
  }
  return [...result.values()];
}

export const MAX_MESSAGE_LENGTH = 800;
export function validMessage(text: string): boolean {
  return text.trim().length > 0 && text.length <= MAX_MESSAGE_LENGTH;
}
