/** Display formatting. One place, so dates and money never drift between screens. */

import { format, formatDistanceToNowStrict, isValid, parseISO } from 'date-fns'

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null
  const date = typeof value === 'string' ? parseISO(value) : value
  return isValid(date) ? date : null
}

export function formatDate(value: string | Date | null | undefined, fallback = '—') {
  const date = toDate(value)
  return date ? format(date, 'd MMM yyyy') : fallback
}

export function formatDateTime(value: string | Date | null | undefined, fallback = '—') {
  const date = toDate(value)
  return date ? format(date, 'd MMM yyyy, HH:mm') : fallback
}

export function formatTime(value: string | Date | null | undefined, fallback = '—') {
  const date = toDate(value)
  return date ? format(date, 'HH:mm') : fallback
}

export function formatRelative(value: string | Date | null | undefined, fallback = '—') {
  const date = toDate(value)
  if (!date) return fallback
  return `${formatDistanceToNowStrict(date)} ago`
}

/** For `datetime-local` inputs, which want `YYYY-MM-DDTHH:mm` in local time. */
export function toDateTimeLocal(value: string | Date | null | undefined) {
  const date = toDate(value)
  return date ? format(date, "yyyy-MM-dd'T'HH:mm") : ''
}

export function toDateInput(value: string | Date | null | undefined) {
  const date = toDate(value)
  return date ? format(date, 'yyyy-MM-dd') : ''
}

/** Money is served as a decimal STRING to avoid float drift; keep it that way. */
export function formatCurrency(value: string | number | null | undefined, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback
  const amount = typeof value === 'string' ? Number(value) : value
  if (Number.isNaN(amount)) return fallback
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(amount)
}

/** `recommend_reject` → `Recommend reject`. Used for enum values without labels. */
export function humanize(value: string | null | undefined, fallback = '—') {
  if (!value) return fallback
  const spaced = value.replace(/[_-]+/g, ' ').trim()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

export function initials(name: string | null | undefined) {
  if (!name) return '?'
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase()
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase()
}

export function pluralize(count: number, singular: string, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`
}
