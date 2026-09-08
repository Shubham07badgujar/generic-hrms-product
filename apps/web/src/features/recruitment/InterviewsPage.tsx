/**
 * Interviews.
 *
 * Scoped by INTERVIEWER server-side, so a Clinic Doctor at SELF scope sees the
 * interviews booked with them and no others. That is what "assigned candidates
 * only" means in practice, and it is why this page has no "mine / all" toggle:
 * the answer is already decided by the caller's scope.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader, Section } from '@/components/ui/Card'
import {
  CursorPager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, InterviewStatusBadge } from '@/components/ui/Badge'
import { Avatar, Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { FeedbackDrawer } from './FeedbackForm'
import { useInterviews, useRescheduleInterview } from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError } from '@/lib/api'
import { formatDateTime, humanize } from '@/lib/format'
import type { Interview } from '@/lib/types'

const STATUS_OPTIONS = [
  { value: 'scheduled', label: 'Scheduled' },
  { value: 'rescheduled', label: 'Rescheduled' },
  { value: 'completed', label: 'Completed' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'no_show', label: 'Did not attend' },
]

function RescheduleModal({
  interview,
  onClose,
}: {
  interview: Interview
  onClose: () => void
}) {
  const toast = useToast()
  const reschedule = useRescheduleInterview(interview.id)
  const [scheduledAt, setScheduledAt] = useState('')
  const [error, setError] = useState<string | undefined>()

  return (
    <Modal
      open
      onClose={onClose}
      busy={reschedule.isPending}
      size="sm"
      title="Reschedule interview"
      description={`${interview.candidate_name} — ${interview.stage_name}`}
      footer={
        <>
          <Button onClick={onClose} disabled={reschedule.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!scheduledAt}
            loading={reschedule.isPending}
            onClick={() => {
              setError(undefined)
              reschedule.mutate(
                { scheduled_at: new Date(scheduledAt).toISOString() },
                {
                  onSuccess: () => {
                    toast.success('Interview rescheduled')
                    onClose()
                  },
                  onError: (submitError) => {
                    // The same overlap check runs against the new time,
                    // excluding this interview from its own comparison.
                    if (submitError instanceof ApiError) setError(submitError.displayMessage)
                    toast.fromError(submitError, 'Could not reschedule')
                  },
                },
              )
            }}
          >
            Reschedule
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <Banner tone="danger" title="The server refused this time">
            {error}
          </Banner>
        )}
        <p className="text-sm text-ink-muted">
          Currently {formatDateTime(interview.scheduled_at)} for {interview.duration_minutes}{' '}
          minutes.
        </p>
        <TextInput
          label="New date and time"
          type="datetime-local"
          required
          value={scheduledAt}
          onChange={(event) => setScheduledAt(event.target.value)}
          description="The interviewer's calendar is re-checked at both the service and database level."
        />
      </div>
    </Modal>
  )
}

export function InterviewsPage() {
  const permissions = usePermissions()
  const list = useListParams({ ordering: 'scheduled_at' })
  const query = useInterviews(list.queryParams)
  const [rescheduling, setRescheduling] = useState<Interview | null>(null)
  const [feedbackFor, setFeedbackFor] = useState<Interview | null>(null)

  const rows = query.data?.data ?? []
  const scope = permissions.scopeOf(RESOURCE.INTERVIEW)
  const mayReschedule = permissions.can(RESOURCE.INTERVIEW, ACTION.EDIT)
  const mayGiveFeedback = permissions.can(RESOURCE.INTERVIEW_FEEDBACK, ACTION.CREATE)

  const columns: Array<Column<Interview>> = [
    {
      key: 'candidate',
      header: 'Candidate',
      render: (row) => (
        <div className="flex items-center gap-2.5">
          <Avatar name={row.candidate_name} size="sm" />
          <div className="min-w-0">
            <Link
              to={`/recruitment/applications/${row.application}`}
              className="truncate font-medium text-ink hover:text-brand hover:underline"
              onClick={(event) => event.stopPropagation()}
            >
              {row.candidate_name}
            </Link>
            <p className="truncate text-xs text-ink-muted">{row.stage_name}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'interviewer',
      header: 'Interviewer',
      secondary: true,
      render: (row) => row.interviewer_name,
    },
    {
      key: 'when',
      header: 'Scheduled',
      sortKey: 'scheduled_at',
      render: (row) => (
        <div>
          <p className="tabular text-sm text-ink">{formatDateTime(row.scheduled_at)}</p>
          <p className="text-xs text-ink-muted">
            {row.duration_minutes} min · {humanize(row.mode)}
          </p>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (row) => (
        <div className="flex flex-wrap items-center gap-1.5">
          <InterviewStatusBadge status={row.status} />
          {row.feedback_submitted ? (
            <Badge tone="success">Feedback in</Badge>
          ) : (
            row.status === 'completed' && <Badge tone="warning">Feedback due</Badge>
          )}
        </div>
      ),
    },
    {
      key: 'actions',
      header: 'Actions',
      headerSrOnly: true,
      align: 'right',
      render: (row) => (
        <div className="flex justify-end gap-2">
          {mayGiveFeedback && !row.feedback_submitted && (
            <Button size="sm" variant="primary" onClick={() => setFeedbackFor(row)}>
              Feedback
            </Button>
          )}
          {mayReschedule && (row.status === 'scheduled' || row.status === 'rescheduled') && (
            <Button size="sm" onClick={() => setRescheduling(row)}>
              Reschedule
            </Button>
          )}
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Interviews"
        description={
          scope === 'self'
            ? 'The interviews assigned to you.'
            : 'Interviews within your visibility.'
        }
      />

      <Banner tone="info" title="Double-booking is blocked at the database">
        An interviewer cannot hold two overlapping interviews. This is enforced by a PostgreSQL
        exclusion constraint, re-checked inside the scheduling transaction, and previewed in the
        scheduling form — so concurrent bookings cannot slip through.
      </Banner>

      <Section>
        <div className="space-y-4">
          <TableToolbar onReset={list.reset} hasFilters={list.hasFilters}>
            <FilterSelect
              label="Status"
              value={list.filters.status ?? ''}
              onChange={(value) => list.setParam('status', value)}
              options={STATUS_OPTIONS}
              allLabel="All statuses"
            />
          </TableToolbar>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : (
            <DataTable
              caption="Interviews"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              sort={list.sort}
              onSortChange={list.setSort}
              isLoading={query.isLoading}
              loadingState={<TableSkeleton columns={5} />}
              emptyState={
                <EmptyState
                  title={list.hasFilters ? 'No interviews match' : 'No interviews scheduled'}
                  description="Interviews are booked from an application sitting at an interview stage."
                />
              }
              footer={
                <CursorPager
                  hasPrevious={Boolean(query.data?.meta.previous)}
                  hasNext={Boolean(query.data?.meta.next)}
                  onPrevious={() => list.setCursor(query.data?.meta.previous ?? '')}
                  onNext={() => list.setCursor(query.data?.meta.next ?? '')}
                  isFetching={query.isFetching}
                />
              }
            />
          )}
        </div>
      </Section>

      {rescheduling && (
        <RescheduleModal interview={rescheduling} onClose={() => setRescheduling(null)} />
      )}
      {feedbackFor && (
        <FeedbackDrawer
          open
          onClose={() => setFeedbackFor(null)}
          interview={feedbackFor}
          formId={feedbackFor.stage_feedback_form}
          onSubmitted={() => void query.refetch()}
        />
      )}
    </>
  )
}
