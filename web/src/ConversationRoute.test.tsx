import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ConversationRoute } from './ConversationRoute';
import { ConversationPanelState } from './components/conversation-panel-state';
import { readyStatus } from './test-fixtures';
const local = { ...readyStatus, livekit: { configured: true, local: true }, ai: { ...readyStatus.ai, local: true }, stt: { provider: 'nemotron' as const, model: 'test', local: true } };
const render = (status = local, online = true) => renderToStaticMarkup(<ConversationRoute status={status} online={online} locked={false} onSettings={() => {}} />);
describe('conversation route evidence', () => {
  it('distinguishes configured local services from an active session', () => {
    expect(render()).toContain('Local setup');
    expect(render()).toContain('services checked at start');
    expect(render()).not.toContain('Current session');
  });
  it('shows remote AI without claiming the entire route is local', () => {
    const html = render({ ...local, ai: { ...local.ai, local: false } });
    expect(html).toContain('Uses remote services');
    expect(html).not.toContain('Local setup');
  });
  it('does not present stale local information when the server is unavailable', () => {
    expect(render(local, false)).toContain('Route not confirmed');
    expect(render(local, false)).not.toContain('This computer');
  });
  it('does not hide existing messages during reconnect or preparation', () => {
    const html = renderToStaticMarkup(<ConversationPanelState messageCount={1} preparing message="Loading"><p>Existing reply</p></ConversationPanelState>);
    expect(html).toContain('Existing reply');
    expect(html).not.toContain('Loading');
  });
  it('shows the reported startup stage before the first message', () => {
    expect(renderToStaticMarkup(<ConversationPanelState messageCount={0} preparing message="Loading voice"><p>Empty</p></ConversationPanelState>)).toContain('Loading voice');
  });
});
