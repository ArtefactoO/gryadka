import { useEffect, useState } from 'react'
import {
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
} from '@mui/material'

import type {
  CreateManualRegistryEntryRequest,
  RegistryManualRow,
  UpdateManualRegistryEntryRequest,
} from '../services/api'
import { getApiErrorMessage } from '../utils/apiError'

interface RegistryManualDialogProps {
  open: boolean
  mode: 'create' | 'edit'
  initialValue?: RegistryManualRow | null
  addressScope?: string
  title: string
  submitLabel: string
  submitting: boolean
  accentColor?: string
  accentSoftBg?: string
  onClose: () => void
  onSubmit: (payload: CreateManualRegistryEntryRequest | UpdateManualRegistryEntryRequest) => Promise<void> | void
}

interface ManualFormState {
  service_name: string
  ip_or_cidr: string
  dns_name: string
  protocol: string
  port: string
  comment: string
  owner: string
  tags: string
  is_active: boolean
}

const EMPTY_STATE: ManualFormState = {
  service_name: '',
  ip_or_cidr: '',
  dns_name: '',
  protocol: '',
  port: '',
  comment: '',
  owner: '',
  tags: '',
  is_active: true,
}

const buildState = (initialValue?: RegistryManualRow | null): ManualFormState => {
  if (!initialValue) return EMPTY_STATE
  return {
    service_name: initialValue.service_name ?? '',
    ip_or_cidr: initialValue.ip_or_cidr ?? '',
    dns_name: initialValue.dns_name ?? '',
    protocol: initialValue.protocol ?? '',
    port: initialValue.port === undefined || initialValue.port === null ? '' : String(initialValue.port),
    comment: initialValue.comment ?? '',
    owner: initialValue.owner ?? '',
    tags: (initialValue.tags || []).join(', '),
    is_active: Boolean(initialValue.is_active),
  }
}

const parseTags = (tags: string): string[] | undefined => {
  const normalized = tags
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
  return normalized.length > 0 ? normalized : undefined
}

const RegistryManualDialog = ({
  open,
  mode,
  initialValue,
  addressScope,
  title,
  submitLabel,
  submitting,
  accentColor,
  accentSoftBg,
  onClose,
  onSubmit,
}: RegistryManualDialogProps) => {
  const [state, setState] = useState<ManualFormState>(buildState(initialValue))
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setState(buildState(initialValue))
    setError(null)
  }, [open, initialValue])

  const updateField = (field: keyof ManualFormState, value: string | boolean) => {
    setState((prev) => ({
      ...prev,
      [field]: value,
    }))
  }

  const buildPayload = (): CreateManualRegistryEntryRequest | UpdateManualRegistryEntryRequest => {
    const parsedPort = state.port.trim() ? Number(state.port) : null
    const port = Number.isFinite(parsedPort) ? parsedPort : null
    const dnsName = state.dns_name.trim()
    const payload = {
      label: state.service_name.trim(),
      service_name: state.service_name.trim(),
      ip_or_cidr: state.ip_or_cidr.trim(),
      address_scope: addressScope || null,
      dns_name: dnsName && dnsName !== '-' ? dnsName : null,
      protocol: state.protocol.trim() || null,
      port,
      comment: state.comment.trim(),
      owner: state.owner.trim() || null,
      tags: parseTags(state.tags),
      is_active: state.is_active,
    }
    if (mode === 'create') {
      return payload
    }
    return payload
  }

  const handleSubmit = async () => {
    const requiredFields = [state.service_name.trim(), state.ip_or_cidr.trim()]
    if (requiredFields.some((value) => value.length === 0)) {
      setError('Заполните обязательные поля: название сервиса, IP или CIDR')
      return
    }

    try {
      await onSubmit(buildPayload())
    } catch (submitError) {
      const message = getApiErrorMessage(submitError, 'Не удалось сохранить ручную запись')
      setError(message)
    }
  }

  const fieldSx = {
    '& .MuiInputLabel-root.Mui-focused': { color: accentColor },
    '& .MuiOutlinedInput-root.Mui-focused .MuiOutlinedInput-notchedOutline': { borderColor: accentColor },
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle sx={{ borderBottom: '1px solid', borderColor: accentColor || 'divider' }}>{title}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          {error && (
            <Box sx={{ color: 'error.main', fontSize: '0.875rem' }}>
              {error}
            </Box>
          )}
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))' },
              gap: 1.5,
            }}
          >
            <TextField
              label="Название сервиса"
              value={state.service_name}
              onChange={(event) => updateField('service_name', event.target.value)}
              fullWidth
              required
              sx={fieldSx}
            />
            <TextField
              label="IP или CIDR"
              value={state.ip_or_cidr}
              onChange={(event) => updateField('ip_or_cidr', event.target.value)}
              fullWidth
              required
              sx={fieldSx}
            />
            <TextField
              label="DNS-имя"
              value={state.dns_name}
              onChange={(event) => updateField('dns_name', event.target.value)}
              fullWidth
              helperText="Можно оставить пустым, если DNS еще не создан"
              sx={fieldSx}
            />
            <TextField
              label="Протокол"
              value={state.protocol}
              onChange={(event) => updateField('protocol', event.target.value)}
              fullWidth
              sx={fieldSx}
            />
            <TextField
              label="Порт"
              type="number"
              value={state.port}
              onChange={(event) => updateField('port', event.target.value)}
              fullWidth
              sx={fieldSx}
            />
            <TextField
              label="Владелец"
              value={state.owner}
              onChange={(event) => updateField('owner', event.target.value)}
              fullWidth
              sx={fieldSx}
            />
            <TextField
              label="Теги"
              value={state.tags}
              onChange={(event) => updateField('tags', event.target.value)}
              helperText="Теги через запятую"
              fullWidth
              sx={fieldSx}
            />
          </Box>
          <TextField
            label="Комментарий"
            value={state.comment}
            onChange={(event) => updateField('comment', event.target.value)}
            fullWidth
            multiline
            minRows={3}
            sx={fieldSx}
          />
          <FormControlLabel
            control={
              <Switch
                checked={state.is_active}
                onChange={(event) => updateField('is_active', event.target.checked)}
                sx={{
                  '& .MuiSwitch-switchBase.Mui-checked': { color: accentColor },
                  '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': { backgroundColor: accentColor },
                }}
              />
            }
            label="Активна"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={submitting} sx={{ color: accentColor }}>
          Отмена
        </Button>
        <Button
          onClick={handleSubmit}
          variant="contained"
          disabled={submitting}
          sx={{
            backgroundColor: accentColor,
            '&:hover': { backgroundColor: accentColor },
            outline: accentSoftBg ? `2px solid ${accentSoftBg}` : undefined,
          }}
        >
          {submitLabel}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default RegistryManualDialog
