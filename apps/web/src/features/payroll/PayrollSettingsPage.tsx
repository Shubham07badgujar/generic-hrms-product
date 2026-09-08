/**
 * Payroll Settings — the rates and the component catalogue.
 *
 * Nothing on this page is a number the system invented: every percentage,
 * ceiling and slab is entered here by Finance/HR, effective-dated, and goes
 * through the verification workflow before payroll will trust it. Editing a
 * VERIFIED rate set auto-reverts it to draft on the server, loudly — history
 * is safe because a processed run copies the rates it used onto itself.
 *
 * The forms are typed per statute (PF, ESIC, Professional Tax) because those
 * have a small stable shape; income tax keeps a JSON editor since its slabs,
 * rebates and cess genuinely vary by Finance Act.
 */

import { useState } from 'react'
import { Card, CardHeader, PageHeader } from '@/components/ui/Card'
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/States'
import { Badge, type Tone } from '@/components/ui/Badge'
import { Banner, TabPanel, Tabs } from '@/components/ui/Misc'
import { Button } from '@/components/ui/Button'
import { Modal } from '@/components/ui/Modal'
import { Checkbox, Select, TextArea, TextInput } from '@/components/ui/Field'
import { DataTable, type Column } from '@/components/ui/DataTable'
import { useToast } from '@/components/ui/Toast'
import { usePermissions } from '@/app/AuthProvider'
import { ACTION, RESOURCE } from '@/lib/permissions'
import {
  useRuleSetActions,
  useSaveComponent,
  useSaveRuleSet,
  useSalaryComponents,
  useStatutoryRuleSets,
} from '@/lib/payrollQueries'
import { ApiError } from '@/lib/api'
import { formatCurrency, formatDate, humanize } from '@/lib/format'
import type { SalaryComponent, StatutoryRuleSet } from '@/lib/types'

//: The logo's lime — the same accent every branded surface carries.
const LIME = '#A6CE39'

const STATUS_TONES: Record<StatutoryRuleSet['verification_status'], Tone> = {
  draft: 'neutral',
  pending: 'warning',
  verified: 'success',
  rejected: 'danger',
  superseded: 'neutral',
}

/** What each statute's form asks for. Income tax stays JSON — see module doc. */
const STATUTES = [
  { key: 'pf', label: 'Provident Fund (PF & EPS)', rule: 'pf.v1' },
  { key: 'esi', label: 'ESIC', rule: 'esi.v1' },
  { key: 'pt', label: 'Professional Tax', rule: 'pt.v2' },
  { key: 'income_tax', label: 'Income Tax (TDS)', rule: 'income_tax.v2' },
] as const

type StatuteKey = (typeof STATUTES)[number]['key']

function num(parameters: Record<string, unknown>, key: string): string {
  const value = parameters?.[key]
  return value === undefined || value === null ? '' : String(value)
}

/* ===================================================== rule set editor */

interface Slab {
  min_gross: string
  max_gross: string
  monthly_amount: string
}

