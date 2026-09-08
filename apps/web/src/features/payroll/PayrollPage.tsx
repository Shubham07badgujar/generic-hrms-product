/**
 * The payroll run list, and the statutory readiness panel above it.
 *
 * The readiness panel is deliberately the first thing on the page. Every rate
 * set starts unverified, no run can be approved until Finance verifies them,
 * and burying that on another screen is how a payroll team discovers it at the
 * moment they try to pay people.
 */

import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Card, CardHeader, PageHeader, Section } from '@/components/ui/Card'
import {
  CursorPager,
  DataTable,
  FilterSelect,
  TableToolbar,
  type Column,
} from '@/components/ui/DataTable'
import { CardSkeleton, EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Banner, Muted } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useAdjustments,
  useAdjustmentActions,
  useCreatePayrollRun,
  usePackageAlerts,
  usePayrollRuns,
  useStatutoryRuleSets,
} from '@/lib/payrollQueries'
import { useCurrentEmployees } from '@/lib/queries'
import { useListParams } from '@/hooks/useListParams'
import { ApiError } from '@/lib/api'
import { formatCurrency, formatDate, humanize } from '@/lib/format'
import type {
  PayrollAdjustment,
  PayrollRunStatusValue,
  PayrollRunSummary,
  StatutoryRuleSet,
} from '@/lib/types'

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

const STATUS_TONES: Record<PayrollRunStatusValue, Tone> = {
  draft: 'neutral',
  processing: 'info',
  review: 'warning',
  approved: 'brand',
  paid: 'success',
  reversed: 'danger',
}

export const MONTHS = [
  '', 'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export function periodLabel(run: { period_month: number; period_year: number }) {
  return `${MONTHS[run.period_month] ?? run.period_month} ${run.period_year}`
}

/* ------------------------------------------------ statutory readiness */

export function StatutoryReadiness({ ruleSets }: { ruleSets: StatutoryRuleSet[] }) {
  const unverified = ruleSets.filter((rs) => rs.verification_status !== 'verified')
  const tampered = ruleSets.filter((rs) => rs.is_tampered)

  if (ruleSets.length === 0) {
    return (
      <Banner tone="danger" title="No statutory rates are configured">
        Payroll cannot compute PF, ESI, Professional Tax or TDS without rate sets.
        Load them, then have Finance verify each one against its gazette source.
      </Banner>
    )
  }

  if (tampered.length > 0) {
    return (
      <Banner tone="danger" title="A verified rate set has been altered since verification">
        {tampered.map((rs) => describeRuleSet(rs)).join(', ')}. Verification has been
        withdrawn automatically. Finance must re-verify before payroll can be approved.
      </Banner>
    )
  }

  if (unverified.length > 0) {
    return (
      <Banner
        tone="warning"
        title={`${unverified.length} of ${ruleSets.length} statutory rate sets are not verified`}
      >
        Runs can be processed so the figures are visible, but none can be approved
        until Finance verifies these against their gazette source:{' '}
        <strong>{unverified.map((rs) => describeRuleSet(rs)).join(', ')}</strong>.
      </Banner>
    )
  }

  return (
    <Banner tone="success" title="All statutory rates are verified">
      {ruleSets.length} rate sets, each signed off by Finance. Payroll runs computed
      against them can be approved.
    </Banner>
  )
}

export function describeRuleSet(rs: StatutoryRuleSet) {
  const parts = [rs.statute.toUpperCase()]
  if (rs.jurisdiction) parts.push(rs.jurisdiction)
  if (rs.regime) parts.push(`${rs.regime} regime`)
  return parts.join(' · ')
}

/* --------------------------------------------------------- new run */

function NewRunDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const now = new Date()
  const [month, setMonth] = useState(String(now.getMonth() + 1))
  const [year, setYear] = useState(String(now.getFullYear()))
  const [runType, setRunType] = useState('regular')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const create = useCreatePayrollRun()
  const navigate = useNavigate()
  const toast = useToast()

  function submit() {
    setError('')
    create.mutate(
      {
        period_year: Number(year),
        period_month: Number(month),
        run_type: runType,
        notes,
      },
      {
        onSuccess: (run) => {
          toast.push({ tone: 'success', title: `Payroll run created for ${periodLabel(run)}.` })
          onClose()
          navigate(`/payroll/${run.id}`)
        },
        onError: (err) =>
          setError(err instanceof ApiError ? err.message : 'Could not create the run.'),
      },
    )
  }

  return (
    <Modal open={open} onClose={onClose} title="New payroll run">
      <div className="stack">
        {error ? <Banner tone="danger">{error}</Banner> : null}
        <Select
          label="Month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          options={MONTHS.slice(1).map((name, index) => ({
            value: String(index + 1),
            label: name,
          }))}
        />
        <Select
          label="Year"
          value={year}
          onChange={(e) => setYear(e.target.value)}
          options={[now.getFullYear() - 1, now.getFullYear(), now.getFullYear() + 1].map((y) => ({
            value: String(y),
            label: String(y),
          }))}
        />
        <Select
          label="Run type"
          value={runType}
          onChange={(e) => setRunType(e.target.value)}
          hint="Off-cycle and supplementary runs sit alongside the regular one for the same period."
          options={[
            { value: 'regular', label: 'Regular' },
            { value: 'off_cycle', label: 'Off-cycle' },
            { value: 'supplementary', label: 'Supplementary' },
          ]}
        />
        <TextArea
          label="Notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
        />
        <div className="row row--end">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={create.isPending}>
            {create.isPending ? 'Creating…' : 'Create run'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

/* ------------------------------------------------------------- page */

export function PayrollPage() {
  const permissions = usePermissions()
  const canView = permissions.can(RESOURCE.PAYROLL_RUN, ACTION.VIEW)
  const canCreate = permissions.can(RESOURCE.PAYROLL_RUN, ACTION.CREATE)
  const canSeeRates = permissions.can(RESOURCE.STATUTORY_CONFIG, ACTION.VIEW)

  const list = useListParams()
  const runs = usePayrollRuns(list.queryParams)
  const ruleSets = useStatutoryRuleSets(canSeeRates)
  const [newRun, setNewRun] = useState(false)

  const rows = runs.data?.data ?? []

  const columns: Column<PayrollRunSummary>[] = [
    {
      key: 'period',
      header: 'Period',
      render: (run) => (
        <Link to={`/payroll/${run.id}`} className="link inline-flex items-center gap-2">
          <span
            aria-hidden
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: LIME }}
          />
          <span>
            <strong>{periodLabel(run)}</strong>
            {run.run_type !== 'regular' ? (
              <Muted> · {humanize(run.run_type)} #{run.sequence}</Muted>
            ) : null}
          </span>
        </Link>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (run) => (
        <span className="row row--tight">
          <Badge tone={STATUS_TONES[run.status]}>{humanize(run.status)}</Badge>
          {run.locked ? <Badge tone="neutral">Locked</Badge> : null}
        </span>
      ),
    },
    { key: 'payslip_count', header: 'Payslips', render: (run) => run.payslip_count ?? 0 },
    {
      key: 'gross',
      header: 'Gross',
      align: 'right',
      render: (run) => formatCurrency(run.totals?.gross_earnings),
    },
    {
      key: 'net',
      header: 'Net pay',
      align: 'right',
      render: (run) => <strong>{formatCurrency(run.totals?.net_pay)}</strong>,
    },
    {
      key: 'approved',
      header: 'Approved',
      render: (run) => formatDate(run.approved_at),
    },
    {
      // The subtle period link kept getting missed — reviewers looked for an
      // Approve button HERE. The button opens the run detail, which is where
      // Approve/Reject actually live (they need the blockers panel beside
      // them), and says so when the run is waiting on exactly that.
      key: 'open',
      header: 'Actions',
      headerSrOnly: true,
      align: 'right',
      render: (run) => (
        <Link to={`/payroll/${run.id}`}>
          <Button size="sm" variant={run.status === 'review' ? 'primary' : undefined}>
            {run.status === 'review' ? 'Review & approve' : 'Open'}
          </Button>
        </Link>
      ),
    },
  ]

  if (!canView) {
    return (
      <>
        <PageHeader title="Payroll" />
        <EmptyState
          title="You do not have access to payroll runs"
          description="Payroll is restricted to finance and payroll staff. Your own payslips are under My payslips."
        />
      </>
    )
  }

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2.5">
            <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-brand">
              <span
                aria-hidden
                className="block h-4 w-4 rounded-full border-[3px]"
                style={{ borderColor: LIME }}
              />
            </span>
            Payroll
          </span>
        }
        description="Salary runs, statutory computation and payslips."
        actions={
          <span className="flex gap-2">
            {canSeeRates && (
              <Link to="/payroll/settings">
                <Button variant="ghost">Payroll settings</Button>
              </Link>
            )}
            {canCreate && <Button onClick={() => setNewRun(true)}>New run</Button>}
          </span>
        }
      />

      {canSeeRates ? (
        ruleSets.isLoading ? (
          <CardSkeleton />
        ) : ruleSets.data ? (
          <StatutoryReadiness ruleSets={ruleSets.data} />
        ) : null
      ) : null}

      <DeferredEligibleAlert />

      <Section title="Runs">
        <TableToolbar>
          <FilterSelect
            label="Status"
            value={String(list.filters.status ?? '')}
            onChange={(value) => list.setParam('status', value)}
            allLabel="All statuses"
            options={[
              { value: 'draft', label: 'Draft' },
              { value: 'review', label: 'In review' },
              { value: 'approved', label: 'Approved' },
              { value: 'paid', label: 'Paid' },
              { value: 'reversed', label: 'Reversed' },
            ]}
          />
        </TableToolbar>

        {runs.isLoading ? (
          <TableSkeleton columns={columns.length} />
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No payroll runs yet"
            description={
              canCreate
                ? 'Create a run for a period, process it, and it moves to review.'
                : 'Payroll staff create runs for each period.'
            }
            action={canCreate ? <Button onClick={() => setNewRun(true)}>New run</Button> : undefined}
          />
        ) : (
          <>
            <DataTable rows={rows} columns={columns} rowKey={(run) => run.id} />
            <CursorPager
              hasPrevious={Boolean(runs.data?.meta.previous)}
              hasNext={Boolean(runs.data?.meta.next)}
              onPrevious={() => list.setCursor(runs.data?.meta.previous ?? '')}
              onNext={() => list.setCursor(runs.data?.meta.next ?? '')}
              isFetching={runs.isFetching}
            />
          </>
        )}
      </Section>

      {permissions.can(RESOURCE.PAYROLL_ADJUSTMENT) && <AdjustmentsPanel />}

      <NewRunDialog open={newRun} onClose={() => setNewRun(false)} />
    </>
  )
}

