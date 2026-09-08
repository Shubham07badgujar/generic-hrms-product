/**
 * Navigation, generated from permissions.
 *
 * There is no per-role sidebar array anywhere. Each item declares the
 * permission it needs, and the sidebar is the subset the current snapshot
 * allows — so a Clinic Doctor and an HR Head get genuinely different
 * navigation without a single `if (role === ...)`.
 *
 * The consequence worth stating: adding a page means adding one entry here.
 * Forgetting to update six role lists is not a failure mode this design has.
 */

import type { ReactNode } from 'react'
import { ACTION, type Action, type Permissions, type Resource, RESOURCE } from '@/lib/permissions'

export interface NavItem {
  label: string
  to: string
  icon: ReactNode
  resource?: Resource
  action?: Action
  /** Shown only when this predicate passes, for cases permissions can't express. */
  when?: (permissions: Permissions) => boolean
  /** Match child routes too (`/recruitment/jobs/123` highlights "Job openings"). */
  matchPrefix?: boolean
}

export interface NavGroup {
  id: string
  label: string | null
  items: NavItem[]
}

/* --------------------------------------------------------------- icons */

const icon = (path: ReactNode) => (
  <svg viewBox="0 0 24 24" fill="none" className="h-[18px] w-[18px]" aria-hidden>
    <g stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      {path}
    </g>
  </svg>
)