function RuleSetDialog({
  statute,
  existing,
  onClose,
}: {
  statute: StatuteKey
  existing: StatutoryRuleSet | null
  onClose: () => void
}) {
  const toast = useToast()
  const save = useSaveRuleSet()
  const spec = STATUTES.find((s) => s.key === statute)!
  const p = (existing?.parameters ?? {}) as Record<string, unknown>

  const [effectiveFrom, setEffectiveFrom] = useState(existing?.effective_from ?? '')
  const [effectiveTo, setEffectiveTo] = useState(existing?.effective_to ?? '')
  const [financialYear, setFinancialYear] = useState(existing?.financial_year ?? '')
  const [jurisdiction, setJurisdiction] = useState(existing?.jurisdiction || (statute === 'pt' ? 'MH' : ''))
  const [regime, setRegime] = useState(existing?.regime ?? 'new')
  const [citation, setCitation] = useState(existing?.source_citation ?? '')
  const [sourceUrl, setSourceUrl] = useState(existing?.source_url ?? '')
  const [retrievedOn, setRetrievedOn] = useState(
    existing?.retrieved_on ?? new Date().toISOString().slice(0, 10),
  )
  const [error, setError] = useState('')

  // PF
  const [pf, setPf] = useState({
    employee_rate: num(p, 'employee_rate'),
    employer_rate: num(p, 'employer_rate'),
    eps_rate: num(p, 'eps_rate'),
    wage_ceiling: num(p, 'wage_ceiling'),
    eps_wage_ceiling: num(p, 'eps_wage_ceiling'),
    eps_monthly_cap: num(p, 'eps_monthly_cap'),
  })
  // ESI
  const [esi, setEsi] = useState({
    employee_rate: num(p, 'employee_rate'),
    employer_rate: num(p, 'employer_rate'),
    gross_wage_threshold: num(p, 'gross_wage_threshold'),
    disability_wage_threshold: num(p, 'disability_wage_threshold'),
    threshold_is_inclusive: p['threshold_is_inclusive'] !== false,
  })
  // PT
  const [slabs, setSlabs] = useState<Slab[]>(
    Array.isArray(p['slabs']) && (p['slabs'] as unknown[]).length
      ? (p['slabs'] as Array<Record<string, unknown>>).map((row) => ({
          min_gross: String(row.min_gross ?? '0'),
          max_gross: row.max_gross === null || row.max_gross === undefined ? '' : String(row.max_gross),
          monthly_amount: String(row.monthly_amount ?? '0'),
        }))
      : [{ min_gross: '0', max_gross: '', monthly_amount: '0' }],
  )
  const [specialMonth, setSpecialMonth] = useState(num(p, 'special_month'))
  const [specialAmount, setSpecialAmount] = useState(num(p, 'special_amount'))
  // Income tax (and anything else): raw JSON
  const [rawParameters, setRawParameters] = useState(
    JSON.stringify(existing?.parameters ?? {}, null, 2),
  )

  function buildParameters(): Record<string, unknown> {
    if (statute === 'pf') {
      const out: Record<string, unknown> = {
        employee_rate: pf.employee_rate,
        employer_rate: pf.employer_rate,
        eps_rate: pf.eps_rate,
        wage_ceiling: pf.wage_ceiling,
        eps_wage_ceiling: pf.eps_wage_ceiling,
      }
      if (pf.eps_monthly_cap) out.eps_monthly_cap = pf.eps_monthly_cap
      return out
    }
    if (statute === 'esi') {
      return {
        employee_rate: esi.employee_rate,
        employer_rate: esi.employer_rate,
        gross_wage_threshold: esi.gross_wage_threshold,
        disability_wage_threshold: esi.disability_wage_threshold || esi.gross_wage_threshold,
        threshold_is_inclusive: esi.threshold_is_inclusive,
      }
    }
    if (statute === 'pt') {
      const out: Record<string, unknown> = {
        slabs: slabs.map((slab) => ({
          min_gross: slab.min_gross || '0',
          max_gross: slab.max_gross === '' ? null : slab.max_gross,
          monthly_amount: slab.monthly_amount || '0',
        })),
      }
      if (specialMonth) out.special_month = Number(specialMonth)
      if (specialAmount) out.special_amount = specialAmount
      return out
    }
    return JSON.parse(rawParameters)
  }

  function submit() {
    setError('')
    let parameters: Record<string, unknown>
    try {
      parameters = buildParameters()
    } catch {
      setError('The parameters are not valid JSON.')
      return
    }
    save.mutate(
      {
        id: existing?.id,
        statute,
        rule_version: existing?.rule_version ?? spec.rule,
        parameters: parameters as never,
        effective_from: effectiveFrom,
        effective_to: effectiveTo || null,
        financial_year: financialYear,
        jurisdiction: statute === 'pt' ? jurisdiction : '',
        regime: statute === 'income_tax' ? regime : '',
        source_citation: citation,
        source_url: sourceUrl,
        retrieved_on: retrievedOn || null,
      },
      {
        onSuccess: () => {
          toast.success(existing ? 'Rates updated — back to draft for re-verification' : 'Draft rates created')
          onClose()
        },
        onError: (err) => setError(err instanceof ApiError ? err.displayMessage : 'Could not save.'),
      },
    )
  }

  const percent = (label: string, value: string, onChange: (v: string) => void) => (
    <TextInput label={label} type="number" step="0.01" min={0} value={value}
      onChange={(e) => onChange(e.target.value)} required />
  )
  const rupees = (label: string, value: string, onChange: (v: string) => void, required = true) => (
    <TextInput label={label} type="number" step="0.01" min={0} value={value}
      onChange={(e) => onChange(e.target.value)} required={required} />
  )

  return (
    <Modal
      open
      onClose={onClose}
      busy={save.isPending}
      size="lg"
      title={`${existing ? 'Edit' : 'New'} ${spec.label} rates`}
      description="Effective-dated: old payroll keeps the rates it was computed with. Verification is required before payroll can be approved against these."
      footer={
        <>
          <Button onClick={onClose} disabled={save.isPending}>Cancel</Button>
          <Button variant="primary" loading={save.isPending} disabled={!effectiveFrom} onClick={submit}>
            {existing ? 'Save changes' : 'Create draft'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}
        {existing && existing.verification_status === 'verified' && (
          <Banner tone="warning" title="This rate set is verified">
            Saving changes reverts it to draft and withdraws the verification —
            it must be re-verified before payroll can use it again.
          </Banner>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <TextInput label="Effective from" type="date" required value={effectiveFrom}
            onChange={(e) => setEffectiveFrom(e.target.value)} />
          <TextInput label="Effective to" type="date" value={effectiveTo}
            onChange={(e) => setEffectiveTo(e.target.value)}
            description="Leave empty while these rates remain in force." />
        </div>

        {statute === 'pf' && (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              {percent('Employee PF %', pf.employee_rate, (v) => setPf({ ...pf, employee_rate: v }))}
              {percent('Employer PF %', pf.employer_rate, (v) => setPf({ ...pf, employer_rate: v }))}
              {percent('Employer EPS %', pf.eps_rate, (v) => setPf({ ...pf, eps_rate: v }))}
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              {rupees('PF wage limit (₹/month)', pf.wage_ceiling, (v) => setPf({ ...pf, wage_ceiling: v }))}
              {rupees('EPS wage limit (₹/month)', pf.eps_wage_ceiling, (v) => setPf({ ...pf, eps_wage_ceiling: v }))}
              {rupees('EPS monthly cap (optional)', pf.eps_monthly_cap, (v) => setPf({ ...pf, eps_monthly_cap: v }), false)}
            </div>
            <p className="text-xs text-ink-muted">
              Employer EPS is carved out of the employer PF contribution; the remainder goes to EPF.
              PF is assessed on Basic + DA (wage components), never on gross.
            </p>
          </>
        )}

        {statute === 'esi' && (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              {percent('Employee ESIC %', esi.employee_rate, (v) => setEsi({ ...esi, employee_rate: v }))}
              {percent('Employer ESIC %', esi.employer_rate, (v) => setEsi({ ...esi, employer_rate: v }))}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {rupees('ESIC wage limit (₹/month gross)', esi.gross_wage_threshold, (v) => setEsi({ ...esi, gross_wage_threshold: v }))}
              {rupees('Wage limit for persons with disability', esi.disability_wage_threshold, (v) => setEsi({ ...esi, disability_wage_threshold: v }), false)}
            </div>
            <Checkbox
              label="Wage limit is inclusive (gross equal to the limit is still covered)"
              checked={esi.threshold_is_inclusive}
              onChange={(e) => setEsi({ ...esi, threshold_is_inclusive: e.target.checked })}
            />
          </>
        )}

        {statute === 'pt' && (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <TextInput label="State code" required value={jurisdiction}
                onChange={(e) => setJurisdiction(e.target.value.toUpperCase())}
                description="MH for Maharashtra. One rule set per state." />
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium text-ink">Monthly slabs (gross salary → PT amount)</p>
              {slabs.map((slab, index) => (
                <div key={index} className="flex flex-wrap items-end gap-2">
                  <TextInput label={index === 0 ? 'Gross from (₹)' : ''} type="number" value={slab.min_gross}
                    onChange={(e) => setSlabs(slabs.map((s, i) => (i === index ? { ...s, min_gross: e.target.value } : s)))} />
                  <TextInput label={index === 0 ? 'Gross up to (₹, empty = no limit)' : ''} type="number" value={slab.max_gross}
                    onChange={(e) => setSlabs(slabs.map((s, i) => (i === index ? { ...s, max_gross: e.target.value } : s)))} />
                  <TextInput label={index === 0 ? 'PT per month (₹)' : ''} type="number" value={slab.monthly_amount}
                    onChange={(e) => setSlabs(slabs.map((s, i) => (i === index ? { ...s, monthly_amount: e.target.value } : s)))} />
                  <Button size="sm" variant="ghost" onClick={() => setSlabs(slabs.filter((_, i) => i !== index))}
                    disabled={slabs.length === 1} aria-label={`Remove slab ${index + 1}`}>
                    Remove
                  </Button>
                </div>
              ))}
              <Button size="sm" onClick={() => setSlabs([...slabs, { min_gross: '', max_gross: '', monthly_amount: '' }])}>
                Add slab
              </Button>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <TextInput label="Special month (1–12, optional)" type="number" min={1} max={12} value={specialMonth}
                onChange={(e) => setSpecialMonth(e.target.value)}
                description="Maharashtra charges the top slab a higher amount in February." />
              <TextInput label="Special amount (₹, optional)" type="number" value={specialAmount}
                onChange={(e) => setSpecialAmount(e.target.value)} />
            </div>
          </>
        )}

        {statute === 'income_tax' && (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Select label="Regime" required value={regime} onChange={(e) => setRegime(e.target.value)}
                options={[{ value: 'new', label: 'New regime' }, { value: 'old', label: 'Old regime' }]} />
              <TextInput label="Financial year" required placeholder="2026-2027" value={financialYear}
                onChange={(e) => setFinancialYear(e.target.value)} />
            </div>
            <TextArea label="Parameters (JSON)" rows={10} value={rawParameters}
              onChange={(e) => setRawParameters(e.target.value)}
              description='Slabs, standard deduction, rebate and cess — e.g. {"standard_deduction": "75000", "slabs": [{"min_income": "0", "max_income": "400000", "rate": "0"}], "cess_rate": "4", ...}. One rule set per regime per financial year.' />
          </>
        )}

        {statute !== 'income_tax' && (
          <TextInput label="Financial year (optional)" placeholder="2026-2027" value={financialYear}
            onChange={(e) => setFinancialYear(e.target.value)} />
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <TextInput label="Source (notification / circular)" value={citation}
            onChange={(e) => setCitation(e.target.value)}
            description="What these values come from. Required before verification." />
          <TextInput label="Source URL" value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)} />
          <TextInput label="Retrieved on" type="date" value={retrievedOn}
            onChange={(e) => setRetrievedOn(e.target.value)}
            description="When the values were taken from the source." />
        </div>
      </div>
    </Modal>
  )
}

/* ============================================== verification controls */

function VerifyDialog({ ruleSet, onClose }: { ruleSet: StatutoryRuleSet; onClose: () => void }) {
  const toast = useToast()
  const actions = useRuleSetActions(ruleSet.id)
  const [note, setNote] = useState('')
  const [selfReason, setSelfReason] = useState('')
  const [error, setError] = useState('')

  return (
    <Modal
      open
      onClose={onClose}
      busy={actions.verify.isPending}
      size="sm"
      title="Verify these rates"
      description="Certifies that the values match their gazette source. Payroll approvals will rely on this signature."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={actions.verify.isPending}
            onClick={() =>
              actions.verify.mutate(
                { note, ...(selfReason ? { self_verification_reason: selfReason } : {}) },
                {
                  onSuccess: () => { toast.success('Rates verified'); onClose() },
                  onError: (err) => setError(err instanceof ApiError ? err.displayMessage : 'Refused.'),
                },
              )
            }>
            Verify
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}
        <TextArea label="What was checked" rows={2} value={note} onChange={(e) => setNote(e.target.value)}
          description="e.g. Matched against EPFO circular of 01-04-2026." />
        <TextInput label="Self-verification reason (only if you also edited these values)"
          value={selfReason} onChange={(e) => setSelfReason(e.target.value)}
          description="Four-eyes rule: the editor cannot verify without recording why no second person was available." />
      </div>
    </Modal>
  )
}

/* ===================================================== components tab */

const COMPONENT_TYPES = [
  { value: 'earning', label: 'Earning' },
  { value: 'deduction', label: 'Deduction' },
  { value: 'employer_contribution', label: 'Employer contribution' },
  { value: 'reimbursement', label: 'Reimbursement' },
]

function ComponentDialog({ existing, onClose }: { existing: SalaryComponent | null; onClose: () => void }) {
  const toast = useToast()
  const save = useSaveComponent()
  const components = useSalaryComponents()
  const [form, setForm] = useState({
    code: existing?.code ?? '',
    name: existing?.name ?? '',
    component_type: existing?.component_type ?? 'earning',
    calc_type: existing?.calc_type ?? 'fixed',
    percent_of_code: existing?.percent_of_code ?? '',
    is_wage: existing?.is_wage ?? false,
    is_taxable: existing?.is_taxable ?? true,
    is_part_of_ctc: existing?.is_part_of_ctc ?? true,
  })
  const [error, setError] = useState('')

  return (
    <Modal
      open
      onClose={onClose}
      busy={save.isPending}
      size="md"
      title={existing ? `Edit ${existing.code}` : 'Add salary component'}
      description="A nameable part of pay — Basic, HRA, an allowance. Amounts are set per employee in their salary structure; this defines how the component behaves."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={save.isPending}
            disabled={!form.code.trim() || !form.name.trim()}
            onClick={() =>
              save.mutate(
                { id: existing?.id, ...form, calc_type: form.calc_type as SalaryComponent['calc_type'] },
                {
                  onSuccess: () => { toast.success(existing ? 'Component updated' : 'Component added'); onClose() },
                  onError: (err) => setError(err instanceof ApiError ? err.displayMessage : 'Could not save.'),
                },
              )
            }>
            {existing ? 'Save changes' : 'Add component'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Banner tone="danger">{error}</Banner>}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextInput label="Code" required value={form.code} disabled={Boolean(existing)}
            onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase().replace(/\s+/g, '_') })}
            description="Stable identifier, e.g. BASIC, HRA." />
          <TextInput label="Name" required value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Select label="Type" value={form.component_type}
            onChange={(e) => setForm({ ...form, component_type: e.target.value })}
            options={COMPONENT_TYPES} />
          <Select label="Calculation" value={form.calc_type}
            onChange={(e) => setForm({ ...form, calc_type: e.target.value as 'fixed' | 'percent_of' })}
            options={[
              { value: 'fixed', label: 'Fixed amount (₹ set per employee)' },
              { value: 'percent_of', label: 'Percentage of another component' },
            ]} />
        </div>
        {form.calc_type === 'percent_of' && (
          <Select label="Percentage of" required placeholder="Choose the base component"
            value={form.percent_of_code}
            onChange={(e) => setForm({ ...form, percent_of_code: e.target.value })}
            options={(components.data ?? [])
              .filter((c) => c.code !== form.code)
              .map((c) => ({ value: c.code, label: `${c.code} — ${c.name}` }))}
            description="e.g. HRA at 50 means: HRA = 50% of this base. The percentage itself is set per employee." />
        )}
        <div className="space-y-2">
          <Checkbox label="Wage component (counts toward Basic + DA for PF)"
            checked={form.is_wage} onChange={(e) => setForm({ ...form, is_wage: e.target.checked })} />
          <Checkbox label="Taxable"
            checked={form.is_taxable} onChange={(e) => setForm({ ...form, is_taxable: e.target.checked })} />
          <Checkbox label="Part of CTC"
            checked={form.is_part_of_ctc} onChange={(e) => setForm({ ...form, is_part_of_ctc: e.target.checked })} />
        </div>
      </div>
    </Modal>
  )
}

