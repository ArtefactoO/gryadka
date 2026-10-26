import { UiIcon } from "@project/ui/mui"
import { useState, useEffect, useMemo } from 'react'
import {
  Box,
  Paper,
  Typography,
  TextField,
  Chip,
  IconButton,
  Alert,
  CircularProgress,
  LinearProgress,
  Tooltip,
} from '@mui/material'
import { Play as PromoteIcon, Square as AbortIcon, RotateCcw as RestartIcon, ChevronDown as ExpandIcon, ChevronUp as CollapseIcon } from "@project/ui/icons"
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { argoRolloutApi } from '../services/api'
import { subscribeSse } from '../services/sse'
import type { ArgoRolloutPodState, RolloutInfo, DeploymentInfo } from '../types/argo_rollout'
import { getPodBoxSx, isNewPod } from '../utils/podStyles'
import { useAuth } from '../hooks/useAuth'
import TargetRefreshButton from './TargetRefreshButton'

interface ArgoRolloutViewProps {
  targetName: string
  accentColor: string
  namespace?: string
}

type SortableStatus = 'Progressing' | 'Degraded' | 'Paused' | 'Healthy' | string

const getStatusPriority = (status: SortableStatus): number => {
  switch (status) {
    case 'Progressing': return 1
    case 'Degraded': return 2
    case 'Paused': return 3
    case 'Healthy': return 4
    default: return 5
  }
}

const actionIconButtonSx = (color: string) => ({
  p: 0.25,
  width: 26,
  height: 26,
  minWidth: 26,
  flex: '0 0 26px',
  borderRadius: '50%',
  color,
  border: '1px solid',
  borderColor: color,
})

