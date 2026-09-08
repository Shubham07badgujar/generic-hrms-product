/**
 * Payroll hooks.
 *
 * `blockers` and `can_approve` arrive from the server on the run detail
 * payload — this module never computes them. One function on the backend
 * decides whether a run may be approved, guards the action, and hands the same
 * answer to the UI, so a disabled button and a refused request always agree.
 *
 * Nothing here recomputes an amount either. Every figure shown comes from the
 * stored payslip, because a screen that recalculated its own totals could
 * disagree with the payslip the employee was actually paid.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, apiPatch, apiPost, type Paginated, toRelative } from './api'
import type { ListParams } from './queries'
import type {
  EmployeePackage,
  InvestmentDeclaration,
  MyPayroll,
  PackageDeferral,
  PayrollAdjustment,
  PayrollRunDetail,
  PayrollRunSummary,
  PayslipDetail,
  PayslipSummary,
  SalaryComponent,
  SalaryStructure,
  StatutoryRuleSet,
  UUID,
} from './types'

function clean(params: ListParams): Record<string, string> {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== ''),
  ) as Record<string, string>
}

export const payrollKeys = {
  runs: (params?: unknown) => ['payroll-runs', params ?? {}] as const,
  run: (id: UUID) => ['payroll-runs', 'detail', id] as const,
  payslips: (params?: unknown) => ['payslips', params ?? {}] as const,
  payslip: (id: UUID) => ['payslips', 'detail', id] as const,
  structures: (params?: unknown) => ['salary-structures', params ?? {}] as const,
  components: ['salary-components'] as const,
  ruleSets: ['statutory-rule-sets'] as const,
  mine: ['payroll', 'mine'] as const,
  adjustments: (params?: unknown) => ['payroll-adjustments', params ?? {}] as const,
}

/**
 * One payroll action changes several parts of a page at once — approving a run
 * changes its status, its lock, its blockers AND every payslip's run status —
 * so everything the detail view shows refreshes together.
 */
function usePayrollInvalidator(runId?: UUID) {
  const queryClient = useQueryClient()
  return () => {
    if (runId) void queryClient.invalidateQueries({ queryKey: payrollKeys.run(runId) })
    for (const key of [
      'payroll-runs', 'payslips', 'salary-structures', 'salary-components',
      'statutory-rule-sets', 'payroll', 'payroll-adjustments', 'packages',
    ]) {
      void queryClient.invalidateQueries({ queryKey: [key] })
    }
  }
}

/* ------------------------------------------------------------------ runs */

