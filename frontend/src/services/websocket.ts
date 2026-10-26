/**
 * WebSocket service for real-time updates
 */
export interface WebSocketMessage {
  event: string
  timestamp: string
  clusters?: string[]
  target_ids?: string[]
  targets?: Array<{ target_type: string; target_name: string; target_id?: string }>
}

type MessageHandler = (message: WebSocketMessage) => void

class WebSocketService {
  private ws: WebSocket | null = null
  private reconnectAttempts = 0
  private maxReconnectAttempts = 10
  private reconnectDelay = 1000
  private handlers: Map<string, MessageHandler[]> = new Map()
  private url: string
  private manuallyClosed = false

  constructor() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsHost = import.meta.env.VITE_WS_URL || window.location.host
    this.url = `${wsProtocol}//${wsHost}/ws`
  }

  connect(): void {
    if (
      this.ws?.readyState === WebSocket.OPEN ||
      this.ws?.readyState === WebSocket.CONNECTING
    ) {
      return
    }

    try {
      this.manuallyClosed = false
      this.ws = new WebSocket(this.url)

      this.ws.onopen = () => {
        console.log('WebSocket connected')
        this.reconnectAttempts = 0
        this.handleMessage({
          event: 'connected',
          timestamp: new Date().toISOString(),
        })
      }

      this.ws.onmessage = (event) => {
        try {
          const message: WebSocketMessage = JSON.parse(event.data)
          this.handleMessage(message)
        } catch (error) {
          console.error('Error parsing WebSocket message:', error)
        }
      }

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error)
      }

      this.ws.onclose = () => {
        console.log('WebSocket disconnected')
        this.ws = null
        this.handleMessage({
          event: 'disconnected',
          timestamp: new Date().toISOString(),
        })
        if (!this.manuallyClosed) {
          this.attemptReconnect()
        }
      }
    } catch (error) {
      console.error('Error creating WebSocket connection:', error)
      this.attemptReconnect()
    }
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnection attempts reached')
      return
    }

    this.reconnectAttempts++
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1)
    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`)

    setTimeout(() => {
      this.connect()
    }, delay)
  }

  private handleMessage(message: WebSocketMessage): void {
    const handlers = this.handlers.get(message.event) || []
    handlers.forEach((handler) => handler(message))
  }

  on(event: string, handler: MessageHandler): void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, [])
    }
    this.handlers.get(event)!.push(handler)
  }

  off(event: string, handler: MessageHandler): void {
    const handlers = this.handlers.get(event)
    if (handlers) {
      const index = handlers.indexOf(handler)
      if (index > -1) {
        handlers.splice(index, 1)
      }
    }
  }

  disconnect(): void {
    this.manuallyClosed = true
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this.handlers.clear()
  }

  send(message: any): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message))
    }
  }
}

export const websocketService = new WebSocketService()