/**
 * Deferred package amounts whose completion date has arrived. Eligible is
 * NOT paid: HR Head / Finance Head decide each release on the Packages page.
 */
function DeferredEligibleAlert() {
  const permissions = usePermissions()
  const alerts = usePackageAlerts(
    permissions.canAtLeast(RESOURCE.PACKAGE, ACTION.VIEW, 'all'),
  )
  const rows = alerts.data?.eligible ?? []
  if (rows.length === 0) return null
  return (
    <Banner tone="warning" title="Deferred package eligible for release">
      {rows.map((row) => (
        <p key={row.package}>
          <strong>{row.employee_name}</strong> ({row.employee_code}) —{' '}
          {row.amounts.map((a) => `${formatCurrency(a.amount)} (${a.label}, eligible ${formatDate(a.eligible_on)})`).join('; ')}
        </p>
      ))}
      <p className="mt-1">
        <Link to="/payroll/packages" className="link">
          Review and approve on the Packages page
        </Link>
        {' '}— nothing is paid until HR Head or Finance Head approves the release.
      </p>
    </Banner>
  )
}

/* ------------------------------------------------------- adjustments */

const ADJUSTMENT_KINDS = [
  { value: 'bonus', label: 'Bonus' },
  { value: 'incentive', label: 'Incentive' },
  { value: 'arrear', label: 'Arrears' },
  { value: 'reimbursement', label: 'Reimbursement' },
  { value: 'other_earning', label: 'Other earning' },
  { value: 'advance_recovery', label: 'Advance recovery' },
  { value: 'loan_recovery', label: 'Loan EMI recovery' },
  { value: 'other_deduction', label: 'Other deduction' },
]

/**
 * One-off amounts for a period. A draft is a proposal; only after HR Head or
 * Finance Head approves it will the period's run pick it up.
 */
