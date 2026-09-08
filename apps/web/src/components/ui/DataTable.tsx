/**
 * The table used by every list screen.
 *
 * Two constraints from the API shape the design:
 *
 *  1. CURSOR PAGINATION — the server returns `next`/`previous` cursors and no
 *     total. So the footer says "Newer / Older", never "Page 3 of 47", and
 *     there is no row-count label. Inventing one would mean lying or making an
 *     extra count query the backend deliberately avoids.
 *
 *  2. SERVER-SIDE ORDERING — `ordering` is a query parameter on the API, and
 *     sorting only the current page would be wrong (it would reorder 25 rows
 *     out of an unknown total and look correct). So `sort` is a controlled
 *     prop the caller turns into a request; the table never sorts locally.
 */

import clsx from 'clsx'
import type { ReactNode } from 'react'
import { Button } from './Button'

export interface Column<T> {
  key: string
  header: ReactNode
  /** Field name the API accepts in `ordering`. Omit to make the column unsortable. */
  sortKey?: string
  render: (row: T) => ReactNode
  align?: 'left' | 'right' | 'center'
  /** Hidden below `sm`, for columns that are useful but not essential. */
  secondary?: boolean
  width?: string
  headerSrOnly?: boolean
}

export interface SortState {
  key: string
  direction: 'asc' | 'desc'
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  sort,
  onSortChange,
  emptyState,
  isLoading,
  loadingState,
  footer,
  caption,
}: {
  columns: Array<Column<T>>
  rows: T[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  sort?: SortState
  onSortChange?: (sort: SortState) => void
  emptyState?: ReactNode
  isLoading?: boolean
  loadingState?: ReactNode
  footer?: ReactNode
  caption?: string
}) {
  if (isLoading && loadingState) {
    return (
      <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-card">
        {loadingState}
      </div>
    )
  }

  if (!rows.length && emptyState) {
    return (
      <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-card">
        {emptyState}
      </div>
    )
  }

  function toggleSort(column: Column<T>) {
    if (!column.sortKey || !onSortChange) return
    const isCurrent = sort?.key === column.sortKey
    onSortChange({
      key: column.sortKey,
      direction: isCurrent && sort?.direction === 'asc' ? 'desc' : 'asc',
    })
  }

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-card">
      {/* Horizontal scroll is on the wrapper, so the page body never scrolls sideways. */}
      <div className="scrollbar-thin overflow-x-auto">
        <table className="w-full min-w-[40rem] border-collapse text-left">
          {caption && <caption className="sr-only">{caption}</caption>}
          <thead>
            <tr className="border-b border-line bg-canvas/70">
              {columns.map((column) => {
                const isSorted = Boolean(column.sortKey && sort?.key === column.sortKey)
                return (
                  <th
                    key={column.key}
                    scope="col"
                    style={column.width ? { width: column.width } : undefined}
                    // `aria-sort` is what tells assistive tech the table is
                    // sorted and by which column; a chevron alone does not.
                    aria-sort={
                      isSorted ? (sort!.direction === 'asc' ? 'ascending' : 'descending') : undefined
                    }
                    className={clsx(
                      'px-4 py-2.5 text-2xs font-semibold uppercase tracking-wide text-ink-subtle',
                      column.align === 'right' && 'text-right',
                      column.align === 'center' && 'text-center',
                      column.secondary && 'hidden sm:table-cell',
                    )}
                  >
                    {column.headerSrOnly ? (
                      <span className="sr-only">{column.header}</span>
                    ) : column.sortKey && onSortChange ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(column)}
                        className="inline-flex items-center gap-1 rounded transition-colors hover:text-ink"
                      >
                        {column.header}
                        <svg
                          viewBox="0 0 24 24"
                          fill="none"
                          className={clsx('h-3 w-3', isSorted ? 'opacity-100' : 'opacity-30')}
                          aria-hidden
                        >
                          <path
                            d={
                              isSorted && sort!.direction === 'desc'
                                ? 'M6 9l6 6 6-6'
                                : 'M6 15l6-6 6 6'
                            }
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                )
              })}
            </tr>
          </thead>

          <tbody className="divide-y divide-line">
            {rows.map((row) => (
              <tr
                key={rowKey(row)}
                // A clickable row must also be reachable by keyboard, which is
                // why it takes a tabIndex and handles Enter — a plain onClick
                // on a <tr> is a mouse-only feature.
                tabIndex={onRowClick ? 0 : undefined}
                role={onRowClick ? 'button' : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={
                  onRowClick
                    ? (event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          onRowClick(row)
                        }
                      }
                    : undefined
                }
                className={clsx(
                  'transition-colors',
                  onRowClick && 'cursor-pointer hover:bg-canvas focus-visible:bg-canvas',
                )}
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={clsx(
                      'px-4 py-3 align-middle text-sm text-ink',
                      column.align === 'right' && 'text-right tabular',
                      column.align === 'center' && 'text-center',
                      column.secondary && 'hidden sm:table-cell',
                    )}
                  >
                    {column.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {footer}
    </div>
  )
}

/**
 * Cursor pagination controls.
 *
 * "Newer"/"Older" rather than "Previous"/"Next" because the default ordering
 * is `-created_at`; with a reversed sort the caller passes different labels.
 */
/**
 * Numbered pages, for registers people cite: "it's on page 3".
 *
 * Windows the page list around the current page so fifty pages do not become
 * fifty buttons. Falls back to rendering nothing when the response carries no
 * page numbers, so a screen can adopt it before its endpoint does.
 */
export function PagePager({
  page,
  pages,
  count,
  pageSize,
  onPage,
  isFetching,
}: {
  page?: number
  pages?: number
  count?: number
  pageSize?: number
  onPage: (page: number) => void
  isFetching?: boolean
}) {
  if (!page || !pages || pages <= 1) return null

  const windowed: Array<number | '…'> = []
  for (let n = 1; n <= pages; n += 1) {
    if (n === 1 || n === pages || Math.abs(n - page) <= 2) {
      windowed.push(n)
    } else if (windowed[windowed.length - 1] !== '…') {
      windowed.push('…')
    }
  }

  const first = (page - 1) * (pageSize ?? 0) + 1
  const last = count !== undefined && pageSize ? Math.min(page * pageSize, count) : undefined

  return (
    <nav
      className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-canvas/50 px-4 py-2.5"
      aria-label="Pagination"
    >
      <p className="text-xs tabular-nums text-ink-subtle">
        {isFetching
          ? 'Loading…'
          : count !== undefined && last !== undefined
            ? `Showing ${first}–${last} of ${count}`
            : `Page ${page} of ${pages}`}
      </p>
      <div className="flex items-center gap-1">
        <Button size="sm" onClick={() => onPage(page - 1)} disabled={page <= 1 || isFetching}>
          Previous
        </Button>
        {windowed.map((entry, index) =>
          entry === '…' ? (
            <span key={`gap-${index}`} className="px-1 text-xs text-ink-subtle">
              …
            </span>
          ) : (
            <Button
              key={entry}
              size="sm"
              variant={entry === page ? 'primary' : 'ghost'}
              aria-current={entry === page ? 'page' : undefined}
              onClick={() => onPage(entry)}
              disabled={isFetching}
            >
              {entry}
            </Button>
          ),
        )}
        <Button size="sm" onClick={() => onPage(page + 1)} disabled={page >= pages || isFetching}>
          Next
        </Button>
      </div>
    </nav>
  )
}

export function CursorPager({
  hasPrevious,
  hasNext,
  onPrevious,
  onNext,
  isFetching,
  previousLabel = 'Newer',
  nextLabel = 'Older',
}: {
  hasPrevious: boolean
  hasNext: boolean
  onPrevious: () => void
  onNext: () => void
  isFetching?: boolean
  previousLabel?: string
  nextLabel?: string
}) {
  if (!hasPrevious && !hasNext) return null

  return (
    <nav
      className="flex items-center justify-between gap-3 border-t border-line bg-canvas/50 px-4 py-2.5"
      aria-label="Pagination"
    >
      <p className="text-xs text-ink-subtle">
        {isFetching ? 'Loading…' : 'Results are paged by cursor.'}
      </p>
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={onPrevious} disabled={!hasPrevious || isFetching}>
          {previousLabel}
        </Button>
        <Button size="sm" onClick={onNext} disabled={!hasNext || isFetching}>
          {nextLabel}
        </Button>
      </div>
    </nav>
  )
}

/** The filter bar above a table: search on the left, filters on the right. */
export function TableToolbar({
  search,
  onSearchChange,
  searchPlaceholder = 'Search…',
  children,
  onReset,
  hasFilters,
}: {
  search?: string
  onSearchChange?: (value: string) => void
  searchPlaceholder?: string
  children?: ReactNode
  onReset?: () => void
  hasFilters?: boolean
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      {onSearchChange && (
        <div className="relative w-full sm:max-w-xs">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-subtle"
            aria-hidden
          >
            <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="1.8" />
            <path d="m20 20-3.5-3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
          </svg>
          <input
            type="search"
            value={search ?? ''}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            className="h-9 w-full rounded-lg border border-line bg-surface pl-9 pr-3 text-base text-ink placeholder:text-ink-subtle"
          />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {children}
        {hasFilters && onReset && (
          <Button size="sm" variant="ghost" onClick={onReset}>
            Clear filters
          </Button>
        )}
      </div>
    </div>
  )
}

/** A compact select for the toolbar — visually lighter than a labelled Field. */
export function FilterSelect({
  label,
  value,
  onChange,
  options,
  allLabel = 'All',
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: Array<{ value: string; label: string }>
  allLabel?: string
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      aria-label={label}
      className="h-9 rounded-lg border border-line bg-surface px-2.5 pr-7 text-sm text-ink"
    >
      <option value="">{allLabel}</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}
