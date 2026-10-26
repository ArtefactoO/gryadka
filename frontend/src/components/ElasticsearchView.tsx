import MonitorNodesSection from './MonitorNodesSection'
import { formatBytes, formatNumber, formatRate, formatBytesRate } from '../utils/metricFormat'
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
import { useQuery } from '@tanstack/react-query'

import { elasticsearchApi, ElasticsearchCluster, ElasticsearchNode } from '../services/api'
import { formatUpdateTime } from '../utils/formatUpdateTime'
import { getPercentChipSx } from '../utils/statusChipStyles'
import { InfoTooltip } from "@project/ui/mui"
import InfrastructureUsage, { InfrastructureIndicator, hasInfrastructure } from './InfrastructureUsage'
import NeutralValueBadge from './NeutralValueBadge'
import { DataTable } from "@project/ui/mui"
import { IDENTITY_CONTENT_SX } from "@project/ui/mui"
import VersionAndUpdateBlock from './VersionAndUpdateBlock'
import { useUiTheme } from "@project/ui/mui"

interface ElasticsearchViewProps {
  targetName: string
  targetType?: string
}

const getNodeShortName = (nodeName: string) => nodeName.split('.')[0] || nodeName

const getBaseClusterPrefix = (masterName?: string | null) => {
  if (!masterName) return null
  const parts = getNodeShortName(masterName).split('-').filter(Boolean)
  if (parts.length < 3) return null
  return parts.slice(0, -2).join('-')
}

const getElasticsearchNodeGroup = (nodeName: string, baseClusterPrefix: string | null) => {
  if (!baseClusterPrefix) return 0
  const shortName = getNodeShortName(nodeName)
  const basePrefix = `${baseClusterPrefix}-`
  if (!shortName.startsWith(basePrefix)) return 2

  const suffixParts = shortName.slice(basePrefix.length).split('-').filter(Boolean)
  return suffixParts.length === 2 ? 0 : 1
}

