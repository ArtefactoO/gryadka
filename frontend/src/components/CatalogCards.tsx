import type { ReactNode } from 'react'
import { Accordion, AccordionDetails, AccordionSummary, Box, Chip, Paper, Typography } from '@mui/material'
import { compactAccordionSummarySx, UiIcon, useUiMotion } from '@project/ui/mui'
import { ChevronDown, ChevronUp } from '@project/ui/icons'

/** Shared presentation for MongoDB collections and PostgreSQL tables. */
export function CatalogCard({ title, metrics, children }: { title: string; metrics: ReactNode; children: ReactNode }) {
  return <Paper elevation={0} variant="outlined" sx={{ p: 0.85, borderRadius: 1.5, width: 'fit-content', maxWidth: '100%', transition: 'none' }}>
    <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.75, overflowWrap: 'anywhere' }}>{title}</Typography>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap', mb: 0.5 }}>{metrics}</Box>
    {children}
  </Paper>
}

export function CatalogIndexCard({ children }: { children: ReactNode }) {
  return <Paper elevation={0} variant="outlined" sx={{ p: 0.75, borderRadius: 1.25, width: 'fit-content', maxWidth: '100%' }}>
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap', overflowWrap: 'anywhere' }}>{children}</Box>
  </Paper>
}

export function CatalogIndexes({ expanded, onChange, count, children }: {
  expanded: boolean; onChange: (expanded: boolean) => void; count: string | number; children: ReactNode;
}) {
  const motion = useUiMotion()
  return <Accordion disableGutters elevation={0} expanded={expanded} onChange={(_, next) => onChange(next)}
    slotProps={{ transition: { ...motion, unmountOnExit: true } }} sx={{ width: 'fit-content', maxWidth: '100%', mt: 0.5, border: '1px solid', borderColor: 'divider', borderRadius: 1, '&:before': { display: 'none' } }}>
    <AccordionSummary sx={compactAccordionSummarySx}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <UiIcon glyph={expanded ? ChevronUp : ChevronDown} size={16} />
        <Typography variant="body2">Indexes</Typography><Chip label={count} size="small" variant="outlined" />
      </Box>
    </AccordionSummary>
    <AccordionDetails sx={{ pt: 0.25, pb: 0.75 }}>{children}</AccordionDetails>
  </Accordion>
}