export function usePayrollRuns(params: ListParams = {}) {
  return useQuery({
    queryKey: payrollKeys.runs(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<PayrollRunSummary>>(toRelative(params.cursor))
        : apiGet<Paginated<PayrollRunSummary>>('/payroll/runs/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function usePayrollRun(id: UUID | undefined) {
  return useQuery({
    queryKey: payrollKeys.run(id ?? 'none'),
    queryFn: () => apiGet<PayrollRunDetail>(`/payroll/runs/${id}/`),
    enabled: Boolean(id),
  })
}

export function useCreatePayrollRun() {
  const invalidate = usePayrollInvalidator()
  return useMutation({
    mutationFn: (body: {
      period_year: number
      period_month: number
      location?: UUID | null
      run_type?: string
      notes?: string
    }) => apiPost<PayrollRunDetail>('/payroll/runs/', body),
    onSuccess: invalidate,
  })
}

export function usePayrollRunActions(id: UUID) {
  const invalidate = usePayrollInvalidator(id)
  const mutate = <T,>(path: string) =>
    useMutation({
      mutationFn: (body: T) => apiPost<PayrollRunDetail>(`/payroll/runs/${id}/${path}/`, body),
      onSuccess: invalidate,
    })

  return {
    process: mutate<void>('process'),
    // Refused by the server while any rule set is unverified, and refused
    // again if the caller is the person who processed the run.
    approve: mutate<{ notes?: string }>('approve'),
    reject: mutate<{ reason: string }>('reject'),
    markPaid: mutate<{ paid_at?: string }>('mark-paid'),
    reverse: mutate<{ reason: string }>('reverse'),
  }
}

/* -------------------------------------------------------------- payslips */

export function usePayslips(params: ListParams = {}) {
  return useQuery({
    queryKey: payrollKeys.payslips(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<PayslipSummary>>(toRelative(params.cursor))
        : apiGet<Paginated<PayslipSummary>>('/payslips/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function usePayslip(id: UUID | undefined) {
  return useQuery({
    queryKey: payrollKeys.payslip(id ?? 'none'),
    queryFn: () => apiGet<PayslipDetail>(`/payslips/${id}/`),
    enabled: Boolean(id),
  })
}

/** Self-service. Server resolves "mine" from the token, never a client-sent id. */
export function useMyPayroll() {
  return useQuery({
    queryKey: payrollKeys.mine,
    queryFn: () => apiGet<MyPayroll>('/payroll/me/'),
  })
}

/* ------------------------------------------------------------ structures */

export function useSalaryStructures(params: ListParams = {}) {
  return useQuery({
    queryKey: payrollKeys.structures(params),
    queryFn: () =>
      params.cursor
        ? apiGet<Paginated<SalaryStructure>>(toRelative(params.cursor))
        : apiGet<Paginated<SalaryStructure>>('/payroll/structures/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useSalaryComponents(enabled = true) {
  return useQuery({
    queryKey: payrollKeys.components,
    queryFn: () => apiGet<SalaryComponent[]>('/payroll/components/'),
    enabled,
  })
}

export function useCreateSalaryStructure() {
  const invalidate = usePayrollInvalidator()
  return useMutation({
    mutationFn: (body: {
      employee: UUID
      ctc_annual: string
      valid_from: string
      revision_reason?: string
      lines: Array<{ component: UUID; value: string }>
      pf_applicable?: boolean
      esi_applicable?: boolean
      pt_applicable?: boolean
      tds_applicable?: boolean
      gratuity_applicable?: boolean
    }) => apiPost<SalaryStructure>('/payroll/structures/', body),
    onSuccess: invalidate,
  })
}

/* ----------------------------------------------------------- rule sets */

export function useStatutoryRuleSets(enabled = true) {
  return useQuery({
    queryKey: payrollKeys.ruleSets,
    queryFn: () => apiGet<StatutoryRuleSet[]>('/payroll/rule-sets/'),
    enabled,
  })
}

export function useRuleSetActions(id: UUID) {
  const invalidate = usePayrollInvalidator()
  const mutate = <T,>(path: string) =>
    useMutation({
      mutationFn: (body: T) =>
        apiPost<StatutoryRuleSet>(`/payroll/rule-sets/${id}/${path}/`, body),
      onSuccess: invalidate,
    })

  return {
    submit: mutate<void>('submit'),
    // Finance Head only. The server refuses a verifier who edited the rate set,
    // unless they supply an explicit self-verification reason.
    verify: mutate<{ note?: string; self_verification_reason?: string }>('verify'),
    reject: mutate<{ reason: string }>('reject'),
  }
}

/** Create a draft rule set, or edit one (drafts only in practice — editing a
 *  verified set auto-reverts it to draft server-side, loudly). */
export function useSaveRuleSet() {
  const invalidate = usePayrollInvalidator()
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<StatutoryRuleSet> & { id?: UUID }) =>
      id
        ? apiPatch<StatutoryRuleSet>(`/payroll/rule-sets/${id}/`, body)
        : apiPost<StatutoryRuleSet>('/payroll/rule-sets/', body),
    onSuccess: invalidate,
  })
}

/** Create or edit a salary component in the catalogue. */
export function useSaveComponent() {
  const invalidate = usePayrollInvalidator()
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<SalaryComponent> & { id?: UUID }) =>
      id
        ? apiPatch<SalaryComponent>(`/payroll/components/${id}/`, body)
        : apiPost<SalaryComponent>('/payroll/components/', body),
    onSuccess: invalidate,
  })
}

/* -------------------------------------------------------- declarations */

export function useDeclarations(params: ListParams = {}) {
  return useQuery({
    queryKey: ['declarations', params],
    queryFn: () =>
      apiGet<Paginated<InvestmentDeclaration>>('/payroll/declarations/', {
        params: clean(params),
      }),
    placeholderData: (previous) => previous,
  })
}

export function useSaveDeclaration() {
  const invalidate = usePayrollInvalidator()
  return useMutation({
    mutationFn: (body: {
      employee: UUID
      financial_year: string
      regime?: string
      declarations?: Record<string, string>
    }) => apiPost<InvestmentDeclaration>('/payroll/declarations/', body),
    onSuccess: invalidate,
  })
}

export function useReviewDeclaration(id: UUID) {
  const invalidate = usePayrollInvalidator()
  return useMutation({
    mutationFn: (body: { approve: boolean; note?: string }) =>
      apiPost<InvestmentDeclaration>(`/payroll/declarations/${id}/review/`, body),
    onSuccess: invalidate,
  })
}

/* ----------------------------------------------------------- packages */

export interface PackagePayload {
  employee?: UUID
  total_amount: string
  package_type?: string
  start_date: string
  end_date: string
  notes?: string
  reason?: string
  bond_start_date?: string | null
  bond_end_date?: string | null
  bond_required_months?: number | null
  periods: Array<{ label: string; start_date: string; end_date: string; amount: string }>
  deferrals: Array<{
    label: string
    amount: string
    condition_type: string
    condition_months?: number | null
    eligible_on?: string | null
    condition_note?: string
  }>
}

export function usePackages(params: ListParams = {}) {
  return useQuery({
    queryKey: ['packages', params],
    queryFn: () =>
      apiGet<Paginated<EmployeePackage>>('/payroll/packages/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function usePackageAlerts(enabled: boolean) {
  return useQuery({
    queryKey: ['packages', 'alerts'],
    queryFn: () =>
      apiGet<{
        eligible: Array<{
          package: UUID
          employee_name: string
          employee_code: string
          amounts: Array<{ id: UUID; label: string; amount: string; eligible_on: string }>
        }>
      }>('/payroll/packages/alerts/'),
    enabled,
  })
}

export function useSavePackage() {
  const invalidate = usePayrollInvalidator()
  return useMutation({
    mutationFn: ({ id, ...body }: PackagePayload & { id?: UUID }) =>
      id
        ? apiPatch<EmployeePackage>(`/payroll/packages/${id}/`, body)
        : apiPost<EmployeePackage>('/payroll/packages/', body),
    onSuccess: invalidate,
  })
}

export function usePackageActions() {
  const invalidate = usePayrollInvalidator()
  return {
    activate: useMutation({
      mutationFn: (id: UUID) => apiPost<EmployeePackage>(`/payroll/packages/${id}/activate/`),
      onSuccess: invalidate,
    }),
    setStatus: useMutation({
      mutationFn: (input: { id: UUID; status: string; reason?: string }) =>
        apiPost<EmployeePackage>(`/payroll/packages/${input.id}/set-status/`, {
          status: input.status, reason: input.reason ?? '',
        }),
      onSuccess: invalidate,
    }),
    revise: useMutation({
      mutationFn: ({ id, ...body }: PackagePayload & { id: UUID }) =>
        apiPost<EmployeePackage>(`/payroll/packages/${id}/revise/`, body),
      onSuccess: invalidate,
    }),
    decide: useMutation({
      mutationFn: (input: {
        packageId: UUID
        deferralId: UUID
        decision: 'approve' | 'reject' | 'hold'
        period_year?: number
        period_month?: number
        reason?: string
      }) =>
        apiPost<PackageDeferral>(
          `/payroll/packages/${input.packageId}/deferrals/${input.deferralId}/decide/`,
          {
            decision: input.decision, period_year: input.period_year,
            period_month: input.period_month, reason: input.reason ?? '',
          },
        ),
      onSuccess: invalidate,
    }),
    reschedule: useMutation({
      mutationFn: (input: {
        packageId: UUID
        deferralId: UUID
        eligible_on: string
        reason: string
      }) =>
        apiPost<PackageDeferral>(
          `/payroll/packages/${input.packageId}/deferrals/${input.deferralId}/reschedule/`,
          { eligible_on: input.eligible_on, reason: input.reason },
        ),
      onSuccess: invalidate,
    }),
  }
}

/* -------------------------------------------------------- adjustments */

export function useAdjustments(params: ListParams = {}) {
  return useQuery({
    queryKey: payrollKeys.adjustments(params),
    queryFn: () =>
      apiGet<Paginated<PayrollAdjustment>>('/payroll/adjustments/', { params: clean(params) }),
    placeholderData: (previous) => previous,
  })
}

export function useAdjustmentActions() {
  const invalidate = usePayrollInvalidator()
  return {
    create: useMutation({
      mutationFn: (body: Partial<PayrollAdjustment>) =>
        apiPost<PayrollAdjustment>('/payroll/adjustments/', body),
      onSuccess: invalidate,
    }),
    approve: useMutation({
      mutationFn: (id: UUID) =>
        apiPost<PayrollAdjustment>(`/payroll/adjustments/${id}/approve/`),
      onSuccess: invalidate,
    }),
  }
}

/** Download URLs. Auth travels on the fetch, so these go through apiGet callers. */
export const payrollDownloads = {
  register: (runId: UUID) => `/payroll/runs/${runId}/register/`,
  neft: (runId: UUID) => `/payroll/runs/${runId}/neft/`,
  payslipPdf: (payslipId: UUID) => `/payslips/${payslipId}/pdf/`,
}
