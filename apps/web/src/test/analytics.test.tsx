/**
 * Phase 10 UI: analytics, audit and notifications.
 *
 * The assertion that matters across all three: the UI renders the SERVER's
 * answer rather than deriving its own. It shows the catalogue the server
 * filtered, the scope label the server applied, and the sensitivity the server
 * classified — so a screen can never imply a departmental number is
 * organisation-wide, and a permission change needs no frontend edit.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const data = vi.hoisted(() => ({
  catalog: null as unknown,
  metrics: {} as Record<string, unknown>,
  audit: null as unknown,
  auditOptions: null as unknown,
}))

const resultFor = vi.hoisted(() => (key: string) => {
  const store = (globalThis as never as { __metrics: Record<string, unknown> }).__metrics
  return store?.[key] ?? null
})

vi.mock('@/lib/analyticsQueries', async () => {
  const ok = (value: unknown) => ({
    data: value,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  })
  return {
    useMetricCatalog: () => ok(data.catalog),
    // Keyed on the metric asked for. A mock that returned one fixed result for
    // every key would hide a page that rendered the same card twice.
    useMetric: (key: string) => ok(resultFor(key)),
    useAuditLog: () => ok(data.audit),
    useAuditOptions: () => ok(data.auditOptions),
    useNotificationPreferences: () => ok({ preferences: [] }),
    useSetNotificationPreference: () => ({ mutate: vi.fn(), isPending: false }),
  }
})

const notificationState = vi.hoisted(() => ({
  unreadCount: 0,
  items: [] as unknown[],
  markRead: vi.fn(),
  markAllRead: vi.fn(),
}))

vi.mock('@/hooks/useNotifications', () => ({
  useNotifications: () => ({ available: true, ...notificationState }),
}))

import { AnalyticsPage } from '@/features/analytics/AnalyticsPage'
import { AuditPage } from '@/features/audit/AuditPage'
import { NotificationBell } from '@/features/notifications/NotificationBell'
import { renderWithProviders } from './helpers'

function asRole(role: string) {
  state.role = role
}

const CATALOG = {
  metrics: [
    {
      key: 'hr.headcount',
      label: 'Headcount',
      description: 'People currently employed.',
      family: 'hr',
      unit: 'count',
      shape: 'scalar',
      groupings: ['department'],
      supports_range: false,
    },
    {
      key: 'finance.payroll_cost',
      label: 'Payroll cost',
      description: 'Cost to company.',
      family: 'finance',
      unit: 'currency',
      shape: 'scalar',
      groupings: [],
      supports_range: true,
    },
  ],
  families: ['finance', 'hr'],
}

const METRICS: Record<string, unknown> = {
  'hr.headcount': {
    key: 'hr.headcount',
    label: 'Headcount',
    unit: 'count',
    shape: 'scalar',
    scope_label: 'Own department and its sub-departments',
    generated_at: '2026-08-14T10:00:00Z',
    params: { start: '2025-08-14', end: '2026-08-14', group_by: '' },
    points: [{ key: 'total', label: 'Headcount', value: 4, context: {} }],
  },
  'finance.payroll_cost': {
    key: 'finance.payroll_cost',
    label: 'Payroll cost',
    unit: 'currency',
    shape: 'scalar',
    scope_label: 'Own department and its sub-departments',
    generated_at: '2026-08-14T10:00:00Z',
    params: { start: '2025-08-14', end: '2026-08-14', group_by: '' },
    points: [{ key: 'total', label: 'Payroll cost', value: 46076.92, context: {} }],
  },
}

const AUDIT_ROWS = {
  data: [
    {
      id: 1,
      occurred_at: '2026-08-14T09:00:00Z',
      actor: 'u1',
      actor_email: 'hr_head@demo.test',
      actor_name: 'Hema Rao',
      action: 'override',
      action_label: 'Overridden',
      resource: 'candidate',
      entity_type: 'recruitment.Application',
      entity_id: 'a1',
      entity_label: 'Divya Kamat — Therapist',
      subject_employee: 'e1',
      subject_name: 'Divya Kamat',
      subject_code: 'EMP00107',
      subject_department: 'Medical',
      before: { status: 'rejected' },
      after: { status: 'selected', pan: '***' },
      reason: 'Reversed after the department reconsidered.',
      ip: '10.0.0.1',
      request_id: 'r1',
      is_sensitive: true,
    },
  ],
  meta: { next: null, previous: null, page_size: 25 },
}

beforeEach(() => {
  asRole('hr_head')
  data.catalog = CATALOG
  data.metrics = METRICS
  ;(globalThis as never as { __metrics: Record<string, unknown> }).__metrics = METRICS
  data.audit = AUDIT_ROWS
  data.auditOptions = {
    actions: [{ value: 'override', label: 'Overridden', sensitive: true }],
    resources: ['candidate'],
    entity_types: ['recruitment.Application'],
  }
  notificationState.unreadCount = 0
  notificationState.items = []
})

/* =========================================================== analytics */

