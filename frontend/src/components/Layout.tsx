import { ReactNode } from 'react'
import { Box, Typography } from '@mui/material'
import { ThemeControls, UiIcon } from '@project/ui/mui'
import { Sprout } from '@project/ui/icons'
import RegistryMismatchBanner from './RegistryMismatchBanner'
import UnreachableBanner from './UnreachableBanner'

interface LayoutProps {
  children: ReactNode
}

const Layout = ({ children }: LayoutProps) => {
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <Box component="header" sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 2, flexWrap: 'wrap', px: {xs: 2, md: 3}, py: 1.5, borderBottom: 1, borderColor: 'divider', backgroundColor: 'background.paper' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}><UiIcon glyph={Sprout} size={20} /><Typography fontWeight={600}>Gryadka</Typography></Box>
        <ThemeControls />
      </Box>
      <UnreachableBanner />
      <RegistryMismatchBanner />
      <Box component="main" sx={{ flexGrow: 1, p: {xs: 2, md: 3}, minWidth: 0 }}>
        {children}
      </Box>
    </Box>
  )
}

export default Layout
