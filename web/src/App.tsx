import { useCallback, useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { StartAudio, useLocalParticipant, useMultibandTrackVolume, useSessionMessages } from '@livekit/components-react';
import { AgentSessionProvider } from './components/agent-session-provider';
import { measuredSeconds, safeMessage, type StudioStatus } from './api';
import { MAX_MESSAGE_LENGTH, mergeTranscript, validMessage, workspaceState, type TranscriptMessage, type WorkspaceState } from './state';
import { useStudio } from './useStudio';
import { useStudioAgent } from './useStudioAgent';
import { StudioSettings } from './StudioSettings';
import { SetupGuide } from './SetupGuide';
import { ConversationPanelState } from './components/conversation-panel-state';
import { StudioCommands } from './components/studio-commands';
import { ConversationRoute } from './ConversationRoute';
import { MicrophoneCheck } from './MicrophoneCheck';

type Studio = ReturnType<typeof useStudio>;
const stateCopy: Record<WorkspaceState, string> = {
  offline: 'Server offline', ready: 'Ready to start', connecting: 'Preparing conversation', listening: 'Listening',
  thinking: 'Thinking', speaking: 'Speaking', reconnecting: 'Reconnecting', draining: 'Finishing session', blocked: 'Needs attention',
};

function Icon({ name, className = '' }: { name: 'mic' | 'send' | 'stop' | 'close' | 'audio' | 'arrow'; className?: string }) {
  const paths = {
    mic: <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" /></>,
    send: <><path d="m5 12 7-7 7 7M12 5v15" /></>,
    stop: <rect x="6" y="6" width="12" height="12" rx="1" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    audio: <><path d="M4 10h4l5-4v12l-5-4H4zM17 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14" /></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
  };
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function Pipeline({ status: lastStatus, online, connection, agentState, roomName, micEnabled, hasAgentAudio }: { status: StudioStatus | null; online: boolean; connection: string; agentState: string; roomName?: string; micEnabled: boolean; hasAgentAudio: boolean }) {
  const status = online ? lastStatus : null;
  const provider = status ? { azure: 'Azure', openai: 'OpenAI', copilot: 'Copilot', codex: 'Codex', ollama: 'Ollama', 'openai-compatible': 'Custom endpoint' }[status.ai.provider] : 'Not reported';
  const speechProvider = status?.stt ? { nemotron: 'Nemotron', azure: 'Azure Speech', openai: 'OpenAI' }[status.stt.provider] : 'Not reported';
  const mlx = status?.voice.backend === 'mlx';
  const streaming = mlx && status?.voice.streaming === true;
  return <aside className="inspector" aria-label="Pipeline inspector">
    <div className="inspector-title"><h2>Your pipeline</h2><span className="local-label">Local agent</span></div>
    <p className="inspector-intro">Three stages. One conversation.</p>
    <details className="learn-lesson"><summary>Rooms, tracks and controls</summary>
      <p>A room connects participants: this browser and your local agent. Each microphone or speaker stream is an audio track.</p>
      <dl className="detail-list">
        <div><dt>Browser connection</dt><dd>{connection}</dd></div>
        <div><dt>Agent state</dt><dd>{agentState}</dd></div>
        <div><dt>Your microphone</dt><dd>{micEnabled ? 'On' : 'Off'}</dd></div>
        <div><dt>Agent audio track</dt><dd>{hasAgentAudio ? 'Available' : 'Not available'}</dd></div>
        <div><dt>Your room</dt><dd>{roomName || 'Start a session to join'}</dd></div>
      </dl>
      <p>Try typing with your microphone off. Text uses LiveKit’s chat channel; it skips speech recognition.</p>
      <p>During a reply, press Stop reply. The browser sends an owner-checked RPC: a request asking the agent to interrupt. Muting playback and stopping model computation are separate operations.</p>
      <p>Studio starts one local agent process for your room. A managed agent server instead receives dispatched jobs.</p>
      <a href="https://docs.livekit.io/transport/data/rpc/" target="_blank" rel="noreferrer">Explore LiveKit RPC ↗</a>
    </details>
    <ol className="pipeline">
      <li><span className="stage-number">1</span><div><h3>Listen</h3><p>{speechProvider}</p><span>Speech → text · {status?.stt ? status.stt.local ? 'Local' : 'Cloud' : 'Location not reported'}</span></div></li>
      <li><span className="stage-number">2</span><div><h3>Reason</h3><p>{provider} · {status?.ai.model || 'Model not reported'}</p><span>Text → response · {status?.ai.local === true ? 'Local model' : status?.ai.local === false ? 'Remote model' : 'Location not reported'}{status?.ai.effort && status.ai.effort !== 'none' ? ` · ${status.ai.effort} effort` : ''}</span></div></li>
      <li><span className="stage-number">3</span><div><h3>Speak</h3><p>{!status ? 'Not reported' : streaming ? 'Qwen · local streaming' : mlx ? 'Qwen · on this machine' : 'Voicebox · on this machine'}</p><span>Response → generated audio</span></div></li>
    </ol>
    <section className="inspector-section">
      <div className="section-title"><h3>Voice & model</h3><span className={`small-status ${online && status?.voice.loaded ? 'good' : ''}`}>{!online ? 'Unknown' : status?.voice.loaded ? 'Loaded' : mlx ? 'Prepares at start' : 'Not loaded'}</span></div>
      <p className="voice-name">{status?.voice.name || 'Waiting for server'}</p>
      <dl className="detail-list">
        <div><dt>Engine</dt><dd>{status ? `${status.voice.engine.toUpperCase()} · ${status.voice.model}` : 'Not reported'}</dd></div>
        <div><dt>Voice cache</dt><dd>{!online ? 'Unknown' : status?.voice.cached ? 'Available' : 'Not cached'}</dd></div>
        <div><dt>Voice source</dt><dd>{!status ? 'Unknown' : status.voice.source === 'local-bundle' ? 'Private local bundle' : 'Voicebox profile'}</dd></div>
        <div><dt>LiveKit</dt><dd>{!status ? 'Unknown' : !status.livekit.configured ? 'Not configured' : status.livekit.local === true ? 'This computer' : status.livekit.local === false ? 'Remote server' : 'Location not reported'}</dd></div>
      </dl>
    </section>
    <details className="inspector-section latency-details">
      <summary>Measured latency</summary>
      <p className="metric-note">Latest server-reported generation</p>
      <dl className="detail-list metrics">
        <div><dt>LLM first token</dt><dd>{measuredSeconds(status?.metrics.llmFirstTokenSeconds)}</dd></div>
        <div><dt>TTS first frame</dt><dd>{measuredSeconds(status?.metrics.ttsFirstFrameSeconds)}</dd></div>
        <div><dt>Generated audio</dt><dd>{measuredSeconds(status?.metrics.ttsAudioSeconds)}</dd></div>
      </dl>
      <p className="metric-note">Not an end-to-end response-time estimate.</p>
      <dl className="detail-list recognition-metrics">
        <div><dt>End-of-turn delay</dt><dd>{measuredSeconds(status?.metrics.endOfUtteranceSeconds)}</dd></div>
        <div><dt>Transcription delay</dt><dd>{measuredSeconds(status?.metrics.transcriptionDelaySeconds)}</dd></div>
      </dl>
      <p className="metric-note">Stages can overlap and these latest values may belong to different replies. Browser playback is not measured here. Do not add these numbers together.</p>
      <p className="metric-note">Try a short message, then a longer one. Compare reasoning and voice generation to find which stage takes longer. No transcript or audio is saved by this inspector.</p>
      {!!status?.turns?.length && <ol className="reply-timings">{status.turns.map((turn, index) => <li key={turn.id}>
        <strong>Reply {index + 1}</strong>
        <dl className="detail-list">
          <div><dt>End-of-turn delay</dt><dd>{measuredSeconds(turn.endOfUtteranceSeconds)}</dd></div>
          <div><dt>Recognition delay</dt><dd>{measuredSeconds(turn.transcriptionDelaySeconds)}</dd></div>
          <div><dt>First answer text</dt><dd>{measuredSeconds(turn.llmFirstTokenSeconds)}</dd></div>
          <div><dt>First generated audio</dt><dd>{measuredSeconds(turn.ttsFirstFrameSeconds)}</dd></div>
        </dl>
      </li>)}</ol>}
      <p className="metric-note">Up to 20 correlated replies from the current session, in first-observed order. Each duration uses its own stage’s clock, not a shared stopwatch.</p>
    </details>
    <details className="about">
      <summary>How it works</summary>
      <div className="about-body">
        <p>LiveKit carries microphone audio, text, and replies between this browser and your local agent.</p>
        {status ? <>
          <p>{status.stt && <>{speechProvider} transcribes speech {status.stt.local ? 'on this machine' : 'in the cloud'}. </>}{provider} generates the response {status.ai.local === true ? 'on this computer' : status.ai.local === false ? 'using a remote model' : '(model location not reported)' }. {streaming ? 'Qwen streams PCM audio locally. Voice conditioning is cached for reuse after it is prepared.' : mlx ? 'Qwen generates audio locally. The worker has not reported PCM streaming.' : 'Voicebox synthesizes complete WAV audio locally before playback is forwarded through LiveKit.'}</p>
          <p>{streaming ? 'This streams generated audio, not text tokens. The agent still sends sentence-sized text for speech synthesis. The local model and selected voice normally prepare at session start, which can take several seconds.' : mlx ? 'The local model and selected voice normally prepare at session start. Readiness and timings here come from the local worker.' : 'This is not a realtime audio-generation model. A pause while a WAV is generated is expected.'}</p>
        </> : <p>Provider selections and model details will appear when the local server reports them.</p>}
        <p>Typed messages skip speech recognition. Text history stays in this page’s memory. Conversation audio is not recorded here; voice enrollment saves only explicitly approved references to your local server.</p>
        {status?.voice.source === 'local-bundle' && <p>Your selected reference is read from a private local bundle. Voicebox can stay closed. The reference is not uploaded to LiveKit or the LLM.</p>}
      </div>
    </details>
  </aside>;
}

function Workspace({ studio, setMuted }: { studio: Studio; setMuted: (muted: boolean) => void }) {
  const [learning, setLearning] = useState(false);
  const [micCheck, setMicCheck] = useState(false);
  const [settingsRequest, setSettingsRequest] = useState<'providers' | 'voices' | null>(null);
  const { session, status, grant, connected, starting, ending, online } = studio;
  const agent = useStudioAgent(grant?.agentIdentity, session.connectionState);
  const chat = useSessionMessages(session);
  const local = useLocalParticipant({ room: session.room });
  const micEnabled = connected && local.localParticipant.isMicrophoneEnabled;
  const [consent, setConsent] = useState(false);
  const [auditionBusy, setAuditionBusy] = useState(false);
  useEffect(() => { setConsent(false); }, [status?.stt?.provider, status?.stt?.local, status?.ai.provider, status?.ai.local, status?.ai.model, status?.livekit.local, status?.livekit.mode, online]);
  const [draft, setDraft] = useState('');
  const [pendingText, setPendingText] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [micBusy, setMicBusy] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const [awaitingSilence, setAwaitingSilence] = useState(false);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [announcement, setAnnouncement] = useState('');
  const cleared = useRef(new Set<string>());
  const sendingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const composer = useRef<HTMLTextAreaElement>(null);
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(query.matches);
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  const state = workspaceState({ status, online, starting, ending, owned: !!grant, connection: session.connectionState, agent: agent.state, mic: micEnabled });
  const sourceTrack = agent.state === 'speaking' ? agent.microphoneTrack : micEnabled ? session.local.microphoneTrack : undefined;
  const bands = useMultibandTrackVolume(reducedMotion ? undefined : sourceTrack, { bands: 36, loPass: 0, hiPass: 200, updateInterval: 60 });
  const canStart = online && status?.ready && status.phase === 'idle' && !grant && !starting && !ending && !auditionBusy;
  const agentReady = connected && agent.isConnected;
  const canSend = validMessage(draft) && !sending && !pendingText && !starting && !ending && (agentReady || (canStart && consent));
  const mlx = status?.voice.backend === 'mlx';
  const streaming = mlx && status?.voice.streaming === true;

  useEffect(() => {
    const incoming = chat.messages.map((message) => ({
      id: message.id,
      message: message.message,
      timestamp: message.timestamp,
      role: (message.type === 'userTranscript' || message.from?.identity === session.room.localParticipant.identity ? 'you' : 'agent') as 'you' | 'agent',
    }));
    setMessages((previous) => mergeTranscript(previous, incoming, cleared.current));
  }, [chat.messages, session.room]);

  useEffect(() => {
    const transcript = scrollRef.current;
    if (!transcript) return;
    if (!messages.length) {
      transcript.scrollTop = 0;
      follow.current = true;
    } else if (follow.current) {
      transcript.scrollTop = transcript.scrollHeight;
    }
  }, [messages]);

  const send = useCallback(async (text: string) => {
    if (sendingRef.current) return;
    sendingRef.current = true;
    setSending(true);
    studio.setError(null);
    try {
      await chat.send(text);
      setDraft((current) => current.trim() === text ? '' : current);
      setAnnouncement('Message sent.');
    } catch {
      studio.setError('Your message could not be sent. It is still in the composer; check the connection and try again.');
    } finally {
      sendingRef.current = false;
      setSending(false);
      setPendingText(null);
    }
  }, [chat.send, studio.setError]);

  useEffect(() => {
    if (pendingText && agentReady) void send(pendingText);
  }, [agentReady, pendingText, send]);

  useEffect(() => {
    if (!grant && !starting) setPendingText(null);
  }, [grant, starting]);

  useEffect(() => {
    if (!grant) {
      setAwaitingSilence(false);
      setMuted(false);
    } else if (awaitingSilence && !interrupting && agent.state !== 'speaking') {
      setAwaitingSilence(false);
      setMuted(false);
      setAnnouncement('Reply stopped.');
    } else {
      setMuted(interrupting || awaitingSilence);
    }
  }, [agent.state, awaitingSilence, grant, interrupting, setMuted]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSend) return;
    const text = draft.trim();
    if (agentReady) await send(text);
    else {
      setPendingText(text);
      if (!(await studio.start())) setPendingText(null);
    }
  }

  async function toggleMic() {
    if (micBusy || !connected) return;
    setMicBusy(true);
    studio.setError(null);
    try {
      await local.localParticipant.setMicrophoneEnabled(!micEnabled);
    } catch {
      studio.setError('Microphone access is unavailable. You can still type. To speak, allow microphone access in your browser settings, then try again.');
      composer.current?.focus();
    } finally {
      setMicBusy(false);
    }
  }

  async function interrupt() {
    if (!grant || interrupting || awaitingSilence) return;
    setInterrupting(true);
    setAwaitingSilence(true);
    setMuted(true);
    try {
      await local.localParticipant.performRpc({ destinationIdentity: grant.agentIdentity, method: 'voicebox.interrupt', payload: '{}', responseTimeout: 5_000 });
      setAnnouncement('Stop request acknowledged.');
    } catch {
      setAwaitingSilence(false);
      studio.setError('The agent could not confirm the stop request. Use End session to stop all audio.');
    } finally {
      setInterrupting(false);
    }
  }

  function clearTranscript() {
    for (const message of chat.messages) cleared.current.add(message.id);
    for (const message of messages) cleared.current.add(message.id);
    setMessages([]);
    setAnnouncement('Local transcript cleared. The agent may still retain this conversation until the session ends.');
  }

  let help = !online ? 'Start the local Studio server to connect.' :
    auditionBusy ? 'A local voice audition is running or finishing cleanup. Conversation starts are paused until it stops.' :
    state === 'draining' ? 'Audio capture has stopped. Waiting for the local worker to finish safely.' :
    state === 'reconnecting' ? 'LiveKit is restoring the connection. Sending is paused.' :
    state === 'connecting' ? pendingText ? 'Your message is queued until the agent is ready.' : mlx && !status?.voice.loaded ? 'Preparing the connection and selected voice. This can take several seconds.' : 'Connecting to your room and waiting for the agent.' :
    state === 'thinking' ? streaming
      ? status?.voice.loaded ? 'Preparing your reply. Qwen streams audio as it is generated locally.' : 'Preparing your reply. The local model and selected voice are being prepared.'
      : mlx ? 'Preparing your reply with the local Qwen model.' : 'Preparing your reply. Local speech generation can take tens of seconds; you can stop the reply at any time.' :
    state === 'speaking' ? streaming ? 'Playing streamed audio from the local Qwen model.' : mlx ? 'Playing generated audio from the local Qwen model.' : 'Voicebox is playing the generated reply.' :
    state === 'listening' ? 'Your microphone is on. Speak naturally.' :
    grant ? 'Microphone is off. Type a message or turn it on to speak.' :
    status?.phase === 'active' || status?.phase === 'starting' ? 'A session is already running. End it in its owning browser, or wait for it to expire.' :
    !status?.ready ? 'Check the pipeline and resolve the setup items below.' :
    'Start a session, then choose text or microphone.';
  if (agent.state === 'failed' && grant) help = 'The agent did not become ready. End this session and check the local worker before retrying.';
  if (interrupting || awaitingSilence) help = 'Stopping the reply. Speaker audio stays muted until the agent stops speaking.';

  return <div className="app-shell">
    <header className="app-header">
      <a className="brand" href="#conversation" aria-label="LiveKit Voice Studio, conversation"><span className="brand-mark"><Icon name="audio" /></span><span>LiveKit <span className="brand-secondary">Voice Studio</span></span></a>
      <span className="header-note"><span className={`connection-dot ${online ? 'online' : ''}`} />{online ? 'Local server connected' : 'Local server unavailable'}</span>
    </header>
    <main className={`studio-layout${learning ? '' : ' studio-simple'}`}>
      <section className="workspace" id="conversation" aria-labelledby="workspace-title">
        <div className="studio-controls">
        <div className="workspace-heading"><div><h1 id="workspace-title">Your voice. Your conversation.</h1><p>A place to think out loud — or start with a message.</p></div></div>
        <ConversationRoute status={status} online={online} locked={!canStart} onSettings={() => setSettingsRequest('providers')} />
        <div className={`session-strip state-${state}`}>
          <div className="session-status"><span className="state-dot" /><strong role="status">{auditionBusy ? 'Audition in progress' : stateCopy[state]}</strong></div>
          <span className="session-mode">{auditionBusy ? 'Local generation' : grant ? 'Browser ↔ local agent' : 'Voice + text'}</span>
        </div>
        <div className="audio-stage">
          <div className={`audio-meter ${sourceTrack ? 'has-track' : ''}`} aria-hidden="true">
            {bands.map((volume, index) => <span key={index} style={{ '--level': sourceTrack && !reducedMotion ? Math.max(.035, Math.min(1, volume)) : .035 } as CSSProperties} />)}
          </div>
          <p className="state-help">{help}</p>
          <p className="audio-source">{reducedMotion ? 'Audio visualization reduced' : sourceTrack ? agent.state === 'speaking' ? 'Live agent audio' : 'Live microphone input' : 'Audio activity appears here during a conversation'}</p>
          <div className="voice-controls">
            {!grant && <button className="button primary" disabled={!canStart || !consent} onClick={() => void studio.start()}>{starting ? 'Connecting…' : ending || state === 'draining' ? 'Finishing session…' : 'Start session'}<Icon name="arrow" /></button>}
            {grant && <>
              <button className={`button ${micEnabled ? 'primary' : 'secondary'}`} disabled={!connected || micBusy || ending} onClick={() => void toggleMic()} aria-pressed={micEnabled}><Icon name="mic" />{micBusy ? 'Updating mic…' : micEnabled ? 'Turn mic off' : 'Turn mic on'}</button>
              <button className="button secondary" disabled={!connected || !['speaking', 'thinking'].includes(agent.state) || interrupting || awaitingSilence || ending} onClick={() => void interrupt()}><Icon name="stop" />{interrupting || awaitingSilence ? 'Stopping…' : 'Stop reply'}</button>
              <button className="button quiet end-session" disabled={ending} onClick={() => { setPendingText(null); void studio.end(); }}><Icon name="close" />{ending ? 'Ending…' : 'End session'}</button>
            </>}
          </div>
          {connected && <StartAudio className="button audio-unlock" label="Enable speaker audio" />}
        </div>
        {!grant && <div className="privacy">
          <label><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>I understand where this conversation runs.</span></label>
          <details className="privacy-details"><summary>Where audio and text go</summary><p>{online && status?.livekit.local === true ? 'LiveKit on this computer carries audio and text.' : online && status?.livekit.local === false ? 'A remote LiveKit server carries audio and text.' : 'LiveKit: Location not reported.'} {online && status?.stt ? status.stt.local ? 'Speech recognition processes audio on this computer.' : 'Cloud speech recognition processes audio.' : 'Speech recognition: Location not reported.'} {online && status?.ai.local === true ? 'Reasoning processes text on this computer.' : online && status?.ai.local === false ? 'Remote reasoning processes conversation text.' : 'Reasoning: Location not reported.'}</p></details>
          <p>Speech is generated locally. No microphone access until you turn it on. This page keeps transcripts in memory, not browser storage.</p>
        </div>}
        <div className="notices" aria-live="polite">
          {!online && <div className="notice"><p><strong>Local server unavailable.</strong> Start the Studio server on this machine. This page will check again automatically.</p><button className="button quiet" onClick={() => void studio.refresh()}>Check again</button></div>}
          {online && status?.problems.length ? <div className="notice warning"><strong>Before you start</strong><ul>{status.problems.map((problem, index) => <li key={index}>{safeMessage(problem, 'Check local server configuration.')}</li>)}</ul></div> : null}
          {online && status?.message && <p className="server-message">{safeMessage(status.message, '')}</p>}
          {studio.error && <div className="notice error" role="alert"><p>{studio.error}</p><button className="button quiet" onClick={() => studio.setError(null)} aria-label="Dismiss error"><Icon name="close" /></button></div>}
          {studio.heartbeatError && <div className="notice warning"><p>{studio.heartbeatError}</p></div>}
        </div>
        <StudioCommands commands={[
          { id: 'chat', label: 'Write a message', description: 'Focus the conversation composer', action: () => composer.current?.focus() },
          { id: 'voices', label: 'Choose a voice', description: 'Manage your private voice library', disabled: !canStart, action: () => setSettingsRequest('voices') },
          { id: 'settings', label: 'Connection and AI settings', description: 'Choose local or cloud components', disabled: !canStart, action: () => setSettingsRequest('providers') },
          { id: 'learn', label: 'Inspect the pipeline', description: 'See LiveKit stages and measured timings', action: () => setLearning(true) },
        ]} />
        <div className="experience-controls">
          <button className="button quiet" aria-pressed={learning} onClick={() => setLearning(!learning)}>{learning ? 'Back to Studio' : 'How it works'}</button>
          <button className="button quiet" aria-expanded={micCheck} disabled={!!grant || starting || auditionBusy} onClick={() => setMicCheck(!micCheck)}>{micCheck ? 'Close microphone check' : 'Check microphone'}</button>
        </div>
        {micCheck && !grant && !starting && !auditionBusy && <MicrophoneCheck disabled={ending} />}
        <StudioSettings onOpen={() => setMicCheck(false)} requestedTab={settingsRequest} onRequestHandled={() => setSettingsRequest(null)} status={status} locked={!online || !!grant || starting || ending || (!auditionBusy && status?.phase !== 'idle')} onChanged={studio.refresh} onRoutingChanged={() => setConsent(false)} onAuditionBusy={setAuditionBusy} />
        {!grant && <SetupGuide disabled={!online || starting || ending || auditionBusy || status?.phase !== 'idle'} onVoices={() => { setMicCheck(false); setSettingsRequest('voices'); }} onSettings={() => { setMicCheck(false); setSettingsRequest('providers'); }} />}
        </div>
        <section className="conversation" aria-label="Conversation transcript">
          <div className="transcript-heading"><h2>Conversation</h2><button className="button quiet clear-button" disabled={!messages.length} onClick={clearTranscript}>Clear transcript</button></div>
          <div className="transcript" ref={scrollRef} role="log" aria-label="Conversation messages" aria-live="polite" aria-relevant="additions text" tabIndex={0} onScroll={(event) => {
            const element = event.currentTarget;
            follow.current = !messages.length || element.scrollHeight - element.scrollTop - element.clientHeight < 64;
          }}>
            <ConversationPanelState messageCount={messages.length} preparing={starting || status?.phase === 'starting'} message={safeMessage(status?.message, '')}>
            {messages.length === 0 ? <div className="empty-conversation"><span className="empty-symbol" aria-hidden="true">“</span><h3>Make room for a good conversation.</h3><p>Ask a question below, or start a session and turn on your microphone. Your words and the agent’s replies will appear here.</p><button className="text-link" onClick={() => { setDraft('Explain how this voice pipeline works.'); composer.current?.focus(); }}>Try “Explain how this voice pipeline works” <span aria-hidden="true">↗</span></button></div> :
              messages.map((message) => <article className={`message message-${message.role}`} key={message.id}><div className="message-meta"><strong>{message.role === 'you' ? 'You' : 'Agent'}</strong><time dateTime={new Date(message.timestamp).toISOString()}>{new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></div><p>{message.message}</p></article>)}
            </ConversationPanelState>
          </div>
          <form className="composer" onSubmit={(event) => void submit(event)}>
            <label className="sr-only" htmlFor="message">Message your agent</label>
            <textarea id="message" ref={composer} value={draft} maxLength={MAX_MESSAGE_LENGTH} placeholder="Message your agent…" rows={2} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); }
            }} aria-describedby="composer-hint" />
            <div className="composer-footer"><span id="composer-hint">{pendingText ? 'Queued for the agent' : !grant && !consent ? 'Review privacy to start' : 'Enter to send · Shift + Enter for a new line'}</span><span className="character-count" aria-label={`${draft.length} of ${MAX_MESSAGE_LENGTH} characters`}>{draft.length}/{MAX_MESSAGE_LENGTH}</span><button className="button primary send-button" disabled={!canSend} type="submit" aria-label={grant ? 'Send message' : 'Start session and send message'}><span>{sending ? 'Sending…' : pendingText ? 'Queued' : grant ? 'Send' : 'Start & send'}</span><Icon name="send" /></button></div>
          </form>
          <p className="transcript-note">Only in this tab · Clearing this view does not reset the agent’s memory.</p>
        </section>
      </section>
      {learning && <Pipeline status={status} online={online} connection={session.connectionState} agentState={agent.state} roomName={grant?.roomName} micEnabled={micEnabled} hasAgentAudio={!!agent.microphoneTrack} />}
    </main>
    <footer className="app-footer"><span>LiveKit Voice Studio</span><span>Built on LiveKit · Your configured AI · Local voice synthesis</span></footer>
    <span className="sr-only" role="status">{announcement}</span>
  </div>;
}

export default function App() {
  const studio = useStudio();
  const [muted, setMuted] = useState(false);
  return <AgentSessionProvider session={studio.session} muted={muted}><Workspace studio={studio} setMuted={setMuted} /></AgentSessionProvider>;
}
