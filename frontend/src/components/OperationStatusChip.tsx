import Chip from '@mui/material/Chip'

interface OperationStatusChipProps {
  status?: string
  progress?: string
  size?: 'small' | 'medium'
}

const getChipColor = (status?: string): 'default' | 'success' | 'warning' | 'error' => {
  switch ((status || '').toLowerCase()) {
    case 'succeeded':
      return 'success'
    case 'failed':
      return 'error'
    case 'queued':
    case 'running':
      return 'warning'
    default:
      return 'default'
  }
}

const toLabel = (status?: string, progress?: string) => {
  const s = (status || '').toLowerCase()
  const p = (progress || '').toLowerCase()
  if (s === 'succeeded') return 'Completed'
  if (s === 'failed') return 'Failed'
  if (s === 'queued') return 'Queued'
  if (p === 'terminating_old_pod') return 'Terminating old pod'
  if (p === 'waiting_replacement_creation') return 'Waiting replacement'
  if (p === 'waiting_replacement_ready') return 'Starting replacement'
  if (p === 'rollout_in_progress') return 'Rollout in progress'
  return s ? `${s[0].toUpperCase()}${s.slice(1)}` : 'Unknown'
}

const OperationStatusChip = ({ status, progress, size = 'small' }: OperationStatusChipProps) => (
  <Chip
    label={toLabel(status, progress)}
    size={size}
    color={getChipColor(status)}
    variant="outlined"
  />
)

export default OperationStatusChip
