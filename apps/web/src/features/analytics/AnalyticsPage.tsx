/**
 * Analytics.
 *
 * The page renders whatever the server's catalogue contains — it has no list of
 * metrics of its own and no role checks. A Department Head and the CEO run the
 * identical component; the difference is entirely in what `/bi/metrics/`
 * returned and what each metric's `scope_label` says.
 *
 * Every figure is labelled with that scope. A department number presented
 * without saying so reads as an organisation number, and someone will
 * eventually quote it in a board meeting.
 */

import { useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card, CardHeader, PageHeader, Section, StatCard } from '@/components/ui/Card'
import { ChartFrame } from '@/components/ui/Chart'
import { CardSkeleton, EmptyState, ErrorState } from '@/components/ui/States'
import { Banner } from '@/components/ui/Misc'
import { Select } from '@/components/ui/Field'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { useMetric, useMetricCatalog } from '@/lib/analyticsQueries'
import { formatCurrency } from '@/lib/format'
import type { MetricResult, MetricSpec } from '@/lib/types'

const FAMILY_LABELS: Record<string, string> = {
  hr: 'Workforce',
  recruitment: 'Recruitment',
  finance: 'Payroll and finance',
  operations: 'Operations',
}

/** A stable palette so the same series keeps its colour across renders. */
const SERIES_COLORS = [
  'var(--chart-1, #4f46e5)',
  'var(--chart-2, #0891b2)',
  'var(--chart-3, #16a34a)',
  'var(--chart-4, #d97706)',
  'var(--chart-5, #dc2626)',
  'var(--chart-6, #7c3aed)',
]

function formatValue(value: number | string, unit: string): string {
  if (typeof value === 'string') return value
  if (unit === 'currency') return formatCurrency(value)
  if (unit === 'percent') return `${value}%`
  if (unit === 'days') return `${value} days`
  return new Intl.NumberFormat().format(value)
}

function RANGES() {
  const today = new Date()
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  const back = (days: number) => {
    const d = new Date(today)
    d.setDate(d.getDate() - days)
    return iso(d)
  }
  return [
    { value: '90', label: 'Last 90 days', start: back(90), end: iso(today) },
    { value: '180', label: 'Last 6 months', start: back(180), end: iso(today) },
    { value: '365', label: 'Last 12 months', start: back(365), end: iso(today) },
    { value: '730', label: 'Last 24 months', start: back(730), end: iso(today) },
  ]
}

/* ------------------------------------------------------------ one metric */

