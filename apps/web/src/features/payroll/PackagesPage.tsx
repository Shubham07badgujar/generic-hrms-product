/**
 * Custom Packages — the HR/Finance dashboard for package schedules.
 *
 * The package is the agreement layer: periods paid through normal monthly
 * payroll, deferred amounts released only after an explicit HR Head /
 * Finance Head approval. Monthly pay itself always flows from the salary
 * structure — this page never pays anyone directly.
 */

import { useMemo, useState } from 'react'
import { Card, CardHeader, DescriptionList, PageHeader, Section } from '@/components/ui/Card'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  usePackageActions,
  usePackages,
  useSavePackage,
  type PackagePayload,
} from '@/lib/payrollQueries'
import { useCurrentEmployees } from '@/lib/queries'
import { ApiError } from '@/lib/api'
import { formatCurrency, formatDate, humanize } from '@/lib/format'
import { MONTHS } from './PayrollPage'
import type { EmployeePackage, PackageDeferral, UUID } from '@/lib/types'

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

const STATUS_TONES: Record<EmployeePackage['status'], Tone> = {
  draft: 'neutral', active: 'success', completed: 'brand',
  on_hold: 'warning', cancelled: 'danger',
}

const DEFERRAL_TONES: Record<PackageDeferral['effective_status'], Tone> = {
  pending: 'neutral', eligible: 'warning', approved: 'brand',
  paid: 'success', rejected: 'danger', on_hold: 'warning', cancelled: 'neutral',
}

const PACKAGE_TYPES = [
  { value: 'monthly_plus_deferred', label: 'Monthly fixed + deferred amount' },
  { value: 'year_wise', label: 'Year-wise package' },
  { value: 'period_wise', label: 'Custom period-wise package' },
  { value: 'milestone', label: 'Milestone / completion-based release' },
]

const CONDITIONS = [
  { value: 'after_months', label: 'After completing N months' },
  { value: 'on_date', label: 'On a specific date' },
  { value: 'bond_completion', label: 'Completion of bond/service period' },
  { value: 'manual', label: 'Manual approval' },
  { value: 'other', label: 'Other configured condition' },
]

interface PeriodDraft { label: string; start_date: string; end_date: string; amount: string }
interface DeferralDraft {
  label: string; amount: string; condition_type: string
  condition_months: string; eligible_on: string; condition_note: string
}

const EMPTY_FORM = {
  employee: '', total_amount: '', package_type: 'monthly_plus_deferred',
  start_date: '', end_date: '', notes: '', reason: '',
  bond_end_date: '', bond_required_months: '',
}

