import { formatBytes, formatNumber } from '../utils/metricFormat'
import MonitorNodesSection from './MonitorNodesSection'
import MonitorQueryState from './MonitorQueryState'
import { UiIcon } from "@project/ui/mui"
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  LinearProgress,
  Menu,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material'
import { ChevronUp as ExpandLessIcon, ChevronDown as ExpandMoreIcon, ExternalLink as OpenInNewIcon, RotateCcw as RestartAltIcon } from "@project/ui/icons"
import { Fragment, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { formatUpdateTime } from '../utils/formatUpdateTime'
import { InfoTooltip } from "@project/ui/mui"
import NeutralValueBadge from './NeutralValueBadge'
import { useUiTheme } from "@project/ui/mui"
import {
  haproxyApi,
  HAProxyCluster,
  HAProxyIngress,
  HAProxyOperationStatus,
  HAProxyOverview,
  HAProxyProxy,
  HAProxyServer,
  K8sPodState,
} from '../services/api'
import { DataTable } from "@project/ui/mui"
import { IDENTITY_CONTENT_SX } from "@project/ui/mui"
import PodResourcesChips from './PodResourcesChips'
import { useAuth } from '../hooks/useAuth'
import { useOperationTracker } from '../hooks/useOperationTracker'
import OperationStatusChip from './OperationStatusChip'
import OperationTimeline from './OperationTimeline'
import { subscribeSse } from '../services/sse'
import { getPodBoxSx, isNewPod } from '../utils/podStyles'
import VersionAndUpdateBlock from './VersionAndUpdateBlock'

interface HAProxyViewProps {
  targetName: string
  targetType?: string
}

type PodStatus = 'ready' | 'not_ready' | 'terminating'
type RestartConfirmation =
  | { kind: 'rollout' }
  | { kind: 'pod'; podName: string }
  | null

interface HAProxyEndpointNode {
  id: string
  label: string
  url: string
  overview: HAProxyOverview
  proxies: HAProxyProxy[]
  totalSessions?: number
  totalSessionsLimit?: number
  totalBytesIn?: number
  totalBytesOut?: number
  minUptimeSeconds?: number
  /** When set, node comes from k8s_pods; show status chip (Lens-style). */
  podStatus?: PodStatus
  /** Detailed status reason (e.g. ContainerCreating, CrashLoopBackOff) */
  statusDetail?: string
  /** ISO timestamp when pod started; if &lt; 3s ago show "new" animation. */
  podStartTime?: string
  resources?: K8sPodState['resources']
}

const COMPLETED_VISIBLE_MS = 20_000

const HAProxyView = ({ targetName, targetType = 'haproxy' }: HAProxyViewProps) => {
  const getOperationTypeLabel = (operationType: string) => {
    if (operationType === 'k8s.rollout_restart') {
      return 'Rollout restart'
    }
    if (operationType === 'k8s.pod_restart') {
      return 'Pod restart'
    }
    return operationType || 'Operation'
  }
  const { visual } = useUiTheme()
  const { hasPermission } = useAuth()
  const canRestart = hasPermission('haproxy.restart')
  const queryClient = useQueryClient()
  const [restartConfirmation, setRestartConfirmation] = useState<RestartConfirmation>(null)
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())
  const [expandedPodRows, setExpandedPodRows] = useState<Set<string>>(new Set())
  const [rolloutOperationId, setRolloutOperationId] = useState<string | null>(null)
  const [podOperation, setPodOperation] = useState<{ podName: string; operationId: string } | null>(null)
  const [ingressMenuAnchor, setIngressMenuAnchor] = useState<HTMLElement | null>(null)
  const [, setTick] = useState(0)
  const { data, isLoading, error } = useQuery<HAProxyCluster>({
    queryKey: ['haproxy-target', targetName],
    queryFn: () => haproxyApi.getTarget(targetName),
    refetchInterval: (): number => {
      const ops = queryClient.getQueryData<HAProxyOperationStatus[]>(['haproxy-operations', targetName])
      const hasRunningFromList = (ops || []).some(
        (item) => item.status === 'queued' || item.status === 'running',
      )
      const hasRunning =
        hasRunningFromList ||
        Boolean(rolloutOperationId && (rolloutOperationQuery.isLoading || isRunningOperationStatus(rolloutOperationQuery.data?.status))) ||
        Boolean(podOperation?.operationId && (podOperationQuery.isLoading || isRunningOperationStatus(podOperationQuery.data?.status)))
      if (hasRunning) return 2000
      const cached = queryClient.getQueryData<HAProxyCluster>(['haproxy-target', targetName])
      const hasPods = (cached?.k8s_pods?.length ?? 0) > 0
      return hasPods ? 5000 : 30000
    },
    staleTime: 5_000,
  })

  const restartPodMutation = useMutation({
    mutationFn: ({ podName }: { podName: string }) => haproxyApi.restartPod(targetName, podName),
    onSuccess: (response, variables) => {
      setPodOperation({ podName: variables.podName, operationId: response.operation_id })
      queryClient.invalidateQueries({ queryKey: ['haproxy-target', targetName] })
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Неизвестная ошибка'
      window.alert(`Не удалось перезапустить pod: ${message}`)
    },
  })

  const rolloutRestartMutation = useMutation({
    mutationFn: () => haproxyApi.rolloutRestart(targetName),
    onSuccess: (response) => {
      setRolloutOperationId(response.operation_id)
      queryClient.invalidateQueries({ queryKey: ['haproxy-target', targetName] })
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : 'Неизвестная ошибка'
      window.alert(`Не удалось запустить rollout restart: ${message}`)
    },
  })

  const formatIngressLabel = (ingress: HAProxyIngress) => {
    const path = ingress.path && ingress.path !== '/' ? ingress.path : ''
    return `${ingress.host}${path}`
  }

  const getStatusColor = (status: string) => {
    switch (status.toUpperCase()) {
      case 'UP':
        return 'success'
      case 'DOWN':
      case 'MAINT':
        return 'error'
      case 'GOING_UP':
      case 'GOING_DOWN':
        return 'warning'
      default:
        return 'default'
    }
  }

  const formatDuration = (seconds?: number) => {
    if (seconds === undefined || seconds === null || seconds < 0) return 'N/A'
    const d = Math.floor(seconds / 86400)
    const h = Math.floor((seconds % 86400) / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    if (d > 0) return `${d}d ${h}h`
    if (h > 0) return `${h}h ${m}m`
    return `${m}m`
  }

  const getPodUptimeSeconds = (startTime?: string) => {
    if (!startTime) return undefined
    const startedAt = new Date(startTime).getTime()
    if (!Number.isFinite(startedAt)) return undefined
    return Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
  }

  const getUsageColor = (current?: number, limit?: number): 'default' | 'success' | 'warning' | 'error' => {
    if (current === undefined || current === null || limit === undefined || limit === null || limit <= 0) {
      return 'default'
    }
    const ratio = current / limit
    if (ratio >= 0.9) return 'error'
    if (ratio >= 0.75) return 'warning'
    return 'success'
  }

  const formatSessionsRange = (current?: number, limit?: number) => {
    if (current === undefined || current === null) return 'N/A'
    if (limit === undefined || limit === null || limit <= 0) return `${formatNumber(current)} / N/A`
    return `${formatNumber(current)} / ${formatNumber(limit)}`
  }

  const formatSessionsPercent = (current?: number, limit?: number) => {
    if (current === undefined || current === null || limit === undefined || limit === null || limit <= 0) {
      return 'N/A'
    }
    return `${Math.round((current / limit) * 100)}%`
  }
  const closeRestartConfirmation = () => setRestartConfirmation(null)
  const submitRestartConfirmation = () => {
    if (!restartConfirmation) return
    if (restartConfirmation.kind === 'rollout') {
      rolloutRestartMutation.mutate()
    } else {
      restartPodMutation.mutate({ podName: restartConfirmation.podName })
    }
    closeRestartConfirmation()
  }
  const rolloutOperationQuery = useOperationTracker({ operationId: rolloutOperationId, enabled: Boolean(rolloutOperationId) })
  const podOperationQuery = useOperationTracker({
    operationId: podOperation?.operationId,
    enabled: Boolean(podOperation?.operationId),
  })
  const operationsQuery = useQuery<HAProxyOperationStatus[]>({
    queryKey: ['haproxy-operations', targetName],
    queryFn: () => haproxyApi.getOperations(targetName, 12),
    enabled: canRestart,
    refetchInterval: (query) => {
      const hasRunning = (query.state.data || []).some(
        (item) => item.status === 'queued' || item.status === 'running',
      )
      return hasRunning ? 2000 : 15000
    },
  })
  const isRunningOperationStatus = (status?: string) => status === 'queued' || status === 'running'
  // Do not invalidate target on every operation tick — causes 6–10 refetches/sec and UI lag.
  // Target refetchInterval (2s when running) and SSE operations.updated drive updates.
  const operationsSseDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (!canRestart) return undefined
    const unsubscribe = subscribeSse(
      {
        targetType: 'haproxy',
        targetName,
        event: 'operations.updated',
      },
      (_message) => {
        // Invalidate only list and target; do not invalidate single operation to avoid
        // a refetch per SSE (backend sends ~every 1.5s) and log flood. Single operation
        // updates via its own refetchInterval (5s).
        if (operationsSseDebounceRef.current) clearTimeout(operationsSseDebounceRef.current)
        operationsSseDebounceRef.current = setTimeout(() => {
          operationsSseDebounceRef.current = null
          queryClient.invalidateQueries({ queryKey: ['haproxy-operations', targetName] })
          queryClient.invalidateQueries({ queryKey: ['haproxy-target', targetName] })
        }, 400)
      },
    )
    return () => {
      if (operationsSseDebounceRef.current) clearTimeout(operationsSseDebounceRef.current)
      unsubscribe()
    }
  }, [canRestart, queryClient, targetName])
  // Debounce SSE-driven refetch so rapid watch events don't cause lag (rollout = many events)
  const sseDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (!canRestart) return undefined
    const unsubscribe = subscribeSse(
      {
        targetType: 'haproxy',
        targetName,
        event: 'haproxy.k8s.changed',
      },
      (message) => {
        if (message.pods_state) {
          queryClient.setQueryData<HAProxyCluster>(['haproxy-target', targetName], (old) => {
            if (!old) return old
            return {
              ...old,
              k8s_pods: message.pods_state as K8sPodState[],
            }
          })
        }
        if (sseDebounceRef.current) clearTimeout(sseDebounceRef.current)
        sseDebounceRef.current = setTimeout(() => {
          sseDebounceRef.current = null
          queryClient.invalidateQueries({ queryKey: ['haproxy-target', targetName] })
        }, 2000)
      },
    )
    return () => {
      if (sseDebounceRef.current) clearTimeout(sseDebounceRef.current)
      unsubscribe()
    }
  }, [canRestart, queryClient, targetName])
  useEffect(() => {
    setRolloutOperationId(null)
    setPodOperation(null)
    setExpandedRows(new Set())
    setExpandedPodRows(new Set())
  }, [targetName])

  // Clear operation id after "Completed" visibility window so strip/chip hide and we stop polling
  useEffect(() => {
    if (!rolloutOperationId) return
    const status = rolloutOperationQuery.data?.status
    if (status !== 'succeeded' && status !== 'failed') return
    const t = setTimeout(() => setRolloutOperationId(null), COMPLETED_VISIBLE_MS + 1000)
    return () => clearTimeout(t)
  }, [rolloutOperationId, rolloutOperationQuery.data?.status])
  useEffect(() => {
    if (!podOperation?.operationId) return
    const status = podOperationQuery.data?.status
    if (status !== 'succeeded' && status !== 'failed') return
    const t = setTimeout(() => setPodOperation(null), COMPLETED_VISIBLE_MS + 1000)
    return () => clearTimeout(t)
  }, [podOperation?.operationId, podOperationQuery.data?.status])

  // Re-render every second for 21s after a completion so "Completed" stays visible 20s then panel can hide
  useEffect(() => {
    const ops = operationsQuery.data || []
    const latestCompletedAt = ops
      .filter((op) => op.status === 'succeeded' && op.completed_at)
      .map((op) => new Date(op.completed_at!).getTime())
      .filter(Number.isFinite)
      .sort((a, b) => b - a)[0]
    if (latestCompletedAt == null || Date.now() - latestCompletedAt >= COMPLETED_VISIBLE_MS) return undefined
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    const timeout = setTimeout(() => clearInterval(id), 21_000)
    return () => {
      clearInterval(id)
      clearTimeout(timeout)
    }
  }, [operationsQuery.data])

  // Re-render every 1s when we have pods with start_time so "new pod" animation stops after NEW_POD_MS
  useEffect(() => {
    const pods = data?.k8s_pods
    if (!pods?.some((p) => p.start_time)) return undefined
    const id = setInterval(() => setTick((t) => t + 1), 1000)
    const timeout = setTimeout(() => clearInterval(id), 5_000)
    return () => {
      clearInterval(id)
      clearTimeout(timeout)
    }
  }, [data?.k8s_pods])

  const getMinUptimeSeconds = (servers: HAProxyServer[]) => {
    const upLastchg = servers
      .filter((s) => s.status?.toUpperCase() === 'UP' && s.lastchg !== undefined && s.lastchg !== null)
      .map((s) => s.lastchg as number)
    if (upLastchg.length > 0) return Math.min(...upLastchg)

    const anyLastchg = servers
      .filter((s) => s.lastchg !== undefined && s.lastchg !== null)
      .map((s) => s.lastchg as number)
    if (anyLastchg.length > 0) return Math.min(...anyLastchg)
    return undefined
  }

  const toggleRow = (proxyName: string, podKey?: string) => {
    const key = podKey ? `${podKey}|${proxyName}` : proxyName
    const setter = podKey ? setExpandedPodRows : setExpandedRows
    const expanded = podKey ? expandedPodRows : expandedRows
    const newExpanded = new Set(expanded)
    if (newExpanded.has(key)) {
      newExpanded.delete(key)
    } else {
      newExpanded.add(key)
    }
    setter(newExpanded)
  }

  const isRowExpanded = (proxyName: string, podKey?: string) => {
    const key = podKey ? `${podKey}|${proxyName}` : proxyName
    const expanded = podKey ? expandedPodRows : expandedRows
    return expanded.has(key)
  }

  const renderOverviewChips = (
    overview: HAProxyOverview,
    aggregatedSessionsCurrent?: number,
    aggregatedSessionsLimit?: number,
  ) => (
    <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
      <InfoTooltip title="Количество frontend/backend прокси в конфигурации">
        <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
          <Typography component="span" variant="body2" color="text.secondary">Proxies:</Typography>
          <Chip label={overview.total_proxies} size="small" variant="outlined" />
        </Box>
      </InfoTooltip>
      <InfoTooltip title="Серверы в состоянии UP (health check успешен)">
        <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
          <Typography component="span" variant="body2" color="text.secondary">UP:</Typography>
          <Chip label={overview.servers_up} size="small" color="success" variant="outlined" />
        </Box>
      </InfoTooltip>
      <InfoTooltip title="Серверы в состоянии DOWN или MAINT">
        <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
          <Typography component="span" variant="body2" color="text.secondary">DOWN:</Typography>
          <Chip
            label={overview.servers_down}
            size="small"
            color={overview.servers_down > 0 ? 'error' : 'default'}
            variant="outlined"
          />
        </Box>
      </InfoTooltip>
      {(aggregatedSessionsCurrent !== undefined
        || overview.frontend_sessions !== undefined
        || overview.total_sessions !== undefined) && (
        <InfoTooltip title="Текущие сессии на frontend (общая нагрузка target)">
          <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
            <Typography component="span" variant="body2" color="text.secondary">Sessions:</Typography>
            <Chip
              label={formatSessionsRange(
                aggregatedSessionsCurrent ?? overview.frontend_sessions ?? overview.total_sessions,
                aggregatedSessionsLimit ?? overview.frontend_sessions_limit,
              )}
              size="small"
              variant="outlined"
            />
            <Chip
              label={formatSessionsPercent(
                aggregatedSessionsCurrent ?? overview.frontend_sessions ?? overview.total_sessions,
                aggregatedSessionsLimit ?? overview.frontend_sessions_limit,
              )}
              size="small"
              color={getUsageColor(
                aggregatedSessionsCurrent ?? overview.frontend_sessions ?? overview.total_sessions,
                aggregatedSessionsLimit ?? overview.frontend_sessions_limit,
              )}
              variant="outlined"
            />
          </Box>
        </InfoTooltip>
      )}
    </Box>
  )

  const renderProxiesTable = (proxies: HAProxyProxy[], podKey?: string) => (
    <TableContainer sx={{ maxWidth: '100%', overflowX: 'auto' }}>
      <Table sx={{ minWidth: 600 }} size="small">
        <TableHead>
          <TableRow>
            <TableCell padding="checkbox" />
            <TableCell sx={{ fontSize: '0.9rem' }}>Proxy Name</TableCell>
            <TableCell align="center">Port</TableCell>
            <TableCell align="center">Servers</TableCell>
            <TableCell align="center">UP</TableCell>
            <TableCell align="center">DOWN</TableCell>
            <TableCell align="center">Sessions</TableCell>
            <TableCell align="center">Bytes In</TableCell>
            <TableCell align="center">Bytes Out</TableCell>
            <TableCell align="center">Uptime</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {proxies.map((proxy: HAProxyProxy) => {
            const isExpanded = isRowExpanded(proxy.name, podKey)
            const proxyTotals = proxy.servers.reduce(
              (acc, server) => {
                if (server.bin !== undefined && server.bin !== null) {
                  acc.hasBytesIn = true
                  acc.bytesIn += server.bin
                }
                if (server.bout !== undefined && server.bout !== null) {
                  acc.hasBytesOut = true
                  acc.bytesOut += server.bout
                }
                return acc
              },
              {
                bytesIn: 0,
                bytesOut: 0,
                hasBytesIn: false,
                hasBytesOut: false,
              },
            )
            const backendMinUptime = getMinUptimeSeconds(proxy.servers)
            return (
              <Fragment key={proxy.name}>
                <TableRow hover>
                  <TableCell padding="checkbox">
                    <IconButton size="small" onClick={() => toggleRow(proxy.name, podKey)} aria-label="expand row">
                      {isExpanded ? <UiIcon glyph={ExpandLessIcon} /> : <UiIcon glyph={ExpandMoreIcon} />}
                    </IconButton>
                  </TableCell>
                  <TableCell>
                    <NeutralValueBadge sx={{ fontWeight: 600 }}>{proxy.name}</NeutralValueBadge>
                  </TableCell>
                  <TableCell align="center">
                    {proxy.port !== undefined ? (
                      <Chip label={proxy.port} size="small" variant="outlined" />
                    ) : (
                      <NeutralValueBadge>-</NeutralValueBadge>
                    )}
                  </TableCell>
                  <TableCell align="center">
                    <NeutralValueBadge>{proxy.total_servers}</NeutralValueBadge>
                  </TableCell>
                  <TableCell align="center">
                    <Chip label={proxy.servers_up} size="small" color="success" variant="outlined" />
                  </TableCell>
                  <TableCell align="center">
                    <Chip
                      label={proxy.servers_down}
                      size="small"
                      color={proxy.servers_down > 0 ? 'error' : 'default'}
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell align="center">
                    <NeutralValueBadge>{formatNumber(proxy.sessions_current)}</NeutralValueBadge>
                  </TableCell>
                  <TableCell align="center">
                    <NeutralValueBadge>{formatBytes(proxyTotals.hasBytesIn ? proxyTotals.bytesIn : undefined)}</NeutralValueBadge>
                  </TableCell>
                  <TableCell align="center">
                    <NeutralValueBadge>
                      {formatBytes(proxyTotals.hasBytesOut ? proxyTotals.bytesOut : undefined)}
                    </NeutralValueBadge>
                  </TableCell>
                  <TableCell align="center">
                    <InfoTooltip title="Минимальный uptime среди UP серверов этого backend (fallback: любой сервер)">
                      <Box component="span" sx={{ display: 'inline-flex' }}>
                        <NeutralValueBadge>{formatDuration(backendMinUptime)}</NeutralValueBadge>
                      </Box>
                    </InfoTooltip>
                  </TableCell>
                </TableRow>
                {isExpanded && (
                  <TableRow>
                    <TableCell colSpan={9} sx={{ py: 2, backgroundColor: 'background.default' }}>
                      <Box sx={{ pl: 2 }}>
                        <Typography variant="subtitle2" gutterBottom sx={{ mb: 1.5 }}>
                          Servers
                        </Typography>
                        <Table size="small">
                          <TableHead>
                            <TableRow>
                              <TableCell>Server</TableCell>
                              <TableCell align="center">Status</TableCell>
                              <TableCell align="center">Sessions</TableCell>
                              <TableCell align="center">Bytes In</TableCell>
                              <TableCell align="center">Bytes Out</TableCell>
                              <TableCell align="center">Check</TableCell>
                              <TableCell align="center">Uptime</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {proxy.servers.map((server: HAProxyServer) => (
                              <TableRow key={server.server_name}>
                                <TableCell>
                                  <NeutralValueBadge sx={{ fontWeight: 600 }}>{server.server_name}</NeutralValueBadge>
                                </TableCell>
                                <TableCell align="center">
                                  <Chip
                                    label={server.status}
                                    size="small"
                                    color={getStatusColor(server.status)}
                                    variant="outlined"
                                  />
                                </TableCell>
                                <TableCell align="center">
                                  <Box>
                                    <NeutralValueBadge>{formatNumber(server.scur)}</NeutralValueBadge>
                                  </Box>
                                </TableCell>
                                <TableCell align="center">
                                  <NeutralValueBadge>{formatBytes(server.bin)}</NeutralValueBadge>
                                </TableCell>
                                <TableCell align="center">
                                  <NeutralValueBadge>{formatBytes(server.bout)}</NeutralValueBadge>
                                </TableCell>
                                <TableCell align="center">
                                  {server.check_status ? (
                                    <InfoTooltip title={`Статус health check: ${server.check_status}`}>
                                      <Chip
                                        label={server.check_status}
                                        size="small"
                                        color={
                                          server.check_status === 'L4OK' || server.check_status === 'L7OK'
                                            ? 'success'
                                            : 'default'
                                        }
                                        variant="outlined"
                                      />
                                    </InfoTooltip>
                                  ) : (
                                    <NeutralValueBadge>N/A</NeutralValueBadge>
                                  )}
                                </TableCell>
                                <TableCell align="center">
                                  <InfoTooltip title="Время с последнего изменения состояния сервера (lastchg)">
                                    <Box component="span" sx={{ display: 'inline-flex' }}>
                                      <NeutralValueBadge>{formatDuration(server.lastchg)}</NeutralValueBadge>
                                    </Box>
                                  </InfoTooltip>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </Box>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
    </TableContainer>
  )

  if (!data) return <MonitorQueryState loading={isLoading} error={error} hasData={false} label="HAProxy" />

  const { overview, proxies, pods, k8s_pods } = data
  const canUseRestartControls = canRestart && Boolean(data.restart_supported)
  const ingressLinks = (data.ingresses || []).filter((ingress) => ingress.url)
  const singleIngress = ingressLinks.length === 1 ? ingressLinks[0] : null
  const ingressActionButtons = singleIngress ? (
    <InfoTooltip title={`Открыть Statistics ${formatIngressLabel(singleIngress)} в новой вкладке`}>
      <Button
        size="small"
        variant="outlined"
        component="a"
        href={singleIngress.url}
        target="_blank"
        rel="noopener noreferrer"
        startIcon={<UiIcon glyph={OpenInNewIcon} />}
        sx={{
          backgroundColor: visual.accentSoftBg,
          borderColor: visual.accentColor,
          borderWidth: 1.5,
          color: visual.accentColor,
          '&:hover': { borderColor: visual.accentColor, backgroundColor: visual.accentSoftBg },
        }}
      >
        Stats
      </Button>
    </InfoTooltip>
  ) : ingressLinks.length > 1 ? (
    <>
      <Button
        size="small"
        variant="outlined"
        startIcon={<UiIcon glyph={OpenInNewIcon} />}
        onClick={(event) => setIngressMenuAnchor(event.currentTarget)}
        sx={{
          backgroundColor: visual.accentSoftBg,
          borderColor: visual.accentColor,
          borderWidth: 1.5,
          color: visual.accentColor,
          '&:hover': { borderColor: visual.accentColor, backgroundColor: visual.accentSoftBg },
        }}
      >
        Stats ({ingressLinks.length})
      </Button>
      <Menu
        anchorEl={ingressMenuAnchor}
        open={Boolean(ingressMenuAnchor)}
        onClose={() => setIngressMenuAnchor(null)}
      >
        {ingressLinks.map((ingress) => (
          <MenuItem
            key={`${ingress.namespace}:${ingress.name}:${ingress.url}`}
            component="a"
            href={ingress.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={() => setIngressMenuAnchor(null)}
            sx={{ display: 'block', maxWidth: 420 }}
          >
            <Typography variant="body2" noWrap>
              {formatIngressLabel(ingress)}
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {ingress.namespace}/{ingress.name}
            </Typography>
          </MenuItem>
        ))}
      </Menu>
    </>
  ) : undefined
  const isCompletedInLast20s = (op: HAProxyOperationStatus) => {
    if (op.status !== 'succeeded' || !op.completed_at) return false
    const completedAt = new Date(op.completed_at).getTime()
    return Number.isFinite(completedAt) && Date.now() - completedAt < COMPLETED_VISIBLE_MS
  }
  const emptyOverview: HAProxyOverview = {
    total_proxies: 0,
    total_servers: 0,
    servers_up: 0,
    servers_down: 0,
  }
  const emptyProxies: HAProxyProxy[] = []
  const isFinalStatus = (s?: string) => s === 'succeeded' || s === 'failed'
  const rolloutStillRunning =
    Boolean(rolloutOperationId) &&
    !isFinalStatus(rolloutOperationQuery.data?.status)
  const podStillRunning =
    Boolean(podOperation?.operationId) &&
    !isFinalStatus(podOperationQuery.data?.status)
  // Prefer list as source of truth so when navigating to the page we see active rollout immediately
  const operationsList = operationsQuery.data || []
  const hasActiveFromList = operationsList.some(
    (item) => item.status === 'queued' || item.status === 'running',
  )
  const hasActiveOperations = (
    rolloutRestartMutation.isPending
    || restartPodMutation.isPending
    || Boolean(rolloutStillRunning)
    || Boolean(podStillRunning)
    || hasActiveFromList
  )
  const activeOpFromList = operationsList.find(
    (op) => op.status === 'queued' || op.status === 'running',
  )
  const progressMessage =
    activeOpFromList?.message ??
    (rolloutOperationQuery.data?.message ?? podOperationQuery.data?.message) ??
    'Restart/rollout в процессе…'
  const hasRecentlyCompleted = operationsList.some(isCompletedInLast20s)
  const showOperationsPanel = canUseRestartControls && (
    hasActiveOperations
    || hasRecentlyCompleted
    || operationsQuery.isLoading
    || Boolean(operationsQuery.error)
    || Boolean((operationsQuery.data || []).length)
  )
  const calculateEndpointTotals = (nodeProxies: HAProxyProxy[]) => {
    let totalBytesIn = 0
    let totalBytesOut = 0
    let minUptimeSeconds: number | undefined
    let hasBytesIn = false
    let hasBytesOut = false

    for (const proxy of nodeProxies) {
      const proxyMinUptime = getMinUptimeSeconds(proxy.servers)
      if (proxyMinUptime !== undefined) {
        minUptimeSeconds =
          minUptimeSeconds === undefined ? proxyMinUptime : Math.min(minUptimeSeconds, proxyMinUptime)
      }
      for (const server of proxy.servers) {
        if (server.bin !== undefined && server.bin !== null) {
          hasBytesIn = true
          totalBytesIn += server.bin
        }
        if (server.bout !== undefined && server.bout !== null) {
          hasBytesOut = true
          totalBytesOut += server.bout
        }
      }
    }

    return {
      totalBytesIn: hasBytesIn ? totalBytesIn : undefined,
      totalBytesOut: hasBytesOut ? totalBytesOut : undefined,
      minUptimeSeconds,
    }
  }

  const hasPods = pods && pods.length > 0
  const useK8sPods = k8s_pods && k8s_pods.length > 0
  const endpointNodes: HAProxyEndpointNode[] = useK8sPods
    ? (k8s_pods || []).map((k8sPod) => {
        const name = k8sPod.name ?? ''
        const podStatus: PodStatus = k8sPod.terminating
          ? 'terminating'
          : k8sPod.ready
            ? 'ready'
            : 'not_ready'
        const pod = (pods || []).find((p) => p.label === name)
        if (pod) {
          const totals = calculateEndpointTotals(pod.proxies)
          return {
            id: pod.label,
            label: pod.label,
            url: pod.url,
            overview: pod.overview,
            proxies: pod.proxies,
            totalSessions: pod.overview.frontend_sessions ?? pod.overview.total_sessions,
            totalSessionsLimit: pod.overview.frontend_sessions_limit,
            ...totals,
            podStatus,
            statusDetail: k8sPod.status_detail,
            podStartTime: k8sPod.start_time,
            resources: k8sPod.resources,
          }
        }
        return {
          id: name || 'unknown',
          label: name || 'unknown',
          url: '',
          overview: emptyOverview,
          proxies: emptyProxies,
          podStatus,
          statusDetail: k8sPod.status_detail,
          podStartTime: k8sPod.start_time,
          resources: k8sPod.resources,
        }
      })
    : hasPods
      ? (pods || []).map((pod) => {
          const totals = calculateEndpointTotals(pod.proxies)
          return {
            id: pod.label,
            label: pod.label,
            url: pod.url,
            overview: pod.overview,
            proxies: pod.proxies,
            totalSessions: pod.overview.frontend_sessions ?? pod.overview.total_sessions,
            totalSessionsLimit: pod.overview.frontend_sessions_limit,
            ...totals,
          }
        })
      : [
          (() => {
            const totals = calculateEndpointTotals(proxies)
            return {
              id: 'aggregated',
              label: targetName,
              url: 'aggregated',
              overview,
              proxies,
              totalSessions: overview.frontend_sessions ?? overview.total_sessions,
              totalSessionsLimit: overview.frontend_sessions_limit,
              ...totals,
            }
          })(),
        ]

  const isNewPodLocal = (node: HAProxyEndpointNode) => isNewPod(node.podStartTime)
  const hasPodLevelView = hasPods || useK8sPods
  const aggregatedOverviewSessionsCurrent = hasPodLevelView
    ? endpointNodes.reduce((sum, node) => sum + (node.totalSessions ?? 0), 0)
    : (overview.frontend_sessions ?? overview.total_sessions)

  const hasAnyPodSessionsLimit = hasPodLevelView && endpointNodes.some((node) => node.totalSessionsLimit !== undefined)
  const aggregatedOverviewSessionsLimit = hasPodLevelView
    ? (hasAnyPodSessionsLimit
      ? endpointNodes.reduce((sum, node) => sum + (node.totalSessionsLimit ?? 0), 0)
      : undefined)
    : overview.frontend_sessions_limit
  const getNodeSessionsPercent = (node: HAProxyEndpointNode): number | undefined => {
    if (node.totalSessions === undefined || node.totalSessions === null) return undefined
    if (node.totalSessionsLimit === undefined || node.totalSessionsLimit === null || node.totalSessionsLimit <= 0) {
      return undefined
    }
    return (node.totalSessions / node.totalSessionsLimit) * 100
  }
  const podStatusOrder = (s?: PodStatus) => (s === 'terminating' ? 0 : s === 'not_ready' ? 1 : 2)
  const endpointNodesSorted = [...endpointNodes].sort((a, b) => {
    if (a.podStatus !== undefined || b.podStatus !== undefined) {
      const oa = podStatusOrder(a.podStatus)
      const ob = podStatusOrder(b.podStatus)
      if (oa !== ob) return oa - ob
    }
    const aPercent = getNodeSessionsPercent(a)
    const bPercent = getNodeSessionsPercent(b)
    if (aPercent !== undefined && bPercent !== undefined && bPercent !== aPercent) return bPercent - aPercent
    if (aPercent !== undefined && bPercent === undefined) return -1
    if (aPercent === undefined && bPercent !== undefined) return 1

    const aSessions = a.totalSessions ?? -1
    const bSessions = b.totalSessions ?? -1
    if (bSessions !== aSessions) return bSessions - aSessions

    return a.label.localeCompare(b.label)
  })

  const paperSx = {
    border: '1px solid',
    borderColor: 'divider',
    boxShadow: 'none',
    p: 1.5,
  }

  return (
    <Box>
      <MonitorQueryState loading={false} error={error} hasData={true} label="HAProxy" />
      {/* Overview (aggregated across all backends/pods) */}
      <Box sx={{ mb: 3, position: 'relative' }}>
        <VersionAndUpdateBlock
          versionChips={(
            <Chip
              label={overview.version ? `HAProxy Version: ${overview.version}` : 'HAProxy Version: N/A'}
              size="small"
              variant="outlined"
              sx={{ color: visual.accentColor, borderColor: visual.accentColor }}
            />
          )}
          actionButtons={ingressActionButtons}
          updateTime={data.last_update ? formatUpdateTime(data.last_update) : null}
          refreshTarget={{ targetType, targetName }}
          accentColor={visual.accentColor}
          accentSoftBg={visual.accentSoftBg}
        />
        <Box sx={{ mt: 1.5, mb: 1.5, display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Typography variant="h6">
            Overview
          </Typography>
          {canUseRestartControls && (
            <InfoTooltip title="Мягкий rollout restart всех pod-ов через Deployment">
              <Box component="span">
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<UiIcon glyph={RestartAltIcon} size={16} />}
                  onClick={() => setRestartConfirmation({ kind: 'rollout' })}
                  disabled={
                    rolloutRestartMutation.isPending
                    || isRunningOperationStatus(rolloutOperationQuery.data?.status)
                  }
                  sx={{
                    color: visual.accentColor,
                    borderColor: visual.accentColor,
                    '&:hover': {
                      borderColor: visual.accentColor,
                      backgroundColor: visual.accentSoftBg,
                    },
                  }}
                >
                  {rolloutRestartMutation.isPending ? 'Restarting...' : 'Rollout restart'}
                </Button>
              </Box>
            </InfoTooltip>
          )}
          {rolloutOperationQuery.data && (
            <InfoTooltip title={rolloutOperationQuery.data.message}>
              <Box component="span">
                <OperationStatusChip
                  status={rolloutOperationQuery.data.status}
                  progress={rolloutOperationQuery.data.progress}
                />
              </Box>
            </InfoTooltip>
          )}
        </Box>
        <Paper elevation={0} sx={paperSx}>
          {renderOverviewChips(
            overview,
            aggregatedOverviewSessionsCurrent,
            aggregatedOverviewSessionsLimit,
          )}
        </Paper>
        <Box
          sx={{
            position: 'absolute',
            bottom: -32,
            left: 0,
            right: 0,
            opacity: hasActiveOperations ? 1 : 0,
            transition: 'opacity 0.3s ease-out',
            display: 'flex',
            flexDirection: 'column',
            pointerEvents: hasActiveOperations ? 'auto' : 'none',
            zIndex: 1,
          }}
        >
          <LinearProgress
            sx={{
              width: '100%',
              height: 2,
              bgcolor: 'action.hover',
              '& .MuiLinearProgress-bar': {
                bgcolor: visual.accentColor,
              },
            }}
          />
          <Typography
            variant="caption"
            sx={{
              alignSelf: 'flex-start',
              mt: 0.5,
              color: visual.accentColor,
              fontWeight: 500,
            }}
          >
            {progressMessage}
          </Typography>
        </Box>
      </Box>

      {/* Nodes (endpoints/pods) */}
      <Box sx={{ mt: 3, mb: 2 }}>
        <MonitorNodesSection empty={!endpointNodesSorted.length}>
        <DataTable<HAProxyEndpointNode>
          title="Nodes"
          columns={[
            {
              id: 'endpoint',
              label: 'Pods',
              isIdentityColumn: true,
              render: (node) => {
                const isPending =
                  restartPodMutation.isPending && restartPodMutation.variables?.podName === node.label
                const isNodeOperation = podOperation?.podName === node.label
                const nodeOperationStatus = isNodeOperation ? podOperationQuery.data : undefined
                const newPod = isNewPodLocal(node)
                const isTerminating = node.podStatus === 'terminating'
                return (
                  <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                    <Box
                      component="span"
                      sx={getPodBoxSx(newPod, isTerminating, visual.accentColor, visual.accentSoftBg)}
                    >
                      <Typography component="span" sx={IDENTITY_CONTENT_SX}>{node.label}</Typography>
                    </Box>
                    {node.podStatus !== undefined && (
                      <Chip
                        size="small"
                        label={
                          node.podStatus === 'terminating'
                            ? 'Terminating'
                            : node.podStatus === 'not_ready'
                              ? (node.statusDetail || 'Not Ready')
                              : 'Ready'
                        }
                        color={
                          node.podStatus === 'terminating'
                            ? 'warning'
                            : node.podStatus === 'not_ready'
                              ? 'default'
                              : 'success'
                        }
                        variant="outlined"
                        sx={{ height: 20, fontSize: '0.7rem' }}
                      />
                    )}
                    {canUseRestartControls && (
                      <InfoTooltip title="Перезапустить только этот pod">
                        <Box
                          component="span"
                          sx={{
                            display: 'inline-flex',
                            opacity: 0,
                            transition: 'opacity 0.18s ease',
                            '.MuiTableRow-hover:hover &': { opacity: 1 },
                            '.MuiTableRow-hover:focus-within &': { opacity: 1 },
                          }}
                        >
                          <IconButton
                            size="small"
                            disabled={
                              isPending ||
                              node.podStatus === 'terminating' ||
                              isRunningOperationStatus(nodeOperationStatus?.status)
                            }
                            onClick={(e) => {
                              e.stopPropagation()
                              setRestartConfirmation({ kind: 'pod', podName: node.label })
                            }}
                            sx={{ color: visual.accentColor }}
                          >
                            {isPending ? <CircularProgress size={16} sx={{ color: visual.accentColor }} /> : <UiIcon glyph={RestartAltIcon} size={16} />}
                          </IconButton>
                        </Box>
                      </InfoTooltip>
                    )}
                    {nodeOperationStatus && (
                      <InfoTooltip title={nodeOperationStatus.message}>
                        <Box component="span">
                          <OperationStatusChip
                            status={nodeOperationStatus.status}
                            progress={nodeOperationStatus.progress}
                          />
                        </Box>
                      </InfoTooltip>
                    )}
                  </Box>
                )
              },
            },
            {
              id: 'resources',
              label: 'Resources',
              align: 'center',
              render: (node) => <PodResourcesChips resources={node.resources} />,
            },
            {
              id: 'proxies',
              label: 'Proxies',
              align: 'center',
              render: (node) => <NeutralValueBadge>{node.overview.total_proxies}</NeutralValueBadge>,
            },
            {
              id: 'servers',
              label: 'Servers',
              align: 'center',
              render: (node) => <NeutralValueBadge>{node.overview.total_servers}</NeutralValueBadge>,
            },
            {
              id: 'up',
              label: 'UP',
              align: 'center',
              render: (node) => (
                <Chip label={node.overview.servers_up} size="small" color="success" variant="outlined" />
              ),
            },
            {
              id: 'down',
              label: 'DOWN',
              align: 'center',
              render: (node) => (
                <Chip
                  label={node.overview.servers_down}
                  size="small"
                  color={node.overview.servers_down > 0 ? 'error' : 'default'}
                  variant="outlined"
                />
              ),
            },
            {
              id: 'sessions',
              label: 'Sessions',
              align: 'center',
              render: (node) => (
                <InfoTooltip title="Текущие сессии frontend этого endpoint (cur/limit)">
                  <Box component="span" sx={{ display: 'inline-flex' }}>
                    <Chip
                      label={formatSessionsRange(node.totalSessions, node.totalSessionsLimit)}
                      size="small"
                      variant="outlined"
                    />
                    <Chip
                      label={formatSessionsPercent(node.totalSessions, node.totalSessionsLimit)}
                      size="small"
                      color={getUsageColor(node.totalSessions, node.totalSessionsLimit)}
                      variant="outlined"
                    />
                  </Box>
                </InfoTooltip>
              ),
            },
            {
              id: 'bytesIn',
              label: 'Bytes In',
              align: 'center',
              render: (node) => <NeutralValueBadge>{formatBytes(node.totalBytesIn)}</NeutralValueBadge>,
            },
            {
              id: 'bytesOut',
              label: 'Bytes Out',
              align: 'center',
              render: (node) => <NeutralValueBadge>{formatBytes(node.totalBytesOut)}</NeutralValueBadge>,
            },
            {
              id: 'uptime',
              label: 'Uptime',
              align: 'center',
              render: (node) => {
                const podUptime = getPodUptimeSeconds(node.podStartTime)
                const value = podUptime ?? node.minUptimeSeconds
                const title = podUptime !== undefined
                  ? 'Время с запуска Kubernetes pod'
                  : 'Время с последнего изменения состояния HAProxy endpoint'
                return (
                  <InfoTooltip title={title}>
                    <Box component="span" sx={{ display: 'inline-flex' }}>
                      <NeutralValueBadge>{formatDuration(value)}</NeutralValueBadge>
                    </Box>
                  </InfoTooltip>
                )
              },
            },
          ]}
          rows={endpointNodesSorted}
          getRowId={(node) => node.id}
          expandable={{
            renderExpanded: (node) => (
              <Box>
                {renderProxiesTable(node.proxies, node.id)}
              </Box>
            ),
            getExpandTooltip: (node) => `Показать прокси и серверы для ${node.label}`,
          }}
        />
        </MonitorNodesSection>
      </Box>
      {showOperationsPanel && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            Operations
          </Typography>
          <Paper elevation={0} sx={paperSx}>
            {operationsQuery.isLoading && (
              <Box sx={{ mb: 1 }}>
                <LinearProgress />
              </Box>
            )}
            {operationsQuery.error && (
              <Alert severity="warning" sx={{ mb: 1 }}>
                Не удалось загрузить журнал операций.
              </Alert>
            )}
            {!operationsQuery.isLoading && !operationsQuery.error && (operationsQuery.data || []).length === 0 && (
              <Typography variant="body2" color="text.secondary">
                Пока нет операций для отображения.
              </Typography>
            )}
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.25 }}>
              {[...(operationsQuery.data || [])]
                .sort((a, b) => {
                  const aRecent = isCompletedInLast20s(a) ? 1 : 0
                  const bRecent = isCompletedInLast20s(b) ? 1 : 0
                  if (bRecent !== aRecent) return bRecent - aRecent
                  return (new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
                })
                .slice(0, 5)
                .map((operation) => (
                <Box key={operation.operation_id} sx={{ borderBottom: '1px dashed', borderColor: 'divider', pb: 1 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                    <OperationStatusChip status={operation.status} progress={operation.progress} />
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {getOperationTypeLabel(operation.type)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {operation.pod_name || operation.deployment_name || operation.target_name}
                    </Typography>
                    {operation.replacement_pod_name && (
                      <Typography variant="caption" color="success.main" sx={{ fontWeight: 500 }}>
                        New pod: {operation.replacement_pod_name}
                      </Typography>
                    )}
                    <Typography variant="caption" color="text.secondary">
                      {operation.message}
                    </Typography>
                  </Box>
                  <OperationTimeline title="" events={operation.timeline} />
                </Box>
              ))}
            </Box>
          </Paper>
        </Box>
      )}
      <Dialog
        open={Boolean(restartConfirmation)}
        onClose={closeRestartConfirmation}
        maxWidth="xs"
        fullWidth
        PaperProps={{
          sx: {
            border: '1px solid',
            borderColor: visual.accentColor,
            boxShadow: 'none',
          },
        }}
      >
        <DialogTitle sx={{ color: visual.accentColor }}>
          Подтвердить рестарт
        </DialogTitle>
        <DialogContent>
          {restartConfirmation?.kind === 'rollout'
            ? `Выполнить мягкий rollout restart всех pod-ов для ${targetName}?`
            : `Перезапустить pod ${restartConfirmation?.podName} для target ${targetName}?`}
        </DialogContent>
        <DialogActions>
          <Button
            onClick={closeRestartConfirmation}
            sx={{
              color: visual.accentColor,
              '&:hover': { backgroundColor: visual.accentSoftBg },
            }}
          >
            Отмена
          </Button>
          <Button
            variant="contained"
            onClick={submitRestartConfirmation}
            sx={{
              backgroundColor: visual.accentColor,
              '&:hover': {
                backgroundColor: visual.accentColor,
                filter: 'brightness(0.95)',
              },
            }}
          >
            Подтвердить
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

export default HAProxyView
