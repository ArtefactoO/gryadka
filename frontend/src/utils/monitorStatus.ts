export type MonitorStatusTone = 'success' | 'warning' | 'error' | 'neutral'

export function attentionStatus(status: string) {
  if (status.toLowerCase() === 'no_data') return {
    label: 'NO DATA', tone: 'neutral' as const,
    description: 'Данные мониторинга пока не получены. Это не подтверждает недоступность сервиса.',
  }
  const value = monitorStatus(status)
  return { ...value, description: value.tone === 'error'
    ? 'Монитор сообщил о недоступности или ошибке узла. Подробности в мониторе.'
    : value.label === 'DEGRADED'
      ? 'Монитор сообщил об ограничениях или неполных данных. Причина указана в мониторе, это не обязательно отказ сервиса.'
      : 'Состояние требует внимания. Подробности в мониторе.' }
}

export function monitorStatus(status?: string | null): { label: string; tone: MonitorStatusTone } {
  const raw = (status || '').trim().toUpperCase()
  const label = ({ RUNNING: 'UP', UNREACHEBLE: 'DOWN', UNREACHABLE: 'DOWN', MAINTANENCE: 'MAINTENANCE' } as Record<string, string>)[raw] || raw
  if (!label || ['UNKNOWN', 'N/A', '—'].includes(label)) return { label: 'UNKNOWN', tone: 'neutral' }
  if (['UP', 'READY', 'HEALTHY', 'OK'].includes(label)) return { label, tone: 'success' }
  if (['DOWN', 'ERROR', 'ALARM', 'FAILED'].includes(label)) return { label, tone: 'error' }
  if (['DEGRADED', 'LIMITED', 'MAINTENANCE', 'WARNING', 'RECOVERING'].includes(label)) return { label, tone: 'warning' }
  return { label, tone: 'neutral' }
}
