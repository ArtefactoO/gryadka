import { ReactNode } from 'react'
import { Chip } from '@mui/material'
import { SxProps, Theme } from '@mui/material/styles'

interface NeutralValueBadgeProps {
  children: ReactNode
  sx?: SxProps<Theme>
}

const NeutralValueBadge = ({ children, sx }: NeutralValueBadgeProps) => {
  return (
    <Chip
      size="small"
      variant="outlined"
      label={children}
      sx={{
        height: 24,
        borderRadius: '999px',
        borderColor: 'divider',
        backgroundColor: 'var(--sl-panel2)',
        color: 'text.primary',
        '& .MuiChip-label': {
          px: 1,
          fontSize: '0.75rem',
          lineHeight: 1.4,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        },
        ...sx,
      }}
    />
  )
}

export default NeutralValueBadge
