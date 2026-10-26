import type { QueryClient } from '@tanstack/react-query'

export interface TargetRef {
  targetType: string
  targetName: string
}

export const normalizeTargetType = (targetType: string): string =>
  targetType === 'registry' ? 'addresses' : targetType

export const parseTargetId = (targetId: string): TargetRef | null => {
  const separatorIndex = targetId.indexOf(':')
  if (separatorIndex <= 0 || separatorIndex === targetId.length - 1) {
    return null
  }

  return {
    targetType: normalizeTargetType(targetId.slice(0, separatorIndex)),
    targetName: targetId.slice(separatorIndex + 1),
  }
}

export const invalidateGlobalTargetQueries = (queryClient: QueryClient) => {
  void queryClient.invalidateQueries({ queryKey: ['targets'] })
  void queryClient.invalidateQueries({ queryKey: ['unreachable'] })
  void queryClient.invalidateQueries({ queryKey: ['addresses-global-banner'] })
}

export const invalidateTargetQueries = (
  queryClient: QueryClient,
  targetType: string,
  targetName: string,
) => {
  const normalizedType = normalizeTargetType(targetType)

  invalidateGlobalTargetQueries(queryClient)

  if (normalizedType === 'mongodb') {
    void queryClient.invalidateQueries({ queryKey: ['cluster', targetName] })
    void queryClient.invalidateQueries({ queryKey: ['databases-overview'] })
    void queryClient.invalidateQueries({ queryKey: ['collections', targetName] })
    void queryClient.invalidateQueries({ queryKey: ['collection-indexes', targetName] })
  } else if (normalizedType === 'rabbitmq') {
    void queryClient.invalidateQueries({ queryKey: ['rabbitmq-target', targetName] })
  } else if (normalizedType === 'kafka') {
    void queryClient.invalidateQueries({ queryKey: ['kafka-target', targetName] })
  } else if (normalizedType === 'postgresql') {
    void queryClient.invalidateQueries({ queryKey: ['databases-overview', 'postgresql'] })
    void queryClient.invalidateQueries({ queryKey: ['postgresql-target', targetName] })
    void queryClient.invalidateQueries({ queryKey: ['postgresql-hba', targetName] })
    void queryClient.invalidateQueries({ queryKey: ['postgresql-catalog', targetName] })
    void queryClient.invalidateQueries({ queryKey: ['postgresql-roles', targetName] })
  } else if (normalizedType === 'redis') {
    void queryClient.invalidateQueries({ queryKey: ['redis-target', targetName] })
  } else if (normalizedType === 'haproxy') {
    void queryClient.invalidateQueries({ queryKey: ['haproxy-target', targetName] })
    void queryClient.invalidateQueries({ queryKey: ['haproxy-operations', targetName] })
  } else if (normalizedType === 'redpanda') {
    void queryClient.invalidateQueries({ queryKey: ['redpanda-target', targetName] })
  } else if (normalizedType === 'elasticsearch') {
    void queryClient.invalidateQueries({ queryKey: ['elasticsearch-target', targetName] })
  } else if (normalizedType === 'emqx') {
    void queryClient.invalidateQueries({ queryKey: ['emqx-target', targetName] })
  } else if (normalizedType === 'addresses') {
    void queryClient.invalidateQueries({ queryKey: ['addresses', targetName] })
    void queryClient.invalidateQueries({ queryKey: ['addresses-global-banner'] })
  } else if (normalizedType === 'argo-rollout') {
    void queryClient.invalidateQueries({ queryKey: ['argo-rollouts', targetName] })
  }
}
