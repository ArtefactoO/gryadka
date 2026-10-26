import { formatBytes, formatNumber } from '../utils/metricFormat'
import { Box, Chip } from '@mui/material'
import type { KubernetesPodResources } from '../services/api'
import { InfoTooltip } from "@project/ui/mui"
import { getPercentChipSx } from '../utils/statusChipStyles'

interface PodResourcesChipsProps {
  resources?: KubernetesPodResources | null
}

const formatCpu = (cores?: number | null): string => {
  if (cores === undefined || cores === null) return 'N/A'
  if (cores < 1) return `${Math.round(cores * 1000)}m`
  return formatNumber(cores, { maximumFractionDigits: 2 })
}

const percentOfLimit = (usage?: number | null, limit?: number | null): number | null => {
  if (usage === undefined || usage === null || limit === undefined || limit === null || limit <= 0) return null
  return Math.max(0, (usage / limit) * 100)
}

const PodResourcesChips = ({ resources }: PodResourcesChipsProps) => {
  const cpuLimitPercent = percentOfLimit(resources?.cpu_usage_cores, resources?.cpu_limit_cores)
  const memoryLimitPercent = percentOfLimit(resources?.memory_usage_bytes, resources?.memory_limit_bytes)

  const tooltip = (
    <Box sx={{ display: 'grid', gap: 0.65, overflowWrap: 'anywhere' }}>
      {resources?.namespace && <Box>Kubernetes · {resources.namespace}/{resources.pod}</Box>}
      <Box>
        CPU: {formatCpu(resources?.cpu_usage_cores)}
      </Box>
      <Box sx={{ opacity: 0.92 }}>Запрошено: {formatCpu(resources?.cpu_request_cores)}</Box>
      <Box sx={{ opacity: 0.92 }}>Лимит: {formatCpu(resources?.cpu_limit_cores)}</Box>
      <Box sx={{ pt: 0.5, borderTop: '1px solid var(--sl-line)' }}>
        RAM: {formatBytes(resources?.memory_usage_bytes)}
      </Box>
      <Box sx={{ opacity: 0.92 }}>Запрошено: {formatBytes(resources?.memory_request_bytes)}</Box>
      <Box sx={{ opacity: 0.92 }}>Лимит: {formatBytes(resources?.memory_limit_bytes)}</Box>
    </Box>
  )

  return (
    <InfoTooltip title={tooltip}>
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
          label={`CPU ${formatCpu(resources?.cpu_usage_cores)}`}
          size="small"
          variant="outlined"
          sx={getPercentChipSx(cpuLimitPercent)}
        />
        <Chip
          label={`RAM ${formatBytes(resources?.memory_usage_bytes)}`}
          size="small"
          variant="outlined"
          sx={getPercentChipSx(memoryLimitPercent)}
        />
      </Box>
    </InfoTooltip>
  )
}

export default PodResourcesChips
