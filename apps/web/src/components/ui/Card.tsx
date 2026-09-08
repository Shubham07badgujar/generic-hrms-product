import clsx from 'clsx'
import type { ReactNode } from 'react'

export function Card({
  children,
  className,
  padded = true,
  as: Tag = 'section',
}: {
  children: ReactNode
  className?: string
  padded?: boolean
  as?: 'section' | 'div' | 'article'
}) {
  return (
    <Tag
      className={clsx(
        'rounded-xl border border-line bg-surface shadow-card',
        padded && 'p-5',
        className,
      )}
    >
      {children}
    </Tag>
  )
}

export function CardHeader({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={clsx('flex items-start justify-between gap-4', className)}>
      <div className="min-w-0 space-y-1">
        <h2 className="text-lg font-semibold tracking-tight text-ink">{title}</h2>
        {description && <p className="text-sm text-ink-muted">{description}</p>}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  )
}

/**
 * A dashboard metric.
 *
 * `value` is a ReactNode rather than a number so a card can render an em-dash
 * when the backing figure genuinely isn't available — see the dashboard notes
 * on cursor pagination not returning totals. A card must never show 0 for
 * "unknown".
 */
export function StatCard({
  label,
  value,
  hint,
  tone = 'neutral',
  icon,
  footer,
}: {
  label: string
  value: ReactNode
  hint?: string
  tone?: 'neutral' | 'brand' | 'success' | 'warning' | 'danger'
  icon?: ReactNode
  footer?: ReactNode
}) {
  const accents = {
    neutral: 'text-ink',
    brand: 'text-brand',
    success: 'text-success',
    warning: 'text-warning',
    danger: 'text-danger',
  } as const

  return (
    <Card className="flex flex-col justify-between gap-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-wide text-ink-subtle">{label}</p>
        {icon && <span className="text-ink-subtle">{icon}</span>}
      </div>
      <div>
        <p className={clsx('tabular text-3xl font-semibold tracking-tight', accents[tone])}>
          {value}
        </p>
        {hint && <p className="mt-1 text-xs text-ink-muted">{hint}</p>}
      </div>
      {footer}
    </Card>
  )
}

export function PageHeader({
  title,
  description,
  breadcrumbs,
  actions,
  meta,
}: {
  title: ReactNode
  description?: ReactNode
  breadcrumbs?: ReactNode
  actions?: ReactNode
  meta?: ReactNode
}) {
  return (
    <header className="space-y-3">
      {breadcrumbs}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1.5">
          <h1 className="truncate text-2xl font-semibold tracking-tight text-ink">{title}</h1>
          {description && <p className="text-sm text-ink-muted">{description}</p>}
          {meta && <div className="flex flex-wrap items-center gap-2 pt-1">{meta}</div>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  )
}

export function Section({
  title,
  description,
  action,
  children,
  className,
}: {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={clsx('space-y-3', className)}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-4">
          <div className="space-y-0.5">
            {title && (
              <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
                {title}
              </h2>
            )}
            {description && <p className="text-sm text-ink-muted">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

/** Label/value pairs — the backbone of every detail page. */
export function DescriptionList({
  items,
  columns = 2,
}: {
  items: Array<{ label: string; value: ReactNode }>
  columns?: 1 | 2 | 3
}) {
  return (
    <dl
      className={clsx(
        'grid gap-x-6 gap-y-4',
        columns === 1 && 'grid-cols-1',
        columns === 2 && 'sm:grid-cols-2',
        columns === 3 && 'sm:grid-cols-2 lg:grid-cols-3',
      )}
    >
      {items.map((item) => (
        <div key={item.label} className="min-w-0 space-y-0.5">
          <dt className="text-xs font-medium uppercase tracking-wide text-ink-subtle">
            {item.label}
          </dt>
          <dd className="break-words text-sm text-ink">{item.value ?? '—'}</dd>
        </div>
      ))}
    </dl>
  )
}
