import { formatBytes } from '../utils/metricFormat'
import { Box, Chip } from '@mui/material'
import type { NodeExporterMetrics } from '../services/api'
import { InfoTooltip } from "@project/ui/mui"
import { getDiskPercentChipSx, getPercentChipSx } from '../utils/statusChipStyles'

interface HostUtilizationChipsProps {
  metrics?: NodeExporterMetrics
}

const formatPercent = (value: number | null | undefined): string => {
  return value === undefined || value === null ? 'N/A' : `${Math.round(value)}%`
}

const getMaxDiskUsedPercent = (metrics?: NodeExporterMetrics): number | null => {
  const values = (metrics?.disks || [])
    .map((disk) => disk.used_percent)
    .filter((value): value is number => value !== undefined && value !== null)
  if (values.length === 0) return null
  return Math.max(...values)
}

const buildTooltip = (metrics?: NodeExporterMetrics) => {
  const cpu = formatPercent(metrics?.cpu_usage_percent)
  const cpuCores = metrics?.cpu_cores === undefined || metrics.cpu_cores === null
    ? 'N/A'
    : `${metrics.cpu_cores} ядер`
  const ram = formatPercent(metrics?.memory_used_percent)
  const total = metrics?.memory_total_bytes
  const available = metrics?.memory_available_bytes
  const used = total !== undefined && total !== null && available !== undefined && available !== null
    ? Math.max(0, total - available)
    : null
  const totalLabel = total !== undefined && total !== null
    ? formatBytes(total, { maximumFractionDigits: 0, roundUp: true })
    : 'N/A'
  const usedLabel = used !== null ? formatBytes(used, { maximumFractionDigits: 0 }) : 'N/A'
  const disks = metrics?.disks || []

  return (
    <Box sx={{ display: 'grid', gap: 0.5 }}>
      <Box>CPU: {cpu} - {cpuCores}</Box>
      <Box>RAM: {ram} - {totalLabel} ({usedLabel} использовано)</Box>
      {disks.length > 0 ? (
        disks.map((disk) => (
          <Box
            key={`${disk.mountpoint}-${disk.device || ''}`}
            sx={{
              display: 'grid',
              gap: 0.25,
              pt: 0.5,
              borderTop: '1px solid var(--sl-line)',
            }}
          >
            <Box>
              Диск: {disk.device || 'N/A'}
            </Box>
            <Box sx={{ opacity: 0.92 }}>
              Точка монтирования {disk.mountpoint}: {formatPercent(disk.used_percent)}
              {' - '}
              {formatBytes(disk.total_bytes ?? disk.total, { maximumFractionDigits: 0, roundUp: true })}
            </Box>
          </Box>
        ))
      ) : (
        <Box>Диск: N/A</Box>
      )}
    </Box>
  )
}

const HostUtilizationChips = ({ metrics }: HostUtilizationChipsProps) => {
  const diskUsedPercent = getMaxDiskUsedPercent(metrics)
  if (!metrics?.matched) return null
  if (metrics.cpu_usage_percent == null && metrics.memory_used_percent == null && diskUsedPercent == null) return null

  return (
    <InfoTooltip title={buildTooltip(metrics)}>
      <Box
        component="span"
        sx={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 0.5,
          flexWrap: 'nowrap',
        }}
      >
        <Chip
          label={`CPU ${formatPercent(metrics?.cpu_usage_percent)}`}
          size="small"
          variant="outlined"
          sx={getPercentChipSx(metrics?.cpu_usage_percent)}
        />
        <Chip
          label={`RAM ${formatPercent(metrics?.memory_used_percent)}`}
          size="small"
          variant="outlined"
          sx={getPercentChipSx(metrics?.memory_used_percent)}
        />
        <Chip
          label={`Disk ${formatPercent(diskUsedPercent)}`}
          size="small"
          variant="outlined"
          sx={getDiskPercentChipSx(diskUsedPercent)}
        />
      </Box>
    </InfoTooltip>
  )
}

export default HostUtilizationChips
