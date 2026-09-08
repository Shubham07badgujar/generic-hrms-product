/**
 * The Salary tab on an employee profile.
 *
 * One structure is in force at a time; a revision closes it and opens a new
 * one, so every payslip ever issued can still point at the structure that
 * produced it. Amounts here are the employee's own figures — which component
 * catalogue entry they use, and the ₹ or % value for each.
 */

import { useState } from 'react'
import { Card, CardHeader, DescriptionList } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Banner } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { EmptyState, TableSkeleton } from '@/components/ui/States'
import { Modal } from '@/components/ui/Modal'
import { Checkbox, Select, TextArea, TextInput } from '@/components/ui/Field'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useCreateSalaryStructure,
  usePackages,
  useSalaryComponents,
  useSalaryStructures,
} from '@/lib/payrollQueries'
import { Link } from 'react-router-dom'
import { ApiError } from '@/lib/api'
import { formatCurrency, formatDate } from '@/lib/format'
import type { SalaryStructure, UUID } from '@/lib/types'

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

interface LineDraft {
  component: string
  value: string
}

/** Statute label ↔ structure field, in payslip order. */
const STATUTES = [
  ['pf_applicable', 'PF & EPS'],
  ['esi_applicable', 'ESIC'],
  ['pt_applicable', 'Professional Tax'],
  ['tds_applicable', 'TDS'],
  ['gratuity_applicable', 'Gratuity'],
] as const

type StatuteFlag = (typeof STATUTES)[number][0]

const ALL_APPLICABLE: Record<StatuteFlag, boolean> = {
  pf_applicable: true,
  esi_applicable: true,
  pt_applicable: true,
  tds_applicable: true,
  gratuity_applicable: true,
}

