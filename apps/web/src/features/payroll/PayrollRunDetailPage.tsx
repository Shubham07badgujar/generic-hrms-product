/**
 * One payroll run: its blockers, its lifecycle actions, its payslips.
 *
 * The Approve button is disabled from `run.can_approve` — the SERVER's answer,
 * from the same function that guards the action. This page never works out for
 * itself whether a run is approvable; if it did, the button and the endpoint
 * could disagree, and the disagreement would surface as a confusing failure at
 * exactly the moment someone is trying to pay people.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Card, CardHeader, DescriptionList, PageHeader, Section, StatCard } from '@/components/ui/Card'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { EmptyState, ErrorState, LoadingBlock } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Banner, Muted } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { ConfirmDialog, ReasonDialog } from '@/components/ui/ConfirmDialog'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  payrollDownloads,
  usePayrollRun,
  usePayrollRunActions,
} from '@/lib/payrollQueries'
import { ApiError, downloadFile } from '@/lib/api'
import { formatCurrency, formatDateTime, humanize } from '@/lib/format'
import type { PayrollBlocker, PayrollRunDetail, PayslipSummary } from '@/lib/types'
import { periodLabel } from './PayrollPage'

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

const STATUS_TONES: Record<string, Tone> = {
  draft: 'neutral',
  processing: 'info',
  review: 'warning',
  approved: 'brand',
  paid: 'success',
  reversed: 'danger',
}

const GATE_LABELS: Record<string, string> = {
  statutory: 'Statutory rates',
  payslips: 'Payslips',
  segregation: 'Segregation of duties',
}

/* ---------------------------------------------------------- blockers */

export function BlockerPanel({ blockers }: { blockers: PayrollBlocker[] }) {
  if (blockers.length === 0) return null

  return (
    <Banner
      tone="warning"
      title={`${blockers.length} thing${blockers.length === 1 ? '' : 's'} must be resolved before this run can be approved`}
    >
      <ul className="mt-2 space-y-1.5">
        {blockers.map((blocker) => (
          <li key={blocker.id} className="text-sm">
            <strong>{GATE_LABELS[blocker.gate] ?? humanize(blocker.gate)}</strong>
            {' — '}
            {blocker.detail}
          </li>
        ))}
      </ul>
    </Banner>
  )
}

/* -------------------------------------------------- statutory provenance */

