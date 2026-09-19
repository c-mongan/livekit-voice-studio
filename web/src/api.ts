export interface StudioStatus {
  ready: boolean;
  phase: 'idle' | 'starting' | 'active' | 'draining' | 'blocked';
  problems: string[];
  voice: {
    name: string;
    engine: string;
    model: string;
    loaded: boolean;
    cached: boolean;
    backend?: 'voicebox' | 'mlx';
    streaming?: boolean;
    source?: 'voicebox' | 'local-bundle';
  };
  stt?: { provider: 'nemotron' | 'azure' | 'openai'; model: string; local: boolean };
  ai: { provider: 'azure' | 'openai' | 'copilot' | 'codex'; model: string; effort?: string; local?: false };
  livekit: { configured: boolean };
  session: { id: string; roomName: string } | null;
  metrics: {
    ttsFirstFrameSeconds: number | null;
    ttsAudioSeconds: number | null;
    llmFirstTokenSeconds: number | null;
    endOfUtteranceSeconds?: number | null;
    transcriptionDelaySeconds?: number | null;
  };
  message: string | null;
  turns?: Array<{
    id: string;
    llmFirstTokenSeconds?: number;
    ttsFirstFrameSeconds?: number;
    ttsAudioSeconds?: number;
    endOfUtteranceSeconds?: number;
    transcriptionDelaySeconds?: number;
  }>;
}

export interface SessionGrant {
  sessionId: string;
  serverUrl: string;
  participantToken: string;
  roomName: string;
  participantName: string;
  agentIdentity: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function safeMessage(value: unknown, fallback: string): string {
  if (typeof value !== 'string' || !value.trim()) return fallback;
  return value
    .replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[redacted token]')
    .replace(/(?:https?|wss?):\/\/[^\s]+/g, '[service address]')
    .slice(0, 500);
}

export async function api<T>(path: string, body?: object, keepalive = false): Promise<T> {
  return studioRequest<T>(path, { method: body ? 'POST' : 'GET', body, keepalive });
}

export interface ProviderOption { id: string; label: string; available: boolean; reason?: string }
export interface StudioConfig {
  sttProvider: string;
  llmProvider: string;
  llmModel: string;
  reasoningEffort: string;
  codexRestrictedApproved?: boolean;
  voiceId: string | null;
  providers: { stt: ProviderOption[]; llm: ProviderOption[] };
}
export interface LocalVoice { id: string; name: string; durationSeconds: number; source: 'local'; selected: boolean }
export interface VoiceLibrary { voices: LocalVoice[]; guidedText: string }

export interface AuditionStatus {
  auditionId: string;
  voiceId: string;
  phase: StudioStatus['phase'];
  state: 'generating' | 'ready' | 'cancelled' | 'failed';
  message: string | null;
}

export async function auditionAudio(auditionId: string): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch('/api/audition/audio', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-Voicebox-Studio': '1', 'Content-Type': 'application/json' },
      body: JSON.stringify({ auditionId }), signal: AbortSignal.timeout(15_000),
    });
  } catch {
    throw new ApiError(0, 'The generated sample could not be retrieved. Check the local server and generate another sample.');
  }
  if (!response.ok) {
    let data: { message?: unknown; error?: unknown } = {};
    try { data = await response.json(); } catch { /* Never display a non-JSON error body. */ }
    throw new ApiError(response.status, safeMessage(data.message ?? data.error, 'The generated sample is unavailable. Generate another sample.'));
  }
  if (!response.headers.get('Content-Type')?.toLowerCase().startsWith('audio/wav')) {
    throw new ApiError(0, 'The server did not return a WAV sample. Generate another sample.');
  }
  const blob = await response.blob();
  if (!blob.size) throw new ApiError(0, 'The generated sample was empty. Generate another sample.');
  return blob;
}

export async function studioRequest<T>(path: string, options: {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'; body?: object | FormData; keepalive?: boolean;
} = {}): Promise<T> {
  const { method = 'GET', body, keepalive = false } = options;
  const multipart = body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(`/api/${path}`, {
      method,
      credentials: 'same-origin',
      headers: method !== 'GET' ? { 'X-Voicebox-Studio': '1', ...(!multipart ? { 'Content-Type': 'application/json' } : {}) } : undefined,
      body: multipart ? body : body ? JSON.stringify(body) : undefined,
      keepalive,
      signal: keepalive ? undefined : AbortSignal.timeout(15_000),
    });
  } catch {
    throw new ApiError(0, 'The local Studio server is not responding. Check that it is running, then retry.');
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(response.status, 'The Studio server returned an unreadable response.');
  }
  if (!response.ok) {
    const value = data as { message?: unknown; error?: unknown };
    throw new ApiError(response.status, safeMessage(value?.message ?? value?.error, `Studio request failed (${response.status}). Please retry.`));
  }
  return data as T;
}

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function measuredSeconds(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? `${value.toFixed(2)} s`
    : 'Not measured';
}