function StructureCard({ structure, current }: { structure: SalaryStructure; current: boolean }) {
  return (
    <Card className="space-y-4">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            {formatDate(structure.valid_from)} →{' '}
            {structure.valid_to ? formatDate(structure.valid_to) : 'current'}
            {current && <Badge tone="success">In force</Badge>}
          </span>
        }
        description={structure.revision_reason || undefined}
      />
      <DescriptionList
        columns={3}
        items={[
          { label: 'Annual CTC', value: formatCurrency(structure.ctc_annual) },
          { label: 'Monthly gross', value: formatCurrency(structure.monthly_gross) },
          {
            label: 'PF wage share of gross',
            value: `${Math.round(structure.wage_share * 100)}%`,
          },
        ]}
      />
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-muted">
          Statutory
        </span>
        {STATUTES.map(([flag, label]) => (
          <Badge key={flag} tone={structure[flag] ? 'info' : 'neutral'}>
            {label}
            {structure[flag] ? '' : ' — not enrolled'}
          </Badge>
        ))}
      </div>
      <ul className="divide-y divide-line">
        {structure.lines.map((line) => (
          <li key={line.component} className="flex items-center gap-3 py-2">
            <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: LIME }} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink">{line.component_name}</p>
              <p className="text-xs text-ink-muted">
                {line.component_code}
                {line.is_wage ? ' · PF wage' : ''}
              </p>
            </div>
            <span className="text-sm tabular text-ink">{formatCurrency(line.monthly_amount)}/mo</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function CompensationSection({ employeeId }: { employeeId: UUID }) {
  const permissions = usePermissions()
  const toast = useToast()
  const structures = useSalaryStructures({ employee: employeeId, page_size: '50' })
  const components = useSalaryComponents(permissions.can(RESOURCE.SALARY, ACTION.CREATE))
  const create = useCreateSalaryStructure()

  const [revising, setRevising] = useState(false)
  const [ctc, setCtc] = useState('')
  const [validFrom, setValidFrom] = useState('')
  const [reason, setReason] = useState('')
  const [lines, setLines] = useState<LineDraft[]>([{ component: '', value: '' }])
  const [applicability, setApplicability] = useState<Record<StatuteFlag, boolean>>(ALL_APPLICABLE)
  const [error, setError] = useState('')

  const rows = structures.data?.data ?? []
  const current = rows.find((row) => row.valid_to === null) ?? null
  const past = rows.filter((row) => row.valid_to !== null)
  const mayRevise = permissions.can(RESOURCE.SALARY, ACTION.CREATE)

  // §17: a salary change must never silently modify a package schedule —
  // surface the live package so HR decides deliberately.
  const packages = usePackages(
    permissions.canAtLeast(RESOURCE.PACKAGE, ACTION.VIEW, 'all')
      ? { employee: employeeId }
      : {},
  )
  const livePackage = (packages.data?.data ?? []).find(
    (p) => p.employee === employeeId && (p.status === 'active' || p.status === 'on_hold'),
  )

  function openRevise() {
    setError('')
    setCtc(current?.ctc_annual ?? '')
    setValidFrom('')
    setReason('')
    setLines(
      current
        ? current.lines.map((line) => ({ component: line.component, value: line.value }))
        : [{ component: '', value: '' }],
    )
    setApplicability(
      current
        ? {
            pf_applicable: current.pf_applicable,
            esi_applicable: current.esi_applicable,
            pt_applicable: current.pt_applicable,
            tds_applicable: current.tds_applicable,
            gratuity_applicable: current.gratuity_applicable,
          }
        : ALL_APPLICABLE,
    )
    setRevising(true)
  }

  function submit() {
    setError('')
    const cleanLines = lines.filter((line) => line.component && line.value !== '')
    create.mutate(
      {
        employee: employeeId,
        ctc_annual: ctc,
        valid_from: validFrom,
        revision_reason: reason,
        lines: cleanLines.map((line) => ({ component: line.component as UUID, value: line.value })),
        ...applicability,
      },
      {
        onSuccess: () => {
          toast.success(current ? 'Salary revised' : 'Salary structure created')
          setRevising(false)
        },
        onError: (err) => setError(err instanceof ApiError ? err.displayMessage : 'Could not save.'),
      },
    )
  }

  const catalogue = components.data ?? []
  const componentFor = (id: string) => catalogue.find((c) => c.id === id)

  if (structures.isLoading) return <TableSkeleton columns={3} />

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-ink-muted">
          {current
            ? 'Every revision keeps the previous structure as history — payslips always trace back to the structure that produced them.'
            : 'No salary structure yet. Payroll skips this employee until one exists.'}
        </p>
        {mayRevise && (
          <Button variant="primary" onClick={openRevise}>
            {current ? 'Revise salary' : 'Set up salary'}
          </Button>
        )}
      </div>

      {current ? (
        <StructureCard structure={current} current />
      ) : (
        <Card>
          <EmptyState
            compact
            title="No structure in force"
            description={
              mayRevise
                ? 'Set the CTC and its component breakup to bring this employee into payroll.'
                : 'HR maintains salary structures.'
            }
          />
        </Card>
      )}

      {past.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
            Salary history
          </h3>
          {past.map((row) => (
            <StructureCard key={row.id} structure={row} current={false} />
          ))}
        </div>
      )}

      <Modal
        open={revising}
        onClose={() => setRevising(false)}
        busy={create.isPending}
        size="lg"
        title={current ? 'Revise salary' : 'Set up salary'}
        description="The new structure takes effect from the date below; the current one closes the day before and stays as history."
        footer={
          <>
            <Button onClick={() => setRevising(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={create.isPending}
              disabled={!ctc || !validFrom || !lines.some((l) => l.component && l.value !== '')}
              onClick={submit}
            >
              {current ? 'Apply revision' : 'Create structure'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && <Banner tone="danger">{error}</Banner>}
          {livePackage && (
            <Banner tone="warning" title="This employee has an active custom package schedule">
              Changing the salary does not modify the package. If the agreement itself changed,
              also{' '}
              <Link to="/payroll/packages" className="link">
                create a revised package schedule
              </Link>{' '}
              — the previous schedule is kept as history. Otherwise keep the existing schedule.
            </Banner>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <TextInput label="Annual CTC (₹)" type="number" min={0} required value={ctc}
              onChange={(e) => setCtc(e.target.value)} />
            <TextInput label="Effective from" type="date" required value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
              description={
                current
                  ? `After ${formatDate(current.valid_from)} revises forward; a date before the ` +
                    'first structure back-fills earlier months (it closes automatically where ' +
                    'the existing history begins). Months already covered are never rewritten.'
                  : 'Payroll pays an employee only for months a structure covers — a past date here makes earlier months payable too.'
              } />
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium text-ink">Monthly components</p>
            {lines.map((line, index) => {
              const chosen = componentFor(line.component)
              return (
                <div key={index} className="flex flex-wrap items-end gap-2">
                  <div className="min-w-[220px] flex-1">
                    <Select
                      label={index === 0 ? 'Component' : ''}
                      placeholder="Choose a component"
                      value={line.component}
                      onChange={(e) =>
                        setLines(lines.map((l, i) => (i === index ? { ...l, component: e.target.value } : l)))
                      }
                      options={catalogue.map((c) => ({
                        value: c.id,
                        label: `${c.name} (${c.calc_type === 'fixed' ? '₹/month' : `% of ${c.percent_of_code}`})`,
                      }))}
                    />
                  </div>
                  <div className="w-40">
                    <TextInput
                      label={index === 0 ? 'Value' : ''}
                      type="number"
                      step="0.01"
                      min={0}
                      value={line.value}
                      placeholder={chosen?.calc_type === 'percent_of' ? '%' : '₹'}
                      onChange={(e) =>
                        setLines(lines.map((l, i) => (i === index ? { ...l, value: e.target.value } : l)))
                      }
                    />
                  </div>
                  <Button size="sm" variant="ghost" disabled={lines.length === 1}
                    onClick={() => setLines(lines.filter((_, i) => i !== index))}
                    aria-label={`Remove component ${index + 1}`}>
                    Remove
                  </Button>
                </div>
              )
            })}
            <Button size="sm" onClick={() => setLines([...lines, { component: '', value: '' }])}>
              Add component
            </Button>
            <p className="text-xs text-ink-muted">
              Fixed components take a ₹ amount per month; percentage components take the percentage
              (e.g. HRA 50 = 50% of Basic). Percentages resolve against the fixed components in the
              same structure.
            </p>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium text-ink">Statutory applicability</p>
            <p className="text-xs text-ink-muted">
              Untick what genuinely does not apply to this employee — an unticked statute puts
              nothing on their payslip, employer contributions included. Enrolment is HR Head /
              Finance Head&rsquo;s call and is recorded in the audit log with this structure.
            </p>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {STATUTES.map(([flag, label]) => (
                <Checkbox
                  key={flag}
                  label={label}
                  checked={applicability[flag]}
                  onChange={(e) =>
                    setApplicability({ ...applicability, [flag]: e.target.checked })
                  }
                />
              ))}
            </div>
          </div>

          <TextArea label="Reason for this change" rows={2} value={reason}
            onChange={(e) => setReason(e.target.value)}
            description="Stored on the structure and in the audit log." />
        </div>
      </Modal>
    </div>
  )
}
