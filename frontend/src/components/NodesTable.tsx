import MonitorStatusBadge from './MonitorStatusBadge'
import { Box, Chip, Typography } from '@mui/material'
import { Node } from '../services/api'
import LagChart from './LagChart'
import { formatDistanceToNow } from 'date-fns'
import { ru } from 'date-fns/locale'
import { formatDateTimeShort } from '../utils/formatUpdateTime'
import { InfoTooltip } from "@project/ui/mui"
import NeutralValueBadge from './NeutralValueBadge'
import { DataTable, type DataTableColumn } from "@project/ui/mui"
import { IDENTITY_CONTENT_SX } from "@project/ui/mui"
import { getLagChipSx } from '../utils/statusChipStyles'
import InfrastructureUsage, { hasInfrastructure, InfrastructureIndicator } from './InfrastructureUsage'
import { formatUptime, formatBytes, formatNumber } from '../utils/metricFormat'

interface NodesTableProps {
  nodes: Node[]
  clusterName: string
  accentColor: string
  accentSoftBg: string
  sampleTimestamp?: string
  headerActions?: React.ReactNode
}

interface RolePresentation {
  label: string
  color: 'default' | 'primary' | 'error' | 'warning' | 'info'
  variant?: 'filled' | 'outlined'
  sx?: Record<string, string | number>
}

