import MonitorNodesSection from './MonitorNodesSection'
import MonitorStatusBadge from './MonitorStatusBadge'
import MonitorQueryState from './MonitorQueryState'
import { formatNumber, formatBytes, formatRate, formatBytesRate } from '../utils/metricFormat'
import { UiIcon } from "@project/ui/mui"
import {
  Alert,
  Box,
  Button,
  Chip,
  Typography,
} from '@mui/material'
import { ExternalLink as OpenInNewIcon } from "@project/ui/icons"
import { useState } from 'react'
import ConsumerGroupsTable from './ConsumerGroupsTable'
import { useQuery } from '@tanstack/react-query'

import { redpandaApi, RedpandaBroker, RedpandaCluster } from '../services/api'
import { formatUpdateTime } from '../utils/formatUpdateTime'
import { getPercentChipSx } from '../utils/statusChipStyles'
import { InfoTooltip } from "@project/ui/mui"
import InfrastructureUsage, { hasInfrastructure, InfrastructureIndicator } from './InfrastructureUsage'
import NeutralValueBadge from './NeutralValueBadge'
import { DataTable } from "@project/ui/mui"
import { IDENTITY_CONTENT_SX } from "@project/ui/mui"
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import MonitorSections from './MonitorSections'
import { useUiTheme } from "@project/ui/mui"

interface RedpandaViewProps {
  targetName: string
  targetType?: string
}

const LAG_WARNING = 1_500_000
const LAG_CRITICAL = 5_000_000

