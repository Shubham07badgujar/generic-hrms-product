/**
 * The client-side permission model.
 *
 * THIS IS PRESENTATION LOGIC, NOT SECURITY.
 *
 * The snapshot from `/me/permissions/` says so in its own `notice` field, and
 * it is worth restating here because the temptation to treat it as a gate is
 * real: every permission is re-resolved server-side on every request, and the
 * backend's own test suite asserts that a hand-built HTTP call from the wrong
 * role gets 403 regardless of what this file believes.
 *
 * What this buys is a UI that doesn't offer people actions that will fail.
 * Nothing more.
 */

import type {
  FeatureCode,
  OrganizationStatus,
  PermissionSnapshot,
  RoleCode,
  Scope,
} from './types'

export const RESOURCE = {
  USER: 'user',
  ROLE: 'role',
  DEPARTMENT: 'department',
  DESIGNATION: 'designation',
  LOCATION: 'location',
  ORG_SETTINGS: 'org_settings',
  EMPLOYEE: 'employee',
  EMPLOYEE_DOCUMENT: 'employee_document',
  PROBATION_REVIEW: 'probation_review',
  ASSET: 'asset',
  ASSET_ALLOCATION: 'asset_allocation',
  EMAIL_ACCOUNT: 'email_account',
  JOB_OPENING: 'job_opening',
  CANDIDATE: 'candidate',
  APPLICATION: 'application',
  INTERVIEW: 'interview',
  INTERVIEW_FEEDBACK: 'interview_feedback',
  DEPARTMENT_DECISION: 'department_decision',
  OFFER: 'offer',
  HIRING_WORKFLOW: 'hiring_workflow',
  ONBOARDING: 'onboarding',
  OFFBOARDING: 'offboarding',
  LETTER: 'letter',
  ATTENDANCE: 'attendance',
  REGULARIZATION: 'regularization',
  ATTENDANCE_DEVICE: 'attendance_device',
  LEAVE_REQUEST: 'leave_request',
  LEAVE_POLICY: 'leave_policy',
  SALARY: 'salary',
  PACKAGE: 'package',
  PAYROLL_RUN: 'payroll_run',
  PAYSLIP: 'payslip',
  PAYROLL_ADJUSTMENT: 'payroll_adjustment',
  STATUTORY_CONFIG: 'statutory_config',
  POLICY_DOC: 'policy_doc',
  AUDIT_LOG: 'audit_log',
  NOTIFICATION: 'notification',
  REPORT: 'report',
  DASHBOARD_ORG: 'dashboard_org',
  DASHBOARD_DEPARTMENT: 'dashboard_department',
  DASHBOARD_TEAM: 'dashboard_team',
} as const

export type Resource = (typeof RESOURCE)[keyof typeof RESOURCE]

export const ACTION = {
  VIEW: 'view',
  CREATE: 'create',
  EDIT: 'edit',
  DELETE: 'delete',
  APPROVE: 'approve',
  EXPORT: 'export',
  REJECT: 'reject',
  OVERRIDE: 'override',
  RECOMMEND: 'recommend',
  DECIDE: 'decide',
  // Bulk ingest of externally sourced records. Separate from CREATE because
  // adding one walk-in candidate and ingesting thousands of third-party records
  // under a legal-basis attestation are different authorities — and only one of
  // them is worth revoking on its own. Mirrors core/access/catalog.py.
  IMPORT: 'import',
} as const

export type Action = (typeof ACTION)[keyof typeof ACTION]

/** Mirrors the backend's ordering, so `>=` comparisons mean the same thing. */
const SCOPE_RANK: Record<Scope, number> = {
  none: 0,
  self: 1,
  team: 2,
  department: 3,
  all: 4,
}

export class Permissions {
  constructor(private readonly snapshot: PermissionSnapshot) {}

  /** The scope held for a pair, or `'none'`. Absence means deny. */
  scopeOf(resource: Resource, action: Action = ACTION.VIEW): Scope {
    return this.snapshot.grants[resource]?.[action] ?? 'none'
  }

  can(resource: Resource, action: Action = ACTION.VIEW): boolean {
    return this.scopeOf(resource, action) !== 'none'
  }

  /** True when the held scope is at least `minimum` — e.g. org-wide pickers. */
  canAtLeast(resource: Resource, action: Action, minimum: Scope): boolean {
    return SCOPE_RANK[this.scopeOf(resource, action)] >= SCOPE_RANK[minimum]
  }

  canAny(pairs: Array<[Resource, Action]>): boolean {
    return pairs.some(([resource, action]) => this.can(resource, action))
  }

  hasRole(...codes: RoleCode[]): boolean {
    return codes.some((code) => this.snapshot.roles.includes(code))
  }

  get roles(): RoleCode[] {
    return this.snapshot.roles
  }
  get dashboard() {
    return this.snapshot.dashboard
  }
  /** CEO. Every write control is suppressed, and the API blocks them anyway. */
  get isReadOnly() {
    return this.snapshot.read_only
  }
  get canManageUsers() {
    return this.snapshot.can_manage_users
  }
  get layers() {
    return this.snapshot.layers
  }
  /** The most senior layer held; 1 is the top of the org. */
  get topLayer() {
    return Math.min(...(this.snapshot.layers.length ? this.snapshot.layers : [5]))
  }

  /**
   * Whether this organization's plan includes a module.
   *
   * The same truthiness rule as `can()`: absence means unavailable. A snapshot
   * that arrived without a feature list therefore hides gated modules rather
   * than showing them, which is the right way to be wrong here.
   *
   * ENTITLEMENT IS NOT PERMISSION, and the two are kept apart deliberately.
   * `can()` asks whether this person may do a thing; this asks whether the
   * company bought it. Both must be true to render a control, and NEITHER is
   * what stops the request: a disabled module answers `feature_not_available`
   * from the API whatever this returns.
   */
  hasFeature(feature: FeatureCode): boolean {
    return (this.snapshot.features ?? []).includes(feature)
  }

  get features(): FeatureCode[] {
    return this.snapshot.features ?? []
  }

  /**
   * The organization's own lifecycle state.
   *
   * Not a permission: a suspended customer's users keep every grant they had
   * and are refused anyway, which is exactly why this is carried separately.
   * It is what lets the SPA show "this account is suspended" instead of a
   * generic "you do not have access".
   */
  get organizationStatus(): OrganizationStatus {
    return this.snapshot.organization_status ?? ''
  }
}

/** Used before the snapshot loads, and for logged-out rendering. Denies all. */
export const DENY_ALL = new Permissions({
  dashboard: 'self',
  read_only: true,
  can_manage_users: false,
  layers: [],
  roles: [],
  grants: {},
  // No features either: before a snapshot arrives the app knows neither what
  // this person may do nor what their company bought, and both answers have
  // to be "nothing" rather than "everything".
  features: [],
  organization_status: '',
  notice: 'No session.',
})
