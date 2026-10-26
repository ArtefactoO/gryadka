import MonitorStatusBadge from './MonitorStatusBadge'
import MonitorQueryState from './MonitorQueryState'
import MonitorNodesSection from './MonitorNodesSection'
import {
  Box,
  Chip,
  Paper,
  Typography,
} from '@mui/material'
import { useEffect, useState, useMemo } from 'react'

import { redisApi, type RedisNode } from '../services/api'
import { useUiTheme } from "@project/ui/mui"
import { formatUpdateTime } from '../utils/formatUpdateTime'
import { InfoTooltip } from "@project/ui/mui"
import { formatBytes, formatNumber, formatRate, formatUptime, formatBytesRate } from '../utils/metricFormat'
import { useMonitorTarget } from '../hooks/useMonitorTarget'
import NeutralValueBadge from './NeutralValueBadge'
import { DataTable } from "@project/ui/mui"
import { IDENTITY_CONTENT_SX } from "@project/ui/mui"
import RedisKeysBrowser from './RedisKeysBrowser'
import MonitorSections from './MonitorSections'
import { useAuth } from '../hooks/useAuth'
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import InfrastructureUsage, { hasInfrastructure, InfrastructureIndicator } from './InfrastructureUsage'

interface RedisViewProps {
  targetName: string
  targetType?: string
}