function RuleSetsUsed({ run }: { run: PayrollRunDetail }) {
  const entries = Object.entries(run.rule_sets_used ?? {})
  if (entries.length === 0) {
    return (
      <EmptyState
        title="Not computed yet"
        description="Process the run to see which statutory rate sets it used."
      />
    )
  }

  return (
    <Card>
      <CardHeader
        title="Statutory rates used"
        description="Copied onto the run when it was processed, so these figures stay explicable even if a rate set is later superseded."
      />
      <div className="mt-3 space-y-2">
        {entries.map(([statute, ref]) => (
          <div key={statute} className="flex items-center justify-between gap-3 text-sm">
            <span>
              <strong>{statute.toUpperCase()}</strong>
              {ref.jurisdiction ? <Muted> · {ref.jurisdiction}</Muted> : null}
              {ref.regime ? <Muted> · {ref.regime} regime</Muted> : null}
              <Muted> · {ref.rule_version} · from {ref.effective_from}</Muted>
            </span>
            <Badge tone={ref.verification_status === 'verified' ? 'success' : 'warning'}>
              {humanize(ref.verification_status)}
            </Badge>
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------- actions */

function RunActions({ run }: { run: PayrollRunDetail }) {
  const permissions = usePermissions()
  const actions = usePayrollRunActions(run.id)
  const toast = useToast()

  const [confirm, setConfirm] = useState<null | 'process' | 'approve' | 'paid'>(null)
  const [reasonFor, setReasonFor] = useState<null | 'reject' | 'reverse'>(null)

  const canEdit = permissions.can(RESOURCE.PAYROLL_RUN, ACTION.EDIT)
  const canApprove = permissions.can(RESOURCE.PAYROLL_RUN, ACTION.APPROVE)
  const canExport = permissions.can(RESOURCE.PAYROLL_RUN, ACTION.EXPORT)

  function run_(
    mutation: { mutate: (body: never, opts: object) => void; isPending: boolean },
    body: unknown,
    message: string,
  ) {
    mutation.mutate(body as never, {
      onSuccess: () => {
        toast.push({ tone: 'success', title: message })
        setConfirm(null)
        setReasonFor(null)
      },
      onError: (error: unknown) => {
        toast.push({
          tone: 'error',
          title: 'The action was refused',
          description: error instanceof ApiError ? error.displayMessage : undefined,
        })
      },
    })
  }

  async function download(url: string, name: string) {
    try {
      await downloadFile(url, name)
    } catch {
      toast.push({ tone: 'error', title: 'The download was refused' })
    }
  }

  const processable = canEdit && !run.locked && run.status !== 'paid'

  return (
    <>
      <div className="row row--wrap">
        {processable ? (
          <Button onClick={() => setConfirm('process')} disabled={actions.process.isPending}>
            {run.payslip_count > 0 ? 'Re-process' : 'Process'}
          </Button>
        ) : null}

        {canApprove && run.status === 'review' ? (
          <Button
            variant="primary"
            onClick={() => setConfirm('approve')}
            /* The server's answer, not this page's opinion. */
            disabled={!run.can_approve || actions.approve.isPending}
            title={
              run.can_approve
                ? undefined
                : 'Resolve the blockers listed above before approving.'
            }
          >
            Approve run
          </Button>
        ) : null}

        {canApprove && run.status === 'review' ? (
          <Button variant="ghost" onClick={() => setReasonFor('reject')}>
            Reject
          </Button>
        ) : null}

        {canApprove && run.status === 'approved' ? (
          <Button onClick={() => setConfirm('paid')}>Mark paid</Button>
        ) : null}

        {canApprove && (run.status === 'approved' || run.status === 'paid') ? (
          <Button variant="danger" onClick={() => setReasonFor('reverse')}>
            Reverse
          </Button>
        ) : null}

        {canExport && run.payslip_count > 0 ? (
          <Button
            variant="ghost"
            onClick={() =>
              void download(
                payrollDownloads.register(run.id),
                `salary-register-${run.period_year}-${run.period_month}.xlsx`,
              )
            }
          >
            Salary register
          </Button>
        ) : null}

        {canExport && (run.status === 'approved' || run.status === 'paid') ? (
          <Button
            variant="ghost"
            onClick={() =>
              void download(
                payrollDownloads.neft(run.id),
                `neft-${run.period_year}-${run.period_month}.tsv`,
              )
            }
          >
            Bank advice
          </Button>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirm === 'process'}
        onClose={() => setConfirm(null)}
        title={run.payslip_count > 0 ? 'Re-process this run?' : 'Process this run?'}
        description={
          run.payslip_count > 0
            ? 'The existing payslips are discarded and recomputed from current salary structures, adjustments and statutory rates.'
            : 'A payslip is computed for every payable employee with a salary structure in force.'
        }
        confirmLabel="Process"
        onConfirm={() => run_(actions.process, undefined, 'Run processed.')}
        loading={actions.process.isPending}
      />

      <ConfirmDialog
        open={confirm === 'approve'}
        onClose={() => setConfirm(null)}
        title="Approve this payroll run?"
        description="Approval locks the run permanently. Its amounts and payslips can never be edited afterwards — a correction requires a recorded reversal."
        confirmLabel="Approve and lock"
        onConfirm={() => run_(actions.approve, {}, 'Run approved and locked.')}
        loading={actions.approve.isPending}
      />

      <ConfirmDialog
        open={confirm === 'paid'}
        onClose={() => setConfirm(null)}
        title="Mark this run as paid?"
        description="Records that the bank transfer has gone out."
        confirmLabel="Mark paid"
        onConfirm={() => run_(actions.markPaid, {}, 'Run marked paid.')}
        loading={actions.markPaid.isPending}
      />

      <ReasonDialog
        open={reasonFor === 'reject'}
        onClose={() => setReasonFor(null)}
        title="Reject this run"
        description="The payslips are discarded and the run returns to draft. Say what was wrong so the next person can fix it."
        confirmLabel="Reject run"
        onSubmit={(reason: string) => run_(actions.reject, { reason }, 'Run rejected.')}
        loading={actions.reject.isPending}
      />

      <ReasonDialog
        open={reasonFor === 'reverse'}
        onClose={() => setReasonFor(null)}
        title="Reverse this run"
        description="This undoes a payroll that was already released. The reversal is recorded permanently against the run."
        confirmLabel="Reverse run"
        onSubmit={(reason: string) => run_(actions.reverse, { reason }, 'Run reversed.')}
        loading={actions.reverse.isPending}
      />
    </>
  )
}

/* ---------------------------------------------------------------- page */

export function PayrollRunDetailPage() {
  const { id } = useParams<{ id: string }>()
  const query = usePayrollRun(id)

  if (query.isLoading) return <LoadingBlock />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (!query.data) return <EmptyState title="Run not found" />

  const run = query.data
  const totals = run.totals ?? {}

  const columns: Column<PayslipSummary>[] = [
    {
      key: 'employee',
      header: 'Employee',
      render: (p) => (
        <Link to={`/payslips/${p.id}`} className="link">
          <strong>{p.employee_name}</strong>
          <Muted> · {p.employee_code}</Muted>
        </Link>
      ),
    },
    { key: 'days', header: 'Paid days', secondary: true, render: (p) => p.paid_days },
    { key: 'gross', header: 'Gross', align: 'right', render: (p) => formatCurrency(p.gross_earnings) },
    {
      key: 'ded',
      header: 'Deductions',
      align: 'right',
      render: (p) => formatCurrency(p.total_deductions),
    },
    {
      key: 'net',
      header: 'Net pay',
      align: 'right',
      render: (p) => <strong>{formatCurrency(p.net_pay)}</strong>,
    },
  ]

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
            {periodLabel(run)}
          </span>
        }
        description={
          run.run_type === 'regular'
            ? `Financial year ${run.financial_year}`
            : `${humanize(run.run_type)} run #${run.sequence} · FY ${run.financial_year}`
        }
        actions={<RunActions run={run} />}
      />

      <div className="row row--tight">
        <Badge tone={STATUS_TONES[run.status] ?? 'neutral'}>{humanize(run.status)}</Badge>
        {run.locked ? <Badge tone="neutral">Locked</Badge> : null}
      </div>

      <BlockerPanel blockers={run.blockers} />

      {run.status === 'reversed' ? (
        <Banner tone="danger" title="This run was reversed">
          {run.reversal_reason}
        </Banner>
      ) : null}

      <div className="grid-stats">
        <StatCard label="Employees" value={totals.employee_count ?? run.payslip_count ?? 0} />
        <StatCard label="Gross earnings" value={formatCurrency(totals.gross_earnings)} />
        <StatCard label="Deductions" value={formatCurrency(totals.total_deductions)} />
        <StatCard label="Net pay" value={formatCurrency(totals.net_pay)} tone="brand" />
        <StatCard
          label="Employer cost"
          value={formatCurrency(totals.employer_contributions)}
          hint="On top of gross — not deducted from anyone's pay."
        />
      </div>

      {totals.skipped ? (
        <Banner tone="info" title={`${totals.skipped} employee(s) were skipped`}>
          They have no salary structure in force for this period, so there was nothing
          to compute. Give them a structure and re-process.
        </Banner>
      ) : null}

      <RuleSetsUsed run={run} />

      <Section title="Payslips">
        {run.payslips.length === 0 ? (
          <EmptyState
            title="No payslips yet"
            description="Process the run to compute a payslip for every payable employee."
          />
        ) : (
          <DataTable rows={run.payslips} columns={columns} rowKey={(p) => p.id} />
        )}
      </Section>

      <Section title="Run record">
        <Card>
          <DescriptionList
            items={[
              { label: 'Approved', value: formatDateTime(run.approved_at) },
              { label: 'Paid', value: formatDateTime(run.paid_at) },
              { label: 'Reversed', value: formatDateTime(run.reversed_at) },
              { label: 'Notes', value: run.notes || '—' },
            ]}
          />
        </Card>
      </Section>
    </>
  )
}
