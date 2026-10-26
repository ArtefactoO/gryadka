import MonitorStatusBadge from './MonitorStatusBadge'
import MonitorNodesSection from './MonitorNodesSection'
import { formatNumber, formatRate, formatBytesRate, formatUptime } from '../utils/metricFormat'
import MonitorQueryState from './MonitorQueryState'
import { UiIcon } from "@project/ui/mui"
import {
  Box,
  Button,
  Chip,
  Paper,
  Typography,
} from '@mui/material'
import { ExternalLink as OpenInNewIcon } from "@project/ui/icons"
import { useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { emqxApi, EmqxCluster, EmqxNode } from '../services/api'
import { formatUpdateTime } from '../utils/formatUpdateTime'
import { InfoTooltip } from "@project/ui/mui"
import NeutralValueBadge from './NeutralValueBadge'
import { DataTable } from "@project/ui/mui"
import { IDENTITY_CONTENT_SX } from "@project/ui/mui"
import PodResourcesChips from './PodResourcesChips'
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import { useUiTheme } from "@project/ui/mui"

interface EmqxViewProps {
  targetName: string
  targetType?: string
}


const OverviewMetric = ({
  label,
  value,
  title,
}: {
  label: string
  value: ReactNode
  title: ReactNode
}) => (
  <InfoTooltip title={title}>
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
      <Typography component="span" variant="body2" color="text.secondary">{label}:</Typography>
      {typeof value === 'string' || typeof value === 'number' ? (
        <NeutralValueBadge>{value}</NeutralValueBadge>
      ) : value}
    </Box>
  </InfoTooltip>
)

const RatePair = ({
  rate,
  bytesRate,
}: {
  rate?: number | null
  bytesRate?: number | null
}) => (
  <Box
    component="span"
    sx={{
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 0.5,
      flexWrap: 'wrap',
    }}
  >
    <NeutralValueBadge>{formatRate(rate)}</NeutralValueBadge>
    <NeutralValueBadge>{formatBytesRate(bytesRate)}</NeutralValueBadge>
  </Box>
)

const EmqxView = ({ targetName, targetType = 'emqx' }: EmqxViewProps) => {
  const { visual } = useUiTheme()
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery<EmqxCluster>({
    queryKey: ['emqx-target', targetName],
    queryFn: () => emqxApi.getTarget(targetName),
    refetchInterval: 10000,
    refetchOnMount: 'always',
    staleTime: 10_000,
  })

  const sortedNodes = useMemo(() => {
    return [...(data?.nodes || [])].sort((a, b) => (a.host || a.name).localeCompare(b.host || b.name))
  }, [data?.nodes])
  const healthSignature = useMemo(() => {
    if (!data) return ''
    const nodeStatuses = (data.nodes || [])
      .map((node) => `${node.name}:${node.status || ''}`)
      .join('|')
    return `${data.last_update || ''}:${data.overview?.status || ''}:${nodeStatuses}`
  }, [data])

  useEffect(() => {
    if (!healthSignature) return
    void queryClient.invalidateQueries({ queryKey: ['unreachable'] })
  }, [healthSignature, queryClient])

  if (!data) return <MonitorQueryState loading={isLoading} error={error} hasData={false} label="EMQX" />

  const { overview } = data
  const versionChip = (
    <Chip
      label={
        overview.version
          ? `EMQX Version: ${overview.version}${overview.version_mismatch ? ' (mismatch)' : ''}`
          : 'EMQX Version: N/A'
      }
      size="small"
      variant="outlined"
      color={overview.version_mismatch ? 'warning' : undefined}
      sx={overview.version_mismatch ? undefined : { color: visual.accentColor, borderColor: visual.accentColor }}
    />
  )

  const actionButtons = data.ui_url ? (
    <InfoTooltip title="Открыть EMQX Dashboard в новой вкладке">
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
        Dashboard
      </Button>
    </InfoTooltip>
  ) : undefined

  const hasRealNodes = sortedNodes.some((node) => node.source !== 'config')
  const hasNodeDetails = sortedNodes.some((node) =>
    node.version ||
    node.uptime_seconds != null ||
    node.connections != null ||
    node.live_connections != null ||
    node.sessions != null ||
    node.subscriptions != null ||
    node.messages_received_rate != null ||
    node.messages_sent_rate != null ||
    node.bytes_received_rate != null ||
    node.bytes_sent_rate != null ||
    Boolean(node.resources)
  )
  const showNodes = hasRealNodes || hasNodeDetails

  return (
    <Box>
      <MonitorQueryState loading={false} error={error} hasData={true} label="EMQX" />
      <VersionAndUpdateBlock
        versionChips={versionChip}
        actionButtons={actionButtons}
        updateTime={data.last_update ? formatUpdateTime(data.last_update) : null}
        refreshTarget={{ targetType, targetName }}
        accentColor={visual.accentColor}
        accentSoftBg={visual.accentSoftBg}
      />

      <Typography variant="h6" sx={{ mt: 3, mb: 1 }}>
        Overview
      </Typography>
      <Paper
        elevation={0}
        sx={{
          border: '1px solid',
          borderColor: 'divider',
          boxShadow: 'none',
          mb: 2.5,
        }}
      >
        <Box sx={{ p: 1.5 }}>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <OverviewMetric
              label="Status"
              title="Общее состояние EMQX target"
              value={
                <MonitorStatusBadge status={overview.status} />
              }
            />
            {overview.namespace && (
              <OverviewMetric
                label="Namespace"
                title="Kubernetes namespace, где работает EMQX target"
                value={overview.namespace}
              />
            )}
            <OverviewMetric
              label="Connections"
              title="Текущие сетевые подключения клиентов к брокеру"
              value={formatNumber(overview.connections ?? overview.live_connections)}
            />
            <OverviewMetric
              label="Sessions"
              title="MQTT-сессии клиентов: подписки, offline-состояние и очереди сообщений, которые могут жить после disconnect"
              value={formatNumber(overview.sessions)}
            />
            <OverviewMetric
              label="Subscriptions"
              title="Количество подписок"
              value={formatNumber(overview.subscriptions)}
            />
            <OverviewMetric
              label="Topics"
              title="Количество топиков"
              value={formatNumber(overview.topics)}
            />
            <OverviewMetric
              label="Read"
              title="Скорость входящих сообщений и байт"
              value={<RatePair rate={overview.messages_received_rate} bytesRate={overview.bytes_received_rate} />}
            />
            <OverviewMetric
              label="Write"
              title="Скорость исходящих сообщений и байт"
              value={<RatePair rate={overview.messages_sent_rate} bytesRate={overview.bytes_sent_rate} />}
            />
          </Box>
        </Box>
      </Paper>

      <MonitorNodesSection empty={!showNodes} emptyMessage="API EMQX не вернул сведения об узлах.">
      {showNodes ? (
        <DataTable<EmqxNode>
          title="Nodes"
          columns={[
            {
              id: 'host',
              label: 'Host',
              isIdentityColumn: true,
              render: (node) => <Typography component="span" sx={IDENTITY_CONTENT_SX}>{node.host || node.name}</Typography>,
            },
            {
              id: 'resources',
              label: 'Resources',
              align: 'center',
              render: (node) => <PodResourcesChips resources={node.resources} />,
            },
            {
              id: 'status',
              label: 'Status',
              align: 'center',
              render: (node) => (
                <MonitorStatusBadge status={node.status} />
              ),
            },
            {
              id: 'connections',
              label: 'Connections',
              align: 'center',
              render: (node) => <NeutralValueBadge>{formatNumber(node.connections)}</NeutralValueBadge>,
            },
            {
              id: 'subscriptions',
              label: 'Subscriptions',
              align: 'center',
              render: (node) => <NeutralValueBadge>{formatNumber(node.subscriptions)}</NeutralValueBadge>,
            },
            {
              id: 'read',
              label: 'Read',
              align: 'center',
              render: (node) => (
                <RatePair rate={node.messages_received_rate} bytesRate={node.bytes_received_rate} />
              ),
            },
            {
              id: 'write',
              label: 'Write',
              align: 'center',
              render: (node) => (
                <RatePair rate={node.messages_sent_rate} bytesRate={node.bytes_sent_rate} />
              ),
            },
            {
              id: 'uptime',
              label: 'Uptime',
              align: 'center',
              render: (node) => (
                <InfoTooltip title={node.pod_start_time ? 'Время с запуска Kubernetes pod' : 'Uptime EMQX node'}>
                  <Box component="span" sx={{ display: 'inline-flex' }}>
                    <NeutralValueBadge>{formatUptime(node.uptime_seconds)}</NeutralValueBadge>
                  </Box>
                </InfoTooltip>
              ),
            },
          ]}
          rows={sortedNodes}
          getRowId={(node) => node.name}
          expandable={{
            renderExpanded: (node) => (
              <Box sx={{ display: 'grid', gap: 0.85 }}>
                <Typography variant="body2" color="text.secondary">
                  EMQX Version:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>{node.version || 'N/A'}</NeutralValueBadge>
                </Typography>
              </Box>
            ),
          }}
        />
      ) : null}
      </MonitorNodesSection>
    </Box>
  )
}

export default EmqxView
