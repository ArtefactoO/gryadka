import { Alert, Box } from '@mui/material'
import type { ReactNode } from 'react'

/** Shared separation and empty state; node columns remain engine-specific. */
export default function MonitorNodesSection({ children, empty = false, emptyMessage }: {
  children: ReactNode; empty?: boolean; emptyMessage?: string;
}) {
  return <Box sx={{ mt: 3 }}>
    {children}
    {empty && <Alert severity="info" sx={{ mt: 1 }}>{emptyMessage || 'Сведения об узлах не получены.'}</Alert>}
  </Box>
}
