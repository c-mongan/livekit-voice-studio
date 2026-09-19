// Adapted with the repository owner's permission from Intelligent-Document-Processor's
// StatusBadge: semantic state configuration, simplified to Studio's existing CSS tokens.
import type { ReactNode } from 'react';
const statusConfig = {
  local: { label: 'Local setup', symbol: '⌂' },
  remote: { label: 'Uses remote services', symbol: '↗' },
  unknown: { label: 'Route not confirmed', symbol: '?' },
};
export function StatusBadge({ status, children }: { status: keyof typeof statusConfig; children?: ReactNode }) {
  const config = statusConfig[status];
  return <span className={`route-badge route-badge-${status}`}><span aria-hidden="true">{config.symbol}</span>{children ?? config.label}</span>;
}
