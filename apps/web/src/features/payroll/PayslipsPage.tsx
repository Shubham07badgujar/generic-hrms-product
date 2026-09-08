/**
 * Payslips — self-service and, for finance, everyone's.
 *
 * Which payslips a person sees is decided entirely by the server's scope on
 * `payslip/view`: an employee's own, a manager's team, finance's everyone. This
 * page sends no employee filter of its own, so there is nothing here for a
 * client to tamper with.
 */

import { useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Link } from 'react-router-dom'
import { ReasonDialog } from '@/components/ui/ConfirmDialog'
import { Card, PageHeader, Section, StatCard } from '@/components/ui/Card'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { EmptyState, ErrorState, LoadingBlock, TableSkeleton } from '@/components/ui/States'
import { Badge } from '@/components/ui/Badge'
import { Banner, Muted } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import { payrollDownloads, useMyPayroll, usePayslip, usePayslips } from '@/lib/payrollQueries'
import { ApiError, downloadFile } from '@/lib/api'
import { formatCurrency, formatDate, humanize } from '@/lib/format'
import type { PayslipDetail, PayslipLine, PayslipSummary } from '@/lib/types'
import { periodLabel } from './PayrollPage'

const STATUTE_LABELS: Record<string, string> = {
  pf: 'Provident Fund',
  esi: 'ESI',
  pt: 'Professional Tax',
  tds: 'Income tax (TDS)',
  gratuity: 'Gratuity provision',
}

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

/** The brand mark that heads every payroll page. */
function EnsoTitle({ children }: { children: ReactNode }) {
  return (
    <span className="flex items-center gap-2.5">
      <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-brand">
        <span
          aria-hidden
          className="block h-4 w-4 rounded-full border-[3px]"
          style={{ borderColor: LIME }}
        />
      </span>
      {children}
    </span>
  )
}

function useDownloadPayslip() {
  const toast = useToast()
  return async (payslip: { id: string; employee_code: string; period_year: number; period_month: number }) => {
    try {
      await downloadFile(
        payrollDownloads.payslipPdf(payslip.id),
        `payslip-${payslip.employee_code}-${payslip.period_year}-${payslip.period_month}.pdf`,
      )
    } catch (error) {
      toast.push({
        tone: 'error',
        title: 'The download was refused',
        description: error instanceof ApiError ? error.displayMessage : undefined,
      })
    }
  }
}

/* ------------------------------------------------------------ the list */

