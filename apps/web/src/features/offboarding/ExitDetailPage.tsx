/**
 * One employee's exit.
 *
 * THE BLOCKER PANEL IS THE SERVER'S ANSWER, NOT THIS PAGE'S. `blockers` and
 * `can_complete` come from the API, which computes them with the same function
 * that guards approval and completion. The Approve button is disabled when the
 * server says the exit is blocked — and if it were enabled anyway, the request
 * would still be refused with the same list.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Card, CardHeader, DescriptionList, PageHeader } from '@/components/ui/Card'
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Avatar, Banner, Tabs, TabPanel, Timeline, type TimelineEntry } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog, ReasonDialog } from '@/components/ui/ConfirmDialog'
import { Modal } from '@/components/ui/Modal'
import { Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { useClearanceActions, useExit, useExitActions } from '@/lib/exitQueries'
import { formatCurrency, formatDate, formatDateTime, humanize } from '@/lib/format'
import type {
  ExitBlocker,
  ExitClearanceItem,
  ExitStageValue,
  ExitWorkflowDetail,
} from '@/lib/types'

const STAGE_TONES: Record<ExitStageValue, Tone> = {
  initiated: 'neutral',
  notice_period: 'info',
  clearance: 'warning',
  pending_approval: 'warning',
  approved: 'brand',
  completed: 'success',
  cancelled: 'neutral',
}

const CLEARANCE_TONES: Record<string, Tone> = {
  pending: 'neutral',
  in_progress: 'info',
  completed: 'success',
  waived: 'brand',
  blocked: 'danger',
}

const CATEGORY_LABELS: Record<string, string> = {
  hr: 'HR clearance',
  department: 'Department clearance',
  it: 'IT and access',
  finance: 'Finance and settlement',
  assets: 'Company property',
}

/* ------------------------------------------------------------- blockers */

