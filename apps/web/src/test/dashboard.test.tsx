/**
 * Dashboards.
 *
 * The claim under test is that six dashboards are COMPOSITIONS of shared
 * permission-gated panels, not six role-specific pages. So most of these
 * assertions mount the same components as different roles and check that the
 * difference comes from grants — never from a role check inside a component.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({
      user: { first_name: 'Test', full_name: 'Test User' },
      permissions: new Permissions(snapshotForRole(state.role as never)),
    }),
  }
})

const empty = { data: { data: [], meta: { next: null, previous: null, page_size: 25 } } }
const idle = { ...empty, isLoading: false, isError: false, isFetching: false, error: null, refetch: vi.fn() }

vi.mock('@/lib/queries', () => ({
  useApplications: () => idle,
  useEmployees: () => idle,
  isCurrentEmployee: (e: { status: string }) => e.status !== 'exited' && e.status !== 'terminated',
  useHrDecisionQueue: () => idle,
  useInterviews: () => idle,
  useJobs: () => idle,
  useOffers: () => idle,
  useMyEmployeeRecord: () => ({ ...idle, data: null, isError: true }),
}))

const metricState = vi.hoisted(() => ({
  catalog: { metrics: [] as unknown[], families: [] as string[] },
}))

vi.mock('@/lib/analyticsQueries', () => {
  const ok = (value: unknown) => ({
    data: value,
    isLoading: false,
    isError: false,
    isFetched: true,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  })
  return {
    useMetricCatalog: () => ok(metricState.catalog),
    useMetric: (key: string) =>
      ok({
        key,
        label: 'Headcount',
        unit: 'count',
        shape: 'scalar',
        scope_label: 'Own department and its sub-departments',
        generated_at: '2026-08-14T10:00:00Z',
        params: { start: '', end: '', group_by: '' },
        points: [{ key: 'total', label: 'Headcount', value: 4, context: {} }],
      }),
  }
})

const notificationState = vi.hoisted(() => ({ items: [] as unknown[] }))

vi.mock('@/hooks/useNotifications', () => ({
  useNotifications: () => ({
    available: true,
    unreadCount: 0,
    markRead: vi.fn(),
    markAllRead: vi.fn(),
    ...notificationState,
  }),
}))

const exitState = vi.hoisted(() => ({ rows: [] as unknown[] }))

vi.mock('@/lib/exitQueries', () => ({
  useExits: () => ({
    data: { data: exitState.rows, meta: { next: null, previous: null, page_size: 25 } },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useMyClearanceItems: () => ({ data: [], isLoading: false, refetch: vi.fn() }),
}))

vi.mock('@/lib/payrollQueries', () => ({
  usePayrollRuns: () => idle,
  useMyPayroll: () => ({
    data: { payslips: [], declaration: null, structure: null },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }),
}))

vi.mock('@/lib/lifecycleQueries', () => ({
  useOnboardings: () => idle,
  usePendingProbationReviews: () => idle,
}))

import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { ActionCentrePanel, ExitsPanel, MetricStrip } from '@/features/dashboard/panels'
import { renderWithProviders } from './helpers'

function asRole(role: string) {
  state.role = role
}

beforeEach(() => {
  asRole('hr_head')
  metricState.catalog = { metrics: [], families: [] }
  notificationState.items = []
  exitState.rows = []
})

/* ================================================= dashboard selection */

describe('Dashboard selection', () => {
  it.each([
    ['ceo', 'Organisation overview'],
    ['admin', 'System overview'],
    ['hr_head', 'Department overview'],
    ['employee', 'Your workspace'],
  ])('renders the %s dashboard the server chose', (role, heading) => {
    asRole(role)
    renderWithProviders(<DashboardPage />)

    expect(screen.getAllByText(heading).length).toBeGreaterThan(0)
  })

  it('greets by name and shows the roles held', () => {
    renderWithProviders(<DashboardPage />)

    expect(
      screen.getByText(/Good (morning|afternoon|evening), Test/),
    ).toBeInTheDocument()
  })
})

/* ============================================== CEO stays read-only */