export function PayslipsPage() {
  const permissions = usePermissions()
  const scope = permissions.scopeOf(RESOURCE.PAYSLIP, ACTION.VIEW)
  const isSelfOnly = scope === 'self'

  const mine = useMyPayroll()
  const all = usePayslips({})
  const download = useDownloadPayslip()

  const columns: Column<PayslipSummary>[] = [
    {
      key: 'period',
      header: 'Period',
      render: (p) => (
        <Link to={`/payslips/${p.id}`} className="link inline-flex items-center gap-2">
          <span
            aria-hidden
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: LIME }}
          />
          <strong>{periodLabel(p)}</strong>
        </Link>
      ),
    },
    ...(isSelfOnly
      ? []
      : [
          {
            key: 'employee',
            header: 'Employee',
            render: (p: PayslipSummary) => (
              <span>
                {p.employee_name}
                <Muted> · {p.employee_code}</Muted>
              </span>
            ),
          } as Column<PayslipSummary>,
        ]),
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
    {
      key: 'status',
      header: 'Run',
      render: (p) => <Badge tone={p.run_status === 'paid' ? 'success' : 'neutral'}>{humanize(p.run_status)}</Badge>,
    },
    {
      key: 'pdf',
      header: 'PDF',
      headerSrOnly: true,
      render: (p) => (
        <Button variant="ghost" size="sm" onClick={() => void download(p)}>
          Download
        </Button>
      ),
    },
  ]

  const query = isSelfOnly ? mine : all
  const rows: PayslipSummary[] = isSelfOnly ? (mine.data?.payslips ?? []) : (all.data?.data ?? [])

  return (
    <>
      <PageHeader
        title={<EnsoTitle>{isSelfOnly ? 'My payslips' : 'Payslips'}</EnsoTitle>}
        description={
          isSelfOnly
            ? 'Your payslips. Only you, payroll and finance can see these.'
            : 'Payslips within your visibility.'
        }
      />

      {isSelfOnly && mine.data?.structure ? (
        <div className="grid-stats">
          <StatCard
            label="Monthly gross"
            value={formatCurrency(mine.data.structure.monthly_gross)}
          />
          <StatCard
            label="Annual CTC"
            value={formatCurrency(mine.data.structure.ctc_annual)}
          />
          <StatCard
            label="Tax regime"
            value={
              mine.data.declaration?.regime
                ? humanize(mine.data.declaration.regime)
                : 'Not declared'
            }
            hint={
              mine.data.declaration?.regime
                ? undefined
                : 'The statutory default applies until you elect one.'
            }
          />
        </div>
      ) : null}

      {isSelfOnly && mine.data?.package ? (
        <Section title="My package">
          <Card className="space-y-3 p-5">
            <div className="grid-stats">
              <StatCard label="Total package"
                value={formatCurrency(mine.data.package.total_amount)} />
              <StatCard label="Paid so far"
                value={formatCurrency(mine.data.package.summary.paid_so_far)} />
              <StatCard label="Deferred"
                value={formatCurrency(mine.data.package.summary.deferred_total)} />
              <StatCard label="Expected release"
                value={
                  mine.data.package.summary.next_release_date
                    ? formatDate(mine.data.package.summary.next_release_date)
                    : mine.data.package.summary.remaining_deferred === '0.00'
                      ? 'Released'
                      : 'On completion'
                } />
            </div>
            <ul className="divide-y divide-line text-sm">
              {mine.data.package.periods.map((period) => (
                <li key={period.id} className="flex items-center justify-between gap-3 py-2">
                  <span>{period.label} · {formatCurrency(period.monthly_amount)}/month</span>
                  <span className="tabular">{formatCurrency(period.amount)}</span>
                </li>
              ))}
              {mine.data.package.deferrals.map((deferral) => (
                <li key={deferral.id} className="flex items-center justify-between gap-3 py-2">
                  <span>
                    {deferral.label} (deferred)
                    {deferral.eligible_on ? ` · expected ${formatDate(deferral.eligible_on)}` : ''}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="tabular">{formatCurrency(deferral.amount)}</span>
                    <Badge tone={deferral.effective_status === 'paid' ? 'success' : 'neutral'}>
                      {humanize(deferral.effective_status)}
                    </Badge>
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </Section>
      ) : null}

      <Section title="Payslips">
        {query.isLoading ? (
          <TableSkeleton columns={columns.length} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No payslips yet"
            description="Payslips appear once payroll has been processed for a period."
          />
        ) : (
          <DataTable rows={rows} columns={columns} rowKey={(p) => p.id} />
        )}
      </Section>
    </>
  )
}

/* ---------------------------------------------------------- the detail */

export function PayslipDetailPage() {
  const { id } = useParams<{ id: string }>()
  const query = usePayslip(id)
  const download = useDownloadPayslip()
  const permissions = usePermissions()
  const toast = useToast()
  const navigate = useNavigate()
  const [deleting, setDeleting] = useState(false)
  const [deleteBusy, setDeleteBusy] = useState(false)

  if (query.isLoading) return <LoadingBlock />
  if (query.isError) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />
  if (!query.data) return <EmptyState title="Payslip not found" />

  const payslip: PayslipDetail = query.data
  // The Finance Head's correction window — the flag comes from the server,
  // so this button and the API's refusal can never disagree.
  const mayDelete =
    permissions.can(RESOURCE.PAYSLIP, ACTION.DELETE) && payslip.within_delete_window

  async function deletePayslip(reason: string) {
    setDeleteBusy(true)
    try {
      const { http } = await import('@/lib/api')
      await http.delete(`/payslips/${payslip.id}/`, { data: { reason } })
      toast.success('Payslip deleted', 'The employee can be paid again by a new run.')
      navigate('/payslips')
    } catch (error) {
      toast.fromError(error, 'Could not delete the payslip')
    } finally {
      setDeleteBusy(false)
      setDeleting(false)
    }
  }
  const earnings = payslip.lines.filter(
    (l) => !l.is_employer_side && ['earning', 'reimbursement'].includes(l.component_type),
  )
  const deductions = payslip.lines.filter(
    (l) => !l.is_employer_side && ['deduction', 'statutory_deduction'].includes(l.component_type),
  )
  const employer = payslip.lines.filter((l) => l.is_employer_side)

  const lineColumns: Column<PayslipLine>[] = [
    { key: 'label', header: 'Item', render: (l) => l.label },
    { key: 'amount', header: 'Amount', align: 'right', render: (l) => formatCurrency(l.amount) },
  ]

  return (
    <>
      <PageHeader
        title={<EnsoTitle>Payslip — {periodLabel(payslip)}</EnsoTitle>}
        description={`${payslip.employee_name} · ${payslip.employee_code}`}
        actions={
          <span className="flex gap-2">
            <Button onClick={() => void download(payslip)}>Download PDF</Button>
            {mayDelete && (
              <Button variant="danger" onClick={() => setDeleting(true)}>
                Delete payslip
              </Button>
            )}
          </span>
        }
      />

      {permissions.can(RESOURCE.PAYSLIP, ACTION.DELETE) && !payslip.within_delete_window && (
        <Banner tone="neutral" title="Past the correction window">
          This payslip is locked: the {` `}delete window closed on{' '}
          {formatDate(payslip.deletable_until)}. If it is genuinely wrong, reverse the payroll
          run instead — that path is fully recorded.
        </Banner>
      )}

      <ReasonDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        loading={deleteBusy}
        title="Delete this payslip"
        description={`${payslip.employee_name} · ${periodLabel(payslip)} · net ${formatCurrency(payslip.net_pay)}`}
        label="Reason"
        confirmLabel="Delete payslip"
        onSubmit={(reason) => void deletePayslip(reason)}
        banner={
          <Banner tone="danger" title="This withdraws a generated payslip">
            The deletion is audited with your name and this reason. The employee will no longer
            hold a payslip for this period and can be paid by a new run.
          </Banner>
        }
      />

      {payslip.warnings.length > 0 ? (
        <Banner tone="warning" title="Notes recorded when this payslip was computed">
          <ul className="mt-2 space-y-1">
            {payslip.warnings.map((warning) => (
              <li key={warning} className="text-sm">{warning}</li>
            ))}
          </ul>
        </Banner>
      ) : null}

      <div className="grid-stats">
        <StatCard label="Gross earnings" value={formatCurrency(payslip.gross_earnings)} />
        <StatCard label="Deductions" value={formatCurrency(payslip.total_deductions)} />
        <StatCard label="Net pay" value={formatCurrency(payslip.net_pay)} tone="brand" />
        <StatCard
          label="Paid days"
          value={payslip.paid_days}
          hint={`Loss of pay: ${payslip.lop_days}`}
        />
      </div>

      <div className="grid-two">
        <Section title="Earnings">
          <DataTable rows={earnings} columns={lineColumns} rowKey={(l) => l.id} />
        </Section>
        <Section title="Deductions">
          {deductions.length === 0 ? (
            <EmptyState title="No deductions" />
          ) : (
            <DataTable rows={deductions} columns={lineColumns} rowKey={(l) => l.id} />
          )}
        </Section>
      </div>

      {/* Shown only when the employee is actually enrolled in something the
          employer contributes to — the structure's applicability decides. */}
      {employer.length > 0 && (
        <Section
          title="Employer contributions"
          description="A cost to the company, over and above gross. Never deducted from your pay."
        >
          <DataTable rows={employer} columns={lineColumns} rowKey={(l) => l.id} />
        </Section>
      )}

      <Section title="Statutory position">
        <Card>
          <div className="space-y-2">
            {payslip.statutory_contributions.map((c) => (
              <div key={c.id} className="flex items-center justify-between gap-3 text-sm">
                <span>
                  <strong>{STATUTE_LABELS[c.kind] ?? c.kind.toUpperCase()}</strong>
                  {c.state ? <Muted> · {c.state}</Muted> : null}
                  <Muted> · assessed on {formatCurrency(c.base_wage)}</Muted>
                </span>
                <span className="row row--tight">
                  <span className="tabular">
                    you {formatCurrency(c.employee_amount)} · employer{' '}
                    {formatCurrency(c.employer_amount)}
                  </span>
                  {c.applied ? null : (
                    <Badge tone="neutral">{humanize(c.exemption_reason) || 'Not applicable'}</Badge>
                  )}
                </span>
              </div>
            ))}
          </div>
        </Card>
      </Section>
    </>
  )
}
