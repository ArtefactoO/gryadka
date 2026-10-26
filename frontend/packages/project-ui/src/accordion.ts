import type { SxProps, Theme } from '@mui/material/styles'

export const compactAccordionSummarySx: SxProps<Theme> = {
  minHeight: 36,
  '& .MuiAccordionSummary-content': { my: 0.35 },
  '& .MuiAccordionSummary-content.Mui-expanded': { my: 0.35 },
}
