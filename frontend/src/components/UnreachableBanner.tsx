import { InfoTooltip, StatusBadge, UiIcon } from "@project/ui/mui"
import { Box, Typography } from '@mui/material'
import { TriangleAlert as WarningAmberIcon } from "@project/ui/icons"
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { targetsApi, UnreachableHost } from '../services/api'
import { attentionStatus } from '../utils/monitorStatus'

const TARGET_TYPE_LABEL: Record<string, string> = {
  mongodb: 'MongoDB',
  rabbitmq: 'RabbitMQ',
  redis: 'Redis',
  postgresql: 'PostgreSQL',
  kafka: 'Kafka',
  haproxy: 'HAProxy',
  redpanda: 'Redpanda',
  elasticsearch: 'Elasticsearch',
  emqx: 'EMQX',
  addresses: 'Addresses',
  'argo-rollout': 'Rollouts',
}

const UnreachableBanner = () => {
  const [, setSearchParams] = useSearchParams()
  const { data: list = [] } = useQuery({
    queryKey: ['unreachable'],
    queryFn: () => targetsApi.getUnreachable(),
    refetchInterval: 10000,
  })

  const handleChipClick = (item: UnreachableHost) => {
    setSearchParams({ type: item.target_type, target: item.target_name })
  }

  if (!list || list.length === 0) return null

  const formatItemLabel = (item: UnreachableHost) => {
    const typeLabel = TARGET_TYPE_LABEL[item.target_type] || item.target_type
    if (item.host === item.target_name) {
      return `${typeLabel}: ${item.target_name}`
    }
    return `${typeLabel}: ${item.target_name} / ${item.host}`
  }

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        flexWrap: 'wrap',
        px: 2,
        py: 1.25,
        backgroundColor: 'background.paper',
        color: 'text.primary',
        borderBottom: '1px solid',
        borderColor: 'divider',
      }}
    >
      <UiIcon glyph={WarningAmberIcon} sx={{ fontSize: 22 }} />
      <Typography variant="body2" component="span" sx={{ fontWeight: 600 }}>
        Требуют внимания:
      </Typography>
      {list.map((item: UnreachableHost) => (
          <InfoTooltip key={`${item.target_type}:${item.target_name}:${item.host}`}
            describeChild title={`${attentionStatus(item.status).description} Нажмите, чтобы перейти к монитору.`}>
          <Box
            component="button"
            type="button"
            onClick={() => handleChipClick(item)}
            sx={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 0.5,
              border: 'none',
              padding: 0,
              margin: 0,
              background: 'transparent',
              color: 'inherit',
              font: 'inherit',
              cursor: 'pointer',
              borderRadius: 1,
              '&:focus-visible': { outline: '2px solid var(--sl-focus)', outlineOffset: 2 },
              '&:hover': { opacity: 0.9 },
            }}
          >
            <Typography component="span" variant="body2" sx={{ fontWeight: 500 }}>
              {formatItemLabel(item)}:
            </Typography>
            <StatusBadge label={attentionStatus(item.status).label} tone={attentionStatus(item.status).tone} />
          </Box>
          </InfoTooltip>
      ))}
    </Box>
  )
}

export default UnreachableBanner
