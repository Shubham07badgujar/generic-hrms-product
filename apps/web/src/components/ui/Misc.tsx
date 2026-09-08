/** Small shared pieces: avatar, tabs, timeline, banner, progress. */

import clsx from 'clsx'
import { useId, type ReactNode } from 'react'
import { initials } from '@/lib/format'

export function Avatar({
  name,
  size = 'md',
  className,
}: {
  name: string | null | undefined
  size?: 'xs' | 'sm' | 'md' | 'lg'
  className?: string
}) {
  const sizes = {
    xs: 'h-6 w-6 text-2xs',
    sm: 'h-8 w-8 text-xs',
    md: 'h-10 w-10 text-sm',
    lg: 'h-14 w-14 text-lg',
  }
  return (
    <span
      className={clsx(
        'inline-flex shrink-0 select-none items-center justify-center rounded-full',
        'bg-brand-soft font-semibold text-brand-ink',
        sizes[size],
        className,
      )}
      aria-hidden
      title={name ?? undefined}
    >
      {initials(name)}
    </span>
  )
}

export interface TabItem {
  id: string
  label: string
  count?: number
  disabled?: boolean
}

/**
 * Tabs with proper roving semantics: arrow keys move between tabs, and the
 * inactive tabs are removed from the tab order so Tab jumps to the panel.
 */
export function Tabs({
  items,
  active,
  onChange,
  className,
}: {
  items: TabItem[]
  active: string
  onChange: (id: string) => void
  className?: string
}) {
  const baseId = useId()

  function onKeyDown(event: React.KeyboardEvent) {
    const enabled = items.filter((item) => !item.disabled)
    const index = enabled.findIndex((item) => item.id === active)
    if (index === -1) return

    let nextIndex: number | null = null
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % enabled.length
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + enabled.length) % enabled.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = enabled.length - 1

    if (nextIndex !== null) {
      event.preventDefault()
      const next = enabled[nextIndex]!
      onChange(next.id)
      document.getElementById(`${baseId}-${next.id}`)?.focus()
    }
  }

  return (
    <div
      role="tablist"
      onKeyDown={onKeyDown}
      className={clsx('scrollbar-thin flex gap-1 overflow-x-auto border-b border-line', className)}
    >
      {items.map((item) => {
        const isActive = item.id === active
        return (
          <button
            key={item.id}
            id={`${baseId}-${item.id}`}
            role="tab"
            type="button"
            aria-selected={isActive}
            aria-controls={`${baseId}-${item.id}-panel`}
            tabIndex={isActive ? 0 : -1}
            disabled={item.disabled}
            onClick={() => onChange(item.id)}
            className={clsx(
              'relative whitespace-nowrap px-3 py-2.5 text-sm font-medium transition-colors',
              'disabled:cursor-not-allowed disabled:opacity-40',
              isActive ? 'text-brand' : 'text-ink-muted hover:text-ink',
            )}
          >
            <span className="flex items-center gap-1.5">
              {item.label}
              {item.count !== undefined && (
                <span
                  className={clsx(
                    'tabular rounded-full px-1.5 py-0.5 text-2xs',
                    isActive ? 'bg-brand-soft text-brand-ink' : 'bg-canvas text-ink-subtle',
                  )}
                >
                  {item.count}
                </span>
              )}
            </span>
            {isActive && (
              <span className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-brand" aria-hidden />
            )}
          </button>
        )
      })}
    </div>
  )
}

export function TabPanel({
  id,
  active,
  children,
}: {
  id: string
  active: string
  children: ReactNode
}) {
  if (id !== active) return null
  return (
    <div role="tabpanel" tabIndex={0} className="pt-5 focus-visible:outline-none">
      {children}
    </div>
  )
}

export interface TimelineEntry {
  id: string
  title: ReactNode
  meta?: ReactNode
  body?: ReactNode
  timestamp?: ReactNode
  tone?: 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info'
  icon?: ReactNode
}

/** The candidate-journey timeline. Also used for any audit-style history. */
export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  const dotTones = {
    neutral: 'bg-line text-ink-muted',
    brand: 'bg-brand-soft text-brand-ink ring-brand/20',
    success: 'bg-success-soft text-success-ink ring-success/20',
    warning: 'bg-warning-soft text-warning-ink ring-warning/20',
    danger: 'bg-danger-soft text-danger-ink ring-danger/20',
    info: 'bg-info-soft text-info-ink ring-info/20',
  } as const

  return (
    <ol className="relative space-y-0">
      {entries.map((entry, index) => (
        <li key={entry.id} className="relative flex gap-3 pb-5 last:pb-0">
          {/* The connector stops at the last item so the line doesn't dangle. */}
          {index < entries.length - 1 && (
            <span className="absolute left-[13px] top-7 h-[calc(100%-1rem)] w-px bg-line" aria-hidden />
          )}
          <span
            className={clsx(
              'relative z-10 mt-0.5 flex h-[27px] w-[27px] shrink-0 items-center justify-center',
              'rounded-full ring-4 ring-surface',
              dotTones[entry.tone ?? 'neutral'],
            )}
            aria-hidden
          >
            {entry.icon ?? <span className="h-1.5 w-1.5 rounded-full bg-current" />}
          </span>
          <div className="min-w-0 flex-1 space-y-1 pt-0.5">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
              <p className="text-sm font-medium text-ink">{entry.title}</p>
              {entry.timestamp && (
                <span className="tabular shrink-0 text-xs text-ink-subtle">{entry.timestamp}</span>
              )}
            </div>
            {entry.meta && <p className="text-xs text-ink-muted">{entry.meta}</p>}
            {entry.body && <div className="pt-1 text-sm text-ink-muted">{entry.body}</div>}
          </div>
        </li>
      ))}
    </ol>
  )
}

export function Banner({
  tone = 'info',
  title,
  children,
  icon,
  action,
  className,
}: {
  tone?: 'info' | 'success' | 'warning' | 'danger' | 'neutral'
  title?: ReactNode
  children?: ReactNode
  icon?: ReactNode
  action?: ReactNode
  className?: string
}) {
  const tones = {
    info: 'border-info/25 bg-info-soft text-info-ink',
    success: 'border-success/25 bg-success-soft text-success-ink',
    warning: 'border-warning/30 bg-warning-soft text-warning-ink',
    danger: 'border-danger/25 bg-danger-soft text-danger-ink',
    neutral: 'border-line bg-canvas text-ink-muted',
  }
  return (
    <div
      role={tone === 'danger' || tone === 'warning' ? 'alert' : undefined}
      className={clsx('flex gap-3 rounded-xl border p-3.5', tones[tone], className)}
    >
      {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
      <div className="min-w-0 flex-1 space-y-1">
        {title && <p className="text-sm font-semibold">{title}</p>}
        {children && <div className="text-sm opacity-95">{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  )
}

/** Quiet supporting text next to a heading. */
export function Muted({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={clsx('text-ink-muted', className)}>{children}</span>
}