const RedisView = ({ targetName, targetType = 'redis' }: RedisViewProps) => {
  const { hasPermission } = useAuth()
  const [topTab, setTopTab] = useState(0)
  const { visual } = useUiTheme()
  const canReadKeys = hasPermission('redis.keys.read')
  const canDeleteKeys = hasPermission('redis.keys.delete')

  useEffect(() => {
    if (!canReadKeys && topTab !== 0) {
      setTopTab(0)
    }
  }, [canReadKeys, topTab])
  const { data, isLoading, error } = useMonitorTarget('redis', targetName, redisApi.getTarget)

  const formatKbps = (value?: number | null) => {
    if (value === undefined || value === null) return 'N/A'
    return formatBytesRate(value * 1024)
  }

  const formatRatio = (value?: number | null) => (
    value === undefined || value === null ? 'N/A' : formatNumber(value, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  )

  const formatUnixTime = (timestampS?: number): string => {
    if (timestampS === undefined || timestampS === null || timestampS <= 0) return 'N/A'
    return new Date(timestampS * 1000).toLocaleString('ru-RU')
  }

  const getDatabaseTooltip = (dbName: string): string => {
    if (/^db\d+$/.test(dbName)) {
      const dbNum = dbName.replace('db', '')
      return `Логическая база Redis ${dbNum}.`
    }
    return `База Redis: ${dbName}.`
  }

  const defaultDb = useMemo(() => {
    const databases = data?.databases
    if (!databases || !Array.isArray(databases) || databases.length === 0) {
      return 0
    }
    try {
      // Find database with most keys
      const bestDb = databases.reduce((prev, current) => {
        return (current.keys || 0) > (prev.keys || 0) ? current : prev
      }, databases[0])

      // Extract numeric part from name (e.g. "db16" -> 16)
      const match = (bestDb.name || '').match(/(\d+)/)
      return match ? parseInt(match[0], 10) : 0
    } catch (e) {
      return 0
    }
  }, [data?.databases])

  if (!data) {
    return <MonitorQueryState loading={isLoading} error={error} hasData={!!data} label="Redis" />
  }

  const redisNodes = data.nodes.filter((node) => node.kind === 'REDIS')
  const sentinelNodes = data.nodes.filter((node) => node.kind === 'SENTINEL')
  const sentinelByHost = new Map<string, (typeof sentinelNodes)[number]>()
  sentinelNodes.forEach((node) => {
    if (!sentinelByHost.has(node.host)) {
      sentinelByHost.set(node.host, node)
    }
  })

  const nodeVersions = redisNodes
    .map((node) => node.redis_version)
    .filter((version): version is string => Boolean(version))
  const redisVersionLabel =
    nodeVersions.length === 0
      ? null
      : new Set(nodeVersions).size === 1
        ? `Redis Version: ${nodeVersions[0]}`
        : 'Redis Version mismatch'
  const redisVersionChips = (
    <Box sx={{ display: 'inline-flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
      {redisVersionLabel && (
        <Chip
          label={redisVersionLabel}
          size="small"
          color={redisVersionLabel.includes('Version mismatch') ? 'warning' : 'secondary'}
          variant="outlined"
          sx={{
            color: redisVersionLabel.includes('Version mismatch') ? undefined : visual.accentColor,
            borderColor: redisVersionLabel.includes('Version mismatch') ? undefined : visual.accentColor,
          }}
        />
      )}
      <Chip
        label={`Mode: ${data.overview.mode}`}
        size="small"
        variant="outlined"
        sx={{
          color: visual.accentColor,
          borderColor: visual.accentColor,
        }}
      />
    </Box>
  )

  return (
    <Box>
      {error && <MonitorQueryState loading={false} error={error} hasData label="Redis" />}
      <VersionAndUpdateBlock
        versionChips={redisVersionChips}
        updateTime={data.last_update ? formatUpdateTime(data.last_update) : null}
        refreshTarget={{ targetType, targetName }}
        accentColor={visual.accentColor}
        accentSoftBg={visual.accentSoftBg}
      />

      <MonitorSections labels={['Databases', ...(canReadKeys ? ['Keys'] : [])]} value={topTab} onChange={setTopTab} label="redis-sections">
          {topTab === 0 &&
            (data.databases.length > 0 ? (
              <Box
                sx={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: 0.75,
                  alignItems: 'flex-start',
                }}
              >
                {data.databases.map((db) => (
                  <Paper
                    key={db.name}
                    elevation={0}
                    sx={{
                      p: 0.65,
                      border: '1px solid',
                      borderColor: 'divider',
                      borderRadius: 1,
                      backgroundColor: 'background.paper',
                      minWidth: 'fit-content',
                      maxWidth: '100%',
                      flex: '0 1 auto',
                      transition: 'background-color 130ms cubic-bezier(0.2, 0.8, 0.2, 1)',
                      '&:hover': {
                        boxShadow: 'none',
                        transform: 'translateY(-1px)',
                      },
                    }}
                  >
                    <InfoTooltip title={getDatabaseTooltip(db.name)}>
                      <Box component="span" sx={{ display: 'block' }}>
                        <Typography
                          variant="body2"
                          sx={{
                            fontWeight: 500,
                            mb: 0.4,
                            wordBreak: 'break-word',
                            color: 'text.primary',
                            cursor: 'help',
                            fontSize: '0.8125rem',
                            lineHeight: 1.2,
                          }}
                        >
                          {db.name}
                        </Typography>
                      </Box>
                    </InfoTooltip>
                    <Box sx={{ display: 'flex', gap: 0.6, flexWrap: 'wrap', alignItems: 'center' }}>
                      <Chip
                        label={`${formatNumber(db.keys)} keys`}
                        size="small"
                        variant="outlined"
                        sx={{ fontSize: '0.6875rem', height: '20px', '& .MuiChip-label': { px: 0.75 } }}
                      />
                      {db.expires !== undefined && db.expires !== null && (
                        <Chip
                          label={`${formatNumber(db.expires)} expires`}
                          size="small"
                          variant="outlined"
                          sx={{
                            fontSize: '0.6875rem',
                            height: '20px',
                            '& .MuiChip-label': { px: 0.75 },
                            ...(db.expires > 0
                              ? { color: visual.accentColor, borderColor: visual.accentColor }
                              : {}),
                          }}
                        />
                      )}
                    </Box>
                  </Paper>
                ))}
              </Box>
            ) : (
              <Typography variant="body2" color="text.secondary">
                Ключи отсутствуют или их статистика недоступна.
              </Typography>
            ))}
          {topTab === 1 && canReadKeys && (
            <RedisKeysBrowser
              key={targetName}
              targetName={targetName}
              accentColor={visual.accentColor}
              accentSoftBg={visual.accentSoftBg}
              defaultDb={defaultDb}
              databases={data.databases}
              canDelete={canDeleteKeys}
            />
          )}
      </MonitorSections>


      <MonitorNodesSection empty={!redisNodes.length}>
      <DataTable<RedisNode>
        title="Nodes" titleAdornment={<InfrastructureIndicator nodes={data.nodes} />}
        columns={[
          {
            id: 'host',
            label: 'Host',
            isIdentityColumn: true,
            render: (node) => <Typography component="span" sx={IDENTITY_CONTENT_SX}>{node.host}</Typography>,
          },
          {
            id: 'util',
            label: 'Resources', hidden: !redisNodes.some(hasInfrastructure),
            align: 'center',
            render: (node) => <InfrastructureUsage node={node} />,
          },
          { id: 'port', label: 'Port', align: 'center', render: (node) => <NeutralValueBadge>{node.port}</NeutralValueBadge> },
          {
            id: 'status',
            label: 'Status',
            align: 'center',
            render: (node) => {
              const relatedSentinel = sentinelByHost.get(node.host)
              const isWarning = relatedSentinel && relatedSentinel.status !== 'UP'
              const statusTooltip = [node.status_reason, isWarning ? `Статус Sentinel: ${relatedSentinel?.status}` : null].filter(Boolean).join('. ')
              return <MonitorStatusBadge status={node.status} tone={isWarning && node.status === 'UP' ? 'warning' : undefined} description={statusTooltip || undefined} />
            },
          },
          {
            id: 'role',
            label: 'Role',
            align: 'center',
            render: (node) => (
              <Chip
                label={node.role}
                size="small"
                variant="outlined"
                sx={
                  node.role === 'MASTER'
                    ? { backgroundColor: visual.accentSoftBg, color: visual.accentColor, borderColor: visual.accentColor }
                    : { borderColor: 'divider' }
                }
              />
            ),
          },
          {
            id: 'lag',
            label: <InfoTooltip title="Лаг репликации: разница между offset мастера и реплики. У мастера 0 B."><Box component="span" sx={{ cursor: 'help' }}>Lag</Box></InfoTooltip>,
            align: 'center',
            render: (node) => (
              <InfoTooltip
                title={
                  node.role === 'MASTER'
                    ? 'У мастера лаг репликации отсутствует (0 B).'
                    : node.lag_bytes === undefined || node.lag_bytes === null
                      ? 'Лаг репликации недоступен.'
                      : `Лаг репликации: ${formatBytes(node.lag_bytes)}. Разница offset мастера и реплики.`
                }
              >
                <Box component="span">
                  <NeutralValueBadge>
                    {node.role === 'MASTER' ? '0 B' : (node.lag_bytes === undefined || node.lag_bytes === null ? 'N/A' : formatBytes(node.lag_bytes))}
                  </NeutralValueBadge>
                </Box>
              </InfoTooltip>
            ),
          },
          {
            id: 'ops',
            label: <InfoTooltip title="Операций в секунду (instantaneous_ops_per_sec из Redis INFO)."><Box component="span" sx={{ cursor: 'help' }}>Ops/s</Box></InfoTooltip>,
            align: 'center',
            render: (node) => (
              <InfoTooltip
                title={
                  node.ops_per_sec === undefined || node.ops_per_sec === null
                    ? 'Операций в секунду недоступно.'
                    : `Операций в секунду: ${node.ops_per_sec} (instantaneous_ops_per_sec из Redis INFO).`
                }
              >
                <Box component="span">
                  <NeutralValueBadge>{node.ops_per_sec ?? 'N/A'}</NeutralValueBadge>
                </Box>
              </InfoTooltip>
            ),
          },
          {
            id: 'net',
            label: <InfoTooltip title="Сетевой вход/выход по Redis INFO: instantaneous_input_kbps и instantaneous_output_kbps."><Box component="span" sx={{ cursor: 'help' }}>Net</Box></InfoTooltip>,
            align: 'center',
            render: (node) => (
              <InfoTooltip
                title={[
                  `Входящий трафик: ${formatKbps(node.input_kbps)}`,
                  `Исходящий трафик: ${formatKbps(node.output_kbps)}`,
                ].join('\n')}
              >
                <Box component="span" sx={{ display: 'inline-flex', gap: 0.5, flexWrap: 'wrap', justifyContent: 'center' }}>
                  <NeutralValueBadge>{`in ${formatKbps(node.input_kbps)}`}</NeutralValueBadge>
                  <NeutralValueBadge>{`out ${formatKbps(node.output_kbps)}`}</NeutralValueBadge>
                </Box>
              </InfoTooltip>
            ),
          },
          {
            id: 'blocked',
            label: <InfoTooltip title="Клиенты, заблокированные Redis-командами ожидания, например BLPOP/BRPOP."><Box component="span" sx={{ cursor: 'help' }}>Blocked</Box></InfoTooltip>,
            align: 'center',
            render: (node) => (
              <InfoTooltip title={`Заблокировано клиентов: ${formatNumber(node.blocked_clients)}`}>
                <Box component="span">
                  <NeutralValueBadge>{formatNumber(node.blocked_clients)}</NeutralValueBadge>
                </Box>
              </InfoTooltip>
            ),
          },
          {
            id: 'evicted',
            label: <InfoTooltip title="Скорость вытеснения ключей Redis. Ненулевое значение обычно означает давление по памяти/maxmemory policy."><Box component="span" sx={{ cursor: 'help' }}>Evict/s</Box></InfoTooltip>,
            align: 'center',
            render: (node) => (
              <InfoTooltip title={`Вытеснено ключей: ${formatRate(node.evicted_keys_rate)}`}>
                <Box component="span">
                  <Chip
                    label={formatRate(node.evicted_keys_rate)}
                    size="small"
                    variant={(node.evicted_keys_rate ?? 0) > 0 ? 'filled' : 'outlined'}
                    sx={(node.evicted_keys_rate ?? 0) > 0
                      ? {
                          backgroundColor: 'var(--sl-warn)',
                          borderColor: 'var(--sl-warn)',
                          color: 'var(--sl-panel)',
                          '& .MuiChip-label': { color: 'var(--sl-panel)' },
                        }
                      : undefined}
                  />
                </Box>
              </InfoTooltip>
            ),
          },
          {
            id: 'clients',
            label: <InfoTooltip title="Число клиентских подключений к этому узлу Redis (INFO clients)."><Box component="span" sx={{ cursor: 'help' }}>Clients</Box></InfoTooltip>,
            align: 'center',
            render: (node) => (
              <InfoTooltip
                title={
                  node.connected_clients === undefined || node.connected_clients === null
                    ? 'Число подключённых клиентов недоступно.'
                    : `Подключённых клиентов: ${node.connected_clients}.`
                }
              >
                <Box component="span">
                  <NeutralValueBadge>{node.connected_clients ?? 'N/A'}</NeutralValueBadge>
                </Box>
              </InfoTooltip>
            ),
          },
          {
            id: 'uptime',
            label: 'Uptime',
            align: 'center',
            render: (node) => <NeutralValueBadge>{formatUptime(node.uptime_seconds)}</NeutralValueBadge>,
          },
        ]}
        rows={redisNodes}
        getRowId={(n) => n.name}
        expandable={{
          renderExpanded: (node) => {
            const relatedSentinel = sentinelByHost.get(node.host)
            const memoryLimitForPercent =
              node.maxmemory_bytes && node.maxmemory_bytes > 0 ? node.maxmemory_bytes : node.total_memory_bytes
            return (
              <Box sx={{ display: 'grid', gap: 0.85 }}>
                <Typography variant="body2" color="text.secondary">
                  Redis version:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{node.redis_version || 'N/A'}</NeutralValueBadge>
                </Typography>
                <InfoTooltip title="Service RAM из Redis INFO. Ресурсы pod или ВМ показываются отдельно в Resources.">
                  <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                    Service RAM used:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{formatBytes(node.used_memory_bytes)}</NeutralValueBadge>
                  </Typography>
                </InfoTooltip>
                <InfoTooltip title="Лимит Redis maxmemory, если задан, иначе total_system_memory из Redis INFO.">
                  <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                    Service RAM limit:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{formatBytes(memoryLimitForPercent)}</NeutralValueBadge>
                  </Typography>
                </InfoTooltip>
                <InfoTooltip title="RSS из Redis INFO: сколько памяти процесс Redis держит в RAM по данным ОС. Может быть больше used из-за служебных расходов аллокатора и фрагментации.">
                  <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                    Service RAM RSS:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{formatBytes(node.used_memory_rss_bytes)}</NeutralValueBadge>
                  </Typography>
                </InfoTooltip>
                <InfoTooltip title="Peak из Redis INFO: максимальное значение used_memory с момента старта процесса Redis. Это исторический пик, а не текущее потребление.">
                  <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                    Service RAM peak:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{formatBytes(node.used_memory_peak_bytes)}</NeutralValueBadge>
                  </Typography>
                </InfoTooltip>
                <InfoTooltip title="mem_fragmentation_ratio из Redis INFO. Значения сильно выше 1 могут указывать на фрагментацию или служебные расходы аллокатора.">
                  <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                    RAM fragmentation:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{formatRatio(node.mem_fragmentation_ratio)}</NeutralValueBadge>
                  </Typography>
                </InfoTooltip>
                <Typography variant="body2" color="text.secondary">
                  Network:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{`in ${formatKbps(node.input_kbps)}`}</NeutralValueBadge>
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{`out ${formatKbps(node.output_kbps)}`}</NeutralValueBadge>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Expired / Evicted:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{`${formatRate(node.expired_keys_rate)} / ${formatRate(node.evicted_keys_rate)}`}</NeutralValueBadge>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Keyspace hits / misses:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{`${formatRate(node.keyspace_hits_rate)} / ${formatRate(node.keyspace_misses_rate)}`}</NeutralValueBadge>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Rejected connections:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{formatRate(node.rejected_connections_rate)}</NeutralValueBadge>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  RDB last save:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{formatUnixTime(node.rdb_last_save_time)}</NeutralValueBadge>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  RDB:{' '}
                  <Chip
                    label={node.rdb_enabled === undefined || node.rdb_enabled === null ? 'N/A' : (node.rdb_enabled ? 'ON' : 'OFF')}
                    size="small"
                    color={node.rdb_enabled ? 'success' : 'default'}
                    variant={node.rdb_enabled ? 'filled' : 'outlined'}
                    sx={{ ml: 0.5 }}
                  />
                </Typography>
                {node.rdb_enabled !== false && (
                  <Typography variant="body2" color="text.secondary">
                    RDB last bgsave status:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{node.rdb_last_bgsave_status || 'N/A'}</NeutralValueBadge>
                  </Typography>
                )}
                {node.status_reason && (
                  <Typography variant="body2" color="text.secondary">
                    Status reason:{' '}
                    <NeutralValueBadge sx={{ ml: 0.5 }}>{node.status_reason}</NeutralValueBadge>
                  </Typography>
                )}
                {relatedSentinel && (
                  <>
                    <Typography variant="body2" color="text.secondary">
                      Sentinel port:{' '}
                      <NeutralValueBadge sx={{ ml: 0.5 }}>{relatedSentinel.port}</NeutralValueBadge>
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Sentinel status:{' '}
                      {relatedSentinel.status_reason ? (
                        <InfoTooltip title={relatedSentinel.status_reason}>
                          <Box component="span" sx={{ ml: 0.5 }}>
                            <Chip
                              label={relatedSentinel.status}
                              size="small"
                              color={relatedSentinel.status === 'UP' ? 'success' : 'error'}
                              variant="outlined"
                              sx={{
                                color: relatedSentinel.status === 'UP' ? 'success.light' : 'error.light',
                                borderColor: relatedSentinel.status === 'UP' ? 'success.light' : 'error.light',
                              }}
                            />
                          </Box>
                        </InfoTooltip>
                      ) : (
                        <Chip
                          label={relatedSentinel.status}
                          size="small"
                          color={relatedSentinel.status === 'UP' ? 'success' : 'error'}
                          variant="outlined"
                          sx={{
                            ml: 0.5,
                            color: relatedSentinel.status === 'UP' ? 'success.light' : 'error.light',
                            borderColor: relatedSentinel.status === 'UP' ? 'success.light' : 'error.light',
                          }}
                        />
                      )}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Sentinel version:{' '}
                      <NeutralValueBadge sx={{ ml: 0.5 }}>{relatedSentinel.redis_version || 'N/A'}</NeutralValueBadge>
                    </Typography>
                    <InfoTooltip title="connected_clients из INFO самого Sentinel-процесса. Это подключения к Sentinel API/порту, а не количество Sentinel-нод в кластере.">
                      <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                        Sentinel connected clients:{' '}
                        <NeutralValueBadge sx={{ ml: 0.5 }}>{relatedSentinel.connected_clients ?? 'N/A'}</NeutralValueBadge>
                      </Typography>
                    </InfoTooltip>
                  </>
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

export default RedisView