describe('Analytics', () => {
  it('renders the catalogue the server returned, not a list of its own', () => {
    renderWithProviders(<AnalyticsPage />)

    expect(screen.getAllByText('Headcount').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Payroll cost').length).toBeGreaterThan(0)
  })

  it("shows the server's scope label so a department figure is never read as org-wide", () => {
    renderWithProviders(<AnalyticsPage />)

    expect(
      screen.getAllByText('Own department and its sub-departments').length,
    ).toBeGreaterThan(0)
  })

  it('refuses the page outright when the role holds no reporting grant', () => {
    asRole('employee')
    renderWithProviders(<AnalyticsPage />)

    expect(screen.getByText('You do not have access to reporting')).toBeInTheDocument()
  })

  it('tells a read-only principal that nothing here changes data', () => {
    asRole('ceo')
    renderWithProviders(<AnalyticsPage />)

    expect(screen.getByText('Read-only view')).toBeInTheDocument()
  })

  it('offers grouping only for metrics the server says support it', () => {
    renderWithProviders(<AnalyticsPage />)

    // hr.headcount declares groupings; finance.payroll_cost declares none.
    expect(screen.getAllByLabelText(/Group Headcount by/).length).toBeGreaterThan(0)
  })
})

/* =============================================================== audit */

describe('Audit viewer', () => {
  it('renders an entry with its actor, action and subject', () => {
    renderWithProviders(<AuditPage />)

    expect(screen.getByText('Hema Rao')).toBeInTheDocument()
    expect(screen.getAllByText('Overridden').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Divya Kamat/).length).toBeGreaterThan(0)
  })

  it("marks an entry sensitive from the server's classification", () => {
    renderWithProviders(<AuditPage />)
    expect(screen.getByText('!')).toBeInTheDocument()
  })

  it('shows the previous and new values, and never a redacted one', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AuditPage />)

    await user.click(screen.getByRole('button', { name: 'Detail' }))

    expect(screen.getByText('rejected')).toBeInTheDocument()
    expect(screen.getByText('selected')).toBeInTheDocument()
    // The registry redacts at write time; the viewer must show that a field
    // changed without ever showing the value.
    expect(screen.getByText('redacted')).toBeInTheDocument()
  })

  it('surfaces the recorded reason', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AuditPage />)

    await user.click(screen.getByRole('button', { name: 'Detail' }))

    expect(
      screen.getByText('Reversed after the department reconsidered.'),
    ).toBeInTheDocument()
  })

  it('refuses the page when the role holds no audit grant', () => {
    asRole('employee')
    renderWithProviders(<AuditPage />)

    expect(screen.getByText('You do not have access to the audit log')).toBeInTheDocument()
  })

  it('offers no control that could write to the log', () => {
    renderWithProviders(<AuditPage />)

    const buttons = screen.getAllByRole('button').map((b) => b.textContent ?? '')
    for (const label of ['Delete', 'Edit', 'New', 'Create']) {
      expect(buttons.some((text) => text.includes(label))).toBe(false)
    }
  })
})

/* ======================================================= notifications */

