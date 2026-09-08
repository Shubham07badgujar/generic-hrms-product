/**
 * Offboarding UI.
 *
 * The assertion that matters most: the exit page renders the SERVER's blocker
 * list and disables approval from `can_complete`, rather than deciding for
 * itself whether an exit is ready. Everything else checks that the UI offers
 * only actions the server would accept.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
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

/** The exit detail page reads its data from this hook; the tests supply it. */
const exitData = vi.hoisted(() => ({ current: null as unknown }))

vi.mock('@/lib/exitQueries', async () => {
  const noop = { mutate: vi.fn(), isPending: false }
  return {
    useExit: () => ({
      data: exitData.current,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }),
    useExitActions: () => ({
      waiveNotice: noop, earlyRelease: noop, updateNotice: noop,
      approve: noop, complete: noop, cancel: noop,
      interview: noop, settlement: noop, clearSettlement: noop,
    }),
    useClearanceActions: () => ({ complete: noop, waive: noop }),
    useExits: () => ({ data: { data: [], meta: { next: null, previous: null, page_size: 25 } } }),
    useMyClearanceItems: () => ({ data: [], refetch: vi.fn() }),
    usePendingResignations: () => ({ data: { data: [] }, isLoading: false, refetch: vi.fn() }),
    useResignationReview: () => ({ approve: noop, reject: noop, withdraw: noop }),
    useStartExit: () => noop,
    useSubmitResignation: () => noop,
  }
})

import { ExitDetailPage } from '@/features/offboarding/ExitDetailPage'
import { ResignDialog } from '@/features/offboarding/OffboardingPage'
import { renderWithProviders } from './helpers'
import type { ExitBlocker, ExitWorkflowDetail } from '@/lib/types'

function asRole(role: string) {
  state.role = role
}

const BLOCKERS: ExitBlocker[] = [
  {
    id: 'assets:unreturned',
    gate: 'assets',
    label: 'Company property',
    detail: '1 returnable asset(s) still allocated',
    items: ['LAP-0001 (ThinkPad T14)'],
  },
  {
    id: 'finance:settlement',
    gate: 'finance',
    label: 'Full and final settlement',
    detail: 'The settlement has not been cleared by finance',
    items: [],
  },
  {
    id: 'clearance:hr',
    gate: 'hr',
    label: 'HR clearance tasks',
    detail: '2 required item(s) outstanding',
    items: ['Conduct the exit interview', 'HR clearance sign-off'],
  },
]

function exitWorkflow(overrides: Partial<ExitWorkflowDetail> = {}): ExitWorkflowDetail {
  return {
    id: 'exit-1',
    employee: 'employee-1',
    employee_name: 'Priya Nair',
    employee_code: 'EMP00042',
    department_name: 'Medical',
    employee_status: 'resigned',
    exit_type: 'resignation',
    stage: 'clearance',
    notice_start_date: '2026-08-01',
    notice_days: 30,
    expected_last_working_date: '2026-08-31',
    actual_last_working_date: null,
    notice_waived: false,
    early_release_approved: false,
    initiated_at: '2026-08-01T09:00:00Z',
    approved_at: null,
    completed_at: null,
    completed_items: 3,
    total_items: 15,
    resignation: null,
    reason: 'Resignation accepted.',
    notice_waived_by: null,
    notice_waived_at: null,
    notice_waiver_reason: '',
    early_release_by: null,
    early_release_reason: '',
    approved_by: null,
    approval_notes: '',
    cancelled_reason: '',
    clearance_items: [
      {
        id: 'item-1',
        exit_workflow: 'exit-1',
        title: 'HR clearance sign-off',
        description: '',
        category: 'hr',
        owner: 'hr',
        assigned_to: null,
        assigned_to_name: null,
        is_required: true,
        requires_evidence: false,
        due_date: '2026-08-31',
        order: 120,
        status: 'pending',
        has_evidence: false,
        completed_at: null,
        completed_by: null,
        completed_by_email: null,
        notes: '',
        is_done: false,
        is_overdue: false,
      },
      {
        id: 'item-2',
        exit_workflow: 'exit-1',
        title: 'Finance clearance sign-off',
        description: '',
        category: 'finance',
        owner: 'finance',
        assigned_to: null,
        assigned_to_name: null,
        is_required: true,
        requires_evidence: false,
        due_date: '2026-09-07',
        order: 150,
        status: 'completed',
        has_evidence: false,
        completed_at: '2026-08-20T09:00:00Z',
        completed_by: 'user-2',
        completed_by_email: 'finance_head@example.test',
        notes: '',
        is_done: true,
        is_overdue: false,
      },
    ],
    settlement: {
      id: 'settlement-1',
      exit_workflow: 'exit-1',
      final_working_date: '2026-08-31',
      pending_salary: '45000.00',
      leave_encashment: '12000.00',
      bonus_or_incentive: '0.00',
      other_earnings: '0.00',
      outstanding_advances: '5000.00',
      notice_shortfall_recovery: '0.00',
      asset_recovery: '0.00',
      other_deductions: '0.00',
      notes: '',
      status: 'in_review',
      prepared_by: 'user-2',
      cleared_by: null,
      cleared_by_email: null,
      cleared_at: null,
      paid_at: null,
      gross_earnings: '57000.00',
      total_deductions: '5000.00',
      net_payable: '52000.00',
    },
    interview: null,
    blockers: BLOCKERS,
    can_complete: false,
    unreturned_assets: [
      {
        allocation_id: 'alloc-1',
        asset_tag: 'LAP-0001',
        asset_name: 'ThinkPad T14',
        category: 'Laptop',
      },
    ],
    ...overrides,
  }
}

