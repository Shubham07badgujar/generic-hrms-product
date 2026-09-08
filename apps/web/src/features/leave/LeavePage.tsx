/**
 * Leave: apply, track, decide, and configure.
 *
 * The routing rule the buttons follow is the server's, not this file's:
 * a request goes to the employee's reporting manager first, whose approval
 * forwards it to the HR Head for the final say (the HR Head's own leave is
 * decided by Admin), and nobody decides their own request. The tabs only
 * SHOW what the caller's permissions make actionable — the API refuses
 * anything else regardless.
 */

import { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, PageHeader, Section, StatCard } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { FieldRow, Select, TextArea, TextInput } from '@/components/ui/Field'
import { Banner, Tabs, TabPanel } from '@/components/ui/Misc'
import { EmptyState, LoadingBlock } from '@/components/ui/States'
import { useToast } from '@/components/ui/Toast'
import { useAuth, usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { ApiError, downloadFile } from '@/lib/api'
import { formatDate } from '@/lib/format'
import {
  useApplyLeave,
  useDeleteHoliday,
  useDeleteHolidayWork,
  useDeleteShortLeave,
  useHolidayCalendars,
  useHolidayWorkList,
  useLeaveBalances,
  useLeaveCalendar,
  useLeaveDecision,
  useLeavePatterns,
  useLeavePolicies,
  useLeaveRequests,
  useLeaveSettings,
  useLeaveTypes,
  useRecordHolidayWork,
  useRecordShortLeave,
  useSaveHoliday,
  useShortLeaves,
  useUpdateLeaveSettings,
  type LeaveRequest,
  type LeaveSettings,
} from '@/lib/leaveQueries'
import { useEmployees } from '@/lib/queries'

const STATUS_TONE: Record<string, 'info' | 'success' | 'danger' | 'neutral'> = {
  pending: 'info',
  approved: 'success',
  rejected: 'danger',
  cancelled: 'neutral',
}

function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? 'neutral'}>{status.replace('_', ' ')}</Badge>
}

/* ================================================================= page */

export function LeavePage() {
  const permissions = usePermissions()
  const mayApprove = permissions.can(RESOURCE.LEAVE_REQUEST, ACTION.APPROVE)
  const mayConfigure = permissions.can(RESOURCE.LEAVE_POLICY, ACTION.EDIT)

  const [tab, setTab] = useState('mine')
  const items = [
    { id: 'mine', label: 'My leave' },
    ...(mayApprove ? [{ id: 'approvals', label: 'Approvals' }] : []),
    { id: 'calendar', label: 'Calendar' },
    ...(mayConfigure ? [{ id: 'setup', label: 'Types & holidays' }] : []),
  ]

  return (
    <>
      <PageHeader
        title="Leave"
        description="Apply for leave, track balances, and see who is away."
      />
      <Tabs items={items} active={tab} onChange={setTab} />
      <TabPanel id="mine" active={tab}>
        <MyLeave />
      </TabPanel>
      {mayApprove && (
        <TabPanel id="approvals" active={tab}>
          <Approvals />
        </TabPanel>
      )}
      <TabPanel id="calendar" active={tab}>
        <LeaveCalendar />
      </TabPanel>
      {mayConfigure && (
        <TabPanel id="setup" active={tab}>
          <Setup />
        </TabPanel>
      )}
    </>
  )
}

/* ============================================================== my leave */