const NodesTable = ({ nodes, accentColor, accentSoftBg, headerActions }: NodesTableProps) => {
  const sortedNodes = nodes
    .map((node, index) => ({ node, index }))
    .sort((a, b) => {
      const aRank =
        a.node.stateStr === 'PRIMARY'
          ? 0
          : a.node.stateStr === 'SECONDARY' && a.node.priority === 0
            ? 2
            : 1
      const bRank =
        b.node.stateStr === 'PRIMARY'
          ? 0
          : b.node.stateStr === 'SECONDARY' && b.node.priority === 0
            ? 2
            : 1
      if (aRank !== bRank) {
        return aRank - bRank
      }
      return a.index - b.index
    })
    .map((item) => item.node)

  const isAnalyticNode = (node: Node): boolean => {
    return node.stateStr === 'SECONDARY' && node.priority === 0
  }

  const isUnreachableState = (stateStr: string): boolean => {
    return ['DOWN', 'UNKNOWN'].includes(stateStr)
  }

  const isStartupState = (stateStr: string): boolean => {
    return ['STARTUP', 'STARTUP2'].includes(stateStr)
  }

  const getRolePresentation = (node: Node): RolePresentation => {
    if (node.stateStr === 'PRIMARY') {
      return {
        label: 'PRIMARY',
        color: 'default',
        variant: 'outlined',
        sx: {
          backgroundColor: accentSoftBg,
          color: accentColor,
          borderColor: accentColor,
        },
      }
    }

    if (isAnalyticNode(node)) {
      return {
        label: 'ANALYTIC',
        color: 'default',
        variant: 'outlined',
        sx: {
          backgroundColor: accentSoftBg,
          color: accentColor,
          borderColor: 'divider',
        },
      }
    }

    if (node.stateStr === 'SECONDARY') {
      return { label: 'SECONDARY', color: 'default' }
    }

    if (isUnreachableState(node.stateStr)) {
      return {
        label: node.stateStr,
        color: 'default',
        variant: 'outlined',
        sx: {
          color: 'error.dark',
          borderColor: 'error.light',
        },
      }
    }

    if (isStartupState(node.stateStr)) {
      return {
        label: 'STARTUP',
        color: 'warning',
        variant: 'outlined',
      }
    }

    if (node.stateStr === 'RECOVERING') {
      return {
        label: 'RECOVERING',
        color: 'warning',
        variant: 'outlined',
      }
    }

    if (node.stateStr === 'ARBITER') {
      return {
        label: 'ARBITER',
        color: 'info',
        variant: 'outlined',
      }
    }

    if (node.stateStr === 'STANDALONE') {
      return {
        label: 'STANDALONE',
        color: 'default',
        variant: 'outlined',
        sx: {
          backgroundColor: accentSoftBg,
          color: accentColor,
          borderColor: accentColor,
        },
      }
    }

    if (node.stateStr === 'ROLLBACK') {
      return {
        label: 'ROLLBACK',
        color: 'error',
        variant: 'outlined',
      }
    }

    return {
      label: node.stateStr || 'UNKNOWN',
      color: 'default',
      variant: 'outlined',
    }
  }

  const formatBytesNullable = (bytes: number | undefined): string => {
    if (bytes === undefined || bytes === null) return 'N/A'
    return formatBytes(bytes)
  }

  const getPrimaryDataBytes = (): number | undefined => {
    const primaryNode = sortedNodes.find((node) => node.stateStr === 'PRIMARY')
    return primaryNode?.size?.data
  }

  const formatDataDelta = (nodeDataBytes: number | undefined, primaryDataBytes: number | undefined): string => {
    if (nodeDataBytes === undefined || primaryDataBytes === undefined) {
      return ''
    }
    const delta = nodeDataBytes - primaryDataBytes
    const sign = delta > 0 ? '+' : ''
    return ` (Δ ${sign}${formatBytes(Math.abs(delta))})`
  }

  const formatOplogWindow = (seconds: number | undefined): string => {
    if (seconds === undefined || seconds === null) return 'N/A'
    const hours = seconds / 3600
    if (hours >= 24) {
      const days = Math.floor(hours / 24)
      const remainingHours = Math.round(hours % 24)
      return `${days}d ${remainingHours}h`
    }
    return `${formatNumber(hours, { maximumFractionDigits: 1 })}h`
  }


  const primaryDataBytes = getPrimaryDataBytes()

  const renderExpanded = (node: Node) => {
    const history = (node.lag_history || []).map((p) => ({ time: new Date(p.time), lag: p.lag }))
    const isPrimaryLike = node.stateStr === 'PRIMARY' || node.stateStr === 'STANDALONE'
    return (
      <>
        {history.length > 1 ? (
          !isPrimaryLike ? (
            <>
              <Typography variant="subtitle2" gutterBottom>
                Lag History
              </Typography>
              <LagChart data={history} nodeName={node.name} accentColor={accentColor} />
            </>
          ) : null
        ) : !isPrimaryLike ? (
          <Typography variant="body2" color="text.secondary">
            История отставания появится после сбора данных.
          </Typography>
        ) : null}
        {node.sync_source_host && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="body2" color="text.secondary">
              Sync Source:{' '}
              <InfoTooltip title="Текущий источник репликации для этого узла.">
                <Box component="span">
                  <NeutralValueBadge>{node.sync_source_host}</NeutralValueBadge>
                </Box>
              </InfoTooltip>
            </Typography>
          </Box>
        )}
        <Box sx={{ mt: 1, display: 'grid', gap: 0.85 }}>
          <Typography variant="body2" color="text.secondary">
            MongoDB version:{' '}
            <InfoTooltip title="Версия MongoDB, которую отдаёт этот узел.">
              <Box component="span">
                <NeutralValueBadge>{node.mongo_version || 'N/A'}</NeutralValueBadge>
              </Box>
            </InfoTooltip>
          </Typography>
        </Box>
        {isPrimaryLike && node.primary_metrics && (
          <Box sx={{ mt: 1, display: 'grid', gap: 0.85 }}>
            <Typography variant="body2" color="text.secondary">
              Oplog configured:{' '}
              <InfoTooltip title="Настроенный максимальный размер local.oplog.rs на primary-like узле.">
                <Box component="span">
                  <NeutralValueBadge>
                    {formatBytesNullable(node.primary_metrics.oplog_configured_bytes)}
                  </NeutralValueBadge>
                </Box>
              </InfoTooltip>
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Oplog used:{' '}
              <InfoTooltip title="Текущий размер local.oplog.rs на primary-like узле.">
                <Box component="span">
                  <NeutralValueBadge>
                    {formatBytesNullable(node.primary_metrics.oplog_used_bytes)}
                  </NeutralValueBadge>
                </Box>
              </InfoTooltip>
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Oplog window:{' '}
              <InfoTooltip title="Временное окно между самой старой и самой новой записью в oplog.">
                <Box component="span">
                  <NeutralValueBadge>
                    {formatOplogWindow(node.primary_metrics.oplog_window_seconds)}
                  </NeutralValueBadge>
                </Box>
              </InfoTooltip>
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Election timeout:{' '}
              <InfoTooltip title="Таймаут выборов в replica set из конфига replSet. Для standalone будет N/A.">
                <Box component="span">
                  <NeutralValueBadge>
                    {node.primary_metrics.election_timeout_millis ?? 'N/A'} ms
                  </NeutralValueBadge>
                </Box>
              </InfoTooltip>
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Heartbeat interval:{' '}
              <InfoTooltip title="Интервал heartbeat между участниками replica set. Для standalone будет N/A.">
                <Box component="span">
                  <NeutralValueBadge>
                    {node.primary_metrics.heartbeat_interval_millis ?? 'N/A'} ms
                  </NeutralValueBadge>
                </Box>
              </InfoTooltip>
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Majority journal default:{' '}
              <InfoTooltip title="Значение writeConcernMajorityJournalDefault по умолчанию для replica set. Для standalone будет N/A.">
                <Box component="span">
                  <NeutralValueBadge>
                    {node.primary_metrics.write_concern_majority_journal_default === undefined
                      ? 'N/A'
                      : node.primary_metrics.write_concern_majority_journal_default
                        ? 'true'
                        : 'false'}
                  </NeutralValueBadge>
                </Box>
              </InfoTooltip>
            </Typography>
          </Box>
        )}
      </>
    )
  }

  const columns: DataTableColumn<Node>[] = [
    {
      id: 'host',
      label: 'Host',
      isIdentityColumn: true,
      render: (node) => {
        const [hostName] = node.name.includes(':') ? node.name.split(':') : [node.name, '27017']
        return <Typography component="span" sx={IDENTITY_CONTENT_SX}>{hostName}</Typography>
      },
    },
    {
      id: 'util',
      label: 'Resources', hidden: !sortedNodes.some(hasInfrastructure),
      align: 'center',
      headerSx: { width: 150 },
      cellSx: { width: 150 },
      render: (node) => <InfrastructureUsage node={node} />,
    },
    {
      id: 'port',
      label: 'Port',
      align: 'center',
      headerSx: { minWidth: 80, maxWidth: 80 },
      cellSx: { minWidth: 80, maxWidth: 80 },
      isIdentityColumn: false,
      render: (node) => {
        const [, portValue] = node.name.includes(':') ? node.name.split(':') : [node.name, '27017']
        return <NeutralValueBadge>{portValue}</NeutralValueBadge>
      },
    },
    {
      id: 'status',
      label: 'Status',
      align: 'center',
      headerSx: { width: 90 },
      cellSx: { width: 90 },
      render: (node) => {
        return <MonitorStatusBadge status={node.status}
          description={node.stateStr === 'STANDALONE' ? 'Состояние узла по данным MongoDB.' : 'Доступность узла: поле health команды replSetGetStatus.'} />
      },
    },
    {
      id: 'role',
      label: 'Role',
      align: 'center',
      headerSx: { width: 200 },
      cellSx: { width: 200 },
      render: (node) => {
        const rolePresentation = getRolePresentation(node)
        return (
          <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0.5 }}>
            <Chip
              label={rolePresentation.label}
              color={rolePresentation.color}
              size="small"
              variant={rolePresentation.variant || 'filled'}
              sx={rolePresentation.sx}
            />
            {node.primary_history && node.stateStr === 'PRIMARY' && node.primary_history.became_primary_at && (
              <Box sx={{ mt: 0.5 }} component="span" display="block">
                <Typography variant="caption" color="text.secondary" component="span" display="block">
                  Since: ~
                  {formatDistanceToNow(new Date(node.primary_history.became_primary_at), { locale: ru,
                    addSuffix: false,
                  })
                    .replace(/^about /i, '')
                    .replace(/^over /i, '')
                    .replace(/^almost /i, '')}
                </Typography>
                <Typography variant="caption" color="text.secondary" component="span" display="block">
                  {formatDateTimeShort(node.primary_history.became_primary_at)}
                </Typography>
              </Box>
            )}
          </Box>
        )
      },
    },
    {
      id: 'size',
      label: 'Size',
      align: 'left',
      headerSx: { width: 280 },
      cellSx: { width: 280 },
      render: (node) =>
        node.size ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
            {node.size.data !== undefined && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'nowrap' }}>
                <Box component="span" sx={{ minWidth: '3.25rem', flexShrink: 0, fontSize: '0.875rem' }}>
                  Data:
                </Box>
                <NeutralValueBadge>{formatBytes(node.size.data)}</NeutralValueBadge>
                {formatDataDelta(node.size.data, primaryDataBytes) && (
                  <InfoTooltip title="Разница sizeOnDisk относительно PRIMARY. Положительная разница возможна и означает, что на этом узле сейчас занято больше места.">
                    <Box
                      component="span"
                      sx={{ color: 'text.secondary', fontSize: '0.75rem', whiteSpace: 'nowrap' }}
                    >
                      {formatDataDelta(node.size.data, primaryDataBytes)}
                    </Box>
                  </InfoTooltip>
                )}
              </Box>
            )}
            {node.size.oplog !== undefined && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, flexWrap: 'nowrap' }}>
                <Box component="span" sx={{ minWidth: '3.25rem', flexShrink: 0, fontSize: '0.875rem' }}>
                  Oplog:
                </Box>
                <NeutralValueBadge>{formatBytes(node.size.oplog)}</NeutralValueBadge>
              </Box>
            )}
          </Box>
        ) : (
          <Typography variant="body2" color="text.secondary">
            N/A
          </Typography>
        ),
    },
    {
      id: 'lag',
      hidden: sortedNodes.every(node => node.stateStr === 'STANDALONE'),
      label: 'Lag',
      align: 'center',
      render: (node) => (
        <InfoTooltip title="Лаг репликации: задержка применения записей oplog относительно PRIMARY.">
          <Box component="span">
            <Chip
              label={`${node.lag}s`}
              size="small"
              variant="outlined"
              sx={getLagChipSx(node.lag)}
            />
          </Box>
        </InfoTooltip>
      ),
    },
    {
      id: 'priority',
      hidden: sortedNodes.every(node => node.stateStr === 'STANDALONE'),
      label: 'Priority',
      align: 'center',
      render: (node) => (
        <InfoTooltip title="Приоритет при выборах в replica set. Узел с большим приоритетом предпочтителен как PRIMARY.">
          <Box component="span">
            <NeutralValueBadge>{node.priority}</NeutralValueBadge>
          </Box>
        </InfoTooltip>
      ),
    },
    {
      id: 'hidden',
      hidden: sortedNodes.every(node => node.stateStr === 'STANDALONE'),
      label: 'Hidden',
      align: 'center',
      render: (node) => (
        <InfoTooltip title="Флаг скрытого узла в конфиге replica set. Скрытые узлы не участвуют в обычном чтении.">
          <Box component="span">
            <NeutralValueBadge>{node.hidden ? 'Yes' : 'No'}</NeutralValueBadge>
          </Box>
        </InfoTooltip>
      ),
    },
    {
      id: 'uptime',
      label: 'Uptime',
      align: 'center',
      render: (node) => (
        <InfoTooltip title="Время работы процесса mongod с последнего перезапуска.">
          <Box component="span">
            <NeutralValueBadge>{formatUptime(node.uptime)}</NeutralValueBadge>
          </Box>
        </InfoTooltip>
      ),
    },
  ]

  return (
    <DataTable<Node>
      title="Nodes" titleAdornment={<InfrastructureIndicator nodes={sortedNodes} />}
      headerActions={headerActions}
      columns={columns}
      rows={sortedNodes}
      getRowId={(n) => n.name}
      expandable={{
        renderExpanded,
        getExpandTooltip: (node) =>
          isAnalyticNode(node) ? 'Развернуть для деталей узла и истории лага.' : undefined,
      }}
    />
  )
}

export default NodesTable