const ICONS = {
  dashboard: icon(<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>),
  people: icon(<><circle cx="9" cy="8" r="3.2" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0" /><path d="M17 11.2a3 3 0 0 0 0-6M18 20a6 6 0 0 0-2-4.5" /></>),
  briefcase: icon(<><rect x="2.5" y="7" width="19" height="13" rx="2" /><path d="M8.5 7V5.5A1.5 1.5 0 0 1 10 4h4a1.5 1.5 0 0 1 1.5 1.5V7M2.5 12.5h19" /></>),
  upload: icon(<><path d="M12 16V4M8 8l4-4 4 4" /><path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" /></>),
  users: icon(<><circle cx="12" cy="7.5" r="3.5" /><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0" /></>),
  pipeline: icon(<><path d="M4 6h16M7 12h13M10 18h10" /><circle cx="4" cy="12" r="1.4" /><circle cx="7" cy="18" r="1.4" /></>),
  calendar: icon(<><rect x="3" y="4.5" width="18" height="16" rx="2" /><path d="M3 9.5h18M8 3v3M16 3v3" /></>),
  decision: icon(<><path d="M9 11.5 11 13.5 15.5 9" /><rect x="3.5" y="3.5" width="17" height="17" rx="3" /></>),
  offer: icon(<><path d="M6.5 3.5h11l3 5-8.5 12L3.5 8.5z" /><path d="M3.5 8.5h17" /></>),
  chart: icon(<><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>),
  clock: icon(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5.2l3.2 2" /></>),
  wallet: icon(<><rect x="3" y="6" width="18" height="13" rx="2.5" /><path d="M3 10h18M16.5 14.5h1.5" /></>),
  shield: icon(<><path d="M12 3 4.5 6v6c0 4.4 3.1 8.2 7.5 9 4.4-.8 7.5-4.6 7.5-9V6z" /><path d="m9.2 12 2 2 3.6-3.8" /></>),
  cog: icon(<><circle cx="12" cy="12" r="3.2" /><path d="M12 2.5v2.2M12 19.3v2.2M21.5 12h-2.2M4.7 12H2.5M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6M18.7 18.7l-1.6-1.6M6.9 6.9 5.3 5.3" /></>),
  file: icon(<><path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z" /><path d="M13.5 3v5.5H19M8.5 13h7M8.5 17h4" /></>),
  building: icon(<><path d="M4 21V5.5A1.5 1.5 0 0 1 5.5 4h7A1.5 1.5 0 0 1 14 5.5V21M14 10h4.5A1.5 1.5 0 0 1 20 11.5V21M2.5 21h19M7.5 8h3M7.5 12h3M7.5 16h3" /></>),
}

/* ---------------------------------------------------------------- spec */

export const NAV_SPEC: NavGroup[] = [
  {
    id: 'overview',
    label: null,
    items: [{ label: 'Dashboard', to: '/', icon: ICONS.dashboard }],
  },
  {
    id: 'recruitment',
    label: 'Recruitment',
    items: [
      {
        label: 'Pipeline',
        to: '/recruitment/pipeline',
        icon: ICONS.pipeline,
        resource: RESOURCE.APPLICATION,
        matchPrefix: true,
      },
      {
        label: 'Job openings',
        to: '/recruitment/jobs',
        icon: ICONS.briefcase,
        resource: RESOURCE.JOB_OPENING,
        matchPrefix: true,
      },
      {
        label: 'Candidates',
        to: '/recruitment/candidates',
        icon: ICONS.users,
        resource: RESOURCE.CANDIDATE,
        matchPrefix: true,
      },
      {
        label: 'Import candidates',
        to: '/recruitment/candidates/import',
        icon: ICONS.upload,
        resource: RESOURCE.CANDIDATE,
        // IMPORT, not VIEW. The point of a separate action is that it can be
        // revoked on its own; gating this on VIEW would show it to every role
        // that can merely read the candidate list.
        action: ACTION.IMPORT,
      },
      {
        label: 'My interviews',
        to: '/recruitment/interviews',
        icon: ICONS.calendar,
        resource: RESOURCE.INTERVIEW,
        matchPrefix: true,
      },
      {
        // Only HR Head holds APPLICATION/REJECT, so this queue appears for
        // exactly the person who has to work it.
        label: 'Decision queue',
        to: '/recruitment/decisions',
        icon: ICONS.decision,
        resource: RESOURCE.APPLICATION,
        action: ACTION.REJECT,
      },
      {
        label: 'Offers',
        to: '/recruitment/offers',
        icon: ICONS.offer,
        resource: RESOURCE.OFFER,
        matchPrefix: true,
      },
      {
        label: 'Workflows',
        to: '/recruitment/workflows',
        icon: ICONS.file,
        resource: RESOURCE.HIRING_WORKFLOW,
        matchPrefix: true,
      },
    ],
  },
  {
    id: 'people',
    label: 'People',
    items: [
      {
        label: 'Employees',
        to: '/employees',
        icon: ICONS.people,
        resource: RESOURCE.EMPLOYEE,
        matchPrefix: true,
      },
      {
        label: 'My profile',
        to: '/me',
        icon: ICONS.users,
      },
      {
        // The document register. VIEW rather than a management action: an
        // employee opening it sees their own documents, which is a reasonable
        // thing to want and is what the server would return anyway.
        label: 'Documents',
        to: '/documents',
        icon: ICONS.file,
        resource: RESOURCE.EMPLOYEE_DOCUMENT,
      },
      {
        // The HR working queue: new joiners, overdue checklist items and
        // probations needing a decision.
        label: 'Onboarding',
        to: '/onboarding',
        icon: ICONS.decision,
        resource: RESOURCE.ONBOARDING,
        action: ACTION.EDIT,
      },
      {
        // Visible to anyone who can see an exit, which includes an employee
        // following their own. The page adapts to what they may do.
        label: 'Offboarding',
        to: '/offboarding',
        icon: ICONS.offer,
        resource: RESOURCE.OFFBOARDING,
        matchPrefix: true,
      },
      {
        label: 'Assets',
        to: '/assets',
        icon: ICONS.briefcase,
        resource: RESOURCE.ASSET,
        matchPrefix: true,
      },
      {
        label: 'Organisation',
        to: '/organisation',
        icon: ICONS.building,
        resource: RESOURCE.DEPARTMENT,
      },
    ],
  },
  {
    id: 'operations',
    label: 'Operations',
    items: [
      {
        label: 'Attendance',
        to: '/attendance',
        icon: ICONS.clock,
        resource: RESOURCE.ATTENDANCE,
      },
      // The biometric plumbing. Gated on its own resource, so employees —
      // who hold SELF-scoped attendance rights — never see this item.
      {
        label: 'eSSL Integration',
        to: '/attendance/essl',
        icon: ICONS.clock,
        resource: RESOURCE.ATTENDANCE_DEVICE,
      },
      { label: 'Leave', to: '/leave', icon: ICONS.calendar, resource: RESOURCE.LEAVE_REQUEST },
      { label: 'Payroll', to: '/payroll', icon: ICONS.wallet, resource: RESOURCE.PAYROLL_RUN },
      {
        // Package schedules. Everyone holds PACKAGE/VIEW at SELF for their
        // own summary (shown on My payslips), so the dashboard item needs
        // scope beyond self, not mere presence.
        label: 'Packages',
        to: '/payroll/packages',
        icon: ICONS.briefcase,
        resource: RESOURCE.PACKAGE,
        when: (p) => p.canAtLeast(RESOURCE.PACKAGE, ACTION.VIEW, 'all'),
      },
      {
        // Rates and the component catalogue. Gated on its own resource so
        // payroll staff who may only PROCESS runs still see (read-only) what
        // rules the figures came from, while employees see nothing.
        label: 'Payroll settings',
        to: '/payroll/settings',
        icon: ICONS.cog,
        resource: RESOURCE.STATUTORY_CONFIG,
      },
      { label: 'Payslips', to: '/payslips', icon: ICONS.file, resource: RESOURCE.PAYSLIP },
    ],
  },
  {
    id: 'insight',
    label: 'Insight',
    items: [
      { label: 'Reports', to: '/reports', icon: ICONS.chart, resource: RESOURCE.REPORT },
      { label: 'Audit log', to: '/audit', icon: ICONS.shield, resource: RESOURCE.AUDIT_LOG },
      {
        label: 'Settings',
        to: '/settings',
        icon: ICONS.cog,
        resource: RESOURCE.ORG_SETTINGS,
        action: ACTION.EDIT,
      },
    ],
  },
]

/** The groups this principal may see, with empty groups dropped. */
export function visibleNavigation(permissions: Permissions): NavGroup[] {
  return NAV_SPEC.map((group) => ({
    ...group,
    items: group.items.filter((item) => {
      if (item.when && !item.when(permissions)) return false
      if (!item.resource) return true
      return permissions.can(item.resource, item.action ?? ACTION.VIEW)
    }),
  })).filter((group) => group.items.length > 0)
}
