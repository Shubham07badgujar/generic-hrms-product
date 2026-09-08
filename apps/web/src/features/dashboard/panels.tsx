/**
 * Dashboard panels.
 *
 * Every panel here gates itself on a permission and renders nothing when the
 * viewer lacks it. That is what lets six dashboards be compositions of the
 * SAME components rather than six variants of them — an Admin and a Manager
 * mounting `<PayrollPanel />` get different results because the server gave
 * them different grants, not because the panel checked their role.
 *
 * No panel in this file mentions a role.
 */

import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card, CardHeader, StatCard } from '@/components/ui/Card'
import { ChartFrame } from '@/components/ui/Chart'
import { CardSkeleton, EmptyState } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Muted } from '@/components/ui/Misc'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { useMetric, useMetricCatalog } from '@/lib/analyticsQueries'
import { useNotifications } from '@/hooks/useNotifications'
import { usePayrollRuns, useMyPayroll } from '@/lib/payrollQueries'
import { useExits, useMyClearanceItems } from '@/lib/exitQueries'
import { useOnboardings, usePendingProbationReviews } from '@/lib/lifecycleQueries'
import { formatCurrency, formatDate, formatRelative, humanize } from '@/lib/format'
import type { MetricResult } from '@/lib/types'

/**
 * A small tinted icon chip beside a panel title. Emoji keeps this dependency-
 * free; the tinted square gives every panel a recognisable identity at a
 * glance without shouting.
 */
