export const formatBytes = (value?: number | null, options: { maximumFractionDigits?: number; roundUp?: boolean } = {}): string => {
  if (value == null || !Number.isFinite(value)) return 'N/A'
  if (value <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(units.length - 1, Math.max(0, Math.floor(Math.log(value) / Math.log(1024))))
  const scaled = value / 1024 ** index
  return `${formatNumber(options.roundUp ? Math.ceil(scaled) : scaled, { maximumFractionDigits: options.maximumFractionDigits ?? 2 })} ${units[index]}`
}

export const formatNumber = (value?: number | null, options?: Intl.NumberFormatOptions): string =>
  value == null || !Number.isFinite(value) ? 'N/A' : value.toLocaleString('ru-RU', options)

export const formatRate = (value?: number | null): string =>
  value == null || !Number.isFinite(value) ? 'N/A' : `${formatNumber(value, { maximumFractionDigits: value >= 100 ? 0 : 2 })}/s`

export const formatBytesRate = (value?: number | null): string =>
  value == null || !Number.isFinite(value) ? 'N/A' : `${formatBytes(value)}/s`

export const formatUptime = (seconds?: number | null): string => {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return 'N/A'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`
}
