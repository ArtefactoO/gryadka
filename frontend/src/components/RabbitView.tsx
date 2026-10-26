import MonitorStatusBadge from './MonitorStatusBadge'
import MonitorQueryState from './MonitorQueryState'
import MonitorNodesSection from './MonitorNodesSection'
import { UiIcon } from "@project/ui/mui"
import { useEffect, useMemo, useState } from 'react'
import {
  Box,
  Chip,
  Typography,
  Alert,
  CircularProgress,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  TextField,
  TablePagination,
  Divider,
} from '@mui/material'
import { Trash2 as DeleteOutlineIcon, Eraser as DeleteSweepIcon, ExternalLink as OpenInNewIcon } from "@project/ui/icons"
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { SxProps, Theme } from '@mui/material/styles'

import { rabbitApi, type RabbitCluster, type RabbitListener, type RabbitNode, type RabbitQueue } from '../services/api'
import { useUiTheme } from "@project/ui/mui"
import NeutralValueBadge from './NeutralValueBadge'
import { formatNumber, formatBytes, formatRate, formatUptime } from '../utils/metricFormat'
import { formatUpdateTime } from '../utils/formatUpdateTime'
import { InfoControl, InfoTooltip } from "@project/ui/mui"
import RabbitUsersSection from './RabbitUsersSection'
import { DataTable } from "@project/ui/mui"
import { IDENTITY_CONTENT_SX } from "@project/ui/mui"
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import MonitorSections from './MonitorSections'
import InfrastructureUsage, { hasInfrastructure, InfrastructureIndicator } from './InfrastructureUsage'
import { useAuth } from '../hooks/useAuth'
import { invalidateTargetQueries } from '../utils/queryInvalidation'
import { applyQueueActionSnapshot, rabbitQueueKey } from '../utils/rabbitQueueState'
import { getApiErrorMessage } from '../utils/apiError'

interface RabbitViewProps {
  targetName: string
  targetType?: string
}

type RabbitQueueAction = 'purge' | 'delete'

interface RabbitQueueActionState {
  action: RabbitQueueAction
  targetName: string
  queue: RabbitQueue
}

const DELETED_QUEUE_HIDE_MS = 60_000

const formatListener = (listener: RabbitListener): string => {
  const protocol = listener.protocol || 'tcp'
  return `${protocol}:${listener.port}`
}

const getListenersTooltip = (listeners?: RabbitListener[]): string => {
  if (listeners && listeners.length > 0) {
    return listeners
      .map((listener) => {
        const protocol = listener.protocol || 'tcp'
        const address = listener.ip_address || '0.0.0.0'
        return `${protocol}: ${address}:${listener.port}`
      })
      .join('\n')
  }
  return 'Listeners недоступны в RabbitMQ API.'
}

const getMessagesChipSx = (messages: number) => {
  if (messages < 10_000) return {}
  const color = messages >= 100_000 ? 'error.main' : 'warning.main'
  return { color, borderColor: color, backgroundColor: 'transparent' }
}

