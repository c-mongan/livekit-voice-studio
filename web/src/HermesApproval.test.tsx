// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parseHermesApprovalRequest, type HermesApprovalRequest } from './api';
import { HermesApproval } from './HermesApproval';
import { deferred } from './test-fixtures';

const request: HermesApprovalRequest = {
  runId: 'run-1',
  requestId: 'request-1',
  command: 'rm redacted-file',
  choices: ['once', 'deny'],
};
let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function render(node: React.ReactNode) {
  await act(async () => root.render(node));
}

function button(label: string) {
  const found = [...host.querySelectorAll('button')].find((item) => item.textContent === label);
  if (!found) throw new Error(`Missing button ${label}`);
  return found as HTMLButtonElement;
}

describe('approval payload validation', () => {
  it('accepts only exact bounded request fields and advertised choices', () => {
    expect(parseHermesApprovalRequest(new TextEncoder().encode(JSON.stringify(request)))).toEqual(request);
    expect(parseHermesApprovalRequest(new TextEncoder().encode(JSON.stringify({ ...request, secret: 'no' })))).toBeNull();
    expect(parseHermesApprovalRequest(new TextEncoder().encode(JSON.stringify({ ...request, command: 'x'.repeat(501) })))).toBeNull();
    expect(parseHermesApprovalRequest(new TextEncoder().encode(JSON.stringify({ ...request, choices: ['once', 'free text'] })))).toBeNull();
    expect(parseHermesApprovalRequest(new Uint8Array(4097))).toBeNull();
  });
});

describe('HermesApproval', () => {
  it('renders only advertised choices and focuses Deny by default', async () => {
    await render(<HermesApproval request={request} onRespond={vi.fn()} onAccepted={vi.fn()} />);
    expect(host.textContent).toContain('rm redacted-file');
    expect(button('Allow once')).toBeDefined();
    expect(button('Deny')).toBe(document.activeElement);
    expect(host.textContent).not.toContain('Allow for session');
    expect(host.textContent).not.toContain('Always allow');
  });

  it('disables every choice until acknowledgement and announces success', async () => {
    const ack = deferred<void>();
    const respond = vi.fn(() => ack.promise);
    const accepted = vi.fn();
    await render(<HermesApproval request={request} onRespond={respond} onAccepted={accepted} />);

    await act(async () => button('Allow once').click());
    expect([...host.querySelectorAll('button')].every((item) => item.disabled)).toBe(true);
    expect(respond).toHaveBeenCalledExactlyOnceWith(request, 'once');
    await act(async () => ack.resolve());
    expect(host.querySelector('[role=status]')?.textContent).toContain('Response accepted');
    expect(accepted).toHaveBeenCalledExactlyOnceWith(request);
  });

  it('announces a safe failure and permits a retry', async () => {
    const respond = vi.fn().mockRejectedValue(new Error('private RPC details'));
    await render(<HermesApproval request={request} onRespond={respond} onAccepted={vi.fn()} />);

    await act(async () => button('Deny').click());
    expect(host.querySelector('[role=alert]')?.textContent).toContain('could not be confirmed');
    expect(host.textContent).not.toContain('private RPC details');
    expect([...host.querySelectorAll('button')].every((item) => !item.disabled)).toBe(true);
  });
});
