import { UiIcon } from "@project/ui/mui"
import { Alert, Box, CircularProgress, IconButton, Tooltip } from '@mui/material'
import { RefreshCw as RefreshIcon } from "@project/ui/icons"
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { targetsApi } from '../services/api'
import { useAuth } from '../hooks/useAuth'
import { invalidateTargetQueries, normalizeTargetType, parseTargetId } from '../utils/queryInvalidation'
import { getApiErrorMessage } from '../utils/apiError'

const REFRESHABLE_TARGET_TYPES = new Set([
  'mongodb',
  'rabbitmq',
  'redis',
  'postgresql',
  'kafka',
  'haproxy',
  'redpanda',
  'elasticsearch',
  'emqx',
  'addresses',
  'argo-rollout',
])

interface TargetRefreshButtonProps {
  targetType: string
  targetName: string
  accentColor?: string
  accentSoftBg?: string
}

const TargetRefreshButton = ({
  targetType,
  targetName,
  accentColor,
  accentSoftBg,
}: TargetRefreshButtonProps) => {
  const { hasPermission } = useAuth()
  const queryClient = useQueryClient()
  const normalizedType = normalizeTargetType(targetType)
  const targetId = `${normalizedType}:${targetName}`

  const invalidateTarget = (id: string) => {
    const target = parseTargetId(id)
    if (target) invalidateTargetQueries(queryClient, target.targetType, target.targetName)
  }

  const refreshTargetMutation = useMutation({
    mutationFn: (id: string) => targetsApi.refreshTarget(id),
    onSuccess: (_, id) => {
      invalidateTarget(id)
      window.setTimeout(() => invalidateTarget(id), 2500)
      window.setTimeout(() => invalidateTarget(id), 8000)
    },
  })
  const { reset } = refreshTargetMutation
  useEffect(() => { reset() }, [targetId, reset])

  if (!hasPermission('targets.refresh') || !REFRESHABLE_TARGET_TYPES.has(normalizedType)) {
    return null
  }

  return (
    <Box sx={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'flex-end', gap: 0.5, maxWidth: '100%', minWidth: 0 }}>
    <Tooltip
      title={
        refreshTargetMutation.isPending
          ? 'Запускаем обновление…'
          : 'Обновить данные'
      }
    >
      <span>
        <IconButton
          size="small"
          aria-label="Обновить данные текущего монитора"
          disabled={refreshTargetMutation.isPending}
          onClick={() => refreshTargetMutation.mutate(targetId)}
          sx={{
            color: accentColor || 'text.secondary',
            backgroundColor: accentSoftBg || 'action.hover',
            '&:hover': { backgroundColor: accentSoftBg || 'action.hover' },
          }}
        >
          {refreshTargetMutation.isPending ? <CircularProgress size={18} /> : <UiIcon glyph={RefreshIcon} size={16} />}
        </IconButton>
      </span>
    </Tooltip>
    {refreshTargetMutation.isError && <Alert severity="warning" sx={{ maxWidth: 360 }}>
      {getApiErrorMessage(refreshTargetMutation.error, 'Не удалось запустить обновление. Повторите попытку.')}
    </Alert>}
    </Box>
  )
}

export default TargetRefreshButton
