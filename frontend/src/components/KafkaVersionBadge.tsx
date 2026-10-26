import { Chip } from '@mui/material'
import { InfoTooltip } from '@project/ui/mui'
import type { KafkaVersion } from '../services/api'

export default function KafkaVersionBadge({ info, summary = false }: { info?: KafkaVersion; summary?: boolean }) {
  const label = info?.source === 'container_image' ? 'Image Version' : 'Kafka Version'
  const value = info?.value || (info?.versions?.length ? (info.complete ? 'Mixed' : 'Partial') : 'N/A')
  const description = info?.value
    ? info.source === 'app_info'
      ? 'Версия работающего брокера из метрики kafka_server_app_info.'
      : `Тег образа из свежих метрик контейнера. Он не подтверждает версию работающего процесса.${info.image ? ` Образ: ${info.image}` : ''}`
    : info?.versions?.length
      ? `Версии получены не для всех брокеров или различаются по значению либо источнику: ${info.versions.join(', ')}. Подробности в Nodes.`
      : info?.reason || 'Версия недоступна. Для точной версии нужна метрика kafka_server_app_info с label version. ApiVersions не определяет версию Kafka.'
  return <InfoTooltip title={description} describeChild>
    <Chip tabIndex={0} label={summary ? `${label}: ${value}` : `${info?.source === 'container_image' ? 'Image: ' : ''}${value}`}
      size="small" variant="outlined" sx={{ maxWidth: '100%', '& .MuiChip-label': { overflow: 'hidden', textOverflow: 'ellipsis' } }} />
  </InfoTooltip>
}
