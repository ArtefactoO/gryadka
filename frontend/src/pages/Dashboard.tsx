import { UiIcon } from "@project/ui/mui"
import { useState, useEffect, useRef, useMemo, lazy, Suspense } from 'react'
import {
  Box,
  Tabs,
  Tab,
  CircularProgress,
  Alert,
  IconButton,
  Button,
  Typography,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
} from '@mui/material'
import { Info as InfoOutlinedIcon } from "@project/ui/icons"
import { LogOut as LogoutIcon } from "@project/ui/icons"
import { LogIn as LoginIcon } from "@project/ui/icons"
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { QueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import {
  addressesApi,
  argoRolloutApi,
  clustersApi,
  elasticsearchApi,
  emqxApi,
  haproxyApi,
  metaApi,
  rabbitApi,
  redpandaApi,
  redisApi,
  postgresqlApi,
  kafkaApi,
  targetsApi,
  type MonitoringTarget,
} from '../services/api'
import { websocketService } from '../services/websocket'
import type { WebSocketMessage } from '../services/websocket'
import { useAuth } from '../hooks/useAuth'
import LoginModal from '../components/LoginModal'
const ClusterView = lazy(() => import('../components/ClusterView'))
const RabbitView = lazy(() => import('../components/RabbitView'))
const RedisView = lazy(() => import('../components/RedisView'))
const PostgresqlView = lazy(() => import('../components/PostgresqlView'))
const KafkaView = lazy(() => import('../components/KafkaView'))
const HAProxyView = lazy(() => import('../components/HAProxyView'))
const RedpandaView = lazy(() => import('../components/RedpandaView'))
const ElasticsearchView = lazy(() => import('../components/ElasticsearchView'))
const EmqxView = lazy(() => import('../components/EmqxView'))
const ArgoRolloutView = lazy(() => import('../components/ArgoRolloutView'))
const RegistryView = lazy(() => import('../components/RegistryView'))
import { targetTabsSx } from "@project/ui/mui"
import { useUiTheme } from "@project/ui/mui"
import SearchToggleInput from '../components/SearchToggleInput'
import MonitorQueryState from '../components/MonitorQueryState'
import { findDatabaseMatches } from '../utils/databaseSearch'
import { invalidateGlobalTargetQueries, invalidateTargetQueries, parseTargetId } from '../utils/queryInvalidation'

const TARGET_TYPE_LABEL: Record<string, string> = {
  mongodb: 'MongoDB',
  rabbitmq: 'RabbitMQ',
  redis: 'Redis',
  postgresql: 'PostgreSQL',
  kafka: 'Kafka',
  haproxy: 'HAProxy',
  redpanda: 'Redpanda',
  elasticsearch: 'Elasticsearch',
  kibana: 'Kibana',
  emqx: 'EMQX',
  addresses: 'Addresses',
  'argo-rollout': 'Rollouts',
}

const LOGGING_TYPES = ['redpanda', 'elasticsearch', 'kibana']
const PRIMARY_DATA_TYPES = ['mongodb', 'rabbitmq', 'redis', 'postgresql', 'kafka', 'haproxy', 'emqx', 'argo-rollout']
const ADDRESSES_TYPE = 'addresses'
const LOGGING_TYPE = 'logging'
const DETAIL_PREFETCH_STALE_TIME_MS = 30_000
const DETAIL_PREFETCH_BATCH_SIZE = 2
const DETAIL_PREFETCH_BATCH_DELAY_MS = 120
const CHANGELOG_QUERY_PARAM = 'changelog'

const buildTopLevelTypes = (targets: MonitoringTarget[]) => {
  const primaryTypes = PRIMARY_DATA_TYPES.filter((type) =>
    targets.some((target) => target.type === type && target.group !== 'logging')
  )
  const hasLogging = targets.some((target) => target.group === 'logging')
  const hasAddresses = targets.some((target) => target.type === ADDRESSES_TYPE)

  return [
    ...primaryTypes,
    ...(hasLogging ? [LOGGING_TYPE] : []),
    ...(hasAddresses ? [ADDRESSES_TYPE] : []),
  ]
}

const renderChangelogContent = (content?: string) => {
  const lines = (content || '').split(/\r?\n/)
  if (!content?.trim()) {
    return <Typography color="text.secondary">Changelog пока не заполнен.</Typography>
  }

  return (
    <Box sx={{ display: 'grid', gap: 0.6 }}>
      {lines.map((line, index) => {
        const trimmed = line.trim()
        if (!trimmed) {
          return <Box key={`blank-${index}`} sx={{ height: 6 }} />
        }
        if (!line.startsWith(' ') && !trimmed.startsWith('-')) {
          return (
            <Typography
              key={`${trimmed}-${index}`}
              variant={index === 0 ? 'subtitle1' : 'subtitle2'}
              sx={{ fontWeight: 700, mt: index === 0 ? 0 : 1 }}
            >
              {trimmed}
            </Typography>
          )
        }
        return (
          <Typography key={`${trimmed}-${index}`} variant="body2" color="text.secondary" sx={{ pl: 1.5 }}>
            {trimmed}
          </Typography>
        )
      })}
    </Box>
  )
}

const formatTargetTabLabel = (target: MonitoringTarget): string => {
  if (target.type === 'addresses' && target.name === 'addresses') {
    return 'Addresses'
  }
  return target.name
}

const targetSelectionStorageKey = (targetType: string) => `activeTarget:${targetType}`

const targetPriority = (
  target: MonitoringTarget,
  selectedTargetId: string,
  selectedType: string,
  selectedLoggingSubType: string,
) => {
  const selectedConcreteType = selectedType === 'logging' ? selectedLoggingSubType : selectedType
  if (target.id === selectedTargetId) return 0
  if (target.type === selectedConcreteType) return 1
  if (target.group === 'logging') return 3
  return 2
}

const prefetchTargetDetail = (queryClient: QueryClient, target: MonitoringTarget) => {
  const commonOptions = { staleTime: DETAIL_PREFETCH_STALE_TIME_MS }

  if (target.type === 'mongodb') {
    return queryClient.prefetchQuery({
      queryKey: ['cluster', target.name],
      queryFn: () => clustersApi.getCluster(target.name),
      ...commonOptions,
    })
  }
  if (target.type === 'rabbitmq') {
    return queryClient.prefetchQuery({
      queryKey: ['rabbitmq-target', target.name],
      queryFn: () => rabbitApi.getTarget(target.name),
      ...commonOptions,
    })
  }
  if (target.type === 'kafka') {
    return queryClient.prefetchQuery({queryKey: ['kafka-target', target.name], queryFn: () => kafkaApi.getTarget(target.name), ...commonOptions})
  }
  if (target.type === 'postgresql') {
    return queryClient.prefetchQuery({queryKey: ['postgresql-target', target.name], queryFn: () => postgresqlApi.getTarget(target.name), ...commonOptions})
  }
  if (target.type === 'redis') {
    return queryClient.prefetchQuery({
      queryKey: ['redis-target', target.name],
      queryFn: () => redisApi.getTarget(target.name),
      ...commonOptions,
    })
  }
  if (target.type === 'haproxy') {
    return queryClient.prefetchQuery({
      queryKey: ['haproxy-target', target.name],
      queryFn: () => haproxyApi.getTarget(target.name),
      staleTime: 5_000,
    })
  }
  if (target.type === 'redpanda') {
    return queryClient.prefetchQuery({
      queryKey: ['redpanda-target', target.name],
      queryFn: () => redpandaApi.getTarget(target.name),
      ...commonOptions,
    })
  }
  if (target.type === 'elasticsearch') {
    return queryClient.prefetchQuery({
      queryKey: ['elasticsearch-target', target.name],
      queryFn: () => elasticsearchApi.getTarget(target.name),
      ...commonOptions,
    })
  }
  if (target.type === 'emqx') {
    return queryClient.prefetchQuery({
      queryKey: ['emqx-target', target.name],
      queryFn: () => emqxApi.getTarget(target.name),
      ...commonOptions,
    })
  }
  if (target.type === 'addresses') {
    return queryClient.prefetchQuery({
      queryKey: ['addresses', target.name],
      queryFn: () => addressesApi.getAddresses(),
      staleTime: 60_000,
    })
  }
  if (target.type === 'argo-rollout') {
    return queryClient.prefetchQuery({
      queryKey: ['argo-rollouts', target.name],
      queryFn: () => argoRolloutApi.getRollouts(target.name, target.namespace),
      staleTime: 10_000,
    })
  }

  return Promise.resolve()
}

const Dashboard = () => {
  const { username, logout, isAuthenticated } = useAuth()
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const [loginModalOpen, setLoginModalOpen] = useState(false)
  const [changelogOpen, setChangelogOpen] = useState(() => searchParams.get(CHANGELOG_QUERY_PARAM) === '1')
  const [selectedType, setSelectedType] = useState<string>('')
  const [selectedLoggingSubType, setSelectedLoggingSubType] = useState<string>('')
  const [selectedTargetId, setSelectedTargetId] = useState<string>('')
  const [targets, setTargets] = useState<MonitoringTarget[]>([])
  const [dbSearchQuery, setDbSearchQuery] = useState('')
  const [dbSearchOpen, setDbSearchOpen] = useState(false)
  const [dbSearchFilterEnabled, setDbSearchFilterEnabled] = useState(false)
  const [highlightDatabaseName, setHighlightDatabaseName] = useState<string | null>(null)
  const [highlightTargetId, setHighlightTargetId] = useState<string | null>(null)
  const isDatabaseMonitor = selectedType === 'mongodb' || selectedType === 'postgresql'
  const skipNextSyncRef = useRef(false)

  const { data: targetsList, isLoading, isError, refetch } = useQuery({
    queryKey: ['targets'],
    queryFn: () => targetsApi.getTargets(),
    refetchInterval: 30_000,
    retry: 3,
    retryDelay: 1000,
    staleTime: 15_000, // Consider data fresh so background refetch doesn't flash loading
  })

  const { data: databasesOverview = {} } = useQuery({
    queryKey: ['databases-overview', selectedType],
    queryFn: () => selectedType === 'postgresql' ? postgresqlApi.getDatabasesOverview() : clustersApi.getDatabasesOverview(),
    enabled: isDatabaseMonitor,
    refetchInterval: 30_000,
    staleTime: 30_000,
  })
  const { data: appMeta } = useQuery({
    queryKey: ['app-meta'],
    queryFn: () => metaApi.getAppMeta(),
    staleTime: Infinity,
  })
  const { data: changelog, isLoading: changelogLoading } = useQuery({
    queryKey: ['app-changelog'],
    queryFn: () => metaApi.getChangelog(),
    enabled: changelogOpen,
    staleTime: Infinity,
  })

  useEffect(() => {
    setChangelogOpen(searchParams.get(CHANGELOG_QUERY_PARAM) === '1')
  }, [searchParams])

  // Avoid flashing "Загрузка конфигурации" on fast responses; show only after delay
  const [showLoadingMessage, setShowLoadingMessage] = useState(false)
  useEffect(() => {
    if (!isLoading) {
      setShowLoadingMessage(false)
      return
    }
    const t = setTimeout(() => setShowLoadingMessage(true), 700)
    return () => clearTimeout(t)
  }, [isLoading])

  useEffect(() => {
    if (targetsList) {
      setTargets(targetsList)
      if (!selectedType && targetsList.length > 0) {
        const typesForTopTab = buildTopLevelTypes(targetsList)
        const saved = localStorage.getItem('activeTargetTypeTab')
        setSelectedType(
          saved && typesForTopTab.includes(saved) ? saved : typesForTopTab[0]
        )
      }
    }
  }, [targetsList, selectedType])

  // WebSocket connection and updates
  useEffect(() => {
    if (!isAuthenticated) {
      websocketService.disconnect()
      return undefined
    }

    websocketService.connect()

    const invalidateSelectedTarget = () => {
      const currentTarget = targets.find((target) => target.id === selectedTargetId)
      if (currentTarget) {
        invalidateTargetQueries(queryClient, currentTarget.type, currentTarget.name)
        return
      }
      invalidateGlobalTargetQueries(queryClient)
    }

    const handleDataUpdate = (message: WebSocketMessage) => {
      const targetRefs: Array<{ targetType: string; targetName: string }> = []

      message.targets?.forEach((target) => {
        targetRefs.push({
          targetType: target.target_type,
          targetName: target.target_name,
        })
      })

      message.target_ids?.forEach((targetId) => {
        const parsed = parseTargetId(targetId)
        if (parsed) targetRefs.push(parsed)
      })

      message.clusters?.forEach((clusterOrTargetId) => {
        const parsed = parseTargetId(clusterOrTargetId)
        targetRefs.push(parsed || { targetType: 'mongodb', targetName: clusterOrTargetId })
      })

      if (targetRefs.length === 0) {
        invalidateSelectedTarget()
        return
      }

      const seen = new Set<string>()
      targetRefs.forEach(({ targetType, targetName }) => {
        const key = `${targetType}:${targetName}`
        if (seen.has(key)) return
        seen.add(key)
        invalidateTargetQueries(queryClient, targetType, targetName)
      })
    }

    const handleConnected = () => {
      // Immediate sync after reconnect to recover from missed backend notifications.
      invalidateSelectedTarget()
    }

    const handleDisconnected = () => {
      invalidateGlobalTargetQueries(queryClient)
    }

    websocketService.on('data_updated', handleDataUpdate)
    websocketService.on('connected', handleConnected)
    websocketService.on('disconnected', handleDisconnected)

    return () => {
      websocketService.off('data_updated', handleDataUpdate)
      websocketService.off('connected', handleConnected)
      websocketService.off('disconnected', handleDisconnected)
    }
  }, [isAuthenticated, queryClient, selectedTargetId, targets])

  const loggingTargets = useMemo(() => targets.filter((t) => t.group === 'logging'), [targets])
  const loggingSubTypes = useMemo(
    () => Array.from(new Set(loggingTargets.map((t) => t.type))).sort(
      (a, b) => LOGGING_TYPES.indexOf(a) - LOGGING_TYPES.indexOf(b)
    ),
    [loggingTargets]
  )

  const pickFirstTargetId = (targetType: string) => {
    return targets.find((target) => target.type === targetType)?.id ?? ''
  }

  const selectFirstTargetForType = (targetType: string) => {
    const nextTargetId = pickFirstTargetId(targetType)
    setSelectedTargetId(nextTargetId)
    if (nextTargetId) {
      localStorage.setItem(targetSelectionStorageKey(targetType), nextTargetId)
    }
  }

  const handleTypeTabChange = (_event: React.SyntheticEvent, newValue: string) => {
    setSelectedType(newValue)
    localStorage.setItem('activeTargetTypeTab', newValue)
    if (newValue === 'logging') {
      const subType = (() => {
        const saved = localStorage.getItem('activeLoggingSubType')
        return saved && loggingSubTypes.includes(saved) ? saved : loggingSubTypes[0] ?? ''
      })()
      setSelectedLoggingSubType(subType)
      if (subType) {
        selectFirstTargetForType(subType)
      } else {
        setSelectedTargetId('')
      }
    } else {
      selectFirstTargetForType(newValue)
    }
  }

  const handleLoggingSubTypeChange = (_event: React.SyntheticEvent, newValue: string) => {
    setSelectedLoggingSubType(newValue)
    localStorage.setItem('activeLoggingSubType', newValue)
    selectFirstTargetForType(newValue)
  }

  const handleTargetTabChange = (_event: React.SyntheticEvent, newValue: string) => {
    setSelectedTargetId(newValue)
    const target = targets.find((item) => item.id === newValue)
    if (target) {
      localStorage.setItem(targetSelectionStorageKey(target.type), target.id)
    }
  }

  // Apply type/target from URL (e.g. after clicking unreachable chip). Do not clear URL so restore effects skip.
  useEffect(() => {
    const type = searchParams.get('type')
    const target = searchParams.get('target')
    if (!type || !target || targets.length === 0) return
    const targetId = `${type}:${target}`
    const exists = targets.some((t) => t.id === targetId)
    if (!exists) return
    skipNextSyncRef.current = true // prevent sync effect from overwriting URL this cycle
    if (LOGGING_TYPES.includes(type)) {
      setSelectedType('logging')
      setSelectedLoggingSubType(type)
      setSelectedTargetId(targetId)
      localStorage.setItem(targetSelectionStorageKey(type), targetId)
    } else {
      setSelectedType(type)
      setSelectedTargetId(targetId)
      localStorage.setItem(targetSelectionStorageKey(type), targetId)
    }
  }, [searchParams, targets])

  // Restore active type tab from localStorage only when the current selection is empty or invalid.
  // A targets refetch must not override a valid tab the user is already viewing.
  useEffect(() => {
    if (searchParams.get('type') && searchParams.get('target')) return
    const typesForTopTab = buildTopLevelTypes(targets)
    if (typesForTopTab.length === 0) {
      return
    }

    if (selectedType && typesForTopTab.includes(selectedType)) {
      return
    }

    const savedType = localStorage.getItem('activeTargetTypeTab')
    if (savedType && typesForTopTab.includes(savedType)) {
      setSelectedType(savedType)
      return
    }

    setSelectedType(typesForTopTab[0])
  }, [targets, selectedType, searchParams])

  // When Logging selected, init or reset sub-type (skip when URL has type+target)
  useEffect(() => {
    if (searchParams.get('type') && searchParams.get('target')) return
    if (selectedType === 'logging' && loggingSubTypes.length > 0) {
      const saved = localStorage.getItem('activeLoggingSubType')
      const next =
        saved && loggingSubTypes.includes(saved)
          ? saved
          : loggingSubTypes[0]
      setSelectedLoggingSubType(next)
    } else {
      setSelectedLoggingSubType('')
    }
  }, [selectedType, loggingSubTypes.join(','), searchParams])

  // Keep selected target consistent: always first in list when current selection invalid (no restore from localStorage)
  useEffect(() => {
    if (searchParams.get('type') && searchParams.get('target')) return
    if (!selectedType) {
      setSelectedTargetId('')
      return
    }

    const typeForTargets =
      selectedType === 'logging' ? selectedLoggingSubType : selectedType
    const targetsForType = targets.filter((target) => target.type === typeForTargets)
    if (targetsForType.length === 0) {
      setSelectedTargetId('')
      return
    }

    if (!targetsForType.some((target) => target.id === selectedTargetId)) {
      const stored = localStorage.getItem(targetSelectionStorageKey(typeForTargets))
      const nextTargetId =
        stored && targetsForType.some((target) => target.id === stored)
          ? stored
          : targetsForType[0].id
      setSelectedTargetId(nextTargetId)
      localStorage.setItem(targetSelectionStorageKey(typeForTargets), nextTargetId)
    }
  }, [targets, selectedType, selectedLoggingSubType, selectedTargetId, searchParams])

  // Sync URL with current tab so bookmark/refresh and manual tab change stay in sync.
  // Skip one run after "apply from URL" so we don't overwrite unreachable-click URL with stale state.
  const selectedTargetForUrl = targets.find((t) => t.id === selectedTargetId)
  useEffect(() => {
    if (skipNextSyncRef.current) {
      skipNextSyncRef.current = false
      return
    }
    if (!selectedTargetForUrl) return
    const type = selectedType === 'logging' ? selectedLoggingSubType : selectedType
    if (searchParams.get('type') === type && searchParams.get('target') === selectedTargetForUrl.name) return
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set('type', type)
    nextParams.set('target', selectedTargetForUrl.name)
    setSearchParams(nextParams, { replace: true })
  }, [selectedTargetId, selectedType, selectedLoggingSubType, selectedTargetForUrl, searchParams, setSearchParams])

  const availableTypes = useMemo(() => buildTopLevelTypes(targets), [targets])
  const targetsForSelectedType = useMemo(
    () => selectedType === 'logging'
      ? targets.filter((target) => target.type === selectedLoggingSubType)
      : targets.filter((target) => target.type === selectedType),
    [targets, selectedType, selectedLoggingSubType]
  )
  const selectedTarget = targets.find((target) => target.id === selectedTargetId)
  const selectedTypeValue = availableTypes.includes(selectedType) ? selectedType : availableTypes[0] || ''
  const { visual } = useUiTheme()
  const openChangelog = () => {
    const nextParams = new URLSearchParams(searchParams)
    const type = selectedType === 'logging' ? selectedLoggingSubType : selectedType
    if (type && selectedTarget) {
      nextParams.set('type', type)
      nextParams.set('target', selectedTarget.name)
    }
    nextParams.set(CHANGELOG_QUERY_PARAM, '1')
    setChangelogOpen(true)
    setSearchParams(nextParams)
  }
  const closeChangelog = () => {
    const nextParams = new URLSearchParams(searchParams)
    nextParams.delete(CHANGELOG_QUERY_PARAM)
    setChangelogOpen(false)
    setSearchParams(nextParams, { replace: true })
  }
  const selectedLoggingSubTypeValue =
    selectedType === 'logging' && selectedLoggingSubType && loggingSubTypes.includes(selectedLoggingSubType)
      ? selectedLoggingSubType
      : loggingSubTypes[0] || ''

  const loadingAlertSx = { maxWidth: 600 }

  const prefetchQueue = useMemo(() => {
    return [...targets].sort((a, b) => {
      const priorityDiff =
        targetPriority(a, selectedTargetId, selectedType, selectedLoggingSubType) -
        targetPriority(b, selectedTargetId, selectedType, selectedLoggingSubType)
      if (priorityDiff !== 0) return priorityDiff
      return a.id.localeCompare(b.id)
    })
  }, [targets, selectedTargetId, selectedType, selectedLoggingSubType])

  useEffect(() => {
    if (prefetchQueue.length === 0) return undefined

    let cancelled = false
    let timer: number | undefined

    const delay = (ms: number) => new Promise((resolve) => {
      timer = window.setTimeout(resolve, ms)
    })

    const runPrefetch = async () => {
      await delay(250)
      for (let index = 0; index < prefetchQueue.length && !cancelled; index += DETAIL_PREFETCH_BATCH_SIZE) {
        const batch = prefetchQueue.slice(index, index + DETAIL_PREFETCH_BATCH_SIZE)
        await Promise.allSettled(batch.map((target) => prefetchTargetDetail(queryClient, target)))
        if (!cancelled) {
          await delay(DETAIL_PREFETCH_BATCH_DELAY_MS)
        }
      }
    }

    void runPrefetch()

    return () => {
      cancelled = true
      if (timer !== undefined) {
        window.clearTimeout(timer)
      }
    }
  }, [prefetchQueue, queryClient])

  const dbSearchTrimmed = dbSearchQuery.trim().toLowerCase()
  const isDbFilterActive = isDatabaseMonitor && dbSearchFilterEnabled && dbSearchTrimmed.length > 0
  const databasesOverviewSafe = useMemo(() => {
    if (!databasesOverview || typeof databasesOverview !== 'object' || Array.isArray(databasesOverview)) {
      return {} as Record<string, string[]>
    }
    return databasesOverview as Record<string, string[]>
  }, [databasesOverview])

  const dbSearchMatches = useMemo(() => {
    return isDbFilterActive ? findDatabaseMatches(databasesOverviewSafe, dbSearchTrimmed) : []
  }, [databasesOverviewSafe, dbSearchTrimmed, isDbFilterActive])

  const targetsForTabs = useMemo(
    () => isDbFilterActive && dbSearchMatches.length > 0
      ? targetsForSelectedType.filter((target) =>
          dbSearchMatches.some((match) => match.targetName === target.name)
        )
      : targetsForSelectedType,
    [isDbFilterActive, dbSearchMatches, targetsForSelectedType]
  )
  const shouldShowTargetTabs = selectedType !== 'addresses'

  const selectedTargetValue = targetsForTabs.some((target) => target.id === selectedTargetId)
    ? selectedTargetId
    : (targetsForTabs[0]?.id || '')

  useEffect(() => {
    if (targetsForTabs.length === 0) return
    if (searchParams.get('type') && searchParams.get('target')) return
    if (!isDbFilterActive) return
    if (!targetsForTabs.some((target) => target.id === selectedTargetId)) {
      setSelectedTargetId(targetsForTabs[0].id)
    }
  }, [targetsForTabs, selectedTargetId, isDbFilterActive, searchParams])

  useEffect(() => {
    setDbSearchQuery('')
    setDbSearchOpen(false)
    setDbSearchFilterEnabled(false)
    setHighlightDatabaseName(null)
    setHighlightTargetId(null)
  }, [selectedType])

  if (isLoading) {
    return (
      <>
        <Box display="flex" flexDirection="column" justifyContent="center" alignItems="center" minHeight="400px" gap={2}>
          <CircularProgress size={48} sx={{ color: visual.accentColor }} />
          {showLoadingMessage && (
            <Box sx={{ textAlign: 'center', mt: 2 }}>
              <Alert severity="info" sx={loadingAlertSx}>
                Загрузка конфигурации мониторинга...
              </Alert>
            </Box>
          )}
        </Box>
        <LoginModal open={loginModalOpen} onClose={() => setLoginModalOpen(false)} />
      </>
    )
  }

  if (isError && !targetsList) {
    return (
      <>
        <Box display="flex" flexDirection="column" justifyContent="center" alignItems="center" minHeight="400px" gap={2}>
          <Alert severity="warning" sx={{ maxWidth: 640 }} action={<Button color="inherit" size="small" onClick={() => refetch()}>Повторить</Button>}>
            Не удалось загрузить список мониторов. Проверьте соединение и повторите запрос.
          </Alert>
        </Box>
        <LoginModal open={loginModalOpen} onClose={() => setLoginModalOpen(false)} />
      </>
    )
  }

  if (!targets || targets.length === 0) {
    return (
      <>
        <Box sx={{ display: 'grid', justifyItems: 'start', gap: 2, py: 5, maxWidth: '65ch' }} role="status">
          <Typography variant="h6" component="h1">Мониторы не подключены</Typography>
          <Typography variant="body1" color="text.secondary">
            Подготовьте учетные записи и подключения для MongoDB, RabbitMQ, Redis, PostgreSQL или Kafka.
            После настройки сервисы появятся здесь.
          </Typography>
          {isAuthenticated ? (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
              <Typography variant="body2" color="text.secondary">{username}</Typography>
              <Button size="small" startIcon={<UiIcon glyph={LogoutIcon} />} onClick={() => logout()}>Выйти</Button>
            </Box>
          ) : (
            <Button size="small" startIcon={<UiIcon glyph={LoginIcon} />} onClick={() => setLoginModalOpen(true)}>Войти</Button>
          )}
        </Box>
        <LoginModal open={loginModalOpen} onClose={() => setLoginModalOpen(false)} />
      </>
    )
  }

  return (
    <Box>
      {isError && <Alert severity="warning" sx={{ mb: 1.5 }}>Не удалось обновить список мониторов. Показаны последние полученные данные.</Alert>}
      {/* Top-level nav: menu block (grey) only wraps tabs; login is outside so hover stays on pills */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.25, flexWrap: 'wrap' }}>
        <Box
          sx={{
            flex: '0 0 auto',
            width: 'max-content',
            maxWidth: '100%',
            flexWrap: 'wrap',
            minWidth: 0,
            px: 1.25,
            py: 0.75,
            borderRadius: 1,
            backgroundColor: 'action.hover',
            borderBottom: 1,
            borderColor: 'divider',
            minHeight: 36,
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {availableTypes.map((targetType) => (
            <Button
              key={targetType}
              disableRipple
              aria-pressed={selectedTypeValue === targetType}
              onClick={(e) => handleTypeTabChange(e, targetType)}
              sx={{
                minHeight: 28,
                py: 0.25,
                px: 1.25,
                mr: 0.75,
                borderRadius: '999px',
                color: selectedTypeValue === targetType ? visual.accentColor : 'text.secondary',
                backgroundColor: selectedTypeValue === targetType ? visual.accentSoftBg : 'transparent',
                fontWeight: selectedTypeValue === targetType ? 600 : 500,
                textTransform: 'uppercase',
                fontSize: '0.8125rem',
                '&:hover': {
                  backgroundColor:
                    selectedTypeValue === targetType ? visual.accentSoftBg : 'action.hover',
                },
              }}
            >
              {TARGET_TYPE_LABEL[targetType] || targetType}
            </Button>
          ))}
        </Box>
        <Box sx={{ ml: 'auto', display: 'flex', alignItems: 'center', gap: 1 }}>
          <Tooltip title={`Версия: ${appMeta?.version || 'unknown'}. Нажмите, чтобы посмотреть changelog.`}>
            <IconButton
              size="small"
              sx={{ color: 'text.secondary' }}
              aria-label="Посмотреть версию и changelog"
              onClick={openChangelog}
            >
              <UiIcon glyph={InfoOutlinedIcon} size={16} />
            </IconButton>
          </Tooltip>
          {isAuthenticated && username && (
            <Typography variant="body2" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
              {username}
            </Typography>
          )}
          {isAuthenticated ? (
            <Tooltip title="Выйти">
              <IconButton size="small" onClick={() => logout()} sx={{ color: 'text.secondary' }}>
                <UiIcon glyph={LogoutIcon} size={16} />
              </IconButton>
            </Tooltip>
          ) : (
            <Tooltip title="Войти">
              <Button
                size="small"
                onClick={() => setLoginModalOpen(true)}
                startIcon={<UiIcon glyph={LoginIcon} size={16} />}
                sx={{ color: 'text.secondary', textTransform: 'none' }}
              >
                Login
              </Button>
            </Tooltip>
          )}
        </Box>
      </Box>
      <LoginModal open={loginModalOpen} onClose={() => setLoginModalOpen(false)} />
      <Dialog
        open={changelogOpen}
        onClose={closeChangelog}
        maxWidth="sm"
        fullWidth
        PaperProps={{ sx: { borderRadius: 1 } }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          Changelog {appMeta?.version ? `v${appMeta.version}` : ''}
        </DialogTitle>
        <DialogContent dividers>
          {changelogLoading ? (
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={24} />
            </Box>
          ) : (
            renderChangelogContent(changelog?.content)
          )}
        </DialogContent>
      </Dialog>

      {selectedType === 'logging' && loggingSubTypes.length > 0 && (
        <Tabs
          value={selectedLoggingSubTypeValue}
          onChange={handleLoggingSubTypeChange}
          variant="standard"
          scrollButtons={false}
          sx={{ ...targetTabsSx, mb: 1.75 }}
        >
          {loggingSubTypes.map((subType) => (
            <Tab key={subType} label={TARGET_TYPE_LABEL[subType] || subType} value={subType} />
          ))}
        </Tabs>
      )}
      {(isDatabaseMonitor || shouldShowTargetTabs) && (
        <Box
          sx={{
            display: 'inline-flex',
            alignItems: 'flex-start',
            gap: 0.75,
            flexWrap: 'nowrap',
            minHeight: 36,
            mb: 1.5,
            width: 'fit-content',
            maxWidth: '100%',
            borderBottom: 1,
            borderColor: 'divider',
            pb: 0.15,
          }}
        >
          {isDatabaseMonitor && (
            <SearchToggleInput
              value={dbSearchQuery}
              isOpen={dbSearchOpen}
              onValueChange={(nextValue) => {
                setDbSearchQuery(nextValue)
                setDbSearchFilterEnabled(nextValue.trim().length > 0)
              }}
              onOpenChange={setDbSearchOpen}
              onApply={() => setDbSearchFilterEnabled(dbSearchQuery.trim().length > 0)}
              onClear={() => {
                setDbSearchQuery('')
                setDbSearchFilterEnabled(false)
                setHighlightDatabaseName(null)
              }}
              accentColor={visual.accentColor}
              accentSoftBg={visual.accentSoftBg}
              inputSide="left"
              width={160}
              buttonOffsetY={0}
              searchButtonTooltip={`Поиск по базам ${TARGET_TYPE_LABEL[selectedType]}`}
            />
          )}
          {shouldShowTargetTabs ? (
            targetsForTabs.length > 0 ? (
            <Tabs
              aria-label="Мониторы"
              value={selectedTargetValue}
              onChange={handleTargetTabChange}
              variant="standard"
              scrollButtons={false}
              sx={{
                ...targetTabsSx,
                minWidth: 0,
                borderBottom: 0,
                mb: 0,
              }}
            >
              {targetsForTabs.map((target) => (
                <Tab
                  key={target.id}
                  value={target.id}
                  label={formatTargetTabLabel(target)}
                  onMouseEnter={() => { void prefetchTargetDetail(queryClient, target) }}
                  onFocus={() => { void prefetchTargetDetail(queryClient, target) }}
                />
              ))}
            </Tabs>
            ) : (
            <Typography variant="body2" color="text.secondary">
              Нет кластеров
            </Typography>
            )
          ) : null}
        </Box>
      )}

      {isDatabaseMonitor && isDbFilterActive && dbSearchMatches.length === 0 && (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>Базы по этому запросу не найдены.</Typography>
      )}
      {isDatabaseMonitor && isDbFilterActive && dbSearchMatches.length > 0 && (
        <Box sx={{ mb: 1.5, display: 'flex', flexWrap: 'wrap', gap: 0.5, alignItems: 'center' }}>
          <Typography variant="body2" color="text.secondary" sx={{ mr: 0.5 }}>
            Найдено:
          </Typography>
          {dbSearchMatches.slice(0, 30).map(({ targetName, dbName }) => (
            <Button
              key={`${targetName}:${dbName}`}
              size="small"
              variant="outlined"
              onClick={() => {
                const targetId = `${selectedType}:${targetName}`
                setSelectedTargetId(targetId)
                setHighlightTargetId(targetId)
                setHighlightDatabaseName(dbName)
                setDbSearchFilterEnabled(false)
                setDbSearchOpen(false)
                window.setTimeout(() => setHighlightDatabaseName(null), 3000)
              }}
              sx={{
                textTransform: 'none',
                fontSize: '0.8125rem',
                py: 0.25,
                px: 0.75,
                borderColor: 'divider',
                color: 'text.primary',
                '&:hover': {
                  borderColor: visual.accentColor,
                  color: visual.accentColor,
                },
              }}
            >
              {targetName} → {dbName}
            </Button>
          ))}
          {dbSearchMatches.length > 30 && (
            <Typography variant="caption" color="text.secondary">
              и ещё {dbSearchMatches.length - 30}
            </Typography>
          )}
        </Box>
      )}

      <Suspense fallback={<MonitorQueryState loading error={null} hasData={false} label="монитора" />}>
      {selectedTarget?.type === 'mongodb' ? (
        <ClusterView
          clusterName={selectedTarget.name}
          targetType={selectedTarget.type}
          highlightDatabaseName={highlightTargetId === selectedTarget.id ? highlightDatabaseName : null}
        />
      ) : selectedTarget?.type === 'rabbitmq' ? (
        <RabbitView targetName={selectedTarget.name} targetType={selectedTarget.type} />
      ) : selectedTarget?.type === 'kafka' ? (
        <KafkaView targetName={selectedTarget.name} />
      ) : selectedTarget?.type === 'postgresql' ? (
        <PostgresqlView targetName={selectedTarget.name} highlightDatabaseName={highlightTargetId === selectedTarget.id ? highlightDatabaseName : null} />
      ) : selectedTarget?.type === 'redis' ? (
        <RedisView targetName={selectedTarget.name} targetType={selectedTarget.type} />
      ) : selectedTarget?.type === 'haproxy' ? (
        <HAProxyView targetName={selectedTarget.name} targetType={selectedTarget.type} />
      ) : selectedTarget?.type === 'redpanda' ? (
        <RedpandaView targetName={selectedTarget.name} targetType={selectedTarget.type} />
      ) : selectedTarget?.type === 'elasticsearch' ? (
        <ElasticsearchView targetName={selectedTarget.name} targetType={selectedTarget.type} />
      ) : selectedTarget?.type === 'emqx' ? (
        <EmqxView targetName={selectedTarget.name} targetType={selectedTarget.type} />
      ) : selectedTarget?.type === 'addresses' ? (
        <RegistryView targetName={selectedTarget.name} targetType={selectedTarget.type} />
      ) : selectedTarget?.type === 'argo-rollout' ? (
        <ArgoRolloutView
          targetName={selectedTarget.name}
          accentColor={visual.accentColor}
        />
      ) : selectedTarget?.type === 'kibana' ? (
        <Alert severity="info">Kibana мониторинг пока не реализован.</Alert>
      ) : selectedTarget ? (
        <Alert severity="info">Монитор {selectedTarget.type} пока не поддерживается.</Alert>
      ) : targetsForSelectedType.length > 0 ? (
        <Alert severity="info">Выберите кластер</Alert>
      ) : null}
      </Suspense>
    </Box>
  )
}

export default Dashboard
