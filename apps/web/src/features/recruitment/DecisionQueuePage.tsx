/**
 * HR Head's decision queue.
 *
 * Backed by `/applications/pending-hr-decision/`, which returns applications
 * sitting at a stage flagged `is_final_hr_decision`. The route itself is gated
 * on APPLICATION/VIEW, but the sidebar entry that leads here is gated on
 * APPLICATION/REJECT — so it surfaces for the one person whose job it is,
 * while remaining readable by anyone the API already lets see applications.
 */

import { Link } from 'react-router-dom'
import { Card, PageHeader, Section } from '@/components/ui/Card'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge } from '@/components/ui/Badge'
import { Avatar, Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { useHrDecisionQueue } from '@/lib/queries'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { formatDate } from '@/lib/format'

export function DecisionQueuePage() {
  const permissions = usePermissions()
  const mayDecide = permissions.can(RESOURCE.APPLICATION, ACTION.REJECT)
  const queue = useHrDecisionQueue(true)
  const rows = queue.data?.data ?? []

  return (
    <>
      <PageHeader
        title="Awaiting a final decision"
        description="Candidates whose department review is complete and who are waiting on HR."
      />

      {mayDecide ? (
        <Banner tone="warning" title="Only you can close these">
          Selecting or rejecting here is terminal. A rejection requires a written reason of at least
          20 characters, stored permanently against the candidate and visible in their history.
        </Banner>
      ) : (
        <Banner tone="info" title="View only">
          The final selection and rejection belong to the HR Head. You can follow the queue but not
          act on it, and the server enforces that independently of this screen.
        </Banner>
      )}

      <Section>
        {queue.isError ? (
          <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />
        ) : queue.isLoading ? (
          <Card padded={false}>
            <TableSkeleton columns={3} rows={4} />
          </Card>
        ) : rows.length === 0 ? (
          <Card>
            <EmptyState
              title="Nothing waiting"
              description="No candidates are currently at a final-decision stage."
            />
          </Card>
        ) : (
          <div className="space-y-3">
            {rows.map((application) => (
              <Card key={application.id} className="flex flex-wrap items-center gap-4">
                <Avatar name={application.candidate_name} size="md" />
                <div className="min-w-0 flex-1">
                  <Link
                    to={`/recruitment/applications/${application.id}`}
                    className="text-sm font-semibold text-ink hover:text-brand hover:underline"
                  >
                    {application.candidate_name}
                  </Link>
                  <p className="text-xs text-ink-muted">
                    {application.job_title} · {application.department_name} · applied{' '}
                    {formatDate(application.applied_at)}
                  </p>
                </div>
                <Badge tone="warning">{application.stage_name}</Badge>
                <Link to={`/recruitment/applications/${application.id}`}>
                  <Button variant="primary" size="sm">
                    {mayDecide ? 'Review and decide' : 'Review'}
                  </Button>
                </Link>
              </Card>
            ))}
          </div>
        )}
      </Section>
    </>
  )
}