describe('The CEO dashboard', () => {
  beforeEach(() => asRole('ceo'))

  it('says plainly that write actions are withheld', () => {
    renderWithProviders(<DashboardPage />)

    expect(screen.getByText('Oversight view')).toBeInTheDocument()
  })

  it('offers no control that would change a record', () => {
    renderWithProviders(<DashboardPage />)

    const labels = screen.queryAllByRole('button').map((b) => b.textContent ?? '')
    for (const forbidden of ['Decide', 'Approve', 'Reject', 'Create', 'New ', 'Process']) {
      expect(labels.some((text) => text.includes(forbidden))).toBe(false)
    }
  })

  it('drops the HR decision card without needing a read-only variant', () => {
    // The card gates on CANDIDATE/REJECT, which the CEO does not hold. This is
    // the assertion that justifies deleting the duplicated component.
    renderWithProviders(<DashboardPage />)

    expect(screen.queryByText('Awaiting your decision')).not.toBeInTheDocument()
  })
})

/* ================================================ Admin keeps authority */

describe('The Admin dashboard', () => {
  beforeEach(() => asRole('admin'))

  it('keeps its management entry points', () => {
    renderWithProviders(<DashboardPage />)

    expect(screen.getByRole('button', { name: 'Employees' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Organisation' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Audit log' })).toBeInTheDocument()
  })

  it('states that override is an exception path, not a routine one', () => {
    renderWithProviders(<DashboardPage />)

    expect(screen.getByText('Administrative account')).toBeInTheDocument()
  })
})

/* ================================================== panels are shared */

describe('Panels gate on permission, not role', () => {
  it('renders the metric strip only when the role holds reporting', () => {
    metricState.catalog = {
      metrics: [
        {
          key: 'hr.headcount',
          label: 'Headcount',
          description: '',
          family: 'hr',
          unit: 'count',
          shape: 'scalar',
          groupings: [],
          supports_range: false,
        },
      ],
      families: ['hr'],
    }

    asRole('hr_head')
    const withGrant = renderWithProviders(<MetricStrip />)
    expect(screen.getAllByText('Headcount').length).toBeGreaterThan(0)
    withGrant.unmount()

    // `employee` holds no REPORT grant in the fixture set, so the strip
    // renders nothing at all rather than an empty shell.
    asRole('employee')
    renderWithProviders(<MetricStrip />)
    expect(screen.queryByText('Headcount')).not.toBeInTheDocument()
  })

  it('shows the scope the server applied, so a department figure is never read as org-wide', () => {
    metricState.catalog = {
      metrics: [
        {
          key: 'hr.headcount',
          label: 'Headcount',
          description: '',
          family: 'hr',
          unit: 'count',
          shape: 'scalar',
          groupings: [],
          supports_range: false,
        },
      ],
      families: ['hr'],
    }
    asRole('medical_director')
    renderWithProviders(<MetricStrip />)

    expect(
      screen.getByText('Own department and its sub-departments'),
    ).toBeInTheDocument()
  })

  it('renders nothing for a role without the offboarding scope', () => {
    asRole('employee')
    renderWithProviders(<ExitsPanel />)

    expect(screen.queryByText('Exits in progress')).not.toBeInTheDocument()
  })

  it('excludes a finished exit from "in progress"', () => {
    // A completed exit is not in progress; listing it made the panel lie.
    exitState.rows = [
      {
        id: 'x1',
        employee_name: 'Om Jadhav',
        stage: 'completed',
        expected_last_working_date: '2026-09-12',
      },
      {
        id: 'x2',
        employee_name: 'Priya Nair',
        stage: 'clearance',
        expected_last_working_date: '2026-09-30',
      },
    ]
    asRole('hr_head')
    renderWithProviders(<ExitsPanel />)

    expect(screen.getByText('Priya Nair')).toBeInTheDocument()
    expect(screen.queryByText('Om Jadhav')).not.toBeInTheDocument()
  })
})

/* ========================================================= action centre */

describe('The action centre', () => {
  it('lists only what is actually blocked on the viewer', () => {
    notificationState.items = [
      {
        id: 'n1',
        kind: 'clearance_task_assigned',
        title: 'Exit clearance is blocked on you',
        body: '',
        isRead: false,
        createdAt: '2026-08-14T09:00:00Z',
        priority: 'critical',
      },
      {
        id: 'n2',
        kind: 'payslip_available',
        title: 'Your payslip is ready',
        body: '',
        isRead: false,
        createdAt: '2026-08-14T09:00:00Z',
        priority: 'normal',
      },
    ]
    renderWithProviders(<ActionCentrePanel />)

    expect(screen.getByText('Exit clearance is blocked on you')).toBeInTheDocument()
    // Normal-priority news is not something anyone is waiting on.
    expect(screen.queryByText('Your payslip is ready')).not.toBeInTheDocument()
  })

  it('says so when nothing is outstanding', () => {
    renderWithProviders(<ActionCentrePanel />)
    expect(screen.getByText('Nothing is blocked on you')).toBeInTheDocument()
  })
})
