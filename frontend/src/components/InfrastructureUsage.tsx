import { Box, Chip } from '@mui/material'
import { InfoControl, InfoTooltip, UiIcon } from '@project/ui/mui'
import { ShipWheel, Container, Monitor, Server, CircleHelp, Info } from '@project/ui/icons'
import type { InfrastructureMetrics, NodeExporterMetrics } from '../services/api'
import HostUtilizationChips from './HostUtilizationChips'
import PodResourcesChips from './PodResourcesChips'

export interface ResourceNode { infrastructure?: InfrastructureMetrics | null; node_exporter?: NodeExporterMetrics }
export const hasInfrastructure = (node: ResourceNode): boolean =>
  ['kubernetes', 'vm', 'host', 'docker'].includes(node.infrastructure?.kind || '') || !!node.node_exporter?.matched

const placement = {
  kubernetes: { icon: ShipWheel, name: 'Kubernetes', explanation: 'Узлы Kubernetes. Ресурсы контейнеров поступают из cAdvisor.' },
  docker: { icon: Container, name: 'Docker', explanation: 'Контейнеры Docker. Тип размещения задан в конфигурации.' },
  vm: { icon: Monitor, name: 'Виртуальные машины', explanation: 'Виртуальные машины. Ресурсы хоста поступают из node exporter.' },
  host: { icon: Server, name: 'Серверы', explanation: 'Серверы. Метрики из node exporter, тип размещения не указан.' },
  unknown: { icon: CircleHelp, name: 'Неизвестная среда', explanation: 'Тип размещения не определен. Его можно указать в конфигурации target.' },
} as const

export function InfrastructureIndicator({ nodes }: { nodes: ResourceNode[] }) {
  const kinds = [...new Set(nodes.map(node => node.infrastructure?.kind || (node.node_exporter?.matched ? 'host' : 'unknown'))
    .filter(kind => kind !== 'none'))]
  return <>{kinds.map(kind => <Box key={kind} component="span" sx={{ display: 'inline-flex', color: 'text.secondary' }}>
    <InfoControl label={placement[kind].name} description={placement[kind].explanation}>
      <UiIcon glyph={placement[kind].icon} size={18} />
    </InfoControl>
    </Box>
  )}</>
}

export default function InfrastructureUsage({ node }: { node: ResourceNode }) {
  const info = node.infrastructure
  if (info?.kind === 'kubernetes') {
    return info.resources && info.state === 'available'
      ? <PodResourcesChips resources={info.resources} />
      : <MissingMetrics reason={info.reason} />
  }
  if (info?.kind === 'none') return null
  if (node.node_exporter?.matched) return <HostUtilizationChips metrics={node.node_exporter} />
  if (info && ['vm', 'host', 'docker'].includes(info.kind)) return <MissingMetrics reason={info.reason} />
  return null
}

function MissingMetrics({ reason }: { reason?: string }) {
  return <InfoTooltip title={reason || 'Причина пока не получена. Обновите монитор для диагностики источника метрик.'} describeChild>
    <Chip tabIndex={0} size="small" variant="outlined" icon={<UiIcon glyph={Info} size={14} />} label="Нет метрик" />
  </InfoTooltip>
}
