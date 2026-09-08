/**
 * The notification bell and its dropdown.
 *
 * Reads from `useNotifications()`, which owns the polling. This component knows
 * nothing about transports or intervals — when a WebSocket replaces polling,
 * nothing here changes.
 */

import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/Button'
import { Badge, type Tone } from '@/components/ui/Badge'
import { useNotifications, type AppNotification } from '@/hooks/useNotifications'
import { usePermissions } from '@/app/AuthProvider'
import { formatRelative } from '@/lib/format'
import type { NotificationPriority } from '@/lib/types'

const PRIORITY_TONES: Record<NotificationPriority, Tone> = {
  low: 'neutral',
  normal: 'neutral',
  high: 'warning',
  critical: 'danger',
}

function BellIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-[18px] w-[18px]" aria-hidden>
      <path
        d="M6 9a6 6 0 1 1 12 0c0 4 1.5 5.5 1.5 5.5h-15S6 13 6 9ZM10 19a2 2 0 0 0 4 0"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function NotificationRow({
  item,
  onRead,
  canMarkRead,
}: {
  item: AppNotification
  onRead: (id: string) => void
  canMarkRead: boolean
}) {
  const body = (
    <div className="flex items-start gap-2.5">
      <span
        aria-hidden
        className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
          item.isRead ? 'bg-transparent' : 'bg-brand'
        }`}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p className={`text-sm ${item.isRead ? 'text-ink-muted' : 'font-medium text-ink'}`}>
            {item.title}
          </p>
          {item.priority === 'critical' || item.priority === 'high' ? (
            <Badge tone={PRIORITY_TONES[item.priority]}>
              {item.priority === 'critical' ? 'Action required' : 'Important'}
            </Badge>
          ) : null}
        </div>
        {item.body ? (
          <p className="mt-0.5 line-clamp-2 text-xs text-ink-muted">{item.body}</p>
        ) : null}
        <p className="mt-1 text-xs text-ink-subtle">{formatRelative(item.createdAt)}</p>
      </div>
    </div>
  )

  return (
    <li className="border-b border-line last:border-0">
      <div className="flex items-start gap-1 px-3 py-2.5 hover:bg-canvas">
        {item.link ? (
          <Link
            to={item.link}
            className="min-w-0 flex-1 focus-visible:outline-none"
            onClick={() => canMarkRead && !item.isRead && onRead(item.id)}
          >
            {body}
          </Link>
        ) : (
          <div className="min-w-0 flex-1">{body}</div>
        )}
        {canMarkRead && !item.isRead ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onRead(item.id)}
            aria-label={`Mark "${item.title}" as read`}
          >
            Read
          </Button>
        ) : null}
      </div>
    </li>
  )
}

export function NotificationBell() {
  const { available, unreadCount, items, markRead, markAllRead } = useNotifications()
  const permissions = usePermissions()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // A read-only principal (the CEO) can see notifications but cannot mark them
  // read — the engine strips their write actions and the middleware refuses the
  // request. Offering the button anyway would produce a 403 on click.
  const canMarkRead = !permissions.isReadOnly

  useEffect(() => {
    if (!open) return
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div className="relative" ref={containerRef}>
      <Button
        size="icon"
        variant="ghost"
        disabled={!available}
        onClick={() => setOpen((value) => !value)}
        title={available ? 'Notifications' : 'Notifications are unavailable'}
        aria-label={
          available ? `Notifications, ${unreadCount} unread` : 'Notifications (unavailable)'
        }
        aria-expanded={open}
        aria-haspopup="menu"
        className="relative"
      >
        <BellIcon />
        {available && unreadCount > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-brand px-1 text-[10px] font-semibold text-white"
            aria-hidden
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </Button>

      {open && (
        <div
          role="menu"
          aria-label="Notifications"
          className="absolute right-0 z-30 mt-2 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-line bg-surface shadow-lg"
        >
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <p className="text-sm font-semibold text-ink">Notifications</p>
            {canMarkRead && unreadCount > 0 ? (
              <Button size="sm" variant="ghost" onClick={() => void markAllRead()}>
                Mark all read
              </Button>
            ) : null}
          </div>

          {items.length === 0 ? (
            <p className="px-3 py-8 text-center text-sm text-ink-muted">
              Nothing needs your attention.
            </p>
          ) : (
            <ul className="max-h-[26rem] overflow-y-auto">
              {items.map((item) => (
                <NotificationRow
                  key={item.id}
                  item={item}
                  onRead={(id) => void markRead(id)}
                  canMarkRead={canMarkRead}
                />
              ))}
            </ul>
          )}

          <div className="border-t border-line px-3 py-2">
            <Link
              to="/settings/notifications"
              className="text-xs text-brand hover:underline"
              onClick={() => setOpen(false)}
            >
              Notification settings
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}