function AdjustmentsPanel() {
  const permissions = usePermissions()
  const toast = useToast()
  const now = new Date()
  const [month, setMonth] = useState(String(now.getMonth() + 1))
  const [year, setYear] = useState(String(now.getFullYear()))
  const adjustments = useAdjustments({ period_month: month, period_year: year })
  const actions = useAdjustmentActions()
  const employees = useCurrentEmployees()

  const canCreate = permissions.can(RESOURCE.PAYROLL_ADJUSTMENT, ACTION.CREATE)
  const canApprove = permissions.can(RESOURCE.PAYROLL_ADJUSTMENT, ACTION.APPROVE)

  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ employee: '', kind: 'bonus', label: '', amount: '', notes: '' })
  const [error, setError] = useState('')

  const rows = adjustments.data?.data ?? []

  const STATUS: Record<PayrollAdjustment['status'], Tone> = {
    draft: 'neutral', approved: 'brand', applied: 'success', rejected: 'danger',
  }

  return (
    <Section title="Adjustments">
      <Card className="space-y-4">
        <CardHeader
          title="One-off amounts"
          description="Bonuses, incentives, arrears and recoveries for a period. Drafts must be approved by HR Head or Finance Head before a run pays them."
          action={
            <span className="flex items-end gap-2">
              <Select label="" aria-label="Month" value={month} onChange={(e) => setMonth(e.target.value)}
                options={MONTHS.slice(1).map((name, i) => ({ value: String(i + 1), label: name }))} />
              <Select label="" aria-label="Year" value={year} onChange={(e) => setYear(e.target.value)}
                options={[now.getFullYear() - 1, now.getFullYear(), now.getFullYear() + 1]
                  .map((y) => ({ value: String(y), label: String(y) }))} />
              {canCreate && (
                <Button size="sm" variant="primary" onClick={() => { setError(''); setAdding(true) }}>
                  Add adjustment
                </Button>
              )}
            </span>
          }
        />
        {rows.length === 0 ? (
          <EmptyState compact title="No adjustments for this period" />
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-ink">
                    {row.label} — {row.employee_name}
                  </p>
                  <p className="text-xs text-ink-muted">
                    {humanize(row.kind)} · {row.employee_code}
                  </p>
                </div>
                <span className="text-sm tabular">{formatCurrency(row.amount)}</span>
                <Badge tone={STATUS[row.status]}>{humanize(row.status)}</Badge>
                {canApprove && row.status === 'draft' && (
                  <Button size="sm" variant="primary" loading={actions.approve.isPending}
                    onClick={() =>
                      actions.approve.mutate(row.id, {
                        onSuccess: () => toast.success('Adjustment approved'),
                        onError: (err) => toast.fromError(err, 'Could not approve'),
                      })
                    }>
                    Approve
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Modal
        open={adding}
        onClose={() => setAdding(false)}
        busy={actions.create.isPending}
        size="md"
        title="Add adjustment"
        description={`For ${MONTHS[Number(month)]} ${year}. It stays a draft until a head approves it.`}
        footer={
          <>
            <Button onClick={() => setAdding(false)}>Cancel</Button>
            <Button variant="primary" loading={actions.create.isPending}
              disabled={!form.employee || !form.label.trim() || !form.amount}
              onClick={() =>
                actions.create.mutate(
                  {
                    employee: form.employee as never,
                    kind: form.kind as PayrollAdjustment['kind'],
                    label: form.label,
                    amount: form.amount,
                    period_month: Number(month),
                    period_year: Number(year),
                    notes: form.notes,
                  },
                  {
                    onSuccess: () => {
                      toast.success('Adjustment drafted')
                      setAdding(false)
                      setForm({ employee: '', kind: 'bonus', label: '', amount: '', notes: '' })
                    },
                    onError: (err) =>
                      setError(err instanceof ApiError ? err.displayMessage : 'Could not save.'),
                  },
                )
              }>
              Save draft
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <Banner tone="danger">{error}</Banner>}
          <Select label="Employee" required placeholder="Select an employee" value={form.employee}
            onChange={(e) => setForm({ ...form, employee: e.target.value })}
            options={employees.rows.map((row) => ({
              value: row.id, label: `${row.full_name} — ${row.employee_code}`,
            }))} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Select label="Kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}
              options={ADJUSTMENT_KINDS} />
            <TextInput label="Amount (₹)" type="number" min={0} required value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })} />
          </div>
          <TextInput label="Label (appears on the payslip)" required value={form.label}
            onChange={(e) => setForm({ ...form, label: e.target.value })} />
          <TextArea label="Notes" rows={2} value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        </div>
      </Modal>
    </Section>
  )
}
