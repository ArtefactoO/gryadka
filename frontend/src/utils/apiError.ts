import axios from 'axios'

const stringifyDetail = (detail: unknown): string | null => {
  if (typeof detail === 'string') {
    const text = detail.trim()
    return text || null
  }
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => stringifyDetail(item))
      .filter((item): item is string => Boolean(item))
    return parts.length > 0 ? parts.join('; ') : null
  }
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) {
      return message.trim()
    }
  }
  return null
}

export const getApiErrorMessage = (error: unknown, fallback: string): string => {
  if (axios.isAxiosError(error)) {
    const detail = stringifyDetail(error.response?.data?.detail)
    if (detail) {
      return detail
    }
    if (!error.response) return 'Не удалось связаться с сервером. Проверьте соединение и повторите запрос.'
  }
  return fallback
}
