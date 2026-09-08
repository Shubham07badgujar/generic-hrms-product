/**
 * List state (search, filters, sort, cursor) held in the URL.
 *
 * In the URL rather than component state so a filtered view is linkable and
 * survives a reload — the difference between "here's the queue I'm looking at"
 * and "click Pipeline, then set three filters again".
 *
 * Search is debounced before it reaches the query string; the API's
 * `SearchFilter` runs an ILIKE across several columns and firing that per
 * keystroke is wasteful for both ends.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { SortState } from '@/components/ui/DataTable'
import type { ListParams } from '@/lib/queries'

export function useListParams(defaults: { ordering?: string } = {}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const [searchDraft, setSearchDraft] = useState(() => searchParams.get('search') ?? '')

  const search = searchParams.get('search') ?? ''
  const ordering = searchParams.get('ordering') ?? defaults.ordering ?? ''
  const cursor = searchParams.get('cursor') ?? ''

  // Keep the input in step when the URL changes from elsewhere (back button).
  useEffect(() => setSearchDraft(search), [search])

  useEffect(() => {
    if (searchDraft === search) return
    const timer = window.setTimeout(() => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current)
          if (searchDraft) next.set('search', searchDraft)
          else next.delete('search')
          // Any change to the result set invalidates the position in it —
          // keeping either would page into a list that no longer exists.
          next.delete('cursor')
          next.delete('page')
          return next
        },
        { replace: true },
      )
    }, 300)
    return () => window.clearTimeout(timer)
  }, [searchDraft, search, setSearchParams])

  const setParam = useCallback(
    (key: string, value: string) => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current)
        if (value) next.set(key, value)
        else next.delete(key)
        if (key !== 'cursor') next.delete('cursor')
        if (key !== 'page') next.delete('page')
        return next
      })
    },
    [setSearchParams],
  )

  const filters = useMemo(() => {
    const result: Record<string, string> = {}
    searchParams.forEach((value, key) => {
      if (!['search', 'ordering', 'cursor'].includes(key)) result[key] = value
    })
    return result
  }, [searchParams])

  const sort: SortState | undefined = ordering
    ? {
        key: ordering.replace(/^-/, ''),
        direction: ordering.startsWith('-') ? 'desc' : 'asc',
      }
    : undefined

  const setSort = useCallback(
    (next: SortState) => setParam('ordering', `${next.direction === 'desc' ? '-' : ''}${next.key}`),
    [setParam],
  )

  const reset = useCallback(() => {
    setSearchDraft('')
    setSearchParams(new URLSearchParams(), { replace: true })
  }, [setSearchParams])

  const queryParams: ListParams = useMemo(
    () => ({ ...filters, search: search || undefined, ordering: ordering || undefined, cursor: cursor || undefined }),
    [filters, search, ordering, cursor],
  )

  const hasFilters = Boolean(
    search || Object.keys(filters).some((key) => key !== 'page'),
  )

  return {
    queryParams,
    searchDraft,
    setSearchDraft,
    filters,
    setParam,
    sort,
    setSort,
    reset,
    hasFilters,
    cursor,
    setCursor: (value: string) => setParam('cursor', value),
    page: Number(searchParams.get('page') ?? '1') || 1,
    setPage: (value: number) => setParam('page', value > 1 ? String(value) : ''),
  }
}