function ScalarCard({ result }: { result: MetricResult }) {
  const point = result.points[0]
  const context = point?.context ?? {}
  const hint = Object.entries(context)
    .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`)
    .join(' · ')

  return (
    <StatCard
      label={result.label}
      value={point ? formatValue(point.value, result.unit) : '—'}
      hint={hint || result.scope_label}
      tone={result.unit === 'currency' ? 'brand' : 'neutral'}
    />
  )
}

function BreakdownChart({ result }: { result: MetricResult }) {
  const data = result.points.map((p) => ({ name: p.label, value: Number(p.value) || 0 }))
  if (data.length === 0) return <EmptyState title="No data in range" />

  return (
    <ChartFrame
      title={result.label}
      data={data}
      formatValue={(v) => formatValue(v, result.unit)}
      height={256}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e5e7eb)" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} interval={0} angle={-15} textAnchor="end" height={54} />
          <YAxis tick={{ fontSize: 11 }} width={56} />
          <Tooltip formatter={(value: number) => formatValue(value, result.unit)} />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((_, index) => (
              <Cell key={index} fill={SERIES_COLORS[index % SERIES_COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}

function SeriesChart({ result }: { result: MetricResult }) {
  const data = result.points.map((p) => ({ name: p.label, value: Number(p.value) || 0 }))
  if (data.length === 0) return <EmptyState title="No data in range" />

  return (
    <ChartFrame
      title={result.label}
      data={data}
      formatValue={(v) => formatValue(v, result.unit)}
      height={256}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line, #e5e7eb)" vertical={false} />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} width={56} />
          <Tooltip formatter={(value: number) => formatValue(value, result.unit)} />
          <Line
            type="monotone"
            dataKey="value"
            stroke={SERIES_COLORS[0]}
            strokeWidth={2}
            dot={{ r: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}

function MetricCard({ spec, range }: { spec: MetricSpec; range: { start: string; end: string } }) {
  const [groupBy, setGroupBy] = useState('')
  const query = useMetric(
    spec.key,
    spec.supports_range ? { ...range, group_by: groupBy } : { group_by: groupBy },
  )

  if (query.isLoading) return <CardSkeleton />
  if (query.isError) return <ErrorState error={query.error} compact onRetry={() => void query.refetch()} />
  if (!query.data) return null

  const result = query.data
  const grouped = Boolean(groupBy)
  const shape = grouped ? 'breakdown' : result.shape

  return (
    <Card>
      <CardHeader
        title={result.label}
        description={spec.description}
        action={
          spec.groupings.length > 0 ? (
            <Select
              label=""
              aria-label={`Group ${result.label} by`}
              value={groupBy}
              onChange={(event) => setGroupBy(event.target.value)}
              options={[
                { value: '', label: 'No grouping' },
                ...spec.groupings.map((g) => ({
                  value: g,
                  label: `By ${g.replace(/_/g, ' ')}`,
                })),
              ]}
            />
          ) : undefined
        }
      />

      <div className="mt-3">
        {shape === 'scalar' ? (
          <p className="tabular text-3xl font-semibold tracking-tight text-ink">
            {result.points[0] ? formatValue(result.points[0].value, result.unit) : '—'}
          </p>
        ) : shape === 'series' ? (
          <SeriesChart result={result} />
        ) : (
          <BreakdownChart result={result} />
        )}
      </div>

      {/* The server's own description of the breadth it applied. Stating it is
          what stops a departmental figure being read as an org-wide one. */}
      <p className="mt-3 text-xs text-ink-subtle">{result.scope_label}</p>
    </Card>
  )
}

/* ------------------------------------------------------------------ page */

export function AnalyticsPage() {
  const permissions = usePermissions()
  const canView = permissions.can(RESOURCE.REPORT, ACTION.VIEW)
  const catalog = useMetricCatalog(canView)

  const ranges = useMemo(RANGES, [])
  const [rangeKey, setRangeKey] = useState('365')
  const range = ranges.find((r) => r.value === rangeKey) ?? ranges[2]

  const [family, setFamily] = useState('')

  if (!canView) {
    return (
      <>
        <PageHeader title="Analytics" />
        <EmptyState
          title="You do not have access to reporting"
          description="Ask your administrator if you need it. This is enforced by the server, not by this screen."
        />
      </>
    )
  }

  if (catalog.isLoading) return <CardSkeleton />
  if (catalog.isError) {
    return <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />
  }

  const specs = catalog.data?.metrics ?? []
  const families = catalog.data?.families ?? []
  const visible = family ? specs.filter((s) => s.family === family) : specs

  const scalars = visible.filter((s) => s.shape === 'scalar')
  const charts = visible.filter((s) => s.shape !== 'scalar')

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Every figure is scoped to what you are permitted to see."
        actions={
          <div className="flex flex-wrap gap-2">
            <Select
              label=""
              aria-label="Family"
              value={family}
              onChange={(event) => setFamily(event.target.value)}
              options={[
                { value: '', label: 'All areas' },
                ...families.map((f) => ({ value: f, label: FAMILY_LABELS[f] ?? f })),
              ]}
            />
            <Select
              label=""
              aria-label="Date range"
              value={rangeKey}
              onChange={(event) => setRangeKey(event.target.value)}
              options={ranges.map((r) => ({ value: r.value, label: r.label }))}
            />
          </div>
        }
      />

      {specs.length === 0 ? (
        <EmptyState
          title="No metrics available to you"
          description="Your role does not include any reportable resources."
        />
      ) : null}

      {permissions.isReadOnly ? (
        <Banner tone="info" title="Read-only view">
          You can read and export everything here. No control on this page changes data.
        </Banner>
      ) : null}

      {scalars.length > 0 ? (
        <Section title="Summary">
          <div className="grid-stats">
            {scalars.map((spec) => (
              <ScalarMetric key={spec.key} spec={spec} range={range} />
            ))}
          </div>
        </Section>
      ) : null}

      {charts.length > 0 ? (
        <Section title="Detail">
          <div className="grid gap-4 lg:grid-cols-2">
            {charts.map((spec) => (
              <MetricCard key={spec.key} spec={spec} range={range} />
            ))}
          </div>
        </Section>
      ) : null}

      {scalars.length > 0 ? (
        <Section title="Grouped views" description="Break a summary figure down by dimension.">
          <div className="grid gap-4 lg:grid-cols-2">
            {scalars
              .filter((spec) => spec.groupings.length > 0)
              .map((spec) => (
                <MetricCard key={`${spec.key}:grouped`} spec={spec} range={range} />
              ))}
          </div>
        </Section>
      ) : null}
    </>
  )
}

function ScalarMetric({
  spec,
  range,
}: {
  spec: MetricSpec
  range: { start: string; end: string }
}) {
  const query = useMetric(spec.key, spec.supports_range ? range : {})
  if (query.isLoading) return <CardSkeleton />
  if (query.isError || !query.data) {
    return <StatCard label={spec.label} value="—" hint="Unavailable" />
  }
  return <ScalarCard result={query.data} />
}
