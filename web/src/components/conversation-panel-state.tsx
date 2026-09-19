// Adapted with the owner's permission from ExtractionPanelState in
// mongo-ai/Intelligent-Document-Processor. Existing content always remains visible.
import type { ReactNode } from 'react';
export function ConversationPanelState({ messageCount, preparing, message, children }: {
  messageCount: number; preparing: boolean; message?: string | null; children: ReactNode;
}) {
  if (messageCount > 0) return <>{children}</>;
  if (preparing) return <div className="empty-conversation"><h3>Preparing your conversation</h3><p>{message || 'Connecting services and loading your voice. The first start can take longer.'}</p><p>Your message will send when the agent is ready.</p></div>;
  return <>{children}</>;
}
