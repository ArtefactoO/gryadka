/**
 * Shared chip styling for status-like values (outline only, no fill).
 * Used for Status (UP/DOWN), Lag (Mongo), and percentage columns across all monitor views.
 */
import type { SxProps, Theme } from '@mui/material/styles'

/** 0–70 neutral, 71–89 yellow, 90–100 red. Outline only, like Status. */
export function getPercentChipSx(percent: number | null | undefined): SxProps<Theme> {
  if (percent == null) return { borderColor: 'divider' }
  if (percent <= 70) return { borderColor: 'divider' }
  if (percent <= 89) return { color: 'warning.light', borderColor: 'warning.light' }
  return { color: 'error.light', borderColor: 'error.light' }
}

/** Disk usage: <85 neutral, 85–94 yellow, >=95 red. Outline only, like Status. */
export function getDiskPercentChipSx(percent: number | null | undefined): SxProps<Theme> {
  if (percent == null) return { borderColor: 'divider' }
  if (percent < 85) return { borderColor: 'divider' }
  if (percent < 95) return { color: 'warning.light', borderColor: 'warning.light' }
  return { color: 'error.light', borderColor: 'error.light' }
}

/** Mongo lag: <4 green, <8 yellow, >=8 red. Outline only, like Status. */
export function getLagChipSx(lagSeconds: number): SxProps<Theme> {
  if (lagSeconds < 4) return { color: 'success.light', borderColor: 'success.light' }
  if (lagSeconds < 8) return { color: 'warning.light', borderColor: 'warning.light' }
  return { color: 'error.light', borderColor: 'error.light' }
}

/** Same thresholds: stroke color (hex) for charts / non-MUI use. */
export function getLagStrokeColor(lagSeconds: number): string {
  if (lagSeconds < 4) return 'var(--sl-ok)'
  if (lagSeconds < 8) return 'var(--sl-warn)'
  return 'var(--sl-danger)'
}
