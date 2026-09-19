/**
 * What this deployment sells.
 *
 * READ ONLY, and the API is read-only too — this page could not edit a plan if
 * it tried. The reason is worth knowing before someone adds a form here: a
 * plan is referenced by live subscriptions, so an endpoint that could delete
 * one would take customers with it, and one that could quietly retune
 * `employee_limit` would change what every subscriber is entitled to without a
 * single audit row naming any of them. Plans are created by `manage.py
 * seed_plans`, where the change is a deliberate, reviewable act.
 *
 * `subscriber_count` is the number that makes the screen useful: it says which
 * plans are actually in use, and therefore which ones a change would reach.
 */

import { Card, PageHeader } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { humanize } from '@/lib/format'
import { usePlatformPlans } from './queries'
import type { PlatformPlan } from './types'

export function PlatformPlansPage() {
  const query = usePlatformPlans()

  const columns: Array<Column<PlatformPlan>> = [
    {
      key: 'name',
      header: 'Plan',
      render: (row) => (
        <span className="block min-w-0">
          <span className="block truncate font-medium text-ink">{row.name}</span>
          <span className="block truncate text-xs text-ink-subtle">{row.code}</span>
        </span>
      ),
    },
    {
      key: 'seats',
      header: 'Seats',
      align: 'right',
      render: (row) => (row.employee_limit === null ? 'Unlimited' : row.employee_limit),
    },
    {
      key: 'features',
      header: 'Features',
      secondary: true,
      render: (row) => (
        <span className="flex flex-wrap gap-1">
          {row.enabled_features.map((feature) => (
            <Badge key={feature} tone="neutral">
              {humanize(feature)}
            </Badge>
          ))}
        </span>
      ),
    },
    {
      key: 'support',
      header: 'Support',
      secondary: true,
      render: (row) => humanize(row.support_level),
    },
    {
      key: 'subscribers',
      header: 'Subscribers',
      align: 'right',
      render: (row) => <span className="tabular-nums">{row.subscriber_count}</span>,
    },
  ]

  return (
    <>
      <PageHeader
        title="Plans"
        description="Read-only. Plans are seeded by a management command, because changing one changes what every subscriber is entitled to."
      />

      {query.error ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        <Card padded={false}>
          <DataTable
            columns={columns}
            rows={query.data?.data ?? []}
            rowKey={(row) => row.id}
            isLoading={query.isLoading}
            loadingState={<TableSkeleton columns={5} />}
            emptyState={
              <EmptyState
                title="No plans"
                description="This deployment sells nothing yet. Run seed_plans to create the starting set."
              />
            }
          />
        </Card>
      )}
    </>
  )
}
