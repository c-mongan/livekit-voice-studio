// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { deferred, readyStatus, testGrant } from './test-fixtures';

const mocks = vi.hoisted(() => ({
  studio: {} as Record<string, any>,
  agent: {} as Record<string, any>,
  local: {} as Record<string, any>,
  chat: {} as Record<string, any>,
  mute: vi.fn(),
  bands: vi.fn(),
  roomHandlers: {} as Record<string, (...args: any[]) => void>,
}));
vi.mock('./useStudio', () => ({ useStudio: () => mocks.studio }));
vi.mock('./useStudioAgent', () => ({ useStudioAgent: () => mocks.agent }));
vi.mock('./components/agent-session-provider', () => ({
  AgentSessionProvider: ({ children, muted }: { children: ReactNode; muted: boolean }) => {
    mocks.mute(muted);
    return children;
  },
}));
vi.mock('@livekit/components-react', () => ({
  useAgent: () => mocks.agent,
  useSessionMessages: () => mocks.chat,
  useLocalParticipant: () => mocks.local,
  useMultibandTrackVolume: mocks.bands,
  StartAudio: ({ label }: { label: string }) => <button>{label}</button>,
}));

let root: Root;
let host: HTMLDivElement;
let mediaQuery: { matches: boolean; addEventListener: ReturnType<typeof vi.fn>; removeEventListener: ReturnType<typeof vi.fn> };
const render = () => root.render(<App />);
async function mount() { await act(async () => render()); }
function button(text: string) {
  const match = [...host.querySelectorAll('button')].find((element) => element.textContent?.trim() === text);
  if (!match) throw new Error(`Missing button: ${text}`);
  return match;
}
async function click(element: HTMLElement) { await act(async () => element.click()); }
async function type(text: string) {
  const textarea = host.querySelector('textarea')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(textarea, text);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function key(key: string, options: KeyboardEventInit = {}) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...options });
  await act(async () => host.querySelector('textarea')!.dispatchEvent(event));
  return event;
}
function connect() {
  mocks.studio.grant = testGrant;
  mocks.studio.connected = true;
  mocks.studio.starting = false;
  mocks.studio.session.connectionState = 'connected';
  mocks.agent.state = 'listening';
  mocks.agent.isConnected = true;
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  mediaQuery = { matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() };
  vi.stubGlobal('matchMedia', vi.fn(() => mediaQuery));
  mocks.bands.mockReturnValue(new Array(36).fill(0));
  mocks.agent = { state: 'disconnected', isConnected: false, microphoneTrack: undefined };
  const participant = { identity: 'test-you', get isMicrophoneEnabled() { return mocks.local.isMicrophoneEnabled; }, setMicrophoneEnabled: vi.fn().mockResolvedValue(undefined), performRpc: vi.fn().mockResolvedValue('{"accepted":true}') };
  mocks.local = { isMicrophoneEnabled: false, localParticipant: participant };
  mocks.chat = { messages: [], send: vi.fn().mockResolvedValue({ id: 'sent' }), isSending: false };
  mocks.roomHandlers = {};
  const room = {
    localParticipant: participant,
    on: vi.fn((event: string, handler: (...args: any[]) => void) => { mocks.roomHandlers[event] = handler; }),
    off: vi.fn((event: string, handler: (...args: any[]) => void) => {
      if (mocks.roomHandlers[event] === handler) delete mocks.roomHandlers[event];
    }),
  };
  mocks.studio = {
    status: structuredClone(readyStatus), online: true, grant: null, connected: false,
    starting: false, ending: false, error: null, heartbeatError: null,
    session: { connectionState: 'disconnected', room, local: {} },
    start: vi.fn().mockResolvedValue(true), end: vi.fn().mockResolvedValue(undefined), refresh: vi.fn(),
    setError: vi.fn((error) => { mocks.studio.error = error; render(); }),
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('typed-first and keyboard interaction', () => {
  it('keeps measured latency in an optional native disclosure without hiding setup status', async () => {
    await mount();
    const summary = [...host.querySelectorAll('summary')].find((item) => item.textContent === 'Measured latency')!;
    expect(summary).toBeDefined();
    const details = summary.closest('details')!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain('LLM first token');
    expect(details.textContent).toContain('Not an end-to-end response-time estimate');
    await click(summary);
    expect(details.open).toBe(true);
    expect(host.querySelector('.session-status')?.closest('details')).toBeNull();
    await click(summary);
    expect(details.open).toBe(false);
  });
  it.each(['starting', 'active', 'draining', 'blocked'] as const)('keeps a refreshed %s backend protected after audition ownership loss', async (phase) => {
    vi.useFakeTimers();
    const ownedStatus = deferred<Response>();
    const publicStatus = deferred<void>();
    mocks.studio.refresh = vi.fn(() => publicStatus.promise);
    HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    HTMLDialogElement.prototype.close = function () { this.open = false; };
    const fetch = vi.fn(async (url: string) => {
      if (url === '/api/audition') return new Response('{"auditionId":"expired","voiceId":"one","phase":"starting"}');
      if (url === '/api/audition/status') return ownedStatus.promise;
      if (url === '/api/audition/end') return new Response('{"message":"Invalid handle."}', { status: 404 });
      return new Response(JSON.stringify(url.endsWith('/settings') ? {
        sttProvider: 'nemotron', llmProvider: 'copilot', llmModel: 'gpt-5.6-luna', voiceId: 'one',
        providers: { stt: [], llm: [] },
      } : { voices: [{ id: 'one', name: 'My voice', durationSeconds: 10, selected: true }], guidedText: 'Read me.' }));
    });
    vi.stubGlobal('fetch', fetch);
    await mount();
    await click(host.querySelector('input[type=checkbox]')!);
    await type('Hello.');
    await click(button('Voice library')); await click(button('Audition clone'));
    await click(button('Generate sample'));
    await act(async () => ownedStatus.resolve(new Response('{"message":"Expired."}', { status: 410 })));
    expect(mocks.studio.refresh).toHaveBeenCalledTimes(1);
    expect(button('Record a voice').disabled).toBe(true);
    expect(button('Start session').disabled).toBe(true);
    await act(async () => { mocks.studio.status.phase = phase; publicStatus.resolve(); render(); });
    expect(host.textContent).not.toContain('Audition in progress');
    expect(host.querySelector('[role=alert]')?.textContent).toContain('expired or was invalidated');
    expect(button('Record a voice').disabled).toBe(true);
    expect(button('Generate sample').disabled).toBe(true);
    expect(button('Start & send').disabled).toBe(true);
    expect(host.querySelector<HTMLButtonElement>('.voice-controls > button')!.disabled).toBe(true);
    const requests = fetch.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetch).toHaveBeenCalledTimes(requests);
    expect(mocks.studio.start).not.toHaveBeenCalled();
  });
  it('blocks both session start paths immediately during a local audition, including before status polls', async () => {
    const start = deferred<Response>();
    HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    HTMLDialogElement.prototype.close = function () { this.open = false; };
    const fetch = vi.fn(async (url: string) => {
      if (url === '/api/audition') return start.promise;
      if (url.endsWith('/end')) return new Response('{"phase":"idle"}');
      return new Response(JSON.stringify(url.endsWith('/settings') ? {
        sttProvider: 'nemotron', llmProvider: 'copilot', llmModel: 'gpt-5.6-luna', voiceId: 'one',
        providers: { stt: [], llm: [] },
      } : { voices: [{ id: 'one', name: 'My voice', durationSeconds: 10, selected: true }], guidedText: 'Read me.' }));
    });
    vi.stubGlobal('fetch', fetch);
    await mount();
    await click(host.querySelector('input[type=checkbox]')!);
    await type('Hello.');
    expect(button('Start session').disabled).toBe(false);
    await click(button('Voice library')); await click(button('Audition clone'));
    await click(button('Generate sample'));
    expect(button('Start session').disabled).toBe(true);
    expect(button('Start & send').disabled).toBe(true);
    expect(host.textContent).toContain('Audition in progress');
    await click(button('Close'));
    expect(button('Start session').disabled).toBe(true);
    await act(async () => start.resolve(new Response('{"auditionId":"late","voiceId":"one","phase":"starting"}')));
    expect(button('Start session').disabled).toBe(false);
    expect(mocks.studio.start).not.toHaveBeenCalled();
  });
  it('requires renewed privacy acknowledgement when the speech data route changes', async () => {
    mocks.studio.status.stt = { provider: 'nemotron', model: 'local', local: true };
    await mount();
    await click(host.querySelector('input[type=checkbox]')!);
    expect(button('Start session').disabled).toBe(false);
    await act(async () => { mocks.studio.status.stt = { provider: 'azure', model: 'cloud', local: false }; render(); });
    expect(button('Start session').disabled).toBe(true);
  });
  it('explains standalone voice bundles without implying Voicebox must stay open', async () => {
    mocks.studio.status.voice.source = 'local-bundle';
    mocks.studio.status.voice.backend = 'mlx';
    mocks.studio.status.voice.streaming = true;
    await mount();
    expect(host.textContent).toContain('Private local bundle');
    expect(host.textContent).toContain('Voicebox can stay closed');
    expect(host.textContent).toContain('reference is not uploaded');
  });
  it('requires explicit privacy acknowledgement and never requests mic on mount', async () => {
    await mount();
    expect(button('Start session').disabled).toBe(true);
    await type('Hello');
    expect(button('Start & send').disabled).toBe(true);
    expect(mocks.studio.start).not.toHaveBeenCalled();
    expect(mocks.local.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalled();
    await click(host.querySelector('input[type=checkbox]')!);
    expect(button('Start session').disabled).toBe(false);
    expect(button('Start & send').disabled).toBe(false);
  });

  it('starts from typed text, waits for the real agent, sends once, and never enables mic', async () => {
    const roomReady = deferred<void>();
    mocks.studio.start.mockImplementation(async () => {
      mocks.studio.starting = true;
      render();
      await roomReady.promise;
      mocks.studio.grant = testGrant;
      mocks.studio.starting = false;
      mocks.studio.connected = true;
      mocks.studio.session.connectionState = 'connected';
      mocks.agent.state = 'initializing';
      render();
      return true;
    });
    await mount();
    await click(host.querySelector('input')!);
    await type('Explain this pipeline.');
    await click(button('Start & send'));
    expect(mocks.studio.start).toHaveBeenCalledTimes(1);
    expect(mocks.chat.send).not.toHaveBeenCalled();
    await act(async () => { roomReady.resolve(); });
    expect(mocks.chat.send).not.toHaveBeenCalled();
    await act(async () => { connect(); render(); });
    expect(mocks.chat.send).toHaveBeenCalledExactlyOnceWith('Explain this pipeline.');
    expect(host.querySelector('textarea')!.value).toBe('');
    expect(mocks.local.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalled();
  });

  it('keeps unsent text and recovery instructions when sending fails', async () => {
    connect();
    mocks.chat.send.mockRejectedValueOnce(new Error('raw private diagnostics'));
    await mount();
    await type('Keep my draft');
    await click(button('Send'));
    expect(host.querySelector('textarea')!.value).toBe('Keep my draft');
    expect(host.querySelector('[role=alert]')?.textContent).toContain('still in the composer');
    expect(host.textContent).not.toContain('raw private');
    expect(button('Send').disabled).toBe(false);
  });

  it('Enter submits, Shift+Enter is not intercepted, and IME composition does not submit', async () => {
    connect();
    await mount();
    await type('Keyboard message');
    const newline = await key('Enter', { shiftKey: true });
    expect(newline.defaultPrevented).toBe(false);
    const composing = await key('Enter', { isComposing: true });
    expect(composing.defaultPrevented).toBe(false);
    expect(mocks.chat.send).not.toHaveBeenCalled();
    const enter = await key('Enter');
    expect(enter.defaultPrevented).toBe(true);
    expect(mocks.chat.send).toHaveBeenCalledExactlyOnceWith('Keyboard message');
  });

  it('blocks empty messages and exposes native character limit and associated labels', async () => {
    connect();
    await mount();
    expect(button('Send').disabled).toBe(true);
    const textarea = host.querySelector('textarea')!;
    expect(textarea.maxLength).toBe(800);
    expect(host.querySelector(`label[for="${textarea.id}"]`)?.textContent).toBe('Message your agent');
    expect(document.getElementById(textarea.getAttribute('aria-describedby')!)).not.toBeNull();
    expect(host.querySelector('[role=log]')?.getAttribute('tabindex')).toBe('0');
  });

  it('a suggested question fills and focuses the composer without sending', async () => {
    await mount();
    const suggestion = [...host.querySelectorAll('button')].find((element) => element.textContent?.startsWith('Try “Explain'))!;
    await click(suggestion);
    expect(host.querySelector('textarea')!.value).toBe('Explain how this voice pipeline works.');
    expect(document.activeElement).toBe(host.querySelector('textarea'));
    expect(mocks.studio.start).not.toHaveBeenCalled();
    expect(mocks.chat.send).not.toHaveBeenCalled();
  });
});

describe('microphone, playback and lifecycle controls', () => {
  it('does not show microphone-on from a stale hook snapshot after reconnect', async () => {
    connect();
    mocks.local.isMicrophoneEnabled = true;
    Object.defineProperty(mocks.local.localParticipant, 'isMicrophoneEnabled', { configurable: true, value: false });
    await mount();
    expect(button('Turn mic on').getAttribute('aria-pressed')).toBe('false');
    expect(host.querySelector('.session-status')?.textContent).toContain('Ready');
    expect(host.textContent).toContain('Microphone is off.');
  });

  it('keeps a 23-second generation in its real thinking state without inventing a failure', async () => {
    vi.useFakeTimers();
    connect();
    mocks.agent.state = 'thinking';
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(23_000); });
    expect(host.querySelector('.session-status')?.textContent).toContain('Thinking');
    expect(host.textContent).toContain('Local speech generation can take tens of seconds');
    expect(host.querySelector('[role=alert]')).toBeNull();
    expect(button('Stop reply').disabled).toBe(false);
    expect(button('End session').disabled).toBe(false);
    expect(mocks.studio.end).not.toHaveBeenCalled();
  });

  it('mic denial explains typed fallback and focuses an enabled composer', async () => {
    connect();
    mocks.local.localParticipant.setMicrophoneEnabled.mockRejectedValueOnce(new DOMException('Denied', 'NotAllowedError'));
    await mount();
    await click(button('Turn mic on'));
    expect(mocks.local.localParticipant.setMicrophoneEnabled).toHaveBeenCalledExactlyOnceWith(true);
    expect(host.querySelector('[role=alert]')?.textContent).toContain('You can still type');
    expect(document.activeElement).toBe(host.querySelector('textarea'));
    await type('No microphone needed');
    expect(button('Send').disabled).toBe(false);
    expect(button('Turn mic on').getAttribute('aria-pressed')).toBe('false');
  });

  it('toggling an enabled microphone sends false and exposes pressed state', async () => {
    connect();
    mocks.local.isMicrophoneEnabled = true;
    await mount();
    expect(button('Turn mic off').getAttribute('aria-pressed')).toBe('true');
    await click(button('Turn mic off'));
    expect(mocks.local.localParticipant.setMicrophoneEnabled).toHaveBeenCalledExactlyOnceWith(false);
  });

  it('stop uses the granted RPC, mutes through acknowledgement, and states action truth', async () => {
    connect();
    mocks.agent.state = 'speaking';
    const ack = deferred<string>();
    mocks.local.localParticipant.performRpc.mockReturnValue(ack.promise);
    await mount();
    await click(button('Stop reply'));
    expect(mocks.local.localParticipant.performRpc).toHaveBeenCalledExactlyOnceWith({
      destinationIdentity: testGrant.agentIdentity, method: 'voicebox.interrupt', payload: '{}', responseTimeout: 5_000,
    });
    expect(mocks.mute).toHaveBeenLastCalledWith(true);
    expect(button('Stopping…').disabled).toBe(true);
    await act(async () => ack.resolve(JSON.stringify({ stoppedPlayback: true, hermesStopRequested: true, actionUndone: false, backendState: 'ready' })));
    expect(mocks.mute).toHaveBeenLastCalledWith(false);
    expect(button('Stop reply').disabled).toBe(false);
    expect(host.textContent).toContain('Speech stopped. Any completed Hermes action remains completed.');
    await act(async () => { mocks.agent.state = 'speaking'; render(); });
    expect(mocks.mute).toHaveBeenLastCalledWith(false);
  });

  it('does not restore audio before acknowledgement if speaking ends first', async () => {
    connect();
    mocks.agent.state = 'speaking';
    const ack = deferred<string>();
    mocks.local.localParticipant.performRpc.mockReturnValue(ack.promise);
    await mount();
    await click(button('Stop reply'));
    await act(async () => { mocks.agent.state = 'listening'; render(); });
    expect(mocks.mute).toHaveBeenLastCalledWith(true);
    await act(async () => ack.resolve('{}'));
    expect(mocks.mute).toHaveBeenLastCalledWith(false);
    expect(host.textContent).toContain('Speech stopped. Any completed Hermes action remains completed.');
  });

  it('failed stop restores playback and gives an actionable End session fallback', async () => {
    connect();
    mocks.agent.state = 'thinking';
    mocks.local.localParticipant.performRpc.mockRejectedValueOnce(new Error('RPC secret'));
    await mount();
    await click(button('Stop reply'));
    expect(mocks.mute).toHaveBeenLastCalledWith(false);
    expect(host.querySelector('[role=alert]')?.textContent).toContain('Use End session');
    expect(host.textContent).not.toContain('RPC secret');
    await click(button('End session'));
    expect(mocks.studio.end).toHaveBeenCalledTimes(1);
  });

  it('disables sending and mic during reconnect, and blocks new starts during draining', async () => {
    connect();
    mocks.studio.connected = false;
    mocks.studio.session.connectionState = 'reconnecting';
    await mount();
    await type('Wait for reconnection');
    expect(button('Send').disabled).toBe(true);
    expect(button('Turn mic on').disabled).toBe(true);
    expect(host.textContent).toContain('Sending is paused');
    await act(async () => {
      mocks.studio.grant = null;
      mocks.studio.status.phase = 'draining';
      mocks.studio.session.connectionState = 'disconnected';
      render();
    });
    expect(button('Finishing session…').disabled).toBe(true);
  });

  it('does not use a null public session handle to reclaim a foreign active room', async () => {
    mocks.studio.status.phase = 'active';
    mocks.studio.status.session = null;
    await mount();
    await click(host.querySelector('input')!);
    expect(button('Start session').disabled).toBe(true);
    expect(host.textContent).toContain('owning browser');
    expect(mocks.studio.start).not.toHaveBeenCalled();
  });
});

describe('Hermes approval flow', () => {
  const approval = {
    runId: 'run-approval', requestId: 'request-approval', command: 'rm redacted-file', choices: ['once', 'deny'],
  };

  async function receive(value: object, sender = testGrant.agentIdentity) {
    await act(async () => mocks.roomHandlers.dataReceived(
      new TextEncoder().encode(JSON.stringify(value)), { identity: sender }, 0, 'hermes.approval.request',
    ));
  }

  it('accepts requests only from the granted agent and sends exact owner RPC data', async () => {
    connect();
    await mount();
    await receive(approval, 'other-participant');
    expect(host.textContent).not.toContain('rm redacted-file');
    await receive({ ...approval, unknown: true });
    expect(host.textContent).not.toContain('rm redacted-file');
    await receive(approval);
    expect(host.textContent).toContain('rm redacted-file');

    await click(button('Deny'));
    expect(mocks.local.localParticipant.performRpc).toHaveBeenCalledExactlyOnceWith({
      destinationIdentity: testGrant.agentIdentity,
      method: 'hermes.approval.respond',
      payload: JSON.stringify({ runId: 'run-approval', requestId: 'request-approval', choice: 'deny' }),
      responseTimeout: 5_000,
    });
    expect(host.textContent).not.toContain('rm redacted-file');
  });

  it('clears the matching approval when a run becomes terminal', async () => {
    connect();
    mocks.agent.state = 'thinking';
    await mount();
    await receive(approval);
    expect(host.textContent).toContain('rm redacted-file');
    await act(async () => { mocks.agent.state = 'listening'; render(); });
    expect(host.textContent).not.toContain('rm redacted-file');
  });
});

describe('truthful transcript and accessible visual state', () => {
  it('keeps overflowing empty-state guidance at the top on initial render', async () => {
    vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockReturnValue(280);
    vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(100);
    await mount();
    const transcript = host.querySelector<HTMLElement>('.transcript')!;
    expect(transcript.querySelector('h3')?.textContent).toBe('Make room for a good conversation.');
    expect(transcript.scrollTop).toBe(0);
  });
  it('resets cleared history to the top and resumes following new messages', async () => {
    mocks.chat.messages = [{ id: 'old', type: 'agentTranscript', message: 'Previous reply', timestamp: 1 }];
    await mount();
    const transcript = host.querySelector<HTMLElement>('.transcript')!;
    Object.defineProperties(transcript, { scrollHeight: { value: 400 }, clientHeight: { value: 100 } });
    transcript.scrollTop = 80;
    await act(async () => transcript.dispatchEvent(new Event('scroll', { bubbles: true })));
    await click(button('Clear transcript'));
    expect(transcript.scrollTop).toBe(0);
    expect(transcript.querySelector('.empty-conversation')).not.toBeNull();
    await act(async () => transcript.dispatchEvent(new Event('scroll', { bubbles: true })));
    await act(async () => {
      mocks.chat.messages = [...mocks.chat.messages, { id: 'new', type: 'agentTranscript', message: 'New reply', timestamp: 2 }];
      render();
    });
    expect(transcript.scrollTop).toBe(400);
  });
  it('does not move a reader who scrolled up in nonempty conversation history', async () => {
    mocks.chat.messages = [{ id: 'first', type: 'agentTranscript', message: 'First reply', timestamp: 1 }];
    await mount();
    const transcript = host.querySelector<HTMLElement>('.transcript')!;
    Object.defineProperties(transcript, { scrollHeight: { value: 400 }, clientHeight: { value: 100 } });
    transcript.scrollTop = 80;
    await act(async () => transcript.dispatchEvent(new Event('scroll', { bubbles: true })));
    await act(async () => {
      mocks.chat.messages = [...mocks.chat.messages, { id: 'second', type: 'agentTranscript', message: 'Second reply', timestamp: 2 }];
      render();
    });
    expect(transcript.scrollTop).toBe(80);
  });
  it('defaults to complete-WAV Voicebox behavior when optional backend fields are absent', async () => {
    await mount();
    expect(host.querySelector('.pipeline li:last-child p')?.textContent).toBe('Voicebox · on this machine');
    expect(host.querySelector('.about-body')?.textContent).toContain('complete WAV audio');
    expect(host.textContent).not.toContain('Qwen · local streaming');
  });

  it('labels Hermes locality as control-plane location rather than local reasoning', async () => {
    mocks.studio.status.ai = {
      provider: 'hermes', model: 'profile-default', effort: 'none', local: true, profile: 'default',
    };

    await mount();

    const reasoning = host.querySelectorAll('.pipeline li')[1];
    expect(reasoning.textContent).toContain('Hermes · profile-default');
    expect(reasoning.textContent).toContain('Local Hermes control plane');
    expect(reasoning.textContent).not.toContain('Local model');
    expect(host.querySelector('.about-body')?.textContent).toContain('Hermes controls model routing');
  });

  it('describes reported MLX PCM streaming without claiming a cold worker is loaded', async () => {
    mocks.studio.status.voice = { ...readyStatus.voice, backend: 'mlx', streaming: true, loaded: false };
    await mount();
    expect(host.querySelector('.pipeline li:last-child p')?.textContent).toBe('Qwen · local streaming');
    expect(host.querySelector('.small-status')?.textContent).toBe('Prepares at start');
    expect(host.querySelector('.small-status')?.classList.contains('good')).toBe(false);
    expect(host.querySelector('.about-body')?.textContent).toContain('streams PCM audio locally');
    expect(host.querySelector('.about-body')?.textContent).toContain('not text tokens');
    expect(host.querySelector('.about-body')?.textContent).not.toContain('complete WAV');
    expect(host.querySelector('.about-body')?.textContent).toContain('prepare at session start');
    expect(host.textContent).not.toContain('Loads on first reply');
  });

  it('does not infer streaming or warm readiness from the MLX backend name alone', async () => {
    mocks.studio.status.voice = { ...readyStatus.voice, backend: 'mlx', streaming: false, loaded: false, cached: false };
    await mount();
    expect(host.querySelector('.pipeline li:last-child p')?.textContent).toBe('Qwen · on this machine');
    expect(host.querySelector('.about-body')?.textContent).toContain('has not reported PCM streaming');
    expect(host.textContent).toContain('Not cached');
    expect(host.querySelector('.small-status')?.textContent).not.toBe('Loaded');
  });

  it('shows loaded only when the worker reports it and uses streaming-aware waiting copy', async () => {
    mocks.studio.status.voice = { ...readyStatus.voice, backend: 'mlx', streaming: true, loaded: true };
    connect();
    mocks.agent.state = 'thinking';
    await mount();
    expect(host.querySelector('.small-status')?.textContent).toBe('Loaded');
    expect(host.querySelector('.state-help')?.textContent).toContain('streams audio as it is generated');
    expect(host.querySelector('.state-help')?.textContent).not.toContain('tens of seconds');
  });

  it('shows only real messages, safely as text, and clearing does not call the agent', async () => {
    connect();
    mocks.chat.messages = [{ id: 'real', type: 'agentTranscript', message: '<script>not executable</script>', timestamp: 1, from: { identity: testGrant.agentIdentity } }];
    await mount();
    expect(host.querySelector('.message')?.textContent).toContain('<script>not executable</script>');
    expect(host.querySelector('.message script')).toBeNull();
    await click(button('Clear transcript'));
    expect(host.querySelectorAll('.message')).toHaveLength(0);
    await act(async () => { mocks.chat.messages = [{ ...mocks.chat.messages[0], message: 'Later update to cleared ID' }]; render(); });
    expect(host.querySelectorAll('.message')).toHaveLength(0);
    expect(mocks.chat.send).not.toHaveBeenCalled();
    expect(mocks.local.localParticipant.performRpc).not.toHaveBeenCalled();
  });

  it('does not invent measurements or animate silence', async () => {
    await mount();
    expect(host.querySelectorAll('.metrics dd')).toHaveLength(3);
    expect([...host.querySelectorAll('.metrics dd')].map((element) => element.textContent)).toEqual(['Not measured', 'Not measured', 'Not measured']);
    expect([...host.querySelectorAll<HTMLElement>('.audio-meter span')].every((element) => element.style.getPropertyValue('--level') === '0.035')).toBe(true);
    expect(mocks.bands).toHaveBeenLastCalledWith(undefined, expect.any(Object));
  });

  it('reduced motion disables audio analysis and retains textual state', async () => {
    mediaQuery.matches = true;
    connect();
    mocks.agent.state = 'speaking';
    mocks.agent.microphoneTrack = { publication: { track: 'test-track' } };
    await mount();
    expect(mocks.bands).toHaveBeenLastCalledWith(undefined, expect.any(Object));
    expect(host.textContent).toContain('Audio visualization reduced');
    expect(host.querySelector('.session-status')?.textContent).toContain('Speaking');
  });

  it('uses the actual speaking track for visualization, not a decorative timer', async () => {
    connect();
    mocks.agent.state = 'speaking';
    mocks.agent.microphoneTrack = { publication: { track: 'test-track' } };
    await mount();
    expect(mocks.bands).toHaveBeenLastCalledWith(mocks.agent.microphoneTrack, expect.any(Object));
    expect(host.textContent).toContain('Live agent audio');
  });
});