const RedpandaView = ({ targetName, targetType = 'redpanda' }: RedpandaViewProps) => {
  const { visual } = useUiTheme()
  const [redpandaTab, setRedpandaTab] = useState(0)
  const { data, isLoading, error } = useQuery<RedpandaCluster>({
    queryKey: ['redpanda-target', targetName],
    queryFn: () => redpandaApi.getTarget(targetName),
    refetchInterval: 30000,
    staleTime: 30_000,
  })



  const RatePair = ({ count, bytes }: { count?: number | null; bytes?: number | null }) => (
    <Box sx={{ display: 'inline-flex', gap: 0.5, flexWrap: 'wrap', justifyContent: 'center' }}>
      <NeutralValueBadge>{formatRate(count)}</NeutralValueBadge>
      <NeutralValueBadge>{formatBytesRate(bytes)}</NeutralValueBadge>
    </Box>
  )

  if (!data) return <MonitorQueryState loading={isLoading} error={error} hasData={false} label="Redpanda" />

  const { overview } = data
  const brokers = data.brokers || []
  const movingPartitions = (overview.partitions_moving_from ?? 0) + (overview.partitions_moving_to ?? 0)

  const redpandaVersionChips = (
    <Chip
      label={
        overview.version_mismatch
          ? `Redpanda Version: ${overview.version ?? 'N/A'} (Version mismatch)`
          : overview.version
            ? `Redpanda Version: ${overview.version}`
            : 'Redpanda Version: N/A'
      }
      size="small"
      variant="outlined"
      color={overview.version_mismatch ? 'warning' : undefined}
      sx={overview.version_mismatch ? undefined : { color: visual.accentColor, borderColor: visual.accentColor }}
    />
  )

  const redpandaActionButtons = data.ui_url ? (
    <InfoTooltip title="Открыть Redpanda Console в новой вкладке">
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
        Открыть консоль
      </Button>
    </InfoTooltip>
  ) : null

  return (
    <Box>
      {error && <Alert severity="warning">Не удалось обновить данные. Показан последний полученный результат.</Alert>}
      <VersionAndUpdateBlock
        versionChips={redpandaVersionChips}
        actionButtons={redpandaActionButtons ?? undefined}
        updateTime={data.last_update ? formatUpdateTime(data.last_update) : null}
        refreshTarget={{ targetType, targetName }}
        accentColor={visual.accentColor}
        accentSoftBg={visual.accentSoftBg}
      />
      <MonitorSections labels={['Overview', 'Consumer Groups']} value={redpandaTab} onChange={setRedpandaTab} label="redpanda-sections">
          {overview.lag_complete === false && <Alert severity="warning" sx={{ mb: 2 }}>Lag доступен не для всех групп. Недоступные offsets не подменяются нулем.</Alert>}
          {redpandaTab === 0 && (
            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <InfoTooltip title={'Всего consumer groups в кластере'}>
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography component="span" variant="body2" color="text.secondary">Groups:</Typography>
                  <Chip label={overview.total_groups} size="small" variant="outlined" />
                </Box>
              </InfoTooltip>
              <InfoTooltip title="Количество групп, у которых lag > 0">
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography component="span" variant="body2" color="text.secondary">With lag:</Typography>
                  <Chip label={overview.groups_with_lag} size="small" variant="outlined" />
                </Box>
              </InfoTooltip>
              <InfoTooltip title="Суммарный lag по всем группам">
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography component="span" variant="body2" color="text.secondary">Total lag:</Typography>
                  <Chip
                    label={formatNumber(overview.total_lag)}
                    size="small"
                    color={
                      (overview.total_lag ?? -1) >= LAG_CRITICAL
                        ? 'error'
                        : (overview.total_lag ?? -1) >= LAG_WARNING
                          ? 'warning'
                          : 'default'
                    }
                    variant={(overview.total_lag ?? -1) >= LAG_WARNING ? 'filled' : 'outlined'}
                  />
                </Box>
              </InfoTooltip>
              <InfoTooltip title="Максимальный lag среди групп">
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography component="span" variant="body2" color="text.secondary">Max lag:</Typography>
                  <Chip
                    label={formatNumber(overview.max_group_lag)}
                    size="small"
                    color={
                      (overview.max_group_lag ?? -1) >= LAG_CRITICAL
                        ? 'error'
                        : (overview.max_group_lag ?? -1) >= LAG_WARNING
                          ? 'warning'
                          : 'default'
                    }
                    variant={(overview.max_group_lag ?? -1) >= LAG_WARNING ? 'filled' : 'outlined'}
                  />
                </Box>
              </InfoTooltip>
              <InfoTooltip title="Скорость чтения из Kafka: records/s и bytes/s по public_metrics Redpanda">
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography component="span" variant="body2" color="text.secondary">Read:</Typography>
                  <RatePair count={overview.records_fetched_rate} bytes={overview.read_bytes_rate} />
                </Box>
              </InfoTooltip>
              <InfoTooltip title="Скорость записи в Kafka: records/s и bytes/s по public_metrics Redpanda">
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography component="span" variant="body2" color="text.secondary">Write:</Typography>
                  <RatePair count={overview.records_produced_rate} bytes={overview.write_bytes_rate} />
                </Box>
              </InfoTooltip>
              <InfoTooltip title="Партиции, которые переносятся с узла и на узел: from / to">
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography component="span" variant="body2" color="text.secondary">Moving:</Typography>
                  <Chip
                    label={`${formatNumber(overview.partitions_moving_from)} / ${formatNumber(overview.partitions_moving_to)}`}
                    size="small"
                    color={movingPartitions > 0 ? 'warning' : 'default'}
                    variant={movingPartitions > 0 ? 'filled' : 'outlined'}
                  />
                </Box>
              </InfoTooltip>
              <InfoTooltip title="Число реплик вне ISR по данным public_metrics Redpanda">
                <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography component="span" variant="body2" color="text.secondary">Under replicated:</Typography>
                  <Chip
                    label={formatNumber(overview.under_replicated_replicas)}
                    size="small"
                    color={(overview.under_replicated_replicas ?? 0) > 0 ? 'warning' : 'default'}
                    variant={(overview.under_replicated_replicas ?? 0) > 0 ? 'filled' : 'outlined'}
                  />
                </Box>
              </InfoTooltip>
            </Box>
          )}
          {redpandaTab === 1 && <ConsumerGroupsTable groups={data.consumer_groups} />}

      </MonitorSections>

      <MonitorNodesSection empty={!brokers?.length}>
        <DataTable<RedpandaBroker>
          title="Nodes" titleAdornment={<InfrastructureIndicator nodes={brokers} />}
          columns={[
            {
              id: 'host',
              label: 'Host',
              isIdentityColumn: true,
              render: (broker) => <Typography component="span" sx={IDENTITY_CONTENT_SX}>{broker.host}</Typography>,
            },
            {
              id: 'usage',
              label: 'Resources', hidden: !(brokers || []).some(hasInfrastructure),
              align: 'center',
              render: (broker) => <InfrastructureUsage node={broker} />,
            },
            {
              id: 'status',
              label: 'Status',
              align: 'center',
              render: (broker) => {
                return <MonitorStatusBadge status={broker.status} />
              },
            },
            {
              id: 'role',
              label: 'Role',
              align: 'center',
              render: (broker) => (
                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', justifyContent: 'center' }}>
                  {broker.role === 'controller' && (
                    <Chip
                      label="CONTROLLER"
                      size="small"
                      variant="outlined"
                      sx={{ backgroundColor: visual.accentSoftBg, color: visual.accentColor, borderColor: visual.accentColor }}
                    />
                  )}
                  <Chip label="BROKER" size="small" variant="outlined" sx={{ borderColor: 'divider' }} />
                </Box>
              ),
            },
            {
              id: 'read',
              label: 'Read',
              align: 'center',
              render: (broker) => <RatePair count={broker.records_fetched_rate} bytes={broker.read_bytes_rate} />,
            },
            {
              id: 'write',
              label: 'Write',
              align: 'center',
              render: (broker) => <RatePair count={broker.records_produced_rate} bytes={broker.write_bytes_rate} />,
            },
          ]}
          rows={[...brokers].sort((a, b) => (a.role === 'controller' ? 0 : 1) - (b.role === 'controller' ? 0 : 1))}
          getRowId={(b) => String(b.node_id)}
          expandable={{
            renderExpanded: (broker) => (
              <Box sx={{ display: 'grid', gap: 0.85 }}>
                <Typography variant="body2" color="text.secondary">
                  Redpanda Version:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{broker.version ?? 'N/A'}</NeutralValueBadge>
                </Typography>
                <InfoTooltip title="CPU процесса Redpanda по данным самого сервиса. Ресурсы pod или ВМ находятся в колонке Resources.">
                  <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                    Service CPU:{' '}
                    <Box component="span" sx={{ ml: 0.5, display: 'inline-flex' }}>
                      <Chip
                        label={broker.cpu_percent != null ? `${broker.cpu_percent}%` : 'N/A'}
                        size="small"
                        variant="outlined"
                        sx={getPercentChipSx(broker.cpu_percent)}
                      />
                    </Box>
                  </Typography>
                </InfoTooltip>
                <InfoTooltip title="RAM процесса Redpanda / Seastar по данным самого сервиса. Ресурсы pod или ВМ находятся в колонке Resources.">
                  <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                    Service RAM:{' '}
                    <Box component="span" sx={{ ml: 0.5, display: 'inline-flex', gap: 0.5, flexWrap: 'wrap' }}>
                      <Chip
                        label={broker.memory_used_percent != null ? `${broker.memory_used_percent}%` : 'N/A'}
                        size="small"
                        variant="outlined"
                        sx={getPercentChipSx(broker.memory_used_percent)}
                      />
                      <NeutralValueBadge>
                        {broker.memory_allocated_bytes != null && broker.memory_available_bytes != null
                          ? `${formatBytes(broker.memory_allocated_bytes)} / ${formatBytes(broker.memory_allocated_bytes + broker.memory_available_bytes)}`
                          : 'N/A'}
                      </NeutralValueBadge>
                    </Box>
                  </Typography>
                </InfoTooltip>
                <Typography variant="body2" color="text.secondary">
                  Disk IO:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>read {formatRate(broker.disk_read_ops_rate)}</NeutralValueBadge>
                  <NeutralValueBadge sx={{ ml: 0.5 }}>write {formatRate(broker.disk_write_ops_rate)}</NeutralValueBadge>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Moving partitions:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>
                    from {formatNumber(broker.partitions_moving_from)} / to {formatNumber(broker.partitions_moving_to)}
                  </NeutralValueBadge>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Under replicated:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{formatNumber(broker.under_replicated_replicas)}</NeutralValueBadge>
                </Typography>
              </Box>
            ),
          }}
        />
      </MonitorNodesSection>
    </Box>
  )
}

export default RedpandaView