const ElasticsearchView = ({ targetName, targetType = 'elasticsearch' }: ElasticsearchViewProps) => {
  const { visual } = useUiTheme()
  const { data, isLoading, error } = useQuery<ElasticsearchCluster>({
    queryKey: ['elasticsearch-target', targetName],
    queryFn: () => elasticsearchApi.getTarget(targetName),
    refetchInterval: 30000,
    staleTime: 30_000,
  })



  const getStatusChipSx = (status: string) => {
    switch (status?.toLowerCase()) {
      case 'green':
        return { color: 'success.light', borderColor: 'success.light' }
      case 'yellow':
        return { color: 'warning.light', borderColor: 'warning.light' }
      case 'red':
        return { color: 'error.light', borderColor: 'error.light' }
      default:
        return {}
    }
  }

  if (!data) return <MonitorQueryState loading={isLoading} error={error} hasData={false} label="Elasticsearch" />

  const { overview, nodes } = data
  const relocatingShards = overview.relocating_shards ?? 0
  const masterNode = nodes.find((node) => node.role === 'MASTER')
  const baseClusterPrefix = getBaseClusterPrefix(masterNode?.name)
  const nodesSorted = [...nodes].sort((a, b) => {
    if (a.role === 'MASTER' && b.role !== 'MASTER') return -1
    if (a.role !== 'MASTER' && b.role === 'MASTER') return 1
    const groupDiff = getElasticsearchNodeGroup(a.name, baseClusterPrefix) - getElasticsearchNodeGroup(b.name, baseClusterPrefix)
    if (groupDiff !== 0) return groupDiff
    return a.name.localeCompare(b.name)
  })

  const esVersionChips = (
    <Chip
      label={overview.version ? `Elasticsearch Version: ${overview.version}` : 'Elasticsearch Version: N/A'}
      size="small"
      variant="outlined"
      sx={{ color: visual.accentColor, borderColor: visual.accentColor }}
    />
  )

  const hasEsActionButtons = Boolean(data.ui_url || data.monitoring_url)
  const esActionButtons = hasEsActionButtons ? (
    <>
      {data.ui_url ? (
        <InfoTooltip title="Открыть Kibana в новой вкладке">
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
            Kibana
          </Button>
        </InfoTooltip>
      ) : null}
      {data.monitoring_url ? (
        <InfoTooltip title="Открыть Elasticsearch Monitoring в новой вкладке">
          <Button
            size="small"
            variant="outlined"
            component="a"
            href={data.monitoring_url}
            target="_blank"
            rel="noopener noreferrer"
            startIcon={<UiIcon glyph={OpenInNewIcon} />}
            sx={{
              ml: data.ui_url ? 1 : 0,
              backgroundColor: visual.accentSoftBg,
              borderColor: visual.accentColor,
              borderWidth: 1.5,
              color: visual.accentColor,
              '&:hover': { borderColor: visual.accentColor, backgroundColor: visual.accentSoftBg },
            }}
          >
            Monitoring
          </Button>
        </InfoTooltip>
      ) : null}
    </>
  ) : undefined

  return (
    <Box>
      <MonitorQueryState loading={false} error={error} hasData={true} label="Elasticsearch" />
      <Box sx={{ mb: 3 }}>
        <VersionAndUpdateBlock
          versionChips={esVersionChips}
          actionButtons={esActionButtons}
          updateTime={data.last_update ? formatUpdateTime(data.last_update) : null}
          refreshTarget={{ targetType, targetName }}
          accentColor={visual.accentColor}
          accentSoftBg={visual.accentSoftBg}
        />
        <Typography variant="h6" sx={{ mt: 1.5, mb: 1.5 }}>Overview</Typography>
        <Paper
          elevation={0}
          sx={{
            border: '1px solid',
            borderColor: 'divider',
            boxShadow: 'none',
            p: 1.5,
          }}
        >
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <InfoTooltip title="Состояние кластера: green — все шарды распределены, yellow — реплики не везде, red — часть шардов не назначена">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Status:</Typography>
                <Chip
                  label={overview.status}
                  size="small"
                  variant="outlined"
                  sx={getStatusChipSx(overview.status)}
                />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Число узлов в кластере">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Nodes:</Typography>
                <Chip label={overview.number_of_nodes} size="small" variant="outlined" />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Число узлов, хранящих данные">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Data nodes:</Typography>
                <Chip label={overview.number_of_data_nodes} size="small" variant="outlined" />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Активные шарды (всего)">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Shards:</Typography>
                <Chip label={overview.active_shards} size="small" variant="outlined" />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Неназначенные шарды (требуют внимания при red)">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Unassigned:</Typography>
                <Chip
                  label={overview.unassigned_shards}
                  size="small"
                  color={
                    overview.unassigned_shards > 0 && overview.active_shards === 0
                      ? 'error'
                      : overview.unassigned_shards > 0
                        ? 'warning'
                        : 'default'
                  }
                  variant={overview.unassigned_shards > 0 ? 'filled' : 'outlined'}
                />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Шарды, которые сейчас переезжают между узлами">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Relocating:</Typography>
                <Chip
                  label={formatNumber(relocatingShards)}
                  size="small"
                  color={relocatingShards > 0 ? 'warning' : 'default'}
                  variant={relocatingShards > 0 ? 'filled' : 'outlined'}
                />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Общее количество документов в индексах">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Docs:</Typography>
                <Chip label={formatNumber(overview.total_docs)} size="small" variant="outlined" />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Размер данных на диске">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Store:</Typography>
                <Chip label={formatBytes(overview.total_store_size_bytes)} size="small" variant="outlined" />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Скорость индексирования документов по Elasticsearch nodes.stats">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Indexing:</Typography>
                <Chip label={formatRate(overview.indexing_rate)} size="small" variant="outlined" />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Скорость search query по Elasticsearch nodes.stats">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Search:</Typography>
                <Chip label={formatRate(overview.search_rate)} size="small" variant="outlined" />
              </Box>
            </InfoTooltip>
            <InfoTooltip title="Дисковый IO Elasticsearch: read / write bytes per second">
              <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                <Typography component="span" variant="body2" color="text.secondary">Disk IO:</Typography>
                <NeutralValueBadge>read {formatBytesRate(overview.fs_read_bytes_rate)}</NeutralValueBadge>
                <NeutralValueBadge>write {formatBytesRate(overview.fs_write_bytes_rate)}</NeutralValueBadge>
              </Box>
            </InfoTooltip>
          </Box>
        </Paper>
      </Box>

      <MonitorNodesSection empty={!nodesSorted.length}>
      <DataTable<ElasticsearchNode>
        title="Nodes" titleAdornment={<InfrastructureIndicator nodes={nodesSorted} />}
        columns={[
          {
            id: 'node',
            label: 'Node',
            isIdentityColumn: true,
            render: (node) => <Typography component="span" sx={IDENTITY_CONTENT_SX}>{node.name}</Typography>,
          },
          {
            id: 'usage',
            label: 'Resources', hidden: !nodesSorted.some(hasInfrastructure),
            align: 'center',
            render: (node) => <InfrastructureUsage node={node} />,
          },
          {
            id: 'role',
            label: 'Роль',
            align: 'center',
            render: (node) => (
              <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', justifyContent: 'center' }}>
                {node.role === 'MASTER' && (
                  <Chip
                    label="MASTER"
                    size="small"
                    variant="outlined"
                    sx={{ backgroundColor: visual.accentSoftBg, color: visual.accentColor, borderColor: visual.accentColor }}
                  />
                )}
                <Chip label="DATA" size="small" variant="outlined" sx={{ borderColor: 'divider' }} />
              </Box>
            ),
          },
          {
            id: 'shards',
            label: 'Shards',
            align: 'center',
            render: (node) => <NeutralValueBadge>{formatNumber(node.shards_count)}</NeutralValueBadge>,
          },
          {
            id: 'indexing',
            label: 'Indexing',
            align: 'center',
            render: (node) => <NeutralValueBadge>{formatRate(node.indexing_rate)}</NeutralValueBadge>,
          },
          {
            id: 'search',
            label: 'Search',
            align: 'center',
            render: (node) => <NeutralValueBadge>{formatRate(node.search_rate)}</NeutralValueBadge>,
          },
          {
            id: 'heap',
            label: 'Heap',
            align: 'center',
            render: (node) => (
              <Chip
                label={node.heap_used_percent != null ? `${node.heap_used_percent}%` : 'N/A'}
                size="small"
                variant="outlined"
                sx={getPercentChipSx(node.heap_used_percent)}
              />
            ),
          },
          {
            id: 'docs',
            label: 'Docs',
            align: 'center',
            render: (node) => <NeutralValueBadge>{formatNumber(node.docs_count)}</NeutralValueBadge>,
          },
        ]}
        rows={nodesSorted}
        getRowId={(n) => n.node_id}
        expandable={{
          renderExpanded: (node) => (
            <Box sx={{ display: 'grid', gap: 0.85 }}>
              <Typography variant="body2" color="text.secondary">
                Store:{' '}
                <NeutralValueBadge sx={{ ml: 0.5 }}>{formatBytes(node.store_size_bytes)}</NeutralValueBadge>
              </Typography>
              <InfoTooltip title="CPU Elasticsearch node по данным API сервиса. Метрики хоста показаны в колонке Resources.">
                <Typography variant="body2" color="text.secondary" sx={{ width: 'fit-content' }}>
                  Service CPU:{' '}
                  <Box component="span" sx={{ ml: 0.5, display: 'inline-flex' }}>
                    <Chip
                      label={node.cpu_percent != null ? `${node.cpu_percent}%` : 'N/A'}
                      size="small"
                      variant="outlined"
                      sx={getPercentChipSx(node.cpu_percent)}
                    />
                  </Box>
                </Typography>
              </InfoTooltip>
              {node.heap_used_bytes != null && node.heap_max_bytes != null && (
                <Typography variant="body2" color="text.secondary">
                  Heap:{' '}
                  <NeutralValueBadge sx={{ ml: 0.5 }}>
                    {formatBytes(node.heap_used_bytes)} / {formatBytes(node.heap_max_bytes)}
                  </NeutralValueBadge>
                </Typography>
              )}
              <Typography variant="body2" color="text.secondary">
                Disk IO:{' '}
                <NeutralValueBadge sx={{ ml: 0.5 }}>read {formatBytesRate(node.fs_read_bytes_rate)}</NeutralValueBadge>
                <NeutralValueBadge sx={{ ml: 0.5 }}>write {formatBytesRate(node.fs_write_bytes_rate)}</NeutralValueBadge>
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Thread pool rejected:{' '}
                <NeutralValueBadge sx={{ ml: 0.5 }}>search {formatNumber(node.search_rejected)}</NeutralValueBadge>
                <NeutralValueBadge sx={{ ml: 0.5 }}>write {formatNumber(node.write_rejected)}</NeutralValueBadge>
              </Typography>
            </Box>
          ),
        }}
      />
      </MonitorNodesSection>
    </Box>
  )
}

export default ElasticsearchView
