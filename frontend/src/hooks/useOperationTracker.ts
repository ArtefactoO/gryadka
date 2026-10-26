import { useQuery } from '@tanstack/react-query'

import { haproxyApi } from '../services/api'

interface UseOperationTrackerParams {
  operationId?: string | null
  enabled?: boolean
}

export const useOperationTracker = ({ operationId, enabled = true }: UseOperationTrackerParams) => {
  const isEnabled = Boolean(enabled && operationId)

  return useQuery({
    queryKey: ['haproxy-operation', operationId],
    queryFn: () => haproxyApi.getOperationStatus(operationId as string),
    enabled: isEnabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status || status === 'queued' || status === 'running') return 2500
      return false
    },
    refetchIntervalInBackground: false,
  })
}

export default useOperationTracker
