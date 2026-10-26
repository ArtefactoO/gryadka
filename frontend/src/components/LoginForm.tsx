import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Box, Button, CircularProgress, TextField, Typography } from '@mui/material'
import { useAuth } from '../hooks/useAuth'
import { authApi } from '../services/api'

export default function LoginForm({ onSuccess, onBusyChange }: { onSuccess: () => void; onBusyChange?: (busy: boolean) => void }) {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const { data: config, isError, refetch } = useQuery({ queryKey: ['auth-config'], queryFn: authApi.getConfig, retry: false })
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setError(''); setPending(true); onBusyChange?.(true)
    try { await login({username,password}); setPassword(''); onSuccess() }
    catch (cause: any) { setError(cause.code === 'ECONNABORTED' ? 'Время ожидания входа истекло. Попробуйте еще раз.' : cause.response?.data?.detail || 'Не удалось войти. Проверьте логин и пароль.') }
    finally { setPending(false); onBusyChange?.(false) }
  }
  return <Box sx={{ display: 'grid', gap: 2 }}>
    {error && <Alert severity="error" role="alert">{error}</Alert>}
    {isError && <Alert severity="warning" action={<Button onClick={() => refetch()}>Повторить</Button>}>Настройки входа недоступны.</Alert>}
    {config?.sso_enabled && <Button variant="contained" fullWidth href="/api/auth/oidc/login">Войти через Authentik</Button>}
    {config?.local_enabled && <Box component="form" onSubmit={submit} sx={{ display: 'grid', gap: 2 }}>
      {config.sso_enabled && <Typography variant="caption" color="text.secondary">Локальный резервный вход</Typography>}
      <TextField label="Логин" name="username" autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required fullWidth disabled={pending} />
      <TextField label="Пароль" name="password" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required fullWidth disabled={pending} />
      <Button type="submit" variant={config.sso_enabled ? 'outlined' : 'contained'} disabled={pending} fullWidth startIcon={pending ? <CircularProgress size={16} color="inherit" /> : undefined}>{pending ? 'Вход…' : 'Войти'}</Button>
    </Box>}
    {!config && !isError && <CircularProgress size={20} aria-label="Загрузка способов входа" />}
  </Box>
}
