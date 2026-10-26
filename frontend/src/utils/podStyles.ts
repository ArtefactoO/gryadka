import type { SystemStyleObject } from '@mui/system'
import type { Theme } from '@mui/material'

export const NEW_POD_MS = 45_000

export const isNewPod = (startTime?: string): boolean => {
  if (!startTime) return false
  const t = new Date(startTime).getTime()
  return Number.isFinite(t) && Date.now() - t < NEW_POD_MS
}

export const getPodBoxSx = (
  isNew: boolean,
  isTerminating: boolean,
  accentColor: string,
  accentSoftBg: string
): SystemStyleObject<Theme> | undefined => {
  if (isNew) {
    return {
      px: 0.75,
      py: 0.25,
      borderRadius: 1,
      border: `1px solid ${accentColor}`,
      bgcolor: accentSoftBg,

    }
  }
  if (isTerminating) {
    return {
      textDecoration: 'line-through',
      color: 'text.secondary',
      opacity: 0.7,
    }
  }
  return undefined
}