beforeEach(() => {
  asRole('hr_head')
  exitData.current = exitWorkflow()
})

/* ================================================ the blocker panel */

describe('The blocker panel', () => {
  it('renders the gates the server reported, not its own opinion', () => {
    renderWithProviders(<ExitDetailPage />)

    // Scoped to the panel: a gate name legitimately appears twice on this
    // page — once as a blocker and once as the card for that gate.
    const panel = screen
      .getAllByRole('alert')
      .find((element) => element.textContent?.includes('gate(s) still open'))!
    expect(within(panel).getByText('3 gate(s) still open')).toBeInTheDocument()
    expect(within(panel).getByText('Company property')).toBeInTheDocument()
    expect(within(panel).getByText('Full and final settlement')).toBeInTheDocument()
    expect(within(panel).getByText('HR clearance tasks')).toBeInTheDocument()
    // The specific outstanding item is named, so the user knows what to chase.
    expect(within(panel).getByText('LAP-0001 (ThinkPad T14)')).toBeInTheDocument()
  })

  it('disables approval while the server says the exit is blocked', () => {
    renderWithProviders(<ExitDetailPage />)
    expect(screen.getByRole('button', { name: 'Approve exit' })).toBeDisabled()
  })

  it('enables approval only when the server says every gate is clear', () => {
    exitData.current = exitWorkflow({ blockers: [], can_complete: true })
    renderWithProviders(<ExitDetailPage />)

    expect(screen.getByText('Every gate is cleared')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve exit' })).toBeEnabled()
  })

  it('offers the final transition only after approval', () => {
    exitData.current = exitWorkflow({
      blockers: [], can_complete: true, stage: 'approved', approved_at: '2026-08-30T09:00:00Z',
    })
    renderWithProviders(<ExitDetailPage />)

    expect(screen.getByRole('button', { name: 'Mark as exited' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve exit' })).not.toBeInTheDocument()
  })
})

/* ================================================ terminal state */

describe('A completed exit', () => {
  beforeEach(() => {
    exitData.current = exitWorkflow({
      stage: 'completed',
      blockers: [],
      can_complete: true,
      completed_at: '2026-08-31T17:00:00Z',
      actual_last_working_date: '2026-08-31',
    })
  })

  it('says plainly that there is no way back', () => {
    renderWithProviders(<ExitDetailPage />)
    expect(screen.getByText('This exit is complete')).toBeInTheDocument()
    expect(screen.getByText(/new employee record/)).toBeInTheDocument()
  })

  it('offers no further actions', () => {
    renderWithProviders(<ExitDetailPage />)
    for (const label of ['Approve exit', 'Mark as exited', 'Cancel exit', 'Waive notice']) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
  })
})

/* ================================================ permissions */

describe('Permission-driven actions', () => {
  it('withholds every exit authority from a role that lacks approval', () => {
    // A department head can see and work their leavers' clearance, but the
    // exit authority is HR's.
    asRole('medical_director')
    renderWithProviders(<ExitDetailPage />)

    for (const label of ['Approve exit', 'Waive notice', 'Early release', 'Cancel exit']) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
  })

  it('gives HR the notice exceptions', () => {
    renderWithProviders(<ExitDetailPage />)
    expect(screen.getByRole('button', { name: 'Waive notice' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Early release' })).toBeInTheDocument()
  })

  it('requires a substantial reason to waive notice', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ExitDetailPage />)

    await user.click(screen.getByRole('button', { name: 'Waive notice' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveAccessibleName('Waive the notice period')
    // Scoped: the toolbar button that opened this shares the label.
    expect(within(dialog).getByRole('button', { name: 'Waive notice' })).toBeDisabled()
    expect(within(dialog).getByText('This is an exception')).toBeInTheDocument()
  })

  it('warns that marking exited is final', async () => {
    exitData.current = exitWorkflow({ blockers: [], can_complete: true, stage: 'approved' })
    const user = userEvent.setup()
    renderWithProviders(<ExitDetailPage />)

    await user.click(screen.getByRole('button', { name: 'Mark as exited' }))
    expect(screen.getByText('There is no way back')).toBeInTheDocument()
  })
})

/* ================================================ clearance */

describe('The clearance checklist', () => {
  it('groups items by the gate they belong to', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ExitDetailPage />)

    await user.click(screen.getByRole('tab', { name: /Clearance/ }))
    const tabpanel = screen.getByRole('tabpanel')
    expect(within(tabpanel).getByText('HR clearance')).toBeInTheDocument()
    expect(within(tabpanel).getByText('Finance and settlement')).toBeInTheDocument()
  })

  it('shows who completed an item and when', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ExitDetailPage />)

    await user.click(screen.getByRole('tab', { name: /Clearance/ }))
    expect(screen.getByText(/finance_head@example.test/)).toBeInTheDocument()
  })

  it('offers no completion control to a role that cannot edit', async () => {
    asRole('employee')
    const user = userEvent.setup()
    renderWithProviders(<ExitDetailPage />)

    await user.click(screen.getByRole('tab', { name: /Clearance/ }))
    expect(screen.queryByRole('button', { name: 'Waive' })).not.toBeInTheDocument()
  })
})

/* ================================================ settlement */

describe('The settlement panel', () => {
  it('shows the ledger and the net payable', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ExitDetailPage />)

    await user.click(screen.getByRole('tab', { name: 'Settlement' }))
    const tabpanel = screen.getByRole('tabpanel')
    expect(within(tabpanel).getByText('Full and final settlement')).toBeInTheDocument()
    expect(within(tabpanel).getByText('Net payable')).toBeInTheDocument()
    // Statutory computation is explicitly not done here.
    expect(screen.getByText(/belongs to the payroll engine/)).toBeInTheDocument()
  })

  it('flags a negative settlement rather than showing it quietly', async () => {
    exitData.current = exitWorkflow({
      settlement: {
        ...exitWorkflow().settlement!,
        outstanding_advances: '80000.00',
        total_deductions: '80000.00',
        net_payable: '-23000.00',
      },
    })
    const user = userEvent.setup()
    renderWithProviders(<ExitDetailPage />)

    await user.click(screen.getByRole('tab', { name: 'Settlement' }))
    expect(screen.getByText(/owes the company on exit/)).toBeInTheDocument()
  })
})

/* ================================================ exit interview */

describe('The exit interview', () => {
  it('explains why it follows HR permissions', async () => {
    const user = userEvent.setup()
    renderWithProviders(<ExitDetailPage />)

    await user.click(screen.getByRole('tab', { name: 'Exit interview' }))
    expect(screen.getByText(/must not be readable by that manager/)).toBeInTheDocument()
  })
})

/* ================================================ resignation */

describe('The resignation dialog', () => {
  it('tells the employee nothing changes until HR accepts', () => {
    asRole('employee')
    renderWithProviders(<ResignDialog open onClose={() => {}} />)

    expect(screen.getByText('Nothing changes until HR accepts')).toBeInTheDocument()
    expect(screen.getByText(/does not end your employment/)).toBeInTheDocument()
  })

  it('will not submit without a requested last working day', () => {
    asRole('employee')
    renderWithProviders(<ResignDialog open onClose={() => {}} />)

    expect(screen.getByRole('button', { name: 'Submit resignation' })).toBeDisabled()
  })
})
