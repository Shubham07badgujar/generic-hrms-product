/**
 * Custom packages: HR/Finance configure and approve; employees see their own.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const state = vi.hoisted(() => ({ role: 'hr_head' as string }))
const posts = vi.hoisted(() => [] as Array<{ url: string; body?: unknown }>)

vi.mock('@/app/AuthProvider', async () => {
  const { Permissions } = await import('@/lib/permissions')
  const { snapshotForRole } = await import('./helpers')
  return {
    usePermissions: () => new Permissions(snapshotForRole(state.role as never)),
    useAuth: () => ({ permissions: new Permissions(snapshotForRole(state.role as never)) }),
  }
})

const activePackage = {
  id: 'pkg-1', employee: 'e-1', employee_name: 'Employee B', employee_code: 'EMP00001',
  total_amount: '2400000.00', package_type: 'monthly_plus_deferred' as const,
  start_date: '2026-04-01', end_date: '2027-03-31', status: 'active' as const,
  notes: 'internal', supersedes: null,
  bond_start_date: null, bond_end_date: null, bond_required_months: null,
  activated_at: '2026-04-01T00:00:00Z',
  periods: [{
    id: 'per-1', order: 1, label: 'Months 1–12', start_date: '2026-04-01',
    end_date: '2027-03-31', amount: '1800000.00', months: 12, monthly_amount: '150000.00',
  }],
  deferrals: [{
    id: 'def-1', label: 'Completion amount', amount: '600000.00',
    condition_type: 'after_months' as const, condition_months: 12,
    eligible_on: '2027-04-01', condition_note: '', status: 'eligible' as const,
    effective_status: 'eligible' as const, decided_by_email: null, decided_at: null,
    decision_reason: '', released_in_adjustment: null,
  }],
  summary: {
    paid_so_far: '900000.00', deferred_total: '600000.00', released_total: '0.00',
    remaining_deferred: '600000.00', next_release_date: '2027-04-01', allocation_gap: '0.00',
  },
}

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      if (url === '/payroll/packages/')
        return { data: [activePackage], meta: { next: null, previous: null } }
      if (url === '/payroll/me/')
        return {
          payslips: [], declaration: null, structure: null,
          package: { ...activePackage, notes: undefined },
        }
      return { data: [], meta: { next: null, previous: null } }
    }),
    apiPost: vi.fn(async (url: string, body?: unknown) => {
      posts.push({ url, body })
      return { id: 'new' }
    }),
  }
})

import { PackagesPage } from '@/features/payroll/PackagesPage'
import { PayslipsPage } from '@/features/payroll/PayslipsPage'
import { renderWithProviders } from './helpers'

beforeEach(() => {
  state.role = 'hr_head'
  posts.length = 0
})

describe('Custom packages', () => {
  it('shows the dashboard numbers and the release-eligible flag', async () => {
    renderWithProviders(<PackagesPage />)
    expect(await screen.findByText('Employee B')).toBeInTheDocument()
    expect(screen.getByText('Release eligible')).toBeInTheDocument()
    expect(screen.getByText(/9,00,000/)).toBeInTheDocument()   // paid so far
    expect(screen.getByText('New package')).toBeInTheDocument()
  })

  it('lets a head approve a release into a chosen payroll period', async () => {
    const user = userEvent.setup()
    renderWithProviders(<PackagesPage />)
    await user.click(await screen.findByRole('button', { name: /open/i }))
    await user.click(await screen.findByRole('button', { name: /decide/i }))

    const dialog = within((await screen.findAllByRole('dialog')).at(-1)!)
    await user.type(dialog.getByLabelText(/reason/i), 'Service period completed.')
    await user.click(dialog.getByRole('button', { name: /approve release/i }))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0].url).toBe('/payroll/packages/pkg-1/deferrals/def-1/decide/')
    expect(posts[0].body).toMatchObject({ decision: 'approve', reason: 'Service period completed.' })
  })

  it('offers no configuration controls to roles without package write', async () => {
    state.role = 'recruiter'
    renderWithProviders(<PackagesPage />)
    await screen.findByText('Employee B')
    expect(screen.queryByText('New package')).not.toBeInTheDocument()
  })
})

describe('My package (employee view)', () => {
  it('shows the employee their own package summary', async () => {
    state.role = 'employee'
    renderWithProviders(<PayslipsPage />)
    expect(await screen.findByText('My package')).toBeInTheDocument()
    expect(screen.getByText(/24,00,000/)).toBeInTheDocument()
    expect(screen.getByText(/Completion amount \(deferred\)/)).toBeInTheDocument()
    expect(screen.queryByText('internal')).not.toBeInTheDocument()  // notes never leak
  })
})
