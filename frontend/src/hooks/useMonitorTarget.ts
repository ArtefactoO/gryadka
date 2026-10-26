import { useQuery } from '@tanstack/react-query'

export const useMonitorTarget = <T,>(type: string, name: string, getTarget: (name: string) => Promise<T>) =>
  useQuery({
    queryKey: [`${type}-target`, name], queryFn: () => getTarget(name),
    refetchInterval: 30_000, staleTime: 30_000,
  })