function BlockerPanel({ blockers }: { blockers: ExitBlocker[] }) {
  if (blockers.length === 0) {
    return (
      <Banner tone="success" title="Every gate is cleared">
        Clearance is complete, company property is accounted for, the account is deprovisioned
        and finance has signed off. This exit can be approved.
      </Banner>
    )
  }

  return (
    <Banner tone="warning" title={`${blockers.length} gate(s) still open`}>
      <p className="mb-2">
        The server refuses to approve an exit until every one of these is resolved. This list
        is computed by the same check that guards the approval itself.
      </p>
      <ul className="space-y-1.5">
        {blockers.map((blocker) => (
          <li key={blocker.id}>
            <span className="font-medium">{blocker.label}</span> — {blocker.detail}
            {blocker.items.length > 0 && (
              <ul className="ml-4 mt-0.5 list-disc space-y-0.5 text-xs opacity-90">
                {blocker.items.slice(0, 5).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </Banner>
  )
}

/* ------------------------------------------------------------ clearance */

function ClearanceSection({
  exit,
  onChanged,
}: {
  exit: ExitWorkflowDetail
  onChanged: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const actions = useClearanceActions(exit.id)
  const [waiving, setWaiving] = useState<ExitClearanceItem | null>(null)

  const mayComplete = permissions.can(RESOURCE.OFFBOARDING, ACTION.EDIT)
  const mayWaive = permissions.can(RESOURCE.OFFBOARDING, ACTION.APPROVE)

  const grouped = exit.clearance_items.reduce<Record<string, ExitClearanceItem[]>>(
    (accumulator, item) => {
      ;(accumulator[item.category] ??= []).push(item)
      return accumulator
    },
    {},
  )

  return (
    <>
      <div className="space-y-5">
        {Object.entries(grouped).map(([category, items]) => {
          const done = items.filter((item) => item.is_done).length
          return (
            <Card key={category} className="space-y-4">
              <CardHeader
                title={CATEGORY_LABELS[category] ?? humanize(category)}
                action={
                  <Badge tone={done === items.length ? 'success' : 'warning'}>
                    {done}/{items.length}
                  </Badge>
                }
              />
              <ul className="divide-y divide-line">
                {items.map((item) => (
                  <li key={item.id} className="flex flex-wrap items-center gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-medium text-ink">{item.title}</p>
                        <Badge tone={CLEARANCE_TONES[item.status] ?? 'neutral'}>
                          {humanize(item.status)}
                        </Badge>
                        {item.is_required && <Badge tone="warning">Required</Badge>}
                        {item.is_overdue && <Badge tone="danger">Overdue</Badge>}
                      </div>
                      <p className="text-xs text-ink-muted">
                        {humanize(item.owner)}
                        {item.assigned_to_name ? ` (${item.assigned_to_name})` : ''}
                        {item.due_date ? ` · due ${formatDate(item.due_date)}` : ''}
                        {item.requires_evidence ? ' · evidence required' : ''}
                      </p>
                      {item.completed_by_email && (
                        <p className="text-xs text-success-ink">
                          {humanize(item.status)} by {item.completed_by_email} on{' '}
                          {formatDate(item.completed_at)}
                        </p>
                      )}
                      {item.notes && <p className="pt-1 text-xs text-ink-muted">{item.notes}</p>}
                    </div>

                    {!item.is_done && exit.stage !== 'completed' && (
                      <div className="flex shrink-0 gap-2">
                        {mayComplete && !item.requires_evidence && (
                          <Button
                            size="sm"
                            variant="primary"
                            onClick={() =>
                              actions.complete.mutate(
                                { id: item.id },
                                {
                                  onSuccess: () => {
                                    toast.success('Clearance recorded')
                                    onChanged()
                                  },
                                  // A 403 here means the item belongs to
                                  // another role — the server checks ownership
                                  // as well as the permission.
                                  onError: (error) =>
                                    toast.fromError(error, 'Could not complete this item'),
                                },
                              )
                            }
                          >
                            Mark done
                          </Button>
                        )}
                        {mayWaive && (
                          <Button size="sm" onClick={() => setWaiving(item)}>
                            Waive
                          </Button>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          )
        })}
      </div>

      <ReasonDialog
        open={waiving !== null}
        onClose={() => setWaiving(null)}
        loading={actions.waive.isPending}
        tone="primary"
        title="Waive this clearance item"
        description={waiving?.title}
        label="Reason"
        confirmLabel="Waive item"
        onSubmit={(reason) =>
          waiving &&
          actions.waive.mutate(
            { id: waiving.id, reason },
            {
              onSuccess: () => {
                toast.success('Item waived')
                setWaiving(null)
                onChanged()
              },
              onError: (error) => toast.fromError(error, 'Could not waive'),
            },
          )
        }
        banner={
          <Banner tone="info">
            A waived item is recorded as waived, never as completed, so the distinction
            survives into any later review.
          </Banner>
        }
      />
    </>
  )
}

/* ----------------------------------------------------------- settlement */

function SettlementSection({
  exit,
  onChanged,
}: {
  exit: ExitWorkflowDetail
  onChanged: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const actions = useExitActions(exit.id)
  const [editing, setEditing] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [values, setValues] = useState({
    pending_salary: exit.settlement?.pending_salary ?? '0',
    leave_encashment: exit.settlement?.leave_encashment ?? '0',
    bonus_or_incentive: exit.settlement?.bonus_or_incentive ?? '0',
    outstanding_advances: exit.settlement?.outstanding_advances ?? '0',
    notice_shortfall_recovery: exit.settlement?.notice_shortfall_recovery ?? '0',
    asset_recovery: exit.settlement?.asset_recovery ?? '0',
    other_deductions: exit.settlement?.other_deductions ?? '0',
  })

  const settlement = exit.settlement
  if (!settlement) {
    return (
      <Card>
        <EmptyState title="No settlement record" />
      </Card>
    )
  }

  const mayEdit = permissions.can(RESOURCE.OFFBOARDING, ACTION.EDIT) && !settlement.cleared_at
  const mayClear = permissions.can(RESOURCE.OFFBOARDING, ACTION.APPROVE) && !settlement.cleared_at

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Full and final settlement"
          description="Prepared by finance, then cleared. Preparing and accepting are separate acts."
          action={
            <div className="flex items-center gap-2">
              <Badge tone={settlement.cleared_at ? 'success' : 'warning'}>
                {humanize(settlement.status)}
              </Badge>
              {mayEdit && (
                <Button size="sm" onClick={() => setEditing(true)}>
                  Edit figures
                </Button>
              )}
              {mayClear && (
                <Button size="sm" variant="primary" onClick={() => setClearing(true)}>
                  Clear settlement
                </Button>
              )}
            </div>
          }
        />

        <Banner tone="info">
          Statutory computation — gratuity, PF and tax treatment — belongs to the payroll
          engine and is not calculated here. This is the ledger of what is owed and deducted.
        </Banner>

        <div className="grid gap-5 sm:grid-cols-2">
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">
              Earnings
            </p>
            <DescriptionList
              columns={1}
              items={[
                { label: 'Pending salary', value: formatCurrency(settlement.pending_salary) },
                { label: 'Leave encashment', value: formatCurrency(settlement.leave_encashment) },
                { label: 'Bonus / incentive', value: formatCurrency(settlement.bonus_or_incentive) },
                { label: 'Other', value: formatCurrency(settlement.other_earnings) },
                {
                  label: 'Gross',
                  value: (
                    <span className="font-semibold">
                      {formatCurrency(settlement.gross_earnings)}
                    </span>
                  ),
                },
              ]}
            />
          </div>
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-ink-subtle">
              Deductions
            </p>
            <DescriptionList
              columns={1}
              items={[
                { label: 'Advances', value: formatCurrency(settlement.outstanding_advances) },
                {
                  label: 'Notice shortfall',
                  value: formatCurrency(settlement.notice_shortfall_recovery),
                },
                { label: 'Asset recovery', value: formatCurrency(settlement.asset_recovery) },
                { label: 'Other', value: formatCurrency(settlement.other_deductions) },
                {
                  label: 'Total',
                  value: (
                    <span className="font-semibold">
                      {formatCurrency(settlement.total_deductions)}
                    </span>
                  ),
                },
              ]}
            />
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-line pt-4">
          <span className="text-sm font-medium text-ink">Net payable</span>
          <span
            className={`tabular text-lg font-semibold ${
              Number(settlement.net_payable) < 0 ? 'text-danger' : 'text-success'
            }`}
          >
            {formatCurrency(settlement.net_payable)}
          </span>
        </div>
        {Number(settlement.net_payable) < 0 && (
          <Banner tone="warning">
            The deductions exceed the earnings, so the employee owes the company on exit.
          </Banner>
        )}
        {settlement.cleared_by_email && (
          <p className="text-xs text-ink-muted">
            Cleared by {settlement.cleared_by_email} on {formatDateTime(settlement.cleared_at)}
          </p>
        )}
      </Card>

      <Modal
        open={editing}
        onClose={() => setEditing(false)}
        busy={actions.settlement.isPending}
        title="Settlement figures"
        footer={
          <>
            <Button onClick={() => setEditing(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={actions.settlement.isPending}
              onClick={() =>
                actions.settlement.mutate(values as never, {
                  onSuccess: () => {
                    toast.success('Settlement updated')
                    setEditing(false)
                    onChanged()
                  },
                  onError: (error) => toast.fromError(error, 'Could not update'),
                })
              }
            >
              Save
            </Button>
          </>
        }
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {(
            [
              ['pending_salary', 'Pending salary'],
              ['leave_encashment', 'Leave encashment'],
              ['bonus_or_incentive', 'Bonus / incentive'],
              ['outstanding_advances', 'Outstanding advances'],
              ['notice_shortfall_recovery', 'Notice shortfall recovery'],
              ['asset_recovery', 'Asset recovery'],
              ['other_deductions', 'Other deductions'],
            ] as const
          ).map(([field, label]) => (
            <TextInput
              key={field}
              label={label}
              inputMode="decimal"
              value={values[field]}
              onChange={(event) =>
                setValues((current) => ({ ...current, [field]: event.target.value }))
              }
            />
          ))}
        </div>
      </Modal>

      <ConfirmDialog
        open={clearing}
        onClose={() => setClearing(false)}
        loading={actions.clearSettlement.isPending}
        title="Clear the settlement"
        description="Finance accepts these figures. The settlement becomes read-only."
        confirmLabel="Clear settlement"
        onConfirm={() =>
          actions.clearSettlement.mutate(
            {},
            {
              onSuccess: () => {
                toast.success('Settlement cleared')
                setClearing(false)
                onChanged()
              },
              onError: (error) => toast.fromError(error, 'Could not clear'),
            },
          )
        }
      />
    </>
  )
}

/* ------------------------------------------------------------ interview */

function InterviewSection({
  exit,
  onChanged,
}: {
  exit: ExitWorkflowDetail
  onChanged: () => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const actions = useExitActions(exit.id)
  const [editing, setEditing] = useState(false)
  const interview = exit.interview

  const [values, setValues] = useState({
    primary_reason: interview?.primary_reason ?? 'other',
    employee_feedback: interview?.employee_feedback ?? '',
    manager_feedback: interview?.manager_feedback ?? '',
    workplace_feedback: interview?.workplace_feedback ?? '',
    improvement_suggestions: interview?.improvement_suggestions ?? '',
    rehire_eligibility: interview?.rehire_eligibility ?? 'undecided',
    hr_notes: interview?.hr_notes ?? '',
  })

  const mayRecord = permissions.can(RESOURCE.OFFBOARDING, ACTION.EDIT)

  return (
    <>
      <Card className="space-y-4">
        <CardHeader
          title="Exit interview"
          description="Held under HR permissions."
          action={
            mayRecord && (
              <Button size="sm" variant="primary" onClick={() => setEditing(true)}>
                {interview?.is_conducted ? 'Update' : 'Record interview'}
              </Button>
            )
          }
        />

        <Banner tone="info">
          Candid feedback about a named manager must not be readable by that manager, so this
          section follows offboarding permissions rather than the department view of an
          employee. Its contents are also kept out of the audit trail.
        </Banner>

        {interview?.is_conducted ? (
          <DescriptionList
            columns={1}
            items={[
              { label: 'Conducted by', value: interview.conducted_by_email ?? '—' },
              { label: 'When', value: formatDateTime(interview.conducted_at) },
              { label: 'Primary reason', value: humanize(interview.primary_reason) },
              { label: 'Employee feedback', value: interview.employee_feedback || '—' },
              { label: 'Manager feedback', value: interview.manager_feedback || '—' },
              { label: 'Workplace feedback', value: interview.workplace_feedback || '—' },
              { label: 'Suggestions', value: interview.improvement_suggestions || '—' },
              {
                label: 'Rehire eligibility',
                value: (
                  <Badge
                    tone={
                      interview.rehire_eligibility === 'eligible'
                        ? 'success'
                        : interview.rehire_eligibility === 'not_eligible'
                          ? 'danger'
                          : 'neutral'
                    }
                  >
                    {humanize(interview.rehire_eligibility)}
                  </Badge>
                ),
              },
            ]}
          />
        ) : (
          <EmptyState compact title="No exit interview recorded yet" />
        )}
      </Card>

      <Modal
        open={editing}
        onClose={() => setEditing(false)}
        busy={actions.interview.isPending}
        size="lg"
        title="Exit interview"
        footer={
          <>
            <Button onClick={() => setEditing(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={actions.interview.isPending}
              onClick={() =>
                actions.interview.mutate(values as never, {
                  onSuccess: () => {
                    toast.success('Exit interview recorded')
                    setEditing(false)
                    onChanged()
                  },
                  onError: (error) => toast.fromError(error, 'Could not save'),
                })
              }
            >
              Save interview
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Select
            label="Primary reason for leaving"
            value={values.primary_reason}
            onChange={(event) =>
              setValues((current) => ({ ...current, primary_reason: event.target.value as never }))
            }
            options={[
              { value: 'better_opportunity', label: 'Better opportunity' },
              { value: 'compensation', label: 'Compensation' },
              { value: 'relocation', label: 'Relocation' },
              { value: 'higher_studies', label: 'Higher studies' },
              { value: 'personal', label: 'Personal reasons' },
              { value: 'health', label: 'Health' },
              { value: 'work_environment', label: 'Work environment' },
              { value: 'career_change', label: 'Career change' },
              { value: 'other', label: 'Other' },
            ]}
          />
          {(
            [
              ['employee_feedback', 'Employee feedback'],
              ['manager_feedback', 'Feedback about the manager'],
              ['workplace_feedback', 'Feedback about the workplace'],
              ['improvement_suggestions', 'Suggestions for improvement'],
              ['hr_notes', 'HR notes'],
            ] as const
          ).map(([field, label]) => (
            <TextArea
              key={field}
              label={label}
              rows={3}
              value={values[field]}
              onChange={(event) =>
                setValues((current) => ({ ...current, [field]: event.target.value }))
              }
            />
          ))}
          <Select
            label="Rehire eligibility"
            value={values.rehire_eligibility}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                rehire_eligibility: event.target.value as never,
              }))
            }
            options={[
              { value: 'eligible', label: 'Eligible for rehire' },
              { value: 'eligible_with_notes', label: 'Eligible, with reservations' },
              { value: 'not_eligible', label: 'Not eligible' },
              { value: 'undecided', label: 'Not decided' },
            ]}
          />
        </div>
      </Modal>
    </>
  )
}

/* ----------------------------------------------------------------- page */

export function ExitDetailPage() {
  const { id } = useParams<{ id: string }>()
  const permissions = usePermissions()
  const toast = useToast()
  const query = useExit(id)
  const actions = useExitActions(id ?? 'none')

  const [tab, setTab] = useState('overview')
  const [approving, setApproving] = useState(false)
  const [completing, setCompleting] = useState(false)
  const [waivingNotice, setWaivingNotice] = useState(false)
  const [releasing, setReleasing] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [newDate, setNewDate] = useState('')

  if (query.isLoading) return <LoadingBlock label="Loading exit" />
  if (query.isError) return <ErrorState error={query.error} />
  if (!query.data) return <EmptyState title="Exit not found" />

  const exit = query.data
  const refresh = () => void query.refetch()
  const mayApprove = permissions.can(RESOURCE.OFFBOARDING, ACTION.APPROVE)
  const isClosed = exit.stage === 'completed' || exit.stage === 'cancelled'

  const timeline: TimelineEntry[] = [
    {
      id: 'initiated',
      tone: 'info',
      title: `Exit initiated (${humanize(exit.exit_type).toLowerCase()})`,
      timestamp: formatDateTime(exit.initiated_at),
      body: exit.reason || undefined,
    },
    ...(exit.notice_waived
      ? [
          {
            id: 'waived',
            tone: 'warning' as const,
            title: 'Notice waived',
            timestamp: formatDateTime(exit.notice_waived_at),
            body: exit.notice_waiver_reason,
          },
        ]
      : []),
    ...(exit.early_release_approved
      ? [
          {
            id: 'released',
            tone: 'warning' as const,
            title: 'Early release approved',
            body: exit.early_release_reason,
          },
        ]
      : []),
    ...(exit.approved_at
      ? [
          {
            id: 'approved',
            tone: 'brand' as const,
            title: 'Exit approved',
            timestamp: formatDateTime(exit.approved_at),
            body: exit.approval_notes || undefined,
          },
        ]
      : []),
    ...(exit.completed_at
      ? [
          {
            id: 'completed',
            tone: 'success' as const,
            title: 'Employee exited',
            timestamp: formatDateTime(exit.completed_at),
          },
        ]
      : []),
  ]

  return (
    <>
      <PageHeader
        breadcrumbs={
          <nav aria-label="Breadcrumb" className="text-xs text-ink-subtle">
            <Link to="/offboarding" className="hover:text-ink hover:underline">
              Offboarding
            </Link>
            <span className="px-1.5">/</span>
            <span className="text-ink-muted">{exit.employee_name}</span>
          </nav>
        }
        title={
          <span className="flex items-center gap-3">
            <Avatar name={exit.employee_name} size="md" />
            {exit.employee_name}
          </span>
        }
        description={`${exit.employee_code} · ${exit.department_name ?? 'No department'}`}
        meta={
          <>
            <Badge tone={STAGE_TONES[exit.stage] ?? 'neutral'} dot>
              {humanize(exit.stage)}
            </Badge>
            <Badge tone="neutral">{humanize(exit.exit_type)}</Badge>
            {exit.notice_waived && <Badge tone="warning">Notice waived</Badge>}
            {exit.early_release_approved && <Badge tone="warning">Early release</Badge>}
            <span className="text-xs text-ink-subtle">
              Last working day {formatDate(exit.expected_last_working_date)}
            </span>
          </>
        }
        actions={
          !isClosed &&
          mayApprove && (
            <>
              {exit.stage !== 'approved' && (
                <Button
                  variant="primary"
                  // The SERVER decides. This mirrors `can_complete`; the
                  // request is refused with the same list if it is sent anyway.
                  disabled={!exit.can_complete}
                  title={
                    exit.can_complete
                      ? undefined
                      : 'Every clearance gate must be resolved before approval.'
                  }
                  onClick={() => setApproving(true)}
                >
                  Approve exit
                </Button>
              )}
              {exit.stage === 'approved' && (
                <Button variant="primary" onClick={() => setCompleting(true)}>
                  Mark as exited
                </Button>
              )}
              <Button onClick={() => setWaivingNotice(true)}>Waive notice</Button>
              <Button onClick={() => setReleasing(true)}>Early release</Button>
              <Button variant="danger-soft" onClick={() => setCancelling(true)}>
                Cancel exit
              </Button>
            </>
          )
        }
      />

      {exit.stage === 'completed' ? (
        <Banner tone="neutral" title="This exit is complete">
          The employee has left and their record is retained for statutory and audit purposes.
          Nothing moves out of the exited state — a returning employee is represented by a new
          employee record.
        </Banner>
      ) : exit.stage === 'cancelled' ? (
        <Banner tone="info" title="This exit was cancelled">
          {exit.cancelled_reason}
        </Banner>
      ) : (
        <BlockerPanel blockers={exit.blockers} />
      )}

      <Tabs
        active={tab}
        onChange={setTab}
        items={[
          { id: 'overview', label: 'Overview' },
          {
            id: 'clearance',
            label: 'Clearance',
            count: exit.total_items ? exit.total_items - exit.completed_items : undefined,
          },
          { id: 'settlement', label: 'Settlement' },
          { id: 'interview', label: 'Exit interview' },
          { id: 'history', label: 'History' },
        ]}
      />

      <TabPanel id="overview" active={tab}>
        <div className="grid gap-5 lg:grid-cols-2">
          <Card>
            <CardHeader title="Notice period" />
            <div className="pt-4">
              <DescriptionList
                columns={2}
                items={[
                  { label: 'Notice started', value: formatDate(exit.notice_start_date) },
                  { label: 'Notice period', value: `${exit.notice_days} days` },
                  {
                    label: 'Expected last day',
                    value: formatDate(exit.expected_last_working_date),
                  },
                  {
                    label: 'Actual last day',
                    value: formatDate(exit.actual_last_working_date),
                  },
                  {
                    label: 'Notice waived',
                    value: exit.notice_waived ? (
                      <Badge tone="warning">Yes</Badge>
                    ) : (
                      <span className="text-ink-subtle">No</span>
                    ),
                  },
                  {
                    label: 'Early release',
                    value: exit.early_release_approved ? (
                      <Badge tone="warning">Approved</Badge>
                    ) : (
                      <span className="text-ink-subtle">No</span>
                    ),
                  },
                ]}
              />
            </div>
          </Card>

          <Card>
            <CardHeader title="Company property" />
            <div className="pt-4">
              {exit.unreturned_assets.length === 0 ? (
                <Banner tone="success">
                  All returnable property is accounted for.
                </Banner>
              ) : (
                <>
                  <Banner tone="warning" title="Still allocated">
                    An exit cannot complete while these are outstanding. Record a return, or
                    write the item off with a reason.
                  </Banner>
                  <ul className="mt-3 divide-y divide-line">
                    {exit.unreturned_assets.map((asset) => (
                      <li key={asset.allocation_id} className="py-2.5">
                        <p className="text-sm font-medium text-ink">
                          {asset.asset_tag} · {asset.asset_name}
                        </p>
                        <p className="text-xs text-ink-muted">{asset.category}</p>
                      </li>
                    ))}
                  </ul>
                  <Link to={`/employees/${exit.employee}`} className="mt-3 inline-block">
                    <Button size="sm">Manage on the employee profile</Button>
                  </Link>
                </>
              )}
            </div>
          </Card>

          {exit.resignation && (
            <Card className="lg:col-span-2">
              <CardHeader title="Resignation" />
              <div className="pt-4">
                <DescriptionList
                  columns={3}
                  items={[
                    { label: 'Submitted', value: formatDate(exit.resignation.submitted_at) },
                    { label: 'Reason', value: humanize(exit.resignation.reason) },
                    {
                      label: 'Requested last day',
                      value: formatDate(exit.resignation.requested_last_working_date),
                    },
                    {
                      label: 'Reviewed by',
                      value: exit.resignation.reviewed_by_email ?? '—',
                    },
                    {
                      label: 'Employee comments',
                      value: exit.resignation.employee_comments || '—',
                    },
                  ]}
                />
              </div>
            </Card>
          )}
        </div>
      </TabPanel>

      <TabPanel id="clearance" active={tab}>
        <ClearanceSection exit={exit} onChanged={refresh} />
      </TabPanel>

      <TabPanel id="settlement" active={tab}>
        <SettlementSection exit={exit} onChanged={refresh} />
      </TabPanel>

      <TabPanel id="interview" active={tab}>
        <InterviewSection exit={exit} onChanged={refresh} />
      </TabPanel>

      <TabPanel id="history" active={tab}>
        <Card>
          <CardHeader title="Exit history" />
          <div className="pt-4">
            <Timeline entries={timeline} />
          </div>
        </Card>
      </TabPanel>

      {/* --- dialogs --- */}

      <ConfirmDialog
        open={approving}
        onClose={() => setApproving(false)}
        loading={actions.approve.isPending}
        title="Approve this exit"
        description="Confirms every gate is cleared and the employee is free to leave."
        confirmLabel="Approve exit"
        onConfirm={() =>
          actions.approve.mutate(
            {},
            {
              onSuccess: () => {
                toast.success('Exit approved')
                setApproving(false)
                refresh()
              },
              onError: (error) => toast.fromError(error, 'Could not approve'),
            },
          )
        }
      />

      <ConfirmDialog
        open={completing}
        onClose={() => setCompleting(false)}
        loading={actions.complete.isPending}
        tone="danger"
        title="Mark this employee as exited"
        description="This is final. The exited state has no outgoing transition."
        confirmLabel="Mark as exited"
        onConfirm={() =>
          actions.complete.mutate(
            {},
            {
              onSuccess: () => {
                toast.success('Employee exited')
                setCompleting(false)
                refresh()
              },
              onError: (error) => toast.fromError(error, 'Could not complete the exit'),
            },
          )
        }
      >
        <Banner tone="danger" title="There is no way back">
          A returning employee is represented by a new employee record. This one is retained
          for statutory and audit purposes and cannot be reactivated.
        </Banner>
      </ConfirmDialog>

      <ReasonDialog
        open={waivingNotice}
        onClose={() => setWaivingNotice(false)}
        loading={actions.waiveNotice.isPending}
        title="Waive the notice period"
        label="Reason"
        confirmLabel="Waive notice"
        onSubmit={(reason) =>
          actions.waiveNotice.mutate(
            { reason },
            {
              onSuccess: () => {
                toast.success('Notice waived')
                setWaivingNotice(false)
                refresh()
              },
              onError: (error) => toast.fromError(error, 'Could not waive notice'),
            },
          )
        }
        banner={
          <Banner tone="warning" title="This is an exception">
            It is recorded as an override in the audit trail, with your name and this reason.
          </Banner>
        }
      />

      <ReasonDialog
        open={releasing}
        onClose={() => setReleasing(false)}
        loading={actions.earlyRelease.isPending}
        title="Approve an early release"
        label="Reason"
        confirmLabel="Approve early release"
        onSubmit={(reason) => {
          if (!newDate) {
            toast.error('A new last working day is required')
            return
          }
          actions.earlyRelease.mutate(
            { reason, new_last_working_date: newDate },
            {
              onSuccess: () => {
                toast.success('Early release approved')
                setReleasing(false)
                refresh()
              },
              onError: (error) => toast.fromError(error, 'Could not approve'),
            },
          )
        }}
      >
        <TextInput
          label="New last working day"
          type="date"
          required
          value={newDate}
          onChange={(event) => setNewDate(event.target.value)}
          description={`Must be earlier than ${formatDate(exit.expected_last_working_date)}.`}
        />
      </ReasonDialog>

      <ReasonDialog
        open={cancelling}
        onClose={() => setCancelling(false)}
        loading={actions.cancel.isPending}
        title="Cancel this exit"
        description="The employee returns to active. Only possible before completion."
        label="Reason"
        confirmLabel="Cancel exit"
        onSubmit={(reason) =>
          actions.cancel.mutate(
            { reason },
            {
              onSuccess: () => {
                toast.success('Exit cancelled')
                setCancelling(false)
                refresh()
              },
              onError: (error) => toast.fromError(error, 'Could not cancel'),
            },
          )
        }
      />
    </>
  )
}