const queueMatchesSearch = (queue: RabbitQueue, searchTerms: string[]) => {
  if (searchTerms.length === 0) return true
  const searchable = [
    queue.name,
    queue.vhost || '/',
    queue.state || '',
    queue.type || '',
    queue.durable ? 'durable' : 'transient',
    queue.auto_delete ? 'auto_delete' : '',
    queue.exclusive ? 'exclusive' : '',
    `consumers:${queue.consumers}`,
    `ready:${queue.messages_ready}`,
    `unacked:${queue.messages_unacknowledged}`,
    `total:${queue.messages}`,
    `type:${queue.type || ''}`,
    `pub:${formatRate(queue.publish_rate)}`,
    `deliver:${formatRate(queue.deliver_get_rate)}`,
    `redeliver:${formatRate(queue.redeliver_rate)}`,
    String(queue.consumers),
    String(queue.messages),
    String(queue.messages_ready),
    String(queue.messages_unacknowledged),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()

  return searchTerms.every((term) => searchable.includes(term))
}

const RabbitView = ({ targetName, targetType = 'rabbitmq' }: RabbitViewProps) => {
  const { visual } = useUiTheme()
  const queryClient = useQueryClient()
  const { hasPermission, isAuthenticated } = useAuth()
  const [rabbitTab, setRabbitTab] = useState(0)
  const [queueSearchQuery, setQueueSearchQuery] = useState('')
  const [queuePage, setQueuePage] = useState(0)
  const [queueNameConfirmation, setQueueNameConfirmation] = useState('')
  const [queueAction, setQueueAction] = useState<RabbitQueueActionState | null>(null)
  const [queueActionError, setQueueActionError] = useState<string | null>(null)
  const [hiddenQueueKeys, setHiddenQueueKeys] = useState<Set<string>>(() => new Set())
  const canPurgeMessages = hasPermission('rabbitmq.messages.purge')
  const canDeleteQueues = hasPermission('rabbitmq.queues.delete')
  const canViewUsers = hasPermission('rabbitmq.users.read')
  const queueSearchTerms = useMemo(
    () => queueSearchQuery.trim().toLowerCase().split(/\s+/).filter(Boolean),
    [queueSearchQuery],
  )

  const { data, isLoading, error } = useQuery({
    queryKey: ['rabbitmq-target', targetName],
    queryFn: () => rabbitApi.getTarget(targetName),
    refetchInterval: 30000,
    staleTime: 30_000,
  })

  const invalidateRabbitTarget = (name = targetName) => {
    invalidateTargetQueries(queryClient, 'rabbitmq', name)
  }

  const queueKey = rabbitQueueKey

  const openQueueAction = (action: RabbitQueueAction, queue: RabbitQueue) => {
    setQueueActionError(null)
    setQueueNameConfirmation('')
    setQueueAction({ action, queue, targetName })
  }

  const queueActionMutation = useMutation({
    mutationFn: ({ action, queue, targetName: actionTarget }: RabbitQueueActionState) => {
      const payload = { name: queue.name, vhost: queue.vhost }
      return action === 'purge'
        ? rabbitApi.purgeQueue(actionTarget, payload)
        : rabbitApi.deleteQueue(actionTarget, payload)
    },
    onMutate: async () => {
      setQueueActionError(null)
      await queryClient.cancelQueries({ queryKey: ['rabbitmq-target', targetName] })
    },
    onSuccess: (_result, variables) => {
      if (variables.targetName === targetName) setQueueAction(null)
      const affectedQueueKey = variables.targetName + ":" + queueKey(variables.queue)
      const shouldHideDeletedQueue = variables.action === 'delete' && variables.targetName === targetName
      if (shouldHideDeletedQueue) {
        setHiddenQueueKeys((current) => {
          const next = new Set(current)
          next.add(affectedQueueKey)
          return next
        })
      }
      queryClient.setQueryData<RabbitCluster>(['rabbitmq-target', variables.targetName], current =>
        current ? applyQueueActionSnapshot(current, variables.queue, variables.action) : current)
      const refresh = () => invalidateRabbitTarget(variables.targetName)
      refresh()
      window.setTimeout(refresh, 2500)
      window.setTimeout(refresh, 8000)
      if (shouldHideDeletedQueue) {
        window.setTimeout(() => {
          setHiddenQueueKeys((current) => {
            if (!current.has(affectedQueueKey)) return current
            const next = new Set(current)
            next.delete(affectedQueueKey)
            return next
          })
        }, DELETED_QUEUE_HIDE_MS)
      }
    },
    onError: (err: unknown, variables: RabbitQueueActionState) => {
      if (variables.targetName === targetName) setQueueActionError(getApiErrorMessage(err, 'Не удалось выполнить действие с очередью. Проверьте права учетной записи.'))
    },
  })

  useEffect(() => {
    setQueueSearchQuery(''); setQueuePage(0); setQueueAction(null)
    setQueueNameConfirmation(''); setHiddenQueueKeys(new Set())
  }, [targetName])

  if (!data) return <MonitorQueryState loading={isLoading} error={error} hasData={false} label="RabbitMQ" />

  const queuesWithLocalOverlay = data.queues
    .filter((queue) => !hiddenQueueKeys.has(targetName + ":" + queueKey(queue)))
  const visibleQueues = queuesWithLocalOverlay.filter(queue => queueMatchesSearch(queue, queueSearchTerms))
    .sort((a, b) => b.messages - a.messages || a.name.localeCompare(b.name))
  const visibleQueuePage = Math.min(queuePage, Math.max(0, Math.ceil(visibleQueues.length / 25) - 1))
  const uniquePlugins = Array.from(
    new Set(
      data.nodes
        .flatMap((node) => node.enabled_plugins || [])
        .map((plugin) => plugin.trim())
        .filter((plugin) => plugin.length > 0)
    )
  ).sort()
  const rabbitVersions = data.nodes
    .map((node) => node.rabbitmq_version)
    .filter((version): version is string => Boolean(version))
  const overviewRabbitVersion = data.overview.rabbitmq_version
  const rabbitVersionLabel =
    rabbitVersions.length === 0 && !overviewRabbitVersion
      ? null
      : rabbitVersions.length === 0 && overviewRabbitVersion
        ? `RabbitMQ Version: ${overviewRabbitVersion}`
        : new Set(rabbitVersions).size === 1
        ? `RabbitMQ Version: ${rabbitVersions[0]}`
        : 'RabbitMQ Version mismatch'
  const erlangVersions = data.nodes
    .map((node) => node.erlang_version)
    .filter((version): version is string => Boolean(version))
  const overviewErlangVersion = data.overview.erlang_version
  const erlangVersionLabel =
    erlangVersions.length === 0 && !overviewErlangVersion
      ? null
      : erlangVersions.length === 0 && overviewErlangVersion
        ? `Erlang Version: ${overviewErlangVersion}`
        : new Set(erlangVersions).size === 1
          ? `Erlang Version: ${erlangVersions[0]}`
          : 'Erlang Version mismatch'

  const overviewMetrics: Array<{
    label: string
    value: number | string
    tooltip: string
    variant?: 'filled' | 'outlined'
    sx?: SxProps<Theme>
  }> = [
    {
      label: 'Messages',
      value: data.overview.messages,
      tooltip: 'Всего сообщений в очередях (queue_totals.messages из /api/overview).',
      variant: data.overview.messages >= 10_000 ? 'filled' : 'outlined',
      sx: getMessagesChipSx(data.overview.messages),
    },
    {
      label: 'Ready',
      value: data.overview.messages_ready,
      tooltip: 'Сообщения в готовности к доставке (queue_totals.messages_ready).',
    },
    {
      label: 'Unacked',
      value: data.overview.messages_unacknowledged,
      tooltip: 'Неподтверждённые сообщения (queue_totals.messages_unacknowledged).',
    },
    {
      label: 'Queues',
      value: data.overview.queues,
      tooltip: 'Количество очередей (object_totals.queues).',
    },
    {
      label: 'Consumers',
      value: data.overview.consumers,
      tooltip: 'Активные потребители (текущее значение из /api/consumers).',
    },
    {
      label: 'Connections',
      value: data.overview.connections,
      tooltip: 'Количество подключений (object_totals.connections).',
    },
    {
      label: 'Channels',
      value: data.overview.channels_running ?? data.overview.channels,
      tooltip: 'Каналы: сумма по узлам в состоянии RUNNING (/api/nodes) или object_totals.channels.',
    },
    {
      label: 'Publish',
      value: formatRate(data.overview.publish_rate),
      tooltip: 'Скорость публикации сообщений: publish_details.rate из /api/overview.',
    },
    {
      label: 'Deliver',
      value: formatRate(data.overview.deliver_get_rate),
      tooltip: 'Скорость доставки/получения сообщений: deliver_get_details.rate из /api/overview.',
    },
    {
      label: 'Ack',
      value: formatRate(data.overview.ack_rate),
      tooltip: 'Скорость подтверждения сообщений: ack_details.rate из /api/overview.',
    },
    {
      label: 'Redeliver',
      value: formatRate(data.overview.redeliver_rate),
      tooltip: 'Скорость повторной доставки сообщений. Ненулевое значение может указывать на проблемы у consumers.',
      variant: (data.overview.redeliver_rate ?? 0) > 0 ? 'filled' : 'outlined',
      sx: (data.overview.redeliver_rate ?? 0) > 0
        ? {
            backgroundColor: 'var(--sl-warn)',
            borderColor: 'var(--sl-warn)',
            color: 'var(--sl-panel)',
            '& .MuiChip-label': { color: 'var(--sl-panel)' },
          }
        : undefined,
    },
    {
      label: 'Exchanges',
      value: data.overview.exchanges,
      tooltip: 'Количество объектов Exchange (object_totals.exchanges).',
    },
  ]

  const rabbitVersionChips = (
    <>
      {rabbitVersionLabel && (
        <Chip
          label={rabbitVersionLabel}
          size="small"
          color={rabbitVersionLabel.includes('Version mismatch') ? 'warning' : 'secondary'}
          variant="outlined"
          sx={{
            color: rabbitVersionLabel.includes('Version mismatch') ? undefined : visual.accentColor,
            borderColor: rabbitVersionLabel.includes('Version mismatch') ? undefined : visual.accentColor,
          }}
        />
      )}
      {erlangVersionLabel && (
        <Chip
          label={erlangVersionLabel}
          size="small"
          color={erlangVersionLabel.includes('Version mismatch') ? 'warning' : 'secondary'}
          variant="outlined"
          sx={{
            ml: 1,
            mt: { xs: 1, sm: 0 },
            color: erlangVersionLabel.includes('Version mismatch') ? undefined : visual.accentColor,
            borderColor: erlangVersionLabel.includes('Version mismatch') ? undefined : visual.accentColor,
          }}
        />
      )}
    </>
  )

  const rabbitActionButtons = data.ui_url ? (
    <InfoTooltip title="Открыть RabbitMQ Management UI в новой вкладке">
      <Button
        size="small"
        variant="outlined"
        component="a"
        href={data.ui_url}
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
        Открыть RabbitMQ
      </Button>
    </InfoTooltip>
  ) : null

  return (
    <Box>
      {error && <Alert severity="warning">Не удалось обновить данные. Показан последний полученный результат.</Alert>}
      <VersionAndUpdateBlock
        versionChips={rabbitVersionChips}
        actionButtons={rabbitActionButtons ?? undefined}
        updateTime={data.last_update ? formatUpdateTime(data.last_update) : null}
        refreshTarget={{ targetType, targetName }}
        accentColor={visual.accentColor}
        accentSoftBg={visual.accentSoftBg}
      />
      <MonitorSections labels={['Overview', 'Queues', 'Users']} value={rabbitTab} onChange={setRabbitTab} label="rabbitmq-sections">
          {rabbitTab === 0 && (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
              {overviewMetrics.map((metric) => (
                <InfoTooltip key={metric.label} title={metric.tooltip}>
                  <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                    <Typography component="span" variant="body2" color="text.secondary">
                      {metric.label}:
                    </Typography>
                    <Chip
                      label={typeof metric.value === 'number' ? formatNumber(metric.value) : (metric.value ?? 'N/A')}
                      size="small"
                      variant={metric.variant || 'outlined'}
                      sx={metric.sx}
                    />
                  </Box>
                </InfoTooltip>
              ))}
              {data.overview.listeners && data.overview.listeners.length > 0 && (
                <Box sx={{ mt: 1, width: '100%', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.75 }}>
                  <InfoTooltip title={getListenersTooltip(data.overview.listeners)}>
                    <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600, mr: 0.25, cursor: 'help' }}>
                      Listeners:
                    </Typography>
                  </InfoTooltip>
                  {data.overview.listeners.map((listener) => (
                    <InfoTooltip
                      key={`${listener.protocol || 'tcp'}:${listener.ip_address || '*'}:${listener.port}`}
                      title={`${listener.protocol || 'tcp'}: ${listener.ip_address || '0.0.0.0'}:${listener.port}`}
                    >
                      <Box component="span">
                        <NeutralValueBadge>{formatListener(listener)}</NeutralValueBadge>
                      </Box>
                    </InfoTooltip>
                  ))}
                </Box>
              )}
              {uniquePlugins.length > 0 && (
                <Box sx={{ mt: 1, width: '100%', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 0.75 }}>
                  <InfoTooltip title="Включённые плагины по всем узлам RabbitMQ (/api/nodes enabled_plugins).">
                    <Typography variant="body2" color="text.secondary" sx={{ fontWeight: 600, mr: 0.25 }}>
                      Plugins:
                    </Typography>
                  </InfoTooltip>
                  {uniquePlugins.map((plugin) => (
                    <Chip key={plugin} label={plugin} size="small" variant="outlined" />
                  ))}
                </Box>
              )}
            </Box>
          )}
          {rabbitTab === 1 && <Box sx={{ display: 'grid', gap: 2 }}>
            <TextField label="Найти очередь или VHost" size="small" value={queueSearchQuery}
              onChange={event => { setQueueSearchQuery(event.target.value); setQueuePage(0) }}
              sx={{ width: { xs: '100%', sm: 360 }, maxWidth: '100%' }} />
            {data.overview.queues > data.queues.length && <Alert severity="info">
              В обзоре доступно очередей: {data.queues.length} из {data.overview.queues}. Для полного списка откройте интерфейс RabbitMQ.
            </Alert>}
            <DataTable<RabbitQueue> embedded title="Queues" rows={visibleQueues.slice(visibleQueuePage * 25, (visibleQueuePage + 1) * 25)} getRowId={queueKey} columns={[
              { id: 'queue', label: 'Queue', render: queue => queue.name },
              { id: 'vhost', label: 'VHost', render: queue => queue.vhost || '/' },
              { id: 'type', label: 'Type', render: queue => queue.type || 'N/A' },
              { id: 'state', label: 'State', render: queue => queue.state || 'N/A' },
              { id: 'consumers', label: 'Consumers', align: 'right', render: queue => formatNumber(queue.consumers) },
              { id: 'ready', label: <InfoControl label="Пояснение к Ready" description="Сообщения, готовые к доставке. Только они удаляются при очистке сообщений.">Ready</InfoControl>, align: 'right', render: queue => formatNumber(queue.messages_ready) },
              { id: 'unacked', label: <InfoControl label="Пояснение к Unacked" description="Сообщения уже выданы потребителям и ожидают подтверждения. Очистка Ready их не удаляет.">Unacked</InfoControl>, align: 'right', render: queue => formatNumber(queue.messages_unacknowledged) },
              { id: 'total', label: 'Total', align: 'right', render: queue => formatNumber(queue.messages) },
              { id: 'publish', label: 'Publish/s', align: 'right', render: queue => formatRate(queue.publish_rate) },
              { id: 'deliver', label: 'Deliver/s', align: 'right', render: queue => formatRate(queue.deliver_get_rate) },
              { id: 'actions', label: 'Actions', hidden: !canPurgeMessages && !canDeleteQueues, isIdentityColumn: false,
                render: queue => <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                  {canPurgeMessages && <InfoTooltip describeChild title={queue.messages_ready > 0 ? 'Удалить готовые сообщения (Ready). Очередь и Unacked сохранятся.' : 'Нет готовых к доставке сообщений. Unacked уже выданы потребителям.'}>
                    <span><Button size="small" color="warning" variant="outlined" startIcon={<UiIcon glyph={DeleteSweepIcon} size={16} />}
                      disabled={queueActionMutation.isPending || queue.messages_ready === 0}
                      onClick={() => openQueueAction('purge', queue)}>Удалить сообщения</Button></span>
                  </InfoTooltip>}
                  {canPurgeMessages && canDeleteQueues && <Divider orientation="vertical" flexItem />}
                  {canDeleteQueues && <InfoTooltip describeChild title="Удалить саму очередь вместе с ее сообщениями и привязками. Потребители потеряют очередь.">
                    <Button size="small" color="error" variant="outlined" startIcon={<UiIcon glyph={DeleteOutlineIcon} size={16} />}
                      disabled={queueActionMutation.isPending} onClick={() => openQueueAction('delete', queue)}>Удалить очередь</Button>
                  </InfoTooltip>}
                </Box> },
            ]} expandable={{ renderExpanded: queue => <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
              <Typography variant="body2">RAM: {formatBytes(queue.memory_bytes)}</Typography>
              <Typography variant="body2">Durable: {queue.durable ? 'Yes' : 'No'}</Typography>
              <Typography variant="body2">Auto-delete: {queue.auto_delete ? 'Yes' : 'No'}</Typography>
              <Typography variant="body2">Exclusive: {queue.exclusive ? 'Yes' : 'No'}</Typography>
              <Typography variant="body2">Redeliver/s: {formatRate(queue.redeliver_rate)}</Typography>
            </Box> }} />
            <TablePagination component="div" count={visibleQueues.length} page={visibleQueuePage} rowsPerPage={25} rowsPerPageOptions={[25]}
              onPageChange={(_, page) => setQueuePage(page)} labelRowsPerPage="Строк на странице"
              getItemAriaLabel={type => ({ first: 'Первая страница', last: 'Последняя страница', next: 'Следующая страница', previous: 'Предыдущая страница' })[type]}
              labelDisplayedRows={({ from, to, count }) => `${from}–${to} из ${count}`} />
            {!visibleQueues.length && <Alert severity="info">{queueSearchTerms.length ? 'Очереди по этому запросу не найдены.' : 'Очереди не обнаружены.'}</Alert>}
          </Box>}
          {rabbitTab === 2 &&
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
                {isAuthenticated ? 'Недостаточно прав для просмотра пользователей.' : 'Войдите, чтобы просмотреть пользователей.'}
              </Alert>
            ) : data.users && data.users.length > 0 ? (
              <RabbitUsersSection users={data.users} />
            ) : (
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
                Пользователи не найдены или мониторинговой учетке недоступен их список.
              </Alert>
            ))}
      </MonitorSections>


      <Dialog
        open={Boolean(queueAction)}
        onClose={() => {
          if (!queueActionMutation.isPending) {
            setQueueAction(null)
            setQueueActionError(null)
          }
        }}
        maxWidth="md"
        fullWidth
        PaperProps={{
          sx: {
            width: 'fit-content',
            maxWidth: 'calc(100vw - 48px)',
            minWidth: { xs: 'calc(100vw - 32px)', sm: 520 },
          },
        }}
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
          {queueAction?.action === 'delete' ? 'Удалить очередь?' : 'Удалить сообщения?'}
        </DialogTitle>
        <DialogContent sx={{ pt: 2 }}>
          <Typography variant="body2" sx={{ mb: 1.5 }}>
            {queueAction?.action === 'delete'
              ? 'Очередь, ее сообщения и привязки будут удалены. Потребители потеряют очередь. Отменить удаление нельзя.'
              : 'Будут удалены только сообщения Ready. Очередь, ее привязки и сообщения Unacked сохранятся. Отменить удаление сообщений нельзя.'}
          </Typography>
          <Box sx={{ display: 'grid', gap: 0.75, mb: 1.5 }}>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" sx={{ width: 72, flexShrink: 0 }}>
                VHost
              </Typography>
              <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>
                {queueAction?.queue.vhost || '/'}
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" sx={{ width: 72, flexShrink: 0 }}>
                Queue
              </Typography>
              <Typography
                variant="body2"
                sx={{
                  minWidth: 0,
                  maxWidth: 'calc(100vw - 180px)',
                  overflowX: 'auto',
                  whiteSpace: 'nowrap',
                  pb: 0.25,
                }}
              >
                {queueAction?.queue.name || 'N/A'}
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" sx={{ width: 72, flexShrink: 0 }}>
                Messages
              </Typography>
              <Typography variant="body2">
                {queueAction?.queue.messages ?? 0}
              </Typography>
            </Box>
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', minWidth: 0 }}>
              <Typography variant="caption" color="text.secondary" sx={{ width: 72, flexShrink: 0 }}>
                Consumers
              </Typography>
              <Typography variant="body2">
                {queueAction?.queue.consumers ?? 0}
              </Typography>
            </Box>
          </Box>
          {queueAction?.action === 'delete' && <TextField fullWidth autoComplete="off" label="Введите имя очереди для подтверждения"
            value={queueNameConfirmation} onChange={event => setQueueNameConfirmation(event.target.value)}
            disabled={queueActionMutation.isPending} sx={{ mb: 2 }} />}
          <Typography variant="caption" color="text.secondary">
            Данные обновятся автоматически.
          </Typography>
          {queueActionError ? (
            <Alert severity="error" sx={{ mt: 2 }}>
              {queueActionError}
            </Alert>
          ) : null}
        </DialogContent>
        <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1, px: 2, pb: 2, pt: 0 }}>
          <Button
            onClick={() => {
              setQueueAction(null)
              setQueueActionError(null)
            }}
            variant="text"
            size="small"
            disabled={queueActionMutation.isPending}
            sx={{ color: 'text.secondary' }}
          >
            Отмена
          </Button>
          <Button
            onClick={() => {
              if (queueAction) queueActionMutation.mutate(queueAction)
            }}
            variant="contained"
            size="small"
            disabled={queueActionMutation.isPending || !queueAction || (queueAction.action === 'delete' && queueNameConfirmation !== queueAction.queue.name)}
            color={queueAction?.action === 'delete' ? 'error' : 'warning'}
            aria-busy={queueActionMutation.isPending}
            startIcon={queueActionMutation.isPending ? <CircularProgress size={16} color="inherit" /> : undefined}
            sx={{ boxShadow: 'none' }}
          >
            {queueAction?.action === 'delete' ? 'Удалить очередь' : 'Удалить сообщения'}
          </Button>
        </Box>
      </Dialog>

      <MonitorNodesSection empty={!data.nodes.length}>
      <DataTable<RabbitNode>
        title="Nodes" titleAdornment={<InfrastructureIndicator nodes={data.nodes} />}
        columns={[
          {
            id: 'host',
            label: 'Host',
            isIdentityColumn: true,
            render: (node) => {
              const nodeAddress = node.name.includes('@') ? node.name.split('@')[1] : node.name
              const hostName = nodeAddress.includes(':') ? nodeAddress.split(':')[0] : nodeAddress
              return <Typography component="span" sx={IDENTITY_CONTENT_SX}>{hostName}</Typography>
            },
          },
          {
            id: 'util',
            label: 'Resources', hidden: !data.nodes.some(hasInfrastructure),
            align: 'center',
            render: (node) => <InfrastructureUsage node={node} />,
          },
          {
            id: 'status',
            label: 'Status',
            align: 'center',
            render: (node) => {
              return <MonitorStatusBadge status={node.status || (node.running ? 'RUNNING' : 'UNREACHABLE')}
                description={node.status_reason || undefined} />
            },
          },
          {
            id: 'uptime',
            label: 'Uptime',
            align: 'center',
            render: (node) => <NeutralValueBadge>{formatUptime(node.uptime_seconds)}</NeutralValueBadge>,
          },
        ]}
        rows={data.nodes}
        getRowId={(n) => n.name}
        expandable={{
          renderExpanded: (node) => {
            const hasSocketsData =
              (node.sockets_used !== undefined && node.sockets_used > 0) ||
              (node.sockets_total !== undefined && node.sockets_total > 0)
            const hasPartitions = Boolean(node.partitions && node.partitions.length > 0)
            const hasClusterLinks = Boolean((node.cluster_links_count ?? 0) > 0)
            const hasClusterPeers = Boolean(node.cluster_link_peers && node.cluster_link_peers.length > 0)
            return (
              <Box sx={{ display: 'grid', gap: 0.85 }}>
                {node.role != null && (
                  <InfoTooltip title="Роль RabbitMQ node: DISC хранит metadata на диске, RAM держит metadata в памяти. Для кворума кластера важны DISC-ноды.">
                    <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content', cursor: 'help' }}>
                      Role:{' '}
                      <Chip
                        label={node.role}
                        size="small"
                        variant="outlined"
                        sx={{
                          ml: 0.5,
                          ...(node.role === 'MASTER' || node.role === 'leader'
                            ? { backgroundColor: visual.accentSoftBg, color: visual.accentColor, borderColor: visual.accentColor }
                            : { borderColor: 'divider' }),
                        }}
                      />
                    </Typography>
                  </InfoTooltip>
                )}
                <Typography variant="body2" color="text.secondary">
                  RabbitMQ version:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{node.rabbitmq_version || 'N/A'}</NeutralValueBadge>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Erlang version:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{node.erlang_version || 'N/A'}</NeutralValueBadge>
                </Typography>
                {(node.mem_used !== undefined || node.mem_limit !== undefined) && (
                  <InfoTooltip title="Service RAM RabbitMQ из management API. Ресурсы pod или ВМ показываются отдельно в Resources.">
                    <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                      Service RAM:{' '}
                      <NeutralValueBadge sx={{ ml: 0.5 }}>
                        {formatBytes(node.mem_used)}
                        {node.mem_limit != null ? ` / ${formatBytes(node.mem_limit)}` : ''}
                      </NeutralValueBadge>
                    </Typography>
                  </InfoTooltip>
                )}
                <InfoTooltip title="File descriptors: сколько файловых дескрипторов использует RabbitMQ node и какой лимит доступен. При упоре в лимит могут ломаться соединения и операции с файлами.">
                  <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content', cursor: 'help' }}>
                    FD:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>
                      {node.fd_used ?? 'N/A'}
                      {node.fd_total ? ` / ${node.fd_total}` : ''}
                    </NeutralValueBadge>
                  </Typography>
                </InfoTooltip>
                {hasSocketsData && (
                  <Typography variant="body2" color="text.secondary">
                    Sockets:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>
                      {node.sockets_used ?? 'N/A'}
                      {node.sockets_total ? ` / ${node.sockets_total}` : ''}
                    </NeutralValueBadge>
                  </Typography>
                )}
                {hasPartitions && (
                  <Typography variant="body2" color="text.secondary">
                    Partitions:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{node.partitions?.join(', ')}</NeutralValueBadge>
                  </Typography>
                )}
                {hasClusterLinks && (
                  <Typography variant="body2" color="text.secondary">
                    Cluster links:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{node.cluster_links_count}</NeutralValueBadge>
                  </Typography>
                )}
                {hasClusterPeers && (
                  <Typography variant="body2" color="text.secondary">
                    Cluster peers:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{node.cluster_link_peers?.join(', ')}</NeutralValueBadge>
                  </Typography>
                )}
              </Box>
            )
          },
        }}
      />
      </MonitorNodesSection>
    </Box>
  )
}

export default RabbitView
