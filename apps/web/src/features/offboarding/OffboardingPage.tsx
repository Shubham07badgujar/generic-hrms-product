/**
 * The offboarding dashboard and exit list.
 *
 * Counts come from lists the server actually sent — the API uses cursor
 * pagination and returns no totals, so a card shows "5+" rather than inventing
 * a number it cannot know.
 */

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Card, CardHeader, PageHeader, Section, StatCard } from '@/components/ui/Card'
import {
  CursorPager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { CardSkeleton, EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Avatar, Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { ConfirmDialog, ReasonDialog } from '@/components/ui/ConfirmDialog'
import { Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useExits,
  useMyClearanceItems,
  usePendingResignations,
  useResignationReview,
  useStartExit,
  useSubmitResignation,
} from '@/lib/exitQueries'
import { useClearanceActions } from '@/lib/exitQueries'
import { useCurrentEmployees } from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { ApiError } from '@/lib/api'
import { formatDate, humanize } from '@/lib/format'
import type { ExitStageValue, ExitWorkflowSummary, ResignationRequest } from '@/lib/types'

const STAGE_TONES: Record<ExitStageValue, Tone> = {
  initiated: 'neutral',
  notice_period: 'info',
  clearance: 'warning',
  pending_approval: 'warning',
  approved: 'brand',
  completed: 'success',
  cancelled: 'neutral',
}

const REASONS = [
  { value: 'better_opportunity', label: 'Better opportunity' },
  { value: 'compensation', label: 'Compensation' },
  { value: 'relocation', label: 'Relocation' },
  { value: 'higher_studies', label: 'Higher studies' },
  { value: 'personal', label: 'Personal reasons' },
  { value: 'health', label: 'Health' },
  { value: 'work_environment', label: 'Work environment' },
  { value: 'career_change', label: 'Career change' },
  { value: 'other', label: 'Other' },
]

/* ------------------------------------------------------- resign (self) */

export function ResignDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const submit = useSubmitResignation()
  const [lastDay, setLastDay] = useState('')
  const [reason, setReason] = useState('personal')
  const [comments, setComments] = useState('')
  const [error, setError] = useState<string | undefined>()

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={submit.isPending}
      title="Submit your resignation"
      description="This is a request. HR reviews it before anything changes."
      footer={
        <>
          <Button onClick={onClose} disabled={submit.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            loading={submit.isPending}
            disabled={!lastDay}
            onClick={() => {
              setError(undefined)
              submit.mutate(
                {
                  requested_last_working_date: lastDay,
                  reason,
                  comments,
                },
                {
                  onSuccess: () => {
                    toast.success('Resignation submitted', 'HR will review it.')
                    onClose()
                  },
                  onError: (submitError) => {
                    if (submitError instanceof ApiError) setError(submitError.displayMessage)
                    toast.fromError(submitError, 'Could not submit')
                  },
                },
              )
            }}
          >
            Submit resignation
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}
        <Banner tone="info" title="Nothing changes until HR accepts">
          Submitting this does not end your employment or change your status. HR reviews the
          request and may agree a different last working day with you.
        </Banner>
        <TextInput
          label="Requested last working day"
          type="date"
          required
          value={lastDay}
          onChange={(event) => setLastDay(event.target.value)}
        />
        <Select
          label="Reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          options={REASONS}
        />
        <TextArea
          label="Comments"
          rows={4}
          value={comments}
          onChange={(event) => setComments(event.target.value)}
        />
      </div>
    </Modal>
  )
}

/* ---------------------------------------------------------- start exit */

function StartExitDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast()
  const navigate = useNavigate()
  const start = useStartExit()
  const employees = useCurrentEmployees()

  const [employee, setEmployee] = useState('')
  const [exitType, setExitType] = useState('termination')
  const [lastDay, setLastDay] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | undefined>()

  return (
    <Modal
      open={open}
      onClose={onClose}
      busy={start.isPending}
      title="Start an exit"
      description="For terminations and contract endings. A resignation starts from the employee's request."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            loading={start.isPending}
            disabled={!employee || !lastDay}
            onClick={() => {
              setError(undefined)
              start.mutate(
                { employee, exit_type: exitType, last_working_date: lastDay, reason },
                {
                  onSuccess: (created) => {
                    toast.success('Exit started')
                    onClose()
                    navigate(`/offboarding/${created.id}`)
                  },
                  onError: (submitError) => {
                    if (submitError instanceof ApiError) setError(submitError.displayMessage)
                    toast.fromError(submitError, 'Could not start the exit')
                  },
                },
              )
            }}
          >
            Start exit
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}
        <Select
          label="Employee"
          required
          placeholder="Select an employee"
          value={employee}
          onChange={(event) => setEmployee(event.target.value)}
          options={employees.rows.map((item) => ({
            value: item.id,
            label: `${item.full_name} — ${item.department_name ?? ''}`,
          }))}
        />
        <Select
          label="Exit type"
          value={exitType}
          onChange={(event) => setExitType(event.target.value)}
          options={[
            { value: 'termination', label: 'Termination' },
            { value: 'end_of_contract', label: 'End of contract' },
            { value: 'retirement', label: 'Retirement' },
            { value: 'abandonment', label: 'Abandonment' },
          ]}
        />
        <TextInput
          label="Last working day"
          type="date"
          required
          value={lastDay}
          onChange={(event) => setLastDay(event.target.value)}
        />
        <TextArea
          label="Reason"
          rows={3}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </div>
    </Modal>
  )
}