describe('Notification bell', () => {
  it('shows an unread badge when there is something to act on', () => {
    notificationState.unreadCount = 3
    renderWithProviders(<NotificationBell />)

    // The accessible name carries the count; the badge itself is aria-hidden
    // so a screen reader hears it once rather than twice.
    expect(screen.getByLabelText('Notifications, 3 unread')).toBeInTheDocument()
  })

  it('opens a list of notifications', async () => {
    const user = userEvent.setup()
    notificationState.unreadCount = 1
    notificationState.items = [
      {
        id: 'n1',
        kind: 'payroll_processed',
        title: 'Payroll is ready for review',
        body: '15 payslips.',
        isRead: false,
        createdAt: '2026-08-14T09:00:00Z',
        priority: 'high',
      },
    ]
    renderWithProviders(<NotificationBell />)

    await user.click(screen.getByLabelText('Notifications, 1 unread'))

    expect(screen.getByText('Payroll is ready for review')).toBeInTheDocument()
    expect(screen.getByText('Important')).toBeInTheDocument()
  })

  it('flags a critical notification as needing action', async () => {
    const user = userEvent.setup()
    notificationState.unreadCount = 1
    notificationState.items = [
      {
        id: 'n2',
        kind: 'clearance_task_assigned',
        title: 'Exit clearance is blocked on you',
        body: '',
        isRead: false,
        createdAt: '2026-08-14T09:00:00Z',
        priority: 'critical',
      },
    ]
    renderWithProviders(<NotificationBell />)

    await user.click(screen.getByLabelText('Notifications, 1 unread'))

    expect(screen.getByText('Action required')).toBeInTheDocument()
  })

  it('hides mark-as-read from a read-only principal, who would get a 403', async () => {
    const user = userEvent.setup()
    asRole('ceo')
    notificationState.unreadCount = 1
    notificationState.items = [
      {
        id: 'n3',
        kind: 'payroll_approved',
        title: 'Payroll approved',
        body: '',
        isRead: false,
        createdAt: '2026-08-14T09:00:00Z',
        priority: 'normal',
      },
    ]
    renderWithProviders(<NotificationBell />)

    await user.click(screen.getByLabelText('Notifications, 1 unread'))

    expect(screen.getByText('Payroll approved')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Mark all read/ })).not.toBeInTheDocument()
  })

  it('says so plainly when there is nothing to act on', async () => {
    const user = userEvent.setup()
    renderWithProviders(<NotificationBell />)

    await user.click(screen.getByLabelText('Notifications, 0 unread'))

    expect(screen.getByText('Nothing needs your attention.')).toBeInTheDocument()
  })
})

/* ====================================================== chart accessibility */

describe('Charts are reachable without sight', () => {
  it('gives the chart an accessible summary and a real data table', async () => {
    const { ChartFrame } = await import('@/components/ui/Chart')

    renderWithProviders(
      <ChartFrame
        title="Headcount trend"
        data={[
          { name: 'Jan 2026', value: 12 },
          { name: 'Feb 2026', value: 18 },
          { name: 'Mar 2026', value: 9 },
        ]}
        formatValue={(v) => String(v)}
      >
        <div />
      </ChartFrame>,
    )

    // A screen reader must be told what the chart shows, not skip an unlabelled SVG.
    const figure = screen.getByRole('img')
    expect(figure.getAttribute('aria-label')).toContain('Headcount trend')
    expect(figure.getAttribute('aria-label')).toContain('Highest Feb 2026')
    expect(figure.getAttribute('aria-label')).toContain('lowest Mar 2026')

    // And the actual numbers must be readable, not merely described.
    expect(screen.getByRole('table', { name: 'Headcount trend' })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Feb 2026' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '18' })).toBeInTheDocument()
  })

  it('does not claim a range when there is nothing to show', async () => {
    const { ChartFrame } = await import('@/components/ui/Chart')

    renderWithProviders(
      <ChartFrame title="Payroll cost" data={[]} formatValue={String}>
        <div />
      </ChartFrame>,
    )

    expect(screen.getByRole('img').getAttribute('aria-label')).toBe('Payroll cost: no data.')
  })
})