/* ============================================================== page */

export function PayrollSettingsPage() {
  const permissions = usePermissions()
  const toast = useToast()
  const canSeeRates = permissions.can(RESOURCE.STATUTORY_CONFIG, ACTION.VIEW)
  const canEditRates = permissions.can(RESOURCE.STATUTORY_CONFIG, ACTION.EDIT)
  const canVerify = permissions.can(RESOURCE.STATUTORY_CONFIG, ACTION.APPROVE)
  const canEditComponents = permissions.can(RESOURCE.SALARY, ACTION.EDIT)

  const ruleSets = useStatutoryRuleSets(canSeeRates)
  const components = useSalaryComponents()

  const [tab, setTab] = useState('rates')
  const [dialog, setDialog] = useState<{ statute: StatuteKey; existing: StatutoryRuleSet | null } | null>(null)
  const [verifying, setVerifying] = useState<StatutoryRuleSet | null>(null)
  const [componentDialog, setComponentDialog] = useState<{ existing: SalaryComponent | null } | null>(null)

  const componentColumns: Array<Column<SalaryComponent>> = [
    {
      key: 'code', header: 'Component',
      render: (row) => (
        <div className="flex min-w-0 items-center gap-2">
          <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: LIME }} />
          <div className="min-w-0">
            <p className="truncate font-medium text-ink">{row.code}</p>
            <p className="truncate text-xs text-ink-muted">{row.name}</p>
          </div>
        </div>
      ),
    },
    { key: 'type', header: 'Type', render: (row) => humanize(row.component_type) },
    {
      key: 'calc', header: 'Calculation',
      render: (row) => (row.calc_type === 'fixed' ? 'Fixed ₹' : `% of ${row.percent_of_code}`),
    },
    {
      key: 'flags', header: 'Treatment',
      render: (row) => (
        <span className="flex flex-wrap gap-1">
          {row.is_wage && <Badge tone="brand">PF wage</Badge>}
          {row.is_taxable && <Badge tone="neutral">Taxable</Badge>}
          {row.is_part_of_ctc && <Badge tone="neutral">CTC</Badge>}
        </span>
      ),
    },
    {
      key: 'actions', header: 'Actions', headerSrOnly: true, align: 'right',
      render: (row) =>
        canEditComponents ? (
          <Button size="sm" onClick={() => setComponentDialog({ existing: row })}>Edit</Button>
        ) : null,
    },
  ]

  if (!canSeeRates && !canEditComponents) {
    return (
      <>
        <PageHeader title="Payroll settings" />
        <EmptyState title="Payroll configuration is restricted"
          description="Only Finance Head, HR Head and Admin can view or change payroll rules." />
      </>
    )
  }

  const allRuleSets = ruleSets.data ?? []

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2.5">
            <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-brand">
              <span aria-hidden className="block h-4 w-4 rounded-full border-[3px]" style={{ borderColor: LIME }} />
            </span>
            Payroll settings
          </span>
        }
        description="Statutory rates and the salary component catalogue. Every value here is entered and verified by Finance/HR — nothing is hard-coded."
      />

      <Tabs
        active={tab}
        onChange={setTab}
        items={[
          { id: 'rates', label: 'Statutory rates', count: allRuleSets.filter((r) => r.verification_status !== 'verified').length || undefined },
          { id: 'components', label: 'Salary components', count: components.data?.length },
        ]}
      />

      <TabPanel id="rates" active={tab}>
        <div className="space-y-5">
          {ruleSets.isLoading ? (
            <TableSkeleton columns={4} />
          ) : ruleSets.isError ? (
            <ErrorState error={ruleSets.error} onRetry={() => void ruleSets.refetch()} />
          ) : (
            STATUTES.map((spec) => {
              const rows = allRuleSets
                .filter((rs) => rs.statute === spec.key)
                .sort((a, b) => (a.effective_from < b.effective_from ? 1 : -1))
              return (
                <Card key={spec.key} className="space-y-4">
                  <CardHeader
                    title={spec.label}
                    description={
                      spec.key === 'pt'
                        ? 'A step slab per state. The matched slab’s amount is the tax.'
                        : spec.key === 'income_tax'
                          ? 'One rule set per regime per financial year.'
                          : 'Percentages and wage limits, effective-dated.'
                    }
                    action={
                      canEditRates ? (
                        <Button size="sm" variant="primary"
                          onClick={() => setDialog({ statute: spec.key, existing: null })}>
                          New rates
                        </Button>
                      ) : undefined
                    }
                  />
                  {rows.length === 0 ? (
                    <EmptyState compact title="No rates configured"
                      description="Payroll cannot compute this statute until rates are entered and verified." />
                  ) : (
                    <ul className="divide-y divide-line">
                      {rows.map((rs) => (
                        <li key={rs.id} className="flex flex-wrap items-center gap-3 py-3">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="text-sm font-medium text-ink">
                                {formatDate(rs.effective_from)} → {rs.effective_to ? formatDate(rs.effective_to) : 'in force'}
                              </p>
                              {rs.jurisdiction && <Badge tone="neutral">{rs.jurisdiction}</Badge>}
                              {rs.regime && <Badge tone="neutral">{humanize(rs.regime)} regime</Badge>}
                              {rs.financial_year && <Badge tone="neutral">{rs.financial_year}</Badge>}
                              <Badge tone={STATUS_TONES[rs.verification_status]}>
                                {humanize(rs.verification_status)}
                              </Badge>
                              {rs.is_tampered && <Badge tone="danger">Altered after verification</Badge>}
                              {rs.was_self_verified && <Badge tone="warning">Self-verified</Badge>}
                            </div>
                            <p className="text-xs text-ink-muted">{summariseParameters(rs)}</p>
                            {rs.verified_by_email && rs.verification_status === 'verified' && (
                              <p className="text-xs text-ink-muted">
                                Verified by {rs.verified_by_email} on {formatDate(rs.verified_at)}
                              </p>
                            )}
                            {rs.rejection_reason && rs.verification_status === 'rejected' && (
                              <p className="text-xs text-danger-ink">{rs.rejection_reason}</p>
                            )}
                          </div>
                          <div className="flex shrink-0 flex-wrap gap-2">
                            {canEditRates && rs.verification_status !== 'superseded' && (
                              <Button size="sm" onClick={() => setDialog({ statute: spec.key as StatuteKey, existing: rs })}>
                                Edit
                              </Button>
                            )}
                            {canEditRates && rs.verification_status === 'draft' && (
                              <SubmitButton ruleSet={rs} />
                            )}
                            {canVerify && rs.verification_status === 'pending' && (
                              <>
                                <Button size="sm" variant="primary" onClick={() => setVerifying(rs)}>Verify</Button>
                                <RejectButton ruleSet={rs} />
                              </>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </Card>
              )
            })
          )}
          <Banner tone="info" title="How changes take effect">
            New rates are drafts until submitted and verified (four-eyes). Each set carries an
            effective-from date, so past payroll keeps the rates it was computed with — a processed
            run stores a copy of the exact rates it used, and finalised runs are never recalculated.
          </Banner>
        </div>
      </TabPanel>

      <TabPanel id="components" active={tab}>
        <Card className="space-y-4">
          <CardHeader
            title="Salary components"
            description="Basic, HRA, allowances and deductions. Per-employee amounts and percentages live in each employee's salary structure."
            action={
              canEditComponents ? (
                <Button size="sm" variant="primary" onClick={() => setComponentDialog({ existing: null })}>
                  Add component
                </Button>
              ) : undefined
            }
          />
          {components.isLoading ? (
            <TableSkeleton columns={5} />
          ) : (components.data ?? []).length === 0 ? (
            <EmptyState compact title="No components yet"
              description="Add BASIC first, then the components that build on it (HRA as a percentage of BASIC, allowances as fixed amounts)." />
          ) : (
            <DataTable caption="Salary components" columns={componentColumns}
              rows={components.data ?? []} rowKey={(row) => row.id} />
          )}
        </Card>
      </TabPanel>

      {dialog && (
        <RuleSetDialog statute={dialog.statute} existing={dialog.existing} onClose={() => setDialog(null)} />
      )}
      {verifying && <VerifyDialog ruleSet={verifying} onClose={() => setVerifying(null)} />}
      {componentDialog && (
        <ComponentDialog existing={componentDialog.existing} onClose={() => setComponentDialog(null)} />
      )}
    </>
  )

  function SubmitButton({ ruleSet }: { ruleSet: StatutoryRuleSet }) {
    const actions = useRuleSetActions(ruleSet.id)
    return (
      <Button size="sm" loading={actions.submit.isPending}
        onClick={() =>
          actions.submit.mutate(undefined as never, {
            onSuccess: () => toast.success('Submitted for verification'),
            onError: (err) => toast.fromError(err, 'Could not submit'),
          })
        }>
        Submit for verification
      </Button>
    )
  }

  function RejectButton({ ruleSet }: { ruleSet: StatutoryRuleSet }) {
    const actions = useRuleSetActions(ruleSet.id)
    return (
      <Button size="sm" variant="danger-soft" loading={actions.reject.isPending}
        onClick={() => {
          const reason = window.prompt('Why are these rates being rejected?')
          if (!reason) return
          actions.reject.mutate({ reason }, {
            onSuccess: () => toast.success('Rates rejected back to the editor'),
            onError: (err) => toast.fromError(err, 'Could not reject'),
          })
        }}>
        Reject
      </Button>
    )
  }
}

/** One line of the numbers that matter, per statute. */
function summariseParameters(rs: StatutoryRuleSet): string {
  const p = rs.parameters as Record<string, unknown>
  if (rs.statute === 'pf') {
    return `Employee ${p.employee_rate ?? '—'}% · Employer ${p.employer_rate ?? '—'}% (EPS ${p.eps_rate ?? '—'}%) · PF limit ${formatCurrency(p.wage_ceiling as string)} · EPS limit ${formatCurrency(p.eps_wage_ceiling as string)}`
  }
  if (rs.statute === 'esi') {
    return `Employee ${p.employee_rate ?? '—'}% · Employer ${p.employer_rate ?? '—'}% · wage limit ${formatCurrency(p.gross_wage_threshold as string)}`
  }
  if (rs.statute === 'pt') {
    const slabs = Array.isArray(p.slabs) ? (p.slabs as unknown[]).length : 0
    return `${slabs} slab${slabs === 1 ? '' : 's'}${p.special_month ? ` · special amount in month ${p.special_month}` : ''}`
  }
  if (rs.statute === 'income_tax') {
    const slabs = Array.isArray(p.slabs) ? (p.slabs as unknown[]).length : 0
    return `${slabs} slab${slabs === 1 ? '' : 's'} · standard deduction ${formatCurrency(p.standard_deduction as string)}`
  }
  return rs.rule_version
}