function MyLeave() {
  const toast = useToast()
  const { user } = useAuth()
  // HR and Admin can SEE everyone's rows, but this tab is personal: filter
  // to the caller's own employee record.
  const employeeId = user?.employee_id ?? undefined
  const year = String(new Date().getFullYear())
  const balances = useLeaveBalances({ year, employee: employeeId })
  const mine = useLeaveRequests({ employee: employeeId })
  const types = useLeaveTypes()
  const settings = useLeaveSettings()
  const apply = useApplyLeave()

  const [values, setValues] = useState({
    leave_type: '',
    start_date: '',
    end_date: '',
    half_day: '',
    reason: '',
  })
  const [isEmergency, setIsEmergency] = useState(false)
  const [attachment, setAttachment] = useState<File | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [cancelling, setCancelling] = useState<LeaveRequest | null>(null)

  function set(key: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [key]: value }))
    setErrors(({ [key]: _gone, ...rest }) => rest)
  }

  const rows = mine.data?.data ?? []
  const balanceRows = balances.data?.data ?? []

  // Admin and CEO are system accounts without an employee record — they have
  // no leave of their own to apply for.
  if (!employeeId) {
    return (
      <Banner tone="info" title="Your account has no leave of its own">
        System accounts (Admin, CEO) exist without an employee record, so there is nothing to
        apply for here. Your work lives in the Approvals tab — the HR Head's own requests are
        decided by you.
      </Banner>
    )
  }

  return (
    <div className="space-y-5">
      {user?.employee_status === 'on_probation' && (
        <Banner tone="info" title="You are on probation">
          Leave taken during probation is recorded as{' '}
          <strong>Unpaid Leave – Probation Period</strong> and does not use any paid balance.
          Paid Leave (PL/CL) and Sick Leave open once your probation is confirmed — your paid
          balance keeps accruing in the background and becomes available then.
        </Banner>
      )}
      {balanceRows.length > 0 && (
        <Section>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {balanceRows.map((row) => (
              <StatCard
                key={row.id}
                label={row.leave_type_name}
                value={row.available}
                hint={`of ${Number(row.allocated) + Number(row.carried_forward)} · ${row.pending} pending`}
                tone={Number(row.available) > 0 ? 'brand' : 'warning'}
              />
            ))}
          </div>
        </Section>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card className="space-y-4">
          <CardHeader
            title="Apply for leave"
            description="Days are calculated by the server from your working days, weekly offs and holidays."
          />
          {settings.data && (
            <p className="rounded-lg bg-surface-muted px-3 py-2 text-xs text-ink-muted">
              Notice required: {settings.data.single_day_notice_days} days for a one-day
              leave, {settings.data.general_notice_days} working days for planned leave,
              and {settings.data.long_leave_notice_days} days for anything longer than{' '}
              {settings.data.long_leave_threshold_days} days. Emergencies must start within{' '}
              {settings.data.emergency_window_hours} hours and inform HR immediately.
            </p>
          )}
          <Select
            label="Leave type"
            required
            placeholder="Select a type"
            value={values.leave_type}
            onChange={(event) => set('leave_type', event.target.value)}
            options={(types.data ?? []).map((type) => ({
              value: type.id,
              label: type.is_paid ? type.name : `${type.name} (unpaid)`,
            }))}
            error={errors.leave_type}
          />
          <FieldRow>
            <TextInput
              label="From"
              required
              type="date"
              value={values.start_date}
              onChange={(event) => set('start_date', event.target.value)}
              error={errors.start_date}
            />
            <TextInput
              label="To"
              required
              type="date"
              value={values.end_date}
              onChange={(event) => set('end_date', event.target.value)}
              error={errors.end_date}
            />
          </FieldRow>
          <Select
            label="Duration"
            value={values.half_day}
            onChange={(event) => set('half_day', event.target.value)}
            options={[
              { value: '', label: 'Full day(s)' },
              { value: 'first_half', label: 'Half day — first half' },
              { value: 'second_half', label: 'Half day — second half' },
            ]}
            error={errors.half_day}
            description="Half days apply to a single date only."
          />
          <TextArea
            label="Reason"
            required
            rows={2}
            value={values.reason}
            onChange={(event) => set('reason', event.target.value)}
            error={errors.reason}
          />
          <label className="flex items-start gap-2 text-sm text-ink">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={isEmergency}
              onChange={(event) => setIsEmergency(event.target.checked)}
            />
            <span>
              Emergency leave
              <span className="block text-xs font-normal text-ink-muted">
                Waives the notice rules, but must start within the emergency window — HR and
                your reporting manager are informed immediately.
              </span>
            </span>
          </label>
          <div>
            <label className="mb-1 block text-sm font-medium text-ink">
              Attachment <span className="font-normal text-ink-subtle">(when required)</span>
            </label>
            <input
              type="file"
              accept=".pdf,.jpg,.jpeg,.png,.doc,.docx"
              className="block w-full text-sm text-ink-muted file:mr-3 file:rounded-lg file:border file:border-line file:bg-surface file:px-3 file:py-1.5 file:text-sm"
              onChange={(event) => setAttachment(event.target.files?.[0] ?? null)}
            />
            {errors.attachment && (
              <p className="mt-1 text-xs text-danger">{errors.attachment}</p>
            )}
          </div>
          <Button
            variant="primary"
            loading={apply.isPending}
            disabled={
              !values.leave_type || !values.start_date || !values.end_date || !values.reason
            }
            onClick={() => {
              setErrors({})
              apply.mutate(
                { ...values, attachment, is_emergency: isEmergency },
                {
                  onSuccess: (row) => {
                    toast.success(
                      'Leave submitted',
                      `${row.days} day(s) — awaiting ${row.approval_band === 'admin' ? 'Admin' : 'HR'} approval.`,
                    )
                    setValues({ leave_type: '', start_date: '', end_date: '', half_day: '', reason: '' })
                    setAttachment(null)
                    setIsEmergency(false)
                  },
                  onError: (error) => {
                    if (error instanceof ApiError) setErrors(error.fieldErrors)
                    toast.fromError(error, 'Could not submit')
                  },
                },
              )
            }}
          >
            Submit for approval
          </Button>
        </Card>

        <Card className="space-y-3">
          <CardHeader title="My requests" />
          {mine.isLoading ? (
            <LoadingBlock />
          ) : rows.length === 0 ? (
            <EmptyState compact title="No leave requests yet" />
          ) : (
            <ul className="divide-y divide-line">
              {rows.map((row) => (
                <li key={row.id} className="flex flex-wrap items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-ink">
                      {row.display_type ?? row.leave_type_name} · {row.days} day(s)
                      {row.is_emergency && <Badge tone="warning">emergency</Badge>}
                    </p>
                    <p className="text-xs text-ink-muted">
                      {formatDate(row.start_date)} – {formatDate(row.end_date)}
                      {row.half_day && ` · ${row.half_day.replace('_', ' ')}`}
                    </p>
                    {row.status === 'pending' && row.pending_with && (
                      <p className="text-xs text-info">
                        Pending with {row.pending_with}
                        {row.approval_stage === 'hr' && row.manager_decided_at &&
                          ' · manager approved'}
                      </p>
                    )}
                    {row.manager_decided_at && (
                      <p className="text-xs text-ink-muted">
                        Manager ({row.manager_decided_by_email}):{' '}
                        {row.status === 'rejected' && row.approval_stage === 'manager'
                          ? 'rejected'
                          : 'approved'}{' '}
                        {formatDate(row.manager_decided_at)}
                        {row.manager_note && ` — ${row.manager_note}`}
                      </p>
                    )}
                    {/* A manager-stage rejection already reads as the manager line. */}
                    {row.decided_at &&
                      !(row.status === 'rejected' && row.approval_stage === 'manager') && (
                        <p className="text-xs text-ink-muted">
                          Final ({row.decided_by_email}): {row.status}{' '}
                          {formatDate(row.decided_at)}
                        </p>
                      )}
                    {row.status === 'rejected' && row.decision_reason && (
                      <p className="text-xs text-danger">Reason: {row.decision_reason}</p>
                    )}
                    {row.has_attachment && (
                      <button
                        type="button"
                        className="text-xs text-brand hover:underline"
                        onClick={() =>
                          void downloadFile(
                            `/leave-requests/${row.id}/attachment/`,
                            'leave-attachment',
                          ).catch(() => undefined)
                        }
                      >
                        View attached document
                      </button>
                    )}
                  </div>
                  <StatusBadge status={row.status} />
                  {(row.status === 'pending' ||
                    (row.status === 'approved' && row.start_date > new Date().toISOString().slice(0, 10))) && (
                    <Button size="sm" variant="ghost" onClick={() => setCancelling(row)}>
                      Cancel
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {cancelling && (
        <CancelDialog request={cancelling} onClose={() => setCancelling(null)} />
      )}
    </div>
  )
}

function CancelDialog({ request, onClose }: { request: LeaveRequest; onClose: () => void }) {
  const toast = useToast()
  const decide = useLeaveDecision(request.id)
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      loading={decide.isPending}
      tone="danger"
      title="Cancel this leave"
      description={`${request.leave_type_name}, ${formatDate(request.start_date)} – ${formatDate(request.end_date)} (${request.days} day(s)). The balance comes back immediately.`}
      confirmLabel="Cancel leave"
      onConfirm={() =>
        decide.mutate(
          { action: 'cancel' },
          {
            onSuccess: () => {
              toast.success('Leave cancelled', 'The balance has been restored.')
              onClose()
            },
            onError: (error) => toast.fromError(error, 'Could not cancel'),
          },
        )
      }
    />
  )
}

/* ============================================================= approvals */

function Approvals() {
  const pending = useLeaveRequests({ status: 'pending' })
  const [deciding, setDeciding] = useState<{ row: LeaveRequest; action: 'approve' | 'reject' } | null>(null)

  const rows = pending.data?.data ?? []

  return (
    <div className="space-y-4">
      <Banner tone="info" title="How the approval chain works">
        A request goes first to the employee's <strong>reporting manager</strong>; the
        manager's approval forwards it to the <strong>HR Head</strong>, whose approval is the
        final one. A manager's rejection ends the request. Employees without a configured
        manager go straight to the HR Head, and the HR Head's own leave is decided by Admin.
        Nobody can decide their own request — the server enforces all of this. Before
        approving, weigh the clinic's operational picture: patient appointments, treatment
        schedules and clinic timings — continuous leave that would close a clinic must not
        be approved. The Calendar tab shows who else is already away.
      </Banner>
      <Card className="space-y-3">
        <CardHeader
          title="Pending requests"
          description={`${rows.length} awaiting a decision`}
        />
        {pending.isLoading ? (
          <LoadingBlock />
        ) : rows.length === 0 ? (
          <EmptyState compact title="Nothing waiting" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-subtle">
                  <th className="py-2 pr-4">Employee</th>
                  <th className="py-2 pr-4">Type</th>
                  <th className="py-2 pr-4">Dates</th>
                  <th className="py-2 pr-4">Days</th>
                  <th className="py-2 pr-4">Reason</th>
                  <th className="py-2 pr-4">With</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td className="py-2.5 pr-4">
                      <p className="font-medium text-ink">{row.employee_name}</p>
                      <p className="text-xs text-ink-muted">
                        {row.department_name}
                        {row.designation_title && ` · ${row.designation_title}`}
                      </p>
                    </td>
                    <td className="py-2.5 pr-4">
                      {row.display_type ?? row.leave_type_name}
                      {(!row.is_paid || row.probation_unpaid) && <Badge tone="warning">unpaid</Badge>}
                      {row.is_emergency && <Badge tone="warning">emergency</Badge>}
                    </td>
                    <td className="py-2.5 pr-4 whitespace-nowrap">
                      {formatDate(row.start_date)} – {formatDate(row.end_date)}
                      {row.half_day && (
                        <span className="text-xs text-ink-muted"> ({row.half_day.replace('_', ' ')})</span>
                      )}
                    </td>
                    <td className="py-2.5 pr-4 tabular">{row.days}</td>
                    <td className="max-w-[16rem] py-2.5 pr-4 text-ink-muted">
                      <span className="block truncate" title={row.reason}>{row.reason}</span>
                      {row.has_attachment && (
                        <button
                          type="button"
                          className="text-xs text-brand hover:underline"
                          onClick={() =>
                            void downloadFile(
                              `/leave-requests/${row.id}/attachment/`,
                              'leave-attachment',
                            ).catch(() => undefined)
                          }
                        >
                          View document
                        </button>
                      )}
                    </td>
                    <td className="py-2.5 pr-4">
                      <Badge tone={row.approval_stage === 'manager' ? 'warning' : 'info'}>
                        {row.approval_stage === 'manager' ? 'Manager' : row.approval_band === 'admin' ? 'Admin' : 'HR Head'}
                      </Badge>
                      {row.approval_stage === 'hr' && row.manager_decided_at && (
                        <p className="mt-0.5 text-xs text-ink-subtle">manager approved</p>
                      )}
                    </td>
                    <td className="py-2.5 text-right whitespace-nowrap">
                      {row.can_decide ? (
                        <>
                          <Button
                            size="sm"
                            variant="primary"
                            onClick={() => setDeciding({ row, action: 'approve' })}
                          >
                            Approve
                          </Button>{' '}
                          <Button
                            size="sm"
                            variant="danger-soft"
                            onClick={() => setDeciding({ row, action: 'reject' })}
                          >
                            Reject
                          </Button>
                        </>
                      ) : (
                        <span className="text-xs text-ink-subtle">
                          Waiting for {row.pending_with}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <PatternsCard />

      {deciding && (
        <DecisionDialog
          row={deciding.row}
          action={deciding.action}
          onClose={() => setDeciding(null)}
        />
      )}
    </div>
  )
}

function PatternsCard() {
  const year = new Date().getFullYear()
  const patterns = useLeavePatterns(year)
  const rows = patterns.data?.data ?? []

  return (
    <Card className="space-y-3">
      <CardHeader
        title="Patterns to review"
        description={`Signals for ${year}: Monday/Friday clustering, last-minute requests, emergencies and short-leave hours. Numbers only — the conversation is yours.`}
      />
      {rows.length === 0 ? (
        <EmptyState compact title="Nothing to review yet" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wide text-ink-subtle">
                <th className="py-2 pr-4">Employee</th>
                <th className="py-2 pr-4">Requests</th>
                <th className="py-2 pr-4">Mon/Fri</th>
                <th className="py-2 pr-4">Last-minute</th>
                <th className="py-2 pr-4">Emergencies</th>
                <th className="py-2">Short-leave hours</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rows.map((row) => (
                <tr key={row.employee_id}>
                  <td className="py-2 pr-4 font-medium text-ink">{row.employee_name}</td>
                  <td className="py-2 pr-4 tabular">{row.requests}</td>
                  <td className="py-2 pr-4 tabular">
                    {row.monday_friday}
                    {row.monday_friday >= 3 && <Badge tone="warning">review</Badge>}
                  </td>
                  <td className="py-2 pr-4 tabular">
                    {row.last_minute}
                    {row.last_minute >= 3 && <Badge tone="warning">review</Badge>}
                  </td>
                  <td className="py-2 pr-4 tabular">{row.emergency}</td>
                  <td className="py-2 tabular">{row.short_leave_hours}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function DecisionDialog({
  row,
  action,
  onClose,
}: {
  row: LeaveRequest
  action: 'approve' | 'reject'
  onClose: () => void
}) {
  const toast = useToast()
  const decide = useLeaveDecision(row.id)
  const [reason, setReason] = useState('')
  const rejecting = action === 'reject'
  // A manager's approval only forwards the request; the HR stage grants it.
  const forwarding = !rejecting && row.approval_stage === 'manager'

  return (
    <ConfirmDialog
      open
      onClose={onClose}
      loading={decide.isPending}
      tone={rejecting ? 'danger' : 'primary'}
      title={
        rejecting
          ? 'Reject this leave'
          : forwarding
            ? 'Approve and forward to the HR Head'
            : 'Approve this leave'
      }
      description={`${row.employee_name} · ${row.leave_type_name} · ${formatDate(row.start_date)} – ${formatDate(row.end_date)} · ${row.days} day(s)`}
      confirmLabel={rejecting ? 'Reject' : forwarding ? 'Approve & forward' : 'Approve'}
      onConfirm={() => {
        if (rejecting && reason.trim().length < 3) {
          toast.error('A reason is required', 'The employee reads it — say what to change.')
          return
        }
        decide.mutate(
          {
            action,
            reason: rejecting ? reason.trim() : undefined,
            note: rejecting ? undefined : reason.trim() || undefined,
          },
          {
            onSuccess: () => {
              toast.success(
                rejecting
                  ? 'Leave rejected'
                  : forwarding
                    ? 'Forwarded to the HR Head'
                    : 'Leave approved',
                forwarding
                  ? 'The HR Head takes the final decision.'
                  : 'The employee has been notified.',
              )
              onClose()
            },
            onError: (error) => toast.fromError(error, 'Could not record the decision'),
          },
        )
      }}
    >
      <TextArea
        label={rejecting ? 'Reason' : 'Note (optional)'}
        required={rejecting}
        rows={2}
        placeholder={
          rejecting
            ? 'The employee reads this — say what to do differently.'
            : forwarding
              ? 'Anything the HR Head should know before the final decision.'
              : 'Optional note kept with the decision.'
        }
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      />
    </ConfirmDialog>
  )
}

/* ============================================================== calendar */

function LeaveCalendar() {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7))
  const from = `${month}-01`
  const to = useMemo(() => {
    const [y, m] = month.split('-').map(Number)
    return new Date(y, m, 0).toISOString().slice(0, 10)
  }, [month])
  const calendar = useLeaveCalendar({ from, to })
  const [department, setDepartment] = useState('')

  const rows = (calendar.data?.data ?? []).filter(
    (row) => !department || row.department_name === department,
  )
  const departments = [...new Set((calendar.data?.data ?? []).map((r) => r.department_name))].filter(Boolean)

  return (
    <Card className="space-y-4">
      <CardHeader title="Who is away" description="Approved and pending leave for the month." />
      <FieldRow>
        <TextInput
          label="Month"
          type="month"
          value={month}
          onChange={(event) => setMonth(event.target.value)}
        />
        <Select
          label="Department"
          placeholder="All departments"
          value={department}
          onChange={(event) => setDepartment(event.target.value)}
          options={departments.map((name) => ({ value: name, label: name }))}
        />
      </FieldRow>
      {calendar.isLoading ? (
        <LoadingBlock />
      ) : rows.length === 0 ? (
        <EmptyState compact title="Nobody is away this month" />
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((row) => (
            <li key={row.id} className="flex flex-wrap items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{row.employee_name}</p>
                <p className="text-xs text-ink-muted">
                  {row.department_name} · {row.leave_type_name}
                </p>
              </div>
              <span className="text-sm text-ink-muted">
                {formatDate(row.start_date)} – {formatDate(row.end_date)} · {row.days}d
              </span>
              <StatusBadge status={row.status} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

/* ================================================================= setup */

function Setup() {
  const toast = useToast()
  const types = useLeaveTypes()
  const policies = useLeavePolicies()
  const calendars = useHolidayCalendars()
  const addHoliday = useSaveHoliday()
  const removeHoliday = useDeleteHoliday()

  const [holiday, setHoliday] = useState({ date: '', name: '' })

  const calendarRows = calendars.data?.data ?? []
  const defaultCalendar = calendarRows.find((row) => row.location === null) ?? calendarRows[0]

  return (
    <div className="space-y-5">
    <div className="grid gap-5 lg:grid-cols-2">
      <Card className="space-y-3">
        <CardHeader
          title="Leave types & policies"
          description="The rules every application is validated against."
        />
        {(types.data ?? []).map((type) => {
          const policy = (policies.data?.data ?? []).find((row) => row.leave_type === type.id)
          return (
            <div key={type.id} className="rounded-lg border border-line p-3">
              <div className="flex items-center gap-2">
                <p className="text-sm font-medium text-ink">{type.name}</p>
                {!type.is_paid && <Badge tone="warning">unpaid</Badge>}
              </div>
              {policy ? (
                <p className="mt-1 text-xs text-ink-muted">
                  {policy.annual_allocation} days/year
                  {policy.carry_forward && ` · carries forward (max ${policy.carry_forward_limit})`}
                  {policy.min_notice_days > 0 && ` · ${policy.min_notice_days}d notice`}
                  {policy.max_consecutive_days > 0 && ` · max ${policy.max_consecutive_days} consecutive`}
                  {policy.requires_attachment && ' · attachment required'}
                  {policy.min_service_months > 0 && ` · after ${policy.min_service_months} months`}
                  {policy.allow_negative_balance && ' · may go negative'}
                </p>
              ) : (
                <p className="mt-1 text-xs text-warning">No policy — applications will be refused.</p>
              )}
            </div>
          )
        })}
        <p className="text-xs text-ink-subtle">
          PL/CL accrues monthly — the balance an employee can use is what has accrued so far,
          never the full year in advance. Carry-forward and exit encashment follow each
          policy's own flags.
        </p>
      </Card>

      <Card className="space-y-3">
        <CardHeader
          title="Holidays"
          description={
            defaultCalendar
              ? `${defaultCalendar.name} — weekly off: ${defaultCalendar.weekly_off
                  .map((d) => ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][d])
                  .join(', ') || 'none'}`
              : 'No calendar configured'
          }
        />
        {defaultCalendar && (
          <>
            <ul className="divide-y divide-line">
              {defaultCalendar.holidays.map((row) => (
                <li key={row.id} className="flex items-center gap-3 py-2">
                  <span className="flex-1 text-sm text-ink">{row.name}</span>
                  <span className="text-sm text-ink-muted">{formatDate(row.date)}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      removeHoliday.mutate(row.id, {
                        onSuccess: () => toast.success('Holiday removed'),
                        onError: (error) => toast.fromError(error, 'Could not remove'),
                      })
                    }
                  >
                    Remove
                  </Button>
                </li>
              ))}
              {defaultCalendar.holidays.length === 0 && (
                <li className="py-2 text-sm text-ink-muted">No holidays recorded yet.</li>
              )}
            </ul>
            <FieldRow>
              <TextInput
                label="Date"
                type="date"
                value={holiday.date}
                onChange={(event) => setHoliday((h) => ({ ...h, date: event.target.value }))}
              />
              <TextInput
                label="Name"
                placeholder="e.g. Diwali"
                value={holiday.name}
                onChange={(event) => setHoliday((h) => ({ ...h, name: event.target.value }))}
              />
            </FieldRow>
            <Button
              size="sm"
              variant="secondary"
              disabled={!holiday.date || !holiday.name}
              loading={addHoliday.isPending}
              onClick={() =>
                addHoliday.mutate(
                  { calendar: defaultCalendar.id, ...holiday },
                  {
                    onSuccess: () => {
                      toast.success('Holiday added')
                      setHoliday({ date: '', name: '' })
                    },
                    onError: (error) => toast.fromError(error, 'Could not add the holiday'),
                  },
                )
              }
            >
              + Add holiday
            </Button>
          </>
        )}
      </Card>
    </div>

    <LeaveSettingsCard />

    <div className="grid gap-5 lg:grid-cols-2">
      <ShortLeaveCard />
      <HolidayWorkCard />
    </div>
    </div>
  )
}

function LeaveSettingsCard() {
  const toast = useToast()
  const settings = useLeaveSettings()
  const update = useUpdateLeaveSettings()
  const [draft, setDraft] = useState<Partial<LeaveSettings>>({})

  const row = settings.data
  if (!row) return null

  const value = (key: keyof LeaveSettings) => String(draft[key] ?? row[key])
  const flag = (key: keyof LeaveSettings) =>
    (draft[key] ?? row[key]) === true || (draft[key] ?? row[key]) === 'true'

  const numberField = (key: keyof LeaveSettings, label: string, hint?: string) => (
    <TextInput
      label={label}
      type="number"
      value={value(key)}
      description={hint}
      onChange={(event) => setDraft((d) => ({ ...d, [key]: Number(event.target.value) }))}
    />
  )

  return (
    <Card className="space-y-4">
      <CardHeader
        title="Leave policy settings"
        description="The handbook's numbers, as configuration — nothing here is hard-coded."
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {numberField('single_day_notice_days', 'One-day leave notice (days)', '72 hours = 3 days')}
        {numberField('general_notice_days', 'Planned leave notice (working days)')}
        {numberField('long_leave_notice_days', 'Long leave notice (days)', 'Inform a month ahead')}
        {numberField('long_leave_threshold_days', 'Long leave means more than (days)')}
        {numberField('emergency_window_hours', 'Emergency window (hours)')}
        <TextInput
          label="Short-leave hours per full day"
          type="number"
          value={value('short_leave_hours_per_day')}
          description="Cumulative monthly short leave converting to one day"
          onChange={(event) =>
            setDraft((d) => ({ ...d, short_leave_hours_per_day: event.target.value }))
          }
        />
        {numberField('absence_flag_days', 'Flag uninformed absence after (working days)')}
      </div>
      <div className="flex flex-wrap gap-6">
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={flag('probation_leave_unpaid')}
            onChange={(event) =>
              setDraft((d) => ({ ...d, probation_leave_unpaid: event.target.checked }))
            }
          />
          Probation leave is unpaid (no PL/CL deduction)
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={flag('holiday_work_double_pay')}
            onChange={(event) =>
              setDraft((d) => ({ ...d, holiday_work_double_pay: event.target.checked }))
            }
          />
          Double pay for approved work on a public holiday
        </label>
      </div>
      <Button
        variant="primary"
        size="sm"
        loading={update.isPending}
        disabled={Object.keys(draft).length === 0}
        onClick={() =>
          update.mutate(draft, {
            onSuccess: () => {
              toast.success('Settings saved')
              setDraft({})
            },
            onError: (error) => toast.fromError(error, 'Could not save'),
          })
        }
      >
        Save settings
      </Button>
    </Card>
  )
}

function ShortLeaveCard() {
  const toast = useToast()
  const list = useShortLeaves()
  const record = useRecordShortLeave()
  const remove = useDeleteShortLeave()
  const employees = useEmployees({ page_size: '200' })
  const [form, setForm] = useState({ employee: '', date: '', hours: '', out_time: '', reason: '' })

  const rows = list.data?.data ?? []

  return (
    <Card className="space-y-3">
      <CardHeader
        title="Short leave register"
        description="Informed early departures. Monthly totals convert to full-day deductions automatically."
      />
      <ul className="max-h-56 divide-y divide-line overflow-y-auto">
        {rows.slice(0, 20).map((row) => (
          <li key={row.id} className="flex items-center gap-3 py-2 text-sm">
            <span className="min-w-0 flex-1 truncate text-ink">
              {row.employee_name} · {row.hours}h
              {row.out_time && <span className="text-ink-muted"> (out {row.out_time})</span>}
            </span>
            <span className="text-ink-muted">{formatDate(row.date)}</span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                remove.mutate(row.id, {
                  onError: (error) => toast.fromError(error, 'Could not remove'),
                })
              }
            >
              Remove
            </Button>
          </li>
        ))}
        {rows.length === 0 && <li className="py-2 text-sm text-ink-muted">Nothing recorded.</li>}
      </ul>
      <FieldRow>
        <Select
          label="Employee"
          placeholder="Select"
          value={form.employee}
          onChange={(event) => setForm((f) => ({ ...f, employee: event.target.value }))}
          options={(employees.data?.data ?? []).map((item) => ({
            value: item.id,
            label: item.full_name,
          }))}
        />
        <TextInput
          label="Date"
          type="date"
          value={form.date}
          onChange={(event) => setForm((f) => ({ ...f, date: event.target.value }))}
        />
      </FieldRow>
      <FieldRow>
        <TextInput
          label="Hours"
          type="number"
          value={form.hours}
          onChange={(event) => setForm((f) => ({ ...f, hours: event.target.value }))}
        />
        <TextInput
          label="Out time"
          type="time"
          value={form.out_time}
          onChange={(event) => setForm((f) => ({ ...f, out_time: event.target.value }))}
        />
      </FieldRow>
      <Button
        size="sm"
        variant="secondary"
        loading={record.isPending}
        disabled={!form.employee || !form.date || !form.hours}
        onClick={() =>
          record.mutate(
            {
              employee: form.employee,
              date: form.date,
              hours: form.hours,
              out_time: form.out_time || undefined,
              reason: form.reason || undefined,
            },
            {
              onSuccess: () => {
                toast.success('Short leave recorded')
                setForm({ employee: '', date: '', hours: '', out_time: '', reason: '' })
              },
              onError: (error) => toast.fromError(error, 'Could not record'),
            },
          )
        }
      >
        + Record short leave
      </Button>
    </Card>
  )
}

function HolidayWorkCard() {
  const toast = useToast()
  const list = useHolidayWorkList()
  const record = useRecordHolidayWork()
  const remove = useDeleteHolidayWork()
  const employees = useEmployees({ page_size: '200' })
  const [form, setForm] = useState({ employee: '', date: '', note: '' })

  const rows = list.data?.data ?? []

  return (
    <Card className="space-y-3">
      <CardHeader
        title="Public-holiday work"
        description="Approved work on a declared holiday — payroll pays that day double."
      />
      <ul className="max-h-56 divide-y divide-line overflow-y-auto">
        {rows.slice(0, 20).map((row) => (
          <li key={row.id} className="flex items-center gap-3 py-2 text-sm">
            <span className="min-w-0 flex-1 truncate text-ink">
              {row.employee_name} · {row.holiday_name}
            </span>
            <span className="text-ink-muted">{formatDate(row.date)}</span>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                remove.mutate(row.id, {
                  onError: (error) => toast.fromError(error, 'Could not remove'),
                })
              }
            >
              Remove
            </Button>
          </li>
        ))}
        {rows.length === 0 && <li className="py-2 text-sm text-ink-muted">Nothing recorded.</li>}
      </ul>
      <FieldRow>
        <Select
          label="Employee"
          placeholder="Select"
          value={form.employee}
          onChange={(event) => setForm((f) => ({ ...f, employee: event.target.value }))}
          options={(employees.data?.data ?? []).map((item) => ({
            value: item.id,
            label: item.full_name,
          }))}
        />
        <TextInput
          label="Holiday date"
          type="date"
          value={form.date}
          onChange={(event) => setForm((f) => ({ ...f, date: event.target.value }))}
        />
      </FieldRow>
      <Button
        size="sm"
        variant="secondary"
        loading={record.isPending}
        disabled={!form.employee || !form.date}
        onClick={() =>
          record.mutate(
            { employee: form.employee, date: form.date, note: form.note || undefined },
            {
              onSuccess: () => {
                toast.success('Holiday work recorded', 'Payroll will double-pay the day.')
                setForm({ employee: '', date: '', note: '' })
              },
              onError: (error) => toast.fromError(error, 'Could not record'),
            },
          )
        }
      >
        + Record holiday work
      </Button>
    </Card>
  )
}
