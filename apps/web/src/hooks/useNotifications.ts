/**
 * Notifications.
 *
 * This file was written as an interface before the endpoint existed, so that
 * switching transports would be one constant rather than a refactor of every
 * screen showing a badge. That swap has now happened:
 *
 *   - was:   `nullTransport` — reported zero, called nothing
 *   - now:   `createPollingTransport` — GET /notifications/unread-count/ on a timer
 *   - later: a WebSocket transport — same shape, pushed
 *
 * Every consumer is unchanged from when the backend did not exist, which is
 * the whole return on having built the seam first.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiGet, apiPost } from '@/lib/api'
import type { NotificationItem, NotificationPriority } from '@/lib/types'

export interface AppNotification {
  id: string
  kind: string
  title: string
  body: string
  link?: string
  isRead: boolean
  createdAt: string
  priority: NotificationPriority
}

export interface NotificationTransport {
  /** Consumers render an honest disabled state when a transport is inert. */
  readonly available: boolean
  start(onUpdate: (state: { unreadCount: number; items: AppNotification[] }) => void): void
  stop(): void
  markRead(id: string): Promise<void>
  markAllRead(): Promise<void>
}

const nullTransport: NotificationTransport = {
  available: false,
  start(onUpdate) {
    onUpdate({ unreadCount: 0, items: [] })
  },
  stop() {},
  async markRead() {},
  async markAllRead() {},
}

/**
 * The Phase-10 transport, written now so the contract is settled.
 *
 * Polls the count (cheap, indexed on `(recipient, is_read, -created_at)`) and
 * only fetches the list when the count changes. Backs off while the tab is
 * hidden, because a background tab polling every 30s is a battery cost with no
 * user watching.
 */
export function createPollingTransport(options: {
  fetchCount: () => Promise<number>
  fetchItems: () => Promise<AppNotification[]>
  markRead: (id: string) => Promise<void>
  markAllRead: () => Promise<void>
  intervalMs?: number
  hiddenIntervalMs?: number
}): NotificationTransport {
  let timer: number | undefined
  let lastCount = -1
  let stopped = false

  const interval = options.intervalMs ?? 30_000
  const hiddenInterval = options.hiddenIntervalMs ?? 120_000

  return {
    available: true,
    start(onUpdate) {
      stopped = false
      const tick = async () => {
        if (stopped) return
        try {
          const unreadCount = await options.fetchCount()
          if (unreadCount !== lastCount) {
            lastCount = unreadCount
            onUpdate({ unreadCount, items: await options.fetchItems() })
          }
        } catch {
          // A failed poll is not worth surfacing; the next tick retries.
        }
        if (!stopped) {
          timer = window.setTimeout(tick, document.hidden ? hiddenInterval : interval)
        }
      }
      void tick()
    },
    stop() {
      stopped = true
      if (timer) window.clearTimeout(timer)
    },
    markRead: options.markRead,
    markAllRead: options.markAllRead,
  }
}

function toAppNotification(item: NotificationItem): AppNotification {
  return {
    id: item.id,
    kind: item.kind,
    title: item.title,
    body: item.body,
    link: item.link_url || undefined,
    isRead: item.is_read,
    createdAt: item.created_at,
    priority: item.priority,
  }
}

/** The live transport. A WebSocket one would replace this constant and nothing else. */
export const notificationTransport: NotificationTransport = createPollingTransport({
  fetchCount: async () => {
    const { unread } = await apiGet<{ unread: number }>('/notifications/unread-count/')
    return unread
  },
  fetchItems: async () => {
    const page = await apiGet<{ data: NotificationItem[] }>('/notifications/', {
      params: { page_size: '15' },
    })
    return page.data.map(toAppNotification)
  },
  markRead: async (id) => {
    await apiPost(`/notifications/${id}/read/`)
  },
  markAllRead: async () => {
    await apiPost('/notifications/read-all/')
  },
})

export { nullTransport }

export function useNotifications() {
  const [unreadCount, setUnreadCount] = useState(0)
  const [items, setItems] = useState<AppNotification[]>([])
  const transport = useRef(notificationTransport)

  useEffect(() => {
    const active = transport.current
    active.start(({ unreadCount: count, items: list }) => {
      setUnreadCount(count)
      setItems(list)
    })
    return () => active.stop()
  }, [])

  const markRead = useCallback(async (id: string) => {
    await transport.current.markRead(id)
    setItems((current) => current.map((item) => (item.id === id ? { ...item, isRead: true } : item)))
    setUnreadCount((count) => Math.max(0, count - 1))
  }, [])

  const markAllRead = useCallback(async () => {
    await transport.current.markAllRead()
    setItems((current) => current.map((item) => ({ ...item, isRead: true })))
    setUnreadCount(0)
  }, [])

  return useMemo(
    () => ({
      available: transport.current.available,
      unreadCount,
      items,
      markRead,
      markAllRead,
    }),
    [unreadCount, items, markRead, markAllRead],
  )
}
