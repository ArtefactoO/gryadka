import type { RabbitCluster, RabbitQueue } from '../services/api'

export const rabbitQueueKey = (queue: Pick<RabbitQueue, 'name' | 'vhost'>): string =>
  JSON.stringify([queue.vhost || '/', queue.name])

/** Purge removes Ready messages only. Unacked messages are already in flight. */
export function applyQueueActionSnapshot(current: RabbitCluster, queue: Pick<RabbitQueue, 'name' | 'vhost'>,
  action: 'purge' | 'delete'): RabbitCluster {
  const found = current.queues.find(row => rabbitQueueKey(row) === rabbitQueueKey(queue))
  if (!found) return current
  const deleting = action === 'delete'
  return {
    ...current,
    queues: deleting ? current.queues.filter(row => rabbitQueueKey(row) !== rabbitQueueKey(queue))
      : current.queues.map(row => rabbitQueueKey(row) === rabbitQueueKey(queue)
        ? { ...row, messages_ready: 0, messages: row.messages_unacknowledged } : row),
    overview: {
      ...current.overview,
      messages: Math.max(0, current.overview.messages - (deleting ? found.messages : found.messages_ready)),
      messages_ready: Math.max(0, current.overview.messages_ready - found.messages_ready),
      messages_unacknowledged: Math.max(0, current.overview.messages_unacknowledged - (deleting ? found.messages_unacknowledged : 0)),
      queues: Math.max(0, current.overview.queues - (deleting ? 1 : 0)),
    },
  }
}
