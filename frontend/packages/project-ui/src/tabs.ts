import { SxProps, Theme } from '@mui/material/styles'

export const compactTabsSx: SxProps<Theme> = {
  minHeight: 36,
  borderBottom: 1,
  borderColor: 'divider',
  '& .MuiTabs-indicator': {
    display: 'none',
  },
  '& .MuiTabs-scroller': {
    flex: '0 1 auto',
    maxWidth: '100%',
  },
  '& .MuiTabs-flexContainer': {
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: 0,
    width: 'max-content',
  },
  // Same compact pill size as top-level nav (minHeight 28, small font)
  '& .MuiTab-root': {
    minHeight: 28,
    '@media (pointer: coarse)': { minHeight: 44, minWidth: 44 },
    py: 0.25,
    px: 1.25,
    mr: 0.75,
    fontSize: '0.8125rem',
    minWidth: 0,
    width: 'auto',
    flex: '0 0 auto',
    maxWidth: 'none',
    display: 'inline-flex',
    alignSelf: 'center',
    borderRadius: '6px',
    color: 'text.secondary',
    fontWeight: 500,
    '&:hover': {
      backgroundColor: 'action.hover',
    },
  },
  '& .MuiTab-root.Mui-selected': {
    color: 'primary.main',
    backgroundColor: 'action.selected',
    fontWeight: 600,
  },
}

/** Wrapping target/cluster list; use standard Tabs without scroll buttons. */
export const targetTabsSx: SxProps<Theme> = {
  minHeight: 36,
  width: 'fit-content',
  maxWidth: '100%',
  borderBottom: 1,
  borderColor: 'divider',
  '& .MuiTabs-indicator': { display: 'none' },
  '& .MuiTabs-scroller': { flex: '0 1 auto', minWidth: 0, maxWidth: '100%' },
  '& .MuiTabs-flexContainer': {
    flexWrap: 'wrap',
    alignItems: 'center',
    columnGap: 0,
    rowGap: 0.5,
    maxWidth: '100%',
  },
  '& .MuiTab-root': {
    minHeight: 28,
    '@media (pointer: coarse)': { minHeight: 44, minWidth: 44 },
    py: 0.25,
    px: 1.25,
    mr: 0.75,
    fontSize: '0.8125rem',
    minWidth: 0,
    width: 'auto',
    flex: '0 0 auto',
    maxWidth: '100%',
    overflowWrap: 'anywhere',
    display: 'inline-flex',
    alignSelf: 'center',
    borderRadius: '6px',
    color: 'text.secondary',
    fontWeight: 500,
  },
  '& .MuiTab-root.Mui-selected': {
    color: 'primary.main',
    backgroundColor: 'action.selected',
    fontWeight: 600,
  },
}
