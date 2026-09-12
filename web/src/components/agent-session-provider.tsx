// Adapted from livekit-examples/agent-starter-react. See THIRD_PARTY_LICENSES and PROVENANCE.md.
import {
  RoomAudioRenderer,
  type RoomAudioRendererProps,
  SessionProvider,
  type UseSessionReturn,
} from '@livekit/components-react';
import type { ReactNode } from 'react';

export type AgentSessionProviderProps = RoomAudioRendererProps & {
  session: UseSessionReturn;
  children: ReactNode;
};

export function AgentSessionProvider({
  session,
  children,
  ...roomAudioRendererProps
}: AgentSessionProviderProps) {
  return (
    <SessionProvider session={session}>
      {children}
      <RoomAudioRenderer {...roomAudioRendererProps} />
    </SessionProvider>
  );
}