/* -------------------------------------------------- resignation review */

function ResignationQueue() {
  const permissions = usePermissions()
  const toast = useToast()
  const mayReview = permissions.can(RESOURCE.OFFBOARDING, ACTION.APPROVE)
  const query = usePendingResignations(mayReview)
  const [reviewing, setReviewing] = useState<ResignationRequest | null>(null)
  const [rejecting, setRejecting] = useState<ResignationRequest | null>(null)

  const review = useResignationReview(reviewing?.id ?? rejecting?.id ?? 'none')
  const rows = query.data?.data ?? []

  if (!mayReview) return null

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Resignations awaiting review"
          description="Accepting one moves the employee and opens their exit."
        />
        {query.isLoading ? (
          <CardSkeleton />
        ) : rows.length === 0 ? (
          <EmptyState compact title="No resignations awaiting review" />
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((request) => (
              <li key={request.id} className="flex flex-wrap items-center gap-3 py-3">
                <Avatar name={request.employee_name} size="sm" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-ink">
                    {request.employee_name}
                  </p>
                  <p className="truncate text-xs text-ink-muted">
                    {request.department_name ?? 'No department'} · {humanize(request.reason)} ·
                    requested {formatDate(request.requested_last_working_date)}
                  </p>
                  {request.employee_comments && (
                    <p className="pt-1 text-xs text-ink-muted">{request.employee_comments}</p>
                  )}
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button size="sm" variant="primary" onClick={() => setReviewing(request)}>
                    Accept
                  </Button>
                  <Button size="sm" variant="danger-soft" onClick={() => setRejecting(request)}>
                    Reject
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ConfirmDialog
        open={reviewing !== null}
        onClose={() => setReviewing(null)}
        loading={review.approve.isPending}
        title="Accept this resignation"
        description={
          reviewing
            ? `${reviewing.employee_name} — last working day ${formatDate(
                reviewing.requested_last_working_date,
              )}`
            : ''
        }
        confirmLabel="Accept resignation"
        onConfirm={() =>
          review.approve.mutate(
            {},
            {
              onSuccess: () => {
                toast.success('Resignation accepted', 'The exit workflow has been opened.')
                setReviewing(null)
                void query.refetch()
              },
              onError: (error) => toast.fromError(error, 'Could not accept'),
            },
          )
        }
      >
        <Banner tone="info">
          The employee moves to resigned, their clearance checklist is issued, and their
          company account is flagged for deprovisioning.
        </Banner>
      </ConfirmDialog>

      <ReasonDialog
        open={rejecting !== null}
        onClose={() => setRejecting(null)}
        loading={review.reject.isPending}
        title="Reject this resignation"
        description={rejecting?.employee_name}
        label="Reason"
        confirmLabel="Reject resignation"
        onSubmit={(reason) =>
          review.reject.mutate(
            { reason },
            {
              onSuccess: () => {
                toast.success('Resignation rejected')
                setRejecting(null)
                void query.refetch()
              },
              onError: (error) => toast.fromError(error, 'Could not reject'),
            },
          )
        }
        banner={
          <Banner tone="info">
            The employee is entitled to know why, so the reason is recorded and visible to
            them.
          </Banner>
        }
      />
    </>
  )
}

/* -------------------------------------------------- my clearance queue */

function MyClearanceQueue() {
  const toast = useToast()
  const query = useMyClearanceItems()
  const actions = useClearanceActions()
  const rows = query.data ?? []

  if (rows.length === 0) return null

  return (
    <Card className="space-y-4">
      <CardHeader
        title="Clearance assigned to you"
        description="Exit tasks waiting on you personally."
      />
      <ul className="divide-y divide-line">
        {rows.map((item) => (
          <li key={item.id} className="flex flex-wrap items-center gap-3 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">{item.title}</p>
              <p className="text-xs text-ink-muted">
                {humanize(item.category)}
                {item.due_date ? ` · due ${formatDate(item.due_date)}` : ''}
              </p>
            </div>
            {item.is_overdue && <Badge tone="danger">Overdue</Badge>}
            {!item.requires_evidence && (
              <Button
                size="sm"
                variant="primary"
                onClick={() =>
                  actions.complete.mutate(
                    { id: item.id },
                    {
                      onSuccess: () => {
                        toast.success('Clearance recorded')
                        void query.refetch()
                      },
                      onError: (error) => toast.fromError(error, 'Could not complete'),
                    },
                  )
                }
              >
                Mark done
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

/* ----------------------------------------------------------------- page */

export function OffboardingPage() {
  const navigate = useNavigate()
  const permissions = usePermissions()
  const list = useListParams({ ordering: '-initiated_at' })
  const query = useExits(list.queryParams)
  const openExits = useExits({ stage: 'clearance' })
  const [starting, setStarting] = useState(false)

  const rows = query.data?.data ?? []
  const mayStart = permissions.can(RESOURCE.OFFBOARDING, ACTION.CREATE)
  const mayApprove = permissions.can(RESOURCE.OFFBOARDING, ACTION.APPROVE)

  const columns: Array<Column<ExitWorkflowSummary>> = [
    {
      key: 'employee',
      header: 'Employee',
      render: (row) => (
        <div className="flex items-center gap-2.5">
          <Avatar name={row.employee_name} size="sm" />
          <div className="min-w-0">
            <p className="truncate font-medium text-ink">{row.employee_name}</p>
            <p className="truncate text-xs text-ink-muted">
              {row.employee_code} · {row.department_name ?? 'No department'}
            </p>
          </div>
        </div>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      secondary: true,
      render: (row) => <Badge tone="neutral">{humanize(row.exit_type)}</Badge>,
    },
    {
      key: 'stage',
      header: 'Stage',
      render: (row) => (
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={STAGE_TONES[row.stage] ?? 'neutral'}>{humanize(row.stage)}</Badge>
          {row.notice_waived && <Badge tone="warning">Waived</Badge>}
        </div>
      ),
    },
    {
      key: 'clearance',
      header: 'Clearance',
      align: 'right',
      render: (row) => (
        <span className="tabular text-xs text-ink-muted">
          {row.completed_items}/{row.total_items}
        </span>
      ),
    },
    {
      key: 'last_day',
      header: 'Last working day',
      sortKey: 'expected_last_working_date',
      align: 'right',
      render: (row) => (
        <span className="text-xs text-ink-muted">
          {formatDate(row.actual_last_working_date ?? row.expected_last_working_date)}
        </span>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Offboarding"
        description="Resignations, notice periods, clearance and final exits."
        actions={
          mayStart &&
          mayApprove && (
            <Button variant="primary" onClick={() => setStarting(true)}>
              Start an exit
            </Button>
          )
        }
      />

      <Section>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Exits in progress"
            tone="brand"
            value={
              <>
                {rows.filter((row) => !['completed', 'cancelled'].includes(row.stage)).length}
                {query.data?.meta.next && <span className="text-ink-subtle">+</span>}
              </>
            }
          />
          <StatCard
            label="In clearance"
            tone="warning"
            value={
              <>
                {openExits.data?.data.length ?? 0}
                {openExits.data?.meta.next && <span className="text-ink-subtle">+</span>}
              </>
            }
          />
          <StatCard
            label="Awaiting final exit"
            value={rows.filter((row) => row.stage === 'approved').length}
          />
          <StatCard
            label="Completed"
            tone="success"
            value={rows.filter((row) => row.stage === 'completed').length}
          />
        </div>
      </Section>

      <div className="grid gap-5 lg:grid-cols-2">
        <ResignationQueue />
        <MyClearanceQueue />
      </div>

      <Section title="All exits">
        <div className="space-y-4">
          <TableToolbar onReset={list.reset} hasFilters={list.hasFilters}>
            <FilterSelect
              label="Stage"
              value={list.filters.stage ?? ''}
              onChange={(value) => list.setParam('stage', value)}
              options={[
                { value: 'notice_period', label: 'Serving notice' },
                { value: 'clearance', label: 'Clearance' },
                { value: 'approved', label: 'Approved' },
                { value: 'completed', label: 'Completed' },
                { value: 'cancelled', label: 'Cancelled' },
              ]}
              allLabel="All stages"
            />
            <FilterSelect
              label="Type"
              value={list.filters.exit_type ?? ''}
              onChange={(value) => list.setParam('exit_type', value)}
              options={[
                { value: 'resignation', label: 'Resignation' },
                { value: 'termination', label: 'Termination' },
                { value: 'end_of_contract', label: 'End of contract' },
                { value: 'retirement', label: 'Retirement' },
              ]}
              allLabel="All types"
            />
          </TableToolbar>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          ) : (
            <DataTable
              caption="Exits"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              onRowClick={(row) => navigate(`/offboarding/${row.id}`)}
              sort={list.sort}
              onSortChange={list.setSort}
              isLoading={query.isLoading}
              loadingState={<TableSkeleton columns={5} />}
              emptyState={
                <EmptyState
                  title={list.hasFilters ? 'No exits match' : 'No exits under way'}
                  description="An exit starts from an accepted resignation, or is opened directly for a termination."
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

      <StartExitDialog open={starting} onClose={() => setStarting(false)} />
    </>
  )
}

/** Kept alongside so the profile page can offer "resign" without a new module. */
export { ResignDialog as SubmitResignationDialog }

/** Exported for the nav — a link to the caller's own exit, when one exists. */
export function useHasOpenExit() {
  const query = useExits({})
  return (query.data?.data ?? []).some(
    (row) => !['completed', 'cancelled'].includes(row.stage),
  )
}

export function ExitStageBadge({ stage }: { stage: ExitStageValue }) {
  return <Badge tone={STAGE_TONES[stage] ?? 'neutral'}>{humanize(stage)}</Badge>
}

export function OffboardingBreadcrumb() {
  return (
    <Link to="/offboarding" className="text-xs text-ink-subtle hover:text-ink hover:underline">
      Offboarding
    </Link>
  )
}
