import { Box, Paper, Tab, Tabs } from '@mui/material'
import type { ReactNode } from 'react'
import { compactTabsSx } from '@project/ui/mui'

/** Same content hierarchy for database and broker monitors. */
export default function MonitorSections({ labels, value, onChange, children, label }: {
  labels: string[]; value: number; onChange: (value: number) => void;
  children: ReactNode; label: string;
}) {
  return <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', pt: 0.5 }}>
    <Tabs value={value} onChange={(_, next: number) => onChange(next)} aria-label={label}
      variant="scrollable" allowScrollButtonsMobile sx={{ ...compactTabsSx, mb: 0.25, pl: 1 }}>
      {labels.map((name, index) => <Tab key={name} label={name}
        id={`${label}-tab-${index}`} aria-controls={`${label}-panel`} />)}
    </Tabs>
    <Box role="tabpanel" id={`${label}-panel`} aria-labelledby={`${label}-tab-${value}`} sx={{ p: 1.8, minWidth: 0 }}>
      {children}
    </Box>
  </Paper>
}
