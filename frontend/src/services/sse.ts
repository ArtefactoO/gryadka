export interface SSEEventMessage {
  event: string
  domain?: string
  timestamp?: string
  target_type?: string
  target_name?: string
  operation_id?: string
  operation_type?: string
  status?: string
  progress?: string
  message?: string
  [key: string]: unknown
}

interface SSESubscribeParams {
  targetType?: string
  targetName?: string
  event?: string
}

type SSEHandler = (message: SSEEventMessage) => void

const getBaseUrl = () => import.meta.env.VITE_API_URL || window.location.origin

const buildSseUrl = (params: SSESubscribeParams): string => {
  const url = new URL('/api/events/stream', getBaseUrl())
  if (params.targetType) url.searchParams.set('target_type', params.targetType)
  if (params.targetName) url.searchParams.set('target_name', params.targetName)
  if (params.event) url.searchParams.set('event', params.event)
  return url.toString()
}

const parseAndHandleEvent = (eventName: string, dataLines: string[], handler: SSEHandler) => {
  if (dataLines.length === 0) return
  try {
    const payload = JSON.parse(dataLines.join('\n')) as SSEEventMessage
    if (!payload.event) {
      payload.event = eventName
    }
    handler(payload)
  } catch (_error) {
    // Ignore malformed events.
  }
}

export const subscribeSse = (params: SSESubscribeParams, handler: SSEHandler) => {
  let disposed = false
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  const abortController = new AbortController()

  const connect = async () => {
    const token = localStorage.getItem('auth_token')
    const headers: HeadersInit = {}
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }

    try {
      const response = await fetch(buildSseUrl(params), {
        method: 'GET',
        headers,
        credentials: 'include',
        cache: 'no-store',
        signal: abortController.signal,
      })

      if (!response.ok || !response.body) {
        throw new Error(`SSE request failed with status ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = 'message'
      let currentData: string[] = []

      const flushEvent = () => {
        parseAndHandleEvent(currentEvent, currentData, handler)
        currentEvent = 'message'
        currentData = []
      }

      while (!disposed) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        let newlineIndex = buffer.indexOf('\n')
        while (newlineIndex !== -1) {
          let line = buffer.slice(0, newlineIndex)
          buffer = buffer.slice(newlineIndex + 1)
          if (line.endsWith('\r')) {
            line = line.slice(0, -1)
          }

          if (!line) {
            flushEvent()
          } else if (!line.startsWith(':')) {
            if (line.startsWith('event:')) {
              currentEvent = line.slice(6).trim()
            } else if (line.startsWith('data:')) {
              currentData.push(line.slice(5).trimStart())
            }
          }

          newlineIndex = buffer.indexOf('\n')
        }
      }

      if (currentData.length > 0) {
        flushEvent()
      }

      if (!disposed) {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null
          void connect()
        }, 2000)
      }
    } catch (_error) {
      if (disposed || abortController.signal.aborted) {
        return
      }
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null
        void connect()
      }, 2000)
    }
  }

  void connect()

  return () => {
    disposed = true
    abortController.abort()
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }
}
