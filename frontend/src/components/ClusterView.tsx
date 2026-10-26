import MonitorNodesSection from './MonitorNodesSection'
import MonitorQueryState from './MonitorQueryState'
import { UiIcon } from "@project/ui/mui"
import { useState, useEffect } from 'react'
import { Box, Chip, CircularProgress, Alert, Button, Dialog, DialogTitle, DialogContent, Typography } from '@mui/material'
import { ArrowLeftRight as SwapHorizIcon } from "@project/ui/icons"
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { clustersApi } from '../services/api'
import { websocketService } from '../services/websocket'
import { useAuth } from '../hooks/useAuth'
import DatabasesList from './DatabasesList'
import CollectionsSection from './CollectionsSection'
import NodesTable from './NodesTable'
import UsersSection from './UsersSection'
import { formatUpdateTime } from '../utils/formatUpdateTime'
import { InfoTooltip } from "@project/ui/mui"
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import MonitorSections from './MonitorSections'
import { useUiTheme } from "@project/ui/mui"
import { invalidateTargetQueries } from '../utils/queryInvalidation'

interface ClusterViewProps {
  clusterName: string
  targetType?: string
  highlightDatabaseName?: string | null
}

const ClusterView = ({ clusterName, targetType = 'mongodb', highlightDatabaseName = null }: ClusterViewProps) => {
  const { visual } = useUiTheme()
  const queryClient = useQueryClient()
  const { hasPermission, isAuthenticated } = useAuth()
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  const [topTab, setTopTab] = useState(0)
  const [stepDownDialogOpen, setStepDownDialogOpen] = useState(false)
  const [stepDownError, setStepDownError] = useState<string | null>(null)
  const fallbackRefetchMs = Number(import.meta.env.VITE_UPDATE_INTERVAL_MS ?? 15000)

  const parseServerTimestamp = (timestamp: string): Date => {
    // If backend sends naive ISO timestamp, treat it as UTC.
    const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/.test(timestamp)
    return new Date(hasTimezone ? timestamp : `${timestamp}Z`)
  }

  const { data: cluster, isLoading, error } = useQuery({
    queryKey: ['cluster', clusterName],
    queryFn: () => clustersApi.getCluster(clusterName),
    // Keep seamless updates even if WebSocket/Celery is temporarily unavailable.
    refetchInterval: fallbackRefetchMs,
    staleTime: Math.min(fallbackRefetchMs, 30_000),
  })

  // Listen for WebSocket updates

  useEffect(() => {
    const handleDataUpdate = (message: any) => {
      if (message.event === 'data_updated' && message.clusters?.includes(clusterName)) {
        // Refetch cluster data
        queryClient.invalidateQueries({ queryKey: ['cluster', clusterName] })
        setLastUpdate(new Date())
      }
    }

    websocketService.on('data_updated', handleDataUpdate)

    return () => {
      websocketService.off('data_updated', handleDataUpdate)
    }
  }, [clusterName, queryClient])

  // Refresh cluster data immediately after login/logout to show/hide users without F5.
  useEffect(() => {
    const handleAuthChanged = () => {
      queryClient.invalidateQueries({ queryKey: ['cluster', clusterName] })
    }

    window.addEventListener('auth-changed', handleAuthChanged)
    return () => {
      window.removeEventListener('auth-changed', handleAuthChanged)
    }
  }, [clusterName, queryClient])

  // Update lastUpdate when cluster data changes
  useEffect(() => {
    if (cluster?.last_update) {
      const parsed = parseServerTimestamp(cluster.last_update)
      if (!Number.isNaN(parsed.getTime())) {
        setLastUpdate(parsed)
      }
    }
  }, [cluster])

  useEffect(() => {
    if (highlightDatabaseName) {
      setTopTab(0)
    }
  }, [highlightDatabaseName])

  const invalidateCluster = () => {
    invalidateTargetQueries(queryClient, 'mongodb', clusterName)
  }

  const stepDownMutation = useMutation({
    mutationFn: () => clustersApi.stepDownPrimary(clusterName),
    onMutate: () => setStepDownError(null),
    onSuccess: () => {
      setStepDownDialogOpen(false)
      invalidateCluster()
      window.setTimeout(invalidateCluster, 2500)
      window.setTimeout(invalidateCluster, 8000)
      window.setTimeout(invalidateCluster, 15000)
    },
    onError: (err: unknown) => {
      const message = axios.isAxiosError(err)
        ? err.response?.data?.detail || err.message
        : err instanceof Error
          ? err.message
          : 'Неизвестная ошибка'
      setStepDownError(message)
    },
  })

  if (!cluster) return <MonitorQueryState loading={isLoading} error={error} hasData={false} label="MongoDB" />

  const versions = cluster.nodes
    .map((node) => node.mongo_version)
    .filter((version): version is string => Boolean(version))
  const clusterMongoVersionLabel =
    versions.length === 0
      ? null
      : new Set(versions).size === 1
        ? `MongoDB Version: ${versions[0]}`
        : 'MongoDB Version mismatch'
  const isMongoVersionMismatch = Boolean(clusterMongoVersionLabel?.includes('Version mismatch'))

  const primaryNode = cluster.nodes.find((node) => node.stateStr === 'PRIMARY')
  const canStepDownPrimary = hasPermission('mongodb.stepdown') && Boolean(primaryNode)
  const canViewUsers = hasPermission('mongodb.users.read')

  const mongoVersionChips = (
    <>
      {clusterMongoVersionLabel && (
        <Chip
          label={clusterMongoVersionLabel}
          size="small"
          color={isMongoVersionMismatch ? 'warning' : undefined}
          variant="outlined"
          sx={{
            ...(isMongoVersionMismatch
              ? {}
              : { color: visual.accentColor, borderColor: visual.accentColor }),
          }}
        />
      )}
      {cluster.feature_compatibility_version && (
        <InfoTooltip title="Feature Compatibility Version задаёт, какие возможности MongoDB включены в кластере.">
          <Box component="span">
            <Chip
              label={`Feature Compatibility Version: ${cluster.feature_compatibility_version}`}
              size="small"
              variant="outlined"
              sx={{ color: visual.accentColor, borderColor: visual.accentColor, ml: clusterMongoVersionLabel ? 1 : 0, mt: { xs: 1, sm: 0 } }}
            />
          </Box>
        </InfoTooltip>
      )}
    </>
  )

  return (
    <Box>
      {error && <Alert severity="warning">Не удалось обновить данные. Показан последний полученный результат.</Alert>}
      <VersionAndUpdateBlock
        versionChips={mongoVersionChips}
        updateTime={lastUpdate ? formatUpdateTime(lastUpdate) : null}
        refreshTarget={{ targetType, targetName: clusterName }}
        accentColor={visual.accentColor}
        accentSoftBg={visual.accentSoftBg}
      />

      <MonitorSections labels={['Databases', 'Collections', 'Users']} value={topTab} onChange={setTopTab} label="mongodb-sections">
          {topTab === 0 && (
            <DatabasesList
              databases={cluster.databases}
              backups={cluster.backups}
              highlightDatabaseName={highlightDatabaseName}
              accentColor={visual.accentColor}
            />
          )}
          {topTab === 1 && (
            <CollectionsSection
              databases={cluster.databases}
              clusterName={clusterName}
              accentColor={visual.accentColor}
              accentSoftBg={visual.accentSoftBg}
            />
          )}
          {topTab === 2 &&
            (!canViewUsers ? (
              <Alert
                severity="info"
                sx={{
                  border: '1px solid',
                  borderColor: visual.accentColor,
                  backgroundColor: visual.accentSoftBg,
                  width: 'fit-content',
                  maxWidth: '100%',
                  '& .MuiAlert-icon': { color: visual.accentColor },
                }}
              >
                {isAuthenticated ? 'Недостаточно прав для просмотра пользователей и их прав.' : 'Войдите, чтобы просмотреть пользователей и права.'}
              </Alert>
            ) : cluster.users && cluster.users.length > 0 ? (
              <UsersSection
                users={cluster.users}
                accentColor={visual.accentColor}
                accentSoftBg={visual.accentSoftBg}
              />
            ) : cluster.users_visible ? (
              <Alert
                severity="info"
                sx={{
                  border: '1px solid',
                  borderColor: visual.accentColor,
                  backgroundColor: visual.accentSoftBg,
                  width: 'fit-content',
                  maxWidth: '100%',
                  '& .MuiAlert-icon': { color: visual.accentColor },
                }}
              >
                Пользователи не найдены или мониторинговой учетке недоступны сведения об их правах.
              </Alert>
            ) : null)}
      </MonitorSections>

      <MonitorNodesSection empty={!cluster.nodes.length}>
        <NodesTable
          nodes={cluster.nodes}
          clusterName={clusterName}
          accentColor={visual.accentColor}
          accentSoftBg={visual.accentSoftBg}
          sampleTimestamp={cluster.last_update}
          headerActions={
            canStepDownPrimary ? (
              <InfoTooltip title="Снять текущий PRIMARY командой replSetStepDown(). Запустятся выборы PRIMARY.">
                <Box component="span">
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<UiIcon glyph={SwapHorizIcon} />}
                    onClick={() => setStepDownDialogOpen(true)}
                    disabled={stepDownMutation.isPending}
                    sx={{
                      backgroundColor: visual.accentSoftBg,
                      borderColor: visual.accentColor,
                      color: visual.accentColor,
                      '&:hover': { borderColor: visual.accentColor, backgroundColor: visual.accentSoftBg },
                    }}
                  >
                    Снять PRIMARY
                  </Button>
                </Box>
              </InfoTooltip>
            ) : null
          }
        />
      </MonitorNodesSection>
      <Dialog
        open={stepDownDialogOpen}
        onClose={() => {
          if (!stepDownMutation.isPending) setStepDownDialogOpen(false)
        }}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle
          sx={{
            py: 1.5,
            fontSize: '1rem',
            fontWeight: 600,
            color: visual.accentColor,
            borderBottom: '1px solid',
            borderColor: 'divider',
          }}
        >
          Снять PRIMARY?
        </DialogTitle>
        <DialogContent sx={{ pt: 2 }}>
          <Typography variant="body2" sx={{ mb: 1.5, fontFamily: 'inherit' }}>
            Выполнить replSetStepDown() для текущего Primary?
          </Typography>
          <Box sx={{ display: 'grid', gap: 0.75, mb: 1.5 }}>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" sx={{ width: 54, flexShrink: 0 }}>
                Cluster
              </Typography>
              <Typography variant="body2" sx={{ fontFamily: 'inherit' }}>
                {clusterName}
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" sx={{ width: 54, flexShrink: 0 }}>
                Primary
              </Typography>
              <Typography
                variant="body2"
                sx={{ fontFamily: 'inherit', overflowWrap: 'anywhere', lineHeight: 1.4 }}
              >
                {primaryNode?.name || 'N/A'}
              </Typography>
            </Box>
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'inherit' }}>
            Запустятся выборы в replica set. Данные обновятся автоматически.
          </Typography>
          {stepDownError ? (
            <Alert severity="error" sx={{ mt: 2 }}>
              {stepDownError}
            </Alert>
          ) : null}
        </DialogContent>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, px: 2, pb: 2, pt: 0 }}>
          <Button
            onClick={() => setStepDownDialogOpen(false)}
            variant="text"
            size="small"
            disabled={stepDownMutation.isPending}
            sx={{ color: 'text.secondary' }}
          >
            Отмена
          </Button>
          <Button
            onClick={() => stepDownMutation.mutate()}
            variant="contained"
            size="small"
            disabled={stepDownMutation.isPending}
            sx={{
              bgcolor: visual.accentColor,
              boxShadow: 'none',
              '&:hover': { bgcolor: visual.accentColor, filter: 'brightness(0.95)', boxShadow: 'none' },
            }}
          >
            {stepDownMutation.isPending ? <CircularProgress size={18} sx={{ color: 'inherit' }} /> : 'Снять PRIMARY'}
          </Button>
        </Box>
      </Dialog>
    </Box>
  )
}

export default ClusterView
