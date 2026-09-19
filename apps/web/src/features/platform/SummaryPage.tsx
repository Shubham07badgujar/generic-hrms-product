/**
 * The console's first screen: how many customers, and what state they are in.
 *
 * Counts only. There is nothing here about a person, and that is structural
 * rather than a choice made on this page — the platform principal holds no
 * grant in any organization, so every employee, payslip and candidate queryset
 * resolves to nothing for it and the routes behind them are refused outright.
 * What an operator legitimately needs is the shape of the business: how many
 * organizations exist, how many are live, and where the rest are stuck.
 *
 * `pending_setup` is the number worth looking at every morning: those are
 * customers who were provisioned and have not finished the wizard, which is
 * where a new customer silently stalls.
 */

import { Link } from 'react-router-dom'
import { Card, PageHeader, Section, StatCard } from '@/components/ui/Card'
import { QueryBoundary } from '@/components/ui/States'
import { usePlatformSummary } from './queries'
import { ORGANIZATION_STATUSES, ORGANIZATION_STATUS_LABELS } from './status'

export function PlatformSummaryPage() {
  const summary = usePlatformSummary()

  return (
    <>
      <PageHeader
        title="Overview"
        description="Every organization on this deployment, by state."
      />

      <QueryBoundary
        isLoading={summary.isLoading}
        error={summary.error}
        data={summary.data}
        onRetry={() => void summary.refetch()}
      >
        {(data) => (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <StatCard label="Organizations" value={data.organizations_total} />
              <StatCard
                label="Operational"
                value={data.organizations_operational}
                hint="Pending setup, trial and active. The rest are stopped."
              />
            </div>

            <Section
              title="By status"
              description="A status with no organizations is still listed, so a zero is a fact rather than a gap."
            >
              <Card padded={false}>
                <ul className="divide-y divide-line">
                  {ORGANIZATION_STATUSES.map((status) => (
                    <li
                      key={status}
                      className="flex items-center justify-between gap-4 px-5 py-3"
                    >
                      <Link
                        to={`/platform/organizations?status=${status}`}
                        className="text-sm font-medium text-ink hover:text-brand hover:underline"
                      >
                        {ORGANIZATION_STATUS_LABELS[status]}
                      </Link>
                      <span className="text-sm tabular-nums text-ink-muted">
                        {data.by_status[status] ?? 0}
                      </span>
                    </li>
                  ))}
                </ul>
              </Card>
            </Section>
          </>
        )}
      </QueryBoundary>
    </>
  )
}
