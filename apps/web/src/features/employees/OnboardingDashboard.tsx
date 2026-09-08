/**
 * HR's onboarding and probation dashboard.
 *
 * EVERY FIGURE HERE COMES FROM AN ENDPOINT THAT RETURNS THE ROWS IT COUNTS.
 * The API uses cursor pagination and returns no totals, so nothing on this
 * page extrapolates one: a card either counts a list the server actually sent,
 * or it says the figure is a first page. The probation buckets and the overdue
 * checklist both come from purpose-built routes, so those counts are exact.
 */

import { Link } from 'react-router-dom'
import { Card, CardHeader, PageHeader, Section, StatCard } from '@/components/ui/Card'
import { CardSkeleton, EmptyState, ErrorState } from '@/components/ui/States'
import { Badge } from '@/components/ui/Badge'
import { Avatar, Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useEmployeeDocuments,
  useOnboardings,
  useOverdueOnboardings,
  usePendingProbationReviews,
  useUpcomingProbations,
} from '@/lib/lifecycleQueries'
import { useEmployees } from '@/lib/queries'
import { formatDate } from '@/lib/format'
import type { ProbationDue } from '@/lib/types'

function ProbationList({ title, rows, tone }: { title: string; rows: ProbationDue[]; tone: 'warning' | 'danger' | 'info' }) {
  if (!rows.length) return null
  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">{title}</p>
      <ul className="divide-y divide-line">
        {rows.map((row) => (
          <li key={row.id} className="flex items-center gap-3 py-2.5">
            <Avatar name={row.full_name} size="sm" />
            <div className="min-w-0 flex-1">
              <Link
                to={`/employees/${row.id}`}
                className="truncate text-sm font-medium text-ink hover:text-brand hover:underline"
              >
                {row.full_name}
              </Link>
              <p className="truncate text-xs text-ink-muted">
                {row.employee_code} · {row.department_name ?? 'No department'}
              </p>
            </div>
            <Badge tone={tone}>{formatDate(row.probation_end_date)}</Badge>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function OnboardingDashboard() {
  const permissions = usePermissions()

  const maySeeOnboarding = permissions.can(RESOURCE.ONBOARDING)
  const maySeeProbation = permissions.can(RESOURCE.PROBATION_REVIEW)
  const mayDecide = permissions.can(RESOURCE.PROBATION_REVIEW, ACTION.DECIDE)
  const mayVerify = permissions.can(RESOURCE.EMPLOYEE_DOCUMENT, ACTION.APPROVE)

  const inProgress = useOnboardings({ status: 'in_progress' })
  const overdue = useOverdueOnboardings(maySeeOnboarding)
  const upcoming = useUpcomingProbations(maySeeProbation)
  const pending = usePendingProbationReviews(maySeeProbation)
  const pendingDocuments = useEmployeeDocuments(mayVerify ? { status: 'pending' } : {})
  const newHires = useEmployees({ status: 'onboarding', ordering: '-date_of_joining' })

  const checklists = inProgress.data?.data ?? []
  const overdueRows = overdue.data ?? []
  const buckets = upcoming.data
  const pendingReviews = pending.data?.data ?? []
  const documents = pendingDocuments.data?.data ?? []

  return (
    <>
      <PageHeader
        title="Onboarding and probation"
        description="New joiners, outstanding checklist items, and probations that need a decision."
      />

      {mayDecide && (
        <Banner tone="info" title="Nothing is confirmed automatically">
          Probations move to "review due" on their own and stop there. Confirming, extending or
          terminating is always an explicit decision you record.
        </Banner>
      )}

      <Section>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Onboarding in progress"
            tone="brand"
            value={
              <>
                {checklists.length}
                {inProgress.data?.meta.next && <span className="text-ink-subtle">+</span>}
              </>
            }
            hint={inProgress.data?.meta.next ? 'First page — more beyond' : undefined}
          />
          <StatCard
            label="Checklists with overdue items"
            tone={overdueRows.length ? 'danger' : 'neutral'}
            value={overdueRows.length}
          />
          <StatCard
            label="Probation decisions due"
            tone={buckets?.overdue.length ? 'danger' : 'neutral'}
            value={buckets ? buckets.overdue.length : '—'}
            hint={buckets ? undefined : 'Not visible to you'}
          />
          <StatCard
            label="Documents awaiting verification"
            tone={documents.length ? 'warning' : 'neutral'}
            value={
              mayVerify ? (
                <>
                  {documents.length}
                  {pendingDocuments.data?.meta.next && <span className="text-ink-subtle">+</span>}
                </>
              ) : (
                '—'
              )
            }
            hint={mayVerify ? undefined : 'Not visible to you'}
          />
        </div>
      </Section>

      <div className="grid gap-5 lg:grid-cols-2">
        {maySeeOnboarding && (
          <Card className="space-y-4">
            <CardHeader
              title="Onboarding in progress"
              description="Checklists that are still open."
            />
            {inProgress.isLoading ? (
              <CardSkeleton />
            ) : inProgress.isError ? (
              <ErrorState error={inProgress.error} compact />
            ) : checklists.length === 0 ? (
              <EmptyState compact title="No open checklists" />
            ) : (
              <ul className="divide-y divide-line">
                {checklists.slice(0, 8).map((onboarding) => (
                  <li key={onboarding.id} className="flex items-center gap-3 py-2.5">
                    <Avatar name={onboarding.employee_name} size="sm" />
                    <div className="min-w-0 flex-1">
                      <Link
                        to={`/employees/${onboarding.employee}`}
                        className="truncate text-sm font-medium text-ink hover:text-brand hover:underline"
                      >
                        {onboarding.employee_name}
                      </Link>
                      <p className="truncate text-xs text-ink-muted">
                        joined {formatDate(onboarding.joining_date)} ·{' '}
                        {onboarding.department_name ?? 'No department'}
                      </p>
                    </div>
                    <span className="tabular shrink-0 text-xs text-ink-muted">
                      {onboarding.completed_count}/{onboarding.total_count}
                    </span>
                    {onboarding.outstanding_mandatory_count > 0 && (
                      <Badge tone="warning">
                        {onboarding.outstanding_mandatory_count} required
                      </Badge>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}

        {maySeeOnboarding && (
          <Card className="space-y-4">
            <CardHeader
              title="Overdue checklist items"
              description="Past their due date and still outstanding."
            />
            {overdue.isLoading ? (
              <CardSkeleton />
            ) : overdueRows.length === 0 ? (
              <EmptyState compact title="Nothing overdue" />
            ) : (
              <ul className="divide-y divide-line">
                {overdueRows.slice(0, 8).map((onboarding) => (
                  <li key={onboarding.id} className="py-2.5">
                    <Link
                      to={`/employees/${onboarding.employee}`}
                      className="text-sm font-medium text-ink hover:text-brand hover:underline"
                    >
                      {onboarding.employee_name}
                    </Link>
                    <ul className="mt-1 space-y-0.5">
                      {onboarding.items
                        .filter((item) => item.is_overdue)
                        .slice(0, 3)
                        .map((item) => (
                          <li key={item.id} className="text-xs text-danger-ink">
                            {item.title} — due {formatDate(item.due_date)}
                          </li>
                        ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}

        {maySeeProbation && (
          <Card className="space-y-4">
            <CardHeader
              title="Probation approaching"
              description="Grouped by how close the end date is."
            />
            {upcoming.isLoading ? (
              <CardSkeleton />
            ) : !buckets ? (
              <EmptyState compact title="Not visible to you" />
            ) : buckets.overdue.length + buckets.t_minus_7.length + buckets.t_minus_30.length ===
              0 ? (
              <EmptyState compact title="Nothing approaching" />
            ) : (
              <div className="space-y-4">
                <ProbationList title="Overdue" rows={buckets.overdue} tone="danger" />
                <ProbationList title="Within 7 days" rows={buckets.t_minus_7} tone="warning" />
                <ProbationList title="Within 30 days" rows={buckets.t_minus_30} tone="info" />
              </div>
            )}
          </Card>
        )}

        {maySeeProbation && (
          <Card className="space-y-4">
            <CardHeader
              title="Reviews awaiting a decision"
              description={
                mayDecide
                  ? 'Yours to confirm, extend or terminate.'
                  : 'HR records the decision on these.'
              }
            />
            {pending.isLoading ? (
              <CardSkeleton />
            ) : pendingReviews.length === 0 ? (
              <EmptyState compact title="No open reviews" />
            ) : (
              <ul className="divide-y divide-line">
                {pendingReviews.slice(0, 8).map((review) => (
                  <li key={review.id} className="flex items-center gap-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <Link
                        to={`/employees/${review.employee}`}
                        className="truncate text-sm font-medium text-ink hover:text-brand hover:underline"
                      >
                        {review.employee_name}
                      </Link>
                      <p className="truncate text-xs text-ink-muted">
                        ends {formatDate(review.probation_end_date)}
                        {review.recommendation !== 'pending'
                          ? ` · manager recommends ${review.recommendation}`
                          : ' · no assessment yet'}
                      </p>
                    </div>
                    <Link to={`/employees/${review.employee}`}>
                      <Button size="sm" variant={mayDecide ? 'primary' : 'secondary'}>
                        {mayDecide ? 'Decide' : 'Review'}
                      </Button>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}

        {mayVerify && (
          <Card className="space-y-4">
            <CardHeader
              title="Documents awaiting verification"
              description="Uploaded, not yet checked."
            />
            {pendingDocuments.isLoading ? (
              <CardSkeleton />
            ) : documents.length === 0 ? (
              <EmptyState compact title="Nothing awaiting verification" />
            ) : (
              <ul className="divide-y divide-line">
                {documents.slice(0, 8).map((document) => (
                  <li key={document.id} className="flex items-center gap-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <Link
                        to={`/employees/${document.employee}`}
                        className="truncate text-sm font-medium text-ink hover:text-brand hover:underline"
                      >
                        {document.employee_name}
                      </Link>
                      <p className="truncate text-xs text-ink-muted">
                        {document.document_type_name} · uploaded{' '}
                        {formatDate(document.uploaded_at)}
                      </p>
                    </div>
                    <Badge tone="warning">Pending</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}

        <Card className="space-y-4">
          <CardHeader title="Upcoming joiners" description="Employees still in onboarding." />
          {newHires.isLoading ? (
            <CardSkeleton />
          ) : (newHires.data?.data ?? []).length === 0 ? (
            <EmptyState compact title="No one is currently onboarding" />
          ) : (
            <ul className="divide-y divide-line">
              {(newHires.data?.data ?? []).slice(0, 8).map((hire) => (
                <li key={hire.id} className="flex items-center gap-3 py-2.5">
                  <Avatar name={hire.full_name} size="sm" />
                  <div className="min-w-0 flex-1">
                    <Link
                      to={`/employees/${hire.id}`}
                      className="truncate text-sm font-medium text-ink hover:text-brand hover:underline"
                    >
                      {hire.full_name}
                    </Link>
                    <p className="truncate text-xs text-ink-muted">
                      {hire.department_name ?? 'No department'} · joins{' '}
                      {formatDate(hire.date_of_joining)}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}