function toNumber(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

/* ============================================================ editor */

function PackageEditor({
  existing,
  revise,
  onClose,
}: {
  existing: EmployeePackage | null
  revise: boolean
  onClose: () => void
}) {
  const toast = useToast()
  const employees = useCurrentEmployees()
  const save = useSavePackage()
  const actions = usePackageActions()

  const [form, setForm] = useState(() =>
    existing
      ? {
          employee: existing.employee,
          total_amount: existing.total_amount,
          package_type: existing.package_type,
          start_date: existing.start_date,
          end_date: existing.end_date,
          notes: existing.notes ?? '',
          reason: '',
          bond_end_date: existing.bond_end_date ?? '',
          bond_required_months: existing.bond_required_months?.toString() ?? '',
        }
      : EMPTY_FORM,
  )
  const [periods, setPeriods] = useState<PeriodDraft[]>(
    existing?.periods.map((p) => ({
      label: p.label, start_date: p.start_date, end_date: p.end_date, amount: p.amount,
    })) ?? [{ label: 'Months 1–12', start_date: '', end_date: '', amount: '' }],
  )
  const [deferrals, setDeferrals] = useState<DeferralDraft[]>(
    existing?.deferrals
      .filter((d) => !['approved', 'paid', 'rejected'].includes(d.status))
      .map((d) => ({
        label: d.label, amount: d.amount, condition_type: d.condition_type,
        condition_months: d.condition_months?.toString() ?? '',
        eligible_on: d.eligible_on ?? '', condition_note: d.condition_note,
      })) ?? [],
  )
  const [error, setError] = useState('')

  // Live allocation check — the same arithmetic the backend enforces.
  const allocated =
    periods.reduce((sum, p) => sum + toNumber(p.amount), 0) +
    deferrals.reduce((sum, d) => sum + toNumber(d.amount), 0)
  const gap = toNumber(form.total_amount) - allocated

  function submit() {
    setError('')
    const payload: PackagePayload = {
      employee: form.employee as UUID,
      total_amount: form.total_amount,
      package_type: form.package_type,
      start_date: form.start_date,
      end_date: form.end_date,
      notes: form.notes,
      reason: form.reason,
      bond_end_date: form.bond_end_date || null,
      bond_required_months: form.bond_required_months
        ? Number(form.bond_required_months) : null,
      periods: periods.filter((p) => p.start_date && p.end_date && p.amount),
      deferrals: deferrals
        .filter((d) => d.amount)
        .map((d) => ({
          label: d.label || 'Deferred amount',
          amount: d.amount,
          condition_type: d.condition_type,
          condition_months: d.condition_months ? Number(d.condition_months) : null,
          eligible_on: d.eligible_on || null,
          condition_note: d.condition_note,
        })),
    }
    const done = {
      onSuccess: () => {
        toast.success(
          revise ? 'Revised schedule created (previous kept as history)'
          : existing ? 'Package updated' : 'Package drafted',
        )
        onClose()
      },
      onError: (err: Error) =>
        setError(err instanceof ApiError ? err.displayMessage : 'Could not save.'),
    }
    if (revise && existing) actions.revise.mutate({ id: existing.id, ...payload }, done)
    else if (existing) save.mutate({ id: existing.id, ...payload }, done)
    else save.mutate(payload, done)
  }

  const busy = save.isPending || actions.revise.isPending

  return (
    <Modal
      open
      onClose={onClose}
      busy={busy}
      size="lg"
      title={revise ? 'Revise package schedule' : existing ? 'Edit package' : 'New custom package'}
      description={
        revise
          ? 'The current schedule is closed and kept as history; this creates its replacement.'
          : 'The agreed total, how it is paid month by month, and what is deferred. Activation requires the allocation to balance to the rupee.'
      }
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" loading={busy}
            disabled={!form.employee || !form.total_amount || !form.start_date || !form.end_date}
            onClick={submit}>
            {revise ? 'Create revised schedule' : existing ? 'Save changes' : 'Create draft'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}

        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Employee" required placeholder="Select an employee"
            value={form.employee} disabled={Boolean(existing)}
            onChange={(e) => setForm({ ...form, employee: e.target.value })}
            options={employees.rows.map((row) => ({
              value: row.id, label: `${row.full_name} — ${row.employee_code}`,
            }))} />
          <Select label="Package type" value={form.package_type}
            onChange={(e) => setForm({ ...form, package_type: e.target.value })}
            options={PACKAGE_TYPES} />
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <TextInput label="Total package (₹)" type="number" min={0} required
            value={form.total_amount}
            onChange={(e) => setForm({ ...form, total_amount: e.target.value })} />
          <TextInput label="Package start" type="date" required value={form.start_date}
            onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
          <TextInput label="Package end" type="date" required value={form.end_date}
            onChange={(e) => setForm({ ...form, end_date: e.target.value })} />
        </div>

        {/* ------------------------------------------------ paid monthly */}
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">
            Periods paid through monthly payroll
          </p>
          {periods.map((period, index) => (
            <div key={index} className="flex flex-wrap items-end gap-2">
              <div className="w-36">
                <TextInput label={index === 0 ? 'Label' : ''} value={period.label}
                  placeholder={`Period ${index + 1}`}
                  onChange={(e) => setPeriods(periods.map((p, i) =>
                    i === index ? { ...p, label: e.target.value } : p))} />
              </div>
              <TextInput label={index === 0 ? 'From' : ''} type="date" value={period.start_date}
                onChange={(e) => setPeriods(periods.map((p, i) =>
                  i === index ? { ...p, start_date: e.target.value } : p))} />
              <TextInput label={index === 0 ? 'To' : ''} type="date" value={period.end_date}
                onChange={(e) => setPeriods(periods.map((p, i) =>
                  i === index ? { ...p, end_date: e.target.value } : p))} />
              <div className="w-36">
                <TextInput label={index === 0 ? 'Period total (₹)' : ''} type="number"
                  value={period.amount}
                  onChange={(e) => setPeriods(periods.map((p, i) =>
                    i === index ? { ...p, amount: e.target.value } : p))} />
              </div>
              <Button size="sm" variant="ghost" disabled={periods.length === 1}
                onClick={() => setPeriods(periods.filter((_, i) => i !== index))}
                aria-label={`Remove period ${index + 1}`}>
                Remove
              </Button>
            </div>
          ))}
          <Button size="sm"
            onClick={() => setPeriods([...periods, { label: '', start_date: '', end_date: '', amount: '' }])}>
            Add period
          </Button>
          <p className="text-xs text-ink-muted">
            The monthly figure derives from each period (total ÷ months) — Year 1 at ₹10,00,000
            means ₹83,333.33/month. Actual pay still flows from the employee&rsquo;s salary
            structure; keep the structure in step when a new period begins.
          </p>
        </div>

        {/* --------------------------------------------------- deferred */}
        <div className="space-y-2">
          <p className="text-sm font-medium text-ink">Deferred amounts</p>
          {deferrals.map((deferral, index) => (
            <div key={index} className="space-y-2 rounded-lg border border-line p-3">
              <div className="flex flex-wrap items-end gap-2">
                <div className="min-w-40 flex-1">
                  <TextInput label="Label" value={deferral.label}
                    placeholder="Completion amount"
                    onChange={(e) => setDeferrals(deferrals.map((d, i) =>
                      i === index ? { ...d, label: e.target.value } : d))} />
                </div>
                <div className="w-36">
                  <TextInput label="Amount (₹)" type="number" value={deferral.amount}
                    onChange={(e) => setDeferrals(deferrals.map((d, i) =>
                      i === index ? { ...d, amount: e.target.value } : d))} />
                </div>
                <Button size="sm" variant="ghost"
                  onClick={() => setDeferrals(deferrals.filter((_, i) => i !== index))}
                  aria-label={`Remove deferred amount ${index + 1}`}>
                  Remove
                </Button>
              </div>
              <div className="flex flex-wrap items-end gap-2">
                <div className="min-w-52">
                  <Select label="Release condition" value={deferral.condition_type}
                    onChange={(e) => setDeferrals(deferrals.map((d, i) =>
                      i === index ? { ...d, condition_type: e.target.value } : d))}
                    options={CONDITIONS} />
                </div>
                {deferral.condition_type === 'after_months' && (
                  <div className="w-32">
                    <TextInput label="Months" type="number" min={1}
                      value={deferral.condition_months}
                      onChange={(e) => setDeferrals(deferrals.map((d, i) =>
                        i === index ? { ...d, condition_months: e.target.value } : d))}
                      description="Years × 12" />
                  </div>
                )}
                {deferral.condition_type === 'on_date' && (
                  <TextInput label="Release date" type="date" value={deferral.eligible_on}
                    onChange={(e) => setDeferrals(deferrals.map((d, i) =>
                      i === index ? { ...d, eligible_on: e.target.value } : d))} />
                )}
                {(deferral.condition_type === 'other'
                  || deferral.condition_type === 'manual') && (
                  <div className="min-w-60 flex-1">
                    <TextInput label="Condition note" value={deferral.condition_note}
                      onChange={(e) => setDeferrals(deferrals.map((d, i) =>
                        i === index ? { ...d, condition_note: e.target.value } : d))} />
                  </div>
                )}
              </div>
            </div>
          ))}
          <Button size="sm"
            onClick={() => setDeferrals([...deferrals, {
              label: '', amount: '', condition_type: 'after_months',
              condition_months: '', eligible_on: '', condition_note: '',
            }])}>
            Add deferred amount
          </Button>
        </div>

        {/* live allocation check */}
        {form.total_amount && (
          <Banner tone={gap === 0 ? 'success' : 'warning'}
            title={
              gap === 0
                ? 'Fully allocated'
                : gap > 0
                  ? `${formatCurrency(String(gap))} of the total package is not yet allocated`
                  : `Allocation exceeds the total package by ${formatCurrency(String(-gap))}`
            }>
            {formatCurrency(String(allocated))} allocated of {formatCurrency(form.total_amount)}.
            Activation requires an exact balance.
          </Banner>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <TextInput label="Bond / service period ends (optional)" type="date"
            value={form.bond_end_date}
            onChange={(e) => setForm({ ...form, bond_end_date: e.target.value })}
            description="Informational. No deduction or recovery ever happens automatically." />
          <TextInput label="Required completion (months, optional)" type="number" min={0}
            value={form.bond_required_months}
            onChange={(e) => setForm({ ...form, bond_required_months: e.target.value })} />
        </div>
        <TextArea label="Internal notes (never shown to the employee)" rows={2}
          value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
        {(existing || revise) && (
          <TextInput label="Reason for this change" value={form.reason}
            onChange={(e) => setForm({ ...form, reason: e.target.value })}
            description="Recorded in the audit log." />
        )}
      </div>
    </Modal>
  )
}

/* ============================================================ detail */

function PackageDetail({
  packageId,
  onClose,
  onEdit,
  onRevise,
}: {
  packageId: UUID
  onClose: () => void
  onEdit: (row: EmployeePackage) => void
  onRevise: (row: EmployeePackage) => void
}) {
  const permissions = usePermissions()
  const toast = useToast()
  const actions = usePackageActions()
  const query = usePackages({})
  const row = useMemo(
    () => (query.data?.data ?? []).find((p) => p.id === packageId) ?? null,
    [query.data, packageId],
  )
  const [releasing, setReleasing] = useState<PackageDeferral | null>(null)
  const now = new Date()
  const [month, setMonth] = useState(String(now.getMonth() + 1))
  const [year, setYear] = useState(String(now.getFullYear()))
  const [reason, setReason] = useState('')

  if (!row) return null
  const mayEdit = permissions.can(RESOURCE.PACKAGE, ACTION.EDIT)
  const mayApprove = permissions.can(RESOURCE.PACKAGE, ACTION.APPROVE)

  return (
    <Modal
      open
      onClose={onClose}
      size="lg"
      title={`${row.employee_name} — ${formatCurrency(row.total_amount)}`}
      description={`${humanize(row.package_type)} · ${formatDate(row.start_date)} → ${formatDate(row.end_date)}`}
      footer={
        <>
          {mayEdit && row.status !== 'cancelled' && (
            <Button onClick={() => onEdit(row)}>Edit</Button>
          )}
          {mayEdit && (row.status === 'active' || row.status === 'on_hold') && (
            <Button onClick={() => onRevise(row)}>Revise (keep history)</Button>
          )}
          {mayApprove && (row.status === 'draft' || row.status === 'on_hold') && (
            <Button variant="primary" loading={actions.activate.isPending}
              onClick={() =>
                actions.activate.mutate(row.id, {
                  onSuccess: () => toast.success('Package activated'),
                  onError: (err) => toast.fromError(err, 'Activation refused'),
                })
              }>
              Activate
            </Button>
          )}
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATUS_TONES[row.status]}>{humanize(row.status)}</Badge>
          {row.supersedes && <Badge tone="neutral">Revision</Badge>}
        </div>

        <DescriptionList columns={3} items={[
          { label: 'Total package', value: formatCurrency(row.total_amount) },
          { label: 'Paid through payroll', value: formatCurrency(row.summary.paid_so_far) },
          { label: 'Deferred', value: formatCurrency(row.summary.deferred_total) },
          {
            label: 'Released',
            value: `${formatCurrency(row.summary.released_total)} / ${formatCurrency(row.summary.deferred_total)}`,
          },
          { label: 'Remaining deferred', value: formatCurrency(row.summary.remaining_deferred) },
          { label: 'Next release', value: formatDate(row.summary.next_release_date) },
        ]} />

        <Card className="space-y-2">
          <CardHeader title="Schedule" />
          <ul className="divide-y divide-line">
            {row.periods.map((period) => (
              <li key={period.id} className="flex items-center gap-3 py-2">
                <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: LIME }} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-ink">{period.label}</p>
                  <p className="text-xs text-ink-muted">
                    {formatDate(period.start_date)} → {formatDate(period.end_date)} ·{' '}
                    {formatCurrency(period.monthly_amount)}/month
                  </p>
                </div>
                <span className="text-sm tabular">{formatCurrency(period.amount)}</span>
              </li>
            ))}
            {row.deferrals.map((deferral) => (
              <li key={deferral.id} className="flex flex-wrap items-center gap-3 py-2">
                <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-ink-subtle" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-ink">{deferral.label} (deferred)</p>
                  <p className="text-xs text-ink-muted">
                    {humanize(deferral.condition_type)}
                    {deferral.eligible_on ? ` · eligible ${formatDate(deferral.eligible_on)}` : ''}
                    {deferral.decision_reason ? ` · ${deferral.decision_reason}` : ''}
                  </p>
                </div>
                <span className="text-sm tabular">{formatCurrency(deferral.amount)}</span>
                <Badge tone={DEFERRAL_TONES[deferral.effective_status]}>
                  {humanize(deferral.effective_status)}
                </Badge>
                {mayApprove && deferral.effective_status === 'eligible' && (
                  <Button size="sm" variant="primary" onClick={() => setReleasing(deferral)}>
                    Decide
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      </div>

      {releasing && (
        <Modal
          open
          onClose={() => setReleasing(null)}
          busy={actions.decide.isPending}
          size="sm"
          title={`Release ${formatCurrency(releasing.amount)}?`}
          description="Approval schedules it into the chosen payroll period as its own earning line — Deferred Package Release."
          footer={
            <>
              <Button variant="danger-soft" loading={actions.decide.isPending}
                onClick={() =>
                  actions.decide.mutate(
                    { packageId: row.id, deferralId: releasing.id, decision: 'reject', reason },
                    {
                      onSuccess: () => { toast.success('Release rejected'); setReleasing(null) },
                      onError: (err) => toast.fromError(err, 'Refused'),
                    },
                  )
                }>
                Reject
              </Button>
              <Button loading={actions.decide.isPending}
                onClick={() =>
                  actions.decide.mutate(
                    { packageId: row.id, deferralId: releasing.id, decision: 'hold', reason },
                    {
                      onSuccess: () => { toast.success('Put on hold'); setReleasing(null) },
                      onError: (err) => toast.fromError(err, 'Refused'),
                    },
                  )
                }>
                Hold
              </Button>
              <Button variant="primary" loading={actions.decide.isPending}
                onClick={() =>
                  actions.decide.mutate(
                    {
                      packageId: row.id, deferralId: releasing.id, decision: 'approve',
                      period_year: Number(year), period_month: Number(month), reason,
                    },
                    {
                      onSuccess: () => { toast.success('Release approved and scheduled'); setReleasing(null) },
                      onError: (err) => toast.fromError(err, 'Refused'),
                    },
                  )
                }>
                Approve release
              </Button>
            </>
          }
        >
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Select label="Pay in month" value={month}
                onChange={(e) => setMonth(e.target.value)}
                options={MONTHS.slice(1).map((name, i) => ({ value: String(i + 1), label: name }))} />
              <Select label="Year" value={year} onChange={(e) => setYear(e.target.value)}
                options={[now.getFullYear(), now.getFullYear() + 1].map((y) => ({
                  value: String(y), label: String(y),
                }))} />
            </div>
            <TextInput label="Reason / note" value={reason}
              onChange={(e) => setReason(e.target.value)}
              description="Required for reject; recorded in the audit log for every decision." />
          </div>
        </Modal>
      )}
    </Modal>
  )
}

/* ============================================================== page */

export function PackagesPage() {
  const permissions = usePermissions()
  const query = usePackages({})
  const mayCreate = permissions.can(RESOURCE.PACKAGE, ACTION.CREATE)

  const [editor, setEditor] = useState<{ existing: EmployeePackage | null; revise: boolean } | null>(null)
  const [detailId, setDetailId] = useState<UUID | null>(null)

  const rows = query.data?.data ?? []

  const columns: Array<Column<EmployeePackage>> = [
    {
      key: 'employee', header: 'Employee',
      render: (row) => (
        <div className="flex min-w-0 items-center gap-2">
          <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: LIME }} />
          <div className="min-w-0">
            <p className="truncate font-medium text-ink">{row.employee_name}</p>
            <p className="truncate text-xs text-ink-muted">{row.employee_code}</p>
          </div>
        </div>
      ),
    },
    { key: 'total', header: 'Package', align: 'right', render: (row) => formatCurrency(row.total_amount) },
    { key: 'paid', header: 'Paid', align: 'right', render: (row) => formatCurrency(row.summary.paid_so_far) },
    { key: 'deferred', header: 'Deferred', align: 'right', render: (row) => formatCurrency(row.summary.deferred_total) },
    {
      key: 'released', header: 'Released', align: 'right',
      render: (row) => formatCurrency(row.summary.released_total),
    },
    { key: 'next', header: 'Next release', render: (row) => formatDate(row.summary.next_release_date) },
    {
      key: 'status', header: 'Status',
      render: (row) => (
        <span className="flex flex-wrap gap-1">
          <Badge tone={STATUS_TONES[row.status]}>{humanize(row.status)}</Badge>
          {row.deferrals.some((d) => d.effective_status === 'eligible') && (
            <Badge tone="warning">Release eligible</Badge>
          )}
        </span>
      ),
    },
    {
      key: 'actions', header: 'Actions', headerSrOnly: true, align: 'right',
      render: (row) => (
        <Button size="sm" onClick={() => setDetailId(row.id)}>Open</Button>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2.5">
            <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-brand">
              <span aria-hidden className="block h-4 w-4 rounded-full border-[3px]"
                style={{ borderColor: LIME }} />
            </span>
            Custom packages
          </span>
        }
        description="Employee-specific salary schedules and deferred releases. Monthly pay still flows from salary structures; releases enter payroll only after HR Head / Finance Head approval."
        actions={
          mayCreate ? (
            <Button variant="primary" onClick={() => setEditor({ existing: null, revise: false })}>
              New package
            </Button>
          ) : undefined
        }
      />

      <Section>
        {query.isLoading ? (
          <TableSkeleton columns={8} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No custom packages"
            description={
              mayCreate
                ? 'Employees without a package are paid the normal way: CTC → salary structure → payroll. Create a package only where the agreement genuinely differs.'
                : 'HR Head and Finance Head configure package schedules.'
            }
            action={mayCreate ? (
              <Button onClick={() => setEditor({ existing: null, revise: false })}>New package</Button>
            ) : undefined}
          />
        ) : (
          <DataTable caption="Custom packages" columns={columns} rows={rows}
            rowKey={(row) => row.id} />
        )}
      </Section>

      {editor && (
        <PackageEditor existing={editor.existing} revise={editor.revise}
          onClose={() => setEditor(null)} />
      )}
      {detailId && !editor && (
        <PackageDetail
          packageId={detailId}
          onClose={() => setDetailId(null)}
          onEdit={(row) => setEditor({ existing: row, revise: false })}
          onRevise={(row) => setEditor({ existing: row, revise: true })}
        />
      )}
    </>
  )
}
