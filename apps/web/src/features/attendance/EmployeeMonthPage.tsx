/**
 * One employee's month as a calendar — HR's drill-down from the day roster.
 *
 * Every cell shows exactly one resolved kind (the server decides: record >
 * leave > holiday > week off), so the grid, the legend and the summary can
 * never tell three different stories.
 */

import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { Card, PageHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States'
import { useMonthCalendar, type MonthCalendarDay } from '@/lib/attendanceQueries'
import type { UUID } from '@/lib/types'

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

const MONTHS = [
  '', 'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** Cell paint per kind — background, text, and the little dot. */
const KIND_STYLE: Record<MonthCalendarDay['kind'], { bg: string; text: string; dot: string; label: string }> = {
  present: { bg: 'bg-success-soft', text: 'text-success-ink', dot: '#22a06b', label: 'Present' },
  absent: { bg: 'bg-danger-soft', text: 'text-danger-ink', dot: '#d64545', label: 'Absent' },
  half_day: { bg: 'bg-warning-soft', text: 'text-warning-ink', dot: '#d97917', label: 'Half day' },
  leave: { bg: 'bg-info-soft', text: 'text-info-ink', dot: '#2f7cd6', label: 'Leave' },
  holiday: { bg: 'bg-brand-soft', text: 'text-brand', dot: '#232B7C', label: 'Holiday' },
  week_off: { bg: 'bg-canvas', text: 'text-ink-subtle', dot: '#9aa0ab', label: 'Week off' },
  no_record: { bg: 'bg-surface', text: 'text-ink-subtle', dot: '#c9ced6', label: 'No record' },
  not_employed: { bg: 'bg-canvas', text: 'text-ink-subtle', dot: 'transparent', label: 'Not employed' },
  future: { bg: 'bg-surface', text: 'text-ink-subtle', dot: 'transparent', label: '' },
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export function EmployeeMonthPage() {
  const { id } = useParams<{ id: UUID }>()
  const [params, setParams] = useSearchParams()
  const now = new Date()
  const initial = params.get('month') ?? `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  const [cursor, setCursor] = useState(initial)
  const [year, month] = cursor.split('-').map(Number)

  const query = useMonthCalendar(id, year, month)

  function move(delta: number) {
    const index = year * 12 + (month - 1) + delta
    const nextYear = Math.floor(index / 12)
    const nextMonth = (index % 12) + 1
    const next = `${nextYear}-${String(nextMonth).padStart(2, '0')}`
    setCursor(next)
    setParams({ month: next }, { replace: true })
  }

  if (query.isLoading) return <LoadingBlock label="Loading the month" />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (!query.data) return <EmptyState title="No attendance data" />

  const data = query.data
  const summary = data.summary
  // Monday-first grid: pad the front with blanks up to the 1st's weekday.
  const firstWeekday = (new Date(year, month - 1, 1).getDay() + 6) % 7

  const chips: Array<[string, string | number]> = [
    ['Working days', summary.working_days],
    ['Present', summary.present_days],
    ['Absent', summary.absent_days],
    ['Half days', summary.half_days],
    ['Late', summary.late_days],
    ['Leave', summary.leave_days],
    ['Week offs', summary.week_offs],
    ['Holidays', summary.holidays],
    ...(summary.no_record_days ? [['No record', summary.no_record_days] as [string, number]] : []),
  ]

  return (
    <>
      <PageHeader
        breadcrumbs={
          <nav aria-label="Breadcrumb" className="text-xs text-ink-subtle">
            <Link to="/attendance" className="hover:text-ink hover:underline">Attendance</Link>
            <span className="px-1.5">/</span>
            <span className="text-ink-muted">{data.employee.full_name}</span>
          </nav>
        }
        title={
          <span className="flex items-center gap-2.5">
            <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-brand">
              <span aria-hidden className="block h-4 w-4 rounded-full border-[3px]"
                style={{ borderColor: LIME }} />
            </span>
            {data.employee.full_name}
          </span>
        }
        description={`${data.employee.employee_code}${data.employee.department ? ` · ${data.employee.department}` : ''} — monthly attendance`}
        actions={
          <span className="flex items-center gap-2">
            <Button size="sm" onClick={() => move(-1)} aria-label="Previous month">←</Button>
            <span className="min-w-[150px] text-center text-sm font-semibold text-ink">
              {MONTHS[month]} {year}
            </span>
            <Button size="sm" onClick={() => move(1)} aria-label="Next month">→</Button>
          </span>
        }
      />

      {/* ---- the monthly summary ---- */}
      <div className="mb-5 flex flex-wrap gap-2">
        {chips.map(([label, value]) => (
          <span key={label}
            className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-sm">
            <span className="text-ink-muted">{label}</span>
            <span className="font-semibold tabular text-ink">{value}</span>
          </span>
        ))}
      </div>

      {/* ---- the calendar ---- */}
      <Card>
        <div className="grid grid-cols-7 gap-1.5">
          {WEEKDAYS.map((day) => (
            <div key={day}
              className="pb-1 text-center text-xs font-semibold uppercase tracking-wide text-ink-muted">
              {day}
            </div>
          ))}
          {Array.from({ length: firstWeekday }).map((_, index) => (
            <div key={`pad-${index}`} />
          ))}
          {data.days.map((day) => {
            const style = KIND_STYLE[day.kind]
            const dayNumber = Number(day.date.slice(-2))
            return (
              <div
                key={day.date}
                title={[day.label, day.first_in && `In ${day.first_in}`, day.last_out && `Out ${day.last_out}`]
                  .filter(Boolean).join(' · ') || undefined}
                className={`min-h-[76px] rounded-lg border border-line p-1.5 ${style.bg}`}
              >
                <div className="flex items-center justify-between">
                  <span className={`text-xs font-semibold ${style.text}`}>{dayNumber}</span>
                  {day.is_late && (
                    <span className="rounded bg-warning-soft px-1 text-[10px] font-semibold uppercase text-warning-ink">
                      Late
                    </span>
                  )}
                </div>
                {day.kind !== 'future' && (
                  <p className={`mt-1 truncate text-[11px] leading-tight ${style.text}`}>
                    {day.label || style.label}
                  </p>
                )}
                {day.first_in && (
                  <p className="mt-0.5 text-[10px] tabular text-ink-subtle">
                    {day.first_in}{day.last_out ? `–${day.last_out}` : ''}
                  </p>
                )}
              </div>
            )
          })}
        </div>

        {/* ---- legend ---- */}
        <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1.5 border-t border-line pt-3">
          {(Object.keys(KIND_STYLE) as Array<MonthCalendarDay['kind']>)
            .filter((kind) => kind !== 'future' && kind !== 'not_employed')
            .map((kind) => (
              <span key={kind} className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
                <span aria-hidden className="h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: KIND_STYLE[kind].dot }} />
                {KIND_STYLE[kind].label}
              </span>
            ))}
          <span className="inline-flex items-center gap-1.5 text-xs text-ink-muted">
            <Badge tone="warning">Late</Badge> shown on the day it happened
          </span>
        </div>
      </Card>
    </>
  )
}
