import { StatusBadge } from './components/status-badge';
import type { StudioStatus } from './api';

export function ConversationRoute({ status, online, onSettings, locked }: {
  status: StudioStatus | null; online: boolean; onSettings: () => void; locked: boolean;
}) {
  const current = online ? status : null;
  const location = (local: boolean | undefined) => local === true ? 'This computer' : local === false ? 'Remote service' : 'Not reported';
  const local = current?.livekit.local === true && current?.ai.local === true && current?.stt?.local === true;
  const remote = current && [current.livekit.local, current.ai.local, current.stt?.local].includes(false);
  return <section className="conversation-route" aria-label="Conversation route">
    <div className="route-heading"><div><StatusBadge status={local ? 'local' : remote ? 'remote' : 'unknown'} /><p>{current?.phase === 'active' ? 'Current session' : 'Selected for your next conversation · services checked at start'}</p></div><button className="button quiet" disabled={locked} onClick={onSettings}>Change setup</button></div>
    <dl className="route-components">
      <div><dt>Connection</dt><dd>{location(current?.livekit.local)}</dd></div>
      <div><dt>AI model</dt><dd>{location(current?.ai.local)}</dd></div>
      <div><dt>Speech recognition</dt><dd>{location(current?.stt?.local)}</dd></div>
      <div><dt>Voice</dt><dd>{current ? 'This computer' : 'Not reported'}</dd></div>
    </dl>
  </section>;
}
