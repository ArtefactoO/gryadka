import type { SxProps, Theme } from '@mui/material/styles'

export const DATA_TABLE_PAPER_SX: SxProps<Theme> = {
  border: '1px solid',
  borderColor: 'divider',
  boxShadow: 'none',
  p: 1.5,
}

export const DATA_TABLE_TABLE_CONTAINER_SX: SxProps<Theme> = {
  maxWidth: '100%',
  overflowX: 'auto',
}

export const DATA_TABLE_TABLE_SX: SxProps<Theme> = {
  minWidth: 800,
  tableLayout: 'auto',
}

export const DATA_TABLE_ROW_DIVIDER_COLOR = 'var(--sl-line)'

export const DATA_TABLE_ROW_SX: SxProps<Theme> = {
  boxShadow: `inset 0 -1px 0 0 ${DATA_TABLE_ROW_DIVIDER_COLOR}`,
}

export const IDENTITY_HEADER_CELL_SX: SxProps<Theme> = {
  minWidth: '28ch',
  width: '1%',
  maxWidth: 'min(65ch, 45%)',
  whiteSpace: 'nowrap',
}

export const IDENTITY_CELL_SX: SxProps<Theme> = {
  ...IDENTITY_HEADER_CELL_SX,
  fontSize: '0.85rem',
  fontFamily: 'var(--sl-font-mono)',
  fontWeight: 600,
}

export const IDENTITY_SCROLL_WRAPPER_SX: SxProps<Theme> = (theme: Theme) => ({
  overflowX: 'auto',
  overflowY: 'hidden',
  maxWidth: '100%',
  scrollbarWidth: 'thin',
  scrollbarColor: 'transparent transparent',
  '&:hover': {
    scrollbarColor: `${theme.palette.divider} transparent`,
  },
  '&::-webkit-scrollbar': {
    height: 6,
  },
  '&::-webkit-scrollbar-track': {
    background: 'transparent',
  },
  '&::-webkit-scrollbar-thumb': {
    background: 'transparent',
    borderRadius: 3,
  },
  '&:hover::-webkit-scrollbar-thumb': {
    background: theme.palette.divider,
  },
})

export const IDENTITY_CONTENT_SX: SxProps<Theme> = {
  fontSize: 'inherit',
  fontFamily: 'inherit',
  fontWeight: 'inherit',
}
