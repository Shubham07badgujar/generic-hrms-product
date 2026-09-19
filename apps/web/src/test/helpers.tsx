/** Test plumbing: a render that provides every context the app needs. */

import type { ReactElement, ReactNode } from 'react'
import { render, type RenderOptions } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '@/components/ui/Toast'
import { Permissions } from '@/lib/permissions'
import type { FeatureCode, PermissionSnapshot, RoleCode, Scope } from '@/lib/types'

/**
 * Every module, for a test that is not about entitlement.
 *
 * The default is ALL features on purpose. A test asking whether an HR Head
 * sees the payroll menu is asking about permissions; if the fixture quietly
 * had no plan it would pass for the wrong reason, or fail for one. Tests that
 * ARE about entitlement pass their own list.
 */
export const ALL_FEATURES: FeatureCode[] = [
  'core',
  'recruitment',
  'onboarding',
  'offboarding',
  'attendance',
  'attendance_biometric',
  'leave',
  'payroll',
  'assets',
  'it_accounts',
  'reporting',
]

export function makeSnapshot(overrides: Partial<PermissionSnapshot> = {}): PermissionSnapshot {
  return {
    dashboard: 'self',
    read_only: false,
    can_manage_users: false,
    layers: [5],
    roles: [],
    grants: {},
    features: ALL_FEATURES,
    organization_status: 'active',
    notice: 'test',
    ...overrides,
  }
}

/** Builds a snapshot from a compact `{ resource: { action: scope } }` map. */
export function permissionsFor(
  grants: Record<string, Record<string, Scope>>,
  overrides: Partial<PermissionSnapshot> = {},
) {
  return new Permissions(makeSnapshot({ grants, ...overrides }))
}

export const ROLE_FIXTURES: Record<
  string,
  { roles: RoleCode[]; grants: Record<string, Record<string, Scope>>; extra?: Partial<PermissionSnapshot> }