export function PanelTitle({
  icon,
  tone = 'brand',
  children,
}: {
  icon: string
  tone?: 'brand' | 'success' | 'warning' | 'info' | 'danger' | 'neutral'
  children: ReactNode
}) {
  const tints: Record<string, string> = {
    brand: 'bg-brand-soft',
    success: 'bg-success-soft',
    warning: 'bg-warning-soft',
    info: 'bg-info-soft',
    danger: 'bg-danger-soft',
    neutral: 'bg-surface-muted',
  }
  return (
    <span className="flex items-center gap-2.5">
      <span
        aria-hidden
        className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg text-base ${tints[tone]}`}
      >
        {icon}
      </span>
      <span>{children}</span>
    </span>
  )
}

export function PanelLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="text-sm font-medium text-brand hover:underline">
      {children}
    </Link>
  )
}

function formatMetric(value: number | string, unit: string): string {
  if (typeof value === 'string') return value
  if (unit === 'currency') return formatCurrency(value)
  if (unit === 'percent') return `${value}%`
  if (unit === 'days') return `${value} d`
  return new Intl.NumberFormat().format(value)
}

/* =========================================================== BI metrics */

/**
 * Scalar metrics from the Phase 10 catalogue.
 *
 * The catalogue arrives already filtered to what the caller may see, so this
 * renders whatever came back and holds no list of its own. `scope_label` is
 * printed on each card: a departmental figure shown without saying so reads as
 * an organisation figure.
 */
export function MetricStrip({ limit = 4 }: { limit?: number }) {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.REPORT, ACTION.VIEW)
  const catalog = useMetricCatalog(enabled)

  if (!enabled) return null
  if (catalog.isLoading) return <CardSkeleton />

  const scalars = (catalog.data?.metrics ?? [])
    .filter((spec) => spec.shape === 'scalar')
    .slice(0, limit)

  if (scalars.length === 0) return null

  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {scalars.map((spec) => (
        <MetricTile key={spec.key} metricKey={spec.key} label={spec.label} />
      ))}
    </div>
  )
}

function MetricTile({ metricKey, label }: { metricKey: string; label: string }) {
  const query = useMetric(metricKey)

  if (query.isLoading) return <CardSkeleton />
  if (query.isError || !query.data) {
    // One unavailable tile must not blank the row.
    return <StatCard label={label} value="—" hint="Unavailable" />
  }

  const result: MetricResult = query.data
  const point = result.points[0]

  return (
    <StatCard
      label={result.label}
      value={point ? formatMetric(point.value, result.unit) : '—'}
      hint={result.scope_label}
      tone={result.unit === 'currency' ? 'brand' : 'neutral'}
    />
  )
}

/** A trend or breakdown chart for one metric, when the caller may see it. */
export function MetricChartPanel({
  metricKey,
  description,
}: {
  metricKey: string
  description?: string
}) {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.REPORT, ACTION.VIEW)
  const catalog = useMetricCatalog(enabled)
  const available = (catalog.data?.metrics ?? []).some((spec) => spec.key === metricKey)
  const query = useMetric(metricKey, {}, enabled && available)

  // Absent from the catalogue means the server will refuse it too. Rendering
  // nothing is the honest outcome, not an empty chart.
  if (!enabled || (catalog.isFetched && !available)) return null
  if (query.isLoading || catalog.isLoading) return <CardSkeleton />
  if (query.isError || !query.data) return null

  const result: MetricResult = query.data
  const data = result.points.map((p) => ({ name: p.label, value: Number(p.value) || 0 }))

  return (
    <Card className="space-y-3">
      <CardHeader title={result.label} description={description} />
      {data.length === 0 ? (
        <EmptyState compact title="No data yet" />
      ) : (
        <ChartFrame
          title={result.label}
          data={data}
          formatValue={(v) => formatMetric(v, result.unit)}
          height={224}
        >
          <ResponsiveContainer width="100%" height="100%">
            {result.shape === 'series' ? (
              <LineChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e5e7eb)" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} width={52} />
                <Tooltip formatter={(v: number) => formatMetric(v, result.unit)} />
                <Line type="monotone" dataKey="value" stroke="var(--chart-1, #4f46e5)" strokeWidth={2} dot={{ r: 2 }} />
              </LineChart>
            ) : (
              <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e5e7eb)" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} width={52} />
                <Tooltip formatter={(v: number) => formatMetric(v, result.unit)} />
                <Bar dataKey="value" fill="var(--chart-1, #4f46e5)" radius={[4, 4, 0, 0]} />
              </BarChart>
            )}
          </ResponsiveContainer>
        </ChartFrame>
      )}
      <p className="text-xs text-ink-subtle">{result.scope_label}</p>
    </Card>
  )
}

/* ======================================================== action centre */

/**
 * What is blocked on this person right now.
 *
 * Notifications are already per-recipient and prioritised server-side, so this
 * needs no filtering rule of its own — it shows the CRITICAL and HIGH ones,
 * which are precisely the events that mean someone else is waiting.
 */
export function ActionCentrePanel() {
  const { available, items } = useNotifications()
  if (!available) return null

  const needsAction = items.filter(
    (item) => !item.isRead && (item.priority === 'critical' || item.priority === 'high'),
  )

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="⚡" tone="warning">Needs your attention</PanelTitle>}
        description="Things other people are waiting on."
        action={<PanelLink to="/settings/notifications">Settings</PanelLink>}
      />
      {needsAction.length === 0 ? (
        <EmptyState compact title="Nothing is blocked on you" />
      ) : (
        <ul className="divide-y divide-line">
          {needsAction.slice(0, 5).map((item) => {
            const row = (
              <div className="flex items-start gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{item.title}</p>
                  {item.body ? (
                    <p className="truncate text-xs text-ink-muted">{item.body}</p>
                  ) : null}
                  <p className="text-xs text-ink-subtle">{formatRelative(item.createdAt)}</p>
                </div>
                <Badge tone={item.priority === 'critical' ? 'danger' : 'warning'}>
                  {item.priority === 'critical' ? 'Action required' : 'Important'}
                </Badge>
              </div>
            )
            return (
              <li key={item.id}>
                {item.link ? (
                  <Link to={item.link} className="block hover:bg-canvas">
                    {row}
                  </Link>
                ) : (
                  row
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

/* ============================================================== payroll */

const RUN_TONES: Record<string, Tone> = {
  draft: 'neutral',
  processing: 'info',
  review: 'warning',
  approved: 'brand',
  paid: 'success',
  reversed: 'danger',
}

export function PayrollPanel() {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.PAYROLL_RUN, ACTION.VIEW)
  const query = usePayrollRuns({ ordering: '-period_year' })

  if (!enabled) return null
  const rows = query.data?.data ?? []

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="💰" tone="success">Payroll</PanelTitle>}
        description="Recent runs and where each one stands."
        action={<PanelLink to="/payroll">Open payroll</PanelLink>}
      />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="No payroll runs yet" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 4).map((run) => (
            <li key={run.id}>
              <Link to={`/payroll/${run.id}`} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">
                    {run.period_year}-{String(run.period_month).padStart(2, '0')}
                    {run.run_type !== 'regular' ? <Muted> · {humanize(run.run_type)}</Muted> : null}
                  </p>
                  <p className="truncate text-xs text-ink-muted">
                    {run.payslip_count} payslips · net {formatCurrency(run.totals?.net_pay)}
                  </p>
                </div>
                <Badge tone={RUN_TONES[run.status] ?? 'neutral'}>{humanize(run.status)}</Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/** Self-service pay. Everyone holds PAYSLIP/VIEW at SELF, so this is universal. */
export function MyPayPanel() {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.PAYSLIP, ACTION.VIEW)
  const query = useMyPayroll()

  if (!enabled) return null
  if (query.isLoading) return <CardSkeleton />

  const payslips = query.data?.payslips ?? []
  const latest = payslips[0]

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="💸" tone="success">My pay</PanelTitle>}
        description="Your most recent payslip."
        action={<PanelLink to="/payslips">All payslips</PanelLink>}
      />
      {!latest ? (
        <EmptyState
          compact
          title="No payslips yet"
          description="They appear once payroll has been processed for a period."
        />
      ) : (
        <Link to={`/payslips/${latest.id}`} className="block rounded-lg p-3 hover:bg-canvas">
          <div className="flex items-baseline justify-between gap-3">
            <p className="text-sm text-ink-muted">
              {latest.period_year}-{String(latest.period_month).padStart(2, '0')}
            </p>
            <Badge tone={latest.run_status === 'paid' ? 'success' : 'neutral'}>
              {humanize(latest.run_status)}
            </Badge>
          </div>
          <p className="tabular mt-1 text-2xl font-semibold text-ink">
            {formatCurrency(latest.net_pay)}
          </p>
          <p className="text-xs text-ink-subtle">
            Gross {formatCurrency(latest.gross_earnings)} · deductions{' '}
            {formatCurrency(latest.total_deductions)}
          </p>
        </Link>
      )}
    </Card>
  )
}

/* ========================================================== offboarding */

/** Stages at which an exit is over. A finished exit is not "in progress". */
const CLOSED_EXIT_STAGES = new Set(['completed', 'cancelled'])

export function ExitsPanel() {
  const permissions = usePermissions()
  const enabled = permissions.canAtLeast(RESOURCE.OFFBOARDING, ACTION.VIEW, 'department')
  const query = useExits({})

  if (!enabled) return null
  const rows = (query.data?.data ?? []).filter((exit) => !CLOSED_EXIT_STAGES.has(exit.stage))

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="🚪" tone="neutral">Exits in progress</PanelTitle>}
        description="People working notice or in clearance."
        action={<PanelLink to="/offboarding">Open offboarding</PanelLink>}
      />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="No exits in progress" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 5).map((exit) => (
            <li key={exit.id}>
              <Link to={`/offboarding/${exit.id}`} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">{exit.employee_name}</p>
                  <p className="truncate text-xs text-ink-muted">
                    Last day {formatDate(exit.expected_last_working_date)}
                  </p>
                </div>
                <Badge tone="warning">{humanize(exit.stage)}</Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/**
 * Clearance items assigned to this person.
 *
 * Universal: anyone can own a clearance gate, and an exit cannot complete while
 * one is open — so the blocker needs to see it wherever they land.
 */
export function MyClearancePanel() {
  const query = useMyClearanceItems()
  const items = query.data ?? []

  if (items.length === 0) return null

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="🧳" tone="neutral">Exit clearance assigned to you</PanelTitle>}
        description="An exit cannot complete until these are cleared."
      />
      <ul className="divide-y divide-line">
        {items.slice(0, 5).map((item) => (
          <li key={item.id} className="flex items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink">{item.title}</p>
              <p className="truncate text-xs text-ink-muted">
                Due {formatDate(item.due_date)}
              </p>
            </div>
            <Badge tone={item.is_overdue ? 'danger' : 'warning'}>
              {item.is_overdue ? 'Overdue' : 'Open'}
            </Badge>
          </li>
        ))}
      </ul>
    </Card>
  )
}

/* =========================================================== onboarding */

export function OnboardingPanel() {
  const permissions = usePermissions()
  const enabled = permissions.canAtLeast(RESOURCE.ONBOARDING, ACTION.VIEW, 'department')
  const query = useOnboardings({})

  if (!enabled) return null
  const rows = (query.data?.data ?? []).filter((row) => row.status !== 'completed')

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="🌱" tone="success">Onboarding in progress</PanelTitle>}
        description="New joiners who have not finished their checklist."
        action={<PanelLink to="/onboarding">Open onboarding</PanelLink>}
      />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="Nobody is mid-onboarding" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 5).map((row) => (
            <li key={row.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">{row.employee_name}</p>
                <p className="truncate text-xs text-ink-muted">
                  {row.completed_count}/{row.total_count} complete
                  {row.outstanding_mandatory_count > 0
                    ? ` · ${row.outstanding_mandatory_count} mandatory outstanding`
                    : ''}
                </p>
              </div>
              <Badge tone="info">{humanize(row.status)}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/**
 * Probation decisions waiting on someone.
 *
 * The system never auto-confirms, so an unreviewed probation lapses into an
 * employee whose status nobody decided. Surfacing it is the whole point.
 */
export function ProbationPanel() {
  const permissions = usePermissions()
  const enabled = permissions.can(RESOURCE.PROBATION_REVIEW, ACTION.DECIDE)
  const query = usePendingProbationReviews(enabled)

  if (!enabled) return null
  const rows = query.data?.data ?? []

  return (
    <Card className="space-y-4">
      <CardHeader
        title={<PanelTitle icon="⏳" tone="info">Probation decisions due</PanelTitle>}
        description="These never resolve on their own — confirm, extend or terminate."
        action={<PanelLink to="/onboarding">Review</PanelLink>}
      />
      {query.isLoading ? (
        <CardSkeleton />
      ) : rows.length === 0 ? (
        <EmptyState compact title="No probation reviews pending" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.slice(0, 5).map((review) => (
            <li key={review.id}>
              <Link
                to={`/employees/${review.employee}`}
                className="flex items-center gap-3 py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">
                    {review.employee_name}
                  </p>
                  <p className="truncate text-xs text-ink-muted">
                    Ends {formatDate(review.probation_end_date)}
                  </p>
                </div>
                <Badge tone="warning">Decide</Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
