import { Box, Paper, Typography } from '@mui/material'
import { ThemeControls, UiIcon } from '@project/ui/mui'
import { Sprout } from '@project/ui/icons'
import { useNavigate } from 'react-router-dom'
import LoginForm from '../components/LoginForm'

export default function LoginPage() {
  const navigate = useNavigate()
  return <Box sx={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column' }}>
    <Box component="header" sx={{ p: 2, display: 'flex', justifyContent: 'flex-end' }}><ThemeControls /></Box>
    <Box component="main" sx={{ flex: 1, display: 'grid', placeItems: 'center', p: 2 }}>
      <Paper sx={{ width: '100%', maxWidth: 420, border: 1, borderColor: 'divider', p: {xs: 3, md: 4} }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1 }}><UiIcon glyph={Sprout} size={28} /><Typography variant="h4" component="h1">Gryadka</Typography></Box>
        <Typography color="text.secondary" sx={{ mb: 3 }}>Мониторинг сервисов</Typography>
        <LoginForm onSuccess={() => navigate('/')} />
      </Paper>
    </Box>
  </Box>
}