const ArgoRolloutView = ({ targetName, accentColor, namespace }: ArgoRolloutViewProps) => {
  const queryClient = useQueryClient()
  const { hasPermission } = useAuth()
  const [searchTerm, setSearchTerm] = useState('')
  const canPromoteRollout = hasPermission('argo_rollout.promote')
  const canRestartRollout = hasPermission('argo_rollout.restart')

  // Determine view type from target name (case-insensitive check)
  const viewType = targetName.toLowerCase() === 'deployment' ? 'deployments' : 'rollouts'
  const [operationLoading, setOperationLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set())
  const [, setRolloutDetails] = useState<Record<string, RolloutInfo>>({})
  const [, setDeploymentDetails] = useState<Record<string, DeploymentInfo>>({})
  const [rolloutPodStates, setRolloutPodStates] = useState<Record<string, ArgoRolloutPodState[]>>({})
  const [deploymentPodStates, setDeploymentPodStates] = useState<Record<string, ArgoRolloutPodState[]>>({})
  const [detailLoading, setDetailLoading] = useState<Record<string, boolean>>({})
  const [, setTick] = useState(0)

  // Fetch rollouts data - don't pass namespace, let backend determine it from config
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['argo-rollouts', targetName],
    queryFn: () => argoRolloutApi.getRollouts(targetName),
    refetchInterval: 10000,
    staleTime: 10_000,
  })

  // Subscribe to SSE for real-time updates
  useEffect(() => {
    const unsubscribe = subscribeSse(
      {
        targetType: 'argo_rollout',
        event: 'argo_rollout.status_changed',
      },
      () => {
        queryClient.invalidateQueries({ queryKey: ['argo-rollouts', targetName] })
      }
    )
    return () => unsubscribe()
  }, [targetName, queryClient])

  useEffect(() => {
    const unsubscribe = subscribeSse(
      {
        targetType: 'argo_rollout',
        event: 'argo_rollout.k8s.changed',
      },
      (message) => {
        const workloadName = typeof message.workload_name === 'string' ? message.workload_name : ''
        const workloadKind = typeof message.workload_kind === 'string' ? message.workload_kind : ''
        const podsState = Array.isArray(message.pods_state) ? (message.pods_state as ArgoRolloutPodState[]) : null
        if (!workloadName || !workloadKind || !podsState) return
        if (workloadKind === 'rollout') {
          setRolloutPodStates((current) => ({ ...current, [workloadName]: podsState }))
          return
        }
        if (workloadKind === 'deployment') {
          setDeploymentPodStates((current) => ({ ...current, [workloadName]: podsState }))
        }
      }
    )
    return () => unsubscribe()
  }, [])

  useEffect(() => {
    const hasAnimatedPods =
      Object.values(rolloutPodStates).some((pods) => pods?.some((pod) => pod.start_time)) ||
      Object.values(deploymentPodStates).some((pods) => pods?.some((pod) => pod.start_time))
    if (!hasAnimatedPods) return undefined
    const id = setInterval(() => setTick((value) => value + 1), 1000)
    const timeout = setTimeout(() => clearInterval(id), 5000)
    return () => {
      clearInterval(id)
      clearTimeout(timeout)
    }
  }, [rolloutPodStates, deploymentPodStates])

  // Sort and filter rollouts - 0/0 replicas go to the end
  const sortedRollouts = useMemo(() => {
    if (!data?.rollouts) return []
    return [...data.rollouts]
      .filter(r => r.name.toLowerCase().includes(searchTerm.toLowerCase()))
      .sort((a, b) => {
        // First sort by replica presence (0/0 goes to end)
        const aHasReplicas = a.desired_replicas > 0
        const bHasReplicas = b.desired_replicas > 0
        if (aHasReplicas !== bHasReplicas) {
          return aHasReplicas ? -1 : 1
        }
        // Then by status priority
        const priorityDiff = getStatusPriority(a.status) - getStatusPriority(b.status)
        if (priorityDiff !== 0) return priorityDiff
        return a.name.localeCompare(b.name)
      })
  }, [data?.rollouts, searchTerm])

  // Sort and filter deployments - 0/0 replicas go to the end
  const sortedDeployments = useMemo(() => {
    if (!data?.deployments) return []
    return [...data.deployments]
      .filter(d => d.name.toLowerCase().includes(searchTerm.toLowerCase()))
      .sort((a, b) => {
        // First sort by replica presence (0/0 goes to end)
        const aHasReplicas = a.replicas > 0
        const bHasReplicas = b.replicas > 0
        if (aHasReplicas !== bHasReplicas) {
          return aHasReplicas ? -1 : 1
        }
        return a.name.localeCompare(b.name)
      })
  }, [data?.deployments, searchTerm])

  const rolloutNames = useMemo(() => new Set((data?.rollouts || []).map((item) => item.name)), [data?.rollouts])
  const deploymentNames = useMemo(() => new Set((data?.deployments || []).map((item) => item.name)), [data?.deployments])

  useEffect(() => {
    // Prevent cross-tab stale expanded state (deployment names queried as rollouts and vice versa).
    setExpandedItems(new Set())
    setError(null)
  }, [targetName, viewType])

  // Toggle expanded item
  const toggleExpanded = (name: string) => {
    const newExpanded = new Set(expandedItems)
    if (newExpanded.has(name)) {
      newExpanded.delete(name)
    } else {
      newExpanded.add(name)
      if (viewType === 'rollouts') {
        const summary = data?.rollouts?.find((item) => item.name === name)
        if (summary) {
          setRolloutDetails((current) => {
            const existingDetail = current[name]
            return {
              ...current,
              [name]: existingDetail || summary,
            }
          })
        }
      } else {
        const summary = data?.deployments?.find((item) => item.name === name)
        if (summary) {
          setDeploymentDetails((current) => {
            const existingDetail = current[name]
            return {
              ...current,
              [name]: existingDetail || summary,
            }
          })
        }
      }
    }
    setExpandedItems(newExpanded)
  }

  const loadRolloutDetail = async (rolloutName: string) => {
    const loadingKey = `rollout:${rolloutName}`
    if (detailLoading[loadingKey]) return
    setDetailLoading((current) => ({ ...current, [loadingKey]: true }))
    try {
      const detail = await argoRolloutApi.getRolloutDetail(targetName, rolloutName, namespace)
      if (detail.k8s_pods?.length) {
        setRolloutPodStates((current) => current[rolloutName]?.length ? current : { ...current, [rolloutName]: detail.k8s_pods || [] })
      }
      setRolloutDetails((current) => {
        return {
          ...current,
          [rolloutName]: { ...detail, k8s_pods: undefined },
        }
      })
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Не удалось загрузить подробности Rollout.')
    } finally {
      setDetailLoading((current) => ({ ...current, [loadingKey]: false }))
    }
  }

  const loadDeploymentDetail = async (deploymentName: string) => {
    const loadingKey = `deployment:${deploymentName}`
    if (detailLoading[loadingKey]) return
    setDetailLoading((current) => ({ ...current, [loadingKey]: true }))
    try {
      const detail = await argoRolloutApi.getDeploymentDetail(targetName, deploymentName, namespace)
      if (detail.k8s_pods?.length) {
        setDeploymentPodStates((current) => current[deploymentName]?.length ? current : { ...current, [deploymentName]: detail.k8s_pods || [] })
      }
      setDeploymentDetails((current) => {
        return {
          ...current,
          [deploymentName]: { ...detail, k8s_pods: undefined },
        }
      })
    } catch (e: any) {
      setError(e.response?.data?.detail || 'Не удалось загрузить подробности Deployment.')
    } finally {
      setDetailLoading((current) => ({ ...current, [loadingKey]: false }))
    }
  }

  useEffect(() => {
    expandedItems.forEach((name) => {
      if (viewType === 'rollouts') {
        if (!rolloutNames.has(name)) return
        if (!rolloutPodStates[name]?.length && !detailLoading[`rollout:${name}`]) {
          void loadRolloutDetail(name)
        }
      } else {
        if (!deploymentNames.has(name)) return
        if (!deploymentPodStates[name]?.length && !detailLoading[`deployment:${name}`]) {
          void loadDeploymentDetail(name)
        }
      }
    })
  }, [expandedItems, viewType, rolloutPodStates, deploymentPodStates, detailLoading, rolloutNames, deploymentNames])

  useEffect(() => {
    if (expandedItems.size === 0) return undefined
    const id = setInterval(() => {
      expandedItems.forEach((name) => {
        if (viewType === 'rollouts') {
          if (!rolloutNames.has(name)) return
          if (!rolloutPodStates[name]?.length) {
            void loadRolloutDetail(name)
          }
        } else {
          if (!deploymentNames.has(name)) return
          if (!deploymentPodStates[name]?.length) {
            void loadDeploymentDetail(name)
          }
        }
      })
    }, 3000)
    return () => clearInterval(id)
  }, [expandedItems, viewType, targetName, namespace, rolloutPodStates, deploymentPodStates, rolloutNames, deploymentNames])

  // Handle operations
  const handlePromote = async (rollout: RolloutInfo) => {
    setOperationLoading(`promote-${rollout.name}`)
    setError(null)
    try {
      const result = await argoRolloutApi.promoteRollout(targetName, rollout.name, namespace)
      if (!result.success) setError(result.message)
      refetch()
    } catch (e: any) {
      setError(e.response?.status === 403 ? 'Недостаточно прав для этого действия.' : (e.response?.data?.detail || 'Не удалось продолжить развертывание.'))
    } finally {
      setOperationLoading(null)
    }
  }

  const handleAbort = async (rollout: RolloutInfo) => {
    setOperationLoading(`abort-${rollout.name}`)
    setError(null)
    try {
      const result = await argoRolloutApi.abortRollout(targetName, rollout.name, namespace)
      if (!result.success) setError(result.message)
      refetch()
    } catch (e: any) {
      setError(e.response?.status === 403 ? 'Недостаточно прав для этого действия.' : (e.response?.data?.detail || 'Не удалось прервать развертывание.'))
    } finally {
      setOperationLoading(null)
    }
  }

  const handlePromoteFull = async (rollout: RolloutInfo) => {
    setOperationLoading(`promote-full-${rollout.name}`)
    setError(null)
    try {
      const result = await argoRolloutApi.promoteFullRollout(targetName, rollout.name, namespace)
      if (!result.success) setError(result.message)
      refetch()
    } catch (e: any) {
      setError(e.response?.status === 403 ? 'Недостаточно прав для этого действия.' : (e.response?.data?.detail || 'Не удалось завершить развертывание без оставшихся этапов.'))
    } finally {
      setOperationLoading(null)
    }
  }

  const handleRetryRollout = async (rollout: RolloutInfo) => {
    setOperationLoading(`retry-${rollout.name}`)
    setError(null)
    try {
      const result = await argoRolloutApi.retryRollout(targetName, rollout.name, namespace)
      if (!result.success) setError(result.message)
      refetch()
    } catch (e: any) {
      setError(e.response?.status === 403 ? 'Недостаточно прав для этого действия.' : (e.response?.data?.detail || 'Не удалось повторить развертывание.'))
    } finally {
      setOperationLoading(null)
    }
  }

  const handleRestartRollout = async (rollout: RolloutInfo) => {
    setOperationLoading(`restart-${rollout.name}`)
    setError(null)
    try {
      const result = await argoRolloutApi.restartRollout(targetName, rollout.name, namespace)
      if (!result.success) setError(result.message)
      refetch()
    } catch (e: any) {
      setError(e.response?.status === 403 ? 'Недостаточно прав для этого действия.' : (e.response?.data?.detail || 'Не удалось запустить перезапуск.'))
    } finally {
      setOperationLoading(null)
    }
  }

  const handleRestart = async (deployment: DeploymentInfo) => {
    setOperationLoading(`restart-${deployment.name}`)
    setError(null)
    try {
      const result = await argoRolloutApi.restartDeployment(targetName, deployment.name, namespace)
      if (!result.success) setError(result.message)
      refetch()
    } catch (e: any) {
      setError(e.response?.status === 403 ? 'Недостаточно прав для этого действия.' : (e.response?.data?.detail || 'Не удалось запустить перезапуск.'))
    } finally {
      setOperationLoading(null)
    }
  }

  // Get status color for border
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'Progressing': return 'var(--sl-warn)'
      case 'Degraded': return 'var(--sl-danger)'
      case 'Paused': return 'var(--sl-accent)'
      case 'Healthy': return 'var(--sl-ok)'
      default: return accentColor
    }
  }

  const renderPodStates = (pods?: ArgoRolloutPodState[]) => {
    if (!pods?.length) {
      return (
        <Typography variant="caption" sx={{ fontSize: '0.7rem', color: 'text.secondary' }}>
          Нет данных по pod'ам
        </Typography>
      )
    }

    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        {pods.map((pod) => (
          <Box
            key={pod.uid || pod.name}
            sx={[
              {
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 1,
                px: 0.75,
                py: 0.5,
                borderRadius: 1,
                border: '1px solid',
                borderColor: 'divider',
              },
              getPodBoxSx(Boolean(isNewPod(pod.start_time)), Boolean(pod.terminating), accentColor, `${accentColor}14`) ?? {},
            ]}
          >
            <Typography variant="caption" sx={{ fontSize: '0.72rem', fontFamily: 'var(--sl-font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {pod.name}
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              {isNewPod(pod.start_time) ? <Chip label="new" size="small" variant="outlined" sx={{ height: 18, fontSize: '0.65rem' }} /> : null}
              <Chip
                label={pod.terminating ? 'Terminating' : (pod.ready ? 'Ready' : (pod.status_detail || pod.phase || 'Unknown'))}
                size="small"
                variant="outlined"
                sx={{ height: 18, fontSize: '0.65rem' }}
              />
            </Box>
          </Box>
        ))}
      </Box>
    )
  }

  const renderPodStatesBlock = (pods: ArgoRolloutPodState[] | undefined, isRefreshing: boolean) => {
    const hasPods = Boolean(pods?.length)
    if (!hasPods && isRefreshing) {
      return (
        <Typography variant="caption" sx={{ fontSize: '0.7rem', color: 'text.secondary' }}>
          Загрузка pod'ов...
        </Typography>
      )
    }

    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
        {renderPodStates(pods)}
        {hasPods && isRefreshing ? (
          <Typography variant="caption" sx={{ fontSize: '0.68rem', color: 'text.secondary' }}>
            Обновление...
          </Typography>
        ) : null}
      </Box>
    )
  }

  // Compact card for rollout
  const renderRolloutCard = (rollout: RolloutInfo) => {
    const isExpanded = expandedItems.has(rollout.name)
    const progress = rollout.desired_replicas > 0
      ? (rollout.available_replicas / rollout.desired_replicas) * 100
      : 0
    const statusColor = getStatusColor(rollout.status)

    return (
      <Paper
        key={rollout.name}
        elevation={0}
        sx={{
          p: 1.5,
          border: '1px solid',
          borderColor: statusColor,
          borderRadius: 1,
          bgcolor: 'background.paper',
        }}
      >
        {/* Header with name and status on separate lines */}
        <Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
            <IconButton aria-label={`${isExpanded ? 'Свернуть' : 'Развернуть'} Rollout ${rollout.name}`} aria-expanded={isExpanded} size="small" onClick={() => toggleExpanded(rollout.name)} sx={{ p: 0.25 }}>
              {isExpanded ? <UiIcon glyph={CollapseIcon} size={16} /> : <UiIcon glyph={ExpandIcon} size={16} />}
            </IconButton>

            <Typography
              variant="body2"
              sx={{
                flex: 1,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontWeight: 500,
                color: statusColor,
              }}
            >
              {rollout.name}
            </Typography>
          </Box>

          {/* Status with replica count - left aligned with monitor accent color */}
          <Box sx={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', gap: 1, ml: 3.5 }}>
            <Chip
              label={`${rollout.status} • ${rollout.available_replicas}/${rollout.desired_replicas}`}
              size="small"
              sx={{
                height: 20,
                fontSize: '0.7rem',
                color: accentColor,
                borderColor: accentColor,
                border: '1px solid',
                bgcolor: 'transparent',
              }}
            />
          </Box>
        </Box>

        {/* Progress bar - only show if there are replicas */}
        {rollout.desired_replicas > 0 && (
          <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
            <LinearProgress
              variant="determinate"
              value={progress}
              sx={{
                flex: 1,
                height: 4,
                bgcolor: 'grey.200',
                '& .MuiLinearProgress-bar': { bgcolor: statusColor }
              }}
            />
          </Box>
        )}

        {/* Image */}
        {rollout.current_image && rollout.current_image !== 'unknown' && (
          <Typography
            variant="caption"
            sx={{
              fontSize: '0.65rem',
              fontFamily: 'var(--sl-font-mono)',
              color: 'text.secondary',
              display: 'block',
              mt: 0.5,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {rollout.current_image.split(':').pop()?.slice(0, 20)}
          </Typography>
        )}

        {/* Expanded - Pod info */}
        {isExpanded && (
          <Box sx={{ mt: 1.5, pt: 1.5, borderTop: '1px dashed', borderColor: 'divider' }}>
            {(canPromoteRollout || canRestartRollout) && (
              <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 0.5, mb: 1, flexWrap: 'wrap' }}>
                {canRestartRollout ? (
                  <Tooltip title="Перезапустить поды Rollout">
                    <IconButton aria-label="Перезапустить поды Rollout"
                      size="small"
                      onClick={() => handleRestartRollout(rollout)}
                      disabled={!!operationLoading}
                      sx={actionIconButtonSx(accentColor)}
                    >
                      {operationLoading === `restart-${rollout.name}` ? <CircularProgress size={14} sx={{ color: accentColor }} /> : <UiIcon glyph={RestartIcon} size={16} />}
                    </IconButton>
                  </Tooltip>
                ) : null}
                {canPromoteRollout && rollout.status === 'Paused' && (
                  <Tooltip title="Продолжить развертывание">
                    <IconButton aria-label="Продолжить развертывание"
                      size="small"
                      onClick={() => handlePromote(rollout)}
                      disabled={!!operationLoading}
                      sx={actionIconButtonSx(accentColor)}
                    >
                      {operationLoading === `promote-${rollout.name}` ? <CircularProgress size={14} sx={{ color: accentColor }} /> : <UiIcon glyph={PromoteIcon} size={16} />}
                    </IconButton>
                  </Tooltip>
                )}
                {canPromoteRollout && (rollout.status === 'Paused' || rollout.status === 'Progressing') && (
                  <Tooltip title="Завершить развертывание без оставшихся этапов">
                    <IconButton aria-label="Завершить развертывание без оставшихся этапов"
                      size="small"
                      onClick={() => handlePromoteFull(rollout)}
                      disabled={!!operationLoading}
                      sx={actionIconButtonSx(accentColor)}
                    >
                      {operationLoading === `promote-full-${rollout.name}` ? <CircularProgress size={14} sx={{ color: accentColor }} /> : <UiIcon glyph={PromoteIcon} size={16} />}
                    </IconButton>
                  </Tooltip>
                )}
                {canPromoteRollout && (rollout.status === 'Progressing' || rollout.status === 'Paused') && (
                  <Tooltip title="Прервать развертывание">
                    <IconButton aria-label="Прервать развертывание"
                      size="small"
                      onClick={() => handleAbort(rollout)}
                      disabled={!!operationLoading}
                      sx={actionIconButtonSx('var(--sl-danger)')}
                    >
                      {operationLoading === `abort-${rollout.name}` ? <CircularProgress size={14} color="error" /> : <UiIcon glyph={AbortIcon} size={16} />}
                    </IconButton>
                  </Tooltip>
                )}
                {canRestartRollout && rollout.status === 'Degraded' && (
                  <Tooltip title="Повторить развертывание">
                    <IconButton aria-label="Повторить развертывание"
                      size="small"
                      onClick={() => handleRetryRollout(rollout)}
                      disabled={!!operationLoading}
                      sx={actionIconButtonSx(accentColor)}
                    >
                      {operationLoading === `retry-${rollout.name}` ? <CircularProgress size={14} sx={{ color: accentColor }} /> : <UiIcon glyph={RestartIcon} size={16} />}
                    </IconButton>
                  </Tooltip>
                )}
              </Box>
            )}
            <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mb: 1 }}>
              <Chip label={rollout.strategy} size="small" variant="outlined" sx={{ height: 18, fontSize: '0.65rem' }} />
              {rollout.message && (
                <Chip label={rollout.message} size="small" sx={{ height: 18, fontSize: '0.65rem', color: 'var(--sl-warn)', borderColor: 'var(--sl-warn)', border: '1px solid', bgcolor: 'transparent' }} />
              )}
            </Box>

            <Typography variant="caption" sx={{ fontSize: '0.7rem' }}>
              Pods: {rollout.available_replicas} ready / {rollout.current_replicas} current / {rollout.desired_replicas} desired
            </Typography>

            <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
              {Array.from({ length: Math.min(rollout.desired_replicas, 30) }).map((_, i) => {
                const isReady = i < rollout.available_replicas
                return (
                  <Box
                    key={i}
                    sx={{ width: 8, height: 8, borderRadius: '2px', bgcolor: isReady ? 'var(--sl-ok)' : 'var(--sl-warn)' }}
                  />
                )
              })}
              {rollout.desired_replicas > 30 && (
                <Typography variant="caption" sx={{ fontSize: '0.6rem', color: 'text.secondary' }}>
                  +{rollout.desired_replicas - 30}
                </Typography>
              )}
            </Box>
            <Box sx={{ mt: 1 }}>
              {renderPodStatesBlock(
                rolloutPodStates[rollout.name],
                Boolean(detailLoading[`rollout:${rollout.name}`]),
              )}
            </Box>
          </Box>
        )}
      </Paper>
    )
  }

  // Compact card for deployment
  const renderDeploymentCard = (deployment: DeploymentInfo) => {
    const isExpanded = expandedItems.has(deployment.name)
    const progress = deployment.replicas > 0 ? (deployment.available_replicas / deployment.replicas) * 100 : 0

    return (
      <Paper
        key={deployment.name}
        elevation={0}
        sx={{
          p: 1.5,
          border: '1px solid',
          borderColor: accentColor,
          borderRadius: 1,
          bgcolor: 'background.paper',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
          <IconButton aria-label={`${isExpanded ? 'Свернуть' : 'Развернуть'} Deployment ${deployment.name}`} aria-expanded={isExpanded} size="small" onClick={() => toggleExpanded(deployment.name)} sx={{ p: 0.25 }}>
            {isExpanded ? <UiIcon glyph={CollapseIcon} size={16} /> : <UiIcon glyph={ExpandIcon} size={16} />}
          </IconButton>
          <Typography
            variant="body2"
            sx={{
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              fontWeight: 500,
              color: accentColor,
            }}
          >
            {deployment.name}
          </Typography>
        </Box>

        {/* Replica count chip - left aligned */}
        <Box sx={{ display: 'flex', justifyContent: 'flex-start', alignItems: 'center', gap: 1, mb: 0.5 }}>
          <Chip
            label={`${deployment.available_replicas}/${deployment.replicas}`}
            size="small"
            sx={{
              height: 20,
              fontSize: '0.7rem',
              color: accentColor,
              borderColor: accentColor,
              border: '1px solid',
              bgcolor: 'transparent',
            }}
          />
        </Box>

        {/* Progress bar - only show if there are replicas */}
        {deployment.replicas > 0 && (
          <Box sx={{ mt: 0.5, display: 'flex', alignItems: 'center', gap: 1 }}>
            <LinearProgress
              variant="determinate"
              value={progress}
              sx={{ flex: 1, height: 4, bgcolor: 'grey.200', '& .MuiLinearProgress-bar': { bgcolor: accentColor } }}
            />
          </Box>
        )}

        {deployment.current_image && deployment.current_image !== 'unknown' && (
          <Typography
            variant="caption"
            sx={{ fontSize: '0.65rem', fontFamily: 'var(--sl-font-mono)', color: 'text.secondary', display: 'block', mt: 0.5, overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {deployment.current_image.split(':').pop()?.slice(0, 20)}
          </Typography>
        )}
        {isExpanded && (
          <Box sx={{ mt: 1.5, pt: 1.5, borderTop: '1px dashed', borderColor: 'divider' }}>
            {canRestartRollout && (
              <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
                <Tooltip title="Перезапустить поды Deployment">
                  <IconButton aria-label="Перезапустить поды Deployment"
                    size="small"
                    onClick={() => handleRestart(deployment)}
                    disabled={!!operationLoading}
                    sx={actionIconButtonSx(accentColor)}
                  >
                    {operationLoading === `restart-${deployment.name}` ? <CircularProgress size={14} sx={{ color: accentColor }} /> : <UiIcon glyph={RestartIcon} size={16} />}
                  </IconButton>
                </Tooltip>
              </Box>
            )}
            {renderPodStatesBlock(
              deploymentPodStates[deployment.name],
              Boolean(detailLoading[`deployment:${deployment.name}`]),
            )}
          </Box>
        )}
      </Paper>
    )
  }

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 400 }}>
        <CircularProgress size={48} sx={{ color: accentColor }} />
      </Box>
    )
  }

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 2 }}>
      {/* Error */}
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Content based on viewType */}
      {viewType === 'rollouts' ? (
        <Paper elevation={0} sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Header with search */}
          <Box sx={{ p: 2, borderBottom: '1px solid', borderColor: accentColor }}>
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
              <TextField
                label="Поиск"
                size="small"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Найти Rollout"
                sx={{ width: 250 }}
              />
              <Box sx={{ ml: 'auto' }}>
                <TargetRefreshButton
                  targetType="argo-rollout"
                  targetName={targetName}
                  accentColor={accentColor}
                  accentSoftBg="transparent"
                />
              </Box>
            </Box>
          </Box>

          {/* Content */}
          <Box sx={{ p: 2, overflow: 'auto', flex: 1 }}>
            {sortedRollouts.length === 0 ? (
              <Typography color="text.secondary" align="center">
                Развертывания Rollout не найдены.
              </Typography>
            ) : (
              <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 1.5 }}>
                {sortedRollouts.map(renderRolloutCard)}
              </Box>
            )}
          </Box>
        </Paper>
      ) : (
        <Paper elevation={0} sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Header with search */}
          <Box sx={{ p: 2, borderBottom: '1px solid', borderColor: accentColor }}>
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
              <TextField
                label="Поиск"
                size="small"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Найти Deployment"
                sx={{ width: 250 }}
              />
              <Box sx={{ ml: 'auto' }}>
                <TargetRefreshButton
                  targetType="argo-rollout"
                  targetName={targetName}
                  accentColor={accentColor}
                  accentSoftBg="transparent"
                />
              </Box>
            </Box>
          </Box>

          {/* Content */}
          <Box sx={{ p: 2, overflow: 'auto', flex: 1 }}>
            {sortedDeployments.length === 0 ? (
              <Typography color="text.secondary" align="center">
                Развертывания Deployment не найдены.
              </Typography>
            ) : (
              <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 1.5 }}>
                {sortedDeployments.map(renderDeploymentCard)}
              </Box>
            )}
          </Box>
        </Paper>
      )}
    </Box>
  )
}

export default ArgoRolloutView