> = {
  hr_head: {
    roles: ['hr_head'],
    grants: {
      application: { view: 'all', create: 'all', edit: 'all', approve: 'all', reject: 'all' },
      candidate: { view: 'all', create: 'all', edit: 'all' , import: 'all' },
      job_opening: { view: 'all', create: 'all', edit: 'all' },
      interview: { view: 'all', create: 'all', edit: 'all' },
      interview_feedback: { view: 'all' },
      offer: { view: 'all', create: 'all', edit: 'all' },
      employee: { view: 'all', create: 'all', edit: 'all' },
      user: { view: 'all', create: 'all' },
      department_decision: { view: 'all' },
      employee_document: {
        view: 'all', create: 'all', edit: 'all', approve: 'all', reject: 'all',
      },
      probation_review: { view: 'all', create: 'all', edit: 'all', decide: 'all' },
      onboarding: { view: 'all', create: 'all', edit: 'all', approve: 'all' },
      letter: { view: 'all', create: 'all' },
      email_account: { view: 'all', create: 'all', edit: 'all' },
      asset: { view: 'all', create: 'all', edit: 'all' },
      asset_allocation: { view: 'all', create: 'all', edit: 'all', delete: 'all' },
      offboarding: { view: 'all', create: 'all', edit: 'all', approve: 'all' },
      // Payroll co-ownership: prepares and reviews, never releases or
      // certifies — APPROVE on runs and statutory config is Finance-only.
      salary: { view: 'all', create: 'all', edit: 'all' },
      package: { view: 'all', create: 'all', edit: 'all', delete: 'all', approve: 'all' },
      payroll_run: { view: 'all', create: 'all', edit: 'all', export: 'all' },
      payslip: { view: 'all', export: 'all' },
      payroll_adjustment: { view: 'all', create: 'all', edit: 'all', approve: 'all' },
      statutory_config: { view: 'all', create: 'all', edit: 'all' },
      report: { view: 'all', export: 'all' },
      audit_log: { view: 'all' },
      notification: { view: 'self', edit: 'self' },
    },
    extra: { dashboard: 'department', can_manage_users: true, layers: [2] },
  },
  finance_head: {
    roles: ['finance_head'],
    grants: {
      employee: { view: 'all' },
      salary: { view: 'all', create: 'all', edit: 'all', delete: 'all' },
      package: { view: 'all', create: 'all', edit: 'all', delete: 'all', approve: 'all' },
      payroll_run: { view: 'all', create: 'all', edit: 'all', approve: 'all', export: 'all' },
      payslip: { view: 'all', export: 'all' },
      payroll_adjustment: {
        view: 'all', create: 'all', edit: 'all', delete: 'all', approve: 'all',
      },
      statutory_config: { view: 'all', create: 'all', edit: 'all', approve: 'all' },
      report: { view: 'all', export: 'all' },
      audit_log: { view: 'all' },
      notification: { view: 'self', edit: 'self' },
    },
    extra: { dashboard: 'department', layers: [2] },
  },
  medical_director: {
    roles: ['medical_director'],
    grants: {
      application: { view: 'department' },
      candidate: { view: 'department' },
      job_opening: { view: 'department' },
      interview: { view: 'department' },
      interview_feedback: { view: 'department' },
      department_decision: { view: 'department', recommend: 'department', create: 'department' },
      employee: { view: 'department' },
      employee_document: { view: 'department' },
      report: { view: 'department', export: 'department' },
      audit_log: { view: 'department' },
      notification: { view: 'self', edit: 'self' },
      probation_review: { view: 'department', edit: 'department' },
      onboarding: { view: 'department' },
      asset_allocation: { view: 'department' },
      letter: { view: 'self' },
      offboarding: { view: 'department', edit: 'department' },
    },
    extra: { dashboard: 'department', layers: [2] },
  },
  clinic_doctor: {
    roles: ['clinic_doctor'],
    grants: {
      application: { view: 'self' },
      candidate: { view: 'self' },
      interview: { view: 'self', edit: 'self' },
      interview_feedback: { view: 'self', create: 'self', edit: 'self' },
    },
    extra: { dashboard: 'executive', layers: [4] },
  },
  hr_manager: {
    roles: ['hr_manager'],
    grants: {
      application: { view: 'all', create: 'all', edit: 'all' },
      // Holds IMPORT in the backend matrix, alongside Admin, HR Head and
      // Recruiter — and nobody else.
      candidate: { view: 'all', create: 'all', edit: 'all', import: 'all' },
      job_opening: { view: 'all', create: 'all', edit: 'all' },
      interview: { view: 'all', create: 'all', edit: 'all' },
      employee: { view: 'all', create: 'all', edit: 'all' },
      employee_document: {
        view: 'all', create: 'all', edit: 'all', approve: 'all', reject: 'all',
      },
      user: { view: 'all', create: 'all' },
      notification: { view: 'self', edit: 'self' },
    },
    extra: { dashboard: 'manager', can_manage_users: true, layers: [3] },
  },
  recruiter: {
    roles: ['recruiter'],
    grants: {
      application: { view: 'all', create: 'all', edit: 'all' },
      candidate: { view: 'all', create: 'all', edit: 'all' , import: 'all' },
      job_opening: { view: 'all', create: 'all', edit: 'all' },
      interview: { view: 'all', create: 'all', edit: 'all' },
      // VIEW org-wide; CREATE/EDIT at SELF so a recruiter can record the
      // rounds a workflow puts them in. Mirrors the matrix.
      interview_feedback: { view: 'all', create: 'self', edit: 'self' },
      offer: { view: 'all' },
      // Read-only on the organisation's own configuration, mirroring the
      // backend matrix exactly. These are the job opening form's dropdowns;
      // without them its required fields cannot be filled. `designation` also
      // serves the seniority levels list, as it does on the server.
      department: { view: 'all' },
      designation: { view: 'all' },
      location: { view: 'all' },
      role: { view: 'all' },
      hiring_workflow: { view: 'all' },
    },
    extra: { dashboard: 'executive', layers: [4] },
  },
  admin: {
    roles: ['admin'],
    grants: {
      application: { view: 'all', create: 'all', edit: 'all', override: 'all', approve: 'all' },
      candidate: { view: 'all', create: 'all', edit: 'all' , import: 'all' },
      job_opening: { view: 'all', create: 'all', edit: 'all' },
      // _manage(S.ALL, ...ADMIN_MANAGED) in the matrix: full CRUD, delete included.
      employee: { view: 'all', create: 'all', edit: 'all', delete: 'all' },
      employee_document: {
        view: 'all', create: 'all', edit: 'all', approve: 'all', reject: 'all',
      },
      user: { view: 'all', create: 'all', edit: 'all' },
      role: { view: 'all', create: 'all', edit: 'all', delete: 'all' },
      department: { view: 'all', create: 'all', edit: 'all', delete: 'all' },
      designation: { view: 'all', create: 'all', edit: 'all', delete: 'all' },
      location: { view: 'all', create: 'all', edit: 'all', delete: 'all' },
      hiring_workflow: { view: 'all', create: 'all', edit: 'all', delete: 'all' },
      org_settings: { view: 'all', edit: 'all' },
      audit_log: { view: 'all' },
    },
    extra: { dashboard: 'admin', can_manage_users: true, layers: [1] },
  },
  ceo: {
    roles: ['ceo'],
    grants: {
      application: { view: 'all', export: 'all' },
      employee_document: { view: 'all', export: 'all' },
      candidate: { view: 'all', export: 'all' },
      job_opening: { view: 'all' },
      employee: { view: 'all', export: 'all' },
      report: { view: 'all', export: 'all' },
      audit_log: { view: 'all', export: 'all' },
      notification: { view: 'all' },
      dashboard_org: { view: 'all' },
    },
    extra: { dashboard: 'ceo', read_only: true, layers: [1] },
  },
  employee: {
    roles: ['employee'],
    grants: {
      employee: { view: 'self' },
      package: { view: 'self' },
      payslip: { view: 'self' },
      leave_request: { view: 'self', create: 'self' },
      employee_document: { view: 'self', create: 'self' },
      onboarding: { view: 'self', edit: 'self' },
      asset_allocation: { view: 'self' },
      email_account: { view: 'self' },
      letter: { view: 'self' },
      offboarding: { view: 'self', create: 'self', edit: 'self' },
    },
    extra: { dashboard: 'self', layers: [5] },
  },
}

export function snapshotForRole(role: keyof typeof ROLE_FIXTURES): PermissionSnapshot {
  const fixture = ROLE_FIXTURES[role]!
  return makeSnapshot({ roles: fixture.roles, grants: fixture.grants, ...fixture.extra })
}

export function permissionsForRole(role: keyof typeof ROLE_FIXTURES) {
  return new Permissions(snapshotForRole(role))
}

export function TestProviders({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ToastProvider>{children}</ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

export function renderWithProviders(ui: ReactElement, options?: RenderOptions) {
  return render(ui, { wrapper: TestProviders, ...options })
}
