import { Box, Chip, Typography } from '@mui/material'

import type { HAProxyOperationTimelineEvent } from '../services/api'

interface OperationTimelineProps {
  title?: string
  events: HAProxyOperationTimelineEvent[]
}

const OperationTimeline = ({ title, events }: OperationTimelineProps) => {
  if (!events || events.length === 0) return null

  return (
    <Box sx={{ mt: 1, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
      {title && (
        <Typography variant="body2" color="text.secondary">
          {title}
        </Typography>
      )}
      {events.slice(-6).map((event, idx) => (
        <Box
          key={`${event.timestamp}-${idx}`}
          sx={{ display: 'flex', alignItems: 'center', gap: 0.75, flexWrap: 'wrap' }}
        >
          <Chip label={new Date(event.timestamp).toLocaleTimeString()} size="small" variant="outlined" />
          <Typography variant="caption" color="text.secondary">
            {event.message}
          </Typography>
        </Box>
      ))}
    </Box>
  )
}

export default OperationTimeline
