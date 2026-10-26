import { Chip } from '@mui/material'
import { InfoTooltip } from './info-tooltip'

export type StatusTone = 'success' | 'warning' | 'error' | 'neutral'

/** Visual status only. The application decides the meaning and scope. */
export function StatusBadge({ label, tone = 'neutral', description }: {
  label: string; tone?: StatusTone; description?: string;
}) {
  const color = tone === 'neutral' ? 'text.secondary' : `${tone}.main`
  const chip = <Chip size="small" variant="outlined" label={label}
    tabIndex={description ? 0 : undefined}
    sx={{ color, borderColor: tone === 'neutral' ? 'divider' : color }} />
  return description ? <InfoTooltip title={description} describeChild>{chip}</InfoTooltip> : chip
}
