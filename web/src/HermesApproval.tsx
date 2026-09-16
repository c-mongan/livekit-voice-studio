import { useEffect, useRef, useState } from 'react';
import type { HermesApprovalChoice, HermesApprovalRequest } from './api';

const LABELS: Record<HermesApprovalChoice, string> = {
  once: 'Allow once',
  session: 'Allow for session',
  always: 'Always allow',
  deny: 'Deny',
};

export function HermesApproval({
  request,
  onRespond,
  onAccepted,
}: {
  request: HermesApprovalRequest;
  onRespond: (request: HermesApprovalRequest, choice: HermesApprovalChoice) => Promise<void>;
  onAccepted: (request: HermesApprovalRequest) => void;
}) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<{ kind: 'status' | 'alert'; message: string } | null>(null);
  const deny = useRef<HTMLButtonElement>(null);
  const current = useRef(`${request.runId}\0${request.requestId}`);

  useEffect(() => {
    current.current = `${request.runId}\0${request.requestId}`;
    setPending(false);
    setResult(null);
    deny.current?.focus();
  }, [request.runId, request.requestId]);

  async function respond(choice: HermesApprovalChoice) {
    if (pending) return;
    const requestKey = `${request.runId}\0${request.requestId}`;
    setPending(true);
    setResult(null);
    try {
      await onRespond(request, choice);
      if (current.current !== requestKey) return;
      setResult({ kind: 'status', message: 'Response accepted.' });
      onAccepted(request);
    } catch {
      if (current.current !== requestKey) return;
      setPending(false);
      setResult({ kind: 'alert', message: 'The approval response could not be confirmed. Try again.' });
    }
  }

  return <section className="hermes-approval" aria-labelledby="hermes-approval-title">
    <h2 id="hermes-approval-title">Hermes needs approval</h2>
    <p className="hermes-approval-command">{request.command}</p>
    <div className="hermes-approval-actions">
      {request.choices.map((choice) => <button
        className={`button ${choice === 'deny' ? 'secondary' : 'primary'}`}
        disabled={pending}
        key={choice}
        onClick={() => void respond(choice)}
        ref={choice === 'deny' ? deny : undefined}
        type="button"
      >{LABELS[choice]}</button>)}
    </div>
    {result && <p role={result.kind}>{result.message}</p>}
  </section>;
}
