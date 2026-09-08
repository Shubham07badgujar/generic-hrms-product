/**
 * Loading, empty and error states.
 *
 * These exist as first-class components because the difference between them is
 * meaningful and gets blurred otherwise: "no results for your filter" is not
 * "nothing exists yet", and neither is "you cannot see this". A 403 rendered as
 * an empty table is a lie that costs someone an afternoon.
 */

import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Button } from './Button'
import { Spinner } from './Spinner'
import { ApiError } from '@/lib/api'

export function Skeleton({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={clsx(
        'relative block overflow-hidden rounded-md bg-line/60',
        'after:absolute after:inset-0 after:-translate-x-full after:animate-shimmer',
        'after:bg-gradient-to-r after:from-transparent after:via-surface/70 after:to-transparent',
        className,
      )}
    />
  )
}

export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="space-y-px" role="status" aria-label="Loading results">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex items-center gap-4 px-4 py-3.5">
          {Array.from({ length: columns }).map((__, columnIndex) => (
            <Skeleton
              key={columnIndex}
              className={clsx('h-3.5', columnIndex === 0 ? 'w-40' : 'w-24')}
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export function CardSkeleton({ className }: { className?: string }) {
  return (
    <div
      className={clsx('space-y-3 rounded-xl border border-line bg-surface p-5', className)}
      role="status"
      aria-label="Loading"
    >
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-8 w-32" />
      <Skeleton className="h-3 w-40" />
    </div>
  )
}

export function LoadingBlock({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-sm text-ink-muted">
      <Spinner className="h-4 w-4" label={label} />
      <span aria-hidden>{label}…</span>
    </div>
  )
}

export function EmptyState({
  title,
  description,
  icon,
  action,
  compact,
}: {
  title: string
  description?: ReactNode
  icon?: ReactNode
  action?: ReactNode
  compact?: boolean
}) {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center px-6 text-center',
        compact ? 'py-10' : 'py-16',
      )}
    >
      {icon && (
        <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-canvas text-ink-subtle">
          {icon}
        </div>
      )}
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && (
        <p className="mt-1 max-w-md text-sm text-ink-muted">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/**
 * Renders an API failure honestly.
 *
 * 403 and 404 get their own copy because the backend uses them deliberately:
 * out-of-scope rows return 404 rather than 403 so their existence is not
 * confirmed. Telling the user "not found" there is both accurate and the
 * intended behaviour, so the UI does not second-guess it.
 */
export function ErrorState({
  error,
  onRetry,
  compact,
}: {
  error: unknown
  onRetry?: () => void
  compact?: boolean
}) {
  const apiError = error instanceof ApiError ? error : null

  let title = 'Something went wrong'
  let description = apiError?.displayMessage ?? 'An unexpected error occurred.'
  let retryable = true

  if (apiError?.isPermissionDenied) {
    title = 'You do not have access to this'
    description =
      'Your role does not include this permission. If you believe this is wrong, contact your administrator.'
    retryable = false
  } else if (apiError?.isNotFound) {
    title = 'Not found'
    description = 'This record does not exist, or it is outside what you are permitted to see.'
    retryable = false
  } else if (apiError?.status === 0) {
    title = 'Cannot reach the server'
    description = 'Check your connection and try again.'
  }

  return (
    <div
      role="alert"
      className={clsx(
        'flex flex-col items-center justify-center px-6 text-center',
        compact ? 'py-10' : 'py-16',
      )}
    >
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-danger-soft text-danger">
        <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden>
          <path
            d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      <p className="mt-1 max-w-md text-sm text-ink-muted">{description}</p>
      {apiError?.requestId && (
        <p className="mt-2 font-mono text-2xs text-ink-subtle">
          Reference: {apiError.requestId}
        </p>
      )}
      {onRetry && retryable && (
        <Button className="mt-4" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

/**
 * The one place a query's three states are resolved, so no screen renders a
 * table over stale data while an error is on the floor.
 */
export function QueryBoundary<T>({
  isLoading,
  error,
  data,
  onRetry,
  loading,
  empty,
  isEmpty,
  children,
}: {
  isLoading: boolean
  error: unknown
  data: T | undefined
  onRetry?: () => void
  loading?: ReactNode
  empty?: ReactNode
  isEmpty?: (data: T) => boolean
  children: (data: T) => ReactNode
}) {
  if (isLoading) return <>{loading ?? <LoadingBlock />}</>
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  if (data === undefined) return <>{empty ?? <EmptyState title="No data" />}</>
  if (isEmpty?.(data)) return <>{empty ?? <EmptyState title="Nothing here yet" />}</>
  return <>{children(data)}</>
}
